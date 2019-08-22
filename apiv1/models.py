import datetime
import json
import uuid

import django.utils.timezone as timezone
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import models, IntegrityError
from django.db.models.signals import post_save, post_init
from django.dispatch import receiver

from apiv1.fields import MyDecimalField
from box_dashboard import xlogging, xdatetime

_logger = xlogging.getLogger(__name__)

StartDateTime = timezone.now()
_logger.warning('start date time : {} \n '.format(StartDateTime))


# 磁盘信息
class Disk(models.Model):
    # 磁盘唯一标识
    ident = models.CharField(max_length=32, unique=True)

    def __str__(self):
        return r'disk:{ident}'.format(ident=self.ident)


# 连接过的服务器主机
class Host(models.Model):
    AGENT = 0
    REMOTE_AGENT = 1
    PROXY_AGENT = 2
    ARCHIVE_AGENT = 3
    NAS_AGENT = 4
    DB_AGENT_KVM = 5
    DB_AGENT_REMOTE = 6

    HOST_TYPE_CHOICES = [
        (AGENT, '普通客户端'),
        (REMOTE_AGENT, '远程客户端'),  # 远程灾备产生的镜像客户端
        (PROXY_AGENT, '免代理客户端'),
        (ARCHIVE_AGENT, '数据导入'),
        (NAS_AGENT, 'NAS客户端'),
        (DB_AGENT_KVM, '数据库客户端K'),
        (DB_AGENT_REMOTE, '数据库客户端R')
    ]

    # 主机唯一编码
    ident = models.CharField(max_length=32, unique=True)
    # 用于显示的别名，用户可自定义
    display_name = models.CharField(max_length=256, blank=True)
    # 主机最近的连接时间
    login_datetime = models.DateTimeField(blank=True, null=True)
    # 所有做过备份（有上次备份点）的磁盘
    disks = models.ManyToManyField(Disk, related_name='hosts', blank=True)
    # 从属用户
    user = models.ForeignKey(User, related_name='hosts', blank=True, null=True)
    # 最后登录连接的IP
    last_ip = models.GenericIPAddressField(blank=True, null=True)
    # 扩展参数（JSON格式）
    ext_info = models.TextField(default='{}')
    # 数据传输方式 1加密 2不加密 3加密优先
    network_transmission_type = models.IntegerField(default=2)
    # 所属容灾系统（JSON格式。如果为默认值，标识该主机为本地主机；否则为远端主机）
    aio_info = models.TextField(default='{}')
    # 客户端的类型
    type = models.BigIntegerField(choices=HOST_TYPE_CHOICES, default=0)
    # 归档客户端uuid
    archive_uuid = models.CharField(max_length=32, blank=True, null=True)

    @property
    def is_linked(self):
        if self.type in [Host.AGENT, Host.PROXY_AGENT, Host.DB_AGENT_KVM, Host.DB_AGENT_REMOTE]:
            return (self.login_datetime is not None) and (self.login_datetime > StartDateTime)
        else:
            return True

    @property
    def soft_ident(self):
        for mac in self.macs.all():
            if mac.mac[0] == 'G':
                return mac.mac
        else:
            return ''

    @property
    def is_deleted(self):
        ext_info = json.loads(self.ext_info)
        return ext_info.get('is_deleted', False)

    @property
    def is_verified(self):
        ext_info = json.loads(self.ext_info)
        return ext_info.get('is_valiade_host', False)

    @property
    def is_nas_host(self):
        ext_info = json.loads(self.ext_info)
        return ext_info.get('nas_path', False)

    def login(self, host_ip, local_ip):
        self.login_datetime = timezone.now()
        self.last_ip = host_ip
        ext_info = json.loads(self.ext_info)
        ext_info['local_ip'] = local_ip
        self.ext_info = json.dumps(ext_info, ensure_ascii=False)
        self.save(update_fields=['last_ip', 'login_datetime', 'ext_info'])
        HostLog.objects.create(host=self, type=HostLog.LOG_LOGIN, reason=json.dumps({'ip': host_ip}))

    def logout(self):
        self.login_datetime = None
        self.save(update_fields=['login_datetime'])
        HostLog.objects.create(host=self, type=HostLog.LOG_LOGOUT)

    @property
    def name(self):
        if self.last_ip and (self.last_ip != '127.0.0.1'):
            return '{}({})'.format(self.display_name, self.last_ip)
        else:
            return self.display_name

    def reportAgentModuleError(self, reason):
        HostLog.objects.create(host=self, type=HostLog.LOG_INIT_ERROR, reason=json.dumps(reason, ensure_ascii=False))

    def set_delete(self, is_deleted=True):
        ext_info = json.loads(self.ext_info)
        ext_info['is_deleted'] = is_deleted
        self.ext_info = json.dumps(ext_info)
        self.save(update_fields=['ext_info'])
        if is_deleted:
            self.display_name = '{}{}'.format(self.display_name, '(已删除)')
            self.save(update_fields=['display_name'])
        else:
            self.display_name = self.display_name[:-5] + self.display_name[-5:].replace('(已删除)', '', 1)
            self.save(update_fields=['display_name'])

    @property
    def is_remote(self):
        return self.aio_info != '{}'

    def __str__(self):
        return r'host:[{name}] ident:({ident}) {link_status} {gmac}'.format(
            name=self.display_name, ident=self.ident, link_status='linked' if self.is_linked else 'disconnected',
            gmac=self.soft_ident
        )

    # vmware 代理客户端的home目录
    @property
    def home_path(self):
        if hasattr(self, 'vm_session'):
            return self.vm_session.home_path
        else:
            return ''


@receiver(post_init, sender=Host)
def after_init_host(instance, **kwargs):
    instance.__original_ext_info = instance.ext_info


@receiver(post_save, sender=Host)
def after_update_host(instance, created, **kwargs):
    if not created and instance.__original_ext_info != instance.ext_info:
        cache.set('host_{}'.format(instance.ident), None, None)


# 服务器主机日志
class HostLog(models.Model):
    LOG_UNKNOWN = 0
    LOG_LOGIN = 1
    LOG_LOGOUT = 2
    LOG_INIT_ERROR = 3
    LOG_BACKUP_START = 4
    LOG_BACKUP_SUCCESSFUL = 5
    LOG_BACKUP_FAILED = 6
    LOG_RESTORE_START = 7
    LOG_RESTORE_SUCCESSFUL = 8
    LOG_RESTORE_FAILED = 9
    LOG_CDP_START = 10
    LOG_CDP_STOP = 11
    LOG_CDP_FAILED = 12
    LOG_CDP_PAUSE = 13
    LOG_CDP_RESTART = 14
    LOG_MIGRATE_START = 15
    LOG_MIGRATE_SUCCESSFUL = 16
    LOG_MIGRATE_FAILED = 17
    LOG_COLLECTION_SPACE = 18
    LOG_AGENT_STATUS = 19
    LOG_HTB = 20
    LOG_CDP_BASE_FINISHED = 21
    LOG_VMWARE_RESTORE = 22
    LOG_ARCHIVE_EXPORT = 23
    LOG_ARCHIVE_IMPORT = 24
    LOG_AUTO_VERIFY_TASK_SUCCESSFUL = 25
    LOG_AUTO_VERIFY_TASK_FAILED = 26
    LOG_AUDIT = 27
    LOG_KVM_BACKUP4SELF = 28
    LOG_KVM_BACKUP4DATABASE = 29

    LOG_CLUSTER_BACKUP_START = 100
    LOG_CLUSTER_BACKUP_BASE = 101
    LOG_CLUSTER_BACKUP_ANALYZE = 102
    LOG_CLUSTER_BACKUP_SNAPSHOT = 103
    LOG_CLUSTER_BACKUP_SUCCESSFUL = 104
    LOG_CLUSTER_BACKUP_FAILED = 105

    LOG_CLUSTER_CDP_START = 150
    LOG_CLUSTER_CDP_BASE = 151
    LOG_CLUSTER_CDP_ANALYZE = 152
    LOG_CLUSTER_CDP_SNAPSHOT = 153
    LOG_CLUSTER_CDP_CDP = 154
    LOG_CLUSTER_CDP_FAILED = 155
    LOG_CLUSTER_CDP_PAUSE = 156
    LOG_CLUSTER_CDP_STOP = 157

    LOG_REMOTE_BACKUP_NORM_START = 200
    LOG_REMOTE_BACKUP_NORM_SUCCESSFUL = 201
    LOG_REMOTE_BACKUP_NORM_FAILED = 202
    LOG_REMOTE_BACKUP_CDP_START = 203
    LOG_REMOTE_BACKUP_CDP_END = 204
    LOG_REMOTE_BACKUP = 205

    LOG_CLUSTER_CDP = 300
    LOG_FILE_SYNC = 400

    LOG_TYPE_CHOICES = (
        (LOG_UNKNOWN, 'unknown'),
        (LOG_LOGIN, '连接'),
        (LOG_LOGOUT, '断开'),

        # {"moduleName": "module", "description": "description string", "debug": "debug string", "rawCode": 55}
        (LOG_INIT_ERROR, '代理程序初始化失败'),

        # {"backup_task": id_number}
        (LOG_BACKUP_START, '备份'),

        # {"backup_task": id_number}
        (LOG_BACKUP_SUCCESSFUL, '备份成功'),

        # {"backup_task": id_number, "debug": "debug string", "description": "description_string"}
        (LOG_BACKUP_FAILED, '备份失败'),

        # {"restore_task": id_number}
        (LOG_RESTORE_START, '还原'),

        # {"restore_task": id_number}
        (LOG_RESTORE_SUCCESSFUL, '还原成功'),

        # {"restore_task": id_number, "debug": "debug string", "description": "description_string"}
        (LOG_RESTORE_FAILED, '还原失败'),

        # {"cdp_task": id_number}
        (LOG_CDP_START, 'CDP保护'),

        # {"cdp_task": id_number}
        (LOG_CDP_STOP, 'CDP保护停止'),

        # {"cdp_task": id_number, "debug": "debug string", "description": "description_string"}
        (LOG_CDP_FAILED, 'CDP保护失败'),

        # {"cdp_task": id_number, "debug": "debug string", "description": "description_string"}
        (LOG_CDP_PAUSE, 'CDP保护暂停'),

        # {"cdp_task": id_number}
        (LOG_CDP_RESTART, 'CDP保护重新开始'),

        # {"migrate_task": id_number}
        (LOG_MIGRATE_START, '迁移'),

        # {"migrate_task": id_number}
        (LOG_MIGRATE_SUCCESSFUL, '迁移成功'),

        # {"migrate_task": id_number, "debug": "debug string", "description": "description_string"}
        (LOG_MIGRATE_FAILED, '迁移失败'),

        # {"collection_task": id_number}
        (LOG_COLLECTION_SPACE, '回收过期数据空间'),

        (LOG_AGENT_STATUS, '基础备份'),

        # {'htb_task':task_id, 'debug':'', "description": "description_string"}
        (LOG_HTB, '热备'),

        (LOG_CDP_BASE_FINISHED, '基础备份完成'),

        (LOG_CLUSTER_BACKUP_START, '集群备份开始'),
        (LOG_CLUSTER_BACKUP_BASE, '集群基础备份'),
        (LOG_CLUSTER_BACKUP_ANALYZE, '分析集群数据'),
        (LOG_CLUSTER_BACKUP_SNAPSHOT, '生成集群快照'),
        (LOG_CLUSTER_BACKUP_SUCCESSFUL, '集群备份成功'),
        (LOG_CLUSTER_BACKUP_FAILED, '集群备份失败'),

        (LOG_CLUSTER_CDP_START, 'CDP集群备份开始'),
        (LOG_CLUSTER_CDP_BASE, 'CDP集群基础备份'),
        (LOG_CLUSTER_CDP_ANALYZE, '分析CDP集群数据'),
        (LOG_CLUSTER_CDP_SNAPSHOT, '生成CDP集群快照'),
        (LOG_CLUSTER_CDP_CDP, 'CDP集群持续保护中'),
        (LOG_CLUSTER_CDP_FAILED, 'CDP集群备份失败'),
        (LOG_CLUSTER_CDP_PAUSE, 'CDP集群备份终止'),
        (LOG_CLUSTER_CDP_STOP, 'CDP集群备份停止'),

        (LOG_REMOTE_BACKUP_NORM_START, '同步普通快照开始'),
        (LOG_REMOTE_BACKUP_NORM_SUCCESSFUL, '同步普通快照成功'),
        (LOG_REMOTE_BACKUP_NORM_FAILED, '同步普通快照失败'),
        (LOG_REMOTE_BACKUP_CDP_START, '同步CDP快照开始'),
        (LOG_REMOTE_BACKUP_CDP_END, '同步CDP快照结束'),
        (LOG_REMOTE_BACKUP, '远程灾备'),
        (LOG_VMWARE_RESTORE, '免代理还原'),
        (LOG_ARCHIVE_EXPORT, '备份数据导出'),
        (LOG_ARCHIVE_IMPORT, '备份数据导入'),
        (LOG_AUTO_VERIFY_TASK_SUCCESSFUL, '自动验证成功'),
        (LOG_AUTO_VERIFY_TASK_FAILED, '自动验证失败'),
        (LOG_AUDIT, '审批'),
        (LOG_CLUSTER_CDP, 'CDP集群备份'),
        (LOG_KVM_BACKUP4SELF, '虚拟机备份'),
        (LOG_KVM_BACKUP4DATABASE, '数据库备份'),
        (LOG_FILE_SYNC, '文件归档'),
    )

    host = models.ForeignKey(Host, related_name='logs', on_delete=models.PROTECT)
    datetime = models.DateTimeField(auto_now_add=True)
    type = models.PositiveSmallIntegerField(choices=LOG_TYPE_CHOICES, default=LOG_UNKNOWN)
    # 日志详细信息，使用JSON存放，见LOG_TYPE_CHOICES注释
    reason = models.TextField(blank=True)

    def __str__(self):
        return r'{time} host:({host}) [{type}] : {reason}'.format(time=self.datetime, host=self.host.display_name,
                                                                  type=self.get_type_display(), reason=self.reason)


# 服务器主机的网卡硬件信息
class HostMac(models.Model):
    host = models.ForeignKey(Host, related_name='macs')
    # 物理网卡信息（字节流十六进制表示）
    mac = models.CharField(max_length=12)
    # 对不同主机上的重复网卡信息做标记
    duplication = models.BooleanField(default=False)

    def __str__(self):
        return r'mac:[{mac}] {status}'.format(mac=self.mac, status='duplication' if self.duplication else 'unique')


# 备份任务计划表
class BackupTaskSchedule(models.Model):
    # 备份源类型
    BACKUP_DISKS = 1
    BACKUP_DATABASES = 2
    BACKUP_VIRTUAL_PLATFORM = 3
    BACKUP_FILES = 4

    # 计划执行时间表类型
    CYCLE_CDP = 1
    CYCLE_ONCE = 2
    CYCLE_PERDAY = 3
    CYCLE_PERWEEK = 4
    CYCLE_PERMONTH = 5

    BACKUP_SOURCE_CHOICES = (
        (BACKUP_DISKS, '整机备份'),
        (BACKUP_DATABASES, '数据库备份'),
        (BACKUP_VIRTUAL_PLATFORM, '虚拟平台备份'),
        (BACKUP_FILES, '文件备份'),
    )

    CYCLE_CHOICES = (
        (CYCLE_CDP, 'CDP备份'),
        (CYCLE_ONCE, '仅备份一次'),
        (CYCLE_PERDAY, '每天'),
        (CYCLE_PERWEEK, '每周'),
        (CYCLE_PERMONTH, '每月'),
    )

    # 使能/禁用
    enabled = models.BooleanField(blank=True, default=True)
    # 删除（不会在界面显示）
    deleted = models.BooleanField(blank=True, default=False)
    # 计划名称，用户可自定义
    name = models.CharField(max_length=256)
    # 备份源类型
    backup_source_type = models.IntegerField(choices=BACKUP_SOURCE_CHOICES)
    # 计划周期类型
    cycle_type = models.IntegerField(choices=CYCLE_CHOICES)
    # 计划创建时间
    created = models.DateTimeField(auto_now_add=True)
    # 计划开始时间
    plan_start_date = models.DateTimeField(blank=True, null=True, default=None)
    # 关联的主机
    host = models.ForeignKey(Host, related_name='backup_task_schedules', on_delete=models.PROTECT)
    # 任务扩展配置（JSON格式）
    ext_config = models.TextField(default='')
    # 上次备份时间
    last_run_date = models.DateTimeField(blank=True, null=True, default=None)
    # 下次备份时间
    next_run_date = models.DateTimeField(blank=True, null=True, default=None)
    # 为该计划指派一个存储节点
    storage_node_ident = models.CharField(max_length=256, blank=False)

    @property
    def host_ident(self):
        return self.host.ident

    @property
    def abstract_name(self):
        if self.cycle_type == self.CYCLE_ONCE:
            return '仅备份一次'
        if self.cycle_type == self.CYCLE_CDP:
            return '持续备份'
        if self.cycle_type == self.CYCLE_PERDAY:
            ext_config = json.loads(self.ext_config)
            if ext_config['IntervaBLOCKit'] == 'min':
                return r'每{}分钟备份'.format(ext_config['backupDayInterval'] // 60)
            if ext_config['IntervaBLOCKit'] == 'hour':
                return r'每{}小时备份'.format(ext_config['backupDayInterval'] // 3600)
            if ext_config['IntervaBLOCKit'] == 'day':
                return r'每{}天备份'.format(ext_config['backupDayInterval'] // (24 * 3600))
            else:
                xlogging.raise_and_logging_error('参数错误',
                                                 'abstract_name IntervaBLOCKit is {}'.format(
                                                     ext_config['IntervaBLOCKit']))
        if self.cycle_type == self.CYCLE_PERWEEK:
            ext_config = json.loads(self.ext_config)
            week = ["一", "二", "三", "四", "五", "六", "日"]
            return r'每周{}备份'.format('、'.join([week[w - 1] for w in sorted(ext_config['daysInWeek'])]))
        if self.cycle_type == self.CYCLE_PERMONTH:
            ext_config = json.loads(self.ext_config)
            return r'每月{}日备份'.format('、'.join([str(m) for m in ext_config['daysInMonth']]))
        xlogging.raise_and_logging_error('参数错误', 'abstract_name cycle_type is {}'.format(self.cycle_type))

    @property
    def backup_source_type_display(self):
        return self.get_backup_source_type_display()

    @property
    def cycle_type_display(self):
        return self.get_cycle_type_display()

    def delete_and_collection_space_later(self):
        self.deleted = True
        self.enabled = True
        self.save(update_fields=['deleted', 'enabled'])


class ClusterBackupSchedule(models.Model):
    # 使能/禁用
    enabled = models.BooleanField(blank=True, default=True)
    # 删除（不会在界面显示）
    deleted = models.BooleanField(blank=True, default=False)
    # 计划名称，用户可自定义
    name = models.CharField(max_length=256)
    # 计划周期类型
    cycle_type = models.IntegerField(choices=BackupTaskSchedule.CYCLE_CHOICES)
    # 计划创建时间
    created = models.DateTimeField(auto_now_add=True)
    # 计划开始时间
    plan_start_date = models.DateTimeField(blank=True, null=True, default=None)
    # 关联的主机
    hosts = models.ManyToManyField(Host, related_name='cluster_backup_schedules')
    # 任务扩展配置（JSON格式）
    ext_config = models.TextField(default='{}')
    # 上次备份时间
    last_run_date = models.DateTimeField(blank=True, null=True, default=None)
    # 下次备份时间
    next_run_date = models.DateTimeField(blank=True, null=True, default=None)
    # 为该计划指派一个存储节点
    storage_node_ident = models.CharField(max_length=256, blank=False)
    # Agent的有效快照点以及从节点的已有快照链（JSON格式）
    agent_valid_disk_snapshot = models.TextField(default='{}')

    @property
    def cycle_type_display(self):
        return self.get_cycle_type_display()

    def delete_and_collection_space_later(self):
        self.deleted = True
        self.enabled = True
        self.save(update_fields=['deleted', 'enabled'])

    def set_enabled(self, enable):
        self.enabled = enable
        self.save(update_fields=['enabled'])


class RemoteBackupSchedule(models.Model):
    # 使能/禁用
    enabled = models.BooleanField(blank=True, default=True)
    # 删除（不会在界面显示）
    deleted = models.BooleanField(blank=True, default=False)
    # 计划名称，用户可自定义
    name = models.CharField(max_length=256)
    # 计划创建时间
    created = models.DateTimeField(auto_now_add=True)
    # 关联的主机，仅能关联远端主机（见Host.aio_info）
    host = models.ForeignKey(Host, related_name='remote_backup_schedules', on_delete=models.PROTECT)
    # 任务扩展配置（JSON格式）
    ext_config = models.TextField(default='{}')
    # 为该计划指派一个存储节点
    storage_node_ident = models.CharField(max_length=256, blank=False)
    # 下次运行时间
    next_run_date = models.DateTimeField(blank=True, null=True, default=None)
    # 上次运行时间
    last_run_date = models.DateTimeField(blank=True, null=True, default=None)

    def set_enabled(self, enable):
        self.enabled = enable
        self.save(update_fields=['enabled'])

    def delete_and_collection_space_later(self):
        self.deleted = True
        self.enabled = True
        self.save(update_fields=['deleted', 'enabled'])


# 服务器主机的快照信息
class HostSnapshot(models.Model):
    host = models.ForeignKey(Host, related_name='snapshots', on_delete=models.PROTECT, null=True)
    # 开始时间
    start_datetime = models.DateTimeField(blank=True, null=True, default=None)
    # 完成时间
    finish_datetime = models.DateTimeField(blank=True, null=True, default=None)
    # 是否成功
    successful = models.NullBooleanField(default=None)
    # 是否被删除
    deleted = models.BooleanField(default=False)
    # 扩展信息
    ext_info = models.TextField(default='{}')
    # 实时状态
    display_status = models.TextField(default='')
    # 进入删除流程
    deleting = models.BooleanField(default=False)
    # 是否是CDP主机快照
    is_cdp = models.BooleanField(default=False)
    # 隶属计划
    schedule = models.ForeignKey(BackupTaskSchedule, related_name='host_snapshots', on_delete=models.PROTECT, null=True)
    # 隶属集群备份计划
    cluster_schedule = models.ForeignKey(ClusterBackupSchedule, related_name='host_snapshots',
                                         on_delete=models.PROTECT, null=True)
    # 集群备份完成
    cluster_finish_datetime = models.DateTimeField(blank=True, null=True, default=None)
    # 隶属同步计划
    remote_schedule = models.ForeignKey(RemoteBackupSchedule, related_name='host_snapshots',
                                        on_delete=models.PROTECT, null=True)
    # 集群备份点是否可见
    cluster_visible = models.BooleanField(default=False)
    # 不完整快照点
    partial = models.BooleanField(default=False)
    # label 用户添加的标签
    label = models.TextField(default='')

    @property
    def host_ident(self):
        return self.host.ident

    STATUS_INVISIBLE = 'invisible'
    STATUS_BACKUPING = 'backuping'
    STATUS_NORMAL = 'normal'
    STATUS_FAILED = 'failed'
    STATUS_DELETING = 'deleting'
    STATUS_DELETED = 'deleted'

    STATUS_CDP_BEGIN = 'cdp_begin'
    STATUS_CDPING = 'cdping'
    STATUS_CDP_END = 'cdp_end'

    @property
    def status(self):
        if self.start_datetime is None:
            return self.STATUS_INVISIBLE
        if self.is_cdp:
            if self.finish_datetime is not None:
                if self.successful:
                    if self.partial:
                        return self.STATUS_FAILED
                    if self.deleted:
                        return self.STATUS_DELETED
                    if self.deleting:
                        return self.STATUS_DELETING
                    if self.cdp_info.stopped:
                        return self.STATUS_CDP_END
                    return self.STATUS_CDPING
                else:
                    return self.STATUS_FAILED
            else:
                return self.STATUS_CDP_BEGIN
        else:
            if self.finish_datetime is not None:
                if self.successful:
                    if self.partial:
                        return self.STATUS_FAILED
                    if self.deleted:
                        return self.STATUS_DELETED
                    if self.deleting:
                        return self.STATUS_DELETING
                    return self.STATUS_NORMAL
                else:
                    return self.STATUS_FAILED
            else:
                return self.STATUS_BACKUPING

    @property
    def backup_datetime(self):
        if self.is_cdp:
            return None
        elif self.start_datetime:
            return self.start_datetime.strftime(xdatetime.FORMAT_WITH_MICROSECOND)
        else:
            return None

    @property
    def first_datetime(self):
        if not self.is_cdp:
            return None
        try:
            result = self.cdp_info.first_datetime
            return result.strftime(xdatetime.FORMAT_WITH_MICROSECOND) if result else self.backup_datetime
        except HostSnapshotCDP.DoesNotExist:
            return self.backup_datetime

    @property
    def last_datetime(self):
        if not self.is_cdp:
            return None
        try:
            result = self.cdp_info.last_datetime
            return result.strftime(xdatetime.FORMAT_WITH_MICROSECOND) if result else self.backup_datetime
        except HostSnapshotCDP.DoesNotExist:
            return self.backup_datetime

    # collection_space_later
    def set_deleting(self):
        result = self.deleting
        self.deleting = True
        self.save(update_fields=['deleting'])
        return result

    @property
    def name(self):
        if self.is_cdp:
            return 'CDP备份 {}'.format(self.start_datetime)
        else:
            return '整机备份 {}'.format(self.start_datetime)


# 磁盘快照为CDP时的扩展信息
class HostSnapshotCDP(models.Model):
    host_snapshot = models.OneToOneField(HostSnapshot, primary_key=True, related_name='cdp_info',
                                         on_delete=models.PROTECT)
    # 是否停止
    stopped = models.BooleanField(default=False)
    # 是否被合并过
    merged = models.BooleanField(default=False)
    # 该CDP快照最后的时间点
    last_datetime = models.DateTimeField(blank=True, null=True, default=None)
    # 该CDP快照最早的时间点
    first_datetime = models.DateTimeField(blank=True, null=True, default=None)


# 磁盘的快照信息
class DiskSnapshot(models.Model):
    DISK_RAW = 0
    DISK_MBR = 1
    DISK_GPT = 2

    DISK_TYPE_CHOICES = (
        (DISK_RAW, 'RAW'),
        (DISK_MBR, 'MBR'),
        (DISK_GPT, 'GPT'),
    )

    disk = models.ForeignKey(Disk, related_name='snapshots', on_delete=models.PROTECT)
    # 用于显示的别名
    display_name = models.CharField(max_length=256, blank=True)
    # 父快照（基础备份没有"父快照"）
    parent_snapshot = models.ForeignKey('self', related_name='child_snapshots', blank=True, null=True,
                                        on_delete=models.PROTECT)
    # 存放快照的镜像文件全路径
    image_path = models.CharField(max_length=256)
    # 唯一标识
    ident = models.CharField(max_length=32, unique=True)
    # 从属的主机快照点
    host_snapshot = models.ForeignKey(HostSnapshot, related_name='disk_snapshots', on_delete=models.PROTECT, blank=True,
                                      null=True, default=None)
    # 磁盘字节大小
    bytes = models.BigIntegerField()
    # 磁盘的分区格式
    type = models.PositiveSmallIntegerField(choices=DISK_TYPE_CHOICES, default=DISK_RAW)
    # 是否是启动磁盘
    boot_device = models.BooleanField()
    # 当父节点为CDP时，记录CDP中的时间节点
    parent_timestamp = MyDecimalField(null=True, blank=True, default=None)
    # 是否被合并
    merged = models.BooleanField(default=False)
    # 引用记录
    reference_tasks = models.TextField(default='')
    # 增量备份字节，image_path为.cdp时，该字段无意义
    inc_date_bytes = models.BigIntegerField(default=-1)
    # 是否整理过hash
    reorganized_hash = models.BooleanField(default=False)
    # 扩展信息
    ext_info = models.TextField(default='{}')
    # 进入了删除流程
    deleting = models.BooleanField(default=False)

    # collection_space_later
    def set_deleting(self):
        result = self.deleting
        self.deleting = True
        self.save(update_fields=['deleting'])
        return result

    @property
    def type_display(self):
        return self.get_type_display()

    @property
    def is_base_snapshot(self):
        return self.parent_snapshot is None

    @property
    def is_finished(self):
        if self.host_snapshot is None:
            return None
        else:
            return self.host_snapshot.finish_datetime is not None

    @property
    def is_cdp(self):
        return self.is_cdp_file(self.image_path)

    @staticmethod
    def is_cdp_file(path):
        return path.endswith('.cdp')

    @property
    def hash_path(self):
        if self.is_cdp:
            return ''
        else:
            return '{}_{}.hash'.format(self.image_path, self.ident)

    def __str__(self):
        status = list()
        if self.host_snapshot is None:
            status.append('none')
        else:
            if self.host_snapshot.deleted:
                status.append('deleted')
            if self.host_snapshot.deleting:
                status.append('deleting')
        if self.is_finished:
            if self.host_snapshot is not None:
                if self.host_snapshot.successful:
                    status.append('succcessful')
                else:
                    status.append('failed')
        else:
            status.append('processing')
        start = self.host_snapshot.start_datetime if self.host_snapshot is not None else None
        finish = self.host_snapshot.finish_datetime if self.host_snapshot is not None else None
        return r'{type} snapshot [{ident}] in ({path}) start:{start} finish:{finish} status:{status}' \
            .format(type='base' if self.is_base_snapshot else ('incremental' if not self.is_cdp else 'cdp'),
                    ident=self.ident, path=self.image_path, start=start,
                    finish=(finish if self.is_finished else 'unknown' if self.is_finished is None else 'processing'),
                    status=' '.join(status))


# 还原目标客户端
class RestoreTarget(models.Model):
    TYPE_UNKNOWN = 128
    TYPE_PE = 0
    TYPE_AGENT = 1
    TYPE_AGENT_RESTORE = 2

    TYPE_CHOICES = (
        (TYPE_UNKNOWN, '未知类型'),
        (TYPE_PE, 'PE系统'),
        (TYPE_AGENT, 'AGENT系统'),
        (TYPE_AGENT_RESTORE, '卷还原'),
    )

    # 唯一标识
    ident = models.CharField(max_length=32, unique=True)
    # 用于显示的别名，用户自定义
    display_name = models.CharField(max_length=256, blank=True)
    # 开始还原的时间
    # 还未生成好还原参数时为null，否则为生成的时间
    start_datetime = models.DateTimeField(blank=True, null=True)
    # 完成时间
    finish_datetime = models.DateTimeField(blank=True, null=True)
    # 是否成功
    successful = models.BooleanField(default=False)
    # 需要还原的数据量
    total_bytes = models.BigIntegerField(null=True, blank=True, default=None)
    # 已经还原的数据量
    restored_bytes = models.BigIntegerField(null=True, blank=True, default=None)
    # token标识符过期时间（多个RestoreTargetDisk共用）
    token_expires = models.DateTimeField(null=True, blank=True, default=None)
    # KTService::Token.keepAliveIntervalSeconds参数
    keep_alive_interval_seconds = models.IntegerField(default=3600)
    # KTService::Token.expiryMinutes参数
    expiry_minutes = models.IntegerField(default=1440)
    # 主机信息 Json
    info = models.TextField(default='{}')
    # 类型
    type = models.PositiveSmallIntegerField(choices=TYPE_CHOICES, default=TYPE_UNKNOWN)
    # 实时状态
    display_status = models.TextField(default='')
    # 所属用户
    user = models.ForeignKey(User, related_name='pe_hosts', blank=True, null=True)

    @property
    def is_finished(self):
        return self.finish_datetime is not None

    @property
    def restore_task_object(self):
        try:
            return self.restore
        except RestoreTask.DoesNotExist:
            return None


class RestoreTargetDisk(models.Model):
    pe_host = models.ForeignKey(RestoreTarget, related_name='disks', on_delete=models.PROTECT)
    # 唯一标识符
    token = models.CharField(max_length=32, unique=True)
    # 还原时读取的磁盘快照
    snapshot = models.ForeignKey(DiskSnapshot, related_name='restore_target_disks', on_delete=models.PROTECT)
    # 当snapshot为CDP时，记录CDP中的时间节点
    snapshot_timestamp = MyDecimalField(null=True, blank=True, default=None)
    # 快照链的hash文件路径
    hash_path = models.CharField(max_length=256, default='')


# 迁移任务
class MigrateTask(models.Model):
    SOURCE_TYPE_UNKNOWN = 0
    SOURCE_TYPE_NORMAL = 1
    SOURCE_TYPE_TEMP_NORMAL = 2
    SOURCE_TYPE_CDP = 3

    SOURCE_TYPE_CHOICES = (
        (SOURCE_TYPE_UNKNOWN, '未知源类型'),
        (SOURCE_TYPE_NORMAL, '保留中转数据'),
        (SOURCE_TYPE_TEMP_NORMAL, '丢弃中转数据'),
        (SOURCE_TYPE_CDP, '使用CDP数据'),
    )

    DESTINATION_TYPE_UNKNOWN = 0
    DESTINATION_TYPE_HOST = 1
    DESTINATION_TYPE_PE = 2

    DESTINATION_TYPE_CHOICES = (
        (DESTINATION_TYPE_UNKNOWN, '未知目标类型'),
        (DESTINATION_TYPE_HOST, '迁移到在线客户端'),
        (DESTINATION_TYPE_PE, '迁移到离线客户端'),
    )

    # 开始时间
    start_datetime = models.DateTimeField(auto_now_add=True)
    # 完成时间
    finish_datetime = models.DateTimeField(null=True, blank=True, default=None)
    # 是否成功
    successful = models.BooleanField(default=False)
    # 源主机
    source_host = models.ForeignKey(Host, related_name='migrate_sources', on_delete=models.PROTECT)
    # 源类型
    source_type = models.PositiveSmallIntegerField(choices=SOURCE_TYPE_CHOICES, default=SOURCE_TYPE_UNKNOWN)
    # 源服务器快照
    host_snapshot = models.ForeignKey(HostSnapshot, related_name='migrates', on_delete=models.PROTECT, null=True)
    # 目标类型
    destination_type = models.PositiveSmallIntegerField(choices=DESTINATION_TYPE_CHOICES,
                                                        default=DESTINATION_TYPE_UNKNOWN)
    # 目标服务器（当destination_type为host时有意义）
    destination_host = models.ForeignKey(Host, related_name='migrate_destinations', on_delete=models.PROTECT, null=True)
    # 还原目标（当源服务器快照需要新建时，该值可能会是None，直到快照信息生成）
    restore_target = models.OneToOneField(RestoreTarget, related_name='migrate', on_delete=models.PROTECT)
    # 扩展参数（JSON格式）
    ext_config = models.TextField(default='{}')

    def set_host_snapshot(self, host_snapshot_object):
        if self.host_snapshot is not None:
            xlogging.raise_and_logging_error('内部异常，代码2333', 'set host_snapshot only once')
        self.host_snapshot = host_snapshot_object
        self.save(update_fields=['host_snapshot'])

    @property
    def user_account(self):
        return self.source_host.user.username

    @property
    def name(self):
        try:
            host_ip = self.host_snapshot.host.last_ip
            restore_target = self.restore_target
            pe_ip = json.loads(restore_target.info)['remote_ip'] if restore_target else '--'
        except Exception:
            return '迁移-{}'.format(self.id)
        else:
            return '迁移-{}到{}'.format(host_ip, pe_ip)


# 恢复任务
class RestoreTask(models.Model):
    TYPE_UNKNOWN = 0
    TYPE_HOST = 1
    TYPE_PE = 2
    TYPE_VOLUME = 3

    TYPE_CHOICES = (
        (TYPE_UNKNOWN, '未知目标类型'),
        (TYPE_HOST, '恢复到在线客户端'),
        (TYPE_PE, '恢复到启动介质客户端'),
        (TYPE_VOLUME, '卷恢复')
    )

    # 开始时间
    start_datetime = models.DateTimeField(auto_now_add=True)
    # 完成时间
    finish_datetime = models.DateTimeField(null=True, blank=True, default=None)
    # 是否成功
    successful = models.BooleanField(default=False)
    # 目标类型
    type = models.PositiveSmallIntegerField(choices=TYPE_CHOICES, default=TYPE_UNKNOWN)
    # 目标服务器（当type为host时有意义）
    target_host = models.ForeignKey(Host, related_name='restores', on_delete=models.PROTECT, null=True)
    # 源服务器快照
    host_snapshot = models.ForeignKey(HostSnapshot, related_name='restores', on_delete=models.PROTECT)
    # 还原目标
    restore_target = models.OneToOneField(RestoreTarget, related_name='restore', on_delete=models.PROTECT)
    # 扩展参数（JSON格式）
    ext_config = models.TextField(default='{}')

    @property
    def name(self):
        try:
            host_ip = self.host_snapshot.host.last_ip
            restore_target = self.restore_target
            pe_ip = json.loads(restore_target.info)['remote_ip'] if restore_target else '--'
        except Exception:
            return '恢复-{}'.format(self.id)
        else:
            return '恢复-{}到{}'.format(host_ip, pe_ip)

    @property
    def user_account(self):
        return self.host_snapshot.host.user.username

    @property
    def snapshot_id(self):
        return self.host_snapshot_id

    @property
    def host_ident(self):
        if self.host_snapshot_id:
            if self.host_snapshot.host_id:
                return self.host_snapshot.host.ident
        return None

    @property
    def backup_datetime(self):
        return self.start_datetime


# 备份任务
class BackupTask(models.Model):
    REASON_UNKNOWN = 0
    REASON_PLAN_AUTO = 1
    REASON_PLAN_MANUAL = 2

    REASON_CHOICES = (
        (REASON_UNKNOWN, '未知原因'),
        (REASON_PLAN_AUTO, '自动执行'),
        (REASON_PLAN_MANUAL, '手动执行'),
    )

    # 开始时间
    start_datetime = models.DateTimeField(auto_now_add=True)
    # 完成时间
    finish_datetime = models.DateTimeField(null=True, blank=True, default=None)
    # 是否成功
    successful = models.BooleanField(default=False)
    # 触发原因
    reason = models.PositiveSmallIntegerField(choices=REASON_CHOICES, default=REASON_UNKNOWN)
    # 哪个计划
    schedule = models.ForeignKey(BackupTaskSchedule, related_name='backup_tasks', on_delete=models.PROTECT)
    # 快照
    host_snapshot = models.OneToOneField(HostSnapshot, related_name='backup_task', on_delete=models.PROTECT, null=True)
    # 扩展参数（JSON格式）
    ext_config = models.TextField(default='{}')

    def set_host_snapshot(self, host_snapshot_object):
        if self.host_snapshot is not None:
            xlogging.raise_and_logging_error('内部异常，代码2331', 'set host_snapshot only once')
        self.host_snapshot = host_snapshot_object
        self.save(update_fields=['host_snapshot'])

    @property
    def user_account(self):
        if self.schedule.host.user_id:
            return self.schedule.host.user.username
        return None


# 远程备份任务（主机级别）
class RemoteBackupTask(models.Model):
    QUERY_HOST_SNAPSHOT = -1
    NEW_HOST_SNAPSHOT = 0
    QUERY_DISK_STATUS = 1
    NEW_SUB_TASK = 2
    TRANS_DATA = 3
    NETWORK_UNREACHABLE = 4
    INVALID_USER = 8
    SNAPSHOT_NOT_USEABLE = 5
    SCHEDULE_DISABLE = 6
    SCHEDULE_DELETED = 7

    STATUS_CHOICES = (
        (QUERY_HOST_SNAPSHOT, '查询快照点状态'),
        (NEW_HOST_SNAPSHOT, '创建主机快照'),
        (QUERY_DISK_STATUS, '查询磁盘状态'),
        (NEW_SUB_TASK, '创建同步子任务'),
        (TRANS_DATA, '同步数据'),
        (NETWORK_UNREACHABLE, '通信异常, 无法同步'),
        (INVALID_USER, '连接参数异常, 无法同步；请检查用户名和密码'),
        (SNAPSHOT_NOT_USEABLE, '快照文件被删除, 无法同步'),
        (SCHEDULE_DISABLE, '计划被禁用, 无法同步'),
        (SCHEDULE_DELETED, '计划被删除, 无法同步'),
    )

    # 开始时间
    start_datetime = models.DateTimeField(auto_now_add=True)
    # 完成时间
    finish_datetime = models.DateTimeField(null=True, blank=True, default=None)
    # 是否成功
    successful = models.BooleanField(default=False)
    # 哪个计划
    schedule = models.ForeignKey(RemoteBackupSchedule, related_name='remote_backup_tasks', on_delete=models.PROTECT)
    # 快照
    host_snapshot = models.OneToOneField(HostSnapshot, related_name='remote_backup_task', on_delete=models.PROTECT,
                                         null=True)
    # 扩展参数（JSON格式）
    ext_config = models.TextField(default='{}')
    # 任务状态
    status = models.IntegerField(choices=STATUS_CHOICES, default=QUERY_HOST_SNAPSHOT)
    # 任务被暂停
    paused = models.BooleanField(default=False)

    def set_host_snapshot(self, host_snapshot_object):
        if self.host_snapshot is not None:
            xlogging.raise_and_logging_error('内部异常，代码2389', 'set host_snapshot only once')
        self.host_snapshot = host_snapshot_object
        self.save(update_fields=['host_snapshot'])

    def set_status(self, status):
        self.status = status
        self.save(update_fields=['status'])


# 远程备份子任务（磁盘快照级别）
class RemoteBackupSubTask(models.Model):
    # 开始时间
    start_datetime = models.DateTimeField(auto_now_add=True)
    # 完成时间
    finish_datetime = models.DateTimeField(null=True, blank=True, default=None)
    # 是否成功
    successful = models.BooleanField(default=False)
    # 属于哪个远程备份任务
    main_task = models.ForeignKey(RemoteBackupTask, related_name='remote_backup_sub_tasks', on_delete=models.PROTECT)
    # 本地磁盘快照
    local_snapshot = models.OneToOneField(DiskSnapshot, related_name='remote_backup_sub_task', on_delete=models.PROTECT)
    # 远端磁盘快照
    remote_snapshot_ident = models.CharField(max_length=32)
    # 远端磁盘快照时间戳
    remote_timestamp = MyDecimalField(blank=True, null=True, default=-1)
    # 远端磁盘快照路径
    remote_snapshot_path = models.CharField(max_length=256, default='invalid')
    # 扩展参数（JSON格式）
    ext_config = models.TextField(default='{}')

    @property
    def name(self):
        if self.main_task.host_snapshot_id:
            return '远程灾备-{}'.format(self.main_task.host_snapshot.name)
        return '没有相应主机快照'


# 备份任务
class ClusterBackupTask(models.Model):
    # 开始时间
    start_datetime = models.DateTimeField(auto_now_add=True)
    # 完成时间
    finish_datetime = models.DateTimeField(null=True, blank=True, default=None)
    # 是否成功
    successful = models.BooleanField(default=False)
    # 触发原因
    reason = models.PositiveSmallIntegerField(choices=BackupTask.REASON_CHOICES, default=BackupTask.REASON_UNKNOWN)
    # 哪个计划
    schedule = models.ForeignKey(ClusterBackupSchedule, related_name='backup_tasks', on_delete=models.PROTECT)
    # 扩展参数（JSON格式）
    ext_config = models.TextField(default='{}')
    # running task
    task_uuid = models.TextField(default=r'{}')
    # run status
    status_info = models.TextField(default=r'None')

    def run_status(self, info=r''):
        if info is r'':
            return self.status_info
        self.status_info = info
        self.save(update_fields=['status_info'])

    @property
    def user_account(self):
        host = self.schedule.hosts.first()
        if host:
            if host.user_id:
                return host.user.username
        return ""


# CDP任务
class CDPTask(models.Model):
    # 开始时间
    start_datetime = models.DateTimeField(auto_now_add=True)
    # 完成时间
    finish_datetime = models.DateTimeField(null=True, blank=True, default=None)
    # 是否成功
    successful = models.BooleanField(default=False)
    # 隶属计划
    schedule = models.ForeignKey(BackupTaskSchedule, related_name='cdp_tasks', on_delete=models.PROTECT, null=True)
    # 隶属集群任务
    cluster_task = models.ForeignKey(ClusterBackupTask, related_name='sub_tasks', on_delete=models.PROTECT, null=True)
    # 快照
    host_snapshot = models.OneToOneField(HostSnapshot, related_name='cdp_task', on_delete=models.PROTECT, null=True)
    # 扩展参数（JSON格式）
    ext_config = models.TextField(default='{}')

    def set_host_snapshot(self, host_snapshot_object):
        if self.host_snapshot is not None:
            xlogging.raise_and_logging_error('内部异常，代码2330', 'set host_snapshot only once')
        self.host_snapshot = host_snapshot_object
        self.save(update_fields=['host_snapshot'])

    @property
    def user_account(self):
        if self.schedule.host.user_id:
            return self.schedule.host.user.username
        return None


# CDP Token
class CDPDiskToken(models.Model):
    # 依赖的普通快照点
    parent_disk_snapshot = models.OneToOneField(
        DiskSnapshot, related_name='cdp_token', on_delete=models.PROTECT)
    # 隶属任务
    task = models.ForeignKey(CDPTask, related_name='tokens', on_delete=models.PROTECT)
    # 唯一标识符
    token = models.CharField(max_length=32, unique=True)
    # token标识符过期时间。用来判断CDP保护的开始与暂停，值为None时表示没有CDP保护，有数据时表示有CDP保护
    token_expires = models.DateTimeField(null=True, blank=True, default=None)
    # KTService::Token.keepAliveIntervalSeconds参数
    keep_alive_interval_seconds = models.IntegerField(default=3600)
    # KTService::Token.expiryMinutes参数
    expiry_minutes = models.IntegerField(default=52560000)
    # 正在写入的CDP快照点
    using_disk_snapshot = models.ForeignKey(DiskSnapshot, related_name='using_token', on_delete=models.PROTECT,
                                            null=True, blank=True, default=None)
    # 最后一次写入的CDP文件
    last_disk_snapshot = models.ForeignKey(DiskSnapshot, related_name='last_token', on_delete=models.PROTECT,
                                           null=True, blank=True, default=None)

    def __str__(self):
        return r'cdp token:[{token}] {status}'.format(token=self.token,
                                                      status='pausing' if self.token_expires is None else 'protecting')


# 集群备份，Token影射
class ClusterTokenMapper(models.Model):
    cluster_task = models.ForeignKey(ClusterBackupTask, related_name='token_maps', on_delete=models.PROTECT)
    # 客户端的token
    agent_token = models.CharField(max_length=32, unique=True)
    # 客户端的ident
    host_ident = models.CharField(max_length=32)
    # 客户端的磁盘序号
    disk_id = models.IntegerField()
    # 存储文件的token
    file_token = models.ForeignKey(CDPDiskToken, related_name='token_maps', on_delete=models.PROTECT, blank=True,
                                   null=True)


# 磁盘快照为CDP时的扩展信息
class DiskSnapshotCDP(models.Model):
    # 当该快照为CDP时，记录CDP Token
    disk_snapshot = models.OneToOneField(DiskSnapshot, primary_key=True, related_name='cdp_info',
                                         on_delete=models.PROTECT)
    # 对应的Token记录
    token = models.ForeignKey(CDPDiskToken, related_name='files', on_delete=models.PROTECT, null=True)
    # 该CDP文件第一个timestamp
    first_timestamp = MyDecimalField()
    # 该CDP文件最后一个timestamp
    last_timestamp = MyDecimalField(blank=True, null=True, default=None)


# 空间回收任务
class SpaceCollectionTask(models.Model):
    TYPE_UNKNOWN = 0
    TYPE_NORMAL_DELETE = 1
    TYPE_NORMAL_MERGE = 2
    TYPE_CDP_MERGE = 3
    TYPE_CDP_MERGE_SUB = 4
    TYPE_DELETE_SNAPSHOT = 5
    TYPE_DELETE_CDP_FILE = 6
    TYPE_DELETE_CDP_OBJECT = 7
    TYPE_CDP_DELETE = 8
    TYPE_MERGE_SNAPSHOT = 9

    TYPE_CHOICES = (
        (TYPE_UNKNOWN, '未知类型'),
        (TYPE_NORMAL_DELETE, '删除普通备份点'),
        (TYPE_NORMAL_MERGE, '合并普通备份点'),
        (TYPE_CDP_MERGE, '回收CDP备份主任务'),
        (TYPE_CDP_MERGE_SUB, '回收CDP备份子任务'),
        (TYPE_DELETE_SNAPSHOT, '删除快照点'),
        (TYPE_DELETE_CDP_FILE, '删除CDP文件'),
        (TYPE_DELETE_CDP_OBJECT, '删除CDP快照'),
        (TYPE_CDP_DELETE, '删除CDP客户端快照'),
        (TYPE_MERGE_SNAPSHOT, '合并快照点'),
    )

    # 任务类型
    type = models.PositiveSmallIntegerField(choices=TYPE_CHOICES, default=TYPE_UNKNOWN)
    # 主机快照
    host_snapshot = models.ForeignKey(HostSnapshot, related_name='space_collection_tasks', on_delete=models.PROTECT,
                                      null=True)
    # 从属计划
    schedule = models.ForeignKey(BackupTaskSchedule, related_name='space_collection_tasks', on_delete=models.PROTECT,
                                 null=True)
    # 从属集群计划
    cluster_schedule = models.ForeignKey(ClusterBackupSchedule, related_name='space_collection_tasks', null=True,
                                         on_delete=models.PROTECT)
    # 从属同步计划
    remote_schedule = models.ForeignKey(RemoteBackupSchedule, related_name='space_collection_tasks', null=True,
                                        on_delete=models.PROTECT)
    # 开始时间
    start_datetime = models.DateTimeField(auto_now_add=True)
    # 完成时间
    finish_datetime = models.DateTimeField(null=True, blank=True, default=None)
    # 扩展信息
    ext_info = models.TextField(default='{}')

    def set_finished(self):
        self.finish_datetime = timezone.now()
        self.save(update_fields=['finish_datetime'])

    @property
    def name(self):
        return '空间回收-{}'.format(self.get_type_display())


# 外部存储服务器连接
class ExternalStorageDeviceConnection(models.Model):
    # ip
    ip = models.GenericIPAddressField()
    # port
    port = models.IntegerField()
    # iqn
    last_iqn = models.CharField(max_length=256, default='')
    # 连接参数
    params = models.TextField(default={})
    # deleted
    deleted = models.BooleanField(default=False)
    # 最后活跃时间
    last_available_datetime = models.DateTimeField()

    def update_last_available_datetime(self):
        self.last_available_datetime = timezone.now()
        self.save(update_fields=['last_available_datetime'])

    def set_deleted(self, deleted=True):
        self.deleted = deleted
        self.save(update_fields=['deleted'])

    def update_iqn_and_params(self, iqn, params):
        self.deleted = False
        self.last_iqn = iqn
        self.params = json.dumps(params, ensure_ascii=False)
        self.last_available_datetime = timezone.now()
        self.save(update_fields=['deleted', 'last_iqn', 'params', 'last_available_datetime'])


# 存储节点信息
class StorageNode(models.Model):
    # 名称
    name = models.CharField(max_length=256, unique=True, blank=False)
    # 挂载路径
    path = models.CharField(max_length=256, unique=True, blank=False)
    # 配置信息
    config = models.TextField(default='{}')
    # 是否被删除
    deleted = models.BooleanField(default=False)
    # 是否可用
    available = models.BooleanField(default=False)
    # 唯一标识
    ident = models.CharField(max_length=256, unique=True, blank=False)
    # 是否是内部存储节点
    internal = models.BooleanField(default=True)

    def delete_and_rename(self):
        if not self.deleted:
            self.deleted = True
            self.name = r'{}+{}'.format(self.name, uuid.uuid4().hex)
            self.path = r'{}-{}'.format(self.path, uuid.uuid4().hex)
            self.ident = r'{}_{}'.format(self.ident, uuid.uuid4().hex)
            self.available = False
            self.save(update_fields=['deleted', 'name', 'path', 'ident', 'available'])

    def set_name(self, name):
        try:
            self.name = name
            self.save(update_fields=['name'])
            return True
        except IntegrityError:
            return False


class HostSnapshotShare(models.Model):
    # 登录用户名
    login_user = models.CharField(max_length=32)
    # 共享用户名
    samba_user = models.CharField(max_length=32)
    # 共享用户密码
    samba_pwd = models.CharField(max_length=32)
    # 共享url
    samba_url = models.CharField(default='', max_length=512)
    # 共享状态
    share_status = models.TextField(default='')
    # 共享开始时间
    share_start_time = models.DateTimeField(blank=True, null=True, default=None)
    # 主机名称
    host_display_name = models.CharField(default='', max_length=256)
    # 备份点类型(cdp or normal)
    host_snapshot_type = models.CharField(default='', max_length=32)
    # 备份开始时间
    host_start_time = models.CharField(default='', max_length=32)
    # 备份结束时间
    host_finish_time = models.DateTimeField(blank=True, null=True, default=None)
    # host快照id
    host_snapshot_id = models.IntegerField(default=0)
    # difinfo
    dirinfo = models.CharField(default='', max_length=512, unique=True)
    # locked files
    locked_files = models.TextField(default='')
    # 拓展信息
    ext_info = models.TextField(default='{}')

    class Meta:
        unique_together = (("host_start_time", "host_snapshot_id"),)

    @property
    def name(self):
        return '文件验证-{}-{}'.format(self.host_display_name, self.host_start_time)

    @property
    def backup_datetime(self):
        return (self.host_start_time if type(self.host_start_time) == datetime.datetime
                else xdatetime.string2datetime(self.host_start_time)) if self.host_start_time else None

    @property
    def host_ident(self):
        snapshot_obj = HostSnapshot.objects.filter(id=self.host_snapshot_id).first()
        if snapshot_obj:
            if snapshot_obj.host_id:
                return snapshot_obj.host.ident
        return None

    @property
    def snapshot_id(self):
        return self.host_snapshot_id


class UserQuota(models.Model):
    # 是否被删除
    deleted = models.BooleanField(default=False)
    # 从属的节点
    storage_node = models.ForeignKey(StorageNode, related_name='userquotas', on_delete=models.PROTECT)
    # 从属于的User
    user = models.ForeignKey(User, related_name='userquotas', on_delete=models.PROTECT)
    # 配额大小(MB)
    quota_size = models.BigIntegerField()
    # 警告大小(MB)
    caution_size = models.BigIntegerField()
    # 可用大小(MB)：min(节点剩余, 配额剩余), 不可信
    available_size = models.BigIntegerField()
    # 拓展信息
    ext_info = models.TextField(default='{}')

    # 删掉该记录
    def set_deleted(self):
        self.deleted = True
        self.save(update_fields=['deleted'])

    @property
    def storage_node_ident(self):
        return self.storage_node.ident

    @property
    def user_account(self):
        return self.user.username

    @property
    def quota_mega_bytes(self):
        return self.quota_size

    @property
    def caution_mega_bytes(self):
        return self.caution_size

    @property
    def available_mega_bytes(self):
        return self.available_size


class Tunnel(models.Model):
    # 名称
    name = models.CharField(max_length=256, default='')
    # 客户端公网ip
    host_ip = models.GenericIPAddressField()
    # 客户端公网port
    host_port = models.IntegerField()
    # 从属于User, 允许为None
    user = models.ForeignKey(User, related_name='host_tunnels', on_delete=models.PROTECT, null=True)
    # 对应的host
    host = models.OneToOneField(Host, related_name='tunnel', on_delete=models.PROTECT, blank=True, null=True,
                                default=None)
    # 创建时间
    create_datetime = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = (("host_ip", "host_port"),)

    @property
    def get_user(self):
        if self.host is None:
            return self.user
        elif self.host.user_id:
            return self.host.user
        return None

    def set_host(self, host):
        self.host = host
        self.user = None
        self.save(update_fields=['host', 'user'])

    def set_name(self, name):
        self.name = name
        self.save(update_fields=['name'])


class CompressTask(models.Model):
    # 任务创建时间
    create_datetime = models.DateTimeField(auto_now_add=True)
    # 磁盘快照
    disk_snapshot = models.ForeignKey(DiskSnapshot, related_name='compress_tasks')
    # 是否完成
    completed = models.NullBooleanField(default=False)
    # 位图总行数
    total_lines = models.PositiveIntegerField(default=0)
    # 下一次开始的行号
    next_start_lines = models.PositiveIntegerField(default=0)
    # 下一次开始的时间
    next_start_date = models.DateTimeField(auto_now_add=True)
    # 扩展信息
    exe_info = models.TextField(default='{}')

    @property
    def is_deleted(self):
        return self.deleted

    def set_deleted(self):
        self.deleted = True
        self.save(update_fields=['deleted'])


class HTBSchedule(models.Model):
    OLD_POINT_NOT_NEED_UPDATE = 0
    NEW_POINT_NEED_UPDATE = 1

    TASK_TYPE = (
        (OLD_POINT_NOT_NEED_UPDATE, '还原到特定点'),
        (NEW_POINT_NEED_UPDATE, '还原到最新')
    )

    HTB_RESTORE_TYPE_SYSTEM = 1
    HTB_RESTORE_TYPE_VOLUME = 2
    HTB_RESTORE_TYPE = (
        (HTB_RESTORE_TYPE_SYSTEM, '系统还原'),
        (HTB_RESTORE_TYPE_VOLUME, '卷还原')
    )

    # 计划类型
    task_type = models.PositiveSmallIntegerField(choices=TASK_TYPE, default=OLD_POINT_NOT_NEED_UPDATE)
    # 目标主机MAC信息,用于存放pe的mac信息
    target_info = models.TextField(default='[]')
    # 目标主机
    dst_host_ident = models.CharField(default='', max_length=32)
    # 还原类型
    restore_type = models.PositiveSmallIntegerField(choices=HTB_RESTORE_TYPE, default=HTB_RESTORE_TYPE_SYSTEM)
    # 计划名称，用户可自定义
    name = models.CharField(max_length=256)
    # 关联的主机
    host = models.ForeignKey(Host, related_name='htb_schedule')
    # 启用/禁用
    enabled = models.BooleanField(default=True)
    # 删除
    deleted = models.BooleanField(default=False)
    # 扩展信息
    ext_config = models.TextField(default='{}')
    # 是否进入stand_by
    in_stand_by = models.BooleanField(default=False)
    # 进入stand_by任务的uuid
    task_uuid = models.CharField(max_length=32, default='')

    def set_stand_by(self, task_uuid):
        self.task_uuid = task_uuid
        self.in_stand_by = True
        self.save(update_fields=['task_uuid', 'in_stand_by'])
        return self

    def cancel_stand_by(self):
        self.in_stand_by = False
        self.save(update_fields=['in_stand_by'])
        return self

    def enabled_schedule(self, status=True):
        self.enabled = status
        self.save(update_fields=['enabled'])
        return self

    @staticmethod
    def update_config(schedule_id, mission_list):
        schedule = HTBSchedule.objects.get(id=schedule_id)
        ext_config = json.loads(schedule.ext_config)
        for k, v in mission_list:
            ext_config[k] = v
        schedule.ext_config = json.dumps(ext_config)
        schedule.save(update_fields=['ext_config'])


class HTBTask(models.Model):
    INIT = 0
    INITSYS = 1
    SYNC = 2
    SENDCMD = 3
    WAITECMPLTE = 4
    MISFAIL = 5
    MISSUC = 6
    GETDISKDATA = 7
    SWITCH_IP = 8
    TRANSCRIPT = 9
    PREPARE_SWITCH_IP = 10
    WAITETRANSEND = 11
    TRANSDRIVERS = 12
    STOP_SERVICE = 13
    VOL_INITSYS = 14
    VOL_SYNC = 15
    VOL_WAITEINIT = 16

    TASK_STATUS = (
        (INIT, '开始热备任务'),
        (INITSYS, '初始化系统'),
        (SYNC, '构建备机操作系统成功, 同步剩余数据'),
        (SENDCMD, '发送切换命令'),
        (WAITECMPLTE, '等待客户端完成登录'),
        (MISFAIL, '任务失败'),
        (MISSUC, '任务成功'),
        (GETDISKDATA, '获取驱动数据'),
        (PREPARE_SWITCH_IP, '初始化动态IP'),
        (SWITCH_IP, '切换IP'),
        (TRANSDRIVERS, '传输驱动数据'),
        (WAITETRANSEND, '等待数据传输完毕'),
        (TRANSCRIPT, '执行服务启动脚本'),
        (STOP_SERVICE, '执行服务停止脚本'),
        (VOL_INITSYS, '发送卷热备命令'),
        (VOL_SYNC, '发送热备命令成功，同步剩余数据'),
        (VOL_WAITEINIT, '等待卷完成初始化'),
    )

    # 关联的热备计划
    schedule = models.ForeignKey(HTBSchedule, related_name='htb_task')
    # 热备任务的 uuid
    task_uuid = models.CharField(max_length=32)
    # 开始时间
    start_datetime = models.DateTimeField(null=True, blank=True, default=None)
    # 完成时间
    finish_datetime = models.DateTimeField(null=True, blank=True, default=None)
    # 是否成功
    successful = models.BooleanField(default=False)
    # task_uuid
    running_task = models.TextField(default='{}')
    # 任务状态
    status = models.PositiveSmallIntegerField(choices=TASK_STATUS, default=INIT)
    # 扩展信息
    ext_config = models.TextField(default='{}')
    # 开始切换指令
    start_switch = models.BooleanField(default=False)
    # 切换时间
    switch_time = models.DateTimeField(null=True, blank=True, default=None)
    # 关联的PE
    restore_target = models.ForeignKey(RestoreTarget, related_name='htb_task', null=True, blank=True, default=None)

    def __str__(self):
        return 'HTBTask_{}'.format(self.id)

    def set_status(self, status, debug=''):
        self.status = status
        self.save(update_fields=['status'])
        self._create_log(debug)
        return self

    def _create_log(self, debug):
        reason = {'htb_task': self.id, 'debug': debug, "description": self.get_status_display()}
        HostLog.objects.create(host=self.schedule.host, type=HostLog.LOG_HTB,
                               reason=json.dumps(reason, ensure_ascii=False))

    @property
    def name(self):
        try:
            host_ip = self.schedule.host.last_ip
            restore_target = self.restore_target
            pe_ip = json.loads(restore_target.info)['remote_ip'] if restore_target else '--'
        except Exception:
            return '热备-{}'.format(self.id)
        else:
            return '热备-{}到{}'.format(host_ip, pe_ip)

    @property
    def user_account(self):
        if self.schedule.host.user_id:
            return self.schedule.host.user.username
        return None


class HTBSendTask(models.Model):
    QEMU_WORK = 0
    CLOSED_CDP_WORK = 1
    NOT_CLOSED_CDP_WORK = 2

    TASK_TYPE = (
        (QEMU_WORK, '普通快照文件'),
        (CLOSED_CDP_WORK, '封闭的CDP'),
        (NOT_CLOSED_CDP_WORK, '没有封闭的CDP')
    )

    """
    snapshots:[
            {
                path:""
                snapshot:""
            },
            {
                path:""
                snapshot:""
            }
        ]
    """
    # 热备任务
    htb_task = models.ForeignKey(HTBTask, related_name='send_task', on_delete=models.PROTECT)
    # token
    disk_token = models.CharField(max_length=32)
    # native_guid
    native_guid = models.CharField(max_length=50)
    # task_type
    task_type = models.PositiveSmallIntegerField(choices=TASK_TYPE)
    # snap
    snapshots = models.TextField(default='[]')
    # 是否完成
    o_completed_trans = models.BooleanField(default=False)
    # bit_map
    o_bit_map = models.CharField(default='', max_length=255)
    # stop_time
    o_stop_time = models.CharField(default='', max_length=255)
    # ex_vols
    ex_vols = models.TextField(default='[]')

    def __str__(self):
        return "id:{},type:{},o_completed_trans:{},o_bit_map:{},o_stop_time:{}".format(self.id,
                                                                                       self.get_task_type_display(),
                                                                                       self.o_completed_trans,
                                                                                       self.o_bit_map,
                                                                                       self.o_stop_time)


class TakeOverKVM(models.Model):
    name = models.CharField(max_length=256, default='')
    kvm_type = models.CharField(max_length=20, default='')
    host_snapshot = models.ForeignKey(HostSnapshot, related_name='takeover_host_snapshot', on_delete=models.PROTECT,
                                      null=True)
    snapshot_time = models.DateTimeField(blank=True, null=True)
    # 虚拟插槽数（十位） 每个插槽的核数（个位）例如：12 表示1个虚拟插槽 每个插槽2核
    kvm_cpu_count = models.IntegerField(default=0)
    kvm_memory_size = models.IntegerField(default=0)
    kvm_memory_unit = models.CharField(max_length=5, default='')
    kvm_run_start_time = models.DateTimeField(blank=True, null=True)
    kvm_flag_file = models.CharField(max_length=256, default='')
    # 硬件、网卡、路由信息
    ext_info = models.TextField(default='[]')

    @property
    def snapshot_id(self):
        return self.host_snapshot_id

    @property
    def host_ident(self):
        if self.host_snapshot_id:
            if self.host_snapshot.host_id:
                return self.host_snapshot.host.ident
        return None

    @property
    def backup_datetime(self):
        return (self.snapshot_time if type(self.snapshot_time) == datetime.datetime
                else xdatetime.string2datetime(self.snapshot_time)) if self.snapshot_time else None


class Qcow2ReorganizeTask(models.Model):
    SLICE_QCOW = 0
    REORGANIZE_QCOW = 1

    TASK_TYPE = (
        (SLICE_QCOW, '分片'),
        (REORGANIZE_QCOW, '整理'),
    )
    # 任务创建时间
    create_datetime = models.DateTimeField(auto_now_add=True)
    # 开始时间
    start_datetime = models.DateTimeField(null=True, blank=True, default=None)
    # 结束时间
    finish_datetime = models.DateTimeField(null=True, blank=True, default=None)
    # 是否成功
    successful = models.BooleanField(default=False)
    # 快照路径
    image_path = models.CharField(max_length=256)
    # 任务种类
    task_type = models.PositiveIntegerField(choices=TASK_TYPE)
    # 扩展信息
    ext_info = models.TextField(default='{}')
    # 下次执行时间, 任务被中断
    next_start_date = models.DateTimeField(auto_now_add=True)


class HostRebuildRecord(models.Model):
    # 重建目标机数量控制
    host_ident = models.CharField(max_length=32, unique=True)
    mac_list = models.TextField(default='[]')
    rebuild_count = models.IntegerField(default=0)


# vcenter 的连接信息
class VirtualCenterConnection(models.Model):
    user = models.ForeignKey(User, related_name='vcenters')
    username = models.CharField(max_length=256, default='')
    password = models.CharField(max_length=256, default='')
    address = models.CharField(max_length=256, default='')
    port = models.IntegerField(default=443)
    disable_ssl = models.BooleanField(max_length=256, default=True)
    ext_config = models.TextField(default='{}')

    @property
    def name(self):
        return '{}|{}'.format(self.address, self.username)


# 每一个虚拟机维持一个单独的进程
class VirtualMachineSession(models.Model):
    # 虚拟机的唯一标识 'vcenter address|moid'
    ident = models.CharField(max_length=256, unique=True)
    name = models.CharField(max_length=256, default='')
    connection = models.ForeignKey(VirtualCenterConnection, related_name='vm_clients', on_delete=models.PROTECT)
    host = models.OneToOneField(Host, related_name='vm_session', blank=True, null=True, default=None)
    enable = models.BooleanField(default=True)
    home_path = models.CharField(max_length=256, default='')

    @property
    def moid(self):
        return self.ident.split('|')[-1]

    @staticmethod
    def g_ident(connection, moid):
        return '{}|{}'.format(connection.address, moid)

    def __str__(self):
        return 'VirtualMachineSession {}|{}|{}'.format(self.id, self.name, self.ident)


class VirtualMachineRestoreTask(models.Model):
    INIT = 0
    CREATE_VIRTUAL_MACHINE = 1
    MOUNT_NBD = 2
    TRANSFER_DATA = 3
    MISSION_SUCCESSFUL = 4
    MISSION_FAIL = 5
    FIND_SNAPSHOTS = 6
    LOCK_SNAPSHOTS = 7

    TASK_STATUS = [
        (INIT, '初始化参数'),
        (FIND_SNAPSHOTS, '查询快照文件'),
        (LOCK_SNAPSHOTS, '锁定快照文件'),
        (CREATE_VIRTUAL_MACHINE, '构建还原目标机'),
        (MOUNT_NBD, '挂载虚拟磁盘'),
        (TRANSFER_DATA, '传输数据'),
        (MISSION_SUCCESSFUL, '还原任务成功'),
        (MISSION_FAIL, '还原任务失败'),
    ]

    host_snapshot = models.ForeignKey(HostSnapshot, related_name='vmr_tasks')
    # 任务的 uuid
    task_uuid = models.CharField(max_length=32)
    # 开始时间
    start_datetime = models.DateTimeField(null=True, blank=True, default=None)
    # 完成时间
    finish_datetime = models.DateTimeField(null=True, blank=True, default=None)
    # 是否成功
    successful = models.BooleanField(default=False)
    # task_uuid
    running_task = models.TextField(default='{}')
    # 任务状态
    status = models.PositiveSmallIntegerField(choices=TASK_STATUS, default=FIND_SNAPSHOTS)
    # 扩展信息
    ext_config = models.TextField(default='{}')

    def set_status(self, status, debug=''):
        self.status = status
        self.save(update_fields=['status'])
        self._create_log(debug)
        return self

    def _create_log(self, debug):
        reason = {'vmr_task': self.id, 'debug': debug, "description": self.get_status_display()}
        HostLog.objects.create(host=self.host_snapshot.host, type=HostLog.LOG_VMWARE_RESTORE,
                               reason=json.dumps(reason, ensure_ascii=False))

    @property
    def name(self):
        '免代理恢复-{}'.format(self.host_snapshot.name)


class ArchiveSchedule(models.Model):
    """
    归档计划
    """
    # 关联主机
    host = models.ForeignKey(Host, related_name='archive_schedules')
    # 计划名称
    name = models.CharField(max_length=256)
    # 删除（不会在界面显示）
    deleted = models.BooleanField(blank=True, default=False)
    # 启用/禁用
    enabled = models.BooleanField(default=True)
    # 计划开始时间
    plan_start_date = models.DateTimeField(blank=True, null=True, default=None)
    # 计划创建时间
    created = models.DateTimeField(auto_now_add=True)
    # 上次执行时间
    last_run_date = models.DateTimeField(blank=True, null=True, default=None)
    # 下次执行时间
    next_run_date = models.DateTimeField(blank=True, null=True, default=None)
    # 扩展信息
    ext_config = models.TextField(default='')
    # 计划周期类型
    cycle_type = models.IntegerField(choices=BackupTaskSchedule.CYCLE_CHOICES)
    # 为该计划指派一个存储节点
    storage_node_ident = models.CharField(max_length=256, blank=False)

    @property
    def cycle_type_display(self):
        return self.get_cycle_type_display()

    def delete_and_collection_space_later(self):
        self.deleted = True
        self.enabled = True
        self.save(update_fields=['deleted', 'enabled'])


class TaskCommon(models.Model):
    # 开始时间
    start_datetime = models.DateTimeField(null=True, blank=True, default=None)
    # 完成时间
    finish_datetime = models.DateTimeField(null=True, blank=True, default=None)
    # 是否成功
    successful = models.BooleanField(default=False)
    # 扩展信息
    ext_config = models.TextField(default='{}')

    class Meta:
        abstract = True


class ArchiveTask(TaskCommon):
    """
    导出介质信息，存储在ext_config
    """
    INIT = 0
    GENERATE_HASH = 1
    TRANSFER_DATA = 2
    MISSION_SUCCESSFUL = 3
    MISSION_FAIL = 4
    FIND_SNAPSHOTS = 5
    LOCK_SNAPSHOTS = 6
    PACK_DATABASE = 7
    GENERATE_BITMAP = 8

    TASK_STATUS = [
        (INIT, '初始化参数'),
        (FIND_SNAPSHOTS, '查询快照文件'),
        (LOCK_SNAPSHOTS, '锁定快照文件'),
        (GENERATE_HASH, '生成去重数据'),
        (TRANSFER_DATA, '传输数据'),
        (MISSION_SUCCESSFUL, '任务成功'),
        (MISSION_FAIL, '任务失败'),
        (PACK_DATABASE, '生成关键数据信息'),
        (GENERATE_BITMAP, '生成位图信息')
    ]

    # 手工执行的任务没有计划字段
    schedule = models.ForeignKey(ArchiveSchedule, related_name='archive_tasks', null=True)
    # 任务的 uuid
    task_uuid = models.CharField(max_length=32)
    # task_uuid
    running_task = models.TextField(default='{}')
    # 任务状态
    status = models.PositiveSmallIntegerField(choices=TASK_STATUS, default=INIT)
    # 主机快照
    host_snapshot = models.ForeignKey(HostSnapshot, related_name='archive_tasks')
    # 快照点时间（cdp, 快照点需要填写此字段）
    snapshot_datetime = models.DateTimeField(null=True, blank=True, default=None)
    # force_full
    force_full = models.BooleanField(default=False)

    @property
    def name(self):
        return '备份数据导出-{}'.format(self.host_snapshot_name)

    @property
    def host_snapshot_name(self):
        if self.snapshot_datetime:
            datetime_str = self.snapshot_datetime.strftime(
                xdatetime.FORMAT_WITH_MICROSECOND_2)
        else:
            datetime_str = self.host_snapshot.start_datetime.strftime(
                xdatetime.FORMAT_WITH_MICROSECOND_2)
        return '整机备份 {}'.format(datetime_str)

    def set_status(self, status, debug='', create_log=True):
        self.status = status
        self.save(update_fields=['status'])
        if create_log:
            self.create_log(self.get_status_display(), debug)
        return self

    def create_log(self, description, debug):
        reason = {'arch_task': self.id, 'debug': debug, "description": description}
        HostLog.objects.create(host=self.host_snapshot.host, type=HostLog.LOG_ARCHIVE_EXPORT,
                               reason=json.dumps(reason, ensure_ascii=False))

    def __str__(self):
        return '<ArchiveTask:{},{}> <HostSnapshot:{},{}> ' \
               'snapshot_datetime:{} force_full:{}'.format(self.id,
                                                           self.task_uuid,
                                                           self.host_snapshot.id,
                                                           self.host_snapshot.name,
                                                           self.snapshot_datetime,
                                                           self.force_full)


class ArchiveSubTask(TaskCommon):
    # 主任务
    main_task = models.ForeignKey(ArchiveTask, related_name='sub_tasks')
    # 磁盘快照
    disk_snapshot = models.ForeignKey(DiskSnapshot)
    # 磁盘GUID
    native_guid = models.CharField(max_length=256)
    # 磁盘快照点全量hash路径
    hash_path = models.CharField(max_length=256)
    # 唯一标识
    ident = models.CharField(max_length=32, blank=True, null=True)
    # cdp点时间信息
    date_time = models.DateTimeField(null=True, blank=True, default=None)


class ImportSource(models.Model):
    LOCAL_TASK = 1
    SRC_TYPE = [
        (LOCAL_TASK, '本地任务')
    ]
    # 导入源的类型
    src_type = models.IntegerField(choices=SRC_TYPE, default=LOCAL_TASK)
    # local task uuid
    local_task_uuid = models.CharField(max_length=32, blank=True, null=True)
    # 扩展信息
    ext_config = models.TextField(default='{}')


class ImportSnapshotTask(TaskCommon):
    INIT = 0
    TRANSFER_WAIT = 1
    TRANSFER_DATA = 2
    MISSION_SUCCESSFUL = 3
    MISSION_FAIL = 4

    TASK_STATUS = [
        (INIT, '获取关键数据'),
        (TRANSFER_WAIT, '正在排队'),
        (TRANSFER_DATA, '传输数据'),
        (MISSION_SUCCESSFUL, '任务成功'),
        (MISSION_FAIL, '任务失败'),
    ]

    # 任务状态
    status = models.PositiveSmallIntegerField(choices=TASK_STATUS, default=INIT)
    # 任务的 uuid
    task_uuid = models.CharField(max_length=32)
    # task_uuid
    running_task = models.TextField(default='{}')
    # 主机快照
    host_snapshot = models.ForeignKey(HostSnapshot, related_name='import_tasks', null=True)
    # 导入源
    source = models.ForeignKey(ImportSource, null=True, blank=True)

    def set_status(self, status, debug=''):
        self.status = status
        self.save(update_fields=['status'])
        self.create_log(self.get_status_display(), debug)
        return self

    def set_init_status(self, host, status, debug=''):
        self.status = status
        self.save(update_fields=['status'])
        self.create_init_log(host, self.get_status_display(), debug)
        return self

    def create_init_log(self, host, description, debug):
        reason = {'arch_task': self.id, 'debug': debug, "description": description}
        HostLog.objects.create(host=host, type=HostLog.LOG_ARCHIVE_IMPORT,
                               reason=json.dumps(reason, ensure_ascii=False))

    def create_log(self, description, debug):
        reason = {'arch_task': self.id, 'debug': debug, "description": description}
        HostLog.objects.create(host=self.host_snapshot.host, type=HostLog.LOG_ARCHIVE_IMPORT,
                               reason=json.dumps(reason, ensure_ascii=False))


class ImportSnapshotSubTask(TaskCommon):
    # 主任务
    main_task = models.ForeignKey(ImportSnapshotTask, related_name='sub_tasks')
    # 磁盘快照
    disk_snapshot = models.ForeignKey(DiskSnapshot)
    # 记录远端快照点ident
    remote_disk_snapshot = models.CharField(max_length=32)


class VolumePool(models.Model):
    CYCLE_PERDAY = 1
    CYCLE_PERWEEK = 2
    CYCLE_PERMONTH = 3
    CYCLE_CHOICES = (
        (CYCLE_PERDAY, '天'),
        (CYCLE_PERWEEK, '周'),
        (CYCLE_PERMONTH, '月'),
    )
    # 卷池名称
    name = models.CharField(max_length=256, unique=True)
    # 驱动器
    driver = models.CharField(max_length=256)
    # 周期
    cycle = models.IntegerField(default=0)
    # 周期类型
    cycle_type = models.IntegerField(choices=CYCLE_CHOICES)
    # 磁带
    tapas = models.TextField(default='{}')
    # uuid
    pool_uuid = models.CharField(max_length=32, default='1')

    @property
    def max_days(self):
        if self.cycle_type == VolumePool.CYCLE_PERDAY:
            unit = 1
        elif self.cycle_type == VolumePool.CYCLE_PERWEEK:
            unit = 7
        elif self.cycle_type == VolumePool.CYCLE_PERMONTH:
            unit = 30
        else:
            unit = 1
        return self.cycle * unit


class EnumLink(models.Model):
    # 驱动器
    driver = models.CharField(max_length=256)
    # 带库
    library = models.CharField(max_length=256)
    # drv_ID
    drvid = models.IntegerField()


class UserVolumePoolQuota(models.Model):
    # 是否被删除
    deleted = models.BooleanField(default=False)
    # 从属的节点
    volume_pool_node = models.ForeignKey(VolumePool, related_name='uservolumepoolquotas', on_delete=models.PROTECT)
    # 从属于的User
    user = models.ForeignKey(User, related_name='uservolumepoolquotas', on_delete=models.PROTECT)
    # 配额大小(MB)
    quota_size = models.BigIntegerField()
    # 警告大小(MB)
    caution_size = models.BigIntegerField()
    # 可用大小(MB)：min(节点剩余, 配额剩余), 不可信
    available_size = models.BigIntegerField()
    # 拓展信息
    ext_info = models.TextField(default='{}')


class FileBackupTask(TaskCommon):
    REASON_UNKNOWN = 0
    REASON_PLAN_AUTO = 1
    REASON_PLAN_MANUAL = 2

    REASON_CHOICES = (
        (REASON_UNKNOWN, '未知原因'),
        (REASON_PLAN_AUTO, '自动执行'),
        (REASON_PLAN_MANUAL, '手动执行'),
    )

    INIT = 0
    FIND_SNAPSHOTS = 1
    LOCK_SNAPSHOTS = 2
    SEND_BACKUP_COMMAND = 3
    TRANSFER_DATA = 4
    INITIALIZE_THE_BACKUP_AGENT = 5
    MISSION_SUCCESSFUL = 6
    MISSION_FAIL = 7
    BACKUP_MODE = 8

    TASK_STATUS = [
        (INIT, '初始化参数'),
        (FIND_SNAPSHOTS, '查询快照文件'),
        (LOCK_SNAPSHOTS, '锁定快照文件'),
        (SEND_BACKUP_COMMAND, '发送备份指令'),
        (INITIALIZE_THE_BACKUP_AGENT, '初始化备份代理'),
        (TRANSFER_DATA, '传输数据'),
        (MISSION_SUCCESSFUL, '任务成功'),
        (MISSION_FAIL, '任务失败'),
        (BACKUP_MODE, '本次备份模式：')
    ]

    schedule = models.ForeignKey(BackupTaskSchedule, related_name='archive_tasks', null=True)
    # 任务的 uuid
    task_uuid = models.CharField(max_length=32)
    # task_uuid
    running_task = models.TextField(default='{}')
    # 任务状态
    status = models.PositiveSmallIntegerField(choices=TASK_STATUS, default=INIT)
    # force_full
    force_full = models.BooleanField(default=False)
    # 快照
    host_snapshot = models.OneToOneField(HostSnapshot, related_name='file_backup_task', on_delete=models.PROTECT,
                                         null=True)
    # 触发原因
    reason = models.PositiveSmallIntegerField(choices=REASON_CHOICES, default=REASON_UNKNOWN)

    @property
    def name(self):
        if self.schedule.host.type == Host.DB_AGENT_KVM:
            return '数据库备份 {} {}'.format(self.start_datetime, self.schedule.host.name)
        else:
            return '文件备份 {} {}'.format(self.start_datetime, self.schedule.host.name)

    def set_host_snapshot(self, host_snapshot_object):
        if self.host_snapshot is not None:
            return
        self.host_snapshot = host_snapshot_object
        self.save(update_fields=['host_snapshot'])

    def set_status(self, status, debug='', create_log=True, more_msg=''):
        self.status = status
        self.save(update_fields=['status'])
        if create_log:
            self.create_log(self.get_status_display() + more_msg, debug)
        return self

    def create_log(self, description, debug):
        reason = {'file_backup_task': self.id, 'debug': debug, "description": description}
        HostLog.objects.create(host=self.schedule.host, type=HostLog.LOG_AGENT_STATUS,
                               reason=json.dumps(reason, ensure_ascii=False))

    def __str__(self):
        return '<FileBackupTask:{},{}> host:{}' \
               'force_full:{}'.format(self.id, self.task_uuid, self.schedule.host.name, self.force_full)


class HostGroup(models.Model):
    user = models.ForeignKey(User, related_name='usergroup', blank=True, null=True)
    name = models.CharField(max_length=256)
    hosts = models.ManyToManyField(Host, related_name='groups', blank=True)


class GroupBackupTaskSchedule(models.Model):
    SCHEDULE_TYPE_BACKUP_TASK = 1
    SCHEDULE_TYPE_CHOICES = (
        (SCHEDULE_TYPE_BACKUP_TASK, '整机备份计划'),
    )
    user = models.ForeignKey(User, related_name='usergroupchedule', blank=True, null=True)
    host_group = models.ForeignKey(HostGroup, related_name='hostgroup', blank=True, null=True)
    name = models.CharField(max_length=256)
    type = models.IntegerField(choices=SCHEDULE_TYPE_CHOICES)
    schedules = models.ManyToManyField(BackupTaskSchedule, related_name='schedules', blank=True)
    enabled = models.BooleanField(blank=True, default=True)


class AutoVerifySchedule(models.Model):
    user = models.ForeignKey(User, related_name='user_AutoVerifySchedule', blank=True, null=True)
    name = models.CharField(max_length=256)
    storage_node_ident = models.CharField(max_length=256, blank=False)
    hosts = models.ManyToManyField(Host, related_name='hosts_AutoVerifySchedule', blank=True)
    host_groups = models.ManyToManyField(HostGroup, related_name='host_group_AutoVerifySchedule', blank=True)
    ext_config = models.TextField(default='{}')
    enabled = models.BooleanField(default=True)
    created = models.DateTimeField(auto_now_add=True)
    # 上次执行时间
    last_run_date = models.DateTimeField(blank=True, null=True, default=None)
    # 下次执行时间
    next_run_date = models.DateTimeField(blank=True, null=True, default=None)
    plan_start_date = models.DateTimeField(blank=True, null=True, default=None)
    cycle_type = models.IntegerField(choices=BackupTaskSchedule.CYCLE_CHOICES)


class DeployTemplate(models.Model):
    name = models.CharField(max_length=256)
    desc = models.TextField(default='')
    host_snapshot = models.ForeignKey(HostSnapshot, related_name='deploy_templates', blank=True, null=True)
    snapshot_datetime = models.DateTimeField(blank=True, null=True)
    create_datetime = models.DateTimeField(auto_now_add=True)
    ext_info = models.TextField(default='{}')


class AutoVerifyTask(models.Model):
    VERIFY_TYPE_QUEUE = 1
    VERIFY_TYPE_ING = 2
    VERIFY_TYPE_END = 3
    VERIFY_TYPE_NO_SNAPSHOT = 4
    VERIFY_TYPE_CHOICES = (
        (VERIFY_TYPE_QUEUE, '等待验证'),
        (VERIFY_TYPE_ING, '正在验证'),
        (VERIFY_TYPE_END, '验证完成'),
        (VERIFY_TYPE_NO_SNAPSHOT, '无备份点'),
    )
    # 格式为normal|12|2019-03-22T14:35:25.783684
    point_id = models.CharField(max_length=256)
    created = models.DateTimeField(auto_now_add=True)
    schedule_name = models.CharField(max_length=256)
    storage_node_ident = models.CharField(max_length=256, blank=False)
    # AutoVerifySchedule ext_config 当时的值
    schedule_ext_config = models.TextField(default='{}')
    verify_type = models.PositiveSmallIntegerField(choices=VERIFY_TYPE_CHOICES, default=VERIFY_TYPE_QUEUE)
    verify_result = models.TextField(default='{}')


class DBBackupKVM(models.Model):
    # 创建时间
    created = models.DateTimeField(auto_now_add=True)
    # 扩展信息
    ext_info = models.TextField(default='{}')
    # 关联的主机
    host = models.OneToOneField(Host, related_name='kvm_info')
    # 物理网卡信息
    mac = models.CharField(max_length=12, blank=True, default='')
    # host_snapshot 依赖的快照点
    host_snapshot = models.OneToOneField(HostSnapshot, related_name='kvm_info', blank=True, null=True)

    @property
    def name(self):
        return self.host.display_name

    def __str__(self):
        return '<DBBackupKVM:{} {}>'.format(self.id, self.host.name)


class KVMBackupTask(TaskCommon):
    INIT = 0
    FIND_SNAPSHOTS = 1
    LOCK_SNAPSHOTS = 2
    SEND_BACKUP_COMMAND = 3
    TRANSFER_DATA = 4
    INITIALIZE_THE_BACKUP_AGENT = 5
    MISSION_SUCCESSFUL = 6
    MISSION_FAIL = 7
    BACKUP_MODE = 8

    TASK_STATUS = [
        (INIT, '初始化参数'),
        (FIND_SNAPSHOTS, '查询快照文件'),
        (LOCK_SNAPSHOTS, '锁定快照文件'),
        (SEND_BACKUP_COMMAND, '发送开机指令'),
        (MISSION_SUCCESSFUL, '任务成功'),
        (MISSION_FAIL, '任务失败'),
        (BACKUP_MODE, '本次备份模式：')
    ]

    KVM_BACKUP4SELF = 'kvm_backup4self'
    KVM_BACKUP4DATABASE = 'kvm_backup4database'

    TASK_TYPE_CHOICES = [
        (KVM_BACKUP4SELF, '虚拟机备份'),
        (KVM_BACKUP4DATABASE, '数据库备份'),
    ]

    task_type = models.CharField(choices=TASK_TYPE_CHOICES, default=KVM_BACKUP4DATABASE, max_length=256)
    # 任务的 uuid
    task_uuid = models.CharField(max_length=32)
    # 任务状态
    status = models.PositiveSmallIntegerField(choices=TASK_STATUS, default=INIT)
    # 快照
    host_snapshot = models.OneToOneField(HostSnapshot, related_name='kvm_backup_task', on_delete=models.PROTECT,
                                         null=True)
    # 关联的kvm
    kvm = models.ForeignKey(DBBackupKVM, related_name='kvm_backup_tasks')
    # 关联的计划
    schedule = models.ForeignKey(BackupTaskSchedule, related_name='kvm_backup_tasks', null=True)

    @property
    def name(self):
        return '{} {} {}'.format(self.get_task_type_display(), self.start_datetime, self.kvm.host.name)

    @property
    def task_key(self):
        return self.task_type + '_' + self.kvm.host.ident

    def set_host_snapshot(self, host_snapshot_object):
        if self.host_snapshot is not None:
            return
        self.host_snapshot = host_snapshot_object
        self.save(update_fields=['host_snapshot'])

    def set_status(self, status, debug='', create_log=True):
        self.status = status
        self.save(update_fields=['status'])
        if create_log:
            self.create_log(self.get_status_display(), debug)
        return self

    def create_log(self, description, debug):
        reason = {'kvm_backup_task': self.id, 'debug': debug, "description": description}
        if self.task_type == KVMBackupTask.KVM_BACKUP4SELF:
            log_type = HostLog.LOG_KVM_BACKUP4SELF
        elif self.task_type == KVMBackupTask.KVM_BACKUP4DATABASE:
            log_type = HostLog.LOG_KVM_BACKUP4DATABASE
        else:
            log_type = HostLog.LOG_AGENT_STATUS

        HostLog.objects.create(host=self.kvm.host, type=log_type,
                               reason=json.dumps(reason, ensure_ascii=False))

    def __str__(self):
        return '<KVMBackupTask:{},{}> host:{}'.format(self.id, self.task_uuid, self.kvm.host.name)


class FileSyncSchedule(models.Model):
    # 使能/禁用
    enabled = models.BooleanField(blank=True, default=True)
    # 删除（不会在界面显示）
    deleted = models.BooleanField(blank=True, default=False)
    # 计划名称，用户可自定义
    name = models.CharField(max_length=256)
    # 计划周期类型
    cycle_type = models.IntegerField(choices=BackupTaskSchedule.CYCLE_CHOICES)
    # 计划创建时间
    created = models.DateTimeField(auto_now_add=True)
    # 计划开始时间
    plan_start_date = models.DateTimeField(blank=True, null=True, default=None)
    # 关联的主机
    host = models.ForeignKey(Host, related_name='file_sync_schedules', on_delete=models.PROTECT, null=True, blank=True)
    # 目标主机
    target_host_ident = models.CharField(max_length=32)
    # 任务扩展配置（JSON格式）
    ext_config = models.TextField(default='')
    # 上次备份时间
    last_run_date = models.DateTimeField(blank=True, null=True, default=None)
    # 下次备份时间
    next_run_date = models.DateTimeField(blank=True, null=True, default=None)

    @property
    def abstract_name(self):
        if self.cycle_type == BackupTaskSchedule.CYCLE_ONCE:
            return '仅备份一次'
        if self.cycle_type == BackupTaskSchedule.CYCLE_CDP:
            return '持续备份'
        if self.cycle_type == BackupTaskSchedule.CYCLE_PERDAY:
            ext_config = json.loads(self.ext_config)
            if ext_config['IntervaBLOCKit'] == 'min':
                return r'每{}分钟备份'.format(ext_config['backupDayInterval'] // 60)
            if ext_config['IntervaBLOCKit'] == 'hour':
                return r'每{}小时备份'.format(ext_config['backupDayInterval'] // 3600)
            if ext_config['IntervaBLOCKit'] == 'day':
                return r'每{}天备份'.format(ext_config['backupDayInterval'] // (24 * 3600))
            else:
                xlogging.raise_and_logging_error('参数错误',
                                                 'abstract_name IntervaBLOCKit is {}'.format(
                                                     ext_config['IntervaBLOCKit']))
        if self.cycle_type == BackupTaskSchedule.CYCLE_PERWEEK:
            ext_config = json.loads(self.ext_config)
            week = ["一", "二", "三", "四", "五", "六", "日"]
            return r'每周{}备份'.format('、'.join([week[w - 1] for w in sorted(ext_config['daysInWeek'])]))
        if self.cycle_type == BackupTaskSchedule.CYCLE_PERMONTH:
            ext_config = json.loads(self.ext_config)
            return r'每月{}日备份'.format('、'.join([str(m) for m in ext_config['daysInMonth']]))
        xlogging.raise_and_logging_error('参数错误', 'abstract_name cycle_type is {}'.format(self.cycle_type))

    @property
    def cycle_type_display(self):
        return self.get_cycle_type_display()


class FileSyncTask(TaskCommon):
    FIND_SNAPSHOTS = 0
    LOCK_SNAPSHOTS = 1
    START_LOCAL_PROXY = 2
    MISSION_SUCCESSFUL = 3
    MISSION_FAIL = 4
    TASK_STATUS = [
        (FIND_SNAPSHOTS, '查询快照文件'),
        (LOCK_SNAPSHOTS, '锁定快照文件'),
        (START_LOCAL_PROXY, '启动本地代理程序'),  # 主业务逻辑完成，剩下状态放置到 ext_config
        (MISSION_SUCCESSFUL, '任务成功'),
        (MISSION_FAIL, '任务失败'),
    ]
    schedule = models.ForeignKey(FileSyncSchedule, related_name='file_sync_tasks', null=True)
    # 任务的 uuid
    task_uuid = models.CharField(max_length=32)
    # 任务状态
    status = models.PositiveSmallIntegerField(choices=TASK_STATUS, default=FIND_SNAPSHOTS)
    # 主机快照
    host_snapshot = models.ForeignKey(HostSnapshot, related_name='file_sync_tasks')
    # 快照点时间（cdp, 快照点需要填写此字段）
    snapshot_datetime = models.DateTimeField(null=True, blank=True, default=None)

    @property
    def name(self):
        return '文件归档-{}'.format(self.host_snapshot.host.name)

    @property
    def host_snapshot_name(self):
        if self.snapshot_datetime:
            datetime_str = self.snapshot_datetime.strftime(
                xdatetime.FORMAT_WITH_MICROSECOND_2)
        else:
            datetime_str = self.host_snapshot.start_datetime.strftime(
                xdatetime.FORMAT_WITH_MICROSECOND_2)
        return '整机备份 {}'.format(datetime_str)

    def set_status(self, status, debug='', create_log=True):
        self.status = status
        self.save(update_fields=['status'])
        if create_log:
            self.create_log(self.get_status_display(), debug)
        return self

    def create_log(self, description, debug):
        reason = {'file_sync_task': self.id, 'debug': debug, "description": description}
        HostLog.objects.create(host=self.host_snapshot.host, type=HostLog.LOG_FILE_SYNC,
                               reason=json.dumps(reason, ensure_ascii=False))
