from .license import is_functional_visible
from .functional import hasFunctional
from xdashboard.models import UserProfile
from xdashboard.handle.authorize.authorize_init import get_separation_of_the_three_members


# 1-系统状态
# 2-备份
# 4-恢复
# 8-部署
# 16-迁移
# 32-销毁
# 64-日志管理
class CSyspower(object):
    "菜单项和该菜单允许被显示给哪些用户"

    def getSyspowerList(self, user):
        aList = list()
        userType = self.getUserType(user)
        id = 0

        if userType == 4:
            subList = list()
            subList.append({'page_name': '../audittask', 'page_title': '审批任务', 'userType': [userType]})
            subList.append({'page_name': '../approved', 'page_title': '已审批的任务', 'userType': [userType]})
            id = id + 1
            aList.append({'CATEGORY': '审批管理', 'subList': subList, 'id': id, 'icon': 'audit'})

        # 搜索关键字"65536 * 128"
        # 若有新增导航栏,则5处修改：后台4，前端1
        if hasFunctional('clw_desktop_aio') and userType == 2:
            subList = list()
            subList.append({'page_name': '../home', 'page_title': '任务执行状态', 'userType': [userType]})
            subList.append({'page_name': '../dashboard', 'page_title': '仪表盘', 'userType': [userType]})
            id = id + 1
            aList.append({'CATEGORY': '系统状态', 'subList': subList, 'id': id, 'icon': 'status'})

        if is_functional_visible('webguard'):
            subList = list()
            if self.hasComponents(user, 65536 * 32):
                subList.append({'page_name': '../webhome', 'page_title': '网站防护状态', 'userType': [userType]})
                subList.append({'page_name': '../webstrategies', 'page_title': '监控目标管理', 'userType': [userType]})
                subList.append({'page_name': '../webautoplans', 'page_title': '应急策略管理', 'userType': [userType]})
                subList.append({'page_name': '../switchemergency', 'page_title': '主页应急切换', 'userType': [userType]})
                subList.append({'page_name': '../webguardset', 'page_title': '网站防护设置', 'userType': [userType]})
                id = id + 1
                aList.append({'CATEGORY': '网站防护', 'subList': subList, 'id': id, 'icon': 'guard'})
        if is_functional_visible('hotBackup'):
            subList = list()
            if self.hasComponents(user, 65536 * 64):
                subList.append({'page_name': '../createhotbackup', 'page_title': '新建热备计划', 'userType': [userType]})
                subList.append({'page_name': '../mgrhotbackup', 'page_title': '热备计划管理', 'userType': [userType]})
                subList.append({'page_name': '../masterstandbyswitching', 'page_title': '主备切换', 'userType': [userType]})
                id = id + 1
                aList.append({'CATEGORY': '热备', 'subList': subList, 'id': id, 'icon': 'hotbackup'})
        subList = list()
        if is_functional_visible('importExport'):
            subList = list()
            if self.hasComponents(user, 65536 * 4096):
                subList.append({'page_name': '../backupdataexport', 'page_title': '备份数据导出', 'userType': [userType]})
                subList.append({'page_name': '../backupdataimport', 'page_title': '备份数据导入', 'userType': [userType]})
                subList.append({'page_name': '../backupfileexport', 'page_title': '备份文件导出', 'userType': [userType]})
                id = id + 1
                aList.append({'CATEGORY': '备份数据归档', 'subList': subList, 'id': id, 'icon': 'archive'})
            subList = list()
        if userType == 1:
            subList.append({'page_name': '../adapter', 'page_title': '网络设置', 'userType': [userType]})
            subList.append({'page_name': '../smtp', 'page_title': '消息与通知设置', 'userType': [userType]})
            id = id + 1
            aList.append({'CATEGORY': '网络管理', 'subList': subList, 'id': id, 'icon': 'network'})
        subList = list()
        if not hasFunctional('nouser_mgr') and userType in (1, 3):
            subList.append({'page_name': '../user', 'page_title': '用户管理', 'userType': [userType]})
            id = id + 1
            aList.append({'CATEGORY': '用户管理', 'subList': subList, 'id': id, 'icon': 'user'})
        subList = list()
        if userType == 1:
            subList.append({'page_name': '../storagemgr', 'page_title': '存储单元管理', 'userType': [userType]})
            subList.append({'page_name': '../quotamgr', 'page_title': '存储配额管理', 'userType': [userType]})
            if is_functional_visible('offlineStorage'):
                subList.append({'page_name': '../offlinestorage', 'page_title': '离线存储', 'userType': [userType]})
            id = id + 1
            aList.append({'CATEGORY': '存储管理', 'subList': subList, 'id': id, 'icon': 'storage'})
        subList = list()
        if userType == 1 and is_functional_visible('importExport'):
            subList.append({'page_name': '../libraryinfo', 'page_title': '带库信息浏览', 'userType': [userType]})
            subList.append({'page_name': '../volumepool', 'page_title': '磁带存储卷池', 'userType': [userType]})
            subList.append({'page_name': '../tapasmgr', 'page_title': '存储配额管理', 'userType': [userType]})
            id = id + 1
            aList.append({'CATEGORY': '磁带管理', 'subList': subList, 'id': id, 'icon': 'tap'})
        subList = list()
        if self.hasComponents(user, 2):
            subList.append({'page_name': '../createbackup', 'page_title': '新建备份计划', 'userType': [userType]})
            subList.append({'page_name': '../mgrbackup', 'page_title': '备份计划管理', 'userType': [userType]})
            if self.hasComponents(user, 65536 * 2048) and is_functional_visible('clusterbackup'):
                subList.append({'page_name': '../clusterbackup', 'page_title': '集群备份计划管理', 'userType': [userType]})
            if is_functional_visible('vmwarebackup') and self.hasComponents(user, 65536 * 512):
                subList.append({'page_name': '../mgrvcenter', 'page_title': '数据中心连接管理', 'userType': [userType]})
            subList.append({'page_name': '../taskpolicy', 'page_title': '备份计划策略', 'userType': [userType]})
            id = id + 1
            aList.append({'CATEGORY': '备份', 'subList': subList, 'id': id, 'icon': 'backup'})
        subList = list()
        if is_functional_visible('nas_backup') and self.hasComponents(user, 65536 * 8192):
            subList.append({'page_name': '../mgrnasbackup', 'page_title': 'NAS备份计划管理', 'userType': [userType]})
            aList.append({'CATEGORY': 'NAS备份', 'subList': subList, 'icon': 'nas'})
        subList = list()
        if userType == 2 and is_functional_visible('remotebackup') and self.hasComponents(user, 65536 * 256):
            subList.append({'page_name': '../remotebackup', 'page_title': '新建远程容灾计划', 'userType': [userType]})
            subList.append({'page_name': '../mgrrebackup', 'page_title': '远程容灾计划管理', 'userType': [userType]})
            aList.append({'CATEGORY': '远程容灾', 'subList': subList, 'icon': 'remote'})
        subList = list()
        if self.hasComponents(user, 4):
            subList.append({'page_name': '../restore', 'page_title': '恢复', 'userType': [userType]})
            if not hasFunctional('novalidate'):
                subList.append({'page_name': '../validate', 'page_title': '验证', 'userType': [userType]})
            id = id + 1
            aList.append({'CATEGORY': '恢复', 'subList': subList, 'id': id, 'icon': 'restore'})
        subList = list()
        if self.hasComponents(user, 8):
            subList.append({'page_name': '../mgrtemplate', 'page_title': '模板管理', 'userType': [userType]})
        id = id + 1
        aList.append({'CATEGORY': '桌面模板', 'subList': subList, 'id': id, 'icon': 'template'})
        subList = list()
        if self.hasComponents(user, 32) and not hasFunctional('nomigrate_UI'):
            subList.append({'page_name': '../migrate', 'page_title': '迁移', 'userType': [userType]})
            id = id + 1
            aList.append({'CATEGORY': '迁移', 'subList': subList, 'id': id, 'icon': 'migrate'})
        subList = list()
        if self.hasComponents(user, 4):
            # 有恢复和验证就有接管
            if self.hasComponents(user, 65536 * 128) and is_functional_visible('takeover'):
                subList.append({'page_name': '../takeover', 'page_title': '接管主机', 'userType': [userType]})
                id = id + 1
                aList.append({'CATEGORY': '接管', 'subList': subList, 'id': id, 'icon': 'takeover'})
            elif is_functional_visible('temporary_takeover'):
                subList.append({'page_name': '../takeover', 'page_title': '验证主机', 'userType': [userType]})
                id = id + 1
                aList.append({'CATEGORY': '虚拟机管理', 'subList': subList, 'id': id, 'icon': 'virtual_machine'})
        subList = list()
        if self.hasComponents(user, 64):
            # subList.append({'page_name': '../destroy', 'page_title': '销毁', 'userType': [userType]})
            id = id + 1
            aList.append({'CATEGORY': '销毁', 'subList': subList, 'id': id, 'icon': 'destroy'})
        subList = list()
        if is_functional_visible('auto_verify_task') and self.hasComponents(user, 65536 * 16384):
            subList.append({'page_name': '../autoverifytask', 'page_title': '自动验证计划', 'userType': [userType]})
            subList.append({'page_name': '../userverifyscript', 'page_title': '用户自定义脚本', 'userType': [userType]})
            subList.append({'page_name': '../verifytaskreport', 'page_title': '验证报告', 'userType': [userType]})
            id = id + 1
            aList.append({'CATEGORY': '自动验证', 'subList': subList, 'id': id, 'icon': 'verify'})
        subList = list()
        if userType == 1:
            subList.append({'page_name': '../update', 'page_title': '系统更新', 'userType': [userType]})
            subList.append({'page_name': '../serversadminmgr', 'page_title': '客户端管理', 'userType': [userType]})
        else:
            if self.hasComponents(user, 128):
                subList.append({'page_name': '../serversmgr', 'page_title': '客户端管理', 'userType': [userType]})
            if self.hasComponents(user, 256):
                subList.append({'page_name': '../sysset', 'page_title': '制作启动介质', 'userType': [userType]})
            if self.hasComponents(user, 65536 * 16):
                subList.append({'page_name': '../tunnel', 'page_title': '连接管理', 'userType': [userType]})
        if userType == 1:
            if hasFunctional('nolicense_UI'):
                pass
            else:
                subList.append({'page_name': '../license', 'page_title': '授权管理', 'userType': [userType]})
            if not hasFunctional('nolinux'):
                subList.append(
                    {'page_name': '../linuxinternalcompatible', 'page_title': 'linux内置支持版本', 'userType': [userType]})
                subList.append({'page_name': '../linuxcompatible', 'page_title': 'linux兼容支持版本', 'userType': [userType]})
            subList.append({'page_name': '../system', 'page_title': '系统设置', 'userType': [userType]})
            if is_functional_visible('tunnel_num'):
                subList.append({'page_name': '../tunnel', 'page_title': '连接管理', 'userType': [userType]})
        id = id + 1
        aList.append({'CATEGORY': '设置', 'subList': subList, 'id': id, 'icon': 'config'})
        subList = list()
        if userType == 3:
            subList.append({'page_name': '../operationlog', 'page_title': '操作日志', 'userType': [userType]})
        if userType == 1:
            subList.append({'page_name': '../operationlog', 'page_title': '操作日志', 'userType': [userType]})
            subList.append({'page_name': '../updatelog', 'page_title': '更新日志', 'userType': [userType]})
        if self.hasComponents(user, 512) or self.hasComponents(user, 1024) or self.hasComponents(user,
                                                                                                 2048) or self.hasComponents(
            user, 4096):
            subList.append({'page_name': '../serverlog', 'page_title': '客户端日志', 'userType': [userType]})
        if self.hasComponents(user, 8192) or self.hasComponents(user, 16384) or self.hasComponents(user,
                                                                                                   32768) or self.hasComponents(
            user, 65536):
            if not get_separation_of_the_three_members().is_separation_of_the_three_members_available():
                subList.append({'page_name': '../operationlog', 'page_title': '操作日志', 'userType': [userType]})
        if is_functional_visible('webguard') and self.hasComponents(user, 65536 * 32):
            subList.append({'page_name': '../webemergency', 'page_title': '网页防护日志', 'userType': [userType]})
        if self.hasComponents(user, 65536 * 1024):
            subList.append({'page_name': '../emaillog', 'page_title': '邮件记录', 'userType': [userType]})
        id = id + 1
        aList.append({'CATEGORY': '日志管理', 'subList': subList, 'id': id, 'icon': 'log'})
        subList = list()
        if userType == 2:
            if self.hasComponents(user, 65536 * 2):
                subList.append({'page_name': '../safetyreport', 'page_title': '安全状态报告', 'userType': [userType]})
            if self.hasComponents(user, 65536 * 4):
                subList.append({'page_name': '../storagestatus', 'page_title': '容量状态报告', 'userType': [userType]})
            if self.hasComponents(user, 65536 * 8):
                subList.append({'page_name': '../devicestatus', 'page_title': '设备历史状态报告', 'userType': [userType]})
        elif userType == 1:
            subList.append({'page_name': '../adminstoragestatus', 'page_title': '容量状态报告', 'userType': [userType]})
            subList.append({'page_name': '../devicestatus', 'page_title': '设备历史状态报告', 'userType': [userType]})
        if len(subList):
            id = id + 1
            aList.append({'CATEGORY': '业务报告', 'subList': subList, 'id': id, 'icon': 'report'})

        return aList

    def getUserType(self, user):
        # userType 1:超级管理员 2:系统管理员 3.安全审计管理员 4.验证/恢复管理员
        if user.is_superuser:
            return 1
        elif user.userprofile.user_type == UserProfile.NORMAL_USER:
            return 2
        elif user.userprofile.user_type == UserProfile.AUD_ADMIN:
            return 3
        elif user.userprofile.user_type == UserProfile.AUDIT_ADMIN:
            return 4
        return 2

    def hasComponents(self, user, components):
        if user.is_superuser:
            return False
        try:
            if type(user.userprofile.modules) == type(1):
                if user.userprofile.modules & components:
                    return True
        except Exception as e:
            pass
        return False
