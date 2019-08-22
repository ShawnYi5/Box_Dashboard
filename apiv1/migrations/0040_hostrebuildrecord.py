# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0039_restoretargetdisk_hash_path'),
    ]

    operations = [
        migrations.CreateModel(
            name='HostRebuildRecord',
            fields=[
                ('id', models.AutoField(serialize=False, verbose_name='ID', primary_key=True, auto_created=True)),
                ('host_ident', models.CharField(max_length=32, unique=True)),
                ('rebuild_count', models.IntegerField(default=0)),
            ],
        ),
    ]
