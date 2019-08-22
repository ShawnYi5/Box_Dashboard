import threading
import time

from box_dashboard import xdata, xlogging
from xdashboard.common.dict import GetDictionary
from xdashboard.models import DataDictionary

_logger = xlogging.getLogger(__name__)

_running_tasks = list()
_queuing_tasks = list()  # 尾入头出
_locker = threading.Lock()


@xlogging.convert_exception_to_value(int(xdata.DICT_TYPE_TASK_QUEUE_NUM_DEFAULT))
def _get_running_max_count():
    return int(GetDictionary(DataDictionary.DICT_TYPE_TASK_QUEUE_NUM,
                             'queue_num', xdata.DICT_TYPE_TASK_QUEUE_NUM_DEFAULT))


@xlogging.LockDecorator(_locker)
def _queuing_add(item):
    _queuing_tasks.append(item)


@xlogging.LockDecorator(_locker)
def _queuing_remove(item):
    _queuing_tasks.remove(item)


def _is_task_obj_running(task_obj):
    task_obj.refresh_from_db(fields=['finish_datetime', ])
    return task_obj.finish_datetime is None


def _pop_finished_task():
    global _running_tasks
    _running_tasks = [task_obj for task_obj in _running_tasks if _is_task_obj_running(task_obj)]


def _queuing_get_index_and_count(queuing_task_item):
    for index, item in enumerate(_queuing_tasks):
        if item == queuing_task_item:
            return index, len(_queuing_tasks)


def queue(task_obj, host_snapshot_obj, user_cancel_check=None):
    """
    队列任务
    :param task_obj: 任务的数据库对象
    :param host_snapshot_obj: 主机快照数据库对象
    :param user_cancel_check: 检查对象，该对象具有方法check()，当check不过时，抛出异常
    """
    sleep_seconds = None
    queuing_task_item = {'task_obj': task_obj, 'user_cancel_check': user_cancel_check}
    _queuing_add(queuing_task_item)

    try:
        while True:
            if user_cancel_check:
                user_cancel_check.check()

            if sleep_seconds is None:
                sleep_seconds = 5
            else:
                time.sleep(sleep_seconds)

            running_max_count = _get_running_max_count()

            with _locker:
                index, count = _queuing_get_index_and_count(queuing_task_item)
                if index != 0:
                    host_snapshot_obj.display_status = r'等待执行排队中，排队序号/总数：{}/{}'.format(index + 1, count)
                    host_snapshot_obj.save(update_fields=['display_status'])
                    continue

                _pop_finished_task()

                if len(_running_tasks) >= running_max_count:
                    host_snapshot_obj.display_status = r'等待执行排队中，排队序号/总数：1/{}'.format(count)
                    host_snapshot_obj.save(update_fields=['display_status'])
                    continue

                _running_tasks.append(task_obj)
                break
    finally:
        _queuing_remove(queuing_task_item)


@xlogging.LockDecorator(_locker)
def add_running(task_obj):
    _running_tasks.append(task_obj)
