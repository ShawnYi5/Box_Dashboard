# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('xdashboard', '0025_auto_20170906_1113'),
    ]

    operations = [
        migrations.CreateModel(
            name='sshkvm',
            fields=[
                ('id', models.AutoField(serialize=False, primary_key=True, auto_created=True, verbose_name='ID')),
                ('enablekvm', models.BooleanField(default=False)),
                ('ssh_ip', models.CharField(max_length=16)),
                ('ssh_port', models.IntegerField()),
                ('ssh_key', models.TextField()),
                ('ssh_path', models.CharField(max_length=256)),
                ('aio_ip', models.CharField(max_length=16)),
                ('ssh_os_type', models.CharField(max_length=32)),
            ],
        ),
    ]
