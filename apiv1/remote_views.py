import json
import html
import os
import re
from datetime import datetime
import uuid

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.core.paginator import Paginator
from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404
from django.utils import timezone

from apiv1.models import Host, RemoteBackupSchedule, RemoteBackupTask
from box_dashboard import xlogging, xdatetime, functions
from apiv1.remote_backup_logic_remote import RemoteBackupHelperRemote
from apiv1.signals import exe_schedule
from xdashboard.handle import serversmgr
from .spaceCollection import SpaceCollectionWorker

_logger = xlogging.getLogger(__name__)


class RemoteBackupView(APIView):
    def __init__(self, **kwargs):
        super(RemoteBackupView, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def post(self, request, api_request=None):
        # api_request={'type':'xxx','param_1':'xxx'}
        if api_request is None:
            api_request = request.data
        result = {'r': 1, 'e': '没找到{}'.format(html.escape(api_request['type']))}

        if api_request['type'] == 'createremotebackup':
            result = self.createremotebackup(api_request)
        if api_request['type'] == 'list':
            result = self.listRemoteBackupSchedule(request, api_request)
        if api_request['type'] == 'delete':
            result = self.deleteRemoteBackupSchedule(api_request)
        if api_request['type'] == 'enable':
            result = self.enableRemoteBackupSchedule(api_request)
        if api_request['type'] == 'count':
            result = self.countRemoteBackupSchedule()

        if api_request['type'] == 'query_new_host_backup':
            result = RemoteBackupHelperRemote.query_new_host_backup(api_request['host_ident'],
                                                                    api_request['last_host_snapshot_id'])
        if api_request['type'] == 'query_latest_host_backup':
            result = RemoteBackupHelperRemote.query_latest_host_backup(api_request['host_ident'],
                                                                       api_request['last_host_snapshot_id'],
                                                                       api_request['last_datetime'])
        if api_request['type'] == 'query_new_disk_backup':
            result = RemoteBackupHelperRemote.query_new_disk_backup(api_request['host_snapshot_id'],
                                                                    api_request['last_disk_snapshot_ident'])
        if api_request['type'] == 'query_latest_disk_backup':
            result = RemoteBackupHelperRemote.query_latest_disk_backup(api_request['host_snapshot_id'],
                                                                       api_request['last_disk_snapshot_ident'],
                                                                       api_request['last_timestamp'])
        if api_request['type'] == 'kill_remote_backup_logic':
            result = RemoteBackupHelperRemote.kill_remote_backup_logic(api_request['task_uuid'],
                                                                       api_request['disk_token'])
        if api_request['type'] == 'start_remote_backup_logic':
            result = RemoteBackupHelperRemote.start_remote_backup_logic(api_request['task_uuid'],
                                                                        api_request['disk_token'],
                                                                        api_request['disk_snapshot_ident'],
                                                                        api_request['disk_snapshot_list'],
                                                                        api_request['start_time'])
        if api_request['type'] == 'query_remote_backup_status':
            result = RemoteBackupHelperRemote.query_remote_backup_status(api_request['task_uuid'],
                                                                         api_request['disk_token'])
        if api_request['type'] == 'close_remote_backup_logic':
            result = RemoteBackupHelperRemote.close_remote_backup_logic(api_request['task_uuid'],
                                                                        api_request['disk_token'])
        if api_request['type'] == 'query_is_host_cdp_back_end':
            result = RemoteBackupHelperRemote.query_is_host_cdp_back_end(api_request['host_snapshot_id'])

        if api_request['type'] == 'update_next_run_date_to_now':
            result = self.update_next_run_date_to_now(api_request)

        if api_request['type'] == 'check_qcow_file_exists':
            result = RemoteBackupHelperRemote.check_qcow_file_exists(api_request['snapshots'])

        if result is None:
            result = {}
        return Response(data=result, status=status.HTTP_200_OK)

    @staticmethod
    def append_plan_info_to_result(result, plan):
        result['plan_id'], result['plan_name'], result['enabled'] = plan.id, plan.name, plan.enabled

    @staticmethod
    def filter_remote_plans_by_key(plans, search_key):
        valid_plans_id = []
        for plan in plans:
            is_need = serversmgr.filter_hosts(search_key, plan.name, plan.host.name)
            if is_need:
                valid_plans_id.append(plan.id)
        return plans.filter(id__in=valid_plans_id)

    def listRemoteBackupSchedule(self, request, api_request):
        rs = RemoteBackupSchedule.objects.filter(deleted=False, host__user_id=request.user.id)
        search_key = request.GET.get('s_key', None)
        if search_key:
            rs = self.filter_remote_plans_by_key(rs, search_key)
        sidx = api_request.get('sidx', None)
        page = api_request['page']
        if sidx is not None:
            rs = rs.order_by(sidx)
        paginator = Paginator(object_list=rs, per_page=api_request['rows'])
        records = paginator.count
        total = paginator.num_pages
        page = total if page > total else page
        object_list = paginator.page(page).object_list
        rslist = list()
        for object in object_list:
            one = {'cell': list(), 'id': object.id}
            created = object.created.strftime(xdatetime.FORMAT_WITH_MICROSECOND)
            if object.last_run_date:
                last_run_date = object.last_run_date.strftime(xdatetime.FORMAT_WITH_SECOND)
            else:
                last_run_date = '--'
            next_run_date = self._get_next_run_date(object)
            one['cell'] = [object.id, object.name, object.host.display_name, created, object.ext_config, object.enabled,
                           last_run_date, next_run_date]
            rslist.append(one)
        result = {'r': 0, 'a': 'list', 'page': page, 'total': total, 'records': records, 'rows': rslist}
        functions.sort_gird_rows(request, result)
        return json.dumps(result, ensure_ascii=False)

    @xlogging.convert_exception_to_value('--')
    def _get_next_run_date(self, schedule):
        ext_config = json.loads(schedule.ext_config)
        if ext_config['backup_period']['period_type'] == 'bak-continue':
            return '--'
        else:
            return schedule.next_run_date.strftime(xdatetime.FORMAT_WITH_SECOND)

    def update_next_run_date_to_now(self, api_request):
        result = {'r': 0, 'e': '操作成功'}
        schedule_set = RemoteBackupSchedule.objects.filter(id=api_request['id'])
        if schedule_set.exists():
            schedule = schedule_set.first()
        else:
            result['r'] = 1
            result['e'] = '内部错误，获取计划失败！'
            return result
        if RemoteBackupTask.objects.filter(start_datetime__isnull=False,
                                           finish_datetime__isnull=True,
                                           schedule=schedule).exists():
            result['r'] = 1
            result['e'] = '立即执行计划【{}】失败，已存在任务正在运行。'.format(schedule.name)
        elif not schedule.enabled:
            result['r'] = 1
            result['e'] = '立即执行计划【{}】失败，计划被禁用。'.format(schedule.name)
        else:
            result['e'] = '立即执行计划【{}】成功。'.format(schedule.name)
            schedule.next_run_date = timezone.now()
            schedule.save(update_fields=['next_run_date'])
            exe_schedule.send(RemoteBackupSchedule, schedule_id="RemoteBackupSchedule_{}".format(schedule.id))
        self.append_plan_info_to_result(result, schedule)
        return result

    @staticmethod
    def is_edit_remote_plan(api_request):
        edit_plan_id = api_request['edit_plan_id']
        return edit_plan_id.isdigit()

    @staticmethod
    def remove_fields_from_new_ext_config(new_ext_config):
        if 'create_time' in new_ext_config:
            del new_ext_config['create_time']

        if 'sync_host' in new_ext_config:
            del new_ext_config['sync_host']

        return new_ext_config

    def modify_schedule_info(self, api_request):
        edit_plan_id = api_request['edit_plan_id']
        schedule = RemoteBackupSchedule.objects.get(id=edit_plan_id)
        new_ext_config, old_ext_config = api_request['full_param'], json.loads(schedule.ext_config)
        new_ext_config = self.remove_fields_from_new_ext_config(new_ext_config)
        old_ext_config.update(new_ext_config)
        schedule.name = old_ext_config['plan_name']['value']
        schedule.ext_config = json.dumps(old_ext_config)
        schedule.storage_node_ident = old_ext_config['storage_device']['value']
        schedule.next_run_date = datetime.strptime(old_ext_config['backup_period']['start_datetime'],
                                                   '%Y-%m-%d %H:%M:%S')
        self.generate_bandwidth_config_file(api_request['full_param'])
        schedule.save(update_fields=['name', 'ext_config', 'storage_node_ident', 'next_run_date'])
        exe_schedule.send(RemoteBackupSchedule, schedule_id="RemoteBackupSchedule_{}".format(schedule.id))
        return schedule

    def _gen_display_name(self, host_name):
        i = 0
        tmp_name = host_name
        while True:
            if Host.objects.filter(display_name=tmp_name):
                i += 1
                tmp_name = '{}({})'.format(host_name, i)
            else:
                return tmp_name

    def createremotebackup(self, api_request):
        result = {'r': 0, 'e': '操作成功'}
        aio_info = {'ip': api_request['ip'], 'username': api_request['username'],
                    'password': api_request['password']}
        aio_info = json.dumps(aio_info, ensure_ascii=False)
        hosts = Host.objects.filter(ident=api_request['ident'])
        if hosts:
            hosts.update(aio_info=aio_info)
        if self.is_edit_remote_plan(api_request):
            schedule = self.modify_schedule_info(api_request)
            self.append_plan_info_to_result(result, schedule)
            return json.dumps(result, ensure_ascii=False)

        bupdatehost = False
        for host in hosts:
            if not host.is_remote:
                result['r'] = '1'
                result['e'] = '创建失败，主机【{}】已在本地列表中'.format(host.display_name)
                return json.dumps(result, ensure_ascii=False)
            else:
                host.set_delete(False)
                if not host.user:
                    host.user = User.objects.get(id=api_request['user_id'])
                    host.save(update_fields=['user'])
                elif host.user.id != api_request['user_id']:
                    result['r'] = '1'
                    result['e'] = '创建失败，主机【{}】已从属于用户【{}】'.format(host.display_name, host.user, host.user.id)
                    return json.dumps(result, ensure_ascii=False)
                else:
                    pass
                hosts.update(display_name=api_request['display_name'])
                if api_request['network_transmission_type']:
                    hosts.update(network_transmission_type=api_request['network_transmission_type'])
                bupdatehost = True
        if not bupdatehost:
            user = User.objects.get(id=api_request['user_id'])
            Host.objects.create(ident=api_request['ident'],
                                display_name=self._gen_display_name(api_request['display_name']), user=user,
                                network_transmission_type=api_request['network_transmission_type'], aio_info=aio_info,
                                ext_info=api_request['host_ext_info'],
                                type=Host.REMOTE_AGENT)

        host = Host.objects.get(ident=api_request['ident'])
        rs = RemoteBackupSchedule.objects.filter(host=host, deleted=False).first()
        if not rs:
            self.generate_bandwidth_config_file(api_request['full_param'])
            self.set_schedule_space_collection_params(api_request['full_param'])
            start_datetime = api_request['full_param']['backup_period']['start_datetime']
            next_run_date = datetime.strptime(start_datetime, '%Y-%m-%d %H:%M:%S')
            schedule = RemoteBackupSchedule.objects.create(name=api_request['name'], host=host,
                                                           storage_node_ident=api_request['storage_node_ident'],
                                                           ext_config=json.dumps(api_request['full_param']),
                                                           next_run_date=next_run_date)
        else:
            result['r'] = '1'
            result['e'] = '创建失败，计划【{}】已包含主机【{}】'.format(rs.name, host.display_name)
            return json.dumps(result, ensure_ascii=False)

        self.append_plan_info_to_result(result, schedule)
        return json.dumps(result, ensure_ascii=False)

    @staticmethod
    def generate_bandwidth_config_file(full_param):
        bandwidth_config_path = full_param.get('bandwidth_config_path', None)
        if not bandwidth_config_path:
            bandwidth_config_path = r'/home/mnt/remotess/rate_limit_{}'.format(uuid.uuid4().hex)
        os.makedirs(os.path.dirname(bandwidth_config_path), exist_ok=True)
        max_network_val = full_param['max_network_Mb']['value']

        if max_network_val and max_network_val != '-1':
            setting = {'mbps': int(max_network_val)}
        else:
            setting = {'mbps': None}

        with open(bandwidth_config_path, 'wt') as fout:
            json.dump(setting, fout)
        full_param['bandwidth_config_path'] = bandwidth_config_path

    @staticmethod
    def _convert_data_keep_duration_to_days(data_keep_duration):
        val, unit = data_keep_duration['value'], data_keep_duration['unit']

        return int(val) if unit == 'day' else int(val) * 30

    def set_schedule_space_collection_params(self, full_param):
        full_param['backupDataHoldDays'] = self._convert_data_keep_duration_to_days(full_param['data_keep_duration'])
        full_param['backupLeastNumber'] = int(full_param['mini_keep_points']['value'])
        full_param['autoCleanDataWhenlt'] = int(full_param['space_keep_GB']['value'])
        if full_param['continue_windows']['value']:  # None, '1', '2', '3' ...
            full_param['cdpDataHoldDays'] = int(full_param['continue_windows']['value'])

    @staticmethod
    def rm_schedule_bandwidth_config_file(schedule):
        sche_ext = json.loads(schedule.ext_config)
        config_path = sche_ext.get('bandwidth_config_path', '')
        if not os.path.exists(config_path):
            return False

        os.remove(config_path)
        return True

    def deleteRemoteBackupSchedule(self, api_request):
        result = {'r': 0, 'e': '操作成功'}
        sche_obj = RemoteBackupSchedule.objects.filter(id=api_request['id']).first()
        if sche_obj:
            self.rm_schedule_bandwidth_config_file(sche_obj)
            sche_obj.delete_and_collection_space_later()
            SpaceCollectionWorker.set_remote_schedule_deleting_and_collection_space_later(sche_obj)

        self.append_plan_info_to_result(result, sche_obj)
        return json.dumps(result, ensure_ascii=False)

    def enableRemoteBackupSchedule(self, api_request):
        result = {'r': 0, 'e': '操作成功'}
        rs = RemoteBackupSchedule.objects.filter(id=api_request['id'])
        if rs:
            if rs.first() and rs.first().enabled:
                rs.update(enabled=False)
            else:
                rs.update(enabled=True)

        self.append_plan_info_to_result(result, rs.first())
        return json.dumps(result, ensure_ascii=False)

    def countRemoteBackupSchedule(self):
        result = {'r': 0, 'e': '操作成功'}
        result['count'] = RemoteBackupSchedule.objects.filter(deleted=False).count()
        return json.dumps(result, ensure_ascii=False)
