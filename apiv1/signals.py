from django.dispatch.dispatcher import Signal

exe_schedule = Signal(providing_args=["schedule_id"])
end_sleep = Signal(providing_args=["schedule_id"])
