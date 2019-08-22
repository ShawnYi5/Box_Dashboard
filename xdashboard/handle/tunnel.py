import json, html
from apiv1.models import Tunnel
from django.http import HttpResponse
from django.core.paginator import Paginator
from django.contrib.auth.models import User
from rest_framework import status
from xdashboard.common.license import check_license, get_functional_int_value
from xdashboard.handle.authorize.authorize_init import get_tunnel_num
from apiv1.views import TunnelManage
from box_dashboard import functions


# 返回JS调用函数语句, 函数具体定义在前端
def get_del_btn(tunnel):
    if tunnel.user.is_superuser:
        return '''<span onclick="del_tunnel('{0}')" style="color:blue;cursor:pointer">[删除]</span>'''.format(tunnel.id)

    return '-'


def get_tunnel_user(tunnel):
    if tunnel.user:
        return tunnel.user

    if tunnel.host and tunnel.host.user:
        return tunnel.host.user

    return User.objects.get(username='admin')


def set_tunnel_user(tunnel):
    tunnel.user = get_tunnel_user(tunnel)
    return tunnel


def query_all_tunnels(request):
    params = request.GET
    page_rows = params.get('rows', '10')
    page_index = params.get('page', '1')

    all_tunnel = Tunnel.objects.all()
    if request.user.is_superuser:
        all_tunnel = [set_tunnel_user(tunnel) for tunnel in all_tunnel]
    else:
        all_tunnel = TunnelManage.filter_out_invalid_tunnel(all_tunnel)

    if request.user.is_superuser:
        objects = all_tunnel
    else:
        objects = list(filter(lambda tunnel: tunnel.user == request.user, all_tunnel))

    paginator = Paginator(object_list=objects, per_page=page_rows)
    all_rows = paginator.count
    page_num = paginator.num_pages
    get_pagex = paginator.page(page_index).object_list
    ret = {'a': 'list', 'records': all_rows, 'total': page_num, 'page': page_index, 'rows': [], 'r': 0}

    for obj in get_pagex:
        ret['rows'].append({
            'cell': [obj.id, obj.host_ip, obj.host_port, obj.user.username, get_del_btn(obj)], 'id': obj.id
        })

    functions.sort_gird_rows(request, ret)
    return HttpResponse(json.dumps(ret))


def rename_tunnel(request):
    tu_id = request.GET['id']
    new_name = request.GET['name']
    TunnelManage().put(request=None, api_request={'tu_name': new_name, 'tu_id': tu_id})
    return HttpResponse('{"r": "0", "e": "success"}')


def _check_tunnel_license(request):
    clret = check_license('tunnel_num')
    if clret.get('r', 0) != 0:
        return clret
    count = get_functional_int_value('tunnel_num')
    tunnel_count = get_tunnel_num()
    if tunnel_count >= count:
        return {'r': 2, 'e': '当前授权数量{}，已创建连接数量{}，请增加授权数量或删除一些连接。'.format(count, tunnel_count)}
    return {'r': 0, 'e': 'OK'}


def new_tunnel(request):
    tu_ip = request.GET['tu_ip']
    tu_port = request.GET['tu_port']
    rev = {
        'e': '',
        'r': 0
    }

    clret = _check_tunnel_license(request)
    if clret.get('r', 0) != 0:
        return HttpResponse(json.dumps(clret, ensure_ascii=False))

    rsp = TunnelManage().post(request=None, api_request={'tu_ip': tu_ip, 'tu_port': tu_port, 'user_id':
        request.user.id})
    if not status.is_success(rsp.status_code):
        rev['r'] = 1
        rev['e'] = rsp.data
    return HttpResponse(json.dumps(rev, ensure_ascii=False))


def delete_tunnel(request):
    tu_ids = request.GET['id']
    for tu_id in tu_ids.split(','):
        TunnelManage().delete(request=None, api_request={'tu_id': tu_id})
    return HttpResponse('{"r": "0", "e": "success"}')


def tunnelmanage_handle(request):
    a = request.GET.get('a', 'none')
    if a == 'none':
        a = request.POST.get('a', 'none')
    if a == 'query':
        return query_all_tunnels(request)
    if a == 'rename':
        return rename_tunnel(request)
    if a == 'new':
        return new_tunnel(request)
    if a == 'del':
        return delete_tunnel(request)
    return HttpResponse(json.dumps({"r": "1", "e": "没有对应的action:{}".format(html.escape(a))}))
