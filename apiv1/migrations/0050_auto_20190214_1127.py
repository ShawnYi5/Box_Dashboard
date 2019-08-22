# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('apiv1', '0049_auto_20181205_1552'),
    ]

    operations = [
        migrations.CreateModel(
            name='GroupBackupTaskSchedule',
            fields=[
                ('id', models.AutoField(verbose_name='ID', primary_key=True, auto_created=True, serialize=False)),
                ('name', models.CharField(max_length=256)),
                ('type', models.IntegerField(choices=[(1, '整机备份计划')])),
                ('enabled', models.BooleanField(default=True)),
            ],
        ),
        migrations.CreateModel(
            name='HostGroup',
            fields=[
                ('id', models.AutoField(verbose_name='ID', primary_key=True, auto_created=True, serialize=False)),
                ('name', models.CharField(max_length=256)),
                ('hosts', models.ManyToManyField(related_name='groups', to='apiv1.Host', blank=True)),
                ('user', models.ForeignKey(null=True, related_name='usergroup', to=settings.AUTH_USER_MODEL, blank=True)),
            ],
        ),
        migrations.AddField(
            model_name='groupbackuptaskschedule',
            name='host_group',
            field=models.ForeignKey(null=True, related_name='hostgroup', to='apiv1.HostGroup', blank=True),
        ),
        migrations.AddField(
            model_name='groupbackuptaskschedule',
            name='schedules',
            field=models.ManyToManyField(related_name='schedules', to='apiv1.BackupTaskSchedule', blank=True),
        ),
        migrations.AddField(
            model_name='groupbackuptaskschedule',
            name='user',
            field=models.ForeignKey(null=True, related_name='usergroupchedule', to=settings.AUTH_USER_MODEL, blank=True),
        ),
    ]
