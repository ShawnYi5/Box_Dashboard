# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0012_auto_20170111_0927'),
    ]

    operations = [
        migrations.CreateModel(
            name='CompressTask',
            fields=[
                ('id', models.AutoField(serialize=False, verbose_name='ID', auto_created=True, primary_key=True)),
                ('create_datetime', models.DateTimeField(auto_now_add=True)),
                ('completed', models.NullBooleanField(default=False)),
                ('total_lines', models.PositiveIntegerField(default=0)),
                ('next_start_lines', models.PositiveIntegerField(default=0)),
                ('next_start_date', models.DateTimeField(auto_now_add=True)),
                ('exe_info', models.TextField(default='{}')),
                ('disk_snapshot', models.ForeignKey(to='apiv1.DiskSnapshot', related_name='compress_tasks')),
            ],
        ),
    ]
