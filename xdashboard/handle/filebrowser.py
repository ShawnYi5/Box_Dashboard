import os
import json
from datetime import datetime
from urllib.parse import quote

from .version import getProductName
from django.shortcuts import render_to_response, get_object_or_404, HttpResponse
from django.http.response import HttpResponseForbidden
from apiv1.models import HostSnapshotShare, HostSnapshot
from django.http import FileResponse, HttpResponse
from django.utils.encoding import filepath_to_uri, escape_uri_path

from box_dashboard import xlogging, functions

_logger = xlogging.getLogger(__name__)
router = functions.Router(globals())


@xlogging.convert_exception_to_value('')
def _format_time(time_stamp):
    return datetime.fromtimestamp(time_stamp).strftime('%Y/%m/%d %H:%M')


def _get_stat(path):
    try:
        stat = os.stat(path)
    except FileNotFoundError:
        stat = os.stat(path, follow_symlinks=False)
    except:
        return '', 0

    if os.path.isfile(path):
        return stat.st_mtime, stat.st_size
    else:
        return stat.st_mtime, 0


def home(request):
    schedule = request.GET.get('schedule')
    username = request.user.username
    try:
        schedule_obj = get_object_or_404(HostSnapshotShare, id=schedule)
    except Exception as e:
        return HttpResponse('浏览点已关闭')
    host_snapshot = HostSnapshot.objects.get(id=schedule_obj.host_snapshot_id)
    if host_snapshot.schedule is not None:
        task_name = host_snapshot.schedule.name
    else:
        task_name = ''
    if schedule_obj.login_user != username:
        return HttpResponseForbidden()
    backuptype = '普通备份'
    if json.loads(host_snapshot.host.ext_info).get('nas_path'):
        backuptype = 'NAS备份'
    date_dict = {
        'schedule': schedule_obj,
        'username': username,
        'task_name': task_name,
        'point_type': 'CDP' if host_snapshot.is_cdp else backuptype,
        'title': getProductName()
    }
    return render_to_response('filebrowser.html', date_dict)


def list_file(request):
    schedule = request.GET.get('schedule')
    _id = request.GET.get('id', 'root')
    if _id == '':
        root = [{'id': '{}'.format('root'), "branch": [], "inode": True, "open": True,
                 'label': '{}'.format('/'), 'icon': 'folder', 'size': '大小', 'mtime': '修改日期'}]
        return HttpResponse(json.dumps(root, ensure_ascii=False))

    href_format = '../filebrowser_handle/?a=download&schedule={}&filename={{}}'.format(schedule)
    snapshot_share = get_object_or_404(HostSnapshotShare, id=schedule)
    root_dir = os.path.join('/home/', snapshot_share.samba_user, snapshot_share.samba_user, snapshot_share.dirinfo)
    if _id == 'root':
        node_list = _get_sub_node(root_dir, href_format)
        return HttpResponse(json.dumps(node_list, ensure_ascii=False))
    elif os.path.exists(_id):
        node_list = _get_sub_node(_id, href_format)
        return HttpResponse(json.dumps(node_list, ensure_ascii=False))
    else:
        return HttpResponse(json.dumps(list(), ensure_ascii=False))


def _get_sub_node(current_dir, href_format):
    file_names = os.listdir(current_dir)
    node_list = list()
    for file_name in file_names:
        node_path = os.path.join(current_dir, file_name)
        mtime, size = _get_stat(node_path)
        if os.path.isfile(node_path):
            icon = 'file'
            inode = False
            label = functions.tag('a', file_name, attrs={
                'href': href_format.format(quote(node_path, safe=''))
            })
            size = functions.format_size(size)
        else:
            inode = True
            icon = 'folder'
            label = file_name
            size = ''
        node_list.append({'id': '{}'.format(quote(node_path, safe='')), "branch": [],
                          "inode": inode, "open": False,
                          'label': '{}'.format(label), 'icon': icon,
                          'size': size, 'mtime': _format_time(mtime),
                          })
    return sorted(node_list, key=lambda e: (not e['inode'], e['label']))


def ping(request):
    schedule = request.GET.get('schedule')
    try:
        HostSnapshotShare.objects.get(id=schedule)
    except HostSnapshotShare.DoesNotExist:
        return HttpResponse(json.dumps({'r': 1, 'e': 'invalid'}))
    return HttpResponse(json.dumps({'r': 0, 'e': 'valid'}))


def download(request):
    schedule = request.GET.get('schedule')
    filename = request.GET.get('filename')
    username = request.user.username
    schedule_obj = get_object_or_404(HostSnapshotShare, id=schedule)
    if schedule_obj.login_user != username:
        _logger.warning('download user Forbidden')
        return HttpResponseForbidden()

    root_dir = os.path.join('/home/', schedule_obj.samba_user, schedule_obj.samba_user, schedule_obj.dirinfo)
    if not filename.startswith(root_dir):
        _logger.warning('download invalid Forbidden, root_dir:{}'.format(root_dir))
        return HttpResponseForbidden()

    if not os.path.exists(filename):
        _logger.warning('download not exists Forbidden, filename:{}'.format(filename))
        return HttpResponseForbidden()

    if not os.path.isfile(filename):
        _logger.warning('download not file Forbidden, filename:{}'.format(filename))
        return HttpResponseForbidden()

    response = HttpResponse()
    response['Content-Type'] = 'application/octet-stream'
    response['Content-Disposition'] = 'attachment;filename="{0}"'.format(os.path.split(escape_uri_path(filename))[-1])
    response['content-length'] = '{}'.format(os.stat(filename).st_size)
    response['X-Accel-Redirect'] = '/file_download/' + '/'.join(quote(filename, encoding='utf-8').split('/')[2:])
    return response
