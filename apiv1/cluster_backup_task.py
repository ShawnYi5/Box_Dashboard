import contextlib
import copy
import datetime
import json
import os
import threading
import time
import uuid
from functools import partial

from rest_framework import status as http_status
from taskflow import engines, task
from taskflow.listeners import logging as logging_listener
from taskflow.patterns import linear_flow as lf
from taskflow.persistence import models

from apiv1.models import ClusterBackupTask, DiskSnapshot, HostSnapshot, DiskSnapshotCDP, CDPDiskToken, CDPTask, \
    HostSnapshotCDP, HostLog, BackupTask, SpaceCollectionTask, ClusterBackupSchedule, BackupTaskSchedule
from apiv1.snapshot import GetSnapshotList, GetDiskSnapshot, DiskSnapshotHash, Tokens, DiskSnapshotLocker
from apiv1.spaceCollection import DeleteDiskSnapshotTask, DeleteCdpFileTask, CDPHostSnapshotSpaceCollectionMergeTask
from apiv1.task_helper import TaskHelper
from apiv1.task_queue import queue
from apiv1.tasks import is_force_full_by_config, is_force_full_by_schedule, BackupScheduleRetryHandle, \
    is_force_store_full_by_config, CDPTaskWorker, Sleep
from apiv1.work_processors import HostBackupWorkProcessors, HostSnapshotFinishHelper
from box_dashboard import xlogging, xdatetime, xdata, boxService
from box_dashboard.task_backend import get_backend

_logger = xlogging.getLogger(__name__)
_logger_human = xlogging.getLogger('cluster_human')

import IMG

_generate_locker = threading.Lock()

_running_tasks = list()


def copyfile(s, d):
    cmd = 'cp "{}" "{}"'.format(s, d)
    return_code = os.system(cmd)
    if return_code != 0:
        xlogging.raise_and_logging_error('拷贝文件失败', r'copyfile failed {}'.format(cmd))
    else:
        _logger.info(r'{} ok'.format(cmd))


@xlogging.LockDecorator(_generate_locker)
def _insert_running(task_id):
    if task_id in _running_tasks:
        xlogging.raise_and_logging_error(r'重复调度的任务', r'task_id run twice : {}'.format(task_id))
    else:
        _running_tasks.append(task_id)


@xlogging.LockDecorator(_generate_locker)
def _remove_running(task_id):
    if task_id in _running_tasks:
        _running_tasks.remove(task_id)


def _is_canceled(task_id):
    cluster_task = ClusterBackupTask.objects.get(id=task_id)
    schedule_object = cluster_task.schedule
    if not schedule_object.enabled:
        xlogging.raise_and_logging_error(
            r'用户取消，计划“{}”被禁用'.format(schedule_object.name),
            r'UserCancelCheck _is_canceled : {}'.format(cluster_task.id))
    if schedule_object.deleted:
        xlogging.raise_and_logging_error(
            '用户取消，计划“{}”被删除'.format(schedule_object.name),
            r'UserCancelCheck _is_canceled : {}'.format(cluster_task.id))

    if '"{}"'.format(xdata.CANCEL_TASK_EXT_KEY) in cluster_task.ext_config:
        xlogging.raise_and_logging_error(
            '用户取消，计划“{}”'.format(cluster_task.schedule.name),
            r'UserCancelCheck _is_canceled : {}'.format(cluster_task.id))


class ClusterBackupTaskExecutor(threading.Thread):
    def __init__(self):
        super(ClusterBackupTaskExecutor, self).__init__()
        self.name = r'ClusterBackupTask_'
        self._schedule_id = None
        self._task_id = None
        self._engine = None
        self._book_uuid = None

    def load_from_uuid(self, task_uuid, task_id):
        backend = get_backend()
        with contextlib.closing(backend.get_connection()) as conn:
            book = conn.get_logbook(task_uuid['book_id'])
            flow_detail = book.find(task_uuid['flow_id'])
        self._engine = engines.load_from_detail(flow_detail, backend=backend, engine='serial')
        self._book_uuid = book.uuid
        self.name += r'{} load exist uuid {} {}'.format(task_id, task_uuid['book_id'], task_uuid['flow_id'])
        assert self._task_id is None
        self._task_id = task_id

    @staticmethod
    @xlogging.LockDecorator(_generate_locker)
    def generate_task_object(reason, schedule_object, input_config):
        assert schedule_object.cycle_type != BackupTaskSchedule.CYCLE_CDP

        other_object = ClusterBackupTask.objects.filter(
            finish_datetime__isnull=True, schedule=schedule_object).first()
        if other_object is not None:
            xlogging.raise_and_logging_error(r'计划正在执行中',
                                             r'other_object running : {}'.format(other_object.id),
                                             http_status.HTTP_501_NOT_IMPLEMENTED)

        force_full, disable_optimize = ClusterBackupTaskExecutor._is_force_full_and_disable_optimize(input_config,
                                                                                                     schedule_object,
                                                                                                     reason)
        config_json = json.dumps({
            "input": input_config,
            "force_full": force_full,
            "disable_optimize": disable_optimize,
            "force_store_full": ClusterBackupTaskExecutor._is_force_store_full(input_config, schedule_object)
        })
        return ClusterBackupTask.objects.create(reason=reason, schedule=schedule_object, ext_config=config_json)

    def generate_and_save(self, schedule_object, reason, input_config):
        task_object = self.generate_task_object(reason, schedule_object, input_config)
        HostBackupWorkProcessors.cluster_hosts_log(schedule_object, task_object, HostLog.LOG_CLUSTER_BACKUP_START, **{
            'substage': '集群备份任务：{}, 手动启动：{}'.format(schedule_object.name,
                                                    '是' if reason == BackupTask.REASON_PLAN_MANUAL else '否')
        })

        self.name += r'{}'.format(task_object.id)
        self._task_id = task_object.id
        self._schedule_id = schedule_object.id

        backend = get_backend()
        book = models.LogBook(
            r"{}_{}".format(self.name, datetime.datetime.now().strftime(xdatetime.FORMAT_WITH_SECOND_FOR_PATH)))
        with contextlib.closing(backend.get_connection()) as conn:
            conn.save_logbook(book)

        try:
            self._engine = engines.load_from_factory(create_flow, backend=backend, book=book, engine='serial',
                                                     factory_args=(self.name, self._task_id, book.uuid))
            self._book_uuid = book.uuid

            task_object.task_uuid = json.dumps({'book_id': book.uuid, 'flow_id': self._engine.storage.flow_uuid})
            task_object.save(update_fields=['task_uuid'])
            return task_object
        except Exception as e:
            _logger.error(r'generate_uuid failed {}'.format(e), exc_info=True)
            with contextlib.closing(backend.get_connection()) as conn:
                conn.destroy_logbook(book.uuid)
            raise e

    def start(self):
        if self._engine:
            try:
                _insert_running(self._task_id)
                super().start()
            except Exception as e:
                _remove_running(self._task_id)
                raise e
        else:
            xlogging.raise_and_logging_error('内部异常，无效的调用', r'start without _engine ：{}'.format(self.name),
                                             http_status.HTTP_501_NOT_IMPLEMENTED)

    def run(self):
        try:
            with logging_listener.DynamicLoggingListener(self._engine):
                self._engine.run()
        except Exception as e:
            _logger.error(r'ClusterBackupTaskExecutor run engine {} failed {}'.format(self.name, e), exc_info=True)
            BackupScheduleRetryHandle.modify(ClusterBackupSchedule.objects.get(id=self._schedule_id))
        finally:
            with contextlib.closing(get_backend().get_connection()) as conn:
                conn.destroy_logbook(self._book_uuid)
            _remove_running(self._task_id)
        self._engine = None

    @staticmethod
    def has_error(task_context):
        return task_context['error'] is not None

    @staticmethod
    def _is_force_full_and_disable_optimize(input_config, schedule_object, reason):
        if schedule_object and is_force_full_by_schedule(schedule_object, _logger.info, 2):
            return True, True
        if reason != BackupTask.REASON_PLAN_MANUAL and schedule_object and \
                is_force_full_by_schedule(schedule_object, _logger.info, 1):
            return True, True
        else:
            return is_force_full_by_config(input_config, _logger.info), False

    @staticmethod
    def _is_force_store_full(input_config, schedule_object):
        return is_force_store_full_by_config(input_config, _logger.info)


def create_flow(name, task_id, book_uuid):
    flow = lf.Flow(name).add(
        CBT_StartBackup(name, task_id),
        CBT_BaseBackup(name, task_id),
        CBT_DiffBackup(name, task_id),
        CBT_PreStopCdp(name, task_id),
        CBT_StopCdp(name, task_id),
        CBT_PreSplitCdp(name, task_id),
        CBT_SplitCdp(name, task_id),
        CBT_PreMergeCdp(name, task_id),
        CBT_MergeCdp(name, task_id),
        CBT_PostMergeCdp(name, task_id),
        CBT_PreMergeBackup(name, task_id),
        CBT_MergeBackup(name, task_id),
        CBT_PreCreateHostSnapshot(name, task_id),
        CBT_CreateHostSnapshot(name, task_id),
        CBT_PostCreateHostSnapshot(name, task_id),
        CBT_CleanTemporary(name, task_id),
        CBT_FinishBackup(name, task_id),
    )
    return flow


def _check_run_twice(task_object, step_name):
    task_uuid = json.loads(task_object.task_uuid)
    if 'run_twice' not in task_uuid.keys():
        task_uuid['run_twice'] = dict()
    if task_uuid['run_twice'].get(step_name, None) is not None:
        xlogging.raise_and_logging_error('内部异常，代码2385', '_check_run_twice {} - {} failed'
                                         .format(task_object.id, step_name))
    task_uuid['run_twice'][step_name] = 'r'
    task_object.task_uuid = json.dumps(task_uuid)
    task_object.save(update_fields=['task_uuid'])
    _logger.info(r'_check_run_twice {} - {}'.format(task_object.id, step_name))


# 更新CBTObj的_task_object, _config
def update_cluster_backup_task_and_config_to_CBTObj(CBT_obj, task_id, status_info=None):
    cluster_backup_task = ClusterBackupTask.objects.get(id=task_id)
    CBT_obj._task_object = cluster_backup_task
    CBT_obj._config = json.loads(cluster_backup_task.ext_config)
    if status_info and isinstance(status_info, str):
        cluster_backup_task.run_status(status_info)


class CBT_StartBackup(task.Task):
    default_provides = 'task_context'

    def __init__(self, name, task_id, inject=None):
        super(CBT_StartBackup, self).__init__(r'CBT_Backup {}'.format(name), inject=inject)
        self._task_id = task_id
        self._task_object = ClusterBackupTask.objects.get(id=self._task_id)
        self._config = json.loads(self._task_object.ext_config)
        self._schedule_object = self._task_object.schedule
        self._schedule_config = json.loads(self._schedule_object.ext_config)

    def _get_host_snapshots(self):
        host_snapshots = list()
        for sub_task in self._task_object.sub_tasks.all():
            host_snapshots.append(sub_task.host_snapshot)
        return host_snapshots

    def _get_host_snapshot(self, host_ident):
        for host_snapshot in self._get_host_snapshots():
            if host_snapshot.host.ident == host_ident:
                return host_snapshot
        else:
            xlogging.raise_and_logging_error(r'无效的主机信息', r'CBT_StartBackup invalid host {}'.format(host_ident), 0)

    @staticmethod
    def _get_disk_snapshot_by_disk_guid(host_snapshot_object, disk_guid):
        host_snapshot_ext = json.loads(host_snapshot_object.ext_info)
        for include_range in host_snapshot_ext['include_ranges']:
            if include_range['diskNativeGUID'] == disk_guid:
                return include_range['diskIndex'], include_range['diskIdent'], include_range['diskSnapshot']
        else:
            xlogging.raise_and_logging_error(r'无效的磁盘信息',
                                             r'CBT_StartBackup invalid host_snapshot {} disk_guid {}'.format(
                                                 host_snapshot_object.id, disk_guid), 0)

    def _get_cluster_disk_snapshots(self):
        cluster_disk_snapshots = list()

        for cluster_disk in self._schedule_config['cluster_disks']:
            if len(cluster_disk['map_disks']) < 2:
                _logger.error(r'some mistake len(map_disks) < 2 : {}'.format(cluster_disk))
                continue

            cluster_disk_snapshot = copy.deepcopy(cluster_disk)

            for map_disk in cluster_disk_snapshot['map_disks']:
                host_snapshot = self._get_host_snapshot(map_disk['host_ident'])
                map_disk['disk_index'], map_disk['disk_ident'], map_disk['disk_snapshot_ident'] = \
                    self._get_disk_snapshot_by_disk_guid(host_snapshot, map_disk['disk_guid'])

            cluster_disk_snapshots.append(cluster_disk_snapshot)

        return cluster_disk_snapshots

    @staticmethod
    def _is_exclude_disk_snapshot(host_snapshot_object, disk_snapshot_object):
        host_snapshot_ext = json.loads(host_snapshot_object.ext_info)
        for exclude_range in host_snapshot_ext['exclude_ranges']:
            if exclude_range['type'] != 'disk':
                continue
            if disk_snapshot_object.disk.ident != exclude_range['diskIdent']:
                continue
            return True
        else:
            return False

    def _get_exclude_disk_snapshots(self):
        exclude_disk_snapshots = list()

        for host_snapshot in self._get_host_snapshots():
            host_ident = host_snapshot.host.ident
            for disk_snapshot in list(host_snapshot.disk_snapshots.all()):
                disk_ident = disk_snapshot.disk.ident
                if not self._is_exclude_disk_snapshot(host_snapshot, disk_snapshot):
                    continue
                exclude_disk_snapshots.append({
                    'host_ident': host_ident,
                    'disk_ident': disk_ident,
                    'host_snapshot_id': host_snapshot.id,
                    'disk_snapshot_ident': disk_snapshot.ident,
                    'disk_bytes': disk_snapshot.bytes,
                })

        return exclude_disk_snapshots

    @staticmethod
    def _is_cluster_disk_snapshot(host_ident, disk_ident, cluster_disk_snapshots):
        for cluster_disk_snapshot in cluster_disk_snapshots:
            for map_disk in cluster_disk_snapshot['map_disks']:
                if map_disk['host_ident'] == host_ident and map_disk['disk_ident'] == disk_ident:
                    return True
        return False

    @staticmethod
    def _get_disk_index_by_disk_ident(host_snapshot_object, disk_ident):
        host_snapshot_ext = json.loads(host_snapshot_object.ext_info)
        for include_range in host_snapshot_ext['include_ranges']:
            if include_range['diskIdent'] == disk_ident:
                return include_range['diskIndex']
        else:
            xlogging.raise_and_logging_error(r'无效的磁盘信息',
                                             r'CBT_StartBackup invalid host_snapshot {} disk_ident {}'.format(
                                                 host_snapshot_object.id, disk_ident), 0)

    def _get_normal_disk_snapshots(self, cluster_disk_snapshots):
        normal_disk_snapshots = list()
        for host_snapshot in self._get_host_snapshots():
            host_ident = host_snapshot.host.ident
            for disk_snapshot in list(host_snapshot.disk_snapshots.all()):
                disk_ident = disk_snapshot.disk.ident
                if self._is_exclude_disk_snapshot(host_snapshot, disk_snapshot):
                    continue
                if self._is_cluster_disk_snapshot(host_ident, disk_ident, cluster_disk_snapshots):
                    continue
                normal_disk_snapshots.append({
                    'host_ident': host_ident,
                    'disk_ident': disk_ident,
                    'host_snapshot_id': host_snapshot.id,
                    'disk_snapshot_ident': disk_snapshot.ident,
                    'disk_bytes': disk_snapshot.bytes,
                    'disk_index': self._get_disk_index_by_disk_ident(host_snapshot, disk_ident)
                })

        return normal_disk_snapshots

    @staticmethod
    def _generate_path(cluster_disk_snapshots, task_id):
        for cluster_disk in cluster_disk_snapshots:
            disk_snapshot = DiskSnapshot.objects.get(ident=cluster_disk['map_disks'][0]['disk_snapshot_ident'])
            tmp_dir = os.path.split(disk_snapshot.image_path)[0]
            cluster_disk['disk_index'] = cluster_disk['map_disks'][0]['disk_index']
            cluster_disk['diff_host'] = disk_snapshot.host_snapshot.host.ident
            cluster_disk['diff_image_path'] = os.path.join(tmp_dir,
                                                           r'diff_{}_{}.qcow'.format(task_id, cluster_disk['ident']))
            cluster_disk['diff_image_bytes'] = disk_snapshot.bytes
            cluster_disk['cdp_file_path_pre'] = os.path.join(tmp_dir,
                                                             r'diff_{}_{}'.format(task_id, cluster_disk['ident']))
            for map_disk in cluster_disk['map_disks']:
                disk_snapshot = DiskSnapshot.objects.get(ident=map_disk['disk_snapshot_ident'])
                map_disk['time0_hash_path'] = disk_snapshot.image_path + '_' + disk_snapshot.ident + '.time0hash'

    def _get_disk_guid_array(self, host_ident):
        result = list()
        for cluster_disk in self._schedule_config['cluster_disks']:
            if len(cluster_disk['map_disks']) < 2:
                _logger.error(r'some mistake len(map_disks) < 2 : {}'.format(cluster_disk))
                continue

            for map_disk in cluster_disk['map_disks']:
                if map_disk['host_ident'] == host_ident:
                    result.append(map_disk['disk_guid'])

        return result

    def execute(self, *args, **kwargs):
        update_cluster_backup_task_and_config_to_CBTObj(self, self._task_id)
        lock_info = dict(task_name='cluster_backup_{}'.format(self._task_id), snapshots_idents=list())
        try:
            _check_run_twice(self._task_object, 'CBT_StartBackup')
            force_full = self._config['force_full']
            force_store_full = self._config['force_store_full']
            disable_optimize = self._config.get('disable_optimize', False)
            hosts = self._schedule_object.hosts.all()
            for host in hosts:
                cdp_task_object = CDPTask.objects.create(cluster_task=self._task_object)
                work = HostBackupWorkProcessors(self.name, host, force_full, True, xdata.CDP_MODE_SYN,
                                                cdp_task_object.id,
                                                self._schedule_object.storage_node_ident,
                                                cluster_schedule_object=self._schedule_object,
                                                force_store_full=force_store_full,
                                                disable_optimize=disable_optimize,
                                                force_optimize=True,
                                                )

                cdp_task_object.set_host_snapshot(work.host_snapshot)

                queue(cdp_task_object, work.host_snapshot)

                work.work()  # 创建HostSnapshot 与 DiskSnapshot 数据库对象，发送备份命令

                lock_info['snapshots_idents'].extend(
                    self._lock_disk_snapshots_by_host_snapshot(work.host_snapshot, lock_info['task_name']))

                # reload
            self._task_object = ClusterBackupTask.objects.get(id=self._task_id)
            cluster_disk_snapshots = self._get_cluster_disk_snapshots()
            self._generate_path(cluster_disk_snapshots, self._task_id)
            exclude_disk_snapshots = self._get_exclude_disk_snapshots()
            normal_disk_snapshots = self._get_normal_disk_snapshots(cluster_disk_snapshots)

            task_context = {
                'error': None,
                'cluster_disk_snapshots': cluster_disk_snapshots,
                'exclude_disk_snapshots': exclude_disk_snapshots,
                'normal_disk_snapshots': normal_disk_snapshots,
                'lock_info': lock_info
            }

        except Exception as e:
            _logger.error(r'CBT_Backup failed : {}'.format(e), exc_info=True)
            CBT_StopCdp.unlock_snapshots(lock_info)
            task_context = {
                'error': (r'备份数据失败', r'CBT_Backup failed : {}'.format(e),),
            }

        return task_context

    @staticmethod
    @xlogging.convert_exception_to_value(None)  # 可能会调用2次， 比如备份时候重启服务
    def _lock_disk_snapshots_by_host_snapshot(host_snapshot, task_name):
        snapshots_idents = list()
        for disk_snapshot in host_snapshot.disk_snapshots.all():
            snapshots = CBT_DiffBackup.fetch_all_disk_snapshot_objs(disk_snapshot, None)
            snapshots_idents.extend(
                [DiskSnapshotLocker.get_disk_snapshot_object(snapshot.path, snapshot.snapshot).ident for
                 snapshot in snapshots])
            try:
                DiskSnapshotLocker.lock_files(snapshots, task_name)
            except Exception:
                _logger.warning('lock failed, host_snapshot {} task_name {}'.format(host_snapshot.id, task_name))

        return snapshots_idents


class CBT_BaseBackup(task.Task):
    default_provides = 'task_context'

    def __init__(self, name, task_id, inject=None):
        super(CBT_BaseBackup, self).__init__(r'CBT_BaseBackup {}'.format(name), inject=inject)
        self._task_id = task_id
        self._task_object = ClusterBackupTask.objects.get(id=self._task_id)
        self._config = json.loads(self._task_object.ext_config)
        self._schedule_object = self._task_object.schedule
        self._schedule_config = json.loads(self._schedule_object.ext_config)
        self._sleep = Sleep(self._schedule_object.id, sender=ClusterBackupSchedule)

    def _query_sub_tasks(self):
        sub_tasks = list()
        for sub_task in self._task_object.sub_tasks.all():
            sub_tasks.append({'host_ident': sub_task.host_snapshot.host.ident, 'sub_task_id': sub_task.id})
        return sub_tasks

    def execute(self, task_context, *args, **kwargs):
        update_cluster_backup_task_and_config_to_CBTObj(self, self._task_id)
        try:
            if ClusterBackupTaskExecutor.has_error(task_context):
                return task_context

            _logger.info(r'begin wait backup base step ...')
            unfinished_count = 2
            sub_tasks = self._query_sub_tasks()
            checker = partial(_is_canceled, self._task_id)
            while unfinished_count != 0:
                self._sleep.sleep(30)
                unfinished_count = 0
                for sub_task in sub_tasks:
                    user_cancel_check = CDPTaskWorker.UserCancelCheck(
                        Tokens.get_schedule_obj_from_cdp_task(
                            CDPTask.objects.get(id=sub_task['sub_task_id'])), sub_task['sub_task_id'],
                        checker
                    )
                    if TaskHelper.check_backup_status_in_cdp_task(
                            sub_task['host_ident'], sub_task['sub_task_id'], user_cancel_check):
                        unfinished_count += 1
            _logger.info(r'wait backup base step end')
            task_context['normal_backup_successful'] = True
        except xlogging.BoxDashboardException as bde:
            _logger.error(r'CBT_BaseBackup failed : {} | {}'.format(bde.msg, bde.debug))
            task_context['error'] = (r'备份数据失败' + bde.msg, r'CBT_BaseBackup failed : {}'.format(bde.debug),)
            if bde.http_status == xlogging.ERROR_HTTP_STATUS_NEED_RETRY:
                # 不能立刻发送停止CDP的指令，直接 sleep 一小会儿
                time.sleep(10)
        except Exception as e:
            _logger.error(r'CBT_BaseBackup failed : {}'.format(e), exc_info=True)
            task_context['error'] = (r'备份数据失败', r'CBT_BaseBackup failed : {}'.format(e),)

        return task_context


class CBT_DiffBackup(task.Task):
    default_provides = 'task_context'

    def __init__(self, name, task_id, inject=None):
        super(CBT_DiffBackup, self).__init__(r'CBT_DiffBackup {}'.format(name), inject=inject)
        self._task_id = task_id
        self._task_object = ClusterBackupTask.objects.get(id=self._task_id)
        self._config = json.loads(self._task_object.ext_config)
        self._schedule_object = self._task_object.schedule
        self._schedule_config = json.loads(self._schedule_object.ext_config)
        self._status = r'备份集群差异数据'

    @staticmethod
    def _fetch_snapshot_before_time0(map_disk, time0):
        disk_snapshot = DiskSnapshot.objects.get(ident=map_disk['disk_snapshot_ident'])
        cdp_disk_snapshot_ident, time0_disksnapshot_timestamp = \
            GetDiskSnapshot.query_cdp_disk_snapshot_ident_by_normal_disk_snapshot(
                disk_snapshot, time0.timestamp())
        if cdp_disk_snapshot_ident is None or time0_disksnapshot_timestamp is None:
            _logger.warning(r'not cdp before time0. skip fix hash {} {}'.format(
                map_disk['disk_snapshot_ident'], time0))
            _logger_human.info('  磁盘 {}-{} 在time0 以前没有 CDP 区域'.format(
                map_disk['host_ident'], map_disk['disk_index']))
            return disk_snapshot, None
        else:
            return DiskSnapshot.objects.get(ident=cdp_disk_snapshot_ident), time0_disksnapshot_timestamp

    @staticmethod
    def fetch_all_disk_snapshot_objs(snapshot_object, time0_disksnapshot_timestamp):
        validator_list = [GetSnapshotList.is_disk_snapshot_object_exist,
                          GetSnapshotList.is_disk_snapshot_file_exist]

        disk_snapshots = GetSnapshotList.query_snapshots_by_snapshot_object(
            snapshot_object, validator_list, time0_disksnapshot_timestamp, include_all_node=True)
        if len(disk_snapshots) == 0:
            xlogging.raise_and_logging_error(r'无法访问历史快照文件，请检查存储节点连接状态',
                                             r'{} disk_snapshot_object invalid'.format(
                                                 snapshot_object.ident))
        else:
            return disk_snapshots

    def _wait_all_disk_snapshot_reorganize_hash_file(self, disk_snapshots):
        for disk_snapshot in disk_snapshots:
            _is_canceled(self._task_id)
            while True:
                try:
                    disk_snapshot_obj = DiskSnapshot.objects.get(ident=disk_snapshot.snapshot)
                except DiskSnapshot.DoesNotExist:
                    break  # cdp
                rev = DiskSnapshotHash.reorganize_hash_file_by_disk_snapshot(disk_snapshot_obj)
                if rev == 'failed':
                    xlogging.raise_and_logging_error(r'计算磁盘快照HASH错误',
                                                     r'{} reorganize_hash_file_by_disk_snapshot invalid'.format(
                                                         disk_snapshot))
                elif rev == 'successful':
                    break
                else:
                    pass
                if xlogging.logger_traffic_control.is_logger_print(
                        '_wait_all_disk_snapshot_reorganize_hash_file', disk_snapshot.snapshot):
                    _logger.warning(
                        r'c_wait_all_disk_snapshot_reorganize_hash_file. disk_snapshot {}'.format(
                            disk_snapshot.snapshot))
                time.sleep(0.1)

    def execute(self, task_context, *args, **kwargs):
        HostBackupWorkProcessors.cluster_hosts_log(
            self._schedule_object, self._task_object, HostLog.LOG_CLUSTER_BACKUP_ANALYZE,
            **{'substage': self._status, 'error_occur': ClusterBackupTaskExecutor.has_error(task_context)})
        update_cluster_backup_task_and_config_to_CBTObj(self, self._task_id, status_info=self._status)
        try:
            if ClusterBackupTaskExecutor.has_error(task_context):
                return task_context

            _logger.info(r'begin diff backup step ...')

            datetime_now = datetime.datetime.now()
            task_context['backup_snapshot_time_0'] = datetime_now.strftime(xdatetime.FORMAT_WITH_MICROSECOND)

            _logger_human.info(r'确定time0为 : {}'.format(task_context['backup_snapshot_time_0']))

            for cluster_disk_snapshot in task_context['cluster_disk_snapshots']:
                for map_disk in cluster_disk_snapshot['map_disks']:
                    boxService.box_service.testDisk(map_disk['host_ident'], map_disk['disk_index'], 23, 1)

            time.sleep(3)

            for cluster_disk_snapshot in task_context['cluster_disk_snapshots']:
                _logger_human.info('集群磁盘 {} ：'.format(cluster_disk_snapshot['name']))

                for map_disk in cluster_disk_snapshot['map_disks']:
                    _is_canceled(self._task_id)
                    disk_snapshot_obj, time0_snapshot_timestamp = \
                        self._fetch_snapshot_before_time0(map_disk, datetime_now)

                    disk_snapshots = self.fetch_all_disk_snapshot_objs(disk_snapshot_obj, time0_snapshot_timestamp)

                    self._wait_all_disk_snapshot_reorganize_hash_file(disk_snapshots)
                    disk_snapshots, hash_files = GetSnapshotList.fetch_snapshots_and_hash_files(
                        disk_snapshot_obj, time0_snapshot_timestamp)

                    snapshots = list({'path': o.path, 'ident': o.snapshot} for o in disk_snapshots)
                    _logger_human.info('  磁盘 {}-{} 的快照链为 ： {}'.format(
                        map_disk['host_ident'], map_disk['disk_index'], json.dumps(snapshots)))

                    result_mount_snapshot = json.loads(boxService.box_service.startBackupOptimize({
                        'hash_files': hash_files,
                        'ordered_hash_file': map_disk['time0_hash_path'],
                        'disk_bytes': disk_snapshot_obj.bytes,
                        'include_cdp': True,
                        'snapshots': snapshots,
                    }))
                    result_mount_snapshot['delete_hash'] = False
                    boxService.box_service.stopBackupOptimize([result_mount_snapshot])
                    assert boxService.box_service.isFileExist(map_disk['time0_hash_path']), 'hash path not exists!'

            _is_canceled(self._task_id)
            boxService.box_service.generateClusterDiffImages(task_context['cluster_disk_snapshots'])

            _logger.info(r'diff backup step end')
        except Exception as e:
            _logger.error(r'CBT_DiffBackup failed : {}'.format(e), exc_info=True)
            task_context['error'] = (r'备份数据失败', r'CBT_DiffBackup failed : {}'.format(e),)

        return task_context


class CBT_PreStopCdp(task.Task):
    default_provides = 'task_context'

    def __init__(self, name, task_id, inject=None):
        super(CBT_PreStopCdp, self).__init__(r'CBT_PreStopCdp {}'.format(name), inject=inject)
        self._task_id = task_id
        self._task_object = ClusterBackupTask.objects.get(id=self._task_id)
        self._config = json.loads(self._task_object.ext_config)
        self._schedule_object = self._task_object.schedule
        self._schedule_config = json.loads(self._schedule_object.ext_config)
        self._status = r'准备停止数据采集'

    def execute(self, task_context, *args, **kwargs):
        HostBackupWorkProcessors.cluster_hosts_log(
            self._schedule_object, self._task_object, HostLog.LOG_CLUSTER_BACKUP_ANALYZE,
            **{'substage': self._status, 'error_occur': ClusterBackupTaskExecutor.has_error(task_context)})
        update_cluster_backup_task_and_config_to_CBTObj(self, self._task_id, status_info=self._status)
        try:
            if ClusterBackupTaskExecutor.has_error(task_context):
                return task_context

            _logger.info(r'begin pre stop cdp step ...')

            task_context['backup_snapshot_time'] = datetime.datetime.now().strftime(xdatetime.FORMAT_WITH_MICROSECOND)

            _logger_human.info(r'确定time1为 : {}'.format(task_context['backup_snapshot_time']))

            hosts = self._schedule_object.hosts.all()
            self.check_host_in_cdp(hosts)

            for cluster_disk_snapshot in task_context['cluster_disk_snapshots']:
                for map_disk in cluster_disk_snapshot['map_disks']:
                    boxService.box_service.testDisk(map_disk['host_ident'], map_disk['disk_index'], 23, 1)

            for normal_disk_snapshot in task_context['normal_disk_snapshots']:
                boxService.box_service.testDisk(
                    normal_disk_snapshot['host_ident'], normal_disk_snapshot['disk_index'], 22, 1)

            time.sleep(5)

            self.check_host_in_cdp(hosts)

            _logger.info(r'pre stop cdp step end')
        except Exception as e:
            _logger.error(r'CBT_PreStopCdp failed : {}'.format(e), exc_info=True)
            task_context['error'] = (r'检测数据状态失败', r'CBT_PreStopCdp failed : {}'.format(e),)

        return task_context

    @staticmethod
    def check_host_in_cdp(hosts):
        for host in hosts:
            host_status_list = boxService.box_service.GetStatus(host.ident)
            if 'cdp_syn' not in host_status_list:
                xlogging.raise_and_logging_error(
                    r'CDP状态异常', 'CBT_StopCdp check {} failed {}'.format(host.ident, host_status_list))


class CBT_StopCdp(task.Task):
    default_provides = 'task_context'

    def __init__(self, name, task_id, inject=None):
        super(CBT_StopCdp, self).__init__(r'CBT_StopCdp {}'.format(name), inject=inject)
        self._task_id = task_id
        self._task_object = ClusterBackupTask.objects.get(id=self._task_id)
        self._config = json.loads(self._task_object.ext_config)
        self._schedule_object = self._task_object.schedule
        self._schedule_config = json.loads(self._schedule_object.ext_config)
        self._status = r'停止数据采集'

    def _get_host_snapshots(self):
        host_snapshots = list()
        for sub_task in self._task_object.sub_tasks.all():
            host_snapshots.append(sub_task.host_snapshot)
        return host_snapshots

    @staticmethod
    @xlogging.convert_exception_to_value(None)
    def unlock_snapshots(lock_info):
        while lock_info['snapshots_idents']:
            ident = lock_info['snapshots_idents'].pop()
            disk_snapshot = DiskSnapshot.objects.get(ident=ident)
            DiskSnapshotLocker.unlock_file(disk_snapshot.image_path, ident, lock_info['task_name'])

    def execute(self, task_context, *args, **kwargs):
        HostBackupWorkProcessors.cluster_hosts_log(
            self._schedule_object, self._task_object, HostLog.LOG_CLUSTER_BACKUP_ANALYZE,
            **{'substage': self._status, 'error_occur': ClusterBackupTaskExecutor.has_error(task_context)})
        update_cluster_backup_task_and_config_to_CBTObj(self, self._task_id, status_info=self._status)
        try:
            _logger.info(r'begin stop cdp step ...')

            _flag_file_path = '/dev/shm/pause_cluster_stop_cdp'
            while os.path.isfile(_flag_file_path):
                _logger.info(r'{}  exist. please remove !!!'.format(_flag_file_path))
                time.sleep(60)

            if task_context.get('lock_info', None):
                self.unlock_snapshots(task_context['lock_info'])

            hosts = self._schedule_object.hosts.all()
            for host in hosts:
                try:
                    boxService.box_service.stopCdpStatus(host.ident)
                except Exception as e:
                    _logger.warning(r'CBT_StopCdp call stopCdpStatus {} failed {}'.format(host.ident, e))

            for host_snapshot in self._get_host_snapshots():
                cdp_host_snapshot = host_snapshot.cdp_info
                cdp_host_snapshot.stopped = True
                cdp_host_snapshot.save(update_fields=['stopped'])

            if ClusterBackupTaskExecutor.has_error(task_context):
                for host_snapshot in self._get_host_snapshots():
                    cdp_task_object = self._task_object.sub_tasks.get(host_snapshot=host_snapshot)
                    HostBackupWorkProcessors.deal_cdp_when_backup_failed(host_snapshot, cdp_task_object)
                    force_close_files = list()
                    for disk_snapshot in host_snapshot.disk_snapshots.all():
                        force_close_files.append(disk_snapshot.ident)
                    HostBackupWorkProcessors.forceCloseBackupFiles(force_close_files)
                return task_context

            for host_snapshot in self._get_host_snapshots():
                HostSnapshotFinishHelper.stopBackupOptimize(host_snapshot)
                DiskSnapshotHash.reorganize_hash_file(host_snapshot)
                cdp_task_object = self._task_object.sub_tasks.get(host_snapshot=host_snapshot)
                HostBackupWorkProcessors.deal_cdp_when_backup_ok(host_snapshot, cdp_task_object)

            _logger.info(r'stop cdp step end')
        except Exception as e:
            _logger.error(r'CBT_StopCdp failed : {}'.format(e), exc_info=True)
            task_context['error'] = (r'备份数据失败', r'CBT_StopCdp failed : {}'.format(e),)

        return task_context


class CBT_PreSplitCdp(task.Task):
    default_provides = 'task_context'

    def __init__(self, name, task_id, inject=None):
        super(CBT_PreSplitCdp, self).__init__(r'CBT_PreSplitCdp {}'.format(name), inject=inject)
        self._task_id = task_id
        self._task_object = ClusterBackupTask.objects.get(id=self._task_id)
        self._config = json.loads(self._task_object.ext_config)
        self._schedule_object = self._task_object.schedule
        self._schedule_config = json.loads(self._schedule_object.ext_config)
        self._status = r'分析集群差异数据阶段一'

    def execute(self, task_context, *args, **kwargs):
        HostBackupWorkProcessors.cluster_hosts_log(
            self._schedule_object, self._task_object, HostLog.LOG_CLUSTER_BACKUP_ANALYZE,
            **{'substage': self._status, 'error_occur': ClusterBackupTaskExecutor.has_error(task_context)})
        update_cluster_backup_task_and_config_to_CBTObj(self, self._task_id, status_info=self._status)
        try:
            if ClusterBackupTaskExecutor.has_error(task_context):
                return task_context

            _logger.info(r'begin pre split cdp step ...')

            time_1 = datetime.datetime.strptime(
                task_context['backup_snapshot_time'], xdatetime.FORMAT_WITH_MICROSECOND)
            time_1_timestamp = time_1.timestamp()

            for normal_disk_snapshot in task_context['normal_disk_snapshots']:
                normal_disk_snapshot['cdp_stream'] = list()
                normal_disk_snapshot['split'] = None

                disk_snapshot = DiskSnapshot.objects.get(ident=normal_disk_snapshot['disk_snapshot_ident'])
                dir_path = os.path.split(disk_snapshot.image_path)[0]
                base_disk_snapshot = disk_snapshot

                while disk_snapshot is not None:
                    if base_disk_snapshot != disk_snapshot:
                        assert DiskSnapshot.is_cdp_file(disk_snapshot.image_path)
                        begin, end = boxService.box_service.queryCdpTimestampRange(disk_snapshot.image_path)
                        if begin < time_1_timestamp < end:
                            assert normal_disk_snapshot['split'] is None  # 只会有最多一个需要分割点
                            guid_before = uuid.uuid4().hex
                            guid_after = uuid.uuid4().hex

                            normal_disk_snapshot['split'] = {
                                'split_timestamp': time_1_timestamp,
                                'snapshot_ident': disk_snapshot.ident,
                                'path': disk_snapshot.image_path,
                                'snapshot_before': guid_before,
                                'file_before': os.path.join(dir_path, r'{}.cdp'.format(guid_before)),
                                'snapshot_after': guid_after,
                                'file_after': os.path.join(dir_path, r'{}.cdp'.format(guid_after)),
                                'disk_bytes': disk_snapshot.bytes,
                            }
                            normal_disk_snapshot['cdp_stream'].append({
                                'path': normal_disk_snapshot['split']['file_before'],
                                'snapshot': normal_disk_snapshot['split']['snapshot_before'],
                                'type': 'create',
                            })
                            normal_disk_snapshot['cdp_stream'].append({
                                'path': normal_disk_snapshot['split']['file_after'],
                                'snapshot': normal_disk_snapshot['split']['snapshot_after'],
                                'type': 'create',
                            })
                        else:
                            normal_disk_snapshot['cdp_stream'].append({
                                'path': disk_snapshot.image_path, 'snapshot': disk_snapshot.ident,
                                'type': 'exist',
                            })

                    assert disk_snapshot.child_snapshots.filter(parent_timestamp__isnull=True).count() in (0, 1,)
                    disk_snapshot = disk_snapshot.child_snapshots.filter(parent_timestamp__isnull=True).first()

            _logger.info(r'pre split cdp step end')
        except Exception as e:
            _logger.error(r'CBT_PreSplitCdp failed : {}'.format(e), exc_info=True)
            task_context['error'] = (r'检测数据状态失败', r'CBT_PreSplitCdp failed : {}'.format(e),)

        return task_context


class CBT_SplitCdp(task.Task):
    default_provides = 'task_context'

    def __init__(self, name, task_id, inject=None):
        super(CBT_SplitCdp, self).__init__(r'CBT_SplitCdp {}'.format(name), inject=inject)
        self._task_id = task_id
        self._task_object = ClusterBackupTask.objects.get(id=self._task_id)
        self._config = json.loads(self._task_object.ext_config)
        self._schedule_object = self._task_object.schedule
        self._schedule_config = json.loads(self._schedule_object.ext_config)
        self._status = r'分析集群差异数据阶段二'

    def execute(self, task_context, *args, **kwargs):
        HostBackupWorkProcessors.cluster_hosts_log(
            self._schedule_object, self._task_object, HostLog.LOG_CLUSTER_BACKUP_ANALYZE,
            **{'substage': self._status, 'error_occur': ClusterBackupTaskExecutor.has_error(task_context)})
        update_cluster_backup_task_and_config_to_CBTObj(self, self._task_id, status_info=self._status)
        try:
            if ClusterBackupTaskExecutor.has_error(task_context):
                return task_context

            _logger.info(r'begin split cdp step ...')

            for normal_disk_snapshot in task_context['normal_disk_snapshots']:
                if normal_disk_snapshot['split'] is not None:
                    self.split(normal_disk_snapshot['split'])

                for cdp in normal_disk_snapshot['cdp_stream']:
                    cdp['begin'], cdp['end'] = boxService.box_service.queryCdpTimestampRange(cdp['path'])

            _logger.info(r'split cdp step end')
        except Exception as e:
            _logger.error(r'CBT_SplitCdp failed : {}'.format(e), exc_info=True)
            task_context['error'] = (r'分析数据失败', r'CBT_SplitCdp failed : {}'.format(e),)

        return task_context

    @staticmethod
    def split(split_params):
        timestamp = boxService.box_service.queryCdpTimestamp(split_params['path'], split_params['split_timestamp'],
                                                             mode='forwards')
        boxService.box_service.cutCdpFile(json.dumps({
            'disk_bytes': split_params['disk_bytes'],
            'new_path': split_params['file_before'],
            'path': split_params['path'],
            'range': GetSnapshotList.format_timestamp(None, timestamp),
        }))

        boxService.box_service.cutCdpFile(json.dumps({
            'disk_bytes': split_params['disk_bytes'],
            'new_path': split_params['file_after'],
            'path': split_params['path'],
            'range': GetSnapshotList.format_timestamp(timestamp, None),
        }))


class CBT_PreMergeCdp(task.Task):
    default_provides = 'task_context'

    def __init__(self, name, task_id, inject=None):
        super(CBT_PreMergeCdp, self).__init__(r'CBT_PreMergeCdp {}'.format(name), inject=inject)
        self._task_id = task_id
        self._task_object = ClusterBackupTask.objects.get(id=self._task_id)
        self._config = json.loads(self._task_object.ext_config)
        self._schedule_object = self._task_object.schedule
        self._schedule_config = json.loads(self._schedule_object.ext_config)
        self._status = r'分析集群差异数据阶段三'

    def execute(self, task_context, *args, **kwargs):
        HostBackupWorkProcessors.cluster_hosts_log(
            self._schedule_object, self._task_object, HostLog.LOG_CLUSTER_BACKUP_ANALYZE,
            **{'substage': self._status, 'error_occur': ClusterBackupTaskExecutor.has_error(task_context)})
        update_cluster_backup_task_and_config_to_CBTObj(self, self._task_id, status_info=self._status)
        try:
            if ClusterBackupTaskExecutor.has_error(task_context):
                return task_context

            _logger.info(r'begin pre merge cdp step ...')

            for cluster_disk_snapshot in task_context['cluster_disk_snapshots']:
                _logger_human.info('集群磁盘 {} ：'.format(cluster_disk_snapshot['name']))
                cluster_disk_snapshot['cdp_stream'] = list()
                for map_disk in cluster_disk_snapshot['map_disks']:
                    snapshots = list()
                    disk_snapshot = DiskSnapshot.objects.get(ident=map_disk['disk_snapshot_ident'])
                    base_disk_snapshot = disk_snapshot
                    while disk_snapshot is not None:
                        if base_disk_snapshot != disk_snapshot:
                            assert DiskSnapshot.is_cdp_file(disk_snapshot.image_path)
                            snapshots.append({'path': disk_snapshot.image_path, 'snapshot': disk_snapshot.ident})
                        assert disk_snapshot.child_snapshots.filter(parent_timestamp__isnull=True).count() in (0, 1,)
                        disk_snapshot = disk_snapshot.child_snapshots.filter(parent_timestamp__isnull=True).first()
                    cluster_disk_snapshot['cdp_stream'].append(snapshots)
                    _logger_human.info('  磁盘 {}-{} 的 CDP 区域将要被合并 : {}'.format(
                        map_disk['host_ident'], map_disk['disk_index'], json.dumps(snapshots)))

            _logger.info(r'pre merge cdp step end')
        except Exception as e:
            _logger.error(r'CBT_PreMergeCdp failed : {}'.format(e), exc_info=True)
            task_context['error'] = (r'检测数据状态失败', r'CBT_PreMergeCdp failed : {}'.format(e),)

        return task_context


class CBT_MergeCdp(task.Task):
    default_provides = 'task_context'

    def __init__(self, name, task_id, inject=None):
        super(CBT_MergeCdp, self).__init__(r'CBT_MergeCdp {}'.format(name), inject=inject)
        self._task_id = task_id
        self._task_object = ClusterBackupTask.objects.get(id=self._task_id)
        self._config = json.loads(self._task_object.ext_config)
        self._schedule_object = self._task_object.schedule
        self._schedule_config = json.loads(self._schedule_object.ext_config)
        self._status = r'分析集群差异数据阶段四'

    def execute(self, task_context, *args, **kwargs):
        HostBackupWorkProcessors.cluster_hosts_log(
            self._schedule_object, self._task_object, HostLog.LOG_CLUSTER_BACKUP_ANALYZE,
            **{'substage': self._status, 'error_occur': ClusterBackupTaskExecutor.has_error(task_context)})
        update_cluster_backup_task_and_config_to_CBTObj(self, self._task_id, status_info=self._status)
        try:
            if ClusterBackupTaskExecutor.has_error(task_context):
                return task_context

            _logger.info(r'begin merge cdp step ...')
            _logger_human.info('== 合并 CDP流 ==')

            for cluster_disk_snapshot in task_context['cluster_disk_snapshots']:
                if len(cluster_disk_snapshot['cdp_stream']) < 2:
                    xlogging.raise_and_logging_error(
                        r'无效的快照信息', r'less cdp file : {}'.format(cluster_disk_snapshot))

                cdp_file_path_pre_dir, cdp_file_path_pre_name = \
                    os.path.split(cluster_disk_snapshot['cdp_file_path_pre'])
                for f in boxService.box_service.findFiles(cdp_file_path_pre_name + '_*.cdp', cdp_file_path_pre_dir):
                    boxService.box_service.remove(f, False)

                cluster_disk_snapshot['merge_cdp_stream'] = {
                    'disk_bytes': cluster_disk_snapshot['diff_image_bytes'],
                    'cdp_file_path_pre': cluster_disk_snapshot['cdp_file_path_pre'],
                    'cdp_files': list(),
                }

                for snapshot in cluster_disk_snapshot['cdp_stream']:
                    for s in snapshot:
                        cluster_disk_snapshot['merge_cdp_stream']['cdp_files'].append(s['path'])

                boxService.box_service.mergeCdpFiles(json.dumps(cluster_disk_snapshot['merge_cdp_stream']))

            _logger.info(r'merge cdp step end')
        except Exception as e:
            _logger.error(r'CBT_MergeCdp failed : {}'.format(e), exc_info=True)
            task_context['error'] = (r'分析数据状态失败', r'CBT_MergeCdp failed : {}'.format(e),)

        return task_context


class CBT_PostMergeCdp(task.Task):
    default_provides = 'task_context'

    def __init__(self, name, task_id, inject=None):
        super(CBT_PostMergeCdp, self).__init__(r'CBT_PostMergeCdp {}'.format(name), inject=inject)
        self._task_id = task_id
        self._task_object = ClusterBackupTask.objects.get(id=self._task_id)
        self._config = json.loads(self._task_object.ext_config)
        self._schedule_object = self._task_object.schedule
        self._schedule_config = json.loads(self._schedule_object.ext_config)
        self._status = r'分析集群差异数据阶段五'

    def execute(self, task_context, *args, **kwargs):
        HostBackupWorkProcessors.cluster_hosts_log(
            self._schedule_object, self._task_object, HostLog.LOG_CLUSTER_BACKUP_ANALYZE,
            **{'substage': self._status, 'error_occur': ClusterBackupTaskExecutor.has_error(task_context)})
        update_cluster_backup_task_and_config_to_CBTObj(self, self._task_id, status_info=self._status)
        try:
            if ClusterBackupTaskExecutor.has_error(task_context):
                return task_context

            _logger.info(r'begin post merge cdp step ...')

            time_0 = datetime.datetime.strptime(
                task_context['backup_snapshot_time_0'], xdatetime.FORMAT_WITH_MICROSECOND)
            time_0_timestamp = time_0.timestamp()
            time_1 = datetime.datetime.strptime(
                task_context['backup_snapshot_time'], xdatetime.FORMAT_WITH_MICROSECOND)
            time_1_timestamp = time_1.timestamp()

            for cluster_disk_snapshot in task_context['cluster_disk_snapshots']:
                _logger_human.info('集群磁盘 {} ：'.format(cluster_disk_snapshot['name']))
                dir_path, file_pre = os.path.split(cluster_disk_snapshot['merge_cdp_stream']['cdp_file_path_pre'])
                self.clean_splited_cdps(dir_path, file_pre)
                cluster_disk_snapshot['cdps'] = self.get_all_merged_cdps(file_pre, dir_path)
                _logger_human.info('  合并后的CDP流 {} ：'.format(cluster_disk_snapshot['cdps']))
                self.split_cdp(
                    cluster_disk_snapshot['cdps'], time_0_timestamp, cluster_disk_snapshot['diff_image_bytes'])
                self.split_cdp(
                    cluster_disk_snapshot['cdps'], time_1_timestamp, cluster_disk_snapshot['diff_image_bytes'])
                cluster_disk_snapshot['cdps'].sort(key=lambda x: x['begin'])
                _logger_human.info('  切分了time0与time1的CDP流 {} ：'.format(cluster_disk_snapshot['cdps']))

            _logger.info(r'post merge cdp step end')
        except Exception as e:
            _logger.error(r'CBT_PostMergeCdp failed : {}'.format(e), exc_info=True)
            task_context['error'] = (r'获取数据状态失败', r'CBT_PostMergeCdp failed : {}'.format(e),)

        return task_context

    @staticmethod
    def get_all_merged_cdps(file_pre, dir_path):
        cdp_files = boxService.box_service.findFiles(r'{}_*.cdp'.format(file_pre), dir_path)
        result = list()
        for cdp_file in cdp_files:
            begin, end = boxService.box_service.queryCdpTimestampRange(cdp_file)
            result.append({'begin': begin, 'end': end, 'path': cdp_file})
        return result

    @staticmethod
    def split_cdp(cdps, timestamp, disk_bytes):
        for cdp in cdps:
            if cdp['begin'] < timestamp < cdp['end']:
                splite_cdp_item = cdp
                break
        else:
            _logger.info(r'not split cdp : {} {}'.format(timestamp, cdps))
            return

        cdps.remove(splite_cdp_item)
        file_pre_path = splite_cdp_item['path'][:-4]

        file_before_timestamp_path = r'{}_before_{:f}.cdp'.format(file_pre_path, timestamp)
        modified_timestamp = boxService.box_service.queryCdpTimestamp(splite_cdp_item['path'], timestamp, 'forwards')
        file_before_range = GetSnapshotList.format_timestamp(None, modified_timestamp)
        CBT_PostMergeCdp.split_cdp_file(cdps, disk_bytes, file_before_range, file_before_timestamp_path,
                                        splite_cdp_item['path'])

        file_after_timestamp_path = r'{}_after_{:f}.cdp'.format(file_pre_path, timestamp)
        file_after_range = GetSnapshotList.format_timestamp(modified_timestamp, None)
        CBT_PostMergeCdp.split_cdp_file(cdps, disk_bytes, file_after_range, file_after_timestamp_path,
                                        splite_cdp_item['path'])

    @staticmethod
    def split_cdp_file(cdps, disk_bytes, file_range, file_new_path, file_path):
        boxService.box_service.cutCdpFile(json.dumps({
            'disk_bytes': disk_bytes,
            'new_path': file_new_path,
            'path': file_path,
            'range': file_range,
        }))
        begin, end = boxService.box_service.queryCdpTimestampRange(file_new_path)
        cdps.append({'begin': begin, 'end': end, 'path': file_new_path})

    @staticmethod
    def clean_splited_cdps(dir_path, file_pre):
        cdp_files = boxService.box_service.findFiles(r'{}_*_before_*.cdp'.format(file_pre), dir_path)
        for cdp_file in cdp_files:
            boxService.box_service.remove(cdp_file, False)
        cdp_files = boxService.box_service.findFiles(r'{}_*_after_*.cdp'.format(file_pre), dir_path)
        for cdp_file in cdp_files:
            boxService.box_service.remove(cdp_file, False)


class CBT_PreMergeBackup(task.Task):
    default_provides = 'task_context'

    def __init__(self, name, task_id, inject=None):
        super(CBT_PreMergeBackup, self).__init__(r'CBT_PreMergeBackup {}'.format(name), inject=inject)
        self._task_id = task_id
        self._task_object = ClusterBackupTask.objects.get(id=self._task_id)
        self._config = json.loads(self._task_object.ext_config)
        self._schedule_object = self._task_object.schedule
        self._schedule_config = json.loads(self._schedule_object.ext_config)
        self._status = r'分析集群差异数据阶段六'

    def execute(self, task_context, *args, **kwargs):
        HostBackupWorkProcessors.cluster_hosts_log(
            self._schedule_object, self._task_object, HostLog.LOG_CLUSTER_BACKUP_ANALYZE,
            **{'substage': self._status, 'error_occur': ClusterBackupTaskExecutor.has_error(task_context)})
        update_cluster_backup_task_and_config_to_CBTObj(self, self._task_id, status_info=self._status)
        try:
            if ClusterBackupTaskExecutor.has_error(task_context):
                return task_context

            _logger.info(r'begin pre merge backup step ...')

            _logger_human.info('== 重组快照链 ==')

            time_0 = datetime.datetime.strptime(
                task_context['backup_snapshot_time_0'], xdatetime.FORMAT_WITH_MICROSECOND)
            time_0_timestamp = time_0.timestamp()
            time_1 = datetime.datetime.strptime(
                task_context['backup_snapshot_time'], xdatetime.FORMAT_WITH_MICROSECOND)
            time_1_timestamp = time_1.timestamp()

            task_context['merge_tasks'] = dict()

            for cluster_disk_snapshot in task_context['cluster_disk_snapshots']:
                _logger_human.info('集群磁盘 {} ：'.format(cluster_disk_snapshot['name']))
                task_time_0 = list()
                task_diff = list()
                task_time_1 = list()
                task_tail = list()

                for map_disk in cluster_disk_snapshot['map_disks']:
                    last_disk_snapshot_ident = map_disk['disk_snapshot_ident']
                    dir_path = self.get_dir_path(last_disk_snapshot_ident)
                    map_disk['new_disk_snapshots'] = list()

                    # 拷贝time0以前的数据
                    for cdp in cluster_disk_snapshot['cdps']:
                        if time_0_timestamp <= cdp['begin']:
                            break

                        guid = uuid.uuid4().hex
                        task_time_0_item = {
                            'parent_disk_snapshot_ident': last_disk_snapshot_ident,
                            'disk_snapshot_ident': guid,
                            'image_path': os.path.join(dir_path, r'{}.cdp'.format(guid)),
                            'cdp_file': cdp['path'],
                            'begin': cdp['begin'],
                            'end': cdp['end'],
                        }
                        last_disk_snapshot_ident = task_time_0_item['disk_snapshot_ident']
                        task_time_0.append(task_time_0_item)
                        map_disk['new_disk_snapshots'].append(task_time_0_item)

                    # 拷贝diff数据
                    guid = uuid.uuid4().hex
                    task_diff_item = {
                        'parent_disk_snapshot_ident': last_disk_snapshot_ident,
                        'disk_snapshot_ident': guid,
                        'image_path': os.path.join(dir_path, r'{}.qcow'.format(guid)),
                        'image_path_map': os.path.join(dir_path, r'{}.qcow_diff.map'.format(guid)),
                        'image_path_hash': os.path.join(dir_path, r'{}.qcow_diff.hash'.format(guid)),
                        'qcow_file': cluster_disk_snapshot['diff_image_path'],
                        'qcow_file_map': cluster_disk_snapshot['diff_image_path'] + '_diff.map',
                        'qcow_file_hash': cluster_disk_snapshot['diff_image_path'] + '_diff.hash',
                        'disk_bytes': cluster_disk_snapshot['diff_image_bytes'],
                    }
                    last_disk_snapshot_ident = task_diff_item['disk_snapshot_ident']
                    task_diff.append(task_diff_item)
                    map_disk['new_disk_snapshots'].append(task_diff_item)

                    # 拷贝time0-time1数据
                    for cdp in cluster_disk_snapshot['cdps']:
                        if time_1_timestamp <= cdp['begin']:
                            break
                        elif cdp['begin'] < time_0_timestamp:
                            continue

                        guid = uuid.uuid4().hex
                        task_time_1_item = {
                            'parent_disk_snapshot_ident': last_disk_snapshot_ident,
                            'disk_snapshot_ident': guid,
                            'image_path': os.path.join(dir_path, r'{}.cdp'.format(guid)),
                            'cdp_file': cdp['path'],
                            'begin': cdp['begin'],
                            'end': cdp['end'],
                        }
                        last_disk_snapshot_ident = task_time_1_item['disk_snapshot_ident']
                        task_time_1.append(task_time_1_item)
                        map_disk['new_disk_snapshots'].append(task_time_1_item)

                    # 拷贝尾部数据
                    for cdp in cluster_disk_snapshot['cdps']:
                        if cdp['begin'] < time_1_timestamp:
                            continue

                        guid = uuid.uuid4().hex
                        task_tail_item = {
                            'parent_disk_snapshot_ident': last_disk_snapshot_ident,
                            'disk_snapshot_ident': guid,
                            'image_path': os.path.join(dir_path, r'{}.cdp'.format(guid)),
                            'cdp_file': cdp['path'],
                            'begin': cdp['begin'],
                            'end': cdp['end'],
                        }
                        last_disk_snapshot_ident = task_tail_item['disk_snapshot_ident']
                        task_tail.append(task_tail_item)
                        map_disk['new_disk_snapshots'].append(task_tail_item)

                    _logger_human.info('  磁盘 {}-{} 重组后的依赖链 : {}'.format(
                        map_disk['host_ident'], map_disk['disk_index'],
                        json.dumps(list(x['image_path'] for x in map_disk['new_disk_snapshots']))))

                task_context['merge_tasks'][cluster_disk_snapshot['ident']] = {
                    'task_time_0': task_time_0,
                    'task_diff': task_diff,
                    'task_time_1': task_time_1,
                    'task_tail': task_tail,
                }

            _logger.info(r'pre merge backup step end')
        except Exception as e:
            _logger.error(r'CBT_PreMergeBackup failed : {}'.format(e), exc_info=True)
            task_context['error'] = (r'分析数据状态失败', r'CBT_PreMergeBackup failed : {}'.format(e),)

        return task_context

    @staticmethod
    def get_dir_path(disk_snapshot_ident):
        disk_snapshot_object = DiskSnapshot.objects.get(ident=disk_snapshot_ident)
        return os.path.split(disk_snapshot_object.image_path)[0]


class CBT_MergeBackup(task.Task):
    default_provides = 'task_context'

    def __init__(self, name, task_id, inject=None):
        super(CBT_MergeBackup, self).__init__(r'CBT_MergeBackup {}'.format(name), inject=inject)
        self._task_id = task_id
        self._task_object = ClusterBackupTask.objects.get(id=self._task_id)
        self._config = json.loads(self._task_object.ext_config)
        self._schedule_object = self._task_object.schedule
        self._schedule_config = json.loads(self._schedule_object.ext_config)
        self._status = r'分析集群差异数据阶段七'

    def execute(self, task_context, *args, **kwargs):
        HostBackupWorkProcessors.cluster_hosts_log(
            self._schedule_object, self._task_object, HostLog.LOG_CLUSTER_BACKUP_ANALYZE,
            **{'substage': self._status, 'error_occur': ClusterBackupTaskExecutor.has_error(task_context)})
        update_cluster_backup_task_and_config_to_CBTObj(self, self._task_id, status_info=self._status)
        try:
            if ClusterBackupTaskExecutor.has_error(task_context):
                return task_context

            _logger.info(r'begin merge backup step ... {}'.format(task_context['merge_tasks']))

            for ident in task_context['merge_tasks'].keys():
                for task_time_0 in task_context['merge_tasks'][ident]['task_time_0']:
                    self.do_task_time_0(task_time_0)

                for task_diff in task_context['merge_tasks'][ident]['task_diff']:
                    self.do_task_diff(task_diff)

                for task_time_1 in task_context['merge_tasks'][ident]['task_time_1']:
                    self.do_task_time_1(task_time_1)

                for task_tail in task_context['merge_tasks'][ident]['task_tail']:
                    self.do_task_tail(task_tail)

            _logger.info(r'merge backup step end')
        except Exception as e:
            _logger.error(r'CBT_MergeBackup failed : {}'.format(e), exc_info=True)
            task_context['error'] = (r'合并数据失败', r'CBT_MergeBackup failed : {}'.format(e),)

        return task_context

    @staticmethod
    def do_task_time_0(task_time_0):
        _logger.info('do_task_time_0 begin : {} -> {}'.format(task_time_0['cdp_file'], task_time_0['image_path']))
        boxService.box_service.remove(task_time_0['image_path'], False)
        copyfile(task_time_0['cdp_file'], task_time_0['image_path'])
        _logger.info('do_task_time_0 end : {} -> {}'.format(task_time_0['cdp_file'], task_time_0['image_path']))

    @staticmethod
    def do_task_diff(task_diff):
        _logger.info('do_task_diff begin : {}'.format(task_diff))
        boxService.box_service.remove(task_diff['image_path'], False)
        boxService.box_service.remove(task_diff['image_path_map'], False)
        boxService.box_service.remove(task_diff['image_path_hash'], False)
        copyfile(task_diff['qcow_file'], task_diff['image_path'])
        if boxService.box_service.isFileExist(task_diff['qcow_file_map']):
            copyfile(task_diff['qcow_file_map'], task_diff['image_path_map'])
        else:
            _logger.warning(r'diff qcow no map file : {}'.format(task_diff['qcow_file_map']))
        if boxService.box_service.isFileExist(task_diff['qcow_file_hash']):
            copyfile(task_diff['qcow_file_hash'], task_diff['image_path_hash'])
        else:
            _logger.warning(r'diff qcow no hash file : {}'.format(task_diff['qcow_file_hash']))
        boxService.box_service.renameSnapshot(
            task_diff['image_path'], 'diff', task_diff['disk_snapshot_ident'], task_diff['disk_bytes'])
        _logger.info('do_task_diff end : {}'.format(task_diff))

    @staticmethod
    def do_task_time_1(task_time_1):
        _logger.info('do_task_time_1 begin : {} -> {}'.format(task_time_1['cdp_file'], task_time_1['image_path']))
        boxService.box_service.remove(task_time_1['image_path'], False)
        copyfile(task_time_1['cdp_file'], task_time_1['image_path'])
        _logger.info('do_task_time_1 end : {} -> {}'.format(task_time_1['cdp_file'], task_time_1['image_path']))

    @staticmethod
    def do_task_tail(task_tail):
        _logger.info('do_task_tail begin : {} -> {}'.format(task_tail['cdp_file'], task_tail['image_path']))
        boxService.box_service.remove(task_tail['image_path'], False)
        copyfile(task_tail['cdp_file'], task_tail['image_path'])
        _logger.info('do_task_tail end : {} -> {}'.format(task_tail['cdp_file'], task_tail['image_path']))


class CBT_PreCreateHostSnapshot(task.Task):
    default_provides = 'task_context'

    def __init__(self, name, task_id, inject=None):
        super(CBT_PreCreateHostSnapshot, self).__init__(r'CBT_PreCreateHostSnapshot {}'.format(name), inject=inject)
        self._task_id = task_id
        self._task_object = ClusterBackupTask.objects.get(id=self._task_id)
        self._config = json.loads(self._task_object.ext_config)
        self._schedule_object = self._task_object.schedule
        self._schedule_config = json.loads(self._schedule_object.ext_config)
        self._status = r'准备创建集群快照'

    def execute(self, task_context, *args, **kwargs):
        HostBackupWorkProcessors.cluster_hosts_log(
            self._schedule_object, self._task_object, HostLog.LOG_CLUSTER_BACKUP_SNAPSHOT,
            **{'substage': self._status, 'error_occur': ClusterBackupTaskExecutor.has_error(task_context)})
        update_cluster_backup_task_and_config_to_CBTObj(self, self._task_id, status_info=self._status)
        try:
            if ClusterBackupTaskExecutor.has_error(task_context):
                return task_context

            _logger.info(r'begin pre create hostsnapshot step ...')

            time_0 = datetime.datetime.strptime(
                task_context['backup_snapshot_time_0'], xdatetime.FORMAT_WITH_MICROSECOND)
            time_0_timestamp = time_0.timestamp()
            time_1 = datetime.datetime.strptime(
                task_context['backup_snapshot_time'], xdatetime.FORMAT_WITH_MICROSECOND)
            time_1_timestamp = time_1.timestamp()

            task_context['create_host_snapshot_tasks'] = list()

            for cdp_task_object in self._task_object.sub_tasks.all():
                base_host_snapshot_object = cdp_task_object.host_snapshot

                create_host_snapshot_task = {
                    'cdp_task_id': cdp_task_object.id,
                    'host_ident': base_host_snapshot_object.host.ident,
                    'base_host_snapshot': {
                        'id': base_host_snapshot_object.id,
                    },
                    'disks': dict(),
                    'diff_host_snapshot': dict(),
                    'snapshot_host_snapshot': dict(),
                    'next_host_snapshot': dict(),
                }

                task_context['create_host_snapshot_tasks'].append(create_host_snapshot_task)

                for base_disk_snapshot in base_host_snapshot_object.disk_snapshots.all():
                    disk_task = {
                        'disk_ident': base_disk_snapshot.disk.ident,
                        'normal_host_snapshot_ident': base_disk_snapshot.ident,
                        'type': 'unknown',
                        'sub_tasks': list(),
                    }
                    create_host_snapshot_task['disks'][disk_task['disk_ident']] = disk_task

                    data = xlogging.DataHolder()
                    if self._is_exclude_disk(base_disk_snapshot.ident, task_context):
                        # 排除的磁盘，整理后为 BASE->snapshot->NEXT
                        disk_task['type'] = 'exclude'
                        disk_task['qcow_image_path'] = base_disk_snapshot.image_path
                        disk_task['qcow_base_ident'] = base_disk_snapshot.ident
                        disk_task['snapshot_ident'] = uuid.uuid4().hex
                        disk_task['next_ident'] = uuid.uuid4().hex
                        disk_task['original_cdp_token'] = base_disk_snapshot.cdp_token.token
                        disk_task['base_cdp_token'] = uuid.uuid4().hex
                        disk_task['sub_tasks'].append({
                            'type': 'create_disk_snapshot_object',
                            'parent_disk_snapshot_ident': disk_task['qcow_base_ident'],
                            'image_path': disk_task['qcow_image_path'],
                            'new_ident': disk_task['snapshot_ident'],
                            'step': 'snapshot',
                        })
                        disk_task['sub_tasks'].append({
                            'type': 'create_disk_snapshot_object',
                            'parent_disk_snapshot_ident': disk_task['snapshot_ident'],
                            'image_path': disk_task['qcow_image_path'],
                            'new_ident': disk_task['next_ident'],
                            'step': 'next',
                        })
                    elif (data.set(self._is_normal_disk(base_disk_snapshot.ident, task_context))) is not None:
                        # 没有集群的普通磁盘，整理后为 BASE->cdp1->snapshot->NEXT->cdp2
                        disk_task['type'] = 'normal'
                        disk_task['qcow_image_path'] = os.path.join(
                            os.path.split(base_disk_snapshot.image_path)[0], r'{}.qcow'.format(uuid.uuid4().hex))
                        disk_task['snapshot_ident'] = None
                        disk_task['next_ident'] = None
                        disk_task['snapshot_parent_ident'] = base_disk_snapshot.ident
                        disk_task['original_cdp_token'] = base_disk_snapshot.cdp_token.token
                        disk_task['base_cdp_token'] = uuid.uuid4().hex

                        if data.value['split'] is not None:
                            disk_task['need_remove_disk_snapshot_ident'] = data.value['split']['snapshot_ident']
                        else:
                            disk_task['need_remove_disk_snapshot_ident'] = None

                        last_disk_snapshot_ident = base_disk_snapshot.ident
                        for cdp_object in data.value['cdp_stream']:
                            if cdp_object['end'] <= time_1_timestamp:
                                # 属于cdp1区域
                                if cdp_object['type'] == 'exist':
                                    # 无需修正
                                    last_disk_snapshot_ident = cdp_object['snapshot']
                                else:
                                    # 新建数据库对象
                                    disk_task['sub_tasks'].append({
                                        'type': 'create_disk_snapshot_object',
                                        'parent_disk_snapshot_ident': last_disk_snapshot_ident,
                                        'image_path': cdp_object['path'],
                                        'new_ident': cdp_object['snapshot'],
                                        'host_snapshot': 'base',
                                        'cdp_token': disk_task['base_cdp_token'],
                                        'first_timestamp': cdp_object['begin'],
                                        'last_timestamp': cdp_object['end'],
                                    })
                                    last_disk_snapshot_ident = cdp_object['snapshot']
                                disk_task['snapshot_parent_ident'] = last_disk_snapshot_ident
                            else:
                                # 属于cdp2区域，逻辑上一定有cdp2区域
                                if disk_task['snapshot_ident'] is None:
                                    # 第一个cdp文件，需要创建基础点
                                    disk_task['snapshot_ident'] = uuid.uuid4().hex
                                    disk_task['next_ident'] = uuid.uuid4().hex
                                    disk_task['sub_tasks'].append({
                                        'type': 'create_disk_snapshot_object',
                                        'parent_disk_snapshot_ident': last_disk_snapshot_ident,
                                        'image_path': disk_task['qcow_image_path'],
                                        'new_ident': disk_task['snapshot_ident'],
                                        'step': 'snapshot',
                                    })
                                    disk_task['sub_tasks'].append({
                                        'type': 'create_disk_snapshot_object',
                                        'parent_disk_snapshot_ident': disk_task['snapshot_ident'],
                                        'image_path': disk_task['qcow_image_path'],
                                        'new_ident': disk_task['next_ident'],
                                        'step': 'next',
                                    })
                                    last_disk_snapshot_ident = disk_task['next_ident']

                                disk_task['sub_tasks'].append({
                                    'type':
                                        'alter_disk_snapshot_object' if cdp_object['type'] == 'exist' else
                                        'create_disk_snapshot_object',
                                    'parent_disk_snapshot_ident': last_disk_snapshot_ident,
                                    'image_path': cdp_object['path'],
                                    'new_ident': cdp_object['snapshot'],
                                    'host_snapshot': 'next',
                                    'cdp_token': disk_task['original_cdp_token'],
                                    'first_timestamp': cdp_object['begin'],
                                    'last_timestamp': cdp_object['end'],
                                })
                                last_disk_snapshot_ident = cdp_object['snapshot']
                    elif (data.set(self._is_cluster_disk(create_host_snapshot_task['host_ident'],
                                                         disk_task['disk_ident'], task_context))) is not None:
                        # 集群磁盘，整理后为 BASE->cdp1->DIFF->cdp2->snapshot->NEXT->cdp3
                        disk_task['type'] = 'cluster'
                        disk_task['qcow_image_path'] = os.path.join(
                            os.path.split(base_disk_snapshot.image_path)[0], r'{}.qcow'.format(uuid.uuid4().hex))
                        disk_task['snapshot_ident'] = None
                        disk_task['next_ident'] = None
                        disk_task['snapshot_parent_ident'] = base_disk_snapshot.ident
                        disk_task['original_cdp_token'] = base_disk_snapshot.cdp_token.token
                        disk_task['base_cdp_token'] = uuid.uuid4().hex
                        disk_task['diff_cdp_token'] = uuid.uuid4().hex

                        disk_task['need_remove_disk_snapshot_ident_array'] = list()
                        disk_snapshot = base_disk_snapshot
                        while disk_snapshot is not None:
                            if DiskSnapshot.is_cdp_file(disk_snapshot.image_path):
                                disk_task['need_remove_disk_snapshot_ident_array'].append(disk_snapshot.ident)
                            assert disk_snapshot.child_snapshots.filter(
                                parent_timestamp__isnull=True).count() in (0, 1,)
                            disk_snapshot = disk_snapshot.child_snapshots.filter(parent_timestamp__isnull=True).first()

                        last_disk_snapshot_ident = base_disk_snapshot.ident
                        for new_disk_snapshot in data.value[0]['new_disk_snapshots']:  # map_disk['new_disk_snapshots']
                            if DiskSnapshot.is_cdp_file(new_disk_snapshot['image_path']) and \
                                    (new_disk_snapshot['end'] <= time_1_timestamp):
                                # cdp1 or cdp2 区域
                                base_or_diff = 'base' if new_disk_snapshot['end'] <= time_0_timestamp else 'diff'
                                disk_task['sub_tasks'].append({
                                    'type': 'create_disk_snapshot_object',
                                    'parent_disk_snapshot_ident': last_disk_snapshot_ident,
                                    'image_path': new_disk_snapshot['image_path'],
                                    'new_ident': new_disk_snapshot['disk_snapshot_ident'],
                                    'host_snapshot': base_or_diff,
                                    'cdp_token':
                                        disk_task['base_cdp_token'] if base_or_diff == 'base'
                                        else disk_task['diff_cdp_token'],
                                    'first_timestamp': new_disk_snapshot['begin'],
                                    'last_timestamp': new_disk_snapshot['end'],
                                })
                                last_disk_snapshot_ident = new_disk_snapshot['disk_snapshot_ident']
                                if base_or_diff == 'base':
                                    disk_task['snapshot_parent_ident'] = last_disk_snapshot_ident
                            elif not DiskSnapshot.is_cdp_file(new_disk_snapshot['image_path']):
                                # diff 点
                                disk_task['sub_tasks'].append({
                                    'type': 'create_disk_snapshot_object',
                                    'parent_disk_snapshot_ident': last_disk_snapshot_ident,
                                    'image_path': new_disk_snapshot['image_path'],
                                    'new_ident': new_disk_snapshot['disk_snapshot_ident'],
                                    'step': 'diff',
                                })
                                last_disk_snapshot_ident = new_disk_snapshot['disk_snapshot_ident']
                            else:
                                # cdp3 区域，逻辑上一定有这块区域
                                assert DiskSnapshot.is_cdp_file(new_disk_snapshot['image_path'])
                                assert new_disk_snapshot['begin'] >= time_1_timestamp
                                if disk_task['snapshot_ident'] is None:
                                    # 创建可见快照点与下次备份需要的CDP数据
                                    disk_task['snapshot_ident'] = uuid.uuid4().hex
                                    disk_task['next_ident'] = uuid.uuid4().hex
                                    disk_task['sub_tasks'].append({
                                        'type': 'create_disk_snapshot_object',
                                        'parent_disk_snapshot_ident': last_disk_snapshot_ident,
                                        'image_path': disk_task['qcow_image_path'],
                                        'new_ident': disk_task['snapshot_ident'],
                                        'step': 'snapshot',
                                    })
                                    disk_task['sub_tasks'].append({
                                        'type': 'create_disk_snapshot_object',
                                        'parent_disk_snapshot_ident': disk_task['snapshot_ident'],
                                        'image_path': disk_task['qcow_image_path'],
                                        'new_ident': disk_task['next_ident'],
                                        'step': 'next',
                                    })
                                    last_disk_snapshot_ident = disk_task['next_ident']

                                disk_task['sub_tasks'].append({
                                    'type': 'create_disk_snapshot_object',
                                    'parent_disk_snapshot_ident': last_disk_snapshot_ident,
                                    'image_path': new_disk_snapshot['image_path'],
                                    'new_ident': new_disk_snapshot['disk_snapshot_ident'],
                                    'host_snapshot': 'next',
                                    'cdp_token': disk_task['original_cdp_token'],
                                    'first_timestamp': new_disk_snapshot['begin'],
                                    'last_timestamp': new_disk_snapshot['end'],
                                })
                                last_disk_snapshot_ident = new_disk_snapshot['disk_snapshot_ident']
                    else:
                        xlogging.raise_and_logging_error(
                            r'内部异常，代码2386', r'CBT_PreCreateHostSnapshot unknown base_disk_snapshot : {}'
                                .format(base_disk_snapshot.ident))

            _logger.info(r'pre create hostsnapshot step end')

        except Exception as e:
            _logger.error(r'CBT_PreCreateHostSnapshot failed : {}'.format(e), exc_info=True)
            task_context['error'] = (r'合并数据信息失败', r'CBT_PreCreateHostSnapshot failed : {}'.format(e),)

        return task_context

    @staticmethod
    def _is_exclude_disk(disk_snapshot_ident, task_context):
        for exclude_disk in task_context['exclude_disk_snapshots']:
            if exclude_disk['disk_snapshot_ident'] == disk_snapshot_ident:
                return True
        else:
            return False

    @staticmethod
    def _is_normal_disk(disk_snapshot_ident, task_context):
        for normal_disk in task_context['normal_disk_snapshots']:
            if normal_disk['disk_snapshot_ident'] == disk_snapshot_ident:
                return normal_disk
        else:
            return None

    @staticmethod
    def _is_cluster_disk(host_ident, disk_ident, task_context):
        for cluster_disk_snapshot in task_context['cluster_disk_snapshots']:
            for map_disk in cluster_disk_snapshot['map_disks']:
                if map_disk['host_ident'] == host_ident and map_disk['disk_ident'] == disk_ident:
                    return map_disk, cluster_disk_snapshot
        return None


class CBT_CreateHostSnapshot(task.Task):
    default_provides = 'task_context'

    def __init__(self, name, task_id, inject=None):
        super(CBT_CreateHostSnapshot, self).__init__(r'CBT_CreateHostSnapshot {}'.format(name), inject=inject)
        self._task_id = task_id
        self._task_object = ClusterBackupTask.objects.get(id=self._task_id)
        self._config = json.loads(self._task_object.ext_config)
        self._schedule_object = self._task_object.schedule
        self._schedule_config = json.loads(self._schedule_object.ext_config)
        self._status = r'合并集群数据'

    def execute(self, task_context, *args, **kwargs):
        HostBackupWorkProcessors.cluster_hosts_log(
            self._schedule_object, self._task_object, HostLog.LOG_CLUSTER_BACKUP_SNAPSHOT,
            **{'substage': self._status, 'error_occur': ClusterBackupTaskExecutor.has_error(task_context)})
        update_cluster_backup_task_and_config_to_CBTObj(self, self._task_id, status_info=self._status)
        try:
            if ClusterBackupTaskExecutor.has_error(task_context):
                return task_context

            _check_run_twice(self._task_object, 'CBT_CreateHostSnapshot')

            _logger.info(r'begin create host snapshot step ...')

            for create_host_snapshot_task in task_context['create_host_snapshot_tasks']:
                # 处理原始 cdp Token
                self._deal_original_cdp_token(create_host_snapshot_task)
                # 处理 cdp in Base
                self._deal_cdp_in_base(create_host_snapshot_task)
                # 处理 Diff
                self._deal_diff(create_host_snapshot_task, task_context)
                # 处理 cdp in Diff
                self._deal_cdp_in_diff(create_host_snapshot_task)
                # 处理 snapshot 与 Next 的qcow文件
                self._deal_snapshot_and_next_file(create_host_snapshot_task)
                # 处理 snapshot 与 Next
                self._deal_snapshot_and_next(create_host_snapshot_task, task_context)
                # 处理 cdp in Next
                self._deal_cdp_in_next(create_host_snapshot_task)

            _logger.info(r'create host snapshot end')
        except Exception as e:
            _logger.error(r'CBT_CreateHostSnapshot failed : {}'.format(e), exc_info=True)
            task_context['error'] = (r'合并数据状态失败', r'CBT_CreateHostSnapshot failed : {}'.format(e),)

        return task_context

    @staticmethod
    def _has_cluster_disk(create_host_snapshot_task):
        for _, disk_task in create_host_snapshot_task['disks'].items():
            if disk_task['type'] == 'cluster':
                return True
        return False

    @staticmethod
    def _deal_original_cdp_token(create_host_snapshot_task):
        for _, disk_task in create_host_snapshot_task['disks'].items():
            cdp_disk_token_object = CDPDiskToken.objects.get(token=disk_task['original_cdp_token'])
            cdp_disk_token_object.token = disk_task['base_cdp_token']
            cdp_disk_token_object.save(update_fields=['token', ])

    def _deal_cdp(self, create_host_snapshot_task, host_snapshot_object, host_snapshot_type):
        for _, disk_task in create_host_snapshot_task['disks'].items():
            for sub_task in disk_task['sub_tasks']:
                if not DiskSnapshot.is_cdp_file(sub_task['image_path']) \
                        or sub_task['host_snapshot'] != host_snapshot_type:
                    continue
                if sub_task['type'] == 'create_disk_snapshot_object':
                    self._create_disk_snapshot_object(sub_task, host_snapshot_object)
                elif sub_task['type'] == 'alter_disk_snapshot_object':
                    self._alter_disk_snapshot_object(sub_task)
                else:
                    xlogging.raise_and_logging_error(
                        r'内部异常，代码2387', '_deal_cdp unknown type : {}'.format(sub_task['type']))

                cdp_info_object = host_snapshot_object.cdp_info
                update_fields = list()
                if cdp_info_object.first_datetime is None:
                    cdp_info_object.first_datetime = datetime.datetime.fromtimestamp(sub_task['first_timestamp'])
                    update_fields.append('first_datetime')
                last = datetime.datetime.fromtimestamp(sub_task['last_timestamp'])
                if cdp_info_object.last_datetime is None \
                        or cdp_info_object.last_datetime < last:
                    cdp_info_object.last_datetime = last
                    update_fields.append('last_datetime')
                if len(update_fields):
                    cdp_info_object.save(update_fields=update_fields)

    def _deal_cdp_in_base(self, create_host_snapshot_task):
        base_host_snapshot_object = \
            HostSnapshot.objects.get(id=create_host_snapshot_task['base_host_snapshot']['id'])
        self._deal_cdp(create_host_snapshot_task, base_host_snapshot_object, 'base')

    @staticmethod
    def _alter_disk_snapshot_object(sub_task):
        parent_disk_snapshot_object = DiskSnapshot.objects.get(ident=sub_task['parent_disk_snapshot_ident'])
        disk_snapshot_object = DiskSnapshot.objects.get(ident=sub_task['new_ident'])
        disk_snapshot_object.parent_snapshot = parent_disk_snapshot_object
        disk_snapshot_object.save(update_fields=['parent_snapshot', ])
        cdp_disk_token_object = CDPDiskToken.objects.get(token=sub_task['cdp_token'])
        disk_snapshot_object.cdp_info.token = cdp_disk_token_object
        disk_snapshot_object.cdp_info.first_timestamp = sub_task['first_timestamp']
        disk_snapshot_object.cdp_info.last_timestamp = sub_task['last_timestamp']
        disk_snapshot_object.cdp_info.save(update_fields=['token', 'first_timestamp', 'last_timestamp'])
        cdp_disk_token_object.last_disk_snapshot = disk_snapshot_object
        cdp_disk_token_object.save(update_fields=['last_disk_snapshot', ])
        return disk_snapshot_object

    @staticmethod
    def _create_disk_snapshot_object(sub_task, base_host_snapshot_object):
        parent_disk_snapshot_object = DiskSnapshot.objects.get(ident=sub_task['parent_disk_snapshot_ident'])
        assert boxService.box_service.isFileExist(sub_task['image_path'])
        if DiskSnapshot.is_cdp_file(sub_task['image_path']):
            disk_snapshot_object = DiskSnapshot.objects.create(
                disk=parent_disk_snapshot_object.disk, display_name=parent_disk_snapshot_object.display_name,
                parent_snapshot=parent_disk_snapshot_object,
                image_path=sub_task['image_path'], ident=sub_task['new_ident'],
                host_snapshot=None, bytes=parent_disk_snapshot_object.bytes,
                type=parent_disk_snapshot_object.type, boot_device=parent_disk_snapshot_object.boot_device,
            )
            cdp_disk_token_object = CDPDiskToken.objects.get(token=sub_task['cdp_token'])
            disk_snapshot_cdp_object = DiskSnapshotCDP.objects.create(
                disk_snapshot=disk_snapshot_object, token=cdp_disk_token_object,
                first_timestamp=sub_task['first_timestamp'], last_timestamp=sub_task['last_timestamp'],
            )
            cdp_disk_token_object.last_disk_snapshot = disk_snapshot_object
            cdp_disk_token_object.save(update_fields=['last_disk_snapshot', ])
            return disk_snapshot_object
        else:
            disk_snapshot_object = DiskSnapshot.objects.create(
                disk=parent_disk_snapshot_object.disk, display_name=parent_disk_snapshot_object.display_name,
                parent_snapshot=parent_disk_snapshot_object,
                image_path=sub_task['image_path'], ident=sub_task['new_ident'],
                host_snapshot=base_host_snapshot_object, bytes=parent_disk_snapshot_object.bytes,
                type=parent_disk_snapshot_object.type, boot_device=parent_disk_snapshot_object.boot_device,
                reorganized_hash=True  # 都确保hash一致性，标记为整理过
            )
            return disk_snapshot_object

    def _deal_diff(self, create_host_snapshot_task, task_context):
        if not self._has_cluster_disk(create_host_snapshot_task):
            return

        base_host_snapshot_object = \
            HostSnapshot.objects.get(id=create_host_snapshot_task['base_host_snapshot']['id'])

        time_0 = datetime.datetime.strptime(
            task_context['backup_snapshot_time_0'], xdatetime.FORMAT_WITH_MICROSECOND)

        # 创建 host_snapshot
        host_snapshot_object = HostSnapshot.objects.create(
            host=base_host_snapshot_object.host, start_datetime=time_0, finish_datetime=time_0,
            successful=True, deleted=False, ext_info=base_host_snapshot_object.ext_info,
            display_status='', deleting=False, is_cdp=True, cluster_schedule=base_host_snapshot_object.cluster_schedule,
        )
        create_host_snapshot_task['diff_host_snapshot']['id'] = host_snapshot_object.id

        HostSnapshotCDP.objects.create(host_snapshot=host_snapshot_object, stopped=True)

        # 创建 cdp_task
        cdp_task_object = CDPTask.objects.create(
            start_datetime=time_0, cluster_task=self._task_object, host_snapshot=host_snapshot_object,
        )
        create_host_snapshot_task['diff_host_snapshot']['diff_cdp_task_id'] = cdp_task_object.id

        for _, disk_task in create_host_snapshot_task['disks'].items():
            for sub_task in disk_task['sub_tasks']:
                if DiskSnapshot.is_cdp_file(sub_task['image_path']) or sub_task['step'] != 'diff':
                    continue

                disk_snapshot_object = self._create_disk_snapshot_object(sub_task, host_snapshot_object)
                sub_task['disk_snapshot_id'] = disk_snapshot_object.id
                CDPDiskToken.objects.create(
                    parent_disk_snapshot=disk_snapshot_object, task=cdp_task_object, token=disk_task['diff_cdp_token'],
                )

    def _deal_cdp_in_diff(self, create_host_snapshot_task):
        if not self._has_cluster_disk(create_host_snapshot_task):
            return

        diff_host_snapshot_object = \
            HostSnapshot.objects.get(id=create_host_snapshot_task['diff_host_snapshot']['id'])

        self._deal_cdp(create_host_snapshot_task, diff_host_snapshot_object, 'diff')

    @staticmethod
    def _deal_snapshot_and_next_file(create_host_snapshot_task):
        validator_list = [GetSnapshotList.is_disk_snapshot_object_exist,
                          GetSnapshotList.is_disk_snapshot_file_exist]

        for _, disk_task in create_host_snapshot_task['disks'].items():
            if disk_task['type'] == 'exclude':
                base_snapshot_object = DiskSnapshot.objects.get(ident=disk_task['qcow_base_ident'])
                base_snapshots = GetSnapshotList.query_snapshots_by_snapshot_object(base_snapshot_object,
                                                                                    validator_list)
            else:
                base_snapshot_object = DiskSnapshot.objects.get(ident=disk_task['snapshot_parent_ident'])
                base_snapshots = list()

            new_ident = IMG.ImageSnapshotIdent(disk_task['qcow_image_path'], disk_task['snapshot_ident'])
            handle = boxService.box_service.createNormalDiskSnapshot(
                new_ident, base_snapshots, base_snapshot_object.bytes,
                r'PiD{:x} BoxDashboard|_deal_snapshot_and_next_file-1'.format(os.getpid()))
            boxService.box_service.runCmd('touch {}'.format(
                disk_task['qcow_image_path'] + '_' + disk_task['snapshot_ident'] + '.hash'))
            boxService.box_service.closeNormalDiskSnapshot(handle, True)
            base_snapshots.append(new_ident)
            new_ident = IMG.ImageSnapshotIdent(disk_task['qcow_image_path'], disk_task['next_ident'])
            handle = boxService.box_service.createNormalDiskSnapshot(
                new_ident, base_snapshots, base_snapshot_object.bytes,
                r'PiD{:x} BoxDashboard|_deal_snapshot_and_next_file-2'.format(os.getpid()))
            boxService.box_service.runCmd('touch {}'.format(
                disk_task['qcow_image_path'] + '_' + disk_task['next_ident'] + '.hash'))
            boxService.box_service.closeNormalDiskSnapshot(handle, True)

    def _deal_snapshot_and_next(self, create_host_snapshot_task, task_context):
        base_host_snapshot_object = \
            HostSnapshot.objects.get(id=create_host_snapshot_task['base_host_snapshot']['id'])

        time_1 = datetime.datetime.strptime(task_context['backup_snapshot_time'], xdatetime.FORMAT_WITH_MICROSECOND)

        # 创建 snapshot_host_snapshot
        snapshot_host_snapshot_object = HostSnapshot.objects.create(
            host=base_host_snapshot_object.host, start_datetime=time_1, finish_datetime=time_1,
            successful=True, deleted=False, ext_info=base_host_snapshot_object.ext_info,
            display_status='', deleting=False, is_cdp=False,
            cluster_schedule=base_host_snapshot_object.cluster_schedule,
            cluster_visible=True,
        )
        create_host_snapshot_task['snapshot_host_snapshot']['id'] = snapshot_host_snapshot_object.id

        # 创建 next_host_snapshot
        next_host_snapshot_object = HostSnapshot.objects.create(
            host=base_host_snapshot_object.host, start_datetime=time_1, finish_datetime=time_1,
            successful=True, deleted=False, ext_info=base_host_snapshot_object.ext_info,
            display_status='', deleting=False, is_cdp=True,
            cluster_schedule=base_host_snapshot_object.cluster_schedule,
        )
        create_host_snapshot_task['next_host_snapshot']['id'] = next_host_snapshot_object.id

        HostSnapshotCDP.objects.create(host_snapshot=next_host_snapshot_object, stopped=True)

        # 创建 cdp_task
        cdp_task_object = CDPTask.objects.create(
            start_datetime=time_1, cluster_task=self._task_object, host_snapshot=next_host_snapshot_object,
        )
        create_host_snapshot_task['next_host_snapshot']['next_cdp_task_id'] = cdp_task_object.id

        for _, disk_task in create_host_snapshot_task['disks'].items():
            for sub_task in disk_task['sub_tasks']:
                if DiskSnapshot.is_cdp_file(sub_task['image_path']) or sub_task['step'] not in ('snapshot', 'next',):
                    continue

                disk_snapshot_object = self._create_disk_snapshot_object(
                    sub_task, snapshot_host_snapshot_object if sub_task['step'] == 'snapshot'
                    else next_host_snapshot_object)
                sub_task['disk_snapshot_id'] = disk_snapshot_object.id

                if sub_task['step'] == 'next':
                    CDPDiskToken.objects.create(
                        parent_disk_snapshot=disk_snapshot_object, task=cdp_task_object,
                        token=disk_task['original_cdp_token'],
                    )

    def _deal_cdp_in_next(self, create_host_snapshot_task):
        diff_host_snapshot_object = \
            HostSnapshot.objects.get(id=create_host_snapshot_task['next_host_snapshot']['id'])

        self._deal_cdp(create_host_snapshot_task, diff_host_snapshot_object, 'next')


class CBT_PostCreateHostSnapshot(task.Task):
    default_provides = 'task_context'

    def __init__(self, name, task_id, inject=None):
        super(CBT_PostCreateHostSnapshot, self).__init__(r'CBT_PostCreateHostSnapshot {}'.format(name), inject=inject)
        self._task_id = task_id
        self._task_object = ClusterBackupTask.objects.get(id=self._task_id)
        self._config = json.loads(self._task_object.ext_config)
        self._schedule_object = self._task_object.schedule
        self._schedule_config = json.loads(self._schedule_object.ext_config)
        self._status = r'创建集群快照'

    def execute(self, task_context, *args, **kwargs):
        HostBackupWorkProcessors.cluster_hosts_log(
            self._schedule_object, self._task_object, HostLog.LOG_CLUSTER_BACKUP_SNAPSHOT,
            **{'substage': self._status, 'error_occur': ClusterBackupTaskExecutor.has_error(task_context)})
        update_cluster_backup_task_and_config_to_CBTObj(self, self._task_id, status_info=self._status)
        try:
            if ClusterBackupTaskExecutor.has_error(task_context):
                return task_context

            _check_run_twice(self._task_object, 'CBT_PostCreateHostSnapshot')

            _logger.info(r'begin post create host snapshot step ...')

            if os.path.exists(r'/dev/shm/cluster_retain.flag'):
                _logger.warning(r'skip post createhost snapshot step')
                return task_context

            for create_host_snapshot_task in task_context['create_host_snapshot_tasks']:
                for _, disk_task in create_host_snapshot_task['disks'].items():
                    if disk_task['type'] == 'normal':
                        if disk_task['need_remove_disk_snapshot_ident'] is not None:
                            self._remove(disk_task['need_remove_disk_snapshot_ident'])
                        else:
                            pass  # do nothing
                    elif disk_task['type'] == 'cluster':
                        for disk_snapshot_ident in reversed(disk_task['need_remove_disk_snapshot_ident_array']):
                            self._remove(disk_snapshot_ident)
                    elif disk_task['type'] == 'exclude':
                        pass  # do nothing
                    else:
                        xlogging.raise_and_logging_error(
                            '内部异常，代码2388', 'CBT_PostCreateHostSnapshot unknown type : {}'.format(disk_task))

            _logger.info(r'post create host snapshot end')
        except Exception as e:
            _logger.error(r'CBT_CreateHostSnapshot failed : {}'.format(e), exc_info=True)
            task_context['error'] = (r'合并数据状态失败', r'CBT_CreateHostSnapshot failed : {}'.format(e),)

        return task_context

    @staticmethod
    def _remove(disk_snapshot_ident):
        disk_snapshot_object = DiskSnapshot.objects.get(ident=disk_snapshot_ident)
        image_path = disk_snapshot_object.image_path
        assert DiskSnapshot.is_cdp_file(image_path)
        DeleteCdpFileTask(DeleteCdpFileTask.create(image_path)).work()
        disk_snapshot_object.parent_snapshot = None
        disk_snapshot_object.merged = True
        disk_snapshot_object.save(update_fields=['parent_snapshot', 'merged', ])


class CBT_CleanTemporary(task.Task):
    default_provides = 'task_context'

    def __init__(self, name, task_id, inject=None):
        super(CBT_CleanTemporary, self).__init__(r'CBT_CleanTemporary {}'.format(name), inject=inject)
        self._task_id = task_id
        self._task_object = ClusterBackupTask.objects.get(id=self._task_id)
        self._config = json.loads(self._task_object.ext_config)
        self._schedule_object = self._task_object.schedule
        self._schedule_config = json.loads(self._schedule_object.ext_config)
        self._status = r'清理临时数据'

    def execute(self, task_context, *args, **kwargs):
        HostBackupWorkProcessors.cluster_hosts_log(self._schedule_object, self._task_object,
                                                   HostLog.LOG_CLUSTER_BACKUP_SNAPSHOT, **{'substage': self._status})
        update_cluster_backup_task_and_config_to_CBTObj(self, self._task_id, status_info=self._status)

        if os.path.exists(r'/dev/shm/cluster_retain.flag'):
            _logger.warning(r'skip clean temporary step')
            return task_context

        try:
            _logger.info(r'begin clean temporary step ...')

            cluster_disk_snapshots = task_context.get('cluster_disk_snapshots', list())
            for cluster_disk_snapshot in cluster_disk_snapshots:
                diff_image_path = cluster_disk_snapshot.get('diff_image_path', None)
                if diff_image_path:
                    for f in boxService.box_service.findFiles(
                            os.path.split(diff_image_path)[1] + '*', os.path.split(diff_image_path)[0]):
                        boxService.box_service.remove(f)
                cdp_file_path_pre = cluster_disk_snapshot.get('cdp_file_path_pre', None)
                if cdp_file_path_pre:
                    for f in boxService.box_service.findFiles(
                            os.path.split(cdp_file_path_pre)[1] + '*', os.path.split(cdp_file_path_pre)[0]):
                        boxService.box_service.remove(f)
                for map_disk in cluster_disk_snapshot.get('map_disks', list()):
                    time0_hash_path = map_disk.get('time0_hash_path', None)
                    if time0_hash_path:
                        boxService.box_service.remove(time0_hash_path)

            _logger.info(r'clean temporary end')
        except Exception as e:
            _logger.error(r'CBT_CleanTemporary failed : {}'.format(e), exc_info=True)

        return task_context


class CBT_FinishBackup(task.Task):
    def __init__(self, name, task_id, inject=None):
        super(CBT_FinishBackup, self).__init__(r'CBT_FinishBackup {}'.format(name), inject=inject)
        self._task_id = task_id
        self._task_object = ClusterBackupTask.objects.get(id=self._task_id)
        self._config = json.loads(self._task_object.ext_config)
        self._schedule_object = self._task_object.schedule
        self._schedule_config = json.loads(self._schedule_object.ext_config)

    @staticmethod
    def finish_host_snapshot_object(task_context, successful):
        create_host_snapshot_tasks = task_context.get('create_host_snapshot_tasks', list())
        for create_host_snapshot_task in create_host_snapshot_tasks:
            host_snapshots = list()
            if 'id' in create_host_snapshot_task['diff_host_snapshot']:
                host_snapshots.append(create_host_snapshot_task['diff_host_snapshot']['id'])
            if 'id' in create_host_snapshot_task['snapshot_host_snapshot']:
                host_snapshots.append(create_host_snapshot_task['snapshot_host_snapshot']['id'])
            if 'id' in create_host_snapshot_task['next_host_snapshot']:
                host_snapshots.append(create_host_snapshot_task['next_host_snapshot']['id'])

            for host_snapshot in HostSnapshot.objects.filter(id__in=host_snapshots):
                host_snapshot.cluster_finish_datetime = datetime.datetime.now()
                host_snapshot.successful = successful
                host_snapshot.save(update_fields=['cluster_finish_datetime', 'successful'])

    @staticmethod
    def _clean_middle_file_in_current_task(task_context):
        for normal_disk_snapshot in task_context.get('normal_disk_snapshots', list()):
            if normal_disk_snapshot.get('split', None):
                split_params = normal_disk_snapshot['split']
                _logger.info(r'_clean_middle_file_in_current_task will clean normal_disk_snapshot file : {}'.
                             format(split_params['file_before']))
                boxService.box_service.remove(split_params['file_before'])
                _logger.info(r'_clean_middle_file_in_current_task will clean normal_disk_snapshot file : {}'.
                             format(split_params['file_after']))
                boxService.box_service.remove(split_params['file_after'])

        merge_tasks = task_context.get('merge_tasks', dict())
        for ident in merge_tasks.keys():
            for task_time_0 in merge_tasks[ident].get('task_time_0', list()):
                boxService.box_service.remove(task_time_0['image_path'])
                boxService.box_service.remove(task_time_0['cdp_file'])
            for task_diff in merge_tasks[ident].get('task_diff', list()):
                boxService.box_service.remove(task_diff['image_path'])
                boxService.box_service.remove(task_diff['image_path_map'])
                boxService.box_service.remove(task_diff['image_path_hash'])
                boxService.box_service.remove(task_diff['qcow_file'])
                boxService.box_service.remove(task_diff['qcow_file_map'])
                boxService.box_service.remove(task_diff['qcow_file_hash'])
            for task_time_1 in merge_tasks[ident].get('task_time_1', list()):
                boxService.box_service.remove(task_time_1['image_path'])
                boxService.box_service.remove(task_time_1['cdp_file'])
            for task_tail in task_context['merge_tasks'][ident]['task_tail']:
                boxService.box_service.remove(task_tail['image_path'])
                boxService.box_service.remove(task_tail['cdp_file'])

    def _clean_invisible_object_in_current_task(self, task_context):
        tasks = list()

        time_1_ids = list()
        tail_ids = list()

        create_host_snapshot_tasks = task_context.get('create_host_snapshot_tasks', list())
        for create_host_snapshot_task in create_host_snapshot_tasks:
            if 'id' in create_host_snapshot_task['diff_host_snapshot']:
                time_1_ids.append(create_host_snapshot_task['diff_host_snapshot']['id'])
            if 'id' in create_host_snapshot_task['diff_host_snapshot']:
                tail_ids.append(create_host_snapshot_task['next_host_snapshot']['id'])

        time_0 = datetime.datetime.strptime(task_context['backup_snapshot_time_0'], xdatetime.FORMAT_WITH_MICROSECOND)

        for sub_task in self._task_object.sub_tasks.all():
            host_snapshot_object = sub_task.host_snapshot
            if host_snapshot_object.id in time_1_ids or host_snapshot_object.id in tail_ids:
                continue
            host_snapshot_object.set_deleting()
            task_object = SpaceCollectionTask.objects.create(
                type=SpaceCollectionTask.TYPE_CDP_MERGE, host_snapshot=host_snapshot_object,
                cluster_schedule=self._schedule_object,
                ext_info=json.dumps(
                    {'new_snapshot_datetime': time_0.strftime(xdatetime.FORMAT_WITH_MICROSECOND),
                     'expire_host_snapshot_ids': list(), 'new_snapshot_invisible': True}, ensure_ascii=False)
            )
            tasks.append(task_object)

        time_1 = datetime.datetime.strptime(task_context['backup_snapshot_time'], xdatetime.FORMAT_WITH_MICROSECOND)

        for time_1_id in time_1_ids:
            host_snapshot_object = HostSnapshot.objects.get(
                id=time_1_id)
            host_snapshot_object.set_deleting()
            task_object = SpaceCollectionTask.objects.create(
                type=SpaceCollectionTask.TYPE_CDP_MERGE, host_snapshot=host_snapshot_object,
                cluster_schedule=self._schedule_object,
                ext_info=json.dumps(
                    {'new_snapshot_datetime': time_1.strftime(xdatetime.FORMAT_WITH_MICROSECOND),
                     'expire_host_snapshot_ids': list(), 'new_snapshot_invisible': True}, ensure_ascii=False)
            )
            tasks.append(task_object)

        return tasks

    def _clean_middle_object_in_current_task(self, task_context):
        qcow_snapshots = list()

        for create_host_snapshot_task in task_context.get('create_host_snapshot_tasks', list()):
            for _, disk_task in create_host_snapshot_task['disks'].items():
                qcow_snapshots.append({'path': disk_task['qcow_image_path'], 'snapshot': disk_task['next_ident']})
                qcow_snapshots.append({'path': disk_task['qcow_image_path'], 'snapshot': disk_task['snapshot_ident']})

        for qcow_snapshot in qcow_snapshots:
            DeleteDiskSnapshotTask(
                DeleteDiskSnapshotTask.create(qcow_snapshot['path'], qcow_snapshot['snapshot'])).work()

        for sub_task in self._task_object.sub_tasks.all():
            if sub_task.host_snapshot is None:
                continue
            HostBackupWorkProcessors.deal_cdp_when_backup_failed(sub_task.host_snapshot, sub_task)

    def execute(self, task_context, *args, **kwargs):
        update_cluster_backup_task_and_config_to_CBTObj(self, self._task_id)
        try:
            _logger.info(r'begin finish backup step ...')

            successful = not ClusterBackupTaskExecutor.has_error(task_context)

            for sub_task in self._task_object.sub_tasks.all():
                sub_task.finish_datetime = datetime.datetime.now()
                sub_task.successful = successful
                sub_task.save(update_fields=['finish_datetime', 'successful', ])

                host_snapshot_object = sub_task.host_snapshot
                if host_snapshot_object is not None:
                    host_snapshot_object.finish_datetime = \
                        host_snapshot_object.cluster_finish_datetime = datetime.datetime.now()
                    host_snapshot_object.successful = successful
                    host_snapshot_object.save(
                        update_fields=['finish_datetime', 'cluster_finish_datetime', 'successful', ])

            if successful and not os.path.exists(r'/dev/shm/cluster_retain.flag'):
                merge_cdp_tasks = self._clean_invisible_object_in_current_task(task_context)
                for merge_cdp_task in merge_cdp_tasks:
                    CDPHostSnapshotSpaceCollectionMergeTask(merge_cdp_task).work()  # fixme 是否有必要等它必须完成?

            self._task_object.finish_datetime = datetime.datetime.now()
            self._task_object.successful = successful
            self._task_object.save(update_fields=['finish_datetime', 'successful', ])

            CBT_FinishBackup.finish_host_snapshot_object(task_context, successful)

            if successful:
                HostBackupWorkProcessors.cluster_hosts_log(self._schedule_object, self._task_object,
                                                           HostLog.LOG_CLUSTER_BACKUP_SUCCESSFUL)
                BackupScheduleRetryHandle.clean(self._schedule_object)
            else:
                self._clean_middle_file_in_current_task(task_context)
                self._clean_middle_object_in_current_task(task_context)
                HostBackupWorkProcessors.cluster_hosts_log(self._schedule_object, self._task_object,
                                                           HostLog.LOG_CLUSTER_BACKUP_FAILED, **{'debug': task_context})
                BackupScheduleRetryHandle.modify(self._schedule_object, backup_task=self._task_object)
            _logger.info(r'finish backup end')
        except Exception as e:
            _logger.error(r'CBT_FinishBackup failed : {}'.format(e), exc_info=True)
