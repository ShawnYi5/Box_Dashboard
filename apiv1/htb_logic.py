import json
import uuid

from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from xdashboard.handle.authorize import authorize_init
from apiv1.htb_task import HTBFlowEntrance
from apiv1.models import HTBSchedule, HTBTask, RestoreTarget, HostMac, Host
from apiv1.serializers import HTBScheduleSerializer
from apiv1.views import PeHostSessionInfo
from box_dashboard import xlogging

_logger = xlogging.getLogger(__name__)


def _check_htb_schedule_valid(_id):
    try:
        return HTBSchedule.objects.get(id=_id)
    except HTBSchedule.DoesNotExist:
        xlogging.raise_and_logging_error('不存在的热备计划:{}'.format(_id), 'invalid HTBSchedule id:{}'.format(_id),
                                         status.HTTP_404_NOT_FOUND)


class HTBScheduleCreate(APIView):
    serializer_class_s = HTBScheduleSerializer
    query_set = HTBSchedule.objects

    def __init__(self, **kwargs):
        super(HTBScheduleCreate, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def post(self, request, api_request=None):
        if api_request is None:
            api_request = request.data
        obj = HTBSchedule.objects.create(**api_request)
        return Response(status=status.HTTP_202_ACCEPTED, data={"id": obj.id})

    def update(self, request, api_request=None):
        if api_request is None:
            api_request = request.data
        htb_obj = HTBSchedule.objects

        if 'id' in api_request['filter']:
            htb_obj = htb_obj.filter(id=api_request['filter']['id'])

        if 'name' in api_request:
            htb_obj.update(name=api_request['name'])

        if 'task_type' in api_request:
            htb_obj.update(task_type=api_request['task_type'])

        if 'ext_config' in api_request:
            htb_obj.update(ext_config=api_request['ext_config'])

        if 'enabled' in api_request:
            htb_obj.update(enabled=api_request['enabled'])

        if 'deleted' in api_request:
            htb_obj.update(deleted=api_request['deleted'])

        if 'target_info' in api_request:
            htb_obj.update(target_info=api_request['target_info'])

        return Response(status=status.HTTP_202_ACCEPTED)

    def get(self, request, api_request=None):
        if api_request is None:
            api_request = request.data
        current_user = None if request is None else request.user
        _id = None
        if 'id' in api_request:
            _id = api_request['id']
        if current_user.is_superuser:
            data = self.query_set.all()
        else:
            data = self.query_set.filter(host__user_id=current_user.id).all()
        if _id:
            data = data.filter(id=_id)
        if 'filter' in api_request:
            if 'deleted' in api_request['filter']:
                data = data.filter(deleted=api_request['filter']['deleted'])
            if 'enabled' in api_request['filter']:
                data = data.filter(enabled=api_request['filter']['enabled'])
            if 'host' in api_request['filter']:
                data = data.filter(host=api_request['filter']['host'])
            if 'target_info' in api_request['filter']:
                data = data.filter(target_info=api_request['filter']['target_info'])
        return Response(status=status.HTTP_202_ACCEPTED, data=self.serializer_class_s(data, many=True).data)


class HTBScheduleExecute(APIView):
    def __init__(self, **kwargs):
        super(HTBScheduleExecute, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def post(self, schedule_id, standby_restart=False):

        schedule = _check_htb_schedule_valid(schedule_id)
        if not schedule.enabled:
            return Response(status=status.HTTP_406_NOT_ACCEPTABLE, data='立即执行失败，计划被禁用！')
        if schedule.deleted:
            return Response(status=status.HTTP_406_NOT_ACCEPTABLE, data='立即执行失败，计划被删除！')

        if schedule.htb_task.filter(start_datetime__isnull=False, finish_datetime__isnull=True).exists():
            return Response(status=status.HTTP_406_NOT_ACCEPTABLE, data='立即执行失败，已经存在正在运行的任务！')

        sc_host = schedule.host
        if sc_host.htb_schedule.filter(deleted=False).count() > 1:
            return Response(status=status.HTTP_406_NOT_ACCEPTABLE, data='立即执行失败，主机存在其它热备计划！')

        self.init_switch_params(schedule)

        if standby_restart:
            htb_task = schedule.htb_task.last()
            htb_task.finish_datetime = None
            htb_task.successful = False
            htb_task.ext_config = json.dumps({'standby_restart': True})
            htb_task.save(update_fields=['finish_datetime', 'successful', 'ext_config'])
        else:
            # 系统恢复
            if schedule.restore_type == HTBSchedule.HTB_RESTORE_TYPE_SYSTEM:
                msg, htb_task = self._create_htb_task(schedule)
            # 卷恢复
            elif schedule.restore_type == HTBSchedule.HTB_RESTORE_TYPE_VOLUME:
                msg, htb_task = self._create_htb_task_for_vol(schedule)
            else:
                return Response(status=status.HTTP_406_NOT_ACCEPTABLE, data='内部错误，不支持的内型。')

            if not htb_task:
                return Response(status=status.HTTP_406_NOT_ACCEPTABLE, data=msg)

        hb_flow = HTBFlowEntrance(htb_task.id)
        htb_task.start_datetime = timezone.now()
        htb_task.running_task = json.dumps(hb_flow.generate_uuid())
        htb_task.save(update_fields=['start_datetime', 'running_task'])
        hb_flow.start()
        return Response(status=status.HTTP_202_ACCEPTED)

    @staticmethod
    def init_switch_params(schedule):
        exc_config = json.loads(schedule.ext_config)
        if 'manual_switch' in exc_config:
            exc_config['manual_switch']['status'] = 4
            schedule.ext_config = json.dumps(exc_config)
            schedule.save(update_fields=['ext_config'])
        else:
            # do nothing
            pass

    @staticmethod
    def _create_htb_task(schedule):
        if not schedule.host:
            _logger.debug('_create_htb_task not a host')
            return '立即执行失败，计划未关联主机', None
        target_info = json.loads(schedule.target_info)
        if not target_info:
            _logger.debug('_create_htb_task target_info is empty')
            return '立即执行失败，获取PE信息失败', None
        rs_ident = HTBScheduleExecute.get_restore_target_from_mac(target_info)
        if not rs_ident:
            msg = '立即执行失败，未关联还原目标客户端。请重新建立热备计划。'
            return msg, None
        restore_target = RestoreTarget.objects.get(ident=rs_ident)
        clret = authorize_init.check_host_rebuild_count(rs_ident)
        if clret.get('r', 0) != 0:
            return clret.get('e', '错误'), None
        if restore_target.start_datetime or restore_target.finish_datetime:
            msg = '立即执行失败，还原目标客户端已经发送过命令。请删除当前计划后再次建立热备计划。'
            return msg, None
        else:
            return '', HTBTask.objects.create(schedule=schedule,
                                              task_uuid=uuid.uuid4().hex.lower(),
                                              restore_target=restore_target)

    @staticmethod
    def _create_htb_task_for_vol(schedule):
        if not schedule.host:
            _logger.debug('_create_htb_task_for_vol not a host')
            return '立即执行失败，计划未关联主机', None
        dst_host = Host.objects.filter(ident=schedule.dst_host_ident).first()
        if not dst_host:
            return '立即执行失败，目标主机不存在', None
        if not dst_host.is_linked:
            return '立即执行失败，目标主机{}已经离线。'.format(dst_host.name), None
        return '', HTBTask.objects.create(schedule=schedule,
                                          task_uuid=uuid.uuid4().hex.lower()
                                          )

    @staticmethod
    def get_host_by_mac_info(target_info):
        host_macs = HostMac.objects.filter(mac__in=target_info, duplication=False)
        for host_mac in host_macs:
            host = host_mac.host
            if host.is_linked:
                if host.htb_task.filter(start_datetime__isnull=False, finish_datetime__isnull=True).exists():
                    continue
                return host
        return None

    @staticmethod
    @xlogging.convert_exception_to_value(list())
    def get_host_mac_info(host):
        net_adapters = json.loads(host.ext_info)['system_infos']['Nic']
        return [net_adapter['Mac'] for net_adapter in net_adapters]

    @staticmethod
    @xlogging.convert_exception_to_value(list())
    def get_restore_target_macs(restore_target_ident):
        target = RestoreTarget.objects.get(ident=restore_target_ident)
        if target.start_datetime:
            info = json.loads(target.info)
            # 需要排下序， 否则is_dest_host_in_plan 会判断失误
            return sorted([net_adapter['szMacAddress'] for net_adapter in info['net_adapters']])
        else:
            rsp = PeHostSessionInfo().get(None, restore_target_ident)
            if not status.is_success(rsp.status_code):
                return list()
            return sorted([net_adapter['szMacAddress'] for net_adapter in rsp.data['network_adapters']])

    @staticmethod
    def get_restore_target_from_mac(mac_info):
        _logger.debug('get_access_target_from_mac mac_info:{}'.format(mac_info))
        if not mac_info:
            _logger.error('get_access_target_from_mac mac_info is empty')
            return None
        if not isinstance(mac_info, list):
            _logger.error('get_access_target_from_mac mac_info is not a list')
            return None

        targets = RestoreTarget.objects.all().order_by('-id')
        for target in targets:
            info_db = HTBScheduleExecute.get_restore_target_macs(target.ident)
            if not info_db:
                _logger.debug('get_access_target_from_mac not get mac_info, from ident:{}'.format(target.ident))
                continue
            elif set(info_db) & set(mac_info):
                break
        else:
            return None
        return target.ident
