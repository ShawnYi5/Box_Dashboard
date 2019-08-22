# coding=utf-8
import base64
import copy
import datetime
import html
import json
import os
import re
import uuid
from django.core.paginator import Paginator
from django.db import transaction
from django.http import HttpResponse
from rest_framework import status
from django.utils import timezone
from apiv1 import work_processors
from apiv1.logic_processors import query_system_info
from apiv1.models import BackupTask, HostGroup, GroupBackupTaskSchedule
from apiv1.models import StorageNode, Host, BackupTaskSchedule
from apiv1.views import HostSessions, HostSessionDisks, BackupTaskSchedules, BackupTaskScheduleSetting, \
    BackupTaskScheduleExecute, QuotaManage, HostSessionInfo, DiskVolMap, ClusterBackupScheduleManager, \
    get_response_error_string
from apiv1.work_processors import HostBackupWorkProcessors
from box_dashboard import boxService, functions
from box_dashboard import xlogging, xdata
from xdashboard.common import file_utils
from xdashboard.common.license import check_license, is_functional_available
from xdashboard.handle import serversmgr
from xdashboard.handle.authorize import authorize_init
from xdashboard.handle.logserver import SaveOperationLog
from xdashboard.models import OperationLog, taskpolicy, cdpcycle, onlyonecycle, everydaycycle, everyweekcycle, \
    everymonthcycle
from xdashboard.request_util import get_operator
from .logserver import cur_file_dir, delexpirelog

_logger = xlogging.getLogger(__name__)

import BoxLogic


def is_windows_host(host_ident):
    host = Host.objects.get(ident=host_ident)
    system_infos = json.loads(host.ext_info)['system_infos']
    return 'LINUX' not in system_infos['System']['SystemCaption'].upper()


def get_linux_disk_total_used_bytes(host_ident):
    try:
        host_obj = Host.objects.get(ident=host_ident)
        system_infos = json.loads(host_obj.ext_info)['system_infos']
        # 统计非"lvm"分区 已用空间
        disks = system_infos['Disk']
        partitions = []
        for disk in disks:
            partitions += disk['Partition']
        partitions = list(filter(lambda partition: 'LVM' not in partition['FileSystem'].upper(), partitions))
        used_bytes = [int(pti['PartitionSize']) - int(pti['FreeSize']) for pti in partitions if
                      int(pti['FreeSize']) != -1]
        total_bytes = sum(used_bytes)

        # 统计所有LV，已用空间
        volume_groups = system_infos['Storage']['vgs']
        logical_volumes = []
        for volume_group in volume_groups:
            logical_volumes += volume_group['lvs']
        logical_volumes = list(filter(lambda lv: lv['mountPoint'], logical_volumes))
        used_bytes = [int(lv.get('used_bytes', 0)) for lv in logical_volumes]
        total_bytes += sum(used_bytes)

        return '{0:.2f}'.format(total_bytes / 1024 ** 3)
    except Exception as e:
        _logger.error('get_linux_disk_total_used_bytes host_ident:{},error:{}'.format(host_ident, e),
                      exc_info=True)
        return -1


# windows系统，分区的表示："分区标识符|显示名称|所属磁盘GUID"
def windows_partition_identifier(part):
    if part['Letter']:
        identifier = part['Letter']
    else:
        guid, byte_offset, byte_length = part['NativeGUID'], part['PartitionOffset'], part['PartitionSize']
        identifier = '{0}:{1}:{2}'.format(guid, byte_offset, byte_length)
    show_name = DiskVolMap.get_name(part)

    return '{0}|{1}|{2}|{3}'.format(identifier, show_name, part['NativeGUID'], part['Letter'])


# linux系统，分区的表示："分区标识符|显示名称|所属磁盘GUID"
def linux_partition_identifier(part):
    if part['Type'] == 'lv':
        identifier = part['VolumeDevice']
    else:
        byte_start, byte_end, guid = part['BytesStart'], part['BytesEnd'], part['NativeGUID']
        byte_length = int(byte_end) - int(byte_start)
        identifier = '{0}:{1}:{2}'.format(guid, byte_start, byte_length)
    show_name = DiskVolMap.get_name_linux(part)

    return '{0}|{1}|{2}|{3}'.format(identifier, show_name, part['NativeGUID'], part['MountPoint'])


# linux、windows：获取磁盘{index: guid,}
def get_host_disks_index_guid(system_infos):
    disks = system_infos['Disk']
    return {disk['DiskNum']: disk['NativeGUID'] for disk in disks}


# 过滤Linux分区：排除掉属于PV的
def filter_out_partition_which_belong_to_pv(system_infos, partitions):
    vgs = system_infos['Storage']['vgs']
    pvs = []
    for vg in vgs:
        pvs += vg['pvs']
    pvs_name = [pv['name'] for pv in pvs]

    return list(filter(lambda part: part['VolumeDevice'] not in pvs_name, partitions))


# linux、windows：获取卷信息[vol_guid|show_str|disk_guid]
def get_host_volumes_info(host_obj, system_infos):
    if is_windows_host(host_obj.ident):
        partitions, disks = [], system_infos['Disk']
        for disk in disks:
            for part in disk['Partition']:
                part['NativeGUID'] = disk['NativeGUID']  # 分区添加字段GUID
            partitions += disk['Partition']
        return list(map(windows_partition_identifier, partitions))  # 提取标识符
    else:
        partitions = work_processors.HostBackupWorkProcessors.analyze_linux_partitions(None, system_infos)
        partitions = filter_out_partition_which_belong_to_pv(system_infos, partitions)
        indexs_guids = get_host_disks_index_guid(system_infos)
        for part in partitions:
            part['NativeGUID'] = indexs_guids[str(part['DiskIndex'])]
        return list(map(linux_partition_identifier, partitions))


def get_boot_info(boot_map, index):
    if boot_map[str(index)]:
        return '(启动盘)'
    else:
        return ''


# 磁盘描述：[disk_guid|show_str|index]
def get_host_disks_info(host_obj, system_infos):
    disks_descr = []
    disks = system_infos['Disk']
    if is_windows_host(host_obj.ident):
        for disk in disks:
            disks_descr.append('{guid}|磁盘{index}({name},{size:.2f}GB){boot_str}|{index}'.format(
                guid=disk['NativeGUID'], index=disk['DiskNum'],
                boot_str=get_boot_info(system_infos['BootMap'], disk['DiskNum']),
                name=disk['DiskName'], size=int(disk['DiskSize']) / 1024 ** 3))
    else:
        for disk in disks:
            disks_descr.append('{guid}|{dev_name}({name},{size:.2f}GB){boot_str}|{index}'.format(
                guid=disk['NativeGUID'], dev_name=disk['DiskIndex'], index=disk['DiskNum'],
                boot_str=get_boot_info(system_infos['BootMap'], disk['DiskNum']),
                name=get_disk_name(disk), size=int(disk['DiskSize']) / 1024 ** 3))
    return disks_descr


def get_disk_name(disk):
    return HostBackupWorkProcessors.get_disk_name(disk)


# 当disk为启动盘时不能被排除
def check_startup_disk(disk_node, volume_node):
    system_reserved = '系统保留'
    if '启动盘' not in disk_node['label']:
        return volume_node
    if (volume_node['mount_point'] in ['/', '/boot', 'C']) or (system_reserved in volume_node['label']):
        volume_node['disabled'] = True
    return volume_node


def append_to_disk_node(volume_node, disk_guid, target_disks_node):
    for disk_node in target_disks_node:
        volume_node = check_startup_disk(disk_node, volume_node)
        if disk_node['id'] == disk_guid:
            disk_node['branch'].append(volume_node)
            disk_node['inode'] = disk_node['open'] = True
            break
    else:
        _logger.error('卷({0})所属的磁盘({1})不存在'.format(volume_node['id'], disk_guid))


hostErrorMsg = '<span style="color: red"> 错误: 获取主机信息失败</span>'

disks_status = {
    'host-ident': 'queryDisksStatus(host-ident)'
}


# True, False, None
# 使用注意: 查询完毕后, 记得清除缓存
def check_is_support_disk(disk_index, host_ident):
    if host_ident not in disks_status:
        try:
            disks_status[host_ident] = boxService.box_service.queryDisksStatus(host_ident)
        except Exception as e:
            _logger.warning('queryDisksStatus {0} Exception {1}'.format(host_ident, e))
            return None

    disk_obj = list(filter(lambda disk: str(disk.id) == str(disk_index), disks_status[host_ident]))
    return disk_obj[0].detail.status != BoxLogic.DiskStatus.Unsupported


# 禁用全部的Node(磁盘，卷)
def all_nodes_disabled(disks_node):
    for disk_node in disks_node:
        disk_node['disabled'] = True
        for vol_node in disk_node['branch']:
            vol_node['disabled'] = True


def removing_disk_if_empty_guid(system_infos):
    disks = system_infos['Disk']
    system_infos['Disk'] = list(filter(lambda disk: disk['NativeGUID'] != '', disks))


def disk_vol_guid_not_in_exclude_list(api, exclude_guids, hosts_exclude, host_ident, disk_vol_guid):
    if api is None:
        exclude_list = exclude_guids
    else:
        exclude_list = hosts_exclude.get(host_ident, [])

    return disk_vol_guid not in exclude_list


# 获取host目前磁盘,卷信息(后台30分钟更新)
def getCurrentDiskVol(request, api=None, update=True):
    host_ident = request.GET['ident'] if (api is None) else api['ident']  # 'ident'
    plan_id = request.GET['planId'] if (api is None) else api['planId']  # 'id'、'undefined'
    host_obj = Host.objects.get(ident=host_ident)
    # 目前磁盘、卷的最新状态
    system_infos = query_system_info(host_obj, update=update)
    if system_infos is None:
        system_infos = query_system_info(host_obj, update=False)
    removing_disk_if_empty_guid(system_infos)  # 过滤Disk

    volumes_list = get_host_volumes_info(host_obj, system_infos)  # [vol_guid|show_str|disk_guid]
    disks_list = get_host_disks_info(host_obj, system_infos)  # [disk_guid|show_str|index]

    # 普通修改计划
    exclude_guids = ['disk-guid', 'vol-guid']
    if not api and plan_id != 'undefined':
        planDetl = BackupTaskScheduleSetting().get(request=request, backup_task_schedule_id=plan_id).data
        planExt = json.loads(planDetl['ext_config'])
        exclude_list = planExt['exclude']  # [{'exclude_info': 'vol9_guid', 'lable': 'vol9', 'exclude_type': 2}]
        exclude_guids = [vol_or_disk['exclude_info'] for vol_or_disk in exclude_list]

    # 集群修改计划
    hosts_exclude = {'host-ident': ['disk-guid', 'vol-guid']}
    if api and plan_id != 'undefined':
        ext_config = ClusterBackupScheduleManager().get(request, plan_id).data['ext_config']
        exclude = json.loads(ext_config)['exclude']
        for host_exclude in exclude.items():
            hosts_exclude[host_exclude[0]] = list(map(lambda disk_vol: disk_vol['exclude_info'], host_exclude[1]))

    # 1.生成磁盘Node
    disks_node, host_error = [], False
    for disk in disks_list:
        disk_node = {'id': '', 'label': '', 'branch': [], 'checkbox': False, 'checked': False, 'disabled': False}
        disk_guid, show_str, disk_index = disk.split('|')[0], disk.split('|')[1], disk.split('|')[2]
        supported = check_is_support_disk(disk_index, host_ident)
        disk_node['id'], disk_node['label'] = disk_guid, show_str
        checked = disk_vol_guid_not_in_exclude_list(api, exclude_guids, hosts_exclude, host_ident, disk_guid)
        if supported is True:
            disk_node['checkbox'], disk_node['disabled'] = True, False
            disk_node['checked'] = checked
        elif supported is False:
            disk_node['checkbox'], disk_node['disabled'] = False, False
        elif supported is None:
            disk_node['checkbox'], disk_node['disabled'] = True, True
            disk_node['checked'] = checked
            host_error = True
        else:
            pass
        disks_node.append(disk_node)

    # 2.生成卷Node
    for volume in volumes_list:
        vol_guid, show_str, disk_guid, mount_point = volume.split('|')
        checked = disk_vol_guid_not_in_exclude_list(api, exclude_guids, hosts_exclude, host_ident, vol_guid)
        volume_node = {"id": vol_guid, "label": show_str, 'checkbox': True, 'disabled': host_error, 'checked': checked,
                       'mount_point': mount_point}
        append_to_disk_node(volume_node, disk_guid, disks_node)

    if host_ident in disks_status:  # 该主机遍历磁盘完毕, 释放cache
        del disks_status[host_ident]

    if api is not None:  # return to api
        return disks_node, host_error
    return HttpResponse(json.dumps(disks_node))


# 获取该主机的磁盘Nodes(及其子Nodes:卷)
def get_host_disks_nodes(request, api_params):
    return getCurrentDiskVol(request, api=api_params, update=False)


# 多主机: 排除磁盘,卷
# Tree-0: Hosts  Tree-1: Disks  Tree-2: Vols
def get_hosts_nodes(request):
    host_node = {'id': '', 'label': '', 'branch': [], 'checkbox': False, 'inode': True, 'open': True}  # level-0
    hosts = request.GET['idents'].split(',')
    plan_id = request.GET.get('plan_id', 'undefined')  # 'id': 编辑计划  'undefined': 创建计划
    hosts_nodes = []
    for host in hosts:
        host_name = Host.objects.get(ident=host).name
        disks_node, host_error = get_host_disks_nodes(request, {'ident': host, 'planId': plan_id})  # level-1
        _host_node = copy.deepcopy(host_node)
        _host_node['id'], _host_node['label'] = host, host_name
        _host_node['branch'] = disks_node
        if host_error:
            _host_node['label'] += hostErrorMsg
            _host_node['disabled'] = True
        hosts_nodes.append(_host_node)
    return hosts_nodes


def is_cluster(request):
    return request.GET.get('cluster', None) is not None


# 获取指定服务器详细信息
def getServerDetail(request):
    paramsQD = request.GET
    hostIdent = paramsQD.get('id', default='')
    if not hostIdent:
        return HttpResponse('{"r": "1", "e": "请求参数缺失：serverid"}')

    host = Host.objects.get(ident=hostIdent)

    api_response = HostSessionDisks().get(request=request, ident=hostIdent)
    if not status.is_success(api_response.status_code):
        return HttpResponse('{{"r": "1", "e": "HostSessionDisks().get() failed {}"}}'.format(api_response.status_code))

    disks = api_response.data
    disksNum = len(disks)
    disksSize = 0
    disksUsed = 0
    disksName = list()
    for disk in disks:
        disk_bytes = int(disk['bytes'])
        convert_disk_bytes = int((disk_bytes / (1024 ** 3)) * 100) / 100
        disksName.append(disk['name'] + ':容量' + str(convert_disk_bytes) + 'GB')
        disksSize += disk_bytes
        disksUsed += int(disk['used_bytes'])
    disksSize = int((disksSize / (1024 ** 3)) * 100) / 100
    disksUsed = int((disksUsed / (1024 ** 3)) * 100) / 100

    hostDetail = HostSessionInfo().get(request=request, ident=hostIdent).data
    ips = list()
    macs = list()
    for etherAdapter in hostDetail['ether_adapters']:
        for ipaddr in etherAdapter['ip_addresses']:
            if ipaddr:
                ips.append(ipaddr)
        if etherAdapter['mac']:
            macs.append(etherAdapter['mac'])

    network_transmission_type = host.network_transmission_type

    # 新增字段：lasttime：最后时间，agent_version：版本
    rsp = serversmgr.getServerInfo(request, {'ident': hostIdent})
    json_data = json.loads(rsp.content.decode('utf-8'))

    ext_info, display_name = host.ext_info, host.display_name
    volume_info = get_host_volume_info(request, hostIdent)

    auto_verify_task_list = list()
    for task in host.hosts_AutoVerifySchedule.all():
        auto_verify_task_list.append(task.name)

    retInfo = {
        "r": 0, "e": "操作成功",
        'servername': hostDetail['host']['name'],
        'pcname': hostDetail['computer_name'],
        'os': hostDetail['os_type'],
        'buildnum': hostDetail['os_version'],
        'harddisknum': str(disksNum),
        'harddiskinfo': '|'.join(disksName),
        'harddiskinfoyun': volume_info,
        'total': str(disksSize),
        'use': str(disksUsed) if is_windows_host(hostIdent) else get_linux_disk_total_used_bytes(hostIdent),
        'ip': '|'.join(ips),
        'mac': '|'.join(macs),
        'network_transmission_type': network_transmission_type,
        'lasttime': json_data.get('lasttime', '--'),
        'agent_version': json_data.get('agent_version', '--'),
        'host_ext_info': ext_info,
        'display_name': display_name,
        'home_path': hostDetail['host']['home_path'],
        'auto_verify_task_list': ','.join(auto_verify_task_list)
    }
    if is_cluster(request):
        retInfo = [
            {'id': 'show_servername', 'text': retInfo['servername']},
            {'id': 'show_pcname', 'text': retInfo['pcname']},
            {'id': 'show_ip', 'text': retInfo['ip']},
            {'id': 'show_mac', 'text': retInfo['mac']},
            {'id': 'show_os', 'text': retInfo['os']},
            {'id': 'show_buildnum', 'text': retInfo['buildnum']},
            {'id': 'show_harddisknum', 'text': retInfo['harddisknum']},
            {'id': 'show_harddiskinfo', 'text': retInfo['harddiskinfo']},
            {'id': 'show_total', 'text': retInfo['total'] + 'GB'},
            {'id': 'show_use', 'text': retInfo['use'] + 'GB'},
            {'id': 'is_encrypt', 'text': retInfo['network_transmission_type']}
        ]
    return HttpResponse(json.dumps(retInfo, ensure_ascii=False))


def get_host_volume_info(request, host_id):
    respn = HostSessionDisks().get(request=request, ident=host_id)
    if not status.is_success(respn.status_code):
        return [{"label": 'agent error', "icon": "harddisk"}]
    disks = respn.data
    is_windows = is_windows_host(host_id)
    result_list = list()
    for disk in disks:
        _logger.info("is_windows:{},disk:{}".format(is_windows, disk))
        # detail = '{name}(容量{g1}GB,已用{g2}GB,类型{type},{boot})'
        detail = '{name}　容量:{g2}/{g1}(GB)　属性:{type}' \
            .format(name=disk['name'],
                    # boot='启动盘' if disk['boot_able'] else '',
                    g1=int((disk['bytes'] / (1024 ** 3)) * 100) / 100,
                    g2=int((disk['used_bytes'] / (1024 ** 3)) * 100) / 100,
                    type=disk['partition_table_type'])
        if not is_windows:
            detail = re.sub(r':.+/', '', detail)
        if disk['dynamic_disk']:  # 动态磁盘不统计使用量
            detail = re.sub(r':.+/', '', detail)
            detail = detail + ',动态磁盘'
        result_list.append({"label": detail, "icon": "harddisk"})
    return result_list


def is_checkbox_hosts(request, infoList):
    isCluster = request.GET.get('cluster', None)
    if isCluster is None:
        return
    for host_node in infoList:
        host_node['radio'], host_node['checkbox'] = False, True


def get_host_name(host):
    if isinstance(host, Host):
        host_obj = host
    else:
        host_obj = Host.objects.get(ident=host)
    all_ip_mask_pair, system_infos = [], json.loads(host_obj.ext_info).get('system_infos', dict())
    for nic in system_infos.get('Nic', list()):
        all_ip_mask_pair += nic['IpAndMask']
    all_ips = [pair['Ip'] for pair in all_ip_mask_pair]

    if host_obj.type == Host.AGENT:
        _format_tmp = '{}{}'
    else:
        _format_tmp = '{{}}{{}}[{type_display}]'.format(type_display=host_obj.get_type_display())

    if all_ips:
        return _format_tmp.format(host_obj.display_name, '({})'.format('|'.join(set(all_ips))))
    else:
        return _format_tmp.format(host_obj.display_name, '')


def _have_backup_plan(host_id):
    if BackupTaskSchedule.objects.filter(host_id=host_id, deleted=False):
        return True
    return False


# 获取User的所有服务器
def getServerList(request):
    id = request.GET.get('id', 'root')
    no_plan = request.GET.get('noplan', '')  # 只显示没有备份计划的客户端
    bgroup = request.GET.get('group', '')  # 按分组方式显示
    input_type = request.GET.get('input_type', 'radio')  # 按分组方式显示
    include_remote_host = int(request.GET.get('include_remote_host', '0'))
    inc_nas_host = int(request.GET.get('inc_nas_host', '0'))
    inc_archive_host = int(request.GET.get('inc_import_host', '0'))  # 数据导入
    inc_offline = True if int(request.GET.get('inc_offline', '0')) == 1 else False  # 包含离线主机
    _logger.debug(
        'getServerList inc_offline:{} inc_nas_host:{} include_remote_host:{}'.format(inc_offline, inc_nas_host,
                                                                                     include_remote_host))
    id = 'root' if id == '' else id

    filter_funcs = [HostSessions.filter_deleted,
                    HostSessions.filter_verified,
                    HostSessions.filter_remote]
    filter_funcs_remote = [HostSessions.filter_deleted,
                           HostSessions.filter_not_remote]
    if not inc_offline:
        filter_funcs.append(HostSessions.filter_offline)
    if not inc_archive_host:
        filter_funcs.append(HostSessions.filter_archive)
    if inc_nas_host != 1:
        filter_funcs.append(HostSessions.filter_nas_host)
        filter_funcs_remote.append(HostSessions.filter_nas_host)
    if id == 'ui_hasHosts' and bgroup == 'group':
        # 过滤掉已在组中的主机
        filter_funcs.append(HostSessions.filter_in_group)
    if no_plan == 'noplan':
        filter_funcs.append(HostSessions.filter_no_plan)
        filter_funcs_remote.append(HostSessions.filter_no_plan)

    # 判断该User是否有从属的Hosts(当前处于连接状态)
    if id == 'root':

        host_exists = HostSessions().get(request=request, filter_funcs=filter_funcs,
                                         check_exists=True).data['exists']
        remote_exists = False
        if include_remote_host == 1:
            remote_exists = HostSessions().get(request=request, filter_funcs=filter_funcs_remote,
                                               check_exists=True).data['exists']

        jsonStr1 = '[{"id": "ui_hasHosts","branch":[],"inode":true,"open":true, "label": "客户端列表","icon":"pcroot","radio":false}]'
        jsonStr2 = '[{"id": "ui_notHosts","branch":[],"inode":false, "label": "没有在线客户端","icon":"pcroot","radio":false}]'

        if bgroup == 'group' and (not host_exists) and (not remote_exists):
            host_groups = HostGroup.objects.filter(user_id=request.user.id)
            for host_group in host_groups:
                if host_group.hosts.count():
                    return HttpResponse(jsonStr1)

        return HttpResponse(jsonStr1 if host_exists or remote_exists else jsonStr2)

    # 获取该User从属的Hosts
    if id == 'ui_hasHosts':
        attr_getters = [('name1', get_host_name)]

        hosts = HostSessions().get(request=request, filter_funcs=filter_funcs, attr_getters=attr_getters).data
        remote_hosts = list()
        if include_remote_host == 1:
            remote_hosts = HostSessions().get(request=request, filter_funcs=filter_funcs_remote).data
        infoList = list()
        for host in hosts:
            host['name'] = host['name1']
            inode = True
            if host['is_nas_host']:
                inode = False
            infoList.append({'id': host['ident'], 'icon': 'pc', input_type: 'true',
                             "inode": inode, "open": False, 'label': host['name'], 'type': host['type']})
        for host in remote_hosts:
            inode = True
            if host['is_nas_host']:
                inode = False
            remote_lable = '{}上的主机'.format(json.loads(host['aio_info']).get('ip', 'unknown'))
            infoList.append({'id': host['ident'], 'icon': 'pc', input_type: 'true', 'type': host['type'],
                             "inode": inode, "open": False, 'label': '[{}]{}'.format(remote_lable, host['name'])})

        if bgroup == 'group':
            host_groups = HostGroup.objects.filter(user_id=request.user.id)
            for host_group in host_groups:
                if host_group.hosts.count():
                    if host_group.hosts.first().type == Host.REMOTE_AGENT:
                        continue
                    if no_plan == 'noplan' and host_group.hostgroup.count():
                        continue
                    infoList.append(
                        {'id': 'group_{}'.format(host_group.id), 'icon': 'group', input_type: 'true', 'type': 'group',
                         "inode": True, "open": False,
                         'label': '{}'.format(host_group.name)})

        is_checkbox_hosts(request, infoList)
        return HttpResponse(json.dumps(infoList, ensure_ascii=False))

    # 获取分组中的客户端
    if id.startswith('group_'):
        id = id[6:]
        infoList = list()
        host_group = HostGroup.objects.get(id=id)
        for host in host_group.hosts.all():
            infoList.append(
                {'id': '{}'.format(host.ident), 'icon': 'pc', input_type: False, 'type': host.type,
                 "inode": True, "open": False,
                 'label': '{}'.format(host.name)})
        return HttpResponse(json.dumps(infoList, ensure_ascii=False))

    # 获取指定Host的磁盘信息(by hostIdent)
    if len(id) >= 30:
        disks = HostSessionDisks().get(request=request, ident=id).data
        infoList = list()
        is_windows = is_windows_host(id)
        for disk in disks:
            detail = '{name}(容量{g1}GB,已用{g2}GB,类型{type},{boot})' \
                .format(name=disk['name'],
                        boot='启动盘' if disk['boot_able'] else '',
                        g1=int((disk['bytes'] / (1024 ** 3)) * 100) / 100,
                        g2=int((disk['used_bytes'] / (1024 ** 3)) * 100) / 100,
                        type=disk['partition_table_type'])
            if not is_windows:
                detail = re.sub(r'已用.+GB,', '', detail)
            if disk['dynamic_disk']:  # 动态磁盘不统计使用量
                detail = re.sub(r'已用.+GB,', '', detail)
            infoList.append({"label": detail, "icon": "harddisk"})
        return HttpResponse(json.dumps(infoList, ensure_ascii=False))

    return HttpResponse('{"r": "1", "e": "请求参数异常"}')


# 创建/修改计划时，检查总管理的主机数, cdp保护的主机数
def check_authorize_at_manage_host(request, api_request=None):
    if not api_request:
        host_ident = request.POST.get('serverid')
        scheduleType = request.POST['bakschedule']
        backupSourceType = int(request.POST.get('backuptype', -1))
    else:
        host_ident = api_request.get('serverid')
        scheduleType = api_request['bakschedule']
        backupSourceType = int(api_request.get('backuptype', -1))

    if backupSourceType == BackupTaskSchedule.BACKUP_FILES:
        # NAS客户端
        return True, ''

    host_type = authorize_init.get_host_type(host_ident)

    hosts_in_manage, hosts_server_in_manage, hosts_pc_in_manage, hosts_in_cdp, hosts_virtual_server_in_manage, hosts_virtual_pc_in_manage, proxy_agent_in_manage = authorize_init.get_license_BackupTaskSchedule()

    # 读取授权文件

    # 检查总管理主机数
    if is_functional_available('manage_host_num'):
        clret = check_license('manage_host_num')
        if clret.get('r', 0) != 0:
            return False, clret['e']
        val_manage = authorize_init.get_license_int_value('manage_host_num')
        if val_manage is None:
            return False, '读取授权文件异常'
        if len(hosts_in_manage) >= int(val_manage) and host_ident not in hosts_in_manage:
            return False, '管理主机数超过授权允许值({0}台), 目前{1}台正被管理'.format(val_manage, len(hosts_in_manage))

    if host_type == 'server' and is_functional_available('manage_host_num_server'):
        clret = check_license('manage_host_num_server')
        if clret.get('r', 0) != 0:
            return False, clret['e']
        val_manage = authorize_init.get_license_int_value('manage_host_num_server')
        if val_manage is None:
            return False, '读取授权文件异常'
        if len(hosts_server_in_manage) >= int(val_manage) and host_ident not in hosts_server_in_manage:
            return False, '管理主机数（服务器版）超过授权允许值({0}台), 目前{1}台正被管理'.format(val_manage, len(hosts_server_in_manage))

    if host_type == 'pc' and is_functional_available('manage_host_num_pc'):
        clret = check_license('manage_host_num_pc')
        if clret.get('r', 0) != 0:
            return False, clret['e']
        val_manage = authorize_init.get_license_int_value('manage_host_num_pc')
        if val_manage is None:
            return False, '读取授权文件异常'
        if len(hosts_pc_in_manage) >= int(val_manage) and host_ident not in hosts_pc_in_manage:
            return False, '管理主机数（桌面版）超过授权允许值({0}台), 目前{1}台正被管理'.format(val_manage, len(hosts_pc_in_manage))

    if host_type == 'virtual_host_server' and is_functional_available('manage_host_num_virtual_sever'):
        clret = check_license('manage_host_num_virtual_sever')
        if clret.get('r', 0) != 0:
            return False, clret['e']
        val_manage = authorize_init.get_license_int_value('manage_host_num_virtual_sever')
        if val_manage is None:
            return False, '读取授权文件异常'
        if len(hosts_virtual_server_in_manage) >= int(val_manage) and host_ident not in hosts_virtual_server_in_manage:
            return False, '管理虚拟主机数（服务器版）超过授权允许值({0}台), 目前{1}台正被管理'.format(val_manage,
                                                                          len(hosts_virtual_server_in_manage))

    if host_type == 'virtual_host_pc' and is_functional_available('manage_host_num_virtual_pc'):
        clret = check_license('manage_host_num_virtual_pc')
        if clret.get('r', 0) != 0:
            return False, clret['e']
        val_manage = authorize_init.get_license_int_value('manage_host_num_virtual_pc')
        if val_manage is None:
            return False, '读取授权文件异常'
        if len(hosts_virtual_pc_in_manage) >= int(val_manage) and host_ident not in hosts_virtual_pc_in_manage:
            return False, '管理虚拟主机数（桌面版）超过授权允许值({0}台), 目前{1}台正被管理'.format(val_manage, len(hosts_virtual_pc_in_manage))

    # 检查cdp保护主机数
    if scheduleType == '1':
        val_cdp = authorize_init.get_license_int_value('cdp_host_num')
        if val_cdp is None:
            return False, '读取授权文件异常'
        if len(hosts_in_cdp) >= int(val_cdp) and host_ident not in hosts_in_cdp:
            return False, 'CDP保护主机数超过授权允许值({0}台), 目前{1}台正处于CDP保护'.format(val_cdp, len(hosts_in_cdp))

    if host_type == 'proxy_agent' and is_functional_available('vmwarebackup'):
        clret = check_license('vmwarebackup')
        if clret.get('r', 0) != 0:
            return False, clret['e']
        val_manage = authorize_init.get_license_int_value('vmwarebackup')
        if val_manage is None:
            return False, '读取授权文件异常'
        if len(proxy_agent_in_manage) >= int(val_manage) and host_ident not in proxy_agent_in_manage:
            return False, '无代理备份允许值({0}台), 目前{1}台正被管理'.format(val_manage, len(proxy_agent_in_manage))

    return True, ''


# excludes: {'disk': [guid|lable], 'vol': [guid|lable]}
def convert_to_type_and_guid_lable(excludes):
    exclude_list = []
    for disk in excludes['disk']:
        guid, lable = disk.split('|')[0], disk.split('|')[1]
        exclude_list.append({'exclude_type': 1, 'exclude_info': guid, 'lable': lable})
    for volume in excludes['vol']:
        guid, lable = volume.split('|')[0], volume.split('|')[1]
        exclude_list.append({'exclude_type': 2, 'exclude_info': guid, 'lable': lable})
    return exclude_list


def get_normal_backup_interval_secs(paramsQDict):
    interval = int(paramsQDict.get('timeinterval', -1))
    unit = paramsQDict['intervalUnit']
    return interval * {'min': 60, 'hour': 3600, 'day': 24 * 3600}[unit]


# zip_tmp_path: zip在AIO的临时路径
# zip_path: zip在AIO的永久路径
# zip_file: zip在本地路径(界面使用)
def get_shell_infos_when_enable_shell(shell_infos_str, plan_obj=None):
    shell_infos = json.loads(shell_infos_str)

    # 1.创建计划流程
    if plan_obj is None:
        shell_infos['zip_path'] = file_utils.move_tmp_file(shell_infos['zip_tmp_path'])
        return json.dumps(shell_infos)

    # 2.更改计划流程
    if shell_infos['zip_tmp_path'] == 'use_last_zip':  # 当前没有上传文件, 针对上一次上传了的情况
        ext_config = json.loads(plan_obj.ext_config)
        shellInfoStr = ext_config['shellInfoStr']
        shell_infos['zip_path'] = json.loads(shellInfoStr)['zip_path']
    else:  # 当前有选择上传文件
        shell_infos['zip_path'] = file_utils.move_tmp_file(shell_infos['zip_tmp_path'])

    return json.dumps(shell_infos)


def _check_duplicates_functional(SystemFolderDup):
    if is_functional_available('remove_duplicates_in_system_folder'):
        return {'r': 0, 'e': 'OK'}
    if SystemFolderDup:
        return {'r': 1, 'e': '去重功能未授权。'}
    return {'r': 0, 'e': 'OK'}


def _create_nas_host(request, nas_path):
    ret = {'r': 0}
    login_datetime = timezone.now()
    with open(r'/sbin/aio/box_dashboard/box_dashboard/host_fake_info/host_ext_info.json', 'r') as f:
        ext_fake_info = json.load(f)
    ext_fake_info['nas_path'] = nas_path
    ext_info_str = json.dumps(ext_fake_info, ensure_ascii=False)
    hosts = Host.objects.filter(type=Host.NAS_AGENT)
    for host in hosts:
        ext_info = json.loads(host.ext_info)
        if nas_path == ext_info.get('nas_path'):
            Host.objects.filter(id=host.id).update(login_datetime=login_datetime, ext_info=ext_info_str)
            _logger.info('_create_nas_host:request.user - {}'.format(request.user.id))
            if host.user is None:
                host.user = request.user
                host.save()
            if host.user and host.user.id == request.user.id:
                ret['host_id'] = host.id
                if host.is_deleted:
                    host.set_delete(False)
                return ret
            else:
                ret['r'] = 1
                ret['e'] = '该NAS客户端名称为{}，未分配给当前用户，请联系管理员分配客户端'.format(host.name)
                return ret

    display_name = 'NAS客户端({})'.format(nas_path)
    host = Host.objects.create(ident=uuid.uuid4().hex, display_name=display_name, user=request.user,
                               ext_info=ext_info_str,
                               type=Host.NAS_AGENT, login_datetime=login_datetime)
    ret['host_id'] = host.id
    return ret


def _get_groupbackup_taskname(taskname):
    gs = GroupBackupTaskSchedule.objects.filter(name=taskname)
    i = 1
    tmptaskname = taskname
    while gs:
        tmptaskname = '{}({})'.format(taskname, i)
        gs = GroupBackupTaskSchedule.objects.filter(name=tmptaskname)
        i = i + 1
    return tmptaskname


def _get_nas_max_space_actual(val, unit):
    """
    Bytes, 该值会实际传入底层
    """
    if unit not in ['GB', 'TB'] or val < 1:
        return -1

    val_GB = val if unit == 'GB' else val * 1024
    val_GB = min(val_GB * 1.05, val_GB + 500)

    return int(val_GB * 1024 ** 3)


# 创建计划, 更改计划
def createModifyPlan(request):
    paramsQDict = request.POST
    # host数据库id
    hostIdent = paramsQDict.get('serverid', default='')
    if hostIdent.startswith('group_'):
        err_list = list()
        if 'taskid' in paramsQDict:
            # 更改计划
            is_create_plan = False
            gs = GroupBackupTaskSchedule.objects.get(id=hostIdent[6:])
            host_group_id = gs.host_group.id
        else:
            # 创建计划
            is_create_plan = True
            host_group_id = hostIdent[6:]
            gs = GroupBackupTaskSchedule.objects.create(name=_get_groupbackup_taskname(request.POST['taskname']),
                                                        user_id=request.user.id,
                                                        host_group_id=host_group_id,
                                                        type=GroupBackupTaskSchedule.SCHEDULE_TYPE_BACKUP_TASK)

        host_group = HostGroup.objects.get(id=host_group_id)
        hosts = host_group.hosts.all()
        org_taskname = request.POST['taskname']
        for host in hosts:
            request.POST._mutable = True
            request.POST['serverid'] = host.ident
            if 'taskid' not in paramsQDict:
                # 创建计划
                request.POST['taskname'] = '{}_{}'.format(org_taskname, host.name)[0:255]
            if not is_create_plan:
                request.POST['taskid'] = gs.schedules.filter(host_id=host.id).first().id
            request.POST._mutable = False
            ret = createModifyPlan(request)
            if is_create_plan and status.is_success(ret.status_code):
                json_ret = json.loads(ret.content.decode('utf-8'))
                if int(json_ret.get('r', 1)) != 0:
                    err_list.append('{}，{}'.format(request.POST['taskname'], json_ret.get('e', '内部错误')))
                gs.schedules.add(BackupTaskSchedule.objects.get(id=json_ret['plan_id']))
        if len(err_list):
            return HttpResponse(json.dumps({"r": 1, "e": "<br>".join(err_list)}, ensure_ascii=False))
        return HttpResponse(json.dumps({"r": 0, "e": ""}, ensure_ascii=False))

    result = check_authorize_at_manage_host(request)
    if not result[0]:
        return HttpResponse(json.dumps({"r": "1", "e": result[1], "list": []}))
    # 备份源类型
    backupSourceType = int(paramsQDict.get('backuptype', default=-1))  # 1/2/3/4
    # 所选存储结点的ident
    storage_node_ident = paramsQDict.get('storagedevice', default=-1)
    if backupSourceType == BackupTaskSchedule.BACKUP_FILES:
        hostId = -1
    else:
        api_response = HostSessionInfo().get(request=request, ident=hostIdent)
        if not status.is_success(api_response.status_code):
            return HttpResponse(json.dumps({"r": 1, "e": "{}".format(get_response_error_string(api_response))}))
        hostId = api_response.data['host']['id'] if hostIdent else -1
    # 计划名称
    planName = paramsQDict.get('taskname', default='')
    # 储存设备类型 （没意义了）
    storageDeviceType = 1
    # 备份数据保留期限 天, 月
    backupDataHoldDays = int(paramsQDict.get('retentionperiod', default=-1))
    # 空间剩余XGB时，自动清理
    autoCleanDataWhenlt = int(paramsQDict.get('cleandata', default=-1))
    # CDP数据保留期限 天
    cdpDataHoldDays = int(paramsQDict.get('cdpperiod', default=7))
    # 带宽限速 MB
    usemaxBroadband = float(paramsQDict.get('usemaxbandwidth', default=1))
    maxBroadband = float(paramsQDict.get('maxbandwidth', default=-1))
    if usemaxBroadband == 0:
        maxBroadband = -1
    # 立即备份吗
    immediateBackup = int(paramsQDict.get('immediately', default=-1))  # 0/1
    # 该计划的时间表类型
    scheduleType = int(paramsQDict.get('bakschedule', default=-1))  # 1/2/3/4/5
    # CDP方式：同步，异步
    cdpSynchAsynch = int(paramsQDict.get('cdptype', default=1))  # 0/1
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
    # 至少保留N个个备份点
    backupLeastNumber = int(paramsQDict.get('keepingpoint', default=-1))  # 0-999个
    # 数据传输 是否加密 1 加密 0 不加密
    isencipher = int(paramsQDict.get('isencipher', default=0))  # 0/1
    backupmode = int(paramsQDict.get('backupmode', default=2))
    # 系统文件夹，去重
    SystemFolderDup = paramsQDict['SystemFolderDup'] == '1'  # '1'、'0'
    # 磁盘、分区排除情况
    excludeDetails = json.loads(paramsQDict['excludeDetails'])  # {'disk': [guid|lable], 'vol': [guid|lable]}
    excludeList = convert_to_type_and_guid_lable(excludeDetails)
    # 脚本信息
    shell_infos = paramsQDict.get('shell_infos', None)  # None: 未启用脚本功能
    # 备份重试
    backup_retry_or = json.loads(paramsQDict.get('backup_retry', '{"enable":true,"count":"5","interval":"10"}'))
    # 开启/禁用
    enabled = paramsQDict.get('enabled', True)
    # 1.自动 2. SAN 3. HotAdd 4. NBD
    vmware_tranport_modes = int(paramsQDict.get('vmware_tranport_modes', '1'))
    # 1.静默 0.不静默
    vmware_quiesce = int(paramsQDict.get('vmware_quiesce', '1'))

    # NAS计划相关参数
    nas_protocol = paramsQDict.get('nas_protocol')
    nas_username = paramsQDict.get('nas_username')
    nas_password = paramsQDict.get('nas_password')
    nas_path = paramsQDict.get('nas_path')
    nas_exclude_dir = paramsQDict.get('nas_exclude_dir')
    nas_path = paramsQDict.get('nas_path')
    enum_threads = paramsQDict.get('enum_threads', -1)
    sync_threads = paramsQDict.get('sync_threads', -1)
    cores = paramsQDict.get('cores', -1)
    memory_mbytes = paramsQDict.get('memory_mbytes', -1)
    net_limit = paramsQDict.get('net_limit', -1)
    enum_level = paramsQDict.get('enum_level', -1)
    sync_queue_maxsize = paramsQDict.get('sync_queue_maxsize', -1)
    nas_max_space_val = paramsQDict.get('nas_max_space_val', -1)  # 正整数
    nas_max_space_unit = paramsQDict.get('nas_max_space_unit', '')  # GB、TB
    nas_max_space_actual = _get_nas_max_space_actual(int(nas_max_space_val), nas_max_space_unit)

    if backupSourceType == BackupTaskSchedule.BACKUP_FILES:
        # NAS
        ret = _create_nas_host(request, nas_path)
        if ret['r'] != 0:
            return HttpResponse(json.dumps(ret, ensure_ascii=False))
        hostId = ret['host_id']

    backup_retry = {
        'enable': backup_retry_or['enable'],
        'count': int(backup_retry_or['count']),
        'interval': int(backup_retry_or['interval'])
    }
    # 备份数据保留期 单位：day, month, None
    data_keeps_deadline_unit = paramsQDict.get('retentionperiod_unit', None)
    if data_keeps_deadline_unit == 'day' or data_keeps_deadline_unit is None:
        backupDataHoldDays = backupDataHoldDays * 1
    if data_keeps_deadline_unit == 'month':
        backupDataHoldDays = backupDataHoldDays * 30
    # 线程数
    thread_count = int(paramsQDict.get('thread_count', '4'))
    # 备份时候IO占用
    BackupIOPercentage = int(paramsQDict.get('BackupIOPercentage', '30'))

    # BackupTaskSchedule的ext_config字段
    extConfig = {'backupDataHoldDays': backupDataHoldDays,  # 备份数据保留期, 天
                 'autoCleanDataWhenlt': autoCleanDataWhenlt,
                 'cdpDataHoldDays': cdpDataHoldDays,
                 'maxBroadband': maxBroadband,
                 'cdpSynchAsynch': cdpSynchAsynch,
                 'backupDayInterval': backupDayInterval,  # 按间隔时间: 秒数
                 'daysInWeek': daysInWeek,
                 'daysInMonth': daysInMonth,
                 'backupLeastNumber': backupLeastNumber,
                 'isencipher': isencipher,
                 'incMode': backupmode,
                 'exclude': excludeList,  # [{'exclude_info': 'vol9_guid', 'lable': 'vol9', 'exclude_type': 2}]
                 'removeDuplicatesInSystemFolder': SystemFolderDup,
                 'IntervalUnit': paramsQDict['intervalUnit'],  # 按间隔时间, 单位: 'min', 'hour', 'day'
                 'backup_retry': backup_retry,
                 'data_keeps_deadline_unit': data_keeps_deadline_unit,
                 'diskreadthreadcount': thread_count,
                 'vmware_tranport_modes': vmware_tranport_modes,
                 'vmware_quiesce': vmware_quiesce,
                 'BackupIOPercentage': BackupIOPercentage,
                 'nas_protocol': nas_protocol,
                 'nas_username': nas_username,
                 'nas_password': nas_password,
                 'nas_exclude_dir': nas_exclude_dir,
                 'nas_path': nas_path,
                 'enum_threads': int(enum_threads),
                 'sync_threads': int(sync_threads),
                 'cores': int(cores),
                 'memory_mbytes': int(memory_mbytes),
                 'net_limit': int(net_limit),
                 'enum_level': int(enum_level),
                 'sync_queue_maxsize': int(sync_queue_maxsize),
                 'nas_max_space_val': int(nas_max_space_val),
                 'nas_max_space_unit': nas_max_space_unit,
                 'nas_max_space_actual': int(nas_max_space_actual),
                 }
    _logger.info('createModifyPlan extConfig:{}'.format(extConfig))
    # 启用了脚本功能: 创建流程, 更改流程
    if shell_infos:
        plan_obj = BackupTaskSchedule.objects.get(id=paramsQDict['taskid']) if ('taskid' in paramsQDict) else None
        extConfig.update({'shellInfoStr': get_shell_infos_when_enable_shell(shell_infos, plan_obj)})

    extConfig = json.dumps(extConfig, ensure_ascii=False)
    code = 0

    clret = _check_duplicates_functional(SystemFolderDup)
    if clret.get('r', 0) != 0:
        return HttpResponse(json.dumps(clret, ensure_ascii=False))

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
        respn = BackupTaskScheduleSetting().put(request=request, backup_task_schedule_id=planId, api_request=params)
        if respn.status_code == status.HTTP_202_ACCEPTED:
            infoCM = '修改计划成功'
            desc = {'操作': '修改备份计划', '任务ID': str(planId), '任务名称': planName}
        else:
            code = 1
            infoCM = '修改计划失败,{}'.format(respn.data)
    # 创建计划
    else:
        params = {"host": hostId, "name": planName, "storage_device_type": storageDeviceType,
                  "cycle_type": scheduleType, "plan_start_date": planStartDate,
                  "backup_source_type": backupSourceType, "ext_config": extConfig,
                  'storage_node_ident': storage_node_ident, 'enabled': enabled}
        respn = BackupTaskSchedules().post(request=request, api_request=params)
        if respn.status_code == status.HTTP_201_CREATED:
            infoCM = '创建计划成功'
            desc = {'操作': '创建备份计划', '任务ID': str(hostId), '任务名称': planName}
        else:
            code = 1
            infoCM = '创建计划失败,{}'.format(respn.data)
    # 是否立即执行该计划一次
    infoEcx = ''
    plan_id = respn.data['id']
    if immediateBackup > 0:
        curPlanid = respn.data['id']
        api_request = {'type': backupmode}
        respn = BackupTaskScheduleExecute().post(request=request, backup_task_schedule_id=curPlanid,
                                                 api_request=api_request)

        if respn.status_code == status.HTTP_201_CREATED:
            infoEcx = '立即执行计划成功'
        else:
            code = 1
            infoEcx = '立即执行计划失败,{}'.format(respn.data)

    if (infoCM == '修改计划成功') or (infoCM == '创建计划成功'):
        desc.update({'立即执行': infoEcx if infoEcx else '否'})
        SaveOperationLog(
            request.user, OperationLog.TYPE_BACKUP, json.dumps(desc, ensure_ascii=False), get_operator(request))

    # 返回给界面信息
    infoStr = '{} {}'.format(infoCM, infoEcx)
    return HttpResponse(json.dumps({"r": code, "e": "{}".format(infoStr), "plan_id": plan_id}, ensure_ascii=False))


# 只用于yun edit
def createModifyPlan_for_yun_before(request):
    params_dict = json.loads(request.POST['params'])
    result_yun = {'r': 0, 'e': '修改成功'}
    perform = list()
    try:
        with transaction.atomic():
            for paramsqdict in params_dict:
                result = createModifyPlan_for_yun(paramsqdict)
                perform.append(result)
                if result['r']:
                    raise Exception(result['e'])
    except Exception as e:
        result_yun = {'r': 1, 'e': str(e)}
    return HttpResponse(json.dumps(result_yun))


def _set_host_encrypt_trans(val, host_ident):
    host = Host.objects.get(ident=host_ident)
    if val in [0, '0']:
        host.network_transmission_type = 2
    else:
        host.network_transmission_type = 1
    host.save(update_fields=['network_transmission_type'])


# 只用于yun edit
def createModifyPlan_for_yun(paramsqdict):
    try:
        result = check_authorize_at_manage_host(request=None, api_request=paramsqdict)
        if not result[0]:
            return {"r": "1", "e": result[1], "list": []}
        paramsQDict = paramsqdict
        # # 备份源类型
        # backupSourceType = int(paramsQDict.get('backuptype', default=-1))  # 1/2/3/4
        # host数据库id
        hostIdent = paramsQDict.get('serverid', '')
        # 所选存储结点的ident
        storage_node_ident = paramsQDict.get('storagedevice', -1)
        api_response = HostSessionInfo().get(request=None, ident=hostIdent)
        if not status.is_success(api_response.status_code):
            return {"r": 1, "e": "{}".format(get_response_error_string(api_response))}
        # 计划名称
        planName = paramsQDict.get('taskname', '')
        # 储存设备类型 （没意义了）
        storageDeviceType = 1
        # 备份数据保留期限 天, 月
        backupDataHoldDays = int(paramsQDict.get('retentionperiod', -1))
        # 空间剩余XGB时，自动清理
        autoCleanDataWhenlt = int(paramsQDict.get('cleandata', -1))
        # CDP数据保留期限 天
        cdpDataHoldDays = int(paramsQDict.get('cdpperiod', 7))
        # 带宽限速 MB
        usemaxBroadband = float(paramsQDict.get('usemaxbandwidth', 1))
        maxBroadband = float(paramsQDict.get('maxbandwidth', -1))
        if usemaxBroadband == 0:
            maxBroadband = -1
        # 该计划的时间表类型
        scheduleType = int(paramsQDict.get('bakschedule', -1))  # 1/2/3/4/5
        # CDP方式：同步，异步
        cdpSynchAsynch = int(paramsQDict.get('cdptype', 1))  # 0/1
        # 计划开始日期
        planStartDate = paramsQDict.get('starttime', None)
        # 按间隔时间: 分,时,天
        backupDayInterval = get_normal_backup_interval_secs(paramsQDict)
        # 每周模式
        daysInWeek = paramsQDict.get('perweek', [])  # [1, 3, 5]
        daysInWeek = list(map(int, daysInWeek))
        # 每月模式
        daysInMonth = paramsQDict.get('monthly', [])  # [1, 18, 27, 31]
        daysInMonth = list(map(int, daysInMonth))
        # 至少保留N个个备份点
        backupLeastNumber = int(paramsQDict.get('keepingpoint', -1))  # 0-999个
        # 数据传输 是否加密 1 加密 0 不加密
        isencipher = int(paramsQDict.get('isencipher', 0))  # 0/1
        backupmode = int(paramsQDict.get('backupmode', 2))
        # 系统文件夹，去重
        SystemFolderDup = paramsQDict['SystemFolderDup'] in ['1', 1]  # '1'、'0'
        # 磁盘、分区排除情况
        excludeDetails = json.loads(paramsQDict['excludeDetails'])  # {'disk': [guid|lable], 'vol': [guid|lable]}
        excludeList = convert_to_type_and_guid_lable(excludeDetails)
        # 脚本信息
        shell_infos = paramsQDict.get('shell_infos', None)  # None: 未启用脚本功能
        # 备份重试
        backup_retry_or = json.loads(paramsQDict.get('backup_retry', '{"enable":true,"count":"5","interval":"10"}'))
        backup_retry = {
            'enable': backup_retry_or['enable'],
            'count': int(backup_retry_or['count']),
            'interval': int(backup_retry_or['interval'])
        }
        # 备份数据保留期 单位：day, month, None
        data_keeps_deadline_unit = paramsQDict.get('retentionperiod_unit', None)
        if data_keeps_deadline_unit == 'day' or data_keeps_deadline_unit is None:
            backupDataHoldDays = backupDataHoldDays * 1
        if data_keeps_deadline_unit == 'month':
            backupDataHoldDays = backupDataHoldDays * 30
        # 线程数
        thread_count = int(paramsQDict.get('thread_count', '4'))

        # BackupTaskSchedule的ext_config字段
        extConfig = {'backupDataHoldDays': backupDataHoldDays,  # 备份数据保留期, 天
                     'autoCleanDataWhenlt': autoCleanDataWhenlt,
                     'cdpDataHoldDays': cdpDataHoldDays,
                     'maxBroadband': maxBroadband,
                     'cdpSynchAsynch': cdpSynchAsynch,
                     'backupDayInterval': backupDayInterval,  # 按间隔时间: 秒数
                     'daysInWeek': daysInWeek,
                     'daysInMonth': daysInMonth,
                     'backupLeastNumber': backupLeastNumber,
                     'isencipher': isencipher,
                     'incMode': backupmode,
                     'exclude': excludeList,  # [{'exclude_info': 'vol9_guid', 'lable': 'vol9', 'exclude_type': 2}]
                     'removeDuplicatesInSystemFolder': SystemFolderDup,
                     'IntervalUnit': paramsQDict['intervalUnit'],  # 按间隔时间, 单位: 'min', 'hour', 'day'
                     'backup_retry': backup_retry,
                     'data_keeps_deadline_unit': data_keeps_deadline_unit,
                     'diskreadthreadcount': thread_count
                     }

        # 启用了脚本功能: 创建流程, 更改流程
        if shell_infos:
            plan_obj = BackupTaskSchedule.objects.get(id=paramsQDict['taskid']) if ('taskid' in paramsQDict) else None
            extConfig.update({'shellInfoStr': get_shell_infos_when_enable_shell(shell_infos, plan_obj)})

        extConfig = json.dumps(extConfig, ensure_ascii=False)
        # 更改计划(禁止修改cdp - syn / cdp - asyn等)
        assert 'taskid' in paramsQDict
        result = {'r': 0, 'e': '修改计划成功'}
        plan_id = int(paramsQDict['taskid'])
        params = {
            'name': planName,
            'cycle_type': scheduleType,
            'plan_start_date': planStartDate,
            'ext_config': extConfig,
            'storage_node_ident': storage_node_ident
        }

        respn = BackupTaskScheduleSetting().put(request=None, backup_task_schedule_id=plan_id,
                                                api_request=params)
        if respn.status_code != status.HTTP_202_ACCEPTED:
            result = {'r': 1, 'e': '修改计划失败'}

        _set_host_encrypt_trans(isencipher, hostIdent)
        return result
    except Exception as e:
        _logger.warning('createModifyPlan_for_yun error:{}'.format(e), exc_info=True)


def change_node_about_yun(request):
    # old_node = request.GET.get('old_node') 不使用的参数(暂留)
    new_node = request.GET.get('new_node')
    tenants = json.loads(request.GET.get('contain_tenant'))
    try:
        BackupTaskScheduleSetting.change_node_for_yun(new_node, tenants)
        desc = {'e': '修改存储节点成功', 'r': 0}
    except Exception as e:
        desc = {'e': '修改存储节点失败', 'r': 1, 'describe': str(e)}
    return HttpResponse(json.dumps(desc))


# 该用户的全部存储结点，返回能够用于备份的节点
def get_user_available_quotas(request):
    # 所有存储结点的id
    nodes = StorageNode.objects.filter(deleted=False).all()
    nodes_id = [node.id for node in nodes]

    # 所有"存储结点"的，全部"用户配额"信息
    nodes_users_quota = list()
    for node_id in nodes_id:
        resp = QuotaManage().get(request=request, api_request={'node_id': node_id})
        if resp.status_code == status.HTTP_200_OK:
            nodes_users_quota += resp.data

    user_quotas = filter(lambda quota: quota['user_id'] == request.user.id, nodes_users_quota)
    user_quotas = filter(lambda quota: quota['available_size'] > xdata.HOST_BACKUP_FORBID_SIZE_MB, user_quotas)
    return [{'free_mb': quota['available_size'], 'node_id': quota['node_id']} for quota in user_quotas]


# 创建计划时，获取存储单元
def getStorageDevice(request):
    available_quotas = get_user_available_quotas(request)

    if not available_quotas:
        return HttpResponse(json.dumps([{'name': '无可用存储单元', 'value': -1, 'id': -1}]), status.HTTP_200_OK)

    ret_info = []
    for quota in available_quotas:
        node = StorageNode.objects.get(id=quota['node_id'])
        ret_info.append({'name': node.name, 'value': node.ident, 'free': quota['free_mb']})

    ret_info.sort(key=lambda x: x['name'])
    return HttpResponse(content=json.dumps(ret_info), status=status.HTTP_200_OK)


# 主页容量，获取存储单元：未分配，正常
def getstorage(request):
    ret_info = list()
    for node in set(StorageNode.objects.filter(deleted=False, available=True, userquotas__user__id=request.user.id)):
        ret_info.append({'name': node.name, 'value': node.ident, 'id': node.id})

    ret_info = [{'name': '未分配', 'value': -1, 'id': -1}] if not ret_info else ret_info
    return HttpResponse(content=json.dumps(ret_info), status=status.HTTP_200_OK)


def FmtWeek(week):
    weekvec = list()
    if week & pow(2, 0):
        weekvec.append('星期一')
    if week & pow(2, 1):
        weekvec.append('星期二')
    if week & pow(2, 2):
        weekvec.append('星期三')
    if week & pow(2, 3):
        weekvec.append('星期四')
    if week & pow(2, 4):
        weekvec.append('星期五')
    if week & pow(2, 5):
        weekvec.append('星期六')
    if week & pow(2, 6):
        weekvec.append('星期日')
    return '、'.join(weekvec)


def FmtWeek2(week):
    weekvec = list()
    for i in range(1, 7 + 1):
        if week & pow(2, i - 1):
            weekvec.append(str(i))
    return ','.join(weekvec)


def FmtMonthly(monthly):
    monthvec = list()
    for i in range(1, 31 + 1):
        if monthly & pow(2, i - 1):
            monthvec.append(str(i))
    return ','.join(monthvec)


# 新建、编辑、导入策略时调用
def addpolicy(user, policy):
    policyname = policy['name'] if 'name' in policy else 'none'
    cycletype = policy['cycletype'] if 'cycletype' in policy else '1'
    retentionperiod = policy['retentionperiod'] if 'retentionperiod' in policy else '30'
    cleandata = policy['cleandata'] if 'cleandata' in policy else '4'
    cdpperiod = policy['cdpperiod'] if 'cdpperiod' in policy else '7'
    cdptype = policy['cdptype'] if 'cdptype' in policy else '1'
    usemaxbandwidth = policy['usemaxbandwidth'] if 'usemaxbandwidth' in policy else '0'
    maxbandwidth = policy['maxbandwidth'] if 'maxbandwidth' in policy else '1000'
    keepingpoint = policy['keepingpoint'] if 'keepingpoint' in policy else '5'
    isencipher = policy['isencipher'] if 'isencipher' in policy else '0'
    backupmode = policy['backupmode'] if 'backupmode' in policy else '2'

    starttime = policy['starttime'] if 'starttime' in policy else '2016-09-25 10:25:24'
    timeinterval = policy['timeinterval'] if 'timeinterval' in policy else '1'
    intervalUnit = policy['intervalUnit'] if 'intervalUnit' in policy else 'day'
    retentionperiod_unit = policy['retentionperiod_unit'] if 'retentionperiod_unit' in policy else 'month'
    thread_count = policy['thread_count'] if 'thread_count' in policy else '1'

    perweek = policy['perweek'] if 'perweek' in policy else [1]
    monthly = policy['monthly'] if 'monthly' in policy else [1]

    cycletype = int(cycletype)
    retentionperiod = int(retentionperiod)
    cleandata = int(cleandata)
    usemaxbandwidth = int(usemaxbandwidth)
    maxbandwidth = int(maxbandwidth)
    keepingpoint = int(keepingpoint)
    isencipher = int(isencipher)
    backupmode = int(backupmode)
    isdup = policy['removeDup']

    policy = taskpolicy()
    policy.user = user
    policy.name = policyname
    policy.retentionperiod = retentionperiod
    policy.keepingpoint = keepingpoint
    policy.cleandata = cleandata
    policy.usemaxbandwidth = usemaxbandwidth
    policy.maxbandwidth = maxbandwidth
    policy.cycletype = cycletype
    policy.isencipher = isencipher
    policy.backupmode = backupmode
    policy.isdup = isdup
    policy.ext_info = json.dumps({'retentionperiod_unit': retentionperiod_unit,
                                  'thread_count': thread_count})
    policy.save()

    mylog = {"名称": policy.name}
    ret = dict()

    if cycletype == 1:
        cdp = cdpcycle()
        cdp.taskpolicy = policy
        cdp.cdpperiod = int(cdpperiod)
        cdp.cdptype = int(cdptype)
        cdp.starttime = starttime
        cdp.save()
        mylog["类型"] = "连续数据保护（CDP）"
    elif cycletype == 2:
        onlyone = onlyonecycle()
        onlyone.taskpolicy = policy
        onlyone.starttime = starttime
        onlyone.save()
        mylog["类型"] = "仅备份一次，开始时间{}".format(starttime)
    elif cycletype == 3:
        everyday = everydaycycle()
        everyday.taskpolicy = policy
        everyday.starttime = starttime
        everyday.timeinterval = timeinterval
        everyday.unit = intervalUnit
        everyday.save()
        unit = {'min': '分钟', 'hour': '小时', 'day': '天'}
        mylog["类型"] = "按间隔时间，开始时间{0}，每{1}{2}开始执行".format(starttime, timeinterval, unit[intervalUnit])
    elif cycletype == 4:
        everyweek = everyweekcycle()
        everyweek.taskpolicy = policy
        everyweek.starttime = starttime
        everyweek.perweek = 0
        for item in perweek:
            everyweek.perweek += pow(2, int(item) - 1)
        everyweek.save()
        mylog["类型"] = "每周，开始时间：{}，每周{}备份".format(starttime, FmtWeek(everyweek.perweek))
    elif cycletype == 5:
        everymonth = everymonthcycle()
        everymonth.taskpolicy = policy
        everymonth.starttime = starttime
        everymonth.monthly = 0
        for item in monthly:
            everymonth.monthly += pow(2, int(item) - 1)
        everymonth.save()
        mylog["类型"] = "每月，开始时间：{}，每月{}日备份".format(starttime, FmtMonthly(everymonth.monthly))
    else:
        policy.delete()
        e = "未知周期类型:{}".format(cycletype)
        mylog["r"] = 1
        mylog["e"] = e
        mylog["操作结果"] = e
        return mylog

    mylog["r"] = 0
    mylog["e"] = "操作成功"
    mylog["操作结果"] = "操作成功"
    return mylog


def createpolicy(request):
    policyname = request.GET.get('taskname', default='none')
    cycletype = int(request.GET.get('bakschedule', default='1'))
    retentionperiod = int(request.GET.get('retentionperiod', default='30'))  # days
    cleandata = int(request.GET.get('cleandata', default='4'))  # GB
    cdpperiod = request.GET.get('cdpperiod', default='7')  # cdp窗口 days
    cdptype = request.GET.get('cdptype', default='1')  # cdp方式
    usemaxbandwidth = int(request.GET.get('usemaxbandwidth', default='0'))
    maxbandwidth = int(request.GET.get('maxbandwidth', default='1000'))  # Mbps
    keepingpoint = int(request.GET.get('keepingpoint', default='5'))  # 保留备份点个数
    isencipher = int(request.GET.get('isencipher', default='0'))
    removeDup = request.GET['SystemFolderDup'] == '1'

    starttime = request.GET.get('starttime', default='2016-09-25 10:25:24')
    timeinterval = request.GET.get('timeinterval', default='1')
    intervalUnit = request.GET['intervalUnit']
    retentionperiod_unit = request.GET['retentionperiod_unit']  # 'month', 'day'
    perweek = request.GET.getlist('perweek', default=[1])
    monthly = request.GET.getlist('monthly', default=[1])
    backupmode = int(request.GET.get('backupmode', default=2))
    thread_count = request.GET['thread_count']

    if retentionperiod_unit == 'day':
        retentionperiod = int(retentionperiod) * 1
    if retentionperiod_unit == 'month':
        retentionperiod = int(retentionperiod) * 30

    policy = dict()
    policy["name"] = policyname
    policy["cycletype"] = cycletype
    policy["retentionperiod"] = retentionperiod
    policy["cleandata"] = cleandata
    policy["cdpperiod"] = cdpperiod
    policy["cdptype"] = cdptype
    policy["usemaxbandwidth"] = usemaxbandwidth
    policy["maxbandwidth"] = maxbandwidth
    policy["keepingpoint"] = keepingpoint
    policy["starttime"] = starttime
    policy["timeinterval"] = timeinterval
    policy["intervalUnit"] = intervalUnit
    policy["retentionperiod_unit"] = retentionperiod_unit
    policy["perweek"] = perweek
    policy["monthly"] = monthly
    policy["isencipher"] = isencipher
    policy["backupmode"] = backupmode
    policy["removeDup"] = removeDup
    policy["thread_count"] = thread_count

    clret = _check_duplicates_functional(removeDup)
    if clret.get('r', 0) != 0:
        return HttpResponse(json.dumps(clret, ensure_ascii=False))

    addret = addpolicy(request.user, policy)

    ret = {"r": addret["r"], "e": addret["e"]}
    addret.pop('r')
    addret.pop('e')
    mylog = addret
    mylog["操作"] = "新建策略"
    SaveOperationLog(
        request.user, OperationLog.TYPE_BACKUP, json.dumps(mylog, ensure_ascii=False), get_operator(request))
    return HttpResponse(content=json.dumps(ret), status=status.HTTP_200_OK)


def getInfosByPolicyObj(itemObject):
    name = itemObject.name
    id = itemObject.id
    return [id, name]


def policylist(request):
    rows = int(request.GET.get('rows', '30'))  # 设置每页条数
    page = int(request.GET.get('page', '1'))  # 返回第几页

    policy = taskpolicy.objects.filter(user=request.user)
    paginator = Paginator(policy, rows)
    totalPlan = paginator.count
    totalPage = paginator.num_pages

    page = totalPage if page > totalPage else page
    Objs = paginator.page(page).object_list
    rowList = list()
    for Obj in Objs:
        detailDict = {'id': Obj.id, 'cell': getInfosByPolicyObj(Obj)}
        rowList.append(detailDict)

    retInfo = {'r': 0, 'a': 'list', 'page': str(page), 'total': totalPage,
               'records': totalPlan, 'rows': rowList}
    functions.sort_gird_rows(request, retInfo)
    jsonStr = json.dumps(retInfo, ensure_ascii=False)
    return HttpResponse(jsonStr)


def getonepolicy(id):
    retInfo = dict()
    policy = taskpolicy.objects.filter(id=int(id)).first()
    policy_ext_info = json.loads(policy.ext_info)
    if not policy:
        retInfo["r"] = 1
        retInfo["e"] = "找不到策略"
        return retInfo
    retInfo["name"] = policy.name
    retInfo["cycletype"] = policy.cycletype
    retInfo["retentionperiod"] = policy.retentionperiod
    retInfo["keepingpoint"] = policy.keepingpoint
    retInfo["cleandata"] = policy.cleandata
    retInfo["usemaxbandwidth"] = policy.usemaxbandwidth
    retInfo["maxbandwidth"] = policy.maxbandwidth
    retInfo["isencipher"] = policy.isencipher
    retInfo["backupmode"] = policy.backupmode
    retInfo["removeDup"] = policy.isdup
    retInfo['retentionperiod_unit'] = policy_ext_info.get('retentionperiod_unit', 'is_old')
    retInfo['thread_count'] = policy_ext_info.get('thread_count', 1)
    cycletype = policy.cycletype
    if cycletype == 1:
        cdp = cdpcycle.objects.filter(taskpolicy=policy).first()
        if cdp.starttime:
            starttime = cdp.starttime.strftime('%Y-%m-%d %H:%M:%S')
        else:
            starttime = None
        if not cdp:
            retInfo["r"] = 2
            retInfo["e"] = "找不到策略周期1"
            return retInfo
        cdptype = cdp.cdptype
        cdpperiod = cdp.cdpperiod
        retInfo["cdptype"] = cdptype
        retInfo["cdpperiod"] = cdpperiod
        retInfo["starttime"] = starttime
    elif cycletype == 2:
        onlyone = onlyonecycle.objects.filter(taskpolicy=policy).first()
        if not onlyone:
            retInfo["r"] = 3
            retInfo["e"] = "找不到策略周期2"
            return retInfo
        starttime = onlyone.starttime.strftime('%Y-%m-%d %H:%M:%S')
        retInfo["starttime"] = starttime
    elif cycletype == 3:
        everyday = everydaycycle.objects.filter(taskpolicy=policy).first()
        if not everyday:
            retInfo["r"] = 4
            retInfo["e"] = "找不到策略周期3"
            return retInfo
        starttime = everyday.starttime.strftime('%Y-%m-%d %H:%M:%S')
        timeinterval = everyday.timeinterval
        retInfo["starttime"] = starttime
        retInfo["timeinterval"] = timeinterval
        retInfo['unit'] = everyday.unit
    elif cycletype == 4:
        everyweek = everyweekcycle.objects.filter(taskpolicy=policy).first()
        if not everyweek:
            retInfo["r"] = 5
            retInfo["e"] = "找不到策略周期4"
            return retInfo
        starttime = everyweek.starttime.strftime('%Y-%m-%d %H:%M:%S')
        perweek = int(everyweek.perweek)
        retInfo["starttime"] = starttime
        retInfo["perweek"] = FmtWeek(perweek)
        retInfo["period"] = FmtWeek2(perweek)
    elif cycletype == 5:
        everymonth = everymonthcycle.objects.filter(taskpolicy=policy).first()
        if not everymonth:
            retInfo["r"] = 6
            retInfo["e"] = "找不到策略周期5"
            return retInfo
        starttime = everymonth.starttime.strftime('%Y-%m-%d %H:%M:%S')
        monthly = int(everymonth.monthly)
        retInfo["starttime"] = starttime
        retInfo["monthly"] = FmtMonthly(monthly)
    else:
        retInfo["r"] = 2
        retInfo["e"] = "周期类型未知：{}".format(cycletype)
        return retInfo

    retInfo["r"] = 0
    retInfo["e"] = "操作成功"
    return retInfo


def getpolicydetail(request):
    id = int(request.GET.get('id', '0'))
    jsonStr = json.dumps(getonepolicy(id), ensure_ascii=False)
    return HttpResponse(jsonStr)


def getpolicy(request):
    policy = taskpolicy.objects.filter(user=request.user)
    policies = list()
    for item in policy:
        policies.append({"id": item.id, "name": item.name, "cycletype": item.cycletype})
    return HttpResponse(json.dumps({"r": 0, "e": "操作成功", "list": policies}, ensure_ascii=False))


def deloneuserpolicy(userobj):
    policys = taskpolicy.objects.filter(user=userobj)
    if not policys:
        return True
    for policy in policys:
        cycletype = policy.cycletype
        if cycletype == 1:
            cdpcycle.objects.filter(taskpolicy=policy).delete()
        if cycletype == 2:
            onlyonecycle.objects.filter(taskpolicy=policy).delete()
        if cycletype == 3:
            everydaycycle.objects.filter(taskpolicy=policy).delete()
        if cycletype == 4:
            everyweekcycle.objects.filter(taskpolicy=policy).delete()
        if cycletype == 5:
            everymonthcycle.objects.filter(taskpolicy=policy).delete()
        policy.delete()
    return True


def delpolicybyid(id):
    policy = taskpolicy.objects.filter(id=id).first()
    if not policy:
        return 'none'
    cycletype = policy.cycletype
    name = policy.name
    if cycletype == 1:
        cdpcycle.objects.filter(taskpolicy=policy).delete()
    if cycletype == 2:
        onlyonecycle.objects.filter(taskpolicy=policy).delete()
    if cycletype == 3:
        everydaycycle.objects.filter(taskpolicy=policy).delete()
    if cycletype == 4:
        everyweekcycle.objects.filter(taskpolicy=policy).delete()
    if cycletype == 5:
        everymonthcycle.objects.filter(taskpolicy=policy).delete()
    taskpolicy.objects.filter(id=id).delete()
    return name


def delpolicy(request):
    ids = request.GET.get('id', '')
    mylog = {"操作": "删除策略"}
    names = list()
    for id in ids.split(','):
        name = delpolicybyid(id)
        names.append(name)
    mylog['操作结果'] = '删除成功'
    mylog['策略名'] = names
    SaveOperationLog(
        request.user, OperationLog.TYPE_BACKUP, json.dumps(mylog, ensure_ascii=False), get_operator(request))
    return HttpResponse(json.dumps({"r": 0, "e": "操作成功"}, ensure_ascii=False))


def editpolicy(request):
    id = int(request.GET.get('id', default='0'))
    policyname = request.GET.get('taskname', default='none')
    cycletype = int(request.GET.get('bakschedule', default='1'))
    retentionperiod = int(request.GET.get('retentionperiod', default='30'))
    cleandata = int(request.GET.get('cleandata', default='4'))
    cdpperiod = request.GET.get('cdpperiod', default='7')
    cdptype = request.GET.get('cdptype', default='1')
    usemaxbandwidth = int(request.GET.get('usemaxbandwidth', default='0'))
    maxbandwidth = int(request.GET.get('maxbandwidth', default='1000'))
    keepingpoint = int(request.GET.get('keepingpoint', default='5'))
    isencipher = int(request.GET.get('isencipher', default='0'))
    backupmode = int(request.GET.get('backupmode', default='0'))
    removeDup = request.GET['SystemFolderDup'] == '1'

    starttime = request.GET.get('starttime', default='2016-09-25 10:25:24')
    timeinterval = request.GET.get('timeinterval', default='1')
    intervalUnit = request.GET['intervalUnit']
    retentionperiod_unit = request.GET['retentionperiod_unit']
    perweek = request.GET.getlist('perweek', default=[1])
    monthly = request.GET.getlist('monthly', default=[1])
    thread_count = request.GET['thread_count']

    if retentionperiod_unit == 'day':
        retentionperiod = int(retentionperiod) * 1
    if retentionperiod_unit == 'month':
        retentionperiod = int(retentionperiod) * 30

    delpolicybyid(id)

    policy = dict()
    policy["name"] = policyname
    policy["cycletype"] = cycletype
    policy["retentionperiod"] = retentionperiod
    policy["cleandata"] = cleandata
    policy["cdpperiod"] = cdpperiod
    policy["cdptype"] = cdptype
    policy["usemaxbandwidth"] = usemaxbandwidth
    policy["maxbandwidth"] = maxbandwidth
    policy["keepingpoint"] = keepingpoint
    policy["starttime"] = starttime
    policy["timeinterval"] = timeinterval
    policy["intervalUnit"] = intervalUnit
    policy["retentionperiod_unit"] = retentionperiod_unit
    policy["thread_count"] = thread_count

    policy["perweek"] = perweek
    policy["monthly"] = monthly
    policy["isencipher"] = isencipher
    policy["backupmode"] = backupmode
    policy["removeDup"] = removeDup

    addret = addpolicy(request.user, policy)

    ret = {"r": addret["r"], "e": addret["e"]}
    addret.pop('r')
    addret.pop('e')
    mylog = addret
    mylog["操作"] = "编辑策略"
    SaveOperationLog(
        request.user, OperationLog.TYPE_BACKUP, json.dumps(mylog, ensure_ascii=False), get_operator(request))
    return HttpResponse(content=json.dumps(ret, ensure_ascii=False), status=status.HTTP_200_OK)


def exportpolicy(request):
    exportpath = os.path.join(cur_file_dir(), 'static', 'tmp')
    delexpirelog(exportpath)
    ids = request.GET.get('ids', default='0').split(',')
    retvec = list()
    for id in ids:
        retvec.append(getonepolicy(id))
    policyjson = json.dumps(retvec, ensure_ascii=False)

    try:
        os.makedirs(exportpath)
    except OSError as e:
        pass
    timestr = str(datetime.datetime.now())
    jsonname = timestr + 'policy.zip'
    filepath = os.path.join(exportpath, jsonname)

    try:
        file_object = open(filepath, 'w')
        file_object.write(policyjson)
        file_object.close()
    except Exception as e:
        pass

    mylog = {'操作': '导出策略', '操作结果': "导出成功"}
    SaveOperationLog(
        request.user, OperationLog.TYPE_BACKUP_POLICY, json.dumps(mylog, ensure_ascii=False), get_operator(request))
    return HttpResponse(
        json.dumps({"r": "0", "e": "操作成功", "url": "/static/tmp/{}".format(jsonname), "filename": jsonname},
                   ensure_ascii=False))


def importpolicy(user, filepath, bcover, operator):
    if not os.path.isfile(filepath):
        return False
    policylist = list()
    try:
        file_object = open(filepath, 'r')
        jsonstr = file_object.read()
    except Exception as e:
        pass

    try:
        file_object.close()
        policylist = json.loads(jsonstr)
    except Exception as e:
        pass

    for policy in policylist:
        if policy["r"] == 0:
            if bcover == 1:
                tmppolicylist = taskpolicy.objects.filter(name=policy["name"])
                for tmppolic in tmppolicylist:
                    delpolicybyid(tmppolic.id)
            policy["monthly"] = policy['monthly'].split(',') if 'monthly' in policy else [1]
            policy["perweek"] = policy['period'].split(',') if 'period' in policy else [1]
            policy['removeDup'] = policy['removeDup'] if 'removeDup' in policy else True
            addret = addpolicy(user, policy)
            addret.pop('r')
            addret.pop('e')
            mylog = addret
            mylog["操作"] = "导入策略"
            SaveOperationLog(user, OperationLog.TYPE_BACKUP, json.dumps(mylog, ensure_ascii=False), operator)
    return True


def uploadpolicy(request):
    file_data = request.body
    name = request.GET.get('name', 'none.bin')
    start = int(request.GET.get('start', '0'))
    step = int(request.GET.get('step', 1024 * 1024))
    total = int(request.GET.get('total', 0))
    bcover = int(request.GET.get('bcover', 0))
    r = 0
    try:
        os.makedirs(os.path.join(cur_file_dir(), 'static', 'tmp'))
    except OSError as e:
        pass

    filepath = os.path.join(cur_file_dir(), 'static', 'tmp', name)

    if start == 0:
        try:
            os.remove(filepath)
        except OSError as e:
            pass

    binfile = open(filepath, 'ab')
    vec = str(file_data).split(';base64,')
    if len(vec) == 2:
        strbase64 = vec[1]
    else:
        return HttpResponse(json.dumps({"r": 1, "e": "忽略"}, ensure_ascii=False))
    binfile.write(base64.b64decode(strbase64))
    binfile.close()
    start = start + step
    if start >= total:
        if os.path.getsize(filepath) == total:
            r = 200
            importpolicy(request.user, filepath, bcover, get_operator(request))
            os.remove(filepath)
    return HttpResponse(
        json.dumps({"r": r, "e": "操作成功", "name": name, "start": start}, ensure_ascii=False))


def uploadpolicybyflash(request):
    file = request.FILES.get("Filedata", None)
    bcover = int(request.GET.get('bcover', 0))
    ret = 2
    name = 'none'

    try:
        os.makedirs(os.path.join(cur_file_dir(), 'static', 'tmp'))
    except OSError as e:
        pass

    if file:
        filepath = os.path.join(cur_file_dir(), 'static', 'tmp', file.name)
        name = file.name
        fp = open(filepath, 'wb')
        for content in file.chunks():
            fp.write(content)
        fp.close()
        ret = 200
        importpolicy(request.user, filepath, bcover, get_operator(request))
        os.remove(filepath)
    return HttpResponse(json.dumps({'r': ret, 'save_name': name}, ensure_ascii=False))


def _Byte2GBp2(_bytes):
    return float('{0:.2f}'.format(_bytes / 1024 ** 3))


def getSubVollist(request):
    hostIdent = request.GET.get('serverid', 'none')
    showtype = request.GET.get('showtype', 'none')
    infoList = list()
    try:
        host_obj = Host.objects.get(ident=hostIdent)
    except Host.DoesNotExist:
        return HttpResponse("except Host.DoesNotExist hostIdent=" + hostIdent)
    system_info = query_system_info(host_obj, True)
    if system_info is None:
        system_info = query_system_info(host_obj, False)
    if system_info is None:
        return HttpResponse("query_system_info is none hostIdent=" + hostIdent)

    for diskdist in system_info['Disk']:
        for Partition in diskdist['Partition']:
            letter = Partition['Letter']
            if letter == '':
                pass
            else:
                VolumeSize = int(Partition['VolumeSize'])
                FreeSize = int(Partition['FreeSize'])
                if FreeSize == -1:
                    UsedSize = '--'
                    label = '{}:（容量{}GB,已用{}）'.format(letter, _Byte2GBp2(VolumeSize), _Byte2GBp2(UsedSize))
                else:
                    UsedSize = int(VolumeSize - FreeSize)
                    label = '{}:（容量{}GB,已用{}GB）'.format(letter, _Byte2GBp2(VolumeSize), _Byte2GBp2(UsedSize))
                checked = True
                if showtype == 'nocheck':
                    checked = False
                infoList.append(
                    {'id': letter, 'icon': 'harddisk', 'checkbox': 'true', "inode": False, "checked": checked,
                     'label': label})

    return HttpResponse(json.dumps(infoList, ensure_ascii=False))


def getVollist(request):
    id = request.GET.get('id', 'root')
    hostIdent = request.GET.get('serverid', 'none')
    id = 'root' if id == '' else id
    if id == 'root':
        infoList = list()
        hostDetail = HostSessionInfo().get(request=request, ident=hostIdent).data
        servername = hostDetail['host']['name']
        infoList.append({'id': 'ui_' + hostIdent, 'icon': 'pc', "inode": True, "open": True, 'label': servername})
        return HttpResponse(json.dumps(infoList, ensure_ascii=False))
    else:
        return getSubVollist(request)


def not_exist_host_snapshot(request):
    task_id = request.GET['task_id']
    task_obj = BackupTask.objects.get(id=task_id)
    host_snapshot = task_obj.host_snapshot

    if host_snapshot and (not host_snapshot.deleting) and (not host_snapshot.deleted):
        return HttpResponse(content=json.dumps({'r': 0, 'e': 'ok', 'not_exist': False}))

    return HttpResponse(content=json.dumps({'r': 0, 'e': 'ok', 'not_exist': True}))


def backup_handler(request):
    a = request.GET.get('a', 'none')
    if a == 'none':
        a = request.POST.get('a', 'none')
    if a == 'getserverinfo':
        return getServerDetail(request)
    if a == 'getstoragedevice':
        return getStorageDevice(request)
    if a == 'getstorage':
        return getstorage(request)
    if a == 'createbackup':
        return createModifyPlan(request)
    if a == 'getlist':
        return getServerList(request)
    if a == 'createpolicy':
        return createpolicy(request)
    if a == 'policylist':
        return policylist(request)
    if a == 'getpolicydetail':
        return getpolicydetail(request)
    if a == 'getpolicy':
        return getpolicy(request)
    if a == 'delpolicy':
        return delpolicy(request)
    if a == 'editpolicy':
        return editpolicy(request)
    if a == 'exportpolicy':
        return exportpolicy(request)
    if a == 'uploadpolicy':
        return uploadpolicy(request)
    if a == 'uploadpolicybyflash':
        return uploadpolicybyflash(request)
    if a == 'getVollist':
        return getVollist(request)
    if a == 'diskvolinfo':
        return getCurrentDiskVol(request)
    if a == 'change_node':
        return change_node_about_yun(request)
    if a == 'createbackup_yun':
        return createModifyPlan_for_yun_before(request)
    if a == 'not_exist_host_snapshot':
        return not_exist_host_snapshot(request)
    return HttpResponse(json.dumps({"r": "1", "e": "没有对应的action:{}".format(html.escape(a))}))
