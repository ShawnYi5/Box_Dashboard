import json
import os
import subprocess
import threading
import time
import uuid
from datetime import datetime, timedelta

from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.response import Response

from apiv1.models import StorageNode, ExternalStorageDeviceConnection, Host, UserQuota, BackupTaskSchedule, \
    RemoteBackupSchedule
from box_dashboard import xlogging, boxService, xdata, xsys

_logger = xlogging.getLogger(__name__)

_cfg = ''
try:
    with open(r'/etc/aio/store_manage.cfg') as f:
        _cfg = f.read()
except Exception as e:
    _logger.warning(r'open store_manage.cfg failed {}'.format(e))

_user_quota_cache = dict()
_user_quota_cache_locker = threading.Lock()


class UpdateUserQuota(threading.Thread):
    def __init__(self):
        super(UpdateUserQuota, self).__init__(name='UpdateUserQuota')

    def run(self):
        last_update_time = time.time()
        while True:
            time.sleep(max(last_update_time + (60 * 3) - time.time(), 1))
            last_update_time = time.time()

            try:
                for quota_obj in UserQuota.objects.filter(deleted=False, storage_node__deleted=False).all():
                    if not quota_obj.storage_node.available:
                        self._clean(quota_obj)
                        continue

                    try:
                        user_available_size = UserQuotaTools(quota_obj.storage_node_id, quota_obj.user_id,
                                                             quota_obj.quota_size). \
                            get_user_available_storage_size_in_node()
                        self._set(quota_obj, user_available_size)
                    except Exception as ex:
                        self._clean(quota_obj)
                        _logger.error(r'get_user_available_storage_size_in_node {} failed {}'.format(quota_obj.id, ex),
                                      exc_info=True)
            except Exception as ee:
                _logger.error(r'UpdateDiskSnapshotCdpTimestamp failed {}'.format(ee), exc_info=True)

    @staticmethod
    def _query(quota_obj):
        with _user_quota_cache_locker:
            return _user_quota_cache.get(quota_obj.id, None)

    @staticmethod
    def _clean(quota_obj):
        with _user_quota_cache_locker:
            _user_quota_cache.pop(quota_obj.id, None)

    @staticmethod
    def _set(quota_obj, user_available_size):
        with _user_quota_cache_locker:
            _user_quota_cache[quota_obj.id] = user_available_size

    @staticmethod
    def query(quota_obj):
        r = UpdateUserQuota._query(quota_obj)
        if r is not None:
            return r

        return UserQuotaTools(quota_obj.storage_node_id, quota_obj.user_id,
                              quota_obj.quota_size). \
            get_user_available_storage_size_in_node()


# 在指定的存储结点上，获取该用户的剩余空间
class UserQuotaTools(object):
    def __init__(self, storage_node_id, user_id, quota_total, images_name='images'):
        self._storage_node_id = storage_node_id
        self._user_id = user_id
        self._quota_total = quota_total
        self._images_name = images_name

    # 检测user_id 是否合法
    @staticmethod
    def _check_user_id(user_id):
        is_valid = User.objects.filter(id=user_id).exists()
        if not is_valid:
            xlogging.raise_and_logging_error('用户不存在', 'there is not user_id exists')

    # 获取指定的"储存节点"信息
    @staticmethod
    def get_storage_node_detail(node_id, refresh_device):
        nodes_info = StorageNodeLogic.get_all_nodes(refresh_device)
        if not nodes_info:
            xlogging.raise_and_logging_error('没有任何已添加的存储节点', 'there is not any storage node')

        for node_info in nodes_info:
            if node_info['id'] == node_id:
                return node_info

        xlogging.raise_and_logging_error('没有找到指定的存储节点', 'there is not storage node (id={})'.format(node_id))

    # 获取指定的储存节点挂载路径
    @staticmethod
    def get_storage_node_base_path(node_id):
        try:
            node_base_path = StorageNode.objects.get(id=node_id).path

            if not boxService.box_service.isFolderExist(node_base_path):
                xlogging.raise_and_logging_error(r'节点挂载路径不存在',
                                                 r'{path} do not exist'.format(path=node_base_path))
            return node_base_path

        except StorageNode.DoesNotExist:
            xlogging.raise_and_logging_error('数据库查询存储节点失败', 'StorageNode(id={}) do not exist')

    # 该User的Hosts，在指定的节点上，是否存在备份文件夹(/home/aio/images/hostident1)
    # 返回Hosts的备份文件夹路径[snapshots_file_path]
    @staticmethod
    def hosts_snapshots_file_path_with_bp_data(node_base_path, user_id, images_name):
        user_hosts_ident = [host_obj.ident for host_obj in Host.objects.filter(user_id=user_id)]

        # 用户没有从属的主机
        if not user_hosts_ident:
            return []

        # 用户存在从属的主机(全部)，且这些主机在指定节点上有备份数据存在
        files_path = list()
        for host_ident in user_hosts_ident:
            snapshots_file_path = boxService.box_service.pathJoin([node_base_path, images_name, host_ident])
            if boxService.box_service.isFolderExist(snapshots_file_path):
                files_path.append(snapshots_file_path)
        return files_path

    # 计算该用户的，Hosts的snapshots_file_path大小
    @staticmethod
    def user_hosts_backup_size(snapshots_file_paths):
        used_size_in_node = 0
        for path in snapshots_file_paths:
            used_size_in_node += xsys.get_host_snapshots_file_size(path)

        return used_size_in_node

    # 在某个节点中，用户可用的空间(MB)：min(节点剩余, 配额剩余)
    @xlogging.convert_exception_to_value(0)
    def get_user_available_storage_size_in_node(self, refresh_device=False):
        # 获取节点剩余(MB)
        node_detail = self.get_storage_node_detail(self._storage_node_id, refresh_device)
        node_remain = int(node_detail['available_bytes'] / 1024 / 1024)

        # 用户配额不限制时，返回节点剩余空间
        if self._quota_total == xdata.USER_QUOTA_IS_NOT_LIMIT_VALUE:
            return node_remain

        # 用户配额限制时，计算配额当前剩余
        base_path = self.get_storage_node_base_path(self._storage_node_id)
        self._check_user_id(self._user_id)
        paths_with_bp_data = self.hosts_snapshots_file_path_with_bp_data(base_path, self._user_id, self._images_name)

        if not paths_with_bp_data:
            quota_remain = self._quota_total
        else:
            user_used_size_in_node = self.user_hosts_backup_size(paths_with_bp_data)
            quota_remain = self._quota_total - user_used_size_in_node
            quota_remain = quota_remain if quota_remain >= 0 else 0

        return min(node_remain, quota_remain)

    # 检查计划的存储节点
    @staticmethod
    def check_user_storage_size_in_node(schedule_object, more_than_mb=xdata.HOST_BACKUP_FORBID_SIZE_MB, log_ok=True):
        storage_node_ident = schedule_object.storage_node_ident
        if isinstance(schedule_object, BackupTaskSchedule) or isinstance(schedule_object, RemoteBackupSchedule):
            user_id = schedule_object.host.user.id
        else:
            user_id = schedule_object.hosts.all().first().user.id

        quota_obj = UserQuota.objects.filter(user_id=user_id, deleted=False). \
            filter(storage_node__ident=storage_node_ident, storage_node__deleted=False).first()

        if quota_obj is None:
            xlogging.raise_and_logging_error(
                '无效的存储节点',
                'invalid storage_node(ident={}), user(id={})'.format(storage_node_ident, user_id),
                http_status=xlogging.HTTP_STATUS_USER_STORAGE_NODE_NOT_VALID
            )

        node_name = quota_obj.storage_node.name

        if not quota_obj.storage_node.available:
            xlogging.raise_and_logging_error(
                '存储节点({})离线'.format(node_name),
                'storage_node(ident={}), user(id={}) not available'.format(storage_node_ident, user_id),
                http_status=xlogging.HTTP_STATUS_USER_STORAGE_NODE_NOT_ONLINE
            )

        # 在存储节点下，用户的可用空间
        user_available_size = UpdateUserQuota.query(quota_obj)
        if user_available_size < more_than_mb:
            xlogging.raise_and_logging_error(
                '({})上没有足够的存储空间'.format(node_name),
                'storage_node(ident={}), user(id={}) low size'.format(storage_node_ident, user_id),
                http_status=xlogging.HTTP_STATUS_USER_STORAGE_NODE_NOT_ENOUGH_SPACE
            )

        result_path = quota_obj.storage_node.path

        if log_ok:
            _logger.info(r'check_user_storage_size_in_node {} - {} - {} ok'.format(node_name, user_id, result_path))

        return result_path

    @staticmethod
    def is_user_allocated_any_quota(user):
        if UserQuota.objects.filter(user_id=user.id, deleted=False).exists():
            return True

        return False


class MountHelper(object):
    @staticmethod
    def _execute_cmd_and_return_code(cmd):
        with subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True) as p:
            stdout, stderr = p.communicate()
        return p.returncode, stdout, stderr

    @staticmethod
    def is_mount(dev_path):
        match_starts = dev_path + ' on '
        _, out, _ = MountHelper._execute_cmd_and_return_code('mount')
        for line in out.splitlines():
            if line.startswith(match_starts):
                return True
        else:
            return False

    @staticmethod
    def mount_nfs(nfs_path, dir_path):
        os.makedirs(dir_path, 0o755, True)
        cmd = 'mount -t nfs {} {} -o soft,noatime,nodiratime,timeo=60'.format(nfs_path, dir_path)
        r, out, err = MountHelper._execute_cmd_and_return_code(cmd)
        _logger.info(r'mount_nfs {} {} {} {}'.format(cmd, r, out, err))

    @staticmethod
    def mount_smb(smb_path, dir_path, username, password):
        os.makedirs(dir_path, 0o755, True)
        cmd = r'mount -t cifs -o nomapposix,rw,soft,username="{}",password="{}"  "{}" "{}"'.format(
            username, password, smb_path, dir_path)
        r, out, err = MountHelper._execute_cmd_and_return_code(cmd)
        _logger.info(r'mount_smb {} {} {} {}'.format(cmd, r, out, err))

    @staticmethod
    def mount_lvm(lv_path, dir_path):
        os.makedirs(dir_path, 0o755, True)
        cmd = 'mount {} {} -o defaults,noatime,nodiratime'.format(lv_path, dir_path)
        r, out, err = MountHelper._execute_cmd_and_return_code(cmd)
        _logger.info(r'mount_lvm {} {} {} {}'.format(cmd, r, out, err))


class MountNfsHelper(object):
    @staticmethod
    def mount(nfs_path, dir_path):
        MountHelper.mount_nfs(nfs_path, dir_path)


class MountSmbHelper(object):
    def __init__(self, username, password):
        self.username = username
        self.password = password

    def mount(self, smb_path, dir_path):
        MountHelper.mount_smb(smb_path, dir_path, self.username, self.password)


class MountLvmHelper(object):
    @staticmethod
    def mount(lv_path, dir_path):
        MountHelper.mount_lvm(lv_path, dir_path)


class StorageNodeLogic(object):
    locker = threading.RLock()
    update_locker = threading.RLock()
    updating = False

    @staticmethod
    def _find_low_level_node_by_logic_device_path(low_level_nodes, logic_device_path):
        for low_level_node in low_level_nodes:
            if low_level_node['logic_device_path'] == logic_device_path:
                return low_level_node

        return None

    @staticmethod
    def add_storage_node(adding_node_data, mount_path, format_device):
        low_level_nodes = boxService.box_service.enumStorageNodes()
        low_level_node = StorageNodeLogic._find_low_level_node_by_logic_device_path(
            low_level_nodes, adding_node_data['logic_device_path'])
        if low_level_node is None:
            xlogging.raise_and_logging_error(r'添加存储节点失败，无效的设备名',
                                             'invalid logic_device_path : {}'.format(adding_node_data))

        if format_device:
            StorageNodeLogic._force_initialize_internal_node(low_level_node, mount_path)
        else:
            low_level_node['mount_path'] = mount_path
            boxService.box_service.unmountStorageNode(low_level_node)
            boxService.box_service.mountStorageNode(low_level_node)

        low_level_nodes = boxService.box_service.enumStorageNodes()
        return StorageNodeLogic._find_low_level_node_by_logic_device_path(low_level_nodes,
                                                                          adding_node_data['logic_device_path'])

    @staticmethod
    def _get_node_guid_or_none(low_level_node):
        if low_level_node['has_file_system'] and (low_level_node['node_guid'] is not None) and (
                len(low_level_node['node_guid']) != 0):
            return low_level_node['node_guid']
        else:
            return None

    @staticmethod
    def _get_deleted_node_by_guid_used(node_guid):
        return StorageNode.objects.filter(deleted=True, ident__startswith=node_guid).order_by('-id').first()

    @staticmethod
    def _get_using_node_by_guid_used(node_guid):
        try:
            return StorageNode.objects.get(ident=node_guid)
        except StorageNode.DoesNotExist:
            return None

    @staticmethod
    def get_internal_storage_nodes():
        low_level_nodes = boxService.box_service.enumStorageNodes()
        internal_low_level_nodes = [low_level_node for low_level_node in low_level_nodes if
                                    low_level_node['is_internal']]
        internal_storage_nodes = list()
        using_node_guid = list()
        for low_level_node in internal_low_level_nodes:
            node_guid = StorageNodeLogic._get_node_guid_or_none(low_level_node)
            if node_guid is None:
                internal_storage_nodes.append(
                    {'device_name': low_level_node['device_name'], 'device_size': low_level_node['disk_size'],
                     'logic_device_path': low_level_node['logic_device_path'], 'old_node_id': None,
                     'status': xdata.STORAGE_NODE_STATUS_NOT_INIT})
                continue

            old_node_object = StorageNodeLogic._get_using_node_by_guid_used(node_guid)
            if old_node_object is not None:
                old_node_id = old_node_object.id
                if old_node_object.ident in using_node_guid:
                    node_status = xdata.STORAGE_NODE_STATUS_SAME_WITH_USING
                else:
                    using_node_guid.append(old_node_object.ident)
                    node_status = xdata.STORAGE_NODE_STATUS_USING
            else:
                old_node_object = StorageNodeLogic._get_deleted_node_by_guid_used(node_guid)
                if old_node_object is not None:
                    node_status = xdata.STORAGE_NODE_STATUS_INIT_BY_SELF
                    old_node_id = old_node_object.id
                else:
                    node_status = xdata.STORAGE_NODE_STATUS_INIT_BY_OTHER
                    old_node_id = None

            internal_storage_nodes.append(
                {'device_name': low_level_node['device_name'], 'device_size': low_level_node['disk_size'],
                 'logic_device_path': low_level_node['logic_device_path'], 'status': node_status,
                 'old_node_id': old_node_id})

        return internal_storage_nodes

    @staticmethod
    def get_external_storage_nodes(external_storage_device_connection_object):
        low_level_nodes = boxService.box_service.enumStorageNodes()
        external_low_level_nodes = [low_level_node for low_level_node in low_level_nodes
                                    if (
                                            (not low_level_node['is_internal']) and
                                            (low_level_node['external_ip'] ==
                                             external_storage_device_connection_object.ip) and
                                            (low_level_node['external_port'] ==
                                             external_storage_device_connection_object.port)
                                    )]

        external_storage_nodes = list()
        using_node_guid = list()

        for low_level_node in external_low_level_nodes:
            node_guid = StorageNodeLogic._get_node_guid_or_none(low_level_node)
            if node_guid is None:
                external_storage_nodes.append(
                    {'device_name': low_level_node['device_name'], 'device_size': low_level_node['disk_size'],
                     'logic_device_path': low_level_node['logic_device_path'], 'old_node_id': None,
                     'status': xdata.STORAGE_NODE_STATUS_NOT_INIT, 'lun_name': low_level_node['external_lun']})
                continue

            old_node_object = StorageNodeLogic._get_using_node_by_guid_used(node_guid)
            if old_node_object is not None:
                old_node_id = old_node_object.id
                config = json.loads(old_node_object.config)
                if (old_node_object.ident not in using_node_guid) and \
                        (config['external_ip'] == low_level_node['external_ip']) and \
                        (config['external_port'] == low_level_node['external_port']):
                    using_node_guid.append(old_node_object.ident)
                    node_status = xdata.STORAGE_NODE_STATUS_USING
                else:
                    node_status = xdata.STORAGE_NODE_STATUS_SAME_WITH_USING
            else:
                old_node_object = StorageNodeLogic._get_deleted_node_by_guid_used(node_guid)
                if old_node_object is not None:
                    node_status = xdata.STORAGE_NODE_STATUS_INIT_BY_SELF
                    old_node_id = old_node_object.id
                else:
                    node_status = xdata.STORAGE_NODE_STATUS_INIT_BY_OTHER
                    old_node_id = None

            external_storage_nodes.append(
                {'device_name': low_level_node['device_name'], 'device_size': low_level_node['disk_size'],
                 'logic_device_path': low_level_node['logic_device_path'], 'status': node_status,
                 'old_node_id': old_node_id, 'lun_name': low_level_node['external_lun']})

        return external_storage_nodes

    @staticmethod
    @xlogging.LockDecorator(update_locker)
    def update_node_status():
        def __need_enum_storage_nodes(_node_objects):
            for _node_object in _node_objects:
                _config = json.loads(_node_object.config)
                if 'nfs' in _config.keys():
                    continue
                if 'smb' in _config.keys():
                    continue
                elif 'lvm' in _config.keys():
                    continue
                else:
                    return True
            return False

        try:
            StorageNodeLogic.updating = True

            node_objects = StorageNode.objects.filter(deleted=False).all()

            if __need_enum_storage_nodes(node_objects):
                low_level_nodes = boxService.box_service.enumStorageNodes()
            else:
                low_level_nodes = list()

            StorageNodeLogic._check_node_is_mounting(low_level_nodes)

            StorageNodeLogic._update_node_status(node_objects, low_level_nodes)
        finally:
            StorageNodeLogic.updating = False

    @staticmethod
    def _check_node_is_mounting(low_level_nodes):
        for low_level_node in low_level_nodes:
            if not low_level_node['is_mounting']:
                continue

            try:
                check_dir_path = boxService.box_service.pathJoin([low_level_node['mount_path'], 'check'])
                boxService.box_service.makeDirs(check_dir_path)
                boxService.box_service.remove(check_dir_path)
            except Exception as e:
                _logger.error(r'_check_node_is_mounting {} failed {}'.format(low_level_node['mount_path'], e))
                low_level_node['is_mounting'] = False

    @staticmethod
    def _find_low_level_node_by_node_guid(node_guid, low_level_nodes):
        for low_level_node in low_level_nodes:
            if low_level_node['node_guid'] == node_guid:
                return low_level_node

        return None

    @staticmethod
    def _update_node_status(storage_node_objects, low_level_nodes):
        def __mount_logic(dev_path, _storage_node_object, _config, mount_helper):
            if not MountHelper.is_mount(dev_path):
                mount_helper.mount(dev_path, _storage_node_object.path)
            if MountHelper.is_mount(dev_path):
                _config['mount_path'] = _storage_node_object.path
                _config['is_mounting'] = True
                _storage_node_object.available = True
                _storage_node_object.config = json.dumps(_config, ensure_ascii=False)
            else:
                _config['mount_path'] = None
                _config['is_mounting'] = False
                _storage_node_object.config = json.dumps(_config, ensure_ascii=False)

        for storage_node_object in storage_node_objects:
            storage_node_object.available = False

            low_level_node = StorageNodeLogic._find_low_level_node_by_node_guid(
                storage_node_object.ident, low_level_nodes)

            config = json.loads(storage_node_object.config)
            if "nfs" in config.keys():
                nfs_path = config['nfs']
                __mount_logic(nfs_path, storage_node_object, config, MountNfsHelper())
            if "smb" in config.keys():
                smb_path = config['smb']['path']
                __mount_logic(smb_path, storage_node_object, config,
                              MountSmbHelper(config['smb']['username'], config['smb']['password']))
            elif "lvm" in config.keys():
                lvm_path = config['lvm']
                __mount_logic(lvm_path, storage_node_object, config, MountLvmHelper())
            elif low_level_node is None:
                # 设备未连接，修改 'logic_device_path' 'device_name' 'disk_size' 为 None
                config = json.loads(storage_node_object.config)
                config['logic_device_path'] = None
                config['disk_size'] = None
                config['device_name'] = None
                config['has_file_system'] = False
                config['file_system_name'] = None
                config['company_code'] = None
                config['logic_device_path'] = None
                config['node_guid'] = None
                config['is_mounting'] = False
                config['mount_path'] = None
                storage_node_object.config = json.dumps(config, ensure_ascii=False)
            else:
                if low_level_node['is_internal'] != storage_node_object.internal:
                    _logger.error(r'why node type invalid ?! ~ {} | {}'.format(storage_node_object.id, low_level_node))
                elif not low_level_node['is_mounting']:
                    _logger.error(r'is not mount : {}'.format(low_level_node))
                elif low_level_node['mount_path'] != storage_node_object.path:
                    _logger.error(r'mount_path invalid : {} | {}'.format(storage_node_object.path, low_level_node))
                else:
                    storage_node_object.available = True
                storage_node_object.config = json.dumps(low_level_node, ensure_ascii=False)

            storage_node_object.save(update_fields=['config', 'available'])

    @staticmethod
    def convert_is_internal_to_type(is_internal, config, ident):
        result = xdata.STORAGE_NODE_TYPE_INTERNAL if is_internal else xdata.STORAGE_NODE_TYPE_EXTERNAL
        config_obj = json.loads(config)
        if 'lvm' in config_obj:
            result = xdata.STORAGE_NODE_TYPE_VOLUME
        elif 'nfs' in config_obj:
            result = xdata.STORAGE_NODE_TYPE_VOLUME
        elif result == xdata.STORAGE_NODE_TYPE_INTERNAL:
            if ident in _cfg:
                result = xdata.STORAGE_NODE_TYPE_VOLUME
        return result

    @staticmethod
    def _is_low_level_node_linked(low_level_node):
        return low_level_node['has_file_system'] and low_level_node['is_mounting']

    @staticmethod
    def _force_initialize_internal_node(low_level_node, mount_path):
        low_level_node['mount_path'] = mount_path
        boxService.box_service.formatAndInitializeStorageNode(low_level_node)
        low_level_node['file_system_name'] = low_level_node['logic_device_path'] + '1'
        boxService.box_service.unmountStorageNode(low_level_node)
        boxService.box_service.mountStorageNode(low_level_node)

    @staticmethod
    def initialize_all_internal_nodes(part_uuid):
        low_level_nodes = boxService.box_service.enumStorageNodes()
        internal_and_not_linked_nodes = \
            [low_level_node for low_level_node in low_level_nodes if (low_level_node['is_internal'] and
                                                                      (part_uuid is None or low_level_node[
                                                                          'node_guid'] != part_uuid))]

        for internal_and_not_linked_node in internal_and_not_linked_nodes:
            StorageNodeLogic._force_initialize_internal_node(
                internal_and_not_linked_node, StorageNodeLogic.generate_mount_path())

        low_level_nodes = boxService.box_service.enumStorageNodes()
        return [low_level_node for low_level_node in low_level_nodes if
                (low_level_node['is_internal'] and StorageNodeLogic._is_low_level_node_linked(low_level_node))]

    @staticmethod
    def generate_mount_path():
        return '/mnt/nodes/' + uuid.uuid4().hex

    @staticmethod
    def delete_external_device_object(device_object, timeouts):
        last_available_datetime = datetime.now() - timedelta(seconds=timeouts)
        if device_object.last_available_datetime > last_available_datetime:
            return Response(status=status.HTTP_406_NOT_ACCEPTABLE)

        using = False

        nodes = StorageNode.objects.filter(internal=False, deleted=False).all()
        for node in nodes:
            config = json.loads(node.config)
            if (config['external_ip'] == device_object.ip) and (config['external_port'] == device_object.port):
                using = True
                break

        if using:
            device_object.update_last_available_datetime()
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

        device_object.set_deleted()
        boxService.box_service.logoutExternalDevice(device_object.last_iqn)

        return Response(status=status.HTTP_204_NO_CONTENT)

    @staticmethod
    def get_all_nodes(refresh_device=True, include_deleted=False):
        if refresh_device:
            if StorageNodeLogic.updating:
                with StorageNodeLogic.update_locker:
                    pass  # 等待后台线程更新完毕
            else:
                StorageNodeLogic.update_node_status()  # 当前线程更新状态

        if include_deleted:
            nodes = StorageNode.objects.all()
        else:
            nodes = StorageNode.objects.filter(deleted=False).all()
        storage_nodes = list()
        for node in nodes:
            node_item = {'name': node.name, 'linked': node.available, 'id': node.id, 'ident': node.ident,
                         'type': StorageNodeLogic.convert_is_internal_to_type(node.internal, node.config, node.ident),
                         'total_bytes': None, 'available_bytes': None, 'deleted': node.deleted}
            if (not node.deleted) and node.available:
                total, used, available = xsys.get_total_and_used_and_available_by_mount_point(node.path)
                node_item['total_bytes'] = total
                node_item['available_bytes'] = available

            storage_nodes.append(node_item)

        return storage_nodes


class StorageNodeDeleteWorker(threading.Thread):
    def __init__(self):
        super(StorageNodeDeleteWorker, self).__init__()

    def run(self):
        while True:
            time.sleep(60 * 10)
            try:
                self.run_worker()
            except Exception as e:
                _logger.error(r'StorageNodeDeleteWorker Exception : {}'.format(e), exc_info=True)

    @staticmethod
    @xlogging.db_ex_wrap
    def run_worker():
        device_objects = ExternalStorageDeviceConnection.objects.filter(deleted=False)
        for device_object in device_objects:
            StorageNodeLogic.delete_external_device_object(device_object, 3600)


class StorageNodeRelinkWorker(threading.Thread):
    def __init__(self):
        super(StorageNodeRelinkWorker, self).__init__()
        StorageNode.objects.filter(deleted=False).update(available=False)

    def run(self):
        time.sleep(10)

        while True:
            try:
                self.run_worker()
            except Exception as ex:
                _logger.error(r'StorageNodeRelinkWorker Exception : {}'.format(ex), exc_info=True)

            self.time_sleep()

    @xlogging.db_ex_wrap
    def time_sleep(self):
        if StorageNode.objects.filter(deleted=False, available=False).count() == 0:
            time.sleep(60 * 10)
        else:
            time.sleep(60 * 3)

    @staticmethod
    def _has_unlinked(node_objects):
        for node in node_objects:
            if not node.available:
                return True

        return False

    @staticmethod
    @xlogging.convert_exception_to_value(None)
    def _mount(file_system_name, mount_path):
        boxService.box_service.mountStorageNode({'file_system_name': file_system_name, 'mount_path': mount_path})

    @staticmethod
    @xlogging.convert_exception_to_value(None)
    def _unmount(file_system_name, mount_path):
        boxService.box_service.unmountStorageNode({'file_system_name': file_system_name, 'mount_path': mount_path})

    @staticmethod
    def _deal_mount(node_object, config):
        if not config['is_mounting'] and (config['mount_path'] is None or config['mount_path'] == ''):
            StorageNodeRelinkWorker._mount(config['file_system_name'], node_object.path)
        else:
            StorageNodeRelinkWorker._unmount(config['file_system_name'], config['mount_path'])
            StorageNodeRelinkWorker._mount(config['file_system_name'], node_object.path)

    @staticmethod
    def _find_same_link_node(ip, port, re_link_nodes):
        for node in re_link_nodes:
            if (node['ip'] == ip) and (node['port'] == port):
                return True
        return False

    @staticmethod
    def _find_other_available_external_node_same_link(ip, port, available_external_node_configs):
        for config in available_external_node_configs:
            if (config['external_ip'] == ip) and (config['external_port'] == port):
                return True
        return False

    @staticmethod
    def _get_device_object(ip, port):
        return ExternalStorageDeviceConnection.objects.filter(ip=ip, port=port).first()

    def _refresh_device(self, ip, port):
        device_object = self._get_device_object(ip, port)
        if device_object is None:
            _logger.warning(r'!! why can NOT find device object in _refresh_device : {} {}'.format(ip, port))
            return

        try:
            boxService.box_service.refreshExternalDevice(device_object.last_iqn)
            device_object.set_deleted(False)
        except xlogging.BoxDashboardException:
            _logger.warning(r'!! call refreshExternalDevice in _refresh_device failed')

    def _relogin_device(self, ip, port):
        device_object = self._get_device_object(ip, port)
        if device_object is None:
            _logger.warning(r'!! why can NOT find device object in _relogin_device : {} {}'.format(ip, port))
            return

        try:
            boxService.box_service.logoutExternalDevice(device_object.last_iqn)
        except xlogging.BoxDashboardException:
            _logger.warning(r'!! call logoutExternalDevice in _relogin_device failed')

        try:
            params = json.loads(device_object.params)
            iqn = boxService.box_service.loginExternalDevice(device_object.ip, device_object.port, params['use_chap'],
                                                             params['user_name'], params['password'])
            device_object.update_iqn_and_params(iqn, params)
        except xlogging.BoxDashboardException:
            _logger.warning(r'!! call loginExternalDevice in _relogin_device failed')

    @staticmethod
    @xlogging.convert_exception_to_value(False)
    def _is_point_valid(point_path):
        check_dir_path = boxService.box_service.pathJoin([point_path, 'check'])
        boxService.box_service.makeDirs(check_dir_path)
        boxService.box_service.remove(check_dir_path)
        return True

    @staticmethod
    @xlogging.convert_exception_to_value(None)
    def _umount_invalid_points():
        point_paths = [boxService.box_service.pathJoin([r'/mnt/nodes', name]) for name in
                       xsys.get_files_name_list(r'/mnt/nodes')]
        for point_path in point_paths:
            if not StorageNodeRelinkWorker._is_point_valid(point_path):
                StorageNodeRelinkWorker._unmount('', point_path)
                _logger.warning(r'umount invalid point: {}'.format(point_path))

    @xlogging.db_ex_wrap
    def run_worker(self):
        self._umount_invalid_points()

        StorageNodeLogic.update_node_status()

        node_objects = StorageNode.objects.filter(deleted=False).all()
        not_available_internal_nodes = [node for node in node_objects if ((not node.available) and node.internal)]
        for not_available_internal_node in not_available_internal_nodes:
            config = json.loads(not_available_internal_node.config)
            if config['logic_device_path'] is None:
                pass  # TODO : refresh internal disk
            else:
                self._deal_mount(not_available_internal_node, config)

        not_linked_external_nodes = list()  # 未连接上的设备

        not_available_external_nodes = [node for node in node_objects if (not node.available) and (not node.internal)]
        for not_available_external_node in not_available_external_nodes:
            config = json.loads(not_available_external_node.config)
            if config['logic_device_path'] is None:
                not_linked_external_nodes.append({'node': not_available_external_node, 'config': config})
            else:
                self._deal_mount(not_available_external_node, config)

        if len(not_linked_external_nodes) != 0:
            available_external_node_configs = [json.loads(node.config) for node in node_objects if
                                               node.available and (not node.internal)]

            re_link_nodes = list()
            for not_linked_external_node in not_linked_external_nodes:
                config = not_linked_external_node['config']
                if self._find_same_link_node(config['external_ip'], config['external_port'], re_link_nodes):
                    continue
                else:
                    re_link_nodes.append({'ip': config['external_ip'], 'port': config['external_port']})

                if self._find_other_available_external_node_same_link(config['external_ip'], config['external_port'],
                                                                      available_external_node_configs):
                    # 还有其他可用的节点在使用同一个设备，refresh
                    self._refresh_device(config['external_ip'], config['external_port'])
                else:
                    # 没有其他可用的节点在使用同一个设备，re-login
                    self._relogin_device(config['external_ip'], config['external_port'])

        StorageNodeLogic.update_node_status()
