# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('web_guard', '0003_auto_20170419_2003'),
    ]

    operations = [
        migrations.AlterField(
            model_name='alarmmethod',
            name='exc_info',
            field=models.TextField(default='{"high": {"phone": {"frequency": 10, "is_use": true, "item_list": []}, "email": {"frequency": 10, "is_use": true, "item_list": []}, "sms": {"frequency": 10, "is_use": true, "item_list": []}}, "low": {"phone": {"frequency": 60, "is_use": true, "item_list": []}, "email": {"frequency": 60, "is_use": true, "item_list": []}, "sms": {"frequency": 60, "is_use": true, "item_list": []}}, "middle": {"phone": {"frequency": 30, "is_use": true, "item_list": []}, "email": {"frequency": 30, "is_use": true, "item_list": []}, "sms": {"frequency": 30, "is_use": true, "item_list": []}}}'),
        ),
        migrations.AlterField(
            model_name='webguardstrategy',
            name='present_status',
            field=models.IntegerField(default=0, choices=[(0, '--'), (1, '正常'), (2, '分析中'), (3, '发现篡改风险'), (4, '已切换为应急页面')]),
        ),
    ]
