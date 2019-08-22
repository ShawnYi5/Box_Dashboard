import json
import threading
import time
from datetime import datetime
import os
import pprint
import uuid

from django.core import serializers
from django.shortcuts import get_object_or_404

from apiv1.compress import CompressTaskThreading
from apiv1.htb_task import (HTBStartTransferData, get_task_code, WorkerLog, HTBFinisHTask, get_disk_snapshot_from_info,
                            convert_disk_size2bitmap_size)
from apiv1.models import HostSnapshot, DiskSnapshot, Host, CDPTask
from apiv1.snapshot import DiskSnapshotLocker, GetSnapshotList, SnapshotsUsedBitMapGeneric, GetDiskSnapshot
from box_dashboard import xlogging, boxService, pyconv, xdata, xdatetime, functions

_logger = xlogging.getLogger(__name__)

import DataQueuingIce
import IMG

_threading_op_locker = threading.Lock()


def cover_ice_to_xdata(ice_status):
    if ice_status == DataQueuingIce.WorkType.noneWork:
        return xdata.REMOTE_BACKUP_NONE_WORK
    elif ice_status == DataQueuingIce.WorkType.cdpWork:
        return xdata.REMOTE_BACKUP_CDP_WORK
    elif ice_status == DataQueuingIce.WorkType.qemuWork:
        return xdata.REMOTE_BACKUP_QEMU_WORK
    else:
        return -1


class ThreadingPools(object):
    def __init__(self):
        xlogging.LockerDecorator(_threading_op_locker).decorate()
        self.ths = dict()

    def put(self, k, v):
        if k in self.ths:
            raise Exception('put fail, thread:{} has exists'.format(k))
        self.ths[k] = v

    def get(self, k):
        return self.ths.get(k, None)

    def delete(self, k):
        if k in self.ths:
            del self.ths[k]


_threading_pools = ThreadingPools()


class TaskHandle(object):
    def __init__(self, task_uuid, disk_token):
        self.task_uuid = task_uuid
        self.disk_token = disk_token
        self._task_type = None

    def task_type(self, cache=True):
        if not cache:
            return self._work_type()
        if self._task_type is None:
            self._task_type = self._work_type()
        return self._task_type

    def progress(self):
        task_type = self.task_type()
        if task_type == xdata.REMOTE_BACKUP_QEMU_WORK:
            return self._get_qemu_progress()[0]
        elif task_type == xdata.REMOTE_BACKUP_CDP_WORK:
            return self._get_cdp_progress()[0]
        else:
            return None

    def finished(self):
        task_type = self.task_type()
        if task_type == xdata.REMOTE_BACKUP_QEMU_WORK:
            return self._get_qemu_progress()[1]
        elif task_type == xdata.REMOTE_BACKUP_CDP_WORK:
            return self._get_cdp_progress()[1]
        else:
            return False

    def close(self):
        if self.task_type(cache=False) != xdata.REMOTE_BACKUP_NONE_WORK:
            self.stop()
        code = boxService.box_service.CloseTask(self.task_uuid)
        return code

    def end_task(self):
        return boxService.box_service.EndTask(self.task_uuid)

    def stop(self):
        key = RemoteBackupHelperRemote.get_key(self.task_uuid, self.disk_token)
        ths_ins = _threading_pools.get(key)
        if ths_ins:
            ths_ins.quit()
        task_type = self.task_type()
        if task_type == xdata.REMOTE_BACKUP_QEMU_WORK:
            code = boxService.box_service.StopQemuWorkv2(self.task_uuid, self.disk_token)
            return code
        elif task_type == xdata.REMOTE_BACKUP_CDP_WORK:
            code, _ = boxService.box_service.StopCDPWork(self.task_uuid, self.disk_token)
            return code
        else:
            return 0

    def waite_ths(self):
        ths_ins = _threading_pools.get(RemoteBackupHelperRemote.get_key(self.task_uuid, self.disk_token))
        if ths_ins is None:
            return
        while ths_ins.isAlive():
            time.sleep(5)

    def _work_type(self):
        try:
            code, work_type = boxService.box_service.QueryWorkStatus(self.task_uuid, self.disk_token)
        except Exception as e:
            _logger.error('_work_type failed. {}'.format(e), exc_info=True)
            return -1
        return cover_ice_to_xdata(work_type)

    @xlogging.convert_exception_to_value(('', False))
    def _get_cdp_progress(self):
        code, last_time, queue_len = boxService.box_service.QueryCDPProgress(self.task_uuid, self.disk_token)
        if code != 0:
            return '', False
        return last_time, (last_time == 'end' and queue_len == 0)

    @xlogging.convert_exception_to_value(('', False))
    def _get_qemu_progress(self):
        code, t_bytes, com_bytes, queue_len = boxService.box_service.QueryQemuProgress(self.task_uuid, self.disk_token)
        if code != 0:
            return '', False
        progress_str = '{}/{}({})'.format(
            functions.format_size(com_bytes),
            functions.format_size(t_bytes),
            functions.format_progress(com_bytes, t_bytes)
        )
        return progress_str, (t_bytes == com_bytes and queue_len == 0)


class RemoteBackupLogicRemoteThreading(threading.Thread, WorkerLog):
    QEMU_WORK = 0
    CLOSED_CDP_WORK = 1
    NOT_CLOSED_CDP_WORK = 2
    BIT_CNT = [bin(i).count("1") for i in range(256)]

    def __init__(self, task_uuid, disk_token, disk_snapshot, disk_snapshot_list_str, start_time):
        super(RemoteBackupLogicRemoteThreading, self).__init__(name='remote_back_up_{}'.format(task_uuid))
        self.task_uuid = task_uuid
        self.disk_token = disk_token
        self.disk_snapshot = disk_snapshot
        self.disk_snapshot_list = json.loads(disk_snapshot_list_str)
        self.task_name = 'remote_back_up_{}'.format(task_uuid)  # 锁磁盘快照
        self.snap_shots = self.get_snapshots()  # 需要推送的快照链， 使用这个来获取位图
        self.task_type = self._get_task_type()
        self.need_lock_snapshots = list()  # 需要锁定的快照链，调用startQemuWorkForBitmap 用
        self.bit_map_path = self.get_bit_map_path(self.task_uuid)
        self.ex_vols = []
        self.task_handle = TaskHandle(task_uuid, disk_token)
        self._quit = False
        self.completed = False
        self.send_cmd = False
        self.start_time = start_time if start_time else 'all'

    @staticmethod
    def get_bit_map_path(task_uuid):
        return os.path.join(xdata.TMP_BITMAP_DIR, '{}_remote_backup'.format(task_uuid))

    def run(self):
        self.log_info('logic start')
        try:
            self._get_need_lock_snapshots()
            self.lock_snapshots()
            self._query_send_bitmap()
            self.insert_push_file_to_data_q()
            self.wait_data_q_finish()
        except Exception as e:
            self.log_error('logic error:{}'.format(e))
        finally:
            self.unlock_snapshots()
            self.send_cmd = True
        self.log_info('logic end')

    def _get_need_lock_snapshots(self):
        self.log_info('_get_need_lock_snapshots start')
        if self.task_type == self.QEMU_WORK:
            last_snapshot = self.snap_shots[-1]
            last_disk_snapshot = get_disk_snapshot_from_info(last_snapshot.path, last_snapshot.snapshot)
            if last_disk_snapshot.is_cdp:
                timestamp = RemoteBackupHelperRemote._from_new_disk_snapshot_info_get_last_timestamp(
                    last_snapshot.snapshot)
                if timestamp == -1:
                    restore_timestamp = None
                else:
                    restore_timestamp = timestamp
            else:
                restore_timestamp = None
            validator_list = [GetSnapshotList.is_disk_snapshot_object_exist,
                              GetSnapshotList.is_disk_snapshot_file_exist]
            snapshots = GetSnapshotList.query_snapshots_by_snapshot_object(
                last_disk_snapshot, validator_list, restore_timestamp)
            if not snapshots:
                xlogging.raise_and_logging_error('获取快照链失败', 'get snapshots failed {}'.format(last_disk_snapshot.ident))
            self.need_lock_snapshots = snapshots
        else:
            self.need_lock_snapshots = self.snap_shots
        self.log_info('_get_need_lock_snapshots end')

    def _query_send_bitmap(self):
        self.log_info('_query_send_bitmap start')
        if len(self.snap_shots) >= 1 and self.task_type == self.QEMU_WORK:
            flag = r'PiD{:x} BoxDashboard|remote backup get_used_bit_map{}'.format(os.getpid(), self.task_uuid)
            bit_map = SnapshotsUsedBitMapGeneric(self.snap_shots, flag).get()
            with open(self.bit_map_path, 'wb') as wf:
                wf.truncate(convert_disk_size2bitmap_size(self.disk_snapshot.bytes))
                wf.write(bit_map)
            self.log_info('_query_send_bitmap end')
        else:
            self.log_info('_query_send_bitmap end')
            return None

    def insert_push_file_to_data_q(self):
        self.log_info('insert_push_file_to_data_q start')
        self._send(*self._get_args())
        self.send_cmd = True
        self.log_info('insert_push_file_to_data_q end')

    def _send(self, *args):
        if self.task_type == self.QEMU_WORK:
            code = boxService.box_service.StartQemuWorkForBitmapv2(*args)
        else:
            code = boxService.box_service.StartCDPWork(*args)
        if code != 0:
            raise Exception('receive code:{}'.format(code))

    def wait_data_q_finish(self):
        self.log_info('wait_data_q_finish start')
        not_work_list = list()
        while not self._quit:
            code = self._task_done()
            task_handle = TaskHandle(self.task_uuid, self.disk_token)
            if code == 0:
                self.log_info('wait_data_q_finish work done!,{},{}'.format(self.need_lock_snapshots, self.disk_token))
                self.completed = True
                break
            if code > 0:
                raise Exception('check task_done error code:{}'.format(code))
            if code == -1:
                task_handle.progress()
            if task_handle.task_type() == xdata.REMOTE_BACKUP_NONE_WORK:
                if sum(not_work_list) > 4:
                    break
                else:
                    not_work_list.append(1)
            time.sleep(30)
        self.log_info('wait_data_q_finish end')

    def _task_done(self):
        if self.task_type == self.NOT_CLOSED_CDP_WORK:
            return self.waite_cdp_finished()

        key = "{}_{}".format(self.task_uuid, self.disk_token)
        code = get_task_code(key)
        return code

    def waite_cdp_finished(self):
        rv = -1
        while not self._quit:
            task_handle = TaskHandle(self.task_uuid, self.disk_token)
            if task_handle.task_type() == xdata.REMOTE_BACKUP_NONE_WORK:
                break
            # 产生了新的cdp 文件且当前cdp已经传输完毕
            if self.new_file_exists() and task_handle.finished():
                rv = 0
                break
            # cdp任务停止,且当前cdp已经传输完毕
            if self._is_cdp_stopped() and task_handle.finished():
                rv = 0
                break
            time.sleep(5)
        return rv

    def _is_cdp_stopped(self):
        current_host_snapshot = HTBStartTransferData.get_host_snapshot_by_disk_snapshot(self.disk_snapshot)
        host_snapshot = HostSnapshot.objects.get(id=current_host_snapshot.id)
        return host_snapshot.cdp_info.stopped

    def new_file_exists(self):
        next_valid_disk_snapshot = HTBStartTransferData.next_valid_disk_snapshot(self.disk_snapshot)
        if next_valid_disk_snapshot:
            return True
        else:
            host_snapshot = self.next_host_snapshot()
            if host_snapshot:
                return True
        return False

    def next_host_snapshot(self):
        current_host_snapshot = HTBStartTransferData.get_host_snapshot_by_disk_snapshot(self.disk_snapshot)
        new_snapshot = HostSnapshot.objects.filter(successful=True,
                                                   deleted=False,
                                                   deleting=False,
                                                   host_id=current_host_snapshot.host.id,
                                                   start_datetime__gt=current_host_snapshot.start_datetime).exists()
        if new_snapshot:
            return True
        return False

    def _get_task_type(self):
        if self.disk_snapshot_list:  # 在周期性备份的时候，始终认为同步某一时刻
            return self.QEMU_WORK
        if len(self.snap_shots) == 1 and self.disk_snapshot.is_cdp:
            if self.is_open_cdp():
                return self.NOT_CLOSED_CDP_WORK
            else:
                return self.CLOSED_CDP_WORK
        else:
            return self.QEMU_WORK

    def _get_args(self):
        if self.task_type == self.QEMU_WORK:
            return [self.task_uuid, self.disk_token, self.need_lock_snapshots, self.bit_map_path, self.ex_vols]
        elif self.task_type == self.CLOSED_CDP_WORK:
            start_time = self.start_time
            is_watch = False
            file_name = self.disk_snapshot.image_path
            return [self.task_uuid, self.disk_token, file_name, start_time, is_watch, self.ex_vols]
        elif self.task_type == self.NOT_CLOSED_CDP_WORK:
            start_time = self.start_time
            is_watch = True
            file_name = self.disk_snapshot.image_path
            return [self.task_uuid, self.disk_token, file_name, start_time, is_watch, self.ex_vols]
        else:
            return []

    def get_snapshots(self):
        if self.disk_snapshot_list:
            return [pyconv.convertJSON2OBJ(IMG.ImageSnapshotIdent, item) for item in self.disk_snapshot_list]
        else:
            path = self.disk_snapshot.image_path
            snapshot = 'all' if self.disk_snapshot.is_cdp else self.disk_snapshot.ident
            return [pyconv.convertJSON2OBJ(IMG.ImageSnapshotIdent, {'path': path, 'snapshot': snapshot})]

    def is_open_cdp(self):
        return HTBStartTransferData.check_is_unclosed_cdp(self.disk_snapshot)

    def lock_snapshots(self):
        self.log_info('lock_snapshots start')
        for snap in self.need_lock_snapshots:
            if not DiskSnapshot.is_cdp_file(snap.path):
                CompressTaskThreading().update_task_by_disk_snapshot(snap.path,
                                                                     snap.snapshot)

            DiskSnapshotLocker.lock_file(snap.path, snap.snapshot, self.task_name)
        self.log_info('lock_snapshots end')

    @xlogging.convert_exception_to_value(None)
    def unlock_snapshots(self):
        for snap in self.need_lock_snapshots:
            DiskSnapshotLocker.unlock_file(snap.path, snap.snapshot, self.task_name)

    def quit(self):
        self._quit = True

    def waite_send_cmd(self):
        while not self.send_cmd:
            time.sleep(1)


class RemoteBackupHelperRemote(object):
    @staticmethod
    def convert_objs_to_json(objs):
        data_str = serializers.serialize('json', objs, ensure_ascii=False)
        return json.loads(data_str)

    @staticmethod
    def query_host_snapshot_order_by_time(host_ident):
        return HostSnapshot.objects.filter(host__ident=host_ident,
                                           start_datetime__isnull=False,
                                           successful=True,
                                           deleted=False,
                                           deleting=False).exclude(cluster_schedule__isnull=False,
                                                                   cluster_visible=False).order_by('start_datetime')

    @staticmethod
    def query_disks_idents(disk_snapshots):
        return [disk_snapshot.disk.ident for disk_snapshot in disk_snapshots]

    @staticmethod
    def add_info_to_disk_snapshot_info(disk_snapshot_info, disk_snapshot_obj):
        if disk_snapshot_obj.parent_snapshot:
            disk_snapshot_info['fields']['parent_snapshot_ident'] = disk_snapshot_obj.parent_snapshot.ident
        else:
            disk_snapshot_info['fields']['parent_snapshot_ident'] = None

        disk_snapshot_info['fields']['is_cdp'] = disk_snapshot_obj.is_cdp
        disk_snapshot_info['fields']['disk_ident'] = disk_snapshot_obj.disk.ident
        if disk_snapshot_obj.is_cdp:
            disk_snapshot_info['fields']['first_timestamp'] = disk_snapshot_obj.cdp_info.first_timestamp
        else:
            disk_snapshot_info['fields']['first_timestamp'] = None

    @staticmethod
    def add_disk_ident_to_disk_snapshot_chain(disk_snapshot_chain):
        for disk_snapshot_info in disk_snapshot_chain:
            disk_snapshot_obj = DiskSnapshot.objects.get(id=disk_snapshot_info['pk'])
            RemoteBackupHelperRemote.add_info_to_disk_snapshot_info(disk_snapshot_info, disk_snapshot_obj)

        return disk_snapshot_chain

    @staticmethod
    def add_boot_map_to_host_snapshot_sys_info(host_ident, host_snapshot):
        snapshot_ext_info = json.loads(host_snapshot['fields']['ext_info'])
        host_ext_info = json.loads(Host.objects.get(ident=host_ident).ext_info)
        if 'BootMap' in host_ext_info['system_infos']:
            snapshot_ext_info['system_infos']['BootMap'] = host_ext_info['system_infos']['BootMap']
            host_snapshot['fields']['ext_info'] = json.dumps(snapshot_ext_info)

        return host_snapshot

    @staticmethod
    def query_latest_host_backup(host_ident, last_host_snapshot_id, last_datetime):
        last_host_snapshot_id = int(last_host_snapshot_id)

        if last_host_snapshot_id <= 0:  # 1.返回最新的: 主机快照, 磁盘快照链
            latest_host_snapshot, latest_disk_snapshots = RemoteBackupHelperRemote.get_host_latest_snapshots(host_ident)
            if not latest_disk_snapshots:
                return None
            return RemoteBackupHelperRemote._convert_host_snapshot_2_new_host_backup_info_with_snapshots(
                host_ident, latest_host_snapshot, latest_disk_snapshots)

        # 查询是否有最新的状态产生
        else:
            host_snapshots = RemoteBackupHelperRemote.query_host_snapshot_order_by_time(host_ident)  # 获取当前主机可用的快照点
            cur_snapshot = HostSnapshot.objects.get(id=last_host_snapshot_id)
            cur_snapshot_time = cur_snapshot.start_datetime  # 2.返回最新的: 主机快照, 磁盘快照
            next_host_snapshot = host_snapshots.filter(
                start_datetime__gt=cur_snapshot_time).last()
            if last_datetime == '':  # 最后一个点同步的是普通备份点
                if not next_host_snapshot:
                    return None
                else:
                    pass
            else:
                if not next_host_snapshot:  # 不存在的情况下
                    last_backup_datetime = xdatetime.string2datetime(last_datetime)
                    if cur_snapshot.cdp_info.last_datetime > last_backup_datetime:
                        pass
                    else:
                        return None

            latest_host_snapshot, latest_disk_snapshots = RemoteBackupHelperRemote.get_host_latest_snapshots(
                host_ident)
            if not latest_disk_snapshots:
                return None

            return RemoteBackupHelperRemote._convert_host_snapshot_2_new_host_backup_info_with_snapshots(
                host_ident, latest_host_snapshot, latest_disk_snapshots)

    @staticmethod
    def query_new_host_backup(host_ident, last_host_snapshot_id):
        last_host_snapshot_id = int(last_host_snapshot_id)
        host_snapshots = RemoteBackupHelperRemote.query_host_snapshot_order_by_time(host_ident)

        if last_host_snapshot_id <= 0:  # 1.返回最新的: 主机快照, 磁盘快照链
            last_host_snapshot = host_snapshots.last()
            if not last_host_snapshot:
                return None
            return RemoteBackupHelperRemote._convert_host_snapshot_2_new_host_backup_info(
                host_ident, last_host_snapshot, True)

        next_host_snapshot = host_snapshots.filter(
            start_datetime__gt=HostSnapshot.objects.get(id=last_host_snapshot_id).start_datetime).order_by(
            'start_datetime').first()
        if not next_host_snapshot:
            return None
        return RemoteBackupHelperRemote._convert_host_snapshot_2_new_host_backup_info(
            host_ident, next_host_snapshot)

    @staticmethod
    def _convert_host_snapshot_2_new_host_backup_info(host_ident, last_host_snapshot, first_snapshot=False):
        disk_snapshot_chain = []  # 每个磁盘的链: [[disk1_chain],[disk2_chain]]
        disks_snapshots = last_host_snapshot.disk_snapshots.all()
        for disk_snapshot in disks_snapshots:
            if first_snapshot:
                validators = [GetSnapshotList.is_disk_snapshot_object_exist,
                              GetSnapshotList.is_disk_snapshot_file_exist]
                disk_chain = GetSnapshotList.query_snapshots_by_snapshot_object(disk_snapshot, validators)
                disk_chain = [get_disk_snapshot_from_info(img.path, img.snapshot) for img in disk_chain]
            else:
                disk_chain = [disk_snapshot]
            assert disk_chain, '无效的快照链 {}'.format(disk_snapshot)
            disk_chain = RemoteBackupHelperRemote.convert_objs_to_json(disk_chain)
            disk_chain = RemoteBackupHelperRemote.add_disk_ident_to_disk_snapshot_chain(disk_chain)
            disk_snapshot_chain.append(disk_chain)
        host_snapshot = RemoteBackupHelperRemote.convert_objs_to_json([last_host_snapshot])[0]
        # 修正时间，serializers.serialize 会把时间变成 YYYY-MM-DDTHH:mm:ss.sss 损失精度
        host_snapshot['fields']['start_datetime'] = last_host_snapshot.start_datetime.strftime(
            xdatetime.FORMAT_WITH_MICROSECOND)
        host_snapshot = RemoteBackupHelperRemote.add_boot_map_to_host_snapshot_sys_info(host_ident, host_snapshot)
        host_snapshot['disks_idents'] = RemoteBackupHelperRemote.query_disks_idents(disks_snapshots)
        host_snapshot['disks_chains'] = disk_snapshot_chain
        return host_snapshot

    @staticmethod
    def _convert_host_snapshot_2_new_host_backup_info_with_snapshots(host_ident, last_host_snapshot,
                                                                     latest_disk_snapshots):
        disk_snapshot_chain = []  # 每个磁盘的链: [[disk1_chain],[disk2_chain]]
        disks_idents = []
        for disk_ident, disk_snapshots in latest_disk_snapshots.items():
            disk_chain = [get_disk_snapshot_from_info(img.path, img.snapshot) for img in disk_snapshots]
            disk_chain = RemoteBackupHelperRemote.convert_objs_to_json(disk_chain)
            RemoteBackupHelperRemote.add_snapshot_info(disk_chain, disk_snapshots)
            disk_chain = RemoteBackupHelperRemote.add_disk_ident_to_disk_snapshot_chain(disk_chain)
            disk_snapshot_chain.append(disk_chain)
            disks_idents.append(disk_ident)
        host_snapshot = RemoteBackupHelperRemote.convert_objs_to_json([last_host_snapshot])[0]
        # 修正时间，serializers.serialize 会把时间变成 YYYY-MM-DDTHH:mm:ss.sss 损失精度
        host_snapshot['fields']['start_datetime'] = last_host_snapshot.start_datetime.strftime(
            xdatetime.FORMAT_WITH_MICROSECOND)
        host_snapshot = RemoteBackupHelperRemote.add_boot_map_to_host_snapshot_sys_info(host_ident, host_snapshot)
        host_snapshot['disks_idents'] = disks_idents
        host_snapshot['disks_chains'] = disk_snapshot_chain
        if last_host_snapshot.is_cdp:
            host_snapshot['cdp_end_time'] = last_host_snapshot.cdp_info.last_datetime.strftime(
                xdatetime.FORMAT_WITH_MICROSECOND)
        else:
            host_snapshot['cdp_end_time'] = ''
        return host_snapshot

    @staticmethod
    def query_new_disk_backup(host_snapshot_id, last_disk_snapshot_ident):  # 查询最新: "磁盘快照" (.cdp)
        host_snapshot_id = int(host_snapshot_id)  # TODO 简化处理，假设仅会查询cdp，有且仅有一个
        last_disk_snapshot = DiskSnapshot.objects.get(ident=last_disk_snapshot_ident)
        try:
            next_disk_snapshot = last_disk_snapshot.child_snapshots.get(host_snapshot__isnull=True)
            disk_snapshot_info = RemoteBackupHelperRemote.convert_objs_to_json([next_disk_snapshot])[0]
            RemoteBackupHelperRemote.add_info_to_disk_snapshot_info(disk_snapshot_info, next_disk_snapshot)
            return disk_snapshot_info
        except DiskSnapshot.DoesNotExist:
            return None

    # 获取主机的磁盘最新的状态
    @staticmethod
    def get_host_latest_snapshots(host_ident, inc_all=False):
        snapshots = RemoteBackupHelperRemote.query_host_snapshot_order_by_time(host_ident)
        if snapshots.last():
            return snapshots.last(), RemoteBackupHelperRemote._get_host_snapshot_latest_disk_snapshots(snapshots.last(),
                                                                                                       inc_all)
        return None, {}

    # 获取快照点最新的 快照链
    @staticmethod
    def _get_host_snapshot_latest_disk_snapshots(host_snapshot, inc_call):
        disk_snapshot_chain = {}
        if host_snapshot.is_cdp:
            disks_snapshots = host_snapshot.disk_snapshots.all()
            for disk_snapshot in disks_snapshots:
                disk_ident = disk_snapshot.disk.ident
                timestamp = host_snapshot.cdp_info.last_datetime.timestamp()

                disk_snapshot_ident, restore_timestamp = \
                    GetDiskSnapshot.query_cdp_disk_snapshot_ident(host_snapshot, disk_ident, timestamp)
                if disk_snapshot_ident is None or restore_timestamp is None:
                    disk_snapshot_ident = GetDiskSnapshot.query_normal_disk_snapshot_ident(host_snapshot,
                                                                                           disk_ident)
                disk_snapshot_object = DiskSnapshot.objects.get(ident=disk_snapshot_ident)

                validator_list = [GetSnapshotList.is_disk_snapshot_object_exist,
                                  GetSnapshotList.is_disk_snapshot_file_exist]
                disk_chain = GetSnapshotList.query_snapshots_by_snapshot_object(
                    disk_snapshot_object, validator_list, restore_timestamp, include_all_node=inc_call)
                assert disk_chain, '无效的快照链 {}'.format(disk_snapshot)
                disk_snapshot_chain[disk_snapshot.disk.ident] = disk_chain
        else:
            disks_snapshots = host_snapshot.disk_snapshots.all()
            for disk_snapshot in disks_snapshots:
                validators = [GetSnapshotList.is_disk_snapshot_object_exist,
                              GetSnapshotList.is_disk_snapshot_file_exist]
                disk_chain = GetSnapshotList.query_snapshots_by_snapshot_object(disk_snapshot, validators,
                                                                                include_all_node=inc_call)
                assert disk_chain, '无效的快照链 {}'.format(disk_snapshot)
                disk_snapshot_chain[disk_snapshot.disk.ident] = disk_chain

        return disk_snapshot_chain

    @staticmethod
    def _from_new_disk_snapshot_info_get_last_timestamp(snapshot):
        if snapshot == 'all':
            return -1
        assert ('$' in snapshot or '~' in snapshot), 'invalid cdp snapshot:{}'.format(snapshot)
        if '$' in snapshot:
            datetime_str = snapshot.replace('$', '').replace('~', '')
        else:
            datetime_str = snapshot.split('~')[1]
        return datetime.strptime(datetime_str, '%Y-%m-%d-%H:%M:%S.%f').timestamp()

    @staticmethod
    def _modify_cdp_disk_snapshot_info_with_last_timestamp(cdp_disk_snapshot_info, last_timestamp):
        assert isinstance(last_timestamp, float), 'not invalid info:{}'.format(last_timestamp)
        cdp_timestamp = RemoteBackupHelperRemote._from_new_disk_snapshot_info_get_last_timestamp(
            cdp_disk_snapshot_info.snapshot)
        if cdp_timestamp == -1:
            if GetSnapshotList.is_cdp_timestamp_exist(cdp_disk_snapshot_info.path,
                                                      last_timestamp):
                return GetSnapshotList.format_timestamp(last_timestamp, None), False
            else:
                return None, True
        elif last_timestamp < cdp_timestamp:
            if GetSnapshotList.is_cdp_timestamp_exist(cdp_disk_snapshot_info.path,
                                                      last_timestamp):
                return GetSnapshotList.format_timestamp(last_timestamp, cdp_timestamp), False
            else:
                return None, True
        else:
            return None, False

    @staticmethod
    def query_latest_disk_backup(host_snapshot_id, last_disk_snapshot_ident, last_timestamp):  # 查询最新: "磁盘快照" (.cdp)
        _logger.info(
            'query_latest_disk_backup host_snapshot_id:{}, last_disk_snapshot_ident:{}, last_timestamp:{}'.format(
                host_snapshot_id, last_disk_snapshot_ident, last_timestamp))
        host_snapshot_id = int(host_snapshot_id)
        last_timestamp = float(last_timestamp)
        host = HostSnapshot.objects.get(id=host_snapshot_id).host
        latest_host_snapshot, latest_disk_snapshots = RemoteBackupHelperRemote.get_host_latest_snapshots(host.ident,
                                                                                                         inc_all=True)
        if not latest_disk_snapshots:
            _logger.error('query_latest_disk_backup latest_disk_snapshots is empty!')
            return None
        last_disk_snapshot = DiskSnapshot.objects.get(ident=last_disk_snapshot_ident)
        # 在快照链中 求取差异部分
        new_backup_list = list()
        is_family = True
        if last_disk_snapshot.disk.ident in latest_disk_snapshots:
            disk_snapshots = latest_disk_snapshots[last_disk_snapshot.disk.ident]
            find_key = False
            for disk_snapshot_info in disk_snapshots:
                disk_snapshot_object = get_disk_snapshot_from_info(disk_snapshot_info.path, disk_snapshot_info.snapshot)
                if disk_snapshot_object.ident == last_disk_snapshot.ident:
                    find_key = True
                    if disk_snapshot_object.is_cdp:
                        if last_timestamp == -1:
                            continue
                        new_time_str, need_all_backup = \
                            RemoteBackupHelperRemote._modify_cdp_disk_snapshot_info_with_last_timestamp(
                                disk_snapshot_info, last_timestamp)
                        _logger.info('query_latest_disk_backup disk_snapshot_info:{} '
                                     'last_timestamp:{} '
                                     'new_time_str:{},'
                                     'need_all_backup:{}'.format(disk_snapshot_info, last_timestamp, new_time_str,
                                                                 need_all_backup))

                        # 需要完整备份
                        if need_all_backup:
                            is_family = False
                            new_backup_list = disk_snapshots
                            break
                        if new_time_str:
                            disk_snapshot_info.snapshot = new_time_str
                            new_backup_list.append(disk_snapshot_info)
                        else:
                            return {'is_family': True, 'disk_snapshot_info': []}  # 不需要备份
                else:
                    if find_key:
                        new_backup_list.append(disk_snapshot_info)

            if not find_key and not disk_snapshots:
                _logger.warning('query_latest_disk_backup not find_key!')
                is_family = False
                new_backup_list = disk_snapshots

        else:  # 没有在一个链中 需要根据native guid 获取 最新的链
            is_family = False
            last_host_snapshot = GetSnapshotList.get_host_snapshot_by_disk_snapshot(last_disk_snapshot)
            native_guid = RemoteBackupHelperRemote.get_disk_native_guid(last_disk_snapshot_ident, last_host_snapshot)
            if not native_guid:
                _logger.error('query_latest_disk_backup not find native guid, last_host_snapshot:{} '
                              'last_disk_snapshot:{}'.format(
                    last_host_snapshot, last_disk_snapshot))
                return None
            disk_snapshot = GetDiskSnapshot.get_by_native_guid_and_host_snapshot(native_guid, latest_host_snapshot)
            disk_ident = disk_snapshot.disk.ident
            new_backup_list = latest_disk_snapshots[disk_ident]

        new_backup_object_list = [get_disk_snapshot_from_info(item.path, item.snapshot) for item in
                                  GetSnapshotList.clean_not_key_snapshots(new_backup_list)]

        disk_snapshot_info = RemoteBackupHelperRemote.convert_objs_to_json(new_backup_object_list)
        RemoteBackupHelperRemote.add_snapshot_info(disk_snapshot_info, new_backup_list)
        RemoteBackupHelperRemote.add_disk_ident_to_disk_snapshot_chain(disk_snapshot_info)
        result = {'is_family': is_family, 'disk_snapshot_info': disk_snapshot_info}
        _logger.info('query_latest_disk_backup out:{}'.format(pprint.pformat(result)))
        return result

    @staticmethod
    def add_snapshot_info(disk_snapshot_info, new_backup_list):
        for index, item in enumerate(disk_snapshot_info):
            item['snapshot'] = new_backup_list[index].snapshot

    @staticmethod
    def get_disk_native_guid(disk_ident, host_snapshot):
        info = json.loads(host_snapshot.ext_info)
        for disk_info in info['include_ranges']:
            if disk_info['diskSnapshot'] == disk_ident:
                return disk_info['diskNativeGUID']
        return None

    @staticmethod
    def query_is_host_cdp_back_end(host_snapshot_id):
        try:
            cdp_task = HostSnapshot.objects.get(id=host_snapshot_id).cdp_task
            is_task_end = cdp_task.finish_datetime is not None
            return {'cdp_task_end': is_task_end}
        except CDPTask.DoesNotExist:
            _logger.warning('query_is_host_cdp_back_end CDPTask.DoesNotExist : {}'.format(host_snapshot_id))
            return {'cdp_task_end': True}

    @staticmethod
    def kill_remote_backup_logic(task_uuid, disk_token):
        task_handle = TaskHandle(task_uuid, disk_token)
        rv = -1
        try:
            rv = task_handle.stop()
        except Exception as e:
            _logger.error('kill_remote_backup_logic error:{}'.format(e))
        finally:
            task_handle.end_task()
            task_handle.waite_ths()
            _threading_pools.delete(RemoteBackupHelperRemote.get_key(task_uuid, disk_token))
            boxService.box_service.remove(RemoteBackupLogicRemoteThreading.get_bit_map_path(task_uuid))
        return {'code': rv}

    @staticmethod
    def close(task_uuid, disk_token):
        task_handle = TaskHandle(task_uuid, disk_token)
        return {'code': task_handle.close()}

    @staticmethod
    def start_remote_backup_logic(task_uuid, disk_token, disk_snapshot_ident, disk_snapshot_list, start_time):
        try:
            disk_snapshot = get_object_or_404(DiskSnapshot, ident=disk_snapshot_ident)
            key = RemoteBackupHelperRemote.get_key(task_uuid, disk_token)
            if _threading_pools.get(key):
                raise Exception('task exists:{}'.format(key))
            rs_backup = RemoteBackupLogicRemoteThreading(task_uuid, disk_token, disk_snapshot, disk_snapshot_list,
                                                         start_time)
            rs_backup.setDaemon(True)
            rs_backup.start()
            _threading_pools.put(key, rs_backup)
            rs_backup.waite_send_cmd()
            return {'sub_task_type': rs_backup.task_type, 'code': 0}
        except Exception as e:
            _logger.error('start_remote_backup_logic error:{}'.format(e), exc_info=True)
            raise e

    @staticmethod
    def query_remote_backup_status(task_uuid, disk_token):
        task_handle = TaskHandle(task_uuid, disk_token)
        ins = RemoteBackupHelperRemote.get_back_up_instance(task_uuid, disk_token)
        if not ins:
            raise Exception('not find threading!')
        return {
            'work_type': task_handle.task_type(),
            'progress': task_handle.progress(),
            'finished': ins.completed,
            'task_uuid': task_uuid
        }

    @staticmethod
    def get_key(task_uuid, disk_token):
        return '{}_{}'.format(task_uuid, disk_token)

    @staticmethod
    def get_back_up_instance(task_uuid, disk_token):
        key = RemoteBackupHelperRemote.get_key(task_uuid, disk_token)
        return _threading_pools.get(key)

    @staticmethod
    def close_remote_backup_logic(task_uuid, disk_token):
        task_handle = TaskHandle(task_uuid, disk_token)
        return {'code': task_handle.close()}

    @staticmethod
    def check_qcow_file_exists(snapshots):
        """
        snapshots [ base, inc1, inc2]
        :param snapshots:
        :return:
        """
        snapshots = json.loads(snapshots)
        snapshots.reverse()
        _logger.info('check_qcow_file_exists snapshots:{}'.format(snapshots))
        rs = {'is_valid': True}
        try:
            for index, snapshot in enumerate(snapshots):
                disk_snapshot = get_disk_snapshot_from_info(snapshot['path'], snapshot['snapshot'])
                if index == 0:
                    if not GetSnapshotList.is_schedule_valid(disk_snapshot):
                        rs['is_valid'] = False
                        break
                    if not GetSnapshotList.is_disk_snapshot_object_exist(disk_snapshot):
                        rs['is_valid'] = False
                        break
                if not GetSnapshotList.is_disk_snapshot_file_exist(disk_snapshot):
                    rs['is_valid'] = False
                    break

        except Exception as e:
            rs['is_valid'] = False
            _logger.error('check_qcow_file_exists error:{}'.format(e), exc_info=True)
        _logger.info('check_qcow_file_exists is_valid:{}'.format(rs['is_valid']))
        return rs


class InnerTest(object):
    """
    'from apiv1.remote_backup_logic_remote import *'
    """

    def __init__(self):
        self.task_uuid = '31b7dea05b654756aec5613678a83911'
        self.disk_token = '31b7dea05b654756aec5613678a83911'
        self.disk_snapshot_ident = DiskSnapshot.objects.get(id=2).ident

    def start(self):
        rs = RemoteBackupHelperRemote.start_remote_backup_logic(self.task_uuid,
                                                                self.disk_token, self.disk_snapshot_ident)
        print(rs)

    def query(self):
        rs = RemoteBackupHelperRemote.query_remote_backup_status(self.task_uuid,
                                                                 self.disk_token)
        print(rs)

    def kill(self):
        rs = RemoteBackupHelperRemote.kill_remote_backup_logic(self.task_uuid,
                                                               self.disk_token)
        print(rs)
