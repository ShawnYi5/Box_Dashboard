from datetime import datetime

import django.utils.timezone as timezone
import numpy

from apiv1.models import HostSnapshot, DiskSnapshot, DiskSnapshotCDP
from apiv1.snapshot import GetSnapshotList, GetDiskSnapshot
from box_dashboard import xdatetime, xsys, xlogging


# 获取所有磁盘的ident
def query_current_disks_ident_by_host_snapshot(host_snapshot_id):
    disk_ident = set()
    disks_snapshot = HostSnapshot.objects.get(id=host_snapshot_id).disk_snapshots.all()
    for disk_snapshot in disks_snapshot:
        disk_ident.add(disk_snapshot.disk.ident)

    return list(disk_ident)


# 获取该cdp片段的：disk_cdps_image
def get_disk_cdps_image_for_slice(disk_images_all):
    _disk_images = list()
    for disk_image in reversed(disk_images_all):
        if disk_image.path.endswith('.qcow'):
            break
        _disk_images.append(disk_image)
    return _disk_images


# 判断disk_image: 是否和查询时间存在交集
def is_disk_image_available(query_start_datetime, query_end_datetime, disk_image):
    snapshotcdp = DiskSnapshotCDP.objects.get(disk_snapshot__image_path=disk_image.path)
    first_timestamp = snapshotcdp.first_timestamp  # float num
    last_timestamp = timezone.now().timestamp() if snapshotcdp.last_timestamp is None else snapshotcdp.last_timestamp
    image_first_datetime = datetime.fromtimestamp(first_timestamp)
    image_last_datetime = datetime.fromtimestamp(last_timestamp)

    if query_end_datetime < image_first_datetime or query_start_datetime > image_last_datetime:
        return False
    return True


# 过滤disks_images：滤出和查询时间存在交集的disk_image
def filter_disk_cdp_image_within_query_time(query_start_datetime, query_end_datetime, disks_images):
    _disks_images = list()
    for disk_image in disks_images:
        if is_disk_image_available(query_start_datetime, query_end_datetime, disk_image):
            _disks_images.append(disk_image)

    return _disks_images


# 每个.cdp文件的io信息总和: [line_info, line_info, line_info]
def get_cdp_images_infos(cdp_images_paths):
    images_infos = list()
    for path in cdp_images_paths:
        snapshotcdp = DiskSnapshotCDP.objects.get(disk_snapshot__image_path=path)
        is_use_flush_flag = 0 if (snapshotcdp.last_timestamp is None) else 1
        images_infos += xsys.get_cdp_file_io_info(path, is_use_flush_flag)

    return images_infos


# 一条io记录中提取数据
def extract_ioval_and_datetime_from_info_line(info_line):
    ioval = int(info_line.split(',')[1].split('0x')[-1], base=16)
    iotime = datetime.strptime(info_line.split(',')[-1].split('name:')[-1].strip(), '%Y-%m-%d-%H:%M:%S.%f')
    return ioval, iotime


# 将.cdp文件的io信息，数据结构化
def images_infos_convert_to_struct_time_ioval(images_infos):
    time_ioval = list()
    for info_line in images_infos:
        if -1 not in [info_line.find('offset'), info_line.find('len'), info_line.find('name')]:
            ioval, start_datetime = extract_ioval_and_datetime_from_info_line(info_line)
            time_ioval.append({'time': start_datetime, 'ioval': ioval})

    return time_ioval


# times_iovals，过滤出用户选定时间域的，并按时间排序
def filter_time_ioval_within_query_time(query_start_datetime, query_end_datetime, times_iovals):
    new_time_ioval = list()
    for time_ioval in times_iovals:
        if query_start_datetime <= time_ioval['time'] <= query_end_datetime:
            new_time_ioval.append(time_ioval)

    return sorted(new_time_ioval, key=lambda elem: elem['time'], reverse=False)


# 等间隔抽取元素(元素需按时间排序)
def get_constant_elems(elems_list, basic_number):
    elems_list = list(elems_list)
    elem_nums = len(elems_list)
    step = int(elem_nums / basic_number)

    if elem_nums <= basic_number:
        return elems_list
    result = list()
    for index in range(elem_nums):
        if index % step == 0:
            result.append(elems_list[index])
    return result


# 从该cdp片段开始，逆获取的disk_all_image, 不应该为空
def is_exist_chain(disk_images_all, cur_ident):
    if len(disk_images_all) == 0:
        xlogging.raise_and_logging_error(r'无法访问快照文件，请检查存储节点连接状态',
                                         r'disk_snapshot_object invalid : {}'.format(cur_ident))


# 每个time距离下一个time的值，决定score； times_iovals：需要按时间排好序
def convert_times_iovals_to_times_scores(times_iovals):
    timestamps = [elem['time'].timestamp() for elem in times_iovals]
    diffs = list(numpy.diff(timestamps))

    times_scores = list()
    for elem, score in zip(times_iovals, diffs):
        times_scores.append({'time': elem['time'], 'score': score})

    return times_scores


# 遍历有序times_iovals(需按时间排序)，查找靠近query_start_datetime的time_ioval
def get_closed_time_ioval(times_iovals, query_start_datetime):
    times_iovals = sorted(times_iovals, key=lambda elem: elem['time'], reverse=False)
    if not times_iovals:
        return None
    if query_start_datetime < times_iovals[0]['time']:
        return None
    if query_start_datetime > times_iovals[-1]['time']:
        return times_iovals[-1]

    closed_time_ioval = None
    for time_ioval in times_iovals:
        if time_ioval['time'] > query_start_datetime:
            break
        closed_time_ioval = time_ioval

    return closed_time_ioval


# disk_cdp_image object 过滤
def is_disk_cdp_image_lt_query_start_datetime(disk_cdp_image, query_start_datetime):
    snapshotcdp = DiskSnapshotCDP.objects.get(disk_snapshot__image_path=disk_cdp_image.path)
    image_first_datetime = datetime.fromtimestamp(snapshotcdp.first_timestamp)
    return image_first_datetime < query_start_datetime


def get_cdp_image_last_datetime(cdp_image):
    snapshotcdp = DiskSnapshotCDP.objects.get(disk_snapshot__image_path=cdp_image.path)
    if snapshotcdp.last_timestamp is None:
        return timezone.now()
    return datetime.fromtimestamp(snapshotcdp.last_timestamp)


# disk_cdps_image中滤出小于query_start_datetime的image
# disk_cdps_image中取最末端的image
def get_disk_cdp_image_lt_query_start_datetime(disk_cdps_image, query_start_datetime):
    new_disk_cdps_image = list()
    for disk_cdp_image in disk_cdps_image:
        if is_disk_cdp_image_lt_query_start_datetime(disk_cdp_image, query_start_datetime):
            new_disk_cdps_image.append(disk_cdp_image)

    new_disk_cdps_image = sorted(new_disk_cdps_image, key=get_cdp_image_last_datetime, reverse=False)
    return new_disk_cdps_image[-1] if new_disk_cdps_image else None


# _disks_cdps_image: [[disk_cdps_image], [disk_cdps_image], [disk_cdps_image]]
# disk_cdps_image过滤后, 取最末端的cdp_image
def get_disks_cdps_image_lt_query_start_datetime(_disks_cdps_image, query_start_datetime):
    new_disks_cdps_image = list()
    for disk_cdps_image in _disks_cdps_image:
        cdp_image = get_disk_cdp_image_lt_query_start_datetime(disk_cdps_image, query_start_datetime)
        new_disks_cdps_image.append(cdp_image) if cdp_image is not None else None

    return new_disks_cdps_image


# host_snapshot对应的times_scores
def get_times_scores_by_host_snapshot(host_snapshot_id, cdp_slice_end_time, query_start_time, query_end_time):
    query_start_datetime = datetime.strptime(query_start_time, '%Y-%m-%d %H:%M:%S.%f')
    query_end_datetime = datetime.strptime(query_end_time, '%Y-%m-%d %H:%M:%S.%f')

    disks_idents = query_current_disks_ident_by_host_snapshot(host_snapshot_id)
    host_snapshot = HostSnapshot.objects.get(id=host_snapshot_id)
    cdp_end_timestamp = xdatetime.string2datetime(cdp_slice_end_time).timestamp()
    validator_list = [GetSnapshotList.is_disk_snapshot_object_exist, GetSnapshotList.is_disk_snapshot_file_exist]

    # 该cdp片段的所有磁盘的: cdps image
    disks_cdps_image_4slice = list()
    _disks_cdps_image_4slice = list()
    for disk_ident in disks_idents:
        cur_ident, rstamp = GetDiskSnapshot.query_cdp_disk_snapshot_ident(host_snapshot, disk_ident, cdp_end_timestamp)
        if cur_ident and rstamp:
            disk_snapshot = DiskSnapshot.objects.get(ident=cur_ident)
            disk_all_image_until_slice = GetSnapshotList.query_snapshots_by_snapshot_object(disk_snapshot,
                                                                                            validator_list, rstamp)
            is_exist_chain(disk_all_image_until_slice, cur_ident)
            disk_cdps_image_4slice = get_disk_cdps_image_for_slice(disk_all_image_until_slice)
            disks_cdps_image_4slice += disk_cdps_image_4slice
            _disks_cdps_image_4slice.append(disk_cdps_image_4slice)

    # 保留与查询存在交集的: disk_cdp_image
    disks_cdps_image_within_query = filter_disk_cdp_image_within_query_time(query_start_datetime, query_end_datetime,
                                                                            disks_cdps_image_4slice)
    disks_cdps_image_path = [disk_image_obj.path for disk_image_obj in disks_cdps_image_within_query]
    disks_cdps_image_info = get_cdp_images_infos(disks_cdps_image_path)
    times_iovals = images_infos_convert_to_struct_time_ioval(disks_cdps_image_info)
    times_iovals = filter_time_ioval_within_query_time(query_start_datetime, query_end_datetime, times_iovals)
    times_scores = convert_times_iovals_to_times_scores(times_iovals)
    times_scores = get_constant_elems(times_scores, 200)

    # 在查询时间内，没有找到时间点，则往窗口左侧找，若无则启用该cdp片段的host_snapshot时间
    if len(times_scores) == 0:
        disks_cdps_image_before_query = get_disks_cdps_image_lt_query_start_datetime(_disks_cdps_image_4slice,
                                                                                     query_start_datetime)
        disks_cdps_image_path = [disk_cdp_image.path for disk_cdp_image in disks_cdps_image_before_query]
        disks_cdps_image_info = get_cdp_images_infos(disks_cdps_image_path)
        times_iovals = images_infos_convert_to_struct_time_ioval(disks_cdps_image_info)
        time_ioval = get_closed_time_ioval(times_iovals, query_start_datetime)

        closed_time = host_snapshot.start_datetime if time_ioval is None else time_ioval['time']
        times_scores = [{'time': closed_time, 'score': -1}]

    return times_scores
