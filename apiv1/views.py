import base64
import copy
import json
import os
import queue
import random
import re
import subprocess
import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta
from itertools import chain
from threading import Timer

import django.utils.timezone as timezone
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apiv1 import tasks
from apiv1.cluster_backup_task import ClusterBackupTaskExecutor
from apiv1.cluster_cdp_backup_task import ClusterCdpTaskExecutor, AgentValidDiskSnapshotInfo
from apiv1.hostshare import addHostShareMonitor, delHostShareMonitor, getDiskInfo, iceCmdInit, iceGetCmd, \
    operating_host_dict, LockDiskFilesOper
from apiv1.logic_processors import CreateHostLogicProcessor, BackupTaskScheduleLogicProcessor, \
    BackupTaskScheduleExecuteLogicProcessor, HostSnapshotRestoreLogicProcessor, HostSessionMigrateLogicProcessor, \
    query_system_info, HostSnapshotRestoreVolumeLogicProcessor
from apiv1.main import host_session_logic_processor
from apiv1.models import ClusterTokenMapper
from apiv1.models import Host, RestoreTarget, HostSnapshot, DiskSnapshot, CDPDiskToken, StorageNode, \
    RestoreTargetDisk, BackupTaskSchedule, BackupTask, MigrateTask, RestoreTask, ExternalStorageDeviceConnection, \
    HostSnapshotShare, UserQuota, Tunnel, CDPTask, HostLog, HTBSchedule, ClusterBackupSchedule, ClusterBackupTask, \
    HTBTask, RemoteBackupTask, TakeOverKVM, ArchiveTask, FileBackupTask
from apiv1.restore import RestoreTargetChecker, PeRestore, is_restore_target_belong_htb
from apiv1.serializers import (HostCreateSerializer, HostSerializer, HostLoginSerializer, HostSessionSerializer,
                               AgentModuleErrorSerializer, PeHostSessionSerializer, HostSessionBackupProgressSerializer,
                               HostSnapshotSerializer, HostSnapshotRestoreSerializer, StorageNodeSerializer,
                               PeHostSessionLoginSerializer, PeHostSessionDetailSerializer, HostInfoAlterSerializer,
                               HostSessionDiskSerializer,
                               BackupTaskScheduleSerializer, BackupTaskScheduleCreateSerializer,
                               BackupTaskScheduleUpdateSerializer,
                               StorageNodePerDeviceSerializer, AddStorageNodeSerializer,
                               AddExternalStorageDeviceSerializer,
                               AlterStorageNodeInfo, HostSnapshotShareSerializer, HostSessionMigrateSerializer,
                               BackupTaskScheduleExecuteSerializer, ClusterBackupTaskScheduleSerializer,
                               PeHostSessionVolumeRestoreSerializer, HostSnapshotLocalRestoreSerializer,
                               HostMoreInfoInputSerializer, DealStorageNode)
from apiv1.signals import end_sleep
from apiv1.snapshot import GetSnapshotList, Tokens, GetDiskSnapshot
from apiv1.spaceCollection import SpaceCollectionWorker, CDPHostSnapshotSpaceCollectionMergeTask
from apiv1.storage_nodes import StorageNodeLogic, UserQuotaTools
from apiv1.tasks import BackupScheduleRetryHandle
from apiv1.work_processors import HostBackupWorkProcessors
from box_dashboard import xlogging, xdatetime, boxService, xdata, pyconv, functions
from xdashboard.common.dict import GetDictionary
from xdashboard.models import UserProfile, DriverBlackList, DataDictionary, ForceInstallDriver
from xdashboard.models import sshkvm

_logger = xlogging.getLogger(__name__)

import CProxy
import KTService
import BoxLogic

HARDWARE_BLACKLIST_FILE = '/sbin/aio/box_dashboard/hardware_blacklist.json'


def get_disk_snapshot_from_info(path, snapshot):
    if DiskSnapshot.is_cdp_file(path):
        return DiskSnapshot.objects.get(image_path=path)
    else:
        return DiskSnapshot.objects.get(image_path=path, ident=snapshot)


def get_ex_vols(or_ex_vol_info):
    rs = list()
    vol_name2_info_maps = {}
    for vol in json.loads(or_ex_vol_info):
        if vol['VolumeName'] in vol_name2_info_maps:
            vol_name2_info_maps[vol['VolumeName']]['ranges'].append(vol['ranges'])
        else:
            vol_name2_info_maps[vol['VolumeName']] = {"display_name": vol['display_name'], "ranges": [vol['ranges']]}
    for _, value in vol_name2_info_maps.items():
        rs.append(value)
    return rs


def check_host_ident_valid(ident):
    try:
        return Host.objects.get(ident=ident)
    except Host.DoesNotExist:
        xlogging.raise_and_logging_error('不存在的客户端标识符:{}'.format(ident), 'invalid host ident:{}'.format(ident),
                                         status.HTTP_404_NOT_FOUND)


def _check_host_session_valid(ident):
    host = check_host_ident_valid(ident)
    if not host.is_linked:
        xlogging.raise_and_logging_error('客户端{}为离线状态'.format(host.display_name), 'host not link:{}'.format(ident),
                                         status.HTTP_404_NOT_FOUND)
    return host


def _check_pe_host_valid(ident):
    try:
        return RestoreTarget.objects.get(ident=ident)
    except RestoreTarget.DoesNotExist:
        xlogging.raise_and_logging_error('不存在的还原目标客户端标识符:{}\n请检查目标机是否在线'.format(ident),
                                         'invalid pe host ident:{}'.format(ident),
                                         status.HTTP_404_NOT_FOUND)


def _check_pe_host_session(ident):
    pe_host = _check_pe_host_valid(ident)
    if pe_host.start_datetime is not None:  # 非None表示已经发送过命令
        xlogging.raise_and_logging_error('还原目标客户端已经发送过命令', 'pe host busy ident:{}'.format(ident),
                                         status.HTTP_404_NOT_FOUND)
    return pe_host


def _check_pe_host_running(ident):
    pe_host = _check_pe_host_valid(ident)
    if pe_host.start_datetime is None:  # None表示未发送过命令
        xlogging.raise_and_logging_error('还原目标客户端未发送过命令', 'pe host not begin run ident:{}'.format(ident),
                                         status.HTTP_404_NOT_FOUND)
    if pe_host.finish_datetime is not None:  # 非None表示已经结束
        xlogging.raise_and_logging_error('还原目标客户端已经结束任务', 'pe host run finish ident:{}'.format(ident),
                                         status.HTTP_404_NOT_FOUND)
    return pe_host


def _check_cluster_backcup_task_schedule_valid(schedule_id):
    try:
        return ClusterBackupSchedule.objects.get(id=schedule_id)
    except BackupTaskSchedule.DoesNotExist:
        xlogging.raise_and_logging_error('不存在的备份计划:{}'.format(schedule_id),
                                         'invalid BackupTaskSchedule:{}'.format(schedule_id),
                                         status.HTTP_404_NOT_FOUND)


def _check_backcup_task_schedule_valid(backup_task_schedule_id):
    try:
        return BackupTaskSchedule.objects.get(id=backup_task_schedule_id)
    except BackupTaskSchedule.DoesNotExist:
        xlogging.raise_and_logging_error('不存在的备份计划:{}'.format(backup_task_schedule_id),
                                         'invalid BackupTaskSchedule:{}'.format(backup_task_schedule_id),
                                         status.HTTP_404_NOT_FOUND)


def _check_cluster_schedule_valid(schedule_id):
    try:
        return ClusterBackupSchedule.objects.get(id=schedule_id)
    except ClusterBackupSchedule.DoesNotExist:
        xlogging.raise_and_logging_error('不存在的集群备份计划:{}'.format(schedule_id),
                                         'invalid ClusterBackupSchedule:{}'.format(schedule_id),
                                         status.HTTP_404_NOT_FOUND)


def _check_host_snapshot_id_valid(host_snapshot_id):
    try:
        return HostSnapshot.objects.get(id=host_snapshot_id)
    except HostSnapshot.DoesNotExist:
        xlogging.raise_and_logging_error('不存在的客户端快照:{}'.format(host_snapshot_id),
                                         'invalid host snapshot id {}'.format(host_snapshot_id),
                                         status.HTTP_404_NOT_FOUND)


def _check_host_snapshot_object_valid(host_snapshot_object, restore_time):
    if host_snapshot_object.deleting or host_snapshot_object.deleted:
        xlogging.raise_and_logging_error(
            r'客户端快照不可用，已进入空间回收流程', 'host_snapshot_object invalid {}'.format(host_snapshot_object.id))

    if host_snapshot_object.is_cdp:
        if restore_time is None:
            xlogging.raise_and_logging_error(
                r'参数无效，没有指定时间', 'restore_time invalid {} {}'.format(host_snapshot_object.id, restore_time))

        if restore_time < host_snapshot_object.cdp_info.first_datetime:
            xlogging.raise_and_logging_error(
                r'指定的客户端快照时间不可用，已进入空间回收流程', 'restore_time invalid {} {}'
                    .format(host_snapshot_object.id, restore_time))
    else:
        if restore_time is not None:
            xlogging.raise_and_logging_error(
                r'指定的客户端快照不可用，已进入空间回收流程', 'not cdp , can NOT pass restore_time {} {}'
                    .format(host_snapshot_object.id, restore_time))


def _check_using_storage_node_id_valid(node_id):
    try:
        return StorageNode.objects.get(id=node_id, deleted=False)
    except StorageNode.DoesNotExist:
        xlogging.raise_and_logging_error('不存在的存储节点:{}'.format(node_id), 'invalid storage node id {}'.format(node_id),
                                         status.HTTP_404_NOT_FOUND)


def _check_disk_snapshot_exist(host_snapshot_id):
    mdisk_snapshots = DiskSnapshot.objects.filter(host_snapshot=host_snapshot_id)
    if len(mdisk_snapshots) == 0:
        xlogging.raise_and_logging_error('不存在的客户端快照:{}'.format(host_snapshot_id),
                                         'invalid host snapshot id'.format(host_snapshot_id), status.HTTP_404_NOT_FOUND)
    return mdisk_snapshots


def _check_host_share_exist(host_snapshot_id, timestamp):
    try:
        return HostSnapshotShare.objects.get(host_snapshot_id=host_snapshot_id, host_start_time=timestamp)
    except HostSnapshotShare.DoesNotExist:
        return None


def _check_cdp_token_string_valid(token_string):
    try:
        return CDPDiskToken.objects.get(token=token_string)
    except CDPDiskToken.DoesNotExist:
        xlogging.raise_and_logging_error(
            '不存在的CDP标识符:{}'.format(token_string), 'invalid cdp token:{}'.format(token_string),
            status.HTTP_404_NOT_FOUND)


def _check_path_and_remove_file(path):
    if not os.path.exists(os.path.dirname(path)):
        os.mkdir(os.path.dirname(path))
    if os.path.exists(path):
        os.remove(path)


def _get_aio_logs():
    _truncate_core_dumps()
    _create_7z_file()


def _truncate_core_dumps():
    all_file = os.listdir(xdata.CORE_DUMPS_PATH)
    file_items_key = dict()
    for onefile in all_file:
        file_items = onefile.split('-')
        keys = file_items[1] + file_items[2]
        times = int(file_items[4])
        if keys not in file_items_key:
            file_items_key[keys] = [(onefile, times)]
        else:
            if len(file_items_key[keys]) == 2:
                minobj = min(file_items_key[keys], key=lambda x: x[1])
                if times > minobj[1]:
                    index = file_items_key[keys].index(minobj)
                    file_items_key[keys].pop(index)
                    file_items_key[keys].append((onefile, times))
            else:
                file_items_key[keys].append((onefile, times))

    be_save_file = list()
    for i in file_items_key.values():
        for j in i:
            be_save_file.append(j[0])

    for onefile in all_file:
        if onefile not in be_save_file:
            os.remove(os.path.join(xdata.CORE_DUMPS_PATH, onefile))


def _create_7z_file():
    _check_path_and_remove_file(xdata.ALL_DEBUG_MESSAGE_PATH)

    source_path = xdata.CORE_DUMPS_PATH + ' ' + xdata.AIO_LOG_PATH + ' ' + xdata.AIO_VERSION_PATH
    target_path = xdata.ALL_DEBUG_MESSAGE_PATH + ' '
    commend = 'tar -jc -f ' + target_path + source_path

    with subprocess.Popen(commend, shell=True, stderr=subprocess.PIPE, universal_newlines=True) as p:
        _logger.info(r'_create_7z_file ,CMD:{0},PID:{1}'.format(commend, p.pid))
        stdout, stderr = p.communicate()
    if p.returncode > 1:
        raise Exception('tar exit code:{},errmesg:{}'.format(p.returncode, stderr))


def _check_cdp_host_is_idle(scheduleobj):
    host_stat = boxService.box_service.GetStatus(scheduleobj.host.ident)
    _logger.info('{}'.format(json.dumps(host_stat)))
    if 'off_line' in host_stat:
        xlogging.raise_and_logging_error('客户端处于离线状态', 'execute schedul fail ,host is off_line',
                                         status.HTTP_429_TOO_MANY_REQUESTS)
    if 'idle' not in host_stat:
        xlogging.raise_and_logging_error('客户端正在执行其它任务，立即执行CDP计划失败', 'execute schedul fail ,host is not idle',
                                         status.HTTP_429_TOO_MANY_REQUESTS)


def _check_host_exist_backupTaskSchedule(host):
    if BackupTaskSchedule.objects.filter(deleted=False, host=host).exists():
        xlogging.raise_and_logging_error(
            '删除失败，客户端：[{}]存在备份计划，不能删除。'
            '请在“备份”－“备份计划管理”页面,删除备份计划后再做删除。'.format(host.name),
            'exists backupTaskSchedule', status.HTTP_406_NOT_ACCEPTABLE)


def _check_host_exist_mount_point(host):
    hostsnapts = host.snapshots.all()
    for hostsnapt in hostsnapts:
        if HostSnapshotShare.objects.filter(host_snapshot_id=hostsnapt.id).exclude(share_status='init'):
            xlogging.raise_and_logging_error(
                '删除失败，客户端：[{}]的备份点正在被浏览，不能删除。'
                '请在“恢复”－“验证”页面，将此客户端正在被浏览的备份点释放后再做删除。'.format(host.name),
                'exists share point', status.HTTP_406_NOT_ACCEPTABLE)


def _check_host_exist_restore_task(host):
    if host.restores.filter(finish_datetime__isnull=True, successful=False).exists():
        xlogging.raise_and_logging_error(
            '删除失败，客户端：[{}]存在还原任务，不能删除。'
            '请在"系统状态"-"任务执行状态"页面，将此客户端正在执行的还原任务取消后再做删除。'.format(
                host.name), 'exists share point', status.HTTP_406_NOT_ACCEPTABLE)

    if host.migrate_destinations.filter(finish_datetime__isnull=True, successful=False).exists():
        xlogging.raise_and_logging_error(
            '删除失败，客户端：[{}]存在迁移任务，不能删除。'
            '请在"系统状态"-"任务执行状态"页面，将此客户端正在执行的迁移任务取消后再做删除。'.format(
                host.name), 'exists share point', status.HTTP_406_NOT_ACCEPTABLE)


def _check_host_exist_cluster_plan(host):
    if host.cluster_backup_schedules.filter(deleted=False).exists():
        xlogging.raise_and_logging_error(
            '删除失败，客户端：[{}]存在集群备份计划，不能删除。'
            '请在“备份”－“集群备份计划管理”页面,删除集群备份计划后再做删除。'.format(host.name),
            'exists ClusterBackupSchedule', status.HTTP_406_NOT_ACCEPTABLE)


def _check_host_exist_hotback_plan(host):
    if host.htb_schedule.filter(deleted=False).exists():
        xlogging.raise_and_logging_error(
            '删除失败，客户端：[{}]存在热备计划，不能删除。'
            '请在“热备”－“热备计划管理”页面,删除热备计划后再做删除。'.format(host.name),
            'exists HTBSchedule', status.HTTP_406_NOT_ACCEPTABLE)


def _check_host_exist_remote_back_plan(host):
    if host.remote_backup_schedules.filter(deleted=False).exists():
        xlogging.raise_and_logging_error(
            '删除失败，客户端：[{}]存在远程容灾计划，不能删除。'
            '请在“远程容灾”－“远程容灾计划管理”页面,删除远程灾备计划后再做删除。'.format(host.name),
            'exists HTBSchedule', status.HTTP_406_NOT_ACCEPTABLE)


def _check_host_exist_audit_task(host):
    from xdashboard.handle.audittask import get_approved_task_host_snapshot_id
    from xdashboard.models import audit_task
    audit_tasks = audit_task.objects.filter(status=audit_task.AUIDT_TASK_STATUS_WAITE)
    for task in audit_tasks:
        task_type_display, host_snapshot_id = get_approved_task_host_snapshot_id(json.loads(task.task_info))
        if host_snapshot_id:
            hostsnapts = host.snapshots.all()
            for hostsnapt in hostsnapts:
                if hostsnapt.id == int(host_snapshot_id):
                    xlogging.raise_and_logging_error(
                        '删除失败，客户端：[{}]存在待审批的任务。'
                        '请在“任务执行状态”中取消待审批的任务。'.format(host.name),
                        'exists verify task', status.HTTP_406_NOT_ACCEPTABLE)


def delete_host(host):
    host.set_delete()
    # host.logs.all().delete()


def query_host_routers_info(host_ident):
    input_argument = json.dumps({"GetAgHostRouteInfo": 1})
    try:
        route_info_or = boxService.box_service.getHostHardwareInfo(host_ident, input_argument)
    except Exception as e:
        _logger.error("get host route info error ,input {},{}".format(host_ident, input_argument))
        return []
    route_info_or = json.loads(route_info_or)
    if not route_info_or:
        return []
    route_info_or = route_info_or["RouteInfo"]
    return _get_route_info_from_dict_to_list(route_info_or)


def _get_route_info_from_dict_to_list(route_infos_origin):
    r_list = list()
    _logger.debug(type(route_infos_origin))
    _logger.debug(route_infos_origin)
    if not route_infos_origin:
        _logger.error("get empty route info :{}".format(route_infos_origin))
        return r_list
    else:
        for router in route_infos_origin:
            _logger.debug(type(router))
            _logger.debug(router)
            if router["dwForwardMetric1"] and router["szDestIp"] and router["szGatewayIp"] and router[
                "szMaskIp"]:
                r_list.append(
                    [router["szDestIp"], router["szMaskIp"], router["szGatewayIp"], router["dwForwardMetric1"]])
        return r_list


def from_mac_get_adapter(mac, net_adapters):
    for net_adapter in net_adapters:
        if xdata.is_two_mac_addr_equal(mac, net_adapter['szMacAddress']):
            return net_adapter
    return None


@xlogging.convert_exception_to_value(False)
def driver_in_blacklist(HWIds_0, hardwares, sys_os_type, driver_id):
    for hardware in hardwares:
        if HWIds_0 in hardware.HWIds:
            break
    else:
        xlogging.raise_and_logging_error('HWIds_0 is Error', 'HWIds_0: {}, hardwares: {}'.format(HWIds_0, hardwares))

    for db_obj in DriverBlackList.objects.filter(sys_type=sys_os_type, driver_id=driver_id):
        if db_obj.device_id in ','.join(hardware.HWIds):
            return True
    return False


def check_driver_in_blacklist(device, sys_os_type, driver_id):
    for db_obj in DriverBlackList.objects.filter(sys_type=sys_os_type, driver_id=driver_id):
        if db_obj.device_id in ','.join(device['HWIds']):
            return True
    return False


def unselect_drivers_according_to_db_blacklist(hardwares, sys_os_type, all_drivers):
    for HWIds_0, drivers in all_drivers.items():
        for driver in drivers:
            driver['UserSelected'] = int(not driver_in_blacklist(HWIds_0, hardwares, sys_os_type, driver['zip_path']))


def unselect_all_drivers_when_restore_to_self(all_drivers, restore_to_self):
    if restore_to_self:
        for HWIds_0, drivers in all_drivers.items():
            for driver in drivers:
                driver['UserSelected'] = 0
    else:
        return


def update_db_blacklist(sys_os_type, drivers):
    all_drivers = []
    for one_device_drivers in drivers.values():
        all_drivers += one_device_drivers
    for one_driver in all_drivers:
        device_id, driver_id, sys_type = one_driver['hard_or_comp_id'], one_driver['zip_path'], sys_os_type
        if one_driver['UserSelected']:
            DriverBlackList.objects.filter(device_id=device_id, driver_id=driver_id, sys_type=sys_type).delete()
        else:
            DriverBlackList.objects.get_or_create(device_id=device_id, driver_id=driver_id, sys_type=sys_type)


class HostInfo(APIView):
    serializer_class = HostSerializer
    queryset = Host.objects.all()

    def __init__(self, **kwargs):
        super(HostInfo, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator(self, _logger).decorate()

    def get(self, request, ident):
        host = check_host_ident_valid(ident)
        serializer = self.serializer_class(host)
        return Response(serializer.data)

    def put(self, request, ident):
        host = check_host_ident_valid(ident)
        serializer = HostInfoAlterSerializer(host, data=request.data)
        serializer.is_valid(True)

        update_fields = list()

        if 'display_name' in serializer.validated_data.keys():
            host.display_name = serializer.validated_data['display_name']
            update_fields.append('display_name')
        if 'user_id' in serializer.validated_data.keys():
            host.user_id = serializer.validated_data['user_id']
            update_fields.append('user_id')
        if 'network_transmission_type' in serializer.validated_data.keys():
            host.network_transmission_type = serializer.validated_data['network_transmission_type']
            update_fields.append('network_transmission_type')

        ext_info_obj = json.loads(host.ext_info)
        ext_info_obj['orgname'] = serializer.validated_data.get('orgname')
        ext_info_obj['systemname'] = serializer.validated_data.get('systemname')
        host.ext_info = json.dumps(ext_info_obj, ensure_ascii=False)
        update_fields.append('ext_info')

        host.save(update_fields=update_fields)

        return Response(HostSerializer(host).data, status=status.HTTP_202_ACCEPTED)


class Hosts(APIView):
    serializer_class = HostSerializer

    def __init__(self, **kwargs):
        super(Hosts, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    @staticmethod
    def get_queryset():
        return Host.objects

    def get(self, request):
        current_user = None if request is None else request.user
        if current_user.is_superuser:
            hosts = self.get_queryset().all()
        else:
            hosts = self.get_queryset().filter(user_id=current_user.id).all()
        serializer = self.serializer_class(hosts, many=True)
        return Response(serializer.data)

    @staticmethod
    def _is_force_allocate_user(host):
        if host.user:
            return False

        return boxService.get_always_allocate_user_switch()

    @staticmethod
    @xlogging.convert_exception_to_value(False)
    def is_need_to_update_host_user(host, report_info):
        cur_sysinfo = json.loads(report_info['sysinfo'])
        cur_timestamp = cur_sysinfo['ClientTimestamp']

        db_sysinfo = json.loads(host.ext_info)['system_infos']
        last_timestamp = db_sysinfo['ClientTimestamp']

        return (cur_timestamp != last_timestamp) or Hosts._is_force_allocate_user(host)

    @staticmethod
    def _is_nas_host(host_obj):
        _ext_info = json.loads(host_obj.ext_info)
        return host_obj.type == Host.NAS_AGENT or 'nas_path' in _ext_info

    def post(self, request):
        serializer = HostCreateSerializer(data=request.data)
        serializer.is_valid(True)
        logic = CreateHostLogicProcessor(serializer.validated_data)
        host_ident = logic.run()
        host = Host.objects.get(ident=host_ident)

        report_info = serializer.validated_data
        if self.is_need_to_update_host_user(host, report_info):
            host.user = logic._getUser(report_info['user_ident'])

        _ext_info = json.loads(host.ext_info)
        _ext_info['system_infos'] = json.loads(serializer.data['sysinfo'])

        # 是否是 验证过的主机
        if serializer.validated_data['user_ident'] == xdata.UUID_VALIADE_HOST:
            _ext_info['is_valiade_host'] = True
            host.user = None
        else:
            _ext_info['is_valiade_host'] = False

        _ext_info['is_deleted'] = False

        host.ext_info = json.dumps(_ext_info)
        host.save(update_fields=['ext_info', 'user'])

        _logger.info('Hosts post host:{}, ext_info:{}'.format(host, host.ext_info))

        serializer = HostSerializer(host)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def delete(self, request, ident):
        host = check_host_ident_valid(ident)

        if host.type in [Host.AGENT, Host.PROXY_AGENT] and host.is_linked:  # 普通客户端及免代理客户端需要在离线状态下删除
            # 需要先禁用免代理客户端，使其进程退出
            if hasattr(host, 'vm_session'):
                if host.vm_session.enable:
                    return Response('删除失败，请先禁客户端', status=status.HTTP_406_NOT_ACCEPTABLE)
                else:
                    return Response('删除失败，客户端正在释放资源，请稍候再试', status=status.HTTP_406_NOT_ACCEPTABLE)
            return Response(data="删除失败，客户端：[{}]在线。".format(host.name), status=status.HTTP_400_BAD_REQUEST)
        _check_host_exist_backupTaskSchedule(host)
        _check_host_exist_mount_point(host)
        _check_host_exist_restore_task(host)
        _check_host_exist_cluster_plan(host)
        _check_host_exist_hotback_plan(host)
        _check_host_exist_remote_back_plan(host)
        _check_host_exist_audit_task(host)

        if request.user.is_superuser and host.user:
            xlogging.raise_and_logging_error('删除失败，客户端：[{}]存在分配的用户。'.format(host.name), 'exists user use',
                                             status.HTTP_406_NOT_ACCEPTABLE)
        # 免代理客户端的删除
        if hasattr(host, 'vm_session'):
            from apiv1.vmware_logic import VirtualHostSession
            rsp = VirtualHostSession().delete(request, {'id': host.vm_session.id})
            if not status.is_success(rsp.status_code):
                return rsp
        host.user = None
        host.save(update_fields=['user'])
        host.set_delete()
        return Response(data={"name": host.name, "msg": "删除成功，客户端：[{}]。".format(host.name)},
                        status=status.HTTP_202_ACCEPTED)


class HostSessions(APIView):
    serializer_class = HostSerializer
    queryset = Host.objects.select_related('vm_session').all()

    def __init__(self, **kwargs):
        super(HostSessions, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    @staticmethod
    def del_host_other_tunnel(current_host, current_tunnel):
        try:
            other_tunnel = current_host.tunnel
        except Tunnel.DoesNotExist:
            _logger.info('tunnel host(id={}) first login'.format(current_host.id))
            return

        if other_tunnel.id != current_tunnel.id:
            other_tunnel.delete()
            _logger.warning(
                'delete host other tunnel when host login in tunnel model, host={0}, old_tunnel={1}, new_tunnel={2}'.format(
                    current_host.ident, other_tunnel.id, current_tunnel.id))

    @staticmethod
    def _update_tunnel_and_host(tu_id, host_ident):
        host = Host.objects.get(ident=host_ident)
        tunnel = Tunnel.objects.get(id=tu_id)

        # update host "ext_info" Field
        _ext_info = json.loads(host.ext_info)
        _ext_info['tunnel_index'] = tu_id
        host.ext_info = json.dumps(_ext_info)
        host.save(update_fields=['ext_info'])

        # update tunnel "host" Field
        HostSessions.del_host_other_tunnel(host, tunnel)
        tunnel.set_host(host)

    @staticmethod
    def filter_verified(host):
        """
            过滤掉用于验证主机
        """
        return host.is_verified

    @staticmethod
    def filter_deleted(host):
        """
            过滤掉删除的主机
        """
        return host.is_deleted

    @staticmethod
    def filter_nas_host(host):
        """
        过滤掉nas主机
        """
        return host.is_nas_host

    @staticmethod
    def filter_archive(host):
        """
        过滤掉数据导入主机
        """
        return host.type == Host.ARCHIVE_AGENT

    @staticmethod
    def filter_in_group(host):
        """
        过滤掉组中的主机
        """
        return host.groups.exists()

    @staticmethod
    def filter_no_plan(host):
        """
        过滤没有计划的主机
        """
        return host.backup_task_schedules.filter(deleted=False)

    @staticmethod
    def filter_offline(host):
        return not host.is_linked

    @staticmethod
    def filter_not_remote(host):
        return not host.is_remote

    @staticmethod
    def filter_remote(host):
        return host.is_remote

    @staticmethod
    def serializer_host(host, attr_getters=None):
        attr_getters = list() if attr_getters is None else attr_getters
        fields = (
            'id', 'name', 'ident', 'is_linked', 'network_transmission_type', 'aio_info', 'type', 'is_nas_host')
        rs = {field: getattr(host, field) for field in fields}
        for name, getter in attr_getters:
            rs[name] = getter(host)
        return rs

    def get(self, request, api_request=None, filter_funcs=None, attr_getters=None, check_exists=False):
        filter_funcs = list() if filter_funcs is None else filter_funcs
        attr_getters = list() if attr_getters is None else attr_getters
        current_user = None if request is None else request.user
        filter_content = request.GET.get('filter_content', None) if request else None
        if current_user.is_superuser:
            hosts = self.queryset.all()
        else:
            hosts = self.queryset.filter(user_id=current_user.id).all()

        if api_request and 'type' in api_request:
            hosts = hosts.filter(type__in=api_request['type'])

        if filter_content:
            hosts = hosts.filter(Q(display_name__contains=filter_content) | Q(last_ip__contains=filter_content))

        res = list()
        is_exists = False
        for host in hosts:
            if any([func(host) for func in filter_funcs]):  # 符合任意一个过滤条件，就不要
                continue
            else:
                res.append(HostSessions.serializer_host(host, attr_getters))

            if check_exists:  # 如果仅仅是检测有没有结果，那么只需要有就可以
                is_exists = True
                break
        if check_exists:
            return Response({'exists': is_exists})
        else:
            return Response(res)

    def post(self, request):
        host_login = HostLoginSerializer(data=request.data)
        host_login.is_valid(True)

        host_ident = host_login.validated_data['host_ident']
        host_ip = host_login.validated_data['host_ip']
        local_ip = host_login.validated_data['local_ip']
        tunnel_index = host_login.validated_data['tunnel_index']

        # 该host为隧道模式
        if tunnel_index > 0:
            self._update_tunnel_and_host(tunnel_index, host_ident)
            host_ip = Tunnel.objects.get(id=tunnel_index).host_ip

        host = check_host_ident_valid(host_ident)
        host_session_logic_processor().login(host_ident, host_ip, local_ip).set_delete(is_deleted=False)

        # 登陆之后更新下信息
        if host.type == Host.AGENT:
            t = Timer(2, self._update_htb_task, args=(host_ident,))
            t.start()

        return Response(status=status.HTTP_201_CREATED)

    @staticmethod
    def _update_htb_task(host_ident):
        result = ''
        counts = 5
        while counts > 0:
            result = boxService.box_service.get_host_ini_info(host_ident)
            if result == 'not support':
                return
            elif result == 'retry':
                counts -= 1
                time.sleep(2)
                continue
            else:
                break
        htb_task_uuid = result.get('restore', 'htb_task_uuid', fallback='')
        try:
            task = HTBTask.objects.get(task_uuid=htb_task_uuid)
        except HTBTask.DoesNotExist:
            pass
        else:
            if task.finish_datetime:
                pass  # do nothing
            else:
                _logger.info('_update_htb_task update task:{}'.format(task))
                ext_config = json.loads(task.ext_config)
                ext_config['is_login'] = True
                ext_config['host_ident'] = host_ident
                task.ext_config = json.dumps(ext_config)
                task.save(update_fields=['ext_config'])
        return None

    def delete(self, request):
        host_session_logic_processor().clear()
        return Response(status=status.HTTP_204_NO_CONTENT)


class HostSessionInfo(APIView):
    serializer_class = HostSessionSerializer
    queryset = Host.objects.none()

    def __init__(self, **kwargs):
        super(HostSessionInfo, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def get(self, request, ident, isupdate=False):
        """
        {
            'host': host,
            'computer_name': 'WIN-BP333I23SBQ',
            'os_type': 'Windows Xp + SP',
            'os_version': 'BuildNumber',
            'ether_adapters':
            [
                {'ip_addresses': ['172.16.6.107', '172.16.6.100'], 'mac': '00-0C-29-71-F3-71'},
                {'ip_addresses': ['172.16.6.118'], 'mac': '00-0C-29-71-F3-70'}
            ]
        }
        """
        host = check_host_ident_valid(ident)
        system_info = query_system_info(host, update=isupdate)
        if system_info is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        nics_info = system_info.get('Nic', list())
        host_ethers = [{'mac': nic['Mac'], 'ip_addresses': [ip['Ip'] for ip in nic['IpAndMask']]} for nic in nics_info]
        os_bit_str = '64位' if '64' in system_info['System']['ProcessorArch'] else '32位'

        host_session = {
            'host': Host.objects.get(ident=ident),
            'computer_name': system_info['System']['ComputerName'],
            'os_type': system_info['System']['SystemCaption'] + ' ' + system_info['System'][
                'ServicePack'] + ' ' + os_bit_str,
            'os_version': system_info['System'].get('BuildNumber', 'Unknown version'),
            'ether_adapters': host_ethers
        }
        return Response(self.serializer_class(host_session).data, status=status.HTTP_200_OK)

    def delete(self, request, ident):
        host_session_logic_processor().logout(ident)
        return Response(status=status.HTTP_204_NO_CONTENT)  # always successful

    def put(self, request, ident):
        check_host_ident_valid(ident)

        serializer = AgentModuleErrorSerializer(data=request.data)
        serializer.is_valid(True)

        host_session_logic_processor().report_agent_module_error_and_logout(ident, serializer.validated_data)

        return Response(status=status.HTTP_201_CREATED)

    def post(self, request, ident):
        _logger.info('request.data is {}'.format(request.data))
        serializer = HostMoreInfoInputSerializer(data=request.data)
        serializer.is_valid(True)
        if serializer.validated_data['type'] == xdata.QUERY_HOST_INFO_BACKUP:
            return self.query_backup_resources_params(ident)
        else:
            _logger.error('not support type:{}'.format(serializer.validated_data['type']))
            return Response(status=status.HTTP_406_NOT_ACCEPTABLE)

    @staticmethod
    def _current_backup_task_params(host_ident):
        task = MigrateTask.objects.filter(start_datetime__isnull=False,
                                          finish_datetime__isnull=True,
                                          source_host__ident=host_ident).first()
        if task:
            return json.loads(task.ext_config)

        task = BackupTask.objects.filter(start_datetime__isnull=False,
                                         finish_datetime__isnull=True,
                                         schedule__host__ident=host_ident).first()
        if task:
            return json.loads(task.schedule.ext_config)

        task = CDPTask.objects.filter(start_datetime__isnull=False,
                                      finish_datetime__isnull=True,
                                      schedule__host__ident=host_ident).first()
        if task:
            return json.loads(task.schedule.ext_config)

        task = ClusterBackupTask.objects.filter(start_datetime__isnull=False,
                                                finish_datetime__isnull=True,
                                                schedule__hosts__ident=host_ident).first()
        if task:
            return json.loads(task.schedule.ext_config)

        return dict()

    # 备份资源设置
    def query_backup_resources_params(self, host_ident):
        task_params = self._current_backup_task_params(host_ident)
        _logger.debug('query_backup_resources_params task_params:{}'.format(task_params))
        pre = int(task_params.get('BackupIOPercentage', '30')) * 0.01  # 工作占用百分比
        bk_io_cyc = int(GetDictionary(DataDictionary.DICT_TYPE_BACKUP_PARAMS, 'bk_io_cyc', 200))  # io 周期
        broad_band = int(task_params.get('maxBroadband', '300'))  # Mbit/s
        backup_broadband_kb_sec = -1 if broad_band == -1 else int(broad_band * 1024 / 8)  # Kbytes/s

        res = {
            'Agent.BackupIOTraCtrllWorkmillisec': int(bk_io_cyc * pre),
            'Agent.BackupIOTraCtrllIdlemillisec': bk_io_cyc - int(bk_io_cyc * pre),
            'Agent.BackupBroadbandKBSEC': backup_broadband_kb_sec
        }
        _logger.info('query_backup_resources_params host:{} res:{}'.format(host_ident, res))
        return Response(res)


class HostSessionDisks(APIView):
    serializer_class = HostSessionDiskSerializer
    queryset = Host.objects.none()

    def __init__(self, **kwargs):
        super(HostSessionDisks, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def get(self, request, ident, isupdate=False):
        """
         [
            {
                "index": 0,
                "name": "disk name",
                "boot_able": True,
                "bytes": 42949672960,
                "used_bytes": 0,
                "partition_table_type": "MBR"
            }
        ]
        """
        host = check_host_ident_valid(ident)
        system_info = query_system_info(host, update=isupdate)
        if system_info is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        disk_list = system_info['Disk']
        host_disks = list()
        # 遍历每一块磁盘, 磁盘情况: 1.未分配  2.Partition + 未分配  3.Partition(RAW/format)
        for disk in disk_list:
            if disk['DiskNum'] not in system_info['BootMap']:
                continue
            if not disk['Partition']:
                disk_total_bytes = int(disk['DiskSize'])
                disk_used_bytes = 0
            elif disk.get('dynamic_disk', False):
                disk_total_bytes = int(disk['DiskSize'])
                disk_used_bytes = 0
            else:
                disk_total_bytes = int(disk['DiskSize'])
                disk_used_bytes = 0
                for partition in disk['Partition']:
                    partition_used_bytes = int(partition['PartitionSize']) - int(partition['FreeSize'])
                    disk_used_bytes += partition_used_bytes  # Linux host，该字段无意义

            host_disks.append(
                {
                    'index': int(disk['DiskNum']),
                    'name': HostBackupWorkProcessors.get_disk_name(disk),
                    'boot_able': system_info['BootMap'][disk['DiskNum']],
                    'bytes': disk_total_bytes,
                    'used_bytes': disk_used_bytes,
                    'partition_table_type': disk['Style'],
                    'dynamic_disk': disk.get('dynamic_disk', False)
                }
            )
        return Response(self.serializer_class(instance=host_disks, many=True).data, status.HTTP_200_OK)

    @staticmethod
    def disk_label_for_human(disk, calc_used):
        """
        :param disk: one disk in system_info Disk
        :param calc_used: whether calc used
        :return:
        """
        disk_name = HostBackupWorkProcessors.get_disk_name(disk)
        if not disk['DiskSize']:
            disk['DiskSize'] = 0
            _logger.info('disk_label_for_human DiskSize is 0')
        total = functions.format_size(int(disk['DiskSize']))
        style = disk['Style'].upper()
        if (not calc_used) or disk.get('dynamic_disk', False):  # 动态盘不统计使用量
            return '{0}(空间:{1},{2})'.format(disk_name, total, style)
        else:
            if not disk['Partition']:
                disk_used_bytes = 0
            else:
                disk_used_bytes = 0
                for partition in disk['Partition']:
                    partition_used_bytes = int(partition['PartitionSize']) - int(partition['FreeSize'])
                    disk_used_bytes += partition_used_bytes
            used = functions.format_size(disk_used_bytes)
            return '{0}(空间:{1} 使用:{2},{3})'.format(disk_name, total, used, style)


class BackupTaskSchedules(APIView):
    serializer_class = BackupTaskScheduleSerializer
    queryset = BackupTaskSchedule.objects.filter(deleted=False)

    def __init__(self, **kwargs):
        super(BackupTaskSchedules, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def get(self, request):
        backup_source_type = int(request.GET.get('backup_source_type', BackupTaskSchedule.BACKUP_DISKS))
        current_user = None if request is None else request.user
        if current_user.is_superuser:
            schedules = self.queryset.all(backup_source_type=backup_source_type)
        else:
            schedules = self.queryset.filter(host__user_id=current_user.id, backup_source_type=backup_source_type).all()
        serializer = self.serializer_class(schedules, many=True)
        return Response(serializer.data)

    @staticmethod
    def _check_ext_config(logicProcessor, schedule_object):
        try:
            logicProcessor.check_ext_config()
        except Exception as e:
            schedule_object.delete()
            raise e

    def post(self, request, api_request=None):
        if api_request is None:
            api_request = request.data
        serializer = BackupTaskScheduleCreateSerializer(data=api_request)
        serializer.is_valid(True)
        schedule = BackupTaskSchedule.objects.create(**serializer.validated_data)
        logicProcessor = BackupTaskScheduleLogicProcessor(schedule)
        self._check_ext_config(logicProcessor, schedule)
        schedule.next_run_date = logicProcessor.calc_next_run(True)
        schedule.save(update_fields=['next_run_date'])
        return Response(self.serializer_class(schedule).data, status=status.HTTP_201_CREATED)


class BackupTaskScheduleSetting(APIView):
    queryset = BackupTaskSchedule.objects.none()
    serializer_class = BackupTaskScheduleSerializer

    def __init__(self, **kwargs):
        super(BackupTaskScheduleSetting, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def get(self, request, backup_task_schedule_id):
        schedule = _check_backcup_task_schedule_valid(backup_task_schedule_id)
        serializer = self.serializer_class(schedule)
        return Response(serializer.data)

    def put(self, request, backup_task_schedule_id, api_request=None):
        schedule = _check_backcup_task_schedule_valid(backup_task_schedule_id)

        serializer_old = self.serializer_class(schedule)
        data_old = serializer_old.data

        if api_request is None:
            api_request = request.data
        serializer = BackupTaskScheduleUpdateSerializer(data=api_request)
        serializer.is_valid(True)

        update_fields = list()

        if 'enabled' in serializer.validated_data:
            schedule.enabled = serializer.validated_data['enabled']
            update_fields.append('enabled')
        if 'name' in serializer.validated_data:
            schedule.name = serializer.validated_data['name']
            update_fields.append('name')
        if 'cycle_type' in serializer.validated_data:
            if (schedule.cycle_type != serializer.validated_data['cycle_type']) and (
                    (schedule.cycle_type == BackupTaskSchedule.CYCLE_CDP) or
                    (serializer.validated_data['cycle_type'] == BackupTaskSchedule.CYCLE_CDP)):
                xlogging.raise_and_logging_error('不支持切换CDP模式', 'can NOT alter cdp mode', status.HTTP_400_BAD_REQUEST)
            schedule.cycle_type = serializer.validated_data['cycle_type']
            update_fields.append('cycle_type')
        if 'plan_start_date' in serializer.validated_data:
            schedule.plan_start_date = serializer.validated_data['plan_start_date']
            update_fields.append('plan_start_date')
        if 'ext_config' in serializer.validated_data:
            schedule.ext_config = serializer.validated_data['ext_config']
            update_fields.append('ext_config')
        if 'storage_node_ident' in serializer.validated_data:
            schedule.storage_node_ident = serializer.validated_data['storage_node_ident']
            update_fields.append('storage_node_ident')

        logicProcessor = BackupTaskScheduleLogicProcessor(schedule)
        logicProcessor.check_ext_config()
        schedule.next_run_date = logicProcessor.calc_next_run(True)
        update_fields.append('next_run_date')

        schedule.save(update_fields=update_fields)

        if ('enabled' in update_fields) and (not schedule.enabled):
            end_sleep.send_robust(sender=BackupTaskSchedule, schedule_id=schedule.id)

        serializer_new = self.serializer_class(schedule)
        data_new = serializer_new.data

        if json.dumps(data_old, sort_keys=True) != json.dumps(data_new, sort_keys=True):
            _logger.info(r'alter schedule : {}'.format(
                json.dumps({'schedule_id': schedule.id, 'old': data_old, 'new': data_new}, ensure_ascii=False)))
        return Response(data_new, status=status.HTTP_202_ACCEPTED)

    @staticmethod
    @xlogging.convert_exception_to_value(None)
    def _get_schedule_or_None(backup_task_schedule_id):
        return _check_backcup_task_schedule_valid(backup_task_schedule_id)

    def delete(self, request, backup_task_schedule_id):
        schedule = self._get_schedule_or_None(backup_task_schedule_id)
        if schedule is not None:
            SpaceCollectionWorker.set_schedule_deleting_and_collection_space_later(schedule)
            end_sleep.send_robust(sender=BackupTaskSchedule, schedule_id=schedule.id)
        return Response(status=status.HTTP_204_NO_CONTENT)  # always successful

    @staticmethod
    def change_node_for_yun(new_node, tenants):
        with transaction.atomic():
            schedules = BackupTaskSchedule.objects.filter(host__user__username__in=tenants).all()
            for schedule in schedules:
                schedule.storage_node_ident = new_node
                schedule.save(update_fields=['storage_node_ident', ])
            node = StorageNode.objects.get(ident=new_node)
            for t in tenants:
                try:
                    user_id = User.objects.get(username=t).id
                except User.DoesNotExist:
                    continue
                waring_value = xdata.USER_QUOTA_IS_NOT_WARING_VALUE
                quota_size = xdata.USER_QUOTA_IS_NOT_LIMIT_VALUE
                available_size = UserQuotaTools(node.id, user_id, quota_size).get_user_available_storage_size_in_node()
                is_exist = UserQuota.objects.filter(storage_node_id=node.id, user_id=user_id).count()
                if not is_exist:
                    UserQuota.objects.create(storage_node_id=node.id,
                                             user_id=user_id,
                                             quota_size=quota_size,
                                             caution_size=waring_value,
                                             available_size=available_size)


# "集群备份计划"的管理: 创建, 查询等
class ClusterBackupScheduleManager(APIView):
    serializer_class = ClusterBackupTaskScheduleSerializer
    queryset = ClusterBackupSchedule.objects.filter(deleted=False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def get(self, request, plan_id):
        schedule = self.queryset.get(id=plan_id)
        serializer = self.serializer_class(schedule)
        return Response(serializer.data)

    def post(self, request, api_request, hosts):
        schedule = ClusterBackupSchedule.objects.create(**api_request)
        schedule.hosts.add(*hosts)
        logicProcessor = BackupTaskScheduleLogicProcessor(schedule)
        schedule.next_run_date = logicProcessor.calc_next_run(True)
        schedule.save(update_fields=['next_run_date'])
        return Response(self.serializer_class(schedule).data, status=status.HTTP_201_CREATED)

    def put(self, request, api_request, plan_id):
        ClusterBackupSchedule.objects.filter(id=plan_id).update(**api_request)
        schedule = ClusterBackupSchedule.objects.get(id=plan_id)
        logicProcessor = BackupTaskScheduleLogicProcessor(schedule)
        schedule.next_run_date = logicProcessor.calc_next_run(True)
        schedule.save(update_fields=['next_run_date'])
        return Response(self.serializer_class(schedule).data, status=status.HTTP_201_CREATED)

    @staticmethod
    @xlogging.convert_exception_to_value(None)
    def _get_schedule_or_None(schedule_id):
        return _check_cluster_schedule_valid(schedule_id)

    @staticmethod
    @xlogging.convert_exception_to_value(None)
    def delete_shell_zip(schedule):
        shell_infos = HostBackupWorkProcessors.get_shell_infos_from_schedule_or_host(None, schedule)
        if shell_infos is None:
            return

        boxService.box_service.remove(shell_infos['zip_path'])

    def delete(self, request, schedule_id):
        cluster_schedule = self._get_schedule_or_None(schedule_id)
        if cluster_schedule is not None:
            self.delete_shell_zip(schedule=cluster_schedule)
            SpaceCollectionWorker.set_cluster_schedule_deleting_and_collection_space_later(
                cluster_schedule, AgentValidDiskSnapshotInfo)
        return Response(data=cluster_schedule, status=status.HTTP_204_NO_CONTENT)


class ClusterBackupTaskScheduleExecute(APIView):
    queryset = ClusterBackupSchedule.objects.none()
    serializer_class = ClusterBackupTaskScheduleSerializer

    def __init__(self, **kwargs):
        super(ClusterBackupTaskScheduleExecute, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    @staticmethod
    def get(request, schedule_id):
        return Response()

    def post(self, request, schedule_id, api_request=None):
        schedule = _check_cluster_backcup_task_schedule_valid(schedule_id)
        logicProcessor = BackupTaskScheduleLogicProcessor(schedule)

        reason = BackupTask.REASON_PLAN_AUTO if (request is None or request.user.is_superuser) \
            else BackupTask.REASON_PLAN_MANUAL

        config = None
        if reason == BackupTask.REASON_PLAN_MANUAL and api_request is not None:
            serializer = BackupTaskScheduleExecuteSerializer(data=api_request)
            if serializer.is_valid(False):
                config = serializer.validated_data
        try:
            self.start_backup(config, logicProcessor, reason, schedule)
        except Exception as e:
            BackupScheduleRetryHandle.modify(schedule)
            raise e
        return Response(status=status.HTTP_201_CREATED)

    def start_backup(self, config, logicProcessor, reason, schedule):
        executor = ClusterBackupTaskExecutor()
        executor.generate_and_save(schedule, reason, config)
        executor.start()
        schedule.last_run_date = timezone.now()
        schedule.next_run_date = logicProcessor.calc_next_run(False)
        schedule.save(update_fields=['last_run_date', 'next_run_date'])


class ClusterCdpBackupTaskScheduleExecute(APIView):
    queryset = ClusterBackupSchedule.objects.none()
    serializer_class = ClusterBackupTaskScheduleSerializer

    def __init__(self, **kwargs):
        super(ClusterCdpBackupTaskScheduleExecute, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    @staticmethod
    def get(request, schedule_id):
        schedule = _check_cluster_backcup_task_schedule_valid(schedule_id)
        return Response(data={'name': schedule.name})

    def post(self, request, schedule_id, api_request=None):
        schedule = _check_cluster_backcup_task_schedule_valid(schedule_id)

        reason = BackupTask.REASON_PLAN_AUTO if (request is None or request.user.is_superuser) \
            else BackupTask.REASON_PLAN_MANUAL

        config = None
        if reason == BackupTask.REASON_PLAN_MANUAL and api_request is not None:
            serializer = BackupTaskScheduleExecuteSerializer(data=api_request)
            if serializer.is_valid(False):
                config = serializer.validated_data
        try:
            self.start_backup(config, reason, schedule)
        except Exception as e:
            BackupScheduleRetryHandle.modify(schedule)
            raise e
        return Response(status=status.HTTP_201_CREATED)

    @staticmethod
    def start_backup(config, reason, schedule):
        executor = ClusterCdpTaskExecutor()
        executor.generate_and_save(schedule, reason, config)
        executor.start()
        schedule.last_run_date = timezone.now()
        schedule.save(update_fields=['last_run_date', ])


class BackupTaskScheduleExecute(APIView):
    queryset = BackupTaskSchedule.objects.none()
    serializer_class = BackupTaskScheduleSerializer

    def __init__(self, **kwargs):
        super(BackupTaskScheduleExecute, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    @staticmethod
    def get(request, backup_task_schedule_id):
        return Response()

    def post(self, request, backup_task_schedule_id, api_request=None):
        schedule = _check_backcup_task_schedule_valid(backup_task_schedule_id)
        if schedule.cycle_type == BackupTaskSchedule.CYCLE_CDP:
            _check_cdp_host_is_idle(schedule)
        logic_processor = BackupTaskScheduleExecuteLogicProcessor(schedule)
        reason = BackupTask.REASON_PLAN_AUTO if (request is None or request.user.is_superuser) \
            else BackupTask.REASON_PLAN_MANUAL

        config = None
        if reason == BackupTask.REASON_PLAN_MANUAL and api_request is not None:
            serializer = BackupTaskScheduleExecuteSerializer(data=api_request)
            if serializer.is_valid(False):
                config = serializer.validated_data

        try:
            logic_processor.run(reason, config)
        except Exception as e:
            BackupScheduleRetryHandle.modify(schedule)
            raise e
        return Response(status=status.HTTP_201_CREATED)


class HostSessionBackup(APIView):
    queryset = HostSnapshot.objects

    def __init__(self, **kwargs):
        super(HostSessionBackup, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def _get_host_snapshot(self, ident):
        host = _check_host_session_valid(ident)

        host_snapshot = self.queryset.filter(host=host, start_datetime__isnull=False, finish_datetime__isnull=True) \
            .order_by('-start_datetime').first()

        if host_snapshot is None:
            xlogging.raise_and_logging_error('无法更新客户端快照信息', 'can NOT update host :{}'
                                             .format(ident), status.HTTP_405_METHOD_NOT_ALLOWED)
        return host_snapshot

    @staticmethod
    def get(request, ident):
        return Response()

    def put(self, request, ident):
        host_snapshot = self._get_host_snapshot(ident)
        serializer = HostSessionBackupProgressSerializer(data=request.data)
        _logger.info('HostSessionBackup serializer:{},request.data:{}'.format(serializer, request.data))
        serializer.is_valid(True)
        HostBackupWorkProcessors.report_progress(host_snapshot, serializer.validated_data)
        return Response(status=status.HTTP_200_OK)

    def delete(self, request, ident):
        host_snapshot = self._get_host_snapshot(ident)

        if 'code' not in request.query_params:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        HostBackupWorkProcessors.report_finish(host_snapshot, int(request.query_params.get('code')))
        if host_snapshot.schedule:
            end_sleep.send_robust(sender=BackupTaskSchedule, schedule_id=host_snapshot.schedule.id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class HostSessionMigrate(APIView):
    queryset = MigrateTask.objects
    serializer_class = HostSessionMigrateSerializer

    def __init__(self, **kwargs):
        super(HostSessionMigrate, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    @staticmethod
    def get(request, ident):
        return Response()

    def post(self, request, ident, api_request=None, immediately_run=False):
        if api_request is None:
            api_request = request.data
        host_session = _check_host_session_valid(ident)
        serializer = self.serializer_class(data=api_request)
        serializer.is_valid(True)

        pe_host_object = _check_pe_host_session(serializer.validated_data['pe_host_ident'])
        target_host_ident = serializer.validated_data.get('target_host_ident', None)
        if target_host_ident is not None:
            target_host_object = _check_host_session_valid(target_host_ident)
        else:
            target_host_object = None

        logic_processor = HostSessionMigrateLogicProcessor(host_session, pe_host_object, target_host_object,
                                                           serializer.validated_data)
        logic_processor.run(immediately_run)
        return Response(status=status.HTTP_201_CREATED)


# class PeHosts(APIView):
#     queryset = RestoreTarget.objects.none()
#     serializer_class = PeHostSerializer
#
#     def get(self, request):
#         hosts = RestoreTarget.objects.all()
#         if len(hosts) == 0:
#             return Response(status=status.HTTP_204_NO_CONTENT)
#
#         serializer = self.serializer_class(hosts, many=True)
#         return Response(serializer.data)


# class PeHostInfo(APIView):
#     queryset = RestoreTarget.objects.none()
#     serializer_class = PeHostSerializer
#
#     def get_rate(self, numerator, denominator):
#         rate = float(numerator) / float(denominator)
#         return int(rate * 100)
#
#     def get(self, request, ident):
#         try:
#             host_data = RestoreTarget.objects.get(ident=ident)
#         except:
#             host_data = None
#         if host_data is None:
#             return Response(status=status.HTTP_404_NOT_FOUND)
#         if host_data.is_finished():
#             return Response('还原成功')
#         if host_data.restored_bytes is None or host_data.total_bytes is None:
#             return Response('正在初始化...')
#         rate = self.get_rate(host_data.restored_bytes, host_data.total_bytes)
#         return Response('还原进度:{}%'.format(rate))
#
#     def put(self, request, ident):
#         host_obj = RestoreTarget.objects.filter(ident=ident)
#         try:
#             data = host_obj.get()
#         except:
#             data = None
#         if data is None:
#             return Response(status=status.HTTP_404_NOT_FOUND)
#         if request.data['error']:
#             now_time = timezone.now()
#             host_obj.update(finish_datetime=now_time)
#             return Response(status=status.HTTP_200_OK)
#         if request.data['successful'] and \
#                         request.data['restored_bytes'] == request.data['total_bytes']:
#             now_time = timezone.now()
#             host_obj.update(successful=True,
#                             restored_bytes=request.data['restored_bytes'],
#                             total_bytes=request.data['total_bytes'],
#                             finish_datetime=now_time)
#             return Response(status=status.HTTP_200_OK)
#         else:
#             host_obj.update(restored_bytes=request.data['restored_bytes'],
#                             total_bytes=request.data['total_bytes'])
#             return Response(status=status.HTTP_200_OK)


# PE登陆，获取(未发送过还原命令的)，清除(数据库中)
class PeHostSessions(APIView):
    queryset = RestoreTarget.objects.filter(start_datetime__isnull=True, type=RestoreTarget.TYPE_PE)
    serializer_class = PeHostSessionSerializer

    def __init__(self, **kwargs):
        super(PeHostSessions, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    @staticmethod
    def is_show_own():
        return boxService.get_pe_host_filter_with_user_switch()

    # 获取未发送过还原命令的还原目标客户端
    def get(self, request):
        filter_content = request.GET.get('filter_content', None)
        if self.is_show_own():
            hosts = self.queryset.filter(user=request.user)
        else:
            hosts = self.queryset.all()
        if filter_content:
            hosts = hosts.filter(display_name__contains=filter_content)
        if len(hosts) == 0:
            return Response(status=status.HTTP_204_NO_CONTENT)
        serializer = self.serializer_class(hosts, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    # 还原目标客户端登陆
    def post(self, request):
        admin = User.objects.get(is_superuser=True, username='admin')
        serializer = PeHostSessionLoginSerializer(data=request.data)
        serializer.is_valid(True)
        info = json.dumps(serializer.validated_data, ensure_ascii=False)

        try:
            show_ip = serializer.validated_data['more_info']['ConnectAddress']['LocalAddress']
        except (KeyError, TypeError):
            show_ip = serializer.validated_data['remote_ip']

        pe_host_session = RestoreTarget.objects.create(
            ident=uuid.uuid4().hex.lower(),
            display_name='{} {}'.format(show_ip, timezone.now().strftime('%Y-%m-%d %H:%M:%S')),
            info=info, type=serializer.validated_data['login_type'],
            expiry_minutes=1440 * 7,  # expiry 7 days
            user=admin
        )
        threading.Thread(target=self.set_user_2_pe_host, args=(pe_host_session,)).start()
        data = self.serializer_class(pe_host_session).data
        return Response(data, status=status.HTTP_201_CREATED)

    # 清除数据库所有还原目标客户端
    def delete(self, request):
        _logger.info('已经清除所有未发送过命令的还原目标客户端...')
        self.queryset.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @staticmethod
    def verify_json_str(json_str):
        """ json_str: '{"user_fingerprint": "*xxxxxxxxxx"}' """
        try:
            fingerprint = json.loads(json_str)['user_fingerprint']
            if isinstance(fingerprint, str) and fingerprint.startswith('*'):
                return fingerprint[1:]
            _logger.warning('get_pe_iso_fingerprint, invalid: {}'.format(json_str))
            return None
        except Exception as e:
            _logger.warning('get_pe_iso_fingerprint, invalid: {}'.format(e))
            return None

    def get_pe_iso_fingerprint(self, ident, retry_times=5, interval_secs=10):
        """
        :return: None or fingerprint
        """
        for attempt in range(retry_times):
            time.sleep(interval_secs)
            try:
                json_str, _ = boxService.box_service.PEJsonFunc(ident, json.dumps({'type': 'get_user_fingerprint'}))
                return self.verify_json_str(json_str)
            except xlogging.OperationImplemented as e:
                _logger.warning('get_pe_iso_fingerprint, give up. {}'.format(e.debug))
                return None
            except Exception as e:
                _logger.warning('get_pe_iso_fingerprint, retrying ... {}'.format(e))
        else:
            _logger.warning('get_pe_iso_fingerprint, failed !')
            return None

    def set_user_2_pe_host(self, pe_host):
        pe_user = None
        fingerprint = self.get_pe_iso_fingerprint(pe_host.ident)
        if fingerprint is not None:
            try:
                pe_user = User.objects.get(userprofile__user_fingerprint=fingerprint)
            except User.DoesNotExist:
                _logger.warning('no user with fingerprint: {}'.format(fingerprint))
            except Exception as e:
                _logger.warning('set_user_2_pe_host, failed. {}'.format(e))

        pe_host.user = pe_user
        pe_host.save(update_fields=['user'])


# 获取指定PE(未发送过还原命令)的信息
class PeHostSessionInfo(APIView):
    queryset = RestoreTarget.objects.none()
    serializer_class = PeHostSessionDetailSerializer

    def __init__(self, **kwargs):
        super(PeHostSessionInfo, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    @staticmethod
    def get_pe_network_adapters(ident):
        adapters = list()
        pe_host_adapters = boxService.box_service.GetPeHostNetAdapterInfo(ident)
        for adapter in pe_host_adapters:
            adapters.append(adapter.__dict__)
        return adapters

    @staticmethod
    def fill_hardware_stacks(hardwares, stacks):
        for hardware in hardwares:
            if hardware.parentDevLevel == 0:
                stacks.append([])
            stacks[-1].append(hardware.__dict__)

    # 不在这一层把非pci设备过滤掉，linux可以还原上去
    def get_disk_controller_hardware_stacks(self, ident):
        stacks = list()
        disk_controllers = boxService.box_service.GetPeHostClassHWInfo(ident, 'SCSIAdapter')
        self.fill_hardware_stacks(disk_controllers, stacks)
        disk_controllers = boxService.box_service.GetPeHostClassHWInfo(ident, 'HDC')
        self.fill_hardware_stacks(disk_controllers, stacks)
        ck_funcs = [PeRestore.is_empty_hardware_id_or_compat_id,
                    PeRestore.is_pci_ide_hardware,
                    PeRestore.is_invalid_pci_instance,
                    PeRestore.no_pci_and_no_xen_hypev]
        PeRestore.pop_checked_stacks(stacks, ck_funcs)
        return stacks

    # 不在这一层把非pci设备过滤掉，linux可以还原上去
    def get_network_controller_hardware_stacks(self, ident):
        stacks = list()
        network_controllers = boxService.box_service.GetPeHostClassHWInfo(ident, 'net')
        self.fill_hardware_stacks(network_controllers, stacks)
        ck_funcs = [PeRestore.is_empty_hardware_id_or_compat_id,
                    PeRestore.no_pci_and_no_xen_hypev]
        PeRestore.pop_checked_stacks(stacks, ck_funcs)
        return stacks

    @staticmethod
    def disk_convert(disks, boot_disk_id):
        new_disks = list()
        for disk in disks:
            new_disks.append({'disk_id': disk['diskID'], 'disk_bytes': disk['diskSecCount'] * 512,
                              'is_boot_device': (boot_disk_id == disk['diskID'])})
        return new_disks

    @staticmethod
    def get_data_field_base64_str_from_reg_dev_info(net_device, pe_host):
        reg_json_enum = boxService.box_service.fetch_reg_json(pe_host.ident)
        if reg_json_enum is None:
            return None

        device_id = net_device['szDeviceInstanceID']
        if not device_id:
            return None
        nodes_names = device_id.split('\\')
        nodes_names = nodes_names + ['Properties', '{540b947e-8b40-45bc-a8a2-6a0b894cbda2}', '00000004', '00000000']
        current_node = reg_json_enum
        for node_name in nodes_names:
            current_node_ks = current_node['ks']
            expect_node = list(filter(lambda node: node['n'].upper() == node_name.upper(), current_node_ks))
            if not expect_node:
                return None
            current_node = expect_node[0]

        for item in current_node['vs']:
            if item['t'].upper() == 'REG_BINARY' and item['n'].upper() == 'DATA':
                return item['v']

        return None

    @staticmethod
    def generate_xen_net_location_information_by_device_instance_id(net_device):
        device_id = net_device['szDeviceInstanceID']
        if not device_id:
            _logger.warning(r'generate_xen_net_location_information_by_device_instance_id '
                            r'can NOT find szDeviceInstanceID : {}'.format(net_device))
            return net_device['szLocationInfo']

        device_id = device_id[-2:]
        if (not device_id) or (len(device_id) != 2):
            _logger.warning(r'generate_xen_net_location_information_by_device_instance_id '
                            r'szDeviceInstanceID invalid : {}'.format(net_device))
            return net_device['szLocationInfo']

        _logger.info(r'generate_xen_net_location_information_by_device_instance_id ok : {}'.format(device_id))
        return device_id

    def generate_xen_net_location_information(self, net_device, pe_host):
        base64_str = self.get_data_field_base64_str_from_reg_dev_info(net_device, pe_host)
        if base64_str is None:
            return self.generate_xen_net_location_information_by_device_instance_id(net_device)

        data_str = base64.b64decode(base64_str.encode()).decode('utf-16')
        if '#' not in data_str:
            return self.generate_xen_net_location_information_by_device_instance_id(net_device)
        num_str = data_str.split('#')[-1].strip().strip('\x00')
        result = '{:02}'.format(int(num_str, 16))
        _logger.info(r'generate_xen_net_location_information ok : {}'.format(result))
        return result

    def correct_net_device_location_information_with_xen(self, pe_net_ctr_stacks, pe_host):
        net_devices = chain(*pe_net_ctr_stacks)
        for net_device in net_devices:
            if 'XEN BUS' in net_device['szLocationInfo'].upper():
                net_device['szLocationInfo'] = self.generate_xen_net_location_information(net_device, pe_host)

        boxService.box_service.fetch_reg_json(pe_host.ident, clean_cache=True)

    def get_pe_system_infos(self, ident):
        try:
            arg = {'type': 'GetComputerAllInfo'}
            json_str, _ = boxService.box_service.PEJsonFunc(ident, json.dumps(arg))
            return json.loads(json_str)
        except Exception as e:
            _logger.error('PeHostSessionInfo get_pe_system_infos fail, ident:{}, e:{}'.format(ident, e))
            return {}

    def get_pe_soft_ident(self, ident):
        try:
            arg = {'type': 'get_soft_ident'}
            json_str, _ = boxService.box_service.PEJsonFunc(ident, json.dumps(arg))
            soft_ident = json.loads(json_str)['soft_ident']
            if soft_ident:
                return soft_ident
            else:
                return 'empty'
        except Exception as e:
            _logger.error('PeHostSessionInfo get_pe_soft_ident fail, ident:{}, e:{}'.format(ident, e))
            return 'empty'

    # 获取从未还原的PE信息
    def get(self, request, ident):
        pe_host = _check_pe_host_session(ident)
        pe_info = json.loads(pe_host.info)

        pe_disks = self.disk_convert(pe_info['disks'], pe_info['boot_disk_id'])

        need_update = False

        if 'net_adapters' not in pe_info:
            pe_net_adapters = self.get_pe_network_adapters(ident)
            pe_info['net_adapters'] = pe_net_adapters
            need_update = True
        else:
            pe_net_adapters = pe_info['net_adapters']

        if 'disk_ctr_stacks' not in pe_info:
            pe_disk_ctr_stacks = self.get_disk_controller_hardware_stacks(ident)
            pe_info['disk_ctr_stacks'] = self.filter_duplicate_device(pe_disk_ctr_stacks, ident)
            need_update = True
        else:
            pe_disk_ctr_stacks = pe_info['disk_ctr_stacks']

        if 'net_ctr_stacks' not in pe_info:
            pe_net_ctr_stacks = self.get_network_controller_hardware_stacks(ident)
            self.correct_net_device_location_information_with_xen(pe_net_ctr_stacks, pe_host)
            pe_info['net_ctr_stacks'] = pe_net_ctr_stacks
            need_update = True
        else:
            pe_net_ctr_stacks = pe_info['net_ctr_stacks']

        if 'system_infos' not in pe_info:
            system_infos = self.get_pe_system_infos(ident)
            pe_info['system_infos'] = system_infos
            need_update = True
        else:
            system_infos = pe_info['system_infos']

        if 'soft_ident' not in pe_info:
            soft_ident = self.get_pe_soft_ident(ident)
            pe_info['soft_ident'] = soft_ident
            need_update = True
        else:
            soft_ident = pe_info['soft_ident']

        if need_update:
            pe_host.info = json.dumps(pe_info, ensure_ascii=False)
            pe_host.save(update_fields=['info'])

        pe_host_detail = {
            'pe_host': pe_host,
            'disks': pe_disks,
            'network_adapters': pe_net_adapters,
            'disk_controller_hardware_stacks': pe_disk_ctr_stacks,
            'network_controller_hardware_stacks': pe_net_ctr_stacks,
            'system_infos': system_infos,
            'soft_ident': soft_ident,
        }

        _logger.info('pe_host_detail: {}'.format(pe_host_detail))
        serializer = self.serializer_class(pe_host_detail)
        return Response(serializer.data)

    # 删除从未还原的PE
    def delete(self, request, ident):
        pe_host = _check_pe_host_session(ident)
        pe_host.delete()
        _logger.info('客户端已经断开连接:{}'.format(ident))
        return Response(status.HTTP_204_NO_CONTENT)

    @staticmethod
    def filter_duplicate_device(devices, target_ident):
        rs = list()
        for device in devices:
            if device in rs:
                _logger.warning('target_ident {}, filter_duplicate_device filter:{}'.format(target_ident, device))
            else:
                rs.append(device)
        return rs


class PeHostSessionRestore(APIView):
    queryset = RestoreTarget.objects.none()

    def __init__(self, **kwargs):
        super(PeHostSessionRestore, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    @staticmethod
    def get(request, ident):
        return Response()

    def put(self, request, ident):
        pe_host = _check_pe_host_running(ident)

        pe_host_info = json.loads(pe_host.info)

        pe_host_info['kvm_start'] = datetime.now().strftime(xdatetime.FORMAT_WITH_MICROSECOND)
        pe_host.info = json.dumps(pe_host_info, ensure_ascii=False)

        pe_host.save(update_fields=['info'])

        return Response(status=status.HTTP_205_RESET_CONTENT)


class PeHostSessionVolumeRestore(APIView):
    def __init__(self, **kwargs):
        super(PeHostSessionVolumeRestore, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def put(self, request, ident):
        serializer = PeHostSessionVolumeRestoreSerializer(data=request.data)
        serializer.is_valid(True)
        restore_target = _check_pe_host_valid(ident)
        restore_target.display_status = self._code_2_info(serializer.data['code'])
        restore_target.save(update_fields=['display_status'])

        task_obj = self.get_task_object(restore_target)
        self._check_and_finish_task(task_obj, serializer.data['code'], serializer.data['msg'],
                                    serializer.data['debug'])

        return Response(status=status.HTTP_202_ACCEPTED)

    @staticmethod
    def _code_2_info(code):
        code_list, name_list = zip(*xdata.VOLUME_RESTORE_STATUS_CHOICES)
        return name_list[code_list.index(code)]

    @staticmethod
    def get_task_object(restore_target_object):
        restore_task = restore_target_object.restore
        return RestoreTask.objects.get(id=restore_task.id)

    def _check_and_finish_task(self, task_obj, code, msg, debug):
        if code == xdata.VOLUME_RESTORE_STATUS_FINISH_FAILED:
            restore_disks = task_obj.restore_target.disks.all()
            pe_restore = PeRestore(task_obj.restore_target)
            for restore_disk in restore_disks:
                boxService.box_service.updateToken(
                    KTService.Token(token=restore_disk.token, snapshot=[], expiryMinutes=0))
            pe_restore.unlock_disk_snapshots('volume_restore_{}'.format(task_obj.id))

            tasks.finish_restore_task_object('', task_obj.id, False, msg, debug)
        else:
            value = self._code_2_info(code)
            if is_restore_target_belong_htb(task_obj.restore_target):
                log_type = HostLog.LOG_HTB
            else:
                log_type = HostLog.LOG_RESTORE_START
            HostLog.objects.create(host=task_obj.host_snapshot.host, type=log_type,
                                   reason=json.dumps({'description': value}))


class HostSnapshotInfo(APIView):
    serializer_class = HostSnapshotSerializer
    queryset = HostSnapshot.objects.none()

    def __init__(self, **kwargs):
        super(HostSnapshotInfo, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def get(self, request, host_snapshot_id):
        serializer = self.serializer_class(_check_host_snapshot_id_valid(host_snapshot_id))
        return Response(serializer.data)


class HostSnapshotRestore(APIView):
    serializer_class = HostSnapshotRestoreSerializer
    queryset = HostSnapshot.objects.none()

    def __init__(self, **kwargs):
        super(HostSnapshotRestore, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    @staticmethod
    def get(request, host_snapshot_id):
        return Response()

    def post(self, request, host_snapshot_id, api_request=None, immediately_run=False):
        if api_request is None:
            api_request = request.data

        _logger.info(r'HostSnapshotRestore post : {} {}'.format(host_snapshot_id, api_request))

        host_snapshot_object = _check_host_snapshot_id_valid(host_snapshot_id)

        serializer = self.serializer_class(data=api_request)
        serializer.is_valid(True)

        restore_time = serializer.validated_data['restore_time']
        _check_host_snapshot_object_valid(host_snapshot_object, restore_time)

        pe_host_ident = serializer.validated_data.get('pe_host_ident', None)
        if pe_host_ident is not None:  # 整机还原
            pe_host_object = _check_pe_host_session(pe_host_ident)
            host_ident = serializer.validated_data.get('host_ident', None)
            if host_ident is not None:
                host_object = _check_host_session_valid(host_ident)
            else:
                host_object = None

            logicProcessor = HostSnapshotRestoreLogicProcessor(host_snapshot_object, pe_host_object, host_object,
                                                               serializer.validated_data)
        else:  # 卷还原
            host_object = _check_host_session_valid(serializer.validated_data['host_ident'])
            logicProcessor = HostSnapshotRestoreVolumeLogicProcessor(
                host_snapshot_object, host_object, serializer.validated_data)

        logicProcessor.run(immediately_run)
        return Response(status=status.HTTP_201_CREATED)


class HostSnapshotLocalRestore(APIView):
    serializer_class = HostSnapshotLocalRestoreSerializer
    queryset = HostSnapshot.objects.none()

    def __init__(self, **kwargs):
        super(HostSnapshotLocalRestore, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    @staticmethod
    def get(request, host_snapshot_id):
        return Response()

    @staticmethod
    def is_pe_mac_in_mac_map_ips(pe_mac, mac_map_ips):
        for mac in mac_map_ips.keys():
            if xdata.is_two_mac_addr_equal(pe_mac, mac):
                return True
        else:
            return False

    @staticmethod
    def query_target_nic_info_in_snapshot_nics(snapshot_nics, nic_mac):
        for nic in snapshot_nics:
            if xdata.is_two_mac_addr_equal(nic['Mac'], nic_mac):
                return nic
        return None

    @staticmethod
    def is_restore_to_linux_host(hostsnapshot_ext_info):
        system_infos = hostsnapshot_ext_info['system_infos']
        return 'LINUX' in system_infos['System']['SystemCaption'].upper()

    @staticmethod
    def query_nic_instance_id_in_src(src_ext_info, nic_mac):
        if HostSnapshotLocalRestore.is_restore_to_linux_host(src_ext_info):
            return ''

        for nic in src_ext_info['network_adapter_infos']:
            if xdata.is_two_mac_addr_equal(nic['szMacAddress'], nic_mac):
                return nic['szDeviceInstanceID']

        xlogging.raise_and_logging_error(msg='目标网卡信息不存在于源中',
                                         debug='target mac:{} not in snapshot_ext_info'.format(nic_mac, src_ext_info))

    def generate_target_nic_info_by_scr_hostsnapshot(self, target_adapter, snapshot_obj):
        ext_info = json.loads(snapshot_obj.ext_info)
        snapshot_nics = ext_info['system_infos']['Nic']
        snapshot_nic = self.query_target_nic_info_in_snapshot_nics(snapshot_nics, target_adapter['szMacAddress'])
        if snapshot_nic is None:
            return None

        return {
            "adapter": target_adapter['szGuid'],
            "ip": snapshot_nic['IpAndMask'][0]['Ip'] if snapshot_nic['IpAndMask'] else '',
            "subnet_mask": snapshot_nic['IpAndMask'][0]['Mask'] if snapshot_nic['IpAndMask'] else '',
            "routers": snapshot_nic['GateWay'],
            "dns": snapshot_nic['Dns'][0] if snapshot_nic['Dns'] else '',
            "multi_infos": json.dumps({
                "target_nic": target_adapter,
                "ip_mask_pair": snapshot_nic['IpAndMask'],
                "dns_list": snapshot_nic['Dns'],
                "gate_way": snapshot_nic['GateWay'],
                "is_to_self": True,
                "src_instance_id": self.query_nic_instance_id_in_src(ext_info, target_adapter['szMacAddress'])
            })
        }

    @staticmethod
    def target_master_nic_is_vaild(adapters):
        for adapter in adapters:
            multi_infos = json.loads(adapter['multi_infos'])
            if multi_infos['target_nic']['isConnected'] and adapter['ip'] not in [None, '', '0.0.0.0']:
                return True
        return False

    @staticmethod
    def _get_ssh_os_type():
        os_type_list = list()
        rootdir = r'/sbin/aio/remote_host'
        folders = os.listdir(rootdir)
        for name in folders:
            curname = os.path.join(rootdir, name)
            if os.path.isfile(curname):
                ext = os.path.splitext(curname)[1]
                if ext == '.json':
                    try:
                        with open(curname, 'r') as fout:
                            try:
                                info = json.loads(fout.read())
                                os_type_list.append(info)
                            except Exception as e:
                                _logger.info('_ssh_os_type read Failed.e={}'.format(e))
                            finally:
                                fout.close()
                    except Exception as e:
                        pass
        return os_type_list

    def post(self, request, host_snapshot_id, api_request=None, immediately_run=False):
        if api_request is None:
            api_request = request.data

        _logger.info(r'HostSnapshotLocalRestore post : {} {}'.format(host_snapshot_id, api_request))

        serializer = self.serializer_class(data=api_request)
        serializer.is_valid(True)

        point_type = serializer.validated_data.get('type', None)
        host_ident = serializer.validated_data.get('host_ident', None)
        pe_host_ident = serializer.validated_data.get('pe_host_ident', None)
        htb_task_uuid = serializer.validated_data.get('htb_task_uuid', None)
        htb_schedule_id = serializer.validated_data.get('htb_schedule_id', None)
        htb_disk_params = serializer.validated_data.get('disk_params', list())

        if not pe_host_ident:
            rspn = Agent2Pe().post(request=request, host_ident=host_ident, api_request={})
            if status.is_success(rspn.status_code):
                pe_host_ident = rspn.data

            if pe_host_ident is None:
                _logger.error(r'HostSnapshotLocalRestore pe_host_ident is None Failed.')
                return Response(status=status.HTTP_404_NOT_FOUND)

        PeHostSessionInfo_response = PeHostSessionInfo().get(request=request, ident=pe_host_ident)
        if not status.is_success(PeHostSessionInfo_response.status_code):
            _logger.error(
                r'HostSnapshotLocalRestore PeHostSessionInfo.get() Failed.pe_host_ident={}'.format(pe_host_ident))
            return Response(status=status.HTTP_404_NOT_FOUND)

        snapshot_obj = HostSnapshot.objects.get(id=host_snapshot_id)
        ext_info = json.loads(snapshot_obj.ext_info)
        system_infos = ext_info["system_infos"]
        include_ranges = ext_info["include_ranges"]
        route_infos = ext_info["route_infos"]

        disk_params_obj = list()
        if htb_task_uuid:
            disk_params_obj = htb_disk_params
        else:
            for disk_ranges in include_ranges:
                disk_params_obj.append({"src": disk_ranges["diskIdent"], "dest": disk_ranges['diskIndex']})

        adapter_params_obj = list()
        ex_vols = list()
        if htb_task_uuid:  # htb windows/linux restore
            htb_schedule = HTBSchedule.objects.get(id=htb_schedule_id)
            pe_host = RestoreTarget.objects.get(ident=pe_host_ident)
            pe_info = json.loads(pe_host.info)
            exc_info = json.loads(htb_schedule.ext_config)
            dns_l = exc_info['standby_adpter']['dns']
            gateway_l = exc_info['standby_adpter']['gateway']
            for ip_info in exc_info['standby_adpter']['control']:
                mac = ip_info['mac']
                adapter = from_mac_get_adapter(mac, pe_info['net_adapters'])
                if not adapter:
                    _logger.info('from_mac_get_adapter not find adapter from mac:{}'.format(mac))
                    continue
                if not adapter['isConnected']:
                    _logger.info('HostSnapshotLocalRestore adapter is not connected, adapter:{}'.format(adapter))
                    continue
                multi_info_str = dict()
                multi_info_str['target_nic'] = adapter
                multi_info_str['ip_mask_pair'] = [{'Ip': ip_info['ips'][0]['ip'], 'Mask': ip_info['ips'][0]['mask']}]
                multi_info_str['dns_list'] = dns_l
                multi_info_str['gate_way'] = gateway_l[0]
                multi_info_str['network_name'] = ''
                multi_info_str['is_set'] = True
                multi_info_str['is_to_self'] = False
                multi_info_str['src_instance_id'] = ''
                adapter_params_obj.append(
                    {"adapter": adapter['szGuid'],
                     "ip": ip_info['ips'][0]['ip'],
                     "subnet_mask": ip_info['ips'][0]['mask'],
                     "routers": gateway_l[0],
                     "dns": dns_l[0] if dns_l else '',
                     "multi_infos": json.dumps(multi_info_str)
                     })

            assert adapter_params_obj, '无效的网络适配器, htb exc_info:\n{}\n pe net_adapters:\n{}\n'.format(exc_info, pe_info[
                'net_adapters'])

            ex_vols = get_ex_vols(exc_info['ex_vols'])

            drivers_ids = exc_info['drivers_ids']
            drivers_type = exc_info['drivers_type']
            drivers_ids_force = exc_info.get('drivers_ids_force', '')
        else:  # webguard windows/linux restore
            drivers_ids = ''
            drivers_type = '1'  # 本机还原时候智能勾选驱动
            drivers_ids_force = ''
            pe_host = RestoreTarget.objects.get(ident=pe_host_ident)
            if not TargetHardware.is_target_connected_nic_exist_in_source_nics(snapshot_obj, pe_host):
                xlogging.raise_and_logging_error('目标机主网卡, 在源中找不到',
                                                 'target connected nic not in src nics {} {}'.format(snapshot_obj.id,
                                                                                                     pe_host_ident))
            target_adapters = PeHostSessionInfo_response.data['network_adapters']
            for adapter_data in target_adapters:
                target_nic_cfg = self.generate_target_nic_info_by_scr_hostsnapshot(adapter_data, snapshot_obj)
                if target_nic_cfg:
                    adapter_params_obj.append(target_nic_cfg)

            assert adapter_params_obj, '在源中不存在任何目标网卡配置'
            assert self.target_master_nic_is_vaild(adapter_params_obj), '无效的主网卡配置 {}'.format(adapter_params_obj)

        disk_params = json.dumps(disk_params_obj, ensure_ascii=False)
        is_to_self = False if htb_task_uuid else True
        try:
            drivers_list_str = GetDriversVersions.get_drivers_list_str(pe_host_ident, drivers_ids, host_snapshot_id,
                                                                       drivers_type, restore_to_self=is_to_self,
                                                                       user_id=snapshot_obj.host.user.id,
                                                                       driver_ids_force=drivers_ids_force)
            drivers_list_str = drivers_list_str.replace('\\', '|')
        except Exception as e:
            _logger.error(
                r'HostSnapshotLocalRestore get_drivers_list_str Failed.pe_host_ident={} {}'.format(pe_host_ident, e))
            return Response(status=status.HTTP_404_NOT_FOUND)

        user = snapshot_obj.host.user
        user_id, user_fingerprint = user.id, user.userprofile.user_fingerprint

        agent_user_info = '{}|*{}'.format(user_id, user_fingerprint)
        routers = '{"is_save": 0, "router_list": []}'
        restore_time = serializer.validated_data.get('restore_time', None)

        api_request_data = \
            r'"type": "{point_type}", "pe_host_ident": "{pe_host_ident}", ' \
            r' "disks": {disk_params}, "drivers_ids": {drivers_ids}, "agent_user_info": "{agent_user_info}",' \
            r'"routers":{routers}'.format(
                point_type=point_type, pe_host_ident=pe_host_ident,
                disk_params=disk_params, drivers_ids=drivers_list_str, agent_user_info=agent_user_info,
                routers=routers)
        if point_type == xdata.SNAPSHOT_TYPE_CDP:
            api_request_data += ', "restore_time": "{restore_time}"'.format(restore_time=restore_time)

        ssh_os_type = self._get_ssh_os_type()
        query_params = dict()
        if len(ssh_os_type) and sshkvm.objects.all().count() > 0:
            objs = sshkvm.objects.all()
            for obj in objs:
                if obj.enablekvm:
                    query_params['enablekvm'] = '1'
                else:
                    query_params['enablekvm'] = '0'
                query_params['ssh_port'] = obj.ssh_port
                query_params['ssh_key'] = obj.ssh_key
                query_params['aio_ip'] = obj.aio_ip
                query_params['ssh_os_type'] = obj.ssh_os_type
                query_params['ssh_path'] = obj.ssh_path
                query_params['ssh_ip'] = obj.ssh_ip
        else:
            query_params['enablekvm'] = '0'
            query_params['ssh_ip'] = ''
            query_params['ssh_port'] = '22'
            query_params['ssh_key'] = ''
            query_params['aio_ip'] = ''
            query_params['ssh_os_type'] = ''
            query_params['ssh_path'] = ''
            query_params['ssh_ip'] = ''
        remote_kvm_params = dict()
        remote_kvm_params['enablekvm'] = str(query_params['enablekvm'])
        if remote_kvm_params['enablekvm'] == '0':
            remote_kvm_params['ssh_ip'] = ''
            remote_kvm_params['ssh_port'] = ''
            remote_kvm_params['ssh_key'] = ''
            remote_kvm_params['aio_ip'] = ''
            remote_kvm_params['ssh_path'] = ''
            remote_kvm_params['ssh_os_type'] = ''
        else:
            remote_kvm_params['ssh_ip'] = str(query_params['ssh_ip'])
            remote_kvm_params['ssh_port'] = str(query_params['ssh_port'])
            remote_kvm_params['ssh_key'] = str(query_params['ssh_key'])
            remote_kvm_params['aio_ip'] = str(query_params['aio_ip'])
            remote_kvm_params['ssh_path'] = os.path.join(str(query_params['ssh_path']),
                                                         '{}_{}'.format(host_snapshot_id, time.time()))
            remote_kvm_params['ssh_os_type'] = str(query_params['ssh_os_type'])

        api_request_data = api_request_data.replace('\\', '')
        api_local_request = json.loads('{{{}}}'.format(api_request_data))
        api_local_request['exclude_volumes'] = ex_vols
        api_local_request['disable_fast_boot'] = False
        api_local_request['htb_task_uuid'] = htb_task_uuid
        api_local_request['adapters'] = adapter_params_obj
        api_local_request['remote_kvm_params'] = remote_kvm_params

        return HostSnapshotRestore().post(None, host_snapshot_id, api_local_request, immediately_run)


def _is_data_transmittal_filter_host_snapshot(host_snapshots):
    result = list()
    for host_snapshot in host_snapshots:
        ext_info = json.loads(host_snapshot.ext_info)
        index = ext_info.get('progressIndex', 0)
        if index > 0 or host_snapshot.finish_datetime:
            result.append(host_snapshot)

    return result


def _is_schedule_valid(host_snapshot):
    if host_snapshot.remote_schedule:
        return not host_snapshot.remote_schedule.deleted
    else:
        return False


def _is_belong_to_cluster_schedule_filter_host_snapshot(host_snapshots):
    result = list()
    for host_snapshot in host_snapshots:
        if hasattr(host_snapshot, 'cluster_schedule') and host_snapshot.cluster_schedule:  # 隶属集群备份计划
            if all([host_snapshot.cluster_finish_datetime, host_snapshot.successful, host_snapshot.cluster_visible]):
                result.append(host_snapshot)
                continue
        elif hasattr(host_snapshot, 'schedule') and host_snapshot.schedule:  # 隶属备份计划
            result.append(host_snapshot)
        elif hasattr(host_snapshot, 'remote_schedule') and _is_schedule_valid(host_snapshot):
            result.append(host_snapshot)
        elif host_snapshot.host.type == Host.ARCHIVE_AGENT:
            result.append(host_snapshot)

    return result


class HostSnapshotsWithNormalPerHost(APIView):
    serializer_class = HostSnapshotSerializer
    queryset = HostSnapshot.objects.none()

    def __init__(self, **kwargs):
        super(HostSnapshotsWithNormalPerHost, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    @staticmethod
    def get_host_snapshots(ident, is_finish, begin_time, end_time, include_partial):
        queryset = HostSnapshot.objects.filter(host__ident=ident, deleted=False, start_datetime__isnull=False,
                                               deleting=False, is_cdp=False).exclude(successful=False)

        if not include_partial:
            queryset = queryset.exclude(finish_datetime__isnull=False, partial=True)

        if is_finish is not None:
            queryset = queryset.filter(finish_datetime__isnull=(not bool(is_finish)))

        if begin_time is not None:
            queryset = queryset.filter(start_datetime__gte=begin_time)

        if end_time is not None:
            queryset = queryset.filter(start_datetime__lt=end_time)

        return queryset

    def get(self, request, ident, api_request=None):
        check_host_ident_valid(ident)

        if api_request is None:
            api_request = request.query_params

        is_finish = api_request.get('finish', None)
        begin_time = api_request.get('begin', None)
        end_time = api_request.get('end', None)
        include_partial = api_request.get('include_partial', 'False').upper() == 'TRUE'
        use_serializer = api_request.get('use_serializer', True)

        host_snapshots = self.get_host_snapshots(ident, is_finish, begin_time, end_time, include_partial).order_by(
            'start_datetime').all()
        host_snapshots = _is_data_transmittal_filter_host_snapshot(host_snapshots)
        host_snapshots = _is_belong_to_cluster_schedule_filter_host_snapshot(host_snapshots)

        if use_serializer:
            serializer = self.serializer_class(host_snapshots, many=True)
            return Response(serializer.data)
        else:
            return Response(
                [{'id': host_snapshot.id,
                  'start_datetime': host_snapshot.start_datetime.strftime(xdatetime.FORMAT_WITH_MICROSECOND)}
                 for host_snapshot in host_snapshots])


class HostSnapshotsWithCdpPerHost(APIView):
    queryset = HostSnapshot.objects.none()

    def __init__(self, **kwargs):
        super(HostSnapshotsWithCdpPerHost, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    @staticmethod
    def get_host_snapshots(ident, is_finish, begin_time, end_time):
        queryset = HostSnapshot.objects.filter(host__ident=ident, deleted=False, start_datetime__isnull=False,
                                               deleting=False, is_cdp=True).exclude(successful=False)

        queryset = queryset.exclude(finish_datetime__isnull=False, partial=True)

        if is_finish is not None:
            queryset = queryset.filter(finish_datetime__isnull=(not bool(is_finish)))

        if begin_time is not None:
            queryset = queryset.filter(cdp_info__last_datetime__gte=begin_time)

        if end_time is not None:
            queryset = queryset.filter(cdp_info__first_datetime__lte=end_time)

        return queryset

    def get(self, request, ident, api_request=None):
        host_object = check_host_ident_valid(ident)

        if api_request is None:
            api_request = request.query_params

        is_finish = api_request.get('finish', None)
        begin_time = api_request.get('begin', None)
        end_time = api_request.get('end', None)

        if begin_time is not None:
            begin_time = xdatetime.string2datetime(begin_time)

        if end_time is not None:
            end_time = xdatetime.string2datetime(end_time)

        host_snapshot_objects = self.get_host_snapshots(host_object.ident, is_finish, begin_time, end_time).order_by(
            'start_datetime').all()
        host_snapshot_objects = _is_data_transmittal_filter_host_snapshot(host_snapshot_objects)
        host_snapshot_objects = _is_belong_to_cluster_schedule_filter_host_snapshot(host_snapshot_objects)

        result = list()
        for host_snapshot_object in host_snapshot_objects:
            begin = host_snapshot_object.cdp_info.first_datetime
            if begin_time is not None:
                begin = begin if begin > begin_time else begin_time
            end = host_snapshot_object.cdp_info.last_datetime
            if CDPHostSnapshotSpaceCollectionMergeTask.get_running_task_using(host_snapshot_object):
                end = timezone.now()
            if end_time is not None:
                end = end if end < end_time else end_time
            result.append({'id': host_snapshot_object.id, 'begin': begin.strftime(xdatetime.FORMAT_WITH_MICROSECOND),
                           'end': end.strftime(xdatetime.FORMAT_WITH_MICROSECOND)})

        return Response(result)


class CdpTokenInfo(APIView):
    queryset = DiskSnapshot.objects.none()

    def __init__(self, **kwargs):
        super(CdpTokenInfo, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def get(self, request, token):
        last_path = request.query_params.get('last_path', None)
        if last_path is None:
            xlogging.raise_and_logging_error('无效的参数', 'CdpTokenInfo get NO last_path', status.HTTP_400_BAD_REQUEST)

        cdp_token_object = _check_cdp_token_string_valid(token)
        Tokens.change_cdp_file_logic(cdp_token_object.id, last_path)

        return Response(status=status.HTTP_200_OK)

    def delete(self, request, token):
        cdp_token_obj = _check_cdp_token_string_valid(token)
        if cdp_token_obj.using_disk_snapshot is None:
            return Response(status=status.HTTP_204_NO_CONTENT)

        using_disk_snapshot_object = cdp_token_obj.using_disk_snapshot

        if not Tokens.close_cdp_file_logic(using_disk_snapshot_object.cdp_info):
            return Response(status=status.HTTP_406_NOT_ACCEPTABLE)

        return Response(status=status.HTTP_204_NO_CONTENT)


class CdpTokenTc(APIView):
    queryset = DiskSnapshot.objects.none()

    def __init__(self, **kwargs):
        super(CdpTokenTc, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def get(self, request, token):
        io_session = int(request.query_params.get('io_session', '0'))
        kilo_bytes_per_second = -1

        cdp_token_object = _check_cdp_token_string_valid(token)
        schedule_object = Tokens.get_schedule_obj_from_cdp_task(cdp_task=cdp_token_object.task)
        maxBroadband = json.loads(schedule_object.ext_config)['maxBroadband']
        if maxBroadband != -1:
            kilo_bytes_per_second = int(maxBroadband * 1024 / 8)
            if kilo_bytes_per_second <= 1024:
                kilo_bytes_per_second = 1024

        boxService.box_service.updateTrafficControl(
            io_session, 'schedule_{}'.format(schedule_object.id), kilo_bytes_per_second)

        return Response(status=status.HTTP_200_OK)


class TokenInfo(APIView):
    queryset = DiskSnapshot.objects.none()

    def __init__(self, **kwargs):
        super(TokenInfo, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    @staticmethod
    def get_token_object(token):
        cluster_token_mapper_obj = ClusterTokenMapper.objects.filter(agent_token=token).first()
        if cluster_token_mapper_obj is not None:
            return None, None, cluster_token_mapper_obj

        query_restore_obj = RestoreTargetDisk.objects.filter(token=token).first()
        if query_restore_obj is not None:
            return query_restore_obj, None, None

        query_cdp_obj = CDPDiskToken.objects.filter(token=token).first()
        if query_cdp_obj is not None:
            return None, query_cdp_obj, None

        _logger.error(r'never exist token : {}'.format(token))
        raise Http404

    def get(self, request, token):
        _logger.info(r'TokenInfo get token : {}'.format(token))

        restore_token_object, cdp_token_object, mapper_token_object = TokenInfo.get_token_object(token)
        if restore_token_object is not None:
            return TokenInfo.refresh_restore_token(restore_token_object)
        elif cdp_token_object is not None:
            return TokenInfo.refresh_cdp_token(cdp_token_object)
        elif mapper_token_object is not None:
            return TokenInfo.set_mapper_token(mapper_token_object)

    @staticmethod
    def set_mapper_token(mapper_token_object):
        if mapper_token_object.cluster_task.finish_datetime is not None:
            _logger.info('set_mapper_token cluster_task.finish_datetime is not None ')
            return Response(status=status.HTTP_200_OK)

        file_token =  mapper_token_object.file_token.token \
            if  mapper_token_object.file_token else mapper_token_object.agent_token
        # 没有mapper_token_object.file_token意味着不存储该agent_token传输的数据
        boxService.box_service.updateClientCdpToken(
            mapper_token_object.agent_token, file_token,
            mapper_token_object.host_ident, mapper_token_object.disk_id, '', 0)
        return Response(status=status.HTTP_200_OK)

    @staticmethod
    def refresh_cdp_token(cdp_token_object):
        Tokens.check_cdp_token_object(cdp_token_object)
        Tokens.check_cdp_task_object(cdp_token_object.task)
        if not Tokens.refresh_cdp_file_logic(cdp_token_object):
            return Response(status=status.HTTP_406_NOT_ACCEPTABLE)

        return Response(status=status.HTTP_200_OK)

    @staticmethod
    def refresh_restore_token(restore_token_object):
        if not Tokens.check_restore_token_object(restore_token_object):
            return Response(status=status.HTTP_400_BAD_REQUEST)

        validator_list = [GetSnapshotList.is_disk_snapshot_object_exist,
                          GetSnapshotList.is_disk_snapshot_file_exist]
        disk_snapshot_objects = GetSnapshotList.query_snapshots_by_snapshot_object(
            restore_token_object.snapshot, validator_list, restore_token_object.snapshot_timestamp)
        if len(disk_snapshot_objects) == 0:
            _logger.error(r'TokenInfo get len(disk_snapshot_objects) == 0, token:{}'.format(restore_token_object.token))
            return Response(status=status.HTTP_409_CONFLICT)

        Tokens.update_restore_expires(restore_token_object)
        token_setting = Tokens.restore_token_object2KTServiceToken(restore_token_object, disk_snapshot_objects)
        if not Tokens.set_token(token_setting):
            return Response(status=status.HTTP_406_NOT_ACCEPTABLE)
        return Response(status=status.HTTP_200_OK)

    def put(self, request, token):
        restore_token_object, cdp_token_object, _ = TokenInfo.get_token_object(token)
        if restore_token_object is not None:
            remainingBytes = request.data.get('remainingBytes', None)
            totalBytes = request.data.get('totalBytes', None)
            finished = request.data.get('finished', None)
            successful = request.data.get('successful', None)
            host_ident = request.data.get('host_ident')  # 这个host_ident是目标客户端的ident
            _logger.info(r'remainingBytes:{} totalBytes:{} finished:{} successful:{} host_ident:{}'
                         .format(remainingBytes, totalBytes, finished, successful, host_ident))

            if remainingBytes is None or totalBytes is None or finished is None or successful is None:
                return Response(status=status.HTTP_400_BAD_REQUEST)

            restore_target_object = restore_token_object.pe_host
            TokenInfo.update_restore_progress(restore_target_object, remainingBytes, totalBytes, host_ident)

            if finished:
                RestoreTargetChecker.report_restore_target_finished(
                    restore_target_object, successful, r'内部异常，代码2345', r'TokenInfo put never happened')

                for disk in restore_target_object.disks.all():
                    try:
                        boxService.box_service.updateToken(
                            KTService.Token(token=disk.token, snapshot=[], expiryMinutes=0))
                    except Exception as e:
                        _logger.warning('call boxService.updateToken failed. {}'.format(e))

            return Response(status=status.HTTP_205_RESET_CONTENT)
        else:
            _logger.error(r'TokenInfo put NOT restore_token_object. {}'.format(token))
            raise Http404

    @staticmethod
    def update_restore_progress(restore_target_object, remainingBytes, totalBytes, host_ident):
        update_fields = list()
        if restore_target_object.total_bytes is None:
            restore_target_object.total_bytes = totalBytes
            update_fields.append('total_bytes')
        else:
            if totalBytes > restore_target_object.total_bytes:
                xlogging.raise_and_logging_error('Agent上传还原进度异常',
                                                 'report total_bytes:{0} > database total_bytes:{1} restore_target:{2}'
                                                 .format(totalBytes,
                                                         restore_target_object.total_bytes,
                                                         restore_target_object.ident))
            totalBytes = restore_target_object.total_bytes

        _info = json.loads(restore_target_object.info)
        if restore_target_object.restored_bytes is None:
            _info['restoreBytesTemp'] = 0
        else:
            _info['restoreBytesTemp'] = restore_target_object.restored_bytes
        restore_target_object.restored_bytes = totalBytes - remainingBytes
        _info['updateTime'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        _info['host_ident'] = host_ident
        restore_target_object.info = json.dumps(_info, ensure_ascii=False)
        update_fields.append('info')
        update_fields.append('restored_bytes')

        restore_target_object.save(update_fields=update_fields)

    def post(self, request, token):
        _logger.info('TokenInfo.post, token: {}'.format(token))
        restore_token, *_ = TokenInfo.get_token_object(token)
        if restore_token:
            if not self.is_restore_target_initial_linked(restore_token.pe_host):
                self.create_restore_target_initial_linked_log(restore_token.pe_host, token)

            return Response(status=status.HTTP_201_CREATED)

        xlogging.raise_and_logging_error(r'无效的Token信息', r'TokenInfo.post invalid token {}'.format(token),
                                         status.HTTP_404_NOT_FOUND)

    @staticmethod
    def is_restore_target_initial_linked(restore_target):
        info = json.loads(restore_target.info)
        return info.get('initial_linked', False)

    @staticmethod
    def get_restore_target_src_host(restore_target, token):
        if hasattr(restore_target, 'restore'):
            src_host, task_type = restore_target.restore.host_snapshot.host, 'RestoreTask'
        elif restore_target.htb_task.exists():
            src_host, task_type = restore_target.htb_task.first().schedule.host, 'HTBTask'
        elif hasattr(restore_target, 'migrate'):
            src_host, task_type = restore_target.migrate.source_host, 'MigrateTask'
        else:
            src_host, task_type = None, ''

        if src_host is None:
            _logger.warning(
                'restore_target not in any tasks: MigrateTask, RestoreTask, HTBTask. {} {}'.format(restore_target.ident,
                                                                                                   token))
        return src_host, task_type

    def create_restore_target_initial_linked_log(self, restore_target, token):
        info = json.loads(restore_target.info)
        info['initial_linked'] = True
        restore_target.info = json.dumps(info)
        restore_target.save(update_fields=['info'])

        src_host, task_type = self.get_restore_target_src_host(restore_target, token)
        if None in [src_host, task_type]:
            return None
        restore_task = restore_target.restore
        task_type_transition = ''
        if task_type == 'MigrateTask':
            log_type = HostLog.LOG_MIGRATE_START
            task_type_transition = 'migrate_task'
        elif task_type == 'RestoreTask':
            log_type = HostLog.LOG_RESTORE_START
            task_type_transition = 'restore_task'
        elif task_type == 'HTBTask':
            log_type = HostLog.LOG_HTB
            task_type_transition = 'htb_task'
        else:
            log_type = HostLog.LOG_RESTORE_START
        if info.get('restore_cmd', None):
            pass  # 卷还原  不做记录
        else:
            reason = {'token': token, 'description': '目标客户端已经重启并连接成功', 'task_type': task_type_transition,
                      task_type_transition: restore_task.id, 'stage': 'TASK_STEP_IN_PROGRESS_RESTORE_START_AND_CONNECT'}
            HostLog.objects.create(host=src_host, type=log_type, reason=json.dumps(reason, ensure_ascii=False))
            # 为云端增加一条日志记录
            reason = {'token': token, 'description': '发送数据中', 'task_type': task_type_transition,
                      task_type_transition: restore_task.id, 'stage': 'TASK_STEP_IN_PROGRESS_RESTORE_TRANSFER_DATA'}
            HostLog.objects.create(host=src_host, type=log_type, reason=json.dumps(reason, ensure_ascii=False))


class TokenInfoDetail(APIView):
    queryset = DiskSnapshot.objects.none()

    def __init__(self, **kwargs):
        super(TokenInfoDetail, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def get(self, request, token):
        restore_token_object, cdp_token_object, _ = TokenInfo.get_token_object(token)
        if restore_token_object is not None:
            return TokenInfoDetail._restore_token(restore_token_object)
        elif cdp_token_object is not None:
            pass

        xlogging.raise_and_logging_error(
            r'无效的Token信息', r'TokenInfoDetail invalid token {}'.format(token), status.HTTP_404_NOT_FOUND)

    @staticmethod
    def _restore_token(restore_token_object):
        disk_snapshot_object = restore_token_object.snapshot
        if not disk_snapshot_object.is_cdp:
            detail = {r'cdp_timestamp_seconds': -1,
                      r'cdp_timestamp_microseconds': -1,
                      r'cdp_token': disk_snapshot_object.ident}
        else:
            cdp_timestamp_string = ('{:f}'.format(restore_token_object.snapshot_timestamp)).split('.')
            detail = {r'cdp_timestamp_seconds': int(cdp_timestamp_string[0]),
                      r'cdp_timestamp_microseconds': int(cdp_timestamp_string[1]),
                      r'cdp_token': disk_snapshot_object.ident}

        return Response(detail)


# 通过cdp_token 来获取cdp的detail
class TokenInfoDetailByCdpToken(APIView):
    queryset = DiskSnapshot.objects.none()

    def __init__(self, **kwargs):
        super(TokenInfoDetailByCdpToken, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def get(self, request, token, api_request=None):
        if api_request is None:
            api_request = request.query_params

        host_name = api_request.get('host_name', None)
        schedule_id = api_request.get('schedule_id', None)

        _logger.info('TokenInfoDetailByCdpToken get detail, token:{}, host_name:{} schedule_id:{}'.format(token,
                                                                                                          host_name,
                                                                                                          schedule_id))
        cdp_token_object = _check_cdp_token_string_valid(token)
        host = check_host_ident_valid(host_name)
        src_host = cdp_token_object.parent_disk_snapshot.host_snapshot.host

        # 不同的主机，说明是在以CDP启动的接管主机。需要修正cdp 相关信息
        if src_host.ident != host.ident:
            detail = self._get_cdp_detail(cdp_token_object, host, schedule_id)
        else:
            detail = {r'cdp_timestamp_seconds': -1,
                      r'cdp_timestamp_microseconds': -1,
                      r'cdp_token': '-1',
                      r'returned': False}
        _logger.info('TokenInfoDetailByCdpToken token:{}, detail:{}'.format(token, detail))
        return Response(detail)

    def _get_cdp_detail(self, cdp_token_object, host, schedule_id):
        takeover_object = self._get_takeover_task_object(schedule_id)
        disk_ident = cdp_token_object.parent_disk_snapshot.disk.ident
        ext_info = json.loads(takeover_object.ext_info)
        disk_snapshot_info_list = list()
        disk_snapshot_info_list.extend(ext_info['disk_snapshots']['boot_devices'])
        disk_snapshot_info_list.extend(ext_info['disk_snapshots']['data_devices'])
        for disk_snapshot_info in disk_snapshot_info_list:
            if disk_snapshot_info['disk_ident'] == disk_ident:
                last_disk_snapshot_info = disk_snapshot_info['disk_snapshots'][-1]
                last_disk_snapshot = get_disk_snapshot_from_info(last_disk_snapshot_info['path'],
                                                                 last_disk_snapshot_info['ident'])
                _logger.info('TokenInfoDetailByCdpToken last_disk_snapshot:{}'.format(last_disk_snapshot))
                if last_disk_snapshot.is_cdp:
                    cdp_timestamp_seconds, cdp_timestamp_microseconds = self._get_cdp_timestamp_by_ident(
                        last_disk_snapshot_info['ident'])
                    return {r'cdp_timestamp_seconds': cdp_timestamp_seconds,
                            r'cdp_timestamp_microseconds': cdp_timestamp_microseconds,
                            r'cdp_token': last_disk_snapshot.ident,
                            r'returned': True}
                else:
                    return {r'cdp_timestamp_seconds': -1,
                            r'cdp_timestamp_microseconds': -1,
                            r'cdp_token': last_disk_snapshot.ident,
                            r'returned': True}

        xlogging.raise_and_logging_error(
            r'无效的Token信息', r'TokenInfoDetailByCdpToken invalid token {}'.format(cdp_token_object),
            status.HTTP_404_NOT_FOUND)

    def _get_takeover_task_object(self, schedule_id):
        return get_object_or_404(TakeOverKVM, id=schedule_id)

    def _get_cdp_timestamp_by_ident(self, restore_time_str):
        restore_time_str = restore_time_str.replace('$', '').replace('~', '')
        time_stamp = datetime.strptime(restore_time_str, '%Y-%m-%d-%H:%M:%S.%f').timestamp()
        item = ('{:f}'.format(time_stamp)).split('.')
        return int(item[0]), int(item[1])


class HostTasksStatt(APIView):
    queryset = Host.objects.none()

    # 对指定任务们的完成状态作一次统计
    # 返回: (成功数, 已执行数)
    @staticmethod
    def getTasksStatt(taskObjs):
        doneNum = 0
        successNum = 0
        for taskObj in taskObjs:
            if taskObj.finish_datetime is not None:
                doneNum += 1
            if taskObj.successful:
                successNum += 1
        return successNum, doneNum

    # 对Cdp任务统计：(成功数, 总数)
    @staticmethod
    def getCdpsStatt(taskObjs):
        doneNum = len(taskObjs)
        successNum = 0
        for taskObj in taskObjs:
            if (taskObj.finish_datetime and taskObj.successful) or (not taskObj.finish_datetime):  # 暂停 or Cdping
                successNum += 1
        return successNum, doneNum

    # 获取Host普通备份任务们的统计: (9, 10)
    def getNormBackupTasksStatt(self, hostIdent, timeBegin):
        # 没有满足的,返回空QuerySet
        taskObjs = BackupTask.objects.filter(schedule__host__ident=hostIdent).filter(
            Q(start_datetime__gte=timeBegin) | Q(finish_datetime__isnull=True))
        if taskObjs:
            return self.getTasksStatt(taskObjs)
        return None

    # 获取Host迁移任务们的统计: (9, 10)
    def getMigrationTasksStatt(self, hostIdent, timeBegin):
        taskObjs = MigrateTask.objects.filter(source_host__ident=hostIdent).filter(
            Q(start_datetime__gte=timeBegin) | Q(finish_datetime__isnull=True))
        if taskObjs:
            return self.getTasksStatt(taskObjs)
        return None

    # 获取Host恢复任务们的统计: (9, 10)
    def getRestoreTasksStatt(self, hostIdent, timeBegin):
        taskObjs = RestoreTask.objects.filter(host_snapshot__host__ident=hostIdent).filter(
            Q(start_datetime__gte=timeBegin) | Q(finish_datetime__isnull=True)).filter(
            restore_target__htb_task__isnull=True)
        if taskObjs:
            return self.getTasksStatt(taskObjs)
        return None

    # 获取HostCdp任务们的统计: (4, 5)
    def getCdpTasksStatt(self, hostIdent, timeBegin):
        taskObjs = CDPTask.objects.filter(schedule__host__ident=hostIdent).filter(
            Q(start_datetime__gte=timeBegin) | Q(finish_datetime__isnull=True))
        if taskObjs:
            return self.getCdpsStatt(taskObjs)
        return None

    def getClusterTasksStatt(self, hostIdent, timeBegin):
        taskObjs = ClusterBackupTask.objects.filter(sub_tasks__host_snapshot__isnull=False,
                                                    sub_tasks__host_snapshot__host__ident=hostIdent).filter(
            Q(start_datetime__gte=timeBegin) | Q(finish_datetime__isnull=True)
        )
        if taskObjs:
            return self.getTasksStatt(set(taskObjs))
        return None

    def getHtbTasksStatt(self, hostIdent, timeBegin):
        taskObjs = HTBTask.objects.filter(schedule__host__ident=hostIdent, start_datetime__isnull=False).filter(
            Q(finish_datetime__isnull=False) | Q(start_datetime__gte=timeBegin)
        )
        if taskObjs:
            return self.getTasksStatt(set(taskObjs))
        return None

    def get_remote_task_status(self, hostIdent, timeBegin):
        taskObjs = RemoteBackupTask.objects.filter(schedule__host__ident=hostIdent,
                                                   start_datetime__isnull=False).filter(
            Q(finish_datetime__isnull=False) | Q(start_datetime__gte=timeBegin)
        )
        if taskObjs:
            return self.getTasksStatt(set(taskObjs))
        return None

    def get_archive_task_status(self, hostIdent, timeBegin):
        taskObjs = ArchiveTask.objects.filter(schedule__host__ident=hostIdent,
                                              start_datetime__isnull=False).filter(
            Q(finish_datetime__isnull=False) | Q(start_datetime__gte=timeBegin)
        )
        if taskObjs:
            return self.getTasksStatt(set(taskObjs))
        return None

    # todo
    def get_file_backup_task_status(self, hostIdent, timeBegin):
        taskObjs = FileBackupTask.objects.filter(schedule__host__ident=hostIdent,
                                                 start_datetime__isnull=False).filter(
            Q(finish_datetime__isnull=False) | Q(start_datetime__gte=timeBegin)
        )
        if taskObjs:
            return self.getTasksStatt(set(taskObjs))
        return None

    # 获取Host的: NormBackupTasks, MigrationTasks, RestoreTasks, CdpTasks的一个统计情况
    # 返回: {'normBackupTasks': (9,10), 'migrationTasks': (9,10), 'restoreTasks': (9,10), 'cdpTasks': (4,5)}
    def get(self, request, ident, api_request=None):
        if api_request is None:
            api_request = request.data
        timeBegin = api_request['timeBegin'] if 'timeBegin' in api_request else None
        if not timeBegin:
            return Response(status=status.HTTP_404_NOT_FOUND)

        tasksStatt = dict()
        normBackupTasksStatt = self.getNormBackupTasksStatt(ident, timeBegin)
        migrationTasksStatt = self.getMigrationTasksStatt(ident, timeBegin)
        restoreTasksStatt = self.getRestoreTasksStatt(ident, timeBegin)
        cdpTasksStatt = self.getCdpTasksStatt(ident, timeBegin)
        clusterTasksStatt = self.getClusterTasksStatt(ident, timeBegin)
        htbTasksStatt = self.getHtbTasksStatt(ident, timeBegin)
        remote_task_status = self.get_remote_task_status(ident, timeBegin)
        archive_task_status = self.get_archive_task_status(ident, timeBegin)
        file_backup_task_status = self.get_file_backup_task_status(ident, timeBegin)

        if normBackupTasksStatt:
            tasksStatt['normBackupTasks'] = normBackupTasksStatt
        if migrationTasksStatt:
            tasksStatt['migrationTasks'] = migrationTasksStatt
        if restoreTasksStatt:
            tasksStatt['restoreTasks'] = restoreTasksStatt
        if cdpTasksStatt:
            tasksStatt['cdpTasks'] = cdpTasksStatt
        if clusterTasksStatt:
            tasksStatt['clusterTasks'] = clusterTasksStatt
        if htbTasksStatt:
            tasksStatt['htbTasks'] = htbTasksStatt
        if remote_task_status:
            tasksStatt['remote_backup_task'] = remote_task_status
        if archive_task_status:
            tasksStatt['archive_task_status'] = archive_task_status
        if file_backup_task_status:
            tasksStatt['file_backup_task_status'] = file_backup_task_status

        return Response(tasksStatt, status=status.HTTP_200_OK)


class StorageNodes(APIView):
    queryset = StorageNode.objects.none()
    serializer_class = StorageNodeSerializer

    def __init__(self, **kwargs):
        super(StorageNodes, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def get(self, request, include_deleted=False):
        storage_nodes = StorageNodeLogic.get_all_nodes(include_deleted=include_deleted)
        serializer = self.serializer_class(storage_nodes, many=True)
        return Response(serializer.data)

    @staticmethod
    def _clean_old_nodes(part_uuid):
        old_nodes = StorageNode.objects.all()
        for node in old_nodes:
            if part_uuid and node.ident == part_uuid:
                _logger.warning(r'_clean_old_nodes skip node : {}'.format(node.id))
                continue
            if node.userquotas.exists():
                for obj in node.userquotas.all():
                    obj.set_deleted()
            node.delete_and_rename()

    @staticmethod
    def _save_all_nodes(nodes, part_uuid):
        index = 0
        for node in nodes:
            if part_uuid and node['node_guid'] == part_uuid:
                try:
                    StorageNode.objects.create(name='内部存储节点'.format(index), path=node['mount_path'],
                                               ident=node['node_guid'], config=json.dumps(node, ensure_ascii=False),
                                               available=True, internal=True)
                except Exception as e:
                    _logger.warning(r'_save_all_nodes : {}'.format(e))
            else:
                index += 1
                StorageNode.objects.create(name='存储节点{}'.format(index), path=node['mount_path'],
                                           ident=node['node_guid'], config=json.dumps(node, ensure_ascii=False),
                                           available=True, internal=True)

    @xlogging.LockDecorator(StorageNodeLogic.locker)
    def post(self, request):
        if (not request.user.is_superuser) or (request.data['key'] != 'kQidpmnknzvGrqpkbh7y7Vsnm5zbadvq'):
            return Response(status=status.HTTP_406_NOT_ACCEPTABLE)

        part_uuid = request.data.get('part_uuid', None)

        self._clean_old_nodes(part_uuid)

        nodes = StorageNodeLogic.initialize_all_internal_nodes(part_uuid)

        self._save_all_nodes(nodes, part_uuid)

        return Response(status=status.HTTP_201_CREATED)

    @staticmethod
    def get_max_storage_nodes():
        def my_sort(item):
            if item['linked']:
                return int(item['available_bytes'])
            else:
                return -1

        storage_nodes = StorageNodeLogic.get_all_nodes()
        storage_nodes.sort(key=my_sort, reverse=True)
        serializer = StorageNodes.serializer_class(storage_nodes[0])
        return Response(serializer.data)


class StorageNodeInfo(APIView):
    queryset = StorageNode.objects.filter(deleted=False)
    serializer_class = AlterStorageNodeInfo

    def __init__(self, **kwargs):
        super(StorageNodeInfo, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def get(self, request, node_id):
        return Response()

    @staticmethod
    @xlogging.convert_exception_to_value(None)
    def _unmount(file_system_name, mount_path):
        boxService.box_service.unmountStorageNode({'file_system_name': file_system_name, 'mount_path': mount_path})

    @staticmethod
    @xlogging.convert_exception_to_value(None)
    def _get_file_system_name(node_object):
        config = json.loads(node_object.config)
        return config['file_system_name'] if config['has_file_system'] else None

    def delete(self, request, node_id):
        node_object = _check_using_storage_node_id_valid(node_id)
        mount_path = node_object.path
        file_system_name = self._get_file_system_name(node_object)
        node_object.delete_and_rename()
        self._unmount(file_system_name, mount_path)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def put(self, request, node_id):
        node_object = _check_using_storage_node_id_valid(node_id)
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(True)

        if node_object.set_name(serializer.validated_data['name']):
            return Response(status=status.HTTP_205_RESET_CONTENT)
        else:
            xlogging.raise_and_logging_error(
                r'设置存储节点名失败，重复的存储节点名', r'StorageNodeInfo.put invalid name : {}'.format(
                    serializer.validated_data['name']), status.HTTP_409_CONFLICT)

    def post(self, request, node_id, api_request=None):
        if api_request is None:
            api_request = request.data
        node_object = _check_using_storage_node_id_valid(node_id)
        serializer = DealStorageNode(data=api_request)
        serializer.is_valid(True)

        deal_type = serializer.validated_data['type']
        if deal_type == DealStorageNode.DEAL_STORAGE_NODE_TYPE_RM_ALL:
            admin_pwd = serializer.validated_data['admin_pwd']
            if not self._is_admin_pwd(request, admin_pwd):
                xlogging.raise_and_logging_error(r'密码错误', r'invalid admin pwd : {}'.format(admin_pwd),
                                                 status.HTTP_401_UNAUTHORIZED)
            cmd = 'rm -rf {}/images/*'.format(node_object.path)
            subprocess.Popen(cmd, shell=True)
            return Response(status=status.HTTP_200_OK)

        xlogging.raise_and_logging_error(r'参数错误', r'invalid type : {}'.format(deal_type),
                                         status.HTTP_400_BAD_REQUEST)

    @staticmethod
    @xlogging.convert_exception_to_value(False)
    def _is_admin_pwd(request, admin_pwd):
        return request.user.is_superuser and request.user.check_password(admin_pwd)


class InternalStorageNodes(APIView):
    queryset = StorageNode.objects.none()
    serializer_class = StorageNodePerDeviceSerializer

    def __init__(self, **kwargs):
        super(InternalStorageNodes, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def get(self, request):
        internal_storage_nodes = StorageNodeLogic.get_internal_storage_nodes()
        serializer = self.serializer_class(internal_storage_nodes, many=True)
        return Response(serializer.data)

    @xlogging.LockDecorator(StorageNodeLogic.locker)
    def post(self, request, api_request=None):
        if api_request is None:
            api_request = request.data

        serializer = AddStorageNodeSerializer(data=api_request)
        serializer.is_valid(True)

        if StorageNode.objects.filter(name=serializer.validated_data['name']).count() != 0:
            xlogging.raise_and_logging_error(
                r'新增内部存储节点失败，重复的存储节点名', r'ExternalStorageDeviceInfo.post invalid name : {}'.format(
                    serializer.validated_data['name']), status.HTTP_409_CONFLICT)

        if serializer.validated_data['status'] == xdata.STORAGE_NODE_STATUS_USING:
            xlogging.raise_and_logging_error(r'无法加入正在使用中的存储节点', r'want to re-add storage node,why?!',
                                             status.HTTP_406_NOT_ACCEPTABLE)

        format_device = False
        mount_path = StorageNodeLogic.generate_mount_path()

        if serializer.validated_data['status'] == xdata.STORAGE_NODE_STATUS_INIT_BY_SELF:
            format_device = serializer.validated_data['force_format']
            if not format_device:
                mount_path = StorageNode.objects.get(id=serializer.validated_data['old_node_id']).path[0:-33]
        else:
            if not serializer.validated_data['force_format']:
                xlogging.raise_and_logging_error(r'该存储节点含有未知数据，加入设备列表失败，请勾选强制格式化后重试。',
                                                 r'want to add storage node without format',
                                                 status.HTTP_406_NOT_ACCEPTABLE)
            else:
                format_device = True

        node = StorageNodeLogic.add_storage_node(serializer.validated_data, mount_path, format_device)
        StorageNode.objects.create(name=serializer.validated_data['name'], path=node['mount_path'], internal=True,
                                   ident=node['node_guid'], config=json.dumps(node, ensure_ascii=False), available=True)

        return Response(status=status.HTTP_201_CREATED)


class ExternalStorageDevices(APIView):
    queryset = ExternalStorageDeviceConnection.objects
    serializer_class = AddExternalStorageDeviceSerializer

    def __init__(self, **kwargs):
        super(ExternalStorageDevices, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def get(self, request):
        return Response()

    def _get_device_by_ip_and_port(self, ip, port):
        return self.queryset.filter(ip=ip, port=port).first()

    @staticmethod
    def login_device(params):
        return boxService.box_service.loginExternalDevice(params['ip'], params['port'], params['use_chap'],
                                                          params['user_name'], params['password'])

    @staticmethod
    @xlogging.convert_exception_to_value(None)
    def logout_device(iqn):
        boxService.box_service.logoutExternalDevice(iqn)

    @staticmethod
    def refresh_device(iqn):
        boxService.box_service.refreshExternalDevice(iqn)

    def post(self, request, api_request=None):
        if api_request is None:
            api_request = request.data

        serializer = self.serializer_class(data=api_request)
        serializer.is_valid(True)
        params = serializer.validated_data.copy()
        force = params.pop('force', False)

        device_object = self._get_device_by_ip_and_port(ip=params['ip'], port=params['port'])
        if (device_object is not None) and (not device_object.deleted):
            if json.dumps(params, ensure_ascii=False) == device_object.params:
                self.refresh_device(device_object.last_iqn)
                device_object.update_last_available_datetime()
                return Response({'id': device_object.id, 'iqn': device_object.last_iqn},
                                status=status.HTTP_201_CREATED)
            elif not force:
                return Response(status=status.HTTP_429_TOO_MANY_REQUESTS)
            else:
                device_object.set_deleted()
                self.logout_device(device_object.last_iqn)

        iqn = self.login_device(params)

        if device_object is None:
            device_object = ExternalStorageDeviceConnection.objects.create(
                ip=params['ip'], port=params['port'], last_iqn=iqn, params=json.dumps(params, ensure_ascii=False),
                last_available_datetime=datetime.now())
        else:
            device_object.update_iqn_and_params(iqn, params)

        return Response({'id': device_object.id, 'iqn': device_object.last_iqn}, status=status.HTTP_201_CREATED)


class ExternalStorageDeviceInfo(APIView):
    queryset = ExternalStorageDeviceConnection.objects
    serializer_class = StorageNodePerDeviceSerializer

    def __init__(self, **kwargs):
        super(ExternalStorageDeviceInfo, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def _get_device_object(self, device_id):
        try:
            return self.queryset.get(id=device_id)
        except ExternalStorageDeviceConnection.DoesNotExist:
            xlogging.raise_and_logging_error(
                r'无效的外部存储设备', r'ExternalStorageDeviceInfo._get_device_object {} failed'.format(device_id),
                status.HTTP_404_NOT_FOUND)

    def get(self, request, device_id, api_request=None):
        if api_request is None:
            api_request = request.query_params

        refresh = api_request.get('refresh', 'False') == 'True'
        device_object = self._get_device_object(device_id)

        if refresh:
            response = ExternalStorageDevices().post(request, json.loads(device_object.params))
            if not status.is_success(response.status_code):
                return response

        external_storage_nodes = StorageNodeLogic.get_external_storage_nodes(device_object)
        serializer = self.serializer_class(external_storage_nodes, many=True)
        return Response(serializer.data)

    def post(self, request, device_id, api_request=None):
        if api_request is None:
            api_request = request.data

        serializer = AddStorageNodeSerializer(data=api_request)
        serializer.is_valid(True)

        if StorageNode.objects.filter(name=serializer.validated_data['name']).count() != 0:
            xlogging.raise_and_logging_error(
                r'新增外部存储节点失败，重复的存储节点名', r'ExternalStorageDeviceInfo.post invalid name : {}'.format(
                    serializer.validated_data['name']), status.HTTP_409_CONFLICT)

        if serializer.validated_data['status'] == xdata.STORAGE_NODE_STATUS_USING:
            xlogging.raise_and_logging_error(r'无法加入正在使用中的存储节点', r'want to re-add storage node,why?!',
                                             status.HTTP_406_NOT_ACCEPTABLE)

        device_object = self._get_device_object(device_id)
        response = ExternalStorageDevices().post(request, json.loads(device_object.params))
        if not status.is_success(response.status_code):
            return response

        format_device = False
        mount_path = StorageNodeLogic.generate_mount_path()

        if serializer.validated_data['status'] == xdata.STORAGE_NODE_STATUS_INIT_BY_SELF:
            format_device = serializer.validated_data['force_format']
            if not format_device:
                mount_path = StorageNode.objects.get(id=serializer.validated_data['old_node_id']).path[0:-33]
        else:
            if not serializer.validated_data['force_format']:
                xlogging.raise_and_logging_error(r'该存储节点含有未知数据，加入设备列表失败，请勾选强制格式化后重试。',
                                                 r'want to add storage node without format',
                                                 status.HTTP_406_NOT_ACCEPTABLE)
            else:
                format_device = True

        node = StorageNodeLogic.add_storage_node(serializer.validated_data, mount_path, format_device)
        StorageNode.objects.create(name=serializer.validated_data['name'], path=node['mount_path'], internal=False,
                                   ident=node['node_guid'], config=json.dumps(node, ensure_ascii=False), available=True)

        return Response(status=status.HTTP_201_CREATED)

    def delete(self, request, device_id, api_request=None):
        if api_request is None:
            api_request = request.query_params

        timeouts = int(api_request.get('timeouts', '360'))
        device_object = self._get_device_object(device_id)
        if device_object.deleted:
            return Response(status=status.HTTP_204_NO_CONTENT)

        return StorageNodeLogic.delete_external_device_object(device_object, timeouts)


# 创建普通用户，并分配所有在线Host
class CreateNormUser(APIView):
    queryset = StorageNode.objects.none()

    @staticmethod
    def post(request):
        if not request.user.is_superuser:
            return Response(status=status.HTTP_406_NOT_ACCEPTABLE)

        username = request.data['name']
        password = request.data['pswd']
        # 创建普通用户
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            user = User()
            user.username = username
            user.is_superuser = False
            user.is_staff = False
            user.is_active = True
            user.set_password(password)
            user.save()

        # 用户扩展信息
        try:
            profile = UserProfile.objects.get(user_id=user.id)
        except UserProfile.DoesNotExist:
            profile = UserProfile()
            profile.user_id = user.id
            profile.quota = -1
            profile.modules = 127
            profile.save()

        # 为创建的用户，分配所有备份机
        hosts_online = Host.objects.filter(login_datetime__isnull=False)
        if not hosts_online:
            return Response(status=status.HTTP_404_NOT_FOUND)

        for host_obj in hosts_online:
            host_obj.user_id = user.id
            host_obj.save(update_fields=['user_id'])

        # 返回从属的备份机数
        return Response(data={"slave_hosts": len(hosts_online)}, status=status.HTTP_201_CREATED)


class HostSnapshotShareDeleteProc():
    def __init__(self, cmd_type, check_info):
        self.ret_value = self.delShare(cmd_type, check_info)

    def rm_link_dir(self, share_object):
        dir_uuid = json.loads(share_object.ext_info).get('dir_uuid', '')
        link_dir = os.path.join(xdata.FILE_BROWSER_LINK_PREFIX, dir_uuid)
        if dir_uuid and os.path.exists(link_dir):
            HostSnapshotShareAdd.rm_link(link_dir)
        return None

    def delShare(self, cmd_type, check_info):
        _logger.debug('HostSnapshotShareDeleteProc {} {}'.format(cmd_type, check_info))
        try:
            if cmd_type == 'share_id':
                new_share_object = HostSnapshotShare.objects.filter(id=check_info)
                if new_share_object.exists():
                    self.rm_link_dir(new_share_object.last())
            elif cmd_type == 'host_id':
                mhostsnapshot_object = HostSnapshot.objects.filter(host_id=check_info)
                _logger.debug('HostSnapshotShareDeleteProc get {} host snapshot'.format(mhostsnapshot_object.count()))
                new_share_object = None
                if mhostsnapshot_object.count() > 0:
                    for i in range(mhostsnapshot_object.count()):
                        if not new_share_object:
                            new_share_object = HostSnapshotShare.objects.filter(
                                host_snapshot_id=mhostsnapshot_object[i].id)
                        else:
                            new_share_object += HostSnapshotShare.objects.filter(
                                host_snapshot_id=mhostsnapshot_object[i].id)
                            # new_share_object = list(filter(lambda e: e.host_snapshot_id in
                            #                                          HostSnapshot.objects.filter(host_id=check_info).values_list('id'),
                            #                                HostSnapshotShare.objects.all()))
                            #
            elif cmd_type == 'samba_user':
                new_share_object = HostSnapshotShare.objects.filter(samba_user=check_info)

            else:
                _logger.debug('HostSnapshotShareDeleteProc input {} {},match None'.format(cmd_type, check_info))
                new_share_object = None

            if new_share_object is not None:
                object_num = new_share_object.count()
                if object_num <= 0:
                    return '查询值为空'
                _logger.debug('HostSnapshotShareDeleteProc get {} object'.format(object_num))
                share_info_list = list()
                for i in range(object_num):
                    share_info_list.append([new_share_object[i].samba_user,
                                            new_share_object[i].dirinfo,
                                            new_share_object[i].locked_files])
                new_share_object.delete()
                for i in range(len(share_info_list)):
                    try:
                        delHostShareMonitor(share_info_list[i][1])
                        LockDiskFilesOper(2, share_info_list[i][2])
                        boxService.box_service.CmdCtrl(
                            iceGetCmd('del_host', 'break', [share_info_list[i][0], share_info_list[i][1]]), 30)
                        _logger.debug('del samba_user {} host {} success'.format(share_info_list[i][0],
                                                                                 share_info_list[i][1]))
                    except Exception as e:
                        _logger.debug(
                            'del share host {} failed,except {} {}'.format(share_info_list[i][1],
                                                                           e, traceback.format_exc()))
            else:
                return '查询值为None'

            return 0
        except Exception as e:
            _logger.error(
                "HostSnapshotShareDeleteProc failed type {}, info {}, except {} {}".format(cmd_type, check_info, e,
                                                                                           traceback.format_exc()))
            return 'except'


class HostSnapshotShareDelete(APIView):
    queryset = HostSnapshotShare.objects.none()

    def __init__(self, **kwargs):
        super(HostSnapshotShareDelete, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def delete(self, request, shared_host_snapshot_id, api_request=None):
        cmdtype = "share_id"
        newproc = HostSnapshotShareDeleteProc(cmdtype, shared_host_snapshot_id)
        if newproc.ret_value != 0:
            return Response(r'删除共享失败,{},{}:{}'.format(newproc.ret_value, cmdtype, shared_host_snapshot_id),
                            status=status.HTTP_404_NOT_FOUND)
        else:
            return Response(r'删除共享成功,{}:{}'.format(cmdtype, shared_host_snapshot_id), status=status.HTTP_200_OK)


class HostSnapshotShareUserDelete(APIView):
    queryset = HostSnapshotShare.objects.none()

    def __init__(self, **kwargs):
        super(HostSnapshotShareUserDelete, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def delete(self, request, samba_user, api_request=None):
        cmdtype = "samba_user"
        newproc = HostSnapshotShareDeleteProc(cmdtype, samba_user)
        if newproc.ret_value != 0:
            return Response(r'删除共享失败,{},{}:{}'.format(newproc.ret_value, cmdtype, samba_user),
                            status=status.HTTP_404_NOT_FOUND)
        else:
            return Response(r'删除共享成功,{}:{}'.format(cmdtype, samba_user), status=status.HTTP_200_OK)


class HostSnapshotShareHostDelete(APIView):
    queryset = HostSnapshotShare.objects.none()

    def __init__(self, **kwargs):
        super(HostSnapshotShareHostDelete, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def delete(self, request, host_id, api_request=None):
        cmdtype = "host_id"
        newproc = HostSnapshotShareDeleteProc(cmdtype, host_id)
        if newproc.ret_value != 0:
            return Response(r'删除共享失败,{},{}:{}'.format(newproc.ret_value, cmdtype, host_id),
                            status=status.HTTP_404_NOT_FOUND)
        else:
            return Response(r'删除共享成功,{}:{}'.format(cmdtype, host_id), status=status.HTTP_200_OK)


class HostSnapshotShareQuery(APIView):
    queryset = HostSnapshotShare.objects.none()

    def __init__(self, **kwargs):
        super(HostSnapshotShareQuery, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def get(self, request, api_request=None):
        try:
            if request.user.is_superuser:
                new_share = HostSnapshotShare.objects.all()
            else:
                new_share = HostSnapshotShare.objects.filter(login_user=request.user)

            if new_share.count() > 0:
                new_share = new_share.filter(~Q(share_status='init'))
            if new_share.count() > 0:
                _logger.debug("query share from login user {},info {}".format(request.user, new_share))
                serializer = list()
                for i in range(new_share.count()):
                    serializer.append(HostSnapshotShareSerializer(new_share[i]).data)
                _logger.debug("ShareQuery serializer {}".format(serializer))
                return Response(serializer)
            else:
                return Response(status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            _logger.error(
                "query disk share failed,login user {} except {} {}".format(request.user, e, traceback.format_exc()))
            xlogging.raise_and_logging_error('query disk share failed,login user:{}'.format(request.user),
                                             'query disk share failed,login user {}'.format(request.user),
                                             status.HTTP_404_NOT_FOUND)


class HostSnapshotShareAdd(APIView):
    queryset = HostSnapshotShare.objects.none()

    def __init__(self, **kwargs):
        super(HostSnapshotShareAdd, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def dirname_check(self, in_dir_name):
        in_dir_name = in_dir_name.replace(':', '-')
        in_dir_name = in_dir_name.replace('\\', '-')
        in_dir_name = in_dir_name.replace('?', '-')
        in_dir_name = in_dir_name.replace('*', '-')
        in_dir_name = in_dir_name.replace('<', '-')
        in_dir_name = in_dir_name.replace('>', '-')
        in_dir_name = in_dir_name.replace('|', '-')
        in_dir_name = in_dir_name.replace(' ', '')
        return in_dir_name

    @staticmethod
    def mklink_and_update_object(new_share_object):
        ext_info = json.loads(new_share_object.ext_info)
        if ext_info.get('dir_uuid', ''):
            return None
        else:
            dir_uuid = uuid.uuid4().hex
            link_dir = os.path.join(xdata.FILE_BROWSER_LINK_PREFIX, dir_uuid)
            src_dir = os.path.join('/home/', new_share_object.samba_user, new_share_object.samba_user,
                                   new_share_object.dirinfo)
            HostSnapshotShareAdd.rm_link(link_dir)
            os.makedirs(link_dir, exist_ok=True)
            HostSnapshotShareAdd._mk_link(src_dir, link_dir)
            ext_info['dir_uuid'] = dir_uuid
            new_share_object.ext_info = json.dumps(ext_info, ensure_ascii=False)
            new_share_object.save(update_fields=['ext_info'])

    @staticmethod
    def _mk_link(src_dir, dst_dir):
        cmd = 'ln -s "{}" "{}"'.format(src_dir, dst_dir)
        returned_code, lines = boxService.box_service.runCmd(cmd, True)
        if returned_code != 0:
            xlogging.raise_and_logging_error(r'创建链接失败',
                                             'create link fail : {} {}'.format(returned_code, lines))

    @staticmethod
    def rm_link(link_name):
        if not link_name.startswith(xdata.FILE_BROWSER_LINK_PREFIX):
            return None
        cmd = 'rm -rf "{}"'.format(link_name)
        boxService.box_service.runCmd(cmd, True)

    @staticmethod
    def _chmod_dir(new_share_object):
        user_name = new_share_object.samba_user
        cmd = 'chmod 755 -R {}'.format(os.path.join('/home/{}'.format(user_name)))
        boxService.box_service.runCmd(cmd, True)

    def post(self, request, api_request=None):
        _FILETYPE_NORMAL = 'normal'
        _FILETYPE_CDP = 'cdp'
        new_share_object = None
        lock_file = False
        logic_cmd = False
        operating_share_info = ''
        locked_files = ''
        samba_user = ''
        host_snapshot_id = None
        dirinfo = ''
        try:
            if api_request is None:
                api_request = request.data
            _logger.info('HostSnapshotShareAdd post,data {}'.format(api_request))
            host_snapshot_id = api_request['host_snapshot_id'] if 'host_snapshot_id' in api_request else None
            timestamp = api_request['timestamp'] if 'timestamp' in api_request else None
            filetype = api_request['filetype'] if 'filetype' in api_request else None
            samba_user = api_request['samba_user'] if 'samba_user' in api_request else None
            samba_pwd = api_request['samba_pwd'] if 'samba_pwd' in api_request else None
            user_id = api_request['user_id']
            if not host_snapshot_id:
                _logger.error('do not have shared_host_snapshot_id info')
                return Response(status=status.HTTP_404_NOT_FOUND)
            if not timestamp:
                _logger.error('do not have timestamp info')
                return Response(status=status.HTTP_404_NOT_FOUND)
            if not filetype or (filetype != _FILETYPE_NORMAL and filetype != _FILETYPE_CDP):
                _logger.error('filetype info invalid {}'.format(filetype))
                return Response(status=status.HTTP_404_NOT_FOUND)
            if not samba_user:
                _logger.error('do not have samba_user info')
                return Response(status=status.HTTP_404_NOT_FOUND)
            if not samba_pwd:
                _logger.error('do not have samba_pwd info')
                return Response(status=status.HTTP_404_NOT_FOUND)
            mhost_share_object = _check_host_share_exist(host_snapshot_id, timestamp)
            if mhost_share_object is not None:
                serializer = HostSnapshotShareSerializer(mhost_share_object)
                return Response(serializer.data, status=status.HTTP_200_OK)

            mhost_snapshot_object = _check_host_snapshot_id_valid(host_snapshot_id)
            operating_share_info = str(mhost_snapshot_object.id) + '_' + timestamp
            if operating_host_dict(2, operating_share_info) == 0:
                _logger.error('operating_share_info already:{}'.format(operating_share_info))
                return Response(status=status.HTTP_404_NOT_FOUND)
            host_start_time = timestamp
            if filetype == _FILETYPE_CDP:
                timestamp = xdatetime.string2datetime(timestamp)
                _check_host_snapshot_object_valid(mhost_snapshot_object, timestamp)
                timestamp = timestamp.timestamp()
                # _logger.debug('cdp timestamp {}'.format(timestamp))
            else:
                _check_host_snapshot_object_valid(mhost_snapshot_object, None)
            if samba_user is None or samba_pwd is None:
                mshared_snapshots = HostSnapshotShare.objects.filter(login_user=request.user)
                if len(mshared_snapshots) == 0:
                    samba_user = ",".join(random.sample('abcdefghijklmnopqrstuvwxyz', 6)).replace(',', '')
                    samba_pwd = str(random.randint(100000, 999999))
                else:
                    samba_user = mshared_snapshots[0].samba_user
                    samba_pwd = mshared_snapshots[0].samba_pwd
            host_display_name = re.sub(r'[\W_[\u4e00-\u9fa5]]+', '', mhost_snapshot_object.host.display_name,
                                       flags=re.LOCALE)
            host_snapshot_type = filetype

            host_finish_time = mhost_snapshot_object.finish_datetime
            # dirinfo = samba_user + '/' + str(
            #     mhost_snapshot_object.id) + '_' + host_display_name + '/' + filetype + '_' + host_start_time
            if filetype == _FILETYPE_CDP:
                dirinfo = host_display_name + '_' + str(mhost_snapshot_object.id) \
                          + '_' + filetype + '_' + host_start_time
            else:
                dirinfo = host_display_name + '_' + str(mhost_snapshot_object.id) + '_' + host_start_time
            dirinfo = self.dirname_check(dirinfo)

            _logger.debug('get host snapshot id {},host name {}'.format(host_snapshot_id, host_display_name))

            mdisk_snapshots = _check_disk_snapshot_exist(host_snapshot_id)
            mdisk_snapshots_len = len(mdisk_snapshots)
            if mdisk_snapshots_len <= 0:
                xlogging.raise_and_logging_error('客户端快照没有硬盘信息:{}'.format(host_snapshot_id),
                                                 'invalid host snapshot id {}'.format(host_snapshot_id),
                                                 status.HTTP_404_NOT_FOUND)

            _logger.debug('get {} disk snapshot'.format(mdisk_snapshots_len))

            ext_info = json.loads(mhost_snapshot_object.ext_info)
            system_infos = ext_info['system_infos']
            include_ranges = ext_info['include_ranges']
            system = system_infos['System']
            sys_os_class_type = 'linux' if 'LINUX' in (system['SystemCaption'].upper()) else 'windows'
            disk_index_info = None

            cmddict = {'ostype': sys_os_class_type, 'username': samba_user, 'userpwd': samba_pwd,
                       'linux_storage': '', 'include_ranges': include_ranges, 'hostname': dirinfo, 'disklist': [],
                       'windows_volumes': ext_info['system_infos'].get('volumes', list())}
            if sys_os_class_type == 'linux':
                cmddict['linux_storage'] = system_infos['Storage']

            disk_index_info = ext_info['disk_index_info']
            for index_info in disk_index_info:
                index_info['disk_ident'] = \
                    DiskSnapshot.objects.get(ident=index_info['snapshot_disk_ident']).disk.ident

            for i in range(mdisk_snapshots_len):
                if filetype == _FILETYPE_CDP:
                    disk_ident = mdisk_snapshots[i].disk.ident
                    disk_snapshot_ident, restore_timestamp = \
                        GetDiskSnapshot.query_cdp_disk_snapshot_ident(mhost_snapshot_object, disk_ident,
                                                                      timestamp)
                    if disk_snapshot_ident is None or restore_timestamp is None:
                        disk_snapshot_ident = GetDiskSnapshot.query_normal_disk_snapshot_ident(mhost_snapshot_object,
                                                                                               disk_ident)
                        if disk_snapshot_ident is None:
                            _logger.error(
                                'no valid cdp disk snapshot,and get normal failed {} {} {}'.format(
                                    mhost_snapshot_object.id,
                                    disk_ident,
                                    timestamp))
                            continue
                        _logger.warning('no valid cdp disk snapshot use normal snapshot : {} {} {} {}'.format(
                            mhost_snapshot_object.id, disk_ident, timestamp, disk_snapshot_ident))
                        restore_timestamp = timestamp
                    else:
                        _logger.debug(
                            'get valid cdp disk snapshot {} {} {} {}'.format(mhost_snapshot_object.id, disk_ident,
                                                                             timestamp, disk_snapshot_ident))

                else:
                    disk_snapshot_ident, restore_timestamp = mdisk_snapshots[i].ident, timestamp
                disk_infos = getDiskInfo(disk_snapshot_ident, restore_timestamp)
                mdick = dict()
                mdick['disksnapshots'] = list()

                disk_snapshot_object = disk_infos[0]
                while disk_snapshot_object:
                    if disk_snapshot_object.host_snapshot is not None:
                        break
                    else:
                        disk_snapshot_object = disk_snapshot_object.parent_snapshot
                else:
                    xlogging.raise_and_logging_error('find disk ident failed:{}'.format(host_snapshot_id),
                                                     'find disk ident failed', status.HTTP_404_NOT_FOUND)
                for info in disk_index_info:
                    if disk_snapshot_object.disk.ident == info['disk_ident']:
                        mdick['diskid'] = info['snapshot_disk_index']
                        break
                else:
                    xlogging.raise_and_logging_error('find disk id failed:{}'.format(host_snapshot_id),
                                                     'find disk id failed', status.HTTP_404_NOT_FOUND)

                if sys_os_class_type == 'windows':
                    mdick['diskdir'] = 'disk' + str(mdick['diskid'])
                else:
                    mdick['diskdir'] = '/'

                for j in range(len(disk_infos[1])):
                    if locked_files != '':
                        locked_files += '::'
                    locked_files += disk_infos[1][j].path + ';' + disk_infos[1][j].snapshot + ';'
                    mdick['disksnapshots'].append({'path': disk_infos[1][j].path, 'ident': disk_infos[1][j].snapshot})
                cmddict['disklist'].append(mdick)

            cmdlist = [iceCmdInit('add_share', 'break', cmddict)]
            mjson = json.dumps(cmdlist)
            _logger.debug('start logic cmd list {}'.format(mjson))

            temp_share = HostSnapshotShare.objects.get_or_create(login_user=User.objects.get(id=user_id),
                                                                 samba_user=samba_user,
                                                                 samba_pwd=samba_pwd,
                                                                 samba_url='',
                                                                 share_status='init',
                                                                 share_start_time=datetime.now(),
                                                                 host_display_name=host_display_name,
                                                                 host_snapshot_type=host_snapshot_type,
                                                                 host_start_time=host_start_time,
                                                                 host_finish_time=host_finish_time,
                                                                 host_snapshot_id=host_snapshot_id,
                                                                 dirinfo=dirinfo,
                                                                 locked_files="")
            if temp_share[1]:
                new_share_object = temp_share[0]
                mlist = locked_files.split('::')
                locked_files = ''
                for i in range(len(mlist)):
                    if locked_files != '':
                        locked_files += '::'
                    locked_files += mlist[i] + 'shared_' + str(new_share_object.id)
                _logger.debug('create init share success {} {}'.format(dirinfo, locked_files))
                lock_file = True
                LockDiskFilesOper(1, locked_files)

            else:
                _logger.debug('create init share failed {} {}'.format(dirinfo, locked_files))
                operating_host_dict(4, operating_share_info)
                return Response(status=status.HTTP_404_NOT_FOUND)

            # 执行之前进行一次清理
            _logger.info('HostSnapshotShareAdd before add do clean, dir:{}'.format(dirinfo))
            boxService.box_service.CmdCtrl(iceGetCmd('del_host', 'break', [samba_user, dirinfo]))  # 同步调用
            retstr = boxService.box_service.CmdCtrl(mjson)
            retval = json.loads(retstr)
            _logger.debug('logice return {}'.format(retval))
            for i in range(len(retval)):
                if retval[i][0] == 0 and retval[i][1][0] == 0:
                    _logger.debug('add share logic success,ret {}'.format(retval[i][1]))
                    samba_url = retval[i][1][1]
                    logic_cmd = True
                    break
            else:
                boxService.box_service.CmdCtrl(iceGetCmd('del_host', 'break', [samba_user, dirinfo]), 30)
                operating_host_dict(4, operating_share_info)
                LockDiskFilesOper(2, locked_files)
                lock_file = False
                new_share_object.delete()
                share_db = False
                return Response(data=r'添加共享失败', status=status.HTTP_404_NOT_FOUND)

            _logger.debug(
                "user {} pwd {} url {} host_snapshot_id {} hostname {}".format(samba_user, samba_pwd, samba_url,
                                                                               host_snapshot_id,
                                                                               host_display_name))
            HostSnapshotShare.objects.filter(dirinfo=dirinfo).update(samba_url=samba_url,
                                                                     share_status='ok',
                                                                     locked_files=locked_files)

            _logger.debug("create new share success {},{},{},{},{},{},{},{}\n".format(samba_user, samba_pwd, samba_url,
                                                                                      host_display_name,
                                                                                      host_snapshot_type,
                                                                                      host_start_time, host_snapshot_id,
                                                                                      dirinfo))
            new_share_object = HostSnapshotShare.objects.get(id=new_share_object.id)
            serializer = HostSnapshotShareSerializer(new_share_object)
            addHostShareMonitor(dirinfo, 'ok')
            operating_host_dict(4, operating_share_info)

            return Response(serializer.data, status=status.HTTP_201_CREATED)

        except Exception as e:
            _logger.error("create new_share failed,host snapshot {} except {} {}".format(host_snapshot_id, e,
                                                                                         traceback.format_exc()))
            if new_share_object is not None:
                new_share_object.delete()
            if lock_file:
                LockDiskFilesOper(2, locked_files)
            if logic_cmd:
                boxService.box_service.CmdCtrl(iceGetCmd('del_host', 'break', [samba_user, dirinfo]), 30)
            if operating_share_info != '':
                operating_host_dict(4, operating_share_info)
            return Response(data=r'添加共享失败，异常：{}，host_snapshot_id={}'.format(e, host_snapshot_id),
                            status=status.HTTP_404_NOT_FOUND)


# 用户配额管理
class QuotaManage(APIView):
    queryset = UserQuota.objects.none()

    def __init__(self, **kwargs):
        super(QuotaManage, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    @staticmethod
    def _check_user_id(user_id):
        is_valid = User.objects.filter(id=user_id).exists()
        if not is_valid:
            xlogging.raise_and_logging_error('用户不存在', 'there is not user exists')

    # 初始创建用户配额
    def post(self, request, api_request=None):
        if not api_request:
            api_request = request.data

        storage_node_id = api_request['node_id']
        user_id = api_request['user_id']
        self._check_user_id(user_id)
        quota_size = api_request['quota_size']  # MB

        # 不可信的字段，需要实时统计
        available_size = UserQuotaTools(storage_node_id, user_id, quota_size).get_user_available_storage_size_in_node()
        UserQuota.objects.create(storage_node_id=storage_node_id,
                                 user_id=user_id,
                                 quota_size=quota_size,
                                 caution_size=api_request['caution_size'],
                                 available_size=available_size)
        return Response(status=status.HTTP_201_CREATED)

    # 获取指定节点的，所有用户配额信息
    def get(self, request, api_request=None):
        if api_request is None:
            api_request = request.query_params

        node_id = api_request['node_id']
        quota_objs = UserQuota.objects.filter(storage_node_id=node_id).filter(deleted=False)  # 该节点下所有的用户配额

        if quota_objs:
            node_users_quota = list()
            for quota_obj in quota_objs:
                # 该节点离线
                if not StorageNode.objects.get(id=quota_obj.storage_node_id).available:
                    available_size = 0
                else:
                    available_size = UserQuotaTools(quota_obj.storage_node_id, quota_obj.user_id,
                                                    quota_obj.quota_size).get_user_available_storage_size_in_node()
                node_users_quota.append({'node_id': node_id,
                                         'user_id': quota_obj.user_id,
                                         'quota_id': quota_obj.id,
                                         'username': User.objects.get(id=quota_obj.user_id).username,
                                         'quota_total': quota_obj.quota_size,
                                         'caution_size': quota_obj.caution_size,
                                         'available_size': available_size})
            return Response(data=node_users_quota, status=status.HTTP_200_OK)
        return Response(data=None, status=status.HTTP_404_NOT_FOUND)

    # 编辑用户配额
    def put(self, request, api_request=None):
        if api_request is None:
            api_request = request.query_params
        quota_id = api_request['quota_id']
        quota_obj = UserQuota.objects.get(id=quota_id)

        if quota_obj:
            quota_obj.quota_size = api_request['quota_size']
            quota_obj.caution_size = api_request['caution_size']
            quota_obj.save(update_fields=['quota_size', 'caution_size'])
            return Response(status=status.HTTP_202_ACCEPTED)
        return Response(status=status.HTTP_404_NOT_FOUND)

    # 删除用户配额
    def delete(self, request, api_request=None):
        if api_request is None:
            api_request = request.query_params

        quota_id = api_request['quota_id']
        UserQuota.objects.get(id=quota_id).set_deleted()
        return Response(status=status.HTTP_204_NO_CONTENT)


# 还原某快照点时，检查Pe的硬件信息
class TargetHardware(APIView):
    queryset = RestoreTarget.objects.none()

    def __init__(self, **kwargs):
        super(TargetHardware, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    @staticmethod
    @xlogging.convert_exception_to_value(True)
    def not_cc0601_cc0604_hardware(hardware):
        hardwareIds = hardware.HWIds + hardware.CompatIds
        t = ','.join(hardwareIds).upper()
        return '&CC_0601' not in t and '&CC_0604' not in t

    @staticmethod
    def filter_exclude_cc0601_cc0604_hardware(hardwares):
        return list(filter(TargetHardware.not_cc0601_cc0604_hardware, hardwares))

    # 在源中查找target_hardware
    @staticmethod
    def is_target_hardware_in_source_hardwares(target_hardware, source_hardwares):
        for source_hardware in source_hardwares:
            if TargetHardware._same_hardware(source_hardware, target_hardware):
                _logger.info(
                    'is_target_hardware_in_source_hardwares same hardware between {} and {}'.format(target_hardware,
                                                                                                    source_hardware))
                break
        else:
            return False
        return True

    @staticmethod
    def _same_hardware(src, dst):
        if TargetHardware._is_rand_name_device(dst) or TargetHardware._is_rand_name_device(src):
            if src['szDeviceInstanceID'].startswith('VMBUS\\') and dst['szDeviceInstanceID'].startswith('VMBUS\\'):
                return True
            if src['szDeviceInstanceID'].startswith('ACPI\\VMBUS\\') and dst['szDeviceInstanceID'].startswith(
                    'ACPI\\VMBUS\\'):
                return True
            return False
        else:
            src_key = '-'.join(src['szDeviceInstanceID'].split('\\')[0:2])
            dst_key = '-'.join(dst['szDeviceInstanceID'].split('\\')[0:2])
            return src_key == dst_key

    @staticmethod
    def _is_rand_name_device(hardware):
        invalid_id_list = ('VMBUS\\', 'ACPI\\VMBUS\\')
        return hardware['szDeviceInstanceID'].upper().startswith(invalid_id_list)

    @staticmethod
    def fill_hardwares(hardware_type, hardware_stack, hardwares):
        find_pci = False
        for hardware in hardware_stack:
            if PeRestore.is_qemu_hardware(hardware):
                return
            elif PeRestore.is_vwifimp_service(hardware):
                return
            elif PeRestore.is_pci_service(hardware):
                return
            elif PeRestore.is_usb_hub_service(hardware):
                return
            elif PeRestore.is_usb_hardware(hardware):
                return
            elif PeRestore.is_pci_hardware(hardware):
                if PeRestore.is_net_adapter_and_not_ethernet(hardware):
                    return
                find_pci = True
            elif find_pci:
                return
            else:
                pass

            hardware['Type'] = hardware_type
            hardwares.append(hardware)

        if not find_pci:
            xlogging.raise_and_logging_error(r'无效的设备栈信息', r'invalid hardware stack: {}'.format(hardware_stack))

    def check_hardware_match_blacklistfile(self, sys_os_type, sys_os_bit, pe_host):
        # 目标硬件信息
        pe_host_detail = PeHostSessionInfo().get(request=None, ident=pe_host.ident).data
        if pe_host_detail is not None:
            pe_network_ctr_stacks = pe_host_detail['network_controller_hardware_stacks']
            pe_disk_ctr_stacks = pe_host_detail['disk_controller_hardware_stacks']
            # 过滤目标硬件
            target_hardwares = list()
            self.generate_hardwares_by_hardwares_stacks('network', pe_network_ctr_stacks, target_hardwares)
            self.generate_hardwares_by_hardwares_stacks('disk', pe_disk_ctr_stacks, target_hardwares)
            _logger.info(
                'sys_os_type:{},sys_os_bit:{},target_hardwares:{}'.format(sys_os_type, sys_os_bit, target_hardwares))
            system_type = sys_os_type.split('.')
            check_sys_os_type = str(int(system_type[0])) + '.' + str(int(system_type[1]))
            with open(HARDWARE_BLACKLIST_FILE, 'r', encoding='utf-8') as f:
                hardhare_blacklist = json.load(f)
            _logger.info('hardhare_blacklist:{}'.format(hardhare_blacklist))
            for black in hardhare_blacklist:
                if (black['sys_system_type'].strip() == check_sys_os_type) and (
                        black['sys_system_bit'] == int(sys_os_bit)):
                    _logger.info('black_sys_system_bit:{},sys_os_bit{}'.format(black['sys_system_bit'], sys_os_bit))
                    for hardware in target_hardwares:
                        result = [k for k in dict(hardware)['HWIds'] if black['hardware_id'] in k]
                        if len(result) != 0:
                            _logger.info('hardware not match:{}'.format(result))
                            xlogging.raise_and_logging_error(black['error_info'], 'hardware not match', )
        else:
            pass

    # 过滤生成目标硬件
    def generate_hardwares_by_hardwares_stacks(self, hardware_type, hardware_stacks, hardwares):
        PeRestore.pop_checked_stacks(hardware_stacks, [PeRestore.is_no_pci_in_stack])
        for hardware_stack in hardware_stacks:
            self.fill_hardwares(hardware_type, hardware_stack, hardwares)

    # 比较目标，备份点的硬件环境，返回目标机独有的硬件
    def get_target_particular_hardwares_by_comparing_with_source(self, host_snapshot, pe_host):
        # 源硬件信息
        src_ext_info = json.loads(host_snapshot.ext_info)
        src_disk_controller_hardware = src_ext_info['disk_controller_hardware']
        src_network_controller_hardware = src_ext_info['network_controller_hardware']

        # 目标硬件信息
        pe_host_detail = PeHostSessionInfo().get(request=None, ident=pe_host.ident).data
        pe_network_ctr_stacks = pe_host_detail['network_controller_hardware_stacks']
        pe_disk_ctr_stacks = pe_host_detail['disk_controller_hardware_stacks']

        # 过滤目标硬件
        target_hardwares = list()
        self.generate_hardwares_by_hardwares_stacks('network', pe_network_ctr_stacks, target_hardwares)
        self.generate_hardwares_by_hardwares_stacks('disk', pe_disk_ctr_stacks, target_hardwares)

        # 滤出目标独有的硬件信息
        target_particular = list()
        for target_hardware in target_hardwares:
            if target_hardware['Type'] == 'network':
                source_hardwares = list(
                    filter(lambda src_hardware: src_hardware['parentDevLevel'] == target_hardware['parentDevLevel'],
                           src_network_controller_hardware))
            elif target_hardware['Type'] == 'disk':
                source_hardwares = list(
                    filter(lambda src_hardware: src_hardware['parentDevLevel'] == target_hardware['parentDevLevel'],
                           src_disk_controller_hardware))
            else:
                source_hardwares = list()

            if not self.is_target_hardware_in_source_hardwares(target_hardware, source_hardwares):
                target_particular.append(target_hardware)

        _target_particular = list()
        for hardware in target_particular:
            hardware_obj = BoxLogic.Hardware('', '', hardware['HWIds'], hardware['CompatIds'])
            hardware_obj.szDescription = hardware.get('szDescription', hardware['HWIds'][0])
            _target_particular.append(hardware_obj)

        return _target_particular

    @staticmethod
    def is_target_connected_nic_exist_in_source_nics(host_snapshot_obj, pe_obj):
        pe_nics = json.loads(pe_obj.info)['net_adapters']
        src_nics = json.loads(host_snapshot_obj.ext_info)['system_infos']['Nic']
        pe_connected_mac = list(filter(lambda nic: nic['isConnected'], pe_nics))[0]['szMacAddress']
        src_all_macs = [xdata.standardize_mac_addr(nic['Mac']) for nic in src_nics]

        return xdata.standardize_mac_addr(pe_connected_mac) in src_all_macs

    @staticmethod
    def get_current_network_disk_controller_hardware_stacks(host_ident):
        ext_info = dict()
        network_ctr_hardware = HostBackupWorkProcessors.query_current_hardware_info(host_ident, 'net')['HWInfo']
        disk_ctr_hardware = HostBackupWorkProcessors.query_current_hardware_info(host_ident, 'SCSIAdapter')['HWInfo']
        disk_ctr_hardware += HostBackupWorkProcessors.query_current_hardware_info(host_ident, 'HDC')['HWInfo']
        ext_info['network_controller_hardware'] = network_ctr_hardware
        ext_info['disk_controller_hardware'] = disk_ctr_hardware
        return json.dumps(ext_info)

    # 查询驱动库，返回不存在驱动的硬件(host_snapshot_id or host_ident)
    def get(self, request, id_ident, pe_ident, api_request=None):
        if api_request is None:
            api_request = request.query_params

        pe_host = RestoreTarget.objects.get(ident=pe_ident)
        restore_process, host_snapshot = False, None
        try:
            host_snapshot = HostSnapshot.objects.get(id=id_ident)
            system_infos = json.loads(host_snapshot.ext_info)['system_infos']
            restore_process = True
        except Exception:
            host = Host.objects.get(ident=id_ident)
            system_infos = json.loads(host.ext_info)['system_infos']

        # 备份源的操作系统
        source_os_type = '{}{}-{}-{}bit'.format(
            system_infos['System']['SystemCaption'],
            system_infos['System']['ServicePack'],
            system_infos['System'].get('BuildNumber', 'Unknown version'),
            system_infos['System'].get('ProcessorArch', '')
        )
        sys_os_type = system_infos['System']['SystemCatName']  # Server2008R2_X64
        sys_os_bit = system_infos['System']['ProcessorArch']  # 64

        if 'LINUX' not in system_infos['System']['SystemCaption'].upper():
            if os.path.exists(HARDWARE_BLACKLIST_FILE) and (os.path.getsize(HARDWARE_BLACKLIST_FILE) != 0):
                self.check_hardware_match_blacklistfile(sys_os_type, sys_os_bit, pe_host)
        # 获取目标特有的硬件(还原/迁移流程)
        if restore_process and host_snapshot:
            target_particular = self.get_target_particular_hardwares_by_comparing_with_source(host_snapshot, pe_host)
        else:
            tmp_obj = copy.deepcopy(pe_host)
            tmp_obj.ext_info = self.get_current_network_disk_controller_hardware_stacks(id_ident)
            target_particular = self.get_target_particular_hardwares_by_comparing_with_source(tmp_obj, pe_host)

        target_particular = TargetHardware.filter_exclude_cc0601_cc0604_hardware(target_particular)
        if host_snapshot:  # host_snapshot 存在说明是还原，迁移一定不是本还原
            restore_to_self = TargetHardware.is_target_connected_nic_exist_in_source_nics(host_snapshot, pe_host)
        else:
            restore_to_self = False

        # 查看是否是异构和是否是本机还原
        if int(request.GET.get('need_check_local_db', 0)):
            return Response(data={"is_same": len(target_particular), "restore_to_self": restore_to_self})

        # 在驱动库查找指定的硬件，滤出不存在的
        hardwares_need_driver = list(
            filter(lambda hardware: not boxService.box_service.isHardwareDriverExist(hardware, sys_os_type, sys_os_bit),
                   target_particular)
        )
        hardwares_need_driver = [pyconv.convert2JSON(hardware) for hardware in hardwares_need_driver]

        return Response(data={'os_type': source_os_type, 'drivers': hardwares_need_driver})


class Agent2Pe(APIView):
    queryset = Host.objects.none()

    def __init__(self, **kwargs):
        super(Agent2Pe, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def post(self, request, host_ident, api_request=None):
        if api_request is None:
            api_request = request.data
        host_stat = boxService.box_service.GetStatus(host_ident)
        # 移除此限制，为了在还原过程中再次还原。
        # if 'restore' in host_stat:
        #     return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED, data='目标机处于还原状态，请等待还原完成。')
        if 'off_line' in host_stat:
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED, data='目标机处于离线状态。')
        if 'error' in host_stat:
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED, data='目标机驱动启动失败。')
        if {'backup', 'cdp_syn', 'cdp_asy'} & set(host_stat):
            descr = '目标机正处于备份保护中，请在“备份计划管理”界面禁用此客户端的备份计划，再执行恢复功能。'
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED, data=descr)

        result, pe_ident = boxService.box_service.start_agent_pe(host_ident)  # True, ident
        if result:
            return Response(status=status.HTTP_200_OK, data=pe_ident)
        return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED, data='目标机启动还原模块失败。')


proc_detail = {'last_ident': None, 'is_alive': None}
proc_worker = None
proc_locker = threading.Lock()


class AgentLogs(APIView):
    def __init__(self, **kwargs):
        super(AgentLogs, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    @staticmethod
    def generate_log_zip_task():
        global proc_detail
        proc_detail['is_alive'] = True
        if proc_detail['last_ident'] != xdata.AIO_LOG_IDENT:
            try:
                _check_path_and_remove_file(proc_detail['zip_path'])
                boxService.box_service.fetchAgentDebugFile(proc_detail['last_ident'], proc_detail['zip_path'])
            except Exception as e:
                _logger.warning('generate_log_zip_task, agent, error: {}'.format(e))
        else:
            try:
                _get_aio_logs()
            except Exception as e:
                _logger.warning('generate_log_zip_task, aio, error: {}'.format(e))

        proc_detail['is_alive'] = False

    @staticmethod
    def delete_expired_files(dir_to_search):
        for dirpath, dirnames, filenames in os.walk(dir_to_search):
            for file in filenames:
                curpath = os.path.join(dirpath, file)
                file_modified = datetime.fromtimestamp(os.path.getmtime(curpath))
                if datetime.now() - file_modified > timedelta(hours=xdata.LOG_ZIP_EXPIRE_HOURS):
                    os.remove(curpath)

    @staticmethod
    def delete_expired_aio_agent_zip_logs():
        agent_logs_dir = os.path.dirname(xdata.AGENT_LOG_PATH)
        aio_log_dir = os.path.dirname(xdata.ALL_DEBUG_MESSAGE_PATH)
        AgentLogs.delete_expired_files(agent_logs_dir)
        AgentLogs.delete_expired_files(aio_log_dir)

    @staticmethod
    def c_time(file_path):
        time_step = os.path.getctime(file_path)
        return datetime.fromtimestamp(time_step).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def target_zip_file_status(zip_path, target_ident):
        global proc_detail

        if not os.path.exists(zip_path):
            return 'NotExist'
        if proc_detail['last_ident'] == target_ident and proc_detail['is_alive']:
            return 'NotFinished'
        return 'OK'

    # 功能1: 重新生成 aio_log, agent_xx_log
    # 功能2: 获取指定 aio_log, agent_xx_log, 同时删除过期的Zip文件
    def get(self, request):
        global proc_worker, proc_locker, proc_detail
        target_ident = request.GET['ident']
        action = request.GET['action']
        agent_zip_path = '{}.{}'.format(xdata.AGENT_LOG_PATH, target_ident)
        agent_zip_url = os.path.join(os.sep, *agent_zip_path.split(os.sep)[-3:])
        aio_zip_path = xdata.ALL_DEBUG_MESSAGE_PATH
        aio_zip_url = os.path.join(os.sep, *aio_zip_path.split(os.sep)[-3:])

        if target_ident == xdata.AIO_LOG_IDENT:
            zip_path, ret_url = aio_zip_path, aio_zip_url
        else:
            zip_path, ret_url = agent_zip_path, agent_zip_url

        self.delete_expired_aio_agent_zip_logs()
        if target_ident != xdata.AIO_LOG_IDENT:
            if not Host.objects.get(ident=target_ident).is_linked:
                return Response(data={'url': None, 'result': False, 'msg': '客户端当前处于离线状态, 获取失败!'})

        if action == 'get_zip_url':  # 1.获取客户端、一体机zip文件
            zip_status = self.target_zip_file_status(zip_path, target_ident)
            if zip_status == 'NotExist':
                return Response(data={'url': None, 'msg': '日志文件不存在, 请重新生成!'})
            if zip_status == 'NotFinished':
                return Response(data={'url': None, 'msg': '请稍后, 正在生成中...'})
            return Response(data={'url': ret_url, 'msg': 'successful', 'ctime': self.c_time(zip_path)})

        if action == 'regenerate_log_zip':  # 2.开始重新生成日志zip文件
            with proc_locker:
                zip_status = self.target_zip_file_status(zip_path, target_ident)
                if zip_status == 'NotFinished':
                    return Response(data={'result': False, 'msg': '请稍后, 正在生成中...'})

                if not proc_detail['is_alive']:
                    proc_detail = {'last_ident': target_ident, 'is_alive': None, 'zip_path': zip_path}
                    proc_worker = threading.Thread(target=self.generate_log_zip_task, args=())
                    proc_worker.start()
                    _logger.info('generate_log_zip_task start ... {}.'.format(target_ident))
                    return Response(data={'result': True, 'msg': 'successful'})

                if target_ident == proc_detail['last_ident']:
                    return Response(data={'result': False, 'msg': '请稍后, 正在生成中...'})

                return Response(data={'result': False, 'msg': '为其他客户端生成日志文件中, 请稍后再试!'})

        return Response(data={'url': None, 'result': False, 'msg': '请求参数错误!!!'})


class TunnelManage(APIView):
    queryset = Tunnel.objects.all()

    def __init__(self, **kwargs):
        super(TunnelManage, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    @staticmethod
    def _is_ip_port_existed(tu_ip, tu_port):
        return True if Tunnel.objects.filter(host_ip=tu_ip, host_port=tu_port) else False

    def _update_endpoints(self):
        endPoints = self.get(request=None, api_request={}).data
        endPoints = [{'Index': dic['id'], 'IpAddress': dic['host_ip'], 'Port': dic['host_port']} for dic in endPoints]
        endPoints = [pyconv.convertJSON2OBJ(CProxy.EndPoint, endPoint) for endPoint in endPoints]
        boxService.box_service.updateTunnelsEndPoints(endPoints)

    # None: 未分配的Host登录过该隧道
    @staticmethod
    def get_tunnel_user(tunnel):
        if tunnel.user is None:
            return tunnel.host.user
        else:
            return tunnel.user

    # 滤掉无效的隧道：User为空，且Host的User不存在(未分配)
    # 同时更新隧道的User字段，不存入数据库
    @staticmethod
    def filter_out_invalid_tunnel(tunnels):
        valid_tunnels = []
        for tunnel in tunnels:
            tunnel_user = TunnelManage.get_tunnel_user(tunnel)
            if tunnel_user:
                tunnel.user = tunnel_user
                valid_tunnels.append(tunnel)
        return valid_tunnels

    # 添加一个隧道
    def post(self, request, api_request=None):
        if not api_request:
            api_request = request.data
        tu_ip = api_request['tu_ip']
        tu_port = api_request['tu_port']
        user_id = api_request['user_id']

        if self._is_ip_port_existed(tu_ip, tu_port):
            return Response(data='新建失败，{}:{}已经存在，不能重复添加。'.format(tu_ip, tu_port), status=status.HTTP_400_BAD_REQUEST)

        Tunnel.objects.create(name='{}:{}'.format(tu_ip, tu_port), host_ip=tu_ip, host_port=tu_port, user_id=user_id)
        self._update_endpoints()
        return Response(status=status.HTTP_200_OK)

    # 获取所有隧道
    def get(self, request, api_request=None):
        if api_request is None:
            api_request = request.query_params

        tunnels = Tunnel.objects.filter()
        tunnels = self.filter_out_invalid_tunnel(tunnels)
        tunnels = [
            {'id': obj.id, 'name': obj.name, 'host_ip': obj.host_ip, 'host_port': obj.host_port, 'user_id': obj.user_id}
            for obj in tunnels]
        return Response(status=status.HTTP_200_OK, data=tunnels)

    # 编辑(仅用户名)
    def put(self, request, api_request=None):
        if api_request is None:
            api_request = request.query_params
        tu_name = api_request['tu_name']
        tu_id = api_request['tu_id']
        Tunnel.objects.get(id=tu_id).set_name(tu_name)
        return Response(status=status.HTTP_200_OK)

    # 删除指定隧道
    def delete(self, request, api_request=None):
        if api_request is None:
            api_request = request.query_params
        tu_id = api_request['tu_id']
        Tunnel.objects.get(id=tu_id).delete()
        self._update_endpoints()
        return Response(status=status.HTTP_204_NO_CONTENT)

    # 更新endPoints
    def head(self, request, api_request=None):
        if api_request is None:
            api_request = request.query_params
        self._update_endpoints()
        return Response(status=status.HTTP_200_OK)


# 获取匹配的驱动版本
class GetDriversVersions(APIView):
    queryset = RestoreTarget.objects.none()

    def __init__(self, **kwargs):
        super(GetDriversVersions, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    # 获取匹配的驱动版本
    def get(self, request, snapshot_id, pe_ident, api_request=None):
        if api_request is None:
            api_request = request.query_params

        pe_host = RestoreTarget.objects.get(ident=pe_ident)
        try:
            # snapshot_id 为快照ID
            host_snapshot = HostSnapshot.objects.get(id=snapshot_id)
            system_infos = json.loads(host_snapshot.ext_info)['system_infos']
        except:
            # snapshot_id 为主机ident
            host = Host.objects.get(ident=snapshot_id)
            system_infos = json.loads(host.ext_info)['system_infos']

        # 目标机的硬件环境
        pe_restore_instance = PeRestore(pe_host)
        target_hardwares = pe_restore_instance.generate_hardwares()
        sys_os_type = system_infos['System']['SystemCatName']  # 10_X64
        sys_os_bit = system_infos['System']['ProcessorArch']  # 64

        # 获取匹配的驱动版本
        target_hardwares = TargetHardware.filter_exclude_cc0601_cc0604_hardware(target_hardwares)
        # 过滤掉子设备
        target_hardwares = pe_restore_instance.filter_virtual_sub_device(target_hardwares)
        drivers_versions = list()
        for hardware in target_hardwares:
            drivers_version = boxService.box_service.GetDriversVersions(hardware, sys_os_type, sys_os_bit)
            drivers_version = json.loads(drivers_version)
            if drivers_version:
                drivers_versions.append([pyconv.convert2JSON(hardware)])
                drivers_versions[-1].append(drivers_version)
            else:
                _logger.error('not find drivers {}'.format(pyconv.convert2JSON(hardware)))
        return Response(data={'drivers': drivers_versions, 'sys_os_type': sys_os_type})

    # 标记用户已选择的驱动, UserSelected=1
    # 将结果返回给底层功能(制作ISO)
    @staticmethod
    def get_drivers_list_str(pe_ident, driversids, snapshot_id_host_ident, my_type, restore_to_self=False,
                             user_id=-1, driver_ids_force=''):
        _logger.debug('get_drivers_list_str my_type {} driversids {} driver_ids_force {}'.format(my_type, driversids,
                                                                                                 driver_ids_force))
        try:
            host_snapshot = HostSnapshot.objects.get(id=snapshot_id_host_ident)
            system_infos = json.loads(host_snapshot.ext_info)['system_infos']
        except:
            host = Host.objects.get(ident=snapshot_id_host_ident)
            system_infos = json.loads(host.ext_info)['system_infos']

        sys_os_type = system_infos['System']['SystemCatName']  # 10_X64
        sys_os_bit = system_infos['System']['ProcessorArch']  # 64

        if 'LINUX' in system_infos['System']['SystemCaption'].upper():  # 原操作系统是linux时候，不需要获取驱动
            return json.dumps(dict())

        pe_host = RestoreTarget.objects.get(ident=pe_ident)
        target_hardwares = PeRestore(pe_host).generate_hardwares()

        target_hardwares = TargetHardware.filter_exclude_cc0601_cc0604_hardware(target_hardwares)
        all_versions = dict()
        # 获取所有的驱动版本
        for hardware in target_hardwares:
            drivers_version = boxService.box_service.GetDriversVersions(hardware, sys_os_type, sys_os_bit)
            drivers_version = json.loads(drivers_version)
            if drivers_version:
                all_versions[pyconv.convert2JSON(hardware)['HWIds'][0]] = drivers_version
            else:
                all_versions[pyconv.convert2JSON(hardware)['HWIds'][0]] = list()

        # 智能匹配(模式一)
        if my_type == '1':
            for versions in all_versions.values():  # 1.勾选所有驱动
                for version in versions:
                    version['UserSelected'] = 1
            if restore_to_self:
                unselect_all_drivers_when_restore_to_self(all_versions, restore_to_self)  # 3.若同构本机还原, 所有驱动不勾选
            else:
                unselect_drivers_according_to_db_blacklist(target_hardwares, sys_os_type, all_versions)  # 2.排除在黑名单的驱动
                GetDriversVersions._select_force_install_drivers(sys_os_type, user_id, all_versions)

        # 用户指定驱动(模式二), 且至少勾选一个驱动
        elif driversids or driver_ids_force:
            if driversids:
                for id in driversids.split(','):
                    hradwareid_and_choiceid = id.split('|')
                    all_versions[hradwareid_and_choiceid[0]][int(hradwareid_and_choiceid[1])]['UserSelected'] = 1
            if driver_ids_force:
                for id in driver_ids_force.split(','):
                    hradwareid_and_choiceid = id.split('|')
                    all_versions[hradwareid_and_choiceid[0]][int(hradwareid_and_choiceid[1])]['UserSelected'] = 1
                    all_versions[hradwareid_and_choiceid[0]][int(hradwareid_and_choiceid[1])]['ForceInst'] = 1

            update_db_blacklist(sys_os_type, all_versions)
            GetDriversVersions._update_db_force_install(sys_os_type, user_id, all_versions)

        _logger.info('get_drivers_list_str: {} {}'.format(all_versions, restore_to_self))
        return json.dumps(all_versions)

    @staticmethod
    def _select_force_install_drivers(sys_os_type, user_id, all_versions):
        """
        :param sys_os_type: 源操作系统
        :param user_id: 用户ID
        :param all_versions: 获取的所有驱动版本 {‘hwid’:[version1, version2]}
        :return:
        遍历每一个硬件下的所有驱动，查看是否在ForceInstallDriver中存在记录
        如果存在记录，那么意味着这个驱动需要强制安装
        """
        for HWIds_0, drivers in all_versions.items():
            for index1, driver in enumerate(drivers):
                if ForceInstallDriver.objects.filter(sys_type=sys_os_type,
                                                     user_id=user_id,
                                                     device_id=driver['hard_or_comp_id'],
                                                     driver_id=driver['zip_path']).exists():
                    _logger.info('force install driver by user choice from db,'
                                 'sys_os_type {} driver {}'.format(sys_os_type, driver))
                    break
            else:
                return

            for index2, driver in enumerate(drivers):
                if index2 == index1:
                    driver['UserSelected'] = 1
                    driver['ForceInst'] = 1
                else:
                    driver['UserSelected'] = 0
                    driver['ForceInst'] = 0

    @staticmethod
    def _update_db_force_install(sys_os_type, user_id, all_versions):
        """
        根据驱动勾选情况，更新数据库，记录用户选择需要强制安装的驱动
        """
        if user_id == -1:
            _logger.info('_update_db_force_install user is None, skip update')
            return
        for drivers in all_versions.values():
            for driver in drivers:
                if int(driver.get('ForceInst', 0)):
                    _, created = ForceInstallDriver.objects.get_or_create(sys_type=sys_os_type,
                                                                          user_id=user_id,
                                                                          device_id=driver['hard_or_comp_id'],
                                                                          driver_id=driver['zip_path'])
                    if created:
                        _logger.info(
                            'created force install to db, sys_type {} user_id {} device_id {} driver_id {}'.format(
                                sys_os_type, user_id, driver['hard_or_comp_id'], driver['zip_path']
                            ))
                else:
                    ForceInstallDriver.objects.filter(sys_type=sys_os_type,
                                                      user_id=user_id,
                                                      device_id=driver['hard_or_comp_id'],
                                                      driver_id=driver['zip_path']).delete()


class HostRoutersInfo(APIView):
    def __init__(self, **kwargs):
        super(HostRoutersInfo, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def get(self, request, snapshot_id_or_host_ident):
        # 获取主机路由信息
        if len(snapshot_id_or_host_ident) == 32:
            routers_info = query_host_routers_info(snapshot_id_or_host_ident)
        else:
            # 获取快照点路由
            host_snapshot = HostSnapshot.objects.get(id=int(snapshot_id_or_host_ident))
            route_infos = json.loads(host_snapshot.ext_info).get("route_infos", [])
            routers_info = _get_route_info_from_dict_to_list(route_infos)
        # 过滤掉 默认网关
        if routers_info:
            routers_info = list(filter(lambda x: x[0] != "0.0.0.0", routers_info))
        return Response(data={'routers': routers_info})


class DiskVolMap(APIView):
    def __init__(self, **kwargs):
        super(DiskVolMap, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    """
    # 还原时候需要传回的数据结构
    # 快照中的卷属于哪块磁盘（来自disksnapshot的ident）
    disk_ident = serializers.CharField(max_length=32, required=True)
    # 卷的扇区偏移
    sector_offset = serializers.CharField()
    # 卷的扇区数量
    sectors = serializers.CharField()
    # 快照中卷的显示名称
    display_name = serializers.CharField()
    # 目标卷的设备名
    target_ident = serializers.CharField()
    # 目标卷所在的磁盘序号
    target_disk_number = serializers.CharField()
    # 目标卷的显示名称
    target_display_name = serializers.CharField()

    #调用get方法获取到的数据结构,每一个dict代表源的一个卷，其中的target_vol为与源匹配的目标卷，可能多个，可能没有
    [{'disk_ident':'', 'sector_offset':'', 'sectors':'', 'display_name':'','target_vol':[]}]
    """

    def get(self, request, snapshot_id, host_ident):
        snapshot_obj = HostSnapshot.objects.get(id=int(snapshot_id))
        snapshot_disk_info = json.loads(snapshot_obj.ext_info)['include_ranges']
        host_obj = Host.objects.get(ident=host_ident)
        system_info = query_system_info(host_obj, True)
        if system_info is None:
            system_info = query_system_info(host_obj, False)
        if system_info is None:
            return Response(status=status.HTTP_403_FORBIDDEN)
        os_type = 'linux' if 'LINUX' in (
            json.loads(snapshot_obj.ext_info)['system_infos']['System']['SystemCaption'].upper()) else 'windows'
        if os_type == 'windows':
            src_vols, dst_vols = self._get_vols_windows(snapshot_disk_info, system_info)
        else:
            src_vols, dst_vols = self._get_vols_linux(snapshot_disk_info, system_info)

        result = []
        for vol in src_vols:
            indexs = self.get_target_index(vol, dst_vols)  # 获取匹配卷在目标磁盘列表dst_vols的index
            for index in indexs:
                vol['target_vol'].append(dst_vols[index])
            result.append(vol)

        self.marked_not_support_vols(result)

        return Response(data={'maps': result})

    @staticmethod
    def get_target_index(src_vol, dst_vols):
        target_indexs = []
        for dst_vol_index, dst_vol in enumerate(dst_vols):
            if len(dst_vol['ranges']) != len(src_vol['ranges']):
                continue
            is_find_area = True
            for src_disk_range in src_vol['ranges']:
                if not is_find_area:
                    break
                for dst_disk_range in dst_vol['ranges']:
                    st1 = src_disk_range['sector_offset'] == dst_disk_range['sector_offset']
                    st2 = src_disk_range['sectors'] == dst_disk_range['sectors']
                    if st1 and st2:
                        break
                else:
                    is_find_area = False
            if is_find_area:
                target_indexs.append(dst_vol_index)
        return target_indexs

    def _get_vols_windows(self, src_disk_info, dst_disk_info):
        src_partitions = [{'display_name': self.get_name(partition),
                           'target_vol': [],
                           'ranges': [{
                               'disk_ident': disk['diskIdent'],
                               'sector_offset': self.to_sector(partition['PartitionOffset']),
                               'sectors': self.to_sector(partition['PartitionSize']),
                           }],
                           'child': False,
                           'VolumeName': partition['VolumeName'],
                           'Letter': partition['Letter'],
                           'VolumeLabel': partition['VolumeLabel']
                           } for disk in src_disk_info for partition in disk['ranges']]
        all_vol_name_index = [vol['VolumeName'] for vol in src_partitions]
        src_vols = self.merge_vols(all_vol_name_index, src_partitions)
        dst_partitions = [{'target_ident': partition['VolumeName'],
                           'child': False,
                           'ranges': [{
                               'target_disk_number': disk['DiskNum'],
                               'sector_offset': self.to_sector(partition['PartitionOffset']),
                               'sectors': self.to_sector(partition['PartitionSize']),
                           }],
                           'target_display_name': self.get_name(partition),
                           'Letter': partition['Letter'],
                           'VolumeLabel': partition['VolumeLabel']
                           } for disk in dst_disk_info['Disk'] for partition in disk['Partition']]

        all_dst_vol_name_index = [vol['target_ident'] for vol in dst_partitions]
        dst_vols = self.merge_vols(all_dst_vol_name_index, dst_partitions)

        return src_vols, dst_vols

    def _get_vols_linux(self, src_disk_info, dst_disk_info):
        src_partitions = [{'display_name': self.get_name_linux(partition),
                           'target_vol': [],
                           'ranges': [{'disk_ident': disk['diskIdent'],
                                       'sector_offset': self.to_sector(int(partition['BytesStart'])),
                                       'sectors': self.to_sector(
                                           int(partition['BytesEnd']) - int(partition['BytesStart']))}],
                           'VolumeDevice': partition['VolumeDevice'],
                           'child': False,
                           'Type': partition['Type'],
                           'mount_point_after_restore': partition['MountPoint'],
                           'mount_fs_type_after_restore': partition['FileSystem'],
                           'mount_fs_opts_after_restore': partition['MountOpts']
                           } for disk in src_disk_info for partition in disk['ranges']]

        all_vol_name_index = [vol['VolumeDevice'] for vol in src_partitions]
        src_vols = self.merge_vols(all_vol_name_index, src_partitions)

        linux_partitions = HostBackupWorkProcessors.analyze_linux_partitions(None, dst_disk_info)
        dst_partitions = [{'target_ident': partition['VolumeDevice'],
                           'ranges': [{'target_disk_number': partition['DiskIndex'],
                                       'sector_offset': self.to_sector(int(partition['BytesStart'])),
                                       'sectors': self.to_sector(
                                           int(partition['BytesEnd']) - int(partition['BytesStart']))}],
                           'target_display_name': self.get_name_linux(partition),
                           'child': False,
                           'Type': partition['Type'],
                           'mount_point_after_restore': partition['MountPoint'],
                           'mount_fs_type_after_restore': partition['FileSystem'],
                           'mount_fs_opts_after_restore': partition['MountOpts']
                           } for partition in linux_partitions]

        all_dst_vol_name_index = [vol['target_ident'] for vol in dst_partitions]
        dst_vols = self.merge_vols(all_dst_vol_name_index, dst_partitions)

        return src_vols, dst_vols

    @staticmethod
    def merge_vols(all_vol_name_index, src_disk):
        for index, vol_name in enumerate(all_vol_name_index[1:], 1):
            parent_index = all_vol_name_index.index(vol_name)
            if parent_index != index:
                src_disk[parent_index]['ranges'].extend(src_disk[index]['ranges'])
                src_disk[index]['child'] = True
        src_disk = list(filter(lambda x: not x['child'], src_disk))
        return src_disk

    @staticmethod
    def to_sector(_bytes):
        return str(int(_bytes) // 512)

    @staticmethod
    def to_gb_str(_bytes):
        if isinstance(_bytes, str):
            return _bytes
        value = _bytes / 1024 ** 3
        return '{0:.1f}GB'.format(value if value > 0.1 else 0.1)

    # WINDOWS 分区显示名称
    @staticmethod
    def get_name(partition):
        letter = partition['Letter']
        volume_label = partition['VolumeLabel']
        t_size = int(partition['VolumeSize'])
        f_size = int(partition['FreeSize'])
        if f_size == -1:
            u_size = '--'
        else:
            u_size = int(t_size - f_size)
        if letter and volume_label:
            return '{}({}:)(容量{},已用{})'.format(volume_label, letter, DiskVolMap.to_gb_str(t_size),
                                               DiskVolMap.to_gb_str(u_size))
        elif letter:
            return '{}:(容量{},已用{})'.format(letter, DiskVolMap.to_gb_str(t_size), DiskVolMap.to_gb_str(u_size))
        elif volume_label:
            return '{}:(容量{},已用{})'.format(volume_label, DiskVolMap.to_gb_str(t_size), DiskVolMap.to_gb_str(u_size))
        else:
            return '无盘符:(容量{},已用{})'.format(DiskVolMap.to_gb_str(t_size), DiskVolMap.to_gb_str(u_size))

    # linux 分区显示名称
    @staticmethod
    def get_name_linux(partition):
        mount_point = partition.get('MountPoint', '')
        device_name = partition.get('VolumeDevice', '')
        t_size = int(partition['TotalBytes']) if partition['TotalBytes'] else int(partition['BytesEnd']) - int(
            partition['BytesStart'])
        u_size = int(partition['UsedBytes']) if partition['UsedBytes'] else '--'
        if mount_point and device_name:
            return '{}({})(容量{},已用{})'.format(mount_point, device_name, DiskVolMap.to_gb_str(t_size),
                                              DiskVolMap.to_gb_str(u_size))
        elif mount_point:
            return '{}(容量{},已用{})'.format(mount_point, DiskVolMap.to_gb_str(t_size), DiskVolMap.to_gb_str(u_size))
        elif device_name:
            return '{}(容量{},已用{})'.format(device_name, DiskVolMap.to_gb_str(t_size), DiskVolMap.to_gb_str(u_size))
        else:
            return '无盘符(容量{},已用{})'.format(DiskVolMap.to_gb_str(t_size), DiskVolMap.to_gb_str(u_size))

    def marked_not_support_vols(self, vol_lists):
        for vol in vol_lists:
            if 'mount_point_after_restore' in vol:
                self._check_linux_vol_is_valid(vol)
                self._check_2_ranges_with_same_sector_offset(vol)
            else:
                self._check_windows_vol_is_valid(vol)

    @staticmethod
    def _check_linux_vol_is_valid(vol):
        bad_mount_points = ['/', '/boot']
        if vol['mount_point_after_restore'] in bad_mount_points:
            vol['valid'] = False
        elif 'swap' in vol['mount_fs_type_after_restore']:
            vol['valid'] = False
        else:
            vol['valid'] = True

    @staticmethod
    def _check_windows_vol_is_valid(vol):
        bad_letters = ['C']
        bad_labels = ['系统保留']
        if vol['Letter'] in bad_letters:
            vol['valid'] = False
        elif vol['VolumeLabel'] in bad_labels:
            vol['valid'] = False
        else:
            vol['valid'] = True

    @staticmethod
    def _check_2_ranges_with_same_sector_offset(vol):
        offset_tuple = [(vol_ranges['sector_offset'], vol_ranges['sectors']) for vol_ranges in vol['ranges']]
        item_count = [offset_tuple.count(i) for i in offset_tuple]
        for item in item_count:
            if item != 1:
                _logger.error('vol:{} is not valid, 2_ranges_with_same_sector_offset'.format(vol))
                vol['valid'] = False

    # 获取一个卷还原需要的参数
    @staticmethod
    def get_vol_restore_params(src_vol_info, dst_index):
        restore_params = dict()
        for _range in src_vol_info['ranges']:
            _range['target_disk_number'] = DiskVolMap._get_disk_number(_range['sector_offset'],
                                                                       _range['sectors'],
                                                                       src_vol_info['target_vol'][dst_index]['ranges'])

        restore_params['ranges'] = src_vol_info['ranges']
        restore_params['display_name'] = src_vol_info['display_name']
        restore_params['target_ident'] = src_vol_info['target_vol'][dst_index]['target_ident']
        restore_params['target_display_name'] = src_vol_info['target_vol'][dst_index]['target_display_name']
        restore_params['mount_point_after_restore'] = src_vol_info.get('mount_point_after_restore', '')
        restore_params['mount_fs_type_after_restore'] = src_vol_info.get('mount_fs_type_after_restore', '')
        restore_params['mount_fs_opts_after_restore'] = src_vol_info.get('mount_fs_opts_after_restore', '')
        return restore_params

    @staticmethod
    def _get_disk_number(sector_offset, sectors, ranges):
        for _range in ranges:
            if (_range['sector_offset'] == sector_offset) and (_range['sectors'] == sectors):
                break
        else:
            raise Exception('_get_disk_number error:{}|{}|{}'.format(sector_offset, sectors, ranges))
        return _range['target_disk_number']


class GetOneDiskVols(APIView):
    """
     根据磁盘的ident，在hostsnapshot的include_rangs中获取已备份磁盘的卷的分布，
     在exclude_ranges中，则查找此卷在历史快照点中的最新状态。
     如果都找不到则此卷从未被备份过。
    """

    def __init__(self, **kwargs):
        super(GetOneDiskVols, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    @staticmethod
    def get(snapshot_info, disk_ident):
        os_type = 'linux' if 'LINUX' in (
            snapshot_info['system_infos']['System']['SystemCaption'].upper()) else 'windows'
        if os_type == 'windows':
            in_vols, ex_vols = GetOneDiskVols._get_windows_vols(snapshot_info, disk_ident)
        else:
            in_vols, ex_vols = GetOneDiskVols._get_linux_vols(snapshot_info, disk_ident)

        return Response(data={'include_vols': in_vols, 'exclude_vols': ex_vols})

    @staticmethod
    def _get_windows_vols(snapshot_info, disk_ident):
        include_partitions = list()
        exclude_partitions = list()
        # 找到当前备份的卷
        for disk in snapshot_info['include_ranges']:
            if disk['diskIdent'] != disk_ident:
                continue
            for partition in disk['ranges']:
                pt = GetOneDiskVols._get_windows_partition_info(disk, partition, '')
                include_partitions.append(pt)
        # 获取历史卷的状态
        for ex_disk_or_partition in snapshot_info['exclude_ranges']:
            if ex_disk_or_partition['diskIdent'] != disk_ident:
                continue
            if ex_disk_or_partition['type'] == 'partition':
                ex_pt = GetOneDiskVols._get_windows_history_partition_info(
                    ex_disk_or_partition['partition']['VolumeName'],
                    disk_ident,
                    disk['diskSnapshot'])
                if ex_pt:
                    exclude_partitions.append(ex_pt)
                else:
                    pt = GetOneDiskVols._get_windows_partition_info(disk, ex_disk_or_partition['partition'], 'NAN')
                    exclude_partitions.append(pt)
            else:
                for ex_partition in ex_disk_or_partition['disk']['Partition']:
                    ex_pt = GetOneDiskVols._get_windows_history_partition_info(ex_partition['VolumeName'],
                                                                               disk_ident,
                                                                               disk['diskSnapshot'])
                    if ex_pt:
                        exclude_partitions.append(ex_pt)
                    else:
                        pt = GetOneDiskVols._get_windows_partition_info(disk, ex_partition, 'NAN')
                        exclude_partitions.append(pt)

        return include_partitions, exclude_partitions

    @staticmethod
    def _get_linux_vols(snapshot_info, disk_ident):
        include_partitions = list()
        exclude_partitions = list()
        _all_pv_device_name = [pv['name'] for vg in snapshot_info['system_infos']['Storage']['vgs'] for pv in vg['pvs']]
        # 找到当前备份的卷
        for disk in snapshot_info['include_ranges']:
            if disk['diskIdent'] != disk_ident:
                continue

            for partition in disk['ranges']:
                if GetOneDiskVols._check_is_lvm(partition, _all_pv_device_name):
                    continue
                pt = GetOneDiskVols._get_linux_partition_info(disk, partition, '')
                include_partitions.append(pt)

        # 获取历史卷的状态
        for ex_disk_or_partition in snapshot_info['exclude_ranges']:
            if ex_disk_or_partition['diskIdent'] != disk_ident:
                continue
            if ex_disk_or_partition['type'] == 'partition':
                if GetOneDiskVols._check_is_lvm(ex_disk_or_partition['partition'], _all_pv_device_name):
                    continue
                ex_pt = GetOneDiskVols._get_linux_history_partition_info(
                    ex_disk_or_partition['partition']['VolumeDevice'],
                    disk_ident,
                    disk['diskSnapshot'])
                if ex_pt:
                    exclude_partitions.append(ex_pt)
                else:
                    pt = GetOneDiskVols._get_linux_partition_info(disk, ex_disk_or_partition['partition'],
                                                                  'NAN')
                    exclude_partitions.append(pt)
            else:
                for ex_partition in ex_disk_or_partition['partitions']:
                    if GetOneDiskVols._check_is_lvm(ex_partition, _all_pv_device_name):
                        continue
                    ex_pt = GetOneDiskVols._get_linux_history_partition_info(ex_partition['VolumeDevice'],
                                                                             disk_ident,
                                                                             disk['diskSnapshot'])
                    if ex_pt:
                        exclude_partitions.append(ex_pt)
                    else:
                        pt = GetOneDiskVols._get_linux_partition_info(disk, ex_partition, 'NAN')
                        exclude_partitions.append(pt)

        return include_partitions, exclude_partitions

    # 寻找卷在历史中的最新状态,没有找到返回None,找到就返回对应的partition
    @staticmethod
    def _get_windows_history_partition_info(vol_name, disk_ident, disk_snapshot_ident):
        disk_snapshot = DiskSnapshot.objects.get(ident=disk_snapshot_ident)
        parent_disk_snapshot = disk_snapshot.parent_snapshot
        while parent_disk_snapshot:
            parent_disk_snapshot, host_snapshot = GetOneDiskVols.get_host_snapshot_by_disk_snapshot(
                parent_disk_snapshot)
            if host_snapshot.deleting or host_snapshot.deleted:
                return None
            snapshot_info = json.loads(host_snapshot.ext_info)
            exclude_vol_name_list = GetOneDiskVols._get_ex_vol_name_list(snapshot_info['exclude_ranges'])
            if vol_name in exclude_vol_name_list:
                parent_disk_snapshot = parent_disk_snapshot.parent_snapshot
            else:
                for disk in snapshot_info['include_ranges']:
                    if disk['diskIdent'] != disk_ident:
                        continue
                    for partition in disk['ranges']:
                        if partition['VolumeName'] == vol_name:
                            break
                    else:
                        return None
                    time_str = host_snapshot.start_datetime.strftime('%Y-%m-%dT%H:%M:%S.%f')
                    pt = GetOneDiskVols._get_windows_partition_info(disk, partition, time_str)
                    return pt
                return None
        return None

    # 寻找卷在历史中的最新状态,没有找到返回None,找到就返回对应的partition
    @staticmethod
    def _get_linux_history_partition_info(vol_name, disk_ident, disk_snapshot_ident):
        disk_snapshot = DiskSnapshot.objects.get(ident=disk_snapshot_ident)
        parent_disk_snapshot = disk_snapshot.parent_snapshot
        while parent_disk_snapshot:
            parent_disk_snapshot, host_snapshot = GetOneDiskVols.get_host_snapshot_by_disk_snapshot(
                parent_disk_snapshot)
            if host_snapshot.deleting or host_snapshot.deleted:
                return None
            snapshot_info = json.loads(host_snapshot.ext_info)
            exclude_vol_name_list = GetOneDiskVols._get_linux_ex_vol_name_list(snapshot_info['exclude_ranges'])
            if vol_name in exclude_vol_name_list:
                parent_disk_snapshot = parent_disk_snapshot.parent_snapshot
            else:
                _all_pv_device_name = [pv['name'] for vg in snapshot_info['system_infos']['Storage']['vgs'] for pv in
                                       vg['pvs']]
                for disk in snapshot_info['include_ranges']:
                    if disk['diskIdent'] != disk_ident:
                        continue
                    for partition in disk['ranges']:
                        if GetOneDiskVols._check_is_lvm(partition, _all_pv_device_name):
                            continue
                        if partition['VolumeDevice'] == vol_name:
                            break
                    else:
                        return None
                    time_str = host_snapshot.start_datetime.strftime('%Y-%m-%dT%H:%M:%S.%f')
                    pt = GetOneDiskVols._get_linux_partition_info(disk, partition, time_str)
                    return pt
                return None
        return None

    @staticmethod
    def _get_ex_vol_name_list(ex_ranges):
        rs_list = list()
        for disk_or_partition in ex_ranges:
            if disk_or_partition['type'] == 'partition':
                rs_list.append(disk_or_partition['partition']['VolumeName'])
            else:
                rs_list.append([par['VolumeName'] for par in disk_or_partition['disk']['Partition']])
        return rs_list

    @staticmethod
    def _get_linux_ex_vol_name_list(ex_ranges):
        rs_list = list()
        for disk_or_partition in ex_ranges:
            if disk_or_partition['type'] == 'partition':
                rs_list.append(disk_or_partition['partition']['VolumeDevice'])
            else:
                rs_list.append([par['VolumeDevice'] for par in disk_or_partition['partitions']])
        return rs_list

    @staticmethod
    def _get_windows_partition_info(disk, partition, postfix):
        pt = dict()
        pt['ranges'] = {
            'disk_ident': disk['diskIdent'],
            'sector_offset': DiskVolMap.to_sector(partition['PartitionOffset']),
            'sectors': DiskVolMap.to_sector(partition['PartitionSize']),
        }
        pt['VolumeName'] = partition['VolumeName']
        pt['postfix'] = postfix
        pt['display_name'] = DiskVolMap.get_name(partition)
        pt['disabled'] = GetOneDiskVols._is_disabled_checked_for_windows(partition)
        return pt

    @staticmethod
    def _get_linux_partition_info(disk, partition, postfix):
        pt = dict()
        pt['ranges'] = {
            'disk_ident': disk['diskIdent'],
            'sector_offset': DiskVolMap.to_sector(partition['BytesStart']),
            'sectors': DiskVolMap.to_sector(int(partition['BytesEnd']) - int(partition['BytesStart'])),
        }
        pt['VolumeName'] = partition['VolumeDevice']
        pt['postfix'] = postfix
        pt['display_name'] = DiskVolMap.get_name_linux(partition)
        pt['disabled'] = GetOneDiskVols._is_disabled_checked_for_linux(partition)
        return pt

    # 检测分区是否是native 并且被 创建为lvm
    @staticmethod
    def _check_is_lvm(partition, all_dev_name):
        if partition['Type'] == 'native' and partition['VolumeDevice'] in all_dev_name:
            return True
        else:
            return False

    @staticmethod
    def get_host_snapshot_by_disk_snapshot(disk_snapshot_object):
        while disk_snapshot_object.host_snapshot is None:
            disk_snapshot_object = disk_snapshot_object.parent_snapshot

        return disk_snapshot_object, disk_snapshot_object.host_snapshot

    @staticmethod
    def _is_disabled_checked_for_windows(partition):
        letter = partition['Letter']
        volume_label = partition['VolumeLabel']
        if letter == 'C' or volume_label == '系统保留':
            return True
        else:
            return False

    @staticmethod
    def _is_disabled_checked_for_linux(partition):
        if partition['MountPoint'] in ['/', '/boot']:
            return True
        else:
            return False


def get_response_error_string(response):
    return response.data


class ModuleTestView(APIView):
    def __init__(self, **kwargs):
        super(ModuleTestView, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def post(self, request):
        _logger.info('ModuleTestView : {}'.format(request.data))
        if request.data['type'] == 'generateClusterDiffImages':
            self.generateClusterDiffImages(request.data['config'])
        elif request.data['type'] == 'StartQemuWork':
            self.StartQemuWork(request.data['config'])
        elif request.data['type'] == 'get_host_ini_info':
            config = boxService.box_service.get_host_ini_info(request.data['host_ident'])
            return Response(config.items(), status=status.HTTP_200_OK)
        else:
            xlogging.raise_and_logging_error('unknown type', 'unknown type', )

        return Response(status=status.HTTP_205_RESET_CONTENT)

    @staticmethod
    def generateClusterDiffImages(config):
        boxService.box_service.generateClusterDiffImages(config)

    @staticmethod
    def StartQemuWork(config):
        boxService.box_service.StartQemuWork('task', 'token', list(), 'flag')


class DataQueuingReportView(APIView):
    def __init__(self, **kwargs):
        super(DataQueuingReportView, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    @xlogging.LockDecorator(boxService.message_dict_locker)
    def post(self, request):
        _logger.info('DataQueuingReportView : {}'.format(request.data))
        task = request.data['task']
        token = request.data['token']
        code = request.data['code']

        key = "{}_{}".format(task, token)
        if key not in boxService.message_dict:
            boxService.message_dict[key] = queue.Queue()
        boxService.message_dict[key].put(code)

        return Response(status=status.HTTP_205_RESET_CONTENT)


class Token2HashFile(APIView):
    def __init__(self, **kwargs):
        super(Token2HashFile, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def get(self, request, token):
        restore_target_disk, *_ = TokenInfo.get_token_object(token)

        if boxService.box_service.isFileExist(restore_target_disk.hash_path):
            _logger.info('Token2HashFile token:{} hash path:{}'.format(token, restore_target_disk.hash_path))
            return Response(data={'path': restore_target_disk.hash_path})
        else:
            _logger.warning(
                'Token2HashFile toekn:{}, hash path:{} not exists'.format(token, restore_target_disk.hash_path))
            return Response(data={'path': ''})
