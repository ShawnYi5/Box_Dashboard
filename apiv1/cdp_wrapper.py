import datetime
import json
import re

if __name__ == '__main__':
    import os
    import sys

    sys.path.append('../')
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "box_dashboard.settings")
    import django

    django.setup()

from box_dashboard import xlogging
from apiv1.models import DiskSnapshot
from apiv1.models import HostSnapshot
from xdashboard.handle.sysSetting import cdpFileIO
from box_dashboard import boxService
from box_dashboard import xdatetime

_logger = xlogging.getLogger(__name__)


def find_idle_time(cdp_file_list):
    """
    :param cdp_file_list: [['/mnt/6f68195854df42cca6da5de441ef259d.cdp',],]
    :return: [{'start': datetime.datetime(2019, 6, 10, 12, 14, 30, 379627), 'end': datetime.datetime(2019, 6, 10, 12, 14, 31, 380192)},]
    """
    idle_time_list = list()
    cdp_wrapper_path = r'/sbin/aio/cdp_wrapper'
    cdp_file_lists = list()
    for cdp_file in cdp_file_list:
        cdp_file_lists.append(' '.join(cdp_file))
    cdp_files = ' chain '.join(cdp_file_lists)
    cmd = '{cdp_wrapper_path} -find_idle_time {cdp_files}'.format(cdp_wrapper_path=cdp_wrapper_path,
                                                                  cdp_files=cdp_files)
    _logger.info('find_idle_time cmd={}'.format(cmd))
    returncode, lines = boxService.box_service.runCmd(cmd, False)
    _logger.info('find_idle_time runCmd lines={}'.format(lines[-10:]))
    if returncode != 0:
        _logger.info('cdp_wrapper Failed.returncode={} ignore.'.format(returncode))
    pattern = re.compile(r'find a time:   client time: ([\d\.\-:]+) -  client time: ([\d\.\-:]+) , [\d\.]+', re.I)
    for line in lines:
        m = pattern.match(line.strip())
        if m:
            start_time = datetime.datetime.strptime(m.group(1), '%Y-%m-%d-%H:%M:%S.%f')
            end_time = datetime.datetime.strptime(m.group(2), '%Y-%m-%d-%H:%M:%S.%f')
            idle_time_list.append({'start': start_time, 'end': end_time})
    return idle_time_list


def find_cdp_obj_index(disk_snapshot_objs, chk_stamp):
    for index, disk_snapshot_obj in enumerate(disk_snapshot_objs):
        if disk_snapshot_obj.cdp_info.first_timestamp > chk_stamp:
            return max(index - 1, 0)
    return max(len(disk_snapshot_objs) - 1, 0)


def get_cluster_cdp_file_list(host_snapshot_id, cdp_time_point):
    """
    :param host_snapshot_id
    :param cdp_time_point: datetime obj
    :return:只返回集群盘的cdp文件[['/mnt/6f68195854df42cca6da5de441ef259d.cdp',],]
    """

    def _find_cluster_disk_snapshot_objs(_cluster_disk_snapshot_objs):
        host_snapshot_obj = HostSnapshot.objects.get(id=host_snapshot_id)
        cluster_schedule_ext_config = json.loads(host_snapshot_obj.cluster_schedule.ext_config)
        host_snapshot_ext_info = json.loads(host_snapshot_obj.ext_info)

        for cluster_disk in cluster_schedule_ext_config['cluster_disks']:
            if len(cluster_disk['map_disks']) < 2:
                _logger.error(r'get_cluster_cdp_file_list some mistake len(map_disks) < 2 : {}'.format(cluster_disk))
                continue
            for include_range in host_snapshot_ext_info['include_ranges']:
                for map_disk in cluster_disk['map_disks']:
                    if include_range['diskNativeGUID'] == map_disk['disk_guid']:
                        disk_snapshot_ident = include_range['diskSnapshot']
                        _cluster_disk_snapshot_objs.append(DiskSnapshot.objects.get(ident=disk_snapshot_ident))
        _cluster_disk_snapshot_objs = list(set(_cluster_disk_snapshot_objs))

    cluster_disk_snapshot_objs = list()
    _find_cluster_disk_snapshot_objs(cluster_disk_snapshot_objs)

    def _find_cdp_children(_disk_snapshot, _find_cache, _timestamp):
        for snapshot_obj in _disk_snapshot.child_snapshots.all():
            if not snapshot_obj.is_cdp:
                continue
            if (_disk_snapshot.is_cdp
                    and _disk_snapshot.cdp_info.first_timestamp > _timestamp
                    and snapshot_obj.cdp_info.first_timestamp > _timestamp):
                continue
            _find_cache.append(snapshot_obj)

    result = list()
    timestamp = cdp_time_point.timestamp()

    for disk_snapshot in cluster_disk_snapshot_objs:
        cdp_disk_snapshot_objs = list()
        find_cache = list()

        _find_cdp_children(disk_snapshot, find_cache, timestamp)

        while find_cache:
            cdp_disk_snapshot = find_cache.pop(0)
            cdp_disk_snapshot_objs.append(cdp_disk_snapshot)
            _find_cdp_children(cdp_disk_snapshot, find_cache, timestamp)

        if not cdp_disk_snapshot_objs:
            continue

        cdp_obj_index = find_cdp_obj_index(cdp_disk_snapshot_objs, timestamp)
        result.append([
            o.image_path for o in cdp_disk_snapshot_objs[max(cdp_obj_index - 2, 0):cdp_obj_index + 2]
        ])

    return result


# times_iovals，过滤出用户选定时间域的，并按时间排序
def _filter_time_ioval_within_query_time(idle_time_list, times_iovals):
    new_time_ioval = list()
    for time_ioval in times_iovals:
        for idle_time in idle_time_list:
            if idle_time['start'] <= time_ioval['time'] <= idle_time['end']:
                new_time_ioval.append(time_ioval)
                break

    return sorted(new_time_ioval, key=lambda elem: elem['time'], reverse=False)


def _filter_idle_time(query_start_datetime, query_end_datetime, idle_time_list):
    filter_idle_time_list = list()
    for idle_time in idle_time_list:
        if query_start_datetime <= idle_time['start'] <= query_end_datetime or query_start_datetime <= idle_time[
            'end'] <= query_end_datetime:
            filter_idle_time_list.append(idle_time)
    return filter_idle_time_list


def get_cluster_io_daychart(host_snapshot_id, cdp_slice_end_time, centre_time, window_secs):
    half_window_secs = window_secs / 2
    query_start_datetime = centre_time - datetime.timedelta(seconds=half_window_secs)
    query_end_datetime = centre_time + datetime.timedelta(seconds=half_window_secs)

    cdp_file_list = get_cluster_cdp_file_list(host_snapshot_id, centre_time)
    cdp_file_lists = list()
    for cdp_files in cdp_file_list:
        for cdp_file in cdp_files:
            cdp_file_lists.append(cdp_file)
    disks_cdps_image_info = cdpFileIO.get_cdp_images_infos(cdp_file_lists)
    times_iovals = cdpFileIO.images_infos_convert_to_struct_time_ioval(disks_cdps_image_info)
    idle_time_list = find_idle_time(cdp_file_list)
    idle_time_list = _filter_idle_time(query_start_datetime, query_end_datetime, idle_time_list)
    times_iovals = cdpFileIO.filter_time_ioval_within_query_time(query_start_datetime, query_end_datetime, times_iovals)
    times_iovals = _filter_time_ioval_within_query_time(idle_time_list, times_iovals)
    times_scores = cdpFileIO.convert_times_iovals_to_times_scores(times_iovals)
    times_scores = cdpFileIO.get_constant_elems(times_scores, 200)
    if len(times_scores) == 0:
        times_scores = [{'time': query_start_datetime, 'score': -1}]
    _list = [{'time': str(time_score['time']), 'score': time_score['score']} for time_score in times_scores]
    return _list


def fix_restore_time(host_snapshot_id, restore_time):
    if isinstance(restore_time, str):
        fix_restore_time_str = restore_time
        cdp_time_point = xdatetime.string2datetime(restore_time)
    else:
        fix_restore_time_str = restore_time.strftime(xdatetime.FORMAT_WITH_MICROSECOND)
        cdp_time_point = restore_time

    idle_time_list = find_idle_time(
        get_cluster_cdp_file_list(host_snapshot_id, cdp_time_point))
    if not idle_time_list:
        _logger.info('fix_restore_time idle_time_list is empty.Failed.ignore.')
        return False, fix_restore_time_str

    for idle_time in reversed(idle_time_list):
        if idle_time['start'] <= cdp_time_point <= idle_time['end']:
            _logger.info('datetime {} in safe range {}'.format(cdp_time_point, idle_time))  # 不需要修正
            return False, fix_restore_time_str
        if idle_time['start'] <= cdp_time_point:
            _logger.info('fix_restore_time {} -> {} end'.format(cdp_time_point, idle_time))
            return True, idle_time['end'].strftime(xdatetime.FORMAT_WITH_MICROSECOND)
    else:
        _logger.error(r'fix_restore_time can NOT find valid {} {}'.format(cdp_time_point, idle_time_list[0]))
        return False, fix_restore_time_str


if __name__ == '__main__':
    host_snapshot_id = 639
    cdp_slice_end_time = '2019-06-18T05:22:38.370910'
    centre_time = xdatetime.string2datetime('2019-06-18T01:50:00.214514')
    window_secs = 20
    _list = get_cluster_io_daychart(host_snapshot_id, cdp_slice_end_time, centre_time, window_secs)
    _logger.info('list={}'.format(_list))
    # bNeedFix, fix_restore_time = fix_restore_time(3, '2019-06-10T16:56:52.093974')
    # _logger.info('bNeedFix={},fix_restore_time={}'.format(bNeedFix, fix_restore_time))
    # pointid = 'cdp|3|2019-06-10T13:45:01.025442|2019-06-10T16:56:52.093974'
    # score_list = get_cluster_io_daychart(3, '2019-06-10T16:56:52.093974', 1560157012, 20)
    # _logger.info(score_list)
    # cdp_file_list = get_cluster_cdp_file_list(host_snapshot_id, centre_time)
    # _logger.info('cdp_file_list={}'.format(cdp_file_list))
    # get_cdp_times_scores_by_host_snapshot(pointid)
