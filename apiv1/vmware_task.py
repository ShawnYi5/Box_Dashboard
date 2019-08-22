# coding=utf-8
import threading
import contextlib
import datetime
import subprocess
import json
import time
import os
import signal
from collections import namedtuple
from functools import partial
import uuid
import shutil

from taskflow import engines
from taskflow import task
from taskflow.listeners import logging as logging_listener
from taskflow.patterns import linear_flow as lf
from taskflow.persistence import models
from rest_framework import status
from django.utils import timezone
from tools.vm_proxy import VmwareProxy
from tools.backup import load_vmnbd_params
from box_dashboard import xlogging, task_backend, xdatetime, boxService, xdata
from apiv1.models import VirtualMachineRestoreTask, VirtualCenterConnection
from apiv1.restore import PeRestore
from apiv1.htb_task import SendTaskWork
from apiv1.snapshot import SnapshotsUsedBitMapGeneric

_logger = xlogging.getLogger(__name__)
VM_NBD = '/sbin/aio/vmware_agent/vmDiskSnapshot2nbd/vm_nbd'
LIB_PATH = '/sbin/aio/vmware_agent/vmDiskSnapshot2nbd/vmware-vix-disklib-distrib/lib64'


def is_cancel(task):
    return xdata.CANCEL_TASK_EXT_KEY in load_ext_config(task)


def raise_exception_when_cancel(task):
    if is_cancel(task):
        raise Exception('user cancel')
    else:
        pass


def load_ext_config(task):
    return json.loads(VirtualMachineRestoreTask.objects.get(id=task.id).ext_config)


def execute_cmd(cmd, shell=False, is_waite=True, **kwargs):
    _logger.info('execute_cmd cmd:{}'.format(cmd))
    process = subprocess.Popen(cmd,
                               shell=shell,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE,
                               universal_newlines=True, **kwargs)
    if is_waite:
        stdout, stderr = process.communicate()
        _logger.info('execute_cmd out:{}|{}|{}'.format(process.returncode, stdout, stderr))
        return process.returncode, stdout, stderr
    else:
        _logger.info('execute_cmd out:{}|pid:{}'.format(process, process.pid))
        return process


def get_thumb(address, port):
    cmd = 'echo | openssl s_client -connect {}:{} |& openssl x509 -fingerprint -noout'.format(address, port)
    rs = execute_cmd(cmd, shell=True)
    if rs[0] == 0:
        return rs[1].split('=')[1].strip()
    xlogging.raise_and_logging_error('获取关键信息失败', 'get_thumb error')


def get_api_instance(host, user, password, port):
    try:
        api = VmwareProxy(host, user, password, port)
    except Exception as e:
        _logger.error('_get_api_instance error:{}'.format(e), exc_info=True)
        return None
    else:
        return api


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


class VMRInit(task.Task, WorkerLog):
    default_provides = 'task_content'

    def __init__(self, task_id, inject=None):
        super(VMRInit, self).__init__('VMRInit_{}'.format(task_id), inject=inject)
        self.task = VirtualMachineRestoreTask.objects.get(id=task_id)
        self.ext_config = dict()

    def execute(self, *args, **kwargs):
        task_content = {
            'error': '',
            'restore_snapshots': list(),
            'lock_name': 'vmr_restore_{}'.format(self.task.id)
        }
        try:
            self.task.set_status(VirtualMachineRestoreTask.INIT)
            raise_exception_when_cancel(self.task)
            self.ext_config = load_ext_config(self.task)
            host_snapshot = self.task.host_snapshot
            host_snapshot_ext_config = json.loads(host_snapshot.ext_info)
            vc = VirtualCenterConnection.objects.get(id=self.ext_config['vcenter_id'])
            task_content['host'] = vc.address
            task_content['user'] = vc.username
            task_content['password'] = vc.password
            task_content['port'] = vc.port
            task_content['disable_ssl'] = vc.disable_ssl
            task_content['thumb'] = get_thumb(vc.address, vc.port)
            task_content['task_dir'] = os.path.join('/dev/shm/{}'.format(uuid.uuid4().hex))
            self.log_info('task dir:{}'.format(task_content['task_dir']))
            os.makedirs(task_content['task_dir'], exist_ok=True)
            task_content['vmware_config'] = os.path.join(task_content['task_dir'], 'vmware.cfg.json')
            task_content['src_vmware_config'] = json.dumps(host_snapshot_ext_config['vmware_config'],
                                                           ensure_ascii=False)

            self._dump_json_to_file(host_snapshot_ext_config['vmware_config'], task_content['vmware_config'])

            lock_snapshots = list()
            self.task.set_status(VirtualMachineRestoreTask.FIND_SNAPSHOTS)
            host_snapshot = self.task.host_snapshot
            for disk_snapshot in host_snapshot.disk_snapshots.all():
                snapshot_info = dict()
                meta_data = json.loads(disk_snapshot.ext_info)['meta_data']
                _uuid = uuid.uuid4().hex
                snapshot_info['disk_ident'] = disk_snapshot.disk.ident
                snapshot_info['disk_bytes'] = disk_snapshot.bytes
                snapshot_info['bitmapfile'] = os.path.join(task_content['task_dir'], '{}.bitmap'.format(_uuid))
                snapshot_info['meta_filepath'] = os.path.join(task_content['task_dir'], '{}.meta'.format(_uuid))
                disk_snapshot, snapshots = PeRestore.get_disk_snapshot_object(disk_snapshot.ident, None, self.name)
                self._generate_bitmap(snapshots, snapshot_info['bitmapfile'])
                self._dump_json_to_file(meta_data, snapshot_info['meta_filepath'])
                snapshot_info['disk_snapshot_ident'] = disk_snapshot.ident
                snapshot_info['snapshots'] = [{'path': snapshot.path, 'ident': snapshot.snapshot} for snapshot in
                                              snapshots]
                snapshot_info['disk_key'] = meta_data['disk_key']
                snapshot_info['output_file'] = os.path.join(task_content['task_dir'], '{}.output'.format(_uuid))
                task_content['restore_snapshots'].append(snapshot_info)
                lock_snapshots.append(snapshots)
            self.task.set_status(VirtualMachineRestoreTask.LOCK_SNAPSHOTS)
            for lock_snapshot in lock_snapshots:
                SendTaskWork.lock_snapshots_u(lock_snapshot, task_content['lock_name'])
        except Exception as e:
            self.log_error(e)
            task_content['error'] = '{}'.format(e)

        return task_content

    def _generate_bitmap(self, snapshots, file_name):
        flag = r'PiD{:x} BoxDashboard|VMRInit _generate_bitmap{}'.format(os.getpid(), self.task.id)
        bit_map = SnapshotsUsedBitMapGeneric(snapshots, flag).get()
        if bit_map:
            with open(file_name, 'wb') as f:
                f.write(bit_map)
        else:
            with open(file_name, 'wb') as f:
                f.write(b'')

    def _dump_json_to_file(self, content, file_name):
        with open(file_name, 'w') as f:
            json.dump(content, f)

    def _get_disk_key_from_meta(self, meta_file):
        with open(meta_file) as f:
            content = json.load(f)
        return content['disk_key']


class VMRCreateVM(task.Task, WorkerLog):
    default_provides = 'task_content'

    def __init__(self, task_id, inject=None):
        super(VMRCreateVM, self).__init__('VMRCreateVM_{}'.format(task_id), inject=inject)
        self.task = VirtualMachineRestoreTask.objects.get(id=task_id)

    def execute(self, task_content, **kwargs):
        if VMRFlowEntrance.has_error(task_content):
            return task_content
        self.task_content = task_content
        try:
            self.task.set_status(VirtualMachineRestoreTask.CREATE_VIRTUAL_MACHINE)
            raise_exception_when_cancel(self.task)
            ext_config = json.loads(self.task.ext_config)
            vm_cfg = self._gen_virtual_machine_config(ext_config)
            vm = self._duplicate_virtual_machine(ext_config, vm_cfg)
            self.task_content['vm_name'] = vm_cfg['name']
        except Exception as e:
            self.log_error(e)
            task_content['error'] = '{}'.format(e)
        return task_content

    def _get_api_instance(self, host, user, password, port):
        try:
            api = VmwareProxy(host, user, password, port)
        except Exception as e:
            _logger.error('_get_api_instance error:{}'.format(e), exc_info=True)
            return None
        else:
            return api

    def _gen_virtual_machine_config(self, ext_config):
        vm_cfg_file = self.task_content['vmware_config']
        vmname = ext_config['vmname']
        vm_datastore = ext_config['vm_datastore']
        with open(vm_cfg_file, 'r') as fout:
            vm_cfg = json.loads(fout.read())
        vm_cfg['hardware']['numCPU'] = int(ext_config['numCPU'])
        vm_cfg['hardware']['numCoresPerSocket'] = int(ext_config['numCoresPerSocket'])
        vm_cfg['hardware']['memoryMB'] = int(ext_config['memoryMB'])
        vm_cfg['files']['logDirectory'] = '[{vm_datastore}] {vmname}'.format(vm_datastore=vm_datastore, vmname=vmname)
        vm_cfg['files']['vmPathName'] = vm_cfg['files']['logDirectory']
        vm_cfg['files']['snapshotDirectory'] = vm_cfg['files']['logDirectory']
        vm_cfg['files']['suspendDirectory'] = vm_cfg['files']['logDirectory']

        for device in vm_cfg['hardware']['device']:
            if device['_wsdlName'] in ('VirtualParallelPort', 'VirtualSerialPort',):
                # TODO，可能需要用户来填写
                fileName = device['backing'].get('fileName')
                if fileName:
                    device['backing']['fileName'] = '[{vm_datastore}] {vmname}/{tmp_name}.txt'.format(
                        vm_datastore=vm_datastore, vmname=vmname, tmp_name=time.time())
        return vm_cfg

    def _duplicate_virtual_machine(self, ext_config, vm_cfg):
        host = self.task_content['host']
        user = self.task_content['user']
        password = self.task_content['password']
        port = self.task_content['port']
        disable_ssl_verification = self.task_content['disable_ssl']
        api = self._get_api_instance(host, user, password, port)
        vm_connect_args = {
            'host': host,
            'port': port,
            'user': user,
            'password': password,
            'disable_ssl_verification': disable_ssl_verification
        }
        vm_cfg['name'] = ext_config['vmname']
        vm = api.create_virtual_machine(vm_connect_args, vm_cfg, ext_config['vm_datastore'])
        return vm


class VMRMountNbd(task.Task, WorkerLog):
    default_provides = 'task_content'

    def __init__(self, task_id, inject=None):
        super(VMRMountNbd, self).__init__('VMRMountNbd_{}'.format(task_id), inject=inject)
        self.task = VirtualMachineRestoreTask.objects.get(id=task_id)

    def execute(self, task_content, **kwargs):
        self.task_content = task_content
        if VMRFlowEntrance.has_error(task_content):
            return task_content
        self.task.set_status(VirtualMachineRestoreTask.MOUNT_NBD)
        try:
            raise_exception_when_cancel(self.task)
            for snapshot_info in self.task_content['restore_snapshots']:
                optimize_parameter = {
                    'hash_files': None,
                    'snapshots': snapshot_info['snapshots']
                }
                result_mount_snapshot = boxService.box_service.startBackupOptimize(optimize_parameter)
                snapshot_info['result_mount_snapshot_list'] = result_mount_snapshot
        except Exception as e:
            self.log_error(e)
            task_content['error'] = '{}'.format(e)
        return task_content


class ProcessHandle(object):
    def __init__(self, process, is_exit):
        self._p = process
        self._is_exit = is_exit

    def join(self):
        while True:
            if self._p.poll() is None:
                if self._is_exit():
                    raise Exception('catch exit flag')
                else:
                    pass
            else:
                if self._p.returncode == 0:
                    break
                else:
                    stdout, stderr = self._p.communicate()
                    raise Exception('process error:{}|{}|{}'.format(self._p.returncode, stdout, stderr))
            time.sleep(4)

    @xlogging.convert_exception_to_value(None)
    def stop(self):
        while self._p.poll() is None:
            _logger.info('ProcessHandle waite process:{} quit'.format(self._p.pid))
            os.kill(self._p.pid, signal.SIGINT)
            time.sleep(4)


class UpdateProcessThreading(threading.Thread):
    def __init__(self, files, task_id):
        super(UpdateProcessThreading, self).__init__(name='UpdateProcessThreading_{}'.format(task_id))
        self._exit = False
        self._files = files
        self._task_id = task_id

    def run(self):
        while not self._exit:
            try:
                total_bytes, restored_bytes = 0, 0
                for file in self._files:
                    successful, total, restored = self._get_data(file)
                    if successful:
                        total_bytes += total
                        restored_bytes += restored
                    else:
                        pass

                self._update(total_bytes, restored_bytes)
            except Exception as e:
                _logger.error('UpdateProcessThreading error:{}'.format(e), exc_info=True)
            time.sleep(5)

    def _get_data(self, file):
        try:
            with open(file) as f:
                c = json.load(f)
            if c['status'] == 'WriteDisk':
                return True, int(c['numberOfBytes']), int(c['byteOffset'])
            else:
                return False, -1, -1
        except Exception as e:
            _logger.error('_get_data error:{}'.format(e), exc_info=True)
            return False, -1, -1

    def _update(self, total_bytes, restored_bytes):
        task = VirtualMachineRestoreTask.objects.get(id=self._task_id)
        ext_config = json.loads(task.ext_config)
        need_update = False
        if total_bytes != ext_config.get('total_bytes', 0):
            ext_config['total_bytes'] = total_bytes
            need_update = True
        if restored_bytes != ext_config.get('restored_bytes', 0):
            ext_config['restored_bytes'] = restored_bytes
            need_update = True
        if need_update:
            task.ext_config = json.dumps(ext_config)
            task.save(update_fields=['ext_config'])
        else:
            pass

    def stop(self):
        self._exit = True


class VMRTransferData(task.Task, WorkerLog):
    default_provides = 'task_content'

    def __init__(self, task_id, inject=None):
        super(VMRTransferData, self).__init__('VMRTransferData_{}'.format(task_id), inject=inject)
        self.task = VirtualMachineRestoreTask.objects.get(id=task_id)
        self.task_content = None

        self._nbd_path = None
        self._lib_path = None
        self._lib_path_env = None

    def execute(self, task_content, **kwargs):
        if VMRFlowEntrance.has_error(task_content):
            return task_content
        self.task_content = task_content
        self.task.set_status(VirtualMachineRestoreTask.TRANSFER_DATA)
        worker_processes = list()
        update_thread = None

        # 检查退出信号
        is_exit = partial(is_cancel, self.task)

        try:
            raise_exception_when_cancel(self.task)
            files = list()
            disk_path_list = list()
            for restore_snapshot in task_content['restore_snapshots']:
                cmd, disk_path = self._generate_cmd(restore_snapshot, disk_path_list)
                disk_path_list.append(disk_path)
                process = self._execute_vm_nbd(cmd, is_waite=False)
                worker_processes.append(ProcessHandle(process, is_exit))
                files.append(restore_snapshot['output_file'])

            update_thread = UpdateProcessThreading(files, self.task.id)
            update_thread.setDaemon(True)
            update_thread.start()

            for worker_process in worker_processes:
                worker_process.join()

        except Exception as e:
            self.log_error(e)
            task_content['error'] = '{}'.format(e)
        finally:
            self.end_process(worker_processes)
            if update_thread:
                update_thread.stop()
        return task_content

    def end_process(self, processes):
        for process in processes:
            process.stop()

    def _generate_cmd(self, restore_snapshot, disk_path_list):
        api = get_api_instance(self.task_content['host'],
                               self.task_content['user'],
                               self.task_content['password'],
                               self.task_content['port']
                               )
        if not api:
            raise Exception('get api fail')
        if self._nbd_path is None:
            self._nbd_path, self._lib_path, self._lib_path_env = load_vmnbd_params(api.version, _logger)
        vm = api.get_vm_by_name(self.task_content['vm_name'])
        if not vm:
            raise Exception('get vm fail')
        src_vmware_config = self.task_content['src_vmware_config']
        disk_path = api.get_disk_path_by_key(vm, restore_snapshot['disk_key'], src_vmware_config, disk_path_list)
        cmd = list()
        cmd.extend(['-type', 'writedisk'])
        cmd.extend(['-host', self.task_content['host']])
        cmd.extend(['-port', str(self.task_content['port'])])
        cmd.extend(['-user', self.task_content['user']])
        cmd.extend(['-password', self.task_content['password']])
        cmd.extend(['-thumb', self.task_content['thumb']])
        cmd.extend(['-disk_bytes', str(restore_snapshot['disk_bytes'])])
        if api.isESXi:
            cmd.extend(['-PrepareForAccess', '0'])
        cmd.extend(['-vm', 'moref={}'.format(vm._moId)])
        cmd.extend(['-meta_filepath', restore_snapshot['meta_filepath']])
        cmd.extend(['-bitmapfile', restore_snapshot['bitmapfile']])
        cmd.extend(['-dev_name', json.loads(restore_snapshot['result_mount_snapshot_list'])['nbd_device_path']])
        cmd.extend(['-disk_path', disk_path])
        cmd.extend(['-output', restore_snapshot['output_file']])
        cmd.extend(['-libdir', self._lib_path])
        return cmd, disk_path

    def _execute_vm_nbd(self, cmd, is_waite=True):
        _cmd = [self._nbd_path]
        _cmd.extend(cmd)
        return execute_cmd(_cmd, is_waite=is_waite, cwd=self._lib_path_env, env={'LD_LIBRARY_PATH': self._lib_path_env})


class VMRFinisTask(task.Task, WorkerLog):
    def __init__(self, task_id, inject=None):
        super(VMRFinisTask, self).__init__('VMRFinisTask_{}'.format(task_id), inject=inject)
        self.task = VirtualMachineRestoreTask.objects.get(id=task_id)

    def execute(self, task_content, **kwargs):
        self.task_content = task_content
        try:
            self._unmount_nbd()
            self._unlock_snapshots()
            self._del_task_dir()
            self._consolidate_vmdisks()
        except Exception as e:
            self.log_error(e)
            self.task_content['error'] = '{}'.format(e)

        if VMRFlowEntrance.has_error(self.task_content):
            self.task.set_status(VirtualMachineRestoreTask.MISSION_FAIL, self.task_content['error'])
            successful = False
        else:
            self.task.set_status(VirtualMachineRestoreTask.MISSION_SUCCESSFUL)
            successful = True

        self.task.finish_datetime = timezone.now()
        self.task.successful = successful
        self.task.running_task = '{}'
        self.task.save(update_fields=['finish_datetime', 'successful', 'running_task'])

        return None

    @xlogging.convert_exception_to_value(None)
    def _unmount_nbd(self):
        for restore_snapshot in self.task_content['restore_snapshots']:
            result_mount_snapshot_list = restore_snapshot.get('result_mount_snapshot_list', None)
            if result_mount_snapshot_list:
                boxService.box_service.stopBackupOptimize('[{}]'.format(result_mount_snapshot_list))
            else:
                pass

    @xlogging.convert_exception_to_value(None)
    def _unlock_snapshots(self):
        Snapshot = namedtuple('Snapshot', ['path', 'snapshot'])
        for restore_snapshot in self.task_content['restore_snapshots']:
            snapshot_objs = [Snapshot(path=snapshot['path'], snapshot=snapshot['ident']) for snapshot in
                             restore_snapshot['snapshots']]
            SendTaskWork.unlock_snapshots_u(snapshot_objs, self.task_content['lock_name'])

    @xlogging.convert_exception_to_value(None)
    def _del_task_dir(self):
        shutil.rmtree(self.task_content['task_dir'])

    @xlogging.convert_exception_to_value(None)
    def _consolidate_vmdisks(self):
        api = get_api_instance(self.task_content['host'],
                               self.task_content['user'],
                               self.task_content['password'],
                               self.task_content['port']
                               )
        if not api:
            return
        vm = api.get_vm_by_name(self.task_content['vm_name'])
        if not vm:
            return
        vm.ConsolidateVMDisks_Task()


class VMRFlowEntrance(threading.Thread):
    def __init__(self, task_id):
        super(VMRFlowEntrance, self).__init__()
        self.name = r'VMRFlowEntrance_{}'.format(task_id)
        self._engine = None
        self._book_uuid = None
        self.task_id = task_id

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
            self._engine = engines.load_from_factory(create_flow, backend=backend, book=book, engine='serial',
                                                     factory_args=(self.name, self.task_id)
                                                     )

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
        try:
            with logging_listener.DynamicLoggingListener(self._engine):
                self._engine.run()
        except Exception as e:
            _logger.error(r'VMRFlowEntrance run engine {} failed {}'.format(self.name, e), exc_info=True)
        finally:
            with contextlib.closing(task_backend.get_backend().get_connection()) as conn:
                conn.destroy_logbook(self._book_uuid)
        self._engine = None

    @staticmethod
    def has_error(task_content):
        return task_content['error']


def create_flow(name, task_id):
    flow = lf.Flow(name).add(
        VMRInit(task_id),
        VMRCreateVM(task_id),
        VMRMountNbd(task_id),
        VMRTransferData(task_id),
        VMRFinisTask(task_id),
    )
    return flow
