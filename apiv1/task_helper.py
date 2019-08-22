import json
import time

from box_dashboard import xlogging, boxService
from .models import CDPTask, Host

_logger = xlogging.getLogger(__name__)


class TaskHelper(object):
    @staticmethod
    def check_backup_status_in_cdp_task(host_ident, cdp_task_id, user_cancel_check):
        cdp_task_object = CDPTask.objects.get(id=cdp_task_id)
        if cdp_task_object.finish_datetime is not None:
            return False

        user_cancel_check.check()

        host_status_list = boxService.box_service.GetStatus(host_ident)
        if 'off_line' in host_status_list:
            host_status_list = TaskHelper.wait_host_status_list_is_not_off_line(host_ident,
                                                                                '_check_backup_status_in_cdp_task')

        if 'backup' not in host_status_list:
            host_snapshot_object = CDPTask.objects.get(id=cdp_task_id).host_snapshot
            if host_snapshot_object.successful:
                if host_snapshot_object.partial:
                    _logger.error(
                        'check_backup_status_in_cdp_task task finished, snapshot_id:{} is partial'.format(
                            host_snapshot_object.id))
                    TaskHelper.raise_last_backup_error(host_snapshot_object)
                else:
                    return False
            else:
                TaskHelper.raise_last_backup_error(host_snapshot_object)

        return True  # 正在做基础备份

    @staticmethod
    def wait_host_status_list_is_not_off_line(host_ident, debug, interval_seconds=20, retry_times=15):
        host_status_list = list()

        while retry_times > 0:
            time.sleep(interval_seconds)

            host_object = Host.objects.get(ident=host_ident)
            if not host_object.is_linked:
                break

            host_status_list = boxService.box_service.GetStatus(host_ident)
            if 'off_line' not in host_status_list:
                return host_status_list
            else:
                retry_times -= 1

        xlogging.raise_and_logging_error('客户端离线', r'{} {} {}'.format(host_ident, debug, host_status_list))

    @staticmethod
    def raise_last_backup_error(host_snapshot_object):
        ext_info = json.loads(host_snapshot_object.ext_info)
        if ext_info.get('agent_finish_code', 2) == 2:  # 2 is BackupFinishCode.Failed
            error_string = TaskHelper._queryLastBackupError(host_snapshot_object.host.ident)
            if error_string is None or len(error_string) < 3:
                xlogging.raise_and_logging_error(
                    '任务状态异常，代码2351', r'{} queryLastBackupError invalid : {}'
                        .format(host_snapshot_object.host.ident, error_string))

            _logger.warning(r'_raise_last_backup_error : {}'.format(error_string))
            error_string_args = error_string.split('||')
            if error_string_args[0] == '1':  # BackupErrorType.SystemError - 1||message||debug||raw_code
                if error_string_args[3] == '22617':
                    xlogging.raise_and_logging_error(error_string_args[1] + r'，请将代理程序加入360等安全类软件的白名单 ',
                                                     error_string_args[2])
                xlogging.raise_and_logging_error(error_string_args[1], error_string_args[2])
            elif error_string_args[0] == '2':  # SbdError - 2||error_code||message
                # TODO : 需要将Sbd产生的错误码格式化
                debug_string = 'error code : 0x{}'.format(error_string_args[1])
                if error_string_args[1].upper() == 'C000009A':
                    xlogging.raise_and_logging_error('Agent无法分配到足够的系统内核内存',
                                                     r'{} {}'.format(error_string_args[2], debug_string))
                elif error_string_args[1].upper() == 'C00E0011':  # ERROR_CANCEL_BAK
                    xlogging.raise_and_logging_error('备份异常取消',
                                                     r'{}'.format(debug_string))
                elif len(error_string_args[2]) > 1:
                    xlogging.raise_and_logging_error(error_string_args[2], debug_string)
                else:
                    xlogging.raise_and_logging_error('代理程序异常，错误码：0x{}'.format(error_string_args[1]), debug_string)
            elif error_string_args[0] == '3':  # NetError - 3||error_name||message
                xlogging.raise_and_logging_error(error_string_args[2], r'error type : {}'.format(error_string_args[1]))
            elif error_string_args[0] == '4':  # DriverError - 4||Region||User||System
                err_http_status = xlogging.ERROR_HTTP_STATUS_DEFAULT
                if error_string_args[2].upper() == 'C00E0002':
                    err_http_status = xlogging.ERROR_HTTP_STATUS_NEED_RETRY
                    msg = 'Agent检测到磁盘IO繁忙，稍后自动重试'
                elif error_string_args[2].upper() == 'C000009A':
                    msg = 'Agent无法分配到足够的系统内核内存'
                else:
                    msg = '代理程序异常'
                # TODO : 需要将driver产生的错误码格式化

                xlogging.raise_and_logging_error(msg, r'Region:0x{} User:0x{} System:0x{}'
                                                 .format(error_string_args[1], error_string_args[2],
                                                         error_string_args[3]),
                                                 err_http_status)
            elif error_string_args[0] == 'vmware':
                xlogging.raise_and_logging_error(error_string_args[1], error_string_args[2])
            else:
                xlogging.raise_and_logging_error(
                    '任务状态异常，代码2349', r'{} queryLastBackupError invalid : {}'
                        .format(host_snapshot_object.host.ident, error_string))
        elif ext_info['agent_finish_code'] == 1:  # 1 is  BackupFinishCode.UserCancel
            xlogging.raise_and_logging_error(r'用户取消',
                                             r'_raise_last_backup_error : {}'.format(
                                                 ext_info.get('agent_finish_code', None)))
        else:
            xlogging.raise_and_logging_error(
                '任务状态异常，代码2350', r'{} agent_finish_code invalid : {}'
                    .format(host_snapshot_object.host.ident, ext_info['agent_finish_code']))

    @staticmethod
    @xlogging.convert_exception_to_value(None)
    def _queryLastBackupError(host_ident):
        return boxService.box_service.queryLastBackupError(host_ident)
