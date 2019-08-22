import base64
import json
import requests

from django.http import HttpResponse
from rest_framework import status

from apiv1.models import Host
from apiv1.remote_views import RemoteBackupView
from box_dashboard import xlogging, functions
from xdashboard.common.license import check_license, get_functional_int_value
from xdashboard.handle.authorize.authorize_init import get_remotebackup_schedule_count
from xdashboard.handle.logserver import SaveOperationLog
from xdashboard.models import OperationLog
from xdashboard.request_util import get_operator
from .authorize import authCookies

_logger = xlogging.getLogger(__name__)
router = functions.Router(globals())


def _init_authCookies(request):
    aio_ip = request.GET.get('ip', None)
    if aio_ip is None:
        aio_ip = request.POST.get('ip', '')
    username = request.GET.get('u', None)
    if username is None:
        username = request.POST.get('u', '')
    password = request.GET.get('p', None)
    if password is None:
        password = request.POST.get('p', '')
    ssl = request.GET.get('ssl', None)
    if ssl is None:
        ssl = request.POST.get('ssl', '0')
    password = base64.b64decode(password).decode('utf-8')
    ssl = int(ssl)
    if ssl == 0:
        ins = authCookies.AuthCookies(r'http://{}:{}/'.format(aio_ip, 8000), username, password)
    else:
        ins = authCookies.AuthCookies(r'https://{}/'.format(aio_ip), username, password)
    return ins


def test_access_to_remote_aio(request):
    params = request.GET
    aio_ip, username, password = params['ip'], params['u'], base64.b64decode(params['p']).decode('utf-8')
    if aio_ip in ['0.0.0.0', '127.0.0.1']:
        return HttpResponse(content=json.dumps({'access_able': False}), status=status.HTTP_200_OK)
    if params['ssl'] == '0':
        ins = authCookies.AuthCookies(r'http://{}:{}/'.format(aio_ip, 8000), username, password, timeout=10)
    else:
        ins = authCookies.AuthCookies(r'https://{}/'.format(aio_ip), username, password, timeout=10)

    try:
        secure_cookie, csrf_token, _ = ins.get(r'')
    except Exception as e:
        _logger.warning('test_access_to_remote_aio, failed: {}'.format(e))
        secure_cookie, csrf_token = None, None

    if all([secure_cookie, csrf_token]):
        return HttpResponse(content=json.dumps({'access_able': True}), status=status.HTTP_200_OK)

    return HttpResponse(content=json.dumps({'access_able': False}), status=status.HTTP_200_OK)


def getlist(request):
    id = request.GET.get('id', '')
    ins = _init_authCookies(request)
    secure_cookie, csrf_token, f_url = ins.get(
        r'xdashboard/backup_handle/?a=getlist&include_remote_host=1&inc_nas_host=1&id={}'.format(id))  # 获取下次请求需要的关键数据
    params = {}
    rsp1 = requests.get(
        f_url,
        headers={'Content-Type': 'application/x-www-form-urlencoded', 'x-csrftoken': csrf_token},
        data=params,
        cookies=secure_cookie,
        verify=False
    )
    if not status.is_success(rsp1.status_code):
        _logger.error('getlist Failed.status_code={}'.format(rsp1.status_code))
    return HttpResponse(rsp1.content.decode('utf-8'))


def getserverinfo(request):
    id = request.GET.get('id', '')
    ins = _init_authCookies(request)
    secure_cookie, csrf_token, f_url = ins.get(
        r'xdashboard/backup_handle/?a=getserverinfo&id={}'.format(id))  # 获取下次请求需要的关键数据
    params = {}
    rsp1 = requests.get(
        f_url,
        headers={'Content-Type': 'application/x-www-form-urlencoded', 'x-csrftoken': csrf_token},
        data=params,
        cookies=secure_cookie,
        verify=False
    )
    if not status.is_success(rsp1.status_code):
        _logger.error('getserverinfo Failed.status_code={}'.format(rsp1.status_code))
    return HttpResponse(rsp1.content.decode('utf-8'))


def get_point_list(request):
    host_ident = request.GET.get('host_ident', '')
    checkident = request.GET.get('checkident', '')
    ins = _init_authCookies(request)
    secure_cookie, csrf_token, f_url = ins.get(
        r'xdashboard/hotbackup_handle/?a=get_point_list&host_ident={}&checkident={}'.format(host_ident,
                                                                                            checkident))  # 获取下次请求需要的关键数据
    params = {}
    rsp1 = requests.get(
        f_url,
        headers={'Content-Type': 'application/x-www-form-urlencoded', 'x-csrftoken': csrf_token},
        data=params,
        cookies=secure_cookie,
        verify=False
    )
    if not status.is_success(rsp1.status_code):
        _logger.error('get_point_list Failed.status_code={}'.format(rsp1.status_code))
    return HttpResponse(rsp1.content.decode('utf-8'))


def _get_host_info(request, ident):
    id = ident
    hosts = Host.objects.filter(ident=ident)
    for host in hosts:
        return host.name, host.ext_info
    ins = _init_authCookies(request)
    secure_cookie, csrf_token, f_url = ins.get(
        r'xdashboard/backup_handle/?a=getserverinfo&id={}'.format(id))  # 获取下次请求需要的关键数据
    params = {}
    rsp1 = requests.get(
        f_url,
        headers={'Content-Type': 'application/x-www-form-urlencoded', 'x-csrftoken': csrf_token},
        data=params,
        cookies=secure_cookie,
        verify=False
    )
    if not status.is_success(rsp1.status_code):
        _logger.error('_get_displayname Failed.status_code={}'.format(rsp1.status_code))
        return '', ''
    json_info = json.loads(rsp1.content.decode('utf-8'))

    if json_info.get('display_name', ''):
        display_name = json_info.get('display_name', '')
    else:
        display_name = json_info.get('servername', '')

    return display_name, json_info.get('host_ext_info', '')


def _check_remotebackup_license(request):
    clret = check_license('remotebackup')
    if clret.get('r', 0) != 0:
        return clret
    remotebackup_count = get_remotebackup_schedule_count()
    count = get_functional_int_value('remotebackup')
    if remotebackup_count >= count:
        return {'r': 2, 'e': '当前授权数量{}，已创建计划数量{}，请增加授权数量或删除一些计划。'.format(count, remotebackup_count)}
    return {'r': 0, 'e': 'OK'}


def createremotebackup(request):
    name = request.POST.get('name', '')
    ident = request.POST.get('ident', '')
    network_transmission_type = request.POST.get('network_transmission_type', '')
    aio_ip = request.POST.get('ip', '')
    username = request.POST.get('u', '')
    password = request.POST.get('p', '')
    storage_node_ident = request.POST.get('storage_node_ident', '')
    password = base64.b64decode(password).decode('utf-8')
    full_param = request.POST['full_param']

    clret = _check_remotebackup_license(request)
    if clret.get('r', 0) != 0:
        return HttpResponse(json.dumps(clret, ensure_ascii=False))

    display_name, host_ext_info = _get_host_info(request, ident)
    if not host_ext_info or not display_name:
        _logger.error('createremotebackup get remote host info fail, host_ext_info:{} display_name:{}'.format(
            host_ext_info, display_name))
        return HttpResponse(
            json.dumps({'r': 1, 'e': '内部错误。获取远端主机关键信息失败。'}, ensure_ascii=False))

    api_request = {'type': 'createremotebackup',
                   'ident': ident,
                   'display_name': display_name,
                   'network_transmission_type': network_transmission_type,
                   'ip': aio_ip,
                   'username': username,
                   'password': password,
                   'user_id': request.user.id,
                   'name': name,
                   'storage_node_ident': storage_node_ident,
                   'full_param': json.loads(full_param),
                   'edit_plan_id': request.POST['edit_plan_id'],
                   'host_ext_info': host_ext_info
                   }

    api_request['full_param']['remote_aio'] = {'aio_ip': aio_ip, 'username': username, 'password': password}

    api_response = RemoteBackupView().post(request, api_request)
    if not status.is_success(api_response.status_code):
        return HttpResponse(
            json.dumps({'r': 1, 'e': '内部错误。status_code:{}'.format(api_response.status_code)}, ensure_ascii=False))

    res_data = json.loads(api_response.data)
    if RemoteBackupView.is_edit_remote_plan(api_request):
        operation_log(request, {'操作': '更改计划', '计划ID': res_data['plan_id'], '计划名称': res_data['plan_name']})
    else:
        if res_data['r'] == '1':
            pass  # 创建失败了 do nothing
        else:
            operation_log(request, {'操作': '创建计划', '计划ID': res_data['plan_id'], '计划名称': res_data['plan_name']})
    return HttpResponse(api_response.data)


def listplan(request):
    page = int(request.GET.get('page', '1'))
    rows = int(request.GET.get('rows', '30'))
    sidx = request.GET.get('sidx', None)
    sord = request.GET.get('sord', 'asc')
    if sidx not in ('name', 'host', 'created', 'enabled', 'next_run_date', 'last_run_date'):
        sidx = 'created'
    if sidx and sord == 'desc':
        sidx = '-{}'.format(sidx)

    api_request = {
        'type': 'list',
        'page': page,
        'rows': rows,
        'sidx': sidx,
    }
    api_response = RemoteBackupView().post(request, api_request)
    if not status.is_success(api_response.status_code):
        return HttpResponse(
            json.dumps({'r': 1, 'e': '内部错误。status_code:{}'.format(api_response.status_code)}, ensure_ascii=False))
    return HttpResponse(api_response.data)


def del_remotebackup_plan(request):
    result = json.dumps({'r': 0, 'e': '操作成功'}, ensure_ascii=False)
    plan_ids = request.GET.get('ids', '0')
    for _id in plan_ids.split(','):
        _id = int(_id)
        api_request = {
            'type': 'delete',
            'id': _id,
        }
        api_response = RemoteBackupView().post(request, api_request)
        if not status.is_success(api_response.status_code):
            return HttpResponse(
                json.dumps({'r': 1, 'e': '内部错误。status_code:{}'.format(api_response.status_code)}, ensure_ascii=False))
        res_data = json.loads(api_response.data)
        operation_log(request, {'操作': '删除计划', '计划ID': res_data['plan_id'], '计划名称': res_data['plan_name']})
        result = api_response.data
    return HttpResponse(result)


def enable_remotebackup_plan(request):
    result = json.dumps({'r': 0, 'e': '操作成功'}, ensure_ascii=False)
    plan_ids = request.GET.get('ids', '0')
    for _id in plan_ids.split(','):
        _id = int(_id)
        api_request = {
            'type': 'enable',
            'id': _id,
        }
        api_response = RemoteBackupView().post(request, api_request)
        if not status.is_success(api_response.status_code):
            return HttpResponse(
                json.dumps({'r': 1, 'e': '内部错误。status_code:{}'.format(api_response.status_code)}, ensure_ascii=False))
        res_data = json.loads(api_response.data)
        on_msg = '启用' if res_data['enabled'] else '禁用'
        operation_log(request, {'操作': on_msg, '计划ID': res_data['plan_id'], '计划名称': res_data['plan_name']})
        result = api_response.data
    return HttpResponse(result)


def execute_plan_immediately(request):
    task_ids_str = request.GET['ids']
    result = {'r': 0, 'e': '', 'result': list()}
    for _id in task_ids_str.split(','):
        _id = int(_id)
        api_request = {
            'type': 'update_next_run_date_to_now',
            'id': _id,
        }
        api_response = RemoteBackupView().post(request, api_request)
        if not status.is_success(api_response.status_code):
            return HttpResponse(
                json.dumps({'r': 1, 'e': '内部错误。status_code:{}'.format(api_response.status_code)}, ensure_ascii=False))
        else:
            result['result'].append(api_response.data)
        res_data = api_response.data
        operation_log(request, {'操作': '执行计划', '计划ID': res_data['plan_id'], '计划名称': res_data['plan_name']})
    return HttpResponse(json.dumps(result, ensure_ascii=False))


def operation_log(request, detial):
    user, log_event = request.user, OperationLog.REMOTE_BACKUP
    SaveOperationLog(user, log_event, json.dumps(detial, ensure_ascii=False), get_operator(request))
