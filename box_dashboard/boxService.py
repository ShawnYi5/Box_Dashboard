#!/usr/bin/env python3

import configparser
import json
import os
import sys
import threading
import time
import uuid

import Ice
from django.contrib.auth.models import User

from box_dashboard import xlogging, xdata, pyconv

_logger = xlogging.getLogger(__name__)

import Box
import BoxLogic
import KTService
import IMG
import InstallModule
import CProxy
import WatchPowerServ
import Utils
import DataQueuingIce

_box_service_msgs = {
    'isAgentLinked': '获取Agent在线状态失败',
    'queryDisksStatus': '获取Agent磁盘状态失败',
    'restore': '发送还原指令失败',
    'backup': '发送备份指令失败',
    'generatePeStageIso': '生成智能驱动光盘失败',
    'loginExternalDevice': '无法连接到存储设备',
    'refreshExternalDevice': '更新外部存储设备信息失败',
    'GetStatus': '获取Agent状态失败',
    'fetchAgentDebugFile': '获取客户端调试信息失败'
}

message_dict_locker = threading.Lock()
message_dict = dict()
bit_map_locker = threading.Lock()
bit_map_object = dict()

reg_json_cache = dict()
lock_reg_json_cache = threading.Lock()


class _box_service(object):
    def __init__(self):
        xlogging.TraceDecorator(
            ['getBoxPrx', 'getKtsPrx', 'getLogicPrx', 'getImgPrx', 'getInstallPrx', 'GetStatus', 'isPeHostLinked',
             'runCmd', 'getCTcpPrx', 'getPowerPrx', 'getHTBCreatePrx', 'pathJoin', 'isFileExist', 'isFolderExist',
             'AllFilesExist', 'getTotalUesdBlockBitmap']
        ).decorate()
        xlogging.ConvertExceptionToBoxDashboardExceptionDecorator(msgs=_box_service_msgs).decorate()

        _logger.info(r'box_dashdorad_service starting ...')

        self.__boxPrx = None
        self.__ktsPrx = None
        self.__logicPrx = None
        self.__imgPrx = None
        self.__installPrx = None
        self.__cTcpPrx = None
        self.__PowerPrx = None
        self.__HTBCreatePrx = None

        config = self.__generate_ice_config()
        self.__init_ice(config=config)
        self.__create_web_api_user(config.properties.getProperty(r'Logic.WebApi.Username'),
                                   config.properties.getProperty(r'Logic.WebApi.Password'))

        self.__fake_remove = None
        self.__fake_call = None

        self.__fake_call, self.__fake_remove = self.__generate_fake_call()

    def __generate_fake_call(self):
        fake_remove = list()
        fake_call_string = self.communicator.getProperties().getPropertyWithDefault(r'Logic.FakeCall', r'')
        fake_call = fake_call_string.split('|')
        if 'remove_cdp' in fake_call:
            fake_call.remove('remove_cdp')
            fake_call.append('remove')
            fake_remove.append('.cdp')
        if 'remove_hash' in fake_call:
            fake_call.remove('remove_hash')
            fake_call.append('remove')
            fake_remove.append('.hash')
        return list(set(fake_call)), list(set(fake_remove))

    def __check_fake_call(self, fn_string):
        return fn_string in self.__fake_call

    def __check_fake_remove(self, path):
        ext = os.path.splitext(path)[1]
        return ext in self.__fake_remove

    @staticmethod
    def __generate_ice_config():
        initData = Ice.InitializationData()
        initData.properties = Ice.createProperties()
        initData.properties.setProperty(r'Ice.ThreadPool.Client.Size', r'8')
        initData.properties.setProperty(r'Ice.ThreadPool.Client.SizeMax', r'64')
        initData.properties.setProperty(r'Ice.ThreadPool.Client.ThreadIdleTime', r'0')
        initData.properties.setProperty(r'Ice.ThreadPool.Client.StackSize', r'8388608')
        initData.properties.setProperty(r'ImageSerivce.Proxy', r'img : tcp -h localhost -p 21101')
        initData.properties.setProperty(r'BoxSerivce.Proxy', r'apis : tcp -h 127.0.0.1 -p 21105')
        initData.properties.setProperty(r'InstallInterface.Proxy', r'install : tcp -h 127.0.0.1 -p 21106')
        initData.properties.setProperty(r'KTSerivce.Proxy', r'kts : tcp -h 127.0.0.1 -p 21108')
        initData.properties.setProperty(r'BoxLogic.Proxy', r'logicInternal : tcp -h 127.0.0.1 -p 21109')
        initData.properties.setProperty(r'CTcp.Proxy', r'tcpcproxy : tcp -h localhost -p 21107')
        initData.properties.setProperty(r'PowerOffProc.Proxy', r'poweroffproc : tcp -h 127.0.0.1 -p 21110')
        initData.properties.setProperty(r'DataCreator.Proxy', r'datacreator:tcp -h 127.0.0.1 -p 21113')
        initData.properties.setProperty(r'Ice.Default.Host', r'localhost')
        initData.properties.setProperty(r'Ice.Warn.Connections', r'1')
        initData.properties.setProperty(r'Ice.LogFile', r'/var/log/aio/box_dashboard_ice.log')
        initData.properties.setProperty(r'Ice.RetryIntervals', r'0')

        initData.properties.setProperty(r'Ice.MessageSizeMax', r'65536')  # 64MB

        initData.properties.setProperty(r'Ice.ACM.Heartbeat', r'3')  # BoxService KernelTcp 会检测心跳

        initData.properties.setProperty(r'BoxSerivce.ReloginDelaySeconds', r'60')

        initData.properties.setProperty(r'Logic.DefaultStorageNodePath', r'/home/aio')

        initData.properties.setProperty(r'Logic.TcpKernelService.Restore.SocketNumber', r'10')
        initData.properties.setProperty(r'Logic.TcpKernelService.Port', r'20002')
        initData.properties.setProperty(r'Logic.TcpKernelService.CDP.SocketNumber', r'1')
        initData.properties.setProperty(r'Logic.TcpKernelService.CDP.CacheBytes', r'134217728')  # 128MB
        initData.properties.setProperty(r'Logic.TcpKernelService.CDP.Timeouts', r'10')
        initData.properties.setProperty(r'Logic.TcpKernelService.CDP.RotatingFileInterval', r'43200')  # 60*60*12s 12h
        initData.properties.setProperty(r'Logic.DeleteTempKvmIso', r'1')  # 大于0 就删除

        initData.properties.setProperty(r'Logic.WebApi.Username', r'web_api')
        initData.properties.setProperty(r'Logic.WebApi.Password', r'd24609a757394b40bb838c8f3a378fb1')

        initData.properties.setProperty(r'Logic.SpaceCollectionInterval', r'300')  # 60*5s 5m

        initData.properties.setProperty(r'Logic.CompressionEnable', r'1')  # 是否启用（启用1 关闭0）
        initData.properties.setProperty(r'Logic.CompressionAlgorithm', r'4')  # 算法
        initData.properties.setProperty(r'Logic.CompressionRank', r'11')  # 压缩等级
        initData.properties.setProperty(r'Logic.CompressionMeasureBusy', r'50')  # 判断繁忙

        initData.properties.setProperty(r'Logic.PeHostFilterWithUser', r'0')  # 过滤用户 （0、不过滤 1、过滤）
        initData.properties.setProperty(r'Logic.VerifyUserFingerprint', r'0')  # 强制检测用户指纹
        initData.properties.setProperty(r'Logic.AlwaysAllocateUser', r'0')  # 总是分配主机到所属的用户 (如果Host没有用户)
        initData.properties.setProperty(r'Logic.DisableAlterUser', r'0')  # 禁止修改主机用户
        initData.properties.setProperty(r'Logic.DefaultNetworkTransmissionType', r'2')  # 1、加密 2、明文 3、加密优先
        initData.properties.setProperty(r'Logic.RetryScheduleStyle', r'-1')  # -1、不反复重试，0、为反复重试
        initData.properties.setProperty(r'Logic.CalcNextRunVersion', '1')  # 计划调度算法版本

        config_path = r'/etc/aio/box_dashboard.cfg'
        if os.path.exists(config_path):
            initData.properties.load(config_path)

        return initData

    def __init_ice(self, config):
        self.communicator = Ice.initialize(sys.argv, config)

    @staticmethod
    def __create_web_api_user(username, password):
        try:
            User.objects.get(username=username)
        except User.DoesNotExist:
            User.objects.create_superuser(username, '{}@nothingness.local'.format(username), password)

    def __del__(self):
        self.communicator.destroy()

    def getImgPrx(self):
        if self.__imgPrx is None:
            self.__imgPrx = IMG.ImgServicePrx.checkedCast(self.communicator.propertyToProxy(r'ImageSerivce.Proxy'))
        return self.__imgPrx

    def getBoxPrx(self):
        if self.__boxPrx is None:
            self.__boxPrx = Box.ApisPrx.checkedCast(self.communicator.propertyToProxy(r'BoxSerivce.Proxy'))
        return self.__boxPrx

    def getKtsPrx(self):
        if self.__ktsPrx is None:
            self.__ktsPrx = KTService.KTSPrx.checkedCast(self.communicator.propertyToProxy(r'KTSerivce.Proxy'))
        return self.__ktsPrx

    def getLogicPrx(self):
        if self.__logicPrx is None:
            self.__logicPrx = BoxLogic.LogicInternalPrx.checkedCast(
                self.communicator.propertyToProxy(r'BoxLogic.Proxy'))
        return self.__logicPrx

    def getInstallPrx(self):
        if self.__installPrx is None:
            self.__installPrx = InstallModule.InstallInterfacePrx.checkedCast(
                self.communicator.propertyToProxy(r'InstallInterface.Proxy'))
        return self.__installPrx

    def getCTcpPrx(self):
        if self.__cTcpPrx is None:
            self.__cTcpPrx = CProxy.TunnelManagerPrx.checkedCast(self.communicator.propertyToProxy(r'CTcp.Proxy'))
        return self.__cTcpPrx

    def getPowerPrx(self):
        if self.__PowerPrx is None:
            self.__PowerPrx = WatchPowerServ.PowerOffProcPrx.checkedCast(
                self.communicator.propertyToProxy(r'PowerOffProc.Proxy'))
        return self.__PowerPrx

    def getHTBCreatePrx(self):
        if self.__HTBCreatePrx is None:
            self.__HTBCreatePrx = DataQueuingIce.DataCreatorPrx.checkedCast(
                self.communicator.propertyToProxy(r'DataCreator.Proxy'))
        return self.__HTBCreatePrx

    def get_communicator(self):
        return self.communicator

    def isAgentLinked(self, host_ident):
        return self.getBoxPrx().isAgentLinked(host_ident)

    def forceOfflineAgent(self, host_ident):
        return self.getBoxPrx().forceOfflineAgent(host_ident)

    def forceOfflinePeHost(self, pe_host_ident):
        return self.getBoxPrx().forceOfflinePeHost(pe_host_ident)

    @xlogging.convert_exception_to_value(None)
    def reloginAllHostSession(self):
        delay = self.communicator.getProperties().getPropertyAsInt(r'BoxSerivce.ReloginDelaySeconds')
        return self.getBoxPrx().reloginAllHostSession(delay)

    def queryDisksStatus(self, host_ident):
        r, more = self.getBoxPrx().queryDisksStatus(host_ident)
        if more:
            disks = json.loads(more)['disks']
        else:
            disks = list()

        for disk in r:
            for m in disks:
                if m['id'] == disk.id:
                    disk.detail.systemDevice = m['is_system']
                    disk.detail.bmfDevice = m['is_bmf']
                    break
            else:
                disk.detail.systemDevice = disk.detail.bmfDevice = disk.detail.bootDevice
        return r

    def restore(self, host_ident, info_data, image_list_data, json_config):
        self.getBoxPrx().restore(host_ident, info_data, image_list_data, json_config)

    def updateToken(self, token):
        self.getKtsPrx().update(token)

    def updateClientCdpToken(self, clientToken, cdpFileToken, clientName, diskIndex, rev, revTwo):
        self.getKtsPrx().updateClientCdpToken(clientToken, cdpFileToken, clientName, diskIndex, rev, revTwo)

    def backup(self, host_ident, images, mega_bytes_per_second, additional_cmd):
        try:
            self.getBoxPrx().backup(host_ident, images, mega_bytes_per_second, additional_cmd)
            return 0, None, None, None
        except Utils.CreateSnapshotImageError as e:
            _logger.error(r'getBoxPrx().backup failed Utils.CreateSnapshotImageError')
            return 1, e.description, e.debug, e.rawCode

    def volumeRestore(self, host_ident, json_string, restore_files, dummy_host):
        return self.getBoxPrx().volumeRestore(host_ident, json_string, restore_files, dummy_host)

    def queryLastBackupError(self, host_ident):
        return self.getBoxPrx().queryLastBackupError(host_ident)

    def queryLastCdpError(self, host_ident):
        return self.getBoxPrx().queryLastCdpError(host_ident)

    def forceCloseBackupFiles(self, files):
        return self.getBoxPrx().forceCloseBackupFiles(files)

    def stopCdpStatus(self, host_ident):
        return self.getBoxPrx().stopCdpStatus(host_ident)

    def pathJoin(self, paths):
        return os.path.join(*paths)
        # return self.getLogicPrx().pathJoin(paths)

    def isFileExist(self, path, force_wait=True):
        def _isFileExist():
            rev = self.getLogicPrx().isFileExist(path)
            if not rev:
                _logger.warning('isFileExist path {} not exists'.format(path))
            return rev

        if force_wait:
            while True:
                try:
                    return _isFileExist()
                except Exception as e:
                    _logger.warning(r'isFileExist failed {}'.format(e))
                    time.sleep(60)
        else:
            return _isFileExist()

    def AllFilesExist(self, paths):
        rev = self.getLogicPrx().AllFilesExist(paths)
        if not rev:
            _logger.warning('AllFilesExist paths {} not exists'.format(paths))
        return rev

    def isFolderExist(self, path):
        rev = self.getLogicPrx().isFolderExist(path)
        if not rev:
            _logger.warning('isFolderExist path {} not exists'.format(path))
        return rev

    def makeDirs(self, path, existOk=True, mode=0o755):
        return self.getLogicPrx().makeDirs(path, existOk, mode)

    def remove(self, path, enable_fake=True):
        if self.__check_fake_call('remove') and self.__check_fake_remove(path):
            if enable_fake:
                _logger.warning(r'remove enable fake : {}'.format(path))
                return
            else:
                _logger.warning(r'remove disable fake : {}'.format(path))

        self.getLogicPrx().remove(path)

    def copy(self, new_path, path):
        self.getLogicPrx().copy(json.dumps({'new_path': new_path, 'path': path}))

    def move(self, new_path, path):
        self.runCmd(r'mv {} {}'.format(path, new_path))

    def findFiles(self, pattern, path):
        return self.getLogicPrx().findFiles(json.dumps({'pattern': pattern, 'path': path}))

    def queryCdpTimestampRange(self, path, discard_dirty_data=False):
        try:
            result = self.getLogicPrx().queryCdpTimestampRange(path, discard_dirty_data)
        except Utils.SystemError as se:
            if se.rawCode == xdata.CDP_FILE_NO_CONTENT_ERR:
                return None, None
            raise se

        result_list = result.split()
        if len(result_list) != 2:
            xlogging.raise_and_logging_error(r'读取CDP数据失败，获取{}的时间范围失败'.format(path),
                                             r'get {} timestamp range error. discard_dirty_data:{}'.format(
                                                 path, discard_dirty_data))
        return float(result_list[0]), float(result_list[1])

    def queryCdpTimestamp(self, path, timestamp, mode='forwards'):  # mode: 'forwards', 'backwards'
        result = self.getLogicPrx().queryCdpTimestamp(path, '{0:f}|{1}'.format(timestamp, mode))
        result_list = result.split()
        if len(result_list) != 1:
            xlogging.raise_and_logging_error(r'读取CDP数据失败，获取{}的时间点{}失败'.format(path, timestamp),
                                             r'get {} snapshot time {} error'.format(path, timestamp))
        return float(result_list[0])

    def formatCdpTimestamp(self, timestamp):
        return self.getLogicPrx().formatCdpTimestamp('{:f}'.format(timestamp))

    def mergeCdpFile(self, params):
        if not os.path.exists(r'/dev/shm/fake_merge_cdp_file'):
            return self.getLogicPrx().mergeCdpFile(params)

    def mergeCdpFiles(self, config):
        return self.getLogicPrx().mergeCdpFiles(config)

    def cutCdpFile(self, config):
        return self.getLogicPrx().cutCdpFile(config)

    def mergeQcowFile(self, config):
        return self.getLogicPrx().mergeQcowFile(config)

    def GetPeHostNetAdapterInfo(self, pe_host_ident):
        returned, result = self.getBoxPrx().GetPeHostNetAdapterInfo(pe_host_ident)
        if returned != 0:
            xlogging.raise_and_logging_error(
                r'获取网络适配器失败', r'call BoxPrx GetPeHostNetAdapterInfo {} failed. returned {}'.format(
                    pe_host_ident, returned), returned, _logger)
        return result

    def GetPeHostClassHWInfo(self, pe_host_ident, class_name, parent_level=8):
        returned, result = self.getBoxPrx().GetPeHostClassHWInfo(pe_host_ident, class_name, parent_level)
        if returned != 0:
            xlogging.raise_and_logging_error(
                r'获取硬件信息失败', r'call BoxPrx GetPeHostClassHWInfo {} - {} - {} failed. returned {}'.format(
                    pe_host_ident, class_name, parent_level, returned))
        return result

    def KvmStopped(self, pe_host_ident):
        self.getBoxPrx().KvmStopped(pe_host_ident)

    def GetStatus(self, hostIdent):
        return self.getBoxPrx().GetStatus(hostIdent)

    def isHardwareDriverExist(self, hardware, os_type, os_bit):
        return self.getLogicPrx().isHardwareDriverExist(hardware, os_type, os_bit)

    def GetDriversVersions(self, hardware, os_type, os_bit):
        return self.getLogicPrx().GetDriversVersions(hardware, os_type, os_bit)

    def ChkIsSubId(self, hardware):
        return self.getLogicPrx().ChkIsSubId(hardware)

    def GetDriversSubList(self, userSelect):
        return self.getLogicPrx().GetDriversSubList(json.dumps(userSelect))

    def generatePeStageIso(self, isoWorkerFolderPath, isoFilePath, hardwares, ipconfigs, pci_bus_device_ids, os_type,
                           os_bit, drivers_ids, agentServiceConfigure):
        return self.getLogicPrx().generatePeStageIso(
            isoWorkerFolderPath, isoFilePath, hardwares, ipconfigs, pci_bus_device_ids, os_type, os_bit, drivers_ids,
            agentServiceConfigure)

    def runRestoreKvm(self, params):
        return self.getLogicPrx().runRestoreKvm(params)

    def isPeHostLinked(self, peHostIdent):
        return self.getBoxPrx().isPeHostLinked(peHostIdent)

    def start_agent_pe(self, host_ident):
        try:
            pe_ident = self.getBoxPrx().StartAgentPe(host_ident)
        except Utils.SystemError as se:
            if se.rawCode == xdata.HOST_IN_CDPING_STATUS:
                return False, None
            raise se
        return True, pe_ident

    def getNetworkInfos(self):
        return self.getLogicPrx().getCurrentNetworkInfos()

    def setNetwork(self, net_infos):
        return self.getLogicPrx().setNetwork(net_infos)

    def enumStorageNodes(self):
        return json.loads(self.getLogicPrx().enumStorageNodes())

    def formatAndInitializeStorageNode(self, node):
        return self.getLogicPrx().formatAndInitializeStorageNode(json.dumps(node, ensure_ascii=False))

    def mountStorageNode(self, node):
        return self.getLogicPrx().mountStorageNode(json.dumps(node, ensure_ascii=False))

    def unmountStorageNode(self, node):
        return self.getLogicPrx().unmountStorageNode(json.dumps(node, ensure_ascii=False))

    def refreshExternalDevice(self, iqn):
        return self.getLogicPrx().refreshExternalDevice(iqn)

    def logoutExternalDevice(self, iqn):
        return self.getLogicPrx().logoutExternalDevice(iqn)

    def loginExternalDevice(self, ip, port, use_chap, user_name, password):
        return self.getLogicPrx().loginExternalDevice(ip, port, use_chap, user_name, password)

    def runCmd(self, cmd, shell=False):
        return self.getLogicPrx().runCmd(cmd, shell)

    def CmdCtrl(self, cmdinfo, timeout=None):
        if timeout is None:
            return self.getLogicPrx().CmdCtrl(cmdinfo)
        else:
            prx = self.getLogicPrx()
            async_object = prx.begin_CmdCtrl(cmdinfo)
            i = 0
            # 如果ice call没有执行完毕，会有极少量的内存泄漏
            while i < timeout:
                time.sleep(1)
                if async_object.isCompleted():
                    return prx.end_CmdCtrl(async_object)
                else:
                    i += 1

    def getPasswd(self):
        return self.getLogicPrx().getPasswd()

    def setPasswd(self, passwdinfo):
        return self.getLogicPrx().setPasswd(passwdinfo)

    def getLocalIqn(self):
        return self.getLogicPrx().getLocalIqn()

    def setLocalIqn(self, iqn):
        return self.getLogicPrx().setLocalIqn(iqn)

    def setGlobalDoubleChap(self, user_name, password):
        return self.getLogicPrx().setGlobalDoubleChap(user_name, password)

    # return (enabled, user_name, password)
    def getGlobalDoubleChap(self):
        return self.getLogicPrx().getGlobalDoubleChap()

    def renameSnapshot(self, path, ident, new_ident, disk_bytes):
        base_snapshots = [IMG.ImageSnapshotIdent(path, ident), ]
        new_image_ident = IMG.ImageSnapshotIdent(path, new_ident)
        handle = self.createNormalDiskSnapshot(new_image_ident, base_snapshots, disk_bytes,
                                               r'PiD{:x} BoxDashboard|renameSnapshot'.format(os.getpid()))
        self.closeNormalDiskSnapshot(handle, True)
        hash_path = r'{}_{}.hash'.format(path, ident)
        if box_service.isFileExist(hash_path):
            new_hash_path = r'{}_{}.hash'.format(path, new_ident)
            self.move(new_hash_path, hash_path)
        os.system('sync')
        time.sleep(1)
        self.deleteNormalDiskSnapshot(path, ident)

    def deleteNormalDiskSnapshot(self, path, ident, enable_fake=True):
        if self.__check_fake_call('deleteNormalDiskSnapshot') and enable_fake:
            _logger.warning(r'deleteNormalDiskSnapshot enable fake')
            return

        try:
            config_path = '/dev/shm/delete_normal_disk_snapshot_sleep'
            if os.path.exists(config_path):
                with open(config_path) as f:
                    sleep_seconds = int(f.read())
                    _logger.debug(r'deleteNormalDiskSnapshot will sleep : {}'.format(sleep_seconds))
                    time.sleep(sleep_seconds)
        except Exception:
            pass

        returned = self.getImgPrx().DelSnaport(IMG.ImageSnapshotIdent(path, ident))
        if returned == -2:
            xlogging.raise_and_logging_error(
                r'快照磁盘镜像({})正在使用中，无法回收'.format(ident),
                r'delete snapshot {} - {} failed, using'.format(path, ident))
        elif returned != 0:
            xlogging.raise_and_logging_error(
                r'回收快照磁盘镜像({})失败'.format(ident),
                r'delete snapshot {} - {} failed, {}'.format(path, ident, returned))

    def openSnapshots(self, snapshots, flag):
        return self.getImgPrx().open(snapshots, flag)

    def getTotalUesdBlockBitmap(self, handle, index):
        return self.getImgPrx().getTotalUesdBlockBitmap(handle, index)

    def createNormalDiskSnapshot(self, ident, last_snapshot, disk_bytes, flag):
        handle = self.getImgPrx().create(ident, last_snapshot, disk_bytes, flag)
        if handle == 0 or handle == -1:
            xlogging.raise_and_logging_error(
                r'创建快照磁盘镜像失败'.format(ident),
                r'create snapshot {} - {} failed, {} {} {}'.format(ident, last_snapshot, disk_bytes, handle, flag))
        else:
            _logger.info(r'createNormalDiskSnapshot ok {} {} {} {} {}'.format(
                handle, ident, last_snapshot, disk_bytes, flag))
            return handle

    def closeNormalDiskSnapshot(self, handle, successful):
        _logger.info(r'closeNormalDiskSnapshot : {} {}'.format(handle, successful))
        self.getImgPrx().close(handle, successful)

    def GetOnSnMapFile(self, ident):
        return self.getImgPrx().GetOnSnMapFile(ident)

    def querySystemInfo(self, ident):
        return self._filling_disk_field_in_system_info(self.getBoxPrx().querySystemInfo(ident))

    def _filling_disk_field_in_system_info(self, system_info_str):
        """
            新版的windows客户端会有新的字段 `volumes`， 老的字段 `Disk`内容无法使用,
        为了代码兼容，需要将volumes中的内容填充到Disk中，使以前的业务逻辑正常。
        :param system_info_str:
        :return:new system_info_str
        """
        system_info = json.loads(system_info_str)
        if 'linux' in system_info['System']['SystemCaption'].lower():
            return system_info_str
        else:
            # old client do nothing
            if 'volumes' not in system_info:
                return system_info_str
            else:
                disk_info = dict()
                for vol in system_info['volumes']:  # 枚举所有的卷
                    if not vol['Extents']:  # vol['Extents'] 可能为None
                        vol['Extents'] = list()
                    if len(vol['Extents']) >= 2:  # 判断是否是动态盘
                        dynamic_disk = True
                    else:
                        dynamic_disk = False
                    for vol_range in vol['Extents']:
                        disk_info.setdefault(vol_range['DiskNumber'], dict(pt=list(), dynamic_disk=dynamic_disk))
                        disk_info[str(vol_range['DiskNumber'])]['pt'].append({
                            'FileSystem': vol['FileSystem'],
                            'FreeSize': -1,
                            'Index': '',
                            'Letter': vol['Letter'],
                            'PartitionOffset': vol_range['StartingOffset'],
                            'PartitionSize': vol_range['ExtentLength'],
                            'Style': '',
                            'VolumeLabel': vol['VolumeLabel'],
                            'VolumeName': vol['VolumeName'],
                            'VolumeSize': vol['TotalNumberOfBytes']
                        })
                # 修正system_info
                for disk in system_info['Disk']:  # 填充信息
                    info = disk_info.get(str(disk['DiskNum']), dict(pt=list(), dynamic_disk=False))
                    dynamic_disk, pt = info['dynamic_disk'], info['pt']
                    if dynamic_disk:
                        disk['Partition'] = sorted(pt, key=lambda x: int(x['PartitionOffset']))
                        disk['dynamic_disk'] = True
                    else:
                        disk['dynamic_disk'] = False
                return json.dumps(system_info)

    def updateTrafficControl(self, io_session, ident, kilo_bytes_per_second):
        return self.getKtsPrx().updateTrafficControl(io_session, ident, kilo_bytes_per_second)

    def installfun(self, path):
        # self.getInstallPrx().install('chmod +1 ' + path)
        return self.getInstallPrx().install(path)

    def fetchAgentDebugFile(self, host_ident, path):
        return self.getBoxPrx().fetchAgentDebugFile(host_ident, path)

    def updateTunnelsEndPoints(self, endPoints):
        self.getCTcpPrx().Update(endPoints)

    def getTime(self):
        return self.getPowerPrx().GetTime()

    def getHostHardwareInfo(self, hostName, inputParam):
        return self.getBoxPrx().JsonFunc(hostName, inputParam)

    def async_JsonFunc(self, hostName, inputParam):
        i_obj = json.loads(inputParam)
        i_obj['box_service_async'] = True
        return self.getBoxPrx().JsonFunc(hostName, json.dumps(i_obj, ensure_ascii=False))

    def get_restore_key_data_process(self, pe_ident):
        return self.getBoxPrx().QueryRWDiskWithPeHost(pe_ident)

    def writeFile2Host(self, host_ident, pathPrefix, path, byteOffset, bs):
        config = {
            'type': 'write_new', 'pathPrefix': pathPrefix, 'path': path, 'byteOffset': '{}'.format(byteOffset)
        }
        return self.getBoxPrx().JsonFuncV2(host_ident, json.dumps(config), bs)

    def aswriteFile2Host(self, host_ident, pathPrefix, path, byteOffset, bs):
        config = {
            'type': 'write_new', 'pathPrefix': pathPrefix, 'path': path, 'byteOffset': '{}'.format(byteOffset)
        }
        return self.getBoxPrx().begin_JsonFuncV2(host_ident, json.dumps(config), bs)

    def fetch_reg_json(self, pe_ident, clean_cache=False):
        with lock_reg_json_cache:
            if clean_cache:
                return reg_json_cache.pop(pe_ident, None)

            if pe_ident in reg_json_cache:
                return reg_json_cache[pe_ident]

        try:
            none_obj = None
            inputJson = {"type": "get_size", "pathPrefix": "current", "path": "reg.json", "byteOffset": "", "bytes": ""}
            outputJson, outputBs = self.getBoxPrx().PEJsonFunc(pe_ident, json.dumps(inputJson), none_obj)
            file_infos = json.loads(outputJson)
            file_size = int(file_infos['Bytes'].strip(), 16)

            allOutputBs = b''
            one_MB = 1024 * 1024
            byteOffset, maxOffset = 0, (file_size - 1)
            while byteOffset <= maxOffset:
                inputJson = {"type": "read_exist", "pathPrefix": "current", "path": "reg.json",
                             "byteOffset": str(byteOffset), "bytes": str(one_MB)}
                outputJson, outputBs = self.getBoxPrx().PEJsonFunc(pe_ident, json.dumps(inputJson), none_obj)
                allOutputBs += outputBs
                byteOffset += one_MB

            pe_reg_json = json.loads(allOutputBs.decode(encoding="utf-8"))
            with lock_reg_json_cache:
                reg_json_cache.update({pe_ident: pe_reg_json})
            return pe_reg_json
        except Utils.OperationNotExistError:
            _logger.error(r'getBoxPrx().PEJsonFunc failed Utils.OperationNotExistError')
            return None
        except Exception as e:
            _logger.error(r'fetch_reg_json failed, {}'.format(e))
            return None

    def get_host_ini_info(self, host_ident):
        """
        :param host_ident:
        :return: ConfigParser instance
        use config.get('restore', 'restore_token', fallback='') get restore_token, fail get ''
        use config.get('restore', 'htb_task_uuid', fallback='') get htb_task_uuid, fail get ''
        """
        try:
            inputJson = {"type": "get_size", "pathPrefix": "current", "path": "AgentService.ini", "byteOffset": "",
                         "bytes": ""}
            outputJson, _ = self.JsonFuncV2(host_ident, json.dumps(inputJson), None)
            file_infos = json.loads(outputJson)
            file_size = int(file_infos['Bytes'].strip(), 16)

            inputJson = {"type": "read_exist", "pathPrefix": "current", "path": "AgentService.ini",
                         "byteOffset": '0', "bytes": str(file_size)}

            _, raw = self.JsonFuncV2(host_ident, json.dumps(inputJson), None)
            content = raw.decode('utf-8')
            _logger.info('get_host_ini_info content:{}'.format(content))
            config = configparser.ConfigParser()
            config.read_string(content)
        except Utils.OperationNotExistError:
            return 'not support'
        except Exception:
            return 'retry'
        else:
            return config

    def simpleRunShell(self, host_ident, cmd):
        config = {
            'SimpleRunShell': {
                'cmd': cmd
            }
        }
        return self.getBoxPrx().JsonFunc(host_ident, json.dumps(config))

    def JsonFuncV2(self, hostName, inputParam, inputBs):
        return self.getBoxPrx().JsonFuncV2(hostName, inputParam, inputBs)

    def PEJsonFunc(self, hostName, inputParam, inputBs=b''):
        try:
            return self.getBoxPrx().PEJsonFunc(hostName, inputParam, inputBs)
        except Utils.OperationNotExistError as e:
            raise xlogging.OperationImplemented(function_name='PEJsonFunc', msg=e.description,
                                                debug='Utils.OperationNotExistError: PEJsonFunc OperationImplemented',
                                                file_line=0, http_status=556, is_log=True)
        except Utils.SystemError as se:
            if se.debug.startswith('JsonFunc The method or operation is not implemented'):
                raise xlogging.OperationImplemented(function_name='PEJsonFunc', msg=se.description,
                                                    debug='Utils.SystemError: PEJsonFunc OperationImplemented',
                                                    file_line=0, http_status=556, is_log=True)
            raise se

    def get_service_list(self, hostName):
        return self.getBoxPrx().GetServiceList(hostName)

    def get_tcp_listen_list(self, hostName, portList):
        return self.getBoxPrx().GetTcpListenList(hostName, portList)

    def start_service_sync(self, hostName, serviceName):
        return self.getBoxPrx().StartServiceSync(hostName, serviceName)

    def stop_service_sync(self, hostName, serviceName):
        return self.getBoxPrx().StopServiceSync(hostName, serviceName)

    def start_http_d_service_async(self, hostName, port, bs):
        return self.getBoxPrx().StartHttpDServiceAsync(hostName, port, bs)

    def get_http_d_service_list_sync(self, hostName):
        return self.getBoxPrx().GetHttpDServiceListSync(hostName)

    def stop_all_http_d_service_sync(self, hostName):
        return self.getBoxPrx().StopAllHttpDServiceSync(hostName)

    def testDisk(self, hostName, diskIndex, sectorOffset, numberOfSectors):
        return self.getBoxPrx().testDisk(hostName, diskIndex, sectorOffset, numberOfSectors)

    def calcClusterTime0Hash(self, config):
        return self.getLogicPrx().calcClusterTime0Hash(json.dumps(config))

    def generateClusterDiffImages(self, config):
        return self.getLogicPrx().generateClusterDiffImages(json.dumps(config))

    def StartQemuWork(self, task, token, snapshots, excludes=list()):
        return self.getHTBCreatePrx().StartQemuWork(task, token, snapshots, excludes)

    def StartQemuWorkForBitmap(self, task, token, snapshots, bit_map, excludes=list()):
        return self.getHTBCreatePrx().StartQemuWorkForBitmap(task, token, snapshots, bit_map, excludes)

    def StartQemuWorkForBitmapv2(self, task, token, snapshots, bit_map_path, excludes=None):
        excludes = list() if excludes is None else excludes
        return self.getHTBCreatePrx().StartQemuWorkForBitmapv2(task, token, snapshots, bit_map_path, excludes)

    def StartCDPWork(self, task, token, cdp_file, start_time, is_wait=True, excludes=None):
        excludes = list() if excludes is None else excludes
        return self.getHTBCreatePrx().StartCDPWork(task, token, cdp_file, start_time, is_wait, excludes)

    @xlogging.LockDecorator(bit_map_locker)
    def StopQemuWork(self, task, token):
        global bit_map_object
        code, o_bit_map = self.getHTBCreatePrx().StopQemuWork(task, token)
        key = '{}_{}'.format(task, token)
        bit_map_object[key] = o_bit_map
        return code, key

    def StopQemuWorkv2(self, task, token):
        return self.getHTBCreatePrx().StopQemuWorkv2(task, token)

    def StopCDPWork(self, task, token):
        return self.getHTBCreatePrx().StopCDPWork(task, token)

    def QueryCDPProgress(self, task, token):
        return self.getHTBCreatePrx().QueryCDPProgress(task, token)

    def QueryQemuProgress(self, task, token):
        return self.getHTBCreatePrx().QueryQemuProgress(task, token)

    def SetRestoreBitmap(self, task, _bit_map, token):
        _d = {'token': token, 'bitmap': _bit_map}
        disk_bit_map = pyconv.convertJSON2OBJ(DataQueuingIce.DiskBitmap, _d)
        return self.getHTBCreatePrx().SetRestoreBitmap(task, disk_bit_map)

    def SetRestoreBitmapv2(self, task, bit_map_path, token):
        _d = {'token': token, 'bitmapPath': bit_map_path}
        disk_bit_map = pyconv.convertJSON2OBJ(DataQueuingIce.DiskBitmapv2, _d)
        return self.getHTBCreatePrx().SetRestoreBitmapv2(task, disk_bit_map)

    def QueryWorkStatus(self, task, token):
        return self.getHTBCreatePrx().QueryWorkStatus(task, token)

    def EndTask(self, task):
        return self.getHTBCreatePrx().EndTask(task)

    def CloseTask(self, task):
        return self.getHTBCreatePrx().CloseTask(task)

    def getRawDiskFiles(self, binpath, destpath):
        return self.getLogicPrx().getRawDiskFiles(binpath, destpath)

    def getBackupInfo(self, host_ident, json_str):
        return self.getBoxPrx().getBackupInfo(host_ident, json_str)

    def setBackupInfo(self, host_ident, json_str):
        return self.getBoxPrx().setBackupInfo(host_ident, json_str)

    def NbdFindUnusedReverse(self):
        return self.getLogicPrx().NbdFindUnusedReverse()

    def NbdSetUnused(self, device_name):
        return self.getLogicPrx().NbdSetUnused(device_name)

    def NbdSetUsed(self, device_name):
        return self.getLogicPrx().NbdSetUsed(device_name)

    def queryTakeOverHostInfo(self, query_string):
        return self.getLogicPrx().queryTakeOverHostInfo(query_string)

    def refreshNetwork(self):
        try:
            self.getKtsPrx().refreshNetwork()
        except Exception as e:
            _logger.error("getKtsPrx refreshNetwork failed {}".format(e), exc_info=True)
        try:
            self.getBoxPrx().refreshNetwork()
        except Exception as e:
            _logger.error("getBoxPrx refreshNetwork failed {}".format(e), exc_info=True)

    def mergeHashFile(self, old_path, path, disk_bytes):
        return self.getLogicPrx().mergeHashFile(json.dumps({
            'old_path': old_path, 'type': 'merge2end', 'disk_bytes': disk_bytes, 'path': path
        }))

    def startBackupOptimize(self, params):
        if isinstance(params, str):
            return self.getLogicPrx().startBackupOptimize(params)
        else:
            return self.getLogicPrx().startBackupOptimize(json.dumps(params))

    def stopBackupOptimize(self, params):
        if isinstance(params, str):
            return self.getLogicPrx().stopBackupOptimize(params)
        else:
            return self.getLogicPrx().stopBackupOptimize(json.dumps(params))

    def reorganizeHashFile(self, bitmap, json_params):
        map_path = os.path.join(xdata.TMP_BITMAP_DIR, '{}_reorganizeHashFile'.format(uuid.uuid4().hex))
        try:
            with open(map_path, 'wb') as wf:
                wf.write(bitmap)
            rev = self.getLogicPrx().reorganizeHashFilev2(map_path, json_params)
        finally:
            self.getLogicPrx().remove(map_path)
        return rev

    def archiveMediaOperation(self, params):
        return self.getLogicPrx().archiveMediaOperation(params)

    def hash2Interval(self, params):
        return self.getLogicPrx().hash2Interval(params)

    def exportSnapshot(self, params):
        return self.getLogicPrx().exportSnapshot(params)

    def get_archive_file_meta_data(self, params):
        return self.getLogicPrx().getArchiveFileMetaData(params)

    def gen_archive_qcow_file(self, params):
        return self.getLogicPrx().genArchiveQcowFile(params)

    def fileBackup(self, params):
        return self.getLogicPrx().fileBackup(params)

    def kvm_remote_procedure_call(self, params):
        from xdashboard.common.dict import GetDictionary
        from xdashboard.models import DataDictionary
        params_obj = json.loads(params)
        if params_obj.get('info'):
            params_obj['info']['aio_server_ip'] = '{}'.format(
                '{}{}'.format(GetDictionary(DataDictionary.DICT_TYPE_TAKEOVER_SEGMENT, 'SEGMENT', '172.29'), '.16.2'))
        return self.getLogicPrx().kvmRpc(json.dumps(params_obj, ensure_ascii=False))

    def generateClusterDiffQcow(self, params):
        return self.getLogicPrx().generateClusterDiffQcow(params)


box_service = _box_service()


def get_tcp_kernel_service_ip(local_ip, agent_connect_ip=None):
    if local_ip == '127.0.0.1':
        return local_ip
    from_config = box_service.communicator.getProperties().getPropertyWithDefault(r'Logic.TcpKernelService.Ip', None)
    if from_config is None or from_config == '':
        return agent_connect_ip if agent_connect_ip else local_ip
    else:
        return from_config


def get_agent_service_config_host(local_ip, agent_connect_ip):
    if local_ip == '127.0.0.1':
        return local_ip
    from_config = box_service.communicator.getProperties().getPropertyWithDefault(
        r'Logic.AgentServiceWithRestoreTarget.Host', None)
    if from_config is None or from_config == '':
        return get_tcp_kernel_service_ip(local_ip, agent_connect_ip)
    else:
        return from_config


def get_tcp_kernel_service_port():
    return box_service.communicator.getProperties().getPropertyAsInt(r'Logic.TcpKernelService.Port')


def get_tcp_kernel_service_restore_socket_number():
    return box_service.communicator.getProperties().getPropertyAsInt(r'Logic.TcpKernelService.Restore.SocketNumber')


def get_default_storage_node_path():
    return box_service.communicator.getProperties().getProperty(r'Logic.DefaultStorageNodePath')


def get_tcp_kernel_service_cdp_socket_number():
    return box_service.communicator.getProperties().getPropertyAsInt(r'Logic.TcpKernelService.CDP.SocketNumber')


# default is 128MB
@xlogging.convert_exception_to_value(134217728)
def get_tcp_kernel_service_cdp_cache_bytes(memSize):
    _1_G = 1024 * 1024 * 1024
    _1_M = 1024 * 1024
    if not memSize:
        # 没有获取到目标机内存大小
        return box_service.communicator.getProperties().getPropertyAsInt(r'Logic.TcpKernelService.CDP.CacheBytes')
    memSize = int(memSize)
    if memSize <= 2 * _1_G:
        # 目标机内存小于2G，为128MB
        return int(128 * _1_M)
    elif memSize <= 16 * _1_G:
        # 目标机内存小于16G，为128MB + （nGB - 2GB）* 0.1。最大到1GB
        size = int((memSize - 2 * _1_G) * 0.1 + 128 * _1_M)
        max_size = int(_1_G)
        return size if size < max_size else max_size
    else:
        # 大于16GB，为1GB + （nGB - 16GB） * 0.05。最大到约2GB
        size = int((memSize - 16 * _1_G) * 0.05 + _1_G)
        max_size = int((2 * _1_G) - _1_M)
        return size if size < max_size else max_size


def get_tcp_kernel_service_cdp_timeouts():
    return box_service.communicator.getProperties().getPropertyAsInt(r'Logic.TcpKernelService.CDP.Timeouts')


def get_cdp_rotating_file_interval():
    return box_service.communicator.getProperties().getPropertyAsInt(r'Logic.TcpKernelService.CDP.RotatingFileInterval')


def get_space_collection_interval():
    return box_service.communicator.getProperties().getPropertyAsInt(r'Logic.SpaceCollectionInterval')


def get_delete_temp_kvm_iso_switch():
    return box_service.communicator.getProperties().getPropertyAsInt(r'Logic.DeleteTempKvmIso') > 0


def get_compression_switch():
    return box_service.communicator.getProperties().getPropertyAsInt(r'Logic.CompressionEnable') > 0


def get_compression_algorithm():
    return box_service.communicator.getProperties().getPropertyAsInt(r'Logic.CompressionAlgorithm')


def get_compression_rank():
    return box_service.communicator.getProperties().getPropertyAsInt(r'Logic.CompressionRank')


def get_compression_measure_busy_value():
    return box_service.communicator.getProperties().getPropertyAsInt(r'Logic.CompressionMeasureBusy')


def get_verify_user_fingerprint_switch():
    return box_service.communicator.getProperties().getPropertyAsInt(r'Logic.VerifyUserFingerprint') > 0


def get_pe_host_filter_with_user_switch():
    return box_service.communicator.getProperties().getPropertyAsInt(r'Logic.PeHostFilterWithUser') > 0


def get_always_allocate_user_switch():
    return box_service.communicator.getProperties().getPropertyAsInt(r'Logic.AlwaysAllocateUser') > 0


def get_disable_alter_user_switch():
    return box_service.communicator.getProperties().getPropertyAsInt(r'Logic.DisableAlterUser') > 0


def get_default_network_transmission_type():
    return box_service.communicator.getProperties().getPropertyAsInt(r'Logic.DefaultNetworkTransmissionType')


def get_retry_schedule_style():
    return box_service.communicator.getProperties().getPropertyAsInt(r'Logic.RetryScheduleStyle') > -1


def get_calc_next_run_version():
    return box_service.communicator.getProperties().getPropertyAsInt(r'Logic.CalcNextRunVersion')
