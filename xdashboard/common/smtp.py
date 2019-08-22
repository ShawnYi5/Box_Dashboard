# coding=utf-8
import smtplib
import time
import json
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.header import Header
from email.utils import parseaddr, formataddr
from django.contrib.auth.models import User
from apiv1.models import StorageNode
from box_dashboard import xlogging
from xdashboard.models import DataDictionary
from .dict import GetDictionary, GetDictionaryByTpye
from xdashboard.models import Email, UserProfile
from threading import Thread, Condition
from xdashboard.handle.version import getProductName
from box_dashboard.boxService import box_service
from xdashboard.handle.enterprise_wei_xin import send_to_wei_xin
import queue

q_wei_xin = queue.Queue()
cond = Condition()
_logger = xlogging.getLogger(__name__)


def _format_addr(s):
    name, addr = parseaddr(s)
    return formataddr(( \
        Header(name, 'utf-8').encode(), \
        addr))


# 对主题分类：警告，错误，信息
def getMsgType(sub):
    if sub.find('配额不足') != -1:
        return '警告'
    elif sub.find('存储单元离线') != -1:
        return '警告'
    elif sub.find('存储单元不可用') != -1:
        return '错误'
    elif sub.find('CDP保护停止') != -1:
        return '警告'
    elif sub.find('CDP保护被暂停') != -1:
        return '警告'
    elif sub.find('CDP保护失败') != -1:
        return '错误'
    elif sub.find('备份失败') != -1:  # 备份，迁移，还原：邮件通知结果
        return '错误'
    elif sub.find('迁移失败') != -1:
        return '错误'
    elif sub.find('还原失败') != -1:
        return '错误'
    return '信息'


def FmtEmailMsg(info):
    if info.find('Account is not active') != -1:
        return '邮箱地址不存在(Account is not active)'
    elif info.find('Unknown user') != -1:
        return '邮箱地址不存在（Unknown user）'
    elif info.find('Name or service not known') != -1:
        return '邮箱地址不存在（Name or service not known）'
    elif info.find('No address associated with hostname') != -1:
        return '邮件服务器地址不正确（No address associated with hostname）'
    elif info.find('Connection timed out') != -1:
        return '网络超时，请检查SMTP配置'
    elif info.find('Authentication failed. Restarting authentication process.') != -1:
        return '认证失败（Authentication failed. Restarting authentication process.）'
    return info


def send_mail(to_list, sub, content, smtp_set=None):
    from xdashboard.handle.version import getProductName
    smtp_host = ''
    smtp_port = '25'
    smtp_mail = ''
    smtp_user = ''
    smtp_pass = ''
    smtp_ssl = '0'
    onedates = GetDictionaryByTpye(DataDictionary.DICT_TYPE_SMTP)
    if onedates:
        for onedate in onedates:
            if onedate.dictKey == 'smtp_host':
                smtp_host = onedate.dictValue
            if onedate.dictKey == 'smtp_port':
                smtp_port = onedate.dictValue
            if onedate.dictKey == 'smtp_mail':
                smtp_mail = onedate.dictValue
            if onedate.dictKey == 'smtp_user':
                smtp_user = onedate.dictValue
            if onedate.dictKey == 'smtp_pass':
                smtp_pass = onedate.dictValue
            if onedate.dictKey == 'smtp_ssl':
                smtp_ssl = onedate.dictValue
    if smtp_set:
        smtp_host = smtp_set['smtp_host']
        smtp_port = smtp_set['smtp_port']
        smtp_mail = smtp_set['smtp_mail']
        smtp_user = smtp_set['smtp_user']
        smtp_pass = smtp_set['smtp_pass']
        smtp_ssl = smtp_set['smtp_ssl']

    sub = getMsgType(sub) + ' - ' + sub

    username = to_list
    tmp = to_list.split('@')
    if len(tmp) > 1:
        username = tmp[0]
    jsonstr = box_service.getNetworkInfos()
    adapterlist = json.loads(jsonstr)
    precontent = "Dear {}：\r\n\r\n".format(username)
    for element in adapterlist:
        if (type(element) == type({})):
            for name, adapter in element.items():
                if adapter['ip4']:
                    aio_ip = adapter['ip4']
    precontent += "\r\n　　"
    content = precontent + content

    content += "\r\n\r\n\r\n\r\n{}（http://{}）".format(getProductName(), aio_ip)
    content += "\r\n此为自动发送邮件，请勿回复。"
    content += "\r\n如果你有任何问题, 请与客户支持部门联系。"
    content += "\r\n\r\n谢谢"
    content += "\r\n\r\n\r\n\r\n深圳市科力锐科技有限公司"
    content += "\r\n公司网址：http://www.clerware.com"
    content += "\r\n服务时间：周一～周五 9:00--18:00"
    content += "\r\n技术支持email：helper.sz@clerware.com"
    content += "\r\n技术支持电话：400-161-5658"
    me = smtp_mail
    msg = MIMEText(content)
    msg['Subject'] = sub
    msg['From'] = _format_addr(getProductName() + '<{}>'.format(smtp_mail))
    msg['To'] = to_list
    try:
        if smtp_ssl == '1':
            s = smtplib.SMTP_SSL()
        else:
            s = smtplib.SMTP()
        s.connect(smtp_host, smtp_port)
        s.login(smtp_user, smtp_pass)
        s.sendmail(me, to_list, msg.as_string())
        s.close()
        return 'OK'
    except Exception as e:
        return FmtEmailMsg(str(e))


def limit_and_clean_send():
    filters = GetDictionary(DataDictionary.DICT_TYPE_CHOICE_SEND_EMAIL_RANGE, 'range', '')
    Email.objects.filter(is_successful=True).filter(datetime__lte=datetime.now() - timedelta(7)).delete()
    Email.objects.filter(is_successful=False, times__gt=5).filter(
        datetime__lte=datetime.now() - timedelta(30)).delete()
    if not filters:
        emails = Email.objects.filter(is_successful=False, times__lte=5)
    else:
        filters = list(map(lambda x: int(x), filters.split(',')))
        emails = Email.objects.filter(is_successful=False, times__lte=5).filter(type__in=filters)
    return emails


class sendEmailRobot(Thread):
    def __init__(self, cond):
        super(sendEmailRobot, self).__init__()
        self.cond = cond

    def run(self):
        time.sleep(60)
        while True:
            try:
                self.worker()
            except Exception as e:
                _logger.error('sendEmailRobot error:{}'.format(e), exc_info=True)
            with self.cond:
                self.cond.wait(3600)

    @staticmethod
    @xlogging.db_ex_wrap
    def worker():
        emails = limit_and_clean_send()
        if not emails:
            return 'Ok'
        save_weixin_queue(emails)
        for email in emails:
            content = json.loads(email.content)
            to_list = content['email_address']
            sub = content['sub']
            content = content['content']
            resp = send_mail(to_list, sub, content, smtp_set=None)
            if resp == 'OK':
                email.times += 1
                email.is_successful = True
                email.save(update_fields=['times', 'is_successful'])
            else:
                email.times += 1
                email.save(update_fields=['times'])
                _logger.error('sendEmailRobot worker exception:{0}'.format(resp))
        return


def save_weixin_queue(emails):
    """
    信息来源与email相同
    :return:
    """
    emails = emails.filter(times=0)
    for email in emails:
        content = json.loads(email.content)
        u_id = content['user_id']
        describe = content['content'].strip()
        describe = describe[:-1] if describe[-1] == ',' else describe
        text = describe + '\n' + content['sub']
        q_wei_xin.put((u_id, text))


@xlogging.convert_exception_to_value(None)
def send_email(type_num, exc_inf):
    storage_ident = exc_inf.get('storage_node_ident', '')
    userid = exc_inf.get('user_id', '')  # 必有参数：type_num邮件类型， userid收件人
    content = exc_inf.get('content', '')
    sub = exc_inf.get('sub', '')

    if storage_ident:
        storagenode = StorageNode.objects.filter(ident=storage_ident).first()
        if storagenode:
            storagename = storagenode.name
        else:
            type_num = Email.STORAGE_NODE_NOT_VALID
            storagename = '--'

    user = User.objects.filter(id=userid).first()
    if user:
        username = user.username
        if user.email != '':
            email_address = user.email
        else:
            email_address = username

    else:
        _logger.error('send_email 不存在此用户(id:{0})，发送邮件失败'.format(userid))
        return 'OK'
    detail = dict()
    if type_num == Email.STORAGE_NODE_NOT_ENOUGH_SPACE:
        sub = '配额不足'
        content = '在存储单元({})上的配额不足'.format(storagename)
    if type_num == Email.STORAGE_NODE_NOT_ONLINE:
        sub = '存储单元离线'
        content = '存储单元({})当前处于离线状态'.format(storagename)
    if type_num == Email.STORAGE_NODE_NOT_VALID:
        sub = '存储单元不可用'
        content = '存储单元({})当前不可用'.format(storagename)
    if type_num == Email.CDP_STOP:
        sub = 'CDP保护停止'
    if type_num == Email.CDP_PAUSE:
        sub = 'CDP保护被暂停'
    if type_num == Email.CDP_FAILED:
        sub = 'CDP保护失败'

    # 备份, 迁移, 还原: 成功否
    if type_num == Email.BACKUP_FAILED:
        sub, content = '备份失败', exc_inf['desc']
    if type_num == Email.BACKUP_SUCCESS:
        sub, content = '备份成功', exc_inf['desc']

    if type_num == Email.MIGRATE_FAILED:
        sub, content = '迁移失败', exc_inf['desc']
    if type_num == Email.MIGRATE_SUCCESS:
        sub, content = '迁移成功', exc_inf['desc']

    if type_num == Email.RESTORE_FAILED:
        sub, content = '还原失败', exc_inf['desc']
    if type_num == Email.RESTORE_SUCCESS:
        sub, content = '还原成功', exc_inf['desc']

    detail['email_address'] = email_address
    detail['content'] = content
    detail['sub'] = sub
    detail['user_id'] = userid
    Email.objects.create(type=type_num, content=json.dumps(detail, ensure_ascii=False), times=0)
    with cond:
        cond.notify()
    return 'OK'


class sendWeiXinRobot(Thread):
    def __init__(self):
        super(sendWeiXinRobot, self).__init__()

    def _get_info_wei_xin(self):
        wei_xin, info = None, None
        if q_wei_xin.empty():
            time.sleep(60)
            return wei_xin, info
        content = q_wei_xin.get()
        user_id = content[0]
        info = content[1]
        wei_xin = UserProfile.objects.filter(user_id=int(user_id)).first().wei_xin
        return wei_xin, info

    def run(self):
        time.sleep(60)
        while True:
            try:
                wei_xin, info = self._get_info_wei_xin()
                if not wei_xin:
                    continue
                res = send_to_wei_xin(wei_xin, info)
                if res['errcode']:
                    _logger.info('sendWeiXinRobot res:{}'.format(res))
            except Exception as e:
                _logger.error('sendWeiXinRobot error:{}'.format(e), exc_info=True)
