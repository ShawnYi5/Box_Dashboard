# coding=utf-8
import html
import json
import os
import re
import shutil
import subprocess

from django.core.paginator import Paginator
from django.http import HttpResponse
from rest_framework import status

from apiv1.models import BackupTaskSchedule, StorageNode, UserQuota, Host, HostSnapshot, GroupBackupTaskSchedule
from apiv1.views import BackupTaskScheduleExecute, BackupTaskScheduleSetting
from apiv1.work_processors import HostBackupWorkProcessors
from box_dashboard import functions
from box_dashboard import xlogging, xdatetime
from xdashboard.common import file_utils
from xdashboard.common.license import check_license, is_functional_available
from xdashboard.handle import serversmgr
from xdashboard.handle.logserver import SaveOperationLog
from xdashboard.models import OperationLog
from xdashboard.request_util import get_operator

_logger = xlogging.getLogger(__name__)
DRIVE_FILE_DIR = '/home/disable_sys_in_kvm'
DRIVE_FILE = '/home/disable_sys_in_kvm/new_disable_sys_in_kvm.json'


def get_host_ip_str(host_or_id):
    try:
        if isinstance(host_or_id, Host):
            host_exe_info = json.loads(host_or_id.ext_info)
        else:
            host_exe_info = json.loads(Host.objects.get(id=host_or_id).ext_info)
        host_ip_mask = [ip_mask['Ip'] for one_eth in host_exe_info['system_infos']['Nic'] for ip_mask in
                        one_eth['IpAndMask']]
        return ','.join(host_ip_mask)
    except:
        return ''


def _check_remove_duplicates_in_system_folder_license():
    clret = check_license('remove_duplicates_in_system_folder')
    if clret.get('r', 0) != 0:
        return clret
    if is_functional_available('remove_duplicates_in_system_folder'):
        return {'r': 0, 'e': 'OK'}
    return {'r': 1, 'e': '去重功能未授权。'}


# 立即执行User的多个计划一次
def excPlanNow(request):
    paramsQD = request.GET
    planids = paramsQD.get('taskid', '')  # '4,5,6'
    backupmode = paramsQD.get('backupmode', '2')
    force_store_full = paramsQD.get('force_store_full', '0')
    api_request = {'type': backupmode, 'force_store_full': force_store_full}
    if not planids:
        return HttpResponse('{"r": "1", "e": "请求参数缺失：taskid"}')
    if backupmode == '1' and force_store_full == '0':
        # 完整备份启用智能增量存储
        clret = _check_remove_duplicates_in_system_folder_license()
        if clret.get('r', 0) != 0:
            return HttpResponse(json.dumps(clret, ensure_ascii=False))
    err = list()

    ids = list()

    for planid in planids.split(','):
        if planid.startswith('group_'):
            gs = GroupBackupTaskSchedule.objects.filter(id=planid[6:])
            for schedules in gs.first().schedules.all():
                ids.append(schedules.id)
        else:
            ids.append(planid)

    for planid in ids:
        planStat = BackupTaskScheduleSetting().get(request=request, backup_task_schedule_id=planid).data
        is_enable = planStat['enabled']
        if is_enable:
            rsp = BackupTaskScheduleExecute().post(request=request, backup_task_schedule_id=planid,
                                                   api_request=api_request)
            if rsp.status_code == status.HTTP_201_CREATED:
                desc = {'操作': '执行备份计划', '计划名称': planStat['name'], "操作结果": "已发送命令"}
                SaveOperationLog(
                    request.user, OperationLog.TYPE_BACKUP, json.dumps(desc, ensure_ascii=False), get_operator(request))
            else:
                err.append({'name': planStat['name'], 'e': rsp.data})
                mylog = {'操作': '执行备份计划', '计划名称': planStat['name'], "操作结果": "执行失败{}".format(rsp.data)}
                SaveOperationLog(
                    request.user, OperationLog.TYPE_BACKUP, json.dumps(mylog, ensure_ascii=False),
                    get_operator(request))
        else:
            err.append({'name': planStat['name'], 'e': '已禁用'})
            mylog = {'操作': '执行备份计划务', '计划名称': planStat['name'], "操作结果": "计划已禁用，执行失败"}
            SaveOperationLog(
                request.user, OperationLog.TYPE_BACKUP, json.dumps(mylog, ensure_ascii=False), get_operator(request))

    if len(err) > 0:
        return HttpResponse(json.dumps({"r": 1, "e": "共有{}个计划执行失败".format(len(err)), "err": err}, ensure_ascii=False))

    return HttpResponse('{"r": "0","e": "操作成功"}')


def _is_plan_in_user_quota(user_id, node_ident):
    user_quota = UserQuota.objects.filter(deleted=False, user_id=user_id, storage_node__ident=node_ident)
    return True if user_quota else False


# 启用/禁用User的多个计划
def disablePlan(request):
    paramsQD = request.GET
    planids = paramsQD.get('taskid', '')  # '4,5,6'
    enabled_yun = paramsQD.get('enabled_yun', '')
    user = request.user
    if not planids:
        return HttpResponse('{"r": "1", "e": "请求参数缺失：taskid"}')
    _failed_enable = list()

    ids = list()

    for planid in planids.split(','):
        if planid.startswith('group_'):
            gs = GroupBackupTaskSchedule.objects.filter(id=planid[6:])
            gs.update(enabled=not gs.first().enabled)
            for schedules in gs.first().schedules.all():
                ids.append(schedules.id)
        else:
            ids.append(planid)

    for planid in ids:
        planStat = BackupTaskScheduleSetting().get(request=request, backup_task_schedule_id=planid).data
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
                    request.user, OperationLog.TYPE_BACKUP, json.dumps(desc, ensure_ascii=False), get_operator(request))
                _failed_enable.append(planStat['name'])
                continue
        # 从yun过来的操作
        if enabled_yun != '':
            api_request = {'enabled': enabled_yun}
            desc = {'操作': '启用/禁用任务', '任务ID': planid, '状态': '启用成功' if enabled_yun else '禁用成功', '主机名称': host_name,
                    '计划名称': plan_name}
        resp = BackupTaskScheduleSetting().put(request=request, backup_task_schedule_id=planid, api_request=api_request)
        if resp.status_code == status.HTTP_202_ACCEPTED:
            SaveOperationLog(
                request.user, OperationLog.TYPE_BACKUP, json.dumps(desc, ensure_ascii=False), get_operator(request))

    if _failed_enable:
        ret_info = {"r": "1", "e": "{}:没有可用配额,启用失败".format(','.join(_failed_enable))}
        return HttpResponse(json.dumps(ret_info))

    return HttpResponse('{"r": "0","e": "操作成功"}')


def delete_shell_zip(planid):
    schedule = BackupTaskSchedule.objects.get(id=planid)
    shell_infos = HostBackupWorkProcessors.get_shell_infos_from_schedule_or_host(None, schedule)
    if shell_infos is None:
        return
    file_utils.delete_file_safely(shell_infos['zip_path'])


# 删除User的多个计划
def delPlans(request):
    paramsQD = request.GET
    planids = paramsQD.get('taskid', '')  # '4,5,6'

    host_ident = paramsQD.get('host_ident', None)  # 存在就删除主机所有计划
    if host_ident:
        user_plans = BackupTaskSchedule.objects.filter(enabled=True, deleted=False, host__ident=host_ident)
        planids = ','.join([str(plan.id) for plan in user_plans])
        if not planids:
            return HttpResponse('{"r": "0","e": "操作成功"}')

    if not planids:
        return HttpResponse('{"r": "1", "e": "请求参数缺失：taskid"}')

    ids = list()

    for planid in planids.split(','):
        if planid.startswith('group_'):
            gs = GroupBackupTaskSchedule.objects.filter(id=planid[6:])
            for schedules in gs.first().schedules.all():
                ids.append(schedules.id)
            gs.delete()
        else:
            ids.append(planid)

    for planid in ids:
        delete_shell_zip(planid)
        BackupTaskScheduleSetting().delete(request=request, backup_task_schedule_id=planid)
    desc = {'操作': '删除备份计划', '计划ID': planids.split(',')}
    SaveOperationLog(
        request.user, OperationLog.TYPE_BACKUP, json.dumps(desc, ensure_ascii=False), get_operator(request))
    return HttpResponse('{"r": "0","e": "操作成功"}')


def check_task_exits_locked_snapshots(request):
    paramsQD = request.POST
    planids = paramsQD.get('taskid', '')

    ids = list()

    for planid in planids.split(','):
        if planid.startswith('group_'):
            gs = GroupBackupTaskSchedule.objects.filter(id=planid[6:])
            for schedules in gs.first().schedules.all():
                ids.append(schedules.id)
        else:
            ids.append(planid)

    _logger.info('check_task_exits_locked_snapshots:{}'.format(planids))
    if not planids:
        return HttpResponse('{"r": "1", "e": "请求参数缺失：taskid"}')
    exits_locked_task = dict()
    for planid in ids:
        backuptaskschedule_obj = BackupTaskSchedule.objects.get(id=planid)
        host_ident = backuptaskschedule_obj.host_ident
        snapshots = HostSnapshot.objects.filter(host__ident=host_ident,
                                                successful=True,
                                                deleted=False,
                                                ).exclude(disk_snapshots__reference_tasks='')
        if len(snapshots) != 0:
            exits_locked_task[planid] = backuptaskschedule_obj.host.display_name
    return HttpResponse(json.dumps(exits_locked_task, ensure_ascii=False))


# retInfo = {"r": 0, "a": "list", "page": "1", "total": 1, "records": 1,
#            "rows": [
#                {"id": planId, "cell": ["planId", "任务名", "客户端名称", "启用/禁用", "操作系统备份", "上次备份时间", "下次备份时间"]}
#            ]}
# 获取User的计划列表
def getPlanList(request):
    paramsQD = request.GET
    perPage = paramsQD.get('rows', '10')  # 设置每页条数
    targPage = paramsQD.get('page', '1')  # 返回第几页
    search_key = request.GET.get('s_key', None)
    bgroup = paramsQD.get('group', '')

    backup_source_type = int(request.GET.get('backup_source_type', BackupTaskSchedule.BACKUP_DISKS))
    schedules = BackupTaskSchedule.objects.filter(deleted=False, backup_source_type=backup_source_type).select_related(
        'host').order_by('id')
    if not request.user.is_superuser:
        schedules = schedules.filter(host__user=request.user)
    rowList = list()

    gss = GroupBackupTaskSchedule.objects.filter(user_id=request.user.id)
    gs_schedule_ids = set()
    for gs in gss:
        for sc in gs.schedules.all():
            gs_schedule_ids.add(sc.id)

    for schedule in schedules:
        if bgroup == 'group':
            if schedule.id in gs_schedule_ids:  # 如果计划在组计划里 就不显示
                continue
        next_run_date = None
        last_run_date = None
        if schedule.last_run_date:
            last_run_date = schedule.last_run_date.strftime(xdatetime.FORMAT_WITH_USER_SECOND)
        if schedule.next_run_date:
            next_run_date = schedule.next_run_date.strftime(xdatetime.FORMAT_WITH_USER_SECOND)
        one_info = {'id': schedule.id, 'cell': [
            schedule.id, schedule.name, schedule.host.name, '启用' if schedule.enabled else '禁用',
            '整机备份', last_run_date, next_run_date
        ]}
        backup_host_ip_str = get_host_ip_str(schedule.host) if search_key else ''
        is_need = serversmgr.filter_hosts(search_key, one_info['cell'][1], one_info['cell'][2], backup_host_ip_str)
        if is_need:
            rowList.append(one_info)
        else:
            pass

    if bgroup == 'group':
        for schedule in gss:
            schedule_id = 'group_{}'.format(schedule.id)
            host_name = '{}台客户端'.format(schedule.schedules.count())
            one_info = {'id': schedule_id,
                        'cell': [schedule_id, schedule.name, host_name, '启用' if schedule.enabled else '禁用', '整机备份', '-',
                                 '-']}
            is_need = serversmgr.filter_hosts(search_key, schedule.name)
            if is_need:
                rowList.append(one_info)

    paginator = Paginator(object_list=rowList, per_page=perPage)
    plansNum = paginator.count
    pagesNum = paginator.num_pages
    getPlans = paginator.page(targPage).object_list

    retInfo = {'r': 0, 'a': 'list', 'page': targPage, 'total': pagesNum, 'records': plansNum, 'rows': getPlans}
    functions.sort_gird_rows(request, retInfo)
    jsonStr = json.dumps(retInfo, ensure_ascii=False)

    return HttpResponse(jsonStr)


def getDriveList(request):
    paramsQD = request.GET
    perPage = paramsQD.get('rows', '10')  # 设置每页条数
    targPage = paramsQD.get('page', '1')  # 返回第几页
    search_key = request.GET.get('s_key', None)
    with open(DRIVE_FILE, 'r') as f:
        data = json.load(f)
    drive_info = {}
    for index, i in enumerate(data['sys']):
        drive_info[index + 1] = data['sys'][index]
    rows = []
    if search_key is None:
        for key, value in drive_info.items():
            row_info = {'id': key, 'cell': [key, value]}
            rows.append(row_info)
    else:
        for key, value in drive_info.items():
            if search_key in value:
                row_info = {'id': key, 'cell': [key, value]}
                rows.append(row_info)
    paginator = Paginator(object_list=rows, per_page=perPage)
    plansNum = paginator.count
    pagesNum = paginator.num_pages
    getPlans = paginator.page(targPage).object_list
    retInfo = {'r': 0, 'a': 'list', 'page': targPage, 'total': pagesNum, 'records': plansNum, 'rows': getPlans}
    functions.sort_gird_rows(request, retInfo)
    jsonStr = json.dumps(retInfo, ensure_ascii=False)

    return HttpResponse(jsonStr)


# 如果DRIVE_FILE不存在,则初始化DRIVE_FILE
def initialization_drive_file(path, file_dir):
    subprocess.call('mkdir ' + file_dir, shell=True)
    subprocess.call('touch ' + path, shell=True)
    json_detail = {"sys": []}
    with open(path, 'w') as f:
        f.write(json.dumps(json_detail, f))


def addDrive(request):
    drive_name_str = request.GET.get('add_key')
    _logger.info('s is {}'.format(drive_name_str))
    add_key = re.sub('\s', '', drive_name_str).strip('|')
    _logger.info('add_key is {}'.format(add_key))
    ret_info = {'r': 0, 'e': ''}
    if not os.path.exists(DRIVE_FILE):
        initialization_drive_file(DRIVE_FILE, DRIVE_FILE_DIR)
    with open(DRIVE_FILE, 'r') as f:
        data = json.load(f)
    drive_info = {}
    for index, i in enumerate(data['sys']):
        drive_info[index + 1] = data['sys'][index]
    result = {}
    if '|' in add_key:
        input_drive_name = add_key.split('|')
        check_exits_null = [name for name in input_drive_name if '' == name]
        if len(check_exits_null) == 0:
            check_aleady_exits_name = [name for name in input_drive_name for drive_name in data['sys'] if
                                       name == drive_name]
            if len(check_aleady_exits_name) == 0:
                result['sys'] = data['sys'] + [new_drive for new_drive in input_drive_name]
                with open(DRIVE_FILE, 'w') as f:
                    f.write(json.dumps(result, f))
            else:
                ret_info = {'r': 1, 'e': '驱动' + ",".join(check_aleady_exits_name) + '已经存在,不能重复添加'}
        else:
            ret_info = {'r': 1, 'e': '|之间存在空驱动名,请检查'}
    else:
        if add_key != '':
            check_aleady_exits_name = [name for name in data['sys'] if add_key == name]
            if len(check_aleady_exits_name) == 0:
                result['sys'] = data['sys'] + [add_key]
                with open(DRIVE_FILE, 'w') as f:
                    f.write(json.dumps(result, f))
            else:
                ret_info = {'r': 1, 'e': '这些驱动' + add_key + '已经存在'}
        else:
            ret_info = {'r': 1, 'e': '你输入的驱动名为空'}
    return HttpResponse(json.dumps(ret_info))


def delDrive(request):
    paramsQD = request.GET
    driveids = paramsQD.get('taskid', '')  # '4,5,6'
    del_drive_ids = driveids.split(',')
    _logger.info('del_drive_ids:{}'.format(del_drive_ids))
    with open(DRIVE_FILE, 'r') as f:
        data = json.load(f)
    drive_info = {}
    for index, i in enumerate(data['sys']):
        drive_info[index + 1] = data['sys'][index]
    need_del = []
    for del_id in del_drive_ids:
        need_del.append(drive_info[int(del_id)])
    del_after = [new for new in data['sys'] if new not in need_del]
    result = {}
    result['sys'] = del_after
    with open(DRIVE_FILE, 'w') as f:
        f.write(json.dumps(result, f))
    return HttpResponse('{"r": "0","e": "操作成功"}')


# retInfo = {"r": "0", "e": "操作成功", "taskname": "任务一", "storagedevice": {"name": "外部存储设备", "value": "2"},
#            "createtime": "2016-4-26 14:22", "backuptype": "整机备份",
#            "src": [{"id": "node_hostIdent", "name": "Windows 2008 x64 SP2  （IP:192.168.1.4）"}],
#            "schedule": {"type": "每月", "starttime": "2016-04-26 15:03:01", "period": "1,2,3,4,5,7"},
#            "retentionperiod": "60", "cleandata": "6", "cdpperiod": "14", "cdptype": "同步", "maxbandwidth": "8"}
# 获取指定计划的详细信息
def getPlanDetail(request):
    paramsQD = request.GET
    taskid = paramsQD.get('taskid', '')
    if taskid.startswith('group_'):
        # 任意返回组内的一个计划
        group_backup_task_schedule_id = taskid[6:]
        gs = GroupBackupTaskSchedule.objects.get(id=group_backup_task_schedule_id)
        request.GET._mutable = True
        request.GET['taskid'] = str(gs.schedules.first().id)
        request.GET['group_backup_task_schedule_id'] = group_backup_task_schedule_id
        request.GET._mutable = False
        return getPlanDetail(request)

    planid = int(taskid)
    if planid < 0:
        return HttpResponse('{"r": "1", "e": "请求参数缺失：taskid"}')
    planDetl = BackupTaskScheduleSetting().get(request=request, backup_task_schedule_id=planid).data
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
    bs = BackupTaskSchedule.objects.filter(id=planDetl["id"])
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

    backup_task_scheduld = BackupTaskSchedule.objects.get(id=planid)
    host = backup_task_scheduld.host
    transmission_type = host.network_transmission_type
    shellInfoStr = planExt.get("shellInfoStr", '-1')
    backup_source_type = backup_task_scheduld.backup_source_type

    taskname = planDetl['name']
    src = [{"id": planDetl['host']['ident'], "name": planDetl['host']['name'], "type": planDetl['host']['type']}]
    group_backup_task_schedule_id = paramsQD.get('group_backup_task_schedule_id')
    if group_backup_task_schedule_id:
        gs = GroupBackupTaskSchedule.objects.get(id=group_backup_task_schedule_id)
        taskname = gs.name
        group_name = gs.host_group.name
        src = [{"id": 'group_{}'.format(group_backup_task_schedule_id), "name": group_name,
                "type": 'group_{}'.format(gs.type)}]

    retInfo = {
        "r": "0", "e": "操作成功",
        "taskname": taskname,
        "storagedevice": {'name': storagedevice, 'value': storagedeviceid},
        "createtime": created_time,
        "backuptype": planDetl['backup_source_type_display'],
        "src": src,
        "schedule": {"type": planDetl['cycle_type_display'], 'starttime': plan_start_date,
                     'period': cycle, 'unit': planExt.get('IntervalUnit', 'day')},
        "retentionperiod": planExt['backupDataHoldDays'],
        "cleandata": planExt['autoCleanDataWhenlt'],
        "cdpperiod": planExt['cdpDataHoldDays'],
        "cdptype": '同步' if planExt['cdpSynchAsynch'] == 0 else '异步',
        "maxbandwidth": planExt['maxBroadband'],
        "keepingpoint": planExt['backupLeastNumber'],
        "isencipher": transmission_type,
        "backupmode": planExt.get('incMode', 2),
        'backupobj': planExt.get('specialMode', 0),
        "removeDuplicatesInSystemFolder": planExt['removeDuplicatesInSystemFolder'],  # True、False
        "excludeDetails": planExt['exclude'],  # [{'exclude_info': 'vol9_guid', 'lable': 'vol9', 'exclude_type': 2}]
        "shellInfos": json.loads(shellInfoStr),  # dict or -1
        "backup_retry": planExt.get('backup_retry', {"count": 5, "enable": True, "interval": 10}),
        'data_keeps_deadline_unit': planExt.get('data_keeps_deadline_unit', None),
        'thread_count': planExt.get('diskreadthreadcount', 1),
        'vmware_tranport_modes': planExt.get('vmware_tranport_modes', 1),
        'vmware_quiesce': planExt.get('vmware_quiesce', True),
        'BackupIOPercentage': planExt.get('BackupIOPercentage', 30),
        'backup_source_type': backup_source_type,
        'nas_protocol': planExt.get('nas_protocol'),
        'nas_username': planExt.get('nas_username'),
        'nas_password': planExt.get('nas_password'),
        'nas_exclude_dir': planExt.get('nas_exclude_dir'),
        'nas_path': planExt.get('nas_path'),
        'enum_threads': planExt.get('enum_threads', 2),
        'sync_threads': planExt.get('sync_threads', 4),
        'cores': planExt.get('cores', 2),
        'memory_mbytes': planExt.get('memory_mbytes', 512),
        'net_limit': planExt.get('net_limit', 300),
        'enum_level': planExt.get('enum_level', 4),
        'sync_queue_maxsize': planExt.get('sync_queue_maxsize', 256),
        'nas_max_space_val': planExt.get('nas_max_space_val', ''),  # 数据库没有，返回空字符串
        'nas_max_space_unit': planExt.get('nas_max_space_unit', 'TB'),
        'nas_max_space_actual': planExt.get('nas_max_space_actual', ''),
    }
    jsonStr = json.dumps(retInfo, ensure_ascii=False)
    return HttpResponse(jsonStr)


def getZipFile(request):
    zip_path = request.GET['path']
    if not os.path.exists(zip_path):
        return HttpResponse(json.dumps({'is_success': False}))

    access_url = r'/static/download/shell/{}/shell.zip'.format(request.user.id)
    tmp_file_path = r'/sbin/aio/box_dashboard/xdashboard' + access_url

    file_utils.touch_file(tmp_file_path)
    shutil.copyfile(zip_path, tmp_file_path)

    return HttpResponse(json.dumps({'url': access_url, 'is_success': True}))


def run_next_time(request):
    result = {'run_next_time': '', "r": 0, "e": "请求成功"}
    try:
        schedule_id = request.GET['schedule_id']
        schedule_type = request.GET['schedule_type']
        plan = BackupTaskSchedule.objects.get(id=schedule_id)
        result['run_next_time'] = plan.next_run_date.strftime('%Y-%m-%d %H:%M:%S')
    except Exception as e:
        result['r'] = 1
        result['e'] = str(e)
    return HttpResponse(json.dumps(result))


def backupmgr_handler(request):
    a = request.GET.get('a', 'none')
    if a == 'none':
        a = request.POST.get('a', 'none')
    if a == 'getplandetail':
        return getPlanDetail(request)
    if a == 'enablebackuptask':
        return disablePlan(request)
    if a == 'deldabackuptask':
        return delPlans(request)
    if a == 'startdabackuptask':
        return excPlanNow(request)
    if a == 'list':
        return getPlanList(request)
    if a == 'get_zip_file':
        return getZipFile(request)
    if a == 'get_plan_next_time':
        return run_next_time(request)
    if a == 'drive_list':
        return getDriveList(request)
    if a == 'add_drive_list':
        return addDrive(request)
    if a == 'deldrive':
        return delDrive(request)
    if a == 'check_task_exits_locked_snapshots':
        return check_task_exits_locked_snapshots(request)
    return HttpResponse(json.dumps({"r": "1", "e": "没有对应的action:{}".format(html.escape(a))}))
