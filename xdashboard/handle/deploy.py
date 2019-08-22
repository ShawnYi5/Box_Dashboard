# coding=utf-8
import json
from django.http import HttpResponse, HttpRequest
from rest_framework import status
from . import migrate
import html

def existimglist(request):
    infoList = list()
    info = {'id': '124', 'name': 'Windows2008 32位的web全能环境（配置双核2G起）', 'desc': 'IIS7.0+FTP+ASP+ASP.NET2.0/3.5/4.0+PHP5.3', 'image': 'https://oss.aliyuncs.com/netmarket/image/3f2a6464-7f97-4d17-864b-1a32b590f67b.png'}
    infoList.append(info)
    info = {'id': '34dfg', 'name': 'Windows2008 32位的web全能环境）', 'desc': 'IIS7.0+FTP+ASP+ASP.NET2.0/3.5/4.0+PHP5.5',
            'image': 'https://oss.aliyuncs.com/netmarket/image/3f2a6464-7f97-4d17-864b-1a32b590f67b.png'}
    infoList.append(info)
    ret={"r": 0, "e": "操作成功","list":infoList}
    infos = json.dumps(ret, ensure_ascii=False)
    return  HttpResponse(infos);

def delimg(request):
    return HttpResponse('{"r":"0","e":"操作成功"}')

def deploy(request):
    return HttpResponse('{"r":"0","e":"操作成功"}')

def destserverlist(request):
    return migrate.getDestServerList(request)

def getAdapterSettings(request):
    return migrate.getAdapterSettings(request)

def getHardSettings(request):
    return migrate.getHardSettings(request)

def AddPointToImg(request):
    return HttpResponse('{"r":"0","e":"增加成功"}')

def GetImgUrl(request):
    return HttpResponse('{"r":"0","e":"操作成功","url":"http://172.16.6.197/soap/jsonp.php"}')

def UploadFile(request):
    request.POST.raw_post_data;
    return HttpResponse('ok')

def deploy_handler(request):
    a = request.GET.get('a', 'none')
    if a == 'none':
        a = request.POST.get('a', 'none')
    if a == 'existimglist':
        return existimglist(request)
    if a == 'del':
        return delimg(request)
    if a == 'deploy':
        return deploy(request)
    if a == 'destserverlist':
        return destserverlist(request)
    if a == 'adaptersettings':
        return getAdapterSettings(request)
    if a == 'hardsettings':
        return getHardSettings(request)
    if a == 'addpointimg':
        return AddPointToImg(request)
    if a == 'imgurl':
        return GetImgUrl(request)
    if a == 'upload':
        return UploadFile(request)
    return HttpResponse(json.dumps({"r": "1", "e": "没有对应的action:{}".format(html.escape(a))}))