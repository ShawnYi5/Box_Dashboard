# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0051_auto_20190520_0656'),
    ]

    operations = [
        migrations.CreateModel(
            name='ClusterTokenMapper',
            fields=[
                ('id', models.AutoField(serialize=False, primary_key=True, auto_created=True, verbose_name='ID')),
                ('agent_token', models.CharField(unique=True, max_length=32)),
                ('host_ident', models.CharField(max_length=32)),
                ('disk_id', models.IntegerField()),
                ('cluster_task', models.ForeignKey(related_name='token_maps', to='apiv1.ClusterBackupTask', on_delete=django.db.models.deletion.PROTECT)),
            ],
        ),
        migrations.AddField(
            model_name='clusterbackupschedule',
            name='agent_valid_disk_snapshot',
            field=models.TextField(default='{}'),
        ),
        migrations.AlterField(
            model_name='hostlog',
            name='type',
            field=models.PositiveSmallIntegerField(choices=[(0, 'unknown'), (1, '连接'), (2, '断开'), (3, '代理程序初始化失败'), (4, '备份'), (5, '备份成功'), (6, '备份失败'), (7, '还原'), (8, '还原成功'), (9, '还原失败'), (10, 'CDP保护'), (11, 'CDP保护停止'), (12, 'CDP保护失败'), (13, 'CDP保护暂停'), (14, 'CDP保护重新开始'), (15, '迁移'), (16, '迁移成功'), (17, '迁移失败'), (18, '回收过期数据空间'), (19, '基础备份'), (20, '热备'), (21, '基础备份完成'), (100, '集群备份开始'), (101, '集群基础备份'), (102, '分析集群数据'), (103, '生成集群快照'), (104, '集群备份成功'), (105, '集群备份失败'), (150, 'CDP集群备份开始'), (151, 'CDP集群基础备份'), (152, '分析CDP集群数据'), (153, '生成CDP集群快照'), (154, 'CDP集群持续保护中'), (155, 'CDP集群备份失败'), (156, 'CDP集群备份终止'), (157, 'CDP集群备份停止'), (200, '同步普通快照开始'), (201, '同步普通快照成功'), (202, '同步普通快照失败'), (203, '同步CDP快照开始'), (204, '同步CDP快照结束'), (205, '远程灾备'), (22, '免代理还原'), (23, '备份数据导出'), (24, '备份数据导入'), (25, '自动验证成功'), (26, '自动验证失败'), (27, '审批')], default=0),
        ),
        migrations.AddField(
            model_name='clustertokenmapper',
            name='file_token',
            field=models.ForeignKey(related_name='token_maps', to='apiv1.CDPDiskToken', on_delete=django.db.models.deletion.PROTECT),
        ),
    ]
