# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('apiv1', '0043_auto_20180523_1144'),
    ]

    operations = [
        migrations.AddField(
            model_name='restoretarget',
            name='user',
            field=models.ForeignKey(blank=True, related_name='pe_hosts', to=settings.AUTH_USER_MODEL, null=True),
        ),
    ]
