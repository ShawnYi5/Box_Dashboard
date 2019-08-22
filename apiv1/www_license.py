import threading, requests, base64, random
import time
import os
from Crypto.PublicKey import RSA
from Crypto.Cipher import AES
from Crypto.Cipher import PKCS1_v1_5 as Cipher_pkcs1_v1_5

try:
    from box_dashboard import xlogging
    from xdashboard.handle.authorize.authorize_init import AIO_PRIKEY_PATH, AIO_SN_PATH, AIO_AUTHORIZE_FILE_PATH, \
        AIO_NET_ERROR_FILE_PATH, AIO_NET_LIC_FLAG_FILE

    URL = r'https://license.clerware.com:8443'
    _logger = xlogging.getLogger(__name__)
except Exception as e:
    import logging as _logger

    AIO_NET_LIC_FLAG_FILE = r'C:\Windows\notepad.exe'
    URL = r'http://127.0.0.1:25672'
    AIO_SN_PATH = r'D:\sn'
    AIO_PRIKEY_PATH = r'D:\priKey'
    AIO_AUTHORIZE_FILE_PATH = r'D:\authorize'
    AIO_NET_ERROR_FILE_PATH = r'D:\neterrortime'
    _logger.basicConfig(level=_logger.DEBUG)


def my_http_get(url, params):
    f_url = '{}{}'.format(URL, url)
    sesstion = requests.Session()
    try:
        rsp1 = sesstion.get(
            f_url,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            params=params,
            verify=False
        )
        if requests.codes.ok != rsp1.status_code:
            e = 'my_http_get Failed. f_url={},status_code={},text={}'.format(f_url, rsp1.status_code, rsp1.text)
            _logger.error(e)
            return sesstion, {"r": 1002, "e": e}
    except Exception as e:
        return sesstion, {"r": 1000, "e": e}
    return sesstion, rsp1.json()


def my_http_post(url, params, sesstion):
    f_url = '{}{}'.format(URL, url)
    if sesstion is None:
        sesstion = requests.Session()
    try:
        rsp1 = sesstion.post(
            f_url,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            data=params,
            verify=False
        )
        if requests.codes.ok != rsp1.status_code:
            e = 'my_http_post Failed. f_url={},status_code={},text={}'.format(f_url, rsp1.status_code, rsp1.text)
            _logger.error(e)
            return sesstion, {"r": 1000, "e": e}
    except Exception as e:
        return sesstion, {"r": 1000, "e": e}
    return sesstion, rsp1.json()


def aes_decrypt(data, password):
    iv = password
    try:
        cipher = AES.new(password, AES.MODE_CBC, iv)
        data = cipher.decrypt(data).decode()
    except Exception as e:
        _logger.error(e)
        return None
    return data


def dealNetErrorTime():
    if os.path.isfile(AIO_NET_ERROR_FILE_PATH):
        with open(AIO_NET_ERROR_FILE_PATH, mode='rt', encoding='utf-8') as fin:
            err_time = float(fin.read())
            if time.time() - err_time > 7 * 24 * 60 * 60:
                if os.path.isfile(AIO_AUTHORIZE_FILE_PATH):
                    os.remove(AIO_AUTHORIZE_FILE_PATH)
                    _logger.error('dealNetErrorTime del file={}'.format(AIO_AUTHORIZE_FILE_PATH))
        return

    with open(AIO_NET_ERROR_FILE_PATH, mode='wt') as fout:
        fout.write(str(time.time()))


def wwwlicense(i='-2'):
    wwwlicense_pre(i)
    if not os.path.isfile(AIO_NET_LIC_FLAG_FILE):
        return 'no_www_license', None
    if not os.path.isfile(AIO_SN_PATH):
        return 'no_sn', 10
    if not os.path.isfile(AIO_PRIKEY_PATH):
        return 'no_prikey', 10
    sleeptime = 60
    if os.path.isfile(AIO_AUTHORIZE_FILE_PATH):
        sleeptime = 60 * 60
    with open(AIO_SN_PATH, mode='rt', encoding='utf-8') as fin:
        sn = fin.read()
    sesstion, jsonobj = my_http_get('/index.php/api/license/?a=getsalt', {'sn': sn})
    if jsonobj['r'] != 0:
        _logger.error('wwwlicense getsalt Failed.r={},e={}'.format(jsonobj['r'], jsonobj['e']))
        dealNetErrorTime()
        return 'net_error', sleeptime

    _logger.info('wwwlicense salt={}'.format(jsonobj['salt']))

    with open(AIO_PRIKEY_PATH, mode='rt', encoding='utf-8') as fin:
        prikey = fin.read()
    rsakey = RSA.importKey(prikey)
    cipher = Cipher_pkcs1_v1_5.new(rsakey)
    aes_salt = ''.join(random.sample('0123456789abcdef', 16))
    saltbyte = '{}|{}|{}'.format(jsonobj['salt'], aes_salt, i)
    saltbyte = bytes(saltbyte, encoding='utf-8')
    cipher_text = base64.b64encode(cipher.encrypt(saltbyte))

    sesstion, jsonobj = my_http_post('/index.php/api/license/?a=getlicense', {'sn': sn, 'key': cipher_text}, sesstion)
    if jsonobj['r'] == 1000:
        _logger.error('wwwlicense getlicense Failed.r={},e={}'.format(jsonobj['r'], jsonobj['e']))
        dealNetErrorTime()
        return 'net_error', sleeptime

    if not jsonobj['authorizeFileIsOK']:
        # 服务器上没有授权文件
        if os.path.isfile(AIO_AUTHORIZE_FILE_PATH):
            os.remove(AIO_AUTHORIZE_FILE_PATH)
        return 'license_del', None

    authorize = aes_decrypt(base64.b64decode(jsonobj['authorize']), aes_salt)
    if authorize is None:
        return 'aio_error', jsonobj['nextcall']

    authorize = authorize[:jsonobj['len']]

    authorize_hdd = ''
    if os.path.isfile(AIO_AUTHORIZE_FILE_PATH):
        with open(AIO_AUTHORIZE_FILE_PATH, mode='rt', encoding='utf-8') as fin:
            authorize_hdd = fin.read()
    if authorize_hdd != authorize:
        _logger.info('wwwlicense save authorize')
        with open(AIO_AUTHORIZE_FILE_PATH, mode='wt') as fout:
            fout.write(authorize)

    if os.path.isfile(AIO_NET_ERROR_FILE_PATH):
        os.remove(AIO_NET_ERROR_FILE_PATH)
    return 'ok', jsonobj['nextcall']


def wwwlicense_pre(i='-2'):
    WWW_PUB_KEY_PATH = r'/etc/aio/authorize/www_pub.key'
    if not os.path.isfile(AIO_NET_LIC_FLAG_FILE):
        return
    if os.path.isfile(AIO_PRIKEY_PATH):
        return
    if not os.path.isfile(AIO_SN_PATH):
        return
    if not os.path.isfile(WWW_PUB_KEY_PATH):
        return
    with open(AIO_SN_PATH, mode='rt', encoding='utf-8') as fin:
        sn = fin.read()
    with open(WWW_PUB_KEY_PATH, mode='rt', encoding='utf-8') as fin:
        pubkey = fin.read()
    rsakey = RSA.importKey(pubkey)
    cipher = Cipher_pkcs1_v1_5.new(rsakey)
    aes_salt = ''.join(random.sample('0123456789abcdef', 16))
    saltbyte = '{}|{}'.format(aes_salt, i)
    saltbyte = bytes(saltbyte, encoding='utf-8')
    cipher_text = base64.b64encode(cipher.encrypt(saltbyte))
    sesstion, jsonobj = my_http_post('/index.php/api/license/?a=getpri', {'sn': sn, 'key': cipher_text}, None)
    if jsonobj['r'] != 0:
        _logger.debug('wwwlicense_pre e={}'.format(jsonobj['e']))
        return
    pri = aes_decrypt(base64.b64decode(jsonobj['pri']), aes_salt)
    pri = pri[:jsonobj['len']]
    with open(AIO_PRIKEY_PATH, mode='wt') as fout:
        fout.write(pri)


class wwwLicenseThread(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        self.run_status = True

    def run(self):
        i = 0
        while self.run_status:
            try:
                wwwlicense_pre(i)
                ret, sleeptime = wwwlicense(i)
            except Exception as e:
                ret = 'excption'
            i += 1
            _logger.debug('www license ret ={},sleeptime={}'.format(ret, sleeptime))
            if ret in ('no_www_license', 'license_del',):
                break
            elif ret in ('ok',):
                time.sleep(sleeptime)
            elif ret in ('no_sn', 'no_prikey',):
                time.sleep(sleeptime)
            elif ret in ('net_error', 'aio_error',):
                time.sleep(sleeptime)
            else:
                _logger.debug('wwwLicenseThread run Failed.')
                time.sleep(60)

        _logger.debug('wwwLicenseThread exit')

    def stop(self):
        _logger.debug('wwwLicenseThread stopped')
        self.run_status = False


if __name__ == '__main__':
    wwwlicense_pre()
    ret = wwwlicense()
    _logger.debug('www license ret ={}'.format(ret))
