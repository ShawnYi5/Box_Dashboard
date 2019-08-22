# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0015_disksnapshot_inc_date_bytes'),
        ('web_guard', '0004_auto_20170420_0859'),
    ]

    operations = [
        migrations.CreateModel(
            name='HostMaintainConfig',
            fields=[
                ('id', models.AutoField(auto_created=True, verbose_name='ID', primary_key=True, serialize=False)),
                ('config', models.TextField(default='{}')),
                ('cache', models.TextField(default='{}')),
                ('host', models.OneToOneField(related_name='maintain_config', to='apiv1.Host')),
            ],
        ),
        migrations.AlterField(
            model_name='alarmmethod',
            name='exc_info',
            field=models.TextField(default='{"low": {"phone": {"frequency": 60, "item_list": [], "is_use": true}, "sms": {"frequency": 60, "item_list": [], "is_use": true}, "email": {"frequency": 60, "item_list": [], "is_use": true}}, "middle": {"phone": {"frequency": 30, "item_list": [], "is_use": true}, "sms": {"frequency": 30, "item_list": [], "is_use": true}, "email": {"frequency": 30, "item_list": [], "is_use": true}}, "high": {"phone": {"frequency": 10, "item_list": [], "is_use": true}, "sms": {"frequency": 10, "item_list": [], "is_use": true}, "email": {"frequency": 10, "item_list": [], "is_use": true}}}'),
        ),
    ]
