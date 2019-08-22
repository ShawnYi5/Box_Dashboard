# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('xdashboard', '0014_taskpolicy_isincipher'),
    ]

    operations = [
        migrations.RenameField(
            model_name='taskpolicy',
            old_name='isincipher',
            new_name='isencipher',
        ),
    ]
