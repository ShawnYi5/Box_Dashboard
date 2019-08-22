# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('apiv1', '0005_hostsnapshotshare_share_start_time'),
    ]

    operations = [
        migrations.CreateModel(
            name='Tunnel',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False, auto_created=True, verbose_name='ID')),
                ('name', models.CharField(max_length=256, default='')),
                ('host_ip', models.GenericIPAddressField()),
                ('host_port', models.IntegerField()),
                ('deleted', models.BooleanField(default=False)),
                ('create_datetime', models.DateTimeField(auto_now_add=True)),
                ('host', models.OneToOneField(null=True, related_name='tunnel', default=None, on_delete=django.db.models.deletion.PROTECT, to='apiv1.Host', blank=True)),
                ('user', models.ForeignKey(related_name='host_tunnels', to=settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.PROTECT)),
            ],
        ),
    ]
