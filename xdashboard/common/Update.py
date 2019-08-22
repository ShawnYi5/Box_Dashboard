import base64
import configparser
import json
import os
import shutil
import sys
import time
import traceback

import rsa

from box_dashboard import xlogging
from box_dashboard.boxService import box_service

# =========================================================================================
_logger = xlogging.getLogger(__name__)


# =========================================================================================

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
        cmd_line = in_cmd_line + ' 2>&1'
        _logger.debug(cmd_line)
        with os.popen(cmd_line) as out_put:
            out_put_lines = out_put.readlines()
            if '' == chk_err_str:
                _logger.debug('0'), _logger.debug(out_put_lines)
                return 0, out_put_lines
            for one_line in out_put_lines:
                if -1 != one_line.find(chk_err_str):
                    _logger.debug('-1'), _logger.debug(out_put_lines)
                    return -1, out_put_lines
        _logger.debug('0'), _logger.debug(out_put_lines)
        return 0, out_put_lines
    except:
        _logger.error(traceback.format_exc())
        _logger.error('-1'), _logger.error(out_put_lines)
        return -1, out_put_lines


class CUpdate:
    def __init__(self):
        try:
            self.UpdateInfoList = list()
            self.current_dir = os.path.split(os.path.realpath(__file__))[0]
        except:
            _logger.error(traceback.format_exc())

    def AddOneUpdateToList(self, file_name, intro, log, id, type, date, size, ver, additional):
        try:
            one_info = {'file_name': file_name, 'intro': intro, 'log': log, 'id': id, 'type': type, 'date': date,
                        'size': size, 'ver': ver, 'additional': additional}
            self.UpdateInfoList.append(one_info)
        except:
            _logger.error(traceback.format_exc())

    def GenAllUpdateJsonFileAndCleanList(self, json_file_path):
        try:
            with open(json_file_path, 'w') as file_handle:
                file_handle.write(json.dumps(self.UpdateInfoList))
            self.UpdateInfoList = list()
        except:
            _logger.error(traceback.format_exc())
            self.UpdateInfoList = list()

    def WriteSigToFile(self, priv_key_path, file_path, sig_path, type):
        try:
            base64_sig_str = self.RsaSigFile_base64(priv_key_path, file_path)
            cf = configparser.ConfigParser()
            cf.add_section("Main")
            if base64_sig_str is None:
                cf.set("Main", "sig_str", "")
                cf.set("Main", "sig_file", "")
                cf.set("Main", "type", str(type))
            else:
                cf.set("Main", "sig_str", base64_sig_str)
                cf.set("Main", "sig_file", os.path.basename(file_path))
                cf.set("Main", "type", str(type))
            cf.write(open(sig_path, "w"))
        except:
            _logger.error(traceback.format_exc())

    def GenRsaKeyPair(self, pub_key_path, priv_key_path):
        try:
            (pubkey, privkey) = rsa.newkeys(1024)
            pub = pubkey.save_pkcs1()
            with open(pub_key_path, 'wb') as pubfile:
                pubfile.write(pub)

            pri = privkey.save_pkcs1()
            with open(priv_key_path, 'wb') as prifile:
                prifile.write(pri)
        except:
            _logger.error(traceback.format_exc())

    def RsaSigFile_base64(self, priv_key_path, file_path):
        try:
            base64_sig_str = None
            with open(priv_key_path, 'rb') as privfile:
                p = privfile.read()
                privkey = rsa.PrivateKey.load_pkcs1(p)
                with open(file_path, 'rb')as file_handle:
                    bin_sig_str = rsa.sign(file_handle, privkey, 'SHA-1')
                    _logger.debug(bin_sig_str)
                    base64_sig_str = base64.b64encode(bin_sig_str).decode()
            return base64_sig_str
        except:
            _logger.error(traceback.format_exc())
            return None

    def RsaVerSigFile_base64(self, pub_key_path, file_path, base64_sig_str):
        try:
            bResult = False
            rsa_md5_string = base64.b64decode(base64_sig_str.encode())
            _logger.debug(rsa_md5_string)
            with open(pub_key_path, 'rb') as pubfile:
                p = pubfile.read()
                pubkey = rsa.PublicKey.load_pkcs1(p)
                with open(file_path, 'rb')as file_handle:
                    bResult = rsa.verify(file_handle, rsa_md5_string, pubkey)
            return bResult
        except:
            _logger.error(traceback.format_exc())
            return False

    def Update(self, first_have_zip_dir, update_callback=None, user=None, logfilename=None, operator=None):
        _logger.debug('Update first_have_zip_dir={}'.format(first_have_zip_dir))
        try:
            # 读取签名字符串
            sig_ini_path = os.path.join(first_have_zip_dir, 'sig.ini')
            cf = configparser.ConfigParser()
            cf.read_file(open(sig_ini_path))
            type = cf.get("Main", "type")
            base64_sig_str = cf.get("Main", "sig_str")
            sig_file_base_name = cf.get("Main", "sig_file")
            _logger.debug(
                'Update sig.ini base64_sig_str = {} , sig_file_base_name = {} ,type={}\n'.format(base64_sig_str,
                                                                                                 sig_file_base_name,
                                                                                                 type))
            _logger.debug('Update will VerSig')
            if 0 == len(base64_sig_str):
                pass
            else:
                sig_file_path = os.path.join(first_have_zip_dir, sig_file_base_name)

                # with zipfile.ZipFile(file_path) as zip_handle:
                #     zip_handle.extractall(file_dir, None, pwd='{89B87785-0C5F-47eb-B7DE-73DD962B0FAE}'.encode())

                _logger.debug('Update will VerSig')
                # 校验签名
                bVerSigResult = self.RsaVerSigFile_base64(os.path.join(self.current_dir, 'update_pub.pem'),
                                                          sig_file_path,
                                                          base64_sig_str)
                _logger.debug('Update bResult = {}'.format(bVerSigResult))
                if bVerSigResult is not True:
                    bsuccess = False
                    reason = '文件签名不正确'
                    if update_callback:
                        update_callback(user, logfilename, bsuccess, reason, operator)
                    return False

                # 第二次，解压缩签名的ZIP
                ret, lines = show_and_exe_cmd_line_and_get_ret('unzip ' + sig_file_path + ' -d' + first_have_zip_dir)

            ret, lines = show_and_exe_cmd_line_and_get_ret('chmod -R 777 ' + first_have_zip_dir)
            # 查找并执行aio_update.sh
            for root, dirs, files in os.walk(first_have_zip_dir):
                for file in files:
                    if file == 'update.sh':
                        _logger.debug(r'will run update file = {}'.format(os.path.join(root, file)))
                        if type == "1":
                            cmd = r'cd "{}";sh "{}" -n unchanged'.format(first_have_zip_dir, os.path.join(root, file))
                        else:
                            cmd = r'cd "{}";sh "{}"'.format(first_have_zip_dir, os.path.join(root, file))
                        bsuccess = True
                        reason = ''
                        if update_callback:
                            update_callback(user, logfilename, bsuccess, reason, operator)
                        box_service.installfun(cmd)
                        # ret, lines = show_and_exe_cmd_line_and_get_ret(os.path.join(root, file))

                        return True

            bsuccess = False
            reason = '没找到update.sh'
            if update_callback:
                update_callback(user, logfilename, bsuccess, reason, operator)
            return False
        except:
            _logger.error(traceback.format_exc())
            bsuccess = False
            reason = traceback.format_exc()
            if update_callback:
                update_callback(user, logfilename, bsuccess, reason, operator)
            return False


def GenType1SigFile():
    try:
        rar_path = r'"C:\Program Files\WinRAR\WinRAR.exe"'
        work_dir = r'D:\work\code\aio\update_make'
        will_package_dir1 = '2016_08_10-17_59_56'
        will_package_dir1_zip_name = will_package_dir1 + '.zip'
        will_package_dir2 = 'aio'
        will_package_dir2_zip_name = will_package_dir2 + '.zip'
        out_dir = 'out'
        # ===============================================================================================================
        # 删除原来的生成文件
        try:
            os.remove(work_dir + '\\' + out_dir + '\\' + will_package_dir2_zip_name)
        except:
            pass
        shutil.rmtree(work_dir + '\\' + will_package_dir2, True)
        try:
            os.remove(work_dir + '\\' + will_package_dir1_zip_name)
        except:
            pass
        # 新建目录
        try:
            os.mkdir(work_dir + '\\' + out_dir)
        except:
            pass
        os.mkdir(work_dir + '\\' + will_package_dir2)
        # ===============================================================================================================
        show_and_exe_cmd_line_and_get_ret('del ' + work_dir + '\\' + will_package_dir2 + '\*.* / q / f / s')
        show_and_exe_cmd_line_and_get_ret('rmdir ' + work_dir + '\\' + will_package_dir2 + ' / q / s')
        show_and_exe_cmd_line_and_get_ret('mkdir ' + work_dir + '\\' + will_package_dir2)
        # 开始压缩文件
        show_and_exe_cmd_line_and_get_ret(rar_path + ' a -afzip -ep1 '
                                          + work_dir + '\\' + will_package_dir2 + '\\' + will_package_dir1_zip_name
                                          + ' ' + work_dir + '\\' + will_package_dir1)
        # ===============================================================================================================
        update_class = CUpdate()
        # ===============================================================================================================
        update_class.WriteSigToFile(work_dir + '\\' + 'update_pri.pem',
                                    work_dir + '\\' + will_package_dir2 + '\\' + will_package_dir1_zip_name,
                                    work_dir + '\\' + will_package_dir2 + '\\' + 'sig.ini',
                                    1)
        # ===============================================================================================================
        # 开始最后压缩
        show_and_exe_cmd_line_and_get_ret(rar_path + ' a -p{89B87785-0C5F-47eb-B7DE-73DD962B0FAE} -afzip -ep1 '
                                          + work_dir + '\\' + out_dir + '\\' + will_package_dir2_zip_name
                                          + ' ' + work_dir + '\\' + will_package_dir2)
        # ===============================================================================================================
        update_class.AddOneUpdateToList(will_package_dir2_zip_name, "一体机更新", "log", 123, 1,
                                        time.strftime('%Y-%m-%d %X', time.localtime(time.time())), os.path.getsize(
                work_dir + '\\' + out_dir + '\\' + will_package_dir2_zip_name), '1.0', "")
        # update_class.AddOneUpdateToList("2016_08_10-17_59_56.zip", "服务器驱动更新", "log", 123, 2, '2016/8/10 10:28', 102400,
        #                                 '1.0', "")
        # update_class.AddOneUpdateToList("2016_08_10-17_59_56.zip", "启动介质数据源更新", "log", 123, 3, '2016/8/10 10:28',
        #                                 102400, '1.0', "")py
        # update_class.AddOneUpdateToList("2016_08_10-17_59_56.zip", "去重数据库更新", "log", 123, 4, '2016/8/10 10:28', 102400,
        #                                 '1.0', "")
        update_class.GenAllUpdateJsonFileAndCleanList(work_dir + '\\' + out_dir + r'\jsonp.txt')
    except:
        _logger.error(traceback.format_exc())


def GenType2SigFile():
    try:
        rar_path = r'"C:\Program Files\WinRAR\WinRAR.exe"'
        work_dir = r'D:\work\code\aio\update_make'
        will_package_dir1 = 'drv_database'
        will_package_dir1_zip_name = will_package_dir1 + '.zip'
        out_dir = 'out'
        # ===============================================================================================================
        # 删除原来的生成文件
        try:
            os.remove(work_dir + '\\' + out_dir + '\\' + will_package_dir1_zip_name)
        except:
            pass
        # 新建目录
        try:
            os.mkdir(work_dir + '\\' + out_dir)
        except:
            pass
        # ===============================================================================================================
        update_class = CUpdate()
        # ===============================================================================================================
        update_class.WriteSigToFile(work_dir + '\\' + 'update_pri.pem', None,
                                    work_dir + '\\' + will_package_dir1 + '\\' + 'sig.ini', 2)
        # ===============================================================================================================
        # 开始压缩文件
        show_and_exe_cmd_line_and_get_ret(rar_path + ' a -p{89B87785-0C5F-47eb-B7DE-73DD962B0FAE} -afzip -ep1 '
                                          + work_dir + '\\' + out_dir + '\\' + will_package_dir1_zip_name
                                          + ' ' + work_dir + '\\' + will_package_dir1)
    except:
        _logger.error(traceback.format_exc())


if __name__ == "__main__":
    xlogging.basicConfig(stream=sys.stdout, level=xlogging.DEBUG)
    cur_file_dir_str = cur_file_dir()
    os.chdir(cur_file_dir_str)
    if len(sys.argv) > 1:
        GenType1SigFile()
        GenType2SigFile()
        sys.exit(0)

    update_class = CUpdate()
    # ===============================================================================================================
    _logger.debug(update_class.Update(r'/root/aio.zip'))
    # ===============================================================================================================
    _logger.debug(update_class.Update(r'/root/drv_database.zip'))
