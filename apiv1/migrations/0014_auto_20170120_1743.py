# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0013_compresstask'),
    ]

    operations = [
        migrations.AlterField(
            model_name='restoretarget',
            name='type',
            field=models.PositiveSmallIntegerField(choices=[(128, '未知类型'), (0, 'PE系统'), (1, 'AGENT系统'), (2, '卷还原')], default=128),
        ),
        migrations.AlterField(
            model_name='restoretask',
            name='type',
            field=models.PositiveSmallIntegerField(choices=[(0, '未知目标类型'), (1, '恢复到在线客户端'), (2, '恢复到启动介质客户端'), (3, '卷恢复')], default=0),
        ),
    ]
