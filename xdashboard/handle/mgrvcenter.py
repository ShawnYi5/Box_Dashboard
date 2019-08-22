import json

from django.core.paginator import Paginator
from django.http import HttpResponse
from rest_framework import status
from tools.vm_proxy import VmwareProxy

from apiv1.vmware_logic import VirtualCenterConnectionView
from box_dashboard import xlogging, functions
from xdashboard.handle import serversmgr
from xdashboard.handle.logserver import SaveOperationLog
from xdashboard.models import OperationLog
from xdashboard.request_util import get_operator

_logger = xlogging.getLogger(__name__)
router = functions.Router(globals())


def _get_api_instance(host, user, password, port):
    try:
        api = VmwareProxy(host, user, password, port)
    except Exception as e:
        _logger.error('_get_api_instance error:{}'.format(e), exc_info=True)
        return None
    else:
        return api


def _get_api_instance_ex(host, user, password, port):
    try:
        api = VmwareProxy(host, user, password, port)
    except Exception as e:
        _logger.error('_get_api_instance_ex error:{}'.format(e), exc_info=True)
        return None, str(e)
    else:
        return api, 'OK'


def list_host(request):
    paramsQD = request.GET
    perPage = paramsQD.get('rows', '10')  # 设置每页条数
    targPage = paramsQD.get('page', '1')  # 返回第几页
    search_key = request.GET.get('s_key', None)

    allPlans = VirtualCenterConnectionView().get(request).data
    rowList = list()
    for planAttr in allPlans:
        one_info = {'id': planAttr['id'], 'cell': [planAttr['id'], planAttr['address'], planAttr['username']]}
        is_need = serversmgr.filter_hosts(search_key, one_info['cell'][1], one_info['cell'][2])
        if is_need:
            rowList.append(one_info)
        else:
            pass
        rowList.sort(key=lambda x: x['id'])

    paginator = Paginator(object_list=rowList, per_page=perPage)
    plansNum = paginator.count
    pagesNum = paginator.num_pages
    getPlans = paginator.page(targPage).object_list

    retInfo = {'r': 0, 'a': 'list', 'page': targPage, 'total': pagesNum, 'records': plansNum, 'rows': getPlans}
    functions.sort_gird_rows(request, retInfo)
    jsonStr = json.dumps(retInfo, ensure_ascii=False)

    return HttpResponse(jsonStr)


def _fmt_vmware_proxy_msg(e):
    if 'No route to host' in e:
        return 'IP地址/名称不正确，不能访问，无法连接到该数据中心'
    if 'Connection timed out' in e:
        return 'IP地址/名称不正确，连接超时，无法连接到该数据中心'
    if 'InvalidLogin' in e:
        return '用户名或者密码不正确，创建失败，无法连接到该数据中心'
    return '创建失败。{}'.format(e)


def add_host(request):
    data = json.loads(request.POST['data'])
    address = data['address']
    username = data['username']
    password = data['password']
    ret = {'e': '', 'r': 0, 'debug': ''}
    api, e = _get_api_instance_ex(address, username, password, 443)  # 验证连接信息
    if not api:
        ret['e'] = _fmt_vmware_proxy_msg(e)
        ret['r'] = 1
        return HttpResponse(json.dumps(ret, ensure_ascii=False))
    else:
        api_request = {
            'address': address,
            'username': username,
            'password': password
        }
        rsp = VirtualCenterConnectionView().post(request, api_request)
        if status.is_success(rsp.status_code):
            _record_log(request, {
                '操作': '添加数据中心连接',
                '结果': '添加成功',
                '数据中心': address,
                '账号': username
            })
            return HttpResponse(json.dumps(ret))
        else:
            ret['e'] = '创建失败，内部异常。'
            ret['r'] = 1
            return HttpResponse(json.dumps(ret, ensure_ascii=False))


def del_host(request):
    obj_ids = request.GET['taskid']
    ret = {'e': '', 'r': 0, 'debug': ''}
    for obj_id in obj_ids.split(','):
        api_request = {
            'id': obj_id
        }
        rsp = VirtualCenterConnectionView().delete(request, api_request)
        if status.is_success(rsp.status_code):
            _record_log(request, {
                '操作': '删除数据中心连接',
                '结果': '删除成功',
                '数据中心': rsp.data['address'],
                '账号': rsp.data['username']
            })
        else:
            ret['e'] = rsp.data
            ret['r'] = 1
            return HttpResponse(json.dumps(ret, ensure_ascii=False))

    return HttpResponse(json.dumps(ret))


def _record_log(request, detail):
    SaveOperationLog(
        request.user, OperationLog.VMWARE_BACKUP, json.dumps(detail, ensure_ascii=False), get_operator(request))
