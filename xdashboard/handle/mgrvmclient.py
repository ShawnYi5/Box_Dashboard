import json

from django.core.paginator import Paginator
from django.http import HttpResponse
from rest_framework import status
from tools.vm_proxy import VmwareProxy

from apiv1.models import VirtualCenterConnection
from apiv1.views import StorageNodes
from apiv1.vmware_logic import VirtualHostSession
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


@xlogging.convert_exception_to_value(list())
def _get_info_list(api, moid, wsdlName, vc, list_all=None):
    info = list()
    for client in api.list_clients(moid, wsdlName, list_all):
        checkbox = True
        inode = True
        icon = 'folder'
        if client['wsdlName'] in ('Network', 'Datastore',):
            checkbox = False
            inode = False
            icon = ''
        elif client['wsdlName'] in ('VirtualMachine',):
            icon = 'pc'

        if client['is_dir']:
            if client['wsdlName'] in ['Datacenter', 'ClusterComputeResource']:  # 数据中心，集群
                open = True
            else:
                open = False

            if client['wsdlName'] == 'Datacenter':
                label = '{}({})'.format(client['name'], vc.name)
            else:
                label = '{}'.format(client['name'])

            info.append({'id': '{}|{}|{}'.format(client['moId'], client['wsdlName'], vc.id), 'icon': icon,
                         'checkbox': checkbox, "inode": inode, "open": open, 'label': label})

        else:
            label = '{}'.format(client['name'])
            if client['disabledMethod']:
                disabled = True
            else:
                disabled = False
            info.append({'id': '{}|{}|{}'.format(client['moId'], client['wsdlName'], vc.id),
                         'icon': icon, 'checkbox': checkbox, "inode": False,
                         "open": False, 'label': label, 'disabled': disabled})

    return info


def getlist(request):
    _id = request.GET.get('id', None)
    # 按照vcenter本来的结构列出信息，适用于当我们枚举不出虚拟机的情况
    list_all = request.GET.get('list_all', None)
    info = list()

    if not _id:
        vcs = VirtualCenterConnection.objects.filter(user=request.user)
        if not vcs.exists():
            info.append({'id': 'novcs', "inode": False, "open": False, 'label': '无'})
        else:
            for vc in vcs:
                api = _get_api_instance(vc.address, vc.username, vc.password, vc.port)
                if not api:
                    _logger.error('get api fail:{}'.format(vc.name))
                else:
                    info.extend(_get_info_list(api, None, None, vc, list_all))
    else:
        moId, wsdlName, vc_id = _id.split('|')
        vc = VirtualCenterConnection.objects.get(id=vc_id)
        api = _get_api_instance(vc.address, vc.username, vc.password, vc.port)
        info.extend(_get_info_list(api, moId, wsdlName, vc, list_all))

    return HttpResponse(json.dumps(info, ensure_ascii=False))


def list_host(request):
    paramsQD = request.GET
    perPage = paramsQD.get('rows', '10')  # 设置每页条数
    targPage = paramsQD.get('page', '1')  # 返回第几页
    search_key = request.GET.get('s_key', None)

    allPlans = VirtualHostSession().get(request).data
    rowList = list()
    for planAttr in allPlans:
        center = VirtualCenterConnection.objects.get(id=planAttr['connection'])
        one_info = {'id': planAttr['id'], 'cell': [
            planAttr['id'], planAttr['name'], '启用' if planAttr['enable'] else '禁用', center.address]}
        is_need = serversmgr.filter_hosts(search_key, one_info['cell'][1], one_info['cell'][3])
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


def add_host(request):
    params = request.POST
    key_infos = json.loads(params['ids'])
    ret = {'e': '', 'r': 0, 'debug': ''}
    resp = StorageNodes.get_max_storage_nodes()
    errlist = list()
    if status.is_success(resp.status_code):
        storage_node = resp.data['ident']
        for key_info in key_infos:
            api_request = {
                'ident': key_info['id'],
                'name': key_info['label'],
                'storage_node': storage_node
            }
            resp1 = VirtualHostSession().post(request, api_request)
            if status.is_success(resp1.status_code):
                _record_log(request, {
                    '操作': '添加免代理客户端',
                    '结果': '添加成功',
                    '客户端': key_info['label']
                })
            elif resp1.status_code == status.HTTP_406_NOT_ACCEPTABLE:
                errlist.append('添加<span>{}</span>失败，{}'.format(key_info['label'], resp1.data))
                ret['r'] = 1
            else:
                errlist.append('添加<span>{}</span>失败'.format(key_info['label']))
                ret['r'] = 1
        if len(errlist) > 0:
            ret['e'] = '<br />'.join(errlist)
        return HttpResponse(json.dumps(ret, ensure_ascii=False))
    else:
        ret['e'] = '添加失败，无法获取存储信息'
        ret['r'] = 1
        return HttpResponse(json.dumps(ret, ensure_ascii=False))


# todo
def host_detail(request):
    return HttpResponse(json.dumps({'e': '', 'r': '0'}, ensure_ascii=False))


def getserverinfo(request):
    id = request.GET.get('id', default='')
    retInfo = {
        "r": 0, "e": "操作成功",
        'servername': 'Microsoft Windows Server 2008 R2（64位）',
        'pcname': "svr2008x64r2",
        'os': "Microsoft Windows Server 2008 R2 Enterprise Service Pack 1",
        'buildnum': '7601',
        'harddisknum': '1',
        'harddiskinfo': 'VMware Virtual disk SCSI Disk Device',
        'total': '30GB',
        'use': '21.28GB',
        'ip': '172.16.184.187',
        'mac': '00-50-56-95-93-1B',
        'network_transmission_type': 1,
        'lasttime': '',
        'agent_version': '-'
    }
    return HttpResponse(json.dumps(retInfo, ensure_ascii=False))


def del_host(request):
    obj_ids = request.GET['taskid']
    ret = {'e': '', 'r': 0, 'debug': ''}
    for obj_id in obj_ids.split(','):
        api_request = {
            'id': obj_id
        }
        rsp = VirtualHostSession().delete(request, api_request)
        if status.is_success(rsp.status_code):
            _record_log(request, {
                '操作': '删除免代理客户端',
                '结果': '删除成功',
                '客户端': rsp.data['name']
            })
        else:
            if rsp.data:
                ret['e'] = rsp.data
            else:
                ret['e'] = 'status_code={}'.format(rsp.status_code)
            ret['r'] = 1
            return HttpResponse(json.dumps(ret, ensure_ascii=False))
    return HttpResponse(json.dumps(ret, ensure_ascii=False))


def enable_host(request):
    obj_ids = request.GET['taskid']
    ret = {'e': '', 'r': 0, 'debug': ''}
    for obj_id in obj_ids.split(','):
        api_request = {
            'id': obj_id,
            'action': 'enable'
        }
        rsp = VirtualHostSession().put(request, api_request)
        if status.is_success(rsp.status_code):
            if rsp.data['enable']:
                action = '启用'
            else:
                action = '禁用'
            _record_log(request, {
                '操作': '{}免代理客户端'.format(action),
                '结果': '{}成功'.format(action),
                '客户端': rsp.data['name']
            })
        else:
            ret['e'] = rsp.data
            ret['r'] = 1
            return HttpResponse(json.dumps(ret, ensure_ascii=False))

    return HttpResponse(json.dumps(ret, ensure_ascii=False))


def _record_log(request, detail):
    SaveOperationLog(
        request.user, OperationLog.VMWARE_BACKUP, json.dumps(detail, ensure_ascii=False), get_operator(request))
