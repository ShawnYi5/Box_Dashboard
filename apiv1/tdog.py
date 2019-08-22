import threading
import time
from box_dashboard import xlogging
from xdashboard.handle.authorize.authorize_init import AIO_PRIKEY_PATH, AIO_SN_PATH
import ctypes, os
from Crypto.PublicKey import RSA

_logger = xlogging.getLogger(__name__)


def getstring():
    try:
        # 在/usr/lib64放入libtdog.so.0和test_v2.so
        pdll = ctypes.CDLL('test_v2.so')
    except Exception as e:
        return (-1, str(e))

    class chars(ctypes.Structure):
        _fields_ = [('szKey', ctypes.c_char * 4096), ('iLen', ctypes.c_int), ]

    pdll.getstring.argtypes = [ctypes.POINTER(chars)]

    o = chars()
    # 读取长度
    o.iLen = 819

    ret = pdll.getstring(ctypes.byref(o))
    return (ret, o.szKey.decode())


def gen_privatekey(privatekey):
    private_key_str = '-----BEGIN RSA PRIVATE KEY-----\n{}\n-----END RSA PRIVATE KEY-----'.format(privatekey)
    pkey = RSA.importKey(private_key_str)
    return pkey.exportKey().decode()


def save_lincense_file(tfile, privatekey):
    try:
        file_object = open(tfile, 'w')
        file_object.writelines(gen_privatekey(privatekey))
        file_object.close()
    except Exception as e:
        _logger.debug('save_lincense_file Exception e={}'.format(str(e)))


def _is_same_sn(tfile, sn):
    with open(tfile, 'r') as fout:
        sn_in_file = fout.read()
        sn_in_file = sn_in_file.strip()
        if sn_in_file == sn:
            return True
    return False


def is_key_same(keyfilepath, privatekey):
    try:
        private_key_str = open(keyfilepath, 'rb').read()
        pkey = RSA.importKey(private_key_str)
        key1 = pkey.exportKey().decode()
        key2 = gen_privatekey(privatekey)
        if key1 == key2:
            return True
        else:
            _logger.debug('is_key_same key1={}'.format(key1))
            _logger.debug('is_key_same key2={}'.format(key2))
    except Exception as e:
        _logger.error('is_key_same Exception e={}'.format(e), exc_info=True)
    return False


def license():
    code, szkey = getstring()
    if code == -1:
        # 无test_v2模块
        return 'no_test_v2'
    elif code == 0:
        keyvec = szkey.split('|', 1)
        if len(keyvec) != 2:
            # 读取数据格式不对
            _logger.debug('license dog data error szkey={}'.format(szkey))
            if os.path.isfile(AIO_PRIKEY_PATH):
                os.remove(AIO_PRIKEY_PATH)
            return 'no_sn'
        sn = keyvec[0]
        privatekey = keyvec[1]

        if not _is_same_sn(AIO_SN_PATH, sn):
            if os.path.isfile(AIO_PRIKEY_PATH):
                os.remove(AIO_PRIKEY_PATH)
            return 'no_sn'

        if not os.path.isfile(AIO_PRIKEY_PATH):
            # 无私钥文件，直接写入
            _logger.debug('license save_lincense_file')
            save_lincense_file(AIO_PRIKEY_PATH, privatekey)
        elif not is_key_same(AIO_PRIKEY_PATH, privatekey):
            # 私钥文件和uk中的不一至，直接写入
            _logger.debug('license not is_key_same')
            os.remove(AIO_PRIKEY_PATH)
            save_lincense_file(AIO_PRIKEY_PATH, privatekey)

        if os.path.isfile(AIO_PRIKEY_PATH):
            return 'ok'
        else:
            return 'error'

    else:
        # 无dog或dog硬件错误
        _logger.debug('license code={},szkey={}'.format(code, szkey))
        if os.path.isfile(AIO_PRIKEY_PATH):
            os.remove(AIO_PRIKEY_PATH)
        return 'dog_error'


class tdogThread(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        self.run_status = True

    def run(self):
        while self.run_status:
            ret = license()
            _logger.debug('tdogThread license ret ={}'.format(ret))
            if ret == 'no_test_v2':
                break
            elif ret == 'ok':
                time.sleep(604800)
            elif ret in ['no_sn', 'dog_error']:
                time.sleep(10)
            else:
                _logger.debug('tdogThread run Failed.')
                time.sleep(60)

        _logger.debug('tdogThread exit')

    def stop(self):
        _logger.debug('tdogThread stopped')
        self.run_status = False
