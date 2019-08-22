import json
import os
import sys
import threading
import time
import traceback

from box_dashboard import xlogging

_logger = xlogging.getLogger(__name__)

PYCHARM_DEBUG_FILE = r'/var/aio/BoxDashboard/pycharm-debug-py3k.egg'
# {"address":"172.16.6.80", "cfg":{"port":21000}}
PYCHARM_DEBUG_CONFIG = r'/var/aio/BoxDashboard/pycharm-debug-py3k.json'
CORE_DUMPS_PATH = r'/tmp/coredumps'

if os.path.isfile(PYCHARM_DEBUG_FILE) and os.path.isfile(PYCHARM_DEBUG_CONFIG):
    sys.path.append(PYCHARM_DEBUG_FILE)
    import pydevd

    with open(PYCHARM_DEBUG_CONFIG) as f:
        pycharm_debug_cfg = json.load(f)
    _logger.info(r'pycharm_debug_cfg : {}'.format(pycharm_debug_cfg))

    pydevd.settrace(pycharm_debug_cfg['address'], **pycharm_debug_cfg['cfg'])


class XDebugHelper(threading.Thread):
    TIMER_INTERVAL_SECS = 30

    DUMP_ALL_THREAD_STACK_FILE = r'/var/aio/BoxDashboard/dump_stack'

    def __init__(self):
        threading.Thread.__init__(self)
        self.pycharm_debug = False

    def run(self):
        while True:
            try:
                self.do_run()
                break
            except Exception as e:
                _logger.error(r'XDebugHelper run Exception : {}'.format(e), exc_info=True)

    def do_run(self):
        while True:
            time.sleep(self.TIMER_INTERVAL_SECS)

            self.dump_all_thread_stack_when_file_exist()
            self.truncate_core_dumps()

            # self.stop_pycharm_debug()
            # self.begin_pycharm_debug()

    def dump_all_thread_stack_when_file_exist(self):
        try:
            if not os.path.isfile(self.DUMP_ALL_THREAD_STACK_FILE):
                return
            self.dump_all_thread_stack()
        except Exception as e:
            _logger.error(r'XDebugHelper dump_all_thread_stack_when_file_exist Exception : {}'.format(e), exc_info=True)

    def stop_pycharm_debug(self):
        try:
            if self.pycharm_debug and (not os.path.isfile(PYCHARM_DEBUG_CONFIG)):
                pydevd.stoptrace()
                self.pycharm_debug = False
        except Exception as e:
            _logger.error(r'XDebugHelper stop_pycharm_debug Exception : {}'.format(e), exc_info=True)

    def begin_pycharm_debug(self):
        try:
            if self.pycharm_debug or (not os.path.isfile(PYCHARM_DEBUG_FILE)) \
                    or (not os.path.isfile(PYCHARM_DEBUG_CONFIG)):
                return

            with open(PYCHARM_DEBUG_CONFIG) as f:
                debug_cfg = json.load(f)
            _logger.info(r'pycharm_debug_cfg : {}'.format(debug_cfg))

            pydevd.settrace(debug_cfg['address'], **debug_cfg['cfg'])

            self.pycharm_debug = True
        except Exception as e:
            _logger.error(r'XDebugHelper begin_pycharm_debug Exception : {}'.format(e), exc_info=True)

    def truncate_core_dumps(self):
        try:
            self._truncate_core_dumps()
        except Exception as e:
            _logger.error(r'XDebugHelper truncate_core_dumps Exception : {}'.format(e), exc_info=True)

    @staticmethod
    def dump_all_thread_stack():
        id2name = dict((th.ident, th.name) for th in threading.enumerate())
        for thread_id, stack in sys._current_frames().items():
            _logger.info('Thread {} - {}\n>{}'
                         .format(thread_id, id2name[thread_id], '>'.join(traceback.format_stack(stack))))

    @staticmethod
    def _truncate_core_dumps():
        all_file = os.listdir(CORE_DUMPS_PATH)
        file_items_key = dict()
        for one_file in all_file:
            file_items = one_file.split('-')
            if len(file_items) < 4:
                continue
            XDebugHelper.__get_core_file_info(file_items, file_items_key, one_file)

        for _, v in file_items_key.items():
            if len(v) > 2:
                for one in sorted(v, key=lambda x: x[1], reverse=True)[2:]:
                    os.remove(os.path.join(CORE_DUMPS_PATH, one[0]))

    @staticmethod
    @xlogging.convert_exception_to_value(None)
    def __get_core_file_info(file_items, file_items_key, one_file):
        keys = ''.join(file_items[1:-3])
        times = int(file_items[-1])
        if keys not in file_items_key:
            file_items_key[keys] = [(one_file, times,), ]
        else:
            file_items_key[keys].append((one_file, times,))


class PrintSQL(object):
    def process_response(self, request, response):
        from django.db import connection
        _sql_logger = xlogging.getLogger('djangosql')
        temp = 0
        for x in connection.queries:
            _sql_logger.info('{}:{}--{}'.format(os.getpid(), threading.get_ident(), x))
            temp += float(x['time'])
        _sql_logger.info('{}:{}:{}--time count:{}'.format(request, os.getpid(), threading.get_ident(), temp))
        return response
