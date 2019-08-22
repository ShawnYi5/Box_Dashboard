# coding=utf-8
import datetime
import json
from datetime import timedelta
from itertools import groupby

from django.utils import timezone as timezone

from apiv1.models import BackupTask, BackupTaskSchedule, ClusterBackupSchedule, ClusterBackupTask
from box_dashboard import functions


class CommonUtils(object):

    @staticmethod
    # 热备 2018-10-10 17：00：00 热备172.16.1.1 正在xxxx button
    def get_current_label(title, time, name, status_title, status, button, time_left=''):
        label = '<div class="tasktree_sub_width">' \
                '<li style="float:left;overflow:hidden;">{title}</li>' \
                '<li style="float:left;padding-left:10px;">{time}</li>' \
                '<li style="float:left;width:30%;padding-left:10px;overflow:hidden;word-break:keep-all;white-space:nowrap' \
                ';" title="{name}">{name}</li>' \
                '<li style="float:left;padding-left:5px;overflow:hidden;word-break:keep-all;display:none;white-space:nowrap;">{time_left}</li>' \
                '<li style="float:left;padding-left:10px;overflow:hidden;word-break:keep-all;white-space:nowrap;" title="{status_title}">{status}</li>' \
                '<li style="float:left;padding-left:5px;overflow:hidden;word-break:keep-all;white-space:nowrap;" class="timeleft"></li>' \
                '<div style="float:right">{button}</div>' \
                '<div style="clear:both"></div>' \
                '</div>'

        return label.format(title=title, time=time, name=name, status=status, status_title=status_title, button=button,
                            time_left=time_left)

    @staticmethod
    def get_op_button(ul_content):
        content = '{}{}'.format('[更多]', ul_content)
        span = functions.tag('span', content,
                             attrs={'class': 'up_down_icon'})
        return span

    @staticmethod
    def get_ul_content(content):
        return functions.tag('ul', content,
                             attrs={'class': 'my-ui-menu ui-menu ui-widget ui-widget-content '
                                             'ui-corner-all'})

    @staticmethod
    def get_li_content(content, title, func_str):
        return functions.tag('li', content, attrs={'title': title, 'onclick': func_str})

    @staticmethod
    def get_li_detail(type_str, task_id):
        li_detail_func_str = "gotoTaskDetail('{}','{}')".format(type_str, str(task_id))
        return CommonUtils.get_li_content('详细', '详细信息', li_detail_func_str)


class TaskSummary(object):

    def __init__(self, total=0, successful=0, failed=0, waiting=0, processing=0):
        self.total = total
        self.successful = successful
        self.failed = failed
        self.waiting = waiting
        self.processing = processing

    def add(self, task_summary):
        self.total += task_summary.total
        self.successful += task_summary.successful
        self.failed += task_summary.failed
        self.waiting += task_summary.waiting
        self.processing += task_summary.processing

    def to_html(self, label):
        return '<div style="float:right;" class="tasktree_total_width"><span>{label}</span><div style="float:right;">' \
               '计划定时备份次数：{total}/运行中：{processing}/成功：{successful}/等待：{waiting}/失败：{failed}</div></div>' \
            .format(label=label, total=self.total, successful=self.successful, failed=self.failed,
                    waiting=self.waiting, processing=self.processing)

    def __str__(self):
        return 'total:{}|successful:{}|failed:{}|waiting:{}|processing:{}'.format(self.total, self.successful,
                                                                                  self.failed, self.waiting,
                                                                                  self.processing)


class TaskHandle(object):

    def __init__(self):
        self.start_datetime = None
        self._is_todo = None
        self._finish_datetime = None
        self._successful = None
        self._task = None

    @classmethod
    def ins(cls, task):
        ins = cls()
        ins._task = task
        ins.start_datetime = task.start_datetime
        ins._finish_datetime = task.finish_datetime
        ins._is_todo = False
        ins._successful = task.successful
        return ins

    @classmethod
    def ins1(cls, start_datetime):
        ins = cls()
        ins.start_datetime = start_datetime
        ins._finish_datetime = False
        ins._is_todo = True
        ins._successful = False
        return ins

    def to_node(self):
        icon = 'info'
        if self._is_todo:
            info = '等待执行'
        else:
            if self._finish_datetime:
                if self._successful:
                    info = '成功'
                else:
                    info = functions.tag('span', '失败', attrs={'style': 'color:red'})
                    icon = 'error'
            else:
                info = '执行中'
        button = self._generate_button()
        label_str = '<div class="tasktree_sub_width">' \
                    '<div style="float:left;overflow:hidden;">{}</div>' \
                    '<div style="float:left;padding-left:30px;">{}</div>' \
                    '<div style="float:left;padding-left:30px;">{}</div>' \
                    '<div style="float:right">{}</div>' \
                    '<div style="clear:both"></div>' \
                    '</div>'
        label = label_str.format('备份', self.start_datetime.strftime('%Y-%m-%d %H:%M:%S'), info, button)
        return {'label': label, 'date_time': self.start_datetime, 'icon': icon}

    def _generate_button(self):
        if self._task:
            if isinstance(self._task, BackupTask):
                type_str = 'backup'
            else:
                type_str = 'cluster'
            return CommonUtils.get_op_button(
                CommonUtils.get_ul_content(CommonUtils.get_li_detail(type_str, self._task.id)))
        else:
            return ''


class TodayTasks(object):

    def __init__(self):
        self.tasks = list()
        self.task_summary = TaskSummary()
        self._now = timezone.now()
        self._begin_time = datetime.datetime.combine(self._now.date(), datetime.time(0, 0, 0))
        self._end_time = datetime.datetime.combine(self._now.date(), datetime.time(23, 59, 59))

    def calc(self):
        self._calc_done_and_doing()
        self._calc_to_do()

    def _calc_done_and_doing(self):
        for task in self._query_tasks():
            self.tasks.append(TaskHandle.ins(task))
            self.task_summary.total += 1
            if not task.finish_datetime:
                self.task_summary.processing += 1
                break
            else:
                if task.successful:
                    self.task_summary.successful += 1
                else:
                    self.task_summary.failed += 1

    def _calc_to_do(self):
        to_do = list()
        for schedule in self._query_schedules():
            start_time = schedule.next_run_date
            while start_time and (start_time < self._end_time):
                self.task_summary.total += 1
                self.task_summary.waiting += 1
                to_do.append(TaskHandle.ins1(start_time))
                start_time = self._get_next_start_time(start_time, schedule)
        to_do.sort(key=lambda x: x.start_datetime)
        self.tasks.extend(to_do)

    def exists_task(self):
        return self.task_summary.total > 0

    def _get_next_start_time(self, base, schedule):
        if schedule.cycle_type == BackupTaskSchedule.CYCLE_PERDAY:  # 按间隔时间
            ext_config = json.loads(schedule.ext_config)
            if ext_config['IntervalUnit'] in ['min', 'hour']:
                return base + timedelta(seconds=ext_config['backupDayInterval'])
            else:
                return None
        else:
            return None

    def _query_schedules(self):
        raise NotImplementedError

    def _query_tasks(self):
        raise NotImplementedError


class BackupTodayTasks(TodayTasks):
    """
    仅仅统计了普通的备份任务，不包含CDP
    """

    def __init__(self, host):
        super(BackupTodayTasks, self).__init__()
        self._host = host

    def _query_tasks(self):
        return BackupTask.objects.filter(start_datetime__gte=self._begin_time,
                                         schedule__host=self._host,
                                         host_snapshot__isnull=False).exclude(
            schedule__cycle_type=BackupTaskSchedule.CYCLE_CDP).order_by('start_datetime')

    def _query_schedules(self):
        return BackupTaskSchedule.objects.filter(enabled=True,
                                                 next_run_date__gt=self._begin_time,
                                                 next_run_date__lt=self._end_time,
                                                 deleted=False,
                                                 host=self._host).exclude(
            cycle_type=BackupTaskSchedule.CYCLE_CDP)


class ClusterTodayTasks(TodayTasks):
    """
    统计集群计划的今日任务，集群计划具有相同的主机
    """
    id_prefix = 'cluster_'

    def __init__(self, schedules):
        super(ClusterTodayTasks, self).__init__()
        self._schedules = schedules  # 计划都具有相同的主机

    def to_node(self):
        host = self._schedules[0].hosts.first()
        key = (-1 if host.is_linked else 0,
               host.name
               )
        return {'label': self.gen_label(), 'icon': 'pc', 'id': self.gen_id(), 'inode': self.exists_task(), 'key': key}

    def gen_id(self):
        return '{}{}'.format(self.id_prefix, ','.join([str(sc.id) for sc in self._schedules]))

    def gen_label(self):
        name = '集群备份 {}'.format('|'.join([host.name for host in self._schedules[0].hosts.all()]))
        info = functions.tag('span', name[:30], attrs={'title': name})
        return self.task_summary.to_html(info)

    @classmethod
    def get_ins(cls, id_str):
        ids = id_str.strip(cls.id_prefix)
        schedules = [ClusterBackupSchedule.objects.get(id=_id) for _id in ids.split(',')]
        return cls(schedules)

    def _query_schedules(self):
        return self._schedules

    def _query_tasks(self):
        return ClusterBackupTask.objects.filter(start_datetime__gte=self._begin_time,
                                                schedule__in=self._schedules).exclude(
            schedule__cycle_type=BackupTaskSchedule.CYCLE_CDP).order_by('start_datetime')

    @staticmethod
    def get_schedules(user_id):
        """
        :param user_id:
        :return: [[schedule1, schedule2], [schedule3, schedule4]]
        """
        schedules = ClusterBackupSchedule.objects.filter(deleted=False,
                                                         enabled=True,
                                                         hosts__user_id=user_id).distinct()

        def _key_f(schedule):
            return ','.join([str(host.id) for host in schedule.hosts.all()])

        return [list(t[1]) for t in groupby(schedules, key=_key_f)]
