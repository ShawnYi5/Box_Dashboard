import base64
import gzip
import json
import os
import threading
import uuid
from datetime import datetime, timedelta

from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apiv1 import ClientIpMg
from apiv1.models import (
    HostSnapshot, DiskSnapshot, Disk, CDPDiskToken, CDPTask, DiskSnapshotCDP, StorageNode, MigrateTask, HostSnapshotCDP,
    UserQuota, HostLog, BackupTaskSchedule, ClusterBackupSchedule, Host, RestoreTargetDisk, FileBackupTask,
    ClusterTokenMapper
)
from apiv1.serializers import HostSessionPhysicalDisk, HostSessionPhysicalDiskSerializer
from apiv1.snapshot import GetSnapshotList, Tokens, GetDiskSnapshot
from apiv1.spaceCollection import DeleteDiskSnapshotTask, DeleteCdpFileTask
from apiv1.storage_nodes import UserQuotaTools
from box_dashboard import xlogging, boxService, xdata

_logger = xlogging.getLogger(__name__)

import BoxLogic
import Box
import KTService
import IMG

NAS_QCOW_PATH = r'/home/file_backup/'
report_progress_locker = threading.Lock()


class HostSnapshotFinishHelper(object):
    @staticmethod
    @xlogging.convert_exception_to_value(None)
    def stopBackupOptimize(host_snapshot_object):
        ext_info = json.loads(host_snapshot_object.ext_info)
        if ext_info.get('stop_optimize_parameters', None):
            boxService.box_service.stopBackupOptimize(json.dumps(ext_info['stop_optimize_parameters']))


# 特殊处理的异常
class CreateSnapshotImageError(Exception):
    def __init__(self, msg, debug, err_code):
        super(CreateSnapshotImageError, self).__init__(msg)
        self.msg = msg
        self.debug = debug
        self.err_code = err_code


class HostBackupWorkProcessors(object):
    # progress HostSessionBackupProgressSerializer 反序列化的字典对象
    @staticmethod
    def report_progress(host_snapshot_object, progress):
        if host_snapshot_object.display_status != \
                json.loads(xdata.BACKUP_PROGRESS_TYPE_CHOICES_DICT[progress['code']])[0]:

            if progress['code'] == xdata.BACKUP_PROGRESS_TYPE_CHOICES_POST_DATA:
                with report_progress_locker:
                    host_snapshot_object = HostSnapshot.objects.get(id=host_snapshot_object.id)
                    if host_snapshot_object.display_status != \
                            json.loads(xdata.BACKUP_PROGRESS_TYPE_CHOICES_DICT[progress['code']])[0]:
                        HostBackupWorkProcessors.update_progress(host_snapshot_object, progress)
                        HostBackupWorkProcessors.create_log_in_db(host_snapshot_object, progress)
                        return
            else:
                HostBackupWorkProcessors.create_log_in_db(host_snapshot_object, progress)

        HostBackupWorkProcessors.update_progress(host_snapshot_object, progress)

    @staticmethod
    @xlogging.convert_exception_to_value(None)
    def cluster_hosts_log(schedule, task_object, log_type, special_host=None, **kwargs):
        if kwargs.get('error_occur', None):
            return

        for host in schedule.hosts.all():
            if special_host and (special_host.ident != host.ident):
                continue
            reason = {'backup_task': task_object.id, 'description': '', 'debug': ''}

            if log_type in [HostLog.LOG_CLUSTER_BACKUP_START, HostLog.LOG_CLUSTER_BACKUP_BASE,
                            HostLog.LOG_CLUSTER_BACKUP_ANALYZE, HostLog.LOG_CLUSTER_BACKUP_SNAPSHOT,
                            HostLog.LOG_CLUSTER_CDP]:
                reason['description'] = kwargs.get('substage', None)

            elif log_type == HostLog.LOG_CLUSTER_BACKUP_SUCCESSFUL:
                reason['description'] = '集群主机{}备份成功'.format(host.name)

            elif log_type == HostLog.LOG_CLUSTER_BACKUP_FAILED:
                reason['description'] = '集群主机{}备份失败'.format(host.name)
                reason['debug'] = kwargs.get('debug', None)

            else:
                pass

            HostLog.objects.create(host=host, type=log_type, reason=json.dumps(reason, ensure_ascii=False))

    @staticmethod
    def create_log_in_db(host_snapshot_object, progress):
        if isinstance(host_snapshot_object.schedule, BackupTaskSchedule):
            if host_snapshot_object.is_cdp:
                HostLog.objects.create(
                    host=host_snapshot_object.host,
                    type=HostLog.LOG_AGENT_STATUS,
                    reason=json.dumps(
                        {'description': json.loads(xdata.BACKUP_PROGRESS_TYPE_CHOICES_DICT[progress['code']])[0],
                         'stage': json.loads(xdata.BACKUP_PROGRESS_TYPE_CHOICES_DICT[progress['code']])[1],
                         'cdp_task': host_snapshot_object.cdp_task.id,
                         'task_type': 'cdp_task'}))
            else:
                HostLog.objects.create(
                    host=host_snapshot_object.host,
                    type=HostLog.LOG_AGENT_STATUS,
                    reason=json.dumps(
                        {'description': json.loads(xdata.BACKUP_PROGRESS_TYPE_CHOICES_DICT[progress['code']])[0],
                         'stage': json.loads(xdata.BACKUP_PROGRESS_TYPE_CHOICES_DICT[progress['code']])[1],
                         'backup_task': host_snapshot_object.backup_task.id,
                         'task_type': 'backup_task'}))

        elif isinstance(host_snapshot_object.cluster_schedule, ClusterBackupSchedule):
            schedule = host_snapshot_object.cluster_schedule
            task_object = host_snapshot_object.cdp_task.cluster_task
            if schedule.cycle_type == BackupTaskSchedule.CYCLE_CDP:
                log_type = HostLog.LOG_CLUSTER_CDP
            else:
                log_type = HostLog.LOG_CLUSTER_BACKUP_BASE
            HostBackupWorkProcessors.cluster_hosts_log(schedule, task_object, log_type,
                                                       host_snapshot_object.host,
                                                       **{'substage':
                                                              json.loads(xdata.BACKUP_PROGRESS_TYPE_CHOICES_DICT[
                                                                             progress['code']])[0]})
        else:
            pass

    @staticmethod
    def update_progress(host_snapshot_object, progress):
        host_snapshot_object.display_status = json.loads(xdata.BACKUP_PROGRESS_TYPE_CHOICES_DICT[progress['code']])[0]
        if progress['code'] == xdata.BACKUP_PROGRESS_TYPE_CHOICES_POST_DATA:
            ext_info = json.loads(host_snapshot_object.ext_info)
            ext_info['progressIndexTemp'] = ext_info.get('progressIndex', 0)
            ext_info['progressIndex'] = progress['progressIndex']
            ext_info['progressTotal'] = progress['progressTotal']
            ext_info['updateTime'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            host_snapshot_object.ext_info = json.dumps(ext_info, ensure_ascii=False)
            update_fields = ['display_status', 'ext_info']
        else:
            update_fields = ['display_status']
        host_snapshot_object.save(update_fields=update_fields)

    @staticmethod
    def report_finish(host_snapshot_object, code):
        _logger.info('HostBackupWorkProcessors report_finish recv code : {} - {}'
                     .format(code, BoxLogic.BackupFinishCode.valueOf(code)))
        if code == BoxLogic.BackupFinishCode.Error.value:
            successful = False
        else:
            successful = True
        HostBackupWorkProcessors.set_host_snapshot_finish(host_snapshot_object, successful, code)

    @staticmethod
    def deal_cdp_when_backup_failed(host_snapshot_object, cdp_task_object, partial=False):
        cdp_tokens = CDPDiskToken.objects.filter(task=cdp_task_object).all()
        for cdp_token in cdp_tokens:
            try:
                boxService.box_service.updateToken(KTService.Token(token=cdp_token.token, snapshot=[], expiryMinutes=0))
            except Exception as e:
                _logger.warning('deal_cdp_when_backup_failed call boxService.updateToken {} failed. {}'
                                .format(cdp_token.token, e))

        delete_disk_snapshot_task_workers = list()

        if not partial:
            disk_snapshots = host_snapshot_object.disk_snapshots.all()
            for ds in disk_snapshots:
                ds.merged = True
                ds.save(update_fields=['merged'])
                worker = DeleteDiskSnapshotTask(DeleteDiskSnapshotTask.create(ds.image_path, ds.ident))
                delete_disk_snapshot_task_workers.append(worker)

        delete_cdp_file_task_workers = list()

        cdp_disk_snapshots = DiskSnapshotCDP.objects.filter(token__in=cdp_tokens).all()
        for cdp_disk_snapshot in cdp_disk_snapshots:
            cdp_disk_snapshot.disk_snapshot.merged = True
            cdp_disk_snapshot.disk_snapshot.save(update_fields=['merged'])
            worker = DeleteCdpFileTask(DeleteCdpFileTask.create(cdp_disk_snapshot.disk_snapshot.image_path))
            delete_cdp_file_task_workers.append(worker)

        for worker in delete_disk_snapshot_task_workers:
            worker.work()
        for worker in delete_cdp_file_task_workers:
            worker.work()

    @staticmethod
    def deal_cdp_when_backup_ok(host_snapshot_object, cdp_task_object):
        cdp_tokens = CDPDiskToken.objects.filter(task=cdp_task_object).all()
        for cdp_token in cdp_tokens:
            try:
                boxService.box_service.updateToken(KTService.Token(token=cdp_token.token, snapshot=[], expiryMinutes=0))
            except Exception as e:
                _logger.warning(
                    'deal_cdp_when_backup_ok call boxService.updateToken {} failed. {}'.format(cdp_token.token, e))

            if cdp_token.using_disk_snapshot is None:
                continue

            disk_snapshot_object = cdp_token.using_disk_snapshot
            disk_snapshot_cdp_object, token_setting = \
                Tokens.suspend_disk_snapshot_cdp_object(disk_snapshot_object.cdp_info, True, True)
            Tokens.set_token(token_setting)
            if disk_snapshot_cdp_object is not None:
                Tokens.suspend_disk_snapshot_cdp_object(disk_snapshot_cdp_object, False)

        cdp_tokens = CDPDiskToken.objects.filter(task=cdp_task_object).all()
        last_timestamp = None
        for cdp_token in cdp_tokens:
            if cdp_token.last_disk_snapshot is None:
                continue

            disk_snapshot_object = DiskSnapshot.objects.get(id=cdp_token.last_disk_snapshot.id)

            if last_timestamp is None or disk_snapshot_object.cdp_info.last_timestamp > last_timestamp:
                last_timestamp = disk_snapshot_object.cdp_info.last_timestamp

        if last_timestamp is not None:
            host_snapshot_object.cdp_info.last_datetime = datetime.fromtimestamp(last_timestamp)
            host_snapshot_object.cdp_info.save(update_fields=['last_datetime'])

    @staticmethod
    def get_disk_read_thread_count_from_schedule_or_task(backup_or_cluster_schedule, migrate_task):
        if migrate_task:
            ext_config = json.loads(migrate_task.ext_config)
        elif backup_or_cluster_schedule:
            ext_config = json.loads(backup_or_cluster_schedule.ext_config)
        else:
            return 1

        return ext_config.get('diskreadthreadcount', 1)

    def additional_backup_command(self, task_obj):
        """
        :return: json_str
        """
        if isinstance(task_obj, MigrateTask):
            arch = self._host_ext_info.get('system_infos', {}).get('System', {}).get('ProcessorArch', '')
            if '64' in arch:
                return json.dumps(xdata.MIGRATE_BACKUP_X64_PARAMS)
            return json.dumps(xdata.MIGRATE_BACKUP_X86_PARAMS)

        return json.dumps({})

    # task_name 任务名称
    # host_object Host 数据库对象
    def __init__(self, task_name, host_object, force_agent_full=False, is_cdp=False, cdp_mode_type=-1,
                 cdp_task_object_id=-1, storage_node_ident=None, schedule_object=None, cluster_schedule_object=None,
                 cluster_disks=None, migrate_task=None, force_store_full=False, disable_optimize=False,
                 force_optimize=False):

        self._schedule_object = schedule_object if schedule_object else cluster_schedule_object
        self._is_cdp = is_cdp
        self._cdp_mode_type = cdp_mode_type
        self._host_object = host_object
        self._host_ext_info = json.loads(host_object.ext_info)
        self._cdp_task_object_id = cdp_task_object_id
        self._task_name = task_name
        self._force_agent_full = force_agent_full
        self._force_store_full = force_store_full
        self._disable_optimize = disable_optimize
        self._force_optimize = force_optimize
        self._cluster_disks = cluster_disks
        self._disk_read_thread_count = self.get_disk_read_thread_count_from_schedule_or_task(self._schedule_object,
                                                                                             migrate_task)
        self._additional_backup_cmd = self.additional_backup_command(migrate_task)

        self._storage_node_base_path = StorageNode.objects.get(ident=storage_node_ident).path if storage_node_ident \
            else self._get_storage_node_path_with_user_max_free_quota()
        self._kilo_bytes_per_second = -1
        self._remove_duplicates_in_system_folder = True
        self._special_mode = xdata.BACKUP_TASK_SCHEDULE_EXECUTE_SPECIAL_MODE_NONE
        self._exclude = list()
        self.vmware_quiesce = True
        self.vmware_tranport_modes = 1
        ext_config_json = None
        if schedule_object is not None:
            ext_config_json = json.loads(schedule_object.ext_config)
        elif cluster_schedule_object is not None:
            ext_config_json = json.loads(cluster_schedule_object.ext_config)
        if ext_config_json:
            maxBroadband = ext_config_json.get('maxBroadband', -1)
            if maxBroadband != -1:
                self._kilo_bytes_per_second = int(maxBroadband * 1024 / 8)
                if self._kilo_bytes_per_second <= 1024:
                    self._kilo_bytes_per_second = 1024
            self._special_mode = ext_config_json.get(
                'specialMode', xdata.BACKUP_TASK_SCHEDULE_EXECUTE_SPECIAL_MODE_NONE)
            self._remove_duplicates_in_system_folder = ext_config_json.get('removeDuplicatesInSystemFolder', True)
            self.vmware_quiesce = True if int(ext_config_json.get('vmware_quiesce', 1)) == 1 else False
            self.vmware_tranport_modes = ext_config_json.get('vmware_tranport_modes', 1)
            if schedule_object is not None:
                self._exclude = ext_config_json.get('exclude', list())
            elif cluster_schedule_object is not None:
                self._exclude = ext_config_json['exclude'].get(host_object.ident, list())

        self._host_snapshot = HostSnapshot.objects.create(
            host=self._host_object, is_cdp=self._is_cdp, schedule=schedule_object, display_status=r'命令初始化中',
            cluster_schedule=cluster_schedule_object, partial=True
        )
        if self._is_cdp:
            self._host_snapshot_cdp = HostSnapshotCDP.objects.create(host_snapshot=self._host_snapshot)
        else:
            self._host_snapshot_cdp = None
        self.log_info(r'{} cdp:{} cdpMode:{}'.format(self._host_object.ident, self._is_cdp, self._cdp_mode_type))

    def _get_storage_node_path_with_user_max_free_quota(self):
        user = self._host_object.user
        node_map = dict()
        for user_quota in UserQuota.objects.filter(user_id=user.id, deleted=False):
            free_size = UserQuotaTools(user_quota.storage_node.id, user_quota.user.id,
                                       user_quota.quota_size).get_user_available_storage_size_in_node()
            node_map[free_size] = user_quota.storage_node.path

        if not node_map:
            xlogging.raise_and_logging_error('用户无任何配额, 不能迁移',
                                             '_get_storage_node_path_with_user_max_free_quota when migration, failed')

        return node_map[sorted(node_map)[-1]]

    @property
    def _status_display(self):
        if self._host_snapshot:
            return self._host_snapshot.display_status
        else:
            r'noinit'

    @_status_display.setter
    def _status_display(self, values):
        value = values[0]
        stage = values[1]
        self._host_snapshot.display_status = value
        self._host_snapshot.save(update_fields=['display_status'])
        if isinstance(self._host_snapshot.schedule, BackupTaskSchedule):
            if self._is_cdp:
                HostLog.objects.create(host=self._host_object,
                                       type=HostLog.LOG_CDP_START,
                                       reason=json.dumps(
                                           {'description': value, 'stage': stage,
                                            'task_type': 'cdp_task',
                                            'cdp_task': self._host_snapshot.cdp_task.id
                                            }))
            else:
                HostLog.objects.create(host=self._host_object,
                                       type=HostLog.LOG_BACKUP_START,
                                       reason=json.dumps(
                                           {'description': value, 'stage': stage,
                                            'task_type': 'backup_task',
                                            'backup_task': self._host_snapshot.backup_task.id
                                            }))
        elif isinstance(self._host_snapshot.cluster_schedule, ClusterBackupSchedule):
            schedule = self._host_snapshot.cluster_schedule
            task_object = self._host_snapshot.cdp_task.cluster_task
            if schedule.cycle_type == BackupTaskSchedule.CYCLE_CDP:
                log_type = HostLog.LOG_CLUSTER_CDP
            else:
                log_type = HostLog.LOG_CLUSTER_BACKUP_BASE
            HostBackupWorkProcessors.cluster_hosts_log(schedule, task_object, log_type,
                                                       self._host_object,
                                                       **{'substage': value})
        else:
            pass

    def log_info(self, msg):
        _logger.info(self.add_msg_prefix(msg))

    def log_warnning(self, msg):
        _logger.warning(self.add_msg_prefix(msg))

    def log_error(self, msg):
        _logger.error(self.add_msg_prefix(msg), exc_info=True)

    def add_msg_prefix(self, msg):
        return '{}-{}: {}'.format(self._task_name, self._status_display, msg)

    @property
    def host_snapshot(self):
        return self._host_snapshot

    def clean_next_force_full(self):
        if self._host_snapshot.schedule:
            schedule = BackupTaskSchedule.objects.get(id=self._host_snapshot.schedule.id)
        elif self._host_snapshot.cluster_schedule:
            schedule = ClusterBackupSchedule.objects.get(id=self._host_snapshot.cluster_schedule.id)
        else:
            self.log_warnning(r'call clean_next_force_full without schedule')
            return

        ext_config = json.loads(schedule.ext_config)
        ext_config['next_force_full_count'] = 0
        schedule.ext_config = json.dumps(ext_config, ensure_ascii=False)
        schedule.save(update_fields=['ext_config', ])

    def set_next_force_full(self):
        if self._host_snapshot.schedule:
            schedule = BackupTaskSchedule.objects.get(id=self._host_snapshot.schedule.id)
            if schedule.cycle_type == BackupTaskSchedule.CYCLE_CDP:
                next_force_full_max = 24
            elif schedule.cycle_type == BackupTaskSchedule.CYCLE_ONCE:
                next_force_full_max = 0
            else:
                next_force_full_max = 6
                self._update_next_run_time(schedule, 5)
        elif self._host_snapshot.cluster_schedule:
            schedule = ClusterBackupSchedule.objects.get(id=self._host_snapshot.cluster_schedule.id)
            if schedule.cycle_type == BackupTaskSchedule.CYCLE_ONCE:
                next_force_full_max = 0
            else:
                next_force_full_max = 6
                self._update_next_run_time(schedule, 10)
        else:
            self.log_warnning(r'call set_next_force_full without schedule')
            return

        ext_config = json.loads(schedule.ext_config)
        next_force_full_count = ext_config.get('next_force_full_count', 0)
        if next_force_full_count < next_force_full_max:
            ext_config['next_force_full_count'] = next_force_full_count + 1
            self.log_warnning('next_force_full_count : {}'.format(next_force_full_count))
        else:
            ext_config['next_force_full_count'] = 0
            ext_config['next_force_full'] = True
            self.log_warnning('next_force_full to True')
        schedule.ext_config = json.dumps(ext_config, ensure_ascii=False)
        schedule.save(update_fields=['ext_config', ])

    def _update_next_run_time(self, schedule, mins):
        schedule.next_run_date = timezone.now() + timedelta(minutes=mins)
        schedule.save(update_fields=['next_run_date'])
        self.log_warnning('modify schedule next_run_date to:{}'.format(schedule.next_run_date))

    def _check_host_status(self):
        # TODO 后期需要支持一边还原一边备份
        host_status = boxService.box_service.GetStatus(self._host_object.ident)
        if 'v_restore' in host_status:
            xlogging.raise_and_logging_error(r'正在执行卷还原任务', self.add_msg_prefix(r'v_restore in host_status'))
        elif 'restore' in host_status:
            xlogging.raise_and_logging_error(r'正在执行还原任务', self.add_msg_prefix(r'restore in host_status'))

    def work(self):
        try:
            physical_disk_objects = self.update_physical_disks()
            self._check_host_status()
            efi_boot_data = self._get_efi_boot_data()
            _logger.info('efi_boot_data:{}'.format(efi_boot_data))
            system_infos = json.loads(boxService.box_service.querySystemInfo(self._host_object.ident))
            parameters, include_ranges, exclude_ranges, optimize_parameters = \
                self.generate_parameters(physical_disk_objects, system_infos)
            stop_optimize_parameters = self.run_optimize_parameters_before_backup(parameters, optimize_parameters)
            self.update_database(parameters, physical_disk_objects, system_infos, include_ranges, exclude_ranges,
                                 efi_boot_data, stop_optimize_parameters)
            self.run_script_before_backup(self._host_object, self._schedule_object)

            self.send_backup_command(parameters, self._kilo_bytes_per_second, self._additional_backup_cmd)
            self.set_host_snapshot_valid()
            self.clean_next_force_full()
            return parameters
        except CreateSnapshotImageError as csie:
            self.set_next_force_full()
            self.set_host_snapshot_failed()
            xlogging.raise_and_logging_error(csie.msg,
                                             r'work CreateSnapshotImageError : {} {}'.format(csie.debug, csie.err_code))
        except xlogging.BoxDashboardException as bde:
            if not bde.is_log:
                self.log_error(r'work BoxDashboardException : {} | {}'.format(bde.msg, bde.debug))
                bde.is_log = True
            self.set_host_snapshot_failed()
            raise
        except Exception as e:
            self.set_host_snapshot_failed()
            xlogging.raise_and_logging_error(r'内部异常，代码2346', r'work Exception : {}'.format(e))

    def _get_efi_boot_data(self):
        try:
            args = {'type': 'read_efi'}
            _, raw_data = boxService.box_service.JsonFuncV2(self._host_object.ident, json.dumps(args),
                                                            b'')
        except Exception as e:
            _logger.error('_get_efi_boot_data error:{}'.format(e))
            return ''
        _logger.info('_get_efi_boot_data raw_data:{}'.format(raw_data))

        try:
            return base64.b64encode(gzip.compress(raw_data)).decode()
        except Exception as e:
            _logger.error('_get_efi_boot_data compress error:{}'.format(e))
        return ''

    def set_host_snapshot_valid(self):
        now = timezone.now()
        if self.host_snapshot.is_cdp:
            self.host_snapshot.cdp_info.first_datetime = now
            self.host_snapshot.cdp_info.last_datetime = now
            self.host_snapshot.cdp_info.save(update_fields=['first_datetime', 'last_datetime'])

        self.host_snapshot.start_datetime = now
        self.host_snapshot.save(update_fields=['start_datetime'])

    def set_host_snapshot_failed(self):
        self.set_host_snapshot_finish(self.host_snapshot, False)
        self.log_info(r'set host snapshot finish failed : {} {}'.format(
            self.host_snapshot.id, self.host_snapshot.host.ident))

    @staticmethod
    def set_host_snapshot_finish(host_snapshot, successful, code=None):
        host_snapshot.finish_datetime = timezone.now()
        host_snapshot.successful = successful
        host_snapshot.partial = (code is None or (code != 0 and code != BoxLogic.BackupFinishCode.Error.value))
        ext_info = json.loads(host_snapshot.ext_info)
        ext_info['agent_finish_code'] = code
        host_snapshot.ext_info = json.dumps(ext_info, ensure_ascii=False)
        host_snapshot.save(update_fields=['finish_datetime', 'successful', 'partial', 'ext_info'])

    # 返回值：HostSessionPhysicalDiskSerializer 反序列化的字典对象
    def update_physical_disks(self):
        self._status_display = (r'获取磁盘状态', 'TASK_STEP_IN_PROGRESS_BACKUP_STATUS')
        try:
            disks = boxService.box_service.queryDisksStatus(self._host_object.ident)
            self.remove_unsupported_disks(disks)
            self.check_disks_only_one_boot_device(disks)
            self.check_disks_status_backupable(disks)

            physicalDisks = list()
            for disk in disks:
                physicalDisk = HostSessionPhysicalDisk.create(disk)
                physicalDisks.append(physicalDisk.__dict__)

            serializer = HostSessionPhysicalDiskSerializer(data=physicalDisks, many=True)
            serializer.is_valid(True)

            self.log_info(r'physical_disks : {}'.format(serializer.validated_data))
            return serializer.validated_data

        except ValidationError as ve:
            xlogging.raise_and_logging_error(
                r'获取磁盘状态失败，数据格式错误', self.add_msg_prefix(
                    r'update_physical_disks failed. HostSessionPhysicalDiskSerializer : {} '.format(ve)))

    @staticmethod
    def remove_unsupported_disks(disks):
        temp_disks = disks.copy()
        for disk in temp_disks:
            if disk.detail.status == BoxLogic.DiskStatus.Unsupported:
                disks.remove(disk)

    def check_disks_only_one_boot_device(self, disks):
        boot_device_count = 0
        for disk in disks:
            if disk.detail.bootDevice:
                boot_device_count += 1
        if boot_device_count != 1:
            if len(disks) != 1:
                xlogging.raise_and_logging_error(r'磁盘状态异常，可启动设备数量异常', self.add_msg_prefix(
                    'check_physical_disks failed. boot_device_count : {}'.format(boot_device_count)))
            else:
                _logger.warning(r'check_disks_only_one_boot_device force fix bootDevice')
                disks[0].detail.bootDevice = True

    def check_disks_status_backupable(self, disks):
        for disk in disks:
            disk_status = disk.detail.status

            if disk_status == BoxLogic.DiskStatus.ErrorOccurred:
                xlogging.raise_and_logging_error(r'磁盘驱动异常', self.add_msg_prefix('disk_status error: ErrorOccurred'))

            if disk_status == BoxLogic.DiskStatus.Backuping:
                xlogging.raise_and_logging_error(r'磁盘正在备份中', self.add_msg_prefix('disk_status error: Backuping'))

            if disk_status == BoxLogic.DiskStatus.CDPing:
                xlogging.raise_and_logging_error(r'磁盘正在CDP状态', self.add_msg_prefix('disk_status error: CDPing'))

    @staticmethod
    def _get_disk_style(disks, index):
        for disk in disks:
            if disk['index'] == index:
                return disk['style']
        return 'none'

    @staticmethod
    def get_linux_disk_partitions(disk_index, linux_partitions):
        rs = list()
        for partition in linux_partitions:
            if disk_index != partition['DiskIndex']:
                continue
            else:
                rs.append(partition)
        return rs

    @staticmethod
    def analyze_linux_partitions(linux_partitions, system_infos):
        if linux_partitions is not None:
            return linux_partitions

        linux_partitions = list()
        for disk in system_infos['Storage']['disks']:
            for partition in disk['partitions']:
                if partition['type'] != 'native':
                    continue
                linux_partitions.append({
                    'Style': disk["style"],
                    "Type": "native",
                    "FileSystem": "" if partition["fileSystem"] is None else partition["fileSystem"],
                    "MountPoint": "" if partition["mountPoint"] is None else partition["mountPoint"],
                    "MountOpts": "" if partition["mountOpts"] is None else partition["mountOpts"],
                    "VolumeDevice": partition["device"],
                    "TotalBytes": partition["totalBytes"] if (
                            partition["totalBytes"] and int(partition["totalBytes"]) >= 0) else '',
                    "UsedBytes": partition["usedByte"] if (
                            partition["usedByte"] and int(partition["usedByte"]) >= 0) else '',
                    "FreeBytes": partition["freeBytes"] if (
                            partition["freeBytes"] and int(partition["freeBytes"]) >= 0) else '',
                    "DiskIndex": disk["index"],
                    "BytesStart": partition["bytesStart"],
                    "BytesEnd": partition["bytesEnd"],
                })

        for vg in system_infos['Storage']['vgs']:
            for lv in vg['lvs']:
                for disk_range in lv['diskRanges']:
                    linux_partitions.append({
                        'Style': HostBackupWorkProcessors._get_disk_style(
                            system_infos['Storage']['disks'], disk_range["index"]),
                        "Type": "lv",
                        "FileSystem": "" if lv["fileSystem"] is None else lv["fileSystem"],
                        "MountPoint": "" if lv["mountPoint"] is None else lv["mountPoint"],
                        "MountOpts": "" if lv["mountOpts"] is None else lv["mountOpts"],
                        "VolumeDevice": r'/dev/mapper/{}-{}'.format(vg["name"], lv["name"]),
                        "TotalBytes": lv["total_bytes"] if (lv["total_bytes"] and int(lv["total_bytes"]) >= 0) else '',
                        "UsedBytes": lv["used_bytes"] if (lv["used_bytes"] and int(lv["used_bytes"]) >= 0) else '',
                        "FreeBytes": lv["free_bytes"] if (lv["free_bytes"] and int(lv["free_bytes"]) >= 0) else '',
                        "DiskIndex": int(disk_range["index"]),
                        "BytesStart": str(int(disk_range["start_sector"]) * 512),
                        "BytesEnd": str((int(disk_range["start_sector"]) + int(disk_range["sector_count"])) * 512),
                    })

        _logger.info('linux_partitions: {}'.format(linux_partitions))
        return linux_partitions

    # 返回值：Box.BackupFile 的 list
    def generate_parameters(self, physical_disk_objects, system_infos):
        system = system_infos['System']
        sys_os_class_type = 'linux' if 'LINUX' in (system['SystemCaption'].upper()) else 'windows'

        self._status_display = (r'查询历史快照点状态', 'TASK_STEP_IN_PROGRESS_BACKUP_HISTORY')
        include_ranges = list()
        exclude_ranges = list()
        parameters = list()
        optimize_parameters = list()

        linux_partitions = None
        could_optimize = self._check_if_could_optimize(system['version'])
        could_hash_disk_data = self._check_if_could_hash_disk_data()

        for physical_disk in physical_disk_objects:
            self.check_physical_disk(physical_disk)
            self.check_physical_disk_cdp(physical_disk)

            disk_agent_index = physical_disk['index']

            for disk in system_infos['Disk']:
                if int(disk['DiskNum']) == disk_agent_index:
                    break
            else:
                xlogging.raise_and_logging_error(
                    r'无效的磁盘信息', r'generate_parameters invalid physical_disk {}'.format(physical_disk),
                    physical_disk['index']
                )
                disk = None  # never execute

            if self._is_disk_exclude(disk) \
                    and BoxLogic.DiskStatus.NotExistLastSnapshot.value == physical_disk['disk_status']:
                # “计划中排除”且“从未做过备份”的磁盘，跳过
                continue

            disk_native_guid = disk['NativeGUID']

            if self._cluster_disks:
                for _disk_native_guid, disk_cluster_info in self._cluster_disks.items():
                    if disk_native_guid == _disk_native_guid:
                        break
                else:
                    disk_cluster_info = None
            else:
                disk_cluster_info = None

            parameter = Box.BackupFile()
            parameter.diskIndex = physical_disk['index']
            parameter.snapshot.snapshot = uuid.uuid4().hex.lower()
            parameter.diskByteSize = physical_disk['disk_bytes']

            config = {
                'exclude': list(),
                'diskreadthreadcount': self._disk_read_thread_count,
                'hash_disk_data': could_hash_disk_data,
                'de_duplication': 0,
                'host_snapshot_id': self._host_snapshot.id,  # 免代理备份使用
                'vmware_quiesce': self.vmware_quiesce,
                'vmware_tranport_modes': self.vmware_tranport_modes,
            }

            optimize_parameter = dict()

            not_cluster_in_slave = False

            if disk_cluster_info is None:  # 非集群 或 主节点的非集群盘
                backup_type, last_snapshot_array, last_snapshot_object = self.analyze_optimize_parameter(
                    config, could_optimize, disk, optimize_parameter, physical_disk)
            elif self._cluster_disks['master_node']:
                _logger.info('analyze_cluster_master_optimize_parameter disk_cluster_info {}'.format(disk_cluster_info))
                backup_type, last_snapshot_array, last_snapshot_object = self.analyze_cluster_master_optimize_parameter(
                    config, could_optimize, disk, optimize_parameter, physical_disk, disk_cluster_info)
            else:
                backup_type, last_snapshot_array, last_snapshot_object = self.analyze_cluster_slave_optimize_parameter(
                    config, could_optimize, disk, optimize_parameter, physical_disk, disk_cluster_info)

            if (config['de_duplication'] & xdata.DEDUPLE_TYPE_CLIENT_WORK) == xdata.DEDUPLE_TYPE_CLIENT_WORK:
                assert isinstance(optimize_parameter['hash_files'], list) and optimize_parameter['hash_files']
                config['deduple_hash_filepath'] = optimize_parameter['ordered_hash_file'] = (
                    '{}.{}'.format(optimize_parameter['hash_files'][-1], uuid.uuid4().hex))
                optimize_parameter['disk_bytes'] = int(physical_disk['disk_bytes'])  # 仅分析当前磁盘大小的区域

            _logger.info('HostBackupWorkProcessors disk:{} type:{}'.format(
                physical_disk['disk_ident'], xdata.get_type_name(xdata.BACKUP_TYPE, backup_type)))

            config['force_full'] = backup_type in (xdata.BACKUP_TYPE_AGENT_FULL_STORE_FULL_WITHOUT_OPTIMIZE,
                                                   xdata.BACKUP_TYPE_AGENT_FULL_STORE_FULL_WITH_OPTIMIZE,
                                                   xdata.BACKUP_TYPE_AGENT_FULL_STORE_INCREMENT,)

            generate_new = backup_type in (xdata.BACKUP_TYPE_AGENT_FULL_STORE_FULL_WITHOUT_OPTIMIZE,
                                           xdata.BACKUP_TYPE_AGENT_FULL_STORE_FULL_WITH_OPTIMIZE,)

            if generate_new:
                parameter.diskIdent = optimize_parameter['disk_ident'] = uuid.uuid4().hex.lower()
                parameter.lastSnapshot = list()
                last_snapshot_object = None
            else:
                if disk_cluster_info and (not self._cluster_disks['master_node']):
                    assert disk_cluster_info['disk_logic_ident']
                    assert not optimize_parameter.get('hash_files', None)
                    parameter.diskIdent = disk_cluster_info['disk_logic_ident']
                else:
                    parameter.diskIdent = optimize_parameter['disk_ident'] = last_snapshot_object.disk.ident
                parameter.lastSnapshot = last_snapshot_array

            if not optimize_parameter.get('hash_files', None):
                optimize_parameter = dict()

            if disk_cluster_info is None:  # 非集群盘
                parameter.snapshot.path = self.generate_parameters_image_path(physical_disk, last_snapshot_object)
            else:
                # 集群盘总是生成新的文件来存储
                parameter.snapshot.path = self.generate_parameters_image_path(physical_disk, None)

            if self._is_cdp and (not not_cluster_in_slave):
                self._fill_cdp_info_in_parameter(disk_cluster_info, parameter, system_infos)
            else:
                parameter.enableCDP = False

            include_range = self._fill_include_and_exclude(
                config, disk, exclude_ranges, linux_partitions, parameter,
                physical_disk, sys_os_class_type, system_infos)

            parameter.jsonConfig = json.dumps(config)

            if parameter.diskIdent in [p.diskIdent for p in parameters]:
                xlogging.raise_and_logging_error('重复的磁盘标识符',
                                                 'repeat disk ident found, disk ident:{}'.format(parameter.diskIdent))

            parameters.append(parameter)
            optimize_parameters.append(optimize_parameter)
            include_ranges.append(include_range)
            self.log_info('parameter {} : {}\n\tincludes\t{}\n\t{}'.format(
                len(parameters), parameter.__dict__, include_range, optimize_parameter))

        self.log_info('exclude:\t{}'.format(exclude_ranges))

        return parameters, include_ranges, exclude_ranges, optimize_parameters

    def analyze_cluster_slave_optimize_parameter(
            self, config, could_optimize, disk, optimize_parameter, physical_disk, disk_cluster_info):
        if disk_cluster_info['agent_force_full']:
            backup_type = xdata.BACKUP_TYPE_AGENT_FULL_STORE_FULL_WITHOUT_OPTIMIZE
            last_snapshot_object = None
            last_snapshot_array = list()
            config['empty_base_backup'] = True  # agent 全量备份，从节点不需要基础备份数据
        else:
            backup_type = xdata.BACKUP_TYPE_AGENT_INCREMENT_STORE_INCREMENT_WITH_OPTIMIZE
            if disk_cluster_info['snapshot_chain']:
                last_snapshot_object = DiskSnapshot.objects.get(ident=disk_cluster_info['snapshot_chain'][-1])
                assert not last_snapshot_object.is_cdp

                last_snapshot_array = list()
                for snapshot in disk_cluster_info['snapshot_chain']:
                    ds = DiskSnapshot.objects.get(ident=snapshot)
                    last_snapshot_array.append(IMG.ImageSnapshotIdent(ds.image_path, snapshot))
            else:
                last_snapshot_object = None
                last_snapshot_array = list()

        return backup_type, last_snapshot_array, last_snapshot_object

    def analyze_cluster_master_optimize_parameter(
            self, config, could_optimize, disk, optimize_parameter, physical_disk, disk_cluster_info):
        if disk_cluster_info['last_snapshot_ident']:
            last_snapshot_obj = DiskSnapshot.objects.get(ident=disk_cluster_info['last_snapshot_ident'])
            last_snapshot_array, last_snapshot_object, optimize_parameter['hash_files'] = \
                self._fetch_optimize_snapshots(last_snapshot_obj)
            assert last_snapshot_array
            assert last_snapshot_object
            assert optimize_parameter['hash_files']
        else:
            last_snapshot_obj = None
            last_snapshot_object = None
            last_snapshot_array = list()
            optimize_parameter['hash_files'] = list()

        if disk_cluster_info['agent_force_full']:
            if last_snapshot_obj is None:
                # 没有可用依赖链
                backup_type = xdata.BACKUP_TYPE_AGENT_FULL_STORE_FULL_WITHOUT_OPTIMIZE
            else:
                config['de_duplication'] = xdata.DEDUPLE_TYPE_CLIENT_WORK
                config['remove_duplicates_in_system_folder'] = False

                if self._force_store_full:
                    backup_type = xdata.BACKUP_TYPE_AGENT_FULL_STORE_FULL_WITH_OPTIMIZE
                    config['de_duplication'] |= (
                            xdata.DEDUPLE_TYPE_COPY_ALL_DATA_WRITE_2_NEW |
                            xdata.DEDUPLE_TYPE_HASH_VERIFY_BEFORE_WRITE
                    )
                    optimize_parameter['snapshots'] = list()
                    for snapshot in last_snapshot_array:
                        optimize_parameter['snapshots'].append({'path': snapshot.path, 'ident': snapshot.snapshot})
                else:
                    backup_type = xdata.BACKUP_TYPE_AGENT_FULL_STORE_INCREMENT
        else:
            assert last_snapshot_obj
            backup_type = xdata.BACKUP_TYPE_AGENT_INCREMENT_STORE_INCREMENT_WITH_OPTIMIZE
            config['de_duplication'] = xdata.DEDUPLE_TYPE_CLIENT_WORK
        return backup_type, last_snapshot_array, last_snapshot_object

    def _fill_include_and_exclude(self, config, disk, exclude_ranges, linux_partitions, parameter, physical_disk,
                                  sys_os_class_type, system_infos):
        include_range = {'diskIndex': parameter.diskIndex, 'diskIdent': parameter.diskIdent,
                         'diskSnapshot': parameter.snapshot.snapshot, 'ranges': list(),
                         'diskNativeGUID': disk['NativeGUID']}
        if sys_os_class_type == 'linux':
            linux_partitions = self.analyze_linux_partitions(linux_partitions, system_infos)
        if self._is_disk_exclude(disk):
            sectorOffset, sectors = 0, int(disk['DiskSize']) // 512
            exclude_ranges.append({
                "type": 'disk',
                "disk": disk,
                "sectorOffset": sectorOffset,
                "sectors": sectorOffset,
                'diskIdent': parameter.diskIdent,
                'partitions': list() if sys_os_class_type == 'windows' else self.get_linux_disk_partitions(
                    parameter.diskIndex, linux_partitions)
            })
            config['exclude'].append({
                "sectorOffset": str(sectorOffset),
                "sectors": str(sectors)
            })
        else:
            if sys_os_class_type == 'windows':
                for partition in disk['Partition']:
                    sectorOffset = int(partition['PartitionOffset']) // 512
                    sectors = int(partition['PartitionSize']) // 512
                    if self._is_partition_exclude_win(partition, disk):
                        exclude_ranges.append({
                            "type": 'partition',
                            "disk": disk,
                            "partition": partition,
                            "sectorOffset": sectorOffset,
                            "sectors": sectors,
                            'diskIdent': parameter.diskIdent
                        })
                        config['exclude'].append({
                            "sectorOffset": str(sectorOffset),
                            "sectors": str(sectors)
                        })
                    else:
                        include_range['ranges'].append(partition)

                if not self._remove_duplicates_in_system_folder and physical_disk['boot_device']:
                    config['remove_duplicates_in_system_folder'] = False
            elif sys_os_class_type == 'linux':
                for linux_partition in linux_partitions:
                    if parameter.diskIndex != linux_partition['DiskIndex']:
                        continue
                    sectorOffset = int(linux_partition['BytesStart']) // 512
                    sectors = (int(linux_partition['BytesEnd']) - int(linux_partition['BytesStart'])) // 512
                    if self._is_partition_exclude_linux(linux_partition, disk):
                        exclude_ranges.append({
                            "type": 'partition',
                            "disk": disk,
                            "partition": linux_partition,
                            "sectorOffset": sectorOffset,
                            "sectors": sectors,
                            'diskIdent': parameter.diskIdent
                        })
                        config['exclude'].append({
                            "sectorOffset": str(sectorOffset),
                            "sectors": str(sectors)
                        })
                    else:
                        include_range['ranges'].append(linux_partition)
        return include_range

    def _fill_cdp_info_in_parameter(self, disk_cluster_info, parameter, system_infos):
        try:
            agent_connect_ip = system_infos['ConnectAddress']['RemoteAddress']
        except (KeyError, TypeError):
            agent_connect_ip = None
        parameter.enableCDP = True
        if disk_cluster_info is None:
            parameter.cdpConfig.token = uuid.uuid4().hex.lower()
        else:
            parameter.cdpConfig.token = disk_cluster_info['cdp_token']
        parameter.cdpConfig.mode = self._cdp_mode_type
        parameter.cdpConfig.ip = boxService.get_tcp_kernel_service_ip(
            self._host_ext_info['local_ip'], agent_connect_ip)
        parameter.cdpConfig.port = boxService.get_tcp_kernel_service_port()
        parameter.cdpConfig.socketNumber = boxService.get_tcp_kernel_service_cdp_socket_number()
        parameter.cdpConfig.cacheMaxBytes = int(boxService.get_tcp_kernel_service_cdp_cache_bytes(
            self._host_ext_info.get('system_infos', {}).get('System', {}).get('PhysicalMemory', 0)))
        parameter.cdpConfig.netTimeouts = boxService.get_tcp_kernel_service_cdp_timeouts()

    def analyze_optimize_parameter(self, config, could_optimize, disk, optimize_parameter, physical_disk):
        last_snapshot_array, last_snapshot_object = self.generate_parameters_last_snapshot(physical_disk)
        _logger.info('HostBackupWorkProcessors disk:{} last_snapshot_array:{} last_snapshot_object:{}'.format(
            physical_disk['disk_ident'], last_snapshot_array, last_snapshot_object))
        if last_snapshot_object is None or self._force_agent_full:
            # Agent 进行完整备份
            if could_optimize:
                last_snapshot_object_from_agent = last_snapshot_object if (
                        (last_snapshot_object is not None) and (not last_snapshot_object.is_cdp)) else None
                last_snapshot_array, last_snapshot_object, optimize_parameter['hash_files'] = \
                    self.fetch_usable_snapshot_for_optimize(disk['NativeGUID'])
                if last_snapshot_object is None and last_snapshot_object_from_agent is not None:
                    last_snapshot_array, last_snapshot_object, optimize_parameter['hash_files'] = \
                        self._fetch_optimize_snapshots(last_snapshot_object_from_agent)
            else:
                last_snapshot_array, last_snapshot_object, optimize_parameter['hash_files'] = list(), None, list()
            if last_snapshot_object is None:
                # 没有可用依赖链
                backup_type = xdata.BACKUP_TYPE_AGENT_FULL_STORE_FULL_WITHOUT_OPTIMIZE
            else:
                config['de_duplication'] = xdata.DEDUPLE_TYPE_CLIENT_WORK
                config['remove_duplicates_in_system_folder'] = False

                if self._force_store_full:
                    backup_type = xdata.BACKUP_TYPE_AGENT_FULL_STORE_FULL_WITH_OPTIMIZE
                    config['de_duplication'] |= (
                            xdata.DEDUPLE_TYPE_COPY_ALL_DATA_WRITE_2_NEW |
                            xdata.DEDUPLE_TYPE_HASH_VERIFY_BEFORE_WRITE
                    )
                    optimize_parameter['snapshots'] = list()
                    for snapshot in last_snapshot_array:
                        optimize_parameter['snapshots'].append({'path': snapshot.path, 'ident': snapshot.snapshot})
                else:
                    backup_type = xdata.BACKUP_TYPE_AGENT_FULL_STORE_INCREMENT
        else:
            # Agent 进行增量备份
            if could_optimize and (not last_snapshot_object.is_cdp):  # 客户端记录的最后一次备份可以定位到CDP文件，就不做流量优化
                temp_last_snapshot_array, temp_last_snapshot_object, optimize_parameter['hash_files'] = \
                    self.fetch_last_snapshot_for_optimize(disk['NativeGUID'], last_snapshot_object)
                if temp_last_snapshot_object is None:
                    temp_last_snapshot_array, temp_last_snapshot_object, optimize_parameter['hash_files'] = \
                        self._fetch_optimize_snapshots(last_snapshot_object)
            else:
                temp_last_snapshot_array, temp_last_snapshot_object, optimize_parameter['hash_files'] = \
                    list(), None, list()
            if temp_last_snapshot_object is not None:
                backup_type = xdata.BACKUP_TYPE_AGENT_INCREMENT_STORE_INCREMENT_WITH_OPTIMIZE
                last_snapshot_array = temp_last_snapshot_array
                last_snapshot_object = temp_last_snapshot_object
                config['de_duplication'] = xdata.DEDUPLE_TYPE_CLIENT_WORK
            else:
                backup_type = xdata.BACKUP_TYPE_AGENT_INCREMENT_STORE_INCREMENT_WITHOUT_OPTIMIZE
                optimize_parameter['hash_files'] = list()
        return backup_type, last_snapshot_array, last_snapshot_object

    def check_physical_disk(self, physical_disk):
        if physical_disk['disk_status'] == BoxLogic.DiskStatus.LastSnapshotIsNormal.value:
            if (physical_disk['last_snapshot_ident'] in ['', 'invalid']) \
                    or (physical_disk['disk_ident'] in ['', 'invalid']):
                xlogging.raise_and_logging_error(
                    r'磁盘状态异常，最后一次备份信息未同步', self.add_msg_prefix(
                        r'check_physical_disk failed. last_snapshot_ident : {} disk_ident : {}'.format(
                            physical_disk['last_snapshot_ident'], physical_disk['disk_ident'])))

    def check_physical_disk_cdp(self, physical_disk):
        if physical_disk['disk_status'] == BoxLogic.DiskStatus.LastSnapshotIsCDP.value:
            if physical_disk['disk_cdpSnapshot_token'] in ['', 'invalid']:
                xlogging.raise_and_logging_error(
                    r'磁盘状态异常，最后一次CDP信息未同步', self.add_msg_prefix(
                        r'check_physical_disk_cdp failed. token : [{}]'.format(
                            physical_disk['disk_cdpSnapshot_token'])))

            if (physical_disk['disk_cdpSnapshot_set_by_restore']) and (
                    (physical_disk['disk_cdpSnapshot_seconds'] in [0, -1]) or (
                    physical_disk['disk_cdpSnapshot_microseconds'] < 0) or (
                            physical_disk['disk_cdpSnapshot_microseconds'] > 999999)):
                xlogging.raise_and_logging_error(
                    r'磁盘状态异常，最后一次CDP时间未同步', self.add_msg_prefix(
                        r'check_physical_disk_cdp failed. token : [{}] seconds:{}  microseconds:{}'.format(
                            physical_disk['disk_cdpSnapshot_token'], physical_disk['disk_cdpSnapshot_seconds'],
                            physical_disk['disk_cdpSnapshot_microseconds'])))

    def generate_parameters_last_snapshot(self, physical_disk):
        disk_status = physical_disk['disk_status']
        validator_list = [GetSnapshotList.is_disk_snapshot_object_exist,
                          GetSnapshotList.is_disk_snapshot_file_exist,
                          GetSnapshotList.is_disk_snapshot_object_finished,
                          GetSnapshotList.is_disk_snapshot_object_successful
                          ]
        # 备份时候，最后一个点是cdp且即将删除不要依赖 #3274
        validator_list_first = [GetSnapshotList.is_schedule_valid,
                                GetSnapshotList.is_cdp_valid]
        result = list()
        last_snapshot_object = None

        if disk_status == BoxLogic.DiskStatus.NotExistLastSnapshot.value:
            pass  # do nothing
        elif disk_status == BoxLogic.DiskStatus.LastSnapshotIsNormal.value:
            last_snapshot_object = self.query_last_disk_snapshot_with_normal(physical_disk['last_snapshot_ident'])
            result = GetSnapshotList.query_snapshots_by_snapshot_object(
                last_snapshot_object, validator_list, None, validator_list_first)
        elif disk_status == BoxLogic.DiskStatus.LastSnapshotIsCDP.value:
            last_snapshot_object, timestamp = self.query_last_disk_snapshot_with_cdp(
                physical_disk['disk_cdpSnapshot_set_by_restore'], physical_disk['disk_cdpSnapshot_token'],
                physical_disk['disk_cdpSnapshot_seconds'], physical_disk['disk_cdpSnapshot_microseconds'])
            result = GetSnapshotList.query_snapshots_by_snapshot_object(
                last_snapshot_object, validator_list, timestamp, validator_list_first)
        else:  # never happen
            xlogging.raise_and_logging_error(r'内部异常，无效的磁盘状态',
                                             r'generate_parameters_last_snapshot failed. disk_status : {}'.format(
                                                 disk_status))

        if len(result) == 0 or len(result) > xdata.SNAPSHOT_FILE_CHAIN_MAX_LENGTH:
            _logger.warning(r'disk[{}] snapshot chain is:{}, max chain is:{} '.format(
                physical_disk['disk_ident'], len(result), xdata.SNAPSHOT_FILE_CHAIN_MAX_LENGTH))
            return list(), None
        else:
            return result, last_snapshot_object

    @staticmethod
    def _used_by_finished_restore(last_snapshot_object):
        for rtd in RestoreTargetDisk.objects.filter(snapshot=last_snapshot_object).all():
            pe_host = rtd.pe_host
            if pe_host.is_finished and pe_host.successful and pe_host.restore_task_object is not None:
                return True
        else:
            return False

    # 找到最近可用的主机快照，适用于源主机做全量备份
    def fetch_usable_snapshot_for_optimize(self, disk_native_guid):
        self.log_info('fetch_usable_snapshot_for_optimize start disk_native_guid:{}'.format(disk_native_guid))
        exclude_ids = list()
        search_counts = 1
        latest_host_snapshot = self._get_latest_host_snapshot(exclude_ids)
        while latest_host_snapshot and (search_counts < 10000):
            last_disk_snapshot = GetDiskSnapshot.get_by_native_guid_and_host_snapshot(disk_native_guid,
                                                                                      latest_host_snapshot)
            if not last_disk_snapshot:
                exclude_ids.append(latest_host_snapshot.id)
                latest_host_snapshot = self._get_latest_host_snapshot(exclude_ids)
                search_counts += 1
                continue
            snapshots, last_snapshot_with_hash, hash_files = self._fetch_optimize_snapshots(last_disk_snapshot)
            if not snapshots:
                exclude_ids.append(latest_host_snapshot.id)
                latest_host_snapshot = self._get_latest_host_snapshot(exclude_ids)
                search_counts += 1
                continue
            else:
                break
        else:
            snapshots, last_snapshot_with_hash, hash_files = list(), None, list()
        self.log_info('fetch_usable_snapshot_for_optimize end disk_native_guid:{} search_counts:{} snapshots:{}'.format(
            disk_native_guid, search_counts, snapshots))
        return snapshots, last_snapshot_with_hash, hash_files

    # 找到最新的主机快照，适用于源主机做增量备份
    def fetch_last_snapshot_for_optimize(self, disk_native_guid, last_snapshot_object):
        self.log_info('fetch_last_snapshot_for_optimize start disk_native_guid:{}'.format(disk_native_guid))

        # 当agent记录的点是一个有过成功还原的本机的快照点，那么就不做优化 issue 4066
        if HostBackupWorkProcessors._used_by_finished_restore(last_snapshot_object):
            return list(), None, list()

        latest_host_snapshot = self._get_latest_host_snapshot(list())
        if not latest_host_snapshot:
            self.log_info('fetch_last_snapshot_for_optimize not find latest_host_snapshot,disk_native_guid:{}'.format(
                disk_native_guid))
            return list(), None, list()

        last_disk_snapshot_in_aio = GetDiskSnapshot.get_by_native_guid_and_host_snapshot(disk_native_guid,
                                                                                         latest_host_snapshot)
        if not last_disk_snapshot_in_aio:
            self.log_info('fetch_last_snapshot_for_optimize not find last_disk_snapshot_in_aio,disk_native_guid:{},'
                          'latest_host_snapshot:{}'.format(disk_native_guid, latest_host_snapshot))
            return list(), None, list()

        snapshots, last_snapshot_with_hash, hash_files = self._fetch_optimize_snapshots(last_disk_snapshot_in_aio)
        self.log_info(
            'fetch_last_snapshot_for_optimize end disk_native_guid:{} snapshots:{}'.format(disk_native_guid, snapshots))
        return snapshots, last_snapshot_with_hash, hash_files

    def _fetch_optimize_snapshots(self, last_disk_snapshot):
        validator_list = [GetSnapshotList.is_disk_snapshot_object_exist,
                          GetSnapshotList.is_disk_snapshot_file_exist,
                          GetSnapshotList.is_disk_snapshot_object_finished,
                          ]
        # 备份时候，最后一个点是cdp且即将删除不要依赖 #3274
        validator_list_first = [GetSnapshotList.is_schedule_valid,
                                GetSnapshotList.is_cdp_valid,
                                GetSnapshotList.fix_disk_snapshot_hash_file]  # 对第一个点修复hash文件，防止刚备份完又备份没有流量优化
        if self._force_optimize:
            validator_list.append(GetSnapshotList.fix_disk_snapshot_hash_file)
        validator_list.append(GetSnapshotList.is_disk_snapshot_hash_file_exists)

        snapshots, hash_files = GetSnapshotList.query_snapshots_by_snapshot_object_with_hash_file(
            last_disk_snapshot, validator_list, validator_list_first)
        if snapshots:
            last_snapshot_with_hash = last_disk_snapshot
        else:
            last_snapshot_with_hash = None
        hash_files.reverse()  # 最新的排在最前
        return snapshots, last_snapshot_with_hash, hash_files

    def _get_latest_host_snapshot(self, exclude_ids):
        return HostSnapshot.objects.filter(host=self._host_object,
                                           deleted=False,
                                           deleting=False,
                                           start_datetime__isnull=False,
                                           finish_datetime__isnull=False,
                                           successful=True
                                           ).exclude(id__in=exclude_ids).order_by('-start_datetime').first()

    def run_optimize_parameters_before_backup(self, parameters, optimize_parameters):
        stop_optimize_parameters = list()
        for optimize_parameter in optimize_parameters:
            if not optimize_parameter:
                continue
            result = json.loads(boxService.box_service.startBackupOptimize(json.dumps(optimize_parameter)))
            if result['nbd_object_uuid']:
                self._update_parameters_json_config(parameters,
                                                    optimize_parameter['disk_ident'],
                                                    result['nbd_device_path']
                                                    )
            stop_optimize_parameters.append({
                'nbd_object_uuid': result['nbd_object_uuid'],
                'hash_file_path': result['hash_file_path'],
                'nbd_device_path': result['nbd_device_path']
            })
        return stop_optimize_parameters

    def _update_parameters_json_config(self, parameters, disk_ident, nbd_path):
        for parameter in parameters:
            if parameter.diskIdent == disk_ident:
                config = json.loads(parameter.jsonConfig)
                config['pre_data_device'] = nbd_path
                parameter.jsonConfig = json.dumps(config)
                break
        else:
            xlogging.raise_and_logging_error(r'内部异常，无效的磁盘编号',
                                             r'_update_parameters_json_config failed parameters:{} disk_ident:{}, '
                                             r'nbd_path:{}'.format(parameters, disk_ident, nbd_path))

    def query_last_disk_snapshot_with_normal(self, last_snapshot_ident):
        try:
            last_disk_snapshot = DiskSnapshot.objects.get(ident=last_snapshot_ident)
            return last_disk_snapshot
        except DiskSnapshot.DoesNotExist:
            self.log_warnning(
                r'查询上次快照点状态失败，不存在的快照点 query_last_disk_snapshots failed.'
                r' DiskSnapshot.DoesNotExist. : {}'.format(last_snapshot_ident))
            return None

    def query_last_disk_snapshot_with_cdp(self, set_by_restore, token, seconds, microseconds):
        if set_by_restore:  # token 为 DiskSnapshot 的 ident
            try:
                snapshot = DiskSnapshot.objects.get(ident=token)
                if not snapshot.is_cdp:
                    self.log_warnning(
                        r'查询上次CDP快照点状态失败，DiskSnapshot快照点类型无效 query_last_disk_snapshot_with_cdp failed. '
                        r'ident : {}'.format(token))
                    return None, None
                timestamp = float(r'{}.{:06d}'.format(seconds, microseconds))
                return snapshot, timestamp
            except DiskSnapshot.DoesNotExist:
                self.log_warnning(
                    r'查询上次CDP快照点状态失败，不存在的DiskSnapshot快照点 query_last_disk_snapshot_with_cdp failed. '
                    r'DiskSnapshot.DoesNotExist. : {}'.format(token))
                return None, None
        else:  # token 为 CDPDiskToken 的token
            cdp_token_object = CDPDiskToken.objects.filter(token=token).first()
            if cdp_token_object is None:
                self.log_warnning(
                    r'查询上次CDP快照点状态失败，不存在的CDPDiskToken快照点 query_last_disk_snapshot_with_cdp failed. '
                    r'RestoreTargetDisk.DoesNotExist. : {}'.format(token))
                return None, None

            snapshot = DiskSnapshot.objects.filter(cdp_info__token__token=token).order_by('-id').first()
            if snapshot is None:
                self.log_warnning(r'上次CDP快照点没有CDP数据，使用普通快照点 {}'.format(token))
                snapshot = cdp_token_object.parent_disk_snapshot
            return snapshot, None

    # 生成快照文件路径：如/home/aio/+ images + hostident
    def generate_parameters_new_image_path(self, file_type='.qcow'):
        folder_path = boxService.box_service.pathJoin([self._storage_node_base_path, 'images', self._host_object.ident])
        boxService.box_service.makeDirs(folder_path)
        return boxService.box_service.pathJoin([folder_path, uuid.uuid4().hex.lower() + file_type])

    def generate_parameters_image_path(self, physical_disk, last_snapshot_object):
        if last_snapshot_object is None:  # 没有上次快照信息，为基础备份，需要新生成存储文件
            generate_new = True
        else:
            host_snapshot_object = GetSnapshotList.get_host_snapshot_by_disk_snapshot(last_snapshot_object)

            if physical_disk['disk_bytes'] != last_snapshot_object.bytes:  # 磁盘大小发生变化，需要新生成存储文件
                generate_new = True
            elif host_snapshot_object.host.ident != self._host_object.ident:  # 主机ident发生变化，需要新生成存储文件
                generate_new = True
            elif last_snapshot_object.is_cdp:  # 上次备份是CDP备份点，需要新生成存储文件
                generate_new = True
            elif not last_snapshot_object.image_path.startswith(self._storage_node_base_path):  # 备份的存储结点有变化
                generate_new = True
            else:  # 不需要新生成
                generate_new = False

        if generate_new:
            return self.generate_parameters_new_image_path()
        else:
            return last_snapshot_object.image_path

    @staticmethod
    def disk_name(system_infos, disk_num):
        return {disk['DiskNum']: HostBackupWorkProcessors.get_disk_name(disk) for disk in system_infos['Disk']}[
            str(disk_num)]

    @staticmethod
    def get_disk_name(disk):
        if disk.get('DiskIndex', ''):
            disk_index = disk['DiskIndex']
            if disk.get('DiskMapperName', ''):
                return '{}[{},{}]'.format(disk['DiskName'], disk_index, disk['DiskMapperName'])
            else:
                return '{}[{}]'.format(disk['DiskName'], disk_index)
        else:
            return disk['DiskName']

    @staticmethod
    def query_current_hardware_info(host_ident, hardware_name):
        ret_info, inputParam = dict(), dict()
        if hardware_name in ['net', 'SCSIAdapter', 'HDC']:  # class name：硬件信息
            inputParam = {"GetAgHostClassHWInfo": {"classname": hardware_name, "parentLevel": 255}}
            ret_info = {'HWInfo': []}
        elif hardware_name == 'NetAdapterInfo':  # 网络适配器信息
            inputParam = {"GetAgHostNetAdapterInfo": 1}
            ret_info = {'NetInfo': []}
        elif hardware_name == 'RouteInfo':  # 路由信息
            inputParam = {"GetAgHostRouteInfo": 1}
            ret_info = {'RouteInfo': []}
        else:
            xlogging.raise_and_logging_error('查询硬件信息失败：无效的参数' + hardware_name, 'invalid arg: ' + hardware_name)

        host_hardware = boxService.box_service.getHostHardwareInfo(host_ident, json.dumps(inputParam))  # 'null', '{}'
        try:
            host_hardware = json.loads(host_hardware)
            return host_hardware if host_hardware else ret_info
        except Exception as e:
            _logger.warning(r'query_current_hardware_info failed. {} | {} - {}'.format(e, host_ident, hardware_name))
            return ret_info

    def update_database(self, parameters, physical_disk_objects, system_infos, include_ranges, exclude_ranges,
                        efi_boot_data, stop_optimize_parameters):
        self._status_display = (r'生成快照点信息', 'TASK_STEP_IN_PROGRESS_BACKUP_GENERATE_INFO')

        host_ident = self._host_object.ident

        host_snapshot_ext = json.loads(self._host_snapshot.ext_info)
        host_snapshot_ext['system_infos'] = system_infos
        host_snapshot_ext['include_ranges'] = include_ranges
        host_snapshot_ext['exclude_ranges'] = exclude_ranges
        host_snapshot_ext['efi_boot_entry'] = efi_boot_data
        host_snapshot_ext['stop_optimize_parameters'] = stop_optimize_parameters

        # 获取磁盘控制器，网络适配器的硬件信息，关联到对应Hostsnapshot
        network_adapter_infos = self.query_current_hardware_info(host_ident, 'NetAdapterInfo')['NetInfo']
        route_infos = self.query_current_hardware_info(host_ident, 'RouteInfo')['RouteInfo']
        network_controller_hardware = self.query_current_hardware_info(host_ident, 'net')['HWInfo']
        disk_controller_hardware = self.query_current_hardware_info(host_ident, 'SCSIAdapter')['HWInfo']
        disk_controller_hardware += self.query_current_hardware_info(host_ident, 'HDC')['HWInfo']
        host_snapshot_ext['network_controller_hardware'] = network_controller_hardware
        host_snapshot_ext['disk_controller_hardware'] = disk_controller_hardware
        host_snapshot_ext['network_adapter_infos'] = network_adapter_infos
        host_snapshot_ext['route_infos'] = route_infos

        host_snapshot_ext['new_snapshot_ident'] = list()
        host_snapshot_ext['disk_index_info'] = list()

        for parameter in parameters:
            physical_disk = self.get_physical_disk(parameter.diskIndex, physical_disk_objects)
            disk_object = self.get_disk_object(parameter.diskIdent)
            parent_snapshot, parent_timestamp = self.get_snapshot_object(parameter.lastSnapshot, physical_disk)

            host_snapshot_ext['disk_index_info'].append({
                'snapshot_disk_index': int(parameter.diskIndex),
                'snapshot_disk_ident': parameter.snapshot.snapshot,
                'boot_device': physical_disk['boot_device'],
                'is_system': physical_disk['is_system'],
                'is_bmf': physical_disk['is_bmf']
            })

            normal_snapshot_object = DiskSnapshot.objects.create(disk=disk_object,
                                                                 parent_snapshot=parent_snapshot,
                                                                 image_path=parameter.snapshot.path,
                                                                 ident=parameter.snapshot.snapshot,
                                                                 host_snapshot=self.host_snapshot,
                                                                 bytes=parameter.diskByteSize,
                                                                 type=physical_disk['disk_type'],
                                                                 boot_device=physical_disk['boot_device'],
                                                                 parent_timestamp=parent_timestamp,
                                                                 display_name=self.disk_name(system_infos,
                                                                                             parameter.diskIndex)
                                                                 )
            if parameter.enableCDP and (not self._is_cluster_disk_token(parameter.cdpConfig.token)):
                CDPDiskToken.objects.create(parent_disk_snapshot=normal_snapshot_object,
                                            token=parameter.cdpConfig.token,
                                            task=CDPTask.objects.get(id=self._cdp_task_object_id)
                                            )

            host_snapshot_ext['new_snapshot_ident'].append(normal_snapshot_object.ident)

        self._host_snapshot.ext_info = json.dumps(host_snapshot_ext)
        self._host_snapshot.save(update_fields=['ext_info'])

    @staticmethod
    def _is_cluster_disk_token(token):
        return 0 != ClusterTokenMapper.objects.filter(agent_token=token).count()

    def get_physical_disk(self, index, physical_disk_objects):
        for physical_disk in physical_disk_objects:
            if physical_disk['index'] == index:
                return physical_disk

        xlogging.raise_and_logging_error('内部异常，代码2343',
                                         self.add_msg_prefix(r'get_physical_disk never happen. index {}'.format(index)))

    @staticmethod
    def get_disk_object(disk_ident):
        try:
            return Disk.objects.get(ident=disk_ident)
        except Disk.DoesNotExist:
            return Disk.objects.create(ident=disk_ident)

    @staticmethod
    def get_snapshot_object(lastSnapshot, physical_disk):
        if len(lastSnapshot) == 0:
            return None, None
        else:
            parent_timestamp = None

            if DiskSnapshot.is_cdp_file(lastSnapshot[-1].path):
                # CDP快照文件是唯一的
                disk_snapshot_object = DiskSnapshot.objects.get(image_path=lastSnapshot[-1].path)
                if physical_disk['disk_cdpSnapshot_set_by_restore']:
                    parent_timestamp = float(
                        r'{}.{:06d}'.format(physical_disk['disk_cdpSnapshot_seconds'],
                                            physical_disk['disk_cdpSnapshot_microseconds']))
            else:
                # 普通快照的名称是唯一的
                disk_snapshot_object = DiskSnapshot.objects.get(ident=lastSnapshot[-1].snapshot)

            return disk_snapshot_object, parent_timestamp

    @staticmethod
    def get_shell_infos_from_schedule_or_host(host_object, schedule_object):
        if schedule_object is None:
            ext_info = json.loads(host_object.ext_info)
        else:
            ext_info = json.loads(schedule_object.ext_config)

        if 'shellInfoStr' not in ext_info:
            return None

        return json.loads(ext_info['shellInfoStr'])

    @staticmethod
    def is_host_windows(host_ident):
        host = Host.objects.get(ident=host_ident)
        system_infos = json.loads(host.ext_info)['system_infos']
        return 'LINUX' not in system_infos['System']['SystemCaption'].upper()

    def start_run_in_client(self, shell_infos, host_ident):
        ins = ClientIpMg.SendCompressAndRunInClient()
        cmd = {
            'AppName': shell_infos['exe_name'], 'param': shell_infos['params'], 'workdir': shell_infos['work_path'],
            'unzip_dir': shell_infos['unzip_path'], 'timeout_sec': None, 'username': None, 'pwd': None,
            'serv_zip_full_path': shell_infos['zip_path']
        }
        _logger.info('start_run_in_client cmd: {}'.format(cmd))
        ins.exec_one_cmd(host_ident, cmd, self.is_host_windows(host_ident))
        _logger.info('start_run_in_client, No Exception Occur')

    def run_script_before_backup(self, host_object, schedule_object):
        shell_infos = self.get_shell_infos_from_schedule_or_host(host_object, schedule_object)
        if shell_infos is None:
            return
        try:
            self.start_run_in_client(shell_infos, host_object.ident)
        except Exception as e:
            errorMsg = 'run shell in client failed:\r\n{0}\r\n{1}'.format(shell_infos, e)
            if shell_infos['ignore_shell_error']:
                _logger.warning(errorMsg)
            else:
                raise Exception(errorMsg)

    def send_backup_command(self, parameters, kilo_bytes_per_second, additional_cmd):
        self._status_display = (r'发送任务指令', 'TASK_STEP_IN_PROGRESS_BACKUP_SEND_COMMAND')
        result = boxService.box_service.backup(self._host_object.ident, parameters, kilo_bytes_per_second,
                                               additional_cmd)
        assert len(result) == 4
        if result[0] == 1:
            raise CreateSnapshotImageError(result[1], result[2], result[3])
        self._status_display = (r'发送任务指令完成，等待客户端上传数据', 'TASK_STEP_IN_PROGRESS_BACKUP_WAIT_RESTART')

    def _is_partition_exclude_win(self, partition, disk):
        for _entry in self._exclude:
            if _entry['exclude_type'] != xdata.BACKUP_TASK_SCHEDULE_EXCLUDE_VOLUME:
                continue
            info = _entry['exclude_info']
            is_letter = info.upper() if (':' not in info) else None
            if is_letter:
                if ('?' in info) and (partition['VolumeName'].upper() == is_letter):
                    _logger.warning(r'_is_partition_exclude 0 : {}'.format(partition))
                    return True
                elif partition['Letter'] and (partition['Letter'].upper() == is_letter):
                    _logger.warning(r'_is_partition_exclude 1 : {}'.format(partition))
                    return True
            else:
                is_offset = info.split(':')
                if (len(is_offset) == 3) and (is_offset[0] == disk['NativeGUID']) and \
                        (is_offset[1] == partition['PartitionOffset']) and \
                        (is_offset[2] == partition['PartitionSize']):
                    _logger.warning(r'_is_partition_exclude 2 : {}'.format(partition))
                    return True
        return False

    def _is_partition_exclude_linux(self, linux_partition, disk):
        for _entry in self._exclude:
            if _entry['exclude_type'] != xdata.BACKUP_TASK_SCHEDULE_EXCLUDE_VOLUME:
                continue
            info = _entry['exclude_info']
            is_device = (':' not in info)
            if is_device:
                if (linux_partition['VolumeDevice'] == info) and \
                        (int(disk['DiskNum']) == linux_partition["DiskIndex"]):
                    _logger.warning(r'_is_partition_exclude_linux 1 : {}'.format(linux_partition))
                    return True
            else:
                is_offset = info.split(':')
                if (len(is_offset) == 3) and (is_offset[0] == disk['NativeGUID']) and \
                        (is_offset[1] == linux_partition['BytesStart']) and \
                        (int(is_offset[2]) == int(linux_partition['BytesEnd']) - int(linux_partition['BytesStart'])):
                    _logger.warning(r'_is_partition_exclude_linux 2 : {}'.format(linux_partition))
                    return True
        return False

    def _is_disk_exclude(self, disk):
        for _entry in self._exclude:
            if _entry['exclude_type'] != xdata.BACKUP_TASK_SCHEDULE_EXCLUDE_DISK:
                continue
            if disk['NativeGUID'] == _entry['exclude_info']:
                _logger.warning(r'_is_disk_exclude : {}'.format(disk))
                return True
        return False

    @staticmethod
    @xlogging.convert_exception_to_value(None)
    def forceCloseBackupFiles(files):
        boxService.box_service.forceCloseBackupFiles(files)

    # 判断是否能够流量优化
    def _check_if_could_optimize(self, client_ver_str):
        if self._disable_optimize:
            _logger.warning('_check_if_could_optimize disable optimize by api!')
            return False

        if boxService.box_service.isFileExist(xdata.DISABLE_DEDUP_FLAG_FILE):
            _logger.warning('_check_if_could_optimize disable optimize by flag file!')
            return False

        client_ver_date = self._convert_ver_string_to_date(client_ver_str)
        deadline = datetime(2018, 1, 12)
        if client_ver_date and client_ver_date >= deadline:
            return True
        else:
            _logger.warning(
                '_check_if_could_optimize disable optimize by client version, client_ver_str:{} deadline:{}'.format(
                    client_ver_str, deadline))
            return False

    @staticmethod
    @xlogging.convert_exception_to_value(None)
    def _convert_ver_string_to_date(client_ver_str):
        # "client_ver_str": "2.0.180105"
        time_str = client_ver_str.split('.')[-1]
        year, month, day = int(time_str[0:2]), int(time_str[2:4]), int(time_str[4:6])
        return datetime(2000 + year, month, day)

    def _check_if_could_hash_disk_data(self):
        if isinstance(self._schedule_object, ClusterBackupSchedule):
            return True
        else:
            if boxService.box_service.isFileExist(xdata.DISABLE_HASH_DISK_DATA_FLAG_FILE):
                return False
            else:
                return True


def file_backup_api(action, key, info):
    rev = json.loads(boxService.box_service.fileBackup(json.dumps({'action': action, 'key': key, 'info': info})))
    return rev['result']


class FileBackupWorkProcessors(HostBackupWorkProcessors):

    def __init__(self, task_name, host_object, backup_task, force_agent_full=False, storage_node_ident=None,
                 schedule_object=None,
                 force_store_full=False, disable_optimize=False):
        self.task = backup_task
        self._schedule_object = schedule_object
        self._host_object = host_object
        self._task_name = task_name
        self._force_agent_full = force_agent_full
        self._force_store_full = force_store_full
        self._disable_optimize = disable_optimize

        self._storage_node_base_path = StorageNode.objects.get(ident=storage_node_ident).path if storage_node_ident \
            else HostBackupWorkProcessors._get_storage_node_path_with_user_max_free_quota(self._host_object.user)
        self.task_content = {
            'error': '',
            'tmp_files': list(),
            'lock_name': 'file_backup_{}'.format(self.task.id),
            'backup_params': {
                'name': self._task_name,
                'disksnapshots': list(),
                'temp_qcow': '',
                'diskbytes': -1,
                'host_ident': self.task.schedule.host.ident,
                'aio_server_ip': '172.29.16.2',  # todo
                'task_uuid': self.task.task_uuid,
            }
        }
        self.task.set_status(FileBackupTask.INIT)
        self._host_snapshot = HostSnapshot.objects.create(host=self._host_object, schedule=schedule_object,
                                                          display_status=r'命令初始化中', partial=True)
        self.task.set_status(FileBackupTask.FIND_SNAPSHOTS)
        last_host_snapshot = self._query_last_host_snapshot()
        last_snapshots, last_disk_snapshot = self._fetch_snapshots(last_host_snapshot)
        disk_snapshot = self._create_disk_snapshots(last_disk_snapshot, self._host_snapshot)
        self._update_host_snapshot_ext_info(disk_snapshot, self._host_snapshot)
        self.task_content['backup_params']['disksnapshots'] = [{'path': snapshot.path, 'ident': snapshot.snapshot}
                                                               for snapshot in last_snapshots]
        self.task_content['backup_params']['disksnapshots'].append(
            {'path': disk_snapshot.image_path, 'ident': disk_snapshot.ident})
        self.task_content['backup_params']['diskbytes'] = disk_snapshot.bytes
        nas = os.path.join(NAS_QCOW_PATH, 'tmp')
        os.makedirs(nas, exist_ok=True)
        temp_qcow = os.path.join(nas, '{}.fbk.tmp'.format(disk_snapshot.ident))
        self.task_content['backup_params']['temp_qcow'] = temp_qcow
        self.task_content['tmp_files'].append(temp_qcow)
        self.task_content['backup_params'].update(self._load_nas_params())

    def _load_nas_params(self):
        ext_info = json.loads(self.task.schedule.ext_config)
        rs = dict()
        rs['nas_type'] = ext_info['nas_protocol']
        if isinstance(ext_info['nas_exclude_dir'], str):
            rs['nas_excludes'] = ext_info['nas_exclude_dir'].split(';')
        else:
            rs['nas_excludes'] = ext_info['nas_exclude_dir']
        rs['nas_excludes'] = [r for r in rs['nas_excludes'] if r]
        rs['nas_path'] = ext_info['nas_path']
        rs['nas_user'] = ext_info['nas_username']
        rs['nas_pwd'] = ext_info['nas_password']
        rs['enum_threads'] = ext_info.get('enum_threads', 2)
        rs['enum_queue_maxsize'] = ext_info.get('enum_queue_maxsize', 1)
        rs['enum_level'] = ext_info.get('enum_level', 4)
        rs['sync_threads'] = ext_info.get('sync_threads', 4)
        rs['net_limit'] = ext_info.get('net_limit', -1)
        rs['sync_queue_maxsize'] = ext_info.get('sync_queue_maxsize', 256)
        rs['cores'] = ext_info.get('cores', 2)
        rs['memory_mbytes'] = ext_info.get('memory_mbytes', 512)
        return rs

    @staticmethod
    def _update_host_snapshot_ext_info(disk_snapshot, host_snapshot):
        NativeGUID = '7fed0c00000000000000000000004321'
        mountPoint = '/'
        ext_info = json.loads(host_snapshot.ext_info)
        ext_info['system_infos'] = {
            "System": {
                "BuildNumber": "Linux-2.6.32-358.el6.x86_64-x86_64-with-centos-6.4-Final",
                "SystemCaption": "Linux CentOS 6.4 Final",
                "WorkGroup": "",
                "ServicePack": "",
                "PhysicalMemory": "1968603136",
                "ProcessorArch": "x86_64",
                "ComputerName": "clerware",
                "SystemCatName": "64bit ELF",
                "ProcessorInfo": "Intel(R) Xeon(R) CPU           E5520  @ 2.27GHz",
                "version": "2.0.181106"
            },
            "Disk": [{
                "DiskName": "VMware Virtual disk",
                "Partition": [],
                "DiskSize": disk_snapshot.bytes,
                "Style": "mbr",
                "DiskMapperName": "",
                "NativeGUID": NativeGUID,
                "DiskIndex": "/dev/sdb",
                "DiskNum": "0"
            }],
            "Storage": {
                "vgs": [{
                    "pvs": [{
                        "name": "/dev/sdb",
                        "uuid": "GtfN4x-7dms-aHvV-DSGF-KHP1-DZkV-rQrNU6"
                    }],
                    "lvs": [{
                        "mountPoint": mountPoint,
                        "used_bytes": "",
                        "mountOpts": "rw",
                        "uuid": "",
                        "fileSystem": "xfs",
                        "free_bytes": "",
                        "total_bytes": "",
                        "diskRanges": [{
                            "index": "0",
                            "sector_count": "",
                            "start_sector": "",
                            "sector_size": "512",
                            "device": "/dev/sdb"
                        }],
                        "name": "filebackup"
                    }
                    ],
                    "name": "644121ca",
                    "uuid": ""
                }],
                "disks": [{
                    "style": "mbr",
                    "index": 0,
                    "partitions": [],
                    "device": "/dev/sdb",
                    "bytes": disk_snapshot.bytes
                }]
            }
        }
        ext_info['include_ranges'] = [{
            "ranges": [],
            "diskIdent": disk_snapshot.disk.ident,
            "diskNativeGUID": NativeGUID,
            "diskIndex": 0,
            "diskSnapshot": disk_snapshot.ident
        }]
        ext_info['disk_index_info'] = [{
            "boot_device": False,
            "snapshot_disk_index": 0,
            "is_system": False,
            "is_bmf": False,
            "snapshot_disk_ident": disk_snapshot.ident
        }]
        ext_info['exclude_ranges'] = []
        host_snapshot.ext_info = json.dumps(ext_info)
        host_snapshot.save(update_fields=['ext_info'])

    def _query_last_host_snapshot(self):
        if self._force_agent_full:
            return None
        else:
            from apiv1.remote_backup_logic_remote import RemoteBackupHelperRemote
            query_set = RemoteBackupHelperRemote.query_host_snapshot_order_by_time(self.task.schedule.host.ident)
            latest_snapshot = query_set.exclude(partial=True).last()
            if not latest_snapshot:
                _logger.warning('_fetch_latest_snapshot not exists, latest snapshot')
                return None
            return latest_snapshot

    def generate_parameters_image_path(self, last_snapshot_object):
        if last_snapshot_object is None:  # 没有上次快照信息，为基础备份，需要新生成存储文件
            generate_new = True
        else:
            if not last_snapshot_object.image_path.startswith(self._storage_node_base_path):  # 备份的存储结点有变化
                generate_new = True
            else:  # 不需要新生成
                generate_new = False

        if generate_new:
            return self.generate_parameters_new_image_path()
        else:
            return last_snapshot_object.image_path

    def _create_disk_snapshots(self, last_disk_snapshot, host_snapshot):
        ext_config = json.loads(self._schedule_object.ext_config)
        image_path = self.generate_parameters_image_path(last_disk_snapshot)
        ident = uuid.uuid4().hex
        if last_disk_snapshot:
            disk_snapshot = DiskSnapshot.objects.create(disk=last_disk_snapshot.disk,
                                                        display_name=last_disk_snapshot.display_name,
                                                        parent_snapshot=last_disk_snapshot, image_path=image_path,
                                                        ident=ident,
                                                        host_snapshot=host_snapshot,
                                                        bytes=last_disk_snapshot.bytes,
                                                        type=last_disk_snapshot.type,
                                                        boot_device=last_disk_snapshot.boot_device,
                                                        ext_info=last_disk_snapshot.ext_info)
        else:
            disk = Disk.objects.create(ident=uuid.uuid4().hex)
            disk_snapshot = DiskSnapshot.objects.create(disk=disk,
                                                        display_name='NAS 数据盘',
                                                        parent_snapshot=None, image_path=image_path,
                                                        ident=ident,
                                                        host_snapshot=host_snapshot,
                                                        bytes=int(ext_config['nas_max_space_actual']),
                                                        type=DiskSnapshot.DISK_MBR,
                                                        boot_device=False)
        return disk_snapshot

    @staticmethod
    def _fetch_snapshots(host_snapshot):
        if not host_snapshot:
            return list(), None
        else:
            disk_snapshot = host_snapshot.disk_snapshots.first()
        validator_list = [GetSnapshotList.is_disk_snapshot_object_exist,
                          GetSnapshotList.is_disk_snapshot_file_exist,
                          GetSnapshotList.is_disk_snapshot_object_finished,
                          GetSnapshotList.is_disk_snapshot_object_successful
                          ]
        # 备份时候，最后一个点是cdp且即将删除不要依赖 #3274
        validator_list_first = [GetSnapshotList.is_schedule_valid]

        snapshots = GetSnapshotList.query_snapshots_by_snapshot_object(
            disk_snapshot, validator_list, None, validator_list_first)

        if len(snapshots) == 0 or len(snapshots) > xdata.SNAPSHOT_FILE_CHAIN_MAX_LENGTH:
            _logger.warning(r'disk[{}] snapshot chain is:{}, max chain is:{} '.format(
                disk_snapshot, len(snapshots), xdata.SNAPSHOT_FILE_CHAIN_MAX_LENGTH))
            return list(), None
        else:
            return snapshots, disk_snapshot

    def work(self):
        try:
            self.task.set_status(FileBackupTask.SEND_BACKUP_COMMAND)
            file_backup_api('new', self.task_content['backup_params']['host_ident'], self.task_content['backup_params'])
            self.set_host_snapshot_valid()
            self.clean_next_force_full()
        except CreateSnapshotImageError as csie:
            self.set_next_force_full()
            self.set_host_snapshot_failed()
            xlogging.raise_and_logging_error(csie.msg,
                                             r'work CreateSnapshotImageError : {} {}'.format(csie.debug, csie.err_code))
        except xlogging.BoxDashboardException as bde:
            if not bde.is_log:
                self.log_error(r'work BoxDashboardException : {} | {}'.format(bde.msg, bde.debug))
                bde.is_log = True
            self.set_host_snapshot_failed()
            raise
        except Exception as e:
            self.set_host_snapshot_failed()
            xlogging.raise_and_logging_error(r'内部异常，代码2346', r'work Exception : {}'.format(e))
