"""box_dashboard URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/1.8/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  url(r'^$', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  url(r'^$', Home.as_view(), name='home')
Including another URLconf
    1. Add a URL to urlpatterns:  url(r'^blog/', include('blog.urls'))
"""
from django.conf.urls import include, url
from django.contrib import admin
from django.views.generic import RedirectView

from apiv1 import views, remote_views, vmware_logic, archive_views
from box_dashboard import settings
from web_guard import views as web_guard_views
from web_guard.content_mgr import UserStatusView
from xdashboard import views as dashboad_views
from xdashboard.handle import UsedForZhongYeYun

urlpatterns = [
    url(r'^robots\.txt$', dashboad_views.robotsview),
    url(r'^favicon\.ico$', RedirectView.as_view(url='/static/images/favicon.ico')),
    url(r'^$', dashboad_views.indexview),
    url(r'^admin/', include(admin.site.urls)),
    url(r'^api-auth/', include('rest_framework.urls', namespace='rest_framework')),
    url(r'^redirect/', dashboad_views.redirect),
    url(r'^xdashboard/([a-z]*)(_?[a-z]*)/$', dashboad_views.commonview),
    url(r'^fun_htmls/', dashboad_views.get_fun_htmls),

    # get 列出所有主机
    #   remark：超级管理员用户可列举所有主机，普通用户仅能访问属于自己的主机
    #   参考 HostSerializer
    # post 上传主机的mac列表，返回一个可用的主机标识号
    #   参考 HostCreateSerializer
    url(r'^apiv1/hosts/$', views.Hosts.as_view()),

    # get 列出指定主机的信息
    #   参考 HostSerializer
    # put 更新主机信息（名称，从属用户）
    #   参考 HostInfoAlterSerializer
    url(r'^apiv1/hosts/(?P<ident>.{32})/$', views.HostInfo.as_view()),

    # get 列出当前处于连接状态的主机
    #   remark：超级管理员用户可列举所有主机，普通用户仅能访问属于自己的主机
    #   参考 HostSerializer
    # post 主机的标识符和ip，返回是否连接成功的状态，如果连接失败，需要重新申请一个可用的主机识别号
    #   参考 HostLoginSerializer
    # delete 离线所有主机
    url(r'^apiv1/hosts/sessions/$', views.HostSessions.as_view()),

    # get 列出指定主机的信息(在线的)
    #   参考 HostSessionSerializer
    # delete 离线该主机
    # put 提交指定主机的Agent初始化错误信息
    #   参考 AgentModuleErrorSerializer
    url(r'^apiv1/hosts/sessions/(?P<ident>.{32})/$', views.HostSessionInfo.as_view()),

    # get 列出指定主机的磁盘信息
    #   参考 HostSessionDiskSerializer
    url(r'^apiv1/hosts/sessions/(?P<ident>.{32})/disks/$', views.HostSessionDisks.as_view()),

    # put 上传备份的进度
    #   参考 HostSessionBackupProgressSerializer
    # delete 报告备份完成
    #   /?code=xxx
    url(r'^apiv1/hosts/sessions/(?P<ident>.{32})/backup/$', views.HostSessionBackup.as_view()),

    # post 开始迁移
    url(r'^apiv1/hosts/sessions/(?P<ident>.{32})/migrate/$', views.HostSessionMigrate.as_view()),

    # # get 列出所有发送过还原命令的还原目标客户端列表（含主要信息的摘要）
    # url(r'^apiv1/pe_hosts/$', views.PeHosts.as_view()),

    # # get 获取还原状态、进度
    # url(r'^apiv1/pe_hosts/(?P<ident>.{32})/$', views.PeHostInfo.as_view()),

    # get 列出当前未发送过命令的还原目标客户端列表
    #   参考 PeHostSessionSerializer
    # post 上传主机的硬件信息列表，返回创建一个可用的PE标识
    #   参考  PeHostSessionLoginSerializer
    #   返回值 PeHostSessionSerializer
    # delete 注销所有处于连接状态的还原目标客户端
    url(r'^apiv1/pe_hosts/sessions/$', views.PeHostSessions.as_view()),

    # get 列出指定在线状态还原目标客户端的信息
    # 参考 PeHostSessionDetailSerializer
    # delete 该主机离线
    url(r'^apiv1/pe_hosts/sessions/(?P<ident>.{32})/$', views.PeHostSessionInfo.as_view()),

    # put 开始KVM阶段
    url(r'^apiv1/pe_hosts/sessions/(?P<ident>.{32})/restore/$', views.PeHostSessionRestore.as_view()),

    # put 还原到Agent时候，报告还原状态
    url(r'^apiv1/pe_hosts/sessions/(?P<ident>.{32})/volume_restore/$', views.PeHostSessionVolumeRestore.as_view()),

    # get 获取主机指定的快照信息
    #   参考 HostSnapshotSerializer
    url(r'^apiv1/host_snapshots/(?P<host_snapshot_id>[0-9]+)/$', views.HostSnapshotInfo.as_view()),

    # post 开始还原主机快照
    #   參考 HostSnapshotRestoreSerializer
    url(r'^apiv1/host_snapshots/(?P<host_snapshot_id>[0-9]+)/restore/$', views.HostSnapshotRestore.as_view()),

    # post 自动本机还原主机快照
    #   參考 HostSnapshotLocalRestoreSerializer
    url(r'^apiv1/host_snapshots/(?P<host_snapshot_id>[0-9]+)/localrestore/$', views.HostSnapshotLocalRestore.as_view()),

    # get 获取某一台主机的普通快照列表
    #   参考 HostSnapshotSerializer
    url(r'^apiv1/host_snapshots/(?P<ident>.{32})/normal/$', views.HostSnapshotsWithNormalPerHost.as_view()),

    # get 获取某一台主机的CDP快照列表
    url(r'^apiv1/host_snapshots/(?P<ident>.{32})/cdp/$', views.HostSnapshotsWithCdpPerHost.as_view()),

    # get 请求更新cdp token
    # delete 暂停cdp token
    url(r'^apiv1/cdps/(?P<token>.{32})/$', views.CdpTokenInfo.as_view()),

    # get 请求更新cdp token的流量限制
    url(r'^apiv1/cdps/(?P<token>.{32})/tc/$', views.CdpTokenTc.as_view()),

    # get 请求更新token信息
    # put 上传还原的进度（简化处理，实际上提交的是“token对应的host”的百分比，是否成功完成，是否发生错误）
    # post 表示该token对应的还原目标机, 在自动重启后, 成功连接上了"kernel tcp"  (kernel tcp --> ice --> logicService --> here)
    url(r'^apiv1/tokens/(?P<token>.{32})/$', views.TokenInfo.as_view()),

    # get 通过restore_token查询信息
    url(r'^apiv1/tokens/(?P<token>.{32})/detail/$', views.TokenInfoDetail.as_view()),

    # get 通过cdp_token查询信息
    url(r'^apiv1/tokens/(?P<token>.{32})/detailByCdp/$', views.TokenInfoDetailByCdpToken.as_view()),

    # get 获得所有任务计划
    #   参考 BackupTaskScheduleSerializer
    #   remark：超级管理员用户可列举所有未删除的计划，普通用户仅能访问属于自己主机的未删除计划
    # post 创建任务计划
    #   参考 BackupTaskScheduleCreateSerializer
    #   返回值：参考 BackupTaskScheduleSerializer
    url(r'^apiv1/backup_task_schedules/$', views.BackupTaskSchedules.as_view()),

    # get 获得计划任务的设置
    #   参考 BackupTaskScheduleSerializer
    # put 更新计划任务设置
    #   参考 BackupTaskScheduleUpdateSerializer
    #   返回值：参考 BackupTaskScheduleSerializer
    #   remark: CDP模式不可转换为非CDP模式，反之亦然
    # delete 删除计划任务
    #   删除计划任务会导致该计划任务产生的备份点被“立刻”删除（合并）
    url(r'^apiv1/backup_task_schedules/(?P<backup_task_schedule_id>[0-9]+)/$',
        views.BackupTaskScheduleSetting.as_view()),

    # post 执行计划任务
    #   remark: 当调用者为superuser时被判定为自动执行，否则为手动执行
    #   返回状态：HTTP429 表示计划正在执行中 或者 主机正在执行其他任务
    url(r'^apiv1/backup_task_schedules/(?P<backup_task_schedule_id>[0-9]+)/execute/$',
        views.BackupTaskScheduleExecute.as_view()),

    # get 获取Host所有任务(NormBackupTasks, MigrationTasks, RestoreTasks)的一个统计情况
    url(r'^apiv1/host_tasks_statt/(?P<ident>.{32})/$', views.HostTasksStatt.as_view()),

    # post 执行集群cdp计划
    url(r'^apiv1/cluster_cdp_task_schedules/(?P<schedule_id>[0-9]+)/execute/$',
        views.ClusterCdpBackupTaskScheduleExecute.as_view()),

    # get 获取所有已添加的存储节点信息
    #   参考 StorageNodeSerializer
    # post 添加并初始化所有内部存储
    #   remark:
    #       需要post的数据为 '{"key" : "kQidpmnknzvGrqpkbh7y7Vsnm5zbadvq"}', 且调用者为superuser时有效
    url(r'^apiv1/storage_nodes/$', views.StorageNodes.as_view()),

    # get 获取所有内部存储节点（含未添加的）
    #   参考 StorageNodePerDeviceSerializer
    # post 添加一个内部存储节点
    #   参考 AddStorageNodeSerializer
    #   remark：
    #       当status为STORAGE_NODE_STATUS_INIT_BY_SELF时，默认为不格式化设备。其余情况则默认格式化设备
    #       当有重名时，返回 HTTP409
    url(r'^apiv1/storage_nodes/internal/$', views.InternalStorageNodes.as_view()),

    # post 登陆一个外部存储
    #   参考 AddExternalStorageDeviceSerializer
    #   remark：
    #       如果该外部存储已经登陆过，当force为False时会返回429，为True则会断开已有链接
    #   返回值：
    #       {"id":device_id_number, "iqn":iqn_string}
    #       刷新与添加外部存储节点时使用
    url(r'^apiv1/storage_nodes/external_device/$', views.ExternalStorageDevices.as_view()),

    # get 获取外部存储中的存储节点
    #   参考 StorageNodePerDeviceSerializer
    #   /?refresh=True 刷新设备信息 /?refresh=False 不刷新设备信息
    # post 添加一个外部存储节点
    #   参考 AddStorageNodeSerializer
    #   remark：
    #       当status为STORAGE_NODE_STATUS_INIT_BY_SELF时，默认为不格式化设备。其余情况则默认格式化设备
    #       当有重名时，返回 HTTP409
    # delete 移除外部存储节点
    #   /?timeouts=seconds 多久没有处于活跃的节点，默认为360s（1h）
    #   remark:
    #       仅当该设备上没有任何正在使用的存储节点时才能返回成功，否则返回失败
    url(r'^apiv1/storage_nodes/external_device/(?P<device_id>[0-9]+)/$', views.ExternalStorageDeviceInfo.as_view()),

    # put 修改存储节点信息
    #   参考 AlterStorageNodeInfo
    #   remark:
    #       当有重名时，返回 HTTP409
    # delete 移除存储节点
    # post 执行指定操作
    #   参考 DealStorageNode
    #   remark:
    #       rm 操作异步执行
    url(r'^apiv1/storage_nodes/(?P<node_id>[0-9]+)/$', views.StorageNodeInfo.as_view()),

    # root用户创建普通用户，并分配备份机
    url(r'^apiv1/create_norm_user/$', views.CreateNormUser.as_view()),

    # post add new share
    url(r'^apiv1/shared_host_snapshots/add/$', views.HostSnapshotShareAdd.as_view()),

    # get get all shared_disk_snapshots  remark: user
    url(r'^apiv1/shared_host_snapshots/$', views.HostSnapshotShareQuery.as_view()),

    # delete stop share
    url(r'^apiv1/shared_host_snapshots/del/(?P<shared_host_snapshot_id>[0-9]+)/$',
        views.HostSnapshotShareDelete.as_view()),

    # delete stop share
    url(r'^apiv1/shared_host_snapshots/delhost/(?P<host_id>[0-9]+)/$',
        views.HostSnapshotShareHostDelete.as_view()),

    # delete share user
    url(r'^apiv1/shared_host_snapshots/deluser/(?P<samba_user>.+)/$',
        views.HostSnapshotShareUserDelete.as_view()),

    # post 初始创建，某节点用户配额信息
    # get 获取，某节点用户配额信息
    # put 编辑，某节点用户配额信息
    # delete 删除，某节点用户配额信息
    url(r'^apiv1/user_quota_manage/$', views.QuotaManage.as_view()),

    # 还原某快照点时，检查Pe的硬件信息，返回无匹配驱动的硬件
    url(r'^apiv1/check_target_hardware/(?P<snapshot_id>[0-9]+)/(?P<pe_ident>.{32})/$', views.TargetHardware.as_view()),

    # 获取客户端的调试信息
    url(r'^apiv1/logs/$', views.AgentLogs.as_view()),

    # 隧道管理
    url(r'^apiv1/tunnels_manage/$', views.TunnelManage.as_view()),

    # post 开始执行检测策略
    url(r'^web_guard/strategies/(?P<strategy_id>[0-9]+)/run/$', web_guard_views.StrategyExecute.as_view()),

    # get 获取主机页面保护状态
    # put 切换主机页面保护状态
    url(r'^web_guard/maintain/(?P<ident>.{32})/$', web_guard_views.MaintainStatus.as_view()),

    # get 获取主机页面保护状态配置
    # put 修改主机页面保护状态配置
    url(r'^web_guard/maintain/(?P<ident>.{32})/config/$', web_guard_views.MaintainConfig.as_view()),

    # post 开始执行检测策略
    url(r'^web_guard/emergency_plans/(?P<plan_id>[0-9]+)/run/$', web_guard_views.EmergencyPlanExecute.as_view()),

    # get 获取可供操作的内容修改入口, 对于内容管理员
    url(r'^web_guard/get_modify_entry/$', web_guard_views.GetModifyEntry.as_view()),

    # post 启动修改任务
    url(r'^web_guard/modify_entry_tasks/$', web_guard_views.ModifyEntryTasks.as_view()),

    # get 更新某个修改任务的信息（剩余时间等）
    # delete 停止某个修改任务
    url(r'^web_guard/modify_entry_tasks/(?P<task_uuid>\w{32})/$', web_guard_views.ModifyEntryTaskInfo.as_view()),

    # get 不断更新 user session
    url(r'^web_guard/update_session/', UserStatusView.as_view()),

    # post 测试用自定义
    url(r'^module_test/', views.ModuleTestView.as_view()),

    # post 推送快照数据上报状态
    url(r'^apiv1/data_queuing_report/', views.DataQueuingReportView.as_view()),

    # 远程灾备
    url(r'^apiv1/remote_backup/', remote_views.RemoteBackupView.as_view()),

    # 免代理备份
    url(r'^apiv1/vmware_agent_report/', vmware_logic.VmwareAgentReport.as_view()),

    # django, session
    url(r'^django/sessions/$', dashboad_views.django_sessions),

    # 根据磁盘token获取对应的hash文件
    url(r'^apiv1/token/(?P<token>.{32})/hashfile', views.Token2HashFile.as_view()),
    # 中冶平台返回地址
    url(r'^oauth/sessions/$', UsedForZhongYeYun.token_for_zy_oauth),
    # 中冶云 login 接口：
    url(r'^aio/login/$', UsedForZhongYeYun.user_login_from_oauth),

    # 上传任务状态
    url(r'^apiv1/task/progress/$', archive_views.TaskProgressReport.as_view()),

]

if settings.DEBUG:
    urlpatterns += [
        url(r'^(?:index.html)?$', 'django.contrib.staticfiles.views.serve', kwargs={'path': 'index.html'}),
        url(r'^(?P<path>(?:js|css|img|libs|static)/.*)$', 'django.contrib.staticfiles.views.serve'),
        url(r'^$', 'django.contrib.staticfiles.views.serve',
            kwargs={'path': 'index.html', 'document_root': settings.STATIC_ROOT}),
    ]
