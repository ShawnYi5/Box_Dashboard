# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('xdashboard', '0020_taskpolicy_isdup'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='user_type',
            field=models.CharField(default='normal-admin', max_length=256, choices=[('normal-admin', '系统管理员'), ('content-admin', '内容管理员')]),
        ),
    ]
