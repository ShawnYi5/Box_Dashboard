# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0009_auto_20160929_1658'),
    ]

    operations = [
        migrations.AlterField(
            model_name='hostlog',
            name='type',
            field=models.PositiveSmallIntegerField(default=0, choices=[(0, 'unknown'), (1, '连接'), (2, '断开'), (3, '代理程序初始化失败'), (4, '备份开始'), (5, '备份成功'), (6, '备份失败'), (7, '还原开始'), (8, '还原成功'), (9, '还原失败'), (10, 'CDP保护开始'), (11, 'CDP保护停止'), (12, 'CDP保护失败'), (13, 'CDP保护暂停'), (14, 'CDP保护重新开始'), (15, '迁移开始'), (16, '迁移成功'), (17, '迁移失败'), (18, '回收过期数据空间'), (19, '客户端状态')]),
        ),
    ]
