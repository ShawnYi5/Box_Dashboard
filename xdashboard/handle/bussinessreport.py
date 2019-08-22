import datetime
import html
import json
import os
import time
import zipfile
from datetime import timedelta
from random import random
from functools import partial
import django.utils.timezone as timezone
import xlsxwriter
from django.core.paginator import Paginator
from django.http import HttpResponse
import re

from apiv1.models import (StorageNode, HostSnapshot, Host, HostLog, UserQuota, HostSnapshotCDP, BackupTask, CDPTask,
                          RestoreTask, MigrateTask, ClusterBackupTask, HTBTask, HostSnapshotShare, TakeOverKVM,
                          VirtualMachineRestoreTask, RemoteBackupSubTask, SpaceCollectionTask,
                          DeployTemplate)
from apiv1.storage_nodes import UserQuotaTools
from apiv1.views import StorageNodes, QuotaManage
from box_dashboard import xdata, xdatetime, xlogging, functions, boxService
from xdashboard.handle.home import getStorageChart, getBandwidthChart, getIOChart
from xdashboard.handle.sysSetting import storage
from xdashboard.models import StorageNodeSpace, UserProfile
from .xlwt.Style import easyxf
from .xlwt.Workbook import Workbook

_logger = xlogging.getLogger(__name__)


def catch_http_request_exception(handle_fun):
    def new_func(*args, **kwargs):
        try:
            return handle_fun(*args, **kwargs)
        except Exception as e:
            _logger.error('{}: {}'.format(handle_fun.__name__, e), exc_info=True)
            return HttpResponse(json.dumps({'r': 1, 'e': '未知异常!'}, ensure_ascii=False))

    return new_func


# 获取客户端，历史 备份，还原，迁移，成功/失败 次数
def totaltimes(request):
    jsonstr = {'r': 0, 'e': '操作成功', 'list': []}
    filters_success = [HostLog.LOG_BACKUP_SUCCESSFUL, HostLog.LOG_RESTORE_SUCCESSFUL,
                       HostLog.LOG_MIGRATE_SUCCESSFUL]
    filters_fail = [HostLog.LOG_BACKUP_FAILED, HostLog.LOG_RESTORE_FAILED,
                    HostLog.LOG_MIGRATE_FAILED]
    hosts = Host.objects.filter(user=request.user)
    stm = request.GET.get('starttime', None)
    endtm = request.GET.get('endtime', None)
    stm = datetime.datetime.strptime(stm, '%Y-%m-%d')
    endtm = datetime.datetime.strptime(endtm, '%Y-%m-%d') + datetime.timedelta(days=1)
    # 过滤掉 验证的主机
    hosts = list(filter(lambda x: not x.is_verified, hosts))
    # 过滤掉 被删除的主机
    hosts = list(filter(lambda x: not x.is_deleted, hosts))
    for host in hosts:
        tmps = {'data': [], 'hostname': host.display_name, 'ident': host.ident}
        logs = HostLog.objects.filter(host=host, datetime__gte=stm, datetime__lte=endtm)
        backup_success = logs.filter(type=filters_success[0]).count()
        restore_success = logs.filter(type=filters_success[1]).count()
        migrate_success = logs.filter(type=filters_success[2]).count()

        backup_fail = logs.filter(type=filters_fail[0]).count()
        restore_fail = logs.filter(type=filters_fail[1]).count()
        migrate_fail = logs.filter(type=filters_fail[2]).count()

        tmps['data'].append({'value': backup_success, 'label': '备份成功'})
        tmps['data'].append({'value': restore_success, 'label': '恢复成功'})
        tmps['data'].append({'value': migrate_success, 'label': '迁移成功'})
        tmps['data'].append({'value': backup_fail, 'label': '备份失败'})
        tmps['data'].append({'value': restore_fail, 'label': '恢复失败'})
        tmps['data'].append({'value': migrate_fail, 'label': '迁移失败'})
        totaltimes = backup_success + restore_success + migrate_success + backup_fail + restore_fail + migrate_fail
        if totaltimes:
            jsonstr['list'].append(tmps)
        else:
            tmps['data'] = list()
            jsonstr['list'].append(tmps)

    return HttpResponse(json.dumps(jsonstr, ensure_ascii=False))


def recentlyevents(request):
    hosts = Host.objects.filter(user=request.user)

    jsonstr = {'r': 0, 'e': '操作成功', 'list': []}
    filters_fail = [HostLog.LOG_BACKUP_FAILED, HostLog.LOG_RESTORE_FAILED,
                    HostLog.LOG_CDP_FAILED, HostLog.LOG_MIGRATE_FAILED]

    logs = HostLog.objects.filter(type__in=filters_fail, host__in=hosts).order_by('-datetime')[:5]
    for i in logs:
        times = i.datetime.strftime('%Y-%m-%d %H:%M:%S')
        hostname = i.host.display_name
        jsonstr['list'].append({'hostname': hostname, 'event': i.get_type_display(), 'time': times, 'id': i.id})

    return HttpResponse(json.dumps(jsonstr, ensure_ascii=False))


def _get_log_type(log, types):
    if log.type == HostLog.LOG_CDP_PAUSE or log.type == HostLog.LOG_CDP_FAILED or \
            log.type == HostLog.LOG_CDP_STOP:
        return 'cdp'
    else:
        return types


def _get_host_log_data(st_time_str, ed_time_str, host, log_type):
    filters_fail = [HostLog.LOG_RESTORE_FAILED, HostLog.LOG_MIGRATE_FAILED,
                    HostLog.LOG_BACKUP_FAILED, HostLog.LOG_CDP_PAUSE,
                    HostLog.LOG_CDP_FAILED, HostLog.LOG_CDP_STOP]
    filters_start = [HostLog.LOG_RESTORE_START, HostLog.LOG_MIGRATE_START, HostLog.LOG_BACKUP_START,
                     HostLog.LOG_CDP_START]
    st_datetime = datetime.datetime.strptime(st_time_str, '%Y-%m-%d')
    ed_datetime = datetime.datetime.strptime(ed_time_str, '%Y-%m-%d') + datetime.timedelta(days=1)
    if log_type == 'backup':
        filters_fail = filters_fail[2:]
        filters_start = filters_start[2:]
    if log_type == 'restore':
        filters_fail = filters_fail[0]
        filters_start = filters_start[0]
    if log_type == 'migrate':
        filters_fail = filters_fail[1]
        filters_start = filters_start[1]
    if log_type == 'backup':
        logs = HostLog.objects.filter(type__in=filters_fail, host=host, datetime__gte=st_datetime,
                                      datetime__lte=ed_datetime).order_by('-datetime')
        logs_start = HostLog.objects.filter(type__in=filters_start, host=host).order_by('-datetime')
    else:
        logs = HostLog.objects.filter(type=filters_fail, host=host, datetime__gte=st_datetime,
                                      datetime__lte=ed_datetime).order_by('-datetime')
        logs_start = HostLog.objects.filter(type=filters_start, host=host).order_by('-datetime')
    if logs:
        logs = logs[0]
        hostname = host.display_name
        times = logs.datetime.strftime('%Y-%m-%d %H:%M:%S')
        event = logs.get_type_display()
        desc = json.loads(logs.reason).get('description', '----')

        log_type_str = _get_log_type(logs, log_type)
        try:
            taskid = json.loads(logs.reason)[log_type_str + '_task']
            if logs_start and taskid:
                for log in logs_start:
                    if json.loads(log.reason).get(log_type_str + '_task', None) == taskid:
                        sttime = log.datetime.strftime('%Y-%m-%d %H:%M:%S')
                        break
        except:
            sttime = '----'
        times = '开始时间:' + sttime + '\r\n' + '结束时间:' + times
        return [logs.id, hostname, times, event, desc]
    return []


def excutelist(request):
    page = 1
    rows = 30
    if 'page' in request.GET:
        page = int(request.GET['page'])
    if 'rows' in request.GET:
        rows = int(request.GET['rows'])

    hosts = Host.objects.filter(user_id=request.user.id)
    # 过滤掉 验证的主机
    hosts = list(filter(lambda x: not x.is_verified, hosts))
    # 过滤掉 被删除的主机
    hosts = list(filter(lambda x: not x.is_deleted, hosts))
    rowList = []
    sttime = request.GET.get('st')
    endtime = request.GET.get('end')
    types = request.GET.get('type', None)
    for host in hosts:
        tmp = _get_host_log_data(sttime, endtime, host, types)
        detailDict = {'id': host.id, 'cell': tmp}
        if tmp:
            rowList.append(detailDict)
    if not rowList:
        rowList.append({'id': -1, 'cell': ['暂无数据', '暂无数据', '暂无数据', '暂无数据', '暂无数据']})
    paginator = Paginator(rowList, rows)
    totalPlan = paginator.count
    totalPage = paginator.num_pages
    page = totalPage if page > totalPage else page
    currentObjs = paginator.page(page).object_list
    retInfo = {'r': 0, 'a': 'list', 'page': str(page), 'total': totalPage,
               'records': totalPlan, 'rows': currentObjs}
    functions.sort_gird_rows(request, retInfo)
    jsonStr = json.dumps(retInfo, ensure_ascii=False)

    return HttpResponse(jsonStr)


def datasafety(request):
    page = 1
    rows = 30
    if 'page' in request.GET:
        page = int(request.GET['page'])
    if 'rows' in request.GET:
        rows = int(request.GET['rows'])

    hosts = Host.objects.filter(user_id=request.user.id)
    # 过滤掉 验证的主机
    hosts = list(filter(lambda x: not x.is_verified, hosts))
    # 过滤掉 被删除的主机
    hosts = list(filter(lambda x: not x.is_deleted, hosts))
    rowList = []
    for host in hosts:
        tmp = datasafetyobj(request, host)
        detailDict = {'id': host.id, 'cell': tmp}
        if tmp:
            rowList.append(detailDict)
    if not rowList:
        rowList.append({'id': -1, 'cell': [-2, '暂无数据', '暂无数据', '暂无数据', -4]})
    paginator = Paginator(rowList, rows)
    totalPlan = paginator.count
    totalPage = paginator.num_pages

    page = totalPage if page > totalPage else page
    currentObjs = paginator.page(page).object_list
    retInfo = {'r': 0, 'a': 'list', 'page': str(page), 'total': totalPage,
               'records': totalPlan, 'rows': currentObjs}
    functions.sort_gird_rows(request, retInfo)
    jsonStr = json.dumps(retInfo, ensure_ascii=False)

    return HttpResponse(jsonStr)


def datasafetyobj(request, host):
    queryset = HostSnapshot.objects.filter(host=host, deleted=False, start_datetime__isnull=False,
                                           deleting=False, is_cdp=True).exclude(successful=False)
    if queryset:
        obj = queryset.latest('start_datetime')
        times = obj.start_datetime.strftime('%Y-%m-%d %H:%M:%S')
        # cdp保护
        if not HostSnapshotCDP.objects.get(host_snapshot=obj).stopped:
            return [obj.id, host.display_name, times, 'CDP持续保护中，安全', -1]
        else:
            # cdp 保护停止
            sttime = HostSnapshotCDP.objects.get(host_snapshot=obj).first_datetime.strftime('%Y-%m-%d %H:%M:%S')
            lasttime = HostSnapshotCDP.objects.get(host_snapshot=obj).last_datetime
            cdpsafedata = (datetime.datetime.now() - lasttime).days + 1
            msg = 'CDP保护停止,可供还原的窗口期：\r\n' + '开始时间:' + sttime + '\r\n' + '结束时间:' + lasttime.strftime('%Y-%m-%d %H:%M:%S')
            return [obj.id, host.display_name, lasttime.strftime('%Y-%m-%d %H:%M:%S'), msg, cdpsafedata]
    queryset = HostSnapshot.objects.filter(host=host, deleted=False, start_datetime__isnull=False,
                                           deleting=False).exclude(successful=False)
    safeleve = UserProfile.objects.get(user=request.user).safeset.split(',')
    if queryset:
        obj = queryset.latest('finish_datetime')
        times = obj.start_datetime.strftime('%Y-%m-%d %H:%M:%S')
        safequota = (datetime.datetime.now() - obj.start_datetime).days + 1
        # 普通保护
        msg = ''
        if safequota == 0:
            msg = '普通备份 距离现在：' + '不到一天' + ',风险：低'
        if 0 < safequota <= int(safeleve[0]):
            msg = '普通备份 距离现在：' + str(safequota) + '天,风险：低'
        if int(safeleve[0]) < safequota <= int(safeleve[1]):
            msg = '普通备份 距离现在：' + str(safequota) + '天,风险：中'
        if int(safeleve[1]) < safequota:
            msg = '普通备份 距离现在：' + str(safequota) + '天,风险：高'

        return [obj.id, host.display_name, times, msg, safequota]

    # 无保护
    return [-1, host.display_name, '----', '没有备份数据，不安全', -2]


def saferange(request):
    type = request.GET.get('type')
    if type == 'set':
        userprofile = UserProfile.objects.get(user=request.user)
        userprofile.safeset = request.GET.get('range')
        userprofile.save()
        return HttpResponse(json.dumps({'e': '操作成功', 'r': 0}))
    if type == 'get':
        safeleve = UserProfile.objects.get(user=request.user).safeset.split(',')
        return HttpResponse(json.dumps({'e': '操作成功', 'r': 0, 'data': safeleve}))


# 从集合中获取指定数目的元素
def _get_constant_elems(elems_list, basic_number):
    elems_list = list(elems_list)
    elem_nums = len(elems_list)
    step = int(elem_nums / basic_number)

    if elem_nums <= basic_number:
        return elems_list
    result = list()
    for index in range(elem_nums):
        if index % step == 0:
            result.append(elems_list[index])
    return result


def _Byte2GBp2(_bytes):
    return float('{0:.2f}'.format(_bytes / 1024 ** 3))


# 所有的存储结点，容量使用图表(历史)
def _get_storage_chart(request):
    beginT = datetime.datetime.strptime(request.GET['starttime'], '%Y-%m-%d')
    endT = datetime.datetime.strptime(request.GET['endtime'], '%Y-%m-%d') + datetime.timedelta(days=1)

    ret_info = {'r': 0, 'e': '操作成功', 'nodes': []}
    nodes_obj = StorageNode.objects.filter(deleted=False)
    for node_obj in nodes_obj:
        _is_link = True
        node_detail = UserQuotaTools.get_storage_node_detail(node_obj.id, False)

        # 获取最新状态，作末尾元素
        try:
            total_GB_now = _Byte2GBp2(node_detail['total_bytes'])
            used_GB_now = _Byte2GBp2((node_detail['total_bytes'] - node_detail['available_bytes']))
            raw_GB_now = _Byte2GBp2(storage.all_users_raw_data_bytes_in_a_node(node_detail['id']))
        except Exception as e:
            _logger.warning('RecordNodesSpace Exception: {0}'.format(e))
            total_GB_now, used_GB_now, raw_GB_now, _is_link = 0, 0, 0, False

        # 某个存储单元，容量空间历史统计记录
        nodes_rcd = StorageNodeSpace.objects.filter(node_id=node_obj.id, time_date__gte=beginT, time_date__lt=endT)
        nodes_rcd = _get_constant_elems(nodes_rcd, 200)
        history = [{'year': str(obj.time_date)[:19], 'used': _Byte2GBp2(obj.total_bytes - obj.free_bytes),
                    'free': _Byte2GBp2(obj.free_bytes), 'raw': _Byte2GBp2(obj.raw_data_bytes)} for obj in nodes_rcd]

        # 确定表的时间范围：设定首尾时间(零值)
        history.append({'year': str(beginT), 'used': 0, 'free': 0, 'raw': 0})
        history.append({'year': str(endT), 'used': 0, 'free': 0, 'raw': 0})

        # 若结束时间为当天、最新数据有效，则追加最新点
        added_now = False
        end_date = datetime.datetime.strptime(request.GET['endtime'], '%Y-%m-%d').date()
        now_date = timezone.now().date()
        if end_date == now_date and _is_link:
            history.append({'year': str(timezone.now())[:19], 'used': used_GB_now, 'free': total_GB_now - used_GB_now,
                            'raw': raw_GB_now})
            added_now = True

        # 作UI截断处理：存在元素，则首尾添加时间(零值)
        if nodes_rcd:
            first_elem_time = nodes_rcd[0].time_date
            last_elem_time = timezone.now() if added_now else nodes_rcd[-1].time_date
            history.append({'year': str(first_elem_time - timedelta(seconds=1))[:19], 'used': 0, 'free': 0, 'raw': 0})
            history.append({'year': str(last_elem_time + timedelta(seconds=1))[:19], 'used': 0, 'free': 0, 'raw': 0})

        # 描述一个存储单元：单元名称，总空间，目前已用，目前可用，目前RAW，历史记录
        _node = {'name': node_obj.name, 'total': total_GB_now, 'used': used_GB_now, 'free': total_GB_now - used_GB_now,
                 'raw': raw_GB_now, 'list': history}
        ret_info['nodes'].append(_node)

    return HttpResponse(json.dumps(ret_info))


# 获取指定的Node的space使用情况(当前)
def _get_storage_info(request):
    node_id = request.GET['storagedevice']
    if node_id in ['-1', 'null']:
        return HttpResponse(json.dumps({"r": 0, "e": "操作成功"}, ensure_ascii=False))
    node_id = int(node_id)

    node = UserQuotaTools.get_storage_node_detail(node_id, False)
    total_size = '{0:.2f}GB'.format(node['total_bytes'] / 1024 ** 3)
    used_size = '{0:.2f}GB'.format((node['total_bytes'] - node['available_bytes']) / 1024 ** 3)

    ret_info = {"r": "0", "e": "操作成功", 'total': total_size, 'used': used_size}
    return HttpResponse(json.dumps(ret_info))


# 获取所有Nodes的Space使用情况(当前)
def _get_all_storage_info(request):
    nodes = [{'name': node['name'], 'total': node['total_bytes'], 'free': node['available_bytes']} for node in
             StorageNodes().get(request).data]

    ret_info = {"r": "0", "e": "操作成功", 'nodes': nodes}
    return HttpResponse(json.dumps(ret_info))


# 所有可用的Nodes
def _get_nodes_list(request):
    node_objs = StorageNode.objects.filter(deleted=False, available=True)
    nodes = [{'id': node.id, 'name': node.name} for node in node_objs]
    return HttpResponse(json.dumps(nodes))


# 用户配额使用情况(当前)
def quotastatus(request):
    ret_info = {"r": "0", "e": "操作成功", "list": []}
    user_quotas = UserQuota.objects.filter(user_id=request.user.id, deleted=False)
    for quota_obj in user_quotas:
        node_obj = quota_obj.storage_node
        node_tools = UserQuotaTools(node_obj.id, request.user.id, quota_obj.quota_size)
        try:
            free_bytes = node_tools.get_user_available_storage_size_in_node(True) * 1024 ** 2
            used_bytes = storage.user_used_size_mb_in_a_node(node_obj.id, request.user.id) * 1024 ** 2
        except Exception:
            free_bytes, used_bytes = 0, 0

        quota_bytes = -1 if quota_obj.quota_size in [-1, 0] else quota_obj.quota_size * 1024 ** 2

        ret_info['list'].append({"name": node_obj.name, "total": quota_bytes, "free": free_bytes, 'used': used_bytes})

    return HttpResponse(json.dumps(ret_info))


def cur_file_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def delexpirelog(exportpath):
    ctime = time.time()
    for dirpath, dirnames, filenames in os.walk(exportpath):
        for filename in filenames:
            thefile = os.path.join(dirpath, filename)
            if os.path.splitext(thefile)[1] in ('.zip', '.xls', '.xlsx'):
                mtime = os.path.getmtime(thefile)
                if ctime - mtime > 2 * 60:
                    os.remove(thefile)


def exportxls(data, name, merge_range=None):
    exportpath = os.path.join(cur_file_dir(), 'static', 'exportsafe')
    delexpirelog(exportpath)
    timestr = datetime.datetime.now().strftime(xdatetime.FORMAT_WITH_SECOND)
    try:
        os.makedirs(exportpath)
    except OSError:
        pass
    filename = xdata.PREFIX_SAFE_REPORT_FILE + timestr + '_' + str(int(random() * 1000000)) + '.zip'
    filepath = os.path.join(exportpath, filename)
    style0 = easyxf('align: vert centre, horiz center')
    wb = Workbook()
    ws = wb.add_sheet('Sheet1', cell_overwrite_ok=True)
    row = 0
    columnwidth = dict()
    for rowdata in data:
        column = 0
        for colomndata in rowdata:
            if column in columnwidth:
                if len(colomndata) > columnwidth[column]:
                    columnwidth[column] = len(colomndata)
            else:
                columnwidth[column] = len(colomndata)
            ws.write(row, column, colomndata, style0)
            column = column + 1
        row = row + 1
    for column, widthvalue in columnwidth.items():
        ws.col(column).width = (widthvalue + 4) * 367
    if merge_range is not None:
        merge_range(ws)
    tmppath = timestr + '-' + 'safereport.xls'
    tmppath = os.path.join(exportpath, tmppath)
    wb.save(tmppath)

    z = zipfile.ZipFile(filepath, 'w')
    z.write(tmppath, name + timestr[:-7] + '.xls')
    os.remove(tmppath)
    url = '/static/exportsafe/' + filename
    return url, filename


def getxls(request):
    sttime = request.GET.get('starttime')
    edtime = request.GET.get('endtime')
    types = request.GET.get('type')
    name = request.GET.get('name')
    hosts = Host.objects.filter(user=request.user)
    # 过滤掉 验证的主机
    hosts = list(filter(lambda x: not x.is_verified, hosts))
    # 过滤掉 被删除的主机
    hosts = list(filter(lambda x: not x.is_deleted, hosts))
    data = [['客户端', '时间', '描述', '详细']]
    for prehost in hosts:
        res_list = _get_host_log_data(sttime, edtime, prehost, types)
        if res_list:
            # filter log.id
            data.append(res_list[1:])
    if len(data) == 1:
        jsonstr = {"r": "1", "e": "操作失败,无数据"}
        return HttpResponse(json.dumps(jsonstr, ensure_ascii=False))
    try:
        url, filename = exportxls(data, name)
    except Exception as e:
        jsonstr = {"r": "1", "e": e}
        return HttpResponse(json.dumps(jsonstr, ensure_ascii=False))

    jsonstr = {"r": "0", "e": "操作成功", "url": url, "filename": filename}
    return HttpResponse(json.dumps(jsonstr, ensure_ascii=False))


def getxlsdatasafe(request):
    name = request.GET.get('name')
    hosts = Host.objects.filter(user_id=request.user.id)
    # 过滤掉 验证的主机
    hosts = list(filter(lambda x: not x.is_verified, hosts))
    # 过滤掉 被删除的主机
    hosts = list(filter(lambda x: not x.is_deleted, hosts))
    data = [['客户端', '最近备份点时间', '安全评价']]
    if not hosts:
        jsonstr = {"r": "1", "e": "操作失败,无数据"}
        return HttpResponse(json.dumps(jsonstr, ensure_ascii=False))
    for host in hosts:
        tmp = datasafetyobj(request, host)
        data.append([tmp[1], tmp[2], tmp[3]])
    try:
        url, filename = exportxls(data, name)
    except Exception as e:
        jsonstr = {"r": "1", "e": e}
        return HttpResponse(json.dumps(jsonstr, ensure_ascii=False))

    jsonstr = {"r": "0", "e": "操作成功", "url": url, "filename": filename}
    return HttpResponse(json.dumps(jsonstr, ensure_ascii=False))


def _createchartline(workbook, data, headings, labels):
    worksheet = workbook.add_worksheet()
    bold = workbook.add_format({'bold': 1})
    sheetname = worksheet.get_name()
    worksheet.write_row('A1', headings, bold)
    worksheet.write_column('A2', data[0])
    worksheet.write_column('B2', data[1])
    if len(data) > 2:
        worksheet.write_column('C2', data[2])

    if len(data) > 3:
        worksheet.write_column('D2', data[3])

    # Create a new chart object. In this case an embedded chart.
    chart1 = workbook.add_chart({'type': 'line'})
    datalen = len(data[1])
    # Configure the first series.
    chart1.add_series({
        'name': '=' + sheetname + '!$B$1',
        'categories': '=' + sheetname + '!$A$2:$A$' + str(datalen - 1),
        'values': '=' + sheetname + '!$B$2:$B$' + str(datalen - 1),
        'line': {
            'color': 'red',
            'width': 1
        }
    })
    # Configure second series. Note use of alternative syntax to define ranges.
    if len(data) > 2:
        chart1.add_series({
            'name': '=' + sheetname + '!$C$1',
            'categories': '=' + sheetname + '!$A$2:$A$' + str(datalen - 1),
            'values': '=' + sheetname + '!$C$2:$C$' + str(datalen - 1),
            'line': {
                'color': 'green',
                'width': 1
            }
        })

    if len(data) > 3:
        chart1.add_series({
            'name': '=' + sheetname + '!$D$1',
            'categories': '=' + sheetname + '!$A$2:$A$' + str(datalen - 1),
            'values': '=' + sheetname + '!$D$2:$D$' + str(datalen - 1),
            'line': {
                'color': 'blue',
                'width': 1
            }
        })

    chart1.set_title({'name': labels['title']})
    chart1.set_x_axis(
        {'name': labels['x_axis'], 'interval_unit': len(data[0]) // 10, 'interval_tick': len(data[0]) // 10})

    chart1.set_y_axis({'name': labels['y_axis'],
                       'major_gridlines': {'visible': True,
                                           'line': {'width': 1.25, 'dash_type': 'dash'}}})

    # Set an Excel chart style. Colors with white outline and shadow.
    chart1.set_style(10)
    chart1.set_size({'x_scale': 3, 'y_scale': 1.5})
    # Insert the chart into the worksheet (with an offset).
    worksheet.insert_chart('E2', chart1, {'x_offset': 25, 'y_offset': 10})


def _createchartpie(workbook, data, colors, labels):
    worksheet = workbook.add_worksheet()
    chart = workbook.add_chart({'type': 'pie'})
    worksheet.write_column('A1', data[0])
    datas = list(map(lambda x: x if x else '', data[1]))
    worksheet.write_column('B1', datas)
    sheetname = worksheet.get_name()
    series = {
        'categories': '=' + sheetname + '!$A$1:$A$' + str(len(data[0])),
        'values': '=' + sheetname + '!$B$1:$B$' + str(len(data[0])),
        'points': [],
        'data_labels': {'value': True, 'center': 'leader_lines'},
    }
    for color in colors:
        series['points'].append({'fill': {'color': color}})
    chart.set_title({'name': labels['title']})
    chart.set_size({'x_scale': 0.7, 'y_scale': 1})
    chart.add_series(series)

    worksheet.insert_chart('C3', chart)


def exchart(request):
    types = request.GET.get('type')
    names = request.GET.get('name')
    exportpath = os.path.join(cur_file_dir(), 'static', 'exportsafe')
    delexpirelog(exportpath)
    timestr = datetime.datetime.now().strftime(xdatetime.FORMAT_WITH_SECOND)
    try:
        os.makedirs(exportpath)
    except OSError as e:
        pass
    tmppath = timestr + str(int(random() * 1000000)) + 'chart_pie.xlsx'
    tmppath = os.path.join(exportpath, tmppath)
    # 获取客户端，历史 备份，还原，迁移，成功/失败 次数 饼图
    if types == 'totaltimes':
        filename = xdata.PREFIX_SAFE_REPORT_SUMMARY_FILE + timestr + '_' + str(int(random() * 1000000)) + '.zip'
        rsp = totaltimes(request)
        jsonstr = json.loads(rsp.content.decode())
        _write_data_safe_(jsonstr, tmppath)
    # 用户配额状态 饼图
    if types == 'quotastatus':
        filename = xdata.PREFIX_SAFE_REPORT_USER_QUOTAS_FILE + timestr + '_' + str(int(random() * 1000000)) + '.zip'
        rsp = quotastatus(request)
        jsonstr = json.loads(rsp.content.decode())
        _write_quota_status_(jsonstr, tmppath)
    # 存储节点状态 饼图
    if types == 'storagesstatus':
        filename = xdata.PREFIX_SAFE_REPORT_STORAGE_STATUS_FILE + timestr + '_' + str(int(random() * 1000000)) + '.zip'
        rsp = _get_all_storage_info(request)
        jsonstr = json.loads(rsp.content.decode())
        _write_storage_status_(jsonstr, tmppath)
    # 用户配额增长趋势 折线图
    if types == 'quotachart':
        filename = xdata.PREFIX_SAFE_REPORT_SUMMARY_FILE + timestr + '_' + str(int(random() * 1000000)) + '.zip'
        rsp = getStorageChart(request)
        jsonstr = json.loads(rsp.content.decode())
        _write_quota_chart_(jsonstr, tmppath)
    # 存储节点增长趋势 折线图
    if types == 'storageschart':
        filename = xdata.PREFIX_SAFE_REPORT_STORAGE_CHART_FILE + timestr + '_' + str(int(random() * 1000000)) + '.zip'
        rsp = _get_storage_chart(request)
        jsonstr = json.loads(rsp.content.decode())
        _write_storage_chart_(jsonstr, tmppath)
    # 设备带宽变化 折线图
    if types == 'exbandwidth':
        filename = xdata.PREFIX_SAFE_REPORT_BAND_WIDTH_CHART_FILE + timestr + '_' + str(
            int(random() * 1000000)) + '.zip'
        rsp = getBandwidthChart(request)
        stdate = request.GET.get('starttime')
        jsonstr = json.loads(rsp.content.decode())
        _write_bandwidth_chart_(stdate, jsonstr, tmppath)
    # 设备磁盘IO变化 折线图
    if types == 'exdiskio':
        filename = xdata.PREFIX_SAFE_REPORT_DISK_CHART_FILE + timestr + '_' + str(int(random() * 1000000)) + '.zip'
        rsp = getIOChart(request)
        stdate = request.GET.get('starttime')
        jsonstr = json.loads(rsp.content.decode())
        _write_diskio_chart_(stdate, jsonstr, tmppath)

    filepath = os.path.join(exportpath, filename)
    z = zipfile.ZipFile(filepath, 'w')
    z.write(tmppath, names + timestr[:-7] + '.xlsx')
    os.remove(tmppath)
    url = '/static/exportsafe/' + filename

    jsonstr = {"r": "0", "e": "操作成功", "url": url, "filename": filename}
    return HttpResponse(json.dumps(jsonstr, ensure_ascii=False))


def _write_data_safe_(jsonstrs, tmppaths):
    workbook = xlsxwriter.Workbook(tmppaths)
    excedata0 = ['备份成功', '恢复成功', '迁移成功', '备份失败', '恢复失败', '迁移失败']
    colors = ['#20B2AA', '#FFA07A', '#FFB6C1', '#90EE90', '#D3D3D3', '#FAFAD2']
    for onepie in jsonstrs['list']:
        if not onepie['data']:
            excedata = list()
            labels = dict()
            excedata1 = [0, 0, 0, 0, 0, 0]
            labels['title'] = onepie['hostname'] + '----' + '暂时无任务执行'
            excedata.append(excedata0)
            excedata.append(excedata1)
            _createchartpie(workbook, excedata, colors, labels)
        else:
            excedata = list()
            labels = dict()
            excedata1 = list([i['value'] for i in onepie['data']])
            labels['title'] = onepie['hostname']
            excedata.append(excedata0)
            excedata.append(excedata1)
            _createchartpie(workbook, excedata, colors, labels)
    workbook.close()


def _write_quota_status_(datas, path):
    workbook = xlsxwriter.Workbook(path)
    excedata0 = ['已用空间(GB)', '可用空间(GB)']
    colors = ['#ACACAC', '#26A0DA']
    for onepie in datas['list']:
        excedata = list()
        labels = dict()
        use = round(onepie['used'] / pow(1024, 3), 2)
        free = round(onepie['free'] / pow(1024, 3), 2)
        excedata1 = [use, free]
        labels['title'] = onepie['name']
        excedata.append(excedata0)
        excedata.append(excedata1)
        _createchartpie(workbook, excedata, colors, labels)
    workbook.close()


def _write_storage_status_(datas, path):
    workbook = xlsxwriter.Workbook(path)
    excedata0 = ['已用空间(GB)', '可用空间(GB)']
    colors = ['#ACACAC', '#26A0DA']
    for onepie in datas['nodes']:
        excedata = list()
        labels = dict()
        use = round((onepie['total'] - onepie['free']) / pow(1024, 3), 2)
        free = round(onepie['free'] / pow(1024, 3), 2)
        excedata1 = [use, free]
        labels['title'] = onepie['name']
        excedata.append(excedata0)
        excedata.append(excedata1)
        _createchartpie(workbook, excedata, colors, labels)
    workbook.close()


def sort_datas_by_time(datas):
    for node in datas['nodes']:
        node['list'].sort(key=lambda elem: datetime.datetime.strptime(elem['year'], "%Y-%m-%d %H:%M:%S"))


def _write_quota_chart_(datas, path):
    workbook = xlsxwriter.Workbook(path)
    headings = ['时间', '已用空间(GB)', '可用空间(GB)', 'RAW备份数据']
    labels = {'x_axis': '时间', 'y_axis': '值：GB'}
    sort_datas_by_time(datas)
    for onepie in datas['nodes']:
        data = list()
        data1 = list()
        data2 = list()
        data3 = list()
        data0 = [i['year'] for i in onepie['list']]
        for i in onepie['list']:
            data1.append(i['used'] if 'used' in i else '')
            data2.append(i['free'] if 'free' in i else '')
            data3.append(i['raw'] if 'raw' in i else '')
        data.append(data0)
        data.append(data1)
        data.append(data2)
        data.append(data3)
        labels['title'] = onepie['name']
        _createchartline(workbook, data, headings, labels)
    workbook.close()


def _write_storage_chart_(datas, path):
    _write_quota_chart_(datas, path)


def _write_bandwidth_chart_(date, datas, path):
    workbook = xlsxwriter.Workbook(path)
    headings = ['时间', '接收(Mbps)', '发送(Mbps)']
    labels = {'x_axis': '时间({0})'.format(date), 'y_axis': '单位：Mbps'}
    data = list()
    data1 = list()
    data2 = list()
    data0 = [i['hour'][-8:] for i in datas['list']]
    for i in datas['list']:
        data1.append(i['RX'] if 'RX' in i else '')
        data2.append(i['TX'] if 'TX' in i else '')
    data.append(data0)
    data.append(data1)
    data.append(data2)
    labels['title'] = '带宽变化图'
    _createchartline(workbook, data, headings, labels)
    workbook.close()


def _write_diskio_chart_(date, datas, path):
    workbook = xlsxwriter.Workbook(path)
    headings = ['时间', '写入(MB/s)', '读出(MB/s)']
    labels = {'x_axis': '时间({0})'.format(date), 'y_axis': '单位：MB/s'}
    data = list()
    data1 = list()
    data2 = list()
    data0 = [i['hour'][-8:] for i in datas['list']]
    for i in datas['list']:
        data1.append(i['value1'] if 'value1' in i else '')
        data2.append(i['value2'] if 'value2' in i else '')
    data.append(data0)
    data.append(data1)
    data.append(data2)
    labels['title'] = '磁盘IO变化图'
    _createchartline(workbook, data, headings, labels)
    workbook.close()


def getstorageslist(request):
    username = request.GET.get('username', None)
    sused = request.GET.get('sused', None)
    eused = request.GET.get('eused', None)
    nodes = StorageNodes().get(request=None).data
    sidx = request.GET.get('sidx', None)
    sord = request.GET.get('sord', 'asc')
    ret_info_list = list()
    for storage_node in nodes:
        node_id = storage_node['id']
        user_quotas = QuotaManage().get(request=request, api_request={'node_id': node_id}).data
        if not user_quotas:
            continue

        for quota in user_quotas:
            if username is not None:
                if username not in quota['username']:
                    continue
            used_bytes = 0
            try:
                used_bytes = storage.user_used_size_mb_in_a_node(storage_node['id'], quota['user_id']) * 1024 ** 2
                if sused is not None:
                    sused = float(sused) * 1024 ** 3
                    if sused > used_bytes:
                        continue
                if eused is not None:
                    eused = float(eused) * 1024 ** 3
                    if eused < used_bytes:
                        continue
            except Exception as e:
                _logger.info('getStoragesbyObj user_used_size_mb_in_a_node Failed.e={}'.format(e))

            ret_info_list.append(
                {'id': storage_node['id'], "storage_name": storage_node['name'], "username": quota['username'],
                 "user_id": quota['user_id'], "used_bytes": used_bytes,
                 "quota_total": quota['quota_total'] * 1024 ** 2})

    if sidx in ('username', 'used_bytes',):
        reverse = True
        if sord == 'asc':
            reverse = False
        ret_info_list = sorted(ret_info_list, key=lambda elem: elem[sidx], reverse=reverse)

    return ret_info_list


def _Byte2GBp2(_bytes):
    return float('{0:.2f}'.format(_bytes / 1024 ** 3))


def getStoragesbyObj(obj):
    username = obj['username']
    storage_name = obj['storage_name']
    quota_total = obj['quota_total']
    used_bytes = obj['used_bytes']
    if quota_total == -1:
        quota_total = '无限制'
    return [username, storage_name, _Byte2GBp2(quota_total), _Byte2GBp2(used_bytes)]


def storageslist(request):
    page = int(request.GET.get('page', 1))
    rows = int(request.GET.get('rows', 30))

    paginator = Paginator(getstorageslist(request), rows)
    totalPlan = paginator.count
    totalPage = paginator.num_pages

    page = totalPage if page > totalPage else page
    currentObjs = paginator.page(page).object_list
    rowList = list()
    for Obj in currentObjs:
        detailDict = {'id': Obj['id'], 'cell': getStoragesbyObj(Obj)}
        rowList.append(detailDict)

    retInfo = {'r': 0, 'a': 'list', 'page': str(page), 'total': totalPage,
               'records': totalPlan, 'rows': rowList}

    jsonStr = json.dumps(retInfo, ensure_ascii=False)
    return HttpResponse(jsonStr)


def exportstorages(request):
    exportpath = os.path.join(cur_file_dir(), 'static', 'exportsafe')
    delexpirelog(exportpath)
    startpage = int(request.GET.get('startpage', 1) if request.GET.get('startpage', 1) else 1)
    endpage = int(request.GET.get('endpage', 1) if request.GET.get('endpage', 1) else 1)
    rows = int(request.GET.get('rows', 30) if request.GET.get('rows', 30) else 30)
    iMaxRow = int(request.GET.get('maxrow', '5000') if request.GET.get('maxrow', '5000') else 5000)
    if startpage <= 0:
        startpage = 1
    if endpage <= 0:
        endpage = 1

    timestr = datetime.datetime.now().strftime(xdatetime.FORMAT_WITH_SECOND)

    try:
        os.makedirs(exportpath)
    except OSError as e:
        pass
    filename = xdata.PREFIX_SAFE_REPORT_USER_QUOTAS_FILE + timestr + '.zip'
    filepath = os.path.join(exportpath, filename)

    paginator = Paginator(getstorageslist(request), rows)
    totalPage = paginator.num_pages
    irow = 0
    xlpatharr = list()
    for page in range(startpage, endpage + 1, 1):
        if page > totalPage:
            break;
        currentObjs = paginator.page(page).object_list
        for Obj in currentObjs:
            element = getStoragesbyObj(Obj)
            username = element[0]
            storage_name = element[1]
            quota_total = element[2]
            used = element[3]
            if irow == 0:
                wb = Workbook()
                ws = wb.add_sheet('Sheet1')
                ws.write(0, 0, '用户名')
                ws.write(0, 1, '存储单元')
                ws.write(0, 2, '配额限制（GB）')
                ws.write(0, 3, '已使用空间（GB）')
            ws.write(irow + 1, 0, username)
            ws.write(irow + 1, 1, storage_name)
            ws.write(irow + 1, 2, quota_total)
            ws.write(irow + 1, 3, used)
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
        z.write(xlpath, '用户空间状态-' + str(i) + '.xls')
    z.close()

    for xlpath in xlpatharr:
        os.remove(xlpath)

    return HttpResponse('{"r": "0","e": "操作成功","url":"/static/exportsafe/%s","filename":"%s"}' % (filename, filename))


# 获取存储节点下，主机的使用量情况
def _get_node_special_host_usage(node_path, host_idents):
    rs = {host_ident: 0 for host_ident in host_idents}
    qcow_dir = os.path.join(node_path, 'images')
    cmd = r'du --block-size=1 -d 1 {}'.format(qcow_dir)
    returned_code, lines = boxService.box_service.runCmd(cmd, True)
    if returned_code != 0:
        _logger.error('_get_node_special_host_usage cmd:{} ,error:{}|{}'.format(cmd, returned_code, lines))
        return rs
    for line in lines:
        try:
            size, path = line.split('\t')
            host_ident = os.path.relpath(path, qcow_dir)
            rs[host_ident] = int(size)  # bytes
        except Exception as e:
            _logger.error('_get_node_special_host_usage error:{}'.format(e), exc_info=True)
    return rs


# 客户端在各个存储节点占用的大小
@catch_http_request_exception
def host_storages_status(request):
    page = int(request.POST.get('page', 1))
    rows = int(request.POST.get('rows', 30))
    host_idents = json.loads(request.POST.get('host_ids', '[]'))

    all_host_info, all_nodes = _host_storages_status_work(host_idents, request)
    _logger.info('rows:{}'.format(rows))
    paginator = Paginator(all_host_info, rows)
    totalPlan = paginator.count
    totalPage = paginator.num_pages

    page = totalPage if page > totalPage else page
    currentObjs = paginator.page(page).object_list

    retInfo = {'r': 0, 'a': 'list', 'page': str(page), 'total': totalPage,
               'records': totalPlan, 'all_host_info': currentObjs, 'all_nodes': all_nodes}

    jsonStr = json.dumps(retInfo, ensure_ascii=False)
    return HttpResponse(jsonStr)


def _host_storages_status_work(host_idents, request):
    # 找出所有的节点, 统计主机
    all_nodes = list()
    user_quotas = UserQuota.objects.filter(user_id=request.user.id, deleted=False)
    for quota_obj in user_quotas:
        node_obj = quota_obj.storage_node
        node_tools = UserQuotaTools(node_obj.id, request.user.id, quota_obj.quota_size)
        try:
            free_bytes = node_tools.get_user_available_storage_size_in_node(True) * 1024 ** 2
            used_bytes = storage.user_used_size_mb_in_a_node(node_obj.id, request.user.id) * 1024 ** 2
        except Exception:
            free_bytes, used_bytes = 0, 0

        host_use_info = _get_node_special_host_usage(node_obj.path, host_idents)

        all_nodes.append({"name": node_obj.name, "total": free_bytes + used_bytes,
                          'label': '{}({})'.format(node_obj.name, functions.format_size(free_bytes + used_bytes)),
                          "free": free_bytes, 'used': used_bytes,
                          'key': node_obj.ident,
                          'host_use_info': host_use_info})

    all_host_info = list()
    host_all = {'name': '所有主机', 'ident': 'all', 'nodes': {node['key']: 0 for node in all_nodes}, 'total': 0}

    for host_ident in host_idents:
        host = Host.objects.get(ident=host_ident)
        info = {'name': host.name, 'ident': host.ident, 'nodes': dict(), 'total': 0}
        for node in all_nodes:
            size = node['host_use_info'].get(host.ident, 0)
            info['nodes'][node['key']] = size
            host_all['nodes'][node['key']] += size
            info['total'] += size
        all_host_info.append(info)
    host_all['total'] = sum(host_all['nodes'].values())
    all_host_info.sort(key=lambda x: x['total'], reverse=True)
    all_host_info.insert(0, host_all)  # 注入所有的统计

    all_nodes_total = sum([node['total'] for node in all_nodes])
    nodes_map = {node['key']: node for node in all_nodes}

    def _format(host_info):
        total_size_str = functions.format_size(host_info['total'])
        total_progress_str = functions.format_progress(host_info['total'], all_nodes_total)
        if total_size_str == '0B':
            host_info['total'] = '0'
        else:
            host_info['total'] = '{}({})'.format(total_size_str, total_progress_str)

        for ident, value in host_info.pop('nodes').items():
            size_str = functions.format_size(value)
            progress_str = functions.format_progress(value, nodes_map[ident]['total'])
            if size_str == '0B':
                value = '0'
            else:
                value = '{}({})'.format(size_str, progress_str)
            host_info[ident] = value
        return host_info

    all_host_info = [_format(host_info) for host_info in all_host_info]

    return all_host_info, all_nodes


# 客户端在各个存储节点占用的大小, 导出
@catch_http_request_exception
def host_storages_status_ex(request):
    or_data = json.loads(request.POST.get('data', '{}'))
    all_host_info, all_nodes = or_data['all_host_info'], or_data['all_nodes']
    first = ['客户端']
    first.extend(['各存储节点使用量' for _ in all_nodes])
    first.append('合计')
    second = ['客户端']
    second.extend([node['label'] for node in all_nodes])
    second.append('合计')
    head = [first, second]

    def _my_format(row):
        rs = list()
        rs.append(row['name'])
        for node in all_nodes:
            rs.append(row[node['key']])
        rs.append(row['total'])
        return rs

    def _merge(ws):
        ws.write_merge(0, 1, 0, 0, '客户端', easyxf('align: vert centre, horiz center'))
        ws.write_merge(0, 0, 1, len(all_nodes), '各存储节点使用量', easyxf('align: vert centre, horiz center'))
        ws.write_merge(0, 1, len(all_nodes) + 1, len(all_nodes) + 1, '合计', easyxf('align: vert centre, horiz center'))

    body = list(map(_my_format, all_host_info))
    head.extend(body)
    _logger.info('host_storages_status_ex data:{}'.format(head))
    name = '客户端容量使用统计'
    try:
        url, filename = exportxls(head, name, _merge)
    except Exception as e:
        jsonstr = {"r": "1", "e": e}
        return HttpResponse(json.dumps(jsonstr, ensure_ascii=False))

    jsonstr = {"r": "0", "e": "操作成功", "url": url, "filename": filename}
    return HttpResponse(json.dumps(jsonstr, ensure_ascii=False))


def _query_task_type_count_summary(begin_date, end_date, task_types, hosts, group_type):
    """
    查询任务数量的摘要信息
    :param begin_date: date 开始日期（从0点开始）
    :param end_date: date 结束日期（到24点结束）
    :param task_types: list[task_type|str,] 任务类型，可选“snapshot”，“cdp”，“restore_host”，“restore_volume”，“migrate”，“cluster”
    :param hosts: list[host_ident|str,] 主机
    :param group_type: str，可选“by_task_type”，“by_host”
    :return:
    [
        {
            "host_ident": str,
            "host_name": str,
            "task_type": str, 可选“snapshot”，“cdp”，“restore_host”，“restore_volume”，“migrate”，“cluster”……
            "task_type_display": str,
            "task_succeed": int,
            "task_execute": int,
            "percent": str,
        },
    ]
    """

    def __get_task_type_dispaly(_task_type):
        if _task_type == 'snapshot':
            return '快照备份'
        elif _task_type == 'cdp':
            return 'CDP保护'
        elif _task_type == 'restore_host':
            return '整机重建'
        elif _task_type == 'restore_volume':
            return '数据卷重建'
        elif _task_type == 'migrate':
            return '整机迁移'
        elif _task_type == 'cluster':
            return '集群备份'
        else:
            xlogging.raise_and_logging_error(r'无效的参数', 'invalid type : {}'.format(_task_type))
            return ''  # never run 移除IDE警告

    def __query_tasks(_begin_datetime, _end_datetime, _db_objs, _hosts):
        return _db_objs.filter(host_snapshot__host__ident__in=_hosts) \
            .exclude(finish_datetime__isnull=True, finish_datetime__lt=_begin_datetime,
                     start_datetime__gte=_end_datetime) \
            .all()

    def __standard_item_percent(_item):
        if _item['task_execute'] == 0:
            _item['percent'] = '-'
        elif _item['task_succeed'] == _item['task_execute']:
            _item['percent'] = '100'
        elif _item['task_succeed'] == 0:
            _item['percent'] = '0'
        else:
            _item['percent'] = '{:.01f}'.format(_item['task_succeed'] * 100 / _item['task_execute'])

    def __standard_items(_hosts, _task_type, _task_type_display, temp_dict):
        _result = list()
        for _, _item in temp_dict.items():
            _result.append(_item)

        for host in _hosts:
            for _item in _result:
                if _item['host_ident'] == host:
                    break
            else:
                _host_obj = Host.objects.get(ident=host)
                _result.append({
                    'host_ident': host,
                    'host_name': _host_obj.display_name,
                    'task_type': _task_type,
                    'task_type_display': _task_type_display,
                    'task_succeed': 0,
                    'task_execute': 0,
                })
        return _result

    def __get_host_obj(_task_obj):
        return _task_obj.host_snapshot.host

    def __get_cluster_hosts(_task_obj):
        _hosts = _task_obj.schedule.hosts.all()
        idents = sorted([host.ident for host in _hosts])
        names = '\r\n'.join(sorted([host.display_name for host in _hosts]))
        return idents, names

    def __convert_to_items(_hosts, _tasks, _task_type, task_type_display, __get_host_obj_func=__get_host_obj):
        temp_dict = dict()
        for task in _tasks:
            _host_obj = __get_host_obj_func(task)
            _item = temp_dict.get(_host_obj.ident, None)
            if _item is None:
                temp_dict[_host_obj.ident] = {
                    'host_ident': _host_obj.ident,
                    'host_name': _host_obj.display_name,
                    'task_type': _task_type,
                    'task_type_display': task_type_display,
                    'task_succeed': 1 if task.successful else 0,
                    'task_execute': 1,
                }
            else:
                _item['task_execute'] = _item['task_execute'] + 1
                if task.successful:
                    _item['task_succeed'] = _item['task_succeed'] + 1
        return __standard_items(_hosts, _task_type, task_type_display, temp_dict)

    def __query_cluster_items(_hosts, _tasks, _task_type, task_type_display):
        temp_dict, cluster_hosts, not_in_cluster = dict(), list(), list()
        for task in _tasks:
            idents, names = __get_cluster_hosts(task)
            if not (set(idents) & set(_hosts)):
                continue
            _hosts_idents = ','.join(idents)
            _item = temp_dict.get(_hosts_idents, None)
            if _item is None:
                temp_dict[_hosts_idents] = {
                    'host_ident': _hosts_idents,
                    'host_name': names,
                    'task_type': _task_type,
                    'task_type_display': task_type_display,
                    'task_succeed': 1 if task.successful else 0,
                    'task_execute': 1,
                }
            else:
                _item['task_execute'] = _item['task_execute'] + 1
                if task.successful:
                    _item['task_succeed'] = _item['task_succeed'] + 1
            cluster_hosts.extend(idents)

        for host in Host.objects.filter(ident__in=set(_hosts) - set(cluster_hosts)):
            not_in_cluster.append({
                'host_ident': host.ident,
                'host_name': host.display_name,
                'task_type': _task_type,
                'task_type_display': task_type_display,
                'task_succeed': 0,
                'task_execute': 0,
            })

        return list(temp_dict.values()) + not_in_cluster

    def __report_by_task_type(_task_type, _summary_items, _task_type_display):
        _have_execute = [x for x in _summary_items if (x['task_type'] == _task_type and x['task_execute'] != 0)]
        _have_execute.sort(key=lambda x: x['host_name'])

        _total_succeed = 0
        _total_execute = 0
        for _item in _have_execute:
            _total_succeed = _total_succeed + _item['task_succeed']
            _total_execute = _total_execute + _item['task_execute']

        _not_execute = [x for x in _summary_items if (x['task_type'] == _task_type and x['task_execute'] == 0)]
        _not_execute.sort(key=lambda x: x['host_name'])

        # 有数据的排前面，然后按照主机名排序

        _total = {
            'host_ident': r'-',
            'host_name': '所有主机',
            'task_type': _task_type,
            'task_type_display': _task_type_display,
            'task_succeed': _total_succeed,
            'task_execute': _total_execute,
        }

        _result = [_total, ]
        _result.extend(_have_execute)
        _result.extend(_not_execute)

        return _result

    def __report_host_total(_task_type, _summary_items, _task_type_display):
        _total_succeed = 0
        _total_execute = 0
        for _item in _summary_items:
            if _item['task_type'] == _task_type:
                _total_succeed = _total_succeed + _item['task_succeed']
                _total_execute = _total_execute + _item['task_execute']
        return {
            'host_ident': r'-',
            'host_name': '所有主机',
            'task_type': _task_type,
            'task_type_display': _task_type_display,
            'task_succeed': _total_succeed,
            'task_execute': _total_execute,
        }

    def __report_host_one(_host, _summary_items, _task_type):
        if _task_type == 'cluster':
            for x in _summary_items:
                if _host in x['host_ident'] and x['task_type'] == _task_type and not x.get('accessed', False):
                    x['accessed'] = True
                    return x
            return None

        for x in _summary_items:
            if x['host_ident'] == _host and x['task_type'] == _task_type:
                return x
        return None

    def __report_host_one_total(_host_obj, _summary_items):
        _total_succeed = 0
        _total_execute = 0
        _host_ident, _host_display = _host_obj.ident, _host_obj.display_name
        for _item in _summary_items:
            if _item['host_ident'] == _host_ident:
                _total_succeed = _total_succeed + _item['task_succeed']
                _total_execute = _total_execute + _item['task_execute']
        _total = {
            'host_ident': _host_ident,
            'host_name': _host_display,
            'task_type': r'-',
            'task_type_display': '所有任务',
            'task_succeed': _total_succeed,
            'task_execute': _total_execute,
        }
        return _total

    def __standard_result(_result, current_row_property):
        current_row_item = None
        for _item in _result:
            __standard_item_percent(_item)
            if (current_row_item is None) or (current_row_item[current_row_property] != _item[current_row_property]):
                current_row_item = _item
                current_row_item['rowspan'] = 1
            else:
                current_row_item['rowspan'] = current_row_item['rowspan'] + 1

    ########################################################################

    begin_datetime = datetime.datetime.combine(begin_date, datetime.time.min)
    end_datetime = datetime.datetime.combine(end_date, datetime.time.min) + timedelta(days=1)

    summary_items = list()
    for task_type in task_types:
        if task_type == 'snapshot':
            summary_items.extend(
                __convert_to_items(hosts, __query_tasks(begin_datetime, end_datetime, BackupTask.objects, hosts),
                                   task_type, __get_task_type_dispaly(task_type))
            )
        elif task_type == 'cdp':
            summary_items.extend(
                __convert_to_items(hosts, __query_tasks(begin_datetime, end_datetime, CDPTask.objects, hosts),
                                   task_type, __get_task_type_dispaly(task_type))
            )
        elif task_type == 'restore_host':
            tasks = RestoreTask.objects.filter(
                host_snapshot__host__ident__in=hosts, type__in=[RestoreTask.TYPE_HOST, RestoreTask.TYPE_PE]) \
                .exclude(
                finish_datetime__isnull=True, finish_datetime__lt=begin_datetime, start_datetime__gte=end_datetime) \
                .all()
            summary_items.extend(
                __convert_to_items(hosts, tasks, task_type, __get_task_type_dispaly(task_type))
            )
        elif task_type == 'restore_volume':
            tasks = RestoreTask.objects.filter(
                host_snapshot__host__ident__in=hosts, type=RestoreTask.TYPE_VOLUME) \
                .exclude(
                finish_datetime__isnull=True, finish_datetime__lt=begin_datetime, start_datetime__gte=end_datetime) \
                .all()
            summary_items.extend(
                __convert_to_items(hosts, tasks, task_type, __get_task_type_dispaly(task_type))
            )
        elif task_type == 'migrate':
            tasks = MigrateTask.objects.filter(source_host__ident__in=hosts) \
                .exclude(
                finish_datetime__isnull=True, finish_datetime__lt=begin_datetime, start_datetime__gte=end_datetime) \
                .all()
            summary_items.extend(
                __convert_to_items(hosts, tasks, task_type, __get_task_type_dispaly(task_type), lambda x: x.source_host)
            )
        elif task_type == 'cluster':
            tasks = ClusterBackupTask.objects.exclude(
                finish_datetime__isnull=True, finish_datetime__lt=begin_datetime, start_datetime__gte=end_datetime) \
                .all()
            summary_items.extend(
                __query_cluster_items(hosts, tasks, task_type, __get_task_type_dispaly(task_type))
            )
        else:
            xlogging.raise_and_logging_error(r'无效的参数', 'invalid type : {}'.format(task_type))
            return list()  # never run 移除IDE警告

    if group_type == 'by_task_type':
        result = list()
        for tt in ['snapshot', 'cdp', 'cluster', 'restore_host', 'restore_volume', 'migrate']:
            if tt not in task_types:
                continue
            result.extend(__report_by_task_type(tt, summary_items, __get_task_type_dispaly(tt)))
        __standard_result(result, 'task_type')
        return result
    elif group_type == 'by_host':
        result = list()
        for tt in ['snapshot', 'cdp', 'cluster', 'restore_host', 'restore_volume', 'migrate']:
            if tt not in task_types:
                continue
            result.append(__report_host_total(tt, summary_items, __get_task_type_dispaly(tt)))
        host_objs = [Host.objects.get(ident=x) for x in hosts]
        host_objs.sort(key=lambda x: x.display_name)
        for host_obj in host_objs:
            if len(task_types) not in (0, 1,):
                result.append(__report_host_one_total(host_obj, summary_items))
            for tt in ['snapshot', 'cdp', 'cluster', 'restore_host', 'restore_volume', 'migrate']:
                if tt not in task_types:
                    continue
                report_one = __report_host_one(host_obj.ident, summary_items, tt)
                if report_one:
                    result.append(report_one)
        __standard_result(result, 'host_ident')
        return result
    else:
        xlogging.raise_and_logging_error(r'无效的参数', 'invalid group_type : {}'.format(group_type))
        return list()  # never run 移除IDE警告


@catch_http_request_exception
def query_hosts_tasks_summary(request):
    q_data = json.loads(request.GET['q_data'])
    time_range = q_data['time_range']
    task_types = q_data['task_types']
    hosts = q_data['hosts']
    group_type = q_data['group_type']
    begin_date = datetime.datetime.fromtimestamp(time_range[0] / 1000).date()
    end_date = datetime.datetime.fromtimestamp(time_range[1] / 1000).date()

    summaries = _query_task_type_count_summary(begin_date, end_date, task_types, hosts, group_type)
    return HttpResponse(json.dumps({'r': 0, 'e': 'ok', 'summaries': summaries}, ensure_ascii=False))


def make_newline_for_xls(cell_info):
    return cell_info.replace('\r\n', '、')


def deal_merge_range(summaries, group_type, worksheet):
    for i, summary in enumerate(summaries, 1):
        if 'rowspan' not in summary:
            continue
        label = summary['host_name'] if group_type == 'by_host' else summary['task_type_display']
        label = make_newline_for_xls(label)
        worksheet.write_merge(i, i + summary['rowspan'] - 1, 0, 0, label, easyxf('align: vert centre, horiz center'))


@catch_http_request_exception
def export_hosts_tasks_summary(request):
    q_data = json.loads(request.GET['q_data'])
    time_range = q_data['time_range']
    task_types = q_data['task_types']
    hosts = q_data['hosts']
    group_type = q_data['group_type']
    begin_date = datetime.datetime.fromtimestamp(time_range[0] / 1000).date()
    end_date = datetime.datetime.fromtimestamp(time_range[1] / 1000).date()

    summaries = _query_task_type_count_summary(begin_date, end_date, task_types, hosts, group_type)
    if group_type == 'by_host':
        item_1st = ['主机名', '任务类型', '任务成功数', '任务执行数', '百分比']
        tab_data = [
            [make_newline_for_xls(item['host_name']), item['task_type_display'], str(item['task_succeed']),
             str(item['task_execute']), item['percent']] for item in summaries]
    else:
        item_1st = ['任务类型', '主机名', '任务成功数', '任务执行数', '百分比']
        tab_data = [
            [item['task_type_display'], make_newline_for_xls(item['host_name']), str(item['task_succeed']),
             str(item['task_execute']), item['percent']] for item in summaries]

    tab_data.insert(0, item_1st)
    if len(tab_data) == 1:
        return HttpResponse(json.dumps({'r': 1, 'e': '操作失败,无数据'}, ensure_ascii=False))

    url, filename = exportxls(tab_data, '任务统计', partial(deal_merge_range, summaries, group_type))
    return HttpResponse(json.dumps({'r': 0, 'e': 'ok', 'url': url, 'filename': filename}, ensure_ascii=False))


def locked_snapshots(request):
    host_ident = request.GET['host']
    snapshots = HostSnapshot.objects.filter(host__ident=host_ident,
                                            successful=True,
                                            start_datetime__isnull=False,
                                            deleted=False,
                                            ).exclude(disk_snapshots__reference_tasks='')
    _TASK_RE = re.compile('([A-Za-z_]+)(\d+)')

    def _format(reference_task):
        rs = {'task_type': '', 'task_id': '', 'or_str': reference_task, 'task_name': reference_task}
        try:
            match = _TASK_RE.match(reference_task)
            if not match or len(match.groups()) != 2:
                return rs
            prefix, num = match.groups()
            rs['task_id'] = num
            rs['task_type'] = prefix
            if prefix.startswith('htb_task'):
                rs['task_name'] = HTBTask.objects.get(id=num).name
            elif prefix.startswith('migrate'):
                rs['task_name'] = MigrateTask.objects.get(id=num).name
            elif prefix.startswith(('restore', 'volume_restore')):
                rs['task_name'] = RestoreTask.objects.get(id=num).name
            elif prefix.startswith('shared'):
                rs['task_name'] = HostSnapshotShare.objects.get(id=num).name
            elif prefix.startswith('takeover'):
                rs['task_name'] = TakeOverKVM.objects.get(id=num).name
                if rs['task_name'].startswith('auto_verify_'):
                    rs['task_name'] = '自动验证{}'.format(rs['task_name'][12:])
            elif prefix.startswith('vmr_restore'):
                rs['task_name'] = VirtualMachineRestoreTask.objects.get(id=num).name
            elif prefix.startswith('remote_back_up_sub_task'):
                rs['task_name'] = RemoteBackupSubTask.objects.get(id=num).name
            elif prefix.startswith(('merge_disk_task', 'delete_disk_task', 'cdp_merge_sub_task')):
                rs['task_name'] = SpaceCollectionTask.objects.get(id=num).name
            elif prefix.startswith('deploy_template'):
                rs['task_name'] = DeployTemplate.objects.get(id=num).name
                if rs['task_name'].startswith('deploy_template_'):
                    rs['task_name'] = '部署模板{}'.format(rs['task_name'][16:])
            else:
                pass
        except Exception as e:
            _logger.error('locked_snapshots _format error:{}'.format(e), exc_info=True)
        return rs

    lock_snapshots = list()
    for snapshot in snapshots:
        reference_tasks = set([disk_snapshot.reference_tasks for disk_snapshot in
                               snapshot.disk_snapshots.filter(merged=False).exclude(reference_tasks='')])

        lock_snapshots.append({'name': snapshot.name, 'status': list(map(_format, reference_tasks))})

    return HttpResponse(json.dumps({'r': 0, 'e': '', 'lock_snapshots': lock_snapshots}, ensure_ascii=False))


def upload_test(request):
    files = request.FILES
    for _, handle in files.items():
        _logger.info('name size :{}|{}'.format(handle.name, handle.size))
        with open('/tmp/{}'.format(handle.name), 'wb') as f:
            for chunk in handle.chunks(64 * 1024):
                f.write(chunk)
    return HttpResponse()


def bussinessreport_handle(request):
    a = request.GET.get('a', 'none')
    if a == 'none':
        a = request.POST.get('a', 'none')
    if a == 'totaltimes':
        return totaltimes(request)
    if a == 'recentlyevents':
        return recentlyevents(request)
    if a == 'excutelist':
        return excutelist(request)
    if a == 'datasafety':
        return datasafety(request)
    if a == 'storagechart':
        return _get_storage_chart(request)
    if a == 'storageinfo':
        return _get_storage_info(request)
    if a == 'allstorageinfo':
        return _get_all_storage_info(request)
    if a == 'getstorages':
        return _get_nodes_list(request)
    if a == 'quotastatus':
        return quotastatus(request)
    if a == 'saferange':
        return saferange(request)
    if a == 'getxls':
        return getxls(request)
    if a == 'getxlsdatasafe':
        return getxlsdatasafe(request)
    if a == 'exchart':
        return exchart(request)
    if a == 'storageslist':
        return storageslist(request)
    if a == 'exportstorages':
        return exportstorages(request)
    if a == 'host_storages_status':
        return host_storages_status(request)
    if a == 'host_storages_status_ex':
        return host_storages_status_ex(request)
    if a == 'query_hosts_tasks_summary':
        return query_hosts_tasks_summary(request)
    if a == 'export_hosts_tasks_summary':
        return export_hosts_tasks_summary(request)
    if a == 'locked_snapshots':
        return locked_snapshots(request)
    if a == 'upload_test':
        return upload_test(request)

    return HttpResponse(json.dumps({"r": "1", "e": "没有对应的action:{}".format(html.escape(a))}))
