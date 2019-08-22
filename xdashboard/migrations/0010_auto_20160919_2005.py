# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('xdashboard', '0009_userquotaspace'),
    ]

    operations = [
        migrations.AlterField(
            model_name='operationlog',
            name='event',
            field=models.PositiveSmallIntegerField(choices=[(0, 'unknown'), (1, '邮件服务器'), (2, '网络设置'), (3, '备份任务管理'), (4, '客户端管理'), (5, '恢复'), (6, '迁移'), (7, '存储管理'), (8, '用户管理'), (9, '系统设置'), (10, '启动介质'), (11, '操作日志'), (12, '客户端日志'), (13, '浏览备份'), (20, '一体机更新'), (21, '服务器驱动更新'), (22, '启动介质数据源更新'), (23, 'TODO')], default=0),
        ),
    ]
