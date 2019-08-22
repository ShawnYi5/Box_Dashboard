# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('xdashboard', '0019_auto_20170222_1107'),
    ]

    operations = [
        migrations.AddField(
            model_name='taskpolicy',
            name='isdup',
            field=models.BooleanField(default=True),
        ),
    ]
