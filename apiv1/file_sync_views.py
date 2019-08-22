# coding=utf-8
import uuid
import json
import threading
import datetime

from rest_framework.views import APIView
from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone

from apiv1.models import (FileSyncSchedule, FileSyncTask, Host)
from apiv1.serializers import FileSyncScheduleSerializer
from box_dashboard import xlogging, xdatetime
from apiv1.file_sync_tasks import FileSyncFlowEntrance, create_sync_flow
from apiv1.remote_backup_logic_remote import RemoteBackupHelperRemote
from apiv1.spaceCollection import CDPHostSnapshotSpaceCollectionMergeTask
from apiv1.logic_processors import BackupTaskScheduleLogicProcessor
from .signals import end_sleep

_logger = xlogging.getLogger(__name__)
_module_locker = threading.Lock()


def _check_archive_schedule_valid(backup_task_schedule_id):
    try:
        return FileSyncSchedule.objects.get(id=backup_task_schedule_id)
    except FileSyncSchedule.DoesNotExist:
        xlogging.raise_and_logging_error('不存在的导出计划:{}'.format(backup_task_schedule_id),
                                         'invalid FileSyncSchedule:{}'.format(backup_task_schedule_id),
                                         status.HTTP_404_NOT_FOUND)


class FileSyncScheduleExecute(APIView):

    def __init__(self, **kwargs):
        super(FileSyncScheduleExecute, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def post(self, request, api_request=None):
        if api_request is None:
            api_request = request.data

        schedule = get_object_or_404(FileSyncSchedule, id=api_request['schedule'])
        latest_host_snapshot, latest_snapshot_datetime = self._fetch_latest_snapshot(schedule.host)
        if not latest_host_snapshot:
            return Response('主机不存在可归档的快照点。', status=status.HTTP_403_FORBIDDEN)

        task_or_response = self._create_task(latest_host_snapshot, schedule, latest_snapshot_datetime)
        if isinstance(task_or_response, Response):
            return task_or_response
        else:
            task_flow = FileSyncFlowEntrance(task_or_response.id, 'file_sync_{}'.format(task_or_response.id),
                                             create_sync_flow)
            task_or_response.start_datetime = timezone.now()
            ext_config = json.loads(task_or_response.ext_config)
            ext_config['running_task'] = task_flow.generate_uuid()
            task_or_response.ext_config = json.dumps(ext_config)
            task_or_response.save(update_fields=['start_datetime', 'ext_config'])
            task_flow.start()
            logic_processor = BackupTaskScheduleLogicProcessor(schedule)
            schedule.last_run_date = timezone.now()
            schedule.next_run_date = logic_processor.calc_next_run(False)
            schedule.save(update_fields=['last_run_date', 'next_run_date'])
            return Response(status=status.HTTP_201_CREATED)

    @xlogging.LockDecorator(_module_locker)
    def _create_task(self, host_snapshot, schedule, snapshot_datetime):
        if FileSyncTask.objects.filter(schedule=schedule, finish_datetime__isnull=True).exists():
            return Response('主机存在任务正在被执行', status=status.HTTP_403_FORBIDDEN)
        else:
            task = FileSyncTask.objects.create(
                schedule=schedule,
                host_snapshot=host_snapshot,
                snapshot_datetime=snapshot_datetime,
                task_uuid=uuid.uuid4().hex,
            )
            _logger.info('create FileSyncTask {}'.format(task))
            return task

    def put(self):
        pass

    @staticmethod
    def _fetch_latest_snapshot(host):
        query_set = RemoteBackupHelperRemote.query_host_snapshot_order_by_time(host.ident)
        latest_snapshot = query_set.exclude(partial=True).last()
        if not latest_snapshot:
            _logger.warning('_fetch_latest_snapshot not exists, latest snapshot')
            return None, None
        else:
            if latest_snapshot.is_cdp:
                if CDPHostSnapshotSpaceCollectionMergeTask.get_running_task_using(latest_snapshot):
                    end_datetime = timezone.now()
                else:
                    end_datetime = latest_snapshot.cdp_info.last_datetime
            else:
                end_datetime = None

            return latest_snapshot, end_datetime


class FileSyncScheduleViews(APIView):
    serializer_class = FileSyncScheduleSerializer
    queryset = FileSyncSchedule.objects.filter(deleted=False)

    def __init__(self, **kwargs):
        super(FileSyncScheduleViews, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def get(self, request, schedule_id=None):
        if schedule_id:
            schedule = _check_archive_schedule_valid(schedule_id)
            serializer = self.serializer_class(schedule)
            return Response(serializer.data)
        else:
            current_user = None if request is None else request.user
            if current_user.is_superuser:
                schedules = self.queryset.all()
            else:
                schedules = self.queryset.filter(host__user=current_user).all()
            serializer = self.serializer_class(schedules, many=True)
            return Response(serializer.data)

    @xlogging.LockDecorator(_module_locker)
    def post(self, request, api_request=None):
        if api_request is None:
            api_request = request.data
        if FileSyncSchedule.objects.filter(deleted=False,
                                           host__ident=api_request['source_host_ident']).exists():
            return Response('主机已存在导出计划', status=status.HTTP_406_NOT_ACCEPTABLE)
        schedule = FileSyncSchedule.objects.create(
            name=api_request['name'],
            cycle_type=api_request['cycle_type'],
            plan_start_date=datetime.datetime.strptime(api_request['plan_start_date'], '%Y-%m-%d %H:%M:%S'),
            host=Host.objects.get(ident=api_request['source_host_ident']),
            target_host_ident=api_request['target_host_ident'],
            ext_config=api_request['ext_config']
        )
        logic_processor = BackupTaskScheduleLogicProcessor(schedule)
        schedule.next_run_date = logic_processor.calc_next_run(True)
        schedule.save(update_fields=['next_run_date'])
        return Response(data=self.serializer_class(schedule).data, status=status.HTTP_201_CREATED)

    def put(self, request, schedule_id, api_request=None):
        if api_request is None:
            api_request = request.data

        _logger.debug('api_request {}'.format(api_request))
        schedule = _check_archive_schedule_valid(schedule_id)

        serializer_old = self.serializer_class(schedule)
        data_old = serializer_old.data

        update_fields = list()

        if 'enabled' in api_request:
            schedule.enabled = api_request['enabled']
            update_fields.append('enabled')
        if 'name' in api_request:
            schedule.name = api_request['name']
            update_fields.append('name')
        if 'cycle_type' in api_request:
            schedule.cycle_type = api_request['cycle_type']
            update_fields.append('cycle_type')
        if 'plan_start_date' in api_request:
            schedule.plan_start_date = datetime.datetime.strptime(api_request['plan_start_date'], '%Y-%m-%d %H:%M:%S')
            update_fields.append('plan_start_date')
        if 'ext_config' in api_request:
            schedule.ext_config = api_request['ext_config']
            update_fields.append('ext_config')

        logic_processor = BackupTaskScheduleLogicProcessor(schedule)
        schedule.next_run_date = logic_processor.calc_next_run(True)
        update_fields.append('next_run_date')

        schedule.save(update_fields=update_fields)

        if ('enabled' in update_fields) and (not schedule.enabled):
            end_sleep.send_robust(sender=FileSyncSchedule, schedule_id=schedule.id)

        serializer_new = self.serializer_class(schedule)
        data_new = serializer_new.data
        if json.dumps(data_old, sort_keys=True) != json.dumps(data_new, sort_keys=True):
            _logger.info(r'alter schedule : {}'.format(
                json.dumps({'schedule_id': schedule.id, 'old': data_old, 'new': data_new}, ensure_ascii=False)))

        return Response(data_new, status=status.HTTP_202_ACCEPTED)

    @staticmethod
    def delete(request, schedule_id):
        schedule = _check_archive_schedule_valid(schedule_id)
        schedule.deleted = True
        schedule.save(update_fields=['deleted'])
        end_sleep.send_robust(sender=FileSyncSchedule, schedule_id=schedule.id)
        return Response(status=status.HTTP_204_NO_CONTENT)  # always successful
