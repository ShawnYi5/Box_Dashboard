import ipaddress
import json
import queue
import time
from threading import Thread, Event

from django.db.models.signals import post_save

from apiv1.models import Host, BackupTaskSchedule, HostSnapshot, HostSnapshotCDP, UserQuota, CDPTask, HTBTask, \
    BackupTask, MigrateTask, RestoreTask, ClusterBackupTask, HostSnapshotShare, TakeOverKVM
from apiv1.serializers import MqHostSerializer, MqBackupTaskScheduleSerializer, MqHostSnapshotSerializer, \
    MqUserQuotaSerializer, MqTaskChangeCollectSerializer, MqTaskConfirmSerializer, MqRestoreTaskSerializer
from box_dashboard import xlogging

_logger = xlogging.getLogger(__name__)

MQ_QUEUE = queue.Queue(16)

MQ_PARAMS = None


def _get_mq_param(key):
    global MQ_PARAMS
    if MQ_PARAMS is None:
        try:
            with open('/etc/aio/mq_params.json') as f:
                MQ_PARAMS = json.load(f)
                """
                {
                    "enable": true,
                    "MQ_HOST": "x",
                    "MQ_USR": "y",
                    "MQ_PWD": "z",
                    "MQ_RECONNECT_INTERVAL": 15,
                    "MQ_FAILOVER_CACHE_PATH": "/var/db/mq_failover",
                    "MQ_EXCHANGE_NAME": "ex_aio",
                    "MQ_NODE": "4444"
                }
                """
        except Exception as e:
            _logger.warning(r'can NOT load mq params : {}'.format(e))
            MQ_PARAMS = dict()

    v = MQ_PARAMS.get(key, None)
    if v is None and key != 'enable':
        raise Exception('can NOT get mq param : {}'.format(key))
    return v


def hook_post_save(instance, created, **kwargs):
    if not _get_mq_param('enable'):
        return

    content_obj = None
    content_type = None
    instance_type = type(instance)

    if instance_type == Host:
        host_obj = _fetch_host_info(instance)
        if host_obj:
            content_obj = MqHostSerializer(host_obj).data
            content_type = 'host'
    elif instance_type == BackupTaskSchedule:
        backup_task_schedule_obj = instance  # _fetch_backup_task_schedule_obj(instance)
        if backup_task_schedule_obj:
            content_obj = MqBackupTaskScheduleSerializer(backup_task_schedule_obj).data
            content_type = 'backup_task_schedule'
    elif instance_type == HostSnapshot or instance_type == HostSnapshotCDP:
        host_snapshot_obj = _fetch_host_snapshot_obj(instance, instance_type)
        if host_snapshot_obj:
            content_obj = MqHostSnapshotSerializer(host_snapshot_obj).data
            content_type = 'host_snapshot'
    elif instance_type == UserQuota:
        user_quota_obj = instance  # _fetch_user_quota_obj(instance)
        if user_quota_obj:
            content_obj = MqUserQuotaSerializer(user_quota_obj).data
            content_type = 'user_quota'
    elif instance_type == MigrateTask:
        migrate_task_obj = instance
        if migrate_task_obj:
            content_obj = MqTaskChangeCollectSerializer(migrate_task_obj).data
            content_type = 'migrate_task'
    elif instance_type == RestoreTask:
        restore_task_obj = instance
        if restore_task_obj:
            content_obj = MqRestoreTaskSerializer(restore_task_obj).data
            content_type = 'restore_task'
    elif instance_type == ClusterBackupTask:
        cluster_backup_task_obj = instance
        if cluster_backup_task_obj:
            content_obj = MqTaskChangeCollectSerializer(cluster_backup_task_obj).data
            content_type = 'cluster_backup_task'
    elif instance_type == BackupTask:
        backup_task_obj = instance
        if backup_task_obj:
            content_obj = MqTaskChangeCollectSerializer(backup_task_obj).data
            content_type = 'backup_task'
    elif instance_type == CDPTask:
        cdp_task_obj = instance
        if cdp_task_obj:
            content_obj = MqTaskChangeCollectSerializer(cdp_task_obj).data
            content_type = 'cdp_task'
    elif instance_type == HTBTask:
        htb_task_obj = instance
        if htb_task_obj:
            content_obj = MqTaskChangeCollectSerializer(htb_task_obj).data
            content_type = 'htb_task'
    elif instance_type == HostSnapshotShare:
        host_snapshot_share_obj = instance
        if host_snapshot_share_obj:
            content_obj = MqTaskConfirmSerializer(host_snapshot_share_obj).data
            content_type = "filebrowser_confirm"
    elif instance_type == TakeOverKVM:
        take_over_kvm_obj = instance
        if take_over_kvm_obj:
            content_obj = MqTaskConfirmSerializer(take_over_kvm_obj).data
            content_type = "takeover_confirm"
    else:
        pass  # do nothing

    if content_obj or content_type:
        __send_content_obj(content_obj, created, content_type)


def __send_content_obj(content_obj, created, content_type):
    try:
        assert content_obj
        assert content_type
        hook_obj = {
            'content_str': json.dumps({
                "node": _get_mq_param('MQ_NODE'), "created": created, "timestamp": time.time(),
                "type": content_type, "content": content_obj}),
            'sent': Event()
        }
    except Exception:
        _logger.error(r'hook_post_save convert obj 2 str failed {} {} {}'.format(content_obj, created, content_type))
        raise
    MQ_QUEUE.put(hook_obj)
    hook_obj['sent'].wait()


def send_content_obj_to_mq(content_obj, created, content_type):
    if _get_mq_param('enable'):
        return __send_content_obj(content_obj, created, content_type)


def _models_hook_init():
    if not _get_mq_param('enable'):
        _logger.warning(r'mq mode disable')
        return None
    _logger.warning(r'mq mode enable : {}'.format(MQ_PARAMS))
    from ClwMqClient.mq_client import ClwMqPublishClient, ClwMqConnection, ClwReconnectWithInterval, \
        ClwMqPublishFailoverCache
    publish_client = ClwMqPublishClient(
        'ModelsHook PublishClient',
        ClwMqConnection(
            'ModelsHook Connection',
            _get_mq_param('MQ_HOST'), _get_mq_param('MQ_USR'), _get_mq_param('MQ_PWD'),
            xlogging.getLogger('ModelsHook_connection')
        ),
        xlogging.getLogger('ModelsHook'),
        ClwReconnectWithInterval(_get_mq_param('MQ_RECONNECT_INTERVAL')),
        ClwMqPublishFailoverCache(
            _get_mq_param('MQ_FAILOVER_CACHE_PATH'),
            xlogging.getLogger('ModelsHook_FailoverCache')
        ),
        True
    )

    assert (0 < len(_get_mq_param('MQ_NODE')) <= 4)
    post_save.connect(hook_post_save)
    return publish_client


class ModelsHook(Thread):
    def __init__(self):
        super(ModelsHook, self).__init__(name='ModelsHook')
        self.publish_client = _models_hook_init()
        if self.publish_client is not None:
            self.publish_client.init()

    def run(self):
        if self.publish_client is None:
            return

        while True:
            try:
                self.run_real()
            except Exception as e:
                _logger.error('ModelsHook error:{}'.format(e), exc_info=True)

    def run_real(self):
        hook_obj = MQ_QUEUE.get()
        while True:
            try:
                self.publish_client.basic_publish(body=hook_obj['content_str'],
                                                  exchange=_get_mq_param('MQ_EXCHANGE_NAME'))
                MQ_QUEUE.task_done()
                hook_obj['sent'].set()
                break
            except Exception as e:
                _logger.error('ModelsHook error:{}'.format(e), exc_info=True)
                time.sleep(1)


@xlogging.convert_exception_to_value(None)
def _fetch_host_info(host_obj):
    def _is_linux(system):
        system_caption = system.get('SystemCaption', '').upper()
        return 'LINUX' in system_caption

    def _extract_sys_info(system_infos):
        system = system_infos['System']
        system_caption = system.get('SystemCaption', '')
        processor_arch = system.get('ProcessorArch', '')
        operating_system = '{} {}'.format(system_caption, processor_arch)
        return {
            'operating_system': operating_system,
            'computer_name': system.get('ComputerName', ''),
            'agent_version': system.get('version', ''),
        }

    def _calc_partitions_used_bytes(partitions, is_linux):
        if is_linux:
            return '-1'

        used_bytes = 0
        for partition in partitions:
            partition_size = partition.get('PartitionSize', '')
            free_size = partition.get('FreeSize', '')
            if str(partition_size).isdigit() and str(free_size).isdigit():
                used_bytes += int(partition_size) - int(free_size)

        return str(used_bytes)

    def _extract_disk_info(system_infos):
        nonlocal host_obj
        disks_list = list()
        try:
            system, disks = system_infos['System'], system_infos['Disk']
            for disk in disks:
                is_linux, capacity_bytes = _is_linux(system), str(disk['DiskSize'])
                disk_item = {
                    'style': disk['Style'].upper(),
                    'name': '{} [{}]'.format(disk['DiskName'], disk['DiskIndex']) if is_linux else disk['DiskName'],
                    'capacity_bytes': capacity_bytes if capacity_bytes.isdigit() else '-1',  # '-1' or '456xxx'
                    'used_bytes': _calc_partitions_used_bytes(disk['Partition'], is_linux),  # '-1' or '456xxx'
                }
                disks_list.append(disk_item)
        except Exception as e:
            _logger.error('_extract_disk_info: {} {}'.format(host_obj, e), exc_info=True)
        finally:
            return {'disks_list': disks_list}

    ext_info = json.loads(host_obj.ext_info)
    ip_address = list()

    for nic in ext_info['system_infos']['Nic']:
        for ip_and_mask in nic['IpAndMask']:
            try:
                if ip_and_mask['Ip'] == '0.0.0.0' or ip_and_mask['Ip'].startswith('127.'):
                    continue
                assert ipaddress.ip_address(ip_and_mask['Ip'])
                ip_address.append({"ip": ip_and_mask['Ip'], "type": 'other'})
            except ValueError:
                pass

    local_address = ext_info['system_infos']['ConnectAddress']['LocalAddress']
    if host_obj.last_ip and host_obj.last_ip != '0.0.0.0' and (not host_obj.last_ip.startswith('127.')) \
            and local_address and local_address != '0.0.0.0' and (not local_address.startswith('127.')):
        if host_obj.last_ip != ext_info['system_infos']['ConnectAddress']['LocalAddress']:
            __set_ip_address_type(ip_address, host_obj.last_ip, 'nat_out')
            __set_ip_address_type(ip_address, local_address, 'nat_in')
        else:
            __set_ip_address_type(ip_address, local_address, 'direct')

    return {
        "base": host_obj,
        "user_account": '' if host_obj.user is None else host_obj.user.username,
        "ip_address": ip_address,
        "os_type": 'linux' if 'Linux' in ext_info['system_infos'].keys() else 'windows',
        "sys_info": _extract_sys_info(ext_info['system_infos']),
        "disk_info": _extract_disk_info(ext_info['system_infos']),
    }


def __set_ip_address_type(ip_address, ip, ip_address_type):
    for index, o in enumerate(ip_address):
        if o["ip"] == ip:
            obj = ip_address.pop(index)
            break
    else:
        obj = {"ip": ip}
    obj["type"] = ip_address_type
    ip_address.insert(0, obj)


@xlogging.convert_exception_to_value(None)
def _fetch_backup_task_schedule_obj(backup_task_schedule_obj):
    return backup_task_schedule_obj


@xlogging.convert_exception_to_value(None)
def _fetch_host_snapshot_obj(instance, instance_type):
    if instance_type == HostSnapshot:
        return instance
    elif instance_type == HostSnapshotCDP:
        return instance.host_snapshot
    else:
        assert False, r'error instance_type : {}'.format(instance_type)


@xlogging.convert_exception_to_value(None)
def _fetch_user_quota_obj(user_quota_obj):
    return user_quota_obj
