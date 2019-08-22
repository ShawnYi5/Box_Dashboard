# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from django.conf import settings
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('apiv1', '0015_disksnapshot_inc_date_bytes'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AlarmEvent',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, verbose_name='ID', serialize=False)),
                ('last_update_time', models.DateTimeField()),
                ('last_update_uuid', models.TextField()),
                ('strategy_sub_type', models.IntegerField(choices=[(11, '首页篡改检测'), (12, '首页敏感词检测'), (101, '网页篡改检测'), (102, '网页敏感词检测'), (201, '文件篡改检测')])),
                ('detail', models.TextField(default='{}')),
                ('current_status', models.IntegerField(choices=[(1, '待处理'), (2, '手动处理中'), (3, '自动处理中'), (100, '已经修复'), (1000, '确认无风险')])),
            ],
        ),
        migrations.CreateModel(
            name='AlarmEventLog',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, verbose_name='ID', serialize=False)),
                ('event_time', models.DateTimeField(auto_now_add=True)),
                ('strategy_sub_type', models.IntegerField(choices=[(11, '首页篡改检测'), (12, '首页敏感词检测'), (101, '网页篡改检测'), (102, '网页敏感词检测'), (201, '文件篡改检测')])),
                ('book_uuid', models.TextField()),
                ('detail', models.TextField(default='{}')),
                ('log_type', models.IntegerField(choices=[(1, '发现风险'), (2, '风险已移除'), (3, '风险已确认')])),
            ],
        ),
        migrations.CreateModel(
            name='AlarmMethod',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, verbose_name='ID', serialize=False)),
                ('exc_info', models.TextField(default='{"middle_level": {"phone": {"item_list": [], "is_use": true, "frequency": 30}, "email": {"item_list": [], "is_use": true, "frequency": 30}, "sms": {"item_list": [], "is_use": true, "frequency": 30}}, "low_level": {"phone": {"item_list": [], "is_use": true, "frequency": 60}, "email": {"item_list": [], "is_use": true, "frequency": 60}, "sms": {"item_list": [], "is_use": true, "frequency": 60}}, "high_level": {"phone": {"item_list": [], "is_use": true, "frequency": 10}, "email": {"item_list": [], "is_use": true, "frequency": 10}, "sms": {"item_list": [], "is_use": true, "frequency": 10}}}')),
                ('user', models.OneToOneField(to=settings.AUTH_USER_MODEL, related_name='alarm_method')),
            ],
        ),
        migrations.CreateModel(
            name='EmergencyPlan',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, verbose_name='ID', serialize=False)),
                ('name', models.CharField(blank=True, max_length=256)),
                ('enabled', models.BooleanField(default=True)),
                ('deleted', models.BooleanField(default=False)),
                ('exc_info', models.TextField(default='{}')),
                ('timekeeper', models.TextField(default='{}')),
                ('running_tasks', models.TextField(default='{}')),
                ('hosts', models.ManyToManyField(related_name='emergency_plan', to='apiv1.Host')),
            ],
        ),
        migrations.CreateModel(
            name='StrategyGroup',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, verbose_name='ID', serialize=False)),
                ('name', models.CharField(unique=True, max_length=256)),
                ('create_time', models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.CreateModel(
            name='WebGuardStrategy',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, verbose_name='ID', serialize=False)),
                ('name', models.CharField(max_length=256, default='策略')),
                ('enabled', models.BooleanField(default=True)),
                ('deleted', models.BooleanField(default=False)),
                ('last_run_date', models.DateTimeField(null=True, default=None)),
                ('next_run_date', models.DateTimeField(null=True, default=None)),
                ('present_status', models.IntegerField(choices=[(0, '--'), (1, '正常'), (2, '分析中'), (3, '发现篡改风险')], default=0)),
                ('check_type', models.IntegerField(choices=[(1, '首页检测'), (100, '网页检测'), (200, '文件检测')])),
                ('ext_info', models.TextField(default='{}')),
                ('running_task', models.TextField(null=True, default=None)),
                ('task_histories', models.TextField(default='{"tasks":[]}')),
                ('force_credible', models.BooleanField(default=False)),
                ('group', models.ForeignKey(related_name='strategies', to='web_guard.StrategyGroup')),
                ('user', models.ForeignKey(to=settings.AUTH_USER_MODEL, related_name='web_guard_strategies', on_delete=django.db.models.deletion.PROTECT)),
            ],
        ),
        migrations.AddField(
            model_name='emergencyplan',
            name='strategy',
            field=models.ManyToManyField(related_name='emergency_plan', to='web_guard.WebGuardStrategy'),
        ),
        migrations.AddField(
            model_name='emergencyplan',
            name='user',
            field=models.ForeignKey(related_name='emergency_plan', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='alarmeventlog',
            name='strategy',
            field=models.ForeignKey(related_name='alarm_event_logs', to='web_guard.WebGuardStrategy'),
        ),
        migrations.AddField(
            model_name='alarmevent',
            name='strategy',
            field=models.ForeignKey(related_name='alarm_events', to='web_guard.WebGuardStrategy'),
        ),
    ]
