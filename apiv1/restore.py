import base64
import datetime
import gzip
import json
import os
import re
import threading
import time
import uuid

import psutil
from django.utils import timezone
from box_dashboard import boxService, xlogging, xdata, pyconv, xdatetime
from .compress import CompressTaskThreading
from .models import RestoreTargetDisk, DiskSnapshot, RestoreTarget, RestoreTask, MigrateTask, HostLog, HostSnapshot, \
    Tunnel, HTBTask
from .snapshot import GetSnapshotList, DiskSnapshotLocker

_logger = xlogging.getLogger(__name__)

import Box
import IMG
import BoxLogic
import PerpcIce
import KTService
from xdashboard.common.dict import GetDictionary
from xdashboard.models import DataDictionary
from rest_framework import status


class RestoreSourceValidChecker(threading.Thread):
    def __init__(self):
        super(RestoreSourceValidChecker, self).__init__(name='RestoreSourceValidChecker')
        self.restore_objs = list()
        self._locker = threading.Lock()

    def run(self):
        _logger.debug('RestoreSourceValidChecker start')

        time.sleep(60)
        while True:
            try:
                with self._locker:
                    self._run()
            except Exception as e:
                _logger.error('RestoreSourceValidChecker error : {}'.format(e), exc_info=True)
            time.sleep(60)

    def _run(self):
        for x in self.restore_objs:
            try:
                x.restore_source_valid_checker()
            except Exception as e:
                _logger.error('RestoreSourceValidChecker error : {}'.format(e), exc_info=True)

    def insert_one_restore_logic_obj(self, obj):
        with self._locker:
            if obj not in self.restore_objs:
                self.restore_objs.append(obj)

    def remove_one_restore_logic_obj(self, obj):
        with self._locker:
            if obj in self.restore_objs:
                self.restore_objs.remove(obj)


_restore_source_valid_checker = None


def get_restore_source_valid_checker_obj():
    global _restore_source_valid_checker
    if _restore_source_valid_checker is None:
        _restore_source_valid_checker = RestoreSourceValidChecker()
    return _restore_source_valid_checker


def _is_cancel_task(task_object, create_host_log=True):
    if task_object is not None:
        ext_config = json.loads(task_object.ext_config)
        if xdata.CANCEL_TASK_EXT_KEY in ext_config:
            if create_host_log:
                stage = 'OPERATE_TASK_STATUS_DESELECT'
                if isinstance(task_object, RestoreTask):
                    task_type = 'restore_task'
                    if is_restore_target_belong_htb(task_object.restore_target):
                        log_type = HostLog.LOG_HTB
                    else:
                        log_type = HostLog.LOG_RESTORE_FAILED
                else:
                    task_type = 'migrate_task'
                    log_type = HostLog.LOG_MIGRATE_FAILED

                HostLog.objects.create(host=task_object.host_snapshot.host, type=log_type,
                                       reason=json.dumps({'description': r'用户取消任务',
                                                          'stage': stage, 'task_type': task_type,
                                                          task_type: task_object.id}))
            raise xlogging.raise_and_logging_error(r'用户取消任务', '_is_cancel_task is true', 1)


def _restore_is_complete(task_object):
    ext_info = json.loads(task_object.ext_config)
    ext_info[xdata.RESTORE_IS_COMPLETE] = 'any_value'
    task_object.ext_config = json.dumps(ext_info)
    task_object.save(update_fields=['ext_config'])


def is_restore_target_belong_htb(restore_target):
    return restore_target.htb_task.exists()


def get_plan_name_start_time_for_htb(htb_task, plan_name, start_time):
    try:
        if not htb_task:
            return plan_name, start_time
        schedule = htb_task.schedule
        src_host = htb_task.schedule.host
        exc_info = json.loads(schedule.ext_config)
        item_list = exc_info['manual_switch']['point_id'].split('|')
        time_str = exc_info['manual_switch']['restoretime']
        if src_host.is_remote:
            plan_name = src_host.display_name
        else:
            plan_name = HostSnapshot.objects.get(id=item_list[1]).schedule.name
        if item_list[0] == 'normal':
            start_time = item_list[2].replace('T', ' ')
        else:
            start_time = time_str.replace('T', ' ')
    except Exception as e:
        _logger.error('get_plan_name_start_time_for_htb error:{}'.format(e), exc_info=True)
    return plan_name, start_time


class AgentRestore(object):
    def __init__(self, host_object):
        self.host_object = host_object
        self.restore_target_object = None
        self._restore_target_disk_objects = list()
        self.restore_cmd = {'volumes': list(), 'disks': list(),
                            'config': {'aio_ip': None, 'aio_port': None, 'sockets': None, 'htb_task_uuid': None}}
        self.restore_files = list()
        self.volumes_display = list()
        self.restore_time = ''

    # data AgentHostSessionRestoreSerializer 反序列化的字典对象
    def init(self, data):
        self._generate_params(data)
        self._update_db()

        self._set_status_display_and_check_cancel(r'查询快照点状态')

        return self.restore_target_object

    def _generate_params(self, data):
        host_ext_info = json.loads(self.host_object.ext_info)

        try:
            agent_connect_ip = host_ext_info['system_infos']['ConnectAddress']['RemoteAddress']
        except (KeyError, TypeError):
            agent_connect_ip = host_ext_info['local_ip']

        self.restore_cmd['config']['aio_ip'] = boxService.get_tcp_kernel_service_ip(
            host_ext_info['local_ip'], agent_connect_ip)
        self.restore_cmd['config']['aio_port'] = boxService.get_tcp_kernel_service_port()
        self.restore_cmd['config']['sockets'] = boxService.get_tcp_kernel_service_restore_socket_number()
        self.restore_cmd['htb_task_uuid'] = data['htb_task_uuid']
        self.restore_time = data['restore_time'].strftime('%Y-%m-%d %H:%M:%S')

        for disk in data['disks']:
            restore_timestamp = disk['restore_timestamp'] if data['type'] == xdata.SNAPSHOT_TYPE_CDP else None
            disk_snapshot_object, disk_snapshots = PeRestore.get_disk_snapshot_object(
                disk['disk_snapshot_ident'], restore_timestamp, r'volume restore {}'.format(self.host_object.ident))

            self.restore_files.append(Box.RestoreFile(disk['disk_index'], disk_snapshot_object.bytes, disk_snapshots))

            restore_target_disk_token = uuid.uuid4().hex.lower()

            self._restore_target_disk_objects.append(
                RestoreTargetDisk(token=restore_target_disk_token, snapshot=disk_snapshot_object,
                                  snapshot_timestamp=restore_timestamp))

            if restore_timestamp is None:
                try:
                    backup_datetime = disk_snapshot_object.host_snapshot.start_datetime
                    restore_timestamp = int(backup_datetime.timestamp())
                except Exception as e:
                    _logger.warning(r'get restore_timestamp failed {}'.format(e))
                    restore_timestamp = int(datetime.datetime.now().timestamp())
            else:
                restore_timestamp = int(restore_timestamp)

            self.restore_cmd['disks'].append({
                'disk_number': int(disk['disk_index']), 'disk_token': restore_target_disk_token,
                'disk_bytes': str(disk_snapshot_object.bytes), 'timestamp': str(restore_timestamp),
            })

            for volume in disk['volumes']:
                for restore_cmd_volume in self.restore_cmd['volumes']:
                    if restore_cmd_volume["device_name"] == volume['device_name']:
                        restore_cmd_volume["disks"].append({
                            "disk_number": int(disk['disk_index']),
                            "ranges": [{
                                "sector_offset": volume['sector_offset'],
                                "sectors": volume['sectors'],
                            }]
                        })
                        break
                else:
                    self.restore_cmd['volumes'].append({
                        "device_name": volume['device_name'],
                        "display_name": volume['target_display_name'],
                        "disks": [{
                            "disk_number": int(disk['disk_index']),
                            "ranges": [{
                                "sector_offset": volume['sector_offset'],
                                "sectors": volume['sectors'],
                            }]
                        }],
                        "mount_point_after_restore": volume['mount_point_after_restore'],
                        "mount_fs_type_after_restore": volume['mount_fs_type_after_restore'],
                        "mount_fs_opts_after_restore": volume['mount_fs_opts_after_restore'],
                    })
                    self.volumes_display.append(volume['display_name'])

    def _update_db(self):
        pe_info = {
            'restore_files': [pyconv.convert2JSON(restore_file) for restore_file in self.restore_files],
            'restore_cmd': self.restore_cmd,
            'remote_ip': self.host_object.last_ip,
            'restore_time': self.restore_time,
        }

        self.restore_target_object = RestoreTarget.objects.create(
            ident=uuid.uuid4().hex.lower(),
            type=RestoreTarget.TYPE_AGENT_RESTORE,
            start_datetime=timezone.now(),
            display_name='AgentRestore {}'.format(self.host_object.ident),
            info=json.dumps(pe_info),
            expiry_minutes=1440 * 7  # expiry 7 days
        )

        self._update_htb_task()

        self.restore_target_object.token_expires = self.restore_target_object.start_datetime + datetime.timedelta(
            minutes=self.restore_target_object.expiry_minutes)
        self.restore_target_object.save(update_fields=['token_expires'])

        for restore_target_disk_object in self._restore_target_disk_objects:
            restore_target_disk_object.pe_host = self.restore_target_object
            restore_target_disk_object.save()

    def _set_status_display_and_check_cancel(self, value, stage='', create_log_when_user_cancel=True):
        if self.restore_target_object is None:
            return

        self.restore_target_object.display_status = value
        self.restore_target_object.save(update_fields=['display_status'])

        task_object = self.get_task_object()
        if isinstance(task_object, RestoreTask):
            task_type = ''
            if is_restore_target_belong_htb(self.restore_target_object):
                task_type = 'migrate_task'
                log_type = HostLog.LOG_HTB
            else:
                task_type = 'restore_task'
                log_type = HostLog.LOG_RESTORE_START
            HostLog.objects.create(host=task_object.host_snapshot.host, type=log_type,
                                   reason=json.dumps({'description': value, 'stage': stage,
                                                      'task_type': task_type, task_type: task_object.id}))
        _is_cancel_task(self.get_task_object(), create_log_when_user_cancel)

    def get_task_object(self):
        try:
            restore_task = self.restore_target_object.restore
            return RestoreTask.objects.get(id=restore_task.id)
        except RestoreTask.DoesNotExist:
            return None

    def work(self, task_name):
        try:
            _logger.info(r'{} : running'.format(task_name))
            self._set_status_display_and_check_cancel(r'锁定快照点文件', 'TASK_STEP_IN_PROGRESS_RESTORE_LOCK_SNAPSHOT', False)
            self._check_and_lock_disk_snapshots(task_name)
            self._set_status_display_and_check_cancel(r'发送任务指令到目标客户端',
                                                      'TASK_STEP_IN_PROGRESS_RESTORE_SEND_COMMAND_TO_CLIENT', False)
            self._send_cmd_to_agent_host()
            self._set_status_display_and_check_cancel(r'等待目标客户端完成初始化', 'TASK_STEP_IN_PROGRESS_RESTORE_WAIT_CLIENT_INIT',
                                                      False)
        except Exception as e:
            _logger.warning(r'AgentRestore work failed : {}'.format(e), exc_info=True)
            self.unlock_disk_snapshots(task_name)
            raise e
        finally:
            _restore_is_complete(self.get_task_object())

    def _check_and_lock_disk_snapshots(self, task_name):
        PeRestore.check_and_lock_disk_snapshots_by_restore_files(self.restore_files, task_name)

    def unlock_disk_snapshots(self, task_name):
        PeRestore.unlock_disk_snapshots_by_restore_files(self.restore_files, task_name)

    def _send_cmd_to_agent_host(self):
        boxService.box_service.volumeRestore(self.host_object.ident, json.dumps(self.restore_cmd, ensure_ascii=False),
                                             self.restore_files, self.restore_target_object.ident)

    def _update_htb_task(self):
        if self.restore_cmd['htb_task_uuid']:
            htb_task = HTBTask.objects.get(task_uuid=self.restore_cmd['htb_task_uuid'])
            htb_task.restore_target = self.restore_target_object
            htb_task.save(update_fields=['restore_target'])


VID_FINDER = [r'VEN_.{4}', 4, 8]
DEV_ID_FINDER = [r'DEV_.{4}', 4, 8]
SUBSYS_VEN_ID_FINDER = [r'SUBSYS_.{8}', 11, 15]
SUBSYS_ID_FINDER = [r'SUBSYS_.{8}', 7, 11]
REVISION_FINDER = [r'REV_.{2}', 4, 6]
CLASS_ID_FINDER = [r'CC_.{6}', 3, 7]
INTERFACE_FINDER = [r'CC_.{6}', 7, 9]


class PeRestore(object):
    def __init__(self, restore_target_object):
        self._restore_target_object = restore_target_object

        self._pe_ident = restore_target_object.ident
        self._pe_info = json.loads(restore_target_object.info)
        if self._pe_info.get('soft_ident') is None:
            self._pe_info['soft_ident'] = ""
        self._pe_stage_iso_temp_folder = r'/dev/shm/pe_stage_iso_{}'.format(self._pe_ident)
        self._pe_stage_iso_file_path = self._pe_stage_iso_temp_folder + '.iso'
        self._floppy_path = r'/dev/shm/pe_stage_floppy_{}.bin'.format(self._pe_ident)
        self._fake_hardware_ids = self._load_fake_hardware_ids()
        self._fake_hardware_ids_pci = self._load_fake_hardware_ids_pci()
        self._restore_source_valid = True
        self._restore_optimize_parameters = list()

    def _open_kvm_params(self):
        """
        获取起kvm的参数
        :param host_snapshot: 主机快照
        :return:
        """
        params_file_name = '{}_restore.json'.format(uuid.uuid4().hex)
        params = {
            "logic": 'linux',
            "system_type": 64,
            "vnc": None,
            "shutdown": False,
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
            },
            'disk_devices': self._pe_info['kvm_disk_params']
        }
        _logger.info('kvm_disk_params:{}'.format(self._pe_info['kvm_disk_params']))
        params['tmp_qcow'] = '/home/kvm_rpc/{}_tmp.qcow2'.format(params['disk_devices'][0]['disk_snapshot_ident'])
        params['aio_server_ip'] = '{}'.format(
            '{}{}'.format(GetDictionary(DataDictionary.DICT_TYPE_TAKEOVER_SEGMENT, 'SEGMENT', '172.29'), '.16.2'))
        return params

    def restore_source_valid_checker(self):
        task_obj = self.get_task_object()
        if task_obj.host_snapshot.finish_datetime is not None and (
                not task_obj.host_snapshot.successful or task_obj.host_snapshot.partial):
            self._restore_source_valid = False
            force_kill_kvm(task_obj)
            _logger.warning(r'task : {} restore source invalid'.format(task_obj.name))

    def _is_restore_source_invalid(self):
        task_object = self.get_task_object()
        if (task_object is not None) and (not self._restore_source_valid):
            if isinstance(task_object, RestoreTask):
                raise xlogging.raise_and_logging_error(
                    r'备份源机上传备份数据失败', '_restore_source_valid is false {}'.format(task_object.name), 1)
            elif isinstance(task_object, MigrateTask):
                raise xlogging.raise_and_logging_error(
                    r'迁移源机上传数据失败', '_restore_source_valid is false {}'.format(task_object.name), 1)
            else:
                raise xlogging.raise_and_logging_error(
                    r'备份点生成失败', '_restore_source_valid is false {} - {}'.format(
                        type(task_object), task_object.id), 1)

    @property
    def _set_status_display_and_check_cancel(self):
        return self._restore_target_object.display_status

    @_set_status_display_and_check_cancel.setter
    def _set_status_display_and_check_cancel(self, value):
        stage = ''
        if isinstance(value, tuple):
            status_str = value[0]
            stage = value[1]
        else:
            status_str = value
        self._restore_target_object.display_status = status_str
        log_vnc_port = None
        if status_str[0:10] == '为目标客户端配置硬件':
            log_vnc_port = status_str
            status_str = status_str[0:10]
            self._restore_target_object.display_status = status_str
        self._restore_target_object.save(update_fields=['display_status'])

        task_object = self.get_task_object()
        if isinstance(task_object, RestoreTask):
            task_type = 'restore_task'
            restore_target = task_object.restore_target
            if is_restore_target_belong_htb(self._restore_target_object):
                log_type = HostLog.LOG_HTB
            else:
                log_type = HostLog.LOG_RESTORE_START
            info = json.loads(restore_target.info)
            remote_ip = info['remote_ip']

            log_reason = {'description': r'还原到 ' + remote_ip + ':' + status_str,
                          'stage': stage,
                          'task_type': task_type,
                          task_type: task_object.id}

            if log_vnc_port:
                log_reason['vnc'] = log_vnc_port

            HostLog.objects.create(host=task_object.host_snapshot.host, type=log_type,
                                   reason=json.dumps(log_reason))
        if isinstance(task_object, MigrateTask):
            task_type = 'migrate_task'
            restore_target = task_object.restore_target
            info = json.loads(restore_target.info)
            remote_ip = info['remote_ip']  # 得到目标机ip
            HostLog.objects.create(host=task_object.host_snapshot.host, type=HostLog.LOG_MIGRATE_START,
                                   reason=json.dumps({
                                       'description': r'迁移到 ' + remote_ip + ':' + status_str,
                                       'stage': stage,
                                       'task_type': task_type,
                                       task_type: task_object.id
                                   }))

    def get_task_object(self):
        try:
            restore_task = self._restore_target_object.restore
            return RestoreTask.objects.get(id=restore_task.id)
        except RestoreTask.DoesNotExist:
            try:
                migrate_task = self._restore_target_object.migrate
                return MigrateTask.objects.get(id=migrate_task.id)
            except MigrateTask.DoesNotExist:
                return None  # 还原或迁移：锁定快照前

    # data PeHostSessionRestoreSerializer 反序列化的字典对象
    def init(self, data):
        host_snapshot = HostSnapshot.objects.get(id=data['host_snapshot_id'])
        ext_info = json.loads(host_snapshot.ext_info)
        system_infos = ext_info['system_infos']
        system = system_infos['System']
        sys_os_type = system['SystemCatName']  # 10_X64
        sys_os_bit = system['ProcessorArch']  # 64
        sys_os_class_type = 'linux' if 'LINUX' in (system['SystemCaption'].upper()) else 'windows'

        self._set_status_display_and_check_cancel = (r'查询快照点状态', '暂时不定字符串')
        _is_cancel_task(self.get_task_object())
        params = self._generate_params(data)
        self._set_status_display_and_check_cancel = (r'检查硬件信息', '暂时不定字符串')
        _is_cancel_task(self.get_task_object())
        hardwares = self.generate_hardwares(sys_os_class_type == 'linux')
        self._set_status_display_and_check_cancel = (r'检查网络配置', '暂时不定字符串')
        _is_cancel_task(self.get_task_object())
        ipconfigs = self._generate_ipconfigs(data)
        self._set_status_display_and_check_cancel = (r'检查虚拟化硬件信息', '暂时不定字符串')
        _is_cancel_task(self.get_task_object())
        kvm_virtual_devices, no_pci_devices, kvm_virtual_device_hids, kvm_vbus_devices = \
            self._generate_kvm_virtual_devices(sys_os_class_type == 'linux')

        if sys_os_class_type == 'linux':
            self._update_linux_params(params, ext_info)
        else:
            params['linux_disk_index_info'] = None
            params['linux_storage'] = None
            params['linux_info'] = None
        agent_service_configure = self._generate_agent_service_configs(data, ipconfigs)
        agent_service_configure['replace_efi'] = data.get('replace_efi', False)
        disable_fast_boot = data.get('disable_fast_boot', False)
        efi_boot_entry = ext_info.get('efi_boot_entry', '')

        self._db_save(params['restore_files'], params['pe_restore_info'], params['restore_target_disk_objects'],
                      hardwares, ipconfigs, kvm_virtual_devices, no_pci_devices, kvm_virtual_device_hids,
                      params['boot_device_disk_snapshots'], params['boot_device_restore_target_disk_token'],
                      params['boot_device_disk_bytes'], params['data_devices'], params['boot_device_disk_is_gpt'],
                      params['boot_device_normal_snapshot_ident'], sys_os_type, sys_os_bit, kvm_vbus_devices,
                      data['drivers_ids'], agent_service_configure, sys_os_class_type, params['linux_disk_index_info'],
                      params['linux_storage'], params['linux_info'], params['ice_ex_vols'], disable_fast_boot,
                      data['restore_time'], data['remote_kvm_params'], efi_boot_entry, params['kvm_disk_params'])

    @staticmethod
    def _load_fake_hardware_ids():
        if os.path.exists('/sbin/aio/fakeid.txt'):
            with open('/sbin/aio/fakeid.txt') as f:
                return list(map(lambda x: x.strip(), f.readlines()))
        else:
            return []

    def _load_fake_hardware_ids_pci(self):
        if os.path.exists('/sbin/aio/f_pci.txt'):
            with open('/sbin/aio/f_pci.txt') as f:
                hid_list = list()
                load_ids = list(map(lambda x: x.strip(), f.readlines()))
                for hid in load_ids:
                    if self._check_fake_id_is_userful(hid):
                        hid_list.append(hid)
                return hid_list
        else:
            return []

    def delete_temp_files(self):
        if boxService.get_delete_temp_kvm_iso_switch():
            boxService.box_service.remove(self._pe_stage_iso_file_path)
            boxService.box_service.remove(self._pe_stage_iso_temp_folder)

    def _db_save(self, restore_files, pe_restore_info, restore_target_disk_objects, hardwares, ipconfigs,
                 kvm_virtual_devices, no_pci_devices, kvm_virtual_device_hids, boot_device_disk_snapshots,
                 boot_device_restore_target_disk_token, boot_device_disk_bytes, data_devices, boot_device_disk_is_gpt,
                 boot_device_normal_snapshot_ident, sys_os_type, sys_os_bit, kvm_vbus_devices, drivers_ids,
                 agent_service_configure, sys_os_class_type, linux_disk_index_info, linux_storage, linux_info,
                 ice_ex_vols, disable_fast_boot, restore_time, remote_kvm_params, efi_boot_entry, kvm_disk_params):
        self._restore_target_object.start_datetime = timezone.now()
        self._restore_target_object.token_expires = self._restore_target_object.start_datetime + datetime.timedelta(
            minutes=self._restore_target_object.expiry_minutes)

        self._pe_info['restore_files'] = [pyconv.convert2JSON(restore_file) for restore_file in restore_files]
        self._pe_info['pe_restore_info'] = pyconv.convert2JSON(pe_restore_info)
        self._pe_info['hardwares'] = [pyconv.convert2JSON(hardware) for hardware in hardwares]
        self._pe_info['ipconfigs'] = [pyconv.convert2JSON(ipconfig) for ipconfig in ipconfigs]
        self._pe_info['kvm_virtual_devices'] = kvm_virtual_devices
        self._pe_info['kvm_virtual_device_hids'] = kvm_virtual_device_hids
        self._pe_info['no_pci_devices'] = no_pci_devices
        self._pe_info['boot_device_disk_snapshots'] = [pyconv.convert2JSON(disk_snapshot) for disk_snapshot in
                                                       boot_device_disk_snapshots]
        self._pe_info['boot_device_restore_target_disk_token'] = boot_device_restore_target_disk_token
        self._pe_info['boot_device_disk_bytes'] = boot_device_disk_bytes
        self._pe_info['data_devices'] = data_devices
        self._pe_info['boot_device_disk_is_gpt'] = boot_device_disk_is_gpt
        self._pe_info['boot_device_normal_snapshot_ident'] = boot_device_normal_snapshot_ident
        self._pe_info['sys_os_type'] = sys_os_type
        self._pe_info['sys_os_bit'] = sys_os_bit
        self._pe_info['kvm_vbus_devices'] = kvm_vbus_devices
        self._pe_info['drivers_ids'] = drivers_ids
        self._pe_info['agent_service_configure'] = agent_service_configure
        self._pe_info['sys_os_class_type'] = sys_os_class_type
        self._pe_info['linux_disk_index_info'] = linux_disk_index_info
        self._pe_info['linux_storage'] = linux_storage
        self._pe_info['linux_info'] = linux_info
        self._pe_info['ice_ex_vols'] = ice_ex_vols
        self._pe_info['disable_fast_boot'] = disable_fast_boot
        self._pe_info['restore_time'] = restore_time.strftime('%Y-%m-%d %H:%M:%S')
        self._pe_info['remote_kvm_params'] = remote_kvm_params
        self._pe_info['efi_boot_entry'] = efi_boot_entry
        self._pe_info['kvm_disk_params'] = kvm_disk_params
        self._restore_target_object.info = json.dumps(self._pe_info)

        self._restore_target_object.save(update_fields=['start_datetime', 'token_expires', 'info'])

        for restore_target_disk_object in restore_target_disk_objects:
            restore_target_disk_object.pe_host = self._restore_target_object
            restore_target_disk_object.save()

    @staticmethod
    def get_disk_snapshot_object(disk_snapshot_ident, restore_timestamp, debug_msg):
        try:
            disk_snapshot_object = DiskSnapshot.objects.get(ident=disk_snapshot_ident)

            validator_list = [GetSnapshotList.is_disk_snapshot_object_exist,
                              GetSnapshotList.is_disk_snapshot_file_exist]
            disk_snapshots = GetSnapshotList.query_snapshots_by_snapshot_object(
                disk_snapshot_object, validator_list, restore_timestamp)
            if len(disk_snapshots) == 0:
                xlogging.raise_and_logging_error(r'还原参数无效，无法访问快照文件，请检查存储节点连接状态',
                                                 r'{} arguments disk_snapshot_object invalid : {}'.format(
                                                     debug_msg, disk_snapshot_ident))

            return disk_snapshot_object, disk_snapshots
        except DiskSnapshot.DoesNotExist:
            xlogging.raise_and_logging_error(r'还原参数无效，无效的磁盘快照标识号',
                                             r'{} arguments disk_snapshot_ident invalid : {}'.format(
                                                 debug_msg, disk_snapshot_ident))

    @staticmethod
    def _update_linux_params(params, ext_info):
        params['linux_disk_index_info'] = ext_info['disk_index_info']
        system_infos = ext_info['system_infos']
        params['linux_storage'] = system_infos['Storage']
        params['linux_info'] = system_infos['Linux']

        find_first_partition_bytes_offset = False
        for disk_index_info in params['linux_disk_index_info']:
            if not disk_index_info['boot_device']:
                continue
            for disk_info in system_infos['Storage']['disks']:
                if disk_info['index'] != disk_index_info['snapshot_disk_index']:
                    continue
                for partition in disk_info['partitions']:
                    if ('first_partition_bytes_offset' in disk_index_info.keys()) \
                            and (int(disk_index_info['first_partition_bytes_offset']) <= int(partition['bytesStart'])):
                        continue
                    disk_index_info['first_partition_bytes_offset'] = partition['bytesStart']
                    find_first_partition_bytes_offset = True
            break
        if not find_first_partition_bytes_offset:
            xlogging.raise_and_logging_error(
                r'内部异常，无法获取启动磁盘信息',
                '_update_linux_params failed linux_disk_index_info : {} linux_storage : {}'.format(
                    params['linux_disk_index_info'], params['linux_storage']), 1)

    @staticmethod
    def _get_normal_disk_snapshot_object(disk_snapshot_object):
        if disk_snapshot_object.ident in (xdata.CLW_BOOT_REDIRECT_MBR_UUID, xdata.CLW_BOOT_REDIRECT_GPT_UUID,
                                          xdata.CLW_BOOT_REDIRECT_GPT_LINUX_UUID,):
            return disk_snapshot_object
        for_debug = disk_snapshot_object
        while disk_snapshot_object:
            if disk_snapshot_object.host_snapshot is not None:
                return disk_snapshot_object
            else:
                disk_snapshot_object = disk_snapshot_object.parent_snapshot
        xlogging.raise_and_logging_error(r'内部异常，代码2340',
                                         r'_get_normal_disk_snapshot_object failed, {}'.format(for_debug))

    def _generate_params(self, data):
        # box_api.ice restore 接口使用
        restore_files = list()

        try:
            agent_connect_ip = self._pe_info['more_info']['ConnectAddress']['RemoteAddress']
        except (KeyError, TypeError):
            agent_connect_ip = self._pe_info['local_ip']

        pe_restore_info = PerpcIce.PeRestoreInfo(
            boxService.get_tcp_kernel_service_ip(self._pe_info['local_ip'], agent_connect_ip),
            boxService.get_tcp_kernel_service_port(),
            boxService.get_tcp_kernel_service_restore_socket_number(), -1, [])
        # RestoreTargetDisk 数据库对象列表
        restore_target_disk_objects = list()

        # 启动kvm时使用
        data_devices = list()

        disk_ident_2_ex_vols_maps = self._get_disk2_ex_vols_maps(data)
        # box_api.ice restore 接口使用
        ice_ex_vols = list()

        boot_device_disk_snapshots = None
        boot_device_restore_target_disk_token = None
        boot_device_disk_bytes = None
        boot_device_disk_is_gpt = False
        boot_device_normal_snapshot_ident = None
        boot_device_disk_index = -1

        target_disk_index_array = list()
        kvm_disk_params = list()
        for disk in data['disks']:
            restore_timestamp = disk['restore_timestamp'] if data['type'] == xdata.SNAPSHOT_TYPE_CDP else None
            if disk['disk_snapshot_ident'] in (xdata.CLW_BOOT_REDIRECT_MBR_UUID, xdata.CLW_BOOT_REDIRECT_GPT_UUID,
                                               xdata.CLW_BOOT_REDIRECT_GPT_LINUX_UUID,):
                disk_snapshot_object = DiskSnapshot.objects.get(ident=disk['disk_snapshot_ident'])
                disk_snapshots = [IMG.ImageSnapshotIdent(disk_snapshot_object.image_path, disk_snapshot_object.ident)]
            else:
                disk_snapshot_object, disk_snapshots = self.get_disk_snapshot_object(
                    disk['disk_snapshot_ident'], restore_timestamp, 'full restore {}'.format(self._pe_ident))

            restore_files.append(Box.RestoreFile(disk['disk_index'], disk_snapshot_object.bytes, disk_snapshots))

            restore_target_disk_token = uuid.uuid4().hex.lower()

            if boxService.box_service.isFileExist(xdata.ENABLE_RESTORE_OPTIMIZE):
                restore_optimize_parameter = self._get_restore_optimize_parameter(disk_snapshots, disk_snapshot_object,
                                                                                  restore_target_disk_token)
                self._restore_optimize_parameters.append(restore_optimize_parameter)

            boot_device, is_system, is_bmf = self._get_disk_detail(data['host_snapshot_id'], disk_snapshot_object)
            _logger.info(
                '_generate_params disk_index:{} boot_device:{}, is_system:{}, is_bmf:{}'.format(disk['disk_index'],
                                                                                                boot_device,
                                                                                                is_system,
                                                                                                is_bmf))

            if disk['disk_index'] in target_disk_index_array:
                xlogging.raise_and_logging_error(r'还原参数无效，重复的目标磁盘',
                                                 r'duplicate target disk, pe : {}'.format(self._pe_ident))
            target_disk_index_array.append(disk['disk_index'])

            # dwOsDiskID 等价于 is_bmf(bmf文件所在的磁盘)
            if is_bmf:
                pe_restore_info.dwOsDiskID = disk['disk_index']

            if boot_device:
                boot_device_disk_index = disk['disk_index']
                boot_device_disk_snapshots = disk_snapshots
                boot_device_restore_target_disk_token = restore_target_disk_token
                boot_device_disk_bytes = disk_snapshot_object.bytes
                boot_device_disk_is_gpt = disk_snapshot_object.type == DiskSnapshot.DISK_GPT
                boot_device_normal_snapshot_ident = self._get_normal_disk_snapshot_object(disk_snapshot_object).ident
            else:
                data_devices.append({'index': disk['disk_index'], 'token': restore_target_disk_token,
                                     'disk_bytes': disk_snapshot_object.bytes,
                                     'normal_snapshot_ident':
                                         self._get_normal_disk_snapshot_object(disk_snapshot_object).ident})

            disk_ident = disk_snapshot_object.disk.ident  # 磁盘的唯一编码
            pe_restore_info.tokens.append(
                PerpcIce.PeDiskToken(disk['disk_index'], restore_target_disk_token, disk_ident))

            restore_target_disk_objects.append(
                RestoreTargetDisk(token=restore_target_disk_token, snapshot=disk_snapshot_object,
                                  snapshot_timestamp=restore_timestamp))

            if disk_ident in disk_ident_2_ex_vols_maps:
                for ex_range in disk_ident_2_ex_vols_maps[disk_ident]:
                    ex_range['token'] = restore_target_disk_token
                    ice_ex_vols.append(ex_range)

            dst_disk_size = self._get_target_disk_byte_size(disk['disk_index'])
            if disk_snapshot_object.bytes > dst_disk_size:
                _logger.info(
                    '_generate_params big disk:{} to small disk:{}'.format(disk_snapshot_object.bytes, dst_disk_size))
                ice_ex_vols.append({'token': restore_target_disk_token,
                                    'sectorStart': str(int(dst_disk_size // 512)),
                                    'sectorEnd': str(int(disk_snapshot_object.bytes // 512))})
            else:
                pass
            kvm_disk_params.append({"disk_ident": disk_snapshot_object.disk.ident,
                                    "boot_device": disk_snapshot_object.boot_device,
                                    "device_profile": {"nbd": dict()},
                                    "disk_snapshot_ident": disk_snapshot_object.ident})
        if not boot_device_disk_snapshots:
            xlogging.raise_and_logging_error(r'还原参数无效，没有启动磁盘',
                                             r'there is no boot disk, pe : {}'.format(self._pe_ident))

        # 找不到bmf 盘就使用，boot磁盘
        if pe_restore_info.dwOsDiskID == -1:
            _logger.warning('not found bmf disk, use boot disk:{} as bmf disk'.format(boot_device_disk_index))
            pe_restore_info.dwOsDiskID = boot_device_disk_index

        return {'restore_files': restore_files, 'pe_restore_info': pe_restore_info,
                'restore_target_disk_objects': restore_target_disk_objects,
                'boot_device_disk_snapshots': boot_device_disk_snapshots,
                'boot_device_restore_target_disk_token': boot_device_restore_target_disk_token,
                'boot_device_disk_bytes': boot_device_disk_bytes, 'data_devices': data_devices,
                'boot_device_disk_is_gpt': boot_device_disk_is_gpt,
                'boot_device_normal_snapshot_ident': boot_device_normal_snapshot_ident,
                'ice_ex_vols': ice_ex_vols,
                'kvm_disk_params': kvm_disk_params}

    @staticmethod
    def _get_disk2_ex_vols_maps(data):
        exclude_volumes = data.get('exclude_volumes', list())
        rs = dict()
        for vol in exclude_volumes:
            for vol_range in vol['ranges']:
                disk_ident = vol_range['disk_ident']
                sector_end = str(int(vol_range['sector_offset']) + int(vol_range['sectors']))
                vol_info = {'token': '', 'sectorStart': vol_range['sector_offset'], 'sectorEnd': sector_end}
                if disk_ident not in rs:
                    rs[disk_ident] = [vol_info]
                else:
                    rs[disk_ident].append(vol_info)
        return rs

    def _generate_ipconfigs(self, data):
        # logic.ice generatePeStageIso 接口使用
        ipconfigs = list()
        adapters = data['adapters']
        for adapter in adapters:
            ipconfig = self._convert_adapter_to_BoxLogic_IPConfig(adapter)
            if ipconfig is None:
                continue
            ipconfigs.append(ipconfig)
        return ipconfigs

    def _convert_adapter_to_BoxLogic_IPConfig(self, adapter):
        network_adapter = self._get_network_adapter(adapter['adapter'])

        for network_controller_hardware_stack in self._pe_info['net_ctr_stacks']:
            network_controller_hardware = network_controller_hardware_stack[0]
            if network_controller_hardware['szDeviceInstanceID'].upper() != \
                    network_adapter['szDeviceInstanceID'].upper():
                continue

            hardwareConfig = list()
            find_pci = False

            for network_controller_hardware in network_controller_hardware_stack:
                if self.is_qemu_hardware(network_controller_hardware):
                    break
                # elif self.is_pci_service(network_controller_hardware): 配置网卡时需要传递所有 PCI hardware
                #     break
                elif self.is_pci_hardware(network_controller_hardware):
                    assert not PeRestore.is_net_adapter_and_not_ethernet(network_controller_hardware)
                    find_pci = True
                elif find_pci:
                    break
                else:
                    pass

                hardwareConfig.append({'HardwareID': network_controller_hardware['HWIds'],
                                       'LocationInformation': network_controller_hardware['szLocationInfo'],
                                       'UINumber': network_controller_hardware['UINumber'],
                                       'Address': network_controller_hardware['Address'],
                                       'ContainerID': network_controller_hardware['szContainerID'],
                                       'NameGUID': network_adapter['szGuid'] if len(hardwareConfig) == 0 else None,
                                       'Service': network_controller_hardware['szService'].upper(),
                                       'Mac': network_adapter['szMacAddress'],
                                       'szDeviceInstanceID': network_controller_hardware['szDeviceInstanceID']
                                       })

            adapter['multi_infos'] = self._inject_mtu_to_adapter(adapter['multi_infos'])

            return BoxLogic.IPConfig(adapter['ip'], adapter['subnet_mask'], adapter['routers'], adapter['dns'],
                                     adapter['multi_infos'], json.dumps(hardwareConfig))

        if adapter['ip'] == '0.0.0.0':
            # 没有配额网络信息，业务层面就是将这张网卡设置为DHCP或不改动
            # 如果找不到对应硬件信息，那么忽略该配置的风险是很小的
            return None

        xlogging.raise_and_logging_error(r'内部错误，无效的网络适配器配置',
                                         r'{} _convert_adapter_to_BoxLogic_IPConfig invalid : {}'.format(self._pe_ident,
                                                                                                         adapter))

    @staticmethod
    def _inject_mtu_to_adapter(multi_infos_str):
        mtu = int(GetDictionary(DataDictionary.DICT_TYPE_AIO_NETWORK, 'aio_mtu', -1))
        if mtu == -1:
            return multi_infos_str
        multi_infos = json.loads(multi_infos_str)
        multi_infos.setdefault('mtu', mtu)
        return json.dumps(multi_infos)

    def _get_network_adapter(self, adapter_guid):
        for network_adapter in self._pe_info['net_adapters']:
            if network_adapter['szGuid'] == adapter_guid:
                return network_adapter
        xlogging.raise_and_logging_error(r'内部错误，无效的网络适配器标识号',
                                         r'{} _get_network_adapter invalid : {}'.format(self._pe_ident, adapter_guid))

    @staticmethod
    def all_network_adapter_and_check(destserverid, inc_no_pci=False):
        restore_target_object = RestoreTarget.objects.get(ident=destserverid)
        net_adapters = json.loads(restore_target_object.info).get('net_adapters')
        if not net_adapters:
            return {'r': 1, 'e': 'restore_target_object info not find net_adapters'}
        # for network_adapter in net_adapters:
        #     if network_adapter['isConnected']: # isConnected 主网卡字段
        #         current_net_adapter = network_adapter
        result_adapter_dict = dict()  # {'szMacAddress':{'此网卡是否可用':true,'isConnected':是否主网卡}}
        for adapters in net_adapters:
            result, msg = PeRestore.check_network_adapter_support(restore_target_object, adapters, inc_no_pci)
            result_adapter_dict[adapters.get('szMacAddress', None)] = {'is_valid': result,
                                                                       'isConnected': adapters['isConnected'],
                                                                       'msg': msg}

        return result_adapter_dict

    @staticmethod
    def check_network_adapter_support(restore_target_object, adapters, inc_no_pci):
        """
        检测网卡是否有效
        :param restore_target_object: obj
        :return:
        """
        adapters_info = json.loads(restore_target_object.info)
        net_adapters = adapters_info['net_adapters']
        current_net_adapter = adapters
        for network_controller_hardware_stack in adapters_info['net_ctr_stacks']:
            network_controller_hardware = network_controller_hardware_stack[0]
            if network_controller_hardware['szDeviceInstanceID'].upper() != \
                    current_net_adapter['szDeviceInstanceID'].upper():
                continue

            for network_controller_hardware in network_controller_hardware_stack:
                if PeRestore.is_qemu_hardware(network_controller_hardware):
                    _logger.warn(r'check_network_adapter_support not support dummy device : {}'
                                 .format(adapters_info['net_ctr_stacks']))
                    return False, '不允许的虚拟化环境'  # 不支持还原到科力锐的虚拟化环境
                elif PeRestore.is_usb_hardware(network_controller_hardware):
                    _logger.warn(r'check_network_adapter_support not support USB device : {}'
                                 .format(adapters_info['net_ctr_stacks']))
                    return False, 'USB网卡'  # 不支持USB网卡
                # elif self.is_pci_service(network_controller_hardware): 配置网卡时需要传递所有 PCI hardware
                #     break
                elif PeRestore.is_pci_hardware(network_controller_hardware):
                    if PeRestore.is_net_adapter_and_not_ethernet(network_controller_hardware):
                        _logger.warn(r'check_network_adapter_support not support not_ethernet device : {}'
                                     .format(adapters_info['net_ctr_stacks']))
                        return False, 'Wi-Fi网卡'  # 不支持非以太网适配器，比如WIFI等
                    else:
                        return True, None

                if inc_no_pci:  # 源是linux的情况下，不需要有pci设备
                    if PeRestore.is_xen_hardware(network_controller_hardware):
                        return True, None
                    elif PeRestore.is_hype_v_hardware(network_controller_hardware):
                        return True, None
                    else:
                        pass

            _logger.warn(r'check_network_adapter_support find unknown device : {}'
                         .format(adapters_info['net_ctr_stacks']))
            return False, '虚拟网卡'

        _logger.warn(r'check_network_adapter_support NOT find device : {} {}'
                     .format(current_net_adapter, adapters_info['net_ctr_stacks']))
        return False, '虚拟网卡'

    @staticmethod
    def _convert_hex_string_to_dex_string(hex_string):
        nunber = int(hex_string, 16)
        return r'{:d}'.format(nunber)

    def _find_hardware_id_part(self, finder, hid):
        part = re.search(finder[0], hid)
        if part is None:
            _logger.warning(
                r'{} _find_hardware_id_part failed : {} - {} - {}'.format(self._pe_ident, finder[0], finder[1], hid))
            return None
        else:
            _logger.debug(r'{} _find_hardware_id_part ok : {} - {} - {} - {}'.format(
                self._pe_ident, finder[0], finder[1], finder[2], hid))
            return part.group()[finder[1]:finder[2]]

    @staticmethod
    def _s_find_hardware_id_part(finder, hid):
        part = re.search(finder[0], hid)
        if part is not None:
            _logger.debug(r'{} _s_find_hardware_id_part ok : {} - {} - {}'.format(finder[0], finder[1], finder[2], hid))
            return part.group()[finder[1]:finder[2]]
        else:
            return None

    # @staticmethod
    # def _is_ide_controller(class_id):
    #     return class_id == 0x0101

    @staticmethod
    def is_net_adapter_and_not_ethernet(hardware):
        hids = hardware['HWIds']

        for hid in hids:
            cc = PeRestore._s_find_hardware_id_part(CLASS_ID_FINDER, hid)
            if cc is None:
                continue

            return cc.startswith('02') and cc != '0200'
        else:
            _logger.warning(r'is_net_adapter_and_not_ethernet no cc {}'.format(hids))
            return False

    @staticmethod
    def is_pci_hardware(hardware):
        return hardware['szDeviceInstanceID'].upper().startswith('PCI\\')

    @staticmethod
    def is_usb_hardware(hardware):
        return hardware['szDeviceInstanceID'].upper().startswith('USB\\')

    @staticmethod
    def is_qemu_hardware(hardware):
        qemu_hardwares = [r'PCI\VEN_8086&DEV_7000', r'PCI\VEN_8086&DEV_1237', r'PCI\VEN_1234&DEV_1111']
        for qemu_hardware in qemu_hardwares:
            if hardware['szDeviceInstanceID'].upper().startswith(qemu_hardware):
                return True
        return False

    @staticmethod
    def is_pci_service(hardware):
        return hardware['szService'].upper() == r'PCI'

    @staticmethod
    def is_vwifimp_service(hardware):
        return hardware['szService'].upper() == r'VWIFIMP'

    @staticmethod
    def is_usb_hub_service(hardware):
        return hardware['szService'].upper() == r'USBHUB'

    @staticmethod
    def is_xen_hardware(hardware):
        return ('VIF' in hardware['szDeviceInstanceID'].upper()) or ('VBD' in hardware['szDeviceInstanceID'].upper())

    @staticmethod
    def is_hype_v_hardware(hardware):
        return 'VMBS' in hardware['szDeviceInstanceID'].upper()

    def _generate_kvm_virtual_device_with_pci(self, hardware, kvm_virtual_devices, kvm_virtual_device_hids):
        hids = hardware['HWIds']
        hardware_cc = None

        for hid in hids:
            if self._find_hardware_id_part(CLASS_ID_FINDER, hid) is not None:
                hardware_cc = hid
                break
        if hardware_cc is None:
            xlogging.raise_and_logging_error(r'硬件信息中没有Class信息',
                                             r'_generate_kvm_virtual_device no cc {}'.format(hids))

        hardware_id = None
        for hid in hids:
            if self._find_hardware_id_part(REVISION_FINDER, hid) is not None:
                hardware_id = hid
                break
        if hardware_id is None:
            xlogging.raise_and_logging_error(r'硬件信息中没有Revision信息',
                                             r'_generate_kvm_virtual_device no rev {}'.format(hids))

        class_id_str = self._find_hardware_id_part(CLASS_ID_FINDER, hardware_cc)
        kvm_virtual_device = r'pci-vdev,ven_id={ven_id:d},dev_id={dev_id:d},subsys_ven_id={subsys_ven_id:d},' \
                             r'subsys_id={subsys_id:d},revision={revision:d},' \
                             r'class_id={class_id:d},interface={interface:d}' \
            .format(ven_id=int(self._find_hardware_id_part(VID_FINDER, hardware_id), 16),
                    dev_id=int(self._find_hardware_id_part(DEV_ID_FINDER, hardware_id), 16),
                    subsys_ven_id=int(self._find_hardware_id_part(SUBSYS_VEN_ID_FINDER, hardware_id), 16),
                    subsys_id=int(self._find_hardware_id_part(SUBSYS_ID_FINDER, hardware_id), 16),
                    revision=int(self._find_hardware_id_part(REVISION_FINDER, hardware_id), 16),
                    class_id=int(class_id_str, 16),
                    interface=int(self._find_hardware_id_part(INTERFACE_FINDER, hardware_cc), 16),
                    )

        # 非本地的pci设备，就保留一个
        if class_id_str.upper().startswith('0C'):
            if kvm_virtual_device not in kvm_virtual_devices:
                kvm_virtual_devices.append(kvm_virtual_device)
            else:
                _logger.warning(
                    '_generate_kvm_virtual_device_with_pci find no local pci, filter kvm_virtual_device:{}'.format(
                        kvm_virtual_device))
            if hardware_id not in kvm_virtual_device_hids:
                kvm_virtual_device_hids.append(hardware_id)
            else:
                _logger.warning('_generate_kvm_virtual_device_with_pci find no local pci, filter hardware_id:{}'.format(
                    hardware_id
                ))
        else:
            kvm_virtual_devices.append(kvm_virtual_device)
            kvm_virtual_device_hids.append(hardware_id)

    @staticmethod
    def _hardware_filter(hwid):
        return hwid.upper() not in ['XENDEVICE', 'XENCLASS']

    @staticmethod
    def _generate_kvm_virtual_device_no_pci(current_hardware, no_pci_devices, kvm_virtual_device_hids):
        hardware_ids = [x for x in current_hardware['HWIds'] if PeRestore._hardware_filter(x)]
        hardware_compatible_ids = [x for x in current_hardware['CompatIds'] if PeRestore._hardware_filter(x)]
        if len(hardware_compatible_ids) == 0:
            hardware_compatible_ids = hardware_ids
        no_pci_devices.append([hardware_ids, hardware_compatible_ids])
        kvm_virtual_device_hids.append(hardware_ids[0])

    def _generate_kvm_virtual_device(
            self, hardware_stack, kvm_virtual_devices, no_pci_devices, kvm_virtual_device_hids, inc_no_pci):
        find_pci = False
        for current_hardware in hardware_stack:
            if self.is_qemu_hardware(current_hardware):
                return
            elif self.is_vwifimp_service(current_hardware):
                return
            elif self.is_pci_service(current_hardware):
                return
            elif self.is_usb_hub_service(current_hardware):
                return
            elif self.is_usb_hardware(current_hardware):
                return
            elif self.is_pci_hardware(current_hardware):
                if PeRestore.is_net_adapter_and_not_ethernet(current_hardware):
                    return
                self._generate_kvm_virtual_device_with_pci(current_hardware, kvm_virtual_devices,
                                                           kvm_virtual_device_hids)
                find_pci = True
            elif find_pci:  # 已经找到过PCI设备，那么就退出
                return
            else:  # 没有找到过PCI设备，说明是半虚拟化设备
                self._generate_kvm_virtual_device_no_pci(current_hardware, no_pci_devices, kvm_virtual_device_hids)

        if inc_no_pci:
            pass
        else:
            if not find_pci:
                xlogging.raise_and_logging_error(
                    r'虚拟设备的没有PCI类型的父设备',
                    r'{} _genrate_kvm_virtual_device_no_pci find_pci_device is false'.format(self._pe_ident))

    def _generate_kvm_virtual_device_by_fake_id(self, fhid, kvm_virtual_devices, kvm_virtual_device_hids):

        kvm_virtual_device = r'pci-vdev,ven_id={ven_id:d},dev_id={dev_id:d},subsys_ven_id={subsys_ven_id:d},' \
                             r'subsys_id={subsys_id:d},revision={revision:d},' \
                             r'class_id={class_id:d},interface={interface:d}' \
            .format(ven_id=int(self._find_hardware_id_part(VID_FINDER, fhid), 16),
                    dev_id=int(self._find_hardware_id_part(DEV_ID_FINDER, fhid), 16),
                    subsys_ven_id=int(self._find_hardware_id_part(SUBSYS_VEN_ID_FINDER, fhid), 16),
                    subsys_id=int(self._find_hardware_id_part(SUBSYS_ID_FINDER, fhid), 16),
                    revision=int(self._find_hardware_id_part(REVISION_FINDER, fhid), 16),
                    class_id=int(self._find_hardware_id_part(CLASS_ID_FINDER, fhid), 16),
                    interface=int(self._find_hardware_id_part(INTERFACE_FINDER, fhid), 16),
                    )

        kvm_virtual_devices.append(kvm_virtual_device)
        kvm_virtual_device_hids.append(fhid)

    def _generate_kvm_virtual_devices(self, inc_no_pci):
        kvm_virtual_devices = list()
        no_pci_devices = list()
        kvm_virtual_device_hids = list()

        if not inc_no_pci:  # windows还原需要过滤掉非pci设备
            self.pop_checked_stacks(self._pe_info['disk_ctr_stacks'], [self.is_no_pci_in_stack])
            self.pop_checked_stacks(self._pe_info['net_ctr_stacks'], [self.is_no_pci_in_stack])

        for disk_controller_hardware_stack in self._pe_info['disk_ctr_stacks']:
            self._generate_kvm_virtual_device(disk_controller_hardware_stack, kvm_virtual_devices, no_pci_devices,
                                              kvm_virtual_device_hids, inc_no_pci)
        for network_controller_hardware_stack in self._pe_info['net_ctr_stacks']:
            self._generate_kvm_virtual_device(network_controller_hardware_stack, kvm_virtual_devices, no_pci_devices,
                                              kvm_virtual_device_hids, inc_no_pci)
        for fake_id_pci in self._fake_hardware_ids_pci:
            self._generate_kvm_virtual_device_by_fake_id(fake_id_pci, kvm_virtual_devices, kvm_virtual_device_hids)

        self._fill_fake_hardware(no_pci_devices, kvm_virtual_device_hids)
        kvm_vbus_devices = self._generate_no_pci_kvm_virtual_device(no_pci_devices)
        return kvm_virtual_devices, no_pci_devices, kvm_virtual_device_hids, kvm_vbus_devices

    def _fill_fake_hardware(self, no_pci_devices, kvm_virtual_device_hids):
        for fake_hardware_id in self._fake_hardware_ids:
            no_pci_devices.append([[fake_hardware_id], [fake_hardware_id]])
            kvm_virtual_device_hids.append(fake_hardware_id)

    def _check_fake_id_is_userful(self, fhid):
        flags = True
        for checker in [VID_FINDER, DEV_ID_FINDER, SUBSYS_VEN_ID_FINDER, REVISION_FINDER, CLASS_ID_FINDER]:
            if self._find_hardware_id_part(checker, fhid) is None:
                _logger.error('_generate_kvm_virtual_device_by_fake_id fail, hid:{} ,checker:{}'.
                              format(fhid, checker[0]))
                flags = False
                break
        return flags

    @staticmethod
    def _generate_no_pci_kvm_virtual_device_ids_string(hardware_ids):
        result = ''

        for hardware_id in hardware_ids:
            if result != '':
                result += '\n'
            result += hardware_id.strip()
        return result

    @staticmethod
    def _generate_no_pci_kvm_virtual_device(no_pci_devices):
        if len(no_pci_devices) == 0:
            return ''

        kvm_vbus_devices = ''
        for hardware_ids in no_pci_devices:
            if kvm_vbus_devices != '':
                kvm_vbus_devices += r'||'

            kvm_vbus_devices += PeRestore._generate_no_pci_kvm_virtual_device_ids_string(hardware_ids[0])
            kvm_vbus_devices += r'|'
            kvm_vbus_devices += PeRestore._generate_no_pci_kvm_virtual_device_ids_string(hardware_ids[1])

        if len(kvm_vbus_devices) > (64 * 1024 - 2):
            xlogging.raise_and_logging_error(r'任务执行失败，虚拟化设备过多',
                                             r'_generate_no_pci_kvm_virtual_device failed. too many devices')

        return kvm_vbus_devices

    def _fill_hardwares(self, hardware_type, hardware_stack, hardwares, inc_no_pci):
        find_pci = False
        for hardware in hardware_stack:
            if self.is_qemu_hardware(hardware):
                return
            elif self.is_vwifimp_service(hardware):
                return
            elif self.is_pci_service(hardware):
                return
            elif self.is_usb_hub_service(hardware):
                return
            elif self.is_usb_hardware(hardware):
                return
            elif self.is_pci_hardware(hardware):
                if self.is_net_adapter_and_not_ethernet(hardware):
                    return
                find_pci = True
            elif inc_no_pci:
                if self.is_hype_v_hardware(hardware):
                    find_pci = True
                elif self.is_xen_hardware(hardware):
                    find_pci = True
            elif find_pci:
                return
            else:
                pass

            hardwares.append(self._convert_hardware_to_BoxLogic_Hardware(hardware_type, hardware))

        if not find_pci:
            xlogging.raise_and_logging_error(r'无效的设备栈信息',
                                             r'_generate_hardware never happened : {}'.format(hardware_stack))

    def generate_hardwares(self, inc_no_pci=False):
        # logic.ice generatePeStageIso 接口使用
        hardwares = list()
        if not inc_no_pci:  # windows还原需要过滤掉非pci设备
            self.pop_checked_stacks(self._pe_info['disk_ctr_stacks'], [self.is_no_pci_in_stack])
            self.pop_checked_stacks(self._pe_info['net_ctr_stacks'], [self.is_no_pci_in_stack])
        for disk_controller_hardware_stack in self._pe_info['disk_ctr_stacks']:
            self._fill_hardwares('disk', disk_controller_hardware_stack, hardwares, inc_no_pci)
        for network_controller_hardware_stack in self._pe_info['net_ctr_stacks']:
            self._fill_hardwares('net', network_controller_hardware_stack, hardwares, inc_no_pci)
        for fake_hardware_id in self._fake_hardware_ids:
            hardwares.append(BoxLogic.Hardware('net', '8086', [fake_hardware_id], [fake_hardware_id]))
        for fake_hardware_id_pci in self._fake_hardware_ids_pci:
            hardwares.append(BoxLogic.Hardware('net', '8086', [fake_hardware_id_pci], [fake_hardware_id_pci]))
        return hardwares

    @staticmethod
    def pop_checked_stacks(stacks, checkers):
        checked_stacks = list()
        for stack in stacks:
            for checker in checkers:
                if checker(stack):
                    checked_stacks.append(stack)
                    break  # deal next stack
        for checked_stack in checked_stacks:
            stacks.remove(checked_stack)
        return checked_stacks

    @staticmethod
    def is_empty_hardware_id_or_compat_id(stack):
        hardware = stack[0]
        return (len(hardware['HWIds']) == 0) or (len(hardware['CompatIds']) == 0)

    @staticmethod
    def no_pci_and_no_xen_hypev(stack):
        for hardware in stack:
            if PeRestore.is_hype_v_hardware(hardware):
                break
            elif PeRestore.is_xen_hardware(hardware):
                break
            if hardware['szDeviceInstanceID'].startswith('PCI\\'):
                break
        else:
            return True
        return False

    @staticmethod
    def is_pci_ide_hardware(stack):
        hardware = stack[0]
        return hardware['szDeviceInstanceID'].startswith('PCIIDE\\')

    @staticmethod
    def is_no_pci_in_stack(stack):
        for hardware in stack:
            if hardware['szDeviceInstanceID'].startswith('PCI\\'):
                return False
        return True

    @staticmethod
    def is_invalid_pci_instance(stack):
        for hardware in stack:
            if hardware['szDeviceInstanceID'].startswith('PCI\\') and (
                    not hardware['szDeviceInstanceID'].startswith('PCI\\VEN_')):
                return True
        return False

    @staticmethod
    @xlogging.convert_exception_to_value(False)
    def _check_hardware_driver_exist(hardware):
        return boxService.box_service.isHardwareDriverExist(hardware)

    def _convert_hardware_to_BoxLogic_Hardware(self, hardware_type, hardware):
        vid = self._find_hardware_id_part(VID_FINDER, hardware['HWIds'][0])
        if vid is None:
            _logger.warning(r'no vid: {} type: {} {}'.format(self._pe_ident, hardware_type, hardware))
            vid = '0000'
        _hardware = BoxLogic.Hardware(hardware_type, vid, hardware['HWIds'], hardware['CompatIds'])
        _hardware.szDescription = hardware.get('szDescription', hardware['HWIds'][0])
        return _hardware

    def _get_restore_config(self):
        restore_config = dict()

        pci_bus_device_ids = self._pe_info['no_pci_devices']

        if len(pci_bus_device_ids) > 0:
            restore_config['pci_bus_device_ids'] = ['not empty']
        else:
            restore_config['pci_bus_device_ids'] = list()

        restore_config['os_type'] = self._pe_info['sys_os_type']
        restore_config['os_bit'] = self._pe_info['sys_os_bit']

        choice_drivers_versions = self._pe_info['drivers_ids']
        if isinstance(choice_drivers_versions, dict):
            choice_drivers_versions = json.dumps(choice_drivers_versions)
        restore_config['choice_drivers_versions'] = choice_drivers_versions

        agent_service_configure = self._pe_info['agent_service_configure']
        agent_service_configure['soft_ident'] = self._pe_info['soft_ident']
        restore_config['agent_service_configure'] = json.dumps(agent_service_configure)

        return restore_config

    # 不要有数据库的回写，可能会把 'kvm_start' 字段写没
    def _generate_pe_stage_iso(self):
        restore_config = self._get_restore_config()

        BoxLogic.Hardware.HWIds = [str]
        BoxLogic.Hardware.CompatIds = [str]
        hardwares = [pyconv.convertJSON2OBJ(BoxLogic.Hardware, hardware) for hardware in self._pe_info['hardwares']]

        self._modify_hardwares_and_choice_version(hardwares, restore_config['choice_drivers_versions'])

        ipconfigs = [pyconv.convertJSON2OBJ(BoxLogic.IPConfig, ipconfig) for ipconfig in
                     self._altered_info['ipconfigs']]

        boxService.box_service.generatePeStageIso(self._pe_stage_iso_temp_folder, self._pe_stage_iso_file_path,
                                                  self._altered_info['hardwares'], ipconfigs,
                                                  self._altered_info['pci_bus_device_ids'], restore_config['os_type'],
                                                  restore_config['os_bit'],
                                                  self._altered_info['choice_drivers_versions'],
                                                  restore_config['agent_service_configure'])

    def _get_pe_restore_info_from_db(self):
        PerpcIce.PeRestoreInfo.tokens = [PerpcIce.PeDiskToken]
        pe_restore_info = pyconv.convertJSON2OBJ(PerpcIce.PeRestoreInfo, self._pe_info['pe_restore_info'])
        return pe_restore_info

    def _generate_agent_service_configs(self, data, ipconfigs):
        try:
            agent_connect_ip = self._pe_info['more_info']['ConnectAddress']['RemoteAddress']
        except (KeyError, TypeError):
            agent_connect_ip = self._pe_info['local_ip']

        user_info = data['agent_user_info']
        aio_ip = boxService.get_agent_service_config_host(self._pe_info['local_ip'], agent_connect_ip)
        htb_task_uuid = data.get('htb_task_uuid', None)
        tunnel_ip, tunnel_port = self._get_tunnel_ip_port(self._pe_info['tunnel_index'])
        if tunnel_ip != -1:
            tunnel_ip = '0.0.0.0'
        else:
            pass
        routers = data['routers'] if isinstance(data['routers'], dict) else json.loads(data['routers'])
        return {"user_info": user_info, "aio_ip": aio_ip, "routers": routers, 'tunnel_ip': tunnel_ip,
                'tunnel_port': tunnel_port, 'htb_task_uuid': htb_task_uuid,
                'restore_target': self._restore_target_object.ident}

    # 隧道模式下 需要根据主网卡的IP 修正tunnel_ip, tunnel_port
    def _modify_ip_port_by_master_ip(self, tunnel_ip, tunnel_port, ipconfigs):
        ip_config = self._get_master_ipconfigs(ipconfigs)
        if ip_config.ipAddress == tunnel_ip:
            return tunnel_ip, tunnel_port
        else:
            _logger.warning('_modify_ip_port_by_master_ip master ip:{} != tunnel ip:{}, use 0.0.0.0 3345'.format(
                ip_config.ipAddress, tunnel_ip))
            return '0.0.0.0', '3345'

    def _get_master_ipconfigs(self, ipconfigs):
        for ipconfig in ipconfigs:
            if json.loads(ipconfig.multiInfos)['target_nic']['isConnected']:
                return ipconfig
        xlogging.raise_and_logging_error('获取关键网卡信息失败', 'get master apapter info fail', 13)

    def _send_cmd_to_pe_host(self):
        pe_restore_info = self._get_pe_restore_info_from_db()
        restore_files = self._get_restore_files()
        ex_vols = self._pe_info['ice_ex_vols']
        disable_fast_boot = self._pe_info['disable_fast_boot']
        efi_boot_entry = self._get_efi_boot_entry()
        json_config = {'floppyPath': self._floppy_path, 'excludes': ex_vols, 'disable_fast_boot': disable_fast_boot,
                       'efi_boot_entry': efi_boot_entry}
        boxService.box_service.restore(self._restore_target_object.ident, pe_restore_info, restore_files,
                                       json.dumps(json_config))

    def _get_efi_boot_entry(self):
        db_efi_boot_entry = self._pe_info['efi_boot_entry']
        uncompress_data = gzip.decompress(base64.b64decode(db_efi_boot_entry.encode()))
        return base64.b64encode(uncompress_data).decode()

    def _get_restore_files(self):
        Box.RestoreFile.snapshot = [IMG.ImageSnapshotIdent]
        restore_files = [pyconv.convertJSON2OBJ(Box.RestoreFile, restore_file) for restore_file in
                         self._pe_info['restore_files']]
        return restore_files

    def _check_pe_host_linked(self):
        pe_ident = self._restore_target_object.ident
        if not boxService.box_service.isPeHostLinked(pe_ident):
            xlogging.raise_and_logging_error(r'目标客户端离线', '{} not linked'.format(pe_ident))

    def _is_restore_windows(self):
        sys_os_class_type = self._pe_info['sys_os_class_type']
        return sys_os_class_type == 'windows'

    @xlogging.convert_exception_to_value(None)
    def _get_nbd_device(self, restore_target_ident):
        p = psutil.process_iter()
        for r in p:
            if r.name().strip().lower() in ('gznbd',):
                cmdline = r.cmdline()
                if len(cmdline) > 6:
                    if cmdline[0].lower() == '/sbin/aio/gznbd':
                        if cmdline[3].lower() == restore_target_ident.lower():
                            return cmdline[2]
        return None

    @xlogging.convert_exception_to_value(None)
    def _get_kvm_cmd_line(self, nbd_device):
        p = psutil.process_iter()
        for r in p:
            if r.name().strip().lower() in ('qemu-kvm', 'qemu-system-x86_64',):
                for line in r.cmdline():
                    if nbd_device in line:
                        return r.cmdline()

        return None

    @xlogging.convert_exception_to_value(None)
    def _get_vnc_port(self, restore_target_ident):
        nbd_device = self._get_nbd_device(restore_target_ident)
        if nbd_device:
            kvm_cmd_line = self._get_kvm_cmd_line(nbd_device)
            if kvm_cmd_line:
                i = 0
                for line in kvm_cmd_line:
                    i = i + 1
                    if line == '-vnc':
                        return int(kvm_cmd_line[i].split(':')[1])
        return None

    def _get_kvm_port(self):
        vnc_port = None
        i = 0
        while True:
            vnc_port = self._get_vnc_port(self._restore_target_object.ident)
            if not vnc_port:
                i = i + 1
                if i > 15:
                    break
                time.sleep(1)
                continue
            break
        if vnc_port:
            status_desc = r'为目标客户端配置硬件，分配资源{}'.format(vnc_port)
        else:
            status_desc = r'为目标客户端配置硬件'
        self._set_status_display_and_check_cancel = (
            status_desc, 'TASK_STEP_IN_PROGRESS_RESTORE_CONFIG_FOR_CLIENT')

    def work(self, task_name):
        _restore_source_valid_checker.insert_one_restore_logic_obj(self)
        try:
            status_str = self._get_status_str()
            if self._is_restore_windows():
                _logger.info(r'{} : windows'.format(task_name))
                self._set_status_display_and_check_cancel = (r'锁定快照点文件', 'TASK_STEP_IN_PROGRESS_RESTORE_LOCK_SNAPSHOT')
                _is_cancel_task(self.get_task_object(), False)
                self._check_and_lock_disk_snapshots(task_name)
                self._set_status_display_and_check_cancel = (r'为目标磁盘生成去重数据', 'TASK_STEP_IN_PROGRESS_RESTORE_OPTIMIZE')
                _is_cancel_task(self.get_task_object(), False)
                self._run_restore_optimize()
                self._set_status_display_and_check_cancel = (
                    r'发送任务指令到目标客户端', 'TASK_STEP_IN_PROGRESS_RESTORE_SEND_COMMAND_TO_CLIENT')
                _is_cancel_task(self.get_task_object(), False)
                self._send_cmd_to_pe_host()
                self._set_status_display_and_check_cancel = (
                    r'为目标客户端硬件生成配置', 'TASK_STEP_IN_PROGRESS_RESTORE_GENERATE_CONFIG')
                _is_cancel_task(self.get_task_object(), False)
                self._generate_pe_stage_iso()
                self._set_status_display_and_check_cancel = (
                    r'等待目标客户端完成初始化', 'TASK_STEP_IN_PROGRESS_RESTORE_WAIT_CLIENT_INIT')
                _is_cancel_task(self.get_task_object(), False)
                self._wait_kvm_start_command()
                _get_kvm_port_handle = threading.Thread(target=self._get_kvm_port, name='get_kvm_port')
                _get_kvm_port_handle.start()
                _is_cancel_task(self.get_task_object(), False)
                self.run_kvm()
                _get_kvm_port_handle.join()
                self._set_status_display_and_check_cancel = status_str[0]
                _is_cancel_task(self.get_task_object(), False)
                self.notify_kvm_end()
                self._wait_trans_data_end()
                self._set_status_display_and_check_cancel = status_str[1]
                _is_cancel_task(self.get_task_object(), False)
            else:
                _logger.info(r'{} : linux'.format(task_name))
                self._set_status_display_and_check_cancel = (r'锁定快照点文件', 'TASK_STEP_IN_PROGRESS_RESTORE_LOCK_SNAPSHOT')
                _is_cancel_task(self.get_task_object(), False)
                self._check_and_lock_disk_snapshots(task_name)
                self._set_status_display_and_check_cancel = (r'为目标磁盘生成去重数据', 'TASK_STEP_IN_PROGRESS_RESTORE_OPTIMIZE')
                _is_cancel_task(self.get_task_object(), False)
                self._run_restore_optimize()
                self._set_status_display_and_check_cancel = (
                    r'发送任务指令到目标客户端', 'TASK_STEP_IN_PROGRESS_RESTORE_SEND_COMMAND_TO_CLIENT')
                _is_cancel_task(self.get_task_object(), False)
                self._send_cmd_to_pe_host()
                self._set_status_display_and_check_cancel = (
                    r'等待目标客户端完成初始化', 'TASK_STEP_IN_PROGRESS_RESTORE_WAIT_CLIENT_INIT')
                _is_cancel_task(self.get_task_object(), False)
                self._wait_kvm_start_command()
                self._set_status_display_and_check_cancel = (
                    r'为目标客户端配置硬件', 'TASK_STEP_IN_PROGRESS_RESTORE_GENERATE_CONFIG')
                _is_cancel_task(self.get_task_object(), False)
                self._run_kvm_linux()
                self._set_status_display_and_check_cancel = status_str[0]
                _is_cancel_task(self.get_task_object(), False)
                self.notify_kvm_end()
                self._wait_trans_data_end()
                self._set_status_display_and_check_cancel = status_str[1]
                _is_cancel_task(self.get_task_object(), False)

            if self.is_disable_fast_restore:
                self._finish_task()

            self._is_restore_source_invalid()
        except Exception as e:
            _logger.warning(r'PeRestore work failed : {}'.format(e), exc_info=True)
            is_link = self._is_target_link()
            self._force_offline_target()
            self.unlock_disk_snapshots(task_name)

            if is_link:
                self._is_restore_source_invalid()
                raise e
            else:
                xlogging.raise_and_logging_error('目标计算机离线',
                                                 'PeRestore.work is NOT link {} failed'.format(task_name))
        finally:
            _restore_source_valid_checker.remove_one_restore_logic_obj(self)
            self.delete_temp_files()
            _restore_is_complete(self.get_task_object())

    @xlogging.convert_exception_to_value(None)
    def _force_offline_target(self):
        boxService.box_service.forceOfflinePeHost(self._restore_target_object.ident)

    @xlogging.convert_exception_to_value(False)
    def _is_target_link(self):
        return boxService.box_service.isPeHostLinked(self._restore_target_object.ident)

    def _check_and_lock_disk_snapshots(self, task_name):
        restore_files = self._get_restore_files()
        self.check_and_lock_disk_snapshots_by_restore_files(restore_files, task_name)

    @staticmethod
    def check_and_lock_disk_snapshots_by_restore_files(restore_files, task_name):
        for restore_file in restore_files:
            DiskSnapshotLocker.lock_files(restore_file.snapshot, task_name)  # snapshot 为 IMG::ImageSnapshotIdents
            for file in restore_file.snapshot:
                CompressTaskThreading().update_task_by_disk_snapshot(file.path, file.snapshot)

    def unlock_disk_snapshots(self, task_name):
        restore_files = self._get_restore_files()
        self.unlock_disk_snapshots_by_restore_files(restore_files, task_name)

    @staticmethod
    def unlock_disk_snapshots_by_restore_files(restore_files, task_name):
        for restore_file in restore_files:
            DiskSnapshotLocker.unlock_files(restore_file.snapshot, task_name)  # snapshot 为 IMG::ImageSnapshotIdents

    def _wait_kvm_start_command(self):
        _logger.info(r'_wait_kvm_start_command begin ： {}'.format(self._restore_target_object.ident))
        kvm_start = False
        while not kvm_start:
            _is_cancel_task(self.get_task_object(), False)
            self._restore_target_object = RestoreTarget.objects.get(id=self._restore_target_object.id)
            self._pe_info = json.loads(self._restore_target_object.info)
            if 'kvm_start' in self._pe_info.keys():
                kvm_start = True
            else:
                time.sleep(5)
                self._check_pe_host_linked()
        _logger.info(r'_wait_kvm_start_command end ： {}'.format(self._restore_target_object.ident))

    def _create_start_kvm_flag_file(self, folder_path=r'/tmp/running_kvms'):
        if not boxService.box_service.isFolderExist(folder_path):
            boxService.box_service.makeDirs(folder_path)
        flag_file = os.path.join(folder_path, uuid.uuid4().hex)

        task_obj = self.get_task_object()
        ext_info = json.loads(task_obj.ext_config)
        if xdata.CANCEL_TASK_EXT_KEY in ext_info:
            return flag_file
        else:
            with open(flag_file, 'w') as fout:
                pass
            ext_info[xdata.START_KVM_FLAG_FILE] = flag_file
            task_obj.ext_config = json.dumps(ext_info)
            task_obj.save(update_fields=['ext_config'])
            return flag_file

    def run_kvm(self):
        if self._pe_info['remote_kvm_params']['enablekvm'] == '0':
            run_on_other_host = None
        else:
            run_on_other_host = json.dumps(self._pe_info['remote_kvm_params'])
        params = {
            'pe_ident': self._restore_target_object.ident,
            'boot_disk_token': self._pe_info['boot_device_restore_target_disk_token'],
            'boot_disk_bytes': self._pe_info['boot_device_disk_bytes'],
            'kvm_virtual_devices': self._pe_info['kvm_virtual_devices'],
            'kvm_cpu_id': r'40000600-20160519-0-0-0',
            'iso_path': self._pe_stage_iso_file_path,
            'kvm_virtual_device_hids': self._altered_info['kvm_virtual_device_hids'],
            'floppy_path': self._floppy_path,
            'data_devices': self._pe_info['data_devices'],
            'is_efi': self._pe_info['boot_device_disk_is_gpt'],
            'kvm_vbus_devices': self._altered_info['kvm_vbus_devices'],
            'htb_disk_path': None,
            'run_on_other_host': run_on_other_host,
            'logic': 'windows',
            'start_kvm_flag_file': self._create_start_kvm_flag_file()
        }

        boxService.box_service.runRestoreKvm(json.dumps(params, ensure_ascii=False))

    # for linux
    def get_htb_key_data_dir(self):
        cfg = self._pe_info['agent_service_configure']
        task_uuid = cfg.get('htb_task_uuid', '')
        path = ''
        if task_uuid:
            prefix = xdata.HTB_DISK_FILES_DIR
            path = prefix.format(task_uuid)
        return path

    @staticmethod
    def get_disk_ident_by_snapshot_ident(snapshot_ident):
        return DiskSnapshot.objects.get(ident=snapshot_ident).disk.ident

    def linux_disk_index_info(self):
        disks_index_info = self._pe_info['linux_disk_index_info']
        for index_info in disks_index_info:
            index_info['disk_ident'] = self.get_disk_ident_by_snapshot_ident(index_info['snapshot_disk_ident'])
        return disks_index_info

    def data_devices(self):
        devices = self._pe_info['data_devices']
        for data_device in devices:
            data_device['disk_ident'] = self.get_disk_ident_by_snapshot_ident(data_device['normal_snapshot_ident'])
        return devices

    def _run_kvm_linux(self):
        open_kvm_params = self._open_kvm_params()
        _logger.info('open_kvm_params:{}'.format(open_kvm_params))
        random_string = uuid.uuid4().hex
        params = {
            'open_kvm_params': open_kvm_params,
            'pe_ident': self._restore_target_object.ident,
            'boot_disk_token': self._pe_info['boot_device_restore_target_disk_token'],
            'boot_disk_bytes': self._pe_info['boot_device_disk_bytes'],
            'boot_device_normal_snapshot_ident': self.get_disk_ident_by_snapshot_ident(
                self._pe_info['boot_device_normal_snapshot_ident']),
            'data_devices': self.data_devices(),
            'linux_disk_index_info': self.linux_disk_index_info(),
            'linux_storage': self._pe_info['linux_storage'],
            'linux_info': self._pe_info['linux_info'],
            'mount_path': xdata.get_path_in_ram_fs('kvm_linux', random_string),
            'link_path': xdata.get_path_in_ram_fs(random_string),
            'restore_config': self._get_restore_config(),
            'floppy_path': self._floppy_path,
            'ipconfigs': self._pe_info['ipconfigs'],
            'kvm_virtual_devices': self._pe_info['kvm_virtual_devices'],
            'kvm_vbus_devices': self._pe_info['kvm_vbus_devices'],

            'logic': 'linux',
            'start_kvm_flag_file': self._create_start_kvm_flag_file(),
            'htb_key_data_dir': self.get_htb_key_data_dir(),
        }

        boxService.box_service.runRestoreKvm(json.dumps(params, ensure_ascii=False))

    def notify_kvm_end(self):
        boxService.box_service.KvmStopped(self._restore_target_object.ident)

    def _wait_trans_data_end(self):
        _logger.info(r'_wait_trans_data_end begin ： {}'.format(self._restore_target_object.ident))
        trans_end = False
        while not trans_end:
            _is_cancel_task(self.get_task_object(), False)
            flag, total_d, send_d = boxService.box_service.get_restore_key_data_process(
                self._restore_target_object.ident)
            if (flag and (total_d == send_d)) or (not flag):
                trans_end = True
            else:
                time.sleep(5)
        _logger.info(r'_wait_trans_data_end end ： {}'.format(self._restore_target_object.ident))

    @staticmethod
    def _get_tunnel_ip_port(tunnel_index):
        if int(tunnel_index) == -1:
            return '-1', '-1'
        else:
            tunnel_obj = Tunnel.objects.get(id=int(tunnel_index))
            return str(tunnel_obj.host_ip), str(tunnel_obj.host_port)

    @property
    def is_disable_fast_restore(self):
        return self._pe_info['disable_fast_boot']

    def _get_status_str(self):
        if self.is_disable_fast_restore:
            return (r'发送重建数据', 'TASK_STEP_IN_PROGRESS_RESTORE_SEND_REBUILD_DATA'), (
                r'等待客户端自动重启（请勿手动重启）', 'TASK_STEP_RESTORE_REBUILD_WAIT_CLIENT_RESTART')
        else:
            return (r'发送系统关键热数据', 'TASK_STEP_IN_PROGRESS_RESTORE_SEND_HOT_DATA'), (
                r'等待客户端自动重启（请勿手动重启）', 'TASK_STEP_RESTORE_HOT_WAIT_CLIENT_RESTART')

    def _finish_task(self):
        task_obj = self.get_task_object()
        restore_target = task_obj.restore_target
        RestoreTargetChecker.report_restore_target_finished(restore_target, True, '内部异常，代码1056', 'finish fail')
        for disk in restore_target.disks.all():
            try:
                boxService.box_service.updateToken(
                    KTService.Token(token=disk.token, snapshot=[], expiryMinutes=0))
            except Exception as e:
                _logger.warning('call boxService.updateToken failed. {}'.format(e))

    def _is_virtual_sub_device(self, hardware):
        is_sub = boxService.box_service.ChkIsSubId(hardware)
        if is_sub:
            _logger.warning('is_virtual_sub_device find virtual sub device, device:{}'.format(hardware))
        return is_sub

    def filter_virtual_sub_device(self, hardwares):
        return [item for item in hardwares if not self._is_virtual_sub_device(item)]

    # 还原到linuxAgent的时候，博康网卡需要额外的添加子设备。
    # 需要修正 的信息在 _altered_info 之中
    def _modify_hardwares_and_choice_version(self, hardwares, choice_drivers_versions):
        restore_target = RestoreTarget.objects.get(id=self._restore_target_object.id)
        pe_info = json.loads(restore_target.info)
        kvm_vbus_device_string = pe_info['kvm_vbus_devices']
        kvm_virtual_device_hids = pe_info['kvm_virtual_device_hids']
        ipconfigs = pe_info['ipconfigs']
        new_choice_drivers_versions, new_hardwares, no_pci_devices = self._get_new_hardwares_and_no_pci_devices(
            choice_drivers_versions, hardwares)
        self._altered_info = {
            'kvm_vbus_devices': kvm_vbus_device_string,
            'kvm_virtual_device_hids': kvm_virtual_device_hids,
            'ipconfigs': ipconfigs,
            'choice_drivers_versions': new_choice_drivers_versions,
            'hardwares': new_hardwares
        }
        _logger.info('_modify_hardwares_and_choice_version no_pci_devices:{}'.format(no_pci_devices))
        # no_pci_devices 表示设备产生的虚拟子设备，非空的情况下，就需要进行修正
        if no_pci_devices:

            kvm_vbus_device_string = self._get_kvm_virtual_parameters(kvm_vbus_device_string, kvm_virtual_device_hids,
                                                                      no_pci_devices)

            self._modify_ipconfig_from_no_pci_devices(ipconfigs, no_pci_devices)

            self._altered_info['kvm_vbus_devices'] = kvm_vbus_device_string
            self._altered_info['kvm_virtual_device_hids'] = kvm_virtual_device_hids
            self._altered_info['ipconfigs'] = ipconfigs
        else:
            pass

        self._altered_info['pci_bus_device_ids'] = ['not empty'] if kvm_vbus_device_string else list()

        _logger.info('_altered_info:{}'.format(self._altered_info))
        return None

    def _get_new_hardwares_and_no_pci_devices(self, choice_drivers_versions_str, hardwares):
        hardwares = self.filter_virtual_sub_device(hardwares)  # 过滤虚拟子设备
        no_pci_devices = dict()
        choice_drivers_versions = json.loads(choice_drivers_versions_str.replace('|', '\\'))
        for hardware in hardwares:
            driver_versions = choice_drivers_versions.get(hardware.HWIds[0], list())
            first_select = None
            for driver_version in driver_versions:
                # 不能用微软驱动去查找子设备
                if driver_version['UserSelected'] == 1 and (not driver_version['IsMicro']):
                    first_select = driver_version
                    break
            sub_virtual_device_list = self._get_sub_virtual_device(first_select)
            if sub_virtual_device_list:
                no_pci_devices[hardware.HWIds[0]] = [item['hard_or_comp_id'] for item in sub_virtual_device_list]
            else:
                pass

        return choice_drivers_versions_str, hardwares, no_pci_devices

    def _get_kvm_virtual_parameters(self, kvm_vbus_device_string, kvm_virtual_device_hids, no_pci_devices):
        for _, sub_device in no_pci_devices.items():
            for device in sub_device:
                if device in kvm_virtual_device_hids:
                    continue
                _logger.warning('_modify_hardwares_and_choice_version append kvm vbus device,{}'.format(device))
                kvm_virtual_device_hids.append(device)
                if kvm_vbus_device_string != '' and not kvm_vbus_device_string.endswith('||'):
                    kvm_vbus_device_string += '||'
                kvm_vbus_device_string += '{}|{}'.format(device, device)
        return kvm_vbus_device_string

    def _modify_ipconfig_from_no_pci_devices(self, ipconfigs, no_pci_devices):
        for ipconfig in ipconfigs:
            hardwareConfig = json.loads(ipconfig['hardwareConfig'])
            sub_device = self._get_sub_device_from_no_pci_devices(hardwareConfig, no_pci_devices)
            _logger.warning('_modify_ipconfig_from_no_pci_devices sub_device:{}'.format(sub_device))
            if sub_device:
                sub_hw_config = hardwareConfig[0].copy()
                sub_hw_config['HardwareID'] = [sub_device]
                hardwareConfig.insert(0, sub_hw_config)
                ipconfig['hardwareConfig'] = json.dumps(hardwareConfig)

    def _get_sub_virtual_device(self, user_select):
        if user_select:
            return json.loads(boxService.box_service.GetDriversSubList(user_select))
        return list()

    def _get_sub_device_from_no_pci_devices(self, hardwareConfig, no_pci_devices):
        for hardware_id, sub_device in no_pci_devices.items():
            if hardware_id == hardwareConfig[0]['HardwareID'][0] and len(sub_device) == 1:
                return sub_device[0]
        return None

    def _get_target_disk_byte_size(self, dst_disk_index):
        for disk in self._pe_info['disks']:
            if int(disk['diskID']) == int(dst_disk_index):
                return int(disk['diskSecCount']) * 512
        xlogging.raise_and_logging_error('无效的磁盘大小', 'not valid disk size disk info:{} index:{}'.format(self._pe_info[
                                                                                                           'disks'],
                                                                                                       dst_disk_index))

    @staticmethod
    def _get_disk_hash_files_from_ice_snapshots(ice_snapshots):
        hash_files = list()
        validator_list = [GetSnapshotList.is_disk_snapshot_object_finished,
                          GetSnapshotList.is_disk_snapshot_hash_file_exists]

        for snapshot in ice_snapshots:
            disk_snapshot = PeRestore.get_snapshot_by_ice_snapshot(snapshot)
            for validator_fun in validator_list:
                if not validator_fun(disk_snapshot):
                    _logger.warning('_get_disk_hash_files_from_ice_snapshots validator_fun:{} return False'.format(
                        validator_fun))
                    return list()

            if disk_snapshot.is_cdp:
                hash_files.append('cdp|{}|{}'.format(snapshot.path, snapshot.snapshot))
            else:
                hash_files.append(disk_snapshot.hash_path)
        hash_files.reverse()
        return hash_files

    @staticmethod
    def _get_restore_optimize_parameter(ice_snapshots, disk_snapshot, restore_token):
        ordered_hash_file = os.path.join(os.path.dirname(disk_snapshot.image_path),
                                         'restore_{}.hash'.format(restore_token))
        return {
            'hash_files': PeRestore._get_disk_hash_files_from_ice_snapshots(ice_snapshots),
            'disk_bytes': disk_snapshot.bytes,
            'ordered_hash_file': ordered_hash_file,
            'restore_token': restore_token
        }

    def _run_restore_optimize(self):
        for optimize_parameter in self._restore_optimize_parameters:
            if not optimize_parameter['hash_files']:
                _logger.warning('_run_restore_optimize disk:{} has no hash file, not enable optimize'.format(
                    optimize_parameter['restore_token']))
                continue
            else:
                try:
                    boxService.box_service.startBackupOptimize(json.dumps(optimize_parameter))
                except Exception as e:
                    _logger.error('_run_restore_optimize fail:{}'.format(e), exc_info=True)
                else:
                    restore_disk = RestoreTargetDisk.objects.get(token=optimize_parameter['restore_token'])
                    restore_disk.hash_path = optimize_parameter['ordered_hash_file']
                    restore_disk.save(update_fields=['hash_path'])

    @staticmethod
    def end_restore_optimize(restore_target):
        for disk in restore_target.disks.all():
            boxService.box_service.remove(disk.hash_path)

    @staticmethod
    def get_snapshot_by_ice_snapshot(snapshot):
        if DiskSnapshot.is_cdp_file(snapshot.path):
            return DiskSnapshot.objects.get(image_path=snapshot.path)
        else:
            return DiskSnapshot.objects.get(ident=snapshot.snapshot)

    @staticmethod
    def _get_disk_detail(host_snapshot_id, disk_snapshot):
        host_snapshot = HostSnapshot.objects.get(id=host_snapshot_id)
        ext_info = json.loads(host_snapshot.ext_info)
        for disk in ext_info['disk_index_info']:
            snapshot_disk_ident = disk.get('snapshot_disk_ident', None)
            if not snapshot_disk_ident:
                continue
            try:
                tmp_disk_snapshot = DiskSnapshot.objects.get(ident=snapshot_disk_ident)
            except DiskSnapshot.DoesNotExist:
                continue
            else:
                if tmp_disk_snapshot.disk.ident == disk_snapshot.disk.ident:
                    break
        else:
            return disk_snapshot.boot_device, disk_snapshot.boot_device, disk_snapshot.boot_device
        return disk_snapshot.boot_device, disk.get('is_system', False), disk.get('is_bmf', False)


class RestoreTargetChecker(threading.Thread):
    def run(self):
        time.sleep(120)
        while True:
            try:
                self.work()
            except Exception as e:
                _logger.error(r'RestoreTargetChecker Exception : {}'.format(e), exc_info=True)
            time.sleep(600)  # 间隔600秒执行一次

    @staticmethod
    @xlogging.db_ex_wrap
    def work():
        targets = RestoreTarget.objects.filter(start_datetime__isnull=False, finish_datetime__isnull=True,
                                               token_expires__lt=timezone.now())
        for target in targets:
            RestoreTargetChecker.report_restore_target_finished(
                target, False, r'目标客户端离线超过{}分钟'.format(target.expiry_minutes),
                r'RestoreTargetChecker work expires : {}'.format(
                    target.token_expires.strftime(xdatetime.FORMAT_WITH_SECOND)))

    @staticmethod
    def report_restore_target_finished(restore_target_object, successful, msg, debug):
        if RestoreTargetChecker.report_restore_task_finished(restore_target_object, successful, msg, debug):
            pass
        elif RestoreTargetChecker.report_migrate_task_finished(restore_target_object, successful, msg, debug):
            pass
        else:
            xlogging.raise_and_logging_error(r'无关联任务的还原目标客户端：{}'.format(restore_target_object.ident),
                                             'RestoreTargetChecker no task !? {}'.format(restore_target_object.ident))
        PeRestore.end_restore_optimize(restore_target_object)
        pe_restore = PeRestore(restore_target_object)
        pe_restore.delete_temp_files()
        restore_target_object.finish_datetime = timezone.now()
        restore_target_object.successful = successful
        restore_target_object.save(update_fields=['finish_datetime', 'successful'])

    @staticmethod
    def report_restore_task_finished(restore_token_object, successful, msg, debug):
        restore_task_object = RestoreTargetChecker._get_restore_task_object(restore_token_object)
        if restore_task_object is not None:
            pe_restore = PeRestore(restore_token_object)
            pe_restore.unlock_disk_snapshots('restore_{}'.format(restore_task_object.id))
            pe_restore.unlock_disk_snapshots('volume_restore_{}'.format(restore_task_object.id))
            RestoreTargetChecker.report_restore_task_finish(restore_task_object, successful, msg, debug)
            return True
        return False

    @staticmethod
    def report_migrate_task_finished(restore_token_object, successful, msg, debug):
        migrate_task_object = RestoreTargetChecker._get_migrate_task_object(restore_token_object)
        if migrate_task_object is not None:
            pe_restore = PeRestore(restore_token_object)
            pe_restore.unlock_disk_snapshots('migrate_{}'.format(migrate_task_object.id))
            RestoreTargetChecker.report_migrate_task_finish(migrate_task_object, successful, msg, debug)
            return True
        return False

    @staticmethod
    def _get_restore_task_object(restore_token_object):
        try:
            return restore_token_object.restore
        except RestoreTask.DoesNotExist:
            return None

    @staticmethod
    def _get_migrate_task_object(restore_token_object):
        try:
            return restore_token_object.migrate
        except MigrateTask.DoesNotExist:
            return None

    @staticmethod
    def report_restore_task_finish(restore_task, is_successful, msg, debug):
        if not restore_task.finish_datetime:
            restore_task.finish_datetime = timezone.now()
            restore_task.successful = is_successful
            restore_task.save(update_fields=['finish_datetime', 'successful'])

            host_snapshot = restore_task.host_snapshot
            restore_target = restore_task.restore_target

            pe_info = json.loads(restore_target.info)

            plan_obj = host_snapshot.schedule
            plan_name_alias = host_snapshot.host.display_name
            plan_name = plan_obj.name if plan_obj else plan_name_alias
            start_time = pe_info['restore_time']
            pe_ip = pe_info['remote_ip']
            task_type = ''
            if is_successful:
                if is_restore_target_belong_htb(restore_task.restore_target):
                    log_type = HostLog.LOG_HTB
                    plan_name, start_time = get_plan_name_start_time_for_htb(restore_target.htb_task.last(), plan_name,
                                                                             start_time)
                    desc = r'传输数据"{0}:{1}"到“{2}”完成'.format(plan_name, start_time, pe_ip)
                else:
                    task_type = 'restore_task'
                    log_type = HostLog.LOG_RESTORE_SUCCESSFUL
                    desc = r'还原备份点"{0}:{1}"到"{2}" 成功'.format(plan_name, start_time, pe_ip)
                HostLog.objects.create(
                    host=restore_task.host_snapshot.host, type=log_type,
                    reason=json.dumps({'pe_host': restore_task.restore_target.id, 'description': desc,
                                       'stage': 'TASK_STEP_IN_PROGRESS_RESTORE_SUCCESS',
                                       'task_type': task_type,
                                       task_type: restore_task.id},
                                      ensure_ascii=False))
            else:
                desc = r'还原备份点"{0}:{1}"到"{2}" 失败'.format(plan_name, start_time, pe_ip)
                if is_restore_target_belong_htb(restore_task.restore_target):
                    log_type = HostLog.LOG_HTB
                else:
                    task_type = 'restore_task'
                    log_type = HostLog.LOG_RESTORE_FAILED
                HostLog.objects.create(
                    host=restore_task.host_snapshot.host, type=log_type,
                    reason=json.dumps(
                        {'pe_host': restore_task.restore_target.id, 'debug': debug, 'description': desc + '：' + msg,
                         'stage': 'TASK_STEP_IN_PROGRESS_RESTORE_FAILED',
                         'task_type': task_type,
                         task_type: restore_task.id
                         },
                        ensure_ascii=False))

    @staticmethod
    def report_migrate_task_finish(migrate_task, is_successful, msg, debug):
        if not migrate_task.finish_datetime:
            migrate_task.finish_datetime = timezone.now()
            migrate_task.successful = is_successful
            migrate_task.save(update_fields=['finish_datetime', 'successful'])

            host_ip = migrate_task.host_snapshot.host.last_ip,
            pe_ip = json.loads(migrate_task.restore_target.info)['remote_ip']
            if is_successful:
                desc = r'迁移"{host_ip}"到"{pe_ip}" 成功'.format(host_ip=host_ip, pe_ip=pe_ip)
                HostLog.objects.create(
                    host=migrate_task.host_snapshot.host, type=HostLog.LOG_MIGRATE_SUCCESSFUL,
                    reason=json.dumps({'pe_host': migrate_task.restore_target.id, 'description': desc},
                                      ensure_ascii=False))
            else:
                desc = r'迁移"{host_ip}"到"{pe_ip}" 失败'.format(host_ip=host_ip, pe_ip=pe_ip)
                HostLog.objects.create(
                    host=migrate_task.host_snapshot.host, type=HostLog.LOG_MIGRATE_FAILED,
                    reason=json.dumps(
                        {'pe_host': migrate_task.restore_target.id, 'debug': debug, 'description': desc + '：' + msg},
                        ensure_ascii=False))


@xlogging.convert_exception_to_value(None)
def force_kill_kvm(task_obj):
    ext_info = json.loads(task_obj.ext_config)
    flag_file = ext_info.get(xdata.START_KVM_FLAG_FILE, None)
    if (flag_file is not None) and boxService.box_service.isFileExist(flag_file):
        boxService.box_service.remove(flag_file)
