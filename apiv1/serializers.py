from rest_framework import serializers

from box_dashboard import xdata, xlogging
from .models import (Host, HostMac, DiskSnapshot, Disk, RestoreTarget, HostSnapshot, BackupTaskSchedule, UserQuota,
                     HostSnapshotShare, HTBSchedule, ClusterBackupSchedule, TakeOverKVM, VirtualMachineSession,
                     VirtualCenterConnection, ImportSource, ArchiveSchedule, ImportSnapshotTask, DeployTemplate,
                     FileSyncSchedule)


class HostSerializer(serializers.ModelSerializer):
    class Meta:
        model = Host
        fields = ('id', 'name', 'ident', 'is_linked', 'network_transmission_type', 'aio_info', 'soft_ident', 'type',
                  'home_path')
        # id 数字 数据库id
        # name 字符串 主机名：当主机在线时内容是 display_name(last_ip)，当主机离线时内容为 display_name
        # ident 字符串 主机标识
        # is_linked 是否在线
        # network_transmission_type 数据传输方式 1加密 2不加密 3加密优先


class HostCreateSerializer(serializers.Serializer):
    macs = serializers.ListField(required=True,
                                 child=serializers.CharField(max_length=12, allow_null=False, allow_blank=False))
    user_ident = serializers.CharField(max_length=256, allow_null=False, allow_blank=False)
    host_name = serializers.CharField(max_length=256, allow_null=False, allow_blank=False)
    sysinfo = serializers.CharField(required=True)
    # macs 数组 网卡硬件地址
    # user_ident 字符串 用户标识（用户名）
    # host_name 字符串 系统的主机名


class HostInfoAlterSerializer(serializers.Serializer):
    display_name = serializers.CharField(max_length=256, required=False)
    user_id = serializers.IntegerField(required=False)
    network_transmission_type = serializers.IntegerField(required=False)
    orgname = serializers.CharField(max_length=256, required=False, allow_null=True)
    systemname = serializers.CharField(max_length=256, required=False, allow_null=True)
    # display_name 主机名称，无该字段表示不修改
    # user_id 主机从属用户，无该字段表示不修改
    # network_transmission_type 数据传输方式，无该字段表示不修改
    # orgname 单位名称，无该字段表示不修改
    # systemname 系统名称，无该字段表示不修改


class HostSessionDiskSerializer(serializers.Serializer):
    index = serializers.IntegerField(required=True)
    name = serializers.CharField(max_length=256, required=True)
    boot_able = serializers.BooleanField(default=False)
    bytes = serializers.IntegerField(required=True)
    used_bytes = serializers.IntegerField(required=True)
    partition_table_type = serializers.CharField(max_length=3, required=True)
    dynamic_disk = serializers.BooleanField(default=False)

    # index 磁盘序号
    # name 磁盘名称（磁盘型号）
    # boot_able 是否是启动磁盘
    # bytes 磁盘容量
    # used_bytes 磁盘已经使用的数据量
    # partition_table_type 磁盘分区表类型 (GPT MBR)

    # struct Disk {
    #   int         id;
    #   DiskDetail  detail;
    # };
    #
    # struct DiskDetail {
    #   string      name;                     //随机生成的字符串，不含'\0'最大32byte，大小写敏感
    #   DiskStatus  status;
    #   DiskType    type;
    #   long        numberOfSectors;          //磁盘扇区数大小
    #   bool        bootDevice;               //是否为启动设备
    #   string      lastSnapshot = "invalid"; //当status为LAST_SNAPSHOT_IS_NORMAL时候有效
    #   CDPSnapshot cdpSnapshot;              //当status为LAST_SNAPSHOT_IS_CDP时候有效
    # };
    @staticmethod
    def create_from_BoxLogic_Disk_list(disk_list):
        data = list()
        for disk in disk_list:
            detail = disk.detail
            detail.name = 'fake disk name'  # TODO
            disk_data = {"index": disk.id, "name": detail.name, "boot_able": detail.bootDevice,
                         "bytes": 512 * detail.numberOfSectors, "used_bytes": 0,
                         "partition_table_type": detail.type.name}
            data.append(disk_data)
        serializer = HostSessionDiskSerializer(data=data, many=True)
        if not serializer.is_valid():
            xlogging.raise_and_logging_error('内部异常，无效的磁盘信息', 'invalid disk info : {}'.format(serializer.errors))
        return serializer


class ClusterBackupTaskScheduleSerializer(serializers.ModelSerializer):
    hosts = HostSerializer(many=True)

    class Meta:
        model = ClusterBackupSchedule
        fields = ('id', 'enabled', 'deleted', 'name',
                  'cycle_type', 'cycle_type_display', 'created',
                  'plan_start_date', 'hosts', 'ext_config', 'last_run_date', 'next_run_date', 'storage_node_ident')
        # 1：1数据库映射


class BackupTaskScheduleSerializer(serializers.ModelSerializer):
    host = HostSerializer()

    class Meta:
        model = BackupTaskSchedule
        fields = ('id', 'enabled', 'deleted', 'name', 'backup_source_type_display', 'backup_source_type', 'cycle_type',
                  'cycle_type_display', 'created', 'plan_start_date', 'host', 'ext_config', 'last_run_date',
                  'next_run_date', 'storage_node_ident')
        # 1：1数据库映射


class ArchiveScheduleSerializer(serializers.ModelSerializer):
    host = HostSerializer()

    class Meta:
        model = ArchiveSchedule
        fields = ('id', 'enabled', 'deleted', 'name', 'cycle_type', 'cycle_type_display',
                  'created', 'plan_start_date', 'host', 'ext_config', 'last_run_date', 'next_run_date',
                  'storage_node_ident')
        # 1：1数据库映射


class ArchiveTaskScheduleCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ArchiveSchedule
        fields = ('enabled', 'name', 'cycle_type', 'plan_start_date',
                  'host', 'ext_config', 'storage_node_ident')
        # 1：1数据库映射


class BackupTaskScheduleCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = BackupTaskSchedule
        fields = ('enabled', 'name', 'backup_source_type', 'cycle_type', 'plan_start_date',
                  'host', 'ext_config', 'storage_node_ident')
        # 1：1数据库映射


class BackupTaskScheduleUpdateSerializer(serializers.Serializer):
    enabled = serializers.BooleanField(required=False)
    name = serializers.CharField(required=False, max_length=256)
    cycle_type = serializers.ChoiceField(required=False, choices=BackupTaskSchedule.CYCLE_CHOICES)
    plan_start_date = serializers.DateTimeField(required=False, allow_null=True)
    ext_config = serializers.CharField(required=False)
    storage_node_ident = serializers.CharField(required=False)


class ArchiveTaskScheduleUpdateSerializer(serializers.Serializer):
    enabled = serializers.BooleanField(required=False)
    name = serializers.CharField(required=False, max_length=256)
    cycle_type = serializers.ChoiceField(required=False, choices=BackupTaskSchedule.CYCLE_CHOICES)
    plan_start_date = serializers.DateTimeField(required=False, allow_null=True)
    ext_config = serializers.CharField(required=False)
    storage_node_ident = serializers.CharField(required=False)


class BackupTaskScheduleExcludeSerializer(serializers.Serializer):
    exclude_type = serializers.ChoiceField(choices=xdata.BACKUP_TASK_SCHEDULE_EXCLUDE_CHOICES)
    # 对于windows系统：
    #   排除volume时，如果该volume有盘符，则该值为盘符(例如 'C' 'D')；否则为 disk_native_guid:byte_offset:byte_length
    #   卷的设备名（例如 '\\\\?\\Volume{59e60f58-791e-11e6-b603-806e6f6e6963}'）表达方式暂时不用
    # 对于linux系统：
    #   排除volume时，如果该volume为lv，则该值为 卷设备名 （例如 /dev/mapper/vg-lv）；否则为 disk_native_guid:byte_offset:byte_length
    #   原生partition设备名（例如 /dev/sdc1）表达方式暂时不用
    #
    # 排除disk时，该值为 disk_native_guid
    exclude_info = serializers.CharField()
    lable = serializers.CharField()


class BackupTaskScheduleRetrySerializer(serializers.Serializer):
    enable = serializers.BooleanField()
    count = serializers.IntegerField()
    interval = serializers.IntegerField()


class BackupTaskScheduleExtConfigSerializer(serializers.Serializer):
    backupDataHoldDays = serializers.IntegerField(required=False, allow_null=True)
    backupLeastNumber = serializers.IntegerField(required=False, allow_null=True)
    autoCleanDataWhenlt = serializers.IntegerField(required=False, allow_null=True)
    cdpDataHoldDays = serializers.IntegerField(required=False, allow_null=True)
    maxBroadband = serializers.IntegerField(required=False, allow_null=True)
    cdpSynchAsynch = serializers.ChoiceField(required=False, choices=xdata.CDP_TYPE_CHOICES, allow_null=True)
    backupDayInterval = serializers.IntegerField(required=False, allow_null=True)
    daysInWeek = serializers.ListField(required=False, child=serializers.IntegerField(), allow_null=True)
    daysInMonth = serializers.ListField(required=False, child=serializers.IntegerField(), allow_null=True)
    specialMode = serializers.ChoiceField(required=False,
                                          choices=xdata.BACKUP_TASK_SCHEDULE_EXECUTE_SPECIAL_MODE_CHOICES)
    removeDuplicatesInSystemFolder = serializers.BooleanField(required=False)
    incMode = serializers.ChoiceField(required=False, choices=xdata.BACKUP_TASK_SCHEDULE_EXECUTE_TYPE_CHOICES)
    exclude = serializers.ListField(required=False, child=BackupTaskScheduleExcludeSerializer())
    IntervalUnit = serializers.CharField(required=False, allow_null=True)
    shellInfoStr = serializers.CharField(required=False, allow_null=True)
    data_keeps_deadline_unit = serializers.CharField(required=False, allow_null=True)
    diskreadthreadcount = serializers.IntegerField(required=False)
    backup_retry = BackupTaskScheduleRetrySerializer(required=False)
    BackupIOPercentage = serializers.IntegerField(required=False)
    nas_protocol = serializers.CharField(required=False, allow_null=True)
    nas_username = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    nas_password = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    nas_exclude_dir = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    nas_path = serializers.CharField(required=False, allow_null=True)
    enum_threads = serializers.IntegerField(required=False)
    sync_threads = serializers.IntegerField(required=False)
    cores = serializers.IntegerField(required=False)
    memory_mbytes = serializers.IntegerField(required=False)
    net_limit = serializers.IntegerField(required=False)
    enum_level = serializers.IntegerField(required=False)
    sync_queue_maxsize = serializers.IntegerField(required=False)
    nas_max_space_val = serializers.IntegerField(required=False)
    nas_max_space_unit = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    nas_max_space_actual = serializers.IntegerField(required=False)
    vmware_quiesce = serializers.IntegerField(required=False)
    vmware_tranport_modes = serializers.IntegerField(required=False)


class EtherAdapterSerializer(serializers.Serializer):
    ip_addresses = serializers.ListField(child=serializers.IPAddressField())
    mac = serializers.CharField()


class HostSessionSerializer(serializers.Serializer):
    host = HostSerializer()
    computer_name = serializers.CharField()
    os_type = serializers.CharField()
    os_version = serializers.CharField()
    ether_adapters = serializers.ListField(child=EtherAdapterSerializer())


class HostMoreInfoInputSerializer(serializers.Serializer):
    type = serializers.ChoiceField(required=True, allow_null=False,
                                   choices=xdata.QUERY_HOST_INFO_TYPE_CHOICES)


class BackupTaskScheduleExecuteSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=xdata.BACKUP_TASK_SCHEDULE_EXECUTE_TYPE_CHOICES)
    force_store_full = serializers.ChoiceField(choices=xdata.BACKUP_TASK_SCHEDULE_STORE_CHOICES,
                                               required=False,
                                               default=xdata.BACKUP_TASK_SCHEDULE_STORE_AS_DELTA)


class HostSessionBackupProgressSerializer(serializers.Serializer):
    progressIndex = serializers.IntegerField()
    progressTotal = serializers.IntegerField()
    code = serializers.ChoiceField(choices=xdata.BACKUP_PROGRESS_TYPE_CHOICES)


class AgentModuleErrorSerializer(serializers.Serializer):
    moduleName = serializers.CharField()
    description = serializers.CharField()
    debug = serializers.CharField()
    rawCode = serializers.IntegerField()


class DiskSerializer(serializers.ModelSerializer):
    class Meta:
        model = Disk
        fields = ('id', 'ident')


class HostSnapshotWithoutDiskSnapshotsSerializer(serializers.ModelSerializer):
    host = HostSerializer()

    class Meta:
        model = HostSnapshot
        fields = ('id', 'start_datetime', 'host')


class DiskSnapshotWithoutParentSnapshotSerializer(serializers.ModelSerializer):
    disk = DiskSerializer()
    host_snapshot = HostSnapshotWithoutDiskSnapshotsSerializer()

    class Meta:
        model = DiskSnapshot
        fields = ('id', 'disk', 'display_name', 'image_path', 'ident', 'host_snapshot', 'bytes', 'type',
                  'type_display', 'boot_device')


class DiskSnapshotSerializer(serializers.ModelSerializer):
    disk = DiskSerializer()
    host_snapshot = HostSnapshotWithoutDiskSnapshotsSerializer()
    parent_snapshot = DiskSnapshotWithoutParentSnapshotSerializer()

    class Meta:
        model = DiskSnapshot
        fields = ('id', 'disk', 'display_name', 'image_path', 'ident', 'host_snapshot', 'bytes', 'type',
                  'type_display', 'boot_device', 'parent_snapshot')


class HostSnapshotSerializer(serializers.ModelSerializer):
    host = HostSerializer()
    disk_snapshots = DiskSnapshotSerializer(many=True)

    class Meta:
        model = HostSnapshot
        fields = ('id', 'start_datetime', 'host', 'disk_snapshots')


class HostSessionPhysicalDisk(object):
    def __init__(self, index, disk_ident, last_snapshot_ident, disk_bytes, disk_type, boot_device, disk_status,
                 disk_cdpSnapshot_set_by_restore, disk_cdpSnapshot_token, disk_cdpSnapshot_seconds,
                 disk_cdpSnapshot_microseconds, is_system, is_bmf):
        self.index = index
        self.disk_ident = disk_ident
        self.last_snapshot_ident = last_snapshot_ident
        self.disk_bytes = disk_bytes
        self.disk_type = disk_type
        self.boot_device = boot_device
        self.disk_status = disk_status
        self.disk_cdpSnapshot_set_by_restore = disk_cdpSnapshot_set_by_restore
        self.disk_cdpSnapshot_token = disk_cdpSnapshot_token
        self.disk_cdpSnapshot_seconds = disk_cdpSnapshot_seconds
        self.disk_cdpSnapshot_microseconds = disk_cdpSnapshot_microseconds
        self.is_system = is_system
        self.is_bmf = is_bmf

    @staticmethod
    def create(disk):
        index = disk.id
        disk_ident = disk.detail.name
        last_snapshot_ident = disk.detail.lastSnapshot
        disk_bytes = disk.detail.numberOfSectors * 0x200
        boot_device = disk.detail.bootDevice
        is_system = disk.detail.systemDevice
        is_bmf = disk.detail.bmfDevice
        disk_cdpSnapshot_set_by_restore = disk.detail.cdpSnapshot.setByRestore
        disk_cdpSnapshot_token = disk.detail.cdpSnapshot.token
        disk_cdpSnapshot_seconds = disk.detail.cdpSnapshot.seconds
        disk_cdpSnapshot_microseconds = disk.detail.cdpSnapshot.microseconds
        disk_status = disk.detail.status.value
        disk_type = disk.detail.type.value

        return HostSessionPhysicalDisk(index=index, disk_ident=disk_ident, last_snapshot_ident=last_snapshot_ident,
                                       disk_bytes=disk_bytes, disk_type=disk_type, boot_device=boot_device,
                                       disk_cdpSnapshot_set_by_restore=disk_cdpSnapshot_set_by_restore,
                                       disk_status=disk_status, disk_cdpSnapshot_token=disk_cdpSnapshot_token,
                                       disk_cdpSnapshot_seconds=disk_cdpSnapshot_seconds,
                                       disk_cdpSnapshot_microseconds=disk_cdpSnapshot_microseconds,
                                       is_system=is_system, is_bmf=is_bmf)


class HostSessionPhysicalDiskSerializer(serializers.Serializer):
    index = serializers.IntegerField(required=True)
    disk_ident = serializers.CharField(max_length=32, required=False, allow_blank=True, default='')
    last_snapshot_ident = serializers.CharField(max_length=32, required=False, allow_blank=True, default='')
    disk_bytes = serializers.IntegerField(required=True)
    disk_type = serializers.ChoiceField(required=True, choices=DiskSnapshot.DISK_TYPE_CHOICES)
    boot_device = serializers.BooleanField(required=True)
    disk_status = serializers.IntegerField(required=True)
    disk_cdpSnapshot_set_by_restore = serializers.BooleanField(required=True)
    disk_cdpSnapshot_token = serializers.CharField(max_length=32, required=False, allow_blank=True, default='')
    disk_cdpSnapshot_seconds = serializers.IntegerField(required=True)
    disk_cdpSnapshot_microseconds = serializers.IntegerField(required=True)
    is_system = serializers.BooleanField(required=True)
    is_bmf = serializers.BooleanField(required=True)

    def create(self, validated_data):
        return HostSessionPhysicalDisk(**validated_data)


class HostLoginSerializer(serializers.Serializer):
    host_ident = serializers.CharField(max_length=32, required=True)
    host_ip = serializers.IPAddressField(required=True)
    local_ip = serializers.IPAddressField(required=True)
    tunnel_index = serializers.IntegerField(required=True)


class PeHostSerializer(serializers.ModelSerializer):
    class Meta:
        model = RestoreTarget
        fields = ('ident', 'display_name', 'start_datetime', 'finish_datetime',
                  'successful', 'total_bytes', 'restored_bytes', 'token_expires',
                  'keep_alive_interval_seconds', 'expiry_minutes')


class PeHostSessionDiskSerializer(serializers.Serializer):
    disk_id = serializers.IntegerField(required=True)
    disk_bytes = serializers.IntegerField(required=True)
    is_boot_device = serializers.BooleanField(required=True)


class PeHostSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = RestoreTarget
        fields = ('ident', 'display_name')


class PeHostSessionNetworkAdapterSerializer(serializers.Serializer):
    szDeviceInstanceID = serializers.CharField(required=True)
    szDescription = serializers.CharField(required=True)
    szGuid = serializers.CharField(required=True)
    szNetType = serializers.CharField(required=True)
    szMacAddress = serializers.CharField(required=True)
    isConnected = serializers.BooleanField(required=True)


class PeHostSessionHardwareSerializer(serializers.Serializer):
    szDeviceInstanceID = serializers.CharField(required=True)
    szDescription = serializers.CharField(required=True)
    szLocationInfo = serializers.CharField(required=True)
    szContainerID = serializers.CharField(required=True)
    szMacAddress = serializers.CharField(required=True)
    parentDevLevel = serializers.IntegerField(required=True)
    Address = serializers.IntegerField(required=True)
    UINumber = serializers.IntegerField(required=True)
    HWIds = serializers.ListField(required=True, child=serializers.CharField())
    CompatIds = serializers.ListField(required=True, child=serializers.CharField())
    szService = serializers.CharField(required=True)


class PeHostSessionDetailSerializer(serializers.Serializer):
    pe_host = PeHostSessionSerializer()
    disks = PeHostSessionDiskSerializer(many=True, required=True)
    network_adapters = PeHostSessionNetworkAdapterSerializer(many=True, required=True)
    disk_controller_hardware_stacks = serializers.ListField(required=True,
                                                            child=PeHostSessionHardwareSerializer(many=True))
    network_controller_hardware_stacks = serializers.ListField(required=True,
                                                               child=PeHostSessionHardwareSerializer(many=True))
    system_infos = serializers.JSONField()
    soft_ident = serializers.CharField(max_length=12)


class HostSnapshotRestoreDiskSerializer(serializers.Serializer):
    src = serializers.CharField(max_length=32, required=True)
    dest = serializers.IntegerField(required=True)


class HostSnapshotRestoreAdapterSettingSerializer(serializers.Serializer):
    adapter = serializers.CharField(required=True)
    ip = serializers.IPAddressField(required=True)
    subnet_mask = serializers.IPAddressField(required=True)
    routers = serializers.IPAddressField(allow_blank=True)
    dns = serializers.IPAddressField(allow_blank=True)
    multi_infos = serializers.JSONField(default='{}')


class HostSnapshotRestoreVolumeDiskRangeSerializer(serializers.Serializer):
    # 快照中的卷属于哪块磁盘（来自disk的ident）
    disk_ident = serializers.CharField(max_length=32, required=True)
    # 卷的扇区偏移
    sector_offset = serializers.CharField()
    # 卷的扇区数量
    sectors = serializers.CharField()
    # 目标卷所在的磁盘序号
    target_disk_number = serializers.CharField(default='', allow_blank=True)


class HostSnapshotRestoreVolumeSerializer(serializers.Serializer):
    # 磁盘分布
    ranges = HostSnapshotRestoreVolumeDiskRangeSerializer(many=True, required=True)
    # 快照中卷的显示名称
    display_name = serializers.CharField(default='', allow_blank=True)
    # 目标卷的设备名
    target_ident = serializers.CharField(default='', allow_blank=True)
    # 目标卷的显示名称
    target_display_name = serializers.CharField(default='', allow_blank=True)
    # 还原后卷的挂载点(Linux)
    mount_point_after_restore = serializers.CharField(default='', allow_blank=True)
    # 还原后挂载的文件系统类型（Linux）
    mount_fs_type_after_restore = serializers.CharField(default='', allow_blank=True)
    # 还原后挂载文件系统的参数（Linux）
    mount_fs_opts_after_restore = serializers.CharField(default='', allow_blank=True)


# 当整机还原时，pe_host_ident 一定为非None的有效值，
#           disks，restore_time（可选），adapters，host_ident（可选），drivers_ids，
#           agent_user_info，routers, exclude_volumes（可选） 有意义
# 当卷还原时，pe_host_ident 一定为None
#           host_ident，restore_time（可选），volumes 有意义
class HostSnapshotRestoreSerializer(serializers.Serializer):
    type = serializers.ChoiceField(required=True, choices=xdata.SNAPSHOT_TYPE_CHOICES)
    # 整机还原目标主机
    pe_host_ident = serializers.CharField(max_length=32, required=False, default=None, allow_null=True)
    disks = HostSnapshotRestoreDiskSerializer(many=True, required=False, default=None, allow_null=True)
    restore_time = serializers.DateTimeField(required=False, allow_null=True, default=None)
    adapters = serializers.ListField(required=False, child=HostSnapshotRestoreAdapterSettingSerializer(required=True),
                                     allow_null=True, default=None)
    # 为还原到Agent主机时传递
    host_ident = serializers.CharField(max_length=32, required=False, allow_null=True, default=None)
    drivers_ids = serializers.JSONField(required=False, allow_null=True, default=None)
    agent_user_info = serializers.CharField(required=False, allow_null=True, default=None)
    routers = serializers.JSONField(required=False, allow_null=True, default=None)
    disable_fast_boot = serializers.NullBooleanField(required=False, default=False)
    replace_efi = serializers.NullBooleanField(required=False, default=False)
    htb_task_uuid = serializers.CharField(required=False, allow_null=True, default=None)
    # 整机还原时排除的卷
    exclude_volumes = serializers.ListField(required=False, allow_null=True, default=None,
                                            child=HostSnapshotRestoreVolumeSerializer())
    # 卷还原时需要还原的卷
    volumes = serializers.ListField(required=False, allow_null=True, default=None,
                                    child=HostSnapshotRestoreVolumeSerializer())

    remote_kvm_params = serializers.JSONField(required=False, allow_null=True, default=None)


class HostSnapshotLocalRestoreSerializer(serializers.Serializer):
    type = serializers.ChoiceField(required=True, choices=xdata.SNAPSHOT_TYPE_CHOICES)
    host_ident = serializers.CharField(max_length=32, required=False, allow_null=True, default=None)
    restore_time = serializers.DateTimeField(required=False, allow_null=True, default=None)
    # 热备还原，直接传入了pe_ident
    pe_host_ident = serializers.CharField(max_length=32, required=False, allow_null=True, default=None)
    # 热备任务uuid
    htb_task_uuid = serializers.CharField(required=False, allow_null=True, default=None)
    # 热备计划id
    htb_schedule_id = serializers.CharField(required=False, allow_null=True, default=None)
    disk_params = HostSnapshotRestoreDiskSerializer(many=True, required=False, default=None, allow_null=True)


class HostSessionMigrateDiskSerializer(serializers.Serializer):
    src = serializers.IntegerField(required=True)
    dest = serializers.IntegerField(required=True)


class HostSessionMigrateSerializer(serializers.Serializer):
    pe_host_ident = serializers.CharField(max_length=32, required=True)
    # 为迁移到Agent主机时传递
    target_host_ident = serializers.CharField(max_length=32, required=False, allow_null=True, default=None)
    disks = HostSessionMigrateDiskSerializer(many=True, required=True)
    adapters = serializers.ListField(required=True, child=HostSnapshotRestoreAdapterSettingSerializer(required=True))
    drivers_ids = serializers.JSONField()
    agent_user_info = serializers.CharField()
    routers = serializers.JSONField()
    disable_fast_boot = serializers.NullBooleanField(required=False, default=False)
    replace_efi = serializers.NullBooleanField(required=False, default=False)
    remote_kvm_params = serializers.JSONField(required=False, allow_null=True, default=None)
    diskreadthreadcount = serializers.IntegerField(required=False)


class PeHostSessionLoginDiskSerializer(serializers.Serializer):
    diskID = serializers.IntegerField(required=True)
    diskSecCount = serializers.IntegerField(required=True)


class PeHostSessionLoginSerializer(serializers.Serializer):
    disks = PeHostSessionLoginDiskSerializer(many=True, required=True)
    remote_ip = serializers.IPAddressField(required=True)
    local_ip = serializers.IPAddressField(required=True)
    boot_disk_id = serializers.IntegerField(required=True)
    login_type = serializers.ChoiceField(required=True, choices=RestoreTarget.TYPE_CHOICES)
    tunnel_index = serializers.IntegerField(required=True)
    more_info = serializers.JSONField(required=False, allow_null=True, default=None)


class PeHostSessionRestoreDiskSerializer(serializers.Serializer):
    disk_index = serializers.IntegerField(required=True)
    disk_snapshot_ident = serializers.CharField(max_length=32, required=True)
    restore_timestamp = serializers.FloatField(required=False, allow_null=True, default=None)


class PeHostSessionRestoreSerializer(serializers.Serializer):
    host_snapshot_id = serializers.IntegerField(required=True)
    type = serializers.ChoiceField(required=True, choices=xdata.SNAPSHOT_TYPE_CHOICES)
    disks = PeHostSessionRestoreDiskSerializer(many=True, required=True)
    adapters = serializers.ListField(required=True, child=HostSnapshotRestoreAdapterSettingSerializer(required=True))
    drivers_ids = serializers.JSONField(required=True)
    agent_user_info = serializers.CharField(required=True)
    routers = serializers.JSONField(required=True)
    disable_fast_boot = serializers.NullBooleanField(required=False, default=False)
    replace_efi = serializers.NullBooleanField(required=False, default=False)
    htb_task_uuid = serializers.CharField(required=False, allow_null=True, default=None)
    exclude_volumes = serializers.ListField(required=False, allow_null=True, default=list(),
                                            child=HostSnapshotRestoreVolumeSerializer())
    # 界面传入的时间，用作日志的记录。可能不是真正的还原时间点
    restore_time = serializers.DateTimeField(required=False, allow_null=True, default=None)
    remote_kvm_params = serializers.JSONField(required=False, allow_null=True, default=None)


class AgentHostSessionRestoreVolumeSerializer(serializers.Serializer):
    sector_offset = serializers.CharField()
    sectors = serializers.CharField()
    device_name = serializers.CharField()
    display_name = serializers.CharField()
    target_display_name = serializers.CharField()
    mount_point_after_restore = serializers.CharField(default='', allow_blank=True)
    mount_fs_type_after_restore = serializers.CharField(default='', allow_blank=True)
    mount_fs_opts_after_restore = serializers.CharField(default='', allow_blank=True)


class AgentHostSessionRestoreDiskSerializer(serializers.Serializer):
    disk_index = serializers.IntegerField(required=True)
    disk_snapshot_ident = serializers.CharField(max_length=32, required=True)
    restore_timestamp = serializers.FloatField(required=False, allow_null=True, default=None)
    volumes = AgentHostSessionRestoreVolumeSerializer(many=True, required=True)


class AgentHostSessionRestoreSerializer(serializers.Serializer):
    host_snapshot_id = serializers.IntegerField(required=True)
    type = serializers.ChoiceField(required=True, choices=xdata.SNAPSHOT_TYPE_CHOICES)
    disks = AgentHostSessionRestoreDiskSerializer(many=True, required=True)
    htb_task_uuid = serializers.CharField(required=False, allow_null=True, default=None)
    # 界面传入的时间，用作日志的记录。可能不是真正的还原时间点
    restore_time = serializers.DateTimeField(required=False, allow_null=True, default=None)


class HostMacSerializer(serializers.ModelSerializer):
    class Meta:
        model = HostMac
        fields = ('id', 'mac', 'duplication')


class HostSessionBackupStatusSerializer(serializers.Serializer):
    display = serializers.CharField(read_only=True)
    progress_index = serializers.IntegerField(read_only=True)
    progress_total = serializers.IntegerField(read_only=True)


class StorageNodeSerializer(serializers.Serializer):
    id = serializers.IntegerField(required=True)
    name = serializers.CharField(required=True)
    linked = serializers.BooleanField(required=True)
    type = serializers.ChoiceField(required=True, choices=xdata.STORAGE_NODE_TYPE_CHOICES)
    total_bytes = serializers.IntegerField(required=False, allow_null=True)
    available_bytes = serializers.IntegerField(required=False, allow_null=True)
    ident = serializers.CharField(required=False, allow_null=True)
    deleted = serializers.BooleanField(required=True)


class StorageNodePerDeviceSerializer(serializers.Serializer):
    device_name = serializers.CharField(required=True)
    device_size = serializers.CharField(required=True)
    logic_device_path = serializers.CharField(required=True)
    status = serializers.ChoiceField(required=True, choices=xdata.STORAGE_NODE_STATUS_CHOICES)
    old_node_id = serializers.IntegerField(required=True, allow_null=True)
    lun_name = serializers.CharField(required=False, allow_blank=True)


class AddStorageNodeSerializer(serializers.Serializer):
    logic_device_path = serializers.CharField(required=True)
    status = serializers.ChoiceField(required=True, choices=xdata.STORAGE_NODE_STATUS_CHOICES)
    force_format = serializers.BooleanField(default=False)
    name = serializers.CharField(required=True)
    old_node_id = serializers.IntegerField(required=True, allow_null=True)


class AddExternalStorageDeviceSerializer(serializers.Serializer):
    ip = serializers.IPAddressField(required=True)
    port = serializers.IntegerField(required=True)
    use_chap = serializers.BooleanField(required=True)
    user_name = serializers.CharField(default=None, allow_null=True, allow_blank=True)
    password = serializers.CharField(default=None, allow_null=True, allow_blank=True)
    force = serializers.BooleanField(default=False)


class ArchiveTaskScheduleSerializer(serializers.ModelSerializer):
    host = HostSerializer()

    class Meta:
        model = ArchiveSchedule
        fields = ('id', 'enabled', 'deleted', 'name', 'cycle_type', 'cycle_type_display',
                  'created', 'plan_start_date', 'host', 'ext_config', 'last_run_date', 'next_run_date',
                  'storage_node_ident')
        # 1：1数据库映射


class AlterStorageNodeInfo(serializers.Serializer):
    name = serializers.CharField(required=True, allow_blank=False, allow_null=False, max_length=256)


class DealStorageNode(serializers.Serializer):
    DEAL_STORAGE_NODE_TYPE_RM_ALL = 'rm_images'
    type = serializers.CharField(required=True, allow_blank=False, allow_null=False)
    admin_pwd = serializers.CharField(allow_blank=False, allow_null=False)


class HostSnapshotShareSerializer(serializers.ModelSerializer):
    class Meta:
        model = HostSnapshotShare
        fields = ('id', 'login_user', 'samba_user', 'samba_pwd', 'samba_url', 'share_status', 'share_start_time',
                  'host_display_name', 'host_snapshot_type', 'host_start_time', 'host_finish_time', 'host_snapshot_id',
                  'dirinfo')


class PeHostSessionVolumeRestoreSerializer(serializers.Serializer):
    code = serializers.ChoiceField(required=True, choices=xdata.VOLUME_RESTORE_STATUS_CHOICES)
    msg = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    debug = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class HTBScheduleSerializer(serializers.ModelSerializer):
    class Meta:
        model = HTBSchedule
        fields = '__all__'


class TakeOverKVMSerializer(serializers.ModelSerializer):
    class Meta:
        model = TakeOverKVM
        fields = '__all__'


class VirtualMachineSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = VirtualMachineSession
        fields = '__all__'


class VirtualCenterConnectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = VirtualCenterConnection
        fields = '__all__'


class VirtualHostRestoreSerializer(serializers.Serializer):
    host_snapshot = serializers.IntegerField()
    ext_config = serializers.JSONField(required=False, allow_null=True, default=None)


class MqHostBaseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Host
        fields = (
            'display_name',
            'ident',
            'is_linked',
            'soft_ident',
            'type',
            'login_datetime',
            'is_deleted',
        )


class MqHostIpAddressSerializer(serializers.Serializer):
    ip = serializers.IPAddressField()
    type = serializers.ChoiceField(choices=(('other', 'other'), ('nat_out', 'nat外部'),
                                            ('nat_in', 'nat内部'), ('direct', '直连')))


class MqHostSerializer(serializers.Serializer):
    base = MqHostBaseSerializer()
    user_account = serializers.CharField(allow_blank=True)
    ip_address = MqHostIpAddressSerializer(many=True)
    os_type = serializers.ChoiceField(choices=(('windows', 'windows'), ('linux', 'linux'),))
    sys_info = serializers.JSONField(required=False, default={})
    disk_info = serializers.JSONField(required=False, default={})


class MqBackupTaskScheduleSerializer(serializers.ModelSerializer):
    class Meta:
        model = BackupTaskSchedule
        fields = (
            'id',
            'enabled',
            'deleted',
            'backup_source_type',
            'cycle_type',
            'created',
            'plan_start_date',
            'host_ident',
            'last_run_date',
            'next_run_date',
            'storage_node_ident',
            'abstract_name',
        )


class MqHostSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = HostSnapshot
        fields = (
            'id',
            'host_ident',
            'deleted',
            'deleting',
            'is_cdp',
            'status',
            'backup_datetime',
            'first_datetime',
            'last_datetime',
        )


class MqUserQuotaSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserQuota
        fields = (
            'id',
            'deleted',
            'storage_node_ident',
            'user_account',
            'quota_mega_bytes',
            'caution_mega_bytes',
            'available_mega_bytes',
        )


class MqTaskChangeCollectSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    start_datetime = serializers.DateTimeField()
    finish_datetime = serializers.DateTimeField(allow_null=True, default=None)
    user_account = serializers.CharField(allow_blank=True)
    successful = serializers.BooleanField(default=False)


class MqTaskConfirmSerializer(serializers.Serializer):
    snapshot_id = serializers.IntegerField(allow_null=True, default=None)
    backup_datetime = serializers.DateTimeField(allow_null=True, default=None)
    host_ident = serializers.CharField(max_length=128, allow_blank=True, default=None, allow_null=True)


class MqRestoreTaskSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    start_datetime = serializers.DateTimeField()
    finish_datetime = serializers.DateTimeField(allow_null=True, default=None)
    user_account = serializers.CharField(allow_blank=True)
    successful = serializers.BooleanField(default=False)
    snapshot_id = serializers.IntegerField()
    backup_datetime = serializers.DateTimeField(allow_null=True, default=None)
    host_ident = serializers.CharField(max_length=128)


class ArchiveScheduleExecuteSerializer(serializers.Serializer):
    schedule = serializers.IntegerField(allow_null=True)
    force_full = serializers.BooleanField(default=False)


class ImportTaskSerializer(serializers.Serializer):
    # 导入类型，按任务导入
    src_type = serializers.ChoiceField(required=True, choices=ImportSource.SRC_TYPE)
    # 任务uuid 此值可以不填
    local_task_uuid = serializers.CharField(allow_blank=True, allow_null=True, default=None)
    # 新创建Host的user
    user_id = serializers.CharField(required=True, allow_null=False)
    # 导入目标位置
    storage_path = serializers.CharField(required=True, allow_null=False)


class ImportTaskListSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImportSnapshotTask
        fields = '__all__'


class TaskProgressReportSerializer(serializers.Serializer):
    task_type = serializers.ChoiceField(choices=xdata.TASK_PROGRESS_TYPE_CHOICES)
    task_uuid = serializers.CharField(max_length=128)
    payload = serializers.JSONField()


class DeployTemplateCURDSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeployTemplate
        fields = '__all__'


class FileSyncScheduleSerializer(serializers.ModelSerializer):
    host = HostSerializer()

    class Meta:
        model = FileSyncSchedule
        fields = ('id', 'enabled', 'deleted', 'name', 'host',
                  'cycle_type', 'cycle_type_display', 'created',
                  'plan_start_date', 'ext_config', 'last_run_date', 'next_run_date',
                  'target_host_ident')
