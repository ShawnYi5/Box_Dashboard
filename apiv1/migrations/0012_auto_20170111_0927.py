# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0011_host_network_transmission_type'),
    ]

    operations = [
        migrations.AlterField(
            model_name='migratetask',
            name='ext_config',
            field=models.TextField(default='{}'),
        ),
    ]
