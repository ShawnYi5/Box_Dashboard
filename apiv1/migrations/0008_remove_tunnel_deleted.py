# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0007_auto_20160923_1631'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='tunnel',
            name='deleted',
        ),
    ]
