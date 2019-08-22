import contextlib
import datetime
import json
import threading
import time

import django.utils.timezone as timezone
from rest_framework import status as http_status
from taskflow import engines
from taskflow import task
from taskflow.listeners import logging as logging_listener
from taskflow.patterns import linear_flow as lf
from taskflow.persistence import models

from apiv1.models import BackupTaskSchedule, HostSnapshot, Host
from apiv1.views import HostSnapshotLocalRestore
from box_dashboard import xlogging, xdatetime, xdata, boxService, task_backend
from web_guard.web_check_logic import OpAlarmEvent
from .models import WGRestoreTask, WebGuardStrategy, EmergencyPlan, AlarmEventLog

_logger = xlogging.getLogger(__name__)

_generate_locker = threading.Lock()


class WGRTask(threading.Thread):
    def __init__(self):
        super(WGRTask, self).__init__()
        self.name = r'WGRTask_'
        self._task_id = None
        self._plan_id = None
        self._engine = None
        self._book_uuid = None

    def load_from_uuid(self, task_uuid, task_id):
        backend = task_backend.get_backend()
        with contextlib.closing(backend.get_connection()) as conn:
            book = conn.get_logbook(task_uuid['book_id'])
            flow_detail = book.find(task_uuid['flow_id'])
        self._engine = engines.load_from_detail(flow_detail, backend=backend, engine='serial')
        self._book_uuid = book.uuid
        self.name += r'{} load exist uuid {} {}'.format(task_id, task_uuid['book_id'], task_uuid['flow_id'])
        assert self._task_id is None
        self._task_id = task_id

    @staticmethod
    @xlogging.LockDecorator(_generate_locker)
    def generate_task_object(uni_ident, is_auto, plan_id):
        if is_auto:
            other_object = WGRestoreTask.objects.filter(finish_datetime__isnull=True, uni_ident=uni_ident).first()
            if other_object is not None:
                xlogging.raise_and_logging_error(r'还原目标正在执行还原任务中',
                                                 r'other_object running : {}'.format(other_object.id),
                                                 http_status.HTTP_501_NOT_IMPLEMENTED)

        return WGRestoreTask.objects.create(uni_ident=uni_ident, plan_id=plan_id)

    # 当 restore_time 为 None 时候
    def generate_and_save(self, host_object, plan_id, is_auto=True, restore_time=None, restore_host_snapshot_id=None):
        # 自动调用时候需要分析
        if restore_time is None and restore_host_snapshot_id is None:
            info = AcquireRestoreInfo(plan_id).get_info(host_object)[0]
            if info['restore_time'] == -1:
                xlogging.raise_and_logging_error('没有可用的还原时间', r'not restore time'.format(self.name),
                                                 http_status.HTTP_501_NOT_IMPLEMENTED)
            if info['snapshot_id'] == -1:
                xlogging.raise_and_logging_error('客户端没有备份数据', r'host:{}:{},not snapshot find'.format(info['host_name'],
                                                                                                     info[
                                                                                                         'host_ident']),
                                                 http_status.HTTP_501_NOT_IMPLEMENTED)
            restore_time = info['restore_time']
            restore_host_snapshot_id = info['snapshot_id']

        task_object = self.generate_task_object(host_object.ident, is_auto, plan_id)
        self.name += r'{}'.format(task_object.id)
        self._task_id = task_object.id
        self._plan_id = plan_id

        try:
            backend = task_backend.get_backend()
            book = models.LogBook(
                r"{}_{}".format(self.name, datetime.datetime.now().strftime(xdatetime.FORMAT_WITH_SECOND_FOR_PATH)))
            with contextlib.closing(backend.get_connection()) as conn:
                conn.save_logbook(book)
        except Exception as e:
            _logger.error(r'get_backend failed {}'.format(e), exc_info=True)
            task_object.finish_datetime = timezone.now()
            task_object.save(update_fields=['finish_datetime'])
            raise e

        try:
            self._engine = engines.load_from_factory(create_flow, backend=backend, book=book, engine='serial',
                                                     factory_args=(self.name, self._task_id, self._plan_id, book.uuid,
                                                                   host_object.ident,
                                                                   restore_time,
                                                                   restore_host_snapshot_id
                                                                   )
                                                     )

            self._book_uuid = book.uuid

            task_object.task_uuid = json.dumps({'book_id': book.uuid, 'flow_id': self._engine.storage.flow_uuid})
            task_object.save(update_fields=['task_uuid'])
            return task_object
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
                                             http_status.HTTP_501_NOT_IMPLEMENTED)

    def run(self):
        # _logger.info(r'WebAnalyze {} running'.format(self.name))
        try:
            with logging_listener.DynamicLoggingListener(self._engine):
                self._engine.run()
        except Exception as e:
            _logger.error(r'WebAnalyze run engine {} failed {}'.format(self.name, e), exc_info=True)
        finally:
            with contextlib.closing(task_backend.get_backend().get_connection()) as conn:
                conn.destroy_logbook(self._book_uuid)
        # _logger.info(r'WebAnalyze {} stopped'.format(self.name))
        self._engine = None

    @staticmethod
    def has_error(task_context):
        return task_context['error'] is not None


def create_flow(name, task_id, plan_id, book_uuid, host_ident, restore_time_string, restore_host_snapshot_id):
    flow = lf.Flow(name).add(
        WGRTaskQueryStoppedCdpScheduleIds(name, host_ident),
        WGRTaskDisableAllCdpScheduleAndStopCdpStatus(name, host_ident),
        WGRTaskRestoreHost(name, host_ident, restore_time_string, restore_host_snapshot_id, task_id),
        WGRTaskWaiteHostPowerOff(name, host_ident),
        WGRTaskCleanAllAlarmEvent(name, plan_id),
        WGRTaskEnableAllCdpSchedule(name),
        WGRTaskFinishRestoreTask(name, task_id)
    )
    return flow


class WGRTaskQueryStoppedCdpScheduleIds(task.Task):
    default_provides = 'task_context'

    def __init__(self, name, host_ident, inject=None):
        super(WGRTaskQueryStoppedCdpScheduleIds, self).__init__(r'WGRTaskQueryStoppedCdpScheduleIds {}'.format(name),
                                                                inject=inject)
        self._host_ident = host_ident

    def execute(self, *args, **kwargs):
        try:
            # 记录需要暂停的CDP备份计划
            stopped_cdp_schedule_ids = [schedule.id for
                                        schedule in
                                        BackupTaskSchedule.objects.filter(deleted=False, enabled=True,
                                                                          host__ident=self._host_ident,
                                                                          cycle_type=BackupTaskSchedule.CYCLE_CDP).all()
                                        ]

            task_context = {
                'error': None,
                'stopped_cdp_schedule_ids': stopped_cdp_schedule_ids,
            }
        except Exception as e:
            _logger.error(r'WGRTaskQueryStoppedCdpScheduleIds failed : {}'.format(e), exc_info=True)
            task_context = {
                'error': (r'查询需要暂停的CDP备份计划失败', r'WGRTaskQueryStoppedCdpScheduleIds failed : {}'.format(e),),
            }

        return task_context


class WGRTaskDisableAllCdpScheduleAndStopCdpStatus(task.Task):
    default_provides = 'task_context'

    def __init__(self, name, host_ident, inject=None):
        super(WGRTaskDisableAllCdpScheduleAndStopCdpStatus, self).__init__(
            r'WGRTaskDisableAllCdpScheduleAndStopCdpStatus {} {}'.format(name, host_ident), inject=inject)
        self._host_ident = host_ident

    def execute(self, task_context, *args, **kwargs):
        if WGRTask.has_error(task_context):
            return task_context

        try:
            BackupTaskSchedule.objects. \
                filter(deleted=False, enabled=True, host__ident=self._host_ident,
                       cycle_type=BackupTaskSchedule.CYCLE_CDP). \
                update(enabled=False)

            boxService.box_service.stopCdpStatus(self._host_ident)
        except Exception as e:
            _logger.error(r'WGRTaskDisableAllCdpScheduleAndStopCdpStatus failed : {}'.format(e), exc_info=True)
            task_context['error'] = \
                (r'暂停CDP备份计划失败', r'WGRTaskDisableAllCdpScheduleAndStopCdpStatus failed : {}'.format(e),)

        return task_context


class WGRTaskEnableAllCdpSchedule(task.Task):
    default_provides = 'task_context'

    def __init__(self, name, inject=None):
        super(WGRTaskEnableAllCdpSchedule, self).__init__(r'WGRTaskEnableAllCdpSchedule {}'.format(name), inject=inject)

    def execute(self, task_context, *args, **kwargs):
        try:
            stopped_cdp_schedule_ids = task_context.get('stopped_cdp_schedule_ids', list())
            schedules = BackupTaskSchedule.objects.filter(id__in=stopped_cdp_schedule_ids).all()
            for schedule in schedules:
                schedule.enabled = True
                schedule.save(update_fields=['enabled', ])
        except Exception as e:
            _logger.error(r'WGRTaskEnableAllCdpSchedule failed : {}'.format(e), exc_info=True)
            # do nothing
            pass

        return task_context


class WGRTaskFinishRestoreTask(task.Task):
    default_provides = 'task_context'

    def __init__(self, name, task_id, inject=None):
        super(WGRTaskFinishRestoreTask, self).__init__(r'WGRTaskFinishRestoreTask {}'.format(name), inject=inject)
        self.task_obj = WGRestoreTask.objects.get(id=task_id)

    def execute(self, task_context, *args, **kwargs):
        try:
            self.task_obj.finish_datetime = timezone.now()
            self.task_obj.save(update_fields=['finish_datetime'])
        except Exception as e:
            _logger.error(r'WGRTaskFinishRestoreTask failed : {}'.format(e), exc_info=True)
            # do nothing
            pass

        return task_context


class WGRTaskCleanAllAlarmEvent(task.Task):
    default_provides = 'task_context'

    def __init__(self, name, plan_id, inject=None):
        super(WGRTaskCleanAllAlarmEvent, self).__init__(r'WGRTaskCleanAllAlarmEvent {} {}'.format(name, plan_id),
                                                        inject=inject)
        self._plan_id = plan_id

    def execute(self, task_context, *args, **kwargs):
        try:
            if WGRTask.has_error(task_context):
                return task_context

            strategy_ids = [strategy.id for strategy in
                            EmergencyPlan.objects.get(id=self._plan_id).strategy.all()]
            for strategy_id in strategy_ids:
                self.clean_alarm_event(strategy_id)
        except Exception as e:
            _logger.error(r'WGRTaskCleanAllAlarmEvent failed : {}'.format(e), exc_info=True)
            pass

        return task_context

    @staticmethod
    def clean_alarm_event(model_id):
        strategy_obj = WebGuardStrategy.objects.get(id=model_id)
        OpAlarmEvent.confirm_events(strategy_obj, AlarmEventLog.ALARM_EVENT_LOG_TYPE_RESTORE_CONFIRMED)


class WGRTaskRestoreHost(task.Task):
    def __init__(self, name, host_ident, restore_time_string, restore_host_snapshot_id, task_id, inject=None):
        super(WGRTaskRestoreHost, self).__init__(
            r'WGRTaskRestoreHost {} {} {}'.format(name, host_ident, restore_time_string), inject=inject)
        self._restore_time = datetime.datetime.strptime(restore_time_string, xdatetime.FORMAT_WITH_MICROSECOND)
        self._host_ident = host_ident
        self._restore_host_snapshot_id = restore_host_snapshot_id
        self.task_obj = WGRestoreTask.objects.get(id=task_id)

    def execute(self, task_context, *args, **kwargs):
        if WGRTask.has_error(task_context):
            return task_context

        api_request = {'type': xdata.SNAPSHOT_TYPE_CDP,
                       'host_ident': self._host_ident,
                       'restore_time': self._restore_time}

        restore_info = json.loads(self.task_obj.restore_info)
        if restore_info:
            task_context['error'] = ('启动还原失败，重复的任务', 'restore task has exists :{} ,start fail!'.format(restore_info),)
            return task_context
        else:
            info = {'type': xdata.SNAPSHOT_TYPE_CDP,
                    'host_ident': self._host_ident,
                    'restore_time': self._restore_time.strftime(xdatetime.FORMAT_WITH_MICROSECOND)
                    }
            info_str = json.dumps(info)
            self.task_obj.restore_info = info_str
            self.task_obj.save(update_fields=['restore_info'])

        rsp = HostSnapshotLocalRestore().post(None, self._restore_host_snapshot_id, api_request, True)
        if not http_status.is_success(rsp.status_code):
            task_context['error'] = \
                (rsp.data if rsp.data else '执行还原任务失败', r'WGRTaskRestoreHost failed {} {}'.format(
                    self._restore_host_snapshot_id, api_request),)

        return task_context


class WGRTaskWaiteHostPowerOff(task.Task):
    def __init__(self, name, host_ident, inject=None):
        super(WGRTaskWaiteHostPowerOff, self).__init__(
            r'WGRTaskWaiteHostPowerOff {} {}'.format(name, host_ident), inject=inject)
        self._host_ident = host_ident

    def execute(self, task_context, *args, **kwargs):
        if WGRTask.has_error(task_context):
            return task_context

        max_waite_time = 10 * 60
        waite_time = 0
        time_unit = 1
        _logger.debug('WGRTaskWaiteHostPowerOff check host:{} status start '.format(self._host_ident))
        while waite_time <= max_waite_time:
            if not self.host_is_linked(self._host_ident):
                _logger.debug(
                    'WGRTaskWaiteHostPowerOff host:{} offline after:{}sec'.format(self._host_ident, waite_time))
                time.sleep(10)
                break
            else:
                time.sleep(time_unit)
                waite_time += time_unit
        else:
            _logger.warning('WGRTaskWaiteHostPowerOff host:{} is always online after 10min'.format(self._host_ident))
        _logger.debug('WGRTaskWaiteHostPowerOff check host:{} status end'.format(self._host_ident))
        return task_context

    @staticmethod
    def host_is_linked(host_ident):
        return Host.objects.get(ident=host_ident).is_linked


class AcquireRestoreInfo(object):
    def __init__(self, plan_id):
        self._plan = EmergencyPlan.objects.get(id=plan_id)
        self._strategies = self._plan.strategy.filter(deleted=False, enabled=True).all()
        self._hosts = self._plan.hosts.all()

    def get_info(self, host=None):
        credible_time = self.query_the_earliest_credible_time()
        info = list()
        # [
        #   {
        #       'host_name':host_name,
        #       'host_ident':host_ident,
        #       'snapshot_id':host_snapshot_id,    当没有找到适用的快照点时为 -1
        #       'restore_time':'time_str'
        #   }
        # ]
        if host:
            host_snapshot_id, restore_time = self.query_host_snapshot(host, credible_time)
            info.append({
                'host_name': host.name, 'host_ident': host.ident,
                'snapshot_id': host_snapshot_id if host_snapshot_id else -1,
                'restore_time': restore_time.strftime(xdatetime.FORMAT_WITH_MICROSECOND) if restore_time else -1
            })
        else:
            for host in self._hosts:
                host_snapshot_id, restore_time = self.query_host_snapshot(host, credible_time)
                info.append({
                    'host_name': host.name, 'host_ident': host.ident,
                    'snapshot_id': host_snapshot_id if host_snapshot_id else -1,
                    'restore_time': restore_time.strftime(xdatetime.FORMAT_WITH_MICROSECOND) if restore_time else -1
                })
        _logger.info(
            'AcquireRestoreInfo get restore args, credible_time:{} | restore_info:{}'.format(credible_time, info))
        return info

    def query_the_earliest_credible_time(self):
        result = None

        for strategy_object in self._strategies:
            task_histories = json.loads(strategy_object.task_histories)
            credible_tasks = list(filter(lambda x: x['credible'] == 'yes', task_histories['tasks']))
            incredible_tasks = list(filter(lambda x: x['credible'] == 'no', task_histories['tasks']))
            incredible = datetime.datetime.strptime(incredible_tasks[-1]['date_time'],
                                                    xdatetime.FORMAT_WITH_SECOND_FOR_PATH) \
                if len(incredible_tasks) > 0 else None
            if len(credible_tasks) > 0:
                credible = datetime.datetime.strptime(credible_tasks[-1]['date_time'],
                                                      xdatetime.FORMAT_WITH_SECOND_FOR_PATH)

                if result is None or (credible < result < incredible if incredible else credible < result):
                    result = credible

        _logger.info(r'query_the_earliest_credible_time {} | {}'.format(self._plan.name, self._plan.id))
        return result

    @staticmethod
    def query_host_snapshot(host_object, credible_time):
        if credible_time is None:
            return None, None

        host_snapshot_object = HostSnapshot.objects.filter(host=host_object, deleted=False,
                                                           start_datetime__isnull=False, deleting=False,
                                                           is_cdp=True, successful=True,
                                                           cdp_info__first_datetime__lte=credible_time) \
            .order_by('-start_datetime').first()

        if host_snapshot_object is None:
            return None, credible_time

        finish_datetime = host_snapshot_object.cdp_task.finish_datetime
        if finish_datetime is None or finish_datetime >= credible_time:
            return host_snapshot_object.id, credible_time
        else:
            return host_snapshot_object.id, finish_datetime
