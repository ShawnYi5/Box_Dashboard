# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0053_auto_20190619_1340'),
    ]

    operations = [
        migrations.CreateModel(
            name='DBBackupKVM',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('ext_info', models.TextField(default='{}')),
                ('mac', models.CharField(max_length=12, blank=True, default='')),
            ],
        ),
        migrations.CreateModel(
            name='KVMBackupTask',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('start_datetime', models.DateTimeField(null=True, blank=True, default=None)),
                ('finish_datetime', models.DateTimeField(null=True, blank=True, default=None)),
                ('successful', models.BooleanField(default=False)),
                ('ext_config', models.TextField(default='{}')),
                ('task_type', models.CharField(max_length=256, default='kvm_backup4database', choices=[('kvm_backup4self', '虚拟机备份'), ('kvm_backup4database', '数据库备份')])),
                ('task_uuid', models.CharField(max_length=32)),
                ('status', models.PositiveSmallIntegerField(default=0, choices=[(0, '初始化参数'), (1, '查询快照文件'), (2, '锁定快照文件'), (3, '发送开机指令'), (6, '任务成功'), (7, '任务失败'), (8, '本次备份模式：')])),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.AddField(
            model_name='hostsnapshot',
            name='label',
            field=models.TextField(default=''),
        ),
        migrations.AlterField(
            model_name='filebackuptask',
            name='status',
            field=models.PositiveSmallIntegerField(default=0, choices=[(0, '初始化参数'), (1, '查询快照文件'), (2, '锁定快照文件'), (3, '发送备份指令'), (5, '初始化备份代理'), (4, '传输数据'), (6, '任务成功'), (7, '任务失败'), (8, '本次备份模式：')]),
        ),
        migrations.AlterField(
            model_name='host',
            name='type',
            field=models.BigIntegerField(default=0, choices=[(0, '普通客户端'), (1, '远程客户端'), (2, '免代理客户端'), (3, '数据导入'), (4, 'NAS客户端'), (5, '数据库客户端K'), (6, '数据库客户端R')]),
        ),
        migrations.AlterField(
            model_name='hostlog',
            name='type',
            field=models.PositiveSmallIntegerField(default=0, choices=[(0, 'unknown'), (1, '连接'), (2, '断开'), (3, '代理程序初始化失败'), (4, '备份'), (5, '备份成功'), (6, '备份失败'), (7, '还原'), (8, '还原成功'), (9, '还原失败'), (10, 'CDP保护'), (11, 'CDP保护停止'), (12, 'CDP保护失败'), (13, 'CDP保护暂停'), (14, 'CDP保护重新开始'), (15, '迁移'), (16, '迁移成功'), (17, '迁移失败'), (18, '回收过期数据空间'), (19, '基础备份'), (20, '热备'), (21, '基础备份完成'), (100, '集群备份开始'), (101, '集群基础备份'), (102, '分析集群数据'), (103, '生成集群快照'), (104, '集群备份成功'), (105, '集群备份失败'), (150, 'CDP集群备份开始'), (151, 'CDP集群基础备份'), (152, '分析CDP集群数据'), (153, '生成CDP集群快照'), (154, 'CDP集群持续保护中'), (155, 'CDP集群备份失败'), (156, 'CDP集群备份终止'), (157, 'CDP集群备份停止'), (200, '同步普通快照开始'), (201, '同步普通快照成功'), (202, '同步普通快照失败'), (203, '同步CDP快照开始'), (204, '同步CDP快照结束'), (205, '远程灾备'), (22, '免代理还原'), (23, '备份数据导出'), (24, '备份数据导入'), (25, '自动验证成功'), (26, '自动验证失败'), (27, '审批'), (300, 'CDP集群备份'), (28, '虚拟机备份'), (29, '数据库备份')]),
        ),
        migrations.AlterField(
            model_name='hostsnapshot',
            name='host',
            field=models.ForeignKey(related_name='snapshots', null=True, to='apiv1.Host', on_delete=django.db.models.deletion.PROTECT),
        ),
        migrations.AddField(
            model_name='kvmbackuptask',
            name='host_snapshot',
            field=models.OneToOneField(related_name='kvm_backup_task', null=True, to='apiv1.HostSnapshot', on_delete=django.db.models.deletion.PROTECT),
        ),
        migrations.AddField(
            model_name='kvmbackuptask',
            name='kvm',
            field=models.ForeignKey(to='apiv1.DBBackupKVM', related_name='kvm_backup_tasks'),
        ),
        migrations.AddField(
            model_name='kvmbackuptask',
            name='schedule',
            field=models.ForeignKey(related_name='kvm_backup_tasks', null=True, to='apiv1.BackupTaskSchedule'),
        ),
        migrations.AddField(
            model_name='dbbackupkvm',
            name='host',
            field=models.OneToOneField(related_name='kvm_info', to='apiv1.Host'),
        ),
        migrations.AddField(
            model_name='dbbackupkvm',
            name='host_snapshot',
            field=models.OneToOneField(related_name='kvm_info', null=True, blank=True, to='apiv1.HostSnapshot'),
        ),
    ]
