import json
import uuid
import os
import base64
import shutil
from django.http import HttpResponse
from box_dashboard import functions
from box_dashboard import xlogging, xdatetime, xdata
from apiv1.models import AutoVerifySchedule, StorageNode, Host, AutoVerifyTask
from xdashboard.handle.logserver import SaveOperationLog
from xdashboard.models import OperationLog
from apiv1.models import HostSnapshot
from xdashboard.request_util import get_operator
from django.core.paginator import Paginator
from apiv1.views import HostSnapshotsWithNormalPerHost, HostSnapshotsWithCdpPerHost, get_response_error_string
from rest_framework import status
import django.utils.timezone as timezone
from datetime import timedelta
import calendar
from apiv1.logic_processors import BackupTaskScheduleLogicProcessor
from apiv1.models import HostGroup
from xdashboard.common.license import is_functional_available
from xdashboard.common.license import get_functional_int_value
from xdashboard.models import auto_verify_script
from django.core.paginator import Paginator
from django.utils.encoding import escape_uri_path
from urllib.parse import quote
import zipfile
import threading

_lock = threading.RLock()
router = functions.Router(globals())
_logger = xlogging.getLogger(__name__)


def _get_calendar_week(week):
    if week == 1:
        return calendar.MONDAY
    elif week == 2:
        return calendar.TUESDAY
    elif week == 3:
        return calendar.WEDNESDAY
    elif week == 4:
        return calendar.THURSDAY
    elif week == 5:
        return calendar.FRIDAY
    elif week == 6:
        return calendar.SATURDAY
    elif week == 7:
        return calendar.SUNDAY
    _logger.info('_get_calendar_week Failed.week={}'.format(week))
    return calendar.MONDAY


def createtask(request):
    taskid = request.POST.get('taskid')
    taskname = request.POST.get('taskname')
    storage_node_ident = request.POST.get('storagedevice')
    cycle_type = int(request.POST.get('schedule'))  # 2仅验证一次 3按间隔时间 4每周 5每月
    starttime = request.POST.get('starttime')
    timeinterval = request.POST.get('timeinterval')
    intervalUnit = request.POST.get('intervalUnit')
    perweek = request.POST.getlist('perweek', default=[])
    perweek = list(map(int, perweek))
    monthly = request.POST.getlist('monthly', default=[])
    monthly = list(map(int, monthly))
    verify_osname = int(request.POST.get('verify_osname', 0))
    verify_osver = int(request.POST.get('verify_osver', 0))
    verify_hdd = int(request.POST.get('verify_hdd', 0))
    last_point = int(request.POST.get('last_point', 1))  # 只验证最后一个点
    if timeinterval:
        backupDayInterval = int(timeinterval) * {'min': 60, 'hour': 3600, 'day': 24 * 3600}[intervalUnit]
    else:
        # 不参与运算，只是为了调用calc_next_run不出错
        backupDayInterval = 0

    script1 = request.POST.get('script1')
    script2 = request.POST.get('script2')
    script3 = request.POST.get('script3')
    script4 = request.POST.get('script4')
    script5 = request.POST.get('script5')
    script6 = request.POST.get('script6')
    script7 = request.POST.get('script7')
    script8 = request.POST.get('script8')

    kvm_memory_size = request.POST.get('kvm_memory_size')
    kvm_memory_unit = request.POST.get('kvm_memory_unit')

    script_list = list()
    script_list.append(script1)
    script_list.append(script2)
    script_list.append(script3)
    script_list.append(script4)
    script_list.append(script5)
    script_list.append(script6)
    script_list.append(script7)
    script_list.append(script8)

    result = {'r': 0, 'e': '操作成功'}

    ext_config = dict()
    ext_config['timeinterval'] = timeinterval
    ext_config['IntervalUnit'] = intervalUnit
    ext_config['daysInWeek'] = perweek
    ext_config['daysInMonth'] = monthly
    ext_config['backupDayInterval'] = backupDayInterval
    ext_config['script_list'] = script_list
    ext_config['kvm_memory_size'] = kvm_memory_size
    ext_config['kvm_memory_unit'] = kvm_memory_unit
    if last_point == 1:
        ext_config['verify_last_point_only'] = True
    else:
        ext_config['verify_last_point_only'] = False
    ext_config['verify_osname'] = True if verify_osname == 1 else False
    ext_config['verify_osver'] = True if verify_osver == 1 else False
    ext_config['verify_hdd'] = True if verify_hdd == 1 else False

    if not is_functional_available('auto_verify_task'):
        result = {'r': 1, 'e': '没有授权，请联系管理员'}
        return HttpResponse(json.dumps(result, ensure_ascii=False))

    count = get_functional_int_value('auto_verify_task')
    sc_count = AutoVerifySchedule.objects.all().count()
    if sc_count >= count:
        result = {'r': 1, 'e': '自动验证授权数为{}，当前已用{}'.format(count, sc_count)}
        return HttpResponse(json.dumps(result, ensure_ascii=False))

    if taskid is None:
        verify_schedule = AutoVerifySchedule.objects.create(user=request.user,
                                                            name=taskname,
                                                            plan_start_date=starttime,
                                                            cycle_type=cycle_type,
                                                            storage_node_ident=storage_node_ident,
                                                            ext_config=json.dumps(ext_config, ensure_ascii=False))
        verify_schedule_id = verify_schedule.id
        desc = {'操作': '自动验证计划', '计划名称': taskname}
        SaveOperationLog(request.user, OperationLog.TYPE_AUTO_VERIFY_TASK, json.dumps(desc, ensure_ascii=False),
                         get_operator(request))
    else:
        verify_schedule_id = taskid
        AutoVerifySchedule.objects.filter(id=taskid).update(name=taskname, plan_start_date=starttime,
                                                            cycle_type=cycle_type,
                                                            storage_node_ident=storage_node_ident,
                                                            ext_config=json.dumps(ext_config, ensure_ascii=False))
        desc = {'操作': '更改自动验证计划', '计划名称': taskname}
        SaveOperationLog(request.user, OperationLog.TYPE_AUTO_VERIFY_TASK, json.dumps(desc, ensure_ascii=False),
                         get_operator(request))

    verify_schedule = AutoVerifySchedule.objects.get(id=verify_schedule_id)
    logicProcessor = BackupTaskScheduleLogicProcessor(verify_schedule)
    verify_schedule.next_run_date = logicProcessor.calc_next_run(True)
    verify_schedule.save(update_fields=['next_run_date'])

    return HttpResponse(json.dumps(result, ensure_ascii=False))


def _get_verify_detail(ext_config):
    verify = list()
    verify.append('操作系统能否启动')
    verify.append('网络状态')
    if ext_config['verify_osname']:
        verify.append('客户端名称')
    if ext_config['verify_osver']:
        verify.append('操作系统版本')
    if ext_config['verify_hdd']:
        verify.append('硬盘（分区结构、容量、已使用量）')
    return verify


def _get_verify(ext_config):
    verify = list()
    verify.append('verify_start')
    verify.append('verify_network')
    if ext_config['verify_osname']:
        verify.append('verify_osname')
    if ext_config['verify_osver']:
        verify.append('verify_osver')
    if ext_config['verify_hdd']:
        verify.append('verify_hdd')
    return verify


def get_task_list(request):
    page = int(request.GET.get('page', 1))
    rows = int(request.GET.get('rows', 30))

    tasks = AutoVerifySchedule.objects.filter(user_id=request.user.id)
    paginator = Paginator(tasks, rows)
    totalPlan = paginator.count
    totalPage = paginator.num_pages

    page = totalPage if page > totalPage else page
    currentObjs = paginator.page(page).object_list
    rowList = list()
    for Obj in currentObjs:
        ext_config = json.loads(Obj.ext_config)
        verify = _get_verify_detail(ext_config)
        if Obj.enabled:
            status = '启用'
        else:
            status = '禁用'
        if Obj.last_run_date:
            last_run_date = Obj.last_run_date.strftime(xdatetime.FORMAT_WITH_SECOND)
        else:
            last_run_date = '-'
        if Obj.next_run_date:
            next_run_date = Obj.next_run_date.strftime(xdatetime.FORMAT_WITH_SECOND)
        else:
            next_run_date = '-'
        detailDict = {'id': Obj.id,
                      'cell': [Obj.id, Obj.name, '<br>'.join(verify), status, last_run_date, next_run_date]}
        rowList.append(detailDict)

    result = {'r': 0, 'page': str(page), 'total': totalPage, 'records': totalPlan, 'rows': rowList}

    return HttpResponse(json.dumps(result, ensure_ascii=False))


def get_task_detail(request):
    id = request.GET.get('taskid', 0)
    task = AutoVerifySchedule.objects.filter(id=id).first()
    result = {'r': 0, 'e': '操作成功'}
    result['taskname'] = task.name
    storagedevice, storagedeviceid = '未分配', -1
    storage_node_ident = task.storage_node_ident
    sd = StorageNode.objects.filter(ident=storage_node_ident)
    if sd:
        storagedevice = sd[0].name
        storagedeviceid = storage_node_ident
    result['storagedevice'] = {'name': storagedevice, 'value': storagedeviceid}
    result['createtime'] = task.created.strftime(xdatetime.FORMAT_WITH_SECOND)
    ext_config = json.loads(task.ext_config)
    verify = _get_verify_detail(ext_config)
    result['backuptype'] = '<br>'.join(verify)
    result['verify'] = ','.join(_get_verify(ext_config))
    period = None
    # 2仅验证一次 3按间隔时间 4每周 5每月
    if task.cycle_type == 3:
        # 按间隔时间
        period = ext_config['timeinterval']
    elif task.cycle_type == 4:
        # 每周
        period = ','.join(list(map(str, ext_config['daysInWeek'])))
    elif task.cycle_type == 5:
        # 每月
        period = ','.join(list(map(str, ext_config['daysInMonth'])))

    host_list = list()

    for host in task.hosts.all():
        host_list.append(host.name)

    for host_group in task.host_groups.all():
        host_list.append(host_group.name)

    kvm_memory_size = ext_config.get('kvm_memory_size', 512)
    kvm_memory_unit = ext_config.get('kvm_memory_unit', 'MB')

    result['schedule'] = {"type": task.cycle_type, "verify_last_point_only": ext_config['verify_last_point_only'],
                          'starttime': task.plan_start_date.strftime(xdatetime.FORMAT_WITH_USER_SECOND),
                          'period': period, 'unit': ext_config['IntervalUnit'], 'kvm_memory_size': kvm_memory_size,
                          'kvm_memory_unit': kvm_memory_unit}
    result['hosts'] = '<br>'.join(host_list)
    return HttpResponse(json.dumps(result, ensure_ascii=False))


def get_task_host_list(request):
    task_id = request.GET.get('task_id', 0)
    host_type = 'host'
    task = AutoVerifySchedule.objects.filter(id=task_id).first()
    result = {'r': 0, 'e': '操作成功'}

    host_list = list()

    for host in task.hosts.all():
        host_list.append(host.ident)

    for host_group in task.host_groups.all():
        host_list.append('group_{}'.format(host_group.id))
        host_type = 'group'

    result['hosts'] = ','.join(list(map(str, host_list)))
    result['host_type'] = host_type
    return HttpResponse(json.dumps(result, ensure_ascii=False))


def add_to_task(request):
    task_id = int(request.POST.get('task_id', 0))
    host_ids = json.loads(request.POST.get('host_ids'))
    result = {'r': 0, 'e': '操作成功'}

    host_name_list = list()

    task = AutoVerifySchedule.objects.get(id=task_id)
    task.hosts.clear()
    task.host_groups.clear()

    for host_id in host_ids:
        if host_id.startswith('group_'):
            host_group = HostGroup.objects.get(id=host_id[6:])
            host_name_list.append(host_group.name)
            task.host_groups.add(host_group)
        else:
            host = Host.objects.get(ident=host_id)
            host_name_list.append(host.name)
            task.hosts.add(host)

    desc = {'操作': '加入自动验证计划', '客户端名称': host_name_list, '自动验证计划名称': task.name}
    SaveOperationLog(request.user, OperationLog.TYPE_AUTO_VERIFY_TASK, json.dumps(desc, ensure_ascii=False),
                     get_operator(request))
    return HttpResponse(json.dumps(result, ensure_ascii=False))


def _del_queue_tasks(schedule_id_list):
    need_del_task_id_list = list()
    tasks = AutoVerifyTask.objects.filter(verify_type=AutoVerifyTask.VERIFY_TYPE_QUEUE).all()
    for task in tasks:
        if json.loads(task.schedule_ext_config).get('verify_schedule_id', 0) in schedule_id_list:
            need_del_task_id_list.append(task.id)
    for task_id in need_del_task_id_list:
        AutoVerifyTask.objects.filter(id=task_id).delete()


def deltask(request):
    task_ids = request.GET.get('task_id', 0)
    result = {'r': 0, 'e': '操作成功'}
    task_name_list = list()
    task_id_list = task_ids.split(',')
    for task_id in task_id_list:
        task = AutoVerifySchedule.objects.filter(id=task_id)
        task_name_list.append(task.first().name)
        task.delete()

    _del_queue_tasks(task_id_list)

    desc = {'操作': '删除自动验证计划', '自动验证计划名称': ','.join(task_name_list)}
    SaveOperationLog(request.user, OperationLog.TYPE_AUTO_VERIFY_TASK, json.dumps(desc, ensure_ascii=False),
                     get_operator(request))

    return HttpResponse(json.dumps(result, ensure_ascii=False))


def enabletask(request):
    task_ids = request.GET.get('task_id', 0)
    result = {'r': 0, 'e': '操作成功'}
    task_name_list = list()
    for task_id in task_ids.split(','):
        task = AutoVerifySchedule.objects.filter(id=task_id)
        task_name_list.append(task.first().name)
        task.update(enabled=True)

    desc = {'操作': '启用自动验证计划', '自动验证计划名称': ','.join(task_name_list)}
    SaveOperationLog(request.user, OperationLog.TYPE_AUTO_VERIFY_TASK, json.dumps(desc, ensure_ascii=False),
                     get_operator(request))

    return HttpResponse(json.dumps(result, ensure_ascii=False))


def disabletask(request):
    task_ids = request.GET.get('task_id', 0)
    result = {'r': 0, 'e': '操作成功'}
    task_name_list = list()
    task_id_list = task_ids.split(',')
    for task_id in task_id_list:
        task = AutoVerifySchedule.objects.filter(id=task_id)
        task_name_list.append(task.first().name)
        task.update(enabled=False)
    _del_queue_tasks(task_id_list)
    desc = {'操作': '禁用自动验证计划', '自动验证计划名称': ','.join(task_name_list)}
    SaveOperationLog(request.user, OperationLog.TYPE_AUTO_VERIFY_TASK, json.dumps(desc, ensure_ascii=False),
                     get_operator(request))

    return HttpResponse(json.dumps(result, ensure_ascii=False))


def _get_group_type(point_type, point_id):
    if point_type == 'cdp':
        auto_verify_tasks = AutoVerifyTask.objects.filter(point_id__contains=point_id)
    else:
        auto_verify_tasks = AutoVerifyTask.objects.filter(point_id=point_id)
    group = 'not_need_verify'
    for auto_verify_task in auto_verify_tasks:
        if auto_verify_task.verify_type == AutoVerifyTask.VERIFY_TYPE_END:
            verify_result = json.loads(auto_verify_task.verify_result)
            if verify_result.get('result') == 'pass':
                group = 'verify_pass'
                break
            else:
                group = 'verify_failed'
        elif auto_verify_task.verify_type in (AutoVerifyTask.VERIFY_TYPE_ING, AutoVerifyTask.VERIFY_TYPE_QUEUE):
            group = 'not_need_verify'
        else:
            _logger.info('get_report verify_failed verify_type={}, point_id={}'.format(auto_verify_task.verify_type,
                                                                                       point_id))
            group = 'verify_failed'
    return group


def get_report(request):
    host_ident = request.GET.get('host_ident')
    start_date = request.GET.get('stime')
    end_date = request.GET.get('endtime')
    result = {'r': 0, 'e': '操作成功', 'report': list()}

    start_date = xdatetime.string2datetime(start_date)
    end_date = xdatetime.string2datetime(end_date) + timedelta(days=1)

    point_type = '整机备份'

    api_request = {'begin': start_date.strftime(xdatetime.FORMAT_ONLY_DATE),
                   'end': end_date.strftime(xdatetime.FORMAT_ONLY_DATE),
                   'use_serializer': False}

    api_response = HostSnapshotsWithNormalPerHost().get(request=request, ident=host_ident, api_request=api_request)
    if not status.is_success(api_response.status_code):
        e = get_response_error_string(api_response)
        debug = "HostSnapshotsWithNormalPerHost().get(begin:{} end:{} ident:{}) failed {}".format(
            start_date, end_date, host_ident, api_response.status_code)
        return HttpResponse(json.dumps({"r": 1, "e": e, "debug": debug}, ensure_ascii=False))

    for host_snapshot in api_response.data:
        point_id = '{}|{}|{}'.format(xdata.SNAPSHOT_TYPE_NORMAL, host_snapshot['id'], host_snapshot['start_datetime'])

        result['report'].append({
            "id": point_id,
            "content": '{} {}'.format(point_type, host_snapshot['start_datetime']),
            "start": host_snapshot['start_datetime'],
            "group": _get_group_type('normal', point_id),
            'type': 'point'})

    api_response = HostSnapshotsWithCdpPerHost().get(None, ident=host_ident, api_request=api_request)
    if not status.is_success(api_response.status_code):
        e = get_response_error_string(api_response)
        debug = "HostSnapshotsWithCdpPerHost().get(begin:{} end:{} ident:{}) failed {}".format(start_date, end_date,
                                                                                               host_ident,
                                                                                               api_response.status_code)
        return HttpResponse(json.dumps({"r": 1, "e": e, "debug": debug}, ensure_ascii=False))

    for host_snapshot in api_response.data:
        point_id_filter = '{}|{}|{}'.format(xdata.SNAPSHOT_TYPE_CDP, host_snapshot['id'], host_snapshot['begin'])
        point_id = '{}|{}'.format(point_id_filter, host_snapshot['end'])
        result['report'].append({
            "id": point_id,
            "content": 'CDP备份 {}'.format(host_snapshot['begin']),
            "start": host_snapshot['begin'],
            "end": host_snapshot['end'],
            "group": _get_group_type('cdp', point_id_filter)
        })

    # 计算no_point
    host_snapshots = HostSnapshot.objects.filter(host__ident=host_ident, successful=False,
                                                 finish_datetime__isnull=False,
                                                 finish_datetime__gte=api_request['begin'],
                                                 finish_datetime__lt=api_request['end'])

    i = 0
    for host_snapshot in host_snapshots:
        result['report'].append({
            "id": 'no_point_{}'.format(i),
            "content": '无备份点 {}'.format(host_snapshot.finish_datetime.strftime(xdatetime.FORMAT_WITH_USER_SECOND)),
            "start": host_snapshot.finish_datetime.strftime(xdatetime.FORMAT_WITH_USER_SECOND),
            'type': 'point',
            "group": 'no_point'
        })
        i = i + 1

    return HttpResponse(json.dumps(result, ensure_ascii=False))


def get_report_detail(request):
    point_id = request.GET.get('id')
    result = {'r': 0, 'e': '操作成功'}
    point_id_v = point_id.split('|')
    if point_id_v[0] == 'cdp':
        auto_verify_tasks = AutoVerifyTask.objects.filter(verify_type=AutoVerifyTask.VERIFY_TYPE_END,
                                                          point_id__contains='{}|{}|{}'.format(point_id_v[0],
                                                                                               point_id_v[1],
                                                                                               point_id_v[2]))
    else:
        auto_verify_tasks = AutoVerifyTask.objects.filter(point_id=point_id, verify_type=AutoVerifyTask.VERIFY_TYPE_END)

    verfiy_detail_list = list()
    for auto_verify_task in auto_verify_tasks:
        verfiy_detail = dict()
        verify_result_list = list()
        verfiy_detail['schedule_name'] = auto_verify_task.schedule_name
        verify_result = json.loads(auto_verify_task.verify_result)
        if verify_result.get('stime'):
            verfiy_detail['stime'] = '{} - {}'.format(verify_result.get('stime', ''), verify_result.get('endtime', ''))
        else:
            verfiy_detail['stime'] = auto_verify_task.created.strftime(xdatetime.FORMAT_WITH_USER_SECOND)
        verify_os = verify_result.get('verify_os')
        verify_osname = verify_result.get('verify_osname')
        verify_osver = verify_result.get('verify_osver')
        verify_hdd = verify_result.get('verify_hdd')
        user_script_result = verify_result.get('user_script_result')
        if verify_os:
            if verify_os['result'] == 'pass':
                verify_result_list.append('操作系统能否启动（通过）')
                verify_result_list.append('网络状态(通过)')
            else:
                verify_result_list.append('操作系统能否启动（失败）')
                verify_result_list.append('网络状态(失败)')
        else:
            verify_result_list.append('操作系统能否启动（失败）')
            verify_result_list.append('网络状态(失败)')

        if verify_osname:
            if verify_osname['result'] == 'pass':
                verify_result_list.append('客户端名称（通过）')
            else:
                verify_result_list.append(
                    '客户端名称（{} -> {}）'.format(verify_osname['snapshot_osname'], verify_osname['kvm_osname']))

        if verify_osver:
            if verify_osver['result'] == 'pass':
                verify_result_list.append('操作系统版本（通过）')
            else:
                verify_result_list.append(
                    '操作系统版本（{} -> {}）'.format(verify_osver['snapshot_version'], verify_osver['kvm_version']))

        if verify_hdd:
            if verify_hdd['result'] == 'pass':
                verify_result_list.append('硬盘分区结构（通过）')
            else:
                verify_result_list.append('硬盘分区数量（{} -> {}）'.format(verify_hdd['snapshot_disk_partition_count'],
                                                                    verify_hdd['kvm_disk_partition_count']))
        if verify_hdd:
            snapshot_disk_used_str = '<br>'.join(verify_hdd.get('snapshot_disk_used', ''))
            kvm_disk_used_str = '<br>'.join(verify_hdd.get('kvm_disk_used', ''))
            verify_result_list.append('备份前硬盘使用情况：<br>{}'.format(snapshot_disk_used_str))
            verify_result_list.append('备份点硬盘使用情况：<br>{}'.format(kvm_disk_used_str))
        if user_script_result:
            for user_result in user_script_result:
                script_name = user_result['script_name']
                try:
                    script_rc = json.loads(user_result['rc']['stdout'])
                except Exception as e:
                    script_rc = {'msg': user_result['rc']['stdout']}
                verify_result_list.append(
                    '脚本名称：{}，执行结果：{} <div style="display:none;">{}</div>'.format(
                        script_name, script_rc.get('msg'), user_result['rc']))
            verify_result_list.append(
                '<span onclick="show_more_debug(this);" style="color:blue;cursor:pointer;">更多</span>')
        verfiy_detail['verify_result_list'] = verify_result_list
        verfiy_detail_list.append(verfiy_detail)

    result['result'] = verfiy_detail_list

    return HttpResponse(json.dumps(result, ensure_ascii=False))


def _check_up_file_valid(filepath, exename, ext):
    if ext == '.zip':
        try:
            z = zipfile.ZipFile(filepath, mode='r')
            for filename in z.namelist():
                # TODO:文件名不支持中文
                if filename.lower() == exename.lower():
                    return True
            return False
        except Exception as e:
            _logger.info('_check_up_file_valid Failed.e={}'.format(e))
            return False
    elif ext == 'tar.gz':
        try:
            with tarfile.open(filepath) as tar:
                names = tar.getnames()
                for filename in names:
                    if filename.lower()[-len(exename):] == exename.lower():
                        return True
            return False
        except Exception as e:
            _logger.info('_check_up_file_valid Failed.e={}'.format(e))
            return False
    return True


def upload(request):
    file_data = request.body
    filename = request.GET.get('filename')
    start = int(request.GET.get('start', '0'))
    step = int(request.GET.get('step', 1024 * 1024))
    total = int(request.GET.get('total', 0))
    tmp_dir = os.path.join('/home', 'user_script', '{}'.format(request.user.id))

    r = 0
    if not os.path.isdir(tmp_dir):
        os.makedirs(tmp_dir)

    filepath = os.path.join(tmp_dir, filename)

    if start == 0:
        ext = os.path.splitext(filename)[1].lower()
        if ext not in ('.zip', '.gz',):
            return HttpResponse(json.dumps({"r": 2, "e": "请上传zip或.tar.gz格式的文件", "ext": ext}, ensure_ascii=False))
        if ext == '.gz':
            ext = 'tar.gz'
        if len(filepath) > 15 and os.path.isfile(filepath):
            os.remove(filepath)

    binfile = open(filepath, 'ab')
    vec = str(file_data).split(';base64,')
    if len(vec) == 2:
        strbase64 = vec[1]
    else:
        return HttpResponse(json.dumps({"r": 1, "e": "忽略"}, ensure_ascii=False))
    binfile.write(base64.b64decode(strbase64))
    binfile.close()

    if start == 0:
        script_name = request.GET.get('script_name')
        script_desc = request.GET.get('script_desc')
        auto_verify_script.objects.create(user=request.user, filename=filename, name=script_name,
                                          desc=script_desc, path=filepath)

    start = start + step
    if start >= total:
        if os.path.getsize(filepath) == total:
            r = 200
            ext = os.path.splitext(filename)[1].lower()
            if ext == '.gz':
                ext = 'tar.gz'
            if not _check_up_file_valid(filepath, 'main.py', ext):
                os.remove(filepath)
                auto_verify_script.objects.filter(path=filepath).delete()
                return HttpResponse(json.dumps({"r": 1, "e": "上传压缩包中未找到main.py"}, ensure_ascii=False))
            uuidfilepath = os.path.join(tmp_dir, '{}.zip'.format(uuid.uuid4().hex))
            shutil.move(filepath, uuidfilepath)
            auto_verify_script.objects.filter(path=filepath).update(path=uuidfilepath)
            _logger.info('file upload ok filepath={}'.format(filepath))

    return HttpResponse(
        json.dumps({"r": r, "e": "操作成功", "name": filename, "start": start}, ensure_ascii=False))


def _get_srcipt_info(obj):
    return [obj.id, obj.name, obj.desc]


def list_user_script(request):
    page = int(request.GET.get('page', 1))
    rows = int(request.GET.get('rows', 30))

    scripts = auto_verify_script.objects.filter(user_id=request.user.id)
    paginator = Paginator(scripts, rows)
    totalPlan = paginator.count
    totalPage = paginator.num_pages

    page = totalPage if page > totalPage else page
    currentObjs = paginator.page(page).object_list
    rowList = list()
    for Obj in currentObjs:
        detailDict = {'id': Obj.id, 'cell': _get_srcipt_info(Obj)}
        rowList.append(detailDict)

    retInfo = {'r': 0, 'a': 'list', 'page': str(page), 'total': totalPage,
               'records': totalPlan, 'rows': rowList}
    jsonStr = json.dumps(retInfo, ensure_ascii=False)
    return HttpResponse(jsonStr)


def get_user_script_list(request):
    script_list = list()
    auto_verify_scripts = auto_verify_script.objects.filter(user=request.user)
    if auto_verify_scripts:
        for script in auto_verify_scripts.all():
            script_list.append({'id': script.id, 'name': script.name})
    return HttpResponse(json.dumps(script_list, ensure_ascii=False))


@xlogging.LockDecorator(_lock)
def download(request):
    id = request.GET.get('id')
    if id is None:
        return HttpResponseForbidden()
    if int(id) == 0:
        # 下载示例脚本
        user_script_template = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'verify_task',
            'user_script_template.py')
        if not os.path.isdir('/home/user_script'):
            os.makedirs('/home/user_script')
        filepath = '/home/user_script/sample.zip'
        if len(filepath) > 15 and os.path.isfile(filepath):
            os.remove(filepath)
        with zipfile.ZipFile(filepath, 'w') as myzip:
            myzip.write(user_script_template, 'main.py')
        filename = 'sample.zip'

    else:
        scriptfile = auto_verify_script.objects.filter(id=id, user=request.user)
        if scriptfile is None:
            return HttpResponseForbidden()
        scriptfile = scriptfile.first()

        filepath = scriptfile.path
        filename = scriptfile.filename
        if not os.path.isfile(filepath):
            return HttpResponseForbidden()
    response = HttpResponse()
    response['Content-Type'] = 'application/octet-stream'
    response['Content-Disposition'] = 'attachment;filename="{0}"'.format(escape_uri_path(filename))
    response['content-length'] = '{}'.format(os.stat(filepath).st_size)
    response['X-Accel-Redirect'] = '/file_download/' + '/'.join(quote(filepath, encoding='utf-8').split('/')[2:])
    return response


def delscripts(request):
    ids = request.POST.get('ids')

    for id in ids.split(','):
        script = auto_verify_script.objects.filter(id=id)
        if script:
            one_script = script.first()
            filepath = one_script.path
            if len(filepath) > 15 and os.path.isfile(filepath):
                os.remove(filepath)
            one_script.delete()

    return HttpResponse(json.dumps({'r': 0}, ensure_ascii=False))


def get_lineup_tasks(request):
    current_user_verify_count = 0
    current_user_verify_ing_count = 0
    all_verify_count = 0
    all_verify_ing_count = 0
    auto_verify_tasks = AutoVerifyTask.objects.filter(verify_type=AutoVerifyTask.VERIFY_TYPE_QUEUE)

    for task in auto_verify_tasks:
        all_verify_count = all_verify_count + 1
        host_snapshot_id = task.point_id.split('|')[1]
        if HostSnapshot.objects.get(id=host_snapshot_id).host.user.id == request.user.id:
            current_user_verify_count = current_user_verify_count + 1

    auto_verify_tasks = AutoVerifyTask.objects.filter(verify_type=AutoVerifyTask.VERIFY_TYPE_ING)
    for task in auto_verify_tasks:
        all_verify_ing_count = all_verify_ing_count + 1
        host_snapshot_id = task.point_id.split('|')[1]
        if HostSnapshot.objects.get(id=host_snapshot_id).host.user.id == request.user.id:
            current_user_verify_ing_count = current_user_verify_ing_count + 1
    return HttpResponse(json.dumps(
        {'r': 0, 'all_verify_count': all_verify_count, 'all_verify_ing_count': all_verify_ing_count,
         'current_user_verify_count': current_user_verify_count,
         'current_user_verify_ing_count': current_user_verify_ing_count}, ensure_ascii=False))
