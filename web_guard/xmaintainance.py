# coding:utf-8
import sys

from box_dashboard import xlogging
from box_dashboard.boxService import box_service

_logger = xlogging.getLogger('XMAINTAINANCE')


def _find_srv_name_by_pid(info_list, pid):
    service_name = None
    for info in info_list:
        if info.dwProcessId == pid:
            service_name = info.lpServiceName
            break
    _logger.debug('_find_srv_name_by_pid(info_list, pid={}) return: service_name={}'.format(pid, service_name))
    return service_name


def _get_bs(port, port_list, bs_list):
    for n, p in enumerate(port_list):
        if p == port and n < len(bs_list):
            return bs_list[n]
    return None


def _str_info_list(info_list):
    ret_string = ''
    for info in info_list:
        ret_string += 'dwProcessId={}, lpServiceName={}, lpDisplayName={}, dwCurrentState={}, dwServiceType={}'.format(
            info.dwProcessId, info.lpServiceName, info.lpDisplayName, info.dwCurrentState, info.dwServiceType)
    return ret_string


def wrap_get_http_d_service_list_sync(hostname):
    _logger.debug('get_http_d_service_list_sync(hostname={})'.format(hostname))
    status, port_list_done = box_service.get_http_d_service_list_sync(hostname)
    if status != 0:
        raise Exception('获取处于维护模式的端口列表失败, 错误代码: {}'.format(status))
    _logger.debug('get_http_d_service_list_sync return: status={}, port_list_done={}'.format(status, port_list_done))
    return status, port_list_done


def wrap_get_tcp_listen_list(hostname, new_port_list):
    _logger.debug('get_tcp_listen_list(host_name={}, new_port_list={})'.format(hostname, new_port_list))
    status, pid_list = box_service.get_tcp_listen_list(hostname, new_port_list)
    if status != 0:
        raise Exception('获取端口列表:{}对应的进程列表失败, 错误代码: {}'.format(new_port_list, status))
    _logger.debug('get_tcp_listen_list return: status={}, pid_list={}'.format(status, pid_list))
    return status, pid_list


def wrap_get_service_list(hostname):
    _logger.debug('get_service_list(hostname={})'.format(hostname))
    status, info_list = box_service.get_service_list(hostname)
    if status != 0:
        raise Exception('获取服务列表失败, 错误代码: {}'.format(status))
    _logger.debug('get_service_list return: status={}, info_list={}'.format(status, _str_info_list(info_list)))
    return status, info_list


def wrap_stop_service_sync(hostname, service_name):
    _logger.debug('stop_service_sync(hostname={}, service_name={})'.format(hostname, service_name))
    status = box_service.stop_service_sync(hostname, service_name)
    _logger.debug('stop_service_sync return: status={}'.format(status))
    return status


def wrap_start_http_d_service_async(hostname, port, bs):
    _logger.debug('start_http_d_service_async(hostname={}, port={})'.format(hostname, port))
    status = box_service.start_http_d_service_async(hostname, port, bs)
    _logger.debug('start_http_d_service_async return: status={}'.format(status))
    return status


def wrap_stop_all_http_d_service_sync(hostname):
    _logger.debug('stop_all_http_d_service_sync(hostname={})'.format(hostname))
    status = box_service.stop_all_http_d_service_sync(hostname)
    _logger.debug('stop_all_http_d_service_sync return: status={}'.format(status))
    return status


def wrap_start_service_sync(hostname, service_name):
    _logger.debug('start_service_sync(hostname={}, service_name={})'.format(hostname, service_name))
    status = box_service.start_service_sync(hostname, service_name)
    _logger.debug('start_service_sync return: status={}'.format(status))
    return status


def enter_maintain_mode(hostname, port_list, bs_list, stop_script, is_linux):
    """
    传入hostname, port_list, 调用C接口, 查询并返回客户端占用这些端口的服务名称
    如果调用ice暴露出来的api失败, 抛出异常.
    :param hostname: 客户端机器标识, 据说是个guid, 不用管, 传入API即可
    :param port_list: 端口, 由业务来定, 80, 443?
    :return status, 0 - 成功, 非0 - 停止服务失败的个数
            dict(key = port, value = (state, servicename))
            state = 0, 表示已经是我们的服务, servicename - clrw_httpd表示我们的服务
            state = 0, 表示是客户的服务, servicename - 客户服务的名称, None表示进程bind
            state = 非0, 表示客户的服务停止失败错误码, servicename - 客户服务的名称
    """
    _logger.debug('enter_maintain_mode(hostname={}, port_list={})'.format(hostname, port_list))
    status, port_list_done = wrap_get_http_d_service_list_sync(hostname)

    ret_dict = dict()
    new_port_list = list()
    stop_failed_count = 0

    # key=port, value=(status, service_name)
    for port in port_list:
        if port in port_list_done:
            ret_dict[port] = (0, 'clrw_httpd')
        else:
            ret_dict[port] = None
            new_port_list.append(port)

    _logger.debug('ret_dict={}, new_port_list={}'.format(ret_dict, new_port_list))

    if len(new_port_list) == 0:
        _logger.debug('已经全部处于维护模式')
        return 0, ret_dict

    if is_linux:
        box_service.writeFile2Host(hostname, 'current', 'enter_maintain_mode', 0, bytearray(stop_script, 'utf8'))
        box_service.simpleRunShell(hostname, 'chmod 754 ./enter_maintain_mode')
        box_service.simpleRunShell(hostname, './enter_maintain_mode > log/enter_maintain_mode.log')

        status, pid_list = wrap_get_tcp_listen_list(hostname, new_port_list)
        for pid in pid_list:
            box_service.simpleRunShell(hostname, 'kill -11 {}'.format(pid))

        for port in new_port_list:
            bs = _get_bs(port, port_list, bs_list)
            status = wrap_start_http_d_service_async(hostname, port, bs)
            if status != 0:
                _logger.error('启动服务占用端口:{} 失败, 失败代码: {}'.format(port, status))
                stop_failed_count += 1
                continue

            _logger.debug('stop port={} and start out pid success'.format(port))
    else:
        status, pid_list = wrap_get_tcp_listen_list(hostname, new_port_list)
        status, info_list = wrap_get_service_list(hostname)
        _logger.debug('len(new_port_list)={}, len(pid_list)={}'.format(len(new_port_list), len(pid_list)))

        for index, pid in enumerate(pid_list):  # 会不会出现端口复用, 返回pid比port多, 导致index越界?
            if index >= len(new_port_list):
                raise Exception('通过端口列表:{}获得进程列表:{}, 数目不匹配'.format(new_port_list, pid_list))

            port = new_port_list[index]
            service_name = None
            if pid != 0:  # 如果为0表示该端口没有人占用, 反之要查service_anme
                service_name = _find_srv_name_by_pid(info_list, pid)
                if service_name is None:
                    if pid == 4:  # platform.system().lower() == 'windows'
                        service_name = 'W3SVC'
                        _logger.debug('windows pid=4 service_name=W3SVC entered')
                    else:
                        raise Exception('端口已绑定进程ID, 但找不到服务名称, 需要特殊处理, 请寻求技术支持.')

                # 至此, service_name必不为None了, 为什么不在此raise呢, 因为此时可能已经有些停止了.
                status = wrap_stop_service_sync(hostname, service_name)
                if status != 0:
                    _logger.error('停止服务:{}, 状态码:{}'.format(service_name, status))
                    stop_failed_count += 1
                    ret_dict[port] = (status, service_name)  # 返回停止结果
                    continue

            bs = _get_bs(port, port_list, bs_list)
            status = wrap_start_http_d_service_async(hostname, port, bs)
            if status != 0:
                _logger.error('启动服务占用端口:{} 失败, 失败代码: {}, 服务: {} 已停止'.format(port, status, service_name))
                ret_dict[port] = (status, service_name)
                stop_failed_count += 1
                continue

            _logger.debug('stop service_name={} on port={} and start out pid success'.format(service_name, port))
            ret_dict[port] = (0, service_name)

    _logger.debug(
        'enter_maintain_mode leaved: stop_failed_count={}, ret_dict={}'.format(stop_failed_count, ret_dict))
    return stop_failed_count, ret_dict


def get_maintain_status(hostname, port_list):
    """
     给定一组port, 看这些端口是否都是我们的服务在占用, 如果是, 返回0, 否则返回没有被我们服务占用的个数
    :param hostname:
    :param port_list:
    :return: status, 0 - 表示全被我们占用, 非0 - 没有被我们服务占用的个数
             dict(key=port, value = True/False)
             True 表示被我们占用, False表示没有被我们占用
    """
    _logger.debug('get_maintain_status(hostname={}, port_list={})'.format(hostname, port_list))

    status, port_list_done = wrap_get_http_d_service_list_sync(hostname)
    if status != 0:
        raise Exception('获取处于维护模式的端口列表失败, 错误代码: {}'.format(status))

    ret_dict = dict()
    not_stop_count = 0
    for port in port_list:
        if port in port_list_done:
            ret_dict[port] = True
        else:
            ret_dict[port] = False
            not_stop_count += 1

    _logger.debug('get_maintain_status leaved: not_stop_count={}, ret_dict={}'.format(not_stop_count, ret_dict))
    return not_stop_count, ret_dict


def leave_maintain_mode(hostname, service_name_list, start_script, is_linux):
    """
    停止我们的所有服务, 依次启动客户的服务
    :param hostname:
    :param service_name_list:
    :return: status 0 - 表示成功, 非0 - 启动服务失败的个数, 查看dict, 可以看到哪些具体成功或失败
            dict(key = service_name, value = status)
            status = 表示启动服务的状态码, 0为启动成功, 非0位错误码
    """
    _logger.debug('leave_maintain_mode(hostname={}, service_name_list={}) entered'.format(hostname, service_name_list))

    status = wrap_stop_all_http_d_service_sync(hostname)
    if status != 0:
        raise Exception('停止所有科力锐服务失败, 错误代码: {}'.format(status))

    ret_dict = dict()
    failed_start_count = 0
    if is_linux:
        box_service.writeFile2Host(hostname, 'current', 'leave_maintain_mode', 0, bytearray(start_script, 'utf8'))
        box_service.simpleRunShell(hostname, 'chmod 754 ./leave_maintain_mode')
        box_service.simpleRunShell(hostname, './leave_maintain_mode > log/leave_maintain_mode.log')
    else:
        for service_name in service_name_list:
            _logger.debug('start_service_sync(hostname={}, service_name={})'.format(hostname, service_name))
            status = wrap_start_service_sync(hostname, service_name)
            _logger.debug('start_service_sync return: status={}'.format(status))
            ret_dict[service_name] = status
            if status != 0:
                _logger.error('启动服务:{}失败, 错误代码: {}'.format(service_name, status))
                failed_start_count += 1

    _logger.debug('leave_maintain_mode leaved: failed_start_count={}, ret_dict={}'.format(failed_start_count, ret_dict))
    return failed_start_count, ret_dict


if __name__ == "__main__":
    sys.exit(0)
