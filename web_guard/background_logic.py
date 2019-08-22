import datetime
import json
import threading
import time

import django.utils.timezone as timezone
from rest_framework import status

from box_dashboard import xlogging, xdata, xdatetime
from .models import WebGuardStrategy, EmergencyPlan, WGRestoreTask
from .notification import aio_notify
from .restore import WGRTask
from .views import StrategyExecute, EmergencyPlanExecute
from .web_check_logic import WebAnalyze, QueryAlarmEvent, AlarmEventMessage

START_DELAY_SECS = 60
PLAN_SCHEDULER_TIMER_INTERVAL_SECS = 0.5
ALARM_RESPONSE_SCHEDULER_TIMER_INTERVAL_SECS = 30
AUTO_RESPONSE_SCHEDULER_TIMER_INTERVAL_SECS = 60
_logger = xlogging.getLogger(__name__)


class PlanScheduler(threading.Thread):
    @staticmethod
    @xlogging.db_ex_wrap
    def run_scheduler():
        nowDateTime = timezone.now()
        strategies = WebGuardStrategy.objects.filter(deleted=False, enabled=True, running_task__isnull=True)
        for strategy in strategies:
            if strategy.next_run_date is None or nowDateTime > strategy.next_run_date:
                rsp = StrategyExecute().post(request=None, strategy_id=strategy.id)
                if rsp.status_code == status.HTTP_201_CREATED:
                    _logger.info('start StrategyExecute id:{} name:{} ok'.format(strategy.id, strategy.name))
                else:
                    _logger.warning('start StrategyExecute id:{} name:{} failed {}'
                                    .format(strategy.id, strategy.name, rsp.status_code))

    def run(self):
        # 确保各模块加载正常
        time.sleep(START_DELAY_SECS)

        _logger.info(r'PlanScheduler thread start')

        self.run_incomplete()

        while True:
            try:
                self.run_scheduler()
            except Exception as e:
                _logger.error(r'PlanScheduler Exception : {}'.format(e), exc_info=True)
            # 有效plans的第二次调度间隔
            time.sleep(PLAN_SCHEDULER_TIMER_INTERVAL_SECS)

    @staticmethod
    @xlogging.db_ex_wrap
    def run_incomplete():
        strategies = WebGuardStrategy.objects.filter(deleted=False, enabled=True, running_task__isnull=False)
        for strategy in strategies:
            try:
                web_analyze = WebAnalyze(strategy.id)
                running_task = json.loads(strategy.running_task)
                web_analyze.load_from_uuid(running_task)
                _logger.info(r'load web_analyze {} : {}'.format(strategy.name, web_analyze.name))
                web_analyze.start()
            except Exception as e:
                _logger.error(r'load strategy task {} failed {}'.format(strategy.id, e), exc_info=True)
                strategy.running_task = None
                strategy.save(update_fields=['running_task'])


class AlarmResponseScheduler(threading.Thread):
    @staticmethod
    @xlogging.db_ex_wrap
    def run_scheduler():
        strategies = WebGuardStrategy.objects.filter(deleted=False, enabled=True)
        for strategy in strategies:
            try:
                alarm_level, alarm_detail = QueryAlarmEvent.query_level_and_detail(strategy)
                if alarm_level in ('normal', 'other'):
                    continue
                message = AlarmEventMessage.generate(alarm_level, alarm_detail)
                aio_notify(strategy, message, alarm_level, datetime.datetime.now())
            except Exception as e:
                _logger.error(r'AlarmResponseScheduler run_scheduler : {}'.format(e), exc_info=True)

    def run(self):
        # 确保各模块加载正常
        time.sleep(START_DELAY_SECS)

        _logger.info(r'AlarmResponseScheduler thread start')

        while True:
            try:
                self.run_scheduler()
            except Exception as e:
                _logger.error(r'AlarmResponseScheduler Exception : {}'.format(e), exc_info=True)
            time.sleep(ALARM_RESPONSE_SCHEDULER_TIMER_INTERVAL_SECS)


class AutoResponseScheduler(threading.Thread):
    @staticmethod
    @xlogging.db_ex_wrap
    def run_scheduler():
        plans = EmergencyPlan.objects.filter(deleted=False, enabled=True)
        for plan in plans:
            alarms = AutoResponseScheduler.update_timekeeper(plan)
            _logger.debug('AutoResponseScheduler alarms:{}'.format(alarms))
            for alarm in alarms:
                AutoResponseScheduler.execute_plan(plan, alarm)

    @staticmethod
    def execute_plan(plan, alarm):
        rsp = EmergencyPlanExecute().post(request=None, plan_id=plan.id, api_request=alarm)
        if rsp.status_code == status.HTTP_201_CREATED:
            _logger.info('start EmergencyPlanExecute id:{} name:{} ok'.format(plan.id, plan.name))
        else:
            _logger.warning('start EmergencyPlanExecute id:{} name:{} failed {}'
                            .format(plan.id, plan.name, rsp.status_code))

    @staticmethod
    def update_timekeeper(plan):
        now_datetime = datetime.datetime.now()
        timekeeper = json.loads(plan.timekeeper)
        highest_alarming = 'normal'
        strategies = plan.strategy.filter(deleted=False, enabled=True)
        for strategy in strategies:
            alarm_level, _ = QueryAlarmEvent.query_level_and_detail(strategy)
            if 'normal' == alarm_level or 'other' == alarm_level:
                continue
            highest_alarming = xdata.max_alarm_level(highest_alarming, alarm_level)
            if alarm_level not in timekeeper.keys() or timekeeper[alarm_level] is None:
                timekeeper[alarm_level] = now_datetime.strftime(xdatetime.FORMAT_WITH_SECOND)

        for _key in timekeeper.keys():
            alarm_level = xdata.max_alarm_level(_key, highest_alarming)
            if alarm_level != highest_alarming:
                timekeeper[alarm_level] = None

        for _key in xdata.STRATEGY_EVENT_STATUS.keys():
            if 'normal' == _key or 'other' == _key:
                continue
            alarm_level = xdata.max_alarm_level(_key, highest_alarming)
            if alarm_level != _key and (alarm_level not in timekeeper.keys() or timekeeper[alarm_level] is None):
                timekeeper[alarm_level] = now_datetime.strftime(xdatetime.FORMAT_WITH_SECOND)

        plan.timekeeper = json.dumps(timekeeper)
        plan.save(update_fields=['timekeeper'])

        alarms = list()

        if plan.is_valid_with_datetime(now_datetime):
            for _key in timekeeper.keys():
                if timekeeper[_key] is None:
                    continue

                alarm_time = datetime.datetime.strptime(timekeeper[_key], xdatetime.FORMAT_WITH_SECOND)
                trigger_interval, trigger_type = plan.query_trigger_interval_and_type_with_level(_key)
                if (now_datetime - alarm_time).total_seconds() > trigger_interval:
                    alarms.append({"level": _key, "type": trigger_type})

        return sorted(alarms, key=lambda x: xdata.convert_alarm_level_2_int(x["level"]), reverse=True)

    def run(self):
        # 确保各模块加载正常
        time.sleep(START_DELAY_SECS)

        _logger.info(r'AutoResponseScheduler thread start')

        self.run_incomplete()

        while True:
            try:
                _logger.debug('AutoResponseScheduler run')
                self.run_scheduler()
                _logger.debug('AutoResponseScheduler end')
            except Exception as e:
                _logger.error(r'AutoResponseScheduler Exception : {}'.format(e), exc_info=True)
            time.sleep(AUTO_RESPONSE_SCHEDULER_TIMER_INTERVAL_SECS)

    @staticmethod
    @xlogging.db_ex_wrap
    def run_incomplete():
        wgr_tasks = WGRestoreTask.objects.filter(finish_datetime__isnull=True)
        for wgr_task in wgr_tasks:
            try:
                task = WGRTask()
                task_uuid = json.loads(wgr_task.task_uuid)
                task.load_from_uuid(task_uuid, wgr_task.id)
                _logger.info(r'load WGRTask {}'.format(wgr_task.id))
                task.start()
            except Exception as e:
                _logger.error(r'load WGRTask task {} failed {}'.format(wgr_task.id, e), exc_info=True)
                wgr_task.finish_datetime = timezone.now()
                wgr_task.save(update_fields=['finish_datetime'])
