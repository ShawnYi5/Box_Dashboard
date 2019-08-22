# coding=utf-8
import datetime
import html
import json
import os
import re
import time
import zipfile
from django.db.models import Q
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status

from apiv1.database_space_alert import is_database_full
from apiv1.models import (BackupTask, MigrateTask, HTBTask, RestoreTask, CDPTask, BackupTaskSchedule, ClusterBackupTask,
                          VirtualMachineRestoreTask, RemoteBackupTask, FileBackupTask, FileSyncTask)
from apiv1.models import Host
from apiv1.models import HostLog, ArchiveTask
from box_dashboard import boxService
from box_dashboard import xlogging, xdata, xdatetime
from xdashboard.models import OperationLog
from xdashboard.request_util import get_operator
from .xlwt.Workbook import *

_logger = xlogging.getLogger(__name__)


@xlogging.convert_exception_to_value('')
def fmt_detail_error_form_debug(debug):
    debug_msg = ''
    if debug is None:
        return ''
    if not isinstance(debug, str):
        return ''
    p = re.compile('[\s\S]*ImgWrapper[\s\S]*0xffffffffffffffe4[\s\S]*')
    if p.match(debug):
        return '\r\n存储空间满'

    return debug_msg


def FmtDesc(servername, event, desc):
    jsonobj = None
    if desc:
        jsonobj = json.loads(desc)
    if event in ('连接', '断开'):
        from .version import getProductName
        return '{}与{}{}'.format(servername, getProductName(), event)
    if 'description' in jsonobj:
        return jsonobj['description'] + fmt_detail_error_form_debug(jsonobj.get('debug'))
    return servername + event


def getLogbyObj(itemObject, isdebug):
    _type = ''
    host = get_object_or_404(Host, id=itemObject.host_id)
    time = str(itemObject.datetime)[0:19] if itemObject.datetime is not None else '--'
    event = itemObject.get_type_display()
    if isdebug:
        desc = itemObject.reason
    else:
        desc = FmtDesc(host.name, event, itemObject.reason)
    if int(itemObject.type) in (
            1, 2, 4, 5, 7, 8, 10, 11, 14, 15, 16, 18, 19, 20,
            HostLog.LOG_CLUSTER_BACKUP_START,
            HostLog.LOG_CLUSTER_BACKUP_BASE,
            HostLog.LOG_CLUSTER_BACKUP_ANALYZE,
            HostLog.LOG_CLUSTER_BACKUP_SNAPSHOT,
            HostLog.LOG_CLUSTER_BACKUP_SUCCESSFUL,
            HostLog.LOG_CDP_BASE_FINISHED,
            HostLog.LOG_REMOTE_BACKUP_NORM_START,
            HostLog.LOG_REMOTE_BACKUP_NORM_SUCCESSFUL,
            HostLog.LOG_REMOTE_BACKUP_CDP_START,
            HostLog.LOG_REMOTE_BACKUP_CDP_END,
            HostLog.LOG_REMOTE_BACKUP,
            HostLog.LOG_VMWARE_RESTORE,
            HostLog.LOG_ARCHIVE_EXPORT,
            HostLog.LOG_ARCHIVE_IMPORT,
            HostLog.LOG_AUTO_VERIFY_TASK_SUCCESSFUL,
            HostLog.LOG_AUDIT,
            HostLog.LOG_CLUSTER_CDP,
            HostLog.LOG_FILE_SYNC,

    ):
        _type = '信息'
    if int(itemObject.type) in (
            0, 3, 6, 9, 12, 17,
            HostLog.LOG_CLUSTER_BACKUP_FAILED,
            HostLog.LOG_REMOTE_BACKUP_NORM_FAILED,
            HostLog.LOG_AUTO_VERIFY_TASK_FAILED,
    ):
        _type = '错误'
    if int(itemObject.type) in (13,):
        _type = '警告'
    return [_type, host.name, time, event, desc]


def _analyze_key_info(type_str, task_id, stime, etime):
    host_ids = list()
    log_types = list()
    if stime:
        time1 = datetime.datetime.strptime(stime, '%Y-%m-%d %H:%M:%S')
    else:
        time1 = datetime.datetime.strptime('2016-07-20 00:00:00', '%Y-%m-%d %H:%M:%S')

    if etime:
        etime += '.999'
        time2 = datetime.datetime.strptime(etime, '%Y-%m-%d %H:%M:%S.%f')
    else:
        time2 = datetime.datetime.now()
    # 首页 任务详细
    if type_str and task_id:
        try:
            task = None
            if type_str == 'backup':
                log_types = [HostLog.LOG_BACKUP_START, HostLog.LOG_BACKUP_SUCCESSFUL, HostLog.LOG_BACKUP_FAILED,
                             HostLog.LOG_AGENT_STATUS]
                task = BackupTask.objects.get(id=task_id)
                host_ids.append(task.host_snapshot.host.id)
            elif type_str == 'cdp':
                log_types = [HostLog.LOG_CDP_FAILED, HostLog.LOG_CDP_PAUSE, HostLog.LOG_CDP_RESTART,
                             HostLog.LOG_CDP_START,
                             HostLog.LOG_CDP_STOP, HostLog.LOG_AGENT_STATUS, HostLog.LOG_CDP_BASE_FINISHED]
                task = CDPTask.objects.get(id=task_id)
                host_ids.append(task.schedule.host.id)
            elif type_str == 'restore':
                log_types = [HostLog.LOG_RESTORE_FAILED, HostLog.LOG_RESTORE_START, HostLog.LOG_RESTORE_SUCCESSFUL]
                task = RestoreTask.objects.get(id=task_id)
                host_ids.append(task.host_snapshot.host.id)
            elif type_str == 'migrate':
                log_types = [HostLog.LOG_MIGRATE_FAILED, HostLog.LOG_MIGRATE_START, HostLog.LOG_MIGRATE_SUCCESSFUL]
                task = MigrateTask.objects.get(id=task_id)
                host_ids.append(task.source_host.id)
            elif type_str == 'cluster':
                log_types = [HostLog.LOG_CLUSTER_BACKUP_START, HostLog.LOG_CLUSTER_BACKUP_BASE,
                             HostLog.LOG_CLUSTER_BACKUP_ANALYZE, HostLog.LOG_CLUSTER_BACKUP_SNAPSHOT,
                             HostLog.LOG_CLUSTER_BACKUP_SUCCESSFUL, HostLog.LOG_CLUSTER_BACKUP_FAILED,
                             HostLog.LOG_CLUSTER_CDP,
                             ]
                task = ClusterBackupTask.objects.get(id=task_id)
                for host in task.schedule.hosts.all():
                    host_ids.append(host.id)
            elif type_str == 'htb':
                log_types = [HostLog.LOG_HTB]
                task = HTBTask.objects.get(id=task_id)
                host_ids.append(task.schedule.host.id)
            elif type_str == 'vm_restore':
                log_types = [HostLog.LOG_VMWARE_RESTORE]
                task = VirtualMachineRestoreTask.objects.get(id=task_id)
                host_ids.append(task.host_snapshot.host.id)
            elif type_str == 'remote_backup_task':
                log_types = [HostLog.LOG_REMOTE_BACKUP_NORM_START, HostLog.LOG_REMOTE_BACKUP_NORM_SUCCESSFUL,
                             HostLog.LOG_REMOTE_BACKUP_NORM_FAILED, HostLog.LOG_REMOTE_BACKUP_CDP_START,
                             HostLog.LOG_REMOTE_BACKUP_CDP_END, HostLog.LOG_REMOTE_BACKUP]
                task = RemoteBackupTask.objects.get(id=task_id)
                host_ids.append(task.host_snapshot.host.id)
            elif type_str == 'archive':
                log_types = [HostLog.LOG_ARCHIVE_EXPORT, ]
                task = ArchiveTask.objects.get(id=task_id)
                host_ids.append(task.host_snapshot.host.id)
            elif type_str in ('file_backup_snapshot',):
                log_types = [HostLog.LOG_BACKUP_START, HostLog.LOG_BACKUP_SUCCESSFUL, HostLog.LOG_BACKUP_FAILED,
                             HostLog.LOG_AGENT_STATUS]
                task = FileBackupTask.objects.get(id=task_id)
                host_ids.append(task.host_snapshot.host.id)
            elif type_str == 'file_sync':
                log_types = [HostLog.LOG_FILE_SYNC, ]
                task = FileSyncTask.objects.get(id=task_id)
                host_ids.append(task.host_snapshot.host.id)
            else:
                pass

            if task:
                if task.finish_datetime:
                    time2 = task.finish_datetime + datetime.timedelta(seconds=5)  # 日在在完成之后创建
                if task.start_datetime:
                    time1 = task.start_datetime
        except Exception as e:
            _logger.error('_analyze_key_info error:{}|{}|{}'.format(e, type_str, task_id), exc_info=True)
            return host_ids, time1, time2, log_types
        else:
            return host_ids, time1, time2, log_types
    else:
        return host_ids, time1, time2, log_types


def getlog(request):
    servername = request.GET.get('servername')
    if servername is None:
        servername = request.POST.get('servername', '')
    desc = request.GET.get('desc')
    if desc is None:
        desc = request.POST.get('desc', '')
    stime = request.GET.get('stime')
    if stime is None:
        stime = request.POST.get('desc', '')
    etime = request.GET.get('etime')
    if etime is None:
        etime = request.POST.get('etime', '')
    type = request.GET.get('type', 0)
    if type is None:
        type = int(request.POST.get('type', 0))
    else:
        type = int(type)
    tasktype = request.GET.get('tasktype')
    if tasktype is None:
        tasktype = request.POST.get('tasktype', '')
    taskid = request.GET.get('taskid')
    if taskid is None:
        taskid = request.POST.get('taskid', '')
    events = request.GET.get('event')
    if events is None:
        events = request.POST.get('event', '')
    auto_verify_point_ids = request.POST.get('auto_verify_point_ids', None)
    if servername == 'debug':
        servername = ''
    sidx = request.GET.get('sidx', None)
    if sidx is None:
        sidx = request.POST.get('sidx')
    sord = request.GET.get('sord')
    if sord is None:
        sord = request.POST.get('sord', 'asc')
    if sidx not in ('host', 'datetime', 'type', 'reason',):
        sidx = None
    if sidx and sord == 'desc':
        sidx = '-{}'.format(sidx)

    hostlogs = HostLog.objects.filter()
    if events and int(events):
        hostlogs = hostlogs.filter(type=int(events))

    hostidarray = list()
    hosts = Host.objects.filter(user_id=request.user.id)
    for host in hosts:
        hostidarray.append(host.id)
    hostlogs = hostlogs.filter(host_id__in=hostidarray)
    # 首页任务"详细"按钮：滤出待显示HostLog
    host_ids, stime, etime, log_types = _analyze_key_info(tasktype, taskid, stime, etime)
    if log_types:
        hostlogs = hostlogs.filter(type__in=log_types)
    if host_ids:
        hostlogs = hostlogs.filter(host_id__in=host_ids)

    hostlogs = hostlogs.filter(datetime__range=(stime, etime))

    if auto_verify_point_ids:
        hostlogs = hostlogs.filter(
            type__in=(HostLog.LOG_AUTO_VERIFY_TASK_SUCCESSFUL, HostLog.LOG_AUTO_VERIFY_TASK_FAILED))
        point_id_filter = None
        for auto_verify_point_id in auto_verify_point_ids.split(','):
            if point_id_filter is None:
                point_id_filter = Q(reason__contains=auto_verify_point_id)
            else:
                point_id_filter = point_id_filter | Q(reason__contains=auto_verify_point_id)
        if point_id_filter:
            hostlogs = hostlogs.filter(point_id_filter)

    hostidarray = list()
    if servername:
        servername_ip = re.findall(r'[^()]+', servername)
        if len(servername_ip) > 0:
            hosts = Host.objects.filter(display_name__contains=servername_ip[0])
            for host in hosts:
                hostidarray.append(host.id)
        if len(servername_ip) > 0:
            hosts = Host.objects.filter(last_ip__contains=servername_ip[0])
            for host in hosts:
                hostidarray.append(host.id)
        if len(servername_ip) > 1:
            hosts = Host.objects.filter(last_ip__contains=servername_ip[1])
            for host in hosts:
                hostidarray.append(host.id)
        hostlogs = hostlogs.filter(host_id__in=hostidarray)

    if desc:
        hostlogs = hostlogs.filter(reason__contains=desc)

    if type == 1:
        hostlogs = hostlogs.filter(type__in=(
            1, 2, 4, 5, 7, 8, 10, 11, 14, 15, 16, 18, HostLog.LOG_VMWARE_RESTORE,
            HostLog.LOG_AUTO_VERIFY_TASK_SUCCESSFUL))

    if type == 2:
        hostlogs = hostlogs.filter(type__in=(13,))

    if type == 3:
        hostlogs = hostlogs.filter(type__in=(0, 3, 6, 9, 12, 17, HostLog.LOG_AUTO_VERIFY_TASK_FAILED))

    if sidx is not None:
        hostlogs = hostlogs.order_by(sidx)
    else:
        hostlogs = hostlogs.order_by('-datetime')

    return hostlogs


# {"r": 0, "a": "list", "page": "1", "total": 1, "records": 3,"rows": [
#    {"id": "1", "cell": ["信息","服务器一", "2016-4-28", "13:03", "开始备份","描述1"]},
#    {"id": "2", "cell": ["警告","服务器一", "2016-4-28", "13:03", "完成备份","描述2"]},
#    {"id": "3", "cell": ["错误","服务器一", "2016-4-28", "13:03", "事件3","描述3"]}
# ]}
# rows每页条数  page想获取第几页
def getLogList(request):
    page = request.GET.get('page')
    if page is None:
        page = int(request.POST.get('page', 1))
    else:
        page = int(page)
    rows = request.GET.get('rows')
    if rows is None:
        rows = int(request.POST.get('rows', 30))
    else:
        rows = int(rows)

    servername = request.GET.get('servername')
    if servername is None:
        servername = request.POST.get('servername', '')
    isdebug = False
    if servername == 'debug':
        isdebug = True
        servername = ''

    paginator = Paginator(getlog(request), rows)
    totalPlan = paginator.count
    totalPage = paginator.num_pages

    page = totalPage if page > totalPage else page
    currentObjs = paginator.page(page).object_list
    rowList = list()
    for Obj in currentObjs:
        detailDict = {'id': Obj.id, 'cell': getLogbyObj(Obj, isdebug)}
        rowList.append(detailDict)

    retInfo = {'r': 0, 'a': 'list', 'page': str(page), 'total': totalPage,
               'records': totalPlan, 'rows': rowList}
    jsonStr = json.dumps(retInfo, ensure_ascii=False)
    return HttpResponse(jsonStr)


def delAllLog(request):
    # 删除所有日志
    try:
        hostidarray = list()
        hosts = Host.objects.filter(user_id=request.user.id)
        for host in hosts:
            hostidarray.append(host.id)
        HostLog.objects.filter(host_id__in=hostidarray).delete()
    except Exception as e:
        return HttpResponse('{"r": "1","e": "' + str(e) + '"}')
    mylog = {'操作': '删除所有日志', '操作结果': "删除成功"}
    SaveOperationLog(
        request.user, OperationLog.TYPE_SYS_LOG, json.dumps(mylog, ensure_ascii=False), get_operator(request))
    return HttpResponse('{"r": "0","e": "操作成功"}')


def delLogByIds(request):
    # 根据ID删除日志
    ids = request.GET.get('ids', '0').split(',')
    try:
        HostLog.objects.filter(id__in=ids).delete()
    except Exception as e:
        return HttpResponse('{"r": "1","e": "' + str(e) + '"}')

    mylog = {'操作': '删除选定的日志', '操作结果': "删除成功"}
    SaveOperationLog(
        request.user, OperationLog.TYPE_SYS_LOG, json.dumps(mylog, ensure_ascii=False), get_operator(request))
    return HttpResponse('{"r": "0","e": "操作成功"}')


def cur_file_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def delexpirelog(exportpath):
    ctime = time.time()
    for dirpath, dirnames, filenames in os.walk(exportpath):
        for filename in filenames:
            thefile = os.path.join(dirpath, filename)
            if os.path.splitext(thefile)[1] in ('.zip', '.xls', '.json'):
                mtime = os.path.getmtime(thefile)
                if ctime - mtime > 2 * 60 * 60:
                    os.remove(thefile)


def exportLog(request):
    exportpath = os.path.join(cur_file_dir(), 'static', 'exportlog')
    delexpirelog(exportpath)
    startpage = int(request.GET.get('startpage', 1) if request.GET.get('startpage', 1) else 1)
    endpage = int(request.GET.get('endpage', 1) if request.GET.get('endpage', 1) else 1)
    rows = int(request.GET.get('rows', 30) if request.GET.get('rows', 30) else 30)
    iMaxRow = int(request.GET.get('maxrow', '5000') if request.GET.get('maxrow', '5000') else 5000)
    servername = request.GET.get('servername', '')
    isdebug = False
    if servername == 'debug':
        isdebug = True
        servername = ''

    if startpage <= 0:
        startpage = 1
    if endpage <= 0:
        endpage = 1

    timestr = datetime.datetime.now().strftime(xdatetime.FORMAT_WITH_SECOND)

    try:
        os.makedirs(exportpath)
    except OSError as e:
        pass
    filename = xdata.PREFIX_LOG_CLIENT_FILE + timestr + '.zip'
    filepath = os.path.join(exportpath, filename)

    paginator = Paginator(getlog(request), rows)
    totalPage = paginator.num_pages
    irow = 0
    xlpatharr = list()
    for page in range(startpage, endpage + 1, 1):
        if page > totalPage:
            break;
        currentObjs = paginator.page(page).object_list
        for Obj in currentObjs:
            element = getLogbyObj(Obj, isdebug)
            type = element[0]
            servername = element[1]
            time = element[2]
            event = element[3]
            desc = element[4]
            if irow == 0:
                wb = Workbook()
                ws = wb.add_sheet('Sheet1')
                ws.write(0, 0, '类型')
                ws.write(0, 1, '服务器名')
                ws.write(0, 2, '时间')
                ws.write(0, 3, '事件')
                ws.write(0, 4, '描述')
            ws.write(irow + 1, 0, type)
            ws.write(irow + 1, 1, servername)
            ws.write(irow + 1, 2, time)
            ws.write(irow + 1, 3, event)
            ws.write(irow + 1, 4, desc)
            irow += 1
            if irow >= iMaxRow:
                tmppath = timestr + "-" + str(len(xlpatharr) + 1) + 'log.xls'
                xlpath = os.path.join(exportpath, tmppath)
                xlpatharr.append(xlpath)
                wb.save(xlpath)
                irow = 0

    if irow % iMaxRow != 0:
        tmppath = timestr + "-" + str(len(xlpatharr) + 1) + 'log.xls'
        xlpath = os.path.join(exportpath, tmppath)
        xlpatharr.append(xlpath)
        wb.save(xlpath)

    z = zipfile.ZipFile(filepath, 'w')
    i = 0
    for xlpath in xlpatharr:
        i += 1
        z.write(xlpath, '客户端日志-' + str(i) + '.xls')
    z.close()

    for xlpath in xlpatharr:
        os.remove(xlpath)

    mylog = {'操作': '导出日志', '操作结果': "导出成功"}
    SaveOperationLog(
        request.user, OperationLog.TYPE_SYS_LOG, json.dumps(mylog, ensure_ascii=False), get_operator(request))
    return HttpResponse('{"r": "0","e": "操作成功","url":"/static/exportlog/%s","filename":"%s"}' % (filename, filename))


def SaveOperationLog(user, event, desc, operator=None):
    from xdashboard.handle.authorize.authorize_init import get_separation_of_the_three_members
    if get_separation_of_the_three_members().is_database_use_policy():
        is_full, database_can_use_size, database_used_bytes = is_database_full()
        if is_full:
            try:
                first_item = OperationLog.objects.filter(user=user).order_by('datetime').first()
                if first_item:
                    first_item.delete()
            except Exception as e:
                pass
    try:
        OperationLog.objects.create(user=user, event=event, desc=desc, operator=operator)
    except Exception as e:
        _logger.info(str(e))


def delLogByTime(request):
    params = request.GET
    sDatetime = params['sDatetime']
    eDatetime = params['eDatetime']
    if '' in [sDatetime, eDatetime]:
        return HttpResponse('{"r": "1","e": "无效的时间范围"}')

    sDatetime = datetime.datetime.strptime(sDatetime, "%Y-%m-%d %H:%M:%S")
    eDatetime = datetime.datetime.strptime(eDatetime, "%Y-%m-%d %H:%M:%S") + datetime.timedelta(microseconds=999999)
    if sDatetime > eDatetime:
        return HttpResponse('{"r": "1","e": "无效的时间范围"}')

    try:
        user_hosts = Host.objects.filter(user_id=request.user.id)
        HostLog.objects.filter(host__in=user_hosts, datetime__gte=sDatetime, datetime__lte=eDatetime).delete()
    except Exception as e:
        return HttpResponse('{"r": "1","e": "' + str(e) + '"}')

    mylog = {'操作': '按时间段删除日志', '操作结果': "删除成功"}
    SaveOperationLog(
        request.user, OperationLog.TYPE_SYS_LOG, json.dumps(mylog, ensure_ascii=False), get_operator(request))
    return HttpResponse('{"r": "0","e": "操作成功"}')


# 查询日志--用于外部接口
def getLoglistToOut(request):
    start_id = request.GET.get('start_id', 0)
    search_size = request.GET.get('search_size', 50)
    try:
        exclude_list = [HostLog.LOG_LOGIN, HostLog.LOG_LOGOUT, HostLog.LOG_COLLECTION_SPACE, HostLog.LOG_INIT_ERROR]
        host_logs = HostLog.objects.filter(id__gt=int(start_id)).exclude(type__in=exclude_list).order_by('id')[
                    :int(search_size)]
        host_list = list()
        for hl in host_logs:
            result_dict = dict()
            try:
                result_dict = load_log_to_list(hl)
            except Exception as e:
                pass
                # _logger.warning('load host info failed host.reason:{},e:{}'.format(hl.reason, str(e)))
            if result_dict:
                host_list.append(result_dict)
        return HttpResponse(json.dumps(host_list))
    except Exception as e:
        my_result = {"r": "1", "e": "{}".format(str(e))}
        return HttpResponse(json.dumps(my_result))


def task_and_schedule_info(host_log_obj):
    """
    :param host_log_obj:
    :return: str,obj,obj
    """
    schedule_obj = None
    point_name = ''
    host_snapshot = None
    target_info = {}
    reason = json.loads(host_log_obj.reason)
    task_type = reason['task_type']  # task_type
    task_id = reason[task_type]

    if task_type == 'cdp_task':
        schedule_obj = BackupTaskSchedule.objects.get(cdp_tasks__id=task_id)
        try:
            host_snapshot = CDPTask.objects.get(id=task_id).host_snapshot
            point_name = r'cdp备份{}'.format(host_snapshot.start_datetime)
        except Exception:
            pass
    if task_type == 'backup_task':
        schedule_obj = BackupTaskSchedule.objects.get(backup_tasks__id=task_id)
        try:
            host_snapshot = BackupTask.objects.get(id=task_id).host_snapshot
            point_name = r'普通备份{}'.format(host_snapshot.start_datetime)
        except Exception:
            pass
    if task_type == 'restore_task':
        restore_task_obj = RestoreTask.objects.get(id=task_id)
        host_snapshot = restore_task_obj.host_snapshot
        target_host_name = restore_task_obj.restore_target.display_name
        schedule_obj = host_snapshot.schedule
        point_begin = host_snapshot.cdp_info.first_datetime.strftime(
            xdatetime.FORMAT_WITH_USER_SECOND) if host_snapshot.is_cdp else host_snapshot.start_datetime.strftime(
            xdatetime.FORMAT_WITH_USER_SECOND)
        target_info = {'target_ip': target_host_name.split(' ')[0],
                       'point_begin': point_begin}
    return point_name, host_snapshot, schedule_obj, target_info


def load_log_to_list(host_log_obj):
    result_dict = {'row_id': host_log_obj.id}
    point_name, host_snapshot, schedule_obj, target_info = task_and_schedule_info(host_log_obj)
    if host_snapshot:
        snapshot_start_time = host_snapshot.start_datetime
        snapshot_start_time = snapshot_start_time.strftime('%Y-%m-%d %H:%M:%S') if snapshot_start_time else None
        snapshot_finish_time = host_snapshot.finish_datetime
        snapshot_finish_time = snapshot_finish_time.strftime('%Y-%m-%d %H:%M:%S') if snapshot_finish_time else None
        snapshot_id = host_snapshot.id
    else:
        snapshot_start_time = None
        snapshot_finish_time = None
        snapshot_id = -1
    try:
        result_dict = {
            'time': host_log_obj.datetime.strftime('%Y-%m-%d %H:%M:%S'),
            'reason': host_log_obj.reason,
            'row_id': host_log_obj.id,
            'host_ident': host_log_obj.host.ident,
            'point_name': point_name,
            'backup_task_schedules': schedule_obj.name if schedule_obj else '',
            'schedule_id': schedule_obj.id,
            'point_id': snapshot_id,
            'point_start_time': snapshot_start_time,
            'point_finish_time': snapshot_finish_time,
            'target_host': json.dumps(target_info)
        }
    except AttributeError:
        result_dict = {
            'row_id': host_log_obj.id,
            'reason': host_log_obj.reason,
            'time': host_log_obj.datetime.strftime('%Y-%m-%d %H:%M:%S'),
            'point_name': point_name,
            'backup_task_schedules': schedule_obj.name if schedule_obj else '',
            'schedule_id': schedule_obj.id,
            'point_id': snapshot_id,
            'point_start_time': snapshot_start_time,
            'point_finish_time': snapshot_finish_time,
            'target_host': json.dumps(target_info)
        }
    except Exception as e:
        raise Exception(e)
    finally:
        return result_dict


# 查询阶段进度--用于外部接口
def getProgressToOut(request):
    my_result = dict()
    try:
        task_ids = json.loads(request.GET.get('task_ids', '{}'))
        if not task_ids:
            my_result = {"r": "1", "e": "没有对应的task_id:{}".format(task_ids)}
            return HttpResponse(json.dumps(my_result))
        for task_type, id_list in task_ids.items():
            if task_type not in my_result.keys():
                my_result[task_type] = list()
            if task_type == 'migrate_task':
                task_progress_migrate(id_list, my_result, task_type)  # , MigrateTask
            if task_type == 'restore_task':
                task_progress_restore(id_list, my_result, task_type)
            if task_type == 'backup_task':
                task_progress_backup(id_list, my_result, task_type)
            if task_type == 'cdp_task':
                task_progress_backup(id_list, my_result, task_type)  # , CDPTask
    except Exception as e:
        # _logger.error('getProgressToOut e:'.format(e), exc_info=True)
        my_result = {"r": "1", "e": "{}".format(str(e))}

    return HttpResponse(json.dumps(my_result))


def task_progress_migrate(id_list, my_result, task_type):
    pass


def task_progress_restore(id_list, my_result, task_type):
    for id_one in id_list:
        try:
            restore_target = RestoreTask.objects.get(id=id_one).restore_target
            _logger.info('get_task_progress>>>:{},{}'.format(id_one, restore_target.id))
            index = restore_target.restored_bytes
            total = restore_target.total_bytes
            if None in [index, total]:
                flag, totals, hs_send = boxService.box_service.get_restore_key_data_process(restore_target.ident)
                if flag and hs_send:
                    index, total = hs_send * 512, totals * 512
            my_result[task_type].append([id_one, index, total])
        except Exception as e:
            # _logger.warning('get progress failed id:{},e:{}'.format(id_one, e))
            pass


def task_progress_backup(id_list, my_result, task_type):
    if task_type == 'backup_task':
        for id_one in id_list:
            try:
                ext_info = json.loads(BackupTask.objects.get(id=id_one).host_snapshot.ext_info)
                index = ext_info.get('progressIndex', None)
                total = ext_info.get('progressTotal', None)
                my_result[task_type].append([id_one, index, total])
            except Exception as e:
                # _logger.warning('get progress failed id:{},e:{}'.format(id_one, e))
                pass
    if task_type == 'cdp_task':
        for id_one in id_list:
            try:
                ext_info = json.loads(CDPTask.objects.get(id=id_one).host_snapshot.ext_info)
                index = ext_info.get('progressIndex', None)
                total = ext_info.get('progressTotal', None)
                my_result[task_type].append([id_one, index, total])
            except Exception as e:
                # _logger.warning('get progress failed id:{},e:{}'.format(id_one, e))
                pass


def _get_deleted_hosts():
    """
    1. 有用户且删除
    2. 无用户
    """
    return [host.ident for host in Host.objects.filter(user__isnull=False) if host.is_deleted] + \
           [host.ident for host in Host.objects.filter(user__isnull=True)]


# 查询主机信息--用于外部接口
def getHostInfo(request):
    """
    :param request:
    :return:json
    """
    host_info_list = list()
    hosts = [host for host in Host.objects.filter(user__isnull=False, user__is_active=True).all().order_by('id') if
             host.is_deleted is False]
    for host in hosts:
        try:
            ext_info = json.loads(host.ext_info)
            if host.is_deleted or not ext_info:
                continue
            login_time = host.login_datetime
            if not login_time:
                if host.logs.count():
                    login_time = host.logs.order_by('-datetime')[0].datetime.strftime('%Y-%m-%d %H:%M:%S')
                else:
                    login_time = None
            else:
                login_time = login_time.strftime('%Y-%m-%d %H:%M:%S')

            point_list = get_host_backup_point(host)

            if len(point_list) == 0:
                last_point = None
                first_point = None
            else:
                last_point = point_list[-1]['id']
                if len(point_list) == 1:
                    first_point = None
                else:
                    first_point = point_list[0]['id']

            host_info = {'host_ident': host.ident, 'last_ip': host.last_ip,
                         'host_is_enable': ext_info.get('is_valiade_host', True),
                         'host_is_online': host.is_linked, 'host_type': 'HOST_TYPE_AGENT',
                         'host_address': ext_info['system_infos']['Nic'][0]['IpAndMask'],
                         'host_name': host.display_name, 'tenant': host.user.username,
                         'login_datetime': login_time,
                         'system_type': 'Linux' if 'Linux' in ext_info['system_infos'].keys() else 'Window',
                         'point_num': len(point_list),
                         # yun待优化（以前是从任务来得到的大概时间可优化）
                         'last_point_time': last_point.split('|')[-1] if last_point else None,
                         'first_point_time': first_point.split('|')[-1] if first_point else None,
                         'last_point_info': last_point,
                         'first_point_info': first_point,
                         }
            host_info_list.append(host_info)
        except Exception as e:
            _logger.error('getHostInfo:{},e:{}'.format(host, e), exc_info=True)

    return HttpResponse(json.dumps({'host_info_list': host_info_list, 'hosts_deleted': _get_deleted_hosts()}))


def get_host_backup_point(host_obj):
    from apiv1.views import HostSnapshotsWithCdpPerHost, HostSnapshotsWithNormalPerHost
    api_request = {
        'begin': '2016-01-01',
        'end': (datetime.datetime.now() + datetime.timedelta(days=1)).strftime('%Y-%m-%d'),
        'finish': True,
        'use_serializer': False
    }
    host_snapshot_list = list()
    try:
        api_response = HostSnapshotsWithCdpPerHost().get(None, ident=host_obj.ident, api_request=api_request)
        if status.is_success(api_response.status_code):
            for host_snapshot in api_response.data:
                data = {
                    "id": '{}|{}|{}|{}'.format(xdata.SNAPSHOT_TYPE_CDP, host_snapshot['id'],
                                               host_snapshot['begin'], host_snapshot['end']),
                    "time": host_snapshot['begin'],
                }
                host_snapshot_list.append(data)
        api_response = HostSnapshotsWithNormalPerHost().get(request=None, ident=host_obj.ident, api_request=api_request)
        if status.is_success(api_response.status_code):
            for host_snapshot in api_response.data:
                data = {
                    "id": '{}|{}|{}'.format(xdata.SNAPSHOT_TYPE_NORMAL, host_snapshot['id'],
                                            host_snapshot['start_datetime']),
                    "time": host_snapshot['start_datetime'],
                }
                host_snapshot_list.append(data)
    except Exception as e:
        _logger.error('get_host_backup_point>>:{}'.format(e), exc_info=True)

    host_snapshot_list.sort(key=lambda o: xdatetime.string2datetime(o['time']))

    return host_snapshot_list


def logserver_handler(request):
    a = request.GET.get('a', 'none')
    if a == 'none':
        a = request.POST.get('a', 'none')
    if a == 'list':
        return getLogList(request)
    if a == 'delall':
        return delAllLog(request)
    if a == 'delbyid':
        return delLogByIds(request)
    if a == 'export':
        return exportLog(request)
    if a == 'delbytime':
        return delLogByTime(request)
    if a == 'list_to_out':
        return getLoglistToOut(request)
    if a == 'stageprogress':
        return getProgressToOut(request)
    if a == 'gethostinfo':
        return getHostInfo(request)
    return HttpResponse(json.dumps({"r": "1", "e": "没有对应的action:{}".format(html.escape(a))}))
