# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('apiv1', '0041_hostrebuildrecord_mac_list'),
    ]

    operations = [
        migrations.CreateModel(
            name='VirtualCenterConnection',
            fields=[
                ('id', models.AutoField(serialize=False, auto_created=True, verbose_name='ID', primary_key=True)),
                ('username', models.CharField(max_length=256, default='')),
                ('password', models.CharField(max_length=256, default='')),
                ('address', models.CharField(max_length=256, default='')),
                ('port', models.IntegerField(default=443)),
                ('disable_ssl', models.BooleanField(max_length=256, default=True)),
                ('ext_config', models.TextField(default='{}')),
                ('user', models.ForeignKey(related_name='vcenters', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='VirtualMachineRestoreTask',
            fields=[
                ('id', models.AutoField(serialize=False, auto_created=True, verbose_name='ID', primary_key=True)),
                ('task_uuid', models.CharField(max_length=32)),
                ('start_datetime', models.DateTimeField(null=True, blank=True, default=None)),
                ('finish_datetime', models.DateTimeField(null=True, blank=True, default=None)),
                ('successful', models.BooleanField(default=False)),
                ('running_task', models.TextField(default='{}')),
                ('status', models.PositiveSmallIntegerField(choices=[(0, '初始化参数')], default=0)),
                ('ext_config', models.TextField(default='{}')),
                ('host_snapshot', models.ForeignKey(related_name='vmr_tasks', to='apiv1.HostSnapshot')),
            ],
        ),
        migrations.CreateModel(
            name='VirtualMachineSession',
            fields=[
                ('id', models.AutoField(serialize=False, auto_created=True, verbose_name='ID', primary_key=True)),
                ('ident', models.CharField(unique=True, max_length=256)),
                ('name', models.CharField(max_length=256, default='')),
                ('enable', models.BooleanField(default=True)),
                ('home_path', models.CharField(max_length=256, default='')),
                ('connection', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='vm_clients', to='apiv1.VirtualCenterConnection')),
            ],
        ),
        migrations.AddField(
            model_name='host',
            name='type',
            field=models.BigIntegerField(choices=[(0, '普通客户端'), (1, '远程客户端'), (2, '免代理客户端')], default=0),
        ),
        migrations.AddField(
            model_name='virtualmachinesession',
            name='host',
            field=models.OneToOneField(related_name='vm_session', blank=True, default=None, null=True, to='apiv1.Host'),
        ),
    ]
