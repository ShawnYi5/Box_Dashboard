# coding=utf-8
import uuid

from django.contrib.auth.models import User
from django.db import models


class TmpDictionary(models.Model):
    '临时数据字典'
    TMP_DICT_TYPE_UNKNOWN = 0
    TMP_DICT_TYPE_PWD = 1
    TMP_DICT_TYPE_REDIRECT = 2
    TMP_DICT_TYPE_CHOICES = (
        (TMP_DICT_TYPE_PWD, '更改密码链接'),
        (TMP_DICT_TYPE_REDIRECT, '免密码登录并重定向到指定页面的链接'),
    )
    dictType = models.PositiveSmallIntegerField(choices=TMP_DICT_TYPE_CHOICES, default=TMP_DICT_TYPE_PWD)
    dictKey = models.CharField(max_length=10)
    dictValue = models.CharField(max_length=256)
    expireTime = models.DateTimeField()


class DataDictionary(models.Model):
    '数据字典'
    DICT_TYPE_UNKNOWN = 0
    DICT_TYPE_SMTP = 1
    DICT_TYPE_EXPIRY = 2
    DICT_TYPE_PWD_POLICY = 3
    DICT_TYPE_CHOICE_SEND_EMAIL_RANGE = 4
    DICT_TYPE_UPDATE_URL = 5
    DICT_TYPE_SAMBA = 6
    DICT_TYPE_PWD_EXPIRY = 7
    DICT_TYPE_PWD_CYCLE = 8
    DICT_TYPE_LOGIN_FAILED = 9
    DICT_TYPE_USER_LOGIN_FAILED_COUNT = 10
    DICT_TYPE_LOGIN_LOCK_MIN = 11
    DICT_TYPE_TAKEOVER_SEGMENT = 12
    DICT_TYPE_PARTIAL_DISK_SNAPSHOT_EXP = 13
    DICT_TYPE_TASK_QUEUE_NUM = 14
    DICT_TYPE_DATABASE_CAN_USE_SIZE = 15
    DICT_TYPE_BACKUP_AIO_DATABASE = 16
    DICT_TYPE_BACKUP_PARAMS = 17
    DICT_TYPE_AIO_NETWORK = 18
    DICT_TYPE_CHOICES = (
        (DICT_TYPE_SMTP, '邮件服务器'),
        (DICT_TYPE_EXPIRY, 'session过期时间'),
        (DICT_TYPE_PWD_POLICY, '密码策略'),
        (DICT_TYPE_CHOICE_SEND_EMAIL_RANGE, '选择邮件发送范围'),
        (DICT_TYPE_UPDATE_URL, '一体机更新外网地址'),
        (DICT_TYPE_SAMBA, 'samba用户名和密码'),
        (DICT_TYPE_PWD_EXPIRY, '密码过期时间'),
        (DICT_TYPE_PWD_CYCLE, '密码周期'),
        (DICT_TYPE_LOGIN_FAILED, '登录失败次数和解锁时间'),
        (DICT_TYPE_USER_LOGIN_FAILED_COUNT, '限制登录失败次数默认10次'),
        (DICT_TYPE_LOGIN_LOCK_MIN, '限制登录锁定分钟数默认30分钟'),
        (DICT_TYPE_TAKEOVER_SEGMENT, '接管使用的专用网段'),
        (DICT_TYPE_PARTIAL_DISK_SNAPSHOT_EXP, '不完整点的过期天数'),
        (DICT_TYPE_TASK_QUEUE_NUM, '任务队列数量'),
        (DICT_TYPE_DATABASE_CAN_USE_SIZE, '日志数据存储空间'),
        (DICT_TYPE_BACKUP_AIO_DATABASE, '备份一体机的数据库'),
        (DICT_TYPE_BACKUP_PARAMS, '备份参数'),
        (DICT_TYPE_AIO_NETWORK, '网络参数'),
    )
    dictType = models.PositiveSmallIntegerField(choices=DICT_TYPE_CHOICES, default=DICT_TYPE_UNKNOWN)
    dictKey = models.CharField(max_length=10)
    dictValue = models.CharField(max_length=256)


class UserProfile(models.Model):
    NORMAL_USER = 'normal-admin'
    CONTENT_ADMIN = 'content-admin'
    AUD_ADMIN = 'aud-admin'
    AUDIT_ADMIN = 'audit-admin'
    SEC_ADMIN = 'sec-admin'
    SUPER_ADMIN = 'super-admin'

    USER_TYPE = (
        (NORMAL_USER, '系统管理员'),
        (CONTENT_ADMIN, '内容管理员'),
        (AUD_ADMIN, '安全审计管理员'),
        (SEC_ADMIN, '安全保密管理员'),
        (AUDIT_ADMIN, '验证/恢复审批管理员'),
        (SUPER_ADMIN, '超级管理员'),
    )

    # 用户扩展
    user = models.OneToOneField(User)
    # 功能授权
    modules = models.BigIntegerField(null=True)
    desc = models.TextField(blank=True)
    winpeset = models.TextField(blank=True)
    deleted = models.BooleanField(default=False)
    # 安全等级设定
    safeset = models.TextField(blank=True, default='3,10')
    # 表示该用户类型
    user_type = models.CharField(max_length=256, choices=USER_TYPE, default=NORMAL_USER)
    # 用户指纹
    user_fingerprint = models.UUIDField(unique=True, editable=False, default=uuid.uuid4)
    # 用户微信账号
    wei_xin = models.CharField(max_length=100, default='')


class OperationLog(models.Model):
    '操作日志'
    TYPE_UNKNOWN = 0
    TYPE_SMTP = 1
    TYPE_ADAPTER = 2
    TYPE_BACKUP = 3
    TYPE_SERVER = 4
    TYPE_RESTORE = 5
    TYPE_MIGRATE = 6
    TYPE_QUOTA = 7
    TYPE_USER = 8
    TYPE_SYSTEM_SET = 9
    TYPE_BOOT_ISO = 10
    TYPE_OP_LOG = 11
    TYPE_SYS_LOG = 12
    TYPE_BROWSE_LOG = 13
    TYPE_BACKUP_POLICY = 14
    TYPE_UPDATE_AIO_BASE = 20
    TYPE_UPDATE_AIO_DRIVE = 21
    TYPE_UPDATE_AIO_ISO = 22
    TYPE_UPDATE_AIO_DE_DUPLICATION = 23
    TYPE_HOT_BACKUP = 24
    TYPE_WEBGUARD = 25
    TYPE_PXE = 26
    TYPE_TAKEOVER = 27
    CLUSTER_BACKUP = 28
    REMOTE_BACKUP = 29
    VMWARE_BACKUP = 30
    WEIXIN = 31
    BACKUP_EXPORT = 32
    TYPE_AUTO_VERIFY_TASK = 33
    TYPE_TEMPLATE = 34
    DBBACKUP = 35

    EVENT_TYPE_CHOICES = (
        (TYPE_UNKNOWN, 'unknown'),
        (TYPE_SMTP, '邮件服务器'),
        (TYPE_ADAPTER, '网络设置'),
        (TYPE_BACKUP, '备份任务管理'),
        (TYPE_SERVER, '客户端管理'),
        (TYPE_RESTORE, '恢复'),
        (TYPE_MIGRATE, '迁移'),
        (TYPE_QUOTA, '存储管理'),
        (TYPE_USER, '用户管理'),
        (TYPE_SYSTEM_SET, '系统设置'),
        (TYPE_BOOT_ISO, '启动介质'),
        (TYPE_OP_LOG, '操作日志'),
        (TYPE_SYS_LOG, '客户端日志'),
        (TYPE_BROWSE_LOG, '浏览备份'),
        (TYPE_UPDATE_AIO_BASE, '一体机更新'),
        (TYPE_UPDATE_AIO_DRIVE, '服务器驱动更新'),
        (TYPE_UPDATE_AIO_ISO, '启动介质数据源更新'),
        (TYPE_UPDATE_AIO_DE_DUPLICATION, '去重数据更新'),
        (TYPE_BACKUP_POLICY, '备份任务策略'),
        (TYPE_HOT_BACKUP, '热备'),
        (TYPE_WEBGUARD, '网站防护'),
        (TYPE_PXE, 'PXE设置'),
        (TYPE_TAKEOVER, '接管'),
        (CLUSTER_BACKUP, '集群备份计划管理'),
        (REMOTE_BACKUP, '远程容灾计划管理'),
        (VMWARE_BACKUP, '免代理'),
        (WEIXIN, '微信设置'),
        (BACKUP_EXPORT, '备份数据导出'),
        (TYPE_AUTO_VERIFY_TASK, '自动验证'),
        (TYPE_TEMPLATE, '模板管理'),
        (DBBACKUP, '数据库备份'),
    )
    user = models.ForeignKey(User, related_name='userid', on_delete=models.PROTECT)
    event = models.PositiveSmallIntegerField(choices=EVENT_TYPE_CHOICES, default=TYPE_UNKNOWN)
    datetime = models.DateTimeField(auto_now_add=True)
    desc = models.TextField(blank=False)
    operator = models.CharField(null=True, max_length=64)


class Email(models.Model):
    STORAGE_NODE_NOT_ENOUGH_SPACE = 1
    STORAGE_NODE_NOT_ONLINE = 2
    STORAGE_NODE_NOT_VALID = 3
    CDP_STOP = 4
    CDP_FAILED = 5
    CDP_PAUSE = 6
    SYS_TIME_WRONG = 7
    BACKUP_FAILED = 8
    BACKUP_SUCCESS = 9
    MIGRATE_FAILED = 10
    MIGRATE_SUCCESS = 11
    RESTORE_FAILED = 12
    RESTORE_SUCCESS = 13
    LOG_ALERT = 14
    DRIVER_ABNORMAL_ALERT = 15

    EMAIL_TYPE_CHOICES = (
        (1, '用户配额不足'),
        (2, '存储结点离线'),
        (3, '存储结点不可用'),
        (4, 'CDP保护停止'),
        (5, 'CDP保护失败'),
        (6, 'CDP保护暂停'),
        (7, '系统时间错误'),
        (8, '备份失败'),
        (9, '备份成功'),
        (10, '迁移失败'),
        (11, '迁移成功'),
        (12, '还原失败'),
        (13, '还原成功'),
        (14, '日志空间告警'),
        (15, '代理程序初始化失败'),
    )

    type = models.PositiveSmallIntegerField(choices=EMAIL_TYPE_CHOICES, default=-1)
    datetime = models.DateTimeField(auto_now_add=True)
    content = models.TextField()
    times = models.IntegerField()
    is_successful = models.BooleanField(default=False)


class DeviceRunState(models.Model):
    TYPE_DISK_IO = 1
    TYPE_NETWORK_IO = 2
    TYPE_OTHER = 3

    ACQUIRE_DATA_TYPE = (
        (TYPE_DISK_IO, '磁盘IO变化'),
        (TYPE_NETWORK_IO, '网络IO变化'),
        (TYPE_OTHER, '其他描述'),
    )

    datetime = models.DateTimeField(auto_now_add=True)
    type = models.PositiveSmallIntegerField(choices=ACQUIRE_DATA_TYPE, default=TYPE_OTHER)
    writevalue = models.BigIntegerField(default=0)
    readvalue = models.BigIntegerField(default=0)
    last_in_total = models.BigIntegerField(default=0, blank=True)
    last_out_total = models.BigIntegerField(default=0, blank=True)


class BackupDataStatt(models.Model):
    date_time = models.DateTimeField(auto_now_add=True)
    node_id = models.IntegerField(default=-1)
    user_id = models.IntegerField(default=-1)
    original_data_mb = models.IntegerField(default=0)
    backup_data_mb = models.IntegerField(default=0)


# 存储单元：已用，可用，RAW_DATA
class StorageNodeSpace(models.Model):
    node_id = models.IntegerField()
    total_bytes = models.BigIntegerField()
    free_bytes = models.BigIntegerField()
    raw_data_bytes = models.BigIntegerField()
    time_date = models.DateTimeField(auto_now_add=True)


# 用户配额：已用，可用，RAW_DATA
class UserQuotaSpace(models.Model):
    quota_id = models.IntegerField()
    free_bytes = models.BigIntegerField(default=0)
    used_bytes = models.BigIntegerField(default=0)
    raw_data_bytes = models.BigIntegerField(default=0)
    date_time = models.DateTimeField(auto_now_add=True)


class taskpolicy(models.Model):
    '任务策略'
    user = models.ForeignKey(User, related_name='taskuserid', on_delete=models.PROTECT)
    name = models.CharField(max_length=256)
    # 保留期限
    retentionperiod = models.IntegerField()
    # 保留备份点个数
    keepingpoint = models.IntegerField()
    # 自动清理，当空间剩余nGB时
    cleandata = models.IntegerField()
    # 是否使用带宽限制
    usemaxbandwidth = models.IntegerField()
    # 带宽
    maxbandwidth = models.IntegerField()
    # 备份周期类型
    cycletype = models.IntegerField()
    # 是否加密 -- 未使用
    isencipher = models.IntegerField(default=0)
    # 备份方式 xdata.BACKUP_TASK_SCHEDULE_EXECUTE_TYPE_AUTO
    backupmode = models.IntegerField(default=2)
    # 是否启用去重
    isdup = models.BooleanField(default=True)
    # 拓展信息
    ext_info = models.TextField(default='{}')


class cdpcycle(models.Model):
    taskpolicy = models.ForeignKey(taskpolicy, related_name='cdpcycletaskpolicyid', on_delete=models.PROTECT)
    cdpperiod = models.IntegerField()
    cdptype = models.IntegerField()
    starttime = models.DateTimeField(blank=True, null=True, default=None)


class onlyonecycle(models.Model):
    taskpolicy = models.ForeignKey(taskpolicy, related_name='onlyonecycletaskpolicyid', on_delete=models.PROTECT)
    starttime = models.DateTimeField()


class everydaycycle(models.Model):  # 修改为'按间隔时间'类型
    taskpolicy = models.ForeignKey(taskpolicy, related_name='everydaycycletaskpolicyid', on_delete=models.PROTECT)
    starttime = models.DateTimeField()
    timeinterval = models.IntegerField()
    unit = models.CharField(max_length=256, default='day')


class everyweekcycle(models.Model):
    taskpolicy = models.ForeignKey(taskpolicy, related_name='everyweekcycletaskpolicyid', on_delete=models.PROTECT)
    starttime = models.DateTimeField()
    perweek = models.IntegerField()


class everymonthcycle(models.Model):
    taskpolicy = models.ForeignKey(taskpolicy, related_name='everymonthcycletaskpolicyid', on_delete=models.PROTECT)
    starttime = models.DateTimeField()
    monthly = models.IntegerField()


class DriverBlackList(models.Model):
    device_id = models.CharField(max_length=256)
    driver_id = models.CharField(max_length=256)
    sys_type = models.CharField(max_length=256)


class ForceInstallDriver(models.Model):
    """
    此表记录了，用户从操作系统（sys_type）还原到硬件(device_id) 时候，勾选驱动(driver_id) 为强制安装
    """
    # hard_or_comp_id 例如 "PCI\\VEN_8086&DEV_100E"
    device_id = models.CharField(max_length=256)
    # 驱动的zip path "08afdeb3731917cce16f2b363648203496a75bc8.zip" 标识一个驱动
    driver_id = models.CharField(max_length=256)
    # 源的操作系统类型
    sys_type = models.CharField(max_length=256)
    # 用户
    user = models.ForeignKey(User, related_name='force_install_driver')


class sshkvm(models.Model):
    'KVM配置'
    enablekvm = models.BooleanField(default=False)
    ssh_ip = models.CharField(max_length=16)
    ssh_port = models.IntegerField()
    ssh_key = models.TextField()
    ssh_path = models.CharField(max_length=256)
    aio_ip = models.CharField(max_length=16)
    ssh_os_type = models.CharField(max_length=32)


class audit_task(models.Model):
    AUIDT_TASK_STATUS_WAITE = 1
    AUIDT_TASK_STATUS_AGREE = 2
    AUIDT_TASK_STATUS_REFUSE = 3
    AUIDT_TASK_STATUS_CHOICES = (
        (AUIDT_TASK_STATUS_WAITE, '待审批'),
        (AUIDT_TASK_STATUS_AGREE, '批准'),
        (AUIDT_TASK_STATUS_REFUSE, '拒绝'),
    )

    create_user = models.ForeignKey(User, related_name='create_user_audit_tasks', blank=True, null=True)
    # 审批人有多个，谁审批了这个任务这里就填谁
    audit_user = models.ForeignKey(User, related_name='audit_user_audit_tasks', blank=True, null=True)
    create_datetime = models.DateTimeField(blank=False, null=False)
    audit_datetime = models.DateTimeField(auto_now_add=True)
    status = models.IntegerField(choices=AUIDT_TASK_STATUS_CHOICES)
    # {"task_type":"pc_restore/vol_restore/file_restore/file_verify/forever_kvm/temporary_kvm"}
    task_info = models.TextField(default='{}')
    # {"comments":"这里填审批意见"}
    audit_info = models.TextField(default='{}')


class auto_verify_script(models.Model):
    user = models.ForeignKey(User, related_name='user_auto_verify_script', null=False)
    filename = models.CharField(max_length=256, null=False)
    name = models.CharField(max_length=256, null=False)
    desc = models.CharField(max_length=256)
    path = models.CharField(max_length=256, null=False)
