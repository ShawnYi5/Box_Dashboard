import os
import traceback
import sys
import configparser
import time
import re
from IPy import IP
import socket
from binascii import hexlify
import sys, struct

try:
    from box_dashboard import xlogging
except ImportError:
    import logging as xlogging

_logger = xlogging.getLogger(__name__)
g_pxe_file_dir = '/var/lib/tftpboot'


def cur_file_dir():
    try:
        # 获取脚本路径
        path = sys.path[0]
        # 判断为脚本文件还是py2exe编译后的文件，如果是脚本文件，则返回的是脚本的目录，如果是py2exe编译后的文件，则返回的是编译后的文件路径
        if os.path.isdir(path):
            _logger.debug("cur_file_dir = %s" % (path))
            return path
        elif os.path.isfile(path):
            _logger.debug("cur_file_dir = %s" % (os.path.dirname(path)))
            return os.path.dirname(path)
    except:
        _logger.error(traceback.format_exc())


def show_and_exe_cmd_line_and_get_ret(in_cmd_line, chk_err_str=''):
    try:
        cmd_line = in_cmd_line + ' 2>&1'
        _logger.debug(cmd_line)
        with os.popen(cmd_line) as out_put:
            out_put_lines = out_put.readlines()
            if '' == chk_err_str:
                _logger.debug('0'), _logger.debug(out_put_lines)
                return 0, out_put_lines
            for one_line in out_put_lines:
                if -1 != one_line.find(chk_err_str):
                    _logger.debug('-1'), _logger.debug(out_put_lines)
                    return -1, out_put_lines
        _logger.debug('0'), _logger.debug(out_put_lines)
        return 0, out_put_lines
    except:
        _logger.error(traceback.format_exc())
        _logger.debug('-1'), _logger.debug(out_put_lines)
        return -1, out_put_lines


class CIpPxe:
    def __init__(self):
        pass

    def str_ip_change_pxe_ip_txt(self, str_ip):
        try:
            pass
        except:
            _logger.error(traceback.format_exc())

    def read_ini(self, path):
        try:
            _logger.debug("read_ini path = {}".format(path))
            ini_list = {'GUID': None, 'bStatic': None, 'port': None, 'mask': None, 'gateway_ip': None, 'serv_ip': None,
                        'dns': None}
            ini_handle = configparser.ConfigParser()
            ini_handle.read(path)
            ini_list['GUID'] = ini_handle.get('main', 'GUID')
            ini_list['bStatic'] = ini_handle.getint('main', 'bStatic')
            ini_list['port'] = ini_handle.getint('main', 'port')
            ini_list['mask'] = ini_handle.get('main', 'mask')
            ini_list['gateway_ip'] = ini_handle.get('main', 'gateway_ip')
            ini_list['serv_ip'] = ini_handle.get('main', 'serv_ip')
            ini_list['dns'] = ini_handle.get('main', 'dns')
            _logger.debug("read_ini ini_list = {}".format(ini_list))
            return ini_list
        except:
            _logger.error(traceback.format_exc())
            return None

    def read_ip_ini(self, ip, dir):
        try:
            # _logger.debug(hex(socket.htonl(IP(ip).int())))
            # _logger.debug(IP(IP(ip).reverseName().strip('.in-addr.arpa.')).strHex())
            pxe_str = IP(ip).strHex().upper()
            subPost = pxe_str.find('0X')
            if -1 != subPost:
                pxe_str = '0x' + pxe_str[subPost + 2:]
            pxe_str = g_pxe_file_dir + '/' + pxe_str + '.txt'
            return self.read_ini(pxe_str)
        except:
            _logger.error(traceback.format_exc())
            return None

    def set_ini(self, ini_list, path):
        try:
            _logger.debug("set_ini path = {}".format(path))
            _logger.debug("set_ini ini_list = {}".format(ini_list))
            if ini_list is None:
                return -1
            ini_handle = configparser.ConfigParser()
            ini_handle.add_section('main')
            ini_handle.set('main', 'GUID', ini_list['GUID'])
            ini_handle.set('main', 'bStatic', str(ini_list['bStatic']))
            ini_handle.set('main', 'port', str(ini_list['port']))
            if ini_list['mask'] is not None:
                ini_handle.set('main', 'mask', ini_list['mask'])
            if ini_list['gateway_ip'] is not None:
                ini_handle.set('main', 'gateway_ip', ini_list['gateway_ip'])
            if ini_list['serv_ip'] is not None:
                ini_handle.set('main', 'serv_ip', ini_list['serv_ip'])
            if ini_list['dns'] is not None:
                ini_handle.set('main', 'dns', ini_list['dns'])
            with open(path, 'w') as file_handle:
                ini_handle.write(file_handle)
                for i in range(512):  # 追加512字节到ini尾部，以便george搜索内存不出错。
                    file_handle.write('\n')
            return 0
        except:
            _logger.error(traceback.format_exc())
            return -1

    def set_ip_ini(self, ip, ini_list, dir):
        try:
            # _logger.debug(hex(socket.htonl(IP(ip).int())))
            # _logger.debug(IP(IP(ip).reverseName().strip('.in-addr.arpa.')).strHex())
            pxe_str = IP(ip).strHex().upper()
            subPost = pxe_str.find('0X')
            if -1 != subPost:
                pxe_str = '0x' + pxe_str[subPost + 2:]
            pxe_str = g_pxe_file_dir + '/' + pxe_str + '.txt'
            return self.set_ini(ini_list, pxe_str)
        except:
            _logger.error(traceback.format_exc())
            return None

    def find_str_in_all_line(self, lines, substr):
        try:
            if lines is None:
                return False
            if substr is None:
                return False

            for line in lines:
                if -1 != line.find(substr):
                    return True
            return False
        except:
            _logger.error(traceback.format_exc())
            return False

    def get_pxe_status(self):
        try:
            _logger.debug("get_pxe_status begin")
            nRet, lines = show_and_exe_cmd_line_and_get_ret('systemctl status dhcpd', 'dead')
            bRet = self.find_str_in_all_line(lines, 'active')
            if bRet != True:
                _logger.debug("get_pxe_status return False:dhcpd failed")
                return False
            nRet, lines = show_and_exe_cmd_line_and_get_ret('systemctl status xinetd', 'dead')
            bRet = self.find_str_in_all_line(lines, 'active')
            if bRet != True:
                _logger.debug("get_pxe_status return False:xinetd failed")
                return False
            _logger.debug("get_pxe_status return True")
            return True
        except:
            _logger.error(traceback.format_exc())
            _logger.debug("get_pxe_status exception return False")
            return False


if __name__ == "__main__":
    xlogging.basicConfig(stream=sys.stdout, level=xlogging.NOTSET)
    cur_file_dir_str = cur_file_dir()
    os.chdir(cur_file_dir_str)
    ip_pxe_class = CIpPxe()
    # ini_list = ip_pxe_class.read_ini(g_pxe_file_dir + '/ip_pxe.txt')
    # ip_pxe_class.set_ip_ini('192.168.1.1', ini_list, g_pxe_file_dir)
    # ini_list_2 = ip_pxe_class.read_ip_ini('192.168.1.1', g_pxe_file_dir)
    ip_pxe_class.get_pxe_status()
