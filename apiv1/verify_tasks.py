# coding=utf-8
import contextlib
import datetime
import Ice
import os
import json
import time
import CustomizedOS
import sys
import random
import threading
import copy
import django.utils.timezone as timezone
from rest_framework import status
from taskflow import engines
from taskflow import task
from taskflow.listeners import logging as logging_listener
from taskflow.patterns import linear_flow as lf
from taskflow.persistence import models
from box_dashboard import xlogging, task_backend, xdatetime, xdata
from apiv1.models import Host, AutoVerifyTask, TakeOverKVM
from xdashboard.handle.takeover import get_takeover_hardware_recommend, takeover_close_kvm, takeover_kvm_is_run
from apiv1.takeover_logic import TakeOverKVMCreate, TakeOverKVMExecute
from apiv1.views import get_response_error_string
from xdashboard.common.dict import GetDictionary
from xdashboard.models import DataDictionary
from apiv1.models import HostSnapshot
from apiv1.models import HostLog
from xdashboard.models import auto_verify_script

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


class CreateVerifyKvm(task.Task, WorkerLog):
    default_provides = 'task_content'

    def __init__(self, task_id, inject=None):
        super(CreateVerifyKvm, self).__init__('CreateVerifyKvm_{}'.format(task_id), inject=inject)
        self.task_id = task_id
        self.task_content = None
        self.task = None

    def _get_mac_address(self, bond_ip):
        split_ip = bond_ip.strip().split('.')
        if len(split_ip) != 4:
            raise Exception("split ip address:{} error:{}".format(bond_ip, split_ip))

        _mac_address = r'cc-cc-{:02x}-{:02x}-{:02x}-{:02x}'.format(
            int(split_ip[0]), int(split_ip[1]), int(split_ip[2]), int(split_ip[3]))

        return _mac_address

    def _get_kvm_private_ip(self):
        kvm_private_ip = list()
        TakeOverKVMobj = TakeOverKVM.objects.all()
        for kvm in TakeOverKVMobj:
            ext_info = json.loads(kvm.ext_info)
            kvm_adpter = ext_info['kvm_adpter']
            for adpter in kvm_adpter:
                if adpter['name'] in ('private_aio', 'my_private_aio'):
                    if len(adpter['ips']):
                        for ip in adpter['ips']:
                            kvm_private_ip.append(ip)
        return kvm_private_ip

    def _save_kvm_run_info(self, start_kvm_flag_file, kvm_key, kvm_value):
        flag_file = start_kvm_flag_file
        try:
            with open(flag_file, 'w') as fout:
                info = dict()
                info[kvm_key] = kvm_value
                fout.seek(0)
                fout.truncate()
                info_str = json.dumps(info, ensure_ascii=False)
                fout.write(info_str)
        except Exception as e:
            _logger.info('_save_kvm_run_info r Failed.e={}'.format(e))

    def execute(self, **kwargs):
        self.task_content = {
            'error': '',
            'stime': timezone.now().strftime(xdatetime.FORMAT_WITH_USER_SECOND),
        }
        try:
            self.log_info('CreateVerifyKvm execute task_id={}'.format(self.task_id))
            auto_verify_task_obj = AutoVerifyTask.objects.get(id=self.task_id)
            pointid = auto_verify_task_obj.point_id
            _hardware_recommend = get_takeover_hardware_recommend(pointid, True)
            self.task_content['logic'] = _hardware_recommend['logic']
            params = pointid.split('|')
            if params[0] == xdata.SNAPSHOT_TYPE_NORMAL:
                snapshot_time = params[2]
            else:
                # CDP时间
                snapshot_time = params[3]
            kvm_cpu_sockets = 1
            kvm_cpu_cores = 2

            private_ip = list()
            kvm_private_ip = self._get_kvm_private_ip()
            for ip in kvm_private_ip:
                if 'ip' in ip:
                    private_ip.append(ip['ip'])

            kvm_ip = None
            kvm_mac = None
            for i in range(1, 255):
                ip = '{seg}.130.{i}'.format(
                    seg=GetDictionary(DataDictionary.DICT_TYPE_TAKEOVER_SEGMENT, 'SEGMENT', '172.29'), i=i)
                if ip not in private_ip:
                    kvm_mac = self._get_mac_address(ip)
                    kvm_ip = ip
                    break
            assert kvm_ip
            assert kvm_mac

            schedule_ext_config_obj = json.loads(auto_verify_task_obj.schedule_ext_config)

            api_request = {"name": 'auto_verify_{}'.format(self.task_id),
                           "pointid": pointid,
                           "snapshot_time": snapshot_time,
                           "kvm_cpu_count": int('{}{}'.format(kvm_cpu_sockets, kvm_cpu_cores)),
                           "kvm_memory_size": schedule_ext_config_obj.get('kvm_memory_size', 1024),
                           "kvm_memory_unit": schedule_ext_config_obj.get('kvm_memory_unit', 'MB').upper(),
                           "kvm_storagedevice": auto_verify_task_obj.storage_node_ident,
                           "kvm_adpter": [
                               {"name": "private_aio", "nic_name": None, "mac": kvm_mac, "ips": []}],
                           "kvm_route": [],
                           "kvm_gateway": [""],
                           "kvm_dns": [],
                           "kvm_type": 'verify_kvm',
                           'kvm_pwd': ''.join(random.sample('0123456789', 6)),
                           'hddctl': _hardware_recommend['hddctl'],
                           'vga': _hardware_recommend['vga'],
                           'net': _hardware_recommend['net'],
                           'cpu': 'core2duo',
                           'boot_firmware': None,
                           }
            api_response = TakeOverKVMCreate().post(request=None, api_request=api_request)
            if not status.is_success(api_response.status_code):
                self.task_content['error'] = get_response_error_string(api_response)
                self.log_error('CreateVerifyKvm TakeOverKVMCreate Failed.e={}'.format(self.task_content['error']))
            takeover_id = int(api_response.data['id'])
            schedule_ext_config_obj['takeover_id'] = takeover_id
            schedule_ext_config_obj['stime'] = time.time()
            AutoVerifyTask.objects.filter(id=self.task_id).update(
                schedule_ext_config=json.dumps(schedule_ext_config_obj, ensure_ascii=False))
            self.task_content['takeover_id'] = takeover_id
            api_request = {"id": takeover_id, "debug": 0}
            takeover_kvm_obj = TakeOverKVM.objects.get(id=takeover_id)
            flag_file = takeover_kvm_obj.kvm_flag_file
            self._save_kvm_run_info(flag_file, 'msg', '已发送开机命令')
            api_response = TakeOverKVMExecute().post(request=None, api_request=api_request)
            if not status.is_success(api_response.status_code):
                self.task_content['error'] = get_response_error_string(api_response)
                self.log_error('CreateVerifyKvm TakeOverKVMCreate Failed.e={}'.format(self.task_content['error']))
            self.task_content['ip'] = kvm_ip
            self.log_info('CreateVerifyKvm kvm_ip={}'.format(kvm_ip))
        except Exception as e:
            self.task_content['error'] = e
            self.log_error('CreateVerifyKvm execute failed.e={}'.format(e))
        return self.task_content


class CallbackSenderI(CustomizedOS.CallbackSender):
    def __init__(self, communicator):
        self._communicator = communicator
        self._client = None

    def addClient(self, ident, current=None):
        _logger.info("adding client `" + self._communicator.identityToString(ident) + "'")
        client = CustomizedOS.MiniLoaderPrx.uncheckedCast(current.con.createProxy(ident))
        self._client = client
        assert self._client

    def getClient(self):
        return self._client


class ClwMiniLoaderProxy(object):
    def __init__(self):
        self._communicator = None

    def get_mini_loader_ptr(self, wait_timeout=30 * 60):
        init_data = Ice.InitializationData()
        init_data.properties = Ice.createProperties()
        init_data.properties.setProperty(r'Ice.LogFile', r'clw_MiniLoader_ice.log')
        init_data.properties.setProperty(r'Ice.ThreadPool.Server.Size', r'1')
        init_data.properties.setProperty(r'Ice.ThreadPool.Server.SizeMax', r'8')
        init_data.properties.setProperty(r'Ice.ThreadPool.Server.StackSize', r'8388608')
        init_data.properties.setProperty(r'Ice.ThreadPool.Client.Size', r'1')
        init_data.properties.setProperty(r'Ice.ThreadPool.Client.SizeMax', r'8')
        init_data.properties.setProperty(r'Ice.ThreadPool.Client.StackSize', r'8388608')
        init_data.properties.setProperty(r'Ice.Default.Host', r'localhost')
        init_data.properties.setProperty(r'Ice.Warn.Connections', r'1')
        init_data.properties.setProperty(r'Ice.ACM.Heartbeat', r'3')
        init_data.properties.setProperty(r'Ice.ThreadPool.Client.ThreadIdleTime', r'0')
        init_data.properties.setProperty(r'Ice.ThreadPool.Server.ThreadIdleTime', r'0')
        init_data.properties.setProperty(r'Callback.Server.Endpoints', r'tcp -h 0.0.0.0 -p 10000')
        init_data.properties.setProperty(r'Ice.MessageSizeMax', r'131072')  # 单位KB, 128MB
        self._communicator = Ice.initialize(sys.argv, init_data)
        adapter = self._communicator.createObjectAdapter(r'Callback.Server')
        sender = CallbackSenderI(self._communicator)
        adapter.add(sender, self._communicator.stringToIdentity(r"sender"))
        adapter.activate()
        st1 = datetime.datetime.now()
        while True:
            client = sender.getClient()
            if client:
                _logger.info('get_mini_loader_ptr OK.')
                return client
                break
            st2 = datetime.datetime.now()
            if (st2 - st1).seconds > wait_timeout:
                _logger.info('get_mini_loader_ptr getClient Failed. timeout.')
                self.destroy()
                break
            time.sleep(1)
        return None

    def destroy(self):
        if self._communicator:
            self._communicator.destroy()


class RunVerifyCmd(task.Task, WorkerLog):
    default_provides = 'task_content'

    def __init__(self, task_id, inject=None):
        super(RunVerifyCmd, self).__init__('RunVerifyCmd_{}'.format(task_id), inject=inject)
        self.task_id = task_id
        self.task_content = None
        self.task = None
        self._loader_prx = None
        # 客户端所在的目录
        self._run_path = None
        self._clw_mini_loader_proxy = ClwMiniLoaderProxy()

    def _get_loader_prx(self, ip=None):
        if self._loader_prx is None and ip:
            self._loader_prx = self._clw_mini_loader_proxy.get_mini_loader_ptr()
        if self._loader_prx is None:
            self.log_error('_get_loader_prx run kvm timeout')
            self.task_content['error'] = 'run kvm timeout'

        return self._loader_prx

    def _get_run_path(self):
        rc = json.loads(self._get_loader_prx().getRunPath())
        self._run_path = rc['run_path']
        self.log_info('_get_run_path _run_path={}'.format(self._run_path))

    @xlogging.convert_exception_to_value(None)
    def _run_on_remote(self, cmd, work_dir=None, timeouts=None):
        rc = json.loads(self._get_loader_prx().popen(json.dumps({
            'async': False, 'shell': True, 'cmd': cmd, 'work_dir': work_dir, 'timeouts_seconds': timeouts
        })))
        return rc

    def _rw_file_in_kvm(self, inputJson, inputBs=None):
        r, b = self._get_loader_prx().rwFile(json.dumps(inputJson), inputBs)
        return json.loads(r), b

    def _connect(self, ip):
        self._get_loader_prx(ip)
        self._get_run_path()

    @xlogging.convert_exception_to_value(False)
    def _shutdown(self):
        if self.task_content['logic'] == 'windows':
            cmd = 'shutdown /s /t 5'
        else:
            # shutdown之前的命令是用来清除缓存的
            cmd = 'init 0'
        self._get_loader_prx().popen(json.dumps({
            'async': False, 'shell': True, 'cmd': cmd, 'work_dir': None, 'timeouts_seconds': None
        }))

    def _verify(self, host_id, schedule_ext_config_obj, snapshot_systeminfo, kvm_systeminfo, logic):
        from apiv1.views import HostSessionDisks
        from xdashboard.handle.restore import is_host_cdp_plan_existed
        result = dict()
        result['result'] = 'pass'
        result['verify_os'] = {'result': 'pass'}
        result['verify_network'] = {'result': 'pass'}
        if kvm_systeminfo is None:
            result['verify_os']['result'] = 'not pass'
            result['verify_network']['result'] = 'not pass'
            result['result'] = 'not pass'
            return result
        if schedule_ext_config_obj['verify_osname']:
            snapshot_osname = snapshot_systeminfo['System']['ComputerName']
            kvm_osname = kvm_systeminfo['System']['ComputerName']
            result['verify_osname'] = {'snapshot_osname': snapshot_osname, 'kvm_osname': kvm_osname}
            if snapshot_osname != kvm_osname:
                result['result'] = 'not pass'
                result['verify_osname']['result'] = 'not pass'
            else:
                result['verify_osname']['result'] = 'pass'

        if schedule_ext_config_obj['verify_osver']:
            snapshot_version = snapshot_systeminfo['System']['SystemCatName']
            kvm_version = kvm_systeminfo['System']['SystemCatName']
            result['verify_osver'] = {'snapshot_version': snapshot_version, 'kvm_version': kvm_version}
            if snapshot_version != kvm_version:
                result['result'] = 'not pass'
                result['verify_osver']['result'] = 'not pass'
            else:
                result['verify_osver']['result'] = 'pass'

        if schedule_ext_config_obj['verify_hdd']:
            snapshot_disks = snapshot_systeminfo['Disk']
            kvm_disks = kvm_systeminfo['Disk']

            snapshot_disk_partition_count = 0
            snapshot_disk_used = list()
            existed_cdp_plan = is_host_cdp_plan_existed(host_id)
            if existed_cdp_plan or logic == 'linux':
                calc_used = False
            else:
                calc_used = True
            for snapshot_disk in snapshot_disks:
                snapshot_disk_partition_count = snapshot_disk_partition_count + len(snapshot_disk['Partition'])
                snapshot_disk_used.append(HostSessionDisks.disk_label_for_human(snapshot_disk, calc_used))

            kvm_disk_partition_count = 0
            kvm_disk_used = list()
            for kvm_disk in kvm_disks:
                kvm_disk_partition_count = kvm_disk_partition_count + len(kvm_disk['Partition'])
                kvm_disk_used.append(HostSessionDisks.disk_label_for_human(kvm_disk, calc_used))

            result['verify_hdd'] = dict()

            result['verify_hdd']['snapshot_disk_partition_count'] = snapshot_disk_partition_count
            result['verify_hdd']['kvm_disk_partition_count'] = kvm_disk_partition_count

            result['verify_hdd']['snapshot_disk_used'] = snapshot_disk_used
            result['verify_hdd']['kvm_disk_used'] = kvm_disk_used

            if snapshot_disk_partition_count != kvm_disk_partition_count:
                result['verify_hdd']['result'] = 'not pass'
            else:
                result['verify_hdd']['result'] = 'pass'
        return result

    def _save_host_log(self, auto_verify_task_name, pointid, verify_result):
        reason = dict()
        reason['description'] = '任务名：{}'.format(auto_verify_task_name)
        reason['description'] = '{}<br>时间：{} - {}'.format(reason['description'], verify_result.get('stime', ''),
                                                          verify_result.get('endtime', ''))
        if verify_result['result'] == 'pass':
            log_type = HostLog.LOG_AUTO_VERIFY_TASK_SUCCESSFUL
        else:
            log_type = HostLog.LOG_AUTO_VERIFY_TASK_FAILED
        verify_os = verify_result.get('verify_os')
        verify_osname = verify_result.get('verify_osname')
        verify_osver = verify_result.get('verify_osver')
        verify_hdd = verify_result.get('verify_hdd')
        verify_result_list = list()
        if verify_os:
            if verify_os['result'] == 'pass':
                verify_result_list.append('操作系统能否启动（通过）')
                verify_result_list.append('网络状态(通过)')
            else:
                verify_result_list.append('操作系统能否启动（失败）')
                verify_result_list.append('网络状态(失败)')

        if verify_osname:
            if verify_osname['result'] == 'pass':
                verify_result_list.append('客户端名称（通过）')
            else:
                verify_result_list.append(
                    '客户端名称（{} -> {}）'.format(verify_osname['snapshot_osname'], verify_osname['kvm_osname']))

        if verify_osver:
            if verify_osver['result'] == 'pass':
                verify_result_list.append('操作系统版本（通过）')
            else:
                verify_result_list.append(
                    '操作系统版本（{} -> {}）'.format(verify_osver['snapshot_version'], verify_osver['kvm_version']))

        if verify_hdd:
            if verify_hdd['result'] == 'pass':
                verify_result_list.append('硬盘分区结构（通过）')
            else:
                verify_result_list.append('硬盘分区数量（{} -> {}）'.format(verify_hdd['snapshot_disk_partition_count'],
                                                                    verify_hdd['kvm_disk_partition_count']))
        if verify_hdd:
            snapshot_disk_used_str = '<br>'.join(verify_hdd['snapshot_disk_used'])
            kvm_disk_used_str = '<br>'.join(verify_hdd['kvm_disk_used'])
            verify_result_list.append('备份前硬盘使用情况：<br>{}'.format(snapshot_disk_used_str))
            verify_result_list.append('备份点硬盘使用情况：<br>{}'.format(kvm_disk_used_str))
        if verify_result_list:
            reason['description'] = '{}<br>{}'.format(reason['description'], '<br>'.join(verify_result_list))
        point_params = pointid.split('|')
        host_snapshot_id = point_params[1]
        host_snapshot = HostSnapshot.objects.get(id=host_snapshot_id)
        host_id = host_snapshot.host.id
        reason['host_id'] = host_id
        reason['pointid'] = pointid
        HostLog.objects.create(host_id=host_id, type=log_type,
                               reason=json.dumps(reason, ensure_ascii=False))

    def _put_user_script_proxy(self):
        with open('/sbin/aio/box_dashboard/verify_task/user_script_proxy.py', 'rb') as f:
            inputBs = bytearray(f.read())
        inputJson = {
            "type": "write_new",
            "path": os.path.join(self._run_path, 'user_script_proxy.py')
        }
        self._rw_file_in_kvm(inputJson, inputBs)

    def _user_verify_script(self, schedule_ext_config_obj):
        script_result_list = list()
        self._put_user_script_proxy()
        script_list = schedule_ext_config_obj.get('script_list', [])
        for id in script_list:
            if id is None:
                continue
            user_script = auto_verify_script.objects.filter(id=id)
            if user_script:
                user_script = user_script.first()
                with open(user_script.path, 'rb') as f:
                    inputBs = bytearray(f.read())
                script_name = 'user_script{}.zip'.format(user_script.id)
                inputJson = {
                    "type": "write_new",
                    "path": os.path.join(self._run_path, script_name)
                }
                self._rw_file_in_kvm(inputJson, inputBs)

                cmd = '"{}/python/python.exe" user_script_proxy.py {}'.format(self._run_path, script_name)

                rc = self._run_on_remote(cmd, self._run_path)

                self.log_info('_user_verify_script script_name={},rc={}'.format(user_script.name, rc))
                script_result_list.append({'id': user_script.id, 'script_name': user_script.name, 'rc': rc})
        return script_result_list

    def execute(self, task_content, **kwargs):
        self.task_content = task_content
        try:
            if VerifyFlowEntrance.has_error(task_content):
                return task_content
            client_ip = copy.deepcopy(task_content['ip'])
            self.log_info('RunVerifyCmd _connect ip={}'.format(client_ip))
            self._connect(client_ip)
            self.log_info('RunVerifyCmd _connect end ip={}'.format(client_ip))
            auto_verify_task_obj = AutoVerifyTask.objects.get(id=self.task_id)
            schedule_ext_config_obj = json.loads(auto_verify_task_obj.schedule_ext_config)

            pointid = auto_verify_task_obj.point_id
            _hardware_recommend = get_takeover_hardware_recommend(pointid, True)
            host_snapshot_ext_info = _hardware_recommend['host_snapshot_ext_info']

            snapshot_systeminfo = json.loads(host_snapshot_ext_info)['system_infos']

            with open('/sbin/aio/box_dashboard/verify_task/getsysteminfo.py', 'rb') as f:
                inputBs = bytearray(f.read())
            inputJson = {
                "type": "write_new",
                "path": os.path.join(self._run_path, 'getsysteminfo.py')
            }
            self._rw_file_in_kvm(inputJson, inputBs)

            rc = self._run_on_remote('"{}/python/python.exe" getsysteminfo.py'.format(self._run_path), self._run_path)
            # 取回systeminfo文件
            self.log_info('diskinfo.py rc={}'.format(rc))
            try:
                kvm_systeminfo = json.loads(rc['stdout'])['systeminfo']
            except Exception as e:
                self.log_error('RunVerifyCmd kvm_systeminfo Failed.e={}'.format(e))
                kvm_systeminfo = None

            user_script_result = self._user_verify_script(schedule_ext_config_obj)
            self._shutdown()
            verify_result = self._verify(_hardware_recommend['host_id'], schedule_ext_config_obj, snapshot_systeminfo,
                                         kvm_systeminfo, _hardware_recommend['logic'])
            verify_result['user_script_result'] = user_script_result
            verify_result['stime'] = self.task_content['stime']
            verify_result['endtime'] = timezone.now().strftime(xdatetime.FORMAT_WITH_USER_SECOND)
            if user_script_result:
                for user_result in user_script_result:
                    try:
                        script_rc = json.loads(user_result['rc']['stdout'])
                        if script_rc['r'] != 0:
                            verify_result['result'] = 'not pass'
                    except Exception as e:
                        verify_result['result'] = 'not pass'
            AutoVerifyTask.objects.filter(id=self.task_id).update(
                verify_result=json.dumps(verify_result, ensure_ascii=False))

            self._save_host_log(auto_verify_task_obj.schedule_name, pointid, verify_result)
        except Exception as e:
            self.log_error('RunVerifyCmd Failed.e={}'.format(e))
        finally:
            self._clw_mini_loader_proxy.destroy()

        self.log_info('RunVerifyCmd task_content={}'.format(self.task_content))

        return self.task_content


class VerifyEnd(task.Task, WorkerLog):
    default_provides = 'task_content'

    def __init__(self, task_id, inject=None):
        super(VerifyEnd, self).__init__('VerifyEnd_{}'.format(task_id), inject=inject)
        self.task_id = task_id
        self.task = None
        self.task_content = None

    def execute(self, task_content, **kwargs):
        self.task_content = task_content
        try:
            qcow2path_path_list = list()
            TakeOverKVMobj = TakeOverKVM.objects.get(id=self.task_content['takeover_id'])
            ext_info = json.loads(TakeOverKVMobj.ext_info)
            for boot_device in ext_info['disk_snapshots']['boot_devices']:
                qcow2path_path_list.append(boot_device['device_profile']['qcow2path'])

            st1 = datetime.datetime.now()
            while True:
                if not takeover_kvm_is_run(qcow2path_path_list):
                    break
                time.sleep(1)
                st2 = datetime.datetime.now()
                if (st2 - st1).seconds > 60:
                    break

            # 强制关闭kvm并删除
            takeover_close_kvm(self.task_content['takeover_id'])
            api_request = {'id': self.task_content['takeover_id']}
            TakeOverKVMCreate().delete(request=None, api_request=api_request)
            AutoVerifyTask.objects.filter(id=self.task_id).update(verify_type=AutoVerifyTask.VERIFY_TYPE_END)

        except Exception as e:
            self.log_error(e)

        return self.task_content


_book_ids = list()
_book_id_locker = threading.Lock()


class VerifyFlowEntrance(threading.Thread):
    def __init__(self, task_id, name, flow_func):
        super(VerifyFlowEntrance, self).__init__()
        self.name = name
        self._engine = None
        self._book_uuid = None
        self.task_id = task_id
        self._flow_func = flow_func

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
                                                     factory_args=(self.name, self.task_id))

            self._book_uuid = book.uuid
            return {'book_id': book.uuid, 'flow_id': self._engine.storage.flow_uuid}
        except Exception as e:
            _logger.error(r'VerifyFlowEntrance generate_uuid failed {}'.format(e), exc_info=True)
            with contextlib.closing(backend.get_connection()) as conn:
                conn.destroy_logbook(book.uuid)
            raise e

    def start(self):
        if self._engine:
            super().start()
        else:
            xlogging.raise_and_logging_error('内部异常，无效的调用',
                                             r'VerifyFlowEntrance start without _engine ：{}'.format(self.name),
                                             status.HTTP_501_NOT_IMPLEMENTED)

    def run(self):
        with _book_id_locker:
            if self._book_uuid in _book_ids:
                # 重复运行
                _logger.warning('VerifyFlowEntrance book uuid:{} already run'.format(self._book_uuid))
                return
            else:
                _book_ids.append(self._book_uuid)
        _logger.info('VerifyFlowEntrance _book_ids:{}'.format(_book_ids))
        try:
            with logging_listener.DynamicLoggingListener(self._engine):
                self._engine.run()
        except Exception as e:
            _logger.error(r'VerifyFlowEntrance run engine {} failed {}'.format(self.name, e), exc_info=True)
        finally:
            with contextlib.closing(task_backend.get_backend().get_connection()) as conn:
                conn.destroy_logbook(self._book_uuid)
            with _book_id_locker:
                _book_ids.remove(self._book_uuid)
        self._engine = None

    @staticmethod
    def has_error(task_content):
        return task_content['error']


def create_verify_flow(name, task_id):
    flow = lf.Flow(name).add(
        CreateVerifyKvm(task_id),
        RunVerifyCmd(task_id),
        VerifyEnd(task_id),
    )
    return flow
