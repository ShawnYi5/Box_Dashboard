import threading
import time
import uuid
import datetime

from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from web_guard.models import ModifyTask, UserStatus
from box_dashboard import xlogging, xdata

_logger = xlogging.getLogger(__name__)
_lock = threading.RLock()


class ModifyTaskMonitorThreading(threading.Thread):
    TIME_DELAY = 60

    def run(self):
        time.sleep(self.TIME_DELAY)
        try:
            self._work()
        except Exception as e:
            _logger.error('ModifyTaskMonitorThreading error:{}'.format(e))

    @xlogging.db_ex_wrap
    def _work(self):
        while True:
            tasks = self._get_work_tasks()
            for task in tasks:
                if self._check_is_invalid(task):
                    _logger.debug('ModifyTaskMonitorThreading task:{} is finish!'.format(task.name))
                    ModifyTaskHandle.finish_task(task)
            time.sleep(1)

    @staticmethod
    @xlogging.LockDecorator(_lock)
    def _get_work_tasks():
        return ModifyTask.objects.filter(finish_datetime__isnull=True)

    @staticmethod
    @xlogging.LockDecorator(_lock)
    def _check_is_invalid(task):
        as_user = task.modify_entry.modify_admin.all()
        return task.expire_datetime <= timezone.now() or list(filter(ModifyTaskMonitorThreading.is_offline, as_user))

    @staticmethod
    def is_offline(user):
        user_status_obj = UserStatusView.get_user_status_obj(user)
        rs = not user_status_obj.is_linked
        return rs


class ModifyTaskHandle(object):
    @staticmethod
    @xlogging.LockDecorator(_lock)
    def update_task_time(task_uuid):
        task = ModifyTask.objects.get(task_uuid=task_uuid)
        # 如果任务没有完成，更新时间
        if not task.finish_datetime:
            task.expire_datetime += datetime.timedelta(seconds=xdata.CREATE_MODIFY_TASK_DEFAULT_SECS)
            task.finish_datetime = None
            task.save(update_fields=['expire_datetime', 'finish_datetime'])
            return task
        # 新创建任务
        else:
            return ModifyTaskHandle._create_task_in_db(task.modify_entry)

    @staticmethod
    @xlogging.LockDecorator(_lock)
    def create_task(modify_entry):
        exists_task = ModifyTask.objects.filter(finish_datetime__isnull=True, modify_entry=modify_entry)
        if exists_task.exists():
            return exists_task.first()
        else:
            return ModifyTaskHandle._create_task_in_db(modify_entry)

    @staticmethod
    @xlogging.LockDecorator(_lock)
    def finish_task(task):
        ModifyTaskHandle._enable_strategy(task)
        ModifyTaskHandle._force_trusted(task)
        task.finish_datetime = timezone.now()
        task.save(update_fields=['finish_datetime'])

    @staticmethod
    def _disable_strategy(task):
        task.modify_entry.monitors.all().update(enabled=False)

    @staticmethod
    def _enable_strategy(task):
        task.modify_entry.monitors.all().update(enabled=True)

    @staticmethod
    def _force_trusted(task):
        task.modify_entry.monitors.all().update(use_history=False)

    @staticmethod
    def _create_task_in_db(modify_entry):
        task_uuid = uuid.uuid4().hex
        expire_datetime = timezone.now() + datetime.timedelta(seconds=xdata.CREATE_MODIFY_TASK_DEFAULT_SECS)
        task = ModifyTask.objects.create(modify_entry=modify_entry, task_uuid=task_uuid,
                                         expire_datetime=expire_datetime)
        ModifyTaskHandle._disable_strategy(task)
        return task


class UserStatusView(APIView):
    MAX_SESSION = 15

    def __init__(self, **kwargs):
        super(UserStatusView, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    @staticmethod
    def get(request, api_request=None):
        if api_request is None:
            api_request = request.data
        user = request.user
        if user:
            UserStatusView._update_session_time(user)
        return Response(status=status.HTTP_200_OK)

    @staticmethod
    def _update_session_time(user):
        user_status_obj = UserStatusView.get_user_status_obj(user)
        user_status_obj.session_time = timezone.now() + datetime.timedelta(seconds=UserStatusView.MAX_SESSION)
        user_status_obj.save(update_fields=['session_time'])

    @staticmethod
    def login(user):
        user_status_obj = UserStatusView.get_user_status_obj(user)
        user_status_obj.status = UserStatus.STATUS_LOGIN
        user_status_obj.session_time = timezone.now() + datetime.timedelta(seconds=UserStatusView.MAX_SESSION)
        user_status_obj.save(update_fields=['session_time', 'status'])

    @staticmethod
    def logout(user):
        user_status_obj = UserStatusView.get_user_status_obj(user)
        user_status_obj.status = UserStatus.STATUS_LOGOUT
        user_status_obj.save(update_fields=['status'])

    @staticmethod
    def get_user_status_obj(user):
        try:
            return user.user_status
        except Exception:
            return UserStatus.objects.create(user=user)
