# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('xdashboard', '0018_taskpolicy_backupmode'),
    ]

    operations = [
        migrations.AddField(
            model_name='storagenodespace',
            name='raw_data_bytes',
            field=models.BigIntegerField(default=0),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='userquotaspace',
            name='raw_data_bytes',
            field=models.BigIntegerField(default=0),
        ),
    ]
