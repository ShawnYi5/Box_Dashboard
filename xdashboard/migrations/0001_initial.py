# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from django.conf import settings
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='DataDictionary',
            fields=[
                ('id', models.AutoField(verbose_name='ID', primary_key=True, serialize=False, auto_created=True)),
                ('dictType', models.PositiveSmallIntegerField(choices=[(1, '邮件服务器'), (2, 'session过期时间'), (3, '密码策略'), (4, '选择邮件发送范围')], default=0)),
                ('dictKey', models.CharField(max_length=10)),
                ('dictValue', models.CharField(max_length=256)),
            ],
        ),
        migrations.CreateModel(
            name='DeviceRunState',
            fields=[
                ('id', models.AutoField(verbose_name='ID', primary_key=True, serialize=False, auto_created=True)),
                ('datetime', models.DateTimeField(auto_now_add=True)),
                ('type', models.PositiveSmallIntegerField(choices=[(1, '磁盘IO变化'), (2, '网络IO变化'), (3, '设备存储状态')])),
                ('writevalue', models.IntegerField(default=0)),
                ('readvalue', models.IntegerField(default=0)),
            ],
        ),
        migrations.CreateModel(
            name='Email',
            fields=[
                ('id', models.AutoField(verbose_name='ID', primary_key=True, serialize=False, auto_created=True)),
                ('type', models.PositiveSmallIntegerField(choices=[(1, '用户配额不足'), (2, '存储结点离线'), (3, '存储结点不可用')], default=-1)),
                ('datetime', models.DateTimeField(auto_now_add=True)),
                ('content', models.TextField()),
                ('times', models.IntegerField()),
                ('is_successful', models.BooleanField(default=False)),
            ],
        ),
        migrations.CreateModel(
            name='OperationLog',
            fields=[
                ('id', models.AutoField(verbose_name='ID', primary_key=True, serialize=False, auto_created=True)),
                ('event', models.PositiveSmallIntegerField(choices=[(0, 'unknown'), (1, '邮件服务器'), (2, '网络设置'), (3, '备份任务管理'), (4, '客户端管理'), (5, '恢复'), (6, '迁移'), (7, '存储管理'), (8, '用户管理')], default=0)),
                ('datetime', models.DateTimeField(auto_now_add=True)),
                ('desc', models.TextField()),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='userid', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='TmpDictionary',
            fields=[
                ('id', models.AutoField(verbose_name='ID', primary_key=True, serialize=False, auto_created=True)),
                ('dictType', models.PositiveSmallIntegerField(choices=[(1, '更改密码链接')], default=1)),
                ('dictKey', models.CharField(max_length=10)),
                ('dictValue', models.CharField(max_length=256)),
                ('expireTime', models.DateTimeField()),
            ],
        ),
        migrations.CreateModel(
            name='UserProfile',
            fields=[
                ('id', models.AutoField(verbose_name='ID', primary_key=True, serialize=False, auto_created=True)),
                ('modules', models.IntegerField(null=True)),
                ('desc', models.TextField(blank=True)),
                ('winpeset', models.TextField(blank=True)),
                ('user', models.OneToOneField(to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
