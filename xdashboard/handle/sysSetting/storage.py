import json
import threading
import os
from apiv1.models import BackupTaskSchedule, HostSnapshot, UserQuota, StorageNode, DiskSnapshot, Host
from apiv1.storage_nodes import UserQuotaTools
from apiv1.runCmpr import runCmpr
from xdashboard.models import BackupDataStatt
from box_dashboard import xlogging
from apiv1.snapshot_bitmap import get_snapshot_inc_bitmap
from apiv1.snapshot import SnapshotsUsedBitMapGeneric

import IMG

_logger = xlogging.getLogger(__name__)
BIT_CNT = [bin(i).count("1") for i in range(256)]


# 指定节点中，用户的所有计划
def _get_user_plans_in_a_node(node_id, user_id):
    nodes = StorageNode.objects.filter(id=node_id)
    if not nodes:
        return []
    return BackupTaskSchedule.objects.filter(host__user__id=user_id, storage_node_ident=nodes.first().ident,
                                             deleted=False)


# 指定节点中，用户的所有计划中，是否存在Cdp计划
def is_user_has_cdp_plan_in_a_node(node_id, user_id):
    for plan in _get_user_plans_in_a_node(node_id, user_id):
        if plan.cycle_type == BackupTaskSchedule.CYCLE_CDP:
            return True
    return False


# 通过system_infos，统计当前的磁盘使用量
def _host_disks_used_mb(system_infos):
    disks_bytes = 0
    for disk in system_infos['Disk']:
        if disk['Partition']:
            for partition in disk['Partition']:
                disks_bytes += int(partition['PartitionSize']) - int(partition['FreeSize'])
    return disks_bytes / 1024 ** 2


# 指定节点中，用户的预期备份数据
def user_norm_backup_total_original_size_mb_in_node(node_id, user_id):
    user_plans_in_a_node = _get_user_plans_in_a_node(node_id, user_id)
    user_hosts_in_a_node = set([plan.host.ident for plan in user_plans_in_a_node])
    snapshots = list()
    for host_ident in user_hosts_in_a_node:
        snapshots += HostSnapshot.objects.filter(host__ident=host_ident, deleted=False, start_datetime__isnull=False,
                                                 deleting=False, is_cdp=False).exclude(successful=False)
    original_size_mb = 0
    for snapshot_obj in snapshots:
        system_infos = json.loads(snapshot_obj.ext_info).get('system_infos', None)
        if system_infos is None:
            continue
        else:
            original_size_mb += _host_disks_used_mb(system_infos)
    return round(original_size_mb)


# 指定节点中，用户的实际备份数据
def user_used_size_mb_in_a_node(node_id, user_id):
    node_base_path = UserQuotaTools.get_storage_node_base_path(node_id)
    paths_with_bp_data = UserQuotaTools.hosts_snapshots_file_path_with_bp_data(node_base_path, user_id, 'images')
    return UserQuotaTools.user_hosts_backup_size(paths_with_bp_data)


# 刷入预期备份，实际备份(MB)到数据库
def _update_user_original_and_actual_backup_size_mb_into_db(node_id, user_id):
    used_size_mb = user_used_size_mb_in_a_node(node_id, user_id)
    if is_user_has_cdp_plan_in_a_node(node_id, user_id):
        BackupDataStatt.objects.create(node_id=node_id, user_id=user_id, backup_data_mb=used_size_mb)
    else:
        original_data_mb = user_norm_backup_total_original_size_mb_in_node(node_id, user_id)
        BackupDataStatt.objects.create(
            node_id=node_id, user_id=user_id, original_data_mb=original_data_mb,
            backup_data_mb=used_size_mb if used_size_mb < original_data_mb else original_data_mb
        )


# 遍历所有有效的配额，并统计一次
def update_all_node_all_user_storage_statt():
    all_quotas = UserQuota.objects.filter(deleted=False)
    for quota in all_quotas:
        _update_user_original_and_actual_backup_size_mb_into_db(quota.storage_node_id, quota.user_id)
    return


# 过滤DiskSnapshot：未合并、指定Node
# 过滤HostSnapshot：属于用户、未删除、完成、成功
# 普通快照(.qcow DiskSnapshot)才有HostSnapshot
def user_valid_disk_snapshots_in_a_node(user_id, node_id):
    node_path = UserQuotaTools.get_storage_node_base_path(node_id)
    disk_snapshots = DiskSnapshot.objects \
        .filter(merged=False, image_path__contains=node_path) \
        .filter(host_snapshot__host__user_id=user_id, host_snapshot__deleted=False) \
        .filter(host_snapshot__finish_datetime__isnull=False, host_snapshot__successful=True).all()
    return disk_snapshots


# 该DiskSnapshot有父，且两者有相同的qcow文件(image_path)
def is_disk_snapshot_exist_parent_snapshot_and_same_qcow_image(disk_snapshot):
    parent, child = disk_snapshot.parent_snapshot, disk_snapshot
    return parent is not None and parent.image_path == child.image_path


# 分析map/dupmap文件，得到空间占用字节
def map_or_dupmap_to_bytes(snapshot, suffix_name):
    file_path = '{qcow}_{ident}.{suffix}'.format(qcow=snapshot.image_path, ident=snapshot.ident, suffix=suffix_name)
    if not os.path.exists(file_path):  # 处理情况：1.qcow没有对应的dupmap  2.文件不存在
        return 0

    total_bytes = 0
    with open(file_path, mode='rt') as fin:
        for line in fin:
            params = line.split(r':')
            if suffix_name == 'map' and len(params) == 3:
                total_bytes += int(params[2], base=10) * runCmpr.sector_size
            if suffix_name == 'dupmap' and len(params) == 4:
                total_bytes += int(params[1], base=16) - int(params[0], base=16)
    return total_bytes


# 比较子、父map文件，得到子的增量bytes
def get_child_map_inc_data_bytes_by_comparing_with_parent_map(child_map, parent_map, snapshot):
    child_map, parent_map, qcow_file = runCmpr.verify_map_qcow(child_map, parent_map, snapshot.image_path)
    if child_map is None:  # child_map=None, 子没有增量数据
        return 0

    parent_bitmap = runCmpr.generate_map_file_bitmap(parent_map, qcow_file)  # parent_map=None，生成其parent_bitmap=None
    child_priv_addrs = []
    with open(child_map, mode='rt') as fin:
        for child_line in fin:
            params = child_line.split(r':')
            if len(params) == 3:
                line_priv_addr = runCmpr.get_map1_line_particular_positions(child_line, parent_bitmap)
                child_priv_addrs += line_priv_addr
    return len(child_priv_addrs) * runCmpr.block_size


# 子snapshot相比较于父，增量的bytes
def child_inc_data_bytes_base_on_parent(child_snapshot):
    curr_snapshot = IMG.ImageSnapshotIdent(child_snapshot.image_path, child_snapshot.ident)
    prev_snapshot = IMG.ImageSnapshotIdent(child_snapshot.parent_snapshot.image_path,
                                           child_snapshot.parent_snapshot.ident)

    inc_bit_map_line_interval = get_snapshot_inc_bitmap(curr_snapshot=curr_snapshot,
                                                        prev_snapshot=prev_snapshot)
    if not inc_bit_map_line_interval:
        return 0
    else:
        return sum([i[1] for i in inc_bit_map_line_interval])

    # parent_snapshot = child_snapshot.parent_snapshot
    # parent_map = '{qcow}_{ident}.map'.format(qcow=parent_snapshot.image_path, ident=parent_snapshot.ident)
    # child_map = '{qcow}_{ident}.map'.format(qcow=child_snapshot.image_path, ident=child_snapshot.ident)
    # inc_data_bytes = get_child_map_inc_data_bytes_by_comparing_with_parent_map(child_map, parent_map, child_snapshot)
    # return inc_data_bytes


_all_result = dict()
_all_result_locker = threading.Lock()


class Result(object):

    def __init__(self, ident):
        self._event = threading.Event()
        self._ident = ident
        self._result = None

    def set_result(self, result):
        self._result = result
        self._event.set()

    def get_result(self):
        self._event.wait()
        return self._result


# 更新disk_snapshot的inc_date_bytes字段
@xlogging.convert_exception_to_value(None)
def update_disk_snapshot_inc_data(disk_snapshot):  # 有多线程并发的问题
    with _all_result_locker:
        result = _all_result.get(disk_snapshot.ident)
        if not result:
            result = Result(disk_snapshot.ident)
            w = threading.Thread(target=_get_inc_bytes, args=(disk_snapshot, result))
            w.setDaemon(True)
            w.start()
            _all_result[disk_snapshot.ident] = result

    _logger.info(
        'update_disk_snapshot_inc_data disk_snapshot:{} inc_date_bytes:{}'.format(disk_snapshot, result.get_result()))
    disk_snapshot.inc_date_bytes = result.get_result()
    disk_snapshot.save(update_fields=['inc_date_bytes'])
    with _all_result_locker:
        if _all_result.get(disk_snapshot.ident):
            del _all_result[disk_snapshot.ident]


def _get_inc_bytes(disk_snapshot, result):
    inc_bytes = -1
    try:
        flag = r'PiD{:x} BoxDashboard|get_inc_bytes {}'.format(os.getpid(),
                                                               disk_snapshot.ident)
        bit_map = SnapshotsUsedBitMapGeneric(
            [IMG.ImageSnapshotIdent(disk_snapshot.image_path, disk_snapshot.ident)],
            flag).get()
        inc_bytes = sum([BIT_CNT[x] for x in bit_map]) * 64 * 1024
    except Exception as e:
        _logger.error('_get_inc_bytes error {} {}'.format(e, disk_snapshot), exc_info=True)
    result.set_result(inc_bytes)


# 更新DiskSnapshot的inc_date_bytes：若>=0, 则表示已经更新过
def update_disk_snapshots_inc_data(disk_snapshots):
    for disk_snapshot in disk_snapshots:
        if disk_snapshot.inc_date_bytes < 0:
            update_disk_snapshot_inc_data(disk_snapshot)


# 用户在Node中，CDP文件占用空间
def user_cdp_files_bytes_in_a_node(user_id, node_id):
    node_path = UserQuotaTools.get_storage_node_base_path(node_id)
    back_data_folders = UserQuotaTools.hosts_snapshots_file_path_with_bp_data(node_path, user_id, 'images')

    cdp_files_paths = []
    for back_data_folder in back_data_folders:
        child_names = os.listdir(back_data_folder)
        cdp_files_names = list(filter(lambda name: name.endswith(r'.cdp'), child_names))
        cdps_paths = list(map(lambda name: os.path.join(back_data_folder, name), cdp_files_names))
        cdp_files_paths += cdps_paths

    total_bytes = 0
    for cdp_path in cdp_files_paths:
        total_bytes += os.path.getsize(cdp_path)

    return total_bytes


# 用户在Node中，所有有效的DiskSnapshot，求和inc_date_bytes
def user_disk_snapshots_sum_inc_data_in_a_node(disk_snapshots):
    sum_inc_data = 0
    for snapshot in disk_snapshots:
        if snapshot.inc_date_bytes <= 0:  # val>=0：表示更新过  val=-1：更新时出异常
            continue
        sum_inc_data += snapshot.inc_date_bytes
    return sum_inc_data


# 用户在Node中，RAW备份数据
def user_raw_data_bytes_in_a_node(node_id, user_id):
    disk_snapshots = user_valid_disk_snapshots_in_a_node(user_id, node_id)
    update_disk_snapshots_inc_data(disk_snapshots)
    inc_data_total = user_disk_snapshots_sum_inc_data_in_a_node(disk_snapshots)
    cdp_file_bytes = user_cdp_files_bytes_in_a_node(user_id, node_id)
    return cdp_file_bytes + inc_data_total


# 在Node中，所有的用户
def all_users_in_a_node(node_id):
    quotas_in_a_node = UserQuota.objects.filter(storage_node__id=node_id)
    users_ids = [quota.user.id for quota in quotas_in_a_node]
    return set(users_ids)


# 所有用户在Node中，RAW备份数据
def all_users_raw_data_bytes_in_a_node(node_id):
    all_users = all_users_in_a_node(node_id)
    raw_data_bytes = 0
    for user_id in all_users:
        raw_data_bytes += user_raw_data_bytes_in_a_node(node_id, user_id)
    return raw_data_bytes
