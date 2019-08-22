import datetime
import json
import threading

import django.utils.timezone as timezone
from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apiv1.models import Host, RestoreTask
from apiv1.restore import RestoreTargetChecker
from apiv1.views import check_host_ident_valid, HostSnapshotLocalRestore
from box_dashboard import xlogging, xdata, xdatetime
from .host_maintain import WebMaintain
from .models import EmergencyPlan, WebGuardStrategy, AlarmMethod, StrategyGroup, UserStatus, ModifyEntry, ModifyTask
from .restore import WGRTask
from .serializers import EmPlansInfoSerializers, StrategySerializers4Create, StrategySerializers4GetInfo, \
    MaintainStatusSerializer, MaintainStatusSwitchSerializer, MaintainConfigSerializer, EmergencyPlanExecuteSerializer, \
    WGRLogicSerializer, ModifyContentTaskSerializer
from .web_check_logic import WebAnalyze
from .xmaintainance import get_maintain_status
from web_guard.content_mgr import ModifyTaskHandle

_logger = xlogging.getLogger(__name__)


def _check_user_exist(user_id):
    try:
        return User.objects.get(id=user_id)
    except User.DoesNotExist:
        return None


def _check_modify_entry(modify_entry_id):
    try:
        return ModifyEntry.objects.get(id=modify_entry_id)
    except User.DoesNotExist:
        return None


def _check_modify_task(task_uuid):
    try:
        return ModifyTask.objects.get(task_uuid=task_uuid)
    except User.DoesNotExist:
        return None


class EmPlansInfo(APIView):
    m_mgr = EmergencyPlan.objects
    serializer_class = EmPlansInfoSerializers

    def __init__(self, **kwargs):
        super(EmPlansInfo, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    @staticmethod
    def get_as_hosts(hosts_infos):
        rs = []
        for host in hosts_infos:
            rs.append(Host.objects.get(id=host['id']))
        return rs

    @staticmethod
    def get_as_strategys(strategys_info):
        rs = []
        for strategy in strategys_info:
            rs.append(WebGuardStrategy.objects.get(id=strategy['id']))
        return rs

    def get(self, request):
        re_id = request.GET.get('id', False)  # EmergencyPlan id
        st_id = request.GET.get('st_id', False)  # WebGuardStrategy id
        if re_id:
            serializer = self.serializer_class(self.m_mgr.get(id=re_id))
            return Response({'data': serializer.data, 'is_set': False})
        elif st_id:
            serializer = self.serializer_class(self.m_mgr.filter(strategy__id=st_id), many=True)
            return Response({'data': serializer.data, 'is_set': True})
        else:
            serializer = self.serializer_class(self.m_mgr.filter(user=request.user), many=True)
            return Response({'data': serializer.data, 'is_set': True})

    def put(self, request):
        args = json.loads(request.POST['data'])

        _id = args.get('id', False)
        name = args['name']
        enabled = args.get('enabled', True)
        deleted = args.get('deleted', False)
        exc_info = json.dumps(args['exc_info'])
        hosts = self.get_as_hosts(args['hosts'])
        strategy = self.get_as_strategys(args['strategy'])
        if _id:
            plan_obj = self.m_mgr.get(id=_id)
            plan_obj.name = name
            plan_obj.enabled = enabled
            plan_obj.deleted = deleted
            plan_obj.exc_info = exc_info
            plan_obj.hosts = hosts
            plan_obj.strategy = strategy
            plan_obj.save()
        else:
            obj = self.m_mgr.create(name=name, user=request.user, exc_info=exc_info)
            obj.hosts = hosts
            obj.strategy = strategy
            obj.save()
        return Response(status=status.HTTP_201_CREATED)

    def delete(self, _id):
        plan_obj = self.m_mgr.get(id=_id)
        plan_obj.deleted = True
        plan_obj.hosts = []
        plan_obj.strategy = []
        plan_obj.save()
        return Response(status=status.HTTP_202_ACCEPTED)

    def enable(self, _id, value):
        plan_obj = self.m_mgr.get(id=_id)
        plan_obj.enabled = True if int(value) else False
        plan_obj.save()
        return Response(status=status.HTTP_202_ACCEPTED)


class AlarmMethodInfo(APIView):
    m_mgr = AlarmMethod.objects

    def post(self, request):
        if self.m_mgr.filter(user=request.user).exists():
            obj = self.m_mgr.get(user=request.user)
            obj.exc_info = request.POST.get('data')
            obj.save()
        else:
            self.m_mgr.create(user=request.user)
        return Response(status=status.HTTP_202_ACCEPTED)

    def get(self, request):
        if not self.m_mgr.filter(user=request.user).exists():
            self.m_mgr.create(user=request.user)
        obj = self.m_mgr.get(user=request.user)
        rs_data = json.loads(obj.exc_info)
        return Response(data=rs_data, status=status.HTTP_202_ACCEPTED)


class Strategy(APIView):
    queryset = WebGuardStrategy.objects.filter(deleted=False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def get(self, request):
        strategies = self.queryset.all().filter(user=request.user)
        serializer = StrategySerializers4GetInfo(strategies, many=True)
        return Response(data=serializer.data)

    def post(self, request, api_params):
        serializer = StrategySerializers4Create(data=api_params)
        serializer.is_valid(True)
        strategy = WebGuardStrategy.objects.create(**serializer.validated_data)
        return Response(data=StrategySerializers4GetInfo(strategy).data, status=status.HTTP_201_CREATED)

    @staticmethod
    def set_delete(strategy_id):
        WebGuardStrategy.objects.get(id=strategy_id).set_delete()

    @staticmethod
    def set_disable(strategy_id):
        WebGuardStrategy.objects.get(id=strategy_id).set_disable()

    @staticmethod
    def set_enable(strategy_id):
        WebGuardStrategy.objects.get(id=strategy_id).set_enable()

    @staticmethod
    def update_fields(strategy_id, **kwargs):
        strategy = WebGuardStrategy.objects.get(id=strategy_id)
        update_fields = []
        if 'group' in kwargs:
            strategy.group = StrategyGroup.objects.get(id=kwargs['group'])
            update_fields.append('group')
        if 'name' in kwargs:
            strategy.name = kwargs['name']
            update_fields.append('name')
        if 'ext_info' in kwargs:
            strategy.ext_info = kwargs['ext_info']
            update_fields.append('ext_info')
        if 'check_type' in kwargs:
            strategy.check_type = kwargs['check_type']
            update_fields.append('check_type')
        strategy.save(update_fields=update_fields)

        return strategy


def _check_strategy_valid(strategy_id):
    try:
        return WebGuardStrategy.objects.get(id=strategy_id)
    except WebGuardStrategy.DoesNotExist:
        xlogging.raise_and_logging_error('不存在的检测计划:{}'.format(strategy_id),
                                         'invalid WebGuardStrategy:{}'.format(strategy_id),
                                         status.HTTP_404_NOT_FOUND)


def _check_emergency_plan_valid(plan_id):
    try:
        return EmergencyPlan.objects.get(id=plan_id)
    except EmergencyPlan.DoesNotExist:
        xlogging.raise_and_logging_error('不存在的自动应急计划:{}'.format(plan_id),
                                         'invalid EmergencyPlan:{}'.format(plan_id),
                                         status.HTTP_404_NOT_FOUND)


def _check_user_valid(user_id):
    try:
        return User.objects.get(id=user_id)
    except User.DoesNotExist:
        xlogging.raise_and_logging_error('不存在的用户:{}'.format(user_id),
                                         'invalid EmergencyPlan:{}'.format(user_id),
                                         status.HTTP_404_NOT_FOUND)


_strategy_execute_locker = threading.Lock()
_emergency_plan_execute_locker = threading.Lock()


class StrategyExecute(APIView):
    queryset = WebGuardStrategy.objects.none()
    serializer_class = StrategySerializers4GetInfo

    def __init__(self, **kwargs):
        super(StrategyExecute, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    @staticmethod
    def get(request, strategy_id):
        _logger.info(r' strategy_id : {}'.format(strategy_id))
        return Response()

    @xlogging.LockDecorator(_strategy_execute_locker)
    def post(self, request, strategy_id, api_request=None):
        strategy = _check_strategy_valid(strategy_id)
        if strategy.running_task is not None:
            xlogging.raise_and_logging_error(r'检测任务正在执行中',
                                             r'strategy.running_task is not None : {}'.format(strategy.running_task),
                                             status.HTTP_501_NOT_IMPLEMENTED)

        web_analyze = WebAnalyze(strategy_id)
        ext_info = json.loads(strategy.ext_info)
        strategy.running_task = json.dumps(web_analyze.generate_uuid(ext_info))
        strategy.last_run_date, strategy.next_run_date = self._calc_next_date(ext_info['interval_time'])
        strategy.save(update_fields=['running_task', 'last_run_date', 'next_run_date'])

        web_analyze.start()

        return Response(status=status.HTTP_201_CREATED)

    @staticmethod
    def _calc_next_date(interval_info):
        value = int(interval_info['time'])
        unit = interval_info['unit']  # secs mins
        if unit == 'mins':
            value *= 60
        return timezone.now(), timezone.now() + datetime.timedelta(seconds=int(value))


class MaintainStatus(APIView):
    queryset = Host.objects.none()
    serializer_class = MaintainStatusSerializer

    def __init__(self, **kwargs):
        super(MaintainStatus, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def get(self, request, ident):
        host = check_host_ident_valid(ident)
        maintain_status = {
            'host': host,
            'status': xdata.MAINTAIN_STATUS_UNKNOWN,
        }

        web_maintain = WebMaintain.get_or_create_web_maintain_object(host)
        config_object = json.loads(web_maintain.config)

        try:
            if 0 == get_maintain_status(host.ident, config_object['ports'])[0]:
                maintain_status['status'] = xdata.MAINTAIN_STATUS_TAKEOVER
            else:
                maintain_status['status'] = xdata.MAINTAIN_STATUS_NORMAL
        except Exception as e:
            _logger.error(r'call get_maintain_status ({}) failed. {}'.format(host.ident, e))

        return Response(self.serializer_class(maintain_status).data, status=status.HTTP_200_OK)

    def put(self, request, ident, api_request=None):
        if api_request is None:
            api_request = request.data

        serializer = MaintainStatusSwitchSerializer(data=api_request)
        serializer.is_valid(True)

        switch = (serializer.validated_data['status'] == xdata.MAINTAIN_STATUS_TAKEOVER)
        host = check_host_ident_valid(ident)

        if switch:
            WebMaintain.enter(host)
        else:
            WebMaintain.leave(host)

        return Response(status=status.HTTP_205_RESET_CONTENT)


class MaintainConfig(APIView):
    queryset = Host.objects.none()
    serializer_class = MaintainConfigSerializer

    def __init__(self, **kwargs):
        super(MaintainConfig, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def get(self, request, ident):
        host = check_host_ident_valid(ident)
        web_maintain = WebMaintain.get_or_create_web_maintain_object(host)
        config_object = json.loads(web_maintain.config)

        config = {
            'ports': config_object['ports'],
            'jpg_path': WebMaintain.get_maintain_pic(host.ident),
            'havescrpit': True if config_object.get('havescrpit', 0) == 1 else False,
            'stop_script': config_object.get('stop_script', ''),
            'start_script': config_object.get('start_script', '')
        }
        return Response(self.serializer_class(config).data, status=status.HTTP_200_OK)

    def put(self, request, ident, api_request=None):
        if api_request is None:
            api_request = request.data

        serializer = MaintainConfigSerializer(data=api_request)
        serializer.is_valid(True)

        host = check_host_ident_valid(ident)

        host_maintain_config = WebMaintain.get_or_create_web_maintain_object(host)
        if host_maintain_config:
            host_maintain_config.config = json.dumps(api_request, ensure_ascii=False)
            host_maintain_config.save()
        else:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        return Response(status=status.HTTP_200_OK)


class EmergencyPlanExecute(APIView):
    queryset = EmergencyPlan.objects.none()
    serializer_class = MaintainConfigSerializer

    def __init__(self, **kwargs):
        super(EmergencyPlanExecute, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    @staticmethod
    def get(request, plan_id):
        _logger.info(r' plan_id : {}'.format(plan_id))
        return Response()

    def post(self, request, plan_id, api_request=None):
        if api_request is None:
            api_request = request.data

        serializer = EmergencyPlanExecuteSerializer(data=api_request)
        serializer.is_valid(True)

        plan = _check_emergency_plan_valid(plan_id)
        running_tasks = json.loads(plan.running_tasks)

        current_level = serializer.validated_data['level']
        current_type = serializer.validated_data['type']
        if current_level in running_tasks.keys() and running_tasks[current_level] is not None:
            xlogging.raise_and_logging_error(r'自动应急任务正在执行中',
                                             r'EmergencyPlan.running_task [{}] is not None : {}'.format(
                                                 current_level, running_tasks[current_level]),
                                             status.HTTP_501_NOT_IMPLEMENTED)

        if current_type == EmergencyPlan.EM_MANUAL:
            pass  # do nothing
        elif current_type == EmergencyPlan.EM_AUTO:
            for host in plan.hosts.all():
                api_request = {'host_ident': host.ident, 'plan_id': plan.id, 'is_auto': True}
                rsp = WGRPreLogic().post(None, api_request)
                if rsp.status_code == status.HTTP_201_CREATED:
                    WGRPreLogic.clear_strategy_last_404(plan.id)
                    _logger.info('start WGRLogic id:{} name:{} ok'.format(plan.id, plan.name))
                else:
                    _logger.warning('start WGRLogic id:{} name:{} failed {}'
                                    .format(plan.id, plan.name, rsp.status_code))
                    return Response(status=status.HTTP_500_INTERNAL_SERVER_ERROR, data=rsp.data)
        elif current_type == EmergencyPlan.EM_MAINTAIN:
            for host in plan.hosts.all():
                MaintainStatus().put(None, host.ident, {'status': xdata.MAINTAIN_STATUS_TAKEOVER})
        else:
            xlogging.raise_and_logging_error(r'未定义的处理类型', r'EmergencyPlan em type [{}] is unknown'.format(
                current_level), status.HTTP_400_BAD_REQUEST)

        return Response(status=status.HTTP_201_CREATED)


class WGRPreLogic(APIView):
    serializer_class = WGRLogicSerializer

    def __init__(self, **kwargs):
        super(WGRPreLogic, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def post(self, request, api_request=None):
        if api_request is None:
            api_request = request.data

        serializer = self.serializer_class(data=api_request)
        serializer.is_valid(True)

        args = serializer.validated_data
        host = check_host_ident_valid(args['host_ident'])
        task = WGRTask()
        if args['is_auto']:
            task.generate_and_save(host, args['plan_id'], args['is_auto'])
        else:
            task.generate_and_save(host, args['plan_id'], args['is_auto'], args['restore_time'],
                                   args['snapshot_id'])
        task.start()

        return Response(status=status.HTTP_201_CREATED)

    @staticmethod
    def check_host_could_restore(host_ident):
        exists_task = RestoreTask.objects.filter(host_snapshot__host__ident=host_ident, finish_datetime__isnull=True)
        # 不存在任务
        if not exists_task:
            return True
        for task_obj in exists_task:
            restore_target = task_obj.restore_target
            # 如果正在传送数据,且以还原到一定的百分比则可以再次还原，则可以再次还原
            t_b = restore_target.total_bytes
            r_b = restore_target.restored_bytes
            if t_b and r_b and (int(r_b) / int(t_b) >= 0.5):
                # 先手动finish 这个任务
                RestoreTargetChecker.report_restore_target_finished(
                    restore_target, False, r'警报未接触，再次还原', r'alarm is exists, restore again!')
                return True
        return False

    @staticmethod
    def clear_strategy_last_404(plan_id):
        plan = _check_emergency_plan_valid(plan_id)
        plan.strategy.filter(deleted=False, enabled=True).update(last_404_date=None)


class GetModifyEntry(APIView):
    def __init__(self, **kwargs):
        super(GetModifyEntry, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def get(self, request):
        content_user = None if request is None else request.user
        if content_user is None:
            return Response(status=status.HTTP_406_NOT_ACCEPTABLE)
        modify_entries = content_user.modify_entries.all()
        result = list()
        for entry in modify_entries:
            result.append(r'{}'.format(entry.entrance))
        result = list(set(result))

        return Response(data=result)


class ModifyEntryTasks(APIView):
    serializer_class = ModifyContentTaskSerializer

    def __init__(self, **kwargs):
        super(ModifyEntryTasks, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def post(self, modify_entry_id):
        modify_entry = _check_modify_entry(modify_entry_id)
        if not modify_entry:
            return Response(status=status.HTTP_404_NOT_FOUND)
        task = ModifyTaskHandle.create_task(modify_entry)
        return Response(status=status.HTTP_201_CREATED, data=self.serializer_class(task).data)


class ModifyEntryTaskInfo(APIView):
    serializer_class = ModifyContentTaskSerializer

    def __init__(self, **kwargs):
        super(ModifyEntryTaskInfo, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def get(self, request, task_uuid):
        if not request.user.check_password(request.GET['ps']):
            return Response(status=status.HTTP_202_ACCEPTED, data={'remain_secs': -1})

        pre_task = _check_modify_task(task_uuid)
        task = ModifyTaskHandle.update_task_time(pre_task.task_uuid)
        remain_secs = (task.expire_datetime - timezone.now()).total_seconds()
        ret_data = self.serializer_class(task).data
        ret_data.update({'remain_secs': remain_secs})
        return Response(status=status.HTTP_202_ACCEPTED, data=ret_data)

    def delete(self, request, task_id):
        return Response(status=status.HTTP_204_NO_CONTENT)
