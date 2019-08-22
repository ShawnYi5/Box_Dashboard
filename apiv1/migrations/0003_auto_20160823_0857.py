# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0002_auto_20160819_1152'),
    ]

    operations = [
        migrations.AlterField(
            model_name='spacecollectiontask',
            name='type',
            field=models.PositiveSmallIntegerField(default=0, choices=[(0, '未知类型'), (1, '删除普通备份点'), (2, '合并普通备份点'), (3, '回收CDP备份主任务'), (4, '回收CDP备份子任务'), (5, '删除快照点'), (6, '删除CDP文件'), (7, '删除CDP快照'), (8, '删除CDP客户端快照')]),
        ),
    ]
