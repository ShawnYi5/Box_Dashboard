# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0027_takeoverkvm'),
    ]

    operations = [
        migrations.CreateModel(
            name='RemoteBackupSchedule',
            fields=[
                ('id', models.AutoField(auto_created=True, serialize=False, primary_key=True, verbose_name='ID')),
                ('enabled', models.BooleanField(default=True)),
                ('deleted', models.BooleanField(default=False)),
                ('name', models.CharField(max_length=256)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('ext_config', models.TextField(default='{}')),
                ('storage_node_ident', models.CharField(max_length=256)),
            ],
        ),
        migrations.CreateModel(
            name='RemoteBackupSubTask',
            fields=[
                ('id', models.AutoField(auto_created=True, serialize=False, primary_key=True, verbose_name='ID')),
                ('start_datetime', models.DateTimeField(auto_now_add=True)),
                ('finish_datetime', models.DateTimeField(blank=True, null=True, default=None)),
                ('successful', models.BooleanField(default=False)),
                ('remote_snapshot_ident', models.CharField(unique=True, max_length=32)),
                ('ext_config', models.TextField(default='{}')),
                ('local_snapshot', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='remote_backup_sub_task', to='apiv1.DiskSnapshot')),
            ],
        ),
        migrations.CreateModel(
            name='RemoteBackupTask',
            fields=[
                ('id', models.AutoField(auto_created=True, serialize=False, primary_key=True, verbose_name='ID')),
                ('start_datetime', models.DateTimeField(auto_now_add=True)),
                ('finish_datetime', models.DateTimeField(blank=True, null=True, default=None)),
                ('successful', models.BooleanField(default=False)),
                ('ext_config', models.TextField(default='{}')),
                ('host_snapshot', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, null=True, related_name='remote_backup_task', to='apiv1.HostSnapshot')),
                ('schedule', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='remote_backup_tasks', to='apiv1.RemoteBackupSchedule')),
            ],
        ),
        migrations.AddField(
            model_name='host',
            name='aio_info',
            field=models.TextField(default='{}'),
        ),
        migrations.AlterField(
            model_name='htbtask',
            name='status',
            field=models.PositiveSmallIntegerField(choices=[(0, '初始化配置'), (1, '初始化系统'), (2, '构建备机操作系统成功, 同步剩余数据'), (3, '发送切换命令'), (4, '等待客户端完成登录'), (5, '任务失败'), (6, '任务成功'), (7, '获取驱动数据'), (10, '初始化动态IP'), (8, '切换IP'), (12, '传输驱动数据'), (11, '等待数据传输完毕'), (9, '执行服务启动脚本'), (13, '执行服务停止脚本'), (14, '发送卷热备命令'), (15, '发送热备命令成功，同步剩余数据'), (16, '等待卷完成初始化')], default=0),
        ),
        migrations.AddField(
            model_name='remotebackupsubtask',
            name='main_task',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='remote_backup_sub_tasks', to='apiv1.RemoteBackupTask'),
        ),
        migrations.AddField(
            model_name='remotebackupschedule',
            name='host',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='remote_backup_schedules', to='apiv1.Host'),
        ),
    ]
