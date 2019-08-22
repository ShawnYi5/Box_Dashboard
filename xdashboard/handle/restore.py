# coding=utf-8
import copy
import html
import json
import os
import random
import time
from collections import defaultdict
from datetime import datetime, timedelta

import django.utils.timezone as timezone
from django.http import HttpResponse
from rest_framework import status
from xdashboard.common.msg import notify_audits

from apiv1.models import (HostSnapshot, RestoreTarget, BackupTaskSchedule, RestoreTask,
                          HostSnapshotShare, Host, HostGroup)
from apiv1.tasks import get_systeminfo_for_proxy_agent
from apiv1.views import (HostSnapshotRestore, HostSnapshotsWithNormalPerHost, HostSnapshotsWithCdpPerHost,
                         PeHostSessionInfo, HostSnapshotInfo, HostSnapshotShareAdd, HostSnapshotShareDelete,
                         HostSnapshotShareQuery, TargetHardware, GetDriversVersions, DiskVolMap, GetOneDiskVols,
                         check_driver_in_blacklist, get_response_error_string, HostSessionDisks, HostSessions)
from box_dashboard import boxService
from box_dashboard import xlogging, xdatetime, xdata
from xdashboard.common.dict import SaveDictionary, GetDictionary
from xdashboard.common.functional import hasFunctional
from xdashboard.handle.authorize import authorize_init
from xdashboard.handle.logserver import SaveOperationLog
from xdashboard.handle.migrate import getAdapterSettings as migrate_getAdapterSettings
from xdashboard.handle.migrate import getDestServerList, re_generate_adapters_params, backup, getDestServerListYun
from xdashboard.handle.migrate import query_pe_disks_sn_and_add_to_retinfo
from xdashboard.handle.sysSetting import cdpFileIO
from xdashboard.handle.user import getUniqueSambaUsername
from xdashboard.handle.user import have_audit_admin
from xdashboard.handle.version import upload as ver_pload
from xdashboard.models import DataDictionary
from xdashboard.models import ForceInstallDriver
from xdashboard.models import OperationLog, audit_task
from xdashboard.request_util import get_operator
from apiv1.cdp_wrapper import get_cluster_io_daychart
from apiv1.cdp_wrapper import fix_restore_time

_logger = xlogging.getLogger(__name__)


def _is_in_group(group_list, id):
    for group in group_list:
        if group['id'] == id:
            return True
    return False


# 获取所有主机信息，这些主机都含有可以使用的备份点
def getServerList(request):
    group_ui = request.GET.get('group')

    """
    下面的逻辑为了筛选出，含有可使用备份点的主机
    对于 普通agent 和 免代理备份，需要支持边备份边浏览
    对于其它的主机 那么一定是需要完成的，且是成功的
    """

    host_snapshots = HostSnapshot.objects.filter(host__user=request.user,
                                                 start_datetime__isnull=False,
                                                 deleted=False, deleting=False
                                                 )
    host_snapshots = host_snapshots.exclude(partial=True, finish_datetime__isnull=False)  # 排除完成的且是 不完整的
    host_snapshots = host_snapshots.exclude(finish_datetime__isnull=False, successful=False)  # 排除完成的 且是失败的

    use_able_idents = host_snapshots.values_list('host__ident', flat=True).distinct()  # 支持边浏览 边备份
    use_able_idents_completed = host_snapshots.filter(finish_datetime__isnull=False).values_list('host__ident',
                                                                                                 flat=True).distinct()  # 成功完成的主机

    def not_exists_snapshot(host):
        if host.type in (Host.AGENT, Host.PROXY_AGENT):  # 支持边备份边使用的客户端
            return host.ident not in use_able_idents
        else:
            return host.ident not in use_able_idents_completed

    filter_funcs = [HostSessions.filter_deleted,
                    not_exists_snapshot]
    attr_getters = [('name1', backup.get_host_name)]
    hosts = HostSessions().get(request=request, filter_funcs=filter_funcs,
                               attr_getters=attr_getters).data

    result = list()
    group_list = list()
    ident2groups = defaultdict(set)
    if group_ui == 'group':
        for group in HostGroup.objects.all():
            for h in group.hosts.all():
                ident2groups[h.ident].add((group.id, group.name))
    # 没有备份点主机的总数
    for host in hosts:
        if host['is_nas_host']:
            type = Host.NAS_AGENT
        else:
            type = host['type']
        one_host = {'name': host['name1'], 'id': host['ident'], 'mydefault': '0', 'group_id': None,
                    'type': type}
        if group_ui == 'group' and ident2groups[host['ident']]:
            for group_info in ident2groups[host['ident']]:
                one_host['group_id'] = group_info[0]
                result.append(copy.deepcopy(one_host))
                if not _is_in_group(group_list, group_info[0]):
                    group_list.append({'id': group_info[0], 'name': group_info[1]})
        else:
            result.append(one_host)
    if result:
        retInfo = {'r': '0', 'e': '操作成功', 'list': result, 'group_list': group_list}
        return HttpResponse(json.dumps(retInfo, ensure_ascii=False))
    else:
        return HttpResponse('{"r": "1","e": "当前用户没有可用的还原点，请先分配主机并对该主机进行备份。"}')


def _get_user_mount_tasks_ids(request):
    api_response = HostSnapshotShareQuery().get(request)
    if not api_response:
        return []
    if not status.is_success(api_response.status_code):
        return []

    user_mounts = api_response.data
    if not isinstance(user_mounts, list):
        return []

    return [mount['id'] for mount in user_mounts if mount.get('id', None)]


def _get_point_mount_task_id(user_mounts_ids, snapshot_id):
    """获取备份点对应的挂载任务ID"""
    if user_mounts_ids is None:
        return None

    mount_task = HostSnapshotShare.objects.filter(host_snapshot_id=snapshot_id).order_by('id').last()
    if not mount_task:
        return None

    if mount_task.id in user_mounts_ids:
        return mount_task.id

    _logger.error('_get_point_mount_task_id: mount_task is invalid, mount_task={}, user_mounts_ids={}'.
                  format(mount_task.id, user_mounts_ids))
    return None


# 获取主机在指定时间范围内的还原点
def getRestorePoint(request):
    query_params = request.GET
    focus_date = None
    host_ident = query_params.get('serverid', default='')
    start_date = query_params.get('starttime', default='')
    query_point_mount = query_params.get('query_point_mount', 'no') == 'yes'  # 查询备份点对应的`挂载任务`
    only_show_finished = query_params.get('only_show_finished', 'no') == 'yes'  # 仅显示`已完成`的备份点
    if not host_ident or not start_date:
        return HttpResponse('{"r": "1","e": "请求参数缺失：serverid/starttime"}')

    start_date = xdatetime.string2datetime(start_date)
    end_date = (xdatetime.string2datetime(query_params.get('endtime')) if (
            'endtime' in query_params) else datetime.now().date()) + timedelta(days=1)

    result = {
        "id": host_ident,
        "title": "服务器名",
        "focus_date": end_date.strftime(xdatetime.FORMAT_ONLY_DATE),
        "initial_zoom": "14",
        "image_lane_height": 1,
        "events": [],
        "tags": {"mardigras": 2, "chris": 2, "arizona": 2, "netscape": 2, "flop": 1},
        "legend": [
            {"title": "libs &amp; frameworks", "icon": "triangle_orange.png"},
            {"title": "engines", "icon": "square_gray.png"},
            {"title": "browsers", "icon": "triangle_yellow.png"},
            {"title": "JS evolution", "icon": "triangle_green.png"},
            {"title": "languages", "icon": "circle_green.png"},
            {"title": "standards", "icon": "square_blue.png"},
            {"title": "conferences", "icon": "circle_blue.png"},
            {"title": "milestones", "icon": "circle_purple.png"}
        ]
    }

    api_request = {'begin': start_date.strftime(xdatetime.FORMAT_ONLY_DATE),
                   'end': end_date.strftime(xdatetime.FORMAT_ONLY_DATE),
                   'use_serializer': False}
    if only_show_finished:
        api_request['finish'] = True

    api_response = HostSnapshotsWithNormalPerHost().get(request=request, ident=host_ident, api_request=api_request)
    if not status.is_success(api_response.status_code):
        e = get_response_error_string(api_response)
        debug = "HostSnapshotsWithNormalPerHost().get(begin:{} end:{} ident:{}) failed {}".format(
            start_date, end_date, host_ident, api_response.status_code)
        return HttpResponse(json.dumps({"r": 1, "e": e, "debug": debug}, ensure_ascii=False))

    host = Host.objects.get(ident=host_ident)
    if json.loads(host.ext_info).get('nas_path'):
        point_type = 'NAS备份'
    else:
        point_type = '整机备份'

    user_mounts_ids = _get_user_mount_tasks_ids(request) if query_point_mount else None  # [] or None
    for host_snapshot in api_response.data:
        result['events'].append({
            "id": '{}|{}|{}'.format(xdata.SNAPSHOT_TYPE_NORMAL, host_snapshot['id'], host_snapshot['start_datetime']),
            "title": '{} {}'.format(point_type, host_snapshot['start_datetime']),
            "startdate": host_snapshot['start_datetime'],
            "enddate": host_snapshot['start_datetime'],
            "high_threshold": 50,
            "importance": "50",
            "date_display": "hour",
            "mounted": _get_point_mount_task_id(user_mounts_ids, host_snapshot['id']),  # id or None
            "icon": "circle_green.png"})
        if focus_date is None:
            focus_date = xdatetime.string2datetime(host_snapshot['start_datetime']).strftime(xdatetime.FORMAT_ONLY_DATE)
        else:
            tmp = xdatetime.string2datetime(host_snapshot['start_datetime']).strftime(xdatetime.FORMAT_ONLY_DATE)
            tmpdata1 = xdatetime.string2datetime(tmp)
            tmpdata2 = xdatetime.string2datetime(focus_date)
            if tmpdata1 > tmpdata2:
                focus_date = tmp

    api_response = HostSnapshotsWithCdpPerHost().get(None, ident=host_ident, api_request=api_request)
    if not status.is_success(api_response.status_code):
        e = get_response_error_string(api_response)
        debug = "HostSnapshotsWithCdpPerHost().get(begin:{} end:{} ident:{}) failed {}".format(start_date, end_date,
                                                                                               host_ident,
                                                                                               api_response.status_code)
        return HttpResponse(json.dumps({"r": 1, "e": e, "debug": debug}, ensure_ascii=False))

    for host_snapshot in api_response.data:
        result['events'].append({
            "id": '{}|{}|{}|{}'.format(xdata.SNAPSHOT_TYPE_CDP, host_snapshot['id'], host_snapshot['begin'],
                                       host_snapshot['end']),
            "title": 'CDP备份 {}'.format(host_snapshot['begin']),
            "startdate": host_snapshot['begin'],
            "enddate": host_snapshot['end'],
            "high_threshold": 50,
            "importance": "50",
            "date_display": "hour",
            "icon": "square_black.png"})
        if focus_date is None:
            focus_date = xdatetime.string2datetime(host_snapshot['begin']).strftime(xdatetime.FORMAT_ONLY_DATE)
        else:
            tmp = xdatetime.string2datetime(host_snapshot['begin']).strftime(xdatetime.FORMAT_ONLY_DATE)
            tmpdata1 = xdatetime.string2datetime(tmp)
            tmpdata2 = xdatetime.string2datetime(focus_date)
            if tmpdata1 > tmpdata2:
                focus_date = tmp

    if focus_date:
        result['focus_date'] = focus_date

    # result['events'].sort(key=lambda x: (xdatetime.string2datetime(x['startdate']))) 无需排序

    return HttpResponse(json.dumps([result]))


# 主机磁盘使用情况
def host_disks_info(system_infos, calc_used):
    host_disks = list()
    for disk in system_infos['Disk']:
        host_disks.append(HostSessionDisks.disk_label_for_human(disk, calc_used))

    return host_disks


# 主机是否存在(过)Cdp计划
def is_host_cdp_plan_existed(host_id):
    cdp_plans = BackupTaskSchedule.objects.filter(host_id=host_id, cycle_type=BackupTaskSchedule.CYCLE_CDP)
    return True if cdp_plans else False


def is_linux_host(system_infos):
    return 'LINUX' in system_infos['System']['SystemCaption'].upper()


# 得到备份点详细信息
def getPointDetail(request):
    query_params = request.GET
    if 'pointid' not in query_params:
        return HttpResponse('{"r": "1","e": "pointid is not exist"}')

    request_params = request.GET['pointid']
    params = request_params.split('|')

    if len(params) < 3:
        return HttpResponse('{{"r": "1","e": "pointid invalid : {}"}}'.format(request_params))

    result_data = dict()

    if params[0] == xdata.SNAPSHOT_TYPE_NORMAL:
        host_snapshot_id = params[1]
        start_datetime = datetime.strptime(params[2], '%Y-%m-%dT%H:%M:%S.%f').strftime('%Y-%m-%d %H:%M:%S')
        snapshot_obj = HostSnapshot.objects.get(id=host_snapshot_id)

        host_name = snapshot_obj.host.display_name
        system_infos = json.loads(snapshot_obj.ext_info)['system_infos']
        plan_obj = snapshot_obj.schedule
        plan_name = plan_obj.name if plan_obj else ''

        system_caption = '{} {}(版本号:{})'.format(system_infos['System']['SystemCaption'],
                                                system_infos['System']['ServicePack'],
                                                system_infos['System'].get('BuildNumber', 'Unknown version'))
        existed_cdp_plan = is_host_cdp_plan_existed(snapshot_obj.host.id)
        if existed_cdp_plan or is_linux_host(system_infos):
            calc_used = False
        else:
            calc_used = True
        backuptype = '整机备份'
        type = snapshot_obj.host.type
        if json.loads(snapshot_obj.host.ext_info).get('nas_path'):
            type = Host.NAS_AGENT
            backuptype = 'NAS备份'
        result_data = {
            "r": "0",
            "e": "操作成功",
            "srcserver": host_name,
            "taskname": plan_name,
            "backuptime": start_datetime,
            "backuptype": backuptype,
            "iscdp": "0",
            "size": '<br>'.join(host_disks_info(system_infos, calc_used)),
            'system': system_caption,
            'type': type
        }
    elif params[0] == xdata.SNAPSHOT_TYPE_CDP:
        host_snapshot_id = params[1]
        start_datetime = datetime.strptime(params[2], '%Y-%m-%dT%H:%M:%S.%f').strftime('%Y-%m-%d %H:%M:%S.%f')
        end_datetime = datetime.strptime(params[3], '%Y-%m-%dT%H:%M:%S.%f').strftime('%Y-%m-%d %H:%M:%S.%f')
        snapshot_obj = HostSnapshot.objects.get(id=host_snapshot_id)

        host_name = snapshot_obj.host.display_name
        system_infos = json.loads(snapshot_obj.ext_info)['system_infos']
        plan_obj = snapshot_obj.schedule
        plan_name = plan_obj.name if plan_obj else ''

        system_caption = '{} {}(版本号:{})'.format(system_infos['System']['SystemCaption'],
                                                system_infos['System']['ServicePack'],
                                                system_infos['System'].get('BuildNumber', 'Unknown version'))
        result_data = {
            "r": "0",
            "e": "操作成功",
            "srcserver": host_name,
            "taskname": plan_name,
            "backuptype": "整机备份",
            "iscdp": "1",
            "cdpbackupstarttime": start_datetime,
            "cdpbackupendtime": end_datetime,
            "size": '<br>'.join(host_disks_info(system_infos, False)),
            'system': system_caption
        }

    result_data['taskname'] = get_plan_name_by_snapshot_obj(HostSnapshot.objects.get(id=params[1]))
    return HttpResponse(json.dumps(result_data, ensure_ascii=False))


# 获取目的服务器列表
def getRestoreServerList(request):
    return getDestServerList(request)


# 获取目的服务器列表yun
def getRestoreServerListYun(request):
    return getDestServerListYun(request)


# 获取设置网卡信息
def getAdapterSettings(request):
    return migrate_getAdapterSettings(request)


def check_authorize_at_restore():
    restore_tasks = RestoreTask.objects.filter(finish_datetime__isnull=True)

    json_txt = authorize_init.get_authorize_plaintext_from_local()
    if json_txt is None:
        return False, '读取授权文件异常'
    val = authorize_init.get_license_val_by_guid('restore_task_concurrent', json_txt)
    if val is None:
        return False, '读取授权文件异常'
    if len(restore_tasks) >= int(val):
        return False, '同时还原任务数超过授权允许值({0}个), 目前{1}个任务正在进行中'.format(val, len(restore_tasks))

    return True, ''


def _get_pe_firmware(ident):
    try:
        arg = {'type': 'read_efi'}
        _, raw_data = boxService.box_service.PEJsonFunc(ident, json.dumps(arg))
        # _, raw_data = boxService.box_service.JsonFuncV2(ident, json.dumps(arg), b'')
    except Exception as e:
        _logger.error('_get_pe_firmware Failed.ignore. ident:{}, e:{}'.format(ident, e))
        return 'MBR'
    return 'GPT'


# 获取设置磁盘信息
def getHardDiskSettings(request):
    disk_type = request.GET.get('disk_type', 0)
    if authorize_init.is_evaluation_and_expiration():
        return HttpResponse(json.dumps({"r": "1", "e": "试用版已过期", "list": []}))

    result = check_authorize_at_restore()
    if not result[0]:
        return HttpResponse(json.dumps({"r": "1", "e": result[1], "list": []}))

    query_params = request.GET

    if 'pointid' not in query_params:
        return HttpResponse('{"r": "1","e": "pointid is not exist"}')
    if 'destserverid' not in query_params:
        return HttpResponse('{"r": "1","e": "destserverid is not exist"}')

    request_params = query_params['pointid']
    params = request_params.split('|')
    host_snapshot_id = params[1]
    pe_host_ident = request.GET['destserverid']
    result = {"r": "0", "e": "操作成功", "replace_efi": False, "srclist": [], "destlist": []}

    # 源信息
    api_response = HostSnapshotInfo().get(request=request, host_snapshot_id=host_snapshot_id)
    if not status.is_success(api_response.status_code):
        e = get_response_error_string(api_response)
        debug = "HostSnapshotInfo().get() {} failed {}".format(host_snapshot_id, api_response.status_code)
        return HttpResponse(json.dumps({"r": 1, "e": e, "debug": debug}, ensure_ascii=False))

    exe_info_dict = json.loads(HostSnapshot.objects.get(id=host_snapshot_id).ext_info)
    if disk_type:
        src_disk_type = exe_info_dict.get('efi_boot_entry', '')
        if src_disk_type == '':
            src_disk_type = 'MBR'
        else:
            src_disk_type = 'GPT'
    _boot_disk_is_gpt = False
    type_describe = ''
    for disk_snapshot in api_response.data['disk_snapshots']:
        if len(list(filter(lambda x: x['id'] == disk_snapshot['disk']['ident'], result["srclist"]))) == 0:
            rs_dict = dict()
            if disk_snapshot['type'] == 2 and disk_snapshot['boot_device']:  # 该磁盘是引导盘, 且是GPT
                _boot_disk_is_gpt = True
                type_describe = '源引导盘是GPT分区,若目标引导盘是MBR分区,请在还原后,调整引导模式为UEFI'
            rs_dict['id'] = disk_snapshot['disk']['ident']
            rs_dict['name'] = disk_snapshot['display_name']
            rs_dict['bytes'] = disk_snapshot['bytes']
            rs_dict['startdate'] = api_response.data['start_datetime']
            rs_dict['bootable'] = disk_snapshot['boot_device']
            rs_dict['vols'] = _get_vols(exe_info_dict, disk_snapshot['disk']['ident'])
            rs_dict['disk_type'] = disk_snapshot['type']
            rs_dict['type_describe'] = type_describe
            result["srclist"].append(rs_dict)

    # append 科力锐启动盘
    _is_linux = 'LINUX' in exe_info_dict['system_infos']['System']['SystemCaption'].upper()
    if _is_linux:
        result["srclist"].append({
            'id': xdata.CLW_BOOT_REDIRECT_MBR_UUID,
            'name': '科力锐系统加载盘（MBR版本）',
            'bytes': 5 * (1024 ** 3),
            'startdata': '',
            'bootable': 1,
            'vols': dict(include_vols=list(), exclude_vols=list()),
            'disk_type': '',
            'clw_disk': 'clw_disk'
        })

        if _boot_disk_is_gpt:
            result["srclist"].append({
                'id': xdata.CLW_BOOT_REDIRECT_GPT_LINUX_UUID,
                'name': '科力锐系统加载盘（Linux GPT）',
                'bytes': 5 * (1024 ** 3),
                'startdata': '',
                'bootable': 1,
                'vols': dict(include_vols=list(), exclude_vols=list()),
                'disk_type': '',
                'clw_disk': 'clw_disk'
            })

    else:  # windows
        if _boot_disk_is_gpt:
            result["srclist"].append({
                'id': xdata.CLW_BOOT_REDIRECT_GPT_UUID,
                'name': '科力锐系统加载盘（GPT版本）',
                'bytes': 5 * (1024 ** 3),
                'startdata': '',
                'bootable': 1,
                'vols': dict(include_vols=list(), exclude_vols=list()),
                'disk_type': '',
                'clw_disk': 'clw_disk'
            })

            if src_disk_type == 'GPT':
                system_infos = exe_info_dict['system_infos']
                BuildNumber = system_infos['System'].get('BuildNumber', 'Unknown version')
                try:
                    if BuildNumber == 'Unknown version' or int(BuildNumber) <= 7601:
                        result['replace_efi'] = True
                except Exception as e:
                    _logger.info('getHardDiskSettings BuildNumber Failed replace_efi.ignore.e={}'.format(e))
                    result['replace_efi'] = True
        else:
            result["srclist"].append({
                'id': xdata.CLW_BOOT_REDIRECT_MBR_UUID,
                'name': '科力锐系统加载盘（MBR版本）',
                'bytes': 5 * (1024 ** 3),
                'startdata': '',
                'bootable': 1,
                'vols': dict(include_vols=list(), exclude_vols=list()),
                'disk_type': '',
                'clw_disk': 'clw_disk'
            })

    # Pe信息
    api_response = PeHostSessionInfo().get(request=request, ident=pe_host_ident)
    if not status.is_success(api_response.status_code):
        rs_dict = get_wrong_rsp(api_response, '网络异常，获取信息失败。code:{}'.format(api_response.status_code))
        return HttpResponse(json.dumps(rs_dict, ensure_ascii=False))
    for disk in api_response.data['disks']:
        boot_name = '磁盘:{0}_{1:.2f}'.format(disk['disk_id'], disk['disk_bytes'] / (1024 * 1024 * 1024)) + 'G'
        result["destlist"].append(
            {'id': disk['disk_id'],
             'name': boot_name + '(引导盘)' if disk['is_boot_device'] else boot_name,
             'bytes': disk['disk_bytes'],
             'bootable': 1 if disk['is_boot_device'] else 0})

    query_pe_disks_sn_and_add_to_retinfo(result, pe_host_ident)

    if disk_type:
        dest_disk_type = _get_pe_firmware(pe_host_ident)
        if src_disk_type != dest_disk_type:
            type_describe = ' 原计算机是{src_disk_type}模式。请还原重启后修改BIOS的启动方式。改为跟原机一样的{src_disk_type}模式。'.format(
                src_disk_type=src_disk_type)
        result['disk_type'] = {'src': src_disk_type, 'dest': dest_disk_type, 'type_describe': type_describe}
    return HttpResponse(json.dumps(result, ensure_ascii=False))


def _get_vols(snapshot_info, disk_ident):
    rsp = GetOneDiskVols.get(snapshot_info, disk_ident)
    if not status.is_success(rsp.status_code):
        return []
    else:
        return rsp.data


def is_restore_to_self(ipconfig_infos):
    for ui_nic_cfg in ipconfig_infos:
        if all([ui_nic_cfg['is_set'], ui_nic_cfg['is_to_self']]):
            return True

    return False


def _update_master_mic(ipconfig_infos):
    for ip_config in ipconfig_infos:
        ip_config['is_to_self'] = False


# 普通还原流程, 开始执行还原
def startRestore(request):
    query_params = request.POST if request.POST else request.GET  # POST数据优先
    change_disk = query_params.get('change_disk', '0')  # 分区扩大、缩小或改变位置

    if 'pointid' not in query_params:
        return HttpResponse('{"r": "1","e": "pointid is not exist"}')
    if 'destserverid' not in query_params:
        return HttpResponse('{"r": "1","e": "destserverid is not exist"}')
    if 'adapters' not in query_params:
        return HttpResponse('{"r": "1","e": "adapters is not exist"}')
    if 'drivers_ids' not in query_params:
        return HttpResponse('{"r": "1","e": "drivers_ids is not exist"}')
    if 'is_valiade' not in query_params:
        return HttpResponse('{"r": "1","e": "is_valiade is not exist"}')
    if 'routers' not in query_params:
        return HttpResponse('{"r": "1","e": "routers is not exist"}')
    if change_disk == '0':
        if 'ex_vols' not in query_params:
            return HttpResponse('{"r": "1","e": "ex_vols is not exist"}')
        if 'disks' not in query_params:
            return HttpResponse('{"r": "1","e": "disks is not exist"}')
    if 'is_same' not in query_params:
        return HttpResponse('{"r": "1","e": "is_same is not exist"}')
    if 'is_restore_to_self' not in query_params:
        return HttpResponse('{"r": "1","e": "is_restore_to_self is not exist"}')

    request_params = query_params['pointid']
    point_params = request_params.split('|')
    point_type = point_params[0]
    host_snapshot_id = point_params[1]
    pe_host_ident = query_params['destserverid']
    ipconfig_infos = json.loads(query_params['adapters'])
    drivers_ids = query_params['drivers_ids']  # 'device_id|driver_index|driver_id,..' 选择驱动的情况
    drivers_ids_force = query_params.get('drivers_ids_force',
                                         '')  # 'device_id|driver_index|driver_id,..' 选择需要强制安装驱动的情况
    drivers_type = query_params['drivers_type']  # '1': 模式一, '2':模式二
    is_valiade_host = query_params['is_valiade']
    routers = query_params['routers']
    enablekvm = query_params.get('enablekvm', '0')
    remote_kvm_params = dict()
    remote_kvm_params['enablekvm'] = str(enablekvm)
    is_same = query_params['is_same']  # 是否是同构的还原
    is_restore_to_self_ui = query_params['is_restore_to_self']  # 备份点中的mac和主网卡的mac一致
    disk_params = query_params.get('disks')  # change_disk等于0时有效
    ex_vols = query_params.get('ex_vols')  # change_disk等于0时有效

    clret = authorize_init.check_host_rebuild_count(pe_host_ident)
    if clret.get('r', 0) != 0:
        return HttpResponse(json.dumps(clret, ensure_ascii=False))

    if change_disk == '1':
        diskpartition_info = json.loads(query_params.get('diskpartition_info'))
        return HttpResponse(json.dumps(diskpartition_info, ensure_ascii=False))

    if enablekvm == '0':
        remote_kvm_params['ssh_ip'] = ''
        remote_kvm_params['ssh_port'] = ''
        remote_kvm_params['ssh_key'] = ''
        remote_kvm_params['aio_ip'] = ''
        remote_kvm_params['ssh_path'] = ''
        remote_kvm_params['ssh_os_type'] = ''
    else:
        remote_kvm_params['ssh_ip'] = str(query_params['ssh_ip'])
        remote_kvm_params['ssh_port'] = str(query_params['ssh_port'])
        remote_kvm_params['ssh_key'] = str(query_params['ssh_key'])
        remote_kvm_params['aio_ip'] = str(query_params['aio_ip'])
        remote_kvm_params['ssh_path'] = os.path.join(str(query_params['ssh_path']),
                                                     '{}_{}'.format(host_snapshot_id, time.time()))
        remote_kvm_params['ssh_os_type'] = str(query_params['ssh_os_type'])

    # 同构且本机还原
    if int(is_same) and int(is_restore_to_self_ui):
        need_install_driver = False  # 不需要装驱动
        if is_restore_to_self(ipconfig_infos):
            use_src_instance_id = True
        else:
            use_src_instance_id = False
    else:
        need_install_driver = True  # 需要装驱动
        use_src_instance_id = False

    if not use_src_instance_id:  # 不能使用老的instance id
        _logger.info('startRestore need set is_to_self to false!')
        _update_master_mic(ipconfig_infos)  # 将 is_to_self 置为 Fasle
    else:
        pass

    if need_install_driver:
        restore_to_self = False
    else:
        restore_to_self = True  # 智能模式下 不装驱动

    _logger.info('startRestore is_same:{}, is_restore_to_self_ui:{}, use_src_instance_id:{}'.format(is_same,
                                                                                                    is_restore_to_self_ui,
                                                                                                    use_src_instance_id))

    try:
        drivers_list_str = GetDriversVersions.get_drivers_list_str(pe_host_ident, drivers_ids, host_snapshot_id,
                                                                   drivers_type, restore_to_self=restore_to_self,
                                                                   user_id=request.user.id,
                                                                   driver_ids_force=drivers_ids_force)
        drivers_list_str = drivers_list_str.replace('\\', '|')
    except Exception as e:
        _logger.error('startRestore fail {}'.format(e), exc_info=True)
        return HttpResponse(json.dumps({"r": "1", "e": "操作失败"}, ensure_ascii=False))

    try:
        host_snapshot = HostSnapshot.objects.get(id=host_snapshot_id)
    except HostSnapshot.DoesNotExist:
        _logger.error('startRestore fail, not found host_snapshot:{}'.format(host_snapshot_id), exc_info=True)
        return HttpResponse(json.dumps({"r": "1", "e": "获取快照失败"}, ensure_ascii=False))

    restore_node_time = host_snapshot.start_datetime.strftime('%Y-%m-%d %H:%M:%S.%f')

    if int(is_valiade_host):  # 验证还原
        userid = '-1'
        username = xdata.UUID_VALIADE_HOST
        agent_user_info = '{}|{}'.format(userid, username)
    else:
        user = host_snapshot.host.user
        userid, user_fingerprint = user.id, user.userprofile.user_fingerprint
        agent_user_info = '{}|*{}'.format(userid, user_fingerprint)

    api_request_data = r'"type": "{point_type}", "pe_host_ident": "{pe_host_ident}", "adapters": {adapter_params},' \
                       ' "disks": {disk_params}, "drivers_ids": {drivers_ids}, "agent_user_info": "{agent_user_info}",' \
                       '"routers":{routers}' \
        .format(point_type=point_type, pe_host_ident=pe_host_ident, adapter_params='[]',
                disk_params=disk_params, drivers_ids=drivers_list_str, agent_user_info=agent_user_info,
                routers=routers)

    if point_type == xdata.SNAPSHOT_TYPE_CDP:
        if 'restoretime' not in query_params:
            return HttpResponse('{"r": "1","e": "restoretime is not exist"}')
        restore_time = query_params['restoretime']
        restore_node_time = restore_time
        api_request_data += ', "restore_time": "{restore_time}"'.format(restore_time=restore_time)
    elif point_type == xdata.SNAPSHOT_TYPE_NORMAL:
        pass
    else:
        return HttpResponse('{{"r": "1","e": "point type invalid : {}"}}'.format(point_type))

    api_request_data = api_request_data.replace('\\', '')
    api_request = json.loads('{{{}}}'.format(api_request_data))

    api_request['exclude_volumes'] = _get_ex_vols(ex_vols)
    api_request['disable_fast_boot'] = True if int(query_params['disable_fast_boot']) else False
    api_request['adapters'] = re_generate_adapters_params(ipconfig_infos)
    api_request['remote_kvm_params'] = remote_kvm_params
    api_request['replace_efi'] = True if int(query_params['replace_efi']) else False

    if hasFunctional('clw_desktop_aio'):
        if not have_audit_admin():
            return HttpResponse(json.dumps({'r': 1, 'e': '请先创建验证/恢复审批管理员'}, ensure_ascii=False))
        task_info = {'task_type': 'pc_restore'}
        task_info['host_snapshot_id'] = host_snapshot_id
        task_info['api_request'] = api_request
        task_info['pe_host_ident'] = pe_host_ident
        task_info['ipconfig_infos'] = ipconfig_infos
        task_info['operator'] = get_operator(request)
        task_info['restore_node_time'] = restore_node_time
        task_info['api_request_data'] = api_request_data
        ret = _add_to_audit_task_queue(request.get_host(), request.user, task_info)
        return HttpResponse(json.dumps(ret, ensure_ascii=False))
    else:
        return api_snapshot_restore(request.user, host_snapshot_id, api_request, pe_host_ident, ipconfig_infos,
                                    get_operator(request), restore_node_time, api_request_data)


def _add_to_audit_task_queue(host_name, user, task_info):
    create_datetime = timezone.now()
    audit_task.objects.create(create_user=user, status=audit_task.AUIDT_TASK_STATUS_WAITE,
                              create_datetime=create_datetime, task_info=json.dumps(task_info, ensure_ascii=False))
    notify_audits(user, host_name, create_datetime.strftime('%Y-%m-%d %H:%M:%S'), task_info)

    desc = {'操作': '执行恢复', '备份点ID': str(task_info['host_snapshot_id']), '任务状态': '等待审批',
            '备份点时间': task_info['restore_node_time']}
    SaveOperationLog(user, OperationLog.TYPE_RESTORE, json.dumps(desc, ensure_ascii=False), task_info['operator'])

    return {"r": 0, "e": '已提交恢复审批任务，请在<a style="color:blue;" href="../home">任务执行状态</a>中查看任务执行情况。', 'audit': 'audit'}


def api_snapshot_restore(user, host_snapshot_id, api_request, pe_host_ident, ipconfig_infos, operator,
                         restore_node_time, api_request_data):
    host_snapshot_obj = HostSnapshot.objects.get(id=host_snapshot_id)
    bNeedFix = False
    org_restore_time = api_request.get('restore_time')
    if host_snapshot_obj.is_cdp and host_snapshot_obj.cluster_schedule:
        bNeedFix, api_request['restore_time'] = fix_restore_time(host_snapshot_id, api_request['restore_time'])

    api_response = HostSnapshotRestore().post(None, host_snapshot_id, api_request)
    if not status.is_success(api_response.status_code):
        desc = {'操作': '执行恢复', '备份点ID': str(host_snapshot_id), '任务状态': api_response.data, 'debug': api_request}
        desc['debug']['FixRestoreTime'] = bNeedFix
        desc['debug']['org_restore_time'] = org_restore_time
        SaveOperationLog(
            user, OperationLog.TYPE_RESTORE, json.dumps(desc, ensure_ascii=False), operator)
        return HttpResponse(json.dumps({"r": 1, "e": api_response.data}, ensure_ascii=False))

    desc = {'操作': '执行恢复', '备份点ID': str(host_snapshot_id), '任务状态': '成功', '备份点时间': restore_node_time,
            'debug': api_request}
    desc['debug']['FixRestoreTime'] = bNeedFix
    desc['debug']['org_restore_time'] = org_restore_time
    save_target_master_nic_ips_to_pe(pe_host_ident, ipconfig_infos)
    SaveOperationLog(
        user, OperationLog.TYPE_RESTORE, json.dumps(desc, ensure_ascii=False), operator)
    authorize_init.save_host_rebuild_record(pe_host_ident)
    return HttpResponse(json.dumps({"r": "0", "e": "操作成功"}, ensure_ascii=False))


def save_target_master_nic_ips_to_pe(pe_host_ident, ipconfig_infos):
    pe_obj = RestoreTarget.objects.get(ident=pe_host_ident)
    ext_info = json.loads(pe_obj.info)
    for ipconfig in ipconfig_infos:
        if ipconfig['target_nic']['isConnected']:
            ip_mask_pair = ipconfig['ip_mask_pair']
            ext_info['master_nic_ips'] = [nic['Ip'] for nic in ip_mask_pair]
            pe_obj.info = json.dumps(ext_info)
            pe_obj.save(update_fields=['info'])

    return None


def _get_ex_vols(or_ex_vol_info):
    rs = list()
    vol_name2_info_maps = {}
    for vol in json.loads(or_ex_vol_info):
        if vol['VolumeName'] in vol_name2_info_maps:
            vol_name2_info_maps[vol['VolumeName']]['ranges'].append(vol['ranges'])
        else:
            vol_name2_info_maps[vol['VolumeName']] = {"display_name": vol['display_name'], "ranges": [vol['ranges']]}
    for _, value in vol_name2_info_maps.items():
        rs.append(value)
    return rs


def check_authorize_at_scan_backup():
    scan_backups = HostSnapshotShare.objects.all()

    json_txt = authorize_init.get_authorize_plaintext_from_local()
    if json_txt is None:
        return False, '读取授权文件异常'
    val = authorize_init.get_license_val_by_guid('scan_backup_concurrent', json_txt)
    if val is None:
        return False, '读取授权文件异常'
    if len(scan_backups) >= int(val):
        return False, '同时浏览备份点数超过授权允许值({0}个), 目前{1}个正被浏览'.format(val, len(scan_backups))

    return True, ''


def get_plan_name_by_snapshot_obj(snapshot):
    if snapshot.schedule:
        plan_name = snapshot.schedule.name
    elif snapshot.cluster_schedule:
        plan_name = snapshot.cluster_schedule.name
    elif snapshot.remote_schedule:
        plan_name = snapshot.remote_schedule.name
    else:
        plan_name = ''

    return plan_name


def mountpoint(request):
    if authorize_init.is_evaluation_and_expiration():
        return HttpResponse(json.dumps({"r": "1", "e": "试用版已过期", "list": []}))

    result = check_authorize_at_scan_backup()
    if not result[0]:
        return HttpResponse(json.dumps({"r": "1", "e": result[1], "list": []}))

    host_snapshot_id = request.GET.get('pointid', 0)
    timestamp = request.GET.get('time', 'none')
    filetype = request.GET.get('filetype', 'normal')
    page = request.GET.get('page', '')
    arr = host_snapshot_id.split('|')
    if len(arr) > 2:
        host_snapshot_id = arr[1]
    ret = GetDictionary(DataDictionary.DICT_TYPE_SAMBA, str(request.user.id), 'test|123456')
    vec = ret.split('|')
    username = 'test'
    password = '123'
    if len(vec) == 2:
        username = vec[0]
        password = vec[1]

    snapshot_obj = HostSnapshot.objects.get(id=host_snapshot_id)
    if snapshot_obj.host.type == Host.PROXY_AGENT:
        ext_info_obj = json.loads(snapshot_obj.ext_info)
        system_infos = ext_info_obj.get('system_infos')
        if not system_infos or not system_infos.get('load'):
            logic = 'windows'
            get_systeminfo_for_proxy_agent(snapshot_obj, logic)
            return HttpResponse(json.dumps({"r": "99", "e": "正在为第一次浏览准备系统信息，请5分钟后再试"}, ensure_ascii=False))
    task_name = get_plan_name_by_snapshot_obj(snapshot_obj)
    if timestamp == 'none':
        timestamp = snapshot_obj.start_datetime.strftime("%Y-%m-%dT%H:%M:%S.%f")

    api_request = {
        "host_snapshot_id": "{}".format(host_snapshot_id),
        "timestamp": timestamp,
        "filetype": filetype,
        "samba_user": username,
        "samba_pwd": password,
        "user_id": request.user.id,
    }
    isWin = request.GET['isWin'] == 'true'
    if hasFunctional('clw_desktop_aio'):
        if not have_audit_admin():
            return HttpResponse(json.dumps({'r': 1, 'e': '请先创建验证/恢复审批管理员'}, ensure_ascii=False))
        if page == 'validate':
            task_info = {'task_type': 'file_verify'}
            file_restore_display = '文件验证'
        else:
            task_info = {'task_type': 'file_restore'}
            file_restore_display = '文件恢复'
        task_info['api_request'] = api_request
        task_info['task_name'] = task_name
        task_info['timestamp'] = timestamp
        task_info['operator'] = get_operator(request)
        task_info['host_name'] = request.get_host()
        task_info['isWin'] = isWin
        ret = _add_file_restore_to_audit_task_queue(request.get_host(), request.user, task_info, file_restore_display)
        return HttpResponse(json.dumps(ret, ensure_ascii=False))
    else:
        return api_snapshot_share_add(request.user, api_request, task_name, timestamp, get_operator(request),
                                      request.get_host(), isWin)


def _add_file_restore_to_audit_task_queue(host_name, user, task_info, file_restore_display):
    create_datetime = timezone.now()
    audit_task.objects.create(create_user=user, status=audit_task.AUIDT_TASK_STATUS_WAITE,
                              create_datetime=create_datetime, task_info=json.dumps(task_info, ensure_ascii=False))
    notify_audits(user, host_name, create_datetime.strftime('%Y-%m-%d %H:%M:%S'), task_info)

    desc = {'操作': '浏览备份数据', '备份点': str(task_info['task_name']), '任务状态': '等待审批',
            '备份点时间': task_info['timestamp']}
    SaveOperationLog(user, OperationLog.TYPE_BROWSE_LOG, json.dumps(desc, ensure_ascii=False), task_info['operator'])

    return {"r": 0,
            "e": '已提{}审批，请在<a style="color:blue;" href="../home">任务执行状态</a>中查看任务执行情况。'.format(file_restore_display),
            'audit': 'audit'}


def api_snapshot_share_add(user, api_request, task_name, timestamp, operator, host_name, isWin):
    api_response = HostSnapshotShareAdd().post(None, api_request)
    if not status.is_success(api_response.status_code):
        mylog = {'操作': '浏览备份数据', '备份点': task_name, '备份点时间': timestamp, "操作结果": api_response.data}
        SaveOperationLog(
            user, OperationLog.TYPE_BROWSE_LOG, json.dumps(mylog, ensure_ascii=False), operator)
        return HttpResponse(json.dumps({"r": "1", "e": api_response.data}))
    sambaip = host_name
    sambahost = host_name.split(':')
    if len(sambahost) == 2:
        sambaip = sambahost[0]
    samba_url = api_response.data["samba_url"]
    samba_url = samba_url.replace('SambaServer', '\\\\' + sambaip)
    samba_url = check_samba_url(isWin, samba_url)
    id = api_response.data["id"]
    samba_user = api_response.data["samba_user"]
    samba_pwd = api_response.data["samba_pwd"]
    host_display_name = api_response.data["host_display_name"]

    is_exist = None
    if api_response.status_code == status.HTTP_201_CREATED:
        is_exist = False
    if api_response.status_code == status.HTTP_200_OK:
        is_exist = True

    mylog = {'操作': '浏览备份数据', '备份点': task_name, '备份点时间': timestamp, "操作结果": '执行成功'}
    SaveOperationLog(
        user, OperationLog.TYPE_BROWSE_LOG, json.dumps(mylog, ensure_ascii=False), operator)
    return HttpResponse(json.dumps(
        {"r": "0", "e": "操作成功", "id": "{}".format(id), "addr": "{}".format(samba_url), 'is_exist': is_exist,
         "name": "{}".format(samba_user),
         "pwd": "{}".format(samba_pwd), "time": timestamp, "task_name": task_name, "hostname": host_display_name,
         'share_status': api_response.data["share_status"]}, ensure_ascii=False))


def unmountpoint(request):
    shared_host_snapshot_ids = str(request.GET.get('id', 0))
    arr = shared_host_snapshot_ids.split(',')
    for shared_host_snapshot_id in arr:
        api_response = HostSnapshotShareDelete().delete(request, shared_host_snapshot_id)
        if not status.is_success(api_response.status_code):
            mylog = {'操作': '关闭浏览备份数据', '浏览备份点id': shared_host_snapshot_id, "操作结果": "关闭失败{}".format(api_response.data)}
            SaveOperationLog(
                request.user, OperationLog.TYPE_BROWSE_LOG, json.dumps(mylog, ensure_ascii=False),
                get_operator(request))
            return HttpResponse(json.dumps({"r": "1", "e": "关闭失败{}".format(api_response.data)}, ensure_ascii=False))

        mylog = {'操作': '关闭浏览备份数据', '浏览备份点id': shared_host_snapshot_id, "操作结果": "操作成功"}
        SaveOperationLog(
            request.user, OperationLog.TYPE_BROWSE_LOG, json.dumps(mylog, ensure_ascii=False), get_operator(request))
    return HttpResponse(json.dumps({"r": "0", "e": "操作成功"}, ensure_ascii=False))


def check_samba_url(is_Win, samba_url):
    if is_Win:
        return samba_url
    else:
        return 'smb:{}'.format(samba_url.replace('\\', '/'))


def getlinklist(request):
    origin = request.GET.get('origin', None)
    api_response = HostSnapshotShareQuery().get(request)
    if not status.is_success(api_response.status_code):
        if api_response.data:
            return HttpResponse(
                json.dumps({"r": "1", "e": "没有已打开的备份点{}".format(api_response.data)}, ensure_ascii=False))
        return HttpResponse(json.dumps({"r": "1", "e": "没有已打开的备份点"}, ensure_ascii=False))

    infoList = list()
    isWin = request.GET['isWin'] == 'true'
    for item in api_response.data:
        sambaip = request.get_host()
        sambahost = request.get_host().split(':')
        if len(sambahost) == 2:
            sambaip = sambahost[0]
        samba_url = item["samba_url"]
        samba_url = samba_url.replace('SambaServer', '\\\\' + sambaip)
        samba_url = check_samba_url(isWin, samba_url)
        host_snapshot_id = item["host_snapshot_id"]
        snapshot_obj = HostSnapshot.objects.get(id=host_snapshot_id)
        schedulobj = snapshot_obj.schedule
        task_name = schedulobj.name if schedulobj else ''

        host_start_time = xdatetime.string2datetime(item['host_start_time']).strftime('%Y-%m-%d %H:%M:%S.%f')

        if item["share_status"] == 'init':
            continue
        if origin:
            web_link = '{}/xdashboard/filebrowser_handle/?a=home&schedule={}'.format(origin, item['id'])
        else:
            web_link = '{}://{}/'.format(request.scheme, request.META['HTTP_HOST']) + \
                       'xdashboard/filebrowser_handle/?a=home&schedule=' + \
                       str(item['id'])
        info = {'id': item["id"], 'url': '计算机名：{} 时间：{}'.format(item["host_display_name"], host_start_time),
                'auth': '访问地址：{}<br />在线访问地址：{}<br />用户名：{}<br />密　码：{}<br />任务名：{}'.format(samba_url,
                                                                                            web_link,
                                                                                            item["samba_user"],
                                                                                            item["samba_pwd"],
                                                                                            task_name)}
        info['host_display_name'] = item["host_display_name"]
        info['host_start_time'] = item["host_start_time"]
        info['task_name'] = task_name
        info['schedule_id'] = schedulobj.id if schedulobj else -1
        info['host_snapshot_type'] = item["host_snapshot_type"]
        infoList.append(info)
    ret = {"r": 0, "e": "操作成功", "list": infoList}
    infos = json.dumps(ret, ensure_ascii=False)
    return HttpResponse(infos)


def checkpedriver(request):
    serverid = request.GET['serverid']
    pointid = request.GET['pointid']

    if '|' in pointid:
        pointid = pointid.split('|')[1]

    api_response = PeHostSessionInfo().get(request=request, ident=serverid)
    if not status.is_success(api_response.status_code):
        e = get_response_error_string(api_response)
        debug = "PeHostSessionInfo().get() serverid={} failed {}".format(serverid, api_response.status_code)
        return HttpResponse(json.dumps({"r": 1, "e": e, "debug": debug}, ensure_ascii=False))

    respn = TargetHardware().get(request=request, id_ident=pointid, pe_ident=serverid, api_request={})
    if not status.is_success(respn.status_code):
        return HttpResponse('{"r": 1, "e": "操作失败"}')
    hardwares = respn.data
    os_type = hardwares['os_type']
    drivers = hardwares['drivers']

    driverlist = []
    driverlist = list()
    for driver in drivers:
        driverlist.append(
            {
                'windows': os_type,
                'hardware': driver['szDescription'] if driver['szDescription'] else driver['HWIds'][0],
                'id': driver['HWIds'],
                'compatible': driver['CompatIds']
            }
        )
    ret_info = {"r": 0, "e": "操作成功", "list": driverlist, 'update': 1 if driverlist else 0}
    # 暂时有问题
    # ret_info = {"r": 0, "e": "操作成功", "list": [], 'update': 0}
    return HttpResponse(json.dumps(ret_info, ensure_ascii=False))


def cur_file_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def upload(request):
    return ver_pload(request)


def uploadbyflash(request):
    file = request.FILES.get("Filedata", None)
    ret = 2
    name = 'none'
    if file:
        filepath = os.path.join(cur_file_dir(), 'static', 'download', 'update', file.name)
        name = file.name
        fp = open(filepath, 'wb')
        for content in file.chunks():
            fp.write(content)
        fp.close()
        ret = 200
        _logger.info('file upload ok filename={}'.format(filepath))
    return HttpResponse(json.dumps({'r': ret, 'save_name': name}, ensure_ascii=False))


def resetpwd(request):
    username = getUniqueSambaUsername()
    password = ''.join(random.sample('0123456789', 6))
    ret = SaveDictionary(DataDictionary.DICT_TYPE_SAMBA, str(request.user.id), username + '|' + password)
    mylog = {'操作': '重置浏览备份点的用户名和密码', "操作结果": "操作成功"}
    SaveOperationLog(
        request.user, OperationLog.TYPE_BROWSE_LOG, json.dumps(mylog, ensure_ascii=False), get_operator(request))
    return HttpResponse(json.dumps(ret, ensure_ascii=False))


def getsambapwd(request):
    ret = GetDictionary(DataDictionary.DICT_TYPE_SAMBA, str(request.user.id), 'aio|123456')
    vec = ret.split('|')
    username = 'test'
    password = '123'
    if len(vec) == 2:
        username = vec[0]
        password = vec[1]
    return HttpResponse(json.dumps({"r": 0, "e": "操作成功", "pwd": password, "username": username}, ensure_ascii=False))


def getiodaychart(request):
    host_snapshot_id = request.GET['id']
    slice_end_time = request.GET['sliceend']
    centre_time = datetime.fromtimestamp(int(request.GET['centre'][0:10]))
    window_secs = int(request.GET['window'])
    half_window_secs = window_secs / 2
    query_start_time = (centre_time - timedelta(seconds=half_window_secs)).strftime('%Y-%m-%d %H:%M:%S.%f')
    query_end_time = (centre_time + timedelta(seconds=half_window_secs)).strftime('%Y-%m-%d %H:%M:%S.%f')

    host_snapshot_obj = HostSnapshot.objects.get(id=host_snapshot_id)
    if host_snapshot_obj.cluster_schedule:
        _list = get_cluster_io_daychart(host_snapshot_id, slice_end_time, centre_time, window_secs)
    else:
        times_scores = cdpFileIO.get_times_scores_by_host_snapshot(host_snapshot_id, slice_end_time, query_start_time,
                                                                   query_end_time)
        _list = [{'time': str(time_score['time']), 'score': time_score['score']} for time_score in times_scores]

    return HttpResponse(json.dumps({'r': 0, 'times_scores': _list}))


def enhance_font_to_html(text, style='bold'):
    if style == 'bold':
        return '<span style="font-weight:bold">{}</span>'.format(text)
    return text


# 返回驱动TreeNode信息到UI: 即用户手动选择驱动界面
# 在选择,排除驱动后, 将结果存入'drivers_ids', 见函数'startRestore(request)'
def get_driver_version(request):
    query_params = request.GET
    if '|' in query_params['pointid']:
        request_params = query_params['pointid']
        point_params = request_params.split('|')
        host_snapshot_id = point_params[1]
    else:
        host_snapshot_id = query_params['pointid']
    pe_host_ident = query_params['destserverid']

    respn = GetDriversVersions().get(request=request, snapshot_id=host_snapshot_id, pe_ident=pe_host_ident,
                                     api_request={})
    if not status.is_success(respn.status_code):
        return HttpResponse('{"r": 1, "e": "操作失败"}')
    drive_list = respn.data['drivers']
    os_type = respn.data['sys_os_type']
    result_list = list()
    for driver in drive_list:  # 遍历所有设备
        if not driver:
            continue
        root_item = {"id": 'id_' + driver[0]['HWIds'][0], "branch": [], "inode": True, "open": True, "checked": False,
                     "label": driver[0]['szDescription'], "checkbox": False, 'is_platform': False,
                     'is_force_install': False}
        ch_id = 0
        for version in driver[1]:  # 遍历该设备下的所有驱动
            # 设备的名称在为空的情况下，填入驱动的名称
            if not root_item['label']:
                root_item['label'] = version['show_name']

            # 硬件是平台相关的硬件
            if version['Str_HWPlatform'] and version['Str_HWPlatform'] != '用户导入':
                root_item['is_platform'] = True

            if version['IsPlatform']:
                prefix_name = '平台驱动_' + version['Str_HWPlatform']
            elif version['IsMicro']:
                prefix_name = '微软驱动'
            else:
                prefix_name = '普通驱动_' + version['Str_HWPlatform'] if version['Str_HWPlatform'] else '普通驱动'

            # 添加驱动zip_path信息
            if version['zip_path'].endswith('.zip'):
                prefix_name = '{0}({1})'.format(prefix_name, version['zip_path'].strip('.zip'))
            prefix_name = enhance_font_to_html(prefix_name)

            if version['IsMicro']:
                show_time = ''
            else:
                show_time = str(version['year']) + '年' + str(version['mon']) + '月' + str(version['day']) + '日'

            label_name = [prefix_name, show_time, version['show_name'], version['hard_or_comp_id']]
            label_name = list(filter(lambda x: x, label_name))
            label = '; '.join(label_name)
            ch_item_id = driver[0]['HWIds'][0] + '|' + str(ch_id) + '|' + version['zip_path']
            force_install = ForceInstallDriver.objects.filter(sys_type=os_type,
                                                              driver_id=version['zip_path'],
                                                              user_id=request.user.id,
                                                              device_id=version['hard_or_comp_id']).exists()
            if force_install:
                root_item['is_force_install'] = True

            ch_item = {"id": ch_item_id, "branch": [],
                       "inode": False, "open": False, "checked": True, "label": label, "checkbox": True,
                       'is_in_black': check_driver_in_blacklist(driver[0], os_type, version['zip_path']),
                       'is_force_install': force_install,
                       'Str_HWPlatform': version['Str_HWPlatform']}
            root_item['branch'].append(ch_item)
            ch_id = ch_id + 1
        if root_item['is_platform']:
            for ch_item in root_item['branch']:
                ch_item['checked'] = False
        if root_item['is_force_install']:  # 强制安装某个驱动
            for ch_item in root_item['branch']:
                ch_item['checked'] = False
                ch_item['checkbox'] = False
                ch_item['radio'] = True
            for ch_item in root_item['branch']:
                if ch_item['is_force_install']:
                    ch_item['checked'] = True
                    break

        result_list.append(root_item)
    return HttpResponse(json.dumps(result_list))


def check_is_same_computer(request):
    serverid = request.GET['serverid']
    pointid = request.GET['pointid']
    if '|' in pointid:
        point_params = pointid.split('|')
        pointid = point_params[1]

    api_response = PeHostSessionInfo().get(request=request, ident=serverid)
    if not status.is_success(api_response.status_code):
        rs_dict = get_wrong_rsp(api_response, '获取信息失败。')
        return HttpResponse(json.dumps(rs_dict, ensure_ascii=False))

    respn = TargetHardware().get(request=request, id_ident=pointid, pe_ident=serverid, api_request={})
    if not status.is_success(respn.status_code):
        rs_dict = get_wrong_rsp(respn, '检测平台是否异构失败。')
        return HttpResponse(json.dumps(rs_dict, ensure_ascii=False))
    jsonstr = {"is_same": respn.data['is_same'], "r": 0, "restore_to_self": respn.data['restore_to_self']}
    return HttpResponse(json.dumps(jsonstr))


def get_wrong_rsp(rsp, msg):
    err_msg = rsp.data if rsp.data else msg
    rs_dict = {'r': 1, 'e': err_msg}
    return rs_dict


def restorevol(request):
    pointid = request.POST.get('pointid', default='none')
    serverid = request.POST.get('serverid', default='none')
    rs_time = request.POST.get('restore_time', default='none')
    vols_str = request.POST.get('vol_maps', default='')
    index_lists_str = request.POST.get('index_list', default='')
    index_lists = json.loads(index_lists_str)
    vols = json.loads(vols_str)
    point_params = pointid.split('|')
    point_type = point_params[0]
    host_snapshot_id = point_params[1]

    restore_node_time = ''
    hostsnapshot_obj = HostSnapshot.objects.filter(id=host_snapshot_id)
    if hostsnapshot_obj:
        restore_node_time = hostsnapshot_obj[0].start_datetime.strftime('%Y-%m-%d %H:%M:%S.%f')

    volumes = list()
    for src_index, dst_index in enumerate(index_lists):
        if dst_index is not None:
            volumes.append(DiskVolMap.get_vol_restore_params(vols[src_index], int(dst_index)))

    api_request = {
        'type': point_type,
        'host_ident': serverid,
        'volumes': volumes,
        'restore_time': rs_time if rs_time else None
    }
    _logger.debug(api_request)
    if hasFunctional('clw_desktop_aio'):
        if not have_audit_admin():
            return HttpResponse(json.dumps({'r': 1, 'e': '请先创建验证/恢复审批管理员'}, ensure_ascii=False))
        task_info = {'task_type': 'vol_restore'}
        task_info['host_snapshot_id'] = host_snapshot_id
        task_info['api_request'] = api_request
        task_info['operator'] = get_operator(request)
        task_info['restore_node_time'] = restore_node_time
        ret = _add_to_audit_task_queue(request.get_host(), request.user, task_info)
        return HttpResponse(json.dumps(ret, ensure_ascii=False))
    else:
        return api_host_snapshot_restore_vol(request.user, host_snapshot_id, api_request, get_operator(request),
                                             restore_node_time)


def api_host_snapshot_restore_vol(user, host_snapshot_id, api_request, operator, restore_node_time):
    api_response = HostSnapshotRestore().post(None, host_snapshot_id, api_request)
    if not status.is_success(api_response.status_code):
        desc = {'操作': '执行恢复', '备份点ID': str(host_snapshot_id), '任务状态': api_response.data}
        SaveOperationLog(
            user, OperationLog.TYPE_RESTORE, json.dumps(desc, ensure_ascii=False), operator)
        return HttpResponse(json.dumps({"r": 1, "e": api_response.data}, ensure_ascii=False))

    desc = {'操作': '执行恢复', '备份点ID': str(host_snapshot_id), '任务状态': '成功', '备份点时间': restore_node_time}
    SaveOperationLog(
        user, OperationLog.TYPE_RESTORE, json.dumps(desc, ensure_ascii=False), operator)
    return HttpResponse(json.dumps({"r": 0, "e": "操作成功"}, ensure_ascii=False))


def _check_host_is_idle(ident):
    try:
        host_status = boxService.box_service.GetStatus(ident)
    except Exception as e:
        _logger.error('_check_host_is_idle error ident:{} msg:{}'.format(ident, e))
        return False
    else:
        return True if 'idle' in host_status else False


def get_disk_vol_maps(request):
    pointid = request.GET.get('point_id', default='none')
    host_ident = request.GET.get('server_id', default='none')
    point_params = pointid.split('|')
    point_type = point_params[0]
    host_snapshot_id = point_params[1]
    rst = _check_host_is_idle(host_ident)
    if not rst:
        return HttpResponse(json.dumps({'r': 1, 'e': '获取磁盘信息失败，目标还原机正在执行其它任务。'}))
    rsp = DiskVolMap().get(request, host_snapshot_id, host_ident)
    if status.is_success(rsp.status_code):
        js_dict = dict()
        js_dict['e'] = '操作成功'
        js_dict['r'] = 0
        js_dict['maps'] = rsp.data['maps']
        return HttpResponse(json.dumps(js_dict))
    else:
        return HttpResponse(json.dumps({'r': 1, 'e': '获取磁盘信息失败'}))


def check_pe_status(request):
    pe_ident = request.GET['pe_ident']
    try:
        time.sleep(1)
        is_linked = boxService.box_service.isPeHostLinked(pe_ident)
    except Exception as e:
        _logger.warning('check_pe_status, failed: {}'.format(e))
        is_linked = False

    return HttpResponse(json.dumps({'is_linked': is_linked}))


def adapternames(request):
    point_params = request.GET.get('hostsnapshot', default='none')
    hostsnapshot_id = point_params.split('|')[1]  # 获取备份点中网卡信息
    hostsnapshot = HostSnapshot.objects.get(id=hostsnapshot_id)
    ext_info = json.loads(hostsnapshot.ext_info)
    nic = ext_info['system_infos']['Nic']
    for one_nic in nic:
        for one in one_nic.get('Dns', []):
            if one.find(':') != -1:
                one_nic['Dns'] = ''
    return HttpResponse(json.dumps({'r': 0, 'nic': nic, 'e': '操作成功'}))


def get_src_diskpartition_info(request):
    from django.shortcuts import render_to_response
    return render_to_response("src.json")


def get_dest_diskpartition_info(request):
    from django.shortcuts import render_to_response
    return render_to_response("dest.json")


def restore_handler(request):
    a = request.GET.get('a', 'none')
    if a == 'none':
        a = request.POST.get('a', 'none')
    if a == 'getserverlist':
        return getServerList(request)
    if a == 'getrestoreserverlist':
        return getRestoreServerList(request)
    if a == 'getrestoreserverlistyun':
        return getRestoreServerListYun(request)
    if a == 'getpoint':
        return getRestorePoint(request)
    if a == 'startrestore':
        return startRestore(request)
    if a == 'gepointdetail':
        return getPointDetail(request)
    if a == 'adaptersettings':
        return getAdapterSettings(request)
    if a == 'harddisksettings':
        return getHardDiskSettings(request)
    if a == 'mountpoint':
        return mountpoint(request)
    if a == 'unmountpoint':
        return unmountpoint(request)
    if a == 'getlinklist':
        return getlinklist(request)
    if a == 'checkpedriver':
        return checkpedriver(request)
    if a == 'upload':
        return upload(request)
    if a == 'uploadbyflash':
        return uploadbyflash(request)
    if a == 'resetpwd':
        return resetpwd(request)
    if a == 'getsambapwd':
        return getsambapwd(request)
    if a == 'getiodaychart':
        return getiodaychart(request)
    if a == 'get_driver_version':
        return get_driver_version(request)
    if a == 'check_is_same_computer':
        return check_is_same_computer(request)
    if a == 'restorevol':
        return restorevol(request)
    if a == 'get_disk_vol_maps':
        return get_disk_vol_maps(request)
    if a == 'check_pe_status':
        return check_pe_status(request)
    if a == 'adapternames':
        return adapternames(request)
    if a == 'get_src_diskpartition_info':
        return get_src_diskpartition_info(request)
    if a == 'get_dest_diskpartition_info':
        return get_dest_diskpartition_info(request)

    return HttpResponse(json.dumps({"r": "1", "e": "没有对应的action:{}".format(html.escape(a))}))
