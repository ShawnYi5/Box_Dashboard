# coding=utf-8
import contextlib
import datetime
import json
import os
import threading
import uuid
from collections import namedtuple
import time

from django.utils import timezone
from rest_framework import status
from taskflow import engines
from taskflow import task
from taskflow.listeners import logging as logging_listener
from taskflow.patterns import linear_flow as lf
from taskflow.persistence import models

from apiv1.htb_task import SendTaskWork
from apiv1.models import FileSyncTask, DiskSnapshot, FileSyncSchedule
from apiv1.work_processors import file_backup_api
from apiv1.tasks import Sleep
from apiv1.snapshot import GetDiskSnapshot, GetSnapshotList
from box_dashboard import xlogging, task_backend, xdatetime, boxService, xdata

_logger = xlogging.getLogger(__name__)


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


class FileSyncInit(task.Task, WorkerLog):
    default_provides = 'task_content'

    def __init__(self, task_id, inject=None):
        super(FileSyncInit, self).__init__('FileSyncInit_{}'.format(task_id), inject=inject)
        self.task_id = task_id
        self.task = None
        self.task_content = None

    def execute(self, *args, **kwargs):
        try:
            self.task = FileSyncTask.objects.get(id=self.task_id)
            self.task_content = {
                'error': '',
                'locked_snapshots': list(),
                'tmp_files': list(),
                'lock_name': 'file_sync_{}'.format(self.task.id),
                'task_key': 'file_sync{}'.format(self.task.schedule.host.ident),
                'sync_params': {
                    'name': self.task.name,
                    'task_uuid': self.task.task_uuid,
                    'task_type': 'file_sync',
                    'target_host_ident': self.task.schedule.target_host_ident
                }
            }
            self.task.create_log(
                '开始归档备份点:{}'.format(self.task.host_snapshot_name), '')

            lock_snapshots = list()
            self.task.set_status(FileSyncTask.FIND_SNAPSHOTS)
            self.task_content['sync_params']['mount_file_system_params'] = self._get_mount_file_system_params(
                self.task.host_snapshot)
            self.task_content['sync_params']['samba_params'] = self._get_samba_params()
            self.task_content['sync_params']['sync_params'] = self._get_sync_params()
            tmp_files, snapshots_ice, kvm_used_params = self._get_kvm_used_params(self.task.host_snapshot,
                                                                                  self.task.snapshot_datetime)
            self.task_content['sync_params']['kvm_used_params'] = kvm_used_params
            lock_snapshots.extend(snapshots_ice)
            self.task_content['tmp_files'].extend(tmp_files)
            self.task.set_status(FileSyncTask.LOCK_SNAPSHOTS)
            for lock_snapshot in lock_snapshots:
                SendTaskWork.lock_snapshots_u(lock_snapshot, self.task_content['lock_name'])

        except Exception as e:
            self.log_error(e)
            self.task_content['error'] = 'error:{}'.format(e)

        return self.task_content

    def _get_sync_params(self):  # 同步相关的参数
        sync_params_user = json.loads(self.task.schedule.ext_config).get('sync_rules', dict())
        host_name = self.task.schedule.host.name
        time_label = self.task.start_datetime.strftime('%Y%m%d%H%M')
        sync_destination_list = [sync_params_user['sync_destination'], ':', '\\', '归档', '\\', host_name, '\\',
                                 time_label]
        sync_destination_str = "".join(sync_destination_list)
        sync_params_user['sync_destination'] = sync_destination_str
        # sync_params_user['sync_destination'] = sync_params_user['sync_destination'] + '\\' +'归档'+ '\\'+host_name +'\\'+time_label
        # sync_params_user['sync_destination'] = sync_params_user['sync_destination'] + r':' + '\\' + '归档' + '\\'+host_name +'\\'+time_label

        return sync_params_user

    def _get_samba_params(self):
        return {
            'username': 'filesync',
            'userpwd': 'a00dce',
            'hostname': self.task.task_uuid,
            'read_only': False
        }

    def _get_kvm_used_params(self, host_snapshot, snapshot_datetime):
        tmp_files = list()
        ice_snapshots = list()
        kvm_used_params = {
            'logic': 'linux',
            'disk_ctl_type': 'scsi-hd',
            'aio_server_ip': '172.29.16.2',
            'ip_prefix': '172.29.140',
            'tap_name_prefix': 'filesync',
            'memory_mbytes': 128,
            'qcow_files': list(),
            'disksnapshots': list()
        }
        tmp_qcow_dir = r'/tmp/tmp_qcow/'
        os.makedirs(tmp_qcow_dir, exist_ok=True)
        tmp_qcow_file = os.path.join(tmp_qcow_dir, 'filesync{}.qcow2'.format(uuid.uuid4().hex))
        kvm_used_params['qcow_files'].append({
            'base': '/home/kvm_rpc/Clerware-7-x86_64-1611.mini.loader.qcow2',
            'new': tmp_qcow_file,
            'qcow_type': 'with_base'
        })
        tmp_files.append(tmp_qcow_file)
        for index, disk_snapshot in enumerate(host_snapshot.disk_snapshots.all()):
            timestamp = self.task.snapshot_datetime.timestamp() if snapshot_datetime else None
            if timestamp:
                disk_snapshot, timestamp = self.get_disk_snapshot(host_snapshot, disk_snapshot.disk.ident,
                                                                  timestamp)
            validator_list = [GetSnapshotList.is_disk_snapshot_object_exist,
                              GetSnapshotList.is_disk_snapshot_file_exist]
            snapshots_ice = GetSnapshotList.query_snapshots_by_snapshot_object(disk_snapshot, validator_list,
                                                                               timestamp=timestamp)
            if not snapshots_ice:
                xlogging.raise_and_logging_error('获取快照链失败', 'get snapshot failed', 169)
            snapshots = [{'path': snapshot.path, 'ident': snapshot.snapshot} for snapshot in
                         snapshots_ice]
            kvm_used_params['disksnapshots'].append({
                'images': snapshots,
                'nbd_type': 'gznbd',
                'scsi_id': disk_snapshot.disk.ident,
            })
            ice_snapshots.extend(snapshots_ice)
        return tmp_files, ice_snapshots, kvm_used_params

    @staticmethod
    def _get_mount_file_system_params(host_snapshot):
        ext_info = json.loads(host_snapshot.ext_info)
        system_infos = ext_info['system_infos']
        include_ranges = ext_info['include_ranges']
        system = system_infos['System']
        sys_os_class_type = 'linux' if 'LINUX' in (system['SystemCaption'].upper()) else 'windows'
        if sys_os_class_type == 'linux':
            linux_storage = system_infos['Storage']
        else:
            linux_storage = ''
        mount_file_system_params = {
            'read_only': False,
            'linux_storage': linux_storage,
            'include_ranges': include_ranges,
            'windows_volumes': ext_info['system_infos'].get('volumes', list()),
            'ostype': sys_os_class_type,
            'disklist': list()
        }
        disk_index_info = ext_info['disk_index_info']
        for index_info in disk_index_info:
            mount_file_system_params['disklist'].append({
                'diskid': index_info['snapshot_disk_index'],
                'nbd_uuid': DiskSnapshot.objects.get(ident=index_info['snapshot_disk_ident']).disk.ident
            })
        return mount_file_system_params

    @staticmethod
    def get_disk_snapshot(host_snapshot_object, disk_ident, time_stamp):
        disk_snapshot_ident, restore_timestamp = \
            GetDiskSnapshot.query_cdp_disk_snapshot_ident(host_snapshot_object, disk_ident, time_stamp)

        if disk_snapshot_ident is None or restore_timestamp is None:
            _logger.warning('no valid cdp disk snapshot use normal snapshot : {} {} {}'.format(
                host_snapshot_object, disk_ident, time_stamp))
            disk_snapshot_ident = GetDiskSnapshot.query_normal_disk_snapshot_ident(host_snapshot_object, disk_ident)

        return DiskSnapshot.objects.get(ident=disk_snapshot_ident), restore_timestamp


class FileSyncSendCommand(task.Task, WorkerLog):
    default_provides = 'task_content'

    def __init__(self, task_id, inject=None):
        super(FileSyncSendCommand, self).__init__('FileSyncSendCommand_{}'.format(task_id), inject=inject)
        self.task_id = task_id
        self.task = None
        self.task_content = None

    def execute(self, task_content, **kwargs):
        if FileSyncFlowEntrance.has_error(task_content):
            return task_content
        self.task_content = task_content
        self.task = FileSyncTask.objects.get(id=self.task_id)
        self.task.set_status(FileSyncTask.START_LOCAL_PROXY)
        try:
            file_backup_api('new', self.task_content['task_key'], self.task_content['sync_params'])
        except Exception as e:
            self.log_error(e)
            self.task_content['error'] = 'error:{}'.format(e)
        return self.task_content


class FileSyncWaiteEnd(task.Task, WorkerLog):
    default_provides = 'task_content'

    def __init__(self, task_id, inject=None):
        super(FileSyncWaiteEnd, self).__init__('FileSyncWaiteEnd_{}'.format(task_id), inject=inject)
        self.task_id = task_id
        self.task = None
        self.task_content = None
        self.sleep = None

    def execute(self, task_content, **kwargs):
        if FileSyncFlowEntrance.has_error(task_content):
            return task_content
        self.task_content = task_content
        self.task = FileSyncTask.objects.get(id=self.task_id)
        self.sleep = Sleep(self.task.schedule.id, FileSyncSchedule)
        try:
            time.sleep(2)  # 等待线程启动 然后在去询问状态
            try:
                while not self._is_task_successful():
                    self.sleep.sleep(30)
            finally:
                file_backup_api('delete', self.task_content['task_key'], '')
        except Exception as e:
            self.log_error(e)
            self.task_content['error'] = 'error:{}'.format(e)
        return self.task_content

    def _check_status(self):
        schedule_object = FileSyncSchedule.objects.get(id=self.task.schedule.id)
        task_object = FileSyncTask.objects.get(id=self.task_id)
        if not schedule_object.enabled:
            xlogging.raise_and_logging_error(
                r'用户取消，计划“{}”被禁用'.format(schedule_object.name),
                r'UserCancelCheck task_object_id : {} schedule_name : {} disable'.format(
                    task_object.id, schedule_object.name))
        if schedule_object.deleted:
            xlogging.raise_and_logging_error(
                '用户取消，计划“{}”被删除'.format(schedule_object.name),
                r'UserCancelCheck task_object_id : {} schedule_name : {} delete'.format(
                    task_object.id, schedule_object.name))

        if '"{}"'.format(xdata.CANCEL_TASK_EXT_KEY) in task_object.ext_config:
            xlogging.raise_and_logging_error(
                '用户取消，计划“{}”'.format(schedule_object.name),
                r'UserCancelCheck task_object_id : {} schedule_name : {} delete'.format(
                    task_object.id, schedule_object.name))

    def _is_task_successful(self):
        task_obj = FileSyncTask.objects.get(id=self.task_id)
        if task_obj.finish_datetime is not None:  # agent报告备份完成了
            if task_obj.successful:
                return True
            else:
                file_backup_api('raise_last_error', self.task_content['task_key'], '')
        else:
            if 'alive' not in file_backup_api('poll', self.task_content['task_key'], ''):
                file_backup_api('raise_last_error', self.task_content['task_key'], '')
        self._check_status()


class FileSyncFinisTask(task.Task, WorkerLog):
    def __init__(self, task_id, inject=None):
        super(FileSyncFinisTask, self).__init__('FileSyncFinisTask_{}'.format(task_id), inject=inject)
        self.task_id = task_id
        self.task_content = None
        self.task = None

    def execute(self, task_content, **kwargs):
        self.task_content = task_content
        self.task = FileSyncTask.objects.get(id=self.task_id)
        try:
            if FileSyncFlowEntrance.has_error(self.task_content):
                self.task.set_status(FileSyncTask.MISSION_FAIL, '', False)
                error_info = task_content['error'].lstrip('error:')
                if not error_info:
                    error_info = '内部异常，2345'
                self.task.create_log(
                    '归档备份点:{} 失败，{}'.format(self.task.host_snapshot_name, error_info), '')
                successful = False
            else:
                self.task.set_status(FileSyncTask.MISSION_SUCCESSFUL, '', False)
                self.task.create_log('归档备份点:{} 成功'.format(self.task.host_snapshot_name), '')
                successful = True
            ext_config = json.loads(self.task.ext_config)
            ext_config['running_task'] = {}
            self.task.ext_config = json.dumps(ext_config)
            self.task.finish_datetime = timezone.now()
            self.task.successful = successful
            self.task.save(update_fields=['finish_datetime', 'successful', 'ext_config'])

            self._unlock_snapshots()
            self._remove_tmp_file()
        except Exception as e:
            self.log_error('error :{}'.format(e))

        return None

    @xlogging.convert_exception_to_value(None)
    def _unlock_snapshots(self):
        Snapshot = namedtuple('Snapshot', ['path', 'snapshot'])
        snapshot_objs = [Snapshot(path=snapshot['path'], snapshot=snapshot['ident']) for snapshot in
                         self.task_content['locked_snapshots']]
        SendTaskWork.unlock_snapshots_u(snapshot_objs, self.task_content['lock_name'])

    @xlogging.convert_exception_to_value(None)
    def _remove_tmp_file(self):
        for file in self.task_content['tmp_files']:
            boxService.box_service.remove(file)


_book_ids = list()
_book_id_locker = threading.Lock()


class FileSyncFlowEntrance(threading.Thread):
    def __init__(self, task_id, name, flow_func, user_id=None, storage_path=None):
        super(FileSyncFlowEntrance, self).__init__()
        self.name = name
        self._engine = None
        self._book_uuid = None
        self.task_id = task_id
        self._flow_func = flow_func
        self._user_id = user_id
        self._storage_path = storage_path

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
            self._engine = engines.load_from_factory(self._flow_func, backend=backend, book=book, engine='serial',
                                                     factory_args=(
                                                         self.name, self.task_id))

            self._book_uuid = book.uuid
            return {'book_id': book.uuid, 'flow_id': self._engine.storage.flow_uuid}
        except Exception as e:
            _logger.error(r'generate_uuid failed {}'.format(e), exc_info=True)
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
        with _book_id_locker:
            if self._book_uuid in _book_ids:
                # 重复运行
                _logger.warning('FileSyncFlowEntrance book uuid:{} already run'.format(self._book_uuid))
                return
            else:
                _book_ids.append(self._book_uuid)
        _logger.info('FileSyncFlowEntrance _book_ids:{}'.format(_book_ids))
        try:
            with logging_listener.DynamicLoggingListener(self._engine):
                self._engine.run()
        except Exception as e:
            _logger.error(r'FileSyncFlowEntrance run engine {} failed {}'.format(self.name, e), exc_info=True)
        finally:
            with contextlib.closing(task_backend.get_backend().get_connection()) as conn:
                conn.destroy_logbook(self._book_uuid)
            with _book_id_locker:
                _book_ids.remove(self._book_uuid)
        self._engine = None

    @staticmethod
    def has_error(task_content):
        return task_content['error']


def create_sync_flow(name, task_id):
    flow = lf.Flow(name).add(
        FileSyncInit(task_id),  # 锁定快照, 创建子任务
        FileSyncSendCommand(task_id),  # 发送指令
        FileSyncWaiteEnd(task_id),
        FileSyncFinisTask(task_id),
    )
    return flow
