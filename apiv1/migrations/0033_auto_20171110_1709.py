# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
import apiv1.fields


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0032_auto_20171102_2112'),
    ]

    operations = [
        migrations.AddField(
            model_name='remotebackupschedule',
            name='last_run_date',
            field=models.DateTimeField(default=None, blank=True, null=True),
        ),
        migrations.AddField(
            model_name='remotebackupschedule',
            name='next_run_date',
            field=models.DateTimeField(default=None, blank=True, null=True),
        ),
        migrations.AddField(
            model_name='remotebackupsubtask',
            name='remote_timestamp',
            field=apiv1.fields.MyDecimalField(default=-1, blank=True, null=True, max_digits=16, decimal_places=6),
        ),
    ]
