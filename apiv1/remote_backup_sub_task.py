import json
import os
import subprocess
import threading
import time
from datetime import datetime, timedelta
from functools import wraps

import requests
from django.utils import timezone

from apiv1.compress import CompressTaskThreading
from apiv1.models import DiskSnapshot, RemoteBackupSchedule, RemoteBackupTask, HostLog
from apiv1.remote_backup_api import NetWorkUnReachable, InvalidUser
from apiv1.snapshot import Tokens, GetSnapshotList, DiskSnapshotLocker
from apiv1.spaceCollection import DeleteCdpFileTask, DeleteDiskSnapshotTask
from box_dashboard import xdata, xlogging, boxService

_logger = xlogging.getLogger(__name__)


class DeleteScheduleException(Exception):
    pass


class QcowFileNotExists(Exception):
    pass


class NotEnableSchedule(Exception):
    pass


class SubTaskFailed(Exception):
    ERROR_CODE_BASE = 100
    ERROR_CODE_NETWORK_CONNECTION = ERROR_CODE_BASE + 1  # 网络异常
    ERROR_CODE_NETWORK_INVALID_USER = ERROR_CODE_BASE + 2  # 错误的用户名合密码
    ERROR_CODE_LOCAL_SNAPSHOT_MISS = ERROR_CODE_BASE + 3  # 本地快照丢失
    ERROR_CODE_REMOTE_SNAPSHOT_MISS = ERROR_CODE_BASE + 4  # 远端快照丢失
    ERROR_CODE_NOT_ENOUGH_SPACE = ERROR_CODE_BASE + 5  # 空间不足

    def __init__(self, msg, debug, code=-1):
        super(SubTaskFailed, self).__init__(msg)
        self.debug = debug
        self.msg = msg
        self.code = code


def retry(fuc):
    @wraps(fuc)
    def wrap(*args, **kwargs):
        dead_line = datetime.now() + timedelta(minutes=10)
        while datetime.now() < dead_line:
            try:
                rev = fuc(*args, **kwargs)
            except InvalidUser:
                raise SubTaskFailed('同步子任务失败，连接参数异常', 'invalid user and password',
                                    SubTaskFailed.ERROR_CODE_NETWORK_INVALID_USER)
            except requests.ConnectionError as e:
                time.sleep(10)
                _logger.info('retry func:{} dead_line:{}, error:{}'.format(fuc.__qualname__, dead_line, e))
            else:
                return rev
        raise SubTaskFailed('同步子任务失败，网络通信异常', 'net work unreachable', SubTaskFailed.ERROR_CODE_NETWORK_CONNECTION)

    return wrap


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


class RemoteBackupSubTaskThreading(threading.Thread, WorkerLog):
    def __init__(self, sub_task_object, web_api):
        super(RemoteBackupSubTaskThreading, self).__init__()
        self._sub_task_object = sub_task_object
        self.sub_task_id = sub_task_object.id
        self.disk_ident = sub_task_object.local_snapshot.disk.ident
        self.is_cdp = sub_task_object.local_snapshot.is_cdp
        self.task_uuid = sub_task_object.local_snapshot.ident
        self.popen = None
        self.is_create_empty_qcow = self._is_create_empty_qcow()
        self._host_object = self._sub_task_object.main_task.host_snapshot.host
        self._need_lock_snapshots = list()
        self._task_name = 'remote_back_up_sub_task{}'.format(self.sub_task_id)
        self._force_stop = False
        self.name = 'remote_task_{}_{}_{}'.format(self._sub_task_object.main_task.schedule.id,
                                                  self._sub_task_object.main_task.id,
                                                  self._sub_task_object.id)
        self.web_api = web_api
        self.error = None

    @xlogging.convert_exception_to_value(None)
    def run(self):
        while True:
            self.log_info('start run ...')
            self.log_info('task_uuid {} disk_ident {}'.format(self.task_uuid, self.disk_ident))
            try:
                if self.is_create_empty_qcow:
                    self.run_with_empty()
                else:
                    self.run_with_not_empty()
                break
            except SubTaskFailed as su:  # 任务失败不可重试
                self.log_error('catch SubTaskFailed, {}|{}'.format(su.msg, su.debug))
                self.error = su
                self.tear_down()
                if su.code in (SubTaskFailed.ERROR_CODE_LOCAL_SNAPSHOT_MISS,
                               SubTaskFailed.ERROR_CODE_REMOTE_SNAPSHOT_MISS):
                    self._finish_sub_task(False)
                break
            except Exception as e:
                self.log_error('catch Exception, {}'.format(e))
            finally:
                self._close_task()
                self._unlock_snapshots()
                self.log_info('start end')

    def is_failed(self):
        return (self._sub_task_object.finish_datetime is not None) and (not self._sub_task_object.successful)

    def _is_create_empty_qcow(self):
        ext_config = json.loads(self._sub_task_object.ext_config)
        disk_snapshot_list = ext_config.get('disk_snapshot_list', list())
        if disk_snapshot_list == ['empty']:
            return True
        return False

    def run_with_empty(self):
        self._set_status(RemoteBackupTask.TRANS_DATA)
        self._check_validity()
        self._clean_local_snapshot()
        self._start_create_empty_qcow()
        self._finish_sub_task()

    def run_with_not_empty(self):
        self._check_validity()
        self._check_snapshot_valid()
        self._kill_remote_logic()
        self._set_status(RemoteBackupTask.TRANS_DATA)
        self._close_task()
        self._kill_local_rs_module()
        self._clean_local_snapshot()
        self._start_remote_logic()
        self._lock_snapshots()
        self._start_local_rs_module()
        self._check_remote_logic()
        self._join_local_rs_module()
        self._finish_sub_task()
        self._update_cdp_last_timestamp()

    @xlogging.convert_exception_to_value(None)
    def tear_down_with_paused(self):
        if self.is_cdp:
            self._kill_remote_logic()
            self._join_local_rs_module()
        else:
            self.tear_down()

    @xlogging.convert_exception_to_value(None)
    def tear_down(self):
        if self.is_create_empty_qcow:
            self._clean_local_snapshot()
        else:
            self._kill_local_rs_module()
            self._kill_remote_logic()
            self._clean_local_snapshot()

    def force_stop(self):
        self.log_info('start force stop!')
        self._force_stop = True

    @xlogging.convert_exception_to_value(None)
    def _kill_local_rs_module(self):
        if not self.popen:
            return None
        while self.popen.poll() is None:
            self.log_info('_kill_local_rs_module sub_task_{}, pid:{}'.format(self.sub_task_id, self.popen.pid))
            os.kill(self.popen.pid, 9)
            time.sleep(5)

    @xlogging.convert_exception_to_value(None)
    def _clean_local_snapshot(self):
        local_snapshot = self._sub_task_object.local_snapshot
        if self.is_cdp:
            # self._delete_cdp(local_snapshot)
            pass
        else:
            self._del_normal_qcow(local_snapshot)

    def _del_normal_qcow(self, local_snapshot):
        task_name = 'delete_disk_task_{}'.format(self.sub_task_id)
        while True:
            if DeleteDiskSnapshotTask.do_del(task_name, False, local_snapshot.ident, local_snapshot.image_path):
                self.log_info(
                    '_clean_local_snapshot disk_snapshot {} successful '.format(local_snapshot))
                break
            time.sleep(10)

    def _delete_cdp(self, local_snapshot):
        while True:
            if DeleteCdpFileTask.do_del(local_snapshot.image_path):
                self.log_info('_clean_local_snapshot successful cdp:{}'.format(local_snapshot.image_path))
                break
            time.sleep(10)

    def _start_local_rs_module(self):
        cmd = list()
        python_path = '/root/.pyenv/versions/3.4.4/bin/python3.4'
        module_path = '/usr/sbin/aio/remotess/rmtssmain.py'
        args = self._get_args()
        cmd.append(python_path)
        cmd.append(module_path)
        cmd.extend(args)
        self.log_info('cmd {}'.format(cmd))
        self.popen = subprocess.Popen(cmd, universal_newlines=True)
        if self.popen.poll() is not None:
            raise Exception('_start_local_rs_module error:{}'.format(self.popen.communicate()))
        return None

    def _join_local_rs_module(self):
        while self.popen.poll() is None:
            self.log_debug('waite process join')
            time.sleep(5)
        if self.popen.returncode != 0:
            raise Exception('_join_local_rs_module return code:{} != 0, error:{}|{}'.format(
                self.popen.returncode, *self.popen.communicate()))
        return None

    @xlogging.convert_exception_to_value(None)
    def _close_task(self):
        if self.is_create_empty_qcow:
            return None
        self._close_task_real()

    @retry
    def _close_task_real(self):
        self.web_api.http_close_remote_backup_logic(self.task_uuid, self.task_uuid)

    @retry
    def _kill_remote_logic(self):
        return self.web_api.http_kill_remote_backup_logic(self.task_uuid, self.task_uuid)

    @retry
    def _start_remote_logic_real(self, disk_snapshot_list_str, remote_ident):
        return self.web_api.http_start_remote_backup_logic(self.task_uuid, self.task_uuid,
                                                           remote_ident, disk_snapshot_list_str,
                                                           self.get_cdp_start_time())

    @retry
    def _query_task_status(self):
        return self.web_api.http_query_remote_backup_status(self.task_uuid, self.task_uuid)

    def _start_remote_logic(self):
        ext_config = json.loads(self._sub_task_object.ext_config)
        disk_snapshot_list = ext_config.get('disk_snapshot_list', list())
        disk_snapshot_list_str = json.dumps(disk_snapshot_list)
        remote_ident = self._sub_task_object.remote_snapshot_ident
        rs = self._start_remote_logic_real(disk_snapshot_list_str, remote_ident)
        assert int(rs.get('code', -1)) == 0, '_start_remote_logic error :{}'.format(rs)

    # 等待远端任务结束
    def _check_remote_logic(self):
        status = self._query_task_status()
        self.log_info('_check_remote_logic status:{}'.format(status))
        not_work_list = list()
        while not status['finished']:
            self._check_validity()
            status = self._query_task_status()
            self.log_info('_check_remote_logic status:{}'.format(status))
            if status['finished']:
                break
            if status['work_type'] == xdata.REMOTE_BACKUP_NONE_WORK:
                if sum(not_work_list) > 4:
                    raise Exception('not find worker!')
                else:
                    not_work_list.append(1)
            else:
                self._update_cdp_last_timestamp()
                self._update_qcow_progress(status['progress'])
                self._is_rs_exists()
            time.sleep(30)
        # 发送end指令
        info = self._kill_remote_logic()
        assert int(info.get('code', -1)) == 0, '_kill_remote_logic rev valid {}'.format(info)

    def update_progress(self):
        pass

    def _update_cdp_last_timestamp(self):
        disk_snapshot = self._sub_task_object.local_snapshot
        if self.is_cdp:
            try:
                Tokens.update_disk_snapshot_cdp_timestamp(disk_snapshot.cdp_info, False)
            except Exception as e:
                self.log_warning('_update_cdp_last_timestamp update_disk_snapshot_cdp_timestamp error:{}'.format(e))
        else:
            pass

    def _finish_sub_task(self, successful=True):
        self._sub_task_object.finish_datetime = timezone.now()
        self._sub_task_object.successful = successful
        self._sub_task_object.save(update_fields=['finish_datetime', 'successful'])

    def _is_schedule_enable_ssl(self):
        schedule = RemoteBackupSchedule.objects.get(id=self._sub_task_object.main_task.schedule.id)
        sche_ext = json.loads(schedule.ext_config)
        return sche_ext['transfer_encipher']['value'] == 'yes'

    def _get_schedule_bandwidth_config_file(self):
        schedule = RemoteBackupSchedule.objects.get(id=self._sub_task_object.main_task.schedule.id)
        sche_ext = json.loads(schedule.ext_config)
        config_path = sche_ext['bandwidth_config_path']
        if os.path.exists(config_path):
            return config_path

        return None

    def _get_args(self):
        args_list = list()

        aio_info = json.loads(self._host_object.aio_info)
        aio_ip = aio_info['ip']
        # for dataq_ip_port
        if self._is_schedule_enable_ssl():
            ip_port = '{}:{}'.format(aio_ip, '20001')
            args_list.extend(['--dataq_ip_port', ip_port, '--dataq_protocol', 'ssl'])
        else:
            ip_port = '{}:{}'.format(aio_ip, '20000')
            args_list.extend(['--dataq_ip_port', ip_port, '--dataq_protocol', 'tcp'])

        if self._get_schedule_bandwidth_config_file():
            args_list.extend(['--rate_limit', self._get_schedule_bandwidth_config_file()])

        # for qcow_img_ip_port
        args_list.extend(['--qcow_img_ip_port', '127.0.0.1:21101'])

        # for sanpshot_curr
        local_snapshot = self._sub_task_object.local_snapshot
        snapshot = self._get_snapshot_str(local_snapshot)
        args_list.extend(['--sanpshot_curr', snapshot])

        # for snapshot_dep
        last_disk_snapshot_object = local_snapshot.parent_snapshot
        if last_disk_snapshot_object:
            validator_list = [GetSnapshotList.is_disk_snapshot_object_exist,
                              GetSnapshotList.is_disk_snapshot_file_exist]
            disk_snapshots = GetSnapshotList.query_snapshots_by_snapshot_object(last_disk_snapshot_object,
                                                                                validator_list)
            if len(disk_snapshots) == 0:
                raise SubTaskFailed('同步子任务失败，获取快照链失败', 'not found dep snapshots',
                                    SubTaskFailed.ERROR_CODE_LOCAL_SNAPSHOT_MISS)
            for disk_snapshot in disk_snapshots:
                _snapshot = self._get_snapshot_str_img(disk_snapshot)
                args_list.extend(['--snapshot_dep', _snapshot])
        else:
            disk_snapshots = list()

        if disk_snapshots:
            self._need_lock_snapshots = disk_snapshots
        else:
            self._need_lock_snapshots = list()

        # for task_id
        args_list.extend(['--task_id', self.task_uuid])

        # for snapshot_type
        args_list.extend(['--snapshot_type', 'cdp' if self.is_cdp else 'qcow2'])

        # for dataq_number
        args_list.extend(['--dataq_number', '10'])

        # for disk_token
        args_list.extend(['--disk_token', self.task_uuid])

        # for disk_bytesize
        args_list.extend(['--disk_bytesize', str(local_snapshot.bytes)])

        return args_list

    def _get_snapshot_str(self, disk_snapshot):
        img_path = disk_snapshot.image_path
        name = 'all!' if self.is_cdp else disk_snapshot.ident
        snapshot = '{path},{name}'.format(path=img_path, name=name)
        return snapshot

    def _get_snapshot_str_img(self, snap):
        img_path = snap.path
        name = 'all!' if DiskSnapshot.is_cdp_file(img_path) else snap.snapshot
        snapshot = '{path},{name}'.format(path=img_path, name=name)
        return snapshot

    def _is_rs_exists(self):
        if self.popen.poll() is None:
            return True
        else:
            if self.popen.returncode == 124:
                raise SubTaskFailed('磁盘空间不足', 'not enough space', SubTaskFailed.ERROR_CODE_NOT_ENOUGH_SPACE)
            raise Exception(
                'sub process is not exists! {} {} {}'.format(self.popen.returncode, *self.popen.communicate()))

    def _update_qcow_progress(self, process):
        if not self.is_cdp:
            ext_config = json.loads(self._sub_task_object.ext_config)
            ext_config['process'] = process
            self._sub_task_object.ext_config = json.dumps(ext_config)
            self._sub_task_object.save(update_fields=['ext_config'])
        else:
            pass

    @xlogging.convert_exception_to_value('')
    def get_cdp_start_time(self):
        if self.is_cdp:
            rs = ''
            disk_snapshot = self._sub_task_object.local_snapshot
            self._fix_cdp_file(disk_snapshot)
            try:
                start_timestamp, end_timestamp = boxService.box_service.queryCdpTimestampRange(disk_snapshot.image_path,
                                                                                               True)
            except Exception:
                pass
            else:
                rs = '{:.6f}'.format(end_timestamp) if end_timestamp else ''

            if not rs:
                self._delete_cdp(disk_snapshot)  # 需要删除文件
            return rs
        else:
            return ''

    def _fix_cdp_file(self, disk_snapshot):
        pass

    def _start_create_empty_qcow(self):
        local_snapshot = self._sub_task_object.local_snapshot
        GetSnapshotList.create_empty_snapshot(local_snapshot, r'内部异常，代码2312', self._task_name, self._task_name)

    def _check_validity(self):
        schedule = RemoteBackupSchedule.objects.get(id=self._sub_task_object.main_task.schedule.id)
        if schedule.deleted or not schedule.enabled or self._force_stop:
            raise SubTaskFailed('empty', 'deleted or not enabled')

    def _create_log(self, msg, debug):
        HostLog.objects.create(host=self._host_object, type=HostLog.LOG_REMOTE_BACKUP,
                               reason=json.dumps({'description': msg, 'debug': debug},
                                                 ensure_ascii=False))

    def _set_status(self, status):
        self._sub_task_object.main_task.set_status(status)

    @retry
    def _http_check_qcow_file_exists(self):
        ext_config = json.loads(self._sub_task_object.ext_config)
        disk_snapshot_list = ext_config.get('disk_snapshot_list', list())
        if not disk_snapshot_list:
            snapshot = {'snapshot': self._sub_task_object.remote_snapshot_ident,
                        'path': self._sub_task_object.remote_snapshot_path}
            if self.is_cdp:
                snapshot['snapshot'] = 'all'
            disk_snapshot_list = [snapshot]
        self.log_debug('start _http_check_qcow_file_exists {}'.format(disk_snapshot_list))
        rs = self.web_api.http_check_qcow_file_exists(json.dumps(disk_snapshot_list))
        self.log_debug('end _http_check_qcow_file_exists {}, result {}'.format(disk_snapshot_list, rs))
        return rs['is_valid']

    def _check_snapshot_valid(self):
        if not self._http_check_qcow_file_exists():
            self.log_warning('snapshots is not exists, raise QcowFileNotExists Exception')
            raise SubTaskFailed('同步子任务失败，源快照文件被删除', 'file not exists', SubTaskFailed.ERROR_CODE_REMOTE_SNAPSHOT_MISS)
        else:
            pass

    def _lock_snapshots(self):
        for snap in self._need_lock_snapshots:
            if not DiskSnapshot.is_cdp_file(snap.path):
                CompressTaskThreading().update_task_by_disk_snapshot(snap.path,
                                                                     snap.snapshot)

            DiskSnapshotLocker.lock_file(snap.path, snap.snapshot, self._task_name)

    def _unlock_snapshots(self):
        for snap in self._need_lock_snapshots:
            DiskSnapshotLocker.unlock_file(snap.path, snap.snapshot, self._task_name)
