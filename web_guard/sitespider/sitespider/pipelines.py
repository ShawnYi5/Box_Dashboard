# -*- coding: utf-8 -*-
from scrapy.exceptions import DropItem
import urllib2, urllib, os, hashlib, shutil, re, subprocess
from .settings import SPIDER_PLATFORM_SYSTEM
from .models import CModel_pagetitle, CModel_webpage, CModel_site_webpage, CModel_site


# Define your item pipelines here
#
# Don't forget to add your pipeline to the ITEM_PIPELINES setting
# See: http://doc.scrapy.org/en/latest/topics/item-pipeline.html
class CSavePage():
    _UserAgent = 'spider'
    # 网页保存路径
    strSavePath = ''

    def __init__(self, savepath=None):
        self.strSavePath = savepath

    def _GetFileMd5(self, filename):
        if not os.path.isfile(filename):
            return 'none'
        myhash = hashlib.md5()
        f = file(filename, 'rb')
        while True:
            b = f.read(8096)
            if not b:
                break
            myhash.update(b)
        f.close()
        return myhash.hexdigest()

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

    def _myurl2pathname(self, url):
        filepath = urllib.url2pathname(url)
        if SPIDER_PLATFORM_SYSTEM == 'linux':
            pos = filepath.find('/')
            if pos > 0:
                filepath = filepath[pos + 2:]
        else:
            pos = filepath.find('\\')
            if pos > 0:
                filepath = filepath[pos + 1:]
        try:
            filepath = os.path.join(self.strSavePath, filepath.encode('utf-8'))
        except Exception as e:
            filepath = os.path.join(self.strSavePath.encode('utf-8'), filepath)
        return filepath

    def _excute_cmd_and_return_code(self, cmd):
        p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             universal_newlines=True)
        stdoutdata, stderrdata = p.communicate()
        # TODO ReferenceError: Can't find variable: key
        # if stdoutdata:
        #    print(r'pipelines.py  _excute_cmd_and_return_code stdoutdata={}'.format(stdoutdata))
        # if stderrdata:
        #    print(r'pipelines.py _excute_cmd_and_return_code stderrdata={}'.format(stderrdata))

        return p.returncode

    def screenshot(self, url, path):
        cmd = 'timeout 60 /usr/bin/phantomjs /usr/sbin/aio/box_dashboard/web_guard/sitespider/rasterize.js {} {}'.format(
            url, path)
        print('pipelines.py screenshot cmd={}'.format(cmd))
        self._excute_cmd_and_return_code(cmd)

    def SavePage(self, url, resourceType, body, screenshot):
        if resourceType == 'url':
            filepath = self._myurl2pathname(url + '_.html')
        else:
            filepath = self._myurl2pathname(url)
        strinfo = re.compile('\?')
        filepath = strinfo.sub('_', filepath)
        strinfo = re.compile(':')
        filepath = strinfo.sub('_', filepath)
        self._mkdir(os.path.dirname(filepath))

        try:
            if os.path.splitext(filepath)[1] == '':
                filepath = filepath + '_.' + resourceType
        except Exception as e:
            filepath = filepath + '_.' + resourceType

        try:
            f = open(filepath, "wb")
        except IOError:
            # 如果文件名是目录，会到这里
            filepath = filepath + '_.' + resourceType
            self._mkdir(os.path.dirname(filepath))
            f = open(filepath, "wb")
        f.write(body)
        f.close()

        if screenshot and resourceType == 'url':
            screenshot_path = filepath.replace('_.html', '_screenshot.jpg')
            self.screenshot(url, screenshot_path)

        return (self._GetFileMd5(filepath), filepath)


class CSiteDB():
    site_id = 0

    def InitSite(self, name, url, strSavePath):
        mSite = CModel_site()
        site_id = mSite.get_site_id(name)
        if site_id:
            self.site_id = site_id
            CModel_pagetitle().delBySiteid(site_id)
            CModel_webpage().delBySiteid(site_id)
            CModel_site_webpage().delBySiteid(site_id)
            path = CModel_site().get_site_path(site_id)
            if path and len(path) > 5:
                try:
                    shutil.rmtree(path)
                except Exception as e:
                    pass
            mSite.updateByName(name, url, 1, strSavePath)
        else:
            mSite.addSite(name, url, 1, strSavePath)
            self.site_id = mSite.get_site_id(name)

    def IsWebPageExsit(self, link):
        mSiteWebpage = CModel_site_webpage()
        if mSiteWebpage.get_page_id(self.site_id, link) > 0:
            return True
        return False

    def insert_webpage(self, item, strSavePath, screenshot):
        if self.IsWebPageExsit(item['link']):
            return True
        link = item['link']
        resourceType = item['type']
        body = item['body']
        depth = item['depth']
        reference = item['reference']
        savePage = CSavePage(strSavePath)
        ret = savePage.SavePage(link, resourceType, body, screenshot)
        md5 = ret[0]
        if SPIDER_PLATFORM_SYSTEM == 'linux':
            path = ret[1]
        else:
            path = ret[1].decode('gb2312').encode('utf-8')
        mWebpage = CModel_webpage()
        mWebpage.addWebPage(link, path.decode('utf-8'), md5, resourceType, depth, reference)
        webpage_id = mWebpage.get_webpage_id(link, path.decode('utf-8'), md5, resourceType)
        if webpage_id:
            CModel_site_webpage().addSite_Webpage(self.site_id, webpage_id)
            if resourceType == 'url':
                CModel_pagetitle().addPagetitle(webpage_id, item['title'].decode('utf-8'))
        return True

    def UpdateSiteStatus(self, siteStatus):
        CModel_site().update_siteStatus(self.site_id, siteStatus)


class SitespiderPipeline(object):
    siteDB = CSiteDB()

    def open_spider(self, spider):
        for url in spider.start_urls:
            self.siteDB.InitSite(spider.sitename, url, spider.strSavePath)

    def close_spider(self, spider):
        self.siteDB.UpdateSiteStatus(0)

    def process_item(self, item, spider):
        if 'link' in item:
            self.siteDB.insert_webpage(item, spider.strSavePath, spider.screenshot)
            return item

        raise DropItem("Missing link in %s" % item)
