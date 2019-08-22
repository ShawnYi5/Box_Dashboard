# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0030_auto_20170921_1124'),
    ]

    operations = [
        migrations.AddField(
            model_name='remotebackupsubtask',
            name='remote_snapshot_path',
            field=models.CharField(max_length=256, default='invalid'),
        ),
    ]
