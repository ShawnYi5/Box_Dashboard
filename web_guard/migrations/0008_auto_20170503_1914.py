# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('web_guard', '0007_auto_20170503_1911'),
    ]

    operations = [
        migrations.AlterField(
            model_name='alarmmethod',
            name='exc_info',
            field=models.TextField(default='{"low": {"phone": {"is_use": true, "item_list": [], "frequency": 60}, "email": {"is_use": true, "item_list": [], "frequency": 60}, "sms": {"is_use": true, "item_list": [], "frequency": 60}}, "middle": {"phone": {"is_use": true, "item_list": [], "frequency": 30}, "email": {"is_use": true, "item_list": [], "frequency": 30}, "sms": {"is_use": true, "item_list": [], "frequency": 30}}, "high": {"phone": {"is_use": true, "item_list": [], "frequency": 10}, "email": {"is_use": true, "item_list": [], "frequency": 10}, "sms": {"is_use": true, "item_list": [], "frequency": 10}}}'),
        ),
    ]
