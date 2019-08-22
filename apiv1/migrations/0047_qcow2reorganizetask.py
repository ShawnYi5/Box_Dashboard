# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0046_disksnapshot_deleting'),
    ]

    operations = [
        migrations.CreateModel(
            name='Qcow2ReorganizeTask',
            fields=[
                ('id', models.AutoField(serialize=False, primary_key=True, verbose_name='ID', auto_created=True)),
                ('create_datetime', models.DateTimeField(auto_now_add=True)),
                ('start_datetime', models.DateTimeField(blank=True, null=True, default=None)),
                ('finish_datetime', models.DateTimeField(blank=True, null=True, default=None)),
                ('successful', models.BooleanField(default=False)),
                ('image_path', models.CharField(max_length=256)),
                ('task_type', models.PositiveIntegerField(choices=[(0, '分片'), (1, '整理')])),
                ('ext_info', models.TextField(default='{}')),
                ('next_start_date', models.DateTimeField(auto_now_add=True)),
            ],
        ),
    ]
