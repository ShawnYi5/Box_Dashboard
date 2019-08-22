#!/usr/bin/env python3
# coding=utf-8
import threading, hashlib, os, subprocess, json, time
import difflib, shutil, re

try:
    from .sitespider.settings import SQLLITE_DB_PATH
    from .sitespider.models import CModel_pagetitle, CModel_webpage, CModel_site_webpage, CModel_site, \
        CModel_ignore_url_area
    from .myexception import OwnWebsiteException
except Exception as e:
    from sitespider.settings import SQLLITE_DB_PATH
    from sitespider.models import CModel_pagetitle, CModel_webpage, CModel_site_webpage, CModel_site, \
        CModel_ignore_url_area
    from myexception import OwnWebsiteException

try:
    from box_dashboard import xlogging
except Exception as e:
    import logging as xlogging

_logger = xlogging.getLogger(__name__)

OWN_WEB_SITE_MD5 = '7bb8a9e678a02794491cb2284bc2d7dc'


class CWebsiteMonitor():
    _crawl_thread_handle_list = list()
    _compare_thread_handle_list = list()
    _dbpath = SQLLITE_DB_PATH
    _savepath = None

    def __init__(self, savepath):
        self._savepath = savepath

    def _excute_cmd_and_return_code(self, cmd):
        workpath = os.path.dirname(os.path.realpath(__file__))
        _logger.info(r'_excute_cmd_and_return_code cmd={},workpath={}'.format(cmd, workpath))
        with subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=workpath,
                              universal_newlines=True) as p:
            stdoutdata, stderrdata = p.communicate()
            if stdoutdata:
                _logger.info(r'scrapy stdoutdata={}'.format(stdoutdata))
            if stderrdata:
                _logger.info(r'scrapy stderrdata={}'.format(stderrdata))

        return p.returncode

    def _MD5(self, src):
        m2 = hashlib.md5()
        m2.update(src.encode('utf-8'))
        return m2.hexdigest()

    def scrapyCrawl(self, paramPath):
        cmd = 'scrapy crawl site -a paramPath={} -L ERROR'.format(paramPath)
        self._excute_cmd_and_return_code(cmd)

    def crawlWebsite(self, name, paramPath, callback=None):
        thread_name = self._MD5(name)
        thread_handle = self._IsExsitThreadHandle(thread_name, 'crawl')
        if thread_handle and not thread_handle.isAlive():
            self._crawl_thread_handle_list.remove(thread_handle)
            thread_handle = None
        if thread_handle is None:
            thread_handle = threading.Thread(target=self.scrapyCrawl, args=(paramPath,))
            thread_handle.setName(thread_name)
            self._crawl_thread_handle_list.append(thread_handle)
            thread_handle.start()

    def getWebsites(self, filter):
        return CModel_site().get_site_list(filter)

    def delWebsites(self, site_id):
        CModel_pagetitle().delBySiteid(site_id)
        CModel_webpage().delBySiteid(site_id)
        CModel_site_webpage().delBySiteid(site_id)
        path = CModel_site().get_site_path(site_id)
        if path and len(path) > 5:
            try:
                shutil.rmtree(path)
            except Exception as e:
                pass
        CModel_site().delBySiteid(site_id)

    def delWebsite(self, sitename):
        site_id = CModel_site().get_site_id(sitename)
        return self.delWebsites(site_id)

    def countWebsitePages(self, sitename):
        site_id = CModel_site().get_site_id(sitename)
        return CModel_webpage().get_site_page_count(site_id)

    def getWebsitePage(self, sitename, limit, offset, filter=None):
        site_id = CModel_site().get_site_id(sitename)
        return CModel_webpage().get_page_list(site_id, limit, offset, filter)

    def get_page_title(self, webpage_id):
        return CModel_pagetitle().get_page_title(webpage_id)

    def _read_all_file(self, path):
        all_the_text = None
        try:
            with open(path, 'r', encoding='utf-8') as f:
                all_the_text = f.read()
                f.close()
        except Exception as e:
            pass

        if all_the_text is None:
            try:
                with open(path, 'r', encoding='gbk') as f:
                    all_the_text = f.read()
                    f.close()
            except Exception as e:
                pass

        if all_the_text is None:
            try:
                with open(path, 'r', encoding='gb2312') as f:
                    all_the_text = f.read()
                    f.close()
            except Exception as e:
                return (1, '读取文件{}异常,e={}'.format(path, e))
        return (0, all_the_text)

    def _is_float(self, str):
        try:
            float(str)
            return True
        except Exception as e:
            return False
        return False

    def _diff_summary(self, path1, path2, ignore_area):
        all_the_text_1 = None
        all_the_text_2 = None

        code, all_the_text_1 = self._read_all_file(path1)
        if code != 0:
            return (1, '读取文件{}异常,e={}'.format(path1, all_the_text_1), 0, 0, 0, 0, 0, [])

        # if self.is_my_web_site(all_the_text_1):
        #    raise OwnWebsiteException('own website path={}'.format(path1))

        code, all_the_text_2 = self._read_all_file(path2)
        if code != 0:
            return (2, '读取文件{}异常,e={}'.format(path2, all_the_text_2), 0, 0, 0, 0, 0, [])

        for ignore in ignore_area:
            p = re.compile(ignore["re"])
            all_the_text_1 = p.sub(r'', all_the_text_1)
            all_the_text_2 = p.sub(r'', all_the_text_2)

        s = difflib.SequenceMatcher(lambda x: len(x.strip()) == 0,  # ignore blank lines
                                    all_the_text_1, all_the_text_2)
        diffcount = 0
        delcount = 0
        insertcount = 0
        replacecout = 0
        charactercount = 0
        changelist = list()
        for tag, i1, i2, j1, j2 in s.get_opcodes():
            t1 = all_the_text_1[i1:i2]
            t2 = all_the_text_2[j1:j2]
            if tag == 'equal':
                pass
            elif tag == 'delete':
                diffcount = diffcount + 1
                delcount = delcount + 1
                charactercount += i2 - i1
                changelist.append({"type": "delete", "src": t1, "dest": t2})
            elif tag == 'insert':
                diffcount = diffcount + 1
                insertcount = insertcount + 1
                charactercount += i2 - i1
                changelist.append({"type": "insert", "src": t1, "dest": t2})
            elif tag == 'replace':
                if self._is_float(t1) and self._is_float(t2):
                    continue
                diffcount = diffcount + 1
                replacecout = replacecout + 1
                charactercount += i2 - i1
                changelist.append({"type": "replace", "src": t1, "dest": t2})
            else:
                diffcount = diffcount + 1
                charactercount += i2 - i1

        return (0, 'SUCCESS', diffcount, delcount, insertcount, replacecout, charactercount, changelist)

    def _Compare_increase_decrease_pages(self, comparename, site_id_1, site_id_2, inspect_item, callback):
        mSiteWebpage = CModel_site_webpage()
        site1links = mSiteWebpage.get_webpage_link(site_id_1)
        for obj in site1links:
            idmd5 = mSiteWebpage.get_id_and_md5(site_id_2, obj[1].link, obj[1].resourceType)
            id = idmd5[0]
            md5 = idmd5[1]
            webpage_path_1 = obj[1].path
            reference = obj[1].reference
            resourceType = obj[1].resourceType
            webpage_path_2 = idmd5[2]
            if id == 0:
                # 减少文件
                NeedDelFileCompare = False
                if NeedDelFileCompare:
                    compare_result = {"type": 4, "url": obj[1].link, "reference": reference,
                                      "resourceType": resourceType,
                                      "delpage": True, "path_base": webpage_path_1}
                    ret = callback(comparename, compare_result, 0, 'SUCCESS')
                    if ret == 1:
                        return 1
            elif (md5 == obj[1].md5):
                # 文件相同
                pass
            else:
                # 文件不同
                if obj[1].resourceType in ('url', 'js', 'css',):
                    type = None
                    if resourceType == 'url':
                        if 'content-tamper' in inspect_item:
                            type = 'content-tamper'
                    elif 'frameworks-tamper' in inspect_item:
                        type = 'frameworks-tamper'
                    if type:
                        path1 = webpage_path_1
                        path2 = webpage_path_2
                        ignore_area = list()
                        relist = CModel_ignore_url_area().get_re_list(obj[1].link)
                        for onere in relist:
                            ignore_area.append({"re": onere[1].ignore})
                        errcode, err_string, diffcount, delcount, insertcount, replacecout, charactercount, change = self._diff_summary(
                            path1,
                            path2,
                            ignore_area)
                        if diffcount <= 0:
                            continue
                        compare_result = {"type": type, "url": obj[1].link, "path_base": path1, "path_current": path2,
                                          "diffcount": diffcount, "delcount": delcount,
                                          "insertcount": insertcount, "replacecout": replacecout,
                                          "charactercount": charactercount, "change": change, "reference": reference,
                                          "resourceType": resourceType}
                        ret = callback(comparename, compare_result, errcode, err_string)
                        if ret == 1:
                            return 1
                else:
                    type = None
                    if resourceType == 'image':
                        if 'pictures-tamper' in inspect_item:
                            type = 'pictures-tamper'
                    elif 'resources-tamper' in inspect_item:
                        type = 'resources-tamper'

                    if type:
                        compare_result = {"type": type, "url": obj[1].link, "filechange": True, "reference": reference,
                                          "resourceType": resourceType, "path_current": webpage_path_2,
                                          "path_base": webpage_path_1}
                        ret = callback(comparename, compare_result, 0, 'SUCCESS')
                        if ret == 1:
                            return 1

        NeedAddFileCompare = False
        if NeedAddFileCompare:
            site2links = mSiteWebpage.get_webpage_link(site_id_2)
            for obj in site2links:
                idmd5 = mSiteWebpage.get_id_and_md5(site_id_1, obj[1].link, obj[1].resourceType)
                id = idmd5[0]
                reference = obj[1].reference
                resourceType = obj[1].resourceType
                webpage_path_2 = obj[1].path
                if id == 0:
                    # 增加文件
                    compare_result = {"type": 3, "url": obj[1].link, "newpage": True, "reference": reference,
                                      "resourceType": resourceType, "path_current": webpage_path_2}
                    ret = callback(comparename, compare_result, 0, 'SUCCESS')
                    if ret == 1:
                        return 1

        compare_result = {"type": "finish", "err_string": "SUCCESS"}
        callback(comparename, compare_result, 0, 'SUCCESS')
        return 0

    def compare_site(self, comparename, crawlname_base, crawlname_current, inspect_item, callback):
        site_id_1 = CModel_site().get_site_id(crawlname_base)
        site_id_2 = CModel_site().get_site_id(crawlname_current)
        ret = 0
        err_string = 'SUCCESS'
        if site_id_1 == 0:
            ret = 1
            err_string = '找不到站点{}'.format(crawlname_base)
        if site_id_2 == 0:
            ret = 2
            err_string = '找不到站点{}'.format(crawlname_current)
        if ret == 0:
            self._Compare_increase_decrease_pages(comparename, site_id_1, site_id_2, inspect_item, callback)
        return (ret, err_string)

    def _IsExsitThreadHandle(self, name, type):
        if 'crawl' in type:
            for thread_handle in self._crawl_thread_handle_list:
                if thread_handle.getName() == name:
                    return thread_handle
        if 'compare' in type:
            for thread_handle in self._compare_thread_handle_list:
                if thread_handle.getName() == name:
                    return thread_handle
        return None

    def isCrawling(self, name):
        thread_name = self._MD5(name)
        thread_handle = self._IsExsitThreadHandle(thread_name, 'crawl')
        if thread_handle and thread_handle.isAlive():
            return True
        if thread_handle and not thread_handle.isAlive():
            self._crawl_thread_handle_list.remove(thread_handle)
            thread_handle = None
        return False


class CSiteSpiderInterface():
    def __init__(self):
        pass

    def _MD5(self, src):
        m2 = hashlib.md5()
        m2.update(src.encode('utf-8'))
        return m2.hexdigest()

    def _mkdir(self, path):
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

    def _stop_crawl(self, crawlname, path):
        strSavePath = path
        strSavePath = os.path.join(strSavePath, crawlname)
        stopfile = os.path.join(strSavePath, 'stop')
        try:
            f = open(stopfile, 'w')
            f.close()
        except Exception as e:
            return 1
        return 0

    def crawl_site(self, crawlname, path, policy, browser, callback):
        param = {'crawlname': crawlname, 'path': path, 'policy': policy, 'browser': browser, 'dbpath': SQLLITE_DB_PATH}
        paramPath = os.path.join(path, crawlname, crawlname + '.json')
        _logger.info(r'crawl_site paramPath={}, param={}'.format(paramPath, json.dumps(param)))
        self._mkdir(os.path.dirname(paramPath))
        try:
            f = open(paramPath, 'w')
            f.write(json.dumps(param))
            f.close()
        except Exception as e:
            _logger.error(r'crawl_site Exception : can not save param to {}({})'.format(paramPath, e))
            return (1, 'can not save param to {}({})'.format(paramPath, e))

        WebsiteMonitor = CWebsiteMonitor(path)
        WebsiteMonitor.crawlWebsite(crawlname, paramPath)
        bCancel = False
        while (True):
            time.sleep(5)
            count = WebsiteMonitor.countWebsitePages(crawlname)
            pages = WebsiteMonitor.getWebsitePage(crawlname, limit=1, offset=count - 1)
            last_page = 'none'
            err_string = '操作成功'
            if pages:
                for page in pages:
                    last_page = page[0].link
                    if OWN_WEB_SITE_MD5 == page[0].md5:
                        self._stop_crawl(crawlname, path)
                        raise OwnWebsiteException('own website md5={}'.format(OWN_WEB_SITE_MD5))
            if not WebsiteMonitor.isCrawling(crawlname):
                status = 0
                last_crawl = {"url": last_page, "count": count}
                ret = 0
                if bCancel:
                    ret = 2
                    err_string = '用户取消'
                callback(crawlname, status, last_crawl, ret, err_string)
                break;
            else:
                status = 1
                last_crawl = {"url": last_page, "count": count}
                ret = 0
                if count > 0:
                    ret = callback(crawlname, status, last_crawl, ret, err_string)
                    if ret == 1:
                        ret = self._stop_crawl(crawlname, path)
                        if ret != 0:
                            err_string = '取消失败，请重试'
                        else:
                            bCancel = True
                        callback(crawlname, status, last_crawl, ret, err_string)

        filter = {"md5": OWN_WEB_SITE_MD5}
        pages = WebsiteMonitor.getWebsitePage(crawlname, limit=1, offset=0, filter=filter)
        if pages:
            for page in pages:
                if OWN_WEB_SITE_MD5 == page[0].md5:
                    raise OwnWebsiteException('own website md5={}'.format(OWN_WEB_SITE_MD5))

        return (0, 'SUCCESS')

    def del_site(self, crawlname):
        CWebsiteMonitor('').delWebsite(crawlname)
        return (0, 'SUCCESS')

    def compare_site(self, comparename, crawlname_base, crawlname_current, inspect_item, callback):
        return CWebsiteMonitor('').compare_site(comparename, crawlname_base, crawlname_current, inspect_item, callback)

    def countWebsitePages(self, sitename):
        return CWebsiteMonitor('').countWebsitePages(sitename)

    def getWebsitePage(self, sitename, limit, offset, filter):
        return CWebsiteMonitor('').getWebsitePage(sitename, limit, offset, filter)

    def get_page_title(self, webpage_id):
        return CWebsiteMonitor('').get_page_title(webpage_id)


if __name__ == "__main__":
    # WebsiteMonitor = CWebsiteMonitor('/home/web')
    WebsiteMonitor = CWebsiteMonitor('E:\\web')

    filter = {}
    # filter = {'id':7,'name':'website2','url':'http://www.changhai.org','siteStatus':'0'}
    sites = WebsiteMonitor.getWebsites(filter)
    for site in sites:
        print('id={},name={},url={},siteStatus={}'.format(site.id, site.name, site.url, site.siteStatus))

    # 展示攫取到的网页
    totalpages = WebsiteMonitor.countWebsitePages(sitename='个人主页1')
    WebsiteMonitor.getWebsitePage(sitename='个人主页1', limit=30, offset=0)
