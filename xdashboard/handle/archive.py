import json

from django.http import HttpResponse
from rest_framework import status
from box_dashboard import xdatetime
from apiv1.views import HostSessionInfo, get_response_error_string
from apiv1.archive_views import (ArchiveScheduleExecute, ArchiveScheduleViews,
                                 ImportTaskExecute)
from box_dashboard import functions
from .backup import get_normal_backup_interval_secs, get_host_name
from xdashboard.request_util import get_operator
from xdashboard.handle.logserver import SaveOperationLog
from xdashboard.models import OperationLog
from box_dashboard import xlogging
from django.core.paginator import Paginator
from xdashboard.handle.backupmgr import get_host_ip_str
from xdashboard.handle import serversmgr
from xdashboard.handle.backupmgr import _is_plan_in_user_quota
from apiv1.models import ArchiveSchedule, StorageNode, ArchiveTask, HostSnapshot, BackupTaskSchedule, VolumePool, \
    UserVolumePoolQuota
from xdashboard.handle import backup
from xdashboard.handle.authorize import authorize_init

_logger = xlogging.getLogger(__name__)
router = functions.Router(globals())


# 创建计划, 更改计划
def createModifyPlan(request):
    paramsQDict = request.POST
    # host数据库id
    hostIdent = paramsQDict.get('serverid', default='')
    # 所选存储结点的ident
    storage_node_ident = paramsQDict.get('storagedevice', default=-1)
    api_response = HostSessionInfo().get(request=request, ident=hostIdent)
    if not status.is_success(api_response.status_code):
        return HttpResponse(json.dumps({"r": 1, "e": "{}".format(get_response_error_string(api_response))}))
    hostId = api_response.data['host']['id'] if hostIdent else -1
    # 计划名称
    planName = paramsQDict.get('taskname', default='')
    # 导出数据保留期限 天, 月
    backupDataHoldDays = int(paramsQDict.get('retentionperiod', default=-1))
    # 空间剩余XGB时，自动清理
    autoCleanDataWhenlt = int(paramsQDict.get('cleandata', default=-1))
    # 带宽限速 MB
    usemaxBroadband = float(paramsQDict.get('usemaxbandwidth', default=1))
    maxBroadband = float(paramsQDict.get('maxbandwidth', default=-1))
    if usemaxBroadband == 0:
        maxBroadband = -1
    # 立即导出吗
    immediateBackup = int(paramsQDict.get('immediately', default=-1))  # 0/1
    # 该计划的时间表类型
    scheduleType = int(paramsQDict.get('bakschedule', default=-1))  # 1/2/3/4/5
    # 计划开始日期
    planStartDate = paramsQDict.get('starttime', default=None)
    # 按间隔时间: 分,时,天
    backupDayInterval = get_normal_backup_interval_secs(paramsQDict)
    # 每周模式
    daysInWeek = paramsQDict.getlist('perweek', default=[])  # [1, 3, 5]
    daysInWeek = list(map(int, daysInWeek))
    # 每月模式
    daysInMonth = paramsQDict.getlist('monthly', default=[])  # [1, 18, 27, 31]
    daysInMonth = list(map(int, daysInMonth))
    # 数据传输 是否加密 1 加密 0 不加密
    isencipher = int(paramsQDict.get('isencipher', default=0))  # 0/1
    backupmode = int(paramsQDict.get('backupmode', default=2))
    # 系统文件夹，去重
    SystemFolderDup = paramsQDict['SystemFolderDup'] == '1'  # '1'、'0'
    # 导出重试
    backup_retry_or = json.loads(paramsQDict.get('backup_retry', '{"enable":true,"count":"5","interval":"10"}'))
    # 开启/禁用
    enabled = paramsQDict.get('enabled', True)
    # 1.静默 0.不静默
    vmware_quiesce = int(paramsQDict.get('vmware_quiesce', '1'))
    backup_retry = {
        'enable': backup_retry_or['enable'],
        'count': int(backup_retry_or['count']),
        'interval': int(backup_retry_or['interval'])
    }
    # 数据保留期 单位：day, month, None
    data_keeps_deadline_unit = paramsQDict.get('retentionperiod_unit', None)
    if data_keeps_deadline_unit == 'day' or data_keeps_deadline_unit is None:
        backupDataHoldDays = backupDataHoldDays * 1
    if data_keeps_deadline_unit == 'month':
        backupDataHoldDays = backupDataHoldDays * 30
    # 线程数
    # thread_count = int(paramsQDict.get('thread_count', '4'))
    # 导出时候IO占用
    BackupIOPercentage = int(paramsQDict.get('BackupIOPercentage', '30'))
    # 导出完整备份间隔
    full_interval = int(paramsQDict.get('full_interval', '-1'))  # 0 每次完整导出， -1 第一此完整备份

    # BackupTaskSchedule的ext_config字段
    extConfig = {'backupDataHoldDays': backupDataHoldDays,  # 导出数据保留期, 天
                 'autoCleanDataWhenlt': autoCleanDataWhenlt,
                 'maxBroadband': maxBroadband,
                 'backupDayInterval': backupDayInterval,  # 按间隔时间: 秒数
                 'daysInWeek': daysInWeek,
                 'daysInMonth': daysInMonth,
                 'isencipher': isencipher,
                 'incMode': backupmode,
                 'removeDuplicatesInSystemFolder': SystemFolderDup,
                 'IntervalUnit': paramsQDict['intervalUnit'],  # 按间隔时间, 单位: 'min', 'hour', 'day'
                 'backup_retry': backup_retry,
                 'data_keeps_deadline_unit': data_keeps_deadline_unit,
                 'vmware_quiesce': vmware_quiesce,
                 'BackupIOPercentage': BackupIOPercentage,
                 'full_interval': full_interval
                 }
    extConfig = json.dumps(extConfig, ensure_ascii=False)
    code = 0
    # 更改计划(禁止修改cdp - syn / cdp - asyn等)
    if 'taskid' in paramsQDict:
        planId = int(paramsQDict['taskid'])
        params = {
            'name': planName,
            'cycle_type': scheduleType,
            'plan_start_date': planStartDate,
            'ext_config': extConfig,
            'storage_node_ident': storage_node_ident
        }
        respn = ArchiveScheduleViews().put(request, planId, params)
        if respn.status_code == status.HTTP_202_ACCEPTED:
            infoCM = '修改计划成功'
            desc = {'操作': '修改导出计划', '任务ID': str(planId), '任务名称': planName}
        else:
            code = 1
            infoCM = '修改计划失败,{}'.format(respn.data)
    # 创建计划
    else:
        result = authorize_init.check_backup_archive_license()
        if result['r'] != 0:
            return HttpResponse(json.dumps({"r": "1", "e": result['e'], "list": []}))
        params = {"host": hostId, "name": planName, "cycle_type": scheduleType, "plan_start_date": planStartDate,
                  "ext_config": extConfig, 'storage_node_ident': storage_node_ident, 'enabled': enabled}
        respn = ArchiveScheduleViews().post(request, params)
        if respn.status_code == status.HTTP_201_CREATED:
            infoCM = '创建计划成功'
            desc = {'操作': '创建导出计划', '任务ID': str(hostId), '任务名称': planName}
        else:
            code = 1
            infoCM = '创建计划失败,{}'.format(respn.data)
    # 是否立即执行该计划一次
    infoEcx = ''
    plan_id = respn.data['id'] if code == 0 else -1
    if immediateBackup > 0 and code == 0:
        respn = ArchiveScheduleExecute().post(request=request, api_request={'schedule': plan_id})
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

    allPlans = ArchiveScheduleViews().get(request=request).data
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
            '整机导出', last_run_date, next_run_date
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
        user_plans = ArchiveSchedule.objects.filter(enabled=True, deleted=False, host__ident=host_ident)
        planids = ','.join([str(plan.id) for plan in user_plans])
        if not planids:
            return HttpResponse('{"r": "0","e": "操作成功"}')

    if not planids:
        return HttpResponse('{"r": "1", "e": "请求参数缺失：taskid"}')
    for planid in planids.split(','):
        # delete_shell_zip(planid)
        ArchiveScheduleViews().delete(request, planid)
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
        planStat = ArchiveScheduleViews().get(request, planid).data
        node_ident = planStat['storage_node_ident']
        is_enable = planStat['enabled']
        host_name = planStat['host']['name']
        plan_name = planStat['name']
        if is_enable:  # 该计划当前"启用状态"
            api_request = {'enabled': False}
            desc = {'操作': '启用/禁用任务', '任务ID': planid, '状态': '禁用成功', '主机名称': host_name, '计划名称': plan_name}
        else:  # 该计划当前"禁用状态"
            api_request = {'enabled': True}
            desc = {'操作': '启用/禁用任务', '任务ID': planid, '状态': '启用成功', '主机名称': host_name, '计划名称': plan_name}
            if not _is_plan_in_user_quota(user.id, node_ident):
                desc = {'操作': '启用/禁用任务', '任务ID': planid, '操作结果': '没有可用配额,启用失败', '主机名称': host_name, '计划名称': plan_name}
                SaveOperationLog(
                    request.user, OperationLog.BACKUP_EXPORT, json.dumps(desc, ensure_ascii=False),
                    get_operator(request))
                _failed_enable.append(planStat['name'])
                continue
        # 从yun过来的操作
        # if enabled_yun != '':
        #     api_request = {'enabled': enabled_yun}
        #     desc = {'操作': '启用/禁用任务', '任务ID': planid, '状态': '启用成功' if enabled_yun else '禁用成功', '主机名称': host_name,
        #             '计划名称': plan_name}
        resp = ArchiveScheduleViews().put(request, planid, api_request)
        if resp.status_code == status.HTTP_202_ACCEPTED:
            SaveOperationLog(
                request.user, OperationLog.BACKUP_EXPORT, json.dumps(desc, ensure_ascii=False), get_operator(request))

    if _failed_enable:
        ret_info = {"r": "1", "e": "{}:没有可用配额,启用失败".format(','.join(_failed_enable))}
        return HttpResponse(json.dumps(ret_info))

    return HttpResponse('{"r": "0","e": "操作成功"}')


# 获取指定计划的详细信息
def getPlanDetail(request):
    paramsQD = request.GET
    planid = int(paramsQD.get('taskid', -1))
    if planid < 0:
        return HttpResponse('{"r": "1", "e": "请求参数缺失：taskid"}')
    planDetl = ArchiveScheduleViews().get(request, planid).data
    planExt = json.loads(planDetl['ext_config'])
    cycle = ''  # '2' or '1, 6, 7' or '1, 25, 28, 30, 31'
    cycleVal = planDetl['cycle_type']
    if cycleVal == BackupTaskSchedule.CYCLE_PERDAY:
        cycle = planExt['backupDayInterval']
    if cycleVal == BackupTaskSchedule.CYCLE_PERWEEK:
        cycle = ','.join(list(map(str, planExt['daysInWeek'])))
    if cycleVal == BackupTaskSchedule.CYCLE_PERMONTH:
        cycle = ','.join(list(map(str, planExt['daysInMonth'])))

    storagedevice, storagedeviceid = '未分配', -1
    bs = ArchiveSchedule.objects.filter(id=planDetl["id"])
    if bs:
        storagedeviceid = bs[0].storage_node_ident
        sd = StorageNode.objects.filter(ident=bs[0].storage_node_ident)
        if sd:
            storagedevice = sd[0].name

        if not _is_plan_in_user_quota(user_id=request.user.id, node_ident=storagedeviceid):
            storagedevice, storagedeviceid = '未分配', -1

    plan_start_date = None
    if planDetl['plan_start_date']:
        plan_start_date = planDetl['plan_start_date'].replace('T', ' ').split('.')[0]

    created_time = None
    if planDetl['created']:
        created_time = planDetl['created'].replace('T', ' ').split('.')[0]

    if created_time and not plan_start_date:
        plan_start_date = created_time

    host = ArchiveSchedule.objects.get(id=planid).host

    retInfo = {
        "r": "0", "e": "操作成功",
        "taskname": planDetl['name'],
        "storagedevice": {'name': storagedevice, 'value': storagedeviceid},
        "createtime": created_time,
        "src": [{"id": planDetl['host']['ident'], "name": planDetl['host']['name'], "type": planDetl['host']['type']}],
        "schedule": {"type": planDetl['cycle_type_display'], 'starttime': plan_start_date,
                     'period': cycle, 'unit': planExt.get('IntervalUnit', 'day')},
        "retentionperiod": planExt['backupDataHoldDays'],
        "cleandata": planExt['autoCleanDataWhenlt'],
        "maxbandwidth": planExt['maxBroadband'],
        "isencipher": planExt['isencipher'],
        "backupmode": planExt.get('incMode', 2),
        'backupobj': planExt.get('specialMode', 0),
        "removeDuplicatesInSystemFolder": planExt['removeDuplicatesInSystemFolder'],  # True、False
        "backup_retry": planExt.get('backup_retry', {"count": 5, "enable": True, "interval": 10}),
        'data_keeps_deadline_unit': planExt.get('data_keeps_deadline_unit', None),
        'thread_count': planExt.get('diskreadthreadcount', 1),
        'vmware_tranport_modes': planExt.get('vmware_tranport_modes', 1),
        'vmware_quiesce': planExt.get('vmware_quiesce', True),
        'BackupIOPercentage': planExt.get('BackupIOPercentage', 30),
        'full_interval': planExt.get('full_interval', -1),
    }
    jsonStr = json.dumps(retInfo, ensure_ascii=False)
    return HttpResponse(jsonStr)


def startexporttask(request):
    paramsQD = request.GET
    planids = paramsQD.get('taskid', '')  # '4,5,6'
    exportmode = paramsQD.get('exportmode', '2')  # False,默认为增量导出
    if int(exportmode) == 2:
        force_full = False
    else:
        force_full = True
    if not planids:
        return HttpResponse('{"r": "1", "e": "请求参数缺失：taskid"}')
    err = list()
    for planid in planids.split(','):
        planStat = ArchiveScheduleViews().get(request, planid).data
        is_enable = planStat['enabled']
        if is_enable:
            host_id = ArchiveSchedule.objects.get(id=int(planid)).host_id
            host_hostsnapshot = HostSnapshot.objects.filter(host_id=host_id).order_by('-id').first().id
            api_request = {'schedule': planid, 'host_snapshot': host_hostsnapshot, 'snapshot_datetime': '',
                           'force_full': force_full}
            rsp = ArchiveScheduleExecute().post(request=request, api_request=api_request)
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


def _gen_host_node(host):
    return {'id': host.ident, 'icon': 'pc', "inode": True, "open": True, 'label': get_host_name(host),
            'type': host.type, 'branch': []}


def _gen_task_node(task):
    return {'id': task.task_uuid, 'icon': 'file', 'label': task.name, "radio": True}


def list_archive_task(request):
    key = request.GET.get('key', 'root')

    all_tasks = ArchiveTask.objects.filter(host_snapshot__host__user=request.user,
                                           schedule__deleted=False,
                                           successful=True,
                                           finish_datetime__isnull=False).order_by('-start_datetime').all()

    host_nodes = dict()
    for archive_task in all_tasks:
        host = archive_task.host_snapshot.host
        if not host_nodes.get(host.ident):
            host_nodes[host.ident] = _gen_host_node(host)
        host_node = host_nodes[host.ident]
        host_node['branch'].append(_gen_task_node(archive_task))

    info_list = list(host_nodes.values())
    if not info_list:
        json_str = '[{"id": "ui_hasHosts","branch":[],"inode":false, "label": "无任务","icon":"pcroot","radio":false}]'
    else:
        json_str = json.dumps(info_list)
    return HttpResponse(json_str)


def start_import(request):
    src_type = request.GET['src_type']
    task_uuid = request.GET.get('task_uuid', None)
    storage_ident = request.GET.get('storagedevice', None)
    storagenode = StorageNode.objects.filter(ident=storage_ident).first()
    rsp = ImportTaskExecute().post(request, {'src_type': int(src_type), 'local_task_uuid': task_uuid,
                                             'user_id': request.user.id, 'storage_path': storagenode.path})
    if status.is_success(rsp.status_code):
        return HttpResponse(json.dumps({"r": "0", "e": ""}))
    else:
        return HttpResponse(json.dumps({"r": "1", "e": rsp.data if rsp.data else '执行失败，参数错误'}))


# 创建计划时，获取存储单元
def getstoragedevice(request):
    user_id = request.user.id
    uservolumepoolquota = UserVolumePoolQuota.objects.filter(user_id=user_id).all()

    if uservolumepoolquota is False:
        return HttpResponse(json.dumps([{'name': '无可用存储单元', 'value': -1, 'id': -1}]), status.HTTP_200_OK)

    ret_info = []
    for quota in uservolumepoolquota:
        node = quota.volume_pool_node
        ret_info.append({'name': node.name, 'value': node.pool_uuid, 'free': -1})

    # 一下代码仅供测试，正式代码请注释 fixme
    # ret_info.append({'name': '测试磁带库01', 'value': 'test_media_uuid', 'free': 100 * 1024 * 1024 * 1024})
    available_quotas = backup.get_user_available_quotas(request)
    for quota in available_quotas:
        node = StorageNode.objects.get(id=quota['node_id'])
        ret_info.append({'name': node.name, 'value': node.ident, 'free': quota['free_mb']})

    return HttpResponse(content=json.dumps(ret_info), status=status.HTTP_200_OK)


def getImportBackupDataList(request):
    paramsQD = request.GET
    perPage = paramsQD.get('rows', '10')  # 设置每页条数
    targPage = paramsQD.get('page', '1')  # 返回第几页
    search_key = request.GET.get('s_key', None)

    allPlans = ImportTaskExecute().get(request=request).data
    rowList = list()
    for planAttr in allPlans:
        host_snapshot_id = planAttr['host_snapshot']
        host_snapshot = HostSnapshot.objects.get(id=host_snapshot_id)
        host_display_name = host_snapshot.host.display_name
        point_name = '整机备份{}'.format(host_snapshot.start_datetime.strftime(xdatetime.FORMAT_WITH_USER_SECOND))
        status = planAttr['status']
        one_info = {'id': planAttr['id'], 'cell': [
            planAttr['id'], host_display_name, point_name, status
        ]}
        if not host_snapshot.deleted:
            rowList.append(one_info)
        rowList.sort(key=lambda x: x['id'])

    paginator = Paginator(object_list=rowList, per_page=perPage)
    plansNum = paginator.count
    pagesNum = paginator.num_pages
    getPlans = paginator.page(targPage).object_list

    retInfo = {'r': 0, 'a': 'list', 'page': targPage, 'total': pagesNum, 'records': plansNum, 'rows': getPlans}
    functions.sort_gird_rows(request, retInfo)
    jsonStr = json.dumps(retInfo, ensure_ascii=False)

    return HttpResponse(jsonStr)


def delImportPlans(request):
    paramsQD = request.GET
    planids = paramsQD.get('taskid', '')  # '4,5,6'
    err_list = list()
    for planid in planids.split(','):
        rsp = json.loads(ImportTaskExecute().delete(request, planid).data)
        if rsp['r'] != 0:
            err_list.append(planid)
    desc = {'操作': '删除导入任务', '任务ID': planids.split(',')}
    SaveOperationLog(
        request.user, OperationLog.BACKUP_EXPORT, json.dumps(desc, ensure_ascii=False), get_operator(request))
    if len(err_list) > 0:
        return HttpResponse('{"r": "1","e": "任务正在执行中，请先取消该任务。"}')
    return HttpResponse('{"r": "0","e": "操作成功"}')
