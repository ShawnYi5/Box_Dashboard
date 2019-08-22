# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('xdashboard', '0002_auto_20160815_1944'),
    ]

    operations = [
        migrations.AlterField(
            model_name='operationlog',
            name='event',
            field=models.PositiveSmallIntegerField(default=0, choices=[(0, 'unknown'), (1, '邮件服务器'), (2, '网络设置'), (3, '备份任务管理'), (4, '客户端管理'), (5, '恢复'), (6, '迁移'), (7, '存储管理'), (8, '用户管理'), (20, '一体机更新'), (21, '服务器驱动更新'), (22, '启动介质数据源更新'), (23, 'TODO')]),
        ),
    ]
