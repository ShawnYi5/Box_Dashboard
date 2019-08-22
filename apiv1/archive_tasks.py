# coding=utf-8
import contextlib
import datetime
import json
import os
import shutil
import subprocess
import threading
import uuid
from collections import namedtuple

from django.core import serializers
from django.utils import timezone
from rest_framework import status
from taskflow import engines
from taskflow import task
from taskflow.listeners import logging as logging_listener
from taskflow.patterns import linear_flow as lf
from taskflow.persistence import models

from apiv1.htb_task import SendTaskWork
from apiv1.models import ArchiveTask, ArchiveSubTask, StorageNode, ImportSnapshotTask, Host, HostSnapshot, \
    DiskSnapshot, Disk
from apiv1.snapshot import GetDiskSnapshot, GetSnapshotList
from box_dashboard import xlogging, task_backend, xdatetime, boxService, xdata

_logger = xlogging.getLogger(__name__)


def is_cancel(task):
    return xdata.CANCEL_TASK_EXT_KEY in load_ext_config(task)


def raise_exception_when_cancel(task):
    if is_cancel(task):
        raise Exception('user cancel')
    else:
        pass


def load_ext_config(task):
    return json.loads(ArchiveTask.objects.get(id=task.id).ext_config)


def execute_cmd(cmd, shell=False, is_waite=True, **kwargs):
    _logger.info('execute_cmd cmd:{}'.format(cmd))
    process = subprocess.Popen(cmd,
                               shell=shell,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE,
                               universal_newlines=True, **kwargs)
    if is_waite:
        stdout, stderr = process.communicate()
        _logger.info('execute_cmd out:{}|{}|{}'.format(process.returncode, stdout, stderr))
        return process.returncode, stdout, stderr
    else:
        _logger.info('execute_cmd out:{}|pid:{}'.format(process, process.pid))
        return process


class WorkerLog(object):
    name = ''

    def log_debug(self, msg):
        _logger.debug('{}: {}'.format(self.name, msg))

    def log_info(self, msg):
        _logger.info('{}: {}'.format(self.name, msg))

    def log_warning(self, msg):
        _logger.warning('{}: {}'.format(self.name, msg))

    def log_error(self, msg):
        _logger.error('{}: {}'.format(self.name, msg), exc_info=True)

    def log_error_and_raise_exception(self, msg):
        _logger.error('{}: {}'.format(self.name, msg), exc_info=True)
        raise Exception(msg)


class EXPSNInit(task.Task, WorkerLog):
    default_provides = 'task_content'

    def __init__(self, task_id, inject=None):
        super(EXPSNInit, self).__init__('EXPSNInit_{}'.format(task_id), inject=inject)
        self.task_id = task_id
        self.task = None
        self.task_content = None

    def execute(self, *args, **kwargs):
        try:
            self.task = ArchiveTask.objects.get(id=self.task_id)
            self.task_content = {
                'error': '',
                'locked_snapshots': list(),
                'tmp_files': list(),
                'lock_name': 'export_snapshot_{}'.format(self.task.id),
                'meta_data': {
                    'database': '',
                    'host_snapshot_start_datetime': '',  # 导出快照点时间
                    'host_snapshot_finish_datetime': '',
                    'total_blocks': 0,
                    'task_uuid': self.task.task_uuid,
                    'disk_info': list(),
                    'task_date': self.task.start_datetime.strftime(xdatetime.FORMAT_WITH_SECOND_FOR_PATH)  # 归档日期
                }
            }
            if self.task.snapshot_datetime:
                self.task_content['meta_data']['host_snapshot_start_datetime'] = self.task.snapshot_datetime.strftime(
                    xdatetime.FORMAT_WITH_MICROSECOND_2)
                self.task_content['meta_data']['host_snapshot_finish_datetime'] = (
                        self.task.snapshot_datetime + datetime.timedelta(minutes=10)).strftime(
                    xdatetime.FORMAT_WITH_MICROSECOND_2)
            else:
                self.task_content['meta_data'][
                    'host_snapshot_start_datetime'] = self.task.host_snapshot.start_datetime.strftime(
                    xdatetime.FORMAT_WITH_MICROSECOND_2)
                self.task_content['meta_data'][
                    'host_snapshot_finish_datetime'] = self.task.host_snapshot.finish_datetime.strftime(
                    xdatetime.FORMAT_WITH_MICROSECOND_2)
            self.task.create_log(
                '开始导出备份点:{}'.format(self.task.host_snapshot_name), '')
            self.task.set_status(ArchiveTask.INIT)
            raise_exception_when_cancel(self.task)
            host_snapshot = self.task.host_snapshot
            host_snapshot_ext_config = json.loads(host_snapshot.ext_info)
            storage = StorageNode.objects.filter(deleted=False, available=True).first()
            assert storage, 'not found available storage'
            self.task_content['task_dir'] = os.path.join(storage.path, 'archive_task', self.task.task_uuid)
            self.log_info('task dir:{}'.format(self.task_content['task_dir']))
            os.makedirs(self.task_content['task_dir'], exist_ok=True)

            lock_snapshots = list()
            self.task.set_status(ArchiveTask.FIND_SNAPSHOTS)
            host_snapshot = self.task.host_snapshot
            for index, disk_snapshot in enumerate(host_snapshot.disk_snapshots.all()):
                timestamp = self.task.snapshot_datetime.timestamp() if self.task.snapshot_datetime else None
                if timestamp:
                    disk_snapshot, timestamp = self.get_disk_snapshot(host_snapshot, disk_snapshot.disk.ident,
                                                                      timestamp)
                ident = uuid.uuid4().hex
                disk_info = {
                    'ident': ident,
                    'disk_index': index,
                    'disk_bytes': disk_snapshot.bytes,
                    'disk_snapshot': disk_snapshot.ident,
                    'disk_ident': disk_snapshot.disk.ident,
                    'native_guid': None,
                    'parent_ident': None,
                    'blocks': ''
                }

                ext_config = dict()
                ext_config['disk_index'] = index
                native_guid = self._get_native_guid(host_snapshot_ext_config, disk_snapshot.disk.ident)
                disk_info['native_guid'] = native_guid
                ext_config['map_path'] = os.path.join(self.task_content['task_dir'],
                                                      '{}.map'.format(disk_snapshot.ident))
                self.task_content['tmp_files'].append(ext_config['map_path'])
                ext_config['disk_bytes'] = disk_snapshot.bytes
                last_task = self._get_last_task(native_guid)
                if last_task:
                    ext_config['parent_ident'] = last_task.ident
                    ext_config['parent_hash'] = last_task.hash_path
                    ext_config['parent_sub_task'] = last_task.id
                    disk_info['parent_ident'] = last_task.ident
                else:
                    empty_hash = os.path.join(self.task_content['task_dir'],
                                              '{}.empty.hash'.format(disk_snapshot.ident))
                    self.task_content['tmp_files'].append(empty_hash)
                    with open(empty_hash, 'w'):
                        pass
                    ext_config['parent_ident'] = None
                    ext_config['parent_hash'] = empty_hash
                    ext_config['parent_sub_task'] = None
                snapshots, ext_config['hash_files'] = GetSnapshotList.fetch_snapshots_and_hash_files(
                    disk_snapshot, timestamp)
                ext_config['snapshots'] = [{'path': snapshot.path, 'ident': snapshot.snapshot} for snapshot in
                                           snapshots]
                date_time = datetime.datetime.fromtimestamp(timestamp) if timestamp else None
                self._create_sub_task(native_guid, ident, disk_snapshot, date_time, ext_config)
                lock_snapshots.append(snapshots)
                self.task_content['locked_snapshots'].extend(ext_config['snapshots'])
                self.task_content['meta_data']['disk_info'].append(disk_info)
            self.task.set_status(ArchiveTask.LOCK_SNAPSHOTS)
            for lock_snapshot in lock_snapshots:
                SendTaskWork.lock_snapshots_u(lock_snapshot, self.task_content['lock_name'])
        except Exception as e:
            self.log_error(e)
            self.task_content['error'] = 'error:{}'.format(e)

        return self.task_content

    @staticmethod
    def get_disk_snapshot(host_snapshot_object, disk_ident, time_stamp):
        disk_snapshot_ident, restore_timestamp = \
            GetDiskSnapshot.query_cdp_disk_snapshot_ident(host_snapshot_object, disk_ident, time_stamp)

        if disk_snapshot_ident is None or restore_timestamp is None:
            _logger.warning('no valid cdp disk snapshot use normal snapshot : {} {} {}'.format(
                host_snapshot_object, disk_ident, time_stamp))
            disk_snapshot_ident = GetDiskSnapshot.query_normal_disk_snapshot_ident(host_snapshot_object, disk_ident)

        return DiskSnapshot.objects.get(ident=disk_snapshot_ident), restore_timestamp

    def _create_sub_task(self, native_guid, ident, disk_snapshot, date_time, ext_config):
        try:
            return ArchiveSubTask.objects.get(main_task=self.task, native_guid=native_guid)
        except ArchiveSubTask.DoesNotExist:
            hash_path = os.path.join(self.task_content['task_dir'], '{}.hash'.format(ident))
            sub_task = ArchiveSubTask.objects.create(main_task=self.task,
                                                     ident=ident,
                                                     native_guid=native_guid,
                                                     disk_snapshot=disk_snapshot,
                                                     date_time=date_time,
                                                     ext_config=json.dumps(ext_config),
                                                     hash_path=hash_path)
            self.log_debug(
                'create sub task:{}|{}|{}|{}'.format(sub_task.native_guid, sub_task.disk_snapshot, ext_config,
                                                     sub_task.hash_path))
            return sub_task

    @staticmethod
    def _get_native_guid(ext_config, disk_ident):
        for disk_info in ext_config['include_ranges']:
            if disk_info['diskIdent'] == disk_ident:
                break
        else:
            xlogging.raise_and_logging_error('生成参数失败', 'get native guid fail:{}|{}'.format(ext_config, disk_ident),
                                             11131)
            return
        return disk_info['diskNativeGUID']

    def _get_last_task(self, native_guid):
        if (not self.task.schedule) or self.task.force_full:
            return None
        else:
            last_task = ArchiveTask.objects.filter(schedule=self.task.schedule,
                                                   successful=True,
                                                   finish_datetime__isnull=False).order_by('finish_datetime').last()
            if not last_task:
                return None
            sub_task = last_task.sub_tasks.filter(native_guid=native_guid).first()
            if not sub_task:
                return None
            if not boxService.box_service.isFileExist(sub_task.hash_path):
                _logger.warning('last sub task hash file not exists:{}'.format(sub_task.hash_path))
                return None
            return sub_task


class EXPSNPackagingDateBase(task.Task, WorkerLog):
    default_provides = 'task_content'

    def __init__(self, task_id, inject=None):
        super(EXPSNPackagingDateBase, self).__init__('EXPSNPackagingDateBase_{}'.format(task_id), inject=inject)
        self.task_id = task_id
        self.task = None
        self.task_content = None

    def execute(self, task_content, **kwargs):
        if FlowEntrance.has_error(task_content):
            return task_content
        self.task_content = task_content
        try:
            database = dict()
            self.task = ArchiveTask.objects.get(id=self.task_id)
            self.task.set_status(ArchiveTask.PACK_DATABASE)
            raise_exception_when_cancel(self.task)
            database['host'] = self._pack_host()
            database['host_snapshot'] = self._pack_host_snapshot()
            database['disk_snapshots'] = self._pack_snapshots()
            self.task_content['meta_data']['database'] = database
        except Exception as e:
            self.log_error(e)
            self.task_content['error'] = 'error:{}'.format(e)
        return self.task_content

    def _pack_host(self):
        host = self.task.schedule.host
        if not host.archive_uuid:
            archive_uuid = uuid.uuid4().hex
            host.archive_uuid = archive_uuid
            host.save(update_fields=['archive_uuid'])
        else:
            archive_uuid = host.archive_uuid
        assert host
        data = self.convert_objs_to_json([host])
        data[0]['fields']['ident'] = archive_uuid
        return data[0]

    def _pack_host_snapshot(self):
        assert self.task.host_snapshot
        data = self.convert_objs_to_json([self.task.host_snapshot])
        return data[0]

    def _pack_snapshots(self):
        data = list()
        for sub_task in self.task.sub_tasks.all():
            ext_config = json.loads(sub_task.ext_config)
            data.append(
                self.convert_objs_to_json([sub_task.disk_snapshot])[0])
        return data

    @staticmethod
    def convert_objs_to_json(objs):
        data_str = serializers.serialize('json', objs, ensure_ascii=False)
        return json.loads(data_str)


class EXPSNGenerateHash(task.Task, WorkerLog):
    default_provides = 'task_content'

    def __init__(self, task_id, inject=None):
        super(EXPSNGenerateHash, self).__init__('EXPSNGenerateHash_{}'.format(task_id), inject=inject)
        self.task_id = task_id
        self.task = None
        self.task_content = None

    def execute(self, task_content, **kwargs):
        if FlowEntrance.has_error(task_content):
            return task_content
        self.task_content = task_content
        try:
            self.task = ArchiveTask.objects.get(id=self.task_id)
            self.task.set_status(ArchiveTask.GENERATE_HASH)
            raise_exception_when_cancel(self.task)
            for sub_task in self.task.sub_tasks.all():
                if boxService.box_service.isFileExist(sub_task.hash_path):
                    continue
                ext_config = json.loads(sub_task.ext_config)
                optimize_parameter = {
                    'hash_files': ext_config['hash_files'],
                    'ordered_hash_file': sub_task.hash_path,
                    'disk_bytes': ext_config['disk_bytes']
                }
                # 包含CDP时需要将CDP中的数据转换为HASH数据
                if self._include_cdp(ext_config['hash_files']):
                    optimize_parameter['include_cdp'] = True
                    optimize_parameter['snapshots'] = ext_config['snapshots']
                result_mount_snapshot = json.loads(boxService.box_service.startBackupOptimize(optimize_parameter))
                result_mount_snapshot['delete_hash'] = False
                boxService.box_service.stopBackupOptimize([result_mount_snapshot])
                assert boxService.box_service.isFileExist(sub_task.hash_path), 'hash path not exists!'
        except Exception as e:
            self.log_error(e)
            self.task_content['error'] = 'error:{}'.format(e)
        return self.task_content

    @staticmethod
    def _include_cdp(hash_files):
        for hash_file in hash_files:
            if hash_file.startswith('cdp|'):
                break
        else:
            return False
        return True


class EXPSNGenerateBitMap(task.Task, WorkerLog):
    default_provides = 'task_content'

    def __init__(self, task_id, inject=None):
        super(EXPSNGenerateBitMap, self).__init__('EXPSNGenerateBitMap_{}'.format(task_id), inject=inject)
        self.task_id = task_id
        self.task_content = None
        self.task = None

    def execute(self, task_content, **kwargs):
        if FlowEntrance.has_error(task_content):
            return task_content
        self.task_content = task_content
        try:
            self.task = ArchiveTask.objects.get(id=self.task_id)
            self.task.set_status(ArchiveTask.GENERATE_BITMAP)
            raise_exception_when_cancel(self.task)
            for sub_task in self.task.sub_tasks.all():
                ext_config = json.loads(sub_task.ext_config)
                args = {
                    'base_hash': sub_task.hash_path,
                    'parent_hash': ext_config['parent_hash'],
                    'map_path': ext_config['map_path'],
                }
                blocks = boxService.box_service.hash2Interval(json.dumps(args))
                self._update_blocks_nums(blocks, sub_task.disk_snapshot.ident)
        except Exception as e:
            self.log_error(e)
            self.task_content['error'] = 'error:{}'.format(e)
        return self.task_content

    def _update_blocks_nums(self, blocks, disk_snapshot_ident):
        for disk_info in self.task_content['meta_data']['disk_info']:
            if disk_info['disk_snapshot'] == disk_snapshot_ident:
                break
        else:
            raise Exception('not found disk info')
        disk_info['blocks'] = blocks
        self.task_content['meta_data']['total_blocks'] += blocks


class EXPSNExportData(task.Task, WorkerLog):
    default_provides = 'task_content'

    def __init__(self, task_id, inject=None):
        super(EXPSNExportData, self).__init__('EXPSNExportData_{}'.format(task_id), inject=inject)
        self.task_id = task_id
        self.task_content = None
        self.task = None

    def execute(self, task_content, **kwargs):
        if FlowEntrance.has_error(task_content):
            return task_content
        self.task_content = task_content
        try:
            self.task = ArchiveTask.objects.get(id=self.task_id)
            self.task.set_status(ArchiveTask.TRANSFER_DATA)
            raise_exception_when_cancel(self.task)
            all_info = {
                'meta_data': self.task_content['meta_data'],
                'disk_snapshots': list(),
                'media_uuid': self.task.schedule.storage_node_ident,
                'total_blocks': self.task_content['meta_data']['total_blocks'],
                'task_uuid': self.task.task_uuid
            }
            for sub_task in self.task.sub_tasks.all():
                ext_config = json.loads(sub_task.ext_config)
                disk_info = dict()
                disk_info['disk_index'] = ext_config['disk_index']
                disk_info['disk_bytes'] = ext_config['disk_bytes']
                disk_info['snapshots'] = ext_config['snapshots']
                disk_info['intervals_file'] = ext_config['map_path']
                all_info['disk_snapshots'].append(disk_info)
            file_media_infos = boxService.box_service.exportSnapshot(json.dumps(all_info))
            ext_config = json.loads(self.task.ext_config)
            ext_config['file_media_infos'] = json.loads(file_media_infos)
            ext_config['media_uuid'] = self.task.schedule.storage_node_ident
            self.task.ext_config = json.dumps(ext_config)
            self.task.save(update_fields=['ext_config'])
        except Exception as e:
            self.log_error(e)
            self.task_content['error'] = 'error:{}'.format(e)
        return self.task_content


class EXPSNFinisTask(task.Task, WorkerLog):
    def __init__(self, task_id, inject=None):
        super(EXPSNFinisTask, self).__init__('EXPSNFinisTask_{}'.format(task_id), inject=inject)
        self.task_id = task_id
        self.task_content = None
        self.task = None

    def execute(self, task_content, **kwargs):
        self.task_content = task_content
        self.task = ArchiveTask.objects.get(id=self.task_id)
        if FlowEntrance.has_error(self.task_content):
            self.task.set_status(ArchiveTask.MISSION_FAIL, '', False)
            error_info = task_content['error'].lstrip('error:')
            if not error_info:
                error_info = '内部异常，456'
            self.task.create_log(
                '导出备份点:{} 失败，{}'.format(self.task.host_snapshot_name, error_info), '')
            successful = False
        else:
            self.task.set_status(ArchiveTask.MISSION_SUCCESSFUL, '', False)
            self.task.create_log('导出备份点:{} 成功'.format(self.task.host_snapshot_name), '')
            successful = True

        for sub_task in self.task.sub_tasks.all():
            sub_task.finish_datetime = timezone.now()
            sub_task.successful = successful
            sub_task.save(update_fields=['finish_datetime', 'successful'])

        self.task.finish_datetime = timezone.now()
        self.task.successful = successful
        self.task.running_task = '{}'
        self.task.save(update_fields=['finish_datetime', 'successful', 'running_task'])

        self._unlock_snapshots()
        self._remove_tmp_file()
        if not successful:
            self._remove_task_dir()

        return None

    @xlogging.convert_exception_to_value(None)
    def _unlock_snapshots(self):
        Snapshot = namedtuple('Snapshot', ['path', 'snapshot'])
        snapshot_objs = [Snapshot(path=snapshot['path'], snapshot=snapshot['ident']) for snapshot in
                         self.task_content['locked_snapshots']]
        SendTaskWork.unlock_snapshots_u(snapshot_objs, self.task_content['lock_name'])

    @xlogging.convert_exception_to_value(None)
    def _remove_task_dir(self):
        shutil.rmtree(self.task_content['task_dir'])

    @xlogging.convert_exception_to_value(None)
    def _remove_tmp_file(self):
        for file in self.task_content['tmp_files']:
            boxService.box_service.remove(file)


# 解析出来meta data
class IPTSNInit(task.Task, WorkerLog):
    default_provides = 'task_content'

    def __init__(self, task_id, user_id, inject=None):
        super(IPTSNInit, self).__init__('IPTSNInit_{}'.format(task_id), inject=inject)
        self.task_id = task_id
        self.user_id = user_id
        self.task_content = None
        self.task = None

    def _get_file_uuid(self, ext_config):
        file_media_infos = json.loads(ext_config)['file_media_infos']
        media_uuid = json.loads(ext_config)['media_uuid']
        for key, value in file_media_infos.items():
            file_info = value
            break
        return {'file_info': file_info, 'media_uuid': media_uuid, 'file_media_infos': file_media_infos}

    def _get_meta_data_by_file_uuid(self, file_uuid):
        meta_data_json_str = boxService.box_service.get_archive_file_meta_data(json.dumps({'file_uuid': file_uuid}))
        meta_data = json.loads(meta_data_json_str)
        return meta_data

    def _get_meta_data_list(self, meta_data, meta_data_list, schedule_id):
        meta_data_list.append(meta_data)
        for disk_info in meta_data['disk_info']:
            if disk_info['parent_ident']:
                archive_task_obj = ArchiveSubTask.objects.get(ident=disk_info['parent_ident']).main_task
                file_uuid = self._get_file_uuid(archive_task_obj.ext_config)
                meta_data = self._get_meta_data_by_file_uuid(file_uuid)
                meta_data['file_uuid'] = file_uuid
                self._get_meta_data_list(meta_data, meta_data_list, schedule_id)
                break

    def _create_host(self, export_host):
        host_ident = export_host.archive_uuid
        host = Host.objects.filter(ident=host_ident)
        if host:
            host = Host.objects.get(ident=host_ident)
            if host.is_deleted:
                host.set_delete(False)
            return host
        host = Host.objects.create(ident=host_ident, display_name=export_host.display_name, user=export_host.user,
                                   type=Host.ARCHIVE_AGENT, archive_uuid=host_ident)
        return host

    def _create_host_snapshot(self, host, archive_task_obj):
        if archive_task_obj.snapshot_datetime:
            start_datetime = archive_task_obj.snapshot_datetime
        else:
            start_datetime = archive_task_obj.host_snapshot.start_datetime
        host_snapshot = HostSnapshot.objects.create(host=host, successful=False, start_datetime=start_datetime)
        return host_snapshot

    def _get_meta_data(self):
        self.task = ImportSnapshotTask.objects.get(id=self.task_id)
        local_task_uuid = self.task.source.local_task_uuid
        archive_task_obj = ArchiveTask.objects.get(task_uuid=local_task_uuid)
        host = self._create_host(archive_task_obj.host_snapshot.host)
        host_snapshot = self._create_host_snapshot(host, archive_task_obj)
        ImportSnapshotTask.objects.filter(id=self.task_id).update(host_snapshot=host_snapshot)
        self.task_content['host_snapshot_id'] = host_snapshot.id
        self.task.set_init_status(host, ImportSnapshotTask.TRANSFER_WAIT)
        self.task_content['archive_task_id'] = archive_task_obj.id
        file_uuid = self._get_file_uuid(archive_task_obj.ext_config)
        meta_data = self._get_meta_data_by_file_uuid(file_uuid)
        meta_data['file_uuid'] = file_uuid
        meta_data_list = list()
        self._get_meta_data_list(meta_data, meta_data_list, archive_task_obj.schedule_id)
        meta_data_list.reverse()
        debug = True
        if debug == True:
            for meta_data in meta_data_list:
                self.log_info('_get_meta_data disk_info={}'.format(meta_data['disk_info']))
        return meta_data_list

    def execute(self, **kwargs):
        self.task_content = {
            'error': '',
            'user_id': self.user_id,
        }
        try:
            self.task_content['meta_data_list'] = self._get_meta_data()
        except Exception as e:
            self.log_error(e)
            self.task_content['error'] = '{}'.format(e)
        return self.task_content


class IPTSNCreateData(task.Task, WorkerLog):
    default_provides = 'task_content'

    def __init__(self, task_id, storage_path, inject=None):
        super(IPTSNCreateData, self).__init__('IPTSNCreateData_{}'.format(task_id), inject=inject)
        self.task_id = task_id
        self.task_content = None
        self.storage_path = storage_path

    def _update_host(self):
        meta_data_list = self.task_content['meta_data_list']
        current_meta_data = meta_data_list[-1]['database']
        host_meta_data = current_meta_data['host']['fields']
        host_ident = host_meta_data['archive_uuid']
        host = Host.objects.filter(ident=host_ident)
        host.update(display_name=host_meta_data['display_name'], ext_info=host_meta_data['ext_info'])
        host = Host.objects.get(ident=host_ident)
        return host

    def _update_host_snapshot(self):
        meta_data_list = self.task_content['meta_data_list']
        current_meta_data = meta_data_list[-1]['database']
        host_snapshot_meta_data = current_meta_data['host_snapshot']['fields']
        start_datetime = xdatetime.string2datetime(meta_data_list[-1]['host_snapshot_start_datetime'])
        finish_datetime = xdatetime.string2datetime(meta_data_list[-1]['host_snapshot_finish_datetime'])
        host_snapshot = HostSnapshot.objects.filter(id=self.task_content['host_snapshot_id'])
        host_snapshot.update(start_datetime=start_datetime,
                             finish_datetime=finish_datetime,
                             successful=False,
                             ext_info=host_snapshot_meta_data['ext_info'],
                             schedule=None, cluster_schedule=None)
        host_snapshot = HostSnapshot.objects.get(id=self.task_content['host_snapshot_id'])
        return host_snapshot

    def _get_disk_snapshots_meta_data(self, disk_snapshots_list_meta_data, disk_snapshot):
        for disk_snapshots_meta_data in disk_snapshots_list_meta_data:
            if disk_snapshots_meta_data['fields']['ident'] == disk_snapshot:
                return disk_snapshots_meta_data['fields']
        self.log_error(
            '_disk_snapshots_meta_data find Failed.disk_snapshot={},disk_snapshots_list_meta_data={}'.format(
                disk_snapshot, disk_snapshots_list_meta_data))
        return None

    def _create_disk_snapshot(self, host_snapshot):
        qcow_file_list = list()
        meta_data_list = self.task_content['meta_data_list']
        current_meta_data = meta_data_list[-1]['database']
        current_disk_info_meta_data = meta_data_list[-1]['disk_info']
        disk_snapshots_list_meta_data = current_meta_data['disk_snapshots']
        host_meta_data = current_meta_data['host']['fields']
        host_ident = host_meta_data['archive_uuid']
        replace_map = list()
        for disk_info in current_disk_info_meta_data:
            disk_ident = disk_info['disk_ident']
            disk_snapshot_ident = uuid.uuid4().hex
            replace_map.append({'src': disk_info['disk_snapshot'], 'dest': disk_snapshot_ident})
            disk, _ = Disk.objects.get_or_create(ident=disk_ident)
            image_path = os.path.join(self.storage_path, 'images/{}'.format(host_ident))
            os.makedirs(image_path, exist_ok=True)
            image_path = os.path.join(image_path, '{}.qcow'.format(uuid.uuid4().hex))

            native_guid = disk_info['native_guid']

            disk_snapshots_meta_data = self._get_disk_snapshots_meta_data(disk_snapshots_list_meta_data,
                                                                          disk_info['disk_snapshot'])
            qcow_file_list.append(
                {'native_guid': native_guid, 'image_path': image_path, 'disk_snapshot_ident': disk_snapshot_ident,
                 'bytes': disk_snapshots_meta_data['bytes']})
            DiskSnapshot.objects.create(disk=disk, display_name=disk_snapshots_meta_data['display_name'],
                                        parent_snapshot=None, image_path=image_path, ident=disk_snapshot_ident,
                                        host_snapshot=host_snapshot, bytes=disk_snapshots_meta_data['bytes'],
                                        type=disk_snapshots_meta_data['type'], boot_device
                                        =disk_snapshots_meta_data['boot_device'],
                                        ext_info=disk_snapshots_meta_data['ext_info'])
        return qcow_file_list, replace_map

    def execute(self, task_content, **kwargs):
        self.task_content = task_content
        if FlowEntrance.has_error(task_content):
            return task_content
        try:
            self._update_host()
            host_snapshot = self._update_host_snapshot()
            self.task_content['host_snapshot_id'] = host_snapshot.id
            self.task_content['qcow_file_list'], replace_maps = self._create_disk_snapshot(host_snapshot)

            ext_info = host_snapshot.ext_info
            for replace_map in replace_maps:
                ext_info = ext_info.replace(replace_map['src'], replace_map['dest'])
            host_snapshot.ext_info = ext_info
            host_snapshot.save(update_fields=['ext_info'])
            import_snapshot_task = ImportSnapshotTask.objects.get(id=self.task_id)
            import_snapshot_task.create_log(
                '开始导入备份点:{} {}'.format(import_snapshot_task.host_snapshot.host.name,
                                       import_snapshot_task.host_snapshot.name), '')
            import_snapshot_task.set_status(ImportSnapshotTask.INIT, self.task_content['error'])
        except Exception as e:
            self.log_error(e)
            self.task_content['error'] = '{}'.format(e)
        return self.task_content


class IPTSNGenerateQcow(task.Task, WorkerLog):
    default_provides = 'task_content'

    def __init__(self, task_id, inject=None):
        super(IPTSNGenerateQcow, self).__init__('IPTSNGenerateQcow_{}'.format(task_id), inject=inject)
        self.task_id = task_id
        self.task_content = None
        self.task = None

    def _get_path_and_disk_snapshot_id_by_native_guid(self, qcow_file_list, native_guid):
        for qcow_file in qcow_file_list:
            if qcow_file['native_guid'] == native_guid:
                return qcow_file['image_path'], qcow_file['disk_snapshot_ident'], qcow_file['bytes']
        self.log_error_and_raise_exception(
            '_get_path_and_disk_snapshot_id_by_native_guid Failed.native_guid={,qcow_file_list={}'.format(
                native_guid,
                qcow_file_list))
        return None, None

    def _gen_qcow_file_parameter(self):
        qcow_file_list = self.task_content['qcow_file_list']
        meta_data_list = self.task_content['meta_data_list']
        current_meta_data = meta_data_list[-1]
        qcow_file_parameter = dict()
        source_data = list()
        dst = list()
        need_native_guid_list = list()

        for disk_info in current_meta_data['disk_info']:
            native_guid = disk_info['native_guid']
            path, snapshot, bytes = self._get_path_and_disk_snapshot_id_by_native_guid(qcow_file_list, native_guid)
            need_native_guid_list.append(native_guid)
            dst.append({'native_guid': native_guid, 'path': path, 'snapshot': snapshot, 'bytes': bytes})

        qcow_file_parameter['dst'] = dst

        for meta_data in meta_data_list:
            disk_info = list()
            for one_disk_info in meta_data['disk_info']:
                native_guid = one_disk_info['native_guid']
                if native_guid not in need_native_guid_list:
                    continue
                disk_info.append(
                    {'disk_index': one_disk_info['disk_index'], 'native_guid': native_guid,
                     'blocks': one_disk_info['blocks']})
            if len(disk_info) == 0:
                continue
            source_data.append(
                {'disk_info': disk_info, 'file_media_infos': meta_data['file_uuid']['file_media_infos'],
                 'media_uuid': meta_data['file_uuid']['media_uuid']})

        qcow_file_parameter['source_data'] = source_data

        return qcow_file_parameter

    def _gen_qcow_file(self, qcow_file_parameter):
        boxService.box_service.gen_archive_qcow_file(
            json.dumps({'task_id': self.task_id, 'qcow_file_parameter': qcow_file_parameter}))

    def execute(self, task_content, **kwargs):
        self.task_content = task_content
        if FlowEntrance.has_error(task_content):
            return task_content
        try:
            qcow_file_parameter = self._gen_qcow_file_parameter()
            self.task = ImportSnapshotTask.objects.get(id=self.task_id)
            self.task.set_status(ImportSnapshotTask.TRANSFER_DATA, self.task_content['error'])
            self._gen_qcow_file(qcow_file_parameter)
        except Exception as e:
            self.log_error(e)
            self.task_content['error'] = '{}'.format(e)
        return self.task_content


class IPTSNFinisTask(task.Task, WorkerLog):
    def __init__(self, task_id, inject=None):
        super(IPTSNFinisTask, self).__init__('IPTSNFinisTask_{}'.format(task_id), inject=inject)
        self.task_id = task_id
        self.task_content = None
        self.task = None

    def execute(self, task_content, **kwargs):
        self.task_content = task_content
        try:
            self.task = ImportSnapshotTask.objects.get(id=self.task_id)
            ImportSnapshotTask.objects.filter(id=self.task_id).update(finish_datetime=timezone.now())
            if not FlowEntrance.has_error(task_content):
                HostSnapshot.objects.filter(id=self.task_content['host_snapshot_id']).update(successful=True)
                ArchiveTask.objects.filter(id=self.task_content['archive_task_id']).update(successful=True)
                ImportSnapshotTask.objects.filter(id=self.task_id).update(successful=True)
                self.task.set_status(ImportSnapshotTask.MISSION_SUCCESSFUL, self.task_content['error'])
            else:
                self.log_error('IPTSNFinisTask execute Failed.error={}'.format(self.task_content['error']))
                ImportSnapshotTask.objects.filter(id=self.task_id).update(successful=False)
                try:
                    self.task.set_status(ImportSnapshotTask.MISSION_FAIL, self.task_content['error'])
                except Exception as e:
                    # IPTSNCreateData失败了，记录不了日志，ignore
                    pass
        except Exception as e:
            self.log_error(e)

        return None


_book_ids = list()
_book_id_locker = threading.Lock()


class FlowEntrance(threading.Thread):
    def __init__(self, task_id, name, flow_func, user_id=None, storage_path=None):
        super(FlowEntrance, self).__init__()
        self.name = name
        self._engine = None
        self._book_uuid = None
        self.task_id = task_id
        self._flow_func = flow_func
        self._user_id = user_id
        self._storage_path = storage_path

    def load_from_uuid(self, task_uuid):
        backend = task_backend.get_backend()
        with contextlib.closing(backend.get_connection()) as conn:
            book = conn.get_logbook(task_uuid['book_id'])
            flow_detail = book.find(task_uuid['flow_id'])
        self._engine = engines.load_from_detail(flow_detail, backend=backend, engine='serial')
        self.name += r' load exist uuid {} {}'.format(task_uuid['book_id'], task_uuid['flow_id'])
        self._book_uuid = book.uuid

    def generate_uuid(self):
        backend = task_backend.get_backend()
        book = models.LogBook(
            r"{}_{}".format(self.name, datetime.datetime.now().strftime(xdatetime.FORMAT_WITH_SECOND_FOR_PATH)))
        with contextlib.closing(backend.get_connection()) as conn:
            conn.save_logbook(book)

        try:
            self._engine = engines.load_from_factory(self._flow_func, backend=backend, book=book, engine='serial',
                                                     factory_args=(
                                                         self.name, self.task_id, self._user_id,
                                                         self._storage_path))

            self._book_uuid = book.uuid
            return {'book_id': book.uuid, 'flow_id': self._engine.storage.flow_uuid}
        except Exception as e:
            _logger.error(r'generate_uuid failed {}'.format(e), exc_info=True)
            with contextlib.closing(backend.get_connection()) as conn:
                conn.destroy_logbook(book.uuid)
            raise e

    def start(self):
        if self._engine:
            super().start()
        else:
            xlogging.raise_and_logging_error('内部异常，无效的调用', r'start without _engine ：{}'.format(self.name),
                                             status.HTTP_501_NOT_IMPLEMENTED)

    def run(self):
        with _book_id_locker:
            if self._book_uuid in _book_ids:
                # 重复运行
                _logger.warning('FlowEntrance book uuid:{} already run'.format(self._book_uuid))
                return
            else:
                _book_ids.append(self._book_uuid)
        _logger.info('FlowEntrance _book_ids:{}'.format(_book_ids))
        try:
            with logging_listener.DynamicLoggingListener(self._engine):
                self._engine.run()
        except Exception as e:
            _logger.error(r'FlowEntrance run engine {} failed {}'.format(self.name, e), exc_info=True)
        finally:
            with contextlib.closing(task_backend.get_backend().get_connection()) as conn:
                conn.destroy_logbook(self._book_uuid)
            with _book_id_locker:
                _book_ids.remove(self._book_uuid)
        self._engine = None

    @staticmethod
    def has_error(task_content):
        return task_content['error']


def del_import_task(task_id):
    from .spaceCollection import SpaceCollectionWorker
    import_snapshot_task = ImportSnapshotTask.objects.get(id=task_id)
    if import_snapshot_task.status not in ([ImportSnapshotTask.MISSION_SUCCESSFUL, ImportSnapshotTask.MISSION_FAIL]):
        return False
    host_snapshot_object = import_snapshot_task.host_snapshot
    SpaceCollectionWorker.create_normal_host_snapshot_delete_task(host_snapshot_object)
    ImportSnapshotTask.objects.filter(id=task_id).delete()
    return True


def create_exp_flow(name, task_id, user_id, storage_path):
    flow = lf.Flow(name).add(
        EXPSNInit(task_id),  # 锁定快照, 创建子任务
        EXPSNPackagingDateBase(task_id),  # 打包数据库对象（host, host_snapshot, disk_snapshot）
        EXPSNGenerateHash(task_id),  # 生成磁盘hash数据
        EXPSNGenerateBitMap(task_id),  # 比较2份hash文件，生成增量的位图
        EXPSNExportData(task_id),  # 导出数据
        EXPSNFinisTask(task_id),
    )
    return flow


def create_imp_flow(name, task_id, user_id, storage_path):
    flow = lf.Flow(name).add(
        IPTSNInit(task_id, user_id),  # 解析数据，获取meta data
        IPTSNCreateData(task_id, storage_path),  # 创建数据库对象（host, host_snapshot, disk_snapshot）
        IPTSNGenerateQcow(task_id),  # 导入数据
        IPTSNFinisTask(task_id),
    )
    return flow
