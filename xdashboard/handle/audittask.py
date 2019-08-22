import json
from django.db.models import Q
from box_dashboard import xlogging, functions
from django.http import HttpResponse
from django.core.paginator import Paginator
from xdashboard.models import audit_task
from apiv1.models import HostSnapshot
from .restore import api_snapshot_restore, api_snapshot_share_add, api_host_snapshot_restore_vol
from xdashboard.common.msg import save_host_log
from .takeover import api_create_kvm
from apiv1.models import HostLog
import django.utils.timezone as timezone
import threading

_logger = xlogging.getLogger(__name__)
router = functions.Router(globals())


def get_audit_task_list(request):
    page = int(request.GET.get('page', '1'))
    rows = int(request.GET.get('rows', '30'))
    sidx = request.GET.get('sidx', 'create_datetime')
    sord = request.GET.get('sord', 'asc')
    if sidx not in ('create_user', 'create_datetime',):
        sidx = None
    if sidx and sord == 'desc':
        sidx = '-{}'.format(sidx)

    audit_task_list = audit_task.objects.filter(status=audit_task.AUIDT_TASK_STATUS_WAITE)
    if sidx:
        audit_task_list = audit_task_list.order_by(sidx)

    paginator = Paginator(object_list=audit_task_list, per_page=rows)
    records = paginator.count
    total = paginator.num_pages
    page = total if page > total else page
    object_list = paginator.page(page).object_list

    rows = list()
    for object in object_list:
        task_info_obj = json.loads(object.task_info)
        info = '-'
        op = '<span style="color:blue;cursor:pointer;" onclick="OnAudit({})">执行审批</span>'.format(object.id)
        task_type, host_snapshot_id = get_approved_task_host_snapshot_id(task_info_obj)
        if task_type is None:
            continue
        host_snapshot_obj = HostSnapshot.objects.filter(id=host_snapshot_id)
        if host_snapshot_obj:
            host_snapshot_obj = host_snapshot_obj.first()
            info = '{}的整机备份{}'.format(host_snapshot_obj.host.name,
                                      host_snapshot_obj.start_datetime.strftime('%Y-%m-%d %H:%M:%S.%f'))
        rows.append({'id': object.id, 'cell': [object.id, object.create_user.username,
                                               object.create_datetime.strftime('%Y-%m-%d %H:%M:%S'), info,
                                               task_type, op]})

    result = {'r': 0, 'a': 'list', 'page': page, 'total': total, 'records': records, 'rows': rows}

    return HttpResponse(json.dumps(result, ensure_ascii=False))


def get_approved_task_host_snapshot_id(task_info_obj):
    task_type = task_info_obj['task_type']
    if task_type == 'pc_restore':
        task_type_display = '整机恢复'
        host_snapshot_id = task_info_obj['host_snapshot_id']
    elif task_type == 'vol_restore':
        task_type_display = '卷恢复'
        host_snapshot_id = task_info_obj['host_snapshot_id']
    elif task_type in ('file_restore', 'file_verify',):
        if task_type == 'file_restore':
            task_type_display = '文件恢复'
        else:
            task_type_display = '文件验证'
        host_snapshot_id = task_info_obj['api_request']['host_snapshot_id']
    elif task_type in ('forever_kvm', 'temporary_kvm',):
        task_type_display = '快速验证'
        host_snapshot_id = task_info_obj['api_request']['pointid'].split('|')[1]
    else:
        _logger.info('get_approved_task_host_snapshot_id Failed.task_type={}'.format(task_type))
        host_snapshot_id = None
        task_type_display = None
    return task_type_display, host_snapshot_id


def get_approved_task_list(request):
    page = int(request.GET.get('page', '1'))
    rows = int(request.GET.get('rows', '30'))
    sidx = request.GET.get('sidx', 'audit_datetime')
    sord = request.GET.get('sord', 'asc')
    if sidx not in ('audit_datetime',):
        sidx = None
    if sidx and sord == 'desc':
        sidx = '-{}'.format(sidx)

    audit_task_list = audit_task.objects.filter(audit_user=request.user).filter(
        ~Q(status=audit_task.AUIDT_TASK_STATUS_WAITE))
    if sidx:
        audit_task_list = audit_task_list.order_by(sidx)

    paginator = Paginator(object_list=audit_task_list, per_page=rows)
    records = paginator.count
    total = paginator.num_pages
    page = total if page > total else page
    object_list = paginator.page(page).object_list

    rows = list()
    for object in object_list:
        task_info_obj = json.loads(object.task_info)
        host_name = '-'
        approved_time = object.audit_datetime.strftime('%Y-%m-%d %H:%M:%S')

        task_type, host_snapshot_id = get_approved_task_host_snapshot_id(task_info_obj)
        if task_type is None:
            continue
        host_snapshot_obj = HostSnapshot.objects.filter(id=host_snapshot_id)
        if host_snapshot_obj:
            host_snapshot_obj = host_snapshot_obj.first()
            host_name = '{}'.format(host_snapshot_obj.host.name)
        create_datetime = object.create_datetime.strftime('%Y-%m-%d %H:%M:%S')
        desc = '{}{}了{}于{}发起的{}'.format(object.audit_user.username, object.get_status_display(), object.create_user,
                                        create_datetime, task_type)
        rows.append([object.id, object.get_status_display(), host_name, approved_time, desc])

    result = {'r': 0, 'a': 'list', 'page': page, 'total': total, 'records': records, 'rows': rows}

    return HttpResponse(json.dumps(result, ensure_ascii=False))


def get_host_log_type(task_type):
    log_type = HostLog.LOG_UNKNOWN
    if task_type == 'pc_restore':
        log_type = HostLog.LOG_RESTORE_START
    elif task_type == 'vol_restore':
        log_type = HostLog.LOG_RESTORE_START
    elif task_type in ('file_restore', 'file_verify',):
        log_type = HostLog.LOG_RESTORE_START
    elif task_type in ('forever_kvm', 'temporary_kvm',):
        log_type = HostLog.LOG_RESTORE_START
    else:
        _logger.error('notify_audits Failed. task_type={}'.format(task_type))
    return log_type


def _snapshot_share_add(create_user, task_info_obj):
    api_snapshot_share_add(create_user, task_info_obj['api_request'], task_info_obj['task_name'],
                           task_info_obj['timestamp'], task_info_obj['operator'], task_info_obj['host_name'],
                           task_info_obj['isWin'])


def _create_kvm(create_user, task_info_obj):
    api_create_kvm(create_user, task_info_obj['operator'], task_info_obj['api_request'],
                   task_info_obj['desc'], task_info_obj['run'], task_info_obj['kvm_debug'])


def _host_snapshot_restore_vol(create_user, task_info_obj):
    api_host_snapshot_restore_vol(create_user, task_info_obj['host_snapshot_id'],
                                  task_info_obj['api_request'], task_info_obj['operator'],
                                  task_info_obj['restore_node_time'])


def _snapshot_restore(create_user, task_info_obj):
    api_snapshot_restore(create_user, task_info_obj['host_snapshot_id'],
                         task_info_obj['api_request'], task_info_obj['pe_host_ident'],
                         task_info_obj['ipconfig_infos'], task_info_obj['operator'],
                         task_info_obj['restore_node_time'], task_info_obj['api_request_data'])


def doaudit(request):
    result = {'r': 0, 'e': '操作成功'}
    ids = request.GET.get('ids')
    doaudit = int(request.GET.get('doaudit'))
    for id in ids.split(','):
        audit_task_obj = audit_task.objects.filter(id=id)
        if audit_task_obj is None:
            _logger.info('doaudit audit_task_obj is None Failed.ignore.id={}'.format(id))
            continue
        audit_task_obj = audit_task_obj.first()
        if audit_task_obj.status != audit_task.AUIDT_TASK_STATUS_WAITE:
            _logger.info(
                'doaudit Failed.ignore. audit_task_id={}, status={}'.format(audit_task_obj.id, audit_task_obj.status))
            continue
        task_info_obj = json.loads(audit_task_obj.task_info)
        task_type = task_info_obj['task_type']
        task_type_display, host_snapshot_id = get_approved_task_host_snapshot_id(task_info_obj)
        if task_type_display is None:
            continue
        audit_task_obj.status = doaudit
        audit_task_obj.audit_user = request.user
        audit_task_obj.audit_datetime = timezone.now()
        audit_task_obj.save()

        host_snapshot_obj = HostSnapshot.objects.filter(id=host_snapshot_id)
        if host_snapshot_obj:
            host_snapshot_obj = host_snapshot_obj.first()
            host = host_snapshot_obj.host
            info = '{}的整机备份{}'.format(host_snapshot_obj.host.name,
                                      host_snapshot_obj.start_datetime.strftime('%Y-%m-%d %H:%M:%S.%f'))
            reason = "{}{}，备份点为“{}”".format(audit_task_obj.get_status_display(), task_type_display, info)
            save_host_log(host, HostLog.LOG_AUDIT, {'description': reason})

        if int(audit_task_obj.status) != audit_task.AUIDT_TASK_STATUS_AGREE:
            _logger.info('doaudit refuse status={}'.format(audit_task_obj.status))
            continue
        if task_type == 'pc_restore':
            threading.Thread(target=_snapshot_restore, args=(audit_task_obj.create_user, task_info_obj,)).start()
        elif task_type == 'vol_restore':
            threading.Thread(target=_host_snapshot_restore_vol,
                             args=(audit_task_obj.create_user, task_info_obj,)).start()
        elif task_type in ('file_restore', 'file_verify',):
            threading.Thread(target=_snapshot_share_add, args=(audit_task_obj.create_user, task_info_obj,)).start()
        elif task_type in ('forever_kvm', 'temporary_kvm',):
            threading.Thread(target=_create_kvm, args=(audit_task_obj.create_user, task_info_obj,)).start()
        else:
            _logger.error('doaudit Failed.task_type={}'.format(task_type))

    return HttpResponse(json.dumps(result, ensure_ascii=False))
