# coding=utf-8
import html
import json
import os
import re
import shutil
import subprocess

from django.core.paginator import Paginator
from django.http import HttpResponse
from rest_framework import status

from apiv1.models import DeployTemplate, Host
from apiv1.template_logic import DeployTemplateCURD
from apiv1.work_processors import HostBackupWorkProcessors
from box_dashboard import xlogging, xdatetime, functions, xdata
from xdashboard.common import file_utils
from xdashboard.common.license import check_license, is_functional_available
from xdashboard.handle import serversmgr
from xdashboard.handle.logserver import SaveOperationLog
from xdashboard.models import OperationLog
from xdashboard.request_util import get_operator
from xdashboard.handle.restore import (is_linux_host, is_host_cdp_plan_existed, host_disks_info,
                                       get_plan_name_by_snapshot_obj)

_logger = xlogging.getLogger(__name__)
router = functions.Router(globals())


def create_template(request):
    params = request.POST
    name = params['name']
    desc = params['desc']
    pointid = params.get('pointid', None)  # 不一定有
    snapshot_datetime = params.get('snapshot_datetime', None)  # 不一定有
    if pointid:
        pointid = pointid.split('|')[1]
        api_request = {'name': name,
                       'desc': desc,
                       'host_snapshot_id': pointid,
                       'snapshot_datetime': snapshot_datetime}
        rsp = DeployTemplateCURD().post(request, api_request)
        if status.is_success(rsp.status_code):
            mylog = {'操作': '创建模板', '模板名称': name, "模板ID": rsp.data['id'], "操作结果": "创建成功"}
            SaveOperationLog(
                request.user, OperationLog.TYPE_TEMPLATE, json.dumps(mylog, ensure_ascii=False), get_operator(request))
            return HttpResponse(json.dumps({'r': 0, 'e': ''}))
        else:
            return HttpResponse(json.dumps({'r': 1, 'e': rsp.data if rsp.data else '内部错误41'}))
    else:
        return HttpResponse(json.dumps({'r': 1, 'e': '内部错误42'}))


def lists(request):
    paramsQD = request.GET
    perPage = paramsQD.get('rows', '10')  # 设置每页条数
    targPage = paramsQD.get('page', '1')  # 返回第几页
    search_key = request.GET.get('s_key', None)

    rsp = DeployTemplateCURD().get(request)
    if status.is_success(rsp.status_code):
        rowList = list()
        for row in rsp.data:
            # '序号', '模板名称', '创建时间', '状态', '描述'
            one_info = {'id': row['id'], 'cell': [row['id'],
                                                  row['name'],
                                                  row['create_datetime'],
                                                  row['desc']]}
            if search_key:
                is_need = serversmgr.filter_hosts(search_key, one_info['cell'][1],
                                                  one_info['cell'][2],
                                                  one_info['cell'][3])
            else:
                is_need = True
            if is_need:
                rowList.append(one_info)

        paginator = Paginator(object_list=rowList, per_page=perPage)
        plansNum = paginator.count
        pagesNum = paginator.num_pages
        getPlans = paginator.page(targPage).object_list

        retInfo = {'r': 0, 'a': 'list', 'page': targPage, 'total': pagesNum, 'records': plansNum, 'rows': getPlans}
        functions.sort_gird_rows(request, retInfo)
        jsonStr = json.dumps(retInfo, ensure_ascii=False)

        return HttpResponse(jsonStr)
    else:
        return HttpResponse(json.dumps({'r': 1, 'e': rsp.data if rsp.data else '内部错误64'}))


def delPlans(request):
    paramsQD = request.GET
    planids = paramsQD.get('taskid', '')  # '4,5,6'

    for planid in planids.split(','):
        DeployTemplateCURD().delete(request, {'id': planid})

    desc = {'操作': '删除模板', '模板ID': planids.split(',')}
    SaveOperationLog(
        request.user, OperationLog.TYPE_TEMPLATE, json.dumps(desc, ensure_ascii=False), get_operator(request))
    return HttpResponse('{"r": "0","e": "操作成功"}')


# 立即执行User的多个计划一次
def detail(request):
    paramsQD = request.GET
    objid = paramsQD.get('taskid')
    obj = DeployTemplate.objects.get(id=objid)
    snapshot_obj = obj.host_snapshot
    system_infos = json.loads(snapshot_obj.ext_info)['system_infos']
    system_caption = '{} {}(版本号:{})'.format(system_infos['System']['SystemCaption'],
                                            system_infos['System']['ServicePack'],
                                            system_infos['System'].get('BuildNumber', 'Unknown version'))

    if is_host_cdp_plan_existed(snapshot_obj.host.id) or is_linux_host(system_infos):
        calc_used = False
    else:
        calc_used = True

    result_data = {
        'host': snapshot_obj.host.display_name,
        'schedule': get_plan_name_by_snapshot_obj(snapshot_obj),
        'snapshot': snapshot_obj.name,
        'disk': '<br>'.join(host_disks_info(system_infos, calc_used)),
        'os': system_caption,
        'deploy_template_name': obj.name,
        'deploy_template_desc': obj.desc,
        'deploy_template_id': obj.id,
        'r': 0,
        'e': ''
    }
    return HttpResponse(json.dumps(result_data, ensure_ascii=False))


def get_restore_info(request):
    obj = DeployTemplate.objects.get(id=request.GET['id'])
    snapshot_obj = obj.host_snapshot
    start_datetime = snapshot_obj.start_datetime.strftime('%Y-%m-%d %H:%M:%S.%f')
    if snapshot_obj.is_cdp:
        pointid = '{}|{}|{}|{}'.format(xdata.SNAPSHOT_TYPE_CDP, snapshot_obj.id, start_datetime,
                                       start_datetime)
        snapshot_time = obj.snapshot_datetime.strftime('%Y-%m-%d %H:%M:%S.%f')
    else:
        ponitid = '{}|{}|{}'.format(xdata.SNAPSHOT_TYPE_NORMAL, snapshot_obj.id, start_datetime)
        snapshot_time = start_datetime

    system_infos = json.loads(snapshot_obj.ext_info)['system_infos']
    system_caption = '{} {}(版本号:{})'.format(system_infos['System']['SystemCaption'],
                                            system_infos['System']['ServicePack'],
                                            system_infos['System'].get('BuildNumber', 'Unknown version'))
    result_data = {
        'pointid': ponitid,
        'snapshot_time': snapshot_time,
        'host_name': snapshot_obj.host.name,
        'host_os': system_caption,
        'r': 0,
        'e': ''
    }
    return HttpResponse(json.dumps(result_data, ensure_ascii=False))


def modify(request):
    rsp = DeployTemplateCURD().put(request, {
        'name': request.POST['name'],
        'desc': request.POST['desc'],
        'id': request.POST['id']
    })
    if status.is_success(rsp.status_code):
        mylog = {'操作': '编辑模板',
                 '模板名称': rsp.data['name'],
                 '模板描述': rsp.data['desc'],
                 "模板ID": rsp.data['id'],
                 "操作结果": "创建成功"}
        SaveOperationLog(
            request.user, OperationLog.TYPE_TEMPLATE, json.dumps(mylog, ensure_ascii=False), get_operator(request))
        return HttpResponse('{"r": "0","e": "操作成功"}')
    else:
        return HttpResponse(json.dumps({'r': 1, 'e': rsp.data if rsp.data else '内部错误170'}))
