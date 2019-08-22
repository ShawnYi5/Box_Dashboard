import datetime
import functools
import inspect
import json
import os
import queue
import threading
import time
import uuid
from functools import partial
from itertools import zip_longest
from threading import Lock, Thread

import django.utils.timezone as timezone
from rest_framework import status

from apiv1.compress import CompressTaskThreading
from apiv1.models import DiskSnapshotCDP, HostLog, CDPDiskToken, DiskSnapshot, BackupTaskSchedule, ClusterBackupSchedule
from apiv1.runCmpr.bitmap import BitMap
from apiv1.snapshot_bitmap import get_snapshot_inc_bitmap_V2
from apiv1.storage_nodes import UserQuotaTools
from apiv1.system_info_helper import get_agent_client_version
from box_dashboard import xlogging, boxService, xdatetime, xdata

_logger = xlogging.getLogger(__name__)

import IMG
import KTService
import Utils

_rehash_locker = Lock()
_rehash_disks = set()

_update_disk_snapshot_cdp_timestamp_queue = queue.Queue()


class UpdateDiskSnapshotCdpTimestamp(Thread):
    def __init__(self):
        super(UpdateDiskSnapshotCdpTimestamp, self).__init__(name='UpdateDiskSnapshotCdpTimestamp')

    def run(self):
        while True:
            try:
                disk_snapshot_cdp_object = _update_disk_snapshot_cdp_timestamp_queue.get()
                Tokens.update_disk_snapshot_cdp_timestamp(disk_snapshot_cdp_object)
            except Exception as e:
                _logger.error(r'UpdateDiskSnapshotCdpTimestamp failed {}'.format(e), exc_info=True)


class DiskSnapshotHash(object):
    @staticmethod
    def mergeHash2Children(disk_snapshot_object):
        hash_path = disk_snapshot_object.hash_path
        delete_hash_file = (not boxService.box_service.isFileExist(hash_path))

        for child_disk_snapshot in disk_snapshot_object.child_snapshots.filter(merged=False).all():
            if child_disk_snapshot.is_cdp:
                continue
            child_hash_path = child_disk_snapshot.hash_path
            if delete_hash_file:
                _logger.warning(r'mergeHash2Children can NOT find {}, delete {}'.format(hash_path, child_hash_path))
                boxService.box_service.remove(child_hash_path, False)
            elif boxService.box_service.isFileExist(child_hash_path):
                boxService.box_service.mergeHashFile(hash_path, child_hash_path, disk_snapshot_object.bytes)
            else:
                _logger.info(r'mergeHash2Children not find child hash file : {}'.format(child_hash_path))

    @staticmethod
    def reorganize_hash_file(host_snapshot):
        if DiskSnapshotHash.get_client_version_from_snapshot(host_snapshot) <= datetime.datetime(2018, 1, 12):
            _logger.warning('reorganize_hash_file found old client exit! {}'.format(host_snapshot))
            return

        disk_snapshots = host_snapshot.disk_snapshots.all()
        for disk_snapshot in disk_snapshots:
            DiskSnapshotHash.reorganize_hash_file_by_disk_snapshot(disk_snapshot)

    # 多线程调度
    @staticmethod
    def reorganize_hash_file_by_disk_snapshot(disk_snapshot):
        """
        :param disk_snapshot:
        :return: successful 整理成功
                 failed     整理失败（1. 调用出现异常 2. 老客户端[在2018, 1, 12之前]不计算hash）
                 working    正在整理中
        """
        global _rehash_disks
        with _rehash_locker:
            if disk_snapshot.ident in _rehash_disks:
                _logger.info('reorganize_hash_file_by_disk_snapshot disk_snapshot:{} is reorganizeing! return'.format(
                    disk_snapshot))
                return 'working'
            else:
                _logger.info('reorganize_hash_file_by_disk_snapshot disk_snapshot:{} is not reorganize, '
                             'will be reorganize'.format(disk_snapshot))
                _rehash_disks.add(disk_snapshot.ident)
        try:
            disk_snapshot = DiskSnapshot.objects.get(ident=disk_snapshot.ident)
            return DiskSnapshotHash._reorganize_hash_file_by_disk_snapshot_worker(disk_snapshot)
        except DiskSnapshot.DoesNotExist:
            _logger.error(r'reorganize_hash_file_by_disk_snapshot failed {}'.format(disk_snapshot.ident))
            raise
        finally:
            with _rehash_locker:
                _rehash_disks.remove(disk_snapshot.ident)

    # 整理hash 文件
    @staticmethod
    def _reorganize_hash_file_by_disk_snapshot_worker(disk_snapshot):
        # 如果是老的客户端（在2018, 1, 12之前），不整理
        host_snapshot = GetSnapshotList.get_host_snapshot_by_disk_snapshot(disk_snapshot)
        if DiskSnapshotHash.get_client_version_from_snapshot(host_snapshot) <= datetime.datetime(2018, 1, 12):
            _logger.warning('reorganize_hash_file_by_disk_snapshot found old client exit! {}'.format(disk_snapshot))
            disk_snapshot.reorganized_hash = True
            disk_snapshot.save(update_fields=['reorganized_hash'])
            return 'failed'
        else:
            if disk_snapshot.reorganized_hash:
                _logger.warning(
                    'reorganize_hash_file_by_disk_snapshot already reorganized exit! {}'.format(disk_snapshot))
                return 'successful'

        validator_list = [GetSnapshotList.is_disk_snapshot_object_exist,
                          GetSnapshotList.is_disk_snapshot_file_exist
                          ]
        hash_path_backup, hash_path = '{}_backup'.format(disk_snapshot.hash_path), disk_snapshot.hash_path
        boxService.box_service.remove(hash_path_backup)
        # 不存在源文件，则需要重新生成
        if not boxService.box_service.isFileExist(disk_snapshot.hash_path):
            hash_path_backup, hash_path = '', disk_snapshot.hash_path
        else:
            boxService.box_service.move(hash_path_backup, hash_path)

        try:
            flag = r'PiD{:x} BoxDashboard|reorganize hash file{}'.format(os.getpid(),
                                                                         disk_snapshot.ident)
            bit_map = SnapshotsUsedBitMapGeneric(
                [IMG.ImageSnapshotIdent(disk_snapshot.image_path, disk_snapshot.ident)],
                flag).get()
            if bit_map and sum(bit_map):
                ice_snapshots = GetSnapshotList.query_snapshots_by_snapshot_object(
                    disk_snapshot, validator_list)
                json_params = {
                    'hash_file_tmp': hash_path_backup,
                    'hash_file': hash_path,
                    'snapshots': [{'path': ice_snapshot.path, 'ident': ice_snapshot.snapshot} for ice_snapshot in
                                  ice_snapshots],
                    'disk_bytes': disk_snapshot.bytes
                }
                boxService.box_service.reorganizeHashFile(bit_map, json.dumps(json_params))
            else:
                _logger.info('reorganize_hash_file_by_disk_snapshot disk_snapshot is empty, gen empty hash'.format(
                    disk_snapshot))
                boxService.box_service.remove(hash_path)
                boxService.box_service.runCmd('touch {}'.format(hash_path))
        except Exception as e:
            _logger.error('reorganizeHashFile fail:{}'.format(e), exc_info=True)
            boxService.box_service.remove(hash_path)
            return 'failed'
        else:
            disk_snapshot.reorganized_hash = True
            disk_snapshot.save(update_fields=['reorganized_hash'])
            return 'successful'
        finally:
            if boxService.box_service.isFileExist(xdata.SAVE_SRC_HASH):
                pass
            else:
                boxService.box_service.remove(hash_path_backup)

    @staticmethod
    @xlogging.convert_exception_to_value(datetime.datetime(2000, 1, 1))
    def get_client_version_from_snapshot(host_snapshot):
        ext_info = json.loads(host_snapshot.ext_info)
        system_info_obj = ext_info['system_infos']
        return get_agent_client_version(system_info_obj)


class GetDiskSnapshot(object):
    @staticmethod
    def query_normal_disk_snapshot_object(host_snapshot_object, disk_ident):
        try:
            result = DiskSnapshot.objects.filter(host_snapshot=host_snapshot_object, disk__ident=disk_ident).order_by(
                'id').first()
            if result is None:
                _logger.error(
                    r'GetDiskSnapshot query_normal_disk_snapshot_object failed. '
                    r'host_snapshot_object.id:{} disk_ident:{}'.format(host_snapshot_object.id, disk_ident)
                )
            return result
        except Exception as e:
            _logger.error(
                r'GetDiskSnapshot query_normal_disk_snapshot_object failed. host_snapshot_object.id:{} disk_ident:{}'
                r' e:{}'.format(host_snapshot_object.id, disk_ident, e))
            return None

    @staticmethod
    def query_normal_disk_snapshot_ident(host_snapshot_object, disk_ident):
        normal_disk_snapshot_object = \
            GetDiskSnapshot.query_normal_disk_snapshot_object(host_snapshot_object, disk_ident)
        if normal_disk_snapshot_object:
            return normal_disk_snapshot_object.ident
        else:
            _logger.error(r'GetDiskSnapshot query_normal_disk_snapshot_ident failed. look forward log')
            return None

    @staticmethod
    def get_snapshot_timestamp(path, restore_time):
        try:
            return boxService.box_service.queryCdpTimestamp(path, restore_time)
        except xlogging.BoxDashboardException:
            pass  # do nothing
        except Utils.SystemError:
            pass  # do nothing
        _logger.warning(r'cdp file {} need fix restore time {}'.format(path, restore_time))
        start_timestamp, end_timestamp = boxService.box_service.queryCdpTimestampRange(path)
        if start_timestamp is None:
            _logger.error('boxService.queryCdpTimestampRange error None not find {}'.format(restore_time))
        else:
            if restore_time < start_timestamp:
                return start_timestamp
            elif restore_time > end_timestamp:
                return end_timestamp
            else:
                _logger.error('boxService.queryCdpTimestampRange error {}-{} not find {}'
                              .format(start_timestamp, end_timestamp, restore_time))
        return None

    @staticmethod
    def query_child_cdp_disk_snapshot(disk_snapshot_object):
        assert disk_snapshot_object.child_snapshots.filter(
            parent_timestamp__isnull=True, merged=False, host_snapshot__isnull=True).count() in (0, 1,)

        result = disk_snapshot_object.child_snapshots.filter(
            parent_timestamp__isnull=True, merged=False, host_snapshot__isnull=True).first()

        assert (result is None) or result.is_cdp

        return result

    @staticmethod
    def query_cdp_disk_snapshot_ident(host_snapshot_object, disk_ident, restore_time):
        try:
            normal_disk_snapshot_object = \
                GetDiskSnapshot.query_normal_disk_snapshot_object(host_snapshot_object, disk_ident)

            if normal_disk_snapshot_object is None:
                _logger.error('can NOT find snapshot. look forward log')
                return None, None

            return GetDiskSnapshot.query_cdp_disk_snapshot_ident_by_normal_disk_snapshot(
                normal_disk_snapshot_object, restore_time)
        except Exception as e:
            _logger.error(
                r'GetDiskSnapshot query_cdp_disk_snapshot_ident failed. id:{} ident:{} time:{} '
                r'e:{}'.format(host_snapshot_object.id, disk_ident, restore_time, e), exc_info=True)
            return None, None

    @staticmethod
    def query_cdp_disk_snapshot_ident_by_normal_disk_snapshot(normal_disk_snapshot_object, restore_time):
        _logger.info(r'query_cdp_disk_snapshot_ident_by_normal_disk_snapshot begin query {} {}'.format(
            normal_disk_snapshot_object.ident, restore_time))

        prev_cdp_disk_snapshot_object = None
        current_cdp_disk_snapshot_object = xlogging.DataHolder()
        current_cdp_disk_snapshot_object.set(normal_disk_snapshot_object)

        while current_cdp_disk_snapshot_object.set(
                GetDiskSnapshot.query_child_cdp_disk_snapshot(current_cdp_disk_snapshot_object.get())) is not None:
            if restore_time < current_cdp_disk_snapshot_object.get().cdp_info.first_timestamp:
                # 使用前一个快照文件
                current_cdp_disk_snapshot_object.set(prev_cdp_disk_snapshot_object)
                break

            if (current_cdp_disk_snapshot_object.get().cdp_info.last_timestamp is None) or \
                    (current_cdp_disk_snapshot_object.get().cdp_info.first_timestamp <= restore_time
                     <= current_cdp_disk_snapshot_object.get().cdp_info.last_timestamp):
                start_timestamp, end_timestamp = boxService.box_service.queryCdpTimestampRange(
                    current_cdp_disk_snapshot_object.get().image_path)
                if restore_time < start_timestamp:
                    # 使用前一个快照文件
                    current_cdp_disk_snapshot_object.set(prev_cdp_disk_snapshot_object)
                    break
                elif (current_cdp_disk_snapshot_object.get().cdp_info.last_timestamp is not None) and \
                        (restore_time <= end_timestamp):
                    # 使用本快照文件
                    break

            prev_cdp_disk_snapshot_object = current_cdp_disk_snapshot_object.get()

        if current_cdp_disk_snapshot_object.get() is None:
            if prev_cdp_disk_snapshot_object is None:
                _logger.error(r'query_cdp_disk_snapshot_ident_by_normal_disk_snapshot no cdp_disk_snapshot_object {} {}'
                              .format(normal_disk_snapshot_object.ident, restore_time))
                return None, None
            else:
                _logger.warning(
                    r'query_cdp_disk_snapshot_ident_by_normal_disk_snapshot use prev_cdp_disk_snapshot_object '
                    r'{} {}'.format(normal_disk_snapshot_object.ident, restore_time))
                current_cdp_disk_snapshot_object.set(prev_cdp_disk_snapshot_object)
        restore_timestamp = GetDiskSnapshot.get_snapshot_timestamp(
            current_cdp_disk_snapshot_object.get().image_path, restore_time)
        if restore_timestamp is None:
            _logger.warning(
                r'query_cdp_disk_snapshot_ident_by_normal_disk_snapshot can not get_snapshot_timestamp {} {}'.format(
                    normal_disk_snapshot_object.ident, restore_time))
            return None, None
        _logger.info(r'query_cdp_disk_snapshot_ident_by_normal_disk_snapshot ok {} {} - {} {}'.format(
            normal_disk_snapshot_object.ident, restore_time, current_cdp_disk_snapshot_object.get().ident,
            restore_timestamp))
        return current_cdp_disk_snapshot_object.get().ident, restore_timestamp

    @staticmethod
    def get_by_native_guid_and_host_snapshot(native_guid, host_snapshot):
        info = json.loads(host_snapshot.ext_info)
        for disk_info in info['include_ranges']:
            if disk_info['diskNativeGUID'] == native_guid:
                break
        else:
            return None
        return host_snapshot.disk_snapshots.filter(disk__ident=disk_info['diskIdent']).first()


class GetSnapshotList(object):
    @staticmethod
    def fetch_snapshots_and_hash_files(disk_snapshot, time_stamp):
        validator_list = [GetSnapshotList.is_disk_snapshot_object_exist,
                          GetSnapshotList.is_disk_snapshot_file_exist,
                          GetSnapshotList.is_disk_snapshot_object_finished,
                          GetSnapshotList.fix_disk_snapshot_hash_file
                          ]
        # 备份时候，最后一个点是cdp且即将删除不要依赖 #3274
        validator_list_first = [GetSnapshotList.is_schedule_valid,
                                GetSnapshotList.is_cdp_valid]
        snapshots, hash_files = GetSnapshotList.query_snapshots_by_snapshot_object_with_hash_file(
            disk_snapshot, validator_list, validator_list_first, time_stamp)
        if not snapshots:
            xlogging.raise_and_logging_error('无效的快照文件', 'fetch_snapshots fail:{}|{}'.format(disk_snapshot, time_stamp),
                                             11132)
        hash_files.reverse()
        return snapshots, hash_files

    @staticmethod
    def _is_backuping_qcow(snapshot_ident):
        snapshot_object = DiskSnapshot.objects.get(ident=snapshot_ident)

        if snapshot_object.is_cdp:
            return False

        if snapshot_object.host_snapshot is None:
            _logger.warning(r'_is_backuping_qcow find no host_snapshot {} first time'.format(snapshot_object.ident))
            time.sleep(1)
            snapshot_object = DiskSnapshot.objects.get(ident=snapshot_ident)
            if snapshot_object.host_snapshot is None:
                _logger.warning(
                    r'_is_backuping_qcow find no host_snapshot {} second time'.format(snapshot_object.ident))
                return False

        if snapshot_object.host_snapshot.finish_datetime is None:
            _logger.warning(r'GetSnapshotList find backuping : {} in {}'
                            .format(snapshot_object.ident, snapshot_object.image_path))
            return True
        else:
            return False

    @staticmethod
    def _wrap_validator_list(funcs, query_cache):
        """
        :param funcs: need wrpa funcs
        :param query_cache: cache object
        :return: new funcs after warp
        不再单个的查看qcow文件及hash文件是否存在
        对于主机快照的相同的磁盘快照，进行缓存
        """
        if not funcs:
            return funcs
        nfuncs = list()
        for func in funcs:
            if func == GetSnapshotList.is_disk_snapshot_file_exist:
                nfuncs.append(GetSnapshotList.is_disk_snapshot_file_exist_v2)
            elif func == GetSnapshotList.is_disk_snapshot_hash_file_exists:
                continue
            elif 'query_cache' in inspect.signature(func).parameters.keys():
                nfuncs.append(partial(func, query_cache=query_cache))
            else:
                nfuncs.append(func)
        return nfuncs

    @staticmethod
    def query_snapshots_by_snapshot_object(snapshot_object, validator_list, timestamp=None, validator_list_first=None,
                                           include_all_node=False):
        result = list()
        key_snapshot_objects = list()
        query_cache = dict()
        validator_list_v2 = GetSnapshotList._wrap_validator_list(validator_list, query_cache)
        validator_list_first_v2 = GetSnapshotList._wrap_validator_list(validator_list_first, query_cache)

        current_snapshot = snapshot_object
        while current_snapshot is not None:
            first_node = (len(result) == 0)

            if include_all_node:
                key_node = True
            elif first_node:  # 从后向前的第一个节点，为关键节点
                key_node = True
            elif current_snapshot.is_base_snapshot:  # 基础备份，为关键节点
                key_node = True
            elif current_snapshot.image_path != current_snapshot.parent_snapshot.image_path:  # 当前节点与父节点的文件路径不同，为关键节点
                key_node = True
            elif current_snapshot.image_path != result[-1].path:  # 当前节点与上一个关键节点文件路径，为关键节点
                key_node = True
            elif GetSnapshotList._is_backuping_qcow(result[-1].snapshot):  # 下一个节点为正在备份中的普通快照点
                key_node = True
            else:
                key_node = False

            if key_node:
                if validator_list is not None:
                    for validator in validator_list_v2:
                        if not validator(current_snapshot):  # 只要一个校验器返回false，就表明快照点链无效，重新做基础备份
                            _logger.warning(
                                r'query_snapshots_by_snapshot_object invalid. {}'.format(snapshot_object.ident))
                            return []

                if validator_list_first is not None and first_node:
                    for validator in validator_list_first_v2:
                        if not validator(current_snapshot):  # 只要一个校验器返回false，就表明快照点链无效，重新做基础备份
                            _logger.warning(
                                r'query_snapshots_by_snapshot_object invalid first node. {}'.format(
                                    snapshot_object.ident))
                            return []

                if current_snapshot.is_cdp:
                    if first_node:  # 如果是第一个节点，使用外部传入的时间点
                        if not GetSnapshotList.is_cdp_timestamp_exist(current_snapshot.image_path, timestamp):
                            _logger.warning(r'query_snapshots_by_snapshot_object cdp timestamp invalid.')
                            return []

                        result.append(
                            IMG.ImageSnapshotIdent(current_snapshot.image_path,
                                                   GetSnapshotList.format_timestamp(None, timestamp)))
                    else:  # 使用前一个节点记录的时间点
                        if not GetSnapshotList.is_cdp_timestamp_exist(current_snapshot.image_path,
                                                                      key_snapshot_objects[-1].parent_timestamp):
                            _logger.warning(r'query_snapshots_by_snapshot_object cdp timestamp invalid.')
                            return []

                        result.append(
                            IMG.ImageSnapshotIdent(
                                current_snapshot.image_path,
                                GetSnapshotList.format_timestamp(None, key_snapshot_objects[-1].parent_timestamp)))
                else:
                    result.append(IMG.ImageSnapshotIdent(current_snapshot.image_path, current_snapshot.ident))

                key_snapshot_objects.append(current_snapshot)
            else:
                pass  # do nothing

            current_snapshot = current_snapshot.parent_snapshot

        if len(result) > 0 and boxService.box_service.AllFilesExist(list({snapshot.path for snapshot in result})):
            result.reverse()
        else:
            result = list()
        return result

    @staticmethod
    def query_snapshots_by_snapshot_object_with_hash_file(snapshot_object, validator_list,
                                                          validator_list_first=None, timestamp=None,
                                                          include_all_node=False):
        result = list()
        key_snapshot_objects = list()
        hash_files = list()
        query_cache = dict()
        validator_list_v2 = GetSnapshotList._wrap_validator_list(validator_list, query_cache)
        validator_list_first_v2 = GetSnapshotList._wrap_validator_list(validator_list_first, query_cache)

        current_snapshot = snapshot_object
        while current_snapshot:
            first_node = (len(result) == 0)

            if validator_list_first_v2 is not None and first_node:
                for validator in validator_list_first_v2:
                    if not validator(current_snapshot):  # 只要一个校验器返回false，就表明快照点链无效，重新做基础备份
                        _logger.warning(
                            r'query_snapshots_by_snapshot_object_inc_all_with_hash_file invalid first node. {}'.format(
                                snapshot_object.ident))
                        return list(), list()

            for validator in validator_list_v2:
                if not validator(current_snapshot):
                    _logger.warning(
                        r'query_snapshots_by_snapshot_object_inc_all_with_hash_file invalid. {}'.format(
                            current_snapshot.ident))
                    return list(), list()

            if current_snapshot.is_cdp:
                if not first_node:  # 非第一个节点使用前一个节点记录的时间
                    timestamp = key_snapshot_objects[-1].parent_timestamp

                if not GetSnapshotList.is_cdp_timestamp_exist(current_snapshot.image_path,
                                                              timestamp):
                    _logger.warning(r'query_snapshots_by_snapshot_object_inc_all_with_hash_file cdp timestamp invalid.')
                    return list(), list()
                else:
                    path = current_snapshot.image_path
                    snapshot = GetSnapshotList.format_timestamp(None, timestamp)
                    result.append(IMG.ImageSnapshotIdent(path, snapshot))
                    hash_files.append('cdp|{}|{}'.format(path, snapshot))
            else:
                result.append(IMG.ImageSnapshotIdent(current_snapshot.image_path, current_snapshot.ident))
                hash_files.append(current_snapshot.hash_path)

            key_snapshot_objects.append(current_snapshot)
            current_snapshot = current_snapshot.parent_snapshot

        if result and boxService.box_service.AllFilesExist(list({snapshot.path for snapshot in result})):
            result.reverse()
        else:
            return list(), list()

        check_hash_files = [hash_file for hash_file in hash_files if not hash_file.startswith('cdp')]
        if check_hash_files and boxService.box_service.AllFilesExist(list(set(check_hash_files))):
            hash_files.reverse()
        else:
            return list(), list()

        return GetSnapshotList.clean_not_key_snapshots(result) if not include_all_node else result, hash_files

    # @staticmethod
    # def is_disk_snapshot_object_deleting(disk_snapshot_object):
    #     if disk_snapshot_object.host_snapshot.deleting:
    #         _logger.warning(r'disk_snapshot.host_snapshot.deleting : {}'.format(disk_snapshot_object.ident))
    #         return False
    #     else:
    #         return True

    @staticmethod
    def is_schedule_valid(disk_snapshot_object, query_cache=None):
        host_snapshot_object = GetSnapshotList.get_host_snapshot_by_disk_snapshot(disk_snapshot_object, query_cache)

        # 因为迁移没有schedule 所以要先判断有没有schedule
        if host_snapshot_object.schedule and host_snapshot_object.schedule.deleted:
            _logger.warning(r'find snapshots list host_snapshot_object.schedule.deleted : {} {}'
                            .format(disk_snapshot_object.ident, host_snapshot_object.schedule.id))
            return False
        else:
            return True

    @staticmethod
    def is_cdp_valid(disk_snapshot_object):
        if disk_snapshot_object.is_cdp and disk_snapshot_object.deleting:
            _logger.warning(r'is_cdp_valid deleting: {}'.format(disk_snapshot_object))
            return False
        else:
            return True

    @staticmethod
    def is_cdp_timestamp_exist(path, timestamp):
        if timestamp is None:
            return True
        return timestamp == boxService.box_service.queryCdpTimestamp(path, timestamp)

    @staticmethod
    def is_disk_snapshot_file_exist(disk_snapshot_object):
        if disk_snapshot_object.merged:
            _logger.warning(r'disk_snapshot.image_path file merged : {} {}'
                            .format(disk_snapshot_object.image_path, disk_snapshot_object.ident))
            return False
        if boxService.box_service.isFileExist(disk_snapshot_object.image_path):
            return True
        else:
            _logger.warning(r'disk_snapshot.image_path file NOT exist : {}'.format(disk_snapshot_object.image_path))
            return False

    @staticmethod
    def is_disk_snapshot_file_exist_v2(disk_snapshot_object):
        if disk_snapshot_object.merged:
            _logger.warning(r'disk_snapshot.image_path file merged : {} {}'
                            .format(disk_snapshot_object.image_path, disk_snapshot_object.ident))
            return False
        else:
            return True

    @staticmethod
    def get_host_snapshot_by_disk_snapshot(disk_snapshot_object, query_cache=None):
        if query_cache is not None:
            if disk_snapshot_object.id in query_cache:
                return query_cache[disk_snapshot_object.id]
            _temp_cache = list()
        else:
            _temp_cache = None

        while disk_snapshot_object.host_snapshot is None:
            if _temp_cache is not None:
                _temp_cache.append(disk_snapshot_object.id)
            disk_snapshot_object = disk_snapshot_object.parent_snapshot

        result = disk_snapshot_object.host_snapshot

        if _temp_cache is not None:
            for t in _temp_cache:
                query_cache[t] = result
            query_cache[disk_snapshot_object.id] = result
        return result

    @staticmethod
    def is_disk_snapshot_object_exist(disk_snapshot_object, query_cache=None):
        host_snapshot_object = GetSnapshotList.get_host_snapshot_by_disk_snapshot(disk_snapshot_object, query_cache)

        if host_snapshot_object.deleted:
            _logger.warning(r'host_snapshot_object.deleted : {} {}'
                            .format(disk_snapshot_object.ident, host_snapshot_object.id))
            return False
        else:
            return True

    @staticmethod
    def is_disk_snapshot_object_finished(disk_snapshot_object, query_cache=None):
        host_snapshot_object = GetSnapshotList.get_host_snapshot_by_disk_snapshot(disk_snapshot_object, query_cache)

        if host_snapshot_object.finish_datetime is None:
            _logger.warning(r'host_snapshot_object.finish_datetime is None : {} {}'
                            .format(disk_snapshot_object.ident, host_snapshot_object.id))
            return False
        else:
            return True

    @staticmethod
    def is_disk_snapshot_hash_file_exists(disk_snapshot_object):
        if disk_snapshot_object.is_cdp:
            return True
        return boxService.box_service.isFileExist(disk_snapshot_object.hash_path)

    @staticmethod
    def fix_disk_snapshot_hash_file(disk_snapshot_object):
        if disk_snapshot_object.is_cdp:
            return True
        if (not disk_snapshot_object.reorganized_hash) or \
                not boxService.box_service.isFileExist(disk_snapshot_object.hash_path):
            disk_snapshot_object = DiskSnapshot.objects.get(ident=disk_snapshot_object.ident)
            disk_snapshot_object.reorganized_hash = False
            disk_snapshot_object.save(update_fields=['reorganized_hash', ])
            while True:
                if xlogging.logger_traffic_control.is_logger_print(
                        'fix_disk_snapshot_hash_file', disk_snapshot_object.ident):
                    _logger.warning(
                        r'fix_disk_snapshot_hash_file. disk_snapshot {}'.format(
                            disk_snapshot_object.ident))

                disk_snapshot_obj = DiskSnapshot.objects.get(ident=disk_snapshot_object.ident)
                rev = DiskSnapshotHash.reorganize_hash_file_by_disk_snapshot(disk_snapshot_obj)
                if rev == 'failed':
                    _logger.warning('fix_disk_snapshot_hash_file could not fix, disk_snapshot_object:{}'.format(
                        disk_snapshot_object))
                    return False
                elif rev == 'successful':
                    break
                else:
                    pass

                time.sleep(0.1)
        return True

    @staticmethod
    def is_disk_snapshot_object_successful(disk_snapshot_object, query_cache=None):
        host_snapshot_object = GetSnapshotList.get_host_snapshot_by_disk_snapshot(disk_snapshot_object, query_cache)

        if host_snapshot_object.successful is None or not host_snapshot_object.successful:
            _logger.warning(r'host_snapshot_object.successful is None or False : {} {}'
                            .format(disk_snapshot_object.ident, host_snapshot_object.id))
            return False
        else:
            return True

    @staticmethod
    def format_timestamp(timestamp_begin, timestamp_end):
        if timestamp_begin is None and timestamp_end is None:
            return 'all'
        elif timestamp_begin is None and timestamp_end is not None:
            return r'$~{}'.format(boxService.box_service.formatCdpTimestamp(timestamp_end))
        elif timestamp_begin is not None and timestamp_end is None:
            return r'{}~$'.format(boxService.box_service.formatCdpTimestamp(timestamp_begin))
        else:
            return r'{}~{}'.format(boxService.box_service.formatCdpTimestamp(timestamp_begin),
                                   boxService.box_service.formatCdpTimestamp(timestamp_end))

    @staticmethod
    def copy_hash_in_create_empty_snapshot(last_disk_snapshot_object, disk_snapshot_object):
        if last_disk_snapshot_object.image_path == disk_snapshot_object.image_path:
            _logger.info(r'create_empty_snapshot create inc snapshot : {}'.format(disk_snapshot_object))
            if boxService.box_service.isFileExist(last_disk_snapshot_object.hash_path):
                boxService.box_service.remove(disk_snapshot_object.hash_path)
                boxService.box_service.copy(disk_snapshot_object.hash_path, last_disk_snapshot_object.hash_path)

            DiskSnapshotHash.reorganize_hash_file_by_disk_snapshot(disk_snapshot_object)
        else:
            _logger.info(r'create_empty_snapshot create first snapshot : {}'.format(disk_snapshot_object))
            boxService.box_service.runCmd('touch {}'.format(disk_snapshot_object.hash_path))

    @staticmethod
    def create_empty_snapshot(disk_snapshot_object, exception_msg, exception_debug, flag_str='create_empty_snapshot',
                              copy_hash_func=None):
        last_disk_snapshot_object = disk_snapshot_object.parent_snapshot

        validator_list = [GetSnapshotList.is_disk_snapshot_object_exist,
                          GetSnapshotList.is_disk_snapshot_file_exist]
        disk_snapshots = GetSnapshotList.query_snapshots_by_snapshot_object(last_disk_snapshot_object, validator_list)

        if len(disk_snapshots) == 0:
            xlogging.raise_and_logging_error(
                exception_msg, r'{} -- create_empty_snapshot snapshot_object invalid {}'.format(
                    exception_debug, last_disk_snapshot_object.id))

        new_ident = IMG.ImageSnapshotIdent(disk_snapshot_object.image_path, disk_snapshot_object.ident)
        handle = boxService.box_service.createNormalDiskSnapshot(
            new_ident, disk_snapshots, disk_snapshot_object.bytes,
            r'PiD{:x} BoxDashboard|{} {}'.format(os.getpid(), flag_str, last_disk_snapshot_object.id))
        boxService.box_service.closeNormalDiskSnapshot(handle, True)

        if copy_hash_func is not None:
            copy_hash_func(last_disk_snapshot_object, disk_snapshot_object)

    @staticmethod
    def clean_not_key_snapshots(snapshots):
        new_list = list()
        for index, snapshot in enumerate(snapshots):
            if index == 0:  # 第一个节点
                key_node = True
            elif index == len(snapshots) - 1:  # 最后一个节点
                key_node = True
            elif snapshot.path != snapshots[index - 1].path:  # 与前一个不同qcow文件
                key_node = True
            elif snapshot.path != snapshots[index + 1].path:  # 与后一个不同qcow文件
                key_node = True
            else:
                key_node = False

            if key_node:
                new_list.append(snapshot)
            else:
                pass
        return new_list


_cdp_locker = threading.RLock()


class Tokens(object):
    @staticmethod
    def get_using_cdp_token(host_snapshot_object):
        return CDPDiskToken.objects.filter(parent_disk_snapshot__host_snapshot=host_snapshot_object).exclude(
            token_expires=None).count()

    @staticmethod
    def cdp_logger(host_log_type, disk_snapshot_cdp_obj):
        host_snapshot_object = disk_snapshot_cdp_obj.disk_snapshot.host_snapshot
        tokens_number = Tokens.get_using_cdp_token(host_snapshot_object)

        if host_log_type == HostLog.LOG_CDP_PAUSE:
            if 0 == tokens_number:
                HostLog.objects.create(type=host_log_type, host=host_snapshot_object.host,
                                       reason=json.dumps({'host_snapshot': host_snapshot_object.id,
                                                          'description': 'CDP保护暂停',
                                                          'task_type': 'cdp_task',
                                                          'cdp_task': host_snapshot_object.cdp_task.id,
                                                          'stage': 'TASK_STEP_IN_PROGRESS_CDP_PAUSE'

                                                          },
                                                         ensure_ascii=False))
        elif host_log_type == HostLog.LOG_CDP_START:
            if 1 == tokens_number:
                HostLog.objects.create(type=host_log_type, host=host_snapshot_object.host,
                                       reason=json.dumps(
                                           {'host_snapshot': host_snapshot_object.id,
                                            'description': 'CDP保护开始',
                                            'task_type': 'cdp_task',
                                            'cdp_task': host_snapshot_object.cdp_task.id,
                                            'stage': 'TASK_STEP_IN_PROGRESS_CDP_START'
                                            },
                                           ensure_ascii=False))
        else:
            _logger.error(r'cdp_logger never happen {}'.format(host_log_type))

    @staticmethod
    def update_disk_snapshot_cdp_timestamp(disk_snapshot_cdp_obj, is_closed_cdp=True):
        cdp_path = disk_snapshot_cdp_obj.disk_snapshot.image_path
        start_timestamp, end_timestamp = boxService.box_service.queryCdpTimestampRange(cdp_path, is_closed_cdp)
        if start_timestamp is None:
            disk_snapshot_cdp_obj.last_timestamp = disk_snapshot_cdp_obj.first_timestamp
        else:
            disk_snapshot_cdp_obj.first_timestamp = start_timestamp
            disk_snapshot_cdp_obj.last_timestamp = end_timestamp
        disk_snapshot_cdp_obj.save(update_fields=['first_timestamp', 'last_timestamp'])

    @staticmethod
    def generate_new_disk_snapshot_cdp_obj(parent_disk_snapshot_obj, storage_node_path, host_ident, token_obj):
        new_ident = uuid.uuid4().hex.lower()

        cdp_file_folder = boxService.box_service.pathJoin([storage_node_path, 'images', host_ident])
        boxService.box_service.makeDirs(cdp_file_folder)
        new_cdp_file_name = boxService.box_service.pathJoin([cdp_file_folder, new_ident + '.cdp'])

        new_disk_snapshot_object = DiskSnapshot.objects.create(disk=parent_disk_snapshot_obj.disk,
                                                               parent_snapshot=parent_disk_snapshot_obj,
                                                               image_path=new_cdp_file_name,
                                                               ident=new_ident,
                                                               host_snapshot=None,
                                                               bytes=parent_disk_snapshot_obj.bytes,
                                                               type=parent_disk_snapshot_obj.type,
                                                               boot_device=parent_disk_snapshot_obj.boot_device)

        return DiskSnapshotCDP.objects.create(disk_snapshot=new_disk_snapshot_object,
                                              token=token_obj, first_timestamp=time.time())

    @staticmethod
    def generate_new_disk_snapshot_cdp_obj_v2(parent_disk_snapshot_obj, token_obj):
        new_ident = uuid.uuid4().hex.lower()

        cdp_file_folder = os.path.dirname(parent_disk_snapshot_obj.image_path)
        boxService.box_service.makeDirs(cdp_file_folder)
        new_cdp_file_name = boxService.box_service.pathJoin([cdp_file_folder, new_ident + '.cdp'])

        new_disk_snapshot_object = DiskSnapshot.objects.create(disk=parent_disk_snapshot_obj.disk,
                                                               parent_snapshot=parent_disk_snapshot_obj,
                                                               image_path=new_cdp_file_name,
                                                               ident=new_ident,
                                                               host_snapshot=None,
                                                               bytes=parent_disk_snapshot_obj.bytes,
                                                               type=parent_disk_snapshot_obj.type,
                                                               boot_device=parent_disk_snapshot_obj.boot_device)

        return DiskSnapshotCDP.objects.create(disk_snapshot=new_disk_snapshot_object,
                                              token=token_obj, first_timestamp=time.time())

    @staticmethod
    def disk_snapshot_cdp_obj2KTServiceToken(disk_snapshot_cdp_obj):
        disk_snapshot_object = disk_snapshot_cdp_obj.disk_snapshot
        token_object = disk_snapshot_cdp_obj.token
        schedule_object = Tokens.get_schedule_obj_from_cdp_task(token_object.task)

        if isinstance(schedule_object,
                      BackupTaskSchedule) and schedule_object.cycle_type != BackupTaskSchedule.CYCLE_CDP:
            xlogging.raise_and_logging_error(
                '内部异常，代码2382', 'disk_snapshot_cdp_obj2KTServiceToken type error : {}'.format(
                    schedule_object.cycle_type))

        cdp_mode_type = json.loads(schedule_object.ext_config)['cdpSynchAsynch']
        ident = 'all#' if cdp_mode_type == xdata.CDP_MODE_SYN else 'all'

        if isinstance(schedule_object, ClusterBackupSchedule):
            ident += '!'  # 集群备份关闭IO合并

        return KTService.Token(token=token_object.token,
                               snapshot=[IMG.ImageSnapshotIdent(disk_snapshot_object.image_path, ident)],
                               keepAliveIntervalSeconds=token_object.keep_alive_interval_seconds,
                               expiryMinutes=token_object.expiry_minutes,
                               diskBytes=disk_snapshot_object.bytes)

    @staticmethod
    def set_token(token_setting):
        if token_setting.expiryMinutes == 0:
            _logger.info('remove token:{}'.format(token_setting))
        elif (token_setting.snapshot is not None) and (len(token_setting.snapshot) > 0):
            _logger.info(
                r'set token:{} snapshot:{} snapshot:{} bytes:{}'.format(token_setting.token,
                                                                        token_setting.snapshot[0].path,
                                                                        token_setting.snapshot[0].snapshot,
                                                                        token_setting.diskBytes))
        else:
            _logger.warning(r'set token with no snapshot')

        try:
            boxService.box_service.updateToken(token_setting)
            return True
        except Exception as e:
            _logger.info(r'set token info failed, error:{}'.format(e))
            return False

    @staticmethod
    def delete_disk_snapshot_cdp_obj(disk_snapshot_cdp_obj):
        disk_snapshot_obj = disk_snapshot_cdp_obj.disk_snapshot
        disk_snapshot_cdp_obj.delete()
        disk_snapshot_obj.delete()

    @staticmethod
    def set_token_pause(disk_snapshot_cdp_obj):
        token_object = disk_snapshot_cdp_obj.token
        token_object.token_expires = None
        token_object.save(update_fields=['token_expires'])
        return KTService.Token(token=token_object.token,
                               snapshot=[],
                               expiryMinutes=0)

    @staticmethod
    def check_restore_token_object(restore_token_object):
        now_time = timezone.now()
        token_expires = restore_token_object.pe_host.token_expires
        if now_time > token_expires:
            if xlogging.logger_traffic_control.is_logger_print(
                    'check_restore_token_object__token_expires', restore_token_object.token):
                _logger.error(r'check_restore_token_object failed. token_expires {}'.format(restore_token_object.token))
            return False

        if restore_token_object.pe_host.finish_datetime is not None:
            if xlogging.logger_traffic_control.is_logger_print(
                    'check_restore_token_object__finish_datetime', restore_token_object.token):
                _logger.error(
                    r'check_restore_token_object failed. finish_datetime {}'.format(restore_token_object.token))
            return False

        return True

    @staticmethod
    def update_restore_expires(restore_token_object):
        pe_host_object = restore_token_object.pe_host
        pe_host_object.token_expires = timezone.now() + datetime.timedelta(minutes=pe_host_object.expiry_minutes)
        pe_host_object.save(update_fields=['token_expires'])

    @staticmethod
    def restore_token_object2KTServiceToken(restore_token_object, disk_snapshot_objects):
        pe_host_object = restore_token_object.pe_host
        return KTService.Token(token=restore_token_object.token, snapshot=disk_snapshot_objects,
                               keepAliveIntervalSeconds=pe_host_object.keep_alive_interval_seconds,
                               expiryMinutes=pe_host_object.expiry_minutes, )

    @staticmethod
    def check_cdp_token_object(cdp_token_object):
        if not cdp_token_object.parent_disk_snapshot.host_snapshot:
            return

        # TODO 随着空间回收，最开始的主机快照会被回收。 那么这个算法就不正确，需要修正
        host_snapshot_object = cdp_token_object.parent_disk_snapshot.host_snapshot
        if host_snapshot_object.deleted:
            xlogging.raise_and_logging_error(
                r'CDP快照关联的普通快照点被删除',
                r'check_cdp_token_object failed. check deleted {}'.format(cdp_token_object.token),
                status.HTTP_405_METHOD_NOT_ALLOWED)

        if (host_snapshot_object.finish_datetime is not None) and (not host_snapshot_object.successful):
            xlogging.raise_and_logging_error(
                r'CDP快照关联的普通快照点未能成功完成',
                r'check_cdp_token_object failed. check successful {}'.format(cdp_token_object.token),
                status.HTTP_405_METHOD_NOT_ALLOWED)

    @staticmethod
    def check_cdp_task_object(cdp_task_object):
        if cdp_task_object.schedule:
            if (not cdp_task_object.schedule.enabled) or cdp_task_object.schedule.deleted:
                xlogging.raise_and_logging_error(
                    r'CDP任务关联的计划已经停止',
                    r'check_cdp_task_object failed. check schedule {}'.format(cdp_task_object.schedule.name),
                    status.HTTP_405_METHOD_NOT_ALLOWED)

        if cdp_task_object.cluster_task:
            schedule_obj = cdp_task_object.cluster_task.schedule
            if (not schedule_obj.enabled) or schedule_obj.deleted:
                xlogging.raise_and_logging_error(
                    r'CDP任务关联的计划已经停止',
                    r'check_cdp_task_object failed. check schedule {}'.format(schedule_obj.name),
                    status.HTTP_405_METHOD_NOT_ALLOWED)

        if cdp_task_object.finish_datetime is not None:
            xlogging.raise_and_logging_error(
                r'CDP任务已经停止',
                r'check_cdp_task_object failed. check finish_datetime {}'.format(
                    cdp_task_object.finish_datetime.strftime(xdatetime.FORMAT_WITH_SECOND)),
                status.HTTP_405_METHOD_NOT_ALLOWED)

    # BackupTaskSchedule, ClusterBackupSchedule
    @staticmethod
    def get_schedule_obj_from_cdp_task(cdp_task):
        if cdp_task.schedule:
            return cdp_task.schedule
        elif cdp_task.cluster_task:
            return cdp_task.cluster_task.schedule
        else:
            xlogging.raise_and_logging_error(
                r'CDPTask[id={0}]不存在关联的BackupTaskSchedule或ClusterBackupSchedule'.format(cdp_task.id),
                r'CDPTask[id={0}] reference BackupTaskSchedule or ClusterBackupSchedule'.format(cdp_task.id),
                status.HTTP_405_METHOD_NOT_ALLOWED)

    @staticmethod
    def query_or_generate_disk_snapshot_cdp(cdp_token_object, storage_node_path):
        cdp_token_object.token_expires = timezone.now() + datetime.timedelta(minutes=cdp_token_object.expiry_minutes)
        cdp_token_object.save(update_fields=['token_expires'])

        if cdp_token_object.using_disk_snapshot is not None:
            # 有正在写入的CDP文件
            return cdp_token_object.using_disk_snapshot.cdp_info, False

        parent_disk_snapshot = cdp_token_object.parent_disk_snapshot

        if cdp_token_object.last_disk_snapshot is not None:
            if parent_disk_snapshot.host_snapshot:
                new_disk_snapshot_cdp_object = \
                    Tokens.generate_new_disk_snapshot_cdp_obj(
                        cdp_token_object.last_disk_snapshot, storage_node_path,
                        parent_disk_snapshot.host_snapshot.host.ident, cdp_token_object)
            else:
                new_disk_snapshot_cdp_object = \
                    Tokens.generate_new_disk_snapshot_cdp_obj_v2(
                        cdp_token_object.last_disk_snapshot, cdp_token_object)
        else:
            # 没有CDP文件，意味着这是第一次生成CDP文件
            if parent_disk_snapshot.host_snapshot:
                new_disk_snapshot_cdp_object = \
                    Tokens.generate_new_disk_snapshot_cdp_obj(
                        parent_disk_snapshot, storage_node_path,
                        parent_disk_snapshot.host_snapshot.host.ident, cdp_token_object)
            else:
                new_disk_snapshot_cdp_object = \
                    Tokens.generate_new_disk_snapshot_cdp_obj_v2(parent_disk_snapshot, cdp_token_object)

        cdp_token_object.using_disk_snapshot = new_disk_snapshot_cdp_object.disk_snapshot
        cdp_token_object.save(update_fields=['using_disk_snapshot'])

        return new_disk_snapshot_cdp_object, True

    @staticmethod
    @xlogging.LockDecorator(_cdp_locker)
    def suspend_disk_snapshot_cdp_object(disk_snapshot_cdp_object, suspend_token=False, sync=False):
        disk_snapshot = disk_snapshot_cdp_object.disk_snapshot
        is_cdp_file_exist = boxService.box_service.isFileExist(disk_snapshot.image_path)
        token_setting = None
        token_object = disk_snapshot_cdp_object.token

        if suspend_token:
            token_setting = Tokens.set_token_pause(disk_snapshot_cdp_object)

        if is_cdp_file_exist:
            if sync:
                Tokens.update_disk_snapshot_cdp_timestamp(disk_snapshot_cdp_object)
            else:
                # 优化性能，异步修正CDP文件的实际时间范围
                _update_disk_snapshot_cdp_timestamp_queue.put(disk_snapshot_cdp_object)
            if token_object.using_disk_snapshot is not None:
                token_object.last_disk_snapshot = token_object.using_disk_snapshot
                token_object.using_disk_snapshot = None
                token_object.save(update_fields=['using_disk_snapshot', 'last_disk_snapshot'])
            return disk_snapshot_cdp_object, token_setting
        else:
            token_object.using_disk_snapshot = None
            token_object.save(update_fields=['using_disk_snapshot'])
            disk_snapshot_cdp_object.delete()
            disk_snapshot.delete()
            return None, token_setting

    @staticmethod
    def stop_cdp_task(cdp_task_obj):
        @xlogging.LockDecorator(_cdp_locker)
        def _stop_token(cdp_token_obj_id):
            _cdp_token_obj = CDPDiskToken.objects.get(id=cdp_token_obj_id)

            if _cdp_token_obj.using_disk_snapshot is None:
                return

            disk_snapshot_object = _cdp_token_obj.using_disk_snapshot
            disk_snapshot_cdp_object, token_setting = \
                Tokens.suspend_disk_snapshot_cdp_object(disk_snapshot_object.cdp_info, True, True)
            Tokens.set_token(token_setting)
            if disk_snapshot_cdp_object is not None:
                Tokens.suspend_disk_snapshot_cdp_object(disk_snapshot_cdp_object, False)

        token_str_list = list()

        if cdp_task_obj.finish_datetime is None:
            cdp_task_obj.finish_datetime = datetime.datetime.now()
            cdp_task_obj.save(update_fields=['finish_datetime', ])

        cdp_tokens = CDPDiskToken.objects.filter(task=cdp_task_obj).all()
        for ctk in cdp_tokens:
            _stop_token(ctk.id)
            token_str_list.append(ctk.token)
        return token_str_list

    @staticmethod
    @xlogging.LockDecorator(_cdp_locker)
    def change_cdp_file_logic(cdp_token_id, last_path, only_close=False):
        cdp_token_object = CDPDiskToken.objects.get(id=cdp_token_id)

        if cdp_token_object.using_disk_snapshot is None:
            xlogging.raise_and_logging_error(
                '无效的参数', 'CdpTokenInfo using_disk_snapshot', status.HTTP_400_BAD_REQUEST)

        if cdp_token_object.using_disk_snapshot.image_path != last_path:
            xlogging.raise_and_logging_error(
                '内部异常，无效的存储路径',
                'CdpTokenInfo image_path != last_path {} | {}'.format(cdp_token_object.using_disk_snapshot.image_path,
                                                                      last_path))

        parent_disk_snapshot_cdp_obj, token_setting = Tokens.suspend_disk_snapshot_cdp_object(
            cdp_token_object.using_disk_snapshot.cdp_info, only_close)

        # Tokens.suspend_disk_snapshot_cdp_object 中更新了 cdp_token_object， 重新获取
        cdp_token_object = CDPDiskToken.objects.get(id=cdp_token_object.id)

        if parent_disk_snapshot_cdp_obj is None:
            xlogging.raise_and_logging_error(
                '内部异常，无效的父快照文件',
                r'CdpTokenInfo get {} path {} failed. cdp file not exist'.format(cdp_token_object.token, last_path),
                status.HTTP_501_NOT_IMPLEMENTED)

        if only_close:
            token_setting.expiryMinutes = -2
            if not Tokens.set_token(token_setting):
                xlogging.raise_and_logging_error(
                    r'内部异常，代码2376', 'change_cdp_file_logic clear token info failed {}'.format(token_setting.token))
            Tokens.suspend_disk_snapshot_cdp_object(parent_disk_snapshot_cdp_obj, False)
            return

        Tokens.check_cdp_token_object(cdp_token_object)

        Tokens.check_cdp_task_object(cdp_token_object.task)

        schedule = Tokens.get_schedule_obj_from_cdp_task(parent_disk_snapshot_cdp_obj.token.task)

        storage_node_path = UserQuotaTools.check_user_storage_size_in_node(schedule)

        disk_snapshot_cdp_obj, is_new_file \
            = Tokens.query_or_generate_disk_snapshot_cdp(cdp_token_object, storage_node_path)

        if not is_new_file:
            xlogging.raise_and_logging_error(
                '内部异常，无效的快照文件',
                r'CdpTokenInfo get NOT use old cdp file {}'.format(disk_snapshot_cdp_obj.disk_snapshot.image_path),
                status.HTTP_501_NOT_IMPLEMENTED)

        token_setting = Tokens.disk_snapshot_cdp_obj2KTServiceToken(disk_snapshot_cdp_obj)
        if not Tokens.set_token(token_setting):
            Tokens.suspend_disk_snapshot_cdp_object(disk_snapshot_cdp_obj, False)
            xlogging.raise_and_logging_error('无法创建快照文件', 'CdpTokenInfo get send cmd failed',
                                             status.HTTP_406_NOT_ACCEPTABLE)

    @staticmethod
    @xlogging.LockDecorator(_cdp_locker)
    def close_cdp_file_logic(disk_snapshot_cdp_object):
        disk_snapshot_cdp_object = DiskSnapshotCDP.objects.get(disk_snapshot=disk_snapshot_cdp_object.disk_snapshot)

        disk_snapshot_cdp_obj, token_setting = \
            Tokens.suspend_disk_snapshot_cdp_object(disk_snapshot_cdp_object, True)

        if not Tokens.set_token(token_setting):
            _logger.warning(r'CdpTokenInfo delete send close cdp cmd failed {}'.format(token_setting.token))
            return False

        if disk_snapshot_cdp_obj is not None:
            Tokens.suspend_disk_snapshot_cdp_object(disk_snapshot_cdp_obj, False)
        return True

    @staticmethod
    @xlogging.LockDecorator(_cdp_locker)
    def refresh_cdp_file_logic(cdp_token_object):
        cdp_token_object = CDPDiskToken.objects.get(id=cdp_token_object.id)

        Tokens.check_cdp_token_object(cdp_token_object)

        Tokens.check_cdp_task_object(cdp_token_object.task)

        schedule = Tokens.get_schedule_obj_from_cdp_task(cdp_token_object.task)

        # 判断空间是否够用、可用，注意不要refresh device
        # 返回： /mnt/nodes/xxxx 或 /home/aio
        storage_node_path = UserQuotaTools.check_user_storage_size_in_node(schedule)

        disk_snapshot_cdp_obj, is_new_file \
            = Tokens.query_or_generate_disk_snapshot_cdp(cdp_token_object, storage_node_path)
        if is_new_file:
            _logger.info(r'cdp create new file token:{} file:{}'
                         .format(cdp_token_object.token, disk_snapshot_cdp_obj.disk_snapshot.image_path))
        else:
            _logger.info(r'cdp use old file token:{} file:{}'
                         .format(cdp_token_object.token, disk_snapshot_cdp_obj.disk_snapshot.image_path))

        token_setting = Tokens.disk_snapshot_cdp_obj2KTServiceToken(disk_snapshot_cdp_obj)
        if not Tokens.set_token(token_setting):
            Tokens.suspend_disk_snapshot_cdp_object(disk_snapshot_cdp_obj, False)
            return False

        return True


_disk_snapshot_locker = threading.RLock()


class DiskSnapshotLocker(object):
    @staticmethod
    @xlogging.LockDecorator(_disk_snapshot_locker)
    @xlogging.convert_exception_to_value(None)
    def unlock_files_by_task_name_prefix(task_name_prefix):
        disk_snapshots = DiskSnapshot.objects.filter(reference_tasks__contains=task_name_prefix)
        for disk_snapshot_object in disk_snapshots:
            if disk_snapshot_object.merged:
                _logger.warning(r'DiskSnapshotLocker unlock_files_by_task_name_prefix has merged {}'.format(
                    disk_snapshot_object.id))
                continue

            tasks = list(filter(None, disk_snapshot_object.reference_tasks.split('|')))
            tasks = [x for x in tasks if (not x.startswith(task_name_prefix))]

            disk_snapshot_object.reference_tasks = '|'.join(tasks)
            disk_snapshot_object.save(update_fields=['reference_tasks'])

    @staticmethod
    @xlogging.LockDecorator(_disk_snapshot_locker)
    # 锁定快照文件不被回收
    # file_path, ident 磁盘快照的文件路径与快照名称
    # task_name 锁定的任务，命名规则：字母、数字、下划线，不可有 |
    def lock_file(file_path, ident, task_name, has_merged=False):
        disk_snapshot_object = DiskSnapshotLocker.get_disk_snapshot_object(file_path, ident)

        if not has_merged and disk_snapshot_object.merged:
            xlogging.raise_and_logging_error(
                r'锁定快照镜像失败，该镜像文件被回收',
                'DiskSnapshotLocker lock_file has merged {}'.format(disk_snapshot_object.id))

        tasks = list(filter(None, disk_snapshot_object.reference_tasks.split('|')))
        if task_name in tasks:
            xlogging.raise_and_logging_error(
                r'内部异常，代码2352',
                'DiskSnapshotLocker lock_file same name {} {} {}'.format(disk_snapshot_object.id, task_name, tasks))

        tasks.append(task_name)
        disk_snapshot_object.reference_tasks = '|'.join(tasks)
        disk_snapshot_object.save(update_fields=['reference_tasks'])

    @staticmethod
    @xlogging.LockDecorator(_disk_snapshot_locker)
    @xlogging.convert_exception_to_value(None)
    # 解除快照文件锁定
    # file_path, ident 磁盘快照的文件路径与快照名称
    # task_name 锁定的任务，命名规则：字母、数字、下划线，不可有 |
    def unlock_file(file_path, ident, task_name, has_merged=False):
        disk_snapshot_object = DiskSnapshotLocker.get_disk_snapshot_object(file_path, ident)

        if not has_merged and disk_snapshot_object.merged:
            _logger.warning(r'DiskSnapshotLocker unlock_file has merged {}'.format(disk_snapshot_object.id))
            return

        tasks = list(filter(None, disk_snapshot_object.reference_tasks.split('|')))
        if task_name not in tasks:
            _logger.warning(
                r'DiskSnapshotLocker unlock_file NOT locker {} {}'.format(disk_snapshot_object.id, task_name))
            return

        tasks.remove(task_name)
        disk_snapshot_object.reference_tasks = '|'.join(tasks)
        disk_snapshot_object.save(update_fields=['reference_tasks'])

    @staticmethod
    def get_disk_snapshot_object(file_path, ident):
        if DiskSnapshot.is_cdp_file(file_path):
            disk_snapshot_object = DiskSnapshot.objects.get(image_path=file_path)
        else:
            disk_snapshot_object = DiskSnapshot.objects.get(image_path=file_path, ident=ident)
        return disk_snapshot_object

    @staticmethod
    @xlogging.LockDecorator(_disk_snapshot_locker)
    # files 为 IMG::ImageSnapshotIdents
    def lock_files(files, task_name):
        for file in files:
            DiskSnapshotLocker.lock_file(file.path, file.snapshot, task_name)
            if not DiskSnapshot.is_cdp_file(file.path):
                CompressTaskThreading().update_task_by_disk_snapshot(file.path, file.snapshot)

    @staticmethod
    @xlogging.LockDecorator(_disk_snapshot_locker)
    # files 为 IMG::ImageSnapshotIdents
    def unlock_files(files, task_name):
        for file in files:
            DiskSnapshotLocker.unlock_file(file.path, file.snapshot, task_name)

    @staticmethod
    @xlogging.LockDecorator(_disk_snapshot_locker)
    def set_merged(disk_snapshot):
        disk_snapshot_object = DiskSnapshot.objects.get(id=disk_snapshot.id)
        if disk_snapshot_object.merged:
            return False

        tasks = list(filter(None, disk_snapshot_object.reference_tasks.split('|')))
        if len(tasks) != 0:
            xlogging.raise_and_logging_error(r'磁盘快照被使用中', r'disk snapshot {} using by {}'
                                             .format(disk_snapshot_object.id, tasks), print_args=False)

        disk_snapshot.merged = True
        disk_snapshot.save(update_fields=['merged'])
        return True


class SnapshotsUsedBitMapGeneric(object):
    """
    获取快照链的位图
    """

    def __init__(self, snapshots, flag):
        self.snapshots = snapshots
        self.flag = flag
        self.bit_map = b''

    def get(self):
        """
        :return:  bytearray()
        """
        _logger.info('SnapshotsUsedBitMapGeneric get snapshots:{}'.format(self.snapshots))
        with_same_file_snapshot_list, other_snapshot_list = self._separate_snapshot_list()
        _logger.info('SnapshotsUsedBitMapGeneric get with_same_file_snapshot_list:{}'.format(
            with_same_file_snapshot_list))
        _logger.info('SnapshotsUsedBitMapGeneric get other_snapshot_list:{}'.format(
            other_snapshot_list))

        same_file_bit_map = self._get_bit_with_same_file(with_same_file_snapshot_list)
        other_bit_map = self._get_bit_other(other_snapshot_list)
        if same_file_bit_map and other_bit_map:
            return SnapshotsUsedBitMap.merge_bit_maps(same_file_bit_map, other_bit_map)
        else:
            if same_file_bit_map:
                rs = bytearray(same_file_bit_map)
            elif other_bit_map:
                rs = bytearray(other_bit_map)
            else:
                rs = bytearray()
            return rs

    def _separate_snapshot_list(self):
        for index, snapshot in enumerate(self.snapshots):
            if not self._qcow_file_same_with_its_parent(snapshot):
                sp_index = index
                break
        else:
            return self.snapshots[:], []
        return self.snapshots[:sp_index], self.snapshots[sp_index:]

    def _qcow_file_same_with_its_parent(self, snapshot):
        disk_snapshot = self._get_disk_snapshot(snapshot)
        parent_snapshot = disk_snapshot.parent_snapshot
        if parent_snapshot:
            return disk_snapshot.image_path == parent_snapshot.image_path
        return False

    def _get_bit_with_same_file(self, snapshot_list):
        if snapshot_list:
            first_disk_snapshot = self._get_disk_snapshot(snapshot_list[0])
            parent_disk_snapshot = first_disk_snapshot.parent_snapshot
            prev_snapshot = IMG.ImageSnapshotIdent(parent_disk_snapshot.image_path, parent_disk_snapshot.ident)
            tmp_file = '/tmp/tmp_qcow_bitmap{}'.format(uuid.uuid4().hex)
            try:
                if not get_snapshot_inc_bitmap_V2(snapshot_list[-1], prev_snapshot, tmp_file):
                    raise xlogging.raise_and_logging_error('获取增量位图失败', 'get inc map fail')
                else:
                    with open(tmp_file, 'rb') as f:
                        return bytearray(f.read())
            finally:
                boxService.box_service.remove(tmp_file)
        else:
            return None

    def _get_bit_other(self, snapshot_list):
        if snapshot_list:
            with SnapshotsUsedBitMap(snapshot_list, self.flag) as f:
                return f.read()
        else:
            return None

    @staticmethod
    def _get_disk_snapshot(snap):
        if DiskSnapshot.is_cdp_file(snap.path):
            return DiskSnapshot.objects.get(image_path=snap.path)
        else:
            return DiskSnapshot.objects.get(ident=snap.snapshot)


class SnapshotsUsedBitMap(object):
    """
    snapshots为完整的链或者头一个为cdp
    """

    def __init__(self, snapshots, flag):
        self.snapshots = snapshots
        self.flag = flag
        self.bit_map = b''
        self.handle = None
        self.all_exists = False  # 还没有检测

    def __enter__(self):
        count = 5
        while count > 0:
            self.handle = boxService.box_service.openSnapshots(self.snapshots, self.flag)
            if self.handle != 0:
                break
            else:
                _logger.warning(
                    'SnapshotsUsedBitMap open snapshots:{} fail, fd is 0, will retry'.format(self.snapshots))
                self._check_snapshots_file_exists()
                time.sleep(5)
                count -= 1
        else:
            xlogging.raise_and_logging_error('打开快照文件失败', 'SnapshotsUsedBitMap open snapshots:{} fail'.format(
                self.snapshots), 110)
        return self

    # 在打开文件失败的时候，检测文件是否存在
    def _check_snapshots_file_exists(self):
        if self.all_exists:
            return None
        for snapshot in self.snapshots:
            if not boxService.box_service.isFileExist(snapshot.path):
                xlogging.raise_and_logging_error('快照文件不存在，打开失败', 'snapshot:{} not exists'.format(snapshot.path), 7654)
            else:
                pass
        self.all_exists = True

    def __exit__(self, exc_type, exc_val, exc_tb):
        boxService.box_service.closeNormalDiskSnapshot(self.handle, True)

    def read(self):
        index = 0
        while True:
            index, bitmap, finish = boxService.box_service.getTotalUesdBlockBitmap(self.handle, index)
            if index == 0:
                return b''
            self.bit_map += bitmap
            if finish:
                break
        return self.bit_map

    @staticmethod
    def merge_bit_maps(*args):
        return bytearray(map(SnapshotsUsedBitMap._reduce, zip_longest(*args, fillvalue=0)))

    @staticmethod
    def _reduce(args):
        return functools.reduce(lambda x, y: x | y, args)


class LineIntervalToBitMap(object):
    """
    max_size:磁盘最大字节数
    line_interval:线段[[offset, bytes]]
    blk:块大小
    """

    @staticmethod
    def to_bitmap(max_size, line_interval, blk=64 * 1024):
        bitmap = BitMap((max_size + blk - 1) // blk)
        for line in line_interval:
            start_offset = line[0] // blk
            end_offset = (line[0] + line[1] + blk - 1) // blk
            for b in range(start_offset, end_offset):
                bitmap.set(b)
        return bytearray(bitmap.bitmap)
