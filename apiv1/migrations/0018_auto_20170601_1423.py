# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0017_auto_20170522_1959'),
    ]

    operations = [
        migrations.CreateModel(
            name='ClusterBackupSchedule',
            fields=[
                ('id', models.AutoField(primary_key=True, verbose_name='ID', serialize=False, auto_created=True)),
                ('enabled', models.BooleanField(default=True)),
                ('deleted', models.BooleanField(default=False)),
                ('name', models.CharField(max_length=256)),
                ('cycle_type', models.IntegerField(choices=[(1, 'CDP备份'), (2, '仅备份一次'), (3, '每天'), (4, '每周'), (5, '每月')])),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('plan_start_date', models.DateTimeField(default=None, null=True, blank=True)),
                ('ext_config', models.TextField(default='{}')),
                ('last_run_date', models.DateTimeField(default=None, null=True, blank=True)),
                ('next_run_date', models.DateTimeField(default=None, null=True, blank=True)),
                ('storage_node_ident', models.CharField(max_length=256)),
                ('hosts', models.ManyToManyField(related_name='cluster_backup_schedules', to='apiv1.Host')),
            ],
        ),
        migrations.CreateModel(
            name='ClusterBackupTask',
            fields=[
                ('id', models.AutoField(primary_key=True, verbose_name='ID', serialize=False, auto_created=True)),
                ('start_datetime', models.DateTimeField(auto_now_add=True)),
                ('finish_datetime', models.DateTimeField(default=None, null=True, blank=True)),
                ('successful', models.BooleanField(default=False)),
                ('reason', models.PositiveSmallIntegerField(default=0, choices=[(0, '未知原因'), (1, '自动执行'), (2, '手动执行')])),
                ('ext_config', models.TextField(default='{}')),
                ('task_uuid', models.TextField(default='{}')),
                ('schedule', models.ForeignKey(related_name='backup_tasks', on_delete=django.db.models.deletion.PROTECT, to='apiv1.ClusterBackupSchedule')),
            ],
        ),
        migrations.AddField(
            model_name='hostsnapshot',
            name='cluster_finish_datetime',
            field=models.DateTimeField(default=None, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='hostsnapshot',
            name='cluster_visible',
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name='cdptask',
            name='schedule',
            field=models.ForeignKey(null=True, related_name='cdp_tasks', on_delete=django.db.models.deletion.PROTECT, to='apiv1.BackupTaskSchedule'),
        ),
        migrations.AlterField(
            model_name='hostlog',
            name='type',
            field=models.PositiveSmallIntegerField(default=0, choices=[(0, 'unknown'), (1, '连接'), (2, '断开'), (3, '代理程序初始化失败'), (4, '备份开始'), (5, '备份成功'), (6, '备份失败'), (7, '还原开始'), (8, '还原成功'), (9, '还原失败'), (10, 'CDP保护开始'), (11, 'CDP保护停止'), (12, 'CDP保护失败'), (13, 'CDP保护暂停'), (14, 'CDP保护重新开始'), (15, '迁移开始'), (16, '迁移成功'), (17, '迁移失败'), (18, '回收过期数据空间'), (19, '客户端状态'), (20, '热备计划')]),
        ),
        migrations.AlterField(
            model_name='htbtask',
            name='status',
            field=models.PositiveSmallIntegerField(default=0, choices=[(0, '初始化配置'), (1, '初始化系统'), (2, '同步数据'), (3, '发送切换命令'), (4, '等待客户端完成登录'), (5, '任务失败'), (6, '任务成功'), (7, '获取驱动数据'), (8, '切换IP'), (9, '传输用户脚本')]),
        ),
        migrations.AddField(
            model_name='cdptask',
            name='cluster_task',
            field=models.ForeignKey(null=True, related_name='sub_tasks', on_delete=django.db.models.deletion.PROTECT, to='apiv1.ClusterBackupTask'),
        ),
        migrations.AddField(
            model_name='hostsnapshot',
            name='cluster_schedule',
            field=models.ForeignKey(null=True, related_name='host_snapshots', on_delete=django.db.models.deletion.PROTECT, to='apiv1.ClusterBackupSchedule'),
        ),
    ]
