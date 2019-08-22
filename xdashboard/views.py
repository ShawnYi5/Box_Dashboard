# coding=utf-8
import ctypes
import datetime
import html
import json
import time
import os
import traceback

import django.utils.timezone as timezone
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.http import HttpResponseRedirect
from django.shortcuts import render_to_response, render
from rest_framework import status
from django.contrib.sessions.models import Session
from apiv1.models import Host, HostLog
from box_dashboard import xlogging, xdata
from web_guard.handle import router as web_guard_router
from web_guard.models import ModifyEntry
from web_guard.views import ModifyEntryTasks
from xdashboard.common.dict import GetDictionary
from xdashboard.common.functional import hasFunctional
from xdashboard.common.license import is_functional_visible, is_functional_available
from xdashboard.handle import clustermgr
from xdashboard.handle import debug
from xdashboard.handle import ntp, tunnel
from xdashboard.handle.authorize import authorize_init
from xdashboard.handle.bussinessreport import bussinessreport_handle
from xdashboard.handle.hotbackup import router as hotbackup_router
from xdashboard.handle.takeover import router as takeover_router
from xdashboard.handle.filebrowser import router as filebrowser_router
from xdashboard.models import DataDictionary, OperationLog, UserProfile
from .common.syspower import CSyspower
from .handle import backup
from .handle import browsepoint
from .handle.backupmgr import backupmgr_handler
from .handle.deploy import deploy_handler
from .handle.download import download_handler
from .handle.home import home_handler
from .handle.logserver import logserver_handler
from .handle.logsystem import logsystem_handler
from .handle.migrate import migrate_handler
from .handle.restore import restore_handler
from .handle.serversmgr import serversmgr_handler
from .handle.storage import storage_handler
from .handle.systemset import gethandler as systemset_gethandler
from .handle.systemset import is_aio_sys_vt_valid, is_aio_sys_has_auxiliary_sys
from .handle.user import user_handler, login as user_login, logout as user_logout, forgetPwd as user_forgetPwd, \
    forget_handler
from .handle.version import getAIOVersion, version_handler, get_oem_info
from xdashboard.handle.remotebackup import router as remotebackup_router
from xdashboard.handle.mgrvmclient import router as mgrvmclient_router
from xdashboard.handle.mgrvcenter import router as mgrvcenter_router
from xdashboard.handle.vmrestore import router as vmrestore_router
from xdashboard.handle.archive import router as archive_router
from xdashboard.handle.authorize.authorize_init import get_separation_of_the_three_members
from .handle.license import router as license_router
from .handle.linuxcompatible import router as linuxcompatible_router
from .handle.audittask import router as audittask_router
from .handle.autoverifytask import router as autoverifytask_router
from .handle.mgrtemplate import router as mgrtemplate_router
from .handle.dashboard import router as dashboard_router
from .handle.watchpower import router as watchpower_router
from .handle.filesync import router as filesync_router

_logger = xlogging.getLogger(__name__)


def robotsview(request):
    return render_to_response("robots.txt")


def redirect(request):
    # 免登录重定向
    try:
        import uuid
        from xdashboard.common.dict import GetTmpDictionary
        from xdashboard.models import TmpDictionary
        from django.contrib.auth.models import User
        from django.contrib import auth
        page = request.GET.get('page')
        key = request.GET.get('key')
        value = request.GET.get('value')
        ret = GetTmpDictionary(TmpDictionary.TMP_DICT_TYPE_REDIRECT, key)
        TmpDictionary.objects.filter(dictType=TmpDictionary.TMP_DICT_TYPE_REDIRECT, dictKey=key).delete()

        if ret["r"] != 0:
            return HttpResponse('链接无效!')
        if ret["value"] != value:
            return HttpResponse('链接失效!如发送了多封邮件，请使用最后一个的链接')
        t = time.mktime(time.strptime(str(ret["expireTime"]), "%Y-%m-%d %H:%M:%S.%f")) - time.time()
        if t < 0:
            return HttpResponse('已过期，请联系管理员!')

        user = User.objects.filter(id=key)
        if user:
            user = user.first()
            if user.is_active:
                user.backend = 'django.contrib.auth.backends.ModelBackend'
                auth.login(request, user)
        return HttpResponseRedirect('/xdashboard/{}/'.format(page))
    except Exception as e:
        return HttpResponse('<pre>{}</pre>'.format(traceback.format_exc()),
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def indexview(request):
    # 重定向
    try:
        if not request.user.is_authenticated():
            return HttpResponseRedirect('/xdashboard/login/')
        if request.user.is_superuser:
            return HttpResponseRedirect('/xdashboard/admin/')
        user_type = request.user.userprofile.user_type
        if user_type == 'audit-admin':
            return HttpResponseRedirect('/xdashboard/audittask/')
        return HttpResponseRedirect('/xdashboard/home/')
    except Exception as e:
        return HttpResponse('<pre>{}</pre>'.format(traceback.format_exc()),
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def handleview(request, act):
    if act == 'home':
        return home_handler(request)
    if act == 'backupmgr':
        return backupmgr_handler(request)
    if act == 'backup':
        return backup.backup_handler(request)
    if act == 'archive':
        return archive_router.start_response(request)
    if act == 'restore':
        return restore_handler(request)
    if act == 'migrate':
        return migrate_handler(request)
    if act == 'deploy':
        return deploy_handler(request)
    if act == 'browsepoint':
        return browsepoint.browsePoint(request)
    if act == 'storage':
        return storage_handler(request)
    if act == 'user':
        return user_handler(request)
    if act == 'syssetget':
        return systemset_gethandler(request)
    if act == 'serversmgr':
        return serversmgr_handler(request)
    if act == 'logserver':
        return logserver_handler(request)
    if act == 'logsystem':
        return logsystem_handler(request)
    if act == 'version':
        return version_handler(request)
    if act == 'download':
        return download_handler(request)
    if act == 'hostmacsidents':
        return host_macs_and_idents(request)
    if act == 'bussinessreport':
        return bussinessreport_handle(request)
    if act == 'initntpip':
        ntp_ip = request.GET['ntpip']
        ntp.modify_ntp_server_ips_conf(ntp_ip, is_init=True)
        return HttpResponse({"r": "1", "e": "0"})
    if act == 'tunnelmanage':
        return tunnel.tunnelmanage_handle(request)
    if act == 'debuginfo':
        return debug.debuginfo_handle(request)
    if act == 'webguard':
        return web_guard_router.start_response(request)
    if act == 'cluster':
        return clustermgr.router.start_response(request)
    if act == 'hotbackup':
        return hotbackup_router.start_response(request)
    if act == 'takeover':
        return takeover_router.start_response(request)
    if act == 'remotebackup':
        return remotebackup_router.start_response(request)
    if act == 'filebrowser':
        return filebrowser_router.start_response(request)
    if act == 'mgrvmclient':
        return mgrvmclient_router.start_response(request)
    if act == 'mgrvcenter':
        return mgrvcenter_router.start_response(request)
    if act == 'vmrestore':
        return vmrestore_router.start_response(request)
    if act == 'license':
        return license_router.start_response(request)
    if act == 'linuxcompatible':
        return linuxcompatible_router.start_response(request)
    if act == 'audittask':
        return audittask_router.start_response(request)
    if act == 'autoverifytask':
        return autoverifytask_router.start_response(request)
    if act == 'mgrtemplate':
        return mgrtemplate_router.start_response(request)
    if act == 'dashboard':
        return dashboard_router.start_response(request)
    if act == 'watchpower':
        return watchpower_router.start_response(request)
    if act == 'filesync':
        return filesync_router.start_response(request)

    return HttpResponse(json.dumps({"r": "1", "e": "没有对应的处理函数:{}".format(html.escape(act))}))


def host_macs_and_idents(request):
    hosts = Host.objects.filter(user=request.user)
    macs_idents = dict()
    for host in hosts:
        sys_info = json.loads(host.ext_info)
        macs_idents[xdata.standardize_mac_addr(sys_info['system_infos']['Nic'][0]['Mac'])] = host.ident

    return HttpResponse(content=json.dumps(macs_idents, ensure_ascii=False), status=status.HTTP_200_OK)


def isSessionTimeout(request):
    try:
        if request.user.is_authenticated():
            if 'lastRequest' in request.session:
                elapsedTime = time.time() - request.session['lastRequest']
                if 'myexpiry' in request.session:
                    Minute = int(request.session['myexpiry'])
                else:
                    request.session['myexpiry'] = int(GetDictionary(DataDictionary.DICT_TYPE_EXPIRY, 'expiry', 120))
                    Minute = int(request.session['myexpiry'])
                if Minute != 0 and elapsedTime > Minute * 60:
                    del request.session['lastRequest']
                    return True
            request.session['lastRequest'] = time.time()
    except Exception as e:
        _logger.info(str(e))
    return False


def isHaveModules(offset1, modules, user_type):
    if offset1 in ('home', 'decode',):
        return True
    elif offset1 == 'webhome' and is_functional_visible('webguard'):
        if modules & 65536 * 32:
            return True
    elif offset1 in ('createbackup', 'mgrbackup', 'taskpolicy', 'clusterbackup'):
        if offset1 == 'clusterbackup' and (not is_functional_visible('clusterbackup') or not modules & 65536 * 2048):
            return False
        if modules & 2:
            return True
    elif offset1 in ('restore', 'validate', 'takeover',):
        if modules & 4:
            return True
    elif offset1 == 'mgrtemplate':
        if modules & 8:
            return True
    elif offset1 == 'migrate':
        if modules & 32 and not hasFunctional('nomigrate_UI'):
            return True
    elif offset1 == 'destroy':
        if modules & 64:
            return True
    elif offset1 in ('serversmgr',):
        if modules & 128:
            return True
    elif offset1 == 'sysset':
        if modules & 256:
            return True
    elif offset1 == 'serverlog':
        if modules & 512 or modules & 1024 or modules & 2048 or modules & 4096:
            return True
    elif offset1 == 'operationlog':
        if modules & 8192 or modules & 16384 or modules & 32768 or modules & 65536:
            return True
    elif offset1 == 'safetyreport':
        if modules & 65536 * 2:
            return True
    elif offset1 == 'storagestatus':
        if modules & 65536 * 4:
            return True
    elif offset1 == 'devicestatus':
        if modules & 65536 * 8:
            return True
    elif offset1 == 'tunnel':
        if modules & 65536 * 16:
            return True
    elif offset1 in ('webstrategies', 'webguardset', 'webautoplans', 'webemergency', 'switchemergency',):
        if modules & 65536 * 32:
            return True
    elif offset1 in ('createhotbackup', 'mgrhotbackup', 'masterstandbyswitching',):
        if modules & 65536 * 64:
            return True
    elif offset1 in ('backupdataexport', 'backupdataimport', 'backupfileexport'):
        if modules & 65536 * 4096:
            return True
    elif offset1 == 'emaillog':
        if modules & 65536 * 1024:
            return True
    elif offset1 in ('remotebackup', 'mgrrebackup',):
        return True
    elif offset1 in ('mgrvmclient', 'mgrvcenter',) and is_functional_visible('vmwarebackup'):
        if modules & 65536 * 512:
            return True
    elif offset1 in ('mgrnasbackup',) and is_functional_visible('nas_backup'):
        if modules & 65536 * 8192:
            return True
    elif offset1 in ('autoverifytask', 'verifytaskreport', 'userverifyscript',):
        if modules & 65536 * 16384:
            return True
    elif offset1 == 'dashboard':
        if hasFunctional('clw_desktop_aio') and user_type == UserProfile.NORMAL_USER:
            return True
    return False


def isAdminHaveModules(offset1):
    if offset1 in (
            'admin', 'adapter', 'smtp', 'storagemgr', 'quotamgr', 'update', 'serversadminmgr', 'license', 'system',
            'setweixin', 'operationlog', 'updatelog', 'adminstoragestatus', 'devicestatus', 'tunnel', 'debug',
            'linuxcompatible', 'linuxinternalcompatible',):
        return True
    if offset1 in ('offlinestorage',) and hasFunctional('offlineStorage'):
        return True
    if offset1 in ('user',) and not hasFunctional('nouser_mgr'):
        return True
    if offset1 in ('volumepool', 'libraryinfo', 'tapasmgr',) and (is_functional_visible('importExport')):
        return True
    return False


def isAudAdminHaveModules(offset1):
    if offset1 in ('operationlog', 'user',):
        return True
    return False


def isAuditAdminHaveModules(offset1):
    if offset1 in ('audittask', 'approved',):
        return True
    return False


def add_recently_login_status(request, adict):
    logs_list = []
    user_login_logs = OperationLog.objects.filter(user=request.user, event=OperationLog.TYPE_USER).order_by('-datetime')
    for log in user_login_logs:
        log_desc_dict = json.loads(log.desc)
        values = log_desc_dict.values()
        if '用户登录' in values or '密码错误，登录失败' in values:
            logs_list.append(log)
    if len(logs_list) >= 2:
        target_log = logs_list[1]
        target_desc_dict = json.loads(target_log.desc)
        adict['r_detail'] = '登录成功' if '用户登录' in target_desc_dict.values() else _get_label(logs_list[2:])
        adict['r_ip'] = target_desc_dict['IP']
        adict['r_datetime'] = target_log.datetime
    else:
        adict['r_detail'] = '--'
        adict['r_ip'] = '--'
        adict['r_datetime'] = '--'


def _get_label(log):
    count = 1
    for item in log:
        item_desc_dict = json.loads(item.desc)
        if '用户登录' in item_desc_dict.values():
            break
        else:
            count += 1
    return '登录失败（{}）'.format(count)


def get_pwd_expire(request, adict):
    pwdcycle = int(GetDictionary(DataDictionary.DICT_TYPE_PWD_CYCLE, "pwdcycle", '0'))
    if pwdcycle == 0:
        adict['exp_days'] = '永不过期'
        return None
    pwdexpriy = GetDictionary(DataDictionary.DICT_TYPE_PWD_EXPIRY, request.user.id, 'none')
    if pwdexpriy == 'none':
        adict['exp_days'] = '请重新登录'
        return None
    else:
        t = (datetime.datetime.strptime(str(pwdexpriy), "%Y-%m-%d %H:%M:%S") - timezone.now()).days
        adict['exp_days'] = str(t + 1) + '天'
        return None


# 开启(继续)一个修改任务
def start_one_modify_task(request, entry_id):
    entry = ModifyEntry.objects.get(id=entry_id)
    result = ModifyEntryTasks().post(entry_id).data
    expire_datetime, task_uuid = result['expire_datetime'], result['task_uuid']
    remain_secs = (datetime.datetime.strptime(expire_datetime, '%Y-%m-%dT%H:%M:%S.%f') - timezone.now()).total_seconds()
    data = {
        'entry': entry.entrance,
        'admin': request.user.username,
        'remain': remain_secs,
        'task_uuid': task_uuid
    }
    return render(request, 'webcontentadminhome.html', data)


@login_required
def needloginview(request, offset1, offset2, param):
    "通用页面"
    adict = dict()
    set_response_context(request, offset1, offset2, adict)
    adict['title'] = param['title']
    adict['company'] = param['company']
    adict['right_body'] = offset1 + '_body'
    adict['is_superuser'] = request.user.is_superuser
    if request.user.is_superuser:
        user_type = 'sec-admin'
    else:
        user_type = request.user.userprofile.user_type
    adict['user_type'] = user_type
    if adict['user_type'] == 'sec-admin':
        if get_separation_of_the_three_members().is_separation_of_the_three_members_available():
            adict['user_type_name'] = '安全保密管理员'
        elif hasFunctional('clw_desktop_aio'):
            adict['user_type_name'] = '系统管理员'
        else:
            adict['user_type_name'] = '超级管理员'
    elif adict['user_type'] == 'normal-admin':
        if hasFunctional('clw_desktop_aio'):
            adict['user_type_name'] = '业务管理员'
        else:
            adict['user_type_name'] = '系统管理员'
    elif adict['user_type'] == 'aud-admin':
        adict['user_type_name'] = '安全审计管理员'
    elif adict['user_type'] == 'audit-admin':
        adict['user_type_name'] = '验证/恢复审批管理员'
    else:
        adict['user_type_name'] = 'unknown({})'.format(adict['user_type'])

    url = offset1 + offset2 + '.html'
    if not request.user.is_active or isSessionTimeout(request):
        return user_logout(request, adict)
    else:
        request.session.set_expiry(0)

    if offset2 != '_handle' and request.user.is_active and hasattr(request.user, 'userprofile'):
        if request.user.userprofile.user_type == UserProfile.CONTENT_ADMIN:  # 拦截: 内容管理员, 且访问非_handle
            if 'modify_entry' in request.GET:  # 用户选择指定的"入口地址"
                entry_id = request.GET['modify_entry']
                return start_one_modify_task(request, entry_id)
            else:  # 获取用户可用的"入口地址"
                entries = request.user.modify_entries.all()
                admin = request.user.username
                return render(request, 'webcontentmodifyentries.html', {'entries': entries, 'admin': admin})

    if offset2 == '_body':
        if offset1 == 'home':
            if user_type == 'audit-admin':
                url = 'audittask_body.html'
        no_license = '（未授权）'
        adict['now'] = timezone.now()
        if offset1 in ('createbackup', 'mgrbackup', 'taskpolicy', 'clusterbackup'):
            adict['remove_duplicates_in_system_folder_available'] = is_functional_available(
                'remove_duplicates_in_system_folder')
        if offset1 in ('createbackup', 'mgrbackup', 'taskpolicy', 'restore'):
            adict['backupobj'] = hasFunctional('backupobj')
            adict['page'] = offset1
        if offset1 in ('validate', 'restore', 'takeover'):
            if not is_functional_available('takeover'):
                adict['takeover_license'] = no_license
            adict['smbencrypt'] = hasFunctional('smbencrypt')
            adict['temporary_takeover_visible'] = is_functional_visible('temporary_takeover')
            if request.user.userprofile.modules & 65536 * 128 and is_functional_visible('takeover'):
                adict['takeover'] = True
            else:
                adict['takeover'] = False
            if request.user.userprofile.modules & 8:
                adict['createtemplate'] = True
            else:
                adict['createtemplate'] = False

        if offset1 in ('migrate', 'restore', 'validate',):
            adict['no_fast_boot'] = hasFunctional('no_fast_boot')
            adict['no_host_validate'] = hasFunctional('clw_desktop_aio')
        if offset1 == 'validate':
            adict['page'] = 'validate'
            url = 'restore' + offset2 + '.html'
        if offset1 == 'browsepoint':
            adict['pointid'] = request.GET.get('pointid', 'none')
        if offset1 == 'operationlog' or offset1 == 'serverlog':
            adict['search'] = 1
            adict['export'] = 1
            adict['del'] = 1
            adict['delall'] = 1
            adict['log_entries'] = list(HostLog.LOG_TYPE_CHOICES)
        if offset1 == 'operationlog' and not request.user.is_superuser:
            adict['search'] = 0
            adict['export'] = 0
            adict['del'] = 0
            adict['delall'] = 0
            if request.user.userprofile.modules & 8192:
                adict['search'] = 1
            if request.user.userprofile.modules & 16384:
                adict['export'] = 1
            if request.user.userprofile.modules & 32768:
                adict['del'] = 1
            if request.user.userprofile.modules & 65536:
                adict['delall'] = 1
        if offset1 == 'serverlog' and not request.user.is_superuser:
            adict['search'] = 0
            adict['export'] = 0
            adict['del'] = 0
            adict['delall'] = 0
            adict['tasktype'] = request.GET.get('tasktype', default='')
            adict['taskid'] = request.GET.get('taskid', default='')
            adict['servername'] = request.GET.get('servername', default='')
            adict['stime'] = request.GET.get('stime', default='')
            if request.user.userprofile.modules & 512:
                adict['search'] = 1
            if request.user.userprofile.modules & 1024:
                adict['export'] = 1
            if request.user.userprofile.modules & 2048:
                adict['del'] = 1
            if request.user.userprofile.modules & 4096:
                adict['delall'] = 1
        if offset1 == 'operationlog' and not request.user.is_superuser and request.user.userprofile.user_type == UserProfile.AUD_ADMIN:
            adict['search'] = 1
            adict['export'] = 1
        if offset1 == 'operationlog':
            if get_separation_of_the_three_members().is_separation_of_the_three_members_available():
                adict['separation_of_the_three_members'] = 1
            if request.user.is_superuser:
                adict['user_type'] = 'sec-admin'
            else:
                adict['user_type'] = request.user.userprofile.user_type
        if offset1 in ('operationlog', 'updatelog', 'serverlog',):
            # 覆盖上面的设置
            if get_separation_of_the_three_members().is_cannot_del_log():
                adict['del'] = 0
                adict['delall'] = 0
        if offset1 in ('remotebackup', 'mgrrebackup',):
            adict['remotebackup_license'] = ''
            if not is_functional_available('remotebackup'):
                adict['remotebackup_license'] = no_license
        if offset1 in ('createhotbackup', 'mgrhotbackup',):
            adict['hotBackup_license'] = ''
            if not is_functional_available('hotBackup'):
                adict['hotBackup_license'] = no_license
        if offset1 in ('clusterbackup',):
            adict['clusterbackup_license'] = ''
            if not is_functional_available('clusterbackup'):
                adict['clusterbackup_license'] = no_license
        if offset1 in ('serversadminmgr', 'serversmgr',):
            adict['vmwarebackup_visible'] = is_functional_visible('vmwarebackup')
            if offset1 == 'serversmgr':
                if not request.user.userprofile.modules & 65536 * 512:
                    adict['vmwarebackup_visible'] = False
        if offset1 in ('license',):
            from xdashboard.handle.authorize.authorize_init import AIO_NET_LIC_FLAG_FILE
            if os.path.isfile(AIO_NET_LIC_FLAG_FILE):
                adict['license_type'] = 'www'
                adict['license_name'] = '互联网授权'
            elif os.path.isfile(r'/usr/lib64/test_v2.so'):
                adict['license_type'] = 'tdog'
                adict['license_name'] = '加密狗授权'
            else:
                adict['license_type'] = 'aio'
                adict['license_name'] = ''
        if request.user.is_superuser:
            if isAdminHaveModules(offset1):
                return render_to_response(url, adict)
        elif request.user.userprofile.user_type == UserProfile.AUD_ADMIN:
            if isAudAdminHaveModules(offset1):
                return render_to_response(url, adict)
        elif request.user.userprofile.user_type == UserProfile.AUDIT_ADMIN:
            if isAuditAdminHaveModules(offset1):
                return render_to_response(url, adict)
        elif type(request.user.userprofile.modules) == type(1):
            if isHaveModules(offset1, request.user.userprofile.modules, request.user.userprofile.user_type):
                return render_to_response(url, adict)
        return HttpResponse('<div class="right">没有权限</div>')

    if offset2 == '_handle':
        result = handleview(request, offset1)
        result['Cache-Control'] = 'no-cache'
        return result

    if offset1 == 'novnc':
        adict['id'] = request.GET['id']
        return render_to_response("novnc.html", adict)

    if offset1 == 'vnclite':
        return render_to_response("vnc_lite.html", adict)

    aList = list()
    syspower = CSyspower()
    aList = syspower.getSyspowerList(request.user)
    adict['menulist'] = aList
    if 'aioversion' in request.session:
        adict['version'] = request.session['aioversion']
    else:
        request.session['aioversion'] = getAIOVersion()
        adict['version'] = request.session['aioversion']
    adict['now'] = timezone.now()
    adict['nolinux'] = hasFunctional('nolinux')
    add_recently_login_status(request, adict)
    get_pwd_expire(request, adict)
    adict['loginusername'] = request.user.username
    adict['loginrealname'] = request.user.first_name
    if os.path.isfile(r'/var/www/static/OEM/top_bg1.png'):
        adict['top_bg1'] = r'/static/OEM/top_bg1.png'
    else:
        adict['top_bg1'] = r'/static/images/top_bg1.png'
    if os.path.isfile(r'/var/www/static/OEM/top_bg2.png'):
        adict['top_bg2'] = r'/static/OEM/top_bg2.png'
    else:
        adict['top_bg2'] = r'/static/images/top_bg2.png'
    if os.path.isfile(r'/var/www/static/OEM/top_bg3.png'):
        adict['top_bg3'] = r'/static/OEM/top_bg3.png'
    else:
        adict['top_bg3'] = r'/static/images/top_bg3.png'

    adict['PREFIX_DR_CLIENT'] = xdata.PREFIX_DR_CLIENT
    oem_info = json.loads(request.session['oem'])
    return render_to_response(oem_info['base_html'], adict)


def have_tdog_modle():
    try:
        # 在/usr/lib64放入libtdog.so.0和test_v2.so
        ctypes.CDLL('test_v2.so')
    except Exception as e:
        return False

    return True


def set_response_context(request, offset1, offset2, context):
    if offset1 == 'system':
        context['show_take_over_tab'] = is_functional_visible('takeover')
        context['show_auxiliary_tab'] = is_aio_sys_has_auxiliary_sys()
        context['show_vt_alert_msg'] = (not is_aio_sys_has_auxiliary_sys()) and (not is_aio_sys_vt_valid())
        context['strict_password_policy'] = get_separation_of_the_three_members().is_strict_password_policy()
        context['database_use_policy'] = get_separation_of_the_three_members().is_database_use_policy()


def commonview(request, offset1, offset2):
    "通用页面"
    try:
        adict = dict()
        if 'oem' in request.session:
            oem_info = json.loads(request.session['oem'])
            adict['title'] = oem_info['title']
            adict['company'] = oem_info['company']
        else:
            oem_info = get_oem_info()
            request.session['oem'] = json.dumps(oem_info, ensure_ascii=False)
            adict['title'] = oem_info['title']
            adict['company'] = oem_info['company']
        if offset1 == 'watchpower' and offset2 == '_handle':
            result = handleview(request, 'watchpower')
            result['Cache-Control'] = 'no-cache'
            return result
        if os.path.isfile(r'/run/watchpower_check_process.json'):
            return render_to_response('watchpower_check_process.html', adict)
        if offset1 == 'login':
            if os.path.isfile(r'/var/www/static/OEM/login.jpg'):
                adict['loginbgimg'] = r'/static/OEM/login.jpg'
            else:
                adict['loginbgimg'] = r'/static/images/login/login.jpg'
            return user_login(request, adict)
        if offset1 == 'logout':
            return user_logout(request, adict)
        if offset1 == 'forget' and offset2 == '_handle':
            return user_forgetPwd(request)
        if offset1 == 'resetpwd' and offset2 == '_html':
            return forget_handler(request, adict)
        if offset1 == 'faq':
            return render_to_response('faq.html', adict)

        # 出厂授权处理
        if offset1 == 'gethtml':  # 1.普通版本, 返回和"授权服务器"交互的UI, 以执行初始授权  (前提: AIO可访问内网授权服务器)
            if have_tdog_modle():  # 2.uKey版本, 返回"上传授权文件"的UI, 以执行初始授权
                return render_to_response('license.html', adict)  # 3.插入加密狗, 界面执行登录流程, SN及私钥会拷贝AIO
            return render_to_response('factory_authorize.html')  # 4.若uKey版本, 到此分支, 则加密狗处于正常状态
        if offset1 == 'getmodnames':
            return authorize_init.get_avail_aio_models_name_from_plis(request)
        if offset1 == 'exeinitauthorize':
            return authorize_init.run_factory_authorize(request)
        # 上传授权文件
        if offset1 == 'uploadfile':
            return authorize_init.update_authorize(request)
        # 查询本地当前授权
        if offset1 == 'querycurauthorize':
            return authorize_init.query_current_authorize(request)
        # 检查试用版
        if offset1 == 'evaluation':
            return authorize_init.base_ui_query_current_authorize()

        return needloginview(request, offset1, offset2, adict)
    except Exception as e:
        _logger.error('commonview error :{}'.format(e), exc_info=True)
        return HttpResponse('<pre>{}</pre>'.format(traceback.format_exc()),
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def get_fun_htmls(request):
    return render(request, 'fun_htmls.html')


def django_sessions(request):
    ret_json = {'r': 0, 'e': 'ok'}
    query_params = request.GET or request.POST
    action = query_params.get('action', None)
    if action is None:
        ret_json['r'], ret_json['e'] = 1, '请求参数缺少: action'
        return HttpResponse(json.dumps(ret_json, ensure_ascii=False))

    if action == 'check_expired':
        session = Session.objects.get(pk=query_params['session_key'])
        ret_json['r'] = 0
        ret_json['is_expired'] = session.expire_date <= datetime.datetime.now()
    else:
        ret_json['r'], ret_json['e'] = 1, '未知参数action: {}'.format(action)

    return HttpResponse(json.dumps(ret_json, ensure_ascii=False))
