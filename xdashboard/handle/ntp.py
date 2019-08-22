import json
import time
from django.http import HttpResponse
from box_dashboard import xlogging, xsys, xdata
from xdashboard.handle.sysSetting import ntplib

_logger = xlogging.getLogger(__name__)

ntp_server_ips = ['asia.pool.ntp.org', 'cn.pool.ntp.org', 'tw.pool.ntp.org']

conf_path = r'/etc/ntp.conf'
syn_hwclock_path = r'/etc/sysconfig/ntpd'


def query_avail_and_conf_ntp_ip(request):
    ips = {'ntp_ips': ntp_server_ips, 'e': 0}

    with open(conf_path, 'r') as fout:
        lines = fout.readlines()
    lines = list(filter(lambda line: line.startswith('server '), lines))
    ips['conf_ip'] = lines[0].split(' ')[1] if lines else None

    return HttpResponse(json.dumps(ips))


def remove_old_server_ips(lines):
    new_lines = list()
    for line in lines:
        new_lines.append(line) if not line.startswith('server ') else None
    return new_lines


def enable_sync_hwclock():
    with open(syn_hwclock_path, 'r') as fin:
        lines = fin.readlines()
    new_lines = list(filter(lambda line: not line.startswith('SYNC_HWCLOCK'), lines))

    new_lines.append('SYNC_HWCLOCK=yes\n')
    with open(syn_hwclock_path, 'w') as fout:
        fout.writelines(new_lines)


def modify_ntp_server_ips_conf(ntp_ip, is_init=False):
    with open(conf_path, 'r') as fin:
        lines = fin.readlines()

    new_lines = remove_old_server_ips(lines)
    with open(conf_path, 'w') as fout:
        fout.writelines(new_lines)
        fout.write('server {}'.format(ntp_ip))

    if is_init:
        enable_sync_hwclock()

    time.sleep(1)
    xsys.restart_ntp_server()  # 重启生效


# 从指定的"ntp ip"获取时间
# 获取成功则: 更新系统时间, 刷入bios, 修改serverip配置
# 返回更新是否成功等信息
def update_system_time_now(request):
    ntp_ip = request.GET['ntp_ip']
    client = ntplib.NTPClient()
    try:
        info = client.request(host=ntp_ip, timeout=xdata.ACCESS_NTP_SERVER_TIMEOUT_SECS)
    except Exception as e:
        _logger.warning('get ntp time from server[{0}] failed, debug:{1}'.format(ntp_ip, str(e)))
        return HttpResponse(json.dumps({'e': 1}))

    secs = info.recv_time
    struct = time.localtime(secs)  # 系统默认时区：CST
    time_str = '{0}-{1}-{2} {3}:{4}:{5}'.format(struct.tm_year, struct.tm_mon, struct.tm_mday, struct.tm_hour,
                                                struct.tm_min, struct.tm_sec)
    xsys.set_system_and_bios_time(time_str)  # 2016-12-12 10:00:00
    modify_ntp_server_ips_conf(ntp_ip)

    return HttpResponse(json.dumps({'e': 0, 'time': time_str}))
