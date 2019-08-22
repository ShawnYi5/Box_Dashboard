import html
import json
import os
import psutil
import subprocess
from datetime import datetime

from django.http import HttpResponse

from apiv1.models import HostSnapshot, DiskSnapshot, Host, MigrateTask, RestoreTask
from apiv1.snapshot import GetSnapshotList, GetDiskSnapshot
from box_dashboard import xlogging
from xdashboard.request_util import get_operator

_logger = xlogging.getLogger(__name__)
pe_path = '/var/lib/tftpboot/winpe.iso'
external_pe_file = '/dev/shm/external_pe_file.json'


def query_disksnapshot_path_by_hostsnapshot(request):
    point_params = request.GET['point']
    host_snapshot = HostSnapshot.objects.get(id=point_params.split('|')[1])
    disk_snapshots = host_snapshot.disk_snapshots.all()
    paths = [{'abs_path': disk_snapshot.image_path, 'id': disk_snapshot.id, 'snapshot_name': disk_snapshot.ident}
             for disk_snapshot in disk_snapshots]
    return HttpResponse(json.dumps(paths))


def query_disksnapshot_chain(request):
    disk_snapshot_id = request.GET['disksnapshot_id']
    cdp_time_point = request.GET.get('cdp_time_point', None)
    hostsnapshot_id = request.GET.get('hostsnapshot_id', None)
    if None not in [cdp_time_point, hostsnapshot_id]:  # 查询CDP链
        restore_timeStp = datetime.strptime(cdp_time_point, '%Y-%m-%d %H:%M:%S.%f').timestamp()
        host_snapshot = HostSnapshot.objects.get(id=hostsnapshot_id)
        disk_ident = DiskSnapshot.objects.get(id=disk_snapshot_id).disk.ident
        cur_ident, rstamp = GetDiskSnapshot.query_cdp_disk_snapshot_ident(host_snapshot, disk_ident, restore_timeStp)

        if None in [cur_ident, rstamp]:
            disk_snapshot = DiskSnapshot.objects.get(id=disk_snapshot_id)
            disk_all_image_until_select = GetSnapshotList.query_snapshots_by_snapshot_object(disk_snapshot, [])
        else:
            disk_snapshot = DiskSnapshot.objects.get(ident=cur_ident)
            disk_all_image_until_select = GetSnapshotList.query_snapshots_by_snapshot_object(disk_snapshot, [], rstamp)
    else:
        disk_snapshot = DiskSnapshot.objects.get(id=disk_snapshot_id)
        disk_all_image_until_select = GetSnapshotList.query_snapshots_by_snapshot_object(disk_snapshot, [])

    jsonDt = []
    for image in disk_all_image_until_select:
        jsonDt.append({
            'path': image.path,
            'status': 'Exist' if os.path.exists(image.path) else 'NoExist',
            'snapshot_name': 'see cdp-path' if image.path.endswith('.cdp') else image.snapshot
        })

    return HttpResponse(json.dumps(jsonDt))


def _execute_cmd_and_return_code(cmd):
    with subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True) as p:
        stdout, stderr = p.communicate()
    return p.returncode, stdout, stderr


def decode_pwd(request):
    from xdashboard.models import OperationLog
    from xdashboard.handle.logserver import SaveOperationLog
    encode_txt = request.POST.get('encode_txt', None)
    retjson = dict()
    cmd = 'dec_passwd {}'.format(encode_txt)
    ret = _execute_cmd_and_return_code(cmd)
    retjson['r'] = ret[0]
    retjson['encode_txt'] = encode_txt
    retjson['decode_txt'] = '{}{}'.format(ret[1], ret[2])
    SaveOperationLog(
        request.user, OperationLog.TYPE_OP_LOG, json.dumps(retjson, ensure_ascii=False), get_operator(request))
    return HttpResponse(json.dumps(retjson, ensure_ascii=False))


def _get_host_exist_restore_task(hostIdent):
    migration_tasks_exists = MigrateTask.objects.filter(source_host__ident=hostIdent,
                                                        host_snapshot__isnull=False).filter(
        finish_datetime__isnull=True)
    if migration_tasks_exists.exists():
        return migration_tasks_exists
    restore_tasks_exists = RestoreTask.objects.filter(host_snapshot__host__ident=hostIdent).filter(
        finish_datetime__isnull=True).filter(restore_target__htb_task__isnull=True)
    if restore_tasks_exists.exists():
        return restore_tasks_exists
    return list()


@xlogging.convert_exception_to_value(None)
def _get_nbd_device(restore_target_ident):
    p = psutil.process_iter()
    for r in p:
        if r.name().strip().lower() in ('gznbd',):
            cmdline = r.cmdline()
            if len(cmdline) > 6:
                if cmdline[0].lower() == '/sbin/aio/gznbd':
                    if cmdline[3].lower() == restore_target_ident.lower():
                        return cmdline[2]
    return None


@xlogging.convert_exception_to_value(None)
def _get_kvm_cmd_line(nbd_device):
    p = psutil.process_iter()
    for r in p:
        if r.name().strip().lower() in ('qemu-kvm', 'qemu-system-x86_64',):
            for line in r.cmdline():
                if nbd_device in line:
                    return r.cmdline()

    return None


@xlogging.convert_exception_to_value(None)
def _get_vnc_port(restore_target_ident):
    nbd_device = _get_nbd_device(restore_target_ident)
    if nbd_device:
        kvm_cmd_line = _get_kvm_cmd_line(nbd_device)
        if kvm_cmd_line:
            i = 0
            for line in kvm_cmd_line:
                i = i + 1
                if line == '-vnc':
                    return int(kvm_cmd_line[i].split(':')[1])
    return None


def _getkvminfo(host_name, task):
    restore_target = task.restore_target
    display_status = restore_target.display_status
    restore_target_info = json.loads(restore_target.info)
    if 'remote_ip' in restore_target_info:
        remote_ip = '{}'.format(restore_target_info['remote_ip'])
    if 'master_nic_ips' in restore_target_info:
        remote_ip = '{}'.format('|'.join(restore_target_info['master_nic_ips']))
    vnc_port = _get_vnc_port(task.restore_target.ident)
    if not vnc_port:
        vnc_port = '-'
    start_time = task.start_datetime.strftime('%Y-%m-%d %H:%M:%S')
    return [host_name, start_time, remote_ip, display_status, vnc_port]


def restore_kvm_list(request):
    kvmlist = list()
    hosts = Host.objects.filter()
    hosts = list(filter(lambda x: not x.is_deleted, hosts))
    for host in hosts:
        tasks = _get_host_exist_restore_task(host.ident)
        for task in tasks:
            kvmlist.append(_getkvminfo(host.name, task))

    retjson = {'r': 0, 'page': 1, 'total': 1, 'records': len(kvmlist), 'rows': kvmlist}
    return HttpResponse(json.dumps(retjson, ensure_ascii=False))


kvm_debug_cfg_file = '/dev/shm/kvm_serial'


def save_debug_kvm(request):
    splash_time = int(request.POST.get('splash_time'))
    if splash_time <= 0:
        splash_time = None
    with open(kvm_debug_cfg_file, 'w') as fout:
        debug_info = {"splash_time": splash_time}
        fout.write(json.dumps(debug_info, ensure_ascii=False))
    return HttpResponse(json.dumps({"r": 0, "e": ""}, ensure_ascii=False))


def get_debug_kvm_cfg(request):
    splash_time = 5
    if os.path.isfile(kvm_debug_cfg_file):
        kvm_debug_status = '调试文件已存在，请调试完成后点击“清除设置”按钮'
        kvm_serial_status = '已启用'
        try:
            with open(kvm_debug_cfg_file, 'r') as fout:
                kvm_cfg = json.loads(fout.read())
                splash_time = kvm_cfg.get('splash_time', '')
        except:
            splash_time = 0
    else:
        kvm_debug_status = '调试文件不存在'
        kvm_serial_status = '未启用'
    debug_info = {"splash_time": splash_time}
    debug_info['r'] = 0
    debug_info['kvm_debug_status'] = kvm_debug_status
    debug_info['kvm_serial_status'] = kvm_serial_status
    return HttpResponse(json.dumps(debug_info, ensure_ascii=False))


def clear_debug_kvm(request):
    if os.path.isfile(kvm_debug_cfg_file):
        os.remove(kvm_debug_cfg_file)
    return HttpResponse(json.dumps({'r': 0}, ensure_ascii=False))


def get_pe_default_path(request):
    debug_info = {}
    if not os.path.isfile(external_pe_file):
        debug_info['pe_path'] = pe_path
        debug_info['debug_file'] = '调试文件不存在'
    else:
        with open(external_pe_file, 'r') as f:
            result = json.loads(f.read())
        debug_info['pe_path'] = result['pe_path']
        debug_info['debug_file'] = '调试文件已存在，请调试完成后点击“禁用”按钮'
    debug_info['r'] = 0
    return HttpResponse(json.dumps(debug_info, ensure_ascii=False))


def enable_pe_path(request):
    pe_path = request.POST.get('pe_path')
    _logger.info('pe_path:{}'.format(pe_path))
    pe_path_file = {'pe_path': pe_path}
    if not os.path.isfile(external_pe_file):
        os.system(r'cd /dev/shm/;touch external_pe_file.json')
        with open(external_pe_file, 'w') as f:
            f.write(json.dumps(pe_path_file, ensure_ascii=False))
    else:
        with open(external_pe_file, 'w') as f:
            f.truncate()
            f.write(json.dumps(pe_path_file, ensure_ascii=False))
    return HttpResponse(json.dumps({"r": 0, "e": ""}, ensure_ascii=False))


def disable_pe_path(request):
    if os.path.isfile(external_pe_file):
        os.remove(external_pe_file)
    return HttpResponse(json.dumps({'r': 0}, ensure_ascii=False))


def debuginfo_handle(request):
    a = request.GET.get('a', None)
    a = request.POST.get('a', None) if a is None else a
    if a == 'disk_snapshots_paths':
        return query_disksnapshot_path_by_hostsnapshot(request)
    elif a == 'disk_snapshot_chain':
        return query_disksnapshot_chain(request)
    elif a == 'decode_pwd':
        return decode_pwd(request)
    elif a == 'restore_kvm_list':
        return restore_kvm_list(request)
    elif a == 'save_debug_kvm':
        return save_debug_kvm(request)
    elif a == 'clear_debug_kvm':
        return clear_debug_kvm(request)
    elif a == 'get_debug_kvm_cfg':
        return get_debug_kvm_cfg(request)
    elif a == 'get_pe_default_path':
        return get_pe_default_path(request)
    elif a == 'enable_pe_path':
        return enable_pe_path(request)
    elif a == 'disable_pe_path':
        return disable_pe_path(request)
    else:
        return HttpResponse(json.dumps({"r": "1", "e": "没有对应的处理函数:{}".format(html.escape(a))}))
