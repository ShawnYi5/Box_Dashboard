#!/usr/bin/env python3
# coding=utf-8
try:
    from .spider import CSiteSpiderInterface
    from .matcher import CSensitiveWordsInterface
except Exception as e:
    from spider import CSiteSpiderInterface
    from matcher import CSensitiveWordsInterface

import time


class CSiteSpiderAPI():
    '''爬虫API'''

    def __init__(self):
        pass

    # crawlname 任务名，传入已存在的任务名，会先删除，再
    # policy（json）中的项target_urls,exclude_urls,level_url,domain（爬取时域名中必须包含的项，用于防止爬取外链）
    # path存储路径
    # browser 1.IE 2.Chrome 3.Firefox
    # callback crawlcallback(name, status, last_crawl ,ret) 爬取过程中调用。callback返回0成功，返回1取消当前任务
    # crawlcallback只返回最新的一个url及总数量，中间会漏掉一些url
    # status 0完成，1正在爬取
    # last_crawl返回示例如下：
    # {"url":"http://www.xxx.com/1.html","count":10}
    # 返回值：(code,err_string)0成功，非0失败
    def crawl_site(self, crawlname, path, policy, browser, callback):
        '''爬取站点'''
        return CSiteSpiderInterface().crawl_site(crawlname, path, policy, browser, callback)

    # 删除不存在的站点返回1
    # 返回值：(code,err_string)0成功，非0失败
    def del_site(self, crawlname):
        '''删除已爬取站点'''
        return CSiteSpiderInterface().del_site(crawlname)

    # crawlname_base 安全爬取点
    # crawlname_current 当前爬取的点
    # callback comparecallback(comparename,compare_result, ret,err_string)比较完成后调用。ret为0，成功，否则失败.callback返回0成功，返回1取消当前任务
    # 比较相同的项，不返回
    # compare_result返回示例如下：
    # {"type":content-tamper或frameworks-tamper,"url":"http://www.xxx.com/1.html","path_base":"/var/1/1.html","path_current":"/var/2/1.htm","diffcount":10,"delcount":2,"insertcount":5,"replacecout":3,"charactercount":300}
    # 解释：http://www.xxx.com/1.html网页，总变化数10处，其中删除2处，新增5处，替换3处 charactercount:变化的字符数
    # {"type":pictures-tamper或resources-tamper,"url":"http://www.xxx.com/2.gif","filechange":True}
    # 解释：非文本类型文件，发生了变化
    # {"type":3,"url":"http://www.xxx.com/2.html","newpage":True}
    # 解释：新增加了页面http://www.xxx.com/2.html
    # {"type":4,"url":"http://www.xxx.com/3.html","delpage":True}
    # 解释：减少了页面http://www.xxx.com/2.html
    # {"type":"finish","code":0,"err_string":"SUCCESS"}
    # 解释：完成所有比较
    # 返回值：(code,err_string)0成功，非0失败
    def compare_site(self, comparename, crawlname_base, crawlname_current, inspect_item, callback):
        '''比较站点'''
        return CSiteSpiderInterface().compare_site(comparename, crawlname_base, crawlname_current, inspect_item,
                                                   callback)

    def countWebsitePages(self, sitename):
        return CSiteSpiderInterface().countWebsitePages(sitename)

    def getWebsitePage(self, sitename, limit, offset, filter):
        return CSiteSpiderInterface().getWebsitePage(sitename, limit, offset, filter)

    def get_page_title(self, webpage_id):
        return CSiteSpiderInterface().get_page_title(webpage_id)


class CSensitiveWordsAPI():
    '''敏感词API'''

    def __init__(self):
        pass

    # crawlname 同crawlsite函数的参数crawlname
    # word_count，当一个页面检测到几个敏感词时返回，-1为不限制
    # callback sensitive_word_test_callback(crawlname,sensitive_word_result, ret,err_string)比较完成后调用。ret为0，成功，否则失败.callback返回0成功，返回1取消当前任务
    # sensitive_word_result返回示例如下：
    # {"url": "http://www.xxx.com/1.html", "words": [],"total":100,"current":20}
    # 返回值：(code,err_string)0成功，非0失败
    def sensitive_word_test(self, crawlname, word_count, callback):
        '''敏感词检测'''
        return CSensitiveWordsInterface().sensitive_word_test(crawlname, word_count, callback)

    # 返回值：count
    def get_sensitive_word_count(self):
        '''查询敏感词总数量'''
        return CSensitiveWordsInterface().get_sensitive_word_count()

    # 返回值：list
    # for word in words:
    #    print(word.word)
    def get_sensitive_word_list(self, filter, limit, offset):
        '''得到敏感词列表'''
        return CSensitiveWordsInterface().get_sensitive_word_list(filter, limit, offset)

    # 返回值：bool
    def add_sensitive_word(self, word):
        '''增加敏感词'''
        return CSensitiveWordsInterface().add_sensitive_word(word)

    # 返回值：bool
    def del_sensitive_word(self, word):
        '''删除敏感词'''
        return CSensitiveWordsInterface().del_sensitive_word(word)

    def del_all_sensitive_word(self):
        return CSensitiveWordsInterface().del_all_sensitive_word()

    # libdatrie中不允许一个词为另一个词的前缀，对这种词要作标记，不能用于生成dict.bin
    # 例如：重庆新闻 重庆 这2个词都存在时，需要删除（标记）重庆新闻
    def sign_dirty_words(self):
        return CSensitiveWordsInterface().sign_dirty_words()

    # 返回值：(code,err_string)0成功，非0失败
    def gen_sensitive_word_bin(self):
        '''重新生成敏感词库二进制文件'''
        return CSensitiveWordsInterface().gen_sensitive_word_bin()


if __name__ == "__main__":
    def crawlcallback(name, status, last_crawl, ret, err_string):
        if status == 1:
            print("crawlcallback name={} 已爬取{},共爬取了{}资源".format(name, last_crawl["url"], last_crawl["count"]))
        elif status == 0:
            if ret == 0:
                print("crawlcallback name={} 已爬取成功,已爬取{},共爬取了{}资源".format(name, last_crawl["url"], last_crawl["count"]))
            else:
                print(
                    "crawlcallback name={} 已爬取完成，但出现错误,共爬取了{}资源,ret={},err_string={}".format(name, last_crawl["count"],
                                                                                             ret,
                                                                                             err_string))
        else:
            print("crawlcallback name={},status={}".format(name, status))

        # return 1#用户取消
        return 0


    site_spider_api = CSiteSpiderAPI()
    crawlname_base = '20170331'
    crawlname_current = '20170401'
    bcrawl = False
    if bcrawl:
        path = '/home/tmp/web'
        policy = {"target_urls": ["http://172.16.1.10:8081/"],
                  # "exclude_urls": [],
                  # "level_url": {"start": "1","end": "3"},
                  # "target_domains": ["changhai.org", "zhangyang.org"]
                  }
        browser = 1
        ret, err_string = site_spider_api.crawl_site(crawlname_base, path, policy, browser, crawlcallback)
        if ret != 0:
            print(err_string)

        print('wait')
        time.sleep(10)
        print('ok')

        browser = 1
        ret, err_string = site_spider_api.crawl_site(crawlname_current, path, policy, browser, crawlcallback)
        if ret != 0:
            print(err_string)


    def comparecallback(comparename, compare_result, ret, err_string):
        if ret != 0:
            print(err_string)
            return 0

        print("comparecallback comparename={},compare_result={}".format(comparename, compare_result))

        # return 1#用户取消
        return 0


    bcompare = True
    if bcompare:
        inspect_item = ["content-tamper", "pictures-tamper", "resources-tamper", "links-tamper", "frameworks-tamper"]
        ret, err_string = site_spider_api.compare_site('比较', crawlname_base, crawlname_current, inspect_item,
                                                       comparecallback)
        if ret != 0:
            print(err_string)

    bsensitive = False
    if bsensitive:
        def sensitive_word_test_callback(crawlname, sensitive_word_result, ret, err_string):
            if ret != 0:
                print(err_string)
                return 0

            print('sensitive_word_test_callback crawlname={},sensitive_word_result={}'.format(crawlname,
                                                                                              sensitive_word_result))
            return 0


        CSensitiveWordsAPI().add_sensitive_word('hello')
        count = CSensitiveWordsAPI().get_sensitive_word_count()
        print('get_sensitive_word_count={}'.format(count))
        # filter = {"word": "模糊匹配单词"}
        filter = None
        words = CSensitiveWordsAPI().get_sensitive_word_list(filter, 30, 0)
        for word in words:
            print(word.word)

        code, err_string = CSensitiveWordsAPI().gen_sensitive_word_bin()
        if code != 0:
            print(err_string)

        CSensitiveWordsAPI().sensitive_word_test(crawlname_base, -1, sensitive_word_test_callback)

    bdel = False
    if bdel:
        ret, err_string = site_spider_api.del_site(crawlname_base)
        if ret != 0:
            print(err_string)

        ret, err_string = site_spider_api.del_site(crawlname_current)
        if ret != 0:
            print(err_string)
