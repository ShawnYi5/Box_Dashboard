# coding=utf-8
import base64
import codecs
import configparser
import datetime
import html
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time

import requests
from django.http import HttpResponse
from rest_framework import status

if __name__ != '__main__':
    from box_dashboard import xlogging
    from xdashboard.models import DataDictionary
    from xdashboard.common.dict import GetDictionary, SaveDictionary
    from xdashboard.common.functional import hasFunctional
    from xdashboard.common.Update import CUpdate
    from xdashboard.request_util import get_operator
    import xdashboard.handle.enum_id as enum_id
    from xdashboard.handle.logserver import SaveOperationLog
    from xdashboard.models import OperationLog
    from apiv1.storage_nodes import UserQuotaTools
    from django.contrib.auth.models import User

if __name__ == '__main__':
    from authorize import authCookies

    sys.path.append('../')
    from common.functional import hasFunctional
    import logging as xlogging

_logger = xlogging.getLogger(__name__)
g_thread_handle = None


def execute_cmd_and_return_code(cmd):
    _logger.info("execute_cmd_and_return_code cmd:{}".format(cmd))
    with subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True) as p:
        stdout, stderr = p.communicate()
    if p.returncode != 0:
        return p.returncode, stderr
    else:
        return p.returncode, stdout


def get_oem_info():
    oem = dict()
    oem_ini_path = r'/var/www/static/OEM/oem.ini'
    config = configparser.ConfigParser()
    try:
        config.read(oem_ini_path, 'gb2312')
    except Exception as e:
        config.read(oem_ini_path, 'utf-8')
    if 'res' not in config:
        config['res'] = dict()
    topsecret = config['res']
    if hasFunctional('clw_desktop_aio'):
        oem['title'] = topsecret.get("title", "科力锐桌面保障恢复系统")
        oem['base_html'] = topsecret.get("base_html", "_base_desktop.html")
    else:
        oem['title'] = topsecret.get("title", "智动全景灾备系统")
        oem['base_html'] = topsecret.get("base_html", "_base.html")
    oem['company'] = topsecret.get("company", "科力锐")
    oem['prefix'] = topsecret.get("prefix", "ClwDR")
    return oem


def getProductName():
    oem = get_oem_info()
    return oem['title']


def getBigVersion():
    if hasFunctional('version1'):
        return '1.0'
    return '2.0'


def getAIOVersion():
    version = '2.0.000000'
    tfile = '/usr/sbin/aio/version.inc'
    try:
        lines = open(tfile, 'r').readline()
        vec = lines.split('_')
        if len(vec) >= 5:
            version = '{}.{}'.format(getBigVersion(), vec[0][2:] + vec[1] + vec[2][0:2])
    except Exception as e:
        _logger.info(e)
    return version


def getClienPathVer(tfile):
    patchver = None
    try:
        if os.path.isfile(tfile):
            cf = configparser.ConfigParser()
            cf.read_file(codecs.open(tfile, "r", "gb2312"))
            patchver = cf.get("default", "patchver")
            return patchver
    except Exception as e:
        _logger.info(e)
    return patchver


def getWinClientPathVer():
    tfile = '/sbin/aio/box_dashboard/xdashboard/static/download/client/win32/clientpatch.ini'
    return getClienPathVer(tfile)


def getLinuxClientX86PathVer():
    tfile = '/sbin/aio/box_dashboard/xdashboard/static/download/client/linux/x86/clientpatch.ini'
    return getClienPathVer(tfile)


def getLinuxClientX64PathVer():
    tfile = '/sbin/aio/box_dashboard/xdashboard/static/download/client/linux/x64/clientpatch.ini'
    return getClienPathVer(tfile)


def getisotime():
    if os.path.isfile(r'/var/lib/tftpboot/winpe.iso'):
        mtime = os.path.getmtime(r'/var/lib/tftpboot/winpe.iso')
        return time.strftime('%y%m%d', time.localtime(mtime))
    return '111111'


def getVersion(request):
    # 得到版本号
    jsonstr = {"r": 0, "e": "操作成功",
               "currentaiover": getAIOVersion(),
               "curentdriverver": '{}.{}'.format(getBigVersion(), enum_id.get_version(enum_id.g_db_path)),
               "curentmediumver": '{}.{}'.format(getBigVersion(), getisotime()),
               "bigver": getBigVersion(),
               }
    return HttpResponse(json.dumps(jsonstr, ensure_ascii=False))


def getUrl(request):
    jsonstr = {"r": 0, "e": "操作成功",
               "url": GetDictionary(DataDictionary.DICT_TYPE_UPDATE_URL, 'url', "update.clerware.com")
               }
    return HttpResponse(json.dumps(jsonstr, ensure_ascii=False))


def seturl(request):
    url = request.GET.get('url', 'update.clerware.com')
    if not url:
        url = 'update.clerware.com'
    SaveDictionary(DataDictionary.DICT_TYPE_UPDATE_URL, 'url', url)
    jsonstr = {"r": 0, "e": "操作成功",
               "url": url
               }
    return HttpResponse(json.dumps(jsonstr, ensure_ascii=False))


def cur_file_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def update_one_driver(zipfile, jsonfile, user, operator):
    _logger.debug('update_one_driver zipfile = {},jsonfile={}\n'.format(zipfile, jsonfile))
    jsonobj = None
    with open(os.path.join(jsonfile)) as file_object:
        jsonobj = json.load(file_object)
    all_sql_info_list = list()
    for item in jsonobj:
        one_sql_info_list = {'server_id': None, 'del': 0, 'show_name': None, 'hard_or_comp_id': None,
                             'inf_driver_ver': None, 'inf_path': None, 'zip_path': None, 'system_name': None}
        one_sql_info_list["server_id"] = item["server_id"]
        one_sql_info_list["hard_or_comp_id"] = item["hard_or_comp_id"]
        one_sql_info_list["inf_driver_ver"] = item["inf_driver_ver"]
        one_sql_info_list["inf_path"] = item["inf_path"]
        if os.path.exists(zipfile):
            one_sql_info_list["zip_path"] = item["zip_path"]
        else:
            one_sql_info_list["zip_path"] = None
        one_sql_info_list["system_name"] = item["system_name"]
        one_sql_info_list["show_name"] = item["show_name"]
        one_sql_info_list["del"] = item["del"]
        all_sql_info_list.append(one_sql_info_list.copy())
    mylog = {'硬件ID数量': len(jsonobj), '文件名': '{}'.format(os.path.basename(zipfile))}
    SaveOperationLog(user, OperationLog.TYPE_UPDATE_AIO_DRIVE, json.dumps(mylog, ensure_ascii=False), operator)
    with sqlite3.connect(enum_id.g_db_path) as cx:
        cu = cx.cursor()
        enum_id.update_one_zip(cu, all_sql_info_list, enum_id.g_db_path, enum_id.g_driver_pool, zipfile)
        cx.commit()


def updatedriver(folder, user, operator=None):
    _logger.debug('updatedriver folder = {}\n'.format(folder))
    folders = os.listdir(folder)
    for name in folders:
        curname = os.path.join(folder, name)
        isfile = os.path.isfile(curname)
        if isfile:
            zipfile = os.path.splitext(curname)[0]
            ext = os.path.splitext(curname)[1]
            if ext == '.json':
                update_one_driver(zipfile, curname, user, operator)


def update_callback(user, logfilename, bsuccess, reason, operator):
    if bsuccess:
        endlog = {'升级': '从文件{}执行升级脚本'.format(logfilename)}
    else:
        endlog = {'结束': '从文件{}更新失败。{}'.format(logfilename, reason)}
    SaveOperationLog(user, OperationLog.TYPE_UPDATE_AIO_BASE, json.dumps(endlog, ensure_ascii=False), operator)


def processupload(filepath, user, operator):
    type = '0'
    rev = 0
    msg = ''
    file_dir = os.path.splitext(filepath)[0]
    logfilename = os.path.basename(filepath)
    startlog = {'开始': '从文件{}更新'.format(logfilename)}
    endlog = {'结束': '从文件{}更新完成'.format(logfilename)}

    cmd = 'unzip -o -P {89B87785-0C5F-47eb-B7DE-73DD962B0FAE} "' + filepath + '" sig.ini -d "{}"'.format(file_dir)
    info = execute_cmd_and_return_code(cmd)
    if info[0] != 0:
        if os.path.isfile(filepath):
            os.remove(filepath)
        endlog = {'结果': '解压文件{}失败。'.format(logfilename)}
        SaveOperationLog(user, OperationLog.TYPE_UPDATE_AIO_DRIVE, json.dumps(endlog, ensure_ascii=False), operator)
        return 1, '从文件{}更新结束'.format(logfilename)

    sig_ini_path = os.path.join(file_dir, 'sig.ini')
    if os.path.isfile(sig_ini_path):
        cf = configparser.ConfigParser()
        cf.read_file(open(sig_ini_path))
        type = cf.get("Main", "type")
    else:
        if os.path.isfile(filepath):
            os.remove(filepath)
        endlog = {'结果': '从文件{}更新失败,文件格式不正确。'.format(logfilename)}
        SaveOperationLog(user, OperationLog.TYPE_UPDATE_AIO_DRIVE, json.dumps(endlog, ensure_ascii=False), operator)
        return 1, '从文件{}更新失败,文件格式不正确。'.format(logfilename)
    logtype = None
    if type in ('3', '5',):
        logtype = OperationLog.TYPE_UPDATE_AIO_DRIVE
    else:
        logtype = OperationLog.TYPE_UPDATE_AIO_BASE
    cmd = 'unzip -o -P {89B87785-0C5F-47eb-B7DE-73DD962B0FAE} "' + filepath + '" -d "{}"'.format(file_dir)
    SaveOperationLog(user, logtype, json.dumps(startlog, ensure_ascii=False), operator)
    info = execute_cmd_and_return_code(cmd)
    if info[0] != 0:
        if os.path.isfile(filepath):
            os.remove(filepath)
        mylog = {'进度': '解压文件{}失败'.format(logfilename)}
        SaveOperationLog(user, logtype, json.dumps(mylog, ensure_ascii=False), operator)
        return 1, '解压文件{}失败'.format(logfilename)

    mylog = {'进度': '解压文件{}完成'.format(logfilename)}
    SaveOperationLog(user, logtype, json.dumps(mylog, ensure_ascii=False), operator)

    if os.path.isfile(filepath):
        os.remove(filepath)

    # 从外网升级驱动库
    if type == "3":
        target_dir = file_dir
        or_db_name = 'drvierid.db'
        new_db_name = 'drvierid.db'
        driver_pool_path = '/home/aio/driver_pool/'
        update_type = 3
        u_ins = enum_id.Update(target_dir, or_db_name, new_db_name, driver_pool_path, update_type)
        bSuccess = True
        try:
            u_ins.work()
        except Exception as e:
            bSuccess = False
            _logger.error("processupload error ,{}".format(e))
            endlog = {'结束': '从文件{}更新失败,{}'.format(logfilename, e)}
        if bSuccess:
            driver_ver = '{}.{}'.format(getBigVersion(), enum_id.get_version(enum_id.g_db_path))
            endlog = {'结束': '成功升级到版本{}'.format(driver_ver)}
        SaveOperationLog(user, OperationLog.TYPE_UPDATE_AIO_DRIVE, json.dumps(endlog, ensure_ascii=False), operator)
        shutil.rmtree(file_dir)

    # 用户手动上传，本地获取到的驱动
    elif type == "5":
        target_dir = file_dir
        or_db_name = 'drvierid.db'
        new_db_name = 'drvierid_user.db'
        driver_pool_path = '/home/aio/driver_pool/'
        update_type = 5
        u_ins = enum_id.Update(target_dir, or_db_name, new_db_name, driver_pool_path, update_type)
        try:
            u_ins.work()
        except Exception as e:
            _logger.error("processupload error ,{}".format(e))
        SaveOperationLog(user, OperationLog.TYPE_UPDATE_AIO_DRIVE, json.dumps(endlog, ensure_ascii=False), operator)
    else:
        target_dir = file_dir
        CUpdate().Update(target_dir, update_callback, user, logfilename, operator)
    return 0, ''


def start_thread(name, fun, args):
    ret = dict()
    global g_thread_handle
    if g_thread_handle is None:
        g_thread_handle = threading.Thread(target=fun, args=args)
        g_thread_handle.setName(name)
        g_thread_handle.start()
        ret["r"] = 0
    elif g_thread_handle.isAlive():
        ret["r"] = 1
        ret["name"] = g_thread_handle.getName()
        ret["e"] = '线程[{}]正在运行'.format(ret["name"])
    else:
        g_thread_handle = None
        g_thread_handle = threading.Thread(target=fun, args=args)
        g_thread_handle.setName(name)
        g_thread_handle.start()
        ret["r"] = 0
    return ret


def _get_tmp_dir():
    from apiv1.views import StorageNodes
    api_response = StorageNodes().get(None)
    if status.is_success(api_response.status_code):
        for element in api_response.data:
            if not element["linked"]:
                continue
            if element["available_bytes"] / 1024 ** 3 > 10:
                node_id = element["id"]
                try:
                    node_base_path = UserQuotaTools.get_storage_node_base_path(node_id)
                    return '{}/update_{}'.format(node_base_path, time.time())
                except:
                    _logger.debug('_get_tmp_dir get_storage_node_base_path Failed.ignore it.')

    return '/home/tmp/update_{}'.format(time.time())


def upload(request):
    file_data = request.body
    type = '0'
    name = request.GET.get('name', 'none.bin')
    start = int(request.GET.get('start', '0'))
    step = int(request.GET.get('step', 1024 * 1024))
    total = int(request.GET.get('total', 0))
    sync = int(request.GET.get('sync', 0))
    tmp_dir = request.GET.get('tmp_dir', None)
    if tmp_dir and os.path.isdir(tmp_dir):
        pass
    else:
        tmp_dir = _get_tmp_dir()

    r = 0
    try:
        os.makedirs(os.path.join(tmp_dir, 'update'))
    except OSError as e:
        pass

    filepath = os.path.join(tmp_dir, 'update', name)

    if start == 0:
        try:
            os.remove(filepath)
        except OSError as e:
            pass

    binfile = open(filepath, 'ab')
    vec = str(file_data).split(';base64,')
    if len(vec) == 2:
        strbase64 = vec[1]
    else:
        return HttpResponse(json.dumps({"r": 1, "e": "忽略"}, ensure_ascii=False))
    binfile.write(base64.b64decode(strbase64))
    binfile.close()
    start = start + step
    if start >= total:
        if os.path.getsize(filepath) == total:
            r = 200
            _logger.info('file upload ok filename={}'.format(filepath))
            if sync:
                rev, msg = processupload(filepath, request.user, get_operator(request))
                if rev:
                    return HttpResponse(
                        json.dumps({"r": rev, "e": msg}, ensure_ascii=False))
            else:
                tret = start_thread('driver_upload', processupload, (filepath, request.user, get_operator(request),))
                if tret["r"] is not 0:
                    return HttpResponse(
                        json.dumps(
                            {"r": tret["r"], "e": tret["e"], "tname": tret["name"], "name": name, "start": start},
                            ensure_ascii=False))

    return HttpResponse(
        json.dumps({"r": r, "e": "操作成功", "name": name, "start": start, "tmp_dir": tmp_dir}, ensure_ascii=False))


def uploadbyflash(request):
    file = request.FILES.get("Filedata", None)
    ret = 2
    name = 'none'
    if file:
        filepath = os.path.join(cur_file_dir(), 'static', 'download', 'update', file.name)
        name = file.name
        fp = open(filepath, 'wb')
        for content in file.chunks():
            fp.write(content)
        fp.close()
        ret = 200
        _logger.info('file upload ok filename={}'.format(filepath))
        tret = start_thread('driver_upload', processupload, (filepath, request.user, get_operator(request),))
        if tret["r"] is not 0:
            return HttpResponse(
                json.dumps({"r": tret["r"], "e": tret["e"], "tname": tret["name"], "save_name": name},
                           ensure_ascii=False))
    return HttpResponse(json.dumps({'r': ret, 'save_name': name}, ensure_ascii=False))


def getlocaldriverlist(request):
    driverlist = enum_id.get_region(enum_id.g_db_path)
    icount = 0
    if driverlist:
        for item in driverlist:
            icount += int(item[1]) - int(item[0]) + 1
    return HttpResponse(json.dumps({'r': 0, 'total': icount}, ensure_ascii=False))


# 检查aio是否可以连接外网
def check_is_out_lan(request):
    checkurl = request.GET.get('checkurl', '')
    try:
        req = requests.request(method='GET', url=checkurl, verify=False)
        if status.is_success(req.status_code):
            content = req.content.decode('utf-8')
            content = json.loads(content)
            if content['r'] == 0:
                return HttpResponse(json.dumps({'r': 1}))
            else:
                return HttpResponse(json.dumps({'r': 0}))
    except:
        return HttpResponse(json.dumps({'r': 0}))


# fork线程
def down_and_execute(request):
    downurl = request.GET.get('downurl', '')
    taskname = request.GET.get('taskname', '')
    filepath = os.path.join(_get_tmp_dir(),
                            datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S-%f') + taskname + '.zip')
    if not os.path.exists(os.path.dirname(filepath)):
        os.mkdir(os.path.dirname(filepath))

    _logger.info('download update file begin, filename:{}'.format(downurl))
    if taskname == 'driver_update':
        tret = start_thread(taskname, download_worker_driver, (request, downurl, filepath,))
    else:
        tret = start_thread(taskname, download_worker, (request, downurl, filepath,))
    if tret['r'] == 1:
        return HttpResponse(
            json.dumps({"r": 1, "e": tret["e"], "tname": tret["name"]}, ensure_ascii=False))
    return HttpResponse(json.dumps({"r": 0, "e": "操作成功"}))


# 根据url下载文件 并执行更新操作
def download_worker(request, url, filepath):
    taskname = request.GET.get('taskname', '')
    if taskname == 'aio_update':
        logtype = OperationLog.TYPE_UPDATE_AIO_BASE
    if taskname == 'iso_update':
        logtype = OperationLog.TYPE_UPDATE_AIO_ISO
    if taskname == 'driver_update':
        logtype = OperationLog.TYPE_UPDATE_AIO_DRIVE

    r = requests.get(url, stream=True, verify=False)
    if not status.is_success(r.status_code):
        SaveOperationLog(request.user, logtype, json.dumps({'失败': '下载文件失败，状态码:{}'.format(r.status_code),
                                                            '文件名': '{}'.format(url)}, ensure_ascii=False),
                         get_operator(request))
        _logger.error('下载文件失败，状态码:{}'.format(r.status_code))
        return
    SaveOperationLog(request.user, logtype, json.dumps({'状态': '开始下载文件', '文件名': '{}'.format(url)}, ensure_ascii=False),
                     get_operator(request))

    lastsize = 0
    size = 0
    bneedlog = True
    with open(filepath, 'wb') as fd:
        for chunk in r.iter_content(1024 * 1024):
            size += len(chunk)
            fd.write(chunk)
            bneedlog = True
            if size >= lastsize + 100 * 1024 * 1024:
                SaveOperationLog(request.user, logtype,
                                 json.dumps({'下载状态': '已下载：{:.1f}MB'.format(size / (1024 * 1024))},
                                            ensure_ascii=False), get_operator(request)
                                 )
                bneedlog = False
                lastsize = size

    if bneedlog:
        SaveOperationLog(request.user, logtype,
                         json.dumps({'下载状态': '已下载：{:.1f}MB'.format(size / (1024 * 1024))},
                                    ensure_ascii=False), get_operator(request)
                         )

    _logger.info('download update file finish and bigin execute update, filepath:{}'.format(filepath))
    g_thread_handle.setName('processing_' + taskname)
    # 判断是否 执行更新操作
    if getattr(threading.current_thread(), 'do_run', True):
        processupload(filepath, request.user, get_operator(request))
        try:
            os.remove(filepath)
        except:
            pass
        SaveOperationLog(request.user, logtype, json.dumps({'状态': '更新完成', '文件名': '{}'.format(url)}, ensure_ascii=False),
                         get_operator(request))
    else:
        _logger.info(
            'download update file is finished, but task has been canceled and file will be remouved, filepath:{}'.format(
                filepath))
        SaveOperationLog(request.user, logtype,
                         json.dumps({'状态': '下载完成，任务被取消，文件将删除', '文件名': '{}'.format(url)}, ensure_ascii=False),
                         get_operator(request))
        try:
            os.remove(filepath)
        except:
            pass
        return


# 获取驱动下载路径
def download_worker_driver(request, url, filepath):
    reqpath = requests.get(url, verify=False)
    if not status.is_success(reqpath.status_code):
        SaveOperationLog(request.user, OperationLog.TYPE_UPDATE_AIO_DRIVE,
                         json.dumps({'失败': '获取驱动下载路径失败，状态码:{} 路径:{}'.format(reqpath.status_code, url)},
                                    ensure_ascii=False), get_operator(request)
                         )
        _logger.error('获取驱动下载路径失败，状态码:{} 路径：{}'.format(reqpath.status_code, url))
        return
    content = reqpath.content.decode('utf-8')
    content = json.loads(content)
    if content['r'] != 0:
        SaveOperationLog(request.user, OperationLog.TYPE_UPDATE_AIO_DRIVE,
                         json.dumps({'失败': '获取驱动下载路径失败，状态码:{}, 路径:{}'.format(reqpath.status_code, url),
                                     '原因': '{}'.format(content['e'])}, ensure_ascii=False), get_operator(request)
                         )
        _logger.error('获取驱动下载路径失败，状态码:{}, 路径:{}, 原因:{}'.format(reqpath.status_code, url, content['e']))
        return
    downurl = content['url']
    download_worker(request, downurl, filepath)


def get_current_taskname():
    global g_thread_handle
    jsonstr = {'name': ''}
    if g_thread_handle and g_thread_handle.isAlive():
        jsonstr['name'] = g_thread_handle.getName()
    return HttpResponse(json.dumps(jsonstr))


def stop_current_threading():
    global g_thread_handle
    if g_thread_handle and g_thread_handle.isAlive():
        name = g_thread_handle.getName()
        if not name.startswith('processing') and (name != 'driver_upload'):
            setattr(g_thread_handle, 'do_run', False)
            g_thread_handle = None
            return HttpResponse(json.dumps({"r": 0, "e": '停止任务成功'}, ensure_ascii=False))
        else:
            return HttpResponse(json.dumps({"r": 1, "e": '任务执行过程中，停止任务失败'}, ensure_ascii=False))
    return HttpResponse(json.dumps({"r": 1, "e": '当前无任务执行，停止失败'}, ensure_ascii=False))


def report_update_ret(request):
    ver = request.POST.get('ver', '')
    update_type = request.POST.get('update_type', '')
    err_msg = request.POST.get('err_msg', None)
    if update_type == 'iso':
        if err_msg:
            endlog = {'结束': '启动介质升级失败，{}'.format(err_msg)}
        else:
            endlog = {'结束': '启动介质成功升级到版本{}'.format(ver)}

    else:
        if err_msg:
            endlog = {'结束': '升级失败，{}'.format(err_msg)}
        else:
            endlog = {'结束': '成功升级到版本{}'.format(ver)}
    user = User.objects.get(username='admin')
    SaveOperationLog(user, OperationLog.TYPE_UPDATE_AIO_BASE, json.dumps(endlog, ensure_ascii=False),
                     get_operator(request))
    return HttpResponse(json.dumps({"r": 0, "e": '操作成功'}, ensure_ascii=False))


def version_handler(request):
    a = request.GET.get('a', 'none')
    if a == 'none':
        a = request.POST.get('a', 'none')
    if a == 'getversion':
        return getVersion(request)
    if a == 'url':
        return getUrl(request)
    if a == 'upload':
        return upload(request)
    if a == 'uploadbyflash':
        return uploadbyflash(request)
    if a == 'seturl':
        return seturl(request)
    if a == 'getlocaldriverlist':
        return getlocaldriverlist(request)
    if a == 'check_is_out_lan':
        return check_is_out_lan(request)
    if a == 'down_and_execute':
        return down_and_execute(request)
    if a == 'getcuttenttaskname':
        return get_current_taskname()
    if a == 'stopcurrentthreading':
        return stop_current_threading()
    if a == 'reportupdate':
        return report_update_ret(request)
    return HttpResponse(json.dumps({"r": "1", "e": "没有对应的action:{}".format(html.escape(a))}))


def _init_authCookies():
    aio_ip = '127.0.0.1'
    username = 'web_api'
    password = 'd24609a757394b40bb838c8f3a378fb1'
    ins = authCookies.AuthCookies(r'http://{}:{}/'.format(aio_ip, 8000), username, password)
    return ins


def report_update_status(update_type=None, err_msg=None):
    if update_type == 'iso':
        ver = '{}.{}'.format(getBigVersion(), getisotime())
    else:
        ver = getAIOVersion()
    params = {'ver': ver, 'update_type': update_type, 'err_msg': err_msg}
    try:
        ins = _init_authCookies()
        secure_cookie, csrf_token, f_url = ins.get(
            r'xdashboard/version_handle/?a=reportupdate')
        rsp1 = requests.post(
            f_url,
            headers={'Content-Type': 'application/x-www-form-urlencoded', 'x-csrftoken': csrf_token},
            data=params,
            cookies=secure_cookie,
            verify=False
        )
    except Exception as e:
        _logger.error('report_update_status Failed.e={}'.format(e))
        return False
    if not status.is_success(rsp1.status_code):
        _logger.error('report_update_status Failed.status_code={}'.format(rsp1.status_code))
        return False
    j = json.loads(rsp1.content.decode('utf-8'))
    if int(j['r']) == 0:
        return True
    _logger.error('report_update_status Failed.r={}'.format(j['r']))
    return False


if __name__ == '__main__':
    argv = sys.argv[1:]
    err_msg = None
    if len(argv) > 1:
        err_msg = argv[1]
    if -1 != argv[0].find("post_update_status", ):
        for i in range(0, 10):
            if report_update_status('aio', err_msg):
                break
            else:
                _logger.error('report_update_status Failed.sleep 30 S')
                time.sleep(30)

    if -1 != argv[0].find("post_iso_update_status", ):
        for i in range(0, 10):
            if report_update_status('iso', err_msg):
                break
            else:
                _logger.error('report_update_status Failed.sleep 30 S')
                time.sleep(30)
