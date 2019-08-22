import json

from django.http import HttpResponse
from rest_framework import status
from django.core.paginator import Paginator

from box_dashboard import functions, xlogging
from .backup import get_normal_backup_interval_secs, get_host_name
from xdashboard.request_util import get_operator
from xdashboard.handle.logserver import SaveOperationLog
from xdashboard.models import OperationLog
from xdashboard.handle.backupmgr import get_host_ip_str
from xdashboard.handle import serversmgr
from apiv1.models import BackupTaskSchedule, Host, FileSyncSchedule
from apiv1.file_sync_views import FileSyncScheduleExecute, FileSyncScheduleViews
from xdashboard.handle.authorize import authorize_init


_logger = xlogging.getLogger(__name__)
router = functions.Router(globals())


# 创建计划, 更改计划
def createModifyPlan(request):
    params_dict = request.POST
    source_host_ident = params_dict['source_host_ident']
    target_host_ident = params_dict['target_host_ident']
    sync_rules_str = params_dict['sync_rules'].strip()
    if not sync_rules_str:
        sync_rules_str = '{}'
    try:
        sync_rules = json.loads(sync_rules_str)
    except Exception as e:
        return HttpResponse(json.dumps({"r": "1", "e": '操作失败，导出规则不是JSON格式', "list": []}))

    schedule_name = params_dict['taskname']
    # 立即导出吗
    immediateBackup = int(params_dict.get('immediately', default=-1))  # 0/1
    # 该计划的时间表类型
    scheduleType = int(params_dict.get('bakschedule', default=-1))  # 1/2/3/4/5
    # 计划开始日期
    planStartDate = params_dict.get('starttime', default=None)
    # 按间隔时间: 分,时,天
    backupDayInterval = get_normal_backup_interval_secs(params_dict)
    # 每周模式
    daysInWeek = params_dict.getlist('perweek', default=[])  # [1, 3, 5]
    daysInWeek = list(map(int, daysInWeek))
    # 每月模式
    daysInMonth = params_dict.getlist('monthly', default=[])  # [1, 18, 27, 31]
    daysInMonth = list(map(int, daysInMonth))
    extConfig = {
        'backupDayInterval': backupDayInterval,  # 按间隔时间: 秒数
        'daysInWeek': daysInWeek,
        'daysInMonth': daysInMonth,
        'sync_rules': sync_rules,
        'IntervalUnit': params_dict['intervalUnit']
    }
    extConfig = json.dumps(extConfig, ensure_ascii=False)
    code = 0
    # 更改计划(禁止修改cdp - syn / cdp - asyn等)
    if 'taskid' in params_dict:
        planId = int(params_dict['taskid'])
        params = {
            'name': schedule_name,
            'cycle_type': scheduleType,
            'plan_start_date': planStartDate,
            'ext_config': extConfig,
        }
        respn = FileSyncScheduleViews().put(request, planId, params)
        if respn.status_code == status.HTTP_202_ACCEPTED:
            infoCM = '修改计划成功'
            desc = {'操作': '修改导出计划', '任务ID': str(planId), '任务名称': schedule_name}
        else:
            code = 1
            infoCM = '修改计划失败,{}'.format(respn.data)
    # 创建计划
    else:
        result = authorize_init.check_backup_archive_license()
        if result['r'] != 0:
            return HttpResponse(json.dumps({"r": "1", "e": result['e'], "list": []}))
        params = {"source_host_ident": source_host_ident,
                  "target_host_ident": target_host_ident,
                  "name": schedule_name,
                  "cycle_type": scheduleType,
                  "plan_start_date": planStartDate,
                  "ext_config": extConfig}
        respn = FileSyncScheduleViews().post(request, params)
        if respn.status_code == status.HTTP_201_CREATED:
            infoCM = '创建计划成功'
            desc = {'操作': '创建导出计划', '任务ID': str(respn.data['id']), '任务名称': schedule_name}
        else:
            code = 1
            infoCM = '创建计划失败,{}'.format(respn.data)
    # 是否立即执行该计划一次
    infoEcx = ''
    plan_id = respn.data['id'] if code == 0 else -1
    if immediateBackup > 0 and code == 0:
        respn = FileSyncScheduleExecute().post(request=request, api_request={'schedule': plan_id})
        if status.is_success(respn.status_code):
            infoEcx = '立即执行计划成功'
        else:
            code = 1
            infoEcx = '立即执行计划失败,{}'.format(respn.data)

    if (infoCM == '修改计划成功') or (infoCM == '创建计划成功'):
        desc.update({'立即执行': infoEcx if infoEcx else '否'})
        SaveOperationLog(
            request.user, OperationLog.BACKUP_EXPORT, json.dumps(desc, ensure_ascii=False), get_operator(request))

    # 返回给界面信息
    infoStr = '{} {}'.format(infoCM, infoEcx)
    return HttpResponse(json.dumps({"r": code, "e": "{}".format(infoStr), "plan_id": plan_id}, ensure_ascii=False))


# 获取User的计划列表
def getPlanList(request):
    paramsQD = request.GET
    perPage = paramsQD.get('rows', '10')  # 设置每页条数
    targPage = paramsQD.get('page', '1')  # 返回第几页
    search_key = request.GET.get('s_key', None)

    allPlans = FileSyncScheduleViews().get(request=request).data
    rowList = list()
    for planAttr in allPlans:
        next_run_date = None
        last_run_date = None
        if planAttr['last_run_date']:
            last_run_date = planAttr['last_run_date'].replace('T', ' ').split('.')[0]
        if planAttr['next_run_date']:
            next_run_date = planAttr['next_run_date'].replace('T', ' ').split('.')[0]
        one_info = {'id': planAttr['id'], 'cell': [
            planAttr['id'], planAttr['name'], planAttr['host']['name'], '启用' if planAttr['enabled'] else '禁用',
            last_run_date, next_run_date
        ]}
        backup_host_ip_str = get_host_ip_str(planAttr['host']['id']) if search_key else ''
        is_need = serversmgr.filter_hosts(search_key, one_info['cell'][1], one_info['cell'][2], backup_host_ip_str)
        if is_need:
            rowList.append(one_info)
        else:
            pass
        rowList.sort(key=lambda x: x['id'])

    paginator = Paginator(object_list=rowList, per_page=perPage)
    plansNum = paginator.count
    pagesNum = paginator.num_pages
    getPlans = paginator.page(targPage).object_list

    retInfo = {'r': 0, 'a': 'list', 'page': targPage, 'total': pagesNum, 'records': plansNum, 'rows': getPlans}
    functions.sort_gird_rows(request, retInfo)
    jsonStr = json.dumps(retInfo, ensure_ascii=False)

    return HttpResponse(jsonStr)


# 删除计划
def delPlans(request):
    paramsQD = request.GET
    planids = paramsQD.get('taskid', '')  # '4,5,6'

    host_ident = paramsQD.get('host_ident', None)  # 存在就删除主机所有计划
    if host_ident:
        user_plans = FileSyncSchedule.objects.filter(enabled=True, deleted=False, host__ident=host_ident)
        planids = ','.join([str(plan.id) for plan in user_plans])
        if not planids:
            return HttpResponse('{"r": "0","e": "操作成功"}')

    if not planids:
        return HttpResponse('{"r": "1", "e": "请求参数缺失：taskid"}')
    for planid in planids.split(','):
        # delete_shell_zip(planid)
        FileSyncScheduleViews().delete(request, planid)
    desc = {'操作': '删除导出计划', '计划ID': planids.split(',')}
    SaveOperationLog(
        request.user, OperationLog.BACKUP_EXPORT, json.dumps(desc, ensure_ascii=False), get_operator(request))
    return HttpResponse('{"r": "0","e": "操作成功"}')


def disablePlan(request):
    paramsQD = request.GET
    planids = paramsQD.get('taskid', '')  # '4,5,6'
    # enabled_yun = paramsQD.get('enabled_yun', '')
    user = request.user
    if not planids:
        return HttpResponse('{"r": "1", "e": "请求参数缺失：taskid"}')
    _failed_enable = list()
    for planid in planids.split(','):
        planStat = FileSyncScheduleViews().get(request, planid).data
        is_enable = planStat['enabled']
        host_name = planStat['host']['name']
        plan_name = planStat['name']
        if is_enable:  # 该计划当前"启用状态"
            api_request = {'enabled': False}
            desc = {'操作': '启用/禁用任务', '任务ID': planid, '状态': '禁用成功', '主机名称': host_name, '计划名称': plan_name}
        else:  # 该计划当前"禁用状态"
            api_request = {'enabled': True}
            desc = {'操作': '启用/禁用任务', '任务ID': planid, '状态': '启用成功', '主机名称': host_name, '计划名称': plan_name}
        # 从yun过来的操作
        # if enabled_yun != '':
        #     api_request = {'enabled': enabled_yun}
        #     desc = {'操作': '启用/禁用任务', '任务ID': planid, '状态': '启用成功' if enabled_yun else '禁用成功', '主机名称': host_name,
        #             '计划名称': plan_name}
        resp = FileSyncScheduleViews().put(request, planid, api_request)
        if resp.status_code == status.HTTP_202_ACCEPTED:
            SaveOperationLog(
                request.user, OperationLog.BACKUP_EXPORT, json.dumps(desc, ensure_ascii=False), get_operator(request))

    return HttpResponse('{"r": "0","e": "操作成功"}')


# 获取指定计划的详细信息
def getPlanDetail(request):
    paramsQD = request.GET
    planid = int(paramsQD.get('taskid', -1))
    if planid < 0:
        return HttpResponse('{"r": "1", "e": "请求参数缺失：taskid"}')
    planDetl = FileSyncScheduleViews().get(request, planid).data
    planExt = json.loads(planDetl['ext_config'])
    cycle = ''  # '2' or '1, 6, 7' or '1, 25, 28, 30, 31'
    cycleVal = planDetl['cycle_type']
    if cycleVal == BackupTaskSchedule.CYCLE_PERDAY:
        cycle = planExt['backupDayInterval']
    if cycleVal == BackupTaskSchedule.CYCLE_PERWEEK:
        cycle = ','.join(list(map(str, planExt['daysInWeek'])))
    if cycleVal == BackupTaskSchedule.CYCLE_PERMONTH:
        cycle = ','.join(list(map(str, planExt['daysInMonth'])))

    plan_start_date = None
    if planDetl['plan_start_date']:
        plan_start_date = planDetl['plan_start_date'].replace('T', ' ').split('.')[0]

    created_time = None
    if planDetl['created']:
        created_time = planDetl['created'].replace('T', ' ').split('.')[0]

    if created_time and not plan_start_date:
        plan_start_date = created_time
    target_host = Host.objects.get(ident=planDetl["target_host_ident"])
    sync_rules = planExt.get('sync_rules', '')
    if not sync_rules:
        sync_rules = ''

    retInfo = {
        "r": "0", "e": "操作成功",
        "taskname": planDetl['name'],
        "createtime": created_time,
        "src": [{"id": planDetl['host']['ident'], "name": planDetl['host']['name'], "type": planDetl['host']['type']}],
        "dest": [{"id": target_host.ident, "name": get_host_name(target_host), "type": target_host.type}],
        "schedule": {"type": planDetl['cycle_type_display'], 'starttime': plan_start_date,
                     'period': cycle, 'unit': planExt.get('IntervalUnit', 'day')},
        'sync_rules': sync_rules
    }
    jsonStr = json.dumps(retInfo, ensure_ascii=False)
    return HttpResponse(jsonStr)


def start_file_sync(request):
    paramsQD = request.GET
    planids = paramsQD.get('taskid', '')  # '4,5,6'
    if not planids:
        return HttpResponse('{"r": "1", "e": "请求参数缺失：taskid"}')
    err = list()
    for planid in planids.split(','):
        planStat = FileSyncScheduleViews().get(request, planid).data
        is_enable = planStat['enabled']
        if is_enable:
            rsp = FileSyncScheduleExecute().post(request=request, api_request={'schedule': planStat['id']})
            if rsp.status_code == status.HTTP_201_CREATED:
                desc = {'操作': '执行导出计划', '计划名称': planStat['name'], "操作结果": "已发送命令"}
                SaveOperationLog(
                    request.user, OperationLog.BACKUP_EXPORT, json.dumps(desc, ensure_ascii=False),
                    get_operator(request))
            else:
                err.append({'name': planStat['name'], 'e': rsp.data})
                mylog = {'操作': '执行导出计划', '计划名称': planStat['name'], "操作结果": "执行失败{}".format(rsp.data)}
                SaveOperationLog(
                    request.user, OperationLog.BACKUP_EXPORT, json.dumps(mylog, ensure_ascii=False),
                    get_operator(request))
        else:
            err.append({'name': planStat['name'], 'e': '已禁用'})
            mylog = {'操作': '执行导出计划务', '计划名称': planStat['name'], "操作结果": "计划已禁用，执行失败"}
            SaveOperationLog(
                request.user, OperationLog.BACKUP_EXPORT, json.dumps(mylog, ensure_ascii=False), get_operator(request))
        if len(err) > 0:
            return HttpResponse(
                json.dumps({"r": 1, "e": "共有{}个计划执行失败".format(len(err)), "err": err}, ensure_ascii=False))

        return HttpResponse('{"r": "0","e": "操作成功"}')
