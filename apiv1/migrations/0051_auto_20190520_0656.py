# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('apiv1', '0050_auto_20190214_1127'),
    ]

    operations = [
        migrations.CreateModel(
            name='AutoVerifySchedule',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False, auto_created=True, verbose_name='ID')),
                ('name', models.CharField(max_length=256)),
                ('storage_node_ident', models.CharField(max_length=256)),
                ('ext_config', models.TextField(default='{}')),
                ('enabled', models.BooleanField(default=True)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('last_run_date', models.DateTimeField(null=True, blank=True, default=None)),
                ('next_run_date', models.DateTimeField(null=True, blank=True, default=None)),
                ('plan_start_date', models.DateTimeField(null=True, blank=True, default=None)),
                ('cycle_type', models.IntegerField(choices=[(1, 'CDP备份'), (2, '仅备份一次'), (3, '每天'), (4, '每周'), (5, '每月')])),
                ('host_groups', models.ManyToManyField(to='apiv1.HostGroup', related_name='host_group_AutoVerifySchedule', blank=True)),
                ('hosts', models.ManyToManyField(to='apiv1.Host', related_name='hosts_AutoVerifySchedule', blank=True)),
                ('user', models.ForeignKey(related_name='user_AutoVerifySchedule', blank=True, to=settings.AUTH_USER_MODEL, null=True)),
            ],
        ),
        migrations.CreateModel(
            name='AutoVerifyTask',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False, auto_created=True, verbose_name='ID')),
                ('point_id', models.CharField(max_length=256)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('schedule_name', models.CharField(max_length=256)),
                ('storage_node_ident', models.CharField(max_length=256)),
                ('schedule_ext_config', models.TextField(default='{}')),
                ('verify_type', models.PositiveSmallIntegerField(choices=[(1, '等待验证'), (2, '正在验证'), (3, '验证完成'), (4, '无备份点')], default=1)),
                ('verify_result', models.TextField(default='{}')),
            ],
        ),
        migrations.CreateModel(
            name='DeployTemplate',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False, auto_created=True, verbose_name='ID')),
                ('name', models.CharField(max_length=256)),
                ('desc', models.TextField(default='')),
                ('snapshot_datetime', models.DateTimeField(null=True, blank=True)),
                ('create_datetime', models.DateTimeField(auto_now_add=True)),
                ('ext_info', models.TextField(default='{}')),
                ('host_snapshot', models.ForeignKey(related_name='deploy_templates', blank=True, to='apiv1.HostSnapshot', null=True)),
            ],
        ),
        migrations.AlterField(
            model_name='filebackuptask',
            name='status',
            field=models.PositiveSmallIntegerField(choices=[(0, '初始化参数'), (1, '查询快照文件'), (2, '锁定快照文件'), (3, '发送备份指令'), (5, '初始化备份代理'), (4, '传输数据'), (6, '任务成功'), (7, '任务失败')], default=0),
        ),
        migrations.AlterField(
            model_name='hostlog',
            name='type',
            field=models.PositiveSmallIntegerField(choices=[(0, 'unknown'), (1, '连接'), (2, '断开'), (3, '代理程序初始化失败'), (4, '备份'), (5, '备份成功'), (6, '备份失败'), (7, '还原'), (8, '还原成功'), (9, '还原失败'), (10, 'CDP保护'), (11, 'CDP保护停止'), (12, 'CDP保护失败'), (13, 'CDP保护暂停'), (14, 'CDP保护重新开始'), (15, '迁移'), (16, '迁移成功'), (17, '迁移失败'), (18, '回收过期数据空间'), (19, '基础备份'), (20, '热备'), (21, '基础备份完成'), (100, '集群备份开始'), (101, '集群基础备份'), (102, '分析集群数据'), (103, '生成集群快照'), (104, '集群备份成功'), (105, '集群备份失败'), (200, '同步普通快照开始'), (201, '同步普通快照成功'), (202, '同步普通快照失败'), (203, '同步CDP快照开始'), (204, '同步CDP快照结束'), (205, '远程灾备'), (22, '免代理还原'), (23, '备份数据导出'), (24, '备份数据导入'), (25, '自动验证成功'), (26, '自动验证失败'), (27, '审批')], default=0),
        ),
        migrations.AlterField(
            model_name='remotebackuptask',
            name='status',
            field=models.IntegerField(choices=[(-1, '查询快照点状态'), (0, '创建主机快照'), (1, '查询磁盘状态'), (2, '创建同步子任务'), (3, '同步数据'), (4, '通信异常, 无法同步'), (8, '连接参数异常, 无法同步；请检查用户名和密码'), (5, '快照文件被删除, 无法同步'), (6, '计划被禁用, 无法同步'), (7, '计划被删除, 无法同步')], default=-1),
        ),
    ]
