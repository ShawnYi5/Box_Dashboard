# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('xdashboard', '0029_auto_20180523_1108'),
    ]

    operations = [
        migrations.CreateModel(
            name='WeiXinInfo',
            fields=[
                ('id', models.AutoField(auto_created=True, serialize=False, primary_key=True, verbose_name='ID')),
                ('corp_id', models.CharField(max_length=20)),
                ('corp_secret', models.CharField(max_length=50)),
                ('agent_id', models.IntegerField(default=-1)),
            ],
        ),
        migrations.AddField(
            model_name='userprofile',
            name='wei_xin',
            field=models.CharField(max_length=100, default=''),
        ),
        migrations.AlterField(
            model_name='datadictionary',
            name='dictType',
            field=models.PositiveSmallIntegerField(default=0, choices=[(1, '邮件服务器'), (2, 'session过期时间'), (3, '密码策略'), (4, '选择邮件发送范围'), (5, '一体机更新外网地址'), (6, 'samba用户名和密码'), (7, '密码过期时间'), (8, '密码周期'), (9, '登录失败次数和解锁时间'), (10, '限制登录失败次数默认10次'), (11, '限制登录锁定分钟数默认30分钟'), (12, '接管使用的专用网段'), (13, '不完整点的过期天数'), (14, '任务队列数量')]),
        ),
    ]
