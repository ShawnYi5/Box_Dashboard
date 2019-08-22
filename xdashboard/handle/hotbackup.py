import hashlib
import json
import os
import tarfile
import time
import zipfile
from datetime import datetime, timedelta

from django.core.paginator import Paginator
from django.http import HttpResponse
from rest_framework import status

from apiv1 import ClientIpMg
from apiv1 import work_processors
from apiv1.htb_logic import HTBScheduleCreate, HTBScheduleExecute
from apiv1.htb_task import HTBTaskQuery, get_newest_pointid
from apiv1.models import Host, RestoreTarget, HostSnapshot, HTBSchedule, HTBTask
from apiv1.views import HostSnapshotsWithNormalPerHost, HostSnapshotsWithCdpPerHost, PeHostSessionInfo
from apiv1.views import get_response_error_string
from box_dashboard import xlogging, functions, xdatetime, xdata, boxService
from xdashboard.common import file_utils
from xdashboard.common.license import check_license, get_functional_int_value
from xdashboard.handle.authorize import authorize_init
from xdashboard.handle.logserver import SaveOperationLog
from xdashboard.models import OperationLog
from xdashboard.request_util import get_operator

_logger = xlogging.getLogger(__name__)
router = functions.Router(globals())


def _get_base_snapshot_from_exc_config(schedule_id):
    exc_config = json.loads(HTBSchedule.objects.get(id=schedule_id).ext_config)
    point = exc_config['pointid']
    _id = point.split('|')[1]
    return HostSnapshot.objects.get(id=_id)


def _FmtMAC(mac):
    mac = xdata.standardize_mac_addr(mac)
    if len(mac) == 12:
        mac = '{}-{}-{}-{}-{}-{}'.format(mac[0:2], mac[2:4], mac[4:6], mac[6:8], mac[8:10], mac[10:])
    return mac


# 假数据
def remote_getAdapterInfo(aio_info, ident):
    result = {'r': '0', 'e': '操作成功', 'remote': 1, 'dns': []}
    result['list'] = [
        {'adapter':
             {'id': 'none', 'name': 'none', "isConnected": 0, "mac": '00-00-00-00-00-00'},
         'ips': [
             {'ip': '0.0.0.0', 'mask': '0.0.0.0'}
         ]
         }
    ]

    return HttpResponse(json.dumps(result, ensure_ascii=False))


def getAdapterInfo(request):
    query_params = request.GET
    result = {"r": "0", "e": "操作成功", "remote": 0, "list": [], "dns": []}

    ident = query_params.get('ident', default=None)
    type = query_params.get('type', default=None)
    if not ident:
        return HttpResponse('{"r": "1","e": "ident is not exist"}')

    if type == 'host':
        host = Host.objects.get(ident=ident)
        if host.is_remote:
            return remote_getAdapterInfo(host.aio_info, ident)
        network_adapter_infos = \
            work_processors.HostBackupWorkProcessors.query_current_hardware_info(ident, 'NetAdapterInfo')['NetInfo']
        system_infos = json.loads(boxService.box_service.querySystemInfo(ident))
        Nics = system_infos['Nic']

        if len(network_adapter_infos) == 0:
            for nic in Nics:
                mac = _FmtMAC(nic['Mac'])
                Description = nic['Description']
                if Description == '':
                    Description = nic['Name']
                id = _MD5('{}_{}'.format(nic['Name'], mac))
                adapter = {'id': id, 'name': Description, "isConnected": 1, "mac": mac}
                item = {'adapter': adapter, 'ips': []}
                for ipmask in nic['IpAndMask']:
                    ip = {'ip': ipmask['Ip'], 'mask': ipmask['Mask']}
                    item['ips'].append(ip)
                result['list'].append(item)
        else:
            for nai in network_adapter_infos:
                adapter = {'id': nai['szGuid'], 'name': nai['szDescription'],
                           "isConnected": nai['isConnected'], "mac": _FmtMAC(nai['szMacAddress'])}
                item = {'adapter': adapter, 'ips': []}
                for nic in Nics:
                    if _FmtMAC(nai['szMacAddress']) == _FmtMAC(nic['Mac']):
                        for ipmask in nic['IpAndMask']:
                            ip = {'ip': ipmask['Ip'], 'mask': ipmask['Mask']}
                            item['ips'].append(ip)
                result['list'].append(item)
        result['list'].sort(key=lambda x: x['adapter']['isConnected'], reverse=True)
        result['dns'], result['gate_way'] = _get_dns_gateway_from_nics(Nics)

    elif type == 'pe':
        api_response = PeHostSessionInfo().get(request=request, ident=ident)
        if not status.is_success(api_response.status_code):
            e = get_response_error_string(api_response)
            debug = "PeHostSessionInfo Failed.ident={},status_code={}".format(ident, api_response.status_code)
            return HttpResponse(json.dumps({"r": 1, "e": e, "debug": debug}, ensure_ascii=False))
        for adapter_data in api_response.data['network_adapters']:
            mac = _FmtMAC(adapter_data['szMacAddress'])
            isConnected = adapter_data['isConnected']
            if isConnected:
                mac += '（主网卡）'
            else:
                pass
            adapter = {'id': adapter_data['szGuid'], 'name': adapter_data['szDescription'],
                       "isConnected": isConnected, "mac": mac}
            item = {'adapter': adapter, 'ips': []}
            if isConnected:
                restore_target = RestoreTarget.objects.get(ident=api_response.data['pe_host']['ident'])
                remote_ip = json.loads(restore_target.info)['remote_ip']
                ip = _get_one_ip_from_pe_info(_FmtMAC(adapter_data['szMacAddress']), remote_ip,
                                              api_response.data['system_infos'])
            else:
                ip = [{'ip': '', 'mask': ''}]
            item['ips'].extend(ip)
            result['list'].append(item)
        result['list'].sort(key=lambda x: x['adapter']['isConnected'], reverse=True)
        result['dns'], result['gate_way'] = _get_dns_gateway_from_nics(
            api_response.data['system_infos'].get('Nic', list()))
    else:
        return HttpResponse('{"r": "1","e": "type is not exist"}')

    return HttpResponse(json.dumps(result, ensure_ascii=False))


@xlogging.convert_exception_to_value((list(), ''))
def _get_dns_gateway_from_nics(nics):
    dns, gate_way = list(), ''
    for nic in nics:
        dns.extend(nic['Dns'])
        if nic['GateWay'] and nic['GateWay'] != '0.0.0.0':
            gate_way = nic['GateWay']
    return list(set(dns)), gate_way


def _get_one_ip_from_pe_info(mac, ip, pe_info):
    _logger.info('_get_one_ip_from_pe_info mac{}, ip{}, pe_info{}'.format(mac, ip, pe_info))
    for nic in pe_info.get('Nic', list()):
        if xdata.is_two_mac_addr_equal(nic['Mac'], mac):
            for ip_mask in nic['IpAndMask']:
                if ip_mask['Ip'] == ip:
                    return [{'ip': ip, 'mask': ip_mask['Mask']}]
    return [{'ip': '', 'mask': ''}]


def getAllAdapterInfo(request):
    query_params = request.GET
    plan_id = int(query_params.get('plan_id', default=0))
    result = {"r": 1, "e": "不能获取网卡信息"}
    allPlans = HTBScheduleCreate().get(request=request, api_request={'id': plan_id}).data
    if not allPlans:
        return HttpResponse(
            '{{"r": "1", "e": "HTBScheduleCreate().get() failed plan_id={}"}}'.format(plan_id))

    for plan in allPlans:
        ext_config_obj = json.loads(plan['ext_config'])
        master_adpter = ext_config_obj['master_adpter']
        standby_adpter = ext_config_obj['standby_adpter']
        result = {"r": 0, "e": "操作成功", "master_adpter": master_adpter, "standby_adpter": standby_adpter}

    return HttpResponse(json.dumps(result, ensure_ascii=False))


def check_authorize_before_run_htb():
    htb_tasks = HTBTask.objects.filter(start_datetime__isnull=False, finish_datetime__isnull=True)

    json_txt = authorize_init.get_authorize_plaintext_from_local()
    if json_txt is None:
        return False, '读取授权文件异常'
    val = authorize_init.get_license_val_by_guid('hot_standby_concurrent', json_txt)
    val = '0' if (val is None) else val

    if len(htb_tasks) >= int(val):
        return False, '同时执行热备任务数超过授权允许值({0}个), 目前{1}个任务正在进行中'.format(val, len(htb_tasks))

    return True, ''


def _check_hotbackup_license():
    clret = check_license('hotBackup')
    if clret.get('r', 0) != 0:
        return clret
    HTBSchedule_count = authorize_init.get_hotbackup_schedule_count()
    count = get_functional_int_value('hotBackup')
    if HTBSchedule_count >= count:
        return {'r': 2, 'e': '当前授权数量{}，已创建计划数量{}，请增加授权数量或删除一些计划。'.format(count, HTBSchedule_count)}
    return {'r': 0, 'e': 'OK'}


def create_hotbackup_plan(request):
    result = {"r": "0", "e": "操作成功"}
    query_params = request.POST
    op_type = query_params.get('op_type', default='create_plan')
    edit_plan_id = query_params.get('edit_plan_id', default=None)
    name = query_params.get('name', default=None)
    timetype = int(query_params.get('timetype', default=1))
    src_ident = query_params.get('src_ident', default=None)
    dest_ident = query_params.get('dest_ident', default=None)
    restoretype = int(query_params.get('restoretype', default=1))
    switchtype = int(query_params.get('switchtype', default=1))
    switchback = int(query_params.get('switchback', default=0))
    test_timeinterval = int(query_params.get('test_timeinterval', default=300))
    test_frequency = int(query_params.get('test_frequency', default=3))
    arbitrate_ip = query_params.get('arbitrate_ip', default=None)
    master_adpter = json.loads(query_params.get('master_adpter', default='{}'))
    standby_adpter = json.loads(query_params.get('standby_adpter', default='{}'))
    pointid = query_params.get('pointid', default=None)
    point_time = query_params.get('point_time', default=None)
    immediately = query_params.get('immediately', default='1')
    restore_time = query_params.get('restore_time', default='')
    # 切换源机的IP, 1切换，0不切换
    switch_change_master_ip = int(query_params.get('switch_change_master_ip', default=1))

    stop_script_exe_name = query_params.get('stop_script_exe_name', default=None)
    stop_script_exe_params = query_params.get('stop_script_exe_params', default=None)
    stop_script_work_path = query_params.get('stop_script_work_path', default=None)
    stop_script_unzip_path = query_params.get('stop_script_unzip_path', default=None)
    stop_script_zip_path = query_params.get('stop_script_zip_path', default=None)

    start_script_exe_name = query_params.get('start_script_exe_name', default=None)
    start_script_exe_params = query_params.get('start_script_exe_params', default=None)
    start_script_work_path = query_params.get('start_script_work_path', default=None)
    start_script_unzip_path = query_params.get('start_script_unzip_path', default=None)
    start_script_zip_path = query_params.get('start_script_zip_path', default=None)

    ext_config = dict()

    clret = _check_hotbackup_license()
    if clret.get('r', 0) != 0:
        return HttpResponse(json.dumps(clret, ensure_ascii=False))

    if switchtype == 2:
        ext_config['detect_arbitrate_2_master_business_ip'] = int(
            query_params.get('detect_arbitrate_2_master_business_ip', default=0))
        ext_config['detect_aio_2_master_control_ip'] = int(
            query_params.get('detect_aio_2_master_control_ip', default=0))
        ext_config['detect_aio_2_master_business_ip'] = int(
            query_params.get('detect_aio_2_master_business_ip', default=0))

    # 业务停止与启动脚本
    stop_eq_start = False
    if stop_script_zip_path and os.path.isfile(stop_script_zip_path):
        if stop_script_zip_path == start_script_zip_path:
            stop_eq_start = True
        stop_script_zip_path = file_utils.move_tmp_file(stop_script_zip_path)
        ext_config['stop_script_exe_name'] = stop_script_exe_name
        ext_config['stop_script_exe_params'] = stop_script_exe_params
        ext_config['stop_script_work_path'] = stop_script_work_path
        ext_config['stop_script_unzip_path'] = stop_script_unzip_path
        ext_config['stop_script_zip_path'] = stop_script_zip_path
    else:
        ext_config['stop_script_zip_path'] = None

    if stop_eq_start or (start_script_zip_path and os.path.isfile(start_script_zip_path)):
        if stop_eq_start:
            start_script_zip_path = stop_script_zip_path
        else:
            start_script_zip_path = file_utils.move_tmp_file(start_script_zip_path)
        ext_config['start_script_exe_name'] = start_script_exe_name
        ext_config['start_script_exe_params'] = start_script_exe_params
        ext_config['start_script_work_path'] = start_script_work_path
        ext_config['start_script_unzip_path'] = start_script_unzip_path
        ext_config['start_script_zip_path'] = start_script_zip_path
    else:
        ext_config['start_script_zip_path'] = None

    ext_config['restoretype'] = restoretype
    if restoretype == 1:
        # 整机还原
        drivers_ids = query_params.get('drivers_ids', default=None)
        drivers_ids_force = query_params.get('drivers_ids_force', '')
        drivers_type = query_params.get('drivers_type', default=None)
        disks = query_params.get('disks', default=None)
        ex_vols = query_params.get('ex_vols', default=None)
        boot_vols = query_params.get('boot_vols', default=None)
        disable_fast_boot = query_params.get('disable_fast_boot', default=None)
        target_info = json.dumps(HTBScheduleExecute.get_restore_target_macs(dest_ident), ensure_ascii=False)
        dst_host_ident = ''
        dest_host = RestoreTarget.objects.filter(ident=dest_ident).first()

        ext_config['drivers_ids'] = drivers_ids
        ext_config['drivers_ids_force'] = drivers_ids_force
        ext_config['drivers_type'] = drivers_type
        ext_config['disks'] = disks
        ext_config['ex_vols'] = ex_vols
        ext_config['boot_vols'] = boot_vols
        ext_config['disable_fast_boot'] = disable_fast_boot
    else:
        # 卷还原
        vol_maps = query_params.get('vol_maps', default=None)
        index_list = query_params.get('index_list', default=None)

        ext_config['vol_maps'] = vol_maps
        ext_config['index_list'] = index_list
        target_info = '[]'
        dst_host_ident = dest_ident
        dest_host = Host.objects.get(ident=dest_ident)

    if timetype == 1:
        # (NEW_POINT_NEED_UPDATE, '还原到最新')
        task_type = 1
    else:
        # (OLD_POINT_NOT_NEED_UPDATE, '还原到特定点')
        task_type = 0

    # switchtype 1 手工切换 2 主备自动切换
    ext_config['switchtype'] = switchtype
    # switchback 1 或者 0 备机故障后自动切换回主机
    ext_config['switchback'] = switchback
    # test_timeinterval 故障检测间隔时间 秒
    ext_config['test_timeinterval'] = test_timeinterval
    # test_frequency 故障检测次数
    ext_config['test_frequency'] = test_frequency
    # arbitrate_ip 仲裁IP
    ext_config['arbitrate_ip'] = arbitrate_ip
    # 网卡信息
    ext_config['master_adpter'] = master_adpter
    ext_config['standby_adpter'] = standby_adpter
    # 还原点信息
    ext_config['pointid'] = pointid
    ext_config['point_time'] = point_time
    ext_config['restore_time'] = restore_time
    ext_config['switch_change_master_ip'] = switch_change_master_ip

    src_host = Host.objects.filter(ident=src_ident).first()

    if dest_host is None or src_host is None:
        return HttpResponse(
            '{{"r": "1", "e": "参数错误，找不到源机或目标机 src_ident={},dest_ident={}"}}'.format(src_ident, dest_ident))
    api_request = {"name": name, "task_type": task_type, "target_info": target_info, "host": src_host,
                   "ext_config": json.dumps(ext_config, ensure_ascii=False), 'restore_type': restoretype,
                   'dst_host_ident': dst_host_ident}
    if op_type == 'create_plan':
        plan_name = _srcHost_in_plan(request, src_host.ident)
        if plan_name:
            error_msg = '一台客户端只能创建一个热备计划，该客户端已存在热备计划：{}。'.format(plan_name)
            return HttpResponse(json.dumps({'r': 2, 'e': error_msg}, ensure_ascii=False))
        api_response = HTBScheduleCreate().post(request, api_request)
        if not status.is_success(api_response.status_code):
            return HttpResponse(
                '{{"r": "1", "e": "HTBScheduleCreate().post() failed {}"}}'.format(api_response.status_code))
        plan_id = api_response.data['id']
    elif op_type == 'change_plan':
        plan_id = int(edit_plan_id)
        allPlans = HTBScheduleCreate().get(request=request, api_request={'id': plan_id}).data
        if not allPlans:
            return HttpResponse(
                '{{"r": "1", "e": "HTBScheduleCreate().get() failed plan_id={}"}}'.format(plan_id))

        for plan in allPlans:
            ext_config_obj = json.loads(plan['ext_config'])
            stop_script_zip_path = ext_config_obj['stop_script_zip_path']
            start_script_zip_path = ext_config_obj['start_script_zip_path']
            if stop_script_zip_path and os.path.isfile(stop_script_zip_path):
                os.remove(stop_script_zip_path)
            if start_script_zip_path and os.path.isfile(start_script_zip_path):
                os.remove(start_script_zip_path)

            api_request['filter'] = {'id': plan_id}

            api_response = HTBScheduleCreate().update(request, api_request)
            if not status.is_success(api_response.status_code):
                e = get_response_error_string(api_response)
                debug = "HTBScheduleCreate().update() failed {}".format(api_response.status_code)
                return HttpResponse(json.dumps({"r": 1, "e": e, "debug": debug}, ensure_ascii=False))
    else:
        return HttpResponse(
            '{{"r": "1", "e": "参数不正确，op_type={}"}}'.format(op_type))
    desc = {}
    if op_type == 'create_plan':
        desc['操作'] = '创建热备计划'
    elif op_type == 'change_plan':
        desc['操作'] = '更改热备计划'
    src_host = api_request['host']
    api_request['host'] = '{}（{}）'.format(src_host.display_name, src_host.last_ip)
    desc['hotbackup_plan_detail'] = api_request
    desc['结果'] = '操作成功'
    SaveOperationLog(
        request.user, OperationLog.TYPE_HOT_BACKUP, json.dumps(desc, ensure_ascii=False), get_operator(request))

    if immediately == '1':
        authorize_ok, msg = check_authorize_before_run_htb()
        if not authorize_ok:
            return HttpResponse(json.dumps({"r": 1, "e": msg, "debug": msg}, ensure_ascii=False))

        api_response = HTBScheduleExecute().post(plan_id)
        desc = {"操作": "执行热备计划", "名称": name}
        if not status.is_success(api_response.status_code):
            desc["结果"] = '操作失败。{}'.format(api_response.data)
            SaveOperationLog(
                request.user, OperationLog.TYPE_HOT_BACKUP, json.dumps(desc, ensure_ascii=False), get_operator(request))
            return HttpResponse(json.dumps({"r": 1, "e": api_response.data}, ensure_ascii=False))
        desc["结果"] = '操作成功'
        SaveOperationLog(
            request.user, OperationLog.TYPE_HOT_BACKUP, json.dumps(desc, ensure_ascii=False), get_operator(request))

    json_str = json.dumps(result, ensure_ascii=False)
    return HttpResponse(json_str)


def _getPlanInfo(plan):
    src_host = Host.objects.filter(id=plan['host']).first()
    if src_host is None:
        src_name = plan['host']
    else:
        src_name = src_host.name

    if plan['restore_type'] == HTBSchedule.HTB_RESTORE_TYPE_SYSTEM:
        ident = HTBScheduleExecute.get_restore_target_from_mac(json.loads(plan['target_info']))
        dest_host = RestoreTarget.objects.filter(ident=ident).first()
        if dest_host is None:
            dest_name = plan['target_info']
        else:
            dest_name = '{}'.format(dest_host.display_name)
    else:
        host = Host.objects.get(ident=plan['dst_host_ident'])
        dest_name = '{}'.format(host.name)

    rs = HTBTaskQuery(plan['id']).query()
    if rs:
        result = rs['status']
        sub_info = rs['sub_info']
    else:
        result = '正在获取数据'
        sub_info = list()

    htb_progress = result
    for info in sub_info:
        label = ''
        for i in info:
            label += i
            label += ' '
        htb_progress += '\r\n'
        htb_progress += label

    ext_config = json.loads(plan['ext_config'])
    switch_change_master_ip = ext_config.get('switch_change_master_ip', '1')

    return [plan['id'], plan['name'], src_name, dest_name, plan['task_type'], plan['enabled'],
            plan['ext_config'], plan['in_stand_by'], htb_progress, switch_change_master_ip]


def listplan(request):
    paramsQD = request.GET
    perPage = paramsQD.get('rows', '10')  # 设置每页条数
    targPage = paramsQD.get('page', '1')  # 返回第几页
    html_mark = paramsQD.get('html_mark', 'list')
    api_request = {'filter': {'deleted': False}}
    allPlans = HTBScheduleCreate().get(request=request, api_request=api_request).data

    rs = list()
    for plan in allPlans:
        htb_schedule = HTBSchedule.objects.get(id=plan['id'])
        last_task = htb_schedule.htb_task.last()
        if html_mark == 'switch':
            if last_task and (last_task.status in [HTBTask.SYNC, HTBTask.VOL_SYNC]):
                rs.append(plan)
            else:
                continue
        else:
            rs.append(plan)

    paginator = Paginator(object_list=rs, per_page=perPage)
    plansNum = paginator.count
    pagesNum = paginator.num_pages
    getPlans = paginator.page(targPage).object_list

    rowList = list()
    for plan in getPlans:
        tmp = {'id': plan['id'], 'cell': _getPlanInfo(plan)}
        rowList.append(tmp)

    retInfo = {'r': 0, 'page': targPage, 'total': pagesNum, 'records': plansNum, 'rows': rowList}
    functions.sort_gird_rows(request, retInfo)
    jsonStr = json.dumps(retInfo, ensure_ascii=False)

    return HttpResponse(jsonStr)


def get_switch_params(request):
    # 肯定是(NEW_POINT_NEED_UPDATE, '还原到最新')，需要返回几个最近的备份点
    result = {"r": "0", "e": "操作成功", "list": []}
    paramsQD = request.POST
    plan_id = paramsQD.get('id', '0')
    st_date = paramsQD.get('st_date', '-1')
    host_id = None

    api_request = {'id': plan_id}
    allPlans = HTBScheduleCreate().get(request=request, api_request=api_request).data
    for plan in allPlans:
        host_id = plan['host']

    if not host_id:
        return HttpResponse('{"r": "1","e": "host_id is None. Failed."}')

    # base_host_snapshot = _get_base_snapshot_from_exc_config(plan_id)

    hostobj = Host.objects.filter(id=host_id).first()
    host_ident = hostobj.ident

    if not hostobj:
        return HttpResponse(json.dumps({"r": 2, "e": "not find host host_id={}".format(host_id)}, ensure_ascii=False))

    if st_date == '-1':
        start_date = _get_host_latest_start_datetime(host_ident)
    else:
        start_date = datetime.strptime(st_date, xdatetime.FORMAT_ONLY_DATE)

    end_date = start_date.date() + timedelta(days=1)
    api_request = {'begin': start_date.strftime(xdatetime.FORMAT_ONLY_DATE),
                   'end': end_date.strftime(xdatetime.FORMAT_ONLY_DATE),
                   'finish': True,
                   'use_serializer': False}

    api_response = HostSnapshotsWithNormalPerHost().get(request=request, ident=host_ident, api_request=api_request)
    if not status.is_success(api_response.status_code):
        e = get_response_error_string(api_response)
        debug = "HostSnapshotsWithNormalPerHost().get(begin:{} end:{} ident:{}) failed {}".format(
            start_date, end_date, host_ident, api_response.status_code)
        return HttpResponse(json.dumps({"r": 1, "e": e, "debug": debug}, ensure_ascii=False))

    for host_snapshot in api_response.data:
        backup_point = {
            "id": '{}|{}|{}'.format(xdata.SNAPSHOT_TYPE_NORMAL, host_snapshot['id'], host_snapshot['start_datetime']),
            "time": host_snapshot['start_datetime'], "type": xdata.SNAPSHOT_TYPE_NORMAL,
            "enddate": host_snapshot['start_datetime'], "recommend": False}
        result["list"].append(backup_point)

    api_response = HostSnapshotsWithCdpPerHost().get(None, ident=host_ident, api_request=api_request)
    if not status.is_success(api_response.status_code):
        e = get_response_error_string(api_response)
        debug = "HostSnapshotsWithCdpPerHost().get(begin:{} end:{} ident:{}) failed {}".format(start_date, end_date,
                                                                                               host_ident,
                                                                                               api_response.status_code)
        return HttpResponse(json.dumps({"r": 1, "e": e, "debug": debug}, ensure_ascii=False))

    for host_snapshot in api_response.data:
        backup_point = {"id": '{}|{}|{}|{}'.format(xdata.SNAPSHOT_TYPE_CDP, host_snapshot['id'], host_snapshot['begin'],
                                                   host_snapshot['end']),
                        "time": '{} - {}'.format(host_snapshot['begin'], host_snapshot['end']),
                        "type": xdata.SNAPSHOT_TYPE_CDP, "enddate": host_snapshot['end'], "recommend": False}
        result["list"].append(backup_point)

    result["list"].sort(key=lambda x: (xdatetime.string2datetime(x['enddate'])), reverse=True)
    result['st_date'] = api_request['begin']

    json_str = json.dumps(result, ensure_ascii=False)
    return HttpResponse(json_str)


def hotabckup_switch(request):
    result = {"r": "0", "e": "操作成功"}
    paramsQD = request.POST
    plan_id = paramsQD.get('id', '0')
    # task_type 0 (OLD_POINT_NOT_NEED_UPDATE, '还原到特定点'),
    # task_type 1 (NEW_POINT_NEED_UPDATE, '还原到最新')
    task_type = paramsQD.get('task_type', '0')
    point_id = paramsQD.get('point_id', '0')
    restoretime = paramsQD.get('restoretime', '0')
    switchip = paramsQD.get('switchip', '1')
    use_latest = paramsQD.get('use_latest', '0')

    api_request = {'id': plan_id}
    allPlans = HTBScheduleCreate().get(request=request, api_request=api_request).data
    if not allPlans:
        return HttpResponse(
            '{{"r": "1", "e": "HTBScheduleCreate().get() failed plan_id={}"}}'.format(plan_id))
    ext_config = None
    name = 'none'
    for plan in allPlans:
        ext_config = plan['ext_config']
        name = plan['name']

    ext_config_obj = json.loads(ext_config)
    if int(use_latest) == 1:
        point_id = get_newest_pointid(ext_config_obj, HTBSchedule.objects.get(id=plan_id))
    ext_config_obj['manual_switch'] = {'task_type': task_type, 'point_id': point_id, 'restoretime': restoretime,
                                       'switchip': switchip, 'status': 1, 'use_latest': use_latest}

    api_request = {
        'filter': {'id': plan_id},
        'ext_config': json.dumps(ext_config_obj, ensure_ascii=False)
    }
    desc = {"操作": "切换到备机", "计划名称": name}

    api_response = HTBScheduleCreate().update(request, api_request)
    if not status.is_success(api_response.status_code):
        e = get_response_error_string(api_response)
        debug = "HTBScheduleCreate().update() failed {},api_request={}".format(api_response.status_code, api_request)
        desc['结果'] = '操作失败,{},code:{}'.format(e, api_response.status_code)
        SaveOperationLog(
            request.user, OperationLog.TYPE_HOT_BACKUP, json.dumps(desc, ensure_ascii=False), get_operator(request))
        return HttpResponse(json.dumps({"r": 1, "e": e, "debug": debug}, ensure_ascii=False))

    json_str = json.dumps(result, ensure_ascii=False)
    desc['结果'] = '操作成功'
    SaveOperationLog(
        request.user, OperationLog.TYPE_HOT_BACKUP, json.dumps(desc, ensure_ascii=False), get_operator(request))
    return HttpResponse(json_str)


def _srcHost_in_plan(request, host_ident):
    hostobj = Host.objects.filter(ident=host_ident).first()
    api_request = {'filter': {'deleted': False, 'host': hostobj.id}}
    allPlans = HTBScheduleCreate().get(request=request, api_request=api_request).data
    if not allPlans:
        return None
    for plan in allPlans:
        return plan['name']
    return None


def is_dest_host_in_plan(request):
    paramsQD = request.GET
    dest_ident = paramsQD.get('ident', '0')
    json_str = json.dumps({'r': 0, 'e': '操作成功'}, ensure_ascii=False)
    mac_info = HTBScheduleExecute.get_restore_target_macs(dest_ident)
    if mac_info is not None:
        api_request = {'filter': {'deleted': False, 'target_info': json.dumps(mac_info, ensure_ascii=False)}}
        allPlans = HTBScheduleCreate().get(request=request, api_request=api_request).data
        for plan in allPlans:
            json_str = json.dumps({'r': 1, 'e': '热备目标在计划[{}]中'.format(plan['name'])}, ensure_ascii=False)
    return HttpResponse(json_str)


def get_backup_points(request):
    result = {"r": "0", "e": "操作成功", "list": []}
    paramsQD = request.GET
    host_ident = paramsQD.get('ident', '0')
    checkident = int(paramsQD.get('checkident', '0'))

    if not host_ident:
        return HttpResponse('{"r": "1","e": "请求参数缺失：ident"}')

    if checkident == 1:
        plan_name = _srcHost_in_plan(request, host_ident)
        if plan_name:
            error_msg = '一台客户端只能创建一个热备计划，该客户端已存在热备计划：{}。'.format(plan_name)
            return HttpResponse(json.dumps({'r': 2, 'e': error_msg}, ensure_ascii=False))

    start_date = datetime.strptime('2015-01-01 00:00:00', "%Y-%m-%d %H:%M:%S")
    end_date = datetime.now().date() + timedelta(days=1)

    api_request = {'begin': start_date.strftime(xdatetime.FORMAT_ONLY_DATE),
                   'end': end_date.strftime(xdatetime.FORMAT_ONLY_DATE),
                   'use_serializer': False}

    api_response = HostSnapshotsWithNormalPerHost().get(request=request, ident=host_ident, api_request=api_request)
    if not status.is_success(api_response.status_code):
        e = get_response_error_string(api_response)
        debug = "HostSnapshotsWithNormalPerHost().get(begin:{} end:{} ident:{}) failed {}".format(start_date, end_date,
                                                                                                  host_ident,
                                                                                                  api_response.status_code)
        return HttpResponse(json.dumps({"r": 1, "e": e, "debug": debug}, ensure_ascii=False))

    for host_snapshot in api_response.data:
        result['list'].append({
            "id": '{}|{}|{}'.format(xdata.SNAPSHOT_TYPE_NORMAL, host_snapshot['id'], host_snapshot['start_datetime']),
            "title": '整机备份 {}'.format(host_snapshot['start_datetime']),
            "startdate": host_snapshot['start_datetime'],
            "enddate": host_snapshot['start_datetime'],
            "type": xdata.SNAPSHOT_TYPE_NORMAL
        })

    api_response = HostSnapshotsWithCdpPerHost().get(None, ident=host_ident, api_request=api_request)
    if not status.is_success(api_response.status_code):
        e = get_response_error_string(api_response)
        debug = "HostSnapshotsWithCdpPerHost().get(begin:{} end:{} ident:{}) failed {}".format(start_date, end_date,
                                                                                               host_ident,
                                                                                               api_response.status_code)
        return HttpResponse(json.dumps({"r": 1, "e": e, "debug": debug}, ensure_ascii=False))

    for host_snapshot in api_response.data:
        result['list'].append({
            "id": '{}|{}|{}|{}'.format(xdata.SNAPSHOT_TYPE_CDP, host_snapshot['id'], host_snapshot['begin'],
                                       host_snapshot['end']),
            "title": 'CDP备份 {}'.format(host_snapshot['begin']),
            "startdate": host_snapshot['begin'],
            "enddate": host_snapshot['end'],
            "type": xdata.SNAPSHOT_TYPE_CDP
        })

    result['list'].sort(key=lambda x: (xdatetime.string2datetime(x['enddate'])), reverse=True)

    result['is_windows'] = host_is_windows(host_ident)

    json_str = json.dumps(result, ensure_ascii=False)
    return HttpResponse(json_str)


def host_is_windows(host_ident):
    host = Host.objects.get(ident=host_ident)
    system_infos = json.loads(host.ext_info).get('system_infos')
    if system_infos:
        return 'LINUX' not in system_infos['System']['SystemCaption'].upper()
    return False


def del_hotbackup_plan(request):
    result = {"r": "0", "e": "操作成功"}
    paramsQD = request.GET
    plan_ids = paramsQD.get('ids', '0')

    for _id in plan_ids.split(','):
        _id = int(_id)
        api_request = {
            'filter': {'id': _id},
            'deleted': True
        }

        api_response = HTBScheduleCreate().update(request, api_request)
        if not status.is_success(api_response.status_code):
            return HttpResponse(
                '{{"r": "1", "e": "HTBScheduleCreate().update() failed {}"}}'.format(api_response.status_code))

        api_request = {'id': _id}
        allPlans = HTBScheduleCreate().get(request=request, api_request=api_request).data
        if not allPlans:
            return HttpResponse('{{"r": "1", "e": "HTBScheduleCreate().get() failed plan_id={}"}}'.format(_id))

        for plan in allPlans:
            ext_config = plan['ext_config']
            ext_config_obj = json.loads(ext_config)
            stop_script_zip_path = ext_config_obj['stop_script_zip_path']
            start_script_zip_path = ext_config_obj['start_script_zip_path']
            if stop_script_zip_path and os.path.isfile(stop_script_zip_path):
                os.remove(stop_script_zip_path)
            if start_script_zip_path and os.path.isfile(start_script_zip_path):
                os.remove(start_script_zip_path)
            desc = {"操作": "删除热备计划", "名称": plan["name"], "结果": "操作成功"}
            SaveOperationLog(
                request.user, OperationLog.TYPE_HOT_BACKUP, json.dumps(desc, ensure_ascii=False), get_operator(request))

    json_str = json.dumps(result, ensure_ascii=False)
    return HttpResponse(json_str)


def exe_hotbackup_plan(request):
    paramsQD = request.GET
    plan_ids = paramsQD.get('ids', '0')

    for _id in plan_ids.split(','):
        authorize_ok, msg = check_authorize_before_run_htb()
        if not authorize_ok:
            return HttpResponse(json.dumps({"r": 1, "e": msg}, ensure_ascii=False))

        plan_id = int(_id)
        desc = {"操作": "执行热备计划"}
        allPlans = HTBScheduleCreate().get(request=request, api_request={'id': plan_id}).data
        if allPlans:
            for plan in allPlans:
                desc["名称"] = plan['name']
        api_response = HTBScheduleExecute().post(plan_id)
        if not status.is_success(api_response.status_code):
            desc['结果'] = '操作失败。{}'.format(api_response.data)
            SaveOperationLog(
                request.user, OperationLog.TYPE_HOT_BACKUP, json.dumps(desc, ensure_ascii=False), get_operator(request))
            return HttpResponse(json.dumps({"r": 1, "e": api_response.data}, ensure_ascii=False))
        desc['结果'] = '操作成功。'
        SaveOperationLog(
            request.user, OperationLog.TYPE_HOT_BACKUP, json.dumps(desc, ensure_ascii=False), get_operator(request))
    return HttpResponse(json.dumps({"r": 0, "e": '操作成功'}, ensure_ascii=False))


def enable_hotbackup_plan(request):
    result = {"r": "0", "e": "操作成功"}
    paramsQD = request.GET
    plan_ids = paramsQD.get('ids', '0')

    for _id in plan_ids.split(','):
        _id = int(_id)
        api_request = {'id': _id}
        allPlans = HTBScheduleCreate().get(request=request, api_request=api_request).data
        if not allPlans:
            return HttpResponse(
                '{{"r": "1", "e": "HTBScheduleCreate().get() failed plan_id={}"}}'.format(_id))

        for plan in allPlans:
            desc = {}
            enabled = not plan['enabled']
            if enabled:
                desc["操作"] = "启用热备计划"
            else:
                desc["操作"] = "禁用用热备计划"
            desc["名称"] = plan["name"]
            exc_config = json.loads(plan['ext_config'])
            if 'manual_switch' in exc_config:
                exc_config['manual_switch']['status'] = 4
                api_request = {
                    'filter': {'id': _id},
                    'enabled': enabled,
                    'exc_config': json.dumps(exc_config)
                }
            else:
                api_request = {
                    'filter': {'id': _id},
                    'enabled': enabled
                }

            api_response = HTBScheduleCreate().update(request, api_request)
            if not status.is_success(api_response.status_code):
                e = get_response_error_string(api_response)
                debug = "HTBScheduleCreate().update() failed {}".format(api_response.status_code)
                desc["结果"] = "操作失败。{} code({})".format(e, api_response.status_code)
                SaveOperationLog(
                    request.user, OperationLog.TYPE_HOT_BACKUP, json.dumps(desc, ensure_ascii=False),
                    get_operator(request))
                return HttpResponse(json.dumps({"r": 1, "e": e, "debug": debug}, ensure_ascii=False))
            desc["结果"] = "操作成功"
            SaveOperationLog(
                request.user, OperationLog.TYPE_HOT_BACKUP, json.dumps(desc, ensure_ascii=False), get_operator(request))

    json_str = json.dumps(result, ensure_ascii=False)
    return HttpResponse(json_str)


def get_hotbackup_plan(request):
    result = {"r": "0", "e": "操作成功"}
    paramsQD = request.GET
    plan_id = int(paramsQD.get('id', '0'))
    api_request = {'id': plan_id}
    allPlans = HTBScheduleCreate().get(request=request, api_request=api_request).data
    if not allPlans:
        return HttpResponse('{{"r": "1", "e": "HTBScheduleCreate().get() failed plan_id={}"}}'.format(plan_id))

    for plan in allPlans:
        result['name'] = plan['name']
        result['task_type'] = plan['task_type']
        ident = HTBScheduleExecute.get_restore_target_from_mac(json.loads(plan['target_info']))
        dest_host = RestoreTarget.objects.filter(ident=ident).first()
        if dest_host:
            result['dest'] = [{'id': dest_host.ident, 'name': '{}'.format(dest_host.display_name)}]
        host_id = plan['host']
        hostobj = Host.objects.filter(id=host_id).first()
        result['src'] = [{'id': hostobj.ident, 'name': '{}（{}）'.format(hostobj.display_name, hostobj.last_ip)}]
        result['ext_config'] = plan['ext_config']
        result['in_stand_by'] = plan['in_stand_by']
        result['is_running'] = HTBTask.objects.filter(schedule__id=plan['id'], start_datetime__isnull=False,
                                                      finish_datetime__isnull=True).exists()

    json_str = json.dumps(result, ensure_ascii=False)
    return HttpResponse(json_str)


def _MD5(src):
    m2 = hashlib.md5()
    m2.update(src.encode('utf-8'))
    return m2.hexdigest()


def delexpirefiles(exportpath):
    ctime = time.time()
    for dirpath, dirnames, filenames in os.walk(exportpath):
        for filename in filenames:
            if filename.find('tmp_') == 0:
                thefile = os.path.join(dirpath, filename)
                mtime = os.path.getmtime(thefile)
                if ctime - mtime > 5 * 60:
                    os.remove(thefile)


def _check_up_file_valid(filepath, exename, ext):
    if ext == '.zip':
        try:
            z = zipfile.ZipFile(filepath, mode='r')
            for filename in z.namelist():
                # TODO:文件名不支持中文
                if filename.lower() == exename.lower():
                    return True
            return False
        except Exception as e:
            _logger.info('_check_up_file_valid Failed.e={}'.format(e))
            return False
    elif ext == 'tar.gz':
        try:
            with tarfile.open(filepath) as tar:
                names = tar.getnames()
                for filename in names:
                    if filename.lower()[-len(exename):] == exename.lower():
                        return True
            return False
        except Exception as e:
            _logger.info('_check_up_file_valid Failed.e={}'.format(e))
            return False
    return True


def upload_script(request):
    myfile = request.FILES.get("script_zip", None)
    exename = request.POST.get("exe_name", 'none')
    script_path = '/home/mnt/stop_start_web_script/tmp'
    try:
        os.makedirs(script_path)
    except OSError as e:
        pass
    delexpirefiles(script_path)

    if not myfile:
        return HttpResponse(json.dumps({"r": 1, "e": "无上传文件"}, ensure_ascii=False))

    org_filename = myfile.name
    ext = os.path.splitext(org_filename)[1].lower()
    if ext not in ('.zip', '.gz',):
        return HttpResponse(json.dumps({"r": 2, "e": "请上传zip或.tar.gz格式的文件", "ext": ext}, ensure_ascii=False))
    if ext == '.gz':
        ext = 'tar.gz'
    r = 0
    ret_e = '操作成功'
    data_stream = myfile.read()
    filepath = os.path.join(script_path, 'tmp_{}{}'.format(_MD5(org_filename), ext))
    if os.path.isfile(filepath):
        os.remove(filepath)
    try:
        destination = open(filepath, 'wb')  # 打开特定的文件进行二进制的写操作
        destination.write(data_stream)
        destination.close()
    except Exception as e:
        ret_e = str(e)
        r = 1

    if not _check_up_file_valid(filepath, exename, ext):
        r = 2
        ret_e = '{}中找不到可执行文件：{}'.format(myfile.name, exename)

    return HttpResponse(json.dumps({"r": r, "e": ret_e, "filepath": filepath}, ensure_ascii=False))


def _test_host_is_ok(ip):
    return ClientIpMg.ClientIpSwitch.ping_exist(ip)


def network_status(request):
    result = {"r": "0", "e": "操作成功"}
    paramsQD = request.GET
    plan_id = int(paramsQD.get('id', '0'))
    api_request = {'id': plan_id}
    allPlans = HTBScheduleCreate().get(request=request, api_request=api_request).data
    if not allPlans:
        return HttpResponse('{{"r": "1", "e": "HTBScheduleCreate().get() failed plan_id={}"}}'.format(plan_id))
    ext_config = None
    for plan in allPlans:
        ext_config = json.loads(plan['ext_config'])
    if ext_config is None:
        return HttpResponse({"r": "2", "e": "获取网卡信息失败。"}, ensure_ascii=False)

    # switchtype 1 手工切换 2 主备自动切换
    result["switchtype"] = ext_config['switchtype']
    result["arbitrate_ip"] = ext_config['arbitrate_ip']
    master_adpter = ext_config['master_adpter']
    standby_adpter = ext_config['standby_adpter']
    result["master"] = dict()
    result["standby"] = dict()
    result["master"]["control"] = list()
    result["master"]["business"] = list()
    result["standby"]["control"] = list()
    result["standby"]["business"] = list()

    master_control = master_adpter.get("control", list())
    for control in master_control:
        ips = control["ips"]
        for ip_mask in ips:
            ip = ip_mask["ip"]
            result["master"]["control"].append({"ip": ip, 'r': _test_host_is_ok(ip)})

    master_business = master_adpter.get("business", list())
    for business in master_business:
        ips = business["ips"]
        for ip_mask in ips:
            ip = ip_mask["ip"]
            result["master"]["business"].append({"ip": ip, 'r': _test_host_is_ok(ip)})

    standby_control = standby_adpter.get("control", list())
    for control in standby_control:
        ips = control["ips"]
        for ip_mask in ips:
            ip = ip_mask["ip"]
            result["standby"]["control"].append({"ip": ip, 'r': _test_host_is_ok(ip)})

    standby_business = standby_adpter.get("business", list())
    for business in standby_business:
        ips = business["ips"]
        for ip_mask in ips:
            ip = ip_mask["ip"]
            result["standby"]["business"].append({"ip": ip, 'r': _test_host_is_ok(ip)})

    json_str = json.dumps(result, ensure_ascii=False)
    return HttpResponse(json_str)


def get_plan_detail(request):
    plan_id = request.GET['plan_id']
    data = dict()
    result = {'r': 0, 'e': '操作成功', 'data': data}
    api_request = {'id': plan_id}
    allPlans = HTBScheduleCreate().get(request=request, api_request=api_request).data
    if not allPlans:
        request['r'] = 1
        request['e'] = '获取计划信息失败'
    else:
        plan = allPlans[0]
        exc_info = json.loads(plan['ext_config'])
        data['htb_task_name'] = plan['name']
        if plan['task_type'] == HTBSchedule.OLD_POINT_NOT_NEED_UPDATE:
            data['htb_task_task_type'] = '同步到固定时间点' + '({})'.format(exc_info.get('restore_time', ''))
        else:
            data['htb_task_task_type'] = '同步到最新时间点'
        data['htb_task_switch_type'] = '手动切换' if exc_info['switchtype'] == 1 else '自动切换'
        data['htb_task_restore_type'] = '系统还原' if plan['restore_type'] == HTBSchedule.HTB_RESTORE_TYPE_SYSTEM else '卷还原'
        data['htb_task_src_ip'], data['htb_task_dest_ip'] = _get_ip_info(exc_info['master_adpter']), _get_ip_info(
            exc_info['standby_adpter'])
        data['htb_task_contain_vols'] = _get_vol_info(exc_info.get('vol_maps', '[]'), exc_info.get('index_list', '[]'))
        data['htb_task_start_script'] = os.path.splitext(exc_info['start_script_zip_path'])[1] if exc_info[
            'start_script_zip_path'] else '无'
        data['htb_task_stop_script'] = os.path.splitext(exc_info['stop_script_zip_path'])[1] if exc_info[
            'stop_script_zip_path'] else '无'
        data['switch_change_master_ip'] = '启用' if exc_info.get('switch_change_master_ip', '1') == '1' else '禁用'

    return HttpResponse(json.dumps(result, ensure_ascii=False))


# 演练模式，没有主网卡信息
@xlogging.convert_exception_to_value('--')
def _get_ip_info(adapter_info):
    content_format = '固有IP:{}<br>漂移IP:{}<br>网关:{}<br>DNS:{}'
    gate_way = adapter_info['gateway'][0]
    dns = ' | '.join(adapter_info['dns'])
    control_ip = ' | '.join(['{}/{}'.format(ips['ip'], ips['mask']) for per_nic in adapter_info['control'] for ips in
                             per_nic['ips']])
    business_ip = ' | '.join(['{}/{}'.format(ips['ip'], ips['mask']) for per_nic in adapter_info['business'] for ips in
                              per_nic['ips']])

    return content_format.format(control_ip, business_ip, gate_way, dns)


def _get_vol_info(vol_maps_str, index_list_str):
    vol_maps = json.loads(vol_maps_str)
    index_list = json.loads(index_list_str)
    rs = list()
    for src_index, dest_index in enumerate(index_list):
        if dest_index is None:
            continue
        rs.append(vol_maps[src_index]['display_name'])
    return '<br>'.join(rs) if rs else '无'


# 获取某一天的数据
def get_point_list(request):
    result = {"r": "0", "e": "操作成功", "list": []}
    host_ident = request.GET.get('host_ident')
    st_date = request.GET.get('st_date', '-1')
    checkident = request.GET.get('checkident', '0')

    if not host_ident:
        return HttpResponse('{"r": "1","e": "host_ident is None. Failed."}')

    if checkident == '1':
        plan_name = _srcHost_in_plan(request, host_ident)
        if plan_name:
            error_msg = '一台客户端只能创建一个热备计划，该客户端已存在热备计划：{}。'.format(plan_name)
            return HttpResponse(json.dumps({'r': 2, 'e': error_msg}, ensure_ascii=False))

    if st_date == '-1':
        start_date = _get_host_latest_start_datetime(host_ident)
    else:
        start_date = datetime.strptime(st_date, xdatetime.FORMAT_ONLY_DATE)

    end_date = start_date.date() + timedelta(days=1)
    api_request = {'begin': start_date.strftime(xdatetime.FORMAT_ONLY_DATE),
                   'end': end_date.strftime(xdatetime.FORMAT_ONLY_DATE),
                   'finish': True,
                   'use_serializer': False}

    api_response = HostSnapshotsWithNormalPerHost().get(request=request, ident=host_ident, api_request=api_request)
    if not status.is_success(api_response.status_code):
        e = get_response_error_string(api_response)
        debug = "HostSnapshotsWithNormalPerHost().get(begin:{} end:{} ident:{}) failed {}".format(
            start_date, end_date, host_ident, api_response.status_code)
        return HttpResponse(json.dumps({"r": 1, "e": e, "debug": debug}, ensure_ascii=False))

    for host_snapshot in api_response.data:
        backup_point = {
            "id": '{}|{}|{}'.format(xdata.SNAPSHOT_TYPE_NORMAL, host_snapshot['id'], host_snapshot['start_datetime']),
            "time": host_snapshot['start_datetime'], "type": xdata.SNAPSHOT_TYPE_NORMAL,
            "enddate": host_snapshot['start_datetime'], "recommend": False}
        result["list"].append(backup_point)

    api_response = HostSnapshotsWithCdpPerHost().get(None, ident=host_ident, api_request=api_request)
    if not status.is_success(api_response.status_code):
        e = get_response_error_string(api_response)
        debug = "HostSnapshotsWithCdpPerHost().get(begin:{} end:{} ident:{}) failed {}".format(start_date, end_date,
                                                                                               host_ident,
                                                                                               api_response.status_code)
        return HttpResponse(json.dumps({"r": 1, "e": e, "debug": debug}, ensure_ascii=False))

    for host_snapshot in api_response.data:
        backup_point = {"id": '{}|{}|{}|{}'.format(xdata.SNAPSHOT_TYPE_CDP, host_snapshot['id'], host_snapshot['begin'],
                                                   host_snapshot['end']),
                        "time": '{} - {}'.format(host_snapshot['begin'], host_snapshot['end']),
                        "type": xdata.SNAPSHOT_TYPE_CDP, "enddate": host_snapshot['end'], "recommend": False}
        result["list"].append(backup_point)

    result["list"].sort(key=lambda x: (xdatetime.string2datetime(x['enddate'])), reverse=True)
    result['st_date'] = api_request['begin']
    result['is_windows'] = host_is_windows(host_ident)

    json_str = json.dumps(result, ensure_ascii=False)
    return HttpResponse(json_str)


def _get_host_latest_start_datetime(host_ident):
    host_snapshots = HostSnapshot.objects.filter(host__ident=host_ident,
                                                 deleted=False,
                                                 deleting=False,
                                                 partial=False,
                                                 successful=True).order_by('-start_datetime')
    for host_snapshot in host_snapshots:
        if host_snapshot.is_cdp:
            start_date = host_snapshot.cdp_info.last_datetime
        else:
            start_date = host_snapshot.start_datetime

        if start_date:
            return start_date
    else:
        return datetime.now() - timedelta(days=1)


def standby_restart(request):
    paramsQD = request.GET
    plan_ids = paramsQD.get('ids', '0')

    api_response = HTBScheduleExecute().post(plan_ids.split(',')[0], True)
    if not status.is_success(api_response.status_code):
        return HttpResponse(json.dumps({"r": 1, "e": api_response.data}, ensure_ascii=False))
    return HttpResponse(json.dumps({"r": 0, "e": '操作成功'}, ensure_ascii=False))
