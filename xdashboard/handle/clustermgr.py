import copy
import datetime
import json

from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from rest_framework import status

from apiv1.logic_processors import query_system_info
from apiv1.models import Host, BackupTaskSchedule, ClusterBackupSchedule
from apiv1.signals import end_sleep
from apiv1.views import ClusterBackupScheduleManager, ClusterBackupTaskScheduleExecute, \
    ClusterCdpBackupTaskScheduleExecute
from box_dashboard import xlogging, functions, xdata
from xdashboard.common.license import check_license, get_functional_int_value, is_functional_available
from xdashboard.handle import backup
from xdashboard.handle.authorize.authorize_init import get_clusterbackup_schedule_count
from xdashboard.handle.logserver import SaveOperationLog
from xdashboard.models import OperationLog
from xdashboard.request_util import get_operator

_logger = xlogging.getLogger(__name__)

router = functions.Router(globals())

host_node = {'id': '', 'label': '', 'branch': [], 'checkbox': False, 'inode': True, 'open': True}
disk_node = {'id': '', 'label': ''}


def generate_host_disks_nodes(host_ident):
    host_obj = Host.objects.get(ident=host_ident)
    system_infos = query_system_info(host_obj, update=True)
    if system_infos is None:
        system_infos = query_system_info(host_obj, update=False)
    backup.removing_disk_if_empty_guid(system_infos)

    disks_list = backup.get_host_disks_info(host_obj, system_infos)  # [disk_guid|show_str|index,]
    disks_nodes, host_error = [], False
    for disk_info in disks_list:
        _disk_node = copy.deepcopy(disk_node)
        disk_guid, show_str, disk_index = disk_info.split('|')[0], disk_info.split('|')[1], disk_info.split('|')[2]
        supported = backup.check_is_support_disk(disk_index, host_ident)

        _disk_node['id'], _disk_node['label'] = disk_guid, show_str
        if supported is True:
            _disk_node['checkbox'], _disk_node['disabled'] = True, False
            _disk_node['checked'] = [True, False][-1]
        elif supported is False:
            _disk_node['checkbox'], _disk_node['disabled'] = False, False
        elif supported is None:
            _disk_node['checkbox'], _disk_node['disabled'] = True, True
            _disk_node['checked'] = [True, False][-1]
            host_error = True
        else:
            pass

        disks_nodes.append(_disk_node)

    if host_ident in backup.disks_status:  # 该主机遍历磁盘完毕, 释放cache
        del backup.disks_status[host_ident]

    return disks_nodes, host_error


def genrate_hosts_nodes(hosts):
    hosts_nodes = []
    for host_ident in hosts:
        host_name = Host.objects.get(ident=host_ident).name
        host_disks_nodes, host_error = generate_host_disks_nodes(host_ident)
        _host_node = copy.deepcopy(host_node)
        _host_node['id'], _host_node['label'] = host_ident, host_name
        _host_node['branch'] = host_disks_nodes
        if host_error:
            _host_node['label'] += backup.hostErrorMsg
            _host_node['disabled'] = True
        hosts_nodes.append(_host_node)
    return hosts_nodes


def search_plan_by_keyword(request):
    keyword = request.GET.get('s_key', None)
    object_list = ClusterBackupSchedule.objects.filter(deleted=False, hosts__user_id=request.user.id).distinct()
    if keyword in ['', None]:
        return object_list

    return object_list.filter(
        Q(name__icontains=keyword) | Q(hosts__display_name__icontains=keyword) | Q(hosts__last_ip=keyword)
    )


def get_plans_list(request):
    params = request.GET
    page_rows = params.get('rows', '10')
    page_index = params.get('page', '1')

    paginator = Paginator(object_list=search_plan_by_keyword(request), per_page=page_rows)
    all_rows = paginator.count
    page_num = paginator.num_pages
    get_pagex = paginator.page(page_index).object_list

    ret = {'a': 'list', 'records': all_rows, 'total': page_num, 'page': page_index, 'rows': [], 'r': 0}
    for plan in get_pagex:
        ret['rows'].append({'cell': get_one_plan_abstract_info(plan.id), 'id': plan.id})

    functions.sort_gird_rows(request, ret)
    return HttpResponse(json.dumps(ret))


# 生成集群(关联磁盘)HostTree
def get_hosts_tree(request):
    hosts = request.GET['idents'].split(',')
    hosts_nodes = genrate_hosts_nodes(hosts)
    return HttpResponse(json.dumps(hosts_nodes))


def match_rac_disks(hosts_nodes):
    result = list()
    select_checked = list()
    for j in hosts_nodes:
        for k in j['branch']:
            if 'checked' in k:
                k['map_disk_lab'] = j['label']
                k['host_ident'] = j['id']
                select_checked.append(k)
    for i in range(len(select_checked)):
        if len(select_checked) != 0:
            tmp = list()
            tmp2 = list()
            tmp.append(select_checked[0])
            tmp2.append(0)
            for j in range(1, len(select_checked)):
                if select_checked[0]['id'] == select_checked[j]['id']:
                    tmp.append(select_checked[j])
                    tmp2.append(j)
            if len(tmp) != 1:
                result.append(tmp)
            for index, s in enumerate(tmp2):
                del select_checked[s - index]
        else:
            break
    return result


# 自动关联rac共享盘
def auto_association_rac(request):
    hosts = request.POST['idents'].split(',')
    hosts_nodes = genrate_hosts_nodes(hosts)
    match_rac_disk = match_rac_disks(hosts_nodes)
    _logger.info('matck_rac_disk:{}'.format(match_rac_disk))
    return HttpResponse(json.dumps(match_rac_disk))


# 生成排除(磁盘,分区)HostTree
def get_hosts_disks_partitions_nodes(request):
    return HttpResponse(json.dumps(backup.get_hosts_nodes(request)))


def get_cycle_type(form_params):
    return {
        'bak-cdp': BackupTaskSchedule.CYCLE_CDP,
        'bak-once': BackupTaskSchedule.CYCLE_ONCE,
        'bak-perday': BackupTaskSchedule.CYCLE_PERDAY,
        'bak-perweek': BackupTaskSchedule.CYCLE_PERWEEK,
        'bak-permonth': BackupTaskSchedule.CYCLE_PERMONTH,
    }[form_params['backup_period']['period_type']]


def get_addition_value(form_params, fromWh):
    backup_period = form_params['backup_period']['period_type']
    addition = form_params['backup_period']['addition']  # None/1/1,3,7/1,30,31

    if fromWh == 'backupDayInterval':  # 按间隔时间类型
        if backup_period == 'bak-perday':
            val_unit = form_params['backup_period']['val_unit']
            return int(addition) * {'min': 60, 'hour': 3600, 'day': 24 * 3600}[val_unit]
        else:
            return -1

    if fromWh == 'daysInWeek':
        if backup_period == 'bak-perweek':
            return list(map(int, addition.split(',')))
        else:
            return []

    if fromWh == 'daysInMonth':
        if backup_period == 'bak-permonth':
            return list(map(int, addition.split(',')))
        else:
            return []

    if fromWh == 'intervalUnit':
        if backup_period == 'bak-perday':
            return form_params['backup_period']['val_unit']
        else:
            return ''


def get_incMode(form_params):
    return {
        True: xdata.BACKUP_TASK_SCHEDULE_EXECUTE_TYPE_FORCE_FULL,
        False: xdata.BACKUP_TASK_SCHEDULE_EXECUTE_TYPE_AUTO
    }[form_params['always_full_backup']['value']]


def get_exclude_disks_vols(form_params):
    disks_vols, exclude = form_params['exclude_disks_vols'], {}
    for item in disks_vols:
        host_ident = item['host_ident']
        if host_ident not in exclude:
            exclude[host_ident] = [item]
        else:
            exclude[host_ident].append(item)

    return exclude


def cdp_synch_asynch(mode):
    return {
        'syn': xdata.CDP_MODE_SYN,
        'asyn': xdata.CDP_MODE_ASYN
    }[mode]


def _get_backup_retry(form_params):
    backup_retry_or = form_params['backup_retry']['value']
    return {
        'enable': backup_retry_or['enable'],
        'count': int(backup_retry_or['count']),
        'interval': int(backup_retry_or['interval'])
    }


def _convert_data_keep_duration_to_days(data_keep_duration):
    val, unit = data_keep_duration['value'], data_keep_duration['unit']

    return int(val) if unit == 'day' else int(val) * 30


def get_plan_ext_config(form_params):
    ext_config = {
        'backupDataHoldDays': _convert_data_keep_duration_to_days(form_params['data_keep_duration']),
        'autoCleanDataWhenlt': int(form_params['space_keep_GB']['value']),
        'maxBroadband': int(form_params['max_network_Mb']['value']),
        'backupDayInterval': get_addition_value(form_params, 'backupDayInterval'),  # 按间隔时间: 秒数
        'daysInWeek': get_addition_value(form_params, 'daysInWeek'),
        'daysInMonth': get_addition_value(form_params, 'daysInMonth'),
        'backupLeastNumber': int(form_params['mini_keep_points']['value']),
        'isencipher': int(form_params['transfer_encipher']['value']),
        'incMode': get_incMode(form_params),
        'exclude': get_exclude_disks_vols(form_params),
        'removeDuplicatesInSystemFolder': form_params['enable_dup_sysfolder']['value'],
        'cluster_disks': form_params['cluster_disks'],
        'FullParamsJsonStr': json.dumps(form_params),
        'cdpSynchAsynch': cdp_synch_asynch('syn'),
        'IntervalUnit': get_addition_value(form_params, 'intervalUnit'),  # 按间隔时间, 单位: 'min', 'hour', 'day'
        'backup_retry': _get_backup_retry(form_params),
        'diskreadthreadcount': int(form_params['thread_count']['value']),
        'BackupIOPercentage': int(form_params['BackupIOPercentage']['value']),
        'master_node': form_params['master_host_ident'],
        'cdpDataHoldDays': int(form_params.get('cdpDataHoldDays', 0)),

    }
    shell_infos = form_params.get('shell_infos', None)

    # 创建流程, 更改流程: 启用了脚本功能
    if shell_infos:
        is_modify, plan_id = form_params['from_edit']
        plan_obj = ClusterBackupSchedule.objects.get(id=plan_id) if is_modify else None
        ext_config.update({'shellInfoStr': backup.get_shell_infos_when_enable_shell(shell_infos, plan_obj)})

    return json.dumps(ext_config)


def str2datetime(time_str, str_format=r'%Y-%m-%d %H:%M:%S'):
    return datetime.datetime.strptime(time_str, str_format)


def _check_clusterbackup_license():
    clret = check_license('clusterbackup')
    if clret.get('r', 0) != 0:
        return clret

    clusterbackup_count = get_clusterbackup_schedule_count()
    count = get_functional_int_value('clusterbackup')
    if clusterbackup_count >= count:
        return {'r': 2, 'e': '当前授权数量{}，已创建计划数量{}，请增加授权数量或删除一些计划。'.format(count, clusterbackup_count)}
    return {'r': 0, 'e': 'OK'}


def _check_duplicates_functional(SystemFolderDup):
    if is_functional_available('remove_duplicates_in_system_folder'):
        return {'r': 0, 'e': 'OK'}
    if str(SystemFolderDup) == '1':
        return {'r': 1, 'e': '去重功能未授权。'}
    return {'r': 0, 'e': 'OK'}


# 创建计划, 修改计划
def create_one_plan(request):
    form_params = json.loads(request.POST['FullParamsJsonStr'])
    hosts = form_params['backup_hosts']
    hosts = [host['ident'] for host in hosts]
    hosts = list(Host.objects.filter(ident__in=hosts))

    clret = _check_clusterbackup_license()
    if clret.get('r', 0) != 0:
        return HttpResponse(json.dumps(clret, ensure_ascii=False))

    clret = _check_duplicates_functional(form_params['enable_dup_sysfolder']['value'])
    if clret.get('r', 0) != 0:
        return HttpResponse(json.dumps(clret, ensure_ascii=False))

    api_request = {
        'name': form_params['plan_name']['value'],
        'cycle_type': get_cycle_type(form_params),
        'plan_start_date': str2datetime(form_params['backup_period']['start_datetime']),
        'ext_config': get_plan_ext_config(form_params),
        'storage_node_ident': form_params['storage_device']['ident']
    }

    is_modify, plan_id = form_params['from_edit']
    if is_modify:
        res = ClusterBackupScheduleManager().put(request, api_request, plan_id)
        operation_log(request, {'操作': '更改计划', '计划ID': plan_id, '计划名称': api_request['name']})
    else:
        res = ClusterBackupScheduleManager().post(request, api_request, hosts)
        operation_log(request, {'操作': '创建计划', '计划ID': res.data['id'], '计划名称': api_request['name']})
    rsp = {'r': 0, 'e': '', 'is_modify': is_modify}
    if status.is_success(res.status_code):
        rsp['schedule_id'] = res.data['id']
    else:
        rsp['e'] = '操作失败, {}'.format(res.data) if res.data else '内部错误329'
        rsp['r'] = 1
    return HttpResponse(json.dumps(rsp))


def add_plan_others_info_to_full_param(plan_id, full_param_str, ext_config):
    full_param = json.loads(full_param_str)
    schedule = ClusterBackupSchedule.objects.get(id=plan_id)
    create_time = schedule.created.strftime('%Y-%m-%d %H:%M:%S')
    full_param['create_time'] = {"value": create_time, "label": create_time}

    if 'shellInfoStr' in ext_config:
        full_param['shell_infos'] = ext_config['shellInfoStr']
    else:
        full_param.pop('shell_infos', 'nothing')

    return json.dumps(full_param)


def get_one_plan_full_form_params(request):
    plan_id = request.GET['plan_id']
    ext_config = ClusterBackupScheduleManager().get(request, plan_id).data['ext_config']
    FullParamsJsonStr = json.loads(ext_config)['FullParamsJsonStr']
    FullParamsJsonStr = add_plan_others_info_to_full_param(plan_id, FullParamsJsonStr, json.loads(ext_config))
    return HttpResponse(FullParamsJsonStr)


def get_one_plan_abstract_info(plan_id):
    plan_values = ClusterBackupScheduleManager().get(None, plan_id).data
    last_run = plan_values['last_run_date'] if plan_values['last_run_date'] else '--'
    last_run = last_run.replace('T', ' ')
    next_run = plan_values['next_run_date'] if plan_values['next_run_date'] else '--'
    next_run = next_run.replace('T', ' ')

    cell_list = [
        plan_values['id'], plan_values['name'], '<br>'.join([host['name'] for host in plan_values['hosts']]),
        '启用' if plan_values['enabled'] else '禁用', last_run, next_run
    ]

    return cell_list


def _check_remove_duplicates_in_system_folder_license():
    clret = check_license('remove_duplicates_in_system_folder')
    if clret.get('r', 0) != 0:
        return clret
    if is_functional_available('remove_duplicates_in_system_folder'):
        return {'r': 0, 'e': 'OK'}
    return {'r': 1, 'e': '去重功能未授权。'}


def execute_some_plans(request):
    backuping_type = {
        'full': xdata.BACKUP_TASK_SCHEDULE_EXECUTE_TYPE_FORCE_FULL,
        'inc': xdata.BACKUP_TASK_SCHEDULE_EXECUTE_TYPE_AUTO
    }
    plans, mode = request.GET['ids'].split(','), request.GET['mode']
    force_store_full = request.GET.get('force_store_full', '0')

    if mode == 'full' and force_store_full == '0':
        # 完整备份启用智能增量存储
        clret = _check_remove_duplicates_in_system_folder_license()
        if clret.get('r', 0) != 0:
            return HttpResponse(json.dumps(clret, ensure_ascii=False))

    execute_result = []
    for plan_id in plans:
        schedule = ClusterBackupSchedule.objects.get(id=plan_id)
        plan_name = schedule.name
        if not schedule.enabled:
            execute_result.append('计划[{0}]执行失败: {1}'.format(plan_name, '计划被禁用'))
            result_msg = execute_result[-1]
        else:

            if schedule.cycle_type == BackupTaskSchedule.CYCLE_CDP:
                result = ClusterCdpBackupTaskScheduleExecute().post(request, plan_id,
                                                                    api_request={'type': backuping_type[mode],
                                                                                 'force_store_full': force_store_full})
            else:

                result = ClusterBackupTaskScheduleExecute().post(request, plan_id,
                                                                 api_request={'type': backuping_type[mode],
                                                                              'force_store_full': force_store_full})
            _logger.info('手动执行集群备份计划, id={0}, ret_code={1}'.format(plan_id, result.status_code))

            if result.status_code != status.HTTP_201_CREATED:
                execute_result.append('计划[{0}]执行失败: {1}'.format(plan_name, result.data))

            result_msg = '成功' if result.status_code == status.HTTP_201_CREATED else execute_result[-1]
        operation_log(request, {'操作': '执行立即备份', '计划名称': plan_name, '操作结果': result_msg})

    if len(execute_result):
        return HttpResponse(json.dumps({'r': 1, 'e': '  \n'.join(execute_result)}))
    return HttpResponse(json.dumps({'r': 0, 'e': ''}))


def delete_some_plans(request):
    plans = request.GET['ids'].split(',')
    res_list = [ClusterBackupScheduleManager().delete(request=request, schedule_id=plan) for plan in plans]
    for res in res_list:
        if res.data:
            operation_log(request, {'操作': '删除计划', '计划ID': res.data.id, '计划名称': res.data.name})

    return HttpResponse(json.dumps({'r': 0, 'e': ''}))


def enable_disable_plans(request):
    plans = request.GET['ids'].split(',')
    plans = ClusterBackupSchedule.objects.filter(id__in=plans)
    [plan.set_enabled(not plan.enabled) for plan in plans]
    for plan in plans:
        operation_log(request, {'操作': '启用' if plan.enabled else '禁用', '计划ID': plan.id, '计划名称': plan.name})
        if not plan.enabled:
            end_sleep.send_robust(sender=ClusterBackupSchedule, schedule_id=plan.id)
    return HttpResponse(json.dumps({'r': 0, 'e': ''}))


def operation_log(request, detial):
    user, log_event = request.user, OperationLog.CLUSTER_BACKUP
    SaveOperationLog(user, log_event, json.dumps(detial, ensure_ascii=False), get_operator(request))
