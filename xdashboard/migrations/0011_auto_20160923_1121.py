# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('xdashboard', '0010_auto_20160919_2005'),
    ]

    operations = [
        migrations.AlterField(
            model_name='datadictionary',
            name='dictType',
            field=models.PositiveSmallIntegerField(default=0, choices=[(1, '邮件服务器'), (2, 'session过期时间'), (3, '密码策略'), (4, '选择邮件发送范围'), (5, '一体机更新外网地址'), (6, 'samba用户名和密码'), (7, '密码过期时间'), (8, '密码周期'), (9, '登录失败次数和解锁时间'), (10, '限制登录失败次数默认3次'), (11, '限制登录锁定分钟数默认30分钟')]),
        ),
    ]
