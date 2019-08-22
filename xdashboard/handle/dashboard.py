import json
import time
import copy
import json
import datetime
from django.http import HttpResponse
from box_dashboard import functions
from box_dashboard import xlogging
from apiv1.models import BackupTaskSchedule
from apiv1.models import Host
from apiv1.models import UserQuota
from apiv1.models import HostSnapshot
from .bussinessreport import _get_node_special_host_usage
from apiv1.storage_nodes import UserQuotaTools
from xdashboard.handle.sysSetting import storage
from copy import deepcopy
from box_dashboard import xdatetime
from apiv1.views import HostSnapshotsWithCdpPerHost
from apiv1.views import get_response_error_string
from rest_framework import status
from apiv1.models import RestoreTask
from apiv1.models import HTBTask
from apiv1.models import CDPTask
from apiv1.models import MigrateTask
from apiv1.models import BackupTask
from apiv1.models import AutoVerifyTask
from dateutil.relativedelta import relativedelta

router = functions.Router(globals())
_logger = xlogging.getLogger(__name__)

HOST_SNAPSHOT_COUNT_TYPE_RUNNING = 'r'
HOST_SNAPSHOT_COUNT_TYPE_SUCCESSFUL = 's'


def _fmt_host_type(type):
    if type == 'none':
        return '无备份'
    if type == 'interval':
        return '定时备份'
    if type == 'manual':
        return '手动备份'
    if type == 'continuous':
        return '持续备份'
    if type == 'offline':
        return '离线'
    if type == 'online':
        return '在线'
    return type


def _get_normal_color(type):
    if type == 'none':
        return '#e55e6a'
    if type == 'interval':
        return '#4bac3e'
    if type == 'manual':
        return '#5fd44f'
    if type == 'continuous':
        return '#008f18'
    if type == 'offline':
        return '#ecde02'
    if type == 'online':
        return '#96ca4c'
    return '#333'


HOST_WITH_BACKUP_SCHEDULE_SUB_ONLINE = 'online'  # 在线
HOST_WITH_BACKUP_SCHEDULE_SUB_OFFLINE = 'offline'  # 离线
HOST_WITH_BACKUP_SCHEDULE_INTERVAL = 'interval'  # 定时备份
HOST_WITH_BACKUP_SCHEDULE_CONTINUOUS = 'continuous'  # 持续备份
HOST_WITH_BACKUP_SCHEDULE_MANUAL = 'manual'  # 手动备份
HOST_WITH_BACKUP_SCHEDULE_NONE = 'none'  # 没有备份计划


def query_host_with_backup_schedule_count(user_id):
    """
    查询 计算机与备份计划 的统计
    :param user_id:
        None 为所有用户
    :return:
        [
            {
                "filter_ident": "str",
                "type": "interval"，# HOST_WITH_BACKUP_SCHEDULE_
                "count": int,
                "sub": [
                    {
                        "filter_ident": "str",
                        "type": "online"， # HOST_WITH_BACKUP_SCHEDULE_SUB_
                        "count": int,
                    }
                ]
            },
            ...
        ]
    """
    result = list()
    interval_on_line_host_id_list = list()
    interval_off_line_host_id_list = list()

    continuous_on_line_host_id_list = list()
    continuous_off_line_host_id_list = list()

    manual_on_line_host_id_list = list()
    manual_off_line_host_id_list = list()

    schedules = BackupTaskSchedule.objects.filter(deleted=False,
                                                  cycle_type=BackupTaskSchedule.CYCLE_PERDAY).select_related('host')
    if user_id:
        schedules = schedules.filter(host__user_id=user_id)

    for schedule in schedules:
        if schedule.host.is_linked:
            interval_on_line_host_id_list.append(schedule.host.id)
        else:
            interval_off_line_host_id_list.append(schedule.host.id)

    interval_on_line_host_id_list = list(set(interval_on_line_host_id_list))
    interval_off_line_host_id_list = list(set(interval_off_line_host_id_list))

    interval_offline_objs_count = len(interval_off_line_host_id_list)
    interval_online_objs_count = len(interval_on_line_host_id_list)
    interval_objs_count = interval_online_objs_count + interval_offline_objs_count

    interval_offline_objs_filter_ident = json.dumps({'display': '定时备份离线的客户端', 'ids': interval_off_line_host_id_list},
                                                    ensure_ascii=False)

    interval_online_objs_filter_ident = json.dumps({'display': '定时备份在线的客户端', 'ids': interval_on_line_host_id_list},
                                                   ensure_ascii=False)

    interval_objs_filter_ident = json.dumps(
        {'display': '定时备份的客户端', 'ids': interval_on_line_host_id_list + interval_off_line_host_id_list},
        ensure_ascii=False)

    _logger.info(
        'interval_on_line_host_id_list={},interval_off_line_host_id_list={}'.format(interval_on_line_host_id_list,
                                                                                    interval_off_line_host_id_list))

    result.append({"filter_ident": interval_objs_filter_ident, "type": HOST_WITH_BACKUP_SCHEDULE_INTERVAL,
                   "count": interval_objs_count,
                   "sub": [{"filter_ident": interval_online_objs_filter_ident,
                            "type": HOST_WITH_BACKUP_SCHEDULE_SUB_ONLINE,
                            "count": interval_online_objs_count, },
                           {"filter_ident": interval_offline_objs_filter_ident,
                            "type": HOST_WITH_BACKUP_SCHEDULE_SUB_OFFLINE,
                            "count": interval_offline_objs_count, }
                           ]
                   })

    schedules = BackupTaskSchedule.objects.filter(deleted=False,
                                                  cycle_type=BackupTaskSchedule.CYCLE_CDP).select_related('host')
    if user_id:
        schedules = schedules.filter(host__user_id=user_id)

    for schedule in schedules:
        if schedule.host.is_linked:
            continuous_on_line_host_id_list.append(schedule.host.id)
        else:
            continuous_off_line_host_id_list.append(schedule.host.id)

    continuous_on_line_host_id_list = list(set(continuous_on_line_host_id_list))
    continuous_off_line_host_id_list = list(set(continuous_off_line_host_id_list))

    continuous_online_objs_count = len(continuous_on_line_host_id_list)
    continuous_offline_objs_count = len(continuous_off_line_host_id_list)
    continuous_objs_count = continuous_online_objs_count + continuous_offline_objs_count

    continuous_objs_filter_ident = json.dumps(
        {'display': '持续备份的客户端', 'ids': continuous_on_line_host_id_list + continuous_off_line_host_id_list},
        ensure_ascii=False)

    continuous_online_objs_filter_ident = json.dumps({'display': '持续备份在线的客户端', 'ids': continuous_on_line_host_id_list},
                                                     ensure_ascii=False)

    continuous_offline_objs_filter_ident = json.dumps({'display': '持续备份离线的客户端', 'ids': continuous_offline_objs_count},
                                                      ensure_ascii=False)

    result.append({"filter_ident": continuous_objs_filter_ident, "type": HOST_WITH_BACKUP_SCHEDULE_CONTINUOUS,
                   "count": continuous_objs_count,
                   "sub": [{"filter_ident": continuous_online_objs_filter_ident,
                            "type": HOST_WITH_BACKUP_SCHEDULE_SUB_ONLINE,
                            "count": continuous_online_objs_count, },
                           {"filter_ident": continuous_offline_objs_filter_ident,
                            "type": HOST_WITH_BACKUP_SCHEDULE_SUB_OFFLINE,
                            "count": continuous_offline_objs_count, }
                           ]
                   })

    schedules = BackupTaskSchedule.objects.filter(deleted=False,
                                                  cycle_type=BackupTaskSchedule.CYCLE_ONCE).select_related('host')
    if user_id:
        schedules = schedules.filter(host__user_id=user_id)

    for schedule in schedules:
        if schedule.host.is_linked:
            manual_on_line_host_id_list.append(schedule.host.id)
        else:
            manual_off_line_host_id_list.append(schedule.host.id)

    manual_on_line_host_id_list = list(set(manual_on_line_host_id_list))
    manual_off_line_host_id_list = list(set(manual_off_line_host_id_list))

    _logger.info('manual_on_line_host_id_list={},manual_off_line_host_id_list={}'.format(manual_on_line_host_id_list,
                                                                                         manual_off_line_host_id_list))

    manual_online_objs_count = len(manual_on_line_host_id_list)
    manual_offline_objs_count = len(manual_off_line_host_id_list)
    manual_objs_count = manual_online_objs_count + manual_offline_objs_count

    manual_objs_filter_ident = json.dumps(
        {'display': '手动备份的客户端', 'ids': manual_on_line_host_id_list + manual_off_line_host_id_list},
        ensure_ascii=False)

    manual_online_objs_filter_ident = json.dumps({'display': '手动备份在线的客户端', 'ids': manual_on_line_host_id_list},
                                                 ensure_ascii=False)

    manual_offline_objs_filter_ident = json.dumps({'display': '手动备份离线的客户端', 'ids': manual_off_line_host_id_list},
                                                  ensure_ascii=False)

    result.append({"filter_ident": manual_objs_filter_ident, "type": HOST_WITH_BACKUP_SCHEDULE_MANUAL,
                   "count": manual_objs_count,
                   "sub": [{"filter_ident": manual_online_objs_filter_ident,
                            "type": HOST_WITH_BACKUP_SCHEDULE_SUB_ONLINE,
                            "count": manual_online_objs_count, },
                           {"filter_ident": manual_offline_objs_filter_ident,
                            "type": HOST_WITH_BACKUP_SCHEDULE_SUB_OFFLINE,
                            "count": manual_offline_objs_count, }
                           ]
                   })

    hosts = Host.objects.all()
    if user_id:
        hosts = hosts.filter(user_id=user_id)
    hosts = list(filter(lambda x: not x.is_deleted, hosts))

    all_host_ids = list()
    for host in hosts:
        all_host_ids.append(host.id)

    all_host_id_list = list()
    all_host_id_list.extend(interval_on_line_host_id_list)
    all_host_id_list.extend(interval_off_line_host_id_list)
    all_host_id_list.extend(continuous_on_line_host_id_list)
    all_host_id_list.extend(continuous_off_line_host_id_list)
    all_host_id_list.extend(manual_on_line_host_id_list)
    all_host_id_list.extend(manual_off_line_host_id_list)
    none_objs_count = len(hosts) - len(list(set(all_host_id_list)))

    none_objs_filter_ident = json.dumps({'display': '无备份离线客户端', 'ids': list(set(all_host_ids) - set(all_host_id_list))},
                                        ensure_ascii=False)
    result.append({"filter_ident": none_objs_filter_ident, "type": HOST_WITH_BACKUP_SCHEDULE_NONE,
                   "count": none_objs_count, })

    return result


def host_overview(request):
    # 客户端与备份计划总览
    is_debug = request.GET.get('debug')
    ret = {'r': 0, 'e': '操作成功'}
    host_list = query_host_with_backup_schedule_count(request.user.id)
    if is_debug:
        ret['debug'] = host_list
    series = list()
    rough_data = list()
    detail_data = list()
    for host in host_list:
        if host['count']:
            rough_data.append(
                {"value": host['count'], "name": _fmt_host_type(host['type']),
                 "normal_color": _get_normal_color(host['type']), "filter_uuid": host['filter_ident']})
        if host.get('sub') is None:
            if host['count']:
                detail_data.append(
                    {"value": host['count'], "name": _fmt_host_type(host['type']),
                     "normal_color": _get_normal_color(host['type']), "filter_uuid": host['filter_ident']})
            continue
        for sub_host in host['sub']:
            if sub_host['count']:
                detail_data.append(
                    {"value": sub_host['count'], "name": _fmt_host_type(sub_host['type']),
                     "normal_color": _get_normal_color(sub_host['type']),
                     "filter_uuid": sub_host['filter_ident']})
    series.append({"rough_data": rough_data})
    series.append({"detail_data": detail_data})
    ret['series'] = series
    if len(detail_data) == 0:
        ret = {'r': 1, 'e': '暂无数据'}
    return HttpResponse(json.dumps(ret, ensure_ascii=False))


def _get_storage_overview(user_id):
    host_idents = list()
    host_use_info_list = list()

    hosts = Host.objects.filter(user_id=user_id)
    hosts = list(filter(lambda x: not x.is_deleted, hosts))
    for host in hosts:
        host_idents.append(host.ident)

    user_quotas = UserQuota.objects.filter(user_id=user_id, deleted=False)
    for quota_obj in user_quotas:
        node_obj = quota_obj.storage_node
        host_use_info = _get_node_special_host_usage(node_obj.path, host_idents)
        host_use_info_list.append(host_use_info)

    storage_usage_dict = dict()
    for host_ident in host_idents:
        storage_usage_dict[host_ident] = 0
        for host_use_info in host_use_info_list:
            storage_usage_dict[host_ident] = storage_usage_dict[host_ident] + host_use_info.get(host_ident, 0)

    storage_usage_list = sorted(storage_usage_dict.items(), key=lambda d: d[1], reverse=True)

    data = list()
    for storage_usage in storage_usage_list:
        host_ident = storage_usage[0]
        mega_bytes = storage_usage[1]
        host = Host.objects.get(ident=host_ident)
        display = host.name
        filter_ident = 'host_storage_overview'
        if not mega_bytes:
            continue
        value = float('{0:.2f}'.format(mega_bytes / 1024 ** 3))
        data.append({'filter_uuid': filter_ident, 'name': display, 'value': value})

    return data


def host_storage_overview(request):
    # 空间占用总览
    ret = {'r': 0, 'e': '操作成功'}
    data = _get_storage_overview(request.user.id)
    series = list()
    series.append({"data": data})
    ret['series'] = series
    if len(data) == 0:
        ret = {'r': 1, 'e': '暂无数据'}
    return HttpResponse(json.dumps(ret, ensure_ascii=False))


def _query_storage_usage_and_quota(user_id):
    usage_and_quota = {'used_bytes': 0, 'free_bytes': 0}
    user_quotas = UserQuota.objects.filter(user_id=user_id, deleted=False)
    for quota_obj in user_quotas:
        node_obj = quota_obj.storage_node
        node_tools = UserQuotaTools(node_obj.id, user_id, quota_obj.quota_size)
        try:
            free_bytes = node_tools.get_user_available_storage_size_in_node(True) * 1024 ** 2
            used_bytes = storage.user_used_size_mb_in_a_node(node_obj.id, user_id) * 1024 ** 2
            usage_and_quota['used_bytes'] = usage_and_quota['used_bytes'] + used_bytes
            usage_and_quota['free_bytes'] = usage_and_quota['free_bytes'] + free_bytes
        except Exception:
            _logger.info('_query_storage_usage_and_quota Failed.ignore')
    return usage_and_quota


def user_storage_usage_and_quota_overview(request):
    # 存储配额空间使用总览
    ret = {'r': 0, 'e': '操作成功'}
    usage_and_quota = _query_storage_usage_and_quota(request.user.id)
    quota_bytes = usage_and_quota['used_bytes'] + usage_and_quota['free_bytes']
    used_bytes = usage_and_quota['used_bytes']
    series = list()
    data = dict()
    data['node_name'] = ''
    data['total'] = float('{0:.2f}'.format(quota_bytes / 1024 ** 3))
    data['used'] = float('{0:.2f}'.format(used_bytes / 1024 ** 3))
    series.append(data)
    ret['series'] = series

    return HttpResponse(json.dumps(ret, ensure_ascii=False))


def _ftm_time(timestamp):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))


def _get_axis_pos(begin_timestamp, curr_timestamp, col_count):
    for i in range(col_count, -1, -1):
        if curr_timestamp >= begin_timestamp + 2 * 60 * 60 * i:
            return i
    return 0


def _calc_host_snapshot_count(agent_host_obj, begin_datetime, ranges):
    host_snapshot_objs = HostSnapshot.objects.filter(host_id=agent_host_obj.id, deleted=False, successful=True,
                                                     is_cdp=False).exclude(
        start_datetime__lt=begin_datetime).order_by('start_datetime')
    for host_snapshot_obj in host_snapshot_objs:
        start_datetime = host_snapshot_obj.start_datetime
        if start_datetime is None:
            continue
        timestamp = start_datetime.timestamp()
        for r in ranges:
            if r['begin'] <= timestamp < r['end']:
                r['data'][0]['count'] += 1
                break

    return HostSnapshot.objects.filter(host_id=agent_host_obj.id, finish_datetime=None).exists()


def _query_agent_host_with_schedule_type(user_id, cycle_type):
    schedules = BackupTaskSchedule.objects.filter(deleted=False,
                                                  cycle_type=cycle_type).select_related('host')
    if user_id:
        schedules = schedules.filter(host__user_id=user_id)

    agent_host_objs = list()
    for schedule in schedules:
        agent_host_objs.append(schedule.host)
    return agent_host_objs


def _query_host_snapshot_count(user_id, minutes=1440, minutes_per_range=120):
    """
    查询 普通快照点 的数量
    :param tenants:
        None 为所有租户
        否则为 ["tenant_uuid", "tenant_uuid", ]
    :param minutes:
        统计时间范围
    :param minutes_per_range:
        时间区域的分割参数
    :return:
        {
            "begin_timestamp": float,
            "end_timestamp": float,
            "ranges": [
                {
                    "begin": float,
                    "end": float,
                    "data": [
                                {
                                    "type": HOST_SNAPSHOT_COUNT_TYPE_xxx,
                                    "count": int,
                                },
                                ...
                            ]

                }
            ]
        }

    """
    assert (minutes % 1440) == 0
    assert (minutes_per_range % 60) == 0
    assert (minutes % minutes_per_range) == 0

    now_datetime = datetime.datetime.now()
    now_time = now_datetime.time()
    now_time_with_aligned = datetime.time(
        hour=(((now_time.hour * 60 + now_time.minute + 1) // minutes_per_range) * minutes_per_range) // 60)
    now_datetime_with_aligned = datetime.datetime.combine(now_datetime.date(), now_time_with_aligned)
    begin_datetime = now_datetime_with_aligned - datetime.timedelta(minutes=minutes)

    result = {
        'begin_timestamp': begin_datetime.timestamp(), 'end_timestamp': now_datetime.timestamp(), 'ranges': list()}

    for r in range(minutes // minutes_per_range):
        begin = result['begin_timestamp'] + (r * minutes_per_range * 60)
        end = begin + (minutes_per_range * 60)
        result['ranges'].append({"begin": begin, "end": end, "data": [
            {"type": HOST_SNAPSHOT_COUNT_TYPE_SUCCESSFUL, "count": 0, },
        ]})
    result['ranges'].append({"begin": now_datetime_with_aligned.timestamp(), "end": result['end_timestamp'], "data": [
        {"type": HOST_SNAPSHOT_COUNT_TYPE_SUCCESSFUL, "count": 0, },
    ]})

    running_count = 0
    agent_host_objs = _query_agent_host_with_schedule_type(user_id, BackupTaskSchedule.CYCLE_PERDAY)
    for agent_host_obj in agent_host_objs:
        is_running = _calc_host_snapshot_count(agent_host_obj, begin_datetime, result['ranges'])
        if is_running:
            running_count += 1
    result['ranges'][-1]['data'].append({"type": HOST_SNAPSHOT_COUNT_TYPE_RUNNING, "count": running_count, }, )

    return result


def backup_inc_overview(request):
    # 最近24小时定时备份生成统计
    is_debug = request.GET.get('debug')
    ret = {'r': 0, 'e': '操作成功'}
    host_snapshot_count = _query_host_snapshot_count(request.user.id)
    xAxis = list()
    yAxis = list()
    series = list()

    item_count = len(host_snapshot_count['ranges'])
    r_data = [0 for _ in range(item_count)]
    s_data = [0 for _ in range(item_count)]

    begin_timestamp = host_snapshot_count['begin_timestamp']
    end_timestamp = host_snapshot_count['end_timestamp']

    for i in range(item_count - 1):
        xAxis.append(_ftm_time(begin_timestamp + 2 * 60 * 60 * (i + 1))[0:16])
    xAxis.append(_ftm_time(end_timestamp)[0:16])

    r_info = {"name": "正在生成的备份点", "data": r_data}
    s_info = {"name": "已生成的备份点", "data": s_data}

    ret['begin_time'] = _ftm_time(begin_timestamp)

    if is_debug:
        ret['debug_org'] = host_snapshot_count
        ret['debug_human'] = copy.deepcopy(host_snapshot_count)
        ret['debug_human']['begin_timestamp'] = _ftm_time(ret['debug_human']['begin_timestamp'])
        ret['debug_human']['end_timestamp'] = _ftm_time(ret['debug_human']['end_timestamp'])
        for host in ret['debug_human']['ranges']:
            host['begin'] = _ftm_time(host['begin'])
            host['end'] = _ftm_time(host['end'])

    host_snapshot_ranges = host_snapshot_count['ranges']
    for host in host_snapshot_ranges:
        begin = host['begin']
        ranges_data_list = host['data']
        for ranges_data in ranges_data_list:
            type = ranges_data['type']
            count = ranges_data['count']
            ipos = _get_axis_pos(host_snapshot_count['begin_timestamp'], begin, item_count)
            if type == HOST_SNAPSHOT_COUNT_TYPE_RUNNING:
                r_info['data'][ipos] = count
            elif type == HOST_SNAPSHOT_COUNT_TYPE_SUCCESSFUL:
                s_info['data'][ipos] = count

    iYMax = 0
    for i in range(len(r_data)):
        iYMaxTmp = r_data[i] + s_data[i]
        if iYMaxTmp > iYMax:
            iYMax = iYMaxTmp
    for i in range(iYMax):
        yAxis.append(i)

    series.append(s_info)
    series.append(r_info)
    ret['series'] = series
    ret['xAxis'] = xAxis
    ret['splitNumber'] = iYMax
    return HttpResponse(json.dumps(ret, ensure_ascii=False))


def _fmt_cpd_health_display(value):
    if value == -1:
        return '低于94%的时间段有CDP备份数据'
    elif value:
        return '{}%的时间段有CDP备份数据'.format(value)
    return '未知（{}）'.format(value)


def _get_cdp_health_color(value):
    if value == -1:
        return '#e55e6a'
    return None


def _query_host_cdp_snapshot_current_health_init_result(health_level, minutes, tenants, begin_timestamp, end_timestamp):
    result = {"begin_timestamp": begin_timestamp, "end_timestamp": end_timestamp}

    if not health_level:
        health_level = [
            {"value": 98, "display": "高"},  # 24小时中有大约28分钟无CDP数据
            {"value": 96, "display": "中"},  # 24小时中有大约56分钟无CDP数据
            {"value": 94, "display": "低"},  # 24小时中有大约112分钟无CDP数据
            {"value": -1, "display": "警告"},
        ]

    result['data'] = deepcopy(health_level)

    for r in result['data']:
        query_params = dict()
        query_params['all_level'] = deepcopy(health_level)
        query_params['current_level'] = deepcopy(r)
        query_params['type'] = 'query_host_cdp_snapshot_current_health'
        query_params['tenants'] = tenants
        query_params['minutes'] = minutes
        r['filter_ident'] = {'display': _fmt_cpd_health_display(r['value']), 'ids': list()}
        r['count'] = 0

    return result


def _get_host_cdp_snapshot_current_valid_rang(agent_host_obj, begin_datetime, end_timestamp):
    host_item = {'host_ident': agent_host_obj.ident,
                 'host_id': agent_host_obj.id,
                 'host_display_name': agent_host_obj.display_name, 'range': list()}

    api_request = {'begin': begin_datetime.strftime(xdatetime.FORMAT_ONLY_DATE),
                   'end': end_timestamp.strftime(xdatetime.FORMAT_ONLY_DATE),
                   'use_serializer': False}

    api_response = HostSnapshotsWithCdpPerHost().get(None, ident=agent_host_obj.ident, api_request=api_request)

    if not status.is_success(api_response.status_code):
        e = get_response_error_string(api_response)
        debug = "HostSnapshotsWithCdpPerHost().get(begin:{} end:{} ident:{}) failed {}".format(begin_datetime,
                                                                                               end_timestamp,
                                                                                               agent_host_obj.ident,
                                                                                               api_response.status_code)
        _logger.error('_get_host_cdp_snapshot_current_valid_rang Failed.e={},debug={}'.format(e, debug))
        return host_item

    for host_snapshot in api_response.data:
        host_item['range'].append({'begin': xdatetime.string2datetime(host_snapshot['begin']).timestamp(),
                                   'end': xdatetime.string2datetime(host_snapshot['end']).timestamp()})
    return host_item


def _query_host_cdp_snapshot_current_valid_range(tenants=None, minutes=1440):
    """
    查询 CDP备份 的有效区域统计
    :param tenants:
        None 为所有租户
        否则为 ["tenant_uuid", "tenant_uuid", ]
    :param minutes:
        从 minutes 前开始查询数据
    :return:
        {
            "begin_timestamp": float,
            "end_timestamp": float,
            "data": [
                        {
                            "host_ident": "str",
                            "host_display_name": "str",
                            "range": [
                                {
                                    "begin": float,
                                    "end": float,
                                },
                            ]
                        },
                        ...
                    ]
        }
    """
    agent_host_objs = _query_agent_host_with_schedule_type(tenants, BackupTaskSchedule.CYCLE_CDP)

    now_datetime = datetime.datetime.now()
    begin_datetime = now_datetime - datetime.timedelta(minutes=minutes)

    result = {'begin_timestamp': begin_datetime.timestamp(), 'end_timestamp': now_datetime.timestamp(), 'data': list()}
    for agent_host_obj in agent_host_objs:
        host_item = _get_host_cdp_snapshot_current_valid_rang(agent_host_obj, begin_datetime,
                                                              now_datetime + datetime.timedelta(days=1))
        result['data'].append(host_item)

    return result


def _query_host_cdp_snapshot_current_health(tenants=None, minutes=1440, health_level=None,
                                            host_ident_array=None, current_level=None):
    """
    查询 CDP备份 的健康度统计
    :param tenants:
        None 为所有租户
        否则为 ["tenant_uuid", "tenant_uuid", ]
    :param minutes:
        从 minutes 前开始查询数据
    :param health_level:
        健康等级定义 [{"value":99, "display":"99%的时间段有CDP备份数据"}, {"value":95, "display":"健康"},
         {"value":-1, "display":"告警"},]
        None 为使用默认定义
    :param host_ident_array: out list
        符合查询条件的主机列表
        与current_level同时不为None
    :param current_level:
        指定筛选条件，与host_ident_array同时不为None
    :return:
        {
            "begin_timestamp": float,
            "end_timestamp": float,
            "data": [
                        {
                            "value": int,
                            "display": "str",
                            "filter_ident": "str",
                            "count": int
                        },
                        ...
                    ]
        }
    """
    assert (current_level is not None and host_ident_array is not None) or (
            current_level is None and host_ident_array is None)

    ranges = _query_host_cdp_snapshot_current_valid_range(tenants, minutes)

    result = _query_host_cdp_snapshot_current_health_init_result(health_level, minutes, tenants,
                                                                 ranges['begin_timestamp'], ranges['end_timestamp'])

    for host_item in ranges['data']:
        seconds_sum = 0
        for range_item in host_item['range']:
            seconds_sum += range_item['end'] - range_item['begin']
        per_cent = (seconds_sum * 100 / 60) // minutes
        for level_item in result['data']:
            if per_cent >= level_item['value']:
                level_item['count'] += 1
                level_item['filter_ident']['ids'].append(host_item['host_id'])
                if host_ident_array is not None and level_item['value'] == current_level['value']:
                    host_ident_array.append(host_item['host_ident'])
                break

    return result


def cdp_host_health_overview(request):
    # 最近24小时持续备份客户端健康度
    ret = {'r': 0, 'e': '操作成功'}

    cdp_health_data = _query_host_cdp_snapshot_current_health(request.user.id)

    begin_timestamp = cdp_health_data.get('begin_timestamp')
    end_timestamp = cdp_health_data.get('begin_timestamp')

    ret['begin'] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(begin_timestamp))
    ret['end'] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(end_timestamp))
    series = list()
    data = list()
    cdp_data = cdp_health_data.get('data', [])
    for cdp_health in cdp_data:
        value = cdp_health['value']
        filter_ident = json.dumps(cdp_health['filter_ident'], ensure_ascii=False)
        count = cdp_health['count']
        data.append({'filter_uuid': filter_ident, 'name': _fmt_cpd_health_display(value),
                     "normal_color": _get_cdp_health_color(value), 'value': count})
    series.append({"data": data})
    ret['series'] = series
    if len(data) == 0:
        ret = {'r': 1, 'e': '暂无数据'}

    return HttpResponse(json.dumps(ret, ensure_ascii=False))


def _fmt_health_display(minutes):
    if minutes == 1080:
        return 'RPO≤18小时'
    elif minutes == 2160:
        return 'RPO≤36小时'
    elif minutes == 4320:
        return 'RPO≤3天'
    elif minutes == -1:
        return 'RPO＞3天'
    return '未知（{}）'.format(minutes)


def _get_health_color(minutes):
    if minutes == -1:
        return '#e55e6a'
    return None


def _get_last_snapshot_datetime(agent_host_obj):
    host_snapshot_obj = HostSnapshot.objects.filter(host_id=agent_host_obj.id, deleted=False, successful=True,
                                                    is_cdp=False).order_by('-start_datetime').first()
    return host_snapshot_obj.start_datetime if (host_snapshot_obj is not None) else None


def _query_host_snapshot_current_health(tenants=None, health_level=None, current_level=None, host_ident_array=None):
    """
    查询 普通快照点 的健康度统计
    :param tenants:
        None 为所有租户
        否则为 ["tenant_uuid", "tenant_uuid", ]
    :param health_level:
        健康等级定义 [{"minutes": 1080, "display":"18小时内有快照点"}, {"minutes": 2160, "display":"36小时内有快照点"},
         {"value": 4320, "display":"3天内有快照点"}, {"value":-1, "display":"超过3天没有快照点"},]
        None 为使用默认定义
    :param host_ident_array: out list
        符合查询条件的主机列表
        与current_level同时不为None
    :param current_level:
        指定筛选条件，与host_ident_array同时不为None
    :return:
        [
            {
                "minutes": int,
                "display": "str",
                "filter_ident": "str",
                "count": int
            },
            ...
        ]
    """
    assert (current_level is not None and host_ident_array is not None) or (
            current_level is None and host_ident_array is None)
    if not health_level:
        result = [
            {"minutes": 1080, "display": "高"},  # 18小时内有快照点
            {"minutes": 2160, "display": "中"},  # 36小时内有快照点
            {"minutes": 4320, "display": "低"},  # 3天内有快照点
            {"minutes": -1, "display": "警告"},  # 超过3天没有快照点
        ]
    else:
        result = deepcopy(health_level)

    for r in result:
        query_params = dict()
        query_params['all_level'] = deepcopy(result)
        query_params['current_level'] = deepcopy(r)
        query_params['type'] = 'query_host_snapshot_current_health'
        query_params['tenants'] = tenants
        query_params['minutes'] = r['minutes']
        r['filter_ident'] = {'display': _fmt_health_display(r['minutes']), 'ids': list()}
        r['count'] = 0

    now_datetime = datetime.datetime.now()

    agent_host_objs = _query_agent_host_with_schedule_type(tenants, BackupTaskSchedule.CYCLE_PERDAY)

    for agent_host_obj in agent_host_objs:
        last_snapshot_datetime = _get_last_snapshot_datetime(agent_host_obj)
        if last_snapshot_datetime is None:
            result[-1]['count'] += 1
            result[-1]['filter_ident']['ids'].append(agent_host_obj.id)
            if host_ident_array is not None and current_level["minutes"] == "-1":
                host_ident_array.append(agent_host_obj.host_ident)
            continue
        delta = now_datetime - last_snapshot_datetime
        for r in result:
            if delta <= datetime.timedelta(minutes=r['minutes']):
                r['count'] += 1
                r['filter_ident']['ids'].append(agent_host_obj.id)
                if host_ident_array is not None and r["minutes"] == current_level["minutes"]:
                    host_ident_array.append(agent_host_obj.host_ident)
                break

    for r in result:
        r['filter_ident'] = json.dumps(r['filter_ident'], ensure_ascii=False)

    return result


def host_health_overview(request):
    # 定时保护客户端灾备健康度
    is_debug = request.GET.get('debug')
    ret = {'r': 0, 'e': '操作成功'}
    host_snapshot_current_health_list = _query_host_snapshot_current_health(request.user.id)

    if is_debug:
        ret['debug'] = host_snapshot_current_health_list

    series = list()
    data = list()
    for host_snapshot_current_health in host_snapshot_current_health_list:
        minutes = host_snapshot_current_health['minutes']
        filter_ident = host_snapshot_current_health['filter_ident']
        count = host_snapshot_current_health['count']
        data.append({'filter_uuid': filter_ident, 'name': _fmt_health_display(minutes),
                     "normal_color": _get_health_color(minutes), 'value': count})

    series.append({"data": data})
    ret['series'] = series
    if len(data) == 0:
        ret = {'r': 1, 'e': '暂无数据'}

    return HttpResponse(json.dumps(ret, ensure_ascii=False))


def cdp_host_timeline_overview(request):
    # 最近24小时持续备份生成统计
    is_debug = request.GET.get('debug')
    ret = {'r': 0, 'e': '操作成功'}
    cdp_timelime_data = _query_host_cdp_snapshot_current_valid_range(request.user.id)
    begin_timestamp = cdp_timelime_data.get('begin_timestamp')
    end_timestamp = cdp_timelime_data.get('end_timestamp')
    cdp_timelime_list = cdp_timelime_data.get('data', [])

    ret['begin_time'] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(begin_timestamp))
    ret['end_time'] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(end_timestamp))

    if is_debug:
        ret['debug_org'] = cdp_timelime_data
        cdp_timelime_data_debug = copy.deepcopy(cdp_timelime_data)
        for cdp_timelime_debug in cdp_timelime_data_debug.get('data', []):
            cdp_timelime_debug['host_display_name'] = cdp_timelime_debug['host_display_name']
            range_debug_list = cdp_timelime_debug.get('range', [])
            for range_debug in range_debug_list:
                range_debug['begin'] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(range_debug['begin']))
                range_debug['end'] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(range_debug['end']))

        ret['debug_human'] = cdp_timelime_data_debug

    data = list()
    for cdp_timelime in cdp_timelime_list:
        name = cdp_timelime['host_display_name']
        range = cdp_timelime.get('range', [])
        data.append({'name': name, 'total_begin': begin_timestamp, 'total_end': end_timestamp, 'range': range})
    ret['data'] = data
    if len(data) == 0:
        ret = {'r': 1, 'e': '暂无数据'}

    return HttpResponse(json.dumps(ret, ensure_ascii=False))


CURRENT_RESTORE_TASK_STRING = "灾难恢复任务"
CURRENT_HTB_TASK_STRING = "热备恢复任务"
CURRENT_MIGRATE_TASK_STRING = "迁移任务"
CURRENT_BACKUP_TASK_STRING = "定时备份任务"
CURRENT_CDP_TASK_STRING = "持续备份任务"


def _query_current_execute_task(user_id):
    """
    得到当前各种任务正在执行的数量
    :param tenants:
        None 表示所有租户
        否则为 ["tenant_uuid", "tenant_uuid", ...]
    :return:
    [
        {
            display: str
            count: int
        }
        {
            display: str
            count: int
        }
            ...
    ]
    """
    result = list()

    restore_idents = RestoreTask.objects.filter(
        finish_datetime__isnull=True,
        restore_target__htb_task__isnull=True).filter(host_snapshot__host__user__id=user_id).values_list(
        'host_snapshot__host__ident', flat=True)

    htb_idents = HTBTask.objects.filter(
        start_datetime__isnull=False,
        finish_datetime__isnull=True).filter(restore_target__user__id=user_id).values_list(
        'schedule__host__ident',
        flat=True)

    cdp_idents = CDPTask.objects.filter(
        schedule__isnull=False).filter(
        finish_datetime__isnull=True).filter(host_snapshot__host__user__id=user_id).values_list('schedule__host__ident',
                                                                                                flat=True)

    migration_idents = MigrateTask.objects.filter(
        host_snapshot__isnull=False,
        finish_datetime__isnull=True).filter(host_snapshot__host__user__id=user_id).values_list(
        'source_host__ident',
        flat=True)

    backup_idents = BackupTask.objects.filter(
        host_snapshot__isnull=False,
        finish_datetime__isnull=True).filter(host_snapshot__host__user__id=user_id).values_list(
        'schedule__host__ident',
        flat=True)

    restore_task_count = restore_idents.count()
    htb_task_count = htb_idents.count()
    migrate_task_count = migration_idents.count()
    backup_task_count = backup_idents.count()
    cdp_task_count = cdp_idents.count()
    result.append({"display": CURRENT_RESTORE_TASK_STRING, "count": restore_task_count})
    result.append({"display": CURRENT_HTB_TASK_STRING, "count": htb_task_count})
    result.append({"display": CURRENT_MIGRATE_TASK_STRING, "count": migrate_task_count})
    result.append({"display": CURRENT_BACKUP_TASK_STRING, "count": backup_task_count})
    result.append({"display": CURRENT_CDP_TASK_STRING, "count": cdp_task_count})
    return result


def _get_validate_task_color(name):
    if name == CONFIRM_TASK_FAILED_STRING:
        return '#e55e6a'
    return None


def _query_task_status_abstract(user_id, months):
    """
    得到验证任务的查询集
    """
    now_datetime = datetime.datetime.now()
    start = now_datetime - relativedelta(months=months)
    task_objs = AutoVerifyTask.objects.filter(created__gt=start).all()
    if user_id is not None:
        point_id_list = list()
        for task_obj in task_objs:
            user = HostSnapshot.objects.get(id=task_obj.point_id.split('|')[1]).host.user
            if user and user.id != user_id:
                point_id_list.append(task_obj.point_id)
        if point_id_list:
            task_objs = task_objs.exclude(point_id__in=point_id_list)

    return task_objs


def _query_task_status(task_objs, filter_obj, task_status):
    """
    得到每个任务状态的数量count和筛选标识ident
    """
    if task_status == CONFIRM_TASK_PENDING:
        _task_objs = task_objs.filter(
            verify_type__in=(AutoVerifyTask.VERIFY_TYPE_QUEUE, AutoVerifyTask.VERIFY_TYPE_ING))
    elif task_status in (CONFIRM_TASK_SUCCESS, CONFIRM_TASK_FAILED,):
        _task_objs = task_objs.filter(verify_type=AutoVerifyTask.VERIFY_TYPE_END)
        _success_list = list()
        _failed_list = list()
        for task_obj in _task_objs:
            if json.loads(task_obj.verify_result).get('result') == 'pass':
                _success_list.append(task_obj.id)
            else:
                _failed_list.append(task_obj.id)
        if task_status == CONFIRM_TASK_SUCCESS:
            _task_objs = _task_objs.exclude(id__in=_failed_list)
        else:
            _task_objs = _task_objs.exclude(id__in=_success_list)
    else:
        _logger.error('_query_task_status Failed.task_status={}'.format(task_status))

    _task_objs_filter = deepcopy(filter_obj)
    _task_objs_filter["task_status"] = task_status
    point_id_list = list()
    for _task_obj in _task_objs:
        point_id_list.append(_task_obj.point_id)
    point_id_list = list(set(point_id_list))
    _task_objs_filter_ident = {'task_status': task_status, 'point_id_list': point_id_list}
    _task_objs_count = len(point_id_list)
    return _task_objs_count, _task_objs_filter_ident


CONFIRM_TASK_PENDING_STRING = "等待执行"
CONFIRM_TASK_SUCCESS_STRING = "验证成功"
CONFIRM_TASK_FAILED_STRING = "验证失败"
CONFIRM_TASK_PENDING = 1
CONFIRM_TASK_SUCCESS = 2
CONFIRM_TASK_FAILED = 3


def _query_confirm_task_status_count(tenants=None, months=3):
    """
    查询验证任务状态的统计
    :param tenants:
        None 表示所有租户
        否则为 ["tenant_uuid", "tenant_uuid", ...]
    :param months:
        查询时间
    :return:
        [
            {
                filter_ident: str
                display: str
                count: int
            }
            {
                filter_ident: str
                display: str
                count: int
            }
            ...
         ]
    """
    result = list()
    filter_obj = {"tenants": tenants, "task_status": CONFIRM_TASK_PENDING}
    task_objs = _query_task_status_abstract(tenants, months)

    pending_objs_count, pending_objs_filter_ident = _query_task_status(task_objs, filter_obj, CONFIRM_TASK_PENDING)
    result.append({"filter_ident": pending_objs_filter_ident, "count": pending_objs_count,
                   "display": CONFIRM_TASK_PENDING_STRING})

    success_objs_count, success_objs_filter_ident = _query_task_status(task_objs, filter_obj, CONFIRM_TASK_SUCCESS)
    result.append({"filter_ident": success_objs_filter_ident, "count": success_objs_count,
                   "display": CONFIRM_TASK_SUCCESS_STRING})

    failed_objs_count, failed_objs_filter_ident = _query_task_status(task_objs, filter_obj, CONFIRM_TASK_FAILED)
    result.append({"filter_ident": failed_objs_filter_ident, "count": failed_objs_count,
                   "display": CONFIRM_TASK_FAILED_STRING})

    return result


def _get_validate_task(request):
    task_list = _query_confirm_task_status_count(request.user.id)

    data = list()
    for task in task_list:
        filter_uuid = task['filter_ident']
        name = task['display']
        value = task['count']
        data.append(
            {'filter_uuid': filter_uuid, 'name': name, "normal_color": _get_validate_task_color(name), 'value': value})
    return data


def all_task_overview(request):
    # 当前任务数量
    ret = {'r': 0, 'e': '操作成功'}
    execute_task_list = _query_current_execute_task(request.user.id)
    data = list()
    for execute_task in execute_task_list:
        display = execute_task['display']
        count = execute_task['count']
        data.append({"name": display, "value": count})

    validate_task_list = _get_validate_task(request)
    for validate_task in validate_task_list:
        if validate_task['name'] == '等待执行':
            data.append({"name": '{}的验证任务'.format(validate_task['name']), "value": validate_task['value']})
            break

    ret['data'] = data

    return HttpResponse(json.dumps(ret, ensure_ascii=False))


def _get_validate_task(request):
    task_list = _query_confirm_task_status_count(request.user.id)

    data = list()
    for task in task_list:
        filter_uuid = task['filter_ident']
        name = task['display']
        value = task['count']
        data.append(
            {'filter_uuid': filter_uuid, 'name': name, "normal_color": _get_validate_task_color(name), 'value': value})
    return data


def validate_task_overview(request):
    # 最近3个月内验证任务总览
    ret = {'r': 0, 'e': '操作成功'}

    series = list()
    data = _get_validate_task(request)
    series.append({"data": data})
    ret['series'] = series
    if len(data) == 0:
        ret = {'r': 1, 'e': '暂无数据'}
    return HttpResponse(json.dumps(ret, ensure_ascii=False))
