import json
import threading
from django.http import HttpResponse
from rest_framework import status
from tools.vm_proxy import VmwareProxy
from box_dashboard import boxService
from apiv1.models import HostSnapshot
from apiv1.views import get_response_error_string
from apiv1.vmware_logic import VirtualCenterConnectionView, VirtualHostRestore
from box_dashboard import xlogging, functions
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


def get_vmware_config(request):
    pointid = request.GET.get('pointid')
    ret = {'e': '操作成功', 'r': 0}
    allPlans = VirtualCenterConnectionView().get(request).data
    rowList = list()
    for planAttr in allPlans:
        one_info = {'id': planAttr['id'], 'address': '{}({})'.format(planAttr['address'], planAttr['username'])}
        rowList.append(one_info)
        rowList.sort(key=lambda x: x['id'])
    ret['vcenterlist'] = rowList

    host_snapshot = HostSnapshot.objects.get(id=pointid.split('|')[1])
    host_snapshot_ext_config = json.loads(host_snapshot.ext_info)
    vm_cfg = host_snapshot_ext_config['vmware_config']

    ret['vmconfig'] = {"vmname": vm_cfg["name"], "hardware": {"numCPU": vm_cfg['hardware']["numCPU"],
                                                              "numCoresPerSocket": vm_cfg['hardware'][
                                                                  "numCoresPerSocket"],
                                                              "memoryMB": vm_cfg['hardware']["memoryMB"]}}
    return HttpResponse(json.dumps(ret, ensure_ascii=False))


def restorevm(request):
    pointid = request.POST.get('pointid')
    vmname = request.POST.get('vmname')
    vcenter_id = request.POST.get('vcenter_id')
    memoryMB = request.POST.get('memoryMB')
    vm_datastore = request.POST.get('vm_datastore')
    numCPU = request.POST.get('numCPU')
    numCoresPerSocket = request.POST.get('numCoresPerSocket')
    ret = {'e': '操作成功', 'r': 0}

    allPlans = VirtualCenterConnectionView().get(request, {'id': vcenter_id}).data
    address = None
    for planAttr in allPlans:
        address = planAttr['address']
        username = planAttr['username']
        password = planAttr['password']
        break

    ext_config = {"vmname": vmname, "vcenter_id": vcenter_id, "memoryMB": memoryMB, "vm_datastore": vm_datastore,
                  "numCPU": numCPU, "numCoresPerSocket": numCoresPerSocket}
    api_request = {
        "host_snapshot": pointid.split('|')[1],
        "ext_config": json.dumps(ext_config, ensure_ascii=False)
    }

    api_response = VirtualHostRestore().post(None, api_request)
    if not status.is_success(api_response.status_code):
        ret['r'] = 1
        e = get_response_error_string(api_response)
        ret['e'] = e
        debug = "TakeOverKVMCreate().post() failed {}".format(api_response.status_code)
        _record_log(request, {
            '操作': 'vmware恢复',
            '结果': '操作失败，e={}'.format(ret['e']),
            'vCenter Server/ESXi': address,
            '虚拟机名称': vmname,
            'debug': debug
        })
        return HttpResponse(json.dumps({"r": 1, "e": e, "debug": debug}, ensure_ascii=False))

    _record_log(request, {
        '操作': 'vmware恢复',
        '结果': ret['e'],
        'vCenter Server/ESXi': address,
        '虚拟机名称': vmname
    })
    return HttpResponse(json.dumps(ret, ensure_ascii=False))


def _fmt_size(size_byte):
    if size_byte is None:
        return '-'
    if size_byte == 0:
        return '-'
    if size_byte < 1024 * 1024 * 1024:
        return '{0:.2f}MB'.format(size_byte / 1024 ** 2)
    return '{0:.2f}GB'.format(size_byte / 1024 ** 3)


def getdatastore(request):
    ret = {'e': '操作成功', 'r': 0}
    vcenter_id = request.GET.get('vcenter_id')
    ret['datastorelist'] = list()
    filter = {'id': vcenter_id}
    allPlans = VirtualCenterConnectionView().get(request, filter).data
    address = None
    for planAttr in allPlans:
        if str(vcenter_id) == str(planAttr['id']):
            address = planAttr['address']
            username = planAttr['username']
            password = planAttr['password']
            port = int(planAttr['port'])
            break

    if not address:
        ret['e'] = '没找到数据存储或数据存储集群。'
        ret['r'] = 2
        return HttpResponse(json.dumps(ret, ensure_ascii=False))

    api = _get_api_instance(address, username, password, port)  # 验证连接信息
    if not api:
        ret['e'] = '创建失败，连接信息无效。'
        ret['r'] = 1
        return HttpResponse(json.dumps(ret, ensure_ascii=False))
    else:
        datastore_list = api.list_data_stores()
        for datastore in datastore_list:
            ret['datastorelist'].append({"id": datastore['name'],
                                         "name": "{name}(可用空间：{freeSpace})".format(
                                             name=datastore['name'],
                                             freeSpace=_fmt_size(datastore['freeSpace'])
                                         )})
    return HttpResponse(json.dumps(ret, ensure_ascii=False))


def check_vmname(request):
    vmname = request.GET.get('vmname')
    vcenter_id = request.GET.get('vcenter_id')
    allPlans = VirtualCenterConnectionView().get(request, {'id': vcenter_id}).data
    address = None
    for planAttr in allPlans:
        address = planAttr['address']
        username = planAttr['username']
        password = planAttr['password']
        port = int(planAttr['port'])
        break

    api = _get_api_instance(address, username, password, port)
    if api is None:
        return HttpResponse(json.dumps({'r': 1, 'e': '连接信息无效'}, ensure_ascii=False))
    tmpname = api.get_vm_by_name(vmname)
    if tmpname is not None:
        return HttpResponse(json.dumps({'r': 2, 'e': '虚拟机：{}，已存在'.format(vmname)}, ensure_ascii=False))
    return HttpResponse(json.dumps({'r': 0, 'e': '操作成功'}, ensure_ascii=False))


def _record_log(request, detail):
    SaveOperationLog(request.user, OperationLog.VMWARE_BACKUP, json.dumps(detail, ensure_ascii=False),
                     get_operator(request))


class stopSysteminfoThread(threading.Thread):
    def __init__(self, host_snapshot_id):
        threading.Thread.__init__(self)
        self.host_snapshot_id = host_snapshot_id

    def run(self):
        boxService.box_service.kvm_remote_procedure_call(
            json.dumps({'action': 'delete', 'key': 'systeminfo_{}'.format(self.host_snapshot_id)}))


def systeminfo(request):
    r = int(request.POST.get('r'))
    host_snapshot_id = request.POST.get('host_snapshot_id')
    stopSysteminfoThread(host_snapshot_id).start()
    if r == 0:
        ret_stdout = json.loads(request.POST.get('stdout'))
        if int(ret_stdout['r']) != 0:
            _logger.error('systeminfo Failed.host_snapshot_id={}'.format(host_snapshot_id))
        systeminfo = ret_stdout['systeminfo']
        snapshot_obj = HostSnapshot.objects.get(id=host_snapshot_id)
        ext_info_obj = json.loads(snapshot_obj.ext_info)
        systeminfo['load'] = True
        ext_info_obj['system_infos'] = systeminfo
        snapshot_obj.ext_info = json.dumps(ext_info_obj)
        snapshot_obj.save(update_fields=['ext_info'])
    return HttpResponse(json.dumps({'r': 0, 'e': '操作成功'}, ensure_ascii=False))
