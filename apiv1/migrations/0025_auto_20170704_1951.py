# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0024_auto_20170629_1641'),
    ]

    operations = [
        migrations.AddField(
            model_name='htbschedule',
            name='dst_host_ident',
            field=models.CharField(max_length=32, default=''),
        ),
        migrations.AddField(
            model_name='htbschedule',
            name='restore_type',
            field=models.PositiveSmallIntegerField(choices=[(1, '系统还原'), (2, '卷还原')], default=1),
        ),
    ]
