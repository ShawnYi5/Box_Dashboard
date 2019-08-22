# coding=utf-8
import html
import json
import os
import threading
import time
import uuid

from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.http import HttpResponse
from rest_framework import status

from apiv1.models import UserQuota, BackupTaskSchedule, StorageNode, ExternalStorageDeviceConnection, Host, BackupTask, \
    VolumePool, UserVolumePoolQuota, EnumLink
from apiv1.planScheduler import ArchiveMediaManager
from apiv1.storage_nodes import UserQuotaTools, StorageNodeLogic
from apiv1.views import HostSessionDisks
from apiv1.views import StorageNodes, InternalStorageNodes, ExternalStorageDevices, ExternalStorageDeviceInfo, \
    QuotaManage, StorageNodeInfo, get_response_error_string
from box_dashboard import boxService
from box_dashboard import xdata, xlogging, functions, xdatetime
from box_dashboard.boxService import box_service
from xdashboard.handle.authorize import authorize_init
from xdashboard.handle.backup import is_windows_host, get_linux_disk_total_used_bytes
from xdashboard.handle.logserver import SaveOperationLog
from xdashboard.handle.sysSetting import storage
from xdashboard.models import OperationLog, UserProfile
from xdashboard.request_util import get_operator

_logger = xlogging.getLogger(__name__)


def getlist(request):
    api_response = StorageNodes().get(request)
    if not status.is_success(api_response.status_code):
        e = get_response_error_string(api_response)
        debug = "StorageNodes().get() failed {}".format(api_response.status_code)
        return HttpResponse(json.dumps({"r": 1, "e": e, "debug": debug}, ensure_ascii=False))

    rows = list()
    i = 0
    for element in api_response.data:
        i += 1
        linked = '脱机'
        type = '未知'
        if element["linked"]:
            linked = '联机'
        try:
            type = dict(xdata.STORAGE_NODE_TYPE_CHOICES)[element["type"]]
        except Exception as e:
            pass
        try:
            tt_size = '{0:.2f} GB'.format(element["total_bytes"] / 1024 ** 3)
            av_size = '{0:.2f} GB'.format(element["available_bytes"] / 1024 ** 3)
        except TypeError:
            tt_size, av_size = '0 GB', '0 GB'
        rows.append(
            {"id": element["id"], "ident": element["ident"], "cell": [element["name"], linked, type, tt_size, av_size]})
    retInfo = {"r": 0, "a": "list", "page": "1", "total": 1, "records": i, "rows": rows}

    functions.sort_gird_rows(request, retInfo)
    return HttpResponse(json.dumps(retInfo, ensure_ascii=False))


class Obj(object):
    pass


def rename(request):
    id = request.GET.get('id', '0')
    name = request.GET.get('name', 'none')

    names = [node['name'] for node in StorageNodes().get(request=request).data]
    if name in names:
        return HttpResponse('{"r":"1","e":"存储单元重名!"}')

    myrequest = Obj()
    myrequest.__setattr__("data", {"name": name})
    StorageNodeInfo().put(myrequest, id)
    desc = {'操作': '重命名存储点:{0}'.format(name)}
    SaveOperationLog(
        request.user, OperationLog.TYPE_QUOTA, json.dumps(desc, ensure_ascii=False), get_operator(request))
    return HttpResponse('{"r":"0","e":"操作成功"}')


def delete(request):
    ids = request.GET.get('id', '').split(',')
    errlist = list()
    namelist = list()
    errnamelist = list()
    errReason = list()
    for id in ids:
        user_quotas = QuotaManage().get(request=request, api_request={'node_id': int(id)}).data
        if not user_quotas:
            info = StorageNode.objects.filter(id=id)
            if info:
                nodetype = StorageNodeLogic.convert_is_internal_to_type(info[0].internal, info[0].config, info[0].ident)
                if nodetype != xdata.STORAGE_NODE_TYPE_VOLUME:
                    namelist.append(info[0].name)
                    StorageNodeInfo().delete(request, id)
                else:
                    errnamelist.append(info[0].name)
                    errlist.append({"id": id})
                    if '内部存储不能删除' not in errReason:
                        errReason.append('内部存储不能删除')
        else:
            info = StorageNode.objects.filter(id=id)
            if info:
                errnamelist.append(info[0].name)
            errlist.append({"id": id})
            if '存储单元池已分配给用户，请先移除其他用户分配' not in errReason:
                errReason.append('存储单元池已分配给用户，请先移除其他用户分配')

    mylog = dict()
    mylog['操作'] = '删除存储点'
    if len(namelist) > 0:
        mylog['删除成功'] = namelist

    if len(errnamelist) > 0:
        mylog['删除失败'] = errnamelist
        mylog['原因'] = '；'.join(errReason)

    SaveOperationLog(
        request.user, OperationLog.TYPE_QUOTA, json.dumps(mylog, ensure_ascii=False), get_operator(request))

    if len(errlist) > 0:
        e = "共有{}存储单元池删除失败。原因：{}。".format(len(errlist), '；'.join(errReason))
        return HttpResponse(
            json.dumps({"r": 1, "e": e}, ensure_ascii=False))

    return HttpResponse('{"r":"0","e":"操作成功"}')


def getlocallist(request):
    api_response = InternalStorageNodes().get(request)
    if not status.is_success(api_response.status_code):
        e = get_response_error_string(api_response)
        debug = "InternalStorageNodes().get() failed {}".format(api_response.status_code)
        return HttpResponse(json.dumps({"r": 1, "e": e, "debug": debug}, ensure_ascii=False))
    rows = list()
    i = 0
    for element in api_response.data:
        if element["status"] != 0:
            i += 1
            name = '本地存储设备' + str(time.time())
            rows.append(
                {"id": i, "cell": [name, element["status"], element["device_name"], element["logic_device_path"],
                                   element["device_size"], element["old_node_id"], element["status"]]})
    retInfo = {"r": 0, "a": "list", "page": "1", "total": 1, "records": 1, "rows": rows}
    return HttpResponse(json.dumps(retInfo, ensure_ascii=False))


def getRemotelist(request):
    device_id = int(request.GET.get('id', 0))
    refresh = request.GET.get('refresh', 'False')
    api_response = ExternalStorageDeviceInfo().get(request=request, device_id=device_id,
                                                   api_request={'refresh': refresh})
    if not status.is_success(api_response.status_code):
        e = get_response_error_string(api_response)
        debug = "ExternalStorageDeviceInfo().get() failed {}".format(api_response.status_code)
        return HttpResponse(json.dumps({"r": 1, "e": e, "debug": debug}, ensure_ascii=False))
    rows = list()
    i = 0
    for element in api_response.data:
        if element["status"] != 0:
            i += 1
            rows.append({"id": i, "cell": [element["device_name"], element["status"], element["lun_name"],
                                           element["device_size"],
                                           element["logic_device_path"], element["old_node_id"], element["status"]]})
    retInfo = {"r": 0, "a": "list", "page": "1", "total": 1, "records": 1, "rows": rows}
    return HttpResponse(json.dumps(retInfo, ensure_ascii=False))


def get_bytes(str_format):
    str_format = str_format.upper().strip()
    if 'KB' in str_format:
        return int(float(str_format.split('KB')[0]) * 1024 ** 1)
    if 'MB' in str_format:
        return int(float(str_format.split('MB')[0]) * 1024 ** 2)
    if 'GB' in str_format:
        return int(float(str_format.split('GB')[0]) * 1024 ** 3)
    if 'TB' in str_format:
        return int(float(str_format.split('TB')[0]) * 1024 ** 4)
    return 0


# 添加节点时，判断超过授权容量(TB)
def check_authorize_at_storage(request, is_external=False):
    if is_external:
        device_id = request.GET['device_id']
        device_object = ExternalStorageDeviceConnection.objects.get(id=device_id)
        nodes_external = StorageNodeLogic.get_external_storage_nodes(device_object)
        nodes_addible = list(filter(lambda _node: _node['status'], nodes_external))  # 所有外部可添加节点
    else:
        nodes_internal = InternalStorageNodes().get(request).data
        nodes_addible = list(filter(lambda _node: _node['status'], nodes_internal))  # 所有内部可添加节点

    # 目前已添加节点的总空间
    nodes_added = StorageNodes().get(request).data
    total_bytes = 0
    for node in nodes_added:
        total_bytes += node['total_bytes']

    # 将要添加节点的总空间
    will_add_nodes = json.loads(request.GET.get('nodes', '[]'))
    will_add_path = list()
    for node in will_add_nodes:
        will_add_path.append(node['logic_device_path'])
    nodes_will_add = list(filter(lambda _node: _node['logic_device_path'] in will_add_path, nodes_addible))

    will_add_bytes = 0
    for node in nodes_will_add:
        will_add_bytes += get_bytes(node['device_size'])

    # 授权允许的空间值
    json_txt = authorize_init.get_authorize_plaintext_from_local()
    if json_txt is None:
        return False, '读取授权文件异常'
    val = authorize_init.get_license_val_by_guid('storage_capacity', json_txt)
    if val is None:
        return False, '读取授权文件异常'
    authorized_bytes = int(val) * 1024 ** 4

    # if total_bytes + will_add_bytes > authorized_bytes:
    #     return False, '添加节点容量大小超过授权允许值({0}TB), 目前已添加容量{1:.2f}TB'.format(val, total_bytes / 1024 ** 4)

    allow_add_bytes = authorized_bytes - total_bytes
    allow_add_GB = int(allow_add_bytes / 1000 ** 3 + 1.0)
    allow_add_bytes = allow_add_GB * 1024 ** 3

    if will_add_bytes > allow_add_bytes:
        return False, '添加节点容量大小超过授权允许值({0}TB), 目前已添加容量{1:.2f}TB'.format(val, total_bytes / 1024 ** 4)

    return True, ''


def AddLocalStorage(request):
    result = check_authorize_at_storage(request)
    if not result[0]:
        return HttpResponse(json.dumps({"r": "1", "e": result[1], "list": []}))

    forceformat = request.GET.get('forceformat', False)
    nodes = json.loads(request.GET.get('nodes', '[]'))

    if forceformat == '1':
        forceformat = True
    else:
        forceformat = False

    error = list()
    correct = list()
    for node in nodes:
        old_node_id = None
        if node["old_node_id"]:
            old_node_id = int(node["old_node_id"])
        api_request = {
            "force_format": forceformat,
            "name": node["display_name"],
            "logic_device_path": node["logic_device_path"],
            "old_node_id": old_node_id,
            "status": node["status"]
        }

        _logger.info(json.dumps(api_request))

        ret = InternalStorageNodes().post(request=request, api_request=api_request)
        if not status.is_success(ret.status_code):
            info = {"name": api_request["name"], "err": ret.data}
            error.append(info)
        else:
            info = {"name": api_request["name"]}
            correct.append(info)
    mylog = dict()
    mylog["操作"] = "添加本地存储"
    if len(correct) > 0:
        mylog["成功"] = correct
    if len(error) > 0:
        mylog["失败"] = error
    SaveOperationLog(
        request.user, OperationLog.TYPE_QUOTA, json.dumps(mylog, ensure_ascii=False), get_operator(request))

    if len(error) == 0:
        return HttpResponse('{"r":"0","e":"成功添加"}')

    return HttpResponse(json.dumps({"r": "1", "e": "有{}个存储添加失败".format(len(error)), "list": error}, ensure_ascii=False))


def AddRemoteStorage(request):
    result = check_authorize_at_storage(request, True)
    if not result[0]:
        return HttpResponse(json.dumps({"r": "1", "e": result[1], "list": []}))

    forceformat = request.GET.get('forceformat', False)
    device_id = request.GET.get('device_id', 0)
    nodes = json.loads(request.GET.get('nodes', '[]'))

    if forceformat == '1':
        forceformat = True
    else:
        forceformat = False

    error = list()
    correct = list()
    for node in nodes:
        old_node_id = None
        if node["old_node_id"]:
            old_node_id = int(node["old_node_id"])
        api_request = {
            "force_format": forceformat,
            "name": node["display_name"],
            "logic_device_path": node["logic_device_path"],
            "old_node_id": old_node_id,
            "status": node["status"]
        }
        _logger.info(json.dumps(api_request))
        ret = ExternalStorageDeviceInfo().post(request=request, device_id=device_id, api_request=api_request)
        if not status.is_success(ret.status_code):
            info = {"name": api_request["name"], "err": ret.data}
            error.append(info)
        else:
            info = {"name": api_request["name"]}
            correct.append(info)

    mylog = dict()
    mylog["操作"] = "添加外部存储"
    if len(correct) > 0:
        mylog["成功"] = correct
    if len(error) > 0:
        mylog["失败"] = error
    SaveOperationLog(
        request.user, OperationLog.TYPE_QUOTA, json.dumps(mylog, ensure_ascii=False), get_operator(request))

    if len(error) == 0:
        return HttpResponse('{"r":"0","e":"成功添加"}')

    return HttpResponse(json.dumps({"r": "1", "e": "有{}个存储添加失败".format(len(error)), "list": error}, ensure_ascii=False))


def getIQNName(request):
    return HttpResponse('{"r":"0","e":"操作成功","name":"%s"}' % box_service.getLocalIqn())


def setIQNName(request):
    name = request.GET.get('name', 'none')
    try:
        box_service.setLocalIqn(name)
    except Exception as e:
        return HttpResponse('{"r": "1","e": "' + str(e) + '"}')
    desc = {'操作': '更改IQN:{0}'.format(name)}
    SaveOperationLog(
        request.user, OperationLog.TYPE_QUOTA, json.dumps(desc, ensure_ascii=False), get_operator(request))
    return HttpResponse('{"r":"0","e":"操作成功","name":"%s"}' % name)


def getCHAP(request):
    chapname = ''
    chappwd = ''
    chap = box_service.getGlobalDoubleChap()
    if chap:
        chapname = chap[1]
        chappwd = chap[2]
    return HttpResponse('{"r":"0","e":"操作成功","chapname":"%s","chappwd":"%s"}' % (chapname, chappwd))


def setCHAP(request):
    chapname = request.GET.get('chapname', '')
    chappwd = request.GET.get('chappwd', '')
    try:
        box_service.setGlobalDoubleChap(chapname, chappwd)
    except Exception as e:
        return HttpResponse('{"r": "1","e": "' + str(e) + '"}')
    desc = {'操作': '设置CHAP:{0},{1}'.format(chapname, chappwd)}
    SaveOperationLog(
        request.user, OperationLog.TYPE_QUOTA, json.dumps(desc, ensure_ascii=False), get_operator(request))
    return HttpResponse('{"r":"0","e":"操作成功"}')


def clearChap(request):
    try:
        box_service.setGlobalDoubleChap('', '')
    except Exception as e:
        return HttpResponse('{"r": "1","e": "' + str(e) + '"}')
    desc = {'操作': '清除CHAP'}
    SaveOperationLog(request.user, OperationLog.TYPE_QUOTA, json.dumps(desc, ensure_ascii=False), get_operator(request))
    return HttpResponse('{"r":"0","e":"操作成功"}')


def getSAN(request):
    ip = ''
    port = '3260'
    usechap = '0'
    chapname = ''
    chappwd = ''
    use_mutual_authentication = '0'
    return HttpResponse(
        '{"r":"0","e":"操作成功","ip":"%s","port":"%s","usechap":"%s","chapname":"%s","chappwd":"%s","use_mutual_authentication":"%s"}' %
        (ip, port, usechap, chapname, chappwd, use_mutual_authentication))


def Connectiscsi(request):
    ip = request.GET.get('ip', '')
    port = request.GET.get('port', '3260')
    usechap = request.GET.get('usechap', '0')
    force = request.GET.get('force', '0')
    user_name = request.GET.get('username', '')
    password = request.GET.get('pwd', '')
    if usechap == "1":
        use_chap = True
    else:
        use_chap = False
        user_name = None
        password = None

    if force == "1":
        force = True
    else:
        force = False

    api_request = {
        "ip": ip,
        "port": port,
        "use_chap": use_chap,
        "force": force,
        "user_name": user_name,
        "password": password
    }

    _logger.info(json.dumps(api_request))

    ret = ExternalStorageDevices().post(request=request, api_request=api_request)
    if not status.is_success(ret.status_code):
        r = ret.status_code
        if r == 0:
            r = 1
        retjson = {"r": r, "e": "ExternalStorageDevices().post() Failed，status（{}）".format(ret.status_code),
                   "code": ret.status_code, "err": ret.data}
        return HttpResponse(json.dumps(retjson, ensure_ascii=False))
    return HttpResponse('{"r":"0","e":"操作成功","id":"%d","iqn":"%s"}' % (ret.data['id'], ret.data['iqn']))


# 获取所有,存储节点信息
def getstoragelist(request):
    id = request.GET.get('id', 'root')
    if id == '':
        id = 'root'

    if id == 'root':
        nodes = StorageNodes().get(request=None).data
        ret_info = list()
        for storage_node in nodes:
            ret_info.append({'id': storage_node['id'], "label": storage_node['name'],
                             "branch": [], "inode": False, "open": False, "radio": True})
        return HttpResponse(json.dumps(ret_info, ensure_ascii=False))


def getstoragepoollist(request):
    id = request.GET.get('id', 'root')
    if id == '':
        id = 'root'

    if id == 'root':
        nodes = VolumePool.objects.all()
        ret_info = list()
        for storage_node in nodes:
            ret_info.append({'id': storage_node.id, "label": storage_node.name,
                             "branch": [], "inode": False, "open": False, "radio": True})
        return HttpResponse(json.dumps(ret_info, ensure_ascii=False))


# 获取存储节点的，所有用户配额信息
def getquotalist(request):
    query_params = request.GET
    node_id = int(query_params.get('id'))
    user_quotas = QuotaManage().get(request=request, api_request={'node_id': node_id}).data
    if not user_quotas:
        return HttpResponse('{"r": 0, "a": "list", "page": "1", "total": 1, "records": 1, "rows": []}')

    rows = list()
    for quota in user_quotas:
        try:
            used_mb = storage.user_used_size_mb_in_a_node(node_id, quota['user_id'])
        except Exception:
            used_mb = 0
        rows.append({"id": quota['quota_id'], "cell": [quota['username'], quota['user_id'], quota['quota_total'],
                                                       quota['caution_size'], quota['available_size'], used_mb]})

    retInfo = {"r": 0, "a": "list", "page": "1", "total": 1, "records": 1, "rows": rows}

    functions.sort_gird_rows(request, retInfo)
    return HttpResponse(json.dumps(retInfo, ensure_ascii=False))


def getquotavolumelist(request):
    query_params = request.GET
    node_id = int(query_params.get('id'))
    user_quotas = UserVolumePoolQuota.objects.filter(volume_pool_node_id=node_id).all()
    if not user_quotas:
        return HttpResponse('{"r": 0, "a": "list", "page": "1", "total": 1, "records": 1, "rows": []}')

    rows = list()
    for quota in user_quotas:
        if quota.deleted is False:
            rows.append({"id": quota.id, "cell": [quota.user.username, quota.user_id, quota.quota_size,
                                                  quota.caution_size, quota.available_size, 0]})

    retInfo = {"r": 0, "a": "list", "page": "1", "total": 1, "records": 1, "rows": rows}

    functions.sort_gird_rows(request, retInfo)
    return HttpResponse(json.dumps(retInfo, ensure_ascii=False))


def haveQuota(user_quotas, user_id):
    for quota in user_quotas:
        if quota['user_id'] == user_id:
            return True
    return False


# 获取用户列表
def getuserlist(request):
    node_id = int(request.GET.get('quotaid', default='-1'))
    api_response = QuotaManage().get(request=request, api_request={'node_id': node_id})
    if not status.is_success(api_response.status_code):
        user_quotas = None
    else:
        user_quotas = api_response.data

    Users = User.objects.filter(is_superuser=False, is_active=True, userprofile__user_type=UserProfile.NORMAL_USER)
    userlist = list()
    if Users:
        for user in Users:
            if user_quotas is None:
                userlist.append({"name": user.username, "value": user.id})
            if user_quotas and not haveQuota(user_quotas, user.id):
                userlist.append({"name": user.username, "value": user.id})

    retInfo = {'r': 0, 'e': '操作成功', 'list': userlist}
    jsonStr = json.dumps(retInfo, ensure_ascii=False)
    return HttpResponse(jsonStr)


# 4种单位: MB, GB, TB, PB ---> MB
def value_convert_to_MB(value, unit, goalunit='MB'):
    a = {'B': 0, 'KB': 1, 'MB': 2, 'GB': 3, 'TB': 4, 'PB': 5}
    if a[unit] - a[goalunit] == 0:
        return value
    des = pow(1024, abs(a[unit] - a[goalunit]))
    if a[unit] - a[goalunit] > 0:
        return value * des
    else:
        return value / des


# 添加用户配额
def addquota(request):
    query_params = request.GET

    quota_unit = query_params.get('limitunit', default='')
    caution_unit = query_params.get('waringunit', default='')
    quota_type = query_params.get('limittype', default='1')

    node_id = int(query_params.get('quotaid', default='-1'))
    if node_id == -2:
        ident = query_params.get('storage_node_ident', default='')
        storage_node = StorageNode.objects.get(ident=ident)
        node_id = storage_node.id
    user_id = int(query_params.get('userid', default='-1'))
    if user_id == -1:
        username = query_params.get('username', default='')
        user = User.objects.get(username=username)
        user_id = user.id
    if user_id == -1:
        return HttpResponse('{"r":"1","e":"操作失败,至少选择一个用户"}')

    # 配额不限制, 大小为-1
    if quota_type == '1':
        quota_size = xdata.USER_QUOTA_IS_NOT_LIMIT_VALUE
    else:
        limit = query_params['limit']
        quota_size = value_convert_to_MB(int(limit), quota_unit)
        # 若设定配额>节点可用，则失败
        node = UserQuotaTools.get_storage_node_detail(node_id, False)
        node_avail_MB = node['available_bytes'] / 1024 ** 2
        if quota_size > node_avail_MB:
            return HttpResponse('{"r":"1","e":"超过存储单元可用空间"}')

    # 警告不限制，大小为-1
    waring = query_params['waring']
    if waring == '' or int(waring) == 0:
        caution_size = xdata.USER_QUOTA_IS_NOT_WARING_VALUE
    else:
        caution_size = value_convert_to_MB(int(waring), caution_unit)
    if UserQuota.objects.filter(storage_node_id=node_id, user_id=user_id, deleted=False).exists():
        return HttpResponse('{"r":"1","e":"用户在该节点已存在配额，添加失败"}')

    resp = QuotaManage().post(request=None, api_request={'node_id': node_id, 'user_id': user_id,
                                                         'quota_size': quota_size, 'caution_size': caution_size})
    if resp.status_code == status.HTTP_201_CREATED:
        mylog = dict()
        mylog['操作'] = '添加用户配额'
        mylog['用户'] = User.objects.get(id=user_id).username
        mylog['配额'] = '不限制' if quota_type == '1' else '{0:.1f}{1}'.format(float(limit), quota_unit)
        if waring == '' or int(waring) == 0:
            logwaring = '不限制'
        else:
            logwaring = str(waring) + caution_unit
        mylog['警告等级'] = logwaring
        SaveOperationLog(
            request.user, OperationLog.TYPE_QUOTA, json.dumps(mylog, ensure_ascii=False), get_operator(request))
        return HttpResponse('{"r":"0","e":"操作成功"}')

    if not StorageNode.objects.filter(id=node_id):
        return HttpResponse('{"r":"1","e":"节点离线或不可用"}')
    return HttpResponse('{"r":"1","e":"操作失败"}')


# 获取某个存储节点：总空间/剩余空间
def getquotasize(request):
    query_params = request.GET
    node_id = int(query_params.get('id'))
    nodes = StorageNodes().get(request=None).data
    for node_info in nodes:
        if node_info['id'] == node_id:
            if node_info['total_bytes'] and node_info['available_bytes']:
                return HttpResponse('{{"r":"0","e":"操作成功","total":"{0:.2f}GB","free":"{1:.2f}GB"}}'.format(
                    node_info['total_bytes'] / 1024 ** 3, node_info['available_bytes'] / 1024 ** 3))
    return HttpResponse('{"r":"1","e":"存储节点离线"}')


# 删除指定的配额记录
def deletequota(request):
    query_params = request.GET
    quota_ids = query_params.get('ids').split(',')
    is_confirm = query_params.get('confirm', '')
    objs = _check_has_backupschedule(quota_ids)
    for quota_id in quota_ids:
        if is_confirm and objs:
            for i in objs:
                i.enabled = False
                i.save()
        if objs and not is_confirm:
            return HttpResponse('{"r":"1","e":"有计划"}')
        if not is_confirm:
            return HttpResponse('{"r":"2","e":"无计划"}')

        user_quotas = UserQuota.objects.filter(id=quota_id)
        if user_quotas:
            mylog = dict()
            mylog["操作"] = '删除存储配额'
            mylog["存储单元池"] = user_quotas[0].storage_node.name
            mylog["用户名"] = user_quotas[0].user.username
            SaveOperationLog(
                request.user, OperationLog.TYPE_QUOTA, json.dumps(mylog, ensure_ascii=False), get_operator(request))

        QuotaManage().delete(request=None, api_request={'quota_id': int(quota_id)})

    return HttpResponse('{"r":"0","e":"操作成功"}')


def _check_has_backupschedule(ids):
    objs = list()
    for id in ids:
        user_qutoa_obj = UserQuota.objects.get(id=id)
        objs.extend(BackupTaskSchedule.objects.filter(storage_node_ident=user_qutoa_obj.storage_node.ident,
                                                      enabled=True, deleted=False,
                                                      host__user=user_qutoa_obj.user).all())
    return objs


# 获取指定的配额信息
def getquotainfo(request):
    id = int(request.GET.get('id', '0'))
    quotas = UserQuota.objects.filter(id=id)
    quota_size = -1
    caution_size = -1
    for quota in quotas:
        quota_size = quota.quota_size
        caution_size = quota.caution_size
    return HttpResponse(
        json.dumps({"r": 0, "e": "操作成功", "quota_size": quota_size, "caution_size": caution_size}, ensure_ascii=False))


# 修改指定配额
def editquota(request):
    query_params = request.GET
    quota_unit = query_params['limitunit']
    caution_unit = query_params['waringunit']
    quota_type = query_params['limittype']
    quota_id = int(query_params['id'])

    # 配额不限制
    if quota_type == '1':
        quota_size = xdata.USER_QUOTA_IS_NOT_LIMIT_VALUE
    else:
        quota_size = value_convert_to_MB(int(query_params['limit']), quota_unit)
    waring = query_params.get('waring', default='0')
    if not waring:
        waring = xdata.USER_QUOTA_IS_NOT_WARING_VALUE
    caution_size = value_convert_to_MB(int(waring), caution_unit)

    resp = QuotaManage().put(request=None,
                             api_request={'quota_id': quota_id, 'quota_size': quota_size, 'caution_size': caution_size})
    if resp.status_code == status.HTTP_202_ACCEPTED:
        user_quotas = UserQuota.objects.filter(id=quota_id)
        if user_quotas:
            mylog = dict()
            mylog['操作'] = '编辑用户配额'
            mylog['用户'] = user_quotas[0].user.username
            mylog['配额'] = '不限制' if quota_type == '1' else '{0:.1f}{1}'.format(float(query_params['limit']), quota_unit)
            if int(waring) == 0:
                logwaring = '不限制'
            else:
                logwaring = str(waring) + caution_unit
            mylog['警告等级'] = logwaring
            SaveOperationLog(
                request.user, OperationLog.TYPE_QUOTA, json.dumps(mylog, ensure_ascii=False), get_operator(request))

        return HttpResponse('{"r":"0","e":"操作成功"}')

    return HttpResponse('{"r":"1","e":"操作失败"}')


def getusercount(request):
    all_quota = UserQuota.objects.filter(deleted=False)
    user_list = set()
    for quota in all_quota:
        user_list.add(quota.user)
    return HttpResponse('{"r":"0","e":"操作成功","count":"%s"}' % len(user_list))


def RumCmd(cmd):
    box_service.installfun(cmd)


def bakoffline(request):
    path = request.GET.get('path', 'none')
    if path == 'none':
        return HttpResponse(json.dumps({"r": 1, "e": "参数不正确"}, ensure_ascii=False))

    # tar cvf /dev/st0 /home //备份/home目录

    cmd = 'tar cvf {} /home/mnt/nodes'.format(path)

    threading.Thread(target=RumCmd, args=(cmd,)).start()

    return HttpResponse(json.dumps({"r": 0, "e": cmd}, ensure_ascii=False))


def reoffline(request):
    path = request.GET.get('path', 'none')
    if path == 'none':
        return HttpResponse(json.dumps({"r": 1, "e": "参数不正确"}, ensure_ascii=False))

    # tar xvf /dev/st0 //恢复到当前目录
    cmd = 'cd / && tar xvf {}'.format(path)

    threading.Thread(target=RumCmd, args=(cmd,)).start()

    return HttpResponse(json.dumps({"r": 0, "e": cmd}, ensure_ascii=False))


def get_all_nodes_hosts_usage(request):
    """
    获取所有的 StorageNode, 及其中的 Host 文件的大小(MB)
    :return [] or [{'node_ident': 'xx', 'host_ident': 'xx', 'host_size_MB': int_MB}]
    """
    all_hosts = [host.ident for host in Host.objects.all()]
    all_nodes_usage = []
    for node in StorageNode.objects.filter(deleted=False):
        hosts_parent_dir = r'{}/images'.format(node.path)
        if not box_service.isFolderExist(hosts_parent_dir):
            continue

        for host_ident in os.listdir(hosts_parent_dir):
            if host_ident not in all_hosts:
                continue

            host_folder = r'{}/{}'.format(hosts_parent_dir, host_ident)
            cmd = 'du -ms {dir}'.format(dir=host_folder)
            code, lines = boxService.box_service.runCmd(cmd, False)
            if code != 0 or len(lines) != 1:
                ret_data = {'r': 1, 'e': 'get_all_nodes_hosts_usage failed. {} {}'.format(code, lines)}
                return HttpResponse(json.dumps(ret_data, ensure_ascii=False))

            host_size_MB = int(lines[0].split()[0].strip())

            all_nodes_usage.append({
                'node_ident': node.ident,
                'host_ident': host_ident,
                'host_size_MB': host_size_MB
            })

    ret_data = {'r': 0, 'e': 'ok', 'all_nodes_usage': all_nodes_usage}
    return HttpResponse(json.dumps(ret_data, ensure_ascii=False))


def get_hosts_storage_detail(request):
    res = json.loads(get_all_nodes_hosts_usage_yun_easy(request).content.decode('utf-8'))
    if res['r'] == 1:
        return HttpResponse(json.dumps({'r': 1, 'e': res['e']}))
    for host_detail in res['all_nodes_usage']:
        backup_task_schedule = BackupTaskSchedule.objects.filter(enabled=True, deleted=False,
                                                                 host__ident=host_detail['host_ident']
                                                                 ).order_by('next_run_date').first()
        if not backup_task_schedule:
            next_run_date = None
        else:
            next_run_date = backup_task_schedule.next_run_date
        api_response = HostSessionDisks().get(request=request, ident=host_detail['host_ident'])
        disks = api_response.data if api_response.data else list()
        disks_size = 0
        disks_used = 0
        disks_name = list()
        for disk in disks:
            disks_name.append(disk['name'])
            disks_size += int(disk['bytes'])
            disks_used += int(disk['used_bytes'])
        disks_size = int((disks_size / (1024 ** 3)))
        disks_used = int((disks_used / (1024 ** 3)))
        host_detail['total'] = disks_size
        host_detail['used'] = disks_used if is_windows_host(host_detail['host_ident']) \
            else int(float(get_linux_disk_total_used_bytes(host_detail['host_ident'])))
        host_detail['next_run_date'] = next_run_date.strftime(xdatetime.FORMAT_WITH_MICROSECOND_2) \
            if next_run_date else ''
        host_detail['host_backup_size'] = int(int(host_detail['host_size_MB']) / 1024)

        host_detail['backup_tasks'] = list()
        for backup_task in BackupTask.objects.filter(
                schedule__host__ident=host_detail['host_ident']).order_by('-start_datetime'):
            one_task = {'task_id': backup_task.id}
            if backup_task.host_snapshot:
                if backup_task.host_snapshot.deleting or backup_task.host_snapshot.deleted:
                    one_task['host_snapshot'] = 'none'
                else:
                    one_task['host_snapshot'] = 'exist'
            else:
                if backup_task.finish_datetime is None:
                    one_task['host_snapshot'] = 'creating'
                else:
                    continue
            host_detail['backup_tasks'].append(one_task)
            if len(host_detail['backup_tasks']) > 3:
                break

    return HttpResponse(json.dumps({'r': 1, 'hosts_storage_detail': res['all_nodes_usage'], 'e': '操作成功'}))


def get_all_nodes_hosts_usage_yun_easy(request):
    """
    获取所有的 StorageNode, 及其中的 Host 文件的大小(MB)
    :return [] or [{'node_ident': 'xx', 'host_ident': 'xx', 'host_size_MB': int_MB}]
    """
    hosts = Host.objects.filter(user=request.user)
    all_hosts = [host.ident for host in hosts]
    all_nodes_usage = []
    for node in StorageNode.objects.filter(deleted=False):
        hosts_parent_dir = r'{}/images'.format(node.path)

        for host_ident in all_hosts:
            host_folder = r'{}/{}'.format(hosts_parent_dir, host_ident)
            if not box_service.isFolderExist(host_folder):
                host_size_mb = 0
            else:
                cmd = 'du -ms {dir}'.format(dir=host_folder)
                code, lines = boxService.box_service.runCmd(cmd, False)
                if code != 0 or len(lines) != 1:
                    ret_data = {'r': 1, 'e': 'get_all_nodes_hosts_usage failed. {} {}'.format(code, lines)}
                    return HttpResponse(json.dumps(ret_data, ensure_ascii=False))
                host_size_mb = int(lines[0].split()[0].strip())
            all_nodes_usage.append({
                'node_ident': node.ident,
                'host_ident': host_ident,
                'host_size_MB': host_size_mb
            })

    ret_data = {'r': 0, 'e': 'ok', 'all_nodes_usage': all_nodes_usage}
    return HttpResponse(json.dumps(ret_data, ensure_ascii=False))


def clear_storage(request):
    storage_id = request.GET['storage_id']
    rsp = StorageNodeInfo().post(request, storage_id,
                                 {'type': request.GET['type'], 'admin_pwd': request.GET['admin_pwd']})
    if status.is_success(rsp.status_code):
        mylog = dict()
        mylog['操作'] = '清除存储节点备份数据'
        mylog['存储节点名称'] = '{}'.format(request.GET['name'])
        mylog['存储节点ID'] = '{}'.format(storage_id)
        SaveOperationLog(request.user, OperationLog.TYPE_QUOTA, json.dumps(mylog, ensure_ascii=False))
        return HttpResponse(json.dumps({'r': 0, 'e': ''}))
    else:
        return HttpResponse(
            json.dumps({'r': 1, 'e': '清除失败，{}'.format(rsp.data)}))


def tapesinfo(request):
    params = {'action': 'enum_mc_hw_info', 'info': {'fun': 'enum_mc'}}
    tapes = json.loads(json.loads(box_service.archiveMediaOperation(json.dumps(params)))['rev'])
    rows = list()
    for tape in tapes:
        library = '产商：' + tape['VendorID'].replace('_', '') + ',产品：' + tape['ProductID'].replace('_', '')
        for index, info in enumerate(tape['MCInfo']):
            if info['status'].lower() != 'empty':
                lable = info['VolumeTag']
                vpl_obj = VolumePool.objects.all()
                vpl = ''
                for obj in vpl_obj:
                    tapas = json.loads(obj.tapas)
                    for tapa in tapas:
                        if tapas[tapa] == lable:
                            vpl = obj.name
                            break
                    else:
                        continue
                    break
                if vpl == '':
                    vpl = '未分配'
                tmp = {'id': index, 'cell': [index, lable, vpl, info['drv_ID'], library]}
                rows.append(tmp)
    paramsQD = request.GET
    perPage = paramsQD.get('rows', '10')  # 设置每页条数
    targPage = paramsQD.get('page', '1')  # 返回第几页
    search_key = request.GET.get('s_key', None)
    paginator = Paginator(object_list=rows, per_page=perPage)
    plansNum = paginator.count
    pagesNum = paginator.num_pages
    getPlans = paginator.page(targPage).object_list
    retInfo = {'r': 0, 'a': 'list', 'page': targPage, 'total': pagesNum, 'records': plansNum, 'rows': getPlans}
    functions.sort_gird_rows(request, retInfo)
    jsonStr = json.dumps(retInfo, ensure_ascii=False)

    return HttpResponse(jsonStr)


def libraryinfo(request):
    params = {'action': 'enum_mc_hw_info', 'info': {'fun': 'enum_mc'}}
    tapes = json.loads(json.loads(box_service.archiveMediaOperation(json.dumps(params)))['rev'])
    rows = list()
    for index, tape in enumerate(tapes):
        vendorid = tape['VendorID'].replace('_', '')
        productid = tape['ProductID'].replace('_', '')
        inoutnumber = len([s for s in tape['MCInfo'] if s['BoxType'] == 'InoutBox'])
        tapesnumber = len([s for s in tape['MCInfo'] if s['status'].lower() != 'empty'])
        drivernumber = len([s for s in tape['MCInfo'] if s['BoxType'] == 'Tape'])
        slotnumber = len(tape['MCInfo']) - drivernumber  # 槽位数
        tmp = {'id': index, 'cell': [index, vendorid, productid, drivernumber, tapesnumber, inoutnumber, slotnumber]}
        rows.append(tmp)
    paramsQD = request.GET
    perPage = paramsQD.get('rows', '10')  # 设置每页条数
    targPage = paramsQD.get('page', '1')  # 返回第几页
    search_key = request.GET.get('s_key', None)
    paginator = Paginator(object_list=rows, per_page=perPage)
    plansNum = paginator.count
    pagesNum = paginator.num_pages
    getPlans = paginator.page(targPage).object_list
    retInfo = {'r': 0, 'a': 'list', 'page': targPage, 'total': pagesNum, 'records': plansNum, 'rows': getPlans}
    functions.sort_gird_rows(request, retInfo)
    jsonStr = json.dumps(retInfo, ensure_ascii=False)

    return HttpResponse(jsonStr)


def tapesmgrlist(request):
    paramsQD = request.GET
    perPage = paramsQD.get('rows', '10')  # 设置每页条数
    targPage = paramsQD.get('page', '1')  # 返回第几页
    search_key = request.GET.get('s_key', None)
    alljilu = VolumePool.objects.all()
    params1 = {'action': 'enum_mc_hw_info', 'info': {'fun': 'enum_tape'}}
    driverinfos = json.loads(json.loads(box_service.archiveMediaOperation(json.dumps(params1)))['rev'])

    rows = list()
    for index, b in enumerate(alljilu):
        if b.cycle == 0:
            protect_cycle = '未开启'
        else:
            if b.cycle_type == 1:
                so = '天'
            if b.cycle_type == 2:
                so = '周'
            if b.cycle_type == 3:
                so = '月'
            protect_cycle = str(b.cycle) + so
        driverid = b.driver
        _logger.info('alisdfsfsdfsfddfd:{}'.format(b.name, b.tapas))
        driver_info = ''
        for s in driverinfos:
            if driverid == s['SerialNumber']:
                driver_info = '产商：' + s['VendorID'].replace('_', '') + ',产品：' + s['ProductID'].replace('_', '')
                break
        tapas = json.loads(b.tapas)
        tapes_info = ''
        for key in tapas.keys():
            if tapas[key] is not None:
                tapes_info += str(key) + ':' + tapas[key] + ','
        tmp = {'id': b.id, 'cell': [b.name, protect_cycle, driver_info, tapes_info]}
        rows.append(tmp)
    paginator = Paginator(object_list=rows, per_page=perPage)
    plansNum = paginator.count
    pagesNum = paginator.num_pages
    getPlans = paginator.page(targPage).object_list
    retInfo = {'r': 0, 'a': 'list', 'page': targPage, 'total': pagesNum, 'records': plansNum, 'rows': getPlans}
    functions.sort_gird_rows(request, retInfo)
    jsonStr = json.dumps(retInfo, ensure_ascii=False)

    return HttpResponse(jsonStr)


def driverinfo(request):
    params1 = {'action': 'enum_mc_hw_info', 'info': {'fun': 'enum_tape'}}
    driverinfos = json.loads(json.loads(box_service.archiveMediaOperation(json.dumps(params1)))['rev'])
    params2 = {'action': 'enum_mc_hw_info', 'info': {'fun': 'enum_mc'}}
    mcinfo = json.loads(json.loads(box_service.archiveMediaOperation(json.dumps(params2)))['rev'])
    rows = list()
    for index, driverinfo in enumerate(driverinfos):
        try:
            link = EnumLink.objects.get(driver=driverinfo['SerialNumber'])
        except EnumLink.DoesNotExist:
            continue
        vendorid = driverinfo['VendorID'].replace('_', '')
        productid = driverinfo['ProductID'].replace('_', '')
        serialnumber = driverinfo['SerialNumber'].replace('_', '')
        dependmc = [mc for mc in mcinfo if mc['SerialNumber'] == link.library][0]
        library_vd = dependmc['VendorID'].replace('_', '')
        library_pd = dependmc['ProductID'].replace('_', '')
        library = '产商：' + library_vd + '产品：' + library_pd + '驱动器：' + str(link.drvid)
        dr_open = ['', '打开'][driverinfo['dr_open'] is True]
        online = ['', '在线'][driverinfo['online'] is True]
        wr_prot = ['', '写保护开启'][driverinfo['wr_prot'] is True]
        status = dr_open + ' ' + online + ' ' + wr_prot
        tmp = {'id': index,
               'cell': [vendorid, productid, status, serialnumber, library]}
        rows.append(tmp)
    paramsQD = request.GET
    perPage = paramsQD.get('rows', '10')  # 设置每页条数
    targPage = paramsQD.get('page', '1')  # 返回第几页
    search_key = request.GET.get('s_key', None)
    paginator = Paginator(object_list=rows, per_page=perPage)
    plansNum = paginator.count
    pagesNum = paginator.num_pages
    getPlans = paginator.page(targPage).object_list
    retInfo = {'r': 0, 'a': 'list', 'page': targPage, 'total': pagesNum, 'records': plansNum, 'rows': getPlans}
    functions.sort_gird_rows(request, retInfo)
    jsonStr = json.dumps(retInfo, ensure_ascii=False)

    return HttpResponse(jsonStr)


def addvolumepool(request):
    params1 = {'action': 'enum_mc_hw_info', 'info': {'fun': 'enum_tape'}}
    driverinfos = json.loads(json.loads(box_service.archiveMediaOperation(json.dumps(params1)))['rev'])
    result = dict()
    type = request.GET.get('type')
    if type == 'add':
        link_obj = EnumLink.objects.all()
        for index, link in enumerate(link_obj):
            for s in driverinfos:
                if link.driver == s['SerialNumber']:
                    result[index] = {'value': link.driver,
                                     'text': '产商：' + s['VendorID'].replace('_', '') + ',产品：' + s['ProductID'].replace(
                                         '_',
                                         '')}
    if type == 'edit':
        driverid = request.GET.get('ids')
        driverid = VolumePool.objects.get(id=driverid).driver
        link = EnumLink.objects.get(driver=driverid)
        for s in driverinfos:
            if link.driver == s['SerialNumber']:
                result[0] = {'value': link.driver,
                             'text': '产商：' + s['VendorID'].replace('_', '') + ',产品：' + s['ProductID'].replace(
                                 '_',
                                 '')}
    jsonStr = json.dumps(result, ensure_ascii=False)
    _logger.info('jsonStrvolumepool:{}'.format(jsonStr))
    return HttpResponse(jsonStr)


def usertapas():
    vpl_obj = VolumePool.objects.all()
    use_tapa = list()
    for obj in vpl_obj:
        tapas = json.loads(obj.tapas)
        for tapa in tapas:
            use_tapa.append(tapas[tapa])
    return use_tapa


def edit_usertapas(ids):
    try:
        vpl_obj = VolumePool.objects.get(id=ids)
    except VolumePool.DoesNotExist:
        return list()
    else:
        use_tapa = list()
        tapas = json.loads(vpl_obj.tapas)
        for tapa in tapas:
            use_tapa.append(tapas[tapa])
        return use_tapa


def volumepoolist(request):
    driverid = request.GET.get('driverid')
    type = request.GET.get('type')
    libraryid = EnumLink.objects.get(driver=driverid).library
    params = {'action': 'enum_mc_hw_info', 'info': {'fun': 'enum_mc'}}
    tapes = json.loads(json.loads(box_service.archiveMediaOperation(json.dumps(params)))['rev'])
    volumepool_list = dict()
    volumepool_list['MCInfo'] = list()
    for tape in tapes:
        if tape['SerialNumber'] == libraryid:
            volumepool_list = tape
            break
    _logger.info('volumepool_list:{}'.format(volumepool_list))
    if type == 'edit':
        ids = request.GET.get('ids')
        use_tapa = edit_usertapas(ids)
    else:
        use_tapa = usertapas()
    for MCInfo in volumepool_list['MCInfo']:
        if MCInfo['VolumeTag'] in use_tapa:
            MCInfo['bind'] = True
    ret = {'r': 0, 'e': '', 'volumepool_list': volumepool_list}
    return HttpResponse(json.dumps(ret, ensure_ascii=False))


def volumepool(request):
    poolname = request.POST.get('poolname')
    cycle = int(request.POST.get('cycle'))
    unit = request.POST.get('unit')
    driver = request.POST.get('driver')  # 驱动器
    volumepool_list = json.loads(request.POST.get('tapes'))
    if unit == 'day':
        unit = 1
    if unit == 'week':
        unit = 2
    if unit == 'month':
        unit = 3
    tape = dict()
    index = 0
    for MCInfo in volumepool_list['MCInfo']:
        if MCInfo.get('newadd'):
            tape[index] = MCInfo['VolumeTag']
            index += 1
    if VolumePool.objects.filter(name=poolname):
        return HttpResponse(json.dumps({'e': '存储卷名称' + poolname + '已经存在', 'r': 1}))
    VolumePool.objects.create(name=poolname, driver=driver, cycle=cycle, cycle_type=unit, tapas=json.dumps(tape),
                              pool_uuid=uuid.uuid4().hex)
    return HttpResponse(json.dumps({'e': '添加成功', 'r': 0}))


def delvolumpool(request):
    delname = request.POST.get('name')
    for name in delname.split(','):
        if name != '':
            vpl_obj = VolumePool.objects.get(name=name)
            vpl_id = vpl_obj.id
            check_related = UserVolumePoolQuota.objects.filter(volume_pool_node_id=vpl_id)
            if check_related:
                return HttpResponse(json.dumps({'e': '存在与待删除存储卷关联的用户，不能删除！', 'r': 1}))
            else:
                vpl_obj.delete()

    return HttpResponse(json.dumps({'e': '删除成功', 'r': 0}))


def adduservolumepoolquota(request):
    query_params = request.GET

    # quota_unit = query_params.get('limitunit', default='')
    caution_unit = query_params.get('waringunit', default='')
    # quota_type = query_params.get('limittype', default='1')
    # limit = float(query_params['limit'])
    node_id = int(query_params.get('quotaid', default='-1'))
    volume_pool_node_id = VolumePool.objects.get(id=node_id).id
    user_id = int(query_params.get('userid', default='-1'))
    if user_id == -1:
        username = query_params.get('username', default='')
        user = User.objects.get(username=username)
        user_id = user.id
    if user_id == -1:
        return HttpResponse('{"r":"1","e":"操作失败,至少选择一个用户"}')
    # 警告不限制，大小为-1
    waring = query_params['waring']
    if waring == '' or int(waring) == 0:
        caution_size = xdata.USER_QUOTA_IS_NOT_WARING_VALUE
    else:
        caution_size = value_convert_to_MB(int(waring), caution_unit)
    if UserVolumePoolQuota.objects.filter(user_id=user_id, volume_pool_node_id=volume_pool_node_id):
        return HttpResponse('{"r":"1","e":"操作失败,该用户已经添加该存储池"}')
    UserVolumePoolQuota.objects.create(caution_size=caution_size, user_id=user_id,
                                       volume_pool_node_id=volume_pool_node_id,
                                       quota_size=xdata.USER_QUOTA_IS_NOT_LIMIT_VALUE, available_size=100)
    return HttpResponse('{"r":"0","e":"操作成功"}')


def editvolumepool(request):
    poolname = request.POST.get('poolname')
    cycle = int(request.POST.get('cycle'))
    unit = request.POST.get('unit')
    driver = request.POST.get('driver')
    obj_id = request.POST.get('obj_id')
    volumepool_dict = json.loads(request.POST.get('tapes'))
    if unit == 'day':
        unit = 1
    if unit == 'week':
        unit = 2
    if unit == 'month':
        unit = 3

    obj = VolumePool.objects.get(id=obj_id)
    tapasinfo = json.loads(obj.tapas)  # old
    index = len(tapasinfo)
    for MCInfo in volumepool_dict['MCInfo']:
        if MCInfo.get('newadd'):
            tapasinfo[index] = MCInfo['VolumeTag']
            index += 1

    tape = json.dumps(tapasinfo)
    check_obj = VolumePool.objects.filter(name=poolname)
    if check_obj and (check_obj.first().id != obj.id):
        return HttpResponse(json.dumps({'e': '存储卷名称' + poolname + '已经存在', 'r': 1}))
    obj.name, obj.driver, obj.cycle, obj.cycle_type, obj.tapas = poolname, driver, cycle, unit, tape
    obj.save()
    return HttpResponse(json.dumps({'e': '添加成功', 'r': 0}))


def deleteuservolumepoolquota(request):
    query_params = request.GET
    quota_ids = query_params.get('ids').split(',')
    for quota_id in quota_ids:
        # 假删
        # user_quotas = UserVolumePoolQuota.objects.get(id=quota_id)
        # user_quotas.deleted = True
        # 真删
        UserVolumePoolQuota.objects.get(id=quota_id).delete()
    # user_quotas.save()
    return HttpResponse('{"r":"0","e":"操作成功"}')


def refresh_link(request):
    ArchiveMediaManager.refresh_link_info()
    return HttpResponse('{"r":"0","e":"操作成功"}')


def add_storage(request):
    data = json.loads(request.POST.get('data'))
    type = data.get('type')
    name = data.get('name')
    ident = data.get('ident')
    path = data.get('path')
    config = data.get('config')
    internal = config.get('is_internal', True)
    available = False

    ret = {'r': 0, 'e': '操作成功'}
    if not request.user.is_superuser:
        ret['r'] = 1
        ret['e'] = 'Permission denied'
        return HttpResponse(json.dumps(ret, ensure_ascii=False))

    org_name = name
    storage_node_obj = StorageNode.objects.filter(name=org_name, deleted=False)
    i = 1
    while storage_node_obj:
        name = '{}({})'.format(org_name, i)
        storage_node_obj = StorageNode.objects.filter(name=name)
        i = i + 1

    storage_node_objs = StorageNode.objects.all()
    if type in ('lvm', 'nfs', 'smb',):
        for storage_node_obj in storage_node_objs:
            if json.loads(storage_node_obj.config).get(type) == config[type]:
                StorageNode.objects.filter(id=storage_node_obj.id).update(available=available,
                                                                          internal=internal, deleted=False)
                return HttpResponse(json.dumps(ret, ensure_ascii=False))
    else:
        ret['r'] = 2
        ret['e'] = 'not support type={}'.format(type)
        return HttpResponse(json.dumps(ret, ensure_ascii=False))

    StorageNode.objects.create(name=name, path=path,
                               ident=ident, config=json.dumps(config, ensure_ascii=False),
                               available=available, internal=internal)

    return HttpResponse(json.dumps(ret, ensure_ascii=False))


def storage_handler(request):
    a = request.GET.get('a', 'none')
    if a == 'none':
        a = request.POST.get('a', 'none')
    if a == 'list':
        return getlist(request)
    if a == 'rename':
        return rename(request)
    if a == 'del':
        return delete(request)
    if a == 'localllist':
        return getlocallist(request)
    if a == 'addlocalstorage':
        return AddLocalStorage(request)
    if a == 'getiqnname':
        return getIQNName(request)
    if a == 'setiqnname':
        return setIQNName(request)
    if a == 'getchap':
        return getCHAP(request)
    if a == 'setchap':
        return setCHAP(request)
    if a == 'clearchap':
        return clearChap(request)
    if a == 'getsan':
        return getSAN(request)
    if a == 'remotelist':
        return getRemotelist(request)
    if a == 'addremotestorage':
        return AddRemoteStorage(request)
    if a == 'connectiscsi':
        return Connectiscsi(request)
    if a == 'getstoragelist':
        return getstoragelist(request)
    if a == 'getquotalist':
        return getquotalist(request)
    if a == 'getuserlist':
        return getuserlist(request)
    if a == 'addquota':
        return addquota(request)
    if a == 'getquotasize':
        return getquotasize(request)
    if a == 'deletequota':
        return deletequota(request)
    if a == 'getquotainfo':
        return getquotainfo(request)
    if a == 'editquota':
        return editquota(request)
    if a == 'getusercount':
        return getusercount(request)
    if a == 'bakoffline':
        return bakoffline(request)
    if a == 'reoffline':
        return reoffline(request)
    if a == 'get_all_nodes_hosts_usage':
        return get_all_nodes_hosts_usage(request)
    if a == 'hosts_storage_detail_easy':
        return get_hosts_storage_detail(request)
    if a == 'clear_storage':
        return clear_storage(request)
    if a == 'tapesinfo':
        return tapesinfo(request)
    if a == 'libraryinfo':
        return libraryinfo(request)
    if a == 'tapesmgrlist':
        return tapesmgrlist(request)
    if a == 'driverinfo':
        return driverinfo(request)
    if a == 'addvolumepool':
        return addvolumepool(request)
    if a == 'volumepoolist':
        return volumepoolist(request)
    if a == 'volumepool':
        return volumepool(request)
    if a == 'delvolumpool':
        return delvolumpool(request)
    if a == 'getstoragepoollist':
        return getstoragepoollist(request)
    if a == 'adduservolumepoolquota':
        return adduservolumepoolquota(request)
    if a == 'getquotavolumelist':
        return getquotavolumelist(request)
    if a == 'editvolumepool':
        return editvolumepool(request)
    if a == 'deleteuservolumepoolquota':
        return deleteuservolumepoolquota(request)
    if a == 'refresh_link_info':
        return refresh_link(request)
    if a == 'add_storage':
        return add_storage(request)
    return HttpResponse(json.dumps({"r": "1", "e": "没有对应的action:{}".format(html.escape(a))}))
