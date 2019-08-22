# coding:utf-8
import json
import sys
import traceback
from datetime import datetime
from urllib import request, parse, error

from box_dashboard import xlogging

# from web_guard.models import WebGuardStrategy, EmergencyPlan, StrategyGroup, AlarmEventLog
_logger = xlogging.getLogger('NOTIFICATION')

# import logging
# _handler = logging.StreamHandler(sys.stdout)
# _handler.setFormatter(logging.Formatter('%(asctime)s %(name)-8s %(levelname)-6s: %(message)s'))
# _logger = logging.getLogger('NOTIFICATION')
# _logger.setLevel(logging.DEBUG)
# _logger.addHandler(_handler)

_aio_notice_url = 'http://update.clerware.com/sms/index.php/api/aionotice?'


def aio_email_notify(email_list, strategy_name, risk_text, risk_level, risk_time):
    _logger.debug('call email api: {}, {}, {}, {}, {}'.format(
        ','.join(email_list), strategy_name, risk_text, risk_level, risk_time))
    return 0, 'success'


def get_aio_id():
    return 'aio_id:NanJingAnJian01,sn:335727890'


# 保存上一次的时间: 策略id是最小力度, 所有的检测时以策略来做的, dict[strategy_id] = last_time
# 对方是循环, 循环的话就是发现一个调一次. 这边就设计为一个函数就可以了

_last_notify_time = dict()


# 如何解决越累越多的问题, 要删除吗? 不用删除, 总共就这么多策略, 如果不用删除, 就不用管线程安全的问题.
def get_last_notify_time(strategy_id, risk_level, type):
    last_level_time = _last_notify_time.get(strategy_id)
    if last_level_time is not None and last_level_time.get('level') == risk_level:
        return last_level_time.get(type)
    return None


def update_last_notify_time(strategy_id, risk_level, type, notify_time):
    '''
    更新规则: 以扫描策略为主键, 风险等级变化, 上次的时间就不要了.
    :param strategy_id: 主键
    :param risk_level: 风险等级
    :param type: phone, sms, email
    :param notify_time: 要更新的时间
    :return:
    '''
    last_level_time = _last_notify_time.get(strategy_id)
    if last_level_time is None:
        new_level_time = dict()
        new_level_time['level'] = risk_level
        new_level_time[type] = notify_time
        _last_notify_time[strategy_id] = new_level_time
    else:
        if last_level_time.get('level') != risk_level:
            new_level_time = dict()
            new_level_time['level'] = risk_level
            new_level_time[type] = notify_time
            _last_notify_time[strategy_id] = new_level_time
        else:
            last_level_time[type] = notify_time


def yun_notify(notify_dest_list, strategy_name, risk_text, risk_level, risk_time, notify_type):
    if notify_type == 'email':
        return aio_email_notify(notify_dest_list, strategy_name, risk_text, risk_level, risk_time)

    elif notify_type == 'sms':
        type_number = 1
    elif notify_type == 'phone':
        type_number = 2
    else:
        type_number = 0

    risk_level_fmt = risk_level
    if risk_level == 'high':
        risk_level_fmt = '高'
    elif risk_level == 'middle':
        risk_level_fmt = '中'
    elif risk_level == 'low':
        risk_level_fmt = '低'

    parms = {
        'aioid': get_aio_id(),
        'phnum': ','.join(notify_dest_list),
        'fstrat': strategy_name,
        'ftext': risk_text,
        'flevel': risk_level_fmt,
        'ftime': risk_time.strftime('%Y%m%d%H%M%S'),
        'notype': type_number
    }

    try:
        query_string = parse.urlencode(parms, encoding='utf-8')
        with request.urlopen(_aio_notice_url + query_string) as response:
            resp_string = response.read().decode("utf-8")

        resp_json = json.loads(resp_string)
        _logger.debug('call php/api/aionotice response={}'.format(resp_json))
        return resp_json['r'], resp_json['e']

    except error.HTTPError as httpe:
        _logger.error('yun_notify HTTPError: {}'.format(traceback.format_exc()))
        return -1, str(httpe)

    except error.URLError as urle:
        _logger.error('yun_notify URLError: {}'.format(traceback.format_exc()))
        return -2, str(urle)

    except Exception as unknowne:
        _logger.error('yun_notify unknown Exception: {}'.format(traceback.format_exc()))
        return -3, str(unknowne)


def aio_notify(strategy_detect_result, risk_text, risk_level, risk_time):
    _logger.debug('aio_notify({}, {}, {}, {}) entered'.format(
        strategy_detect_result.id, risk_text, risk_level, risk_time.strftime('%Y-%m-%d %H:%M:%S')))

    strategy_id = strategy_detect_result.id
    alarm_method = json.loads(strategy_detect_result.user.alarm_method.exc_info)

    notify_result = dict()

    try:
        for key in alarm_method[risk_level].keys():

            frequency = int(alarm_method[risk_level][key]['frequency'])
            is_use = alarm_method[risk_level][key]['is_use']
            item_list = alarm_method[risk_level][key]['item_list']
            last_time = get_last_notify_time(strategy_id, risk_level, key)

            _logger.debug('risk_level={}, key={}, frequency={}, is_use={}, item_list={}, last_time={}'.format(
                risk_level, key, frequency, is_use, item_list, last_time))

            should_notify_update = False
            if is_use is True and len(item_list) > 0:
                if last_time is None:
                    should_notify_update = True
                elif (risk_time - last_time).total_seconds() >= frequency * 60:
                    _logger.debug("({} - {}).total_seconds={} frequency={}".format(
                        risk_time, last_time, (risk_time - last_time).total_seconds(), frequency * 60))
                    should_notify_update = True

            if should_notify_update is False:
                continue

            # item_list: 电话号码, name: 策略名称, risk_text: 风险描述, level: 等级, time: 发生时间, key: 语音/sms
            status, msg = yun_notify(item_list, strategy_detect_result.name, risk_text, risk_level, risk_time, key)
            notify_result[key] = (status, msg)
            if status == 0:
                update_last_notify_time(strategy_id, risk_level, key, risk_time)

    except KeyError as e:
        _logger.error('alarm_method KeyError: {}'.format(traceback.format_exc()))
        notify_result[str(e)] = (-1, repr(e))

    return notify_result


# ---------------------------------------------------------------------------------------------------
# TestStub - emulate WebGuardStrategy

class AlarmMethodStub:
    DEFAULT = {
        'high': {
            'email': {
                'is_use': True,
                'item_list': ['crunch@clerware.com', 'crunchyou@qq.com'],
                'frequency': 1
            },
            'phone': {
                'is_use': True,
                'item_list': ['13512347754', '17092326022'],
                'frequency': 1
            },
            'sms': {
                'is_use': True,
                'item_list': ['13512347754', '17092326022'],
                'frequency': 1
            }
        },
        'middle': {
            'email': {
                'is_use': True,
                'item_list': ['crunch@clerware.com', 'crunchyou@qq.com'],
                'frequency': 2
            },
            'phone': {
                'is_use': False,
                'item_list': ['13512347754', '17092326022'],
                'frequency': 2
            },
            'sms': {
                'is_use': True,
                'item_list': ['13512347754', '17092326022'],
                'frequency': 2
            }
        },
        'low': {
            'email': {
                'is_use': True,
                'item_list': ['crunch@clerware.com', 'crunchyou@qq.com'],
                'frequency': 3
            },
            'phone': {
                'is_use': False,
                'item_list': ['13512347754'],
                'frequency': 3
            },
            'sms': {
                'is_use': False,
                'item_list': ['13512347754'],
                'frequency': 3
            }
        }
    }

    exc_info = json.dumps(DEFAULT)


class UserStub:
    def __init__(self):
        self.alarm_method = AlarmMethodStub()


class WebGuardStrategyStub:
    def __init__(self, new_id, new_name):
        self.id = new_id
        self.name = new_name
        self.user = UserStub()


if __name__ == "__main__":

    risk_strat_list = list()
    risk_strat_list.append(WebGuardStrategyStub(10001, '策略1'))
    risk_strat_list.append(WebGuardStrategyStub(10001, '策略1'))
    risk_strat_list.append(WebGuardStrategyStub(10001, '策略1'))
    risk_strat_list.append(WebGuardStrategyStub(10001, '策略1'))
    risk_strat_list.append(WebGuardStrategyStub(10002, '策略22'))
    risk_strat_list.append(WebGuardStrategyStub(10002, '策略22'))
    risk_strat_list.append(WebGuardStrategyStub(10002, '策略22'))
    risk_strat_list.append(WebGuardStrategyStub(10003, '策略333'))
    risk_strat_list.append(WebGuardStrategyStub(10003, '策略333'))
    risk_strat_list.append(WebGuardStrategyStub(10003, '策略333'))

    risk_text_list = list()
    risk_text_list.append('扫描发现1处页面更改')
    risk_text_list.append('扫描发现1处页面更改')
    risk_text_list.append('扫描发现1处页面更改')
    risk_text_list.append('扫描发现1处页面更改')
    risk_text_list.append('扫描发现2处页面更改')
    risk_text_list.append('扫描发现2处页面更改')
    risk_text_list.append('扫描发现2处页面更改')
    risk_text_list.append('扫描发现3处页面更改')
    risk_text_list.append('扫描发现3处页面更改')
    risk_text_list.append('扫描发现3处页面更改')

    risk_level_list = list()
    risk_level_list.append('low')
    risk_level_list.append('low')
    risk_level_list.append('low')
    risk_level_list.append('low')
    risk_level_list.append('middle')
    risk_level_list.append('middle')
    risk_level_list.append('middle')
    risk_level_list.append('high')
    risk_level_list.append('high')
    risk_level_list.append('high')

    from time import sleep

    for i in range(0, 100):
        index = i % 10
        risk_time = datetime.now()
        notify_result = aio_notify(risk_strat_list[index], risk_text_list[index], risk_level_list[index], risk_time)
        print('result_string: {}'.format(repr(notify_result)))
        sleep(60)

    sys.exit(0)
