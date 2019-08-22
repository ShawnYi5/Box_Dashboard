import json
import os
import requests
from xdashboard.models import OperationLog

# token = '4KFOkJ9qHHK4cgy6PKof6LOIYQ6SrmZyoFYp1PDE6r3JW5L6_810qN77HP95bo_hF0_bZl47EYtngrgAufrkVCE0rGT3JUxlPvl33_2pIy3C34hBvVpj31xds7fgGEWqMn7k0IPT8EPdvVJWnU5LfxpL2zzb8uHxUZiBPsuLl6ULUvcX-wZN2sbDJ-mBfAr1x-uU15kGU_tGc8AmQPfS3g'


# token_rr = requests.get(
#     'https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={}&corpsecret={}'.format('wwce5b2e872d2d6669',
#                                                                                   'ouHRmQA0MslDpgfC-pu6sBgqYgo9FzC01NrNzT3oLhk'))
# token_rr_json = token_rr.json()
# token = token_rr_json['access_token']

token_dir = '/var/'
path = os.path.join(token_dir, 'enterprisetokenweixin.txt')
urlType = ['/cgi-bin/message/send?access_token=ACCESS_TOKEN', 'POST']
shortUrl = urlType[0]
method = urlType[1]
response = {}


def __makeUrl(shortUrl):
    base = "https://qyapi.weixin.qq.com"
    if shortUrl[0] == '/':
        return base + shortUrl
    else:
        return base + '/' + shortUrl


def __appendToken(url):
    token = _get_token_local()
    if not token:
        corpid, corpsecret, _ = _get_secret_and_id()
        token = get_token_from_wei_xin(corpid, corpsecret)
        if isinstance(token, dict):
            raise Exception(token['errmsg'])
    return url.replace('ACCESS_TOKEN',
                       token
                       )


def __httpPost(url, args):
    realUrl = __appendToken(url)
    rr = requests.post(realUrl, data=json.dumps(args).encode("utf-8"))
    return rr.json()


def _get_token_local():
    """得到本地token"""
    result = ''
    try:
        with open(path, mode='r', encoding='utf-8') as r:
            result = r.read()
    except Exception as e:
        _set_token_local()
    return result


def get_token_from_wei_xin(corpid, corpsecret):
    token_rr = requests.get(
        'https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={}&corpsecret={}'.format(corpid, corpsecret))

    token_rr_json = token_rr.json()
    if token_rr_json['errcode']:
        return token_rr_json
    _set_token_local(token=token_rr_json['access_token'])
    return token_rr_json['access_token']


def _set_token_local(token=''):
    """将token写入到本地"""
    with open(path, mode='w') as w:
        w.write(token)


def _get_secret_and_id():
    """数据库中查找"""
    # corpid = 'wwce5b2e872d2d6669'
    # corpsecret = 'ouHRmQA0MslDpgfC-pu6sBgqYgo9FzC01NrNzT3oLhk'
    # agentid = 1000003
    try:
        corpid, corpsecret, agent_id = None, None, None
        wx_obj = OperationLog.objects.filter(event=OperationLog.WEIXIN).first()
        if wx_obj:
            desc = json.loads(wx_obj.desc)
            corpid, corpsecret, agent_id = desc['corp_id'], desc['corp_secret'], desc['agent_id']
        return corpid, corpsecret, agent_id
    except Exception as e:
        raise Exception(e)


def send_to_wei_xin(to_user, content=""):
    """
    :param to_user: str 'wx1|wx2'
    :param content: str
    :return:
    """
    # 数据模型
    args = {
        "touser": to_user,  # "YaoYi|ddf" ,"@all"
        "toparty": "PartyID1|PartyID2",
        "totag": "TagID1 | TagID2",
        "msgtype": "text",
        "agentid": '',
        "text": {
            "content": content
        },
        "safe": 0
    }
    corpid, corpsecret, agent_id = _get_secret_and_id()
    args['agentid'] = int(agent_id)
    res = __httpPost(__makeUrl(shortUrl), args)
    if res['errcode'] and res['errcode'] in [40014, 42001]:
        result = get_token_from_wei_xin(corpid, corpsecret)
        if isinstance(result, dict):
            raise Exception(result['errmsg'])
        res = __httpPost(__makeUrl(shortUrl), args)
    return res

if '__main__' == __name__:
    content = '第二次发送1111111'
    agentid = 1000003
    send_to_wei_xin('YaoYi', content)

# 42001	access_token已过期	access_token有时效性，需要重新获取一次
# 40014 invalid access_token
# 41001 缺少access_token参数
