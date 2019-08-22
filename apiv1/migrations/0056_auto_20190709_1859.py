# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0055_auto_20190702_1956'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='filesyncschedule',
            name='source_host_ident',
        ),
        migrations.AddField(
            model_name='filesyncschedule',
            name='host',
            field=models.ForeignKey(blank=True, null=True, related_name='file_sync_schedules', on_delete=django.db.models.deletion.PROTECT, to='apiv1.Host'),
        ),
        migrations.AddField(
            model_name='filesynctask',
            name='snapshot_datetime',
            field=models.DateTimeField(null=True, blank=True, default=None),
        ),
    ]
