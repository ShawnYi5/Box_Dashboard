#!/usr/bin/env python3

import datetime
import functools
import inspect
import logging
import logging.config
import os
import threading

import Ice
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from box_dashboard import loadIce

__all__ = [r'raise_and_logging_error', r'getLogger']

config_path = os.path.join(loadIce.current_dir, 'logging.config')
logging.config.fileConfig(config_path)

import Utils

HTTP_STATUS_USER_STORAGE_NODE_NOT_ENOUGH_SPACE = 901
HTTP_STATUS_USER_STORAGE_NODE_NOT_ONLINE = 902
HTTP_STATUS_USER_STORAGE_NODE_NOT_VALID = 903


def _get_front_back_function_info():
    class_name = ''

    frame = inspect.currentframe().f_back.f_back  # 需要回溯两层
    arg_values = inspect.getargvalues(frame)
    args, _, _, value_dict = arg_values
    # we check the first parameter for the frame function is
    # named 'self'
    if len(args) and args[0] == 'self':
        # in that case, 'self' will be referenced in value_dict
        instance = value_dict.get('self', None)
        if instance:
            class_name = getattr(instance, '__class__', None).__name__
            class_name += '.'

    module_name = inspect.getmodule(frame).__name__

    return class_name + frame.f_code.co_name, frame.f_lineno, module_name, arg_values


def _get_front_back_instance():
    frame = inspect.currentframe().f_back.f_back  # 需要回溯两层
    _, _, _, value_dict = inspect.getargvalues(frame)
    return value_dict.get('self')  # 不做容错，调用者保证


def getLogger(name):
    return logging.getLogger(name)


# 在BoxDashboard模块中产生的异常
class BoxDashboardException(Exception):
    def __init__(self, function_name, msg, debug, file_line, http_status, is_log=False):
        super(BoxDashboardException, self).__init__(msg)
        self.function_name = function_name  # 产生异常的方法名，建议使用__qualname__获取
        self.msg = msg  # 异常描述，供用户浏览
        self.debug = debug  # 异常调试，供开发人员排错
        self.file_line = file_line  # 发生异常的行号
        self.http_status = http_status  # web api 返回的 http status
        self.is_log = is_log


class OperationImplemented(BoxDashboardException):
    pass


ERROR_HTTP_STATUS_DEFAULT = 555
ERROR_HTTP_STATUS_NEED_RETRY = 598


# 抛出并记录错误
# msg：参考BoxDashboardException
# debug：参考BoxDashboardException
# http_status：参考BoxDashboardException
# logger：打印调试的logger对象，建议使用logging.getLogger(__name__)，当不传入时，将通过调用栈自动获取
# function_name：参考BoxDashboardException，当不传入时，将通过调用栈自动获取
# file_line：参考BoxDashboardException，当不传入时，将通过调用栈自动获取
def raise_and_logging_error(msg, debug, http_status=ERROR_HTTP_STATUS_DEFAULT, logger=None, print_args=True,
                            function_name=None,
                            file_line=None):
    function_info = None
    if (function_name is None) or (file_line is None) or print_args or (logger is None):
        function_info = _get_front_back_function_info()
        if function_name is None:
            function_name = function_info[0]
        if file_line is None:
            file_line = function_info[1]
        if logger is None:
            logger = logging.getLogger(function_info[2])

    err_log = r'{function_name}({file_line}):{msg} debug:{debug}' \
        .format(function_name=function_name, file_line=file_line, msg=msg, debug=debug)

    logger.error(err_log + ' args:{}'.format(function_info[3]) if print_args else err_log)
    raise BoxDashboardException(function_name, msg, debug, file_line, http_status, True)


# 为类方法添加装饰器的基类
class DecorateClass(object):
    def decorate(self):
        for name, fn in self.iter():
            if callable(fn):
                self.operate(name, fn)


def LockDecorator(locker):
    def _real_decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kv):
            with locker:
                return fn(*args, **kv)

        return wrapper

    return _real_decorator


# 自动为“对象的公共方法”加上锁装饰器
# remark：对象类的公共方法”是指不是由“_”打头的类方法
class LockerDecorator(DecorateClass):
    # obj：对象实例，建议直接使用需要添加装饰器的对象的self，当在__init__中被调用时可不传入，内部将通过调用栈自动获取
    # lock：锁对象，可使用with语句的对象
    def __init__(self, lock, obj=None):
        self.obj = _get_front_back_instance() if obj is None else obj
        self.lock = lock

    def iter(self):
        return [(name, getattr(self.obj, name)) for name in dir(self.obj) if not name.startswith('_')]

    def operate(self, name, fn):
        @functools.wraps(fn)
        def locker(*args, **kv):
            with self.lock:
                return fn(*args, **kv)

        setattr(self.obj, name, locker)


# 自动为“api view”的get、put、post、delete方法加入异常处理
class ApiViewExceptionHandlerDecorator(DecorateClass):
    # obj：对象实例，建议直接使用需要添加装饰器的对象的self
    # logger：日志对象，建议使用logging.getLogger(__name__)，当不传入时，将通过方法自动获取
    def __init__(self, obj=None, logger=None):
        self.obj = _get_front_back_instance() if obj is None else obj
        self.logger = logger

    def iter(self):
        return [(name, getattr(self.obj, name)) for name in dir(self.obj) if name in ('get', 'put', 'post', 'delete')]

    def getLogger(self, module_name):
        if self.logger is not None:
            return self.logger
        else:
            return logging.getLogger(module_name)

    def operate(self, name, fn):
        @functools.wraps(fn)
        def handler(*args, **kv):
            try:
                result = fn(*args, **kv)
                if not isinstance(result, Response):
                    raise BoxDashboardException(fn.__qualname__, r'内部异常，代码777', r'api view return not Response', 0, 555)
                return result
            except BoxDashboardException as bde:
                self.getLogger(fn.__module__).error(
                    r'{fn_name} api view BoxDashboardException:{msg} debug:{debug}'.format(
                        fn_name=fn.__qualname__, msg=bde.msg, debug=bde.debug),
                    exc_info=(not bde.is_log))
                bde.is_log = True
                return Response(bde.msg, status=bde.http_status)
            except ValidationError as ve:
                self.getLogger(fn.__module__).error(
                    r'{fn_name} api view ValidationError :{ve}'.format(fn_name=fn.__qualname__, ve=ve), exc_info=True)
                return Response(status=ve.status_code)
            except Exception as e:
                self.getLogger(fn.__module__).error(
                    r'{fn_name} api view Exception:{e}'.format(fn_name=fn.__qualname__, e=e), exc_info=True)
                return Response(status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        setattr(self.obj, name, handler)


# 当方法内发生异常时，返回 value
def convert_exception_to_value(value):
    def _real_decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kv):
            try:
                return fn(*args, **kv)
            except BoxDashboardException as bde:
                if not bde.is_log:
                    logging.getLogger(fn.__module__).error(
                        r'{fn_name} raise BoxDashboardException need convert to {value} :{msg} debug:{debug}'.format(
                            fn_name=fn.__qualname__, msg=bde.msg, debug=bde.debug, value=value)
                        , exc_info=True)
                    bde.is_log = True
                else:
                    logging.getLogger(fn.__module__).warning(
                        r'{fn_name} raise BoxDashboardException need convert to {value} :{msg} debug:{debug}'.format(
                            fn_name=fn.__qualname__, msg=bde.msg, debug=bde.debug, value=value))
                return value
            except Exception as e:
                logging.getLogger(fn.__module__).error(
                    r'{fn_name} raise Exception need convert to {value} :{e}'.format(
                        fn_name=fn.__qualname__, e=e, value=value), exc_info=True)
                return value

        return wrapper

    return _real_decorator


# 防止单方面重启数据库后，连接丢失的情况发生
def db_ex_wrap(func):
    @functools.wraps(func)
    def wrap(*args, **kwargs):
        try:
            rev = func(*args, **kwargs)
        finally:
            update_db_connections()
        return rev

    return wrap


@convert_exception_to_value(None)
def update_db_connections():
    from django.db import connections
    for conn in connections.all():
        conn.close_if_unusable_or_obsolete()


# 自动为“对象的公共方法”加上异常转换装饰器
# remark：对象类的公共方法”是指不是由“_”打头的类方法
class ConvertExceptionToBoxDashboardExceptionDecorator(DecorateClass):
    # obj：对象实例，建议直接使用需要添加装饰器的对象的self，当在__init__中被调用时可不传入，内部将通过调用栈自动获取
    def __init__(self, obj=None, msgs=None):
        self.obj = _get_front_back_instance() if obj is None else obj
        self.msgs = msgs

    def iter(self):
        return [(name, getattr(self.obj, name)) for name in dir(self.obj) if not name.startswith('_')]

    def get_msg_prefix(self, fn):
        if (self.msgs is None) or (fn.__name__ not in self.msgs):
            return ''
        return self.msgs[fn.__name__] + '，'

    def operate(self, name, fn):
        @functools.wraps(fn)
        def convert(*args, **kv):
            try:
                return fn(*args, **kv)
            except BoxDashboardException as bde:
                if not bde.is_log:
                    logging.getLogger(fn.__module__).error(
                        r'{fn_name} raise BoxDashboardException:{msg} debug:{debug}'.format(
                            fn_name=fn.__qualname__, msg=self.get_msg_prefix(fn) + bde.msg, debug=bde.debug),
                        exc_info=True)
                    bde.is_log = True
                raise
            except Utils.SystemError as se:
                debug = '{fn_name} raise Utils.SystemError:{msg} debug:{debug} raw_code:{rc}' \
                    .format(fn_name=fn.__qualname__, msg=self.get_msg_prefix(fn) + se.description, debug=se.debug,
                            rc=se.rawCode)
                logging.getLogger(fn.__module__).error(debug)
                raise BoxDashboardException(function_name=fn.__qualname__, msg=se.description, debug=debug,
                                            file_line=0, http_status=556, is_log=True)
            except Ice.Exception as e:
                debug = r'{fn_name} raise Ice.Exception:{e}'.format(fn_name=fn.__qualname__, e=e)
                logging.getLogger(fn.__module__).error(debug)
                raise BoxDashboardException(function_name=fn.__qualname__, msg=self.get_msg_prefix(fn) + '模块间通信失败',
                                            debug=debug,
                                            file_line=0, http_status=558, is_log=True)
            except Exception as e:
                debug = r'{fn_name} raise Exception:{e}'.format(fn_name=fn.__qualname__, e=e)
                logging.getLogger(fn.__module__).error(debug, exc_info=True)
                raise BoxDashboardException(function_name=fn.__qualname__, msg=self.get_msg_prefix(fn) + '内部异常，代码2344',
                                            debug=debug,
                                            file_line=0, http_status=557, is_log=True)

        setattr(self.obj, name, convert)


# 自动为“类的公共方法”加上跟踪装饰器
# remark：公共方法”是指不是由“_”打头的类方法
class TraceDecorator(DecorateClass):
    # ignore 忽略列表，可忽略额外的方法
    # obj：对象实例，建议直接使用需要添加装饰器的对象的self，当在__init__中被调用时可不传入，内部将通过调用栈自动获取
    def __init__(self, ignore=None, obj=None):
        self.ignore = list() if ignore is None else ignore
        self.obj = _get_front_back_instance() if obj is None else obj
        self.index = 0

    def iter(self):
        return [(name, getattr(self.obj, name)) for name in dir(self.obj) if
                ((not name.startswith('_')) and (name not in self.ignore) and (not name.startswith('ice_')))]

    def operate(self, name, fn):
        @functools.wraps(fn)
        def trace(*args, **kv):
            logger = logging.getLogger(fn.__module__)
            index = self.index  # 仅仅用于打印调试无需同步
            self.index += 1

            args_exclude_bytearray = tuple(x for x in args if not isinstance(x, (bytearray, bytes)))

            kv_exclude_bytearray = {
                key: value for key, value in kv.items() if not isinstance(value, (bytearray, bytes))
            }

            logger.debug(
                r'{index}:{fn_name} input args:{args} kv:{kv}'.format(
                    index=index, fn_name=fn.__qualname__, args=args_exclude_bytearray, kv=kv_exclude_bytearray))

            returned = fn(*args, **kv)

            if isinstance(returned, (bytearray, bytes)):
                returned_exclude_bytearray = None
            elif isinstance(returned, tuple):
                returned_exclude_bytearray = tuple(x for x in returned if not isinstance(x, (bytearray, bytes)))
            else:
                returned_exclude_bytearray = returned
            logger.debug(
                r'{index}:{fn_name} return:{returned}'.format(index=index, fn_name=fn.__qualname__,
                                                              returned=returned_exclude_bytearray))

            return returned

        setattr(self.obj, name, trace)


class DataHolder(object):
    def __init__(self, value=None):
        self.value = value

    def set(self, value):
        self.value = value
        return value

    def get(self):
        return self.value


logger_traffic_control_locker = threading.Lock()
logger_traffic_control_content = dict()


class logger_traffic_control(object):
    @staticmethod
    @LockDecorator(logger_traffic_control_locker)
    def is_logger_print(slot, content, seconds=300):
        now = datetime.datetime.now()
        entry = logger_traffic_control_content.get(slot, None)
        if entry is None:
            logger_traffic_control_content[slot] = {content: now}
            result = True
        else:
            last_time = entry.get(content, None)
            if last_time is None:
                entry[content] = now
                result = True
            else:
                if (now - last_time).total_seconds() > seconds:
                    entry[content] = now
                    result = True
                else:
                    result = False

        logger_traffic_control._clean_expires(now)

        return result

    @staticmethod
    def _clean_expires(now, seconds=300):
        for _, entry in logger_traffic_control_content.items():
            content_expires = list()
            for content, last_time in entry.items():
                if abs((now - last_time).total_seconds()) > seconds:
                    content_expires.append(content)
            for c in content_expires:
                del entry[c]
