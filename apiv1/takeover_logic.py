import json
import os
import random
import shlex
import subprocess
import time
import uuid

from django.db.models import Q
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apiv1.models import TakeOverKVM, HostSnapshot, StorageNode, DiskSnapshot
from apiv1.serializers import TakeOverKVMSerializer
from apiv1.snapshot import DiskSnapshotLocker, GetSnapshotList, GetDiskSnapshot
from apiv1.takeover_task import TakeoverKVMEntrance
from box_dashboard import xlogging, xdata, boxService, pyconv, xdatetime
from xdashboard.common.dict import GetDictionary
from xdashboard.models import DataDictionary

_logger = xlogging.getLogger(__name__)

import IMG


class TakeOverKVMCreate(APIView):
    serializer_class_s = TakeOverKVMSerializer
    query_set = TakeOverKVM.objects.all()

    def __init__(self, **kwargs):
        super(TakeOverKVMCreate, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def _create_start_kvm_flag_file(self, storage_ident):
        storagenode = StorageNode.objects.filter(ident=storage_ident).first()
        folder_path = os.path.join(storagenode.path, 'takeover_kvm_user_data')
        if not boxService.box_service.isFolderExist(folder_path):
            boxService.box_service.makeDirs(folder_path)
        flag_file = os.path.join(folder_path, uuid.uuid4().hex)
        return flag_file

    def run_cmd(self, cmd):
        split_cmd = shlex.split(cmd)
        with subprocess.Popen(split_cmd, stderr=subprocess.PIPE, universal_newlines=True) as p:
            stdoutdata, stderrdata = p.communicate()
            if stdoutdata:
                _logger.info(r'takeover_logic.py run_cmd stdoutdata={}'.format(stdoutdata))
            if stderrdata:
                _logger.info(r'takeover_logic.py run_cmd stderrdata={}'.format(stderrdata))
        _logger.info("takeover_logic.py run_cmd returncode={}".format(p.returncode))
        return p.returncode

    def _logic(self, ext_info):
        system_infos = ext_info['system_infos']
        if 'LINUX' not in system_infos['System']['SystemCaption'].upper():
            return 'windows'
        return 'linux'

    def _disk_snapshot(self, point_type, storage_ident, host_snapshot, snapshot_time, logic):
        storagenode = StorageNode.objects.filter(ident=storage_ident).first()
        folder_path = os.path.join(storagenode.path, 'takeover_kvm_user_data')
        if not boxService.box_service.isFolderExist(folder_path):
            boxService.box_service.makeDirs(folder_path)
        if point_type == xdata.SNAPSHOT_TYPE_NORMAL:
            snapshot_time = None
        else:
            snapshot_time = xdatetime.string2datetime(snapshot_time)
            snapshot_time = snapshot_time.timestamp()
        return self._get_disk_snapshot(host_snapshot, snapshot_time, folder_path, logic)

    def _get_disk_snapshot(self, host_snapshot, snapshot_time, folder_path, logic):
        disksnapshot = dict()
        data_devices = list()
        boot_devices = list()
        mdisk_snapshots = DiskSnapshot.objects.filter(host_snapshot=host_snapshot.id)
        if len(mdisk_snapshots) == 0:
            xlogging.raise_and_logging_error('不存在的客户端快照', 'invalid host snapshot id', status.HTTP_404_NOT_FOUND)
        host_snapshot_ext_info = json.loads(host_snapshot.ext_info)
        disk_index_info = host_snapshot_ext_info['disk_index_info']
        for index_info in disk_index_info:
            index_info['disk_ident'] = \
                DiskSnapshot.objects.get(ident=index_info['snapshot_disk_ident']).disk.ident

        for disk_snapshot in mdisk_snapshots:
            boot_device = False
            device_profile = dict()
            device_profile['wwid'] = uuid.uuid4().hex
            disks = list()
            qcow2path = os.path.join(folder_path, '{}.qcow2'.format(time.time()))
            save_disk_ident = None
            for info in disk_index_info:
                if disk_snapshot.disk.ident == info['disk_ident']:
                    save_disk_ident = info['disk_ident']
                    device_profile['snapshot_disk_index'] = info['snapshot_disk_index']
                    device_profile['DiskSize'] = str(disk_snapshot.bytes)
                    boot_device = info['boot_device']
                    break
            device_profile['qcow2path'] = qcow2path
            device_profile['nbd'] = json.loads(boxService.box_service.NbdFindUnusedReverse())

            if snapshot_time is None:
                disk_snapshot_object = DiskSnapshot.objects.get(ident=disk_snapshot.ident)
                restore_timestamp = None
            else:
                disk_ident = disk_snapshot.disk.ident
                disk_snapshot_ident, restore_timestamp = \
                    GetDiskSnapshot.query_cdp_disk_snapshot_ident(host_snapshot, disk_ident, snapshot_time)
                if disk_snapshot_ident is None or restore_timestamp is None:
                    disk_snapshot_ident = GetDiskSnapshot.query_normal_disk_snapshot_ident(host_snapshot,
                                                                                           disk_ident)
                    if disk_snapshot_ident is None:
                        _logger.error(
                            'no valid cdp disk snapshot,and get normal failed {} {} {}'.format(
                                host_snapshot.id,
                                disk_ident,
                                snapshot_time))
                        continue
                    _logger.warning('no valid cdp disk snapshot use normal snapshot : {} {} {} {}'.format(
                        host_snapshot.id, disk_ident, snapshot_time, disk_snapshot_ident))
                else:
                    _logger.debug(
                        'get valid cdp disk snapshot {} {} {} {}'.format(host_snapshot.id, disk_ident,
                                                                         snapshot_time, disk_snapshot_ident))

                disk_snapshot_object = DiskSnapshot.objects.get(ident=disk_snapshot_ident)

            if disk_snapshot_object is None:
                xlogging.raise_and_logging_error('获取硬盘快照信息失败', r'get disk info failed disk_snapshot_object is None')

            validator_list = [GetSnapshotList.is_disk_snapshot_object_exist,
                              GetSnapshotList.is_disk_snapshot_file_exist]
            disk_snapshots = GetSnapshotList.query_snapshots_by_snapshot_object(disk_snapshot_object, validator_list,
                                                                                restore_timestamp)
            if len(disk_snapshots) == 0:
                xlogging.raise_and_logging_error('获取硬盘快照信息失败',
                                                 r'get disk info failed name {} time {}'.format(disk_snapshot.ident,
                                                                                                restore_timestamp))
            for disk_snapshot_m in disk_snapshots:
                disks.append({"path": disk_snapshot_m.path, "ident": disk_snapshot_m.snapshot})
            if boot_device == True:
                if logic == 'linux':
                    device_profile['boot_device_normal_snapshot_ident'] = disk_snapshot.ident
                boot_devices.append(
                    {"device_profile": device_profile, "disk_snapshots": disks, "disk_ident": save_disk_ident})
            else:
                data_devices.append(
                    {"device_profile": device_profile, "disk_snapshots": disks, "disk_ident": save_disk_ident})
        disksnapshot['data_devices'] = data_devices
        disksnapshot['boot_devices'] = boot_devices
        return disksnapshot

    def _is_efi(self, host_snapshot):
        host_snapshot_ext_info = json.loads(host_snapshot.ext_info)
        disk_index_infos = host_snapshot_ext_info['disk_index_info']
        disk_index = -1
        for disk_index_info in disk_index_infos:
            if disk_index_info['boot_device'] == True:
                disk_index = disk_index_info['snapshot_disk_index']
                break

        if disk_index == -1:
            _logger.error('takeover_logic.py _is_efi disk_index Failed.')

        disks = host_snapshot_ext_info['system_infos']['Disk']
        linux = host_snapshot_ext_info['system_infos'].get('Linux', {'boot_firmware': None})
        if linux.get('boot_firmware', None) is None:
            for disk in disks:
                if int(disk_index) == int(disk['DiskNum']):
                    if disk['Style'] == 'mbr':
                        return False
                    else:
                        return True
        else:
            return True if linux['boot_firmware'] == 'efi' else False
        return False

    def _lock_all_nbd(self):
        kvms = self.query_set.all()
        for kvm in kvms:
            ext_info = json.loads(kvm.ext_info)
            disk_snapshots = ext_info['disk_snapshots']
            devices = disk_snapshots['boot_devices']
            for device in devices:
                nbd = device['device_profile'].get('nbd', None)
                if nbd:
                    boxService.box_service.NbdSetUsed(nbd['device_name'])
            devices = disk_snapshots['data_devices']
            for device in devices:
                nbd = device['device_profile'].get('nbd', None)
                if nbd:
                    boxService.box_service.NbdSetUsed(nbd['device_name'])

    def _get_kvm_private_ip(self, nid=None):
        kvm_private_ip = list()
        if nid:
            TakeOverKVMobj = TakeOverKVM.objects.filter(~Q(id='{}'.format(nid)))
        else:
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

    def _get_kvm_mac(self, nid=None):
        kvm_mac = list()
        if nid:
            TakeOverKVMobj = TakeOverKVM.objects.filter(~Q(id='{}'.format(nid)))
        else:
            TakeOverKVMobj = TakeOverKVM.objects.all()
        for kvm in TakeOverKVMobj:
            ext_info = json.loads(kvm.ext_info)
            kvm_adpter = ext_info['kvm_adpter']
            for adpter in kvm_adpter:
                kvm_mac.append(adpter['mac'])
        return kvm_mac

    def _get_unused_mac(self):
        kvm_mac_list = self._get_kvm_mac()
        Maclist = []
        while True:
            for i in range(1, 7):
                if i == 1:
                    Maclist.append('00')
                    continue
                RANDSTR = "".join(random.sample("0123456789ABCDEF", 2))
                Maclist.append(RANDSTR)
            mac = "-".join(Maclist)
            if mac not in kvm_mac_list:
                return mac

    def _add_private_adpter(self, kvm_adpter, exist_kvm_adpter=None):
        for adpter in kvm_adpter:
            if adpter['name'] in ('my_private_aio',):
                return kvm_adpter

        bfind = False
        if exist_kvm_adpter:
            for adpter in exist_kvm_adpter:
                if adpter['name'] in ('my_private_aio',):
                    kvm_adpter.append(adpter)
                    bfind = True
                    break
        if not bfind:
            private_ip = list()
            kvm_private_ip = self._get_kvm_private_ip()
            for ip in kvm_private_ip:
                if 'ip' in ip:
                    private_ip.append(ip['ip'])

            for i in range(3, 255):
                ip = '{seg}.16.{i}'.format(
                    seg=GetDictionary(DataDictionary.DICT_TYPE_TAKEOVER_SEGMENT, 'SEGMENT', '172.29'),
                    i=i)
                if ip not in private_ip:
                    adpter = {"name": "my_private_aio", "nic_name": "Clerware Network Adapter",
                              "ips": [{"ip": ip, "mask": "255.255.0.0"}], "macvtap": "",
                              "mac": self._get_unused_mac()}
                    kvm_adpter.append(adpter)
                    break

        return kvm_adpter

    def post(self, request, api_request=None):
        try:
            if api_request is None:
                api_request = request.data

            name = api_request["name"]
            kvm_type = api_request["kvm_type"]
            point_params = api_request["pointid"].split('|')
            point_type = point_params[0]
            host_snapshot_id = point_params[1]
            host_snapshot = HostSnapshot.objects.get(id=host_snapshot_id)
            snapshot_time = api_request["snapshot_time"]
            kvm_cpu_count = api_request["kvm_cpu_count"]
            kvm_memory_size = api_request["kvm_memory_size"]
            kvm_memory_unit = api_request["kvm_memory_unit"].upper()

            ext_info = {}
            if kvm_type == 'forever_kvm':
                ext_info['kvm_adpter'] = self._add_private_adpter(api_request["kvm_adpter"])
            else:
                ext_info['kvm_adpter'] = api_request["kvm_adpter"]
            ext_info['kvm_route'] = api_request["kvm_route"]
            ext_info['kvm_gateway'] = api_request["kvm_gateway"]
            ext_info['kvm_dns'] = api_request["kvm_dns"]
            boot_firmware = api_request.get("boot_firmware", None)
            ext_info['boot_firmware'] = boot_firmware
            if boot_firmware == 'EFI':
                ext_info['is_efi'] = True
            elif boot_firmware == 'BIOS':
                ext_info['is_efi'] = False
            else:
                ext_info['is_efi'] = self._is_efi(host_snapshot)
            ext_info['kvm_pwd'] = api_request.get("kvm_pwd", None)
            ext_info['hddctl'] = api_request.get("hddctl", None)
            ext_info['vga'] = api_request.get("vga", None)
            ext_info['net'] = api_request.get("net", None)
            ext_info['cpu'] = api_request.get("cpu", None)
            storage_ident = api_request["kvm_storagedevice"]
            host_snapshot_ext_info = json.loads(host_snapshot.ext_info)
            logic = self._logic(host_snapshot_ext_info)
            ext_info['logic'] = logic
            self._lock_all_nbd()
            disk_snapshots = self._disk_snapshot(point_type, storage_ident, host_snapshot, snapshot_time,
                                                 logic)
            ext_info['disk_snapshots'] = disk_snapshots
            ext_info['UserIdent'] = host_snapshot.host.user.username

            host_snapshot_ext_info = json.loads(host_snapshot.ext_info)
            # host_ext_info = json.loads(host_snapshot.host.ext_info)
            if logic == 'linux':
                ext_info['disk_index_info'] = host_snapshot_ext_info['disk_index_info']
                ext_info['Storage'] = host_snapshot_ext_info['system_infos']['Storage']
                ext_info['Linux'] = host_snapshot_ext_info['system_infos']['Linux']
                ext_info['SystemCatName'] = host_snapshot_ext_info['system_infos']['System']['SystemCatName']
                ext_info['ProcessorArch'] = host_snapshot_ext_info['system_infos']['System']['ProcessorArch']

            obj = TakeOverKVM.objects.create(name=name,
                                             kvm_type=kvm_type,
                                             host_snapshot=host_snapshot,
                                             snapshot_time=snapshot_time,
                                             kvm_cpu_count=kvm_cpu_count,
                                             kvm_memory_size=kvm_memory_size,
                                             kvm_memory_unit=kvm_memory_unit,
                                             kvm_flag_file=self._create_start_kvm_flag_file(storage_ident),
                                             ext_info=json.dumps(ext_info, ensure_ascii=False),
                                             )
            devices = disk_snapshots['boot_devices']
            files = list()
            for device in devices:
                for disk_snapshot in device['disk_snapshots']:
                    files.append({"path": disk_snapshot['path'], "snapshot": disk_snapshot['ident']})
            devices = disk_snapshots['data_devices']
            for device in devices:
                for disk_snapshot in device['disk_snapshots']:
                    files.append({"path": disk_snapshot['path'], "snapshot": disk_snapshot['ident']})
            filesobj = [pyconv.convertJSON2OBJ(IMG.ImageSnapshotIdent, snap) for snap in files]
            DiskSnapshotLocker.lock_files(filesobj, 'takeover{}'.format(obj.id))
            return Response(status=status.HTTP_202_ACCEPTED, data={"id": obj.id})
        except Exception as e:
            _logger.error(r'TakeOverKVMCreate().post failed : {}'.format(e), exc_info=True)
            return Response(status=status.HTTP_417_EXPECTATION_FAILED, data=str(e))

    def update(self, request, api_request=None):
        TakeOverKVM_obj = self.query_set
        if api_request is None:
            api_request = request.data

        TakeOverKVM_obj = TakeOverKVM_obj.filter(id=api_request['id'])
        kvm = TakeOverKVM_obj.first()
        ext_info = json.loads(kvm.ext_info)
        ext_info_changed = False
        if 'kvm_adpter' in api_request:
            ext_info_changed = True
            if kvm.kvm_type == 'forever_kvm':
                ext_info['kvm_adpter'] = self._add_private_adpter(api_request['kvm_adpter'], ext_info['kvm_adpter'])
            else:
                ext_info['kvm_adpter'] = api_request["kvm_adpter"]
        if 'kvm_route' in api_request:
            ext_info_changed = True
            ext_info['kvm_route'] = api_request['kvm_route']
        if 'kvm_gateway' in api_request:
            ext_info_changed = True
            ext_info['kvm_gateway'] = api_request['kvm_gateway']
        if 'kvm_dns' in api_request:
            ext_info_changed = True
            ext_info['kvm_dns'] = api_request['kvm_dns']
        if 'kvm_pwd' in api_request:
            ext_info_changed = True
            ext_info['kvm_pwd'] = api_request['kvm_pwd']
        if 'hddctl' in api_request:
            ext_info_changed = True
            ext_info['hddctl'] = api_request['hddctl']
        if 'vga' in api_request:
            ext_info_changed = True
            ext_info['vga'] = api_request['vga']
        if 'net' in api_request:
            ext_info_changed = True
            ext_info['net'] = api_request['net']
        if 'cpu' in api_request:
            ext_info_changed = True
            ext_info['cpu'] = api_request['cpu']

        if 'name' in api_request:
            TakeOverKVM_obj.update(name=api_request['name'])
        if 'kvm_cpu_count' in api_request:
            TakeOverKVM_obj.update(kvm_cpu_count=api_request['kvm_cpu_count'])
        if 'kvm_memory_size' in api_request:
            TakeOverKVM_obj.update(kvm_memory_size=api_request['kvm_memory_size'])
        if 'kvm_memory_unit' in api_request:
            TakeOverKVM_obj.update(kvm_memory_unit=api_request['kvm_memory_unit'])
        if ext_info_changed:
            TakeOverKVM_obj.update(ext_info=json.dumps(ext_info, ensure_ascii=False))

        return Response(status=status.HTTP_202_ACCEPTED)

    def delete(self, request, api_request=None):
        if api_request is None:
            api_request = request.data
        id = api_request['id']
        only_hdd = api_request.get('only_hdd', False)
        one_kvm = self.query_set.filter(id=id).first()
        kvm_flag_file = one_kvm.kvm_flag_file
        if os.path.isfile(kvm_flag_file):
            return Response(status=status.HTTP_400_BAD_REQUEST, data='请先关闭虚拟机，再删除')
        ext_info = json.loads(one_kvm.ext_info)
        disk_snapshots = ext_info['disk_snapshots']
        devices = disk_snapshots['boot_devices']
        files = list()
        for device in devices:
            for disk_snapshot in device['disk_snapshots']:
                files.append({"path": disk_snapshot['path'], "snapshot": disk_snapshot['ident']})
            qcow2path = device['device_profile']['qcow2path']
            if len(qcow2path) > 32 and os.path.isfile(qcow2path):
                os.remove(qcow2path)
                filesizepath = qcow2path + '.md5'
                if os.path.isfile(filesizepath):
                    os.remove(filesizepath)
            nbd = device['device_profile'].get('nbd', None)
            if nbd and not only_hdd:
                boxService.box_service.NbdSetUnused(nbd['device_name'])

        devices = disk_snapshots['data_devices']
        for device in devices:
            for disk_snapshot in device['disk_snapshots']:
                files.append({"path": disk_snapshot['path'], "snapshot": disk_snapshot['ident']})
            qcow2path = device['device_profile']['qcow2path']
            if len(qcow2path) > 32 and os.path.isfile(qcow2path):
                os.remove(qcow2path)
                filesizepath = qcow2path + '.md5'
                if os.path.isfile(filesizepath):
                    os.remove(filesizepath)
            nbd = device['device_profile'].get('nbd', None)
            if nbd and not only_hdd:
                boxService.box_service.NbdSetUnused(nbd['device_name'])

        if only_hdd:
            return Response(status=status.HTTP_202_ACCEPTED)

        filesobj = [pyconv.convertJSON2OBJ(IMG.ImageSnapshotIdent, snap) for snap in files]
        DiskSnapshotLocker.unlock_files(filesobj, 'takeover{}'.format(id))

        floppy_path = ext_info.get('floppy_path', None)
        if floppy_path and os.path.isfile(floppy_path):
            os.remove(floppy_path)

        monitors_addr = r'{}_m'.format(kvm_flag_file)
        if os.path.exists(monitors_addr):
            os.unlink(monitors_addr)

        one_kvm.delete()
        return Response(status=status.HTTP_202_ACCEPTED)

    def get(self, request, api_request=None):
        if api_request is None:
            api_request = request.data
        current_user = None if request is None else request.user
        type = api_request.get('type', None)

        if api_request.get('debug') != '1':
            self.query_set = self.query_set.filter(~Q(kvm_type='verify_kvm'))

        if type == 'count':
            kvm_type = api_request.get('kvm_type')
            if kvm_type:
                data = self.query_set.filter(kvm_type=kvm_type)
            else:
                data = self.query_set.filter()
            result = json.dumps(
                {'r': 0, 'count': data.count()},
                ensure_ascii=False)
            return Response(data=result, status=status.HTTP_200_OK)
        id = api_request.get('id', None)
        if id:
            data = self.query_set.filter(id=id).all()
        elif current_user.is_superuser:
            data = self.query_set.all()
        else:
            data = self.query_set.filter(host_snapshot__host__user_id=current_user.id).all()
        sidx = api_request.get('sidx', None)
        if sidx is not None:
            data = data.order_by(sidx)
        return Response(status=status.HTTP_202_ACCEPTED, data=self.serializer_class_s(data, many=True).data)


class TakeOverKVMExecute(APIView):
    def __init__(self, **kwargs):
        super(TakeOverKVMExecute, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def post(self, request, api_request=None):
        if api_request is None:
            api_request = request.data
        kvm_flow = TakeoverKVMEntrance(api_request['id'], api_request['debug'])
        kvm_flow.generate_uuid()
        kvm_flow.start()
        return Response(status=status.HTTP_202_ACCEPTED)
