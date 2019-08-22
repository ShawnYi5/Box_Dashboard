import json
import os

from box_dashboard.settings import BASE_DIR

BASE_STATIC_PATH = os.path.join(BASE_DIR, 'xdashboard', 'static')

SNAPSHOT_TYPE_NORMAL = 'normal'
SNAPSHOT_TYPE_CDP = 'cdp'

SNAPSHOT_TYPE_CHOICES = (
    (SNAPSHOT_TYPE_NORMAL, '整机快照备份'),
    (SNAPSHOT_TYPE_CDP, '整机CDP备份'),
)

CDP_MODE_SYN = 0  # 同步CDP
CDP_MODE_ASYN = 1  # 异步CDP

CDP_TYPE_CHOICES = (
    (CDP_MODE_SYN, '同步CDP'),
    (CDP_MODE_ASYN, '异步CDP'),
)

BACKUP_PROGRESS_TYPE_CHOICES_POST_DATA = 5

BACKUP_TASK_SCHEDULE_EXECUTE_TYPE_FORCE_FULL = 1
BACKUP_TASK_SCHEDULE_EXECUTE_TYPE_AUTO = 2

BACKUP_TASK_SCHEDULE_EXECUTE_TYPE_CHOICES = (
    (BACKUP_TASK_SCHEDULE_EXECUTE_TYPE_FORCE_FULL, '完全备份'),
    (BACKUP_TASK_SCHEDULE_EXECUTE_TYPE_AUTO, '智能增量'),
)

BACKUP_TASK_SCHEDULE_STORE_AS_DELTA = 0
BACKUP_TASK_SCHEDULE_STORE_AS_FULL = 1

BACKUP_TASK_SCHEDULE_STORE_CHOICES = (
    (BACKUP_TASK_SCHEDULE_STORE_AS_DELTA, '智能增量存储'),
    (BACKUP_TASK_SCHEDULE_STORE_AS_FULL, '完整存储'),
)

BACKUP_TASK_SCHEDULE_EXECUTE_SPECIAL_MODE_NONE = 0
BACKUP_TASK_SCHEDULE_EXECUTE_SPECIAL_MODE_ONLY_SYSTEM_VOLUME = 1

BACKUP_TASK_SCHEDULE_EXECUTE_SPECIAL_MODE_CHOICES = (
    (BACKUP_TASK_SCHEDULE_EXECUTE_SPECIAL_MODE_NONE, '无特殊模式'),
    (BACKUP_TASK_SCHEDULE_EXECUTE_SPECIAL_MODE_ONLY_SYSTEM_VOLUME, '仅备份系统卷(废弃)'),
)

BACKUP_TASK_SCHEDULE_EXCLUDE_DISK = 1
BACKUP_TASK_SCHEDULE_EXCLUDE_VOLUME = 2

BACKUP_TASK_SCHEDULE_EXCLUDE_CHOICES = (
    (BACKUP_TASK_SCHEDULE_EXCLUDE_DISK, '排除磁盘'),
    (BACKUP_TASK_SCHEDULE_EXCLUDE_VOLUME, '排除卷'),
)

BACKUP_PROGRESS_TYPE_CHOICES = (
    (0, json.dumps(['未知的备份状态', ''])),
    (1, json.dumps(['扫描文件', ''])),
    (2, json.dumps(['分析重复文件', ''])),
    (3, json.dumps(['创建备份位图', 'TASK_STEP_IN_PROGRESS_BACKUP_CREATE_BITMAP'])),
    (4, json.dumps(['创建快照', 'TASK_STEP_IN_PROGRESS_BACKUP_CREATE_SNAPSHOT'])),
    (BACKUP_PROGRESS_TYPE_CHOICES_POST_DATA, json.dumps(['传输备份数据', 'TASK_STEP_IN_PROGRESS_BACKUP_TRANSFER_DATA'])),
)

BACKUP_PROGRESS_TYPE_CHOICES_DICT = dict(BACKUP_PROGRESS_TYPE_CHOICES)

STORAGE_NODE_TYPE_INTERNAL = 1
STORAGE_NODE_TYPE_EXTERNAL = 2
STORAGE_NODE_TYPE_VOLUME = 3

STORAGE_NODE_TYPE_CHOICES = (
    (STORAGE_NODE_TYPE_INTERNAL, 'Local LUN'),
    (STORAGE_NODE_TYPE_EXTERNAL, 'LUN'),
    (STORAGE_NODE_TYPE_VOLUME, 'Internal LUN'),
)

STORAGE_NODE_STATUS_USING = 0
STORAGE_NODE_STATUS_NOT_INIT = 1
STORAGE_NODE_STATUS_INIT_BY_SELF = 2
STORAGE_NODE_STATUS_INIT_BY_OTHER = 3
STORAGE_NODE_STATUS_SAME_WITH_USING = 4

STORAGE_NODE_STATUS_CHOICES = (
    (STORAGE_NODE_STATUS_USING, '正在使用中'),
    (STORAGE_NODE_STATUS_NOT_INIT, '未被初始化'),
    (STORAGE_NODE_STATUS_INIT_BY_SELF, '已经被本设备初始化'),
    (STORAGE_NODE_STATUS_INIT_BY_OTHER, '已经被其他设备初始化'),
    (STORAGE_NODE_STATUS_SAME_WITH_USING, '与正在使用中的节点标识相同'),
)

VOLUME_RESTORE_STATUS_FINISH_FAILED = 0
VOLUME_RESTORE_STATUS_FINISH_OK = 1
VOLUME_RESTORE_STATUS_UMOUNT_All_VOLUMES = 2
VOLUME_RESTORE_STATUS_STARTED = 3

VOLUME_RESTORE_STATUS_CHOICES = (
    (VOLUME_RESTORE_STATUS_FINISH_FAILED, '卷还原失败'),
    (VOLUME_RESTORE_STATUS_FINISH_OK, '分析热数据区域'),
    (VOLUME_RESTORE_STATUS_UMOUNT_All_VOLUMES, '卸载卷设备完成'),
    (VOLUME_RESTORE_STATUS_STARTED, '获取还原区域完成'),
)

HOST_BACKUP_FORBID_SIZE_MB = 100

UPDATE_HOST_SYS_INFO_TIME_SEC = 60 * 20

USER_QUOTA_IS_NOT_WARING_VALUE = -1
USER_QUOTA_IS_NOT_LIMIT_VALUE = -1

WIN_PE_NEW_PATH = os.path.join(BASE_DIR, 'xdashboard', 'static', 'download', 'newwinpe')
WIN_PE_MATHER_PATH = os.path.join('/var', 'lib', 'tftpboot')

NET_DISK_IO_SAMPLE_INTERVAL_SEC = 30
NET_DISK_IO_SAMPLE_CYC_TIMES_SEC = 3600
NET_DISK_IO_SAMPLE_TRUNCATE_DAYS = 30

CDP_FILE_NO_CONTENT_ERR = 0x1a
HOST_IN_CDPING_STATUS = -1072758780

STORAGE_GRAPH_BASIC_POINT_NUM = 200
STORAGE_GRAPH_TRUNCATE_DAYS = int(365 / 2)

RECORD_NODES_SPACE_SEC = 60 * 5

HOST_SNAPSHOTSHARE_TIMEOUT = 3600 * 12

ACCESS_NTP_SERVER_TIMEOUT_SECS = 60
AGENT_LOG_PATH = os.path.join(BASE_DIR, 'xdashboard', 'static', 'agentlog', 'agent.7z')
ALL_DEBUG_MESSAGE_PATH = os.path.join(BASE_DIR, 'xdashboard', 'static', 'aiolog', 'aiolog.tar.bz2')
AIO_LOG_IDENT = r'get_aio_log_file_zip'
LOG_ZIP_EXPIRE_HOURS = 24

CORE_DUMPS_PATH = os.path.join('/tmp', 'coredumps')
AIO_LOG_PATH = os.path.join('/var', 'log')
AIO_VERSION_PATH = os.path.join('/sbin', 'aio', 'version.inc')

LOGKEY = '2e0234753447434cb8501125248d6576'

SNAPSHOT_FILE_CHAIN_MAX_LENGTH = 12000

PREFIX_ISO_FILE = "BootResourece"
PREFIX_TCP_DUMP_FILE = "ClwDRTcpDump"
PREFIX_SAFE_REPORT_FILE = "ClwDRSafeReport"
PREFIX_SAFE_REPORT_SUMMARY_FILE = "ClwDRSummaryReport"
PREFIX_SAFE_REPORT_USER_QUOTAS_FILE = "ClwDRUserQuotasReport"
PREFIX_SAFE_REPORT_STORAGE_STATUS_FILE = "ClwDRStorageStatusReport"
PREFIX_SAFE_REPORT_STORAGE_CHART_FILE = "ClwDRStorageIncreaseReport"
PREFIX_SAFE_REPORT_BAND_WIDTH_CHART_FILE = "ClwDRBandWidthReport"
PREFIX_SAFE_REPORT_DISK_CHART_FILE = "ClwDRDiskIoChartReport"
PREFIX_LOG_OPERATION_FILE = "ClwDROperationLog"
PREFIX_LOG_CLIENT_FILE = "ClwDRClientLog"
PREFIX_LOG_UPDATE_FILE = "ClwDRUpdateLog"
PREFIX_LOG_EMAIL_FILE = "ClwDREmailLog"
PREFIX_WORDS_FILE = "ClwWords"
PREFIX_LOG_WEBGUARD = "ClwDRWebguardLog"
PREFIX_DR_CLIENT = "Client"

UUID_VALIADE_HOST = r'31b7dea05b654756aec5613678a83916'
CANCEL_TASK_EXT_KEY = r'cancel_work'
START_KVM_FLAG_FILE = r'start_kvm_flag_file'
RESTORE_IS_COMPLETE = r'restore_is_complete'

TIME_UNIT_SECOND = 0
TIME_UNIT_MINUTE = 1
TIME_UNIT_HOUR = 2
TIME_UNIT_DAY = 3

TIME_UNIT_CHOICES = (
    (TIME_UNIT_SECOND, '秒'),
    (TIME_UNIT_MINUTE, '分'),
    (TIME_UNIT_HOUR, '小时'),
    (TIME_UNIT_DAY, '天'),
)

DEFAULT_STRATEGY_GROUP_NAME = r'默认分组'
CHECK_TYPE_HOME_PAGE = 1
CHECK_TYPE_URLS = 100
CHECK_TYPE_FILES = 200
STRATEGY_TYPE = {
    'web-site': CHECK_TYPE_URLS,
    'web-home': CHECK_TYPE_HOME_PAGE,
    'web-file': CHECK_TYPE_FILES
}

STRATEGY_EVENT_STATUS = {
    'normal': '正常',
    'high': '高风险',
    'middle': '中风险',
    'low': '低风险',
    'other': '其它',
}

_alarm_level_2_int = {'normal': 1, 'other': 2, 'low': 3, 'middle': 4, 'high': 5}


def convert_alarm_level_2_int(level):
    try:
        return _alarm_level_2_int[level]
    except Exception as e:
        raise Exception(r'convert_alarm_level_2_int ({}) failed. {}'.format(level, e))


def max_alarm_level(a, b):
    ia = convert_alarm_level_2_int(a)
    ib = convert_alarm_level_2_int(b)
    return a if ia > ib else b


MAINTAIN_STATUS_UNKNOWN = 0
MAINTAIN_STATUS_NORMAL = 1
MAINTAIN_STATUS_TAKEOVER = 2
MAINTAIN_STATUS_TYPE = (
    (MAINTAIN_STATUS_UNKNOWN, '--'),
    (MAINTAIN_STATUS_NORMAL, '正常模式'),
    (MAINTAIN_STATUS_TAKEOVER, '已切换为应急页面'),
)

STP_CDP = 1
NOT_STP_CDP = 0
NEED_STOP_CDP = (
    (NOT_STP_CDP, '不需要停止CDP'),
    (STP_CDP, '需要停止CDP'),
)

CREATE_MODIFY_TASK_DEFAULT_SECS = 3600 * 0.5


def standardize_mac_addr(mac):
    return mac.replace(' ', '').replace('-', '').replace(':', '').upper()


def is_two_mac_addr_equal(mac1, mac2):
    return standardize_mac_addr(mac1) == standardize_mac_addr(mac2)


HTB_BIN_PATH = r'/dev/shm/htb_disk_{}.bin'
HTB_DISK_FILES_DIR = r'/tmp/htb_kvm_file_{}'

HTB_SWITCH_IP_STEP_PUSH = 0
HTB_SWITCH_IP_STEP_MIGRATE = 1
HTB_SWITCH_IP_STEP = (
    (HTB_SWITCH_IP_STEP_PUSH, '推送数据阶段'),
    (HTB_SWITCH_IP_STEP_MIGRATE, '迁移IP阶段')
)

HTB_IP_TYPE_CONTROL = 1
HTB_IP_TYPE_BUSINESS = 0
HTB_IP_TYPE = (
    (HTB_IP_TYPE_CONTROL, '控制用IP'),
    (HTB_IP_TYPE_BUSINESS, '业务用IP')
)

HTB_TASK_TYPE_HISTORY = 0
HTB_TASK_TYPE_LATEST = 1
HTB_TASK_TYPE = (
    (HTB_TASK_TYPE_HISTORY, '将备机切换到历史数据点'),
    (HTB_TASK_TYPE_LATEST, '停止主机业务，并将备机切换到最新的数据')
)

REMOTE_BACKUP_ERROR = -1
REMOTE_BACKUP_NONE_WORK = 0
REMOTE_BACKUP_CDP_WORK = 1
REMOTE_BACKUP_QEMU_WORK = 2
REMOTE_BACKUP_STATUS = (
    (REMOTE_BACKUP_ERROR, '未知错误'),
    (REMOTE_BACKUP_NONE_WORK, '没有同步工作'),
    (REMOTE_BACKUP_CDP_WORK, '同步CDP'),
    (REMOTE_BACKUP_QEMU_WORK, '同步普通备份点'),
)

FILE_BROWSER_LINK_PREFIX = '/var/www/static/filebrowser/'

BACKUP_TYPE_AGENT_FULL_STORE_FULL_WITHOUT_OPTIMIZE = 11
BACKUP_TYPE_AGENT_FULL_STORE_FULL_WITH_OPTIMIZE = 12
BACKUP_TYPE_AGENT_FULL_STORE_INCREMENT = 20
BACKUP_TYPE_AGENT_INCREMENT_STORE_INCREMENT_WITHOUT_OPTIMIZE = 31
BACKUP_TYPE_AGENT_INCREMENT_STORE_INCREMENT_WITH_OPTIMIZE = 32
BACKUP_TYPE = (
    (BACKUP_TYPE_AGENT_FULL_STORE_FULL_WITHOUT_OPTIMIZE, '源主机执行完整备份，快照文件进行完整存储'),
    (BACKUP_TYPE_AGENT_FULL_STORE_FULL_WITH_OPTIMIZE, '源主机执行完整备份，快照文件进行完整存储，并进行网络优化'),
    (BACKUP_TYPE_AGENT_FULL_STORE_INCREMENT, '源主机执行完整备份，快照文件进行增量存储，并进行网络优化'),
    (BACKUP_TYPE_AGENT_INCREMENT_STORE_INCREMENT_WITHOUT_OPTIMIZE, '源主机执行增量备份，快照文件进行增量存储'),
    (BACKUP_TYPE_AGENT_INCREMENT_STORE_INCREMENT_WITH_OPTIMIZE, '源主机执行增量备份，快照文件进行增量存储，并进行网络优化')
)

DEDUPLE_TYPE_CLIENT_WORK = 0x01  # 客户端做hash去重功能
DEDUPLE_TYPE_COPY_ALL_DATA_WRITE_2_NEW = 0x02  # 从NBD设备读取相同的数据，保证新备份的数据跟以前的存储空间切割
DEDUPLE_TYPE_HASH_VERIFY_BEFORE_WRITE = 0x04  # 写之前在AIO中强制做hash校验，如果nbd数据不同，则重传


def get_type_name(code_name_map, code):
    for entry in code_name_map:
        if entry[0] == code:
            return entry[1]
    return ''


DISABLE_DEDUP_FLAG_FILE = r'/dev/shm/disable_dedup'  # 流量及空间优化 开关文件
DISABLE_HASH_DISK_DATA_FLAG_FILE = r'/dev/shm/disable_hash_disk_data'  # 磁盘产生hash数据开关
VMCLIENT_FAKE_MAC = 'CLW9385b913a'  # VMCLIENT 假MAC地址
DICT_TYPE_TASK_QUEUE_NUM_DEFAULT = '32'
ENABLE_RESTORE_OPTIMIZE = r'/dev/shm/enable_restore_optimize'  # 启用还原优化
SAVE_SRC_HASH = r'/dev/shm/save_src_hash'  # 保留原始的hash文件
CLW_BOOT_REDIRECT_MBR_UUID = 'clwbootdisk'.ljust(32, '0')
CLW_BOOT_REDIRECT_GPT_UUID = 'clwbootdisk'.ljust(31, '0') + '1'
CLW_BOOT_REDIRECT_GPT_LINUX_UUID = 'clwbootdisk'.ljust(31, '0') + '2'

QUERY_HOST_INFO_BACKUP = 'Backup_param'
QUERY_HOST_INFO_TYPE_CHOICES = (
    (QUERY_HOST_INFO_BACKUP, '备份策略参数'),
)

MIGRATE_BACKUP_X86_PARAMS = {
    'pending_setting': [
        {"buffer_percent": 80, "pending_microseconds": 200},
        {"buffer_percent": 95, "pending_microseconds": 500}
    ],
    'kernel_cache_megabytes': 128,
    'failed_when_buffer_full': True,
}

MIGRATE_BACKUP_X64_PARAMS = {
    'pending_setting': [
        {"buffer_percent": 80, "pending_microseconds": 200},
        {"buffer_percent": 95, "pending_microseconds": 500}
    ],
    'kernel_cache_megabytes': 256,
    'failed_when_buffer_full': True,
}


def get_path_in_ram_fs(*p):
    return os.path.join(r'/run', *p)


TASK_PROGRESS_EXPORT_SNAPSHOT = 'export_snapshot'
TASK_PROGRESS_IMPORT_SNAPSHOT = 'import_snapshot'
TASK_PROGRESS_NAS_FILE_BACKUP = 'nas_file_backup'
TASK_PROGRESS_FILE_SYNC = 'file_sync'
TASK_PROGRESS_TYPE_CHOICES = (
    (TASK_PROGRESS_EXPORT_SNAPSHOT, '备份数据导出'),
    (TASK_PROGRESS_IMPORT_SNAPSHOT, '备份数据导入'),
    (TASK_PROGRESS_NAS_FILE_BACKUP, 'NAS文件备份'),
    (TASK_PROGRESS_FILE_SYNC, '文件归档'),
)

TMP_BITMAP_DIR = '/tmp/tmp_bitmap/'
os.makedirs(TMP_BITMAP_DIR, exist_ok=True)
