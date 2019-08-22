"""
WSGI config for box_dashboard project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/1.8/howto/deployment/wsgi/
"""
from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()
import sys

sys.path.append('/sbin/aio/vmware_agent/')

import time
import os
from box_dashboard import boxService, xlogging, xdebug, xdata
from apiv1 import main, planScheduler, storage_nodes, hostshare, restore, spaceCollection, compress, tdog, ClientIpMg, \
    www_license, vmware_session, database_space_alert, models_hook, snapshot
from web_guard import background_logic, content_mgr
from xdashboard.common import smtp
from django.contrib.auth.models import User
from xdashboard.models import Email
from multiprocessing import Process
from xdashboard.handle.authorize.authorize_init import get_license_Thread
import threading

_logger = xlogging.getLogger(__name__)

_logger.info(r'file {} running'.format(__name__))

_plan_scheduler = None
_cdp_checker = None
_storage_node_relink_worker = None
_host_share = None
_storage_node_delete_worker = None
_restore_target_checker = None
_space_collection_thread = None
_x_debug_helper = None
_start_send_email = None
_acquire_device_data = None
_cdp_rotating_file = None
_hosts_sys_info_update = None
_nodes_record_space = None
_quotas_record_space = None
_share_timeout_checker = None
_compress_task_threading_handle = None
_web_guard_plan_scheduler = None
_web_guard_alarm_notify = None
_web_guard_alarm_auto_response = None
_tdog_checker = None
_www_license_checker = None
_modify_content_task_handle = None
_htb_schedule_monitor_handle = None
_ClientIpMg_thread_handle = None
_remote_backup_threading_handle = None
_host_session_monitor_handle = None
_vmware_host_session_logic_handle = None
_get_license_BackupTaskSchedule_handle = None
_database_space_alert_handle = None
_database_backup_base_increment = None
_database_backup_base_base = None
_check_db_bak_cout = None
_models_hook_handle = None
_archive_media_manager = None
_update_disk_snapshot_cdp_timestamp = None
_update_user_quota = None
_set_net_if_mtu_handle = None
_clean_host_snapshot_handle = None


def _start_restore_source_valid_checker():
    _restore_source_valid_checker = restore.get_restore_source_valid_checker_obj()
    _restore_source_valid_checker.setDaemon(True)
    _restore_source_valid_checker.start()


def _start_check_db_bak_cout():
    global _check_db_bak_cout
    _check_db_bak_cout = planScheduler.DatabaseSaveDays()
    _check_db_bak_cout.setDaemon(True)
    _check_db_bak_cout.start()


def _start_database_backup_base_base():
    global _database_backup_base_base
    _database_backup_base_base = planScheduler.BackupDatabaseBase()
    _database_backup_base_base.setDaemon(True)
    _database_backup_base_base.start()


def _start_database_backup_base_increment():
    global _database_backup_base_increment
    _database_backup_base_increment = planScheduler.BackupDatabase()
    _database_backup_base_increment.setDaemon(True)
    _database_backup_base_increment.start()


def _start_storage_node_delete_worker():
    global _storage_node_delete_worker
    _storage_node_delete_worker = storage_nodes.StorageNodeDeleteWorker()
    _storage_node_delete_worker.setDaemon(True)
    _storage_node_delete_worker.start()


def _start_storage_node_relink_worker():
    global _storage_node_relink_worker
    _storage_node_relink_worker = storage_nodes.StorageNodeRelinkWorker()
    _storage_node_relink_worker.setDaemon(True)
    _storage_node_relink_worker.start()


def _start_plan_scheduler():
    global _plan_scheduler
    _plan_scheduler = planScheduler.PlanScheduler()
    _plan_scheduler.setDaemon(True)
    _plan_scheduler.start()


def _start_cdp_checker():
    global _cdp_checker
    _cdp_checker = planScheduler.CdpChecker()
    _cdp_checker.setDaemon(True)
    _cdp_checker.start()


def _start_host_share_thread():
    global _host_share
    _host_share = hostshare.hostShareManageThread()
    _host_share.setDaemon(True)
    _host_share.start()


def _start_restore_target_checker():
    global _restore_target_checker
    _restore_target_checker = restore.RestoreTargetChecker()
    _restore_target_checker.setDaemon(True)
    _restore_target_checker.start()


def _start_space_collection_thread():
    global _space_collection_thread
    _space_collection_thread = spaceCollection.SpaceCollectionWorker(boxService.get_space_collection_interval())
    _space_collection_thread.setDaemon(True)
    _space_collection_thread.start()


def _start_x_debug_helper():
    global _x_debug_helper
    _x_debug_helper = xdebug.XDebugHelper()
    _x_debug_helper.setDaemon(True)
    _x_debug_helper.start()


def _start_send_email_thread(conditions):
    global _start_send_email
    _start_send_email = smtp.sendEmailRobot(conditions)
    _start_send_email.setDaemon(True)
    _start_send_email.start()


def _start_acquire_device_data():
    global _acquire_device_data
    _acquire_device_data = planScheduler.AcquireDeviceData(xdata.NET_DISK_IO_SAMPLE_INTERVAL_SEC)
    _acquire_device_data.setDaemon(True)
    _acquire_device_data.start()


def _start_cdp_rotating_file():
    global _cdp_rotating_file
    _cdp_rotating_file = planScheduler.CdpRotatingFile(boxService.get_cdp_rotating_file_interval())
    _cdp_rotating_file.setDaemon(True)
    _cdp_rotating_file.start()


def _start_hosts_sys_info_update_thread():
    global _hosts_sys_info_update
    _hosts_sys_info_update = planScheduler.UpdateHostSysInfo()
    _hosts_sys_info_update.setDaemon(True)
    _hosts_sys_info_update.start()


def _start_nodes_record_space_thread():
    global _nodes_record_space
    _nodes_record_space = planScheduler.RecordNodesSpace()
    _nodes_record_space.setDaemon(True)
    _nodes_record_space.start()


def _start_quotas_record_space_thread():
    global _quotas_record_space
    _quotas_record_space = planScheduler.RecordQuotaSpace()
    _quotas_record_space.setDaemon(True)
    _quotas_record_space.start()


def _start_share_timeout_checker():
    global _share_timeout_checker
    _share_timeout_checker = planScheduler.ShareTimeout()
    _share_timeout_checker.setDaemon(True)
    _share_timeout_checker.start()


def _start_compress_date_thread():
    global _compress_task_threading_handle
    _compress_task_threading_handle = compress.CompressTaskThreading()
    _compress_task_threading_handle.setDaemon(True)
    _compress_task_threading_handle.start()


def _start_web_guard_plan_scheduler_thread():
    global _web_guard_plan_scheduler
    _web_guard_plan_scheduler = background_logic.PlanScheduler()
    _web_guard_plan_scheduler.setDaemon(True)
    _web_guard_plan_scheduler.start()


def _start_web_guard_alarm_notify_thread():
    global _web_guard_alarm_notify
    _web_guard_alarm_notify = background_logic.AlarmResponseScheduler()
    _web_guard_alarm_notify.setDaemon(True)
    _web_guard_alarm_notify.start()


def _start_web_guard_alarm_auto_response_thread():
    global _web_guard_alarm_auto_response
    _web_guard_alarm_auto_response = background_logic.AutoResponseScheduler()
    _web_guard_alarm_auto_response.setDaemon(True)
    _web_guard_alarm_auto_response.start()


def _start_tdog_checker_thread():
    global _tdog_checker
    _tdog_checker = tdog.tdogThread()
    _tdog_checker.setDaemon(True)
    _tdog_checker.start()


def _start_www_license_checker_thread():
    global _www_license_checker
    _www_license_checker = www_license.wwwLicenseThread()
    _www_license_checker.setDaemon(True)
    _www_license_checker.start()


def _start_web_guard_modify_content_task_thread():
    global _modify_content_task_handle
    _modify_content_task_handle = content_mgr.ModifyTaskMonitorThreading()
    _modify_content_task_handle.setDaemon(True)
    _modify_content_task_handle.start()


def _start_htb_schedule_monitor():
    global _htb_schedule_monitor_handle
    _htb_schedule_monitor_handle = planScheduler.HTBScheduleMonitor()
    _htb_schedule_monitor_handle.setDaemon(True)
    _htb_schedule_monitor_handle.start()


def _start_ClientIpMg_thread():
    global _ClientIpMg_thread_handle
    _ClientIpMg_thread_handle = ClientIpMg.ClientIpSwitch()
    ClientIpMg.client_ip_mg_threading = _ClientIpMg_thread_handle
    _ClientIpMg_thread_handle.setDaemon(True)
    _ClientIpMg_thread_handle.start()
    # # =====================================================================================================
    # SetIpInfo_info = []
    # one_adapeter = {'mac': None, 'ip_mask_list': None, 'dns_list': None, 'gate_way': None}
    # ip_mask_list = []
    # one_ip_mask_info = {'ip_type': 0, 'ip': None, 'mask': None}
    # dns_list = []
    #
    # one_adapeter['mac'] = '00:50:56:95:03:ED';
    # one_adapeter['gate_way'] = '172.16.1.1';
    #
    # one_ip_mask_info['ip_type'] = 1
    # one_ip_mask_info['ip'] = '172.16.6.66'
    # one_ip_mask_info['mask'] = '255.255.0.0'
    # ip_mask_list.append(copy.deepcopy(one_ip_mask_info));
    #
    # one_ip_mask_info['ip_type'] = 0
    # one_ip_mask_info['ip'] = '192.168.6.66'
    # one_ip_mask_info['mask'] = '255.255.255.0'
    # ip_mask_list.append(copy.deepcopy(one_ip_mask_info));
    #
    # dns_list.append('140.207.198.6')
    # dns_list.append('114.114.114.114')
    #
    # one_adapeter['ip_mask_list'] = ip_mask_list;
    # one_adapeter['dns_list'] = dns_list;
    #
    # SetIpInfo_info.append(copy.deepcopy(one_adapeter))
    # # =====================================================================================================
    # _ClientIpMg_thread_handle.InsertOrUpdate('2e62144a21fb4d70addd69c6e3e8b6a7', SetIpInfo_info)
    # time.sleep(30)
    # print('Query result = {}'.format(_ClientIpMg_thread_handle.Query('2e62144a21fb4d70addd69c6e3e8b6a7')))
    # _ClientIpMg_thread_handle.Remove('2e62144a21fb4d70addd69c6e3e8b6a7')
    # _ClientIpMg_thread_handle.Remove('2e62144a21fb4d70addd69c6e3e8b6a7')

    # t = ClientIpMg.SendCompressAndRunInClient()
    # cmd = {'AppName': 'test.exe', 'param': None, 'workdir': None, 'unzip_dir': r"c:\tmp", 'timeout_sec': None,
    #        'username': None, 'pwd': None, 'serv_zip_full_path': '/home/wolf.zip'}
    # t.exec_one_cmd('2e62144a21fb4d70addd69c6e3e8b6a7', cmd, True)


def noVNC_Process():
    cmd = r'python /sbin/aio/box_dashboard/xdashboard/websockify/run --web /var/www/static/noVNC/vnc_tokens --target-config=/var/www/static/noVNC/vnc_tokens 20004'
    os.system(cmd)


def _start_noVNC_process():
    t = Process(target=noVNC_Process, args=())
    t.start()


def _start_remote_backup_threading():
    global _remote_backup_threading_handle
    _remote_backup_threading_handle = planScheduler.RemoteBackupScheduleMonitor()
    _remote_backup_threading_handle.setDaemon(True)
    _remote_backup_threading_handle.start()


def _start_host_session_monitor():
    global _host_session_monitor_handle
    _host_session_monitor_handle = planScheduler.HostSessionMonitor()
    _host_session_monitor_handle.setDaemon(True)
    _host_session_monitor_handle.start()


def _start_gen_hash_threading():
    reorganize_hash_file_handle = planScheduler.ReorganizeHashFileThread()
    reorganize_hash_file_handle.setDaemon(True)
    reorganize_hash_file_handle.start()


def _start_vmware_host_session_threading():
    global _vmware_host_session_logic_handle
    _vmware_host_session_logic_handle = vmware_session.VmwareHostSessionLogic()
    _vmware_host_session_logic_handle.setDaemon(True)
    _vmware_host_session_logic_handle.start()


def _start_get_license_BackupTaskSchedule():
    global _get_license_BackupTaskSchedule_handle
    _get_license_BackupTaskSchedule_handle = get_license_Thread()
    _get_license_BackupTaskSchedule_handle.setDaemon(True)
    _get_license_BackupTaskSchedule_handle.start()


def _start_send_wei_xin_thread():
    global _send_wei_xin
    _send_wei_xin = smtp.sendWeiXinRobot()
    _send_wei_xin.setDaemon(True)
    _send_wei_xin.start()


def _start_database_space_alert_thread():
    global _database_space_alert_handle
    _database_space_alert_handle = database_space_alert.databaseSpaceAlertThread()
    _database_space_alert_handle.setDaemon(True)
    _database_space_alert_handle.start()


def _start_models_hook_thread():
    global _models_hook_handle
    _models_hook_handle = models_hook.ModelsHook()
    _models_hook_handle.setDaemon(True)
    _models_hook_handle.start()


def _start_archive_media_manager_thread():
    global _archive_media_manager
    _archive_media_manager = planScheduler.ArchiveMediaManager()
    _archive_media_manager.setDaemon(True)
    _archive_media_manager.start()


def _start_update_disk_snapshot_cdp_timestamp_thread():
    global _update_disk_snapshot_cdp_timestamp
    _update_disk_snapshot_cdp_timestamp = snapshot.UpdateDiskSnapshotCdpTimestamp()
    _update_disk_snapshot_cdp_timestamp.setDaemon(True)
    _update_disk_snapshot_cdp_timestamp.start()


def _start_update_user_quota_thread():
    global _update_user_quota
    _update_user_quota = storage_nodes.UpdateUserQuota()
    _update_user_quota.setDaemon(True)
    _update_user_quota.start()


def _start_set_net_if_mtu_thread():
    global _set_net_if_mtu_handle
    _set_net_if_mtu_handle = planScheduler.SetNetIfMtu()
    _set_net_if_mtu_handle.setDaemon(True)
    _set_net_if_mtu_handle.start()


def _start_clean_host_snapshot_thread():
    global _clean_host_snapshot_handle
    _clean_host_snapshot_handle = planScheduler.CleanHostSnapshot()
    _clean_host_snapshot_handle.setDaemon(True)
    _clean_host_snapshot_handle.start()


def normal_mode():
    _start_x_debug_helper()

    boxService.box_service.isFileExist('/sbin/aio')

    _start_models_hook_thread()
    main.clean_all_login_datetime()
    _start_storage_node_relink_worker()
    if os.path.isfile(r'/run/watchpower_check_process.json'):
        _logger.info('normal_mode find /run/watchpower_check_process.json sleep 5 S')
    while True:
        if os.path.isfile(r'/run/watchpower_check_process.json'):
            time.sleep(5)
        else:
            _logger.info('normal_mode watchpower_check_process OK continue')
            break
    main.init()
    boxService.box_service.reloginAllHostSession()
    _start_storage_node_delete_worker()
    _start_plan_scheduler()
    _start_cdp_checker()
    _start_host_share_thread()
    _start_restore_target_checker()
    _start_space_collection_thread()
    _start_send_email_thread(smtp.cond)
    _start_acquire_device_data()
    _start_cdp_rotating_file()
    _start_hosts_sys_info_update_thread()
    _start_nodes_record_space_thread()
    _start_quotas_record_space_thread()
    _start_share_timeout_checker()
    _start_compress_date_thread()
    _start_web_guard_plan_scheduler_thread()
    _start_web_guard_alarm_notify_thread()
    _start_web_guard_alarm_auto_response_thread()
    _start_tdog_checker_thread()
    _start_www_license_checker_thread()
    _start_web_guard_modify_content_task_thread()
    _start_htb_schedule_monitor()
    _start_ClientIpMg_thread()
    _start_noVNC_process()
    _start_remote_backup_threading()
    _start_host_session_monitor()
    _start_gen_hash_threading()
    _start_vmware_host_session_threading()
    _start_get_license_BackupTaskSchedule()
    _start_send_wei_xin_thread()
    _start_database_space_alert_thread()
    _start_database_backup_base_increment()
    _start_database_backup_base_base()
    _start_check_db_bak_cout()
    _start_restore_source_valid_checker()
    _start_archive_media_manager_thread()
    _start_update_disk_snapshot_cdp_timestamp_thread()
    _start_update_user_quota_thread()
    _start_set_net_if_mtu_thread()
    _start_clean_host_snapshot_thread()


def emergency_mode():
    global recently_time
    userid = User.objects.get(username='admin').id
    _start_send_email_thread(smtp.cond)
    time_str = time.strftime('%Y/%m/%d %H:%M:%S', time.localtime(int(recently_time)))
    content = '设备停止服务，检测到系统时间早于最后的运行时间：{}。\
            请更换设备CMOS电池，并将CMOS中的时间调整到正确时间。'.format(time_str)
    _logger.error(content)
    exc_info = {'sub': '系统时间错误', 'content': content, 'user_id': userid}
    smtp.send_email(Email.SYS_TIME_WRONG, exc_info)


try:
    recently_time = boxService.box_service.getTime()
except Exception as e:
    _logger.error(r'call boxService.box_service.getTime failed {}'.format(e))
    recently_time = ''

if recently_time:
    emergency_mode()
else:
    threading.Thread(target=normal_mode, args=()).start()
