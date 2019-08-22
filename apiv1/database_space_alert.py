import threading
import time, json
from box_dashboard import xlogging

_logger = xlogging.getLogger(__name__)


def get_database_size():
    from django.db import connection
    sql = "select (pg_database_size('box_dashboard'))"
    cursor = connection.cursor()
    cursor.execute(sql)
    raw = cursor.fetchone()
    if raw:
        used_bytes = raw[0]
    else:
        used_bytes = 0
    return int(used_bytes)


def is_database_full():
    from xdashboard.models import DataDictionary
    from xdashboard.common.dict import GetDictionary
    database_used_bytes = get_database_size()
    database_can_use_size = GetDictionary(DataDictionary.DICT_TYPE_DATABASE_CAN_USE_SIZE, 'dbmaxsize', str(1024))
    database_can_use_size = int(database_can_use_size)
    if database_can_use_size * 1024 * 1024 * 0.95 <= database_used_bytes:
        return True, database_can_use_size, database_used_bytes
    return False, database_can_use_size, database_used_bytes


class databaseSpaceAlertThread(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        self.run_status = True

    def run(self):
        from xdashboard.handle.authorize.authorize_init import get_separation_of_the_three_members
        from xdashboard.handle.logserver import SaveOperationLog
        from xdashboard.models import OperationLog, Email
        from django.contrib.auth.models import User
        from xdashboard.common.smtp import send_email
        i = 0
        while self.run_status:
            if not get_separation_of_the_three_members().is_database_use_policy():
                break
            is_full, database_can_use_size, database_used_bytes = is_database_full()
            if is_full:
                user = User.objects.get(username='admin')
                database_alert_log = '日志数据存储空间(剩余5％)。分配空间：{}MB，已使用空间：{:.2f}MB。'.format(database_can_use_size,
                                                                                       database_used_bytes / 1024 ** 2)
                _logger.debug('database_alert_log={}'.format(database_alert_log))
                SaveOperationLog(user, OperationLog.TYPE_QUOTA,
                                 json.dumps({"日志空间告警": database_alert_log}, ensure_ascii=False))
                exc_inf = dict()
                exc_inf['user_id'] = user.id
                exc_inf['content'] = database_alert_log
                exc_inf['sub'] = '日志空间告警'
                send_email(Email.LOG_ALERT, exc_inf)
                time.sleep(1 * 24 * 60 * 60)

            time.sleep(300)

        _logger.debug('databaseSpaceAlertThread exit')

    def stop(self):
        _logger.debug('databaseSpaceAlertThread stopped')
        self.run_status = False
