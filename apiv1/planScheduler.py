import configparser
import datetime
import json
import os
import subprocess
import time
from threading import Thread, Lock, Event
import calendar
import django.utils.timezone as timezone
import psutil
from django.db.models.signals import post_save
from rest_framework import status

from apiv1.cluster_backup_task import ClusterBackupTaskExecutor
from apiv1.cluster_cdp_backup_task import ClusterCdpTaskExecutor
from apiv1.htb_logic import HTBScheduleExecute
from apiv1.htb_task import HTBFlowEntrance, SendTaskWork
from apiv1.logic_processors import query_system_info
from apiv1.models import (BackupTaskSchedule, CDPDiskToken, Host, UserQuota, HostSnapshotShare, ClusterBackupSchedule,
                          ClusterBackupTask, HTBSchedule, HTBTask, RemoteBackupSchedule, DiskSnapshot, StorageNode,
                          ArchiveSchedule, ArchiveTask, ImportSnapshotTask, VolumePool, EnumLink, FileBackupTask,
                          AutoVerifyTask, AutoVerifySchedule, HostSnapshot, FileSyncTask, FileSyncSchedule)
from apiv1.models_hook import send_content_obj_to_mq
from apiv1.remote_backup_logic_local import RemoteBackupLogicLocal
from apiv1.snapshot import Tokens, DiskSnapshotLocker, DiskSnapshotHash
from apiv1.storage_nodes import UserQuotaTools
from apiv1.tasks import BackupScheduleRetryHandle
from apiv1.views import BackupTaskScheduleExecute, StorageNodes, ClusterBackupTaskScheduleExecute
from box_dashboard import boxService, xlogging, xdata
from xdashboard.common import smtp
from xdashboard.handle.sysSetting import ether, storage
from xdashboard.models import DataDictionary, DeviceRunState, BackupDataStatt, StorageNodeSpace, UserQuotaSpace
from apiv1.archive_views import ArchiveScheduleExecute
from apiv1.archive_tasks import FlowEntrance, create_exp_flow, create_imp_flow
from apiv1.verify_tasks import VerifyFlowEntrance, create_verify_flow
from apiv1.file_sync_tasks import FileSyncFlowEntrance, create_sync_flow
from apiv1.file_sync_views import FileSyncScheduleExecute
from xdashboard.common.dict import GetDictionary
from apiv1.logic_processors import BackupTaskScheduleLogicProcessor
from apiv1.models import TakeOverKVM
from apiv1.takeover_logic import TakeOverKVMCreate
from apiv1.views import ClusterCdpBackupTaskScheduleExecute

backup_ini = '/sbin/aio/box_dashboard/backup.ini'
TIMER_INTERVAL_SECS = 60
INTERVAL_CHECK_BAK_DIR_COUNT = 86400
_logger = xlogging.getLogger(__name__)

update_dict = dict()
update_dict_locker = Lock()
update_dict_event = Event()
remote_schedule_created = Event()


def remote_backup_schedule_created(sender, **kwargs):
    if kwargs['created']:
        remote_schedule_created.set()
    else:
        pass


post_save.connect(remote_backup_schedule_created, sender=RemoteBackupSchedule)


# 计划是否具备执行资格<按照计划表>
class PlanScheduler(Thread):
    @staticmethod
    @xlogging.db_ex_wrap
    def run_scheduler():
        now_date_time = timezone.now()
        valid_plan_objects = BackupTaskSchedule.objects.filter(deleted=False).filter(enabled=True)

        # 遍历每一条计划，每条计划有五种模式
        for planObject in valid_plan_objects:
            hosts_need_backup = False

            # 对于CDP备份：不执行
            if planObject.cycle_type == BackupTaskSchedule.CYCLE_CDP and planObject.last_run_date is None:
                pass

            # 对于仅一次备份：执行一次计划 (只考虑到点执行)
            if planObject.cycle_type == BackupTaskSchedule.CYCLE_ONCE and planObject.next_run_date is not None:
                if now_date_time > planObject.next_run_date:
                    hosts_need_backup = True

            # 对于PerDay PerWeek PerMonth
            if planObject.cycle_type in \
                    (BackupTaskSchedule.CYCLE_PERDAY, BackupTaskSchedule.CYCLE_PERWEEK,
                     BackupTaskSchedule.CYCLE_PERMONTH):
                if planObject.next_run_date is not None:  # 避免比较类型不一致
                    if now_date_time > planObject.next_run_date:
                        hosts_need_backup = True

            # 对该计划的hosts发送备份命令
            if hosts_need_backup:
                rsp = BackupTaskScheduleExecute().post(request=None, backup_task_schedule_id=planObject.id)

                if rsp.status_code == status.HTTP_201_CREATED:
                    _logger.info('start BackupTaskSchedule id:{} name:{} ok'.format(planObject.id, planObject.name))
                else:
                    _logger.warning('start BackupTaskSchedule id:{} name:{} failed {}'
                                    .format(planObject.id, planObject.name, rsp.status_code))
                    BackupScheduleRetryHandle.modify(planObject, not boxService.get_retry_schedule_style())

    @staticmethod
    def _get_last_point(point_list):
        if len(point_list):
            point_list = sorted(point_list, key=lambda x: x['start'])
            return point_list[-1]
        return None

    @staticmethod
    @xlogging.convert_exception_to_value(None)
    @xlogging.db_ex_wrap
    def run_verify_schedule():
        from box_dashboard import xdatetime
        from apiv1.views import HostSnapshotsWithNormalPerHost, HostSnapshotsWithCdpPerHost, get_response_error_string
        now_date_time = timezone.now()
        verify_start_time = timezone.now()
        verify_schedules = AutoVerifySchedule.objects.filter(enabled=True)

        for verify_schedule in verify_schedules:
            need_verify = False
            ext_config = json.loads(verify_schedule.ext_config)
            # 2仅验证一次 3按间隔时间 4每周 5每月
            if verify_schedule.cycle_type == 2 and verify_schedule.next_run_date is not None:
                if now_date_time > verify_schedule.next_run_date:
                    need_verify = True
                    # 检查一天内的点
                    verify_start_time = verify_start_time - datetime.timedelta(seconds=1 * 24 * 60 * 60)

            if verify_schedule.cycle_type in (3, 4, 5,) \
                    and verify_schedule.next_run_date is not None:
                if now_date_time > verify_schedule.next_run_date:
                    need_verify = True

            if need_verify:
                verify_schedule.last_run_date = now_date_time
                if verify_schedule.cycle_type == 3:
                    timeinterval = int(ext_config['timeinterval'])
                    if ext_config['IntervalUnit'] == 'min':
                        interval_seconds = timeinterval * 60
                    elif ext_config['IntervalUnit'] == 'hour':
                        interval_seconds = timeinterval * 60 * 60
                    elif ext_config['IntervalUnit'] == 'day':
                        interval_seconds = timeinterval * 24 * 60 * 60
                    verify_start_time = verify_start_time - datetime.timedelta(seconds=interval_seconds)
                if verify_schedule.cycle_type == 4:
                    verify_start_time = verify_start_time - datetime.timedelta(seconds=7 * 24 * 60 * 60)
                if verify_schedule.cycle_type == 5:
                    verify_start_time = verify_start_time - datetime.timedelta(seconds=31 * 24 * 60 * 60)

                if verify_schedule.cycle_type == 2:
                    verify_schedule.next_run_date = None
                else:
                    logicProcessor = BackupTaskScheduleLogicProcessor(verify_schedule)
                    verify_schedule.next_run_date = logicProcessor.calc_next_run(False)
                verify_schedule.save(update_fields=['next_run_date', 'last_run_date', ])

                hosts_point = dict()

                host_list = list()

                for host in verify_schedule.hosts.all():
                    host_list.append(host)

                for host_group in verify_schedule.host_groups.all():
                    for host in host_group.hosts.all():
                        host_list.append(host)

                for host in host_list:
                    # 得到verify_start_time到现在的所有备份点
                    host_ident = host.ident
                    hosts_point[host_ident] = list()
                    api_request = {'begin': verify_start_time.strftime(xdatetime.FORMAT_WITH_MICROSECOND),
                                   'end': now_date_time.strftime(xdatetime.FORMAT_WITH_MICROSECOND),
                                   'use_serializer': False}

                    api_response = HostSnapshotsWithNormalPerHost().get(request=None, ident=host_ident,
                                                                        api_request=api_request)
                    if not status.is_success(api_response.status_code):
                        e = get_response_error_string(api_response)
                        _logger.error('run_verify_schedule HostSnapshotsWithNormalPerHost Failed.e={}'.format(e))

                    for host_snapshot in api_response.data:
                        point_id = '{}|{}|{}'.format(xdata.SNAPSHOT_TYPE_NORMAL, host_snapshot['id'],
                                                     host_snapshot['start_datetime'])

                        hosts_point[host_ident].append({
                            "id": point_id,
                            "start": host_snapshot['start_datetime']})

                    api_response = HostSnapshotsWithCdpPerHost().get(None, ident=host_ident, api_request=api_request)
                    if not status.is_success(api_response.status_code):
                        e = get_response_error_string(api_response)
                        _logger.error('run_verify_schedule HostSnapshotsWithCdpPerHost Failed.e={}'.format(e))

                    for host_snapshot in api_response.data:
                        point_id = '{}|{}|{}|{}'.format(xdata.SNAPSHOT_TYPE_CDP, host_snapshot['id'],
                                                        host_snapshot['begin'],
                                                        host_snapshot['end'])
                        hosts_point[host_ident].append({
                            "id": point_id,
                            "start": host_snapshot['end']
                        })

                    _logger.info('run_verify_schedule host_name={},begin={},end={},hosts_point={}'
                                 .format(host.name, api_request['begin'], api_request['end'], hosts_point[host_ident]))

                for host_ident in hosts_point.keys():
                    schedule_ext_config_obj = json.loads(verify_schedule.ext_config)
                    schedule_ext_config_obj['verify_schedule_id'] = verify_schedule.id
                    if ext_config['verify_last_point_only']:
                        point = PlanScheduler._get_last_point(hosts_point[host_ident])
                        if point:
                            point_id = point['id']
                            if AutoVerifyTask.objects.filter(point_id=point_id):
                                continue
                            AutoVerifyTask.objects.create(point_id=point_id, schedule_name=verify_schedule.name,
                                                          schedule_ext_config=json.dumps(schedule_ext_config_obj,
                                                                                         ensure_ascii=False),
                                                          storage_node_ident=verify_schedule.storage_node_ident)
                    else:
                        for point in hosts_point[host_ident]:
                            point_id = point['id']
                            if AutoVerifyTask.objects.filter(point_id=point_id):
                                continue
                            AutoVerifyTask.objects.create(point_id=point_id, schedule_name=verify_schedule.name,
                                                          schedule_ext_config=json.dumps(schedule_ext_config_obj,
                                                                                         ensure_ascii=False),
                                                          storage_node_ident=verify_schedule.storage_node_ident)
        verify_tasks = AutoVerifyTask.objects.filter(verify_type=AutoVerifyTask.VERIFY_TYPE_ING)
        if verify_tasks:
            # 同时只有一个任务在验证
            verify_task = verify_tasks.first()
            schedule_ext_config_obj = json.loads(verify_task.schedule_ext_config)

            stime = schedule_ext_config_obj.get('stime')
            if stime is None:
                _logger.error('run_verify_schedule stime is None Failed.set task end id={}'.format(verify_task.id))
                verify_task.verify_type = AutoVerifyTask.VERIFY_TYPE_END
                verify_task.save(update_fields=['verify_type'])
            elif stime + 3600 < time.time():
                # 任务执行超时了
                _logger.error('run_verify_schedule timeout Failed.set task end id={}'.format(verify_task.id))
                verify_task.verify_type = AutoVerifyTask.VERIFY_TYPE_END
                verify_task.save(update_fields=['verify_type'])
                takeover_id = schedule_ext_config_obj.get('takeover_id')
                if takeover_id:
                    # 强制关闭kvm并删除
                    from xdashboard.handle.takeover import takeover_close_kvm
                    from apiv1.takeover_logic import TakeOverKVMCreate
                    takeover_close_kvm(takeover_id)
                    api_request = {'id': takeover_id}
                    TakeOverKVMCreate().delete(request=None, api_request=api_request)
            else:
                return
        verify_tasks = AutoVerifyTask.objects.filter(verify_type=AutoVerifyTask.VERIFY_TYPE_QUEUE)
        if verify_tasks:
            try:
                verify_task = verify_tasks.first()
                verify_task.verify_type = AutoVerifyTask.VERIFY_TYPE_ING
                verify_task.save(update_fields=['verify_type'])
                task = VerifyFlowEntrance(verify_task.id, 'verify_flow_{}'.format(verify_task.id), create_verify_flow)
                task.generate_uuid()
                _logger.info(r'run AutoVerifyTask {}'.format(verify_task.id))
                task.start()
            except Exception as e:
                _logger.error(r'load AutoVerifyTask task {} failed {}'.format(verify_task.id, e), exc_info=True)
                verify_task.verify_type = AutoVerifyTask.VERIFY_TYPE_END
                verify_result = dict()
                verify_result['result'] = '{}'.format(e)
                verify_task.verify_result = json.dumps(verify_result, ensure_ascii=False)
                verify_task.save(update_fields=['verify_type', 'verify_result'])

    @staticmethod
    @xlogging.db_ex_wrap
    def run_cluster_backup_schedule():
        now_date_time = timezone.now()
        schedules = ClusterBackupSchedule.objects.filter(deleted=False).filter(enabled=True)

        # 遍历每一条计划，每条计划有五种模式
        for schedule_object in schedules:
            need_backup = False

            # 对于CDP备份：不执行
            if schedule_object.cycle_type == BackupTaskSchedule.CYCLE_CDP:
                pass

            # 对于仅一次备份：执行一次计划 (只考虑到点执行)
            if schedule_object.cycle_type == BackupTaskSchedule.CYCLE_ONCE:
                if schedule_object.next_run_date is not None and now_date_time > schedule_object.next_run_date:
                    need_backup = True

            # 对于PerDay PerWeek PerMonth
            if schedule_object.cycle_type in \
                    (BackupTaskSchedule.CYCLE_PERDAY, BackupTaskSchedule.CYCLE_PERWEEK,
                     BackupTaskSchedule.CYCLE_PERMONTH):
                if schedule_object.next_run_date is not None and now_date_time > schedule_object.next_run_date:
                    need_backup = True

            # 对该计划的hosts发送备份命令
            if need_backup:
                rsp = ClusterBackupTaskScheduleExecute().post(request=None, schedule_id=schedule_object.id)

                if rsp.status_code == status.HTTP_201_CREATED:
                    _logger.info('start ClusterBackupTaskSchedule id:{} name:{} ok'
                                 .format(schedule_object.id, schedule_object.name))
                else:
                    _logger.warning('start ClusterBackupTaskSchedule id:{} name:{} failed {}'
                                    .format(schedule_object.id, schedule_object.name, rsp.status_code))

    @staticmethod
    @xlogging.db_ex_wrap
    def run_archive_task():
        now_date_time = timezone.now()
        schedules = ArchiveSchedule.objects.filter(deleted=False, enabled=True)

        # 遍历每一条计划，每条计划有五种模式
        for schedule_object in schedules:
            need_backup = False

            # 对于CDP备份：不执行
            if schedule_object.cycle_type == BackupTaskSchedule.CYCLE_CDP:
                pass

            # 对于仅一次备份：执行一次计划 (只考虑到点执行)
            if schedule_object.cycle_type == BackupTaskSchedule.CYCLE_ONCE:
                if schedule_object.next_run_date is not None and now_date_time > schedule_object.next_run_date:
                    need_backup = True

            # 对于PerDay PerWeek PerMonth
            if schedule_object.cycle_type in \
                    (BackupTaskSchedule.CYCLE_PERDAY, BackupTaskSchedule.CYCLE_PERWEEK,
                     BackupTaskSchedule.CYCLE_PERMONTH):
                if schedule_object.next_run_date is not None and now_date_time > schedule_object.next_run_date:
                    need_backup = True

            # 对该计划的hosts发送备份命令
            if need_backup:
                rsp = ArchiveScheduleExecute().post(request=None, api_request={'schedule': schedule_object.id})

                if rsp.status_code == status.HTTP_201_CREATED:
                    _logger.info('start run_archive_task id:{} name:{} ok'
                                 .format(schedule_object.id, schedule_object.name))
                else:
                    _logger.warning('start run_archive_task id:{} name:{} failed {}'
                                    .format(schedule_object.id, schedule_object.name, rsp.status_code))

    @staticmethod
    @xlogging.db_ex_wrap
    def run_file_sync_schedule():
        now_date_time = timezone.now()
        schedules = FileSyncSchedule.objects.filter(deleted=False, enabled=True)

        # 遍历每一条计划，每条计划有五种模式
        for schedule_object in schedules:
            need_backup = False

            # 对于CDP备份：不执行
            if schedule_object.cycle_type == BackupTaskSchedule.CYCLE_CDP:
                pass

            # 对于仅一次备份：执行一次计划 (只考虑到点执行)
            if schedule_object.cycle_type == BackupTaskSchedule.CYCLE_ONCE:
                if schedule_object.next_run_date is not None and now_date_time > schedule_object.next_run_date:
                    need_backup = True

            # 对于PerDay PerWeek PerMonth
            if schedule_object.cycle_type in \
                    (BackupTaskSchedule.CYCLE_PERDAY, BackupTaskSchedule.CYCLE_PERWEEK,
                     BackupTaskSchedule.CYCLE_PERMONTH):
                if schedule_object.next_run_date is not None and now_date_time > schedule_object.next_run_date:
                    need_backup = True

            # 对该计划的hosts发送备份命令
            if need_backup:
                rsp = FileSyncScheduleExecute().post(request=None, api_request={'schedule': schedule_object.id})
                if rsp.status_code == status.HTTP_201_CREATED:
                    _logger.info('start run_file_sync_schedule id:{} name:{} ok'
                                 .format(schedule_object.id, schedule_object.name))
                else:
                    _logger.warning('start run_file_sync_schedule id:{} name:{} failed {}'
                                    .format(schedule_object.id, schedule_object.name, rsp.status_code))

    def run(self):
        # 确保各模块加载正常
        time.sleep(90)

        self.run_incomplete()
        self.run_incomplete_archive()
        self.run_incomplete_import_snapshot()
        self.run_incomplete_auto_verify()
        self.run_incomplete_file_sync()

        while True:
            try:
                self.run_scheduler()
                self.run_cluster_backup_schedule()
                self.run_archive_task()
                self.run_verify_schedule()
                self.run_file_sync_schedule()
            except Exception as e:
                _logger.error(r'PlanScheduler Exception : {}'.format(e), exc_info=True)
            # 有效plans的第二次调度间隔
            time.sleep(TIMER_INTERVAL_SECS)

    @staticmethod
    @xlogging.db_ex_wrap
    def run_incomplete_archive():
        cb_tasks = ArchiveTask.objects.filter(finish_datetime__isnull=True)
        for cb_task in cb_tasks:
            try:
                task = FlowEntrance(cb_task.id, 'export_flow_{}'.format(cb_task.id), create_exp_flow)
                task_uuid = json.loads(cb_task.running_task)
                task.load_from_uuid(task_uuid)
                _logger.info(r'load ArchiveTask {}'.format(cb_task.id))
                task.start()
            except Exception as e:
                _logger.error(r'load ArchiveTask task {} failed {}'.format(cb_task.id, e), exc_info=True)
                cb_task.finish_datetime = timezone.now()
                cb_task.save(update_fields=['finish_datetime'])

    @staticmethod
    @xlogging.db_ex_wrap
    def run_incomplete_import_snapshot():
        cb_tasks = ImportSnapshotTask.objects.filter(finish_datetime__isnull=True)
        for cb_task in cb_tasks:
            try:
                task = FlowEntrance(cb_task.id, 'import_flow_{}'.format(cb_task.id), create_imp_flow)
                task_uuid = json.loads(cb_task.running_task)
                task.load_from_uuid(task_uuid)
                _logger.info(r'load ImportSnapshotTask {}'.format(cb_task.id))
                task.start()
            except Exception as e:
                _logger.error(r'load ImportSnapshotTask task {} failed {}'.format(cb_task.id, e), exc_info=True)
                cb_task.finish_datetime = timezone.now()
                cb_task.save(update_fields=['finish_datetime'])

    @staticmethod
    @xlogging.db_ex_wrap
    def run_incomplete_auto_verify():
        take_overs = TakeOverKVM.objects.filter(kvm_type='verify_kvm').all()
        for take_over in take_overs:
            if os.path.isfile(take_over.kvm_flag_file):
                os.remove(take_over.kvm_flag_file)
            TakeOverKVMCreate().delete(request=None, api_request={'id': take_over.id})
        verify_tasks = AutoVerifyTask.objects.filter(verify_type=AutoVerifyTask.VERIFY_TYPE_ING)
        if verify_tasks:
            try:
                verify_task = verify_tasks.first()
                task = VerifyFlowEntrance(verify_task.id, 'verify_flow_{}'.format(verify_task.id), create_verify_flow)
                task_uuid = json.loads(verify_task.id)
                task.load_from_uuid(task_uuid)
                _logger.info(r'load AutoVerifyTask {}'.format(verify_task.id))
                task.start()
            except Exception as e:
                _logger.error(r'load AutoVerifyTask task {} failed {}'.format(verify_task.id, e), exc_info=True)
                verify_task.verify_type = AutoVerifyTask.VERIFY_TYPE_END
                verify_result = dict()
                verify_result['result'] = '{}'.format(e)
                verify_task.verify_result = json.dumps(verify_result, ensure_ascii=False)
                verify_task.save(update_fields=['verify_type', 'verify_result'])

    @staticmethod
    @xlogging.db_ex_wrap
    def run_incomplete():
        cb_tasks = ClusterBackupTask.objects.filter(finish_datetime__isnull=True)
        for cb_task in cb_tasks:
            try:
                task_uuid = json.loads(cb_task.task_uuid)

                if cb_task.schedule.cycle_type == BackupTaskSchedule.CYCLE_CDP:
                    task = ClusterCdpTaskExecutor()
                else:
                    task = ClusterBackupTaskExecutor()

                task.load_from_uuid(task_uuid, cb_task.id)
                _logger.info(r'load ClusterBackupTask {}'.format(cb_task.id))
                task.start()
            except Exception as e:
                _logger.error(r'load ClusterBackupTask task {} failed {}'.format(cb_task.id, e), exc_info=True)
                cb_task.finish_datetime = timezone.now()
                cb_task.save(update_fields=['finish_datetime'])

    @staticmethod
    @xlogging.db_ex_wrap
    def run_incomplete_file_sync():
        cb_tasks = FileSyncTask.objects.filter(finish_datetime__isnull=True)
        for cb_task in cb_tasks:
            try:
                task = FileSyncFlowEntrance(cb_task.id, 'file_sync_{}'.format(cb_task.id), create_exp_flow)
                task.load_from_uuid(json.loads(cb_task.ext_config)['running_task'])
                _logger.info(r'load file sync task {}'.format(cb_task.id))
                task.start()
            except Exception as e:
                _logger.error(r'load file sync task {} failed {}'.format(cb_task.id, e), exc_info=True)
                cb_task.finish_datetime = timezone.now()
                cb_task.save(update_fields=['finish_datetime'])


# 触发cdp备份：
# 1.立即执行cdp计划
# 2.轮询cdp计划是否异常
class CdpChecker(Thread):
    _cdp = BackupTaskSchedule.CYCLE_CDP

    # 获取需要执行的cdp_plans(没有CDPTasks，或CDPTasks都已经完成)
    def get_cdp_plans_need_exc(self):
        cdp_plans = BackupTaskSchedule.objects.filter(deleted=False).filter(enabled=True).filter(cycle_type=self._cdp)
        plans_need_exc = list()
        last_finished_datetime = timezone.now() - datetime.timedelta(seconds=120)
        for plan_obj in cdp_plans:
            if plan_obj.plan_start_date:
                if not plan_obj.last_run_date and plan_obj.plan_start_date > timezone.now():
                    continue

            plan_cdptasks = plan_obj.cdp_tasks.all()

            if 'cdping' not in ['done' if (cdp_task.finish_datetime
                                           and cdp_task.finish_datetime < last_finished_datetime)
                                else 'cdping' for cdp_task in plan_cdptasks]:
                plans_need_exc.append(plan_obj)

        return plans_need_exc

    # cdp_plan的host状态是'idle'(排它性)，立即执行CDP计划
    @xlogging.convert_exception_to_value(None)
    @xlogging.db_ex_wrap
    def exc_cdp_plans(self):
        cdp_plans = self.get_cdp_plans_need_exc()
        for cdp_plan in cdp_plans:
            host_stat = boxService.box_service.GetStatus(cdp_plan.host.ident)
            if 'idle' in host_stat:
                rsp = BackupTaskScheduleExecute().post(request=None, backup_task_schedule_id=cdp_plan.id)
                rsp_code = rsp.status_code

                if rsp_code == status.HTTP_201_CREATED:
                    _logger.info('start BackupTaskSchedule id:{} name:{} ok'.format(cdp_plan.id, cdp_plan.name))
                else:
                    _logger.warning(
                        'start BackupTaskSchedule id:{} name:{} failed {}'.format(cdp_plan.id, cdp_plan.name, rsp_code))

    def get_cluster_cdp_plans_need_exc(self):
        cdp_plans = ClusterBackupSchedule.objects.filter(deleted=False, enabled=True, cycle_type=self._cdp)
        plans_need_exc = list()
        last_finished_datetime = timezone.now() - datetime.timedelta(seconds=120)
        for plan_obj in cdp_plans:
            if plan_obj.plan_start_date:
                if not plan_obj.last_run_date and plan_obj.plan_start_date > timezone.now():
                    continue

            plan_cdptasks = plan_obj.backup_tasks.all()

            if 'cdping' not in ['done' if (cdp_task.finish_datetime
                                           and cdp_task.finish_datetime < last_finished_datetime)
                                else 'cdping' for cdp_task in plan_cdptasks]:
                plans_need_exc.append(plan_obj)

        return plans_need_exc

    def _is_all_host_idle(self, hosts):
        for host in hosts:
            host_stat = boxService.box_service.GetStatus(host.ident)
            if 'idle' not in host_stat:
                return False
        return True

    @xlogging.convert_exception_to_value(None)
    @xlogging.db_ex_wrap
    def exc_cluster_cdp_plans(self):
        cluster_cdp_plans = self.get_cluster_cdp_plans_need_exc()
        for cluster_cdp_plan in cluster_cdp_plans:
            if self._is_all_host_idle(cluster_cdp_plan.hosts.all()):
                rsp = ClusterCdpBackupTaskScheduleExecute().post(None, cluster_cdp_plan.id, api_request={
                    'type': xdata.BACKUP_TASK_SCHEDULE_EXECUTE_TYPE_AUTO,
                    'force_store_full': '0'})
                rsp_code = rsp.status_code

                if rsp_code == status.HTTP_201_CREATED:
                    _logger.info(
                        'start ClusterCdpBackupTaskScheduleExecute id:{} name:{} ok'.format(cluster_cdp_plan.id,
                                                                                            cluster_cdp_plan.name))
                else:
                    _logger.warning(
                        'start ClusterCdpBackupTaskScheduleExecute id:{} name:{} failed {}'.format(cluster_cdp_plan.id,
                                                                                                   cluster_cdp_plan.name,
                                                                                                   rsp_code))

    # 间隔60秒执行一次
    def run(self):
        time.sleep(120)
        while True:
            self.exc_cdp_plans()
            self.exc_cluster_cdp_plans()
            time.sleep(TIMER_INTERVAL_SECS)


class CdpRotatingFile(Thread):
    def __init__(self, rotating_file_interval):
        super(CdpRotatingFile, self).__init__()
        self.CDP_ROTATING_FILE_CHECK_SECONDS = rotating_file_interval / 10
        self.CDP_ROTATING_FILE_TIMESTAMP_SECONDS = rotating_file_interval

    def run(self):
        time.sleep(self.CDP_ROTATING_FILE_CHECK_SECONDS)
        while True:
            try:
                self.exc_cdp_rotating_file()
            except Exception as e:
                _logger.error(r'CdpRotatingFile Exception : {}'.format(e), exc_info=True)
            time.sleep(self.CDP_ROTATING_FILE_CHECK_SECONDS)

    @xlogging.db_ex_wrap
    def exc_cdp_rotating_file(self):
        using_tokens = CDPDiskToken.objects.filter(using_disk_snapshot__isnull=False).all()
        now_timestamp = timezone.now().timestamp()
        for token in using_tokens:
            self.exec_one(token, now_timestamp)

    @xlogging.convert_exception_to_value(None)
    def exec_one(self, token_object, now_timestamp):
        if (now_timestamp - token_object.using_disk_snapshot.cdp_info.first_timestamp) \
                > self.CDP_ROTATING_FILE_TIMESTAMP_SECONDS:
            Tokens.change_cdp_file_logic(token_object.id, token_object.using_disk_snapshot.image_path, True)


class AcquireDeviceData(Thread):
    def __init__(self, time_spacing=60):
        super(AcquireDeviceData, self).__init__()
        self.time_spacing = time_spacing
        self.last_disk_read_value = 0
        self.last_disk_write_value = 0
        self.cyc_times = 0

    def run(self):
        time.sleep(60)
        while True:
            try:
                self.work()
            except Exception as e:
                _logger.error('AcquireDeviceData error:{}'.format(e), exc_info=True)
            time.sleep(self.time_spacing)

    @xlogging.db_ex_wrap
    def work(self):
        self._worker()
        if self.cyc_times == xdata.NET_DISK_IO_SAMPLE_CYC_TIMES_SEC / xdata.NET_DISK_IO_SAMPLE_INTERVAL_SEC:
            self._truncate_data()
            self.cyc_times = 0
        self.cyc_times += 1

    def _worker(self):
        self._acquire_network_change()
        self._acquire_disk_io_change()

    @staticmethod
    def _is_support_disk(disk_name):
        for prefix in ['sd', 'hd', 'xvd', 'vd']:
            if disk_name.startswith(prefix):
                return True
        return False

    def _acquire_disk_io_change(self):
        try:
            basedata = psutil.disk_io_counters(perdisk=True)
            read_bytes_total = 0
            write_bytes_total = 0
            if not basedata:
                return
            for perdisk in basedata:
                if self._is_support_disk(perdisk):
                    read_bytes_total += basedata[perdisk].read_bytes
                    write_bytes_total += basedata[perdisk].write_bytes
            if self.last_disk_read_value == 0 or self.last_disk_write_value == 0:
                self.last_disk_read_value = read_bytes_total
                self.last_disk_write_value = write_bytes_total
                return
            read_value = abs((read_bytes_total - self.last_disk_read_value) / self.time_spacing)
            write_value = abs((write_bytes_total - self.last_disk_write_value) / self.time_spacing)
            self.last_disk_read_value = read_bytes_total
            self.last_disk_write_value = write_bytes_total
            DeviceRunState.objects.create(type=DeviceRunState.TYPE_DISK_IO, readvalue=read_value,
                                          writevalue=write_value)
        except Exception as e:
            _logger.info('AcquireDeviceData, worker ,_acquire_disk_io_change,error:{}'.format(e))
            raise e

    @staticmethod
    def _acquire_network_change():
        try:
            ether.update_ether_rx_tx_speed_to_db()
        except Exception as e:
            _logger.info('AcquireDeviceData, worker ,_acquire_network_change,error:{}'.format(e))
            raise e

    @staticmethod
    def _acquire_storage_state():
        try:
            storage.update_all_node_all_user_storage_statt()
        except Exception as e:
            _logger.info('AcquireDeviceData, worker ,_acquire_storage_state,error:{}'.format(e))

    @staticmethod
    def _truncate_data():
        expir_date = datetime.datetime.now() - datetime.timedelta(days=xdata.NET_DISK_IO_SAMPLE_TRUNCATE_DAYS)
        DeviceRunState.objects.filter(datetime__lte=expir_date).delete()

        expir_date_statt = datetime.datetime.now() - datetime.timedelta(days=xdata.STORAGE_GRAPH_TRUNCATE_DAYS)
        BackupDataStatt.objects.filter(date_time__lte=expir_date_statt).delete()
        StorageNodeSpace.objects.filter(time_date__lte=expir_date_statt).delete()
        UserQuotaSpace.objects.filter(date_time__lte=expir_date_statt).delete()


# 后台刷新在线host系统信息, 同时探测主机是否在线
class UpdateHostSysInfo(Thread):
    def __init__(self):
        super(UpdateHostSysInfo, self).__init__(name='UpdateHostSysInfo_threading')

    @staticmethod
    @xlogging.LockDecorator(update_dict_locker)
    def update(ident, data_time=None):
        global update_dict
        update_dict[ident] = data_time if data_time else timezone.now()
        if not update_dict_event.is_set():
            update_dict_event.set()

    @xlogging.LockDecorator(update_dict_locker)
    def _init_or_update_update_dict(self):
        global update_dict
        all_host = UpdateHostSysInfo.get_hosts_with_link()
        if os.path.exists('/dev/shm/update_host'):
            return {host.ident: timezone.now() for host in all_host}
        update_dict = {host.ident: update_dict.get(host.ident, timezone.now()) for host in all_host}
        return [host_ident for host_ident, _time in update_dict.items() if timezone.now() >= _time]

    @staticmethod
    def _update_hosts_sys_info(host_idents):
        for host_ident in host_idents:
            host = Host.objects.get(ident=host_ident)
            res = query_system_info(host, update=True)
            if res:
                UpdateHostSysInfo.update(host_ident, timezone.now() + datetime.timedelta(
                    seconds=xdata.UPDATE_HOST_SYS_INFO_TIME_SEC))
            _logger.info(r'UpdateHostSysInfo: update host {},{}'.format(host.ident, 'successful' if res else 'failed'))

    @staticmethod
    def get_hosts_with_link():
        return list(filter(lambda host: host.is_linked, Host.objects.filter(type__in=[Host.AGENT, Host.PROXY_AGENT])))

    def run(self):
        time.sleep(60)  # waiting for steady

        while True:
            try:
                self.worker()
            except Exception as e:
                _logger.error('UpdateHostSysInfo threading error:{}'.format(e), exc_info=True)
            time.sleep(5)

    @xlogging.db_ex_wrap
    def worker(self):
        host_idents = self._init_or_update_update_dict()
        self._update_hosts_sys_info(host_idents)
        if update_dict_event.wait(5):
            update_dict_event.clear()


class HostSessionMonitor(Thread):
    def __init__(self):
        super(HostSessionMonitor, self).__init__(name='HostSessionMonitor')
        self._ident2datetime = dict()  # item：datetime
        self._timeout_delta = datetime.timedelta(minutes=10)

    def run(self):
        time.sleep(60)

        while True:
            try:
                self.work()
            except Exception as e:
                _logger.error('HostSessionMonitor error:{}'.format(e), exc_info=True)
            time.sleep(5)

    @xlogging.db_ex_wrap
    def work(self):
        host_idents = self._init_and_get_online_host_idents()
        for host_ident in host_idents:
            if self._is_host_offline(host_ident):
                if self._is_time_out(host_ident):
                    self._force_off_line_host(host_ident)
                else:
                    pass
            else:
                self._clear(host_ident)

    def _init_and_get_online_host_idents(self):
        host_idents = [host.ident for host in UpdateHostSysInfo.get_hosts_with_link()]
        self._ident2datetime = {ident: off_datetime for ident, off_datetime in self._ident2datetime.items() if ident
                                in host_idents}
        return host_idents

    @staticmethod
    def _is_host_offline(host_ident):
        host_stat = boxService.box_service.GetStatus(host_ident)
        return 'off_line' in host_stat

    def _is_time_out(self, host_ident):
        self._ident2datetime.setdefault(host_ident, timezone.now())
        return (timezone.now() - self._ident2datetime[host_ident]) >= self._timeout_delta

    @xlogging.convert_exception_to_value(None)
    def _force_off_line_host(self, host_ident):
        _logger.warning('HostSessionMonitor detecting host:{} off_line, start logout it'.format(host_ident))
        Host.objects.get(ident=host_ident).logout()
        boxService.box_service.forceOfflineAgent(host_ident)
        _logger.warning('HostSessionMonitor detecting host:{} off_line, logout it end'.format(host_ident))

    def _clear(self, host_ident):
        self._ident2datetime.pop(host_ident, None)


# 对全部存储单元采样：可用空间，使用空间，所有用户在该Node上的RAW_DATA
class RecordNodesSpace(Thread):
    @staticmethod
    @xlogging.db_ex_wrap
    def _get_nodes_and_save_db():
        for node in StorageNodes().get(None, True).data:
            tt_bytes = node['total_bytes']
            av_bytes = node['available_bytes']

            node['total_bytes'] = node['total_bytes'] if node['total_bytes'] else 0
            node['available_bytes'] = node['available_bytes'] if node['available_bytes'] else 0
            send_content_obj_to_mq(node, False, 'storage_node')  # node 的结构为 StorageNodeSerializer

            _logger.info(r'rrr  {}'.format(node))

            if node['deleted']:
                continue

            if None in [tt_bytes, av_bytes]:
                tt_bytes, av_bytes, raw_data_bytes = 0, 0, 0
            else:
                raw_data_bytes = storage.all_users_raw_data_bytes_in_a_node(node['id'])

            StorageNodeSpace.objects.create(node_id=node['id'], total_bytes=tt_bytes, free_bytes=av_bytes,
                                            raw_data_bytes=raw_data_bytes)

    def run(self):
        time.sleep(60)  # waiting for steady
        while True:
            self._get_nodes_and_save_db()
            time.sleep(xdata.RECORD_NODES_SPACE_SEC)


# 对全部有效配额采样：可用空间，使用空间，该用户在该Node上的RAW_DATA
class RecordQuotaSpace(Thread):
    @staticmethod
    @xlogging.db_ex_wrap
    def _get_quotas_and_save_db():
        all_quotas = UserQuota.objects.filter(deleted=False)
        for quota in all_quotas:
            node_id, user_id = quota.storage_node.id, quota.user.id
            node_tools = UserQuotaTools(node_id, user_id, quota.quota_size)
            try:
                free_bytes = node_tools.get_user_available_storage_size_in_node() * 1024 ** 2
                used_bytes = storage.user_used_size_mb_in_a_node(node_id, user_id) * 1024 ** 2
                raw_data_bytes = storage.user_raw_data_bytes_in_a_node(node_id, user_id)
            except Exception as e:
                _logger.warning('RecordQuotaSpace Exception: {0}'.format(e))
                free_bytes, used_bytes, raw_data_bytes = 0, 0, 0

            quota.available_size = free_bytes // (1024 ** 2)
            quota.save(update_fields=['available_size', ])
            UserQuotaSpace.objects.create(quota_id=quota.id, free_bytes=free_bytes, used_bytes=used_bytes,
                                          raw_data_bytes=raw_data_bytes)

    def run(self):
        time.sleep(60)  # waiting for steady
        while True:
            self._get_quotas_and_save_db()
            time.sleep(xdata.RECORD_NODES_SPACE_SEC)


class ShareTimeout(Thread):
    is_checked_a = False
    is_checked_b = False

    @staticmethod
    def _is_time_out(share_obj):
        now = timezone.now()
        return (now - share_obj.share_start_time).total_seconds() > xdata.HOST_SNAPSHOTSHARE_TIMEOUT

    def _timeout_hostsnapshot(self):
        sharing_snapshots = HostSnapshotShare.objects.all()
        return list(filter(self._is_time_out, sharing_snapshots))

    def _sent_email_to_user(self):
        timeout_snapshots = self._timeout_hostsnapshot()
        username = set()
        for snapshot_share in timeout_snapshots:
            username.add(snapshot_share.login_user)

        for name in username:
            smtp.send_mail(name, sub='超时的备份点浏览', content='存在超过12小时的备份点浏览,建议关闭')

    def run(self):
        time.sleep(60)

        while True:
            try:
                self.work()
            except Exception as e:
                _logger.error('ShareTimeout error:{}'.format(e), exc_info=True)

    @xlogging.db_ex_wrap
    def work(self):
        now = timezone.now()
        today_datetime_a = datetime.datetime(now.year, now.month, now.day, 0, 0, 0)  # 00:00:00
        today_datetime_b = datetime.datetime(now.year, now.month, now.day, 12, 0, 0)  # 12:00:00
        today_datetime_clr = datetime.datetime(now.year, now.month, now.day, 13, 0, 0)  # 13:00:00
        # 当天13:00:00清除标志位
        if abs((now - today_datetime_clr).total_seconds()) < 60 and self.is_checked_a or self.is_checked_b:
            self.is_checked_a, self.is_checked_b = False, False

        # 当天00:00:00检查一次
        if abs((now - today_datetime_a).total_seconds()) < 60 and not self.is_checked_a:
            self.is_checked_a = True
            self._sent_email_to_user()

        # 当天12:00:00检查一次
        if abs((now - today_datetime_b).total_seconds()) < 60 and not self.is_checked_b:
            self.is_checked_b = True
            self._sent_email_to_user()
        time.sleep(100)


class HTBScheduleMonitor(Thread):
    def run(self):
        # 确保各模块加载正常
        time.sleep(60)

        self.run_incomplete()

        # _logger.debug('HTBScheduleMonitor start...')
        #
        # while True:
        #     try:
        #         self.run_scheduler()
        #     except Exception as e:
        #         _logger.error(r'HBScheduleMonitor Exception : {}'.format(e), exc_info=True)
        #     # 有效plans的第二次调度间隔
        #     time.sleep(TIMER_INTERVAL_SECS)

    @staticmethod
    def run_scheduler():
        schedules = HTBSchedule.objects.filter(deleted=False, enabled=True)
        for schedule in schedules:
            # 存在正在执行的热备任务
            if schedule.htb_task.filter(start_datetime__isnull=False, finish_datetime__isnull=True).exists():
                continue
            else:
                _logger.debug('get one schedule:{}|{}'.format(schedule.name, schedule.id))
                rsp = HTBScheduleExecute().post(schedule.id)
                if status.is_success(rsp.status_code):
                    _logger.debug('send schedule:{} successful!'.format(schedule.id))
                else:
                    _logger.error('send schedule:{} fail, error:{}'.format(schedule.id, rsp.data))

    @xlogging.convert_exception_to_value(None)
    def run_incomplete(self):
        _logger.debug('HTBScheduleMonitor start...')
        unfinished_task = HTBTask.objects.filter(start_datetime__isnull=False, finish_datetime__isnull=True)
        for htb_task in unfinished_task:
            try:
                _logger.info('run_incomplete load exists htb_task:{}'.format(htb_task.id))
                hb_flow = HTBFlowEntrance(htb_task.id)
                hb_flow.load_from_uuid(json.loads(htb_task.running_task))
                hb_flow.start()
            except Exception as e:
                htb_task.finish_datetime = timezone.now()
                htb_task.successful = False
                htb_task.save(update_fields=['finish_datetime', 'successful'])
                self.close_htb_task(htb_task)
                _logger.error('run_incomplete htb_task:{},error:{}'.format(htb_task.id, e), exc_info=True)
        _logger.debug('HTBScheduleMonitor end...')

    @staticmethod
    def check_in_work(htb_task, send_task):
        code, _type = boxService.box_service.QueryWorkStatus(htb_task.task_uuid, send_task.disk_token)
        if _type != 0:
            return True

    @staticmethod
    def close_htb_task(htb_task):
        for send_task in htb_task.send_task.all():
            try:
                send_task_work = SendTaskWork(htb_task, send_task)
                send_task_work.stop()
                send_task_work.close_send_task()
            except Exception as e:
                _logger.error('close_htb_task error:{}'.format(e))


class RemoteBackupScheduleMonitor(Thread):
    def __init__(self):
        super(RemoteBackupScheduleMonitor, self).__init__(name='RemoteBackupScheduleMonitor')
        self._running = list()

    def run(self):
        # 确保各模块加载正常
        time.sleep(60)

        # 解锁所有快照
        DiskSnapshotLocker.unlock_files_by_task_name_prefix('remote_back_up_')

        while True:
            try:
                self.worker()
            except Exception as e:
                _logger.error('RemoteBackupScheduleMonitor error:{}'.format(e), exc_info=True)

            if remote_schedule_created.wait(15):
                _logger.info('RemoteBackupScheduleMonitor find new schedule')
                remote_schedule_created.clear()

    @xlogging.db_ex_wrap
    def worker(self):
        self._clear_finish_thread()
        self._start_logic_thread()

    def _clear_finish_thread(self):
        running = self._running.copy()
        for logic_thread in running:
            if not logic_thread.is_alive():
                _logger.info('RemoteBackupScheduleMonitor remove : {}'.format(logic_thread.name))
                self._running.remove(logic_thread)

    def _start_logic_thread(self):
        for schedule_object in RemoteBackupSchedule.objects.filter(enabled=True, deleted=False):
            if not self._is_running(schedule_object.id):
                try:
                    logic_thread = RemoteBackupLogicLocal(schedule_object)
                    logic_thread.setDaemon(True)
                    logic_thread.start()
                    _logger.info('RemoteBackupScheduleMonitor append : {}'.format(logic_thread.name))
                    self._running.append(logic_thread)
                except Exception as e:
                    _logger.error('_start_logic_thread error:{}'.format(e), exc_info=True)

    def _is_running(self, schedule_object_id):
        for logic_thread in self._running:
            if logic_thread.schedule_object_id == schedule_object_id:
                return True
        return False


class ReorganizeHashFileThread(Thread):

    def __init__(self):
        super(ReorganizeHashFileThread, self).__init__(name='ReorganizeHashFileThread')

    def run(self):
        _logger.info('ReorganizeHashFileThread start')
        time.sleep(60)

        try:
            self._work()
        except Exception as e:
            _logger.error('GenerateHashFileThread error:{}'.format(e), exc_info=True)

        _logger.info('ReorganizeHashFileThread end')

    def _work(self):
        disk_snapshots = self._fetch_valid_disk_snapshots()

        for disk_snapshot in disk_snapshots:
            try:
                DiskSnapshotHash.reorganize_hash_file_by_disk_snapshot(disk_snapshot)
            except Exception as e:
                _logger.error('DiskSnapshotHash.reorganize_hash_file fail:{}'.format(e), exc_info=True)

    @staticmethod
    def _fetch_valid_disk_snapshots():
        return DiskSnapshot.objects.filter(merged=False,
                                           reorganized_hash=False,
                                           host_snapshot__successful=True,
                                           host_snapshot__deleted=False,
                                           host_snapshot__finish_datetime__isnull=False,
                                           host_snapshot__deleting=False,
                                           host_snapshot__host__type__in=(Host.PROXY_AGENT, Host.AGENT)
                                           ).order_by('-id')


class BackupDatabase(Thread):

    @xlogging.db_ex_wrap
    def back_db_xlog(self):
        """
        增量备份
        :return:
        """
        back_db_xlog_cmd = 'export PGPASSWORD=f;psql -h 127.0.0.1 -p 21114 -U postgres postgres -c ' \
                           '"checkpoint;select pg_switch_xlog()";'
        subprocess.call(back_db_xlog_cmd, shell=True)

    def run(self):
        while True:
            check_value = DataDictionary.objects.filter(dictKey='ebe_db_bak').first()
            if (check_value is not None) and (int(check_value.dictValue) == 1):
                obj = DataDictionary.objects.filter(dictKey='bak_hour').first()
                interval_increment = int(obj.dictValue) * 3600
                if interval_increment <= 0:
                    interval_increment = 180
                    _logger.info('not increment backup')
                else:
                    try:
                        _logger.debug('back_db_xlog is alive')
                        self.back_db_xlog()
                    except Exception as e:
                        _logger.error(r'back_db_xlog Exception : {}'.format(e), exc_info=True)
            else:
                interval_increment = 180
            time.sleep(interval_increment)


class BackupDatabaseBase(Thread):
    @xlogging.db_ex_wrap
    def back_db_base(self):
        """
        这个函数是对data目录的基础备份
        :return:
        """
        obj = DataDictionary.objects.filter(dictKey='db_bak_dev').first()
        if obj is None:
            return
        else:
            save_ident = obj.dictValue
            obj1 = StorageNode.objects.filter(ident=save_ident).first()
            if obj1 is not None:
                save_path = obj1.path
                date_string = time.strftime('%Y%m%d', time.localtime(time.time()))
                config = configparser.ConfigParser()
                config.read(backup_ini)
                path_ = save_path + config.get("bak_db_path", "save_bak_path") + date_string
                subprocess.call('chown postgres:postgres ' + path_, shell=True)
                save_bak_path = path_ + '/base'
                not_db_bak = config.items('bak_path')
                t = dict(not_db_bak)
                if not os.path.exists(save_bak_path):
                    os.makedirs(save_bak_path)
                    subprocess.call('chown postgres:postgres ' + save_bak_path, shell=True)
                if os.path.exists(t['need_bak_var_db']):
                    subprocess.call('rm -rf ' + t['need_bak_var_db'], shell=True)
                os.makedirs(t['need_bak_var_db'])
                not_bak = ['last_time', 'pgsql', 'last_time', 'drvierid.db']
                for file in os.listdir(t['var_db']):
                    if file not in not_bak:
                        subprocess.call('cp ' + t['var_db'] + file + ' ' + t['need_bak_var_db'], shell=True)
                cmd = 'tar -czPf' + save_bak_path + '/need_bak_var_db.tar.gz' + ' ' + t['need_bak_var_db'] + '/*'
                subprocess.call(cmd, shell=True)
                for key in t:
                    if 'var_db' not in key:
                        if 'sbin_aio' in key:
                            tar_cmd = 'tar -czPf ' + save_bak_path + '/' + key + '.tar.gz ' + t[key] + '/version.inc;'
                        else:
                            tar_cmd = 'tar -czPf ' + save_bak_path + '/' + key + '.tar.gz ' + t[key] + '/*;'
                        subprocess.call(tar_cmd, shell=True)
                db_base = save_bak_path + '_db_base'
                if not os.path.exists(db_base):
                    os.makedirs(db_base)
                bak_cmd = 'pg_basebackup -F t -x -D ' + db_base + ' -h 127.0.0.1 -p 21114 -U rep'
                subprocess.call(bak_cmd, shell=True)
            else:
                return

    def run(self):
        while True:
            check_value = DataDictionary.objects.filter(dictKey='ebe_db_bak').first()
            if (check_value is not None) and (int(check_value.dictValue) == 1):
                obj = DataDictionary.objects.filter(dictKey='bak_day').first()
                interval_base = int(obj.dictValue) * 86400
                if interval_base <= 0:
                    interval_base = 180
                    _logger.info('not increment backup')
                else:
                    try:
                        _logger.debug('back_db_xlog is alive')
                        self.back_db_base()
                    except Exception as e:
                        _logger.error(r'back_db_xlog Exception : {}'.format(e), exc_info=True)
            else:
                interval_base = 180
            time.sleep(interval_base)


class DatabaseSaveDays(Thread):
    @xlogging.db_ex_wrap
    def check_databasebackup_count(self):
        obj = DataDictionary.objects.filter(dictKey='db_sav_day').first()
        obj1 = DataDictionary.objects.filter(dictKey='db_bak_dev').first()
        if (obj is None) or (obj1 is None):
            return
        else:
            save_day = int(obj.dictValue)
            save_ident = obj1.dictValue
            save_path = StorageNode.objects.filter(ident=save_ident).first().path
            config = configparser.ConfigParser()
            config.read(backup_ini)
            save_bak_path = save_path + config.get("bak_db_path", "save_bak_path")
            dir_list = []
            count = 0
            for file in os.listdir(save_bak_path):
                dir_list.append(int(file))
            for i in sorted(dir_list):
                count += 1
                if count <= len(dir_list) - save_day:
                    subprocess.call('rm -rf ' + save_bak_path + str(i), shell=True)

    def run(self):
        while True:
            try:
                _logger.debug('check_databasebackup_count is alive')
                self.check_databasebackup_count()
            except Exception as e:
                _logger.error(r'check_databasebackup_count Exception : {}'.format(e), exc_info=True)
            time.sleep(INTERVAL_CHECK_BAK_DIR_COUNT)


class ArchiveMediaManager(Thread):

    def __init__(self):
        super(ArchiveMediaManager, self).__init__(name='ArchiveMediaManager')

    def run(self):
        # time.sleep(60)

        self.enum_mc_hw_info()

        while True:
            try:
                self.work()
            except Exception as e:
                _logger.error('ArchiveMediaManager work error:{}'.format(e), exc_info=True)
            time.sleep(60)

    @staticmethod
    @xlogging.convert_exception_to_value(None)
    def enum_mc_hw_info():
        if not EnumLink.objects.exists():
            ArchiveMediaManager.refresh_link_info()

    @staticmethod
    def refresh_link_info():
        try:
            EnumLink.objects.all().delete()
            params = {'action': 'enum_mc_hw_info', 'info': {'fun': 'enum_link'}}
            enumlink = json.loads(boxService.box_service.archiveMediaOperation(json.dumps(params)))
            DriveList = enumlink['rev']['DriveList']
            for link in DriveList:
                EnumLink.objects.create(driver=link['DriveSN'], library=link['MCSN'], drvid=link['MCBoxID'])
        except Exception as e:
            _logger.error('refresh_link_info error:{}'.format(e), exc_info=True)
            return False
        return True

    @xlogging.convert_exception_to_value(None)
    def add_media_pool(self):
        #  将本地目录作为归档目录，作为测试使用
        for storage in StorageNode.objects.filter(available=True, deleted=False):
            path = os.path.join(storage.path, 'archive_data')
            if not boxService.box_service.isFolderExist(path):
                os.makedirs(path)
            params = {
                'action': 'add',
                'info': {
                    'media_type': 'local',
                    'media_uuid': storage.ident,
                    'info': {'path': path, 'max_days': 1}
                }
            }
            boxService.box_service.archiveMediaOperation(json.dumps(params))

        link_dict = {'DriveList': []}
        for link in EnumLink.objects.all():
            link_dict['DriveList'].append({'DriveSN': link.driver, 'MCSN': link.library, 'MCBoxID': link.drvid})

        for storage_pool in VolumePool.objects.all():
            params = {
                'action': 'add',
                'info': {
                    'media_type': 'tape',
                    'media_uuid': storage_pool.pool_uuid,
                    'info': {'name': storage_pool.name, 'driver': storage_pool.driver, 'cycle': storage_pool.cycle,
                             'cycle_type': storage_pool.cycle_type, 'tapas': json.loads(storage_pool.tapas),
                             'max_days': storage_pool.max_days,
                             'link': link_dict}
                }
            }
            boxService.box_service.archiveMediaOperation(json.dumps(params))

    @xlogging.db_ex_wrap
    def work(self):
        self.add_media_pool()


class SetNetIfMtu(Thread):
    @xlogging.db_ex_wrap
    def _worker(self):
        mtu = int(GetDictionary(DataDictionary.DICT_TYPE_AIO_NETWORK, 'aio_mtu', -1))
        if mtu == -1:
            return
        for name, stats in psutil.net_if_stats().items():
            if name == 'lo':
                continue
            if stats.mtu == mtu:
                continue
            self._update_net_if_mtu(name, mtu)

    @staticmethod
    def _update_net_if_mtu(name, mtu):
        with subprocess.Popen('ip link set {} mtu {}'.format(name, mtu),
                              shell=True,
                              stderr=subprocess.PIPE,
                              stdout=subprocess.PIPE) as p:
            out, error = p.communicate()
        if p.returncode == 0:
            pass
        else:
            _logger.error('SetNetIfMtu update net if {} mtu to {} failed, {}|{}|{}'.format(name, mtu,
                                                                                           p.returncode,
                                                                                           out,
                                                                                           error))

    def run(self):
        _logger.info('SetNetIfMtu threading start')
        time.sleep(60)  # waiting for steady

        while True:
            try:
                self._worker()
            except Exception as e:
                _logger.error('SetNetIfMtu work error:{}'.format(e), exc_info=True)
            time.sleep(60)


class CleanHostSnapshot(Thread):

    @xlogging.db_ex_wrap
    def _worker(self):
        flag = '/var/db/completed_clean_host_snapshot'
        if os.path.exists(flag):
            return
        _logger.debug('start _clean_host_snapshot')
        for host_snapshot in HostSnapshot.objects.filter(deleted=False):
            try:
                ext_info = json.loads(host_snapshot.ext_info)
                ext_info.pop('optimize_parameters', None)
                host_snapshot.ext_info = json.dumps(ext_info)
                host_snapshot.save(update_fields=['ext_info'])
            except Exception:
                pass
        with open(flag, 'w'):
            pass
        _logger.debug('end _clean_host_snapshot')

    def run(self):
        _logger.info('CleanHostSnapshot threading start')
        time.sleep(10)  # waiting for steady

        while True:
            try:
                self._worker()
                break
            except Exception as e:
                _logger.error('CleanHostSnapshot work error:{}'.format(e), exc_info=True)
            time.sleep(60)
        _logger.info('CleanHostSnapshot threading end')
