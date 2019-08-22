# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('xdashboard', '0031_auto_20180702_1500'),
    ]

    operations = [
        migrations.AddField(
            model_name='operationlog',
            name='operator',
            field=models.CharField(max_length=64, null=True),
        ),
        migrations.AlterField(
            model_name='datadictionary',
            name='dictType',
            field=models.PositiveSmallIntegerField(choices=[(1, '邮件服务器'), (2, 'session过期时间'), (3, '密码策略'), (4, '选择邮件发送范围'), (5, '一体机更新外网地址'), (6, 'samba用户名和密码'), (7, '密码过期时间'), (8, '密码周期'), (9, '登录失败次数和解锁时间'), (10, '限制登录失败次数默认10次'), (11, '限制登录锁定分钟数默认30分钟'), (12, '接管使用的专用网段'), (13, '不完整点的过期天数'), (14, '任务队列数量'), (15, '日志数据存储空间'), (16, '备份一体机的数据库'), (17, '备份参数')], default=0),
        ),
        migrations.AlterField(
            model_name='userprofile',
            name='user_type',
            field=models.CharField(choices=[('normal-admin', '系统管理员'), ('content-admin', '内容管理员'), ('aud-admin', '安全审计管理员')], max_length=256, default='normal-admin'),
        ),
    ]
