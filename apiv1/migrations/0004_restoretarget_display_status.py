# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0003_auto_20160823_0857'),
    ]

    operations = [
        migrations.AddField(
            model_name='restoretarget',
            name='display_status',
            field=models.TextField(default=''),
        ),
    ]
