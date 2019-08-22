import os
import traceback
import sys
import configparser
import time
import re
from box_dashboard import xlogging

_logger = xlogging.getLogger(__name__)


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


class CDhcpConfig:
    def __init__(self):
        pass

    def __set_one_sub_net(self, one_line, one_sub_net, name, re1, re2):
        try:
            com1 = re.compile(re1)
            ret1 = com1.search(one_line)
            if ret1 is None:
                return False
            # _logger(ret1)
            com2 = re.compile(re2)
            ret2 = com2.search(one_line, ret1.span()[1])
            # _logger(ret2)
            if ret2 is None:
                return False
            one_sub_net[name] = ret2.group()
            return True
        except:
            _logger.error(traceback.format_exc())
            return False

    def __set_one_host(self, one_line, one_host, name, re1, re2):
        try:
            com1 = re.compile(re1)
            ret1 = com1.search(one_line)
            if ret1 is None:
                return False
            # _logger(ret1)
            com2 = re.compile(re2)
            ret2 = com2.search(one_line, ret1.span()[1])
            # _logger(ret2)
            if ret2 is None:
                return False
            one_host[name] = ret2.group()
            return True
        except:
            _logger.error(traceback.format_exc())
            return False

    def __check_one_line(self, one_line, one_sub_net, one_host):
        try:
            # _logger(one_line)
            ret1 = self.__set_one_sub_net(one_line, one_sub_net, 'subnet', r'\bsubnet\s', '\w*\.\w*\.\w*\.\w*')
            ret1 = self.__set_one_sub_net(one_line, one_sub_net, 'netmask', r'\bnetmask\s', '\w*\.\w*\.\w*\.\w*')
            ret1 = self.__set_one_sub_net(one_line, one_sub_net, 'range_start', r'\brange\s', '\w*\.\w*\.\w*\.\w*')
            ret1 = self.__set_one_sub_net(one_line, one_sub_net, 'range_end', r'\brange\s\w*\.\w*\.\w*\.\w*\s',
                                          r'\w*\.\w*\.\w*\.\w*')
            ret1 = self.__set_one_sub_net(one_line, one_sub_net, 'routers', r'\brouters\s', '\w*\.\w*\.\w*\.\w*')
            ret1 = self.__set_one_sub_net(one_line, one_sub_net, 'subnet-mask', r'\bsubnet-mask\s',
                                          '\w*\.\w*\.\w*\.\w*')
            ret1 = self.__set_one_sub_net(one_line, one_sub_net, 'default-lease-time', r'\bdefault-lease-time\s', '\w*')
            ret1 = self.__set_one_sub_net(one_line, one_sub_net, 'max-lease-time', r'\bmax-lease-time\s', '\w*')
            ret1 = self.__set_one_sub_net(one_line, one_sub_net, 'next-server', r'\bnext-server\s',
                                          '\w*\.\w*\.\w*\.\w*')
            ret1 = self.__set_one_sub_net(one_line, one_sub_net, 'filename', r'\bfilename\s\"', '\w*')

            ret2 = self.__set_one_sub_net(one_line, one_host, 'name', r'\bhost\s', '\w*_\w*')
            ret2 = self.__set_one_sub_net(one_line, one_host, 'hardware ethernet', r'\bhardware ethernet\s',
                                          '\w\w:\w\w:\w\w:\w\w:\w\w:\w\w')
            ret2 = self.__set_one_sub_net(one_line, one_host, 'fixed-address', r'\bfixed-address\s',
                                          '\w*\.\w*\.\w*\.\w*')

            return ret1, ret2
        except:
            _logger.error(traceback.format_exc())
            return False, False

    def get_dhcp_config(self):
        try:
            sub_net_list = list()
            host_list = list()

            one_sub_net = {'subnet': None, 'netmask': None, 'range_start': None, 'range_end': None,
                           'routers': None, 'subnet-mask': None, 'default-lease-time': None, 'max-lease-time': None,
                           'next-server': None, 'filename': None}
            one_host = {'name': None, 'hardware ethernet': None, 'fixed-address': None}

            with open('/etc/dhcp/dhcpd.conf', 'r') as file_handle:
                lines = file_handle.readlines()
                for one_line in lines:
                    sub_net_list_have_end, host_list_have_end = self.__check_one_line(one_line, one_sub_net, one_host)
                    if sub_net_list_have_end:
                        # _logger(one_sub_net)
                        sub_net_list.append(one_sub_net.copy())
                    if host_list_have_end:
                        # _logger(one_host)
                        host_list.append(one_host.copy())
            return sub_net_list, host_list
        except:
            _logger.error(traceback.format_exc())
            return None, None

    def write_dhcp_config(self, sub_net_list, host_list):
        try:
            if len(sub_net_list) == 0 or len(host_list) == 0:
                show_and_exe_cmd_line_and_get_ret("rm -f /etc/dhcp/dhcpd.conf")
                return 0
            with open('/etc/dhcp/dhcpd.conf', 'w') as file_handle:
                file_handle.write('ignore client-updates;\nallow bootp;\n#deny leasequery;\n#allow booting;\n')
                for one_net in sub_net_list:
                    file_handle.write(
                        'subnet ' + one_net['subnet'] + ' netmask ' + one_net['netmask'] + '\n{\n\trange ' + one_net[
                            'range_start'] + ' ' + one_net['range_end'] + ';\n\toption routers ' + one_net[
                            'routers'] + ';\n\toption subnet-mask ' + one_net[
                            'subnet-mask'] + ';\n\tdefault-lease-time ' + one_net[
                            'default-lease-time'] + ';\n\tmax-lease-time ' + one_net[
                            'max-lease-time'] + ';\n\tnext-server ' + one_net[
                            'next-server'] + ';\n\tfilename "' + one_net['filename'] + '";\n}\n')
                for one_host in host_list:
                    file_handle.write('host ' + one_host['name'] + '{\n\thardware ethernet ' + one_host[
                        'hardware ethernet'] + ';\n\tfixed-address ' + one_host['fixed-address'] + ';\n}\n')
            return 0
        except:
            _logger.error(traceback.format_exc())
            return -1

    def re_start_dhcp(self):
        try:
            ret, lines = show_and_exe_cmd_line_and_get_ret('systemctl restart  dhcpd.service', 'failed')
            return ret
        except:
            traceback.print_exc()
            return -1


if __name__ == "__main__":
    #logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)
    cur_file_dir_str = cur_file_dir()
    os.chdir(cur_file_dir_str)
    config = CDhcpConfig()
    sub_net_list, host_list = config.get_dhcp_config()
    _logger.debug(sub_net_list)
    _logger.debug(host_list)
    _logger.debug(config.write_dhcp_config(sub_net_list, host_list))
    _logger.debug(config.re_start_dhcp())
