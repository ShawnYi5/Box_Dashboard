# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0048_auto_20181130_1618'),
    ]

    operations = [
        migrations.CreateModel(
            name='FileBackupTask',
            fields=[
                ('id', models.AutoField(primary_key=True, verbose_name='ID', serialize=False, auto_created=True)),
                ('start_datetime', models.DateTimeField(blank=True, null=True, default=None)),
                ('finish_datetime', models.DateTimeField(blank=True, null=True, default=None)),
                ('successful', models.BooleanField(default=False)),
                ('ext_config', models.TextField(default='{}')),
                ('task_uuid', models.CharField(max_length=32)),
                ('running_task', models.TextField(default='{}')),
                ('status', models.PositiveSmallIntegerField(choices=[(0, '初始化参数'), (1, '查询快照文件'), (2, '锁定快照文件'), (3, '发送备份指令'), (4, '传输数据'), (5, '任务成功'), (6, '任务失败')], default=0)),
                ('force_full', models.BooleanField(default=False)),
                ('reason', models.PositiveSmallIntegerField(choices=[(0, '未知原因'), (1, '自动执行'), (2, '手动执行')], default=0)),
                ('host_snapshot', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='file_backup_task', null=True, to='apiv1.HostSnapshot')),
                ('schedule', models.ForeignKey(null=True, related_name='archive_tasks', to='apiv1.BackupTaskSchedule')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.AlterField(
            model_name='host',
            name='type',
            field=models.BigIntegerField(choices=[(0, '普通客户端'), (1, '远程客户端'), (2, '免代理客户端'), (3, '数据导入'), (4, 'NAS客户端')], default=0),
        ),
    ]
