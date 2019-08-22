# coding=utf-8
import configparser
import datetime
import html
import json
import os
import re
import shutil
import subprocess
import threading
import time
import traceback
import zipfile
from os import system
from Crypto.PublicKey import RSA
import django.utils.timezone as timezone
import psutil
from IPy import IP
from django.core.paginator import Paginator
from django.http import HttpResponse

from apiv1.database_space_alert import get_database_size
from apiv1.models import UserQuota, StorageNode
from apiv1.planScheduler import BackupDatabaseBase, BackupDatabase
from apiv1.takeover_task import takeover_kvm_wrapper
from box_dashboard import xlogging, boxService, xdata, xdatetime
from box_dashboard.boxService import box_service
from xdashboard.common.dict import SaveDictionary, GetDictionary, GetDictionaryByTpye, DelDictionaryByType
from xdashboard.common.file_utils import GetFileMd5
from xdashboard.common.smtp import send_mail
from xdashboard.handle import ntp
from xdashboard.handle.authorize.authorize_init import get_separation_of_the_three_members
from xdashboard.handle.version import get_oem_info
from xdashboard.models import DataDictionary, sshkvm
from xdashboard.models import OperationLog, Email, UserProfile
from xdashboard.request_util import get_operator
from .logserver import SaveOperationLog
from .sysSetting.dhcp_conf import CDhcpConfig
from .sysSetting.ip_pxe import CIpPxe

READ_SIZE = 1 * 1024 * 1024
_logger = xlogging.getLogger(__name__)

_g_route_rollback_handle = None
_g_ipset_rollback_handle = None
_g_tcpdump_handle = None
_g_listen_to_tcpdump_handle = None
last_net_info = list()
tcpdump_file_path = '/sbin/aio/box_dashboard/xdashboard/static/download/tcpdump'
backup_ini = '/sbin/aio/box_dashboard/pgsql_backup/backup.ini'


# tftpFilepath = 'F:/etc/xinetd.d/tftp'     tftp文件预先必须存在
# dhcpFilepath = 'F:/etc/dhcp/dhcpd.conf'   dhcpd.conf文件预先可不存在
# current_dir = os.path.split(os.path.realpath(__file__))[0]
# dhcpFilepath = os.path.join(current_dir, 'sysSetting', 'testFiles', 'dhcpd.conf')
# tftpFilepath = os.path.join(current_dir, 'sysSetting', 'testFiles', 'tftp')
# tftpFile = TFTPConfigFile(tftpFilepath)
# tftpFile.disableIsNo()

def get_sub_net_list_one_by_ip(sub_net_list, ip, name):
    try:
        for one_net in sub_net_list:
            if one_net['next-server'] == ip:
                return one_net[name]
        return None
    except:
        _logger.error(traceback.format_exc())
        return None


def _excute_cmd_and_return_code(cmd):
    _logger.info("_excute_cmd_and_return_code cmd:{}".format(cmd))
    with subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True) as p:
        stdout, stderr = p.communicate()
    if p.returncode != 0:
        return p.returncode, stderr
    else:
        return p.returncode, stdout


# info = '{"r":0,"e":"操作成功","adapter":[{"id":"eth0","name":"eth0","ip":"192.16.8.1.12","submask":"255.255.255.0","routers":"192.168.1.1"}],' \
#       '"aggregation":[{"id":"aggregation0","name":"aggregation0","ip":"192.16.8.1.13","submask":"255.255.255.0","routers":"192.168.1.1","list":[{"id":"eth1","name":"eth1"},{"id":"eth2","name":"eth2"}]},' \
#       '{"id":"aggregation1","name":"aggregation1","ip":"192.16.8.1.13","submask":"255.255.255.0","routers":"192.168.1.1","list":[{"id":"eth3","name":"eth3"},{"id":"eth4","name":"eth4"}]}],' \
#       '"dns":["192.168.1.1","192.168.1.2"]}'
def getAdapterSet(request):
    pxe_file_dir = '/var/lib/tftpboot'
    config = CDhcpConfig()
    sub_net_list, host_list = config.get_dhcp_config()
    jsonstr = box_service.getNetworkInfos()
    adapterlist = json.loads(jsonstr)
    if len(adapterlist) != 2 or type(adapterlist) != type([]):
        return HttpResponse(json.dumps({"r": 1, "e": adapterlist}, ensure_ascii=False))
    info = {"r": 0, "e": "操作成功"}
    info['adapter'] = list()
    info['aggregation'] = list()
    aggregation = list()
    info['gateway'] = ''
    if request.GET.get('check_pxe', 0):
        info['sys_pxe_status'] = 1 if CIpPxe().get_pxe_status() else 0
    for element in adapterlist:
        if (type(element) == type([])):
            info['dns'] = element
        if (type(element) == type({})):
            for name, adapter in element.items():
                if adapter['nettype'] == 'phy' and adapter['mastername'] == '':
                    one = dict()
                    one['id'] = adapter['name']
                    one['name'] = adapter['name']
                    one['ip'] = adapter['ip4']
                    one['submask'] = adapter['netmask']
                    one['routers'] = adapter['gateway']
                    one['mac'] = adapter['mac']
                    one['state'] = adapter['state']
                    one['speed'] = adapter['speed']
                    one['link'] = adapter['link']
                    one['subipset'] = adapter['subipset']
                    if adapter['gateway']:
                        info['gateway'] = adapter['gateway']
                    if get_sub_net_list_one_by_ip(sub_net_list, one['ip'], 'next-server') is None:
                        one['enablepxe'] = 0
                        one['rangestart'] = None
                        one['rangeend'] = None
                        one['pxerouters'] = None
                        one['pxedns'] = None
                        one['defaultleasetime'] = None
                        one['maxleasetime'] = None
                    else:
                        one['enablepxe'] = 1
                        one['rangestart'] = get_sub_net_list_one_by_ip(sub_net_list, one['ip'], 'range_start')
                        one['rangeend'] = get_sub_net_list_one_by_ip(sub_net_list, one['ip'], 'range_end')
                        one['pxerouters'] = get_sub_net_list_one_by_ip(sub_net_list, one['ip'], 'routers')
                        ip_pxe_class = CIpPxe()
                        ini_list = ip_pxe_class.read_ip_ini(one['ip'], pxe_file_dir)
                        if ini_list is None:
                            one['pxedns'] = None
                        else:
                            one['pxedns'] = ini_list['dns']
                        one['defaultleasetime'] = get_sub_net_list_one_by_ip(sub_net_list, one['ip'],
                                                                             'default-lease-time')
                        one['maxleasetime'] = get_sub_net_list_one_by_ip(sub_net_list, one['ip'], 'max-lease-time')
                    info['adapter'].append(one)
                if adapter['nettype'] == 'bond':
                    one = dict()
                    one['list'] = list()
                    one['id'] = adapter['name']
                    one['name'] = '聚合网络接口'
                    one['ip'] = adapter['ip4']
                    one['submask'] = adapter['netmask']
                    one['routers'] = adapter['gateway']
                    one['mac'] = adapter['mac']
                    one['state'] = adapter['state']
                    one['speed'] = adapter['speed']
                    one['link'] = adapter['link']
                    one['subipset'] = adapter['subipset']
                    if adapter['gateway']:
                        info['gateway'] = adapter['gateway']
                    if get_sub_net_list_one_by_ip(sub_net_list, one['ip'], 'next-server') is None:
                        one['enablepxe'] = 0
                        one['rangestart'] = None
                        one['rangeend'] = None
                        one['pxerouters'] = None
                        one['pxedns'] = None
                        one['defaultleasetime'] = None
                        one['maxleasetime'] = None
                    else:
                        one['enablepxe'] = 1
                        one['rangestart'] = get_sub_net_list_one_by_ip(sub_net_list, one['ip'], 'range_start')
                        one['rangeend'] = get_sub_net_list_one_by_ip(sub_net_list, one['ip'], 'range_end')
                        one['pxerouters'] = get_sub_net_list_one_by_ip(sub_net_list, one['ip'], 'routers')
                        ip_pxe_class = CIpPxe()
                        ini_list = ip_pxe_class.read_ip_ini(one['ip'], pxe_file_dir)
                        if ini_list is None:
                            one['pxedns'] = None
                        else:
                            one['pxedns'] = ini_list['dns']
                        one['defaultleasetime'] = get_sub_net_list_one_by_ip(sub_net_list, one['ip'],
                                                                             'default-lease-time')
                        one['maxleasetime'] = get_sub_net_list_one_by_ip(sub_net_list, one['ip'], 'max-lease-time')
                    aggregation.append(one)

    for element in adapterlist:
        if (type(element) == type({})):
            for name, adapter in element.items():
                if adapter['nettype'] == 'phy' and adapter['mastername'] != '':
                    for item in aggregation:
                        if item['id'] == adapter['mastername']:
                            one = dict()
                            one['id'] = adapter['name']
                            one['name'] = adapter['name']
                            one['ip'] = adapter['ip4']
                            one['submask'] = adapter['netmask']
                            one['routers'] = adapter['gateway']
                            one['mac'] = adapter['mac']
                            one['state'] = adapter['state']
                            one['speed'] = adapter['speed']
                            one['link'] = adapter['link']
                            one['enablepxe'] = 0
                            one['rangestart'] = ''
                            one['rangeend'] = ''
                            one['pxerouters'] = ''
                            one['pxedns'] = ''
                            one['defaultleasetime'] = ''
                            one['maxleasetime'] = ''
                            item['list'].append(one)
                            break
    for item in aggregation:
        if len(item['list']) > 0:
            info['aggregation'].append(item)
    _init_last_net_info(info)
    return HttpResponse(json.dumps(info, ensure_ascii=False))


# info = '{"r":0,"e":"操作成功","adapter":[{"id":"eth0","name":"eth0","ip":"192.16.8.1.12","submask":"255.255.255.0","routers":"192.168.1.1"}],' \
#       '"aggregation":[{"id":"aggregation0","name":"aggregation0","ip":"192.16.8.1.13","submask":"255.255.255.0","routers":"192.168.1.1","list":[{"id":"eth1","name":"eth1"},{"id":"eth2","name":"eth2"}]},' \
#       '{"id":"aggregation1","name":"aggregation1","ip":"192.16.8.1.13","submask":"255.255.255.0","routers":"192.168.1.1","list":[{"id":"eth3","name":"eth3"},{"id":"eth4","name":"eth4"}]}],' \
#       '"dns":["192.168.1.1","192.168.1.2"]}'

# box_service.setNetwork(net_infos)参数如下：
# [
#	['interface',['eno16777984'],['172.16.6.165','255.255.248.0','']],
#    ['interface',['eno33557248','eno50336512','eno67115776'],['172.16.6.167','255.255.248.0','172.16.1.1']],
#    ['interface',['eno33557248'],['172.16.6.168','255.255.248.0','']],
#    ['dns',['172.16.1.1','172.16.1.2']]
# ]
def setAdapterSet(request):
    global last_net_info, _g_ipset_rollback_handle
    has_set_net_work = 0
    if 'info' in request.POST:
        info = request.POST['info']
    else:
        return HttpResponse('{"r": "1","e": "参数错误","is_set_net":"0"}')
    adapterlist = json.loads(info)
    adapterlist.pop('r')
    adapterlist.pop('e')
    net_infos = list()
    net_infos_just_for_check_dont_use = list()

    user = request.user
    event = OperationLog.TYPE_ADAPTER

    if 'adapter' in adapterlist:
        for element in adapterlist['adapter']:
            adlist = list()
            adlist.append(element['id'])
            ip = ''
            submask = ''
            routers = ''
            subipset = ''
            if 'ip' in element:
                ip = element['ip']
            if 'submask' in element:
                submask = element['submask']
            if 'routers' in element:
                routers = element['routers']
            if 'subipset' in element:
                subipset = element['subipset']
            state = ''
            if 'state' in element:
                state = element['state']
            net_infos.append(['interface', adlist, [ip, submask, adapterlist['gateway']], subipset])
            net_infos_just_for_check_dont_use.append(['interface', adlist, [ip, submask, routers, state]])

    if 'aggregation' in adapterlist:
        adlist = list()
        for element in adapterlist['aggregation']:
            for item in element['list']:
                adlist.append(item['id'])
            ip = ''
            submask = ''
            routers = ''
            subipset = ''
            if 'ip' in element:
                ip = element['ip']
            if 'submask' in element:
                submask = element['submask']
            if 'routers' in element:
                routers = element['routers']
            if 'subipset' in element:
                subipset = element['subipset']
            state = 'up'
            net_infos.append(['interface', adlist, [ip, submask, adapterlist['gateway']], subipset])
            net_infos_just_for_check_dont_use.append(['interface', adlist, [ip, submask, routers, state]])
            adlist = list()

    if 'dns' in adapterlist:
        adlist = list()
        for element in adapterlist['dns']:
            adlist.append(element)
        net_infos.append(['dns', adlist])

    # return HttpResponse(json.dumps(net_infos,ensure_ascii=False))
    _logger.info(json.dumps(net_infos, ensure_ascii=False))
    if not _check_interface_is_valid(net_infos_just_for_check_dont_use):
        return HttpResponse('{"r": "-1","e": "设置失败，请确保至少在一张已连接上网线的网卡上有正确的网络设置", "is_set_net":"0"}')
    try:
        if net_infos != last_net_info:
            box_service.setNetwork(json.dumps(net_infos, ensure_ascii=False))
            _g_ipset_rollback_handle = RollBackIpSet(last_net_info)
            _g_ipset_rollback_handle.setDaemon(True)
            _g_ipset_rollback_handle.start()
            last_net_info = net_infos
            has_set_net_work = 1
    except xlogging.BoxDashboardException as e:
        resp = {'r': '1', 'e': e.msg, 'is_set_net': has_set_net_work}
        return HttpResponse(json.dumps(resp))
    adapterlist['操作结果'] = '操作成功'
    SaveOperationLog(
        user, event, json.dumps(adapterlist, ensure_ascii=False), get_operator(request))
    return HttpResponse(json.dumps({"r": "0", "e": "操作成功", 'is_set_net': has_set_net_work}, ensure_ascii=False))


def adapter_pxe(request):
    user = request.user
    event = OperationLog.TYPE_PXE
    info = request.POST['info']
    pxe_file_dir = '/var/lib/tftpboot'
    adapterlist = json.loads(info)
    adapterlist.pop('r')
    adapterlist.pop('e')
    # ===================================================================================================================
    # wolf add
    _logger.debug('setAdapterSet begin info={}'.format(info))
    config = CDhcpConfig()
    one_sub_net = {'subnet': None, 'netmask': None, 'range_start': None, 'range_end': None,
                   'routers': None, 'subnet-mask': None, 'default-lease-time': None, 'max-lease-time': None,
                   'next-server': None, 'filename': None}
    one_host = {'name': None, 'hardware ethernet': None, 'fixed-address': None}
    sub_net_list = list()
    host_list = list()
    default_lease_time = str(2 * 60 * 60)
    max_lease_time = str(2 * 60 * 60)
    _logger.debug('setAdapterSet begin 2 adapterlist["adapter"]={}'.format(adapterlist['adapter']))
    for one_adapter in adapterlist['adapter']:
        _logger.debug('setAdapterSet begin 3 one_adapter = {}'.format(one_adapter))
        if 1 != one_adapter.get('enablepxe', 0):
            continue
        _logger.debug('setAdapterSet begin 4 one_adapter = {}'.format(one_adapter))
        one_sub_net['subnet'] = IP(one_adapter['ip']).make_net(one_adapter['submask']).strNormal(0)
        one_sub_net['netmask'] = one_adapter['submask']
        one_sub_net['range_start'] = one_adapter['rangestart']
        one_sub_net['range_end'] = one_adapter['rangeend']
        one_sub_net['routers'] = one_adapter['pxerouters']
        one_sub_net['subnet-mask'] = one_adapter['submask']
        one_sub_net['default-lease-time'] = default_lease_time
        one_sub_net['max-lease-time'] = max_lease_time
        one_sub_net['next-server'] = one_adapter['ip']
        one_sub_net['filename'] = 'grldr'
        sub_net_list.append(one_sub_net.copy())
        one_host['name'] = one_adapter['id']
        one_host['hardware ethernet'] = one_adapter['mac']
        one_host['fixed-address'] = one_adapter['ip']
        host_list.append(one_host.copy())
        ip_pxe_class = CIpPxe()
        pxe_ini_list = ip_pxe_class.read_ini(pxe_file_dir + '/ip_pxe.txt')
        pxe_ini_list['dns'] = one_adapter['pxedns']
        pxe_ini_list['serv_ip'] = one_adapter['ip']
        pxe_ini_list['mask'] = one_adapter['submask']
        pxe_ini_list['gateway_ip'] = one_adapter['pxerouters']
        ip_pxe_class.set_ip_ini(one_adapter['ip'], pxe_ini_list, pxe_file_dir)
    for one_adapter in adapterlist['aggregation']:
        _logger.debug('setAdapterSet begin 5 one_adapter = {}'.format(one_adapter))
        if 1 != one_adapter.get('enablepxe', 0):
            continue
        _logger.debug('setAdapterSet begin 6 one_adapter = {}'.format(one_adapter))
        one_sub_net['subnet'] = IP(one_adapter['ip']).make_net(one_adapter['submask']).strNormal(0)
        one_sub_net['netmask'] = one_adapter['submask']
        one_sub_net['range_start'] = one_adapter['rangestart']
        one_sub_net['range_end'] = one_adapter['rangeend']
        one_sub_net['routers'] = one_adapter['pxerouters']
        one_sub_net['subnet-mask'] = one_adapter['submask']
        one_sub_net['default-lease-time'] = default_lease_time
        one_sub_net['max-lease-time'] = max_lease_time
        one_sub_net['next-server'] = one_adapter['ip']
        one_sub_net['filename'] = 'grldr'
        sub_net_list.append(one_sub_net.copy())
        one_host['name'] = one_adapter['id']
        one_host['hardware ethernet'] = one_adapter['mac']
        one_host['fixed-address'] = one_adapter['ip']
        host_list.append(one_host.copy())
        ip_pxe_class = CIpPxe()
        pxe_ini_list = ip_pxe_class.read_ini(pxe_file_dir + '/ip_pxe.txt')
        if not pxe_ini_list:
            return HttpResponse(json.dumps({"r": "-1", "e": "操作失败，配置文件不存在，无法操作。"}, ensure_ascii=False))
        pxe_ini_list['dns'] = one_adapter['pxedns']
        pxe_ini_list['serv_ip'] = one_adapter['ip']
        pxe_ini_list['mask'] = one_adapter['submask']
        pxe_ini_list['gateway_ip'] = one_adapter['pxerouters']
        ip_pxe_class.set_ip_ini(one_adapter['ip'], pxe_ini_list, pxe_file_dir)
    _logger.debug(sub_net_list)
    _logger.debug(host_list)
    config.write_dhcp_config(sub_net_list, host_list)
    if len(sub_net_list) == 0 or len(host_list) == 0:
        # 因为此时没有dhcpd.conf 实际是停止服务
        config.re_start_dhcp()
    elif 0 != config.re_start_dhcp():
        adapterlist['操作结果'] = '操作失败'
        SaveOperationLog(user, event, json.dumps(adapterlist, ensure_ascii=False), get_operator(request))
        return HttpResponse(json.dumps({"r": "-1", "e": "操作失败"}, ensure_ascii=False))
    adapterlist['操作结果'] = '操作成功'
    SaveOperationLog(user, event, json.dumps(adapterlist, ensure_ascii=False), get_operator(request))
    return HttpResponse(json.dumps({"r": "0", "e": "操作成功"}, ensure_ascii=False))


def _check_interface_is_valid(interfaces):
    good_sets = 0
    pattern = re.compile(r'^(\d+).(\d+).(\d+).(\d+)$')
    for oneset in interfaces:
        if oneset[0] == 'dns':
            continue
        # 网卡 未配置IP
        if not oneset[2]:
            continue
        # 网卡未连接上
        if oneset[2][3] != 'up':
            continue
        # IP 和 mask 为合法IP
        ip = pattern.match(oneset[2][0])
        mask = pattern.match(oneset[2][1])
        if ip and mask:
            i = 1
            j = 1
            for items in ip.groups():
                if not (int(items) >= 0 and int(items) <= 255):
                    i = 0
            for items in mask.groups():
                if not (int(items) >= 0 and int(items) <= 255):
                    j = 0
            if i and j:
                good_sets += 1
    return good_sets


def _init_last_net_info(adapterlist):
    try:
        global last_net_info
        last_net_info = list()
        if 'adapter' in adapterlist:
            for element in adapterlist['adapter']:
                adlist = list()
                adlist.append(element['id'])
                ip = ''
                submask = ''
                subipset = ''
                routers = ''
                if 'ip' in element:
                    ip = element['ip']
                if 'submask' in element:
                    submask = element['submask']
                if 'subipset' in element:
                    subipset = element['subipset']
                if 'routers' in element:
                    routers = element['routers']
                last_net_info.append(['interface', adlist, [ip, submask, routers], subipset])

        if 'aggregation' in adapterlist:
            adlist = list()
            for element in adapterlist['aggregation']:
                for item in element['list']:
                    adlist.append(item['id'])
                ip = ''
                submask = ''
                subipset = ''
                routers = ''
                if 'ip' in element:
                    ip = element['ip']
                if 'submask' in element:
                    submask = element['submask']
                if 'subipset' in element:
                    subipset = element['subipset']
                if 'routers' in element:
                    routers = element['routers']
                last_net_info.append(['interface', adlist, [ip, submask, routers], subipset])
                adlist = list()

        if 'dns' in adapterlist:
            adlist = list()
            for element in adapterlist['dns']:
                adlist.append(element)
            last_net_info.append(['dns', adlist])

    except:
        pass


def getMaxBandwidth(request):
    # 备份/恢复带宽设置
    info = '{"r":"0","bandwidth": "50"}'
    return HttpResponse(info)


def setMaxBandwidth(request):
    # 备份/恢复带宽设置
    return HttpResponse('{"r": "0","e": "操作成功"}')


def saveSMTP(request):
    # 保存MTP
    mail_host = request.POST.get('smtp_host', '')
    mail_port = int(request.POST.get('smtp_port', '25'))
    mail_user = request.POST.get('smtp_user', '')
    mail_pass = request.POST.get('smtp_pass', '')
    mail_mail = request.POST.get('smtp_mail', '')
    mail_ssl = request.POST.get('smtp_ssl', '0')
    onedata = DataDictionary.objects.filter(dictType=DataDictionary.DICT_TYPE_SMTP)
    user = request.user
    event = OperationLog.TYPE_SMTP
    info = {'邮件服务器地址': mail_host, '邮件服务器端口': mail_port, '发送人邮箱': mail_mail, '用户名': mail_user}

    SaveDictionary(DataDictionary.DICT_TYPE_SMTP, 'smtp_host', mail_host)
    SaveDictionary(DataDictionary.DICT_TYPE_SMTP, 'smtp_port', mail_port)
    SaveDictionary(DataDictionary.DICT_TYPE_SMTP, 'smtp_user', mail_user)
    SaveDictionary(DataDictionary.DICT_TYPE_SMTP, 'smtp_pass', mail_pass)
    SaveDictionary(DataDictionary.DICT_TYPE_SMTP, 'smtp_mail', mail_mail)
    SaveDictionary(DataDictionary.DICT_TYPE_SMTP, 'smtp_ssl', mail_ssl)

    info['操作结果'] = '操作成功'
    SaveOperationLog(user, event, json.dumps(info, ensure_ascii=False), get_operator(request))
    return HttpResponse('{"r": "0","e": "操作成功"}')


def getSMTP(request):
    # 得到SMTP设置
    smtp_host = ''
    smtp_port = '25'
    smtp_mail = ''
    smtp_user = ''
    smtp_pass = ''
    smtp_ssl = '0'
    onedates = GetDictionaryByTpye(DataDictionary.DICT_TYPE_SMTP)
    if onedates:
        for onedate in onedates:
            if onedate.dictKey == 'smtp_host':
                smtp_host = onedate.dictValue
            if onedate.dictKey == 'smtp_port':
                smtp_port = onedate.dictValue
            if onedate.dictKey == 'smtp_mail':
                smtp_mail = onedate.dictValue
            if onedate.dictKey == 'smtp_user':
                smtp_user = onedate.dictValue
            if onedate.dictKey == 'smtp_pass':
                smtp_pass = onedate.dictValue
            if onedate.dictKey == 'smtp_ssl':
                smtp_ssl = onedate.dictValue
    return HttpResponse(
        '{"r": "0","e": "操作成功","smtp_host":"%s","smtp_port":"%s","smtp_mail":"%s","smtp_user":"%s","smtp_pass":"%s","smtp_ssl":"%s"}' % (
            smtp_host, smtp_port, smtp_mail, smtp_user, smtp_pass, smtp_ssl))


def testSendEmail(request):
    eamilto = request.POST.get('eamilto', '')
    mail_host = request.POST.get('smtp_host', '')
    mail_port = int(request.POST.get('smtp_port', '25'))
    mail_user = request.POST.get('smtp_user', '')
    mail_pass = request.POST.get('smtp_pass', '')
    mail_mail = request.POST.get('smtp_mail', '')
    mail_ssl = request.POST.get('smtp_ssl', '0')
    smtp_set = {
        'smtp_host': mail_host,
        'smtp_port': mail_port,
        'smtp_mail': mail_mail,
        'smtp_user': mail_user,
        'smtp_pass': mail_pass,
        'smtp_ssl': mail_ssl,
    }
    oem = get_oem_info()
    mylog = dict()
    mylog['发送测试邮件'] = eamilto
    ret = send_mail(eamilto, "测试SMTP设置", oem['title'] + "SMTP设置正确", smtp_set)
    if (ret == 'OK'):
        mylog['操作结果'] = '成功'
        SaveOperationLog(
            request.user, OperationLog.TYPE_SMTP, json.dumps(mylog, ensure_ascii=False), get_operator(request))
        return HttpResponse('{"r": "0","e": "' + ret + '"}')
    mylog['操作结果'] = ret
    SaveOperationLog(
        request.user, OperationLog.TYPE_SMTP, json.dumps(mylog, ensure_ascii=False), get_operator(request))
    return HttpResponse(json.dumps({"r": "1", "e": ret}, ensure_ascii=False))


def send_email(request):
    eamil_to = request.POST.get('eamil_to', '')
    email_title = request.POST.get('email_title', '')
    email_content = request.POST.get('email_content', '')
    result = {'r': 0, 'e': '操作成功'}
    ret = send_mail(eamil_to, email_title, email_content)
    result['e'] = ret
    if ret != 'OK':
        result['r'] = 1
    return HttpResponse(json.dumps(result, ensure_ascii=False))


def _save_halt_flag():
    try:
        with open(r'/dev/shm/stop_watch_power_serv', 'w') as fout:
            fout.write('0')
    except Exception as e:
        pass


def _remove_halt_flag():
    if os.path.isfile(r'/dev/shm/stop_watch_power_serv'):
        os.remove(r'/dev/shm/stop_watch_power_serv')


def myhalt(request):
    pwd = request.POST.get('p', 'none')
    if pwd == '123456':
        system(r'sync')
        system(r'echo 3 > /proc/sys/vm/drop_caches')
        system("shutdown -h 1 --no-wall")
        _save_halt_flag()
        desc = {'操作': '关闭服务器'}
        SaveOperationLog(
            request.user, OperationLog.TYPE_SYSTEM_SET, json.dumps(desc, ensure_ascii=False), get_operator(request))
    return HttpResponse('{"r": "0","e": "操作成功"}')


def myreboot(request):
    pwd = request.POST.get('p', 'none')
    if pwd == '123456':
        system(r'sync')
        system(r'echo 3 > /proc/sys/vm/drop_caches')
        system("shutdown -r +1 --no-wall")
        mylog = {'操作': '重启服务器'}
        _save_halt_flag()
        SaveOperationLog(
            request.user, OperationLog.TYPE_SYSTEM_SET, json.dumps(mylog, ensure_ascii=False), get_operator(request))
    return HttpResponse('{"r": "0","e": "操作成功"}')


def mycancel(request):
    pwd = request.POST.get('p', 'none')
    if pwd == '123456':
        system("shutdown -c -k --no-wall")
        mylog = {'操作': '取消重启/关闭服务器'}
        _remove_halt_flag()
        SaveOperationLog(
            request.user, OperationLog.TYPE_SYSTEM_SET, json.dumps(mylog, ensure_ascii=False), get_operator(request))
    return HttpResponse('{"r": "0","e": "操作成功"}')


def setexpiry(request):
    t = request.POST.get('time', '0')
    lockcount = int(request.POST.get('lockcount', '0'))
    locktime = int(request.POST.get('locktime', '0'))
    if t == '':
        t = '0'
    t = int(t)
    if t < 0:
        t = 0
    if get_separation_of_the_three_members().is_strict_password_policy():
        if lockcount > 5 or lockcount < 3:
            HttpResponse(
                json.dumps({"r": "1", "e": "连续登录失败次数3-5次之间，当前设置为{}，请修改为合法的值。".format(lockcount)}, ensure_ascii=False))
    if lockcount < 0:
        lockcount = 0
    if locktime < 0:
        locktime = 0
    SaveDictionary(DataDictionary.DICT_TYPE_USER_LOGIN_FAILED_COUNT, 'limtcount', str(lockcount))
    SaveDictionary(DataDictionary.DICT_TYPE_LOGIN_LOCK_MIN, 'lock', str(locktime))
    SaveDictionary(DataDictionary.DICT_TYPE_EXPIRY, 'expiry', str(t))
    if t == '0':
        mylog = {'超时退出': '管理员登录Web无过期时间，不需要重新登录认证'}
    else:
        mylog = {'超时退出': '管理员登录Web管理页面后，如未主动退出，超过{}分钟后，需要重新登录认证'.format(t)}
    mylog["锁定帐号"] = '连续登录失败{}次，将锁定帐号。'.format(lockcount)
    mylog["锁定时间"] = '锁定{}分钟后，自动解锁。'.format(locktime)
    SaveOperationLog(
        request.user, OperationLog.TYPE_SYSTEM_SET, json.dumps(mylog, ensure_ascii=False), get_operator(request))
    return HttpResponse('{"r": "0","e": "操作成功"}')


def getexpiry(request):
    expiry = GetDictionary(DataDictionary.DICT_TYPE_EXPIRY, 'expiry', '120')
    if get_separation_of_the_three_members().is_strict_password_policy():
        lockcount = GetDictionary(DataDictionary.DICT_TYPE_USER_LOGIN_FAILED_COUNT, 'limtcount', '5')
    else:
        lockcount = GetDictionary(DataDictionary.DICT_TYPE_USER_LOGIN_FAILED_COUNT, 'limtcount', '10')
    locktime = GetDictionary(DataDictionary.DICT_TYPE_LOGIN_LOCK_MIN, 'lock', '30')
    return HttpResponse(
        json.dumps({"r": "0", "e": "操作成功", "expiry": expiry, "lockcount": lockcount, "locktime": locktime},
                   ensure_ascii=False))


def getpwdpolicy(request):
    if get_separation_of_the_three_members().is_strict_password_policy():
        policy = GetDictionary(DataDictionary.DICT_TYPE_PWD_POLICY, 'policy', '2')
        pwdcycle = GetDictionary(DataDictionary.DICT_TYPE_PWD_CYCLE, 'pwdcycle', '7')
    else:
        policy = GetDictionary(DataDictionary.DICT_TYPE_PWD_POLICY, 'policy', '3')
        pwdcycle = GetDictionary(DataDictionary.DICT_TYPE_PWD_CYCLE, 'pwdcycle', '0')
    if request.user:
        pwdexpriy = GetDictionary(DataDictionary.DICT_TYPE_PWD_EXPIRY, request.user.id, 'none')
    return HttpResponse(
        json.dumps({"r": "0", "e": "操作成功", "policy": "{}".format(policy), "pwdcycle": "{}".format(pwdcycle),
                    "pwdexpriy": pwdexpriy},
                   ensure_ascii=False))


def tpwdpolicy(policy):
    if policy == '1':
        return '强'
    if policy == '2':
        return '中'
    if policy == '3':
        return '弱'


def setpwdpolicy(request):
    policy = request.POST.get('policy', '3')
    pwdcycle = int(request.POST.get('pwdcycle', '0'))
    if get_separation_of_the_three_members().is_strict_password_policy():
        if pwdcycle > 7 or pwdcycle <= 0:
            return HttpResponse(json.dumps({"r": "1", "e": "密码周期{}天不合法，请更改为1-7天".format(pwdcycle)}, ensure_ascii=False))
    if pwdcycle < 0:
        pwdcycle = 0
    SaveDictionary(DataDictionary.DICT_TYPE_PWD_POLICY, 'policy', policy)
    oldpwdcycle = int(GetDictionary(DataDictionary.DICT_TYPE_PWD_CYCLE, 'pwdcycle', '0'))
    mylog = {'设置密码策略': tpwdpolicy(policy)}
    if pwdcycle != oldpwdcycle:
        if pwdcycle == 0:
            logpwdexpiry = '密码永不过期'
        else:
            logpwdexpiry = '密码使用期限{}天'.format(pwdcycle)
        SaveDictionary(DataDictionary.DICT_TYPE_PWD_CYCLE, 'pwdcycle', str(pwdcycle))
        DelDictionaryByType(DataDictionary.DICT_TYPE_PWD_EXPIRY)
        mylog["密码周期"] = logpwdexpiry
    SaveOperationLog(
        request.user, OperationLog.TYPE_SYSTEM_SET, json.dumps(mylog, ensure_ascii=False), get_operator(request))
    return HttpResponse('{"r": "0","e": "操作成功"}')


def getemailrange(request):
    allchoice = ','.join([str(i[0]) for i in Email.EMAIL_TYPE_CHOICES])
    res = GetDictionary(DataDictionary.DICT_TYPE_CHOICE_SEND_EMAIL_RANGE, 'range', allchoice)
    return HttpResponse(json.dumps({"r": "0", "e": "操作成功", 'active': res}, ensure_ascii=False))


def trange(range):
    if str(range) == '1':
        return '存储点配额不足'
    if str(range) == '2':
        return '存储点离线'
    if str(range) == '3':
        return '存储点不可用'
    if str(range) == '4':
        return 'CDP保护停止'
    if str(range) == '5':
        return 'CDP保护失败'
    if str(range) == '6':
        return 'CDP保护暂停'
    if str(range) == '8':
        return '备份失败'
    if str(range) == '9':
        return '备份成功'
    if str(range) == '10':
        return '迁移失败'
    if str(range) == '11':
        return '迁移成功'
    if str(range) == '12':
        return '还原失败'
    if str(range) == '13':
        return '还原成功'

    return '未知:' + str(range)


def setemailrange(request):
    range = request.GET.getlist('range', '')
    rangelist = list()
    for tr in range:
        rangelist.append(trange(tr))
    range = ','.join(range)
    logrange = ','.join(rangelist)
    SaveDictionary(DataDictionary.DICT_TYPE_CHOICE_SEND_EMAIL_RANGE, 'range', range)
    mylog = {'发送邮件范围': logrange}
    SaveOperationLog(
        request.user, OperationLog.TYPE_SMTP, json.dumps(mylog, ensure_ascii=False), get_operator(request))
    return HttpResponse('{"r": "0","e": "操作成功"}')


def setstartupmedia(request):
    params = request.GET
    mark_user = 'yes' if params.get('mark_user') == 'yes' else 'no'  # 为该ISO打上用户指纹: 'yes', 'no'
    bStatic = params.get('bStatic', '')
    start_ip = params.get('start_ip', '')
    end_ip = params.get('end_ip', '')
    mask = params.get('mask', '')
    gateway_ip = params.get('gateway_ip', '')
    dns1 = params.get('dns1', '')
    serv_ip = params.get('serv_ip', '')
    user_id = request.user.id
    port = '20000'
    oem = get_oem_info()
    exp_data = datetime.datetime.now() + datetime.timedelta(hours=12)
    exp_data_str = exp_data.strftime(xdatetime.FORMAT_WITH_SECOND)
    datetime_now_str = datetime.datetime.now().strftime(xdatetime.FORMAT_WITH_SECOND)
    filename = '{}{}'.format(oem['prefix'], xdata.PREFIX_ISO_FILE) + datetime_now_str + '.iso'
    user_fingerprint = '*{}'.format(request.user.userprofile.user_fingerprint)

    data = dict(
        bStatic=bStatic, start_ip=start_ip,
        mask=mask, gateway_ip=gateway_ip,
        dns1=dns1, serv_ip=serv_ip,
        port=port, end_ip=end_ip,
        user_id=user_id, exp_data=exp_data_str,
        filename=filename,
        mark_user=mark_user,
        user_fingerprint=user_fingerprint,
    )
    is_invalid = _check_ip_set(data)
    # 输入不合法
    if is_invalid['status'] == 0:
        msg = is_invalid['msg']
        return HttpResponse(json.dumps({"r": "1", "e": msg, 'status': '0', "msg": msg}, ensure_ascii=False))
    # 生成
    user_profile = get_user_profile(data)
    try:
        DoCreateWinpeWork(user_profile).work()
    except xlogging.BoxDashboardException as e:
        mylog = {'操作': '制作启动介质', '操作结果': "生成链接失败"}
        SaveOperationLog(
            request.user, OperationLog.TYPE_BOOT_ISO, json.dumps(mylog, ensure_ascii=False), get_operator(request))
        return HttpResponse(json.dumps({"r": "1", "e": '生成链接失败，{}'.format(str(e.msg)), 'error': str(e.debug)},
                                       ensure_ascii=False))
    except Exception as e:
        mylog = {'操作': '制作启动介质', '操作结果': "生成链接失败"}
        SaveOperationLog(
            request.user, OperationLog.TYPE_BOOT_ISO, json.dumps(mylog, ensure_ascii=False), get_operator(request))
        return HttpResponse(json.dumps({"r": "1", "e": "生成链接失败", 'error': str(e)}, ensure_ascii=False))
    url = os.path.join('/static/download/newwinpe/', str(user_id), filename)
    mylog = {'操作': '制作启动介质', '操作结果': "生成链接成功"}
    SaveOperationLog(
        request.user, OperationLog.TYPE_BOOT_ISO, json.dumps(mylog, ensure_ascii=False), get_operator(request))
    file_md5 = GetFileMd5('/var/www' + url)
    return HttpResponse(
        json.dumps({"r": "0", "e": "生成链接成功", "url": url, "exp_data": exp_data_str, "md5": file_md5},
                   ensure_ascii=False))


def getstartupmedia(request):
    user_profile = UserProfile.objects.filter(user_id=request.user.id)[0]
    if user_profile and user_profile.winpeset:
        data = json.loads(user_profile.winpeset)
        if data:
            data['is_exp'] = '0'
            if xdatetime.string2datetime(data['exp_data']) < datetime.datetime.now():
                data['is_exp'] = '1'
            if not os.path.exists(os.path.join(xdata.WIN_PE_NEW_PATH, str(request.user.id), data['filename'])):
                data['is_exp'] = '1'
            data['url'] = os.path.join('/static/download/newwinpe/', str(request.user.id), data['filename'])
            data['r'] = '0'
            data['e'] = '成功'
            data['exp_data'] = data['exp_data'].split('.')[0]
        resps = json.dumps(data, ensure_ascii=False)
        return HttpResponse(resps)
    return HttpResponse('{"r": "1","e": "NoData"}')


def _check_ip_set(data):
    is_valid = dict(status=1, msg='正在生成中')
    if data['bStatic'] == '0':
        return is_valid
    else:
        if not all(data.values()):
            is_valid['status'] = 0
            is_valid['msg'] = '选项不能为空'
            return is_valid
        state1 = _check_ip_set_is_valid(data['mask'])
        state2 = _check_ip_set_is_valid(data['gateway_ip'])
        state3 = _check_ip_set_is_valid(data['dns1'])
        state4 = _check_ip_set_is_valid(data['serv_ip'])
        if not all([state1, state2, state3, state4]):
            is_valid['status'] = 0
            is_valid['msg'] = '不是合法的IP地址'
            return is_valid
        state5 = _check_ip_set_is_valid(data['start_ip'], data['end_ip'])
        if not state5:
            is_valid['status'] = 0
            is_valid['msg'] = '开始IP和结束IP，前三段必须相同'
            return is_valid
        return is_valid


def _check_need_create(data):
    user_profile = UserProfile.objects.filter(user_id=data['user_id'])[0]
    winpeset = json.loads(user_profile.winpeset)
    for i in winpeset:
        if i == 'exp_data':
            continue
        if i == 'filename':
            continue
        if winpeset[i] != data[i]:
            return True
    return False


def _check_has_exists(filename):
    if not os.path.exists(os.path.dirname(filename)):
        os.makedirs(os.path.dirname(filename))
    return os.path.exists(filename)


def _check_dir_has_exists(dirname):
    if not os.path.exists(dirname):
        os.makedirs(dirname)
    return os.path.exists(dirname)


def _change_file_name(old_name, new_name):
    if _check_has_exists(old_name):
        os.rename(old_name, new_name)
        return True
    return False


def get_user_profile(data):
    user_id = data['user_id']
    user_profile = UserProfile.objects.get_or_create(user_id=user_id)[0]
    user_profile.winpeset = json.dumps(data)
    user_profile.save(update_fields=['winpeset'])
    return user_profile


def _check_ip_set_is_valid(start_ip, end_ip=None):
    pattern = re.compile(r'\d+')
    res_start = pattern.findall(start_ip)
    for i in res_start:
        if int(i) < 0 or int(i) > 255:
            return False
    if len(res_start) != 4:
        return False
    if end_ip:
        res_end = pattern.findall(end_ip)
        if len(res_end) != 4:
            return False
        for i in res_start:
            if int(i) < 0 or int(i) > 255:
                return False
        if res_start[0] != res_end[0]:
            return False
        if res_start[1] != res_end[1]:
            return False
        if res_start[2] != res_end[2]:
            return False
    return True


class DoCreateWinpeWork(object):
    def __init__(self, user_profile):
        self._data = json.loads(user_profile.winpeset)
        self._user_id = user_profile.user_id
        self._link_dir = os.path.join(xdata.WIN_PE_NEW_PATH, str(self._user_id))
        self._link_path = os.path.join(self._link_dir, self._data['filename'])
        self._mather_file_name = os.path.join(xdata.WIN_PE_MATHER_PATH, 'winpe.iso')
        self._new_file_name = ''
        self._file_dir = ''
        oem = get_oem_info()
        self._title = oem['title']
        self._company = oem['company']

    def work(self):
        try:
            self._check_args_valid()
            self._truncate()
            self._mk_dirs()
            self._copyfile()
            self._createfile()
            self._mk_link()
        except Exception as e:
            self._truncate()
            raise e

    def _mk_link(self):
        cmd = 'ln -s "{}" "{}"'.format(self._new_file_name, self._link_path)
        returned_code, lines = boxService.box_service.runCmd(cmd, True)
        if returned_code != 0:
            xlogging.raise_and_logging_error(r'创建链接失败',
                                             'create link fail : {} {}'.format(returned_code, lines))

    def _check_args_valid(self):
        if not os.path.exists(self._mather_file_name):
            xlogging.raise_and_logging_error('未发现ISO文件', 'not find ISO, user id:{}'.format(self._user_id))

        user_quotas_set = UserQuota.objects.filter(user_id=self._user_id, deleted=False)
        if not user_quotas_set.exists():
            xlogging.raise_and_logging_error('用户不存在配额', 'not find user_quota, user id:{}'.format(self._user_id))

        node_path = ''
        for user_quota in user_quotas_set:
            storage_node = user_quota.storage_node
            if os.path.exists(storage_node.path) and storage_node.available:
                node_path = storage_node.path
                break
        else:
            xlogging.raise_and_logging_error('没有可用的存储节点', 'storage_node_path not exists')

        self._file_dir = os.path.join(node_path, 'iso', str(self._user_id))

    @xlogging.convert_exception_to_value(None)
    def _truncate(self):
        shutil.rmtree(self._file_dir)
        shutil.rmtree(self._link_dir)

    def _mk_dirs(self):
        os.makedirs(self._file_dir, exist_ok=True)
        os.makedirs(self._link_dir, exist_ok=True)

    def _copyfile(self):
        self._new_file_name = os.path.join(self._file_dir, self._data['filename'])
        shutil.copyfile(self._mather_file_name, self._new_file_name)

    def _createfile(self):
        with open(self._new_file_name, 'rb') as f:
            startsize = 0
            mypos = 0
            while True:
                content = f.read(READ_SIZE)
                if not content:
                    raise Exception('ISO文件损坏')
                pos = content.find('{0B5678AD-C2B3-400f-AFE8-598A2C45F138}'.encode('gbk'))
                if pos != -1:
                    mypos = startsize + pos
                    break
                startsize += READ_SIZE

        with open(self._new_file_name, 'rb+') as f1:
            f1.seek(mypos)
            content = '{0B5678AD-C2B3-400f-AFE8-598A2C45F138}\n'.encode('gbk')
            content += ('bstatic = ' + self._data['bStatic'] + '\n').encode('gbk')
            content += ('port = ' + self._data['port'] + '\n').encode('gbk')
            content += ('start_ip= ' + self._data['start_ip'] + '\n').encode('gbk')
            content += ('end_ip= ' + self._data['end_ip'] + '\n').encode('gbk')
            content += ('mask = ' + self._data['mask'] + '\n').encode('gbk')
            content += ('gateway_ip = ' + self._data['gateway_ip'] + '\n').encode('gbk')
            content += ('serv_ip = ' + self._data['serv_ip'] + '\n').encode('gbk')
            content += ('dns = ' + self._data['dns1'] + '\n').encode('gbk')
            if self._data['mark_user'] == 'yes':
                content += ('user_fingerprint=' + self._data['user_fingerprint'] + '\n').encode('gbk')
            content += ('CO = ' + self._company + '\n').encode('gbk')
            content += ('product = ' + self._title + '\n').encode('gbk')
            cyctimes = 498 - len(content)
            for i in range(cyctimes):
                content += '\n'.encode('gbk')
            f1.write(content)


def datatime_to_timestamp(datetime_obj):
    time_tuple = datetime_obj.timetuple()
    return int(time.mktime(time_tuple))


def getcurrenttime(request):
    currenttime = timezone.now().strftime('%Y-%m-%d %H:%M:%S')
    return HttpResponse(json.dumps({"e": 0, "r": 0, "time": currenttime}))


def setservertime(request):
    currenttimes = request.GET.get('currenttime', '')
    if not currenttimes:
        currenttimes = timezone.now().strftime('%Y-%m-%d %H:%M:%S')
    p = subprocess.Popen("date -s " + '"' + currenttimes + '"', shell=True)
    p.wait()
    mylog = {'设置服务器时间': currenttimes}
    SaveOperationLog(
        request.user, OperationLog.TYPE_SYSTEM_SET, json.dumps(mylog, ensure_ascii=False), get_operator(request))
    if p.returncode != 0:
        return HttpResponse(json.dumps({"e": "设置失败", "r": 1}))
    return HttpResponse(json.dumps({"e": "设置成功", "r": 0}))


class RollBackRoute(threading.Thread):
    def __init__(self):
        super(RollBackRoute, self).__init__()
        self.wait_time = 10 * 60
        self.times = 0
        self.is_restarting_net = False
        self.is_stop_restart = False

    def run(self):
        while (self.times < self.wait_time) and (not self.is_stop_restart):
            self.times += 1
            time.sleep(1)
        if not self.is_stop_restart:
            self.is_restarting_net = True
            returncode, result_str = _excute_cmd_and_return_code('systemctl restart network')
            if returncode != 0:
                if result_str:
                    _logger.error('重启网络失败,错误消息:{}'.format(result_str))


class RollBackIpSet(threading.Thread):
    def __init__(self, netinfo):
        super(RollBackIpSet, self).__init__()
        self.wait_time = 10 * 60
        self.times = 0
        self.is_restarting_net = False
        self.is_stop_restart = False
        self.net_info = netinfo

    def run(self):
        while (self.times < self.wait_time) and (not self.is_stop_restart):
            self.times += 1
            time.sleep(1)
        if not self.is_stop_restart:
            self.is_restarting_net = True
            try:
                box_service.setNetwork(json.dumps(self.net_info, ensure_ascii=False))
            except xlogging.BoxDashboardException as e:
                _logger.error('roll back net fail ,net info{},errormsg:{}'.format(json.dumps(self.net_info), e.msg))


class DumpTcpFile(threading.Thread):
    def __init__(self, cmd, taskname):
        super(DumpTcpFile, self).__init__()
        self.cmd = cmd
        self.error = ''
        self.sttime = datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        self.edtime = ''
        self.taskname = taskname
        self.url = ''

    def run(self):
        try:
            self.exe_cmd()
        except Exception as e:
            _logger.debug('DumpTcpFile error msg {}'.format(e))

    def exe_cmd(self):
        global _g_listen_to_tcpdump_handle
        with subprocess.Popen(self.cmd, shell=True, stderr=subprocess.PIPE,
                              universal_newlines=True) as p:
            time.sleep(1)
            if p.poll() is None:
                _logger.debug("start ListenToTcpDump threading")
                _g_listen_to_tcpdump_handle = ListenToTcpDump(p.pid)
                _g_listen_to_tcpdump_handle.setDaemon(True)
                _g_listen_to_tcpdump_handle.start()
                p.wait()
                self.edtime = datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
            else:
                stdout, stderr = p.communicate()
                if stderr:
                    self.error = stderr
                    _logger.error("DumpTcpFile stderr error {}".format(stderr))


class ListenToTcpDump(threading.Thread):
    def __init__(self, pid):
        super(ListenToTcpDump, self).__init__()
        self.pid = pid
        self.is_stop = False

    def run(self):
        try:
            _logger.debug('ListenToTcpDump start listen pid {}'.format(self.pid))
            while True:
                time.sleep(1)
                if self.subprocess_is_terminate():
                    _logger.debug('ListenToTcpDump subprocess_is_terminate break')
                    break
                if self.file_size_is_enough():
                    os.kill(self.pid, 9)
                    _create_zip_file()
                    _logger.debug('ListenToTcpDump file_size_is_enough kill it,pid {}'.format(self.pid))
                    break
                if self.is_stop:
                    os.kill(self.pid, 9)
                    _logger.debug('ListenToTcpDump user kill it'.format(self.pid))
                    break
            _logger.debug('ListenToTcpDump end listen pid {}'.format(self.pid))
        except Exception as e:
            _logger.error('ListenToTcpDump ListenToTcpDump error {}'.format(e))
            os.kill(self.pid, 9)

    @staticmethod
    def file_size_is_enough():
        global tcpdump_file_path
        filepath = os.path.join(tcpdump_file_path, 'tcpdump.pcap')
        if os.path.exists(filepath):
            return os.path.getsize(filepath) > 100 * 1024 * 1024
        return False

    def subprocess_is_terminate(self):
        cmd = "ps " + str(self.pid)
        code, content = _excute_cmd_and_return_code(cmd)
        if "tcpdump" not in content:
            return True
        return False


def getroutes(request):
    page = 1
    rows = 30
    if 'page' in request.GET:
        page = int(request.GET['page'])
    if 'rows' in request.GET:
        rows = int(request.GET['rows'])
    rowList = list()
    get_routes_cmd(rowList)

    paginator = Paginator(rowList, rows)
    totalPlan = paginator.count
    totalPage = paginator.num_pages

    page = totalPage if page > totalPage else page
    currentObjs = paginator.page(page).object_list
    retInfo = {'r': 0, 'a': 'list', 'page': str(page), 'total': totalPage,
               'records': totalPlan, 'rows': currentObjs}
    jsonStr = json.dumps(retInfo, ensure_ascii=False)

    return HttpResponse(jsonStr)


def setroutes(request):
    global _g_route_rollback_handle
    parmsstr = request.GET.get('info')
    parms = json.loads(parmsstr)
    current_routes = list()
    get_routes_cmd(current_routes)
    for c_route in current_routes:
        if c_route[0] == '0.0.0.0(default)':
            continue
        if c_route[2] != '0.0.0.0':
            cmd_line = 'ip route del {}/{}'.format(c_route[0], c_route[1])
            returncode, result_str = _excute_cmd_and_return_code(cmd_line)
            if returncode != 0:
                if result_str:
                    _logger.error('删除路由失败，cmd{},错误消息:{}'.format(cmd_line, result_str))
    for add_route in parms:
        add_fail_routes = list()
        if add_route['network'] == '0.0.0.0(default)':
            continue
        if add_route['gateway'] != '0.0.0.0':
            cmd_line = 'ip route add {}/{} via {}'.format(add_route['network'], add_route['mask'],
                                                          add_route['gateway'])
            returncode, result_str = _excute_cmd_and_return_code(cmd_line)
            if returncode != 0:
                if result_str:
                    _logger.error('添加路由失败，cmd{},错误消息:{}'.format(cmd_line, result_str))
            add_fail_routes.append(add_route)

    if (_g_route_rollback_handle is None) or (not _g_route_rollback_handle.is_alive()):
        _g_route_rollback_handle = RollBackRoute()
        _g_route_rollback_handle.setDaemon(True)
        _g_route_rollback_handle.start()
    else:
        _g_route_rollback_handle.times = 0
    jsonstr = {'r': 0, 'e': '操作成功'}
    return HttpResponse(json.dumps(jsonstr, ensure_ascii=False))


def write_routes():
    global _g_route_rollback_handle
    current_routes = list()
    get_routes_cmd(current_routes)
    if (_g_route_rollback_handle is not None) and (_g_route_rollback_handle.is_alive()):
        if _g_route_rollback_handle.is_restarting_net:
            return HttpResponse(json.dumps({'r': 1, 'e': '生成永久路由失败，请在规定时间内确认'}, ensure_ascii=False))
        _g_route_rollback_handle.is_stop_restart = True
        try:
            _write_route_to_file(current_routes)
            os.system('systemctl restart network')
            return HttpResponse(json.dumps({'r': 0, 'e': '生成成功'}, ensure_ascii=False))
        except Exception as e:
            _logger.error('write route in file fail ,{}'.format(e))
    else:
        return HttpResponse(json.dumps({'r': 1, 'e': '生成永久路由失败，请先点击应用，确认网络状况后，再次点击确认'}, ensure_ascii=False))


def confirm_ipset():
    global _g_ipset_rollback_handle
    if (_g_ipset_rollback_handle is not None) and (_g_ipset_rollback_handle.is_alive()):
        if _g_ipset_rollback_handle.is_restarting_net:
            return HttpResponse(json.dumps({'r': 1, 'e': '正在重启网络到应用之前的状态，请在规定时间内确认'}, ensure_ascii=False))
        _g_ipset_rollback_handle.is_stop_restart = True
        return HttpResponse(json.dumps({'r': 0, 'e': '操作成功'}, ensure_ascii=False))
    else:
        return HttpResponse(json.dumps({'r': 1, 'e': '请先点击应用，确认网络状况后，再次点击确认'}, ensure_ascii=False))


def _write_route_to_file(current_routes):
    truncate_list = list()
    _remove_route_file()
    for route in current_routes:
        _logger.debug(route)
        # 遇到 默认网关
        if route[0] == '0.0.0.0(default)':
            continue
        # 遇到 非静态路由 PASS
        if route[2] == '0.0.0.0':
            continue
        if route[3] not in truncate_list:
            truncate_list.append(route[3])
            cmd_line = _get_cmd_line(route, '>')

            returncode, result_str = _excute_cmd_and_return_code(cmd_line)
            if returncode != 0:
                if result_str:
                    _logger.error('添加路由失败，cmd{},错误消息:{}'.format(cmd_line, result_str))
        else:
            cmd_line = _get_cmd_line(route, '>>')
            returncode, result_str = _excute_cmd_and_return_code(cmd_line)
            if returncode != 0:
                if result_str:
                    _logger.error('添加路由失败，cmd{},错误消息:{}'.format(cmd_line, result_str))


def _get_cmd_line(route, smoble):
    cmd_line = "echo '{}/{} via {}' {} /etc/sysconfig/network-scripts/" \
               "route-{}".format(route[0], route[1],
                                 route[2], smoble, route[3])
    return cmd_line


def _remove_route_file():
    cmd = "rm -f /etc/sysconfig/network-scripts/route-*"
    os.system(cmd)


def get_routes_cmd(result_list):
    cmd_line = "ip route show | awk {'print $1,$2,$3,$4,$5'}"
    returncode, result_str = _excute_cmd_and_return_code(cmd_line)
    if returncode != 0:
        if result_str:
            _logger.error('获取路由表失败，错误消息:{}'.format(result_str))
            return None
    else:
        routes = result_str.strip('\n').split('\n') if result_str else []
        for route in routes:
            route_items = route.split()
            if route_items[0] == 'default' or route_items[0] == '0.0.0.0':
                result_list.append(['0.0.0.0(default)', '0.0.0.0', route_items[2], route_items[4]])
            elif 'via' in route_items:
                network_and_mask = route_items[0].split('/')
                result_list.append([network_and_mask[0],
                                    num_to_mask('255.255.0.0' if len(network_and_mask) < 2 else network_and_mask[1]),
                                    route_items[2], route_items[4]])
            else:
                network_and_mask = route_items[0].split('/')
                result_list.append([network_and_mask[0], num_to_mask(network_and_mask[1]),
                                    '0.0.0.0', route_items[2]])


# num mask num 24
# return '255.255.255.0'
def num_to_mask(num):
    num_map = {1: '128', 2: '192', 3: '224', 4: '240', 5: '248', 6: '252', 7: '254'}
    if '.' in num:
        return num
    msk = list()
    num = int(num)
    last_num = num % 8
    for i in range(num // 8):
        msk.append('255')
    if last_num:
        msk.append(num_map[last_num])
    for j in range(4 - len(msk)):
        msk.append('0')

    return '.'.join(msk)


def get_current_route_ip_threading(request):
    global _g_ipset_rollback_handle, _g_route_rollback_handle
    th_type = request.GET.get('type')
    is_alive = 0
    time = 0
    if th_type == 'ip':
        if (_g_ipset_rollback_handle is not None) and (_g_ipset_rollback_handle.is_alive()):
            is_alive = 1
            time = _g_ipset_rollback_handle.times
    if th_type == 'route':
        if (_g_route_rollback_handle is not None) and (_g_route_rollback_handle.is_alive()):
            is_alive = 1
            time = _g_route_rollback_handle.times
    jsonstr = {'is_alive': is_alive, 'time': time}
    return HttpResponse(json.dumps(jsonstr))


def check_cmd(cmd):
    not_allow_char = ['?', ';', '|', '<<', '>>', '<', '>', '$', '*', '=', '&', '!', '(', ')', '{', '}']
    for char in cmd:
        if char in not_allow_char:
            return False, '非法字符:{}'.format(char)
    return True, ''


def ping_cmd(request):
    cmd = request.GET.get('cmd')
    _logger.debug('ping_cmd: {}'.format(cmd))
    path = os.path.join(xdata.BASE_STATIC_PATH, 'tping.txt')
    _clear_file(path)
    code, msg = check_cmd(cmd)
    jsonstr = {"r": '', "e": ''}
    if not code:
        re_jstr = jsonstr.copy()
        re_jstr['r'] = 1
        re_jstr['e'] = msg
        return HttpResponse(json.dumps(re_jstr))
    if not cmd.startswith("ping"):
        re_jstr = jsonstr.copy()
        re_jstr['r'] = 1
        re_jstr['e'] = '非法命令'
        return HttpResponse(json.dumps(re_jstr))
    if '-c' in cmd:
        pattern = '(?<=-c)\s*\d+'
        match = re.search(pattern, cmd)
        if match:
            count = match.group()
            if int(count) > 10:
                cmd = re.sub('-c\s*\d+', '-c 10', cmd)
    else:
        cmd = cmd + ' -c 5'

    c_cmd = 'echo endflag >> {}'.format(path)
    cmd = cmd + ' > {} 2>&1 ; {}'.format(path, c_cmd)
    _logger.debug(cmd)
    with subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True) as p:
        try:
            outs, errs = p.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            p.kill()
            outs, errs = p.communicate()
    re_jstr = jsonstr.copy()
    re_jstr['r'] = 0
    re_jstr['e'] = '操作成功'
    return HttpResponse(json.dumps(re_jstr))


def _clear_file(path):
    try:
        os.remove(path)
    except:
        pass
    return None


def get_ping_content():
    path = os.path.join(xdata.BASE_STATIC_PATH, 'tping.txt')
    cmd = 'cat {} 2> /dev/null'.format(path)
    info = _excute_cmd_and_return_code(cmd)
    res_list = info[1].splitlines()
    jsonstr = {"r": '', "e": '', 'lists': res_list}
    return HttpResponse(json.dumps(jsonstr))


def _format_tcpdump_cmd(cmd):
    global tcpdump_file_path
    exp = ''
    cmd = re.sub('-w\s*\S+', '', cmd)
    cmd = re.sub('-c\s*\S+', '', cmd)
    cmd = re.sub('-C\s*\S+', '', cmd)
    cmd = re.sub('-W\s*\S+', '', cmd)
    cmd = re.sub('-r\s*\S+', '', cmd)
    cmd = re.sub('-Z\s*\S+', '', cmd)
    cmd = re.sub('-P\s*\S+', '', cmd)
    cmd = re.sub('-B\s*\S+', '', cmd)
    expression_match = re.search("'.+'", cmd)
    if expression_match:
        cmd = cmd[:expression_match.start()] + cmd[expression_match.end():]
        exp = ' ' + expression_match.group()
    cmd += ' -C 111'
    cmd += ' -W 1'
    cmd += ' -U'
    cmd += ' -Z root'
    os.path.exists(tcpdump_file_path) or os.mkdir(tcpdump_file_path)
    # tmp_filename = datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%ST%f') + '.pcap'
    tmp_filename = 'tcpdump.pcap'
    tmp_fil_path = os.path.join(tcpdump_file_path, tmp_filename)
    cmd += ' -w ' + tmp_fil_path
    cmd += exp
    return cmd


def tcpdump(request):
    global _g_tcpdump_handle
    cmd = request.GET.get('cmd')
    taskname = cmd
    _logger.debug('tcpdump: {}'.format(cmd))
    expression_match = re.search("'.+'", cmd)
    if expression_match:
        c_cmd = cmd[:expression_match.start()] + ' ' + cmd[expression_match.end():]
        code, msg = check_cmd(c_cmd)
    else:
        code, msg = check_cmd(cmd)
    jsonstr = {"r": '', "e": ''}
    if not code:
        jsonstr['r'] = 1
        jsonstr['e'] = msg
        return HttpResponse(json.dumps(jsonstr))
    if not cmd.startswith("tcpdump"):
        jsonstr['r'] = 1
        jsonstr['e'] = '非法命令'
        return HttpResponse(json.dumps(jsonstr))

    cmd = _format_tcpdump_cmd(cmd)

    if _g_tcpdump_handle is not None and _g_tcpdump_handle.is_alive():
        jsonstr['r'] = 1
        jsonstr['e'] = '正在执行tcpdump任务，如果想再次执行，请先停止当前任务。'
        return HttpResponse(json.dumps(jsonstr))
    else:
        _g_tcpdump_handle = DumpTcpFile(cmd, taskname)
        _g_tcpdump_handle.setDaemon(True)
        _g_tcpdump_handle.start()
        time.sleep(2)
        if _g_tcpdump_handle.error:
            jsonstr['r'] = 1
            jsonstr['e'] = _g_tcpdump_handle.error
            return HttpResponse(json.dumps(jsonstr))
        jsonstr['r'] = 0
        jsonstr['sttime'] = _g_tcpdump_handle.sttime
        jsonstr['taskname'] = _g_tcpdump_handle.taskname + '正在执行中'
        jsonstr['edtime'] = '无'
        jsonstr['url'] = '无'
        return HttpResponse(json.dumps(jsonstr))


def get_tcpdump_status():
    global _g_tcpdump_handle
    jsonstr = {"r": '', "e": ''}
    if _g_tcpdump_handle is None:
        jsonstr['taskname'] = '无'
        jsonstr['sttime'] = '无'
        jsonstr['edtime'] = '无'
        jsonstr['url'] = '无'
        jsonstr['r'] = 0
        return HttpResponse(json.dumps(jsonstr))
    elif _g_tcpdump_handle.is_alive():
        jsonstr['taskname'] = _g_tcpdump_handle.taskname + '正在执行中'
        jsonstr['sttime'] = _g_tcpdump_handle.sttime
        jsonstr['edtime'] = '无'
        jsonstr['url'] = '无'
        jsonstr['r'] = 0
        return HttpResponse(json.dumps(jsonstr))
    else:
        jsonstr['taskname'] = _g_tcpdump_handle.taskname + '任务结束'
        jsonstr['sttime'] = _g_tcpdump_handle.sttime
        jsonstr['edtime'] = _g_tcpdump_handle.edtime
        jsonstr['url'] = _g_tcpdump_handle.url
        jsonstr['r'] = 0
        return HttpResponse(json.dumps(jsonstr))


def tcpdump_stop():
    global _g_tcpdump_handle, _g_listen_to_tcpdump_handle
    jsonstr = {"r": '', "e": ''}
    if _g_listen_to_tcpdump_handle is not None and _g_listen_to_tcpdump_handle.is_alive():
        _g_listen_to_tcpdump_handle.is_stop = True
        time.sleep(5)
        _create_zip_file()
        jsonstr['taskname'] = _g_tcpdump_handle.taskname
        jsonstr['sttime'] = _g_tcpdump_handle.sttime
        jsonstr['edtime'] = _g_tcpdump_handle.edtime
        jsonstr['url'] = _g_tcpdump_handle.url
        jsonstr['r'] = 0
        return HttpResponse(json.dumps(jsonstr))
    else:
        jsonstr['r'] = 1
        jsonstr['e'] = '无tcpdump任务,请先执行任务，再停止'
        return HttpResponse(json.dumps(jsonstr))


def _truncate_File():
    global tcpdump_file_path
    if os.path.exists(tcpdump_file_path):
        os.system('rm -f ' + tcpdump_file_path + '*')


def _create_zip_file():
    global tcpdump_file_path, _g_tcpdump_handle
    tcpfilepath = os.path.join(tcpdump_file_path, 'tcpdump.pcap')
    zipfilename = xdata.PREFIX_TCP_DUMP_FILE + datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%ST%f') + '.zip'
    zipfilepath = os.path.join(tcpdump_file_path, zipfilename)
    filenames = os.listdir(tcpdump_file_path)
    for filename in filenames:
        if filename.endswith('zip'):
            os.remove(os.path.join(tcpdump_file_path, filename))
    if os.path.exists(tcpfilepath):
        with zipfile.ZipFile(zipfilepath, 'w') as myzip:
            myzip.write(tcpfilepath, 'tcpdump.pcap')
        _g_tcpdump_handle.url = '/static/download/tcpdump/' + zipfilename
    return zipfilename


def set_ssh_kvm(request):
    jsonstr = {"r": 0, "e": "操作成功"}
    enablekvm = int(request.POST.get('enablekvm'))
    if enablekvm == 1:
        enablekvm = True
    else:
        enablekvm = False
    ssh_ip = str(request.POST.get('ssh_ip'))
    ssh_port = int(request.POST.get('ssh_port'))
    ssh_key = str(request.POST.get('ssh_key'))
    ssh_path = str(request.POST.get('ssh_path'))
    aio_ip = str(request.POST.get('aio_ip'))
    ssh_os_type = str(request.POST.get('ssh_os_type'))

    try:
        RSA.importKey(ssh_key)
    except Exception as e:
        jsonstr['r'] = 1
        jsonstr['e'] = '私钥不正确，{}'.format(e)
        return HttpResponse(json.dumps(jsonstr, ensure_ascii=False))

    if sshkvm.objects.all().count() > 0:
        sshkvm.objects.all().update(enablekvm=enablekvm, ssh_ip=ssh_ip, ssh_port=ssh_port, ssh_key=ssh_key,
                                    ssh_path=ssh_path, aio_ip=aio_ip, ssh_os_type=ssh_os_type)
    else:
        sshkvm.objects.create(enablekvm=enablekvm, ssh_ip=ssh_ip, ssh_port=ssh_port, ssh_key=ssh_key,
                              ssh_path=ssh_path, aio_ip=aio_ip, ssh_os_type=ssh_os_type)

    return HttpResponse(json.dumps(jsonstr))


def _get_ssh_os_type():
    os_type_list = list()
    rootdir = r'/sbin/aio/remote_host'
    folders = os.listdir(rootdir)
    for name in folders:
        curname = os.path.join(rootdir, name)
        if os.path.isfile(curname):
            ext = os.path.splitext(curname)[1]
            if ext == '.json':
                try:
                    with open(curname, 'r') as fout:
                        try:
                            info = json.loads(fout.read())
                            os_type_list.append(info)
                        except Exception as e:
                            _logger.info('_ssh_os_type read Failed.e={}'.format(e))
                        finally:
                            fout.close()
                except Exception as e:
                    pass
    return os_type_list


def get_ssh_kvm(request):
    jsonstr = {"r": 0, "e": "操作成功"}

    ssh_os_type = _get_ssh_os_type()

    if len(ssh_os_type):
        jsonstr['remotekvm'] = '1'
    else:
        jsonstr['remotekvm'] = '0'

    jsonstr['all_os_type'] = ssh_os_type
    if len(ssh_os_type) and sshkvm.objects.all().count() > 0:
        objs = sshkvm.objects.all()
        for obj in objs:
            if obj.enablekvm:
                jsonstr['enablekvm'] = '1'
            else:
                jsonstr['enablekvm'] = '0'
            jsonstr['ssh_port'] = obj.ssh_port
            jsonstr['ssh_key'] = obj.ssh_key
            jsonstr['aio_ip'] = obj.aio_ip
            jsonstr['ssh_os_type'] = obj.ssh_os_type
            jsonstr['ssh_path'] = obj.ssh_path
            jsonstr['ssh_ip'] = obj.ssh_ip
    else:
        jsonstr['enablekvm'] = '0'
        jsonstr['ssh_ip'] = ''
        jsonstr['ssh_port'] = '22'
        jsonstr['ssh_key'] = ''
        jsonstr['aio_ip'] = ''
        jsonstr['ssh_os_type'] = ''
        jsonstr['ssh_path'] = ''
        jsonstr['ssh_ip'] = ''

    return HttpResponse(json.dumps(jsonstr))


def _is_adpter_name_exist(name):
    info = psutil.net_if_addrs()
    for k, v in info.items():
        if k == name:
            return True
    return False


def _net_restart_service():
    try:
        box_service.refreshNetwork()
    except Exception as e:
        _logger.error("_net_restart_service refreshNetwork failed {}".format(e), exc_info=True)

    try:
        box_service.refreshNetwork()
    except Exception as e:
        _logger.error("_net_restart_service refreshNetwork failed {}".format(e), exc_info=True)

    return 0


def set_takeover_segment(request):
    segment = request.GET.get('segment', '172.29')
    SaveDictionary(DataDictionary.DICT_TYPE_TAKEOVER_SEGMENT, 'SEGMENT', segment)
    jsonstr = {"r": 0, "e": "操作成功",
               "segment": segment
               }

    if _is_adpter_name_exist('aiotap0'):
        cmd = r'ip li delete aiotap0'
        _excute_cmd_and_return_code(cmd)
    if _is_adpter_name_exist('takeoverbr0'):
        cmd = r'ip li delete takeoverbr0'
        _excute_cmd_and_return_code(cmd)
    _net_restart_service()
    return HttpResponse(json.dumps(jsonstr, ensure_ascii=False))


def get_takeover_segment(request):
    segment = GetDictionary(DataDictionary.DICT_TYPE_TAKEOVER_SEGMENT, 'SEGMENT', '172.29')
    jsonstr = {"r": 0, "e": "操作成功",
               "segment": segment
               }
    return HttpResponse(json.dumps(jsonstr, ensure_ascii=False))


def is_aio_sys_vt_valid():
    return takeover_kvm_wrapper.is_aio_sys_vt_valid()


def is_aio_sys_has_auxiliary_sys():
    return len(_get_ssh_os_type()) > 0


def get_partial_disk_snapshot_exp_days(request):
    exp = GetDictionary(DataDictionary.DICT_TYPE_PARTIAL_DISK_SNAPSHOT_EXP, 'exp_days', '7')
    if get_separation_of_the_three_members().is_database_use_policy():
        database_used_bytes = get_database_size()
        database_can_use_size = GetDictionary(DataDictionary.DICT_TYPE_DATABASE_CAN_USE_SIZE, 'dbmaxsize',
                                              str(1024))
        database_can_use_size = int(database_can_use_size)
    else:
        database_used_bytes = 0
        database_can_use_size = 0
    return HttpResponse(
        json.dumps({'r': 0, 'e': '', 'exp': exp, 'database_used_MB': '{0:.2f}'.format(database_used_bytes / 1024 ** 2),
                    'database_can_use_size': database_can_use_size}))


def set_partial_disk_snapshot_exp_days(request):
    exp_days = request.GET.get('exp_days', '7')
    database_can_use_size = request.GET.get('database_can_use_size', '0')
    SaveDictionary(DataDictionary.DICT_TYPE_PARTIAL_DISK_SNAPSHOT_EXP, 'exp_days', exp_days)

    mylog = {'操作': '更新空间策略过期天数'}
    if get_separation_of_the_three_members().is_database_use_policy():
        SaveDictionary(DataDictionary.DICT_TYPE_DATABASE_CAN_USE_SIZE, 'dbmaxsize', database_can_use_size)
        mylog['操作'] = '更新空间策略过期天数和日志数据存储空间'
        database_can_use_size = int(database_can_use_size)
        mylog['日志数据存储空间'] = '{}MB'.format(database_can_use_size)

    mylog['过期天数'] = exp_days
    mylog['结果'] = '操作成功'
    SaveOperationLog(
        request.user, OperationLog.TYPE_SYSTEM_SET, json.dumps(mylog, ensure_ascii=False), get_operator(request))
    return HttpResponse(json.dumps({'r': 0, 'e': ''}))


def set_task_queue_num(request):
    task_queue_num = str(request.GET.get('task_queue_num', xdata.DICT_TYPE_TASK_QUEUE_NUM_DEFAULT))
    SaveDictionary(DataDictionary.DICT_TYPE_TASK_QUEUE_NUM, 'queue_num', task_queue_num)
    mylog = {'操作': '更新任务队列数量'}
    mylog['任务队列数量'] = task_queue_num
    mylog['结果'] = '操作成功'
    SaveOperationLog(
        request.user, OperationLog.TYPE_SYSTEM_SET, json.dumps(mylog, ensure_ascii=False), get_operator(request))
    return HttpResponse(json.dumps({'r': 0, 'e': ''}))


def get_task_queue_num(request):
    return HttpResponse(json.dumps(
        {'r': 0, 'e': '', 'task_queue_num': GetDictionary(DataDictionary.DICT_TYPE_TASK_QUEUE_NUM, 'queue_num',
                                                          xdata.DICT_TYPE_TASK_QUEUE_NUM_DEFAULT)}))


def disable_vt(request):
    bDisableVT = int(request.GET.get('disable_vt', '1'))
    flag_file = r'/var/db/disable_vt'
    if bDisableVT == 1:
        # 使用VT
        if os.path.isfile(flag_file):
            os.remove(flag_file)
    elif bDisableVT == 2:
        # 不使用VT
        if not os.path.isfile(flag_file):
            with open(flag_file, 'w') as fout:
                pass

    return HttpResponse(json.dumps({'r': 0, 'e': '操作成功'}))


def get_vt_status(request):
    flag_file = r'/var/db/disable_vt'
    if os.path.isfile(flag_file):
        return HttpResponse(json.dumps({'r': 0, 'enable_vt': False, 'e': '操作成功'}))
    return HttpResponse(json.dumps({'r': 0, 'enable_vt': True, 'e': '操作成功'}))


def set_db_bak_device_path(save_path):
    """
    下面是将用户选择的存储写入到archive.sh中,并且将save_path_创建且赋予权限
    :param save_path: 用户选择的存储位置
    :return:
    """
    config = configparser.ConfigParser()
    config.read(backup_ini)
    save_bak_path = config.get("bak_db_path", "save_bak_path")
    PGDATA = config.get("pg_data", "pg_data")
    save_path_ = save_path + save_bak_path
    if not os.path.exists(save_path_):
        os.makedirs(save_path_)
    subprocess.call('chmod 700 ' + save_path_, shell=True)
    subprocess.call('chmod 777 ' + PGDATA + 'archive.sh', shell=True)
    subprocess.call('chown postgres:postgres ' + save_path_, shell=True)
    result1 = open(PGDATA + 'archive.sh', 'r+')
    result_all_lines1 = result1.readlines()
    result1.seek(0)
    result1.truncate()
    for line in result_all_lines1:
        try:
            if 'BACK_PATH=' in line:
                line = line.replace(line, 'BACK_PATH=' + save_path_ + '\n')
                result1.write(line)
            else:
                result1.write(line)
        except:
            print("shell mistake")
    result1.close()


def save_database_backup_info(request):
    # 启用数据库备份
    enable_database_backup = request.POST.get('enable_database_backup', 0)
    # 备份文件存放位置
    database_backup_storagedevice = request.POST.get('database_backup_storagedevice', -1)
    # 多少小时进行一次增量备份
    increment_database_backup_interval_hour = request.POST.get('increment_database_backup_interval_hour', 0)
    # 多少天进行一次全量备份
    full_database_backup_interval_day = request.POST.get('full_database_backup_interval_day', 0)
    # 数据保留天数
    database_retention_day = request.POST.get('database_retention_day', 0)

    run_immediately = int(request.POST.get('run_immediately', 0))
    if int(enable_database_backup) == 1:
        SaveDictionary(DataDictionary.DICT_TYPE_BACKUP_AIO_DATABASE, 'ebe_db_bak', str(enable_database_backup))
        SaveDictionary(DataDictionary.DICT_TYPE_BACKUP_AIO_DATABASE, 'db_bak_dev',
                       str(database_backup_storagedevice))
        SaveDictionary(DataDictionary.DICT_TYPE_BACKUP_AIO_DATABASE, 'bak_hour',
                       str(increment_database_backup_interval_hour))
        SaveDictionary(DataDictionary.DICT_TYPE_BACKUP_AIO_DATABASE, 'bak_day',
                       str(full_database_backup_interval_day))
        SaveDictionary(DataDictionary.DICT_TYPE_BACKUP_AIO_DATABASE, 'db_sav_day', str(database_retention_day))
        if run_immediately == 1:
            obj = StorageNode.objects.filter(ident=database_backup_storagedevice).first()
            if obj is not None:
                save_path = obj.path
                set_db_bak_device_path(save_path)
                BackupDatabaseBase().back_db_base()
                BackupDatabase().back_db_xlog()
    else:
        SaveDictionary(DataDictionary.DICT_TYPE_BACKUP_AIO_DATABASE, 'ebe_db_bak', str(enable_database_backup))
        SaveDictionary(DataDictionary.DICT_TYPE_BACKUP_AIO_DATABASE, 'db_bak_dev',
                       str(database_backup_storagedevice))
        SaveDictionary(DataDictionary.DICT_TYPE_BACKUP_AIO_DATABASE, 'bak_hour',
                       str(increment_database_backup_interval_hour))
        SaveDictionary(DataDictionary.DICT_TYPE_BACKUP_AIO_DATABASE, 'bak_day',
                       str(full_database_backup_interval_day))
        SaveDictionary(DataDictionary.DICT_TYPE_BACKUP_AIO_DATABASE, 'db_sav_day', str(database_retention_day))
    return HttpResponse(json.dumps({'r': 0, 'e': ''}))


def get_database_backup_info(request):
    ret = {'r': 0, 'e': ''}
    ret['enable_database_backup'] = GetDictionary(DataDictionary.DICT_TYPE_BACKUP_AIO_DATABASE,
                                                  'ebe_db_bak', 0)
    ret['database_backup_storagedevice'] = GetDictionary(DataDictionary.DICT_TYPE_BACKUP_AIO_DATABASE,
                                                         'db_bak_dev', -1)
    ret['increment_database_backup_interval_hour'] = GetDictionary(DataDictionary.DICT_TYPE_BACKUP_AIO_DATABASE,
                                                                   'bak_hour', 0)
    ret['full_database_backup_interval_day'] = GetDictionary(DataDictionary.DICT_TYPE_BACKUP_AIO_DATABASE,
                                                             'bak_day', 0)
    ret['database_retention_day'] = GetDictionary(DataDictionary.DICT_TYPE_BACKUP_AIO_DATABASE,
                                                  'db_sav_day', 30)
    return HttpResponse(json.dumps(ret))


def get_backup_io_cyclicity(request):
    return HttpResponse(json.dumps(
        {'r': 0, 'e': '',
         'BackupIOCyclicity': GetDictionary(DataDictionary.DICT_TYPE_BACKUP_PARAMS, 'bk_io_cyc', 200)}))


def set_backup_io_cyclicity(request):
    BackupIOCyclicity = int(request.GET.get('BackupIOCyclicity', 500))
    return HttpResponse(
        json.dumps(SaveDictionary(DataDictionary.DICT_TYPE_BACKUP_PARAMS, 'bk_io_cyc', BackupIOCyclicity)))


def get_aio_mtu(request):
    return HttpResponse(json.dumps(
        {'r': 0, 'e': '',
         'aio_mtu': GetDictionary(DataDictionary.DICT_TYPE_AIO_NETWORK, 'aio_mtu', -1)}))


def set_aio_mtu(request):
    aio_mtu = int(request.GET.get('mtu', '1500'))
    return HttpResponse(
        json.dumps(SaveDictionary(DataDictionary.DICT_TYPE_AIO_NETWORK, 'aio_mtu', aio_mtu)))


def execute_cmd_and_return_code(cmd):
    _logger.info("execute_cmd_and_return_code cmd:{}".format(cmd))
    with subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True) as p:
        stdout, stderr = p.communicate()
    return p.returncode, stdout


def _get_openssl_status(bSureDisable=False):
    # 返回值： 1启用 2禁用
    bEnabled = False
    bActive = False
    if bSureDisable:
        cmd = 'systemctl is-enabled sshd'
        ret, out_put_lines = execute_cmd_and_return_code(cmd)
        if ret == 0:
            bEnabled = True
        # disable状态返回值为1

    cmd = 'systemctl is-active sshd'
    ret, out_put_lines = execute_cmd_and_return_code(cmd)
    if ret == 0:
        bActive = True
    # unknown状态返回值为1

    if bSureDisable:
        if bEnabled and bActive:
            return 1
        return 2
    if bActive:
        return 1
    return 2


def set_openssl(request):
    benable_ssh = int(request.POST.get('enable_ssh', '1'))
    if benable_ssh == 1:
        # 启用OpenSSH
        cmd = 'systemctl enable sshd&&systemctl start sshd'
    elif benable_ssh == 2:
        # 禁用OpenSSH
        cmd = 'systemctl disable sshd&&systemctl stop sshd'
    execute_cmd_and_return_code(cmd)

    openssl_status = _get_openssl_status(True)
    if benable_ssh == 1 and openssl_status == 1:
        return HttpResponse(json.dumps({'r': 0, 'e': '操作成功'}))
    if benable_ssh == 2 and openssl_status == 2:
        return HttpResponse(json.dumps({'r': 0, 'e': '操作成功'}))
    return HttpResponse(json.dumps({'r': 1, 'e': '设置失败,e={}'.format(openssl_status)}))


def get_openssl(request):
    benable = False
    if _get_openssl_status() == 1:
        benable = True
    return HttpResponse(json.dumps({'r': 0, 'benable': benable, 'e': '操作成功'}))


def gethandler(request):
    a = request.GET.get('a', 'none')
    if a == 'none':
        a = request.POST.get('a', 'none')
    if a == 'getadapterset':
        return getAdapterSet(request)
    if a == 'sysgetmaxbandwidth':
        return getMaxBandwidth(request)
    if a == 'sysgetsmtpset':
        return getSMTP(request)
    if a == 'syssetsavesmtp':
        return saveSMTP(request)
    if a == 'syssetbandwidth':
        return setMaxBandwidth(request)
    if a == 'setadapter':
        return setAdapterSet(request)
    if a == 'syssettestsendemail':
        return testSendEmail(request)
    if a == 'halt':
        return myhalt(request)
    if a == 'reboot':
        return myreboot(request)
    if a == 'cancel':
        return mycancel(request)
    if a == 'setexpiry':
        return setexpiry(request)
    if a == 'getexpiry':
        return getexpiry(request)
    if a == 'getpwdpolicy':
        return getpwdpolicy(request)
    if a == 'setpwdpolicy':
        return setpwdpolicy(request)
    if a == 'setemailrange':
        return setemailrange(request)
    if a == 'getemailrange':
        return getemailrange(request)
    if a == 'setstartupmedia':
        return setstartupmedia(request)
    if a == 'getstartupmedia':
        return getstartupmedia(request)
    if a == 'getcurrenttime':
        return getcurrenttime(request)
    if a == 'setservertime':
        return setservertime(request)
    if a == 'getavailntpips':
        return ntp.query_avail_and_conf_ntp_ip(request)
    if a == 'updatesystime':
        return ntp.update_system_time_now(request)
    if a == 'getroutes':
        return getroutes(request)
    if a == 'setroutes':
        return setroutes(request)
    if a == 'writeroutes':
        return write_routes()
    if a == 'confirm_ipset':
        return confirm_ipset()
    if a == 'thread_status':
        return get_current_route_ip_threading(request)
    if a == 'ping_cmd':
        return ping_cmd(request)
    if a == 'tcpdump':
        return tcpdump(request)
    if a == 'get_tcpdump_status':
        return get_tcpdump_status()
    if a == 'tcpdump_stop':
        return tcpdump_stop()
    if a == 'get_ping_content':
        return get_ping_content()
    if a == 'adapter_pxe':
        return adapter_pxe(request)
    if a == 'set_ssh_kvm':
        return set_ssh_kvm(request)
    if a == 'get_ssh_kvm':
        return get_ssh_kvm(request)
    if a == 'set_takeover_segment':
        return set_takeover_segment(request)
    if a == 'get_takeover_segment':
        return get_takeover_segment(request)
    if a == 'get_partial_disk_snapshot_exp_days':
        return get_partial_disk_snapshot_exp_days(request)
    if a == 'set_partial_disk_snapshot_exp_days':
        return set_partial_disk_snapshot_exp_days(request)
    if a == 'set_task_queue_num':
        return set_task_queue_num(request)
    if a == 'get_task_queue_num':
        return get_task_queue_num(request)
    if a == 'disable_vt':
        return disable_vt(request)
    if a == 'get_vt_status':
        return get_vt_status(request)
    if a == 'save_database_backup_info':
        return save_database_backup_info(request)
    if a == 'get_database_backup_info':
        return get_database_backup_info(request)
    if a == 'get_backup_io_cyclicity':
        return get_backup_io_cyclicity(request)
    if a == 'set_backup_io_cyclicity':
        return set_backup_io_cyclicity(request)
    if a == 'set_openssl':
        return set_openssl(request)
    if a == 'get_openssl':
        return get_openssl(request)
    if a == 'send_email':
        return send_email(request)
    if a == 'get_aio_mtu':
        return get_aio_mtu(request)
    if a == 'set_aio_mtu':
        return set_aio_mtu(request)
    return HttpResponse(json.dumps({"r": "1", "e": "没有对应的action:{}".format(html.escape(a))}))
