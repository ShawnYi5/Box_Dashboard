import contextlib
import datetime
import json
import os
import shutil
import threading
import time
import uuid
from functools import partial

from django.db import transaction
from django.utils import timezone
from rest_framework import status as http_status
from taskflow import engines, task
from taskflow.listeners import logging as logging_listener
from taskflow.patterns import linear_flow as lf
from taskflow.persistence import models

from apiv1 import cdp_wrapper
from apiv1 import models as m
from apiv1 import snapshot
from apiv1 import spaceCollection
from apiv1 import task_helper
from apiv1 import tasks
from apiv1 import work_processors
from apiv1.cluster_backup_task import (_is_canceled, CBT_StopCdp)
from box_dashboard import boxService
from box_dashboard import task_backend
from box_dashboard import xdata
from box_dashboard import xdatetime
from box_dashboard import xlogging

_logger = xlogging.getLogger(__name__)
_logger_human = xlogging.getLogger('cluster_human')

import BoxLogic
import IMG
import KTService

_generate_locker = threading.Lock()

STATUS_MAP = (
    ('CctStartBackup', '初始化参数'),
    ('CctSendBackupCmd', '发送备份指令'),
    ('CctBaseBackup', '传输基础数据'),
    ('CctFetchT0', '分析集群数据一'),
    ('CctSplitT0', '分析集群数据二'),
    ('CctGenerateDiff', '生成集群数据'),
    ('CctQueryT1', '分析集群数据三'),
    ('CctCreateHostSnapshot', '生成主机快照'),
    ('CctStopCdpWhenError', '停止CDP状态'),
    ('CctMergeDataWhenError', '合并临时数据'),
    ('CctUnlockResource', ''),
    ('CctCleanTemporary', '清理临时数据'),
    ('CctCdpBackup', 'CDP保护中'),
    ('CctStopCdp', '暂停CDP状态'),
    ('CctFinishBackup', '任务结束'),
)


class CctHelper(object):
    def __init__(self, info):
        self.info = info
        return

    def force_generic_cdp_file(self):
        host, disks = self.info.get_master_disks()
        assert host
        for i in disks:
            boxService.box_service.testDisk(host, i, 22, 1)
        return


class ClusterCdpTaskExecutor(threading.Thread):
    def __init__(self):
        super(ClusterCdpTaskExecutor, self).__init__()
        self.name = r'ClusterCdpTask_'
        self._schedule_id = None
        self._task_id = None
        self._engine = None
        self._book_uuid = None

    def load_from_uuid(self, task_uuid, task_id):
        backend = task_backend.get_backend()
        with contextlib.closing(backend.get_connection()) as conn:
            book = conn.get_logbook(task_uuid['book_id'])
            flow_detail = book.find(task_uuid['flow_id'])
        self._engine = engines.load_from_detail(flow_detail, backend=backend, engine='serial')
        self._book_uuid = book.uuid
        self.name += r'{} load exist uuid {} {}'.format(task_id, task_uuid['book_id'], task_uuid['flow_id'])
        assert self._task_id is None
        self._task_id = task_id

    @staticmethod
    @xlogging.LockDecorator(_generate_locker)
    def generate_task_object(reason, schedule_object, input_config):
        assert schedule_object.cycle_type == m.BackupTaskSchedule.CYCLE_CDP

        other_object = m.ClusterBackupTask.objects.filter(
            finish_datetime__isnull=True, schedule=schedule_object).first()
        if other_object is not None:
            xlogging.raise_and_logging_error(r'计划正在执行中',
                                             r'other_object running : {}'.format(other_object.id),
                                             http_status.HTTP_501_NOT_IMPLEMENTED)

        force_full, disable_optimize = ClusterCdpTaskExecutor._is_force_full_and_disable_optimize(
            input_config, schedule_object, reason)
        config_json = json.dumps({
            "cluster_cdp_task": True,
            "input": input_config,
            "force_full": force_full,
            "disable_optimize": disable_optimize,
            "force_store_full": ClusterCdpTaskExecutor._is_force_store_full(input_config, schedule_object),
        })
        return m.ClusterBackupTask.objects.create(reason=reason, schedule=schedule_object, ext_config=config_json)

    def generate_and_save(self, schedule_object, reason, input_config):
        task_object = self.generate_task_object(reason, schedule_object, input_config)
        # work_processors.HostBackupWorkProcessors.cluster_hosts_log(
        #     schedule_object, task_object, m.HostLog.LOG_CLUSTER_CDP_START, **{
        #         'substage': '集群持续保护：{}'.format(schedule_object.name)
        #     })

        self.name += r'{}'.format(task_object.id)
        self._task_id = task_object.id
        self._schedule_id = schedule_object.id

        backend = task_backend.get_backend()
        book = models.LogBook(
            r"{}_{}".format(self.name, datetime.datetime.now().strftime(xdatetime.FORMAT_WITH_SECOND_FOR_PATH)))
        with contextlib.closing(backend.get_connection()) as conn:
            conn.save_logbook(book)

        try:
            self._engine = engines.load_from_factory(create_flow, backend=backend, book=book, engine='serial',
                                                     factory_args=(self.name, self._task_id, book.uuid))
            self._book_uuid = book.uuid

            task_object.task_uuid = json.dumps({'book_id': book.uuid, 'flow_id': self._engine.storage.flow_uuid})
            task_object.save(update_fields=['task_uuid'])
            return task_object
        except Exception as e:
            _logger.error(r'generate_uuid failed {}'.format(e), exc_info=True)
            with contextlib.closing(backend.get_connection()) as conn:
                conn.destroy_logbook(book.uuid)
            raise e

    def start(self):
        if self._engine:
            try:
                super().start()
            except Exception:
                raise
        else:
            xlogging.raise_and_logging_error('内部异常，无效的调用', r'start without _engine ：{}'.format(self.name),
                                             http_status.HTTP_501_NOT_IMPLEMENTED)

    def run(self):
        try:
            with logging_listener.DynamicLoggingListener(self._engine):
                self._engine.run()
        except Exception as e:
            _logger.error(r'ClusterCdpTaskExecutor run engine {} failed {}'.format(self.name, e), exc_info=True)
            tasks.BackupScheduleRetryHandle.modify(m.ClusterBackupSchedule.objects.get(id=self._schedule_id))
        finally:
            with contextlib.closing(task_backend.get_backend().get_connection()) as conn:
                conn.destroy_logbook(self._book_uuid)
        self._engine = None

    @staticmethod
    def _is_force_full_and_disable_optimize(input_config, schedule_object, reason):
        if schedule_object and tasks.is_force_full_by_schedule(schedule_object, _logger.info, 2):
            return True, True
        if reason != m.BackupTask.REASON_PLAN_MANUAL and schedule_object and \
                tasks.is_force_full_by_schedule(schedule_object, _logger.info, 1):
            return True, True
        else:
            return tasks.is_force_full_by_config(input_config, _logger.info), False

    @staticmethod
    def _is_force_store_full(input_config, schedule_object):
        _ = schedule_object  # TODO 读取计划配置信息
        return tasks.is_force_store_full_by_config(input_config, _logger.info)

    @staticmethod
    def set_status_and_create_log(task_id, host_ident, status_code, description, debug=''):
        _task = m.ClusterBackupTask.objects.get(id=task_id)
        _task.status_info = status_code
        _task.save(update_fields=['status_info'])
        reason = {'task_id': task_id, 'status_code': status_code, 'debug': debug, "description": description}
        if host_ident:
            m.HostLog.objects.create(host=m.Host.objects.get(ident=host_ident), type=m.HostLog.LOG_CLUSTER_CDP,
                                     reason=json.dumps(reason, ensure_ascii=False))


def create_flow(name, task_id, book_uuid):
    _ = book_uuid
    flow = lf.Flow(name).add(
        CctStartBackup(name, task_id),  # 准备备份参数，不可多次执行
        CctSendBackupCmd(name, task_id),  # 发送备份指令，不可多次执行
        CctBaseBackup(name, task_id),  # 基础备份阶段，等待基础备份完成，可多次执行
        CctFetchT0(name, task_id),  # 查找T0时刻，并轮转CDP文件，可多次执行
        CctSplitT0(name, task_id),  # 按照T0时刻，分割cdp文件，可多次执行
        CctGenerateDiff(name, task_id),  # 生成差异数据，可多次执行
        CctQueryT1(name, task_id),  # 查询T1时刻，可多次执行
        CctCreateHostSnapshot(name, task_id),  # 生成CDP主机快照， 不可多次执行
        CctStopCdpWhenError(name, task_id),  # 发生错误时的停止CDP备份，可多次执行
        CctMergeDataWhenError(name, task_id),  # 发生错误时将临时数据，叠加到主节点数据流中，需要可多次执行
        CctUnlockResource(name, task_id),  # 解锁资源，可多次执行
        CctCleanTemporary(name, task_id),  # 清理中间数据
        CctCdpBackup(name, task_id),  # CDP持续备份阶段，等待CDP备份断开，或者 计划被禁用，可多次执行
        CctStopCdp(name, task_id),  # 停止CDP备份，可多次执行
        CctFixCdpLastTime(name, task_id),  # 修正CDP的最后结束时间
        CctFinishBackup(name, task_id),
    )
    return flow


def _check_run_twice(task_object, step_name):
    task_uuid = json.loads(task_object.task_uuid)
    if 'run_twice' not in task_uuid.keys():
        task_uuid['run_twice'] = dict()
    if task_uuid['run_twice'].get(step_name, None) is not None:
        xlogging.raise_and_logging_error('内部异常，代码2385', '_check_run_twice {} - {} failed'
                                         .format(task_object.id, step_name))
    task_uuid['run_twice'][step_name] = 'r'
    task_object.task_uuid = json.dumps(task_uuid)
    task_object.save(update_fields=['task_uuid'])
    _logger.info(r'_check_run_twice {} - {}'.format(task_object.id, step_name))


def load_objs(task_id):
    cluster_cdp_task_obj = m.ClusterBackupTask.objects.get(id=task_id)
    cluster_cdp_task_config = json.loads(cluster_cdp_task_obj.ext_config)
    schedule_obj = cluster_cdp_task_obj.schedule
    schedule_config = json.loads(schedule_obj.ext_config)

    return cluster_cdp_task_obj, cluster_cdp_task_config, schedule_obj, schedule_config


"""
schedule_config
{
    "removeDuplicatesInSystemFolder": true,
    "autoCleanDataWhenlt": 200,
    "next_force_full_count": 0,
    "BackupIOPercentage": 30,
    "FullParamsJsonStr": xxxxxxxxxx
    "cdpSynchAsynch": 0,
    "execute_schedule_retry": 0,
    "diskreadthreadcount": 4,
    "daysInMonth": [],
    "incMode": 2,
    "maxBroadband": 300,
    "backup_retry": {
      "interval": 10,
      "enable": true,
      "count": 5
    },
    "backupDayInterval": -1,
    "daysInWeek": [],
    "IntervalUnit": "",
    "exclude": {},
    "cluster_disks": [{
      "map_disks": [{
        "disk_guid": "5329e410000000000000000000000000",
        "host_ident": "6acb7e6f0d0048839e6266c1c80d8acc"
      }, {
        "disk_guid": "9489381f000000000000000000000000",
        "host_ident": "63b06b18f235463cbe342cd54a3560b4"
      }],
      "ident": "b4b186cf-1d2b-8e63-7952-189f3ad37ff7",
      "name": "sdb",
      "map_disks_lab": "localhost.localdomain(G24K)(172.16.120.24): 
        /dev/sdb(VMware, VMware Virtual S[/dev/sdb],20.00GB)<br>localhost.localdomain(NS7C)(172.16.120.107): 
        /dev/sdb(VMware, VMware Virtual S[/dev/sdb],20.00GB)"
    }],
===============new======================
    "master_node": host_ident,
========================================
    "backupDataHoldDays": 30,
    "isencipher": 0,
    "backupLeastNumber": 5
}
"""


class AgentValidDiskSnapshotInfo(object):
    def __init__(self, schedule_obj):
        self.schedule_obj = schedule_obj
        self.info_dict = json.loads(schedule_obj.agent_valid_disk_snapshot)
        """agent_valid_disk_snapshot
        {
            "master_ident": host_ident,
            "master": {
                "b4b186cf-1d2b-8e63-7952-189f3ad37ff7": {   <--- 集群磁盘的逻辑标识
                    "disk_guid": native_guid,
                    "base_disk_snapshot_ident" : snapshot_ident,    <--- 记录最近一次“完整备份”的基础快照
                }
            },
            "slaves": {
                "b4b186cf-1d2b-8e63-7952-189f3ad37ff7": {   <--- 集群磁盘的逻辑标识
                    "6acb7e6f0d0048839e6266c1c80d8acc": {   <--- 集群主机 (从节点)
                        "disk_guid": native_guid,
                        "disk_snapshots" : [
                            snapshot_ident, ...             <--- 在agent启动备份后，立刻将当次基础备份的数据保存到该列表中
                        ],
                        "cdp_tokens" : [
                            cdp_token, ...                  <--- 在agent启动备份前，将当次将要使用的CPD token记录下来
                        ],
                        "last_disk_logic_ident" : ident     <--- 磁盘逻辑ident，会影响到是否完整备份
                    },
                    ...
                }
            }
        }
        """

    class NeedCleanHistoryData(Exception):
        pass

    def _save_agent_valid_disk_snapshot(self):
        self.schedule_obj.agent_valid_disk_snapshot = json.dumps(self.info_dict, ensure_ascii=False)
        self.schedule_obj.save(update_fields=['agent_valid_disk_snapshot', ])

    def _dump_info(self, msg):
        _logger.info('{} : {}'.format(msg, json.dumps(self.info_dict, ensure_ascii=False)))

    def get_base_disk_snapshot_ident(self):
        """
        {cluster_logic_disk_ident: base_disk_snapshot_ident, }
        """
        result = dict()

        if "master" not in self.info_dict:
            return result

        for cluster_logic_disk_ident, info in self.info_dict['master'].items():
            base_disk_snapshot_ident = info.get('base_disk_snapshot_ident', None)
            if base_disk_snapshot_ident:
                result[cluster_logic_disk_ident] = base_disk_snapshot_ident

        return result

    def clean_invalid_slave_step_one(self, schedule_config, slave_host_infos):
        if 'slaves' not in self.info_dict:
            return list()

        clean_invalid_data_tasks = list()

        self._dump_info('clean_invalid_slave_step_one begin')

        for disk_logic_ident, disk_logic_info in self.info_dict['slaves'].items():
            for host_ident, host_disk_info in disk_logic_info.items():
                try:
                    self._check_snapshot_file_exist(host_disk_info)
                    self._check_agent_disk_status(
                        schedule_config, slave_host_infos, host_ident, disk_logic_ident, host_disk_info)
                except AgentValidDiskSnapshotInfo.NeedCleanHistoryData as nchd:
                    _logger.error('NeedCleanHistoryData : {}'.format(nchd), exc_info=True)
                    self._clean_history_data(clean_invalid_data_tasks, host_disk_info)

        self._dump_info('clean_invalid_slave_step_one end')

        return clean_invalid_data_tasks

    def _clean_history_data(self, clean_invalid_data_tasks, host_disk_info):
        for disk_snapshot_ident in host_disk_info['disk_snapshots']:
            try:
                disk_snapshot_obj = m.DiskSnapshot.objects.get(ident=disk_snapshot_ident)
                clean_invalid_data_tasks.append(
                    spaceCollection.DeleteDiskSnapshotTask(
                        spaceCollection.DeleteDiskSnapshotTask.create(
                            disk_snapshot_obj.image_path, disk_snapshot_obj.ident)
                    )
                )
            except m.DiskSnapshot.DoesNotExist:
                pass  # do nothing
        host_disk_info['disk_snapshots'] = list()
        host_disk_info['cdp_tokens'] = list()
        host_disk_info['last_disk_logic_ident'] = None
        self._save_agent_valid_disk_snapshot()

    def force_clean_all_history(self):
        self._dump_info('force_clean_all_history')
        info = self.info_dict
        self.info_dict = dict()
        self._save_agent_valid_disk_snapshot()

        try:
            clean_invalid_data_tasks = list()

            for host_info in info['slaves'].values():
                for host_disk_info in host_info.values():
                    self._clean_history_data(clean_invalid_data_tasks, host_disk_info)

            return clean_invalid_data_tasks
        except Exception as e:
            _logger.error('force_clean_all_history failed {}'.format(e), exc_info=True)

    @staticmethod
    def _check_snapshot_file_exist(host_disk_info):
        """检查快照文件是否存在"""
        for disk_snapshot_ident in host_disk_info['disk_snapshots']:
            try:
                disk_snapshot_obj = m.DiskSnapshot.objects.get(ident=disk_snapshot_ident)
            except m.DiskSnapshot.DoesNotExist:
                raise AgentValidDiskSnapshotInfo.NeedCleanHistoryData(
                    r'_check_agent_valid_disk_snapshot db {} not EXIST'.format(disk_snapshot_ident)
                )
            if not os.path.isfile(disk_snapshot_obj.image_path):
                raise AgentValidDiskSnapshotInfo.NeedCleanHistoryData(
                    r'_check_agent_valid_disk_snapshot file {} not EXIST'.format(disk_snapshot_obj.image_path)
                )

    @staticmethod
    def _check_agent_disk_status(schedule_config, slave_host_infos, host_ident, disk_logic_ident, host_disk_info):
        def _find_native_guid():
            for cluster_disk in schedule_config['cluster_disks']:
                if disk_logic_ident == cluster_disk['ident']:
                    for map_disk in cluster_disk['map_disks']:
                        if host_ident == map_disk['host_ident']:
                            return map_disk['disk_guid']
            raise AgentValidDiskSnapshotInfo.NeedCleanHistoryData(
                '没有磁盘信息， 可能是因为集群盘变少了 {} {}'.format(host_ident, disk_logic_ident))

        slave_host = slave_host_infos.get(host_ident, None)
        if slave_host is None:
            raise AgentValidDiskSnapshotInfo.NeedCleanHistoryData(
                '没有主机信息， 可能是因为主机变少了 {}'.format(host_ident))
        native_guid = _find_native_guid()
        disk_in_host_info = slave_host['disks'].get(native_guid, None)
        if disk_in_host_info is None:
            xlogging.raise_and_logging_error(
                r'在节点主机中，未识别到备份计划中的磁盘',
                r'_check_agent_disk_status can NOT find {}'.format(native_guid))
        if disk_in_host_info['last_status'] == 'base':
            if disk_in_host_info['status_uuid'] not in host_disk_info['disk_snapshots']:
                raise AgentValidDiskSnapshotInfo.NeedCleanHistoryData(
                    '主机磁盘状态不正确 {} - {} - {} host_disk_info : {}'.format(
                        host_ident, native_guid, disk_in_host_info, host_disk_info))
        elif disk_in_host_info['last_status'] == 'cdp':
            if disk_in_host_info['status_uuid'] not in host_disk_info['cdp_tokens']:
                raise AgentValidDiskSnapshotInfo.NeedCleanHistoryData(
                    '主机磁盘状态不正确 {} - {} - {} host_disk_info : {}'.format(
                        host_ident, native_guid, disk_in_host_info, host_disk_info))
        else:  # disk_in_host_info['last_status'] in ("none", "restore", ):
            raise AgentValidDiskSnapshotInfo.NeedCleanHistoryData(
                '主机磁盘状态不正确 {} - {} - {}'.format(host_ident, native_guid, disk_in_host_info))

    def get_history_from_slaves(self):
        """
        {
            cluster_logic_ident : {
                host_ident : {
                    "disk_guid": disk_guid
                    "disk_snapshots":
                    "cdp_tokens":
                    "last_disk_logic_ident":
                }
            }
        }
        """
        if 'slaves' not in self.info_dict:
            return dict()

        result = dict()
        for cluster_logic_ident, all_info in self.info_dict['slaves'].items():
            result[cluster_logic_ident] = dict()
            for host_ident, info in all_info.items():
                result[cluster_logic_ident][host_ident] = {
                    "disk_guid": info['disk_guid'],
                    "disk_snapshots": info["disk_snapshots"].copy(),
                    "cdp_tokens": info["cdp_tokens"].copy(),
                    "last_disk_logic_ident": info['last_disk_logic_ident']
                }
        return result

    def clean_invalid_slave_step_two(self, clean_invalid_data_tasks, base_info):
        if 'slaves' not in self.info_dict:
            return

        self._dump_info('clean_invalid_slave_step_one begin')

        for cluster_disk_ident, all_info in self.info_dict['slaves'].items():
            if base_info.need_clean_slaves(cluster_disk_ident):
                for host_disk_info in all_info.values():
                    self._clean_history_data(clean_invalid_data_tasks, host_disk_info)

        self._dump_info('clean_invalid_slave_step_one end')

    def save_token_info(self, base_info, master_host_obj):
        self.info_dict['master_ident'] = master_host_obj.ident
        if "master" not in self.info_dict:
            self.info_dict["master"] = dict()
        if "slaves" not in self.info_dict:
            self.info_dict["slaves"] = dict()

        self._dump_info('save_token_info begin')

        cdp_tokens = base_info.get_cdp_tokens()['tokens']
        for cluster_disk_ident, cdp_token in cdp_tokens.items():
            for host_ident, agent_token in cdp_token['agent_tokens'].items():
                if host_ident == self.info_dict['master_ident']:
                    if cluster_disk_ident not in self.info_dict["master"]:
                        self.info_dict["master"][cluster_disk_ident] = {
                            "disk_guid": agent_token["disk_guid"],
                            "base_disk_snapshot_ident": None
                        }
                else:
                    if cluster_disk_ident not in self.info_dict["slaves"]:
                        self.info_dict["slaves"][cluster_disk_ident] = dict()
                    if host_ident not in self.info_dict["slaves"][cluster_disk_ident]:
                        self.info_dict["slaves"][cluster_disk_ident][host_ident] = {
                            "disk_guid": agent_token["disk_guid"],
                            "disk_snapshots": list(),
                            "cdp_tokens": list(),
                            "last_disk_logic_ident": None,
                        }
                    self.info_dict["slaves"][cluster_disk_ident][host_ident]["cdp_tokens"].append(
                        agent_token["token"])

        self._dump_info('save_token_info end')
        self._save_agent_valid_disk_snapshot()

    def save_master_full_disk_snapshot(self, cluster_logic_disk_ident, disk_snapshot_ident):
        self._dump_info('save_master_full_disk_snapshot begin')

        _logger.info(r'save_master_full_disk_snapshot {}  {}'.format(cluster_logic_disk_ident, disk_snapshot_ident))

        self.info_dict['master'][cluster_logic_disk_ident]["base_disk_snapshot_ident"] = disk_snapshot_ident

        self._dump_info('save_master_full_disk_snapshot end')

        self._save_agent_valid_disk_snapshot()

    def save_slave_disk_snapshot(
            self, cluster_logic_disk_ident, host_ident, disk_snapshot_ident, agent_disk_logic_ident):
        assert cluster_logic_disk_ident in self.info_dict['slaves']
        assert host_ident in self.info_dict['slaves'][cluster_logic_disk_ident]

        self._dump_info('save_slave_disk_snapshot begin')

        host_level_info = self.info_dict['slaves'][cluster_logic_disk_ident][host_ident]
        disk_snapshots = host_level_info['disk_snapshots']
        disk_snapshots.append(disk_snapshot_ident)
        host_level_info['last_disk_logic_ident'] = agent_disk_logic_ident

        _logger.info(r'save_slave_disk_snapshot {} {} {}'.format(
            cluster_logic_disk_ident, host_ident, disk_snapshot_ident
        ))

        self._dump_info('save_slave_disk_snapshot end')

        self._save_agent_valid_disk_snapshot()

    def clean_old_cdp_token(self):
        self._dump_info('clean_old_cdp_token begin')

        for disk_info in self.info_dict['slaves'].values():
            for host_disk_info in disk_info.values():
                cdp_token = host_disk_info['cdp_tokens'][-1]
                host_disk_info['cdp_tokens'] = [cdp_token, ]

        self._dump_info('clean_old_cdp_token end')

        self._save_agent_valid_disk_snapshot()

    def clean_slave_history(self, cluster_disk_ident):
        disk_info = self.info_dict['slaves'].get(cluster_disk_ident, None)
        if not disk_info:
            return

        self._dump_info('clean_slave_history begin')

        for host_disk_info in disk_info.values():
            host_disk_info['cdp_tokens'] = list()

        self._dump_info('clean_slave_history end')

        self._save_agent_valid_disk_snapshot()

    def clean_slave_qcow_history(self):
        need_clean_qcow_ident_list = list()

        self._dump_info('clean_slave_qcow_history begin')

        for disk_info in self.info_dict['slaves'].values():
            for host_info in disk_info.values():
                need_clean_qcow_ident_list.extend(host_info.get("disk_snapshots", list()))
                host_info["disk_snapshots"] = list()

        self._dump_info('clean_slave_qcow_history end')

        self._save_agent_valid_disk_snapshot()

        return need_clean_qcow_ident_list


class ClusterBackupBaseInfo(object):
    def __init__(self, info=None):
        if info:
            self.info = info
        else:
            self.info = dict()
        """
        {
            "b4b186cf-1d2b-8e63-7952-189f3ad37ff7": {   <--- 集群磁盘的逻辑标识
                "type": , 1:"完整备份" 2:"紧接着成功的增量备份" 3:"有不成功数据的增量备份"
                "name": name
                "master": {                             <---- disk_info
                    "host_ident": host_ident,
                    "storage_dir": path,                                <--- 用来存储数据的目录，已经包含/images/host_ident/的中间目录，直接使用
                    "disk_guid": native_guid,
                    "depend_disk_snapshot_ident": snapshot_ident,       <--- 基于这个点来做增量存储
                    "valid_last_disk_snapshot_ident": snapshot_ident,   <--- 基于这个点来做集群的差异数据分析(开区间)
                    "agent_info": {
                        "last_status": "none", "base", "cdp", "restore"
                        "status_uuid": "", "yyy",
                        "disk_index": int,
                        "disk_bytes": int                                    <---  磁盘大小
                    }
                    "cdp_token_for_agent": token_uuid,
                    "current_disk_snapshot_ident": snapshot_ident,      <--- 本次备份的基础备份点
                },
                "slaves": [
                    {                                   <---- disk_info
                        "host_ident": host_ident,
                        "disk_guid": native_guid,
                        "disk_snapshots" : [
                            snapshot_ident, ... # 只会放qcow
                        ],
                        "can_inc": T / F,
                        "last_disk_logic_ident": ident                 <--- 增量备份需要传递给agent的磁盘逻辑ident
                        "agent_info": {
                            "last_status": "none", "base", "cdp", "restore"
                            "status_uuid": "", "yyy",
                            "disk_index": int,
                            "disk_bytes": int,                               <---  磁盘大小
                        },
                        "cdp_token_for_agent": token_uuid,
                    },
                    ...
                ]
                "cdp_token_for_storage": {    
                    "cdp_token": token,       <--- 用来存储CDP数据的CDP token, CctStartBackup 阶段生成，CctFetchT0 阶段使用
                    "t0_org_file": path,      <--- t0 所在的原始cdp文件，CctFetchT0 生成， CctSplitT0 阶段使用
                    "t0_timestamp": timestamp,<--- t0 时刻
                    "t0_before_path": path,   <--- t0 所在cdp分割后，前半段区域，CctStartBackup 阶段生成， CctSplitT0 阶段使用
                    "t0_after_path": path,    <--- t0 所在cdp分割后，后半段区域，CctStartBackup 阶段生成， CctSplitT0 阶段使用
                                                    注意：可能最后不存在
                },
                "diff_qcow_image": {
                    "file_path": path,        <--- 存储差异数据的qcow文件，CctStartBackup 阶段生成，CctGenerateDiff 阶段使用
                    "snapshot": snapshot, 
                    "org_files": [
                        {"file_path": f, "snapshot": s}, <--- 基于这些快照文件抓取差异，CctGenerateDiff 阶段生成， CCT_CleanTemporary阶段使用
                    ]
                },
            }
        }
        """

    @staticmethod
    def generate_instance(schedule_config, schedule_obj):
        """备份任务首次初始化"""
        r = ClusterBackupBaseInfo()
        r._init_by_schedule_config(schedule_config, schedule_obj)
        return r

    def _dump_info(self, msg):
        _logger.info(r'{} : {}'.format(msg, json.dumps(self.info, ensure_ascii=False)))

    def _init_by_schedule_config(self, schedule_config, schedule_obj):
        _storage_node_base_path = m.StorageNode.objects.get(ident=schedule_obj.storage_node_ident).path
        master_node = schedule_config['master_node']
        for cluster_disk in schedule_config['cluster_disks']:
            r = {
                "name": cluster_disk['name'],
                "master": {
                    "host_ident": master_node,
                    "storage_dir": os.path.join(_storage_node_base_path, 'images', master_node),
                    "depend_disk_snapshot_ident": None,
                    "valid_last_disk_snapshot_ident": None,
                },
                "slaves": list()
            }
            uuid_ident = uuid.uuid4().hex
            r["diff_qcow_image"] = {
                "file_path": os.path.join(r['master']['storage_dir'], '{}.qcow'.format(uuid_ident)),
                "snapshot": uuid_ident,
                "org_files": list(),
            }
            for map_disk in cluster_disk['map_disks']:
                if map_disk['host_ident'] == master_node:
                    r['master']['disk_guid'] = map_disk['disk_guid']
                else:
                    r['slaves'].append({
                        "host_ident": map_disk['host_ident'],
                        "disk_guid": map_disk['disk_guid'],
                        "disk_snapshots": list()
                    })
            assert r['master']['disk_guid']
            self.info[cluster_disk['ident']] = r

    def _get_disk_info_in_master_by_native_guid(self, disk_guid) -> (str, dict):
        """
        :return:
            cluster_logic_ident, disk_info
        """
        for cluster_logic_ident, all_disk_info in self.info.items():
            if all_disk_info['master']['disk_guid'] == disk_guid:
                return cluster_logic_ident, all_disk_info['master']
        return None, None

    def _get_disk_info_in_slave_by_native_guid(self, host_ident, disk_guid):
        for cluster_logic_ident, all_disk_info in self.info.items():
            for slave in all_disk_info['slaves']:
                if slave['disk_guid'] == disk_guid and slave['host_ident'] == host_ident:
                    return cluster_logic_ident, slave
        return None, None

    def get_cluster_disks_cdp_token(self):
        """
        :return:
            [token_str, ...]
        """
        return [info['cdp_token_for_storage']['cdp_token'] for info in self.info.values()]

    def set_host_info(self, master_host_info, slave_host_infos):
        """设置agent上报的信息"""
        for disk_guid, info in master_host_info['disks'].items():
            cluster_logic_ident, disk_info = self._get_disk_info_in_master_by_native_guid(disk_guid)
            if cluster_logic_ident:
                disk_info['agent_info'] = {
                    "last_status": info['last_status'],
                    "status_uuid": info['status_uuid'],
                    "disk_index": info['disk_index'],
                    "disk_bytes": info['disk_bytes'],
                }
        for host_ident, slave_host_info in slave_host_infos.items():
            for disk_guid, info in slave_host_info['disks'].items():
                cluster_logic_ident, disk_info = self._get_disk_info_in_slave_by_native_guid(host_ident, disk_guid)
                if cluster_logic_ident:
                    disk_info['agent_info'] = {
                        "last_status": info['last_status'],
                        "status_uuid": info['status_uuid'],
                        "disk_index": info['disk_index'],
                        "disk_bytes": info['disk_bytes'],
                    }
        """检查所有磁盘信息是否都填写"""
        for all_disk_info in self.info.values():
            if 'agent_info' not in all_disk_info['master']:
                xlogging.raise_and_logging_error(
                    '未检测到集群磁盘：{}'.format(all_disk_info['name']), 'agent_info not exist : {}'.format(self.info))
            for slave in all_disk_info['slaves']:
                if 'agent_info' not in slave:
                    xlogging.raise_and_logging_error(
                        '未检测到集群磁盘：{}'.format(all_disk_info['name']),
                        'agent_info not exist : {}'.format(self.info))
        self._dump_info('set_host_info 设置agent上报的信息')

    def set_depend_disk_snapshots(self, depend_disk_snapshots):
        """设置备份时依赖的快照"""
        for cluster_logic_ident, disk_snapshot_info in depend_disk_snapshots.items():
            info = self.info.get(cluster_logic_ident, None)
            if info is None:
                _logger.warning(r'skip depend_disk_snapshot : {} {}'.format(cluster_logic_ident, disk_snapshot_info))
                continue
            info['master']['depend_disk_snapshot_ident'] = \
                disk_snapshot_info.get('depend_disk_snapshot_ident', None)
            info['master']['valid_last_disk_snapshot_ident'] = \
                disk_snapshot_info.get('valid_last_disk_snapshot_ident', None)
        self._dump_info('set_depend_disk_snapshots 设置备份时依赖的快照')

    def set_agent_valid_disk_snapshot(self, agent_valid_disk_snapshot):
        """设置从节点的历史数据"""
        for cluster_logic_ident, all_info in agent_valid_disk_snapshot.get_history_from_slaves().items():
            for host_ident, info in all_info.items():
                logic_ident, disk_info = self._get_disk_info_in_slave_by_native_guid(host_ident, info['disk_guid'])
                if logic_ident:
                    assert cluster_logic_ident == logic_ident
                    disk_info["disk_snapshots"] = info["disk_snapshots"]
                    disk_info["can_inc"] = bool(info["cdp_tokens"])
                    if disk_info["can_inc"]:
                        disk_info["last_disk_logic_ident"] = info["last_disk_logic_ident"]
                        assert disk_info["last_disk_logic_ident"]
                    else:
                        disk_info["last_disk_logic_ident"] = None
        self._dump_info('set_agent_valid_disk_snapshot 设置从节点的历史数据')

    def analyze_type(self, force_full):
        """分析备份的类型"""

        def _check_need_full():
            last_status = all_disk_info['master']['agent_info']['last_status']
            if last_status not in ('base', 'cdp',):
                _logger.info('{} need full, because last_status {}'.format(all_disk_info['name'], last_status))
                return True
            depend_disk_snapshot_ident = all_disk_info['master'].get('depend_disk_snapshot_ident', None)
            if not depend_disk_snapshot_ident:
                _logger.info('{} need full, because depend_disk_snapshot_ident'.format(all_disk_info['name']))
                return True
            valid_last_disk_snapshot_ident = all_disk_info['master'].get('valid_last_disk_snapshot_ident', None)
            if not valid_last_disk_snapshot_ident:
                _logger.info('{} need full, because valid_last_disk_snapshot_ident'.format(all_disk_info['name']))
                return True
            for disk_info in all_disk_info['slaves']:
                if not disk_info.get('can_inc', False):
                    _logger.info(
                        '{} need full, because slaves {}'.format(all_disk_info['name'], disk_info['host_ident']))
                    return True
            return False

        def _set_type_1():
            all_disk_info['type'] = 1
            all_disk_info['master']['valid_last_disk_snapshot_ident'] = None
            for slave_disk_info in all_disk_info['slaves']:
                slave_disk_info['can_inc'] = False
                slave_disk_info['disk_snapshots'] = list()

        def _set_type_2():
            all_disk_info['type'] = 2

        def _set_type_3():
            all_disk_info['type'] = 3

        if force_full:
            for cluster_logic_ident, all_disk_info in self.info.items():
                _set_type_1()
        else:
            for cluster_logic_ident, all_disk_info in self.info.items():
                if _check_need_full():
                    _set_type_1()
                else:
                    if (all_disk_info['master']['valid_last_disk_snapshot_ident']
                            == all_disk_info['master']['depend_disk_snapshot_ident']):
                        _set_type_2()
                    else:
                        _set_type_3()

    def need_clean_slaves(self, cluster_disk_ident):
        """判断从节点的集群磁盘是否需要清理历史数据"""
        cluster_disk_info = self.info.get(cluster_disk_ident, None)
        if not cluster_disk_info:
            return True
        return cluster_disk_info['type'] == 1

    def generate_cdp_token(self):
        """生成cdp token字符串"""
        for cluster_info in self.info.values():
            cluster_info["master"]['cdp_token_for_agent'] = uuid.uuid4().hex
            for slave in cluster_info['slaves']:
                slave["cdp_token_for_agent"] = uuid.uuid4().hex
            cluster_info["cdp_token_for_storage"] = {
                "cdp_token": uuid.uuid4().hex,
                "t0_before_path":
                    os.path.join(cluster_info['master']['storage_dir'], r'{}.cdp'.format(uuid.uuid4().hex)),
                "t0_after_path":
                    os.path.join(cluster_info['master']['storage_dir'], r'{}.cdp'.format(uuid.uuid4().hex)),
            }
        self._dump_info('generate_cdp_token')

    def get_cdp_token_for_storage_list(self):
        """
        [
            {
                "cdp_token": token,       <--- 用来存储CDP数据的CDP token, CctStartBackup 阶段生成，CctFetchT0 阶段使用
                "t0_org_file": path,      <--- t0 所在的原始cdp文件，CctFetchT0 生成， CctSplitT0 阶段使用
                "t0_timestamp": timestamp,<--- t0 时刻
                "t0_before_path": path,
                "t0_after_path": path,
                "t1_timestamp": timestamp <--- t1 时刻，CctQueryT1 阶段生成， CctCreateHostSnapshot 阶段使用
            }
            ...
        ]
        """
        return [info['cdp_token_for_storage'] for info in self.info.values()]

    def get_cdp_tokens(self):
        """
        {
            "master_info": {
                "host_ident": host_ident                <--- 主节点主机
                "storage_dir": path                     <--- 存储节点路径
            },
            "tokens" : {
                "b4b186cf-1d2b-8e63-7952-189f3ad37ff7": {   <--- 集群磁盘的逻辑标识
                    "disk_bytes": 123                       <--- 磁盘大小
                    "token_for_storage": token,             <--- 存储数据用的token
                    "agent_tokens": {
                        "host_ident_xxxxx": {
                            "disk_index": 1
                            "token": token                  <--- 发送给agent的token
                            "disk_guid": native_guid
                        }
                    }
                }
            }
        }
        """
        result = {
            "master_info": None,
            "tokens": dict(),
        }
        for cluster_disk_ident, cluster_info in self.info.items():
            if not result["master_info"]:
                result["master_info"] = {
                    "host_ident": cluster_info["master"]["host_ident"],
                    "storage_dir": cluster_info["master"]["storage_dir"],
                }
            master_disk_info = cluster_info["master"]
            token_info = {
                "disk_bytes": master_disk_info["agent_info"]["disk_bytes"],
                "token_for_storage": cluster_info["cdp_token_for_storage"]["cdp_token"],
                "agent_tokens": {
                    master_disk_info["host_ident"]: {
                        "disk_index": master_disk_info["agent_info"]["disk_index"],
                        "token": master_disk_info["cdp_token_for_agent"],
                        "disk_guid": master_disk_info["disk_guid"],
                    },
                },
            }
            for slave_disk_info in cluster_info["slaves"]:
                token_info['agent_tokens'][slave_disk_info["host_ident"]] = {
                    "disk_index": slave_disk_info["agent_info"]["disk_index"],
                    "token": slave_disk_info["cdp_token_for_agent"],
                    "disk_guid": slave_disk_info["disk_guid"],
                }
            result["tokens"][cluster_disk_ident] = token_info
        return result

    def generate_cluster_disks_for_backup_sub_task(self, host_ident, is_master):
        """
        {
            "master_node": T/F,             是否是主节点
            "xxx_native_guid": {            native_guid
                "agent_force_full": T/F,    是否做agent完整数据扫描
                "cdp_token": yyy,           cdp_token
                "last_snapshot_ident": abc, 依赖的磁盘快照，仅仅主节点传入该值
                "snapshot_chain": [         依赖链，仅从节点传入该值
                    {"path": ppp, "ident": iii},
                    ...
                ],
                "disk_logic_ident"：        仅从节点传入该值
            },
            ...
        }
        """
        result = dict()
        if is_master:
            result["master_node"] = True
            for cluster_info in self.info.values():
                master_disk_info = cluster_info["master"]
                assert host_ident == master_disk_info["host_ident"]
                disk_for_task = {
                    "agent_force_full": cluster_info["type"] == 1,
                    "cdp_token": master_disk_info["cdp_token_for_agent"],
                    "last_snapshot_ident": master_disk_info.get("depend_disk_snapshot_ident", None),
                }
                result[master_disk_info["disk_guid"]] = disk_for_task
        else:
            result["master_node"] = False
            for cluster_info in self.info.values():
                for slave_disk_info in cluster_info['slaves']:
                    if slave_disk_info["host_ident"] == host_ident:
                        break
                else:
                    assert False, r'never happened, can NOT find host ?! ' \
                                  r'generate_cluster_disks_for_backup_sub_task {} {}'.format(self.info, host_ident)

                disk_for_task = {
                    "agent_force_full": cluster_info["type"] == 1,
                    "cdp_token": slave_disk_info["cdp_token_for_agent"],
                    "snapshot_chain": slave_disk_info.get("disk_snapshots", list()),
                    "disk_logic_ident": slave_disk_info.get("last_disk_logic_ident", None),
                }
                result[slave_disk_info["disk_guid"]] = disk_for_task
        return result

    def generate_diff_snapshots_params(self):
        """
        {
            "qcows": [{
                "host_ident": "xxxx",
                "disk_id": 0,
                "source_snapshots": [{
                    "path": "/xx/zz.cdp",
                    "ident": "all"
                }, {
                    "path": "/xx/yy.qcow",
                    "ident": "iiii"
                },
                 ...
                 ],
                 "source_snapshots_hash_files":[hash_path1, ]
                "hash_path": "\path\path",
                "new_qcow_hash": "\path\path",
                "new_qcow": {
                    "path": "/xx/yy.qcow",
                    "ident": "iiii",
                                "disk_bytes": 111
                }
            },
            ...
            ]
        }
        """
        result = dict(qcows=list())
        for cluster_disk in self.info.values():
            item = dict()
            item['host_ident'] = cluster_disk['master']['host_ident']
            item['disk_id'] = cluster_disk['master']['agent_info']['disk_index']
            item['source_snapshots'], item[
                'source_snapshots_hash_files'] = self._query_diff_snapshots(cluster_disk)
            item['hash_path'] = os.path.join(os.path.dirname(cluster_disk['diff_qcow_image']['file_path']),
                                             '{}.diff.hash'.format(uuid.uuid4().hex))
            item['new_qcow_hash'] = '{}_{}.hash'.format(cluster_disk['diff_qcow_image']['file_path'],
                                                        cluster_disk['diff_qcow_image']['snapshot'])
            item['new_qcow'] = {
                "path": cluster_disk['diff_qcow_image']['file_path'],
                "ident": cluster_disk['diff_qcow_image']['snapshot'],
                "disk_bytes": m.DiskSnapshot.objects.get(
                    ident=cluster_disk['master']['current_disk_snapshot_ident']).bytes
            }
            result['qcows'].append(item)
        return result

    @staticmethod
    def _fetch_snapshots(disk_snapshot, include_all_node=False):
        validator_list = [
            snapshot.GetSnapshotList.is_disk_snapshot_file_exist,
            snapshot.GetSnapshotList.fix_disk_snapshot_hash_file]
        return snapshot.GetSnapshotList.query_snapshots_by_snapshot_object_with_hash_file(
            disk_snapshot, validator_list, include_all_node=include_all_node)

    @staticmethod
    def _query_diff_snapshots(cluster_disk):
        """
        :param cluster_disk: base info one cluster info
        :return: snapshots, hash_files
        """

        def _ice2dict(obj):
            return {'path': obj.path, 'ident': obj.snapshot}

        base_snapshot_chain, base_snapshot_chain_hash = ClusterBackupBaseInfo._query_diff_snapshots4base_part(
            cluster_disk)
        cdp_snapshot_chain, cdp_snapshot_chain_hash = ClusterBackupBaseInfo._query_diff_snapshots4cdp_part(
            cluster_disk)
        slaves_snapshot_chain, slaves_snapshot_chain_hash = ClusterBackupBaseInfo._query_diff_snapshots4slaves_part(
            cluster_disk)

        result_snapshots = base_snapshot_chain + cdp_snapshot_chain + slaves_snapshot_chain
        result_hash_files = base_snapshot_chain_hash + cdp_snapshot_chain_hash + slaves_snapshot_chain_hash
        _logger.info(
            'token {} diff snapshots {}'.format(cluster_disk['cdp_token_for_storage']['cdp_token'], result_snapshots))
        result_snapshots = [_ice2dict(sn) for sn in result_snapshots]
        return result_snapshots, result_hash_files

    @staticmethod
    def _query_diff_snapshots4cdp_part(cluster_disk):
        # cdp流的快照链，去掉尾部t0_cdp_disk_snapshot和去掉头部伪造的qcow
        t0_cdp_disk_snapshot = m.DiskSnapshot.objects.get(
            image_path=cluster_disk['cdp_token_for_storage']['t0_org_file'])
        t0_before_path = cluster_disk['cdp_token_for_storage']['t0_before_path']
        current_snapshot = t0_cdp_disk_snapshot
        t0_cdp_disk_snapshot_chain, t0_cdp_disk_snapshot_chain_hash = list(), list()
        while current_snapshot:
            if current_snapshot.is_cdp:
                t0_cdp_disk_snapshot_chain.append(
                    IMG.ImageSnapshotIdent(current_snapshot.image_path, 'all'))
                t0_cdp_disk_snapshot_chain_hash.append('cdp|{}|all'.format(current_snapshot.image_path))
            else:
                t0_cdp_disk_snapshot_chain.append(
                    IMG.ImageSnapshotIdent(current_snapshot.image_path, current_snapshot.ident))
                t0_cdp_disk_snapshot_chain_hash.append(current_snapshot.hash_path)
            current_snapshot = current_snapshot.parent_snapshot
        t0_cdp_disk_snapshot_chain.reverse()
        t0_cdp_disk_snapshot_chain_hash.reverse()
        _logger.info('token {} before fix cdp chain {}'.format(cluster_disk['cdp_token_for_storage']['cdp_token'],
                                                               t0_cdp_disk_snapshot_chain))
        if not t0_cdp_disk_snapshot_chain:
            xlogging.raise_and_logging_error('获取差异快照链失败', 't0_cdp_disk_snapshot_chain is empty')
        t0_cdp_disk_snapshot_chain = t0_cdp_disk_snapshot_chain[1:-1]  # 去掉头尾
        t0_cdp_disk_snapshot_chain_hash = t0_cdp_disk_snapshot_chain_hash[1:-1]
        t0_cdp_disk_snapshot_chain.append(IMG.ImageSnapshotIdent(t0_before_path, 'all'))
        t0_cdp_disk_snapshot_chain_hash.append('cdp|{}|{}'.format(t0_before_path, 'all'))
        _logger.info('token {} after fix cdp chain {}'.format(cluster_disk['cdp_token_for_storage']['cdp_token'],
                                                              t0_cdp_disk_snapshot_chain))
        return t0_cdp_disk_snapshot_chain, t0_cdp_disk_snapshot_chain_hash

    @staticmethod
    def _query_diff_snapshots4base_part(cluster_disk):
        # 基础备份的快照链, （valid_last_disk_snapshot_ident， current_disk_snapshot_ident]之间的链，两者一样为空
        base_disk_snapshot = m.DiskSnapshot.objects.get(ident=cluster_disk['master']['current_disk_snapshot_ident'])
        base_snapshot_chain, base_snapshot_chain_hash = ClusterBackupBaseInfo._fetch_snapshots(base_disk_snapshot, True)
        if not base_snapshot_chain:
            xlogging.raise_and_logging_error('获取差异快照链失败', 'base_snapshot_chain is empty')
        _logger.info(
            'token {} before fix base snapshot chain {}'.format(cluster_disk['cdp_token_for_storage']['cdp_token'],
                                                                base_snapshot_chain))

        def _get_disk_snapshot(ice_snapshot):
            if m.DiskSnapshot.is_cdp_file(ice_snapshot.path):
                return m.DiskSnapshot.objects.get(image_path=ice_snapshot.path)
            else:
                return m.DiskSnapshot.objects.get(ident=ice_snapshot.snapshot)

        for index, _snapshot in enumerate(base_snapshot_chain):
            _disk_snapshot = _get_disk_snapshot(_snapshot)
            if _disk_snapshot.ident == cluster_disk['master']['valid_last_disk_snapshot_ident']:
                break
        else:
            xlogging.raise_and_logging_error('获取差异快照链失败', 'not found valid_last_disk_snapshot_ident')
            return list(), list()
        base_snapshot_chain, base_snapshot_chain_hash = base_snapshot_chain[index + 1:], base_snapshot_chain_hash[
                                                                                         index + 1:]
        _logger.info(
            'token {} after fix base snapshot chain {}'.format(cluster_disk['cdp_token_for_storage']['cdp_token'],
                                                               base_snapshot_chain))
        return base_snapshot_chain, base_snapshot_chain_hash

    @staticmethod
    def _query_diff_snapshots4slaves_part(cluster_disk):
        slaves_snapshot_chain, slaves_snapshot_chain_hash = list(), list()
        _logger.info('_query_diff_snapshots4slaves_part cluster_disk:{}'.format(cluster_disk))
        for slave in cluster_disk['slaves']:
            for snapshot_ident in slave['disk_snapshots']:
                disk_snapshot = m.DiskSnapshot.objects.get(ident=snapshot_ident)
                if not snapshot.GetSnapshotList.fix_disk_snapshot_hash_file(disk_snapshot):
                    xlogging.raise_and_logging_error('获取差异快照链失败', 'fix hash file failed {}'.format(disk_snapshot))
                if disk_snapshot.is_cdp:
                    slaves_snapshot_chain.append(IMG.ImageSnapshotIdent(disk_snapshot.image_path, 'all'))
                    slaves_snapshot_chain_hash.append('cdp|{}|all'.format(disk_snapshot.image_path))
                else:
                    slaves_snapshot_chain.append(IMG.ImageSnapshotIdent(disk_snapshot.image_path, disk_snapshot.ident))
                    slaves_snapshot_chain_hash.append(disk_snapshot.hash_path)
        _logger.info(
            'token {} slaves_snapshot_chain {}'.format(cluster_disk['cdp_token_for_storage']['cdp_token'],
                                                       slaves_snapshot_chain))
        return slaves_snapshot_chain, slaves_snapshot_chain_hash

    def get_current_master_host_snapshot(self):
        for cluster_disk in self.info.values():
            disk_snapshot = m.DiskSnapshot.objects.get(
                ident=cluster_disk['master']['current_disk_snapshot_ident'])
            return disk_snapshot.host_snapshot

    # 仅供CctCreateHostSnapshot调用
    def load_cluster_disk_info(self, disk_snapshot):
        for cluster_disk in self.info.values():
            if cluster_disk['master']['current_disk_snapshot_ident'] == disk_snapshot.ident:
                break
        else:
            return None, None, None
        valid_last_disk_snapshot = m.DiskSnapshot.objects.get(
            ident=cluster_disk['master']['valid_last_disk_snapshot_ident'])

        diff_disk_snapshot = m.DiskSnapshot.objects.create(ident=cluster_disk['diff_qcow_image']['snapshot'],
                                                           image_path=cluster_disk['diff_qcow_image']['file_path'],
                                                           disk=disk_snapshot.disk,
                                                           bytes=disk_snapshot.bytes,
                                                           type=disk_snapshot.type,
                                                           boot_device=disk_snapshot.boot_device)
        t0_cdp_disk_snapshot = m.DiskSnapshot.objects.get(
            image_path=cluster_disk['cdp_token_for_storage']['t0_org_file'])
        t0_after_path = cluster_disk['cdp_token_for_storage']['t0_after_path']
        if boxService.box_service.isFileExist(t0_after_path):
            ident = os.path.split(t0_after_path.strip('.cdp'))[1]
            t0_after_disk_snapshot = m.DiskSnapshot.objects.create(image_path=t0_after_path,
                                                                   disk=t0_cdp_disk_snapshot.disk,
                                                                   ident=ident,
                                                                   bytes=t0_cdp_disk_snapshot.bytes,
                                                                   type=t0_cdp_disk_snapshot.type,
                                                                   boot_device=t0_cdp_disk_snapshot.boot_device)
            m.DiskSnapshotCDP.objects.create(disk_snapshot=t0_after_disk_snapshot,
                                             token=t0_cdp_disk_snapshot.cdp_info.token,
                                             first_timestamp=t0_cdp_disk_snapshot.cdp_info.first_timestamp,
                                             # 这个不准确，但是无所谓（界面无法看到这个点，所以没关系的）
                                             last_timestamp=t0_cdp_disk_snapshot.cdp_info.last_timestamp)
            t0_cdp_disk_snapshot.child_snapshots.all().update(parent_snapshot=t0_after_disk_snapshot)

        else:
            if t0_cdp_disk_snapshot.child_snapshots.count() != 1:
                xlogging.raise_and_logging_error('获取集群磁盘信息失败', 't0_cdp_disk_snapshot not have child')
            t0_after_disk_snapshot = t0_cdp_disk_snapshot.child_snapshots.first()

        # 修正集群盘cdp toekn 的 parent_snapshot 为 diff_dis_snapshot
        token_obj = t0_cdp_disk_snapshot.cdp_info.token
        _logger.debug('update token {} parent_disk_snapshot {} to {}'.format(token_obj, token_obj.parent_disk_snapshot,
                                                                             diff_disk_snapshot))
        token_obj.parent_disk_snapshot = diff_disk_snapshot
        token_obj.save(update_fields=['parent_disk_snapshot'])

        return valid_last_disk_snapshot, diff_disk_snapshot, t0_after_disk_snapshot

    def set_diff_qcow_image_snapshots(self, disk_id, org_files):
        for cluster_disk in self.info.values():
            if cluster_disk['master']['agent_info']['disk_index'] == disk_id:
                cluster_disk['diff_qcow_image']['org_files'] = org_files

    def set_master_current_disk_snapshot_ident(self, cluster_logic_disk_ident, disk_snapshot_ident):
        assert cluster_logic_disk_ident in self.info

        self._dump_info('set_master_current_disk_snapshot_ident bgein')

        master_disk_info = self.info[cluster_logic_disk_ident]['master']

        master_disk_info['current_disk_snapshot_ident'] = disk_snapshot_ident

        if not master_disk_info.get('valid_last_disk_snapshot_ident', None):
            _logger.info(r'set_valid_last_disk_snapshot_ident_when_it_null {} {}'.format(
                cluster_logic_disk_ident, disk_snapshot_ident))
            master_disk_info['valid_last_disk_snapshot_ident'] = disk_snapshot_ident

        self._dump_info('set_master_current_disk_snapshot_ident end')

    @staticmethod
    def _get_disk_info_in_slave_by_host_ident(cluster_info, host_ident):
        for slave in cluster_info['slaves']:
            if slave['host_ident'] == host_ident:
                return slave
        return None

    def set_slave_current_disk_snapshot_ident(self, cluster_logic_disk_ident, host_ident, disk_snapshot_ident):
        self._dump_info('set_slave_current_disk_snapshot_ident begin')

        assert cluster_logic_disk_ident in self.info
        disk_info = self._get_disk_info_in_slave_by_host_ident(self.info[cluster_logic_disk_ident], host_ident)
        assert disk_info

        disk_info['disk_snapshots'].append(disk_snapshot_ident)

        self._dump_info('set_slave_current_disk_snapshot_ident end')

    def get_master_disks(self):
        """
        host_ident
        [disk_index, ...]
        """
        host_ident = None
        disk_list = list()
        for info in self.info.values():
            master = info["master"]
            if not host_ident:
                host_ident = master["host_ident"]
            assert host_ident == master["host_ident"], \
                r'not the same host_ident: {} -> {}'.format(host_ident, master["host_ident"])
            disk_list.append(master["agent_info"]["disk_index"])
        return host_ident, disk_list

    def record_t0(self, t0_list):
        def _find_cdp_token_for_storage(token):
            for cluster_info in self.info.values():
                if cluster_info["cdp_token_for_storage"]["cdp_token"] == token:
                    return cluster_info["cdp_token_for_storage"]
            else:
                xlogging.raise_and_logging_error(
                    r'分析磁盘信息失败，无效的磁盘Token', 'never happen, {} {}'.format(token, self.info))

        for t0 in t0_list:
            ci = _find_cdp_token_for_storage(t0['token'])
            ci['t0_org_file'] = t0['file']
            ci['t0_timestamp'] = t0['time']

        self._dump_info('record_t0 记录t0信息')

    def get_current_backup_info(self):
        """
        {
            "b4b186cf-1d2b-8e63-7952-189f3ad37ff7": {   <--- 集群磁盘的逻辑标识
                "current_disk_snapshot_ident": ident, 本次备份主节点上的磁盘快照，可能为空
                "depend_disk_snapshot_ident": ident, 直接依赖的磁盘快照，可能为空
                "cdp_token_for_storage": token, 本次备份的 cdp token
            }
        }
        """
        result = dict()
        for cluster_disk_ident, cluster_info in self.info.items():
            result[cluster_disk_ident] = {
                "current_disk_snapshot_ident": (
                    cluster_info['master'].get("current_disk_snapshot_ident", None) if "master" in cluster_info
                    else None),
                "depend_disk_snapshot_ident": (
                    cluster_info['master'].get("depend_disk_snapshot_ident", None) if "master" in cluster_info
                    else None),
                "cdp_token_for_storage": (
                    cluster_info['cdp_token_for_storage'].get("cdp_token", None)
                    if "cdp_token_for_storage" in cluster_info else None),
            }
        return result

    def get_diff_qcow_org_files(self):
        rs = list()
        for cluster_disk in self.info.values():
            rs.extend(cluster_disk['diff_qcow_image']['org_files'])
        return rs

    def get_t0_files(self):
        rs = list()
        for cluster_disk in self.info.values():
            rs.append(cluster_disk['cdp_token_for_storage']['t0_org_file'])
        return rs


class TaskContextHelper(object):
    def __init__(self, task_context):
        self.context = task_context
        """
        将task_context字典封装在本类中进行调用
        {
            'error': None / (err_msg, err_debug, ), <--- 当error为非None时有效
            'err_step': str,                        <--- 当error为非None时有效，记录发生错误的阶段
            'cluster_info': dict,                   <--- 存储ClusterBackupBaseInfo的原始数据
            'master_host_ident': ident,             <--- 主节点ident
            'lock_info': [disk_snapshot_ident, ...],<--- 锁定的磁盘快照，CctStartBackup阶段初始化, CctUnlockResource阶段将改值设置为None
            'backup_sub_tasks': [                   <--- 备份任务相关
                {
                    "host_ident": host_ident,
                    "host_force_full": T/F,         agent 是否进行完整备份      (只影响到非集群盘)
                    "disable_optimize": T/F,        agent 是否禁用网络优化      (只影响到非集群盘)
                    "force_store_full": T/ F,       服务器是否强制完整存储      (只影响到非集群盘)
                    "cluster_disks": {
                        "master_node": T/F,         是否是主节点
                        "xxx": {                        native_guid
                            "agent_force_full": T/F,    是否做agent完整数据扫描
                            "cdp_token": yyy,           cdp_token
                            "last_snapshot_ident": abc, 依赖的磁盘快照，仅主节点传入该值
                            "snapshot_chain": [         依赖链，仅从节点传入该值
                                {"path": ppp, "ident": iii},
                                ...
                            "disk_logic_ident":         agent磁盘逻辑ident，仅从节点传入该值
                            ],
                        },
                        ...
                    }
                },
                ...
            ],   
            't0_must_after_timestamp': float,       <--- t0 时刻必须晚于该时刻，在 CctSendBackupCmd 阶段设置
            'normal_backup_successful': bool,       <--- 基础备份完成，在 CctBackup 阶段设置成功
            't1_timestamp': float,                  <--- t1 时刻， 在 CctQueryT1 阶段设置
            'create_host_snapshot_successful': bool,    <--- 创建主机快照完成 在 CctCreateHostSnapshotSuccessful 阶段设置
            'new_host_snapshot_id': int,            <--- CctCreateHostSnapshot阶段 初始化
            'need_clean_qcow_ident_list': [qcow_ident, ...], <--- CctCreateHostSnapshot阶段 初始化                                
        }
        """

    def set_error(self, error):
        self.context['error'] = error

    def get_err_msg(self):
        _err = self.context.get('error', None)
        if _err:
            return _err[0]
        else:
            return None

    def get_err_debug(self):
        _err = self.context.get('error', None)
        if _err:
            return _err[1]
        else:
            return None

    def set_error_step(self, err_step):
        self.context['err_step'] = err_step

    def get_error_step(self):
        return self.context.get('err_step', None)

    def has_error(self):
        return self.context.get('error', None) is not None

    def get_master_host_ident(self):
        return self.context.get('master_host_ident', None)

    def get_backup_sub_tasks(self):
        return self.context.get('backup_sub_tasks', None)

    def get_cluster_info(self):
        return self.context.get('cluster_info', None)

    def confirm_cluster_info_is_in_task_contest(self):
        return self.get_cluster_info() is not None

    def set_t0_must_after_timestamp(self, _timestamp):
        self.context['t0_must_after_timestamp'] = _timestamp

    def get_t0_must_after_timestamp(self):
        return self.context.get('t0_must_after_timestamp', None)

    def set_normal_backup_successful(self):
        self.context['normal_backup_successful'] = True

    def is_normal_backup_successful(self):
        return self.context.get('normal_backup_successful', False)

    def set_t1_timestamp(self, timestamp):
        self.context['t1_timestamp'] = timestamp

    def get_t1_timestamp(self):
        return self.context.get('t1_timestamp', None)

    def set_create_host_snapshot_successful(self):
        self.context['create_host_snapshot_successful'] = True

    def is_create_host_snapshot_successful(self):
        return self.context.get('create_host_snapshot_successful', False)

    def set_new_host_snapshot_id(self, snapshot_id):
        self.context['new_host_snapshot_id'] = snapshot_id

    def set_need_clean_qcow_ident_list(self, qcow_ident_list):
        self.context['need_clean_qcow_ident_list'] = qcow_ident_list

    def get_need_clean_qcow_ident_list(self):
        return self.context.get('need_clean_qcow_ident_list', list())

    def get_lock_info(self):
        return self.context.get('lock_info', None)

    def clear_lock_info(self):
        self.context['lock_info'] = None


class CctStartBackup(task.Task):
    default_provides = 'task_context'

    def __init__(self, name, task_id, inject=None):
        super(CctStartBackup, self).__init__(r'CctStartBackup {}'.format(name), inject=inject)
        self._task_id = task_id
        self._status_code = r'CctStartBackup'
        self._status_desc = xdata.get_type_name(STATUS_MAP, self._status_code)

    def execute(self, *args, **kwargs):
        lock_info = {
            'task_name': 'cluster_cdp_{}'.format(self._task_id),
            'snapshots_idents': list(),
        }
        master_host_obj = None

        try:
            cluster_cdp_task_obj, cluster_cdp_task_config, schedule_obj, schedule_config = load_objs(self._task_id)

            _check_run_twice(cluster_cdp_task_obj, 'CctStartBackup')

            (disable_optimize, force_full, force_store_full, master_host_obj, slave_host_objs) = \
                self._load_base_info(cluster_cdp_task_config, schedule_config, schedule_obj)

            ClusterCdpTaskExecutor.set_status_and_create_log(self._task_id,
                                                             master_host_obj.ident,
                                                             self._status_code,
                                                             self._status_desc)

            agent_valid_disk_snapshot = AgentValidDiskSnapshotInfo(schedule_obj)
            base_info = ClusterBackupBaseInfo.generate_instance(schedule_config, schedule_obj)

            master_host_info, slave_host_infos = self._query_agent_info(master_host_obj, slave_host_objs)
            base_info.set_host_info(master_host_info, slave_host_infos)

            _logger.info(r'检查主节点历史数据状态，主节点对应的历史数据')
            # TODO 事实上，主节点可增量备份不意味着可以基于最新的备份点进行备份。 例如：有其他备份计划进行过备份
            #       这里未检测这类可能，暂时依靠人肉来手工解决

            # 锁定“从host找最新的、含不可见的本计划产生的集群cdp主机快照”的所有磁盘快照点，也就是包含了最近可用来做增量存储的数据
            depend_disk_snapshots = self._fetch_depend_disk_snapshots(
                master_host_obj, schedule_obj, schedule_config, lock_info)
            _logger.info(r'可进行增量存储的磁盘快照信息：{}'.format(
                json.dumps(depend_disk_snapshots, ensure_ascii=False)))

            # 从base_backup找到最后一次进行完整备份的数据
            #   该机制是为了处理：基于已有数据的“完整备份”时，基础备份失败；那么下次备份应该对这次备份数据后的数据都做差异数据抓取
            #   即便多次失败，同样都是首次备份后的数据都需要进行差异数据抓取
            self._fetch_base_disk_snapshots(depend_disk_snapshots, agent_valid_disk_snapshot)
            _logger.info(r'最后一次完整备份的磁盘快照信息：{}'.format(
                json.dumps(depend_disk_snapshots, ensure_ascii=False)))

            # 从host找最新的可见的cdp主机快照（本计划），也就是基础备份成功的备份数据"
            self._fetch_prev_disk_snapshots(depend_disk_snapshots, master_host_obj, schedule_obj, schedule_config)
            _logger.info(r'最后一次成功备份的磁盘快照信息：{}'.format(
                json.dumps(depend_disk_snapshots, ensure_ascii=False)))

            self._calc_valid_last_disk_snapshots(depend_disk_snapshots)
            _logger.info(r'进行差异数据分析的磁盘快照信息：{}'.format(
                json.dumps(depend_disk_snapshots, ensure_ascii=False)))

            base_info.set_depend_disk_snapshots(depend_disk_snapshots)

            _logger.info(r"检查从节点历史数据文件是否都存在，从节点对应的历史数据")
            # 执行完毕后，agent_valid_disk_snapshot 中仅有有效的从节点信息
            clean_invalid_data_tasks = agent_valid_disk_snapshot.clean_invalid_slave_step_one(
                schedule_config, slave_host_infos)
            base_info.set_agent_valid_disk_snapshot(agent_valid_disk_snapshot)

            _logger.info(r"分析备份类型")
            base_info.analyze_type(force_full)

            _logger.info(r"根据备份模式，清理从节点的历史数据")
            agent_valid_disk_snapshot.clean_invalid_slave_step_two(clean_invalid_data_tasks, base_info)

            self.do_clean_invalid_data_task(clean_invalid_data_tasks)

            _logger.info(r"创建 CDP 备份需要的token信息")
            base_info.generate_cdp_token()
            self._create_token_objs(base_info, cluster_cdp_task_obj)

            _logger.info(r"保存从节点的历史信息")
            agent_valid_disk_snapshot.save_token_info(base_info, master_host_obj)

            backup_sub_tasks = self._create_backup_sub_tasks(
                base_info, master_host_obj, slave_host_infos, disable_optimize, force_full, force_store_full)

            context_helper = TaskContextHelper({
                'cluster_info': base_info.info,
                'master_host_ident': master_host_obj.ident,
                'lock_info': lock_info,
                'backup_sub_tasks': backup_sub_tasks,
            })
            _logger.info('CctStartBackup ok : {}'.format(json.dumps(context_helper.context, ensure_ascii=False)))

        except Exception as e:
            _logger.error(r'CctStartBackup failed : {}'.format(e), exc_info=True)

            CBT_StopCdp.unlock_snapshots(lock_info)
            context_helper = TaskContextHelper(dict())
            context_helper.set_error((self._status_desc + '失败', r'CctStartBackup failed : {}'.format(e),))
            context_helper.set_error_step('CctStartBackup')

            if master_host_obj:
                ClusterCdpTaskExecutor.set_status_and_create_log(self._task_id,
                                                                 master_host_obj.ident,
                                                                 self._status_code,
                                                                 context_helper.get_err_msg(),
                                                                 context_helper.get_err_debug(),
                                                                 )
        return context_helper.context

    @staticmethod
    def _create_not_cluster_disk_cdp_token(cluster_cdp_task_obj):
        obj = m.ClusterTokenMapper.objects.create(
            cluster_task=cluster_cdp_task_obj,
            agent_token=uuid.uuid4().hex,
            host_ident='not_record_data',
            disk_id=-1,
        )
        return obj.agent_token

    def _fetch_prev_disk_snapshots(self, depend_disk_snapshots, master_host_obj, schedule_obj, schedule_config):
        """
        {
            "b4b186cf-1d2b-8e63-7952-189f3ad37ff7": {   <--- 集群磁盘的逻辑标识
                "disk_guid": native_guid,
                "depend_disk_snapshot_ident": snapshot_ident,
                "base_disk_snapshot_ident": snapshot_ident,
                "prev_disk_snapshot_ident": snapshot_ident,
            }
        }
        """

        def _get_last_disk_snapshot_obj():
            last_one = disk_snapshot_obj
            while True:
                if last_one.child_snapshots.count() == 0:
                    return last_one
                # remark 这里要求最后一次可见备份点后的数据都不要进行回收
                elif last_one.child_snapshots.count() == 1:
                    next_one = last_one.child_snapshots.first()
                    if next_one.is_cdp:
                        last_one = next_one
                    else:
                        return last_one
                else:
                    return last_one

        last_host_snapshot = m.HostSnapshot.objects.filter(
            host=master_host_obj, cluster_schedule=schedule_obj, cluster_visible=True, deleting=False, deleted=False
        ).order_by('-start_datetime').first()

        if last_host_snapshot:
            disk_in_host_snapshot = json.loads(last_host_snapshot.ext_info)['include_ranges']

            for disk_snapshot_obj in last_host_snapshot.disk_snapshots.all():
                native_guid = self.get_native_guid(disk_in_host_snapshot, disk_snapshot_obj)
                cluster_logic_disk_ident = self.get_cluster_logic_disk_ident(
                    schedule_config, native_guid, master_host_obj.ident)
                if cluster_logic_disk_ident is None or cluster_logic_disk_ident not in depend_disk_snapshots:
                    continue
                last_disk_snapshot_obj = _get_last_disk_snapshot_obj()
                depend_disk_snapshots[cluster_logic_disk_ident]["prev_disk_snapshot_ident"] = (
                    last_disk_snapshot_obj.ident)

    @staticmethod
    def _calc_valid_last_disk_snapshots(depend_disk_snapshots):
        """
        "b4b186cf-1d2b-8e63-7952-189f3ad37ff7": {   <--- 集群磁盘的逻辑标识
            "disk_guid": native_guid,
            "depend_disk_snapshot_ident": snapshot_ident,
            "base_disk_snapshot_ident": snapshot_ident,
            "prev_disk_snapshot_ident": snapshot_ident,
            "valid_last_disk_snapshot_ident": snapshot_ident,
        }
        """
        for cluster_logic_disk_ident, info in depend_disk_snapshots.items():
            depend_ident = info.get('depend_disk_snapshot_ident', None)
            prev_ident = info.get('prev_disk_snapshot_ident', None)
            base_ident = info.get('base_disk_snapshot_ident', None)
            if not depend_ident:
                continue
            if (not prev_ident) and (not base_ident):
                continue

            ds = m.DiskSnapshot.objects.get(ident=depend_ident)

            while ds:
                if ds.ident == base_ident or ds.ident == prev_ident:
                    break
                else:
                    ds = ds.parent_snapshot

            if ds:
                info['valid_last_disk_snapshot_ident'] = ds.ident

    @staticmethod
    def _fetch_base_disk_snapshots(depend_disk_snapshots, agent_valid_disk_snapshot):
        """
        {
            "b4b186cf-1d2b-8e63-7952-189f3ad37ff7": {   <--- 集群磁盘的逻辑标识
                "disk_guid": native_guid,
                "depend_disk_snapshot_ident": snapshot_ident,
                "base_disk_snapshot_ident": snapshot_ident,
            }
        }
        """
        for cluster_logic_disk_ident, base_disk_snapshot_ident \
                in agent_valid_disk_snapshot.get_base_disk_snapshot_ident().items():
            if cluster_logic_disk_ident not in depend_disk_snapshots:
                continue

            try:
                disk_snapshot_obj = m.DiskSnapshot.objects.get(ident=base_disk_snapshot_ident)
            except m.DiskSnapshot.DoesNotExist:
                continue

            depend_disk_snapshots[cluster_logic_disk_ident]["base_disk_snapshot_ident"] = disk_snapshot_obj.ident

    @staticmethod
    def get_native_guid(disk_in_host_snapshot, disk_snapshot_obj):
        for _disk in disk_in_host_snapshot:  # json.loads(last_host_snapshot.ext_info)['include_ranges']
            if _disk['diskIdent'] == disk_snapshot_obj.disk.ident:
                return _disk['diskNativeGUID']
        else:
            xlogging.raise_and_logging_error(
                '获取磁盘快照信息失败',
                '_fetch_depend_disk_snapshots get_native_guid failed {}'.format(disk_snapshot_obj))

    @staticmethod
    def get_cluster_logic_disk_ident(schedule_config, native_guid, host_ident):
        for cluster_disk in schedule_config['cluster_disks']:
            for map_disk in cluster_disk['map_disks']:
                if map_disk['disk_guid'] == native_guid and map_disk['host_ident'] == host_ident:
                    return cluster_disk['ident']
        return None

    def _fetch_depend_disk_snapshots(self, master_host_obj, schedule_obj, schedule_config, lock_info):
        """
        {
            "b4b186cf-1d2b-8e63-7952-189f3ad37ff7": {   <--- 集群磁盘的逻辑标识
                "disk_guid": native_guid,
                "depend_disk_snapshot_ident": snapshot_ident,
            }
        }
        """

        def _get_last_disk_snapshot_obj():
            last_one = disk_snapshot_obj
            while True:
                if last_one.child_snapshots.count() == 0:
                    return last_one
                else:
                    for child in last_one.child_snapshots.all():
                        if child.is_cdp:
                            last_one = child
                            break
                    else:
                        if last_one.child_snapshots.count() == 1:  # TODO，无锁，有并发风险
                            last_one = last_one.child_snapshots.first()
                        else:
                            xlogging.raise_and_logging_error(
                                '分析历史快照数据失败', '_get_last_disk_snapshot_obj failed : {}'.format(last_one))

        def _lock_all_disk_snapshots():
            validator_list = [snapshot.GetSnapshotList.is_disk_snapshot_object_exist,
                              snapshot.GetSnapshotList.is_disk_snapshot_file_exist]

            disk_snapshots = snapshot.GetSnapshotList.query_snapshots_by_snapshot_object(
                last_disk_snapshot_obj, validator_list, None, include_all_node=True)
            if len(disk_snapshots) == 0:
                xlogging.raise_and_logging_error(
                    r'无法访问历史快照文件，请检查存储节点连接状态',
                    r'{} disk_snapshot_object invalid'.format(last_disk_snapshot_obj.ident))

            try:
                with transaction.atomic():
                    snapshot.DiskSnapshotLocker.lock_files(disk_snapshots, lock_info['task_name'])
                lock_info['snapshots_idents'].extend(
                    [snapshot.DiskSnapshotLocker.get_disk_snapshot_object(ds.path, ds.snapshot).ident for
                     ds in disk_snapshots])
            except Exception as ee:
                xlogging.raise_and_logging_error(
                    '锁定历史快照文件失败，请稍后重试',
                    '_fetch_depend_disk_snapshots _lock_all_disk_snapshots failed {}'.format(ee))

        last_host_snapshot = m.HostSnapshot.objects.filter(
            host=master_host_obj, cluster_schedule=schedule_obj, deleting=False, deleted=False
        ).exclude(start_datetime__isnull=True).order_by('-start_datetime').first()

        try:
            if last_host_snapshot is None:
                return dict()

            _logger.info('last_host_snapshot : {}'.format(last_host_snapshot.id))
            disk_in_host_snapshot = json.loads(last_host_snapshot.ext_info)['include_ranges']

            result = dict()

            for disk_snapshot_obj in last_host_snapshot.disk_snapshots.all():
                native_guid = self.get_native_guid(disk_in_host_snapshot, disk_snapshot_obj)
                cluster_logic_disk_ident = self.get_cluster_logic_disk_ident(
                    schedule_config, native_guid, master_host_obj.ident)
                if cluster_logic_disk_ident is None:
                    continue

                last_disk_snapshot_obj = _get_last_disk_snapshot_obj()

                retry_count = 10
                while retry_count > 0:
                    try:
                        _lock_all_disk_snapshots()
                        break
                    except xlogging.BoxDashboardException:
                        retry_count = retry_count - 1
                        time.sleep(1)
                else:
                    # 多次尝试锁定快照链失败，就直接忽略已有数据，进行完整备份
                    # remark 设计上仅有备份数据被删除才会触发该分支
                    # remark 之前的流程已经判断过此时存储节点是否上线 TODO 是判断的此次使用的存储，历史的没判断
                    _logger.warning(r'can NOT lock {}, ignore history data'.format(disk_snapshot_obj))
                    continue

                result[cluster_logic_disk_ident] = {
                    "disk_guid": native_guid,
                    "depend_disk_snapshot_ident": last_disk_snapshot_obj.ident,
                }

            return result
        except Exception as e:
            _logger.error(r'_fetch_depend_disk_snapshots failed {}'.format(e), exc_info=True)
            return dict()

    def _query_agent_info(self, master_host_obj, slave_host_objs):
        slave_host_infos = dict()
        for slave_host_obj in slave_host_objs:
            slave_host_infos[slave_host_obj.ident] = self._query_host_info(slave_host_obj.ident)
        master_host_info = self._query_host_info(master_host_obj.ident)
        _logger.info(r'master_host_info : {}'.format(master_host_info))
        _logger.info(r'slave_host_infos : {}'.format(slave_host_infos))
        return master_host_info, slave_host_infos

    @staticmethod
    def _query_host_info(host_ident):
        """
        host_info
            {
                "disks": {
                    "xxx": { <- native_guid
                        "last_status": "none", "base", "cdp", "restore"
                        "status_uuid": "", "yyy",
                        "disk_index": 0，
                        "disk_bytes": int,
                    }
                }
            }
        """

        def _get_last_status():
            disk_status = disk.detail.status

            if disk_status == BoxLogic.DiskStatus.ErrorOccurred:
                xlogging.raise_and_logging_error(
                    r'磁盘驱动异常', 'disk_status error: ErrorOccurred {}'.format(disk.__dict__))
            if disk_status == BoxLogic.DiskStatus.Backuping:
                xlogging.raise_and_logging_error(
                    r'磁盘正在备份中', 'disk_status error: Backuping {}'.format(disk.__dict__))
            if disk_status == BoxLogic.DiskStatus.CDPing:
                xlogging.raise_and_logging_error(
                    r'磁盘正在CDP状态', 'disk_status error: CDPing {}'.format(disk.__dict__))

            if disk_status == BoxLogic.DiskStatus.NotExistLastSnapshot:
                return "none", ""
            if disk_status == BoxLogic.DiskStatus.LastSnapshotIsNormal:
                return "base", disk.detail.lastSnapshot
            if disk_status == BoxLogic.DiskStatus.LastSnapshotIsCDP:
                if disk.detail.cdpSnapshot.setByRestore:
                    return "restore", disk.detail.cdpSnapshot.token
                else:
                    return "cdp", disk.detail.cdpSnapshot.token
            else:
                xlogging.raise_and_logging_error(
                    r'未知的磁盘状态', 'disk_status error: unknown {}'.format(disk.__dict__))
                return None, None  # never execute

        result = dict()

        disks = boxService.box_service.queryDisksStatus(host_ident)
        system_infos = json.loads(boxService.box_service.querySystemInfo(host_ident))

        for disk in disks:
            if disk.detail.status == BoxLogic.DiskStatus.Unsupported:
                _logger.info(r'ignore unsupported disk {}'.format(disk.__dict__))
                continue

            for disk_more in system_infos['Disk']:
                if int(disk_more['DiskNum']) == disk.id:
                    break
            else:
                xlogging.raise_and_logging_error(
                    r'无效的磁盘信息', r'_query_host_info invalid physical_disk {}'.format(disk.__dict__), 1)
                disk_more = None  # never execute

            last_status, status_uuid = _get_last_status()

            result[disk_more['NativeGUID']] = {
                "last_status": last_status,
                "status_uuid": status_uuid,
                "disk_index": disk.id,
                "disk_bytes": disk.detail.numberOfSectors * 0x200,
            }

        return {"disks": result}

    @staticmethod
    def do_clean_invalid_data_task(clean_invalid_data_tasks):
        _logger.info(r'begin do_clean_invalid_data_task')
        for clean_invalid_data_task in clean_invalid_data_tasks:
            clean_invalid_data_task.work()
        _logger.info(r'end do_clean_invalid_data_task')

    @staticmethod
    def _persiste_agent_valid_disk_snapshot(schedule_obj, agent_valid_disk_snapshot):
        schedule_obj.agent_valid_disk_snapshot = json.dumps(agent_valid_disk_snapshot, ensure_ascii=False)
        schedule_obj.save(update_fields=['agent_valid_disk_snapshot', ])

    @staticmethod
    def _load_base_info(cluster_cdp_task_config, schedule_config, schedule_obj):
        force_full = cluster_cdp_task_config['force_full']
        force_store_full = cluster_cdp_task_config['force_store_full']
        disable_optimize = cluster_cdp_task_config.get('disable_optimize', False)
        master_host_obj = None
        slave_host_objs = list()
        for host_obj in schedule_obj.hosts.all():
            if host_obj.ident == schedule_config['master_node']:
                assert master_host_obj is None
                master_host_obj = host_obj
            else:
                slave_host_objs.append(host_obj)
        assert master_host_obj is not None
        return disable_optimize, force_full, force_store_full, master_host_obj, slave_host_objs

    @staticmethod
    def _create_token_objs(base_info, cluster_cdp_task_obj):
        def _get_disk_obj(_disk_ident):
            try:
                _disk_obj = m.Disk.objects.get(ident=_disk_ident)
            except m.Disk.DoesNotExist:
                _disk_obj = m.Disk.objects.create(ident=_disk_ident)
            return _disk_obj

        def _create_disk_snapshot_obj(_disk_obj, _disk_bytes, _image_path):
            return m.DiskSnapshot.objects.create(
                disk=_disk_obj, display_name='CDP集群{}'.format(cluster_disk_ident), image_path=_image_path,
                ident=uuid.uuid4().hex, bytes=_disk_bytes, boot_device=False, inc_date_bytes=0, reorganized_hash=True
            )

        # 创建CDP备份任务用来关联真正的存储用token
        sub_cdp_task_obj = m.CDPTask.objects.create(cluster_task=cluster_cdp_task_obj)

        cdp_tokens = base_info.get_cdp_tokens()
        # 为每个集群盘创建存储数据用的 CDP Token
        for cluster_disk_ident, cdp_info in cdp_tokens["tokens"].items():
            disk_obj = _get_disk_obj(cluster_disk_ident.replace('-', ''))
            disk_snapshot_obj = _create_disk_snapshot_obj(
                disk_obj, cdp_info['disk_bytes'],
                os.path.join(cdp_tokens['master_info']['storage_dir'], 'cluster_never_exist.qcow'))
            cdp_token_for_storage_obj = m.CDPDiskToken.objects.create(
                parent_disk_snapshot=disk_snapshot_obj,
                task=sub_cdp_task_obj,
                token=cdp_info['token_for_storage'],
            )

            for host_ident, agent_token in cdp_info['agent_tokens'].items():
                m.ClusterTokenMapper.objects.create(
                    cluster_task=cluster_cdp_task_obj,
                    agent_token=agent_token["token"],
                    host_ident=host_ident,
                    disk_id=agent_token["disk_index"],
                    file_token=cdp_token_for_storage_obj,
                )

    @staticmethod
    def _create_backup_sub_tasks(base_info, master_host_obj, slave_host_infos,
                                 disable_optimize, force_full, force_store_full):
        """
        one sub task
        {
            "host_ident": host_ident,
            "host_force_full": T/F,         agent 是否进行完整备份      (只影响到非集群盘)
            "disable_optimize": T/F,        agent 是否禁用网络优化      (只影响到非集群盘)
            "force_store_full": T/ F,       服务器是否强制完整存储      (只影响到非集群盘)
            "cluster_disks": {
                "master_node": T/F,         是否是主节点
                "xxx": {                        native_guid
                    "agent_force_full": T/F,    是否做agent完整数据扫描
                    "cdp_token": yyy,           cdp_token
                    "last_snapshot_ident": abc, 依赖的磁盘快照，仅主节点传入该值
                    "snapshot_chain": [         依赖链，仅从节点传入该值
                        {"path": ppp, "ident": iii},
                        ...
                    "disk_logic_ident":         agent磁盘逻辑ident，仅从节点传入该值
                    ],
                },
                ...
            }
        }
        """

        result = list()

        for host_ident, slave_disk_info in slave_host_infos.items():
            backup_sub_task = {
                "host_ident": host_ident,
                "host_force_full": force_full,
                "disable_optimize": disable_optimize,
                "force_store_full": force_store_full,
                "cluster_disks": base_info.generate_cluster_disks_for_backup_sub_task(host_ident, False)
            }

            _logger.info('_create_backup_sub_tasks : {}'.format(backup_sub_task))
            result.append(backup_sub_task)

        backup_sub_task = {
            "host_ident": master_host_obj.ident,
            "host_force_full": force_full,
            "disable_optimize": disable_optimize,
            "force_store_full": force_store_full,
            "cluster_disks": base_info.generate_cluster_disks_for_backup_sub_task(master_host_obj.ident, True)
        }
        _logger.info('_create_backup_sub_tasks : {}'.format(backup_sub_task))
        result.append(backup_sub_task)

        return result


class CctSendBackupCmd(task.Task):
    default_provides = 'task_context'

    def __init__(self, name, task_id, inject=None):
        super(CctSendBackupCmd, self).__init__(r'CctSendBackupCmd {}'.format(name), inject=inject)
        self._task_id = task_id
        self._status_code = r'CctSendBackupCmd'
        self._status_desc = xdata.get_type_name(STATUS_MAP, self._status_code)

    def execute(self, task_context, **kwargs):
        context_helper = TaskContextHelper(task_context)
        try:
            if context_helper.has_error():
                return context_helper.context

            _logger.info(r'begin send backup cmd step ...')

            cluster_cdp_task_obj, cluster_cdp_task_config, schedule_obj, schedule_config = load_objs(self._task_id)
            _check_run_twice(cluster_cdp_task_obj, 'CctSendBackupCmd')

            ClusterCdpTaskExecutor.set_status_and_create_log(self._task_id,
                                                             context_helper.get_master_host_ident(),
                                                             self._status_code,
                                                             self._status_desc)

            backup_sub_tasks = context_helper.get_backup_sub_tasks()
            agent_valid_disk_snapshot = AgentValidDiskSnapshotInfo(schedule_obj)
            base_info = ClusterBackupBaseInfo(context_helper.get_cluster_info())

            slave_host_snapshot_ids = list()

            for backup_sub_task in backup_sub_tasks[:-1]:  # 最后一个为主节点，需要等待从节点进入CDP状态后，再进入CDP状态
                hot_snapshot_id = self._do_backup_sub_task(
                    backup_sub_task, schedule_obj, cluster_cdp_task_obj,
                    agent_valid_disk_snapshot, schedule_config, base_info
                )
                slave_host_snapshot_ids.append(hot_snapshot_id)

            self._wait_entry_backup(slave_host_snapshot_ids, cluster_cdp_task_obj, schedule_obj)

            backup_sub_task = backup_sub_tasks[-1]
            self._do_backup_sub_task(
                backup_sub_task, schedule_obj, cluster_cdp_task_obj,
                agent_valid_disk_snapshot, schedule_config, base_info
            )

            self._wait_cdp_files(base_info, cluster_cdp_task_obj, schedule_obj)

            context_helper.set_t0_must_after_timestamp(time.time())

            _logger.info(r'send backup cmd step end')

        except Exception as e:
            _logger.error(r'CctSendBackupCmd failed : {}'.format(e), exc_info=True)

            context_helper.set_error((self._status_desc + '失败', r'CctSendBackupCmd failed : {}'.format(e),))
            context_helper.set_error_step('CctSendBackupCmd')

            ClusterCdpTaskExecutor.set_status_and_create_log(
                self._task_id,
                context_helper.get_master_host_ident(),
                self._status_code,
                context_helper.get_err_msg(),
                context_helper.get_err_debug(),
            )

        return context_helper.context

    def _wait_entry_backup(self, slave_host_snapshot_ids, cluster_cdp_task_obj, schedule_obj):
        sleep = tasks.Sleep(schedule_obj.id, sender=m.ClusterBackupSchedule)
        sub_tasks = CctBaseBackup.query_sub_tasks(cluster_cdp_task_obj)
        checker = partial(_is_canceled, self._task_id)

        while True:
            _logger.info(r'wait slave backup step {}'.format(slave_host_snapshot_ids))

            if self._is_all_host_snapshot_report_progress(slave_host_snapshot_ids):
                break

            some_one_not_cdp = \
                CctCdpBackup.check_all_host_in_cdp_status(checker, schedule_obj, sub_tasks, self._task_id, True)
            if not some_one_not_cdp:
                sleep.sleep(15)
            else:
                xlogging.raise_and_logging_error('主机不在持续保护状态', 'some_one_not_cdp')

    @staticmethod
    def _is_all_host_snapshot_report_progress(host_snapshot_ids):
        for host_snapshot_id in host_snapshot_ids:
            if not tasks.MigrateTaskWithNormalWorker.is_backup_data_transfer(
                    host_snapshot_id, '主机不在持续保护状态', 'is_backup_data_transfer', 1):
                return False
        return True

    def _wait_cdp_files(self, base_info, cluster_cdp_task_obj, schedule_obj):
        tokens = base_info.get_cluster_disks_cdp_token()
        sleep = tasks.Sleep(schedule_obj.id, sender=m.ClusterBackupSchedule)
        sub_tasks = CctBaseBackup.query_sub_tasks(cluster_cdp_task_obj)
        checker = partial(_is_canceled, self._task_id)

        send_timestamp = time.time()

        while True:
            _logger.info(r'wait cdp file step {}'.format(tokens))

            if self.cdp_files_exist(tokens):
                break

            if time.time() - send_timestamp > 120:
                CctHelper(base_info).force_generic_cdp_file()

            some_one_not_cdp = \
                CctCdpBackup.check_all_host_in_cdp_status(checker, schedule_obj, sub_tasks, self._task_id, True)
            if not some_one_not_cdp:
                sleep.sleep(30)
            else:
                xlogging.raise_and_logging_error('主机不在持续保护状态', 'some_one_not_cdp')

    @staticmethod
    def cdp_files_exist(tokens):
        for tk in tokens:
            token_obj = m.CDPDiskToken.objects.get(token=tk)
            if token_obj.using_disk_snapshot is None and token_obj.last_disk_snapshot is None:
                return False
        return True

    def _do_backup_sub_task(
            self, backup_sub_task, schedule_obj, task_object, agent_valid_disk_snapshot, schedule_config, base_info):

        def _record_disk_snapshot(host_snapshot_obj):
            disk_in_host_snapshot = json.loads(host_snapshot_obj.ext_info)['include_ranges']

            backup_sub_task['disk_snapshots'] = dict()

            for disk_snapshot_obj in host_snapshot_obj.disk_snapshots.all():
                native_guid = CctStartBackup.get_native_guid(disk_in_host_snapshot, disk_snapshot_obj)
                cluster_logic_disk_ident = CctStartBackup.get_cluster_logic_disk_ident(
                    schedule_config, native_guid, host_snapshot_obj.host_ident)

                backup_sub_task['disk_snapshots'][native_guid] = {
                    'cluster_logic_disk_ident': cluster_logic_disk_ident,
                    'disk_snapshot_ident': disk_snapshot_obj.ident,
                    'agent_disk_logic_ident': disk_snapshot_obj.disk.ident,
                }

                if cluster_logic_disk_ident is None:
                    continue

                if cluster_disks['master_node']:
                    base_info.set_master_current_disk_snapshot_ident(cluster_logic_disk_ident, disk_snapshot_obj.ident)
                    if cluster_disks[native_guid]['agent_force_full']:
                        agent_valid_disk_snapshot.save_master_full_disk_snapshot(
                            cluster_logic_disk_ident, disk_snapshot_obj.ident)
                else:
                    base_info.set_slave_current_disk_snapshot_ident(
                        cluster_logic_disk_ident, host_snapshot_obj.host_ident, disk_snapshot_obj.ident)
                    agent_valid_disk_snapshot.save_slave_disk_snapshot(
                        cluster_logic_disk_ident, host_snapshot_obj.host_ident,
                        disk_snapshot_obj.ident, disk_snapshot_obj.disk.ident)

        def _fix_slave_host_snapshot(host_snapshot_obj):
            ext_info = json.loads(host_snapshot_obj.ext_info)
            include_ranges = ext_info['include_ranges']

            new_host_snapshot = m.HostSnapshot.objects.create(
                host=host_snapshot_obj.host,
                start_datetime=host_snapshot_obj.start_datetime,
                finish_datetime=host_snapshot_obj.start_datetime,
                deleting=True,
                ext_info=host_snapshot_obj.ext_info,
                display_status='fake_host_snapshot',
            )

            for disk_snapshot_obj in host_snapshot_obj.disk_snapshots.all():
                native_guid = CctStartBackup.get_native_guid(include_ranges, disk_snapshot_obj)
                cluster_logic_disk_ident = CctStartBackup.get_cluster_logic_disk_ident(
                    schedule_config, native_guid, host_snapshot_obj.host_ident)

                if cluster_logic_disk_ident is None:
                    continue

                disk_snapshot_obj.host_snapshot = new_host_snapshot
                disk_snapshot_obj.save(update_fields=['host_snapshot', ])

                include_ranges = [one_disk for one_disk in include_ranges
                                  if one_disk['diskSnapshot'] != disk_snapshot_obj.ident]

            host_snapshot_obj.ext_info = json.dumps(ext_info)
            host_snapshot_obj.cluster_visible = True
            host_snapshot_obj.save(update_fields=['ext_info', 'cluster_visible', ])

        host_ident = backup_sub_task['host_ident']
        host_force_full = backup_sub_task['host_force_full']
        force_store_full = backup_sub_task['force_store_full']
        disable_optimize = backup_sub_task['disable_optimize']
        cluster_disks = backup_sub_task['cluster_disks']

        host_obj = m.Host.objects.get(ident=host_ident)
        with transaction.atomic():
            cdp_task_object = m.CDPTask.objects.create(cluster_task=task_object)
            work = work_processors.HostBackupWorkProcessors(
                task_name=self.name,
                host_object=host_obj,
                force_agent_full=host_force_full,
                storage_node_ident=schedule_obj.storage_node_ident,
                cluster_schedule_object=schedule_obj,
                cdp_task_object_id=cdp_task_object.id,
                cluster_disks=cluster_disks,
                force_store_full=force_store_full,
                disable_optimize=disable_optimize,
                is_cdp=True,
                cdp_mode_type=xdata.CDP_MODE_ASYN,
                force_optimize=True
            )
            cdp_task_object.set_host_snapshot(work.host_snapshot)

        work.work()  # 创建HostSnapshot 与 DiskSnapshot 数据库对象，发送备份命令

        backup_sub_task['host_snapshot_obj_id'] = work.host_snapshot.id
        _record_disk_snapshot(work.host_snapshot)
        if host_ident != schedule_config['master_node']:
            _fix_slave_host_snapshot(work.host_snapshot)
        return work.host_snapshot.id


class CctBaseBackup(task.Task):
    """
    基础备份阶段：等待基础备份完成
    """
    default_provides = 'task_context'

    def __init__(self, name, task_id, inject=None):
        super(CctBaseBackup, self).__init__(r'CctBaseBackup {}'.format(name), inject=inject)
        self._task_id = task_id
        self._status_code = r'CctBaseBackup'
        self._status_desc = xdata.get_type_name(STATUS_MAP, self._status_code)

    @staticmethod
    def query_sub_tasks(task_object):
        sub_tasks = list()
        for sub_task in task_object.sub_tasks.all():
            if hasattr(sub_task, 'host_snapshot') and sub_task.host_snapshot:
                sub_tasks.append({'host_ident': sub_task.host_snapshot.host.ident, 'sub_task_id': sub_task.id})
        return sub_tasks

    def execute(self, task_context, *args, **kwargs):
        context_helper = TaskContextHelper(task_context)
        try:
            if context_helper.has_error():
                return context_helper.context

            _logger.info(r'begin wait backup base step ...')

            ClusterCdpTaskExecutor.set_status_and_create_log(
                self._task_id,
                context_helper.get_master_host_ident(),
                self._status_code,
                self._status_desc
            )

            cluster_cdp_task_obj, cluster_cdp_task_config, schedule_obj, schedule_config = load_objs(self._task_id)
            master_node = schedule_config['master_node']

            sleep = tasks.Sleep(schedule_obj.id, sender=m.ClusterBackupSchedule)

            sub_tasks = self.query_sub_tasks(cluster_cdp_task_obj)
            checker = partial(_is_canceled, self._task_id)

            self._check_master_task(master_node, sub_tasks, checker, sleep)

            self._check_all_tasks(checker, sleep, sub_tasks)

            _logger.info(r'wait backup base step end')

            AgentValidDiskSnapshotInfo(schedule_obj).clean_old_cdp_token()

            context_helper.set_normal_backup_successful()

        except xlogging.BoxDashboardException as bde:
            _logger.error(r'CctBaseBackup failed : {} | {}'.format(bde.msg, bde.debug))
            context_helper.set_error((self._status_desc + '失败，' + bde.msg,
                                      r'CctBaseBackup failed : {}'.format(bde.debug),))
            context_helper.set_error_step('CctBaseBackup')

            ClusterCdpTaskExecutor.set_status_and_create_log(self._task_id,
                                                             context_helper.get_master_host_ident(),
                                                             self._status_code,
                                                             context_helper.get_err_msg(),
                                                             context_helper.get_err_debug())

            if bde.http_status == xlogging.ERROR_HTTP_STATUS_NEED_RETRY:
                # 不能立刻发送停止CDP的指令，直接 sleep 一小会儿
                time.sleep(10)
        except Exception as e:
            _logger.error(r'CctBaseBackup failed : {}'.format(e), exc_info=True)
            context_helper.set_error((self._status_desc + '失败', r'CctBaseBackup failed : {}'.format(e),))
            context_helper.set_error_step('CctBaseBackup')

            ClusterCdpTaskExecutor.set_status_and_create_log(self._task_id,
                                                             context_helper.get_master_host_ident(),
                                                             self._status_code,
                                                             context_helper.get_err_msg(),
                                                             context_helper.get_err_debug())
        return context_helper.context

    @staticmethod
    def _check_all_tasks(checker, sleep, sub_tasks):
        while True:
            unfinished_count = 0
            for sub_task in sub_tasks:
                unfinished_count = CctBaseBackup._check_sub_task(sub_task, checker, unfinished_count)
            if unfinished_count != 0:
                sleep.sleep(30)
            else:
                break

    @staticmethod
    def _check_master_task(master_node, sub_tasks, checker, sleep):
        for sub_task in sub_tasks:
            if sub_task['host_ident'] == master_node:
                master_task = sub_task
                break
        else:
            assert False, "never happen ! not master task ?!"

        while CctBaseBackup._check_sub_task(master_task, checker, 0):
            sleep.sleep(30)

    @staticmethod
    def _check_sub_task(sub_task, checker, unfinished_count):
        user_cancel_check = tasks.CDPTaskWorker.UserCancelCheck(
            snapshot.Tokens.get_schedule_obj_from_cdp_task(
                m.CDPTask.objects.get(id=sub_task['sub_task_id'])), sub_task['sub_task_id'],
            checker
        )
        if task_helper.TaskHelper.check_backup_status_in_cdp_task(
                sub_task['host_ident'], sub_task['sub_task_id'], user_cancel_check):
            return unfinished_count + 1
        else:
            return unfinished_count


class FindSafeTimeHelper(object):
    @staticmethod
    def get_disk_snapshot_objs(token):
        return list(m.DiskSnapshot.objects.filter(cdp_info__token__token=token)
                    .order_by('cdp_info__first_timestamp').all())

    @staticmethod
    def match_t0_with_disk_snapshots(chk_stamp, disk_snapshot_objs):
        prev_cdp_disk_snapshot_object = None
        current_cdp_disk_snapshot_object = None

        for disk_snapshot_obj in disk_snapshot_objs:
            current_cdp_disk_snapshot_object = disk_snapshot_obj

            if chk_stamp < current_cdp_disk_snapshot_object.cdp_info.first_timestamp:
                # 使用前一个快照文件
                current_cdp_disk_snapshot_object = prev_cdp_disk_snapshot_object
                break

            if (current_cdp_disk_snapshot_object.cdp_info.last_timestamp is None) or \
                    (current_cdp_disk_snapshot_object.cdp_info.first_timestamp <= chk_stamp
                     <= current_cdp_disk_snapshot_object.cdp_info.last_timestamp):
                start_timestamp, end_timestamp = boxService.box_service.queryCdpTimestampRange(
                    current_cdp_disk_snapshot_object.image_path)
                if chk_stamp < start_timestamp:
                    # 使用前一个快照文件
                    current_cdp_disk_snapshot_object = prev_cdp_disk_snapshot_object
                    break
                elif (current_cdp_disk_snapshot_object.cdp_info.last_timestamp is not None) and \
                        (chk_stamp <= end_timestamp):
                    # 使用本快照文件
                    break

            prev_cdp_disk_snapshot_object = current_cdp_disk_snapshot_object

        if not current_cdp_disk_snapshot_object:
            return None, None

        t0_timestamp = snapshot.GetDiskSnapshot.get_snapshot_timestamp(
            current_cdp_disk_snapshot_object.image_path, chk_stamp)
        if t0_timestamp is None:
            _logger.warning(
                r'match_t0_with_disk_snapshots can not get_snapshot_timestamp {} {}'.format(
                    current_cdp_disk_snapshot_object.image_path, chk_stamp))
            return None, None
        else:
            return current_cdp_disk_snapshot_object.image_path, t0_timestamp

    @staticmethod
    def match_t0_with_cdp_file(chk_stamp, cdp_info):
        match = list()
        for token, disk_snapshot_objs in cdp_info.items():
            file, stamp = FindSafeTimeHelper.match_t0_with_disk_snapshots(chk_stamp, disk_snapshot_objs)
            if file is None or stamp is None:
                xlogging.raise_and_logging_error(
                    r'分析数据失败，无法查找到安全时间点',
                    'never happen {}, 不应该发生，因为t0时刻一定有cdp文件创建'.format(token))
            else:
                match.append({
                    "token": token,
                    "file": file,
                    "time": stamp,
                })
        return match

    @staticmethod
    def _find_idle_time(cdp_info):
        _logger.info('_find_idle_time cdp_info:{}'.format(cdp_info))
        cdp_file_list = list()
        for disk_snapshot_objs in cdp_info.values():
            cdp_file_list.append([disk_snapshot_obj.image_path for disk_snapshot_obj in disk_snapshot_objs])

        datetime_list = cdp_wrapper.find_idle_time(cdp_file_list)
        if not datetime_list:
            _logger.warning(r'get_safe_timestamp_after_chk no safe time, cdp_file_list={}'.format(cdp_file_list))
            return None

        stamp_list = [{'start': time.mktime(dt['start'].timetuple()), 'end': time.mktime(dt['end'].timetuple())}
                      for dt in datetime_list]

        return stamp_list

    @staticmethod
    def get_safe_timestamp_more_newer(tokens, chk_stamp):
        cdp_info = dict()

        for token in tokens:
            disk_snapshot_objs = FindSafeTimeHelper.get_disk_snapshot_objs(token)
            if not disk_snapshot_objs:
                _logger.warning(r'get_safe_timestamp_more_newer no cdp file, token = {}'.format(token))
                return None
            cdp_obj_index = cdp_wrapper.find_cdp_obj_index(disk_snapshot_objs, chk_stamp)

            disk_snapshot_objs_count = len(disk_snapshot_objs)
            min_index = max(cdp_obj_index - 2, 0)
            max_index = min(disk_snapshot_objs_count + 1, cdp_obj_index + 2)

            cdp_info[token] = disk_snapshot_objs[min_index:max_index]

        stamp_list = FindSafeTimeHelper._find_idle_time(cdp_info)
        if not stamp_list:
            return None

        for valid_stamp in reversed(stamp_list):
            if valid_stamp["start"] <= chk_stamp <= valid_stamp["end"]:
                return chk_stamp
            if chk_stamp > valid_stamp["start"]:
                return valid_stamp["end"]

        return stamp_list[-1]["end"]

    @staticmethod
    def get_safe_timestamp_after_chk(more_file_dict, tokens, chk_stamp, need_match_file=True):
        """
        返回安全点以及该时刻的对应各个磁盘的cdp文件、最近时间点信息
        match=[{"token":"token","file":"cdp_file", "time":stamp},.....]
        """
        if more_file_dict:
            _logger.info(r'get_safe_timestamp_after_chk more_file_dict : {}'.format(more_file_dict))

        cdp_info = dict()
        for token in tokens:
            disk_snapshot_objs = FindSafeTimeHelper.get_disk_snapshot_objs(token)
            if not disk_snapshot_objs:
                _logger.warning(r'get_safe_timestamp_after_chk no cdp file, token = {}'.format(token))
                return None, None
            cdp_obj_index = cdp_wrapper.find_cdp_obj_index(disk_snapshot_objs, chk_stamp)

            more_file_count = more_file_dict.get(token, 0)
            disk_snapshot_objs_count = len(disk_snapshot_objs)
            min_index = max(cdp_obj_index - 2, 0)
            max_index = min(disk_snapshot_objs_count + 1, cdp_obj_index + 2 + more_file_count)
            if max_index - min_index >= 4 and max_index < disk_snapshot_objs_count + 1:
                # 本次扫描的文件数量大于等于4个，如果还扫描失败，下次就要尝试向后多扫描一个
                more_file_dict[token] = max_index - min_index - 2

            cdp_info[token] = disk_snapshot_objs[min_index:max_index]

        stamp_list = FindSafeTimeHelper._find_idle_time(cdp_info)
        if not stamp_list:
            return None, None

        for valid_stamp in stamp_list:
            if valid_stamp["end"] <= chk_stamp:
                continue

            if valid_stamp["start"] <= chk_stamp:
                safe_timestamp_host = chk_stamp
            else:
                safe_timestamp_host = valid_stamp["start"]
            break
        else:
            _logger.warning(r'get_safe_timestamp_after_chk no match time : {} {}'.format(chk_stamp, stamp_list[-10:]))
            return None, None

        _logger.info('find safe_timestamp_host : {} {} {}'.format(safe_timestamp_host, chk_stamp, stamp_list[-10:]))

        if need_match_file:
            match = FindSafeTimeHelper.match_t0_with_cdp_file(safe_timestamp_host, cdp_info)
            return safe_timestamp_host, match
        else:
            return safe_timestamp_host, None


class CctFetchT0(task.Task):
    default_provides = 'task_context'

    def __init__(self, name, task_id, inject=None):
        super(CctFetchT0, self).__init__(r'CctFetchT0 {}'.format(name), inject=inject)
        self._task_id = task_id
        self._status_code = r'CctFetchT0'
        self._status_desc = xdata.get_type_name(STATUS_MAP, self._status_code)

    @staticmethod
    def cdp_files_unchange(t0_list):
        for t0 in t0_list:
            token_obj = m.CDPDiskToken.objects.get(token=t0['token'])
            if token_obj.using_disk_snapshot is None:
                return False
            if token_obj.using_disk_snapshot.image_path == t0['file']:
                return False
        return True

    def execute(self, task_context, **kwargs):
        context_helper = TaskContextHelper(task_context)
        try:
            if context_helper.has_error():
                return context_helper.context

            ClusterCdpTaskExecutor.set_status_and_create_log(
                self._task_id,
                context_helper.get_master_host_ident(),
                self._status_code,
                self._status_desc
            )

            cluster_cdp_task_obj, cluster_cdp_task_config, schedule_obj, schedule_config = load_objs(self._task_id)
            base_info = ClusterBackupBaseInfo(context_helper.get_cluster_info())

            helper = CctHelper(base_info)

            tokens = base_info.get_cluster_disks_cdp_token()

            sleep = tasks.Sleep(schedule_obj.id, sender=m.ClusterBackupSchedule)
            sub_tasks = CctBaseBackup.query_sub_tasks(cluster_cdp_task_obj)
            checker = partial(_is_canceled, self._task_id)

            more_file_dict = dict()

            while True:
                _logger.info(r'fetch T0 step {} {}'.format(context_helper.get_t0_must_after_timestamp(),
                                                           datetime.datetime.fromtimestamp(
                                                               float(context_helper.get_t0_must_after_timestamp()))))

                t0_host, t0_list = FindSafeTimeHelper.get_safe_timestamp_after_chk(
                    more_file_dict, tokens, context_helper.get_t0_must_after_timestamp())
                if t0_host and (not os.path.exists(r'/dev/shm/cluster_always_no_safe')):
                    _logger.info('find t0 : {}  {}'.format(t0_host, json.dumps(t0_list, ensure_ascii=False)))
                    break

                some_one_not_cdp = \
                    CctCdpBackup.check_all_host_in_cdp_status(checker, schedule_obj, sub_tasks, self._task_id)
                if not some_one_not_cdp:
                    helper.force_generic_cdp_file()
                    sleep.sleep(30)
                else:
                    xlogging.raise_and_logging_error('主机不在持续保护状态', 'some_one_not_cdp')

            base_info.record_t0(t0_list)

            self.force_switch_cdp_file(t0_list)

            force_generic_cdp_file = True

            while True:
                _logger.info(r'wait cdp change step {}'.format(t0_list))

                if self.cdp_files_unchange(t0_list):
                    break

                some_one_not_cdp = \
                    CctCdpBackup.check_all_host_in_cdp_status(checker, schedule_obj, sub_tasks, self._task_id)
                if not some_one_not_cdp:
                    if force_generic_cdp_file:
                        helper.force_generic_cdp_file()
                        force_generic_cdp_file = False
                    sleep.sleep(30)
                else:
                    xlogging.raise_and_logging_error('主机不在持续保护状态', 'some_one_not_cdp')

            _logger.info('CctFetchT0 ok')

        except xlogging.BoxDashboardException as bde:
            _logger.error(r'CctFetchT0 failed : {} | {}'.format(bde.msg, bde.debug))
            context_helper.set_error((self._status_desc + '失败，' + bde.msg,
                                      r'CctFetchT0 failed : {}'.format(bde.debug),))
            context_helper.set_error_step('CctFetchT0')
            ClusterCdpTaskExecutor.set_status_and_create_log(self._task_id,
                                                             context_helper.get_master_host_ident(),
                                                             self._status_code,
                                                             context_helper.get_err_msg(),
                                                             context_helper.get_err_debug()
                                                             )
        except Exception as e:
            _logger.error(r'CctFetchT0 failed : {}'.format(e), exc_info=True)
            context_helper.set_error((self._status_desc + '失败', r'CctFetchT0 failed : {}'.format(e),))
            context_helper.set_error_step('CctFetchT0')
            ClusterCdpTaskExecutor.set_status_and_create_log(self._task_id,
                                                             context_helper.get_master_host_ident(),
                                                             self._status_code,
                                                             context_helper.get_err_msg(),
                                                             context_helper.get_err_debug()
                                                             )
        return context_helper.context

    @staticmethod
    def force_switch_cdp_file(t0_list):
        for t0 in t0_list:
            token_obj = m.CDPDiskToken.objects.get(token=t0['token'])
            if token_obj.using_disk_snapshot and token_obj.using_disk_snapshot.image_path == t0['file']:
                try:
                    snapshot.Tokens.change_cdp_file_logic(token_obj.id, t0['file'], True)
                except xlogging.BoxDashboardException:
                    pass  # do nothing
        return


class CctSplitT0(task.Task):
    """
    按照T0时刻，分割cdp文件
    """
    default_provides = 'task_context'

    def __init__(self, name, task_id, inject=None):
        super(CctSplitT0, self).__init__(r'CctSplitT0 {}'.format(name), inject=inject)
        self._task_id = task_id
        self._status_code = r'CctSplitT0'
        self._status_desc = xdata.get_type_name(STATUS_MAP, self._status_code)

    @staticmethod
    def get_cdp_file_size(cdp_file):
        return m.DiskSnapshot.objects.get(image_path=cdp_file).bytes

    @staticmethod
    def cdp_split(cdp_file, timestamp, part1_path, part2_path):
        disk_bytes = CctSplitT0.get_cdp_file_size(cdp_file)

        boxService.box_service.cutCdpFile(json.dumps({
            'disk_bytes': disk_bytes,
            'new_path': part1_path,
            'path': cdp_file,
            'range': snapshot.GetSnapshotList.format_timestamp(None, timestamp),
        }))

        boxService.box_service.cutCdpFile(json.dumps({
            'disk_bytes': disk_bytes,
            'new_path': part2_path,
            'path': cdp_file,
            'range': snapshot.GetSnapshotList.format_timestamp(timestamp, None),
        }))

    def cdp_split_t0(self, cdp_token):
        """
        cdp_token: {
            t0_org_file: path,          # CDP文件
            t0_timestamp: timestamp,    # 切割点
            t0_before_path: path,       # 文件路径
            t0_after_path: path,        # 文件路径
        }
        """
        _logger.info('cdp_split_t0: {}'.format(json.dumps(cdp_token, ensure_ascii=False)))
        cdp_file = cdp_token['t0_org_file']
        assert os.path.exists(cdp_file)

        part1_path = cdp_token['t0_before_path']
        part2_path = cdp_token['t0_after_path']
        if os.path.exists(part1_path):
            os.remove(part1_path)
        if os.path.exists(part2_path):
            os.remove(part2_path)

        begin, end = boxService.box_service.queryCdpTimestampRange(cdp_file)
        assert begin and end
        assert begin <= cdp_token['t0_timestamp'] <= end
        if end == cdp_token['t0_timestamp']:
            _logger.warning('not t0_after_file, because timestamp is end')
            shutil.copy(cdp_file, part1_path)
        else:
            timestamp = boxService.box_service.queryCdpTimestamp(cdp_file, cdp_token['t0_timestamp'])
            self.cdp_split(cdp_file, timestamp, part1_path, part2_path)

    def execute(self, task_context, *args, **kwargs):
        context_helper = TaskContextHelper(task_context)
        try:
            if context_helper.has_error():
                return context_helper.context

            ClusterCdpTaskExecutor.set_status_and_create_log(
                self._task_id,
                context_helper.get_master_host_ident(),
                self._status_code,
                self._status_desc
            )

            _logger.info('begin CctSplitT0 ...')

            base_info = ClusterBackupBaseInfo(context_helper.get_cluster_info())
            cdp_tokens = base_info.get_cdp_token_for_storage_list()
            for cdp_token in cdp_tokens:
                self.cdp_split_t0(cdp_token)

            _logger.info('CctSplitT0 ok')

        except xlogging.BoxDashboardException as bde:
            _logger.error(r'CctSplitT0 failed : {} | {}'.format(bde.msg, bde.debug))
            context_helper.set_error((self._status_desc + '失败，' + bde.msg,
                                      r'CctSplitT0 failed : {}'.format(bde.debug),))
            context_helper.set_error_step('CctSplitT0')
            ClusterCdpTaskExecutor.set_status_and_create_log(self._task_id,
                                                             context_helper.get_master_host_ident(),
                                                             self._status_code,
                                                             context_helper.get_err_msg(),
                                                             context_helper.get_err_debug())
        except Exception as e:
            _logger.error(r'CctSplitT0 failed : {}'.format(e), exc_info=True)
            context_helper.set_error((self._status_desc + '失败', r'CctSplitT0 failed : {}'.format(e),))
            context_helper.set_error_step('CctSplitT0')
            ClusterCdpTaskExecutor.set_status_and_create_log(self._task_id,
                                                             context_helper.get_master_host_ident(),
                                                             self._status_code,
                                                             context_helper.get_err_msg(),
                                                             context_helper.get_err_debug())

        return context_helper.context


class CctGenerateDiff(task.Task):
    default_provides = 'task_context'

    def __init__(self, name, task_id, inject=None):
        super(CctGenerateDiff, self).__init__(r'CctGenerateDiff {}'.format(name), inject=inject)
        self._task_id = task_id
        self._status_code = r'CctGenerateDiff'
        self._status_desc = xdata.get_type_name(STATUS_MAP, self._status_code)

    def execute(self, task_context, **kwargs):
        context_helper = TaskContextHelper(task_context)
        try:
            if context_helper.has_error():
                return context_helper.context

            ClusterCdpTaskExecutor.set_status_and_create_log(self._task_id,
                                                             context_helper.get_master_host_ident(),
                                                             self._status_code,
                                                             self._status_desc)

            _logger.info('begin CctGenerateDiff ...')

            base_info = ClusterBackupBaseInfo(context_helper.get_cluster_info())
            params = base_info.generate_diff_snapshots_params()
            for one_qcow in params['qcows']:
                boxService.box_service.remove(one_qcow['hash_path'])
                self._generate_hash_file(one_qcow)
                base_info.set_diff_qcow_image_snapshots(one_qcow['disk_id'], one_qcow['source_snapshots'])
            try:
                boxService.box_service.generateClusterDiffQcow(json.dumps(params))
            finally:
                for one_qcow in params['qcows']:
                    boxService.box_service.remove(one_qcow['hash_path'])

            _logger.info('CctGenerateDiff ok')

        except xlogging.BoxDashboardException as bde:
            _logger.error(r'CctGenerateDiff failed : {} | {}'.format(bde.msg, bde.debug))
            context_helper.set_error((self._status_desc + '失败，' + bde.msg,
                                      r'CctGenerateDiff failed : {}'.format(bde.debug),))
            context_helper.set_error_step('CctGenerateDiff')
            ClusterCdpTaskExecutor.set_status_and_create_log(self._task_id,
                                                             context_helper.get_master_host_ident(),
                                                             self._status_code,
                                                             context_helper.get_err_msg(),
                                                             context_helper.get_err_debug())
        except Exception as e:
            _logger.error(r'CctGenerateDiff failed : {}'.format(e), exc_info=True)
            context_helper.set_error((self._status_desc + '失败', r'CctGenerateDiff failed : {}'.format(e),))
            context_helper.set_error_step('CctGenerateDiff')
            ClusterCdpTaskExecutor.set_status_and_create_log(self._task_id,
                                                             context_helper.get_master_host_ident(),
                                                             self._status_code,
                                                             context_helper.get_err_msg(),
                                                             context_helper.get_err_debug())
        return context_helper.context

    @staticmethod
    def _generate_hash_file(one_qcow):
        hash_files = one_qcow.pop('source_snapshots_hash_files', list())
        hash_files.reverse()
        result_mount_snapshot = json.loads(boxService.box_service.startBackupOptimize({
            'hash_files': hash_files,
            'ordered_hash_file': one_qcow['hash_path'],
            'disk_bytes': one_qcow['new_qcow']['disk_bytes'],
            'include_cdp': True,
            'snapshots': one_qcow['source_snapshots'],
            'not_need_nbd': True
        }))
        result_mount_snapshot['delete_hash'] = False
        boxService.box_service.stopBackupOptimize([result_mount_snapshot])


class CctQueryT1(task.Task):
    default_provides = 'task_context'

    def __init__(self, name, task_id, inject=None):
        super(CctQueryT1, self).__init__(r'CctQueryT1 {}'.format(name), inject=inject)
        self._task_id = task_id
        self._status_code = r'CctQueryT1'
        self._status_desc = xdata.get_type_name(STATUS_MAP, self._status_code)

    def execute(self, task_context, **kwargs):
        context_helper = TaskContextHelper(task_context)
        try:
            if context_helper.has_error():
                return context_helper.context

            ClusterCdpTaskExecutor.set_status_and_create_log(self._task_id,
                                                             context_helper.get_master_host_ident(),
                                                             self._status_code,
                                                             self._status_desc)

            _logger.info('begin CctQueryT1 ...')

            cluster_cdp_task_obj, cluster_cdp_task_config, schedule_obj, schedule_config = load_objs(self._task_id)
            base_info = ClusterBackupBaseInfo(context_helper.get_cluster_info())

            helper = CctHelper(base_info)

            tokens = base_info.get_cluster_disks_cdp_token()

            sleep = tasks.Sleep(schedule_obj.id, sender=m.ClusterBackupSchedule)
            sub_tasks = CctBaseBackup.query_sub_tasks(cluster_cdp_task_obj)
            checker = partial(_is_canceled, self._task_id)

            now_timestamp = time.time()

            more_file_dict = dict()

            while True:
                _logger.info(r'query T1 step {} {}'.format(
                    now_timestamp, datetime.datetime.fromtimestamp(now_timestamp)))

                t1_host, _ = FindSafeTimeHelper.get_safe_timestamp_after_chk(more_file_dict, tokens, now_timestamp)
                if t1_host:
                    _logger.info('find t1 : {} '.format(t1_host))
                    context_helper.set_t1_timestamp(t1_host)
                    break

                some_one_not_cdp = \
                    CctCdpBackup.check_all_host_in_cdp_status(checker, schedule_obj, sub_tasks, self._task_id)
                if not some_one_not_cdp:
                    helper.force_generic_cdp_file()
                    sleep.sleep(30)
                else:
                    xlogging.raise_and_logging_error('主机不在持续保护状态', 'some_one_not_cdp')

            _logger.info('CctQueryT1 ok')

        except xlogging.BoxDashboardException as bde:
            _logger.error(r'CctQueryT1 failed : {} | {}'.format(bde.msg, bde.debug))
            context_helper.set_error((self._status_desc + '失败，' + bde.msg,
                                      r'CctQueryT1 failed : {}'.format(bde.debug),))
            context_helper.set_error_step('CctQueryT1')
            ClusterCdpTaskExecutor.set_status_and_create_log(self._task_id,
                                                             context_helper.get_master_host_ident(),
                                                             self._status_code,
                                                             context_helper.get_err_msg(),
                                                             context_helper.get_err_debug())
        except Exception as e:
            _logger.error(r'CctSplitT0 failed : {}'.format(e), exc_info=True)
            context_helper.set_error((self._status_desc + '失败', r'CctQueryT1 failed : {}'.format(e),))
            context_helper.set_error_step('CctQueryT1')
            ClusterCdpTaskExecutor.set_status_and_create_log(self._task_id,
                                                             context_helper.get_master_host_ident(),
                                                             self._status_code,
                                                             context_helper.get_err_msg(),
                                                             context_helper.get_err_debug())

        return context_helper.context


class CctCreateHostSnapshot(task.Task):
    default_provides = 'task_context'

    def __init__(self, name, task_id, inject=None):
        super(CctCreateHostSnapshot, self).__init__(r'CctCreateHostSnapshot {}'.format(name), inject=inject)
        self._task_id = task_id
        self._status_code = r'CctCreateHostSnapshot'
        self._status_desc = xdata.get_type_name(STATUS_MAP, self._status_code)

    @staticmethod
    def _create_host_snapshot_by_old(current_host_snapshot, start_datetime):
        host_snapshot = m.HostSnapshot.objects.create(
            host=current_host_snapshot.host,
            start_datetime=start_datetime,
            successful=current_host_snapshot.successful,
            ext_info=current_host_snapshot.ext_info,
            display_status=current_host_snapshot.display_status,
            is_cdp=current_host_snapshot.is_cdp,
            schedule=current_host_snapshot.schedule,
            cluster_schedule=current_host_snapshot.cluster_schedule,
            cluster_finish_datetime=current_host_snapshot.cluster_finish_datetime,
            remote_schedule=current_host_snapshot.remote_schedule,
        )
        host_snapshot_cdp = m.HostSnapshotCDP.objects.create(host_snapshot=host_snapshot,
                                                             first_datetime=start_datetime)
        return host_snapshot, host_snapshot_cdp

    def execute(self, task_context, **kwargs):
        context_helper = TaskContextHelper(task_context)
        try:
            if context_helper.has_error():
                return context_helper.context

            cluster_cdp_task_obj, cluster_cdp_task_config, schedule_obj, schedule_config = load_objs(self._task_id)

            _check_run_twice(cluster_cdp_task_obj, 'CctCreateHostSnapshot')

            ClusterCdpTaskExecutor.set_status_and_create_log(self._task_id,
                                                             context_helper.get_master_host_ident(),
                                                             self._status_code,
                                                             self._status_desc)

            # 需要创建的对象 host_snapshot host_snapshot_cdp 开始时间是t1
            # 对于集群磁盘 diff qcow + t0_after_file, diff 的parent修正成 valid_last_disk_snapshot_ident
            # 对于非集群盘 只用改变host_snapshot的指向
            base_info = ClusterBackupBaseInfo(context_helper.get_cluster_info())
            current_host_snapshot = base_info.get_current_master_host_snapshot()
            cdp_task = current_host_snapshot.cdp_task
            t1_start_datetime = datetime.datetime.fromtimestamp(float(context_helper.get_t1_timestamp()))
            with transaction.atomic():
                new_host_snapshot, new_host_snapshot_cdp = self._create_host_snapshot_by_old(current_host_snapshot,
                                                                                             t1_start_datetime)
                _logger.info('create new_host_snapshot {}'.format(new_host_snapshot.id))
                # 更改快照链
                new_host_snapshot_ext_info = new_host_snapshot.ext_info
                for disk_snapshot in current_host_snapshot.disk_snapshots.all():
                    new_host_snapshot_ext_info = self._update_disk_snapshot(base_info, disk_snapshot, new_host_snapshot,
                                                                            new_host_snapshot_ext_info)

                _logger.info('update cdp_task {} host_snapshot {} to {} start_datetime {} to {}'.format(
                    cdp_task.id,
                    cdp_task.host_snapshot.id,
                    new_host_snapshot.id,
                    cdp_task.start_datetime,
                    t1_start_datetime))
                cdp_task.host_snapshot = new_host_snapshot
                cdp_task.start_datetime = t1_start_datetime
                cdp_task.save(update_fields=['host_snapshot', 'start_datetime'])

                new_host_snapshot.ext_info = new_host_snapshot_ext_info
                new_host_snapshot.finish_datetime = timezone.now()
                new_host_snapshot.cluster_visible = True
                new_host_snapshot.cluster_finish_datetime = timezone.now()
                new_host_snapshot.save(update_fields=[
                    'finish_datetime', 'cluster_visible', 'cluster_finish_datetime', 'ext_info'])

                # for backup_task in task_context['backup_sub_tasks']:
                #     host_snapshot_obj = m.HostSnapshot.objects.get(id=backup_task['host_snapshot_obj_id'])
                #     if host_snapshot_obj.host.ident == schedule_config['master_node']:
                #         continue

                for backup_task in context_helper.get_backup_sub_tasks():
                    host_snapshot_obj = m.HostSnapshot.objects.get(id=backup_task['host_snapshot_obj_id'])
                    if host_snapshot_obj.host.ident == schedule_config['master_node']:
                        continue
                    host_snapshot_obj.cluster_finish_datetime = timezone.now()
                    host_snapshot_obj.save(update_fields=['cluster_finish_datetime', ])
                context_helper.set_create_host_snapshot_successful()
                context_helper.set_new_host_snapshot_id(new_host_snapshot.id)

                agent_valid_disk_snapshot = AgentValidDiskSnapshotInfo(schedule_obj)
                context_helper.set_need_clean_qcow_ident_list(agent_valid_disk_snapshot.clean_slave_qcow_history())

        except xlogging.BoxDashboardException as bde:
            _logger.error(r'CctCreateHostSnapshot failed : {} | {}'.format(bde.msg, bde.debug))
            context_helper.set_error((self._status_desc + '失败，' + bde.msg,
                                      r'CctCreateHostSnapshot failed : {}'.format(bde.debug),))
            context_helper.set_error_step('CctCreateHostSnapshot')
            ClusterCdpTaskExecutor.set_status_and_create_log(self._task_id,
                                                             context_helper.get_master_host_ident(),
                                                             self._status_code,
                                                             context_helper.get_err_msg(),
                                                             context_helper.get_err_debug())
        except Exception as e:
            _logger.error(r'CctCreateHostSnapshot failed : {}'.format(e), exc_info=True)
            context_helper.set_error((self._status_desc + '失败', r'CctCreateHostSnapshot failed : {}'.format(e),))
            context_helper.set_error_step('CctCreateHostSnapshot')
            ClusterCdpTaskExecutor.set_status_and_create_log(self._task_id,
                                                             context_helper.get_master_host_ident(),
                                                             self._status_code,
                                                             context_helper.get_err_msg(),
                                                             context_helper.get_err_debug())

        return context_helper.context

    @staticmethod
    def _update_disk_snapshot(base_info, disk_snapshot, new_host_snapshot, new_host_snapshot_ext_info):
        valid_last_disk_snapshot, diff_disk_snapshot, t0_after_disk_snapshot = base_info.load_cluster_disk_info(
            disk_snapshot)
        if diff_disk_snapshot:  # 说明是集群盘
            _logger.info('update diff_disk_snapshot {} parent {} to {} host_snapshot {} to {}'.format(
                diff_disk_snapshot,
                diff_disk_snapshot.parent_snapshot,
                valid_last_disk_snapshot,
                diff_disk_snapshot.host_snapshot.id if diff_disk_snapshot.host_snapshot else None,
                new_host_snapshot.id))
            diff_disk_snapshot.parent_snapshot = valid_last_disk_snapshot
            diff_disk_snapshot.host_snapshot = new_host_snapshot
            diff_disk_snapshot.save(update_fields=['host_snapshot', 'parent_snapshot'])

            _logger.info('update t0_after_disk_snapshot {} parent {} to {}'.format(
                t0_after_disk_snapshot, t0_after_disk_snapshot.parent_snapshot, diff_disk_snapshot))
            t0_after_disk_snapshot.parent_snapshot = diff_disk_snapshot
            t0_after_disk_snapshot.save(update_fields=['parent_snapshot'])
            new_host_snapshot_ext_info = new_host_snapshot_ext_info.replace('"{}"'.format(disk_snapshot.ident),
                                                                            '"{}"'.format(diff_disk_snapshot.ident))

            # 修正cdp流的disk
            _logger.debug('fix_disk_id_in_disk_snapshot_chain diff_disk_snapshot {} disk {}'.format(
                diff_disk_snapshot, diff_disk_snapshot.disk_id))
            CctMergeDataWhenError.fix_disk_id_in_disk_snapshot_chain(diff_disk_snapshot, diff_disk_snapshot.disk_id)

        else:
            empty_qcow_ident = uuid.uuid4().hex
            empty_qcow_path = os.path.join(
                os.path.dirname(disk_snapshot.image_path), '{}.qcow'.format(empty_qcow_ident))
            empty_qcow_hash = os.path.join(
                os.path.dirname(disk_snapshot.image_path), '{}.hash'.format(empty_qcow_ident))

            assert not boxService.box_service.isFileExist(empty_qcow_path)

            try:
                snapshot_ice_params = IMG.ImageSnapshotIdent(empty_qcow_path, empty_qcow_ident)
                handle = boxService.box_service.createNormalDiskSnapshot(
                    snapshot_ice_params, [], disk_snapshot.bytes,
                    r'PiD{:x} boxdashboard|empty_qcow'.format(os.getpid()))
                boxService.box_service.runCmd('touch {}'.format(empty_qcow_hash))
                boxService.box_service.closeNormalDiskSnapshot(handle, True)
            except Exception:
                boxService.box_service.remove(empty_qcow_path)
                boxService.box_service.remove(empty_qcow_hash)
                raise

            new_disk_snapshot_obj = m.DiskSnapshot.objects.create(
                disk=disk_snapshot.disk,
                display_name=disk_snapshot.display_name,
                parent_snapshot=disk_snapshot,
                image_path=empty_qcow_path,
                ident=empty_qcow_ident,
                host_snapshot=new_host_snapshot,
                bytes=disk_snapshot.bytes,
                type=disk_snapshot.type,
                boot_device=disk_snapshot.boot_device,
                inc_date_bytes=0,
                reorganized_hash=True,
                ext_info=disk_snapshot.ext_info,
            )

            new_host_snapshot_ext_info = new_host_snapshot_ext_info.replace('"{}"'.format(disk_snapshot.ident),
                                                                            '"{}"'.format(new_disk_snapshot_obj.ident))

            _logger.info(r'create empty disk snapshot after {}'.format(disk_snapshot.ident))

            for child_obj in disk_snapshot.child_snapshots.exclude(id=new_disk_snapshot_obj.id).all():
                child_obj.parent_snapshot = new_disk_snapshot_obj
                child_obj.save(update_fields=['parent_snapshot', ])
                _logger.info(r'move child {} to new parent'.format(child_obj.ident))

            token_obj = m.CDPDiskToken.objects.get(parent_disk_snapshot=disk_snapshot)
            token_obj.parent_disk_snapshot = disk_snapshot
            token_obj.save(update_fields=['parent_disk_snapshot', ])

        return new_host_snapshot_ext_info


class CctStopCdpWhenError(task.Task):
    """
    发生错误就停止主机的CDP
    """
    default_provides = 'task_context'

    def __init__(self, name, task_id, inject=None):
        super(CctStopCdpWhenError, self).__init__(r'CctStopCdpWhenError {}'.format(name), inject=inject)
        self._task_id = task_id
        self._status_code = r'CctStopCdpWhenError'
        self._status_desc = xdata.get_type_name(STATUS_MAP, self._status_code)

    @staticmethod
    def get_host_snapshots(task_object):
        host_snapshots = list()
        for sub_task in task_object.sub_tasks.all():
            if hasattr(sub_task, 'host_snapshot') and sub_task.host_snapshot:
                host_snapshots.append(sub_task.host_snapshot)

        return host_snapshots

    @staticmethod
    def stop_host_cdp(task_object):
        hosts = task_object.schedule.hosts.all()
        for host in hosts:
            try:
                boxService.box_service.stopCdpStatus(host.ident)
            except Exception as e:
                _logger.warning(r'CBT_StopCdp call stopCdpStatus {} failed {}'.format(host.ident, e))

        for host_snapshot in CctStopCdpWhenError.get_host_snapshots(task_object):
            force_close_files = list()
            for disk_snapshot in host_snapshot.disk_snapshots.all():
                force_close_files.append(disk_snapshot.ident)
            work_processors.HostBackupWorkProcessors.forceCloseBackupFiles(force_close_files)
            if not host_snapshot.finish_datetime:
                host_snapshot.finish_datetime = timezone.now()
                host_snapshot.successful = False
                host_snapshot.save(update_fields=['finish_datetime', 'successful', ])

        token_str_list = list()

        for sub_cdp_task_obj in m.CDPTask.objects.filter(cluster_task=task_object).all():
            token_str_list.extend(snapshot.Tokens.stop_cdp_task(sub_cdp_task_obj))

        for mapper_token_object in m.ClusterTokenMapper.objects.filter(cluster_task=task_object).all():
            token_str_list.append(
                (mapper_token_object.file_token.token
                 if mapper_token_object.file_token else mapper_token_object.agent_token)
            )

        for token_str in set(token_str_list):
            try:
                boxService.box_service.updateToken(
                    KTService.Token(token=token_str, snapshot=[], expiryMinutes=0))
            except Exception as e:
                _logger.warning('call boxService.updateToken failed. {}'.format(e))

    def execute(self, task_context, *args, **kwargs):
        context_helper = TaskContextHelper(task_context)
        if context_helper.has_error() and context_helper.confirm_cluster_info_is_in_task_contest():
            try:
                ClusterCdpTaskExecutor.set_status_and_create_log(self._task_id,
                                                                 context_helper.get_master_host_ident(),
                                                                 self._status_code,
                                                                 self._status_desc)

                cluster_cdp_task_obj, cluster_cdp_task_config, schedule_obj, schedule_config = load_objs(self._task_id)
                CctStopCdpWhenError.stop_host_cdp(cluster_cdp_task_obj)
            except Exception as e:
                _logger.error('CctStopCdpWhenError failed : {}'.format(e), exc_info=True)

        return context_helper.context


class CctMergeDataWhenError(task.Task):
    default_provides = 'task_context'

    def __init__(self, name, task_id, inject=None):
        super(CctMergeDataWhenError, self).__init__(r'CctMergeDataWhenError {}'.format(name), inject=inject)
        self._task_id = task_id
        self._status_code = r'CctMergeDataWhenError'
        self._status_desc = xdata.get_type_name(STATUS_MAP, self._status_code)

    def execute(self, task_context, *args, **kwargs):
        context_helper = TaskContextHelper(task_context)
        if context_helper.has_error() and context_helper.confirm_cluster_info_is_in_task_contest():
            try:
                ClusterCdpTaskExecutor.set_status_and_create_log(self._task_id,
                                                                 context_helper.get_master_host_ident(),
                                                                 self._status_code,
                                                                 self._status_desc)

                cluster_cdp_task_obj, cluster_cdp_task_config, schedule_obj, schedule_config = load_objs(self._task_id)
                agent_valid_disk_snapshot = AgentValidDiskSnapshotInfo(schedule_obj)
                base_info = ClusterBackupBaseInfo(context_helper.get_cluster_info())

                all_current_info = base_info.get_current_backup_info()

                for cluster_disk_ident, current_info in all_current_info.items():
                    assert current_info['cdp_token_for_storage']
                    if current_info['current_disk_snapshot_ident']:
                        self.move_cdps_to_parent(
                            current_info['current_disk_snapshot_ident'], current_info['cdp_token_for_storage'])
                    elif current_info['depend_disk_snapshot_ident']:
                        self.move_cdps_to_parent(
                            current_info['depend_disk_snapshot_ident'], current_info['cdp_token_for_storage'])
                    else:
                        agent_valid_disk_snapshot.clean_slave_history(cluster_disk_ident)

            except Exception as e:
                _logger.error('CctStopCdpWhenError failed : {}'.format(e), exc_info=True)
                self._set_next_full_backup()

        return context_helper.context

    @staticmethod
    def fix_disk_id_in_disk_snapshot_chain(disk_snapshot_obj, disk_obj_id):
        child_one = disk_snapshot_obj

        while child_one:
            if child_one.disk_id != disk_obj_id:
                child_one.disk_id = disk_obj_id
                child_one.save(update_fields=['disk', ])
            child_one = child_one.child_snapshots.first()  # 设计上不会有分叉

    @staticmethod
    def move_cdps_to_parent(parent_ident, token):
        cdp_head_obj = (m.DiskSnapshot.objects.filter(cdp_info__token__token=token)
                        .order_by('cdp_info__first_timestamp').first())
        if not cdp_head_obj:
            _logger.info(r'move_cdps_to_parent skip. because no cdp obj')
            return
        parent_obj = m.DiskSnapshot.objects.get(ident=parent_ident)
        cdp_head_obj.parent_snapshot = parent_obj
        cdp_head_obj.save(update_fields=['parent_snapshot', ])

        CctMergeDataWhenError.fix_disk_id_in_disk_snapshot_chain(cdp_head_obj, parent_obj.disk_id)

    def _set_next_full_backup(self):
        try:
            cluster_cdp_task_obj, cluster_cdp_task_config, schedule_obj, schedule_config = load_objs(self._task_id)
            agent_valid_disk_snapshot = AgentValidDiskSnapshotInfo(schedule_obj)
            clean_invalid_data_tasks = agent_valid_disk_snapshot.force_clean_all_history()
            CctStartBackup.do_clean_invalid_data_task(clean_invalid_data_tasks)
        except Exception as e:
            _logger.error(r'_set_next_full_backup failed !!! : {}'.format(e), exc_info=True)
            raise  # 强制中断流程，需要登陆后台，手工修正


class CctUnlockResource(task.Task):
    """
    解锁资源
    """
    default_provides = 'task_context'

    def __init__(self, name, task_id, inject=None):
        super(CctUnlockResource, self).__init__(r'CctUnlockResource {}'.format(name), inject=inject)
        self._task_id = task_id

    def execute(self, task_context, *args, **kwargs):
        context_helper = TaskContextHelper(task_context)
        lock_info = context_helper.get_lock_info()

        _logger.info('CctUnlockResource: {}, {}'.format(self._task_id, lock_info))
        if lock_info:
            CBT_StopCdp.unlock_snapshots(context_helper.get_lock_info())
            context_helper.clear_lock_info()

        return context_helper.context


class CctCleanTemporary(task.Task):
    default_provides = 'task_context'

    def __init__(self, name, task_id, inject=None):
        super(CctCleanTemporary, self).__init__(r'CctCleanTemporary {}'.format(name), inject=inject)
        self._task_id = task_id
        self._status_code = r'CctCleanTemporary'
        self._status_desc = xdata.get_type_name(STATUS_MAP, self._status_code)

    def execute(self, task_context, *args, **kwargs):
        context_helper = TaskContextHelper(task_context)
        try:
            ClusterCdpTaskExecutor.set_status_and_create_log(self._task_id,
                                                             context_helper.get_master_host_ident(),
                                                             self._status_code,
                                                             self._status_desc)

            for need_clean_qcow_ident in context_helper.get_need_clean_qcow_ident_list():
                self._clean_qcow_ident(need_clean_qcow_ident)

            if context_helper.is_create_host_snapshot_successful():
                self._clean_diff_qcow_org(context_helper.context)
                self._clean_t0_files(context_helper.context)

        except Exception as e:
            _logger.error(r'CctCleanTemporary failed : {}'.format(e), exc_info=True)

        return context_helper.context

    def _clean_diff_qcow_org(self, task_context):
        base_info = ClusterBackupBaseInfo(task_context['cluster_info'])
        for org_file in base_info.get_diff_qcow_org_files():
            self._delete_snapshot(org_file['path'], org_file['ident'])

    @staticmethod
    def _delete_snapshot(path, ident):
        _logger.info('_delete_snapshot {} {}'.format(path, ident))
        if m.DiskSnapshot.is_cdp_file(path):
            try:
                disk_snapshot_obj = m.DiskSnapshot.objects.get(image_path=path)
                disk_snapshot_obj.merged = True
                disk_snapshot_obj.deleting = True
                disk_snapshot_obj.parent_snapshot = None
                disk_snapshot_obj.save(update_fields=['merged', 'deleting', 'parent_snapshot'])
            except m.DiskSnapshot.DoesNotExist:
                pass  # do nothing
            spaceCollection.DeleteCdpFileTask(
                spaceCollection.DeleteCdpFileTask.create(path)).work()
        else:
            try:
                disk_snapshot_obj = m.DiskSnapshot.objects.get(ident=ident)
                disk_snapshot_obj.merged = True
                disk_snapshot_obj.deleting = True
                disk_snapshot_obj.host_snapshot = None
                disk_snapshot_obj.parent_snapshot = None
                disk_snapshot_obj.save(update_fields=['merged', 'deleting', 'host_snapshot', 'parent_snapshot'])
            except m.DiskSnapshot.DoesNotExist:
                pass  # do nothing
            spaceCollection.DeleteDiskSnapshotTask(
                spaceCollection.DeleteDiskSnapshotTask.create(
                    path, ident)
            ).work()

    @staticmethod
    def _clean_qcow_ident(need_clean_qcow_ident):
        try:
            disk_snapshot_obj = m.DiskSnapshot.objects.get(ident=need_clean_qcow_ident)
        except m.DiskSnapshot.DoesNotExist:
            pass  # do nothing
        else:
            CctCleanTemporary._delete_snapshot(disk_snapshot_obj.image_path, disk_snapshot_obj.ident)

    @staticmethod
    def _clean_t0_files(task_context):
        base_info = ClusterBackupBaseInfo(task_context['cluster_info'])
        for t0_file_path in base_info.get_t0_files():
            CctCleanTemporary._delete_snapshot(t0_file_path, '')


class CctCdpBackup(task.Task):
    """
    CDP持续备份阶段
        退出条件为：任意节点CDP备份断开，或者 计划被禁用
    """
    default_provides = 'task_context'

    def __init__(self, name, task_id, inject=None):
        super(CctCdpBackup, self).__init__(r'CctCdpBackup {}'.format(name), inject=inject)
        self._task_id = task_id
        self._status_code = r'CctCdpBackup'
        self._status_desc = xdata.get_type_name(STATUS_MAP, self._status_code)

    def execute(self, task_context, *args, **kwargs):
        context_helper = TaskContextHelper(task_context)
        try:
            if context_helper.has_error():
                return context_helper.context

            _logger.info(r'begin wait cdp step ...')

            cluster_cdp_task_obj, cluster_cdp_task_config, schedule_obj, schedule_config = load_objs(self._task_id)
            sleep = tasks.Sleep(schedule_obj.id, sender=m.ClusterBackupSchedule)

            ClusterCdpTaskExecutor.set_status_and_create_log(self._task_id,
                                                             task_context.get('master_host_ident'),
                                                             self._status_code,
                                                             self._status_desc + '({})'.format(';'.join(
                                                                 self._get_all_host_name(cluster_cdp_task_obj))))

            self._check(self._task_id, cluster_cdp_task_obj, schedule_obj, sleep)

            _logger.info(r'end wait cdp step ...')
        except xlogging.BoxDashboardException as bde:
            _logger.error(r'CctCdpBackup failed : {} | {}'.format(bde.msg, bde.debug))
            context_helper.set_error((self._status_desc + '失败，' + bde.msg,
                                      r'CctCdpBackup failed : {}'.format(bde.debug),))
            context_helper.set_error_step('CctCdpBackup')
            ClusterCdpTaskExecutor.set_status_and_create_log(self._task_id,
                                                             context_helper.get_master_host_ident(),
                                                             self._status_code,
                                                             context_helper.get_err_msg(),
                                                             context_helper.get_err_debug())
        except Exception as e:
            _logger.error(r'CctCdpBackup failed : {}'.format(e), exc_info=True)
            context_helper.set_error((self._status_desc + '失败', r'CctCdpBackup failed : {}'.format(e),))
            context_helper.set_error_step('CctCdpBackup')
            ClusterCdpTaskExecutor.set_status_and_create_log(self._task_id,
                                                             context_helper.get_master_host_ident(),
                                                             self._status_code,
                                                             context_helper.get_err_msg(),
                                                             context_helper.get_err_debug())
        return context_helper.context

    @staticmethod
    def _get_all_host_name(cluster_cdp_task_obj):
        sub_tasks = CctBaseBackup.query_sub_tasks(cluster_cdp_task_obj)
        return [m.Host.objects.get(ident=sub_task['host_ident']).name for sub_task in sub_tasks]

    @staticmethod
    def _check(task_id, cluster_cdp_task_obj, schedule_obj, sleep):
        sub_tasks = CctBaseBackup.query_sub_tasks(cluster_cdp_task_obj)
        checker = partial(_is_canceled, task_id)
        some_one_not_cdp = False
        while not some_one_not_cdp:
            _logger.info(r'CctCdpBackup: cdping step, {}'.format(task_id))

            some_one_not_cdp = CctCdpBackup.check_all_host_in_cdp_status(checker, schedule_obj, sub_tasks, task_id)
            if not some_one_not_cdp:
                CctCdpBackup.update_cdp_host_snapshot_last_datetime(sub_tasks)
                sleep.sleep(30)

    @staticmethod
    def update_cdp_host_snapshot_last_datetime(sub_tasks):
        for sub_task in sub_tasks:
            tasks.update_cdp_host_snapshot_last_datetime(sub_task['sub_task_id'])

    @staticmethod
    def check_all_host_in_cdp_status(checker, schedule_obj, sub_tasks, task_id, include_backup=False):
        for sub_task in sub_tasks:
            tasks.CDPTaskWorker.UserCancelCheck(schedule_obj, sub_task['sub_task_id'], checker).check()
            host_status = boxService.box_service.GetStatus(sub_task['host_ident'])
            if ('cdp_asy' in host_status) or ('cdp_syn' in host_status):
                continue
            if include_backup and 'backup' in host_status:
                continue

            _logger.warning(r'CctCdpBackup: not cdping {} {} {}'.format(task_id, sub_task['host_ident'], host_status))
            return True  # 任意节点CDP备份断开
        else:
            return False


class CctStopCdp(task.Task):
    """
    停止CDP备份
    """
    default_provides = 'task_context'

    def __init__(self, name, task_id, inject=None):
        super(CctStopCdp, self).__init__(r'CctStopCdp {}'.format(name), inject=inject)
        self._task_id = task_id
        self._status_code = r'CctStopCdp'
        self._status_desc = xdata.get_type_name(STATUS_MAP, self._status_code)

    def execute(self, task_context, *args, **kwargs):
        context_helper = TaskContextHelper(task_context)
        try:
            if not context_helper.is_create_host_snapshot_successful():  # 只有在创建成功的情况下才需要停止cdp
                _logger.info(r'skip CctStopCdp')
                return context_helper.context

            ClusterCdpTaskExecutor.set_status_and_create_log(self._task_id,
                                                             context_helper.get_master_host_ident(),
                                                             self._status_code,
                                                             self._status_desc)

            _logger.warning('CctStopCdp: will stop cdp {}'.format(self._task_id))
            cluster_cdp_task_obj, cluster_cdp_task_config, schedule_obj, schedule_config = load_objs(self._task_id)
            CctStopCdpWhenError.stop_host_cdp(cluster_cdp_task_obj)
            _logger.warning('CctStopCdp: stop cdp end {}'.format(self._task_id))
        except Exception as e:
            _logger.error('CctStopCdp failed : {}'.format(e), exc_info=True)
        return context_helper.context


class CctFixCdpLastTime(task.Task):
    def __init__(self, name, task_id, inject=None):
        super(CctFixCdpLastTime, self).__init__(r'CctFixCdpLastTime {}'.format(name), inject=inject)
        self._task_id = task_id

    def execute(self, task_context, *args, **kwargs):
        context_helper = TaskContextHelper(task_context)
        try:
            if not context_helper.is_create_host_snapshot_successful():  # 只有在创建成功的情况下才需要修正时间
                _logger.info(r'skip CctFixCdpLastTime')
                return context_helper.context

            _logger.info(r'begin fix cdp last time step ...')

            base_info = ClusterBackupBaseInfo(task_context['cluster_info'])
            tokens = base_info.get_cluster_disks_cdp_token()

            now_timestamp = time.time() - 60  # 最后60秒的数据不要

            if now_timestamp <= context_helper.get_t1_timestamp():
                end_timestamp = context_helper.get_t1_timestamp()
            else:
                end_timestamp = FindSafeTimeHelper.get_safe_timestamp_more_newer(tokens, now_timestamp)

            if (end_timestamp and
                    self.fix_cdp_host_snapshot_last_datetime(task_context['new_host_snapshot_id'], end_timestamp)):
                pass  # do nothing
            else:
                self.set_host_snapshot_invalid(task_context['new_host_snapshot_id'])

            _logger.info(r'finish fix cdp last time end')
        except Exception as e:
            _logger.error(r'CctFixCdpLastTime failed : {}'.format(e), exc_info=True)

        return context_helper.context

    @staticmethod
    @xlogging.convert_exception_to_value(False)
    def fix_cdp_host_snapshot_last_datetime(host_snapshot_id, end_timestamp):
        _logger.info('fix_cdp_host_snapshot_last_datetime {} {}'.format(host_snapshot_id, end_timestamp))
        last_datetime = datetime.datetime.fromtimestamp(end_timestamp)
        cdp_host_snapshot_obj = m.HostSnapshotCDP.objects.get(host_snapshot_id=host_snapshot_id)
        if cdp_host_snapshot_obj.first_datetime <= last_datetime:
            cdp_host_snapshot_obj.last_datetime = last_datetime
            cdp_host_snapshot_obj.save(update_fields=['last_datetime', ])
            return True
        else:
            _logger.error(r'fix_cdp_host_snapshot_last_datetime !!!! {} > {}'.format(
                cdp_host_snapshot_obj.first_datetime, last_datetime))
            return False

    @staticmethod
    def set_host_snapshot_invalid(host_snapshot_id):
        _logger.info(r'CctFixCdpLastTime set_host_snapshot_invalid {}'.format(host_snapshot_id))
        host_snapshot_obj = m.HostSnapshot.objects.get(id=host_snapshot_id)
        host_snapshot_obj.cluster_visible = False
        host_snapshot_obj.save(update_fields=['cluster_visible', ])


class CctFinishBackup(task.Task):
    def __init__(self, name, task_id, inject=None):
        super(CctFinishBackup, self).__init__(r'CctFinishBackup {}'.format(name), inject=inject)
        self._task_id = task_id
        self._status_code = r'CctFinishBackup'
        self._status_desc = xdata.get_type_name(STATUS_MAP, self._status_code)

    def execute(self, task_context, *args, **kwargs):
        context_helper = TaskContextHelper(task_context)
        try:
            _logger.info(r'begin finish backup step ...')

            ClusterCdpTaskExecutor.set_status_and_create_log(self._task_id,
                                                             context_helper.get_master_host_ident(),
                                                             self._status_code,
                                                             self._status_desc)

            cluster_cdp_task_obj, cluster_cdp_task_config, schedule_obj, schedule_config = load_objs(self._task_id)

            successful = not context_helper.has_error()

            self._set_host_snapshot_cdp_stop(cluster_cdp_task_obj)

            self._finish_cluster_cdp_task(cluster_cdp_task_obj, successful)

            _logger.info(r'finish backup end')
        except Exception as e:
            _logger.error(r'CctFinishBackup failed : {}'.format(e), exc_info=True)

    @staticmethod
    def _set_host_snapshot_cdp_stop(cluster_cdp_task_obj):

        @xlogging.convert_exception_to_value(None)
        def _fix_cdp_task_finish_datetime():
            if not cdp_task_object.finish_datetime:
                _logger.warning(r'fix cdp_task_object finish_datetime : {}'.format(cdp_task_object.id))
                cdp_task_object.finish_datetime = timezone.now()
                cdp_task_object.save(update_fields=['finish_datetime', ])

        @xlogging.convert_exception_to_value(None)
        def _set_stop():
            if cdp_task_object.host_snapshot:
                cdp_host_snapshot_object = cdp_task_object.host_snapshot.cdp_info
                if not cdp_host_snapshot_object.stopped:
                    cdp_host_snapshot_object.stopped = True
                    cdp_host_snapshot_object.save(update_fields=['stopped', ])

        for cdp_task_object in cluster_cdp_task_obj.sub_tasks.all():
            _fix_cdp_task_finish_datetime()
            _set_stop()

    @staticmethod
    def _finish_cluster_cdp_task(cluster_cdp_task_obj, successful):
        cluster_cdp_task_obj.finish_datetime = datetime.datetime.now()
        cluster_cdp_task_obj.successful = successful
        cluster_cdp_task_obj.save(update_fields=['finish_datetime', 'successful', ])
