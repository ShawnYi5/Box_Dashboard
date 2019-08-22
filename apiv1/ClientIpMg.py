import copy
import json
import os
import sys
import threading
import time

try:
    from box_dashboard import xlogging, xdata

    g_IsDebug = False
except ImportError:
    import logging as xlogging

    xdata = type('XData', (), {'HTB_IP_TYPE_CONTROL': 1})

    g_IsDebug = True

if not g_IsDebug:
    from box_dashboard import boxService

_logger = xlogging.getLogger(__name__)


# ========================================================================================================
def show_and_exe_cmd_line_and_get_ret(in_cmd_line, chk_err_str='', bPrint=True):
    try:
        cmd_line = in_cmd_line + ' 2>&1'
        if bPrint:
            _logger.debug(cmd_line)
        with os.popen(cmd_line) as out_put:
            out_put_lines = out_put.readlines()
            if '' == chk_err_str:
                if bPrint:
                    _logger.debug('0')
                    _logger.debug(out_put_lines)
                return 0, out_put_lines
            for one_line in out_put_lines:
                if -1 != one_line.find(chk_err_str):
                    if bPrint:
                        _logger.debug('show_and_exe_cmd_line_and_get_ret return -1')
                    return -1, []
        if bPrint:
            _logger.debug('0')
            _logger.debug(out_put_lines)
        return 0, out_put_lines
    except Exception as e:
        if bPrint:
            _logger.error('show_and_exe_cmd_line_and_get_ret error : {},return -1'.format(e), exc_info=True)
        return -1, []


def is_windows(one_cmd_and_result):
    return one_cmd_and_result['cmdinfo']['src_is_windows']


# ========================================================================================================
# 通用命令处理类。
class CmdManage(threading.Thread):
    def __init__(self):
        _logger.info(r'CmdManage init begin')
        super(CmdManage, self).__init__()
        self._cmd_manage_lock = threading.Lock()
        self.cmd_and_result_list = []
        _logger.info(r'CmdManage init end')
        self.loop_event = threading.Event()

    def exec_one_cmd(self, one_cmd_and_result):
        try:
            one_cmd_and_result['result'] = 'get_result'
        except Exception as e:
            _logger.error('exec_one_cmd error : {}'.format(e))

    def _loop_task(self):
        try:
            _logger.info(r'_loop_task begin')

            with self._cmd_manage_lock:
                # 一次性把命令拷贝出来，免得耽误别人插入，查询，删除等操作。
                loop_cmd_and_result_list = copy.deepcopy(self.cmd_and_result_list)

            # 执行命令
            for one_cmd_and_result in loop_cmd_and_result_list:
                self.exec_one_cmd(one_cmd_and_result)

            # 将每一个命令执行结果回拷到类成员变量中，以便查询，注意因为同步的问题，可能导致某些成员已不存在。
            with self._cmd_manage_lock:
                # 将查询结果，拷贝到全局变量中，注意有些东西可能已经丢失。
                for one_cmd_and_result in loop_cmd_and_result_list:
                    num = self.get_num_by_ID_in_db(one_cmd_and_result['ID'])
                    if -1 != num:
                        # 在，替换老数据。
                        _logger.info(
                            r'_loop_task gen end ,insert num = {},result = {}'.format(num,
                                                                                      one_cmd_and_result['result']))
                        self.cmd_and_result_list[num]['result'] = copy.deepcopy(one_cmd_and_result['result'])
            _logger.info(r'_loop_task end')
        except Exception as e:
            _logger.error('_loop_task error : {}'.format(e))

    def run(self):
        global g_IsDebug
        try:
            _logger.debug('CmdManage run start')
            while True:
                if g_IsDebug:
                    time.sleep(10)
                else:
                    self.loop_event.wait(30)
                    self.loop_event.clear()
                self._loop_task()
        except Exception as e:
            _logger.error('CmdManage run error : {}'.format(e))

    def get_num_by_ID_in_db(self, ID):
        try:
            _logger.info(r'get_num_by_ID_in_db begin ID = {}'.format(ID))
            count = len(self.cmd_and_result_list)
            for i in range(count):
                if self.cmd_and_result_list[i]['ID'] == ID:
                    _logger.info(r'get_num_by_ID_in_db return i = {}'.format(i))
                    return i
            _logger.info(r'get_num_by_ID_in_db return -1')
            return -1
        except Exception as e:
            _logger.error('get_num_by_ID_in_db error : {}'.format(e))
            return -1

    # one_cmd_and_result = {'ID': ID, 'cmdinfo': cmd_info, 'result': ''}
    # 输入为 cmd_info
    def InsertOrUpdate(self, ID, cmd_info):
        try:
            _logger.info(r'InsertOrUpdate begin ID = {} | {}'.format(ID, cmd_info))
            one_cmd_and_result = {'ID': ID, 'cmdinfo': cmd_info, 'result': ''}
            with self._cmd_manage_lock:
                # 查询 数据是否在 list 中，在，更新,并清除查询结果。
                num = self.get_num_by_ID_in_db(ID)
                if -1 != num:
                    # 在，删除老数据。
                    del self.cmd_and_result_list[num]
                    _logger.info(r'InsertOrUpdate del old data num = {}'.format(num))
                self.cmd_and_result_list.append(one_cmd_and_result)
                self.loop_event.set()
                _logger.info(r'InsertOrUpdate end have append')

                # 立即处理数据。进行设置。
                # self.SetRemoteOneHostIp(hostName, cmd_info)

        except Exception as e:
            _logger.error(r'InsertOrUpdate failed {}'.format(e))

    # one_cmd_and_result = {'ID': ID, 'cmdinfo': cmd_info, 'result': ''}
    # 输出为 result 结构。失败是 ''
    def Query(self, ID):
        try:
            _logger.info(r'Query begin ID = {}'.format(ID))

            with self._cmd_manage_lock:
                num = self.get_num_by_ID_in_db(ID)
                if -1 != num:
                    _logger.info(r'Query end return = {}'.format(
                        self.cmd_and_result_list[num]['result']))
                    return copy.deepcopy(self.cmd_and_result_list[num]['result'])
            _logger.info(r'Query end return = ZERO STR ')
            return ''
        except Exception as e:
            _logger.error(r'Query failed {}'.format(e))
            return ''

    def Remove(self, ID):
        try:
            _logger.info(r'Remove begin ID = {}'.format(ID))
            with self._cmd_manage_lock:
                # 查询 数据是否在 list 中，在，更新,并清除查询结果。
                num = self.get_num_by_ID_in_db(ID)
                if -1 != num:
                    # 在，删除老数据。
                    _logger.info(r'Remove del old data num = {}'.format(num))
                    del self.cmd_and_result_list[num]
            _logger.info(r'Remove del end')
        except Exception as e:
            _logger.error(r'Remove failed {}'.format(e))


# ========================================================================================================

client_ip_mg_threading = None


class ClientIpMgThreading(CmdManage):
    def __init__(self):
        _logger.info(r'ClientIpMgThreading init begin')
        super(ClientIpMgThreading, self).__init__()
        _logger.info(r'ClientIpMgThreading init end')

    def GetRemoteOneHostIp(self, hostName):
        global g_IsDebug
        try:
            _logger.info(r'GetRemoteOneHostIp begin {}'.format(hostName))
            if g_IsDebug:
                with open(r'O:\work\tmp_code\GetAdaptersAddresses\json.txt') as file_handle:
                    get_json_str = file_handle.read()
            else:
                get_json_str = boxService.box_service.querySystemInfo(hostName)
            system_info = json.loads(get_json_str)
            _logger.info(r'GetRemoteOneHostIp {} return info = {}'.format(hostName, system_info['Nic']))
            return system_info['Nic']
        except Exception as e:
            _logger.error(r'SetRemoteOneHostIp {} failed {}'.format(hostName, e))
            return []

    def SetRemoteOneHostIp(self, hostName, one_host_all_adapter):
        global g_IsDebug
        try:
            _logger.info(r'SetRemoteOneHostIp begin {} | {}'.format(hostName, one_host_all_adapter))
            if 0 == len(one_host_all_adapter):
                _logger.info(r'SetRemoteOneHostIp {} 0 == len(one_host_all_adapter) return'.format(hostName))
                return
            query_info = {"SetIpInfo": one_host_all_adapter}
            inputParam = json.dumps(query_info)
            if g_IsDebug:
                pass
            else:
                boxService.box_service.async_JsonFunc(hostName, inputParam)
            _logger.info(r'SetRemoteOneHostIp {} end'.format(hostName))
        except Exception as e:
            _logger.info(r'SetRemoteOneHostIp {} change client ip lead this excption,can ignore,ex = {}'.format(
                hostName, e))

    @staticmethod
    def set_host_ip_linux(host_ident, adapter_dict):
        _logger.debug('set_host_ip_linux host_ident:{}, adapter_list:{}'.format(host_ident, adapter_dict))
        try:
            set_network = {'SetNetwork': json.dumps(adapter_dict)}
            boxService.box_service.async_JsonFunc(host_ident, json.dumps(set_network))
        except Exception as e:
            _logger.error(
                'set_host_ip_linux host_ident:{}, adapter_list:{}, error:{}'.format(host_ident, adapter_dict, e),
                exc_info=True)

    @staticmethod
    def is_same_ip(one_cmd_and_result):
        try:
            _logger.info(r'is_same_ip begin')

            # 检测 IP 是否一致。
            for one_query in one_cmd_and_result['cmdinfo']:
                for ip_mask_list in one_query['ip_mask_list']:
                    bFind = False
                    for one_result in one_cmd_and_result['result']:
                        for one_IpAndMask in one_result['IpAndMask']:
                            if xdata.is_two_mac_addr_equal(one_query['mac'], one_result['Mac']):
                                if ip_mask_list['ip'] == one_IpAndMask['Ip']:
                                    bFind = True
                    if bFind is not True:
                        # 未找到IP,退出。
                        _logger.info(
                            r'is_same_ip not find mac = {},ip = {}'.format(one_query['mac'], ip_mask_list['ip']))
                        return False
            _logger.info(r'is_same_ip find same ip,one_cmd_and_result = {}'.format(one_cmd_and_result))
            return True
        except Exception as e:
            _logger.error('is_same_ip error : {},one_cmd_and_result = {}'.format(e, one_cmd_and_result))
            return False

    # one_cmd_and_result = {'ID': ID, 'cmdinfo': cmd_info, 'result': ''}
    def exec_one_cmd(self, one_cmd_and_result):
        try:
            _logger.info(r'ClientIpMgThreading exec_one_cmd begin')
            # 开始查询。
            r_ident = one_cmd_and_result['cmdinfo']['Backup_id']
            one_cmd_and_result['result'] = copy.deepcopy(self.GetRemoteOneHostIp(r_ident))
            if self.is_same_ip(one_cmd_and_result) is not True:
                # 要设置IP与查询IP结果不一致，开始设置IP。
                _logger.info(r'ClientIpMgThreading exec_one_cmd not the same ip')
                self.SetRemoteOneHostIp(r_ident, one_cmd_and_result['cmdinfo'])
            _logger.info(r'ClientIpMgThreading exec_one_cmd end')
        except Exception as e:
            _logger.error('ClientIpMgThreading exec_one_cmd error : {}'.format(e))


# ========================================================================================================
class ClientIpSwitch(ClientIpMgThreading):
    def __init__(self):
        _logger.info(r'ClientIpSwitch init begin')
        super(ClientIpSwitch, self).__init__()
        _logger.info(r'ClientIpSwitch init end')

    @staticmethod
    def ping_exist(ip):
        try:
            _logger.info(r'ClientIpSwitch ping begin')
            cmd = 'ping -c 3 -w 3 ' + ip + ' | grep "0 received" | wc -l'
            nRet, lines = show_and_exe_cmd_line_and_get_ret(cmd, '1')
            if 0 == nRet:
                _logger.info(r'ClientIpSwitch ping return True')
                return True
            _logger.info(r'ClientIpSwitch ping return False')
            return False
        except Exception as e:
            _logger.error('ClientIpSwitch ping error : {}'.format(e))
            return False

    def _is_same_ip(self, host, qeury_result, ignore_control=False):
        try:
            _logger.info(r'ClientIpSwitch is_same_ip begin')
            # 检测 IP 是否一致。
            for one_query in host:
                for ip_mask_list in one_query['ip_mask_list']:
                    bFind = False
                    for one_result in qeury_result:
                        for one_IpAndMask in one_result['IpAndMask']:
                            if xdata.is_two_mac_addr_equal(one_query['mac'], one_result['Mac']):
                                if ip_mask_list['ip'] == one_IpAndMask['Ip']:
                                    bFind = True
                    if not bFind:
                        # 在数据推送阶段，忽略固有IP不相同的情况
                        if ignore_control and ip_mask_list['ip_type'] == xdata.HTB_IP_TYPE_CONTROL:
                            _logger.info(
                                'ClientIpSwitch _is_same_ip ignore_control mac={},ip={}'.format(one_query['mac'],
                                                                                                ip_mask_list['ip']))
                            continue
                        else:
                            # 未找到IP,退出。
                            _logger.info(
                                r'ClientIpSwitch is_same_ip not find mac = {},ip = {}'.format(one_query['mac'],
                                                                                              ip_mask_list['ip']))
                            return False
            _logger.info(r'ClientIpSwitch is_same_ip find same ip,host = {}'.format(host))
            return True
        except Exception as e:
            _logger.error('ClientIpSwitch is_same_ip error : {},host = {}'.format(e, host))
            return False

    # one_cmd_and_result = {'ID': ID, 'cmdinfo': cmd_info, 'result': 'result_string'}
    # cmd_info = {'task_type': 0, 'Host_id': None,'Host': None, 'Backup_id':None,'Backup': None}
    def set_ip(self, one_cmd_and_result):
        if is_windows(one_cmd_and_result):
            self.set_ip_windows(one_cmd_and_result)
        else:
            self.set_src_ip_linux(one_cmd_and_result)

    def set_ip_windows(self, one_cmd_and_result):
        try:
            _logger.info(r'ClientIpSwitch set_ip begin')
            cmd_info = one_cmd_and_result['cmdinfo']
            # 开始查询。
            self.push_task_is_removed(one_cmd_and_result['ID'])
            qeury_result = copy.deepcopy(self.GetRemoteOneHostIp(cmd_info['Host_id']))
            self.push_task_is_removed(one_cmd_and_result['ID'])
            if self._is_same_ip(cmd_info['Host'], qeury_result, ignore_control=True) is not True:
                # 要设置IP与查询IP结果不一致，开始设置IP。
                _logger.info(r'ClientIpSwitch set_ip not the same ip,will set')
                self.SetRemoteOneHostIp(cmd_info['Host_id'], cmd_info['Host'])
                one_cmd_and_result['result'] = 'have setting host ip'
            else:
                _logger.info(r'ClientIpSwitch set_ip is same ip,not need set')
                one_cmd_and_result['result'] = 'success'
            _logger.info(r'ClientIpSwitch set_ip end')
        except Exception as e:
            _logger.error('ClientIpSwitch set_ip error : {}'.format(e))

    def all_data_ip_can_not_user(self, host):
        global g_IsDebug
        if host is None:
            _logger.info('all_data_ip_can_not_user host is None return True')
            return True
        try:
            _logger.info('ClientIpSwitch all_data_ip_can_not_user begin')
            bCanUser = False
            if g_IsDebug:
                return True
            for one_query in host:
                for ip_mask_list in one_query['ip_mask_list']:
                    if ip_mask_list['ip_type'] == 1:
                        continue
                    if self.ping_exist(ip_mask_list['ip']):
                        _logger.error(
                            'ClientIpSwitch all_data_ip_can_not_user one ip is used = {}'.format(ip_mask_list['ip']))
                        bCanUser = True
                        break

            if bCanUser:
                _logger.info('ClientIpSwitch all_data_ip_can_not_user False')
                return False
            _logger.info('ClientIpSwitch all_data_ip_can_not_user return True')
            return True
        except Exception as e:
            _logger.error('ClientIpSwitch all_data_ip_can_not_user error : {}'.format(e))
            return False

    def SetRemoteOnlyControlIp(self, hostName, one_host_all_adapter):
        global g_IsDebug
        if hostName is None:
            _logger.info(r'SetRemoteOnlyControlIp hostName is None return')
            return
        try:
            _logger.info(r'SetRemoteOnlyControlIp begin {} | {}'.format(hostName, one_host_all_adapter))
            if 0 == len(one_host_all_adapter):
                _logger.info(r'SetRemoteOnlyControlIp {} 0 == len(one_host_all_adapter) return'.format(hostName))
                return
            query_info = {"ReservIpInfo_OnlyControl": one_host_all_adapter}
            inputParam = json.dumps(query_info)
            if g_IsDebug:
                pass
            else:
                boxService.box_service.async_JsonFunc(hostName, inputParam)
            _logger.info(r'SetRemoteOnlyControlIp {} end'.format(hostName))
        except Exception as e:
            _logger.info(r'SetRemoteOnlyControlIp {} change client ip lead this excption,can ignore,ex = {}'.format(
                hostName, e))

    def switch_hot_backup(self, one_cmd_and_result):
        if is_windows(one_cmd_and_result):
            self.switch_hot_backup_windows(one_cmd_and_result)
        else:
            self.switch_hot_backup_linux(one_cmd_and_result)

    # one_cmd_and_result = {'ID': ID, 'cmdinfo': cmd_info, 'result': 'result_string'}
    # cmd_info = {'task_type': 0, 'Host_id': None,'Host': None, 'Backup_id':None,'Backup': None}
    def switch_hot_backup_windows(self, one_cmd_and_result):
        try:
            _logger.info(r'ClientIpSwitch switch_hot_backup begin')
            cmd_info = one_cmd_and_result['cmdinfo']
            # 已经发送过后，重复发送，主机会反复登录/掉线
            if one_cmd_and_result['result'] != 'success_switch_host':
                self.SetRemoteOnlyControlIp(cmd_info['Host_id'], cmd_info['Host'])
            for one in range(30):
                if one_cmd_and_result['result'] == 'success_switch_host' or \
                        self.all_data_ip_can_not_user(cmd_info['Host']):
                    if one_cmd_and_result['result'] != 'success':
                        one_cmd_and_result['result'] = 'success_switch_host'
                    if cmd_info['Backup_id'] is None:
                        return
                    else:
                        qeury_result = copy.deepcopy(self.GetRemoteOneHostIp(cmd_info['Backup_id']))
                        if len(qeury_result) == 0:
                            time.sleep(1)
                            continue  # 如果获取失败，那么尝试再次获取
                        if self._is_same_ip(cmd_info['Backup'], qeury_result) is not True:
                            # 要设置IP与查询IP结果不一致，开始设置IP。
                            _logger.info(r'ClientIpSwitch switch_hot_backup not the same ip')
                            self.SetRemoteOneHostIp(cmd_info['Backup_id'], cmd_info['Backup'])
                        else:
                            _logger.info(r'ClientIpSwitch switch_hot_backup return success')
                            one_cmd_and_result['result'] = 'success'
                            return

            # 重新赋值会导致 上次任务执行结果 被覆盖掉，进入‘SetRemoteOnlyControlIp’ 死循环
            # _logger.info(r'ClientIpSwitch switch_hot_backup return host have use')
            # one_cmd_and_result['result'] = 'host have use'
            _logger.info(r'ClientIpSwitch switch_hot_backup end')
        except Exception as e:
            _logger.error('ClientIpSwitch switch_hot_backup error : {}'.format(e))

    def switch_hot_backup_linux(self, one_cmd_and_result):
        try:
            _logger.info(r'switch_hot_backup_linux  begin one_cmd_and_result:{}'.format(one_cmd_and_result))
            _cmd_info = one_cmd_and_result['cmdinfo']
            # 已经发送过后，重复发送，主机会反复登录/掉线
            if one_cmd_and_result['result'] != 'success_switch_host':
                self.set_src_ip_only_control(_cmd_info['Host_id'], _cmd_info['Host'])
            for one in range(30):
                if one_cmd_and_result['result'] == 'success_switch_host' or \
                        self.all_data_ip_can_not_user(_cmd_info['Host']):
                    if one_cmd_and_result['result'] != 'success':
                        one_cmd_and_result['result'] = 'success_switch_host'
                    if _cmd_info['Backup_id'] is None:
                        return
                    else:
                        query_result = copy.deepcopy(self.GetRemoteOneHostIp(_cmd_info['Backup_id']))
                        if len(query_result) == 0:
                            time.sleep(1)
                            continue  # 如果获取失败，那么尝试再次获取
                        if not self._is_same_ip(_cmd_info['Backup'], query_result):
                            # 要设置IP与查询IP结果不一致，开始设置IP。
                            self.set_dst_ip_linux(_cmd_info['Backup_id'], _cmd_info['Backup'])
                        else:
                            _logger.info(r'switch_hot_backup_linux  return success')
                            one_cmd_and_result['result'] = 'success'
                            return
        except Exception as e:
            _logger.error('switch_hot_backup_linux error:{}'.format(e), exc_info=True)

    def set_src_ip_only_control(self, host_ident, adapter_list):
        if host_ident is None:
            _logger.info('set_src_ip_only_control host_ident is None return')
            return
        try:
            _logger.info('set_src_ip_only_control adapter_list:{}'.format(adapter_list))
            control_ip = self._get_spc_type_ip(adapter_list, xdata.HTB_IP_TYPE_CONTROL)
            adapter_dict = dict()
            adapter_dict['ip_info'] = control_ip
            adapter_dict['ip_info_file'] = list()
            self.set_host_ip_linux(host_ident, adapter_dict)
        except Exception as e:
            _logger.error('set_src_ip_only_control error:{}'.format(e))

    def set_src_ip_linux(self, one_cmd_and_result):
        try:
            _logger.debug('set_ip_linux begin one_cmd_and_result:{}'.format(one_cmd_and_result))
            _cmd_info = one_cmd_and_result['cmdinfo']
            # 开始查询。
            self.push_task_is_removed(one_cmd_and_result['ID'])
            query_result = copy.deepcopy(self.GetRemoteOneHostIp(_cmd_info['Host_id']))
            self.push_task_is_removed(one_cmd_and_result['ID'])
            if not self._is_same_ip(_cmd_info['Host'], query_result, ignore_control=True):
                adapter_dict = dict()
                adapter_dict['ip_info'] = _cmd_info['Host']
                adapter_dict['ip_info_file'] = self._get_spc_type_ip(_cmd_info['Host'],
                                                                     xdata.HTB_IP_TYPE_CONTROL)

                self.set_host_ip_linux(_cmd_info['Host_id'], adapter_dict)
                one_cmd_and_result['result'] = 'have setting host ip'
            else:
                if one_cmd_and_result['result'] not in ['have setting host ip', 'success']:
                    adapter_dict = dict()
                    adapter_dict['ip_info'] = list()
                    adapter_dict['ip_info_file'] = self._get_spc_type_ip(_cmd_info['Host'],
                                                                         xdata.HTB_IP_TYPE_CONTROL)
                    self.set_host_ip_linux(_cmd_info['Host_id'], adapter_dict)
                _logger.info(r'set_ip_linux one_cmd_and_result:{} is same not need set!'.format(one_cmd_and_result))
                one_cmd_and_result['result'] = 'success'
        except Exception as e:
            _logger.error('set_ip_linux error:{}'.format(e), exc_info=True)

    # 发生在热备数据推送期间
    def set_dst_ip_linux(self, host_ident, adapter_list):
        try:
            _logger.info('set_dst_ip_linux adapter_list:{}'.format(adapter_list))
            adapter_dict = dict()
            adapter_dict['ip_info'] = adapter_list
            adapter_dict['ip_info_file'] = list()
            self.set_host_ip_linux(host_ident, adapter_dict)
        except Exception as e:
            _logger.error('set_src_ip_only_control error:{}'.format(e))

    @staticmethod
    def _get_spc_type_ip(adapter_list, ip_type):
        rs = list()
        for info in adapter_list:
            item = dict()
            item['mac'] = info['mac']
            item['gate_way'] = info['gate_way']
            item['dns_list'] = info['dns_list']
            item['ip_mask_list'] = [{'ip': ip_mask['ip'], 'mask': ip_mask['mask'], 'ip_type': ip_mask['ip_type']}
                                    for ip_mask in
                                    info['ip_mask_list'] if ip_mask['ip_type'] == ip_type]
            rs.append(item)
        return rs

    # one_cmd_and_result = {'ID': ID, 'cmdinfo': cmd_info, 'result': 'result_string'}
    # cmd_info = {'task_type': 0, 'Host_id': None,'Host': None, 'Backup_id':None,'Backup': None}
    def exec_one_cmd(self, one_cmd_and_result):
        try:
            _logger.info(r'ClientIpSwitch exec_one_cmd begin')
            cmd_info = one_cmd_and_result['cmdinfo']
            if cmd_info['task_type'] == 1:
                # 开始热备切换
                self.switch_hot_backup(one_cmd_and_result)
            else:
                self.set_ip(one_cmd_and_result)
            _logger.info(r'ClientIpSwitch exec_one_cmd end')
        except Exception as e:
            _logger.error('ClientIpSwitch exec_one_cmd error : {}'.format(e))

    @staticmethod
    def push_task_is_removed(task_id):
        with client_ip_mg_threading._cmd_manage_lock:
            if client_ip_mg_threading.get_num_by_ID_in_db(task_id) == -1:
                raise Exception('task:{} is removed'.format(task_id))


# ========================================================================================================
class SendCompressAndRunInClient():
    def __init__(self):
        _logger.info(r'SendCompressAndRunInClient init begin')
        super(SendCompressAndRunInClient, self).__init__()
        _logger.info(r'SendCompressAndRunInClient init end')

    # 返回值是 异常
    # timeout_sec 是字符串
    # cmd = {'AppName': AppName, 'param': param, 'workdir': workdir, 'unzip_dir': unzip_dir, 'timeout_sec': timeout_sec,
    #        'username': username, 'pwd': pwd, 'serv_zip_full_path': serv_zip_full_path}
    def exec_one_cmd(self, ID, cmd, bIsWin):
        _logger.info(r'SendCompressAndRunInClient exec_one_cmd begin ID = {}'.format(ID))
        # 开始执行命令。
        # 打开本地文件。并读取推送直到完成。
        zip_base_name = os.path.basename(cmd['serv_zip_full_path'])
        if g_IsDebug:
            aio_path = r'O:\work\tmp_code\JSonTest\Debug\wolf.zip'
        else:
            aio_path = cmd['serv_zip_full_path']
        with open(aio_path, 'rb') as aio_zip_handle:
            byteOffset = 0
            while True:
                once_bin_read = aio_zip_handle.read(1000)
                if once_bin_read is None:
                    break
                if 0 == len(once_bin_read):
                    break
                if 0 == byteOffset:
                    write_cmd = {'type': 'write_new', 'pathPrefix': 'current', 'path': zip_base_name,
                                 'byteOffset': str(byteOffset), 'bytes': 0}
                else:
                    write_cmd = {'type': 'write_exist', 'pathPrefix': 'current', 'path': zip_base_name,
                                 'byteOffset': str(byteOffset), 'bytes': 0}
                write_cmd_str = json.dumps(write_cmd)
                if g_IsDebug:
                    pass
                else:
                    boxService.box_service.JsonFuncV2(ID, write_cmd_str, once_bin_read)
                byteOffset += len(once_bin_read)

        # 发送 JsonFun 命令，解压缩。
        if bIsWin:
            param_str = r' x "|current|\\' + zip_base_name + r'" -o"' + cmd['unzip_dir'] + r'" -r -aoa '
            input_cmd = {'exec': {'username': None, 'pwd': None, 'AppName': r'|current|\7z1602-extra\7za.exe',
                                  'param': param_str, 'workdir': '|current|', 'timeout_sec': cmd['timeout_sec']}}
            inputParam = json.dumps(input_cmd)
            boxService.box_service.getHostHardwareInfo(ID, inputParam)
        else:
            # 发送 JsonFun 命令，解压缩。
            param_str = r' -p "' + cmd['unzip_dir'] + '"'
            input_cmd = {'exec': {'username': None, 'pwd': None, 'AppName': r'mkdir',
                                  'param': param_str, 'workdir': '|current|', 'timeout_sec': cmd['timeout_sec']}}
            inputParam = json.dumps(input_cmd)
            boxService.box_service.getHostHardwareInfo(ID, inputParam)
            param_str = r' -zxf "|current|/' + zip_base_name + r'" -C"' + cmd['unzip_dir'] + '/"'
            input_cmd = {'exec': {'username': None, 'pwd': None, 'AppName': r'tar',
                                  'param': param_str, 'workdir': '|current|', 'timeout_sec': cmd['timeout_sec']}}
            inputParam = json.dumps(input_cmd)
            boxService.box_service.getHostHardwareInfo(ID, inputParam)
            param_str = r' 777 "' + cmd['unzip_dir'] + '/' + cmd['AppName'] + '"'
            input_cmd = {'exec': {'username': None, 'pwd': None, 'AppName': r'chmod',
                                  'param': param_str, 'workdir': '|current|', 'timeout_sec': cmd['timeout_sec']}}
            inputParam = json.dumps(input_cmd)
            boxService.box_service.getHostHardwareInfo(ID, inputParam)
        # if g_IsDebug:
        #     with open(r'O:\work\tmp_code\JSonTest\JSonTest\call.txt', 'w') as write_handle:
        #         write_handle.write(inputParam)
        #     pass
        # else:
        #     boxService.box_service.getHostHardwareInfo(ID, inputParam)

        # 发送 JsonFun 命令，执行解压缩后的程序。
        if bIsWin:
            input_cmd = {'exec': {'username': None, 'pwd': None, 'AppName': cmd['unzip_dir'] + '\\' + cmd['AppName'],
                                  'param': cmd['param'], 'workdir': cmd['workdir'], 'timeout_sec': cmd['timeout_sec']}}
        else:
            input_cmd = {'exec': {'username': None, 'pwd': None, 'AppName': cmd['unzip_dir'] + '/' + cmd['AppName'],
                                  'param': cmd['param'], 'workdir': cmd['workdir'], 'timeout_sec': cmd['timeout_sec']}}
        inputParam = json.dumps(input_cmd)
        if g_IsDebug:
            with open(r'O:\work\tmp_code\JSonTest\JSonTest\call.txt', 'w') as write_handle:
                write_handle.write(inputParam)
            pass
        else:
            boxService.box_service.getHostHardwareInfo(ID, inputParam)
        _logger.info(r'SendCompressAndRunInClient exec_one_cmd end')

    def push_file(self, host_ident, src_path, dst_path, dst_dir):
        with open(src_path, 'rb') as aio_zip_handle:
            byteOffset = 0
            while True:
                once_bin_read = aio_zip_handle.read(1024 * 512)
                if once_bin_read is None:
                    break
                if 0 == len(once_bin_read):
                    break
                if 0 == byteOffset:
                    write_cmd = {'type': 'write_new', 'pathPrefix': dst_dir, 'path': dst_path,
                                 'byteOffset': str(byteOffset), 'bytes': 0}
                else:
                    write_cmd = {'type': 'write_exist', 'pathPrefix': dst_dir, 'path': dst_path,
                                 'byteOffset': str(byteOffset), 'bytes': 0}
                write_cmd_str = json.dumps(write_cmd)
                if g_IsDebug:
                    pass
                else:
                    boxService.box_service.JsonFuncV2(host_ident, write_cmd_str, once_bin_read)
                    byteOffset += len(once_bin_read)

    def exc_command(self, host_ident, input_dict):
        input_cmd = {'exec': input_dict}
        params = json.dumps(input_cmd)
        boxService.box_service.getHostHardwareInfo(host_ident, params)


# ========================================================================================================
class ip_info:
    def __init__(self):
        self.ip_mask_list = list()
        self.mac = ''
        self.gate_way = ''
        self.dns_list = list()

    def set_mac(self, mac_str):
        self.mac = mac_str

    def set_add_to_ip_mask_list(self, ip_str, ip_type_int, mask_str):
        item = dict()
        item['ip'] = ip_str
        item['ip_type'] = ip_type_int
        item['mask'] = mask_str
        self.ip_mask_list.append(item)

    def set_gate_way(self, gate_way_str):
        self.gate_way = gate_way_str

    def set_add_to_dns_list(self, ip_str):
        self.dns_list.append(ip_str)


class setip_info:
    def __init__(self):
        self.task_type = 0
        self.Host_id = ''
        self.Host = list()
        self.Backup_id = ''
        self.Backup = list()

    def set_task_type(self, task_type_int):
        self.task_type = task_type_int

    def set_Host_id(self, Host_id_str):
        self.Host_id = Host_id_str

    def set_Host(self, ip_info):
        self.Host.append(ip_info)

    def set_Backup_id(self, Backup_id_str):
        self.Backup_id = Backup_id_str

    def set_Backup(self, ip_info):
        self.Backup.append(ip_info)

    def to_json(self):
        this_dict = self.__dict__
        js = json.dumps(self, default=lambda o: o.__dict__, sort_keys=True, indent=4)
        return js

    def to_dict(self):
        return self.__dict__


if __name__ == "__main__":
    xlogging.basicConfig(stream=sys.stdout, level=xlogging.DEBUG)
    # ==============================================================
    # t = CmdManage()
    # t.start()
    # time.sleep(10)
    # t.InsertOrUpdate('12345','test_cmd')
    # t.InsertOrUpdate('12345','test_cmd')
    # time.sleep(20)
    # print(t.Query('11111'))
    # print(t.Query('12345'))
    # t.Remove('11111')
    # print(t.Query('11111'))
    # t.Remove('12345')
    # print(t.Query('12345'))
    # t.InsertOrUpdate('12345','test_cmd')
    # ==============================================================

    # t = ClientIpMgThreading()
    # t.start()
    # # ==============================================================
    # SetIpInfo_info = []
    # one_adapeter = {'mac': None, 'ip_mask_list': None, 'dns_list': None, 'gate_way': None}
    # ip_mask_list = []
    # one_ip_mask_info = {'ip_type': 0, 'ip': None, 'mask': None}
    # dns_list = []
    #
    # one_adapeter['mac'] = 'B0:25:AA:09:ED:8D';
    # one_adapeter['gate_way'] = '172.16.1.1';
    #
    # one_ip_mask_info['ip_type'] = 1
    # one_ip_mask_info['ip'] = '172.16.6.60'
    # one_ip_mask_info['mask'] = '255.255.0.0'
    # ip_mask_list.append(copy.deepcopy(one_ip_mask_info));
    #
    # one_ip_mask_info['ip_type'] = 0
    # one_ip_mask_info['ip'] = '192.168.6.60'
    # one_ip_mask_info['mask'] = '255.255.255.0'
    # ip_mask_list.append(copy.deepcopy(one_ip_mask_info));
    #
    # dns_list.append('140.207.198.6')
    # dns_list.append('114.114.114.114')
    #
    # one_adapeter['ip_mask_list'] = ip_mask_list;
    # one_adapeter['dns_list'] = dns_list;
    #
    # SetIpInfo_info.append(copy.deepcopy(one_adapeter))
    # # =======================================================
    # ip_mask_list.clear()
    # dns_list.clear()
    #
    # one_adapeter['mac'] = '28-C2-DD-20-B5-F0';
    # one_adapeter['gate_way'] = None;
    #
    # one_ip_mask_info['ip_type'] = 0
    # one_ip_mask_info['ip'] = '192.168.10.60'
    # one_ip_mask_info['mask'] = '255.255.255.0'
    # ip_mask_list.append(copy.deepcopy(one_ip_mask_info));
    #
    # one_ip_mask_info['ip_type'] = 0
    # one_ip_mask_info['ip'] = '192.168.20.60'
    # one_ip_mask_info['mask'] = '255.255.255.0'
    # ip_mask_list.append(copy.deepcopy(one_ip_mask_info));
    #
    # dns_list.append('140.207.198.6')
    # dns_list.append('114.114.114.114')
    #
    # one_adapeter['ip_mask_list'] = ip_mask_list;
    # one_adapeter['dns_list'] = dns_list;
    #
    # SetIpInfo_info.append(copy.deepcopy(one_adapeter))
    # # =======================================================
    # t.InsertOrUpdate('123456', SetIpInfo_info)
    # t.Query('123456AA')
    # t.Query('123456')
    # t.Remove('123456AA')
    # t.Query('123456AA')
    # t.Query('123456')
    # t.Remove('123456')
    # t.Query('123456AA')
    # t.Query('123456')
    # # =======================================================
    # t = SendCompressAndRunInClient()
    # cmd = {'AppName': 'test.exe', 'param': None, 'workdir': None, 'unzip_dir': r"c:\tmp", 'timeout_sec': None,
    #        'username': None, 'pwd': None, 'serv_zip_full_path': '/home/wolf.zip'}
    # t.exec_one_cmd('12345', cmd, True)
    # # cmd = {'AppName': 'test.exe', 'param': ' param', 'workdir': r'c:\tmp', 'unzip_dir': r"c:\tmp", 'timeout_sec': 30,
    # #        'username': None, 'pwd': None, 'serv_zip_full_path': '/home/wolf.zip'}
    # # t.exec_one_cmd('12345', cmd, True)

    t = ClientIpSwitch()
    t.start()
    # t.ping_exist('172.16.1.151')
    si = setip_info()

    si.set_task_type(1)

    si.set_Backup_id('ac8f246b2bb54303909d725fc1136d1c')
    back = ip_info()
    back.set_mac('00-50-56-95-29-BC')
    back.set_add_to_ip_mask_list('172.16.125.202', 1, '255.255.0.0')
    back.set_add_to_ip_mask_list('172.16.125.203', 0, '255.255.0.0')
    back.set_gate_way('172.16.1.1')
    back.set_add_to_dns_list('172.16.1.1')
    back.set_add_to_dns_list('61.128.128.68')
    si.set_Backup(back)

    host = ip_info()
    si.set_Host_id('68bab7199d494ce4990f4e2150b8ace0')
    host.set_mac('00-50-56-95-71-D9')
    host.set_add_to_ip_mask_list('172.16.125.200', 1, '255.255.0.0')
    host.set_add_to_ip_mask_list('172.16.125.201', 0, '255.255.0.0')
    host.set_gate_way('172.16.1.1')
    host.set_add_to_dns_list('172.16.1.1')
    host.set_add_to_dns_list('61.128.128.68')
    si.set_Host(host)

    get_str = si.to_json()

    cmd_info = json.loads(get_str)
    t.InsertOrUpdate('68bab7199d494ce4990f4e2150b8ace0', cmd_info)
