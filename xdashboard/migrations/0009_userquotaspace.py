# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('xdashboard', '0008_userprofile_safeset'),
    ]

    operations = [
        migrations.CreateModel(
            name='UserQuotaSpace',
            fields=[
                ('id', models.AutoField(auto_created=True, verbose_name='ID', serialize=False, primary_key=True)),
                ('quota_id', models.IntegerField()),
                ('free_bytes', models.BigIntegerField(default=0)),
                ('used_bytes', models.BigIntegerField(default=0)),
                ('date_time', models.DateTimeField(auto_now_add=True)),
            ],
        ),
    ]
