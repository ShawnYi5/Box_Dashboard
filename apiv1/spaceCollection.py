import json
import os
import random
import time
import traceback
import uuid
from datetime import datetime, timedelta
from threading import Thread, RLock, Lock

from django.db import transaction

from apiv1.cdp_wrapper import fix_restore_time
from apiv1.compress import CompressTaskThreading
from apiv1.models import MigrateTask, RestoreTask, HostSnapshotShare, BackupTask, BackupTaskSchedule, \
    SpaceCollectionTask, ClusterBackupTask, \
    HostLog, HostSnapshot, CDPTask, DiskSnapshot, HostSnapshotCDP, CompressTask, ClusterBackupSchedule, \
    RemoteBackupSchedule, RemoteBackupTask, FileBackupTask
from apiv1.snapshot import DiskSnapshotLocker, GetSnapshotList, GetDiskSnapshot, DiskSnapshotHash
from apiv1.storage_nodes import UserQuotaTools
from box_dashboard import xlogging, boxService, xdatetime
from xdashboard.common.dict import GetDictionary
from xdashboard.models import DataDictionary

_logger = xlogging.getLogger(__name__)
_locker = RLock()


def _is_BackupTaskSchedule_instance(schedule):
    if schedule:
        return isinstance(schedule, BackupTaskSchedule)
    return False


def _is_ClusterBackupSchedule_instance(schedule):
    if schedule:
        return isinstance(schedule, ClusterBackupSchedule)
    return False


def _is_RemoteBackupSchedule_instance(schedule):
    if schedule:
        return isinstance(schedule, RemoteBackupSchedule)
    return False


_BackupScheduleInstance = 1
_ClusterScheduleInstance = 2
_RemoteScheduleInstance = 3


@xlogging.convert_exception_to_value(None)
def get_last_cdp_timestamp(host_snapshot_object):
    result = 0
    for disk_snapshot in host_snapshot_object.disk_snapshots.all():
        assert disk_snapshot.child_snapshots.count() == 1
        current = disk_snapshot.child_snapshots.first()
        while True:
            if not current.is_cdp:
                break

            start_timestamp, end_timestamp = boxService.box_service.queryCdpTimestampRange(current.image_path)
            if end_timestamp > result:
                result = end_timestamp

            assert current.child_snapshots.count() == 1
            current = current.child_snapshots.first()
    return result


def _get_schedule_object(schedule_id, schedule_type):
    if schedule_type == _BackupScheduleInstance:
        return BackupTaskSchedule.objects.get(id=schedule_id)
    elif schedule_type == _ClusterScheduleInstance:
        return ClusterBackupSchedule.objects.get(id=schedule_id)
    elif schedule_type == _RemoteScheduleInstance:
        return RemoteBackupSchedule.objects.get(id=schedule_id)
    else:
        xlogging.raise_and_logging_error(
            r'assert schedule_object type', r'unknown schedule_type : {}'.format(schedule_type))


def _check_disk_snapshot_is_cdp(disk_snapshot_id):
    disk_snapshot_object = DiskSnapshot.objects.get(id=disk_snapshot_id)
    if not disk_snapshot_object.is_cdp:
        xlogging.raise_and_logging_error(
            r'内部异常，代码2379', '_check_disk_snapshot_is_cdp failed {}'.format(disk_snapshot_id))
    return disk_snapshot_object


def _check_disk_snapshot_parent_is_not_cdp(disk_snapshot_id):
    disk_snapshot_object = DiskSnapshot.objects.get(id=disk_snapshot_id)
    parent_snapshot_object = disk_snapshot_object.parent_snapshot
    if not parent_snapshot_object:
        xlogging.raise_and_logging_error(
            r'内部异常，代码2364', '_check_disk_snapshot_parent_is_not_cdp parent_snapshot_object is None {}'.format(
                disk_snapshot_id))
    if parent_snapshot_object.is_cdp:
        xlogging.raise_and_logging_error(
            r'内部异常，代码2364', '_check_disk_snapshot_parent_is_not_cdp failed {}'.format(disk_snapshot_id))
    return disk_snapshot_object


def _check_host_snapshot_is_cdp(host_snapshot_id):
    host_snapshot_object = HostSnapshot.objects.get(id=host_snapshot_id)
    if not host_snapshot_object.is_cdp:
        xlogging.raise_and_logging_error(
            r'内部异常，代码2365', '_check_host_snapshot_is_cdp failed {}'.format(host_snapshot_id))

    return host_snapshot_object


def _create_compress_task_by_merge_task(disk_snap_shot_object):
    if CompressTask.objects.filter(disk_snapshot=disk_snap_shot_object).exists():
        _logger.error('_create_compress_task_by_merge_task fail, task is already exists')
        return None
    else:
        _logger.debug(
            '_create_compress_task_by_merge_task successful, disk_snapshot id is :{}'.format(disk_snap_shot_object.id))
        CompressTask.objects.create(disk_snapshot=disk_snap_shot_object)


def check_disk_snapshot_belongs_to_other_valid_schedule(disk_snap_shot_object, schedule_id, schedule_type):
    if disk_snap_shot_object.is_cdp:
        return False
    host_snapshot = GetSnapshotList.get_host_snapshot_by_disk_snapshot(disk_snap_shot_object)

    if schedule_type == _BackupScheduleInstance:
        schedule_obj = host_snapshot.schedule
    elif schedule_type == _ClusterScheduleInstance:
        schedule_obj = host_snapshot.cluster_schedule
    elif schedule_type == _RemoteScheduleInstance:
        schedule_obj = host_snapshot.remote_schedule
    else:
        xlogging.raise_and_logging_error(
            r'assert schedule_object type', r'unknown schedule_type : {}'.format(schedule_type))
        return

    if schedule_obj is None or schedule_obj.id == schedule_id or schedule_obj.deleted:
        return False
    else:
        return True


@xlogging.convert_exception_to_value(7)
def _get_partial_disk_snapshot_expire_days():
    return int(GetDictionary(DataDictionary.DICT_TYPE_PARTIAL_DISK_SNAPSHOT_EXP, 'exp_days', '7'))


_check_dir_locker = Lock()


class CheckDir(object):
    def __init__(self, dir_path):
        self._file_path = boxService.box_service.pathJoin([dir_path, 'check'])
        self._check_string = uuid.uuid4().hex
        self._file_be_created = False

    def __enter__(self):
        try:
            self.check_path_dir_can_write()
            self._file_be_created = True
        except Exception as e:
            xlogging.raise_and_logging_error(r'存储节点无法访问', 'CheckDir check_path_dir_can_write {}'.format(e))

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if (exc_type is None) and (not self.check_path_dir_can_read()):
                xlogging.raise_and_logging_error(r'存储节点无法访问', 'CheckDir check_path_dir_can_read failed')
        except Exception as e:
            xlogging.raise_and_logging_error(r'存储节点无法访问', 'CheckDir check_path_dir_can_read {}'.format(e))
        finally:
            if self._file_be_created:
                boxService.box_service.remove(self._file_path)

    def check_path_dir_can_write(self):
        with _check_dir_locker:
            with open(self._file_path, 'wt') as f:
                f.write(self._check_string)

    def check_path_dir_can_read(self):
        with _check_dir_locker:
            with open(self._file_path, 'rt') as f:
                c = f.read()
        return len(c) == len(self._check_string)


class CdpMergeSubTask(object):
    TYPE_SUB_TASK_WITHOUT_INTERCEPT = 'sub_task_without_intercept'
    TYPE_SUB_TASK_WITH_INTERCEPT = 'sub_task_with_intercept'
    TYPE_SUB_TASK_NO_DATA = 'sub_task_no_data'

    def __init__(self, task_object):
        if task_object.type != SpaceCollectionTask.TYPE_CDP_MERGE_SUB:
            xlogging.raise_and_logging_error(
                '内部异常，代码2362', 'CdpMergeSubTask incorrect type {} {}'.format(task_object.id, task_object.type))
        self._task_object = task_object

    @xlogging.convert_exception_to_value(False)
    def work(self):
        ext_info = json.loads(self._task_object.ext_info)
        sub_type = ext_info['type']

        if sub_type == self.TYPE_SUB_TASK_WITHOUT_INTERCEPT:
            self.deal_sub_task_without_intercept(ext_info)
        elif sub_type == self.TYPE_SUB_TASK_WITH_INTERCEPT:
            self.deal_sub_task_with_intercept(ext_info)
        elif sub_type == self.TYPE_SUB_TASK_NO_DATA:
            self.deal_sub_task_no_data(ext_info)
        else:
            xlogging.raise_and_logging_error(
                '内部异常，代码2363', 'CdpMergeSubTask incorrect sub_type {} {}'.format(self._task_object.id, sub_type))

        self._task_object.set_finished()
        return True

    def _get_or_generate_child_disksnapshot_ids(self, ext_info, child_disk_snapshot_object):
        if '_child_ids' in ext_info.keys():
            return ext_info['_child_ids']

        ext_info['_child_ids'] = list()

        for disk_snapshot in DiskSnapshot.objects.filter(
                parent_snapshot=child_disk_snapshot_object.parent_snapshot,
                parent_timestamp=child_disk_snapshot_object.parent_timestamp,
                merged=False).all():
            ext_info['_child_ids'].append(disk_snapshot.id)

        self._update_ext_info(ext_info)
        return ext_info['_child_ids']

    def _merge_cdp_file_to_normal_file(self, ext_info):
        new_disk_snapshot_ident = ext_info['new_disk_snapshot_ident']
        if ext_info['stage'] == 1:
            parent_snapshot_object, current_snapshot_object, child_disk_snapshot_object = \
                self._get_parent_and_current_and_child_disk_snapshot(ext_info['disk_snapshot_id'],
                                                                     ext_info['child_disk_snapshot_id'])

            if current_snapshot_object.merged:
                _logger.warning(r'can NOT merge_cdp_file, because current_snapshot_object.merged {} {}'
                                .format(current_snapshot_object.id, self._task_object.id))
                ext_info['stage'] = -1
                self._update_ext_info(ext_info)
                return

            host_snapshot_timestamp = None
            if not ext_info['visible']:
                if ext_info['end_timestamp'] is None:
                    current_range = boxService.box_service.queryCdpTimestampRange(
                        current_snapshot_object.image_path, True)
                    if current_range[1] is None:
                        host_snapshot_timestamp = current_snapshot_object.cdp_info.first_timestamp
                    else:
                        host_snapshot_timestamp = current_range[1]
                else:
                    host_snapshot_timestamp = ext_info['end_timestamp']
            # 支持任务redo，删除备份点
            self.delete_normal_snapshot(new_disk_snapshot_ident)
            new_disk_snapshot_object = self.create_new_disk_snapshot(
                parent_snapshot_object, new_disk_snapshot_ident, host_snapshot_timestamp)
            # 合并数据逻辑
            self._do_cdp_to_normal_logic(current_snapshot_object.image_path, ext_info['begin_timestamp'],
                                         ext_info['end_timestamp'], new_disk_snapshot_object)
            # 整理hash文件
            DiskSnapshotHash.reorganize_hash_file_by_disk_snapshot(new_disk_snapshot_object)
            # 为新disk_snapshot 创建压缩任务
            _create_compress_task_by_merge_task(new_disk_snapshot_object)
            ext_info['stage'] = 2
            ext_info['new_disk_snapshot_object_id'] = new_disk_snapshot_object.id
            self._update_ext_info(ext_info)
        if ext_info['stage'] == 2:
            parent_snapshot_object, current_snapshot_object, child_disk_snapshot_object = \
                self._get_parent_and_current_and_child_disk_snapshot(ext_info['disk_snapshot_id'],
                                                                     ext_info['child_disk_snapshot_id'])

            # 修改子备份的依赖
            if child_disk_snapshot_object is not None:
                new_disk_snapshot_object = DiskSnapshot.objects.get(id=ext_info['new_disk_snapshot_object_id'])
                child_ids = self._get_or_generate_child_disksnapshot_ids(ext_info, child_disk_snapshot_object)
                for child_id in child_ids:
                    child = DiskSnapshot.objects.get(id=child_id)
                    child.parent_snapshot = new_disk_snapshot_object
                    child.parent_timestamp = None
                    child.save(update_fields=['parent_snapshot', 'parent_timestamp'])

            ext_info['stage'] = 3
            self._update_ext_info(ext_info)

    @staticmethod
    def _merge_cdp_file_to_normal_file_finish(ext_info):
        new_disk_snapshot_object = DiskSnapshot.objects.get(id=ext_info['new_disk_snapshot_object_id'])
        if ext_info['visible']:
            # 删除临时主机快照对象, 任务完成后后续逻辑会加上主机快照对象
            new_host_snapshot = new_disk_snapshot_object.host_snapshot
            if new_host_snapshot is not None:
                new_disk_snapshot_object.host_snapshot = None
                new_disk_snapshot_object.save(update_fields=['host_snapshot'])
                new_host_snapshot.delete()
        else:
            # 添加host_snapshot到回收任务
            new_host_snapshot = new_disk_snapshot_object.host_snapshot
            if new_host_snapshot.finish_datetime is None:
                new_host_snapshot.finish_datetime = datetime.now()
                new_host_snapshot.deleting = False
                new_host_snapshot.successful = True
                new_host_snapshot.save(update_fields=['finish_datetime', 'deleting', 'successful'])

            SpaceCollectionWorker.set_host_snapshot_deleting_and_collection_space_later(new_host_snapshot)

    def deal_sub_task_with_intercept(self, ext_info):
        self._merge_cdp_file_to_normal_file(ext_info)

        if ext_info['stage'] == 3:
            parent_snapshot_object, current_snapshot_object, child_disk_snapshot_object = \
                self._get_parent_and_current_and_child_disk_snapshot(ext_info['disk_snapshot_id'],
                                                                     ext_info['child_disk_snapshot_id'])
            new_disk_snapshot_object = DiskSnapshot.objects.get(id=ext_info['new_disk_snapshot_object_id'])
            current_snapshot_object.parent_snapshot = new_disk_snapshot_object
            current_snapshot_object.save(update_fields=['parent_snapshot'])

            self._merge_cdp_file_to_normal_file_finish(ext_info)

    def deal_sub_task_without_intercept(self, ext_info):
        self._merge_cdp_file_to_normal_file(ext_info)

        if ext_info['stage'] == 3:
            parent_snapshot_object, current_snapshot_object, child_disk_snapshot_object = \
                self._get_parent_and_current_and_child_disk_snapshot(ext_info['disk_snapshot_id'],
                                                                     ext_info['child_disk_snapshot_id'])
            delete_task_object = DeleteCdpObjectTask.create(self._task_object.schedule, current_snapshot_object)
            DeleteCdpObjectTask(delete_task_object).work()  # 有重试机制，不需要检查是否失败

            self._merge_cdp_file_to_normal_file_finish(ext_info)

    def deal_sub_task_no_data(self, ext_info):
        new_disk_snapshot_ident = ext_info['new_disk_snapshot_ident']
        parent_snapshot_object = DiskSnapshot.objects.get(id=ext_info['base_disk_snapshot_id'])

        if ext_info['stage'] == 1:
            # 支持任务redo，删除备份点
            new_disk_snapshot_object = self.delete_normal_snapshot(new_disk_snapshot_ident)
            if new_disk_snapshot_object is None:
                new_disk_snapshot_object = \
                    self.create_new_disk_snapshot(parent_snapshot_object, new_disk_snapshot_ident, None)
                # 创建空数据快照点逻辑
                self._do_no_data_snapshot_logic(new_disk_snapshot_object)
            else:
                _logger.warning(
                    'deal_sub_task_no_data use old disk_snapshot_object : {}'.format(new_disk_snapshot_object.id))

            # todo 可调用 NormalHostSnapshotSpaceCollectionTask.update_child_disk_snapshot_object_parent_to_special
            for child_disk_snapshot in parent_snapshot_object.child_snapshots.filter(merged=False) \
                    .exclude(id=new_disk_snapshot_object.id).all():
                child_disk_snapshot.parent_snapshot = new_disk_snapshot_object
                child_disk_snapshot.save(update_fields=['parent_snapshot'])

            ext_info['stage'] = 2
            ext_info['new_disk_snapshot_object_id'] = new_disk_snapshot_object.id
            self._update_ext_info(ext_info)

    def _update_ext_info(self, ext_info):
        self._task_object.ext_info = json.dumps(ext_info, ensure_ascii=False)
        self._task_object.save(update_fields=['ext_info'])

    def _do_no_data_snapshot_logic(self, new_disk_snapshot_object):
        msg_debug = '_do_no_data_snapshot_logic{}'.format(self._task_object.id)
        try:
            GetSnapshotList.create_empty_snapshot(new_disk_snapshot_object, r'内部异常，代码2372', msg_debug, msg_debug,
                                                  GetSnapshotList.copy_hash_in_create_empty_snapshot)
        except Exception as e:
            try:
                boxService.box_service.deleteNormalDiskSnapshot(new_disk_snapshot_object.image_path,
                                                                new_disk_snapshot_object.ident, False)
                _logger.info(r'deleteNormalDiskSnapshot ok when _do_no_data_snapshot_logic failed {} {} | {} {}'.format(
                    msg_debug, e, new_disk_snapshot_object.image_path, new_disk_snapshot_object.ident
                ))
            except Exception as ex:
                _logger.info(r'deleteNormalDiskSnapshot failed when _do_no_data_snapshot_logic failed'
                             r' {} | {} {} | {}'.format(msg_debug, new_disk_snapshot_object.image_path,
                                                        new_disk_snapshot_object.ident, ex))
            raise e

    def _do_cdp_to_normal_logic(self, cdp_file, begin_timestamp, end_timestamp, new_disk_snapshot_object):
        last_disk_snapshot_object = new_disk_snapshot_object.parent_snapshot

        cdp_time_range = GetSnapshotList.format_timestamp(begin_timestamp, end_timestamp)

        validator_list = [GetSnapshotList.is_disk_snapshot_object_exist,
                          GetSnapshotList.is_disk_snapshot_file_exist]
        disk_snapshots = GetSnapshotList.query_snapshots_by_snapshot_object(last_disk_snapshot_object, validator_list)
        if len(disk_snapshots) == 0:
            xlogging.raise_and_logging_error(
                r'内部异常，代码2366', r'_do_cdp_to_normal_logic snapshot_object invalid {} {}'.format(
                    self._task_object.id, last_disk_snapshot_object.id))

        params = {'cdp_file': cdp_file, 'cdp_time_range': cdp_time_range, 'disk_bytes': new_disk_snapshot_object.bytes,
                  'qcow_file': last_disk_snapshot_object.image_path, 'qcow_ident': new_disk_snapshot_object.ident,
                  'last_snapshots': []}

        task_name = 'cdp_merge_sub_task' + str(self._task_object.id)
        try:
            for disk_snapshot in disk_snapshots:
                DiskSnapshotLocker.lock_file(disk_snapshot.path, disk_snapshot.snapshot, task_name)
                CompressTaskThreading().update_task_by_disk_snapshot(disk_snapshot.path, disk_snapshot.snapshot)
                params['last_snapshots'].append({'path': disk_snapshot.path, 'ident': disk_snapshot.snapshot})

            boxService.box_service.mergeCdpFile(json.dumps(params, ensure_ascii=False))
        except Exception as e:
            try:
                boxService.box_service.deleteNormalDiskSnapshot(params['qcow_file'], params['qcow_ident'], False)
                _logger.info(r'deleteNormalDiskSnapshot ok when cdp merge failed {} {} | {} {} '.format(
                    task_name, e, params['qcow_file'], params['qcow_ident']
                ))
            except Exception as ex:
                _logger.info(r'deleteNormalDiskSnapshot failed when cdp merge failed {} | {} {} | {}'.format(
                    task_name, params['qcow_file'], params['qcow_ident'], ex
                ))
            raise e
        finally:
            for disk_snapshot in disk_snapshots:
                DiskSnapshotLocker.unlock_file(disk_snapshot.path, disk_snapshot.snapshot, task_name, True)

    @staticmethod
    def create_new_disk_snapshot(parent_disk_snapshot_object, new_disk_snapshot_ident, host_snapshot_timestamp):
        disk_snapshot_object = DiskSnapshot.objects.create(
            disk=parent_disk_snapshot_object.disk, parent_snapshot=parent_disk_snapshot_object,
            image_path=parent_disk_snapshot_object.image_path, ident=new_disk_snapshot_ident,
            host_snapshot=None, bytes=parent_disk_snapshot_object.bytes, type=parent_disk_snapshot_object.type,
            boot_device=parent_disk_snapshot_object.boot_device, display_name=parent_disk_snapshot_object.display_name,
            ext_info=parent_disk_snapshot_object.ext_info)

        if host_snapshot_timestamp is not None:
            parent_host_snapshot_object = parent_disk_snapshot_object.host_snapshot

            host_snapshot_object = HostSnapshot.objects.create(
                host=parent_host_snapshot_object.host, start_datetime=datetime.fromtimestamp(host_snapshot_timestamp),
                deleting=True, schedule=parent_host_snapshot_object.schedule,
                cluster_schedule=parent_host_snapshot_object.cluster_schedule,
                remote_schedule=parent_host_snapshot_object.remote_schedule,
                ext_info=parent_host_snapshot_object.ext_info,
            )

            disk_snapshot_object.host_snapshot = host_snapshot_object
            disk_snapshot_object.save(update_fields=['host_snapshot'])

        return disk_snapshot_object

    @staticmethod
    @xlogging.convert_exception_to_value(None)
    def delete_normal_snapshot(disk_snapshot_ident):
        disk_snapshot_object = DiskSnapshot.objects.filter(ident=disk_snapshot_ident).first()
        if disk_snapshot_object is None:
            return None

        if disk_snapshot_object.child_snapshots.count() > 0:
            remain_disk_snapshot_object = disk_snapshot_object
        else:
            remain_disk_snapshot_object = None
            DeleteDiskSnapshotTask.do_del(
                'delete_normal_snapshot_' + str(disk_snapshot_object.id), False, disk_snapshot_object.image_path,
                disk_snapshot_ident)

            host_snapshot = disk_snapshot_object.host_snapshot

            disk_snapshot_object.delete()

            if host_snapshot is not None:
                host_snapshot.delete()

        return remain_disk_snapshot_object

    @staticmethod
    def _get_parent_and_current_and_child_disk_snapshot(disk_snapshot_id, child_disk_snapshot_id):
        child_disk_snapshot_object = None
        if child_disk_snapshot_id is not None:
            child_disk_snapshot_object = DiskSnapshot.objects.get(id=child_disk_snapshot_id)
        disk_snapshot_object = DiskSnapshot.objects.get(id=disk_snapshot_id)
        return disk_snapshot_object.parent_snapshot, disk_snapshot_object, child_disk_snapshot_object

    # 从 begin_timestamp 时间点开始到文件尾全部回收
    # begin_timestamp 为 None时，表示从文件头开始
    # 当visible为False任务结束后该CDP文件进入删除流程
    @staticmethod
    def create_sub_task_without_intercept(
            schedule, remote_schedule, cluster_schedule, host_snapshot_id, child_disk_snapshot,
            disk_snapshot_id, begin_timestamp, visible=False, new_disk_snapshot_ident=None):
        if new_disk_snapshot_ident is None:
            new_disk_snapshot_ident = uuid.uuid4().hex
        child_disk_snapshot_id = child_disk_snapshot.id if child_disk_snapshot is not None else None
        ext_info = {'type': CdpMergeSubTask.TYPE_SUB_TASK_WITHOUT_INTERCEPT,
                    'child_disk_snapshot_id': child_disk_snapshot_id,
                    'disk_snapshot_id': disk_snapshot_id,
                    'begin_timestamp': begin_timestamp,
                    'end_timestamp': None,
                    'new_disk_snapshot_ident': new_disk_snapshot_ident,
                    'stage': 1,
                    'visible': visible}
        return SpaceCollectionTask.objects.create(type=SpaceCollectionTask.TYPE_CDP_MERGE_SUB,
                                                  host_snapshot_id=host_snapshot_id,
                                                  schedule=schedule,
                                                  remote_schedule=remote_schedule,
                                                  cluster_schedule=cluster_schedule,
                                                  ext_info=json.dumps(ext_info, ensure_ascii=False))

    # 从 begin_timestamp 时间点开始到 end_timestamp 时间点结束全部回收
    # begin_timestamp 为 None时，表示从文件头开始
    # 任务结束后该CDP文件应该依赖新创建的快照点
    @staticmethod
    def create_sub_task_with_intercept(
            schedule, remote_schedule, cluster_schedule, host_snapshot_id, child_disk_snapshot_id, disk_snapshot_id,
            begin_timestamp, end_timestamp, visible=False, new_disk_snapshot_ident=None):
        if new_disk_snapshot_ident is None:
            new_disk_snapshot_ident = uuid.uuid4().hex
        ext_info = {'type': CdpMergeSubTask.TYPE_SUB_TASK_WITH_INTERCEPT,
                    'child_disk_snapshot_id': child_disk_snapshot_id,
                    'disk_snapshot_id': disk_snapshot_id,
                    'begin_timestamp': begin_timestamp,
                    'end_timestamp': end_timestamp,
                    'new_disk_snapshot_ident': new_disk_snapshot_ident,
                    'stage': 1,
                    'visible': visible}
        return SpaceCollectionTask.objects.create(type=SpaceCollectionTask.TYPE_CDP_MERGE_SUB,
                                                  host_snapshot_id=host_snapshot_id,
                                                  schedule=schedule,
                                                  remote_schedule=remote_schedule,
                                                  cluster_schedule=cluster_schedule,
                                                  ext_info=json.dumps(ext_info, ensure_ascii=False))

    @staticmethod
    def create_sub_task_create_no_data_snapshot(schedule, remote_schedule, cluster_schedule, host_snapshot_id,
                                                new_disk_snapshot_ident, base_disk_snapshot_id):
        ext_info = {'type': CdpMergeSubTask.TYPE_SUB_TASK_NO_DATA,
                    'base_disk_snapshot_id': base_disk_snapshot_id,
                    'new_disk_snapshot_ident': new_disk_snapshot_ident,
                    'stage': 1}
        return SpaceCollectionTask.objects.create(type=SpaceCollectionTask.TYPE_CDP_MERGE_SUB,
                                                  host_snapshot_id=host_snapshot_id,
                                                  schedule=schedule,
                                                  remote_schedule=remote_schedule,
                                                  cluster_schedule=cluster_schedule,
                                                  ext_info=json.dumps(ext_info, ensure_ascii=False))


class CDPHostSnapshotSpaceCollectionMergeTask(object):
    def __init__(self, task_object):
        if task_object.type != SpaceCollectionTask.TYPE_CDP_MERGE:
            xlogging.raise_and_logging_error(
                '内部异常，代码2358', 'CDPHostSnapshotSpaceCollectionMergeTask incorrect type {} {}'.format(
                    task_object.id, task_object.type))
        self._task_object = task_object

    @xlogging.convert_exception_to_value(False)
    def work(self):
        self._check_sub_task()

        expire_host_snapshot_ids = self._get_expire_host_snapshot_ids()

        for expire_host_snapshot_id in expire_host_snapshot_ids:  # 这些主机快照都是需要回收为不可见的快照点
            task_ext_info_key = 'expire_host_snapshot_{}'.format(expire_host_snapshot_id)
            task_ext_info = self._get_or_create_task_ext_info(task_ext_info_key, expire_host_snapshot_id)
            if task_ext_info['finished']:
                continue

            expire_host_snapshot_object = HostSnapshot.objects.get(id=expire_host_snapshot_id)

            if expire_host_snapshot_object.is_cdp:
                for disk_ident in task_ext_info['disks'].keys():
                    cdp_disk_snapshots = task_ext_info['disks'][disk_ident]['cdp_disk_snapshots']
                    self._merge_cdp_disk_snapshots_to_invisible_normal_disk_snapshots(
                        task_ext_info_key, cdp_disk_snapshots, expire_host_snapshot_id)

                expire_host_snapshot_object.is_cdp = False
                expire_host_snapshot_object.deleting = False
                expire_host_snapshot_object.save(update_fields=['is_cdp', 'deleting'])

            SpaceCollectionWorker.set_host_snapshot_deleting_and_collection_space_later(expire_host_snapshot_object)
            self._update_host_finished(task_ext_info_key)

        # 该快照点需要回收为可见的快照点
        new_snapshot_datetime, last_expire_host_snapshot, new_snapshot_invisible = \
            self._get_new_snapshot_datetime_and_last_expire_host_snapshot_object()

        task_ext_info_key = 'last_host_snapshot_{}'.format(last_expire_host_snapshot.id)
        task_ext_info = self._get_or_create_task_ext_info(task_ext_info_key, last_expire_host_snapshot.id,
                                                          new_snapshot_datetime)
        if not task_ext_info['finished']:
            task_ext_info = self._get_or_create_last_disk_snapshot_per_disk_ext_info(task_ext_info_key)

            # 不是最后一个cdp文件，回收为不可见的快照点
            for disk_ident in task_ext_info['disks'].keys():
                cdp_disk_snapshots = task_ext_info['disks'][disk_ident]['cdp_disk_snapshots']
                self._merge_cdp_disk_snapshots_to_invisible_normal_disk_snapshots(task_ext_info_key, cdp_disk_snapshots,
                                                                                  last_expire_host_snapshot.id)

            for disk_ident in task_ext_info['last_disks'].keys():
                self._merge_cdp_disk_snapshots_to_visible_normal_disk_snapshots(
                    task_ext_info_key, disk_ident, new_snapshot_datetime, last_expire_host_snapshot.id)

            new_host_snapshot_object = self._generate_new_host_snapshot_object(
                new_snapshot_datetime, last_expire_host_snapshot, task_ext_info['last_disks'])

            if new_snapshot_invisible:
                SpaceCollectionWorker.set_host_snapshot_deleting_and_collection_space_later(new_host_snapshot_object)

            if last_expire_host_snapshot.deleting:
                # 全回收，什么事儿都不干
                pass
            else:
                # 非全回收，需要新建一个cdp_host_snapshot
                for disk_ident in task_ext_info['last_disks'].keys():
                    base_disk_snapshot_id = self._get_disk_snapshot_by_host_snapshot_id_and_disk_ident(
                        new_host_snapshot_object.id, disk_ident)
                    task_object = CdpMergeSubTask.create_sub_task_create_no_data_snapshot(
                        self._task_object.schedule, self._task_object.remote_schedule,
                        self._task_object.cluster_schedule, new_host_snapshot_object.id,
                        task_ext_info['last_disks'][disk_ident]['empty_disk_snapshot_ident'], base_disk_snapshot_id)
                    if not CdpMergeSubTask(task_object).work():
                        xlogging.raise_and_logging_error(
                            r'内部异常，代码2374', 'call sub_task_create_no_data_snapshot failed {}'.format(task_object.id))

                # 刷新数据库缓存
                last_expire_host_snapshot = _check_host_snapshot_is_cdp(last_expire_host_snapshot.id)
                empty_host_snapshot_object = self._generate_empty_host_snapshot_object(
                    new_host_snapshot_object, task_ext_info['last_disks'], last_expire_host_snapshot)

                running_cdp_task = self.get_running_task_using(last_expire_host_snapshot)
                if running_cdp_task is not None:
                    running_cdp_task.host_snapshot = empty_host_snapshot_object
                    running_cdp_task.save(update_fields=['host_snapshot'])

            last_expire_host_snapshot.is_cdp = False
            last_expire_host_snapshot.deleting = False
            last_expire_host_snapshot.save(update_fields=['is_cdp', 'deleting'])
            SpaceCollectionWorker.set_host_snapshot_deleting_and_collection_space_later(last_expire_host_snapshot)

            self._update_host_finished(task_ext_info_key)

        self._task_object.set_finished()
        return True

    def _get_or_create_last_disk_snapshot_per_disk_ext_info(self, info_key):
        ext_info = json.loads(self._task_object.ext_info)
        task_ext_info = ext_info[info_key]

        if 'last_disks' in task_ext_info.keys():
            return task_ext_info

        task_ext_info['last_disks'] = dict()

        for disk_ident in task_ext_info['disks'].keys():
            task_ext_info['last_disks'][disk_ident] = {
                'disk_snapshot_id': None, 'new_disk_snapshot_ident': uuid.uuid4().hex, 'begin_timestamp': None,
                'finished': False, 'empty_disk_snapshot_ident': uuid.uuid4().hex}

            cdp_disk_snapshots = task_ext_info['disks'][disk_ident]['cdp_disk_snapshots']
            if len(cdp_disk_snapshots) == 0:
                continue

            last_one = cdp_disk_snapshots.pop()
            task_ext_info['last_disks'][disk_ident]['disk_snapshot_id'] = last_one['disk_snapshot_id']

        self._task_object.ext_info = json.dumps(ext_info, ensure_ascii=False)
        self._task_object.save(update_fields=['ext_info'])

        return task_ext_info

    def _merge_cdp_disk_snapshots_to_visible_normal_disk_snapshots(
            self, task_ext_info_key, disk_ident, new_snapshot_datetime, host_snapshot_id):
        schedule = self._task_object.schedule
        remote_schedule = self._task_object.remote_schedule
        cluster_schedule = self._task_object.cluster_schedule
        while True:
            last_disk_snapshot, base_disk_snapshot_id = \
                self._get_last_disk_snapshot_from_ext_info(task_ext_info_key, disk_ident)

            if last_disk_snapshot['finished']:
                break

            disk_snapshot_id = last_disk_snapshot['disk_snapshot_id']
            new_disk_snapshot_ident = last_disk_snapshot['new_disk_snapshot_ident']

            if disk_snapshot_id is None:
                # 没有CDP文件，需要创建一个无增量数据的快照点，并修改子快照文件的依赖
                task_object = CdpMergeSubTask.create_sub_task_create_no_data_snapshot(
                    schedule, remote_schedule, cluster_schedule,
                    host_snapshot_id, new_disk_snapshot_ident, base_disk_snapshot_id)

                self._set_last_disk_snapshot_finished(task_ext_info_key, disk_ident)

                if not CdpMergeSubTask(task_object).work():
                    xlogging.raise_and_logging_error(
                        r'内部异常，代码2371', 'call sub_task_create_no_data_snapshot failed {}'.format(task_object.id))

                break  # 处理完毕

            # 有CDP文件，处理开始
            _check_disk_snapshot_is_cdp(disk_snapshot_id)
            _check_disk_snapshot_parent_is_not_cdp(disk_snapshot_id)
            # 查找该CDP文件在 new_snapshot_datetime 前是否有其他依赖
            new_snapshot_timestamp = new_snapshot_datetime.timestamp()
            disk_snapshot_next_timestamp = last_disk_snapshot['begin_timestamp']
            child_disk_snapshot_with_intercept = self.get_child_disk_snapshot_with_intercept(
                disk_snapshot_id, disk_snapshot_next_timestamp)
            # 如果在新主机快照时间后就不处理
            if child_disk_snapshot_with_intercept is not None:
                child_disk_snapshot_with_intercept = child_disk_snapshot_with_intercept \
                    if child_disk_snapshot_with_intercept.parent_timestamp < new_snapshot_timestamp else None

            if child_disk_snapshot_with_intercept is None:  # 没有其他依赖
                host_snapshot_object = HostSnapshot.objects.get(id=host_snapshot_id)
                disk_snapshot_object = DiskSnapshot.objects.get(id=disk_snapshot_id)
                if host_snapshot_object.deleting or \
                        (disk_snapshot_object.cdp_info.last_timestamp <= new_snapshot_timestamp):
                    # 全文件回收
                    child_disk_snapshot_without_intercept = \
                        self.get_child_disk_snapshot_without_intercept(disk_snapshot_id)
                    task_object = CdpMergeSubTask.create_sub_task_without_intercept(
                        schedule, remote_schedule, cluster_schedule, host_snapshot_id,
                        child_disk_snapshot_without_intercept,
                        disk_snapshot_id, disk_snapshot_next_timestamp, True, new_disk_snapshot_ident)
                else:
                    # 部分文件回收，计算实际的timestamp
                    cut_timestamp = GetDiskSnapshot.get_snapshot_timestamp(
                        disk_snapshot_object.image_path, new_snapshot_timestamp)
                    task_object = CdpMergeSubTask.create_sub_task_with_intercept(
                        schedule, remote_schedule, cluster_schedule, host_snapshot_id, None, disk_snapshot_id,
                        disk_snapshot_next_timestamp, cut_timestamp, True, new_disk_snapshot_ident)
            else:  # 有其他依赖，先回收部分
                self._update_last_disk_snapshot_next_timestamp(task_ext_info_key, disk_ident,
                                                               child_disk_snapshot_with_intercept.parent_timestamp)
                task_object = CdpMergeSubTask.create_sub_task_with_intercept(
                    schedule, remote_schedule, cluster_schedule, host_snapshot_id,
                    child_disk_snapshot_with_intercept.id,
                    disk_snapshot_id, disk_snapshot_next_timestamp,
                    child_disk_snapshot_with_intercept.parent_timestamp)

            if child_disk_snapshot_with_intercept is None:
                self._set_last_disk_snapshot_finished(task_ext_info_key, disk_ident)

            if not CdpMergeSubTask(task_object).work():
                xlogging.raise_and_logging_error(
                    r'内部异常，代码2373', 'run CdpMergeSubTask failed {}'.format(task_object.id))

    @staticmethod
    def get_running_task_using(last_expire_host_snapshot):
        return CDPTask.objects.filter(host_snapshot=last_expire_host_snapshot, finish_datetime__isnull=True).first()

    @staticmethod
    def _get_disk_snapshot_by_host_snapshot_id_and_disk_ident(host_snapshot_id, disk_ident):
        host_snapshot_object = HostSnapshot.objects.get(id=host_snapshot_id)
        for disk_snapshot in host_snapshot_object.disk_snapshots.all():
            if disk_snapshot.disk.ident == disk_ident:
                return disk_snapshot.id
        xlogging.raise_and_logging_error(
            '内部异常，代码2375', '_get_disk_snapshot_by_host_snapshot_id_and_disk_ident failed {} {}'
                .format(host_snapshot_id, disk_ident))

    @staticmethod
    @xlogging.convert_exception_to_value(None)
    def _clean_remain_in_task(host_snapshot):
        if host_snapshot is None:
            return
        tasks = list(SpaceCollectionTask.objects.filter(
            host_snapshot=host_snapshot, type=SpaceCollectionTask.TYPE_CDP_MERGE_SUB))
        for task in tasks:
            _logger.warning(r'_clean_remain_in_task clean task {} host_snapshot {}'.format(task.id, host_snapshot.id))
            task.host_snapshot = None
            task.save(update_fields=['host_snapshot'])
        host_snapshot.delete()

    def _generate_new_host_snapshot_object(self, new_snapshot_datetime, last_expire_host_snapshot, last_disks):
        rand_new_snapshot_datetime = new_snapshot_datetime - timedelta(microseconds=random.randint(1, 100000))

        if last_expire_host_snapshot.cluster_schedule:
            new_host_snapshot_object = HostSnapshot.objects.create(
                host=last_expire_host_snapshot.host, start_datetime=rand_new_snapshot_datetime,
                finish_datetime=datetime.now(), successful=True, ext_info=last_expire_host_snapshot.ext_info,
                display_status=last_expire_host_snapshot.display_status, is_cdp=False,
                cluster_schedule=last_expire_host_snapshot.cluster_schedule, cluster_visible=True,
                cluster_finish_datetime=datetime.now(),
            )
        else:
            new_host_snapshot_object = HostSnapshot.objects.create(
                host=last_expire_host_snapshot.host, start_datetime=rand_new_snapshot_datetime,
                finish_datetime=datetime.now(), successful=True, ext_info=last_expire_host_snapshot.ext_info,
                display_status=last_expire_host_snapshot.display_status, is_cdp=False,
                schedule=last_expire_host_snapshot.schedule, remote_schedule=last_expire_host_snapshot.remote_schedule
            )
        _logger.info(r'_generate_new_host_snapshot_object {} {} -> {}'.format(
            new_host_snapshot_object.id,
            new_snapshot_datetime.strftime(xdatetime.FORMAT_WITH_MICROSECOND),
            rand_new_snapshot_datetime.strftime(xdatetime.FORMAT_WITH_MICROSECOND)))

        for disk_ident in last_disks.keys():
            disk_snapshot_ident = last_disks[disk_ident]['new_disk_snapshot_ident']
            disk_snapshot_object = DiskSnapshot.objects.get(ident=disk_snapshot_ident)
            if disk_snapshot_object.host_snapshot is not None:
                _logger.warning(r'_generate_new_host_snapshot_object disk_snapshot_object had host_snapshot {} {}'
                                .format(disk_snapshot_object.id, disk_snapshot_object.host_snapshot.id))
                old_host_snapshot = disk_snapshot_object.host_snapshot
            else:
                old_host_snapshot = None

            disk_snapshot_object.host_snapshot = new_host_snapshot_object
            disk_snapshot_object.save(update_fields=['host_snapshot'])
            self._clean_remain_in_task(old_host_snapshot)

        return new_host_snapshot_object

    @staticmethod
    def _generate_empty_host_snapshot_object(new_host_snapshot_object, last_disks, last_expire_host_snapshot):
        if new_host_snapshot_object.cluster_schedule:
            empty_host_snapshot_object = HostSnapshot.objects.create(
                host=new_host_snapshot_object.host, start_datetime=new_host_snapshot_object.start_datetime,
                finish_datetime=datetime.now(), successful=True, ext_info=new_host_snapshot_object.ext_info,
                display_status=new_host_snapshot_object.display_status, is_cdp=True,
                cluster_schedule=new_host_snapshot_object.cluster_schedule, cluster_visible=True,
                cluster_finish_datetime=datetime.now(),
            )
        else:
            empty_host_snapshot_object = HostSnapshot.objects.create(
                host=new_host_snapshot_object.host, start_datetime=new_host_snapshot_object.start_datetime,
                finish_datetime=datetime.now(), successful=True, ext_info=new_host_snapshot_object.ext_info,
                display_status=new_host_snapshot_object.display_status, is_cdp=True,
                schedule=new_host_snapshot_object.schedule, remote_schedule=new_host_snapshot_object.remote_schedule,
            )

        HostSnapshotCDP.objects.create(host_snapshot=empty_host_snapshot_object,
                                       last_datetime=last_expire_host_snapshot.cdp_info.last_datetime,
                                       first_datetime=last_expire_host_snapshot.cdp_info.first_datetime)
        for disk_ident in last_disks.keys():
            disk_snapshot_ident = last_disks[disk_ident]['empty_disk_snapshot_ident']
            disk_snapshot_object = DiskSnapshot.objects.get(ident=disk_snapshot_ident)
            if disk_snapshot_object.host_snapshot is not None:
                _logger.warning(r'_generate_empty_host_snapshot_object disk_snapshot_object had host_snapshot {} {}'
                                .format(disk_snapshot_object.id, disk_snapshot_object.host_snapshot.id))
            disk_snapshot_object.host_snapshot = empty_host_snapshot_object
            disk_snapshot_object.save(update_fields=['host_snapshot'])

        return empty_host_snapshot_object

    @staticmethod
    def _get_child_disk_snapshot_objects_by_disk_snapshot_id(base_disk_snapshot_id):
        base_disk_snapshot_object = DiskSnapshot.objects.get(id=base_disk_snapshot_id)
        return base_disk_snapshot_object.child_snapshots.filter(merged=False).all()

    def _merge_cdp_disk_snapshots_to_invisible_normal_disk_snapshots(self, task_ext_info_key, cdp_disk_snapshots,
                                                                     host_snapshot_id):
        for disk_snapshot in cdp_disk_snapshots:
            while True:
                disk_snapshot_id = disk_snapshot['disk_snapshot_id']

                ext_info = json.loads(self._task_object.ext_info)
                disk_snapshot_finished, disk_snapshot_next_timestamp = self.get_disk_snapshot_from_ext_info(
                    ext_info, task_ext_info_key, disk_snapshot_id, self._task_object.id)

                if disk_snapshot_finished:
                    break

                _check_disk_snapshot_is_cdp(disk_snapshot_id)
                _check_disk_snapshot_parent_is_not_cdp(disk_snapshot_id)

                child_disk_snapshot_with_intercept = self.get_child_disk_snapshot_with_intercept(
                    disk_snapshot_id, disk_snapshot_next_timestamp)

                if child_disk_snapshot_with_intercept is None:  # 全文件回收
                    child_disk_snapshot_without_intercept = \
                        self.get_child_disk_snapshot_without_intercept(disk_snapshot_id)
                    task_object = CdpMergeSubTask.create_sub_task_without_intercept(
                        self._task_object.schedule, self._task_object.remote_schedule,
                        self._task_object.cluster_schedule, host_snapshot_id,
                        child_disk_snapshot_without_intercept, disk_snapshot_id, disk_snapshot_next_timestamp)
                else:  # 部分文件回收
                    self._update_disk_next_timestamp(task_ext_info_key, disk_snapshot_id,
                                                     child_disk_snapshot_with_intercept.parent_timestamp)
                    task_object = CdpMergeSubTask.create_sub_task_with_intercept(
                        self._task_object.schedule, self._task_object.remote_schedule,
                        self._task_object.cluster_schedule, host_snapshot_id,
                        child_disk_snapshot_with_intercept.id, disk_snapshot_id, disk_snapshot_next_timestamp,
                        child_disk_snapshot_with_intercept.parent_timestamp)

                if child_disk_snapshot_with_intercept is None:
                    self._set_disk_finished(task_ext_info_key, disk_snapshot_id)

                if not CdpMergeSubTask(task_object).work():
                    xlogging.raise_and_logging_error(
                        r'内部异常，代码2370', 'run CdpMergeSubTask failed {}'.format(task_object.id))

    @staticmethod
    def set_disk_finished(ext_info, info_key, disk_snapshot_id, task_id):
        task_ext_info = ext_info[info_key]
        for disk_ident in task_ext_info['disks'].keys():
            cdp_disk_snapshots = task_ext_info['disks'][disk_ident]['cdp_disk_snapshots']
            for disk_snapshot in cdp_disk_snapshots:
                if disk_snapshot['disk_snapshot_id'] == disk_snapshot_id:
                    disk_snapshot['finished'] = True
                    return

        xlogging.raise_and_logging_error(
            '内部异常，代码2361', 'update_disk_finished never happened '
                           '{} {} {}'.format(task_id, info_key, disk_snapshot_id))

    def _set_disk_finished(self, info_key, disk_snapshot_id):
        ext_info = json.loads(self._task_object.ext_info)
        self.set_disk_finished(ext_info, info_key, disk_snapshot_id, self._task_object.id)
        self._task_object.ext_info = json.dumps(ext_info, ensure_ascii=False)
        self._task_object.save(update_fields=['ext_info'])

    @staticmethod
    def update_disk_next_timestamp(ext_info, info_key, disk_snapshot_id, next_timestamp, task_id):
        task_ext_info = ext_info[info_key]
        for disk_ident in task_ext_info['disks'].keys():
            cdp_disk_snapshots = task_ext_info['disks'][disk_ident]['cdp_disk_snapshots']
            for disk_snapshot in cdp_disk_snapshots:
                if disk_snapshot['disk_snapshot_id'] == disk_snapshot_id:
                    disk_snapshot['next_timestamp'] = next_timestamp
                    return

        xlogging.raise_and_logging_error(
            '内部异常，代码2360', 'update_disk_next_timestamp never happened '
                           '{} {} {}'.format(task_id, info_key, disk_snapshot_id))

    def _update_disk_next_timestamp(self, info_key, disk_snapshot_id, next_timestamp):
        ext_info = json.loads(self._task_object.ext_info)
        self.update_disk_next_timestamp(ext_info, info_key, disk_snapshot_id, next_timestamp, self._task_object.id)
        self._task_object.ext_info = json.dumps(ext_info, ensure_ascii=False)
        self._task_object.save(update_fields=['ext_info'])

    def _update_last_disk_snapshot_next_timestamp(self, info_key, disk_ident, next_timestamp):
        ext_info = json.loads(self._task_object.ext_info)
        task_ext_info = ext_info[info_key]
        task_ext_info['last_disks'][disk_ident]['begin_timestamp'] = next_timestamp
        self._task_object.ext_info = json.dumps(ext_info, ensure_ascii=False)
        self._task_object.save(update_fields=['ext_info'])

    def _set_last_disk_snapshot_finished(self, info_key, disk_ident):
        ext_info = json.loads(self._task_object.ext_info)
        task_ext_info = ext_info[info_key]
        task_ext_info['last_disks'][disk_ident]['finished'] = True
        self._task_object.ext_info = json.dumps(ext_info, ensure_ascii=False)
        self._task_object.save(update_fields=['ext_info'])

    def _get_last_disk_snapshot_from_ext_info(self, info_key, disk_ident):
        ext_info = json.loads(self._task_object.ext_info)
        task_ext_info = ext_info[info_key]
        return (task_ext_info['last_disks'][disk_ident],
                task_ext_info['disks'][disk_ident]['base_disk_snapshot_object_id'])

    @staticmethod
    def get_disk_snapshot_from_ext_info(ext_info, info_key, disk_snapshot_id, task_id):
        task_ext_info = ext_info[info_key]
        for disk_ident in task_ext_info['disks'].keys():
            cdp_disk_snapshots = task_ext_info['disks'][disk_ident]['cdp_disk_snapshots']
            for disk_snapshot in cdp_disk_snapshots:
                if disk_snapshot['disk_snapshot_id'] == disk_snapshot_id:
                    return disk_snapshot['finished'], disk_snapshot['next_timestamp']

        xlogging.raise_and_logging_error(
            '内部异常，代码2359', 'get_disk_snapshot_from_ext_info never happened '
                           '{} {} {}'.format(task_id, info_key, disk_snapshot_id))

    @staticmethod
    def get_all_child_disk_snapshot_without_intercept(disk_snapshot_id):
        disk_snapshot_object = DiskSnapshot.objects.get(id=disk_snapshot_id)
        return disk_snapshot_object.child_snapshots.filter(parent_timestamp__isnull=True, merged=False).all()

    @staticmethod
    def get_child_disk_snapshot_without_intercept(disk_snapshot_id):
        disk_snapshot_object = DiskSnapshot.objects.get(id=disk_snapshot_id)
        return disk_snapshot_object.child_snapshots.filter(parent_timestamp__isnull=True, merged=False).first()

    @staticmethod
    def get_child_disk_snapshot_with_intercept(disk_snapshot_id, next_timestamp):
        disk_snapshot_object = DiskSnapshot.objects.get(id=disk_snapshot_id)
        child_snapshot = disk_snapshot_object.child_snapshots.filter(parent_timestamp__isnull=False, merged=False)
        if next_timestamp is not None:
            child_snapshot = child_snapshot.filter(parent_timestamp__gte=next_timestamp)
        child_snapshot = child_snapshot.order_by('parent_timestamp').first()

        return child_snapshot

    def _check_sub_task(self):
        sub_task = SpaceCollectionTask.objects.filter(
            type=SpaceCollectionTask.TYPE_CDP_MERGE_SUB, schedule=self._task_object.schedule,
            remote_schedule=self._task_object.remote_schedule, cluster_schedule=self._task_object.cluster_schedule,
            finish_datetime__isnull=True).first()

        if sub_task is not None:
            xlogging.raise_and_logging_error(
                r'还有子任务未完成', r'_check_sub_task {} in {} is not finished'.format(sub_task.id, self._task_object.id))

    def _get_new_snapshot_datetime_and_last_expire_host_snapshot_object(self):
        ext_info = json.loads(self._task_object.ext_info)
        return (
            xdatetime.string2datetime(ext_info['new_snapshot_datetime']),
            self._task_object.host_snapshot,
            ext_info.get('new_snapshot_invisible', False)
        )

    def _get_expire_host_snapshot_ids(self):
        ext_info = json.loads(self._task_object.ext_info)
        return ext_info['expire_host_snapshot_ids']

    def _update_host_finished(self, info_key):
        ext_info = json.loads(self._task_object.ext_info)
        ext_info[info_key]['finished'] = True
        self._task_object.ext_info = json.dumps(ext_info, ensure_ascii=False)
        self._task_object.save(update_fields=['ext_info'])

    @staticmethod
    def get_or_create_task_ext_info(ext_info, info_key, host_snapshot_id, new_snapshot_datetime=None):
        if info_key in ext_info.keys():
            return False

        host_snapshot_object = _check_host_snapshot_is_cdp(host_snapshot_id)
        disks = dict()
        for base_disk_snapshot_object in host_snapshot_object.disk_snapshots.all():
            disks[base_disk_snapshot_object.disk.ident] = {
                'base_disk_snapshot_object_id': base_disk_snapshot_object.id, 'cdp_disk_snapshots': []}
            cdp_disk_snapshots = CDPHostSnapshotSpaceCollectionMergeTask. \
                _get_cdp_disk_snapshots_from_base_disk_snapshot(base_disk_snapshot_object, None,
                                                                None if host_snapshot_object.deleting
                                                                else new_snapshot_datetime)
            for cdp_disk_snapshot in cdp_disk_snapshots:
                disks[cdp_disk_snapshot.disk.ident]['cdp_disk_snapshots'].append(
                    {'disk_snapshot_id': cdp_disk_snapshot.id, 'finished': False, 'next_timestamp': None})

        ext_info[info_key] = {'disks': disks, 'finished': False}
        return True

    def _get_or_create_task_ext_info(self, info_key, host_snapshot_id, new_snapshot_datetime=None):
        ext_info = json.loads(self._task_object.ext_info)

        if self.get_or_create_task_ext_info(ext_info, info_key, host_snapshot_id, new_snapshot_datetime):
            self._task_object.ext_info = json.dumps(ext_info, ensure_ascii=False)
            self._task_object.save(update_fields=['ext_info'])

        return ext_info[info_key]

    @staticmethod
    def _get_cdp_disk_snapshots_from_base_disk_snapshot(base_disk_snapshot_object, begin_datetime, end_datetime):
        cdp_disk_snapshots = list()
        begin_timestamp = None
        end_timestamp = None
        if begin_datetime is not None:
            begin_timestamp = begin_datetime.timestamp()
        if end_datetime is not None:
            end_timestamp = end_datetime.timestamp()

        while base_disk_snapshot_object is not None:
            child_snapshots = base_disk_snapshot_object.child_snapshots.order_by('cdp_info__first_timestamp').all()
            base_disk_snapshot_object = None
            for child_snapshot in child_snapshots:
                if child_snapshot.is_cdp and (child_snapshot.parent_timestamp is None) \
                        and ((begin_timestamp is None) or child_snapshot.cdp_info.first_timestamp > begin_timestamp) \
                        and ((end_datetime is None) or child_snapshot.cdp_info.first_timestamp < end_timestamp):
                    cdp_disk_snapshots.append(child_snapshot)
                    base_disk_snapshot_object = child_snapshot
                    break

        return cdp_disk_snapshots


class DeleteCdpObjectTask(object):
    def __init__(self, task_object):
        if task_object.type != SpaceCollectionTask.TYPE_DELETE_CDP_OBJECT:
            xlogging.raise_and_logging_error(
                '内部异常，代码2368', 'DeleteCdpObjectTask incorrect type {} {}'.format(task_object.id, task_object.type))
        self._task_object = task_object

    @xlogging.convert_exception_to_value(False)
    def work(self):
        ext_info = json.loads(self._task_object.ext_info)
        disk_snapshot_object_id = ext_info['disk_snapshot_object_id']
        disk_snapshot_object_object = DiskSnapshot.objects.get(id=disk_snapshot_object_id)
        disk_snapshot_object_object.set_deleting()
        DiskSnapshotLocker.set_merged(disk_snapshot_object_object)
        # 创建删除CDP文件的任务
        delete_cdp_file_task_object = DeleteCdpFileTask.create(disk_snapshot_object_object.image_path)
        # 有重试机制，不需要检查是否失败
        DeleteCdpFileTask(delete_cdp_file_task_object).work()

        self._task_object.set_finished()
        return True

    @staticmethod
    def create(schedule_object, disk_snapshot_object):
        if not disk_snapshot_object.is_cdp:
            xlogging.raise_and_logging_error(
                r'内部异常，代码2369', 'disk_snapshot_object.is_cdp false {}'.format(disk_snapshot_object.id))
        ext_info = {'disk_snapshot_object_id': disk_snapshot_object.id}
        disk_snapshot_object.set_deleting()
        return SpaceCollectionTask.objects.create(type=SpaceCollectionTask.TYPE_DELETE_CDP_OBJECT,
                                                  schedule=schedule_object,
                                                  ext_info=json.dumps(ext_info, ensure_ascii=False))


class DeleteCdpFileTask(object):
    def __init__(self, task_object):
        if task_object.type != SpaceCollectionTask.TYPE_DELETE_CDP_FILE:
            xlogging.raise_and_logging_error(
                '内部异常，代码2355', 'DeleteCdpFileTask incorrect type {} {}'.format(
                    task_object.id, task_object.type))
        self._task_object = task_object

    @xlogging.convert_exception_to_value(False)
    def work(self):
        ext_info = json.loads(self._task_object.ext_info)
        path = ext_info['path']

        if not self.do_del(path):
            return False
        self._task_object.set_finished()
        return True

    @staticmethod
    @xlogging.convert_exception_to_value(False)
    def do_del(path):
        checker = CheckDir(os.path.split(path)[0])
        with checker:
            if boxService.box_service.isFileExist(path):
                boxService.box_service.remove(path)
                rm_cmd = r'rm -f {path}_*.readmap; rm -f {path}_*.map'.format(path=path)
                _logger.info(r'DeleteCdpFileTask : {}'.format(rm_cmd))
                os.system(rm_cmd)
        return True

    @staticmethod
    def create(image_path):
        return SpaceCollectionTask.objects.create(
            type=SpaceCollectionTask.TYPE_DELETE_CDP_FILE,
            ext_info=json.dumps({'path': image_path}, ensure_ascii=False))


class MergeDiskSnapshotTask(object):
    def __init__(self, task_object):
        if task_object.type != SpaceCollectionTask.TYPE_MERGE_SNAPSHOT:
            xlogging.raise_and_logging_error(
                '内部异常，代码2390', 'MergeDiskSnapshotTask incorrect type {} {}'.format(
                    task_object.id, task_object.type))
        self._task_object = task_object

    def _check_snapshot_object(self, snapshot_object, prev_snapshot_object):
        if snapshot_object.parent_snapshot.id != prev_snapshot_object.id:
            xlogging.raise_and_logging_error(
                '内部异常，代码2392', 'MergeDiskSnapshotTask _check_parent_snapshot_object 1 {} failed {} --- {}'.format(
                    self._task_object.id, snapshot_object, prev_snapshot_object)
            )

        if prev_snapshot_object.merged:
            xlogging.raise_and_logging_error(
                '内部异常，代码2393', 'MergeDiskSnapshotTask _check_parent_snapshot_object 2 {} failed {} --- {}'.format(
                    self._task_object.id, snapshot_object, prev_snapshot_object)
            )

        if prev_snapshot_object.is_cdp:
            xlogging.raise_and_logging_error(
                '内部异常，代码2394', 'MergeDiskSnapshotTask _check_parent_snapshot_object 3 {} failed {} --- {}'.format(
                    self._task_object.id, snapshot_object, prev_snapshot_object)
            )

        if snapshot_object.merged:
            xlogging.raise_and_logging_error(
                '内部异常，代码2395', 'MergeDiskSnapshotTask _check_parent_snapshot_object 4 {} failed {} --- {}'.format(
                    self._task_object.id, snapshot_object, prev_snapshot_object)
            )

    def _update_ext_info(self, ext_info):
        self._task_object.ext_info = json.dumps(ext_info, ensure_ascii=False)
        self._task_object.save(update_fields=['ext_info'])

    def _do_merge_logic(self, snapshot_object, prev_snapshot_object, new_snapshot_object):
        task_name = 'merge_disk_task' + str(self._task_object.id)
        try:
            DiskSnapshotLocker.unlock_file(snapshot_object.image_path, snapshot_object.ident, task_name)
            DiskSnapshotLocker.lock_file(snapshot_object.image_path, snapshot_object.ident, task_name)
            CompressTaskThreading().remove_task_by_disk_snapshot(snapshot_object.image_path, snapshot_object.ident)

            validator_list = [GetSnapshotList.is_disk_snapshot_object_exist,
                              GetSnapshotList.is_disk_snapshot_file_exist]
            prev_disk_snapshots = GetSnapshotList.query_snapshots_by_snapshot_object(prev_snapshot_object,
                                                                                     validator_list)
            if len(prev_disk_snapshots) == 0:
                xlogging.raise_and_logging_error(
                    r'内部异常，代码2391', r'MergeDiskSnapshotTask prev_snapshot_object invalid {} {}'.format(
                        self._task_object.id, prev_snapshot_object.id))

            params = {
                'disk_bytes': new_snapshot_object.bytes,
                'new_snapshot_qcow_file': new_snapshot_object.image_path,
                'new_snapshot_qcow_ident': new_snapshot_object.ident,
                'current_snapshot_qcow_file': snapshot_object.image_path,
                'current_snapshot_qcow_ident': snapshot_object.ident,
                'prev_disk_snapshots': []
            }

            try:
                for disk_snapshot in prev_disk_snapshots:
                    DiskSnapshotLocker.lock_file(disk_snapshot.path, disk_snapshot.snapshot, task_name)
                    CompressTaskThreading().update_task_by_disk_snapshot(disk_snapshot.path, disk_snapshot.snapshot)
                    params['prev_disk_snapshots'].append({'path': disk_snapshot.path, 'ident': disk_snapshot.snapshot})

                boxService.box_service.mergeQcowFile(json.dumps(params, ensure_ascii=False))
            except Exception:
                CdpMergeSubTask.delete_normal_snapshot(new_snapshot_object.ident)
                raise
            finally:
                for disk_snapshot in prev_disk_snapshots:
                    DiskSnapshotLocker.unlock_file(disk_snapshot.path, disk_snapshot.snapshot, task_name, True)

        finally:
            DiskSnapshotLocker.unlock_file(snapshot_object.image_path, snapshot_object.ident, task_name)

    @xlogging.convert_exception_to_value(False)
    def work(self):
        ext_info = json.loads(self._task_object.ext_info)
        prev_snapshot_object = DiskSnapshot.objects.get(ident=ext_info['prev_snapshot_ident'])
        snapshot_object = DiskSnapshot.objects.get(ident=ext_info['snapshot_ident'])
        new_snapshot_ident = ext_info['new_snapshot_ident']
        enable_fake = ext_info['enable_fake']

        if ext_info['stage'] == 0:
            self._check_snapshot_object(snapshot_object, prev_snapshot_object)

            CdpMergeSubTask.delete_normal_snapshot(new_snapshot_ident)
            new_snapshot_object = CdpMergeSubTask.create_new_disk_snapshot(
                prev_snapshot_object, new_snapshot_ident, snapshot_object.host_snapshot.start_datetime.timestamp())

            self._do_merge_logic(snapshot_object, prev_snapshot_object, new_snapshot_object)
            hash_file_path = snapshot_object.hash_path
            if boxService.box_service.isFileExist(hash_file_path):
                new_hash_file_path = new_snapshot_object.hash_path
                boxService.box_service.remove(new_hash_file_path)
                boxService.box_service.copy(new_hash_file_path, hash_file_path)
                os.system('sync')

            # 为新disk_snapshot 创建压缩任务
            _create_compress_task_by_merge_task(new_snapshot_object)
            ext_info['stage'] = 1
            ext_info['new_disk_snapshot_object_id'] = new_snapshot_object.id
            self._update_ext_info(ext_info)
        if ext_info['stage'] == 1:
            new_snapshot_object = DiskSnapshot.objects.get(ident=new_snapshot_ident)
            if DiskSnapshotLocker.set_merged(snapshot_object):
                NormalHostSnapshotSpaceCollectionTask.update_child_disk_snapshot_object_parent_to_special(
                    snapshot_object, new_snapshot_object)
                DeleteDiskSnapshotTask(DeleteDiskSnapshotTask.create(snapshot_object.image_path,
                                                                     snapshot_object.ident, enable_fake)).work()
            ext_info['stage'] = 2
            self._update_ext_info(ext_info)
        if ext_info['stage'] == 2:
            # 添加host_snapshot到回收任务
            new_host_snapshot = DiskSnapshot.objects.get(id=ext_info['new_disk_snapshot_object_id']).host_snapshot
            if new_host_snapshot.finish_datetime is None:
                new_host_snapshot.finish_datetime = datetime.now()
                new_host_snapshot.deleting = False
                new_host_snapshot.successful = True
                new_host_snapshot.save(update_fields=['finish_datetime', 'deleting', 'successful'])

            SpaceCollectionWorker.set_host_snapshot_deleting_and_collection_space_later(new_host_snapshot)

        self._task_object.set_finished()
        return True

    @staticmethod
    def create(disk_snapshot_object, enable_fake=True):
        snapshot_ident = disk_snapshot_object.ident
        prev_snapshot_ident = disk_snapshot_object.parent_snapshot.ident
        host_snapshot_object = disk_snapshot_object.host_snapshot
        for task in SpaceCollectionTask.objects.filter(
                host_snapshot=host_snapshot_object, type=SpaceCollectionTask.TYPE_MERGE_SNAPSHOT).all():
            if json.loads(task.ext_info)['snapshot_ident'] == snapshot_ident:
                return None

        new_snapshot_ident = uuid.uuid4().hex
        return SpaceCollectionTask.objects.create(
            host_snapshot=host_snapshot_object,
            type=SpaceCollectionTask.TYPE_MERGE_SNAPSHOT,
            ext_info=json.dumps(
                {
                    'prev_snapshot_ident': prev_snapshot_ident, 'snapshot_ident': snapshot_ident,
                    'enable_fake': enable_fake, 'new_snapshot_ident': new_snapshot_ident, 'stage': 0
                }, ensure_ascii=False)
        )


class DeleteDiskSnapshotTask(object):
    def __init__(self, task_object):
        if task_object.type != SpaceCollectionTask.TYPE_DELETE_SNAPSHOT:
            xlogging.raise_and_logging_error(
                '内部异常，代码2356', 'DeleteDiskSnapshotTask incorrect type {} {}'.format(
                    task_object.id, task_object.type))
        self._task_object = task_object

    @xlogging.convert_exception_to_value(False)
    def work(self):
        ext_info = json.loads(self._task_object.ext_info)
        path = ext_info['path']
        ident = ext_info['ident']
        enable_fake = ext_info['enable_fake']
        task_name = 'delete_disk_task' + str(self._task_object.id)

        if not self.do_del(task_name, enable_fake, ident, path):
            return False
        self._task_object.set_finished()
        return True

    @staticmethod
    @xlogging.convert_exception_to_value(False)
    def do_del(task_name, enable_fake, ident, path):
        checker = CheckDir(os.path.split(path)[0])
        with checker:
            if boxService.box_service.isFileExist(path):
                try:
                    DiskSnapshotLocker.lock_file(path, ident, task_name, True)
                    need_unlock = True
                except DiskSnapshot.DoesNotExist:
                    _logger.warning(r'DeleteDiskSnapshotTask do_del can NOT query db_obj {} {} {}'.format(
                        task_name, ident, path))
                    need_unlock = False
                except Exception as e:
                    DiskSnapshotLocker.unlock_file(path, ident, task_name, True)
                    raise e

                try:
                    CompressTaskThreading().remove_task_by_disk_snapshot(path, ident)
                    boxService.box_service.deleteNormalDiskSnapshot(path, ident, enable_fake)
                    hash_file_path = '{}_{}.hash'.format(path, ident)
                    boxService.box_service.remove(hash_file_path)
                finally:
                    if need_unlock:
                        DiskSnapshotLocker.unlock_file(path, ident, task_name, True)

        return True

    @staticmethod
    def create(image_path, snapshot_ident, enable_fake=True):
        return SpaceCollectionTask.objects.create(
            type=SpaceCollectionTask.TYPE_DELETE_SNAPSHOT,
            ext_info=json.dumps(
                {'path': image_path, 'ident': snapshot_ident, 'enable_fake': enable_fake}, ensure_ascii=False))


class NormalHostSnapshotSpaceCollectionTask(object):
    def __init__(self, task_object):
        if task_object.type not in (SpaceCollectionTask.TYPE_NORMAL_DELETE, SpaceCollectionTask.TYPE_NORMAL_MERGE):
            xlogging.raise_and_logging_error(
                '内部异常，代码2354', 'NormalHostSnapshotSpaceCollectionTask incorrect type {} {}'.format(
                    task_object.id, task_object.type))
        self._task_object = task_object

    def _set_task_finished(self):
        host_snapshot = self._task_object.host_snapshot
        if host_snapshot.deleted or SpaceCollectionWorker.is_host_snapshot_object_failed(host_snapshot):
            self._task_object.set_finished()

            if host_snapshot.deleted:
                HostLog.objects.create(host=self._task_object.host_snapshot.host, type=HostLog.LOG_COLLECTION_SPACE,
                                       reason=json.dumps({"collection_task": self._task_object.id}, ensure_ascii=False))

    @staticmethod
    def _check_host_snapshot(host_snapshot_object):
        if host_snapshot_object.deleted:
            xlogging.raise_and_logging_error(
                r'客户端快照点已回收空间',
                'NormalHostSnapshotSpaceCollection _check_host_snapshot deleted {id} {host} {time}'.format(
                    id=host_snapshot_object.id, host=host_snapshot_object.host.display_name,
                    time=host_snapshot_object.start_datetime))

        if host_snapshot_object.finish_datetime is None:
            xlogging.raise_and_logging_error(
                r'客户端快照点正在生成中',
                'NormalHostSnapshotSpaceCollection _check_host_snapshot finish_datetime {id} {host} {time}'.format(
                    id=host_snapshot_object.id, host=host_snapshot_object.host.display_name,
                    time=host_snapshot_object.start_datetime))

        if not host_snapshot_object.successful:
            xlogging.raise_and_logging_error(
                r'客户端快照点未成功生成',
                'NormalHostSnapshotSpaceCollection _check_host_snapshot successful {id} {host} {time}'.format(
                    id=host_snapshot_object.id, host=host_snapshot_object.host.display_name,
                    time=host_snapshot_object.start_datetime))

    @staticmethod
    def _check_not_using(host_snapshot_object):
        migrate_task_object = MigrateTask.objects.filter(
            host_snapshot=host_snapshot_object,
            finish_datetime__isnull=True).first()
        if migrate_task_object is not None:
            xlogging.raise_and_logging_error(
                r'客户端快照点正在被迁移任务使用',
                'NormalHostSnapshotSpaceCollection _check_no_restore_using migrate({mid}) {id} {host} {time}'.format(
                    mid=migrate_task_object.id, id=host_snapshot_object.id, host=host_snapshot_object.host.display_name,
                    time=host_snapshot_object.start_datetime))

        restore_task_object = RestoreTask.objects.filter(host_snapshot=host_snapshot_object,
                                                         finish_datetime__isnull=True).first()
        if restore_task_object is not None:
            xlogging.raise_and_logging_error(
                r'客户端快照点正在被还原任务使用',
                'NormalHostSnapshotSpaceCollection _check_no_restore_using restore({rid}) {id} {host} {time}'
                    .format(rid=restore_task_object.id, id=host_snapshot_object.id,
                            host=host_snapshot_object.host.display_name,
                            time=host_snapshot_object.start_datetime))

        host_snapshot_share_object = HostSnapshotShare.objects.filter(
            host_snapshot_id=host_snapshot_object.id).first()
        if host_snapshot_share_object is not None:
            xlogging.raise_and_logging_error(
                r'客户端快照点正在被浏览中',
                'NormalHostSnapshotSpaceCollection _check_no_restore_using share({sid}) {id} {host} {time}'
                    .format(sid=host_snapshot_share_object.id, id=host_snapshot_object.id,
                            host=host_snapshot_object.host.display_name,
                            time=host_snapshot_object.start_datetime))

    def work(self):
        try:
            host_snapshot_object = self._task_object.host_snapshot
            self._check_host_snapshot(host_snapshot_object)
            self._check_not_using(host_snapshot_object)

            disks = host_snapshot_object.disk_snapshots.all()
            for disk in disks:
                try:
                    deal_disk_snapshot_task_object = self.work_disk(disk, self._task_object.type)
                    if deal_disk_snapshot_task_object is not None:
                        self.insert_delete_disk_snapshot_task_to_ext_info(deal_disk_snapshot_task_object)
                        if deal_disk_snapshot_task_object.type == SpaceCollectionTask.TYPE_DELETE_SNAPSHOT:
                            self.run_delete_disk_snapshot_task_object(deal_disk_snapshot_task_object)
                        elif deal_disk_snapshot_task_object.type == SpaceCollectionTask.TYPE_MERGE_SNAPSHOT:
                            self.run_merge_disk_snapshot_task_object(deal_disk_snapshot_task_object)
                        else:
                            raise Exception('unknown deal_disk_snapshot_task_object type : {} - {}'.format(
                                deal_disk_snapshot_task_object.id, deal_disk_snapshot_task_object.type))
                except Exception as e:
                    _logger.warning(r'delete disk snapshot {} in host snapshot {} failed : {}'
                                    .format(disk.ident, host_snapshot_object.id, e), exc_info=True)

            if self.is_all_disk_snapshot_merged(disks) and self.is_all_delete_disk_snapshot_task_finished():
                host_snapshot_object.deleted = True
                host_snapshot_object.save(update_fields=['deleted'])
        finally:
            self._set_task_finished()

    @staticmethod
    def run_delete_disk_snapshot_task_object(delete_disk_snapshot_task_object):
        task = DeleteDiskSnapshotTask(delete_disk_snapshot_task_object)
        task.work()

    @staticmethod
    def run_merge_disk_snapshot_task_object(merge_disk_snapshot_task_object):
        task = MergeDiskSnapshotTask(merge_disk_snapshot_task_object)
        task.work()

    def insert_delete_disk_snapshot_task_to_ext_info(self, delete_disk_snapshot_task_object):
        ext_info = json.loads(self._task_object.ext_info)

        tasks = ext_info.get('tasks', None)
        if tasks is None:
            tasks = list()
        tasks.append(delete_disk_snapshot_task_object.id)
        ext_info['tasks'] = tasks

        self._task_object.ext_info = json.dumps(ext_info, ensure_ascii=False)
        self._task_object.save(update_fields=['ext_info'])

    def is_all_delete_disk_snapshot_task_finished(self):
        ext_info = json.loads(self._task_object.ext_info)

        tasks = ext_info.get('tasks', None)
        if tasks is None:
            return True

        for task_id in tasks:
            task = SpaceCollectionTask.objects.get(id=task_id)
            if task.finish_datetime is None:
                return False

        return True

    @staticmethod
    def is_all_disk_snapshot_merged(disks):
        for disk in disks:
            if not disk.merged:
                return False
        return True

    @staticmethod
    def work_disk(disk_snapshot_object, task_type):
        can_merge, merge_type = NormalHostSnapshotSpaceCollectionTask.is_disk_snapshot_can_merge(disk_snapshot_object)
        if not can_merge:
            return None
        if merge_type == 'delete':
            with transaction.atomic():
                if DiskSnapshotLocker.set_merged(disk_snapshot_object):
                    DiskSnapshotHash.mergeHash2Children(disk_snapshot_object)
                    NormalHostSnapshotSpaceCollectionTask.update_child_disk_snapshot_object_parent(disk_snapshot_object)
                    return DeleteDiskSnapshotTask.create(disk_snapshot_object.image_path, disk_snapshot_object.ident)
        elif merge_type == 'merge':
            if task_type == SpaceCollectionTask.TYPE_NORMAL_MERGE:
                return MergeDiskSnapshotTask.create(disk_snapshot_object)
            else:
                return None  # 删除任务不支持合并操作，优化删除效率
        else:
            _logger.error(r'NormalHostSnapshotSpaceCollectionTask unknown merge_type')

    @staticmethod
    def update_child_disk_snapshot_object_parent(disk_snapshot_object):
        if disk_snapshot_object.parent_snapshot is None:
            return  # 原始备份点

        NormalHostSnapshotSpaceCollectionTask.update_child_disk_snapshot_object_parent_to_special(
            disk_snapshot_object, disk_snapshot_object.parent_snapshot)

    @staticmethod
    def update_child_disk_snapshot_object_parent_to_special(disk_snapshot_object, parent_disk_snapshot):
        child_disk_snapshot_objects = \
            NormalHostSnapshotSpaceCollectionTask.get_child_disk_snapshot_objects(disk_snapshot_object)
        for child_disk_snapshot_object in child_disk_snapshot_objects:
            child_disk_snapshot_object.parent_snapshot = parent_disk_snapshot
            child_disk_snapshot_object.save(update_fields=['parent_snapshot'])

    @staticmethod
    def is_disk_snapshot_can_merge(disk_snapshot_object):
        if disk_snapshot_object.merged:
            return False, None

        child_disk_snapshot_objects = \
            NormalHostSnapshotSpaceCollectionTask.get_child_disk_snapshot_objects(disk_snapshot_object)

        if disk_snapshot_object.is_base_snapshot:
            if len(child_disk_snapshot_objects) != 0:
                return False, None  # 原始备份在有子快照的情况下，不支持回收
            else:
                return True, 'delete'

        # 以下为非原始备份
        if len(child_disk_snapshot_objects) == 0:
            return True, 'delete'

        # 以下为有子快照
        # 统计与当前快照不在相同文件中的子快照
        in_diff_file_child_disk_snapshot_objects = \
            [x for x in child_disk_snapshot_objects if x.image_path != disk_snapshot_object.image_path]

        if disk_snapshot_object.parent_snapshot.image_path == disk_snapshot_object.image_path:
            # 父快照在同一个文件
            if len(in_diff_file_child_disk_snapshot_objects) != 0:
                return False, None  # 有子快照与当前快照不在同一文件中，不支持回收
            else:
                return True, 'delete'

        if disk_snapshot_object.parent_snapshot.is_cdp:
            return False, None  # 父快照是CDP文件，不支持回收

        # 以下为父快照为不同的qcow文件
        if disk_snapshot_object.parent_snapshot.bytes != disk_snapshot_object.bytes:
            return False, None  # 父快照的磁盘大小与当前快照不一致，不支持回收

        if len(in_diff_file_child_disk_snapshot_objects) == 0:
            return True, 'delete'

        if len(in_diff_file_child_disk_snapshot_objects) != len(child_disk_snapshot_objects):
            return False, None  # 子快照又有相同文件，又有不同文件，不支持回收

        return True, 'merge'

    @staticmethod
    def get_child_disk_snapshot_objects(disk_snapshot_object):
        child_disk_snapshot_objects = list()
        for snapshot in disk_snapshot_object.child_snapshots.all():
            if snapshot.merged:
                continue  # 子快照已经合并
            if not snapshot.is_cdp:
                if (snapshot.host_snapshot.finish_datetime is not None) and (not snapshot.host_snapshot.successful):
                    continue  # 子快照未成功生成
                if snapshot.host_snapshot.deleted:
                    continue  # 子快照已经删除

            child_disk_snapshot_objects.append(snapshot)

        return child_disk_snapshot_objects


class SpaceCollectionWorker(Thread):
    def __init__(self, interval):
        super(SpaceCollectionWorker, self).__init__()
        self.TIMER_INTERVAL_SECS = interval

    def run(self):
        time.sleep(15)
        DiskSnapshotLocker.unlock_files_by_task_name_prefix('delete_disk_task')

        while True:
            try:
                self.do_run()
                break
            except Exception as e:
                tb = traceback.format_exc()
                _logger.error(r'spaceCollection run Exception : {} {}'.format(tb, e))

    @xlogging.db_ex_wrap
    def do_run(self):
        while True:
            if os.path.exists(r'/dev/shm/pause_space_collection'):
                if xlogging.logger_traffic_control.is_logger_print(r'pause_space_collection', r'0'):
                    _logger.warning(r'pause_space_collection')
                time.sleep(1)
                continue
            else:
                time.sleep(self.TIMER_INTERVAL_SECS)

            SpaceCollectionWorker.deal_delete_tasks()

            SpaceCollectionWorker.check_normal_host_snapshot()
            SpaceCollectionWorker.normal_host_snapshot_space_collection()

            SpaceCollectionWorker.cdp_host_snapshot_space_collection()
            SpaceCollectionWorker.cdp_deleted_schedule_space_collection()

    @staticmethod
    def deal_delete_tasks():
        for task_object in SpaceCollectionTask.objects.filter(type=SpaceCollectionTask.TYPE_DELETE_CDP_OBJECT,
                                                              finish_datetime__isnull=True).all():
            DeleteCdpObjectTask(task_object).work()

        for task_object in SpaceCollectionTask.objects.filter(type=SpaceCollectionTask.TYPE_DELETE_CDP_FILE,
                                                              finish_datetime__isnull=True).all():
            DeleteCdpFileTask(task_object).work()

        for task_object in SpaceCollectionTask.objects.filter(type=SpaceCollectionTask.TYPE_DELETE_SNAPSHOT,
                                                              finish_datetime__isnull=True).all():
            DeleteDiskSnapshotTask(task_object).work()

        for task_object in SpaceCollectionTask.objects.filter(type=SpaceCollectionTask.TYPE_CDP_MERGE_SUB,
                                                              finish_datetime__isnull=True).all():
            CdpMergeSubTask(task_object).work()

        for task_object in SpaceCollectionTask.objects.filter(type=SpaceCollectionTask.TYPE_CDP_MERGE,
                                                              finish_datetime__isnull=True).all():
            CDPHostSnapshotSpaceCollectionMergeTask(task_object).work()
        for task_object in SpaceCollectionTask.objects.filter(type=SpaceCollectionTask.TYPE_CDP_DELETE,
                                                              finish_datetime__isnull=True).all():
            DeleteCdpHostSnapshotTask(task_object).work()
        for task_object in SpaceCollectionTask.objects.filter(type=SpaceCollectionTask.TYPE_MERGE_SNAPSHOT,
                                                              finish_datetime__isnull=True).all():
            MergeDiskSnapshotTask(task_object).work()

    @staticmethod
    def normal_host_snapshot_space_collection():
        normal_host_snapshot_space_collection_tasks = SpaceCollectionTask.objects.filter(
            type__in=(SpaceCollectionTask.TYPE_NORMAL_DELETE, SpaceCollectionTask.TYPE_NORMAL_MERGE),
            finish_datetime__isnull=True).order_by('-host_snapshot__start_datetime').all()
        for task_object in normal_host_snapshot_space_collection_tasks:
            try:
                worker = NormalHostSnapshotSpaceCollectionTask(task_object)
                worker.work()
            except Exception as e:
                _logger.warning(r'normal_host_snapshot_space_collection {} failed {}'.format(task_object.id, e))

    @staticmethod
    def cdp_host_snapshot_space_collection():
        for schedule_object in BackupTaskSchedule.objects.filter(
                deleted=False, cycle_type=BackupTaskSchedule.CYCLE_CDP).all():
            SpaceCollectionWorker.cdp_schedule_space_collection(schedule_object)
        for schedule_object in (
                ClusterBackupSchedule.objects.filter(
                    deleted=False).filter(cycle_type=BackupTaskSchedule.CYCLE_CDP).all()
        ):
            SpaceCollectionWorker.cluster_cdp_schedule_space_collection(schedule_object)
        for schedule_object in RemoteBackupSchedule.objects.filter(deleted=False).all():
            if json.loads(schedule_object.ext_config)['backup_period']['period_type'] == 'bak-continue':
                SpaceCollectionWorker.cdp_schedule_space_collection(schedule_object)

    @staticmethod
    @xlogging.convert_exception_to_value(None)
    def cluster_cdp_schedule_space_collection(schedule_object):
        master_node, slave_nodes, exclude_ids = \
            SpaceCollectionWorker._get_exclude_cluster_cdp_host_snapshot_ids(schedule_object)
        _logger.debug(r'{} {} {} _deal_cluster_schedule {}'.format(
            schedule_object.id, master_node, slave_nodes, exclude_ids))

        if not master_node:
            return

        SpaceCollectionWorker.cdp_schedule_space_collection(schedule_object, master_node, exclude_ids, True)
        for slave_node in slave_nodes:
            SpaceCollectionWorker.cdp_schedule_space_collection(schedule_object, slave_node)

    @staticmethod
    def cdp_deleted_schedule_space_collection():
        for schedule_object in BackupTaskSchedule.objects.filter(
                deleted=True, enabled=True, cycle_type=BackupTaskSchedule.CYCLE_CDP).all():
            SpaceCollectionWorker.cdp_deleted_schedule_space_collection_worker(schedule_object)

        for cluster_schedule in (ClusterBackupSchedule.objects.filter(deleted=True, enabled=True)):
            SpaceCollectionWorker.cdp_deleted_schedule_space_collection_worker(cluster_schedule)

        for remote_schedule in RemoteBackupSchedule.objects.filter(deleted=True, enabled=True):
            SpaceCollectionWorker.cdp_deleted_schedule_space_collection_worker(remote_schedule)

    @staticmethod
    @xlogging.convert_exception_to_value(None)
    def cdp_deleted_schedule_space_collection_worker(schedule_object):
        if _is_BackupTaskSchedule_instance(schedule_object):
            if 0 != SpaceCollectionTask.objects.filter(schedule=schedule_object, finish_datetime__isnull=True,
                                                       type__in=[SpaceCollectionTask.TYPE_CDP_MERGE,
                                                                 SpaceCollectionTask.TYPE_CDP_MERGE_SUB,
                                                                 SpaceCollectionTask.TYPE_CDP_DELETE]).count():
                return
            host_snapshots = HostSnapshot.objects.filter(schedule=schedule_object)
            backup_schedule = schedule_object
            cluster_schedule = None
            remote_schedule = None
        elif _is_ClusterBackupSchedule_instance(schedule_object):
            if 0 != SpaceCollectionTask.objects.filter(cluster_schedule=schedule_object, finish_datetime__isnull=True,
                                                       type__in=[SpaceCollectionTask.TYPE_CDP_MERGE,
                                                                 SpaceCollectionTask.TYPE_CDP_MERGE_SUB,
                                                                 SpaceCollectionTask.TYPE_CDP_DELETE]).count():
                return
            host_snapshots = HostSnapshot.objects.filter(cluster_schedule=schedule_object)
            backup_schedule = None
            cluster_schedule = schedule_object
            remote_schedule = None
        elif _is_RemoteBackupSchedule_instance(schedule_object):
            if 0 != SpaceCollectionTask.objects.filter(remote_schedule=schedule_object, finish_datetime__isnull=True,
                                                       type__in=[SpaceCollectionTask.TYPE_CDP_MERGE,
                                                                 SpaceCollectionTask.TYPE_CDP_MERGE_SUB,
                                                                 SpaceCollectionTask.TYPE_CDP_DELETE]).count():
                return
            host_snapshots = HostSnapshot.objects.filter(remote_schedule=schedule_object)
            backup_schedule = None
            cluster_schedule = None
            remote_schedule = schedule_object
        else:
            xlogging.raise_and_logging_error(
                r'assert schedule_object type', r'unknown schedule_object : {}'.format(schedule_object))
            return

        for host_snapshot_object in host_snapshots.filter(is_cdp=False, deleted=False).exclude(
                finish_datetime__isnull=False, successful=False).all():
            SpaceCollectionWorker.create_normal_host_snapshot_delete_task(host_snapshot_object)

        for host_snapshot_object in host_snapshots.filter(is_cdp=True, deleted=False).order_by('-start_datetime').all():
            SpaceCollectionWorker._deal_deleting_cdp_host_snapshot(host_snapshot_object,
                                                                   backup_schedule, cluster_schedule, remote_schedule)

        schedule_object.enabled = False
        schedule_object.save(update_fields=['enabled'])

    @staticmethod
    @xlogging.convert_exception_to_value(None)
    def cdp_schedule_space_collection(schedule_object, host_ident=None, exclude_ids=None, need_fix_safe_time=False):
        schedule_object_ext_config = json.loads(schedule_object.ext_config)
        new_snapshot_datetime = datetime.now() - timedelta(days=int(schedule_object_ext_config['cdpDataHoldDays']))
        expire_datetime = new_snapshot_datetime - timedelta(days=1)

        expire_cdp_host_snapshots = \
            SpaceCollectionWorker.query_exist_older_cdp_host_snapshot_objects_by_schedule_object(
                schedule_object, new_snapshot_datetime, host_ident, exclude_ids)

        expire_cdp_host_snapshots_count = len(expire_cdp_host_snapshots)

        if (expire_cdp_host_snapshots_count == 0) or expire_cdp_host_snapshots[0].start_datetime >= expire_datetime:
            return

        last_expire_datetime = expire_datetime - timedelta(days=1)
        normal_host_snapshots = \
            SpaceCollectionWorker.query_exist_newer_normal_host_snapshot_objects_by_schedule_object(
                schedule_object, last_expire_datetime, host_ident, exclude_ids)

        if len(normal_host_snapshots) == 0:
            expire_cdp_host_snapshots = \
                SpaceCollectionWorker.query_exist_older_cdp_host_snapshot_objects_by_schedule_object(
                    schedule_object, expire_datetime, host_ident, exclude_ids)
            if need_fix_safe_time:
                expire_datetime = SpaceCollectionWorker._fix_safe_datetime(
                    list(expire_cdp_host_snapshots)[-1], expire_datetime)
            SpaceCollectionWorker._create_cdp_merge_task(list(expire_cdp_host_snapshots), schedule_object,
                                                         expire_datetime)
        else:
            if need_fix_safe_time:
                new_snapshot_datetime = SpaceCollectionWorker._fix_safe_datetime(
                    list(expire_cdp_host_snapshots)[-1], new_snapshot_datetime)
            SpaceCollectionWorker._create_cdp_merge_task(list(expire_cdp_host_snapshots), schedule_object,
                                                         new_snapshot_datetime)

    @staticmethod
    def _fix_safe_datetime(host_snapshot, unsafe_datetime):
        safe_datetime = unsafe_datetime
        try:
            _fix, _safe_datetime = fix_restore_time(host_snapshot.id, unsafe_datetime)
            if _fix and _safe_datetime:
                safe_datetime = xdatetime.string2datetime(_safe_datetime)
                _logger.info(r'_fix_safe_datetime {} {} -> {} in {}'.format(
                    host_snapshot.host.display_name, unsafe_datetime, safe_datetime, host_snapshot.id))
            else:
                _logger.info(r'_fix_safe_datetime {} unsafe in {} - {}'.format(
                    host_snapshot.host.display_name, unsafe_datetime, host_snapshot.id))
        except Exception as e:
            _logger.error(r'_fix_safe_datetime failed {}'.format(e), exc_info=True)
        return safe_datetime

    @staticmethod
    def _create_cdp_merge_task(expire_host_snapshots, schedule_object, new_snapshot_datetime):
        new_snapshot_schedule = None
        new_snapshot_remote_schedule = None
        new_cluster_schedule = None
        if _is_BackupTaskSchedule_instance(schedule_object):
            if 0 != SpaceCollectionTask.objects.filter(
                    schedule=schedule_object, type=SpaceCollectionTask.TYPE_CDP_MERGE, finish_datetime__isnull=True) \
                    .count():
                return
            new_snapshot_schedule = schedule_object
        elif _is_RemoteBackupSchedule_instance(schedule_object):
            if 0 != SpaceCollectionTask.objects.filter(
                    remote_schedule=schedule_object, type=SpaceCollectionTask.TYPE_CDP_MERGE,
                    finish_datetime__isnull=True) \
                    .count():
                return
            new_snapshot_remote_schedule = schedule_object
        elif _is_ClusterBackupSchedule_instance(schedule_object):
            if 0 != SpaceCollectionTask.objects.filter(
                    cluster_schedule=schedule_object, type=SpaceCollectionTask.TYPE_CDP_MERGE,
                    finish_datetime__isnull=True) \
                    .count():
                return
            new_cluster_schedule = schedule_object
        else:
            xlogging.raise_and_logging_error(
                r'assert schedule_object type', r'unknown schedule_object : {}'.format(schedule_object))
            return

        last_expire_host_snapshot = expire_host_snapshots[-1]

        expire_host_snapshot_ids = list()

        for expire_host_snapshot in expire_host_snapshots:
            if expire_host_snapshot.id == last_expire_host_snapshot.id:
                if expire_host_snapshot.cdp_info.last_datetime > new_snapshot_datetime:
                    expire_host_snapshot.cdp_info.first_datetime = new_snapshot_datetime
                    expire_host_snapshot.cdp_info.save(update_fields=['first_datetime'])
                else:
                    new_snapshot_datetime = expire_host_snapshot.cdp_info.last_datetime
                    expire_host_snapshot.set_deleting()
            else:
                expire_host_snapshot.set_deleting()
                expire_host_snapshot_ids.append(expire_host_snapshot.id)

        task_object = SpaceCollectionTask.objects.create(
            type=SpaceCollectionTask.TYPE_CDP_MERGE, host_snapshot=last_expire_host_snapshot,
            schedule=new_snapshot_schedule, remote_schedule=new_snapshot_remote_schedule,
            cluster_schedule=new_cluster_schedule,
            ext_info=json.dumps(
                {'new_snapshot_datetime': new_snapshot_datetime.strftime(xdatetime.FORMAT_WITH_MICROSECOND),
                 'expire_host_snapshot_ids': expire_host_snapshot_ids}, ensure_ascii=False)
        )

        task_worker = CDPHostSnapshotSpaceCollectionMergeTask(task_object)
        task_worker.work()

    @staticmethod
    def query_exist_older_cdp_host_snapshot_objects_by_schedule_object(
            schedule_object, snapshot_datetime, host_ident=None, exclude_ids=None):
        if _is_BackupTaskSchedule_instance(schedule_object):
            return HostSnapshot.objects \
                .filter(schedule=schedule_object, is_cdp=True, deleted=False, start_datetime__lte=snapshot_datetime,
                        finish_datetime__isnull=False, deleting=False) \
                .exclude(finish_datetime__isnull=False, successful=False).order_by('start_datetime').all()
        elif _is_RemoteBackupSchedule_instance(schedule_object):
            return HostSnapshot.objects \
                .filter(remote_schedule=schedule_object, is_cdp=True, deleted=False,
                        start_datetime__lte=snapshot_datetime,
                        finish_datetime__isnull=False, deleting=False) \
                .exclude(finish_datetime__isnull=False, successful=False).order_by('start_datetime').all()
        elif _is_ClusterBackupSchedule_instance(schedule_object):
            _q = HostSnapshot.objects \
                .filter(cluster_schedule=schedule_object, is_cdp=True, deleted=False,
                        start_datetime__lte=snapshot_datetime, host__ident=host_ident,
                        finish_datetime__isnull=False, deleting=False) \
                .exclude(finish_datetime__isnull=False, successful=False)
            if exclude_ids:
                return _q.exclude(id__in=exclude_ids).order_by('start_datetime').all()
            else:
                return _q.order_by('start_datetime').all()
        else:
            xlogging.raise_and_logging_error(
                r'assert schedule_object type', r'unknown schedule_object : {}'.format(schedule_object))

    @staticmethod
    def query_exist_newer_normal_host_snapshot_objects_by_schedule_object(
            schedule_object, snapshot_datetime, host_ident=None, exclude_ids=None):
        if _is_BackupTaskSchedule_instance(schedule_object):
            return HostSnapshot.objects \
                .filter(schedule=schedule_object, is_cdp=False, deleted=False, deleting=False,
                        finish_datetime__isnull=False, start_datetime__gte=snapshot_datetime) \
                .exclude(finish_datetime__isnull=False, successful=False).order_by('start_datetime').all()
        elif _is_RemoteBackupSchedule_instance(schedule_object):
            return HostSnapshot.objects \
                .filter(remote_schedule=schedule_object, is_cdp=False, deleted=False, deleting=False,
                        finish_datetime__isnull=False, start_datetime__gte=snapshot_datetime) \
                .exclude(finish_datetime__isnull=False, successful=False).order_by('start_datetime').all()
        elif _is_ClusterBackupSchedule_instance(schedule_object):
            _q = HostSnapshot.objects \
                .filter(cluster_schedule=schedule_object, is_cdp=False, deleted=False, deleting=False,
                        finish_datetime__isnull=False, start_datetime__gte=snapshot_datetime, host__ident=host_ident) \
                .exclude(finish_datetime__isnull=False, successful=False)
            if exclude_ids:
                return _q.exclude(id__in=exclude_ids).order_by('start_datetime').all()
            else:
                return _q.order_by('start_datetime').all()
        else:
            xlogging.raise_and_logging_error(
                r'assert schedule_object type', r'unknown schedule_object : {}'.format(schedule_object))

    @staticmethod
    def _all_disk_snapshot_had_child(host_snapshot):
        for disk_snapshot in host_snapshot.disk_snapshots.all():
            children = disk_snapshot.child_snapshots.all()
            if not children:
                return False
            for child in children:
                if child.is_cdp:
                    continue
                if (child.host_snapshot.finish_datetime and
                        child.host_snapshot.start_datetime and
                        child.host_snapshot.successful):
                    break
            else:
                return False
        return True

    @staticmethod
    def _had_new_finished_hostsnapshot(host_snapshot):
        return HostSnapshot.objects.filter(
            host=host_snapshot.host, start_datetime__gt=host_snapshot.start_datetime,
            finish_datetime__isnull=False).count() > 0

    @staticmethod
    def _check_partial_host_snapshot():

        def _is_cluster_cdp():
            return (host_snapshot.cluster_schedule and
                    host_snapshot.cluster_schedule.cycle_type == BackupTaskSchedule.CYCLE_CDP)

        deleting_host_snapshot_objects = list()
        expire_datetime = datetime.now() - timedelta(days=_get_partial_disk_snapshot_expire_days())
        for host_snapshot in HostSnapshot.objects.filter(partial=True, finish_datetime__isnull=False,
                                                         start_datetime__isnull=False, successful=True,
                                                         deleted=False, deleting=False
                                                         ).all():
            if _is_cluster_cdp():
                continue
            if host_snapshot.finish_datetime < expire_datetime:
                _logger.info(r'partial host snapshot expire : {} {} {}'.format
                             (host_snapshot.id, host_snapshot.finish_datetime, host_snapshot))
                deleting_host_snapshot_objects.append(host_snapshot)
                continue
            if SpaceCollectionWorker._all_disk_snapshot_had_child(host_snapshot):
                _logger.info(r'partial host snapshot _all_disk_snapshot_had_child : {} {}'.format
                             (host_snapshot.id, host_snapshot.finish_datetime))
                deleting_host_snapshot_objects.append(host_snapshot)
                continue
            if SpaceCollectionWorker._had_new_finished_hostsnapshot(host_snapshot):
                _logger.info(r'partial host snapshot _had_new_finished_hostsnapshot : {} {}'.format
                             (host_snapshot.id, host_snapshot.finish_datetime))
                deleting_host_snapshot_objects.append(host_snapshot)
                continue

        for deleting_host_snapshot_object in deleting_host_snapshot_objects:
            if not deleting_host_snapshot_object.set_deleting():
                SpaceCollectionTask.objects.create(type=SpaceCollectionTask.TYPE_NORMAL_MERGE,
                                                   host_snapshot=deleting_host_snapshot_object)

    @staticmethod
    def _check_normal_host_snapshot(schedule_object, host_ident=None, exclude_ids=None):
        schedule_object_ext_config = json.loads(schedule_object.ext_config)
        expire_datetime = datetime.now() - timedelta(days=schedule_object_ext_config['backupDataHoldDays'])
        backup_least_number = schedule_object_ext_config['backupLeastNumber']

        deleting_host_snapshot_objects = list()

        host_snapshot_objects = SpaceCollectionWorker. \
            query_visible_normal_host_snapshot_objects_by_schedule_object(schedule_object)
        host_snapshot_objects = host_snapshot_objects.exclude(deploy_templates__isnull=False)  # 排除掉是模板的快照点

        if host_ident:
            host_snapshot_objects = host_snapshot_objects.filter(host__ident=host_ident)
        if exclude_ids:
            host_snapshot_objects = host_snapshot_objects.exclude(id__in=exclude_ids)

        for host_snapshot_object in host_snapshot_objects:
            if len(host_snapshot_objects) - len(deleting_host_snapshot_objects) <= backup_least_number:
                break
            if host_snapshot_object.start_datetime is not None \
                    and host_snapshot_object.start_datetime < expire_datetime:
                deleting_host_snapshot_objects.append(host_snapshot_object)

        if len(deleting_host_snapshot_objects) == 0 and len(host_snapshot_objects) > backup_least_number \
                and len(host_snapshot_objects) > 0:
            auto_clean_data_when_lt = schedule_object_ext_config['autoCleanDataWhenlt']
            if 2 == SpaceCollectionWorker.get_user_storage_node_space_status(
                    schedule_object, auto_clean_data_when_lt * 1024):
                deleting_host_snapshot_objects.append(host_snapshot_objects[0])

        for deleting_host_snapshot_object in deleting_host_snapshot_objects:
            if not deleting_host_snapshot_object.set_deleting():
                SpaceCollectionTask.objects.create(type=SpaceCollectionTask.TYPE_NORMAL_MERGE,
                                                   host_snapshot=deleting_host_snapshot_object)

    @staticmethod
    def _check_invisible_host_snapshot(cluster_schedule_object):
        for host_obj in cluster_schedule_object.hosts.all():
            try:
                last_one = HostSnapshot.objects.filter(host=host_obj,
                                                       cluster_schedule=cluster_schedule_object,
                                                       is_cdp=False,
                                                       deleting=False,
                                                       deleted=False,
                                                       cluster_visible=True,
                                                       cluster_finish_datetime__isnull=False,
                                                       successful=True,
                                                       partial=False).order_by('-start_datetime').first()
                if last_one is None:
                    continue

                host_snapshot_objects = HostSnapshot.objects.filter(host=host_obj,
                                                                    start_datetime__lt=last_one.start_datetime,
                                                                    cluster_schedule=cluster_schedule_object,
                                                                    is_cdp=False,
                                                                    deleting=False,
                                                                    deleted=False,
                                                                    cluster_visible=False,
                                                                    cluster_finish_datetime__isnull=False,
                                                                    successful=True,
                                                                    partial=False).order_by('start_datetime').all()
                if host_snapshot_objects.count():
                    _logger.info('host {} last host snapshot {} {}'.format(
                        host_obj.ident, last_one.id, last_one.start_datetime))
                for host_snapshot in host_snapshot_objects:
                    if not host_snapshot.set_deleting():
                        _logger.info('find cdp host snapshot in cluster : {} {} , will merge'.format(
                            host_snapshot.id, host_snapshot.start_datetime))
                        SpaceCollectionTask.objects.create(type=SpaceCollectionTask.TYPE_NORMAL_MERGE,
                                                           host_snapshot=host_snapshot)

                host_snapshot_objects = HostSnapshot.objects.filter(host=host_obj,
                                                                    start_datetime__lt=last_one.start_datetime,
                                                                    cluster_schedule=cluster_schedule_object,
                                                                    is_cdp=True,
                                                                    deleting=False,
                                                                    deleted=False,
                                                                    cluster_visible=False,
                                                                    cluster_finish_datetime__isnull=False,
                                                                    successful=True,
                                                                    partial=False).order_by('start_datetime').all()
                if host_snapshot_objects.count():
                    _logger.info('host {} last host snapshot {} {}'.format(
                        host_obj.ident, last_one.id, last_one.start_datetime))
                for host_snapshot in host_snapshot_objects:
                    _logger.info('find cdp host snapshot in cluster : {} {} , will merge'.format(
                        host_snapshot.id, host_snapshot.start_datetime))
                    last_cdp_timestamp = get_last_cdp_timestamp(host_snapshot)
                    if last_cdp_timestamp is None:
                        continue
                    if last_cdp_timestamp == 0:
                        _logger.warning(r'cdp host snapshot : {} NOT cdp , convert to normal'.format(host_snapshot.id))
                        host_snapshot.is_cdp = False
                        host_snapshot.save(update_fields=['is_cdp', ])
                    elif not host_snapshot.set_deleting():
                        last_cdp_datetime = datetime.fromtimestamp(last_cdp_timestamp)
                        SpaceCollectionTask.objects.create(
                            type=SpaceCollectionTask.TYPE_CDP_MERGE, host_snapshot=host_snapshot,
                            cluster_schedule=cluster_schedule_object, ext_info=json.dumps(
                                {'expire_host_snapshot_ids': [], 'new_snapshot_invisible': True,
                                 'new_snapshot_datetime': last_cdp_datetime.strftime(xdatetime.FORMAT_WITH_MICROSECOND),
                                 }, ensure_ascii=False))
            except Exception:
                pass  # do nothing

    @staticmethod
    def check_normal_host_snapshot():
        for schedule_object in BackupTaskSchedule.objects.filter(deleted=False).all():
            SpaceCollectionWorker._check_normal_host_snapshot(schedule_object)
        for schedule_object in (
                ClusterBackupSchedule.objects.filter(
                    deleted=False).exclude(cycle_type=BackupTaskSchedule.CYCLE_CDP).all()
        ):
            SpaceCollectionWorker._check_invisible_host_snapshot(schedule_object)
            SpaceCollectionWorker._check_normal_host_snapshot(schedule_object)
        for schedule_object in (
                ClusterBackupSchedule.objects.filter(
                    deleted=False).filter(cycle_type=BackupTaskSchedule.CYCLE_CDP).all()
        ):
            SpaceCollectionWorker._check_cluster_normal_host_snapshot(schedule_object)
        for schedule_object in RemoteBackupSchedule.objects.filter(deleted=False).all():
            SpaceCollectionWorker._check_normal_host_snapshot(schedule_object)
        SpaceCollectionWorker._check_partial_host_snapshot()

    @staticmethod
    @xlogging.convert_exception_to_value(None)
    def _check_cluster_normal_host_snapshot(schedule_object):
        master_node, slave_nodes, exclude_ids = \
            SpaceCollectionWorker._get_exclude_cluster_cdp_host_snapshot_ids(schedule_object)
        if not master_node:
            return
        SpaceCollectionWorker._check_normal_host_snapshot(schedule_object, master_node, exclude_ids)
        for slave_node in slave_nodes:
            SpaceCollectionWorker._check_normal_host_snapshot(schedule_object, slave_node)

    @staticmethod
    def _get_exclude_cluster_cdp_host_snapshot_ids(schedule_object):
        def _find_last_visable_master_host_snapshot_obj():
            return HostSnapshot.objects.filter(
                host__ident=master_node, cluster_schedule=schedule_object, deleted=False, deleting=False,
                start_datetime__isnull=False, is_cdp=True, cluster_visible=True, cdp_info__last_datetime__isnull=False
            ).order_by('-cdp_info__last_datetime').first()

        def _find_after_master_host_snapshot_objs(last_datetime):
            return HostSnapshot.objects.filter(
                host__ident=master_node, cluster_schedule=schedule_object, deleted=False, deleting=False,
                start_datetime__isnull=False, is_cdp=True, start_datetime__gt=last_datetime
            ).values_list('id', flat=True)

        def _get_slave_nodes():
            return schedule_object.hosts.exclude(ident=master_node).all().values_list('ident', flat=True)

        schedule_cfg = json.loads(schedule_object.ext_config)
        master_node = schedule_cfg["master_node"]
        last_visable_master_host_snapshot_obj = _find_last_visable_master_host_snapshot_obj()
        if not last_visable_master_host_snapshot_obj:
            return None, None, None
        after_last_visable_master_host_snapshot_objs = _find_after_master_host_snapshot_objs(
            last_visable_master_host_snapshot_obj.cdp_info.last_datetime)
        exclude_ids = list(after_last_visable_master_host_snapshot_objs)
        if exclude_ids:
            exclude_ids.append(last_visable_master_host_snapshot_obj.id)
        return master_node, _get_slave_nodes(), exclude_ids

    # 返回值：
    #   1   空间足够
    #   2   空间不足
    #   3   查询空间失败
    #   4   未知异常
    @staticmethod
    @xlogging.convert_exception_to_value(4)
    def get_user_storage_node_space_status(schedule_object, enough_space_mb):
        try:
            if UserQuotaTools.check_user_storage_size_in_node(schedule_object, enough_space_mb, log_ok=False) is None:
                return 3
            else:
                return 1
        except xlogging.BoxDashboardException as e:
            if e.http_status == xlogging.HTTP_STATUS_USER_STORAGE_NODE_NOT_ENOUGH_SPACE:
                return 2
            else:
                return 3

    @staticmethod
    def query_visible_normal_host_snapshot_objects_by_schedule_object(schedule_object):
        if _is_ClusterBackupSchedule_instance(schedule_object):
            return HostSnapshot.objects.filter(cluster_schedule=schedule_object,
                                               is_cdp=False,
                                               deleting=False,
                                               deleted=False,
                                               cluster_visible=True,
                                               cluster_finish_datetime__isnull=False,
                                               successful=True,
                                               partial=False).order_by('start_datetime').all()
        elif _is_RemoteBackupSchedule_instance(schedule_object):
            return HostSnapshot.objects.filter(remote_schedule=schedule_object,
                                               is_cdp=False,
                                               deleting=False,
                                               deleted=False,
                                               finish_datetime__isnull=False,
                                               successful=True,
                                               partial=False).order_by('start_datetime').all()
        elif _is_BackupTaskSchedule_instance(schedule_object):
            return HostSnapshot.objects.filter(schedule=schedule_object,
                                               is_cdp=False,
                                               deleting=False,
                                               deleted=False,
                                               finish_datetime__isnull=False,
                                               successful=True,
                                               partial=False).order_by('start_datetime').all()
        else:
            xlogging.raise_and_logging_error(
                r'assert schedule_object type', r'unknown schedule_object : {}'.format(schedule_object))

    @staticmethod
    def _is_backup_task_object_failed(backup_task_object):
        return (backup_task_object.finish_datetime is not None) and (not backup_task_object.successful)

    @staticmethod
    def _is_cdp_task_object_failed(cdp_task_object):
        return (cdp_task_object.finish_datetime is not None) and (not cdp_task_object.successful)

    @staticmethod
    def _is_remote_task_object_failed(remote_task_object):
        return (remote_task_object.finish_datetime is not None) and (not remote_task_object.successful)

    @staticmethod
    def is_host_snapshot_object_failed(host_snapshot_object):
        return (host_snapshot_object.finish_datetime is not None) and (not host_snapshot_object.successful)

    @staticmethod
    def _wait_host_snapshot_object_in_backup_task_object(backup_task_object):
        while backup_task_object.host_snapshot is None:
            if SpaceCollectionWorker._is_backup_task_object_failed(backup_task_object):
                break
            else:
                _logger.warning(r'wait backup_task host_snapshot created : {}'.format(backup_task_object.id))
                time.sleep(1)

    @staticmethod
    def _wait_host_snapshot_object_in_cdp_task_object(cdp_task_object):
        while cdp_task_object.host_snapshot is None:
            if SpaceCollectionWorker._is_cdp_task_object_failed(cdp_task_object):
                break
            else:
                _logger.warning(r'wait cdp_task host_snapshot created : {}'.format(cdp_task_object.id))
                time.sleep(1)
                cdp_task_object = CDPTask.objects.get(id=cdp_task_object.id)

    @staticmethod
    def _wait_host_snapshot_object_in_remote_task_object(remote_task_object):
        while remote_task_object.host_snapshot is None:
            if SpaceCollectionWorker._is_remote_task_object_failed(remote_task_object):
                break
            else:
                _logger.warning(r'wait remote_task host_snapshot created : {}'.format(remote_task_object.id))
                time.sleep(1)

    @staticmethod
    @xlogging.LockDecorator(_locker)
    def set_host_snapshot_deleting_and_collection_space_later(host_snapshot_object, timestamp=None):
        if (timestamp is None) and (not host_snapshot_object.is_cdp):
            SpaceCollectionWorker._set_normal_host_snapshot_deleting_and_collection_space_later(host_snapshot_object)
        else:
            xlogging.raise_and_logging_error(
                '内部异常，代码2357', 'set_host_snapshot_deleting_and_collection_space_later never happened {}'
                    .format(host_snapshot_object.id))

    @staticmethod
    def _set_normal_host_snapshot_deleting_and_collection_space_later(host_snapshot_object):
        if host_snapshot_object.deleted or \
                SpaceCollectionWorker.is_host_snapshot_object_failed(host_snapshot_object):
            _logger.warning(r'_set_normal_host_snapshot_deleting_and_collection_space_later host_snapshot_object {} is '
                            r'deleted or failed, ignore'.format(host_snapshot_object.id))
            return  # 被删除 或者 失败的主机快照 跳过

        if not host_snapshot_object.set_deleting():
            SpaceCollectionTask.objects.create(type=SpaceCollectionTask.TYPE_NORMAL_MERGE,
                                               host_snapshot=host_snapshot_object)

    @staticmethod
    @xlogging.LockDecorator(_locker)
    def set_schedule_deleting_and_collection_space_later(schedule_object):
        schedule_object.delete_and_collection_space_later()

        if schedule_object.cycle_type == BackupTaskSchedule.CYCLE_CDP:
            SpaceCollectionWorker._set_cdp_schedule_deleting_and_collection_space_later(schedule_object)
        elif schedule_object.backup_source_type == BackupTaskSchedule.BACKUP_FILES:
            SpaceCollectionWorker._set_file_backup_host_snapshot_deleting_and_collection_space_later(schedule_object)
        else:
            SpaceCollectionWorker._set_normal_schedule_deleting_and_collection_space_later(schedule_object)

    @staticmethod
    @xlogging.LockDecorator(_locker)
    def set_cluster_schedule_deleting_and_collection_space_later(cluster_schedule, info_class):
        cluster_schedule.delete_and_collection_space_later()

        for cdp_task_object in CDPTask.objects.filter(cluster_task__schedule=cluster_schedule):
            SpaceCollectionWorker._wait_host_snapshot_object_in_cdp_task_object(cdp_task_object)

        begin_chk_timestamp = time.time()
        while True:
            if time.time() - begin_chk_timestamp > 60.0:  # 最多等待1分钟
                _logger.warning(r'set_cluster_schedule_deleting_and_collection_space_later timeout')
                break
            if ClusterBackupTask.objects.filter(
                    schedule=cluster_schedule, start_datetime__isnull=False, finish_datetime__isnull=True).count() == 0:
                break
            time.sleep(1)

        SpaceCollectionWorker._clean_cluster_slave_data_in_base_info(cluster_schedule.id, info_class)

        for host_snapshot_object in HostSnapshot.objects.filter(cluster_schedule=cluster_schedule):
            if host_snapshot_object.deleted or SpaceCollectionWorker.is_host_snapshot_object_failed(
                    host_snapshot_object):
                _logger.warning(r'set_cluster_schedule_deleting_and_collection_space_later host_snapshot_object {} is '
                                r'deleted or failed, ignore'.format(host_snapshot_object.id))
                continue  # 被删除 或者 失败的主机快照 跳过

            if host_snapshot_object.deploy_templates.exists():
                continue

            if host_snapshot_object.is_cdp:
                host_snapshot_object.set_deleting()
            else:
                SpaceCollectionWorker.create_normal_host_snapshot_delete_task(host_snapshot_object)

    @staticmethod
    def _clean_cluster_slave_data_in_base_info(cluster_schedule_id, info_class):
        try:
            cluster_schedule_obj = ClusterBackupSchedule.objects.get(id=cluster_schedule_id)
            info = info_class(cluster_schedule_obj)
            info.force_clean_all_history()
        except Exception as e:
            _logger.error('_clean_cluster_slave_data_in_base_info failed {}'.format(e), exc_info=True)

    @staticmethod
    @xlogging.LockDecorator(_locker)
    def set_remote_schedule_deleting_and_collection_space_later(remote_schedule):
        remote_schedule.delete_and_collection_space_later()

        for remote_task_object in RemoteBackupTask.objects.filter(schedule=remote_schedule):
            SpaceCollectionWorker._wait_host_snapshot_object_in_remote_task_object(remote_task_object)

        for host_snapshot_object in HostSnapshot.objects.filter(remote_schedule=remote_schedule):
            if host_snapshot_object.deleted or SpaceCollectionWorker.is_host_snapshot_object_failed(
                    host_snapshot_object):
                _logger.warning(r'set_remote_schedule_deleting_and_collection_space_later host_snapshot_object {} is '
                                r'deleted or failed, ignore'.format(host_snapshot_object.id))
                continue  # 被删除 或者 失败的主机快照 跳过

            if host_snapshot_object.is_cdp:
                host_snapshot_object.set_deleting()
            else:
                SpaceCollectionWorker.create_normal_host_snapshot_delete_task(host_snapshot_object)

    @staticmethod
    def _set_cdp_schedule_deleting_and_collection_space_later(schedule_object):
        for cdp_task_object in CDPTask.objects.filter(schedule=schedule_object).all():
            SpaceCollectionWorker._wait_host_snapshot_object_in_cdp_task_object(cdp_task_object)

        for host_snapshot_object in HostSnapshot.objects.filter(schedule=schedule_object).all():
            if host_snapshot_object.deleted or \
                    SpaceCollectionWorker.is_host_snapshot_object_failed(host_snapshot_object):
                _logger.warning(r'_set_cdp_schedule_deleting_and_collection_space_later host_snapshot_object {} is '
                                r'deleted or failed, ignore'.format(host_snapshot_object.id))
                continue  # 被删除 或者 失败的主机快照 跳过

            if host_snapshot_object.deploy_templates.exists():
                continue

            if host_snapshot_object.is_cdp:
                host_snapshot_object.set_deleting()
            else:
                SpaceCollectionWorker.create_normal_host_snapshot_delete_task(host_snapshot_object)

    @staticmethod
    def _check_and_set_task_delete(_task_object, fn_name):
        SpaceCollectionWorker._wait_host_snapshot_object_in_backup_task_object(_task_object)
        if SpaceCollectionWorker._is_backup_task_object_failed(_task_object) \
                and _task_object.host_snapshot is None:
            _logger.warning(r'{} backup_task_object {} '
                            r'is failed AND host_snapshot is NONE ignore'.format(fn_name, _task_object.id))
            return  # 失败的任务跳过

        host_snapshot_object = _task_object.host_snapshot
        if host_snapshot_object.deleted or \
                SpaceCollectionWorker.is_host_snapshot_object_failed(host_snapshot_object):
            _logger.warning(r'{} host_snapshot_object {} is '
                            r'deleted or failed, ignore'.format(fn_name, host_snapshot_object.id))
            return  # 被删除 或者 失败的主机快照 跳过

        if host_snapshot_object.deploy_templates.exists():
            return

        SpaceCollectionWorker.create_normal_host_snapshot_delete_task(host_snapshot_object)

    @staticmethod
    def _set_file_backup_host_snapshot_deleting_and_collection_space_later(schedule_object):
        for backup_task_object in FileBackupTask.objects.filter(schedule=schedule_object):
            SpaceCollectionWorker._check_and_set_task_delete(
                backup_task_object, '_set_file_backup_host_snapshot_deleting_and_collection_space_later')

    @staticmethod
    def _set_normal_schedule_deleting_and_collection_space_later(schedule_object):
        for backup_task_object in BackupTask.objects.filter(schedule=schedule_object).all():
            SpaceCollectionWorker._check_and_set_task_delete(backup_task_object,
                                                             '_set_normal_schedule_deleting_and_collection_space_later')

    @staticmethod
    def create_normal_host_snapshot_delete_task(host_snapshot_object):
        if host_snapshot_object.set_deleting():
            SpaceCollectionTask.objects.filter(type=SpaceCollectionTask.TYPE_NORMAL_MERGE,
                                               host_snapshot=host_snapshot_object, finish_datetime__isnull=True) \
                .update(type=SpaceCollectionTask.TYPE_NORMAL_DELETE)
        else:
            SpaceCollectionTask.objects.create(type=SpaceCollectionTask.TYPE_NORMAL_DELETE,
                                               host_snapshot=host_snapshot_object)

    @staticmethod
    def _deal_deleting_cdp_host_snapshot(host_snapshot_object, backup_schedule, cluster_schedule, remote_schedule):
        task = SpaceCollectionTask.objects.create(type=SpaceCollectionTask.TYPE_CDP_DELETE,
                                                  host_snapshot=host_snapshot_object,
                                                  schedule=backup_schedule,
                                                  cluster_schedule=cluster_schedule,
                                                  remote_schedule=remote_schedule)

        if not DeleteCdpHostSnapshotTask(task).work():
            xlogging.raise_and_logging_error(r'内部异常，代码2378',
                                             r'run DeleteCdpHostSnapshotTask failed {} '.format(task.id))


class DeleteCdpHostSnapshotTask(object):
    def __init__(self, task_object):
        if task_object.type != SpaceCollectionTask.TYPE_CDP_DELETE:
            xlogging.raise_and_logging_error(
                '内部异常，代码2377', 'DeleteCdpHostSnapshotTask incorrect type {} {}'.format(
                    task_object.id, task_object.type))
        self._task_object = task_object

    @xlogging.convert_exception_to_value(False)
    def work(self):
        host_snapshot_id = self._task_object.host_snapshot.id

        if _is_BackupTaskSchedule_instance(self._task_object.schedule):
            schedule_id = self._task_object.schedule.id
            schedule_type = _BackupScheduleInstance
        elif _is_ClusterBackupSchedule_instance(self._task_object.cluster_schedule):
            schedule_id = self._task_object.cluster_schedule.id
            schedule_type = _ClusterScheduleInstance
        elif _is_RemoteBackupSchedule_instance(self._task_object.remote_schedule):
            schedule_id = self._task_object.remote_schedule.id
            schedule_type = _RemoteScheduleInstance
        else:
            xlogging.raise_and_logging_error('内部异常，代码2377',
                                             'SpaceCollectionTask[{}] has no schedule'.format(self._task_object.id))
            return

        task_ext_info_key = 'host_snapshot_{}'.format(host_snapshot_id)

        task_ext_info = self._get_or_create_task_ext_info(
            task_ext_info_key, host_snapshot_id, schedule_id, schedule_type)

        if task_ext_info['finished']:
            return True

        expire_host_snapshot_object = HostSnapshot.objects.get(id=host_snapshot_id)

        if expire_host_snapshot_object.is_cdp:
            for disk_ident in task_ext_info['disks'].keys():
                cdp_disk_snapshots = task_ext_info['disks'][disk_ident]['cdp_disk_snapshots']
                self._merge_cdp_disk_snapshots_to_invisible_normal_disk_snapshots_or_delete_file(
                    task_ext_info_key, cdp_disk_snapshots, host_snapshot_id)

            expire_host_snapshot_object.is_cdp = False
            expire_host_snapshot_object.deleting = False
            expire_host_snapshot_object.save(update_fields=['is_cdp', 'deleting'])

        SpaceCollectionWorker.set_host_snapshot_deleting_and_collection_space_later(expire_host_snapshot_object)
        self._update_host_finished(task_ext_info_key)

        self._task_object.set_finished()
        return True

    def _get_or_create_task_ext_info(self, info_key, host_snapshot_id, schedule_id, schedule_type):
        ext_info = json.loads(self._task_object.ext_info)

        if CDPHostSnapshotSpaceCollectionMergeTask.get_or_create_task_ext_info(ext_info, info_key, host_snapshot_id):
            self._task_object.ext_info = json.dumps(ext_info, ensure_ascii=False)
            self._task_object.save(update_fields=['ext_info'])

        force_merge_disk_idents = self._get_or_create_force_merge_disk_idents(schedule_id, schedule_type)
        new_force_merge_disk_idents = force_merge_disk_idents.copy()

        if self._get_or_create_need_merge_disk_snapshot_ids(ext_info[info_key], new_force_merge_disk_idents,
                                                            schedule_id, schedule_type):
            self._check_normal_disk_snapshot_in_host_snapshot(host_snapshot_id, new_force_merge_disk_idents)
            if len(force_merge_disk_idents) != len(new_force_merge_disk_idents):
                self._update_force_merge_disk_idents(schedule_id, new_force_merge_disk_idents, schedule_type)
            self._task_object.ext_info = json.dumps(ext_info, ensure_ascii=False)
            self._task_object.save(update_fields=['ext_info'])

        return ext_info[info_key]

    def _update_host_finished(self, info_key):
        ext_info = json.loads(self._task_object.ext_info)
        ext_info[info_key]['finished'] = True
        self._task_object.ext_info = json.dumps(ext_info, ensure_ascii=False)
        self._task_object.save(update_fields=['ext_info'])

    @staticmethod
    def _check_normal_disk_snapshot_in_host_snapshot(host_snapshot_id, force_merge_disk_idents):
        host_snapshot_object = _check_host_snapshot_is_cdp(host_snapshot_id)
        for disk_snapshot in host_snapshot_object.disk_snapshots.all():
            if disk_snapshot.disk.ident in force_merge_disk_idents:
                continue

            children = disk_snapshot.child_snapshots.filter(merged=False, host_snapshot__isnull=False).all()
            for child in children:
                if child.host_snapshot.schedule_id != host_snapshot_object.schedule_id:
                    force_merge_disk_idents.append(disk_snapshot.disk.ident)
                    break

    @staticmethod
    def _get_or_create_force_merge_disk_idents(schedule_id, schedule_type):
        schedule_object = _get_schedule_object(schedule_id, schedule_type)
        ext_config = json.loads(schedule_object.ext_config)
        if 'force_merge_disk_idents' not in ext_config.keys():
            ext_config['force_merge_disk_idents'] = list()
            schedule_object.ext_config = json.dumps(ext_config, ensure_ascii=False)
            schedule_object.save(update_fields=['ext_config'])

        return ext_config['force_merge_disk_idents']

    @staticmethod
    def _update_force_merge_disk_idents(schedule_id, new_force_merge_disk_idents, schedule_type):
        schedule_object = _get_schedule_object(schedule_id, schedule_type)
        ext_config = json.loads(schedule_object.ext_config)
        ext_config['force_merge_disk_idents'] = new_force_merge_disk_idents
        schedule_object.ext_config = json.dumps(ext_config, ensure_ascii=False)
        schedule_object.save(update_fields=['ext_config'])

    @staticmethod
    def _get_or_create_need_merge_disk_snapshot_ids(task_ext_info, new_force_merge_disk_idents, schedule_id,
                                                    schedule_type):
        if 'need_merge_disk_snapshot_ids' in task_ext_info.keys():
            return False

        type_merge_ids = list()
        _logger.debug('_get_or_create_need_merge_disk_snapshot_ids, new_force_merge_disk_idents{}'.format(
            new_force_merge_disk_idents))
        for disk_ident in task_ext_info['disks'].keys():
            force_merge = disk_ident in new_force_merge_disk_idents
            cdp_disk_snapshots = task_ext_info['disks'][disk_ident]['cdp_disk_snapshots']
            for cdp_disk_snapshot in cdp_disk_snapshots:
                disk_snapshot_id = cdp_disk_snapshot['disk_snapshot_id']
                _check_disk_snapshot_is_cdp(disk_snapshot_id)

                if force_merge:
                    type_merge_ids.append(disk_snapshot_id)
                else:  # 判断是否有 依赖的子快照点
                    child_disk_snapshot_with_intercept = CDPHostSnapshotSpaceCollectionMergeTask. \
                        get_child_disk_snapshot_with_intercept(disk_snapshot_id, None)
                    if child_disk_snapshot_with_intercept is not None:
                        # 这个判断是为了检查是否有另外一台主机依赖了本主机的某个CDP快照文件
                        type_merge_ids.append(disk_snapshot_id)
                        _logger.debug('type_merge_ids:{}, another host depend this snapshot:{}'.format(
                            disk_snapshot_id, child_disk_snapshot_with_intercept.id))
                        if disk_ident not in new_force_merge_disk_idents:
                            new_force_merge_disk_idents.append(disk_ident)
                    else:
                        # 以下逻辑是为了检查是否还有其他计划的快照依赖了某个CDP快照文件
                        child_disk_snapshot_without_intercepts = CDPHostSnapshotSpaceCollectionMergeTask. \
                            get_all_child_disk_snapshot_without_intercept(disk_snapshot_id)
                        for child_disk_snapshot_without_intercept in child_disk_snapshot_without_intercepts:
                            if check_disk_snapshot_belongs_to_other_valid_schedule(
                                    child_disk_snapshot_without_intercept, schedule_id, schedule_type):
                                _logger.debug('type_merge_ids:{}, child snapshot:{} belongs to other schedule'.format(
                                    disk_snapshot_id, child_disk_snapshot_without_intercept.id))
                                type_merge_ids.append(disk_snapshot_id)
                                if disk_ident not in new_force_merge_disk_idents:
                                    new_force_merge_disk_idents.append(disk_ident)
                                break

            inverted_sequence_cdp_disk_snapshots = cdp_disk_snapshots[::-1]
            need_merge = force_merge
            for cdp_disk_snapshot in inverted_sequence_cdp_disk_snapshots:
                disk_snapshot_id = cdp_disk_snapshot['disk_snapshot_id']
                if need_merge:
                    if disk_snapshot_id not in type_merge_ids:
                        type_merge_ids.append(disk_snapshot_id)
                else:
                    if disk_snapshot_id in type_merge_ids:
                        need_merge = True

        task_ext_info['need_merge_disk_snapshot_ids'] = type_merge_ids
        return True

    def _merge_cdp_disk_snapshots_to_invisible_normal_disk_snapshots_or_delete_file(
            self, task_ext_info_key, cdp_disk_snapshots, host_snapshot_id):
        need_merge_disk_snapshot_ids = json.loads(self._task_object.ext_info)[task_ext_info_key][
            'need_merge_disk_snapshot_ids']

        for disk_snapshot in cdp_disk_snapshots:
            while True:
                disk_snapshot_id = disk_snapshot['disk_snapshot_id']

                ext_info = json.loads(self._task_object.ext_info)
                disk_snapshot_finished, disk_snapshot_next_timestamp = CDPHostSnapshotSpaceCollectionMergeTask. \
                    get_disk_snapshot_from_ext_info(ext_info, task_ext_info_key, disk_snapshot_id, self._task_object.id)

                if disk_snapshot_finished:
                    break

                if disk_snapshot_id in need_merge_disk_snapshot_ids:
                    # 合并
                    _check_disk_snapshot_is_cdp(disk_snapshot_id)
                    _check_disk_snapshot_parent_is_not_cdp(disk_snapshot_id)

                    child_disk_snapshot_with_intercept = CDPHostSnapshotSpaceCollectionMergeTask. \
                        get_child_disk_snapshot_with_intercept(disk_snapshot_id, disk_snapshot_next_timestamp)

                    if child_disk_snapshot_with_intercept is None:  # 全文件回收
                        child_disk_snapshot_without_intercept = CDPHostSnapshotSpaceCollectionMergeTask. \
                            get_child_disk_snapshot_without_intercept(disk_snapshot_id)
                        task_object = CdpMergeSubTask.create_sub_task_without_intercept(
                            self._task_object.schedule, self._task_object.remote_schedule,
                            self._task_object.cluster_schedule, host_snapshot_id,
                            child_disk_snapshot_without_intercept, disk_snapshot_id, disk_snapshot_next_timestamp)
                    else:  # 部分文件回收
                        self._update_disk_next_timestamp(task_ext_info_key, disk_snapshot_id,
                                                         child_disk_snapshot_with_intercept.parent_timestamp)
                        task_object = CdpMergeSubTask.create_sub_task_with_intercept(
                            self._task_object.schedule, self._task_object.remote_schedule,
                            self._task_object.cluster_schedule, host_snapshot_id,
                            child_disk_snapshot_with_intercept.id, disk_snapshot_id, disk_snapshot_next_timestamp,
                            child_disk_snapshot_with_intercept.parent_timestamp)
                else:
                    # 删除
                    child_disk_snapshot_with_intercept = None
                    task_object = DeleteCdpObjectTask.create(self._task_object.schedule,
                                                             _check_disk_snapshot_is_cdp(disk_snapshot_id))

                if child_disk_snapshot_with_intercept is None:
                    self._set_disk_finished(task_ext_info_key, disk_snapshot_id)

                if task_object.type == SpaceCollectionTask.TYPE_CDP_MERGE_SUB:
                    if not CdpMergeSubTask(task_object).work():
                        xlogging.raise_and_logging_error(
                            r'内部异常，代码2380', 'run CdpMergeSubTask failed {}'.format(task_object.id))
                else:
                    if not DeleteCdpObjectTask(task_object).work():
                        xlogging.raise_and_logging_error(
                            r'内部异常，代码2381', 'run DeleteCdpObjectTask failed {}'.format(task_object.id))

    def _update_disk_next_timestamp(self, info_key, disk_snapshot_id, next_timestamp):
        ext_info = json.loads(self._task_object.ext_info)
        CDPHostSnapshotSpaceCollectionMergeTask.update_disk_next_timestamp(
            ext_info, info_key, disk_snapshot_id, next_timestamp, self._task_object.id)
        self._task_object.ext_info = json.dumps(ext_info, ensure_ascii=False)
        self._task_object.save(update_fields=['ext_info'])

    def _set_disk_finished(self, info_key, disk_snapshot_id):
        ext_info = json.loads(self._task_object.ext_info)
        CDPHostSnapshotSpaceCollectionMergeTask.set_disk_finished(
            ext_info, info_key, disk_snapshot_id, self._task_object.id)
        self._task_object.ext_info = json.dumps(ext_info, ensure_ascii=False)
        self._task_object.save(update_fields=['ext_info'])
