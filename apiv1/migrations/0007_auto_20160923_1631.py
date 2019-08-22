# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0006_tunnel'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='tunnel',
            unique_together=set([('host_ip', 'host_port')]),
        ),
    ]
