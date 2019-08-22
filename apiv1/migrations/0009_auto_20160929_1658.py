# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0008_remove_tunnel_deleted'),
    ]

    operations = [
        migrations.AlterField(
            model_name='tunnel',
            name='user',
            field=models.ForeignKey(to=settings.AUTH_USER_MODEL, related_name='host_tunnels', null=True, on_delete=django.db.models.deletion.PROTECT),
        ),
    ]
