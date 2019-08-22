import json, os, re, time, random, socket, hashlib, psutil, subprocess, traceback, signal
import uuid
from box_dashboard import xlogging, functions, xdata, xdatetime
from django.http import HttpResponse
from rest_framework import status
from django.core.paginator import Paginator
from apiv1.models import TakeOverKVM, HostSnapshot, StorageNode, Host, BackupTaskSchedule, DiskSnapshot
from apiv1.takeover_logic import TakeOverKVMCreate, TakeOverKVMExecute
from apiv1.views import get_response_error_string
from box_dashboard.boxService import box_service
from xdashboard.common.license import check_license, get_functional_int_value, check_evaluation_and_expiration
from xdashboard.handle.authorize.authorize_init import get_takeover_count, get_temporary_takeover_count
from xdashboard.handle.backup import get_host_name
from xdashboard.handle.logserver import SaveOperationLog
from xdashboard.models import OperationLog
from django.db.models import Q
from xdashboard.request_util import get_operator
from xdashboard.common.functional import hasFunctional
import django.utils.timezone as timezone
from xdashboard.models import audit_task
from xdashboard.common.msg import notify_audits
from xdashboard.handle.user import have_audit_admin
from apiv1.cdp_wrapper import fix_restore_time

_logger = xlogging.getLogger(__name__)
router = functions.Router(globals())
external_pe_file = '/dev/shm/external_pe_file.json'


def _get_kvm_info(flag_file):
    info = None
    try:
        with open(flag_file, 'r') as fout:
            try:
                info = json.loads(fout.read())
                if info.get('msg', None) is None:
                    info['msg'] = '已发送开机命令'
            except Exception as e:
                _logger.info('_start_kvm read Failed.e={}'.format(e))
    except Exception as e:
        pass
    return info


def get_kvm_run_info(request):
    id = request.GET.get('id', '-1')
    debug = request.GET.get('debug', '0')
    result = {'r': 0, "e": "操作成功"}
    api_request = {"id": id, "debug": debug}
    api_response = TakeOverKVMCreate().get(request=request, api_request=api_request)
    if not status.is_success(api_response.status_code):
        e = get_response_error_string(api_response)
        debug = "TakeOverKVMExecute().post() failed {}".format(api_response.status_code)
        return HttpResponse(json.dumps({"r": 2, "e": e, "debug": debug}, ensure_ascii=False))

    info = None
    for obj in api_response.data:
        info = _get_kvm_info(obj['kvm_flag_file'])
        ext_info = json.loads(obj['ext_info'])
        if info and ext_info:
            info['kvm_pwd'] = ext_info.get('kvm_pwd', None)
            info['kvm_name'] = obj['name']
    if os.path.isfile(external_pe_file):
        result['check_external_pe_file'] = 1
    else:
        result['check_external_pe_file'] = 0
    result['info'] = info

    return HttpResponse(json.dumps(result, ensure_ascii=False))


def _gznbd_is_run(device_path_list):
    p = psutil.process_iter()
    for r in p:
        if r.name().strip().lower() in ('gznbd',):
            for line in r.cmdline():
                for device_path in device_path_list:
                    if device_path in line:
                        return True
    return False


def takeover_kvm_is_run(device_path_list):
    p = psutil.process_iter()
    for r in p:
        if r.name().strip().lower() in ('qemu-kvm', 'qemu-system-x86_64',):
            for line in r.cmdline():
                for device_path in device_path_list:
                    if device_path in line:
                        return True
    return False


def _save_kvm_run_info(start_kvm_flag_file, kvm_key, kvm_value):
    flag_file = start_kvm_flag_file
    try:
        with open(flag_file, 'w') as fout:
            info = dict()
            info[kvm_key] = kvm_value
            fout.seek(0)
            fout.truncate()
            info_str = json.dumps(info, ensure_ascii=False)
            fout.write(info_str)
    except Exception as e:
        _logger.info('_save_kvm_run_info r Failed.e={}'.format(e))


def _remove_start_kvm_flag_file(start_kvm_flag_file):
    if os.path.isfile(start_kvm_flag_file):
        os.remove(start_kvm_flag_file)


def _start_kvm(id, user, operator, kvm_debug):
    TakeOverKVMobj = TakeOverKVM.objects.get(id=id)
    flag_file = TakeOverKVMobj.kvm_flag_file
    kvm_name = TakeOverKVMobj.name
    if os.path.isfile(flag_file):
        result = {"r": 0, "e": "虚拟机已经在运行", "id": id, "kvm_name": kvm_name}
        return result
    _save_kvm_run_info(flag_file, 'msg', '已发送开机命令')
    ext_info = json.loads(TakeOverKVMobj.ext_info)

    clret = check_evaluation_and_expiration()
    if clret.get('r', 0) != 0:
        _remove_start_kvm_flag_file(flag_file)
        return clret

    device_path_list = list()
    for boot_device in ext_info['disk_snapshots']['boot_devices']:
        device_path_list.append(boot_device['device_profile']['nbd']['device_path'])

    for data_device in ext_info['disk_snapshots']['data_devices']:
        device_path_list.append(data_device['device_profile']['nbd']['device_path'])

    if _gznbd_is_run(device_path_list):
        result = {"r": 2, "e": "该系统正在关机中，暂时无法执行“启动”操作，请稍候再试。", "id": id, "device_path_list": device_path_list,
                  "kvm_name": kvm_name}
        _remove_start_kvm_flag_file(flag_file)
        return result

    qcow2path_path_list = list()
    for boot_device in ext_info['disk_snapshots']['boot_devices']:
        qcow2path_path_list.append(boot_device['device_profile']['qcow2path'])

    if takeover_kvm_is_run(qcow2path_path_list):
        result = {"r": 3, "e": "虚拟机正在关机中，暂时无法执行“启动”操作，请稍候再试。", "id": id, "qcow2path_path_list": qcow2path_path_list,
                  "kvm_name": kvm_name}
        _remove_start_kvm_flag_file(flag_file)
        return result

    kvm_memory_size = TakeOverKVMobj.kvm_memory_size
    kvm_memory_unit = TakeOverKVMobj.kvm_memory_unit
    if kvm_memory_unit == 'GB':
        kvm_memory_size_MB = kvm_memory_size * 1024
    elif kvm_memory_unit == 'MB':
        kvm_memory_size_MB = kvm_memory_size
    else:
        _logger.error('_start_kvm kvm_memory_unit Failed.kvm_memory_unit={}'.format(kvm_memory_unit))
        kvm_memory_size_MB = 0

    jsonstr = box_service.queryTakeOverHostInfo('none')
    if jsonstr:
        jsonobj = json.loads(jsonstr)
        if jsonobj['total_memory_mb_for_takeover'] - jsonobj['used_memory_mb'] - kvm_memory_size_MB < 0:
            result = {"r": 1, "e": "内存不足。总共：{}MB,可用：{}MB，当前配置：{}MB。".format(jsonobj['total_memory_mb_for_takeover'],
                                                                            jsonobj['total_memory_mb_for_takeover'] -
                                                                            jsonobj['used_memory_mb'],
                                                                            kvm_memory_size_MB), "id": id,
                      "kvm_name": kvm_name}
            desc = {'操作': '启动接管主机', '名称': kvm_name, '结果': '失败，内存不足'}
            SaveOperationLog(user, OperationLog.TYPE_TAKEOVER, json.dumps(desc, ensure_ascii=False), operator)
            _remove_start_kvm_flag_file(flag_file)
            return result

        kvm_cpu_count_str = str(TakeOverKVMobj.kvm_cpu_count)
        if len(kvm_cpu_count_str) == 1:
            kvm_cpu_sockets = 1
            kvm_cpu_cores = int(kvm_cpu_count_str)
        else:
            kvm_cpu_sockets = int(kvm_cpu_count_str[0])
            kvm_cpu_cores = int(kvm_cpu_count_str[1])
        kvm_cpu_count = kvm_cpu_sockets * kvm_cpu_cores

        if kvm_cpu_count > (jsonobj['total_cpu_number_for_takover'] - jsonobj['used_cpu_number']):
            result = {"r": 1, "e": "CPU核心数超过最大限制。总共：{}核,可用{}核，当前配置：{}核。".format(jsonobj['total_cpu_number_for_takover'],
                                                                                jsonobj[
                                                                                    'total_cpu_number_for_takover'] -
                                                                                jsonobj['used_cpu_number'],
                                                                                kvm_cpu_count), "id": id,
                      "kvm_name": kvm_name}
            desc = {'操作': '启动接管主机', '名称': kvm_name, '结果': '失败，CPU核心数过多'}
            SaveOperationLog(user, OperationLog.TYPE_TAKEOVER, json.dumps(desc, ensure_ascii=False),
                             operator)
            _remove_start_kvm_flag_file(flag_file)
            return result

    api_request = {"id": id, "debug": kvm_debug}
    api_response = TakeOverKVMExecute().post(request=None, api_request=api_request)
    if not status.is_success(api_response.status_code):
        e = get_response_error_string(api_response)
        debug = "TakeOverKVMExecute().post() id={} failed {}".format(id, api_response.status_code)
        _logger.error(debug)
        desc = {'操作': '启动接管主机', '名称': kvm_name, '结果': e}
        SaveOperationLog(user, OperationLog.TYPE_TAKEOVER, json.dumps(desc, ensure_ascii=False), operator)
        _remove_start_kvm_flag_file(flag_file)
        return HttpResponse(json.dumps({"r": 2, "e": e, "id": id, "debug": debug}, ensure_ascii=False))

    result = {"r": 0, "e": "操作成功", "id": id, "kvm_name": kvm_name}
    desc = {'操作': '启动接管主机', '名称': kvm_name, '结果': '操作成功'}
    SaveOperationLog(user, OperationLog.TYPE_TAKEOVER, json.dumps(desc, ensure_ascii=False), operator)

    return result


def _get_kvm_mac(nid=None):
    kvm_mac = list()
    if nid:
        TakeOverKVMobj = TakeOverKVM.objects.filter(~Q(id='{}'.format(nid)))
    else:
        TakeOverKVMobj = TakeOverKVM.objects.all()
    for kvm in TakeOverKVMobj:
        ext_info = json.loads(kvm.ext_info)
        kvm_adpter = ext_info['kvm_adpter']
        for adpter in kvm_adpter:
            kvm_mac.append(adpter['mac'])
    return kvm_mac


def _have_same_kvm_name(request, kvm_name, nid=None):
    if nid:
        TakeOverKVMobj = TakeOverKVM.objects.filter(~Q(id='{}'.format(nid))).filter(
            host_snapshot__host__user_id=request.user.id)
    else:
        TakeOverKVMobj = TakeOverKVM.objects.filter(host_snapshot__host__user_id=request.user.id)
    for kvm in TakeOverKVMobj:
        if kvm_name == kvm.name:
            return True
    return False


def _check_takeover_license(request):
    clret = check_license('takeover')
    if clret.get('r', 0) != 0:
        return clret
    count = get_functional_int_value('takeover')
    takeover_count = get_takeover_count()
    if takeover_count >= count:
        return {'r': 2, 'e': '当前授权数量{}，已创建计划数量{}，请增加授权数量或删除一些接管主机。'.format(count, takeover_count)}
    return {'r': 0, 'e': 'OK'}


def _check_temporary_takeover_license(request):
    clret = check_license('temporary_takeover')
    if clret.get('r', 0) != 0:
        return clret
    count = get_functional_int_value('temporary_takeover')
    takeover_count = get_temporary_takeover_count()
    if takeover_count >= count:
        return {'r': 2, 'e': '当前授权数量{}，已创建计划数量{}，请增加授权数量或删除一些验证主机。'.format(count, takeover_count)}
    return {'r': 0, 'e': 'OK'}


def check_disk_number(request):
    pointid = request.POST.get('pointid', None).split('|')
    hddctl = request.POST.get('hddctl', None)
    kvm_type = request.POST.get('kvm_type')
    host_snapshot_id = pointid[1]
    disk_count = DiskSnapshot.objects.filter(host_snapshot_id=host_snapshot_id).count()
    if kvm_type == 'forever_kvm':  # 当控制器是IDE时，接管最多允许3块硬盘，验证最多允许4块硬盘
        max_disk = 3
    else:
        max_disk = 4
    if hddctl.upper() == 'IDE' and disk_count > max_disk:
        return HttpResponse(
            json.dumps({"r": 99, "e": 'IDE控制器，磁盘个数大于{}个，有硬盘不能加载，你确定要继续吗？'.format(max_disk)}, ensure_ascii=False))
    return HttpResponse(json.dumps({"r": 0, "e": '操作成功'}, ensure_ascii=False))


def _add_to_audit_task_queue(host_name, user, task_info):
    create_datetime = timezone.now()
    audit_task.objects.create(create_user=user, status=audit_task.AUIDT_TASK_STATUS_WAITE,
                              create_datetime=create_datetime, task_info=json.dumps(task_info, ensure_ascii=False))
    notify_audits(user, host_name, create_datetime.strftime('%Y-%m-%d %H:%M:%S'), task_info)

    desc = {'操作': '执行快速验证', '名称': task_info['api_request']['name'], '任务状态': '等待审批'}
    SaveOperationLog(user, OperationLog.TYPE_RESTORE, json.dumps(desc, ensure_ascii=False), task_info['operator'])

    return {"r": 0, "e": '已提交快速验证审批任务，请在<a style="color:blue;" href="../home">任务执行状态</a>中查看任务执行情况。', 'audit': 'audit'}


def create_kvm(request):
    run = int(request.POST.get('run', '1'))
    pointid = request.POST.get('pointid', None)
    kvm_name = request.POST.get('kvm_name', None)
    kvm_cpu_sockets = request.POST.get('kvm_cpu_sockets', 0)
    kvm_cpu_cores = request.POST.get('kvm_cpu_cores', 0)
    kvm_memory_size = request.POST.get('kvm_memory_size', None)
    kvm_memory_unit = request.POST.get('kvm_memory_unit', None)
    kvm_storagedevice = request.POST.get('kvm_storagedevice', None)
    kvm_adpter = json.loads(request.POST.get('kvm_adpter', '[]'))
    kvm_route = json.loads(request.POST.get('kvm_route', '[]'))
    kvm_gateway = json.loads(request.POST.get('kvm_gateway', '[]'))
    kvm_dns = json.loads(request.POST.get('kvm_dns', '[]'))
    kvm_type = request.POST.get('kvm_type', None)
    snapshot_time = request.POST.get('snapshot_time', None)
    enable_kvm_pwd = request.POST.get('enable_kvm_pwd', '1')
    kvm_pwd = request.POST.get('kvm_pwd', ''.join(random.sample('0123456789', 6)))
    hddctl = request.POST.get('hddctl', None)
    vga = request.POST.get('vga', None)
    net = request.POST.get('net', None)
    cpu = request.POST.get('cpu', None)
    boot_firmware = request.POST.get('boot_firmware', None)

    if kvm_type == 'forever_kvm':
        clret = _check_takeover_license(request)
        if clret.get('r', 0) != 0:
            return HttpResponse(json.dumps(clret, ensure_ascii=False))
    else:
        clret = _check_temporary_takeover_license(request)
        if clret.get('r', 0) != 0:
            return HttpResponse(json.dumps(clret, ensure_ascii=False))

    if enable_kvm_pwd == '0':
        kvm_pwd = None
    elif len(kvm_pwd) != 6:
        kvm_pwd = None

    if _have_same_kvm_name(request, kvm_name):
        result = {"r": 1, "e": "名称【{}】，已存在".format(kvm_name)}
        return HttpResponse(json.dumps(result, ensure_ascii=False))

    kvm_cpu_count = int('{}{}'.format(kvm_cpu_sockets, kvm_cpu_cores))

    result = {"r": 0, "e": "操作成功"}

    kvm_mac_list = _get_kvm_mac()
    for adpter in kvm_adpter:
        if adpter['mac'] in kvm_mac_list:
            result = {"r": 1, "e": "MAC地址【{}】，已存在".format(adpter['mac'])}
            return HttpResponse(json.dumps(result, ensure_ascii=False))

    params = pointid.split('|')
    if params[0] == xdata.SNAPSHOT_TYPE_NORMAL:
        snapshot_time = params[2]

    api_request = {"name": kvm_name,
                   "pointid": pointid,
                   "snapshot_time": snapshot_time,
                   "kvm_cpu_count": kvm_cpu_count,
                   "kvm_memory_size": kvm_memory_size,
                   "kvm_memory_unit": kvm_memory_unit,
                   "kvm_storagedevice": kvm_storagedevice,
                   "kvm_adpter": kvm_adpter,
                   "kvm_route": kvm_route,
                   "kvm_gateway": kvm_gateway,
                   "kvm_dns": kvm_dns,
                   "kvm_type": kvm_type,
                   'kvm_pwd': kvm_pwd,
                   'hddctl': hddctl,
                   'vga': vga,
                   'net': net,
                   'cpu': cpu,
                   'boot_firmware': boot_firmware,
                   }
    desc = {'操作': '创建接管主机', '名称': kvm_name, 'debug': api_request}
    if kvm_type == 'forever_kvm':
        desc['保留数据'] = '是'
    else:
        desc['保留数据'] = '否'
    desc['CPU数量'] = kvm_cpu_count
    desc['内存大小'] = '{}{}'.format(kvm_memory_size, kvm_memory_unit)

    kvm_debug = int(request.GET.get('debug', '0'))

    if hasFunctional('clw_desktop_aio') and kvm_type in ('forever_kvm', 'temporary_kvm',):
        if not have_audit_admin():
            return HttpResponse(json.dumps({'r': 1, 'e': '请先创建验证/恢复审批管理员'}, ensure_ascii=False))
        task_info = {'task_type': kvm_type}
        task_info['run'] = run
        task_info['desc'] = desc
        task_info['operator'] = get_operator(request)
        task_info['api_request'] = api_request
        task_info['kvm_debug'] = kvm_debug
        ret = _add_to_audit_task_queue(request.get_host(), request.user, task_info)
        return HttpResponse(json.dumps(ret, ensure_ascii=False))

    result['id'] = api_create_kvm(request.user, get_operator(request), api_request, desc, run, kvm_debug)

    return HttpResponse(json.dumps(result, ensure_ascii=False))


def api_create_kvm(user, operator, api_request, desc, run, kvm_debug):
    host_snapshot_id = api_request["pointid"].split('|')[1]
    host_snapshot_obj = HostSnapshot.objects.get(id=host_snapshot_id)
    if host_snapshot_obj.is_cdp and host_snapshot_obj.cluster_schedule:
        org_snapshot_time = api_request['snapshot_time']
        bNeedFix, api_request['snapshot_time'] = fix_restore_time(host_snapshot_id, api_request['snapshot_time'])
        _logger.info(
            'api_create_kvm bNeedFix={},org_snapshot_time={},snapshot_time={}'.format(bNeedFix, org_snapshot_time,
                                                                                      api_request['snapshot_time']))

    api_response = TakeOverKVMCreate().post(request=None, api_request=api_request)
    if not status.is_success(api_response.status_code):
        e = get_response_error_string(api_response)
        debug = "TakeOverKVMCreate().post() failed {}".format(api_response.status_code)
        desc['结果'] = e
        SaveOperationLog(user, OperationLog.TYPE_TAKEOVER, json.dumps(desc, ensure_ascii=False),
                         operator)
        return HttpResponse(json.dumps({"r": 1, "e": e, "debug": debug}, ensure_ascii=False))

    desc['结果'] = '操作成功'
    SaveOperationLog(user, OperationLog.TYPE_TAKEOVER, json.dumps(desc, ensure_ascii=False),
                     operator)
    kvm_id = 0
    if run == 1:
        id = int(api_response.data['id'])
        _start_kvm(id, user, operator, kvm_debug)
        kvm_id = id
    return kvm_id


def edit_kvm(request):
    id = int(request.POST.get('id', '0'))
    kvm_name = request.POST.get('kvm_name', None)
    kvm_cpu_sockets = request.POST.get('kvm_cpu_sockets', 0)
    kvm_cpu_cores = request.POST.get('kvm_cpu_cores', 0)
    kvm_memory_size = request.POST.get('kvm_memory_size', 0)
    kvm_memory_unit = request.POST.get('kvm_memory_unit', None)
    kvm_adpter = json.loads(request.POST.get('kvm_adpter', '[]'))
    # kvm_gateway = json.loads(request.POST.get('kvm_gateway', '[]'))
    # kvm_dns = json.loads(request.POST.get('kvm_dns', '[]'))
    # kvm_route = json.loads(request.POST.get('kvm_route', '[]'))
    enable_kvm_pwd = request.POST.get('enable_kvm_pwd', '0')
    kvm_pwd = request.POST.get('kvm_pwd', None)
    hddctl = request.POST.get('hddctl', None)
    vga = request.POST.get('vga', None)
    net = request.POST.get('net', None)
    cpu = request.POST.get('cpu', None)

    if enable_kvm_pwd == '0':
        kvm_pwd = None
    elif len(kvm_pwd) != 6:
        kvm_pwd = None

    if _have_same_kvm_name(request, kvm_name, id):
        result = {"r": 1, "e": "名称【{}】，已存在".format(kvm_name)}
        return HttpResponse(json.dumps(result, ensure_ascii=False))

    kvm_cpu_count = int('{}{}'.format(kvm_cpu_sockets, kvm_cpu_cores))

    result = {"r": 0, "e": "操作成功"}

    api_request = {"name": kvm_name,
                   "kvm_cpu_count": kvm_cpu_count,
                   "kvm_memory_size": kvm_memory_size,
                   "kvm_memory_unit": kvm_memory_unit,
                   "kvm_adpter": kvm_adpter,
                   'hddctl': hddctl,
                   'vga': vga,
                   'net': net,
                   'cpu': cpu,
                   # "kvm_route": kvm_route,
                   # "kvm_gateway": kvm_gateway,
                   # "kvm_dns": kvm_dns,
                   # 'kvm_pwd': kvm_pwd,
                   'id': id,
                   }

    kvm_mac_list = _get_kvm_mac(id)
    for adpter in kvm_adpter:
        if adpter['mac'] in kvm_mac_list:
            result = {"r": 1, "e": "MAC地址【{}】，已存在".format(adpter['mac'])}
            return HttpResponse(json.dumps(result, ensure_ascii=False))

    desc = {'操作': '编辑接管主机', '名称': kvm_name, 'debug': api_request}
    desc['CPU核数'] = kvm_cpu_count
    desc['内存大小'] = '{}{}'.format(kvm_memory_size, kvm_memory_unit)
    api_response = TakeOverKVMCreate().update(request=request, api_request=api_request)
    if not status.is_success(api_response.status_code):
        e = get_response_error_string(api_response)
        debug = "TakeOverKVMCreate().update() failed {}".format(api_response.status_code)
        desc['结果'] = e
        SaveOperationLog(request.user, OperationLog.TYPE_TAKEOVER, json.dumps(desc, ensure_ascii=False),
                         get_operator(request))
        return HttpResponse(json.dumps({"r": 1, "e": e, "debug": debug}, ensure_ascii=False))
    desc['结果'] = '成功'
    SaveOperationLog(request.user, OperationLog.TYPE_TAKEOVER, json.dumps(desc, ensure_ascii=False),
                     get_operator(request))
    return HttpResponse(json.dumps(result, ensure_ascii=False))


def start_kvm(request):
    id = int(request.GET.get('id', '0'))
    kvm_debug = int(request.GET.get('debug', '0'))
    result = _start_kvm(id, request.user, get_operator(request), kvm_debug)
    return HttpResponse(json.dumps(result, ensure_ascii=False))


def takeover_close_kvm(id):
    result = {"r": 0, "e": "操作成功"}
    TakeOverKVMobj = TakeOverKVM.objects.get(id=id)
    flag_file = TakeOverKVMobj.kvm_flag_file
    result['kvm_name'] = TakeOverKVMobj.name
    ext_info = json.loads(TakeOverKVMobj.ext_info)
    monitors_addr = r'{}_m'.format(flag_file)
    if os.path.exists(monitors_addr):
        os.unlink(monitors_addr)
    if not os.path.isfile(flag_file):
        result = {"r": 1, "e": "虚拟机已经关闭"}
    else:
        os.remove(flag_file)
        ext_info['poweroff_time'] = time.time()
        flag_file_path, _ = os.path.split(flag_file)
        kvm_flag_file = os.path.join(flag_file_path, uuid.uuid4().hex)
        TakeOverKVM.objects.filter(id=id).update(kvm_flag_file=kvm_flag_file,
                                                 ext_info=json.dumps(ext_info, ensure_ascii=False))
    return result


def close_kvm(request):
    id = int(request.GET.get('id', '0'))
    result = takeover_close_kvm(id)
    desc = {'操作': '接管主机断电', '名称': result['kvm_name'], '结果': '操作成功'}
    SaveOperationLog(request.user, OperationLog.TYPE_TAKEOVER, json.dumps(desc, ensure_ascii=False),
                     get_operator(request))
    return HttpResponse(json.dumps(result, ensure_ascii=False))


def _gen_pointid(host_snapshot_id):
    HostSnapshotObjs = HostSnapshot.objects.filter(id=host_snapshot_id)
    for HostSnapshotObj in HostSnapshotObjs:
        start_datetime = HostSnapshotObj.start_datetime
        start_datetime = start_datetime.strftime('%Y-%m-%dT%H:%M:%S.%f')
        if HostSnapshotObj.is_cdp:
            finish_datetime = HostSnapshotObj.finish_datetime
            if finish_datetime:
                finish_datetime = finish_datetime.strftime('%Y-%m-%dT%H:%M:%S.%f')
            else:
                finish_datetime = start_datetime
            return '{}|{}|{}|{}'.format(xdata.SNAPSHOT_TYPE_CDP, host_snapshot_id, start_datetime, finish_datetime)
        return '{}|{}|{}'.format(xdata.SNAPSHOT_TYPE_NORMAL, host_snapshot_id, start_datetime)


def _fmt_size(size_byte):
    if size_byte == 0:
        return '-'
    if size_byte < 1024 * 1024 * 1024:
        return '{0:.2f}MB'.format(size_byte / 1024 ** 2)
    return '{0:.2f}GB'.format(size_byte / 1024 ** 3)


def _is_kvm_alive(qcow2path):
    p = psutil.process_iter()
    for r in p:
        if r.name().strip().lower() in ('qemu-kvm', 'qemu-system-x86_64',):
            for line in r.cmdline():
                if qcow2path in line:
                    return True
    return False


def _getkvminfo(obj):
    id = obj['id']
    name = obj['name']
    kvm_type = obj['kvm_type']
    snapshot_time = obj['snapshot_time']
    snapshot_time = xdatetime.string2datetime(snapshot_time).strftime('%Y-%m-%d %H:%M:%S')
    flag_file = obj['kvm_flag_file']
    obj['kvm_cpu_count'] = str(obj['kvm_cpu_count'])
    if len(obj['kvm_cpu_count']) == 1:
        kvm_cpu_sockets = 1
        kvm_cpu_cores = int(obj['kvm_cpu_count'])
    else:
        kvm_cpu_sockets = int(obj['kvm_cpu_count'][0])
        kvm_cpu_cores = int(obj['kvm_cpu_count'][1])
    kvm_cpu_count = kvm_cpu_sockets * kvm_cpu_cores
    kvm_memory_size = obj['kvm_memory_size']
    kvm_memory_unit = obj['kvm_memory_unit']
    kvm_memory = '{}{}'.format(kvm_memory_size, kvm_memory_unit)
    pointid = _gen_pointid(obj['host_snapshot'])
    kvm_status = '已关机'
    ext_info = json.loads(obj['ext_info'])
    vnc_address = ext_info['disk_snapshots']['boot_devices'][0]['device_profile']['nbd']['vnc_address']
    port = vnc_address.split(":")
    if len(port) == 2:
        vnc_port = int(port[1]) + 5900

    try:
        with open(flag_file, 'r') as fout:
            info = json.loads(fout.read())
            kvm_status = info.get('msg', '已发送开机命令')
    except Exception as e:
        pass

    disk_size = 0
    qcow2path = ext_info['disk_snapshots']['boot_devices'][0]['device_profile']['qcow2path']
    if qcow2path and os.path.isfile(qcow2path):
        disk_size += os.path.getsize(qcow2path)

    data_devices = ext_info['disk_snapshots']['data_devices']
    for data_device in data_devices:
        qcow2path = data_device['device_profile']['qcow2path']
        if qcow2path and os.path.isfile(qcow2path):
            disk_size += os.path.getsize(qcow2path)

    monitors_addr = r'{}_m'.format(flag_file)

    if os.path.isfile(flag_file) and kvm_status == '已关机':
        kvm_status = '异常'

    if kvm_status != '已关机':
        if kvm_status == '已开机' and not _is_kvm_alive(qcow2path):
            kvm_status = '异常'

        if os.path.isfile(flag_file) and os.path.exists(monitors_addr):
            # startkvmbtn, resetbtn, powerdownbtn, closekvmbtn
            optpye = 4
        elif os.path.isfile(flag_file):
            # startkvmbtn, closekvmbtn
            optpye = 2
        tmp_kvm_status = ''
        if kvm_status in ('已关机', '已开机', '异常',):
            tmp_kvm_status = kvm_status
        startkvmbtn = '<span style="color:#000088;cursor:pointer;" title="操作" onclick="opkvm(this,\'{}\',\'{}\',\'{}\')">[操作]</span>'.format(
            id, optpye, tmp_kvm_status)
    else:
        startkvmbtn = '<span style="color:#000088;cursor:pointer;" title="启动" onclick="startkvm(\'{}\')">[启动]</span>'.format(
            id)
    op = '{}'.format(startkvmbtn)
    return [id, name, kvm_type, kvm_memory, kvm_cpu_count, _fmt_size(disk_size), snapshot_time, vnc_port, kvm_status,
            op, pointid]


def kvmlist(request):
    page = int(request.GET.get('page', '1'))
    rows = int(request.GET.get('rows', '30'))
    sidx = request.GET.get('sidx', 'name')
    sord = request.GET.get('sord', 'asc')
    debug = request.GET.get('debug', '0')
    if sidx not in ('name', 'kvm_type', 'snapshot_time',):
        sidx = None
    if sidx and sord == 'desc':
        sidx = '-{}'.format(sidx)

    api_request = {
        'page': page,
        'rows': rows,
        'sidx': sidx,
        'debug': debug,
    }
    api_response = TakeOverKVMCreate().get(request=request, api_request=api_request)
    if not status.is_success(api_response.status_code):
        e = get_response_error_string(api_response)
        debug = "TakeOverKVMExecute().post() failed {}".format(api_response.status_code)
        return HttpResponse(json.dumps({"r": 2, "e": e, "debug": debug}, ensure_ascii=False))

    kvmlist = list()
    for obj in api_response.data:
        kvmlist.append(_getkvminfo(obj))

    paginator = Paginator(object_list=kvmlist, per_page=rows)
    records = paginator.count
    total = paginator.num_pages
    page = total if page > total else page
    object_list = paginator.page(page).object_list

    result = {'r': 0, 'a': 'list', 'page': page, 'total': total, 'records': records, 'rows': object_list}
    return HttpResponse(json.dumps(result, ensure_ascii=False))


def del_kvm(request):
    ids = request.POST.get('ids', '')
    result = {'r': 0, "e": "操作成功"}
    for id in ids.split(","):
        api_request = {
            'id': id,
        }
        TakeOverKVMobj = TakeOverKVM.objects.get(id=id)
        kvm_name = TakeOverKVMobj.name
        ext_info = json.loads(TakeOverKVMobj.ext_info)
        kvm_flag_file = TakeOverKVMobj.kvm_flag_file

        device_path_list = list()
        for boot_device in ext_info['disk_snapshots']['boot_devices']:
            device_path_list.append(boot_device['device_profile']['nbd']['device_path'])

        for data_device in ext_info['disk_snapshots']['data_devices']:
            device_path_list.append(data_device['device_profile']['nbd']['device_path'])

        if not os.path.isfile(kvm_flag_file) and _gznbd_is_run(device_path_list):
            result = {"r": 2, "e": "虚拟机{}正在释放资源，请稍候再试。".format(kvm_name), "device_path_list": device_path_list}
            return HttpResponse(json.dumps(result, ensure_ascii=False))

        desc = {'操作': '删除接管主机', '名称': kvm_name, 'debug': api_request}
        api_response = TakeOverKVMCreate().delete(request=request, api_request=api_request)
        if not status.is_success(api_response.status_code):
            e = get_response_error_string(api_response)
            debug = "TakeOverKVMCreate().delete failed {}".format(api_response.status_code)
            desc['结果'] = e
            SaveOperationLog(request.user, OperationLog.TYPE_TAKEOVER, json.dumps(desc, ensure_ascii=False),
                             get_operator(request))
            return HttpResponse(json.dumps({"r": 2, "e": e, "debug": debug}, ensure_ascii=False))
        desc['结果'] = '操作成功'
        SaveOperationLog(request.user, OperationLog.TYPE_TAKEOVER, json.dumps(desc, ensure_ascii=False),
                         get_operator(request))
    return HttpResponse(json.dumps(result, ensure_ascii=False))


def del_kvm_hdd(request):
    ids = request.POST.get('ids', '')
    result = {'r': 0, "e": "操作成功"}
    for id in ids.split(","):
        api_request = {
            'id': id,
            'only_hdd': True,
        }
        api_response = TakeOverKVMCreate().delete(request=request, api_request=api_request)
        if not status.is_success(api_response.status_code):
            e = get_response_error_string(api_response)
            debug = "TakeOverKVMCreate().delete failed {}".format(api_response.status_code)
            return HttpResponse(json.dumps({"r": 2, "e": e, "debug": debug}, ensure_ascii=False))
    return HttpResponse(json.dumps(result, ensure_ascii=False))


def _get_adapter_names():
    adapter_names = list()
    jsonstr = box_service.getNetworkInfos()
    adapterlist = json.loads(jsonstr)
    if len(adapterlist) != 2 or type(adapterlist) != type([]):
        return adapter_names

    for element in adapterlist:
        if (type(element) == type({})):
            for name, adapter in element.items():
                if adapter['link'] != 'ok':
                    continue
                if adapter['nettype'] == 'phy' and adapter['mastername'] == '':
                    adapter_names.append(adapter['name'])
                if adapter['nettype'] == 'bond':
                    adapter_names.append(adapter['name'])
    return adapter_names


def get_adapter_name(request):
    result = {'r': 0, "e": "操作成功", "adapter_names": _get_adapter_names()}
    return HttpResponse(json.dumps(result, ensure_ascii=False))


def _get_storagenode_by_file(kvm_flag_file):
    path = os.path.split(kvm_flag_file)[0]
    path = os.path.split(path)[0]
    storagenode = StorageNode.objects.filter(path=path).first()
    if storagenode:
        return storagenode.name
    return path


def _get_hardware_info():
    info = dict()
    jsonstr = box_service.queryTakeOverHostInfo('none')
    if jsonstr:
        jsonobj = json.loads(jsonstr)
        info['phy_available'] = jsonobj['total_memory_mb_for_takeover']
        info['phy_cpu_count'] = jsonobj['total_cpu_number_for_takover']
    return info


def _del_my_private_adpter(kvm_adpter):
    kvm_adpter_new = list()
    for adpter in kvm_adpter:
        if adpter['name'] in ('my_private_aio',):
            continue
        kvm_adpter_new.append(adpter)

    return kvm_adpter_new


def get_kvm_info(request):
    id = request.GET.get('id', '-1')
    result = {'r': 0, "e": "操作成功"}
    api_request = {"id": id}
    api_response = TakeOverKVMCreate().get(request=request, api_request=api_request)
    if not status.is_success(api_response.status_code):
        e = get_response_error_string(api_response)
        debug = "TakeOverKVMExecute().post() failed {}".format(api_response.status_code)
        return HttpResponse(json.dumps({"r": 2, "e": e, "debug": debug}, ensure_ascii=False))

    info = dict()

    for obj in api_response.data:
        info['name'] = obj['name']
        obj['kvm_cpu_count'] = str(obj['kvm_cpu_count'])
        if len(obj['kvm_cpu_count']) == 1:
            kvm_cpu_sockets = 1
            kvm_cpu_cores = int(obj['kvm_cpu_count'])
        else:
            kvm_cpu_sockets = int(obj['kvm_cpu_count'][0])
            kvm_cpu_cores = int(obj['kvm_cpu_count'][1])
        info['kvm_cpu_count'] = kvm_cpu_sockets * kvm_cpu_cores
        info['kvm_cpu_sockets'] = kvm_cpu_sockets
        info['kvm_cpu_cores'] = kvm_cpu_cores
        info['kvm_memory_size'] = obj['kvm_memory_size']
        info['kvm_memory_unit'] = obj['kvm_memory_unit']
        info['storagenode'] = _get_storagenode_by_file(obj['kvm_flag_file'])
        info['adapter_names'] = _get_adapter_names()
        ext_info = json.loads(obj['ext_info'])
        if obj['kvm_type'] == 'forever_kvm':
            info['kvm_adpter'] = _del_my_private_adpter(ext_info.get('kvm_adpter', []))
        else:
            info['kvm_adpter'] = ext_info.get('kvm_adpter', [])
        info['kvm_route'] = ext_info.get('kvm_route', [])
        info['kvm_gateway'] = ext_info.get('kvm_gateway', [])
        info['kvm_dns'] = ext_info.get('kvm_dns', [])
        info['kvm_pwd'] = ext_info.get("kvm_pwd", '')
        info['hddctl'] = ext_info.get("hddctl", 'scsi-hd')
        info['vga'] = ext_info.get("vga", 'std')
        info['net'] = ext_info.get("net", 'rtl8139')
        info['cpu'] = ext_info.get("cpu", 'host')
        info['logic'] = ext_info.get("logic", 'windows')
        info['boot_firmware'] = ext_info.get("boot_firmware", 'auto')
        info['id'] = id
        is_poweroff = True
        if obj['kvm_flag_file'] and os.path.isfile(obj['kvm_flag_file']):
            is_poweroff = False
        info['is_poweroff'] = is_poweroff

    hinfo = _get_hardware_info()
    info['phy_cpu_count'] = hinfo['phy_cpu_count']
    info['phy_available'] = hinfo['phy_available']

    result['info'] = info

    return HttpResponse(json.dumps(result, ensure_ascii=False))


def get_max_hardware_canuse(request):
    result = {'r': 0, "e": "操作成功"}
    info = dict()
    hinfo = _get_hardware_info()
    info['phy_cpu_count'] = hinfo['phy_cpu_count']
    info['phy_available'] = hinfo['phy_available']
    result['info'] = info
    return HttpResponse(json.dumps(result, ensure_ascii=False))


def _send_vnc_cmd(server_address, cmd, sleep_time=1):
    rev = None
    _logger.info('_send_vnc_cmd cmd={},server_address={}'.format(cmd, server_address))
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(server_address)
        sock.sendall(cmd.encode('utf8'))
        time.sleep(sleep_time)
        rev = sock.recv(1024).decode('utf8')
        _logger.info('_send_vnc_cmd rev={}'.format(rev))
    except Exception as e:
        _logger.error('_send_vnc_cmd Failed.cmd={}'.format(cmd))
    finally:
        sock.close()
    return rev


def kvm_system_reset(request):
    id = request.GET.get('id', '-1')
    result = {'r': 0, "e": "操作成功"}
    api_request = {"id": id}
    desc = {'操作': '发送重置命令'}
    api_response = TakeOverKVMCreate().get(request=request, api_request=api_request)
    if not status.is_success(api_response.status_code):
        e = get_response_error_string(api_response)
        debug = "TakeOverKVMExecute().post() failed {}".format(api_response.status_code)
        desc['结果'] = '操作失败，找不到虚拟机'
        desc['debug'] = debug
        SaveOperationLog(request.user, OperationLog.TYPE_TAKEOVER, json.dumps(desc, ensure_ascii=False),
                         get_operator(request))
        return HttpResponse(json.dumps({"r": 2, "e": e, "debug": debug}, ensure_ascii=False))

    for obj in api_response.data:
        kvm_flag_file = obj['kvm_flag_file']
        desc['名称'] = obj['name']
        monitors_addr = r'{}_m'.format(kvm_flag_file)
        if not os.path.exists(monitors_addr):
            result['r'] = 1
            result['e'] = '不能与虚拟机通信，虚拟机是否已关闭'
            desc['结果'] = result['e']
            SaveOperationLog(request.user, OperationLog.TYPE_TAKEOVER, json.dumps(desc, ensure_ascii=False),
                             get_operator(request))
            return HttpResponse(json.dumps(result, ensure_ascii=False))
        if _send_vnc_cmd(monitors_addr, 'system_reset\n') is None:
            result['r'] = 2
            result['e'] = '发送重置命令失败'
            desc['结果'] = result['e']
            SaveOperationLog(request.user, OperationLog.TYPE_TAKEOVER, json.dumps(desc, ensure_ascii=False),
                             get_operator(request))
            return HttpResponse(json.dumps(result, ensure_ascii=False))

    desc['结果'] = '操作成功'
    SaveOperationLog(request.user, OperationLog.TYPE_TAKEOVER, json.dumps(desc, ensure_ascii=False),
                     get_operator(request))

    return HttpResponse(json.dumps(result, ensure_ascii=False))


def get_info_from_syscmd(in_cmd_line, timeout=120):
    if len(in_cmd_line) <= 0:
        _logger.error("invalid cmd line")
        return -1, None
    try:
        _logger.info("start cmd {}".format(in_cmd_line))
        p = subprocess.Popen(in_cmd_line, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            outs, errs = p.communicate(timeout=timeout)
            retval = p.returncode
        except subprocess.TimeoutExpired:
            os.kill(p.pid, signal.SIGKILL)
            _logger.warning('cmd {} process killed,timer {} begin'.format(in_cmd_line, timeout))
            outs, errs = p.communicate()
            retval = p.returncode
            _logger.warning('cmd {} process killed,timer {} end {}'.format(in_cmd_line, timeout, retval))

        _logger.info("run cmd {} ret {} | {} | {}".format(in_cmd_line, retval, outs, errs))
        return retval, outs.decode("utf-8", "replace"), errs.decode()
    except Exception as e:
        _logger.error("run cmd {} error {} - {}".format(in_cmd_line, e, traceback.format_exc()))
        return -1, None, None


def kvm_system_powerdown(request):
    id = request.GET.get('id', '-1')
    result = {'r': 0, "e": "操作成功"}
    api_request = {"id": id}

    desc = {'操作': '发送关机命令'}

    api_response = TakeOverKVMCreate().get(request=request, api_request=api_request)
    if not status.is_success(api_response.status_code):
        e = get_response_error_string(api_response)
        debug = "TakeOverKVMExecute().post() failed {}".format(api_response.status_code)
        desc['结果'] = '操作失败，找不到虚拟机'
        desc['debug'] = debug
        SaveOperationLog(request.user, OperationLog.TYPE_TAKEOVER, json.dumps(desc, ensure_ascii=False),
                         get_operator(request))
        return HttpResponse(json.dumps({"r": 2, "e": e, "debug": debug}, ensure_ascii=False))

    for obj in api_response.data:
        kvm_flag_file = obj['kvm_flag_file']
        desc['名称'] = obj['name']
        monitors_addr = r'{}_m'.format(kvm_flag_file)
        if not os.path.exists(monitors_addr):
            result['r'] = 1
            result['e'] = '不能与虚拟机通信，虚拟机是否已关闭'
            desc['结果'] = result['e']
            SaveOperationLog(request.user, OperationLog.TYPE_TAKEOVER, json.dumps(desc, ensure_ascii=False),
                             get_operator(request))
            return HttpResponse(json.dumps(result, ensure_ascii=False))
        if _send_vnc_cmd(monitors_addr, 'system_powerdown\n') is None:
            result['r'] = 2
            result['e'] = '发送关机命令失败'
            desc['结果'] = result['e']
            SaveOperationLog(request.user, OperationLog.TYPE_TAKEOVER, json.dumps(desc, ensure_ascii=False),
                             get_operator(request))
            return HttpResponse(json.dumps(result, ensure_ascii=False))

    desc['结果'] = '操作成功'
    SaveOperationLog(request.user, OperationLog.TYPE_TAKEOVER, json.dumps(desc, ensure_ascii=False),
                     get_operator(request))

    return HttpResponse(json.dumps(result, ensure_ascii=False))


def kill_test_kvm_pe_start(request):
    token = request.GET.get('token', '-1')
    result = {'r': 0, "e": "操作成功"}
    if token != '-1':
        vnc_num = int(token) - 5900
        vnc_str = 'grep 0.0.0.0:{vnc_port}'.format(vnc_port=vnc_num)
        token_pid = "ps -aux | grep qemu-kvm | grep -v /bin/sh | " + vnc_str + "> result.txt;awk '{print $2}' result.txt"
        retval, stdout, stderr = get_info_from_syscmd(token_pid)
        modinfo_result_list = stdout.replace(' ', '').split('\n')
        for filed in modinfo_result_list:
            if filed != '':
                get_info_from_syscmd('kill ' + filed)
    else:
        result['e'] = '发送关机命令失败'
    return HttpResponse(json.dumps(result, ensure_ascii=False))


def get_hardware_info(request):
    result = {'r': 0, "e": "操作成功"}

    jsonstr = box_service.queryTakeOverHostInfo('none')
    if jsonstr:
        jsonobj = json.loads(jsonstr)
        result['total_memory_mb_for_takeover'] = '{}MB'.format(jsonobj['total_memory_mb_for_takeover'])
        result['total_cpu_number_for_takeover'] = jsonobj['total_cpu_number_for_takover']
        result['one_memory_mb_for_takeover'] = '{}MB'.format(jsonobj['one_memory_mb_for_takeover'])
        result['one_cpu_number_for_takeover'] = jsonobj['one_cpu_number_for_takover']
        result['used_memory_mb'] = '{}MB'.format(jsonobj['used_memory_mb'])
        result['used_memory_mb_for_restore'] = '{}MB'.format(jsonobj['used_memory_mb_for_restore'])
        result['used_memory_mb_for_takeover'] = '{}MB'.format(jsonobj['used_memory_mb_for_takover'])
        result['used_cpu_number'] = jsonobj['used_cpu_number']
        result['used_cpu_number_for_restore'] = jsonobj['used_cpu_number_for_restore']
        result['used_cpu_number_for_takeover'] = jsonobj['used_cpu_number_for_takover']
        result['canuse_memory_mb_for_takeover'] = '{}MB'.format(
            jsonobj['total_memory_mb_for_takeover'] - jsonobj['used_memory_mb'])
        result['canuse_cpu_number_for_takeover'] = jsonobj['total_cpu_number_for_takover'] - jsonobj['used_cpu_number']
    return HttpResponse(json.dumps(result, ensure_ascii=False))


def get_hardware_info_no_unit(request):
    result = {'r': 0, "e": "操作成功"}

    jsonstr = box_service.queryTakeOverHostInfo('none')
    if jsonstr:
        jsonobj = json.loads(jsonstr)
        result['total_memory_mb_for_takeover'] = '{}'.format(jsonobj['total_memory_mb_for_takeover'])
        result['total_cpu_number_for_takeover'] = jsonobj['total_cpu_number_for_takover']
        result['one_memory_mb_for_takeover'] = '{}'.format(jsonobj['one_memory_mb_for_takeover'])
        result['one_cpu_number_for_takeover'] = jsonobj['one_cpu_number_for_takover']
        result['used_memory_mb'] = '{}'.format(jsonobj['used_memory_mb'])
        result['used_memory_mb_for_restore'] = '{}'.format(jsonobj['used_memory_mb_for_restore'])
        result['used_memory_mb_for_takeover'] = '{}'.format(jsonobj['used_memory_mb_for_takover'])
        result['used_cpu_number'] = jsonobj['used_cpu_number']
        result['used_cpu_number_for_restore'] = jsonobj['used_cpu_number_for_restore']
        result['used_cpu_number_for_takeover'] = jsonobj['used_cpu_number_for_takover']
        canuse_memory_mb_for_takeover = jsonobj['total_memory_mb_for_takeover'] - jsonobj['used_memory_mb']
        if canuse_memory_mb_for_takeover < 0:
            canuse_memory_mb_for_takeover = 0
        result['canuse_memory_mb_for_takeover'] = '{}'.format(canuse_memory_mb_for_takeover)
        canuse_cpu_number_for_takeover = jsonobj['total_cpu_number_for_takover'] - jsonobj['used_cpu_number']
        if canuse_cpu_number_for_takeover < 0:
            canuse_cpu_number_for_takeover = 0
        result['canuse_cpu_number_for_takeover'] = canuse_cpu_number_for_takeover
    return HttpResponse(json.dumps(result, ensure_ascii=False))


def _get_need_IDE_os_linux():
    # 需要qemu-kvm以IDE方式加载的OS
    ide_list = list()
    # bit_opt 可为 32 64 或 all
    ide_list.append({'kernel_version': '2.6.18',
                     'release_version': 'Linux-2.6.18-[0-9]+.el5[\s\S]*-[\s\S]*-with-redhat-5.*[0-2]{0,1}-[\s\S]*',
                     'bit_opt': 'all'})
    # ubuntu12.04.3
    ide_list.append({'kernel_version': '3.8.0',
                     'release_version': 'Linux-3.8.0-[0-9]+-[\s\S]*-[\s\S]*-with-debian-wheezy-[\s\S]*',
                     'bit_opt': 'all'})

    ide_list.append({'kernel_version': '4.10.0',
                     'release_version': 'Linux-4.10.0-[0-9]+-generic-x86_64-with-debian-stretch-sid',
                     'bit_opt': 'all'})
    return ide_list


def _get_need_virtio_os_linux():
    virtio_list = list()
    virtio_list.append({'kernel_version': '2.6.18',
                        'release_version': 'Linux-2.6.18-194.el5-x86_64-with-redhat-5.5-Final',
                        'bit_opt': 'all'})
    return virtio_list


def _get_hdd_drive(linux_info):
    hdd_drive = 'virtio-blk'
    kernel_ver = linux_info['kernel_ver']
    bit_opt = linux_info['bit_opt']
    platform = linux_info['platform']
    ide_list = _get_need_IDE_os_linux()
    virtio_list = _get_need_virtio_os_linux()

    for one_os in virtio_list:
        if kernel_ver != one_os['kernel_version']:
            continue
        if one_os['bit_opt'] == 'all':
            pass
        elif bit_opt[0:2] != one_os['bit_opt']:
            continue
        p = re.compile(one_os['release_version'])
        if p.match(platform):
            return hdd_drive

    for one_os in ide_list:
        if kernel_ver != one_os['kernel_version']:
            continue
        if one_os['bit_opt'] == 'all':
            pass
        elif bit_opt[0:2] != one_os['bit_opt']:
            continue
        p = re.compile(one_os['release_version'])
        if not p.match(platform):
            continue
        return 'IDE'

    return hdd_drive


def _get_vga_drive(linux_info):
    return 'std'


def _logic(ext_info):
    system_infos = ext_info['system_infos']
    if 'LINUX' not in system_infos['System']['SystemCaption'].upper():
        return 'windows'
    return 'linux'


def get_takeover_hardware_recommend(pointid, b_get_ext_info):
    point_params = pointid.split('|')
    host_snapshot_id = point_params[1]
    host_snapshot = HostSnapshot.objects.get(id=host_snapshot_id)
    host_snapshot_ext_info = json.loads(host_snapshot.ext_info)
    logic = _logic(host_snapshot_ext_info)
    if logic == 'windows':
        result = {'hddctl': 'scsi-hd', 'vga': 'std', 'net': 'rtl8139', 'logic': logic}
    else:
        linux_info = host_snapshot_ext_info['system_infos']['Linux']
        result = {'hddctl': _get_hdd_drive(linux_info), 'vga': _get_vga_drive(linux_info), 'net': 'rtl8139',
                  'logic': logic}
    result['host_id'] = host_snapshot.host.id
    if b_get_ext_info:
        result['host_snapshot_ext_info'] = host_snapshot.ext_info
    return result


def get_hardware_recommend(request):
    pointid = request.GET.get('pointid', None)
    result = get_takeover_hardware_recommend(pointid, False)
    result['r'] = 0
    return HttpResponse(json.dumps(result, ensure_ascii=False))


def _MD5(src):
    m2 = hashlib.md5()
    m2.update(src.encode('utf-8'))
    return m2.hexdigest()


def get_kvm_usbinfo(request):
    id = request.GET.get('id', '-1')
    result = {'r': 0, "e": "操作成功", "list": []}
    api_request = {"id": id}
    api_response = TakeOverKVMCreate().get(request=request, api_request=api_request)
    if not status.is_success(api_response.status_code):
        e = get_response_error_string(api_response)
        debug = "TakeOverKVMExecute().post() failed {}".format(api_response.status_code)
        return HttpResponse(json.dumps({"r": 2, "e": e, "debug": debug}, ensure_ascii=False))

    for obj in api_response.data:
        kvm_flag_file = obj['kvm_flag_file']
        monitors_addr = r'{}_m'.format(kvm_flag_file)
        if not os.path.exists(monitors_addr):
            result['r'] = 1
            result['e'] = '不能与虚拟机通信，虚拟机是否已关闭'
            return HttpResponse(json.dumps(result, ensure_ascii=False))
        info_usb_list = list()
        info_usbhost_list = list()
        rev = _send_vnc_cmd(monitors_addr, 'info usb\n', 2)
        if rev is not None:
            _logger.info(r'get_kvm_usbinfo info usb={}'.format(rev))
            rev = rev.split('\n')
            for line in rev:
                line = line.strip()
                line = line.split(',')
                if len(line) == 4:
                    Product = line[3].strip()
                    p = re.compile('Product')
                    Product = p.sub(r'', Product)
                    info_usb_list.append({'name': Product.strip()})

        try_usbhost_conut = 30
        bneedtry = False
        while True:
            rev = _send_vnc_cmd(monitors_addr, 'info usbhost\n', 3)
            if rev is not None:
                _logger.info(r'get_kvm_usbinfo info usbhost={}'.format(rev))
                rev = rev.split('\n')
                name = None
                vendorid = None
                productid = None
                for line in rev:
                    line = line.strip()
                    line = line.split(',')
                    if len(line) == 4:
                        bneedtry = True
                    if len(line) == 2:
                        name = line[1].strip()
                        tmp = re.findall('\w+:\w+', line[0].strip())
                        if len(tmp) == 1:
                            md5 = _MD5(tmp[0])
                            ids = tmp[0].split(':')
                            if len(ids) == 2:
                                vendorid = ids[0]
                                productid = ids[1]
                    if name and vendorid and productid:
                        info_usbhost_list.append(
                            {'name': name, 'vendorid': vendorid, 'productid': productid, 'id': 'a{}'.format(md5)})
                        name, vendorid, productid = None, None, None
                if len(info_usbhost_list) > 0:
                    break
                if not bneedtry:
                    break;
                try_usbhost_conut = try_usbhost_conut - 1
                if try_usbhost_conut < 0:
                    break
                _logger.info('info usbhost try_usbhost_conut={}'.format(try_usbhost_conut))

        all_list = list()

        for info_usbhost in info_usbhost_list:
            bIsIn = False
            for info_usb in info_usb_list:
                if info_usbhost['name'] == info_usb['name']:
                    bIsIn = True
                    break
            if bIsIn:
                info_usbhost['in'] = True
            else:
                info_usbhost['in'] = False
            p = re.compile('[\s\S]*QEMU[\s\S]*')
            if p.match(info_usbhost['name']):
                continue
            all_list.append(info_usbhost)
        result['list'] = all_list
    return HttpResponse(json.dumps(result, ensure_ascii=False))


def set_kvm_usb(request):
    vendorid = request.GET.get('vendorid', '0')
    productid = request.GET.get('productid', '0')
    id = request.GET.get('id', '0')
    deviceid = request.GET.get('deviceid', '0')
    isin = int(request.GET.get('isin', '0'))
    result = {'r': 0, "e": "操作成功"}
    api_request = {"id": id}
    api_response = TakeOverKVMCreate().get(request=request, api_request=api_request)
    if not status.is_success(api_response.status_code):
        e = get_response_error_string(api_response)
        debug = "TakeOverKVMExecute().post() failed {}".format(api_response.status_code)
        return HttpResponse(json.dumps({"r": 2, "e": e, "debug": debug}, ensure_ascii=False))

    for obj in api_response.data:
        kvm_flag_file = obj['kvm_flag_file']
        monitors_addr = r'{}_m'.format(kvm_flag_file)
        if not os.path.exists(monitors_addr):
            result['r'] = 1
            result['e'] = '不能与虚拟机通信，虚拟机是否已关闭'
            return HttpResponse(json.dumps(result, ensure_ascii=False))

        if isin == 1:
            rev = _send_vnc_cmd(monitors_addr, 'device_del {}\n'.format(deviceid))
        elif isin == 0:
            rev = _send_vnc_cmd(monitors_addr,
                                ' device_add usb-host,vendorid=0x{vendorid},productid=0x{productid},id={deviceid}\n'.format(
                                    vendorid=vendorid, productid=productid, deviceid=deviceid))
        else:
            result['r'] = 1
            result['e'] = '错误的参数 isin={}'.format(isin)
    return HttpResponse(json.dumps(result, ensure_ascii=False))


def get_host_obj_from_take_over(take_over, all_hosts):
    for host in all_hosts:
        system_infos = json.loads(host.ext_info).get('system_infos')
        if not system_infos:
            _logger.info('get_host_obj_from_take_over system_infos is None host.id={}'.format(host.id))
            continue
        running_platform = system_infos.get('running_platform', 'normal')
        if running_platform.startswith('takeover_'):
            host_take_over_id = int(running_platform.split('_')[-1])
            if host_take_over_id == take_over.id:
                return host
    return None


def is_an_take_over_exist_backup_plan(take_over, all_hosts):
    host_obj = get_host_obj_from_take_over(take_over, all_hosts)
    if not host_obj:
        return False

    return BackupTaskSchedule.objects.filter(host=host_obj, deleted=False).exists()


def check_host_need_plan(request):
    ret_list, all_hosts = [], Host.objects.filter(user=request.user).all()
    take_overs = TakeOverKVM.objects.filter(kvm_type='forever_kvm', host_snapshot__host__user=request.user).all()
    for take_over in take_overs:
        if is_an_take_over_exist_backup_plan(take_over, all_hosts):
            continue

        host_obj = get_host_obj_from_take_over(take_over, all_hosts)
        if host_obj:
            ret_list.append({'take_over_name': take_over.name, 'host_name': get_host_name(host_obj.ident)})
        else:
            ret_list.append({'take_over_name': take_over.name, 'host_name': ''})

    return HttpResponse(json.dumps(ret_list, ensure_ascii=False))


def check_kvm_md5_file(request):
    # kvm没开机，qcow2path存在，但qcow2path的md5文件不存在
    id = int(request.GET.get('id', '0'))
    TakeOverKVMobj = TakeOverKVM.objects.get(id=id)
    kvm_name = TakeOverKVMobj.name
    ret = {'r': 0, 'id': id, 'kvm_name': kvm_name}
    flag_file = TakeOverKVMobj.kvm_flag_file
    if os.path.isfile(flag_file):
        return HttpResponse(json.dumps(ret, ensure_ascii=False))
    ext_info = json.loads(TakeOverKVMobj.ext_info)
    for boot_device in ext_info['disk_snapshots']['boot_devices']:
        qcow2path = boot_device['device_profile']['qcow2path']
        filesizepath = qcow2path + '.md5'
        if os.path.isfile(qcow2path) and not os.path.isfile(filesizepath):
            ret['r'] = 99
            return HttpResponse(json.dumps(ret, ensure_ascii=False))
    for data_device in ext_info['disk_snapshots']['data_devices']:
        qcow2path = data_device['device_profile']['qcow2path']
        filesizepath = qcow2path + '.md5'
        if os.path.isfile(qcow2path) and not os.path.isfile(filesizepath):
            ret['r'] = 99
            return HttpResponse(json.dumps(ret, ensure_ascii=False))
    return HttpResponse(json.dumps(ret, ensure_ascii=False))
