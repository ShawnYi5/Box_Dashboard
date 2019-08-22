import base64
import hashlib
import json
import time

import requests
from django.http import HttpResponse
from django.http import HttpResponseRedirect

from box_dashboard import xlogging
from xdashboard.handle.user import login as user_login

_logger = xlogging.getLogger(__name__)

CACHE_LIST = dict()


def _MD5(src):
    m2 = hashlib.md5()
    m2.update(src.encode('utf-8'))
    return m2.hexdigest()


def token_for_zy_oauth(request):
    """
    得到用户的对应的 token
    :param request:
    :return:
    """
    _logger.info('token_for_zy_oauth-request.get:{}'.format(request.GET))
    code = request.GET.get('code', [])
    url = 'http://auth.mcc-cloud.com/idp/oauth2/getToken'
    grant_type = 'authorization_code'
    client_id = 'testapp'
    client_secret = 'e8d41a1c462f44a6826bb1abf3338c37'

    param = {'client_id': client_id, 'client_secret': client_secret, 'grant_type': grant_type, 'code': code}
    response_token = requests.post(url, params=param)
    try:
        rep_zy = response_token.json()
        _logger.info('token_for_zy_oauth--get:{},type:{}'.format(rep_zy, type(rep_zy)))
        uid = rep_zy['uid']
        refresh_token = rep_zy['refresh_token']
        access_token = rep_zy['access_token']
        get_zy_user_info(access_token)
        user_login_from_oauth(request)
        return HttpResponseRedirect('/xdashboard/home/')
    except Exception as e:
        _logger.error('token_for_zy_oauth error :{}'.format(e), exc_info=True)


def get_zy_user_info(token):
    url = 'http://auth.mcc-cloud.com/idp/oauth2/getUserInfo'
    access_token = token
    client_id = 'testapp'
    param = {'access_token': access_token, 'client_id': client_id}
    response_user_info = requests.get(url, params=param)
    _logger.info('get_zy_user_info:{}'.format(response_user_info.json()))
    # return HttpResponse(response_user_info.json())


def user_login_from_oauth(request):
    global CACHE_LIST
    _logger.info('user_login_from_oauth-request:{}'.format(request.POST))
    try:
        if not request.POST.get('usertoken', None) and not request.GET.get('usertoken', None):
            user = request.POST['username']
            pwd = request.POST['userpwd']
            operator = request.POST['operator_name']
            token = _MD5(str(time.time()))
            redirect_url = 'http://' + request.POST.get('aio_ip', '') + '/aio/login/?usertoken={}'.format(token)
            result = {'r': 0, 'redirect_url': redirect_url}
            CACHE_LIST[user] = [token, pwd, operator]
            return HttpResponse(json.dumps(result, ensure_ascii=False))
        user = request.GET['username']
        token = request.GET['usertoken']
        if token != CACHE_LIST[user][0]:  # token
            return HttpResponse(json.dumps({'r': 1, 'e': 'invalid token'}, ensure_ascii=False))

        pwd = CACHE_LIST[user][1]  # pwd
        request.POST._mutable = True
        request.POST['u'] = user
        request.POST['p'] = base64.b64encode(bytes(pwd, 'utf-8'))
        request.POST['a'] = 'login'
        request.POST['savecookie'] = '1'
        rep_login = user_login(request, dict)
        rep = json.loads(rep_login.content.decode('utf-8'))
        result = {'r': 0, 'e': '成功'}
        if int(rep['r']):
            result['r'] = 1
            result['e'] = rep['e']
            raise Exception('登录失败')

        cache_item = CACHE_LIST.pop(user)
        if user == 'admin':
            r = HttpResponseRedirect('/xdashboard/admin/')
            r.set_cookie('clw_operator', cache_item[2])  # operator
            return r
        else:
            r = HttpResponseRedirect('/xdashboard/home/')
            r.set_cookie('clw_operator', cache_item[2])  # operator
            return r
    except Exception as e:
        _logger.info('user_login_from_oauth error:{}'.format(e), exc_info=True)


def token_zy_oauth_of_framework():
    """
    得到'管理员'对应的 token ---用于查看组织结构
    :return:
    """
    loginId = 'bbc'
    secretKey = 'password'
    force = False  # 非必须
    clientId = str  # 非必须
    param = {'loginId': loginId, 'secretKey': secretKey}
    rul_framework_token = 'http://auth.mcc-cloud.com:8081/bim-server/api/rest/management/ExtApiMgmtAuthService/login'
    req = requests.post(rul_framework_token, params=param).json()
    token = req['data']
    # TODO token 进行存储


def get_framework_zy():
    """
    得到机构结构数据
    :return:
    """
    token = ''  # TODO 在存储中取得token
    _search = {'name_like': '*'}
    _page = {'size': 100, 'number': 1}
    _sort = {"name": "DESC", "createAt": "ASC"}
    param = {'token': token}
    # 用于查询用户相关结构数据
    # rul_framework = 'http://auth.mcc-cloud.com:8081/bim-server/api/rest/management/ExtApiMgmtUserService/findBy'
    # 用于查询组织结构相关数据
    rul_framework = 'http://auth.mcc-cloud.com:8081/bim-server/api/rest/management/ExtApiMgmtOrganizationService/findBy'
    req = requests.post(rul_framework, params=param).json()
    if req['errorCode']:
        if req['errorName'] == 'EXT_API_INVALID_TOKEN':
            # token 无效
            token_zy_oauth_of_framework()
            get_framework_zy()
        else:
            _logger.error('get_framework_zy error:{}'.format(req))

    # todo 对得到的数据进行处理存储
