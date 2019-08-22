import requests

try:
    from box_dashboard import xlogging
    from xdashboard.handle.authorize import http_status
except Exception as e:
    import logging as xlogging
    from authorize import http_status

_logger = xlogging.getLogger(__name__)


class AuthCookies(object):
    def __init__(self, url, username, password, timeout=None):
        self._url = url
        self._username = username
        self._password = password
        self._cookies = None
        self._csrftoken = None
        self._timeout = timeout

    def get(self, url, force_refresh=False):
        if self._cookies is None or force_refresh:
            if not self.refresh():
                raise Exception('connection fail!')
        return self._cookies, self._cookies['csrftoken'], self._url + url

    def refresh(self, self_call=False):
        _logger.info(r'refresh cookies ...')
        url_login = self._url + r'api-auth/login/'
        web_login_get = requests.get(url_login, verify=False, timeout=self._timeout)
        if web_login_get.status_code == http_status.HTTP_200_OK:
            csrftoken = web_login_get.cookies['csrftoken']
            login_data = {'username': self._username, 'password': self._password, 'csrfmiddlewaretoken': csrftoken}
            login_cookies = dict(csrftoken=web_login_get.cookies['csrftoken'])
            headers = dict(Referer=url_login)
            web_login_post = requests.post(url_login, allow_redirects=False, data=login_data, cookies=login_cookies,
                                           headers=headers, verify=False, timeout=self._timeout)
            if web_login_post.status_code == http_status.HTTP_302_FOUND:
                self._cookies = web_login_post.cookies
                _logger.info('refresh cookies ok')
                return True
            elif web_login_post.status_code == http_status.HTTP_200_OK and not self_call:
                _logger.error('refresh cookies failed. super user Not exist!')
                self.refresh(True)
            else:
                _logger.error('无法通过Web组件验证')
                return False
        else:
            _logger.error('无法连接到Web组件')
            return False
