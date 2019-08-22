# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0014_auto_20170120_1743'),
    ]

    operations = [
        migrations.AddField(
            model_name='disksnapshot',
            name='inc_date_bytes',
            field=models.BigIntegerField(default=-1),
        ),
    ]
