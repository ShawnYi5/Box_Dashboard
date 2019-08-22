import json
from apiv1.models import BackupTaskSchedule

try:
    nas_plans = BackupTaskSchedule.objects.filter(backup_source_type=BackupTaskSchedule.BACKUP_FILES, deleted=False)
    if nas_plans.count() < 1:
        print('没有NAS计划。')
    else:
        print('>>>>>>>>>> 请配置以下NAS计划的“最大存储容量”（正整数，单位TB）（该值会实际传入底层）<<<<<<<<<<')
        for plan in nas_plans:
            print('{}：'.format(plan.name))
            space = int(input().rstrip('TB'))
            ext_config = json.loads(plan.ext_config)
            ext_config.update({
                'nas_max_space_val': space,
                'nas_max_space_unit': 'TB',
                'nas_max_space_actual': space * 1024 ** 4,
            })
            plan.ext_config = json.dumps(ext_config)
            plan.save(update_fields=['ext_config'])
except Exception as e:
    print(e)
finally:
    print('执行结束')
