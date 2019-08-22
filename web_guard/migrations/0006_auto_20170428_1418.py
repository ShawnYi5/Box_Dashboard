# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0015_disksnapshot_inc_date_bytes'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('web_guard', '0005_auto_20170425_1442'),
    ]

    operations = [
        migrations.CreateModel(
            name='ModifyEntry',
            fields=[
                ('id', models.AutoField(verbose_name='ID', auto_created=True, serialize=False, primary_key=True)),
                ('entrance', models.URLField(max_length=256)),
                ('modify_admin', models.ManyToManyField(related_name='modify_entries', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='WGRestoreTask',
            fields=[
                ('id', models.AutoField(verbose_name='ID', auto_created=True, serialize=False, primary_key=True)),
                ('start_datetime', models.DateTimeField(auto_now_add=True)),
                ('finish_datetime', models.DateTimeField(blank=True, default=None, null=True)),
                ('task_uuid', models.TextField(default='{}')),
                ('uni_ident', models.TextField()),
                ('exc_info', models.TextField(default='{}')),
                ('restore_info', models.TextField(default='{}')),
                ('plan', models.ForeignKey(related_name='restore_tasks', to='web_guard.EmergencyPlan', default=None, null=True)),
                ('task', models.OneToOneField(to='apiv1.RestoreTask', default=None, null=True, related_name='web_guard_task_info')),
            ],
        ),
        migrations.AddField(
            model_name='webguardstrategy',
            name='use_history',
            field=models.BooleanField(default=True),
        ),
        migrations.AlterField(
            model_name='alarmeventlog',
            name='log_type',
            field=models.IntegerField(choices=[(1, '发现风险'), (2, '风险已移除'), (3, '风险已确认'), (4, '已进行系统还原')]),
        ),
        migrations.AlterField(
            model_name='alarmmethod',
            name='exc_info',
            field=models.TextField(default='{"low": {"sms": {"is_use": true, "frequency": 60, "item_list": []}, "email": {"is_use": true, "frequency": 60, "item_list": []}, "phone": {"is_use": true, "frequency": 60, "item_list": []}}, "high": {"sms": {"is_use": true, "frequency": 10, "item_list": []}, "email": {"is_use": true, "frequency": 10, "item_list": []}, "phone": {"is_use": true, "frequency": 10, "item_list": []}}, "middle": {"sms": {"is_use": true, "frequency": 30, "item_list": []}, "email": {"is_use": true, "frequency": 30, "item_list": []}, "phone": {"is_use": true, "frequency": 30, "item_list": []}}}'),
        ),
        migrations.AlterField(
            model_name='webguardstrategy',
            name='present_status',
            field=models.IntegerField(choices=[(0, '--'), (1, '正常'), (2, '分析中'), (3, '发现篡改风险'), (4, '已切换为应急页面'), (5, '确认篡改风险为可信')], default=0),
        ),
        migrations.AddField(
            model_name='modifyentry',
            name='monitors',
            field=models.ManyToManyField(related_name='modify_entries', to='web_guard.WebGuardStrategy'),
        ),
    ]
