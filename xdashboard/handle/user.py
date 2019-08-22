# coding=utf-8
import base64
import ctypes
import datetime
import html
import json
import os
import random
import subprocess
import threading
import time
import uuid

import django.utils.timezone as timezone
from django.conf import settings
from django.contrib import auth
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.http import HttpResponseRedirect
from django.shortcuts import render_to_response

from apiv1 import tdog
from apiv1.models import Host
from apiv1.views import HostSnapshotShareHostDelete, HostSnapshotShareUserDelete
from apiv1.www_license import wwwlicense
from box_dashboard import xlogging, functions
from box_dashboard.boxService import box_service
from web_guard.content_mgr import UserStatusView
from xdashboard.common.dict import GetTmpDictionary, SaveTmpDictionary, GetDictionary, DelDictionary, SaveDictionary, \
    GetDictionaryByTpye
from xdashboard.common.functional import hasFunctional
from xdashboard.common.license import is_functional_visible, is_functional_available
from xdashboard.common.smtp import send_mail
from xdashboard.handle import enterprise_wei_xin
from xdashboard.handle.authorize import authorize_init
from xdashboard.handle.authorize.authorize_init import AIO_NET_LIC_FLAG_FILE
from xdashboard.handle.authorize.authorize_init import get_separation_of_the_three_members
from xdashboard.handle.logserver import SaveOperationLog
from xdashboard.models import DataDictionary
from xdashboard.models import TmpDictionary
from xdashboard.models import UserProfile, OperationLog
from xdashboard.request_util import get_operator
from .backup import deloneuserpolicy

_logger = xlogging.getLogger(__name__)
is_checked_hard_id = None
has_check_sys_time = None
lock_tdog = threading.Lock()
CIPHERTEXT_FILE = '/etc/aio/password_ciphertext.json'


def IsPwdExpire(user_id):
    if get_separation_of_the_three_members().is_strict_password_policy():
        pwdcycle = int(GetDictionary(DataDictionary.DICT_TYPE_PWD_CYCLE, "pwdcycle", '7'))
    else:
        pwdcycle = int(GetDictionary(DataDictionary.DICT_TYPE_PWD_CYCLE, "pwdcycle", '0'))
    if pwdcycle == 0:
        return False
    pwdexpriy = GetDictionary(DataDictionary.DICT_TYPE_PWD_EXPIRY, user_id, 'none')
    if pwdexpriy == 'none':
        return True
    else:
        t = time.mktime(time.strptime(str(pwdexpriy), "%Y-%m-%d %H:%M:%S")) - time.time()
        if t < 0:
            return True
    return False


def SetPwdExpire(user_id):
    pwdcycle = int(GetDictionary(DataDictionary.DICT_TYPE_PWD_CYCLE, "pwdcycle", '0'))
    if pwdcycle <= 0:
        return
    expiretime = (timezone.now() + datetime.timedelta(hours=pwdcycle * 24)).strftime('%Y-%m-%d %H:%M:%S')
    SaveDictionary(DataDictionary.DICT_TYPE_PWD_EXPIRY, user_id, expiretime)


def loginfailed(request, username):
    remaincount = -1
    Users = User.objects.filter(username=username)
    if not Users:
        return remaincount, 'none'
    tmpuser = Users.first()
    if 'HTTP_X_FORWARDED_FOR' in request.META:
        ip = request.META['HTTP_X_FORWARDED_FOR']
    else:
        ip = request.META['REMOTE_ADDR']
    SaveOperationLog(tmpuser, OperationLog.TYPE_USER, json.dumps({"登录": "密码错误，登录失败", "IP": ip}, ensure_ascii=False),
                     get_operator(request))
    lockedtime = GetDictionary(DataDictionary.DICT_TYPE_LOGIN_FAILED, tmpuser.id, '0|0')
    vec = lockedtime.split('|')
    count = 0
    unlockmin = int(GetDictionary(DataDictionary.DICT_TYPE_LOGIN_LOCK_MIN, 'lock', '30'))
    unlocktime = (timezone.now() + datetime.timedelta(minutes=unlockmin)).strftime('%Y-%m-%d %H:%M:%S')
    if len(vec) == 2:
        count = int(vec[0])
    count += 1
    SaveDictionary(DataDictionary.DICT_TYPE_LOGIN_FAILED, tmpuser.id, str(count) + '|' + unlocktime)
    if get_separation_of_the_three_members().is_strict_password_policy():
        limtcount = '5'
    else:
        limtcount = '10'
    remaincount = int(GetDictionary(DataDictionary.DICT_TYPE_USER_LOGIN_FAILED_COUNT, 'limtcount', limtcount)) - count
    if remaincount < 0:
        remaincount = 0
    return remaincount, unlocktime


def IsUserLockd(username):
    Users = User.objects.filter(username=username)
    if not Users:
        return False
    user_id = Users.first().id
    lockedtime = GetDictionary(DataDictionary.DICT_TYPE_LOGIN_FAILED, str(user_id), '0|0')
    vec = lockedtime.split('|')
    count = 0
    unlocktime = (timezone.now()).strftime('%Y-%m-%d %H:%M:%S')
    if len(vec) == 2:
        count = int(vec[0])
        unlocktime = vec[1]
    if count == 0:
        return False
    if unlocktime == '0':
        return False
    if get_separation_of_the_three_members().is_strict_password_policy():
        limtcount = '5'
    else:
        limtcount = '10'
    if count >= int(GetDictionary(DataDictionary.DICT_TYPE_USER_LOGIN_FAILED_COUNT, 'limtcount', limtcount)):
        t = time.mktime(time.strptime(str(unlocktime), "%Y-%m-%d %H:%M:%S")) - time.time()
        if t > 0:
            return unlocktime
        SaveDictionary(DataDictionary.DICT_TYPE_LOGIN_FAILED, str(user_id), '0|0')
    return False


def is_init_authorize_normal():
    authorize_valid = authorize_init.check_init_authorize()  # 授权OK则不读取加密狗
    if authorize_valid:
        return True, ''

    with lock_tdog:
        tdog_result = tdog.license()
    authorize_valid = authorize_init.check_init_authorize()

    if tdog_result in ['no_test_v2']:  # 1.不支持加密狗的版本
        if not authorize_valid:
            if os.path.isfile(AIO_NET_LIC_FLAG_FILE):
                return True, '互联网授权，可登录'
            return False, '授权异常，请联系管理员或转至<a href="{0}" style="color:blue">授权界面</a>'.format('/xdashboard/gethtml/')
        else:
            return True, ''

    if tdog_result in ['no_sn', 'ok', 'dog_error']:  # 2.支持加密狗的版本, 对应状态：SN文件不存在，私钥已经拷贝至AIO，加密狗设备异常
        if tdog_result == 'dog_error':
            return False, '未检测到授权加密狗，请确认加密狗已插入'

        if not authorize_valid:
            return False, '授权异常，请联系管理员或转至<a href="{0}" style="color:blue">授权界面</a>'.format('/xdashboard/gethtml/')

        return True, ''

    return False, '授权异常，请联系管理员'


def is_aio_key_ok():
    if not os.path.isfile(r'/var/www/static/aio_key.txt'):
        return False
    if not os.path.isfile(r'/home/aio_key.txt'):
        return False
    if not os.path.isfile(r'/var/db/aio_key.txt'):
        return False
    return True


def login(request, adict):
    global has_check_sys_time
    action = request.POST.get('a', '')
    desc = {'操作': '用户登录'}
    if action == 'login':
        try:
            rsp = check_hard_ware_id()
            if not rsp:
                return HttpResponse('{"r":"1","e":"硬件状态异常"}')
        except Exception as e:
            _logger.info('login {}'.format(e), exc_info=True)
        if not has_check_sys_time:
            recently_time = box_service.getTime()
            if recently_time:
                time_str = time.strftime('%Y/%m/%d %H:%M:%S', time.localtime(int(recently_time)))
                msg = '时间错误，请联系管理员修改CMOS时间至{}以后'.format(time_str)
                return HttpResponse(json.dumps({"r": "1", "e": msg}))
        has_check_sys_time = True

        # 硬件,时间无异常: 开始检查授权文件(普通版本, ukey版本)
        is_ok, msg = is_init_authorize_normal()
        if not is_ok:
            return HttpResponse(json.dumps({"r": "1", "e": msg}, ensure_ascii=False))

        username = request.POST.get('u', 'user')
        password = request.POST.get('p', 'pwd')
        password = base64.b64decode(password).decode('utf-8')
        unlocktime = IsUserLockd(username)
        if unlocktime:
            desc['结果'] = "已锁定，将于{}解锁".format(unlocktime)
            users = User.objects.filter(username=username)
            if users:
                user = users.first()
                SaveOperationLog(user, OperationLog.TYPE_USER, json.dumps(desc, ensure_ascii=False),
                                 get_operator(request))
            return HttpResponse(json.dumps({"r": "1", "e": "已锁定，将于{}解锁".format(unlocktime)}, ensure_ascii=False))
        user = auth.authenticate(username=username, password=password)
        if user and user.is_active:
            auth.login(request, user)
            UserStatusView.login(user)
            super = '0'
            if user.is_superuser:
                super = '1'
            binitpwd = '0'
            bexpirepwd = '0'
            pwdcycle = 'none'
            if password == '123456':
                binitpwd = '1'
            if user.is_superuser and not is_aio_key_ok():
                binitpwd = '1'
            if IsPwdExpire(user.id):
                bexpirepwd = '1'
                if get_separation_of_the_three_members().is_strict_password_policy():
                    pwdcycle = GetDictionary(DataDictionary.DICT_TYPE_PWD_CYCLE, 'pwdcycle', '7')
                else:
                    pwdcycle = GetDictionary(DataDictionary.DICT_TYPE_PWD_CYCLE, 'pwdcycle', '0')
            if get_separation_of_the_three_members().is_strict_password_policy():
                policy = GetDictionary(DataDictionary.DICT_TYPE_PWD_POLICY, 'policy', '2')
            else:
                policy = GetDictionary(DataDictionary.DICT_TYPE_PWD_POLICY, 'policy', '3')
            if user.is_superuser:
                user_type = 'sec-admin'
            else:
                user_type = user.userprofile.user_type
            if 'HTTP_X_FORWARDED_FOR' in request.META:
                ip = request.META['HTTP_X_FORWARDED_FOR']
            else:
                ip = request.META['REMOTE_ADDR']
            desc["IP"] = ip
            home = 'home'
            if user_type == 'aud-admin':
                home = 'user'
            elif user_type == 'audit-admin':
                home = 'audittask'
            if is_functional_available('webguard'):
                if not user.is_superuser and request.user.userprofile.modules & 65536 * 32:
                    home = 'webhome'
            SaveOperationLog(request.user, OperationLog.TYPE_USER, json.dumps(desc, ensure_ascii=False),
                             get_operator(request))
            SaveDictionary(DataDictionary.DICT_TYPE_LOGIN_FAILED, str(user.id), '0|0')
            return HttpResponse(json.dumps(
                {"r": "0", "is_superuser": super, "e": "ok", "binitpwd": binitpwd, "policy": policy,
                 "bexpirepwd": bexpirepwd, "pwdcycle": pwdcycle, 'home': home, 'user_type': user_type},
                ensure_ascii=False))
        if user and not user.is_active:
            return HttpResponse('{"r":"2","e":"用户已禁用"}')
        ret = loginfailed(request, username)
        if ret[0] > 0:
            return HttpResponse(json.dumps({"r": "1", "e": "密码不正确，还剩余{}次机会".format(ret[0])}, ensure_ascii=False))
        elif ret[0] == 0:
            users = User.objects.filter(username=username)
            if users:
                user = users.first()
                desc['结果'] = "已锁定，将于{}解锁".format(ret[1])
                SaveOperationLog(user, OperationLog.TYPE_USER, json.dumps(desc, ensure_ascii=False),
                                 get_operator(request))
            return HttpResponse(json.dumps({"r": "1", "e": "已锁定，将于{}解锁".format(ret[1])}, ensure_ascii=False))
        return HttpResponse('{"r":"1","e":"用户名或密码不正确"}')

    adict.update(authorize_init.evaluation_expiration_info())
    if hasFunctional('clw_desktop_aio'):
        response = render_to_response("login_desktop.html", adict)
    else:
        response = render_to_response("login.html", adict)
    response.set_cookie(settings.CSRF_COOKIE_NAME, request.META.get("CSRF_COOKIE", time.time()),
                        max_age=60 * 60 * 24 * 7 * 52,
                        domain=settings.CSRF_COOKIE_DOMAIN)
    return response


def logout(request, adict):
    if not isinstance(request.user, User):
        return HttpResponseRedirect('/xdashboard/login/')
    desc = {'操作': '用户登出'}
    if 'HTTP_X_FORWARDED_FOR' in request.META:
        ip = request.META['HTTP_X_FORWARDED_FOR']
    else:
        ip = request.META['REMOTE_ADDR']
    desc["IP"] = ip
    SaveOperationLog(request.user, OperationLog.TYPE_USER, json.dumps(desc, ensure_ascii=False), get_operator(request))
    UserStatusView.logout(request.user)
    auth.logout(request)
    return HttpResponseRedirect('/xdashboard/login/')


def _save_aio_key_to_file(tfile, sstr):
    try:
        file_object = open(tfile, 'w')
        file_object.writelines(sstr)
        file_object.close()
    except Exception as e:
        pass


def _save_aio_key():
    try:
        rsrp = box_service.getPasswd()
        _save_aio_key_to_file('/var/www/static/aio_key.txt', rsrp)
        _save_aio_key_to_file('/home/aio_key.txt', rsrp)
        _save_aio_key_to_file('/var/db/aio_key.txt', rsrp)
    except Exception as e:
        _logger.info('_save_aio_key Exception Failed. {}'.format(e))


def password_write_file():
    if not os.path.exists(CIPHERTEXT_FILE):
        os.system('touch ' + CIPHERTEXT_FILE)
    username_passwd = User.objects.get(username='admin').password
    ciphertext = {'password_ciphertext': username_passwd}
    with open(CIPHERTEXT_FILE, "w") as f:
        json.dump(ciphertext, f)
    _logger.info('username_passwd:{}'.format(username_passwd))


def changepwd(request):
    oldpwd = request.POST.get('oldpwd', '')
    oldpwd = base64.b64decode(oldpwd).decode('utf-8')
    newpwd = request.POST.get('newpwd', '')
    newpwd = base64.b64decode(newpwd).decode('utf-8')
    if (request.user.check_password(oldpwd)):
        if request.user.is_superuser:
            box_service.setPasswd(json.dumps(newpwd))
            _save_aio_key()
        request.user.set_password(newpwd)
        request.user.save()
        user = auth.authenticate(username=request.user.username, password=newpwd)
        if user and user.is_active:
            SetPwdExpire(user.id)
            auth.login(request, user)
        if request.user.is_superuser:
            password_write_file()
        desc = {'操作': '修改密码'}
        SaveOperationLog(request.user, OperationLog.TYPE_USER, json.dumps(desc, ensure_ascii=False),
                         get_operator(request))
        return HttpResponse('{"r":"0","e":"操作成功"}')
    return HttpResponse('{"r":"1","e":"密码不正确"}')


def changepwd2(request):
    newpwd = request.POST.get('newpwd', '')
    newpwd = base64.b64decode(newpwd).decode('utf-8')
    if request.user.is_superuser:
        box_service.setPasswd(json.dumps(newpwd))
        _save_aio_key()
    request.user.set_password(newpwd)
    request.user.save()
    user = auth.authenticate(username=request.user.username, password=newpwd)
    if user and user.is_active:
        SetPwdExpire(user.id)
        auth.login(request, user)
    if request.user.is_superuser:
        password_write_file()
    desc = {'操作': '修改密码'}
    SaveOperationLog(request.user, OperationLog.TYPE_USER, json.dumps(desc, ensure_ascii=False), get_operator(request))
    return HttpResponse('{"r":"0","e":"操作成功"}')


def getInfosByUserObj(itemObject):
    username = itemObject.username
    is_active = '禁用'
    if itemObject.is_active:
        is_active = '启用'
    unlocktime = IsUserLockd(username)
    user_status = '已锁定，将于{}解锁'.format(unlocktime) if unlocktime else '未锁定'
    desc = ''
    wei_xin = ''
    profile = UserProfile.objects.filter(user_id=itemObject.id)
    if profile:
        if type(profile[0].desc) == type(''):
            desc = profile[0].desc
            wei_xin = profile[0].wei_xin
    if itemObject.is_superuser:
        user_type = 'sec-admin'
    else:
        user_type = itemObject.userprofile.user_type
    return [username, wei_xin, is_active, user_status, desc, user_type]


def getlist(request):
    # 默认值
    page = 1
    rows = 30
    if 'page' in request.GET:
        page = int(request.GET['page'])
    if 'rows' in request.GET:
        rows = int(request.GET['rows'])

    Users = User.objects.exclude(is_superuser=True).filter(userprofile__deleted=False)
    if not request.user.is_superuser and request.user.userprofile.user_type == UserProfile.AUD_ADMIN:
        Users = Users.filter(~Q(id=request.user.id))
        Users = Users.filter(userprofile__user_type=UserProfile.AUD_ADMIN)

    if request.user.is_superuser:
        Users = Users.filter(~Q(userprofile__user_type=UserProfile.AUD_ADMIN))

    paginator = Paginator(Users, rows)
    totalPlan = paginator.count
    totalPage = paginator.num_pages

    page = totalPage if page > totalPage else page
    currentUserObjs = paginator.page(page).object_list
    rowList = list()
    for userObj in currentUserObjs:
        # 无防护模块, 且是内容管理员: 返回UI前过滤
        if not is_functional_visible('webguard') and userObj.userprofile.user_type == UserProfile.CONTENT_ADMIN:
            continue

        detailDict = {'id': userObj.id, 'cell': getInfosByUserObj(userObj)}
        rowList.append(detailDict)

    retInfo = {'r': 0, 'a': 'list', 'page': str(page), 'total': totalPage,
               'records': totalPlan, 'rows': rowList}

    functions.sort_gird_rows(request, retInfo)
    jsonStr = json.dumps(retInfo, ensure_ascii=False)

    return HttpResponse(jsonStr)


def FmtModules(modules):
    modulesvec = list()
    if modules & 2:
        modulesvec.append('备份')
    if modules & 4:
        modulesvec.append('恢复')
    if modules & 8:
        modulesvec.append('模板管理')
    if modules & 32:
        modulesvec.append('迁移')
    if modules & 64:
        modulesvec.append('销毁')
    if modules & 128:
        modulesvec.append('客户端管理')
    if modules & 256:
        modulesvec.append('制作启动介质')
    if modules & 512:
        modulesvec.append('客户端日志-查询')
    if modules & 1024:
        modulesvec.append('客户端日志-导出')
    if modules & 2048:
        modulesvec.append('客户端日志-删除')
    if modules & 4096:
        modulesvec.append('客户端日志-全部删除')
    if modules & 8192:
        modulesvec.append('操作日志-查询')
    if modules & 16384:
        modulesvec.append('操作日志-导出')
    if modules & 32768:
        modulesvec.append('操作日志-删除')
    if modules & 65536:
        modulesvec.append('操作日志-全部删除')
    if modules & 65536 * 2:
        modulesvec.append('安全状态报告')
    if modules & 65536 * 4:
        modulesvec.append('容量状态报告')
    if modules & 65536 * 8:
        modulesvec.append('设备历史状态报告')
    if modules & 65536 * 16:
        modulesvec.append('连接管理')
    if modules & 65536 * 32:
        modulesvec.append('网站防护')
    if modules & 65536 * 64:
        modulesvec.append('热备')
    if modules & 65536 * 128:
        modulesvec.append('接管主机')
    if modules & 65536 * 256:
        modulesvec.append('远程容灾')
    if modules & 65536 * 512:
        modulesvec.append('VMware免代理备份')
    if modules & 65536 * 1024:
        modulesvec.append('邮件记录')
    if modules & 65536 * 2048:
        modulesvec.append('集群')
    if modules & 65536 * 4096:
        modulesvec.append('备份数据归档')
    if modules & 65536 * 8192:
        modulesvec.append('NAS备份')
    if modules & 65536 * 16384:
        modulesvec.append('自动验证')
    if modules & 65536 * 32768:
        modulesvec.append('unused')
    return '，'.join(modulesvec)


def getUniqueSambaUsername():
    username = ''.join(random.sample('abcdefghijklmnopqrstuvwxyz', 3))
    onedates = GetDictionaryByTpye(DataDictionary.DICT_TYPE_SAMBA)
    if onedates:
        busernameUnique = False
        while busernameUnique == False:
            busernameUnique = True
            for onedate in onedates:
                tmpvec = onedate.dictValue.split('|')
                if len(tmpvec) == 2:
                    if tmpvec[0] == username:
                        username = ''.join(random.sample('abcdefghijklmnopqrstuvwxyz', 6))
                        busernameUnique = False
    return username


def createUser(request):
    username = request.POST.get('username', 'none')
    modules = request.POST.get('modules', '1')
    desc = request.POST.get('desc', '')
    wei_xin = request.POST.get('wei_xin', '')
    user_type = request.POST.get('user-type', UserProfile.NORMAL_USER)
    set_password = request.POST.get('password', None)
    Users = User.objects.filter(username=username)

    if Users:
        return HttpResponse('{"r":"1","e":"用户%s已存在"}' % (username))

    if user_type == UserProfile.AUDIT_ADMIN:
        audit_users = User.objects.filter(userprofile__deleted=False).filter(
            userprofile__user_type=UserProfile.AUDIT_ADMIN)
        if audit_users:
            return HttpResponse('{"r":"2","e":"只能有一个验证/恢复审批管理员"}')

    moduleslist = modules.split(',')
    module = 0
    for element in moduleslist:
        module += int(element)

    password = random.sample('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 2)
    password += random.sample('@#!', 1)
    password += random.sample('abcdefghijklmnopqrstuvwxyz', 3)
    password += random.sample('0123456789', 3)
    password = ''.join(password)
    try:
        user = User()
        user.username = username
        user.is_superuser = False
        user.is_staff = False
        user.is_active = True
        if set_password:
            user.set_password(set_password)
        else:
            user.set_password(password)
        user.save()
        # 用户扩展信息 profile
        profile = UserProfile()
        profile.user_id = user.id
        profile.modules = module
        profile.desc = desc
        profile.wei_xin = wei_xin
        profile.user_type = user_type
        profile.save()
    except Exception as e:
        return HttpResponse('{"r": "1","e": "' + str(e) + '"}')

    tmpusername = getUniqueSambaUsername()
    tmppassword = ''.join(random.sample('0123456789', 6))
    SaveDictionary(DataDictionary.DICT_TYPE_SAMBA, str(user.id), tmpusername + '|' + tmppassword)

    mylog = dict()
    mylog['新建用户'] = username
    mylog['用户描述'] = desc
    mylog['功能授权'] = FmtModules(module)
    if set_password is None:
        ret = sendpwdurl(request, username, '')
    else:
        ret = 'OK'
    if (ret == 'OK'):
        mylog["操作结果"] = '成功'
        SaveOperationLog(request.user, OperationLog.TYPE_USER, json.dumps(mylog, ensure_ascii=False),
                         get_operator(request))
        return HttpResponse('{"r":"0","e":"新建用户成功，请在邮箱中查收密码"}')

    user.set_password('123456')
    user.save()
    mylog["操作结果"] = '新建用户成功，发送注册邮件失败' + ret
    SaveOperationLog(request.user, OperationLog.TYPE_USER, json.dumps(mylog, ensure_ascii=False), get_operator(request))
    ret = {"r": "0", "e": "* **该用户的默认密码为***123456*\n* 发送注册邮件失败，请检查【邮件服务器配置】",
           'detail': '{}-{}-{}'.format(user.id, username, module)}
    return HttpResponse(json.dumps(ret, ensure_ascii=False))


def getAllocateServerUserList(request):
    Users = User.objects.filter(is_superuser=False, is_active=True, userprofile__user_type=UserProfile.NORMAL_USER)
    userlist = list()
    if Users:
        for user in Users:
            userlist.append({"name": user.username, "value": user.id})

    retInfo = {'r': 0, 'e': '操作成功', 'list': userlist}
    jsonStr = json.dumps(retInfo, ensure_ascii=False)
    return HttpResponse(jsonStr)


def _get_host_id(serverid, serverids_type):
    if serverids_type == 'host_id':
        return serverid

    return Host.objects.get(ident=serverid).id


def allocateServer(request):
    from .serversmgr import remove_all_group
    from .serversmgr import ON_createNewHost
    serverids = request.POST.get('ids', 'none')
    userid = int(request.POST.get('userid', '0'))
    serverids_type = request.POST.get('serverids_type', 'host_id')  # 'host_id' or 'host_ident'
    user = User.objects.filter(id=userid)
    serveridList = serverids.split(",")
    display_names = list()
    hosts = list()
    if userid == -1:
        for serverid in serveridList:
            # 删除该host的分享
            serverid = str(_get_host_id(serverid, serverids_type))
            HostSnapshotShareHostDelete().delete(request=request, host_id=serverid)
            host = Host.objects.filter(id=serverid)
            display_name = host[0].display_name
            host.update(user=None)
            display_names.append(display_name)
            remove_all_group(host[0].ident, '1')
        desc = {'操作': '收回客户端', '客户端': display_names}
        SaveOperationLog(request.user, OperationLog.TYPE_SERVER, json.dumps(desc, ensure_ascii=False),
                         get_operator(request))
        return HttpResponse('{"r":"0","e":"操作成功"}')

    if len(user) == 1:
        for serverid in serveridList:
            tmplist = UserProfile.objects.filter(user_id=userid)
            if not tmplist:
                try:
                    # 修复用户扩展没有生效的情况
                    profile = UserProfile()
                    profile.user_id = userid
                    profile.save()
                except UserProfile.DoesNotExist:
                    pass
            host = Host.objects.filter(id=serverid)
            hosts.extend(host)

            # 是否删除该host的分享
            if host[0].user_id != userid:
                HostSnapshotShareHostDelete().delete(request=request, host_id=serverid)
                remove_all_group(host[0].ident, '1')

            host.update(user=user[0])
            ON_createNewHost(user[0].id, host[0].ident)
    else:
        return HttpResponse('{"r":"1","e":"分配失败"}')
    desc = {'操作': '分配客户端', '客户端': [i.display_name for i in hosts],
            '使用者': User.objects.filter(id=userid)[0].username}
    SaveOperationLog(request.user, OperationLog.TYPE_SERVER, json.dumps(desc, ensure_ascii=False),
                     get_operator(request))
    return HttpResponse('{"r":"0","e":"操作成功"}')


def components(request):
    id = request.GET.get('id', 'root')
    if id == '':
        id = 'root'

    if id == 'root':
        info = list()
        info.append(
            {"id": "1", "branch": [], "inode": False, "open": False, "checked": True, "label": "系统状态", "disabled": True,
             "checkbox": True})
        info.append(
            {"id": "2", "branch": [], "inode": False, "open": False, "checked": True, "label": "备份", "checkbox": True})
        if is_functional_visible('clusterbackup'):
            info.append(
                {"id": str(65536 * 2048), "branch": [], "inode": False, "open": False, "checked": True, "label": "集群",
                 "checkbox": True})
        if is_functional_visible('nas_backup'):
            info.append(
                {"id": str(65536 * 8192), "branch": [], "inode": False, "open": False, "checked": True,
                 "label": "NAS备份",
                 "checkbox": True})
        if is_functional_visible('vmwarebackup'):
            info.append(
                {"id": str(65536 * 512), "branch": [], "inode": False, "open": False, "checked": True,
                 "label": "VMware免代理备份",
                 "checkbox": True})
        if is_functional_visible('importExport'):
            info.append(
                {"id": str(65536 * 4096), "branch": [], "inode": False, "open": False, "checked": True,
                 "label": "备份数据归档",
                 "checkbox": True})
        if is_functional_visible('remotebackup'):
            info.append(
                {"id": str(65536 * 256), "branch": [], "inode": False, "open": False, "checked": True, "label": "远程容灾",
                 "checkbox": True})
        info.append(
            {"id": "4", "branch": [], "inode": False, "open": False, "checked": True, "label": "恢复", "checkbox": True})
        if is_functional_visible('takeover'):
            info.append(
                {"id": str(65536 * 128), "branch": [], "inode": False, "open": False, "checked": True, "label": "接管主机",
                 "checkbox": True})
        sub1 = {"id": "8", "branch": [], "inode": False, "open": False, "label": "模板管理", "checkbox": True}
        tmp = list()
        tmp.append(sub1)
        if hasFunctional('clw_desktop_aio'):
            info.append(
                {"id": "0", "branch": tmp, "inode": True, "open": False, "label": "桌面模板", "checkbox": True})
        if not hasFunctional('nomigrate_UI'):
            info.append({"id": "32", "branch": [], "inode": False, "open": False, "label": "迁移", "checkbox": True})
        # info.append(
        #    {"id": "64", "branch": [], "inode": False, "open": False, "checked": True, "label": "销毁", "checkbox": True})
        if is_functional_visible('auto_verify_task'):
            info.append(
                {"id": str(65536 * 16384), "branch": [], "inode": False, "open": False, "checked": True,
                 "label": "自动验证",
                 "checkbox": True})
        sub1 = {"id": "128", "branch": [], "inode": False, "open": False, "checked": True, "label": "客户端管理",
                "checkbox": True}
        sub2 = {"id": "256", "branch": [], "inode": False, "open": False, "checked": True, "label": "制作启动介质",
                "checkbox": True}
        sub3 = {"id": str(65536 * 16), "branch": [], "inode": False, "open": False, "checked": True, "label": "连接管理",
                "checkbox": True}
        if hasFunctional('no_make_media'):
            info.append(
                {"id": "0", "branch": [sub1, sub3], "inode": True, "open": False, "checked": True, "label": "设置",
                 "checkbox": True})
        else:
            info.append(
                {"id": "0", "branch": [sub1, sub2, sub3], "inode": True, "open": False, "checked": True, "label": "设置",
                 "checkbox": True})

        sub0 = {"id": "512", "branch": [], "inode": False, "open": False, "checked": True, "label": "查询",
                "checkbox": True}
        sub1 = {"id": "1024", "branch": [], "inode": False, "open": False, "checked": True, "label": "导出",
                "checkbox": True}
        sub2 = {"id": "2048", "branch": [], "inode": False, "open": False, "checked": False, "label": "删除",
                "checkbox": True}
        sub3 = {"id": "4096", "branch": [], "inode": False, "open": False, "checked": False, "label": "全部删除",
                "checkbox": True}
        branch = [sub0, sub1, sub2, sub3]
        if hasFunctional('no_clientlog') or get_separation_of_the_three_members().is_cannot_del_log():
            branch = [sub0, sub1]
        subinfo1 = {"id": "0", "branch": branch, "inode": True, "open": True, "checked": True,
                    "label": "客户端日志", "checkbox": True}
        sub0 = {"id": "8192", "branch": [], "inode": False, "open": False, "checked": True, "label": "查询",
                "checkbox": True}
        sub1 = {"id": "16384", "branch": [], "inode": False, "open": False, "checked": True, "label": "导出",
                "checkbox": True}
        sub2 = {"id": "32768", "branch": [], "inode": False, "open": False, "checked": False, "label": "删除",
                "checkbox": True}
        sub3 = {"id": "65536", "branch": [], "inode": False, "open": False, "checked": False, "label": "全部删除",
                "checkbox": True}
        branch = [sub0, sub1, sub2, sub3]
        if hasFunctional('no_oplog') or get_separation_of_the_three_members().is_cannot_del_log():
            branch = [sub0, sub1]
        subinfo2 = {"id": "0", "branch": branch, "inode": True, "open": True, "checked": True,
                    "label": "操作日志", "checkbox": True}
        subinfo3 = {"id": str(65536 * 1024), "branch": [], "inode": False, "open": False, "checked": True,
                    "label": "邮件记录", "checkbox": True}
        subinfo4 = {"id": "0", "branch": [], "inode": False, "open": False, "label": "网站防护日志", "checkbox": False}
        logbranch = [subinfo1, subinfo2, subinfo3]
        if is_functional_visible('webguard'):
            logbranch = [subinfo1, subinfo2, subinfo3, subinfo4]
        info.append(
            {"id": "0", "branch": logbranch, "inode": True, "open": False, "checked": True,
             "label": "日志管理",
             "checkbox": True})

        sub1 = {"id": str(65536 * 2), "branch": [], "inode": False, "open": False, "checked": True, "label": "安全状态报告",
                "checkbox": True}
        sub2 = {"id": str(65536 * 4), "branch": [], "inode": False, "open": False, "checked": True, "label": "容量状态报告",
                "checkbox": True}
        sub3 = {"id": str(65536 * 8), "branch": [], "inode": False, "open": False, "checked": True, "label": "设备历史状态报告",
                "checkbox": True}
        info.append(
            {"id": "0", "branch": [sub1, sub2, sub3], "inode": True, "open": False, "checked": True, "label": "业务报告",
             "checkbox": True})

        if is_functional_visible('webguard'):
            # “网页防篡改”授权项设置
            sub1 = {"id": "0", "branch": [], "inode": False, "open": False, "checked": True,
                    "label": "监控目标管理", "checkbox": False}
            sub2 = {"id": "0", "branch": [], "inode": False, "open": False, "checked": True,
                    "label": "应急策略管理", "checkbox": False}
            sub3 = {"id": "0", "branch": [], "inode": False, "open": False, "checked": True,
                    "label": "主页应急切换", "checkbox": False}
            sub4 = {"id": "0", "branch": [], "inode": False, "open": False, "checked": True,
                    "label": "网站防护设置", "checkbox": False}
            info.append(
                {"id": str(65536 * 32), "branch": [sub1, sub2, sub3, sub4], "inode": True, "open": False,
                 "checked": True,
                 "label": "网站防护", "checkbox": True})

        if is_functional_visible('hotBackup'):
            sub1 = {"id": "0", "branch": [], "inode": False, "open": False, "label": "新建热备计划", "checkbox": False}
            sub2 = {"id": "0", "branch": [], "inode": False, "open": False, "label": "热备计划管理", "checkbox": False}
            info.append({"id": str(65536 * 64), "branch": [sub1, sub2], "inode": True, "open": False, "checked": True,
                         "label": "热备", "checkbox": True})

        return HttpResponse(json.dumps(info, ensure_ascii=False))


def edituser(request):
    username = request.POST.get('username', 'none')
    modules = request.POST.get('modules', '1')
    desc = request.POST.get('desc', '')
    wei_xin = request.POST.get('wei_xin', '')

    Users = User.objects.filter(username=username)
    if not Users:
        return HttpResponse('{"r":"1","e":"用户%s不存在"}' % (username))

    moduleslist = modules.split(',')
    module = 0
    for element in moduleslist:
        module += int(element)

    try:
        UserProfile.objects.filter(user_id=Users[0].id).update(modules=module, desc=desc, wei_xin=wei_xin)
    except Exception as e:
        return HttpResponse('{"r": "1","e": "' + str(e) + '"}')

    mylog = dict()
    mylog['编辑用户'] = username
    mylog['用户描述'] = desc
    mylog['功能授权'] = FmtModules(module)
    SaveOperationLog(request.user, OperationLog.TYPE_USER, json.dumps(mylog, ensure_ascii=False), get_operator(request))
    return HttpResponse('{"r":"0","e":"操作成功"}')


def getuserinfo(request):
    userid = request.GET.get('userid', '0')
    user_name = request.GET.get('user_name', '')
    if not int(userid):
        Users = User.objects.filter(username=user_name)
    else:
        Users = User.objects.filter(id=userid)
    if not Users:
        return HttpResponse('{"r":"1","e":"用户id:{},name:{}不存在"}'.format(userid, user_name))

    username = Users[0].username

    profile = UserProfile.objects.filter(Q(user_id=userid) | Q(user__username=user_name))
    modules = 1
    desc = ''
    wei_xin = ''
    user_type = UserProfile.NORMAL_USER
    if profile:
        if type(profile[0].modules) == type(1):
            modules = profile[0].modules
        if type(profile[0].desc) == type(''):
            desc = profile[0].desc
        user_type = profile[0].user_type
        wei_xin = profile[0].wei_xin

    ret = json.dumps({"r": "0", "username": username, "modules": modules, "desc": desc, "e": "操作成功", 'type': user_type,
                      'wei_xin': wei_xin},
                     ensure_ascii=False)
    return HttpResponse(ret)


def resetpwd(request):
    ids = request.POST.get('ids', '0')
    logusername = list()
    for id in ids.split(','):
        user = User.objects.get(id=id)
        username = user.username
        logusername.append(username)
        ret = sendpwdurl(request, username, '')

    mylog = {"重置密码": logusername}
    SaveOperationLog(request.user, OperationLog.TYPE_USER, json.dumps(mylog, ensure_ascii=False), get_operator(request))

    if (ret == 'OK'):
        return HttpResponse('{"r":"0","e":"操作成功"}')
    user.set_password('123456')
    user.save()
    res = json.dumps({"r": "1", "e": "* **该用户的默认密码为**123456*  \n* 发送注册邮件失败，请检查【邮件服务器配置】{}".format(ret)},
                     ensure_ascii=False)
    return HttpResponse(res)


def getkey(request):
    try:
        rsrp = box_service.getPasswd()
    except Exception as e:
        rsrp = str(e)
    return HttpResponse(rsrp)


def getcount(request):
    Users = User.objects.exclude(is_superuser=True).exclude(username='web_api')
    return HttpResponse('{"r":"0","e":"操作成功","count":"%d"}' % Users.count())


def enable(request):
    """
    设置用户的启/禁用状态，可传用户id或者用户名
    :param request:
    :return:
    """
    ids = request.GET.get('ids', '0')
    is_enable = request.GET.get('is_enable')
    username = request.GET.get('username')
    enableuser = list()
    disableuser = list()
    if username:
        if is_enable == 'True':
            is_active = True
            enableuser.append(username)
        else:
            is_active = False
            disableuser.append(username)
        User.objects.filter(username=username).update(is_active=is_active)
    else:
        for id in ids.split(','):
            user = User.objects.get(id=id)
            if user.is_active:
                disableuser.append(user.username)
                user.is_active = False
            else:
                enableuser.append(user.username)
                user.is_active = True
            user.save()
    if len(enableuser) > 0 and len(disableuser) > 0:
        mylog = {'启用用户': enableuser, '禁用用户': disableuser}
    elif len(disableuser) > 0:
        mylog = {'禁用用户': disableuser}
    elif len(enableuser) > 0:
        mylog = {'启用用户': enableuser}
    else:
        mylog = {'操作': '启用/禁用用户'}
    SaveOperationLog(request.user, OperationLog.TYPE_USER, json.dumps(mylog, ensure_ascii=False), get_operator(request))
    return HttpResponse('{"r":"0","e":"操作成功"}')


def unlock(request):
    ids = request.POST.get('ids', '0')
    logUser = list()
    for id in ids.split(','):
        user = User.objects.get(id=id)
        logUser.append(user.username)
        SaveDictionary(DataDictionary.DICT_TYPE_LOGIN_FAILED, str(user.id), '0|0')

    desc = {'解锁用户': logUser}
    SaveOperationLog(request.user, OperationLog.TYPE_USER, json.dumps(desc, ensure_ascii=False), get_operator(request))
    return HttpResponse(json.dumps({"r": "0", "e": "有{}个用户解锁成功".format(len(logUser))}, ensure_ascii=False))


def deluser(request):
    ids = request.GET.get('ids', '0')
    num = 0
    logUser = list()
    for id in ids.split(','):
        user = User.objects.get(id=id)
        if user.hosts.exists():
            return HttpResponse('{"r":"1","e":"用户存在已分配的客户端，请先移除此客户端再删除用户"}')
        if user.userquotas.exists():
            for obj in user.userquotas.all():
                obj.set_deleted()
        if user.userid.exists():
            e = '用户有操作日志 <span style="color: blue;cursor:pointer;" onclick=delalllog("{}")>删除</span>'.format(id)
            return HttpResponse(json.dumps({"r": "1", "e": e}, ensure_ascii=False))
        user.userprofile.deleted = True
        user.is_active = False
        logUser.append(user.username)
        new_name = (user.username + '_' + str(uuid.uuid4().hex))[:30]
        user.username = new_name
        user.email = user.email + '_' + str(uuid.uuid4().hex)
        user.save()
        user.userprofile.save()

        user_tunnels = user.host_tunnels.filter()
        for tunnel in user_tunnels:
            tunnel.delete() if tunnel.host is None else None

        samba_user = GetDictionary(DataDictionary.DICT_TYPE_SAMBA, id, 'aio|123456').split('|')[0]
        HostSnapshotShareUserDelete().delete(request=request, samba_user=samba_user)
        DelDictionary(DataDictionary.DICT_TYPE_SAMBA, str(id))
        DelDictionary(DataDictionary.DICT_TYPE_PWD_EXPIRY, str(id))
        DelDictionary(DataDictionary.DICT_TYPE_LOGIN_FAILED, str(id))
        deloneuserpolicy(user)
        num += 1
    desc = {'删除用户': logUser}
    SaveOperationLog(request.user, OperationLog.TYPE_USER, json.dumps(desc, ensure_ascii=False), get_operator(request))
    return HttpResponse(json.dumps({"r": "0", "e": "有{}个用户删除成功".format(num)}, ensure_ascii=False))


def getemail(request):
    return HttpResponse(json.dumps({"r": 0, "e": "操作成功", "email": request.user.email}))


def setemail(request):
    email = request.GET.get('email', '')
    user = User.objects.get(id=request.user.id)
    user.email = email
    user.save()
    mylog = {'设置邮箱': email}
    SaveOperationLog(request.user, OperationLog.TYPE_USER, json.dumps(mylog, ensure_ascii=False), get_operator(request))
    return HttpResponse('{"r":"0","e":"操作成功"}')


def sendpwdurl(request, email, sendall):
    if email == '':
        return "邮箱地址不存在"
    Users = User.objects.filter(username=email)
    if not Users:
        Users = User.objects.filter(email=email)
        if not Users:
            return "邮箱地址不存在"
    if sendall != 'sendall' and len(Users) > 1:
        return 'many'

    for user in Users:
        if user.email != '':
            emailaddr = user.email
        else:
            emailaddr = user.username

        if emailaddr == '':
            return "用户无邮箱地址"
        strKey = str(user.id)
        strValue = uuid.uuid4().hex.lower()
        SaveTmpDictionary(TmpDictionary.TMP_DICT_TYPE_PWD, strKey, strValue,
                          (datetime.datetime.now() + datetime.timedelta(hours=12)))

        url = 'http://' + request.get_host() + '/xdashboard/' + 'resetpwd_html/?key=' + strKey + '&value=' + strValue

        ret = send_mail(emailaddr, "重置密码", "用户{}重置密码的链接为：{}".format(user.username, url))
    return ret


def forgetPwd(request):
    email = request.POST.get('email', '')
    sendall = request.POST.get('sendall', '')
    ret = sendpwdurl(request, email, sendall)
    if ret == 'OK':
        return HttpResponse('{"r":"0","e":"操作成功"}')
    elif ret == 'many':
        return HttpResponse('{"r":"99","e":"该邮箱对应了多个用户，请输入用户名，或点击确定为每个用户发送邮件。"}')
    return HttpResponse(json.dumps({"r": "1", "e": ret}, ensure_ascii=False))


def showui(request, adict):
    key = request.GET.get('key', '')
    value = request.GET.get('value', '')
    ret = GetTmpDictionary(TmpDictionary.TMP_DICT_TYPE_PWD, key)
    if ret["r"] != 0:
        return HttpResponse('链接无效!')
    if ret["value"] != value:
        return HttpResponse('链接失效!如发送了多封邮件，请使用最后一个的链接')
    t = time.mktime(time.strptime(str(ret["expireTime"]), "%Y-%m-%d %H:%M:%S.%f")) - time.time()
    if t < 0:
        return HttpResponse('已过期，请联系管理员!')
    if get_separation_of_the_three_members().is_strict_password_policy():
        policy = GetDictionary(DataDictionary.DICT_TYPE_PWD_POLICY, 'policy', '2')
    else:
        policy = GetDictionary(DataDictionary.DICT_TYPE_PWD_POLICY, 'policy', '3')

    adict['expireTime'] = ret["expireTime"]
    adict['key'] = key
    adict['value'] = value
    adict['policy'] = policy
    response = render_to_response("resetpwd.html", adict)
    response.set_cookie(settings.CSRF_COOKIE_NAME, request.META["CSRF_COOKIE"], max_age=60 * 60 * 24 * 7 * 52,
                        domain=settings.CSRF_COOKIE_DOMAIN)
    return response


def setpwd(request):
    id = request.POST.get('id', '0')
    password = request.POST.get('pwd', '')
    password = base64.b64decode(password).decode('utf-8')
    value = request.POST.get('value', '')

    ret = GetTmpDictionary(TmpDictionary.TMP_DICT_TYPE_PWD, id)
    if ret["r"] != 0:
        return HttpResponse('{"r":"1","e":"链接无效"}')
    if ret["value"] != value:
        return HttpResponse('{"r":"1","e":"链接无效"}')

    t = time.mktime(time.strptime(str(ret["expireTime"]), "%Y-%m-%d %H:%M:%S.%f")) - time.time()
    if int(t) < 0:
        return HttpResponse('{"r":"1","e":"已过期，请联系管理员!"}')

    user = User.objects.get(id=id)
    user.set_password(password)
    user.save()
    if user.is_superuser:
        box_service.setPasswd(json.dumps(password))
        _save_aio_key()
    SetPwdExpire(user.id)

    TmpDictionary.objects.filter(dictType=TmpDictionary.TMP_DICT_TYPE_PWD, dictKey=id).delete()

    return HttpResponse('{"r":"0","e":"操作成功"}')


def check_hard_ware_id():
    global is_checked_hard_id
    # 若工作在加密狗模式下，不再进行硬件的验证！
    if check_is_in_uk_mod():
        return 1
    # 互联网授权下，不再进行硬件的验证！
    if box_service.isFileExist(AIO_NET_LIC_FLAG_FILE):
        return 1
    check_obj = ['mac']
    for i in check_obj:
        if i == 'mac':
            rep = check_mac()
            if not rep:
                return 0

    is_checked_hard_id = True
    return 1


# 若发生异常则说明：没有工作在加密狗的模式下。
def check_is_in_uk_mod():
    try:
        pdll = ctypes.CDLL('test_v2.so')
    except Exception as e:
        return False
    else:
        _logger.warning('work in uk mod, skip confirm harward id!')
        return True


def check_mac():
    global is_checked_hard_id

    if is_checked_hard_id:
        return 1
    else:
        with subprocess.Popen("python /sbin/aio/script2/initHardwareId/inithardwareid.py -check", shell=True,
                              stdout=subprocess.PIPE,
                              universal_newlines=True) as p:
            pass
        return p.returncode == 0


def forget_handler(request, adict):
    a = request.GET.get('a', 'none')
    if a == 'none':
        a = request.POST.get('a', 'none')
    if a == 'none':
        return showui(request, adict)
    if a == 'setpwd':
        return setpwd(request)

    return HttpResponse(json.dumps({"r": "1", "e": "没有对应的action:{}".format(html.escape(a))}))


def get_create_user_type(request):
    if request.user.is_superuser:
        user_type = 'sec-admin'
    else:
        user_type = request.user.userprofile.user_type
    user_type_list = list()
    if user_type == 'aud-admin':
        user_type_list.append({'type': 'aud-admin', 'name': '安全审计管理员'})
    elif is_functional_visible('webguard'):
        user_type_list.append({'type': 'content-admin', 'name': '内容管理员'})

    if user_type == 'sec-admin':
        if hasFunctional('clw_desktop_aio'):
            user_type_list.append({'type': 'normal-admin', 'name': '业务管理员'})
            user_type_list.append({'type': 'audit-admin', 'name': '验证/恢复审批管理员'})
        else:
            user_type_list.append({'type': 'normal-admin', 'name': '系统管理员'})

    return HttpResponse(json.dumps({'user_type_list': user_type_list}))


def getwwwlicense(request):
    result = {'r': 0, 'e': '操作成功'}
    ret, sleeptime = wwwlicense()
    if ret != 'ok':
        result['r'] = 1
        if ret in ('net_error',):
            result['e'] = '网络错误，请稍后再试'
        elif ret in ('no_sn', 'no_prikey',):
            result['e'] = '一体机缺少序列号或私钥文件'
        elif ret in ('license_del',):
            result['e'] = '授权服务器中未找到此序列号'
        elif ret in ('aio_error',):
            result['e'] = '一体机解密授权文件失败'
        else:
            result['e'] = '内部错误,e={}'.format(ret)
    return HttpResponse(json.dumps(result, ensure_ascii=False))


def region_for_wei_xin(request):
    corp_id = request.POST.get('corp', '')
    corp_secret = request.POST.get('secret', '')
    agent_id = request.POST.get('agent', '')
    result_info = {'r': 0, 'e': '修改成功'}
    weixin_info = {'corp_id': corp_id, 'corp_secret': corp_secret, 'agent_id': agent_id}
    weixin_info = json.dumps(weixin_info)
    try:
        ol = OperationLog.objects.filter(event=OperationLog.WEIXIN)
        if ol.count():
            ol.update(desc=weixin_info)
        else:
            OperationLog.objects.create(user=request.user, event=OperationLog.WEIXIN, desc=weixin_info)
    except Exception as e:
        _logger.error('region_for_wei_xin error:{}'.format(e), exc_info=True)
        result_info = {'r': 1, 'e': '修改失败'}
    return HttpResponse(json.dumps(result_info, ensure_ascii=False))


def get_wei_xin_list():
    wx_objs = OperationLog.objects.filter(event=OperationLog.WEIXIN).first()
    result = {}
    if wx_objs:
        desc = json.loads(wx_objs.desc)
        result = {'corp': desc['corp_id'], 'id': wx_objs.id, 'secret': desc['corp_secret'], 'agent': desc['agent_id']}
    return HttpResponse(json.dumps(result, ensure_ascii=False))


def check_wei_xin_token(request):
    corpid = request.GET.get('corp', '')
    corpsecret = request.GET.get('secret', '')
    result = {'r': 0, 'e': '测试连接成功'}
    result_token = enterprise_wei_xin.get_token_from_wei_xin(corpid, corpsecret)
    if isinstance(result_token, dict):
        result = {'r': 1, 'e': result_token['errmsg']}
    return HttpResponse(json.dumps(result, ensure_ascii=False))


def have_audit_admin():
    user_profiles = UserProfile.objects.filter(deleted=False).filter(user_type=UserProfile.AUDIT_ADMIN).all()
    if user_profiles:
        return True
    return False


def user_handler(request):
    a = request.GET.get('a', 'none')
    if a == 'none':
        a = request.POST.get('a', 'none')
    if a == 'new':
        return createUser(request)
    if a == 'edit':
        return edituser(request)
    if a == 'resetpwd':
        return resetpwd(request)
    if a == 'changepwd':
        return changepwd(request)
    if a == 'changepwdex':
        return changepwd2(request)
    if a == 'getuserinfo':
        return getuserinfo(request)
    if a == 'getkey':
        return getkey(request)
    if a == 'getcount':
        return getcount(request)
    if a == 'allocateserver':
        return allocateServer(request)
    if a == 'list':
        return getlist(request)
    if a == 'getcomponents':
        return components(request)
    if a == 'allocatelist':
        return getAllocateServerUserList(request)
    if a == 'enbale':
        return enable(request)
    if a == 'del':
        return deluser(request)
    if a == 'getemail':
        return getemail(request)
    if a == 'setemail':
        return setemail(request)
    if a == 'unlock':
        return unlock(request)
    if a == 'get_create_user_type':
        return get_create_user_type(request)
    if a == 'wx_region':
        return region_for_wei_xin(request)
    if a == 'wx_list':
        return get_wei_xin_list()
    if a == 'check_wx':
        return check_wei_xin_token(request)
    return HttpResponse(json.dumps({"r": "1", "e": "没有对应的action:{}".format(html.escape(a))}))
