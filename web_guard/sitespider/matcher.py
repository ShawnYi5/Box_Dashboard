#!/usr/bin/env python3
# coding=utf-8
import threading, hashlib, os, subprocess, json, time
import difflib, shutil, subprocess
import socket
from sqlalchemy.exc import IntegrityError
from sqlalchemy import Column, String, Integer, DateTime, create_engine, ForeignKey, or_
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base

try:
    from .sitespider.models import CModel_site_webpage, CModel_site
    from .sitespider.settings import SQLLITE_DICT_DB_PATH
except Exception as e:
    from sitespider.models import CModel_site_webpage, CModel_site
    from sitespider.settings import SQLLITE_DICT_DB_PATH

from datetime import datetime

SQLLITE_LOCK_TIMEOUT = 60
SQLLITE_LOCK_SLEEP_TIME = 1

DICT_BIN_PATH = '/var/db/dict.bin'

try:
    from box_dashboard import xlogging
except Exception as e:
    import logging as xlogging

_logger = xlogging.getLogger(__name__)

Base = declarative_base()
# 初始化数据库连接:
engine = create_engine("sqlite:///{}?check_same_thread=False".format(SQLLITE_DICT_DB_PATH))
# 创建DBSession类型:
DBSession = sessionmaker(bind=engine)


class CModel_dict(Base):
    __tablename__ = 'words'
    id = Column(Integer, primary_key=True)
    word = Column(String(260), unique=True)
    is_dirty = Column(Integer, default='0')

    def get_word_count(self):
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                count = session.query(CModel_dict).count()
                return count
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('addPagetitle {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()

    def get_word_list(self, filter, limit, offset):
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                wl = session.query(CModel_dict)
                if filter and 'word' in filter:
                    wl = wl.filter(CModel_dict.word.like('%{}%'.format(filter['word'])))
                if filter and 'is_dirty' in filter:
                    wl = wl.filter(CModel_dict.is_dirty == filter['is_dirty'])
                return wl.limit(limit).offset(offset).all()
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('addPagetitle {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()

    def add_word(self, word):
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                new_dict = CModel_dict(word=word)
                session.add(new_dict)
                session.commit()
                return True
            except IntegrityError as e:
                # 违反unique约束
                return False
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('addPagetitle {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()

    def del_word(self, word):
        try:
            session = DBSession()
            session.query(CModel_dict).filter(CModel_dict.word == word).delete()
            session.commit()
        except Exception as e:
            return False
        finally:
            session.close()
        return True

    def del_all_word(self):
        try:
            session = DBSession()
            session.query(CModel_dict).delete()
            session.commit()
        except Exception as e:
            return False
        finally:
            session.close()
        return True

    def sign_dirty_words(self):
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                session.query(CModel_dict).update({CModel_dict.is_dirty: '0'})
                session.commit()
                break
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('sign_dirty_words 0 {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                wl = session.query(CModel_dict)
                session.close()
                words = wl.all()
                for word in words:
                    session.query(CModel_dict).filter(CModel_dict.word.like('{}_%'.format(word.word))).update(
                        {CModel_dict.is_dirty: '1'}, synchronize_session=False)
                session.commit()
                break
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('sign_dirty_words 1 {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()


if not os.path.isfile(SQLLITE_DICT_DB_PATH):
    Base.metadata.create_all(engine)


class Matcher():
    def __init__(self, host='127.0.0.1', port=21112):
        self._host = host
        self._port = port
        self.client = None

    def _excute_cmd_and_return_code(self, cmd, workpath):
        with subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=workpath,
                              universal_newlines=True) as p:
            stdoutdata, stderrdata = p.communicate()
            if stdoutdata:
                _logger.info(r'Matcher stdoutdata={}'.format(stdoutdata))
            if stderrdata:
                _logger.info(r'Matcher stderrdata={}'.format(stderrdata))
        return p.returncode

    def start_server(self):
        self._excute_cmd_and_return_code("./matcher -D -f {}".format(DICT_BIN_PATH),
                                         "/usr/sbin/aio/libra-server")

    def del_dict(self):
        try:
            os.remove(DICT_BIN_PATH)
        except Exception as e:
            pass

    def add_ref_count(self):
        '''增加引用计数'''
        self._connect()
        cmd = 'ADDREF\r\n'
        self.client.send(cmd.encode('utf8'))
        data = self.client.recv(1024)
        # self._close()
        if data:
            return data.decode('utf8')
        return 'failed'

    def del_ref_count(self):
        '''减少引用计数'''
        # self._connect()
        cmd = 'DELREF\r\n'
        self.client.send(cmd.encode('utf8'))
        data = self.client.recv(1024)
        self._close()
        if data:
            return data.decode('utf8')
        return 'failed'

    def _connect(self):
        ADDR = (self._host, self._port)
        self.client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.client.setblocking(1)
        self.client.settimeout(5)
        self.client.connect(ADDR)

    def set(self, key):
        # self._connect()
        cmd = 'STORE {}\r\n'.format(key)
        self.client.send(cmd.encode('utf8'))
        data = self.client.recv(1024)
        # self._close()
        if data:
            return data.decode('utf8')
        return 'failed'

    def delete(self, key):
        # self._connect()
        cmd = 'DELETE {}\r\n'.format(key)
        self.client.send(cmd.encode('utf8'))
        data = self.client.recv(1024)
        # self._close()
        if data:
            return data.decode('utf8')
        return 'failed'

    def save_dict(self):
        # self._connect()
        cmd = 'SAVEDICT\r\n'
        self.client.send(cmd.encode('utf8'))
        data = self.client.recv(1024)
        # self._close()
        if data:
            return data.decode('utf8')
        return 'failed'

    def gets(self, content, count=-1):
        sensitiveWordsList = list()
        # self._connect()
        _len = len(content.encode('utf8'))
        if _len <= 0:
            return sensitiveWordsList

        cmd = 'VAILD {} {}\r\n{}'.format(count, _len, content)
        self.client.send(cmd.encode('utf8'))
        fd = self.client.makefile('rb', 0)
        data = fd.readline()
        if data:
            line = data.decode('utf8')
            if line[:3] == '[+]':
                count = line[3:]
                for i in range(int(count)):
                    data = fd.readline()
                    line = data.decode('utf8').strip()
                    sensitiveWordsList.append(line)
        # self._close()
        return sensitiveWordsList

    def _close(self):
        self.client.close()


class CSensitiveWordsInterface():
    def _matcher_file(self, matcher, crawlname, link, filepath, word_count, current, total, callback):
        ret = 0
        max_len = 1025
        err_string = 'ERROR_SUCCESS'
        if not os.path.isfile(filepath):
            ret = 2
            err_string = '找不到文件{}'.format(filepath)
            sensitive_word_result = {"url": link, "words": [], "total": 0, "current": 0, "path_current": filepath,
                                     "resourceType": "url"}
            return callback(crawlname, sensitive_word_result, ret, err_string)

        try:
            file_object = open(filepath, 'r', encoding='utf-8')
            lines = file_object.readlines()
            file_object.close()
        except Exception as e:
            lines = None

        if lines is None:
            try:
                file_object = open(filepath, 'r', encoding='gbk')
                lines = file_object.readlines()
                file_object.close()
            except Exception as e:
                lines = None

        if lines is None:
            try:
                file_object = open(filepath, 'r', encoding='gb2312')
                lines = file_object.readlines()
                file_object.close()
            except Exception as e:
                _logger.warning('open file Exception: {}, {}'.format(filepath, str(e)))
                ret = 3
                err_string = '打开文件异常{}'.format(filepath)
                sensitive_word_result = {"url": link, "words": [], "total": 0, "current": 0, "path_current": filepath,
                                         "resourceType": "url"}
                return callback(crawlname, sensitive_word_result, ret, err_string)

        sensitiveWordsList = list()
        for line in lines:
            line = line.strip()
            if len(line) == 0:
                continue
            # if self.is_my_web_site(line):
            #    raise OwnWebsiteException('own website path={}'.format(filepath))
            if word_count == -1 or len(sensitiveWordsList) < word_count:
                i = 0
                tmplist = matcher.gets(line[i:i + max_len], word_count)
                sensitiveWordsList.extend(tmplist)
                i = i + max_len
                while i < len(line):
                    sensitiveWordsList.extend(matcher.gets(line[i:i + max_len], word_count))
                    i = i + max_len
            else:
                break

        sensitive_word_result = {"url": link, "words": sensitiveWordsList, "total": total, "current": current,
                                 "path_current": filepath, "resourceType": "url"}
        return callback(crawlname, sensitive_word_result, ret, err_string)

    def sensitive_word_test(self, crawlname, word_count, callback):
        matcher = Matcher()
        matcher.start_server()
        st1 = datetime.now()
        while True:
            try:
                matcher.add_ref_count()
            except Exception as e:
                sensitive_word_result = {"url": '', "words": [], "total": 0, "current": 0}
                st2 = datetime.now()
                if (st2 - st1).seconds > 10:
                    callback(crawlname, sensitive_word_result, 1, '无法连接服务器')
                    return (1, '无法连接服务器')
                continue
            break;
        try:
            site_id = CModel_site().get_site_id(crawlname)
            if site_id == 0:
                matcher.del_ref_count()
                sensitive_word_result = {"url": '', "words": [], "total": 0, "current": 0}
                callback(crawlname, sensitive_word_result, 4, '找不到站点{}'.format(crawlname))
                return (2, '找不到站点{}'.format(crawlname))
            mSiteWebpage = CModel_site_webpage()
            filter = {"resourceType": "url"}
            sitelinks = mSiteWebpage.get_webpage_link(site_id, filter)
            current = 0
            total = len(sitelinks)
            for obj in sitelinks:
                current = current + 1
                ret = self._matcher_file(matcher, crawlname, obj[1].link, obj[1].path, word_count, current, total,
                                         callback)
                if ret == 1:
                    break
        finally:
            matcher.del_ref_count()
        return (0, "ERROR_SUCCESS")

    def get_sensitive_word_count(self):
        return CModel_dict().get_word_count()

    def get_sensitive_word_list(self, filter, limit, offset):
        return CModel_dict().get_word_list(filter, limit, offset)

    def add_sensitive_word(self, word):
        return CModel_dict().add_word(word)

    def del_sensitive_word(self, word):
        return CModel_dict().del_word(word)

    def del_all_sensitive_word(self):
        return CModel_dict().del_all_word()

    def sign_dirty_words(self):
        return CModel_dict().sign_dirty_words()

    def gen_sensitive_word_bin(self):
        matcher = Matcher()
        matcher.del_dict()
        matcher.start_server()
        st1 = datetime.now()
        while True:
            try:
                matcher.add_ref_count()
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > 10:
                    return (1, str(e))
                continue
            break;
        total = self.get_sensitive_word_count()
        offset = 0
        limit = 5000
        filter = {"is_dirty": "0"}
        while offset < total:
            words = self.get_sensitive_word_list(filter, limit, offset)
            offset = offset + limit
            for word in words:
                matcher.set(word.word)
        matcher.save_dict()
        matcher.del_ref_count()
        return (0, 'SUCCESS')


if not os.path.isfile(DICT_BIN_PATH):
    CSensitiveWordsInterface().gen_sensitive_word_bin()

if __name__ == "__main__":
    matcher = Matcher()
    matcher.start_server()
    st1 = datetime.now()
    while True:
        try:
            matcher.add_ref_count()
        except Exception as e:
            st2 = datetime.now()
            if (st2 - st1).seconds > 10:
                print('无法连接服务器')
                exit(0)
            continue
        break;
    sensitiveWordsList = matcher.gets('hello', -1)
    print(sensitiveWordsList)
    matcher.del_ref_count()
