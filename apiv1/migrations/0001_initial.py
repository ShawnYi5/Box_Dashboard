# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='BackupTask',
            fields=[
                ('id', models.AutoField(verbose_name='ID', primary_key=True, serialize=False, auto_created=True)),
                ('start_datetime', models.DateTimeField(auto_now_add=True)),
                ('finish_datetime', models.DateTimeField(blank=True, default=None, null=True)),
                ('successful', models.BooleanField(default=False)),
                ('reason', models.PositiveSmallIntegerField(choices=[(0, '未知原因'), (1, '自动执行'), (2, '手动执行')], default=0)),
                ('ext_config', models.TextField(default='{}')),
            ],
        ),
        migrations.CreateModel(
            name='BackupTaskSchedule',
            fields=[
                ('id', models.AutoField(verbose_name='ID', primary_key=True, serialize=False, auto_created=True)),
                ('enabled', models.BooleanField(default=True)),
                ('deleted', models.BooleanField(default=False)),
                ('name', models.CharField(max_length=256)),
                ('backup_source_type', models.IntegerField(choices=[(1, '整机备份'), (2, '数据库备份'), (3, '虚拟平台备份'), (4, '文件备份')])),
                ('cycle_type', models.IntegerField(choices=[(1, 'CDP备份'), (2, '仅备份一次'), (3, '每天'), (4, '每周'), (5, '每月')])),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('plan_start_date', models.DateTimeField(blank=True, default=None, null=True)),
                ('ext_config', models.TextField(default='')),
                ('last_run_date', models.DateTimeField(blank=True, default=None, null=True)),
                ('next_run_date', models.DateTimeField(blank=True, default=None, null=True)),
                ('storage_node_ident', models.CharField(max_length=256)),
            ],
        ),
        migrations.CreateModel(
            name='CDPDiskToken',
            fields=[
                ('id', models.AutoField(verbose_name='ID', primary_key=True, serialize=False, auto_created=True)),
                ('token', models.CharField(unique=True, max_length=32)),
                ('token_expires', models.DateTimeField(blank=True, default=None, null=True)),
                ('keep_alive_interval_seconds', models.IntegerField(default=3600)),
                ('expiry_minutes', models.IntegerField(default=52560000)),
            ],
        ),
        migrations.CreateModel(
            name='CDPTask',
            fields=[
                ('id', models.AutoField(verbose_name='ID', primary_key=True, serialize=False, auto_created=True)),
                ('start_datetime', models.DateTimeField(auto_now_add=True)),
                ('finish_datetime', models.DateTimeField(blank=True, default=None, null=True)),
                ('successful', models.BooleanField(default=False)),
                ('ext_config', models.TextField(default='{}')),
            ],
        ),
        migrations.CreateModel(
            name='Disk',
            fields=[
                ('id', models.AutoField(verbose_name='ID', primary_key=True, serialize=False, auto_created=True)),
                ('ident', models.CharField(unique=True, max_length=32)),
            ],
        ),
        migrations.CreateModel(
            name='DiskSnapshot',
            fields=[
                ('id', models.AutoField(verbose_name='ID', primary_key=True, serialize=False, auto_created=True)),
                ('display_name', models.CharField(blank=True, max_length=256)),
                ('image_path', models.CharField(max_length=256)),
                ('ident', models.CharField(unique=True, max_length=32)),
                ('bytes', models.BigIntegerField()),
                ('type', models.PositiveSmallIntegerField(choices=[(0, 'RAW'), (1, 'MBR'), (2, 'GPT')], default=0)),
                ('boot_device', models.BooleanField()),
                ('parent_timestamp', models.FloatField(blank=True, default=None, null=True)),
                ('merged', models.BooleanField(default=False)),
                ('reference_tasks', models.TextField(default='')),
            ],
        ),
        migrations.CreateModel(
            name='ExternalStorageDeviceConnection',
            fields=[
                ('id', models.AutoField(verbose_name='ID', primary_key=True, serialize=False, auto_created=True)),
                ('ip', models.GenericIPAddressField()),
                ('port', models.IntegerField()),
                ('last_iqn', models.CharField(default='', max_length=256)),
                ('params', models.TextField(default={})),
                ('deleted', models.BooleanField(default=False)),
                ('last_available_datetime', models.DateTimeField()),
            ],
        ),
        migrations.CreateModel(
            name='Host',
            fields=[
                ('id', models.AutoField(verbose_name='ID', primary_key=True, serialize=False, auto_created=True)),
                ('ident', models.CharField(unique=True, max_length=32)),
                ('display_name', models.CharField(blank=True, max_length=256)),
                ('login_datetime', models.DateTimeField(blank=True, null=True)),
                ('last_ip', models.GenericIPAddressField(blank=True, null=True)),
                ('ext_info', models.TextField(default='{}')),
                ('disks', models.ManyToManyField(blank=True, related_name='hosts', to='apiv1.Disk')),
                ('user', models.ForeignKey(blank=True, related_name='hosts', to=settings.AUTH_USER_MODEL, null=True)),
            ],
        ),
        migrations.CreateModel(
            name='HostLog',
            fields=[
                ('id', models.AutoField(verbose_name='ID', primary_key=True, serialize=False, auto_created=True)),
                ('datetime', models.DateTimeField(auto_now_add=True)),
                ('type', models.PositiveSmallIntegerField(choices=[(0, 'unknown'), (1, '连接'), (2, '断开'), (3, '代理程序初始化失败'), (4, '备份开始'), (5, '备份成功'), (6, '备份失败'), (7, '还原开始'), (8, '还原成功'), (9, '还原失败'), (10, 'CDP保护开始'), (11, 'CDP保护停止'), (12, 'CDP保护失败'), (13, 'CDP保护暂停'), (14, 'CDP保护重新开始'), (15, '迁移开始'), (16, '迁移成功'), (17, '迁移失败'), (18, '回收过期数据空间')], default=0)),
                ('reason', models.TextField(blank=True)),
                ('host', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='logs', to='apiv1.Host')),
            ],
        ),
        migrations.CreateModel(
            name='HostMac',
            fields=[
                ('id', models.AutoField(verbose_name='ID', primary_key=True, serialize=False, auto_created=True)),
                ('mac', models.CharField(max_length=12)),
                ('duplication', models.BooleanField(default=False)),
                ('host', models.ForeignKey(related_name='macs', to='apiv1.Host')),
            ],
        ),
        migrations.CreateModel(
            name='HostSnapshot',
            fields=[
                ('id', models.AutoField(verbose_name='ID', primary_key=True, serialize=False, auto_created=True)),
                ('start_datetime', models.DateTimeField(blank=True, default=None, null=True)),
                ('finish_datetime', models.DateTimeField(blank=True, default=None, null=True)),
                ('successful', models.NullBooleanField(default=None)),
                ('deleted', models.BooleanField(default=False)),
                ('ext_info', models.TextField(default='{}')),
                ('display_status', models.TextField(default='')),
                ('deleting', models.BooleanField(default=False)),
                ('is_cdp', models.BooleanField(default=False)),
            ],
        ),
        migrations.CreateModel(
            name='HostSnapshotShare',
            fields=[
                ('id', models.AutoField(verbose_name='ID', primary_key=True, serialize=False, auto_created=True)),
                ('login_user', models.CharField(max_length=32)),
                ('samba_user', models.CharField(max_length=32)),
                ('samba_pwd', models.CharField(max_length=32)),
                ('samba_url', models.CharField(default='', max_length=512)),
                ('share_status', models.TextField(default='')),
                ('host_display_name', models.CharField(default='', max_length=256)),
                ('host_snapshot_type', models.CharField(default='', max_length=32)),
                ('host_start_time', models.CharField(default='', max_length=32)),
                ('host_finish_time', models.DateTimeField(blank=True, default=None, null=True)),
                ('host_snapshot_id', models.IntegerField(default=0)),
                ('dirinfo', models.CharField(unique=True, default='', max_length=512)),
                ('locked_files', models.TextField(default='')),
            ],
        ),
        migrations.CreateModel(
            name='MigrateTask',
            fields=[
                ('id', models.AutoField(verbose_name='ID', primary_key=True, serialize=False, auto_created=True)),
                ('start_datetime', models.DateTimeField(auto_now_add=True)),
                ('finish_datetime', models.DateTimeField(blank=True, default=None, null=True)),
                ('successful', models.BooleanField(default=False)),
                ('source_type', models.PositiveSmallIntegerField(choices=[(0, '未知源类型'), (1, '保留中转数据'), (2, '丢弃中转数据'), (3, '使用CDP数据')], default=0)),
                ('destination_type', models.PositiveSmallIntegerField(choices=[(0, '未知目标类型'), (1, '迁移到在线客户端'), (2, '迁移到离线客户端')], default=0)),
                ('ext_config', models.TextField(default='')),
                ('destination_host', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='migrate_destinations', to='apiv1.Host', null=True)),
            ],
        ),
        migrations.CreateModel(
            name='RestoreTarget',
            fields=[
                ('id', models.AutoField(verbose_name='ID', primary_key=True, serialize=False, auto_created=True)),
                ('ident', models.CharField(unique=True, max_length=32)),
                ('display_name', models.CharField(blank=True, max_length=256)),
                ('start_datetime', models.DateTimeField(blank=True, null=True)),
                ('finish_datetime', models.DateTimeField(blank=True, null=True)),
                ('successful', models.BooleanField(default=False)),
                ('total_bytes', models.BigIntegerField(blank=True, default=None, null=True)),
                ('restored_bytes', models.BigIntegerField(blank=True, default=None, null=True)),
                ('token_expires', models.DateTimeField(blank=True, default=None, null=True)),
                ('keep_alive_interval_seconds', models.IntegerField(default=3600)),
                ('expiry_minutes', models.IntegerField(default=1440)),
                ('info', models.TextField(default='{}')),
                ('type', models.PositiveSmallIntegerField(choices=[(128, '未知类型'), (0, 'PE系统'), (1, 'AGENT系统')], default=128)),
            ],
        ),
        migrations.CreateModel(
            name='RestoreTargetDisk',
            fields=[
                ('id', models.AutoField(verbose_name='ID', primary_key=True, serialize=False, auto_created=True)),
                ('token', models.CharField(unique=True, max_length=32)),
                ('snapshot_timestamp', models.FloatField(blank=True, default=None, null=True)),
                ('pe_host', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='disks', to='apiv1.RestoreTarget')),
            ],
        ),
        migrations.CreateModel(
            name='RestoreTask',
            fields=[
                ('id', models.AutoField(verbose_name='ID', primary_key=True, serialize=False, auto_created=True)),
                ('start_datetime', models.DateTimeField(auto_now_add=True)),
                ('finish_datetime', models.DateTimeField(blank=True, default=None, null=True)),
                ('successful', models.BooleanField(default=False)),
                ('type', models.PositiveSmallIntegerField(choices=[(0, '未知目标类型'), (1, '恢复到在线客户端'), (2, '恢复到离线客户端')], default=0)),
                ('ext_config', models.TextField(default='{}')),
            ],
        ),
        migrations.CreateModel(
            name='SpaceCollectionTask',
            fields=[
                ('id', models.AutoField(verbose_name='ID', primary_key=True, serialize=False, auto_created=True)),
                ('type', models.PositiveSmallIntegerField(choices=[(0, '未知类型'), (1, '删除普通备份点'), (2, '合并普通备份点'), (3, '回收CDP备份主任务'), (4, '回收CDP备份子任务'), (5, '删除快照点'), (6, '删除CDP文件')], default=0)),
                ('start_datetime', models.DateTimeField(auto_now_add=True)),
                ('finish_datetime', models.DateTimeField(blank=True, default=None, null=True)),
                ('ext_info', models.TextField(default='{}')),
            ],
        ),
        migrations.CreateModel(
            name='StorageNode',
            fields=[
                ('id', models.AutoField(verbose_name='ID', primary_key=True, serialize=False, auto_created=True)),
                ('name', models.CharField(unique=True, max_length=256)),
                ('path', models.CharField(unique=True, max_length=256)),
                ('config', models.TextField(default='{}')),
                ('deleted', models.BooleanField(default=False)),
                ('available', models.BooleanField(default=False)),
                ('ident', models.CharField(unique=True, max_length=256)),
                ('internal', models.BooleanField(default=True)),
            ],
        ),
        migrations.CreateModel(
            name='UserLog',
            fields=[
                ('id', models.AutoField(verbose_name='ID', primary_key=True, serialize=False, auto_created=True)),
                ('datetime', models.DateTimeField(auto_now_add=True)),
                ('type', models.PositiveSmallIntegerField(choices=[(0, 'unknown'), (1, '登陆'), (2, '登出'), (3, '创建备份计划'), (4, '修改备份计划'), (5, '删除备份计划')], default=0)),
                ('reason', models.TextField(blank=True)),
                ('user', models.ForeignKey(related_name='logs', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='UserQuota',
            fields=[
                ('id', models.AutoField(verbose_name='ID', primary_key=True, serialize=False, auto_created=True)),
                ('deleted', models.BooleanField(default=False)),
                ('quota_size', models.BigIntegerField()),
                ('caution_size', models.BigIntegerField()),
                ('available_size', models.BigIntegerField()),
                ('ext_info', models.TextField(default='{}')),
                ('storage_node', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='userquotas', to='apiv1.StorageNode')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='userquotas', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='DiskSnapshotCDP',
            fields=[
                ('disk_snapshot', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, serialize=False, to='apiv1.DiskSnapshot', related_name='cdp_info', primary_key=True)),
                ('first_timestamp', models.FloatField()),
                ('last_timestamp', models.FloatField(blank=True, default=None, null=True)),
            ],
        ),
        migrations.CreateModel(
            name='HostSnapshotCDP',
            fields=[
                ('host_snapshot', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, serialize=False, to='apiv1.HostSnapshot', related_name='cdp_info', primary_key=True)),
                ('stopped', models.BooleanField(default=False)),
                ('merged', models.BooleanField(default=False)),
                ('last_datetime', models.DateTimeField(blank=True, default=None, null=True)),
                ('first_datetime', models.DateTimeField(blank=True, default=None, null=True)),
            ],
        ),
        migrations.AddField(
            model_name='spacecollectiontask',
            name='host_snapshot',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='space_collection_tasks', to='apiv1.HostSnapshot', null=True),
        ),
        migrations.AddField(
            model_name='spacecollectiontask',
            name='schedule',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='space_collection_tasks', to='apiv1.BackupTaskSchedule', null=True),
        ),
        migrations.AddField(
            model_name='restoretask',
            name='host_snapshot',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='restores', to='apiv1.HostSnapshot'),
        ),
        migrations.AddField(
            model_name='restoretask',
            name='restore_target',
            field=models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, to='apiv1.RestoreTarget', related_name='restore'),
        ),
        migrations.AddField(
            model_name='restoretask',
            name='target_host',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='restores', to='apiv1.Host', null=True),
        ),
        migrations.AddField(
            model_name='restoretargetdisk',
            name='snapshot',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='restore_target_disks', to='apiv1.DiskSnapshot'),
        ),
        migrations.AddField(
            model_name='migratetask',
            name='host_snapshot',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='migrates', to='apiv1.HostSnapshot', null=True),
        ),
        migrations.AddField(
            model_name='migratetask',
            name='restore_target',
            field=models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, to='apiv1.RestoreTarget', related_name='migrate'),
        ),
        migrations.AddField(
            model_name='migratetask',
            name='source_host',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='migrate_sources', to='apiv1.Host'),
        ),
        migrations.AlterUniqueTogether(
            name='hostsnapshotshare',
            unique_together=set([('host_start_time', 'host_snapshot_id')]),
        ),
        migrations.AddField(
            model_name='hostsnapshot',
            name='host',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='snapshots', to='apiv1.Host'),
        ),
        migrations.AddField(
            model_name='hostsnapshot',
            name='schedule',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='host_snapshots', to='apiv1.BackupTaskSchedule', null=True),
        ),
        migrations.AddField(
            model_name='disksnapshot',
            name='disk',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='snapshots', to='apiv1.Disk'),
        ),
        migrations.AddField(
            model_name='disksnapshot',
            name='host_snapshot',
            field=models.ForeignKey(blank=True, on_delete=django.db.models.deletion.PROTECT, default=None, to='apiv1.HostSnapshot', related_name='disk_snapshots', null=True),
        ),
        migrations.AddField(
            model_name='disksnapshot',
            name='parent_snapshot',
            field=models.ForeignKey(blank=True, on_delete=django.db.models.deletion.PROTECT, related_name='child_snapshots', to='apiv1.DiskSnapshot', null=True),
        ),
        migrations.AddField(
            model_name='cdptask',
            name='host_snapshot',
            field=models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, to='apiv1.HostSnapshot', related_name='cdp_task', null=True),
        ),
        migrations.AddField(
            model_name='cdptask',
            name='schedule',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='cdp_tasks', to='apiv1.BackupTaskSchedule'),
        ),
        migrations.AddField(
            model_name='cdpdisktoken',
            name='last_disk_snapshot',
            field=models.ForeignKey(blank=True, on_delete=django.db.models.deletion.PROTECT, default=None, to='apiv1.DiskSnapshot', related_name='last_token', null=True),
        ),
        migrations.AddField(
            model_name='cdpdisktoken',
            name='parent_disk_snapshot',
            field=models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, to='apiv1.DiskSnapshot', related_name='cdp_token'),
        ),
        migrations.AddField(
            model_name='cdpdisktoken',
            name='task',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='tokens', to='apiv1.CDPTask'),
        ),
        migrations.AddField(
            model_name='cdpdisktoken',
            name='using_disk_snapshot',
            field=models.ForeignKey(blank=True, on_delete=django.db.models.deletion.PROTECT, default=None, to='apiv1.DiskSnapshot', related_name='using_token', null=True),
        ),
        migrations.AddField(
            model_name='backuptaskschedule',
            name='host',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='backup_task_schedules', to='apiv1.Host'),
        ),
        migrations.AddField(
            model_name='backuptask',
            name='host_snapshot',
            field=models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, to='apiv1.HostSnapshot', related_name='backup_task', null=True),
        ),
        migrations.AddField(
            model_name='backuptask',
            name='schedule',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='backup_tasks', to='apiv1.BackupTaskSchedule'),
        ),
        migrations.AddField(
            model_name='disksnapshotcdp',
            name='token',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='files', to='apiv1.CDPDiskToken'),
        ),
    ]
