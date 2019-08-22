import json
import random
import re
import threading
import time
import uuid
from datetime import datetime, timedelta

from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework import status

from apiv1.system_info_helper import get_agent_client_version
from box_dashboard import xlogging, boxService, xdata
from .models import Host, HostMac, BackupTaskSchedule, CDPTask, BackupTask, HostSnapshot, MigrateTask, \
    VirtualMachineSession
from .restore import PeRestore, AgentRestore
from .serializers import BackupTaskScheduleExtConfigSerializer, PeHostSessionRestoreSerializer, \
    PeHostSessionRestoreDiskSerializer, AgentHostSessionRestoreSerializer
from .snapshot import GetDiskSnapshot
from .storage_nodes import UserQuotaTools
from .tasks import BackupTaskWorker, RestoreTaskWorker, CDPTaskWorker, MigrateTaskWithCdpWorker, \
    MigrateTaskWithNormalWorker, RestoreVolumeTaskWorker, FileBackupTaskWorker

_logger = xlogging.getLogger(__name__)


class CreateHostLogicProcessor(object):
    # args：HostCreateSerializer反序列化后的数据
    def __init__(self, args):
        self.host_type = None
        self.macs = list(set(filter(None, self._from_system_info_get_macs(args))))  # 网卡硬件地址 ['00000000000A']
        self.user_ident = args['user_ident']  # user ident string
        self.host_name = args['host_name']  # 主机名称
        self.agent_client_version = self._get_agent_client_version(args)

        if boxService.get_verify_user_fingerprint_switch() and (self._getUser(self.user_ident) is None):
            xlogging.raise_and_logging_error(
                '无效的参数', 'invalid user_ident : {}'.format(self.user_ident), status.HTTP_400_BAD_REQUEST)

        if len(self.macs) == 0:
            xlogging.raise_and_logging_error('无效的参数', 'len(self.macs) == 0', status.HTTP_400_BAD_REQUEST)

    # 返回 host ident
    def run(self):
        g_macs = list(filter(lambda m: m[0] == 'G', self.macs))
        assert len(g_macs) in (0, 1,)
        if len(g_macs) == 0:
            g_mac = None
        else:
            g_mac = g_macs[0]

        if g_mac:
            host_from_db = self._queryHostFromDbByMac(g_mac)
            if host_from_db is None:
                # 可能连接到一台新主机
                g_mac_type = 'not_in_db'
            else:
                if self._isHostAlive(host_from_db.ident):
                    # 可能clone了主机
                    _logger.warning(
                        'g_mac:{}, host_ident:{}, have alive brothers, create new one'.format(
                            g_mac, host_from_db.ident))
                    g_mac_type = 'same_alive'
                elif boxService.get_disable_alter_user_switch() and (not self._is_same_user_with_host(host_from_db)):
                    _logger.warning(
                        'g_mac:{}, host_ident:{}, have not same user, create new one'.format(
                            g_mac, host_from_db.ident))
                    g_mac_type = 'diff_user'
                else:
                    _logger.info(r'find ident {} from db by g_mac {}'.format(host_from_db.ident, g_mac))
                    self._updateExistHostMacsFromDb(host_from_db)
                    self._updateDuplication()
                    return host_from_db.ident
        else:
            g_mac_type = 'no_exist'

        if g_mac_type == 'not_in_db':
            self._updateDuplication()
            host_ident, _ = self._createNewHost()
            return host_ident

        if g_mac_type == 'same_alive' or g_mac_type == 'diff_user':
            self.macs.remove(g_mac)
            self.macs.append(self._create_new_g_mac())
            host_ident, _ = self._createNewHost()
            self._updateDuplication()
            return host_ident

        assert g_mac_type == 'no_exist'

        for mac in self.macs:
            host_from_db = self._existMacsFromDbAndHostNotAlive(mac)
            if host_from_db is not None:
                break
        else:
            host_from_db = None

        if host_from_db is not None:  # 数据库中找到可用的主机识别号
            if not host_from_db.soft_ident:
                self.macs.append(self._create_new_g_mac())
            self._updateExistHostMacsFromDb(host_from_db)
            host_ident = host_from_db.ident
        else:  # 数据库中没有找到可用的主机识别号
            self.macs.append(self._create_new_g_mac())
            host_ident, host = self._createNewHost()
            self._update_session(host)  # 免代理客户端更新主机

        self._updateDuplication()
        return host_ident

    def _is_same_user_with_host(self, host_obj):
        current_user_obj = self._getUser(self.user_ident)
        if current_user_obj and host_obj.user and current_user_obj.id == host_obj.user.id:
            return True
        return False

    @staticmethod
    def _create_new_g_mac():
        while True:
            g_mac = 'G{}'.format(uuid.uuid4().hex[0:11].upper())
            if HostMac.objects.filter(mac=g_mac).count() == 0:
                return g_mac

    def _update_session(self, host):
        if self.session:
            self.session.host = host
            self.session.save(update_fields=['host'])
        else:
            pass

    def _existMacsFromDbAndHostNotAlive(self, mac):
        host_from_db = self._queryHostFromDbByMac(mac)
        if host_from_db is not None:
            if self._isHostAlive(host_from_db.ident):
                if self.agent_client_version < datetime(2018, 7, 5):
                    _logger.warning(
                        '_existMacsFromDbAndHostNotAlive, mac:{}, host_ident:{}, have alive brothers, off line it!'.format(
                            mac, host_from_db.ident))
                    boxService.box_service.forceOfflineAgent(host_from_db.ident)
                else:
                    _logger.warning(
                        r'_existMacsFromDbAndHostNotAlive, mac:{}, host_ident:{}, have alive brothers, find none'.format(
                            mac, host_from_db.ident))
                    return None
            return host_from_db
        else:
            return None

    @staticmethod
    def _queryHostFromDbByIdent(host_ident):
        try:
            return Host.objects.get(ident=host_ident)
        except Host.DoesNotExist:
            _logger.warning('没有找到ident为(' + host_ident + ')的客户端')
        except Host.MultipleObjectsReturned:
            _logger.error('ident为(' + host_ident + ')的客户端有多台？！')
        return None

    @staticmethod
    def _isHostAlive(host_ident):
        return boxService.box_service.isAgentLinked(host_ident)

    @staticmethod
    def _queryHostFromDbByMac(mac):
        try:
            return HostMac.objects.get(mac=mac, duplication=False).host
        except HostMac.DoesNotExist:
            _logger.warning('没有找到mac为(' + mac + ')的客户端')
        except HostMac.MultipleObjectsReturned:
            _logger.warning('mac为(' + mac + ')的客户端有多台？！')
        return None

    def _updateExistHostMacsFromDb(self, host_from_db):
        for mac in self.macs:
            if HostMac.objects.filter(host=host_from_db).filter(mac=mac).count() == 0:
                HostMac.objects.create(host=host_from_db, mac=mac)

    def _gen_display_name(self, host_name):
        tmp_name = host_name
        loop_count = 0
        loop_max = 810000  # 30 ** 4
        while True:
            if loop_count < loop_max and Host.objects.filter(display_name=tmp_name).count() > 0:
                loop_count = loop_count + 1
                tmp_name = '{}({})'.format(host_name, ''.join(random.sample('0123456789ABCDEFGHJKLMNPQRSTUVWXYZ', 4)))
            else:
                return tmp_name

    def _createNewHost(self):
        from xdashboard.handle.serversmgr import ON_createNewHost
        host_ident = uuid.uuid4().hex.lower()
        user = self._getUser(self.user_ident)
        host = Host.objects.create(ident=host_ident, display_name=self._gen_display_name(self.host_name), user=user,
                                   type=self.host_type,
                                   network_transmission_type=boxService.get_default_network_transmission_type())
        for mac in self.macs:
            HostMac.objects.create(host=host, mac=mac)
        if user:
            user_id = user.id
        else:
            user_id = None
        ON_createNewHost(user_id, host_ident)
        return host_ident, host

    @staticmethod
    @xlogging.convert_exception_to_value(None)
    def _getUser(user_name):
        try:
            if user_name == xdata.UUID_VALIADE_HOST:
                return None

            if user_name.startswith('*'):
                user_name_uuid = user_name[1:]
                return User.objects.get(userprofile__user_fingerprint=user_name_uuid)

            if boxService.get_verify_user_fingerprint_switch():
                xlogging.raise_and_logging_error('客户端必须上报用户指纹', r'required user fingerprint')

            return User.objects.get(username=user_name)
        except User.DoesNotExist:
            xlogging.raise_and_logging_error('新客户端属于无效的用户', r'invalid user:({}) when create host'.format(user_name))

    def _updateDuplication(self):
        for mac in self.macs:
            if HostMac.objects.filter(mac=mac).count() > 1:
                assert mac[0] != 'G'
                HostMac.objects.filter(mac=mac).update(duplication=True)

    def _from_system_info_get_macs(self, args):
        system_info = json.loads(args['sysinfo'])
        if args['macs'][0] == xdata.VMCLIENT_FAKE_MAC:  # 免代理客户端
            self.host_type = Host.PROXY_AGENT
            self.session = VirtualMachineSession.objects.get(id=system_info['session_id'])
            macs = self._get_or_generate_virtual_mac(self.session)
            _logger.info('_from_system_info_get_macs work in vmclient, macs:{}|session:{}'.format(macs, self.session))
            return macs
        else:
            self.host_type = Host.AGENT
            self.session = None
            if self.is_linux_host(system_info):
                return [x for x in list(set(args['macs'])) if x != 'empty']
            else:
                rs = list()
                soft_ident = system_info.get('soft_ident', None)
                if soft_ident and soft_ident != 'empty':
                    rs.append(soft_ident)
                for nic_info in system_info['Nic']:
                    Hwids = nic_info.get('HwIds', None)
                    if self._is_support_mac(nic_info):
                        if Hwids is None:
                            rs.append(self._format_mac(nic_info['Mac']))
                        elif Hwids and self.is_valid_adpter(Hwids):
                            rs.append(self._format_mac(nic_info['Mac']))
                if not rs:
                    for nic_info in system_info['Nic']:
                        Hwids = nic_info.get('HwIds', None)
                        if self._is_support_mac(nic_info):
                            if Hwids is None:
                                rs.append(self._format_mac(nic_info['Mac']))
                            elif Hwids and self.is_valid_adpter(Hwids, True):
                                rs.append(self._format_mac(nic_info['Mac']))
                return rs

    @staticmethod
    def _get_or_generate_virtual_mac(session):
        try:
            return [mac_object.mac for mac_object in session.host.macs.all()]
        except:
            return ['z{:x}{}'.format(session.id, uuid.uuid4().hex)[:12].upper()]

    @staticmethod
    def _get_agent_client_version(args):
        system_info_obj = json.loads(args['sysinfo'])
        return get_agent_client_version(system_info_obj)

    @staticmethod
    def _is_support_mac(nic_info):
        not_support_nic_descriptions = ['VMware Virtual Ethernet Adapter for VMnet',
                                        'Microsoft Failover Cluster Virtual Adapter']
        for des in not_support_nic_descriptions:
            if nic_info['Description'].lower().startswith(des.lower()):
                return False
        return True

    @staticmethod
    def is_valid_adpter(HwIds, include_usb=False):
        if HwIds is None:
            return True

        adpterIDList = list()
        adpterIDList.append(r'PCI')  # PCI设备
        adpterIDList.append(r'VMBUS')  # Hyper-V的虚拟设备
        adpterIDList.append(r'B06BDRV')  # 博康的虚拟设备
        adpterIDList.append(r'EBDRV')  # 博康的虚拟设备
        adpterIDList.append(r'QEBDRV')  # 博康的虚拟设备
        adpterIDList.append(r'ROOT\\XENEVTCHN')  # XEN的虚拟设备
        adpterIDList.append(r'XEN')  # XEN的虚拟设备
        adpterIDList.append(r'COMPOSITEBUS')  # 2012 聚合网卡
        adpterIDList.append(r'CQ_CPQTEAMMP')  # HP 聚合网卡
        adpterIDList.append(r'BRCM_BLFM')  # 博康
        adpterIDList.append(r'VMS_MP')  # Hyper-V 虚拟以太网适配器
        if include_usb:
            adpterIDList.append(r'USB')  # USB

        for hwid in HwIds:
            hwid = hwid.upper()
            for hid in adpterIDList:
                p = re.compile(r'{}[\s\S]*'.format(hid))
                if p.match(hwid):
                    return True

        _logger.info('is_valid_adpter return False HwIds={}'.format(json.dumps(HwIds)))
        return False

    @staticmethod
    def _format_mac(mac_str):
        return re.sub(r'-|:', '', mac_str).upper()

    def is_linux_host(self, system_info):
        return 'LINUX' in system_info['System']['SystemCaption'].upper()


class HostSessionLogicProcessor(object):
    def __init__(self):
        self.locker = threading.RLock()
        xlogging.TraceDecorator().decorate()
        xlogging.LockerDecorator(self.locker).decorate()

    def login(self, host_ident, host_ip, local_ip):
        host = Host.objects.get(ident=host_ident)
        if host.is_linked:
            xlogging.raise_and_logging_error('客户端登陆失败，重复的客户端标识符',
                                             'host login failed : {}'.format(host_ident),
                                             status.HTTP_429_TOO_MANY_REQUESTS)
        host.login(host_ip, local_ip)
        return host

    @xlogging.convert_exception_to_value(None)
    def logout(self, host_ident):
        host = Host.objects.get(ident=host_ident)
        if host.is_linked:
            host.logout()

    def clear(self):
        _logger.warning(r'will logout all session ...')
        hosts = Host.objects.filter(login_datetime__isnull=False).all()
        for host in hosts:
            host.logout()

    @xlogging.convert_exception_to_value(None)
    def _send_email(self, host, error):
        from xdashboard.models import Email
        from xdashboard.common.smtp import send_email
        exc_inf = dict()
        exc_inf['user_id'] = host.user.id
        exc_inf['content'] = error.get('description', 'None')
        exc_inf['sub'] = '代理程序初始化失败'
        send_email(Email.DRIVER_ABNORMAL_ALERT, exc_inf)

    def report_agent_module_error_and_logout(self, host_ident, error):
        host = Host.objects.get(ident=host_ident)
        if host.is_linked:
            host.reportAgentModuleError(error)
            self._send_email(host, error)
            host.logout()


class BackupTaskScheduleLogicProcessor(object):
    CYCLE_ARGS = {
        BackupTaskSchedule.CYCLE_CDP: ['backupDataHoldDays', 'autoCleanDataWhenlt', 'maxBroadband', 'cdpDataHoldDays',
                                       'cdpSynchAsynch'],
        BackupTaskSchedule.CYCLE_ONCE: ['backupDataHoldDays', 'autoCleanDataWhenlt', 'maxBroadband'],
        BackupTaskSchedule.CYCLE_PERDAY: ['backupDataHoldDays', 'autoCleanDataWhenlt', 'maxBroadband',
                                          'backupDayInterval'],
        BackupTaskSchedule.CYCLE_PERWEEK: ['backupDataHoldDays', 'autoCleanDataWhenlt', 'maxBroadband', 'daysInWeek'],
        BackupTaskSchedule.CYCLE_PERMONTH: ['backupDataHoldDays', 'autoCleanDataWhenlt', 'maxBroadband', 'daysInMonth']
    }

    # schedule_object：BackupTaskSchedule数据库对象
    def __init__(self, schedule_object):
        self._schedule_object = schedule_object

    def check_ext_config(self):
        ext_config = json.loads(self._schedule_object.ext_config)

        serializer = BackupTaskScheduleExtConfigSerializer(data=ext_config)
        serializer.is_valid(True)

        args = self.CYCLE_ARGS[self._schedule_object.cycle_type]
        for arg in args:
            if (arg not in serializer.validated_data) or (serializer.validated_data[arg] is None):
                xlogging.raise_and_logging_error(
                    '参数错误', 'id:{id} type:{type} not arg:{arg} ext_config:{config}'.format(
                        id=self._schedule_object.id, type=self._schedule_object.cycle_type_display, arg=arg,
                        config=serializer.validated_data))

        self._schedule_object.ext_config = json.dumps(serializer.validated_data, ensure_ascii=False)

    def _calc_next_run_with_min(self, ext_config):
        interval_secs = ext_config['backupDayInterval']
        now_time = datetime.now()
        plan_start_time, plan_last_time = self._schedule_object.plan_start_date, self._schedule_object.last_run_date

        if now_time < plan_start_time:  # 1.开始时间在以后
            return plan_start_time

        if plan_last_time:  # 2.开始时间在以前, 且执行过
            return plan_last_time + timedelta(minutes=interval_secs / 60)

        return now_time  # 3.开始时间在以前, 且未执行过

    # 计算下次执行时间
    #   create_or_update
    #       True 当创建和修改计划时传入
    #       False 当执行计划时传入, 且last_run_date已设置
    def calc_next_run(self, create_or_update):
        if self._schedule_object.cycle_type == BackupTaskSchedule.CYCLE_CDP:
            # CDP 没有下次备份时间
            return None

        if (not create_or_update) and self._schedule_object.next_run_date is not None \
                and self._schedule_object.last_run_date < self._schedule_object.next_run_date:
            # 当执行时间比计划时间更早时（手动执行），不更新计划时间
            # 注意处理逻辑分支：仅备份一次时，有可能next_run_date为None
            return self._schedule_object.next_run_date

        if self._schedule_object.cycle_type == BackupTaskSchedule.CYCLE_ONCE:
            if create_or_update:  # 创建时候 下次执行时间 为 页面选择的时间
                return self._schedule_object.plan_start_date
            else:  # 执行的时候，需要将下次执行时间更新为None
                return None

        ext_config = json.loads(self._schedule_object.ext_config)
        if 'IntervalUnit' not in ext_config:  # IntervalUnit: 单位  backupDayInterval: 总秒数  (兼容数据库处理)
            ext_config['IntervalUnit'] = 'day'
            ext_config['backupDayInterval'] = ext_config['backupDayInterval'] * (24 * 3600)

        if self._schedule_object.cycle_type == BackupTaskSchedule.CYCLE_PERDAY:  # 按间隔时间
            if ext_config['IntervalUnit'] == 'min':
                return self._calc_next_run_with_min(ext_config)
            elif ext_config['IntervalUnit'] == 'hour':
                return self._calc_next_run_with_hour(self._schedule_object.plan_start_date,
                                                     int(ext_config['backupDayInterval'] // 3600))
            elif ext_config['IntervalUnit'] == 'day':
                return self._calc_next_run_with_day(self._schedule_object.plan_start_date,
                                                    int(ext_config['backupDayInterval'] // (24 * 3600)))
            else:
                xlogging.raise_and_logging_error('参数错误', 'IntervalUnit is {}'.format(ext_config['IntervalUnit']))

        if self._schedule_object.cycle_type == BackupTaskSchedule.CYCLE_PERWEEK:
            return self._calc_next_run_with_week(
                self._schedule_object.plan_start_date.time(),
                self._schedule_object.plan_start_date if create_or_update else self._schedule_object.last_run_date,
                ext_config['daysInWeek'])

        if self._schedule_object.cycle_type == BackupTaskSchedule.CYCLE_PERMONTH:
            return self._calc_next_run_with_month(
                self._schedule_object.plan_start_date.time(),
                self._schedule_object.plan_start_date if create_or_update else self._schedule_object.last_run_date,
                ext_config['daysInMonth'])

    def _calc_next_run_with_runonce(self, start_datetime):
        if start_datetime is None or start_datetime < datetime.now():
            return None
        else:
            return self._schedule_object.plan_start_date

    @staticmethod
    def _calc_next_run_with_hour(start_datetime, hours):
        now = datetime.now()
        if now < start_datetime:
            return start_datetime

        hours_between_start_datetime = now - start_datetime
        current_add_hours = ((hours_between_start_datetime.days * 24
                              + hours_between_start_datetime.seconds // 3600) // hours) * hours
        current_datetime = start_datetime + timedelta(days=current_add_hours // 24, hours=current_add_hours % 24)
        if now < current_datetime:
            return current_datetime
        else:
            return current_datetime + timedelta(days=hours // 24, hours=hours % 24)

    @staticmethod
    def _calc_next_run_with_day(start_datetime, days):
        now = datetime.now()
        if now < start_datetime:
            return start_datetime
        start_date = start_datetime.date()
        now_date = now.date()

        days_between_start_datetime = now_date - start_date
        current_add_days = int(days_between_start_datetime.days / days) * days
        current_datetime = start_datetime + timedelta(days=current_add_days)
        if now < current_datetime:
            return current_datetime
        else:
            need_add_days = int((days_between_start_datetime.days + days) / days) * days
            return start_datetime + timedelta(days=need_add_days)

    @staticmethod
    def get_start_date(start_datetime):
        now = datetime.now()
        start_date = start_datetime.date()
        now_date = now.date()
        if now < start_datetime:
            # 如果开始时间在未来，则从开始日期（含）计算
            return start_date
        else:
            if start_date == now_date:
                # 如果是当天，则从当前时间（不含）计算
                return now_date + timedelta(days=1)
            else:
                return now_date

    # daysInWeek ['1','2','3','4','5','6','7']
    def _calc_next_run_with_week(self, start_time, start_datetime, daysInWeek):
        start_date = self.get_start_date(start_datetime)
        following_dates = self.getFollowingDatesFromDate(start_date, 8)
        next_date = self.getFirstPlanDateFromDates(following_dates, daysInWeek, None)
        return datetime.combine(next_date, start_time)

    # 从当前Date开始计算，返回N个连续的Date
    @staticmethod
    def getFollowingDatesFromDate(date, totalDateNum):
        dates = list()
        for i in range(totalDateNum):
            dates.append(date + timedelta(days=i))
        return dates

    # 从N个连续的Date，返回处于计划中的Date
    @staticmethod
    def getFirstPlanDateFromDates(dates, daysInWeek=None, daysInMonth=None):
        if daysInWeek is not None:
            for date in dates:
                if (date.weekday() + 1) in daysInWeek:
                    return date

        if daysInMonth is not None:
            for date in dates:
                if date.day in daysInMonth:
                    return date

        xlogging.raise_and_logging_error(r'内部异常，计算计划下次运行时间失败', r'can NOT get first plan date')

    # daysInMonth
    def _calc_next_run_with_month(self, start_time, start_datetime, daysInMonth):
        start_date = self.get_start_date(start_datetime)
        following_dates = self.getFollowingDatesFromDate(start_date, 64)
        next_date = self.getFirstPlanDateFromDates(following_dates, None, daysInMonth)
        return datetime.combine(next_date, start_time)


class BackupTaskScheduleExecuteLogicProcessor(object):
    # schedule_object : BackupTaskSchedule 数据库对象
    def __init__(self, schedule_object):
        self._schedule_object = schedule_object

    def _check_not_cdp_task_running(self):
        if CDPTask.objects.filter(schedule=self._schedule_object, finish_datetime__isnull=True).first():
            xlogging.raise_and_logging_error('该CDP计划正在执行中', 'cdp task running', status.HTTP_429_TOO_MANY_REQUESTS)

    def _check_not_backup_task_running(self):
        if BackupTask.objects.filter(schedule=self._schedule_object, finish_datetime__isnull=True).first():
            xlogging.raise_and_logging_error('该备份计划正在执行中', 'backup task running', status.HTTP_429_TOO_MANY_REQUESTS)

    def _check_other_task(self):
        if HostSnapshot.objects.filter(start_datetime__isnull=False, finish_datetime__isnull=True,
                                       deleted=False, deleting=False, host=self._schedule_object.host).first():
            xlogging.raise_and_logging_error('该客户端正在执行其他任务中', 'host snapshot running',
                                             status.HTTP_429_TOO_MANY_REQUESTS)

    def _check_not_other_cdp_task_running(self):
        task = CDPTask.objects.filter(schedule__host=self._schedule_object.host, finish_datetime__isnull=True).first()
        if task is not None:
            xlogging.raise_and_logging_error('该客户端的[{}]计划正在执行中'.format(task.schedule.name),
                                             'other cdp task running : '.format(task.__dict__),
                                             status.HTTP_429_TOO_MANY_REQUESTS)

    def _check_not_other_task_running(self):
        task = BackupTask.objects.filter(schedule__host=self._schedule_object.host,
                                         finish_datetime__isnull=True).first()
        if task is not None:
            xlogging.raise_and_logging_error('该客户端的[{}]计划正在执行中'.format(task.schedule.name),
                                             'other backup task running : '.format(task.__dict__),
                                             status.HTTP_429_TOO_MANY_REQUESTS)

    def check_not_running(self):
        if self._schedule_object.cycle_type == BackupTaskSchedule.CYCLE_CDP:
            self._check_not_cdp_task_running()
        else:
            self._check_not_backup_task_running()

        self._check_not_other_cdp_task_running()
        self._check_not_other_task_running()
        self._check_other_task()

    def check_enable_running(self):
        if (not self._schedule_object.enabled) or self._schedule_object.deleted:
            xlogging.raise_and_logging_error('任务无法执行，已被禁用', 'schedule enabled:{} deleted:{}'
                                             .format(self._schedule_object.enabled, self._schedule_object.deleted))

    def check_host_online(self):
        if not self._schedule_object.host.is_linked:
            xlogging.raise_and_logging_error('任务无法执行，主机离线', '{} NOT linked'
                                             .format(self._schedule_object.host))

    def set_last_run_date_and_calc_next_run_date(self):
        self._schedule_object.last_run_date = datetime.now()
        logic = BackupTaskScheduleLogicProcessor(self._schedule_object)
        self._schedule_object.next_run_date = logic.calc_next_run(False)
        self._schedule_object.save(update_fields=['next_run_date', 'last_run_date'])

    def run(self, reason, config):
        if reason == BackupTask.REASON_PLAN_AUTO:
            # 自动运行，先修改最后运行时间
            self.set_last_run_date_and_calc_next_run_date()

        self.check_not_running()
        self.check_enable_running()
        self.check_host_online()
        UserQuotaTools.check_user_storage_size_in_node(self._schedule_object)

        if self._schedule_object.cycle_type == BackupTaskSchedule.CYCLE_CDP:
            worker = CDPTaskWorker(self._schedule_object, config)
        else:
            if self._schedule_object.backup_source_type == BackupTaskSchedule.BACKUP_FILES:
                worker = FileBackupTaskWorker(self._schedule_object, reason, config)
            else:
                worker = BackupTaskWorker(self._schedule_object, reason, config)
        worker.work()

        if reason == BackupTask.REASON_PLAN_MANUAL:
            self.set_last_run_date_and_calc_next_run_date()


# restore_datetime CDP时间
# disks HostSnapshotRestoreDiskSerializer 反序列化数组
# host_snapshot_object 主机快照数据库对象
def _generate_PeHostSessionRestoreDiskSerializer_from_cdp(restore_datetime, disks, host_snapshot_object):
    data = list()
    restore_time = restore_datetime.timestamp()
    for disk in disks:
        if disk['src'] in (xdata.CLW_BOOT_REDIRECT_MBR_UUID, xdata.CLW_BOOT_REDIRECT_GPT_UUID,
                           xdata.CLW_BOOT_REDIRECT_GPT_LINUX_UUID,):
            disk_snapshot_ident = disk['src']
            restore_timestamp = None
            data.append({'disk_index': int(disk['dest']),
                         'disk_snapshot_ident': disk_snapshot_ident,
                         'restore_timestamp': restore_timestamp})
        else:
            disk_snapshot_ident, restore_timestamp = \
                GetDiskSnapshot.query_cdp_disk_snapshot_ident(host_snapshot_object, disk['src'], restore_time)

            if disk_snapshot_ident is None or restore_timestamp is None:
                _logger.warning('no valid cdp disk snapshot use normal snapshot : {} {} {}'.format(
                    host_snapshot_object.id, disk['src'], restore_time))
                disk_snapshot_ident = GetDiskSnapshot.query_normal_disk_snapshot_ident(host_snapshot_object,
                                                                                       disk['src'])

            data.append({'disk_index': int(disk['dest']),
                         'disk_snapshot_ident': disk_snapshot_ident,
                         'restore_timestamp': restore_timestamp})

    serializer = PeHostSessionRestoreDiskSerializer(data=data, many=True)
    serializer.is_valid(True)
    return serializer.validated_data


def _generate_PeHostSessionRestoreDiskSerializer_from_normal(disks, host_snapshot_object):
    data = list()

    for disk in disks:
        if disk['src'] in (xdata.CLW_BOOT_REDIRECT_MBR_UUID, xdata.CLW_BOOT_REDIRECT_GPT_UUID,
                           xdata.CLW_BOOT_REDIRECT_GPT_LINUX_UUID,):
            disk_snapshot_ident = disk['src']
        else:
            disk_snapshot_ident = GetDiskSnapshot.query_normal_disk_snapshot_ident(
                host_snapshot_object, disk['src'])

        data.append({'disk_index': int(disk['dest']),
                     'disk_snapshot_ident': disk_snapshot_ident})

    serializer = PeHostSessionRestoreDiskSerializer(data=data, many=True)
    serializer.is_valid(True)
    return serializer.validated_data


class HostSessionMigrateLogicProcessor(object):
    # data : HostSessionMigrateSerializer 反序列化的字典对象
    # target_host_object : 启动 pe host 的 agent host
    # pe_host_object : 迁移目标 RestoreTarget 数据库对象
    # source_host_object : 迁移源 Host 数据库对象
    def __init__(self, source_host_object, pe_host_object, target_host_object, data):
        self._data = data
        self._target_host_object = target_host_object
        self._pe_host_object = pe_host_object
        self._source_host_object = source_host_object

    def _check_source_host_status(self):
        # 获取host的状态：['off_line','error','idle','backup','restore','cdp_syn','cdp_asy']
        ident = self._source_host_object.ident
        host_status = boxService.box_service.GetStatus(ident)
        _logger.info(r'HostSessionMigrateLogicProcessor _check_source_host_status : {} - {}'.format(ident, host_status))
        if 'off_line' in host_status:
            xlogging.raise_and_logging_error(r'启动迁移失败：源服务器处于离线状态', r'off_line in host_status',
                                             status.HTTP_405_METHOD_NOT_ALLOWED)
        elif 'error' in host_status:
            xlogging.raise_and_logging_error(r'启动迁移失败：源服务器状态异常', r'error in host_status',
                                             status.HTTP_405_METHOD_NOT_ALLOWED)
        elif 'backup' in host_status:
            xlogging.raise_and_logging_error(r'启动迁移失败：源服务器正在执行备份任务', r'backup in host_status',
                                             status.HTTP_405_METHOD_NOT_ALLOWED)
        elif 'v_restore' in host_status:
            xlogging.raise_and_logging_error(r'启动迁移失败：源服务器正在执行卷还原任务', r'v_restore in host_status',
                                             status.HTTP_405_METHOD_NOT_ALLOWED)
        elif 'restore' in host_status:
            xlogging.raise_and_logging_error(r'启动迁移失败：源服务器正在执行还原任务', r'restore in host_status',
                                             status.HTTP_405_METHOD_NOT_ALLOWED)
        elif 'cdp_syn' in host_status or 'cdp_asy' in host_status:
            return MigrateTask.SOURCE_TYPE_CDP
        elif 'idle' in host_status:
            return MigrateTask.SOURCE_TYPE_TEMP_NORMAL
        else:
            xlogging.raise_and_logging_error(r'启动迁移失败：源服务器正在执行任务中', r'never happen ~~~',
                                             status.HTTP_405_METHOD_NOT_ALLOWED)

    @staticmethod
    def _wait_for_backup_data_transfer(host_snapshot_id):
        while True:
            host_snapshot = HostSnapshot.objects.get(id=host_snapshot_id)
            if host_snapshot.deleting or host_snapshot.deleted or \
                    (host_snapshot.finish_datetime is not None and host_snapshot.successful is False):
                xlogging.raise_and_logging_error(r'启动迁移任务失败，无效的CDP快照点',
                                                 r'_wait_for_backup_data_transfer : {}'.format(host_snapshot), 0)
            ext_info = json.loads(host_snapshot.ext_info)
            index = ext_info.get('progressIndex', None)
            total = ext_info.get('progressTotal', None)
            if index and total and index > 0 and total > 0:  # 开始传输备份数据
                _logger.warning(
                    r'backup data transfer begining, when cdp migrate, host_snapshot[{}]'.format(host_snapshot_id))
                break
            elif host_snapshot.finish_datetime and host_snapshot.successful is True:  # 备份完成且成功
                _logger.warning(
                    r'backup data transfer finished and successful, when cdp migrate, host_snapshot[{}]'.format(
                        host_snapshot_id))
                break
            elif host_snapshot.finish_datetime and host_snapshot.successful is False:  # 备份完成且失败
                xlogging.raise_and_logging_error('迁移源备份失败', 'migrate source backup failed; hostsnapshot={0}'.format(
                    host_snapshot_id))
            else:
                _logger.warning(
                    r'waiting for backup data transfer, when cdp migrate, host_snapshot[{}]'.format(host_snapshot_id))
                time.sleep(3)

    def run(self, immediately_run):
        source_type = self._check_source_host_status()
        if source_type == MigrateTask.SOURCE_TYPE_CDP:
            self.migrate_from_cdp(immediately_run)
        elif source_type == MigrateTask.SOURCE_TYPE_TEMP_NORMAL:
            self.migrate_from_normal(immediately_run)
        else:
            xlogging.raise_and_logging_error(
                r'无效的迁移类型',
                r'HostSessionMigrateLogicProcessor invalid source_type : {}'.format(source_type))

    def migrate_from_normal(self, immediately_run):
        worker = MigrateTaskWithNormalWorker(self._source_host_object, self._pe_host_object, self._target_host_object,
                                             self._data)
        worker.work(immediately_run)

    def migrate_from_cdp(self, immediately_run):
        host_snapshot_object = \
            HostSnapshot.objects.filter(
                host=self._source_host_object, start_datetime__isnull=False, deleting=False, deleted=False).exclude(
                finish_datetime__isnull=False, successful=False).order_by('-start_datetime').first()
        self._wait_for_backup_data_transfer(host_snapshot_object.id)

        index_ident_map = self._get_source_disk_index_ident_map()
        migrate_disk_map = self._generate_migrate_disk_map(index_ident_map)
        disks = _generate_PeHostSessionRestoreDiskSerializer_from_cdp(datetime.now(), migrate_disk_map,
                                                                      host_snapshot_object)

        pe_restore = PeRestore(self._pe_host_object)
        data = {'type': xdata.SNAPSHOT_TYPE_CDP, 'disks': disks, 'adapters': self._data['adapters'],
                'host_snapshot_id': host_snapshot_object.id, 'drivers_ids': self._data['drivers_ids'],
                'agent_user_info': self._data['agent_user_info'], 'routers': self._data['routers'],
                'disable_fast_boot': self._data['disable_fast_boot'], 'restore_time': timezone.now(),
                'remote_kvm_params': self._data['remote_kvm_params'],
                'replace_efi': self._data.get('replace_efi', False)}
        pe_restore.init(data)
        worker_data = {'diskreadthreadcount': self._data['diskreadthreadcount']}
        worker = MigrateTaskWithCdpWorker(self._source_host_object, self._pe_host_object, self._target_host_object,
                                          host_snapshot_object, worker_data)
        worker.work(immediately_run)

    def _get_source_disk_index_ident_map(self):
        index_ident_map = dict()
        disks = boxService.box_service.queryDisksStatus(self._source_host_object.ident)
        for disk in disks:
            index_ident_map[disk.id] = disk.detail.name
        return index_ident_map

    # index_ident_map : {0: 'zxcvbnm32', 1: 'qwcvbnm32'}
    # self._data['disks'] : [{'src': 0, 'dest': 0}, {'src': 1, 'dest': 1}]
    # result : [{'src': 'zxcvbnm32', 'dest': 0}, {'src': 'qwcvbnm32', 'dest': 1}]
    def _generate_migrate_disk_map(self, index_ident_map):
        result = list()
        disks = self._data['disks']
        for disk in disks:
            result.append({'src': index_ident_map[disk['src']], 'dest': disk['dest']})
        return result


class HostSnapshotRestoreVolumeLogicProcessor(object):
    # data : HostSnapshotRestoreSerializer 反序列化的字典对象
    # host_object : 启动 pe host 的 agent host
    # host_snapshot_object : HostSnapshot 数据库对象
    def __init__(self, host_snapshot_object, host_object, data):
        self._data = data
        self._host_object = host_object
        self._host_snapshot_object = host_snapshot_object

    @staticmethod
    def _find_target_disk_number_index(target_system_info, disk_guid, sector_offset, sectors, letter):
        partitionOffset = str(int(sector_offset) * 512)
        partitionSize = str(int(sectors) * 512)

        for disk in target_system_info['Disk']:
            if disk_guid == disk['NativeGUID']:
                for partirion in disk['Partition']:
                    if partirion['PartitionOffset'] == partitionOffset and partirion['PartitionSize'] == partitionSize:
                        return int(disk['DiskNum'])

        _logger.warning('_find_target_disk_number match failed, try match letter : {}'.format(letter))

        for disk in target_system_info['Disk']:
            for partirion in disk['Partition']:
                if partirion['PartitionOffset'] == partitionOffset and partirion['PartitionSize'] == partitionSize and \
                        letter == partirion['Letter']:
                    return int(disk['DiskNum'])

        xlogging.raise_and_logging_error(
            r'还原目标机未匹配到还原目标卷',
            '_find_target_disk_number failed : {}\n{}\n{}\n{}\n{}'.format(
                target_system_info['Disk'], disk_guid, sector_offset, sectors, letter))

    @staticmethod
    def _convert_volume_2_disk_ranges(volume, disk_range):
        return {
            'sector_offset': int(disk_range['sector_offset']),
            'sectors': int(disk_range['sectors']),
            'device_name': volume['target_ident'],
            'display_name': volume['display_name'],
            'target_display_name': volume['target_display_name'],
            'mount_point_after_restore': volume['mount_point_after_restore'],
            'mount_fs_type_after_restore': volume['mount_fs_type_after_restore'],
            'mount_fs_opts_after_restore': volume['mount_fs_opts_after_restore'],
        }

    def _analyze_disks(self):
        disks = list()
        for volume in self._data['volumes']:
            for disk_range in volume['ranges']:
                for disk in disks:
                    if disk['dest'] == disk_range['target_disk_number']:
                        if disk['src'] != disk_range['disk_ident']:
                            xlogging.raise_and_logging_error('无效的还原目标，无法定位目标磁盘', '_analyze_disks')
                        disk['ranges'].append(self._convert_volume_2_disk_ranges(volume, disk_range))
                        break
                else:
                    disks.append({'dest': disk_range['target_disk_number'], 'src': disk_range['disk_ident'],
                                  'ranges': [self._convert_volume_2_disk_ranges(volume, disk_range)]})
        return disks

    def _convert_2_AgentHostSessionRestoreSerializer(self, input_disks):
        data = {'type': self._data['type'], 'host_snapshot_id': self._host_snapshot_object.id}
        disks = None
        if self._data['type'] == xdata.SNAPSHOT_TYPE_NORMAL:
            disks = _generate_PeHostSessionRestoreDiskSerializer_from_normal(
                input_disks, self._host_snapshot_object)
        elif self._data['type'] == xdata.SNAPSHOT_TYPE_CDP:
            disks = _generate_PeHostSessionRestoreDiskSerializer_from_cdp(
                self._data['restore_time'], input_disks, self._host_snapshot_object)
        else:
            xlogging.raise_and_logging_error(
                r'内部异常，代码2367',
                r'_convert_2_AgentHostSessionRestoreSerializer never happen')

        data['disks'] = disks
        for disk in data['disks']:
            for input_disk in input_disks:
                if disk['disk_index'] == int(input_disk['dest']):
                    disk['volumes'] = input_disk['ranges']
                    break
            else:
                xlogging.raise_and_logging_error('内部异常，代码2383', '_convert_2_AgentHostSessionRestoreSerializer')

        data['htb_task_uuid'] = self._data['htb_task_uuid']
        if self._host_snapshot_object.is_cdp:
            data['restore_time'] = self._data['restore_time']
        else:
            data['restore_time'] = self._host_snapshot_object.start_datetime
        serializer = AgentHostSessionRestoreSerializer(data=data)
        serializer.is_valid(True)
        return serializer.validated_data

    def run(self, immediately_run):
        self._check_valid()
        disks = self._analyze_disks()
        data = self._convert_2_AgentHostSessionRestoreSerializer(disks)
        agent_restore = AgentRestore(self._host_object)
        agent_restore.init(data)
        worker = RestoreVolumeTaskWorker(agent_restore, self._host_snapshot_object)
        worker.work(immediately_run)

    def _check_valid(self):
        sdisk2ddisk = dict()  # src 2 dest
        for volume in self._data['volumes']:
            for disk_range in volume['ranges']:
                if disk_range['disk_ident'] in sdisk2ddisk.keys():
                    if disk_range['target_disk_number'] != sdisk2ddisk[disk_range['disk_ident']]:
                        xlogging.raise_and_logging_error(r'不支持的还原参数，多目标文件卷交叉还原', 'unsupported', 1)
                else:
                    sdisk2ddisk[disk_range['disk_ident']] = disk_range['target_disk_number']

        ddisk2sdisk = dict()  # dest 2 src
        for volume in self._data['volumes']:
            for disk_range in volume['ranges']:
                if disk_range['target_disk_number'] in ddisk2sdisk.keys():
                    if disk_range['disk_ident'] != ddisk2sdisk[disk_range['target_disk_number']]:
                        xlogging.raise_and_logging_error(r'不支持的还原参数，多文件卷交叉还原', 'unsupported', 1)
                else:
                    ddisk2sdisk[disk_range['target_disk_number']] = disk_range['disk_ident']


class HostSnapshotRestoreLogicProcessor(object):
    # data : HostSnapshotRestoreSerializer 反序列化的字典对象
    # host_object : 启动 pe host 的 agent host
    # pe_host_object : RestoreTarget 数据库对象
    # host_snapshot_object : HostSnapshot 数据库对象
    def __init__(self, host_snapshot_object, pe_host_object, host_object, data):
        self._data = data
        self._host_object = host_object
        self._pe_host_object = pe_host_object
        self._host_snapshot_object = host_snapshot_object

    def _convert_HostSnapshotRestoreSerializer_2_PeHostSessionRestoreSerializer(self):
        data = {'type': self._data['type'], 'adapters': self._data['adapters']}

        if self._data['type'] == xdata.SNAPSHOT_TYPE_NORMAL:
            disks = _generate_PeHostSessionRestoreDiskSerializer_from_normal(
                self._data['disks'], self._host_snapshot_object)
        elif self._data['type'] == xdata.SNAPSHOT_TYPE_CDP:
            disks = _generate_PeHostSessionRestoreDiskSerializer_from_cdp(
                self._data['restore_time'], self._data['disks'], self._host_snapshot_object)
        else:
            xlogging.raise_and_logging_error(
                r'内部异常，代码2367',
                r'_convert_HostSnapshotRestoreSerializer_2_PeHostSessionRestoreSerializer never happen')
            disks = list()

        data['disks'] = disks
        data['host_snapshot_id'] = self._host_snapshot_object.id
        data['drivers_ids'] = self._data['drivers_ids']
        data['agent_user_info'] = self._data['agent_user_info']
        data['routers'] = self._data['routers']
        data['exclude_volumes'] = self._data['exclude_volumes']
        data['disable_fast_boot'] = self._data['disable_fast_boot']
        data['replace_efi'] = self._data['replace_efi']
        data['htb_task_uuid'] = self._data['htb_task_uuid']
        if self._host_snapshot_object.is_cdp:
            data['restore_time'] = self._data['restore_time']
        else:
            data['restore_time'] = self._host_snapshot_object.start_datetime
        data['remote_kvm_params'] = self._data['remote_kvm_params']
        serializer = PeHostSessionRestoreSerializer(data=data)
        serializer.is_valid(True)
        return serializer.validated_data

    def run(self, immediately_run):
        data = self._convert_HostSnapshotRestoreSerializer_2_PeHostSessionRestoreSerializer()
        pe_restore = PeRestore(self._pe_host_object)
        pe_restore.init(data)
        worker = RestoreTaskWorker(self._pe_host_object, self._host_snapshot_object, self._host_object)
        worker.work(immediately_run)


def __file_disk_map(ext_info, disk_objs):
    ext_info['BootMapCache'] = ext_info['system_infos']['BootMap'] = \
        {str(disk_obj.id): disk_obj.detail.bootDevice for disk_obj in disk_objs}
    ext_info['SystemMapCache'] = ext_info['system_infos']['SystemMap'] = \
        {str(disk_obj.id): disk_obj.detail.systemDevice for disk_obj in disk_objs}
    ext_info['BmfMapCache'] = ext_info['system_infos']['BmfMap'] = \
        {str(disk_obj.id): disk_obj.detail.bmfDevice for disk_obj in disk_objs}


# 查询主机系统信息(实时，缓存)
@xlogging.convert_exception_to_value(None)
def query_system_info(host_obj, update=False):
    _ext_info = json.loads(host_obj.ext_info)  # system_infos初始存在 (其中Key：BootMap初始不存在)
    if _ext_info.get('nas_path'):
        return _ext_info['system_infos']

    if update:  # 获取实时状态的system_infos
        disk_objs = boxService.box_service.queryDisksStatus(host_obj.ident)
        system_infos = json.loads(boxService.box_service.querySystemInfo(host_obj.ident))
        _ext_info['system_infos'] = system_infos  # 刷新system_infos
        __file_disk_map(_ext_info, disk_objs)
        host_obj.ext_info = json.dumps(_ext_info)
        host_obj.save(update_fields=['ext_info'])
    elif 'BootMap' not in _ext_info['system_infos'] or 'SystemMap' not in _ext_info['system_infos'] \
            or 'BmfMap' not in _ext_info['system_infos']:
        try:
            disk_objs = boxService.box_service.queryDisksStatus(host_obj.ident)
            __file_disk_map(_ext_info, disk_objs)
        except Exception as e:
            if 'BootMapCache' not in _ext_info or 'SystemMapCache' not in _ext_info \
                    or 'BmfMapCache' not in _ext_info:
                raise
            else:
                _logger.warning(r'query_system_info queryDisksStatus failed : {}'.format(e))
                _ext_info['system_infos']['BootMap'] = _ext_info['BootMapCache']
                _ext_info['system_infos']['SystemMap'] = _ext_info['SystemMapCache']
                _ext_info['system_infos']['BmfMap'] = _ext_info['BmfMapCache']

        host_obj.ext_info = json.dumps(_ext_info)
        host_obj.save(update_fields=['ext_info'])

    return _ext_info['system_infos']
