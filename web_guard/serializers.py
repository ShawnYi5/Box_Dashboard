from rest_framework import serializers

from apiv1.models import Host
from apiv1.serializers import HostSerializer
from box_dashboard import xdata
from .models import EmergencyPlan, WebGuardStrategy, ModifyTask


class ManyHostSerializer(serializers.ModelSerializer):
    class Meta:
        model = Host
        fields = ('name', 'id', 'ident')


class ManyStrategySerializer(serializers.ModelSerializer):
    class Meta:
        model = WebGuardStrategy
        fields = ('name', 'id', 'deleted', 'enabled')


class EmPlansInfoSerializers(serializers.ModelSerializer):
    hosts = ManyHostSerializer(many=True)
    strategy = ManyStrategySerializer(many=True)

    class Meta:
        model = EmergencyPlan
        fields = ('id', 'name', 'enabled', 'deleted', 'exc_info', 'hosts', 'strategy')


class StrategySerializers4Create(serializers.ModelSerializer):
    class Meta:
        model = WebGuardStrategy
        fields = ('user', 'name', 'ext_info', 'group', 'check_type')


class StrategySerializers4GetInfo(serializers.ModelSerializer):
    class Meta:
        model = WebGuardStrategy
        fields = ('id', 'user', 'name', 'enabled', 'deleted', 'last_run_date', 'next_run_date', 'present_status',
                  'ext_info', 'running_task', 'group', 'check_type', 'task_histories')


class MaintainStatusSerializer(serializers.Serializer):
    host = HostSerializer()
    status = serializers.ChoiceField(required=True, choices=xdata.MAINTAIN_STATUS_TYPE)


class MaintainStatusSwitchSerializer(serializers.Serializer):
    status = serializers.ChoiceField(required=True, choices=xdata.MAINTAIN_STATUS_TYPE)


class MaintainConfigSerializer(serializers.Serializer):
    ports = serializers.JSONField(required=True)
    jpg_path = serializers.CharField(allow_blank=True, default='')
    havescrpit = serializers.BooleanField(required=False)
    stop_script = serializers.CharField(allow_blank=True, default='')
    start_script = serializers.CharField(allow_blank=True, default='')


class EmergencyPlanExecuteSerializer(serializers.Serializer):
    level = serializers.CharField(required=True)  # STRATEGY_EVENT_STATUS
    type = serializers.ChoiceField(required=True, choices=EmergencyPlan.STATUS_CHOICES)


class WGRLogicSerializer(serializers.Serializer):
    is_auto = serializers.BooleanField(required=True)
    # EmergencyPlan id
    plan_id = serializers.CharField(required=True)
    host_ident = serializers.CharField(required=True)
    # 手动模式需要传递一下2个参数
    snapshot_id = serializers.CharField(required=False)
    restore_time = serializers.CharField(required=False)


class ModifyContentTaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = ModifyTask
        fields = '__all__'
