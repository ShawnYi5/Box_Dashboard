# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('xdashboard', '0007_auto_20160908_1923'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='safeset',
            field=models.TextField(blank=True, default='3,10'),
        ),
    ]
