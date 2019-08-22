# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0026_auto_20170714_1144'),
    ]

    operations = [
        migrations.CreateModel(
            name='TakeOverKVM',
            fields=[
                ('id', models.AutoField(auto_created=True, serialize=False, primary_key=True, verbose_name='ID')),
                ('name', models.CharField(default='', max_length=256)),
                ('kvm_type', models.CharField(default='', max_length=20)),
                ('snapshot_time', models.DateTimeField(null=True, blank=True)),
                ('kvm_cpu_count', models.IntegerField(default=0)),
                ('kvm_memory_size', models.IntegerField(default=0)),
                ('kvm_memory_unit', models.CharField(default='', max_length=5)),
                ('kvm_run_start_time', models.DateTimeField(null=True, blank=True)),
                ('kvm_flag_file', models.CharField(default='', max_length=256)),
                ('ext_info', models.TextField(default='[]')),
                ('host_snapshot', models.ForeignKey(to='apiv1.HostSnapshot', null=True, related_name='takeover_host_snapshot', on_delete=django.db.models.deletion.PROTECT)),
            ],
        ),
    ]
