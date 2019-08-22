import json
from box_dashboard import xlogging
from apiv1.models import HostSnapshot
from apiv1.models import HostLog
from xdashboard.models import UserProfile
from xdashboard.common.smtp import send_mail

_logger = xlogging.getLogger(__name__)


def save_host_log(host, log_type, reason):
    HostLog.objects.create(host=host, type=log_type, reason=json.dumps(reason, ensure_ascii=False))


def notify_audits(create_user, host_name, msg_time, task_info):
    from xdashboard.handle.version import getProductName
    from xdashboard.handle.audittask import get_approved_task_host_snapshot_id
    from xdashboard.handle.audittask import get_host_log_type
    msg_type, host_snapshot_id = get_approved_task_host_snapshot_id(task_info)
    if msg_type is None:
        return

    host_snapshot_obj = HostSnapshot.objects.filter(id=host_snapshot_id)
    if host_snapshot_obj:
        host_snapshot_obj = host_snapshot_obj.first()
        host = host_snapshot_obj.host
        info = '{}的整机备份{}'.format(host_snapshot_obj.host.name,
                                  host_snapshot_obj.start_datetime.strftime('%Y-%m-%d %H:%M:%S.%f'))
    else:
        return
    reason = "用户{}在{}发起了{}的申请，备份点为“{}”".format(create_user.username, msg_time, msg_type, info)
    user_profiles = UserProfile.objects.filter(deleted=False).filter(user_type=UserProfile.AUDIT_ADMIN).all()
    for user_profile in user_profiles:
        user = user_profile.user

        if user.email != '':
            emailaddr = user.email
        else:
            emailaddr = user.username

        title = '【{}审批申请】'.format(msg_type)
        ret = send_mail(emailaddr, title, "{}。\r\n\r\n　　请点击{}链接执行审批任务，如已审批请忽略此邮件。".format(reason, getProductName()))
        _logger.info('notify_audits send_mail ret={}'.format(ret))
    save_host_log(host, get_host_log_type(task_info['task_type']), {'description': reason})
