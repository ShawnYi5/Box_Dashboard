# coding=utf-8
from django.http import HttpResponse, HttpRequest
from django.shortcuts import render_to_response

def browsePoint(request):
    id = request.GET.get('id', 'root')
    if id == 'root':
        return render_to_response("test/browsepoint_handle.html")
    if id == 'C':
        return render_to_response("test/browsepointc_handle.html")
