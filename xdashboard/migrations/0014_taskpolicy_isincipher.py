# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('xdashboard', '0013_auto_20160929_1359'),
    ]

    operations = [
        migrations.AddField(
            model_name='taskpolicy',
            name='isincipher',
            field=models.IntegerField(default=0),
        ),
    ]
