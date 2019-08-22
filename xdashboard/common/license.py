from xdashboard.handle.authorize.authorize_init import is_module_visible, is_module_available, get_license_int_value, \
    is_evaluation_and_expiration


# webguard 网页防篡改
# hotBackup 热备
# clusterbackup 集群备份计划管理
# takeover 接管主机
# vmwarebackup 虚拟化环境(vmware)的免代理备份、还原
# remotebackup 远程容灾
# clusterbackup Oracle RAC
# rebuild_host_num 重建目标数量
def is_functional_visible(module):
    # 授权管理-是否显示该功能
    return is_module_visible(module)


def is_functional_available(module):
    # 授权管理-是否可使用该功能，具体到api的入口处判断
    return is_module_available(module)


def check_license(module):
    if is_evaluation_and_expiration():
        return {'r': 1, 'e': '试用版已过期，请联系管理员'}
    if not is_module_available(module):
        return {'r': 2, 'e': '未授权，请联系管理员'}
    return {'r': 0, 'e': 'OK'}


def check_evaluation_and_expiration():
    if is_evaluation_and_expiration():
        return {'r': 1, 'e': '试用版已过期，请联系管理员'}
    return {'r': 0, 'e': 'OK'}


def get_functional_int_value(module):
    return get_license_int_value(module)
