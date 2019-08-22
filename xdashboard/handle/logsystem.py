# coding=utf-8
import datetime
import html
import json
import os
import time
import zipfile

from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse

from box_dashboard import xdata, xdatetime
from xdashboard.handle.authorize.authorize_init import get_separation_of_the_three_members
from xdashboard.models import OperationLog, Email
from xdashboard.request_util import get_operator
from .logserver import SaveOperationLog
from .xlwt.Workbook import *


def getlog(request):
    log_type = request.GET.get('type', '-1')
    sub_type = request.GET.get('subtype', '-1')
    event = request.GET.get('event', '')
    desc = request.GET.get('desc', '')
    stime = request.GET.get('stime', '')
    etime = request.GET.get('etime', '')
    username = request.GET.get('username', '')
    user_type = request.GET.get('user_type', '')
    operator_user = request.GET.get('operator_user', None)
    if desc == 'debug':
        desc = ''
    sidx = request.GET.get('sidx', None)
    sord = request.GET.get('sord', 'asc')
    if sidx not in ('datetime', 'event', 'desc', 'user_id', 'operator',):
        sidx = None
    if sidx and sord == 'desc':
        sidx = '-{}'.format(sidx)

    if get_separation_of_the_three_members().is_separation_of_the_three_members_available():
        logs = OperationLog.objects.filter(~Q(user_id=request.user.id))
    else:
        logs = OperationLog.objects.filter(user_id=request.user.id)

    if username or user_type:
        users = User.objects.filter()
        if username:
            users = users.filter(username__contains=username)
        if user_type == 'sec-admin':
            users = users.filter(is_superuser=True)
        elif user_type:
            users = users.filter(userprofile__user_type=user_type)

        logs = logs.filter(user__in=users)

    if operator_user:
        op_users = User.objects.filter()
        op_users = op_users.filter(username__contains=operator_user)
        logs = logs.filter(Q(operator__contains=operator_user) | (Q(operator=None) & Q(user__in=op_users)))

    if log_type == '1':
        logs = logs.filter(event__in=(1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14,))

    if log_type == '2':
        logs = logs.filter(event__in=(0,))

    if log_type == '3':
        logs = logs.filter(event__in=(-1,))

    if log_type == '-1':
        # 不包含升级日志
        logs = logs.exclude(event__in=(20, 21, 22, 23))

    if log_type == 'update':
        if sub_type == '-1':
            logs = logs.filter(event__in=(20, 21, 22, 23))
        else:
            logs = logs.filter(event__in=(20, 21, 22, 23))

    time1 = datetime.datetime.strptime('2016-07-20 00:00:00', '%Y-%m-%d %H:%M:%S')
    if stime:
        time1 = datetime.datetime.strptime(stime, '%Y-%m-%d %H:%M:%S')

    time2 = datetime.datetime.now()
    if etime:
        etime += '.999'
        time2 = datetime.datetime.strptime(etime, '%Y-%m-%d %H:%M:%S.%f')

    if stime or etime:
        logs = logs.filter(datetime__range=(time1, time2))

    if desc and desc != 'debug':
        logs = logs.filter(desc__contains=desc)

    if (event) and (event != '0'):
        logs = logs.filter(event=int(event))

    if sidx is not None:
        logs = logs.order_by(sidx)
    else:
        logs = logs.order_by('-datetime')

    return logs


def FmtAdapter(adapter):
    if len(adapter) == 0:
        return '无'
    strret = ''
    for item in adapter:
        strret += '\r\n网卡名称：' + item['name']
        if item['ip']:
            strret += '\r\nIP：' + item['ip']
        if item['submask']:
            strret += '\r\n子网掩码：' + item['submask']
        if item['routers']:
            strret += '\r\n默认网关：' + item['routers']
        if str(item['enablepxe']) == '1':
            strret += '\r\nPXE开始IP：' + item['rangestart']
            strret += '\r\nPXE结束IP：' + item['rangeend']
            if item['pxerouters']:
                strret += '\r\nPXE网关：' + item['pxerouters']
            if item['pxedns']:
                strret += '\r\nPXEDNS：' + item['pxedns']
        else:
            strret += '\r\n启用PXE：未启用'
    return strret


def FmtAggregation(aggregation):
    if len(aggregation) == 0:
        return '无'
    strret = ''
    for item in aggregation:
        strret += '\r\n网卡名称：' + item['name']
        if item['ip']:
            strret += '\r\nIP：' + item['ip']
        if item['submask']:
            strret += '\r\n子网掩码：' + item['submask']
        if item['routers']:
            strret += '\r\n默认网关：' + item['routers']
        adaptername = list()
        for adapter in item['list']:
            adaptername.append(adapter['name'])
        strret += '\r\n聚合的网卡：' + '，'.join(adaptername)
        if len(adaptername) == 1:
            strret += '(自动设为未聚合网卡)'
        if str(item['enablepxe']) == '1':
            strret += '\r\nPXE开始IP：' + item['rangestart']
            strret += '\r\nPXE结束IP：' + item['rangeend']
            if item['pxerouters']:
                strret += '\r\nPXE网关：' + item['pxerouters']
            if item['pxedns']:
                strret += '\r\nPXEDNS：' + item['pxedns']
        else:
            strret += '\r\n启用PXE：未启用'

    return strret


def translatekey(key):
    if str(key) == 'adapter':
        return '未聚合网卡'
    if str(key) == 'aggregation':
        return '聚合网卡'
    if str(key) == 'dns':
        return 'DNS'
    if str(key) == 'name':
        return '名称'
    if str(key) == 'target_info':
        return '备机'
    if str(key) == 'host':
        return '主机'
    if str(key) == 'task_type':
        return '计划类型'
    if str(key) == 'restore_type':
        return '任务类型'
    return str(key)


def FmtTaskType(value):
    if value == 0:
        return '还原到特定点'
    if value == 1:
        return '还原到最新'
    return value


def format_restore_type(value):
    if value == 0:
        return '卷还原'
    if value == 1:
        return '系统还原'
    return value


def translatevalue(key, value):
    if str(key) == 'adapter':
        return FmtAdapter(value)
    if str(key) == 'aggregation':
        return FmtAggregation(value)
    if str(key) == 'task_type':
        return FmtTaskType(value)
    if str(key) == 'restore_type':
        return format_restore_type(value)
    return str(value)


def FmtHotbackupPlanDetail(hotbackup_plan_detail):
    desc = ''
    for (key, value) in hotbackup_plan_detail.items():
        if key in ('ext_config', 'filter', 'dst_host_ident'):
            continue
        if desc != '':
            desc += '\r\n'
        desc += translatekey(key) + ':' + translatevalue(key, value)
    return desc


def _user_type_fmt(user_type):
    if user_type == 'normal-admin':
        return '系统管理员'

    if user_type == 'sec-admin':
        return '安全保密管理员'

    if user_type == 'aud-admin':
        return '安全审计管理员'
    return 'unknown({})'.format(user_type)


def getLogbyObj(itemObject, isDebug):
    type = '警告'
    time = str(itemObject.datetime)[0:19] if itemObject.datetime is not None else '--'
    event = itemObject.get_event_display()
    desc = ''
    if isDebug:
        desc = itemObject.desc
    else:
        try:
            strResult = ''
            for (key, value) in json.loads(itemObject.desc).items():
                if str(key) == 'debug':
                    continue
                if str(key) == '操作结果':
                    strResult = str(key) + "：" + str(value)
                elif str(key) == 'hotbackup_plan_detail':
                    strResult = FmtHotbackupPlanDetail(value)
                else:
                    if desc != '':
                        desc += '\r\n'
                    desc += translatekey(key) + ':' + translatevalue(key, value)
            if strResult:
                desc += '\r\n' + strResult
        except Exception as e:
            desc = str(e) + itemObject.desc
    if int(itemObject.event) in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 20, 21, 22, 23, 24, 28, 29):
        type = '信息'
    if int(itemObject.event) in (0,):
        type = '错误'
    if int(itemObject.event) in (-1,):
        type = '警告'
    username = itemObject.user.username
    if itemObject.user.is_superuser:
        user_type = 'sec-admin'
    else:
        user_type = itemObject.user.userprofile.user_type
    operator_user = itemObject.operator
    if not operator_user:
        operator_user = username
    return [type, operator_user, username, _user_type_fmt(user_type), time, event, desc]


# rows每页条数  page想获取第几页
def getLogList(request):
    page = int(request.GET.get('page', 1))
    rows = int(request.GET.get('rows', 30))
    desc = request.GET.get('desc', '')
    isDebug = False
    if desc == 'debug':
        isDebug = True

    paginator = Paginator(getlog(request), rows)
    totalPlan = paginator.count
    totalPage = paginator.num_pages

    page = totalPage if page > totalPage else page
    currentObjs = paginator.page(page).object_list
    rowList = list()
    for Obj in currentObjs:
        detailDict = {'id': Obj.id, 'cell': getLogbyObj(Obj, isDebug)}
        rowList.append(detailDict)

    retInfo = {'r': 0, 'a': 'list', 'page': str(page), 'total': totalPage,
               'records': totalPlan, 'rows': rowList}
    jsonStr = json.dumps(retInfo, ensure_ascii=False)

    return HttpResponse(jsonStr)


def delAllLog(request):
    # 删除所有日志
    try:
        type = request.GET.get('type', '-1')
        logs = OperationLog.objects.filter(user_id=request.user.id)
        if type == 'update':
            logs = logs.filter(event__in=(20, 21, 22, 23))
        logs.delete()
    except Exception as e:
        return HttpResponse('{"r": "1","e": "' + str(e) + '"}')
    mylog = {'操作': '删除所有日志', '操作结果': "删除成功"}
    SaveOperationLog(
        request.user, OperationLog.TYPE_OP_LOG, json.dumps(mylog, ensure_ascii=False), get_operator(request))
    return HttpResponse('{"r": "0","e": "操作成功"}')


def delLogByIds(request):
    # 根据ID删除日志
    ids = request.GET.get('ids', '0').split(',')
    try:
        OperationLog.objects.filter(id__in=ids).delete()
    except Exception as e:
        return HttpResponse('{"r": "1","e": "' + str(e) + '"}')
    mylog = {'操作': '删除选定的日志', '操作结果': "删除成功"}
    SaveOperationLog(
        request.user, OperationLog.TYPE_OP_LOG, json.dumps(mylog, ensure_ascii=False), get_operator(request))
    return HttpResponse('{"r": "0","e": "操作成功"}')


def cur_file_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def delexpirelog(exportpath):
    ctime = time.time()
    for dirpath, dirnames, filenames in os.walk(exportpath):
        for filename in filenames:
            thefile = os.path.join(dirpath, filename)
            if os.path.splitext(thefile)[1] in ('.zip', '.xls'):
                mtime = os.path.getmtime(thefile)
                if ctime - mtime > 2 * 60 * 60:
                    os.remove(thefile)


def exportLog(request):
    exportpath = os.path.join(cur_file_dir(), 'static', 'exportlog')
    log_type = request.GET.get('type', '-1')
    delexpirelog(exportpath)
    try:
        startpage = int(request.GET.get('startpage', 1) if request.GET.get('startpage', 1) else 1)
        endpage = int(request.GET.get('endpage', 1) if request.GET.get('endpage', 1) else 1)
        rows = int(request.GET.get('rows', 30) if request.GET.get('rows', 30) else 30)
        iMaxRow = int(request.GET.get('maxrow', '5000') if request.GET.get('maxrow', '5000') else 5000)
        timestr = datetime.datetime.now().strftime(xdatetime.FORMAT_WITH_SECOND)
    except:
        return HttpResponse('{"r": "1","e": "操作失败,请输入有效的参数"}')
    if startpage <= 0:
        startpage = 1
    if endpage <= 0:
        endpage = 1

    try:
        os.makedirs(exportpath)
    except OSError as e:
        pass
    filename = xdata.PREFIX_LOG_OPERATION_FILE + timestr + '.zip';
    if log_type == 'update':
        filename = xdata.PREFIX_LOG_UPDATE_FILE + timestr + '.zip';
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
            element = getLogbyObj(Obj, False)
            type = element[0]
            username = element[1]
            user_type = element[2]
            time = element[3]
            event = element[4]
            desc = element[5]
            if get_separation_of_the_three_members().is_separation_of_the_three_members_available():
                if irow == 0:
                    wb = Workbook()
                    ws = wb.add_sheet('Sheet1')
                    ws.write(0, 0, '类型')
                    ws.write(0, 1, '操作者')
                    ws.write(0, 2, '操作者类型')
                    ws.write(0, 3, '时间')
                    ws.write(0, 4, '事件')
                    ws.write(0, 5, '描述')
                ws.write(irow + 1, 0, type)
                ws.write(irow + 1, 1, username)
                ws.write(irow + 1, 2, user_type)
                ws.write(irow + 1, 3, time)
                ws.write(irow + 1, 4, event)
                ws.write(irow + 1, 5, desc)
            else:
                if irow == 0:
                    wb = Workbook()
                    ws = wb.add_sheet('Sheet1')
                    ws.write(0, 0, '类型')
                    ws.write(0, 1, '时间')
                    ws.write(0, 2, '事件')
                    ws.write(0, 3, '描述')
                ws.write(irow + 1, 0, type)
                ws.write(irow + 1, 1, time)
                ws.write(irow + 1, 2, event)
                ws.write(irow + 1, 3, desc)
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

    logname = '操作日志'
    if log_type == 'update':
        logname = '更新日志'

    z = zipfile.ZipFile(filepath, 'w')
    i = 0
    for xlpath in xlpatharr:
        i += 1
        z.write(xlpath, logname + '-' + str(i) + '.xls')
    z.close()

    for xlpath in xlpatharr:
        os.remove(xlpath)

    mylog = {'操作': '导出日志', '操作结果': "导出成功"}
    SaveOperationLog(
        request.user, OperationLog.TYPE_OP_LOG, json.dumps(mylog, ensure_ascii=False), get_operator(request))
    return HttpResponse('{"r": "0","e": "操作成功","url":"/static/exportlog/%s","filename":"%s"}' % (filename, filename))


def delAllLogByUser(request):
    # 删除所有日志
    user_ids = request.POST.get('ids', '')
    user_ids = list(map(lambda x: int(x), user_ids.split(',')))
    try:
        OperationLog.objects.filter(user_id__in=user_ids).delete()
    except Exception as e:
        return HttpResponse('{"r": "1","e": "' + str(e) + '"}')

    return HttpResponse('{"r": "0","e": "操作成功"}')


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
        oper_logs = OperationLog.objects.filter(user=request.user, datetime__gte=sDatetime, datetime__lte=eDatetime)
        if params.get('type', None) == 'update':
            oper_logs = oper_logs.filter(event__in=(20, 21, 22, 23))
        oper_logs.delete()
    except Exception as e:
        return HttpResponse('{"r": "1","e": "' + str(e) + '"}')

    mylog = {'操作': '按时间段删除日志', '操作结果': "删除成功"}
    SaveOperationLog(
        request.user, OperationLog.TYPE_OP_LOG, json.dumps(mylog, ensure_ascii=False), get_operator(request))
    return HttpResponse('{"r": "0","e": "操作成功"}')


def getemaillog(request):
    event = int(request.GET.get('event', '0'))
    desc = request.GET.get('desc', '')
    stime = request.GET.get('stime', '')
    etime = request.GET.get('etime', '')
    sidx = request.GET.get('sidx', None)
    sord = request.GET.get('sord', 'asc')
    if sidx not in ('is_successful', 'datetime', 'type', 'content',):
        sidx = None
    if sidx and sord == 'desc':
        sidx = '-{}'.format(sidx)

    logs = Email.objects.filter(content__contains=request.user.username)

    time1 = datetime.datetime.strptime('2016-07-20 00:00:00', '%Y-%m-%d %H:%M:%S')
    if stime:
        time1 = datetime.datetime.strptime(stime, '%Y-%m-%d %H:%M:%S')

    time2 = datetime.datetime.now()
    if etime:
        etime += '.999'
        time2 = datetime.datetime.strptime(etime, '%Y-%m-%d %H:%M:%S.%f')

    if stime or etime:
        logs = logs.filter(datetime__range=(time1, time2))

    if desc:
        logs = logs.filter(content__contains=desc)

    if event == 1:
        logs = logs.filter(is_successful=True)
    if event == 2:
        logs = logs.filter(is_successful=False)

    if sidx is not None:
        logs = logs.order_by(sidx)
    else:
        logs = logs.order_by('-datetime')

    return logs


def getemailLogbyObj(itemObject):
    if itemObject.is_successful:
        type = '成功'
    else:
        type = '失败'
    time = str(itemObject.datetime)[0:19] if itemObject.datetime is not None else '--'
    title = ''
    desc = ''
    email_address = ''
    try:
        for (key, value) in json.loads(itemObject.content).items():
            if str(key) == 'sub':
                title = str(value)
            if str(key) == 'content':
                desc = str(value)
            if str(key) == 'email_address':
                email_address = str(value)
    except Exception as e:
        desc = str(e) + itemObject.content

    desc = '收件人：{}\r\n'.format(email_address) + desc

    return [type, time, title, desc]


def emaillist(request):
    page = int(request.GET.get('page', 1))
    rows = int(request.GET.get('rows', 30))

    paginator = Paginator(getemaillog(request), rows)
    totalPlan = paginator.count
    totalPage = paginator.num_pages

    page = totalPage if page > totalPage else page
    currentObjs = paginator.page(page).object_list
    rowList = list()
    for Obj in currentObjs:
        detailDict = {'id': Obj.id, 'cell': getemailLogbyObj(Obj)}
        rowList.append(detailDict)

    retInfo = {'r': 0, 'a': 'list', 'page': str(page), 'total': totalPage,
               'records': totalPlan, 'rows': rowList}
    jsonStr = json.dumps(retInfo, ensure_ascii=False)

    return HttpResponse(jsonStr)


def exportemaillog(request):
    exportpath = os.path.join(cur_file_dir(), 'static', 'exportlog')
    delexpirelog(exportpath)
    try:
        startpage = int(request.GET.get('startpage', 1) if request.GET.get('startpage', 1) else 1)
        endpage = int(request.GET.get('endpage', 1) if request.GET.get('endpage', 1) else 1)
        rows = int(request.GET.get('rows', 30) if request.GET.get('rows', 30) else 30)
        iMaxRow = int(request.GET.get('maxrow', '5000') if request.GET.get('maxrow', '5000') else 5000)
        timestr = datetime.datetime.now().strftime(xdatetime.FORMAT_WITH_SECOND)
    except:
        return HttpResponse('{"r": "1","e": "操作失败,请输入有效的参数"}')
    if startpage <= 0:
        startpage = 1
    if endpage <= 0:
        endpage = 1

    try:
        os.makedirs(exportpath)
    except OSError as e:
        pass
    filename = xdata.PREFIX_LOG_EMAIL_FILE + timestr + '.zip';
    filepath = os.path.join(exportpath, filename)

    paginator = Paginator(getemaillog(request), rows)
    totalPage = paginator.num_pages
    irow = 0
    xlpatharr = list()
    for page in range(startpage, endpage + 1, 1):
        if page > totalPage:
            break;
        currentObjs = paginator.page(page).object_list
        for Obj in currentObjs:
            element = getemailLogbyObj(Obj)
            type = element[0]
            time = element[1]
            event = element[2]
            desc = element[3]
            if irow == 0:
                wb = Workbook()
                ws = wb.add_sheet('Sheet1')
                ws.write(0, 0, '结果')
                ws.write(0, 1, '时间')
                ws.write(0, 2, '标题')
                ws.write(0, 3, '内容')
            ws.write(irow + 1, 0, type)
            ws.write(irow + 1, 1, time)
            ws.write(irow + 1, 2, event)
            ws.write(irow + 1, 3, desc)
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

    logname = '邮件发送日志'

    z = zipfile.ZipFile(filepath, 'w')
    i = 0
    for xlpath in xlpatharr:
        i += 1
        z.write(xlpath, logname + '-' + str(i) + '.xls')
    z.close()

    for xlpath in xlpatharr:
        os.remove(xlpath)

    mylog = {'操作': '导出邮件发送日志', '操作结果': "导出成功"}
    SaveOperationLog(
        request.user, OperationLog.TYPE_OP_LOG, json.dumps(mylog, ensure_ascii=False), get_operator(request))
    return HttpResponse('{"r": "0","e": "操作成功","url":"/static/exportlog/%s","filename":"%s"}' % (filename, filename))


def logsystem_handler(request):
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
    if a == 'delalllogbyuser':
        return delAllLogByUser(request)
    if a == 'delbytime':  # 删除，操作日志/更新日志
        return delLogByTime(request)
    if a == 'emaillist':
        return emaillist(request)
    if a == 'exportemaillog':
        return exportemaillog(request)
    return HttpResponse(json.dumps({"r": "1", "e": "没有对应的action:{}".format(html.escape(a))}))
