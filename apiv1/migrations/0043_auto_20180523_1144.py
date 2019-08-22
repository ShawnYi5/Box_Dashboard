# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0042_auto_20180515_1417'),
    ]

    operations = [
        migrations.AlterField(
            model_name='hostlog',
            name='type',
            field=models.PositiveSmallIntegerField(default=0, choices=[(0, 'unknown'), (1, '连接'), (2, '断开'), (3, '代理程序初始化失败'), (4, '备份'), (5, '备份成功'), (6, '备份失败'), (7, '还原'), (8, '还原成功'), (9, '还原失败'), (10, 'CDP保护'), (11, 'CDP保护停止'), (12, 'CDP保护失败'), (13, 'CDP保护暂停'), (14, 'CDP保护重新开始'), (15, '迁移'), (16, '迁移成功'), (17, '迁移失败'), (18, '回收过期数据空间'), (19, '基础备份'), (20, '热备'), (21, '基础备份完成'), (100, '集群备份开始'), (101, '集群基础备份'), (102, '分析集群数据'), (103, '生成集群快照'), (104, '集群备份成功'), (105, '集群备份失败'), (200, '同步普通快照开始'), (201, '同步普通快照成功'), (202, '同步普通快照失败'), (203, '同步CDP快照开始'), (204, '同步CDP快照结束'), (205, '远程灾备'), (22, '免代理还原')]),
        ),
        migrations.AlterField(
            model_name='virtualmachinerestoretask',
            name='status',
            field=models.PositiveSmallIntegerField(default=6, choices=[(0, '初始化参数'), (6, '查询快照文件'), (7, '锁定快照文件'), (1, '构建还原目标机'), (2, '挂载虚拟磁盘'), (3, '传输数据'), (4, '还原任务成功'), (5, '还原任务失败')]),
        ),
    ]
