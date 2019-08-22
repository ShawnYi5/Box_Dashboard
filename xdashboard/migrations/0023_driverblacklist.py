# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('xdashboard', '0022_everydaycycle_unit'),
    ]

    operations = [
        migrations.CreateModel(
            name='DriverBlackList',
            fields=[
                ('id', models.AutoField(serialize=False, verbose_name='ID', primary_key=True, auto_created=True)),
                ('device_id', models.CharField(max_length=256)),
                ('driver_id', models.CharField(max_length=256)),
                ('sys_type', models.CharField(max_length=256)),
            ],
        ),
    ]
