# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('xdashboard', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='BackupDataStatt',
            fields=[
                ('id', models.AutoField(verbose_name='ID', primary_key=True, auto_created=True, serialize=False)),
                ('date_time', models.DateTimeField(auto_now_add=True)),
                ('node_id', models.IntegerField(default=-1)),
                ('user_id', models.IntegerField(default=-1)),
                ('original_data_mb', models.IntegerField(default=0)),
                ('backup_data_mb', models.IntegerField(default=0)),
            ],
        ),
        migrations.AddField(
            model_name='devicerunstate',
            name='last_in_total',
            field=models.IntegerField(blank=True, default=0),
        ),
        migrations.AddField(
            model_name='devicerunstate',
            name='last_out_total',
            field=models.IntegerField(blank=True, default=0),
        ),
        migrations.AlterField(
            model_name='datadictionary',
            name='dictType',
            field=models.PositiveSmallIntegerField(choices=[(1, '邮件服务器'), (2, 'session过期时间'), (3, '密码策略'), (4, '选择邮件发送范围'), (5, '一体机更新外网地址')], default=0),
        ),
        migrations.AlterField(
            model_name='devicerunstate',
            name='type',
            field=models.PositiveSmallIntegerField(choices=[(1, '磁盘IO变化'), (2, '网络IO变化'), (3, '其他描述')], default=3),
        ),
    ]
