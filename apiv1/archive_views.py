# coding=utf-8
import uuid
import json
import threading

from rest_framework.views import APIView
from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone

from apiv1.models import (ArchiveSchedule, ArchiveTask, HostSnapshot, ImportSnapshotTask, ImportSource, FileBackupTask,
                          HostLog, FileSyncTask, FileSyncSchedule)
from apiv1.serializers import (ArchiveScheduleExecuteSerializer, ArchiveScheduleSerializer,
                               ArchiveTaskScheduleCreateSerializer, ArchiveTaskScheduleUpdateSerializer,
                               ImportTaskSerializer, TaskProgressReportSerializer, ImportTaskListSerializer)
from box_dashboard import xlogging, xdata
from apiv1.archive_tasks import FlowEntrance, create_exp_flow, create_imp_flow, del_import_task
from apiv1.remote_backup_logic_remote import RemoteBackupHelperRemote
from apiv1.spaceCollection import CDPHostSnapshotSpaceCollectionMergeTask
from apiv1.logic_processors import BackupTaskScheduleLogicProcessor
from .signals import end_sleep

_logger = xlogging.getLogger(__name__)
_module_locker = threading.Lock()


def _check_archive_schedule_valid(backup_task_schedule_id):
    try:
        return ArchiveSchedule.objects.get(id=backup_task_schedule_id)
    except ArchiveSchedule.DoesNotExist:
        xlogging.raise_and_logging_error('不存在的导出计划:{}'.format(backup_task_schedule_id),
                                         'invalid ArchiveTaskSchedule:{}'.format(backup_task_schedule_id),
                                         status.HTTP_404_NOT_FOUND)


class ArchiveScheduleExecute(APIView):
    serializer_class = ArchiveScheduleExecuteSerializer

    def __init__(self, **kwargs):
        super(ArchiveScheduleExecute, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def post(self, request, api_request=None):
        if api_request is None:
            api_request = request.data

        serializer = self.serializer_class(data=api_request)
        serializer.is_valid(True)
        data = serializer.data

        schedule = get_object_or_404(ArchiveSchedule, id=data['schedule'])
        latest_host_snapshot, latest_snapshot_datetime = self._fetch_latest_snapshot(schedule.host)
        if not latest_host_snapshot:
            return Response('主机不存在可导出的快照点。', status=status.HTTP_403_FORBIDDEN)

        last_host_snapshot, last_snapshot_datetime = self._get_last_export_snapshot(schedule)
        if last_host_snapshot:
            last_cmp_data = last_snapshot_datetime if last_snapshot_datetime else last_host_snapshot.start_datetime
            latest_cmp_data = latest_snapshot_datetime if latest_snapshot_datetime else latest_host_snapshot.start_datetime
            if latest_cmp_data > last_cmp_data:
                host_snapshot = latest_host_snapshot
                snapshot_datetime = latest_snapshot_datetime
            else:
                return Response('主机不存在新的可导出备份点。', status=status.HTTP_403_FORBIDDEN)
        else:
            host_snapshot = latest_host_snapshot
            snapshot_datetime = latest_snapshot_datetime

        task_or_response = self._create_task(host_snapshot, schedule, snapshot_datetime, data['force_full'])
        if isinstance(task_or_response, Response):
            return task_or_response
        else:
            task_flow = FlowEntrance(task_or_response.id, 'export_flow_{}'.format(task_or_response.id), create_exp_flow)
            task_or_response.start_datetime = timezone.now()
            task_or_response.running_task = json.dumps(task_flow.generate_uuid())
            task_or_response.save(update_fields=['start_datetime', 'running_task'])
            task_flow.start()
            logic_processor = BackupTaskScheduleLogicProcessor(schedule)
            schedule.last_run_date = timezone.now()
            schedule.next_run_date = logic_processor.calc_next_run(False)
            schedule.save(update_fields=['last_run_date', 'next_run_date'])
            return Response(status=status.HTTP_201_CREATED)

    @xlogging.LockDecorator(_module_locker)
    def _create_task(self, host_snapshot, schedule, snapshot_datetime, force_full):
        if ArchiveTask.objects.filter(schedule=schedule, finish_datetime__isnull=True).exists():
            return Response('主机存在任务正在被执行', status=status.HTTP_403_FORBIDDEN)
        else:
            task = ArchiveTask.objects.create(
                schedule=schedule,
                host_snapshot=host_snapshot,
                snapshot_datetime=snapshot_datetime,
                task_uuid=uuid.uuid4().hex,
                force_full=force_full
            )
            _logger.info('create ArchiveTask {}'.format(task))
            return task

    def put(self):
        pass

    @staticmethod
    def _get_last_export_snapshot(schedule):
        last_task = ArchiveTask.objects.filter(successful=True, finish_datetime__isnull=False,
                                               schedule=schedule).order_by(
            'start_datetime').last()
        if last_task:
            return last_task.host_snapshot, last_task.snapshot_datetime
        else:
            return None, None

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


class ArchiveScheduleViews(APIView):
    serializer_class = ArchiveScheduleSerializer
    queryset = ArchiveSchedule.objects.filter(deleted=False)

    def __init__(self, **kwargs):
        super(ArchiveScheduleViews, self).__init__(**kwargs)
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
                schedules = self.queryset.filter(host__user_id=current_user.id).all()
            serializer = self.serializer_class(schedules, many=True)
            return Response(serializer.data)

    @xlogging.LockDecorator(_module_locker)
    def post(self, request, api_request=None):
        if api_request is None:
            api_request = request.data
        serializer = ArchiveTaskScheduleCreateSerializer(data=api_request)
        serializer.is_valid(True)
        if ArchiveSchedule.objects.filter(deleted=False, host_id=serializer.validated_data['host']).exists():
            return Response('主机已存在导出计划', status=status.HTTP_406_NOT_ACCEPTABLE)
        schedule = ArchiveSchedule.objects.create(**serializer.validated_data)
        logic_processor = BackupTaskScheduleLogicProcessor(schedule)
        schedule.next_run_date = logic_processor.calc_next_run(True)
        schedule.save(update_fields=['next_run_date'])
        return Response(data=self.serializer_class(schedule).data, status=status.HTTP_201_CREATED)

    def put(self, request, schedule_id, api_request=None):
        if api_request is None:
            api_request = request.data

        schedule = _check_archive_schedule_valid(schedule_id)

        serializer_old = self.serializer_class(schedule)
        data_old = serializer_old.data

        serializer = ArchiveTaskScheduleUpdateSerializer(data=api_request)
        serializer.is_valid(True)

        update_fields = list()

        if 'enabled' in serializer.validated_data:
            schedule.enabled = serializer.validated_data['enabled']
            update_fields.append('enabled')
        if 'name' in serializer.validated_data:
            schedule.name = serializer.validated_data['name']
            update_fields.append('name')
        if 'cycle_type' in serializer.validated_data:
            schedule.cycle_type = serializer.validated_data['cycle_type']
            update_fields.append('cycle_type')
        if 'plan_start_date' in serializer.validated_data:
            schedule.plan_start_date = serializer.validated_data['plan_start_date']
            update_fields.append('plan_start_date')
        if 'ext_config' in serializer.validated_data:
            schedule.ext_config = serializer.validated_data['ext_config']
            update_fields.append('ext_config')
        if 'storage_node_ident' in serializer.validated_data:
            schedule.storage_node_ident = serializer.validated_data['storage_node_ident']
            update_fields.append('storage_node_ident')

        logic_processor = BackupTaskScheduleLogicProcessor(schedule)
        schedule.next_run_date = logic_processor.calc_next_run(True)
        update_fields.append('next_run_date')

        schedule.save(update_fields=update_fields)

        if ('enabled' in update_fields) and (not schedule.enabled):
            end_sleep.send_robust(sender=ArchiveSchedule, schedule_id=schedule.id)

        serializer_new = self.serializer_class(schedule)
        data_new = serializer_new.data
        if json.dumps(data_old, sort_keys=True) != json.dumps(data_new, sort_keys=True):
            _logger.info(r'alter schedule : {}'.format(
                json.dumps({'schedule_id': schedule.id, 'old': data_old, 'new': data_new}, ensure_ascii=False)))

        return Response(data_new, status=status.HTTP_202_ACCEPTED)

    @staticmethod
    def delete(request, schedule_id):
        schedule = _check_archive_schedule_valid(schedule_id)
        if schedule is not None:
            schedule.delete_and_collection_space_later()
            end_sleep.send_robust(sender=ArchiveSchedule, schedule_id=schedule.id)
        return Response(status=status.HTTP_204_NO_CONTENT)  # always successful


class ImportTaskExecute(APIView):
    serializer_class = ImportTaskSerializer
    serializer_class_list = ImportTaskListSerializer

    def __init__(self, **kwargs):
        super(ImportTaskExecute, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def get(self, request):
        current_user = None if request is None else request.user
        if current_user.is_superuser:
            schedules = ImportSnapshotTask.objects.filter.all()
        else:
            schedules = ImportSnapshotTask.objects.filter(host_snapshot__host__user_id=current_user.id).all()
        serializer = self.serializer_class_list(schedules, many=True)
        return Response(serializer.data)

    def post(self, request, api_request=None):
        if api_request is None:
            api_request = request.data

        serializer = self.serializer_class(data=api_request)
        serializer.is_valid(True)
        data = serializer.data
        if data['src_type'] == ImportSource.LOCAL_TASK:
            source, _ = ImportSource.objects.get_or_create(src_type=data['src_type'],
                                                           local_task_uuid=data['local_task_uuid'])
            if ImportSnapshotTask.objects.filter(source=source.id, finish_datetime__isnull=True):
                return Response('执行失败，任务正在被执行', status=status.HTTP_403_FORBIDDEN)
            else:
                task = ImportSnapshotTask.objects.create(
                    source=source,
                    task_uuid=uuid.uuid4().hex
                )
                task_flow = FlowEntrance(task.id, 'import_flow_{}'.format(task.id), create_imp_flow, data['user_id'],
                                         data['storage_path'])
                task.start_datetime = timezone.now()
                task.running_task = json.dumps(task_flow.generate_uuid())
                task.save(update_fields=['start_datetime', 'running_task'])
                task_flow.start()
                return Response()
        else:
            return Response('未知类型', status=status.HTTP_406_NOT_ACCEPTABLE)

    @staticmethod
    def delete(request, task_id):
        ret = del_import_task(task_id)
        if ret:
            return Response('{"r":0}', status=status.HTTP_204_NO_CONTENT)
        else:
            return Response('{"r":1}', status=status.HTTP_204_NO_CONTENT)


class TaskProgressReport(APIView):
    serializer_class = TaskProgressReportSerializer

    def __init__(self, **kwargs):
        super(TaskProgressReport, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def post(self, request, api_request=None):
        if api_request is None:
            api_request = request.data
        serializer = self.serializer_class(data=api_request)
        serializer.is_valid(True)
        data = serializer.data
        if data['task_type'] == xdata.TASK_PROGRESS_EXPORT_SNAPSHOT:
            return self._report_export_snapshot_task(data['task_uuid'], json.loads(data['payload']))
        elif data['task_type'] == xdata.TASK_PROGRESS_IMPORT_SNAPSHOT:
            return self._report_import_snapshot_task(data['task_uuid'], json.loads(data['payload']))
        elif data['task_type'] == xdata.TASK_PROGRESS_NAS_FILE_BACKUP:
            return self._report_nas_file_backup(data['task_uuid'], json.loads(data['payload']))
        elif data['task_type'] == xdata.TASK_PROGRESS_FILE_SYNC:
            return self._report_file_sync(data['task_uuid'], json.loads(data['payload']))
        else:
            return Response('未知类型', status=status.HTTP_406_NOT_ACCEPTABLE)

    @staticmethod
    def _report_export_snapshot_task(task_uuid, payload):
        archive_task = get_object_or_404(ArchiveTask, task_uuid=task_uuid)
        ext_config = json.loads(archive_task.ext_config)
        if payload['status'] == 'transfer_data':
            ext_config['progressIndexTemp'] = ext_config.get('progressIndex', 0)
            ext_config['progressIndex'] = payload['progressIndex']
            ext_config['progressTotal'] = payload['progressTotal']
            ext_config['updateTime'] = timezone.now().strftime('%Y-%m-%d %H:%M:%S')
            archive_task.ext_config = json.dumps(ext_config)
            archive_task.save(update_fields=['ext_config'])
            return Response({'rev': 0})
        else:
            return Response('未知状态，{}'.format(payload['status']), status=status.HTTP_406_NOT_ACCEPTABLE)

    @staticmethod
    def _report_import_snapshot_task(task_uuid, payload):
        archive_task = get_object_or_404(ImportSnapshotTask, id=task_uuid)
        ext_config = json.loads(archive_task.ext_config)
        if payload['status'] == 'transfer_data':
            ext_config['progressIndexTemp'] = ext_config.get('progressIndex', 0)
            ext_config['progressIndex'] = payload['progressIndex']
            ext_config['progressTotal'] = payload['progressTotal']
            ext_config['updateTime'] = timezone.now().strftime('%Y-%m-%d %H:%M:%S')
            archive_task.ext_config = json.dumps(ext_config)
            archive_task.save(update_fields=['ext_config'])
            return Response({'rev': 0})
        else:
            return Response('未知状态，{}'.format(payload['status']), status=status.HTTP_406_NOT_ACCEPTABLE)

    @staticmethod
    def _set_filebackup_status(archive_task, rsync_status):
        check_status_is_initialize = '传输数据'
        filebackup_status = archive_task.get_status_display()
        if filebackup_status == check_status_is_initialize or len(rsync_status) == 0:
            return
        if rsync_status['current_sync_bytes'] > 0 or rsync_status['scan_count'] > 0:
            archive_task.set_status(archive_task.TRANSFER_DATA)
        if rsync_status['current_sync_bytes'] == 0:
            return

    @staticmethod
    def _report_nas_file_backup(task_uuid, payload):
        archive_task = get_object_or_404(FileBackupTask, task_uuid=task_uuid)
        ext_config = json.loads(archive_task.ext_config)
        if payload['status'] == 'transfer_data':
            ext_config['progressIndexTemp'] = ext_config.get('progressIndex', 0)
            ext_config['progressIndex'] = payload['progressIndex']
            ext_config['progressTotal'] = payload['progressTotal']
            ext_config['updateTime'] = timezone.now().strftime('%Y-%m-%d %H:%M:%S')
            ext_config['rsync_status'] = payload.get('rsync_status', {})
            archive_task.ext_config = json.dumps(ext_config)
            TaskProgressReport._set_filebackup_status(archive_task, ext_config['rsync_status'])
            archive_task.save(update_fields=['ext_config'])
            return Response({'rev': 0})
        elif payload['status'] == 'warning_info':
            payload.update({
                'task_type': 'nas_file_backup',
                'archive_task': archive_task.id,
            })
            HostLog.objects.create(host=archive_task.schedule.host, type=HostLog.LOG_BACKUP_START,
                                   reason=json.dumps(payload, ensure_ascii=False))
            return Response({'rev': 0})
        else:
            return Response('未知状态，{}'.format(payload['status']), status=status.HTTP_406_NOT_ACCEPTABLE)

    @staticmethod
    def _report_file_sync(task_uuid, payload):
        file_sync_task = get_object_or_404(FileSyncTask, task_uuid=task_uuid)
        ext_config = json.loads(file_sync_task.ext_config)
        status_human, status_str = payload['status']
        if status_str == 'report_finish':
            file_sync_task.successful = payload['successful']
            file_sync_task.finish_datetime = timezone.now()
            file_sync_task.save(update_fields=['successful', 'finish_datetime'])
            end_sleep.send_robust(sender=FileSyncSchedule, schedule_id=file_sync_task.schedule.id)
            return Response({'rev': 0})
        elif status_str and status_human:
            ext_config['status_str'] = status_str
            ext_config['status_human'] = status_human
            ext_config['updateTime'] = timezone.now().strftime('%Y-%m-%d %H:%M:%S')
            file_sync_task.ext_config = json.dumps(ext_config)
            file_sync_task.save(update_fields=['ext_config'])
            return Response({'rev': 0})
        else:
            return Response('未知状态，{}'.format(payload['status']), status=status.HTTP_406_NOT_ACCEPTABLE)
