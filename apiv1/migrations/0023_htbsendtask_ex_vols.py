# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0022_spacecollectiontask_cluster_schedule'),
    ]

    operations = [
        migrations.AddField(
            model_name='htbsendtask',
            name='ex_vols',
            field=models.TextField(default='[]'),
        ),
    ]
