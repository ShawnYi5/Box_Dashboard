import contextlib
import datetime
import hashlib
import json
import os
import subprocess
import threading
import time
import traceback
import uuid

import psutil
from django.contrib.auth.models import User
from rest_framework import status
from taskflow import engines
from taskflow import task
from taskflow.listeners import logging as logging_listener
from taskflow.patterns import linear_flow as lf
from taskflow.persistence import models

from apiv1.models import TakeOverKVM, DiskSnapshot
from box_dashboard import task_backend, xdatetime
from box_dashboard import xlogging, boxService, xdata
from box_dashboard.boxService import box_service
from xdashboard.common.dict import GetDictionary
from xdashboard.models import DataDictionary

_logger = xlogging.getLogger(__name__)


class takeover_kvm_wrapper(object):
    def __init__(self, id, debug):
        self._id = id
        self._debug = debug

    def _need_fix_driver(self, disk_snapshots):
        devices = disk_snapshots['boot_devices']
        for device in devices:
            qcow2path = device['device_profile']['qcow2path']
            if len(qcow2path) > 32 and os.path.isfile(qcow2path):
                return False
        return True

    def _GetFileMd5(self, filename):
        if not os.path.isfile(filename):
            return 'none'
        myhash = hashlib.md5()
        with open(filename, 'rb') as fout:
            while True:
                b = fout.read(8096)
                if not b:
                    break
                myhash.update(b)
        return myhash.hexdigest()

    def _is_first_boot(self, disk_snapshots):
        bRet = False
        devices = disk_snapshots['boot_devices']
        for device in devices:
            qcow2path = device['device_profile']['qcow2path']
            filemd5path = qcow2path + '.md5'
            if len(qcow2path) > 32 and not os.path.isfile(qcow2path):
                return True
            elif len(filemd5path) > 32 and os.path.isfile(filemd5path):
                filesize = os.path.getsize(qcow2path)
                if filesize > 100 * 1024 * 1024:
                    return False
                with open(filemd5path, 'r') as fout:
                    filemd5 = fout.read()
                    filemd5 = filemd5.strip()
                    if self._GetFileMd5(qcow2path) == filemd5:
                        bRet = True
        return bRet

    @staticmethod
    def get_disk_ident_by_snapshot_ident(snapshot_ident):
        return DiskSnapshot.objects.get(ident=snapshot_ident).disk.ident

    def linux_disk_index_info(self, disk_index_info):
        disks_index_info = disk_index_info
        for index_info in disks_index_info:
            index_info['disk_ident'] = self.get_disk_ident_by_snapshot_ident(index_info['snapshot_disk_ident'])
        return disks_index_info

    def _get_agent_service_configure(self, ext_info):
        try:
            user = User.objects.get(username=ext_info["UserIdent"])
            user_id = user.id
            agent_user_info = '{}|*{}'.format(user_id, user.userprofile.user_fingerprint)
        except User.DoesNotExist:
            agent_user_info = '0|None'

        aio_ip = '{}{}'.format(GetDictionary(DataDictionary.DICT_TYPE_TAKEOVER_SEGMENT, 'SEGMENT', '172.29'), '.16.1')
        tunnel_ip = -1
        tunnel_port = -1
        routers = {"is_save": 0, "router_list": []}
        return {"user_info": agent_user_info, "aio_ip": aio_ip, "routers": routers, 'tunnel_ip': tunnel_ip,
                'tunnel_port': tunnel_port}

    def _get_restore_config(self, ext_info):
        restore_config = dict()

        # pci_bus_device_ids = self._pe_info['no_pci_devices']
        pci_bus_device_ids = list()

        if len(pci_bus_device_ids) > 0:
            restore_config['pci_bus_device_ids'] = ['not empty']
        else:
            restore_config['pci_bus_device_ids'] = list()

        restore_config['os_type'] = ext_info['SystemCatName']
        restore_config['os_bit'] = ext_info['ProcessorArch']

        choice_drivers_versions = dict()
        if isinstance(choice_drivers_versions, dict):
            choice_drivers_versions = json.dumps(choice_drivers_versions)
        restore_config['choice_drivers_versions'] = choice_drivers_versions

        agent_service_configure = self._get_agent_service_configure(ext_info)
        restore_config['agent_service_configure'] = json.dumps(agent_service_configure)

        return restore_config

    def _get_ipconfigs(self, kvm_adpter, kvm_dns, kvm_gateway):
        '''
        [{'hardwareConfig': '[{"LocationInformation": "@system32\\\\drivers\\\\pci.sys,#65536;PCI \\u603b\\u7ebf %1\\u3001\\u8bbe\\u5907 %2\\u3001\\u529f\\u80fd %3;(3,0,0)", "Address": 0, "ContainerID": "", "HardwareID": ["PCI\\\\VEN_15AD&DEV_07B0&SUBSYS_07B015AD&REV_01", "PCI\\\\VEN_15AD&DEV_07B0&SUBSYS_07B015AD", "PCI\\\\VEN_15AD&DEV_07B0&CC_020000", "PCI\\\\VEN_15AD&DEV_07B0&CC_0200"], "NameGUID": "{848F537B-D3A3-4FD7-9360-3F7098E5EDE5}", "UINumber": 160, "Service": "VMXNET3NDIS6", "Mac": "0050569508BB"}, {"LocationInformation": "@system32\\\\drivers\\\\pci.sys,#65536;PCI bus %1, device %2, function %3;(0,21,0)", "Address": 1376256, "ContainerID": "", "HardwareID": ["PCI\\\\VEN_15AD&DEV_07A0&SUBSYS_07A015AD&REV_01", "PCI\\\\VEN_15AD&DEV_07A0&SUBSYS_07A015AD", "PCI\\\\VEN_15AD&DEV_07A0&CC_060400", "PCI\\\\VEN_15AD&DEV_07A0&CC_0604"], "NameGUID": null, "UINumber": -1, "Service": "PCI", "Mac": "0050569508BB"}]', 'subnetMask': '255.255.0.0', 'nameServer': '172.16.1.1', 'multiInfos': '{"is_set": true, "is_to_self": false, "network_name": "", "ip_mask_pair": [{"Ip": "172.16.6.193", "Mask": "255.255.0.0"}, {"Ip": "172.16.6.194", "Mask": "255.255.0.0"}], "src_instance_id": "", "gate_way": "172.16.1.1", "dns_list": ["172.16.1.1", "172.16.1.2"], "target_nic": {"isConnected": true, "szDescription": "vmxnet3 Ethernet Adapter", "szNetType": "MIB_IF_TYPE_ETHERNET", "szDeviceInstanceID": "PCI\\\\VEN_15AD&DEV_07B0&SUBSYS_07B015AD&REV_01\\\\FF565000BB0895FE00", "szMacAddress": "0050569508BB", "szGuid": "{848F537B-D3A3-4FD7-9360-3F7098E5EDE5}"}}', 'gateway': '172.16.1.1', 'ipAddress': '172.16.6.193'}]
        '''
        isConnected = True
        gateway = ''
        for gw in kvm_gateway:
            gateway = gw
        ipconfigs = list()
        for adpter in kvm_adpter:
            ip_mask_pair = list()
            ips = adpter['ips']
            if len(ips) == 0:
                continue
            i = -1
            for ip in ips:
                i = i + 1
                if i == 0:
                    ipAddress = ip['ip']
                    subnetMask = ip['mask']
                ip_mask_pair.append({"Ip": ip['ip'], "Mask": ip['mask']})

            nameServer = ''
            for dns in kvm_dns:
                nameServer = dns

            nic_name = adpter.get('nic_name', None)
            mtu = int(GetDictionary(DataDictionary.DICT_TYPE_AIO_NETWORK, 'aio_mtu', -1))
            if adpter['name'] == 'my_private_aio':
                nic_name = 'ClerwareNetwork'

            ipconfig = {
                'ipAddress': ipAddress, 'subnetMask': subnetMask, "gateway": gateway, 'nameServer': nameServer,
                'hardwareConfig': json.dumps([{'Mac': xdata.standardize_mac_addr(adpter['mac'])}], ensure_ascii=False),
                'multiInfos': json.dumps(
                    {'ip_mask_pair': ip_mask_pair, "target_nic": {"isConnected": isConnected}, "dns_list": kvm_dns,
                     "gate_way": gateway, "name": nic_name, 'mtu': mtu}, ensure_ascii=False)
            }
            ipconfigs.append(ipconfig)
            isConnected = False
        return ipconfigs

    @staticmethod
    def is_aio_sys_vt_valid():
        returned_code, lines = boxService.box_service.runCmd(r'lsmod | grep kvm', True)
        if returned_code == 0:
            for line in lines:
                if 'kvm_intel' in line:
                    return True
            _logger.warning(r'is_aio_sys_vt_valid return not support VT')
            return False
        else:
            _logger.warning(r'is_aio_sys_vt_valid call lsmod failed : {}'.format(returned_code, lines))
            return False

    def _open_kvm_params(self, mdisk_snapshots, flag_file_path):
        """
        获取起kvm的参数
        :param host_snapshot: 主机快照
        :return:
        """
        params_file_name = '{}_takeover.json'.format(uuid.uuid4().hex)
        params = {
            "logic": 'linux',
            "system_type": 64,
            "vnc": None,
            "shutdown": False,
            'tmp_qcow': '{}_tmp.qcow2'.format(flag_file_path),
            "cmd_list": [
                {
                    "cmd": r"/home/python3.6/bin/python3.6 /home/patch/linux_iso/scripts/add_share_restore_takeover.py "
                           r"--kvmparams /home/{}".format(params_file_name),
                    "work_dir": '/home/patch/linux_iso',
                    "timeouts": None, "post_result_url": None,
                    "post_result_params": None}
            ],
            "write_new": {
                "src_path": "/dev/shm/{}".format(params_file_name),
                "dest_path": "/home/{}".format(params_file_name)
            }
        }
        disk_devices = list()
        for disk in mdisk_snapshots:
            if disk == 'boot_devices':
                disk_devices.append(
                    {"disk_ident": mdisk_snapshots[disk][0]['disk_ident'], "boot_device": True,
                     "device_profile": {"nbd": dict()}})
            else:
                for data_device in mdisk_snapshots[disk]:
                    disk_devices.append(
                        {"disk_ident": data_device['disk_ident'], "boot_device": False,
                         "device_profile": {"nbd": dict()}})
        params['disk_devices'] = disk_devices
        params['aio_server_ip'] = '{}'.format(
            '{}{}'.format(GetDictionary(DataDictionary.DICT_TYPE_TAKEOVER_SEGMENT, 'SEGMENT', '172.29'), '.16.2'))
        return params

    def run_kvm(self):
        TakeOverKVMobj = TakeOverKVM.objects.get(id=self._id)
        kvm_memory_size = TakeOverKVMobj.kvm_memory_size;
        if TakeOverKVMobj.kvm_memory_unit == 'GB':
            kvm_memory_size = kvm_memory_size * 1024
        ext_info = json.loads(TakeOverKVMobj.ext_info)
        kvm_type = TakeOverKVMobj.kvm_type
        disk_snapshots = ext_info['disk_snapshots']
        kvm_adpter = ext_info['kvm_adpter']

        if self.is_aio_sys_vt_valid():
            if ext_info['logic'] == 'linux':
                efibios = r'/usr/share/efibios/OVMF.original.fd'
                seabios = r'/usr/share/seabios/bios-256k.original.bin'
            else:
                efibios = r'/usr/share/efibios/OVMF.fd'
                seabios = None
        else:
            efibios = r'/usr/share/efibios/OVMF.fd'
            seabios = r'/sbin/aio/qemu-nokvm/bios-256k.bin'

        kvm_cpu_count = str(TakeOverKVMobj.kvm_cpu_count)
        if len(kvm_cpu_count) == 1:
            kvm_cpu_sockets = 1
            kvm_cpu_cores = int(kvm_cpu_count)
        else:
            kvm_cpu_sockets = int(kvm_cpu_count[0])
            kvm_cpu_cores = int(kvm_cpu_count[1])
        takeover_params = {
            'kvm_type': kvm_type,
            'disk_snapshots': disk_snapshots,
            'memory_size_MB': kvm_memory_size,
            'sockets': kvm_cpu_sockets,
            'cores': kvm_cpu_cores,
            'kvm_adpter': kvm_adpter,
            'logic': ext_info['logic'],
            'efibios': efibios,
            'seabios': seabios,
            'floppy_path': ext_info.get('floppy_path', None),
            'kvm_pwd': ext_info['kvm_pwd'],
            'debug': self._debug,
        }

        if kvm_type in ('temporary_kvm', 'verify_kvm',):
            devices = disk_snapshots['boot_devices']
            for device in devices:
                qcow2path = device['device_profile']['qcow2path']
                if len(qcow2path) > 32 and os.path.isfile(qcow2path):
                    os.remove(qcow2path)
                    filesizepath = qcow2path + '.md5'
                    if os.path.isfile(filesizepath):
                        os.remove(filesizepath)
            devices = disk_snapshots['data_devices']
            for device in devices:
                qcow2path = device['device_profile']['qcow2path']
                if len(qcow2path) > 32 and os.path.isfile(qcow2path):
                    os.remove(qcow2path)
                    filesizepath = qcow2path + '.md5'
                    if os.path.isfile(filesizepath):
                        os.remove(filesizepath)

        if self._is_first_boot(disk_snapshots):
            kvm_cpu_id = '40000600-20170829-{0:x}-0-0'.format(self._id)
        else:
            kvm_cpu_id = '40000600-20170720-{0:x}-0-0'.format(self._id)

        if kvm_type == 'verify_kvm':
            kvm_cpu_id = '40000600-20190412-{0:x}-0-0'.format(self._id)

        params = {
            'kvm_type': 'takeover',
            'takeover_params': takeover_params,
            'pe_ident': None,
            'boot_disk_token': None,
            'boot_disk_bytes': None,
            'kvm_virtual_devices': [],
            'kvm_cpu_id': kvm_cpu_id,
            'iso_path': None,
            'kvm_virtual_device_hids': [],
            'floppy_path': None,
            'data_devices': ext_info['disk_snapshots']['data_devices'],
            'is_efi': ext_info['is_efi'],
            'kvm_vbus_devices': [],
            'htb_disk_path': None,
            'logic': 'windows',
            'start_kvm_flag_file': TakeOverKVMobj.kvm_flag_file,
            'nbd': ext_info.get('nbd', None),
        }

        if ext_info['logic'] == 'linux':
            params['takeover_params']['hdd_drive'] = ext_info.get('hddctl', 'virtio-blk')
            params['open_kvm_params'] = self._open_kvm_params(disk_snapshots, TakeOverKVMobj.kvm_flag_file)
            _logger.info('run_kvm open_kvm_params :{}'.format(params['open_kvm_params']))
        else:
            params['takeover_params']['hdd_drive'] = ext_info.get('hddctl', 'scsi-hd')

        params['takeover_params']['vga'] = ext_info.get('vga', 'std')
        params['takeover_params']['net'] = ext_info.get('net', 'rtl8139')
        params['takeover_params']['cpu'] = ext_info.get('cpu', 'host')

        _logger.info('takeover_task.py run_kvm name={}'.format(TakeOverKVMobj.name))

        if ext_info['logic'] == 'linux' and self._need_fix_driver(disk_snapshots):
            random_string = uuid.uuid4().hex
            params['logic'] = 'linux'
            params['boot_device_normal_snapshot_ident'] = disk_snapshots['boot_devices'][0]['device_profile'][
                'boot_device_normal_snapshot_ident']
            params['linux_disk_index_info'] = self.linux_disk_index_info(ext_info['disk_index_info'])
            params['linux_storage'] = ext_info['Storage']
            params['mount_path'] = xdata.get_path_in_ram_fs('kvm_linux', random_string)
            params['link_path'] = xdata.get_path_in_ram_fs(random_string)
            params['linux_info'] = ext_info['Linux']
            params['restore_config'] = self._get_restore_config(ext_info)
            params['ipconfigs'] = self._get_ipconfigs(kvm_adpter, ext_info['kvm_dns'], ext_info['kvm_gateway'])
            params['kvm_vbus_devices'] = None
            if params['takeover_params']['hdd_drive'] == 'IDE':
                params['kvm_virtual_devices'] = []
            else:
                params['kvm_virtual_devices'] = [
                    'pci-vdev,ven_id=6900,dev_id=4097,subsys_ven_id=5549,subsys_id=137972,revision=0,class_id=256,interface=0']
            params['htb_key_data_dir'] = None
            boxService.box_service.runRestoreKvm(json.dumps(params, ensure_ascii=False))
        else:
            boxService.box_service.runRestoreKvm(json.dumps(params, ensure_ascii=False))


class TakeoverKVMEntrance(threading.Thread):
    def __init__(self, id, debug):
        super(TakeoverKVMEntrance, self).__init__()
        self.name = r'TakeoverKVMEntrance_{}'.format(id)
        self._id = id
        self._engine = None
        self._book_uuid = None
        self._debug = debug

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
            create_flow = create_flow_for_kvm
            self._engine = engines.load_from_factory(create_flow, backend=backend, book=book, engine='serial',
                                                     factory_args=(self.name, self._id, self._debug)
                                                     )

            self._book_uuid = book.uuid
            return {'book_id': book.uuid, 'flow_id': self._engine.storage.flow_uuid}
        except Exception as e:
            _logger.error(r'TakeoverKVMEntrance generate_uuid failed {}'.format(e))
            _logger.error('TakeoverKVMEntrance {}'.format(traceback.format_exc()))
            with contextlib.closing(backend.get_connection()) as conn:
                conn.destroy_logbook(book.uuid)
            raise e

    def start(self):
        if self._engine:
            super().start()
        else:
            xlogging.raise_and_logging_error('TakeoverKVMEntrance start 内部异常，无效的调用',
                                             r'start without _engine ：{}'.format(self.name),
                                             status.HTTP_501_NOT_IMPLEMENTED)

    def run(self):
        try:
            with logging_listener.DynamicLoggingListener(self._engine):
                self._engine.run()
        except Exception as e:
            _logger.error(r'TakeoverKVMEntrance run engine {} failed {}'.format(self.name, e))
            _logger.error('TakeoverKVMEntrance {}'.format(traceback.format_exc()))
        finally:
            with contextlib.closing(task_backend.get_backend().get_connection()) as conn:
                conn.destroy_logbook(self._book_uuid)
        self._engine = None

    @staticmethod
    def has_error(task_content):
        return task_content['error']


class create_macvtap(task.Task):
    default_provides = 'task_content'

    def __init__(self, name, id, debug, inject=None):
        super(create_macvtap, self).__init__('create_macvtap_{}'.format(id), inject=inject)
        self._id = id
        self.task_content = None

    def _save_kvm_run_info(self, kvm_key, kvm_value):
        TakeOverKVMobj = TakeOverKVM.objects.get(id=self._id)
        flag_file = TakeOverKVMobj.kvm_flag_file
        try:
            with open(flag_file, 'r+') as fout:
                info = json.loads(fout.read())
                info[kvm_key] = kvm_value
                fout.seek(0)
                fout.truncate()
                fout.write(json.dumps(info, ensure_ascii=False))
        except Exception as e:
            _logger.info('_save_kvm_run_info r Failed.e={}'.format(e))

    def _excute_cmd_and_return_code(self, cmd, ignor_error):
        workpath = os.path.dirname(os.path.realpath(__file__))
        _logger.info(r'_excute_cmd_and_return_code cmd={},workpath={}'.format(cmd, workpath))
        with subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=workpath,
                              universal_newlines=True) as p:
            stdoutdata, stderrdata = p.communicate()
            if stdoutdata:
                _logger.info(r'_excute_cmd_and_return_code stdoutdata={}'.format(stdoutdata))
            if stderrdata:
                _logger.info(r'_excute_cmd_and_return_code stderrdata={}'.format(stderrdata))
                if not ignor_error:
                    self.task_content['error'] = r'cmd={},err={}'.format(cmd, stderrdata)
                    self._save_kvm_run_info('msg', '执行命令{}出错：{}'.format(cmd, stderrdata))
                    raise Exception('执行命令{}出错：{}'.format(cmd, stderrdata))

        return p.returncode

    def _is_adpter_name_exist(self, name):
        info = psutil.net_if_addrs()
        for k, v in info.items():
            if k == name:
                return True
        return False

    def _get_unused_macvtap_name(self):
        for i in range(100):
            name = r'macvtap{}'.format(i)
            if not self._is_adpter_name_exist(name):
                return name
        return 'none'

    def _FmtMAC(self, mac):
        mac = xdata.standardize_mac_addr(mac)
        if len(mac) == 12:
            mac = '{}:{}:{}:{}:{}:{}'.format(mac[0:2], mac[2:4], mac[4:6], mac[6:8], mac[8:10], mac[10:])
        return mac

    def _net_restart_service(self):
        box_service.refreshNetwork()
        return 0

    def _create_aio_tap(self, tap_name):
        cmd = r'ip tuntap add  {tap_name} mode tap'.format(tap_name=tap_name)
        self._excute_cmd_and_return_code(cmd, False)
        cmd = r'ip link set {tap_name} master takeoverbr0'.format(tap_name=tap_name)
        self._excute_cmd_and_return_code(cmd, False)
        cmd = r'ifconfig {tap_name} {ip} up'.format(tap_name=tap_name, ip='{}{}'.format(
            GetDictionary(DataDictionary.DICT_TYPE_TAKEOVER_SEGMENT, 'SEGMENT', '172.29'), '.16.1'))
        self._excute_cmd_and_return_code(cmd, False)

    def _create_br(self):
        name = 'takeoverbr0'
        if not self._is_adpter_name_exist(name):
            cmd = r'ip link add takeoverbr0 type bridge'
            self._excute_cmd_and_return_code(cmd, True)

            # cmd = r'ifconfig takeoverbr0 0.0.0.0 promisc up'
            # self._excute_cmd_and_return_code(cmd, True)

            cmd = r'ifconfig takeoverbr0 {} up'.format(
                '{}{}'.format(GetDictionary(DataDictionary.DICT_TYPE_TAKEOVER_SEGMENT, 'SEGMENT', '172.29'), '.16.2'))
            self._excute_cmd_and_return_code(cmd, True)

        tap_name = 'aiotap0'
        if not self._is_adpter_name_exist(tap_name):
            self._create_aio_tap(tap_name)
            time.sleep(5)
            self._net_restart_service()

    def _get_same_macvtap(self, mac):
        info = psutil.net_if_addrs()
        for k, v in info.items():
            for item in v:
                if self._FmtMAC(item[1]) == self._FmtMAC(mac):
                    if k.find('macvtap') == 0:
                        return k
        return None

    def _get_same_tap(self, mac):
        info = psutil.net_if_addrs()
        for k, v in info.items():
            for item in v:
                if self._FmtMAC(item[1]) == self._FmtMAC(mac):
                    if k.find('takeovertap') == 0:
                        return k
        return None

    def _get_unused_tap_name(self):
        for i in range(100):
            name = r'takeovertap{}'.format(i)
            if not self._is_adpter_name_exist(name):
                return name
        return 'none'

    def _create_tap(self, mac):
        tap_name = self._get_same_tap(mac)
        if tap_name is not None:
            return tap_name

        tap_name = self._get_unused_tap_name()
        cmd = r'ip tuntap add  {tap_name} mode tap'.format(tap_name=tap_name)
        self._excute_cmd_and_return_code(cmd, False)
        cmd = r'ip link set {tap_name} master takeoverbr0'.format(tap_name=tap_name)
        self._excute_cmd_and_return_code(cmd, False)
        cmd = r'ifconfig {tap_name} 0 0.0.0.0 up'.format(tap_name=tap_name)
        self._excute_cmd_and_return_code(cmd, False)
        return tap_name

    def _create_macvtap(self, name, mac):
        if name in ('private_aio', 'my_private_aio'):
            return self._create_tap(mac)

        macvtap_name = self._get_same_macvtap(mac)
        if macvtap_name is not None:
            return macvtap_name

        macvtap_name = self._get_unused_macvtap_name()
        cmd = r'ip li add link {name} {macvtap} address {mac} type macvtap mode bridge'.format(name=name,
                                                                                               macvtap=macvtap_name,
                                                                                               mac=self._FmtMAC(mac))
        self._excute_cmd_and_return_code(cmd, False)
        cmd = r'ip link set {} up'.format(macvtap_name)
        self._excute_cmd_and_return_code(cmd, False)
        return macvtap_name

    def _create_vlan(self, name, vlan_no):
        if name in ('private_aio', 'my_private_aio'):
            return None
        if vlan_no is None:
            return None

        vlan_adpter_name = 'vlan_{name}_{vlan_no}'.format(name=name, vlan_no=vlan_no)
        if self._is_adpter_name_exist(vlan_adpter_name):
            return vlan_adpter_name
        cmd = 'ip link add link {name} name {vlan_adpter_name} type vlan id {vlan_no}'.format(name=name,
                                                                                              vlan_adpter_name=vlan_adpter_name,
                                                                                              vlan_no=vlan_no)
        self._excute_cmd_and_return_code(cmd, False)
        cmd = r'ip link set {} up'.format(vlan_adpter_name)
        self._excute_cmd_and_return_code(cmd, False)
        return vlan_adpter_name

    def execute(self, **kwargs):
        task_content = {'error': ''}
        self.task_content = task_content
        self._create_br()
        TakeOverKVMobj = TakeOverKVM.objects.get(id=self._id)
        ext_info = json.loads(TakeOverKVMobj.ext_info)
        kvm_adpter = list()
        for adpter in ext_info['kvm_adpter']:
            vlan_adpter_name = self._create_vlan(adpter['name'], adpter.get('vlan_no'))
            if vlan_adpter_name:
                adpter_name = vlan_adpter_name
            else:
                adpter_name = adpter['name']
            kvm_adpter.append(
                {'mac': adpter['mac'], 'name': adpter['name'], "nic_name": adpter.get('nic_name', None),
                 'ips': adpter['ips'],
                 'macvtap': self._create_macvtap(adpter_name, adpter['mac'])})
        ext_info['kvm_adpter'] = kvm_adpter
        TakeOverKVM.objects.filter(id=self._id).update(ext_info=json.dumps(ext_info, ensure_ascii=False))
        return self.task_content


class make_floppy(task.Task):
    default_provides = 'task_content'

    def __init__(self, name, id, debug, inject=None):
        super(make_floppy, self).__init__('make_iso_{}'.format(id), inject=inject)
        self._id = id

    def execute(self, task_content, **kwargs):
        if TakeoverKVMEntrance.has_error(task_content):
            return task_content
        TakeOverKVMobj = TakeOverKVM.objects.get(id=self._id)
        if TakeOverKVMobj.kvm_type == 'verify_kvm':
            return task_content

        ext_info = json.loads(TakeOverKVMobj.ext_info)

        floppy_path = ext_info.get('floppy_path', None)
        if floppy_path and os.path.isfile(floppy_path):
            return task_content

        flp_path = TakeOverKVMobj.kvm_flag_file + '{}.flp'.format(self._id)

        if ext_info['logic'] == 'linux':
            if TakeOverKVMobj.kvm_type == 'forever_kvm':
                ext_info['floppy_path'] = flp_path
                TakeOverKVM.objects.filter(id=self._id).update(ext_info=json.dumps(ext_info, ensure_ascii=False))
            return task_content
        else:

            info = dict()
            info['aio_ip'] = '{}{}'.format(
                GetDictionary(DataDictionary.DICT_TYPE_TAKEOVER_SEGMENT, 'SEGMENT', '172.29'),
                '.16.1')
            info['tunnel_ip'] = -1
            info['tunnel_port'] = -1
            info['SetIpInfo'] = list()
            info['aio_ini'] = self._get_ini_content(ext_info)
            info['SetRoute'] = ext_info['kvm_route']
            kvm_adpters = ext_info['kvm_adpter']
            for kvm_adpter in kvm_adpters:
                mtu = int(GetDictionary(DataDictionary.DICT_TYPE_AIO_NETWORK, 'aio_mtu', -1))
                one_info = {"ip_mask_list": [], "gate_way": "", "mac": "", "name": "", "dns_list": [], 'mtu': mtu}
                if len(kvm_adpter['ips']) == 0:
                    continue
                for ip in kvm_adpter['ips']:
                    one_info['ip_mask_list'].append(
                        {"ip": ip['ip'], "mask": ip["mask"], "ip_type": xdata.HTB_IP_TYPE_CONTROL})
                for kvm_gateway in ext_info['kvm_gateway']:
                    one_info['gate_way'] = kvm_gateway
                one_info['mac'] = kvm_adpter['mac']
                one_info['name'] = kvm_adpter['name']
                one_info['nic_name'] = kvm_adpter.get('nic_name', None)
                for dns in ext_info['kvm_dns']:
                    one_info['dns_list'].append(dns)
                info['SetIpInfo'].append(one_info)

            adpterinfo_str = json.dumps(info, ensure_ascii=False)

            flag_string = r'7294847cc045474882a93ec99090797b'
            flag_raw_content = [ord(letter) for letter in flag_string]

            with open(flp_path, 'wb') as file_object:
                file_bytes = 1024 * 1024 * 20  # 20MB
                file_object.truncate(file_bytes)
                file_object.seek(1024 * 1024 * 10)
                file_object.write(bytearray(flag_raw_content))
                file_object.write(bytearray(adpterinfo_str, 'utf-8'))

            ext_info['floppy_path'] = flp_path
            TakeOverKVM.objects.filter(id=self._id).update(ext_info=json.dumps(ext_info, ensure_ascii=False))
            return task_content

    def _get_ini_content(self, ext_info):
        userid = "-1"
        username = ""
        timestamp = "{}".format(time.time())
        try:
            user = User.objects.get(username=ext_info["UserIdent"])
        except User.DoesNotExist:
            pass
        else:
            userid = str(user.id)
            username = ext_info["UserIdent"]
        return {'userid': userid, 'username': username, 'timestamp': timestamp}


class RunKVM(task.Task):
    default_provides = 'task_content'

    def __init__(self, name, id, debug, inject=None):
        super(RunKVM, self).__init__('RunKVM_{}'.format(id), inject=inject)
        self._id = id
        self._debug = debug

    def execute(self, task_content, **kwargs):
        if TakeoverKVMEntrance.has_error(task_content):
            return task_content
        takeover_kvm_wrapper(self._id, self._debug).run_kvm()
        return task_content


def create_flow_for_kvm(name, id, debug):
    flow = lf.Flow(name).add(
        create_macvtap(name, id, debug),
        make_floppy(name, id, debug),
        RunKVM(name, id, debug),
    )
    return flow
