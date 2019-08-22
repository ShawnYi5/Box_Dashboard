# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('web_guard', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='alarmmethod',
            name='exc_info',
            field=models.TextField(default='{"middle": {"sms": {"item_list": [], "is_use": true, "frequency": 30}, "email": {"item_list": [], "is_use": true, "frequency": 30}, "phone": {"item_list": [], "is_use": true, "frequency": 30}}, "high": {"sms": {"item_list": [], "is_use": true, "frequency": 10}, "email": {"item_list": [], "is_use": true, "frequency": 10}, "phone": {"item_list": [], "is_use": true, "frequency": 10}}, "low": {"sms": {"item_list": [], "is_use": true, "frequency": 60}, "email": {"item_list": [], "is_use": true, "frequency": 60}, "phone": {"item_list": [], "is_use": true, "frequency": 60}}}'),
        ),
    ]
