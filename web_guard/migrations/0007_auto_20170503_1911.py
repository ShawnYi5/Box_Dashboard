# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('web_guard', '0006_auto_20170428_1418'),
    ]

    operations = [
        migrations.CreateModel(
            name='ModifyTask',
            fields=[
                ('id', models.AutoField(primary_key=True, verbose_name='ID', serialize=False, auto_created=True)),
                ('start_datetime', models.DateTimeField(auto_now_add=True)),
                ('finish_datetime', models.DateTimeField(null=True, blank=True, default=None)),
                ('expire_datetime', models.DateTimeField(null=True, blank=True, default=None)),
                ('task_uuid', models.CharField(max_length=32, unique=True)),
                ('modify_entry', models.ForeignKey(to='web_guard.ModifyEntry', related_name='modify_task')),
            ],
        ),
        migrations.CreateModel(
            name='UserStatus',
            fields=[
                ('id', models.AutoField(primary_key=True, verbose_name='ID', serialize=False, auto_created=True)),
                ('status', models.IntegerField(default=0, choices=[(0, '登出'), (1, '登录')])),
                ('session_time', models.DateTimeField(auto_now_add=True)),
                ('user', models.OneToOneField(related_name='user_status', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AlterField(
            model_name='alarmmethod',
            name='exc_info',
            field=models.TextField(default='{"high": {"email": {"frequency": 10, "is_use": true, "item_list": []}, "phone": {"frequency": 10, "is_use": true, "item_list": []}, "sms": {"frequency": 10, "is_use": true, "item_list": []}}, "low": {"email": {"frequency": 60, "is_use": true, "item_list": []}, "phone": {"frequency": 60, "is_use": true, "item_list": []}, "sms": {"frequency": 60, "is_use": true, "item_list": []}}, "middle": {"email": {"frequency": 30, "is_use": true, "item_list": []}, "phone": {"frequency": 30, "is_use": true, "item_list": []}, "sms": {"frequency": 30, "is_use": true, "item_list": []}}}'),
        ),
    ]
