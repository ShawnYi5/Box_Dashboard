import json
import sys


def sample():
    # r:0 成功，其他值失败
    # msg:返回的字符串
    # debug:调试信息
    try:
        result = {'r': 0, 'msg': '数据库连接成功', 'debug': '调试信息'}
    except Exception as e:
        result['r'] = -1
        result['msg'] = '{}'.format(e)
        result['debug'] = '{}'.format(sys.exc_info())
    return result


if __name__ == "__main__":
    jsonobj = sample()
    print(json.dumps(jsonobj, ensure_ascii=False))
