import json
import threading
import time
import uuid
from datetime import timedelta

from django.utils import timezone
from rest_framework import status

from apiv1.models import (HostLog, BackupTask, HostSnapshot, RestoreTask, RestoreTarget, CDPTask, MigrateTask,
                          UserQuota, CompressTask, BackupTaskSchedule, ClusterBackupSchedule,
                          FileBackupTask, DiskSnapshot)
from apiv1.restore import PeRestore, is_restore_target_belong_htb, force_kill_kvm
from apiv1.signals import end_sleep
from apiv1.snapshot import DiskSnapshotHash, Tokens
from apiv1.spaceCollection import SpaceCollectionWorker, DeleteDiskSnapshotTask
from apiv1.storage_nodes import UserQuotaTools
from apiv1.task_helper import TaskHelper
from apiv1.task_queue import add_running, queue
from apiv1.work_processors import HostBackupWorkProcessors, HostSnapshotFinishHelper, FileBackupWorkProcessors, \
    file_backup_api
from box_dashboard import xlogging, boxService, xdata
from xdashboard.common.smtp import send_email
from xdashboard.models import Email

_logger = xlogging.getLogger(__name__)

import KTService


def create_compress_task_after_task(disk_snapshot_objects):
    for disk_snapshot in disk_snapshot_objects:
        if CompressTask.objects.filter(disk_snapshot=disk_snapshot).exists():
            _logger.error('create_compress_task_after_task fail, task is already exists')
            return None
        else:
            _logger.debug(
                'create_compress_task_after_task successful, disk_snapshot id is :{}'.format(
                    disk_snapshot.id))
            CompressTask.objects.create(disk_snapshot=disk_snapshot)


@xlogging.convert_exception_to_value(None)
def delete_shell_zip_if_exist(host_obj):
    shell_infos = HostBackupWorkProcessors.get_shell_infos_from_schedule_or_host(host_obj, None)
    if shell_infos is None:
        return

    boxService.box_service.remove(shell_infos['zip_path'])


def finish_migrate_task_object(thread_name, migrate_task_object_id, successful, description, debug, partial=False):
    migrate_task_object = MigrateTask.objects.get(id=migrate_task_object_id)

    force_kill_kvm(migrate_task_object)

    if (migrate_task_object.source_type == MigrateTask.SOURCE_TYPE_TEMP_NORMAL) and (
            migrate_task_object.host_snapshot is not None):
        if partial:
            if migrate_task_object.host_snapshot.finish_datetime is None:
                migrate_task_object.host_snapshot.finish_datetime = timezone.now()
                migrate_task_object.host_snapshot.successful = True
                migrate_task_object.host_snapshot.partial = True
                migrate_task_object.host_snapshot.save(update_fields=['finish_datetime', 'successful', 'partial'])
            else:
                migrate_task_object.host_snapshot.partial = True
                migrate_task_object.host_snapshot.save(update_fields=['partial'])
        else:
            SpaceCollectionWorker.set_host_snapshot_deleting_and_collection_space_later(
                migrate_task_object.host_snapshot)

    if migrate_task_object.finish_datetime is not None:
        _logger.warning(r'finish_migrate_task_object {} return by do nothing'.format(thread_name))
        return

    migrate_task_object.finish_datetime = timezone.now()
    migrate_task_object.successful = successful
    migrate_task_object.save(update_fields=['finish_datetime', 'successful'])

    host_ip = migrate_task_object.source_host.last_ip
    restore_target = migrate_task_object.restore_target
    pe_ip = json.loads(restore_target.info)['remote_ip'] if restore_target else '--'

    user_id = migrate_task_object.source_host.user_id
    if successful:
        desc = r'迁移"{host_ip}"到"{pe_ip}" 成功'.format(host_ip=host_ip, pe_ip=pe_ip)
        HostLog.objects.create(
            host=migrate_task_object.source_host, type=HostLog.LOG_MIGRATE_SUCCESSFUL,
            reason=json.dumps(
                {'migrate_task': migrate_task_object.id, 'description': desc, 'task_type': 'migrate_task'},
                ensure_ascii=False))
        send_email(Email.MIGRATE_SUCCESS, {'desc': desc, 'user_id': user_id})
    else:
        desc = r'迁移"{host_ip}"到"{pe_ip}" 失败'.format(host_ip=host_ip, pe_ip=pe_ip)
        HostLog.objects.create(
            host=migrate_task_object.source_host, type=HostLog.LOG_MIGRATE_FAILED,
            reason=json.dumps(
                {'migrate_task': migrate_task_object.id, 'debug': debug,
                 'description': desc + '：' + description, 'task_type': 'migrate_task'},
                ensure_ascii=False))
        send_email(Email.MIGRATE_FAILED, {'desc': desc + '({})'.format(description), 'user_id': user_id})

        if (migrate_task_object.source_type == MigrateTask.SOURCE_TYPE_TEMP_NORMAL) and (
                migrate_task_object.host_snapshot is not None):
            force_close_files = list()
            for disk_snapshot in migrate_task_object.host_snapshot.disk_snapshots.all():
                force_close_files.append(disk_snapshot.ident)
            HostBackupWorkProcessors.forceCloseBackupFiles(force_close_files)

    HostSnapshotFinishHelper.stopBackupOptimize(migrate_task_object.host_snapshot)

    if migrate_task_object.restore_target is None:
        _logger.warning(r'finish_migrate_task_object {} return by no restore_target'.format(thread_name))
        return
    restore_target = RestoreTarget.objects.get(id=migrate_task_object.restore_target.id)
    if restore_target.finish_datetime is not None:
        _logger.warning(r'_finish_backup_task_object {} return by restore_target.finish_datetime'.format(thread_name))
        return
    PeRestore.end_restore_optimize(restore_target)
    restore_target.finish_datetime = migrate_task_object.finish_datetime
    restore_target.successful = migrate_task_object.successful
    restore_target.save(update_fields=['finish_datetime', 'successful'])

    delete_shell_zip_if_exist(host_obj=migrate_task_object.source_host)


def finish_restore_task_object(thread_name, restore_task_object_id, successful, description, debug):
    restore_task_object = RestoreTask.objects.get(id=restore_task_object_id)

    force_kill_kvm(restore_task_object)

    if restore_task_object.finish_datetime is not None:
        _logger.warning(r'finish_restore_task_object {} return by do nothing'.format(thread_name))
        return

    restore_task_object.finish_datetime = timezone.now()
    restore_task_object.successful = successful
    restore_task_object.save(update_fields=['finish_datetime', 'successful'])

    host_snapshot = restore_task_object.host_snapshot
    restore_target = restore_task_object.restore_target
    plan_obj = host_snapshot.schedule

    pe_info = json.loads(restore_target.info)
    plan_name = plan_obj.name if plan_obj else host_snapshot.host.display_name,
    start_time = pe_info['restore_time'] if restore_target else '--',
    pe_ip = pe_info['remote_ip'] if restore_target else '--'

    user_id = restore_task_object.host_snapshot.host.user_id
    if successful:
        if is_restore_target_belong_htb(restore_task_object.restore_target):
            log_type = HostLog.LOG_HTB
            plan_name, start_time = get_plan_name_start_time_for_htb(restore_target.htb_task.last(), plan_name,
                                                                     start_time)
            desc = r'传输数据"{0}:{1}"到"{2}"完成'.format(plan_name, start_time, pe_ip)
        else:
            log_type = HostLog.LOG_RESTORE_SUCCESSFUL
            desc = r'还原备份点"{0}:{1}"到"{2}" 成功'.format(plan_name, start_time, pe_ip)
        HostLog.objects.create(host=restore_task_object.host_snapshot.host, type=log_type,
                               reason=json.dumps({'restore_task': restore_task_object.id, 'description': desc,
                                                  'task_type': 'restore_task',
                                                  'stage': 'TASK_STEP_IN_PROGRESS_RESTORE_SUCCESS'},
                                                 ensure_ascii=False))
        send_email(Email.RESTORE_SUCCESS, {'desc': desc, 'user_id': user_id})
    else:
        if is_restore_target_belong_htb(restore_task_object.restore_target):
            log_type = HostLog.LOG_HTB
        else:
            log_type = HostLog.LOG_RESTORE_FAILED
        desc = r'还原备份点"{0}:{1}"到"{2}" 失败'.format(plan_name, start_time, pe_ip)
        HostLog.objects.create(host=restore_task_object.host_snapshot.host, type=log_type,
                               reason=json.dumps({'restore_task': restore_task_object.id, 'debug': debug,
                                                  'description': desc + '：' + description,
                                                  'task_type': 'restore_task',
                                                  'stage': 'OPERATE_TASK_STATUS_DESELECT' if description == '用户取消任务' else 'TASK_STEP_IN_PROGRESS_RESTORE_FAILED'},
                                                 ensure_ascii=False))
        send_email(Email.RESTORE_FAILED, {'desc': desc + '({})'.format(description), 'user_id': user_id})

    if restore_task_object.restore_target is None:
        _logger.warning(r'finish_restore_task_object {} return by no restore_target'.format(thread_name))
        return
    restore_target = RestoreTarget.objects.get(id=restore_task_object.restore_target.id)
    if restore_target.finish_datetime is not None:
        _logger.warning(r'finish_restore_task_object {} return by restore_target.finish_datetime'.format(thread_name))
        return
    PeRestore.end_restore_optimize(restore_target)
    restore_target.finish_datetime = restore_task_object.finish_datetime
    restore_target.successful = restore_task_object.successful
    restore_target.save(update_fields=['finish_datetime', 'successful'])


def get_plan_name_start_time_for_htb(htb_task, plan_name, start_time):
    try:
        if not htb_task:
            return plan_name, start_time
        schedule = htb_task.schedule
        src_host = htb_task.schedule.host
        exc_info = json.loads(schedule.ext_config)
        item_list = exc_info['manual_switch']['point_id'].split('|')
        time_str = exc_info['manual_switch']['restoretime']
        if src_host.is_remote:
            plan_name = src_host.display_name
        else:
            plan_name = HostSnapshot.objects.get(id=item_list[1]).schedule.name
        if item_list[0] == 'normal':
            start_time = item_list[2].replace('T', ' ')
        else:
            start_time = time_str.replace('T', ' ')
    except Exception as e:
        _logger.error('get_plan_name_start_time_for_htb error:{}'.format(e), exc_info=True)
    return plan_name, start_time


# 检查计划关联的存储结点，及该计划的配额使用情况：若存在异常，发送邮件通知用户
@xlogging.convert_exception_to_value(None)
def check_user_storage_node_and_quota_then_send_email(plan_obj):
    user_quota = UserQuota.objects.filter(user_id=plan_obj.host.user.id, deleted=False).filter(
        storage_node__ident=plan_obj.storage_node_ident, storage_node__deleted=False).first()
    user_caution = user_quota.caution_size
    user_info = {'user_id': plan_obj.host.user.id, 'storage_node_ident': plan_obj.storage_node_ident}
    try:
        UserQuotaTools.check_user_storage_size_in_node(schedule_object=plan_obj, more_than_mb=user_caution)
    except xlogging.BoxDashboardException as e:
        if e.http_status == xlogging.HTTP_STATUS_USER_STORAGE_NODE_NOT_ENOUGH_SPACE:
            send_email(Email.STORAGE_NODE_NOT_ENOUGH_SPACE, user_info)
            return
        if e.http_status == xlogging.HTTP_STATUS_USER_STORAGE_NODE_NOT_ONLINE:
            send_email(Email.STORAGE_NODE_NOT_ONLINE, user_info)
            return
        if e.http_status == xlogging.HTTP_STATUS_USER_STORAGE_NODE_NOT_VALID:
            send_email(Email.STORAGE_NODE_NOT_VALID, user_info)
            return
        raise e


# CDP结束，根据结束类型，发送邮件通知用户
@xlogging.convert_exception_to_value(None)
def _check_cdp_task_status_then_send_email(host, types, reason):
    hostname = host.display_name
    reasons = '客户端：{0},'.format(hostname)
    reasons = reasons + reason
    user_info = {'user_id': host.user.id, 'content': reasons}
    send_email(types, user_info)


def finish_cdp_task_object(thread_name, cdp_task_object_id, successful, description, debug, user_cancel,
                           partial=False, stop_cdp_cmd=True):
    check_user_storage_node_and_quota_then_send_email(CDPTask.objects.get(id=cdp_task_object_id).schedule)

    cdp_task_object = CDPTask.objects.get(id=cdp_task_object_id)
    if cdp_task_object.finish_datetime is not None:
        _logger.warning(r'finish_cdp_task_object {} return by do nothing'.format(thread_name))
        return

    if cdp_task_object.host_snapshot is not None and cdp_task_object.host_snapshot.finish_datetime is not None:
        successful = cdp_task_object.host_snapshot.successful  # 修正临界区的值
        partial = cdp_task_object.host_snapshot.partial

    cdp_task_object.finish_datetime = timezone.now()
    cdp_task_object.successful = successful
    cdp_task_object.save(update_fields=['finish_datetime', 'successful'])

    if successful:
        if user_cancel:
            HostLog.objects.create(host=cdp_task_object.schedule.host, type=HostLog.LOG_CDP_STOP,
                                   reason=json.dumps({'cdp_task': cdp_task_object.id, 'task_type': 'cdp_task',
                                                      'stage': 'TASK_STEP_IN_PROGRESS_CDP_STOP'}, ensure_ascii=False))

            _check_cdp_task_status_then_send_email(host=cdp_task_object.schedule.host, types=Email.CDP_STOP,
                                                   reason='')
        else:
            HostLog.objects.create(host=cdp_task_object.schedule.host, type=HostLog.LOG_CDP_PAUSE,
                                   reason=json.dumps({'cdp_task': cdp_task_object.id, 'debug': debug,
                                                      'description': description,
                                                      'task_type': 'cdp_task',
                                                      'stage': 'OPERATE_TASK_STATUS_PAUSE'}, ensure_ascii=False))

            _check_cdp_task_status_then_send_email(host=cdp_task_object.schedule.host, types=Email.CDP_PAUSE,
                                                   reason='CDP暂停，原因：' + description)

    else:
        HostLog.objects.create(host=cdp_task_object.schedule.host, type=HostLog.LOG_CDP_FAILED,
                               reason=json.dumps({'cdp_task': cdp_task_object.id, 'debug': debug,
                                                  'description': description,
                                                  'task_type': 'cdp_task',
                                                  'stage': 'TASK_STEP_IN_PROGRESS_CDP_FAILED'}, ensure_ascii=False))

        _check_cdp_task_status_then_send_email(host=cdp_task_object.schedule.host, types=Email.CDP_FAILED,
                                               reason='CDP失败，原因：' + description)

    if cdp_task_object.host_snapshot is None:
        _logger.warning(r'finish_cdp_task_object {} return by no host_snapshot'.format(thread_name))
        return

    if stop_cdp_cmd:
        # 因为客户端驱动中的多线程竞争问题，部分情况下默认客户端主动停止CDP，不可从服务器端发起调用
        host_ident = cdp_task_object.host_snapshot.host.ident
        try:
            boxService.box_service.stopCdpStatus(host_ident)
        except Exception as e:
            _logger.warning(
                r'finish_cdp_task_object {} call stopCdpStatus {} failed {}'.format(thread_name, host_ident, e))

    host_snapshot = HostSnapshot.objects.get(id=cdp_task_object.host_snapshot.id)  # 重新获取
    if host_snapshot.finish_datetime is None:
        host_snapshot.finish_datetime = cdp_task_object.finish_datetime
        host_snapshot.successful = cdp_task_object.successful or partial
        host_snapshot.partial = (not cdp_task_object.successful) and partial
        host_snapshot.save(update_fields=['finish_datetime', 'successful', 'partial'])
    else:
        if host_snapshot.successful != cdp_task_object.successful:
            _logger.warning(r'CDP任务最终状态与主机快照状态不一致 host_snapshot.successful != cdp_task_object.successful'
                            r' {} - {}'.format(host_snapshot.successful, cdp_task_object.successful))
            if host_snapshot.successful:
                host_snapshot.successful = cdp_task_object.successful
                host_snapshot.save(update_fields=['successful'])

    cdp_host_snapshot = host_snapshot.cdp_info
    cdp_host_snapshot.stopped = True
    cdp_host_snapshot.save(update_fields=['stopped'])

    if (not successful) or partial:  # 不成功或者不完整的点，cdp部分都不能保留
        HostBackupWorkProcessors.deal_cdp_when_backup_failed(host_snapshot, cdp_task_object, partial)
        force_close_files = list()
        for disk_snapshot in host_snapshot.disk_snapshots.all():
            force_close_files.append(disk_snapshot.ident)
        HostBackupWorkProcessors.forceCloseBackupFiles(force_close_files)
    else:
        HostBackupWorkProcessors.deal_cdp_when_backup_ok(host_snapshot, cdp_task_object)

    HostSnapshotFinishHelper.stopBackupOptimize(host_snapshot)
    DiskSnapshotHash.reorganize_hash_file(host_snapshot)


def _is_backup_task_canceled(backup_task):
    ext_config = json.loads(backup_task.ext_config)
    return xdata.CANCEL_TASK_EXT_KEY in ext_config


def get_systeminfo_for_proxy_agent(host_snapshot, logic):
    from apiv1.snapshot import GetSnapshotList
    host_snapshot_id = host_snapshot.id
    params = {
        "logic": logic,
        "system_type": 32,
        "vnc": None,
        "shutdown": True,
        "cmd_list": [
            {"cmd": r"%SYSTEMDRIVE%\Python36\python.exe diskinfo.py", "work_dir": r'X:\patch\systeminfo',
             "timeouts": None, "post_result_url": "xdashboard/vmrestore_handle/?a=systeminfo",
             "post_result_params": {"host_snapshot_id": host_snapshot_id}}
        ]
    }
    mdisk_snapshots = DiskSnapshot.objects.filter(host_snapshot=host_snapshot_id)
    if len(mdisk_snapshots) == 0:
        xlogging.raise_and_logging_error('不存在的客户端快照', 'invalid host snapshot id', status.HTTP_404_NOT_FOUND)
    disk_devices = list()
    for disk_snapshot in mdisk_snapshots:
        device_profile = dict()
        disks = list()
        device_profile['nbd'] = json.loads(boxService.box_service.NbdFindUnusedReverse())
        if params['vnc'] is None:
            params['vnc'] = device_profile['nbd']['vnc_address'].split(':')[1]
        restore_timestamp = None
        disk_snapshot_object = DiskSnapshot.objects.get(ident=disk_snapshot.ident)

        if disk_snapshot_object is None:
            xlogging.raise_and_logging_error('获取硬盘快照信息失败', r'get disk info failed disk_snapshot_object is None')

        validator_list = [GetSnapshotList.is_disk_snapshot_object_exist,
                          GetSnapshotList.is_disk_snapshot_file_exist]
        disk_snapshots = GetSnapshotList.query_snapshots_by_snapshot_object(disk_snapshot_object, validator_list,
                                                                            restore_timestamp)
        if len(disk_snapshots) == 0:
            xlogging.raise_and_logging_error('获取硬盘快照信息失败',
                                             r'get disk info failed name {} time {}'.format(disk_snapshot.ident,
                                                                                            restore_timestamp))

        for disk_snapshot_m in disk_snapshots:
            disks.append({"path": disk_snapshot_m.path, "ident": disk_snapshot_m.snapshot})
        disk_devices.append({"device_profile": device_profile, "disk_snapshots": disks, "disk_ident": None})

    params['disk_devices'] = disk_devices
    boxService.box_service.kvm_remote_procedure_call(
        json.dumps({'action': 'new', 'key': 'systeminfo_{}'.format(host_snapshot_id), 'info': params}))


def _finish_backup_task_object(thread_name, backup_task_object_id, successful, description, debug, partial=False,
                               will_retry=False):
    # 是否发送邮件给用户
    check_user_storage_node_and_quota_then_send_email(BackupTask.objects.get(id=backup_task_object_id).schedule)
    # 始终记录本次备份任务情况
    backup_task_object = BackupTask.objects.get(id=backup_task_object_id)
    backup_task_object.finish_datetime = timezone.now()
    backup_task_object.successful = successful
    backup_task_object.save(update_fields=['finish_datetime', 'successful'])

    # agent报告(设置了host_snapshot)本次备份的结果，是成功的
    user_id = backup_task_object.schedule.host.user_id
    if successful:
        HostLog.objects.create(host=backup_task_object.schedule.host, type=HostLog.LOG_BACKUP_SUCCESSFUL,
                               reason=json.dumps({'backup_task': backup_task_object.id, 'task_type': 'backup_task',
                                                  'stage': 'TASK_STEP_IN_PROGRESS_BACKUP_SUCCESS'}, ensure_ascii=False))
        send_email(Email.BACKUP_SUCCESS,
                   {'desc': '执行任务"{0}"成功'.format(backup_task_object.schedule.name), 'user_id': user_id})

    # agent报告(设置了host_snapshot)本次备份的结果，是失败的 或：等待agent报告时出现异常
    else:
        if _is_backup_task_canceled(backup_task_object):
            stage = r'OPERATE_TASK_STATUS_DESELECT'
        else:
            stage = r'TASK_STEP_IN_PROGRESS_BACKUP_FAILED'
        HostLog.objects.create(host=backup_task_object.schedule.host, type=HostLog.LOG_BACKUP_FAILED, reason=json.dumps(
            {'backup_task': backup_task_object.id, 'debug': debug, 'description': description,
             'task_type': 'backup_task', 'stage': stage}, ensure_ascii=False))
        # 如果将要重试的话，就不发邮件
        if will_retry:
            _logger.warning('{} backup failed, will retry, not send email'.format(backup_task_object.schedule.name))
        else:
            _logger.info('{} backup failed, will send email'.format(backup_task_object.schedule.name))
            send_email(
                Email.BACKUP_FAILED,
                {'desc': '执行任务"{0}"失败({1})'.format(backup_task_object.schedule.name, description), 'user_id': user_id})

        if backup_task_object.host_snapshot is None:
            _logger.warning(r'_finish_backup_task_object {} return by no host_snapshot'.format(thread_name))
            return

        host_snapshot = backup_task_object.host_snapshot
        if host_snapshot.finish_datetime is None:
            host_snapshot.finish_datetime = backup_task_object.finish_datetime
            host_snapshot.successful = partial
            host_snapshot.save(update_fields=['finish_datetime', 'successful'])

        force_close_files = list()
        for disk_snapshot in host_snapshot.disk_snapshots.all():
            force_close_files.append(disk_snapshot.ident)

        HostBackupWorkProcessors.forceCloseBackupFiles(force_close_files)

        if not partial:
            _logger.warning(r'_finish_backup_task_object {} unsuccessful and partial'.format(thread_name))
            task_objects = list()
            for disk_snapshot in host_snapshot.disk_snapshots.all():
                disk_snapshot.merged = True
                disk_snapshot.save(update_fields=['merged'])

                task_objects.append(DeleteDiskSnapshotTask(
                    DeleteDiskSnapshotTask.create(disk_snapshot.image_path, disk_snapshot.ident)
                ))
            for task_object in task_objects:
                task_object.work()
    # TODO 不开放功能，合并代码
    # if backup_task_object.host_snapshot.host.type == Host.PROXY_AGENT:
    #    logic = 'windows'
    #    get_systeminfo_for_proxy_agent(backup_task_object.host_snapshot, logic)
    HostSnapshotFinishHelper.stopBackupOptimize(backup_task_object.host_snapshot)
    DiskSnapshotHash.reorganize_hash_file(backup_task_object.host_snapshot)


def _finish_file_backup_task_object(thread_name, backup_task_object_id, successful, description, debug, partial=False):
    backup_task_object = FileBackupTask.objects.get(id=backup_task_object_id)
    try:
        # 是否发送邮件给用户
        check_user_storage_node_and_quota_then_send_email(backup_task_object.schedule)
        # 始终记录本次备份任务情况
        backup_task_object.finish_datetime = timezone.now()
        backup_task_object.successful = successful
        backup_task_object.save(update_fields=['finish_datetime', 'successful'])

        # agent报告(设置了host_snapshot)本次备份的结果，是成功的
        user_id = backup_task_object.schedule.host.user_id
        if successful:
            HostLog.objects.create(host=backup_task_object.schedule.host, type=HostLog.LOG_BACKUP_SUCCESSFUL,
                                   reason=json.dumps(
                                       {'backup_task': backup_task_object.id, 'task_type': 'file_backup_task',
                                        'stage': 'TASK_STEP_IN_PROGRESS_BACKUP_SUCCESS'}, ensure_ascii=False))
            send_email(Email.BACKUP_SUCCESS,
                       {'desc': '执行任务"{0}"成功'.format(backup_task_object.schedule.name), 'user_id': user_id})

        # agent报告(设置了host_snapshot)本次备份的结果，是失败的 或：等待agent报告时出现异常
        else:
            if _is_backup_task_canceled(backup_task_object):
                stage = r'OPERATE_TASK_STATUS_DESELECT'
            else:
                stage = r'TASK_STEP_IN_PROGRESS_BACKUP_FAILED'
            HostLog.objects.create(host=backup_task_object.schedule.host, type=HostLog.LOG_BACKUP_FAILED,
                                   reason=json.dumps(
                                       {'backup_task': backup_task_object.id, 'debug': debug,
                                        'description': description,
                                        'task_type': 'file_backup_task', 'stage': stage}, ensure_ascii=False))
            send_email(
                Email.BACKUP_FAILED,
                {'desc': '执行任务"{0}"失败({1})'.format(backup_task_object.schedule.name, description), 'user_id': user_id})

            if backup_task_object.host_snapshot is None:
                _logger.warning(r'_finish_file_backup_task_object {} return by no host_snapshot'.format(thread_name))
                return

            host_snapshot = backup_task_object.host_snapshot
            if host_snapshot.finish_datetime is None:
                host_snapshot.finish_datetime = backup_task_object.finish_datetime
                host_snapshot.successful = partial
                host_snapshot.save(update_fields=['finish_datetime', 'successful'])

            _logger.warning(r'_finish_file_backup_task_object {} unsuccessful and partial'.format(thread_name))
            task_objects = list()
            for disk_snapshot in host_snapshot.disk_snapshots.all():
                disk_snapshot.merged = True
                disk_snapshot.save(update_fields=['merged'])

                task_objects.append(DeleteDiskSnapshotTask(
                    DeleteDiskSnapshotTask.create(disk_snapshot.image_path, disk_snapshot.ident)
                ))
            for task_object in task_objects:
                task_object.work()
    finally:
        file_backup_api('delete', backup_task_object.schedule.host.ident, '')


def _check_host_snapshot_status(host_ident, host_snapshot_id):
    host_snapshot_object = HostSnapshot.objects.get(id=host_snapshot_id)
    if host_snapshot_object.finish_datetime is not None:
        if host_snapshot_object.successful:
            if host_snapshot_object.partial:
                _logger.error(
                    '_check_host_snapshot_status hostsnapshot is finished, but snapshot_id:{} is partial'.format(
                        host_snapshot_object.id))
                TaskHelper.raise_last_backup_error(host_snapshot_object)
            else:
                return False

    host_status_list = boxService.box_service.GetStatus(host_ident)
    if 'off_line' in host_status_list:
        host_status_list = TaskHelper.wait_host_status_list_is_not_off_line(host_ident, '_check_host_snapshot_status')

    if 'backup' not in host_status_list:
        host_snapshot_object = HostSnapshot.objects.get(id=host_snapshot_id)
        if (host_snapshot_object.finish_datetime is None) or (not host_snapshot_object.successful) or \
                host_snapshot_object.partial:
            _logger.error(r'{} _check_host_snapshot_status {}'.format(host_ident, host_status_list))
            TaskHelper.raise_last_backup_error(host_snapshot_object)

    return True


def _check_backup_task_status(host_ident, backup_task_object_id, user_cancel_check):
    backup_task_object = BackupTask.objects.get(id=backup_task_object_id)

    host_snapshot_object = backup_task_object.host_snapshot
    if host_snapshot_object.finish_datetime is not None:  # agent报告备份完成了
        if host_snapshot_object.successful:
            if host_snapshot_object.partial:
                _logger.error(
                    '_check_backup_task_status task finished, snapshot_id:{} is partial'.format(
                        host_snapshot_object.id))
                TaskHelper.raise_last_backup_error(host_snapshot_object)
            else:
                return False
        else:
            TaskHelper.raise_last_backup_error(host_snapshot_object)

    user_cancel_check.check()

    host_status_list = boxService.box_service.GetStatus(host_ident)
    if 'off_line' in host_status_list:
        host_status_list = TaskHelper.wait_host_status_list_is_not_off_line(host_ident, '_check_backup_task_status')

    if 'backup' not in host_status_list:
        host_snapshot_object = BackupTask.objects.get(id=backup_task_object_id).host_snapshot
        if (host_snapshot_object.finish_datetime is None) or (not host_snapshot_object.successful):
            _logger.error(r'{} _check_backup_task_status {}'.format(host_ident, host_status_list))
            TaskHelper.raise_last_backup_error(host_snapshot_object)

    return True


def _check_file_backup_task_status(host_ident, backup_task_object_id, user_cancel_check):
    backup_task_object = FileBackupTask.objects.get(id=backup_task_object_id)

    host_snapshot_object = backup_task_object.host_snapshot
    if host_snapshot_object.finish_datetime is not None:  # agent报告备份完成了
        if host_snapshot_object.successful:
            if host_snapshot_object.partial:
                _logger.error(
                    '_check_backup_task_status task finished, snapshot_id:{} is partial'.format(
                        host_snapshot_object.id))
                file_backup_api('raise_last_error', host_ident, '')
            else:
                return False
        else:
            file_backup_api('raise_last_error', host_ident, '')
    else:
        if 'alive' not in file_backup_api('poll', host_ident, ''):
            file_backup_api('raise_last_error', host_ident, '')
    user_cancel_check.check()

    return True


@xlogging.convert_exception_to_value(None)
def _queryLastCdpError(host_ident):
    return boxService.box_service.queryLastCdpError(host_ident)


def _raise_last_cdp_error(host_ident, is_check_cdp_task_status_first):
    error_string = _queryLastCdpError(host_ident)
    if error_string is None:
        xlogging.raise_and_logging_error('无法获取错误代码，内部模块通信异常', r'{} _raise_last_cdp_error'.format(host_ident))
    if len(error_string) == 0:
        xlogging.raise_and_logging_error('网络通信异常，客户端离线', r'{} _raise_last_cdp_error'.format(host_ident))

    error_int = int(error_string, 16)
    if error_int == 1:
        xlogging.raise_and_logging_error(r'传输CDP数据失败，网络超时',
                                         r'{} _raise_last_cdp_error {}'.format(host_ident, error_int))
    elif error_int == 2:
        xlogging.raise_and_logging_error(r'暂停发送CDP数据，Agent检测到磁盘IO繁忙',
                                         r'{} _raise_last_cdp_error {}'.format(host_ident, error_int))
    elif error_int == 3:
        xlogging.raise_and_logging_error(r'缓存CDP数据失败，Agent系统重启',
                                         r'{} _raise_last_cdp_error {}'.format(host_ident, error_int))
    elif error_int == 4:
        xlogging.raise_and_logging_error(r'用户取消，进入还原状态',
                                         r'{} _raise_last_cdp_error {}'.format(host_ident, error_int))
    elif error_int == 5:
        xlogging.raise_and_logging_error(r'受保护的磁盘被移除',
                                         r'{} _raise_last_cdp_error {}'.format(host_ident, error_int))
    elif is_check_cdp_task_status_first:
        xlogging.raise_and_logging_error(r'暂停发送CDP数据，Agent检测到磁盘IO繁忙',
                                         r'{} _raise_last_cdp_error is_check_cdp_task_status_first'.format(host_ident))
    else:
        xlogging.raise_and_logging_error(r'内部异常，代码2319，查询不到cdp状态',
                                         r'{} _raise_last_cdp_error {}'.format(host_ident, error_int))


def _check_cdp_task_status(host_ident, cdp_task_object_id, is_check_cdp_task_status_first):
    cdp_task_object = CDPTask.objects.get(id=cdp_task_object_id)
    if not cdp_task_object.schedule.enabled or cdp_task_object.schedule.deleted:
        return False
    else:
        host_status_list = boxService.box_service.GetStatus(host_ident)
        if 'off_line' in host_status_list:
            host_status_list = TaskHelper.wait_host_status_list_is_not_off_line(host_ident, '_check_cdp_task_status')

        cdp_mode_type = json.loads(cdp_task_object.schedule.ext_config)['cdpSynchAsynch']
        if cdp_mode_type == xdata.CDP_MODE_SYN:
            if 'cdp_asy' in host_status_list:
                xlogging.raise_and_logging_error('CDP模式与计划设定不一致',
                                                 r'{} _check_cdp_task_status {}'.format(host_ident, host_status_list))
            elif 'cdp_syn' not in host_status_list:
                _logger.error(r'cdp_task_object_id {} {} _check_cdp_task_status {}'.format(
                    cdp_task_object_id, host_ident, host_status_list))
                _raise_last_cdp_error(host_ident, is_check_cdp_task_status_first)
        elif cdp_mode_type == xdata.CDP_MODE_ASYN:
            if 'cdp_syn' in host_status_list:
                xlogging.raise_and_logging_error('CDP模式与计划设定不一致',
                                                 r'{} _check_cdp_task_status {}'.format(host_ident, host_status_list))
            elif 'cdp_asy' not in host_status_list:
                _logger.error(r'cdp_task_object_id {} {} _check_cdp_task_status {}'.format(
                    cdp_task_object_id, host_ident, host_status_list))
                _raise_last_cdp_error(host_ident, is_check_cdp_task_status_first)
    return True


def update_cdp_host_snapshot_last_datetime(cdp_task_object_id):
    cdp_task_object = CDPTask.objects.get(id=cdp_task_object_id)
    cdp_host_snapshot_object = cdp_task_object.host_snapshot.cdp_info
    cdp_host_snapshot_object.last_datetime = timezone.now()
    cdp_host_snapshot_object.save(update_fields=['last_datetime'])


def is_force_full_by_config(config, task_logger):
    if config and config['type'] == xdata.BACKUP_TASK_SCHEDULE_EXECUTE_TYPE_FORCE_FULL:
        task_logger(r'force_full by current')
        return True
    return False


def is_force_store_full_by_config(config, task_logger):
    if config and config['force_store_full'] == xdata.BACKUP_TASK_SCHEDULE_STORE_AS_FULL:
        task_logger(r'force_store_full by current')
        return True
    return False


def is_force_full_by_schedule(schedule, task_logger, check_type=None):
    ext_config = json.loads(schedule.ext_config)

    if (check_type is None or check_type == 1) and \
            ext_config.get('incMode', xdata.BACKUP_TASK_SCHEDULE_EXECUTE_TYPE_AUTO) == \
            xdata.BACKUP_TASK_SCHEDULE_EXECUTE_TYPE_FORCE_FULL:
        task_logger(r'force_full by schedule')
        return True

    next_force_full = ext_config.get('next_force_full', False)
    if (check_type is None or check_type == 2) and next_force_full:
        task_logger(r'force_full by next_force_full')
        ext_config['next_force_full'] = False
        schedule.ext_config = json.dumps(ext_config, ensure_ascii=False)
        schedule.save(update_fields=['ext_config'])
        return True

    return False


class Sleep(object):

    def __init__(self, schedule_id, sender=None):
        self.schedule_id = schedule_id
        self._event = threading.Event()
        if sender is None:
            sender = BackupTaskSchedule
        end_sleep.connect(self.end, sender=sender)

    def end(self, sender, **kwargs):
        if kwargs['schedule_id'] == self.schedule_id:
            self._event.set()
            _logger.info('Sleep end sleep by signal, schedule_id:{}'.format(self.schedule_id))
        else:
            pass

    def sleep(self, stime=120):
        self._event.wait(stime)
        self._event.clear()


class BackupScheduleRetryHandle(object):
    """
    提供备份失败后计划重试的静态方法
    """

    @staticmethod
    def _get_backup_retry_options(schedule):
        enable, count, interval = True, 5, 3
        ext_config = json.loads(schedule.ext_config)
        if 'backup_retry' in ext_config:
            enable = ext_config['backup_retry']['enable']
            count = int(ext_config['backup_retry']['count'])
            interval = int(ext_config['backup_retry']['interval'])
        else:
            ext_config['backup_retry'] = {'enable': enable, 'count': count, 'interval': interval}
            schedule.ext_config = json.dumps(ext_config, ensure_ascii=False)
            schedule.save(update_fields=['ext_config', ])
        return enable, count, interval if interval > 2 else 2

    @staticmethod
    def _update_next_run_time(schedule, mins):
        schedule.next_run_date = timezone.now() + timedelta(minutes=mins)
        schedule.save(update_fields=['next_run_date'])
        _logger.warning('modify schedule:{} next_run_date to:{}, call by modify_next_run_date_by_backup_retry'.format(
            schedule.id, schedule.next_run_date))

    @staticmethod
    def clean(schedule):
        if isinstance(schedule, BackupTaskSchedule):
            schedule = BackupTaskSchedule.objects.get(id=schedule.id)
        elif isinstance(schedule, ClusterBackupSchedule):
            schedule = ClusterBackupSchedule.objects.get(id=schedule.id)
        else:
            return None

        ext_config = json.loads(schedule.ext_config)
        ext_config['execute_schedule_retry'] = 0
        schedule.ext_config = json.dumps(ext_config, ensure_ascii=False)
        schedule.save(update_fields=['ext_config', ])

    @staticmethod
    @xlogging.convert_exception_to_value(False)
    def modify(schedule, check_retry_count=True, backup_task=None):
        """
        :param schedule:
        :param check_retry_count: 是否检测重试次数，如果是True，无条件需要重试
        :param backup_task:
        :return: True(需要重试) False(不需要重试)
        """
        if backup_task and _is_backup_task_canceled(backup_task):
            BackupScheduleRetryHandle.clean(schedule)
            return False

        if isinstance(schedule, BackupTaskSchedule):
            schedule = BackupTaskSchedule.objects.get(id=schedule.id)
        elif isinstance(schedule, ClusterBackupSchedule):
            schedule = ClusterBackupSchedule.objects.get(id=schedule.id)
        else:
            return False
        enable, count, interval = BackupScheduleRetryHandle._get_backup_retry_options(schedule)

        timezone_now = timezone.now()
        interval_timedelta = timedelta(minutes=interval)
        wait_next_datetime = timezone_now + interval_timedelta

        need_update_types = [BackupTaskSchedule.CYCLE_ONCE,
                             BackupTaskSchedule.CYCLE_PERDAY,
                             BackupTaskSchedule.CYCLE_PERWEEK,
                             BackupTaskSchedule.CYCLE_PERMONTH,
                             ]
        if schedule.cycle_type in need_update_types and enable:
            next_run_date = schedule.next_run_date
        else:
            next_run_date = None

        will_retry = False
        if next_run_date and wait_next_datetime >= next_run_date:
            retry_max_count = -1
            _logger.info('modify_next_run_date_by_backup_retry schedule:{} will exe soon, no need update!'.format(
                schedule.id))
            will_retry = True
        else:
            retry_max_count = count

        if (not check_retry_count) and retry_max_count != -1:
            will_retry = True
            _logger.info(
                'modify_next_run_date_by_backup_retry update schedule {} next run datetime, by not check_retry_count'.format(
                    schedule.id))
            BackupScheduleRetryHandle._update_next_run_time(schedule, interval)
            return will_retry
        else:
            ext_config = json.loads(schedule.ext_config)
            execute_schedule_retry = ext_config.get('execute_schedule_retry', 0)
            if execute_schedule_retry < retry_max_count:
                _logger.info(
                    'modify_next_run_date_by_backup_retry update schedule {} next run datetime, by retry_max_count'.format(
                        schedule.id))
                BackupScheduleRetryHandle._update_next_run_time(schedule, interval)
                ext_config['execute_schedule_retry'] = execute_schedule_retry + 1
                will_retry = True
            else:
                ext_config['execute_schedule_retry'] = 0

            schedule.ext_config = json.dumps(ext_config, ensure_ascii=False)
            schedule.save(update_fields=['ext_config', ])
            return will_retry


class _WorkerLog(object):
    name = ''

    def log_info(self, msg):
        _logger.info('{}: {}'.format(self.name, msg))

    def log_warnning(self, msg):
        _logger.warnning('{}: {}'.format(self.name, msg))

    def log_error(self, msg):
        _logger.error('{}: {}'.format(self.name, msg), exc_info=True)


class BackupTaskWorker(threading.Thread, _WorkerLog):
    # schedule_object : BackupTaskSchedule 数据库对象
    # immediately_run : 立刻执行，不启动线程（调试使用）
    def __init__(self, schedule_object, reason, config, immediately_run=False):
        super(BackupTaskWorker, self).__init__()
        self._backup_task_object = self._create_backup_task_object(schedule_object, reason)
        self._immediately_run = immediately_run
        self.name = r'[{}]({}) backup task thread'.format(schedule_object.name, self._backup_task_object.id)
        self.log_info('BackupTaskWorker init')
        self._config = config
        self._sleep = Sleep(self._backup_task_object.schedule.id)

    # 创建backup task数据库对象
    @staticmethod
    def _create_backup_task_object(schedule_object, reason):
        backup_task_object = BackupTask.objects.create(reason=reason, schedule=schedule_object)
        HostLog.objects.create(
            host=backup_task_object.schedule.host,
            type=HostLog.LOG_BACKUP_START,
            reason=json.dumps({'backup_task': backup_task_object.id, 'description': '备份任务：{}, 手动启动：{}'.
                              format(schedule_object.name, '是' if reason == BackupTask.REASON_PLAN_MANUAL else '否'),
                               'stage': 'TASK_STEP_IN_PROGRESS_BACKUP_START',
                               'start_type': 'is_hand_{}'.format(
                                   'Y' if reason == BackupTask.REASON_PLAN_MANUAL else 'N'),
                               'task_type': 'backup_task'},
                              ensure_ascii=False))
        return backup_task_object

    def work(self):
        if self._immediately_run:
            self.run()
        else:
            self.start()

    def run(self):
        try:
            self.log_info(r'run start')
            self.run_logic()
            _finish_backup_task_object(self.name, self._backup_task_object.id, True, 'run ok', 'run ok')
            self.log_info(r'run end')
        except xlogging.BoxDashboardException as bde:
            self.log_error(r' run BoxDashboardException : {} | {}'.format(bde.msg, bde.debug))
            will_retry = BackupScheduleRetryHandle.modify(
                BackupTask.objects.get(id=self._backup_task_object.id).schedule,
                (bde.http_status != xlogging.ERROR_HTTP_STATUS_NEED_RETRY) and
                (not boxService.get_retry_schedule_style()),
                BackupTask.objects.get(id=self._backup_task_object.id)
            )
            _finish_backup_task_object(self.name, self._backup_task_object.id, False, bde.msg, bde.debug, partial=True,
                                       will_retry=will_retry)

        except Exception as e:
            self.log_error(r' run Exception : {}'.format(e))
            will_retry = BackupScheduleRetryHandle.modify(
                BackupTask.objects.get(id=self._backup_task_object.id).schedule,
                not boxService.get_retry_schedule_style(),
                BackupTask.objects.get(id=self._backup_task_object.id))
            _finish_backup_task_object(self.name, self._backup_task_object.id, False, '内部异常，代码2342',
                                       'Exception:{}'.format(e), partial=True, will_retry=will_retry)
        else:
            BackupScheduleRetryHandle.clean(
                BackupTask.objects.get(id=self._backup_task_object.id).schedule)

    class UserCancelCheck(object):
        def __init__(self, schedule_object, backup_task_object_id):
            self.schedule_object = schedule_object
            self.backup_task_object_id = backup_task_object_id
            self.backup_task = BackupTask.objects.get(id=self.backup_task_object_id)

        def check(self):
            self.schedule_object.refresh_from_db(fields=['enabled', 'deleted'])
            self.backup_task.refresh_from_db(fields=['ext_config'])
            if not self.schedule_object.enabled:
                xlogging.raise_and_logging_error(
                    r'用户取消，计划“{}”被禁用'.format(self.schedule_object.name),
                    r'UserCancelCheck backup_task_object_id : {} schedule_name : {} disable'.format(
                        self.backup_task_object_id, self.schedule_object.name))
            if self.schedule_object.deleted:
                xlogging.raise_and_logging_error(
                    '用户取消，计划“{}”被删除'.format(self.schedule_object.name),
                    r'UserCancelCheck backup_task_object_id : {} schedule_name : {} delete'.format(
                        self.backup_task_object_id, self.schedule_object.name))

            if '"{}"'.format(xdata.CANCEL_TASK_EXT_KEY) in self.backup_task.ext_config:
                xlogging.raise_and_logging_error(
                    '用户取消，计划“{}”'.format(self.schedule_object.name),
                    r'UserCancelCheck backup_task_object_id : {} schedule_name : {} delete'.format(
                        self.backup_task_object_id, self.schedule_object.name))

    def run_logic(self):
        force_full, disable_optimize = self._is_force_full_and_disable_optimize()
        force_store_full = self._is_force_store_full()

        work = HostBackupWorkProcessors(self.name, self._backup_task_object.schedule.host, force_full,
                                        storage_node_ident=self._backup_task_object.schedule.storage_node_ident,
                                        schedule_object=self._backup_task_object.schedule,
                                        force_store_full=force_store_full,
                                        disable_optimize=disable_optimize)

        self._backup_task_object.set_host_snapshot(work.host_snapshot)

        user_cancel_check = self.UserCancelCheck(self._backup_task_object.schedule, self._backup_task_object.id)
        queue(self._backup_task_object, work.host_snapshot, user_cancel_check)

        work.work()  # 创建HostSnapshot 与 DiskSnapshot 数据库对象，发送备份命令
        # 备份任务开始，其他线程会更新数据库，因此所有数据库对象需要重新获取才能读取到新的数据
        host_ident = self._backup_task_object.host_snapshot.host.ident
        while _check_backup_task_status(host_ident, self._backup_task_object.id, user_cancel_check):  # 等待agent报告被完成
            self._sleep.sleep()
        self.log_info('backup finished, start create compress task')
        create_compress_task_after_task(self._backup_task_object.host_snapshot.disk_snapshots.all())

    def _is_force_full_and_disable_optimize(self):
        if self._backup_task_object.schedule and \
                is_force_full_by_schedule(self._backup_task_object.schedule, self.log_info, 2):
            return True, True  # 多次创建快照失败，需要禁用流量及空间优化
        if self._backup_task_object.reason != BackupTask.REASON_PLAN_MANUAL and self._backup_task_object.schedule and \
                is_force_full_by_schedule(self._backup_task_object.schedule, _logger.info, 1):
            return True, True
        else:
            return is_force_full_by_config(self._config, self.log_info), False

    def _is_force_store_full(self):
        return is_force_store_full_by_config(self._config, self.log_info)


class BackupTaskReWorker(BackupTaskWorker):
    def __init__(self, backup_task_id, immediately_run=False):
        threading.Thread.__init__(self)
        self._backup_task_object = BackupTask.objects.get(id=backup_task_id)
        add_running(self._backup_task_object)
        self._backup_task_object_id = backup_task_id
        self._immediately_run = immediately_run
        self.name = r'[{}]({}) backup task rework thread'.format(
            self._backup_task_object.schedule.name, self._backup_task_object_id)
        self.log_info('BackupTaskReWorker init')
        self._sleep = Sleep(self._backup_task_object.schedule.id)

    def run_logic(self):
        host_snapshot = BackupTask.objects.get(id=self._backup_task_object_id).host_snapshot
        if host_snapshot is None:
            xlogging.raise_and_logging_error('发送备份命令失败', '{} : host_snapshot is None'.format(self.name))

        host_ident = host_snapshot.host.ident
        time.sleep(120)  # 重启服务后，需要等待客户端上线
        user_cancel_check = BackupTaskWorker.UserCancelCheck(
            BackupTask.objects.get(id=self._backup_task_object_id).schedule, self._backup_task_object_id)
        while _check_backup_task_status(host_ident, self._backup_task_object_id, user_cancel_check):
            self._sleep.sleep()
        self.log_info('backup finished, start create compress task')
        create_compress_task_after_task(host_snapshot.disk_snapshots.all())


class FileBackupTaskWorker(threading.Thread, _WorkerLog):
    def __init__(self, schedule_object, reason, config, immediately_run=False):
        super(FileBackupTaskWorker, self).__init__()
        self._check_host_not_running(schedule_object.host.ident)
        self._backup_task_object = self._create_backup_task_object(schedule_object, reason)
        self._immediately_run = immediately_run
        self.name = self._backup_task_object.name
        self.log_info('FileBackupTaskWorker init')
        self._config = config
        self._sleep = Sleep(self._backup_task_object.schedule.id)

    @staticmethod
    def _check_host_not_running(key):
        if 'has_worker' in file_backup_api('poll', key, ''):
            xlogging.raise_and_logging_error('该客户端正在执行其他任务中', 'host snapshot running',
                                             status.HTTP_429_TOO_MANY_REQUESTS)

    # 创建backup task数据库对象
    @staticmethod
    def _create_backup_task_object(schedule_object, reason):
        backup_task_object = FileBackupTask.objects.create(reason=reason,
                                                           schedule=schedule_object,
                                                           task_uuid=uuid.uuid4().hex,
                                                           start_datetime=timezone.now())
        HostLog.objects.create(
            host=backup_task_object.schedule.host,
            type=HostLog.LOG_BACKUP_START,
            reason=json.dumps({'file_backup_task': backup_task_object.id, 'description': '备份任务：{}, 手动启动：{}'.
                              format(schedule_object.name, '是' if reason == FileBackupTask.REASON_PLAN_MANUAL else '否'),
                               'stage': 'TASK_STEP_IN_PROGRESS_BACKUP_START',
                               'start_type': 'is_hand_{}'.format(
                                   'Y' if reason == FileBackupTask.REASON_PLAN_MANUAL else 'N'),
                               'task_type': 'file_backup_task'},
                              ensure_ascii=False))
        return backup_task_object

    def work(self):
        if self._immediately_run:
            self.run()
        else:
            self.start()

    def run(self):
        try:
            self.log_info(r'run start')
            self.run_logic()
            _finish_file_backup_task_object(self.name, self._backup_task_object.id, True, 'run ok', 'run ok')
            self.log_info(r'run end')
        except xlogging.BoxDashboardException as bde:
            self.log_error(r' run BoxDashboardException : {} | {}'.format(bde.msg, bde.debug))
            _finish_file_backup_task_object(self.name, self._backup_task_object.id, False, bde.msg, bde.debug, True)
            BackupScheduleRetryHandle.modify(
                FileBackupTask.objects.get(id=self._backup_task_object.id).schedule,
                (bde.http_status != xlogging.ERROR_HTTP_STATUS_NEED_RETRY) and
                (not boxService.get_retry_schedule_style()),
                FileBackupTask.objects.get(id=self._backup_task_object.id)
            )
        except Exception as e:
            self.log_error(r' run Exception : {}'.format(e))
            _finish_file_backup_task_object(self.name, self._backup_task_object.id, False, '内部异常，代码2342',
                                            'Exception:{}'.format(e), True)
            BackupScheduleRetryHandle.modify(
                FileBackupTask.objects.get(id=self._backup_task_object.id).schedule,
                not boxService.get_retry_schedule_style())
        else:
            BackupScheduleRetryHandle.clean(
                FileBackupTask.objects.get(id=self._backup_task_object.id).schedule)

    class UserCancelCheck(object):
        def __init__(self, schedule_object, backup_task_object_id):
            self.schedule_object = schedule_object
            self.backup_task_object_id = backup_task_object_id
            self.backup_task = FileBackupTask.objects.get(id=self.backup_task_object_id)

        def _is_nas_transfer_started(self):
            if not isinstance(self.backup_task, FileBackupTask):
                return False
            nas_task = FileBackupTask.objects.get(id=self.backup_task.id)
            ext_config = json.loads(nas_task.ext_config)
            rsync_status = ext_config.get('rsync_status', {})
            if not rsync_status:
                return False

            sync_files = rsync_status['current_sync_files']
            return int(sync_files) > 0

        def _notify_nas_end_transfer_data(self, reason):
            plan_name = self.schedule_object.name
            _logger.warning('_notify_nas_end_transfer_data, because plan({}) {}'.format(plan_name, reason))
            file_backup_api('end_transfer_data', self.schedule_object.host.ident, '')

        def _deal_nas_backup_when_necessary(self, reason):
            """
            :return:
                True    发现NAS正在同步数据，且已通知其结束
                False   不需要处理NAS
            """
            if not self._is_nas_transfer_started():
                return False

            self._notify_nas_end_transfer_data(reason)
            return True

        def check(self):
            self.schedule_object.refresh_from_db(fields=['enabled', 'deleted'])
            self.backup_task.refresh_from_db(fields=['ext_config'])
            if not self.schedule_object.enabled:
                if self._deal_nas_backup_when_necessary('disable'):
                    return
                xlogging.raise_and_logging_error(
                    r'用户取消，计划“{}”被禁用'.format(self.schedule_object.name),
                    r'UserCancelCheck backup_task_object_id : {} schedule_name : {} disable'.format(
                        self.backup_task_object_id, self.schedule_object.name))
            if self.schedule_object.deleted:
                if self._deal_nas_backup_when_necessary('deleted'):
                    return
                xlogging.raise_and_logging_error(
                    '用户取消，计划“{}”被删除'.format(self.schedule_object.name),
                    r'UserCancelCheck backup_task_object_id : {} schedule_name : {} delete'.format(
                        self.backup_task_object_id, self.schedule_object.name))

            if '"{}"'.format(xdata.CANCEL_TASK_EXT_KEY) in self.backup_task.ext_config:
                if self._deal_nas_backup_when_necessary('canceled'):
                    return
                xlogging.raise_and_logging_error(
                    '用户取消，计划“{}”'.format(self.schedule_object.name),
                    r'UserCancelCheck backup_task_object_id : {} schedule_name : {} delete'.format(
                        self.backup_task_object_id, self.schedule_object.name))

    def _set_nas_dynamic_params(self):
        schedule = BackupTaskSchedule.objects.get(id=self._backup_task_object.schedule.id)
        ext_config = json.loads(schedule.ext_config)
        cur_params = {
            'sync_workers': ext_config.get('sync_threads', 4),
            'enum_workers': ext_config.get('enum_threads', 2),
            'net_limit': ext_config.get('net_limit', -1),
        }
        last_params = self.last_params if hasattr(self, 'last_params') else {}
        if last_params and all([
            cur_params['sync_workers'] == last_params['sync_workers'],
            cur_params['enum_workers'] == last_params['enum_workers'],
            cur_params['net_limit'] == last_params['net_limit'],
        ]):
            return
        res = file_backup_api('set_nas_dynamic_params', schedule.host.ident, cur_params)
        if res == 'ok':
            self.last_params = cur_params
        _logger.warning('_set_nas_dynamic_params: {}, {}'.format(cur_params, res))

    def run_logic(self):
        force_full, disable_optimize = self._is_force_full_and_disable_optimize()
        force_store_full = self._is_force_store_full()

        work = FileBackupWorkProcessors(self.name, self._backup_task_object.schedule.host, self._backup_task_object,
                                        force_full, force_store_full=force_store_full,
                                        storage_node_ident=self._backup_task_object.schedule.storage_node_ident,
                                        schedule_object=self._backup_task_object.schedule,

                                        disable_optimize=disable_optimize)

        self._backup_task_object.set_host_snapshot(work.host_snapshot)

        user_cancel_check = self.UserCancelCheck(self._backup_task_object.schedule, self._backup_task_object.id)
        queue(self._backup_task_object, work.host_snapshot, user_cancel_check)
        work.work()  # 创建HostSnapshot 与 DiskSnapshot 数据库对象，发送备份命令
        # 备份任务开始，其他线程会更新数据库，因此所有数据库对象需要重新获取才能读取到新的数据
        time.sleep(5)  # 异步启动线程， 需等待
        self._backup_task_object.set_status(FileBackupTask.INITIALIZE_THE_BACKUP_AGENT)
        host_ident = self._backup_task_object.host_snapshot.host.ident
        while _check_file_backup_task_status(host_ident, self._backup_task_object.id,
                                             user_cancel_check):  # 等待agent报告被完成
            self._sleep.sleep(30)
            self._set_nas_dynamic_params()
        self.log_info('backup finished, start create compress task')
        create_compress_task_after_task(self._backup_task_object.host_snapshot.disk_snapshots.all())

    def _is_force_full_and_disable_optimize(self):
        if self._backup_task_object.schedule and \
                is_force_full_by_schedule(self._backup_task_object.schedule, self.log_info, 2):
            return True, True  # 多次创建快照失败，需要禁用流量及空间优化
        if self._backup_task_object.reason != FileBackupTask.REASON_PLAN_MANUAL and self._backup_task_object.schedule and \
                is_force_full_by_schedule(self._backup_task_object.schedule, _logger.info, 1):
            return True, True
        else:
            return is_force_full_by_config(self._config, self.log_info), False

    def _is_force_store_full(self):
        return is_force_store_full_by_config(self._config, self.log_info)


class FileBackupTaskReWorker(threading.Thread, _WorkerLog):
    def __init__(self, backup_task_id, immediately_run=False):
        super(FileBackupTaskReWorker, self).__init__()
        self._backup_task_object = FileBackupTask.objects.get(id=backup_task_id)
        add_running(self._backup_task_object)
        self._backup_task_object_id = backup_task_id
        self._immediately_run = immediately_run
        self.name = self._backup_task_object.name
        self.log_info('FileBackupTaskReWorker init')
        self._sleep = Sleep(self._backup_task_object.schedule.id)

    def work(self):
        if self._immediately_run:
            self.run()
        else:
            self.start()

    def run(self):
        try:
            self.log_info(r'run start')
            self.run_logic()
            _finish_file_backup_task_object(self.name, self._backup_task_object_id, True, 'run ok', 'run ok')
            self.log_info(r'run end')
        except xlogging.BoxDashboardException as bde:
            self.log_error(r' run BoxDashboardException : {} | {}'.format(bde.msg, bde.debug))
            _finish_file_backup_task_object(self.name, self._backup_task_object_id, False, bde.msg, bde.debug)
            BackupScheduleRetryHandle.modify(
                FileBackupTask.objects.get(id=self._backup_task_object_id).schedule,
                (bde.http_status != xlogging.ERROR_HTTP_STATUS_NEED_RETRY) and
                (not boxService.get_retry_schedule_style(),
                 self._backup_task_object)
            )
        except Exception as e:
            self.log_error(r' run Exception : {}'.format(e))
            _finish_file_backup_task_object(self.name, self._backup_task_object_id, False, '内部异常，代码2338',
                                            'Exception:{}'.format(e))
            BackupScheduleRetryHandle.modify(
                FileBackupTask.objects.get(id=self._backup_task_object_id).schedule,
                not boxService.get_retry_schedule_style())
        else:
            BackupScheduleRetryHandle.clean(
                FileBackupTask.objects.get(id=self._backup_task_object_id).schedule)

    def run_logic(self):
        host_snapshot = FileBackupTask.objects.get(id=self._backup_task_object_id).host_snapshot
        if host_snapshot is None:
            xlogging.raise_and_logging_error('发送备份命令失败', '{} : host_snapshot is None'.format(self.name))

        host_ident = host_snapshot.host.ident
        user_cancel_check = FileBackupTaskWorker.UserCancelCheck(
            FileBackupTask.objects.get(id=self._backup_task_object_id).schedule, self._backup_task_object_id)
        time.sleep(5)  # 异步启动线程， 需等待
        while _check_file_backup_task_status(host_ident, self._backup_task_object_id, user_cancel_check):
            self._sleep.sleep()
        self.log_info('backup finished, start create compress task')
        create_compress_task_after_task(host_snapshot.disk_snapshots.all())


class CDPTaskReWorker(threading.Thread, _WorkerLog):
    def __init__(self, cdp_task_object_id, immediately_run=False):
        super(CDPTaskReWorker, self).__init__()
        self._cdp_task_object_id = cdp_task_object_id
        self._cdp_task_object = CDPTask.objects.get(id=cdp_task_object_id)
        add_running(self._cdp_task_object)
        self._immediately_run = immediately_run
        self._schedule_object = self._cdp_task_object.schedule
        if self._schedule_object is not None:
            self.name = r'[{}]({}) cdp task rework thread'.format(self._schedule_object.name, self._cdp_task_object_id)
        else:
            self.name = r'skip cdp task id : {}'.format(self._cdp_task_object_id)
        self.log_info('CDPTaskReWorker init')
        self._normal_backup_successful = False
        self._sleep = Sleep(self._cdp_task_object.schedule.id)

    def work(self):
        if self._schedule_object is None:
            return

        if self._immediately_run:
            self.run()
        else:
            self.start()

    def run(self):
        try:
            self.log_info(r'run start')
            self.run_logic()
            self.log_info(r'run end')
        except xlogging.BoxDashboardException as bde:
            self.log_error(r' run BoxDashboardException : {} | {}'.format(bde.msg, bde.debug))
            finish_cdp_task_object(self.name, self._cdp_task_object_id, self._normal_backup_successful, bde.msg,
                                   bde.debug, False, bde.http_status != xlogging.ERROR_HTTP_STATUS_NEED_RETRY)
        except Exception as e:
            self.log_error(r' run Exception : {}'.format(e))
            finish_cdp_task_object(self.name, self._cdp_task_object_id, self._normal_backup_successful,
                                   '内部异常，代码2311', 'Exception:{}'.format(e), False)

    def run_logic(self):
        cdp_task_object = CDPTask.objects.get(id=self._cdp_task_object_id)
        host_snapshot = cdp_task_object.host_snapshot
        if host_snapshot is None:
            xlogging.raise_and_logging_error('发送开始CDP命令失败', '{} : host_snapshot is None'.format(self.name))

        for token_object in cdp_task_object.tokens.all():
            try:
                boxService.box_service.updateToken(
                    KTService.Token(token=token_object.token, snapshot=[], expiryMinutes=0))
            except Exception as e:
                _logger.warning(
                    'CDPTaskReWorker call boxService.updateToken {} failed. {}'.format(token_object.token, e))

        host_ident = host_snapshot.host.ident
        time.sleep(120)  # 重启服务后，需要等待客户端上线
        user_cancel_check = CDPTaskWorker.UserCancelCheck(
            Tokens.get_schedule_obj_from_cdp_task(self._cdp_task_object), self._cdp_task_object_id)
        while TaskHelper.check_backup_status_in_cdp_task(host_ident, self._cdp_task_object_id, user_cancel_check):
            update_cdp_host_snapshot_last_datetime(self._cdp_task_object_id)
            self._sleep.sleep()
        # 关联的普通备份完成
        HostSnapshotFinishHelper.stopBackupOptimize(host_snapshot)
        DiskSnapshotHash.reorganize_hash_file(host_snapshot)
        self._normal_backup_successful = True
        # 为disk_snapshot 创建压缩任务
        self.log_info('backup finished, start create compress task')
        create_compress_task_after_task(host_snapshot.disk_snapshots.all())

        is_check_cdp_task_status_first = True
        while _check_cdp_task_status(host_ident, self._cdp_task_object_id, is_check_cdp_task_status_first):
            is_check_cdp_task_status_first = False
            update_cdp_host_snapshot_last_datetime(self._cdp_task_object_id)
            self._sleep.sleep()

        self.log_info(r'cdp schedule stopped')
        finish_cdp_task_object(self.name, self._cdp_task_object_id, True, 'run_logic', 'run_logic', True)


class CDPTaskWorker(threading.Thread, _WorkerLog):
    def __init__(self, schedule_object, config, immediately_run=False):
        super(CDPTaskWorker, self).__init__()
        self._cdp_task_object = self._create_cdp_task_object(schedule_object)
        self._immediately_run = immediately_run
        self.name = r'[{}]({}) cdp task thread'.format(schedule_object.name, self._cdp_task_object.id)
        self.log_info('CDPTaskWorker init')
        self._normal_backup_successful = False
        self._config = config
        self._sleep = Sleep(self._cdp_task_object.schedule.id)

    # 创建cdp task数据库对象
    @staticmethod
    def _create_cdp_task_object(schedule_object):
        cdp_task_object = CDPTask.objects.create(schedule=schedule_object)
        HostLog.objects.create(host=cdp_task_object.schedule.host, type=HostLog.LOG_CDP_START,
                               reason=json.dumps({'cdp_task': cdp_task_object.id, 'description': 'CDP保护开始',
                                                  'task_type': 'cdp_task', 'stage': 'TASK_STEP_IN_PROGRESS_CDP_START'},
                                                 ensure_ascii=False))
        return cdp_task_object

    def work(self):
        if self._immediately_run:
            self.run()
        else:
            self.start()

    def run(self):
        try:
            self.log_info(r'run start')
            self.run_logic()
            self.log_info(r'run end')
        except xlogging.BoxDashboardException as bde:
            self.log_error(r' run BoxDashboardException : {} | {}'.format(bde.msg, bde.debug))
            finish_cdp_task_object(self.name, self._cdp_task_object.id, self._normal_backup_successful, bde.msg,
                                   bde.debug, False, True, bde.http_status != xlogging.ERROR_HTTP_STATUS_NEED_RETRY)
        except Exception as e:
            self.log_error(r' run Exception : {}'.format(e))
            finish_cdp_task_object(self.name, self._cdp_task_object.id, self._normal_backup_successful,
                                   '内部异常，代码2313', 'Exception:{}'.format(e), False, True)

    class UserCancelCheck(object):
        def __init__(self, schedule_object, cdp_task_id, check_func=None):
            self.schedule_object = schedule_object
            self.cdp_task_id = cdp_task_id

            self.check_func = check_func

        def check(self):
            self.schedule_object.refresh_from_db(fields=['enabled', 'deleted'])
            if not self.schedule_object.enabled:
                xlogging.raise_and_logging_error(
                    r'用户取消，计划“{}”被禁用'.format(self.schedule_object.name),
                    r'UserCancelCheck cdp_task_object_id : {} schedule_name : {} disable'.format(
                        self.cdp_task_id, self.schedule_object.name))
            if self.schedule_object.deleted:
                xlogging.raise_and_logging_error(
                    '用户取消，计划“{}”被删除'.format(self.schedule_object.name),
                    r'UserCancelCheck cdp_task_object_id : {} schedule_name : {} delete'.format(
                        self.cdp_task_id, self.schedule_object.name))

            if callable(self.check_func):
                self.check_func()

    def run_logic(self):
        force_full, disable_optimize = self._is_force_full_and_disable_optimize()
        force_store_full = self._is_force_store_full()

        cdp_mode_type = json.loads(self._cdp_task_object.schedule.ext_config)['cdpSynchAsynch']
        work = HostBackupWorkProcessors(self.name, self._cdp_task_object.schedule.host, force_full, True, cdp_mode_type,
                                        self._cdp_task_object.id, self._cdp_task_object.schedule.storage_node_ident,
                                        self._cdp_task_object.schedule, force_store_full=force_store_full,
                                        disable_optimize=disable_optimize)

        self._cdp_task_object.set_host_snapshot(work.host_snapshot)

        user_cancel_check = self.UserCancelCheck(
            Tokens.get_schedule_obj_from_cdp_task(self._cdp_task_object), self._cdp_task_object.id)
        queue(self._cdp_task_object, work.host_snapshot, user_cancel_check)

        work.work()  # 创建HostSnapshot 与 DiskSnapshot 数据库对象，发送备份命令
        # 备份任务开始，其他线程会更新数据库，因此所有数据库对象需要重新获取才能读取到新的数据
        host_ident = self._cdp_task_object.host_snapshot.host.ident
        while TaskHelper.check_backup_status_in_cdp_task(host_ident, self._cdp_task_object.id, user_cancel_check):
            update_cdp_host_snapshot_last_datetime(self._cdp_task_object.id)
            self._sleep.sleep()
        # 关联的普通备份完成
        HostSnapshotFinishHelper.stopBackupOptimize(self._cdp_task_object.host_snapshot)
        DiskSnapshotHash.reorganize_hash_file(self._cdp_task_object.host_snapshot)
        HostLog.objects.create(host=self._cdp_task_object.schedule.host, type=HostLog.LOG_CDP_BASE_FINISHED,
                               reason=json.dumps({'cdp_task': self._cdp_task_object.id, 'description': '客户端CDP基础备份已完成',
                                                  'task_type': 'cdp_task',
                                                  'stage': 'TASK_STEP_IN_PROGRESS_CDP_SUCCESS'},
                                                 ensure_ascii=False))
        self._normal_backup_successful = True
        # 为disk_snapshot 创建压缩任务
        self.log_info('backup finished, start create compress task')
        create_compress_task_after_task(self._cdp_task_object.host_snapshot.disk_snapshots.all())

        is_check_cdp_task_status_first = True
        while _check_cdp_task_status(host_ident, self._cdp_task_object.id, is_check_cdp_task_status_first):
            is_check_cdp_task_status_first = False
            update_cdp_host_snapshot_last_datetime(self._cdp_task_object.id)
            self._sleep.sleep()

        self.log_info(r'cdp schedule stopped')
        finish_cdp_task_object(self.name, self._cdp_task_object.id, True, 'run_logic', 'run_logic', True)

    def _is_force_full_and_disable_optimize(self):
        if self._cdp_task_object.schedule and is_force_full_by_schedule(self._cdp_task_object.schedule, self.log_info):
            return True, True  # 多次创建快照失败，需要禁用流量及空间优化
        else:
            return is_force_full_by_config(self._config, self.log_info), False

    def _is_force_store_full(self):
        return is_force_store_full_by_config(self._config, self.log_info)


class RestoreVolumeTaskWorker(threading.Thread, _WorkerLog):
    def __init__(self, agent_restore, host_snapshot_object):
        super(RestoreVolumeTaskWorker, self).__init__()
        self._agent_restore = agent_restore
        self._restore_task_object = self._create_restore_task_object(
            agent_restore.restore_target_object, host_snapshot_object, agent_restore.host_object,
            agent_restore.volumes_display, agent_restore.host_object)
        self.name = r'[{}]({}) volume restore task thread'.format(
            self._agent_restore.host_object.ident, self._restore_task_object.id)
        self.log_info('VolumeRestoreTaskWorker init.')

    @staticmethod
    def _create_restore_task_object(restore_target_object, host_snapshot_object, host_object, volumes_display,
                                    target_host_object):
        restore_task_object = RestoreTask.objects.create(type=RestoreTask.TYPE_VOLUME,
                                                         restore_target=restore_target_object,
                                                         host_snapshot=host_snapshot_object,
                                                         target_host=target_host_object)
        plan_obj = host_snapshot_object.schedule

        info = json.loads(restore_target_object.info)
        restore_time = info['restore_time']

        if is_restore_target_belong_htb(restore_target_object):
            log_type = HostLog.LOG_HTB
            desc = r'开始热备快照点“{plan_name}：{restore_time}”中的卷[{volumes}]到“{host_name}”'.format(
                plan_name=plan_obj.name if plan_obj else host_snapshot_object.host.display_name,
                restore_time=restore_time,
                host_name=host_object.name,
                volumes='，'.join(volumes_display)
            )
        else:
            log_type = HostLog.LOG_RESTORE_START
            desc = r'开始还原快照点“{plan_name}：{restore_time}”中的卷[{volumes}]到“{host_name}”'.format(
                plan_name=plan_obj.name if plan_obj else host_snapshot_object.host.display_name,
                restore_time=restore_time,
                host_name=host_object.name,
                volumes='，'.join(volumes_display)
            )
        HostLog.objects.create(
            host=host_snapshot_object.host, type=log_type,
            reason=json.dumps({'restore_task': restore_task_object.id, 'description': desc,
                               'task_type': 'restore_task', 'stage': 'TASK_STEP_IN_PROGRESS_RESTORE_START'},
                              ensure_ascii=False)
        )
        return restore_task_object

    def work(self, immediately_run):
        if immediately_run:
            self.run()
        else:
            self.start()

    def run(self):
        try:
            self.log_info(r'run start')
            self.run_logic()
            self.log_info(r'run end')
        except xlogging.BoxDashboardException as bde:
            self.log_error(r' run BoxDashboardException : {} | {}'.format(bde.msg, bde.debug))
            finish_restore_task_object(self.name, self._restore_task_object.id, False, bde.msg, bde.debug)
        except Exception as e:
            self.log_error(r' run Exception : {}'.format(e))
            finish_restore_task_object(self.name, self._restore_task_object.id, False, '内部异常，代码2384',
                                       'Exception:{}'.format(e))

    def run_logic(self):
        self._agent_restore.work('volume_restore_{}'.format(self._restore_task_object.id))


class RestoreTaskWorker(threading.Thread, _WorkerLog):
    # restore_target_object : RestoreTarget 数据库对象
    def __init__(self, restore_target_object, host_snapshot_object, host_object):
        super(RestoreTaskWorker, self).__init__()
        self._restore_task_object = self._create_restore_task_object(restore_target_object, host_snapshot_object,
                                                                     host_object)
        self.name = r'[{}]({}) restore task thread'.format(restore_target_object.ident, self._restore_task_object.id)
        self.log_info('RestoreTaskWorker init')

    @staticmethod
    def _create_restore_task_object(restore_target_object, host_snapshot_object, host_object):
        if host_object is None:
            restore_task_object = RestoreTask.objects.create(type=RestoreTask.TYPE_PE,
                                                             restore_target=restore_target_object,
                                                             host_snapshot=host_snapshot_object)
        else:
            restore_task_object = RestoreTask.objects.create(type=RestoreTask.TYPE_HOST,
                                                             restore_target=restore_target_object,
                                                             host_snapshot=host_snapshot_object,
                                                             target_host=host_object)
        plan_obj = host_snapshot_object.schedule
        info = json.loads(restore_target_object.info)
        pe_ip = info['remote_ip']
        restore_time = info['restore_time']

        if is_restore_target_belong_htb(restore_task_object.restore_target):
            log_type = HostLog.LOG_HTB
            desc = r'开始初始化系统"{plan_name}:{restore_time}"到"{pe_ip}"'.format(
                plan_name=plan_obj.name if plan_obj else host_snapshot_object.host.display_name,
                restore_time=restore_time,
                pe_ip=pe_ip
            )
        else:
            log_type = HostLog.LOG_RESTORE_START
            desc = r'开始还原备份点"{plan_name}:{restore_time}"到"{pe_ip}"'.format(
                plan_name=plan_obj.name if plan_obj else host_snapshot_object.host.display_name,
                restore_time=restore_time,
                pe_ip=pe_ip
            )
        HostLog.objects.create(
            host=host_snapshot_object.host, type=log_type,
            reason=json.dumps({'restore_task': restore_task_object.id, 'description': desc, 'task_type': 'restore_task',
                               'stage': 'TASK_STEP_IN_PROGRESS_RESTORE_START'}, ensure_ascii=False)
        )
        return restore_task_object

    def work(self, immediately_run):
        if immediately_run:
            self.run()
        else:
            self.start()

    def run(self):
        try:
            self.log_info(r'run start')
            self.run_logic()
            self.log_info(r'run end')
        except xlogging.BoxDashboardException as bde:
            self.log_error(r' run BoxDashboardException : {} | {}'.format(bde.msg, bde.debug))
            finish_restore_task_object(self.name, self._restore_task_object.id, False, bde.msg, bde.debug)
        except Exception as e:
            self.log_error(r' run Exception : {}'.format(e))
            finish_restore_task_object(self.name, self._restore_task_object.id, False, '内部异常，代码2347',
                                       'Exception:{}'.format(e))

    def run_logic(self):
        pe_restore = PeRestore(self._restore_task_object.restore_target)
        pe_restore.work('restore_{}'.format(self._restore_task_object.id))


class MigrateTaskWithNormalReWorker(threading.Thread, _WorkerLog):
    def __init__(self, migrate_task_id, immediately_run=False):
        super(MigrateTaskWithNormalReWorker, self).__init__()
        self._migrate_task_object_id = migrate_task_id
        self._immediately_run = immediately_run
        self._migrate_task_object = MigrateTask.objects.get(id=self._migrate_task_object_id)
        add_running(self._migrate_task_object)
        self.name = r'[{}]->[{}]({}) migrate task with normal rework thread' \
            .format(self._migrate_task_object.source_host.ident, self._migrate_task_object.restore_target.ident,
                    self._migrate_task_object_id)
        self.log_info('MigrateTaskWithNormalReWorker init')

    def work(self):
        if self._immediately_run:
            self.run()
        else:
            self.start()

    def run(self):
        try:
            self.log_info(r'run start')
            self.run_logic()
            self.log_info(r'run end')
        except xlogging.BoxDashboardException as bde:
            self.log_error(r' run BoxDashboardException : {} | {}'.format(bde.msg, bde.debug))
            finish_migrate_task_object(self.name, self._migrate_task_object_id, False, bde.msg, bde.debug)
        except Exception as e:
            self.log_error(r' run Exception : {}'.format(e))
            finish_migrate_task_object(self.name, self._migrate_task_object_id, False, '内部异常，代码2336',
                                       'Exception:{}'.format(e))

    def run_logic(self):
        host_snapshot = self._migrate_task_object.host_snapshot
        if host_snapshot is None:
            xlogging.raise_and_logging_error('发送迁移命令到源客户端失败', '{} : host_snapshot is None'.format(self.name))

        ext_config = json.loads(self._migrate_task_object.ext_config)
        task_status = ext_config.get('task_status', None)
        if task_status is None:
            xlogging.raise_and_logging_error('发送迁移命令到目标客户端失败', '{} : host_snapshot is None'.format(self.name))

        if task_status == 'wait_restore':
            return
        elif task_status == 'wait_backup':
            host_ident = self._migrate_task_object.source_host.ident
            time.sleep(120)
            while _check_host_snapshot_status(host_ident, self._migrate_task_object.host_snapshot_id):
                time.sleep(120)

            host_snapshot_object = HostSnapshot.objects.get(id=self._migrate_task_object.host_snapshot_id)
            HostSnapshotFinishHelper.stopBackupOptimize(host_snapshot_object)
            DiskSnapshotHash.reorganize_hash_file(host_snapshot_object)

            if self._migrate_task_object.source_type == MigrateTask.SOURCE_TYPE_TEMP_NORMAL:
                SpaceCollectionWorker. \
                    set_host_snapshot_deleting_and_collection_space_later(host_snapshot_object)

            ext_config['task_status'] = 'wait_restore'
            self._migrate_task_object.ext_config = json.dumps(ext_config, ensure_ascii=False)
            self._migrate_task_object.save(update_fields=['ext_config'])
        else:
            xlogging.raise_and_logging_error('内部异常，代码2341',
                                             '{} : invalid task_status : {}'.format(self.name, task_status))


class MigrateTaskWithNormalWorker(threading.Thread, _WorkerLog):
    # restore_target_object : RestoreTarget 数据库对象
    # data : HostSessionMigrateSerializer 反序列化的字典对象
    def __init__(self, source_host_object, restore_target_object, target_host_object, data):
        super(MigrateTaskWithNormalWorker, self).__init__()
        self._migrate_task_object = self._create_migrate_task_object(source_host_object, restore_target_object,
                                                                     target_host_object, data)
        self.name = r'[{}]->[{}]({}) migrate task with normal thread'.format(source_host_object.ident,
                                                                             restore_target_object.ident,
                                                                             self._migrate_task_object.id)
        self.log_info('MigrateTaskWithNormalWorker init')

    @staticmethod
    def _create_migrate_task_object(source_host_object, restore_target_object, target_host_object, data):
        if target_host_object is None:
            task_object = MigrateTask.objects.create(source_host=source_host_object,
                                                     source_type=MigrateTask.SOURCE_TYPE_TEMP_NORMAL,
                                                     destination_type=MigrateTask.DESTINATION_TYPE_PE,
                                                     restore_target=restore_target_object,
                                                     ext_config=json.dumps(data, ensure_ascii=False))
        else:
            task_object = MigrateTask.objects.create(source_host=source_host_object,
                                                     source_type=MigrateTask.SOURCE_TYPE_TEMP_NORMAL,
                                                     destination_type=MigrateTask.DESTINATION_TYPE_HOST,
                                                     destination_host=target_host_object,
                                                     restore_target=restore_target_object,
                                                     ext_config=json.dumps(data, ensure_ascii=False))

        desc = r'开始迁移"{host_ip}"到"{pe_ip}"'.format(
            host_ip=source_host_object.last_ip,
            pe_ip=json.loads(restore_target_object.info)['remote_ip']
        )
        HostLog.objects.create(
            host=source_host_object, type=HostLog.LOG_MIGRATE_START,
            reason=json.dumps({'migrate_task': task_object.id, 'description': desc, 'task_type': 'migrate_task'},
                              ensure_ascii=False)
        )
        return task_object

    def work(self, immediately_run):
        if immediately_run:
            self.run()
        else:
            self.start()

    def run(self):
        try:
            self.log_info(r'run start')
            self.run_logic()
            self.log_info(r'run end')
        except xlogging.BoxDashboardException as bde:
            self.log_error(r' run BoxDashboardException : {} | {}'.format(bde.msg, bde.debug))
            finish_migrate_task_object(self.name, self._migrate_task_object.id, False, bde.msg, bde.debug, True)
        except Exception as e:
            self.log_error(r' run Exception : {}'.format(e))
            finish_migrate_task_object(self.name, self._migrate_task_object.id, False, '内部异常，代码2348',
                                       'Exception:{}'.format(e), True)

    def _is_cancel_task(self):
        task_object = MigrateTask.objects.get(id=self._migrate_task_object.id)
        ext_info = json.loads(task_object.ext_config)
        if xdata.CANCEL_TASK_EXT_KEY in ext_info:
            HostLog.objects.create(host=task_object.host_snapshot.host, type=HostLog.LOG_MIGRATE_FAILED,
                                   reason=json.dumps({'description': r'用户取消任务', 'task_type': 'migrate_task',
                                                      'stage': 'OPERATE_TASK_STATUS_DESELECT'}))
            raise Exception(r'用户取消任务')

    def _wait_for_backup_data_transfer(self, host_snapshot_id):
        while True:
            self._is_cancel_task()
            if self.is_backup_data_transfer(host_snapshot_id, '迁移源备份失败', 'migrate source backup failed'):
                self.log_info(r'is_backup_data_transfer')
                break
            else:
                time.sleep(3)

    @staticmethod
    def is_backup_data_transfer(host_snapshot_id, failed_msg, failed_debug, wait_more_data=5):
        host_snapshot = HostSnapshot.objects.get(id=host_snapshot_id)
        ext_info = json.loads(host_snapshot.ext_info)
        index = ext_info.get('progressIndex', None)
        total = ext_info.get('progressTotal', None)
        if host_snapshot.finish_datetime and host_snapshot.successful is True and host_snapshot.partial is False:
            # 备份完成且成功
            _logger.info(r'backup data transfer beginning : {}'.format(host_snapshot_id))
            return True
        elif host_snapshot.finish_datetime and (host_snapshot.successful is False or host_snapshot.partial is True):
            # 备份完成且失败
            xlogging.raise_and_logging_error(failed_msg, '{}; hostsnapshot={}'.format(failed_debug, host_snapshot_id))
            return False  # never happen
        elif index and total and index > 0 and total > 0 and (  # index is 64K block count
                ((index / total) * 100 > 5.0) or (index * 64 // (1024 ** 2) >= wait_more_data)):
            # 备份开始传输数据
            _logger.info(r'backup data transfer beginning : {}'.format(host_snapshot_id))
            return True
        else:
            _logger.info(r'waiting for backup data transfer {} {} {}'.format(host_snapshot_id, index, total))
            return False

    def run_logic(self):
        self.log_info(r'begin send backup cmd')  # 先发送备份命令
        work = HostBackupWorkProcessors(self.name, self._migrate_task_object.source_host,
                                        migrate_task=self._migrate_task_object)

        self._migrate_task_object.set_host_snapshot(work.host_snapshot)

        queue(self._migrate_task_object, work.host_snapshot)

        backup_parameters = work.work()  # 创建 HostSnapshot 与 DiskSnapshot 数据库对象，发送备份命令
        self._wait_for_backup_data_transfer(work.host_snapshot.id)

        self.log_info(r'begin send restore cmd')  # 备份命令发送成功，开始发送还原命令
        pe_restore = PeRestore(self._migrate_task_object.restore_target)

        src_disks = self._get_src_disks(backup_parameters)
        ext_config = json.loads(self._migrate_task_object.ext_config)
        disks = self._get_disks(src_disks, ext_config['disks'])
        data = {'type': xdata.SNAPSHOT_TYPE_NORMAL, 'disks': disks, 'adapters': ext_config['adapters'],
                'host_snapshot_id': work.host_snapshot.id, 'drivers_ids': ext_config['drivers_ids'],
                'agent_user_info': ext_config['agent_user_info'], 'routers': ext_config['routers'],
                'disable_fast_boot': ext_config['disable_fast_boot'], 'restore_time': timezone.now(),
                'remote_kvm_params': ext_config['remote_kvm_params'],
                'replace_efi': ext_config.get('replace_efi', False)}
        pe_restore.init(data)
        pe_restore.work('migrate_{}'.format(self._migrate_task_object.id))

        ext_config = json.loads(MigrateTask.objects.get(id=self._migrate_task_object.id).ext_config)  # 读取字段，再更新
        ext_config['task_status'] = 'wait_backup'
        self._migrate_task_object.ext_config = json.dumps(ext_config, ensure_ascii=False)
        self._migrate_task_object.save(update_fields=['ext_config'])

        # 还原命令发送完毕，开始监控备份是否完成。其他线程会更新数据库，因此所有数据库对象需要重新获取才能读取到新的数据
        host_ident = self._migrate_task_object.source_host.ident
        while _check_host_snapshot_status(host_ident, self._migrate_task_object.host_snapshot_id):
            time.sleep(120)

        host_snapshot_object = HostSnapshot.objects.get(id=self._migrate_task_object.host_snapshot_id)
        HostSnapshotFinishHelper.stopBackupOptimize(host_snapshot_object)
        DiskSnapshotHash.reorganize_hash_file(host_snapshot_object)

        if self._migrate_task_object.source_type == MigrateTask.SOURCE_TYPE_TEMP_NORMAL:
            SpaceCollectionWorker. \
                set_host_snapshot_deleting_and_collection_space_later(host_snapshot_object)

        ext_config['task_status'] = 'wait_restore'
        self._migrate_task_object.ext_config = json.dumps(ext_config, ensure_ascii=False)
        self._migrate_task_object.save(update_fields=['ext_config'])

    @staticmethod
    # backup_parameters : Box.BackupFiles
    def _get_src_disks(backup_parameters):
        result = dict()
        for backup_parameter in backup_parameters:
            result[backup_parameter.diskIndex] = backup_parameter.snapshot.snapshot
        return result

    # disks : HostSessionMigrateDiskSerializer 反序列化的字典对象数组
    def _get_disks(self, src_disks, disks):
        result = list()
        self.log_info(r'_get_disks src_disks:{} disks:{}'.format(src_disks, disks))
        for disk in disks:
            i = int(disk['src'])
            if i not in src_disks:
                xlogging.raise_and_logging_error(r'原始磁盘({})未启动数据采集'.format(i),
                                                 r'can NOT find disk_snapshot_ident')
            result.append({'disk_index': int(disk['dest']), 'disk_snapshot_ident': src_disks[i]})
        return result


class MigrateTaskWithCdpWorker(threading.Thread, _WorkerLog):
    # restore_target_object : RestoreTarget 数据库对象
    def __init__(self, source_host_object, restore_target_object, target_host_object, host_snapshot_object, data):
        super(MigrateTaskWithCdpWorker, self).__init__()
        self._migrate_task_object = self._create_migrate_task_object(source_host_object, restore_target_object,
                                                                     target_host_object, data)
        self.name = r'[{}]->[{}]({}) migrate task with cdp thread'.format(source_host_object.ident,
                                                                          restore_target_object.ident,
                                                                          self._migrate_task_object.id)
        self.log_info('MigrateTaskWithCdpWorker init')
        self._migrate_task_object.set_host_snapshot(host_snapshot_object)

    @staticmethod
    def _create_migrate_task_object(source_host_object, restore_target_object, target_host_object, data):
        if target_host_object is None:
            task_object = MigrateTask.objects.create(source_host=source_host_object,
                                                     source_type=MigrateTask.SOURCE_TYPE_CDP,
                                                     destination_type=MigrateTask.DESTINATION_TYPE_PE,
                                                     restore_target=restore_target_object,
                                                     ext_config=json.dumps(data, ensure_ascii=False))
        else:
            task_object = MigrateTask.objects.create(source_host=source_host_object,
                                                     source_type=MigrateTask.SOURCE_TYPE_CDP,
                                                     destination_type=MigrateTask.DESTINATION_TYPE_HOST,
                                                     destination_host=target_host_object,
                                                     restore_target=restore_target_object,
                                                     ext_config=json.dumps(data, ensure_ascii=False))

        desc = r'开始迁移"{host_ip}"到"{pe_ip}"'.format(
            host_ip=source_host_object.last_ip,
            pe_ip=json.loads(restore_target_object.info)['remote_ip']
        )
        HostLog.objects.create(
            host=source_host_object, type=HostLog.LOG_MIGRATE_START,
            reason=json.dumps({'migrate_task': task_object.id, 'description': desc, 'task_type': 'migrate_task'},
                              ensure_ascii=False)
        )
        return task_object

    def work(self, immediately_run):
        if immediately_run:
            self.run()
        else:
            self.start()

    def run(self):
        try:
            self.log_info(r'run start')
            self.run_logic()
            self.log_info(r'run end')
        except xlogging.BoxDashboardException as bde:
            self.log_error(r' run BoxDashboardException : {} | {}'.format(bde.msg, bde.debug))
            finish_migrate_task_object(self.name, self._migrate_task_object.id, False, bde.msg, bde.debug)
        except Exception as e:
            self.log_error(r' run Exception : {}'.format(e))
            finish_migrate_task_object(self.name, self._migrate_task_object.id, False, '内部异常，代码2334',
                                       'Exception:{}'.format(e))

    def run_logic(self):
        pe_restore = PeRestore(self._migrate_task_object.restore_target)
        pe_restore.work('migrate_{}'.format(self._migrate_task_object.id))
