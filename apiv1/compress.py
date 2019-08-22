import threading
import time
import os
import datetime
import subprocess
import json

from apiv1.models import CompressTask, DiskSnapshot, StorageNode
from box_dashboard import xlogging, boxService
from apiv1.runCmpr import runCmpr

_logger = xlogging.getLogger(__name__)
_compress_task_lock = threading.RLock()
_enable_compress = boxService.get_compression_switch()


class Singleton(type):
    def __init__(cls, name, bases, dicts):
        super(Singleton, cls).__init__(name, bases, dicts)
        cls._instance = None

    def __call__(cls, *args, **kw):
        if cls._instance is None:
            cls._instance = super(Singleton, cls).__call__(*args, **kw)
        return cls._instance


class CompressTaskThreading(threading.Thread, metaclass=Singleton):
    global _compress_task_lock

    def __init__(self):
        super(CompressTaskThreading, self).__init__()
        self.pid = None
        self.task_id = None
        self.max_to_cmp_bytes = 0.5 * 1024 ** 3  # 每次压缩的数据量控制在0.5GB
        self.signal_num = 35

    def run(self):
        _logger.debug('CompressTaskThreading start')

        time.sleep(60)
        while True:
            try:
                self._run_until_no_task()
            except Exception as e:
                _logger.error('CompressTaskThreading error : {}'.format(e), exc_info=True)
            time.sleep(60)

    @staticmethod
    def _all_waiting_tasks():
        return CompressTask.objects.filter(completed=False, next_start_date__lte=datetime.datetime.now()).order_by(
            '-create_datetime')

    @staticmethod
    def _is_current_disk_snapshot_available(task):
        disk_snapshot_object = task.disk_snapshot
        if disk_snapshot_object.reference_tasks:
            _logger.debug('_is_current_disk_snapshot_available disk_snapshot is not avaiable, is used by:{}'.format(
                disk_snapshot_object.reference_tasks))
            return False
        return True

    @staticmethod
    def _is_qcow_file_available(task):
        disk_snapshot_object = task.disk_snapshot
        disk_snapshot_objects_with_same_qcow = DiskSnapshot.objects.filter(image_path=disk_snapshot_object.image_path)
        for item in disk_snapshot_objects_with_same_qcow:
            if 'cdp_merge_sub_task' in item.reference_tasks or 'delete_disk_task' in item.reference_tasks:
                _logger.debug('_is_qcow_file_available disk_snapshot is not avaiable, is used by:{}'.format(
                    item.reference_tasks))
                return False
        else:
            return True

    @staticmethod
    def _query_task_id(disk_snapshot_object):
        try:
            return disk_snapshot_object.compress_tasks.all().first().id
        except Exception as e:
            _logger.error('_query_task_id error:{},{}'.format(e, disk_snapshot_object.id))
            return None

    @staticmethod
    def _query_task_id_by_path_ident(path, ident):
        try:
            return DiskSnapshot.objects.get(image_path=path, ident=ident).compress_tasks.all().first().id
        except Exception as e:
            return None

    @staticmethod
    def _get_dev_name(path):
        mount_point = path.split(r'/images')
        st_node_obj = StorageNode.objects.get(path=mount_point[0])
        exe_info = json.loads(st_node_obj.config)
        return exe_info['logic_device_path']

    @staticmethod
    def _check_path(*args):
        for path in args:
            if path is None:
                continue
            if not os.path.exists(path):
                raise Exception('_check_path path:{} is not exists'.format(path))

    @staticmethod
    def _gen_map_path(disk_snapshot_object):
        return str(disk_snapshot_object.image_path) + '_' + str(disk_snapshot_object.ident) + '.map'

    @staticmethod
    def _get_map_total_lines(bit_map_path):
        if os.path.exists(bit_map_path):
            with open(bit_map_path) as f:
                return len(f.readlines())
        else:
            return 0

    @staticmethod
    def _update_task(task):
        if task.next_start_lines >= task.total_lines:
            task.completed = True
            task.save(update_fields=['completed'])

    @staticmethod
    def _remove_task(task_id):
        task_object = CompressTask.objects.get(id=task_id)
        task_object.completed = True
        task_object.save(update_fields=['completed'])

    def _cancel_and_wait_task(self, task_id):
        with _compress_task_lock:
            if self._check_task_is_running(task_id):
                self._cancel_task(task_id)
            else:
                pass

    def update_task_by_disk_snapshot(self, disk_snapshot_file_path, ident):
        with _compress_task_lock:
            task_id = self._query_task_id_by_path_ident(disk_snapshot_file_path, ident)
            if task_id:
                self._cancel_and_wait_task(task_id)

    def remove_task_by_disk_snapshot(self, disk_snapshot_file_path, ident):
        with _compress_task_lock:
            task_id = self._query_task_id_by_path_ident(disk_snapshot_file_path, ident)
            if task_id:
                self._cancel_and_wait_task(task_id)
                self._remove_task(task_id)

    @xlogging.db_ex_wrap
    def _run_until_no_task(self):
        if self._not_enable_run():
            return None

        with _compress_task_lock:
            task = self._fetch_valid_task()
            if not task:
                return None
        _logger.debug('_run_until_no_task fetch one task, task id :{}'.format(task.id))
        if self._run_task(task):
            with _compress_task_lock:
                self._update_task(task)

    def _not_enable_run(self):
        if not _enable_compress:
            _logger.error('CompressTaskThreading not work, not _enable_compress')
            return True

        if not os.path.exists(r'/sbin/aio/compresspp'):
            _logger.error('CompressTaskThreading not work, not find /sbin/aio/compresspp file')
            return True

        if self._kvm_exists():
            _logger.error('CompressTaskThreading not work, find kvm')
            return True

        if runCmpr.calculate_available_cores() <= 0:
            _logger.error('CompressTaskThreading not work, calculate_available_cores <= 0')
            return True

        return False

    def _fetch_valid_task(self):
        for task in self._all_waiting_tasks():
            if self._check_task_ok(task):
                return task
        else:
            return None

    def _check_task_ok(self, task):
        return self._is_current_disk_snapshot_available(task) and self._is_qcow_file_available(task)

    def _check_task_is_running(self, task_id):
        _logger.debug(
            '_check_task_is_running tasl_id:{}, pid:{}, to cancel task id:{}'.format(self.task_id, self.pid,
                                                                                     task_id))
        if task_id == self.task_id and self.pid:
            return True
        return False

    def _cancel_task(self, task_id):
        while self._check_task_is_running(task_id):
            try:
                _logger.debug(
                    '_cancel_task task id:{} pid:{}, signal_num:{}'.format(self.task_id, self.pid, self.signal_num))
                os.kill(self.pid, self.signal_num)
                time.sleep(15)
            except Exception as e:
                _logger.error('_cancel_task fail task id:{} pid:{}, error:{}'.format(self.task_id, self.pid, e))

    def _run_task(self, task):
        try:
            with _compress_task_lock:
                self.task_id = task.id
                task.total_lines = self._cal_total_lines(task)
                task.next_start_date = datetime.datetime.now()
                task.save(update_fields=['total_lines'])
            _args = self._get_args(task)
            _logger.debug(_args)
            cmd_lines, next_lines = runCmpr.generate_compress_params_and_run_it(*_args)
            _logger.debug('cmd_lines:{}'.format(cmd_lines))
            _logger.debug('next_lines:{}'.format(next_lines))
            if cmd_lines is None:  # 标志此任务已经完成
                self._remove_task(task.id)
                self._clear_pid_task_id()
                return True

            with subprocess.Popen(cmd_lines, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True,
                                  universal_newlines=True) as fd:
                if fd.pid is None:
                    raise Exception('start subprocess fail')
                else:
                    _logger.info(
                        'CompressTaskThreading start compress task, task id:{} pid:{}'.format(self.task_id, fd.pid))
                    self.pid = fd.pid
                    stdout, stderr = fd.communicate()

            if fd.returncode != 0:
                _logger.error(
                    '_run_task exe fail, return code:{}, pid:{}, error:{}, task id {}'.format(fd.returncode, fd.pid,
                                                                                              stderr, self.task_id))
                raise Exception('start subprocess fail')
            else:
                _logger.info('CompressTaskThreading task finished, task id :{}'.format(self.task_id))
                next_lines_db = next_lines if next_lines else task.total_lines
                task.next_start_lines = next_lines_db
                task.save(update_fields=['next_start_lines'])
                self._clear_pid_task_id()
                return True
        except Exception as e:
            self._clear_pid_task_id()
            _logger.error('_run_task fail:task id {}, error:{}, begin to modify next run time'.format(task.id, e),
                          exc_info=True)
            task.next_start_date += datetime.timedelta(seconds=1800)
            task.save(update_fields=['next_start_date'])
            return False

    def _get_args(self, task):
        # dev_name = self._get_dev_name(task.disk_snapshot.image_path)
        start_lines = task.next_start_lines
        need_compress_bytes = self._get_to_cmp_bytes_num(task)
        current_qcow_path = task.disk_snapshot.image_path
        current_qcow_map_path = self._gen_map_path(task.disk_snapshot)
        parent_qcow_map_path = self._gen_map_path(task.disk_snapshot.parent_snapshot) if \
            task.disk_snapshot.parent_snapshot else None
        self._check_path(current_qcow_path, current_qcow_map_path, parent_qcow_map_path)
        return [self.signal_num, current_qcow_path, current_qcow_map_path, start_lines, need_compress_bytes,
                parent_qcow_map_path, True]

    def _get_to_cmp_bytes_num(self, task):
        return self.max_to_cmp_bytes

    def _cal_total_lines(self, task):
        if task.total_lines:
            return task.total_lines
        else:
            disk_snapshot_object = task.disk_snapshot
            bit_map_path = self._gen_map_path(disk_snapshot_object)
            return self._get_map_total_lines(bit_map_path)

    def _clear_pid_task_id(self):
        self.pid = None
        self.task_id = None

    @xlogging.convert_exception_to_value(False)
    def _kvm_exists(self):
        # cmd = r'ps aux|grep qemu-kvm|grep -v grep|wc -l'
        # with subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        #                       universal_newlines=True) as p:
        #     stdout, stderr = p.communicate()
        # if p.returncode != 0:
        #     return False
        # return int(stdout.strip()) > 0
        return False
