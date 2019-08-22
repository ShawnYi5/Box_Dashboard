# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0010_auto_20161125_0947'),
    ]

    operations = [
        migrations.AddField(
            model_name='host',
            name='network_transmission_type',
            field=models.IntegerField(default=2),
        ),
    ]
