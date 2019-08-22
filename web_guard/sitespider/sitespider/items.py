# -*- coding: utf-8 -*-

# Define here the models for your scraped items
#
# See documentation in:
# http://doc.scrapy.org/en/latest/topics/items.html

import scrapy


class URLItem(scrapy.Item):
    # define the fields for your item here like:
    title = scrapy.Field()
    link = scrapy.Field()
    type = scrapy.Field()
    body = scrapy.Field()
    depth = scrapy.Field()
    reference = scrapy.Field()


class JSItem(scrapy.Item):
    # define the fields for your item here like:
    link = scrapy.Field()
    type = scrapy.Field()
    body = scrapy.Field()
    depth = scrapy.Field()
    reference = scrapy.Field()


class ImageItem(scrapy.Item):
    # define the fields for your item here like:
    link = scrapy.Field()
    type = scrapy.Field()
    body = scrapy.Field()
    depth = scrapy.Field()
    reference = scrapy.Field()


class CSSItem(scrapy.Item):
    # define the fields for your item here like:
    link = scrapy.Field()
    type = scrapy.Field()
    body = scrapy.Field()
    depth = scrapy.Field()
    reference = scrapy.Field()


class FlashItem(scrapy.Item):
    # define the fields for your item here like:
    link = scrapy.Field()
    type = scrapy.Field()
    body = scrapy.Field()
    depth = scrapy.Field()
    reference = scrapy.Field()
