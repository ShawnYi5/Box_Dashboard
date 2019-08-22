# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('xdashboard', '0021_userprofile_user_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='everydaycycle',
            name='unit',
            field=models.CharField(default='day', max_length=256),
        ),
    ]
