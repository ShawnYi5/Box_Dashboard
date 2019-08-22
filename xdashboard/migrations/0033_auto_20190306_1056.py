# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('xdashboard', '0032_auto_20181010_1132'),
    ]

    operations = [
        migrations.CreateModel(
            name='audit_task',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False, auto_created=True, verbose_name='ID')),
                ('create_datetime', models.DateTimeField()),
                ('audit_datetime', models.DateTimeField(auto_now_add=True)),
                ('status', models.IntegerField(choices=[(1, '待审批'), (2, '批准'), (3, '拒绝')])),
                ('task_info', models.TextField(default='{}')),
                ('audit_info', models.TextField(default='{}')),
                ('audit_user', models.ForeignKey(to=settings.AUTH_USER_MODEL, blank=True, related_name='audit_user_audit_tasks', null=True)),
                ('create_user', models.ForeignKey(to=settings.AUTH_USER_MODEL, blank=True, related_name='create_user_audit_tasks', null=True)),
            ],
        ),
        migrations.AlterField(
            model_name='email',
            name='type',
            field=models.PositiveSmallIntegerField(default=-1, choices=[(1, '用户配额不足'), (2, '存储结点离线'), (3, '存储结点不可用'), (4, 'CDP保护停止'), (5, 'CDP保护失败'), (6, 'CDP保护暂停'), (7, '系统时间错误'), (8, '备份失败'), (9, '备份成功'), (10, '迁移失败'), (11, '迁移成功'), (12, '还原失败'), (13, '还原成功'), (14, '日志空间告警'), (15, '代理程序初始化失败')]),
        ),
        migrations.AlterField(
            model_name='operationlog',
            name='event',
            field=models.PositiveSmallIntegerField(default=0, choices=[(0, 'unknown'), (1, '邮件服务器'), (2, '网络设置'), (3, '备份任务管理'), (4, '客户端管理'), (5, '恢复'), (6, '迁移'), (7, '存储管理'), (8, '用户管理'), (9, '系统设置'), (10, '启动介质'), (11, '操作日志'), (12, '客户端日志'), (13, '浏览备份'), (20, '一体机更新'), (21, '服务器驱动更新'), (22, '启动介质数据源更新'), (23, '去重数据更新'), (14, '备份任务策略'), (24, '热备'), (25, '网站防护'), (26, 'PXE设置'), (27, '接管'), (28, '集群备份计划管理'), (29, '远程容灾计划管理'), (30, '免代理'), (31, '微信设置'), (32, '备份数据导出')]),
        ),
        migrations.AlterField(
            model_name='userprofile',
            name='user_type',
            field=models.CharField(default='normal-admin', choices=[('normal-admin', '系统管理员'), ('content-admin', '内容管理员'), ('aud-admin', '安全审计管理员'), ('audit-admin', '验证/恢复审批管理员')], max_length=256),
        ),
    ]
