import copy
import json
import threading
import time

from rest_framework import status

from box_dashboard import xlogging, boxService
from .compress import CompressTaskThreading
from .models import DiskSnapshot, HostSnapshotShare
from .snapshot import GetSnapshotList, DiskSnapshotLocker

_logger = xlogging.getLogger(__name__)

ice_cmd_ctrl_header = {'cmdtype': '', 'contype': '', 'cmdinfo': None}


def iceCmdInit(cmdtype, contype, cmdinfo):
    global ice_cmd_ctrl_header
    mdict = copy.copy(ice_cmd_ctrl_header)
    mdict['cmdtype'] = cmdtype
    mdict['contype'] = contype
    mdict['cmdinfo'] = cmdinfo
    return mdict


def iceGetCmd(cmdtype, contype, cmdinfo):
    cmdlist = list()
    cmdinfo = iceCmdInit(cmdtype, contype, cmdinfo)
    cmdlist.append(cmdinfo)
    return json.dumps(cmdlist)


def LockDiskFilesOper(cmd, locked_files):
    mlocklist = locked_files.split('::')
    for i in range(len(mlocklist)):
        mmlocklist = mlocklist[i].split(';')
        if len(mmlocklist) != 3:
            continue
        if cmd == 1:
            _logger.debug('lock file {} {} {}'.format(mmlocklist[0], mmlocklist[1], mmlocklist[2]))
            DiskSnapshotLocker.lock_file(mmlocklist[0], mmlocklist[1], mmlocklist[2])
            CompressTaskThreading().update_task_by_disk_snapshot(mmlocklist[0], mmlocklist[1])
        elif cmd == 2:
            _logger.debug('unlock file {} {} {}'.format(mmlocklist[0], mmlocklist[1], mmlocklist[2]))
            DiskSnapshotLocker.unlock_file(mmlocklist[0], mmlocklist[1], mmlocklist[2])


GET_STATUS_TIME = 30
MonitorDict = dict()
MonitorDictLock = threading.Lock()


def addHostShareMonitor(share_info, share_status):
    global MonitorDict
    global MonitorDictLock
    with MonitorDictLock:
        if share_info not in MonitorDict:
            MonitorDict[share_info] = share_status
            _logger.debug('add share monitor,share_info {},status {} ,have {} share'.format(share_info, share_status,
                                                                                            len(MonitorDict)))
    return 0


def delHostShareMonitor(share_info):
    global MonitorDict
    global MonitorDictLock
    with MonitorDictLock:
        if share_info in MonitorDict:
            del MonitorDict[share_info]
            _logger.debug('del share monitor,user {},have {} share'.format(share_info, len(MonitorDict)))
    return 0


share_status_cmd_err = 'cmd error'
share_status_connect_err = 'connect error'


class hostShareManageThread(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        self.run_status = True

    def run(self):
        self._do_clean()

        try:
            self.work()
        except Exception as e:
            _logger.error('hostShareManageThread error:{}'.format(e))

        _logger.debug('diskShareManageThread exit')

    @xlogging.convert_exception_to_value(None)
    def _do_clean(self):
        _logger.info('hostShareManageThread start do clean')
        with OperatingHostDictLock:  # 清理过程，防止用户添加
            for share in HostSnapshotShare.objects.all():
                boxService.box_service.CmdCtrl(iceGetCmd('del_host', 'break', [share.samba_user, share.dirinfo]), 30)
                LockDiskFilesOper(2, share.locked_files)
                share.delete()
        _logger.info('hostShareManageThread end do clean')

    @xlogging.db_ex_wrap
    def work(self):
        global GET_STATUS_TIME
        global MonitorDict
        global MonitorDictLock
        time.sleep(GET_STATUS_TIME)
        _logger.debug('diskShareManageThread start')
        DiskSnapshotLocker.unlock_files_by_task_name_prefix('shared_')
        _logger.debug('diskShareManageThread unlock files ok.')
        while self.run_status:
            time.sleep(GET_STATUS_TIME)
            del_list = list()
            with MonitorDictLock:
                for key in MonitorDict:
                    mstaus = ''
                    try:
                        retval = boxService.box_service.CmdCtrl(iceGetCmd('get_host_status', 'break', key))
                        mval = json.loads(retval)
                        _logger.debug('get dirinfo {} status {}'.format(key, mval))
                        if mval[0][0] != 0 or mval[0][1][0] != 0:
                            mstaus = share_status_cmd_err
                        else:
                            mstaus = mval[0][1][1]

                    except Exception as e:
                        _logger.error("get dirinfo {} status failed,excep {}".format(key, e))
                        MonitorDict[key] = share_status_connect_err

                    if MonitorDict[key] != mstaus:
                        MonitorDict[key] = mstaus
                        HostSnapshotShare.objects.filter(dirinfo=key).update(share_status=MonitorDict[key])
                        _logger.debug("updata dirinfo {} share status {}".format(key, MonitorDict[key]))

                        # if MonitorDict[key] != 'ok':
                        #     del_list.append(key)

            for i in range(len(del_list)):
                _logger.debug('in thread del share {}'.format(del_list[i]))
                delHostShareMonitor(del_list[i])

    def stop(self):
        _logger.debug('diskShareManageThread stopped')
        self.run_status = False


def getDiskInfo(ident, cdp_time):
    disk_snapshot_object = ''
    try:
        _logger.debug("start get disk snapshot ident {}".format(ident))
        disk_snapshot_object = DiskSnapshot.objects.get(ident=ident)
    except DiskSnapshot.DoesNotExist:
        _logger.error("invalid disk snapshot ident {}".format(ident))
        xlogging.raise_and_logging_error('不存在的disk快照:{}'.format(ident),
                                         'invalid disk snapshot ident', status.HTTP_404_NOT_FOUND)

    _logger.debug(
        "get disk snapshot success,ident {} id {}".format(disk_snapshot_object.id, disk_snapshot_object.ident))
    validator_list = [GetSnapshotList.is_disk_snapshot_object_exist,
                      GetSnapshotList.is_disk_snapshot_file_exist]
    disk_snapshots = GetSnapshotList.query_snapshots_by_snapshot_object(
        disk_snapshot_object, validator_list, cdp_time)
    if len(disk_snapshots) == 0:
        xlogging.raise_and_logging_error('获取硬盘快照信息失败:ident {},time {}'.format(ident, cdp_time),
                                         r'get disk info failed name {} time {}'.format(ident, cdp_time),
                                         status.HTTP_404_NOT_FOUND)

    _logger.debug("disk id {}".format(disk_snapshot_object.id))
    _logger.debug('image path {}'.format(disk_snapshot_object.image_path))
    _logger.debug("disk_snapshots {}".format(disk_snapshots))
    return disk_snapshot_object, disk_snapshots


OperatingHostDict = dict()
OperatingHostDictLock = threading.Lock()


def operating_host_dict(cmdtype, share_info):
    global OperatingHostDict
    global OperatingHostDictLock
    with OperatingHostDictLock:
        if cmdtype == 1:  # check
            if share_info in OperatingHostDict:
                return 0
            else:
                return -1
        elif cmdtype == 2:  # check and add
            if share_info in OperatingHostDict:
                _logger.debug('check share info already:{}'.format(share_info))
                return 0
            else:
                _logger.debug('add share info :{}'.format(share_info))
                OperatingHostDict[share_info] = 'operating'
                return -1
        elif cmdtype == 3:  # add
            if share_info in OperatingHostDict:
                return -1
            else:
                OperatingHostDict[share_info] = 'operating'
                return 0
        elif cmdtype == 4:  # delete
            if share_info in OperatingHostDict:
                _logger.debug('del share info :{}'.format(share_info))
                del OperatingHostDict[share_info]
                return 0
            else:
                return -1
