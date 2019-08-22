# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0044_restoretarget_user'),
    ]

    operations = [
        migrations.AddField(
            model_name='disksnapshot',
            name='ext_info',
            field=models.TextField(default='{}'),
        ),
    ]
