# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('xdashboard', '0012_auto_20160923_1631'),
    ]

    operations = [
        migrations.CreateModel(
            name='cdpcycle',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False, verbose_name='ID', auto_created=True)),
                ('cdpperiod', models.IntegerField()),
                ('cdptype', models.IntegerField()),
            ],
        ),
        migrations.CreateModel(
            name='everydaycycle',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False, verbose_name='ID', auto_created=True)),
                ('starttime', models.DateTimeField()),
                ('timeinterval', models.IntegerField()),
            ],
        ),
        migrations.CreateModel(
            name='everymonthcycle',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False, verbose_name='ID', auto_created=True)),
                ('starttime', models.DateTimeField()),
                ('monthly', models.IntegerField()),
            ],
        ),
        migrations.CreateModel(
            name='everyweekcycle',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False, verbose_name='ID', auto_created=True)),
                ('starttime', models.DateTimeField()),
                ('perweek', models.IntegerField()),
            ],
        ),
        migrations.CreateModel(
            name='onlyonecycle',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False, verbose_name='ID', auto_created=True)),
                ('starttime', models.DateTimeField()),
            ],
        ),
        migrations.CreateModel(
            name='taskpolicy',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False, verbose_name='ID', auto_created=True)),
                ('name', models.CharField(max_length=256)),
                ('retentionperiod', models.IntegerField()),
                ('keepingpoint', models.IntegerField()),
                ('cleandata', models.IntegerField()),
                ('usemaxbandwidth', models.IntegerField()),
                ('maxbandwidth', models.IntegerField()),
                ('cycletype', models.IntegerField()),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='taskuserid', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AlterField(
            model_name='operationlog',
            name='event',
            field=models.PositiveSmallIntegerField(choices=[(0, 'unknown'), (1, '邮件服务器'), (2, '网络设置'), (3, '备份任务管理'), (4, '客户端管理'), (5, '恢复'), (6, '迁移'), (7, '存储管理'), (8, '用户管理'), (9, '系统设置'), (10, '启动介质'), (11, '操作日志'), (12, '客户端日志'), (13, '浏览备份'), (20, '一体机更新'), (21, '服务器驱动更新'), (22, '启动介质数据源更新'), (23, '去重数据更新'), (14, '备份任务策略')], default=0),
        ),
        migrations.AddField(
            model_name='onlyonecycle',
            name='taskpolicy',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='onlyonecycletaskpolicyid', to='xdashboard.taskpolicy'),
        ),
        migrations.AddField(
            model_name='everyweekcycle',
            name='taskpolicy',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='everyweekcycletaskpolicyid', to='xdashboard.taskpolicy'),
        ),
        migrations.AddField(
            model_name='everymonthcycle',
            name='taskpolicy',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='everymonthcycletaskpolicyid', to='xdashboard.taskpolicy'),
        ),
        migrations.AddField(
            model_name='everydaycycle',
            name='taskpolicy',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='everydaycycletaskpolicyid', to='xdashboard.taskpolicy'),
        ),
        migrations.AddField(
            model_name='cdpcycle',
            name='taskpolicy',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='cdpcycletaskpolicyid', to='xdashboard.taskpolicy'),
        ),
    ]
