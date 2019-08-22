# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0015_disksnapshot_inc_date_bytes'),
    ]

    operations = [
        migrations.CreateModel(
            name='HTBSchedule',
            fields=[
                ('id', models.AutoField(auto_created=True, verbose_name='ID', serialize=False, primary_key=True)),
                ('task_type', models.PositiveSmallIntegerField(choices=[(0, '还原到特定点'), (1, '还原到最新')], default=0)),
                ('name', models.CharField(max_length=256)),
                ('enabled', models.BooleanField(default=True)),
                ('deleted', models.BooleanField(default=False)),
                ('ext_config', models.TextField(default='{}')),
                ('host', models.ForeignKey(to='apiv1.Host', related_name='htb_schedule')),
                ('restore_target', models.OneToOneField(related_name='htb_schedule', to='apiv1.RestoreTarget')),
            ],
        ),
        migrations.CreateModel(
            name='HTBSendTask',
            fields=[
                ('id', models.AutoField(auto_created=True, verbose_name='ID', serialize=False, primary_key=True)),
                ('disk_token', models.CharField(max_length=32)),
                ('native_guid', models.CharField(max_length=32)),
                ('task_type', models.PositiveSmallIntegerField(choices=[(0, '普通快照文件'), (1, '封闭的CDP'), (2, '没有封闭的CDP')])),
                ('snapshots', models.TextField(default='[]')),
                ('o_completed_trans', models.BooleanField(default=False)),
                ('o_bit_map', models.CharField(max_length=255, default='')),
                ('o_stop_time', models.CharField(max_length=255, default='')),
            ],
        ),
        migrations.CreateModel(
            name='HTBTask',
            fields=[
                ('id', models.AutoField(auto_created=True, verbose_name='ID', serialize=False, primary_key=True)),
                ('task_uuid', models.CharField(max_length=32)),
                ('start_datetime', models.DateTimeField(blank=True, null=True, default=None)),
                ('finish_datetime', models.DateTimeField(blank=True, null=True, default=None)),
                ('successful', models.BooleanField(default=False)),
                ('running_task', models.TextField(default='{}')),
                ('status', models.PositiveSmallIntegerField(choices=[(0, '初始化配置'), (1, '传输备份数据'), (2, '等待切换'), (3, '发送切换命令')], default=0)),
                ('ext_config', models.TextField(default='{}')),
                ('start_switch', models.BooleanField(default=False)),
                ('switch_time', models.DateTimeField(blank=True, null=True, default=None)),
                ('schedule', models.ForeignKey(to='apiv1.HTBSchedule', related_name='htb_task')),
            ],
        ),
        migrations.AddField(
            model_name='htbsendtask',
            name='htb_task',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='send_task', to='apiv1.HTBTask'),
        ),
    ]
