# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0019_auto_20170601_1612'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='htbschedule',
            name='restore_target',
        ),
        migrations.AddField(
            model_name='htbschedule',
            name='target_info',
            field=models.TextField(default='[]'),
        ),
        migrations.AddField(
            model_name='htbtask',
            name='restore_target',
            field=models.ForeignKey(default=None, null=True, related_name='htb_task', to='apiv1.RestoreTarget', blank=True),
        ),
        migrations.AlterField(
            model_name='htbtask',
            name='status',
            field=models.PositiveSmallIntegerField(choices=[(0, '初始化配置'), (1, '初始化系统'), (2, '同步数据'), (3, '发送切换命令'), (4, '等待客户端完成登录'), (5, '任务失败'), (6, '任务成功'), (7, '获取驱动数据'), (10, '初始化动态IP'), (8, '切换IP'), (12, '传输驱动数据'), (11, '等待数据传输完毕'), (9, '执行服务启动脚本'), (13, '执行服务停止脚本')], default=0),
        ),
    ]
