# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('xdashboard', '0033_auto_20190306_1056'),
    ]

    operations = [
        migrations.CreateModel(
            name='ForceInstallDriver',
            fields=[
                ('id', models.AutoField(serialize=False, primary_key=True, verbose_name='ID', auto_created=True)),
                ('device_id', models.CharField(max_length=256)),
                ('driver_id', models.CharField(max_length=256)),
                ('sys_type', models.CharField(max_length=256)),
                ('user', models.ForeignKey(to=settings.AUTH_USER_MODEL, related_name='force_install_driver')),
            ],
        ),
        migrations.AlterField(
            model_name='tmpdictionary',
            name='dictType',
            field=models.PositiveSmallIntegerField(choices=[(1, '更改密码链接'), (2, '免密码登录并重定向到指定页面的链接')], default=1),
        ),
    ]
