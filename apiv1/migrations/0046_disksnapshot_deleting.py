# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0045_disksnapshot_ext_info'),
    ]

    operations = [
        migrations.AddField(
            model_name='disksnapshot',
            name='deleting',
            field=models.BooleanField(default=False),
        ),
    ]
