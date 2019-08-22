# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0028_auto_20170906_1526'),
    ]

    operations = [
        migrations.AlterField(
            model_name='disksnapshotcdp',
            name='token',
            field=models.ForeignKey(related_name='files', on_delete=django.db.models.deletion.PROTECT, null=True, to='apiv1.CDPDiskToken'),
        ),
        migrations.AlterField(
            model_name='hostlog',
            name='type',
            field=models.PositiveSmallIntegerField(default=0, choices=[(0, 'unknown'), (1, '连接'), (2, '断开'), (3, '代理程序初始化失败'), (4, '备份开始'), (5, '备份成功'), (6, '备份失败'), (7, '还原开始'), (8, '还原成功'), (9, '还原失败'), (10, 'CDP保护开始'), (11, 'CDP保护停止'), (12, 'CDP保护失败'), (13, 'CDP保护暂停'), (14, 'CDP保护重新开始'), (15, '迁移开始'), (16, '迁移成功'), (17, '迁移失败'), (18, '回收过期数据空间'), (19, '基础备份'), (20, '热备计划'), (100, '集群备份开始'), (101, '集群基础备份'), (102, '分析集群数据'), (103, '生成集群快照'), (104, '集群备份成功'), (105, '集群备份失败'), (200, '同步普通快照开始'), (201, '同步普通快照成功'), (202, '同步普通快照失败'), (203, '同步CDP快照开始'), (204, '同步CDP快照结束')]),
        ),
    ]
