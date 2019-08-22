# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0054_auto_20190628_1124'),
    ]

    operations = [
        migrations.CreateModel(
            name='FileSyncSchedule',
            fields=[
                ('id', models.AutoField(primary_key=True, auto_created=True, serialize=False, verbose_name='ID')),
                ('enabled', models.BooleanField(default=True)),
                ('deleted', models.BooleanField(default=False)),
                ('name', models.CharField(max_length=256)),
                ('cycle_type', models.IntegerField(choices=[(1, 'CDP备份'), (2, '仅备份一次'), (3, '每天'), (4, '每周'), (5, '每月')])),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('plan_start_date', models.DateTimeField(default=None, null=True, blank=True)),
                ('source_host_ident', models.CharField(max_length=32)),
                ('target_host_ident', models.CharField(max_length=32)),
                ('ext_config', models.TextField(default='')),
                ('last_run_date', models.DateTimeField(default=None, null=True, blank=True)),
                ('next_run_date', models.DateTimeField(default=None, null=True, blank=True)),
            ],
        ),
        migrations.CreateModel(
            name='FileSyncTask',
            fields=[
                ('id', models.AutoField(primary_key=True, auto_created=True, serialize=False, verbose_name='ID')),
                ('start_datetime', models.DateTimeField(default=None, null=True, blank=True)),
                ('finish_datetime', models.DateTimeField(default=None, null=True, blank=True)),
                ('successful', models.BooleanField(default=False)),
                ('ext_config', models.TextField(default='{}')),
                ('task_uuid', models.CharField(max_length=32)),
                ('status', models.PositiveSmallIntegerField(default=0, choices=[(0, '查询快照文件'), (1, '锁定快照文件'), (2, '启动本地代理程序'), (3, '任务成功'), (4, '任务失败')])),
                ('host_snapshot', models.ForeignKey(to='apiv1.HostSnapshot', related_name='file_sync_tasks')),
                ('schedule', models.ForeignKey(to='apiv1.FileSyncSchedule', null=True, related_name='file_sync_tasks')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.AlterField(
            model_name='hostlog',
            name='type',
            field=models.PositiveSmallIntegerField(default=0, choices=[(0, 'unknown'), (1, '连接'), (2, '断开'), (3, '代理程序初始化失败'), (4, '备份'), (5, '备份成功'), (6, '备份失败'), (7, '还原'), (8, '还原成功'), (9, '还原失败'), (10, 'CDP保护'), (11, 'CDP保护停止'), (12, 'CDP保护失败'), (13, 'CDP保护暂停'), (14, 'CDP保护重新开始'), (15, '迁移'), (16, '迁移成功'), (17, '迁移失败'), (18, '回收过期数据空间'), (19, '基础备份'), (20, '热备'), (21, '基础备份完成'), (100, '集群备份开始'), (101, '集群基础备份'), (102, '分析集群数据'), (103, '生成集群快照'), (104, '集群备份成功'), (105, '集群备份失败'), (150, 'CDP集群备份开始'), (151, 'CDP集群基础备份'), (152, '分析CDP集群数据'), (153, '生成CDP集群快照'), (154, 'CDP集群持续保护中'), (155, 'CDP集群备份失败'), (156, 'CDP集群备份终止'), (157, 'CDP集群备份停止'), (200, '同步普通快照开始'), (201, '同步普通快照成功'), (202, '同步普通快照失败'), (203, '同步CDP快照开始'), (204, '同步CDP快照结束'), (205, '远程灾备'), (22, '免代理还原'), (23, '备份数据导出'), (24, '备份数据导入'), (25, '自动验证成功'), (26, '自动验证失败'), (27, '审批'), (300, 'CDP集群备份'), (28, '虚拟机备份'), (29, '数据库备份'), (400, '文件归档')]),
        ),
    ]
