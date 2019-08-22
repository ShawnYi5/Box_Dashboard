# coding=utf-8
import datetime
import html
import json, math
from datetime import timedelta
import threading
from apiv1.models import TakeOverKVM
import django.utils.timezone as timezone
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from apiv1.models import HostSnapshotShare
from apiv1 import tasks
from apiv1.htb_task import HTBTaskQuery, get_disk_name, change_master_ip
from apiv1.restore import PeRestore
from apiv1.signals import end_sleep
from apiv1.storage_nodes import UserQuotaTools
from box_dashboard import boxService, xlogging
from box_dashboard import functions
from box_dashboard import xdata, xdatetime
from django.http import HttpResponse
from rest_framework import status
from apiv1.models import (Host, BackupTask, CDPTask, RestoreTask, MigrateTask, UserQuota, HostSnapshot, RestoreTarget,
                          HTBSchedule, HTBTask, ClusterBackupTask, RemoteBackupTask, VirtualMachineRestoreTask,
                          BackupTaskSchedule, ArchiveTask, ImportSnapshotTask, FileBackupTask, ClusterBackupSchedule,
                          FileSyncTask, FileSyncSchedule)
from apiv1.views import HostTasksStatt, BackupTaskScheduleSetting, check_host_ident_valid, ClusterBackupScheduleManager
from xdashboard.handle import backupmgr, serversmgr, logserver
from xdashboard.handle.home_utils import (TaskSummary, BackupTodayTasks, ClusterTodayTasks,
                                          CommonUtils)
from xdashboard.handle.sysSetting import storage
from xdashboard.models import DeviceRunState, UserQuotaSpace, OperationLog
from xdashboard.request_util import get_operator
from django.db.models.signals import post_save
from xdashboard.models import audit_task
from .audittask import get_approved_task_host_snapshot_id
from apiv1.models import AutoVerifyTask
from apiv1.cluster_cdp_backup_task import STATUS_MAP

_logger = xlogging.getLogger(__name__)

import KTService

cache_over_time = dict()
cache_over_time_locker = threading.Lock()


class RestoreBackupOverTime(object):
    BACKUP_FLAG = 'b_'
    RESTORE_FLAG = 'r_'
    ARCHIVE_FLAG = 'archive_'
    IMPORT_ARCHIVE_FLAG = 'importarchive_'

    @staticmethod
    def calc_speed_array_by_sampling(sampling):
        speed = list()
        for i in range(len(sampling) - 1):
            next_sampling = sampling[i + 1]
            current_sampling = sampling[i]
            delta_index = next_sampling['index'] - current_sampling['index']
            delta_time = (next_sampling['time'] - current_sampling['time']).total_seconds()
            speed.append(delta_index / delta_time)
        return speed

    @staticmethod
    def calc_over_time_by_cache_item(cache_item):
        speed = RestoreBackupOverTime.calc_speed_array_by_sampling(cache_item['sampling'])
        avg_speed = sum(speed) / len(speed)

        over_index = abs(cache_item['total'] - cache_item['sampling'][-1]['index'])
        return over_index / avg_speed

    @staticmethod
    @xlogging.convert_exception_to_value(None)
    def backup_over_time(sender, **kwargs):
        backup_obj = kwargs['instance']
        ext_info = json.loads(backup_obj.ext_info)
        total = ext_info.get('progressTotal', None)
        index = ext_info.get('progressIndex', None)
        if None in [index, total]:
            return
        back_task = RestoreBackupOverTime.BACKUP_FLAG + str(backup_obj.id)
        RestoreBackupOverTime.calc_task_over_time(back_task, backup_obj, index, total, backup_obj.host.ident)

    @staticmethod
    @xlogging.convert_exception_to_value(None)
    def restore_over_time(sender, **kwargs):
        restore_obj = kwargs['instance']
        total_bytes = restore_obj.total_bytes
        restored_bytes = restore_obj.restored_bytes
        if None in [total_bytes, restored_bytes]:
            return
        restore_mark = RestoreBackupOverTime.RESTORE_FLAG + str(restore_obj.id)
        restore_target_info = json.loads(restore_obj.info)
        host_ident = restore_target_info['host_ident']
        RestoreBackupOverTime.calc_task_over_time(restore_mark, restore_obj, restored_bytes, total_bytes, host_ident)

    @staticmethod
    def calc_task_over_time(task_ident, task_obj, progress, total, host_ident):
        with cache_over_time_locker:
            if task_obj.finish_datetime is not None:
                cache_over_time.pop(task_ident, None)
                return

            time_now = datetime.datetime.now()
            if task_ident not in cache_over_time.keys() or cache_over_time[task_ident]['total'] != total \
                    or cache_over_time[task_ident]['host_ident'] != host_ident:
                cache_over_time_item = {'total': total, 'sampling': list(), 'host_ident': host_ident}
                cache_over_time[task_ident] = cache_over_time_item
            else:
                cache_over_time_item = cache_over_time[task_ident]

            sampling = cache_over_time_item['sampling']
            if len(sampling) != 0 and sampling[-1]['time'] == time_now:
                sampling[-1]['index'] = progress
            if len(sampling) != 0 and sampling[-1]['index'] == progress:
                sampling[-1]['time'] = time_now
            else:
                sampling.append({'index': progress, 'time': time_now})

            if len(sampling) > (5 + 1):
                del sampling[0]

            if len(sampling) > 5:
                task_over_time = RestoreBackupOverTime.calc_over_time_by_cache_item(cache_over_time_item)
                cache_over_time_item['over_time'] = datetime.datetime.now() + datetime.timedelta(seconds=task_over_time)

    @staticmethod
    def query_end_time(task_ident):
        now_time = datetime.datetime.now()
        with cache_over_time_locker:
            if task_ident not in cache_over_time.keys():
                return ''
            cache_over_time_item = cache_over_time[task_ident]
            if 'over_time' not in cache_over_time_item:
                return "时间计算中"
            if cache_over_time_item['over_time'] <= now_time:
                return ''
            result = str(math.ceil((cache_over_time_item['over_time'] - now_time).total_seconds()))
            return result

    @staticmethod
    @xlogging.convert_exception_to_value(None)
    def host_offline(sender, **kwargs):
        host_obj = kwargs['instance']
        with cache_over_time_locker:
            for k, v in cache_over_time.items():
                if v['host_ident'] == host_obj.ident:
                    del cache_over_time[k]
                    break

    @staticmethod
    def archive_over_time(sender, **kwargs):
        task_obj = kwargs['instance']
        ext_config = json.loads(task_obj.ext_config)
        total = ext_config.get('progressTotal', None)
        index = ext_config.get('progressIndex', None)
        if None in [index, total]:
            return
        back_task = RestoreBackupOverTime.ARCHIVE_FLAG + str(task_obj.id)
        RestoreBackupOverTime.calc_task_over_time(back_task, task_obj, index, total, task_obj.host_snapshot.host.ident)

    @staticmethod
    def import_archive_over_time(sender, **kwargs):
        task_obj = kwargs['instance']
        ext_config = json.loads(task_obj.ext_config)
        total = ext_config.get('progressTotal', None)
        index = ext_config.get('progressIndex', None)
        if None in [index, total]:
            return
        back_task = RestoreBackupOverTime.IMPORT_ARCHIVE_FLAG + str(task_obj.id)
        RestoreBackupOverTime.calc_task_over_time(back_task, task_obj, index, total, task_obj.host_snapshot.host.ident)


post_save.connect(RestoreBackupOverTime.restore_over_time, sender=RestoreTarget)

post_save.connect(RestoreBackupOverTime.backup_over_time, sender=HostSnapshot)

post_save.connect(RestoreBackupOverTime.host_offline, sender=Host)

post_save.connect(RestoreBackupOverTime.archive_over_time, sender=ArchiveTask)

post_save.connect(RestoreBackupOverTime.import_archive_over_time, sender=ImportSnapshotTask)


# 检查目标客户端是否在线(目前只能找到volume还原时候的Host)
def _check_target_host_is_linked(task_object):
    if isinstance(task_object, RestoreTask):
        host = task_object.target_host
    else:
        host = task_object.destination_host

    if host is None:
        return False
    else:
        return host.is_linked


# 过滤主机
def _filter_hosts(hosts, s_key):
    if not s_key:
        return hosts
    else:
        result = []
        for host in hosts:
            host_ip_str = backupmgr.get_host_ip_str(host.id)
            is_need = serversmgr.filter_hosts(s_key, host.display_name, host_ip_str)
            if is_need:
                result.append(host)
        return result


class CancelTask(object):
    """
    kwargs must contain {'name':''}
    """

    def __init__(self, request, **kwargs):
        self.request = request
        self.kwargs = kwargs
        self.ret = {'e': '', 'r': 0}

    def cancel(self):
        func_name = 'cancel_{}'.format(self.kwargs.pop('name', 'unkown'))
        if hasattr(self, func_name):
            return getattr(self, func_name)()
        else:
            self.ret['e'] = 'not exist:{}'.format(func_name)
            self.ret['r'] = 1
            return HttpResponse(json.dumps(self.ret))

    def cancel_audit(self):
        task_id = self.kwargs['task_id']
        audit_task.objects.filter(id=task_id).delete()
        return HttpResponse('{"r": 0, "e": "操作成功"}')

    def cancel_file_backup_snapshot(self):
        # 1.设置task_obj的cancel字段
        task_id = self.kwargs['task_id']
        task_obj = FileBackupTask.objects.get(id=task_id)
        ext_info = json.loads(task_obj.ext_config)
        ext_info[xdata.CANCEL_TASK_EXT_KEY] = 'any_value'
        task_obj.ext_config = json.dumps(ext_info)
        task_obj.save(update_fields=['ext_config'])
        rev = end_sleep.send_robust(sender=BackupTaskSchedule, schedule_id=task_obj.schedule.id)
        _logger.info('cancel_file_backup rev:{}'.format(rev))
        return HttpResponse('{"r": 0, "e": "操作成功"}')

    def cancel_cluster(self):
        # 1.设置task_obj的cancel字段
        task_id = self.kwargs['task_id']
        task_obj = ClusterBackupTask.objects.get(id=task_id)
        ext_info = json.loads(task_obj.ext_config)
        ext_info[xdata.CANCEL_TASK_EXT_KEY] = 'any_value'
        task_obj.ext_config = json.dumps(ext_info)
        task_obj.save(update_fields=['ext_config'])
        rev = end_sleep.send_robust(sender=ClusterBackupSchedule, schedule_id=task_obj.schedule.id)
        _logger.info('cancel_cluster rev:{}'.format(rev))
        return HttpResponse('{"r": 0, "e": "操作成功"}')

    def cancel_migrate(self):
        task_id = self.kwargs['task_id']
        task_obj = MigrateTask.objects.get(id=task_id)
        return self._restore_migrate_common(task_obj)

    def cancel_vm_restore(self):
        task_id = self.kwargs['task_id']
        task_obj = VirtualMachineRestoreTask.objects.get(id=task_id)
        # 1.设置task_obj的cancel字段
        ext_info = json.loads(task_obj.ext_config)
        ext_info[xdata.CANCEL_TASK_EXT_KEY] = 'any_value'
        task_obj.ext_config = json.dumps(ext_info)
        task_obj.save(update_fields=['ext_config'])
        return HttpResponse('{"r": 0, "e": "操作成功"}')

    def cancel_restore(self):
        task_id = self.kwargs['task_id']
        task_obj = RestoreTask.objects.get(id=task_id)
        return self._restore_migrate_common(task_obj)

    def _restore_migrate_common(self, task_obj):
        # 只能取消 目标主机不在线的情况
        if _check_target_host_is_linked(task_obj):
            return HttpResponse('{"r": 1, "e": "取消失败"}')
        if task_obj.finish_datetime:
            return HttpResponse('{"r": 0, "e": "操作成功"}')

        # 1.设置task_obj的cancel字段
        ext_info = json.loads(task_obj.ext_config)
        ext_info[xdata.CANCEL_TASK_EXT_KEY] = 'any_value'
        task_obj.ext_config = json.dumps(ext_info)
        task_obj.save(update_fields=['ext_config'])

        # 2.若KVM已启动，则删除启动标志文件
        flag_file = ext_info.get(xdata.START_KVM_FLAG_FILE, None)
        if (flag_file is not None) and boxService.box_service.isFileExist(flag_file):
            boxService.box_service.remove(flag_file)

        # 3.到KVM结束后，设置对应Token，并finish掉task_obj
        if xdata.RESTORE_IS_COMPLETE in ext_info:
            restore_disks = task_obj.restore_target.disks.all()
            pe_restore = PeRestore(task_obj.restore_target)
            for restore_disk in restore_disks:
                boxService.box_service.updateToken(
                    KTService.Token(token=restore_disk.token, snapshot=[], expiryMinutes=0))
            if isinstance(task_obj, RestoreTask):
                pe_restore.unlock_disk_snapshots('restore_{}'.format(task_obj.id))
                pe_restore.unlock_disk_snapshots('volume_restore_{}'.format(task_obj.id))
                tasks.finish_restore_task_object('', task_obj.id, False, '用户取消任务', 'user cancel task')
            else:
                pe_restore.unlock_disk_snapshots('migrate_{}'.format(task_obj.id))
                tasks.finish_migrate_task_object('', task_obj.id, False, '用户取消任务', 'user cancel task')

        if isinstance(task_obj, RestoreTask):
            record_cancel_log_restore(self.request, task_obj)
        else:
            record_cancel_log_migrate(self.request, task_obj)

        return HttpResponse('{"r": 0, "e": "操作成功"}')

    def cancel_backup(self):
        task_id = self.kwargs['task_id']
        task_obj = BackupTask.objects.get(id=task_id)
        if task_obj.finish_datetime:
            return HttpResponse(json.dumps(self.ret))

        # 1.设置task_obj的cancel字段
        ext_info = json.loads(task_obj.ext_config)
        ext_info[xdata.CANCEL_TASK_EXT_KEY] = 'any_value'
        task_obj.ext_config = json.dumps(ext_info)
        task_obj.save(update_fields=['ext_config'])

        rev = end_sleep.send_robust(sender=BackupTaskSchedule, schedule_id=task_obj.schedule.id)
        _logger.info('cancel_backup rev:{}'.format(rev))
        return HttpResponse(json.dumps(self.ret))

    def cancel_file_sync(self):
        task_id = self.kwargs['task_id']
        task_obj = FileSyncTask.objects.get(id=task_id)
        if task_obj.finish_datetime:
            return HttpResponse(json.dumps(self.ret))
        # 1.设置task_obj的cancel字段
        ext_info = json.loads(task_obj.ext_config)
        ext_info[xdata.CANCEL_TASK_EXT_KEY] = 'any_value'
        task_obj.ext_config = json.dumps(ext_info)
        task_obj.save(update_fields=['ext_config'])

        rev = end_sleep.send_robust(sender=FileSyncSchedule, schedule_id=task_obj.schedule_id)
        _logger.info('cancel_file_sync_task rev:{}'.format(rev))
        return HttpResponse(json.dumps(self.ret))


# 取消还原，迁移任务
def cancel_task(request):
    params = request.POST
    class_name = params.get('class_name')
    task_id = params.get('task_id')
    return CancelTask(request, name=class_name, task_id=task_id).cancel()


def record_cancel_log_restore(request, task_obj):
    host_snapshot = task_obj.host_snapshot
    restore_target = task_obj.restore_target
    plan_obj = host_snapshot.schedule
    plan_name = plan_obj.name if plan_obj else host_snapshot.host.display_name,
    start_time = host_snapshot.start_datetime.strftime('%Y-%m-%d %H:%M:%S'),
    pe_ip = json.loads(restore_target.info)['remote_ip'] if restore_target else '--'

    desc = r'还原备份点"{0}:{1}"到"{2}" 被取消'.format(plan_name, start_time, pe_ip)
    info = {'操作': '取消恢复任务', '描述': desc}
    logserver.SaveOperationLog(
        request.user, OperationLog.TYPE_RESTORE, json.dumps(info, ensure_ascii=False), get_operator(request))


def record_cancel_log_migrate(request, task_obj):
    host_ip = task_obj.host_snapshot.host.last_ip,
    restore_target = task_obj.restore_target
    pe_ip = json.loads(restore_target.info)['remote_ip'] if restore_target else '--'

    desc = r'迁移"{host_ip}"到"{pe_ip}" 被取消'.format(host_ip=host_ip, pe_ip=pe_ip)
    info = {'操作': '取消迁移任务', '描述': desc}
    logserver.SaveOperationLog(
        request.user, OperationLog.TYPE_MIGRATE, json.dumps(info, ensure_ascii=False), get_operator(request))


# 获取HostNode(简要描述: 名称, 成功数, 任务数)
# servername：指定服务器
# starttime：筛选任务们
# taskselect: 1当前任务 2近期任务 3 今日备份任务
def getStatusList(request):
    paramsQ = request.GET
    search_key = paramsQ.get('s_key', default='')
    timeBegin = paramsQ.get('starttime', default='2000-12-12 00:00:00')
    taskselect = int(paramsQ.get('taskselect', default='1'))
    id = paramsQ.get('id', 'root')
    if id == '':
        id = 'root'

    task_summary = TaskSummary()
    # 获取所有历史服务器Node
    if id == 'root':
        hosts = Host.objects.filter(user_id=request.user.id)
        # 过滤掉 被删除的主机
        hosts = list(filter(lambda x: not x.is_deleted, hosts))
        # 用户筛选的过滤
        hosts = _filter_hosts(hosts, search_key)
        if taskselect == 1:  # 当前任务
            rowList = getStatusList1(hosts)
        elif taskselect == 2:  # 历史任务
            rowList = getStatusList2(hosts, timeBegin)
        elif taskselect == 3:  # 今日备份任务
            task_summary, rowList = getStatusList3(hosts, request.user.id)
        else:
            rowList = list()

        rowList = sorted(rowList, key=lambda item: item['key'])

        if taskselect == 3:
            label = task_summary.to_html('所有主机')
            rowList.insert(0, {'id': 'ui_1', "branch": [], "inode": False, "open": False, 'label': label, 'icon': 'pc'})

        if len(rowList) == 0:
            rowList.append(
                {'id': '{}'.format('ui_1'), "branch": [], "inode": False, "open": False, 'label': '{}'.format('无任务')})

        jsonStr = json.dumps(rowList, ensure_ascii=False)
        return HttpResponse(jsonStr)
    else:
        return getSubStatusList(request)  # 某一个HostNode下的, 子Nodes(各类任务的描述)


def _get_exists_current_tasks_idents():
    idents = list()
    backup_idents = BackupTask.objects.filter(
        host_snapshot__isnull=False,
        finish_datetime__isnull=True).values_list(
        'schedule__host__ident',
        flat=True)
    idents.extend(backup_idents)

    migration_idents = MigrateTask.objects.filter(
        host_snapshot__isnull=False,
        finish_datetime__isnull=True).values_list(
        'source_host__ident',
        flat=True)
    idents.extend(migration_idents)

    restore_idents = RestoreTask.objects.filter(
        finish_datetime__isnull=True,
        restore_target__htb_task__isnull=True).values_list(
        'host_snapshot__host__ident', flat=True)
    idents.extend(restore_idents)

    cdp_idents = CDPTask.objects.filter(
        schedule__isnull=False,
        finish_datetime__isnull=True).filter(
        finish_datetime__isnull=True).values_list(
        'schedule__host__ident',
        flat=True)
    idents.extend(cdp_idents)

    cluster_idents = ClusterBackupTask.objects.filter(
        sub_tasks__host_snapshot__isnull=False,
        finish_datetime__isnull=True).values_list(
        'sub_tasks__host_snapshot__host__ident',
        flat=True)
    idents.extend(cluster_idents)

    htb_idents = HTBTask.objects.filter(
        start_datetime__isnull=False,
        finish_datetime__isnull=True).values_list(
        'schedule__host__ident',
        flat=True)
    idents.extend(htb_idents)

    remote_idents = RemoteBackupTask.objects.filter(
        start_datetime__isnull=False,
        finish_datetime__isnull=True,
        schedule__deleted=False,
        paused=False,
        schedule__enabled=True).values_list(
        'schedule__host__ident',
        flat=True)
    idents.extend(remote_idents)

    vmr_idents = VirtualMachineRestoreTask.objects.filter(
        start_datetime__isnull=False,
        finish_datetime__isnull=True).values_list(
        'host_snapshot__host__ident',
        flat=True)
    idents.extend(vmr_idents)

    export_idents = ArchiveTask.objects.filter(
        start_datetime__isnull=False,
        finish_datetime__isnull=True).values_list(
        'host_snapshot__host__ident',
        flat=True)
    idents.extend(export_idents)

    import_idents = ImportSnapshotTask.objects.filter(
        start_datetime__isnull=False,
        finish_datetime__isnull=True).values_list(
        'host_snapshot__host__ident',
        flat=True)
    idents.extend(import_idents)

    file_idents = FileBackupTask.objects.filter(
        start_datetime__isnull=False,
        finish_datetime__isnull=True).values_list(
        'host_snapshot__host__ident',
        flat=True)
    idents.extend(file_idents)

    audit_idents = list()
    audit_tasks = audit_task.objects.filter(status=audit_task.AUIDT_TASK_STATUS_WAITE)
    for task in audit_tasks:
        task_type, host_snapshot_id = get_approved_task_host_snapshot_id(json.loads(task.task_info))
        audit_idents.append(HostSnapshot.objects.get(id=host_snapshot_id).host.ident)
    idents.extend(audit_idents)

    verify_idents = list()
    verify_tasks = AutoVerifyTask.objects.filter(verify_type=AutoVerifyTask.VERIFY_TYPE_ING)
    for task in verify_tasks:
        host_snapshot_id = task.point_id.split('|')[1]
        verify_idents.append(HostSnapshot.objects.get(id=host_snapshot_id).host.ident)
    idents.extend(verify_idents)

    file_restore_idents = list()
    host_snapshot_shares = HostSnapshotShare.objects.all()
    for host_snapshot_share in host_snapshot_shares:
        host_snapshot = HostSnapshot.objects.filter(id=host_snapshot_share.host_snapshot_id)
        if host_snapshot:
            file_restore_idents.append(host_snapshot.first().host.ident)
    idents.extend(file_restore_idents)

    takeover_idents = list()
    takeover_kvms = TakeOverKVM.objects.filter(Q(kvm_type='temporary_kvm') | Q(kvm_type='forever_kvm')).all()
    for takeover_kvm in takeover_kvms:
        takeover_idents.append(takeover_kvm.host_snapshot.host.ident)
    idents.extend(takeover_idents)

    file_sync_idents = FileSyncTask.objects.filter(
        start_datetime__isnull=False,
        finish_datetime__isnull=True).values_list(
        'host_snapshot__host__ident',
        flat=True)
    idents.extend(file_sync_idents)

    return set(idents)


def getStatusList1(hosts):  # 当前任务
    rowList = list()
    exists_task_idents = _get_exists_current_tasks_idents()
    for host in hosts:
        if host.ident not in exists_task_idents:
            continue
        if host.is_linked:
            icon = 'pc'
        elif host.is_remote:
            icon = 'pc'
        else:
            icon = 'pcoffline'
        if host.type != Host.AGENT:
            host_name = '{}[{}]'.format(host.name, host.get_type_display())
        else:
            host_name = '{}'.format(host.name)

        lable = '<div style="float:right;" class="tasktree_total_width"><span>{}</span><div style="float:right;"></div></div>'.format(
            host_name)
        key = (-1 if host.is_linked else 0,  # 在线的主机靠前
               host.name  # 按照主机名称排序
               )
        info = {'id': '{}'.format(host.ident), 'icon': icon, "branch": [],
                "inode": True, "open": True, 'label': lable, 'key': key}
        rowList.append(info)

    return rowList


def getStatusList2(hosts, timeBegin):  # 历史任务
    rowList = list()
    for host in hosts:
        if host.is_linked:
            icon = 'pc'
        elif host.is_remote:
            icon = 'pc'
        else:
            icon = 'pcoffline'
        host_all_tasks, host_suc_tasks = _get_host_tasks_successful2fail_num(host, timeBegin)

        if host.type != Host.AGENT:
            host_name = '{}[{}]'.format(host.name, host.get_type_display())
        else:
            host_name = '{}'.format(host.name)

        lable = '<div style="float:right;" class="tasktree_total_width"><span>{}</span><div style="float:right;">成功数：{}/任务数：{}</div></div>'.format(
            host_name, host_suc_tasks, host_all_tasks)

        inode = (host_all_tasks != 0)  # 主机是否 做过任务
        is_open = False

        key = (-1 if host.is_linked else 0,  # 在线的主机靠前
               host.name  # 按照主机名称排序
               )
        info = {'id': '{}'.format(host.ident), 'icon': icon, "branch": [],
                "inode": inode, "open": is_open, 'label': lable, 'key': key}
        rowList.append(info)

    return rowList


def getStatusList3(hosts, user_id):  # 今日备份
    task_summary = TaskSummary()
    rowList = list()
    for host in hosts:
        host_task = BackupTodayTasks(host)
        if host.is_linked:
            icon = 'pc'
        elif host.is_remote:
            icon = 'pc'
        else:
            icon = 'pcoffline'
        host_task.calc()
        lable = host_task.task_summary.to_html(host.name)
        inode = host_task.exists_task()
        key = (-1 if host.is_linked else 0,  # 在线的主机靠前
               host.name  # 按照主机名称排序
               )
        task_summary.add(host_task.task_summary)
        info = {'id': '{}'.format(host.ident), 'icon': icon, "branch": [],
                "inode": inode, "open": False, 'label': lable, 'key': key}
        if inode:
            rowList.append(info)
    for schedules in ClusterTodayTasks.get_schedules(user_id):
        ins = ClusterTodayTasks(schedules)
        ins.calc()
        if ins.exists_task():
            rowList.append(ins.to_node())
            task_summary.add(ins.task_summary)
        else:
            pass

    return task_summary, rowList


def _get_tree_node(host, taskselect, timeBegin):
    # 统计主机今日任务
    host_task = BackupTodayTasks(host)
    if host.is_linked:
        icon = 'pc'
    elif host.is_remote:
        icon = 'pc'
    else:
        icon = 'pcoffline'
    if taskselect == 1:  # 当前正在进行的任务
        if host.type != Host.AGENT:
            host_name = '{}[{}]'.format(host.name, host.get_type_display())
        else:
            host_name = '{}'.format(host.name)

        lable = '<div style="float:right;" class="tasktree_total_width"><span>{}</span><div style="float:right;"></div></div>'.format(
            host_name)
        inode = is_host_exist_any_current_task(hostIdent=host.ident, timeBegin=timeBegin)
        if inode:
            is_open = True
        else:
            is_open = False
    elif taskselect == 2:  # 历史近期任务
        host_all_tasks, host_suc_tasks = _get_host_tasks_successful2fail_num(host, timeBegin)

        if host.type != Host.AGENT:
            host_name = '{}[{}]'.format(host.name, host.get_type_display())
        else:
            host_name = '{}'.format(host.name)

        lable = '<div style="float:right;" class="tasktree_total_width"><span>{}</span><div style="float:right;">成功数：{}/任务数：{}</div></div>'.format(
            host_name, host_suc_tasks, host_all_tasks)

        inode = (host_all_tasks != 0)  # 主机是否 做过任务
        is_open = False
    else:  # 今日任务 taskselect is 3
        host_task.calc()
        lable = host_task.task_summary.to_html(host.name)
        inode = host_task.exists_task()
        is_open = False

    key = (-1 if host.is_linked else 0,  # 在线的主机靠前
           host.name  # 按照主机名称排序
           )
    if taskselect == 1:
        if not inode:  # 不存在任务时候
            return None, host_task

    if taskselect == 3:
        if not host_task.exists_task():  # 不存在任务时候
            return None, host_task

    info = {'id': '{}'.format(host.ident), 'icon': icon, "branch": [],
            "inode": inode, "open": is_open, 'label': lable, 'key': key}
    return info, host_task


def _get_host_tasks_successful2fail_num(host, timeBegin):
    # 一个host的任务情况, return: {'normBackupTasks': (9,10), 'migrationTasks': (2,2), 'restoreTasks': (2,2)}
    resp = HostTasksStatt().get(request=None, ident=host.ident, api_request={'timeBegin': timeBegin})
    host_all_tasks = 0
    host_suc_tasks = 0
    if status.is_success(resp.status_code):
        hostTasksStatt = resp.data
        if 'normBackupTasks' in hostTasksStatt:
            host_all_tasks += hostTasksStatt['normBackupTasks'][1]
            host_suc_tasks += hostTasksStatt['normBackupTasks'][0]
        if 'migrationTasks' in hostTasksStatt:
            host_all_tasks += hostTasksStatt['migrationTasks'][1]
            host_suc_tasks += hostTasksStatt['migrationTasks'][0]
        if 'restoreTasks' in hostTasksStatt:
            host_all_tasks += hostTasksStatt['restoreTasks'][1]
            host_suc_tasks += hostTasksStatt['restoreTasks'][0]
        if 'cdpTasks' in hostTasksStatt:
            host_all_tasks += hostTasksStatt['cdpTasks'][1]
            host_suc_tasks += hostTasksStatt['cdpTasks'][0]
        if 'clusterTasks' in hostTasksStatt:
            host_all_tasks += hostTasksStatt['clusterTasks'][1]
            host_suc_tasks += hostTasksStatt['clusterTasks'][0]
        if 'htbTasks' in hostTasksStatt:
            host_all_tasks += hostTasksStatt['htbTasks'][1]
            host_suc_tasks += hostTasksStatt['htbTasks'][0]
        if 'remote_backup_task' in hostTasksStatt:
            host_all_tasks += hostTasksStatt['remote_backup_task'][1]
            host_suc_tasks += hostTasksStatt['remote_backup_task'][0]
        if 'archive_task_status' in hostTasksStatt:
            host_all_tasks += hostTasksStatt['archive_task_status'][1]
            host_suc_tasks += hostTasksStatt['archive_task_status'][0]
        if 'file_backup_task_status' in hostTasksStatt:
            host_all_tasks += hostTasksStatt['file_backup_task_status'][1]
            host_suc_tasks += hostTasksStatt['file_backup_task_status'][0]

    return host_all_tasks, host_suc_tasks


def getVirtualProgress(starttime, index, total):
    if index == 0:  # 一直没有走进度，返回虚拟的进度，以50分钟为最大值
        progress_str = '{:.2%}'.format((timezone.now() - starttime).total_seconds() / (50 * 60))
        size_str = ''
    elif total == 10000:  # 只可以表现进度
        progress_str = functions.format_progress(index, total)
        size_str = ''
    else:
        progress_str = functions.format_progress(index, total)
        size_str = '{}/{}'.format(functions.format_size(index * 64 * 1024), functions.format_size(total * 64 * 1024))

    if size_str:
        return '{}({})'.format(size_str, progress_str)
    else:
        return '{}'.format(progress_str)


def _clear_attr(my_dict, key_name):
    my_dict[key_name] = None
    return my_dict


def is_host_exist_any_task(hostIdent, timeBegin):
    backup_tasks_exists = BackupTask.objects.filter(schedule__host__ident=hostIdent,
                                                    host_snapshot__isnull=False).filter(
        Q(start_datetime__gte=timeBegin) | Q(finish_datetime__isnull=True)).exists()
    if backup_tasks_exists:
        return True

    migration_tasks_exists = MigrateTask.objects.filter(source_host__ident=hostIdent,
                                                        host_snapshot__isnull=False).filter(
        Q(start_datetime__gte=timeBegin) | Q(finish_datetime__isnull=True)).exists()
    if migration_tasks_exists:
        return True

    restore_tasks_exists = RestoreTask.objects.filter(host_snapshot__host__ident=hostIdent).filter(
        Q(start_datetime__gte=timeBegin) | Q(finish_datetime__isnull=True)).filter(
        restore_target__htb_task__isnull=True).exists()
    if restore_tasks_exists:
        return True

    cdp_tasks_exists = CDPTask.objects.filter(schedule__isnull=False, schedule__host__ident=hostIdent).filter(
        Q(start_datetime__gte=timeBegin) | Q(finish_datetime__isnull=True)).exists()
    if cdp_tasks_exists:
        return True

    cluster_tasks_exists = ClusterBackupTask.objects.filter(sub_tasks__host_snapshot__host__ident=hostIdent,
                                                            sub_tasks__host_snapshot__isnull=False).filter(
        Q(start_datetime__gte=timeBegin) | Q(finish_datetime__isnull=True)).exists()
    if cluster_tasks_exists:
        return True

    htb_tasks_exists = HTBTask.objects.filter(schedule__host__ident=hostIdent, start_datetime__isnull=False).filter(
        Q(finish_datetime__isnull=False) | Q(start_datetime__gte=timeBegin)
    ).exists()
    if htb_tasks_exists:
        return True

    remote_backup_tasks_exists = RemoteBackupTask.objects.filter(schedule__host__ident=hostIdent,
                                                                 start_datetime__isnull=False).filter(
        Q(finish_datetime__isnull=False) | Q(start_datetime__gte=timeBegin)
    ).exists()
    if remote_backup_tasks_exists:
        return True

    vmr_task_exits = VirtualMachineRestoreTask.objects.filter(schedule__host__ident=hostIdent,
                                                              start_datetime__isnull=False).filter(
        Q(finish_datetime__isnull=False) | Q(start_datetime__gte=timeBegin)).exists()
    if vmr_task_exits:
        return True

    return False


def is_host_exist_any_current_task(hostIdent, timeBegin):
    backup_tasks_exists = BackupTask.objects.filter(schedule__host__ident=hostIdent,
                                                    host_snapshot__isnull=False).filter(
        finish_datetime__isnull=True).exists()
    if backup_tasks_exists:
        return True

    migration_tasks_exists = MigrateTask.objects.filter(source_host__ident=hostIdent,
                                                        host_snapshot__isnull=False).filter(
        finish_datetime__isnull=True).exists()
    if migration_tasks_exists:
        return True

    restore_tasks_exists = RestoreTask.objects.filter(host_snapshot__host__ident=hostIdent).filter(
        finish_datetime__isnull=True).filter(restore_target__htb_task__isnull=True).exists()
    if restore_tasks_exists:
        return True

    cdp_tasks_exists = CDPTask.objects.filter(schedule__isnull=False, schedule__host__ident=hostIdent).filter(
        finish_datetime__isnull=True).exists()
    if cdp_tasks_exists:
        return True

    cluster_tasks_exists = ClusterBackupTask.objects.filter(sub_tasks__host_snapshot__host__ident=hostIdent,
                                                            sub_tasks__host_snapshot__isnull=False,
                                                            finish_datetime__isnull=True).exists()
    if cluster_tasks_exists:
        return True

    htb_tasks_exists = HTBTask.objects.filter(schedule__host__ident=hostIdent, start_datetime__isnull=False,
                                              finish_datetime__isnull=True).exists()
    if htb_tasks_exists:
        return True

    remote_backup_tasks_exists = RemoteBackupTask.objects.filter(schedule__host__ident=hostIdent,
                                                                 start_datetime__isnull=False,
                                                                 finish_datetime__isnull=True,
                                                                 schedule__deleted=False,
                                                                 paused=False,
                                                                 schedule__enabled=True).exists()
    if remote_backup_tasks_exists:
        return True

    vmr_tasks_exists = VirtualMachineRestoreTask.objects.filter(host_snapshot__host__ident=hostIdent,
                                                                start_datetime__isnull=False,
                                                                finish_datetime__isnull=True).exists()
    if vmr_tasks_exists:
        return True

    export_task_exists = ArchiveTask.objects.filter(host_snapshot__host__ident=hostIdent,
                                                    start_datetime__isnull=False,
                                                    finish_datetime__isnull=True).exists()
    if export_task_exists:
        return True

    import_task_exists = ImportSnapshotTask.objects.filter(host_snapshot__host__ident=hostIdent,
                                                           start_datetime__isnull=False,
                                                           finish_datetime__isnull=True).exists()
    if import_task_exists:
        return True

    file_backup_task_exists = FileBackupTask.objects.filter(host_snapshot__host__ident=hostIdent,
                                                            start_datetime__isnull=False,
                                                            finish_datetime__isnull=True).exists()

    if file_backup_task_exists:
        return True
    return False


def get_disk_native_guid(system_infos, disk_ident):
    for _range in system_infos['include_ranges']:
        if _range['diskIdent'] == disk_ident:
            return _range['diskNativeGUID']
    return None


@xlogging.convert_exception_to_value('')
def _get_remote_sub_task_display_info(sub_task):
    disk_name = get_disk_name(sub_task.local_snapshot)
    disk_snapshot = sub_task.local_snapshot
    ext_config = json.loads(sub_task.main_task.ext_config)
    st = ext_config['new_host_snapshot_info']['fields']['start_datetime']
    st = st.replace(' ', 'T')
    datetime_obj = datetime.datetime.strptime(st, '%Y-%m-%dT%H:%M:%S.%f')
    if disk_snapshot.is_cdp:
        ls = disk_snapshot.cdp_info.last_timestamp
        if ls:
            datetime_obj = datetime.datetime.fromtimestamp(ls)
        else:
            datetime_obj = st
        return '{} 已同步到:{}'.format(disk_name, datetime_obj.strftime(xdatetime.FORMAT_WITH_MICROSECOND))
    else:
        ext_config = json.loads(sub_task.ext_config)
        process = ext_config.get('process', '分析中')
        return '{} 同步至:{} 进度:{}'.format(disk_name, datetime_obj.strftime(xdatetime.FORMAT_WITH_MICROSECOND), process)


# 获取指定服务器的各种任务信息
# serverid: 选定的服务器ident
# starttime：筛选任务们
def getSubStatusList(request):
    paramsQ = request.GET
    hostIdent = paramsQ.get('id', default='')
    timeBegin = paramsQ.get('starttime', default='2000-12-12 00:00:00')
    taskselect = int(paramsQ.get('taskselect', default='1'))
    if not hostIdent:
        return HttpResponse('{"r": "1", "e": "请求参数缺失: serverid"}')
    rowList = list()

    if taskselect == 3:
        if hostIdent.startswith(ClusterTodayTasks.id_prefix):
            ins = ClusterTodayTasks.get_ins(hostIdent)
        else:
            ins = BackupTodayTasks(Host.objects.get(ident=hostIdent))
        ins.calc()
        for task in ins.tasks:
            rowList.append(task.to_node())
        rowList = sorted(rowList, key=lambda elem: elem['date_time'], reverse=True)
        rowList = [_clear_attr(elem, 'date_time') for elem in rowList]
    else:
        rowList = get_host_task_status(request, hostIdent, rowList, taskselect, timeBegin)

    jsonStr = json.dumps(rowList, ensure_ascii=False)
    return HttpResponse(jsonStr)


# todo
def get_host_task_status(request, hostIdent, rowList, taskselect, timeBegin):
    # 1.HTBTask的统计
    if taskselect == 1:
        htb_task_objs = HTBTask.objects.filter(schedule__host__ident=hostIdent, start_datetime__isnull=False,
                                               finish_datetime__isnull=True)
    else:
        htb_task_objs = HTBTask.objects.filter(schedule__host__ident=hostIdent, start_datetime__isnull=False).filter(
            Q(finish_datetime__isnull=False) | Q(start_datetime__gte=timeBegin)
        )

    for htb_task in htb_task_objs:
        rowList.append(_get_htb_task_detail_info(htb_task, taskselect))

    # 2.BackupTask的统计
    if taskselect == 1:
        backup_task_objs = BackupTask.objects.filter(schedule__host__ident=hostIdent,
                                                     host_snapshot__isnull=False).filter(finish_datetime__isnull=True)
    else:
        backup_task_objs = BackupTask.objects.filter(schedule__host__ident=hostIdent,
                                                     host_snapshot__isnull=False).filter(
            Q(start_datetime__gte=timeBegin) | Q(finish_datetime__isnull=True))

    for taskObj in backup_task_objs:
        rowList.append(_get_backup_task_detail_info(taskObj, taskselect))

    # 3.RestoreTask的统计
    if taskselect == 1:
        restore_task_objs = RestoreTask.objects.filter(host_snapshot__host__ident=hostIdent).filter(
            finish_datetime__isnull=True).filter(restore_target__htb_task__isnull=True)
    else:
        restore_task_objs = RestoreTask.objects.filter(host_snapshot__host__ident=hostIdent).filter(
            Q(start_datetime__gte=timeBegin) | Q(finish_datetime__isnull=True)).filter(
            restore_target__htb_task__isnull=True)
    for taskObj in restore_task_objs:
        rowList.append(_get_restore_task_detail_info(taskObj, taskselect))

    # 4.MigrateTask的统计
    if taskselect == 1:
        migration_task_objs = MigrateTask.objects.filter(source_host__ident=hostIdent,
                                                         host_snapshot__isnull=False).filter(
            finish_datetime__isnull=True)
    else:
        migration_task_objs = MigrateTask.objects.filter(source_host__ident=hostIdent,
                                                         host_snapshot__isnull=False).filter(
            Q(start_datetime__gte=timeBegin) | Q(finish_datetime__isnull=True))
    for taskObj in migration_task_objs:
        rowList.append(_get_migrate_task_detail_info(taskObj, taskselect))

    # 5.CDPTask的统计
    if taskselect == 1:
        cdp_task_objs = CDPTask.objects.filter(schedule__isnull=False, schedule__host__ident=hostIdent).filter(
            finish_datetime__isnull=True)
    else:
        cdp_task_objs = CDPTask.objects.filter(schedule__isnull=False, schedule__host__ident=hostIdent).filter(
            Q(start_datetime__gte=timeBegin) | Q(finish_datetime__isnull=True))
    for taskObj in cdp_task_objs:
        rowList.append(_get_cdp_task_detail_info(taskObj, taskselect))

    # 6.ClusterBackupTask的统计: 对应集群备份  (一个ClusterBackupTask对应一次Schedule的执行)
    if taskselect == 1:
        cluster_backup_task = ClusterBackupTask.objects.filter(sub_tasks__host_snapshot__host__ident=hostIdent,
                                                               sub_tasks__host_snapshot__isnull=False,
                                                               finish_datetime__isnull=True)
    else:
        cluster_backup_task = ClusterBackupTask.objects.filter(sub_tasks__host_snapshot__host__ident=hostIdent,
                                                               sub_tasks__host_snapshot__isnull=False).filter(
            Q(start_datetime__gte=timeBegin) | Q(finish_datetime__isnull=True))

    for taskObj in set(cluster_backup_task):
        rowList.append(_get_cluster_backup_task_detail_info(hostIdent, taskObj, taskselect))

    # 1.RemoteBackupTask 统计
    if taskselect == 1:
        remote_backup_tasks = RemoteBackupTask.objects.filter(schedule__host__ident=hostIdent,
                                                              start_datetime__isnull=False,
                                                              finish_datetime__isnull=True,
                                                              schedule__deleted=False,
                                                              paused=False,
                                                              schedule__enabled=True)
    else:
        remote_backup_tasks = RemoteBackupTask.objects.filter(schedule__host__ident=hostIdent,
                                                              start_datetime__isnull=False).filter(
            Q(finish_datetime__isnull=False) | Q(start_datetime__gte=timeBegin)
        )

    for remote_backup_task in remote_backup_tasks:
        rowList.append(_get_remote_backup_task_detail_info(remote_backup_task, taskselect))

    # 1.VirtualMachineRestoreTask 统计
    if taskselect == 1:  # 当前任务
        vmr_tasks = VirtualMachineRestoreTask.objects.filter(host_snapshot__host__ident=hostIdent,
                                                             start_datetime__isnull=False,
                                                             finish_datetime__isnull=True)
    else:
        vmr_tasks = VirtualMachineRestoreTask.objects.filter(host_snapshot__host__ident=hostIdent,
                                                             start_datetime__isnull=False).filter(
            Q(finish_datetime__isnull=False) | Q(start_datetime__gte=timeBegin)
        )

    for vmr_task in vmr_tasks:
        rowList.append(_get_vmr_task_detail_info(vmr_task, taskselect))

    # 1.archive 统计
    if taskselect == 1:  # 当前任务
        archive_tasks = ArchiveTask.objects.filter(host_snapshot__host__ident=hostIdent,
                                                   start_datetime__isnull=False,
                                                   finish_datetime__isnull=True)
    else:
        archive_tasks = ArchiveTask.objects.filter(host_snapshot__host__ident=hostIdent,
                                                   start_datetime__isnull=False).filter(
            Q(finish_datetime__isnull=False) | Q(start_datetime__gte=timeBegin)
        )

    for archive_task in archive_tasks:
        rowList.append(_get_archive_task_detail_info(archive_task, taskselect))

    # 2.import archive 统计
    if taskselect == 1:  # 当前任务
        import_snapshot_tasks = ImportSnapshotTask.objects.filter(host_snapshot__host__ident=hostIdent,
                                                                  start_datetime__isnull=False,
                                                                  finish_datetime__isnull=True)
    else:
        import_snapshot_tasks = ImportSnapshotTask.objects.filter(host_snapshot__host__ident=hostIdent,
                                                                  start_datetime__isnull=False).filter(
            Q(finish_datetime__isnull=False) | Q(start_datetime__gte=timeBegin)
        )

    for import_snapshot_task in import_snapshot_tasks:
        rowList.append(_get_import_snapshot_task_detail_info(import_snapshot_task, taskselect))

    # file backup统计
    if taskselect == 1:  # 当前任务
        file_backup_tasks = FileBackupTask.objects.filter(host_snapshot__host__ident=hostIdent,
                                                          start_datetime__isnull=False,
                                                          finish_datetime__isnull=True)
    else:
        file_backup_tasks = FileBackupTask.objects.filter(host_snapshot__host__ident=hostIdent,
                                                          start_datetime__isnull=False).filter(
            Q(finish_datetime__isnull=False) | Q(start_datetime__gte=timeBegin)
        )

    for file_backup_task in file_backup_tasks:
        rowList.append(_get_file_backup_task_detail_info(file_backup_task, taskselect))

    # 待审批的任务统计
    if taskselect == 1:  # 当前任务
        audit_tasks = audit_task.objects.filter(create_user=request.user, status=audit_task.AUIDT_TASK_STATUS_WAITE)
    else:
        audit_tasks = audit_task.objects.filter(create_user=request.user,
                                                status__in=(
                                                    audit_task.AUIDT_TASK_STATUS_WAITE,
                                                    audit_task.AUIDT_TASK_STATUS_AGREE,
                                                    audit_task.AUIDT_TASK_STATUS_REFUSE))

    for task in audit_tasks:
        detail_info = _get_audit_task_task_detail_info(task, hostIdent)
        if detail_info:
            rowList.append(detail_info)

    # 正在验证的任务统计
    verify_tasks = AutoVerifyTask.objects.filter(verify_type=AutoVerifyTask.VERIFY_TYPE_ING)

    for task in verify_tasks:
        detail_info = _get_verify_task_task_detail_info(task, hostIdent)
        if detail_info:
            rowList.append(detail_info)

    # 正在浏览备份点的任务
    host_snapshot_shares = HostSnapshotShare.objects.all()
    for host_snapshot_share in host_snapshot_shares:
        detail_info = _get_host_snapshot_share_task_detail_info(host_snapshot_share, hostIdent)
        if detail_info:
            rowList.append(detail_info)

    # 快速验证/接管的任务
    takeover_kvms = TakeOverKVM.objects.filter(Q(kvm_type='temporary_kvm') | Q(kvm_type='forever_kvm')).all()
    for takeover_kvm in takeover_kvms:
        detail_info = _get_takeover_task_detail_info(takeover_kvm, hostIdent)
        if detail_info:
            rowList.append(detail_info)

    # file sync task统计
    if taskselect == 1:  # 当前任务
        file_sync_tasks = FileSyncTask.objects.filter(host_snapshot__host__ident=hostIdent,
                                                      start_datetime__isnull=False,
                                                      finish_datetime__isnull=True)
    else:
        file_sync_tasks = FileSyncTask.objects.filter(host_snapshot__host__ident=hostIdent,
                                                      start_datetime__isnull=False).filter(
            Q(finish_datetime__isnull=False) | Q(start_datetime__gte=timeBegin)
        )

    for file_sync_task in file_sync_tasks:
        rowList.append(_get_file_sync_task_detail_info(file_sync_task, taskselect))

    rowList = sorted(rowList, key=lambda elem: elem['date_time'], reverse=True)
    rowList = [_clear_attr(elem, 'date_time') for elem in rowList]
    return rowList


def _get_vmr_task_detail_info(vmr_task, taskselect):
    icon = 'info'
    ext_info = json.loads(vmr_task.ext_config)
    target_name = ext_info['vmname']
    if vmr_task.finish_datetime and vmr_task.successful:  # 任务完成 且成功
        back_info = '成功'
    elif vmr_task.finish_datetime and not vmr_task.successful:  # 任务完成 且失败
        back_info = '失败'
        icon = 'error'
    else:
        back_info = vmr_task.get_status_display()
        if xdata.CANCEL_TASK_EXT_KEY in ext_info and not vmr_task.finish_datetime:  # 用户取消且任务没有完成
            back_info = '取消中'
        else:
            if vmr_task.status == VirtualMachineRestoreTask.TRANSFER_DATA:
                total_bytes, restored_bytes = ext_info.get('total_bytes', 0), ext_info.get('restored_bytes', 0)
                if total_bytes and restored_bytes:
                    progress_str = '{}/{}({})'.format(
                        functions.format_size(restored_bytes),
                        functions.format_size(total_bytes),
                        functions.format_progress(restored_bytes, total_bytes)
                    )
                    back_info = '{} {}'.format(back_info, progress_str)
                else:
                    pass
            else:
                pass

    result = back_info
    result_tip = result
    if icon == 'error':
        result = '<span style="color:red;">{}</span>'.format(result_tip)
    remote_ip = '目标机IP：{}'.format(target_name)
    if taskselect == 1 or vmr_task.finish_datetime is None:
        li_cancel_func_str = "cancel_task('vm_restore','{}')".format(vmr_task.id)
        li_cancel = CommonUtils.get_li_content('取消', '取消任务', li_cancel_func_str)
    else:
        li_cancel = ''
    li_detail = CommonUtils.get_li_detail('vm_restore', vmr_task.id)
    ul = CommonUtils.get_ul_content(li_detail + li_cancel)
    label = CommonUtils.get_current_label('免代理恢复', str(vmr_task.start_datetime)[0:19], remote_ip, result_tip, result,
                                          CommonUtils.get_op_button(ul))
    info = {'id': 'vm_restore_{}'.format(vmr_task.id), 'label': '{}'.format(label),
            'date_time': vmr_task.start_datetime,
            'icon': icon}
    return info


def _get_remote_backup_task_detail_info(remote_backup_task, taskselect):
    schedule = remote_backup_task.schedule
    src_ip = json.loads(schedule.host.aio_info)['ip']
    src_ip_str = '远端智动全景灾备系统IP：{}'.format(src_ip)
    result_tip = '成功'
    # 取消任务按钮
    if taskselect == 1:
        li_cancel_func_str = "cancel_task('remote_backup_task','{}','{}')".format(schedule.id,
                                                                                  schedule.name)
        li_cancel = CommonUtils.get_li_content('取消', '取消任务', li_cancel_func_str)
    else:
        li_cancel = ''
    li_detail = CommonUtils.get_li_detail('remote_backup_task', remote_backup_task.id)
    ul = CommonUtils.get_ul_content(li_detail + li_cancel)  # 这里的顺序，就是页面显示li的顺序
    inode = False  # 处理sub_info
    sub_nodes = []
    sub_tasks = remote_backup_task.remote_backup_sub_tasks.filter(start_datetime__isnull=False,
                                                                  finish_datetime__isnull=True)
    if sub_tasks.exists():
        inode = True
    for index, sub_task in enumerate(sub_tasks):
        sub_label = _get_remote_sub_task_display_info(sub_task)
        info = {'id': 'remote_backup_task_{}_{}'.format(remote_backup_task.id, index), "inode": False,
                "open": False,
                'label': '{}'.format(sub_label)}
        sub_nodes.append(info)
    if remote_backup_task.finish_datetime:
        if remote_backup_task.successful:
            result = '同步成功'
        else:
            result = '同步失败'
            result_tip = '失败'
    else:
        result = remote_backup_task.get_status_display()
    label = CommonUtils.get_current_label('远程容灾', str(remote_backup_task.start_datetime)[0:19], src_ip_str,
                                          result_tip, result, CommonUtils.get_op_button(ul))
    info = {'id': 'remote_backup_task_{}'.format(remote_backup_task.id), "inode": inode, "open": True,
            'label': '{}'.format(label), 'date_time': remote_backup_task.start_datetime, 'icon': 'info',
            'branch': sub_nodes}
    return info


def _get_cluster_backup_task_detail_info(hostIdent, taskObj, taskselect):
    cluster_host = Host.objects.get(ident=hostIdent)
    icon = 'info'
    scheObj = taskObj.schedule
    result, timeleft, base_finished = get_status_from_cluster_backup_task(taskObj, hostIdent)
    finish_time = taskObj.finish_datetime
    is_successful = taskObj.successful
    if finish_time and is_successful:
        result = '成功'
    if finish_time and not is_successful:
        result = '失败'
        icon = 'error'
    result_tip = result
    if icon == 'error':
        result = '<span style="color:red;">{}</span>'.format(result_tip)
    if taskselect == 1 or taskObj.finish_datetime is None:
        li_cancel_func_str = "cancel_task('cluster','{}','{}')".format(taskObj.id, scheObj.name)
        li_cancel = CommonUtils.get_li_content('取消', '取消任务', li_cancel_func_str)

        li_speed_func_str = "adjust_spend_click('ClusterBackupTask', '{}', '{}')".format(taskObj.id, hostIdent)
        li_speed = CommonUtils.get_li_content('资源限定', '调整当前任务资源限定', li_speed_func_str)
        if base_finished:
            li_speed = ''  # 基础备份完毕的情况下 不能够限速
    else:
        li_speed = ''
        li_cancel = ''

    li_detail = CommonUtils.get_li_detail('cluster', taskObj.id)
    ul = CommonUtils.get_ul_content(li_detail + li_speed + li_cancel)
    label = CommonUtils.get_current_label('集群', str(taskObj.start_datetime)[0:19], scheObj.name,
                                          result_tip, result, CommonUtils.get_op_button(ul), time_left=timeleft)
    info = {'id': 'cluster_backup_{}'.format(taskObj.id), 'label': '{}'.format(label),
            'date_time': taskObj.start_datetime,
            'icon': icon}
    return info


def _get_cdp_task_detail_info(taskObj, taskselect):
    icon = 'info'
    scheObj = taskObj.schedule
    finish_time = taskObj.finish_datetime
    is_successful = taskObj.successful
    _status = 'CDP保护中'
    timeleft = ''
    if finish_time is None:  # 任务进行中(CDP保护中)
        try:
            host_snapshot = taskObj.host_snapshot
            stage = host_snapshot.display_status
            ext_info = json.loads(host_snapshot.ext_info)
            index = ext_info.get('progressIndex', None)
            total = ext_info.get('progressTotal', None)
            if None in [index, total] or index == 0:
                _status = 'CDP准备阶段，{0} {1}'.format(stage, '')
                timeleft = ''
            else:
                if not host_snapshot.host.is_linked:
                    estimated_end_time = 'CDP备份客户端已离线'
                else:
                    estimated_end_time = RestoreBackupOverTime.query_end_time(
                        RestoreBackupOverTime.BACKUP_FLAG + str(host_snapshot.id))
                starttime = taskObj.start_datetime
                timeleft = '{}'.format(estimated_end_time)
                _status = 'CDP准备阶段，{0} {1}'.format(stage, getVirtualProgress(starttime, index, total))

            if host_snapshot.finish_datetime and host_snapshot.successful:  # 基础备份已完成且成功
                _status = 'CDP保护中'
                timeleft = ''
        except HostSnapshot.DoesNotExist:
            _status = 'CDP初始化中'
    if finish_time and is_successful:
        _status = 'CDP暂停'
        icon = 'waring'
    if finish_time and not is_successful:
        _status = 'CDP失败'
        icon = 'error'
    result = _status
    result_tip = result
    if icon == 'error':
        result = '<span style="color:red;">{}</span>'.format(result_tip)
    if taskselect == 1 or taskObj.finish_datetime is None:
        li_cancel_func_str = "cancel_task('CDPTask','{}','{}')".format(taskObj.id, scheObj.name)
        li_cancel = CommonUtils.get_li_content('取消', '取消任务', li_cancel_func_str)

        if taskObj.host_snapshot and taskObj.host_snapshot.finish_datetime is None:
            li_speed_func_str = "adjust_spend_click('CDPTask', '{}', '{}')".format(taskObj.id,
                                                                                   taskObj.host_snapshot.host.ident)
            li_speed = CommonUtils.get_li_content('资源限定', '调整当前任务资源限定', li_speed_func_str)
        else:
            li_speed = ''
    else:
        li_cancel = ''
        li_speed = ''
    li_detail = CommonUtils.get_li_detail('cdp', taskObj.id)
    ul = CommonUtils.get_ul_content(li_detail + li_speed + li_cancel)
    label = CommonUtils.get_current_label('备份', str(taskObj.start_datetime)[0:19], scheObj.name,
                                          result_tip, result, CommonUtils.get_op_button(ul), time_left=timeleft)
    info = {'id': 'cdp_task_{}'.format(taskObj.id), 'label': '{}'.format(label),
            'date_time': taskObj.start_datetime,
            'icon': icon}
    return info


def _get_migrate_task_detail_info(taskObj, taskselect):
    icon = 'info'
    host_name = taskObj.source_host.display_name
    # 生成中或已生成
    host_snapshot = taskObj.host_snapshot
    restore_target = taskObj.restore_target
    restore_target_info = json.loads(restore_target.info)
    total_bytes = restore_target.total_bytes
    restored_bytes = restore_target.restored_bytes
    ext_cfg = json.loads(taskObj.ext_config)
    if None in [total_bytes, restored_bytes]:
        percent = None
    else:
        percent = restored_bytes / total_bytes
    if taskObj.finish_datetime and taskObj.successful:  # 任务完成 且成功
        back_info = '成功'
    elif taskObj.finish_datetime and not taskObj.successful:  # 任务完成 且失败
        back_info = '失败'
        icon = 'error'
    else:
        if percent is None:  # 源备份-KVM前-KVM-目标机启动前
            if host_snapshot.successful:  # 2.KVM前-KVM-目标机启动前
                back_info = '{0}'.format(restore_target.display_status)
                back_info = HTBTaskQuery.wrap_info(restore_target.ident, back_info)
                if restore_target_info.get('initial_linked', False):
                    back_info = '目标客户端已经重启并连接成功'
                else:
                    back_info = back_info
            else:  # 1.源备份
                ext_info = json.loads(host_snapshot.ext_info)
                index = ext_info.get('progressIndex', None)
                total = ext_info.get('progressTotal', None)
                if (index is None) or (total is None):
                    back_info = '{0} {1}'.format('准备迁移数据中', '')
                else:
                    starttime = taskObj.start_datetime
                    back_info = '{0} {1}'.format('准备迁移数据中', getVirtualProgress(starttime, index, total))

        else:  # 3.目标机启动后
            if percent > 0.997 and not taskObj.finish_datetime:
                progress_str = '99.7%'
            else:
                progress_str = '{}/{}({})'.format(
                    functions.format_size(restored_bytes),
                    functions.format_size(total_bytes),
                    functions.format_progress(restored_bytes, total_bytes)
                )
            back_info = '{0} {1}'.format(r'正在迁移数据中', progress_str)

        if xdata.CANCEL_TASK_EXT_KEY in ext_cfg and not taskObj.finish_datetime:  # 用户取消且任务没有完成
            back_info = '取消中'
    result = back_info
    result_tip = result
    if icon == 'error':
        result = '<span style="color:red;">{}</span>'.format(result_tip)
    if taskselect == 1 or taskObj.finish_datetime is None:
        li_cancel_func_str = "cancel_task('migrate','{}')".format(taskObj.id)
        li_cancel = CommonUtils.get_li_content('取消', '取消任务', li_cancel_func_str)

        if taskObj.host_snapshot and taskObj.host_snapshot.finish_datetime is None:
            li_speed_func_str = "adjust_spend_click('MigrateTask', '{}', '{}')".format(taskObj.id,
                                                                                       taskObj.host_snapshot.host.ident)
            li_speed = CommonUtils.get_li_content('资源限定', '调整当前任务资源限定', li_speed_func_str)
        else:
            li_speed = ''
    else:
        li_cancel = ''
        li_speed = ''
    remote_ip = host_name
    if 'remote_ip' in restore_target_info:
        remote_ip = '目标机IP：{}'.format(restore_target_info['remote_ip'])
    if 'master_nic_ips' in restore_target_info:
        remote_ip = '目标机IP：{}'.format('|'.join(restore_target_info['master_nic_ips']))
    li_detail = CommonUtils.get_li_detail('migrate', taskObj.id)
    ul = CommonUtils.get_ul_content(li_detail + li_speed + li_cancel)
    label = CommonUtils.get_current_label('迁移', str(taskObj.start_datetime)[0:19], remote_ip,
                                          result_tip, result, CommonUtils.get_op_button(ul))
    info = {'id': 'migration_task_{}'.format(taskObj.id), 'label': '{}'.format(label),
            'date_time': taskObj.start_datetime,
            'icon': icon}
    return info


def _get_restore_task_detail_info(taskObj, taskselect):
    icon = 'info'
    host_name = taskObj.host_snapshot.host.display_name
    restore_target = taskObj.restore_target
    restore_target_info = json.loads(restore_target.info)
    total_bytes = restore_target.total_bytes
    restored_bytes = restore_target.restored_bytes
    if None in [total_bytes, restored_bytes]:
        percent = None
    else:
        percent = restored_bytes / total_bytes
    ext_info = json.loads(taskObj.ext_config)
    timeleft = ''
    if taskObj.finish_datetime and taskObj.successful:  # 任务完成 且成功
        back_info = '成功'
    elif taskObj.finish_datetime and not taskObj.successful:  # 任务完成 且失败
        back_info = '失败'
        icon = 'error'
    else:
        if percent is None:  # KVM前-KVM-目标机启动前
            back_info = '{0}'.format(restore_target.display_status)
            back_info = HTBTaskQuery.wrap_info(restore_target.ident, back_info)
            if restore_target_info.get('initial_linked', False):
                back_info = '目标客户端已经重启并连接成功'
                timeleft = ''
            else:
                back_info = back_info
                timeleft = ''
        else:  # 目标机启动后：开始传输还原数据
            _logger.info('target_restore_target_info:{}'.format(restore_target_info['host_ident']))
            if percent > 0.997 and not taskObj.finish_datetime:
                progress_str = '99.7%'
            else:
                progress_str = '{}/{}({})'.format(
                    functions.format_size(restored_bytes),
                    functions.format_size(total_bytes),
                    functions.format_progress(restored_bytes, total_bytes)
                )
            host_ident = restore_target_info['host_ident']
            host = check_host_ident_valid(host_ident)
            if not host.is_linked:
                timeleft = '还原客户端已离线'
            else:
                timeleft = RestoreBackupOverTime.query_end_time(
                    RestoreBackupOverTime.RESTORE_FLAG + str(restore_target.id))
                _logger.info('restore_timeleft:{}'.format(timeleft))
            back_info = '{0} {1}'.format(r'正在恢复数据中', progress_str)
        if xdata.CANCEL_TASK_EXT_KEY in ext_info and not taskObj.finish_datetime:  # 用户取消且任务没有完成
            back_info = '取消中'
            timeleft = ''
    result = back_info
    result_tip = result
    if icon == 'error':
        result = '<span style="color:red;">{}</span>'.format(result_tip)
    remote_ip = host_name
    if 'remote_ip' in restore_target_info:
        remote_ip = '目标机IP：{}'.format(restore_target_info['remote_ip'])
    if 'master_nic_ips' in restore_target_info:
        remote_ip = '目标机IP：{}'.format('|'.join(restore_target_info['master_nic_ips']))
    if taskselect == 1 or taskObj.finish_datetime is None:
        li_cancel_func_str = "cancel_task('restore','{}')".format(taskObj.id)
        li_cancel = CommonUtils.get_li_content('取消', '取消任务', li_cancel_func_str)
    else:
        li_cancel = ''

    li_detail = CommonUtils.get_li_detail('restore', taskObj.id)
    ul = CommonUtils.get_ul_content(li_detail + li_cancel)
    label = CommonUtils.get_current_label('恢复', str(taskObj.start_datetime)[0:19], remote_ip,
                                          result_tip, result, CommonUtils.get_op_button(ul), time_left=timeleft)
    info = {'id': 'restore_task_{}'.format(taskObj.id), 'label': '{}'.format(label),
            'date_time': taskObj.start_datetime,
            'icon': icon}
    return info


def _get_backup_task_detail_info(taskObj, taskselect):
    icon = 'info'
    timeleft = ''
    scheObj = taskObj.schedule
    if taskObj.finish_datetime and taskObj.successful:
        back_info = '成功'
    elif taskObj.finish_datetime and not taskObj.successful:
        back_info = '失败'
        icon = 'error'
    else:
        try:
            host_snapshot = taskObj.host_snapshot
            stage = host_snapshot.display_status
            ext_info = json.loads(host_snapshot.ext_info)
            index = ext_info.get('progressIndex', None)
            total = ext_info.get('progressTotal', None)
            if not index or not total:
                back_info = '{0} {1}'.format(stage, '')
                timeleft = ''
            else:
                if not host_snapshot.host.is_linked:
                    estimated_end_time = '备份客户端已离线'
                else:
                    estimated_end_time = RestoreBackupOverTime.query_end_time(
                        RestoreBackupOverTime.BACKUP_FLAG + str(host_snapshot.id))
                starttime = taskObj.start_datetime
                timeleft = '{}'.format(estimated_end_time)
                back_info = '{0} {1}'.format(stage, getVirtualProgress(starttime, index, total))
            if '"{}"'.format(
                    xdata.CANCEL_TASK_EXT_KEY) in taskObj.ext_config and not taskObj.finish_datetime:  # 用户取消且任务没有完成
                back_info = '取消中'
                timeleft = ''
        except HostSnapshot.DoesNotExist:
            back_info = '初始化中'
    result = back_info
    result_tip = result
    if icon == 'error':
        result = '<span style="color:red;">{}</span>'.format(result_tip)
    # 取消任务按钮
    if taskselect == 1 or taskObj.finish_datetime is None:
        li_cancel_func_str = "cancel_task('backup','{}')".format(taskObj.id)
        li_cancel = CommonUtils.get_li_content('取消', '取消任务', li_cancel_func_str)

        if taskObj.host_snapshot and taskObj.host_snapshot.finish_datetime is None:
            li_speed_func_str = "adjust_spend_click('BackupTask', '{}', '{}')".format(taskObj.id,
                                                                                      taskObj.host_snapshot.host.ident)
            li_speed = CommonUtils.get_li_content('资源限定', '调整当前任务资源限定', li_speed_func_str)
        else:
            li_speed = ''

    else:
        li_cancel = ''
        li_speed = ''
    st_time = taskObj.start_datetime.strftime('%Y-%m-%d %H:%M:%S')
    li_detail = CommonUtils.get_li_detail('backup', taskObj.id)
    ul = CommonUtils.get_ul_content(li_detail + li_speed + li_cancel)
    label = CommonUtils.get_current_label('备份', st_time, scheObj.name,
                                          result_tip, result, CommonUtils.get_op_button(ul), time_left=timeleft)
    info = {'id': 'backup_task_{}'.format(taskObj.id), 'label': '{}'.format(label),
            'date_time': taskObj.start_datetime,
            'icon': icon}
    return info


def _get_htb_task_detail_info(htb_task, taskselect):
    htb_schedule = htb_task.schedule
    icon = 'info'
    task_type = htb_schedule.task_type
    rs = HTBTaskQuery(htb_schedule.id).query(htb_task=htb_task)  # 查询状态
    if rs:
        result = rs['status']
        code = rs['code']
        sub_info = rs['sub_info']
    else:
        result = '正在获取数据'
        code = -1
        sub_info = list()
    if code == HTBTask.MISFAIL:
        icon = 'error'
    result_tip = result
    if icon == 'error':
        result = '<span style="color:red;">{}</span>'.format(result_tip)
    if htb_schedule.restore_type == HTBSchedule.HTB_RESTORE_TYPE_SYSTEM:
        dest_host = htb_task.restore_target
        if dest_host is None:
            dest_host_name = htb_schedule.target_info
        else:
            dest_host_name = '{}'.format(dest_host.display_name)
    else:
        dest_host = Host.objects.get(ident=htb_schedule.dst_host_ident)
        dest_host_name = dest_host.name
    dest_host_name = '备机: {}'.format(dest_host_name)
    # 取消任务按钮
    if taskselect == 1 or htb_task.finish_datetime is None and htb_task.status != HTBTask.INITSYS:
        li_cancel_func_str = "cancel_task('htb','{}','{}')".format(htb_schedule.id, htb_schedule.name)
        li_cancel = CommonUtils.get_li_content('取消', '取消任务', li_cancel_func_str)
    else:
        li_cancel = ''
    # 热备切换按钮
    if htb_task.status in [HTBTask.SYNC, HTBTask.VOL_SYNC]:
        switch_change_master_ip = '1' if change_master_ip(htb_task.id) else '0'
        li_switch_func_str = "hotbackupSwitch('{}','{}','{}')".format(htb_schedule.id, task_type,
                                                                      switch_change_master_ip)
        li_switch = CommonUtils.get_li_content('切换', '热备切换', li_switch_func_str)
    else:
        li_switch = ''
    li_detail = CommonUtils.get_li_detail('htb', htb_task.id)
    ul = CommonUtils.get_ul_content(li_detail + li_switch + li_cancel)  # 这里的顺序，就是页面显示li的顺序
    label = CommonUtils.get_current_label('热备', str(htb_task.start_datetime)[0:19], dest_host_name,
                                          result_tip, result, CommonUtils.get_op_button(ul))
    inode = False  # 处理sub_info
    sub_nodes = []
    if len(sub_info) > 0:
        inode = True
        for index, info in enumerate(sub_info):
            sub_label = ''
            for i in info:
                sub_label += i
                sub_label += ' '
            info = {'id': 'htb_task_{}_{}'.format(htb_task.id, index), "inode": False, "open": False,
                    'label': '{}'.format(sub_label)}
            sub_nodes.append(info)
    info = {'id': 'htb_task_{}'.format(htb_task.id), "inode": inode, "open": True,
            'label': '{}'.format(label), 'date_time': rs['start_datetime'], 'icon': icon, 'branch': sub_nodes}
    return info


def _get_archive_task_detail_info(taskObj, taskselect):
    icon = 'info'
    timeleft = ''
    if taskObj.finish_datetime and taskObj.successful:
        back_info = '成功'
    elif taskObj.finish_datetime and not taskObj.successful:
        back_info = '失败'
        icon = 'error'
    else:
        ext_info = json.loads(taskObj.ext_config)
        stage = taskObj.get_status_display()
        index = ext_info.get('progressIndex', None)
        total = ext_info.get('progressTotal', None)
        if not index or not total:
            back_info = '{0} {1}'.format(stage, '')
            timeleft = ''
        else:
            estimated_end_time = RestoreBackupOverTime.query_end_time(
                RestoreBackupOverTime.ARCHIVE_FLAG + str(taskObj.id))
            starttime = taskObj.start_datetime
            timeleft = '{}'.format(estimated_end_time)
            back_info = '{0} {1}'.format(stage, getVirtualProgress(starttime, index, total))
        if '"{}"'.format(
                xdata.CANCEL_TASK_EXT_KEY) in taskObj.ext_config and not taskObj.finish_datetime:  # 用户取消且任务没有完成
            back_info = '取消中'
            timeleft = ''
    result = back_info
    result_tip = result
    if icon == 'error':
        result = '<span style="color:red;">{}</span>'.format(result_tip)
    # 取消任务按钮
    li_speed = ''
    if taskselect == 1 or taskObj.finish_datetime is None:
        li_cancel_func_str = "cancel_task('archive','{}')".format(taskObj.id)
        li_cancel = CommonUtils.get_li_content('取消', '取消任务', li_cancel_func_str)
    else:
        li_cancel = ''
    st_time = taskObj.start_datetime.strftime('%Y-%m-%d %H:%M:%S')
    li_detail = CommonUtils.get_li_detail('archive', taskObj.id)
    ul = CommonUtils.get_ul_content(li_detail + li_speed + li_cancel)
    label = CommonUtils.get_current_label('备份数据导出', st_time, taskObj.host_snapshot.name,
                                          result_tip, result, CommonUtils.get_op_button(ul), time_left=timeleft)
    info = {'id': 'archive_task_{}'.format(taskObj.id), 'label': '{}'.format(label),
            'date_time': taskObj.start_datetime,
            'icon': icon}
    return info


def _get_import_snapshot_task_detail_info(taskObj, taskselect):
    icon = 'info'
    timeleft = ''
    if taskObj.finish_datetime and taskObj.successful:
        back_info = '成功'
    elif taskObj.finish_datetime and not taskObj.successful:
        back_info = '失败'
        icon = 'error'
    else:
        ext_info = json.loads(taskObj.ext_config)
        stage = taskObj.get_status_display()
        index = ext_info.get('progressIndex', None)
        total = ext_info.get('progressTotal', None)
        if not index or not total:
            back_info = '{0} {1}'.format(stage, '')
            timeleft = ''
        else:
            estimated_end_time = RestoreBackupOverTime.query_end_time(
                RestoreBackupOverTime.IMPORT_ARCHIVE_FLAG + str(taskObj.id))
            starttime = taskObj.start_datetime
            timeleft = '{}'.format(estimated_end_time)
            back_info = '{0} {1}'.format(stage, getVirtualProgress(starttime, index, total))
        if '"{}"'.format(
                xdata.CANCEL_TASK_EXT_KEY) in taskObj.ext_config and not taskObj.finish_datetime:  # 用户取消且任务没有完成
            back_info = '取消中'
            timeleft = ''
    result = back_info
    result_tip = result
    if icon == 'error':
        result = '<span style="color:red;">{}</span>'.format(result_tip)
    # 取消任务按钮
    li_speed = ''
    if taskselect == 1 or taskObj.finish_datetime is None:
        li_cancel_func_str = "cancel_task('import_snapshot','{}')".format(taskObj.id)
        li_cancel = CommonUtils.get_li_content('取消', '取消任务', li_cancel_func_str)
    else:
        li_cancel = ''
    st_time = taskObj.start_datetime.strftime('%Y-%m-%d %H:%M:%S')
    li_detail = CommonUtils.get_li_detail('import_snapshot', taskObj.id)
    ul = CommonUtils.get_ul_content(li_detail + li_speed + li_cancel)
    label = CommonUtils.get_current_label('备份数据导入', st_time, taskObj.host_snapshot.name,
                                          result_tip, result, CommonUtils.get_op_button(ul), time_left=timeleft)
    info = {'id': 'import_snapshot_task_{}'.format(taskObj.id), 'label': '{}'.format(label),
            'date_time': taskObj.start_datetime,
            'icon': icon}
    return info


def _get_file_backup_task_detail_info(taskObj, taskselect):
    icon = 'info'
    timeleft = ''
    if taskObj.finish_datetime and taskObj.successful:
        back_info = '成功'
    elif taskObj.finish_datetime and not taskObj.successful:
        back_info = '失败'
        icon = 'error'
    else:
        ext_info = json.loads(taskObj.ext_config)
        stage = taskObj.get_status_display()
        index = ext_info.get('progressIndex', None)
        total = ext_info.get('progressTotal', None)
        if not index or not total:
            back_info = '{0} {1}'.format(stage, '')
            timeleft = ''
        else:
            estimated_end_time = RestoreBackupOverTime.query_end_time(
                RestoreBackupOverTime.BACKUP_FLAG + str(taskObj.id))
            starttime = taskObj.start_datetime
            timeleft = '{}'.format(estimated_end_time)
            back_info = '{0} {1}'.format(stage, getVirtualProgress(starttime, index, total))

        # 修正back_info，当FileBackupTask时
        if isinstance(taskObj, FileBackupTask):
            rsync_status = ext_info.get('rsync_status', {})
            back_info = rsync_status.get('label', '初始化备份代理')

        if '"{}"'.format(
                xdata.CANCEL_TASK_EXT_KEY) in taskObj.ext_config and not taskObj.finish_datetime:  # 用户取消且任务没有完成
            back_info = '取消中'
            timeleft = ''
    result = back_info
    result_tip = result
    if icon == 'error':
        result = '<span style="color:red;">{}</span>'.format(result_tip)
    # 取消任务按钮
    li_speed = ''
    if taskselect == 1 or taskObj.finish_datetime is None:
        li_cancel_func_str = "cancel_task('file_backup_snapshot','{}')".format(taskObj.id)
        li_cancel = CommonUtils.get_li_content('取消', '取消任务', li_cancel_func_str)
        if taskObj.host_snapshot and taskObj.host_snapshot.finish_datetime is None:
            li_speed_func_str = "adjust_spend_click('FileBackupTask', '{}', '{}')".format(taskObj.id,
                                                                                          taskObj.host_snapshot.host.ident)
            li_speed = CommonUtils.get_li_content('资源限定', '调整当前任务资源限定', li_speed_func_str)
        else:
            li_speed = ''
    else:
        li_cancel = ''
    st_time = taskObj.start_datetime.strftime('%Y-%m-%d %H:%M:%S')
    li_detail = CommonUtils.get_li_detail('file_backup_snapshot', taskObj.id)
    ul = CommonUtils.get_ul_content(li_detail + li_speed + li_cancel)
    label = CommonUtils.get_current_label('文件备份', st_time, taskObj.host_snapshot.name,
                                          result_tip, result, CommonUtils.get_op_button(ul), time_left=timeleft)
    info = {'id': 'import_snapshot_task_{}'.format(taskObj.id), 'label': '{}'.format(label),
            'date_time': taskObj.start_datetime,
            'icon': icon}
    return info


def _get_audit_task_task_detail_info(taskObj, hostIdent):
    icon = 'info'
    timeleft = ''
    # 取消任务按钮
    if taskObj.status == audit_task.AUIDT_TASK_STATUS_WAITE:
        li_cancel_func_str = "cancel_task('audit','{}')".format(taskObj.id)
        li_cancel = CommonUtils.get_li_content('取消', '取消任务', li_cancel_func_str)
        result_tip = '等待审批'
        result = '等待审批'
    else:
        li_cancel = ''
        result_tip = '审批完成'
        result = '审批完成'
    task_info_obj = json.loads(taskObj.task_info)
    task_type, host_snapshot_id = get_approved_task_host_snapshot_id(task_info_obj)
    assert (task_type)
    st_time = taskObj.create_datetime
    ul = CommonUtils.get_ul_content(li_cancel)
    host_snapshot_obj = HostSnapshot.objects.filter(id=host_snapshot_id)
    if host_snapshot_obj:
        host_snapshot_obj = host_snapshot_obj.first()
        if host_snapshot_obj.host.ident != hostIdent:
            return None
        host_snapshot_name = host_snapshot_obj.name
    else:
        _logger.error('_get_audit_task_task_detail_info host_snapshot_id={} not find .ignore.'.host_snapshot_id)
        return None
    label = CommonUtils.get_current_label(task_type, st_time, host_snapshot_name,
                                          result_tip, result, CommonUtils.get_op_button(ul), time_left=timeleft)
    info = {'id': 'audit_task_{}'.format(taskObj.id), 'label': '{}'.format(label),
            'date_time': taskObj.create_datetime,
            'icon': icon}
    return info


def _get_verify_task_task_detail_info(taskObj, hostIdent):
    icon = 'info'
    timeleft = ''
    # 取消任务按钮
    result_tip = '正在验证'
    result = '正在验证'

    st_time = taskObj.created
    host_snapshot_id = taskObj.point_id.split('|')[1]
    host_snapshot_obj = HostSnapshot.objects.filter(id=host_snapshot_id)
    if host_snapshot_obj:
        host_snapshot_obj = host_snapshot_obj.first()
        if host_snapshot_obj.host.ident != hostIdent:
            return None
        li_cancel_func_str = "verify_task_report('{}')".format(host_snapshot_obj.host.ident)
        li_cancel = CommonUtils.get_li_content('验证报告', '验证报告', li_cancel_func_str)
        ul = CommonUtils.get_ul_content(li_cancel)
        host_snapshot_name = host_snapshot_obj.name
    else:
        _logger.error('_get_verify_task_task_detail_info host_snapshot_id={} not find .ignore.'.host_snapshot_id)
        return None
    label = CommonUtils.get_current_label('自动验证', st_time, host_snapshot_name,
                                          result_tip, result, CommonUtils.get_op_button(ul), time_left=timeleft)
    info = {'id': 'verify_task_{}'.format(taskObj.id), 'label': '{}'.format(label), 'date_time': taskObj.created,
            'icon': icon}
    return info


def _get_host_snapshot_share_task_detail_info(taskObj, hostIdent):
    icon = 'info'
    timeleft = ''
    # 取消任务按钮
    result_tip = '文件恢复'
    result = '文件恢复'

    st_time = taskObj.share_start_time

    host_snapshot_id = taskObj.host_snapshot_id
    host_snapshot_obj = HostSnapshot.objects.filter(id=host_snapshot_id)
    if host_snapshot_obj:
        host_snapshot_obj = host_snapshot_obj.first()
        if host_snapshot_obj.host.ident != hostIdent:
            return None
        li_view_func_str = "file_restore_task('{}')".format(taskObj.id)
        li_view = CommonUtils.get_li_content('浏览', '浏览', li_view_func_str)
        li_cancel_func_str = "file_restore_cancel_task('{}')".format(taskObj.id)
        li_cancel = CommonUtils.get_li_content('关闭', '关闭', li_cancel_func_str)
        ul = CommonUtils.get_ul_content(li_view + li_cancel)
        host_snapshot_name = host_snapshot_obj.name
    else:
        _logger.error(
            '_get_host_snapshot_share_task_detail_info host_snapshot_id={} not find .ignore.'.host_snapshot_id)
        return None
    label = CommonUtils.get_current_label('文件恢复', st_time, host_snapshot_name,
                                          result_tip, result, CommonUtils.get_op_button(ul), time_left=timeleft)
    info = {'id': 'file_restore_{}'.format(taskObj.id), 'label': '{}'.format(label),
            'date_time': taskObj.share_start_time,
            'icon': icon}
    return info


def _get_takeover_task_detail_info(taskObj, hostIdent):
    icon = 'info'
    timeleft = ''
    # 取消任务按钮
    result_tip = '详细'
    result = ''

    if taskObj.kvm_run_start_time:
        st_time = taskObj.kvm_run_start_time
    else:
        st_time = timezone.now() - datetime.timedelta(days=7)

    host_snapshot_id = taskObj.host_snapshot.id
    host_snapshot_obj = HostSnapshot.objects.filter(id=host_snapshot_id)
    if host_snapshot_obj:
        host_snapshot_obj = host_snapshot_obj.first()
        if host_snapshot_obj.host.ident != hostIdent:
            return None
        li_more_func_str = "takeover_task('{}')".format(0)
        li_more = CommonUtils.get_li_content('更多', '更多', li_more_func_str)

        ul = CommonUtils.get_ul_content(li_more)
        host_snapshot_name = host_snapshot_obj.name
    else:
        _logger.error(
            '_get_host_snapshot_share_task_detail_info host_snapshot_id={} not find .ignore.'.host_snapshot_id)
        return None
    if taskObj.kvm_type == 'temporary_kvm':
        kvm_type = '快速验证'
    else:
        kvm_type = '接管'
    label = CommonUtils.get_current_label(kvm_type, st_time, host_snapshot_name,
                                          result_tip, result, CommonUtils.get_op_button(ul), time_left=timeleft)
    info = {'id': 'takeover_{}'.format(taskObj.id), 'label': '{}'.format(label),
            'date_time': st_time,
            'icon': icon}
    return info


def get_status_from_cluster_backup_task(task_obj, host_ident):
    cdp_task_objs = CDPTask.objects.filter(cluster_task=task_obj, host_snapshot__isnull=False,
                                           host_snapshot__host__ident=host_ident)
    timeleft = ''
    base_finished = False
    if cdp_task_objs.count() == 0:
        _status = r'准备阶段'
    elif cdp_task_objs.count() == 1:
        host_snapshot = cdp_task_objs.first().host_snapshot
        stage = host_snapshot.display_status
        ext_info = json.loads(host_snapshot.ext_info)
        index = ext_info.get('progressIndex', None)
        total = ext_info.get('progressTotal', None)
        starttime = task_obj.start_datetime
        if None in [index, total] or index == 0:
            _status = '准备阶段，{0} {1}'.format(stage, '')
        else:
            if not host_snapshot.host.is_linked:
                estimated_end_time = '集群备份客户端已离线'
            else:
                estimated_end_time = RestoreBackupOverTime.query_end_time(
                    RestoreBackupOverTime.BACKUP_FLAG + str(host_snapshot.id))
            timeleft = '{}'.format(estimated_end_time)
            _status_info = xdata.get_type_name(STATUS_MAP, task_obj.status_info)
            if _status_info and task_obj.status_info not in ('CctBaseBackup', 'CctSendBackupCmd'):
                _status = _status_info
                base_finished = True
            else:
                if host_snapshot.finish_datetime:
                    _status = '基础数据传输完毕'
                    base_finished = True
                else:
                    _status = '{0} {1}'.format(stage, getVirtualProgress(starttime, index, total))

    else:
        _status = task_obj.run_status()

    return _status, timeleft, base_finished


def _get_file_sync_task_detail_info(taskObj, taskselect):
    icon = 'info'
    timeleft = ''
    if taskObj.finish_datetime and taskObj.successful:
        back_info = '成功'
    elif taskObj.finish_datetime and not taskObj.successful:
        back_info = '失败'
        icon = 'error'
    else:
        ext_info = json.loads(taskObj.ext_config)
        stage = taskObj.get_status_display()
        status_str = ext_info.get('status_str', '')
        status_human = ext_info.get('status_human', '')
        if status_str and status_human:
            stage = status_human
        index = ext_info.get('progressIndex', None)
        total = ext_info.get('progressTotal', None)
        if not index or not total:
            back_info = '{0} {1}'.format(stage, '')
            timeleft = ''
        else:
            estimated_end_time = RestoreBackupOverTime.query_end_time(
                RestoreBackupOverTime.BACKUP_FLAG + str(taskObj.id))
            starttime = taskObj.start_datetime
            timeleft = '{}'.format(estimated_end_time)
            back_info = '{0} {1}'.format(stage, getVirtualProgress(starttime, index, total))

        if '"{}"'.format(
                xdata.CANCEL_TASK_EXT_KEY) in taskObj.ext_config and not taskObj.finish_datetime:  # 用户取消且任务没有完成
            back_info = '取消中'
            timeleft = ''
    result = back_info
    result_tip = result
    if icon == 'error':
        result = '<span style="color:red;">{}</span>'.format(result_tip)
    # 取消任务按钮
    li_speed = ''
    if taskselect == 1 or taskObj.finish_datetime is None:
        li_cancel_func_str = "cancel_task('file_sync','{}')".format(taskObj.id)
        li_cancel = CommonUtils.get_li_content('取消', '取消任务', li_cancel_func_str)
    else:
        li_cancel = ''
    st_time = taskObj.start_datetime.strftime('%Y-%m-%d %H:%M:%S')
    li_detail = CommonUtils.get_li_detail('file_sync', taskObj.id)
    ul = CommonUtils.get_ul_content(li_detail + li_speed + li_cancel)
    label = CommonUtils.get_current_label('文件归档', st_time, taskObj.host_snapshot.name,
                                          result_tip, result, CommonUtils.get_op_button(ul), time_left=timeleft)
    info = {'id': 'file_sync_task_{}'.format(taskObj.id), 'label': '{}'.format(label),
            'date_time': taskObj.start_datetime,
            'icon': icon}
    return info


# 用户当前存储容量状态
def getStorageInfo(request):
    ret_info = {"r": 0, "e": "操作成功", 'orgstorage': '', 'repeatrate': '', 'dataspace': ''}

    user_id = request.user.id
    node_id = request.GET['storagedevice']
    if node_id == '-1':
        return HttpResponse(json.dumps({"r": 0, "e": "操作成功"}, ensure_ascii=False))

    used_mb = storage.user_used_size_mb_in_a_node(node_id, user_id)
    user_quota = UserQuota.objects.filter(storage_node_id=node_id, user_id=user_id).first().quota_size

    if user_quota == xdata.USER_QUOTA_IS_NOT_LIMIT_VALUE:
        node_detail = UserQuotaTools.get_storage_node_detail(node_id=int(node_id), refresh_device=False)
        total_size_gb = int(node_detail['total_bytes'] / 1024 / 1024)
    else:
        total_size_gb = user_quota

    ret_info['totalstorage'] = '{0:.2f}GB'.format(total_size_gb / 1024)
    ret_info['usedspace'] = '{0:.2f}GB'.format(used_mb / 1024)
    ret_info['usagerate'] = '{0:.2f}%'.format(used_mb / total_size_gb * 100)

    if not storage.is_user_has_cdp_plan_in_a_node(node_id, user_id):  # 仅有普通备份时
        original_data_mb = storage.user_norm_backup_total_original_size_mb_in_node(node_id, user_id)
        if original_data_mb and 0 < used_mb < original_data_mb:
            ret_info['orgstorage'] = '{0:.2f}GB'.format(original_data_mb / 1024)
            ret_info['repeatrate'] = '{0:.2f}%'.format((1 - used_mb / original_data_mb) * 100)

    return HttpResponse(json.dumps(ret_info, ensure_ascii=False))


# 从集合中获取指定数目的元素
def _get_constant_elems(elems_list, basic_number):
    elem_nums = len(elems_list)
    step = int(elem_nums / basic_number)

    if elem_nums <= basic_number:
        return elems_list

    result = list()
    for index in range(elem_nums):
        if index % step == 0:
            result.append(elems_list[index])
    return result


def _MB2GBp3(mb):
    return float('{0:.3f}'.format(mb / 1024))


def _Byte2GBp2(_bytes):
    return float('{0:.2f}'.format(_bytes / 1024 ** 3))


def _keep2p(float_num):
    return float('{0:.2f}'.format(float_num))


# 用户配额容量状态(历史)
def getStorageChart(request):
    ret_info = {'r': 0, 'e': '操作成功', 'nodes': []}
    beginT = datetime.datetime.strptime(request.GET['starttime'], '%Y-%m-%d')  # Query: 2016-9-1 -- 2016-9-3
    endT = datetime.datetime.strptime(request.GET['endtime'], '%Y-%m-%d') + datetime.timedelta(days=1)
    user_quotas = UserQuota.objects.filter(user_id=request.user.id, deleted=False)  # 2016-9-1 0:0:0 -- 2016-9-4 0:0:0
    for quota_obj in user_quotas:
        _is_link = True
        node_obj = quota_obj.storage_node
        node_tools = UserQuotaTools(node_obj.id, request.user.id, quota_obj.quota_size)

        # 获取最新状态，作末尾元素
        try:
            free_bytes_cur = node_tools.get_user_available_storage_size_in_node() * 1024 ** 2
            used_bytes_cur = storage.user_used_size_mb_in_a_node(node_obj.id, request.user.id) * 1024 ** 2
            raw_data_bytes_cur = storage.user_raw_data_bytes_in_a_node(node_obj.id, request.user.id)
        except Exception as e:
            _logger.warning('RecordQuotaSpace Exception: {0}'.format(e))
            free_bytes_cur, used_bytes_cur, raw_data_bytes_cur, _is_link = 0, 0, 0, False

        # 某个配额，在对应存储单元上，容量空间历史统计记录
        quota_rcds = UserQuotaSpace.objects.filter(quota_id=quota_obj.id, date_time__gte=beginT, date_time__lt=endT)
        quota_rcds = _get_constant_elems(list(quota_rcds), 200)
        history = [{'year': str(obj.date_time)[:19], 'used': _Byte2GBp2(obj.used_bytes),
                    'free': _Byte2GBp2(obj.free_bytes), 'raw': _Byte2GBp2(obj.raw_data_bytes)} for obj in quota_rcds]

        # 确定表的时间范围：设定首尾时间(零值)
        history.append({'year': str(beginT), 'used': 0, 'free': 0, 'raw': 0})
        history.append({'year': str(endT), 'used': 0, 'free': 0, 'raw': 0})

        # 若结束时间为当天、最新数据有效，则追加最新点
        added_now = False
        end_date = datetime.datetime.strptime(request.GET['endtime'], '%Y-%m-%d').date()
        now_date = timezone.now().date()
        if end_date == now_date and _is_link:
            history.append({'year': str(timezone.now())[:19], 'used': _Byte2GBp2(used_bytes_cur),
                            'free': _Byte2GBp2(free_bytes_cur), 'raw': _Byte2GBp2(raw_data_bytes_cur)})
            added_now = True

        # 作UI截断处理：存在元素，则首尾添加时间(零值)
        if quota_rcds:
            first_elem_time = quota_rcds[0].date_time
            last_elem_time = timezone.now() if added_now else quota_rcds[-1].date_time
            history.append({'year': str(first_elem_time - timedelta(seconds=1))[:19], 'used': 0, 'free': 0, 'raw': 0})
            history.append({'year': str(last_elem_time + timedelta(seconds=1))[:19], 'used': 0, 'free': 0, 'raw': 0})

        # 描述一个存储单元（用户配额）：单元名称，用户配额，目前已用，目前可用，目前RAW，历史记录
        quota_GB = -1 if quota_obj.quota_size in [-1, 0] else _Byte2GBp2(quota_obj.quota_size * 1024 ** 2)
        _node = {'name': node_obj.name, 'total': quota_GB, 'used': _Byte2GBp2(used_bytes_cur),
                 'free': _Byte2GBp2(free_bytes_cur), 'raw': _Byte2GBp2(raw_data_bytes_cur), 'list': history}
        ret_info['nodes'].append(_node)

    return HttpResponse(json.dumps(ret_info))


def getBandwidthChart(request):
    start_date = request.GET['starttime']
    begin_data_time = datetime.datetime.strptime(start_date, '%Y-%m-%d')
    end_data_time = begin_data_time + datetime.timedelta(days=1)
    net_rx_tx = DeviceRunState.objects.filter(datetime__gt=begin_data_time, datetime__lte=end_data_time,
                                              type=DeviceRunState.TYPE_NETWORK_IO).all()
    ret_info = {"r": "0", "e": "操作成功", "list": []}
    cyc_times = 0
    temp_list = list()
    ret_info['list'].append({"hour": str(begin_data_time.date()) + ' 00:00:00'})
    for net_io in net_rx_tx:
        cyc_times += 1
        p = {'hour': str(net_io.datetime)[:19], 'RX': int(net_io.readvalue / 1024), 'TX': int(net_io.writevalue / 1024)}
        temp_list.append(p)
        if cyc_times % 5 == 0:
            center1 = list([i["RX"] for i in temp_list])
            value1 = (min(center1) + max(center1)) / 2

            center2 = list([i["TX"] for i in temp_list])
            value2 = (min(center2) + max(center2)) / 2
            temp_list = list()
            p['RX'] = _keep2p(value1 * 8 / 1024)
            p['TX'] = _keep2p(value2 * 8 / 1024)
            ret_info['list'].append(p)
    else:
        if cyc_times % 5 != 0:
            temp_list = [{'hour': p['hour'], 'RX': _keep2p(p['RX'] * 8 / 1024), 'TX': _keep2p(p['TX'] * 8 / 1024)}
                         for p in temp_list]
            ret_info['list'].extend(temp_list)
    ret_info['list'].append({"hour": str(begin_data_time.date()) + ' 23:59:59'})
    return HttpResponse(json.dumps(ret_info, ensure_ascii=False))


def getIOChart(request):
    jsonstr = {"r": 0, "e": "操作成功", "list": []}
    starttime = request.GET.get('starttime')
    starttime = datetime.datetime.strptime(starttime, '%Y-%m-%d').date()
    endtime = starttime + datetime.timedelta(1)
    data = DeviceRunState.objects.filter(datetime__gte=starttime, datetime__lte=endtime,
                                         type=DeviceRunState.TYPE_DISK_IO).all()
    cyc_times = 0
    temp_list = list()
    jsonstr['list'].append({"hour": str(starttime) + ' 00:00:00'})
    for perdata in data:
        cyc_times += 1
        hour = perdata.datetime
        hour = str(hour).split('.')[0]
        value1 = perdata.writevalue / (1024 * 1024)
        value1 = float('%0.1f' % value1)
        value2 = perdata.readvalue / (1024 * 1024)
        value2 = float('%0.1f' % value2)
        temp_list.append({"hour": hour, "value1": value1, "value2": value2})
        if cyc_times % 5 == 0:
            center1 = list([i["value1"] for i in temp_list])
            value1 = (min(center1) + max(center1)) / 2

            center2 = list([i["value2"] for i in temp_list])
            value2 = (min(center2) + max(center2)) / 2
            temp_list = list()
            jsonstr['list'].append({"hour": hour, "value1": float('%0.1f' % value1), "value2": float('%0.1f' % value2)})
    else:
        if cyc_times % 5 != 0:
            jsonstr['list'].extend(temp_list)
    jsonstr['list'].append({"hour": str(starttime) + ' 23:59:59'})
    jsonstr = json.dumps(jsonstr, ensure_ascii=False)
    return HttpResponse(jsonstr)


def getrecentlyiochart(request):
    hour = request.GET.get('hour')
    jsonstr = {"r": 0, "e": "操作成功", "list": []}
    nowdatetime = datetime.datetime.now()
    starttime = nowdatetime - datetime.timedelta(hours=int(hour))
    data = DeviceRunState.objects.filter(datetime__gte=starttime,
                                         type=DeviceRunState.TYPE_DISK_IO,
                                         datetime__lte=nowdatetime).all()
    for perdata in data:
        hour = perdata.datetime
        hour = str(hour).split('.')[0]
        value1 = perdata.writevalue / (1024 * 1024)
        value1 = float('%0.1f' % value1)
        value2 = perdata.readvalue / (1024 * 1024)
        value2 = float('%0.1f' % value2)
        jsonstr['list'].append({"hour": hour, "value1": float('%0.1f' % value1), "value2": float('%0.1f' % value2)})
    jsonstr = json.dumps(jsonstr, ensure_ascii=False)
    return HttpResponse(jsonstr)


def get_task_labels(request):
    host_idents = request.POST.get('need_update_ids')
    queue_uuid = request.POST.get('quene_uuid')
    time_begin = request.POST.get('starttime')
    tree_select = 1
    rs_dicts = dict()
    rs_dicts['uuid'] = queue_uuid
    rs_dicts['data'] = {}
    for host_indent in json.loads(host_idents):
        re_list = list()
        rs = get_host_task_status(request, host_indent, re_list, tree_select, time_begin)
        rs_dicts['data'][host_indent] = rs
    return HttpResponse(json.dumps(rs_dicts, ensure_ascii=False))


def recentlybandwidthtimechart(request):
    hour = request.GET.get('hour')
    jsonstr = {"r": 0, "e": "操作成功", "list": []}
    nowdatetime = datetime.datetime.now()
    starttime = nowdatetime - datetime.timedelta(hours=int(hour))
    data = DeviceRunState.objects.filter(datetime__gte=starttime,
                                         type=DeviceRunState.TYPE_NETWORK_IO,
                                         datetime__lte=nowdatetime).all()
    for perdata in data:
        p = {'hour': str(perdata.datetime)[:19], 'RX': int(perdata.readvalue * 8 / 1024 ** 2),
             'TX': int(perdata.writevalue * 8 / 1024 ** 2)}
        jsonstr['list'].append(p)
    jsonstr = json.dumps(jsonstr, ensure_ascii=False)
    return HttpResponse(jsonstr)


def get_spend(request):
    request_params = request.GET
    task_id = request_params['task_id']
    class_name = request_params['class_name']
    host_ident = request_params['host_ident']
    jsonstr = {"r": 0, "e": "操作成功", }
    task_obj = eval(class_name).objects.get(id=task_id)
    if isinstance(task_obj, FileBackupTask):
        jsonstr['spend'] = json.loads(task_obj.schedule.ext_config).get('net_limit', -1)
        return HttpResponse(json.dumps(jsonstr, ensure_ascii=False))
    input_js = {
        'type': 'TrafficControl',
        'KiloBytesPerSecond': 1
    }

    try:
        rs = json.loads(boxService.box_service.getBackupInfo(host_ident, json.dumps(input_js)))
    except Exception as e:
        _logger.error('get_spend error:{}'.format(e))
        jsonstr['r'] = 1
        jsonstr['e'] = '获取主机带宽限制失败。'
        return HttpResponse(json.dumps(jsonstr, ensure_ascii=False))

    jsonstr['spend'] = -1 if rs['KiloBytesPerSecond'] == -1 else int(rs['KiloBytesPerSecond'] * 8 / 1024)

    # BackupTask, CDPtask, cluserbackuptask, migrattask
    if isinstance(task_obj, (BackupTask, CDPTask, ClusterBackupTask)):
        ext_config = json.loads(task_obj.schedule.ext_config)
        BackupIOPercentage = ext_config.get('BackupIOPercentage', 30)
    elif isinstance(task_obj, MigrateTask):
        ext_config = json.loads(task_obj.ext_config)
        BackupIOPercentage = ext_config.get('BackupIOPercentage', 30)
    else:
        BackupIOPercentage = -1

    jsonstr['BackupIOPercentage'] = BackupIOPercentage

    return HttpResponse(json.dumps(jsonstr, ensure_ascii=False))


def set_spend(request):
    request_params = request.GET
    host_ident = request_params['host_ident']
    r_spend = request_params['spend']
    task_id = request_params['task_id']
    class_name = request_params['class_name']
    task_obj = eval(class_name).objects.get(id=task_id)
    jsonstr = {"r": 0, "e": "操作成功", }
    if isinstance(task_obj, FileBackupTask):
        info = json.loads(task_obj.schedule.ext_config)
        info['net_limit'] = int(r_spend)
        task_obj.schedule.ext_config = json.dumps(info)
        task_obj.schedule.save()
        return HttpResponse(json.dumps(jsonstr, ensure_ascii=False))
    BackupIOPercentage = request_params['BackupIOPercentage']
    spend = -1 if int(r_spend) == -1 else int(r_spend) * 1024 / 8

    input_js = {
        'type': 'TrafficControl',
        'KiloBytesPerSecond': int(spend)
    }

    try:
        boxService.box_service.setBackupInfo(host_ident, json.dumps(input_js))
    except Exception as e:
        _logger.error('set_spend error:{}'.format(e))
        jsonstr['r'] = 1
        jsonstr['e'] = '设置主机带宽限制失败。'
        return HttpResponse(json.dumps(jsonstr, ensure_ascii=False))

    try:
        boxService.box_service.JsonFuncV2(host_ident, json.dumps({'type': 'update_io_ctl'}), None)
    except Exception as e:
        _logger.warning('notify host fail {}'.format(e))

    if hasattr(task_obj, 'schedule'):
        schedule = task_obj.schedule
        ext_config = json.loads(schedule.ext_config)
        ext_config['maxBroadband'] = int(r_spend)
        ext_config['BackupIOPercentage'] = int(BackupIOPercentage)
        if isinstance(task_obj, (BackupTask, CDPTask)):
            resp = BackupTaskScheduleSetting().put(request=request, backup_task_schedule_id=schedule.id,
                                                   api_request={'ext_config': json.dumps(ext_config)})
        elif isinstance(task_obj, ClusterBackupTask):
            full_params = json.loads(ext_config['FullParamsJsonStr'])
            full_params['max_network_Mb'] = {'value': ext_config['maxBroadband'],
                                             'label': '不限制' if ext_config['maxBroadband'] == -1 else '{}Mbit/s'.format(
                                                 ext_config['maxBroadband'])}
            full_params['BackupIOPercentage'] = {'value': ext_config['BackupIOPercentage'],
                                                 'label': '{}%'.format(ext_config['BackupIOPercentage'])}
            ext_config['FullParamsJsonStr'] = json.dumps(full_params)
            resp = ClusterBackupScheduleManager().put(request, {'ext_config': json.dumps(ext_config)}, schedule.id)
        else:
            resp = None
        desc = {'操作': '更改占用源主机带宽限制为:{}、存储性能为:{}%'.format(
            '不限制' if ext_config['maxBroadband'] == -1 else '{}Mbit/s'.format(ext_config['maxBroadband']),
            ext_config['BackupIOPercentage']),
            '任务ID': task_obj.schedule.id, '计划名称': task_obj.schedule.name}
        if resp and status.is_success(resp.status_code):
            logserver.SaveOperationLog(
                request.user, OperationLog.TYPE_BACKUP, json.dumps(desc, ensure_ascii=False), get_operator(request))
    elif isinstance(task_obj, MigrateTask):
        ext_config = json.loads(task_obj.ext_config)
        ext_config['maxBroadband'] = int(r_spend)
        ext_config['BackupIOPercentage'] = int(BackupIOPercentage)
        task_obj.ext_config = json.dumps(ext_config)
        task_obj.save(update_fields=['ext_config'])
        desc = {'操作': '更改占用源主机带宽限制为:{}、存储性能为:{}%'.format(
            '不限制' if ext_config['maxBroadband'] == -1 else '{}Mbit/s'.format(ext_config['maxBroadband']),
            ext_config['BackupIOPercentage']),
            '任务ID': task_obj.id, '计划名称': task_obj.name}
        logserver.SaveOperationLog(
            request.user, OperationLog.TYPE_MIGRATE, json.dumps(desc, ensure_ascii=False), get_operator(request))

    return HttpResponse(json.dumps(jsonstr, ensure_ascii=False))


def home_handler(request):
    a = request.GET.get('a', 'none')
    if a == 'none':
        a = request.POST.get('a', 'none')
    if a == 'storageinfo':
        return getStorageInfo(request)
    if a == 'storagechart':
        return getStorageChart(request)
    if a == 'taskstatuslist':
        return getStatusList(request)
    if a == 'bandwidthtimechart':
        return getBandwidthChart(request)
    if a == 'getiochart':
        return getIOChart(request)
    if a == 'getrecentlyiochart':
        return getrecentlyiochart(request)
    if a == 'recentlybandwidthtimechart':
        return recentlybandwidthtimechart(request)
    if a == 'cancel_task':
        return cancel_task(request)
    if a == 'get_task_labels':
        return get_task_labels(request)
    if a == 'get_spend':
        return get_spend(request)
    if a == 'set_spend':
        return set_spend(request)
    return HttpResponse(json.dumps({"r": "1", "e": "没有对应的action:{}".format(html.escape(a))}))
