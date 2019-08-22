import json, threading
import os
import requests
from datetime import datetime
import django.utils.timezone as timezone
from django.http import HttpResponse
from requests.auth import HTTPBasicAuth
from apiv1.models import BackupTaskSchedule, RestoreTask, MigrateTask, HTBTask, HostSnapshotShare, Host, \
    HostRebuildRecord, HTBSchedule, ClusterBackupSchedule, RestoreTarget, Tunnel, ArchiveSchedule, FileSyncSchedule
from apiv1.storage_nodes import StorageNodeLogic
from box_dashboard import boxService
from box_dashboard import xlogging, xdata
from xdashboard.handle.sysSetting import dhcpUtil
from xdashboard.handle import version
from xdashboard.handle.authorize import authCookies
from rest_framework import status
from Crypto.Cipher import PKCS1_v1_5 as PKCS1
from Crypto.PublicKey import RSA
import base64
from functools import lru_cache
from django.core.cache import cache

_logger = xlogging.getLogger(__name__)

AIO_SN_PATH = r'/etc/aio/authorize/sn'
AIO_PRIKEY_PATH = r'/etc/aio/authorize/priKey'
AIO_AUTHORIZE_FILE_PATH = r'/etc/aio/authorize/authorize'
AIO_NET_ERROR_FILE_PATH = r'/etc/aio/authorize/neterrortime'
AIO_NET_LIC_FLAG_FILE = r'/usr/lib64/www_license.txt'


def deal_auth_exception(func):
    def new_function(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if r'connection timed out' in str(e).lower() or r'failed to establish' in str(e).lower():
                msgs = '访问授权服务器失败, 请确认AIO能访问到授权服务器'
            elif r'connection fail' in str(e).lower():
                msgs = '登陆授权服务器失败, 请检查用户名/密码是否有效'
            else:
                msgs = '未知错误: {}'.format(e)
            return HttpResponse(json.dumps({'is_success': False, 'msgs': msgs}))

    return new_function


# 读写SN Str
def aio_serial_number(sn=None):
    if sn is None:
        if not boxService.box_service.isFileExist(AIO_SN_PATH):
            return None
        with open(AIO_SN_PATH, mode='rt', encoding='utf-8') as fin:
            sn = fin.read()
        return sn
    else:
        boxService.box_service.makeDirs(os.path.dirname(AIO_SN_PATH))
        with open(AIO_SN_PATH, mode='wt', encoding='utf-8') as fout:
            fout.write(sn)


# 读写priKey Str
def aio_priKey(priKey=None):
    if priKey is None:
        if not boxService.box_service.isFileExist(AIO_PRIKEY_PATH):
            return None
        with open(AIO_PRIKEY_PATH, mode='rt', encoding='utf-8') as fin:
            priKey = fin.read()
        return priKey
    else:
        boxService.box_service.makeDirs(os.path.dirname(AIO_PRIKEY_PATH))
        with open(AIO_PRIKEY_PATH, mode='wt', encoding='utf-8') as fout:
            fout.write(priKey)


# 读写授权文件(RSA)
def aio_authorize_file(rsa_bytes=None):
    if rsa_bytes is None:
        if not boxService.box_service.isFileExist(AIO_AUTHORIZE_FILE_PATH):
            return None
        with open(AIO_AUTHORIZE_FILE_PATH, mode='rb') as fin:
            rsa_bytes = fin.read()
        return rsa_bytes
    else:
        boxService.box_service.makeDirs(os.path.dirname(AIO_AUTHORIZE_FILE_PATH))
        with open(AIO_AUTHORIZE_FILE_PATH, mode='wb') as fout:
            fout.write(rsa_bytes)


# 本地AIO的版本，mac地址
def get_aio_macs_version():
    mac = dhcpUtil.DHCPConfigFile.getMacAddress()
    versn = version.getAIOVersion()
    return mac, versn


# str_or_bytes, str_or_bytes
def decrypt_by_rsa(key_private, cryptograph):
    plaintext_bytes = PKCS1.new(RSA.importKey(key_private)).decrypt(base64.b64decode(cryptograph), None)
    return plaintext_bytes


# 查询可用型号
def http_query_aio_available_models_name_from_PLIS(plis_ip, plis_port, username, password):
    ins = authCookies.AuthCookies(r'http://{}:{}/'.format(plis_ip, plis_port), username, password)
    secure_cookie, csrf_token, f_url = ins.get(r'authorize/factory/')
    rsp1 = requests.get(
        f_url,
        headers={'Content-Type': 'application/json', 'X-CSRFToken': csrf_token},
        params={'info_type': 'avail_mods_name', 'addition_arg': ''},
        cookies=secure_cookie,
        auth=HTTPBasicAuth(username, password)
    )
    if status.is_success(rsp1.status_code):
        rsp1_data = json.loads(rsp1.content.decode('utf-8'))
        return rsp1_data['avail_mods_name']
    return None


# 报告PLIS，该AIO初始授权成功
def http_report_factory_authorize_success(aio_sn, selected_name, plis_ip, plis_port, username, password):
    ins = authCookies.AuthCookies(r'http://{}:{}/'.format(plis_ip, plis_port), username, password)
    secure_cookie, csrf_token, f_url = ins.get(r'authorize/factory/')
    requests.head(f_url,
                  headers={'Content-Type': 'application/json', 'X-CSRFToken': csrf_token},
                  params={'aio_sn': aio_sn, 'selected_mod_name': selected_name},
                  cookies=secure_cookie,
                  auth=HTTPBasicAuth(username, password))


# 从本地加密文件提取出明文(异常：None)
def get_authorize_plaintext_from_local():
    rsa_bytes = aio_authorize_file()
    plaintext = analyze_rsa_bytes_and_convert_to_json(rsa_bytes)
    return plaintext


# rsa_bytes分块解密，重组得到明文
@lru_cache(None)  # 永不过期
@xlogging.convert_exception_to_value(None)
def analyze_rsa_bytes_and_convert_to_json(rsa_bytes):
    cry_blocks = rsa_bytes.split(b'-block-')
    cry_blocks = list(filter(lambda x: x, cry_blocks))
    json_str = ''
    priKey = aio_priKey()
    for cry_block in cry_blocks:
        json_str += decrypt_by_rsa(priKey, cry_block).decode('utf-8')
    return json.loads(json_str)


# 更新授权文件到本地(全量的)
def update_authorize(request):
    type = request.GET.get('type')
    if type == 'pub_key':
        from apiv1.www_license import wwwlicense_pre, wwwlicense
        try:
            pub_key_file = request.FILES['pub_key_file'].read()
            pub_key_file_vec = pub_key_file.split(b'|')
            with open(r'/etc/aio/authorize/sn', mode='wb') as fout:
                fout.write(pub_key_file_vec[0])
            with open(r'/etc/aio/authorize/www_pub.key', mode='wb') as fout:
                fout.write(pub_key_file_vec[1])
        except Exception as e:
            return HttpResponse(json.dumps({'is_success': False, 'msgs': '{}'.format(e)}))
        wwwlicense_pre()
        wwwlicense()
        return HttpResponse(json.dumps({'is_success': True, 'msgs': '授权成功'}))

    try:
        rsa_bytes = request.FILES['file'].read()
    except Exception:
        return HttpResponse(json.dumps({'is_success': False, 'msgs': '读取授权文件失败'}))

    json_txt = analyze_rsa_bytes_and_convert_to_json(rsa_bytes)
    if (json_txt is None) or ('name' not in json_txt) or ('license' not in json_txt):
        return HttpResponse(json.dumps({'is_success': False, 'msgs': '解密授权文件失败'}))

    # 解密授权文件成功才写入本地
    aio_authorize_file(rsa_bytes=rsa_bytes)
    return HttpResponse(json.dumps({'is_success': True, 'msgs': '授权成功'}))


def get_item_val(val_str):
    if val_str.isdigit() and int(val_str) == 0x40000000:
        return '无限制'

    return val_str


# 查询AIO当前的授权json_txt
def query_current_authorize(request):
    json_txt = get_authorize_plaintext_from_local()
    if json_txt is None:
        json_txt = dict()
        json_txt['license'] = list()
    json_txt['aio_sn'] = aio_serial_number()
    for item in json_txt['license']:
        item['show_txt'] = [item['display_name'],
                            '授权：{}'.format(get_item_val(item['value'])),
                            '当前已使用：{}'.format(running_tasks_cnt(item['license_guid']))]
        if item['license_guid'] == 'separation_of_the_three_members':
            item['sub_functions'] = list()
            m = get_separation_of_the_three_members()
            if m.is_separation_of_the_three_members_available():
                item['sub_functions'].append('安全保密管理员、系统管理员、安全审计管理员')
            if m.is_cannot_del_log():
                item['sub_functions'].append('日志不能删除')
            if m.is_database_use_policy():
                item['sub_functions'].append('日志数据存储空间大小 日志空间告警 日志空间自动覆盖')
            if m.is_strict_password_policy():
                item['sub_functions'].append('密码错误次数最多5次，密码定期更换不得长于7天，不支持弱密码')

    return HttpResponse(json.dumps(json_txt))


# 登陆前检查: 文件正常，文件正确性
@xlogging.convert_exception_to_value(False)
def check_init_authorize():
    # 文件存在否
    if None in [aio_serial_number(), aio_priKey(), aio_authorize_file()]:
        return False

    # 文件正确性
    json_txt = get_authorize_plaintext_from_local()
    if (json_txt is None) or ('name' not in json_txt) or ('license' not in json_txt):
        return False

    return True


# 返回可用型号名到UI
@deal_auth_exception
def get_avail_aio_models_name_from_plis(request):
    params = request.GET
    plis_ip = params['plis_ip']
    username, password = params['username'], params['password']

    # 获取可用型号名
    mods_names = http_query_aio_available_models_name_from_PLIS(plis_ip, '8000', username, password)
    if mods_names is None:
        ret_json = json.dumps({'is_success': False, 'msgs': '获取AIO可用型号列表失败'})
        return HttpResponse(ret_json)

    ret_json = json.dumps({'is_success': True, 'names': mods_names})
    return HttpResponse(ret_json)


# 运行初始授权到AIO, 反馈结果到UI
@deal_auth_exception
def run_factory_authorize(request):
    params = request.GET
    plis_ip, plis_port, selected_name = params['plis_ip'], '8000', params['selected_name']
    username, password = params['username'], params['password']
    aio_mac, aio_version = get_aio_macs_version()

    # 访问PLIS，初始授权
    ins = authCookies.AuthCookies(r'http://{}:{}/'.format(plis_ip, plis_port), username, password)
    secure_cookie, csrf_token, f_url = ins.get(r'authorize/factory/')
    rsp1 = requests.post(
        f_url,
        headers={'Content-Type': 'application/json', 'X-CSRFToken': csrf_token},
        data=json.dumps({'aio_mac': aio_mac, 'aio_version': aio_version, 'selected_name': selected_name}),
        cookies=secure_cookie,
        auth=HTTPBasicAuth(username, password)
    )
    if status.is_success(rsp1.status_code):
        rsp1_data = json.loads(rsp1.content.decode('utf-8'))
        rsa_str, aio_sn, aio_prKey = rsp1_data['rsa_str'], rsp1_data['aio_sn'], rsp1_data['aio_prKey']

        # 直接存入rsa_bytes，aio_sn，aio_prKey到本地
        rsa_bytes = bytes(rsa_str, encoding='utf-8')
        aio_authorize_file(rsa_bytes=rsa_bytes)
        aio_serial_number(sn=aio_sn)
        aio_priKey(priKey=aio_prKey)

        json_txt = analyze_rsa_bytes_and_convert_to_json(rsa_bytes=rsa_bytes)
        if (json_txt is None) or ('name' not in json_txt) or ('license' not in json_txt):
            return HttpResponse(json.dumps({'is_success': False, 'msgs': '解密授权文件失败'}))

        http_report_factory_authorize_success(aio_sn, selected_name, plis_ip, plis_port, username, password)
        return HttpResponse(json.dumps({'is_success': True}))

    return HttpResponse(json.dumps({'is_success': False, 'msgs': '访问PLIS初始授权失败'}))


# 从明文提取指定license_guid的值
def get_license_val_by_guid(license_guid, json_txt):
    for _license in json_txt['license']:
        if _license['license_guid'] == license_guid:
            return _license['value']
    return None


# 是否为试用版, 过期否
def check_evaluation_version(from_api=False):
    json_txt = get_authorize_plaintext_from_local()
    expiration = get_license_val_by_guid('expiration_date', json_txt)  # should 'null','2016-1-1'
    is_evaluation = expiration != 'null'

    expir_date = None
    if is_evaluation:
        expir_date = datetime.strptime(expiration, '%Y-%m-%d')
        is_expiration = timezone.now() > expir_date
    else:
        is_expiration = None

    if from_api:
        return is_evaluation and is_expiration
    return HttpResponse(
        json.dumps({'is_evaluation': is_evaluation, 'is_expiration': is_expiration, 'expir_date': expiration}))


# True: “试用版”且“过期了”
# False: “试用版”且“未过期” 或 "销售版"
def is_evaluation_and_expiration():
    return check_evaluation_version(from_api=True)


# 登录前检查
@xlogging.convert_exception_to_value({'is_evaluation': 'error', 'is_expiration': 'error'})
def evaluation_expiration_info():
    resp = check_evaluation_version()
    infos = json.loads(resp.content.decode())
    infos = {'is_evaluation': 'yes' if infos['is_evaluation'] else 'no',
             'is_expiration': 'yes' if infos['is_expiration'] else 'no',
             'expir_date': infos['expir_date']}
    return infos


# 登录后检查: 界面右下角，描述授权信息
def base_ui_query_current_authorize():
    return HttpResponse(json.dumps(evaluation_expiration_info()))


def _is_virtual_host(system_infos):
    try:
        for disk in system_infos.get('Disk', []):
            if 'VMWARE' in disk.get('DiskName', '').upper():
                return True
            if 'QEMU' in disk.get('DiskName', '').upper():
                return True
            if 'XEN' in disk.get('DiskName', '').upper():
                return True
            if 'VIRTUAL' in disk.get('DiskName', '').upper():
                return True
        return False
    except Exception:
        return False


def get_host_type(host_ident):
    host_type = cache.get('host_{}'.format(host_ident))
    if host_type:
        return host_type
    host = Host.objects.get(ident=host_ident)
    if host.type == Host.NAS_AGENT:
        return 'nas'
    if host.type == Host.PROXY_AGENT:
        return 'proxy_agent'
    system_infos = json.loads(host.ext_info).get('system_infos')
    virtual_host = ''
    if _is_virtual_host(system_infos):
        virtual_host = 'virtual_host_'
    jsonobj = get_authorize_plaintext_from_local()
    _license = _get_license_by_guid('manage_host_num_virtual_pc', jsonobj)
    if _license is None:
        # 没有virtual的权限信息，则不区分
        virtual_host = ''
    SystemCaption = system_infos['System']['SystemCaption'].upper()
    if 'LINUX' in SystemCaption:
        host_type = virtual_host + 'server'
    elif 'SERVER' in SystemCaption:
        host_type = virtual_host + 'server'
    else:
        host_type = virtual_host + 'pc'
    cache.set('host_{}'.format(host_ident), host_type, None)
    return host_type


def get_hotbackup_schedule_count():
    HTBSchedule_count = HTBSchedule.objects.filter(deleted=False).count()
    return HTBSchedule_count


def get_clusterbackup_schedule_count():
    clusterbackup_count = ClusterBackupSchedule.objects.filter(deleted=False).count()
    return clusterbackup_count


def get_takeover_count():
    from apiv1.takeover_logic import TakeOverKVMCreate
    api_request = {'type': 'count', 'kvm_type': 'forever_kvm'}
    api_response = TakeOverKVMCreate().get(request=None, api_request=api_request)
    if not status.is_success(api_response.status_code):
        _logger.error(r'get_takeover_count Failed.e={}'.format(api_response.data))
        return 0
    res_data = json.loads(api_response.data)
    return res_data['count']


def get_temporary_takeover_count():
    from apiv1.takeover_logic import TakeOverKVMCreate
    api_request = {'type': 'count', 'kvm_type': 'temporary_kvm'}
    api_response = TakeOverKVMCreate().get(request=None, api_request=api_request)
    if not status.is_success(api_response.status_code):
        _logger.error(r'get_takeover_count Failed.e={}'.format(api_response.data))
        return 0
    res_data = json.loads(api_response.data)
    return res_data['count']


def get_remotebackup_schedule_count():
    from apiv1.remote_views import RemoteBackupView
    api_request = {'type': 'count'}
    api_response = RemoteBackupView().post(None, api_request)
    if not status.is_success(api_response.status_code):
        return 0

    res_data = json.loads(api_response.data)
    return res_data['count']


def get_rebuild_host_num():
    rebuild_count = HostRebuildRecord.objects.filter().count()
    return rebuild_count


def get_license_BackupTaskSchedule():
    all_avail_plans = BackupTaskSchedule.objects.filter(deleted=False)
    cdp_plans = list(filter(lambda plan: plan.cycle_type == BackupTaskSchedule.CYCLE_CDP, all_avail_plans))
    hosts_in_cdp = set(plan.host.ident for plan in cdp_plans)
    hosts_in_manage = set()
    hosts_virtual_server_in_manage = set()
    hosts_virtual_pc_in_manage = set()
    hosts_server_in_manage = set()
    hosts_pc_in_manage = set()
    proxy_agent_in_manage = set()
    for plan in all_avail_plans:
        hosts_in_manage.add(plan.host.ident)
        host_type = get_host_type(plan.host.ident)
        if host_type == 'server':
            hosts_server_in_manage.add(plan.host.ident)
        elif host_type == 'pc':
            hosts_pc_in_manage.add(plan.host.ident)
        elif host_type == 'virtual_host_server':
            hosts_virtual_server_in_manage.add(plan.host.ident)
        elif host_type == 'virtual_host_pc':
            hosts_virtual_pc_in_manage.add(plan.host.ident)
        elif host_type == 'proxy_agent':
            proxy_agent_in_manage.add(plan.host.ident)
        else:
            hosts_pc_in_manage.add(plan.host.ident)
    return hosts_in_manage, hosts_server_in_manage, hosts_pc_in_manage, hosts_in_cdp, hosts_virtual_server_in_manage, hosts_virtual_pc_in_manage, proxy_agent_in_manage


class get_license_Thread(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        self.run_status = True

    def run(self):
        get_license_BackupTaskSchedule()

    def stop(self):
        self.run_status = False


def get_tunnel_num():
    return Tunnel.objects.filter().count()


# 统计正在运行的任务数
def running_tasks_cnt(license_guid):
    if license_guid == 'storage_capacity':
        storage_nodes = StorageNodeLogic.get_all_nodes(False)
        total_bytes = 0
        for node in storage_nodes:
            if node['total_bytes']:
                total_bytes += node['total_bytes']
        return '{0:.2f}'.format(total_bytes / 1024 ** 4)

    hosts_in_manage, hosts_server_in_manage, hosts_pc_in_manage, hosts_in_cdp, hosts_virtual_server_in_manage, hosts_virtual_pc_in_manage, proxy_agent_in_manage = get_license_BackupTaskSchedule()

    if license_guid == 'manage_host_num':
        return len(hosts_in_manage)

    if license_guid == 'manage_host_num_server':
        return len(hosts_server_in_manage)

    if license_guid == 'manage_host_num_pc':
        return len(hosts_pc_in_manage)

    if license_guid == 'cdp_host_num':
        return len(hosts_in_cdp)

    if license_guid == 'manage_host_num_virtual_sever':
        return len(hosts_virtual_server_in_manage)

    if license_guid == 'manage_host_num_virtual_pc':
        return len(hosts_virtual_pc_in_manage)

    if license_guid == 'vmwarebackup':
        return len(proxy_agent_in_manage)

    if license_guid == 'restore_task_concurrent':
        return RestoreTask.objects.filter(finish_datetime__isnull=True).count()

    if license_guid == 'migrate_task_concurrent':
        return MigrateTask.objects.filter(finish_datetime__isnull=True).count()

    if license_guid == 'hot_standby_concurrent':
        return HTBTask.objects.filter(start_datetime__isnull=False, finish_datetime__isnull=True).count()

    if license_guid == 'demo_backup_concurrent':
        return 0

    if license_guid == 'scan_backup_concurrent':
        return HostSnapshotShare.objects.all().count()

    if license_guid == 'hotBackup':
        return get_hotbackup_schedule_count()

    if license_guid == 'clusterbackup':
        return get_clusterbackup_schedule_count()

    if license_guid == 'takeover':
        return get_takeover_count()

    if license_guid == 'temporary_takeover':
        return get_temporary_takeover_count()

    if license_guid == 'remotebackup':
        return get_remotebackup_schedule_count()

    if license_guid == 'rebuild_host_num':
        return get_rebuild_host_num()

    if license_guid == 'tunnel_num':
        return get_tunnel_num()

    if license_guid == 'importExport':
        return get_backup_archive_count()

    return 0


def _get_license_by_guid(license_guid, json_txt):
    for _license in json_txt['license']:
        if _license['license_guid'].lower() == license_guid.lower():
            return _license
    return None


def _default_can_use_module(module):
    if module in (
            'temporary_takeover', 'tunnel_num', 'remove_duplicates_in_system_folder', 'manage_host_num_virtual_pc',
            'manage_host_num_virtual_sever',):
        return True
    return False


# 功能是否可见
def is_module_visible(module):
    try:
        jsonobj = get_authorize_plaintext_from_local()
        _license = _get_license_by_guid(module, jsonobj)
        if _license is None:
            return _default_can_use_module(module)
        if _license.get('fun_visible', 'no') == 'no':
            return False
        return True
    except Exception:
        return False


# 功能是否可用
def is_module_available(module):
    try:
        jsonobj = get_authorize_plaintext_from_local()
        _license = _get_license_by_guid(module, jsonobj)
        if _license is None:
            return _default_can_use_module(module)
        if _license.get('fun_available', 'no') == 'no':
            return False
        return True
    except Exception:
        return False


def get_license_int_value(module):
    jsonobj = get_authorize_plaintext_from_local()
    _license = _get_license_by_guid(module, jsonobj)
    if _license is None:
        if _default_can_use_module(module):
            return 1073741824
        return 0
    return int(_license.get('value', 0))


def _get_restore_target_maclist(host_ident):
    target = RestoreTarget.objects.filter(ident=host_ident)
    mac_list = list()
    if target:
        info = json.loads(target[0].info)
        system_infos = info.get('system_infos', None)
        if system_infos:
            Nic = system_infos.get('Nic', None)
            if Nic:
                for macs in Nic:
                    Mac = macs.get('Mac', None)
                    if Mac:
                        mac_list.append(xdata.standardize_mac_addr(Mac))
    return mac_list


def _get_rebuild_record(mac_list):
    for mac in mac_list:
        host = HostRebuildRecord.objects.filter(mac_list__contains=mac)
        if host:
            return host[0].host_ident
    return None


def save_host_rebuild_record(host_ident):
    mac_list = _get_restore_target_maclist(host_ident)
    if len(mac_list) == 0:
        _logger.error('save_host_rebuild_record Failed.len(mac_list)==0')
        return
    ident = _get_rebuild_record(mac_list)
    if ident:
        build_recode = HostRebuildRecord.objects.filter(host_ident=ident)
        rebuild_count = build_recode[0].rebuild_count + 1
        HostRebuildRecord.objects.filter(host_ident=ident).update(rebuild_count=rebuild_count)
    else:
        host_rebuild_record = HostRebuildRecord()
        host_rebuild_record.rebuild_count = 1
        host_rebuild_record.host_ident = host_ident
        host_rebuild_record.mac_list = json.dumps(mac_list, ensure_ascii=False)
        host_rebuild_record.save()


def check_host_rebuild_count(host_ident):
    rebuild_count = get_rebuild_host_num()
    mac_list = _get_restore_target_maclist(host_ident)
    ident = _get_rebuild_record(mac_list)
    if ident:
        return {'r': 0, 'e': 'OK'}
    count = get_license_int_value('rebuild_host_num')
    if rebuild_count >= count:
        return {'r': 2, 'e': '重建目标机硬件信息缓存已满，请联系原厂售后服务4001615658',
                'debug': 'count={},rebuild_count={}'.format(count, rebuild_count)}
    return {'r': 0, 'e': 'OK'}


class get_separation_of_the_three_members():
    def __init__(self):
        self.module_value = self.get_module_int_value()

    def get_module_int_value(self):
        try:
            module = 'separation_of_the_three_members'
            jsonobj = get_authorize_plaintext_from_local()
            _license = _get_license_by_guid(module, jsonobj)
            if _license is None:
                return 0
            if _license.get('fun_available', 'no') == 'no':
                return 0
            return int(_license.get('value', 0))
        except Exception:
            return 0

    def is_separation_of_the_three_members_available(self):
        return self.module_value & 1

    def is_cannot_del_log(self):
        return self.module_value & 2

    def is_database_use_policy(self):
        return self.module_value & 4

    def is_strict_password_policy(self):
        return self.module_value & 8


def get_backup_archive_count():
    archive_count = ArchiveSchedule.objects.filter(deleted=False).count()
    file_sync = FileSyncSchedule.objects.filter(deleted=False).count()
    return archive_count + file_sync


def check_backup_archive_license():
    from xdashboard.common import license
    clret = license.check_license('importExport')
    if clret.get('r', 0) != 0:
        return clret
    count_current = get_backup_archive_count()
    count_license = license.get_functional_int_value('importExport')
    if count_current >= count_license:
        return {'r': 2, 'e': '当前授权数量{}，已创建计划数量{}，请增加授权数量或删除一些计划。'.format(count_license, count_current)}
    return {'r': 0, 'e': 'OK'}
