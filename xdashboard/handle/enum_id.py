import os
import sys
import traceback
import time
import requests
import subprocess
import configparser
import json
import re

try:
    import win32api
    import win32con
    import win32gui
    import win32file
except ImportError:
    pass
try:
    from box_dashboard import xlogging
except ImportError:
    import logging as xlogging
# import chardet
import re
import sqlite3
import shutil
import zipfile
import hashlib

# =========================================================================================
_logger = xlogging.getLogger(__name__)
g_del = 0
# g_type:1正常扫描数据。
# g_type:2从服务器上下载，产生的数据。
g_type = 1
g_db_path = '/var/db/drvierid.db'
g_driver_pool = '/home/aio/driver_pool'

# 0：未调试。 1：调试。
g_dbg = 1
g_dbg_num = 1  # 计数
g_NotUserDriverTmpDir = 0
# scan all class id
g_jiessie_test = 0

# system_cat_name_list = ['Server10_X64', '10_X64', '10_X86',
#                         'Server6_3_X64','6_3_X64','6_3_X86',
#                         'Server8_X64','8_X64','8_X86',
#                         'Server2008R2_X64','7_X64','7_X86',
#                         'Server2008_X64','Server2008_X86','Vista_X64','Vista_X86',
#                         'Server2003_X64','Server2003_X86','XP_X64','XP_X86','2k']

g_os_name_list = [
    {'os_name': "Server10_X64", 'major': 10, 'min': 0, 'bIs64': 1},
    {'os_name': "10_X64", 'major': 10, 'min': 0, 'bIs64': 1},
    {'os_name': "10_X86", 'major': 10, 'min': 0, 'bIs64': 0},

    {'os_name': "Server6_3_X64", 'major': 6, 'min': 3, 'bIs64': 1},
    {'os_name': "6_3_X64", 'major': 6, 'min': 3, 'bIs64': 1},
    {'os_name': "6_3_X86", 'major': 6, 'min': 3, 'bIs64': 0},

    {'os_name': "Server8_X64", 'major': 6, 'min': 2, 'bIs64': 1},
    {'os_name': "8_X64", 'major': 6, 'min': 2, 'bIs64': 1},
    {'os_name': "8_X86", 'major': 6, 'min': 2, 'bIs64': 0},

    {'os_name': "Server2008R2_X64", 'major': 6, 'min': 1, 'bIs64': 1},
    {'os_name': "7_X64", 'major': 6, 'min': 1, 'bIs64': 1},
    {'os_name': "7_X86", 'major': 6, 'min': 1, 'bIs64': 0},

    {'os_name': "Server2008_X64", 'major': 6, 'min': 0, 'bIs64': 1},
    {'os_name': "Server2008_X86", 'major': 6, 'min': 0, 'bIs64': 0},
    {'os_name': "Vista_X64", 'major': 6, 'min': 0, 'bIs64': 1},
    {'os_name': "Vista_X86", 'major': 6, 'min': 0, 'bIs64': 0},

    {'os_name': "Server2003_X64", 'major': 5, 'min': 2, 'bIs64': 1},
    {'os_name': "Server2003_X86", 'major': 5, 'min': 2, 'bIs64': 0},
    {'os_name': "XP_X64", 'major': 5, 'min': 2, 'bIs64': 1},
    {'os_name': "XP_X86", 'major': 5, 'min': 1, 'bIs64': 0},

    {'os_name': "2000", 'major': 5, 'min': 0, 'bIs64': 0}
]


# =========================================================================================
def IsNewSystemCatNameList(one_name):
    try:
        re1 = r'\d\d.\d\d.\d\d'
        com1 = re.compile(re1)
        ret1 = com1.search(one_name)
        if ret1 is not None:
            return True
        return False
    except:
        _logger.error(traceback.format_exc())
        return False


def cur_file_dir():
    try:
        # 获取脚本路径
        path = sys.path[0]
        # 判断为脚本文件还是py2exe编译后的文件，如果是脚本文件，则返回的是脚本的目录，如果是py2exe编译后的文件，则返回的是编译后的文件路径
        if os.path.isdir(path):
            _logger.debug("cur_file_dir = %s" % (path))
            return path
        elif os.path.isfile(path):
            _logger.debug("cur_file_dir = %s" % (os.path.dirname(path)))
            return os.path.dirname(path)
    except:
        _logger.error(traceback.format_exc())


def show_and_exe_cmd_line_and_get_ret(in_cmd_line, chk_err_str=''):
    try:
        cmd_line = in_cmd_line
        # _logger.debug(cmd_line)
        with os.popen(cmd_line) as out_put:
            out_put_lines = out_put.readlines()
            if '' == chk_err_str:
                # _logger.debug('0'), _logger.debug(out_put_lines)
                return 0, out_put_lines
            for one_line in out_put_lines:
                if -1 != one_line.find(chk_err_str):
                    # _logger.debug('-1'), _logger.debug(out_put_lines)
                    return -1, []
        # _logger.debug('0'), _logger.debug(out_put_lines)
        return 0, out_put_lines
    except:
        _logger.error(traceback.format_exc())
        # _logger.error('-1'), _logger.error(out_put_lines)
        return -1, []


def _excute_cmd_and_return_code(cmd):
    _logger.info("_excute_cmd_and_return_code cmd:{}".format(cmd))
    with subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True) as p:
        stdout, stderr = p.communicate()
    if p.returncode != 0:
        return p.returncode, stderr
    else:
        return p.returncode, stdout


# hash_obj = hashlib.md5()
# hash_obj = hashlib.sha1()
# hash_obj = hashlib.sha224()
# hash_obj = hashlib.sha256()
# hash_obj = hashlib.sha384()
# hash_obj = hashlib.sha512()
def HashOneFile(filepath, hash_obj):
    try:
        with open(filepath, 'rb') as f:
            while True:
                str = f.read()
                if 0 == len(str):
                    break;
                hash_obj.update(str)
            hash = hash_obj.hexdigest()
            return hash
        return None
    except:
        _logger.error(traceback.format_exc())
        return None


def HashFileList(filelist, hash_obj):
    try:
        for one_file in filelist:
            with open(one_file, 'rb') as f:
                while True:
                    str = f.read()
                    if 0 == len(str):
                        break;
                    hash_obj.update(str)
        hash = hash_obj.hexdigest()
        return hash
    except:
        _logger.error(traceback.format_exc())
        return None


def HashDir(dir, hash_obj, bSort=True):
    try:
        filelist = list()
        for root, dirs, files in os.walk(dir):
            for file in files:
                filelist.append(os.path.join(root, file))
        if bSort:
            filelist.sort()
        return HashFileList(filelist, hash_obj)
    except:
        _logger.error(traceback.format_exc())
        return None


def str2sql(str_value):
    if str_value is None:
        return " '' "
    if 0 == len(str_value):
        return " '' "
    return " '" + str_value + "' "


def str2sql_and(str_value, bHaveAnd=True):
    if bHaveAnd:
        if str_value is None:
            return " ,'' "
        if 0 == len(str_value):
            return " ,'' "
        return " , '" + str_value + "' "
    else:
        if str_value is None:
            return " '' "
        if 0 == len(str_value):
            return " '' "
        return " '" + str_value + "' "


def int2sql(num):
    if num is None:
        return " 0 "
    return " " + str(num) + " "


def int2sql_and(num, bHaveAnd=True):
    if bHaveAnd:
        if num is None:
            return " ,0 "
        return " , " + str(num) + " "
    else:
        if num is None:
            return " 0 "
        return " " + str(num) + " "


def strAname2sql(name, str_value):
    if name is None or str_value is None:
        return " "
    if 0 == len(name):
        return " "
    return " " + name + "='" + str_value + "' "


def strAname2sql_and(name, str_value, bHaveAnd=True):
    if name is None:
        return ' '
    if 0 == len(name):
        return ' '
    if bHaveAnd:
        if str_value is None:
            return " and " + name + "='" + "' "
        if 0 == len(str_value):
            return " and " + name + "='" + "' "
        return " and " + name + "='" + str_value + "' "
    else:
        if str_value is None:
            return " " + name + "='" + "' "
        if 0 == len(str_value):
            return " " + name + "='" + "' "
        return " " + name + "='" + str_value + "' "


def intAname2sql(name, num):
    if name is None or num is None:
        return " "
    if 0 == len(name):
        return " "
    return " " + name + "=" + str(num) + " "


def intAname2sql_and(name, num, bHaveAnd=True):
    if name is None:
        return ' '
    if 0 == len(name):
        return ' '
    if bHaveAnd:
        if num is None:
            return " and " + name + "=" + str(0) + " "
        return " and " + name + "=" + str(num) + " "
    else:
        if num is None:
            return " " + name + "=" + str(0) + " "
        return " " + name + "=" + str(num) + " "


def gen_sql_time():
    return " datetime('now','localtime') "


def gen_sql_time_and(bHaveAnd=True):
    if bHaveAnd:
        return " ,datetime('now','localtime') "
    else:
        return " datetime('now','localtime') "


def name_gen_sql_time(name):
    if name is None:
        return " "
    if 0 == len(name):
        return " "
    return " " + name + "=datetime('now','localtime') "


def name_gen_sql_time_and(name, bHaveAnd=True):
    if name is None:
        return " "
    if 0 == len(name):
        return " "
    if bHaveAnd:
        return " and " + name + "=datetime('now','localtime') "
    else:
        return " " + name + "=datetime('now','localtime') "


def zip_dir(dirname, zipfilename):
    _logger.debug('zip_dir dirname={}, zipfilename={}'.format(dirname, zipfilename))
    filelist = []
    if os.path.isfile(dirname):
        filelist.append(dirname)
    else:
        for root, dirs, files in os.walk(dirname):
            for name in files:
                filelist.append(os.path.join(root, name))

    zf = zipfile.ZipFile(zipfilename, "w", zipfile.zlib.DEFLATED)
    for tar in filelist:
        arcname = tar[len(dirname):]
        # print arcname
        zf.write(tar, arcname)
    zf.close()


def unzip_file(zipfilename, unziptodir):
    _logger.debug('zipfilename={}, unziptodir={}'.format(zipfilename, unziptodir))
    if not os.path.exists(unziptodir):
        os.mkdir(unziptodir, 0o777)
    zfobj = zipfile.ZipFile(zipfilename)
    for name in zfobj.namelist():
        name = name.replace('\\', '/')

        if name.endswith('/'):
            os.mkdir(os.path.join(unziptodir, name))
        else:
            ext_filename = os.path.join(unziptodir, name)
            ext_dir = os.path.dirname(ext_filename)
            if not os.path.exists(ext_dir): os.mkdir(ext_dir, 0o777)
            outfile = open(ext_filename, 'wb')
            outfile.write(zfobj.read(name))
            outfile.close()


class CEnumIDByInfDir(object):
    def __init__(self):
        self.class_guid_list = ['{4d36e96a-e325-11ce-bfc1-08002be10318}', '{4d36e972-e325-11ce-bfc1-08002be10318}',
                                '{4d36e97b-e325-11ce-bfc1-08002be10318}']

    def str2sql(self, str_value):
        if str_value is None:
            return " '' "
        _logger.debug(str_value)
        return " '" + str_value + "' "

    def int2sql(self, num):
        if num is None:
            return " '' "
        return " " + str(num) + " "

    def strAname2sql(self, name, str_value):
        if name is None or str_value is None:
            return " "
        return " " + name + "='" + str_value + "' "

    def intAname2sql(self, name, num):
        if name is None or num is None:
            return " "
        return " " + name + "=" + str(num) + " "

    def get_dev(self, hard_or_comp_id):
        try:
            re_rule = r'\bDEV_\w*'
            com = re.compile(re_rule)
            ret = com.search(hard_or_comp_id.upper())
            if ret is not None:
                return ret.group(0)
            return ''
        except Exception as e:
            return ''

    def get_ven(self, hard_or_comp_id):
        try:
            re_rule = r'\bVEN_\w*'
            com = re.compile(re_rule)
            ret = com.search(hard_or_comp_id.upper())
            if ret is not None:
                return ret.group(0)
            return ''
        except Exception as e:
            return ''

    # 插入数据之后，需要设置删除标示，del=1,以便对比后来的ok_dev.txt进行处理
    def wirte_one_to_tmp_db(self, system_name, cu, one, GetClassGudi, inf_driver_ver, HWPlatform=0, bSpec=True):
        global g_del
        global g_type
        global g_dbg
        global g_dbg_num
        global g_NotUserDriverTmpDir
        try:
            if g_NotUserDriverTmpDir != 1:
                cmd = "select * from tmp_inf_dir_2_hash where " + strAname2sql('inf_dir',
                                                                               os.path.dirname(one['inf_path']))
                # print(cmd)
                cu.execute(cmd)
                # 数据库构建时，保持了唯一性
                one_db = cu.fetchone()
                if one_db is None:
                    return
                # if -1 == one['inf_path'].find(system_name):
                #     pass
                zip_path = one_db[2] + '.zip'
                #
                cmd = "select * from tmp_id_table where " \
                      + strAname2sql('hard_or_comp_id', one['hard_or_comp_id']) + ' and ' \
                      + strAname2sql('zip_path', zip_path) + ' and ' \
                      + strAname2sql('system_name', one['system_name'] + ' and ' \
                                     + intAname2sql('HWPlatform', one['HWPlatform']))
                # print(cmd)
                cu.execute(cmd)
                one_db = cu.fetchone()
                if one_db is None:
                    # 插入数据之后，需要设置删除标示，del=1,以便对比后来的ok_dev.txt进行处理
                    cmd = "insert into tmp_id_table (server_id,del,type,time,class_guid,hard_or_comp_id,ven,dev,inf_driver_ver,inf_path,zip_path,show_name,system_name,score,HWPlatform,e_i_1,e_i_2) values (" \
                          + int2sql(g_dbg_num) + "," + int2sql(1) + "," + int2sql(g_type) + "," \
                          + gen_sql_time() + "," \
                          + str2sql(GetClassGudi) + "," + str2sql(one['hard_or_comp_id']) + "," \
                          + str2sql(one['ven']) + "," + str2sql(one['dev']) + "," \
                          + int2sql(inf_driver_ver) + "," + str2sql(one['inf_path']) + "," \
                          + str2sql(zip_path) + "," + str2sql(one['show_name']) + "," \
                          + str2sql(one['system_name']) + "," + int2sql(one['score']) + "," + int2sql(HWPlatform) \
                          + "," + int2sql(one['e_i_1']) + "," + int2sql(one['e_i_2']) + ")"
                    g_dbg_num += 1
                    # _logger.debug(cmd)
                    cu.execute(cmd)
            else:
                # 插入数据之后，需要设置删除标示，del=1,以便对比后来的ok_dev.txt进行处理
                cmd = "insert into tmp_id_table (server_id,del,type,time,class_guid,hard_or_comp_id,ven,dev,inf_driver_ver,inf_path,show_name,system_name,score,HWPlatform,e_i_1,e_i_2) values (" \
                      + int2sql(g_dbg_num) + "," + int2sql(1) + "," + int2sql(g_type) + "," \
                      + gen_sql_time() + "," \
                      + str2sql(GetClassGudi) + "," + str2sql(one['hard_or_comp_id']) + "," \
                      + str2sql(one['ven']) + "," + str2sql(one['dev']) + "," \
                      + int2sql(inf_driver_ver) + "," + str2sql(one['inf_path']) + "," \
                      + str2sql(one['show_name']) + "," + str2sql(one['system_name']) + "," \
                      + int2sql(one['score']) + "," + int2sql(HWPlatform) \
                      + "," + int2sql(one['e_i_1']) + "," + int2sql(one['e_i_2']) + ")"
                g_dbg_num += 1
                # _logger.debug(cmd)
                cu.execute(cmd)
        except Exception as e:
            print(traceback.format_exc())

    def write_all_list_to_tmp_db(self, system_name, cu, get_one_list, db_path, GetClassGudi, inf_driver_ver,
                                 HWPlatform=0, bSpec=True):
        try:
            _logger.debug("write_all_list_to_db len of list = {}".format(len(get_one_list)))
            for i in get_one_list:
                if i['hard_or_comp_id'] is None:
                    continue
                if 0 == len(i['hard_or_comp_id']):
                    continue
                self.wirte_one_to_tmp_db(system_name, cu, i, GetClassGudi, inf_driver_ver, HWPlatform, bSpec)
        except Exception as e:
            _logger.error(traceback.format_exc())
            return None

    def HaveCat(self, inf_path):
        try:
            dir_name = os.path.dirname(inf_path)
            file_list = os.listdir(dir_name)
            for one in file_list:
                if one.upper().endswith('.CAT'):
                    return True
            return False
        except Exception as e:
            _logger.error(traceback.format_exc())
            return False

    def ChkIsSha2562008R2(self, file_path):
        try:
            cmd_line = 'signtool.exe' + ' verify /kp "' + file_path + '"'
            ret, lines = show_and_exe_cmd_line_and_get_ret(cmd_line)
            if lines is not None:
                for one_line in lines:
                    if -1 != one_line.find('sha256'):
                        return True
            return False

        except Exception as e:
            self.logger.warning(r'return error. {}'.format(e))
            return False

    # {"os_ven": "NTx86", "ShowName": "vmxnet3 Ethernet Adapter", "DeviceID": "PCI\VEN_15AD&DEV_07B0"}
    # {"os_ven": "NTx86", "ClassGUID": "{4d36e972-e325-11ce-bfc1-08002be10318}"}
    # {"os_ven": "NTx86", "Class": "Net"}
    # {"os_ven": "NTx86", "DriverVer": "08/28/2013,1.5.2.0"}
    # {"os_ven": "NTx86", "NeedFile": "vmxnet3ndis5.cat"}
    # {"os_ven": "NTx86", "NeedFile": "vmxnet3n51x86.sys"}
    # {"os_ven": "NTx86", "NeedFile": "vmxnet3n51x64.sys"}
    # {"os_ven": "NTx86.6.0.1", "ClassGUID": "{4d36e972-e325-11ce-bfc1-08002be10318}"}
    # {"os_ven": "NTx86.6.0.1", "Class": "Net"}
    # {"os_ven": "NTx86.6.0.1", "DriverVer": "08/28/2013,1.5.2.0"}
    # {"os_ven": "NTx86.6.0.1", "NeedFile": "vmxnet3ndis5.cat"}
    # {"os_ven": "NTx86.6.0.1", "NeedFile": "vmxnet3n51x86.sys"}
    # {"os_ven": "NTx86.6.0.1", "NeedFile": "vmxnet3n51x64.sys"}
    def get_one_inf_id_and_class(self, system_name, driver_pool, inf_path, HWPlatform, bNoGuid, bSpec, SpecType,
                                 bCheckSig):
        try:
            GetClassGudi = None
            get_one_list = list()
            inf_driver_ver = None
            score = 1
            one_result = {'hard_or_comp_id': '', 'ven': '', 'dev': '', 'inf_path': '', 'show_name': '',
                          'system_name': '', 'HWPlatform': HWPlatform, 'score': 0, 'e_i_1': 0, 'e_i_2': 0}

            if bSpec:
                if SpecType:
                    ret, lines = show_and_exe_cmd_line_and_get_ret(
                        'inf.exe "' + inf_path + '" ' + system_name + ' must ')
                else:
                    ret, lines = show_and_exe_cmd_line_and_get_ret('inf.exe "' + inf_path + '" ' + system_name)
            else:
                ret, lines = show_and_exe_cmd_line_and_get_ret('inf.exe "' + inf_path)

            if self.HaveCat(inf_path) is not True:
                score = 0
                one_result['e_i_1'] = one_result['e_i_1'] | 2
            for one_line in lines:
                get_one_str = one_line.replace('\\', '\\\\')
                get_ret_list = json.loads(get_one_str)
                if 'NeedFile' in get_ret_list.keys():
                    need_file_name = get_ret_list['NeedFile']
                    ext = os.path.splitext(need_file_name)[1].upper()
                    need_file_path = os.path.dirname(inf_path)
                    need_file_path = os.path.join(need_file_path, need_file_name)
                    if os.path.exists(need_file_path) is not True:
                        with open(os.path.join(driver_pool, "scan_no_file.log"), 'w') as err_handle:
                            err_handle.write('no file={}\n'.format(need_file_path))
                    if bCheckSig is True:
                        if ext == '.SYS':
                            no_sig_file_path = need_file_path + '.nosig'
                            sha1_file_path = need_file_path + '.sha1'
                            sha256_file_path = need_file_path + '.sha256'
                            if os.path.exists(no_sig_file_path):
                                score = 0
                                one_result['e_i_1'] = one_result['e_i_1'] | 1
                            elif os.path.exists(sha256_file_path):
                                one_result['e_i_2'] = 1
                            elif os.path.exists(sha1_file_path):
                                one_result['e_i_2'] = 0
                            elif os.path.exists(need_file_path):
                                ret_tmp, lines_tmp = show_and_exe_cmd_line_and_get_ret(
                                    'ChkSig.exe "' + need_file_path + '"')
                                for one_line_tmp in lines_tmp:
                                    if -1 != one_line_tmp.find('ChkSig NO_SIG'):
                                        score = 0
                                        one_result['e_i_1'] = one_result['e_i_1'] | 1
                                        create_null_file(no_sig_file_path)
                                    elif -1 != one_line_tmp.find('ChkSig SHA256'):
                                        one_result['e_i_2'] = 1
                                        # if self.ChkIsSha2562008R2(need_file_path):
                                        #     one_result['e_i_2'] = 1
                                        create_null_file(sha256_file_path)
                                    elif -1 != one_line_tmp.find('ChkSig SHA1'):
                                        one_result['e_i_2'] = 0
                                        create_null_file(sha1_file_path)

                if 'ClassGUID' in get_ret_list.keys():
                    if bNoGuid:
                        GetClassGudi = get_ret_list['ClassGUID']
                    else:
                        for one_guid in self.class_guid_list:
                            if -1 != get_ret_list['ClassGUID'].lower().find(one_guid):
                                GetClassGudi = one_guid

                if 'DriverVer' in get_ret_list.keys():
                    str_inf_of_time = get_ret_list['DriverVer']
                    find_tmp_num = str_inf_of_time.find("/")
                    if find_tmp_num >= 2:
                        mon = int(str_inf_of_time[find_tmp_num - 2:find_tmp_num])
                    else:
                        mon = int(str_inf_of_time[0:find_tmp_num])
                    str_inf_of_time = str_inf_of_time[str_inf_of_time.find("/") + 1:]
                    day = int(str_inf_of_time[0:str_inf_of_time.find("/")])
                    start = str_inf_of_time.find("/") + 1
                    end = str_inf_of_time.find(",")
                    if -1 == end:
                        year = int(str_inf_of_time[start:])
                    else:
                        year = int(str_inf_of_time[start:end])
                    if year < 100:
                        year = 1900 + year
                    inf_driver_ver = year * 10000 + mon * 100 + day
                if 'ShowName' in get_ret_list.keys() and 'DeviceID' in get_ret_list.keys() and 'os_ven' in get_ret_list.keys():
                    one_result['show_name'] = get_ret_list['ShowName']
                    one_result['show_name'] = one_result['show_name'].replace('"', ' ')
                    one_result['show_name'] = one_result['show_name'].replace("'", ' ')
                    device_id = get_ret_list['DeviceID']
                    device_id = device_id.strip()
                    device_id = device_id.upper()
                    one_result['hard_or_comp_id'] = device_id
                    one_result['ven'] = self.get_ven(device_id)
                    one_result['dev'] = self.get_dev(device_id)
                    one_result['inf_path'] = inf_path[len(driver_pool) + 1:]
                    one_result['inf_path'] = one_result['inf_path'].replace('\\', '/')
                    one_result['score'] = score
                    one_result['system_name'] = get_ret_list['os_ven'].upper()
                    get_one_list.append(one_result.copy())

            return GetClassGudi, inf_driver_ver, get_one_list
        except:
            _logger.error(traceback.format_exc())

    def get_one_inf_id_and_class_to_tmp_db(self, system_name, cu, driver_pool, db_path, inf_path, HWPlatform,
                                           bNoGuid, bSpec, SpecType):
        try:
            GetClassGudi, inf_driver_ver, get_one_list = self.get_one_inf_id_and_class(
                system_name, driver_pool, inf_path, HWPlatform, bNoGuid, bSpec, SpecType, True)
            if GetClassGudi is not None and inf_driver_ver is not None:
                # _logger.debug("get_one_inf_id_and_class_python 2 class_guid = {}".format(class_guid))
                self.write_all_list_to_tmp_db(system_name, cu, get_one_list, db_path, GetClassGudi, inf_driver_ver,
                                              HWPlatform, bSpec)
        except:
            _logger.error(traceback.format_exc())

    def get_one_dir_all_inf_id_and_class_to_tmp_db(self, system_name, cu, driver_pool, dir_path, db_path, HWPlatform,
                                                   bNoGuid, bSpec, SpecType):
        try:
            for root, dirs, files in os.walk(dir_path):
                for file in files:
                    if file.lower().endswith('.inf'):
                        inf_path = os.path.join(root, file)
                        print(r'get_one_dir_all_inf_id_and_class_to_tmp_db search name = {}'.format(inf_path))
                        self.get_one_inf_id_and_class_to_tmp_db(system_name, cu, driver_pool, db_path, inf_path,
                                                                HWPlatform, bNoGuid, bSpec, SpecType)
        except Exception as e:
            print(traceback.format_exc())
            return None

    def get_all_HWPlatform_okdev_to_tmp_db(self, system_name, cu, driver_pool, db_path, bNoGuid, bSpec, SpecType):
        try:
            my_dir_path = os.path.join(driver_pool, system_name)
            get_file_in_dir_list = os.listdir(my_dir_path)
            for one_file in get_file_in_dir_list:
                if one_file.lower() == 'hwplatform':
                    hw_home_full_path = os.path.join(my_dir_path, one_file)
                    hw_list_file = os.listdir(hw_home_full_path)
                    for one_hw in hw_list_file:
                        hw_full_path = os.path.join(hw_home_full_path, one_hw)
                        if os.path.isdir(hw_full_path):
                            try:
                                self.get_one_dir_all_inf_id_and_class_to_tmp_db(system_name, cu, driver_pool,
                                                                                hw_full_path, db_path, int(one_hw),
                                                                                bNoGuid, bSpec, SpecType)
                            except Exception as e:
                                print(traceback.format_exc())
                elif one_file.lower() == 'ok_dev.txt':
                    # 读取ok_dev.txt
                    all_ok_file_path = os.path.join(my_dir_path, one_file)
                    read_deviceid_file_to_db(system_name, cu, all_ok_file_path, 'tmp_all_ok')
                    pass
                else:
                    search_sub_dir_full_path = os.path.join(my_dir_path, one_file)
                    if os.path.isdir(search_sub_dir_full_path):
                        self.get_one_dir_all_inf_id_and_class_to_tmp_db(system_name, cu, driver_pool,
                                                                        search_sub_dir_full_path,
                                                                        db_path, 0, bNoGuid, bSpec, SpecType)
                        # # 读取ok_dev.txt
                        all_ok_file_path = os.path.join(os.path.join(my_dir_path, one_file), 'ok_dev.txt')
                        if os.path.exists(all_ok_file_path):
                            read_deviceid_file_to_db(system_name, cu, all_ok_file_path, 'tmp_all_ok')

        except Exception as e:
            print(traceback.format_exc())
            return None

    def get_inf_driver_ver_by_dev_id(self, device_id, hard_id_list, inf_driver_ver):
        try:
            for one in hard_id_list:
                try:
                    if (one[0] == device_id) and (-1 != one[1].find(str(inf_driver_ver))):
                        return one[1]
                except:
                    pass
            return None
        except Exception as e:
            print(traceback.format_exc())
            return None

    def NewGetOSNameList(self, inf_sys_name):
        try:
            bFindOldVer = False
            for one_os_name in g_os_name_list:
                if one_os_name['os_name'] == inf_sys_name:
                    # 老版本号被找到。兼容处理。
                    bFindOldVer = True
                    return one_os_name['major'], one_os_name['min'], one_os_name['bIs64']
            if bFindOldVer is not True:
                # 老版本号未找到。采用新版本号处理。
                ver_list = inf_sys_name.split('.')
                if 0 == int(ver_list[2]):
                    return int(ver_list[0]), int(ver_list[1]), 0
                elif 9 == int(ver_list[2]):
                    return int(ver_list[0]), int(ver_list[1]), 1
                return None, None, None
            return None, None, None
        except:
            _logger.error(traceback.format_exc())  # 生成数据库
            return None, None, None

    def __check_near_and_get_db_system_name_by_hash(self, cu, one_id, system_name, inf_driver_ver, hash):
        try:
            # 判定此ID是否平台驱动
            ret_list = []
            cmd = "select count(*) from id_table where  del=0 and hard_or_comp_id='" + one_id \
                  + "' and HWPlatform <> 0 and inf_driver_ver=" + str(
                inf_driver_ver) + " and zip_path='" + hash + ".zip'"
            _logger.debug(cmd)
            cu.execute(cmd)
            one_db = cu.fetchone()
            if one_db is None:
                return ret_list
            if one_db[0] != 0:
                # 平台驱动。只能扫描固定平台。
                db_system_name = self.GetDBSysNameByInfSysName(system_name)
                if db_system_name is None:
                    return ret_list
                cmd = "select * from id_table where " + self.strAname2sql('hard_or_comp_id', one_id) \
                      + "and" + self.intAname2sql('del', 0) + "and system_name like '" + db_system_name \
                      + "%' and inf_driver_ver=" + str(inf_driver_ver) + " and zip_path='" + hash + ".zip'"
                _logger.debug(cmd)
                cu.execute(cmd)
                one_db = cu.fetchone()
                if one_db is not None:
                    ret_list.append((one_db[13], 0))
                return ret_list

            # 非平台驱动。
            inf_max, inf_min, inf_bIs64 = self.NewGetOSNameList(system_name)
            if inf_max is not None:
                # ============================================================================================
                # 依次递减小版本号进行匹配。
                for i in range(inf_min, -1, -1):
                    if 0 == inf_bIs64:
                        db_system_name = 'NTX86.' + str(inf_max) + '.' + str(i)
                    else:
                        db_system_name = 'NTAMD64.' + str(inf_max) + '.' + str(i)
                    cmd = "select * from id_table where " + self.strAname2sql('hard_or_comp_id', one_id) \
                          + "and" + self.intAname2sql('del', 0) + "and system_name like '" + db_system_name \
                          + "%' and inf_driver_ver=" + str(inf_driver_ver) + " and zip_path='" + hash + ".zip'"
                    _logger.debug(cmd)
                    cu.execute(cmd)
                    one_db = cu.fetchone()
                    if one_db is not None:
                        ret_list.append((one_db[13], inf_min - i))

                    if 0 == inf_bIs64:
                        db_system_name = 'NT.' + str(inf_max) + '.' + str(i)
                        cmd = "select * from id_table where " + self.strAname2sql('hard_or_comp_id', one_id) \
                              + "and" + self.intAname2sql('del', 0) + "and system_name like '" + db_system_name \
                              + "%' and inf_driver_ver=" + str(inf_driver_ver) + " and zip_path='" + hash + ".zip'"
                        _logger.debug(cmd)
                        cu.execute(cmd)
                        one_db = cu.fetchone()
                        if one_db is not None:
                            ret_list.append((one_db[13], inf_min - i))

                # ============================================================================================
                # 进行等于 NTAMD64.6. 匹配，不能包含避免出现未匹配的 大于当前小版本号。NTAMD64.6.20
                if 0 == inf_bIs64:
                    db_system_name = 'NTX86.' + str(inf_max) + '.'
                else:
                    db_system_name = 'NTAMD64.' + str(inf_max) + '.'
                cmd = "select * from id_table where " + self.strAname2sql('hard_or_comp_id', one_id) \
                      + "and" + self.intAname2sql('del', 0) + "and" \
                      + self.strAname2sql('system_name', db_system_name) \
                      + "and inf_driver_ver=" + str(inf_driver_ver) + " and zip_path='" + hash + ".zip'"
                _logger.debug(cmd)
                cu.execute(cmd)
                one_db = cu.fetchone()
                if one_db is not None:
                    ret_list.append((one_db[13], inf_min - 0))

                if 0 == inf_bIs64:
                    db_system_name = 'NT.' + str(inf_max) + '.'
                    cmd = "select * from id_table where " + self.strAname2sql('hard_or_comp_id', one_id) \
                          + "and" + self.intAname2sql('del', 0) + "and" \
                          + self.strAname2sql('system_name', db_system_name) \
                          + " and inf_driver_ver=" + str(inf_driver_ver) + " and zip_path='" + hash + ".zip'"
                    _logger.debug(cmd)
                    cu.execute(cmd)
                    one_db = cu.fetchone()
                    if one_db is not None:
                        ret_list.append((one_db[13], inf_min - 0))

                # 进行等于 NTAMD64.6 匹配，不能包含避免出现未匹配的 NTAMD64.6.20
                if 0 == inf_bIs64:
                    db_system_name = 'NTX86.' + str(inf_max)
                else:
                    db_system_name = 'NTAMD64.' + str(inf_max)
                cmd = "select * from id_table where " + self.strAname2sql('hard_or_comp_id', one_id) \
                      + "and" + self.intAname2sql('del', 0) + "and" \
                      + self.strAname2sql('system_name', db_system_name) \
                      + " and inf_driver_ver=" + str(inf_driver_ver) + " and zip_path='" + hash + ".zip'"
                _logger.debug(cmd)
                cu.execute(cmd)
                one_db = cu.fetchone()
                if one_db is not None:
                    ret_list.append((one_db[13], inf_min - 0))

                if 0 == inf_bIs64:
                    db_system_name = 'NT.' + str(inf_max)
                    cmd = "select * from id_table where " + self.strAname2sql('hard_or_comp_id', one_id) \
                          + "and" + self.intAname2sql('del', 0) + "and" \
                          + self.strAname2sql('system_name', db_system_name) \
                          + " and inf_driver_ver=" + str(inf_driver_ver) + " and zip_path='" + hash + ".zip'"
                    _logger.debug(cmd)
                    cu.execute(cmd)
                    one_db = cu.fetchone()
                    if one_db is not None:
                        ret_list.append((one_db[13], inf_min - 0))

                # 进行等于 NTAMD64.6.. 包含匹配
                if 0 == inf_bIs64:
                    db_system_name = 'NTX86.' + str(inf_max) + '..'
                else:
                    db_system_name = 'NTAMD64.' + str(inf_max) + '..'
                cmd = "select * from id_table where " + self.strAname2sql('hard_or_comp_id', one_id) \
                      + "and" + self.intAname2sql('del', 0) + "and system_name like '" + db_system_name \
                      + "%' and inf_driver_ver=" + str(inf_driver_ver) + " and zip_path='" + hash + ".zip'"
                _logger.debug(cmd)
                cu.execute(cmd)
                one_db = cu.fetchone()
                if one_db is not None:
                    ret_list.append((one_db[13], inf_min - 0))

                if 0 == inf_bIs64:
                    db_system_name = 'NT.' + str(inf_max) + '..'
                    cmd = "select * from id_table where " + self.strAname2sql('hard_or_comp_id', one_id) \
                          + "and" + self.intAname2sql('del', 0) + "and system_name like '" + db_system_name \
                          + "%' and inf_driver_ver=" + str(inf_driver_ver) + " and zip_path='" + hash + ".zip'"
                    _logger.debug(cmd)
                    cu.execute(cmd)
                    one_db = cu.fetchone()
                    if one_db is not None:
                        ret_list.append((one_db[13], inf_min - 0))
                        # ============================================================================================
                        # # 进行等于 NTAMD64. 匹配，不能包含避免出现未匹配的 NTAMD64.6
                        # if 0 == inf_bIs64:
                        #     db_system_name = 'NTX86.'
                        # else:
                        #     db_system_name = 'NTAMD64.'
                        # cmd = "select * from id_table where " + self.strAname2sql('hard_or_comp_id', one_id) \
                        #       + "and" + self.intAname2sql('del', 0) + "and" + self.strAname2sql('system_name',
                        #                                                                         db_system_name)
                        # _logger.debug(cmd)
                        # cu.execute(cmd)
                        # one_db = cu.fetchone()
                        # if one_db is not None:
                        #     ret_list.append((one_db[13], 20))  # 因为有大平台版本差异，返回差值增大到20
                        #
                        # if 0 == inf_bIs64:
                        #     db_system_name = 'NT.'
                        #     cmd = "select * from id_table where " + self.strAname2sql('hard_or_comp_id', one_id) \
                        #           + "and" + self.intAname2sql('del', 0) + "and" + self.strAname2sql('system_name',
                        #                                                                             db_system_name)
                        #     _logger.debug(cmd)
                        #     cu.execute(cmd)
                        #     one_db = cu.fetchone()
                        #     if one_db is not None:
                        #         ret_list.append((one_db[13], 20))  # 因为有大平台版本差异，返回差值增大到20
                        #
                        # # 进行等于 NTAMD64 匹配，不能包含避免出现未匹配的 NTAMD64.6
                        # if 0 == inf_bIs64:
                        #     db_system_name = 'NTX86'
                        # else:
                        #     db_system_name = 'NTAMD64'
                        # cmd = "select * from id_table where " + self.strAname2sql('hard_or_comp_id', one_id) \
                        #       + "and" + self.intAname2sql('del', 0) + "and" + self.strAname2sql('system_name',
                        #                                                                         db_system_name)
                        # _logger.debug(cmd)
                        # cu.execute(cmd)
                        # one_db = cu.fetchone()
                        # if one_db is not None:
                        #     ret_list.append((one_db[13], 20))  # 因为有大平台版本差异，返回差值增大到20
                        #
                        # if 0 == inf_bIs64:
                        #     db_system_name = 'NT'
                        #     cmd = "select * from id_table where " + self.strAname2sql('hard_or_comp_id', one_id) \
                        #           + "and" + self.intAname2sql('del', 0) + "and" + self.strAname2sql('system_name',
                        #                                                                             db_system_name)
                        #     _logger.debug(cmd)
                        #     cu.execute(cmd)
                        #     one_db = cu.fetchone()
                        #     if one_db is not None:
                        #         ret_list.append((one_db[13], 20))  # 因为有大平台版本差异，返回差值增大到20
                        #
                        # # 进行等于 NTAMD64.. 包含匹配
                        # if 0 == inf_bIs64:
                        #     db_system_name = 'NTX86..'
                        # else:
                        #     db_system_name = 'NTAMD64..'
                        # cmd = "select * from id_table where " + self.strAname2sql('hard_or_comp_id', one_id) \
                        #       + "and" + self.intAname2sql('del', 0) + "and system_name like '" + db_system_name + "%'"
                        # _logger.debug(cmd)
                        # cu.execute(cmd)
                        # one_db = cu.fetchone()
                        # if one_db is not None:
                        #     ret_list.append((one_db[13], 20))  # 因为有大平台版本差异，返回差值增大到20
                        # if 0 == inf_bIs64:
                        #     db_system_name = 'NT..'
                        #     cmd = "select * from id_table where " + self.strAname2sql('hard_or_comp_id', one_id) \
                        #           + "and" + self.intAname2sql('del', 0) + "and system_name like '" + db_system_name + "%'"
                        #     _logger.debug(cmd)
                        #     cu.execute(cmd)
                        #     one_db = cu.fetchone()
                        #     if one_db is not None:
                        #         ret_list.append((one_db[13], 20))  # 因为有大平台版本差异，返回差值增大到20
            return ret_list
        except Exception as e:
            _logger.warning(r'__check_near_and_get_db_system_name_by_hash failed {}'.format(e), exc_info=True)
            return []

    def get_ok_dev_list(self, file_path, dir_path):
        try:
            hard_id_list = list()
            print('ok_dev file = {}'.format(file_path))
            with open(os.path.join(dir_path, "ok_dev_err.log"), 'w') as err_handle:
                with open(file_path, 'r') as in_handle:
                    while True:
                        line = in_handle.readline()
                        if not line:
                            break
                        print('ok_dev_line = {}'.format(line))
                        try:
                            one_line_info_list = line.split('|')
                            if len(one_line_info_list) > 2:
                                err_handle.write('err_more_time:dev_id=' + dev_id + '\n')
                            dev_id = one_line_info_list[0].strip()
                            inf_driver_ver = one_line_info_list[1].strip()
                            dev_id = dev_id.strip('"')
                            dev_id = dev_id.upper()
                            hard_id_list.append((dev_id, inf_driver_ver))
                        except:
                            print('read line exception')
                            err_handle.write('err_no_time:dev_id=' + dev_id + '\n')
            return hard_id_list
        except Exception as e:
            _logger.warning(r'get_ok_dev_list failed {}'.format(e), exc_info=True)
            return []

    def fix_ok_dev_have_but_db_no(self, system_name, driver_pool, cx, file_path, dir_path):
        try:
            # 读取ok_dev.txt进入列表。
            hard_id_list = self.get_ok_dev_list(file_path, dir_path)
            db_system_name = GetDBSysNameByInfSysName(system_name)
            cu = cx.cursor()
            # 循环获取当前 inf 时间，dev_id
            for dev_id, inf_driver_ver in hard_id_list:
                # if -1 != dev_id.upper().find(r'PCI\VEN_1077&DEV_8020&SUBSYS_1958103C'):
                #     pass
                if -1 == dev_id.upper().find('SUBSYS'):
                    continue
                ven = self.get_ven(dev_id)
                dev = self.get_dev(dev_id)
                if (ven is None) or (dev is None):
                    continue
                # 根据dev_id,生成精简的 pci\ven_xxxx&dev_xxxx,
                ven_dev_id = 'PCI\\' + ven + '&' + dev
                # 查找数据库中，此硬件ID, INF时间相同，是否已经有加分项
                cmd = "select * from id_table where hard_or_comp_id='" + dev_id + "' and inf_driver_ver=" + str(
                    inf_driver_ver.strip()) + " and e_s_1 like'%" + db_system_name + "%'"
                print(cmd)
                cu.execute(cmd)
                one_db = cu.fetchone()
                if one_db is None:
                    # 没有，查找 精简的 pci\ven_xxxx&dev_xxxx,
                    cmd = "select * from id_table where hard_or_comp_id='" + ven_dev_id + "' and inf_driver_ver=" + str(
                        inf_driver_ver) + " and e_s_1 like'%" + db_system_name + "%'"
                    print(cmd)
                    cu.execute(cmd)
                    one_db = cu.fetchone()
                    if one_db is not None:
                        # 加分项，没有完整id,但是有精简id,插入数据，避免出错。
                        cmd = "insert into id_table (server_id,del,type,time,class_guid,hard_or_comp_id,ven,dev," \
                              "inf_driver_ver,inf_path,zip_path,show_name,system_name,IsMicro,score,HWPlatform,e_i_1," \
                              "e_i_2,e_s_1,e_s_2) values (" + int2sql(0) + "," + int2sql(0) + "," + int2sql(0) + "," \
                              + gen_sql_time() + "," + str2sql(one_db[5]) + "," + str2sql(dev_id) + "," + str2sql(ven) \
                              + "," + str2sql(dev) + "," + int2sql(inf_driver_ver) + "," + str2sql(one_db[11]) + "," \
                              + str2sql(one_db[12]) + "," + str2sql(one_db[6]) + "," + str2sql(db_system_name) + "," \
                              + int2sql(0) + "," + int2sql(0) + "," + int2sql(0) + "," + int2sql(one_db[18]) + "," \
                              + int2sql(one_db[19]) + "," + str2sql(db_system_name) + "," + str2sql('') + ")"
                        print(cmd)
                        cu.execute(cmd)
        except Exception as e:
            _logger.warning(r'get_ok_dev_list failed {}'.format(e), exc_info=True)

    def fix_db_platform(self, system_name, driver_pool, cx, file_path, dir_path):
        try:
            hard_id_list = self.get_ok_dev_list(file_path, dir_path)

            db_system_name = GetDBSysNameByInfSysName(system_name)
            cu = cx.cursor()
            with open(os.path.join(dir_path, "ok_dev_run.log"), 'w') as log_handle:
                # 遍历inf
                for root, dirs, files in os.walk(dir_path):
                    for file in files:
                        if file.lower().endswith('.inf'):
                            inf_path = os.path.join(root, file)
                            # if -1 != inf_path.find('qlnd6x32.inf'):
                            #     pass
                            print(r'get_one_dir_all_inf_id_and_class_to_tmp_db search name = {}'.format(inf_path))
                            # 并获取当前inf的，时间，ClassGuid, dev_id列表。
                            GetClassGudi, inf_driver_ver, get_one_list = self.get_one_inf_id_and_class(
                                system_name, driver_pool, inf_path, 0, True, False, False, False)
                            # 获取当前inf文件夹hash值。
                            hash_obj = hashlib.sha1()
                            hash = HashDir(root, hash_obj)
                            # 循环处理当前inf的每一个dev_id
                            for one_result in get_one_list:
                                device_id = one_result['hard_or_comp_id']
                                # if r'PCI\VEN_1077&DEV_8020' == device_id:
                                #     pass
                                # 获取okdev.txt中硬件id对应的时间。
                                get_inf_driver_ver = self.get_inf_driver_ver_by_dev_id(device_id, hard_id_list,
                                                                                       inf_driver_ver)
                                # 如果没有时间，出错退出。
                                if get_inf_driver_ver is not None:
                                    # 将数据库中，所有此id设置为可用。
                                    cmd = "update id_table set del=0 where zip_path ='" + hash + \
                                          ".zip' and hard_or_comp_id='" + device_id + "' " + \
                                          "and inf_driver_ver=" + get_inf_driver_ver
                                    print(cmd)
                                    cu.execute(cmd)
                                    # 检查数据库是不是 id ,time 一条都没有。
                                    if -1 != db_system_name.find('NTAMD64'):
                                        cmd = "select * from id_table where zip_path ='" + hash \
                                              + ".zip' and hard_or_comp_id='" + device_id + "' " \
                                              + "and inf_driver_ver=" + get_inf_driver_ver + " and system_name like 'NTAMD64%'"
                                    else:
                                        cmd = "select * from id_table where zip_path ='" + hash \
                                              + ".zip' and hard_or_comp_id='" + device_id + "' " \
                                              + "and inf_driver_ver=" + get_inf_driver_ver + " and system_name not like 'NTAMD64%'"
                                    print(cmd)
                                    cu.execute(cmd)
                                    one_db = cu.fetchone()
                                    if one_db is None:
                                        # 是没有，记录时间错误信息。退出。
                                        log_handle.write('type:time_err;os:' + db_system_name + ';id:' + device_id
                                                         + ';inf_driver_ver:' + get_inf_driver_ver
                                                         + ';zip_path' + hash + '.zip'
                                                         + ';inf_path' + inf_path + '\n')
                                    else:
                                        # 查询兼容的操作系统列表
                                        db_system_name_list = self.__check_near_and_get_db_system_name_by_hash(
                                            cu, device_id, system_name, inf_driver_ver, hash)
                                        if 0 == len(db_system_name_list):
                                            # 如果没有，插入信息，退出。
                                            cmd = "insert into id_table (server_id,del,type,time,class_guid," \
                                                  "hard_or_comp_id,ven,dev,inf_driver_ver,inf_path,zip_path,show_name,system_name,IsMicro," \
                                                  "score,HWPlatform,e_i_1,e_i_2,e_s_1,e_s_2) values (" \
                                                  + int2sql(0) + "," + int2sql(0) + "," + int2sql(0) + "," \
                                                  + gen_sql_time() + "," + str2sql(GetClassGudi) + "," \
                                                  + str2sql(one_result['hard_or_comp_id']) + "," \
                                                  + str2sql(one_result['ven']) + "," + str2sql(one_result['dev']) \
                                                  + "," + int2sql(inf_driver_ver) + "," \
                                                  + str2sql(one_result['inf_path']) + "," \
                                                  + str2sql(hash + ".zip") + "," \
                                                  + str2sql(one_result['show_name']) + "," + str2sql(db_system_name) \
                                                  + "," + int2sql(0) + "," + int2sql(0) + "," + int2sql(0) + "," \
                                                  + int2sql(one_result['e_i_1']) + "," + int2sql(one_result['e_i_2']) \
                                                  + "," + str2sql(db_system_name) + "," + str2sql('') + ")"
                                            print(cmd)
                                            cu.execute(cmd)
                                            log_handle.write('type:add;os:' + db_system_name + ';id:' + device_id
                                                             + ';inf_driver_ver:' + get_inf_driver_ver
                                                             + ';zip_path' + hash + '.zip'
                                                             + ';inf_path' + inf_path + '\n')
                                        else:
                                            # 如果有，查询，hash，id, inf 时间,且没有处理过ok_dev.txt加分项记录的。
                                            # 根据ID，获取所有相关条目,根据条目，判断是在当前系统路径下。再获取db_system_name不在 e_s_1 中
                                            cmd = "select * from id_table where zip_path ='" + hash + ".zip' and hard_or_comp_id='" + \
                                                  device_id + "' " + "and inf_driver_ver=" + get_inf_driver_ver + \
                                                  " and (e_s_1 is null or e_s_1 not like '%" + db_system_name + "%')"
                                            print(cmd)
                                            cu.execute(cmd)
                                            while True:
                                                one_db = cu.fetchone()
                                                if one_db is None:
                                                    break
                                                # 不在条目中，加入当前平台。
                                                cu2 = cx.cursor()
                                                if one_db[20] is None:
                                                    new_e_s_1 = db_system_name
                                                elif one_db[20] == '':
                                                    new_e_s_1 = db_system_name
                                                else:
                                                    new_e_s_1 = one_db[20] + ';' + db_system_name
                                                # cmd = "update id_table set del=0,e_s_1='" + new_e_s_1 + "' where zip_path = '"  + hash + ".zip'"
                                                # 根据查询结果，循环更新加分项。
                                                cmd = "update id_table set del=0,e_s_1='" + new_e_s_1 + "' where key = " + str(
                                                    one_db[0])
                                                print(cmd)
                                                cu2.execute(cmd)
                                            # 比对兼容系统列表，如果没有当前加分项的系统号，写入log
                                            bFindSameOSName = False
                                            try:
                                                get_db_no_abc = ''
                                                db_no_abc = ''
                                                for get_db_system_name, get_min_os_dec in db_system_name_list:
                                                    # 去除第一个小数点的东西。，避免NTX86.6.3 和 NT.6.3 比较错误
                                                    nPos = get_db_system_name.find('.')
                                                    if -1 != nPos:
                                                        get_db_no_abc = get_db_system_name[nPos:]
                                                    nPos = db_system_name.find('.')
                                                    if -1 != nPos:
                                                        db_no_abc = db_system_name[nPos:]
                                                    if -1 != db_no_abc.find(get_db_no_abc):
                                                        bFindSameOSName = True
                                                        break
                                            except:
                                                pass

                                            if bFindSameOSName is not True:
                                                # 如果不能够查到插入数据，写入LOG
                                                log_handle.write(
                                                    'type:mini_os;os:' + db_system_name + ';id:' + device_id
                                                    + ';inf_driver_ver:' + get_inf_driver_ver
                                                    + ';zip_path' + hash + '.zip'
                                                    + ';inf_path' + inf_path + '\n')

        except Exception as e:
            _logger.error(traceback.format_exc())

    def add_okdev_to_id_table(self, system_name, cx, driver_pool, db_path):
        try:
            my_dir_path = os.path.join(driver_pool, system_name)
            get_file_in_dir_list = os.listdir(my_dir_path)
            for one_file in get_file_in_dir_list:
                if one_file.lower() == 'ok_dev.txt':
                    # 读取ok_dev.txt
                    all_ok_file_path = os.path.join(my_dir_path, one_file)
                    self.fix_db_platform(system_name, driver_pool, cx, all_ok_file_path, my_dir_path)
                    self.fix_ok_dev_have_but_db_no(system_name, driver_pool, cx, all_ok_file_path, my_dir_path)
                else:
                    search_sub_dir_full_path = os.path.join(my_dir_path, one_file)
                    if os.path.isdir(search_sub_dir_full_path):
                        # 读取ok_dev.txt
                        all_ok_file_path = os.path.join(os.path.join(my_dir_path, one_file), 'ok_dev.txt')
                        if os.path.exists(all_ok_file_path):
                            self.fix_db_platform(system_name, driver_pool, cx, all_ok_file_path,
                                                 search_sub_dir_full_path)
                            self.fix_ok_dev_have_but_db_no(system_name, driver_pool, cx, all_ok_file_path, my_dir_path)

        except Exception as e:
            print(traceback.format_exc())
            return None


def gen_zip(db_path, driver_pool, driver_pool_update):
    try:
        with sqlite3.connect(db_path) as cx:
            cu = cx.cursor()
            cmd = 'select distinct zip_path from id_table where ' + intAname2sql('del', 0)
            _logger.debug(cmd)
            cu.execute(cmd)
            while True:
                one_db = cu.fetchone()
                if one_db is None:
                    break
                if one_db[0] is None:
                    continue
                tmp = os.path.splitext(one_db[0])
                zip_path = os.path.join(driver_pool_update, one_db[0])
                dir_path = os.path.join(driver_pool_update, tmp[0])
                if not os.path.exists(dir_path):
                    print('dir_path = {} not exist'.format(dir_path))
                    exit(0)
                if not os.path.exists(zip_path):
                    try:
                        zip_dir(dir_path, zip_path)
                    except Exception as e:
                        print('zip error , dir_path = {},zip_path = {}'.format(dir_path, zip_path))
                        exit(0)
    except Exception as e:
        _logger.error(traceback.format_exc())


# one_sql_info_list = {'server_id': None, 'del': None, 'show_name': None, 'hard_or_comp_id': None,
#                      'inf_driver_ver': None, 'inf_path': None, 'zip_path': None, 'system_name': None,
#                       'IsMicro': None, 'score': None, 'HWPlatform': None, 'depends': None,
#                       'e_i_1': None, 'e_i_2': None, 'e_s_1': None, 'e_s_2': None}
def get_list(db_path, driver_pool, driver_pool_update):
    try:
        list = []
        with sqlite3.connect(db_path) as cx:
            cu = cx.cursor()
            cmd = 'select distinct server_id,del,show_name,hard_or_comp_id,inf_driver_ver,inf_path,zip_path,system_name,\
                IsMicro,score,HWPlatform,depends,e_i_1,e_i_2,e_s_1,class_guid from id_table'
            _logger.debug(cmd)
            cu.execute(cmd)
            while True:
                one_db = cu.fetchone()
                if one_db is None:
                    break
                one_sql_info_list = {'server_id': None, 'del': None, 'show_name': None, 'hard_or_comp_id': None,
                                     'inf_driver_ver': None, 'inf_path': None, 'zip_path': None, 'system_name': None,
                                     'IsMicro': None, 'score': None, 'HWPlatform': None, 'depends': None,
                                     'e_i_1': None, 'e_i_2': None, 'e_s_1': None, 'e_s_2': None}
                one_sql_info_list['server_id'] = one_db[0]
                one_sql_info_list['del'] = one_db[1]
                one_sql_info_list['show_name'] = one_db[2]
                one_sql_info_list['hard_or_comp_id'] = one_db[3]
                one_sql_info_list['inf_driver_ver'] = one_db[4]
                one_sql_info_list['inf_path'] = one_db[5]
                one_sql_info_list['zip_path'] = one_db[6]
                one_sql_info_list['system_name'] = one_db[7]
                one_sql_info_list['IsMicro'] = one_db[8]
                one_sql_info_list['score'] = one_db[9]
                one_sql_info_list['HWPlatform'] = one_db[10]
                one_sql_info_list['depends'] = one_db[11]
                one_sql_info_list['e_i_1'] = one_db[12]
                one_sql_info_list['e_i_2'] = one_db[13]
                one_sql_info_list['e_s_1'] = one_db[14]
                one_sql_info_list['e_s_2'] = one_db[15]
                list.append(one_sql_info_list)
        return list
    except Exception as e:
        _logger.error(traceback.format_exc())


def safe_copy_db(db_path):
    try:
        os.remove(db_path)
    except Exception as e:
        pass
    if os.path.exists(db_path):
        _logger.debug('can not del {}'.format(db_path))
        exit(0)
    try:
        shutil.copy('drvierid.db', db_path)
    except Exception as e:
        pass
    if not os.path.exists(db_path):
        _logger.debug('can not copy {}'.format(db_path))
        exit(0)


def read_exp(system_name, cu, file_path, table_name):
    try:
        _logger.debug('read_exp begin table_name={}'.format(table_name))
        with open(file_path, 'r') as in_handle:
            while True:
                line = in_handle.readline()
                if not line:
                    break
                dev_id = line.upper().strip('\n')
                cmd = "insert into " + table_name + " (hard_or_comp_id,system_name) values (" \
                      + str2sql_and(dev_id, False) + str2sql_and(system_name) + ")"
                # _logger.debug(cmd)
                cu.execute(cmd)
    except Exception as e:
        _logger.error(traceback.format_exc())


def read_deviceid_file_to_db(system_name, cu, file_path, table_name):
    try:
        _logger.debug('read_deviceid_file_to_db begin table_name={}'.format(table_name))
        with open(file_path, 'r') as in_handle:
            while True:
                line = in_handle.readline()
                if not line:
                    break
                try:
                    one_line_info_list = line.split('|')
                    dev_id = one_line_info_list[0].strip()
                    inf_driver_ver = one_line_info_list[1].strip()
                    dev_id = dev_id.strip('"')
                    dev_id = dev_id.upper()
                    cmd = "insert into " + table_name + " (hard_or_comp_id,system_name,inf_driver_ver) values (" \
                          + str2sql_and(dev_id, False) + ",''" + int2sql_and(inf_driver_ver, True) + ")"
                    # _logger.debug(cmd)
                    cu.execute(cmd)
                except:
                    print('read_deviceid_file_to_db exception:continue')
    except Exception as e:
        _logger.error(traceback.format_exc())


def scan_new(db_path, driver_pool, driver_pool_update):
    # 删除文件
    # global system_cat_name_list
    try:
        safe_copy_db(db_path)
        try:
            os.remove(os.path.join(driver_pool, 'scan_no_file.log'))
        except:
            pass
        file_list = os.listdir(driver_pool)
        with sqlite3.connect(db_path) as cx:
            cu = cx.cursor()
            for one_file in file_list:
                if IsNewSystemCatNameList(one_file):
                    # 获取操作系统名字。
                    system_name = one_file
                    hash_driver_pool(cu, system_name, db_path, driver_pool, driver_pool_update)
                    cmd = "delete from tmp_id_table"
                    print(cmd)
                    cu.execute(cmd)
                    cmd = "delete from tmp_all_ok"
                    print(cmd)
                    cu.execute(cmd)
                    # 读取所有数据到临时表。
                    enum_class = CEnumIDByInfDir()  # 插入数据之后，需要设置删除标示，del=1,以便对比后来的ok_dev.txt进行处理
                    enum_class.get_all_HWPlatform_okdev_to_tmp_db(system_name, cu, driver_pool, db_path, True, False,
                                                                  False)
                    # 将 id_table in （tmp_all_ok == id_table（deviceid,system））设置获取的所有inf_path为可用。
                    cmd = "update tmp_id_table set del=0,IsMicro=0 where tmp_id_table.inf_path in \
                                (select distinct tmp_id_table.inf_path  from tmp_id_table where exists \
                                (select * from tmp_all_ok where tmp_id_table.hard_or_comp_id=tmp_all_ok.hard_or_comp_id))"
                    print(cmd)
                    cu.execute(cmd)

                    # 合并3表到永久表。
                    cmd = "insert into id_table(del,type,class_guid,hard_or_comp_id,ven,dev,inf_driver_ver,inf_path,\
                            zip_path,show_name,system_name,IsMicro,score,HWPlatform,e_i_1,e_i_2) select distinct del,type,class_guid,\
                            hard_or_comp_id,ven,dev,inf_driver_ver,inf_path,zip_path,show_name,system_name,IsMicro,score,HWPlatform,\
                            e_i_1,e_i_2 from tmp_id_table"
                    print(cmd)
                    cu.execute(cmd)
            # 读取exp.del.txt 到 tmp_non_deev
            read_exp('All', cu, os.path.join(driver_pool, 'exp.del.txt'), 'tmp_non_dev')

            # 去掉 ntamd64 未签名的驱动。
            cmd = "update id_table set del=1 where system_name like \"NTAMD%\" and e_i_1<>0"
            print(cmd)
            cu.execute(cmd)

            cmd = "delete from id_table where id_table.class_guid in (select tmp_non_dev.hard_or_comp_id from tmp_non_dev)"
            print(cmd)
            cu.execute(cmd)

            cmd = "delete from id_table where exists(select hard_or_comp_id from tmp_non_dev \
              where id_table.hard_or_comp_id like'%' || tmp_non_dev.hard_or_comp_id || '%')"
            print(cmd)
            cu.execute(cmd)

            cmd = "delete from id_table where exists(select hard_or_comp_id from tmp_non_dev \
                where id_table.show_name like'%' || tmp_non_dev.hard_or_comp_id || '%')"
            print(cmd)
            cu.execute(cmd)

            cx.commit()
        print('scan_new end')

    except Exception as e:
        print(traceback.format_exc())


def scan_new_add_spec(db_path, driver_pool, driver_pool_update):
    # 删除文件
    # global system_cat_name_list
    try:
        file_list = os.listdir(driver_pool)
        with sqlite3.connect(db_path) as cx:
            # cu = cx.cursor()
            cmd = "delete from tmp_id_table"
            for one_file in file_list:
                if IsNewSystemCatNameList(one_file):
                    # 获取操作系统名字。
                    system_name = one_file
                    enum_class = CEnumIDByInfDir()  # 插入数据之后，需要设置删除标示，del=1,以便对比后来的ok_dev.txt进行处理
                    enum_class.add_okdev_to_id_table(system_name, cx, driver_pool, db_path)

            # 去掉 ok_dev.txt中没有的驱动。
            # cmd = "update id_table set del=1 where e_s_1 is null or e_s_1=\"\""
            # print(cmd)
            # cu.execute(cmd)

            cx.commit()
        print('scan_new_add_spec end')

    except Exception as e:
        print(traceback.format_exc())


def scan(db_path, driver_pool, driver_pool_update):
    scan_new(db_path, driver_pool, driver_pool_update)
    scan_new_add_spec(db_path, driver_pool, driver_pool_update)


def scan_new_one_dir(db_path, driver_pool, driver_pool_update):
    # 删除文件
    # global system_cat_name_list
    try:
        new_driver_pool = os.path.dirname(driver_pool)
        one_file = os.path.basename(driver_pool)
        safe_copy_db(db_path)
        try:
            os.remove(os.path.join(new_driver_pool, 'scan_no_file.log'))
        except:
            pass
        with sqlite3.connect(db_path) as cx:
            cu = cx.cursor()
            if IsNewSystemCatNameList(one_file):
                # 获取操作系统名字。
                system_name = one_file
                hash_driver_pool(cu, system_name, db_path, new_driver_pool, driver_pool_update)
                cmd = "delete from tmp_id_table"
                print(cmd)
                cu.execute(cmd)
                cmd = "delete from tmp_all_ok"
                print(cmd)
                cu.execute(cmd)
                # 读取所有数据到临时表。
                enum_class = CEnumIDByInfDir()  # 插入数据之后，需要设置删除标示，del=1,以便对比后来的ok_dev.txt进行处理
                enum_class.get_all_HWPlatform_okdev_to_tmp_db(system_name, cu, new_driver_pool, db_path, True, False,
                                                              False)
                # 将 id_table in （tmp_all_ok == id_table（deviceid,system））设置获取的所有inf_path为可用。
                cmd = "update tmp_id_table set del=0,IsMicro=0 where tmp_id_table.inf_path in \
                            (select distinct tmp_id_table.inf_path  from tmp_id_table where exists \
                            (select * from tmp_all_ok where tmp_id_table.hard_or_comp_id=tmp_all_ok.hard_or_comp_id))"
                print(cmd)
                cu.execute(cmd)

                # 合并3表到永久表。
                cmd = "insert into id_table(del,type,class_guid,hard_or_comp_id,ven,dev,inf_driver_ver,inf_path,\
                        zip_path,show_name,system_name,IsMicro,score,HWPlatform,e_i_1,e_i_2) select distinct del,type,class_guid,\
                        hard_or_comp_id,ven,dev,inf_driver_ver,inf_path,zip_path,show_name,system_name,IsMicro,score,HWPlatform,\
                        e_i_1,e_i_2 from tmp_id_table"
                print(cmd)
                cu.execute(cmd)
            # 读取exp.del.txt 到 tmp_non_deev
            read_exp('All', cu, os.path.join(new_driver_pool, 'exp.del.txt'), 'tmp_non_dev')

            # 去掉 ntamd64 未签名的驱动。
            cmd = "update id_table set del=1 where system_name like \"NTAMD%\" and e_i_1<>0"
            print(cmd)
            cu.execute(cmd)

            cmd = "delete from id_table where id_table.class_guid in (select tmp_non_dev.hard_or_comp_id from tmp_non_dev)"
            print(cmd)
            cu.execute(cmd)

            cmd = "delete from id_table where exists(select hard_or_comp_id from tmp_non_dev \
              where id_table.hard_or_comp_id like'%' || tmp_non_dev.hard_or_comp_id || '%')"
            print(cmd)
            cu.execute(cmd)

            cmd = "delete from id_table where exists(select hard_or_comp_id from tmp_non_dev \
                where id_table.show_name like'%' || tmp_non_dev.hard_or_comp_id || '%')"
            print(cmd)
            cu.execute(cmd)

            cx.commit()
        print('scan_new end')

    except Exception as e:
        print(traceback.format_exc())


def scan_new_add_spec_one_dir(db_path, driver_pool, driver_pool_update):
    # 删除文件
    # global system_cat_name_list
    try:
        new_driver_pool = os.path.dirname(driver_pool)
        one_file = os.path.basename(driver_pool)
        file_list = os.listdir(driver_pool)
        with sqlite3.connect(db_path) as cx:
            # cu = cx.cursor()
            cmd = "delete from tmp_id_table"
            if IsNewSystemCatNameList(one_file):
                # 获取操作系统名字。
                system_name = one_file
                enum_class = CEnumIDByInfDir()  # 插入数据之后，需要设置删除标示，del=1,以便对比后来的ok_dev.txt进行处理
                enum_class.add_okdev_to_id_table(system_name, cx, new_driver_pool, db_path)

            # 去掉 ok_dev.txt中没有的驱动。
            # cmd = "update id_table set del=1 where e_s_1 is null or e_s_1=\"\""
            # print(cmd)
            # cu.execute(cmd)

            cx.commit()
        print('scan_new_add_spec end')

    except Exception as e:
        print(traceback.format_exc())


def scan_one_dir(db_path, driver_pool, driver_pool_update):
    scan_new_one_dir(db_path, driver_pool, driver_pool_update)
    scan_new_add_spec_one_dir(db_path, driver_pool, driver_pool_update)


def scan_no_guid_two_type(cu, db_path, driver_pool, driver_pool_update, SpecType):
    # 删除文件
    # global system_cat_name_list
    try:
        print("scan_no_guid_two_type begin")
        file_list = os.listdir(driver_pool)
        for one_file in file_list:
            if IsNewSystemCatNameList(one_file):
                # 获取操作系统名字。
                system_name = one_file
                hash_driver_pool(cu, system_name, db_path, driver_pool, driver_pool_update)
                # 读取所有数据到临时表。
                enum_class = CEnumIDByInfDir()  # 插入数据之后，需要设置删除标示，del=1,以便对比后来的ok_dev.txt进行处理
                enum_class.get_all_HWPlatform_okdev_to_tmp_db(system_name, cu, driver_pool, db_path, True, True,
                                                              SpecType)
        print('scan_no_guid_two_type end')

    except Exception as e:
        print(traceback.format_exc())


def scan_no_guid(db_path, driver_pool, driver_pool_update):
    try:
        print("scan_no_guid begin")
        safe_copy_db(db_path)
        with sqlite3.connect(db_path) as cx:
            cu = cx.cursor()
            scan_no_guid_two_type(cu, db_path, driver_pool, driver_pool_update, False)
            scan_no_guid_two_type(cu, db_path, driver_pool + '\\must', driver_pool_update, True)

            # 合并3表到永久表。
            cmd = "delete from id_table"
            print(cmd)
            cu.execute(cmd)

            cmd = "insert into id_table(del,type,class_guid,hard_or_comp_id,ven,dev,inf_driver_ver,inf_path,\
                zip_path,show_name,system_name,score,HWPlatform,e_i_1,e_i_2) select distinct del,type,class_guid,\
                hard_or_comp_id,ven,dev,inf_driver_ver,inf_path,zip_path,show_name,system_name,score,HWPlatform,e_i_1,e_i_2 from tmp_id_table"
            _logger.debug(cmd)
            cu.execute(cmd)

            cmd = "update id_table set del=0,IsMicro=0"
            print(cmd)
            cu.execute(cmd)
            cx.commit()

        print("scan_no_guid end")
    except Exception as e:
        print(traceback.format_exc())


def scan_micro(db_path, driver_pool, driver_pool_update):
    # 删除文件
    # global system_cat_name_list
    global g_dbg_num
    global g_NotUserDriverTmpDir
    try:
        safe_copy_db(db_path)
        g_NotUserDriverTmpDir = 1
        file_list = os.listdir(driver_pool)
        with sqlite3.connect(db_path) as cx:
            cu = cx.cursor()
            for one_file in file_list:
                if IsNewSystemCatNameList(one_file):
                    # 获取操作系统名字。
                    system_name = one_file
                    # 读取所有数据到临时表。
                    enum_class = CEnumIDByInfDir()  # 插入数据之后，需要设置删除标示，del=1,以便对比后来的ok_dev.txt进行处理
                    for root, dirs, files in os.walk(os.path.join(driver_pool, system_name)):
                        for file in files:
                            if IsRigthInf(file, False) != True:
                                continue
                            inf_path = os.path.join(root, file)
                            print(r'scan_micro search name = {}'.format(inf_path))
                            enum_class.get_one_inf_id_and_class_to_tmp_db(system_name, cu, driver_pool, db_path,
                                                                          inf_path, 0, True, True, False)
            # 读取exp.del.txt 到 tmp_non_deev
            read_exp('All', cu, os.path.join(driver_pool, 'exp.del.txt'), 'tmp_non_dev')

            # 合并3表到永久表。
            cmd = "delete from id_table"
            print(cmd)
            cu.execute(cmd)
            cmd = "insert into id_table(del,type,class_guid,hard_or_comp_id,show_name,system_name)\
            select distinct del,type,class_guid,hard_or_comp_id,show_name,system_name from tmp_id_table"
            print(cmd)
            cu.execute(cmd)
            # 将 id_table 设置获取的所有inf_path为可用。
            cmd = "update id_table set del=0,IsMicro=1,score=1,HWPlatform=0,e_i_1=0,e_i_2=0"
            print(cmd)
            cu.execute(cmd)

            cmd = "delete from id_table where id_table.class_guid in (select tmp_non_dev.hard_or_comp_id from tmp_non_dev)"
            print(cmd)
            cu.execute(cmd)

            cmd = "delete from id_table where exists(select hard_or_comp_id from tmp_non_dev \
              where id_table.hard_or_comp_id like'%' || tmp_non_dev.hard_or_comp_id || '%')"
            print(cmd)
            cu.execute(cmd)

            cmd = "delete from id_table where exists(select hard_or_comp_id from tmp_non_dev \
                where id_table.show_name like'%' || tmp_non_dev.hard_or_comp_id || '%')"
            print(cmd)
            cu.execute(cmd)

            cx.commit()
            _logger.debug('scan_micro end')

    except Exception as e:
        _logger.error(traceback.format_exc())


def get_region(db_path):
    try:
        region_list = list()
        with sqlite3.connect(db_path) as cx:
            cu = cx.cursor()
            cmd = 'select distinct server_id from id_table  where ' + intAname2sql('del', 0) + 'order by server_id'
            _logger.debug(cmd)
            cu.execute(cmd)
            start_num = -1
            last_num = -1
            while True:
                one_db = cu.fetchone()
                if one_db is None:
                    break
                if one_db[0] is None:
                    break
                get_num = one_db[0]
                if (get_num - last_num) > 1:
                    if -1 != start_num:
                        region_list.append([start_num, last_num])
                    start_num = get_num
                last_num = get_num
            if -1 != start_num:
                region_list.append([start_num, last_num])

        _logger.debug('get_region region_list={}'.format(region_list))
        return region_list
    except:
        _logger.error(traceback.format_exc())


def get_version(db_path):
    ver = '111111'
    try:
        with sqlite3.connect(db_path) as cx:
            cu = cx.cursor()
            cmd = "select value from cfg_table where name='version'"
            cu.execute(cmd)
            one_db = cu.fetchone()
            if one_db and one_db[0]:
                ver = one_db[0]
        return ver
    except:
        _logger.error(traceback.format_exc())
        return ver


def gen_one_driver_dir(one_list, driver_pool, zip_down_load_path):
    try:
        _logger.debug('gen_one_driver_dir one_list={},driver_pool={},zip_down_load_path={}'.
                      format(one_list, driver_pool, zip_down_load_path))
        if driver_pool is None or zip_down_load_path is None:
            _logger.debug('gen_one_driver_dir if driver_pool is None or zip_down_load_path is None')
            return
        if 0 == len(driver_pool) or 0 == len(zip_down_load_path):
            _logger.debug('gen_one_driver_dir if 0 == len(driver_pool) or 0 == len(zip_down_load_path)')
            return
        if 0 == len(one_list):
            _logger.debug('gen_one_driver_dir if 0 == len(one_list)')
            return
        if None == one_list['zip_path']:
            _logger.debug('gen_one_driver_dir if None == one_list[0]["inf_path"]')
            return
        # 获取目标目录。
        des_dir = os.path.join(driver_pool, os.path.basename(os.path.splitext(one_list['zip_path'])[0]))
        _logger.debug('gen_one_driver_dir des_dir={}'.format(des_dir))
        # 不能删除原来目录，否则会有很大数据量操作。
        # try:
        #     _logger.debug('gen_one_driver_dir shutil.rmtree(des_dir)={}'.format(des_dir))
        #     shutil.rmtree(des_dir)
        # except:
        #     pass
        if not os.path.exists(des_dir):
            # 如果没有驱动库目录，创建。
            os.makedirs(driver_pool, 0o777, True)
            # 再解压缩驱动
            unzip_file(zip_down_load_path, des_dir)
    except:
        _logger.error(traceback.format_exc())


# 生成数据库
# one_sql_info_list = {'server_id': None, 'del': None, 'show_name': None, 'hard_or_comp_id': None,
#                      'inf_driver_ver': None, 'inf_path': None, 'zip_path': None, 'system_name': None,
#                       'IsMicro': None, 'score': None, 'HWPlatform': None, 'depends': None,
#                       'e_i_1': None, 'e_i_2': None, 'e_s_1': None, 'e_s_2': None}
def update_one(cu, one_list, db_path, driver_pool, zip_down_load_path):
    global g_type
    try:
        bIsAdd = False
        g_type = 2
        # 查询
        cmd = "select * from id_table where " \
              + strAname2sql_and('hard_or_comp_id', one_list['hard_or_comp_id'], False) \
              + intAname2sql_and('inf_driver_ver', one_list['inf_driver_ver']) \
              + strAname2sql_and('inf_path', one_list['inf_path']) \
              + strAname2sql_and('zip_path', one_list['zip_path']) \
              + strAname2sql_and('system_name', one_list['system_name'])
        _logger.debug(cmd)
        cu.execute(cmd)

        # 数据库构建时，保持了唯一性
        one_db = cu.fetchone()
        if one_db is None:
            cmd = "insert into id_table (server_id,del,show_name,hard_or_comp_id,inf_driver_ver,inf_path,zip_path,system_name,type,time,IsMicro,score,HWPlatform,e_i_1,e_i_2) values (" \
                  + int2sql_and(0, False) + int2sql_and(one_list['del']) \
                  + str2sql_and(one_list['show_name']) + str2sql_and(one_list['hard_or_comp_id']) \
                  + int2sql_and(one_list['inf_driver_ver']) + str2sql_and(one_list['inf_path']) \
                  + str2sql_and(one_list['zip_path']) + str2sql_and(one_list['system_name']) \
                  + int2sql_and(g_type) + gen_sql_time_and() + int2sql_and(one_list['IsMicro']) \
                  + int2sql_and(one_list['score']) + int2sql_and(one_list['HWPlatform']) \
                  + int2sql_and(one_list['e_i_1']) + int2sql_and(one_list['e_i_2']) + ")"
            _logger.debug(cmd)
            cu.execute(cmd)
            bIsAdd = True
        else:
            if one_list['del'] != one_db[2]:
                if 0 == one_list['del']:
                    bIsAdd = True
                cmd = "Update id_table set " + intAname2sql_and('del', one_list['del'], False) + " where " \
                      + intAname2sql_and('server_id', 0, False) \
                      + strAname2sql_and('hard_or_comp_id', one_list['hard_or_comp_id']) \
                      + intAname2sql_and('inf_driver_ver', one_list['inf_driver_ver']) \
                      + strAname2sql_and('inf_path', one_list['inf_path']) \
                      + strAname2sql_and('zip_path', one_list['zip_path']) \
                      + strAname2sql_and('system_name', one_list['system_name']) \
                      + intAname2sql_and('IsMicro', one_list['IsMicro']) \
                      + intAname2sql_and('score', one_list['score']) \
                      + intAname2sql_and('HWPlatform', one_list['HWPlatform']) \
                      + intAname2sql_and('e_i_1', one_list['e_i_1'] \
                                         + intAname2sql_and('e_i_2', one_list['e_i_2']))
                _logger.debug(cmd)
                cu.execute(cmd)
        return bIsAdd
    except:
        _logger.error(traceback.format_exc())  # 生成数据库


def update_one_zip(cu, all_sql_info_list, db_path, driver_pool, zip_down_load_path):
    try:
        bIsAdd = False
        if 0 == len(all_sql_info_list):
            return False
        for one_list in all_sql_info_list:
            if update_one(cu, one_list, db_path, driver_pool, zip_down_load_path) is True:
                bIsAdd = True
        if bIsAdd is True:  # 如果是添加，删除原有文件夹，进行替换。因为一个文件夹多个硬件id,所以不需要删除旧文件夹。
            gen_one_driver_dir(all_sql_info_list[0], driver_pool, zip_down_load_path)
        return True
    except:
        _logger.error(traceback.format_exc())  # 生成数据库
        return False


def update_one_zip_of_db(cu, all_sql_info_list, db_path):
    for one_list in all_sql_info_list:
        update_one(cu, one_list, db_path, None, None)


def factory_update_one_zip_of_db(cu, all_sql_info_list, db_path):
    for one_list in all_sql_info_list:
        cmd = "insert into tmp_id_table (server_id,del,show_name,hard_or_comp_id,inf_driver_ver,inf_path,zip_path,system_name,type,time) values (" \
              + int2sql_and(one_list['server_id'], False) + int2sql_and(one_list['del']) \
              + str2sql_and(one_list['show_name']) + str2sql_and(one_list['hard_or_comp_id']) \
              + int2sql_and(one_list['inf_driver_ver']) + str2sql_and(one_list['inf_path']) \
              + str2sql_and(one_list['zip_path']) + str2sql_and(one_list['system_name']) \
              + int2sql_and(2) + gen_sql_time_and() + ")"
        # print(cmd)
        cu.execute(cmd)


def copy_drv_need_dir(src_dir, des_dir, db_path, driver_pool, driver_pool_update):
    try:
        print('will del dir = {}'.format(des_dir))
        safe_del_dir(des_dir)
        print('will copy_drv_need_dir src = {}'.format(src_dir))
        while True:
            try:
                shutil.copytree(src_dir, des_dir)
            except:
                time.sleep(1)
            if os.path.exists(des_dir):
                break
        return True
    except:
        print(traceback.format_exc())
        exit(0)  # 需要手工干预


def hash_driver_pool(cu, system_name, db_path, driver_pool, driver_pool_update):
    try:
        dir_list = list()
        for root, dirs, files in os.walk(os.path.join(driver_pool, system_name)):
            for file in files:
                if file.lower().endswith('.inf'):
                    inf_dir = os.path.dirname(os.path.join(root, file))
                    dir_list.append(inf_dir)
        dir_list = list(set(dir_list))
        print('dir count = {}'.format(len(dir_list)))
        try:
            os.makedirs(driver_pool_update)
        except:
            pass

        num = 0
        # with sqlite3.connect(db_path) as cx:
        #     cu = cx.cursor()
        for one_dir in dir_list:
            des_dir = (os.path.join(driver_pool_update, 'tmp'))
            # 拷贝驱动必要的文件到目标路径。
            ret = copy_drv_need_dir(one_dir, des_dir, db_path, driver_pool, driver_pool_update)
            if ret != True:
                continue
            # 计算目标路径hash值
            hash_obj = hashlib.sha1()
            hash = HashDir(des_dir, hash_obj)
            num = num + 1
            # 查询
            inf_dir = one_dir[len(driver_pool) + 1:]
            inf_dir = inf_dir.replace('\\', '/')
            cmd = "select * from tmp_inf_dir_2_hash where " \
                  + strAname2sql('inf_dir', one_dir) + ' and ' + strAname2sql('hash', hash)
            print('num = {} ,cmd = {}'.format(num, cmd))
            cu.execute(cmd)
            # 数据库构建时，保持了唯一性
            one_db = cu.fetchone()
            if one_db is None:
                cmd = "insert into tmp_inf_dir_2_hash (inf_dir,hash) values (" \
                      + str2sql_and(inf_dir, False) + str2sql_and(hash) + ')'
                print('num = {} ,cmd = {}'.format(num, cmd))
                cu.execute(cmd)
            driver_pool_update_dir = os.path.join(driver_pool_update, hash)
            if not os.path.exists(driver_pool_update_dir):
                shutil.move(des_dir, driver_pool_update_dir)
                # cx.commit()
    except:
        print(traceback.format_exc())  # 生成数据库
        exit(0)  # 有可能拷贝驱动库失败，需要手动处理。


def copy_src_dir_list_to_des_dir(src_dir, list, des_dir):
    try:
        for one in list:
            src_copy_dir = os.path.join(src_dir, one)
            des_copy_dir = os.path.join(des_dir, one)
            shutil.copytree(src_copy_dir, des_copy_dir)
            print('copy dir src={} --> des={}'.format(src_copy_dir, des_copy_dir))
    except:
        _logger.error(traceback.format_exc())


# def gen_all_sys_dir_and_copy(src_dir, des_dir):
#     try:
#         print('gen_all_sys_dir_and_copy begin')
#         # del des dir
#         shutil.rmtree(des_dir)
#         os.makedirs(des_dir)
#
#         src_dir_list = os.listdir(src_dir)
#         for one_sys_name in system_cat_name_list:
#             copy_des_dir = os.path.join(des_dir, one_sys_name)
#             os.makedirs(copy_des_dir)
#             copy_src_dir_list_to_des_dir(src_dir, src_dir_list, copy_des_dir)
#
#         print('gen_all_sys_dir_and_copy end')
#
#     except:
#         _logger.error(traceback.format_exc())  # 生成数据库


def safe_del_dir(dir_path):
    try:
        while True:
            try:
                print('will del dir_path={}'.format(dir_path))
                shutil.rmtree(dir_path)
                print('have del dir_path={}'.format(dir_path))
            except:
                print('can not del path={}'.format(dir_path))
            if os.path.exists(dir_path):
                print('can not del path={},retry'.format(dir_path))
                for root, dirs, files in os.walk(dir_path):
                    show_and_exe_cmd_line_and_get_ret('cmd /c attrib -R -S -H "' + root + '"')
                    for file in files:
                        file_path = os.path.join(root, file)
                        show_and_exe_cmd_line_and_get_ret('cmd /c attrib -R -S -H "' + file_path + '"')
                # show_and_exe_cmd_line_and_get_ret('cmd /c attrib -R -S -H "' + dir_path + '\\*" /S')
                time.sleep(1)
            else:
                print('safe_del_dir del path={},success!'.format(dir_path))
                break
    except:
        _logger.error(traceback.format_exc())  # 生成数据库


def create_null_file(file_path):
    try:
        with open(file_path, 'w') as handle:
            pass
    except:
        _logger.error(traceback.format_exc())  # 生成数据库


def Search_File_In_Dir_And_Copy(search_file, search_dir, des_dir):
    try:
        print('Search_File_In_Dir_And_Copy search_file = {},search_dir = {},des_dir = {},begin' \
              .format(search_file, search_dir, des_dir))
        for root, dirs, files in os.walk(search_dir):
            for file in files:
                if file.lower() == search_file.lower():
                    try:
                        print('Search_File_In_Dir_And_Copy copy file search_file = {},search_dir = {},des_dir = {}' \
                              .format(search_file, search_dir, des_dir))
                        shutil.copy(os.path.join(root, file), os.path.join(des_dir, search_file))
                        print('Search_File_In_Dir_And_Copy  end')
                        return True
                    except:
                        print(
                            'Search_File_In_Dir_And_Copy copy file exception search_file = {},search_dir = {},des_dir = {}' \
                                .format(search_file, search_dir, des_dir))
                        print('Search_File_In_Dir_And_Copy  end')
                        return False
        print('Search_File_In_Dir_And_Copy  end')
        return False
    except:
        print('Search_File_In_Dir_And_Copy exception search_file = {},search_dir = {},des_dir = {}' \
              .format(search_file, search_dir, des_dir))
        print(traceback.format_exc())  # 生成数据库
        return False


def Search_File_And_Copy_ByDirList(search_file, search_dir_list, des_dir):
    try:
        print('Search_File_And_Copy_ByDirList search_file = {},des_dir = {},begin' \
              .format(search_file, des_dir))
        for file in search_dir_list:
            file_name = os.path.basename(file)
            if file_name.lower() == search_file.lower():
                try:
                    print('Search_File_And_Copy_ByDirList copy file src = {},des = {}'.format(file,
                                                                                              os.path.join(des_dir,
                                                                                                           file_name)))
                    shutil.copy(file, os.path.join(des_dir, file_name))
                    print('Search_File_And_Copy_ByDirList end')
                    return True
                except:
                    print('Search_File_And_Copy_ByDirList copy file exception src = {},des = {}'.format(file,
                                                                                                        os.path.join(
                                                                                                            des_dir,
                                                                                                            file_name)))
                    print('Search_File_And_Copy_ByDirList end')
                    return False
        print('Search_File_And_Copy_ByDirList end')
        return False
    except:
        print('Search_File_And_Copy_ByDirList exception search_file = {},des_dir = {}'.format(search_file, des_dir))
        print(traceback.format_exc())  # 生成数据库
        return False


def IsRigthInf(one_inf, bIsThridDrv=True):
    try:
        if one_inf.lower() == 'layout.inf':
            return False
        re_str = r'.inf$'
        com = re.compile(re_str)
        ret = com.search(one_inf.lower())
        if ret is None:
            return False

        re_str = r'^oem\d*.inf$'
        com = re.compile(re_str)
        ret = com.search(one_inf.lower())
        if ret is None:
            if bIsThridDrv:
                return False
            else:
                return True
        else:
            if bIsThridDrv:
                return True
            else:
                return False
    except:
        print('IsRigthInf exception inf = {}'.format(one_inf))
        print(traceback.format_exc())  # 生成数据库
        return False


g_not_need_thrid_drv_class_guid_list = ['{4D36E968-E325-11CE-BFC1-08002BE10318}',
                                        '{25DBCE51-6C8F-4A72-8A6D-B54C2B4FC835}',
                                        '{36FC9E60-C465-11CF-8056-444553540000}',
                                        '{3F966BD9-FA04-4EC5-991C-D326973B5128}',
                                        '{4658EE7E-F050-11D1-B6BD-00C04FA372A7}',
                                        '{48721B56-6795-11D2-B1A8-0080C72E74A2}',
                                        '{49CE6AC8-6F86-11D2-B1E5-0080C72E74A2}',
                                        '{4D36E969-E325-11CE-BFC1-08002BE10318}',
                                        '{4D36E96B-E325-11CE-BFC1-08002BE10318}',
                                        '{4D36E96C-E325-11CE-BFC1-08002BE10318}',
                                        '{4D36E96D-E325-11CE-BFC1-08002BE10318}',
                                        '{4D36E96E-E325-11CE-BFC1-08002BE10318}',
                                        '{4D36E96F-E325-11CE-BFC1-08002BE10318}',
                                        '{4D36E977-E325-11CE-BFC1-08002BE10318}',
                                        '{4D36E978-E325-11CE-BFC1-08002BE10318}',
                                        '{4D36E979-E325-11CE-BFC1-08002BE10318}',
                                        '{4D36E980-E325-11CE-BFC1-08002BE10318}',
                                        '{50906CB8-BA12-11D1-BF5D-0000F805F530}',
                                        '{5099944A-F6B9-4057-A056-8C550228544C}',
                                        '{5175D334-C371-4806-B3BA-71FD53C9258D}',
                                        '{6BDD1FC1-810F-11D0-BEC7-08002BE2092F}',
                                        '{6BDD1FC5-810F-11D0-BEC7-08002BE2092F}',
                                        '{6BDD1FC6-810F-11D0-BEC7-08002BE2092F}',
                                        '{6D807884-7D21-11CF-801C-08002BE10318}',
                                        '{72631E54-78A4-11D0-BCF7-00AA00B7B32A}',
                                        '{745A17A0-74D3-11D0-B6FE-00A0C90F57DA}',
                                        '{C06FF265-AE09-48F0-812C-16753D7CBA83}',
                                        '{D48179BE-EC20-11D1-B6B8-00C04FA372A7}',
                                        '{E0CBF06C-CD8B-4647-BB8A-263B43F0F974}',
                                        '{4D36E970-E325-11CE-BFC1-08002BE10318}']


def gen_driver_pool(system_name, driver_pool, bIsThridDrv=True):
    try:
        print('will del dir = {}'.format(driver_pool))
        safe_del_dir(driver_pool)
        while True:
            try:
                print('will create dir = {}'.format(driver_pool))
                os.makedirs(driver_pool)
                print('will create dir end = {}'.format(driver_pool))
            except:
                print('can create dir will retry ,path={}'.format(driver_pool))
                time.sleep(1)
            if os.path.exists(driver_pool):
                print('create dir succe ,path = {}'.format(driver_pool))
                break

        try:
            nReserv = win32file.Wow64DisableWow64FsRedirection()
        except:
            pass

        windows_dir = win32api.GetWindowsDirectory()
        # windows_dir = r'D:\driver_pool\windows'
        inf_dir = os.path.join(windows_dir, 'inf')
        system_dir = win32api.GetSystemDirectory()
        # system_dir = r'D:\driver_pool\windows\system32'
        driver_dir = os.path.join(system_dir, 'drivers')
        windows_disk_dir = windows_dir[0:2]

        print('will enum inf file')
        inf_dir_list = os.listdir(inf_dir)
        print('will enum system')
        system_dir_list = list()
        for root, dirs, files in os.walk(system_dir):
            for file in files:
                system_dir_list.append(os.path.join(root, file))
        print('will enum windows disk volume all file')
        windows_disk_list = list()

        for root, dirs, files in os.walk(windows_disk_dir):
            for file in files:
                windows_disk_list.append(os.path.join(root, file))
        print('enum windows disk volume all file end')

        # 开始查找 inf
        for one_inf in inf_dir_list:
            if IsRigthInf(one_inf, bIsThridDrv) != True:
                continue
            print('will proc inf name = {}'.format(one_inf))
            tmp_path = os.path.join(driver_pool, 'tmp')
            safe_del_dir(tmp_path)
            # 建立临时目录。
            os.makedirs(tmp_path)
            # 拷贝INF文件。
            if bIsThridDrv:
                shutil.copy(os.path.join(inf_dir, one_inf), os.path.join(tmp_path, 'clerware.inf'))
            else:
                shutil.copy(os.path.join(inf_dir, one_inf), os.path.join(tmp_path, one_inf))
            ret, lines = show_and_exe_cmd_line_and_get_ret(
                'inf.exe "' + inf_dir + '\\' + one_inf + '" ' + system_name + ' must ')
            need_file_list = list()
            class_guid = ''
            for one_line in lines:
                get_one_str = one_line.replace('\\', '\\\\')
                get_ret_list = json.loads(get_one_str)
                if 'NeedFile' in get_ret_list.keys():
                    need_file = get_ret_list['NeedFile']
                    need_file_list.append(need_file)
                if 'ClassGUID' in get_ret_list.keys():
                    class_guid = get_ret_list['ClassGUID']
            if bIsThridDrv:
                if class_guid.upper() in g_not_need_thrid_drv_class_guid_list:
                    continue

            if bIsThridDrv:
                # 2003需要查找对应 oemxxx.inf 的oemxxx.cat文件
                need_file_list.append(os.path.splitext(one_inf)[0] + '.cat')

            can_not_find_list = list()
            for need_file in need_file_list:
                print('will search in system dir list')
                bFind = Search_File_And_Copy_ByDirList(need_file, system_dir_list, tmp_path)
                if bFind != True:
                    print('will search in system disk all list')
                    bFind = Search_File_And_Copy_ByDirList(need_file, windows_disk_list, tmp_path)
                    if bFind != True:
                        print('not find file = {}'.format(need_file))
                        can_not_find_list.append(need_file)

            for one_can_not_find in can_not_find_list:
                if one_can_not_find.lower().endswith('.cat'):
                    src_cat = os.path.splitext(one_inf)[0] + '.cat'
                    des_cat = one_can_not_find
                    try:
                        shutil.copy(os.path.join(tmp_path, src_cat), os.path.join(tmp_path, des_cat))
                    except:
                        print('copy not find tmp dir exception cat src = {},des = {}'.format(src_cat, des_cat))
                        # 因为本来就可能找不到CAT
                    print('copy not find tmp dir cat src = {},des = {}'.format(src_cat, des_cat))
                else:
                    # 有可能本来就没有，比如一个inf多个驱动。
                    pass

            hash_obj = hashlib.sha1()
            hash = HashDir(tmp_path, hash_obj)
            des_dir = os.path.join(driver_pool, hash)
            if os.path.exists(des_dir):
                print('have same hash dir = {}'.format(des_dir))
            else:
                print('move dir src = {},des = {}'.format(tmp_path, des_dir))
                shutil.move(tmp_path, des_dir)
        try:
            win32file.Wow64RevertWow64FsRedirection(nReserv)
        except:
            pass
    except:
        print(traceback.format_exc())


def gen_all_id_by_dir(scan_dir, id_file_path):
    try:
        print('gen_all_id_by_dir begin')
        with open(id_file_path, 'w') as out_handle:
            for root, dirs, files in os.walk(scan_dir):
                for file in files:
                    if file.lower().endswith('.inf'):
                        inf_path = os.path.join(root, file)
                        print('scan inf = {}'.format(inf_path))
                        ret, lines = show_and_exe_cmd_line_and_get_ret('inf.exe "' + inf_path)
                        for one_line in lines:
                            get_one_str = one_line.replace('\\', '\\\\')
                            get_ret_list = json.loads(get_one_str)
                            if 'DeviceID' in get_ret_list.keys():
                                device_id = get_ret_list['DeviceID'].strip()
                                if device_id is not None:
                                    if 0 != len(device_id):
                                        out_handle.write(device_id + '\n')
        print('gen_all_id_by_dir end')
    except:
        print(traceback.format_exc())


def gen_driver_pool_spc(system_name, driver_pool, spec_dir):
    try:
        print('will del dir = {}'.format(driver_pool))
        safe_del_dir(driver_pool)
        print('will create dir = {}'.format(driver_pool))
        os.makedirs(driver_pool)

        try:
            nReserv = win32file.Wow64DisableWow64FsRedirection()
        except:
            pass

        for root, dirs, files in os.walk(spec_dir):
            for file in files:
                if file.lower().endswith('.inf'):
                    tmp_path = os.path.join(driver_pool, 'tmp')
                    safe_del_dir(tmp_path)
                    try:
                        shutil.copytree(root, tmp_path)
                        hash_obj = hashlib.sha1()
                        hash = HashDir(tmp_path, hash_obj)
                        des_dir = os.path.join(driver_pool, hash)
                        if os.path.exists(des_dir):
                            print('have same hash dir = {}'.format(des_dir))
                        else:
                            print('move dir src = {},des = {}'.format(tmp_path, des_dir))
                            shutil.move(tmp_path, des_dir)
                    except:
                        pass
        safe_del_dir(tmp_path)
        try:
            win32file.Wow64RevertWow64FsRedirection(nReserv)
        except:
            pass
    except:
        print(traceback.format_exc())


def gen_driver_pool_spc_only_one_dir(system_name, driver_pool, spec_dir):
    try:
        print('gen_driver_pool_spc_only_one_dir will del dir = {}'.format(driver_pool))
        safe_del_dir(driver_pool)
        print('gen_driver_pool_spc_only_one_dir will create dir = {}'.format(driver_pool))
        os.makedirs(driver_pool)

        try:
            nReserv = win32file.Wow64DisableWow64FsRedirection()
        except:
            pass

        file_list = os.listdir(spec_dir)
        for file in file_list:
            if file.lower().endswith('.inf'):
                tmp_path = os.path.join(driver_pool, 'tmp')
                safe_del_dir(tmp_path)
                try:
                    shutil.copytree(spec_dir, tmp_path)
                    hash_obj = hashlib.sha1()
                    hash = HashDir(tmp_path, hash_obj)
                    des_dir = os.path.join(driver_pool, hash)
                    if os.path.exists(des_dir):
                        print('have same hash dir = {}'.format(des_dir))
                    else:
                        print('move dir src = {},des = {}'.format(tmp_path, des_dir))
                        shutil.move(tmp_path, des_dir)
                except:
                    pass
        safe_del_dir(tmp_path)
        try:
            win32file.Wow64RevertWow64FsRedirection(nReserv)
        except:
            pass
    except:
        print(traceback.format_exc())


def gen_user_update_package(system_name, driver_pool, rar_path, bIsThridDrv, spec_dir, bUseSubDir):
    global g_NotUserDriverTmpDir
    try:
        print("gen_user_update_package begin")
        # 建立驱动库。
        g_NotUserDriverTmpDir = 1
        db_path = os.path.join(driver_pool, 'drvierid.db')
        if spec_dir is None:
            gen_driver_pool(system_name, driver_pool, bIsThridDrv)
        else:
            if bUseSubDir:
                gen_driver_pool_spc(system_name, driver_pool, spec_dir)
            else:
                gen_driver_pool_spc_only_one_dir(system_name, driver_pool, spec_dir)
        safe_copy_db(db_path)
        with sqlite3.connect(db_path) as cx:
            cu = cx.cursor()
            # 读取所有数据到临时表。
            enum_class = CEnumIDByInfDir()  # 插入数据之后，需要设置删除标示，del=1,以便对比后来的ok_dev.txt进行处理
            for root, dirs, files in os.walk(driver_pool):
                for file in files:
                    if file.lower().endswith('.inf'):
                        inf_path = os.path.join(root, file)
                        print(r'gen_user_update_package_by_micro search name = {}'.format(inf_path))
                        enum_class.get_one_inf_id_and_class_to_tmp_db(system_name, cu, driver_pool, db_path, inf_path,
                                                                      0, True, True, True)

            # 合并3表到永久表。
            cmd = "delete from id_table"
            _logger.debug(cmd)
            cu.execute(cmd)
            get_list = []
            cmd = "select distinct del,type,class_guid,\
                hard_or_comp_id,ven,dev,inf_driver_ver,inf_path,zip_path,show_name,system_name,time,class_guid,score,e_i_1,e_i_2 from tmp_id_table"
            print(cmd)
            cu.execute(cmd)
            while True:
                one_db = cu.fetchone()
                if one_db is None:
                    break
                get_list.append(one_db)
            for one_db in get_list:
                zip_path = ''
                if one_db[7] != None:
                    pos = one_db[7].find('/')
                    if -1 != pos:
                        zip_path = one_db[7][0:pos] + '.zip'

                cmd = "insert into id_table (server_id,del,type,hard_or_comp_id,inf_driver_ver,inf_path,zip_path,\
                    IsMicro,score,HWPlatform,system_name,time,class_guid,show_name,ven,dev,depends,e_i_1,e_i_2,e_s_1,e_s_2) values (" \
                      + int2sql_and(0, False) + int2sql_and(0) + int2sql_and(2) + str2sql_and(one_db[3]) \
                      + int2sql_and(one_db[6]) + str2sql_and(one_db[7]) + str2sql_and(zip_path) \
                      + int2sql_and(0) + int2sql_and(one_db[13]) + int2sql_and(1) \
                      + str2sql_and(one_db[10]) + str2sql_and(one_db[11]) + str2sql_and(one_db[2]) \
                      + str2sql_and(one_db[9]) + str2sql_and(one_db[4]) + str2sql_and(one_db[5]) \
                      + str2sql_and('') + int2sql_and(0) + int2sql_and(0) + str2sql_and('') + str2sql_and('') + ")"
                # _logger.debug(cmd)
                cu.execute(cmd)
        cx.close()

        # 如果有tmp 目录，需要先删除tmp 目录。
        safe_del_dir(os.path.join(driver_pool, 'tmp'))
        # 压缩提取驱动库所有文件夹成zip
        hash_file_list = os.listdir(driver_pool)
        for one in hash_file_list:
            if os.path.isdir(os.path.join(driver_pool, one)):
                dirname = os.path.join(driver_pool, one)
                zipfilename = dirname + '.zip'
                print('zip_dir src = {},des = {}'.format(dirname, zipfilename))
                if os.path.exists(zipfilename):
                    print('find same hash zip file,continue');
                else:
                    try:
                        zip_dir(dirname, zipfilename)
                    except:
                        print(traceback.format_exc())
                safe_del_dir(dirname)

        ini_handle = configparser.ConfigParser()
        ini_handle.add_section('Main')
        ini_handle.set('Main', 'sig_str', '')
        ini_handle.set('Main', 'sig_file', '')
        ini_handle.set('Main', 'type', str(5))

        with open(os.path.join(driver_pool, 'sig.ini'), 'w') as file_handle:
            ini_handle.write(file_handle)

        zip_path = driver_pool + '.zip'
        try:
            os.remove(zip_path)
        except Exception as e:
            pass

        show_and_exe_cmd_line_and_get_ret(
            '"' + rar_path + '" a -p{89B87785-0C5F-47eb-B7DE-73DD962B0FAE} -afzip -ep1 ' + zip_path + ' ' + driver_pool + '\*')
        # zip_dir(driver_pool,driver_pool+'.zip','{89B87785-0C5F-47eb-B7DE-73DD962B0FAE}')
        print('gen_user_update_package end')
    except:
        print(traceback.format_exc())
        print('gen_user_update_package end')


def mul_db():
    db_path = r'O:\db\drvierid.db'
    safe_copy_db(db_path)
    num = 1
    try:
        db_list = ['drvierid_1.db', 'drvierid_2.db', 'drvierid_3.db', 'drvierid_4.db', 'drvierid_5.db', 'drvierid_6.db',
                   'drvierid_7.db']
        with sqlite3.connect(db_path) as cx:
            cu = cx.cursor()
            for one_db_name in db_list:
                with sqlite3.connect('O:\\db\\' + one_db_name) as lx:
                    lu = lx.cursor()
                    # 读取所有数据到临时表。
                    print('db is = {}'.format('O:\\db\\' + one_db_name))
                    cmd = "select * from id_table"
                    print(cmd)
                    lu.execute(cmd)
                    while True:
                        # 数据库构建时，保持了唯一性
                        one_db = lu.fetchone()
                        if one_db is None:
                            break
                        # 插入数据之后，需要设置删除标示，del=1,以便对比后来的ok_dev.txt进行处理
                        cmd = "insert into tmp_id_table (del,type,class_guid,hard_or_comp_id,show_name) values (" \
                              + int2sql(0) + "," + int2sql(1) + "," \
                              + str2sql(one_db[5]) + "," + str2sql(one_db[7]) + "," + str2sql(one_db[6]) + ")"
                        print('insert num={}'.format(num))
                        cu.execute(cmd)
                        num = num + 1

        cmd = "insert into id_table(del,type,class_guid,hard_or_comp_id,show_name,comment)\
                 select 0,0,tmp_id_table.class_guid,tmp_id_table.hard_or_comp_id,tmp_id_table.show_name, \
                 count(distinct tmp_id_table.hard_or_comp_id) \
                 from tmp_id_table group by hard_or_comp_id order by tmp_id_table.hard_or_comp_id"
        _logger.debug(cmd)
        cu.execute(cmd)
        cx.commit()

    except:
        print(traceback.format_exc())


def fix_error_inf():
    try:
        path = r'O:\driver_pool'
        for root, dirs, files in os.walk(path):
            for file in files:
                if file.lower().endswith('.inf') != True:
                    continue
                if file == 'clerware.inf':
                    continue
                file_before = file[0:len('clerware')]
                if file_before == 'clerware':
                    shutil.move(os.path.join(root, file), os.path.join(root, 'clerware.inf'))
                    print('fix_error_inf src={},des={}'.format(os.path.join(root, file),
                                                               os.path.join(root, 'clerware.inf')))
    except:
        _logger.error(traceback.format_exc())  # 生成数据库


# ===================================================================================================================
def getOSType(ostype, system_name):
    if system_name not in ostype:
        ostype.append(system_name)
    ostype.sort()
    return ostype


def myPostDel(updateurl, system_name, hash):
    try:
        ostype = list()
        getOSType(ostype, system_name)
        for one_os_id in ostype:
            url = updateurl + '/index.php/handler/driver/?a=setzipusedbyhash'
            print(url)
            post_data = dict()
            post_data['used'] = 0
            post_data['os_id'] = one_os_id
            post_data['hash'] = hash
            r = requests.post(url, data=post_data)
            jsonstr = str(r.content, encoding="utf-8")
            print(jsonstr)
    except:
        _logger.error(traceback.format_exc())  # 生成数据库


def GetDBSysNameByInfSysName(inf_sys_name):
    try:
        bFindOldVer = False
        for one_os_name in g_os_name_list:
            if one_os_name['os_name'] == inf_sys_name:
                # 老版本号被找到。兼容处理。
                bFindOldVer = True
                if 0 == one_os_name['bIs64']:
                    db_system_name = 'NTX86.' + str(one_os_name['major']) + '.' + str(one_os_name['min'])
                else:
                    db_system_name = 'NTAMD64.' + str(one_os_name['major']) + '.' + str(one_os_name['min'])
                return db_system_name
        if bFindOldVer is not True:
            # 老版本号未找到。采用新版本号处理。
            ver_list = inf_sys_name.split('.')
            if 0 == int(ver_list[2]):
                db_system_name = 'NTX86.' + str(int(ver_list[0])) + '.' + str(int(ver_list[1]))
            elif 9 == int(ver_list[2]):
                db_system_name = 'NTAMD64.' + str(int(ver_list[0])) + '.' + str(int(ver_list[1]))
            else:
                _logger.error('inf_sys_name err = {},ver_list = {}'.format(inf_sys_name, ver_list))
                print('inf_sys_name err = {},ver_list = {}'.format(inf_sys_name, ver_list))
                sys.exit(0)
                return None
            return db_system_name
        return None
    except:
        _logger.error(traceback.format_exc())  # 生成数据库
        print(traceback.format_exc())  # 生成数据库
        return None


def DelServ(del_drv_pool, updateurl):
    try:
        file_list = os.listdir(del_drv_pool)
        for one_file in file_list:
            if IsNewSystemCatNameList(one_file):
                # 获取操作系统名字。
                system_name = one_file
                dir_list = list()
                system_name_full_path = os.path.join(del_drv_pool, system_name)
                for root, dirs, files in os.walk(system_name_full_path):
                    for file in files:
                        if file.lower().endswith('.inf'):
                            inf_dir = os.path.dirname(os.path.join(root, file))
                            dir_list.append(inf_dir)
                dir_list = list(set(dir_list))
                print('dir count = {}'.format(len(dir_list)))
                for one_dir in dir_list:
                    hash_obj = hashlib.sha1()
                    hash = HashDir(one_dir, hash_obj)
                    print('will del dir = {} ,hash = {}'.format(one_dir, hash))
                    myPostDel(updateurl, GetDBSysNameByInfSysName(system_name), hash)
                try:
                    with open(os.path.join(system_name_full_path, 'del_hash.txt'), 'r') as a_handle:
                        while True:
                            line = a_handle.readline()
                            hash = line.upper().strip()
                            hash = hash.strip('"')
                            if not line:
                                break
                            myPostDel(updateurl, GetDBSysNameByInfSysName(system_name), hash)
                except:
                    print('{} no del_hash.txt'.format(system_name_full_path))

    except:
        _logger.error(traceback.format_exc())  # 生成数据库


def print_hash(dir_path):
    try:
        hash_obj = hashlib.sha1()
        hash = HashDir(dir_path, hash_obj)
        print('hash={},dir={}'.format(hash, dir_path))
    except:
        _logger.error(traceback.format_exc())  # 生成数据库


class Update(object):
    """
    target_dir: 包含整个解开压缩包的路径
    or_db_name：压缩包中原始的数据库名
    new_db_name：新的数据库名
    driver_pool_path：驱动库的路径
    update_type：更新类型，3/5, 3服务器更新，5用户手动更新
    """

    def __init__(self, target_dir, or_db_name, new_db_name, driver_pool_path, update_type):
        self.target_dir = target_dir
        self.or_db_filename = or_db_name
        self.new_db_filename = new_db_name
        self.driver_pool_path = driver_pool_path
        self.update_type = update_type

    @staticmethod
    def _get_zip_file_path(work_dir):
        file_names = os.listdir(work_dir)
        return list(filter(lambda x: x.endswith('.zip'), file_names))

    @staticmethod
    def _set_del_in_db(cu, del_zip_list):
        for zip_path in del_zip_list:
            _logger.info("_set_del_in_db exe update zip_path={}".format(zip_path))
            cu.execute('UPDATE id_table SET del=:value WHERE zip_path=:path', {'value': 1, 'path': zip_path + '.zip'})

    @staticmethod
    def _remove_zip_file(dir_path, del_path_list):
        for path in del_path_list:
            try:
                file_path = os.path.join(dir_path, path)
                _logger.info('_remove_zip_file remove driver path :{}'.format(file_path))
                shutil.rmtree(file_path)
            except Exception as e:
                _logger.error('_remove_zip_file fail {}'.format(e))

    def work(self):
        # 拷贝文件至 驱动库
        self._copy_zip_file_to_driver_pool()

        # 用户手动上传更新
        if self.update_type == 5:
            src_db_path = os.path.join(self.target_dir, self.or_db_filename)
            dst_db_path = os.path.join('/var/db/', self.new_db_filename)
            # 头一次上传
            if not os.path.exists(dst_db_path):
                self._copy_db_to_db_dir()  # 直接拷贝驱动库db文件 至目标目录
            # 更新上传
            else:
                self._update_local_db(src_db_path, dst_db_path)
        # 一体机本地更新
        else:
            db_path = os.path.join(self.target_dir, self.or_db_filename)
            self._copy_db_to_db_dir()
        self._commpare_local_file_and_db()

        _logger.info("Update successful")

    def copytree(self, src, dst, symlinks=False):
        names = os.listdir(src)
        if not os.path.isdir(dst):
            os.makedirs(dst)

        errors = []
        for name in names:
            srcname = os.path.join(src, name)
            dstname = os.path.join(dst, name)
            try:
                if symlinks and os.path.islink(srcname):
                    linkto = os.readlink(srcname)
                    os.symlink(linkto, dstname)
                elif os.path.isdir(srcname):
                    self.copytree(srcname, dstname, symlinks)
                else:
                    if os.path.isdir(dstname):
                        os.rmdir(dstname)
                    elif os.path.isfile(dstname):
                        os.remove(dstname)
                    shutil.copy2(srcname, dstname)
                    # XXX What about devices, sockets etc.?
            except (IOError, os.error) as why:
                errors.append((srcname, dstname, str(why)))
            # catch the Error from the recursive copytree so that we can
            # continue with other files
            except OSError as err:
                errors.extend(err.args[0])
        try:
            shutil.copystat(src, dst)
        except WindowsError:
            # can't copy file access times on Windows
            pass
        except OSError as why:
            errors.extend((src, dst, str(why)))
        if errors:
            raise shutil.Error(errors)

    def _copy_zip_file_to_driver_pool(self):
        driver_pool_dir = os.path.join(self.target_dir, 'driver_pool')
        if os.path.isdir(driver_pool_dir):
            # driver_pool中的文件已经解压，则直接拷贝过去
            if os.path.isdir(self.driver_pool_path):
                shutil.rmtree(self.driver_pool_path)
            self.copytree(driver_pool_dir, self.driver_pool_path)
            src_len = len(os.listdir(driver_pool_dir))
            dest_len = len(os.listdir(self.driver_pool_path))
            shutil.rmtree(driver_pool_dir)
            if src_len != dest_len:
                raise Exception('共{}个文件，拷贝了{}个文件'.format(src_len, dest_len))
            return
        source_zip_file_names = self._get_zip_file_path(self.target_dir)
        _logger.info('_copy_zip_file_to_driver_pool, src_zip_path:{}'.format(source_zip_file_names))
        for file_name in source_zip_file_names:
            source_path = os.path.join(self.target_dir, file_name)
            # 清空、创建目标目录
            dst_dir = os.path.join(self.driver_pool_path, os.path.splitext(file_name)[0])
            if os.path.exists(dst_dir):
                _logger.debug("_copy_zip_file_to_driver_pool delete path:{}".format(dst_dir))
                shutil.rmtree(dst_dir)
            os.makedirs(dst_dir)
            # 解压zip文件至目标目录
            cmd = 'unzip -P{89B87785-0C5F-47eb-B7DE-73DD962B0FAE} ' + source_path + ' -d ' + dst_dir
            info = _excute_cmd_and_return_code(cmd)
            if info[0] != 0:
                _logger.error('copy file fail, file path {}, error:{}'.format(source_path, info[1]))

    def _update_local_db(self, src_db_path, dst_db_path):
        with sqlite3.connect(src_db_path) as conn:
            _logger.info("_update_local_db src_db_path:{}, dst_db_path:{}".format(src_db_path, dst_db_path))
            conn.row_factory = sqlite3.Row
            src_cu = conn.cursor()
            with sqlite3.connect(dst_db_path) as conn1:
                conn1.row_factory = sqlite3.Row
                dst_cu = conn1.cursor()

                sql = "SELECT * FROM id_table"
                src_cu.execute(sql)
                src_all_rows = src_cu.fetchall()
                for row in src_all_rows:
                    update_one(dst_cu, row, None, None, None)
                conn1.commit()

    def chk_and_copy_drvierid_user(self):
        try:
            _logger.warning(r'chk_and_copy_drvierid_user begin')
            src_user_db_path = '/sbin/aio/box_dashboard/xdashboard/handle/drvierid_user.db'
            user_db_path = '/var/db/drvierid_user.db'
            if not os.path.exists(user_db_path):
                shutil.copyfile(src_user_db_path, user_db_path)
                _logger.warning(r'chk_and_copy_drvierid_user if not os.path.exists(user_db_path) end')
                return
            if 0 == os.path.getsize(user_db_path):
                while True:
                    _logger.warning(r'chk_and_copy_drvierid_user if 0 == os.path.getsize(user_db_path) :will remove')
                    os.remove(user_db_path)
                    if not os.path.exists(user_db_path):
                        _logger.warning(
                            r'chk_and_copy_drvierid_user if 0 == os.path.getsize(user_db_path) :remove not find file')
                        break
                    _logger.warning(
                        r'chk_and_copy_drvierid_user if 0 == os.path.getsize(user_db_path) :remove have find file,will sleep')
                    time.sleep(1)
                shutil.copyfile(src_user_db_path, user_db_path)
                _logger.warning(r'chk_and_copy_drvierid_user if 0 == os.path.getsize(user_db_path) end')
                return
        except Exception as e:
            tb = traceback.format_exc()
            _logger.warning(r'chk_and_copy_drvierid_user failed {} {}'.format(e, tb))

    def _commpare_local_file_and_db(self):
        """
        比较本地驱动库文件与数据库文件。本地文件没有出现在数据库，则删除此文件；数据库zip_path没有找到本地文件，
        则设置del=1
        :return: N
        """
        _logger.info("start commpare local file and driver_pool")
        local_zip_file_names = list(
            filter(lambda x: os.path.isdir(os.path.join(self.driver_pool_path, x)), os.listdir(self.driver_pool_path)))
        local_db_path = '/var/db/drvierid.db'
        user_db_path = '/var/db/drvierid_user.db'
        self.chk_and_copy_drvierid_user()

        # 更新本地驱动数据库
        with sqlite3.connect(local_db_path) as conn:
            _logger.info('connect db {}'.format(local_db_path))
            cu = conn.cursor()
            sql = "SELECT zip_path FROM id_table WHERE zip_path!=''"
            cu.execute(sql)
            all_rows = cu.fetchall()
            all_rows = list(map(lambda x: os.path.splitext(x[0])[0], all_rows))
            to_set_del_in_db_zip_list = set(all_rows) - set(local_zip_file_names)
            to_delete_zip_list = set(local_zip_file_names) - set(all_rows)

            self._set_del_in_db(cu, to_set_del_in_db_zip_list)
            conn.commit()

        # 更新用户驱动数据库
        if os.path.exists(user_db_path):
            with sqlite3.connect(user_db_path) as conn1:
                _logger.info('connect db {}'.format(user_db_path))
                cu1 = conn1.cursor()
                sql = "SELECT zip_path FROM id_table WHERE zip_path!=''"
                cu1.execute(sql)
                all_rows_user = cu1.fetchall()
                all_rows_user = list(map(lambda x: os.path.splitext(x[0])[0], all_rows_user))
                to_set_del_in_db_zip_list_user = set(all_rows_user) - set(local_zip_file_names)
                to_delete_zip_list_user = set(local_zip_file_names) - set(all_rows_user)

                self._set_del_in_db(cu1, to_set_del_in_db_zip_list_user)
                conn1.commit()
            to_delete_zip_list = to_delete_zip_list & to_delete_zip_list_user

        # 删除多余文件
        self._remove_zip_file(self.driver_pool_path, to_delete_zip_list)

    def _copy_db_to_db_dir(self):
        src_path = os.path.join(self.target_dir, self.or_db_filename)
        dest_path = os.path.join('/var/db/', self.new_db_filename)
        _logger.info("_copy_db_to_db_dir src_path:{},dst_path:{}".format(src_path, dest_path))
        shutil.copyfile(src_path, dest_path)


if __name__ == "__main__":
    # global g_db_path
    # global driver_pool
    # global system_cat_name_list
    xlogging.basicConfig(stream=sys.stdout, level=xlogging.NOTSET)
    cur_file_dir_str = cur_file_dir()
    os.chdir(cur_file_dir_str)

    _logger.debug(cur_file_dir_str)

    all_sql_info_list = []
    one_sql_info_list = {'server_id': None, 'del': None, 'show_name': None, 'hard_or_comp_id': None,
                         'inf_driver_ver': None, 'inf_path': None, 'zip_path': None, 'system_name': None,
                         'IsMicro': None, 'score': None, 'HWPlatform': None, 'depends': None,
                         'e_i_1': 0, 'e_i_2': 0, 'e_s_1': None, 'e_s_2': None}
    one_sql_info_list['server_id'] = None
    one_sql_info_list['del'] = 0
    one_sql_info_list['show_name'] = 'wolf 1'
    one_sql_info_list['hard_or_comp_id'] = r'PCI\VEN_10EC&DEV_8136&REV_1F'
    one_sql_info_list['inf_driver_ver'] = 7881
    one_sql_info_list['inf_path'] = r'abcd\1.inf'
    one_sql_info_list['zip_path'] = r'abcd.zip'
    one_sql_info_list['system_name'] = '10_X64'
    all_sql_info_list.append(one_sql_info_list.copy())
    one_sql_info_list['server_id'] = None
    one_sql_info_list['del'] = 0
    one_sql_info_list['show_name'] = 'wolf 2'
    one_sql_info_list['hard_or_comp_id'] = r'PCI\VEN_10EC&DEV_8136&SUBSYS_813610EC&REV_1F'
    one_sql_info_list['inf_driver_ver'] = 7882
    one_sql_info_list['inf_path'] = r'abcdaa\2.inf'
    one_sql_info_list['zip_path'] = r'abcdaa.zip'
    one_sql_info_list['system_name'] = '10_X64'
    all_sql_info_list.append(one_sql_info_list.copy())
    one_sql_info_list['server_id'] = None
    one_sql_info_list['del'] = 0
    one_sql_info_list['show_name'] = None
    one_sql_info_list['hard_or_comp_id'] = r'PCI\VEN_2000&DEV_0055&SUBSYS_346C8086'
    one_sql_info_list['inf_driver_ver'] = None
    one_sql_info_list['inf_path'] = None
    one_sql_info_list['zip_path'] = None
    one_sql_info_list['system_name'] = '10_X64'
    all_sql_info_list.append(one_sql_info_list.copy())

    if len(sys.argv) >= 2:
        if -1 != sys.argv[1].find("print_hash"):
            print_hash(sys.argv[2])
            exit(0)
        if -1 != sys.argv[1].find("DelServ"):
            DelServ(sys.argv[2], sys.argv[3])
            exit(0)
        elif -1 != sys.argv[1].find("fix_error_inf"):
            fix_error_inf()
            exit(0)
        elif -1 != sys.argv[1].find("mul_db"):
            mul_db()
            exit(0)
        # if -1 != sys.argv[1].find("scan_all_no_guid"):
        #     scan_all_no_guid(db_path, driver_pool)
        #     exit(0)
        elif -1 != sys.argv[1].find("scan_micro"):
            db_path = r'O:\driver_pool\uploadtosqldb\drvierid.db'
            driver_pool = r'O:\driver_pool\micro\scan'
            driver_pool_update = r'O:\driver_pool\driver_pool'
            scan_micro(db_path, driver_pool, driver_pool_update)
            exit(0)
        elif -1 != sys.argv[1].find("gen_micro_pool"):
            system_name = sys.argv[2]
            if (len(sys.argv) != 4) or (IsNewSystemCatNameList(system_name) is False):
                print('python enum_id.py gen_micro_pool 10.00.09.01 O:\driver_pool\mdriver_pool')
                exit(0)
            system_name = sys.argv[2]
            gen_driver_pool(system_name, sys.argv[3], False)
            exit(0)
        elif -1 != sys.argv[1].find("gen_user_update_package_by_micro"):
            system_name = sys.argv[2]
            if (len(sys.argv) != 5) or (IsNewSystemCatNameList(system_name) is False):
                print(
                    'python enum_id.py gen_user_update_package_by_micro 10.00.09.01 O:\driver_pool\driver_pool "C:\Program Files\WinRAR\WinRAR.exe" ')
                exit(0)
            gen_user_update_package(system_name, sys.argv[3], sys.argv[4], True, None, False)
            exit(0)
        elif -1 != sys.argv[1].find("gen_user_update_package_by_spec_loop"):
            system_name = sys.argv[2]
            if (len(sys.argv) != 6) or (IsNewSystemCatNameList(system_name) is False):
                print(
                    'python enum_id.py gen_user_update_package_by_spec_loop 10.00.09.01 O:\driver_pool\driver_pool_update "C:\Program Files\WinRAR\WinRAR.exe" O:\driver_pool\driver_pool ')
            gen_user_update_package(system_name, sys.argv[3], sys.argv[4], False, sys.argv[5], True)
            exit(0)
        elif -1 != sys.argv[1].find("gen_user_update_package_by_spec"):
            system_name = sys.argv[2]
            if (len(sys.argv) != 6) or (IsNewSystemCatNameList(system_name) is False):
                print(
                    'python enum_id.py gen_user_update_package_by_spec 10.00.09.01 O:\driver_pool\driver_pool_update "C:\Program Files\WinRAR\WinRAR.exe" O:\driver_pool\driver_pool ')
            gen_user_update_package(system_name, sys.argv[3], sys.argv[4], False, sys.argv[5], False)
            exit(0)
        elif -1 != sys.argv[1].find("gen_all_id_by_dir"):
            if len(sys.argv) != 4:
                print('python enum_id.py gen_all_id_by_dir scan_dir id_file_path')
                exit(0)
            scan_dir = sys.argv[2]
            id_file_path = sys.argv[3]
            gen_all_id_by_dir(scan_dir, id_file_path)
            exit(0)
        elif -1 != sys.argv[1].find("gen_driver_pool"):
            system_name = sys.argv[2]
            if (len(sys.argv) != 4) or (IsNewSystemCatNameList(system_name) is False):
                print('python enum_id.py gen_driver_pool 10.00.09.01 O:\driver_pool\driver_pool')
                exit(0)
            system_name = sys.argv[2]
            gen_driver_pool(system_name, sys.argv[3])
            exit(0)
        elif -1 != sys.argv[1].find("hash"):
            db_path = r'O:\driver_pool\uploadtosqldb\drvierid.db'
            driver_pool = r'O:\driver_pool\normal_drv\scan'
            driver_pool_update = r'O:\driver_pool\driver_pool'
            safe_copy_db(db_path)
            with sqlite3.connect(db_path) as cx:
                cu = cx.cursor()
                hash_driver_pool(cu, db_path, driver_pool, driver_pool_update)
            exit(0)
        elif -1 != sys.argv[1].find("scan_no_guid"):
            db_path = r'O:\driver_pool\uploadtosqldb\drvierid.db'
            driver_pool = r'O:\driver_pool\no_guid\scan'
            driver_pool_update = r'O:\driver_pool\driver_pool'
            scan_no_guid(db_path, driver_pool, driver_pool_update)
            exit(0)
        elif -1 != sys.argv[1].find("scan_new_add_spec"):
            db_path = r'O:\driver_pool\uploadtosqldb\drvierid.db'
            driver_pool = r'O:\driver_pool\normal_drv\scan'
            driver_pool_update = r'O:\driver_pool\driver_pool'
            scan_new_add_spec(db_path, driver_pool, driver_pool_update)
            exit(0)
        elif -1 != sys.argv[1].find("scan_new"):
            db_path = r'O:\driver_pool\uploadtosqldb\drvierid.db'
            driver_pool = r'O:\driver_pool\normal_drv\scan'
            driver_pool_update = r'O:\driver_pool\driver_pool'
            scan_new(db_path, driver_pool, driver_pool_update)
            exit(0)
        elif -1 != sys.argv[1].find("scan_one_dir"):
            db_path = r'O:\driver_pool\uploadtosqldb\drvierid.db'
            driver_pool = r'O:\driver_pool\normal_drv\scan\05.02.09'
            driver_pool_update = r'O:\driver_pool\driver_pool'
            scan_one_dir(db_path, driver_pool, driver_pool_update)
            exit(0)
        elif -1 != sys.argv[1].find("scan"):
            db_path = r'O:\driver_pool\uploadtosqldb\drvierid.db'
            driver_pool = r'O:\driver_pool\normal_drv\scan'
            driver_pool_update = r'O:\driver_pool\driver_pool'
            scan(db_path, driver_pool, driver_pool_update)
            exit(0)
        elif -1 != sys.argv[1].find("gen_zip"):
            db_path = r'O:\driver_pool\uploadtosqldb\drvierid.db'
            driver_pool = r'O:\driver_pool\normal_drv\scan'
            driver_pool_update = r'O:\driver_pool\driver_pool'
            gen_zip(db_path, driver_pool, driver_pool_update)
            exit(0)
        elif -1 != sys.argv[1].find("get_list"):
            db_path = r'O:\driver_pool\uploadtosqldb\drvierid.db'
            driver_pool = r'O:\driver_pool\normal_drv\scan'
            driver_pool_update = r'O:\driver_pool\driver_pool'
            get_list(db_path, driver_pool, driver_pool_update)
            exit(0)
        elif -1 != sys.argv[1].find("get_region"):
            db_path = r'O:\driver_pool\uploadtosqldb\drvierid.db'
            driver_pool = r'O:\driver_pool\normal_drv\scan'
            driver_pool_update = r'O:\driver_pool\driver_pool'
            driverlist = get_region(db_path)
            print(driverlist)
            exit(0)
        elif -1 != sys.argv[1].find("update_one_zip_of_db"):
            db_path = r'O:\driver_pool\uploadtosqldb\drvierid.db'
            driver_pool = r'O:\driver_pool\normal_drv\scan'
            driver_pool_update = r'O:\driver_pool\driver_pool'
            # safe_copy_db(db_path)
            # 工厂生成全新的数据库
            with sqlite3.connect(db_path) as cx:
                cu = cx.cursor()
                update_one_zip_of_db(cu, all_sql_info_list, db_path)
                cx.commit()
            exit(0)
        elif -1 != sys.argv[1].find("update_one_zip"):
            db_path = r'O:\driver_pool\uploadtosqldb\drvierid.db'
            driver_pool = r'O:\driver_pool\normal_drv\scan'
            driver_pool_update = r'O:\driver_pool\driver_pool'
            # 更新一个驱动
            safe_copy_db(db_path)
            with sqlite3.connect(db_path) as cx:
                cu = cx.cursor()
                update_one_zip(cu, all_sql_info_list, db_path, driver_pool_update,
                               r'O:\driver_pool\10_X64\drivers.2016.10.06\e580f68bb0826a0acd3afa5744487a19517840db.zip')
                update_one_zip(cu, all_sql_info_list, db_path, driver_pool, None)
                cx.commit()
            exit(0)
        else:
            db_path = r'O:\driver_pool\uploadtosqldb\drvierid.db'
            driver_pool = r'O:\driver_pool\normal_drv\scan'
            driver_pool_update = r'O:\driver_pool\driver_pool'
            scan(db_path, driver_pool, driver_pool_update)
            gen_zip(db_path, driver_pool, driver_pool_update)
            # 上传驱动
            _logger.debug(get_list(db_path, driver_pool, driver_pool_update))
            # 更新驱动
            get_region(db_path)
            exit(0)
