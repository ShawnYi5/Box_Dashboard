# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('xdashboard', '0017_auto_20161125_0947'),
    ]

    operations = [
        migrations.AddField(
            model_name='taskpolicy',
            name='backupmode',
            field=models.IntegerField(default=2),
        ),
    ]
