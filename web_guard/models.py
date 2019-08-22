import datetime
import json

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone

from apiv1.models import Host, RestoreTask
from box_dashboard import xdata


class StrategyGroup(models.Model):
    name = models.CharField(max_length=256, unique=True)
    create_time = models.DateTimeField(auto_now_add=True)


class WebGuardStrategy(models.Model):
    WEB_UNKNOWN = 0
    WEB_NORMAL = 1
    WEB_ANALYZING = 2
    WEB_EMERGENCY = 3
    WEB_MAINTAIN = 4
    WEB_FORCE_TRUSTED = 5

    STATUS_CHOICES = (
        (WEB_UNKNOWN, '--'),
        (WEB_NORMAL, '正常'),
        (WEB_ANALYZING, '分析中'),
        (WEB_EMERGENCY, '发现篡改风险'),
        (WEB_MAINTAIN, '已切换为应急页面'),
        (WEB_FORCE_TRUSTED, '确认篡改风险为可信'),
    )

    CHECK_TYPE_HOME_PAGE = xdata.CHECK_TYPE_HOME_PAGE
    CHECK_TYPE_URLS = xdata.CHECK_TYPE_URLS
    CHECK_TYPE_FILES = xdata.CHECK_TYPE_FILES
    CHECK_TYPE_CHOICES = (
        (CHECK_TYPE_HOME_PAGE, '首页检测'),
        (CHECK_TYPE_URLS, '网页检测'),
        (CHECK_TYPE_FILES, '文件检测'),
    )

    CHECK_SUB_TYPE_HOME_PAGE_CONTENT_ALTER = 11
    CHECK_SUB_TYPE_HOME_PAGE_SENSITIVE_WORD = 12
    CHECK_SUB_TYPE_HOME_PAGE_PICTURE = 13
    CHECK_SUB_TYPE_HOME_PAGE_RESOURCES = 14
    CHECK_SUB_TYPE_HOME_PAGE_LINKS = 15
    CHECK_SUB_TYPE_HOME_PAGE_FRAMEWORKS = 16
    CHECK_SUB_TYPE_URLS_CONTENT_ALTER = 101
    CHECK_SUB_TYPE_URLS_SENSITIVE_WORD = 102
    CHECK_SUB_TYPE_URLS_PICTURE = 103
    CHECK_SUB_TYPE_URLS_RESOURCES = 104
    CHECK_SUB_TYPE_URLS_LINKS = 105
    CHECK_SUB_TYPE_URLS_FRAMEWORKS = 106
    CHECK_SUB_TYPE_FILES_ALTER = 201

    CHECK_SUB_TYPE_CHOICES = (
        (CHECK_SUB_TYPE_HOME_PAGE_CONTENT_ALTER, '首页内容篡改检测'),
        (CHECK_SUB_TYPE_HOME_PAGE_SENSITIVE_WORD, '首页敏感词检测'),
        (CHECK_SUB_TYPE_HOME_PAGE_PICTURE, '首页图片篡改'),
        (CHECK_SUB_TYPE_HOME_PAGE_RESOURCES, '首页下载资源篡改'),
        (CHECK_SUB_TYPE_HOME_PAGE_LINKS, '首页链接篡改'),
        (CHECK_SUB_TYPE_HOME_PAGE_FRAMEWORKS, '首页框架篡改'),
        (CHECK_SUB_TYPE_URLS_CONTENT_ALTER, '网页内容篡改检测'),
        (CHECK_SUB_TYPE_URLS_SENSITIVE_WORD, '网页敏感词检测'),
        (CHECK_SUB_TYPE_URLS_PICTURE, '网页图片篡改'),
        (CHECK_SUB_TYPE_URLS_RESOURCES, '网页下载资源篡改'),
        (CHECK_SUB_TYPE_URLS_LINKS, '网页链接篡改'),
        (CHECK_SUB_TYPE_URLS_FRAMEWORKS, '网页框架篡改'),
        (CHECK_SUB_TYPE_FILES_ALTER, '文件篡改检测')
    )

    # 所属分组
    group = models.ForeignKey(StrategyGroup, related_name='strategies')
    # 所属用户
    user = models.ForeignKey(User, related_name='web_guard_strategies', on_delete=models.PROTECT)
    # 策略名称
    name = models.CharField(max_length=256, default='策略')
    # 启用、禁用
    enabled = models.BooleanField(default=True)
    # 删除策略
    deleted = models.BooleanField(default=False)
    # 上次执行时间
    last_run_date = models.DateTimeField(default=None, null=True)
    # 下次执行时间
    next_run_date = models.DateTimeField(default=None, null=True)
    # 当前状态
    present_status = models.IntegerField(choices=STATUS_CHOICES, default=WEB_UNKNOWN)
    # 类型
    check_type = models.IntegerField(choices=CHECK_TYPE_CHOICES)
    # 拓展配置
    ext_info = models.TextField(default='{}')
    # 正在处理中的任务
    running_task = models.TextField(default=None, null=True)
    # 处理任务的历史记录
    task_histories = models.TextField(default='{"tasks":[]}')
    # 强制可信
    force_credible = models.BooleanField(default=False)
    # 上一次404的时间
    last_404_date = models.DateTimeField(default=None, null=True)
    # 是否使用历史数据进行比较分析
    use_history = models.BooleanField(default=True)

    @staticmethod
    def display_status(status_val):
        for stat_tup in WebGuardStrategy.STATUS_CHOICES:
            if stat_tup[0] == status_val:
                return stat_tup[1]
        return '--'

    def set_present_status(self, status_val):
        self.present_status = status_val
        self.save(update_fields=['present_status'])

    def set_delete(self):
        self.deleted = True
        self.save(update_fields=['deleted'])

    def set_disable(self):
        self.enabled = False
        self.save(update_fields=['enabled'])

    def set_enable(self):
        self.enabled = True
        self.save(update_fields=['enabled'])

    def update_ext_info_name(self, ext_info, name):
        self.ext_info, self.name, = ext_info, name
        self.save(update_fields=['ext_info', 'name'])

    def set_use_history(self, use_history):
        self.use_history = use_history
        self.save(update_fields=['use_history'])


class EmergencyPlan(models.Model):
    EM_MANUAL = 0
    EM_AUTO = 1
    EM_MAINTAIN = 2
    STATUS_CHOICES = (
        (EM_MANUAL, '手动处理'),
        (EM_AUTO, '自动还原'),
        (EM_MAINTAIN, '自动切换为应急页面')
    )

    # 方案名称
    name = models.CharField(max_length=256, blank=True)
    # 关联的主机
    hosts = models.ManyToManyField(Host, related_name='emergency_plan')
    # 关联的策略
    strategy = models.ManyToManyField(WebGuardStrategy, related_name='emergency_plan')
    # 所属用户
    user = models.ForeignKey(User, related_name='emergency_plan')
    # 启用、禁用
    enabled = models.BooleanField(default=True)
    # 删除策略
    deleted = models.BooleanField(default=False)
    # 额外扩展信息
    exc_info = models.TextField(default='{}')
    # 计时
    timekeeper = models.TextField(default='{}')
    # 执行任务
    running_tasks = models.TextField(default='{}')

    # {
    #     "day_time_range": [
    #         [
    #             "00:00:00",
    #             "08:30:00"
    #         ],
    #         [
    #             "17:30:00",
    #             "23:59:59"
    #         ]
    #     ],
    #     "is_all_day_time": false,
    #     "events_choice": {
    #         "middle": [
    #             "1",  STATUS_CHOICES
    #             "30"
    #         ],
    #         "high": [
    #             "1",  STATUS_CHOICES
    #             "15"
    #         ],
    #         "low": [
    #             "0",  STATUS_CHOICES
    #             "15"
    #         ]
    #     },
    #     "week_day_range": [
    #         1,    周一
    #         2,
    #         3,
    #         4,
    #         5
    #     ]
    # }
    def is_valid_with_datetime(self, dt):
        config = json.loads(self.exc_info)
        if config['is_all_day_time']:
            return True
        # 返回 1-7
        weekday = dt.isoweekday()
        if weekday not in config['week_day_range']:
            return False

        t = dt.time()

        for r in config['day_time_range']:
            bt = datetime.datetime.strptime(r[0], r'%H:%M:%S').time()
            et = datetime.datetime.strptime(r[1], r'%H:%M:%S').time()
            if bt < t < et:
                return True

        return False

    def query_trigger_interval_and_type_with_level(self, level):
        config = json.loads(self.exc_info)
        return int(config["events_choice"][level][1]) * 60, int(config["events_choice"][level][0])


class WGRestoreTask(models.Model):
    task = models.OneToOneField(RestoreTask, related_name='web_guard_task_info', default=None, null=True)
    # 开始时间
    start_datetime = models.DateTimeField(auto_now_add=True)
    # 完成时间
    finish_datetime = models.DateTimeField(null=True, blank=True, default=None)
    # running task
    task_uuid = models.TextField(default=r'{}')
    # 唯一性标识
    uni_ident = models.TextField()
    # 关联任务
    plan = models.ForeignKey(EmergencyPlan, related_name='restore_tasks', default=None, null=True)
    # 额外扩展信息
    exc_info = models.TextField(default=r'{}')
    # restore_args
    restore_info = models.TextField(default=r'{}')


class AlarmMethod(models.Model):
    DEFAULT = {
        'high': {
            'email': {
                'is_use': True,
                'item_list': [],
                'frequency': 10
            },
            'phone': {
                'is_use': True,
                'item_list': [],
                'frequency': 10
            },
            'sms': {
                'is_use': True,
                'item_list': [],
                'frequency': 10
            }
        },
        'middle': {
            'email': {
                'is_use': True,
                'item_list': [],
                'frequency': 30
            },
            'phone': {
                'is_use': True,
                'item_list': [],
                'frequency': 30
            },
            'sms': {
                'is_use': True,
                'item_list': [],
                'frequency': 30
            }
        },
        'low': {
            'email': {
                'is_use': True,
                'item_list': [],
                'frequency': 60
            },
            'phone': {
                'is_use': True,
                'item_list': [],
                'frequency': 60
            },
            'sms': {
                'is_use': True,
                'item_list': [],
                'frequency': 60
            }
        }
    }

    user = models.OneToOneField(User, related_name='alarm_method')
    # 额外扩展信息
    exc_info = models.TextField(default=json.dumps(DEFAULT))


class AlarmEvent(models.Model):
    ALARM_EVENT_PENDING = 1
    ALARM_EVENT_MANUAL_PROCESSING = 2
    ALARM_EVENT_AUTO_PROCESSING = 3
    ALARM_EVENT_FIXED = 100
    ALARM_EVENT_CONFIRMED = 1000
    ALARM_EVENT_CHOICES = (
        (ALARM_EVENT_PENDING, '待处理'),
        (ALARM_EVENT_MANUAL_PROCESSING, '手动处理中'),
        (ALARM_EVENT_AUTO_PROCESSING, '自动处理中'),
        (ALARM_EVENT_FIXED, '已经修复'),
        (ALARM_EVENT_CONFIRMED, '确认无风险'),
    )

    last_update_time = models.DateTimeField()
    last_update_uuid = models.TextField()
    strategy = models.ForeignKey(WebGuardStrategy, related_name='alarm_events')
    strategy_sub_type = models.IntegerField(choices=WebGuardStrategy.CHECK_SUB_TYPE_CHOICES)
    detail = models.TextField(default='{}')
    current_status = models.IntegerField(choices=ALARM_EVENT_CHOICES)


class AlarmEventLog(models.Model):
    ALARM_EVENT_LOG_TYPE_HAPPENED = 1
    ALARM_EVENT_LOG_TYPE_FIXED = 2
    ALARM_EVENT_LOG_TYPE_CONFIRMED = 3
    ALARM_EVENT_LOG_TYPE_RESTORE_CONFIRMED = 4
    ALARM_EVENT_LOG_TYPE_CHOICES = (
        (ALARM_EVENT_LOG_TYPE_HAPPENED, '发现风险'),
        (ALARM_EVENT_LOG_TYPE_FIXED, '风险已移除'),
        (ALARM_EVENT_LOG_TYPE_CONFIRMED, '风险已确认'),
        (ALARM_EVENT_LOG_TYPE_RESTORE_CONFIRMED, '已进行系统还原'),
    )

    event_time = models.DateTimeField(auto_now_add=True)
    strategy = models.ForeignKey(WebGuardStrategy, related_name='alarm_event_logs')
    strategy_sub_type = models.IntegerField(choices=WebGuardStrategy.CHECK_SUB_TYPE_CHOICES)
    book_uuid = models.TextField()
    detail = models.TextField(default='{}')
    log_type = models.IntegerField(choices=ALARM_EVENT_LOG_TYPE_CHOICES)


class HostMaintainConfig(models.Model):
    host = models.OneToOneField(Host, related_name='maintain_config')
    # {
    #     "ports": [
    #         80,
    #         8000
    #     ]
    # }
    config = models.TextField(default='{}')
    # {
    #     "services": [
    #         "iis",
    #         "nginx"
    #     ]
    # }
    cache = models.TextField(default='{}')


class ModifyEntry(models.Model):
    # 内容修改入口
    entrance = models.URLField(max_length=256)
    # 关联的监控目标
    monitors = models.ManyToManyField(WebGuardStrategy, related_name='modify_entries')
    # 关联的内容管理员
    modify_admin = models.ManyToManyField(User, related_name='modify_entries')


class ModifyTask(models.Model):
    # 关联的 ModifyEntry
    modify_entry = models.ForeignKey(ModifyEntry, related_name='modify_task')
    # 开始时间
    start_datetime = models.DateTimeField(auto_now_add=True)
    # 完成时间
    finish_datetime = models.DateTimeField(null=True, blank=True, default=None)
    # 过期日期
    expire_datetime = models.DateTimeField(null=True, blank=True, default=None)
    # task_uuid
    task_uuid = models.CharField(max_length=32, unique=True)

    @property
    def name(self):
        return 'modify_task_{}'.format(','.join([str(st.id) for st in self.modify_entry.monitors.all()]))


class UserStatus(models.Model):
    """
    如果是登录状态，则需要判断session_time是否过期
    """
    STATUS_LOGOUT = 0
    STATUS_LOGIN = 1
    USER_STATUS_CHOICES = (
        (STATUS_LOGOUT, '登出'),
        (STATUS_LOGIN, '登录'),
    )

    user = models.OneToOneField(User, related_name='user_status')
    status = models.IntegerField(choices=USER_STATUS_CHOICES, default=STATUS_LOGOUT)
    session_time = models.DateTimeField(auto_now_add=True)

    @property
    def is_linked(self):
        if self.status == UserStatus.STATUS_LOGOUT:
            return False
        else:
            return self.session_time >= timezone.now()
