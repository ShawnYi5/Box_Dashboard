# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0035_auto_20171127_1912'),
    ]

    operations = [
        migrations.AddField(
            model_name='remotebackuptask',
            name='paused',
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name='remotebackuptask',
            name='status',
            field=models.IntegerField(choices=[(-1, '查询快照点状态'), (0, '创建主机快照'), (1, '查询磁盘状态'), (2, '创建同步子任务'), (3, '同步数据'), (4, '通信异常, 无法同步'), (5, '快照文件被删除, 无法同步'), (6, '计划被禁用, 无法同步'), (7, '计划被删除, 无法同步')], default=-1),
        ),
    ]
