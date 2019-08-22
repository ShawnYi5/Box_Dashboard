# coding=utf-8
from django.http import HttpResponse
import os
import shutil
import zipfile
import html
import tempfile

from xdashboard.common.file_utils import GetFileMd5
from xdashboard.handle.sysSetting.dhcpUtil import DHCPConfigFile
from box_dashboard.boxService import box_service
import json, time
from .version import getAIOVersion, getWinClientPathVer, getLinuxClientX86PathVer, getLinuxClientX64PathVer, \
    get_oem_info
import configparser
from box_dashboard import xlogging
from .systemset import _excute_cmd_and_return_code
from xdashboard.common.functional import hasFunctional
from box_dashboard import xdata

_logger = xlogging.getLogger(__name__)


# 获取脚本文件的当前路径
def cur_file_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def modifyfileText(tfile, sstr, rstr):
    try:
        file_object = open(tfile, 'r')
        lines = file_object.readlines()
        file_object.close()
        flen = len(lines)
        for i in range(flen):
            if sstr in lines[i]:
                lines[i] = lines[i].replace(sstr, rstr)
        file_object = open(tfile, 'w')
        file_object.writelines(lines)
        if hasFunctional('closeagentcompress'):
            file_object.writelines('\nAgent.PostDataQLZ=0')
        file_object.close()
    except Exception as e:
        pass


def getLocalIP():
    serverIp = DHCPConfigFile.getIpAddress()
    return serverIp


def getIPlist(request):
    jsonstr = box_service.getNetworkInfos()
    adapterlist = json.loads(jsonstr)
    if len(adapterlist) != 2 or type(adapterlist) != type([]):
        return HttpResponse('{"r":1,"e":"%s"}' % adapterlist)
    IPList = list()
    for element in adapterlist:
        if (type(element) == type({})):
            for name, adapter in element.items():
                if adapter['ip4']:
                    IPList.append(adapter['ip4'])
                    if adapter['subipset']:
                        IPList.extend([one.split('/')[0] for one in adapter['subipset']])
    return HttpResponse(json.dumps({"r": 0, "list": IPList}, ensure_ascii=False))


def delexpiresetup(exportpath):
    ctime = time.time()
    for dirpath, dirnames, filenames in os.walk(exportpath):
        for filename in filenames:
            thefile = os.path.join(dirpath, filename)
            if not os.path.exists(thefile):
                return
            if os.path.splitext(thefile)[1] in ('.zip', '.ini', '.gz', '.sh') or filename == 'AgentService.config':
                mtime = os.path.getmtime(thefile)
                if ctime - mtime > 10 * 60:
                    os.remove(thefile)


def is_tunnel_md(request):
    mode = request.GET['mod']
    return mode == 'tunnel-md'


# 客户端监听本地IP:Port
def get_ip_port_agent_listen_to(request):
    return request.GET['tunnelip'], request.GET['tunnelport']


def _append_conf_to_file(conf_str, file_path):
    with open(file_path, 'a') as fout:
        fout.write(conf_str)


def genclientini(request, inipath1, inipath2, ip, tunnel_md, patchver=None):
    filepath1 = os.path.join(cur_file_dir(), 'static', 'download', 'client', 'win32', 'src.config')
    # .config文件，覆盖生成
    shutil.copyfile(filepath1, inipath1)
    modifyfileText(inipath1, 'need_change_server_ip', ip)

    # 初始化ini文件配置
    conf = configparser.ConfigParser()
    conf.add_section('client')
    conf.set('client', 'userid', str(request.user.id))
    conf.set('client', 'username', '*{}'.format(request.user.userprofile.user_fingerprint.hex))
    if patchver:
        aiover = '{}(patch{})'.format(getAIOVersion(), patchver)
    else:
        aiover = getAIOVersion()
    conf.set('client', 'ver', aiover)
    conf.set('client', 'timestamp', str(time.time()))

    # ini文件，加入隧道配置
    if tunnel_md:
        _append_conf_to_file('\nSessionFactory.Proxy=agent:ssl -p 20011 -t 30000\n', inipath1)
        _append_conf_to_file('\nSessionFactoryTcp.Proxy=agent:tcp -p 20010 -t 30000\n', inipath1)
        tu_ip, tu_port = get_ip_port_agent_listen_to(request)  # 客户端监听的ip：port
        conf.add_section('tunnel')
        conf.set('tunnel', 'tunnelIP', tu_ip)
        conf.set('tunnel', 'tunnelPort', tu_port)
        conf.set('tunnel', 'proxy_listen', '20010|20011|20002|20003')

    # ini文件，覆盖生成
    with open(inipath2, 'w') as fw:
        conf.write(fw)


def generate_install_success_msg_file(msg):
    _, filename = tempfile.mkstemp()
    with open(filename, mode='wt', encoding='gbk') as fout:
        fout.write(msg)

    return filename


# windows 客户端下载
def download(request):
    oem = get_oem_info()
    uuid = html.escape(request.GET.get('uuid'))
    delexpiresetup(os.path.join(cur_file_dir(), 'static', 'download', 'client', 'win32'))
    delexpiresetup(os.path.join(cur_file_dir(), 'static', 'download', 'client', 'linux32'))
    delexpiresetup(os.path.join(cur_file_dir(), 'static', 'download', 'client', 'linux64'))

    install_success_msg = request.GET.get('install_success_msg', None)
    ip = request.GET.get('ip', '127.0.0.1')  # 直连下，agent访问的ip
    tunnel_md = False
    if is_tunnel_md(request):
        ip = '127.0.0.1'
        tunnel_md = True
    patchver = getWinClientPathVer()
    if patchver:
        version = '{}(patch{})'.format(getAIOVersion(), patchver)
    else:
        version = getAIOVersion()
    user_id = str(request.user.id)
    tmp = request.user.username.split('@')
    username = request.user.username
    if len(tmp) > 1:
        username = tmp[0]
    zipfilename = '{prefix}({username}.{ip}).{version}.zip'.format(
        prefix='{}{}'.format(oem['prefix'], xdata.PREFIX_DR_CLIENT),
        username=username, ip=ip,
        version=version)
    try:
        os.makedirs(os.path.join(cur_file_dir(), 'static', 'download', 'client', 'win32', user_id))
    except OSError as e:
        pass

    # filepath2(.config), filepath3(.ini): 存在则清空
    filepath2 = os.path.join(cur_file_dir(), 'static', 'download', 'client', 'win32', user_id, 'AgentService.config')
    filepath3 = os.path.join(cur_file_dir(), 'static', 'download', 'client', 'win32', user_id, 'AgentService.ini')
    zipfilepath = os.path.join(cur_file_dir(), 'static', 'download', 'client', 'win32', user_id, zipfilename)
    clientfilepath = os.path.join(cur_file_dir(), 'static', 'download', 'client', 'win32', 'setup.exe')

    genclientini(request, filepath2, filepath3, ip, tunnel_md, patchver)

    # 生成zip便于下载
    z = zipfile.ZipFile(zipfilepath, 'w')
    z.write(filepath2, 'AgentService.config')
    z.write(filepath3, 'AgentService.ini')
    z.write(clientfilepath, 'setup.exe')
    if install_success_msg:
        msg_file = generate_install_success_msg_file(install_success_msg)
        z.write(msg_file, 'install_success_message.txt')
        os.remove(msg_file)
    z.close()

    agent_url = '/var/www/static/download/client/win32/' + user_id + '/' + zipfilename
    file_md5 = GetFileMd5(agent_url)
    return HttpResponse(json.dumps({"r": 0, "url": '/static/download/client/win32/' + user_id + '/' + zipfilename,
                                    "uuid": uuid, "version": version, "md5": file_md5}))


# linux 客户端下载
def downloadlinuxclient(request, linuxbit):
    oem = get_oem_info()
    delexpiresetup(os.path.join(cur_file_dir(), 'static', 'download', 'client', 'win32'))
    delexpiresetup(os.path.join(cur_file_dir(), 'static', 'download', 'client', 'linux32'))
    delexpiresetup(os.path.join(cur_file_dir(), 'static', 'download', 'client', 'linux64'))
    uuid = html.escape(request.GET.get('uuid'))
    ip = request.GET.get('ip', '127.0.0.1')  # 直连下，agent访问的ip
    user_id = str(request.user.id)
    if 'linux32' == linuxbit:
        pathchver = getLinuxClientX86PathVer()
    else:
        pathchver = getLinuxClientX64PathVer()
    if pathchver:
        version = '{}_patch{}'.format(getAIOVersion(), pathchver)
    else:
        version = getAIOVersion()

    tunnel_md = False
    if is_tunnel_md(request):
        ip = '127.0.0.1'
        tunnel_md = True
    PREFIX_DR_CLIENT = '{}{}'.format(oem['prefix'], xdata.PREFIX_DR_CLIENT)
    inipath1 = os.path.join(cur_file_dir(), 'static', 'download', 'client', linuxbit, user_id, PREFIX_DR_CLIENT,
                            'AgentService.config')
    inipath2 = os.path.join(cur_file_dir(), 'static', 'download', 'client', linuxbit, user_id, PREFIX_DR_CLIENT,
                            'AgentService.ini')
    tmppath = os.path.join(cur_file_dir(), 'static', 'download', 'client', linuxbit, user_id)

    try:
        shutil.rmtree(tmppath)
    except OSError as e:
        pass

    try:
        os.makedirs(os.path.join(cur_file_dir(), 'static', 'download', 'client', linuxbit))
    except OSError as e:
        pass

    try:
        os.makedirs(tmppath)
    except OSError as e:
        pass

    # 解压安装文件
    if linuxbit == 'linux32':
        cmd_line = 'tar zxvf /var/www/static/download/client/linux/x86/linuxAgentSetup.tar.gz -C /var/www/static/download/client/{}/{}'.format(
            linuxbit,
            user_id)
    else:

        cmd_line = 'tar zxvf /var/www/static/download/client/linux/x64/linuxAgentSetup.tar.gz -C /var/www/static/download/client/{}/{}'.format(
            linuxbit,
            user_id)
    returncode, result_str = _excute_cmd_and_return_code(cmd_line)

    _excute_cmd_and_return_code('\mv -f /var/www/static/download/client/{}/{}/linuxAgentSetup '.format(linuxbit,
                                                                                                   user_id) +
                                '/var/www/static/download/client/{}/{}/{}'.format(linuxbit,
                                                                                  user_id, PREFIX_DR_CLIENT))

    try:
        os.makedirs(os.path.join(tmppath, PREFIX_DR_CLIENT, 'bin'))
    except OSError as e:
        pass

    if linuxbit == 'linux32':
        cmd_line = 'tar zxvf /var/www/static/download/client/linux/x86/linuxAgent.tar.gz -C /var/www/static/download/client/linux32/{}/{}/bin'.format(
            user_id, PREFIX_DR_CLIENT)
    else:
        cmd_line = 'tar zxvf /var/www/static/download/client/linux/x64/linuxAgent.tar.gz -C /var/www/static/download/client/linux64/{}/{}/bin'.format(
            user_id, PREFIX_DR_CLIENT)
    returncode, result_str = _excute_cmd_and_return_code(cmd_line)

    genclientini(request, inipath1, inipath2, ip, tunnel_md, pathchver)

    tmp = request.user.username.split('@')
    username = request.user.username
    if len(tmp) > 1:
        username = tmp[0]
    if linuxbit == 'linux32':
        zipfilename = '{prefix}_{username}.{ip}.{version}.sh'.format(prefix=PREFIX_DR_CLIENT, username=username,
                                                                     ip=ip, version=version)
    else:
        zipfilename = '{prefix}64_{username}.{ip}.{version}.sh'.format(prefix=PREFIX_DR_CLIENT, username=username,
                                                                       ip=ip, version=version)

    cmd_line = r'cd {} && ln -s ./bin/*.so* ./'.format(os.path.join(tmppath, PREFIX_DR_CLIENT))
    returncode, result_str = _excute_cmd_and_return_code(cmd_line)

    # cmd_line = 'cd ' + tmppath + ' && tar -czf "' + zipfilename + '" ClwDRClient'
    cmd_line = 'cd {tmppath} && makeself --gzip --complevel 1 --target /opt/{prefix}_{version} ./{prefix} {shfilename}  "{prefix} Setup" ./install'.format(
        tmppath=tmppath, prefix=PREFIX_DR_CLIENT, version=version, shfilename=zipfilename)
    returncode, result_str = _excute_cmd_and_return_code(cmd_line)

    try:
        shutil.rmtree(os.path.join(tmppath, PREFIX_DR_CLIENT))
    except Exception as e:
        _logger.error('remove path:{} fail:{}'.format(tmppath, e))
    agent_url = '/var/www/static/download/client/{}/'.format(linuxbit) + user_id + '/' + zipfilename
    file_md5 = GetFileMd5(agent_url)
    return HttpResponse(
        json.dumps({"r": 0, "url": '/static/download/client/{}/'.format(linuxbit) + user_id + '/' + zipfilename,
                    "uuid": uuid, "version": version, "md5": file_md5}))


def downloadlinuxcleint32(request):
    return downloadlinuxclient(request, 'linux32')


def downloadlinuxcleint64(request):
    return downloadlinuxclient(request, 'linux64')


def delete_user_packages(request):
    """
    删除用户安装包: Agent, PE
    """
    user_id, agent_types = str(request.user.id), ['win32', 'linux32', 'linux64']
    package_dirs = [os.path.join(xdata.BASE_STATIC_PATH, 'download', 'client', _type) for _type in agent_types]
    for package_dir in package_dirs:
        package_path = os.path.join(package_dir, user_id)
        if not os.path.exists(package_path):
            _logger.warning('delete_user_packages, not exists {}, passing'.format(package_path))
            continue
        shutil.rmtree(package_path, ignore_errors=True)
        _logger.warning('delete_user_packages, deleted {}'.format(package_path))

    pe_dir = xdata.WIN_PE_NEW_PATH
    package_path = os.path.join(pe_dir, user_id)
    if os.path.exists(package_path) and os.listdir(package_path):
        for file_name in os.listdir(package_path):
            if not file_name.endswith('.iso'):
                continue
            pe_iso = os.path.join(package_path, file_name)
            if os.path.islink(pe_iso):
                link_file, target_file = pe_iso, os.path.realpath(pe_iso)
                os.unlink(link_file)
                os.remove(target_file)
            else:
                os.remove(pe_iso)
            _logger.warning('delete_user_packages, deleted {}'.format(pe_iso))
    else:
        _logger.warning('delete_user_packages, not exists {}, or is empty'.format(package_path))

    return HttpResponse(json.dumps({'r': 0, 'e': 'ok'}))


def download_handler(request):
    a = request.GET.get('a', 'none')
    if a == 'none':
        a = request.POST.get('a', 'none')
    if a == 'winclient':
        return download(request)
    if a == 'linuxclient32':
        return downloadlinuxcleint32(request)
    if a == 'linuxclient64':
        return downloadlinuxcleint64(request)
    if a == 'getiplist':
        return getIPlist(request)
    if a == 'delete_user_packages':
        return delete_user_packages(request)
    return HttpResponse(json.dumps({"r": "1", "e": "没有对应的action:{}".format(html.escape(a))}))
