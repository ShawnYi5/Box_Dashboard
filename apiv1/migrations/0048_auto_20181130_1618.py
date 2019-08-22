# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from django.conf import settings
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('apiv1', '0047_qcow2reorganizetask'),
    ]

    operations = [
        migrations.CreateModel(
            name='ArchiveSchedule',
            fields=[
                ('id', models.AutoField(serialize=False, primary_key=True, auto_created=True, verbose_name='ID')),
                ('name', models.CharField(max_length=256)),
                ('deleted', models.BooleanField(default=False)),
                ('enabled', models.BooleanField(default=True)),
                ('plan_start_date', models.DateTimeField(null=True, blank=True, default=None)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('last_run_date', models.DateTimeField(null=True, blank=True, default=None)),
                ('next_run_date', models.DateTimeField(null=True, blank=True, default=None)),
                ('ext_config', models.TextField(default='')),
                ('cycle_type', models.IntegerField(choices=[(1, 'CDP备份'), (2, '仅备份一次'), (3, '每天'), (4, '每周'), (5, '每月')])),
                ('storage_node_ident', models.CharField(max_length=256)),
            ],
        ),
        migrations.CreateModel(
            name='ArchiveSubTask',
            fields=[
                ('id', models.AutoField(serialize=False, primary_key=True, auto_created=True, verbose_name='ID')),
                ('start_datetime', models.DateTimeField(null=True, blank=True, default=None)),
                ('finish_datetime', models.DateTimeField(null=True, blank=True, default=None)),
                ('successful', models.BooleanField(default=False)),
                ('ext_config', models.TextField(default='{}')),
                ('native_guid', models.CharField(max_length=256)),
                ('hash_path', models.CharField(max_length=256)),
                ('ident', models.CharField(null=True, max_length=32, blank=True)),
                ('date_time', models.DateTimeField(null=True, blank=True, default=None)),
                ('disk_snapshot', models.ForeignKey(to='apiv1.DiskSnapshot')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='ArchiveTask',
            fields=[
                ('id', models.AutoField(serialize=False, primary_key=True, auto_created=True, verbose_name='ID')),
                ('start_datetime', models.DateTimeField(null=True, blank=True, default=None)),
                ('finish_datetime', models.DateTimeField(null=True, blank=True, default=None)),
                ('successful', models.BooleanField(default=False)),
                ('ext_config', models.TextField(default='{}')),
                ('task_uuid', models.CharField(max_length=32)),
                ('running_task', models.TextField(default='{}')),
                ('status', models.PositiveSmallIntegerField(choices=[(0, '初始化参数'), (5, '查询快照文件'), (6, '锁定快照文件'), (1, '生成去重数据'), (2, '传输数据'), (3, '任务成功'), (4, '任务失败'), (7, '生成关键数据信息'), (8, '生成位图信息')], default=0)),
                ('snapshot_datetime', models.DateTimeField(null=True, blank=True, default=None)),
                ('force_full', models.BooleanField(default=False)),
                ('host_snapshot', models.ForeignKey(related_name='archive_tasks', to='apiv1.HostSnapshot')),
                ('schedule', models.ForeignKey(related_name='archive_tasks', null=True, to='apiv1.ArchiveSchedule')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='EnumLink',
            fields=[
                ('id', models.AutoField(serialize=False, primary_key=True, auto_created=True, verbose_name='ID')),
                ('driver', models.CharField(max_length=256)),
                ('library', models.CharField(max_length=256)),
                ('drvid', models.IntegerField()),
            ],
        ),
        migrations.CreateModel(
            name='ImportSnapshotSubTask',
            fields=[
                ('id', models.AutoField(serialize=False, primary_key=True, auto_created=True, verbose_name='ID')),
                ('start_datetime', models.DateTimeField(null=True, blank=True, default=None)),
                ('finish_datetime', models.DateTimeField(null=True, blank=True, default=None)),
                ('successful', models.BooleanField(default=False)),
                ('ext_config', models.TextField(default='{}')),
                ('remote_disk_snapshot', models.CharField(max_length=32)),
                ('disk_snapshot', models.ForeignKey(to='apiv1.DiskSnapshot')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='ImportSnapshotTask',
            fields=[
                ('id', models.AutoField(serialize=False, primary_key=True, auto_created=True, verbose_name='ID')),
                ('start_datetime', models.DateTimeField(null=True, blank=True, default=None)),
                ('finish_datetime', models.DateTimeField(null=True, blank=True, default=None)),
                ('successful', models.BooleanField(default=False)),
                ('ext_config', models.TextField(default='{}')),
                ('status', models.PositiveSmallIntegerField(choices=[(0, '获取关键数据'), (1, '正在排队'), (2, '传输数据'), (3, '任务成功'), (4, '任务失败')], default=0)),
                ('task_uuid', models.CharField(max_length=32)),
                ('running_task', models.TextField(default='{}')),
                ('host_snapshot', models.ForeignKey(related_name='import_tasks', null=True, to='apiv1.HostSnapshot')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='ImportSource',
            fields=[
                ('id', models.AutoField(serialize=False, primary_key=True, auto_created=True, verbose_name='ID')),
                ('src_type', models.IntegerField(choices=[(1, '本地任务')], default=1)),
                ('local_task_uuid', models.CharField(null=True, max_length=32, blank=True)),
                ('ext_config', models.TextField(default='{}')),
            ],
        ),
        migrations.CreateModel(
            name='UserVolumePoolQuota',
            fields=[
                ('id', models.AutoField(serialize=False, primary_key=True, auto_created=True, verbose_name='ID')),
                ('deleted', models.BooleanField(default=False)),
                ('quota_size', models.BigIntegerField()),
                ('caution_size', models.BigIntegerField()),
                ('available_size', models.BigIntegerField()),
                ('ext_info', models.TextField(default='{}')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='uservolumepoolquotas', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='VolumePool',
            fields=[
                ('id', models.AutoField(serialize=False, primary_key=True, auto_created=True, verbose_name='ID')),
                ('name', models.CharField(max_length=256, unique=True)),
                ('driver', models.CharField(max_length=256)),
                ('cycle', models.IntegerField(default=0)),
                ('cycle_type', models.IntegerField(choices=[(1, '天'), (2, '周'), (3, '月')])),
                ('tapas', models.TextField(default='{}')),
                ('pool_uuid', models.CharField(max_length=32, default='1')),
            ],
        ),
        migrations.AddField(
            model_name='host',
            name='archive_uuid',
            field=models.CharField(null=True, max_length=32, blank=True),
        ),
        migrations.AlterField(
            model_name='host',
            name='type',
            field=models.BigIntegerField(choices=[(0, '普通客户端'), (1, '远程客户端'), (2, '免代理客户端'), (3, '数据导入')], default=0),
        ),
        migrations.AlterField(
            model_name='hostlog',
            name='type',
            field=models.PositiveSmallIntegerField(choices=[(0, 'unknown'), (1, '连接'), (2, '断开'), (3, '代理程序初始化失败'), (4, '备份'), (5, '备份成功'), (6, '备份失败'), (7, '还原'), (8, '还原成功'), (9, '还原失败'), (10, 'CDP保护'), (11, 'CDP保护停止'), (12, 'CDP保护失败'), (13, 'CDP保护暂停'), (14, 'CDP保护重新开始'), (15, '迁移'), (16, '迁移成功'), (17, '迁移失败'), (18, '回收过期数据空间'), (19, '基础备份'), (20, '热备'), (21, '基础备份完成'), (100, '集群备份开始'), (101, '集群基础备份'), (102, '分析集群数据'), (103, '生成集群快照'), (104, '集群备份成功'), (105, '集群备份失败'), (200, '同步普通快照开始'), (201, '同步普通快照成功'), (202, '同步普通快照失败'), (203, '同步CDP快照开始'), (204, '同步CDP快照结束'), (205, '远程灾备'), (22, '免代理还原'), (23, '备份数据导出'), (24, '备份数据导入')], default=0),
        ),
        migrations.AddField(
            model_name='uservolumepoolquota',
            name='volume_pool_node',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='uservolumepoolquotas', to='apiv1.VolumePool'),
        ),
        migrations.AddField(
            model_name='importsnapshottask',
            name='source',
            field=models.ForeignKey(blank=True, null=True, to='apiv1.ImportSource'),
        ),
        migrations.AddField(
            model_name='importsnapshotsubtask',
            name='main_task',
            field=models.ForeignKey(related_name='sub_tasks', to='apiv1.ImportSnapshotTask'),
        ),
        migrations.AddField(
            model_name='archivesubtask',
            name='main_task',
            field=models.ForeignKey(related_name='sub_tasks', to='apiv1.ArchiveTask'),
        ),
        migrations.AddField(
            model_name='archiveschedule',
            name='host',
            field=models.ForeignKey(related_name='archive_schedules', to='apiv1.Host'),
        ),
    ]
