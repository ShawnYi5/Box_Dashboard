# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0029_auto_20170914_2008'),
    ]

    operations = [
        migrations.AlterField(
            model_name='remotebackupsubtask',
            name='remote_snapshot_ident',
            field=models.CharField(max_length=32),
        ),
    ]
