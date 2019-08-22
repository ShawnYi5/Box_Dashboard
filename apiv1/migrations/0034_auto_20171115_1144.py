# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0033_auto_20171110_1709'),
    ]

    operations = [
        migrations.AddField(
            model_name='hostsnapshot',
            name='remote_schedule',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, null=True, related_name='host_snapshots', to='apiv1.RemoteBackupSchedule'),
        ),
        migrations.AddField(
            model_name='spacecollectiontask',
            name='remote_schedule',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, null=True, related_name='space_collection_tasks', to='apiv1.RemoteBackupSchedule'),
        ),
    ]
