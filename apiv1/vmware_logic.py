import os
import uuid
import re
import json

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from django.utils import timezone

from box_dashboard import xlogging, boxService
from .models import (VirtualMachineSession, VirtualCenterConnection, StorageNode, VirtualMachineRestoreTask,
                     HostSnapshot, DiskSnapshot)
from .serializers import (VirtualMachineSessionSerializer, VirtualCenterConnectionSerializer,
                          VirtualHostRestoreSerializer)
from apiv1.vmware_task import VMRFlowEntrance

_logger = xlogging.getLogger(__name__)


class VmwareAgentReport(APIView):
    def __init__(self, **kwargs):
        super(VmwareAgentReport, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def post(self, request, api_request=None):
        if api_request is None:
            api_request = request.data
        _logger.info('VmwareAgentReport post api_request:{}'.format(api_request))
        action, key, data = api_request['type'], api_request['id'], api_request['data']
        if action == 'meta_data':
            disk_snapshot = get_object_or_404(DiskSnapshot, ident=key)
            ext_info = json.loads(disk_snapshot.ext_info)
            ext_info['meta_data'] = data
            disk_snapshot.ext_info = json.dumps(ext_info)
            disk_snapshot.save(update_fields=['ext_info'])
            return Response()
        elif action == 'vmware_config':
            host_snapshot = get_object_or_404(HostSnapshot, id=key)
            ext_info = json.loads(host_snapshot.ext_info)
            ext_info['vmware_config'] = data
            host_snapshot.ext_info = json.dumps(ext_info)
            host_snapshot.save(update_fields=['ext_info'])
            return Response()
        else:
            return Response('not vaild action', status=status.HTTP_406_NOT_ACCEPTABLE)


class VirtualHostRestore(APIView):
    serializer_class = VirtualHostRestoreSerializer

    def __init__(self, **kwargs):
        super(VirtualHostRestore, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def post(self, request, api_request=None):
        if api_request is None:
            api_request = request.data
        serializer = self.serializer_class(data=api_request)
        serializer.is_valid(True)
        data = serializer.data
        host_snapshot = get_object_or_404(HostSnapshot, id=data['host_snapshot'])
        task = VirtualMachineRestoreTask.objects.create(
            host_snapshot=host_snapshot,
            task_uuid=uuid.uuid4().hex,
            ext_config=data['ext_config']
        )
        task_flow = VMRFlowEntrance(task.id)
        task.start_datetime = timezone.now()
        task.running_task = json.dumps(task_flow.generate_uuid())
        task.save(update_fields=['start_datetime', 'running_task'])
        task_flow.start()
        return Response()


class VirtualHostSession(APIView):
    serializer_class = VirtualMachineSessionSerializer

    def __init__(self, **kwargs):
        super(VirtualHostSession, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def get(self, request):
        if request.user.is_superuser:
            vms = VirtualMachineSession.objects.all()
        else:
            vms = VirtualMachineSession.objects.filter(connection__user=request.user)
        return Response(self.serializer_class(vms, many=True).data)

    # create
    def post(self, request, api_request=None):
        if api_request is None:
            api_request = request.data
        moId, _, vc_center_id = api_request['ident'].split('|')
        connection = get_object_or_404(VirtualCenterConnection, id=vc_center_id)
        ident = VirtualMachineSession.g_ident(connection, moId)
        try:
            vm = VirtualMachineSession.objects.get(ident=ident)
        except VirtualMachineSession.DoesNotExist:
            storage = get_object_or_404(StorageNode, ident=api_request['storage_node'])
            home_path = os.path.join(storage.path, 'vmclient', re.sub('\W', '_', ident))
            vm = VirtualMachineSession.objects.create(ident=ident,
                                                      connection=connection,
                                                      name=api_request['name'],
                                                      home_path=home_path)
        else:
            # 其它用户已经添加
            if vm.connection.user != request.user:
                msg = '客户端已属于用户{}。'.format(vm.connection.user.username)
                return Response(msg, status=status.HTTP_406_NOT_ACCEPTABLE)
            else:
                msg = '客户端已经添加'
                return Response(msg, status=status.HTTP_406_NOT_ACCEPTABLE)
        return Response(self.serializer_class(vm).data)

    def delete(self, request, api_request=None):
        if api_request is None:
            api_request = request.data
        vm = get_object_or_404(VirtualMachineSession, id=api_request['id'])
        if vm.enable:
            return Response('删除失败，请先禁客户端', status=status.HTTP_406_NOT_ACCEPTABLE)
        if vm.host.is_linked:
            return Response('删除失败，客户端正在释放资源，请稍候再试', status=status.HTTP_406_NOT_ACCEPTABLE)
        else:
            boxService.box_service.remove(vm.home_path)
            vm.delete()
            return Response(self.serializer_class(vm).data)

    def put(self, request, api_request=None):
        if api_request is None:
            api_request = request.data
        vm = get_object_or_404(VirtualMachineSession, id=api_request['id'])
        action = api_request['action']
        if action == 'enable':
            vm.enable = not vm.enable
            vm.save(update_fields=['enable'])
            return Response(self.serializer_class(VirtualMachineSession.objects.get(id=api_request['id'])).data)
        else:
            return Response('未知的操作', status=status.HTTP_406_NOT_ACCEPTABLE)


class VirtualCenterConnectionView(APIView):
    serializer_class = VirtualCenterConnectionSerializer

    def __init__(self, **kwargs):
        super(VirtualCenterConnectionView, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    def get(self, request, filter={}):
        if request:
            if request.user.is_superuser:
                hosts = VirtualCenterConnection.objects.all()
            else:
                hosts = VirtualCenterConnection.objects.filter(user=request.user)
        else:
            hosts = VirtualCenterConnection.objects.all()
        if filter.get('id'):
            hosts = hosts.filter(id=filter.get('id'))

        return Response(self.serializer_class(hosts, many=True).data)

    # create
    def post(self, request, api_request=None):
        if api_request is None:
            api_request = request.data
        vc, _ = VirtualCenterConnection.objects.get_or_create(user=request.user,
                                                              username=api_request['username'],
                                                              password=api_request['password'],
                                                              address=api_request['address'],
                                                              ext_config=api_request.get('ext_config', '{}'))
        return Response(self.serializer_class(vc).data)

    def delete(self, request, api_request=None):
        if api_request is None:
            api_request = request.data
        vc = get_object_or_404(VirtualCenterConnection, id=api_request['id'])
        if vc.vm_clients.exists():
            return Response('删除失败，存在使用此连接配置的客户端，请先删除客户端再重试！', status=status.HTTP_406_NOT_ACCEPTABLE)
        else:
            VirtualCenterConnection.objects.get(id=api_request['id']).delete()
            return Response(self.serializer_class(vc).data)
