import datetime
import json
import os
import threading
import time
import uuid
import requests

from django.utils import timezone

from apiv1.remote_backup_api import get_web_api_instance, InvalidUser
from apiv1.remote_backup_sub_task import RemoteBackupSubTaskThreading, SubTaskFailed
from box_dashboard import xlogging, boxService, xdatetime
from .models import RemoteBackupSchedule, RemoteBackupTask, RemoteBackupSubTask, HostSnapshot, HostSnapshotCDP, \
    DiskSnapshot, DiskSnapshotCDP, StorageNode, HostLog
from .work_processors import HostBackupWorkProcessors
from apiv1.logic_processors import BackupTaskScheduleLogicProcessor
from apiv1.snapshot import GetSnapshotList
from apiv1.signals import exe_schedule
from apiv1.spaceCollection import SpaceCollectionWorker

_logger = xlogging.getLogger(__name__)


def create_log(host, type, desc, debug=''):
    HostLog.objects.create(host=host, type=type, reason=json.dumps({'description': desc, 'debug': debug},
                                                                   ensure_ascii=False))
    return None


def set_next_force_full(schedule, value):
    ext_config = json.loads(schedule.ext_config)
    ext_config['next_force_full'] = value
    schedule.ext_config = json.dumps(ext_config)
    schedule.save(update_fields=['ext_config'])


class RemoteBackupScheduleRetryHandle(object):
    """
    提供备份失败后计划重试的静态方法
    """

    @staticmethod
    def _get_backup_retry_options(schedule):
        enable, count, interval = True, 5, 10
        ext_config = json.loads(schedule.ext_config)
        retry_setting = ext_config['retry_setting']['value']
        if retry_setting:
            count, interval = retry_setting.split('|')
        else:
            enable = False
        return enable, int(count), int(interval)

    @staticmethod
    def _update_next_run_time(schedule, mins):
        schedule.next_run_date = timezone.now() + datetime.timedelta(minutes=mins)
        schedule.save(update_fields=['next_run_date'])
        _logger.warning('modify schedule:{} next_run_date to:{}, call by RemoteBackupScheduleRetryHandle'.format(
            schedule.id, schedule.next_run_date))

    @staticmethod
    def clean(schedule_id):
        schedule = RemoteBackupSchedule.objects.get(id=schedule_id)
        ext_config = json.loads(schedule.ext_config)
        ext_config['execute_schedule_retry'] = 0
        schedule.ext_config = json.dumps(ext_config, ensure_ascii=False)
        schedule.save(update_fields=['ext_config', ])

    @staticmethod
    def modify(schedule_id):
        schedule = RemoteBackupSchedule.objects.get(id=schedule_id)
        enable, count, interval = RemoteBackupScheduleRetryHandle._get_backup_retry_options(schedule)
        retry_max_count = 0
        if enable:
            next_run_date = schedule.next_run_date
            if next_run_date and (timezone.now() + datetime.timedelta(minutes=interval)) > next_run_date:
                retry_max_count = 0
                _logger.info('modify_next_run_date_by_backup_retry schedule:{} will exe soon, no need update!'.format(
                    schedule.id))
            else:
                retry_max_count = count

        ext_config = json.loads(schedule.ext_config)
        execute_schedule_retry = ext_config.get('execute_schedule_retry', 0)
        if execute_schedule_retry < retry_max_count:
            RemoteBackupScheduleRetryHandle._update_next_run_time(schedule, interval)
            ext_config['execute_schedule_retry'] = execute_schedule_retry + 1
        else:
            ext_config['execute_schedule_retry'] = 0

        schedule.ext_config = json.dumps(ext_config, ensure_ascii=False)
        schedule.save(update_fields=['ext_config', ])


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


class RemoteBackupTaskCommon(WorkerLog):
    def __init__(self, remote_backup_task_object, web_api):
        self._web_api = web_api
        self._remote_backup_task_object = remote_backup_task_object
        self._ext_config_object = json.loads(remote_backup_task_object.ext_config)
        self._schedule_object = remote_backup_task_object.schedule
        self._host_object = self._schedule_object.host
        self._host_snapshot = None
        self._host_snapshot_cdp = None
        self._successful = True
        self._storage_node_base_path = None
        self.name = 'remote_task_{}_{}'.format(remote_backup_task_object.schedule.id,
                                               remote_backup_task_object.id)
        self.error = None

    def run_logic(self):
        self.log_info('start run ...'.format(self._remote_backup_task_object.id))
        self._load_or_create_host_snapshot()
        self.log_info('need sync host {} hostsnapshot {}'.format(self._host_object, self._host_snapshot.id))
        self.log_info('need sync disks idents {}'.format(self._get_disk_ident_array()))
        self._init()

        remote_backup_sub_task_thread_array = list()

        try:
            while True:
                self._clean_not_alive_remote_backup_sub_task_thread(remote_backup_sub_task_thread_array)
                if self._schedule_valid():
                    self._load_remote_backup_sub_task_array(remote_backup_sub_task_thread_array)
                    if not self.try_create_remote_backup_sub_task(remote_backup_sub_task_thread_array):
                        self.log_info(
                            'running task {}'.format([ths.name for ths in remote_backup_sub_task_thread_array]))
                        self.log_info('_exist_more_sub_task {}'.format(self._exist_more_sub_task()))
                        if 0 == len(remote_backup_sub_task_thread_array) and not self._exist_more_sub_task():
                            self.log_info('start finish_current_task')
                            self._finish_current_task()
                            return True
                        else:
                            self.sleep_until_no_worker(remote_backup_sub_task_thread_array)
                            self._update_progress(remote_backup_sub_task_thread_array)
                else:
                    if 0 != len(remote_backup_sub_task_thread_array):
                        time.sleep(1)
                        continue
                    raise Exception('schedule is not valid')
        except SubTaskFailed as su:
            self.log_error('catch SubTaskFailed:{}|{}|{}'.format(su.msg, su.debug, su.code))
            if su.code != -1 and (not self.error):
                self.error = su
            self._successful = False
            # 本地和远端的快照文件丢失，需要来一次完整的同步，同时本地快照需要删除
            if su.code in (SubTaskFailed.ERROR_CODE_LOCAL_SNAPSHOT_MISS,
                           SubTaskFailed.ERROR_CODE_REMOTE_SNAPSHOT_MISS):
                self.log_warning('local snapshots miss, need force full sync')
                set_next_force_full(self._schedule_object, True)
                self._finish_current_task()
                self._clean_host_snapshot()  # 快照链不可以用
            raise
        except Exception as e:
            self.log_error('find error:{}'.format(e))
            self._successful = False
            raise
        finally:
            for remote_backup_sub_task_thread in remote_backup_sub_task_thread_array:
                remote_backup_sub_task_thread.force_stop()
                remote_backup_sub_task_thread.join()
            self.log_info('start end ...')
            self._create_finish_log()

    def _init(self):
        try:
            self._storage_node_base_path = StorageNode.objects.get(ident=self._schedule_object.storage_node_ident).path
        except StorageNode.DoesNotExist as e:
            self._create_log('任务失败，存储节点被删除。')
            raise e

    def _create_finish_log(self):
        is_cdp = self._get_host_snapshot_info__is_cdp()
        self._create_finish_log_real(is_cdp)

    def _create_finish_log_real(self, is_cdp):
        start_time = self._get_host_snapshot_info__start_datetime()  # 不能使用 hostsnapshot 时间，有可能没有
        last_start_time = self._get_last_start_time()
        if self._successful:
            if not is_cdp:
                if last_start_time:
                    desc_info = '同步{}至{}的快照数据成功'.format(last_start_time, start_time)
                else:
                    desc_info = '同步{}的快照数据成功'.format(start_time)
                log_type = HostLog.LOG_REMOTE_BACKUP_NORM_SUCCESSFUL
            else:
                cdp_start = self._host_snapshot_cdp.first_datetime.strftime(xdatetime.FORMAT_WITH_MICROSECOND)
                cdp_end = self._host_snapshot_cdp.last_datetime.strftime(xdatetime.FORMAT_WITH_MICROSECOND)
                desc_info = '同步{}至{}的持续保护数据成功'.format(cdp_start, cdp_end)
                log_type = HostLog.LOG_REMOTE_BACKUP_CDP_END
        else:
            if self._schedule_deleted():
                postfix = '计划被删除'
            elif self._schedule_disable():
                postfix = '计划被禁用'
            else:
                if self.error:
                    postfix = self.error.msg
                else:
                    postfix = '内部错误2345'
            if not is_cdp:
                if last_start_time:
                    desc_info = '同步{}至{}的快照数据失败，{}'.format(last_start_time, start_time, postfix)
                else:
                    desc_info = '同步{}的快照数据失败，{}'.format(start_time, postfix)
                log_type = HostLog.LOG_REMOTE_BACKUP_NORM_FAILED
            else:
                if self._host_snapshot_cdp.first_datetime and self._host_snapshot_cdp.last_datetime:
                    cdp_start = self._host_snapshot_cdp.first_datetime.strftime(xdatetime.FORMAT_WITH_MICROSECOND)
                    cdp_end = self._host_snapshot_cdp.last_datetime.strftime(xdatetime.FORMAT_WITH_MICROSECOND)
                    desc_info = '同步{}至{}的持续保护数据失败，{}'.format(cdp_start, cdp_end, postfix)
                else:
                    desc_info = '同步持续保护数据失败，{}'.format(postfix)
                log_type = HostLog.LOG_REMOTE_BACKUP_CDP_END
        HostLog.objects.create(host=self._host_object, type=log_type, reason=json.dumps(
            {'host_id': self._host_object.id, 'host_snapshot_id': self._host_snapshot.id,
             'description': desc_info}))

    def sleep_until_no_worker(self, remote_backup_sub_task_thread_array, seconds=60):
        count = 0
        while count < (seconds // 5):
            count += 1
            self._clean_not_alive_remote_backup_sub_task_thread(remote_backup_sub_task_thread_array)
            if 0 == len(remote_backup_sub_task_thread_array):
                break
            time.sleep(5)

    def _load_or_create_host_snapshot(self):
        try:
            self._host_snapshot = self._remote_backup_task_object.host_snapshot
            if self._host_snapshot is None:
                raise HostSnapshot.DoesNotExist
            if self._host_snapshot.is_cdp:
                self._host_snapshot_cdp = self._host_snapshot.cdp_info
        except HostSnapshot.DoesNotExist:
            self._clean_host_snapshot()
            self._create_log('创建快照')
            self._set_status(RemoteBackupTask.NEW_HOST_SNAPSHOT)
            self.create_host_snapshot()

    def _clean_host_snapshot(self):
        if self._host_snapshot and self._host_snapshot.successful:
            if self._host_snapshot.is_cdp:
                self._host_snapshot.set_deleting()
            else:
                SpaceCollectionWorker.create_normal_host_snapshot_delete_task(self._host_snapshot)

    def create_host_snapshot(self):
        ext_info_object = json.loads(self._get_host_snapshot_info__ext_info())
        ext_info_object['remote_backup_task_uuid'] = self._ext_config_object['remote_backup_task_uuid']
        ext_info_object['remote_id'] = self._get_host_snapshot_info__remote_id()

        is_cdp = self._get_host_snapshot_info__is_cdp()

        self._host_snapshot = HostSnapshot.objects.create(
            host=self._host_object, is_cdp=is_cdp, ext_info=json.dumps(ext_info_object),
            remote_schedule=self._schedule_object)
        if is_cdp:
            self._host_snapshot_cdp = HostSnapshotCDP.objects.create(host_snapshot=self._host_snapshot)

        self._remote_backup_task_object.set_host_snapshot(self._host_snapshot)
        self._update_host_system_info()

        self._create_start_log(is_cdp)

    def _create_start_log(self, is_cdp):
        st_datetime = self._get_host_snapshot_info__start_datetime()
        if not is_cdp:
            last_start_time = self._get_last_start_time()
            if last_start_time:
                desc_info = '开始同步{}至{}的快照数据'.format(last_start_time, st_datetime)
            else:
                desc_info = '开始同步{}的快照数据'.format(st_datetime)
            log_type = HostLog.LOG_REMOTE_BACKUP_NORM_START
        else:
            desc_info = '开始同步{}的持续保护数据'.format(st_datetime)
            log_type = HostLog.LOG_REMOTE_BACKUP_CDP_START
        HostLog.objects.create(host=self._host_object, type=log_type, reason=json.dumps(
            {'host_id': self._host_object.id, 'host_snapshot_id': self._host_snapshot.id, 'description': desc_info}))

    def _get_last_local_host_snapshot(self):
        host_snapshot_id = self._ext_config_object['last_local_host_snapshot_id']
        try:
            return HostSnapshot.objects.get(id=host_snapshot_id)
        except HostSnapshot.DoesNotExist:
            return None

    def _get_last_start_time(self):
        host_snapshot = self._get_last_local_host_snapshot()
        if host_snapshot:
            if host_snapshot.start_datetime:
                return host_snapshot.start_datetime.strftime(xdatetime.FORMAT_WITH_MICROSECOND)
            else:
                return None
        else:
            return None

    def _get_host_snapshot_info__is_cdp(self):
        return self._ext_config_object['new_host_snapshot_info']['fields']['is_cdp']

    def _get_host_snapshot_info__remote_id(self):
        return self._ext_config_object['new_host_snapshot_info']['pk']

    def _get_host_snapshot_info__ext_info(self):
        return self._ext_config_object['new_host_snapshot_info']['fields']['ext_info']

    def _get_host_snapshot_info__start_datetime(self):
        start_datetime_str = self._ext_config_object['new_host_snapshot_info']['fields']['start_datetime']
        return xdatetime.string2datetime(start_datetime_str)

    def _update_host_system_info(self):
        snapshot_ext_info = json.loads(self._get_host_snapshot_info__ext_info())
        host_ext_info = json.loads(self._host_object.ext_info)
        host_ext_info['system_infos'] = snapshot_ext_info['system_infos']
        self._host_object.ext_info = json.dumps(host_ext_info)
        self._host_object.save(update_fields=['ext_info'])

    def _clean_not_alive_remote_backup_sub_task_thread(self, remote_backup_sub_task_thread_array):
        t = remote_backup_sub_task_thread_array.copy()
        for remote_backup_sub_task_object in t:
            if not remote_backup_sub_task_object.is_alive():
                self.log_debug(
                    'remote_backup_sub_task_thread_array remove {}'.format(remote_backup_sub_task_object.name))
                remote_backup_sub_task_thread_array.remove(remote_backup_sub_task_object)
                if remote_backup_sub_task_object.error:
                    raise remote_backup_sub_task_object.error

    def _load_remote_backup_sub_task_array(self, remote_backup_sub_task_thread_array):
        running_remote_backup_sub_task_id = [sub_task.sub_task_id for sub_task in remote_backup_sub_task_thread_array]
        try:
            for sub_task_object in self._remote_backup_task_object.remote_backup_sub_tasks.filter(
                    finish_datetime__isnull=True).all():
                if sub_task_object.id in running_remote_backup_sub_task_id:
                    continue
                new_task = RemoteBackupSubTaskThreading(sub_task_object, self._web_api)
                new_task.setDaemon(True)
                new_task.start()
                self.log_debug(
                    'remote_backup_sub_task_thread_array append {}'.format(new_task.name))
                remote_backup_sub_task_thread_array.append(new_task)
        except RemoteBackupSubTask.DoesNotExist:
            pass  # do nothing

    def _check_sub_tasks(self):
        if self._remote_backup_task_object.remote_backup_sub_tasks.filter(finish_datetime__isnull=False,
                                                                          successful=False).exists():
            self._successful = False
        else:
            pass

    def _finish_current_task(self):
        self._check_sub_tasks()
        self._finish_host_snapshot()
        self._remote_backup_task_object.successful = self._successful
        self._remote_backup_task_object.finish_datetime = datetime.datetime.now()
        self._remote_backup_task_object.save(update_fields=['successful', 'finish_datetime'])
        self.log_info('_finish_current_task successful:{}'.format(self._successful))

    def _finish_host_snapshot(self):
        if self._host_snapshot.start_datetime is None:
            self._host_snapshot.start_datetime = self._get_host_snapshot_info__start_datetime()
            self._host_snapshot.save(update_fields=['start_datetime'])
        if self._host_snapshot.finish_datetime is None:
            self._host_snapshot.finish_datetime = datetime.datetime.now()
            self._host_snapshot.successful = self._successful
            self._host_snapshot.save(update_fields=['successful', 'finish_datetime'])

    def _query_new_disk_backup_from_remote(self, disk_snapshot_ident):
        return self._web_api.http_query_new_disk_backup(self._get_host_snapshot_info__remote_id(), disk_snapshot_ident)

    def _get_disk_snapshot_info_from_host_snapshot_info(self, disk_object):
        host_snapshot_info = self._ext_config_object['new_host_snapshot_info']
        disks_chains = host_snapshot_info['disks_chains']  # [[disk1_chain],[disk2_chain]]
        for disk_chain in disks_chains:
            assert len(disk_chain) > 0
            if disk_chain[0]['fields']['disk_ident'] == disk_object.ident:
                assert 'other' not in ['same' if one_snapshot['fields']['disk_ident'] == disk_object.ident else 'other'
                                       for one_snapshot in disk_chain]
                return disk_chain
        xlogging.raise_and_logging_error(
            '同步快照数据失败，无效的参数', 'disk_ident : {} not in disks_chains {}'.format(disk_object.ident, disks_chains))

    def try_create_remote_backup_sub_task(self, remote_backup_sub_task_thread_array):
        result = False

        running_disk_ident_array = [sub_task.disk_ident for sub_task in remote_backup_sub_task_thread_array]

        for disk_ident in self._get_disk_ident_array(running_disk_ident_array):
            self.log_info('start create sub task, disk ident:{}'.format(disk_ident))
            disk_object = HostBackupWorkProcessors.get_disk_object(disk_ident)
            last_disk_backup_object = self._query_last_disk_backup_object_in_local(disk_object)
            if last_disk_backup_object:
                remote_ident = last_disk_backup_object.remote_backup_sub_task.remote_snapshot_ident
                new_disk_snapshot_info = self._query_new_disk_backup_from_remote(remote_ident)
            else:
                new_disk_snapshot_info = self._get_disk_snapshot_info_from_host_snapshot_info(disk_object)

            if new_disk_snapshot_info:
                self.log_info('get disk {} new_disk_snapshot_info {}'.format(disk_ident, new_disk_snapshot_info))
                self._set_status(RemoteBackupTask.NEW_SUB_TASK)
                if type(new_disk_snapshot_info) is list:
                    if 1 == len(new_disk_snapshot_info):
                        self._create_remote_backup_sub_task(new_disk_snapshot_info[0], disk_object)
                    else:
                        self._create_remote_backup_sub_task_by_array(new_disk_snapshot_info, disk_object)
                else:
                    self._create_remote_backup_sub_task(new_disk_snapshot_info, disk_object)
                result = True

        return result

    def _exist_more_sub_task(self):
        if self._host_snapshot_cdp is None:
            # "非CDP主机快照"会不会继续产生新的"磁盘快照"
            return False

        cdp_task_info = self._web_api.http_query_is_host_cdp_back_end(self._get_host_snapshot_info__remote_id())
        return not cdp_task_info['cdp_task_end']

    def _get_disk_ident_array(self, exclude_disk_ident_array=None):
        if exclude_disk_ident_array is None:
            exclude_disk_ident_array = list()
        host_snapshot_info = self._ext_config_object['new_host_snapshot_info']
        return list(set(host_snapshot_info['disks_idents']) - set(exclude_disk_ident_array))

    def _query_last_disk_backup_object_in_local(self, disk_object):
        """
        找到本次同步任务，最后一个同步的磁盘快照点
        :param disk_object:
        :return:  None or diskSnapshot object
        """
        return DiskSnapshot.objects.filter(remote_backup_sub_task__main_task=self._remote_backup_task_object,
                                           disk=disk_object).order_by('id').last()

    @staticmethod
    def _get_disk_snapshot_info(new_disk_snapshot_info):
        return [
            new_disk_snapshot_info['fields']['display_name'],
            new_disk_snapshot_info['fields']['parent_snapshot_ident'],
            new_disk_snapshot_info['fields']['image_path'],
            new_disk_snapshot_info['fields']['ident'],
            new_disk_snapshot_info['fields']['is_cdp'],
            new_disk_snapshot_info['fields']['bytes'],
            new_disk_snapshot_info['fields']['type'],
            new_disk_snapshot_info['fields']['boot_device'],
            new_disk_snapshot_info['fields']['parent_timestamp'],
            new_disk_snapshot_info['fields']['first_timestamp'],
            new_disk_snapshot_info['fields']['disk_ident'],
            new_disk_snapshot_info['fields'].get('ext_info', '{}'),  # 新加字段，兼容老版本
        ]

    @staticmethod
    def _get_disk_snapshot_path_and_ident(new_disk_snapshot_info):
        if DiskSnapshot.is_cdp_file(new_disk_snapshot_info['fields']['image_path']):
            return {'path': new_disk_snapshot_info['fields']['image_path'], 'snapshot': 'all'}
        else:
            return {'path': new_disk_snapshot_info['fields']['image_path'],
                    'snapshot': new_disk_snapshot_info['fields']['ident']}

    def _create_remote_backup_sub_task_by_array(self, new_disk_snapshot_info, local_disk_object):
        last_disk_snapshot = new_disk_snapshot_info[-1]
        display_name, _, image_path, ident, is_cdp, disk_bytes, disk_type, boot_device, _, _, disk_ident, ext_info \
            = self._get_disk_snapshot_info(last_disk_snapshot)
        new_ident = uuid.uuid4().hex
        assert not is_cdp
        local_image_path = self.generate_parameters_new_image_path_for_new_qcow()
        local_disk_snapshot_object = DiskSnapshot.objects.create(
            disk=local_disk_object, display_name=display_name, parent_snapshot=None,
            image_path=local_image_path, ident=new_ident, host_snapshot=None if is_cdp else self._host_snapshot,
            bytes=disk_bytes, type=disk_type, boot_device=boot_device, parent_timestamp=None, ext_info=ext_info
        )

        RemoteBackupSubTask.objects.create(
            main_task=self._remote_backup_task_object,
            local_snapshot=local_disk_snapshot_object,
            remote_snapshot_ident=ident,
            remote_snapshot_path=image_path,
            ext_config=json.dumps({
                'sub_task_uuid': new_ident,
                'disk_snapshot_list': [self._get_disk_snapshot_path_and_ident(one_disk_snapshot) for one_disk_snapshot
                                       in new_disk_snapshot_info],
            })
        )
        self.replace_ident_in_host_snapshot_ext_config(disk_ident, local_disk_object.ident, new_ident)

    def _create_remote_backup_sub_task(self, new_disk_snapshot_info, local_disk_object, parent_disk_snapshot=None):
        display_name, parent_snapshot_ident, image_path, ident, is_cdp, disk_bytes, disk_type, boot_device, \
        parent_timestamp, first_timestamp, disk_ident, ext_info \
            = self._get_disk_snapshot_info(new_disk_snapshot_info)
        new_ident = uuid.uuid4().hex
        if parent_snapshot_ident:
            sub_task_object = self.get_sub_task_by_remote_ident(
                self._remote_backup_task_object.schedule, parent_snapshot_ident)
            if sub_task_object is None:  # B机数据库找不到依赖的快照点，需要进行一次完整的同步
                self.log_info(
                    '_create_remote_backup_sub_task local parent snapshot is not exists, local disk:{}'.format(
                        local_disk_object))
                raise SubTaskFailed('创建同步子任务失败，获取快照链失败', 'not found dep snapshots',
                                    SubTaskFailed.ERROR_CODE_LOCAL_SNAPSHOT_MISS)
            else:
                local_parent_snapshot_object = sub_task_object.local_snapshot
        else:
            local_parent_snapshot_object = None
        if is_cdp:
            local_image_path = self.generate_parameters_new_image_path_for_new_cdp(new_ident)
        else:
            local_image_path = self.generate_parameters_image_path_for_qcow(disk_bytes, local_parent_snapshot_object)

        if RemoteBackupSubTask.objects.filter(main_task=self._remote_backup_task_object,
                                              remote_snapshot_ident=ident,
                                              successful=True).exists():
            xlogging.raise_and_logging_error('创建同步子任务失败', 'same task already exists:{}|{}|{}'.format(ident,
                                                                                                     self._remote_backup_task_object.id,
                                                                                                     locals()))

        local_disk_snapshot_object = DiskSnapshot.objects.create(
            disk=local_disk_object, display_name=display_name, parent_snapshot=local_parent_snapshot_object,
            image_path=local_image_path, ident=new_ident, host_snapshot=None if is_cdp else self._host_snapshot,
            bytes=disk_bytes, type=disk_type, boot_device=boot_device, parent_timestamp=parent_timestamp,
            ext_info=ext_info
        )
        if is_cdp:
            DiskSnapshotCDP.objects.create(disk_snapshot=local_disk_snapshot_object,
                                           first_timestamp=first_timestamp, last_timestamp=first_timestamp)

        RemoteBackupSubTask.objects.create(main_task=self._remote_backup_task_object,
                                           local_snapshot=local_disk_snapshot_object,
                                           remote_snapshot_ident=ident,
                                           remote_snapshot_path=image_path,
                                           ext_config=json.dumps({'sub_task_uuid': new_ident})
                                           )

        self.replace_ident_in_host_snapshot_ext_config(disk_ident, local_disk_object.ident, new_ident)

    @staticmethod
    def get_sub_task_by_remote_ident(schedule, parent_ident):
        try:
            sub_task = RemoteBackupSubTask.objects.filter(main_task__schedule=schedule,
                                                          remote_snapshot_ident=parent_ident,
                                                          successful=True).order_by('start_datetime').last()
        except RemoteBackupSubTask.DoesNotExist:
            return None
        else:
            return sub_task

    # 生成快照文件路径：如/home/aio/+ images + host_ident
    def generate_parameters_new_image_path_for_new_qcow(self, file_type='.qcow'):
        folder_path = boxService.box_service.pathJoin([self._storage_node_base_path, 'images', self._host_object.ident])
        boxService.box_service.makeDirs(folder_path)
        return boxService.box_service.pathJoin([folder_path, uuid.uuid4().hex + file_type])

    def generate_parameters_new_image_path_for_new_cdp(self, ident, file_type='.cdp'):
        folder_path = boxService.box_service.pathJoin([self._storage_node_base_path, 'images', self._host_object.ident])
        boxService.box_service.makeDirs(folder_path)
        return boxService.box_service.pathJoin([folder_path, ident + file_type])

    def generate_parameters_image_path_for_qcow(self, disk_bytes, last_snapshot_object):
        if last_snapshot_object is None:
            generate_new = True
        else:
            host_snapshot_object = GetSnapshotList.get_host_snapshot_by_disk_snapshot(last_snapshot_object)
            if disk_bytes != last_snapshot_object.bytes:  # 磁盘大小发生变化，需要新生成存储文件
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
            return self.generate_parameters_new_image_path_for_new_qcow()
        else:
            return last_snapshot_object.image_path

    def _update_progress(self, remote_backup_sub_task_thread_array):
        is_cdp = self._get_host_snapshot_info__is_cdp()
        if is_cdp:
            self._set_host_snapshot_start_datetime()
        self._update_host_snapshot(remote_backup_sub_task_thread_array)
        self._update_host_snapshot_cdp()

    def _set_host_snapshot_start_datetime(self):
        if self._host_snapshot.start_datetime is None:
            self._host_snapshot.start_datetime = self._get_host_snapshot_info__start_datetime()
            self._host_snapshot.save(update_fields=['start_datetime'])
        if self._host_snapshot_cdp and self._host_snapshot_cdp.first_datetime is None:
            self._host_snapshot_cdp.first_datetime = self._host_snapshot.start_datetime
            self._host_snapshot_cdp.save(update_fields=['first_datetime'])

    def _update_host_snapshot(self, remote_backup_sub_task_thread_array):
        if self._host_snapshot_cdp is None:
            return
        for remote_backup_sub_task_thread in remote_backup_sub_task_thread_array:
            if not remote_backup_sub_task_thread.is_cdp:
                return
        self._finish_host_snapshot()

    def _update_host_snapshot_cdp(self):
        if self._host_snapshot_cdp is None:
            return

        last_cdp_timestamp = None
        for disk_ident in self._get_disk_ident_array():
            disk_object = HostBackupWorkProcessors.get_disk_object(disk_ident)
            last_disk_snapshot_cdp_object = self._query_last_disk_backup_cdp_object_in_local(disk_object)
            if last_disk_snapshot_cdp_object is None:
                continue
            if last_cdp_timestamp is None or last_disk_snapshot_cdp_object.last_timestamp > last_cdp_timestamp:
                last_cdp_timestamp = last_disk_snapshot_cdp_object.last_timestamp

        if last_cdp_timestamp:
            last_cdp_datetime = datetime.datetime.fromtimestamp(last_cdp_timestamp)
            need_update = False
            if self._host_snapshot_cdp.last_datetime is None:
                need_update = True
            elif last_cdp_datetime > self._host_snapshot_cdp.last_datetime:
                need_update = True
            else:
                pass
            if need_update:
                self._host_snapshot_cdp.last_datetime = last_cdp_datetime
                self._host_snapshot_cdp.save(update_fields=['last_datetime'])

    def _query_last_disk_backup_cdp_object_in_local(self, disk_object):
        last_disk_backup_object = self._query_last_disk_backup_object_in_local(disk_object)
        if (last_disk_backup_object is None) or (not last_disk_backup_object.is_cdp):
            return None
        return last_disk_backup_object.cdp_info

    def replace_ident_in_host_snapshot_ext_config(self, disk_ident, new_disk_ident, new_ident):
        ext_info_str = self._host_snapshot.ext_info
        ext_info = json.loads(ext_info_str)
        old_ident = ''
        for disk_info in ext_info['include_ranges']:
            if disk_info['diskIdent'] == disk_ident:
                old_ident = disk_info['diskSnapshot']
        if not old_ident:
            return None
        ext_info_str = ext_info_str.replace('"{}"'.format(old_ident), '"{}"'.format(new_ident))
        ext_info_str = ext_info_str.replace('"{}"'.format(disk_ident), '"{}"'.format(new_disk_ident))
        self._host_snapshot.ext_info = ext_info_str
        self._host_snapshot.save(update_fields=['ext_info'])

    def _schedule_valid(self):
        return not (self._schedule_deleted() or self._schedule_disable())

    def _schedule_deleted(self):
        schedule = RemoteBackupSchedule.objects.get(id=self._remote_backup_task_object.schedule.id)
        return schedule.deleted

    def _schedule_disable(self):
        schedule = RemoteBackupSchedule.objects.get(id=self._remote_backup_task_object.schedule.id)
        return not schedule.enabled

    def _create_log(self, desc_info, debug='', log_type=HostLog.LOG_REMOTE_BACKUP):
        host = self._host_object
        create_log(host, log_type, desc_info, debug)

    def _set_status(self, status):
        if self._remote_backup_task_object.status == status:
            return None
        else:
            self._remote_backup_task_object.status = status
            self._remote_backup_task_object.set_status(status)

    def _set_remote_task_paused(self):
        self._remote_backup_task_object.paused = True
        self._remote_backup_task_object.save(update_fields=['paused'])


class RemoteBackupTaskContinued(RemoteBackupTaskCommon):
    def __init__(self, remote_backup_task_object, web_api):
        super(RemoteBackupTaskContinued, self).__init__(remote_backup_task_object, web_api)


class RemoteBackupTaskInterval(RemoteBackupTaskCommon):
    def __init__(self, remote_backup_task_object, web_api):
        super(RemoteBackupTaskInterval, self).__init__(remote_backup_task_object, web_api)

    def _get_host_snapshot_info__remote_cdp_info_end_time(self):
        return self._ext_config_object['new_host_snapshot_info']['cdp_end_time']

    def _query_latest_disk_backup_from_remote(self, disk_snapshot_ident, last_timestamp):
        return self._web_api.http_query_latest_disk_backup(self._get_host_snapshot_info__remote_id(),
                                                           disk_snapshot_ident, last_timestamp)

    def _from_new_disk_snapshot_info_get_last_timestamp(self, new_disk_snapshot_info):
        snapshot = new_disk_snapshot_info['snapshot']
        if snapshot == 'all':
            return -1
        assert ('$' in snapshot or '~' in snapshot), 'invalid cdp snapshot:{}'.format(snapshot)
        if '$' in snapshot:
            datetime_str = snapshot.replace('$', '').replace('~', '')
        else:
            datetime_str = snapshot.split('~')[1]

        return datetime.datetime.strptime(datetime_str, '%Y-%m-%d-%H:%M:%S.%f').timestamp()

    def _get_host_snapshot_info__start_datetime(self):
        if self._get_host_snapshot_info__is_cdp():
            return xdatetime.string2datetime(self._get_host_snapshot_info__remote_cdp_info_end_time())
        else:
            return super(RemoteBackupTaskInterval, self)._get_host_snapshot_info__start_datetime()

    def _exist_more_sub_task(self):
        return False

    @staticmethod
    def _get_disk_snapshot_path_and_ident(new_disk_snapshot_info):
        if DiskSnapshot.is_cdp_file(new_disk_snapshot_info['fields']['image_path']):
            return {'path': new_disk_snapshot_info['fields']['image_path'],
                    'snapshot': new_disk_snapshot_info['snapshot']}
        else:
            return {'path': new_disk_snapshot_info['fields']['image_path'],
                    'snapshot': new_disk_snapshot_info['fields']['ident']}

    def create_host_snapshot(self):
        ext_info_object = json.loads(self._get_host_snapshot_info__ext_info())
        ext_info_object['remote_backup_task_uuid'] = self._ext_config_object['remote_backup_task_uuid']
        ext_info_object['remote_id'] = self._get_host_snapshot_info__remote_id()

        is_cdp = self._get_host_snapshot_info__is_cdp()
        if is_cdp:
            ext_info_object['remote_last_datetime'] = self._get_host_snapshot_info__remote_cdp_info_end_time()
        else:
            ext_info_object['remote_last_datetime'] = ''

        self._host_snapshot = HostSnapshot.objects.create(
            host=self._host_object, ext_info=json.dumps(ext_info_object), remote_schedule=self._schedule_object)

        self._remote_backup_task_object.set_host_snapshot(self._host_snapshot)
        self._update_host_system_info()

        self._create_start_log(is_cdp=False)

    def _query_last_disk_backup_object_in_local(self, disk_object):
        return DiskSnapshot.objects.filter(host_snapshot__host=self._host_snapshot.host,
                                           host_snapshot__successful=True,
                                           remote_backup_sub_task__main_task__schedule__deleted=False,
                                           remote_backup_sub_task__successful=True,
                                           disk=disk_object).order_by('id').last()

    def _check_has_create_task(self, disk_ident):
        return RemoteBackupSubTask.objects.filter(local_snapshot__disk__ident=disk_ident,
                                                  main_task=self._remote_backup_task_object).exists()

    def try_create_remote_backup_sub_task(self, remote_backup_sub_task_thread_array):
        result = False

        running_disk_ident_array = [sub_task.disk_ident for sub_task in remote_backup_sub_task_thread_array]

        for disk_ident in self._get_disk_ident_array(running_disk_ident_array):
            self.log_info('start create sub task, disk ident:{}'.format(disk_ident))
            if self._check_has_create_task(disk_ident):
                self.log_warning('disk {} already have task skip'.format(disk_ident))
                continue
            disk_object = HostBackupWorkProcessors.get_disk_object(disk_ident)
            last_disk_backup_object = self._query_last_disk_backup_object_in_local(disk_object)
            if last_disk_backup_object:
                remote_ident = last_disk_backup_object.remote_backup_sub_task.remote_snapshot_ident
                last_timestamp = last_disk_backup_object.remote_backup_sub_task.remote_timestamp
                new_disk_snapshot_info_or = self._query_latest_disk_backup_from_remote(remote_ident, last_timestamp)
                assert new_disk_snapshot_info_or is not None, '_query_latest_disk_backup_from_remote get None!'
                is_family = new_disk_snapshot_info_or['is_family']
                new_disk_snapshot_info = new_disk_snapshot_info_or['disk_snapshot_info']
            else:
                is_family = True
                new_disk_snapshot_info = self._get_disk_snapshot_info_from_host_snapshot_info(disk_object)

            if new_disk_snapshot_info:
                if last_disk_backup_object and is_family:
                    parent_disk_snapshot = last_disk_backup_object
                else:
                    parent_disk_snapshot = None

                self.log_info(
                    'get disk {} new_disk_snapshot_info {} is_family {}'.format(disk_ident, new_disk_snapshot_info,
                                                                                is_family))
                self._create_remote_backup_sub_task_by_array(new_disk_snapshot_info, disk_object, parent_disk_snapshot)

            else:
                self.log_warning('try_create_remote_backup_sub_task not found new_disk_snapshot_info create empty '
                                 'snapshot, last_disk_backup_object:{}'.format(last_disk_backup_object))
                self._create_remote_backup_sub_task_by_empty(last_disk_backup_object)

            result = True

        return result

    def _create_remote_backup_sub_task_by_empty(self, last_disk_backup_object):
        new_ident = uuid.uuid4().hex
        local_disk_object = last_disk_backup_object.disk
        display_name = last_disk_backup_object.display_name
        disk_bytes = last_disk_backup_object.bytes
        disk_type = last_disk_backup_object.type
        boot_device = last_disk_backup_object.boot_device
        parent_timestamp = last_disk_backup_object.parent_timestamp
        local_image_path = self.generate_parameters_image_path_for_qcow(disk_bytes, last_disk_backup_object)
        ext_info = last_disk_backup_object.ext_info

        local_disk_snapshot_object = DiskSnapshot.objects.create(
            disk=local_disk_object, display_name=display_name, parent_snapshot=last_disk_backup_object,
            image_path=local_image_path, ident=new_ident, host_snapshot=self._host_snapshot,
            bytes=disk_bytes, type=disk_type, boot_device=boot_device, parent_timestamp=parent_timestamp,
            ext_info=ext_info
        )

        remote_ident = last_disk_backup_object.remote_backup_sub_task.remote_snapshot_ident
        last_timestamp = last_disk_backup_object.remote_backup_sub_task.remote_timestamp
        image_path = last_disk_backup_object.remote_backup_sub_task.remote_snapshot_path

        RemoteBackupSubTask.objects.create(main_task=self._remote_backup_task_object,
                                           local_snapshot=local_disk_snapshot_object,
                                           remote_snapshot_ident=remote_ident,
                                           remote_timestamp=last_timestamp,
                                           remote_snapshot_path=image_path,
                                           ext_config=json.dumps({
                                               'sub_task_uuid': new_ident,
                                               'disk_snapshot_list': ['empty']})
                                           )

        self.replace_ident_in_host_snapshot_ext_config(local_disk_object.ident, local_disk_object.ident, new_ident)

    def _create_remote_backup_sub_task_by_array(self, new_disk_snapshot_info, local_disk_object,
                                                parent_disk_snapshot=None):
        last_disk_snapshot = new_disk_snapshot_info[-1]
        display_name, _, image_path, ident, is_cdp, disk_bytes, disk_type, boot_device, _, _, disk_ident, ext_info \
            = self._get_disk_snapshot_info(last_disk_snapshot)
        new_ident = uuid.uuid4().hex

        # cdp 的点的磁盘名一直是空，所以要特殊处理
        if parent_disk_snapshot:
            display_name = parent_disk_snapshot.display_name
        else:
            display_name, *_ = self._get_disk_snapshot_info(new_disk_snapshot_info[0])

        local_image_path = self.generate_parameters_image_path_for_qcow(disk_bytes, parent_disk_snapshot)

        local_disk_snapshot_object = DiskSnapshot.objects.create(
            disk=local_disk_object, display_name=display_name, parent_snapshot=parent_disk_snapshot,
            image_path=local_image_path, ident=new_ident, host_snapshot=self._host_snapshot,
            bytes=disk_bytes, type=disk_type, boot_device=boot_device, parent_timestamp=None,
            ext_info=ext_info
        )

        if is_cdp:
            remote_timestamp = self._from_new_disk_snapshot_info_get_last_timestamp(last_disk_snapshot)
        else:
            remote_timestamp = -1

        RemoteBackupSubTask.objects.create(
            main_task=self._remote_backup_task_object,
            local_snapshot=local_disk_snapshot_object,
            remote_snapshot_ident=ident,
            remote_timestamp=remote_timestamp,
            remote_snapshot_path=image_path,
            ext_config=json.dumps({
                'sub_task_uuid': new_ident,
                'disk_snapshot_list': [self._get_disk_snapshot_path_and_ident(one_disk_snapshot) for one_disk_snapshot
                                       in new_disk_snapshot_info],
            })
        )
        self.replace_ident_in_host_snapshot_ext_config(disk_ident, local_disk_object.ident, new_ident)

    def _create_finish_log(self):
        self._create_finish_log_real(is_cdp=False)


class RemoteBackupLogicLocal(threading.Thread, WorkerLog):
    def __init__(self, schedule_object):
        super(RemoteBackupLogicLocal, self).__init__(name='RemoteBackupLogicLocal_{}'.format(schedule_object.id))
        self._schedule_object = schedule_object
        self._cyc_type = ''
        self._first_check = True
        self._exe_event = threading.Event()
        exe_schedule.connect(self._exe_schedule, sender=RemoteBackupSchedule)
        self._previous_sate = -1  # 上一次状态，持续同步任务使用
        self.name = 'remote_task_{}'.format(self._schedule_object.id)
        self._web_api = None

    @property
    def schedule_object_id(self):
        return self._schedule_object.id

    def run(self):
        while True:
            self._schedule_object = RemoteBackupSchedule.objects.get(id=self.schedule_object_id)
            self._get_cyc_type()
            if not self._schedule_object.enabled or self._schedule_object.deleted:
                break

            if not self.run_without_exception():
                self._sleep(60)

    def _sleep(self, seconds):
        self._exe_event.clear()
        self._exe_event.wait(seconds)
        self._exe_event.clear()

    def _exe_schedule(self, sender, **kwargs):
        if kwargs['schedule_id'] == 'RemoteBackupSchedule_{}'.format(self._schedule_object.id):
            self._exe_event.set()

    @xlogging.convert_exception_to_value(False)
    def run_without_exception(self):
        try:
            self._web_api = get_web_api_instance(self._schedule_object.host.ident, self._schedule_object)
            data = xlogging.DataHolder()
            while data.set(self._load_remote_backup_task()):
                if not data.get().run_logic():
                    time.sleep(60)

            return self._try_create_remote_backup_task()
        except SubTaskFailed as su:
            if su.code == SubTaskFailed.ERROR_CODE_NOT_ENOUGH_SPACE:
                self._create_log('检测到磁盘空间不足，无法进行同步，计划将被禁用；请检测磁盘空间')
                self._schedule_object.set_enabled(False)
                return True
            return False
        except InvalidUser:
            self._create_log('连接参数异常, 无法同步，计划将被禁用；请更改计划，检查用户名和密码，检查无误后请重新启用计划')
            self._schedule_object.set_enabled(False)
            return True

    def _load_remote_backup_task(self):
        try:
            unfinished_remote_backup_task_object = \
                self._schedule_object.remote_backup_tasks.filter(finish_datetime__isnull=True).first()
        except RemoteBackupTask.DoesNotExist:
            unfinished_remote_backup_task_object = None

        if unfinished_remote_backup_task_object:
            return self._get_task_runner(unfinished_remote_backup_task_object)
        else:
            return None

    @xlogging.convert_exception_to_value(None)
    def _get_cyc_type(self):
        ext_config = json.loads(self._schedule_object.ext_config)
        if ext_config['backup_period']['period_type'] == 'bak-continue':
            self._cyc_type = 'continued'
        else:
            self._cyc_type = 'interval'

    def _get_task_runner(self, remote_backup_task_object):
        if self._cyc_type == 'continued':  # 持续备份
            return RemoteBackupTaskContinued(remote_backup_task_object, self._web_api)
        elif self._cyc_type == 'interval':  # 间隔备份
            return RemoteBackupTaskInterval(remote_backup_task_object, self._web_api)
        else:
            return None

    def _try_create_remote_backup_task(self):
        if not self._should_run():
            return False
        self._set_last_run_date()
        if self._cyc_type == 'continued':
            return self._try_create_remote_backup_task_continued()
        elif self._cyc_type == 'interval':
            self._update_next_run_date()
            try:
                result = self._try_create_remote_backup_task_interval()
            except Exception as e:
                self._create_log('创建同步任务失败')
                RemoteBackupScheduleRetryHandle.modify(self.schedule_object_id)
                self.log_error('_try_create_remote_backup_task_interval error:{}'.format(e))
                raise e
            else:
                RemoteBackupScheduleRetryHandle.clean(self.schedule_object_id)
                return result
        else:
            return False

    def _try_create_remote_backup_task_continued(self):
        last_host_backup_object = self._query_last_host_backup_object_in_local()
        last_host_backup_object_remote_id = self._get_last_host_backup_object_remote_id(last_host_backup_object)
        self._create_log_wrapper('获取快照信息', [0, 2])

        try:
            new_host_snapshot_info = self._query_new_host_backup_from_remote(last_host_backup_object_remote_id)
        except Exception as e:
            self.log_error('_try_create_remote_backup_task_continued error:{}'.format(e))
            self._create_log_wrapper('创建同步任务失败', [0])
            self._previous_sate = 0
            raise
        else:
            if new_host_snapshot_info:
                self._create_log_wrapper('创建同步任务')
                self._previous_sate = 1
                self._create_remote_backup_task(last_host_backup_object, new_host_snapshot_info)
                set_next_force_full(self._schedule_object, False)
                return True
            else:
                self._create_log_wrapper('无新快照数据，等待下一次同步', [2])
                self._previous_sate = 2
                return False

    def _try_create_remote_backup_task_interval(self):
        last_host_backup_object = self._query_last_host_backup_object_in_local()
        last_host_backup_object_remote_id, last_datetime = self._get_last_host_backup_remote_id_and_last_datetime(
            last_host_backup_object)
        self._create_log('获取快照信息')
        new_host_snapshot_info = self._query_latest_host_backup_from_remote(
            last_host_backup_object_remote_id, last_datetime)
        if new_host_snapshot_info:
            self._create_log('创建同步任务')
            self._create_remote_backup_task(last_host_backup_object, new_host_snapshot_info)
            return True
        else:
            self._create_log('无新快照数据，等待下一次同步')
            return False

    def _should_run(self):
        return timezone.now() >= self._schedule_object.next_run_date

    def _set_last_run_date(self):
        self._schedule_object.last_run_date = timezone.now()
        self._schedule_object.save(update_fields=['last_run_date'])

    def _update_next_run_date(self):
        self._schedule_object.next_run_date = self._get_next_run_date(self._schedule_object.next_run_date)
        self._schedule_object.save(update_fields=['next_run_date'])

    def _get_next_run_date(self, last_run_datetime):
        ext_config = json.loads(self._schedule_object.ext_config)
        interval_config = ext_config['backup_period']

        if interval_config['period_type'] == 'bak-cycled':
            interval_unit = interval_config['val_unit']
            interval_value = int(interval_config['addition'])
            if interval_unit == 'min':
                next_date = last_run_datetime + datetime.timedelta(minutes=interval_value)
            elif interval_unit == 'hour':
                next_date = last_run_datetime + datetime.timedelta(hours=interval_value)
            elif interval_unit == 'day':
                next_date = last_run_datetime + datetime.timedelta(days=interval_value)
            else:
                raise Exception('wrong cyc type:{}'.format(ext_config))
        elif interval_config['period_type'] == 'bak-perweek':
            interval_weeks = self._convert_csv_to_list(interval_config['addition'])
            next_date = self._calc_next_run_with_week(last_run_datetime.time(), last_run_datetime, interval_weeks)
        elif interval_config['period_type'] == 'bak-permonth':
            interval_month = self._convert_csv_to_list(interval_config['addition'])
            next_date = self._calc_next_run_with_month(last_run_datetime.time(), last_run_datetime, interval_month)
        else:
            raise Exception('wrong cyc type:{}'.format(ext_config))

        now = timezone.now()
        if next_date < now:
            return now
        else:
            return next_date

    def _convert_csv_to_list(self, value):
        return sorted([int(i) for i in value.split(',')])

    def _calc_next_run_with_week(self, start_time, start_datetime, daysInWeek):
        start_date = BackupTaskScheduleLogicProcessor.get_start_date(start_datetime)
        following_dates = BackupTaskScheduleLogicProcessor.getFollowingDatesFromDate(start_date, 8)
        next_date = BackupTaskScheduleLogicProcessor.getFirstPlanDateFromDates(following_dates, daysInWeek, None)
        return datetime.datetime.combine(next_date, start_time)

    def _calc_next_run_with_month(self, start_time, start_datetime, daysInMonth):
        start_date = BackupTaskScheduleLogicProcessor.get_start_date(start_datetime)
        following_dates = BackupTaskScheduleLogicProcessor.getFollowingDatesFromDate(start_date, 64)
        next_date = BackupTaskScheduleLogicProcessor.getFirstPlanDateFromDates(following_dates, None, daysInMonth)
        return datetime.datetime.combine(next_date, start_time)

    def _query_last_host_backup_object_in_local(self):
        ext_config = json.loads(self._schedule_object.ext_config)
        if ext_config.get('next_force_full', False):
            self.log_info('_query_last_host_backup_object_in_local next_force_full by schedule ')
            return None
        else:
            pass

        try:
            last_task_object = self._schedule_object.remote_backup_tasks.filter(
                finish_datetime__isnull=False, successful=True).order_by('-id').first()
        except RemoteBackupTask.DoesNotExist:
            last_task_object = None

        if not last_task_object:
            return None
        else:
            assert last_task_object.host_snapshot is not None
            return last_task_object.host_snapshot

    @staticmethod
    def _get_last_host_backup_object_remote_id(host_backup_object):
        if host_backup_object:
            host_backup_object_info_object = json.loads(host_backup_object.ext_info)
            return host_backup_object_info_object['remote_id']
        else:
            return -1

    @staticmethod
    def _get_last_host_backup_remote_id_and_last_datetime(host_backup_object):
        if host_backup_object:
            host_backup_object_info_object = json.loads(host_backup_object.ext_info)
            return host_backup_object_info_object['remote_id'], host_backup_object_info_object['remote_last_datetime']
        else:
            return -1, -1

    def _query_new_host_backup_from_remote(self, last_host_backup_object_remote_id):
        host_ident = self._schedule_object.host.ident
        return self._web_api.http_query_new_host_backup(host_ident, last_host_backup_object_remote_id)

    def _query_latest_host_backup_from_remote(self, last_host_backup_object_remote_id, remote_last_datetime):
        host_ident = self._schedule_object.host.ident
        return self._web_api.http_query_latest_host_backup(host_ident, last_host_backup_object_remote_id,
                                                           remote_last_datetime)

    def _create_remote_backup_task(self, last_host_backup_object, new_host_snapshot_info):
        last_local_host_snapshot_id = last_host_backup_object.id if last_host_backup_object else -1
        remote_backup_task_uuid = uuid.uuid4().hex
        task = RemoteBackupTask.objects.create(schedule=self._schedule_object,
                                               ext_config=json.dumps({
                                                   'new_host_snapshot_info': new_host_snapshot_info,
                                                   'remote_backup_task_uuid': remote_backup_task_uuid,
                                                   'last_local_host_snapshot_id': last_local_host_snapshot_id
                                               }))
        self.log_info(
            'create RemoteBackupTask {} remote_backup_task_uuid:{} '
            'last_local_host_snapshot_id:{} new_host_snapshot_info:{}'
            ''.format(task.id, remote_backup_task_uuid, last_local_host_snapshot_id, new_host_snapshot_info))

    def _create_log_wrapper(self, msg, ignore=None):
        if ignore:
            if self._previous_sate in ignore:
                pass
            else:
                self._create_log(msg)
        else:
            self._create_log(msg)

    def _create_log(self, desc_info, debug='', log_type=HostLog.LOG_REMOTE_BACKUP):
        host = self._schedule_object.host
        create_log(host, log_type, desc_info, debug)
