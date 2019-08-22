# coding=utf-8
import html
import json
import os
import re
import time
import traceback
from apiv1.models import RestoreTarget
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from xdashboard.common.functional import hasFunctional
from apiv1.views import HostSessionInfo
from apiv1.logic_processors import CreateHostLogicProcessor
from apiv1.models import HostSnapshot, Host, MigrateTask, RestoreTask
from apiv1.models import TakeOverKVM
from apiv1.restore import PeRestore
from apiv1.storage_nodes import UserQuotaTools
from apiv1.views import HostSessions, PeHostSessions, PeHostSessionInfo, \
    HostSessionMigrate, HostSessionDisks, Agent2Pe, HostRoutersInfo, GetDriversVersions, \
    get_response_error_string
from apiv1.views import HostSnapshotLocalRestore as Restore2Self
from box_dashboard import xlogging, xdata, boxService
from xdashboard.common import file_utils
from xdashboard.common.license import is_functional_visible
from xdashboard.handle import backup
from xdashboard.handle.authorize import authorize_init
from xdashboard.handle.logserver import SaveOperationLog
from xdashboard.models import OperationLog
from xdashboard.request_util import get_operator
from xdashboard.models import audit_task

_logger = xlogging.getLogger(__name__)


# 获取迁移源主机(在线的host)
def getServerList(request):
    return backup.getServerList(request)


def _FmtMAC(mac):
    mac = xdata.standardize_mac_addr(mac)
    if len(mac) == 12:
        mac = '{}-{}-{}-{}-{}-{}'.format(mac[0:2], mac[2:4], mac[4:6], mac[6:8], mac[8:10], mac[10:])
    return mac


def _get_point_mac(pointid):
    point_mac = list()
    params = pointid.split('|')
    host_snapshot_id = params[1]
    snapshot_obj = HostSnapshot.objects.filter(id=host_snapshot_id).first()
    if snapshot_obj:
        ext_info = json.loads(snapshot_obj.ext_info)
        Nic = ext_info['system_infos']['Nic']
        for one in Nic:
            point_mac.append(_FmtMAC(one["Mac"]))
    return point_mac


def _get_host_mac(ident, filter_valid_adpter):
    host_mac = list()
    host_obj = Host.objects.filter(ident=ident).first()
    if host_obj:
        ext_info = json.loads(host_obj.ext_info)
        Nic = ext_info['system_infos']['Nic']
        for one in Nic:
            Hwids = one.get('HwIds', None)
            if filter_valid_adpter and CreateHostLogicProcessor.is_valid_adpter(Hwids):
                host_mac.append(_FmtMAC(one["Mac"]))
    return host_mac


def _get_kvm_mac():
    kvm_mac = list()
    TakeOverKVMobj = TakeOverKVM.objects.all()
    for kvm in TakeOverKVMobj:
        ext_info = json.loads(kvm.ext_info)
        kvm_adpter = ext_info['kvm_adpter']
        for adpter in kvm_adpter:
            kvm_mac.append(adpter['mac'])
    return kvm_mac


def is_run_in_kvm(kvm_mac, ident):
    host_mac = _get_host_mac(ident, True)
    for mac in host_mac:
        if mac in kvm_mac:
            return True

    return False


def is_same_host(pointid, ident):
    return _get_host_ident(pointid) == ident


@xlogging.convert_exception_to_value(None)
def _get_host_ident(pointid):
    params = pointid.split('|')
    host_snapshot_id = params[1]
    snapshot = HostSnapshot.objects.filter(id=host_snapshot_id).first()
    if snapshot:
        return snapshot.host.ident
    else:
        return None


def is_same_host2(pointid, pe_ident):
    if not pointid:
        return False
    point_mac = _get_point_mac(pointid)
    pe_mac = list()

    api_response = PeHostSessionInfo().get(request=None, ident=pe_ident)
    if not status.is_success(api_response.status_code):
        debug = "PeHostSessionInfo Failed.ident={},status_code={}".format(pe_ident, api_response.status_code)
        _logger.error(debug)
        return False
    for adapter_data in api_response.data['network_adapters']:
        mac = _FmtMAC(adapter_data['szMacAddress'])
        pe_mac.append(mac)

    for mac in point_mac:
        if mac in pe_mac:
            return True

    return False


# 获取迁移目的主机(在线的host)
def getDestServerList1(request):
    id = request.GET.get('id', 'root')
    pointid = request.GET.get('pointid', None)
    if id == '':
        id = 'root'

    filter_funcs = [HostSessions.filter_deleted,
                    HostSessions.filter_offline]
    attr_getters = [('name1', backup.get_host_name)]
    hostSessionsObj = HostSessions()
    hostList = hostSessionsObj.get(request=request, api_request={'type': (Host.AGENT,)}, filter_funcs=filter_funcs,
                                   attr_getters=attr_getters).data
    append_validate_host(hostList)
    # 获取根状态
    if id == 'ui_1':
        if len(hostList):
            pass
        else:
            info = '[{"id": "ui_1","branch":[],"inode":false, "label": "无连接","icon":"pc","radio":false}]'
            return HttpResponse(info)

    # 获取展开信息
    infoList = list()
    infoList_other = list()
    kvm_mac = _get_kvm_mac()
    for host in hostList:
        ident = host['ident']
        host['name'] = host['name1']
        if is_run_in_kvm(kvm_mac, ident):
            info = {'id': '{}'.format(ident), 'icon': 'pc', 'radio': False,
                    'label': '<span style="color:blue;">[接管主机]</span>{0}'.format(host['name']), 'inode': False,
                    'open': False}
            infoList_other.append(info)
        elif is_same_host(pointid, ident):
            info = {'id': '{}'.format(ident), 'icon': 'pc', 'radio': 'true',
                    'label': '<span style="color:blue;">[源机]</span>{0}'.format(host['name']), 'inode': True,
                    'open': False, 'source_machine': True}
            infoList.append(info)
        else:
            info = {'id': '{}'.format(ident), 'icon': 'pc', 'radio': 'true',
                    'label': '{0}'.format(host['name']), 'inode': True, 'open': False}
            infoList_other.append(info)
    for info in infoList_other:
        infoList.append(info)
    infos = json.dumps(infoList, ensure_ascii=False)
    return HttpResponse(infos)


def getDestServerList0(request):
    kvmtype = request.GET.get('kvmtype', 'forever_kvm')

    infoList = list()
    if kvmtype == 'forever_kvm':
        info = {'id': 'forever_kvm', 'icon': 'pc', 'radio': 'true', 'label': '用于接管的虚拟机', 'inode': False, 'open': False}
    else:
        info = {'id': 'temporary_kvm', 'icon': 'pc', 'radio': 'true', 'label': '用于验证的虚拟机（不保留新产生的数据）', 'inode': False,
                'open': False}
    infoList.append(info)
    infos = json.dumps(infoList, ensure_ascii=False)
    return HttpResponse(infos)


# 扩展验证主机到 目标主机列表
def append_validate_host(hostList):
    hosts = Host.objects.filter(user=None)
    hosts = list(filter(lambda x: x.is_verified and x.is_linked, hosts))
    if hosts:
        attr_getters = [('name1', backup.get_host_name)]
        hostList.extend([HostSessions.serializer_host(host, attr_getters) for host in hosts])


def is_pe_existed_task(pe_ident):
    if MigrateTask.objects.filter(restore_target__ident=pe_ident).exists():
        return True
    elif RestoreTask.objects.filter(restore_target__ident=pe_ident).exists():
        return True
    else:
        return False


# 获取迁移目的主机(在线的PE)
def getDestServerList2(request):
    id = request.GET.get('id', 'root')
    pointid = request.GET.get('pointid', None)
    if id == '':
        id = 'root'
    resp = PeHostSessions().get(request=request)
    PEsInfo = resp.data

    # 获取根状态
    if id == 'ui_2':
        if resp.status_code == status.HTTP_204_NO_CONTENT:
            info = '[{"id": "1","branch":[],"inode":false, "label": "无连接","icon":"pc","radio":false}]'
            return HttpResponse(info)

    # 获取展开信息
    infoList = list()
    infoList_other = list()
    for PEInfo in PEsInfo:
        peIdent = PEInfo['ident']
        if is_pe_existed_task(peIdent):
            continue
        if is_same_host2(pointid, peIdent):
            retInfo = {'id': '{}'.format(peIdent), 'icon': 'pc', 'radio': 'true',
                       'label': '<span style="color:blue;">[源机]</span>{0}'.format(PEInfo['display_name']),
                       'source_machine': True}
            infoList.append(retInfo)
        else:
            retInfo = {'id': '{}'.format(peIdent), 'icon': 'pc', 'radio': 'true',
                       'label': '{0}'.format(PEInfo['display_name'])}
            infoList_other.append(retInfo)
    for info in infoList_other:
        infoList.append(info)
    jsonStr = json.dumps(infoList, ensure_ascii=False)
    return HttpResponse(jsonStr)


# 获取目的服务器(PE, Agent)
def getDestServerList(request):
    host_id = request.GET.get('id', 'root')
    showtype = request.GET.get('showtype', 'none')
    kvmtype = request.GET.get('kvmtype', 'none')
    if host_id == '':
        host_id = 'root'

    # 获取根状态
    if host_id == 'root':
        infoList = list()
        if kvmtype in ('forever_kvm', 'temporary_kvm'):
            if request.user.userprofile.modules & 65536 * 128 and is_functional_visible('takeover'):
                info = {"id": "ui_0", "branch": [], "inode": True, "open": True, "label": "接管主机", "icon": "pcroot",
                        "radio": False}
                infoList.append(info)
        info = {"id": "ui_1", "branch": [], "inode": True, "open": True, "label": "无需启动介质连接的客户端", "icon": "pcroot",
                "radio": False}
        infoList.append(info)
        if showtype == 'nope':
            pass
        else:
            info = {"id": "ui_2", "branch": [], "inode": True, "open": True, "label": "启动介质连接的客户端(PXE/启动U盘/启动光盘)",
                    "icon": "pcroot", "radio": False}
            infoList.append(info)
        return HttpResponse(json.dumps(infoList, ensure_ascii=False))

    if host_id == 'ui_0':
        return getDestServerList0(request)  # kvm

    if host_id == 'ui_1':
        return getDestServerList1(request)  # 在线目的Host

    if host_id == 'ui_2':
        return getDestServerList2(request)  # 在线目的PE

    if len(host_id) == 32:
        respn = HostSessionDisks().get(request=request, ident=host_id)
        if not status.is_success(respn.status_code):
            return HttpResponse('{"r": "1", "e": "agent error"}')
        disks = respn.data
        is_windows = backup.is_windows_host(host_id)
        infoList = list()
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


# 获取目的服务器为云端服务(PE, Agent)
def getDestServerListYun(request):
    pointid = request.GET.get('pointid', None)
    # agent
    filter_funcs = [HostSessions.filter_deleted]
    hostSessionsObj = HostSessions()
    hostList = hostSessionsObj.get(request=request, filter_funcs=filter_funcs).data
    append_validate_host(hostList)
    infoList = list()
    infoList_other = list()
    kvm_mac = _get_kvm_mac()
    for host in hostList:
        ident = host['ident']
        host_obj = Host.objects.get(ident=ident)
        host['name'] = host_obj.display_name + host_obj.last_ip  # backup.get_host_name(host_ident=ident)
        ips = get_host_all_ips(ident)
        volume_info = backup.get_host_volume_info(request, ident)
        if is_run_in_kvm(kvm_mac, ident):
            info = {'id': '{}'.format(ident), 'icon': 'pc', 'radio': False,
                    'label': '<span style="color:blue;">[接管主机]</span>{0}'.format(host['name']), 'inode': False,
                    'open': False, 'volume_info': volume_info, 'ips': ips, 'client_type': '普通客户端-主机接管'}
            infoList_other.append(info)
        elif is_same_host(pointid, ident):
            info = {'id': '{}'.format(ident), 'icon': 'pc', 'radio': 'true',
                    'label': '<span style="color:blue;">[源机]</span>{0}'.format(host['name']), 'inode': True,
                    'open': False, 'source_machine': True, 'volume_info': volume_info, 'ips': ips,
                    'client_type': '普通客户端-源机'}
            infoList.append(info)
        else:
            info = {'id': '{}'.format(ident), 'icon': 'pc', 'radio': 'true', 'ips': ips, 'client_type': '普通客户端',
                    'label': '{0}'.format(host['name']), 'inode': True, 'open': False, 'volume_info': volume_info}
            infoList_other.append(info)
    # PE
    resp = PeHostSessions().get(request=request)
    PEsInfo = resp.data
    if not PEsInfo:
        PEsInfo = []
    for PEInfo in PEsInfo:
        volume_info_pe = list()
        peIdent = PEInfo['ident']
        if is_pe_existed_task(peIdent):
            continue
        rsp = PeHostSessionInfo().get(None, peIdent).data
        for vol in rsp['disks']:
            volume_info_pe.append(
                {'label': '磁盘{}：容量 {}GB'.format(int(vol['disk_id']) + 1,
                                                float('%.2f' % (vol['disk_bytes'] / (1024 ** 3))))}
            )
        host_ip = rsp['pe_host']['display_name'].split(' ')[0]
        if is_same_host2(pointid, peIdent):
            retInfo = {'id': '{}'.format(peIdent), 'icon': 'pc', 'radio': 'true',
                       'label': '<span style="color:blue;">[源机]</span>{0}'.format(PEInfo['display_name']),
                       'source_machine': True, 'client_type': '介质启动客户端',
                       'ips': [host_ip], 'volume_info': volume_info_pe
                       }
            infoList.append(retInfo)
        else:
            retInfo = {'id': '{}'.format(peIdent), 'icon': 'pc', 'radio': 'true',
                       'volume_info': volume_info_pe, 'ips': [host_ip],
                       'label': '{0}'.format(PEInfo['display_name']), 'client_type': '介质启动客户端'}
            infoList_other.append(retInfo)
    for info in infoList_other:
        infoList.append(info)
    jsonStr = json.dumps(infoList, ensure_ascii=False)
    return HttpResponse(jsonStr)


def get_host_all_ips(host_ident):
    exclude_ips = ['0.0.0.0']
    host_obj = Host.objects.get(ident=host_ident)
    all_ip_mask_pair, system_infos = [], json.loads(host_obj.ext_info).get('system_infos', dict())
    for nic in system_infos.get('Nic', list()):
        all_ip_mask_pair += nic['IpAndMask']
    all_ips = [pair['Ip'] for pair in all_ip_mask_pair]
    all_ips = [valid_ip for valid_ip in all_ips if valid_ip not in exclude_ips]
    return all_ips


def set_target_nics_info_from_src_nics(target_nics, src_ext_info):
    src_nics = src_ext_info['system_infos']['Nic']
    for target_nic in target_nics:
        targ_mac = target_nic['target_nic']['szMacAddress']
        src_nic_info = Restore2Self.query_target_nic_info_in_snapshot_nics(src_nics, targ_mac)

        if src_nic_info:  # 目标该网卡, 在源中存在信息
            target_nic['ip_mask_pair'] = src_nic_info['IpAndMask']
            target_nic['dns_list'] = [dns for dns in src_nic_info['Dns'] if dns.find(':') == -1]
            target_nic['gate_way'] = '' if src_nic_info['GateWay'] == '0.0.0.0' else src_nic_info['GateWay']
            target_nic['is_to_self'] = True
            target_nic['src_is_dhcp'] = src_nic_info.get('Dhcp', '0') == '1'
            target_nic['src_instance_id'] = Restore2Self.query_nic_instance_id_in_src(src_ext_info, targ_mac)


def query_info_form_nics(src_mac, dst_info_list):
    for dst_info in dst_info_list:
        if xdata.is_two_mac_addr_equal(src_mac, dst_info['Mac']):
            return dst_info
    return None


def set_target_nic_info_from_pe_info(target_nics, pe_system_infos):
    nics = pe_system_infos.get('Nic', list())
    if not nics:
        return None
    for target_nic in target_nics:
        target_mac = target_nic['target_nic']['szMacAddress']
        target_info = query_info_form_nics(target_mac, nics)
        if target_info:
            target_nic['ip_mask_pair'] = list(
                filter(lambda x: not (x['Ip'] == '0.0.0.0' or x['Ip'].startswith('169')), target_info['IpAndMask']))
            target_nic['dns_list'] = [dns for dns in target_info['Dns'] if dns.find(':') == -1]
            target_nic['gate_way'] = '' if target_info['GateWay'] == '0.0.0.0' else target_info['GateWay']
            target_nic['is_to_self'] = False
            target_nic['src_is_dhcp'] = target_info.get('Dhcp', '0') == '1'


# 列出目标机的所有网卡信息
def getAdapterSettings(request):
    query_params = request.GET

    request_need_adapter_set = query_params.get('needadapterset', default='0')
    result = {"r": "0", "e": "操作成功", "list": [], "src_nics": []}

    add_routers_info(request, result)

    if request_need_adapter_set == '1':  # 还原到源服务器，需要从源服务器获得源网卡设置
        # host_ident = query_params['serverid']
        return HttpResponse('{"r": "1","e": "needadapterset is 1. not support"}')
    elif request_need_adapter_set == '0':  # 还原到PE服务端，需要从PE服务端获得源网卡设置:
        pe_host_ident = query_params.get('destserverid', default=None)
        if not pe_host_ident:
            pe_host_ident = query_params.get('serverid', default=None)
        if not pe_host_ident:
            return HttpResponse('{"r": "1","e": "serverid is not exist"}')
        api_response = PeHostSessionInfo().get(request=request, ident=pe_host_ident)
        if not status.is_success(api_response.status_code):
            e = get_response_error_string(api_response)
            debug = "PeHostSessionInfo().get({}) failed {}".format(pe_host_ident, api_response.status_code)
            return HttpResponse(json.dumps({"r": 1, "e": e, "debug": debug}, ensure_ascii=False))
        point_params = query_params.get('hostsnapshot', None)
        if point_params is None:
            src_host_ident = query_params['srcIdent']
            src_host = get_object_or_404(Host, ident=src_host_ident)
            ext_info = json.loads(src_host.ext_info)
        # 添加源的网络信息--还原
        else:
            hostsnapshot_id = point_params.split('|')[1]  # 获取备份点中网卡信息
            hostsnapshot = HostSnapshot.objects.get(id=hostsnapshot_id)
            ext_info = json.loads(hostsnapshot.ext_info)
        src_is_linux = 'LINUX' in ext_info['system_infos']['System']['SystemCaption'].upper()
        check_adapter_dic = PeRestore.all_network_adapter_and_check(pe_host_ident, src_is_linux)
        if check_adapter_dic.get('r') == 1:
            return HttpResponse(json.dumps({"r": "1", "e": check_adapter_dic.get('e')}))
        target_adapters = api_response.data['network_adapters']  # 列出目标机所有网卡
        valid_adapter_list = list()
        is_valid_of_adapter_connected = False
        msg = ''
        for adapter in target_adapters[:]:
            current_adapter = check_adapter_dic[adapter['szMacAddress']]
            if current_adapter.get('is_valid'):
                valid_adapter_list.append(adapter['szDescription'])
                if current_adapter['isConnected']:
                    is_valid_of_adapter_connected = True
            else:
                if current_adapter['isConnected']:
                    msg = current_adapter['msg']
                target_adapters.remove(adapter)  # 当前adapter 不可用，移除
        if not is_valid_of_adapter_connected:
            if valid_adapter_list:
                # 请使用有线网络连接 , 有以下网卡可用 valid_adapter_list
                return HttpResponse(json.dumps({"r": "1",
                                                "e": "<p>系统检测到目标机正使用{msg}的网络连接，恢复功能暂不支持以该网络连接进行恢复。<br>请先禁用目标机上的{msg}，然后再使用有线网络重新连接</p>".format(
                                                    msg=msg)}))

            else:
                # 不可恢复，请使用有线网络
                return HttpResponse(json.dumps({"r": "1", "e": "没有检测到有效的有线网卡"}))

        result['list'] = [{
            'target_nic': adapter,
            'ip_mask_pair': [],
            'dns_list': [],
            'gate_way': '',
            'is_set': False,  # 由界面指定, 该网卡是否有效配置了
            'is_to_self': False,  # 是否还原到本机, True时, 界面可能会修改
            'src_instance_id': '',  # 该网卡在源中的instance_id, 当is_to_self=True时有意义
            'src_is_dhcp': False  # 该网卡在源中为DHCP
        } for adapter in target_adapters]
        result['list'].sort(key=lambda item: item['target_nic']['isConnected'], reverse=True)  # 主网卡放置第一个

        point_params = query_params.get('hostsnapshot', None)
        # 添加源的网络信息--迁移
        if point_params is None:
            src_host_ident = query_params['srcIdent']
            src_host = get_object_or_404(Host, ident=src_host_ident)
            ext_info = json.loads(src_host.ext_info)
            result['src_nics'] = ext_info['system_infos']['Nic']
            set_target_nic_info_from_pe_info(result['list'], api_response.data['system_infos'])  # 添加目标的网络信息
            return HttpResponse(json.dumps(result, ensure_ascii=False))
        # 添加源的网络信息--还原
        else:
            hostsnapshot_id = point_params.split('|')[1]  # 获取备份点中网卡信息
            hostsnapshot = HostSnapshot.objects.get(id=hostsnapshot_id)
            ext_info = json.loads(hostsnapshot.ext_info)
            result['src_nics'] = ext_info['system_infos']['Nic']

            # pe_host = RestoreTarget.objects.get(ident=pe_host_ident)
            # if not TargetHardware.is_target_connected_nic_exist_in_source_nics(hostsnapshot, pe_host):
            #     set_target_nic_info_from_pe_info(result['list'], api_response.data['system_infos'])  # 非本机还原，填写目标的网络信息
            #     return HttpResponse(json.dumps(result, ensure_ascii=False))
            #
            # set_target_nics_info_from_src_nics(result['list'], ext_info)  # 本机还原: 在源中查找目标网卡信息
            # return HttpResponse(json.dumps(result, ensure_ascii=False))

            dest_ident = query_params['destIdent']
            # 源机还原
            if hostsnapshot.host.ident == dest_ident:
                set_target_nics_info_from_src_nics(result['list'], ext_info)  # 本机还原: 在源中查找目标网卡信息
            else:
                set_target_nic_info_from_pe_info(result['list'], api_response.data['system_infos'])  # 非本机还原，填写目标的网络信息
            return HttpResponse(json.dumps(result, ensure_ascii=False))

    else:
        return HttpResponse('{{"r": "1","e": "needadapterset invalid : {}"}}'.format(request_need_adapter_set))


def get_disk_sn_by_disk_id(disk_id, pe_diskinfos):
    for disk in pe_diskinfos:
        if int(disk['diskid']) == int(disk_id):
            return disk['sn']

    _logger.warning(
        'get_disk_sn_by_disk_id, not found sn, disk_id={}, pe_diskinfos={}.'.format(disk_id, pe_diskinfos))
    return '???'


def query_pe_disks_sn_and_add_to_retinfo(retinfo, pe_ident):
    if not retinfo.get('destlist', []):
        return False

    try:
        json_str, _ = boxService.box_service.PEJsonFunc(pe_ident, json.dumps({'type': 'get_disk_info'}))
        pe_diskinfos = json.loads(json_str)['diskinfos']
        _logger.info('query_pe_disks_sn_and_add_to_retinfo, get_disk_info: {}.'.format(json_str))
    except Exception as e:
        _logger.info('query_pe_disks_sn_and_add_to_retinfo, error: {}, give up.'.format(e))
        return False

    for disk in retinfo['destlist']:
        disk['disk_sn'] = get_disk_sn_by_disk_id(disk['id'], pe_diskinfos)

    return True


# src: disk_index    dest: disk_index
def getHardSettings(request):
    query_params = request.GET
    origin_ident = query_params['srcserverid']
    target_ident = query_params['destserverid']

    # pe disks信息
    # target_disks = PeHostSessionInfo().get(request=None, ident=target_ident).data['disks']
    _data = PeHostSessionInfo().get(request=None, ident=target_ident).data
    _logger.debug(_data)
    target_disks = _data['disks']
    _logger.debug(target_disks)

    destlist = [{'id': tar_disk['disk_id'],
                 'name': 'disk{0} {1:.2f}GB {2}'.format(
                     tar_disk['disk_id'],
                     tar_disk['disk_bytes'] / 1024 ** 3,
                     '(引导盘)' if tar_disk['is_boot_device'] else ''),
                 'bootable': tar_disk['is_boot_device'],
                 'totalsize': '{0:.2f}'.format(tar_disk['disk_bytes'] / 1024 ** 3)
                 } for tar_disk in target_disks]

    # agent disks信息
    respn = HostSessionDisks().get(request=request, ident=origin_ident)
    if not status.is_success(respn.status_code):
        return HttpResponse(content='{"r": "1", "e": "查询agent信息失败"}')
    origin_disks = respn.data
    srclist = [{'id': disk['index'], 'is_support': backup.check_is_support_disk(disk['index'], origin_ident),
                'name': disk['name'], 'bootable': disk['boot_able'],
                'totalsize': '{0:.2f}'.format(disk['bytes'] / 1024 ** 3)} for disk in origin_disks]

    if origin_ident in backup.disks_status:
        del backup.disks_status[origin_ident]

    retInfo = {'r': '0', 'e': '操作成功', 'srclist': srclist, 'destlist': destlist}

    hostDetail = HostSessionInfo().get(request=request, ident=origin_ident).data

    is_windows = backup.is_windows_host(origin_ident)
    if is_windows:
        try:
            BuildNumber = hostDetail['os_version']
            if BuildNumber == 'Unknown version' or int(BuildNumber) <= 7601:
                retInfo['replace_efi'] = True
        except Exception as e:
            _logger.info('getHardSettings BuildNumber Failed replace_efi.ignore.e={}'.format(e))
            retInfo['replace_efi'] = True

    query_pe_disks_sn_and_add_to_retinfo(retInfo, target_ident)
    jsonStr = json.dumps(retInfo, ensure_ascii=False)
    return HttpResponse(jsonStr)


def check_authorize_at_migrate():
    migrate_tasks = MigrateTask.objects.filter(finish_datetime__isnull=True)

    json_txt = authorize_init.get_authorize_plaintext_from_local()
    if json_txt is None:
        return False, '读取授权文件异常'
    val = authorize_init.get_license_val_by_guid('migrate_task_concurrent', json_txt)
    if val is None:
        return False, '读取授权文件异常'
    if len(migrate_tasks) >= int(val):
        return False, '同时迁移任务数超过授权允许值({0}个), 目前{1}个任务正在进行中'.format(val, len(migrate_tasks))

    return True, ''


def get_shell_infos_when_enable_shell(shell_infos_str):
    if shell_infos_str is None:
        return None

    shell_infos = json.loads(shell_infos_str)
    shell_infos['zip_path'] = file_utils.move_tmp_file(shell_infos['zip_tmp_path'])

    return json.dumps(shell_infos)


def add_shell_infos_to_host_ext(origin_ident, shell_infos_str):
    shell_infos_str = get_shell_infos_when_enable_shell(shell_infos_str)

    host = Host.objects.get(ident=origin_ident)
    ext_info = json.loads(host.ext_info)

    if shell_infos_str is None:
        ext_info.pop('shellInfoStr', 'nothing')
    else:
        ext_info['shellInfoStr'] = shell_infos_str

    host.ext_info = json.dumps(ext_info)
    host.save(update_fields=['ext_info'])


def re_generate_adapters_params(ipconfig_infos):
    return [
        {
            'adapter': ui_nic_cfg['target_nic']['szGuid'],
            'ip': ui_nic_cfg['ip_mask_pair'][0]['Ip'],
            'subnet_mask': ui_nic_cfg['ip_mask_pair'][0]['Mask'],
            'routers': ui_nic_cfg['gate_way'],
            'dns': ui_nic_cfg['dns_list'][0] if ui_nic_cfg['dns_list'] else '',
            'multi_infos': json.dumps(ui_nic_cfg)
        } for ui_nic_cfg in ipconfig_infos if ui_nic_cfg['is_set']
    ]


# 执行迁移：源host-->目的host
def startMigrate(request):
    if authorize_init.is_evaluation_and_expiration():
        return HttpResponse(json.dumps({"r": "1", "e": "试用版已过期", "list": []}))

    if not UserQuotaTools.is_user_allocated_any_quota(request.user):
        return HttpResponse(json.dumps({"r": "1", "e": "用户无任何配额, 不能执行迁移<br>请联系管理员为用户分配配额", "list": []}))

    result = check_authorize_at_migrate()
    if not result[0]:
        return HttpResponse(json.dumps({"r": "1", "e": result[1], "list": []}))

    paramsQ = request.POST
    origin_ident = paramsQ.get('srcserverid', default=None)
    target_ident = paramsQ.get('destserverid', default=None)
    migr_disks_map = json.loads(paramsQ.get('disks', default=None))
    ipconfig_infos = json.loads(paramsQ.get('adapters', '{}'))
    drivers_ids = request.POST['drivers_ids']
    drivers_type = request.POST['drivers_type']
    routers = request.POST['routers']
    drivers_ids_force = request.POST.get('drivers_ids_force', '')

    clret = authorize_init.check_host_rebuild_count(target_ident)
    if clret.get('r', 0) != 0:
        return HttpResponse(json.dumps(clret, ensure_ascii=False))

    desc = {'操作': '迁移', '源目标': str(origin_ident), '目的': str(target_ident)}
    if not origin_ident or not target_ident or not migr_disks_map or not ipconfig_infos:
        return HttpResponse('{"r":"1","e":"操作失败：请求参数错误"}')
    try:
        drivers_list_str = GetDriversVersions.get_drivers_list_str(target_ident, drivers_ids, origin_ident,
                                                                   drivers_type, restore_to_self=False,
                                                                   user_id=request.user.id,
                                                                   driver_ids_force=drivers_ids_force)
        drivers_list_str = drivers_list_str.replace('\\', '|')
    except Exception as e:
        msg = traceback.format_exc()
        _logger.error('startMigrate fail {}'.format(e))
        _logger.error(msg)
        return HttpResponse(json.dumps({"r": "1", "e": "操作失败"}, ensure_ascii=False))

    userid = str(request.user.id)
    user_fingerprint = request.user.userprofile.user_fingerprint
    agent_user_info = userid + '|*' + user_fingerprint.hex

    enablekvm = '0'
    remote_kvm_params = dict()
    remote_kvm_params['enablekvm'] = str(enablekvm)
    if enablekvm == '0':
        remote_kvm_params['ssh_ip'] = ''
        remote_kvm_params['ssh_port'] = ''
        remote_kvm_params['ssh_key'] = ''
        remote_kvm_params['aio_ip'] = ''
        remote_kvm_params['ssh_path'] = ''
        remote_kvm_params['ssh_os_type'] = ''
    else:
        remote_kvm_params['ssh_ip'] = str(paramsQ['ssh_ip'])
        remote_kvm_params['ssh_port'] = str(paramsQ['ssh_port'])
        remote_kvm_params['ssh_key'] = str(paramsQ['ssh_key'])
        remote_kvm_params['aio_ip'] = str(paramsQ['aio_ip'])
        remote_kvm_params['ssh_path'] = os.path.join(str(paramsQ['ssh_path']),
                                                     '{}_{}'.format('migrate', time.time()))
        remote_kvm_params['ssh_os_type'] = str(paramsQ['ssh_os_type'])

    migr_params = {
        'pe_host_ident': target_ident,
        'disks': migr_disks_map,
        'adapters': re_generate_adapters_params(ipconfig_infos),
        'drivers_ids': drivers_list_str,
        'agent_user_info': agent_user_info,
        'routers': routers,
        'disable_fast_boot': paramsQ['disable_fast_boot'] == 'true',
        'replace_efi': paramsQ['replace_efi'] == 'true',
        'remote_kvm_params': remote_kvm_params,
        'diskreadthreadcount': int(paramsQ.get('thread_count', '4'))
    }

    add_shell_infos_to_host_ext(origin_ident, paramsQ.get('shell_infos', None))
    resp = HostSessionMigrate().post(request=request, ident=origin_ident, api_request=migr_params)
    if status.is_success(resp.status_code):
        desc.update({'操作结果': '成功'})
        SaveOperationLog(
            request.user, OperationLog.TYPE_MIGRATE, json.dumps(desc, ensure_ascii=False), get_operator(request))
        authorize_init.save_host_rebuild_record(target_ident)
        return HttpResponse('{"r":"0","e":"操作成功"}')
    else:
        desc.update({'操作结果': '失败，{}'.format(resp.data)})
        SaveOperationLog(
            request.user, OperationLog.TYPE_MIGRATE, json.dumps(desc, ensure_ascii=False), get_operator(request))
        return HttpResponse(json.dumps({"r": "1", "e": resp.data}, ensure_ascii=False))


# agent detail
def src_client_detail(request):
    return backup.getServerDetail(request)


# pe detail
def tar_pe_detail(request):
    pe_info = PeHostSessionInfo().get(request=request, ident=request.GET['id'])
    if not status.is_success(pe_info.status_code):
        _logger.error(
            r'tar_pe_detail PeHostSessionInfo.get() Failed.pe_host_ident={}'.format(request.GET['id']))
        return HttpResponse(json.dumps({"r": "1", "e": pe_info.data}, ensure_ascii=False))
    pe_info = pe_info.data
    disks = pe_info['disks']
    total_bytes = 0
    for disk in disks:
        total_bytes += disk['disk_bytes']

    ret_info = {
        "r": 0, "e": "操作成功",
        'pe_name': pe_info['pe_host']['display_name'],
        'disk_num': len(disks),
        'total_gb': '{0:.2f}'.format(total_bytes / 1024 ** 3),
        'ip': pe_info['pe_host']['display_name'].split(' ')[0],
    }
    return HttpResponse(content=json.dumps(ret_info, ensure_ascii=False))


def _is_dest_host_in_audit(agentserverid):
    # 当前选择目标机的mac
    target = RestoreTarget.objects.filter(ident=agentserverid)
    if target:
        target = target.first()
        info_obj = json.loads(target.info)
        src_mac_list = (
            [xdata.standardize_mac_addr(net_adapter['Mac']) for net_adapter in
             info_obj['system_infos']['Nic']])
    else:
        target = Host.objects.filter(ident=agentserverid)
        if target:
            target = target.first()
            ext_info_obj = json.loads(target.ext_info)
            src_mac_list = (
                [xdata.standardize_mac_addr(net_adapter['Mac']) for net_adapter in ext_info_obj['system_infos']['Nic']])
        else:
            _logger.error('_is_dest_host_in_audit not find src host mac agentserverid={}.ignore.'.format(agentserverid))
            return False

    # 所有待审批的pc_restore/vol_restore目标机的mac地址
    dest_mac_list = list()
    pe_host_idents = list()
    audit_tasks = audit_task.objects.filter(status=audit_task.AUIDT_TASK_STATUS_WAITE)
    for task in audit_tasks:
        task_info_obj = json.loads(task.task_info)
        if task_info_obj['task_type'] in ('pc_restore', 'vol_restore',):
            pe_host_idents.append(task_info_obj['pe_host_ident'])

    for pe_host_ident in pe_host_idents:
        target = RestoreTarget.objects.filter(ident=pe_host_ident)
        if not target:
            _logger.error('_is_dest_host_in_audit Failed.ignore.pe_host_ident={}'.format(pe_host_ident))
            continue
        target = target.first()
        info_obj = json.loads(target.info)
        dest_mac_list.extend(([xdata.standardize_mac_addr(net_adapter['Mac']) for net_adapter in
                               info_obj['system_infos']['Nic']]))

    for src_mac in src_mac_list:
        if src_mac in dest_mac_list:
            return True
    return False


def getserverid(request):
    agentserverid = request.GET.get('agentserverid', 'none')
    if hasFunctional('clw_desktop_aio'):
        if _is_dest_host_in_audit(agentserverid):
            return HttpResponse(json.dumps({'r': 2, 'e': '恢复目标正在等待审批'}, ensure_ascii=False))
    rspn = Agent2Pe().post(request=request, host_ident=agentserverid, api_request={})
    if status.is_success(rspn.status_code):
        return HttpResponse('{"r":"0","e":"操作成功","serverid":"%s","destserverid":"%s"}' % (agentserverid, rspn.data))

    return HttpResponse(json.dumps({'r': 1, 'e': rspn.data}, ensure_ascii=False))


def dest_host_in_audit(request):
    agentserverid = request.GET.get('agentserverid', 'none')
    if _is_dest_host_in_audit(agentserverid):
        return HttpResponse(json.dumps({'r': 2, 'e': '恢复目标正在等待审批'}, ensure_ascii=False))
    return HttpResponse(json.dumps({'r': 0, 'e': '操作成功'}, ensure_ascii=False))


def add_routers_info(request, result):
    src_ident = request.GET.get("srcIdent", default=" ")
    dest_ident = request.GET.get("destIdent", default=" ")
    rsp = HostRoutersInfo().get(request, src_ident)
    if status.is_success(rsp.status_code):
        result["src_routers"] = rsp.data["routers"]
    else:
        result["src_routers"] = list()
    if dest_ident:
        rsp1 = HostRoutersInfo().get(request, dest_ident)
        if status.is_success(rsp1.status_code):
            result["dest_routers"] = rsp1.data["routers"]
        else:
            result["dest_routers"] = list()
    else:
        result["dest_routers"] = list()
    return None


def migrate_handler(request):
    a = request.GET.get('a', 'none')
    if a == 'none':
        a = request.POST.get('a', 'none')
    if a == 'getserverid':
        return getserverid(request)
    if a == 'startmigrate':
        return startMigrate(request)
    if a == 'serverlist':
        return getServerList(request)
    if a == 'destserverlist':
        return getDestServerList(request)
    if a == 'srcclientinfo':
        return src_client_detail(request)
    if a == 'tarclientinfo':
        return src_client_detail(request)
    if a == 'tarpeclientinfo':
        return tar_pe_detail(request)
    if a == 'adaptersettings':
        return getAdapterSettings(request)
    if a == 'harddisksettings':
        return getHardSettings(request)
    if a == 'dest_host_in_audit':
        return dest_host_in_audit(request)
    return HttpResponse(json.dumps({"r": "1", "e": "没有对应的action:{}".format(html.escape(a))}))
