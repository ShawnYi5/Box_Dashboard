# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('xdashboard', '0026_sshkvm'),
    ]

    operations = [
        migrations.AddField(
            model_name='cdpcycle',
            name='starttime',
            field=models.DateTimeField(blank=True, null=True, default=None),
        ),
    ]
