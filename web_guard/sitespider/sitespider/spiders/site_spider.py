# coding=utf-8
import scrapy, os, hashlib, re, json, sys
from sitespider.items import URLItem, JSItem, ImageItem, CSSItem, FlashItem
from scrapy.exceptions import CloseSpider, NotSupported


class SiteSpider(scrapy.Spider):
    name = "site"
    allowed_domains = list()
    start_urls = list()
    exclude_urls = list()
    # 网页保存路径
    strSavePath = ''
    sitename = 'none'
    dbpath = ''
    depth_limit_start = 1
    depth_limit_end = 1

    def __init__(self, paramPath=None, *args, **kwargs):
        super(SiteSpider, self).__init__(*args, **kwargs)
        f = open(paramPath, 'r')
        param = json.loads(f.read())
        f.close()
        self.strSavePath = param['path']
        self.sitename = param['crawlname']
        for url in param['policy']['target_urls']:
            self.start_urls.append(url)
        self.dbpath = param['dbpath']

        self.screenshot = 1
        if 'screenshot' in param['policy']:
            self.screenshot = int(param['policy']['screenshot'])

        if 'target_domains' in param['policy']:
            for domain in param['policy']['target_domains']:
                self.allowed_domains.append(domain)

        if 'exclude_urls' in param['policy']:
            for exclude_url in param['policy']['exclude_urls']:
                self.exclude_urls.append(exclude_url)

        self.strSavePath = os.path.join(self.strSavePath, self.sitename)

        if 'level_url' in param['policy']:
            self.depth_limit_start = int(param['policy']['level_url']['start'])
            self.depth_limit_end = int(param['policy']['level_url']['end'])
        else:
            self.depth_limit_start = 1
            self.depth_limit_end = 1

    def _MD5(self, src):
        m2 = hashlib.md5()
        m2.update(src.encode('utf-8'))
        return m2.hexdigest()

    def parse_css(self, response):
        depth = response.meta['depth']
        if 'reference' in response.meta:
            reference = response.meta['reference']
        else:
            reference = ''
        item = CSSItem()
        item['link'] = response.url
        item['type'] = 'css'
        item['body'] = response.body
        item['depth'] = depth
        item['reference'] = reference
        yield item

        p1 = r"url\(\'(.+?)\'\)"
        pattern1 = re.compile(p1)
        cssurls = pattern1.findall(response.body)
        for url in cssurls:
            img_url = response.urljoin(url)
            yield scrapy.Request(img_url, callback=self.parse_img, meta={'reference': reference})

        p1 = r'import url\("(.+?)"\)'
        pattern1 = re.compile(p1)
        cssurls = pattern1.findall(response.body)
        for url in cssurls:
            img_url = response.urljoin(url)
            yield scrapy.Request(img_url, callback=self.parse_css, meta={'reference': reference})

        p1 = r"background-image:url\(\'*(.+?)\'*\)"
        pattern1 = re.compile(p1)
        cssurls = pattern1.findall(response.body)
        for url in cssurls:
            img_url = response.urljoin(url)
            yield scrapy.Request(img_url, callback=self.parse_img, meta={'reference': reference})

    def parse_js(self, response):
        depth = response.meta['depth']
        if 'reference' in response.meta:
            reference = response.meta['reference']
        else:
            reference = ''
        item = JSItem()
        item['link'] = response.url
        item['type'] = 'js'
        item['body'] = response.body
        item['depth'] = depth
        item['reference'] = reference
        yield item

    def parse_img(self, response):
        depth = response.meta['depth']
        if 'reference' in response.meta:
            reference = response.meta['reference']
        else:
            reference = ''
        item = ImageItem()
        item['link'] = response.url
        item['type'] = 'image'
        item['body'] = response.body
        item['depth'] = depth
        item['reference'] = reference
        yield item

    def parse_flash(self, response):
        depth = response.meta['depth']
        if 'reference' in response.meta:
            reference = response.meta['reference']
        else:
            reference = ''
        item = FlashItem()
        item['link'] = response.url
        item['type'] = 'flash'
        item['body'] = response.body
        item['depth'] = depth
        item['reference'] = reference
        yield item

    def parse(self, response):
        depth = response.meta['depth']
        if 'reference' in response.meta:
            reference = response.meta['reference']
        else:
            reference = ''
        if depth + 1 > self.depth_limit_end:
            return
        stopfile = os.path.join(self.strSavePath, 'stop')
        if os.path.isfile(stopfile):
            raise CloseSpider('user cancel')
        item = URLItem()
        item['link'] = response.url
        item['depth'] = depth
        try:
            item['title'] = response.xpath('/html/head/title/text()').extract()
        except NotSupported as e:
            print >> sys.stderr, 'parse NotSupported {},url={}'.format(str(e), response.url)
            return

        if len(item['title']) > 0:
            item['title'] = item['title'][0].encode('utf-8')
        else:
            item['title'] = 'none'
        item['type'] = 'url'
        item['body'] = response.body
        item['reference'] = reference
        yield item

        if self.depth_limit_start <= depth + 1:
            for href in response.css("script::attr('src')"):
                url = response.urljoin(href.extract())
                yield scrapy.Request(url, callback=self.parse_js, meta={'reference': response.url})

            for href in response.css("img::attr('src')"):
                url = response.urljoin(href.extract())
                yield scrapy.Request(url, callback=self.parse_img, meta={'reference': response.url})

            for href in response.css("link::attr('href')"):
                url = response.urljoin(href.extract())
                yield scrapy.Request(url, callback=self.parse_css, meta={'reference': response.url})

            p1 = r"url\(\'(.+?)\'\)"
            pattern1 = re.compile(p1)
            cssurls = pattern1.findall(response.body)
            for url in cssurls:
                img_url = response.urljoin(url)
                yield scrapy.Request(img_url, callback=self.parse_img, meta={'reference': response.url})

        for href in response.css("embed::attr('src')"):
            url = response.urljoin(href.extract())
            yield scrapy.Request(url, callback=self.parse_flash, meta={'reference': response.url})

        for href in response.xpath('//param[contains(@name,"movie")]/@value'):
            url = response.urljoin(href.extract())
            yield scrapy.Request(url, callback=self.parse_flash, meta={'reference': response.url})

        for href in response.css("a::attr('href')"):
            url = response.urljoin(href.extract())
            ballowed = False

            if len(self.allowed_domains) == 0:
                ballowed = True

            for allowed in self.allowed_domains:
                if allowed in url:
                    ballowed = True
                    break

            for exclude_url in self.exclude_urls:
                if exclude_url in url:
                    ballowed = False
                    break

            if ballowed:
                pattern = re.compile(r'^mailto:')
                match = pattern.match(url)
                if not match:
                    yield scrapy.Request(url, callback=self.parse, meta={'reference': response.url})
