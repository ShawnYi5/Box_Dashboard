# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0040_hostrebuildrecord'),
    ]

    operations = [
        migrations.AddField(
            model_name='hostrebuildrecord',
            name='mac_list',
            field=models.TextField(default='[]'),
        ),
    ]
