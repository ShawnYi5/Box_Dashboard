# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0004_restoretarget_display_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='hostsnapshotshare',
            name='share_start_time',
            field=models.DateTimeField(blank=True, default=None, null=True),
        ),
    ]
