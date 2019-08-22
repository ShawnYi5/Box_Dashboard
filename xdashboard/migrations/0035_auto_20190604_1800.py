# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('xdashboard', '0034_auto_20190327_1512'),
    ]

    operations = [
        migrations.CreateModel(
            name='auto_verify_script',
            fields=[
                ('id', models.AutoField(auto_created=True, verbose_name='ID', serialize=False, primary_key=True)),
                ('filename', models.CharField(max_length=256)),
                ('name', models.CharField(max_length=256)),
                ('desc', models.CharField(max_length=256)),
                ('path', models.CharField(max_length=256)),
                ('user', models.ForeignKey(related_name='user_auto_verify_script', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AlterField(
            model_name='datadictionary',
            name='dictType',
            field=models.PositiveSmallIntegerField(choices=[(1, '邮件服务器'), (2, 'session过期时间'), (3, '密码策略'), (4, '选择邮件发送范围'), (5, '一体机更新外网地址'), (6, 'samba用户名和密码'), (7, '密码过期时间'), (8, '密码周期'), (9, '登录失败次数和解锁时间'), (10, '限制登录失败次数默认10次'), (11, '限制登录锁定分钟数默认30分钟'), (12, '接管使用的专用网段'), (13, '不完整点的过期天数'), (14, '任务队列数量'), (15, '日志数据存储空间'), (16, '备份一体机的数据库'), (17, '备份参数'), (18, '网络参数')], default=0),
        ),
        migrations.AlterField(
            model_name='operationlog',
            name='event',
            field=models.PositiveSmallIntegerField(choices=[(0, 'unknown'), (1, '邮件服务器'), (2, '网络设置'), (3, '备份任务管理'), (4, '客户端管理'), (5, '恢复'), (6, '迁移'), (7, '存储管理'), (8, '用户管理'), (9, '系统设置'), (10, '启动介质'), (11, '操作日志'), (12, '客户端日志'), (13, '浏览备份'), (20, '一体机更新'), (21, '服务器驱动更新'), (22, '启动介质数据源更新'), (23, '去重数据更新'), (14, '备份任务策略'), (24, '热备'), (25, '网站防护'), (26, 'PXE设置'), (27, '接管'), (28, '集群备份计划管理'), (29, '远程容灾计划管理'), (30, '免代理'), (31, '微信设置'), (32, '备份数据导出'), (33, '自动验证'), (34, '模板管理')], default=0),
        ),
    ]
