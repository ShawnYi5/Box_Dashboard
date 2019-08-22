import contextlib
import datetime
import json
import os
import threading
import time
import traceback
from collections import namedtuple
from itertools import groupby
import mmap
import uuid

from xdashboard.handle.authorize import authorize_init
from django.db.models import Count
from django.utils import timezone
from rest_framework import status
from taskflow import engines
from taskflow import task
from taskflow.listeners import logging as logging_listener
from taskflow.patterns import linear_flow as lf
from taskflow.persistence import models

from apiv1 import ClientIpMg, tasks
from apiv1.compress import CompressTaskThreading
from apiv1.models import HTBTask, HostSnapshot, DiskSnapshot, HTBSendTask, HTBSchedule, HostMac, RestoreTarget, \
    HostLog, RestoreTargetDisk, Host
from apiv1.restore import PeRestore
from apiv1.snapshot import GetSnapshotList, Tokens, SnapshotsUsedBitMap, DiskSnapshotLocker, GetDiskSnapshot, \
    LineIntervalToBitMap, SnapshotsUsedBitMapGeneric, DiskSnapshotHash
from apiv1.spaceCollection import CDPHostSnapshotSpaceCollectionMergeTask
from apiv1.views import HostSnapshotLocalRestore, TokenInfo, HostSnapshotsWithNormalPerHost, \
    HostSnapshotsWithCdpPerHost, HostSnapshotRestore, DiskVolMap, HostSessionDisks
from box_dashboard import xlogging, xdatetime, boxService, pyconv, xdata, task_backend, functions
from xdashboard.common.dict import GetDictionary
from xdashboard.models import DataDictionary

_logger = xlogging.getLogger(__name__)

import IMG
import DataQueuingIce
import KTService

TASK_ID_PUSH_FORMAT = 'push_{}'
TASK_ID_MIGRATE_FORMAT = 'migrate_{}'


def remove(path, log_func=None):
    if os.path.exists('/dev/shm/debug_htb'):
        if not log_func:
            log_func = _logger.warning
        log_func('exists /dev/shm/debug_htb, skip remove {}'.format(path))
    else:
        boxService.box_service.remove(path)


def get_lock_name(task_id):
    return 'htb_task_{}'.format(task_id)


def user_stop(task_id):
    schedule = HTBTask.objects.get(id=task_id).schedule
    is_stop = not schedule.enabled
    is_stop1 = schedule.deleted
    return is_stop or is_stop1


def test_host_is_ok(ip):
    if HTBInit.is_valid_ip(ip):
        return ClientIpMg.ClientIpSwitch.ping_exist(ip)
    else:
        return False


def host_is_ok(ext_config):
    ret_list = list()
    detect_aio_2_master_business_ip = ext_config["detect_aio_2_master_business_ip"]
    detect_aio_2_master_control_ip = ext_config["detect_aio_2_master_control_ip"]
    if detect_aio_2_master_business_ip:
        business = ext_config["master_adpter"].get('business', list())
        for business_ips in business:
            for ip_mask in business_ips.get('ips', list()):
                ip = ip_mask["ip"]
                ret_list.append({"ip": ip, 'r': test_host_is_ok(ip)})

    if detect_aio_2_master_control_ip:
        control = ext_config["master_adpter"].get('control', list())
        for control_ips in control:
            for ip_mask in control_ips.get('ips', list()):
                ip = ip_mask["ip"]
                ret_list.append({"ip": ip, 'r': test_host_is_ok(ip)})

    if len(ret_list) > 0:
        allFailed = True
        for test in ret_list:
            if test["r"] == True:
                allFailed = False
        if allFailed:
            return False

    return True


def is_host_failure(schedule, ext_config):
    test_timeinterval = int(ext_config["test_timeinterval"])
    test_frequency = int(ext_config["test_frequency"])

    test_count = int(ext_config.get('test_count', 0))
    last_test_time = ext_config.get('last_test_time', 0)
    if time.time() - last_test_time >= test_timeinterval:
        last_test_time = time.time()
        if host_is_ok(ext_config):
            test_count = 0
        else:
            test_count = test_count + 1
        HTBSchedule.update_config(schedule.id, [('last_test_time', last_test_time), ('test_count', test_count)])
        if test_count >= test_frequency:
            return True

    return False


def get_newest_pointid(ext_config, schedule):
    points = list()
    point = ext_config['pointid']
    _id = point.split('|')[1]
    base_host_snapshot = HostSnapshot.objects.get(id=_id)
    host_ident = schedule.host.ident
    start_date = base_host_snapshot.start_datetime

    api_request = {'begin': start_date.strftime(xdatetime.FORMAT_ONLY_DATE),
                   'finish': True,
                   'use_serializer': False}

    api_response = HostSnapshotsWithNormalPerHost().get(request=None, ident=host_ident, api_request=api_request)
    if not status.is_success(api_response.status_code):
        _logger.info('get_newest_pointid HostSnapshotsWithNormalPerHost Failed.status_code={},host_ident={}'.format(
            api_response.status_code, host_ident))
        return ext_config['pointid']

    for host_snapshot in api_response.data:
        backup_point = {
            "id": '{}|{}|{}'.format(xdata.SNAPSHOT_TYPE_NORMAL, host_snapshot['id'], host_snapshot['start_datetime']),
            "endtime": host_snapshot['start_datetime'], "type": xdata.SNAPSHOT_TYPE_NORMAL}
        points.append(backup_point)

    api_response = HostSnapshotsWithCdpPerHost().get(None, ident=host_ident, api_request=api_request)
    if not status.is_success(api_response.status_code):
        _logger.info('get_newest_pointid HostSnapshotsWithCdpPerHost Failed.status_code={},host_ident={}'.format(
            api_response.status_code, host_ident))
        return ext_config['pointid']

    for host_snapshot in api_response.data:
        backup_point = {"id": '{}|{}|{}|{}'.format(xdata.SNAPSHOT_TYPE_CDP, host_snapshot['id'], host_snapshot['begin'],
                                                   host_snapshot['end']),
                        "time": '{} - {}'.format(host_snapshot['begin'], host_snapshot['end']),
                        "endtime": host_snapshot['end'],
                        "type": xdata.SNAPSHOT_TYPE_CDP}
        points.append(backup_point)

    if len(points) == 0:
        _logger.info('get_newest_pointid Failed.len(points)==0')
        return ext_config['pointid']

    points.sort(key=lambda x: (xdatetime.string2datetime(x['endtime'])), reverse=True)
    return points[0]["id"]


def auto_switch(task_id):
    schedule = HTBTask.objects.get(id=task_id).schedule
    ext_config = json.loads(schedule.ext_config)
    if 'manual_switch' in ext_config:
        if ext_config['manual_switch']['status'] == 2:
            return
    if int(ext_config['switchtype']) != 2:
        return
    if not is_host_failure(schedule, ext_config):
        return
    use_latest = 1
    switchip = 1
    task_type = schedule.task_type
    point_id = None
    if task_type == schedule.NEW_POINT_NEED_UPDATE:
        point_id = get_newest_pointid(ext_config, schedule)
    restoretime = None
    manual_switch = {'task_type': task_type, 'point_id': point_id, 'restoretime': restoretime,
                     'switchip': switchip, 'status': 1, 'use_latest': use_latest}
    HTBSchedule.update_config(schedule.id, [('manual_switch', manual_switch)])

    dest = '自动切换到备机'
    reason = {'htb_task': task_id, 'debug': manual_switch, "description": dest}
    HostLog.objects.create(host=schedule.host, type=HostLog.LOG_HTB, reason=json.dumps(reason, ensure_ascii=False))


@xlogging.convert_exception_to_value(False)
def should_switch_ip(task_id):
    if not change_master_ip(task_id):  # 禁用IP漂移 意味着不进行IP的切换
        _logger.info('should_switch_ip, not change master ip means not switch ip. task id {}'.format(task_id))
        return False
    schedule = HTBTask.objects.get(id=task_id).schedule
    exc_info = json.loads(schedule.ext_config)
    val = exc_info['manual_switch'].get('switchip', False)
    if int(val) == 1:
        return True
    return False


def check_should_switch(task_id, index):
    if index == 0:
        auto_switch(task_id)
    schedule = HTBTask.objects.get(id=task_id).schedule
    exc_info = json.loads(schedule.ext_config)
    if 'manual_switch' in exc_info:
        if exc_info['manual_switch']['status'] == 1:
            exc_info['manual_switch']['status'] = 2
            schedule.ext_config = json.dumps(exc_info)
            schedule.save(update_fields=['ext_config'])
            return True
        elif exc_info['manual_switch']['status'] == 2:
            return True
        else:
            return False

    return False


def get_switch_time(task_id):
    try:
        schedule = HTBTask.objects.get(id=task_id).schedule
        exc_info = json.loads(schedule.ext_config)
        item_list = exc_info['manual_switch']['point_id'].split('|')
        time_str = exc_info['manual_switch']['restoretime']
        if item_list[0] == 'normal':
            item_list[2] = item_list[2].replace('T', ' ')
            return datetime.datetime.strptime(item_list[2], '%Y-%m-%d %H:%M:%S.%f')
        else:
            time_str = time_str.replace('T', ' ')
            return datetime.datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S.%f')
    except Exception as e:
        _logger.info('get_switch_time catch error:{}'.format(e))
        raise e


def get_switch_type(task_id):
    try:
        schedule = HTBTask.objects.get(id=task_id).schedule
        exc_info = json.loads(schedule.ext_config)
        use_latest = exc_info['manual_switch']['use_latest']
        if int(use_latest):
            return xdata.HTB_TASK_TYPE_LATEST
        else:
            return xdata.HTB_TASK_TYPE_HISTORY
    except Exception as e:
        _logger.info('get_switch_time catch error:{}'.format(e))
        raise e


def get_restore_info(task_id):
    try:
        schedule = HTBTask.objects.get(id=task_id).schedule
        exc_info = json.loads(schedule.ext_config)
        restore_info = exc_info['manual_switch']
        return restore_info
    except Exception as e:
        _logger.info('get_restore_info catch error:{}'.format(e))
    return None


def get_exc_info(task_obj):
    return json.loads(task_obj.schedule.ext_config)


@xlogging.LockDecorator(boxService.message_dict_locker)
def get_task_code(key):
    """
    :param key: 任务的uuid
    :return: 0(successful) 1(failed) -1(not report)
    """
    try:
        rev = boxService.message_dict[key].get_nowait()
    except Exception:
        return -1
    else:
        rev = int(rev)
        if rev == 0:
            return 0
        else:
            return 1


@xlogging.LockDecorator(boxService.bit_map_locker)
def get_bit_map(key):
    return boxService.bit_map_object.get(key, list())


def finish_target(restore_target, successful, msg, debug):
    try:
        if not restore_target or not restore_target.restore:
            _logger.debug('finish_target has not restore task! restore_target_ident:{}'.format(restore_target.ident))
            return None
        _logger.debug('finish restore task:{}'.format(restore_target.restore.id))
        pe_restore = PeRestore(restore_target)
        pe_restore.unlock_disk_snapshots('restore_{}'.format(restore_target.restore.id))
        pe_restore.unlock_disk_snapshots('volume_restore_{}'.format(restore_target.restore.id))
        tasks.finish_restore_task_object('', restore_target.restore.id, successful, msg, debug)

        for disk in restore_target.disks.all():
            try:
                boxService.box_service.updateToken(
                    KTService.Token(token=disk.token, snapshot=[], expiryMinutes=0))
            except Exception as e:
                _logger.warning('call boxService.updateToken failed. {}'.format(e))
    except Exception as e:
        _logger.error('finish_target error:{}'.format(e))
        _logger.error('{}'.format(traceback.format_exc()))


def unlock_restore_files(htb_task):
    try:
        restore_target = get_restore_target(htb_task)
        restore_info = json.loads(restore_target.info)
        name = 'restore_{}'.format(restore_target.restore.id)
        restore_files = restore_info.get('restore_files', None)
        if restore_files:
            for restore_file in restore_files:
                snaps = [pyconv.convertJSON2OBJ(IMG.ImageSnapshotIdent, snap) for snap in
                         restore_file['snapshot']]
                DiskSnapshotLocker.unlock_files(snaps, name)
    except Exception as e:
        _logger.error('unlock_restore_files fail :{}'.format(e))
        _logger.error('{}'.format(traceback.format_exc()))


def get_disk_snapshot_from_info(path, snapshot):
    if DiskSnapshot.is_cdp_file(path):
        return DiskSnapshot.objects.get(image_path=path)
    else:
        return DiskSnapshot.objects.get(image_path=path, ident=snapshot)


@xlogging.convert_exception_to_value(None)
def get_restore_target(htb_task):
    htb_task = HTBTask.objects.get(id=htb_task.id)
    restore_target_ident = htb_task.restore_target.ident
    return RestoreTarget.objects.get(ident=restore_target_ident)


def src_is_windows(htb_task):
    host = htb_task.schedule.host
    system_infos = json.loads(host.ext_info)['system_infos']
    return 'LINUX' not in system_infos['System']['SystemCaption'].upper()


@xlogging.convert_exception_to_value('DISK')
def get_disk_name(disk_snapshot):
    host_snapshot = HTBStartTransferData.get_host_snapshot_by_disk_snapshot(disk_snapshot)
    ext_info = json.loads(host_snapshot.ext_info)
    is_linux = 'LINUX' in ext_info['system_infos']['System']['SystemCaption'].upper()
    if is_linux or host_snapshot.is_cdp:
        calc_used = False
    else:
        calc_used = True
    # 2个地方不能够对应起来，只有通过 native guid 对应
    for include_range in ext_info['include_ranges']:
        if include_range['diskIdent'] == disk_snapshot.disk.ident:
            break
    else:
        _logger.warning('get_disk_name disk_snapshot:{}, not found from disk.ident')
        return 'DISK'

    for disk in ext_info['system_infos']['Disk']:
        if disk['NativeGUID'] == include_range['diskNativeGUID']:
            break
    else:
        _logger.warning('get_disk_name disk_snapshot:{}, not found from diskNativeGUID')
        return 'DISK'

    return HostSessionDisks.disk_label_for_human(disk, calc_used)


@xlogging.convert_exception_to_value(False)
def restore_success(task_obj):
    restore_target = get_restore_target(task_obj)
    rs_task = restore_target.restore
    if not rs_task:
        return False
    # 快速还原
    if rs_task.successful:
        return True
    # 普通还原不应该finish
    if rs_task.finish_datetime:
        return False
    return True


def check_is_sys_restore(htb_task):
    return htb_task.schedule.restore_type == HTBSchedule.HTB_RESTORE_TYPE_SYSTEM


def update_ip_config(task_content, task_id):
    task = HTBTask.objects.get(id=task_id)
    ext_config = get_exc_info(task)
    task_content['master_adapter'], task_content['standby_adapter'] = HTBInit.get_ip_info(
        task_id, ext_config['master_adpter'], ext_config['standby_adpter'])
    return None


@xlogging.convert_exception_to_value(False)
def standby_restart_mod(htb_task):
    return 'standby_restart' in htb_task.ext_config


# 切换的时候，是否需要改变原机的IP
def change_master_ip(task_id):
    task = HTBTask.objects.get(id=task_id)
    ext_config = get_exc_info(task)
    return ext_config.get('switch_change_master_ip', True)


def convert_disk_size2bitmap_size(disk_bytes, blk_size=64 * 1024):
    return (((disk_bytes + blk_size - 1) // blk_size) + 8 - 1) // 8


class WorkerLog(object):
    name = ''

    def log_debug(self, msg):
        _logger.debug('{}: {}'.format(self.name, msg))

    def log_info(self, msg):
        _logger.info('{}: {}'.format(self.name, msg))

    def log_warning(self, msg):
        _logger.warning('{}: {}'.format(self.name, msg))

    def log_error(self, msg):
        _logger.error('{}: {}'.format(self.name, msg), exc_info=True)

    def log_error_and_raise_exception(self, msg):
        _logger.error('{}: {}'.format(self.name, msg), exc_info=True)
        raise Exception(msg)


class SendTaskWork(WorkerLog):
    def __init__(self, htb_task, send_task, thread_index=1, name=''):
        self.htb_task = htb_task
        self.send_task = send_task
        self.task_uuid = self.htb_task.task_uuid
        self.disk_token = self.send_task.disk_token
        self.snap_shots = self.get_snapshots()
        self.lock_snapshots_name = get_lock_name(self.htb_task.id)
        self.ex_vols = self.get_ex_vols()
        self.thread_index = thread_index
        self.name = name
        self.bit_map_path = ''

    def send(self, need_unlock_restore=False):
        try:
            self.lock_snapshots()
            self.get_bit_map()
            if need_unlock_restore:
                self.unlock_restore_snapshots()
            _args = self._get_args()
            self._send(*_args)
        except Exception as e:
            self.unlock_snapshots()
            raise e

    def _send(self, *args):
        if self.send_task.task_type == HTBSendTask.QEMU_WORK:
            code = boxService.box_service.StartQemuWorkForBitmapv2(*args)
        else:
            code = boxService.box_service.StartCDPWork(*args)
        if code != 0:
            raise Exception('receive code:{}'.format(code))

    def get_bit_map(self):
        if self.send_task.task_type == HTBSendTask.QEMU_WORK:
            flag = r'PiD{:x} BoxDashboard|htb get_used_bit_map{}'.format(os.getpid(), self.task_uuid)
            bit_map = SnapshotsUsedBitMapGeneric(self.snap_shots, flag).get()
            self.bit_map_path = os.path.join(xdata.HTB_DISK_FILES_DIR.format(self.task_uuid),
                                             '{}.bitmap'.format(uuid.uuid4().hex))
            with open(self.bit_map_path, 'wb') as wf:
                wf.truncate(convert_disk_size2bitmap_size(self._get_disk_bytes()))
                wf.write(bit_map)
        else:
            return None

    def _get_disk_bytes(self):
        disk_snapshot = get_disk_snapshot_from_info(self.snap_shots[-1].path, self.snap_shots[-1].snapshot)
        return disk_snapshot.bytes

    @property
    def is_working(self):
        code, work_type = boxService.box_service.QueryWorkStatus(self.task_uuid, self.disk_token)
        return work_type != DataQueuingIce.WorkType.noneWork

    def work_type(self):
        code, work_type = boxService.box_service.QueryWorkStatus(self.task_uuid, self.disk_token)
        return work_type

    def stop(self):
        try:
            if not self.is_working:
                if not self.send_task.o_completed_trans:
                    _logger.warning('update send_task {} o_completed_trans=True'.format(self.send_task))
                    self.send_task.o_completed_trans = True
                code = 0
            else:
                if self.send_task.task_type == HTBSendTask.QEMU_WORK:
                    code = boxService.box_service.StopQemuWorkv2(self.task_uuid, self.disk_token)
                    self.send_task.o_bit_map = self.bit_map_path
                else:
                    code, self.send_task.o_stop_time = boxService.box_service.StopCDPWork(self.task_uuid,
                                                                                          self.disk_token)
            self.send_task.save(update_fields=['o_bit_map', 'o_stop_time', 'o_completed_trans'])
            return code
        except Exception as e:
            raise e
        finally:
            self.unlock_snapshots()

    def waite(self, task_content):
        no_work_times = list()
        while True:
            code = self._task_done()
            if code == 0:
                self.log_info('send_file_and_waite_end work done!,{},{}'.format(self.snap_shots, self.disk_token))
                self.send_task.o_completed_trans = True
                if self.bit_map_path:
                    remove(self.bit_map_path, self.log_warning)
                break
            if code > 0:
                raise Exception('check task_done error code:{}'.format(code))
            if code == -1:
                self._check_is_exception(no_work_times)
                self.get_work_progress()
            if check_should_switch(self.htb_task.id, self.thread_index):
                break
            if HTBFlowEntrance.has_error(task_content):
                raise Exception('send_file_and_waite_end other threading wrong exit!')
            if user_stop(self.htb_task.id):
                raise Exception('user stop!')
            time.sleep(5)
        self.send_task.save(update_fields=['o_completed_trans'])

    def _check_is_exception(self, rs_list):
        if sum(rs_list) > 5:
            raise Exception('not work after 5 ask!')
        code, work_type = boxService.box_service.QueryWorkStatus(self.task_uuid, self.disk_token)
        if work_type == DataQueuingIce.WorkType.noneWork:
            rs_list.append(1)

    def close_send_task(self):
        boxService.box_service.CloseTask(self.task_uuid)

    def get_work_progress(self):
        if self.send_task.task_type == HTBSendTask.QEMU_WORK:
            self.get_qemu_progress()
        else:
            self.get_cdp_progress()

    def get_cdp_progress(self):
        code, last_time, queue_len = boxService.box_service.QueryCDPProgress(self.task_uuid,
                                                                             self.disk_token)
        if '.' in last_time:
            datetime_obj = datetime.datetime.fromtimestamp(float(last_time))
            self.log_debug('get_work_progress {}->{}'.format(last_time, datetime_obj))
        return last_time

    def get_qemu_progress(self):
        code, total_bytes, completed_bytes, queue_len = boxService.box_service.QueryQemuProgress(self.task_uuid,
                                                                                                 self.disk_token)
        return '{}/{}({})'.format(
            functions.format_size(completed_bytes),
            functions.format_size(total_bytes),
            functions.format_progress(completed_bytes, total_bytes)
        )

    def _task_done(self):
        if self.send_task.task_type == HTBSendTask.NOT_CLOSED_CDP_WORK:
            return 0

        key = "{}_{}".format(self.task_uuid, self.disk_token)
        code = get_task_code(key)
        return code

    def _get_args(self):
        if self.send_task.task_type == HTBSendTask.QEMU_WORK:
            return [self.task_uuid, self.disk_token, self.snap_shots, self.bit_map_path, self.ex_vols]
        elif self.send_task.task_type == HTBSendTask.CLOSED_CDP_WORK:
            start_time = 'all'
            is_watch = False
            file_name = json.loads(self.send_task.snapshots)[-1]['path']
            return [self.task_uuid, self.disk_token, file_name, start_time, is_watch, self.ex_vols]
        elif self.send_task.task_type == HTBSendTask.NOT_CLOSED_CDP_WORK:
            start_time = 'all'
            is_watch = True
            file_name = json.loads(self.send_task.snapshots)[-1]['path']
            return [self.task_uuid, self.disk_token, file_name, start_time, is_watch, self.ex_vols]
        else:
            return []

    def get_ex_vols(self):
        ex_vols = json.loads(self.send_task.ex_vols)
        return [pyconv.convertJSON2OBJ(DataQueuingIce.ExcludeRun, info) for info in ex_vols]

    def get_snapshots(self):
        return [pyconv.convertJSON2OBJ(IMG.ImageSnapshotIdent, snap) for snap in
                json.loads(self.send_task.snapshots)]

    @xlogging.convert_exception_to_value(None)
    def lock_snapshots(self):
        self.lock_snapshots_u(self.snap_shots, self.lock_snapshots_name)

    @staticmethod
    def lock_snapshots_u(snap_shots, task_name):
        try:
            for snapshot in snap_shots:
                if not DiskSnapshot.is_cdp_file(snapshot.path):
                    CompressTaskThreading().update_task_by_disk_snapshot(snapshot.path, snapshot.snapshot)
            DiskSnapshotLocker.lock_files(snap_shots, task_name)
        except Exception as e:
            _logger.error('lock_snapshots_u error:{},{}'.format(snap_shots, task_name))

    @staticmethod
    def unlock_snapshots_u(snap_shots, task_name):
        DiskSnapshotLocker.unlock_files(snap_shots, task_name)

    @xlogging.convert_exception_to_value(None)
    def unlock_snapshots(self):
        self.unlock_snapshots_u(self.snap_shots, self.lock_snapshots_name)

    def unlock_restore_snapshots(self):
        unlock_restore_files(self.htb_task)


class HTBTaskQuery(object):
    def __init__(self, htb_schedule_id):
        self.htb_schedule = self._get_htb_schedule(htb_schedule_id)

    @staticmethod
    def _get_htb_schedule(htb_schedule_id):
        return HTBSchedule.objects.get(id=htb_schedule_id)

    def query(self, htb_task=None):
        if htb_task is None:
            return self._get_task_info(self.htb_schedule.htb_task.last())

        return self._get_task_info(htb_task)

    @staticmethod
    def sectors_to_mb_str(sec):
        return '{:.1f}'.format(sec * 512 / 1024 ** 2)

    # 获取还原阶段，传输关键热数据的进度
    @staticmethod
    def wrap_info(pe_ident, default_msg, prefix='', postfix=''):
        if default_msg not in ['发送系统关键热数据', '发送重建数据']:
            return default_msg
        try:
            flag, total, hs_send = boxService.box_service.get_restore_key_data_process(pe_ident)
            _logger.debug("{},{},{}".format(flag, total, hs_send))
        except Exception as e:
            _logger.error('wrap_info: get restore key data process error :{}, pe_ident:{}'.format(e, pe_ident))
            return default_msg

        if flag and hs_send:
            hs_send_bytes, total_bytes = hs_send * 512, total * 512
            return '{} {}/{}({})'.format(
                default_msg,
                functions.format_size(hs_send_bytes),
                functions.format_size(total_bytes),
                functions.format_progress(hs_send_bytes, total_bytes)
            )
        else:
            return default_msg

    @xlogging.convert_exception_to_value(None)
    def _get_task_info(self, task_obj):
        if not task_obj:
            return None
        rs = {
            'status': task_obj.get_status_display(),
            'code': task_obj.status,
            'start_datetime': task_obj.start_datetime,
            'finish_datetime': task_obj.finish_datetime,
            'name': self.htb_schedule.name,
            'sub_info': self._get_task_sub_info(task_obj)
        }

        postfix = ''
        restore_target = task_obj.restore_target
        if not restore_target:
            return rs
        try:
            restore_task = task_obj.restore_target.restore
        except:
            return rs
        if rs['code'] in [HTBTask.INITSYS, HTBTask.WAITETRANSEND]:
            total_bytes = restore_target.total_bytes
            restored_bytes = restore_target.restored_bytes
            if None in [total_bytes, restored_bytes]:
                percent = None
            else:
                percent = restored_bytes / total_bytes

            if percent is None:  # 1.KVM前 --> KVM中 --> 目标机启动前
                postfix = '{0}'.format(restore_target.display_status)
                if rs['code'] == HTBTask.INITSYS:
                    postfix = self.wrap_info(restore_target.ident, postfix)
                else:
                    postfix = ''
            else:  # 2.目标机启动后：开始传输还原数据
                if percent > 0.997 and not restore_task.finish_datetime:
                    progress_str = '99.7%'
                else:
                    progress_str = '{}/{}({})'.format(
                        functions.format_size(restored_bytes),
                        functions.format_size(total_bytes),
                        functions.format_progress(restored_bytes, total_bytes)
                    )
                postfix = progress_str

            # postfix = ': {}'.format(postfix).replace('目标客户端', '备机')  不知道为什么需要更换，更换后导致客户端日志和首页不一致
            postfix = ': {}'.format(postfix)

        if postfix:
            rs['status'] += postfix

        return rs

    @staticmethod
    @xlogging.convert_exception_to_value(list())
    def _get_task_sub_info(task_obj):
        rs = list()
        try:
            if task_obj.status not in [HTBTask.SYNC, HTBTask.VOL_SYNC]:
                return list()
            disk_token_groups = task_obj.send_task.values('disk_token').annotate(Count('disk_token'))
            for index, disk_info in enumerate(disk_token_groups):
                last_send_task = task_obj.send_task.filter(disk_token=disk_info['disk_token']).last()
                send_task_work = SendTaskWork(task_obj, last_send_task)
                snapshots = send_task_work.get_snapshots()
                disk_snapshot = get_disk_snapshot_from_info(snapshots[-1].path, snapshots[-1].snapshot)
                disk_name = get_disk_name(disk_snapshot)
                if last_send_task.task_type == HTBSendTask.QEMU_WORK:
                    try:
                        progress = send_task_work.get_qemu_progress()
                    except Exception as e:
                        _logger.error('get_qemu_progress error:{} '.format(e))
                        continue

                    st, ed = HTBWaiteSwitchCMD.get_disk_snapshot_timerange(disk_snapshot)
                    datetime_obj = datetime.datetime.fromtimestamp(float(ed))
                    last_time = datetime_obj.strftime(xdatetime.FORMAT_WITH_MICROSECOND)
                    rs.append([disk_name, last_time, progress])
                else:
                    try:
                        last_time = send_task_work.get_cdp_progress()
                    except Exception as e:
                        _logger.error('get_cdp_progress error:{} '.format(e))
                        continue
                    if last_time == 'end':
                        st, ed = HTBWaiteSwitchCMD.get_disk_snapshot_timerange(disk_snapshot)
                        datetime_obj = datetime.datetime.fromtimestamp(float(ed))
                        rs.append([disk_name, datetime_obj.strftime(xdatetime.FORMAT_WITH_MICROSECOND)])
                    else:
                        if '.' in last_time:
                            datetime_obj = datetime.datetime.fromtimestamp(float(last_time))
                            rs.append([disk_name, datetime_obj.strftime(xdatetime.FORMAT_WITH_MICROSECOND)])
        except Exception as e:
            _logger.error('_get_task_sub_info error:{}'.format(e), exc_info=True)

        for elem in rs:
            if len(elem) == 3:
                elem[-2], elem[-1] = '同步至:{}'.format(elem[-2]), '进度:{}'.format(elem[-1])
            if len(elem) == 2:
                elem[-1] = '已同步到:{}'.format(elem[-1])

        return rs


class HTBInit(task.Task, WorkerLog):
    default_provides = 'task_content'

    def __init__(self, name, task_id, inject=None):
        super(HTBInit, self).__init__('HTBInit_{}'.format(task_id), inject=inject)
        self.task = HTBTask.objects.get(id=task_id)
        self.exc_info = ''

    def execute(self, *args, **kwargs):
        task_content = {
            'error': ''
        }
        self.task.set_status(HTBTask.INIT)
        self.exc_info = get_exc_info(self.task)

        host_snap_shot = self.get_last_host_snapshot(self.exc_info)
        if not host_snap_shot:
            task_content['error'] = ('获取主机快照失败', 'get host snap shot fail!')
            return task_content
        os.makedirs(xdata.HTB_DISK_FILES_DIR.format(self.task.task_uuid), exist_ok=True)
        self.log_info('htb task dir {}'.format(xdata.HTB_DISK_FILES_DIR.format(self.task.task_uuid)))
        api_request = dict()
        api_request['pe_host_ident'] = self.task.restore_target.ident
        api_request['htb_task_uuid'] = self.task.task_uuid
        api_request['htb_schedule_id'] = self.task.schedule.id
        api_request['disk_params'] = json.loads(self.exc_info['disks'])
        if host_snap_shot.is_cdp:
            api_request['type'] = xdata.SNAPSHOT_TYPE_CDP
            if self.task.schedule.task_type == HTBSchedule.NEW_POINT_NEED_UPDATE:
                api_request['restore_time'] = self.get_last_restore_time(host_snap_shot)
            else:
                time_str = self.exc_info['restore_time'].replace('T', ' ')
                api_request['restore_time'] = datetime.datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S.%f')
        else:
            api_request['type'] = xdata.SNAPSHOT_TYPE_NORMAL
            api_request['restore_time'] = None

        task_content['api_request'] = api_request
        task_content['host_snap_shot_id'] = host_snap_shot.id
        task_content['master_adapter'], task_content['standby_adapter'] = self.get_ip_info(self.task.id, self.exc_info[
            'master_adpter'], self.exc_info['standby_adpter'])

        task_content['standby_adapter'] = self.inject_mtu_to_adapter(task_content['standby_adapter'])
        task_content['src_is_windows'] = src_is_windows(self.task)
        task_content['switch_ip_result'] = ''

        # 判断主机登陆，需要客户端支持JsonFuncV2，对旧的主机依旧用mac判断
        if DiskSnapshotHash.get_client_version_from_snapshot(host_snap_shot) <= datetime.datetime(2018, 1, 12):
            task_content['check_login_use_mac'] = True  # 使用mac地址查询登陆的主机
        else:
            task_content['check_login_use_mac'] = False
        return task_content

    @staticmethod
    def inject_mtu_to_adapter(adapters):
        mtu = int(GetDictionary(DataDictionary.DICT_TYPE_AIO_NETWORK, 'aio_mtu', -1))
        if mtu == -1:
            return adapters
        for adapter in adapters:
            adapter['mtu'] = mtu
        return adapters

    @staticmethod
    def get_ip_info(task_id, master_adapter, standby_adapter):
        if change_master_ip(task_id):
            return HTBInit._format_ip_info(master_adapter), HTBInit._format_ip_info(standby_adapter)
        else:
            return None, HTBInit._format_ip_info(standby_adapter)

    @staticmethod
    def _format_ip_info(standby_adapter):
        gate_way = standby_adapter['gateway'][0] if standby_adapter['gateway'] else ''
        dns_list = standby_adapter['dns']
        business_ip_list = [{'ips': ip_info['ips'], 'mac': ip_info['mac'], 'ip_type': xdata.HTB_IP_TYPE_BUSINESS} for
                            ip_info in standby_adapter['business']]
        control_ip_list = [{'ips': ip_info['ips'], 'mac': ip_info['mac'], 'ip_type': xdata.HTB_IP_TYPE_CONTROL} for
                           ip_info in standby_adapter['control']]
        business_ip_list.extend(control_ip_list)
        rs_list = list()
        s_list = sorted(business_ip_list, key=lambda x: x['mac'])
        for mac, groups in groupby(s_list, key=lambda x: x['mac']):
            item = dict()
            item['mac'] = mac
            item['gate_way'] = gate_way
            item['dns_list'] = dns_list
            item['ip_mask_list'] = [{'ip': ip_mask['ip'], 'mask': ip_mask['mask'], 'ip_type': group['ip_type']} for
                                    group in groups for ip_mask in
                                    group['ips']]
            item['ip_mask_list'] = [item for item in item['ip_mask_list'] if HTBInit.is_valid_ip(item['ip'])]
            rs_list.append(item)
        return rs_list

    @staticmethod
    def is_valid_ip(ip):
        return (ip) and (ip != '0.0.0.0') and (ip != '127.0.0.1')

    @staticmethod
    def get_last_host_snapshot(exc_info):
        point = exc_info['pointid']
        _id = point.split('|')[1]
        _logger.debug('get_last_host_snapshot HostSnapshot id:{}'.format(_id))
        return HostSnapshot.objects.get(id=_id)

    @staticmethod
    def get_last_restore_time(host_snap_shot):
        end = host_snap_shot.cdp_info.last_datetime
        if CDPHostSnapshotSpaceCollectionMergeTask.get_running_task_using(host_snap_shot):
            end = timezone.now()
        return end


class HTBInitForVol(task.Task, WorkerLog):
    default_provides = 'task_content'

    def __init__(self, name, task_id, inject=None):
        super(HTBInitForVol, self).__init__('HTBInitForVol_{}'.format(task_id), inject=inject)
        self.task = HTBTask.objects.get(id=task_id)
        self.exc_info = ''

    def execute(self, *args, **kwargs):
        task_content = {
            'error': ''
        }
        self.task.set_status(HTBTask.INIT)
        self.exc_info = get_exc_info(self.task)

        host_snap_shot = HTBInit.get_last_host_snapshot(self.exc_info)
        if not host_snap_shot:
            task_content['error'] = ('获取主机快照失败', 'get host snap shot fail!')
            return task_content

        os.makedirs(xdata.HTB_DISK_FILES_DIR.format(self.task.task_uuid))
        self.log_info('htb task dir {}'.format(xdata.HTB_DISK_FILES_DIR.format(self.task.task_uuid)))
        api_request = dict()
        api_request['volumes'] = self.get_vol_params()
        api_request['host_ident'] = self.task.schedule.dst_host_ident
        api_request['htb_task_uuid'] = self.task.task_uuid
        if host_snap_shot.is_cdp:
            api_request['type'] = xdata.SNAPSHOT_TYPE_CDP
            if self.task.schedule.task_type == HTBSchedule.NEW_POINT_NEED_UPDATE:
                api_request['restore_time'] = HTBInit.get_last_restore_time(host_snap_shot)
            else:
                time_str = self.exc_info['restore_time'].replace('T', ' ')
                api_request['restore_time'] = datetime.datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S.%f')
        else:
            api_request['type'] = xdata.SNAPSHOT_TYPE_NORMAL
            api_request['restore_time'] = None

        task_content['api_request'] = api_request
        task_content['host_snap_shot_id'] = host_snap_shot.id
        task_content['master_adapter'], task_content['standby_adapter'] = HTBInit.get_ip_info(
            self.task.id, self.exc_info['master_adpter'], self.exc_info['standby_adpter'])

        task_content['standby_adapter'] = HTBInit.inject_mtu_to_adapter(task_content['standby_adapter'])
        task_content['src_is_windows'] = src_is_windows(self.task)
        task_content['target_host_ident'] = self.task.schedule.dst_host_ident
        task_content['switch_ip_result'] = ''

        return task_content

    def get_vol_params(self):
        index_list = json.loads(self.exc_info['index_list'])
        vols = json.loads(self.exc_info['vol_maps'])
        volumes = list()
        for src_index, dst_index in enumerate(index_list):
            if dst_index is not None:
                volumes.append(DiskVolMap.get_vol_restore_params(vols[src_index], int(dst_index)))

        return volumes


class HTBStartRestoreForVol(task.Task):
    default_provides = 'task_content'

    def __init__(self, name, task_id, inject=None):
        super(HTBStartRestoreForVol, self).__init__('HTBStartRestoreForVol_{}'.format(task_id), inject=inject)
        self.task = HTBTask.objects.get(id=task_id)
        self.monitor = None

    def execute(self, task_content, **kwargs):
        if HTBFlowEntrance.has_error(task_content):
            return task_content

        self.task.set_status(HTBTask.VOL_INITSYS)

        host_snapshot_id = task_content['host_snap_shot_id']
        api_request = task_content['api_request']

        self.monitor = MonitorRestore(self.task.id)
        self.monitor.setDaemon(True)
        self.monitor.start()

        api_response = HostSnapshotRestore().post(None, host_snapshot_id, api_request, True)

        self.monitor.quit()

        if status.is_success(api_response.status_code) and restore_success(self.task):
            self.task.schedule.set_stand_by(self.task.task_uuid)
        else:
            self.task.schedule.cancel_stand_by()
            task_content['error'] = ('还原任务失败', 'HTBStartRestoreForVol fail:{}'.format(api_response.data))
            return task_content

        task_content['since_reboot'] = timezone.now().strftime('%Y-%m-%d %H:%M:%S.%f')

        return task_content


class HTBPreTransDataForVol(task.Task, WorkerLog):
    default_provides = 'task_content'

    def __init__(self, name, task_id, inject=None):
        super(HTBPreTransDataForVol, self).__init__('HTBPreTransDataForVol_{}'.format(task_id), inject=inject)
        self.task = HTBTask.objects.get(id=task_id)

    def execute(self, task_content, **kwargs):
        if HTBFlowEntrance.has_error(task_content):
            return task_content

        self.task.set_status(HTBTask.VOL_WAITEINIT)

        try:
            self.waite_vol_init()
        except Exception as e:
            task_content['error'] = ('等待卷完成初始化', 'HTBPreTransDataForVol error:{}'.format(e))
            return task_content

        return task_content

    def waite_vol_init(self):
        restore_target = get_restore_target(self.task)
        while True:
            self.log_info('waite_vol_init, restore_target:{}'.format(restore_target.display_status))
            if restore_target.finish_datetime:
                raise Exception('RestoreTarget is finish!')
            if user_stop(self.task.id):
                raise Exception('user stop!')
            if restore_target.display_status == dict(xdata.VOLUME_RESTORE_STATUS_CHOICES)[
                xdata.VOLUME_RESTORE_STATUS_STARTED]:
                break
            restore_target = RestoreTarget.objects.get(ident=restore_target.ident)
            time.sleep(5)


class MonitorRestore(threading.Thread):
    def __init__(self, task_id):
        super(MonitorRestore, self).__init__(name='htb_MonitorRestore_{}'.format(task_id))
        self.task = HTBTask.objects.get(id=task_id)
        self._quit = False

    def run(self):
        while not self._quit:
            if user_stop(self.task.id):
                self._cancel_restore_task()
                break
            time.sleep(5)

    def quit(self):
        self._quit = True

    @xlogging.convert_exception_to_value(None)
    def _cancel_restore_task(self):
        restore_target = get_restore_target(self.task)
        if restore_target and restore_target.restore:
            task_obj = restore_target.restore
            # 1.设置task_obj的cancel字段
            ext_info = json.loads(task_obj.ext_config)
            ext_info[xdata.CANCEL_TASK_EXT_KEY] = 'any_value'
            task_obj.ext_config = json.dumps(ext_info)
            task_obj.save(update_fields=['ext_config'])

            # 2.若KVM已启动，则删除启动标志文件
            flag_file = ext_info.get(xdata.START_KVM_FLAG_FILE, None)
            if (flag_file is not None) and boxService.box_service.isFileExist(flag_file):
                boxService.box_service.remove(flag_file)


class HTBStartRestore(task.Task):
    default_provides = 'task_content'

    def __init__(self, name, task_id, inject=None):
        super(HTBStartRestore, self).__init__('HTBStartRestore_{}'.format(task_id), inject=inject)
        self.task = HTBTask.objects.get(id=task_id)
        self.monitor = None

    def execute(self, task_content, **kwargs):
        if HTBFlowEntrance.has_error(task_content):
            return task_content

        if standby_restart_mod(self.task):
            task_content['since_reboot'] = timezone.now().strftime('%Y-%m-%d %H:%M:%S.%f')
            restore_target = get_restore_target(self.task)
            restore_target.total_bytes = None
            restore_target.restored_bytes = None
            restore_target.finish_datetime = None
            restore_target.successful = False
            restore_target.save(update_fields=['finish_datetime', 'successful', 'total_bytes', 'restored_bytes'])
            return task_content

        self.task.set_status(HTBTask.INITSYS)

        self.monitor = MonitorRestore(self.task.id)
        self.monitor.setDaemon(True)
        self.monitor.start()

        rsp = HostSnapshotLocalRestore().post(None, task_content['host_snap_shot_id'], task_content['api_request'],
                                              True)

        self.monitor.quit()

        if status.is_success(rsp.status_code) and restore_success(self.task):
            self.task.schedule.set_stand_by(self.task.task_uuid)
        else:
            self.task.schedule.cancel_stand_by()
            task_content['error'] = ('还原任务失败', 'HTBStartRestore fail:{}'.format(rsp.data))
            return task_content

        task_content['since_reboot'] = timezone.now().strftime('%Y-%m-%d %H:%M:%S.%f')

        return task_content


class HTBPrepareSwitchIp(task.Task):
    default_provides = 'task_content'

    def __init__(self, name, task_id, inject=None):
        super(HTBPrepareSwitchIp, self).__init__('HTBPrepareSwitchIp_{}'.format(task_id), inject=inject)
        self.task = HTBTask.objects.get(id=task_id)

    def execute(self, task_content, **kwargs):
        if HTBFlowEntrance.has_error(task_content):
            return task_content

        if self.task.schedule.host.is_remote:
            _logger.info('HTBPrepareSwitchIp host is_remote return')
            return task_content

        if not change_master_ip(self.task.id):
            _logger.info('HTBPrepareSwitchIp not switch master ip')
            return task_content

        self.task.set_status(HTBTask.PREPARE_SWITCH_IP)
        update_ip_config(task_content, self.task.id)
        try:
            # 重启后只有固有IP,针对源主机是windows
            if task_content['src_is_windows']:
                ht_json = {'ReservIpInfo_OnlyControl': task_content['master_adapter']}
                boxService.box_service.writeFile2Host(
                    self.task.schedule.host.ident, 'current', 'ht.json', 0, bytearray(json.dumps(ht_json), 'utf8'))

            task_id = TASK_ID_PUSH_FORMAT.format(self.task.id)
            # 将页面配置的IP 配置下去
            cmd_info = {
                'task_type': xdata.HTB_SWITCH_IP_STEP_PUSH,
                'Host_id': self.task.schedule.host.ident, 'Host': task_content['master_adapter'],
                'Backup_id': None, 'Backup': task_content['standby_adapter'],
                'src_is_windows': task_content['src_is_windows'],
                'task_id': task_id
            }
            ClientIpMg.client_ip_mg_threading.InsertOrUpdate(task_id, cmd_info)
        except Exception as e:
            task_content['error'] = ('初始化动态IP失败', 'HTBPrepareSwitchIp error:{}'.format(e))
            return task_content

        return task_content


class HTBStartTransferData(task.Task, WorkerLog):
    default_provides = 'task_content'

    def __init__(self, name, task_id, inject=None):
        super(HTBStartTransferData, self).__init__('HTBStartTransferData_{}'.format(task_id), inject=inject)
        self.task = HTBTask.objects.get(id=task_id)
        self.task_content = None
        self.restore_info = None
        self.is_sys_restore = check_is_sys_restore(self.task)

    def execute(self, task_content, **kwargs):
        if HTBFlowEntrance.has_error(task_content):
            _logger.debug('HTBStartTransferData find error, exit!')
            return task_content

        if self.is_sys_restore:
            self.task.set_status(HTBTask.SYNC)
        else:
            self.task.set_status(HTBTask.VOL_SYNC)

        self.task_content = task_content
        self._reinsert_push_ip_task()
        restore_target = get_restore_target(self.task)
        self.restore_info = json.loads(restore_target.info)
        restore_files = self.restore_info.get('restore_files', None)
        restore_files = [i for i in restore_files if not self._is_clw_boot_disk(i)]  # clw 启动盘不推送
        if not restore_files:
            self.task_content['error'] = ('未发现还原数据', 'no restore_files find')
            return task_content

        send_worker_list = list()
        for index, restore_file in enumerate(restore_files):
            name = '{}-{}'.format(self.name, index)
            send_worker = threading.Thread(target=self.send_file,
                                           args=(restore_file, index,), name=name)
            send_worker_list.append(send_worker)

        for work in send_worker_list:
            work.start()
        for work in send_worker_list:
            work.join()

        # 删除 切ip(推送阶段)任务
        ClientIpMg.client_ip_mg_threading.Remove(TASK_ID_PUSH_FORMAT.format(self.task.id))

        return self.task_content

    @staticmethod
    def _is_clw_boot_disk(restore_file):
        return (len(restore_file['snapshot'])) == 1 and (
                restore_file['snapshot'][0]['snapshot'] in (xdata.CLW_BOOT_REDIRECT_MBR_UUID,
                                                            xdata.CLW_BOOT_REDIRECT_GPT_UUID,
                                                            xdata.CLW_BOOT_REDIRECT_GPT_LINUX_UUID,
                                                            ))

    def _reinsert_push_ip_task(self):
        if self.task.schedule.host.is_remote:
            return None

        if not change_master_ip(self.task.id):
            return None

        task_id = TASK_ID_PUSH_FORMAT.format(self.task.id)
        # 将页面配置的IP 配置下去
        cmd_info = {
            'task_type': xdata.HTB_SWITCH_IP_STEP_PUSH,
            'Host_id': self.task.schedule.host.ident, 'Host': self.task_content['master_adapter'],
            'Backup_id': None, 'Backup': self.task_content['standby_adapter'],
            'src_is_windows': self.task_content['src_is_windows'],
            'task_id': task_id
        }
        ClientIpMg.client_ip_mg_threading.InsertOrUpdate(task_id, cmd_info)

    def send_file(self, restore_file, thread_index):
        try:
            self.log_info('start logic send_file restore_file:{} thread_index:{}'.format(restore_file, thread_index))
            work_task = self.get_or_create_send_task(restore_file)
            need_send = True
            need_unlock_restore = True
            send_task_work = SendTaskWork(self.task, work_task, thread_index, self.name)
            while True:
                if need_send:
                    send_task_work.send(need_unlock_restore)
                    send_task_work.waite(self.task_content)
                    need_send = False
                    need_unlock_restore = False
                send_task_work.get_work_progress()
                if check_should_switch(self.task.id, thread_index):
                    code = send_task_work.stop()
                    if int(code) == 0:
                        break
                    else:
                        raise Exception('stop failed')
                if user_stop(self.task.id):
                    raise Exception('user stop!')
                work_task, need_send = self.get_new_task(work_task, thread_index)
                if need_send:
                    send_task_work.unlock_snapshots()
                    send_task_work = SendTaskWork(self.task, work_task, thread_index, self.name)
                else:
                    time.sleep(5)
                    self.update_restore_token(send_task_work.disk_token)
        except Exception as e:
            self.log_error('send_file error:{}'.format(e))
            self.task_content['error'] = ('发送数据错误', 'send_file error:{},traceback={}'.format(e, traceback.format_exc()))
        self.log_info('end logic send_file restore_file:{} thread_index:{}'.format(restore_file, thread_index))

    def update_restore_token(self, disk_token):
        try:
            now = timezone.now()
            restore_target = get_restore_target(self.task)
            ex_time = restore_target.token_expires
            if abs((now - ex_time).total_seconds()) <= 3600:
                self.log_debug('update_restore_token disk token:{}'.format(disk_token))
                TokenInfo().get(None, disk_token)
        except Exception as e:
            self.log_error('update_restore_token error:{}'.format(e))

    def get_or_create_send_task(self, restore_file):
        old_task = self.get_old_task(restore_file)
        if old_task:
            return old_task
        send_task = self._create_first_task(restore_file)
        return send_task

    def get_old_task(self, restore_file):
        if standby_restart_mod(self.task):
            return None
        snapshots = [{'path': item['path'], 'snapshot': item['snapshot']} for item in restore_file['snapshot']]
        disk_snap_shot = get_disk_snapshot_from_info(snapshots[-1]['path'], snapshots[-1]['snapshot'])
        host_snap_shot = self.get_host_snapshot_by_disk_snapshot(disk_snap_shot)
        native_guid = self.get_disk_native_guid(host_snap_shot, disk_snap_shot.disk.ident)
        last_task = self.task.send_task.filter(native_guid=native_guid).last()
        if last_task:
            self.stop_work(last_task)
            last_task.o_stop_time = ''
            last_task.o_bit_map = ''
            last_task.o_completed_trans = False
            last_task.save(update_fields=['o_stop_time', 'o_bit_map', 'o_completed_trans'])
        return last_task

    def _get_snapshots_from_restore_info(self, restore_file):
        last_snap_info = restore_file['snapshot'][-1]
        disk_snapshot = get_disk_snapshot_from_info(last_snap_info['path'], last_snap_info['snapshot'])
        validator_list = [GetSnapshotList.is_disk_snapshot_object_exist,
                          GetSnapshotList.is_disk_snapshot_file_exist]
        disk_snapshots = GetSnapshotList.query_snapshots_by_snapshot_object(
            disk_snapshot, validator_list, include_all_node=True)
        snapshots = list()
        snapshots.extend([{'path': item.path, 'snapshot': item.snapshot} for item in disk_snapshots[:-1]])
        snapshots.append({'path': last_snap_info['path'], 'snapshot': last_snap_info['snapshot']})
        return snapshots

    def _create_first_task(self, restore_file):
        self.log_info('start send restore_file:{}'.format(restore_file))
        snapshots = self._get_snapshots_from_restore_info(restore_file)

        last_snap = snapshots[-1]
        # 丢弃最后一个带时间的CDP
        if last_snap['path'].endswith('cdp') and last_snap['snapshot'] != 'all':
            if self.task.schedule.task_type == HTBSchedule.NEW_POINT_NEED_UPDATE:
                snapshots.pop()
        if len(snapshots) == 0:
            raise Exception('send_file fail error, snapshots len is 0')
        disk_snap_shot = get_disk_snapshot_from_info(snapshots[-1]['path'], snapshots[-1]['snapshot'])
        host_snap_shot = self.get_host_snapshot_by_disk_snapshot(disk_snap_shot)
        native_guid = self.get_disk_native_guid(host_snap_shot, disk_snap_shot.disk.ident)
        disk_token = self.get_disk_token(restore_file['diskIndex'])
        if self.is_sys_restore:
            ex_vols = self.get_ex_vols_for_sys(disk_snap_shot.disk.ident)
            disk_size = self._get_target_disk_size(restore_file['diskIndex'])
            if disk_snap_shot.bytes > disk_size:
                self.log_info('find big disk:{} to small disk:{}'.format(disk_snap_shot.bytes, disk_size))
                ex_vols.append({'byteOffset': disk_size,
                                'bytes': disk_snap_shot.bytes - disk_size})
            else:
                pass
        else:
            ex_vols = self.get_ex_vols_for_vol(restore_file['diskIndex'], restore_file['diskBytes'])
        send_task = HTBSendTask.objects.create(
            htb_task=self.task,
            disk_token=disk_token,
            native_guid=native_guid,
            task_type=HTBSendTask.QEMU_WORK,
            snapshots=json.dumps(snapshots),
            ex_vols=json.dumps(ex_vols)
        )
        return send_task

    def _get_target_disk_size(self, dst_disk_index):
        for disk in self.restore_info['disks']:
            if int(disk['diskID']) == int(dst_disk_index):
                return int(disk['diskSecCount']) * 512
        self.log_error_and_raise_exception('get disk size fail, disks:{} index:{}'.format(self.restore_info['disks'],
                                                                                          dst_disk_index))

    def get_ex_vols_for_sys(self, disk_ident):
        schedule = HTBTask.objects.get(id=self.task.id).schedule
        exc_info = json.loads(schedule.ext_config)
        ex_vols = json.loads(exc_info['ex_vols'])
        # boot_vols = json.loads(exc_info['boot_vols'])  # linux 热备情况 不再需要排除/boot 分区
        # ex_vols.extend(boot_vols)
        rs = list()
        for ex_vol in ex_vols:
            disk_info = ex_vol['ranges']
            if disk_info['disk_ident'] == disk_ident:
                item = dict()
                item['byteOffset'] = int(disk_info['sector_offset']) * 512
                item['bytes'] = int(disk_info['sectors']) * 512
                rs.append(item)
        rs.sort(key=lambda x: x['byteOffset'])
        if rs:
            if rs[0]['byteOffset'] > 512:
                rs.insert(0, {'byteOffset': 0, 'bytes': 512})
            else:
                end = rs[0]['bytes'] + rs[0]['byteOffset']
                rs[0]['bytes'] = end if end > 512 else 512
                rs[0]['byteOffset'] = 0
        else:
            rs.append({'byteOffset': 0, 'bytes': 512})
        return rs

    def get_ex_vols_for_vol(self, disk_index, disk_size):
        use_disk_ranges = list()
        volumes = self.restore_info['restore_cmd']['volumes']
        for volume in volumes:
            for disk in volume['disks']:
                if int(disk['disk_number']) != disk_index:
                    continue
                use_disk_ranges.extend([[int(pre_range['sector_offset']) * 512,
                                         int(pre_range['sector_offset']) * 512 + int(pre_range['sectors']) * 512] for
                                        pre_range in disk['ranges']])
        if not use_disk_ranges:
            self.log_error_and_raise_exception(
                'get_ex_vols_for_vol fail, not find select vols。vols:{},disk_index:{}'.format(volumes, disk_index))

        use_disk_ranges.sort(key=lambda x: x[0])

        offset_list = list()
        offset_list.append(0)
        for use_disk_range in use_disk_ranges:
            offset_list.extend(use_disk_range)
        offset_list.append(disk_size)

        ex_vols = list()
        # offset_list = [0, 1048576, 11490295808, 11490295808, 42947575808, 42949672960]
        # ex_vols = [[0, 1048576], [42947575808, 42949672960]]
        for index, offset in enumerate(offset_list):
            if index % 2 == 0:
                ex_vols.append([offset])
            elif ex_vols[-1][-1] == offset:
                ex_vols.pop()
            else:
                ex_vols[-1].append(offset)

        # 加入零扇区的排除
        if ex_vols[0][0] > 512:
            ex_vols.insert(0, [0, 512])
        else:
            ex_vols[0][1] = ex_vols[0][1] if ex_vols[0][1] > 512 else 512
            ex_vols[0][0] = 0

        return [{'byteOffset': ex_vol[0], 'bytes': ex_vol[1] - ex_vol[0]} for ex_vol in ex_vols]

    @staticmethod
    def get_disk_native_guid(host_snap_shot, disk_ident):
        info = json.loads(host_snap_shot.ext_info)
        for disk_info in info['include_ranges']:
            if disk_info['diskIdent'] == disk_ident:
                return disk_info['diskNativeGUID']
        raise Exception('get disk native guid fail')

    def get_disk_token(self, index):
        if self.is_sys_restore:
            return self.get_disk_token_for_sys(index)
        else:
            return self.get_disk_token_for_vol(index)

    def get_disk_token_for_sys(self, index):
        tokens = self.restore_info['pe_restore_info']['tokens']
        for item in tokens:
            if int(item['diskID']) == int(index):
                return item['token']
        self.log_error_and_raise_exception('get_disk_token_for_agent error,tokens:{},index:{}'.format(tokens, index))

    def get_disk_token_for_vol(self, index):
        tokens = self.restore_info['restore_cmd']['disks']
        for item in tokens:
            if int(item['disk_number']) == int(index):
                return item['disk_token']
        self.log_error_and_raise_exception('get_disk_token_for_agent error,tokens:{},index:{}'.format(tokens, index))

    def get_new_task(self, send_task, thread_index):
        if self.task.schedule.task_type == HTBSchedule.OLD_POINT_NOT_NEED_UPDATE:
            return send_task, False

        last_disk_snap_info = json.loads(send_task.snapshots)[-1]
        disk_snap_shot = get_disk_snapshot_from_info(last_disk_snap_info['path'], last_disk_snap_info['snapshot'])
        next_disk_snapshot = self.next_valid_disk_snapshot(disk_snap_shot)
        if not next_disk_snapshot:
            next_disk_snapshot = self.next_new_disk_snapshot(send_task.native_guid, disk_snap_shot)
        if not next_disk_snapshot:
            return send_task, False
        else:
            if send_task.task_type == HTBSendTask.NOT_CLOSED_CDP_WORK:
                while True:
                    if self.cdp_task_end(send_task):
                        code = self.stop_cdp(send_task)
                        if int(code) == 0:
                            break
                        else:
                            raise Exception('stop cdp failed')
                    if check_should_switch(self.task.id, thread_index):
                        return send_task, False
                    if user_stop(self.task.id):
                        raise Exception('user stop!')
                    time.sleep(5)
            new_send_task = self.create_new_send_task(send_task, next_disk_snapshot)
            return new_send_task, True

    def create_new_send_task(self, father_task, next_disk_snapshot):
        if next_disk_snapshot.is_cdp:
            if self.check_is_unclosed_cdp(next_disk_snapshot):
                task_type = HTBSendTask.NOT_CLOSED_CDP_WORK
                snap = [{'path': next_disk_snapshot.image_path, 'snapshot': 'all'}]
            else:
                task_type = HTBSendTask.CLOSED_CDP_WORK
                snap = [{'path': next_disk_snapshot.image_path, 'snapshot': 'all'}]
        else:
            task_type = HTBSendTask.QEMU_WORK
            snap = [{'path': next_disk_snapshot.image_path, 'snapshot': next_disk_snapshot.ident}]

        return HTBSendTask.objects.create(
            htb_task=self.task,
            disk_token=father_task.disk_token,
            native_guid=father_task.native_guid,
            task_type=task_type,
            snapshots=json.dumps(snap),
            ex_vols=father_task.ex_vols
        )

    @staticmethod
    def check_is_unclosed_cdp(disk_snapshot):
        """
        如果磁盘是没有封闭的，那么2秒后在看下是否是封闭的
        :param disk_snapshot: 
        :return: True->非封闭CDP，False->封闭CDP
        """
        disk_snapshot = DiskSnapshot.objects.get(id=disk_snapshot.id)
        flag = disk_snapshot.using_token.exists()
        if flag:
            time.sleep(2)
            disk_snapshot = DiskSnapshot.objects.get(id=disk_snapshot.id)
            return disk_snapshot.using_token.exists()
        else:
            # 远程灾备的cdp点认为没有关闭
            if hasattr(disk_snapshot, 'remote_backup_sub_task'):
                flag = True
        return flag

    # 检测是否有新的快照产生，这个快照不是一个依赖链上的!
    def next_new_disk_snapshot(self, native_guid, disk_snap_shot):
        current_host_snapshot = self.get_host_snapshot_by_disk_snapshot(disk_snap_shot)
        new_snapshot = HostSnapshot.objects.filter(successful=True,
                                                   deleted=False,
                                                   deleting=False,
                                                   host_id=current_host_snapshot.host.id,
                                                   start_datetime__gt=current_host_snapshot.start_datetime).order_by(
            'start_datetime').first()
        if new_snapshot:
            self.log_debug('next_new_disk_snapshot new_snapshot:{}'.format(new_snapshot.id))
            disk_snap_shot = GetDiskSnapshot.get_by_native_guid_and_host_snapshot(native_guid, new_snapshot)
            if not disk_snap_shot:
                raise Exception(
                    'find host_snapshot:{}, but not find disk_snapshot native_guid:{}'.format(new_snapshot.id,
                                                                                              native_guid))
            self.log_debug('next_new_disk_snapshot disk_snap_shot:{}'.format(disk_snap_shot))
            return disk_snap_shot
        return None

    def cdp_task_end(self, send_task):
        try:
            code, lsat_time, queue_len = boxService.box_service.QueryCDPProgress(self.task.task_uuid,
                                                                                 send_task.disk_token)
        except Exception as e:
            self.log_error('cdp_task_end error :{}'.format(e))
            return False
        if lsat_time == 'end':
            return True
        return False

    def stop_cdp(self, send_task):
        code, lasttime = boxService.box_service.StopCDPWork(self.task.task_uuid, send_task.disk_token)
        self.log_debug('stop_cdp code:{}, lasttime:{}'.format(code, lasttime))
        return code

    @staticmethod
    def next_valid_disk_snapshot(f_disk_snap_shot):
        # 子快照不能是被回收了的，主机快照不一定处于回收中
        c_disk_snap_shots = f_disk_snap_shot.child_snapshots.filter(merged=False)
        f_host_snapshot = HTBStartTransferData.get_host_snapshot_by_disk_snapshot(f_disk_snap_shot)
        valid_disk_snapshot_list = list()
        Host2DiskTuple = namedtuple('Host2DiskTuple', ['host_snapshot', 'disk_snapshot'])
        for c_disk_snap_shot in c_disk_snap_shots:
            c_host_snapshot = HTBStartTransferData.get_host_snapshot_by_disk_snapshot(c_disk_snap_shot)
            if f_host_snapshot.host.id != c_host_snapshot.host.id:
                _logger.debug('next_valid_disk_snapshot not same host!')
                continue
            else:
                if c_host_snapshot.successful and not (c_host_snapshot.deleted or c_host_snapshot.deleting):
                    valid_disk_snapshot_list.append(Host2DiskTuple(c_host_snapshot, c_disk_snap_shot))

        # 使用较新的快照
        valid_disk_snapshot_list.sort(key=lambda x: x.host_snapshot.id)

        return valid_disk_snapshot_list[-1].disk_snapshot if valid_disk_snapshot_list else None

    @staticmethod
    def get_host_snapshot_by_disk_snapshot(disk_snap_shot):
        return GetSnapshotList.get_host_snapshot_by_disk_snapshot(disk_snap_shot)

    def stop_work(self, task_obj):
        if task_obj.task_type == HTBSendTask.QEMU_WORK:
            code = boxService.box_service.StopQemuWorkv2(self.task.task_uuid, task_obj.disk_token)
        else:
            code, _ = boxService.box_service.StopCDPWork(self.task.task_uuid, task_obj.disk_token)
        return code


class HTBStopService(task.Task, WorkerLog):
    default_provides = 'task_content'

    def __init__(self, name, task_id, inject=None):
        super(HTBStopService, self).__init__('HTBStopService_{}'.format(task_id), inject=inject)
        self.task = HTBTask.objects.get(id=task_id)

    def execute(self, task_content, **kwargs):
        if HTBFlowEntrance.has_error(task_content):
            return task_content

        task_type = get_switch_type(self.task.id)

        # 切换到最新的模式
        if task_type == xdata.HTB_TASK_TYPE_LATEST:
            if self.task.schedule.host.is_remote:
                self.update_latest()  # 更新切换点为最新时间
            else:
                self.task.set_status(HTBTask.STOP_SERVICE)
                self.stop_service(self.task)
                update_ip_config(task_content, self.task.id)
                self.switch_ip(self.task, task_content)  # 拿掉源机的漂移IP
                self.update_latest()
        elif should_switch_ip(self.task.id):
            if self.task.schedule.host.is_remote:
                pass
            else:
                self.task.set_status(HTBTask.STOP_SERVICE)
                self.stop_service(self.task)
        else:
            # 不切换IP
            pass

        return task_content

    def update_latest(self):
        try:
            schedule = HTBTask.objects.get(id=self.task.id).schedule
            if schedule.task_type == HTBSchedule.OLD_POINT_NOT_NEED_UPDATE:
                self.log_debug(
                    'HTBStopService update_latest HTBSchedule task_type is OLD_POINT_NOT_NEED_UPDATE, not need update!')
                return None
            exc_info = json.loads(schedule.ext_config)
            item_list = exc_info['manual_switch']['point_id'].split('|')
            if item_list[0] == 'normal':
                self.log_debug('HTBStopService update_latest choice point is normal, not need update!')
            else:
                self.waite_async_cdp()

                self._start_update_time(exc_info)
                schedule.ext_config = json.dumps(exc_info)
                schedule.save(update_fields=['ext_config'])
        except Exception as e:
            self.log_error('HTBStopService update_latest fail!,error:{}'.format(e))

    def waite_async_cdp(self):
        self.log_debug('HTBStopService update_latest waite_async_cdp start')
        time.sleep(6)
        self.log_debug('HTBStopService update_latest waite_async_cdp end')

    def _start_update_time(self, exc_info):
        item_list = exc_info['manual_switch']['point_id'].split('|')
        host_snapshot_object = HostSnapshot.objects.get(id=item_list[1])
        end = host_snapshot_object.cdp_info.last_datetime
        if CDPHostSnapshotSpaceCollectionMergeTask.get_running_task_using(host_snapshot_object):
            end = timezone.now()
        or_time = exc_info['manual_switch']['restoretime']
        exc_info['manual_switch']['restoretime'] = end.strftime(xdatetime.FORMAT_WITH_MICROSECOND)
        self.log_debug(
            'HTBStopService update time or:{} ---> new:{}'.format(or_time, exc_info['manual_switch']['restoretime']))

    # 停止源的服务
    @xlogging.convert_exception_to_value(None)
    def stop_service(self, task_object):
        exc_info = get_exc_info(self.task)
        stop_path = exc_info['stop_script_zip_path']
        host_ident = task_object.schedule.host.ident
        if stop_path:
            ins = ClientIpMg.SendCompressAndRunInClient()
            _tmpdir = r'|tmp|\{}_{}'.format(task_object.schedule.id, 'user_stop')
            workdir = exc_info.get('stop_script_work_path', _tmpdir)
            unzip_dir = exc_info.get('stop_script_unzip_path', _tmpdir)
            param = exc_info.get('stop_script_exe_params', '')
            AppName = exc_info.get('stop_script_exe_name', '')
            cmd = {'AppName': AppName, 'param': param, 'workdir': workdir, 'unzip_dir': unzip_dir,
                   'timeout_sec': None,
                   'username': None, 'pwd': None, 'serv_zip_full_path': stop_path}
            ins.exec_one_cmd(host_ident, cmd, self.task_content['src_is_windows'])
        else:
            _logger.warning(r'not stop_script_zip_path')

    # 拿掉源的漂移IP
    def switch_ip(self, task_object, task_content):
        if not change_master_ip(self.task.id):
            _logger.info('HTBStopService switch_ip not switch master ip')
            return None

        if task_content['src_is_windows']:
            try:
                boxService.box_service.aswriteFile2Host(
                    task_object.schedule.host.ident, 'current', 'ht.json', 0, bytearray(r'', 'utf8'))
            except Exception as e:
                _logger.warning(r'clean host ht.json failed. {}'.format(e))

        task_id = TASK_ID_MIGRATE_FORMAT.format(self.task.id)
        cmd_info = {
            'task_type': xdata.HTB_SWITCH_IP_STEP_MIGRATE,
            'Host_id': task_object.schedule.host.ident, 'Host': task_content['master_adapter'],
            'Backup_id': None, 'Backup': task_content['standby_adapter'],
            'src_is_windows': task_content['src_is_windows'],
            'task_id': task_id
        }
        cmd = {'ID': task_id, 'cmdinfo': cmd_info, 'result': ''}
        while True:
            ClientIpMg.client_ip_mg_threading.switch_hot_backup(cmd)
            rs = cmd['result']
            self.log_debug('client_ip_mg_threading Query:{}'.format(rs))
            if user_stop(task_object.id):
                raise Exception('user stop!')
            if rs == 'success_switch_host':
                break
            time.sleep(5)
        task_content['switch_ip_result'] = 'success_switch_host'


class HTBWaiteSwitchCMD(task.Task, WorkerLog):
    default_provides = 'task_content'

    def __init__(self, name, task_id, inject=None):
        super(HTBWaiteSwitchCMD, self).__init__('HTBWaiteSwitchCMD_{}'.format(task_id), inject=inject)
        self.task = HTBTask.objects.get(id=task_id)
        self.task_content = None
        self.is_sys_restore = check_is_sys_restore(self.task)

    def execute(self, task_content, **kwargs):
        if HTBFlowEntrance.has_error(task_content):
            return task_content

        self.task.set_status(HTBTask.SENDCMD)

        self.task_content = task_content
        self.task_content['lock_snapshots'] = list()
        try:
            self.start_switch()
        except Exception as e:
            self.task_content['error'] = ('发送切换指令失败', 'start_switch error:{}'.format(e))
            self.log_error('start_switch error:{}'.format(e))
            return self.task_content

        return self.task_content

    def start_switch(self):
        disk_token_groups = self.task.send_task.values('disk_token').annotate(Count('disk_token'))
        for disk_info in disk_token_groups:
            self.send_disk_bitmap(self.task, disk_info)
        self.send_end_cmd()

    def send_end_cmd(self):
        rev = boxService.box_service.EndTask(self.task.task_uuid)
        if rev != 0:
            raise Exception('end task fail, rev:{}'.format(rev))

    def send_bitmap_same_family(self, disk_snapshot2task_list, disk_snapshot_object, disk_token, disk_snapshots,
                                restore_time_stamp, ex_vols, index_t):
        snapshots = [{'path': snap.path, 'snapshot': snap.snapshot} for snap in disk_snapshots]
        last_need_send_info = snapshots[-1]

        choice_snapshot2task = disk_snapshot2task_list[index_t[0]]
        choice_send_task = choice_snapshot2task['send_task']
        need_acquire_bit_map_snaps = list()

        # 向后获取bit_map_snaps
        self.log_debug('send_bitmap_same_family choice_send_task:{}'.format(choice_send_task))
        if choice_send_task:
            self.get_bit_map_snaps_from_future(choice_send_task, choice_snapshot2task, disk_snapshot2task_list,
                                               index_t, last_need_send_info, need_acquire_bit_map_snaps,
                                               restore_time_stamp)
        else:
            # 向前获取bit_map_snaps
            self.get_bit_map_snaps_from_past(need_acquire_bit_map_snaps, disk_snapshot2task_list[:index_t[0]],
                                             last_need_send_info)
        self._set_bit_map_and_update_tokens(disk_snapshot_object.bytes, disk_token, ex_vols, need_acquire_bit_map_snaps,
                                            snapshots)

    def _set_bit_map_and_update_tokens(self, disk_bytes, disk_token, ex_vols, need_acquire_bit_map_snaps,
                                       snapshots):
        bit_map_path = self._acquire_restore_bit_map_path(need_acquire_bit_map_snaps, disk_bytes)
        if bit_map_path:
            self._unset_exclude_vols_bits(disk_bytes, bit_map_path, ex_vols)
            rev = boxService.box_service.SetRestoreBitmapv2(self.task.task_uuid, bit_map_path, disk_token)
            if rev != 0:
                raise Exception('SetRestoreBitmapv2 failed')
            self._update_tokens(disk_bytes, disk_token, snapshots)

    def get_index_tuple(self, disk_snapshot2task_list, disk_snapshot_object):
        # 定位选择的disk_snap_shot
        index_t = (None, None)
        for index_a, snapshot2task in enumerate(disk_snapshot2task_list):
            for index_b, disk_snapshot in enumerate(snapshot2task['disk_snapshots']):
                if disk_snapshot_object.id == disk_snapshot.id:
                    index_t = (index_a, index_b)
                    break
        self.log_debug('send_bitmap_same_family index_t:{}'.format(index_t))
        return index_t

    # 将排除的区域位图设置为0
    # ex_vols = [{'bytes': 1048576, 'byteOffset': 0}, {'bytes': 31459377152, 'byteOffset': 11490295808}]
    # used_bit_map blk=64k used=1 [b'0xff',...,b'0x00']
    @staticmethod
    def _unset_exclude_vols_bits(disk_size, bit_map_path, ex_vols):
        with open(bit_map_path, 'rb+') as f:
            f.truncate(convert_disk_size2bitmap_size(disk_size))
            with mmap.mmap(f.fileno(), 0) as mm:
                for ex_vol in ex_vols:
                    first_lba = (ex_vol['byteOffset'] + 0x200 - 1) // 0x200
                    first_blk = first_lba // 0x80
                    last_lba = (ex_vol['byteOffset'] + ex_vol['bytes']) // 0x200
                    last_blk = last_lba // 0x80
                    for blk in range(first_blk, last_blk):
                        mm[blk // 8] &= ~(1 << (blk % 8))

    def _update_tokens(self, disk_bytes, disk_token, snapshots):
        _snapshots = [{'path': info['path'], 'snapshot': info['snapshot']} for info in snapshots]
        self.task_content['lock_snapshots'].extend(_snapshots)
        snapshot = [pyconv.convertJSON2OBJ(IMG.ImageSnapshotIdent, snap_dict) for
                    snap_dict in _snapshots]
        SendTaskWork.lock_snapshots_u(snapshot, get_lock_name(self.task.id))
        self.clear_token(disk_token)
        token_setting = KTService.Token(token=disk_token,
                                        snapshot=snapshot,
                                        diskBytes=disk_bytes)
        if not Tokens.set_token(token_setting):
            raise Exception('set token fail,{}'.format(token_setting))

    @staticmethod
    def clear_token(disk_token):
        token_setting = KTService.Token(token=disk_token,
                                        snapshot=[],
                                        expiryMinutes=0)
        if not Tokens.set_token(token_setting):
            raise Exception('clear_token token fail,{}'.format(token_setting))

    def _acquire_restore_bit_map_path(self, need_acquire_bit_map_snaps, disk_bytes):
        snap_list = list()
        bit_map_existed = ''
        self.log_debug('get_used_bit_map need_acquire_bit_map_snaps:{}'.format(need_acquire_bit_map_snaps))
        for item in need_acquire_bit_map_snaps:
            if isinstance(item, str):
                if not boxService.box_service.isFileExist(item):
                    xlogging.raise_and_logging_error('获取位图文件失败', 'get_bit_map fail')
                bit_map_existed = item
            else:
                snap_list.append(item)
        snap_shots = [pyconv.convertJSON2OBJ(IMG.ImageSnapshotIdent, snap_dict) for
                      snap_dict in snap_list]

        if snap_shots:
            last_snap = snap_shots[-1]
            # 需要修正时间为最大值
            if last_snap.snapshot == 'all':
                start_timestamp, end_timestamp = boxService.box_service.queryCdpTimestampRange(last_snap.path)
                last_snap.snapshot = GetSnapshotList.format_timestamp(None, end_timestamp)

            flag = r'PiD{:x} BoxDashboard|hot backup get_used_bit_map{}'.format(os.getpid(), self.task.id)
            bit_map = SnapshotsUsedBitMapGeneric(snap_shots, flag).get()
        else:
            bit_map = bytearray()

        if bit_map_existed:
            with open(bit_map_existed, 'rb+') as f:
                f.truncate(convert_disk_size2bitmap_size(disk_bytes))
                with mmap.mmap(f.fileno(), 0) as mm:
                    for index, _bytes in enumerate(bit_map):
                        mm[index] |= _bytes
            return bit_map_existed
        else:
            if bit_map:
                bit_map_path = os.path.join(xdata.HTB_DISK_FILES_DIR.format(self.task.task_uuid),
                                            '{}.bitmap'.format(uuid.uuid4().hex))
                with open(bit_map_path, 'wb') as f:
                    f.truncate(convert_disk_size2bitmap_size(disk_bytes))
                    f.write(bit_map)
                return bit_map_path
            else:
                return ''

    def get_bit_map_snaps_from_future(self, choice_send_task, choice_snapshot2task, disk_snapshot2task_list, index_t,
                                      last_need_send_info, need_acquire_bit_map_snaps, restore_time_stamp):
        if choice_send_task.o_bit_map:
            self.log_debug('get_bit_map_snaps_from_future has o_bit_map!')
            if len(choice_snapshot2task['disk_snapshots']) == 1:
                self.log_debug('get_bit_map_snaps_from_future disk_snapshots len is 1')
                need_acquire_bit_map_snaps.append(choice_send_task.o_bit_map)
            else:
                need_acquire_bit_map_snaps.append(choice_send_task.o_bit_map)
                for index, disk_snapshot in enumerate(choice_snapshot2task['disk_snapshots'][index_t[1]:]):
                    if index == 0:
                        if disk_snapshot.is_cdp and last_need_send_info['snapshot'] != 'all':
                            snapshot = GetSnapshotList.format_timestamp(restore_time_stamp, None)
                            snap = {'path': disk_snapshot.image_path, 'snapshot': snapshot}
                            need_acquire_bit_map_snaps.append(snap)
                        else:
                            continue
                    else:
                        snap = {'path': disk_snapshot.image_path, 'snapshot': disk_snapshot.ident}
                        if disk_snapshot.is_cdp:
                            snap['snapshot'] = 'all'
                        need_acquire_bit_map_snaps.append(snap)

        elif choice_send_task.o_stop_time:
            self.log_debug('get_bit_map_snaps_from_future has o_stop_time!')
            stop_time_stamp = choice_send_task.o_stop_time
            disk_snapshot = get_disk_snapshot_from_info(last_need_send_info['path'], last_need_send_info['snapshot'])
            if stop_time_stamp == 'end':
                if last_need_send_info['snapshot'] != 'all':
                    snapshot = GetSnapshotList.format_timestamp(restore_time_stamp, None)
                    snap = {'path': disk_snapshot.image_path, 'snapshot': snapshot}
                    need_acquire_bit_map_snaps.append(snap)
            else:
                stop_time_stamp = float(stop_time_stamp)
                restore_time_stamp = float(restore_time_stamp)
                if restore_time_stamp == stop_time_stamp:
                    # do nothing
                    pass
                else:
                    if last_need_send_info['snapshot'] != 'all':
                        arg = (restore_time_stamp, stop_time_stamp) \
                            if restore_time_stamp < stop_time_stamp else (stop_time_stamp, restore_time_stamp)
                        snapshot = GetSnapshotList.format_timestamp(*arg)
                        snap = {'path': disk_snapshot.image_path, 'snapshot': snapshot}
                        need_acquire_bit_map_snaps.append(snap)
                    else:
                        snapshot = GetSnapshotList.format_timestamp(stop_time_stamp, None)
                        snap = {'path': disk_snapshot.image_path, 'snapshot': snapshot}
                        need_acquire_bit_map_snaps.append(snap)
        elif choice_send_task.o_completed_trans:
            self.log_debug('get_bit_map_snaps_from_future has o_completed_trans!')
            for index_a, snapshot2task in enumerate(disk_snapshot2task_list[index_t[0]:]):
                for index_b, disk_snapshot in enumerate(snapshot2task['disk_snapshots']):
                    if index_a == 0:
                        if index_b < index_t[1]:
                            continue
                        if index_b == index_t[1]:
                            if disk_snapshot.is_cdp and last_need_send_info['snapshot'] != 'all':
                                snapshot = GetSnapshotList.format_timestamp(restore_time_stamp, None)
                                snap = {'path': disk_snapshot.image_path, 'snapshot': snapshot}
                                need_acquire_bit_map_snaps.append(snap)
                            else:
                                # do nothing
                                continue
                        if index_b > index_t[1]:
                            snap = {'path': disk_snapshot.image_path, 'snapshot': disk_snapshot.ident}
                            if disk_snapshot.is_cdp:
                                snap['snapshot'] = 'all'
                            need_acquire_bit_map_snaps.append(snap)
                    else:
                        send_task = snapshot2task['send_task']
                        if send_task:
                            if send_task.o_bit_map:
                                snap = {'path': disk_snapshot.image_path, 'snapshot': disk_snapshot.ident}
                                need_acquire_bit_map_snaps.append(snap)
                            elif send_task.o_stop_time:
                                if send_task.o_stop_time == 'end':
                                    snapshot = 'all'
                                else:
                                    snapshot = GetSnapshotList.format_timestamp(None, float(send_task.o_stop_time))
                                snap = {'path': disk_snapshot.image_path, 'snapshot': snapshot}
                                need_acquire_bit_map_snaps.append(snap)
                            elif send_task.o_completed_trans:
                                snap = {'path': disk_snapshot.image_path, 'snapshot': disk_snapshot.ident}
                                if disk_snapshot.is_cdp:
                                    snap['snapshot'] = 'all'
                                need_acquire_bit_map_snaps.append(snap)
                            else:
                                pass
                        else:
                            # do nothing
                            continue

        else:
            # 向前获取bit_map_snaps
            self.get_bit_map_snaps_from_past(need_acquire_bit_map_snaps, disk_snapshot2task_list[:index_t[0]],
                                             last_need_send_info)

    # 向前查找bitmap_snap_shots
    def get_bit_map_snaps_from_past(self, need_acquire_bit_map_snaps, disk_snapshot2task_list, last_need_send_info):
        self.log_debug('get_bit_map_snaps_from_past branch!')
        need_acquire_bit_map_snaps.append(
            {'path': last_need_send_info['path'], 'snapshot': last_need_send_info['snapshot']})
        for disk_snapshot2task in disk_snapshot2task_list[::-1]:
            send_task = disk_snapshot2task['send_task']
            if send_task:
                if send_task.o_stop_time:
                    if send_task.o_stop_time == 'end':
                        # do nothing
                        break
                    else:
                        disk_snapshot = disk_snapshot2task['disk_snapshots'][-1]
                        snapshot = GetSnapshotList.format_timestamp(float(send_task.o_stop_time), None)
                        snap = {'path': disk_snapshot.image_path, 'snapshot': snapshot}
                        need_acquire_bit_map_snaps.append(snap)
                        break
                elif send_task.o_bit_map:
                    # send_task.o_bit_map 为字符串
                    need_acquire_bit_map_snaps.append(send_task.o_bit_map)
                    break
                elif send_task.o_completed_trans:
                    # do nothing
                    break
                else:
                    for _disk_snap in disk_snapshot2task['disk_snapshots'][::-1]:
                        snap = {'path': _disk_snap.image_path, 'snapshot': _disk_snap.ident}
                        if _disk_snap.is_cdp:
                            snap['snapshot'] = 'all'
                        need_acquire_bit_map_snaps.append(snap)
            else:
                disk_snapshot = disk_snapshot2task['disk_snapshots'][-1]
                snap = {'path': disk_snapshot.image_path, 'snapshot': disk_snapshot.ident}
                if disk_snapshot.is_cdp:
                    snap['snapshot'] = 'all'
                need_acquire_bit_map_snaps.append(snap)
        need_acquire_bit_map_snaps.reverse()
        return None

    def send_bit_map(self, disk_snapshot2task_list, ex_vols):
        if len(disk_snapshot2task_list) != 1:
            raise Exception('send task len != 1, {}'.format(disk_snapshot2task_list))
        last_task = disk_snapshot2task_list[-1]['send_task']
        last_disk_snapshot = disk_snapshot2task_list[-1]['disk_snapshots'][-1]
        if not last_task.o_completed_trans and last_task.o_bit_map:
            self._set_bit_map_and_update_tokens(last_disk_snapshot.bytes, last_task.disk_token, ex_vols,
                                                [last_task.o_bit_map],
                                                json.loads(last_task.snapshots))

    def send_disk_bitmap(self, task_obj, disk_info):
        self.log_info(
            'get_disk_snapshot2task_list task_obj:{},disk_info:{}'.format(task_obj.id, disk_info['disk_token']))
        disk_snapshot2task_list = list()
        last_task = HTBWaiteSwitchCMD._get_last_task(disk_info, task_obj)
        item = dict()
        item['send_task'] = last_task
        item['disk_snapshots'] = HTBWaiteSwitchCMD.from_task_get_disk_snapshots(last_task)

        ex_vols = json.loads(last_task.ex_vols)
        # 还原到特定的点，不需要扩展列表
        if task_obj.schedule.task_type == HTBSchedule.OLD_POINT_NOT_NEED_UPDATE:
            disk_snapshot2task_list.append(item)
            self.send_bit_map(disk_snapshot2task_list, ex_vols)
        else:
            schedule = HTBTask.objects.get(id=self.task.id).schedule
            exc_info = json.loads(schedule.ext_config)
            item_list = exc_info['manual_switch']['point_id'].split('|')
            restore_time = get_switch_time(self.task.id)
            self.log_debug('send_disk_bitmap get_switch_time restore_time:{}'.format(restore_time))

            restore_timestamp, disk_snapshot_object, \
            disk_snapshots = self.get_need_restore_snapshots(item_list[1],
                                                             last_task.native_guid,
                                                             restore_time.timestamp())
            self.log_debug('send_disk_bitmap will restore to:{}'.format(disk_snapshots))
            self._update_restore_token_local(disk_info['disk_token'], disk_snapshot_object, restore_timestamp)

            # 选择的点 和 发送的点 是一个链
            if self.snapshots_is_same_family(last_task.native_guid, item['disk_snapshots'][-1],
                                             item_list[1]):
                disk_snapshot2task_list = self.get_same_family_snapshots(disk_snapshot2task_list, item, last_task,
                                                                         disk_snapshots)
                index_t = self.get_index_tuple(disk_snapshot2task_list, disk_snapshot_object)
                if index_t == (None, None):
                    # 在 disk_snapshot2task_list 无法定位到 需要还原的磁盘快照
                    self.send_bitmap_not_same_family(disk_info, disk_snapshot_object, disk_snapshots, ex_vols)
                else:
                    self.send_bitmap_same_family(disk_snapshot2task_list, disk_snapshot_object,
                                                 disk_info['disk_token'], disk_snapshots,
                                                 restore_timestamp, ex_vols, index_t)
            # 选择的点 和 发送的点 不是一个链
            else:
                self.send_bitmap_not_same_family(disk_info, disk_snapshot_object, disk_snapshots, ex_vols)

    # todo 加入增量还原后，需要重新计算 还原需要用到的hash
    def _update_restore_token_local(self, disk_token, disk_snapshot, timestamp):
        restore_target_disk = RestoreTargetDisk.objects.get(token=disk_token)
        restore_target_disk.snapshot = disk_snapshot
        restore_target_disk.snapshot_timestamp = float(timestamp) if timestamp else None
        restore_target_disk.save(update_fields=['snapshot_timestamp', 'snapshot'])

    def send_bitmap_not_same_family(self, disk_info, disk_snapshot_object, disk_snapshots, ex_vols):
        snapshots = [{'path': snap.path, 'snapshot': snap.snapshot} for snap in disk_snapshots]
        self._set_bit_map_and_update_tokens(disk_snapshot_object.bytes, disk_info['disk_token'], ex_vols, snapshots,
                                            snapshots)

    def get_need_restore_snapshots(self, host_snapshot_id, native_guid, timestamp):
        self.log_debug('get_need_restore_snapshots host_snapshot_id:{}'.format(host_snapshot_id))
        host_snapshot = HostSnapshot.objects.get(id=host_snapshot_id)
        disk_snapshot = GetDiskSnapshot.get_by_native_guid_and_host_snapshot(native_guid, host_snapshot)
        assert disk_snapshot, 'not find disk_snapshot host_snapshot:{}|native_guid:{}'.format(
            host_snapshot_id, native_guid)
        disk_snapshot_ident = disk_snapshot.ident
        disk_ident = disk_snapshot.disk.ident
        restore_timestamp = None
        if host_snapshot.is_cdp:
            disk_snapshot_ident, restore_timestamp = \
                GetDiskSnapshot.query_cdp_disk_snapshot_ident(host_snapshot, disk_ident, timestamp)
            if disk_snapshot_ident is None or restore_timestamp is None:
                self.log_warning('no valid cdp disk snapshot use normal snapshot : {} {} {}'.format(
                    host_snapshot.id, disk_ident, timestamp))
                disk_snapshot_ident = GetDiskSnapshot.query_normal_disk_snapshot_ident(host_snapshot,
                                                                                       disk_ident)
        disk_snapshot_object = DiskSnapshot.objects.get(ident=disk_snapshot_ident)

        validator_list = [GetSnapshotList.is_disk_snapshot_object_exist,
                          GetSnapshotList.is_disk_snapshot_file_exist]
        disk_snapshots = GetSnapshotList.query_snapshots_by_snapshot_object(
            disk_snapshot_object, validator_list, restore_timestamp, include_all_node=True)
        if not disk_snapshots:
            xlogging.raise_and_logging_error('获取快照链失败', 'get snapshots failed')
        return restore_timestamp, disk_snapshot_object, disk_snapshots

    def get_same_family_snapshots(self, disk_snapshot2task_list, item, last_task, disk_snapshots):
        self.log_debug('get_same_family_snapshots last_task:{}'.format(last_task))
        if len(item['disk_snapshots']) == 1:
            disk_snapshot2task_list = HTBWaiteSwitchCMD.append_past_disk_snapshot(item['disk_snapshots'][-1],
                                                                                  last_task)
        disk_snapshot2task_list.append(item)
        if len(disk_snapshot2task_list) == 0:
            raise Exception('start_switch error never send task!')
        last_item = disk_snapshot2task_list[-1]
        last_disk_snapshot = last_item['disk_snapshots'][-1]
        HTBWaiteSwitchCMD.expend_valid_item(disk_snapshot2task_list, last_disk_snapshot, disk_snapshots)
        return disk_snapshot2task_list

    def snapshots_is_same_family(self, native_guid, disk_snapshot, host_snapshot_id):
        choice_host_snapshot = HostSnapshot.objects.get(id=host_snapshot_id)
        choice_disk_snapshot = GetDiskSnapshot.get_by_native_guid_and_host_snapshot(native_guid, choice_host_snapshot)
        rev = choice_disk_snapshot.disk.ident == disk_snapshot.disk.ident
        self.log_debug('check_snapshots_is_same_family host_snapshot_id:{}, disk_snapshot:{} is_same:{}'.format(
            host_snapshot_id, disk_snapshot, rev))
        return rev

    @staticmethod
    def _get_last_task(disk_info, task_obj):
        send_tasks = HTBSendTask.objects.filter(disk_token=disk_info['disk_token'], htb_task=task_obj).order_by('-id')
        last_task = None
        for send_task in send_tasks:
            if send_task.task_type == HTBSendTask.QEMU_WORK:
                if send_task.o_bit_map or send_task.o_completed_trans:
                    last_task = send_task
                    break
            elif send_task.task_type == HTBSendTask.CLOSED_CDP_WORK:
                if send_task.o_stop_time or send_task.o_completed_trans:
                    last_task = send_task
                    break
            else:
                if send_task.o_stop_time:
                    last_task = send_task
                    break
        if not last_task:
            raise Exception('_get_last_task not find last task!')
        return last_task

    @staticmethod
    def append_past_disk_snapshot(last_disk_snapshot, last_task):
        validator_list = [GetSnapshotList.is_disk_snapshot_object_exist,
                          GetSnapshotList.is_disk_snapshot_file_exist]
        disk_snapshots = GetSnapshotList.query_snapshots_by_snapshot_object(
            last_disk_snapshot, validator_list, include_all_node=True)
        if len(disk_snapshots) == 0:
            raise Exception('append_past_disk_snapshot disk_snapshots len==0,{}'.format(last_disk_snapshot))
        _tmp = list()
        for disk_snapshot_info in disk_snapshots[:-1]:
            disk_snapshot_obj = get_disk_snapshot_from_info(disk_snapshot_info.path, disk_snapshot_info.snapshot)
            item = dict()
            item['disk_snapshots'] = [disk_snapshot_obj]
            if disk_snapshot_obj.is_cdp:
                task_type = HTBSendTask.CLOSED_CDP_WORK
                snap = [{'path': disk_snapshot_obj.image_path, 'snapshot': 'all'}]
            else:
                task_type = HTBSendTask.QEMU_WORK
                snap = [{'path': disk_snapshot_obj.image_path, 'snapshot': disk_snapshot_obj.ident}]

            send_task = HTBSendTask(
                htb_task=last_task.htb_task,
                disk_token=last_task.disk_token,
                native_guid=last_task.native_guid,
                task_type=task_type,
                snapshots=json.dumps(snap),
                o_completed_trans=True
            )
            item['send_task'] = send_task
            _tmp.append(item)
        return _tmp

    @staticmethod
    def get_disk_snapshot_timerange(disk_snapshot):
        if disk_snapshot.is_cdp:
            if HTBStartTransferData.check_is_unclosed_cdp(disk_snapshot):
                start_timestamp, end_timestamp = boxService.box_service.queryCdpTimestampRange(
                    disk_snapshot.image_path)
            else:
                start_timestamp, end_timestamp = boxService.box_service.queryCdpTimestampRange(
                    disk_snapshot.image_path, True)
        else:
            host_snapshot = HTBStartTransferData.get_host_snapshot_by_disk_snapshot(disk_snapshot)
            timestamp = host_snapshot.start_datetime.timestamp()
            start_timestamp = timestamp
            end_timestamp = timestamp
        _logger.debug('get_disk_snapshot_timerange disk_snapshot:{}---{},,,{}'.format(disk_snapshot.image_path,
                                                                                      datetime.datetime.fromtimestamp(
                                                                                          start_timestamp),
                                                                                      datetime.datetime.fromtimestamp(
                                                                                          end_timestamp)))
        return start_timestamp, end_timestamp

    @staticmethod
    def merge_time(tmp_rs):
        rs = list()
        prv = tmp_rs[0]
        rs.append(prv)
        for item in tmp_rs[1:]:
            if float(prv[1]) == float(item[0]):
                rs[-1] = (prv[0], item[1])
            else:
                rs.append(item)
            prv = item
        return rs

    @staticmethod
    def expend_valid_item(disk_snapshot2task_list, last_disk_snapshot, disk_snapshots):
        key_node = False
        for snapshot_info in disk_snapshots:
            disk_snapshot = get_disk_snapshot_from_info(snapshot_info.path, snapshot_info.snapshot)
            if key_node:
                item = dict()
                item['send_task'] = None
                item['disk_snapshots'] = [disk_snapshot]
                disk_snapshot2task_list.append(item)
            else:
                if disk_snapshot.id == last_disk_snapshot.id:
                    key_node = True

    @staticmethod
    def from_task_get_disk_snapshots(task_obj):
        return [get_disk_snapshot_from_info(snapshot['path'], snapshot['snapshot']) for snapshot in
                json.loads(task_obj.snapshots)]


class HTBWaiteHostLogin(task.Task, WorkerLog):
    default_provides = 'task_content'

    def __init__(self, name, task_id, inject=None):
        super(HTBWaiteHostLogin, self).__init__('HTBWaiteHostLogin_{}'.format(task_id), inject=inject)
        self.task = HTBTask.objects.get(id=task_id)
        self.task_content = None

    def execute(self, task_content, **kwargs):
        if HTBFlowEntrance.has_error(task_content):
            return task_content

        self.task_content = task_content

        self.task.set_status(HTBTask.WAITECMPLTE)
        try:
            self.wait_restore_target_login()
        except Exception as e:
            self.log_error('wait_restore_target_login:{}'.format(e))
            self.task_content['error'] = ('等待客户端登录失败', 'wait_restore_target_login:{}'.format(e))
        return self.task_content

    def wait_restore_target_login(self):
        info = json.loads(self.task.restore_target.info)
        macs = [cfg['szMacAddress'].upper() for cfg in info['net_adapters'] if
                cfg['szMacAddress'] and len(cfg['szMacAddress']) == 12]
        reboot_datetime = datetime.datetime.strptime(self.task_content['since_reboot'], '%Y-%m-%d %H:%M:%S.%f')
        while True:
            if self.task_content['check_login_use_mac']:
                link, host = self._check_host_link_by_mac(macs)
            else:
                link, host = self._check_host_link_from_ext_info()
            if link and self.is_host_alive(host.ident) and host.login_datetime and (
                    host.login_datetime > reboot_datetime):
                self.log_info('wait_restore_target_login:{}'.format(host.ident))
                self.task_content['target_host_ident'] = host.ident
                break
            else:
                pass
            time.sleep(5)
            if user_stop(self.task.id):
                raise Exception('user stop!')
        self.log_debug('HTBWaiteHostLogin wait_restore_target_login begin:{}'.format(macs))

    @xlogging.convert_exception_to_value(False)
    def is_host_alive(self, host_ident):
        return boxService.box_service.isAgentLinked(host_ident)

    def _check_host_link_from_ext_info(self):
        task = HTBTask.objects.get(id=self.task.id)
        ext_config = json.loads(task.ext_config)
        self.log_debug('HTBWaiteHostLogin _check_host_link_from_ext_info ext_config:{}'.format(ext_config))
        if ext_config.get('is_login', False):
            try:
                host = Host.objects.get(ident=ext_config['host_ident'])
            except Host.DoesNotExist:
                return False, None
            return True, host
        else:
            return False, None

    def _check_host_link_by_mac(self, macs):
        self.log_debug('HTBWaiteHostLogin _check_host_link_by_mac waite for masc:{}'.format(macs))
        host_mac = HostMac.objects.filter(mac__in=macs, duplication=False).first()
        if host_mac and host_mac.host and host_mac.host.is_linked:
            return True, host_mac.host
        else:
            return False, None


class HTBTransferDrivers(task.Task, WorkerLog):
    default_provides = 'task_content'

    def __init__(self, name, task_id, inject=None):
        super(HTBTransferDrivers, self).__init__('HTBTransferDrivers_{}'.format(task_id), inject=inject)
        self.task = HTBTask.objects.get(id=task_id)
        self.task_content = None
        self.file_dir = xdata.HTB_DISK_FILES_DIR.format(self.task.task_uuid)

    def execute(self, task_content, **kwargs):
        if HTBFlowEntrance.has_error(task_content):
            return task_content

        self.task_content = task_content
        self.task.set_status(HTBTask.TRANSDRIVERS)

        try:
            self.transfer_drivers()
        except Exception as e:
            task_content['error'] = ('传输驱动数据', 'HTBTransferDrivers fail:{}'.format(e))
            _logger.debug('HTBTransferDrivers fail:{}'.format(e))

        return task_content

    def transfer_drivers(self):
        if self.task_content['src_is_windows']:
            pass
        else:
            if check_is_sys_restore(self.task):
                self.worker_for_linux()
            else:
                pass  # 卷热备do nothing

    def worker_for_linux(self):
        file_path = os.path.join(self.file_dir, 'actions.json')
        with open(file_path) as f:
            command_list = json.load(f)

        self.log_info('worker_for_linux command_list:{}'.format(command_list))
        handle = ClientIpMg.SendCompressAndRunInClient()
        if command_list and isinstance(command_list, list):
            for one_cmd in command_list:
                action = one_cmd['action']
                if action == 'push_file':
                    self._push_file(handle, one_cmd)
                elif action == 'exc_command':
                    self._exc_command(handle, one_cmd)
                else:
                    self.log_error('worker_for_linux not support command, one_cmd:{}'.format(one_cmd))
        else:
            self.log_warning('worker_for_linux command_list is empty or invalid')

    def _push_file(self, handle, one_cmd):
        host_ident = self.task_content['target_host_ident']
        handle.push_file(host_ident, one_cmd['src_path'], one_cmd['dst_path'], one_cmd['dst_dir'])

    def _exc_command(self, handle, one_cmd):
        host_ident = self.task_content['target_host_ident']
        handle.exc_command(host_ident, one_cmd['exc_dict'])


class HTBSwitchIp(task.Task, WorkerLog):
    default_provides = 'task_content'

    def __init__(self, name, task_id, inject=None):
        super(HTBSwitchIp, self).__init__('HTBSwitchIp_{}'.format(task_id), inject=inject)
        self.task = HTBTask.objects.get(id=task_id)
        self.task_content = None

    def execute(self, task_content, **kwargs):
        # task_id = TASK_ID_MIGRATE_FORMAT.format(self.task.id)
        if HTBFlowEntrance.has_error(task_content):
            self.teardown_handle(task_content)
            # ClientIpMg.client_ip_mg_threading.Remove(task_id)
            return task_content

        update_ip_config(task_content, self.task.id)

        if not should_switch_ip(self.task.id):
            self.log_info('HTBSwitchIp not should_switch_ip!')
            self.teardown_handle(task_content)
            # 移除“IP配置”
            # ClientIpMg.client_ip_mg_threading.Remove(task_id)
            return task_content

        self.task_content = task_content
        self.task.set_status(HTBTask.SWITCH_IP)

        try:
            self.switch_ip(task_content)
        except Exception as e:
            task_content['error'] = ('切换IP失败', 'HTBSwitchIp fail:{}'.format(e))
            _logger.debug('HTBSwitchIp fail:{}'.format(e), exc_info=True)
        # finally:
        #     ClientIpMg.client_ip_mg_threading.Remove(task_id)

        return task_content

    def teardown_handle(self, task_content):
        if not change_master_ip(self.task.id):
            _logger.info('HTBSwitchIp teardown_handle not switch master ip')
            return None
        # 移除“主机重启后仅保留固有IP”
        if task_content['src_is_windows']:
            self.remove_ht_json_file(self.task.schedule.host.ident)
        else:
            # 设置源的配置文件,不重启网络
            self.set_src_ip(self.task.schedule.host.ident, task_content['master_adapter'])

    def switch_ip(self, task_content):
        if task_content['src_is_windows'] and change_master_ip(self.task.id):
            try:
                boxService.box_service.aswriteFile2Host(
                    self.task_content['target_host_ident'], 'current', 'ht.json', 0, bytearray(r'', 'utf8'))
            except Exception as e:
                _logger.warning(r'clean host ht.json failed. {}'.format(e))

        task_id = TASK_ID_MIGRATE_FORMAT.format(self.task.id)
        Host_id = None
        Host_master_adapter = None
        if not self.task.schedule.host.is_remote:
            Host_id = self.task.schedule.host.ident
            Host_master_adapter = task_content['master_adapter']
        cmd_info = {
            'task_type': xdata.HTB_SWITCH_IP_STEP_MIGRATE,
            'Host_id': Host_id, 'Host': Host_master_adapter,
            'Backup_id': self.task_content['target_host_ident'], 'Backup': task_content['standby_adapter'],
            'src_is_windows': task_content['src_is_windows'],
            'task_id': task_id
        }

        if change_master_ip(self.task.id):
            cmd = {'ID': task_id, 'cmdinfo': cmd_info, 'result': task_content['switch_ip_result']}
        else:
            cmd_info['Host_id'] = None
            cmd_info['Host'] = None
            cmd = {'ID': task_id, 'cmdinfo': cmd_info, 'result': 'success_switch_host'}

        while True:
            ClientIpMg.client_ip_mg_threading.switch_hot_backup(cmd)
            rs = cmd['result']
            self.log_debug('client_ip_mg_threading Query:{}'.format(rs))
            if user_stop(self.task.id):
                raise Exception('user stop!')
            if rs == 'success':
                break
            time.sleep(5)
        task_content['switch_ip_result'] = 'success'

    @staticmethod
    @xlogging.convert_exception_to_value(None)
    def remove_ht_json_file(host_ident):
        boxService.box_service.aswriteFile2Host(host_ident, 'current', 'ht.json', 0, bytearray(r'', 'utf8'))

    @staticmethod
    @xlogging.convert_exception_to_value(None)
    def set_src_ip(host_ident, adapter_list):
        adapter_dict = dict()
        adapter_dict['ip_info'] = list()
        adapter_dict['ip_info_file'] = adapter_list
        ClientIpMg.client_ip_mg_threading.set_host_ip_linux(host_ident, adapter_dict)


class HTBTransferUserScript(task.Task, WorkerLog):
    default_provides = 'task_content'

    def __init__(self, name, task_id, inject=None):
        super(HTBTransferUserScript, self).__init__('HTBTransferUserScript_{}'.format(task_id), inject=inject)
        self.task = HTBTask.objects.get(id=task_id)
        self.task_content = None
        self.file_dir = xdata.HTB_DISK_FILES_DIR.format(self.task.task_uuid)

    def execute(self, task_content, **kwargs):
        if HTBFlowEntrance.has_error(task_content):
            return task_content

        self.task_content = task_content
        self.task.set_status(HTBTask.TRANSCRIPT)

        try:
            self.transfer_user_start_script()
        except Exception as e:
            task_content['error'] = ('传输用户脚本', 'HTBTransferUserScript fail:{}'.format(e))
            self.log_error('HTBTransferUserScript fail:{}'.format(e))

        return task_content

    def transfer_user_start_script(self):
        exc_info = get_exc_info(self.task)
        start_path = exc_info['start_script_zip_path']
        host_ident = self.task_content['target_host_ident']
        if start_path:
            ins = ClientIpMg.SendCompressAndRunInClient()
            _tmpdir = r'|tmp|\{}_{}'.format(self.task.schedule.id, 'user_start')
            workdir = exc_info.get('start_script_work_path', _tmpdir)
            unzip_dir = exc_info.get('start_script_unzip_path', _tmpdir)
            param = exc_info.get('start_script_exe_params', '')
            AppName = exc_info.get('start_script_exe_name', '')
            cmd = {'AppName': AppName, 'param': param, 'workdir': workdir, 'unzip_dir': unzip_dir,
                   'timeout_sec': None,
                   'username': None, 'pwd': None, 'serv_zip_full_path': start_path}
            ins.exec_one_cmd(host_ident, cmd, self.task_content['src_is_windows'])
        else:
            self.log_warning(r'not start_script_zip_path')


class HTBCloseTask(task.Task, WorkerLog):
    default_provides = 'task_content'

    def __init__(self, name, task_id, inject=None):
        super(HTBCloseTask, self).__init__('HTBCloseTask_{}'.format(task_id), inject=inject)
        self.task = HTBTask.objects.get(id=task_id)

    def execute(self, task_content, **kwargs):
        try:
            self.stop_not_stop_task()
            boxService.box_service.CloseTask(self.task.task_uuid)
        except Exception as e:
            task_content['error'] = 'HTBCloseTask fail:{}'.format(e)
            self.log_error('HTBCloseTask fail:{}'.format(e))

        return task_content

    def stop_not_stop_task(self):
        try:
            self.log_debug('HTBCloseTask stop_not_stop_task begin!')
            disk_token_groups = self.task.send_task.values('disk_token').annotate(Count('disk_token'))
            for index, disk_info in enumerate(disk_token_groups):
                last_send_task = self.task.send_task.filter(disk_token=disk_info['disk_token']).last()
                send_task_work = SendTaskWork(self.task, last_send_task)
                send_task_work.stop()
            _logger.debug('HTBCloseTask stop_not_stop_task end!')
        except Exception as e:
            self.log_error('stop_not_stop_task error:{}'.format(e))


class HTBFinisHTask(task.Task, WorkerLog):
    def __init__(self, name, task_id, inject=None):
        super(HTBFinisHTask, self).__init__('HTBFinisHTask_{}'.format(task_id), inject=inject)
        self.task = HTBTask.objects.get(id=task_id)
        self.task_content = None

    def execute(self, task_content, **kwargs):
        self.task_content = task_content

        self.unlock_snapshots()

        src_host_ident = self.task.schedule.host.ident
        target_host_ident = self.task_content.get('target_host_ident', None)
        if not HTBFlowEntrance.has_error(task_content):
            self.finish_htb_task(self.task, True, '', '', target_host_ident, src_host_ident)
        else:
            debug = task_content['error']
            self.finish_htb_task(self.task, False, debug, '', target_host_ident, src_host_ident)

        return task_content

    @staticmethod
    @xlogging.convert_exception_to_value(None)
    def finish_htb_task(htb_task, successful, debug, msg, *host_idents):
        htb_task.finish_datetime = timezone.now()

        restore_target = get_restore_target(htb_task)
        if successful:
            htb_task.successful = True
            htb_task.schedule.cancel_stand_by()
            htb_task.set_status(HTBTask.MISSUC)
            finish_target(restore_target, True, '', '')
            if restore_target:
                authorize_init.save_host_rebuild_record(restore_target.ident)
        else:
            running_task = json.loads(htb_task.running_task)
            running_task['error'] = debug
            htb_task.running_task = json.dumps(running_task, ensure_ascii=False)
            htb_task.set_status(HTBTask.MISFAIL, debug)
            finish_target(restore_target, False, '', '')
        htb_task.save(update_fields=['finish_datetime', 'successful', 'running_task'])

        HTBFinisHTask.modify_config(htb_task)

        HTBFinisHTask.clear_message_dict(htb_task.task_uuid)

        HTBFinisHTask.clear_bit_map(htb_task.task_uuid)

        HTBFinisHTask.update_host_info(*host_idents)

        remove(xdata.HTB_DISK_FILES_DIR.format(htb_task.task_uuid))

    @staticmethod
    @xlogging.convert_exception_to_value(None)
    def update_host_info(*host_idents):
        from apiv1.planScheduler import UpdateHostSysInfo
        for host_ident in host_idents:
            if not host_ident:
                continue
            UpdateHostSysInfo.update(host_ident)

    @staticmethod
    @xlogging.convert_exception_to_value(None)
    def modify_config(htb_task):
        schedule = htb_task.schedule
        exc_info = json.loads(schedule.ext_config)
        exc_info['manual_switch']['status'] = 3
        schedule.ext_config = json.dumps(exc_info)
        schedule.save(update_fields=['ext_config'])

    @staticmethod
    @xlogging.LockDecorator(boxService.message_dict_locker)
    def clear_message_dict(task_uuid):
        boxService.message_dict = {
            key: value for key, value in boxService.message_dict.items() if (not key.startswith(task_uuid))
        }

    @staticmethod
    @xlogging.LockDecorator(boxService.bit_map_locker)
    def clear_bit_map(task_uuid):
        boxService.bit_map_object = {
            key: value for key, value in boxService.bit_map_object.items() if (not key.startswith(task_uuid))
        }

    def unlock_snapshots(self):
        try:
            self.waite_restore_target_finish()
        except Exception as e:
            self.task_content['error'] = ('等待数据传输完毕失败', 'waite_restore_target_finish fail:{}'.format(e))
            self.log_error('HTBFinisHTask unlock_snapshots error:{}'.format(e))
        finally:
            lock_snapshots = self.task_content.get('lock_snapshots', list())
            if lock_snapshots:
                snapshot = [pyconv.convertJSON2OBJ(IMG.ImageSnapshotIdent, snap_dict) for
                            snap_dict in lock_snapshots]
                SendTaskWork.unlock_snapshots_u(snapshot, get_lock_name(self.task.id))

    def waite_restore_target_finish(self):
        if HTBFlowEntrance.has_error(self.task_content):
            return None
        self.task.set_status(HTBTask.WAITETRANSEND)
        restore_target = get_restore_target(self.task)
        self.log_info('waite_restore_target_finish begin target:{}'.format(restore_target.ident))
        while not restore_target.finish_datetime:
            self.log_info('waite_restore_target_finish target:{}'.format(restore_target.ident))
            restore_target = RestoreTarget.objects.get(ident=restore_target.ident)
            if user_stop(self.task.id):
                raise Exception('user stop!')
            time.sleep(5)
        self.log_info(
            'waite_restore_target_finish end target:{} finish_datetime {} successful {}'.format(restore_target.ident,
                                                                                                restore_target.finish_datetime,
                                                                                                restore_target.successful))
        if not restore_target.successful:
            raise Exception('restore task fail!')


class HTBFlowEntrance(threading.Thread):
    def __init__(self, task_id):
        super(HTBFlowEntrance, self).__init__()
        self.name = r'HTBFlowEntrance_{}'.format(task_id)
        self._task_id = task_id
        self._engine = None
        self._book_uuid = None
        self.task = HTBTask.objects.get(id=task_id)
        self.is_sys_restore = check_is_sys_restore(self.task)

    def load_from_uuid(self, task_uuid):
        backend = task_backend.get_backend()
        with contextlib.closing(backend.get_connection()) as conn:
            book = conn.get_logbook(task_uuid['book_id'])
            flow_detail = book.find(task_uuid['flow_id'])
        self._engine = engines.load_from_detail(flow_detail, backend=backend, engine='serial')
        self.name += r' load exist uuid {} {}'.format(task_uuid['book_id'], task_uuid['flow_id'])
        self._book_uuid = book.uuid

    def generate_uuid(self):
        backend = task_backend.get_backend()
        book = models.LogBook(
            r"{}_{}".format(self.name, datetime.datetime.now().strftime(xdatetime.FORMAT_WITH_SECOND_FOR_PATH)))
        with contextlib.closing(backend.get_connection()) as conn:
            conn.save_logbook(book)

        try:
            if self.is_sys_restore:
                create_flow = create_flow_for_system
            else:
                create_flow = create_flow_for_volume
            self._engine = engines.load_from_factory(create_flow, backend=backend, book=book, engine='serial',
                                                     factory_args=(self.name, self._task_id)
                                                     )

            self._book_uuid = book.uuid
            return {'book_id': book.uuid, 'flow_id': self._engine.storage.flow_uuid}
        except Exception as e:
            _logger.error(r'generate_uuid failed {}'.format(e))
            _logger.error('{}'.format(traceback.format_exc()))
            with contextlib.closing(backend.get_connection()) as conn:
                conn.destroy_logbook(book.uuid)
            raise e

    def start(self):
        if self._engine:
            super().start()
        else:
            xlogging.raise_and_logging_error('内部异常，无效的调用', r'start without _engine ：{}'.format(self.name),
                                             status.HTTP_501_NOT_IMPLEMENTED)

    def run(self):
        try:
            with logging_listener.DynamicLoggingListener(self._engine):
                self._engine.run()
        except Exception as e:
            _logger.error(r'HTBFlowEntrance run engine {} failed {}'.format(self.name, e))
            _logger.error('{}'.format(traceback.format_exc()))
            HTBFinisHTask.finish_htb_task(self.task, False, '{}'.format(traceback.format_exc()), '')
        finally:
            with contextlib.closing(task_backend.get_backend().get_connection()) as conn:
                conn.destroy_logbook(self._book_uuid)
        self._engine = None

    @staticmethod
    def has_error(task_content):
        return task_content['error']


def create_flow_for_system(name, task_id):
    flow = lf.Flow(name).add(
        HTBInit(name, task_id),
        HTBStartRestore(name, task_id),
        HTBPrepareSwitchIp(name, task_id),
        HTBStartTransferData(name, task_id),
        HTBStopService(name, task_id),
        HTBWaiteSwitchCMD(name, task_id),
        HTBWaiteHostLogin(name, task_id),
        HTBTransferDrivers(name, task_id),
        HTBSwitchIp(name, task_id),
        HTBTransferUserScript(name, task_id),
        HTBCloseTask(name, task_id),
        HTBFinisHTask(name, task_id),
    )
    return flow


def create_flow_for_volume(name, task_id):
    flow = lf.Flow(name).add(
        HTBInitForVol(name, task_id),
        HTBStartRestoreForVol(name, task_id),
        HTBPreTransDataForVol(name, task_id),
        HTBPrepareSwitchIp(name, task_id),
        HTBStartTransferData(name, task_id),
        HTBStopService(name, task_id),
        HTBWaiteSwitchCMD(name, task_id),
        HTBTransferDrivers(name, task_id),
        HTBSwitchIp(name, task_id),
        HTBTransferUserScript(name, task_id),
        HTBFinisHTask(name, task_id),
        HTBCloseTask(name, task_id),
    )
    return flow
