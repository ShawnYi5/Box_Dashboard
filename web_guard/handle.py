import datetime
import json, math, zipfile, html
import datetime, time, os, re
from collections import Counter
from xdashboard.models import OperationLog
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.http import HttpResponse
from rest_framework import status
from xdashboard.handle.xlwt.Workbook import *
from apiv1.models import Host
from apiv1.views import HostSessions, HostSnapshotLocalRestore, get_response_error_string
from box_dashboard import xlogging, functions, xdatetime, xdata
from web_guard.models import WebGuardStrategy, EmergencyPlan, StrategyGroup, AlarmEventLog, AlarmEvent, ModifyEntry
from web_guard.views import EmPlansInfo, AlarmMethodInfo, Strategy, MaintainStatus, EmergencyPlanExecute, WGRPreLogic, \
    MaintainConfig
from web_guard.restore import AcquireRestoreInfo
from web_guard.web_check_logic import QueryAlarmEvent, OpAlarmEvent
from xdashboard.models import UserProfile
from .sitespider.spiderapi import CSensitiveWordsAPI, CSiteSpiderAPI
from .sitespider.sitespider.models import CModel_ignore_url_area
from .sitespider.spider import CWebsiteMonitor
from box_dashboard.functions import show_names, sort_gird_rows

_logger = xlogging.getLogger(__name__)

router = functions.Router(globals())


def _get_strategy_name(info):
    if info['enabled']:
        return '<p>{}</p>'.format(info['name'])
    else:
        return '<p style="color:red">{}(禁用)</p>'.format(info['name'])


def _get_day_str(value):
    str_map = {1: '星期一', 2: '星期二', 3: '星期三', 4: '星期四', 5: '星期五', 6: '星期六', 7: '星期日'}
    return str_map[int(value)]


def _get_op_str(info):
    if int(info[0]) == EmergencyPlan.EM_AUTO:
        return '自动还原,等待时间:{}分钟'.format(info[1])
    elif int(info[0]) == EmergencyPlan.EM_MAINTAIN:
        return '切换为应急页面,等待时间:{}分钟'.format(info[1])
    else:
        return '手动处理'


def _format_info(info):
    rs = dict()
    ex_info = json.loads(info['exc_info'])
    # format 时间
    time_str = ''
    if ex_info['is_all_day_time']:
        time_str = '<p>每天:{}</p>'.format('全天24小时')
    else:
        for index, _v in enumerate(ex_info['day_time_range'], 1):
            if index == 1:
                time_str = '<p>每天：{}</p>'.format(_v[0] + '--' + _v[1])
            else:
                time_str += '<p style="margin-left:3em">{}</p>'.format(_v[0] + '--' + _v[1])

    all_day_str = [_get_day_str(_v) for _v in ex_info['week_day_range']]
    if len(all_day_str) % 2 != 0:
        all_day_str.append('')
    _2_group = list(zip(all_day_str[::2], all_day_str[1::2]))
    for _index, _v2 in enumerate(_2_group, 1):
        if _index == 1:
            time_str += '<p>每周：{}，{}</p>'.format(_v2[0], _v2[1]) if _v2[1] else '<p>每周：{}</p>'.format(_v2[0])
        else:
            time_str += '<p style="margin-left:3em">{}，{}</p>'.format(_v2[0], _v2[1]) if _v2[
                1] else '<p style="margin-left:3em">{}</p>'.format(_v2[0])

    rs['time_info'] = time_str
    # 过滤掉被删除的策略
    info['strategy'] = list(filter(lambda x: not x['deleted'], info['strategy']))
    # format 策略
    if info['strategy']:
        strategy_str = ''.join([_get_strategy_name(_v) for _v in info['strategy']])
    else:
        strategy_str = '无'
    rs['strategy'] = strategy_str
    # format 主机
    if info['hosts']:
        host_str = ''.join(['<p>{}</p>'.format(_v['name']) for _v in info['hosts']])
    else:
        host_str = '无'
    rs['hosts'] = host_str
    # format 操作
    ops = ex_info['events_choice']
    ev_str = '<p date-type="high" date-value="{}">高警告等级:{}</p>'.format(ops['high'][0], _get_op_str(ops['high']))
    ev_str += '<p date-type="middle" date-value="{}">中警告等级:{}</p>'.format(ops['middle'][0], _get_op_str(ops['middle']))
    ev_str += '<p date-type="low" date-value="{}">低警告等级:{}</p>'.format(ops['low'][0], _get_op_str(ops['low']))
    rs['op'] = ev_str
    # format 状态
    rs['enable'] = '启用' if info['enabled'] else '禁用'

    return rs


def _get_em_plans_with_many(request, data):
    page = int(request.GET.get('page', 1))
    rows = int(request.GET.get('rows', 30))

    paginator = Paginator(data, rows)
    total_plan = paginator.count
    total_page = paginator.num_pages

    page = total_page if page > total_page else page
    need_lists = paginator.page(page).object_list
    row_list = list()
    for info in need_lists:
        if info['deleted']:
            continue
        f_dict = _format_info(info)
        _d = {'id': info['id'],
              'cell': [info['id'], '<p>{}</p>'.format(info['name']), f_dict['enable'], f_dict['time_info'],
                       f_dict['strategy'],
                       f_dict['hosts'], f_dict['op'],
                       ]}
        row_list.append(_d)

    ret_info = {'r': 0, 'a': 'list', 'page': str(page), 'total': total_page,
                'records': total_plan, 'rows': row_list}
    json_str = json.dumps(ret_info, ensure_ascii=False)

    sort_gird_rows(request, ret_info)
    return HttpResponse(json.dumps(ret_info))


def get_em_plans(request):
    rsp_data = EmPlansInfo().get(request)
    js_dict = {"r": 0, "data": ''}
    if status.is_success(rsp_data.status_code):
        data = rsp_data.data
        if data['is_set']:
            return _get_em_plans_with_many(request, data['data'])
        else:
            data['data']['exc_info'] = json.loads(data['data']['exc_info'])
            js_dict['data'] = data['data']
    else:
        js_dict['r'] = 1
    return HttpResponse(json.dumps(js_dict))


def get_hosts(request):
    host_lists = HostSessions().get(request=request).data
    # 过滤掉 被删除的主机
    host_lists = list(filter(lambda x: not Host.objects.get(ident=x['ident']).is_deleted, host_lists))
    need_info = [{'name': host['name'], 'id': host['id']} for host in host_lists]
    js_dict = {"r": 0, "item_lists": need_info}
    return HttpResponse(json.dumps(js_dict))


def get_tasks(request):
    task_lists = WebGuardStrategy.objects.filter(deleted=False)
    need_info = [{'name': task.name, 'id': task.id, 'enabled': task.enabled} for task in task_lists]
    js_dict = {"r": 0, "item_lists": need_info}
    return HttpResponse(json.dumps(js_dict))


def create_plans(request):
    rsp_data = EmPlansInfo().put(request)
    js_dict = {"r": 0, 'e': '创建成功'}
    args = json.loads(request.POST['data'])
    _id = args.get('id', False)
    op_str = '创建应急策略' if not _id else '编辑应急策略'
    if not status.is_success(rsp_data.status_code):
        js_dict['r'] = 1
        js_dict['e'] = '创建失败'

    mylog = {'操作': op_str, '操作结果': js_dict['e'], '应急策略': args['name']}
    SaveOperationLog(request.user, OperationLog.TYPE_WEBGUARD, json.dumps(mylog, ensure_ascii=False))
    return HttpResponse(json.dumps(js_dict))


def del_plans(request):
    _id_str = request.GET.get('id')
    _d_handle = EmPlansInfo()
    js_dict = {"r": 0}
    for _id in _id_str.split(','):
        rsp_data = _d_handle.delete(_id)
        if not status.is_success(rsp_data.status_code):
            js_dict['r'] = 1
            mylog = {'操作': '删除应急策略', '操作结果': '操作失败', '应急策略': show_names(EmergencyPlan, (_id,))}
            SaveOperationLog(request.user, OperationLog.TYPE_WEBGUARD, json.dumps(mylog, ensure_ascii=False))
            return HttpResponse(json.dumps(js_dict))

        mylog = {'操作': '删除应急策略', '操作结果': '操作成功', '应急策略': show_names(EmergencyPlan, (_id,))}
        SaveOperationLog(request.user, OperationLog.TYPE_WEBGUARD, json.dumps(mylog, ensure_ascii=False))
    return HttpResponse(json.dumps(js_dict))


def enable_plans(request):
    _id_str = request.GET.get('id')
    _value = request.GET.get('enbale')
    _d_handle = EmPlansInfo()
    js_dict = {"r": 0}
    for _id in _id_str.split(','):
        rsp_data = _d_handle.enable(_id, _value)
        op_str = '启用应急策略' if int(_value) else '禁用应急策略'
        if not status.is_success(rsp_data.status_code):
            js_dict['r'] = 1
            mylog = {'操作': op_str, '操作结果': '操作失败', '应急策略': show_names(EmergencyPlan, (_id,))}
            SaveOperationLog(request.user, OperationLog.TYPE_WEBGUARD, json.dumps(mylog, ensure_ascii=False))
            return HttpResponse(json.dumps(js_dict))

        mylog = {'操作': op_str, '操作结果': '操作成功', '应急策略': show_names(EmergencyPlan, (_id,))}
        SaveOperationLog(request.user, OperationLog.TYPE_WEBGUARD, json.dumps(mylog, ensure_ascii=False))
    return HttpResponse(json.dumps(js_dict))


def create_arm_method(request):
    rsp = AlarmMethodInfo().post(request)
    js_dict = {"r": 0}
    if not status.is_success(rsp.status_code):
        js_dict['r'] = 1
        js_dict['e'] = '创建失败'
    return HttpResponse(json.dumps(js_dict))


def get_arm_method(request):
    rsp = AlarmMethodInfo().get(request)
    js_dict = {"r": 0, 'data': rsp.data}
    if not status.is_success(rsp.status_code):
        js_dict['r'] = 1
        js_dict['e'] = '获取失败'
    return HttpResponse(json.dumps(js_dict))


# 通过组名，查询策略组，不存在则创建
def get_strategy_group_by_name(name):
    try:
        return StrategyGroup.objects.get(name=name)
    except StrategyGroup.DoesNotExist:
        return StrategyGroup.objects.create(name=name)


get_strategy_group_by_name(xdata.DEFAULT_STRATEGY_GROUP_NAME)


def modify_next_run_time(strategy):
    ratio = {'secs': 1, 'mins': 60}
    last_run_time, next_run_time = strategy.last_run_date, strategy.next_run_date
    if next_run_time is None:
        return
    ext_info = json.loads(strategy.ext_info)
    val, unit = int(ext_info['interval_time']['time']), ext_info['interval_time']['unit']
    delta_sec = val * ratio[unit]
    strategy.next_run_date = last_run_time + datetime.timedelta(seconds=delta_sec)
    strategy.save(update_fields=['next_run_date'])


# 创建策略、编辑策略
def create_strategy(request):
    params = request.POST
    name, from_who, group_name = params['name'], params['from'], params['group']
    edit_id, ext_info = params['edit_id'], params['ext_info']
    user_id = request.user.id
    group_id = get_strategy_group_by_name(group_name).id

    check_type = xdata.STRATEGY_TYPE[json.loads(ext_info)['strategy_type']]
    if from_who == 'create':
        api_params = {'user': user_id, 'name': name, 'ext_info': ext_info, 'group': group_id, 'check_type': check_type}
        resp = Strategy().post(request, api_params)
        if not status.is_success(resp.status_code):
            mylog = {'操作': '创建监控策略', '操作结果': '创建策略失败', '监控策略': name}
            SaveOperationLog(request.user, OperationLog.TYPE_WEBGUARD, json.dumps(mylog, ensure_ascii=False))
            return HttpResponse(json.dumps({'r': 1, 'e': '创建策略失败'}, ensure_ascii=False))

        mylog = {'操作': '创建监控策略', '操作结果': '操作成功', '监控策略': name}
        SaveOperationLog(request.user, OperationLog.TYPE_WEBGUARD, json.dumps(mylog, ensure_ascii=False))

    if from_who == 'edit':
        strategy = Strategy.update_fields(edit_id, name=name, group=group_id, ext_info=ext_info, check_type=check_type)
        modify_next_run_time(strategy)
        mylog = {'操作': '编辑监控策略', '操作结果': '操作成功', '监控策略': name}
        SaveOperationLog(request.user, OperationLog.TYPE_WEBGUARD, json.dumps(mylog, ensure_ascii=False))

    return HttpResponse(json.dumps({'r': 0, 'e': ''}))


# 获取用户所有策略、目前的状态
def get_strategies_info(request):
    params = request.GET
    page_rows = params.get('rows', '10')
    page_index = params.get('page', '1')

    user_strategies = Strategy().get(request).data
    paginator = Paginator(object_list=user_strategies, per_page=page_rows)
    all_rows = paginator.count
    page_num = paginator.num_pages
    get_pagex = paginator.page(page_index).object_list

    ret = {'a': 'list', 'records': all_rows, 'total': page_num, 'page': page_index, 'rows': [], 'r': 0}
    for strategy in get_pagex:
        lab_id, name = strategy['id'], strategy['name']
        is_on = '启用' if strategy['enabled'] else '禁用'
        last_run = strategy['last_run_date'] if strategy['last_run_date'] else '--'
        if last_run != '--':
            last_run = datetime.datetime.strptime(last_run, xdatetime.FORMAT_WITH_MICROSECOND).strftime(
                xdatetime.FORMAT_WITH_USER_SECOND)
        lab_status = WebGuardStrategy.display_status(strategy['present_status'])
        credible_time = '--'
        task_histories = json.loads(strategy['task_histories'])
        credible_tasks = list(filter(lambda x: x['credible'] == 'yes', task_histories['tasks']))

        crawl_curr_site_name = 'none'
        if 'tasks' in task_histories and len(task_histories['tasks']) > 0:
            crawl_curr_site_name = task_histories['tasks'][-1]['crawl_site_name']

        crawl_site_name = 'none'
        if len(credible_tasks) > 0:
            credible_time = datetime.datetime.strptime(credible_tasks[-1]['date_time'],
                                                       xdatetime.FORMAT_WITH_SECOND_FOR_PATH).strftime(
                '%Y-%m-%d %H:%M:%S')
            crawl_site_name = credible_tasks[-1]['crawl_site_name']

        ret['rows'].append(
            {'cell': [lab_id, name, credible_time, crawl_site_name, is_on, last_run, lab_status, crawl_curr_site_name],
             'id': lab_id})

    sort_gird_rows(request, ret)
    return HttpResponse(json.dumps(ret))


# 组中没有任何策略时，将被删除 (默认组除外)
def check_strategy_group_delete():
    groups = StrategyGroup.objects.all()
    for group in groups:
        if group.name == xdata.DEFAULT_STRATEGY_GROUP_NAME:
            continue
        if not group.strategies.filter(deleted=False).exists():
            group.delete()


# 删除指定策略
def delete_strategies(request):
    params = request.GET
    strategy_ids = params['ids'].split(',')
    for strategy_id in strategy_ids:
        Strategy.set_delete(strategy_id=strategy_id)

    check_strategy_group_delete()

    mylog = {'操作': '删除监控策略', '操作结果': '操作成功', '监控策略': show_names(WebGuardStrategy, strategy_ids)}
    SaveOperationLog(request.user, OperationLog.TYPE_WEBGUARD, json.dumps(mylog, ensure_ascii=False))

    return HttpResponse(json.dumps({'r': 0, 'e': ''}))


# 禁用策略
def disable_strategies(request):
    params = request.GET
    strategy_ids = params['ids'].split(',')
    for strategy_id in strategy_ids:
        Strategy.set_disable(strategy_id=strategy_id)

    mylog = {'操作': '禁用监控策略', '操作结果': '操作成功', '监控策略': show_names(WebGuardStrategy, strategy_ids)}
    SaveOperationLog(request.user, OperationLog.TYPE_WEBGUARD, json.dumps(mylog, ensure_ascii=False))

    return HttpResponse(json.dumps({'r': 0, 'e': ''}))


# 获取指定的策略、详细信息
def get_strategy_info(request):
    params = request.GET
    strategy_id = int(params['lab_id'])
    strategy = list(filter(lambda elem: elem['id'] == strategy_id, Strategy().get(request).data))[0]
    _logger.debug(strategy)
    strategy['group_name'] = StrategyGroup.objects.get(id=strategy['group']).name
    return HttpResponse(json.dumps(strategy))


# 启用策略：两种方式
def on_strategy(request):
    params = request.GET
    fiducial = params['fiducial']
    strategy_ids = params['ids'].split(',')

    for strategy_id in strategy_ids:
        monitor = WebGuardStrategy.objects.get(id=strategy_id)
        monitor.set_use_history(fiducial == 'fiducial-last')

        if not monitor.enabled:  # 若禁用状态: 修改下次运行时间, 同时启用
            monitor.set_enable()
            modify_next_run_time(monitor)

    mylog = {'操作': '启用监控策略',
             '操作结果': '操作成功',
             '监控策略': show_names(WebGuardStrategy, strategy_ids),
             '启用模式': '使用历史' if fiducial == 'fiducial-last' else '使用最新'}
    SaveOperationLog(request.user, OperationLog.TYPE_WEBGUARD, json.dumps(mylog, ensure_ascii=False))

    return HttpResponse(json.dumps({'r': 0, 'e': ''}))


# 获取可用的策略组
def get_strategy_available_groups(request):
    avail_group = [strategy.name for strategy in StrategyGroup.objects.all()]
    return HttpResponse(json.dumps({'groups': avail_group}))


def get_line_chart_data(request):
    now = datetime.datetime.now()
    st = now - datetime.timedelta(hours=24)
    time_inv = 10
    loop_times = 24 * 60 // time_inv
    data = [{'time': _get_inv_time_str(st, time_inv, i), 'value': 0} for i in range(loop_times)]
    all_arm_event_log = AlarmEventLog.objects.filter(strategy__user=request.user, event_time__gte=st,
                                                     event_time__lte=now,
                                                     log_type__in=[AlarmEventLog.ALARM_EVENT_LOG_TYPE_HAPPENED])
    for log in all_arm_event_log:
        offset = (log.event_time - st).seconds / (60 * time_inv)
        data[int(offset)]['value'] += 1
    js_dict = {'r': 0, 'data': data}
    return HttpResponse(json.dumps(js_dict))


def _get_inv_time_str(st_datetime, inv, offset):
    return (st_datetime + datetime.timedelta(minutes=inv * offset)).strftime('%Y-%m-%d %H:%M:%S')


def get_pie_chart_data(request):
    user_strategies = Strategy().get(request).data
    item_count = dict(normal=0, high=0, middle=0, low=0, other=0)
    for strategy in user_strategies:
        strategy_obj = WebGuardStrategy.objects.get(id=strategy['id'])
        st_level, _ = QueryAlarmEvent.query_level_and_detail(strategy_obj)
        item_count[st_level] += 1
    data = [
        {'value': item_count['high'], 'label': '高风险'},
        {'value': item_count['middle'], 'label': '中风险'},
        {'value': item_count['low'], 'label': '低风险'},
        {'value': item_count['other'], 'label': '其它'}
    ]
    _value = [i['value'] for i in data]
    mark_value = list()
    mark_value.append(sum(_value))
    mark_value.extend(_value)
    js_dict = {'r': 0, 'data': data, 'mark_value': mark_value}
    return HttpResponse(json.dumps(js_dict))


def get_strategy_data(request):
    data = []
    event_level = request.GET.get('event_level')
    user_strategies = Strategy().get(request).data
    for strategy in user_strategies:
        item = dict()
        strategy_obj = WebGuardStrategy.objects.get(id=strategy['id'])
        st_level, _ = QueryAlarmEvent.query_level_and_detail(strategy_obj)
        if not _check_is_need_strategy(st_level, event_level):
            continue
        item['event_id'] = strategy['id']
        item['event_name'] = strategy['name']
        item['event_group'] = StrategyGroup.objects.get(id=strategy['group']).name
        item['event_status'] = xdata.STRATEGY_EVENT_STATUS[st_level]
        item['event_sub_list'] = _get_sub_item(strategy_obj)
        item['event_pc_list'] = _get_pc_item(strategy_obj)
        item['event_point_list'] = _get_point_item(strategy_obj)
        data.append(item)
    js_dict = {'r': 0, 'data': data}
    return HttpResponse(json.dumps(js_dict))


def _get_event_words(list_events):
    rs = list()
    _l = [Counter(json.loads(event.detail)['words']).values() for event in list_events]
    for _ in _l:
        rs.extend(_)
    return rs


def _get_sub_item(strategy_obj):
    ext_info = json.loads(strategy_obj.ext_info)
    rs = list()
    for item in ext_info['inspect_item']:
        one_item = dict()
        if item == 'key-word':
            not_solve_envnts = QueryAlarmEvent.from_strategy_get_events(strategy_obj, 'sensitive')
            sensitive_words_count = sum(QueryAlarmEvent.get_event_words(not_solve_envnts))
            one_item['item_name'] = '敏感词'
            one_item['item_value'] = '异常，{}个敏感词'.format(sensitive_words_count) if sensitive_words_count else '正常'
            rs.append(one_item)
        elif item == 'content-tamper':
            not_solve_envnts = QueryAlarmEvent.from_strategy_get_events(strategy_obj, 'content')
            one_item['item_name'] = '内容篡改'
            one_item['item_value'] = '异常，{}个篡改项'.format(
                QueryAlarmEvent.get_content_events_risk_num(not_solve_envnts)) if not_solve_envnts else '正常'
            rs.append(one_item)
        elif item == 'pictures-tamper':
            not_solve_envnts = QueryAlarmEvent.from_strategy_get_events(strategy_obj, 'pictures')
            one_item['item_name'] = '网页图片篡改'
            one_item['item_value'] = '异常，{}个篡改项'.format(
                QueryAlarmEvent.get_content_events_risk_num(not_solve_envnts)) if not_solve_envnts else '正常'
            rs.append(one_item)
        elif item == 'resources-tamper':
            not_solve_envnts = QueryAlarmEvent.from_strategy_get_events(strategy_obj, 'resources')
            one_item['item_name'] = '网页下载资源篡改'
            one_item['item_value'] = '异常，{}个篡改项'.format(
                QueryAlarmEvent.get_content_events_risk_num(not_solve_envnts)) if not_solve_envnts else '正常'
            rs.append(one_item)
        elif item == 'links-tamper':
            not_solve_envnts = QueryAlarmEvent.from_strategy_get_events(strategy_obj, 'links')
            one_item['item_name'] = '网页链接篡改'
            one_item['item_value'] = '异常，{}个篡改项'.format(
                QueryAlarmEvent.get_content_events_risk_num(not_solve_envnts)) if not_solve_envnts else '正常'
            rs.append(one_item)
        elif item == 'frameworks-tamper':
            not_solve_envnts = QueryAlarmEvent.from_strategy_get_events(strategy_obj, 'frameworks')
            one_item['item_name'] = '网页框架篡改'
            one_item['item_value'] = '异常，{}个篡改项'.format(
                QueryAlarmEvent.get_content_events_risk_num(not_solve_envnts)) if not_solve_envnts else '正常'
            rs.append(one_item)
    return rs


def _get_pc_item(strategy_obj):
    plans = EmergencyPlan.objects.filter(strategy=strategy_obj)
    rs = list()
    one_item = dict()
    for plan in plans:
        hosts = plan.hosts.all()
        for host in hosts:
            one_item['item_name'] = host.display_name
    rs.append(one_item)
    return rs


def _get_point_item(strategy_obj):
    task_histories = json.loads(strategy_obj.task_histories)
    credible_tasks = list(filter(lambda x: x['credible'] == 'yes', task_histories['tasks']))

    crawl_curr_site_name = 'none'
    if 'tasks' in task_histories and len(task_histories['tasks']) > 0:
        crawl_curr_site_name = task_histories['tasks'][-1]['crawl_site_name']

    crawl_site_name = 'none'
    credible_time = '--'
    if len(credible_tasks) > 0:
        credible_time = datetime.datetime.strptime(credible_tasks[-1]['date_time'],
                                                   xdatetime.FORMAT_WITH_SECOND_FOR_PATH).strftime(
            '%Y-%m-%d %H:%M:%S')
        crawl_site_name = credible_tasks[-1]['crawl_site_name']
    rs = list()
    one_item = dict()
    one_item['item_name'] = credible_time
    one_item['crawl_curr_site_name'] = crawl_curr_site_name
    one_item['crawl_site_name'] = crawl_site_name
    rs.append(one_item)
    return rs


def _check_is_need_strategy(c_level, need_level):
    if need_level == 'total':
        return True
    return c_level == need_level


def _Fmt_tamper_type(type):
    if type == 'sensitive_words-tamper':
        return '敏感词'
    if type == 'content-tamper':
        return '内容篡改'
    if type == 'pictures-tamper':
        return '网页图片篡改'
    if type == 'resources-tamper':
        return '网页下载资源篡改'
    if type == 'links-tamper':
        return '网页链接篡改'
    if type == 'frameworks-tamper':
        return '网页框架篡改'
    return type


def _get_em_log(request):
    params = request.GET
    st_id = params.get('st_id', None)
    filter_args = params.get('search_key', '')
    all_logs = AlarmEventLog.objects.all().order_by('-event_time')
    if st_id:
        all_logs = all_logs.filter(strategy__id=st_id)

    if filter_args:
        data = json.loads(filter_args)
        if 'group' in data:
            all_logs = all_logs.filter(strategy__group__name=data['group'])
        if 'st_time' in data:
            all_logs = all_logs.filter(
                event_time__gte=datetime.datetime.strptime(data['st_time'], xdatetime.FORMAT_WITH_USER_SECOND))
        if 'ed_time' in data:
            all_logs = all_logs.filter(
                event_time__lte=datetime.datetime.strptime(data['ed_time'], xdatetime.FORMAT_WITH_USER_SECOND))

    return all_logs


def get_em_events(request):
    params = request.GET
    page_rows = params.get('rows', '30')
    page_index = params.get('page', '1')
    paginator = Paginator(object_list=_get_em_log(request), per_page=page_rows)
    all_rows = paginator.count
    page_num = paginator.num_pages
    get_pagex = paginator.page(page_index).object_list

    ret = {'a': 'list', 'records': all_rows, 'total': page_num, 'page': page_index, 'rows': [], 'r': 0}
    for log in get_pagex:
        log_id = log.id
        log_time = log.event_time.strftime(xdatetime.FORMAT_WITH_USER_SECOND)
        log_status = log.get_log_type_display()
        log_description = _get_description(log)
        log_tamper_type = _Fmt_tamper_type(json.loads(log.detail).get('tamper-type', 'unknown'))
        st_name = log.strategy.name
        st_group_name = log.strategy.group.name
        ret['rows'].append(
            {'cell': [log_id, log_time, log_status, log_tamper_type, log_description, st_name, st_group_name],
             'id': log_id})

    sort_gird_rows(request, ret)
    return HttpResponse(json.dumps(ret, ensure_ascii=False))


def _get_description(event_log_obj):
    ex_detail = json.loads(event_log_obj.detail)
    if ex_detail.get('description', False):
        return ''.join(map(lambda x: '<p>{}</p>'.format(x), ex_detail['description']))
    else:
        return '<p>{}</p>'.format(event_log_obj.get_log_type_display())


def switch_site_mode(request):
    ident = request.POST.get('ident', 'none')
    mode = request.POST.get('mode', 'takeover')
    ret = {"r": 0, "e": "切换成功"}
    if mode == 'takeover':
        api_request = {"status": xdata.MAINTAIN_STATUS_TAKEOVER}
        mode_name = '应急模式'
    elif mode == 'normal':
        api_request = {"status": xdata.MAINTAIN_STATUS_NORMAL}
        mode_name = '正常模式'
    else:
        api_request = {"status": xdata.MAINTAIN_STATUS_UNKNOWN}
        mode_name = '未知'
    api_response = MaintainStatus().put(request, ident, api_request)
    ret["status_code"] = api_response.status_code
    if not status.is_success(api_response.status_code):
        ret = {"r": 1, "debug": "MaintainStatus().put() failed {}".format(api_response.status_code),
               "e": "切换失败，{}".format(get_response_error_string(api_response))}

    mylog = {'操作': '切换主机模式', '操作结果': ret['e'], '模式': mode_name, '主机': Host.objects.get(
        ident=ident).display_name}
    SaveOperationLog(request.user, OperationLog.TYPE_WEBGUARD, json.dumps(mylog, ensure_ascii=False))

    return HttpResponse(json.dumps(ret, ensure_ascii=False))


def _filter_hosts(s_key, *args):
    if not s_key:
        return True
    else:
        for arg in args:
            if arg.find(s_key) != -1:
                return True
        return False


def _getInfobyObj(request, itemObject):
    ident = itemObject.ident
    servername = itemObject.display_name
    ip_addresses = '--'
    serverlist = list()
    system_infos = None
    hosts = Host.objects.filter(ident=ident)
    for host_obj in hosts:
        _ext_info = json.loads(host_obj.ext_info)
        if 'system_infos' in _ext_info:
            system_infos = _ext_info['system_infos']
    if 'Nic' in system_infos:
        nics_info = system_infos['Nic']
        host_ethers = [{'ip_addresses': [ip['Ip'] for ip in nic['IpAndMask']]} for nic in nics_info]
        for item in host_ethers:
            if 'ip_addresses' in item:
                if ip_addresses == '--':
                    ip_addresses = '<br>'.join(item['ip_addresses'])
                else:
                    ip_addresses += '<br>' + '<br>'.join(item['ip_addresses'])
    if ip_addresses != '--':
        ip_addresses = '<br>'.join(list(filter(lambda x: x.strip(), ip_addresses.split('<br>'))))

    try:
        serverlist.append(itemObject.user.username)
    except Exception as e:
        pass

    online = '在线'
    if not itemObject.is_linked:
        online = '离线'
    mode = '未知'
    api_response = MaintainStatus().get(request, ident)
    if not status.is_success(api_response.status_code):
        mode = api_response.status_code
    else:
        mode = xdata.MAINTAIN_STATUS_TYPE[api_response.data['status']][1]

    imgurl = 'none'
    homepage_pic_path = '/var/www/static/web_guard/homepage_pic'
    filepath = os.path.join(homepage_pic_path, '{}.jpg'.format(ident))

    if os.path.isfile(filepath):
        imgurl = '/static/web_guard/homepage_pic/{}.jpg'.format(ident)

    webport = '--'
    havescrpit = 0
    stop_script = ''
    start_script = ''
    api_response = MaintainConfig().get(request, ident)
    if status.is_success(api_response.status_code):
        webport = ','.join("{0}".format(n) for n in api_response.data['ports'])
        if 'havescrpit' in api_response.data:
            havescrpit = api_response.data['havescrpit']
        if 'stop_script' in api_response.data:
            stop_script = html.escape(api_response.data['stop_script'])
        if 'start_script' in api_response.data:
            start_script = html.escape(api_response.data['start_script'])

    target = '--'
    targetlist = list()
    for host_obj in hosts:
        emergency_plan_obj = host_obj.emergency_plan.filter(deleted=False).all()
        for emergency_plan in emergency_plan_obj:
            obj = emergency_plan.strategy.all()
            for strategy in obj:
                if strategy.enabled:
                    targetlist.append('{}'.format(strategy.name))
                else:
                    targetlist.append('<span style="color:red">{}(禁用)</span>'.format(strategy.name))

    if len(targetlist) > 0:
        target = '<br>'.join(targetlist)
    return [ident, servername, ip_addresses, mode, online, webport, imgurl, target, havescrpit, stop_script,
            start_script]


def getclientlist(request):
    # 默认值
    page = 1
    rows = 30
    if 'page' in request.GET:
        page = int(request.GET['page'])
    if 'rows' in request.GET:
        rows = int(request.GET['rows'])

    host_id = None
    user_id = None
    search_key = request.GET.get('s_key', None)
    if 'hostid' in request.GET and request.GET['hostid'] != 'null':
        host_id = request.GET['hostid']

    if 'usedid' in request.GET and request.GET['usedid'] != 'null':
        user_id = request.GET['usedid']

    if request.user.is_superuser:
        if host_id:  # 查询指定的host
            hosts = Host.objects.filter(id=host_id)
        elif user_id:  # 从属于user的host
            hosts = Host.objects.filter(user_id=user_id)
        else:  # 所有的host
            hosts = Host.objects.filter()
    else:
        hosts = Host.objects.filter(user_id=request.user.id)
    # 过滤掉 验证的主机
    hosts = list(filter(lambda x: not x.is_verified, hosts))
    # 过滤掉 被删除的主机
    hosts = list(filter(lambda x: not x.is_deleted, hosts))

    rowList = list()
    for host in hosts:
        cell_info = _getInfobyObj(request, host)
        is_need = _filter_hosts(search_key, cell_info[1], cell_info[2])  # 用户搜索的过滤
        if is_need:
            detailDict = {'id': host.id, 'cell': _getInfobyObj(request, host)}
            rowList.append(detailDict)
        else:
            pass

    paginator = Paginator(rowList, rows)
    totalPlan = paginator.count
    totalPage = paginator.num_pages

    page = totalPage if page > totalPage else page
    currentObjs = paginator.page(page).object_list

    retInfo = {'r': 0, 'a': 'list', 'page': str(page), 'total': totalPage,
               'records': totalPlan, 'rows': currentObjs}
    sort_gird_rows(request, retInfo)
    jsonStr = json.dumps(retInfo, ensure_ascii=False)

    return HttpResponse(jsonStr)


# 确认一个策略是可信的
def confirm_one_strategy(request):
    _id = request.GET.get('id')
    strategy_obj = WebGuardStrategy.objects.get(id=_id)
    # 确认警告
    OpAlarmEvent.confirm_events(strategy_obj, AlarmEventLog.ALARM_EVENT_LOG_TYPE_CONFIRMED)
    # 更改策略的状态
    _update_strategy_status(strategy_obj)
    # 将历史task置为可信
    _confirm_history_task(strategy_obj)
    # 主机切换为正常模式
    _switch_host_to_normal(strategy_obj)

    mylog = {'操作': '确认策略为可信', '操作结果': '确认成功', '策略': show_names(WebGuardStrategy, (_id,))}
    SaveOperationLog(request.user, OperationLog.TYPE_WEBGUARD, json.dumps(mylog, ensure_ascii=False))

    return HttpResponse(json.dumps({'r': 0}))


def _update_strategy_status(strategy_obj):
    # 当主机的状态是 '发现篡改风险'
    if strategy_obj.present_status == WebGuardStrategy.WEB_EMERGENCY:
        strategy_obj.set_present_status(WebGuardStrategy.WEB_FORCE_TRUSTED)
    return None


def _confirm_history_task(strategy_obj):
    task_histories = json.loads(strategy_obj.task_histories)
    r_task = task_histories['tasks'][::-1]
    for task in r_task:
        # 最后的点本来就是可信，不做任何事情
        if task['credible'] == 'yes':
            _logger.debug('confirm_one_strategy, last task is already credible, nothing to do!')
            break
        # 最后的点不可信，添加信任
        if task['credible'] == 'no':
            _logger.debug('confirm_one_strategy, last task:{} is not credible, credible it!'.format(task['date_time']))
            task['credible'] = 'yes'
            break
    task_histories['tasks'] = r_task[::-1]
    strategy_obj.task_histories = json.dumps(task_histories)
    strategy_obj.force_credible = True
    strategy_obj.save(update_fields=['task_histories', 'force_credible'])

    return None


# TODO 没有考虑到 多个监控目标对应一个Host的情况
def _switch_host_to_normal(strategy_obj):
    all_plan = strategy_obj.emergency_plan.all()
    for plan in all_plan:
        as_hosts = plan.hosts.all()
        for host in as_hosts:
            api_request = {"status": xdata.MAINTAIN_STATUS_NORMAL}
            api_response = MaintainStatus().put(None, host.ident, api_request)
            if not status.is_success(api_response.status_code):
                _logger.error('_switch_host_to_normal fail, host:{}'.format(host.name))
    return None


def web_guard_start_restore(request):
    rs_dict = {'r': 0, 'e': '', 'info': ''}
    time_str = request.POST.get('time_str')
    st_id = request.POST.get('st_id')
    info, msg, _ = AcquireRestoreInfo(st_id).get_info()
    if msg:
        rs_dict['r'] = 1
        rs_dict['e'] = msg
    else:
        error_info = list()
        for item in info:
            api_request = {'type': xdata.SNAPSHOT_TYPE_CDP,
                           'host_ident': item['host_ident'],
                           'restore_time': datetime.datetime.strptime(item['restore_time'],
                                                                      xdatetime.FORMAT_WITH_SECOND_FOR_PATH)}
            rsp = HostSnapshotLocalRestore().post(None, item['snap_shot_id'], api_request)
            if not status.is_success(rsp.status_code):
                error_info.append(rsp.data if rsp.data else '未知错误')
        if error_info:
            rs_dict['r'] = 1
            rs_dict['e'] = '<br>'.join(error_info)

    mylog = {'操作': '执行还原', '操作结果': rs_dict['e'], '应急策略': show_names(EmergencyPlan, (st_id,))}
    SaveOperationLog(request.user, OperationLog.TYPE_WEBGUARD, json.dumps(mylog, ensure_ascii=False))

    return HttpResponse(json.dumps(rs_dict, ensure_ascii=False))


def get_log_detail(request):
    id = int(request.POST.get('id'))
    log = AlarmEventLog.objects.filter(id=id).first()
    img_base = ''
    img_current = ''
    txt_base = ''
    txt_current = ''
    type = None
    lhs = None
    rhs = None
    if log:
        detail = json.loads(log.detail)
        tamper_type = detail['tamper-type']
        if 'res' in detail:
            if tamper_type in ('content-tamper', 'frameworks-tamper',):
                if 'res_base' in detail['res']:
                    txt_base = detail['res']['res_base']
                    pos = txt_base.find('/static/')
                    if txt_base[-3:] == 'jpg' and pos != -1:
                        img_base = txt_base[pos:]
                        txt_base = txt_base[0:-3] + 'html'
                if 'res_current' in detail['res']:
                    txt_current = detail['res']['res_current']
                    pos = txt_current.find('/static/')
                    if txt_current[-3:] == 'jpg' and pos != -1:
                        img_current = txt_current[pos:]
                        txt_current = txt_current[0:-3] + 'html'
            if tamper_type in ('pictures-tamper',):
                if 'res_base' in detail['res']:
                    txt_base = detail['res']['res_base']
                    pos = txt_base.find('/static/')
                    if pos != -1:
                        img_base = txt_base[pos:]
                if 'res_current' in detail['res']:
                    txt_current = detail['res']['res_current']
                    pos = txt_current.find('/static/')
                    if pos != -1:
                        img_current = txt_current[pos:]

        if tamper_type in ('content-tamper',):
            if img_base[-3:] == 'jpg' and img_current[-3:] == 'jpg':
                type = 'txt_image'
            else:
                type = 'txt'

        if tamper_type in ('pictures-tamper',):
            type = 'image'

        if tamper_type in ('frameworks-tamper',):
            type = 'txt'

    if type in ('txt', 'txt_image',):
        code, lhs = CWebsiteMonitor('')._read_all_file(txt_base)
        code, rhs = CWebsiteMonitor('')._read_all_file(txt_current)

    if type is not None:
        if txt_base or txt_current:
            ret_json = {"r": 0, "e": "操作成功", "type": type, "img_base": img_base, "img_current": img_current, "lhs": lhs,
                        "rhs": rhs, 'tamper_type': tamper_type}
        else:
            ret_json = {"r": 1, "e": "找不到指定的文件"}
    else:
        ret_json = {"r": 1, "e": "暂不支持的类型"}

    return HttpResponse(json.dumps(ret_json, ensure_ascii=False))


def get_words_list(request):
    filter = None
    page = 1
    rows = 30
    if 'page' in request.GET:
        page = int(request.GET['page'])
    if 'rows' in request.GET:
        rows = int(request.GET['rows'])
    limit = rows
    offset = (page - 1) * rows
    SensitiveWordsAPI = CSensitiveWordsAPI()
    total = SensitiveWordsAPI.get_sensitive_word_count()
    totalPage = math.ceil(total / rows)
    words = SensitiveWordsAPI.get_sensitive_word_list(filter, limit, offset)
    rows = list()
    for word in words:
        rows.append({'cell': [word.word, word.is_dirty], 'id': word.id})

    retInfo = {'r': 0, 'a': 'list', 'page': str(page), 'total': totalPage, 'records': total, 'rows': rows}
    sort_gird_rows(request, retInfo)
    jsonStr = json.dumps(retInfo, ensure_ascii=False)

    return HttpResponse(jsonStr)


def import_words_list(request):
    importtype = int(request.POST.get('importtype'))
    myfile = request.FILES.get("importFile", None)
    if not myfile:
        return HttpResponse(json.dumps({"r": 1, "e": "无上传文件"}, ensure_ascii=False))

    fileobj = myfile.read()
    filestr = None
    try:
        filestr = fileobj.decode('utf-8')
    except Exception as e:
        pass

    try:
        filestr = fileobj.decode('gb2312')
    except Exception as e:
        pass

    if filestr == None:
        return HttpResponse(json.dumps({"r": 2, "e": "读取文件失败，请上传utf-8格式的文件"}, ensure_ascii=False))

    SensitiveWordsAPI = CSensitiveWordsAPI()

    words = filestr.splitlines()
    if len(words) == 0:
        return HttpResponse(json.dumps({"r": 3, "e": "空文件"}, ensure_ascii=False))

    if importtype == 1:
        # 全量导入
        SensitiveWordsAPI.del_all_sensitive_word()

    for word in words:
        SensitiveWordsAPI.add_sensitive_word(word.strip())

    SensitiveWordsAPI.sign_dirty_words()
    r, e = SensitiveWordsAPI.gen_sensitive_word_bin()
    return HttpResponse(
        json.dumps({"r": r, "e": e, "importtype": importtype}, ensure_ascii=False))


def _delexpirelog(exportpath):
    ctime = time.time()
    for dirpath, dirnames, filenames in os.walk(exportpath):
        for filename in filenames:
            thefile = os.path.join(dirpath, filename)
            if os.path.splitext(thefile)[1] in ('.zip', '.txt',):
                mtime = os.path.getmtime(thefile)
                if ctime - mtime > 2 * 60 * 60:
                    os.remove(thefile)


def export_words_list(request):
    exportpath = r'/var/www/static/web_guard/words/'
    try:
        os.makedirs(exportpath)
    except OSError as e:
        pass
    _delexpirelog(exportpath)
    timestr = datetime.datetime.now().strftime(xdatetime.FORMAT_WITH_SECOND)
    filename = xdata.PREFIX_WORDS_FILE + timestr + '.zip'
    filepath = os.path.join(exportpath, filename)
    txtpath = os.path.join(exportpath, 'words.txt')

    SensitiveWordsAPI = CSensitiveWordsAPI()
    limit = SensitiveWordsAPI.get_sensitive_word_count()
    filter = None
    offset = 0
    words = SensitiveWordsAPI.get_sensitive_word_list(filter, limit, offset)
    try:
        file_object = open(txtpath, 'w')
        for word in words:
            file_object.writelines(word.word)
            file_object.writelines('\r\n')
        file_object.close()
    except Exception as e:
        pass

    if not os.path.isfile(txtpath):
        return HttpResponse(json.dumps({"r": 1, "e": "生成文件失败"}, ensure_ascii=False))

    z = zipfile.ZipFile(filepath, 'w')
    z.write(txtpath, 'words.txt')
    z.close()

    os.remove(txtpath)

    return HttpResponse(
        '{"r": "0","e": "操作成功","url":"/static/web_guard/words/%s","filename":"%s"}' % (filename, filename))


def execute_plan(request):
    re_dict = {'r': 0, 'e': '操作成功'}
    plan_id = request.GET.get('id')
    level = request.GET.get('level')
    _type = request.GET.get('type')
    if int(_type) == EmergencyPlan.EM_AUTO:
        rs_info = json.loads(request.GET.get('restore_info'))
        for info in rs_info:
            api_request = {'host_ident': info['host_ident'], 'plan_id': plan_id, 'is_auto': False,
                           'restore_time': info['restore_time'], 'snapshot_id': info['snapshot_id']
                           }
            rsp = WGRPreLogic().post(None, api_request)
            if rsp.status_code == status.HTTP_201_CREATED:
                WGRPreLogic.clear_strategy_last_404(plan_id)
                _logger.info('start WGRLogic id:{} ok'.format(plan_id))
            else:
                re_dict['r'] = 1
                re_dict['e'] = rsp.data if rsp.data else '执行失败'
                _logger.warning('start WGRLogic id:{} failed {}'
                                .format(plan_id, rsp.status_code))
                return HttpResponse(json.dumps(re_dict, ensure_ascii=False))
    else:
        args = {"level": level, "type": _type}
        rsp = EmergencyPlanExecute().post(request=None, plan_id=plan_id, api_request=args)
        if not status.is_success(rsp.status_code):
            re_dict['r'] = 1
            re_dict['e'] = rsp.data if rsp.data else '执行失败'

    mylog = {'操作': '执行应急策略', '操作结果': re_dict['e'], '应急策略': show_names(EmergencyPlan, (plan_id,))}
    SaveOperationLog(request.user, OperationLog.TYPE_WEBGUARD, json.dumps(mylog, ensure_ascii=False))

    return HttpResponse(json.dumps(re_dict, ensure_ascii=False))


# 执行还原前需要先得到确认的信息
def get_restore_confirm_info(request):
    re_dict = {'r': 0, 'e': '', 'info': list()}
    plan_id = request.GET.get('id')
    info = AcquireRestoreInfo(plan_id).get_info()
    re_dict['info'] = info
    return HttpResponse(json.dumps(re_dict, ensure_ascii=False))


def upload_pic(request):
    myfile = request.FILES.get("importFile", None)
    host_ident = request.POST.get('host_ident')
    if not myfile:
        return HttpResponse(json.dumps({"r": 1, "e": "无上传文件"}, ensure_ascii=False))

    org_filename = myfile.name
    ext = os.path.splitext(org_filename)[1].lower()
    if ext not in ('.jpg', '.jpeg',):
        return HttpResponse(json.dumps({"r": 2, "e": "请上传jpg格式的图片", "ext": ext}, ensure_ascii=False))

    homepage_pic_path = '/var/www/static/web_guard/homepage_pic'
    try:
        os.makedirs(homepage_pic_path)
    except OSError as e:
        pass
    r = 0
    ret_e = '操作成功'
    data_stream = myfile.read()
    for indent in host_ident.split(","):
        filepath = os.path.join(homepage_pic_path, '{}.jpg'.format(indent))
        if os.path.isfile(filepath):
            os.remove(filepath)
        try:
            destination = open(filepath, 'wb')  # 打开特定的文件进行二进制的写操作
            destination.write(data_stream)
            destination.close()
        except Exception as e:
            ret_e = str(e)
            r = 1

    mylog = {'操作': '上传应急页面图片', '操作结果': ret_e, '文件': myfile.name}
    SaveOperationLog(request.user, OperationLog.TYPE_WEBGUARD, json.dumps(mylog, ensure_ascii=False))
    return HttpResponse(json.dumps({"r": r, "e": ret_e}, ensure_ascii=False))


def set_client_site_port(request):
    ident = request.POST.get('id', 0)
    webport = request.POST.get('webport', '')
    havescrpit = int(request.POST.get('havescrpit', 0))
    stop_script = request.POST.get('stop_script', '')
    start_script = request.POST.get('start_script', '')
    r = 0
    e = '操作成功'
    debug = ''

    api_request = dict()
    ports = list()
    try:
        for port in webport.split(","):
            ports.append(int(port))
    except Exception as e:
        r = 4
        return HttpResponse(json.dumps({"r": r, "e": str(e)}, ensure_ascii=False))

    api_request['ports'] = ports
    api_request['havescrpit'] = havescrpit
    api_request['stop_script'] = stop_script
    api_request['start_script'] = start_script

    if len(ports) == 0:
        r = 3
        e = '端口不能为空'
        return HttpResponse(json.dumps({"r": r, "e": e}, ensure_ascii=False))

    host_object = Host.objects.filter(ident=ident).first()
    if not host_object:
        r = 1
        e = '找不到客户端,ident={}'.format(ident)
        return HttpResponse(json.dumps({"r": r, "e": e}, ensure_ascii=False))

    api_response = MaintainConfig().put(request, ident, api_request)
    if not status.is_success(api_response.status_code):
        r = 2
        debug = 'MaintainConfig().put Failed.Code={}'.format(api_response.status_code)
        e = get_response_error_string(api_response)

    mylog = {'操作': '编辑主页应急切换', '操作结果': "编辑成功", '客户端': host_object.display_name, '端口': ports}
    SaveOperationLog(request.user, OperationLog.TYPE_WEBGUARD, json.dumps(mylog, ensure_ascii=False))

    return HttpResponse(json.dumps({"r": r, "e": e, "debug": debug}, ensure_ascii=False))


# 当前所有可用, 内容管理员, 属于本用户的监控目标
def avail_monitors_admins(request):
    monitors = WebGuardStrategy.objects.filter(deleted=False, user=request.user)
    content_admins = User.objects.filter(is_superuser=False, is_active=True,
                                         userprofile__user_type=UserProfile.CONTENT_ADMIN)
    monitors = [{'name': monitor.name, 'value': monitor.id} for monitor in monitors]
    content_admins = [{'name': admin.username, 'value': admin.id} for admin in content_admins]

    return HttpResponse(json.dumps({'monitors': monitors, 'admins': content_admins}))


# 创建一个ModifyEntry
def create_entry(request):
    params = request.GET
    entry = params['entry']
    monitors = params['monitors-id'].split(',')
    content_admins = params['content-admins-ids'].split(',')
    modify_entry = ModifyEntry.objects.create(entrance=entry)
    modify_entry.monitors = monitors
    modify_entry.modify_admin = content_admins

    mylog = {'操作': '内容管理员设置--添加关联', '操作结果': "添加成功", '内容管理员': show_names(User, content_admins), '监控目标': show_names(
        WebGuardStrategy, monitors)}
    SaveOperationLog(request.user, OperationLog.TYPE_WEBGUARD, json.dumps(mylog, ensure_ascii=False))
    return HttpResponse(json.dumps({'r': '0', 'e': ''}))


# 本用户(普通用户)关联的监控目标, 其关联的ModifyEntry
def query_all_entries(request):
    params = request.GET
    page_rows = params.get('rows', '10')
    page_index = params.get('page', '1')

    all_entries = list(set(ModifyEntry.objects.filter(monitors__user=request.user)))
    paginator = Paginator(object_list=all_entries, per_page=page_rows)
    all_rows = paginator.count
    page_num = paginator.num_pages
    get_pagex = paginator.page(page_index).object_list

    ret = {'a': 'list', 'records': all_rows, 'total': page_num, 'page': page_index, 'rows': [], 'r': 0}
    for obj in get_pagex:
        monitors = '<br>'.join([monitor.name for monitor in obj.monitors.all() if not monitor.deleted])
        admins = '<br>'.join([admin.username for admin in obj.modify_admin.all() if admin.is_active])
        ret['rows'].append({'cell': [obj.id, obj.entrance, monitors, admins], 'id': obj.id})

    sort_gird_rows(request, ret)
    return HttpResponse(json.dumps(ret))


def delete_entries(request):
    params = request.GET
    entries = params['id'].split(',')
    ModifyEntry.objects.filter(id__in=entries).delete()

    mylog = {'操作': '内容管理员设置--删除关联', '操作结果': "删除成功", '删除ID': entries}
    SaveOperationLog(request.user, OperationLog.TYPE_WEBGUARD, json.dumps(mylog, ensure_ascii=False))
    return HttpResponse(json.dumps({'r': '0', 'e': ''}))


def add_ignore_area(request):
    params = request.GET
    url = params.get('url', '')
    ignore = params.get('ignore', '')
    try:
        re.compile(ignore)
    except Exception as e:
        return HttpResponse(json.dumps({'r': '1', 'e': '正则表达式不正确。'}, ensure_ascii=False))
    CModel_ignore_url_area().add_ignore_url_area(url, ignore)

    mylog = {'操作': '添加网页忽略区域', '操作结果': "添加成功", '忽略URL': url, '正则表达式': ignore}
    SaveOperationLog(request.user, OperationLog.TYPE_WEBGUARD, json.dumps(mylog, ensure_ascii=False))
    return HttpResponse(json.dumps({'r': '0', 'e': ''}))


def get_ignore_list(request):
    filter = None
    page = 1
    rows = 30
    if 'page' in request.GET:
        page = int(request.GET['page'])
    if 'rows' in request.GET:
        rows = int(request.GET['rows'])
    limit = rows
    offset = (page - 1) * rows
    IgnoreUrlArea = CModel_ignore_url_area()
    total = IgnoreUrlArea.get_count(filter)
    totalPage = math.ceil(total / rows)
    relist = IgnoreUrlArea.get_all_list(filter, limit, offset)
    rows = list()
    for onere in relist:
        rows.append(
            {'cell': [onere[0].url, html.escape(onere[1].ignore), onere[2].ignore_url_id, onere[2].ignore_area_id],
             'id': '{}_{}'.format(onere[2].ignore_url_id, onere[2].ignore_area_id)})

    retInfo = {'r': 0, 'a': 'list', 'page': str(page), 'total': totalPage, 'records': total, 'rows': rows}
    sort_gird_rows(request, retInfo)
    jsonStr = json.dumps(retInfo, ensure_ascii=False)

    return HttpResponse(jsonStr)


def delete_ingore(request):
    params = request.GET
    ids = params.get('ids', '')
    IgnoreUrlArea = CModel_ignore_url_area()
    for id in ids.split(","):
        oneid = id.split('_')
        if len(oneid) == 2:
            ignore_url_id = oneid[0]
            ignore_area_id = oneid[1]
            IgnoreUrlArea.del_ignore_url_area(ignore_url_id, ignore_area_id)

    mylog = {'操作': '删除网页忽略区域', '操作结果': "删除成功", '删除ID': ids}
    SaveOperationLog(request.user, OperationLog.TYPE_WEBGUARD, json.dumps(mylog, ensure_ascii=False))

    return HttpResponse(json.dumps({'r': '0', 'e': ''}))


def re_test(request):
    params = request.POST
    content = params.get('content', '')
    s_re = params.get('re', '')

    try:
        p = re.compile(s_re)
        content = p.sub(r'', content)
    except Exception as e:
        return HttpResponse(json.dumps({'r': '1', 'e': str(e)}, ensure_ascii=False))

    return HttpResponse(json.dumps({'r': '0', 'e': '', 'content': content}))


def _FmtResourceType(fmt_type):
    if fmt_type == 'url':
        return '网页'
    if fmt_type == 'css':
        return '层叠样式表（css）'
    if fmt_type == 'image':
        return '图片'
    if fmt_type == 'js':
        return 'JScript'
    return fmt_type


def get_webpage_list(request):
    params = request.GET
    page = int(params.get('page', 1))
    rows = int(params.get('rows', 30))
    sitename = params.get('crawl_site_name', 'none')
    limit = rows
    offset = (page - 1) * rows
    siteSpiderAPI = CSiteSpiderAPI()
    total = siteSpiderAPI.countWebsitePages(sitename)
    totalPage = math.ceil(total / rows)
    filter = None
    pagelist = siteSpiderAPI.getWebsitePage(sitename, limit, offset, filter)
    rows = list()
    for onepage in pagelist:
        title = siteSpiderAPI.get_page_title(onepage[0].id)
        if title == 'none':
            title = _FmtResourceType(onepage[0].resourceType)
        rows.append({'cell': [title, onepage[0].link, onepage[0].depth, onepage[0].path]})

    retInfo = {'r': 0, 'a': 'list', 'page': str(page), 'total': totalPage, 'records': total, 'rows': rows}
    jsonStr = json.dumps(retInfo, ensure_ascii=False)

    return HttpResponse(jsonStr)


def delexpirelog(exportpath):
    ctime = time.time()
    for dirpath, dirnames, filenames in os.walk(exportpath):
        for filename in filenames:
            thefile = os.path.join(dirpath, filename)
            if os.path.splitext(thefile)[1] in ('.zip', '.xls', '.json'):
                mtime = os.path.getmtime(thefile)
                if ctime - mtime > 2 * 60 * 60:
                    os.remove(thefile)


def SaveOperationLog(user, event, desc):
    try:
        OperationLog.objects.create(user=user, event=event, desc=desc)
    except Exception as e:
        _logger.info(str(e))


def exportLog(request):
    exportpath = os.path.join('/var/www/static', 'exportlog')
    delexpirelog(exportpath)
    startpage = int(request.GET.get('startpage', 1) if request.GET.get('startpage', 1) else 1)
    endpage = int(request.GET.get('endpage', 1) if request.GET.get('endpage', 1) else 1)
    rows = int(request.GET.get('rows', 30) if request.GET.get('rows', 30) else 30)
    iMaxRow = int(request.GET.get('maxrow', '5000') if request.GET.get('maxrow', '5000') else 5000)

    if startpage <= 0:
        startpage = 1
    if endpage <= 0:
        endpage = 1

    timestr = datetime.datetime.now().strftime(xdatetime.FORMAT_WITH_SECOND)

    try:
        os.makedirs(exportpath)
    except OSError as e:
        pass
    filename = xdata.PREFIX_LOG_WEBGUARD + timestr + '.zip'
    filepath = os.path.join(exportpath, filename)

    paginator = Paginator(_get_em_log(request), rows)
    totalPage = paginator.num_pages
    irow = 0
    xlpatharr = list()
    for page in range(startpage, endpage + 1, 1):
        if page > totalPage:
            break;
        currentObjs = paginator.page(page).object_list
        for log in currentObjs:
            log_time = log.event_time.strftime(xdatetime.FORMAT_WITH_USER_SECOND)
            log_status = log.get_log_type_display()
            log_description = _get_description(log)
            log_tamper_type = _Fmt_tamper_type(json.loads(log.detail).get('tamper-type', 'unknown'))
            st_name = log.strategy.name
            st_group_name = log.strategy.group.name

            if irow == 0:
                wb = Workbook()
                ws = wb.add_sheet('Sheet1')
                ws.write(0, 0, '时间')
                ws.write(0, 1, '风险状态')
                ws.write(0, 2, '检测项目')
                ws.write(0, 3, '事件描述')
                ws.write(0, 4, '监控目标')
                ws.write(0, 5, '组别')
            ws.write(irow + 1, 0, log_time)
            ws.write(irow + 1, 1, log_status)
            ws.write(irow + 1, 2, log_tamper_type)
            log_description = log_description.replace(r'</p><p>', r' ')
            log_description = log_description.replace(r'<p>', r'')
            log_description = log_description.replace(r'</p>', r'')
            ws.write(irow + 1, 3, log_description)
            ws.write(irow + 1, 4, st_name)
            ws.write(irow + 1, 5, st_group_name)
            irow += 1
            if irow >= iMaxRow:
                tmppath = timestr + "-" + str(len(xlpatharr) + 1) + 'log.xls'
                xlpath = os.path.join(exportpath, tmppath)
                xlpatharr.append(xlpath)
                wb.save(xlpath)
                irow = 0

    if irow % iMaxRow != 0:
        tmppath = timestr + "-" + str(len(xlpatharr) + 1) + 'log.xls'
        xlpath = os.path.join(exportpath, tmppath)
        xlpatharr.append(xlpath)
        wb.save(xlpath)

    z = zipfile.ZipFile(filepath, 'w')
    i = 0
    for xlpath in xlpatharr:
        i += 1
        z.write(xlpath, '网页防护日志-' + str(i) + '.xls')
    z.close()

    for xlpath in xlpatharr:
        os.remove(xlpath)

    mylog = {'操作': '导出网站防护日志', '操作结果': "导出成功"}
    SaveOperationLog(request.user, OperationLog.TYPE_WEBGUARD, json.dumps(mylog, ensure_ascii=False))
    return HttpResponse('{"r": "0","e": "操作成功","url":"/static/exportlog/%s","filename":"%s"}' % (filename, filename))
