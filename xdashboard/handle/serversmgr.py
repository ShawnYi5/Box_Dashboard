# coding=utf-8
import html
import json
import uuid
from collections import defaultdict
from xdashboard.common.functional import hasFunctional
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.http import HttpResponse
from rest_framework import status
from apiv1.views import BackupTaskSchedules, QuotaManage
from apiv1.models import Host, HostGroup, GroupBackupTaskSchedule, BackupTaskSchedule
from apiv1.views import HostInfo, HostSessionInfo, Hosts
from box_dashboard import functions
from box_dashboard.boxService import box_service, xlogging
from xdashboard.handle.logserver import SaveOperationLog
from xdashboard.models import OperationLog, UserProfile
from xdashboard.request_util import get_operator
import django.utils.timezone as timezone
from apiv1.models import AutoVerifySchedule

all_host_group_name = '默认'

_logger = xlogging.getLogger(__name__)


def filter_hosts(s_key, *args):
    if not s_key:
        return True
    else:
        for arg in args:
            if arg.upper().find(s_key.upper()) != -1:
                return True
        return False


def get_host_info(request, host, group_names):
    ident = host.ident
    servername = host.name if request.GET.get('use_host_name', '0') == '1' else host.display_name
    ip_addresses = '--'
    allocate = '未分配'
    serverlist = list()
    system_infos = json.loads(host.ext_info).get('system_infos')
    host_type = host.type
    if host.is_nas_host:
        real_host_type = Host.NAS_AGENT
    else:
        real_host_type = host.type
    if system_infos and 'Nic' in system_infos:
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
        serverlist.append(host.user.username)
    except Exception as e:
        pass
    if len(serverlist) > 0:
        allocate = '<br />'.join(serverlist)
    if host.login_datetime and host.is_linked:
        lasttime = host.login_datetime.strftime('%Y-%m-%d %H:%M:%S')
    else:
        lasttime = '--'
    online = '在线'
    isencipher = '不加密'
    if host.network_transmission_type == 1:
        isencipher = '加密'
    if not host.is_linked:
        online = '离线'
    if host.is_remote:
        online = '远程主机'
        lasttime = '--'

    vm_session_id = -1
    if host_type == Host.PROXY_AGENT:
        try:
            online = '{}/{}'.format('启用' if host.vm_session.enable else '禁用', online)
            vm_session_id = host.vm_session.id
            ip_addresses = host.vm_session.connection.address
        except Exception as e:
            online = '{}/{}'.format('已删除', online)
            pass

    if len(group_names):
        groups = '<br />'.join(group_names)
    else:
        groups = '未分组'

    if not request.user.is_superuser:
        return [ident, servername, host_type, ip_addresses, isencipher, online, lasttime, vm_session_id, real_host_type,
                groups]

    log_btn = '''<span onclick="getDebugLogZip('{0}')" style="color:blue;cursor:pointer">[获取]</span>'''.format(ident)
    return [ident, servername, host_type, ip_addresses, isencipher, online, allocate, lasttime, log_btn, vm_session_id,
            real_host_type, groups]


def getclientlist(request):
    # 默认值
    page = 1
    rows = 30
    if 'page' in request.GET:
        page = int(request.GET['page'])
    if 'rows' in request.GET:
        rows = int(request.GET['rows'])
    sidx = request.GET.get('sidx', None)
    host_ids = request.GET.get('ids', None)
    sord = request.GET.get('sord', 'asc')
    if sidx not in ('display_name', 'last_ip', 'network_transmission_type', 'user',):
        sidx = None
    if sidx and sord == 'desc':
        sidx = '-{}'.format(sidx)

    host_id = None
    user_id = None
    group_id = request.GET.get('group_id')
    search_key = request.GET.get('s_key', '')
    search_is_use_name = request.GET.get('user_name', '')
    search_is_online = request.GET.get('online', '')  # 'online', 'no-online'
    search_is_encrypt = request.GET.get('encrypt', '')  # 'encrypt', 'no-encrypt'
    search_online_time = request.GET.get('online_time', '')

    if 'hostid' in request.GET and request.GET['hostid'] != 'null':
        host_id = request.GET['hostid']

    if 'usedid' in request.GET and request.GET['usedid'] != 'null':
        user_id = request.GET['usedid']

    hosts = Host.objects.select_related('user', 'vm_session', 'vm_session__connection').all()

    if request.user.is_superuser:
        if host_id:  # 查询指定的host
            hosts = hosts.filter(id=host_id)
        elif user_id:  # 从属于user的host
            hosts = hosts.filter(user_id=user_id)
        else:  # 所有的host
            pass
    else:
        hosts = hosts.filter(user=request.user)
        if group_id:
            if group_id == '-1':
                # 未分组的客户端
                hosts = hosts.filter(groups=None)
            else:
                hosts = hosts.filter(groups__id=group_id)
        else:
            pass
    if host_ids:
        hosts = hosts.filter(id__in=host_ids.split(','))
    if sidx is not None:
        hosts = hosts.order_by(sidx)
    # 过滤掉 验证的主机
    hosts = list(filter(lambda x: not x.is_verified, hosts))

    # 过滤掉 被删除的主机
    hosts = list(filter(lambda x: not x.is_deleted, hosts))

    rowList = list()
    ident2groups = defaultdict(set)
    for group in HostGroup.objects.all():
        for h in group.hosts.all():
            ident2groups[h.ident].add(group.name)
    for host in hosts:
        cell_info = get_host_info(request, host, ident2groups.get(host.ident, list()))

        kwd_status = filter_hosts(search_key, cell_info[1], cell_info[3])  # 1.关键词

        if search_is_use_name:  # 2.所属用户
            username = host.user.username if host.user else '未分配'
            use_status = search_is_use_name in username
        else:
            use_status = True

        if search_is_online:  # 3.在线状态
            if search_is_online == 'online':
                online_status = '在线' in cell_info[5]
            elif search_is_online == 'remote-host':
                online_status = '远程主机' in cell_info[5]
            else:
                online_status = '离线' in cell_info[5]
        else:
            online_status = True

        if search_is_encrypt:  # 4.加密与否
            if search_is_encrypt == 'encrypt':
                encrypt_status = '加密' == cell_info[4]
            else:
                encrypt_status = '不加密' == cell_info[4]
        else:
            encrypt_status = True

        if search_online_time:  # 5.在线时间
            if request.user.is_superuser:
                time_status = search_online_time in cell_info[6]
            else:
                time_status = search_online_time in cell_info[7]
        else:
            time_status = True

        if all([kwd_status, use_status, online_status, encrypt_status, time_status]):
            detailDict = {'id': host.id, 'cell': cell_info}
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

    functions.sort_gird_rows(request, retInfo)
    jsonStr = json.dumps(retInfo, ensure_ascii=False)

    return HttpResponse(jsonStr)


class Obj(object):
    pass


def getServerInfo(request, api_params=None):
    if api_params:
        ident = api_params['ident']
    else:
        ident = request.GET.get('ident', None)
    itemObject = Host.objects.filter(ident=ident)[0]
    login_datetime = itemObject.login_datetime
    if login_datetime and itemObject.is_linked:
        lasttime = login_datetime.strftime('%Y-%m-%dT%H:%M:%S')
    else:
        if itemObject.logs.exists():
            lasttime = itemObject.logs.order_by('-datetime')[0].datetime.strftime('%Y-%m-%dT%H:%M:%S')
        else:
            lasttime = '--'
    if ident and itemObject:
        resp = HostSessionInfo().get(request, ident)
        if resp.status_code == status.HTTP_404_NOT_FOUND:
            return HttpResponse('{"r":"1","e":"未获取到客户端信息"}')
        else:
            data = resp.data
            data['lasttime'] = lasttime
            data['ip'] = ''
            for ip_add in data['ether_adapters']:
                if data['ip'] != '':
                    data['ip'] += '|'
                data['ip'] += ','.join(ip_add['ip_addresses'])
            data['r'] = '0'

            _ext_info = json.loads(itemObject.ext_info)
            data['agent_version'] = _ext_info['system_infos']['System'].get('version', 'unknown')
            return HttpResponse(json.dumps(data))
    else:
        return HttpResponse('{"r":"1","e":"不存在此客户端"}')


def renameServer(request):
    ident = request.POST.get('id', 'none')
    display_name = request.POST.get('name', 'none')
    encipher = request.POST.get('encipher', '2')
    orgname = request.POST.get('orgname')
    systemname = request.POST.get('systemname')
    if display_name == 'none':
        return HttpResponse('{"r":"1","e":"名称不能为空"}')
    data = {"display_name": display_name, "network_transmission_type": encipher}
    data['orgname'] = orgname
    data['systemname'] = systemname
    myrequest = Obj()
    myrequest.__setattr__("data", data)
    hostinfoObj = HostInfo()
    oldname = hostinfoObj.get(request, ident).data['name']
    oldencipher = hostinfoObj.get(request, ident).data['network_transmission_type']
    desc = {'操作': '编辑客户端', '内容': {'旧名称': oldname, '新名称': display_name}}
    r = 0
    if int(oldencipher) != int(encipher):
        desc['数据传输方式'] = '未加密' if int(encipher) == 2 else '加密'
        breakoffclient(ident)
    resp = hostinfoObj.put(myrequest, ident)
    if resp.status_code == status.HTTP_202_ACCEPTED:
        SaveOperationLog(
            request.user, OperationLog.TYPE_SERVER, json.dumps(desc, ensure_ascii=False), get_operator(request))
        return HttpResponse(json.dumps({"r": r, "e": "操作成功", "id": ident}, ensure_ascii=False))
    return HttpResponse('{"r":"1","e":"操作失败"}')


def getUsersHosts(request):
    ret_info = {
        'users': [{'name': obj.username, 'id': obj.id} for obj in
                  User.objects.filter(is_superuser=False, is_active=True,
                                      userprofile__user_type__exact=UserProfile.NORMAL_USER)],
        'hosts': [{'name': obj.display_name, 'id': obj.id} for obj in Host.objects.all()]
    }
    return HttpResponse(json.dumps(ret_info))


def breakoffclient(ident):
    box_service.forceOfflineAgent(ident)


def _remove_all_verify_task(ident):
    host = Host.objects.get(ident=ident)
    tasks = AutoVerifySchedule.objects.all()
    for task in tasks:
        task.hosts.remove(host)


def delserver(request):
    idents = request.POST.get('idents', '')
    ret_info = {"e": [], "r": 1}
    success_host_names = list()
    for ident in idents.split(","):
        rsp = Hosts().delete(request, ident)
        if status.is_success(rsp.status_code):
            remove_all_group(ident, '1')
            _remove_all_verify_task(ident)
            ret_info["e"].append(rsp.data['msg'])
            success_host_names.append(rsp.data['name'])
        else:
            ret_info["e"].append(rsp.data if rsp.data else '删除失败，内部异常')
        ret_info["code_is"] = rsp.status_code
    ret_info['e'].sort(key=lambda x: '删除成功' in x, reverse=True)
    ret_info["e"] = '* {}'.format('\n* '.join(ret_info["e"]))
    if success_host_names:
        desc = {'操作': '删除客户端', '客户端': '<br>'.join(success_host_names)}
        SaveOperationLog(
            request.user, OperationLog.TYPE_SERVER, json.dumps(desc, ensure_ascii=False), get_operator(request))
    return HttpResponse(json.dumps(ret_info))


def add_group(request):
    name = request.POST.get('name', '')
    host_ids = request.POST.get('ids')
    ret = {'r': 0}
    if name == '':
        ret['r'] = 1
        ret['e'] = '组名不能为空'
        return HttpResponse(json.dumps(ret))
    if HostGroup.objects.filter(name=name, user_id=request.user.id):
        ret['r'] = 1
        ret['e'] = '组名{}已存在'.format(name)
        return HttpResponse(json.dumps(ret))
    host_group = HostGroup.objects.create(name=name, user_id=request.user.id)

    if host_ids:
        for host_id in host_ids.split(','):
            host_group.hosts.add(Host.objects.get(id=host_id))

    return HttpResponse(json.dumps(ret))


def remove_all_group(host_idents, add_group_disable_plan):
    for host_id in host_idents.split(','):
        host = Host.objects.get(ident=host_id)
        for host_group in host.groups.all():
            host_group.hosts.remove(host)
            gs_list = GroupBackupTaskSchedule.objects.filter(host_group=host_group)
            for gs in gs_list:
                schedules = gs.schedules.filter(host=host)
                if schedules and add_group_disable_plan == '1':
                    # 禁用退出分组的客户端计划任务
                    schedules.update(enabled=False)
                for schedule in schedules:
                    gs.schedules.remove(schedule)
                if gs.schedules.count() == 0:
                    GroupBackupTaskSchedule.objects.filter(id=gs.id).delete()


def movetogroup(request):
    group_id = request.POST.get('group_id', '')
    host_ids = request.POST.get('host_ids')
    remove_org_group = request.POST.get('remove_org_group', '0')
    add_group_disable_plan = request.POST.get('add_group_disable_plan', '0')
    exit_group = request.POST.getlist('exit_group', default=[])
    exit_group = list(map(int, exit_group))
    ret = {'r': 0}

    bremove_all_org_group = False
    if remove_org_group == '1' or group_id == '-1':
        # 退出原来的所有分组
        bremove_all_org_group = True

    if len(exit_group):
        bremove_all_org_group = False

    if len(exit_group):
        # 退出选择的分组
        for host_id in host_ids.split(','):
            host = Host.objects.get(ident=host_id)
            for exit_group_id in exit_group:
                host_group = HostGroup.objects.get(id=exit_group_id)
                host_group.hosts.remove(host)
                gs_list = GroupBackupTaskSchedule.objects.filter(host_group=host_group)
                for gs in gs_list:
                    schedules = gs.schedules.filter(host=host)
                    if schedules and add_group_disable_plan == '1':
                        # 禁用退出分组的客户端计划任务
                        schedules.update(enabled=False)
                    for schedule in schedules:
                        gs.schedules.remove(schedule)
                    if gs.schedules.count() == 0:
                        GroupBackupTaskSchedule.objects.filter(id=gs.id).delete()

    # 退出原来的所有分组
    if bremove_all_org_group:
        remove_all_group(host_ids, add_group_disable_plan)

    # 加入分组
    if group_id != '-1':
        host_group = HostGroup.objects.filter(id=group_id).first()
        for host_id in host_ids.split(','):
            host = Host.objects.get(ident=host_id)
            host_group.hosts.add(host)
            gs_list = GroupBackupTaskSchedule.objects.filter(host_group=host_group)
            # 创建备份计划
            if gs_list:
                gs = gs_list.first()
                schedule = gs.schedules.first()
                hostId = host.id
                planName = '{}（{}）'.format(gs.name, uuid.uuid4().hex)
                params = {"host": hostId, "name": planName, "storage_device_type": 1,
                          "cycle_type": schedule.cycle_type, "plan_start_date": schedule.plan_start_date,
                          "backup_source_type": schedule.backup_source_type, "ext_config": schedule.ext_config,
                          'storage_node_ident': schedule.storage_node_ident, 'enabled': schedule.enabled}
                respn = BackupTaskSchedules().post(request=request, api_request=params)
                plan_id = respn.data['id']
                gs.schedules.add(BackupTaskSchedule.objects.get(id=plan_id))

    return HttpResponse(json.dumps(ret))


def getgrouplist(request):
    # 默认值
    page = 1
    rows = 30
    if 'page' in request.GET:
        page = int(request.GET['page'])
    if 'rows' in request.GET:
        rows = int(request.GET['rows'])
    HostGroups = HostGroup.objects.filter(user_id=request.user.id)
    paginator = Paginator(HostGroups, rows)
    totalPlan = paginator.count
    totalPage = paginator.num_pages

    page = totalPage if page > totalPage else page
    currentObjs = paginator.page(page).object_list
    rowList = list()
    for Obj in currentObjs:
        detailDict = {'id': Obj.id, 'cell': [Obj.id, Obj.name]}
        rowList.append(detailDict)

    retInfo = {'r': 0, 'a': 'list', 'page': str(page), 'total': totalPage,
               'records': totalPlan, 'rows': rowList}

    jsonStr = json.dumps(retInfo, ensure_ascii=False)

    return HttpResponse(jsonStr)


def renamegroup(request):
    name = request.POST.get('name', '')
    id = request.POST.get('id', '')
    ret = {'r': 0}
    if name == '':
        ret['r'] = 1
        ret['e'] = '组名不能为空'
        return HttpResponse(json.dumps(ret))
    if HostGroup.objects.filter(name=name, user_id=request.user.id):
        ret['r'] = 1
        ret['e'] = '组名{}已存在'.format(name)
        return HttpResponse(json.dumps(ret))
    HostGroup.objects.filter(id=id).update(name=name)
    return HttpResponse(json.dumps(ret))


def delgroup(request):
    id = request.POST.get('id', '')
    host_group = HostGroup.objects.filter(id=id)
    if host_group.first().name == all_host_group_name:
        return HttpResponse(json.dumps({'r': 1, 'e': '不能删除默认组'}))
    if host_group.first().hosts.count():
        return HttpResponse(json.dumps({'r': 2, 'e': '请先移除该分组中的客户端'}))
    host_group.delete()
    return HttpResponse(json.dumps({'r': 0, 'e': ''}))


def getallgrouplist(request):
    HostGroups = HostGroup.objects.filter(user_id=request.user.id).order_by('name')
    ident = request.GET.get('ident')
    id = request.GET.get('id', 'root')
    if id == '':
        id = 'root'

    if id == 'root':
        ret_info = list()
        ret_info.append(
            {'id': 0, "label": '所有客户端', "branch": [], 'icon': 'pcroot', "inode": False, "open": False,
             "checked": True})
        ret_info.append({'id': -1, "label": '未分组的客户端', 'icon': 'nogroup', "branch": [], "inode": False, "open": False})
        for host_group in HostGroups:
            if ident and host_group.hosts.count():
                if host_group.hosts.first().type != Host.objects.get(ident=ident).type:
                    continue
            ret_info.append({'id': host_group.id, 'icon': 'group', "label": host_group.name,
                             "branch": [], "inode": False, "open": False})
        return HttpResponse(json.dumps(ret_info, ensure_ascii=False))


def gethostgrouplist(request):
    host_id = request.GET.get('id')
    host = Host.objects.get(ident=host_id)
    group_list = list()
    for group in host.groups.all():
        group_list.append({'id': group.id, 'name': group.name})

    return HttpResponse(json.dumps({'list': group_list}, ensure_ascii=False))


def _get_storage_node_ident(user_id):
    from apiv1.views import StorageNodes
    api_response = StorageNodes().get(None)
    if status.is_success(api_response.status_code):
        for element in api_response.data:
            if not element["linked"]:
                continue
            resp = QuotaManage().get(request=None, api_request={'node_id': element['id']})
            if resp.status_code == status.HTTP_200_OK:
                for data in resp.data:
                    if str(data['user_id']) == str(user_id):
                        return element["ident"]
    return None


def ON_createNewHost(user_id, host_ident):
    if not hasFunctional('clw_desktop_aio'):
        return
    storage_node_ident = _get_storage_node_ident(user_id)
    if storage_node_ident is None:
        # 没有存储节点
        return

    host_group = HostGroup.objects.filter(name=all_host_group_name, user_id=user_id)
    if host_group:
        host_group = host_group.first()
    else:
        host_group = HostGroup.objects.create(name=all_host_group_name, user_id=user_id)
    host = Host.objects.get(ident=host_ident)
    host_group.hosts.add(host)

    gs_list = GroupBackupTaskSchedule.objects.filter(host_group=host_group)
    # 创建备份计划
    if gs_list:
        gs = gs_list.first()
        schedule = gs.schedules.first()
        hostId = host.id
        planName = '{}（{}）'.format(gs.name, uuid.uuid4().hex)
        params = {"host": hostId, "name": planName, "storage_device_type": 1,
                  "cycle_type": schedule.cycle_type, "plan_start_date": schedule.plan_start_date,
                  "backup_source_type": schedule.backup_source_type, "ext_config": schedule.ext_config,
                  'storage_node_ident': schedule.storage_node_ident, 'enabled': schedule.enabled}
        respn = BackupTaskSchedules().post(request=None, api_request=params)
        plan_id = respn.data['id']
        gs.schedules.add(BackupTaskSchedule.objects.get(id=plan_id))
    else:
        gs = GroupBackupTaskSchedule.objects.create(name="自动创建的备份计划",
                                                    user_id=user_id,
                                                    host_group_id=host_group.id,
                                                    type=GroupBackupTaskSchedule.SCHEDULE_TYPE_BACKUP_TASK)
        hostId = host.id
        planName = '{}（{}）'.format(gs.name, uuid.uuid4().hex)
        ext_config = r'{"backupDataHoldDays": 30, "backupLeastNumber": 5, "autoCleanDataWhenlt": 200, "cdpDataHoldDays": 7, "maxBroadband": 300, "cdpSynchAsynch": 1, "backupDayInterval": 86400, "daysInWeek": [], "daysInMonth": [], "removeDuplicatesInSystemFolder": true, "incMode": 2, "exclude": [], "IntervalUnit": "day", "data_keeps_deadline_unit": "month", "diskreadthreadcount": 4, "backup_retry": {"enable": true, "count": 5, "interval": 10}, "BackupIOPercentage": 30, "nas_protocol": null, "nas_username": null, "nas_password": null, "nas_exclude_dir": null, "nas_path": null, "enum_threads": -1, "sync_threads": -1, "cores": -1, "memory_mbytes": -1, "net_limit": -1, "enum_level": -1, "sync_queue_maxsize": -1, "nas_max_space_val": -1, "nas_max_space_unit": "", "nas_max_space_actual": -1}'
        params = {"host": hostId, "name": planName, "storage_device_type": 1,
                  "cycle_type": BackupTaskSchedule.CYCLE_PERDAY, "plan_start_date": timezone.now(),
                  "backup_source_type": BackupTaskSchedule.BACKUP_DISKS, "ext_config": ext_config,
                  'storage_node_ident': storage_node_ident, 'enabled': True}
        respn = BackupTaskSchedules().post(request=None, api_request=params)
        plan_id = respn.data['id']
        gs.schedules.add(BackupTaskSchedule.objects.get(id=plan_id))


def serversmgr_handler(request):
    a = request.GET.get('a', 'none')
    if a == 'none':
        a = request.POST.get('a', 'none')
    if a == 'getclientlist':
        return getclientlist(request)
    if a == 'renameserver':
        return renameServer(request)
    if a == 'getServerInfo':
        return getServerInfo(request)
    if a == 'getusershosts':
        return getUsersHosts(request)
    if a == 'delserver':
        return delserver(request)
    if a == 'add_group':
        return add_group(request)
    if a == 'getgrouplist':
        return getgrouplist(request)
    if a == 'renamegroup':
        return renamegroup(request)
    if a == 'delgroup':
        return delgroup(request)
    if a == 'getallgrouplist':
        return getallgrouplist(request)
    if a == 'movetogroup':
        return movetogroup(request)
    if a == 'gethostgrouplist':
        return gethostgrouplist(request)
    return HttpResponse(json.dumps({"r": "1", "e": "没有对应的action:{}".format(html.escape(a))}))
