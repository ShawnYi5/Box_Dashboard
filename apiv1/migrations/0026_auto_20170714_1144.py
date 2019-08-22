# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
import apiv1.fields


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0025_auto_20170704_1951'),
    ]

    operations = [
        migrations.AlterField(
            model_name='disksnapshot',
            name='parent_timestamp',
            field=apiv1.fields.MyDecimalField(max_digits=16, blank=True, decimal_places=6, null=True, default=None),
        ),
        migrations.AlterField(
            model_name='disksnapshotcdp',
            name='first_timestamp',
            field=apiv1.fields.MyDecimalField(max_digits=16, decimal_places=6),
        ),
        migrations.AlterField(
            model_name='disksnapshotcdp',
            name='last_timestamp',
            field=apiv1.fields.MyDecimalField(max_digits=16, blank=True, decimal_places=6, null=True, default=None),
        ),
        migrations.AlterField(
            model_name='restoretargetdisk',
            name='snapshot_timestamp',
            field=apiv1.fields.MyDecimalField(max_digits=16, blank=True, decimal_places=6, null=True, default=None),
        ),
    ]
