# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0021_auto_20170606_1434'),
    ]

    operations = [
        migrations.AddField(
            model_name='spacecollectiontask',
            name='cluster_schedule',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='space_collection_tasks', to='apiv1.ClusterBackupSchedule', null=True),
        ),
    ]
