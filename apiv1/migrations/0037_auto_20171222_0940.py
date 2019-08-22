# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0036_auto_20171128_1520'),
    ]

    operations = [
        migrations.AddField(
            model_name='hostsnapshot',
            name='partial',
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name='hostlog',
            name='type',
            field=models.PositiveSmallIntegerField(default=0, choices=[(0, 'unknown'), (1, '连接'), (2, '断开'), (3, '代理程序初始化失败'), (4, '备份'), (5, '备份成功'), (6, '备份失败'), (7, '还原'), (8, '还原成功'), (9, '还原失败'), (10, 'CDP保护'), (11, 'CDP保护停止'), (12, 'CDP保护失败'), (13, 'CDP保护暂停'), (14, 'CDP保护重新开始'), (15, '迁移'), (16, '迁移成功'), (17, '迁移失败'), (18, '回收过期数据空间'), (19, '基础备份'), (20, '热备'), (21, '基础备份完成'), (100, '集群备份开始'), (101, '集群基础备份'), (102, '分析集群数据'), (103, '生成集群快照'), (104, '集群备份成功'), (105, '集群备份失败'), (200, '同步普通快照开始'), (201, '同步普通快照成功'), (202, '同步普通快照失败'), (203, '同步CDP快照开始'), (204, '同步CDP快照结束'), (205, '远程灾备')]),
        ),
        migrations.AlterField(
            model_name='htbtask',
            name='status',
            field=models.PositiveSmallIntegerField(default=0, choices=[(0, '开始热备任务'), (1, '初始化系统'), (2, '构建备机操作系统成功, 同步剩余数据'), (3, '发送切换命令'), (4, '等待客户端完成登录'), (5, '任务失败'), (6, '任务成功'), (7, '获取驱动数据'), (10, '初始化动态IP'), (8, '切换IP'), (12, '传输驱动数据'), (11, '等待数据传输完毕'), (9, '执行服务启动脚本'), (13, '执行服务停止脚本'), (14, '发送卷热备命令'), (15, '发送热备命令成功，同步剩余数据'), (16, '等待卷完成初始化')]),
        ),
    ]
