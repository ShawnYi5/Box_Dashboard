import json
import requests
import threading

from apiv1.models import Host, RemoteBackupSchedule
from box_dashboard import xlogging
from rest_framework import status

_logger = xlogging.getLogger(__name__)


class NetWorkUnReachable(Exception):
    """
    网络不通
    """
    pass


class InvalidUser(Exception):
    """
    连接参数错误 用户名和密码
    """
    pass


def get_root_url(aio_ip, schedule):
    schedule = RemoteBackupSchedule.objects.get(id=schedule.id)
    sche_ext = json.loads(schedule.ext_config)
    enable_https = sche_ext.get('enable_https', {'value': False})
    if enable_https['value']:
        result = r'https://{}:{}/'.format(aio_ip, sche_ext.get('web_port', '443'))
    else:
        result = r'http://{}:{}/'.format(aio_ip, sche_ext.get('web_port', '8000'))
    return result


class WebApi(object):
    def __init__(self, host, username, password):
        self._username = username
        self._password = password
        self._url = host
        self._access_url = '{}{}'.format(self._url, r'apiv1/remote_backup/')
        self._cookies = None

        self._refresh_lock = threading.RLock()
        self._in_refresh = False
        self._done_refresh = threading.Event()

        self.refresh_cookies()
        _logger.info('<WebApi {}|{}> init successful'.format(self._url, self._username))

    def refresh_cookies(self):
        """
        多线程公用一个实例，刷新cookie时候只需要一个人刷新
        """
        need_waite = False
        with self._refresh_lock:
            if self._in_refresh:
                need_waite = True
            else:
                self._in_refresh = True
                self._done_refresh.clear()

        if need_waite:  # 有其它线程在刷新 只需要等待刷新完毕
            self._done_refresh.wait()
            return
        else:
            try:
                self._refresh_cookies_worker()
            finally:
                with self._refresh_lock:
                    self._in_refresh = False
                    self._done_refresh.set()

    def _refresh_cookies_worker(self, self_call=False):
        _logger.info(r'will refresh cookies from aio {}'.format(self._url))
        url_login = self._url + r'api-auth/login/'
        web_login_get = requests.get(url_login, verify=False, timeout=120)
        if web_login_get.status_code == status.HTTP_200_OK:
            csrftoken = web_login_get.cookies['csrftoken']
            login_data = {'username': self._username, 'password': self._password, 'csrfmiddlewaretoken': csrftoken}
            login_cookies = {'csrftoken': csrftoken}
            web_login_post = requests.post(url_login, allow_redirects=False, data=login_data, cookies=login_cookies,
                                           headers={'Referer': url_login}, verify=False, timeout=120)
            if web_login_post.status_code == status.HTTP_302_FOUND:
                self._cookies = web_login_post.cookies
                _logger.info(r'refresh cookies ok')
            elif web_login_post.status_code == status.HTTP_200_OK and not self_call:
                _logger.error(r'refresh cookies failed. super user Not exist!, will try once')
                self._refresh_cookies_worker(self_call=True)
            else:
                raise InvalidUser('invalid user {}|{}'.format(self._access_url, self._username))
        else:
            raise NetWorkUnReachable()

    def http_post_to_url(self, payload, self_call=False):
        post_header = {'x-csrftoken': self._cookies['csrftoken'], 'Referer': self._access_url}
        _logger.info('WebApi post data:{}'.format(payload))
        rev = requests.post(self._access_url, data=payload, cookies=self._cookies, headers=post_header, verify=False,
                            timeout=120)  # 请求可能会卡住，需要加入超时参数
        if rev.status_code == status.HTTP_403_FORBIDDEN and not self_call:  # 当前session不可以用（过期或者用户名密码不对）
            self.refresh_cookies()
            return self.http_post_to_url(payload, True)
        else:
            return rev

    def http_query_new_host_backup(self, host_ident, last_host_snapshot_id):
        payload = {'type': 'query_new_host_backup', 'host_ident': host_ident,
                   'last_host_snapshot_id': last_host_snapshot_id}
        res = self.http_post_to_url(payload)
        if status.is_success(res.status_code):
            return json.loads(res.content.decode('utf-8'))

        xlogging.raise_and_logging_error('http_query_new_host_backup error', res.reason)

    def http_query_latest_host_backup(self, host_ident, last_host_snapshot_id, last_datetime):
        payload = {'type': 'query_latest_host_backup', 'host_ident': host_ident,
                   'last_host_snapshot_id': last_host_snapshot_id, 'last_datetime': last_datetime}
        res = self.http_post_to_url(payload)
        if status.is_success(res.status_code):
            return json.loads(res.content.decode('utf-8'))

        xlogging.raise_and_logging_error('http_query_latest_host_backup error', res.reason)

    def http_query_new_disk_backup(self, host_snapshot_id, last_disk_snapshot_ident):
        payload = {'type': 'query_new_disk_backup', 'host_snapshot_id': host_snapshot_id,
                   'last_disk_snapshot_ident': last_disk_snapshot_ident}
        res = self.http_post_to_url(payload)
        if status.is_success(res.status_code):
            return json.loads(res.content.decode('utf-8'))

        xlogging.raise_and_logging_error('http_query_new_disk_backup error', res.reason)

    def http_query_latest_disk_backup(self, host_snapshot_id, last_disk_snapshot_ident, last_timestamp):
        payload = {'type': 'query_latest_disk_backup', 'host_snapshot_id': host_snapshot_id,
                   'last_disk_snapshot_ident': last_disk_snapshot_ident,
                   'last_timestamp': last_timestamp}
        res = self.http_post_to_url(payload)
        if status.is_success(res.status_code):
            return json.loads(res.content.decode('utf-8'))

        xlogging.raise_and_logging_error('http_query_latest_disk_backup error', res.reason)

    def http_kill_remote_backup_logic(self, task_uuid, disk_token):
        payload = {'type': 'kill_remote_backup_logic', 'task_uuid': task_uuid, 'disk_token': disk_token}
        res = self.http_post_to_url(payload)
        if status.is_success(res.status_code):
            return json.loads(res.content.decode('utf-8'))

        xlogging.raise_and_logging_error('http_kill_remote_backup_logic', res.reason)

    def http_start_remote_backup_logic(self, task_uuid, disk_token, disk_snapshot_ident, disk_snapshot_list,
                                       start_time):
        payload = {'type': 'start_remote_backup_logic', 'task_uuid': task_uuid, 'disk_token': disk_token,
                   'disk_snapshot_ident': disk_snapshot_ident, 'disk_snapshot_list': disk_snapshot_list,
                   'start_time': start_time}
        res = self.http_post_to_url(payload)
        if status.is_success(res.status_code):
            return json.loads(res.content.decode('utf-8'))

        xlogging.raise_and_logging_error('http_start_remote_backup_logic', res.reason)

    def http_query_remote_backup_status(self, task_uuid, disk_token):
        payload = {'type': 'query_remote_backup_status', 'task_uuid': task_uuid, 'disk_token': disk_token}
        res = self.http_post_to_url(payload)
        if status.is_success(res.status_code):
            return json.loads(res.content.decode('utf-8'))

        xlogging.raise_and_logging_error('http_query_remote_backup_status', res.reason)

    def http_close_remote_backup_logic(self, task_uuid, disk_token):
        payload = {'type': 'close_remote_backup_logic', 'task_uuid': task_uuid, 'disk_token': disk_token}
        res = self.http_post_to_url(payload)
        if status.is_success(res.status_code):
            return json.loads(res.content.decode('utf-8'))

        xlogging.raise_and_logging_error('http_close_remote_backup_logic', res.reason)

    def http_query_is_host_cdp_back_end(self, host_snapshot_id):
        payload = {'type': 'query_is_host_cdp_back_end', 'host_snapshot_id': host_snapshot_id}
        res = self.http_post_to_url(payload)
        if status.is_success(res.status_code):
            return json.loads(res.content.decode('utf-8'))

        xlogging.raise_and_logging_error('http_query_is_host_cdp_back_end', res.reason)

    def http_check_qcow_file_exists(self, snapshots):
        payload = {'type': 'check_qcow_file_exists', 'snapshots': snapshots}
        res = self.http_post_to_url(payload)
        if status.is_success(res.status_code):
            return json.loads(res.content.decode('utf-8'))

        xlogging.raise_and_logging_error('http_check_qcow_file_exists', res.reason)


_web_apis = dict()
_web_apis_locker = threading.Lock()


def get_web_api_instance(host_ident, schedule):
    host = Host.objects.get(ident=host_ident)
    aio_info = json.loads(host.aio_info)
    host_url = get_root_url(aio_info['ip'], schedule)
    key = '{}|{}|{}'.format(host_url, aio_info['username'], aio_info['password'])
    with _web_apis_locker:
        api = _web_apis.get(key, None)
        if api:
            return api
        else:
            api = WebApi(host_url, aio_info['username'], aio_info['password'])
            _web_apis[key] = api
            return api
