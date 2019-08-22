import json
import os

from django.contrib.auth.models import User

from apiv1.restore import RestoreTargetChecker
from box_dashboard import xlogging, boxService, xdata
from xdashboard.handle.authorize.authorize_init import get_separation_of_the_three_members
from .logic_processors import HostSessionLogicProcessor
from .models import (Host, BackupTask, RestoreTarget, CDPTask, MigrateTask, RestoreTask, VirtualMachineRestoreTask,
                     Disk, DiskSnapshot, FileBackupTask, TakeOverKVM, HostSnapshot)
from .tasks import BackupTaskReWorker, CDPTaskReWorker, MigrateTaskWithNormalReWorker, FileBackupTaskReWorker

_logger = xlogging.getLogger(__name__)

import KTService

_host_session_logic_processor = None
CIPHERTEXT_FILE = '/etc/aio/password_ciphertext.json'


def _generate_system_user():
    default_user = 'admin'
    default_password = '123456'
    try:
        return User.objects.get(username=default_user)
    except User.DoesNotExist:
        User.objects.create_superuser(default_user, '', default_password)
    except Exception as e:
        _logger.error(r'_generate_system_user failed : {}'.format(e))
        raise


def _generate_aud_user():
    if not get_separation_of_the_three_members().is_separation_of_the_three_members_available():
        return
    from xdashboard.models import UserProfile
    default_user = 'audadmin'
    default_password = '123456'
    try:
        return User.objects.get(username=default_user)
    except User.DoesNotExist:
        user = User()
        user.username = default_user
        user.is_superuser = False
        user.is_staff = False
        user.is_active = True
        user.set_password(default_password)
        user.save()
        # 用户扩展信息 profile
        profile = UserProfile()
        profile.user_id = user.id
        profile.modules = 0
        profile.desc = '安全审计管理员'
        profile.user_type = UserProfile.AUD_ADMIN
        profile.save()
    except Exception as e:
        _logger.error(r'_generate_aud_user failed : {}'.format(e))
        raise


def _init_host_session_logic_processor():
    global _host_session_logic_processor
    if _host_session_logic_processor is None:
        _host_session_logic_processor = HostSessionLogicProcessor()


@xlogging.convert_exception_to_value(False)
def _is_pe_host_linked(ident):
    return boxService.box_service.isPeHostLinked(ident)


@xlogging.convert_exception_to_value(None)
def _deal_not_linked_pe_host(pe_host):
    pe_host.delete()


def _check_pe_host_status():
    pe_hosts = RestoreTarget.objects.filter(start_datetime__isnull=True)
    for pe_host in pe_hosts:
        if not _is_pe_host_linked(pe_host.ident):
            _deal_not_linked_pe_host(pe_host)


def _load_not_finished_backup_task():
    tasks = BackupTask.objects.filter(finish_datetime__isnull=True).all()
    for task in tasks:
        try:
            worker = BackupTaskReWorker(task.id)
            worker.work()
        except Exception as e:
            _logger.error('_load_not_finished_backup_task {} failed {}'.format(task.id, e), exc_info=True)


def _load_not_finished_file_backup_task():
    tasks = FileBackupTask.objects.filter(finish_datetime__isnull=True).all()
    for task in tasks:
        try:
            worker = FileBackupTaskReWorker(task.id)
            worker.work()
        except Exception as e:
            _logger.error('_load_not_finished_file_backup_task {} failed {}'.format(task.id, e), exc_info=True)


def _load_not_finished_cdp_task():
    tasks = CDPTask.objects.filter(finish_datetime__isnull=True).all()
    for task in tasks:
        try:
            worker = CDPTaskReWorker(task.id)
            worker.work()
        except Exception as e:
            _logger.error('_load_not_finished_cdp_task {} failed {}'.format(task.id, e), exc_info=True)


def _load_not_finished_migrate_task():
    tasks = MigrateTask.objects.filter(
        finish_datetime__isnull=True, source_type__in=[MigrateTask.SOURCE_TYPE_NORMAL,
                                                       MigrateTask.SOURCE_TYPE_TEMP_NORMAL]).all()
    for task in tasks:
        try:
            worker = MigrateTaskWithNormalReWorker(task.id)
            worker.work()
        except Exception as e:
            _logger.error('_load_not_finished_migrate_task {} failed {}'.format(task.id, e), exc_info=True)


def _load_not_finished_vmware_restore_task():
    from apiv1.vmware_task import VMRFlowEntrance
    tasks = VirtualMachineRestoreTask.objects.filter(finish_datetime__isnull=True).all()
    for task in tasks:
        try:
            vmr_flow = VMRFlowEntrance(task.id)
            vmr_flow.load_from_uuid(json.loads(task.running_task))
            vmr_flow.start()
        except Exception as e:
            _logger.error('_load_not_finished_vmware_restore_task {} failed {}'.format(task.id, e), exc_info=True)


def clean_all_login_datetime():
    hosts = Host.objects.filter(login_datetime__isnull=False).all()
    for host in hosts:
        if host.type == Host.NAS_AGENT:
            continue
        host.login_datetime = None
        host.save(update_fields=['login_datetime', ])


@xlogging.convert_exception_to_value(None)
def _lock_all_takeover_nbd():
    kvms = TakeOverKVM.objects.all()
    for kvm in kvms:
        ext_info = json.loads(kvm.ext_info)
        disk_snapshots = ext_info['disk_snapshots']
        devices = disk_snapshots['boot_devices']
        for device in devices:
            nbd = device['device_profile'].get('nbd', None)
            if nbd:
                boxService.box_service.NbdSetUsed(nbd['device_name'])
        devices = disk_snapshots['data_devices']
        for device in devices:
            nbd = device['device_profile'].get('nbd', None)
            if nbd:
                boxService.box_service.NbdSetUsed(nbd['device_name'])


def init():
    _generate_system_user()
    _generate_aud_user()
    _create_special_disk_snapshot()
    _init_host_session_logic_processor()
    _load_not_finished_backup_task()
    _load_not_finished_file_backup_task()
    _load_not_finished_cdp_task()
    _load_not_finished_migrate_task()
    _load_not_finished_vmware_restore_task()
    _check_pe_host_status()
    _finish_not_finished_restore_task()
    _rm_host_share_link_dir()
    _modify_host_type()
    _lock_all_takeover_nbd()
    username_passwd = User.objects.get(username='admin').password
    ciphertext = {'password_ciphertext': username_passwd}
    if not os.path.exists(CIPHERTEXT_FILE):
        os.system('touch ' + CIPHERTEXT_FILE)
    with open(CIPHERTEXT_FILE, "w") as f:
        json.dump(ciphertext, f)


def host_session_logic_processor():
    if _host_session_logic_processor is None:
        xlogging.raise_and_logging_error(r'内部异常，代码0909', r'_host_session_logic_processor is None')
    return _host_session_logic_processor


def _finish_not_finished_restore_task():
    """
    只能finish 处于还原准备阶段的任务，即:exc_config 中不存在 xdata.RESTORE_IS_COMPLETE  字段
    """
    not_finish_tasks = RestoreTask.objects.filter(start_datetime__isnull=False, finish_datetime__isnull=True)
    for not_finish_task in not_finish_tasks:
        ext_info = json.loads(not_finish_task.ext_config)
        if xdata.RESTORE_IS_COMPLETE not in ext_info:
            _logger.debug('_finish_not_finished_restore_task get task:{}, finish it!'.format(not_finish_task.id))
            finish_restore_task_worker(not_finish_task)


@xlogging.convert_exception_to_value(None)
def finish_restore_task_worker(not_finish_task):
    RestoreTargetChecker.report_restore_target_finished(
        not_finish_task.restore_target, False, r'清除还原目标', r'clean not finish task!')
    for disk in not_finish_task.restore_target.disks.all():
        try:
            boxService.box_service.updateToken(
                KTService.Token(token=disk.token, snapshot=[], expiryMinutes=0))
        except Exception as e:
            _logger.warning('call boxService.updateToken failed. {}'.format(e))


@xlogging.convert_exception_to_value(None)
def _rm_host_share_link_dir():
    boxService.box_service.runCmd('rm -rf {}'.format(os.path.join(xdata.FILE_BROWSER_LINK_PREFIX, '*')), True)


@xlogging.convert_exception_to_value(None)
def _modify_host_type():
    """
    将aio_info字段非空的主机的type字段更新为REMOTE_AGENT
    """
    rows = Host.objects.exclude(aio_info='{}').exclude(type=Host.REMOTE_AGENT).all()
    count = rows.count()
    for row in rows:
        row.type = Host.REMOTE_AGENT
        row.save(update_fields=['type', ])
    _logger.info('_modify_host_type run, update count:{}'.format(count))


@xlogging.convert_exception_to_value(None)
def _create_special_disk_snapshot():
    def _create_one_special_snapshot(_special_disk_ident):
        import IMG
        disk, _ = Disk.objects.get_or_create(ident=_special_disk_ident)
        disk_snapshot, _ = DiskSnapshot.objects.get_or_create(disk=disk,
                                                              bytes=5 * (1024 ** 3),
                                                              display_name='clw boot disk',
                                                              type=DiskSnapshot.DISK_MBR,
                                                              boot_device=False,
                                                              ident=_special_disk_ident)
        disk_snapshot.image_path = '/home/clwboot/{}.qcow'.format(_special_disk_ident)
        if not boxService.box_service.isFileExist(disk_snapshot.image_path):
            snapshot = IMG.ImageSnapshotIdent(disk_snapshot.image_path, _special_disk_ident)
            handle = boxService.box_service.createNormalDiskSnapshot(
                snapshot, [], disk_snapshot.bytes,
                r'PiD{:x} boxdashboard|clw boot disk'.format(os.getpid()))

            boxService.box_service.closeNormalDiskSnapshot(handle, True)
        disk_snapshot.save(update_fields=['image_path'])

    os.makedirs('/home/clwboot/', exist_ok=True)
    for _special_disk_ident in (xdata.CLW_BOOT_REDIRECT_MBR_UUID, xdata.CLW_BOOT_REDIRECT_GPT_UUID,
                                xdata.CLW_BOOT_REDIRECT_GPT_LINUX_UUID,):
        _create_one_special_snapshot(_special_disk_ident)
