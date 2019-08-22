# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('xdashboard', '0015_auto_20161013_1339'),
    ]

    operations = [
        migrations.AlterField(
            model_name='email',
            name='type',
            field=models.PositiveSmallIntegerField(choices=[(1, '用户配额不足'), (2, '存储结点离线'), (3, '存储结点不可用'), (4, 'CDP保护停止'), (5, 'CDP保护失败'), (6, 'CDP保护暂停'), (7, '系统时间错误')], default=-1),
        ),
    ]
