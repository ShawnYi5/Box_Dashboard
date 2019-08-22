# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0018_auto_20170601_1423'),
    ]

    operations = [
        migrations.AlterField(
            model_name='htbschedule',
            name='restore_target',
            field=models.ForeignKey(related_name='htb_schedule', to='apiv1.RestoreTarget'),
        ),
    ]
