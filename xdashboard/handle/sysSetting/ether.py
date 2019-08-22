import psutil

from box_dashboard import boxService, xlogging, xdata
from xdashboard.models import DeviceRunState

_logger = xlogging.getLogger(__name__)


def _get_ethers_info_line():
    cmd_result_code, ethers_info = boxService.box_service.runCmd('ifconfig', False)
    if cmd_result_code != 0:
        xlogging.raise_and_logging_error('查询网卡信息失败', 'run cmd ifconfig failed, return: {}'.format(ethers_info))
    return ethers_info


def _get_valid_ether_name(ethers_info):
    valid_ether_name = list()
    _list = list()
    one_ether_lines = list()
    for line in ethers_info:
        if line == '':
            _list.append(one_ether_lines)
            one_ether_lines = list()
        else:
            one_ether_lines.append(line)

    for ether in _list:
        ether_str = ''.join(ether)
        if ether_str.startswith('lo') or ether_str.startswith('tap') or ether_str.startswith('vbr'):
            continue
        if 'inet ' not in ether_str:
            continue
        valid_ether_name.append(ether[0].split(': flags')[0])

    return valid_ether_name


def _get_rx_tx_bytes(ether_names):
    net_io_detail = psutil.net_io_counters(pernic=True)
    bytes_recv = 0
    bytes_sent = 0
    for ether_name in ether_names:
        if ether_name in net_io_detail:
            bytes_recv += net_io_detail[ether_name].bytes_recv
            bytes_sent += net_io_detail[ether_name].bytes_sent
    return bytes_recv, bytes_sent


def update_ether_rx_tx_speed_to_db():
    ethers_info = _get_ethers_info_line()
    ether_names = _get_valid_ether_name(ethers_info)
    cur_recv_byte, cur_send_bytes = _get_rx_tx_bytes(ether_names)  # 启动AIO以来：历史接收，发送总字节

    cur_recv_speed, cur_send_speed = 0, 0
    last_rcd_obj = DeviceRunState.objects.filter(type=DeviceRunState.TYPE_NETWORK_IO).order_by('-datetime').first()
    if last_rcd_obj and cur_recv_byte - last_rcd_obj.last_in_total >= 0 and cur_send_bytes - last_rcd_obj.last_out_total >= 0:  # 最新一次记录有效
        cur_recv_speed = int((cur_recv_byte - last_rcd_obj.last_in_total) / xdata.NET_DISK_IO_SAMPLE_INTERVAL_SEC)
        cur_send_speed = int((cur_send_bytes - last_rcd_obj.last_out_total) / xdata.NET_DISK_IO_SAMPLE_INTERVAL_SEC)
    DeviceRunState.objects.create(
        type=DeviceRunState.TYPE_NETWORK_IO,
        writevalue=cur_send_speed, readvalue=cur_recv_speed,
        last_in_total=cur_recv_byte, last_out_total=cur_send_bytes
    )
    _logger.warning('cur_recv_byte[{0}] cur_send_bytes[{1}] cur_recv_speed[{2}] cur_send_speed[{3}]'.format(
        cur_recv_byte, cur_send_bytes, cur_recv_speed, cur_send_speed
    ))
    _logger.warning('ether_names: {}'.format(ether_names))
