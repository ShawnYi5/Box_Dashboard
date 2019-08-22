# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0016_auto_20170518_0930'),
    ]

    operations = [
        migrations.AddField(
            model_name='htbschedule',
            name='in_stand_by',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='htbschedule',
            name='task_uuid',
            field=models.CharField(max_length=32, default=''),
        ),
    ]
