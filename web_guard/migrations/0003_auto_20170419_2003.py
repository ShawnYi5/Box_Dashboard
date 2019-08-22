# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('web_guard', '0002_auto_20170414_1517'),
    ]

    operations = [
        migrations.AddField(
            model_name='webguardstrategy',
            name='last_404_date',
            field=models.DateTimeField(default=None, null=True),
        ),
    ]
