# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('xdashboard', '0030_auto_20180627_2201'),
    ]

    operations = [
        migrations.DeleteModel(
            name='WeiXinInfo',
        ),
        migrations.AlterField(
            model_name='operationlog',
            name='event',
            field=models.PositiveSmallIntegerField(choices=[(0, 'unknown'), (1, '邮件服务器'), (2, '网络设置'), (3, '备份任务管理'), (4, '客户端管理'), (5, '恢复'), (6, '迁移'), (7, '存储管理'), (8, '用户管理'), (9, '系统设置'), (10, '启动介质'), (11, '操作日志'), (12, '客户端日志'), (13, '浏览备份'), (20, '一体机更新'), (21, '服务器驱动更新'), (22, '启动介质数据源更新'), (23, '去重数据更新'), (14, '备份任务策略'), (24, '热备'), (25, '网站防护'), (26, 'PXE设置'), (27, '接管'), (28, '集群备份计划管理'), (29, '远程容灾计划管理'), (30, '免代理'), (31, '微信设置')], default=0),
        ),
    ]
