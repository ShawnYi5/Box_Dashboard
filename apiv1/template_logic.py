import json
from collections import namedtuple

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apiv1.models import HostSnapshot, DeployTemplate, SpaceCollectionTask
from apiv1.serializers import DeployTemplateCURDSerializer
from box_dashboard import xlogging, xdatetime
from apiv1.snapshot import DiskSnapshotLocker

_logger = xlogging.getLogger(__name__)

Snapshot = namedtuple('Snapshot', ['path', 'snapshot'])


class DeployTemplateCURD(APIView):
    serializer_class = DeployTemplateCURDSerializer
    queryset = DeployTemplate.objects.all()

    def __init__(self, **kwargs):
        super(DeployTemplateCURD, self).__init__(**kwargs)
        xlogging.ApiViewExceptionHandlerDecorator().decorate()

    # create
    def post(self, request, api_request=None):
        if not api_request:
            api_request = request.data

        name = api_request['name']
        desc = api_request['desc']
        snapshot_datetime = xdatetime.string2datetime(api_request['snapshot_datetime']) if api_request[
            'snapshot_datetime'] else None
        host_snapshot = HostSnapshot.objects.get(id=api_request['host_snapshot_id'])
        ext_info = api_request.get('ext_info', dict())

        if DeployTemplate.objects.filter(name=name).exists():
            return Response('创建失败，已存在相同名称的模板', status=status.HTTP_406_NOT_ACCEPTABLE)

        obj = DeployTemplate.objects.create(
            name=name,
            desc=desc,
            host_snapshot=host_snapshot,
            snapshot_datetime=snapshot_datetime,
            ext_info=json.dumps(ext_info)
        )
        lock_info = {'snapshots': list(), 'lock_name': 'deploy_template_{}'.format(obj.id)}
        for disk_snapshot in host_snapshot.disk_snapshots.all():
            lock_info['snapshots'].append({'path': disk_snapshot.image_path, 'snapshot': disk_snapshot.ident})
        try:
            DiskSnapshotLocker.lock_files([Snapshot(**item) for item in lock_info['snapshots']],
                                          lock_info['lock_name'])
        except Exception as e:
            _logger.warning('lock failed {}'.format(e))

        ext_info = json.loads(obj.ext_info)
        ext_info['lock_info'] = lock_info
        obj.ext_info = json.dumps(ext_info)
        obj.save(update_fields=['ext_info'])

        return Response(self.serializer_class(obj).data)

    # update
    def put(self, request, api_request=None):
        if not api_request:
            api_request = request.data
        _id = api_request.pop('id')
        obj = self.queryset.get(id=_id)
        obj.name = api_request['name']
        obj.desc = api_request['desc']
        obj.save(update_fields=['name', 'desc'])
        return Response(self.serializer_class(obj).data)

    def get(self, request):
        queryset = self.queryset.filter(host_snapshot__host__user=request.user)
        return Response(self.serializer_class(queryset, many=True).data)

    def delete(self, request, api_request=None):
        if not api_request:
            api_request = request.data
        _id = api_request['id']
        obj = self.queryset.get(id=_id)
        ext_info = json.loads(obj.ext_info)
        lock_info = ext_info.get('lock_info', list())
        try:
            DiskSnapshotLocker.unlock_files([Snapshot(**item) for item in lock_info['snapshots']],
                                            lock_info['lock_name'])
        except Exception as e:
            _logger.warning('unlock failed {}'.format(e))

        # 如果快照点的计划删除了，需要手动将快照点删除
        host_snapshot = obj.host_snapshot
        if (host_snapshot.schedule and host_snapshot.schedule.deleted) or (
                host_snapshot.cluster_schedule and host_snapshot.cluster_schedule.deleted):
            if not host_snapshot.set_deleting():
                SpaceCollectionTask.objects.create(type=SpaceCollectionTask.TYPE_NORMAL_MERGE,
                                                   host_snapshot=host_snapshot)

        obj.delete()
        return Response()
