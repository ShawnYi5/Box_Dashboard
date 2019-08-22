# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('xdashboard', '0004_auto_20160819_1514'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='deleted',
            field=models.BooleanField(default=False),
        ),
    ]
