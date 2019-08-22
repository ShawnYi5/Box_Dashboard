import contextlib
import datetime
import html
import json
import os
import re
import shutil
import threading
from collections import Counter

from django.utils import timezone
from json_compare import json_compare
from rest_framework import status as http_status
from taskflow import engines
from taskflow import task
from taskflow.listeners import logging as logging_listener
from taskflow.patterns import linear_flow as lf
from taskflow.persistence import models

from apiv1.models import StorageNode
from box_dashboard import xlogging, xdatetime, xdata, task_backend
from web_guard.models import WebGuardStrategy, AlarmEvent, AlarmEventLog
from web_guard.sitespider import spiderapi, myexception

_logger = xlogging.getLogger(__name__)


# 当策略被 删除，禁用，强制信任，则不再执行 内容检测和敏感词的flow
def check_flow_is_invalid(strategy_id):
    strategy_obj = WebGuardStrategy.objects.get(id=strategy_id)
    is_disable = not strategy_obj.enabled
    is_deleted = strategy_obj.deleted
    is_force_credible = strategy_obj.force_credible
    return is_disable or is_deleted or is_force_credible


class WebSpidersTask(task.Task):
    default_provides = 'task_context'

    def __init__(self, name, config, policy_id, inject=None):
        super(WebSpidersTask, self).__init__(r'WebSpidersTask {}'.format(name), inject=inject)
        self._config = config
        self.strategy_obj = WebGuardStrategy.objects.get(id=policy_id)
        self.task_context = None
        self.isMaintain = False
        self.policy_id = policy_id

    def execute(self, task_context, *args, **kwargs):
        self.task_context = task_context
        if WebAnalyze.has_error(task_context):
            _logger.warning(r'WebSpidersTask {} skip. because error : {}'.format(self.name, task_context['error']))
            return task_context

        try:
            # _logger.info(r'WebSpidersTask {} begin : {} {}'.format(self.name, task_context, args, kwargs))
            spider = spiderapi.CSiteSpiderAPI()
            spider.crawl_site(
                task_context['task_history']['crawl_site_name'],
                task_context['task_history']['path'],
                self._config, 1, self.crawl_callback
            )
        except myexception.OwnWebsiteException as we:
            self.isMaintain = True
            del_path = task_context['task_history']['crawl_site_name']
            spider = spiderapi.CSiteSpiderAPI()
            spider.del_site(del_path)
            _logger.warning(r'catch OwnWebsiteException:{} when WebSpidersTask, del:{}'.format(we, del_path))
        except Exception as e:
            _logger.error(r'WebSpidersTask failed : {}'.format(e), exc_info=True)
            task_context['error'] = (r'检测任务抓取网页内容失败', r'WebSpidersTask failed : {}'.format(e),)
        finally:
            # _logger.info(r'WebSpidersTask {} end .'.format(self.name))
            pass
        if self.isMaintain:
            task_context['task_history']['credible'] = 'maintain'
        return task_context

    def revert(self, **kwargs):
        try:
            _logger.info(r'WebSpidersTask {} revert begin : {}'.format(self.name, kwargs))
            spider = spiderapi.CSiteSpiderAPI()
            spider.del_site(kwargs['result']['task_history']['crawl_site_name'])
        except Exception as e:
            _logger.error(r'WebSpidersTask {} failed : {}'.format(self.name, e))
        finally:
            _logger.info(r'WebSpidersTask {} revert end .'.format(self.name))

    def crawl_callback(self, name, status, last_crawl, ret, err_string):
        _logger.info(r'crawl_callback : {} {} {} {}'.format(name, status, last_crawl, ret, err_string))

        # 放弃本次网页爬取分析
        if self.check_flow_is_invalid(self.policy_id):
            err_msg = r'{} skip.because strategy is invalid'.format(self.name)
            _logger.warning(err_msg)
            self.task_context['error'] = (r'抓取网页内容停止', err_msg,)
            return 1

        # 爬取完成且网站404时候
        if status == 0 and last_crawl['count'] == 0:
            err_msg = r'{} skip.because nothing catch!'.format(self.name)
            _logger.warning(err_msg)
            self.task_context['error'] = (r'未抓取到网页容', err_msg,)
            return 1

        return 0

    # 暂时没有用
    def check_site_404(self):
        if self.strategy_obj.last_404_date:
            # 距离头一次的时间差 大于30分钟,则报警
            if (datetime.datetime.now() - self.strategy_obj.last_404_date).total_seconds() >= 30 * 60:
                self.strategy_obj.last_404_date = None
                _logger.error('WebSpidersTask find 404, 30 minutes from last time')
            # 小于30分钟,不报警
            else:
                self.task_context['error'] = 'site404'
        # 头一次404,不报警，只是记录时间
        else:
            self.strategy_obj.last_404_date = datetime.datetime.now()
            self.task_context['error'] = 'site404'
            _logger.error('WebSpidersTask find first 404 status')
        self.strategy_obj.save(update_fields=['last_404_date'])

        return None

    @staticmethod
    def check_flow_is_invalid(strategy_id):
        strategy_obj = WebGuardStrategy.objects.get(id=strategy_id)
        is_disable = not strategy_obj.enabled
        is_deleted = strategy_obj.deleted
        return is_disable or is_deleted


class WebCompareTask(task.Task):
    default_provides = 'task_context'

    def __init__(self, name, config, policy_id, inject=None):
        super(WebCompareTask, self).__init__(r'WebCompareTask {}'.format(name), inject=inject)
        self._config = config
        self._credible = True
        self.strategy_obj = WebGuardStrategy.objects.get(id=policy_id)
        self.history_events = None
        self.current_uuid = None
        self.policy_id = policy_id
        self.isMaintain = False

    def execute(self, task_context, *args, **kwargs):
        if WebAnalyze.has_error(task_context):
            _logger.warning(r'WebCompareTask {} skip. because error : {}'.format(self.name, task_context['error']))
            return task_context

        self.current_uuid = task_context['task_history']['book_uuid']

        last_credible_task_crawl_site_name = task_context['task_history']['last_credible_task_crawl_site_name']
        if last_credible_task_crawl_site_name is None:
            _logger.warning(r'WebCompareTask {} skip. because last_credible_task_crawl_site_name is None')
            return task_context

        # 是否检测：内容篡改项 （目前：界面必勾选"敏感词"、"内容篡改"之一）
        if 'content-tamper' not in json.loads(self.strategy_obj.ext_info)['inspect_item']:
            pass
        elif 'pictures-tamper' not in json.loads(self.strategy_obj.ext_info)['inspect_item']:
            pass
        elif 'resources-tamper' not in json.loads(self.strategy_obj.ext_info)['inspect_item']:
            pass
        elif 'links-tamper' not in json.loads(self.strategy_obj.ext_info)['inspect_item']:
            pass
        elif 'frameworks-tamper' not in json.loads(self.strategy_obj.ext_info)['inspect_item']:
            _logger.warning(
                r'WebCompareTask {} skip.because not find content-tamper,pictures-tamper,resources-tamper,links-tamper,frameworks-tamper in ext_info'.format(
                    self.name))
            return task_context

        if task_context['task_history']['credible'] == 'maintain':
            _logger.warning(r'WebCompareTask {} skip.because credible==maintain')
            return task_context

        # 开始分析：与基准点比较
        try:
            _logger.info(r'WebCompareTask {} begin : {} {}'.format(self.name, task_context, args, kwargs))
            spider = spiderapi.CSiteSpiderAPI()
            spider.compare_site(self.name, last_credible_task_crawl_site_name,
                                task_context['task_history']['crawl_site_name'],
                                json.loads(self.strategy_obj.ext_info)['inspect_item'],
                                self.crawl_callback)
        except myexception.OwnWebsiteException:
            _logger.warning(r'catch OwnWebsiteException when WebCompareTask')
            self._credible = False
            self.isMaintain = True
        except Exception as e:
            _logger.error(r'WebCompareTask failed : {}'.format(e), exc_info=True)
            task_context['error'] = (r'检测任务比对内容失败', r'WebCompareTask failed : {}'.format(e))
        finally:
            _logger.info(r'WebCompareTask {} end .'.format(self.name))
            pass

        if not self._credible:
            task_context['task_history']['credible'] = 'no'
        if self.isMaintain:
            task_context['task_history']['credible'] = 'maintain'
        return task_context

    def crawl_callback(self, comparename, compare_result, ret, err_string):
        _logger.info('WebCompareTask once callback: comparename={0}, compare_result={1}, ret={2}, err_string={3}'
                     .format(comparename, compare_result, ret, err_string))

        # 放弃本次比较分析
        if check_flow_is_invalid(self.policy_id):
            self._credible = False
            _logger.warning(r'WebCompareTask {} skip.because strategy is invalid')
            return 1

        return self.update_or_create_event_record(compare_result)

    # 获取该策略下，未修复的篡改AE
    def get_all_unsolve_tamper_alarm_events(self, tamper):
        return QueryAlarmEvent.from_strategy_get_events(self.strategy_obj, tamper)

    def convert_tamper(self, tamper):
        if tamper == 'sensitive':
            return 'key-word'
        if tamper == 'content':
            return 'content-tamper'
        if tamper == 'pictures':
            return 'pictures-tamper'
        if tamper == 'resources':
            return 'resources-tamper'
        if tamper == 'links':
            return 'links-tamper'
        if tamper == 'frameworks':
            return 'frameworks-tamper'
        return tamper

    def convert_sub_type(self, sub_type):
        if sub_type == 'key-word':
            return 'sensitive'
        if sub_type == 'content-tamper':
            return 'content'
        if sub_type == 'pictures-tamper':
            return 'pictures'
        if sub_type == 'resources-tamper':
            return 'resources'
        if sub_type == 'links-tamper':
            return 'links'
        if sub_type == 'frameworks-tamper':
            return 'frameworks'
        return sub_type

    # 本次所有资源比较完成：清理掉上次的AE(在本次未出现的)
    def clean_last_tamper_alarm_events(self):
        for tamper in ('content', 'pictures', 'resources', 'links', 'frameworks',):
            unsolve_tamper_alarm_events = self.get_all_unsolve_tamper_alarm_events(tamper)
            sub_type = WebCompareTask.get_sub_type(self.convert_tamper(tamper), self.strategy_obj.check_type)
            last_unsolves = unsolve_tamper_alarm_events.exclude(last_update_uuid__exact=self.current_uuid)
            for alarm_event in last_unsolves:
                alarm_event.last_update_uuid = self.current_uuid
                alarm_event.last_update_time = timezone.now()
                alarm_event.current_status = AlarmEvent.ALARM_EVENT_FIXED
                alarm_event.save(update_fields=['last_update_uuid', 'last_update_time', 'current_status'])

                AlarmEventLog.objects.create(book_uuid=self.current_uuid, strategy=self.strategy_obj,
                                             strategy_sub_type=sub_type,
                                             detail=json.dumps(self.get_log_detail(json.loads(alarm_event.detail))),
                                             log_type=AlarmEventLog.ALARM_EVENT_LOG_TYPE_FIXED)

    @staticmethod
    def _mkdir(path):
        path = path.strip()
        # 判断路径是否存在
        # 存在     True
        # 不存在   False
        isExists = os.path.exists(path)
        # 判断结果
        if not isExists:
            # 如果不存在则创建目录
            # 创建目录操作函数
            os.makedirs(path)
            return True
        else:
            # 如果目录存在则不创建，并提示目录已存在
            return False

    @staticmethod
    def get_ref_path(path_type, path_base, path_current):
        res_base = path_base
        res_current = path_current
        p1 = r"(/home/mnt/nodes/\w{32}/web/)"
        pattern1 = re.compile(p1)
        path_webs = pattern1.findall(path_current)
        path_web = None
        if len(path_webs) == 1:
            path_web = path_webs[0]

        if not path_web:
            path_webs = pattern1.findall(path_base)
            path_web = None
            if len(path_webs) == 1:
                path_web = path_webs[0]

        p1 = r"/home/mnt/nodes/(\w{32})/web/"
        pattern1 = re.compile(p1)
        nodes_names = pattern1.findall(path_current)
        nodes_name = None
        if len(nodes_names) == 1:
            nodes_name = nodes_names[0]

        if not nodes_name:
            nodes_names = pattern1.findall(path_base)
            nodes_name = None
            if len(nodes_names) == 1:
                nodes_name = nodes_names[0]

        if not path_web:
            _logger.warning(r'_get_ref_path get web path Failed.path_base={}'.format(path_base))
            return res_base, res_current

        if not nodes_name:
            _logger.warning(r'_get_ref_path get nodes_name Failed.path_base={}'.format(path_base))
            return res_base, res_current

        static_resource_path = os.path.join('/var/www/static/web_guard', nodes_name)
        WebCompareTask._mkdir(static_resource_path)
        static_resource_path = os.path.join('/var/www/static/web_guard', nodes_name, 'resource')

        nodes_resource_path = os.path.join(path_web, 'resource')
        WebCompareTask._mkdir(nodes_resource_path)

        if not os.path.isdir(static_resource_path):
            os.symlink(nodes_resource_path, static_resource_path)

        filename = datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')
        if path_type == 'url':
            screenshot_path_base = path_base.replace('_.html', '_screenshot.jpg')
            screenshot_path_current = path_current.replace('_.html', '_screenshot.jpg')

        if path_type in ('url',):
            for tmp_path_base in (path_base, screenshot_path_base,):
                shotname, extension = os.path.splitext(tmp_path_base)
                res_nodes_base = os.path.join(nodes_resource_path, '{}_base{}'.format(filename, extension))
                if os.path.isfile(tmp_path_base):
                    shutil.copyfile(tmp_path_base, res_nodes_base)
                    res_base = os.path.join(static_resource_path, '{}_base{}'.format(filename, extension))

            for tmp_path_current in (path_current, screenshot_path_current,):
                shotname, extension = os.path.splitext(tmp_path_current)
                res_nodes_current = os.path.join(nodes_resource_path, '{}_current{}'.format(filename, extension))
                if os.path.isfile(tmp_path_current):
                    shutil.copyfile(tmp_path_current, res_nodes_current)
                    res_current = os.path.join(static_resource_path, '{}_current{}'.format(filename, extension))

        if path_type in ('image', 'css', 'js',):
            shotname, extension = os.path.splitext(path_base)
            res_nodes_base = os.path.join(nodes_resource_path, '{}_base{}'.format(filename, extension))
            if os.path.isfile(path_base):
                shutil.copyfile(path_base, res_nodes_base)
                res_base = os.path.join(static_resource_path, '{}_base{}'.format(filename, extension))

            shotname, extension = os.path.splitext(path_current)
            res_nodes_current = os.path.join(nodes_resource_path, '{}_current{}'.format(filename, extension))
            if os.path.isfile(path_current):
                shutil.copyfile(path_current, res_nodes_current)
                res_current = os.path.join(static_resource_path, '{}_current{}'.format(filename, extension))

        return res_base, res_current

    # 与基准点比较：某个资源的差异
    def get_one_resource_difference(self, compare_result):
        type_num = compare_result['type']

        if type_num in ["content-tamper", "pictures-tamper", "resources-tamper", "links-tamper", "frameworks-tamper", ]:
            self._credible = False

        resource_type = 'none'
        if 'resourceType' in compare_result:
            resource_type = compare_result['resourceType']

        result = {
            'url': None,  # 资源的url(唯一标识)
            'diff_detial': None,  # 资源差异细节
            'diff_type': type_num,  # 资源差异类型
            'resource_type': resource_type,
            'res_base': '',
            'res_current': ''
        }
        if 'path_base' in compare_result:
            result['res_base'] = compare_result['path_base']
        if 'path_current' in compare_result:
            result['res_current'] = compare_result['path_current']

        if type_num in ('content-tamper', 'frameworks-tamper',):  # html、css、js内容修改
            result['url'] = compare_result['url']
            result['diff_detial'] = {}
            result['diff_detial']['change'] = compare_result['change']
        elif type_num in ('pictures-tamper', 'resources-tamper',):  # 非文本文件变化(图片等)
            result['url'] = compare_result['url']
            result['diff_detial'] = {'file': compare_result['url'], 'ref': compare_result['reference'],
                                     'resource_type': compare_result['resourceType']}
        elif type_num == "finish":
            result = None
            self.clean_last_tamper_alarm_events()
        else:
            result = None

        return result

    @staticmethod
    def FmtResourceType(fmt_type):
        if fmt_type == 'url':
            return '网页'
        if fmt_type == 'css':
            return '层叠样式表（css）'
        if fmt_type == 'image':
            return '图片'
        if fmt_type == 'js':
            return 'JScript'
        return fmt_type

    @staticmethod
    def get_log_detail(result=None, saveres=False):
        if result is None:
            return {'description': [r'已修复'], "tamper-type": 'fixed'}

        diff_type, diff_detial, desc = result['diff_type'], result['diff_detial'], []
        res = {}
        desc.append('检测URL[{}]'.format(result['url']))
        if diff_type in ('content-tamper', 'frameworks-tamper',):
            desc.append('共{}处篡改'.format(len(diff_detial['change'])))
        elif diff_type in ('pictures-tamper', 'resources-tamper',):
            desc.append('所在页面:{}'.format(diff_detial['ref']))
            desc.append('类型:{}'.format(WebCompareTask.FmtResourceType(diff_detial['resource_type'])))
        else:
            desc.append('unknown')

        if saveres:
            res['res_base'], res['res_current'] = WebCompareTask.get_ref_path(result['resource_type'],
                                                                              result['res_base'],
                                                                              result['res_current'])

        desc = [html.escape(str_info) for str_info in desc]
        return {'description': desc, "res": res, "tamper-type": result['diff_type']}

    @staticmethod
    def get_sub_type(tamper, check_type):
        if tamper == 'content-tamper':
            return QueryAlarmEvent.get_sub_type(check_type)[0]
        if tamper == 'key-word':
            return QueryAlarmEvent.get_sub_type(check_type)[1]
        if tamper == 'pictures-tamper':
            return QueryAlarmEvent.get_sub_type(check_type)[2]
        if tamper == 'resources-tamper':
            return QueryAlarmEvent.get_sub_type(check_type)[3]
        if tamper == 'links-tamper':
            return QueryAlarmEvent.get_sub_type(check_type)[4]
        if tamper == 'frameworks-tamper':
            return QueryAlarmEvent.get_sub_type(check_type)[5]
        return QueryAlarmEvent.get_sub_type(check_type)[0]

    def update_or_create_event_record(self, compare_result):
        tamper = self.convert_sub_type(compare_result['type'])
        unsolve_tamper_alarm_events = self.get_all_unsolve_tamper_alarm_events(tamper)
        result = self.get_one_resource_difference(compare_result)
        if result is None:
            return 0
        url_event = WebSensitiveWordTask.get_uri_events(unsolve_tamper_alarm_events, result['url'])  # 查询该url对应的AE
        sub_type = WebCompareTask.get_sub_type(self.convert_tamper(tamper), self.strategy_obj.check_type)
        if url_event is None:  # 该url资源没有对应的AE，则创建
            AlarmEvent.objects.create(last_update_time=timezone.now(), last_update_uuid=self.current_uuid,
                                      strategy=self.strategy_obj, strategy_sub_type=sub_type,
                                      detail=json.dumps(result),
                                      current_status=AlarmEvent.ALARM_EVENT_PENDING)
            AlarmEventLog.objects.create(book_uuid=self.current_uuid, strategy=self.strategy_obj,
                                         strategy_sub_type=sub_type,
                                         detail=json.dumps(self.get_log_detail(result, True)),
                                         log_type=AlarmEventLog.ALARM_EVENT_LOG_TYPE_HAPPENED)
        else:  # 修改该url资源的AE事件
            last_result = json.loads(url_event.detail)
            url_event.last_update_uuid = self.current_uuid
            url_event.last_update_time = timezone.now()
            url_event.detail = json.dumps(result)
            url_event.save(update_fields=['last_update_uuid', 'last_update_time', 'detail'])

            last_diff, cur_diff = last_result['diff_detial'], result['diff_detial']
            if not json_compare.are_same(last_diff, cur_diff, ignore_list_order_recursively=True)[0]:
                AlarmEventLog.objects.create(book_uuid=self.current_uuid, strategy=self.strategy_obj,
                                             strategy_sub_type=sub_type,
                                             detail=json.dumps(self.get_log_detail(result, True)),
                                             log_type=AlarmEventLog.ALARM_EVENT_LOG_TYPE_HAPPENED)
        return 0


class WebSensitiveWordTask(task.Task):
    default_provides = 'task_context'

    def __init__(self, name, config, policy_id, inject=None):
        super(WebSensitiveWordTask, self).__init__(r'WebSensitiveWordTask {}'.format(name), inject=inject)
        self._config = config
        self._credible = True
        self.strategy_obj = WebGuardStrategy.objects.get(id=policy_id)
        self.history_events = None
        self.task_context = None
        self.url2words = None
        self.policy_id = policy_id
        self.isMaintain = False

    def execute(self, task_context, *args, **kwargs):
        self.task_context = task_context

        if WebAnalyze.has_error(task_context):
            _logger.warning(r'WebSensitiveWordTask {} skip.because error : {}'.format(self.name, task_context['error']))
            return task_context

        if 'key-word' not in json.loads(self.strategy_obj.ext_info)['inspect_item']:
            _logger.warning(r'WebSensitiveWordTask {} skip.because not find key-word in ext_info'.format(self.name))
            return task_context

        if task_context['task_history']['credible'] == 'maintain':
            _logger.warning(r'WebSensitiveWordTask {} skip.because credible==maintain')
            return task_context

        try:
            # _logger.info(r'WebCompareTask {} begin : {} {}'.format(self.name, task_context, args, kwargs))
            sw = spiderapi.CSensitiveWordsAPI()
            sw.sensitive_word_test(task_context['task_history']['crawl_site_name'], 10, self.crawl_callback)
        except myexception.OwnWebsiteException:
            _logger.warning(r'catch OwnWebsiteException')
            self._credible = False
            self.isMaintain = True
        except Exception as e:
            _logger.error(r'WebSensitiveWordTask failed : {}'.format(e), exc_info=True)
            task_context['error'] = (r'检测关键词失败', r'WebSensitiveWordTask failed : {}'.format(e))
        finally:
            # _logger.info(r'WebCompareTask {} end .'.format(self.name))
            pass

        if not self._credible:
            task_context['task_history']['credible'] = 'no'
        if self.isMaintain:
            task_context['task_history']['credible'] = 'maintain'

        return task_context

    def crawl_callback(self, crawlname, sensitive_word_result, ret, err_string):
        _logger.info(r'crawl_callback : {} {} {} {}'.format(crawlname, sensitive_word_result, ret, err_string))
        if check_flow_is_invalid(self.policy_id):
            self._credible = False
            _logger.warning(r'crawl_callback return 1, because strategy is invalid')
            return 1

        if self.history_events is None:
            self.history_events = QueryAlarmEvent.from_strategy_get_events(self.strategy_obj, 'sensitive')

        self._filter_sensitive_word_result(sensitive_word_result)

        self.update_or_create_event_record(sensitive_word_result)

        if sensitive_word_result['words']:
            self._credible = False
        return 0

    def update_or_create_event_record(self, sensitive_word_result):
        new_url = sensitive_word_result['url']
        words = sensitive_word_result['words']
        url_event = self.get_uri_events(self.history_events, new_url)
        check_type = self.strategy_obj.check_type
        sub_types = QueryAlarmEvent.get_sub_type(check_type)
        log_datail = sensitive_word_result
        log_datail['res'] = {}
        log_datail['tamper-type'] = 'sensitive_words-tamper'
        path_base = ''
        if 'path_base' in sensitive_word_result:
            path_base = sensitive_word_result['path_base']
        path_current = ''
        if 'path_current' in sensitive_word_result:
            path_current = sensitive_word_result['path_current']
        log_datail['res']['res_base'], log_datail['res']['res_current'] = WebCompareTask.get_ref_path(
            sensitive_word_result['resourceType'],
            path_base,
            path_current)
        # 相同的url 找到了警告事件
        if url_event:
            # 有关键词，那么更新这个事件的 uuid,time,detail
            if words:
                history_words = json.loads(url_event.detail)['words']
                # since_uuid = url_event.last_update_uuid
                url_event.last_update_uuid = self.task_context['task_history']['book_uuid']
                url_event.last_update_time = timezone.now()
                url_event.detail = json.dumps(sensitive_word_result)
                url_event.save(update_fields=['last_update_uuid', 'last_update_time', 'detail'])

                # 虽然是相同的URL 但是有变化的敏感词，也要创建日志
                if words != history_words:
                    log_datail['description'] = self._get_event_log_description(sensitive_word_result)
                    log_datail['description'].extend(self._get_new_add(words, history_words))
                    log_datail['description'].extend(self._get_old_remove(words, history_words))
                    AlarmEventLog.objects.create(
                        book_uuid=self.task_context['task_history']['book_uuid'],
                        strategy=self.strategy_obj,
                        strategy_sub_type=sub_types[1],
                        detail=json.dumps(log_datail),
                        log_type=AlarmEventLog.ALARM_EVENT_LOG_TYPE_HAPPENED
                    )

            # 如果关键词为空,那么解除警告！
            else:
                # since_uuid = url_event.last_update_uuid
                url_event.last_update_uuid = self.task_context['task_history']['book_uuid']
                url_event.last_update_time = timezone.now()
                url_event.current_status = AlarmEvent.ALARM_EVENT_FIXED
                url_event.save(update_fields=['last_update_uuid', 'last_update_time', 'current_status'])

                log_datail['description'] = self._get_event_log_description_finish(sensitive_word_result)
                AlarmEventLog.objects.create(
                    book_uuid=self.task_context['task_history']['book_uuid'],
                    strategy=self.strategy_obj,
                    strategy_sub_type=sub_types[1],
                    detail=json.dumps(log_datail),
                    log_type=AlarmEventLog.ALARM_EVENT_LOG_TYPE_FIXED
                )
        # 未找到这个URL 对应的警告事件
        else:
            # 有检测到敏感词，则创建事件
            if words:
                AlarmEvent.objects.create(
                    last_update_time=timezone.now(),
                    last_update_uuid=self.task_context['task_history']['book_uuid'],
                    strategy=self.strategy_obj,
                    strategy_sub_type=sub_types[1],
                    detail=json.dumps(sensitive_word_result),
                    current_status=AlarmEvent.ALARM_EVENT_PENDING
                )
                log_datail['description'] = self._get_event_log_description(sensitive_word_result)
                AlarmEventLog.objects.create(
                    book_uuid=self.task_context['task_history']['book_uuid'],
                    strategy=self.strategy_obj,
                    strategy_sub_type=sub_types[1],
                    detail=json.dumps(log_datail),
                    log_type=AlarmEventLog.ALARM_EVENT_LOG_TYPE_HAPPENED
                )
            else:
                pass

    # 过滤掉 已经添加确认的敏感词
    def _filter_sensitive_word_result(self, sensitive_word_result):
        url = sensitive_word_result['url']
        words = sensitive_word_result['words']
        if self.url2words is None:
            self.url2words = self.get_confirm_url2words_map()

        has_confirm_words = self.url2words.get(url, list())
        rs = list()
        for word in words:
            if word in has_confirm_words:
                _logger.warning('word:"{}" in has_confirm_words, will be filtered'.format(word))
            else:
                rs.append(word)
        sensitive_word_result['words'] = rs

    # 获取确认的敏感词字典{'url':[word1, word2]}
    def get_confirm_url2words_map(self):
        check_type = self.strategy_obj.check_type
        sub_types = QueryAlarmEvent.get_sub_type(check_type)
        all_log = self.strategy_obj.alarm_event_logs.all().filter(strategy_sub_type=sub_types[1],
                                                                  log_type=AlarmEventLog.ALARM_EVENT_LOG_TYPE_CONFIRMED)
        rs = dict()
        for log in all_log:
            detail = json.loads(log.detail)
            if detail['url'] in rs:
                rs[detail['url']].update(detail['words'])
            else:
                rs[detail['url']] = set(detail['words'])
        return rs

    # 根据url, 获取特定的警告事件
    @staticmethod
    def get_uri_events(history_events, new_url):
        rs = None
        for event in history_events:
            detail = json.loads(event.detail)
            if new_url == detail['url']:
                rs = event
                break
        return rs

    @staticmethod
    def _get_event_log_description_finish(sensitive_word_result):
        _p = '检测URL[{}]'.format(sensitive_word_result['url'])
        _P1 = '敏感词风险已移除'
        return [_p, _P1]

    @staticmethod
    def _get_event_log_description(sensitive_word_result):
        c = Counter(sensitive_word_result['words'])
        url = sensitive_word_result['url']
        _p1 = '检测URL[{}]'.format(url)
        _p2 = '敏感词:{}'.format('，'.join(['{}({}次)'.format(i[0], i[1]) for i in c.items()]))
        return [_p1, _p2]

    @staticmethod
    def _get_new_add(current_words, history_words):
        new_word = set(current_words) - set(history_words)
        if new_word:
            return ['新增敏感词:{}'.format(','.join(new_word))]
        return []

    @staticmethod
    def _get_old_remove(current_words, history_words):
        old_remove = set(history_words) - set(current_words)
        if old_remove:
            return ['移除敏感词:{}'.format(','.join(old_remove))]
        return []


class WebAnalyzeCleanExpired(task.Task):
    default_provides = 'task_context'

    def __init__(self, name, policy_id, book_uuid, inject=None):
        super(WebAnalyzeCleanExpired, self).__init__(r'WebAnalyzeCleanExpired {}'.format(name), inject=inject)
        self._policy_id = policy_id
        self._book_uuid = book_uuid

    @xlogging.convert_exception_to_value(None)
    def check_and_delete(self, credible_status, tasks):
        while True:
            condition_tasks = list(filter(lambda x: x['credible'] == credible_status, tasks))
            if len(condition_tasks) < 3:
                break

            condition_task = condition_tasks[0]
            assert condition_task['book_uuid'] != self._book_uuid
            spider = spiderapi.CSiteSpiderAPI()
            spider.del_site(condition_task['crawl_site_name'])
            tasks.remove(condition_task)

    def execute(self, task_context, *args, **kwargs):
        strategy = WebGuardStrategy.objects.get(id=self._policy_id)
        task_histories = json.loads(strategy.task_histories)
        credible_status = ['yes', 'no', 'error', 'maintain']
        for cst in credible_status:
            self.check_and_delete(cst, task_histories['tasks'])
        strategy.task_histories = json.dumps(task_histories)
        strategy.save(update_fields=['task_histories'])
        return task_context


class WebAnalyzeFinished(task.Task):
    def __init__(self, name, policy_id, config, book_uuid, inject=None):
        super(WebAnalyzeFinished, self).__init__(r'WebAnalyzeFinished {}'.format(name), inject=inject)
        self._policy_id = policy_id
        self._config = config
        self._book_uuid = book_uuid

    def execute(self, task_context, *args, **kwargs):
        strategy = WebGuardStrategy.objects.get(id=self._policy_id)
        strategy.running_task = None

        try:
            # _logger.info(r'WebAnalyzeFinished {} begin : {} {}'.format(self.name, args, kwargs))
            if WebAnalyze.has_error(task_context):
                task_context['task_history']['credible'] = 'error'
            # 在维护模式下，可信 是无效的!
            elif strategy.force_credible and (task_context['task_history']['credible'] != 'maintain'):
                task_context['task_history']['credible'] = 'yes'
            elif 'unknown' == task_context['task_history']['credible']:
                task_context['task_history']['credible'] = 'yes'

            task_histories = json.loads(strategy.task_histories)

            task_history = task_histories['tasks'][-1]
            assert task_history['book_uuid'] == self._book_uuid, 'task_history_book_id:{},self._book_uuid:{}'.format(
                task_history['book_uuid'], self._book_uuid)
            task_history['credible'] = task_context['task_history']['credible']
            strategy.task_histories = json.dumps(task_histories)

            # 设置策略状态
            if task_context['task_history']['credible'] == 'yes':
                strategy.set_present_status(WebGuardStrategy.WEB_NORMAL)
            elif task_context['task_history']['credible'] == 'no':
                strategy.set_present_status(WebGuardStrategy.WEB_EMERGENCY)
            elif task_context['task_history']['credible'] == 'maintain':
                strategy.set_present_status(WebGuardStrategy.WEB_MAINTAIN)
            else:
                strategy.set_present_status(WebGuardStrategy.WEB_UNKNOWN)

            if task_context['task_history']['credible'] == 'yes':
                strategy.use_history = True

        except Exception as e:
            _logger.error(r'WebAnalyzeFinished failed : {}'.format(e), exc_info=True)
            task_context['error'] = (r'完成任务失败', r'WebAnalyzeFinished failed : {}'.format(e))
        finally:
            # _logger.info(r'WebAnalyzeFinished {} end .'.format(self.name))
            strategy.save(update_fields=['running_task', 'task_histories', 'use_history'])


class WebAnalyzeStarting(task.Task):
    default_provides = 'task_context'

    def __init__(self, name, policy_id, config, book_uuid, inject=None):
        super(WebAnalyzeStarting, self).__init__(r'WebAnalyzeStarting {}'.format(name), inject=inject)
        self._ident = name
        self._policy_id = policy_id
        self._config = config
        self._book_uuid = book_uuid

    def execute(self, *args, **kwargs):
        try:
            # _logger.info(r'WebAnalyzeStarting {} begin : {} {}'.format(self.name, args, kwargs))
            strategy = WebGuardStrategy.objects.get(id=self._policy_id)
            strategy.set_present_status(WebGuardStrategy.WEB_ANALYZING)
            task_histories = json.loads(strategy.task_histories)

            task_histories['tasks'] = list(filter(lambda x: x['book_uuid'] != self._book_uuid, task_histories['tasks']))
            credible_tasks = list(filter(lambda x: x['credible'] == 'yes', task_histories['tasks']))
            if len(credible_tasks) == 0:
                last_credible_task = None
            else:
                last_credible_task = credible_tasks[-1]

            now = datetime.datetime.now()
            now_str = now.strftime(xdatetime.FORMAT_WITH_SECOND_FOR_PATH)
            path_prefix = self._get_storage_node_path(strategy)
            task_history = {
                'book_uuid': self._book_uuid,
                'credible': 'unknown',
                'crawl_site_name': '{}_{}'.format(self._ident, now_str),
                'date_time': now_str,
                'last_credible_task_crawl_site_name':
                    None if not strategy.use_history else (
                        None if last_credible_task is None else last_credible_task['crawl_site_name']),
                'path': os.path.join(path_prefix, 'web', 'crawler'),
            }

            task_histories['tasks'].append(task_history)
            strategy.task_histories = json.dumps(task_histories)
            strategy.force_credible = False
            strategy.save(update_fields=['task_histories', 'force_credible'])

            task_context = {
                'error': None,
                'task_history': task_history,
            }
        except Exception as e:
            _logger.error(r'WebAnalyzeStarting failed : {}'.format(e), exc_info=True)
            task_context = {
                'error': (r'初始化检测任务失败', r'WebAnalyzeStarting failed : {}'.format(e),),
            }
        finally:
            # _logger.info(r'WebAnalyzeStarting {} end .'.format(self.name))
            pass
        return task_context

    def revert(self, **kwargs):
        try:
            _logger.info(r'WebAnalyzeStarting {} revert begin : {}'.format(self.name, kwargs))
            strategy = WebGuardStrategy.objects.get(id=self._policy_id)
            task_histories = json.loads(strategy.task_histories)
            task_histories['tasks'] = list(filter(lambda x: x['book_uuid'] != self._book_uuid, task_histories['tasks']))
            strategy.task_histories = json.dumps(task_histories)
            strategy.save(update_fields=['task_histories'])
        finally:
            _logger.info(r'WebAnalyzeStarting {} revert end .'.format(self.name))

    @staticmethod
    def _get_storage_node_path(strategy_obj):
        storage_ident = json.loads(strategy_obj.ext_info)['storage_device']['ident']
        st_set = StorageNode.objects.filter(ident=storage_ident, deleted=False, available=True)
        if st_set.exists():
            return st_set[0].path
        else:
            raise Exception('not acquire available storage ident:{}'.format(storage_ident))


class WebAnalyze(threading.Thread):
    def __init__(self, policy_id):
        super(WebAnalyze, self).__init__()
        self.name = r'WebAnalyze_{}'.format(policy_id)
        self._policy_id = policy_id
        self._engine = None
        self._book_uuid = None

    def load_from_uuid(self, task_uuid):
        backend = task_backend.get_backend()
        with contextlib.closing(backend.get_connection()) as conn:
            book = conn.get_logbook(task_uuid['book_id'])
            flow_detail = book.find(task_uuid['flow_id'])
        self._engine = engines.load_from_detail(flow_detail, backend=backend, engine='serial')
        self._book_uuid = book.uuid
        self.name += r' load exist uuid {} {}'.format(task_uuid['book_id'], task_uuid['flow_id'])

    def generate_uuid(self, config):
        backend = task_backend.get_backend()
        book = models.LogBook(
            r"{}_{}".format(self.name, datetime.datetime.now().strftime(xdatetime.FORMAT_WITH_SECOND_FOR_PATH)))
        with contextlib.closing(backend.get_connection()) as conn:
            conn.save_logbook(book)

        try:
            self._engine = engines.load_from_factory(create_flow, backend=backend, book=book, engine='serial',
                                                     factory_args=(self.name, self._policy_id, config, book.uuid,)
                                                     )

            self._book_uuid = book.uuid
            return {'book_id': book.uuid, 'flow_id': self._engine.storage.flow_uuid}
        except Exception as e:
            _logger.error(r'generate_uuid failed {}'.format(e), exc_info=True)
            with contextlib.closing(backend.get_connection()) as conn:
                conn.destroy_logbook(book.uuid)
            raise e

    def start(self):
        if self._engine:
            super().start()
        else:
            xlogging.raise_and_logging_error('内部异常，无效的调用', r'start without _engine ：{}'.format(self.name),
                                             http_status.HTTP_501_NOT_IMPLEMENTED)

    def run(self):
        # _logger.info(r'WebAnalyze {} running'.format(self.name))
        try:
            with logging_listener.DynamicLoggingListener(self._engine):
                self._engine.run()
        except Exception as e:
            _logger.error(r'WebAnalyze run engine {} failed {}'.format(self.name, e), exc_info=True)
        finally:
            with contextlib.closing(task_backend.get_backend().get_connection()) as conn:
                conn.destroy_logbook(self._book_uuid)
            self.finish_strategy(self._policy_id)
        # _logger.info(r'WebAnalyze {} stopped'.format(self.name))
        self._engine = None

    @staticmethod
    def has_error(task_context):
        return task_context['error'] is not None

    @staticmethod
    def finish_strategy(strategy_id):
        try:
            obj = WebGuardStrategy.objects.get(id=strategy_id)
            obj.running_task = None
            obj.save(update_fields=['running_task'])
        except Exception as e:
            _logger.error('finish_strategy:{} error:{}'.format(strategy_id, e))
        return None


def create_flow(name, policy_id, config, book_uuid):
    flow = lf.Flow(name).add(
        WebAnalyzeStarting(name, policy_id, config, book_uuid),
        WebSpidersTask(name, config, policy_id),
        WebCompareTask(name, config, policy_id),
        WebSensitiveWordTask(name, config, policy_id),
        WebAnalyzeCleanExpired(name, policy_id, book_uuid),
        WebAnalyzeFinished(name, policy_id, config, book_uuid),
    )
    return flow


class QueryAlarmEvent(object):
    @staticmethod
    def get_event_words(list_events):
        rs = list()
        _l = [Counter(json.loads(event.detail)['words']).values() for event in list_events]
        for _ in _l:
            rs.extend(_)
        return rs

    @staticmethod
    def get_content_events_risk_num(content_events):
        cnt = 0
        for alarm_event in content_events:
            result = json.loads(alarm_event.detail)
            diff_type = result['diff_type']
            if diff_type in ['content-tamper', 'frameworks-tamper']:
                cnt += len(result['diff_detial']['change'])
            elif diff_type in ['pictures-tamper', 'resources-tamper', 'links-tamper']:
                cnt += 1
            else:
                continue
        return cnt

    @staticmethod
    def query_level_and_detail(strategy_obj):
        detail = {
            'sensitive_words_count': 0,
            'content_alter_count': 0,
            'pictures_count': 0,
            'resources_count': 0,
            'links_count': 0,
            'frameworks_count': 0,
        }
        for tamper in ('content', 'sensitive', 'pictures', 'resources', 'links', 'frameworks'):
            tamper_events = QueryAlarmEvent.from_strategy_get_events(strategy_obj, tamper)
            if tamper == 'sensitive':
                detail['sensitive_words_count'] = sum(QueryAlarmEvent.get_event_words(tamper_events))
            if tamper == 'content':
                detail['content_alter_count'] = QueryAlarmEvent.get_content_events_risk_num(tamper_events)
            if tamper == 'pictures':
                detail['pictures_count'] = QueryAlarmEvent.get_content_events_risk_num(tamper_events)
            if tamper == 'resources':
                detail['resources_count'] = QueryAlarmEvent.get_content_events_risk_num(tamper_events)
            if tamper == 'links':
                detail['links_count'] = QueryAlarmEvent.get_content_events_risk_num(tamper_events)
            if tamper == 'frameworks':
                detail['frameworks_count'] = QueryAlarmEvent.get_content_events_risk_num(tamper_events)

        total_counts = detail['sensitive_words_count'] + detail['content_alter_count'] + detail['pictures_count'] + \
                       detail['resources_count'] + detail['links_count'] + detail['frameworks_count']
        if total_counts == 0:
            return 'normal', detail
        ext_info = json.loads(strategy_obj.ext_info)
        level_dict = ext_info['alarm']
        if 'high' in level_dict:
            if total_counts >= int(level_dict['high']):
                return 'high', detail
        if 'medium' in level_dict:
            if total_counts >= int(level_dict['medium']):
                return 'middle', detail
        if 'low' in level_dict:
            if total_counts >= int(level_dict['low']):
                return 'low', detail
        return 'other', detail

    @staticmethod
    def from_strategy_get_events(strategy_obj, sub_str):
        check_type = strategy_obj.check_type
        sub_types = QueryAlarmEvent.get_sub_type(check_type)
        if sub_str == 'content':
            sub_type = sub_types[0]
        elif sub_str == 'sensitive':
            sub_type = sub_types[1]
        elif sub_str == 'pictures':
            sub_type = sub_types[2]
        elif sub_str == 'resources':
            sub_type = sub_types[3]
        elif sub_str == 'links':
            sub_type = sub_types[4]
        elif sub_str == 'frameworks':
            sub_type = sub_types[5]
        else:
            sub_type = sub_types[0]
        not_solve_status = [AlarmEvent.ALARM_EVENT_PENDING,
                            AlarmEvent.ALARM_EVENT_MANUAL_PROCESSING,
                            AlarmEvent.ALARM_EVENT_AUTO_PROCESSING]
        return strategy_obj.alarm_events.all().filter(strategy_sub_type=sub_type, current_status__in=not_solve_status)

    @staticmethod
    def get_sub_type(check_type):
        if check_type == WebGuardStrategy.CHECK_TYPE_HOME_PAGE:
            return [WebGuardStrategy.CHECK_SUB_TYPE_HOME_PAGE_CONTENT_ALTER,
                    WebGuardStrategy.CHECK_SUB_TYPE_HOME_PAGE_SENSITIVE_WORD,
                    WebGuardStrategy.CHECK_SUB_TYPE_HOME_PAGE_PICTURE,
                    WebGuardStrategy.CHECK_SUB_TYPE_HOME_PAGE_RESOURCES,
                    WebGuardStrategy.CHECK_SUB_TYPE_HOME_PAGE_LINKS,
                    WebGuardStrategy.CHECK_SUB_TYPE_HOME_PAGE_FRAMEWORKS]
        elif check_type == WebGuardStrategy.CHECK_TYPE_URLS:
            return [WebGuardStrategy.CHECK_SUB_TYPE_URLS_CONTENT_ALTER,
                    WebGuardStrategy.CHECK_SUB_TYPE_URLS_SENSITIVE_WORD,
                    WebGuardStrategy.CHECK_SUB_TYPE_URLS_PICTURE,
                    WebGuardStrategy.CHECK_SUB_TYPE_URLS_RESOURCES,
                    WebGuardStrategy.CHECK_SUB_TYPE_URLS_LINKS,
                    WebGuardStrategy.CHECK_SUB_TYPE_URLS_FRAMEWORKS]
        elif check_type == WebGuardStrategy.CHECK_TYPE_FILES:
            return [WebGuardStrategy.CHECK_SUB_TYPE_FILES_ALTER]
        else:
            return list()


class OpAlarmEvent(object):
    @staticmethod
    def confirm_events(strategy_obj, alarm_event_log_type):
        for tamper in ('sensitive', 'content', 'pictures', 'resources', 'links', 'frameworks',):
            not_solve_events = QueryAlarmEvent.from_strategy_get_events(strategy_obj, tamper)
            for one_event in not_solve_events:
                one_event.current_status = AlarmEvent.ALARM_EVENT_CONFIRMED
                one_event.save(update_fields=['current_status'])
                if tamper == 'sensitive':
                    OpAlarmEvent._create_sensitive_confirm_log(one_event, alarm_event_log_type)
                else:
                    OpAlarmEvent._create_content_confirm_log(one_event, alarm_event_log_type)
        return None

    @staticmethod
    def _create_sensitive_confirm_log(alarm_event, alarm_event_log_type):
        event_detail = json.loads(alarm_event.detail)
        words = set(event_detail['words'])
        p = '检测URL[{}]'.format(event_detail['url'])
        p1 = '敏感词：{}'.format(','.join(words))
        check_type = alarm_event.strategy.check_type
        sub_types = QueryAlarmEvent.get_sub_type(check_type)
        detail = dict()
        detail['tamper-type'] = 'sensitive_words-tamper'
        detail['description'] = [p, p1]
        detail['url'] = event_detail['url']
        detail['words'] = event_detail['words']
        AlarmEventLog.objects.create(
            strategy=alarm_event.strategy,
            strategy_sub_type=sub_types[1],
            book_uuid=alarm_event.last_update_uuid,
            detail=json.dumps(detail),
            log_type=alarm_event_log_type
        )
        return None

    @staticmethod
    def _create_content_confirm_log(alarm_event, alarm_event_log_type):
        event_detail = json.loads(alarm_event.detail)
        info = WebCompareTask.get_log_detail(event_detail, True)
        check_type = alarm_event.strategy.check_type
        sub_type = WebCompareTask.get_sub_type(info['tamper-type'], check_type)
        AlarmEventLog.objects.create(
            strategy=alarm_event.strategy,
            strategy_sub_type=sub_type,
            book_uuid=alarm_event.last_update_uuid,
            detail=json.dumps(info),
            log_type=alarm_event_log_type
        )
        return None


class AlarmEventMessage(object):
    @staticmethod
    def generate(alarm_level, alarm_detail):
        msg = r'检测到{}异常，其中'.format(xdata.STRATEGY_EVENT_STATUS[alarm_level])
        if alarm_detail['sensitive_words_count'] != 0:
            msg += r'敏感词（{}）处 '.format(alarm_detail['sensitive_words_count'])
        other_counts = alarm_detail['content_alter_count'] + alarm_detail['pictures_count'] + \
                       alarm_detail['resources_count'] + alarm_detail['links_count'] + alarm_detail['frameworks_count']
        if other_counts != 0:
            msg += r'页面内容改变（{}）处 '.format(other_counts)
        return msg
