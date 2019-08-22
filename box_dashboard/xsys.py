import re

from box_dashboard import boxService, xlogging


def get_total_and_used_and_available_by_mount_point(mount_point_path):
    cmd = 'df | grep "{}" | awk {{\'print $2,$3,$4\'}}'.format(mount_point_path)
    returned_code, lines = boxService.box_service.runCmd(cmd, True)
    if returned_code != 0 or len(lines) != 1:
        xlogging.raise_and_logging_error(
            r'查询存储空间失败', r'get_total_and_used_and_available_by_mount_point failed : {} {}'.format(returned_code, lines))
    values = lines[0].split()
    return int(values[0]) * 1024, int(values[1]) * 1024, int(values[2]) * 1024


# 获取主机快照"文件夹"的大小(MB)
# /home/aio/images/hostident
def get_host_snapshots_file_size(files_dir):
    if not boxService.box_service.isFolderExist(files_dir):
        xlogging.raise_and_logging_error(r'快照路径不存在', r'{path} do not exist'.format(path=files_dir))
    cmd = 'du -ms {dir}'.format(dir=files_dir)
    returned_code, lines = boxService.box_service.runCmd(cmd, False)
    if returned_code != 0 or len(lines) != 1:
        xlogging.raise_and_logging_error(r'查询客户端快照文件夹大小失败',
                                         'get_host_snapshot_files_size failed : {} {}'.format(returned_code, lines))
    return int(lines[0].split('\t')[0].strip())


# 获取指定目录中的文件(夹)名
def get_files_name_list(parent_path):
    if not boxService.box_service.isFolderExist(parent_path):
        xlogging.raise_and_logging_error(r'文件夹不存在', r'{path} do not exist'.format(path=parent_path))

    cmd = 'dir -m {dir}'.format(dir=parent_path)
    returned_code, lines = boxService.box_service.runCmd(cmd, False)
    if returned_code != 0:
        xlogging.raise_and_logging_error(r'列出目录失败', 'get_files_list failed : {} {}'.format(returned_code, lines))
    names = ','.join(lines).split(',')
    names = [name.strip() for name in names]

    return list(filter(lambda name: name, names))


# 设置系统时间
def set_system_and_bios_time(time_str):
    cmd = 'date -s "{}"'.format(time_str)
    returned_code, lines = boxService.box_service.runCmd(cmd, False)
    if returned_code != 0:
        xlogging.raise_and_logging_error(r'设置系统时间失败', 'set sys time failed : {} {}'.format(returned_code, lines))

    cmd = 'hwclock -w'
    returned_code, lines = boxService.box_service.runCmd(cmd, False)
    if returned_code != 0:
        xlogging.raise_and_logging_error(r'设置BIOS时间失败', 'set bios time failed : {} {}'.format(returned_code, lines))


# 重启ntp服务
def restart_ntp_server():
    cmd = 'systemctl restart ntpd'
    returned_code, lines = boxService.box_service.runCmd(cmd, False)
    if returned_code != 0:
        xlogging.raise_and_logging_error(r'重启NTP失败', 'restart ntpd failed : {} {}'.format(returned_code, lines))


# 查看cdp文件的磁盘io变化
def get_cdp_file_io_info(cdp_file_path, is_use_flush_flag=0):
    cmd = '/sbin/aio/cdp_wrapper -print_info {flag} {path} {start} {size}'.format(path=cdp_file_path, start=0,
                                                                                  size=1000000000,
                                                                                  flag=is_use_flush_flag)
    returned_code, lines = boxService.box_service.runCmd(cmd, False)
    if returned_code != 0:
        xlogging.raise_and_logging_error(r'查看CDP文件失败', 'cdp_wrapper failed : {} {}'.format(returned_code, lines))
    return lines


# 创建setup_exe，返回路径
def create_setup_exe(ini_path, config_path, setup_exe_path, c_path=r'/sbin/aio/rarlinux/c.txt',
                     sfx_path=r'/sbin/aio/rarlinux/Setup.SFX', tmp_folder=r'/home/mytmp', rar=r'/home/mytmp/setup.rar',
                     exe=r'/home/mytmp/setup.exe'):
    if not boxService.box_service.isFolderExist(tmp_folder):
        boxService.box_service.makeDirs(tmp_folder)

    # 覆盖式生成：setup.rar
    cmd = r'/sbin/aio/rarlinux/rar a -w{4} -ep {0} {1} {2} {3}'.format(rar, setup_exe_path, config_path, ini_path,
                                                                       tmp_folder)
    returned_code, lines = boxService.box_service.runCmd(cmd, False)
    if returned_code != 0:
        xlogging.raise_and_logging_error(r'生成setup.rar失败',
                                         'generate setup.rar failed : {} {}'.format(returned_code, lines))
    # 为setup.rar添加注释
    cmd = r'/sbin/aio/rarlinux/rar c -w{2} -z{0} {1}'.format(c_path, rar, tmp_folder)
    returned_code, lines = boxService.box_service.runCmd(cmd, False)
    if returned_code != 0:
        xlogging.raise_and_logging_error(r'为setup.rar添加注释失败',
                                         'add comment to setup.rar failed : {} {}'.format(returned_code, lines))

    # Setup.SFX|setup.rar合并: setup.exe
    cmd = r'cat {0} {1} > {2}'.format(sfx_path, rar, exe)
    returned_code, lines = boxService.box_service.runCmd(cmd, True)
    if returned_code != 0:
        xlogging.raise_and_logging_error(r'生成setup.exe失败',
                                         'generate setup.exe failed : {} {}'.format(returned_code, lines))
    return exe


# 通过文件，获取文件系统"簇大小"，文件所在设备
def get_bsize_device_from_xfs_info(file_path):
    cmd = 'xfs_info {path}'.format(path=file_path)
    returned_code, lines = boxService.box_service.runCmd(cmd, False)
    if returned_code != 0:
        xlogging.raise_and_logging_error(r'运行命令xfs_info失败',
                                         'get_bsize_device_from_xfs_info failed : {} {}'.format(returned_code, lines))
    valid_lines = filter(lambda line: line.startswith(('meta-data', 'data')), lines)
    infos = ''.join(valid_lines)
    if 'meta-data=' not in infos or 'bsize=' not in infos:
        xlogging.raise_and_logging_error(r'xfs info返回数据结构异常', r'xfs info lost key info, {}'.format(lines))

    device = re.search(r'meta-data=.+?\s', infos).group().strip().split('=')[1]
    bsize = re.search(r'bsize=.+?\s', infos).group().strip().split('=')[1]

    sdxn = device.split(r'/')[2]
    sdx = re.split(r'\d+', sdxn)[0]
    device = r'/sys/block/{0}/stat'.format(sdx)

    return bsize, device
