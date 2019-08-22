# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('web_guard', '0008_auto_20170503_1914'),
    ]

    operations = [
        migrations.AlterField(
            model_name='alarmevent',
            name='strategy_sub_type',
            field=models.IntegerField(choices=[(11, '首页内容篡改检测'), (12, '首页敏感词检测'), (13, '首页图片篡改'), (14, '首页下载资源篡改'), (15, '首页链接篡改'), (16, '首页框架篡改'), (101, '网页内容篡改检测'), (102, '网页敏感词检测'), (103, '网页图片篡改'), (104, '网页下载资源篡改'), (105, '网页链接篡改'), (106, '网页框架篡改'), (201, '文件篡改检测')]),
        ),
        migrations.AlterField(
            model_name='alarmeventlog',
            name='strategy_sub_type',
            field=models.IntegerField(choices=[(11, '首页内容篡改检测'), (12, '首页敏感词检测'), (13, '首页图片篡改'), (14, '首页下载资源篡改'), (15, '首页链接篡改'), (16, '首页框架篡改'), (101, '网页内容篡改检测'), (102, '网页敏感词检测'), (103, '网页图片篡改'), (104, '网页下载资源篡改'), (105, '网页链接篡改'), (106, '网页框架篡改'), (201, '文件篡改检测')]),
        ),
        migrations.AlterField(
            model_name='alarmmethod',
            name='exc_info',
            field=models.TextField(default='{"middle": {"phone": {"is_use": true, "item_list": [], "frequency": 30}, "sms": {"is_use": true, "item_list": [], "frequency": 30}, "email": {"is_use": true, "item_list": [], "frequency": 30}}, "low": {"phone": {"is_use": true, "item_list": [], "frequency": 60}, "sms": {"is_use": true, "item_list": [], "frequency": 60}, "email": {"is_use": true, "item_list": [], "frequency": 60}}, "high": {"phone": {"is_use": true, "item_list": [], "frequency": 10}, "sms": {"is_use": true, "item_list": [], "frequency": 10}, "email": {"is_use": true, "item_list": [], "frequency": 10}}}'),
        ),
    ]
