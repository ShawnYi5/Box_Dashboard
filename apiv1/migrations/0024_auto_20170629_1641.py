# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0023_htbsendtask_ex_vols'),
    ]

    operations = [
        migrations.AlterField(
            model_name='htbsendtask',
            name='native_guid',
            field=models.CharField(max_length=50),
        ),
        migrations.AlterField(
            model_name='htbtask',
            name='status',
            field=models.PositiveSmallIntegerField(choices=[(0, '初始化配置'), (1, '初始化系统'), (2, '构建备机操作系统成功, 同步剩余数据'), (3, '发送切换命令'), (4, '等待客户端完成登录'), (5, '任务失败'), (6, '任务成功'), (7, '获取驱动数据'), (10, '初始化动态IP'), (8, '切换IP'), (12, '传输驱动数据'), (11, '等待数据传输完毕'), (9, '执行服务启动脚本'), (13, '执行服务停止脚本')], default=0),
        ),
    ]
