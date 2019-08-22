# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0038_auto_20180323_1015'),
    ]

    operations = [
        migrations.AddField(
            model_name='restoretargetdisk',
            name='hash_path',
            field=models.CharField(max_length=256, default=''),
        ),
    ]
