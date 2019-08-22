import json
import os
import signal
import subprocess
import time
from threading import Thread, Event

from django.db.models.signals import post_save

from apiv1.models import VirtualMachineSession
from box_dashboard import xlogging, boxService, xdata

_logger = xlogging.getLogger(__name__)
session_created_event = Event()


def session_created(sender, **kwargs):
    if kwargs['created'] or ('enable' in kwargs['update_fields']):
        session_created_event.set()
    else:
        pass


post_save.connect(session_created, sender=VirtualMachineSession)


class VmwareHostSessionLogic(Thread):

    def __init__(self):
        super(VmwareHostSessionLogic, self).__init__(name='VmwareHostSessionLogic')
        self._wk_threads = dict()

    def run(self):
        # time.sleep(60)

        while True:
            try:
                self.work()
            except Exception as e:
                _logger.error('VmwareHostSessionLogic error:{}'.format(e), exc_info=True)

            if session_created_event.wait(20):
                session_created_event.clear()

    @xlogging.db_ex_wrap
    def work(self):
        self._wk_threads = {wk.key: wk for wk in self._wk_threads.values() if wk.is_alive()}
        for session in VirtualMachineSession.objects.all():
            if (session.ident not in self._wk_threads) and session.enable:
                wk = VmwareHostSessionWorker(session.name)
                if wk.init(session):
                    wk.setDaemon(True)
                    wk.start()
                    self._wk_threads[wk.ident] = wk
                    time.sleep(1)  # 休息1s，否则并发登陆造成boxService卡死
                else:
                    _logger.error('start session:{} fail'.format(session))

            else:
                if (not session.enable) and self._wk_threads.get(session.ident, None):
                    self._wk_threads[session.ident].notify_end()


class VmwareHostSessionWorker(Thread):
    """
    每一个免代理的客户端维持一个线程，此线程不要有数据库链接。
    不然会造成链接过多
    """

    def __init__(self, name):
        super(VmwareHostSessionWorker, self).__init__(
            name='VmwareHostSessionWorker_{}'.format(name))

        self.key = None
        self._setting_path = ''
        self._home_path = ''
        self._process = None
        self._end_flag = False
        self._end_event = Event()

    def run(self):
        try:
            self._start_process()
            self._waite_process_end()
        except Exception as e:
            _logger.error('VmwareHostSessionWorker error:{}'.format(e), exc_info=True)

    @xlogging.convert_exception_to_value(False)
    def init(self, session):
        boxService.box_service.makeDirs(session.home_path, existOk=True)
        self._setting_path = boxService.box_service.pathJoin([session.home_path, 'setting.json'])
        self._home_path = session.home_path
        self.key = session.ident
        content = dict()
        content['si'] = {
            'host': session.connection.address,
            'user': session.connection.username,
            'password': session.connection.password,
            'port': session.connection.port,
            'disable_ssl_verification': session.connection.disable_ssl
        }
        content['moId'] = session.moid
        content['home_dir'] = session.home_path
        content['login_mac'] = xdata.VMCLIENT_FAKE_MAC  # 登陆用
        content['login_name'] = session.name  # 登陆用
        content['login_id'] = session.id  # 登陆用
        content['login_username'] = session.connection.user.username  # 登陆用

        _logger.info('_get_setting_file write {} to {}'.format(content, self._setting_path))
        with open(self._setting_path, 'w') as f:
            json.dump(content, f)

        return True

    def _start_process(self):
        cmd = ['python', '/usr/sbin/aio/vmware_agent/vmAgent.py', '-s', self._setting_path]
        self._process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                                         cwd=self._home_path)
        assert self._process.poll() is None
        _logger.info('VmwareHostSessionWorker start process:{}|{}'.format(' '.join(cmd), self._process.pid))

    def _waite_process_end(self):
        while self._process and self._process.poll() is None:
            if self._end_flag:
                _logger.info('VmwareHostSessionWorker shutdown process:{}'.format(self._process.pid))
                self.shutdown()
            else:
                pass
            self._end_event.wait(5)
            self._end_event.clear()
        _logger.info('VmwareHostSessionWorker end process:{}|{}|{}'.format(self._process.pid,
                                                                           *self._process.communicate()))

    @xlogging.convert_exception_to_value(None)
    def shutdown(self):
        if self._process and self._process.poll() is None:
            os.kill(self._process.pid, signal.SIGINT)
        else:
            pass

    def notify_end(self):
        self._end_flag = True
        self._end_event.set()
