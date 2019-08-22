import datetime

from box_dashboard import xlogging


@xlogging.convert_exception_to_value(datetime.datetime(2000, 1, 1))
def get_agent_client_version(system_info_obj):
    version_str = system_info_obj['System']['version']  # 2.0.180105
    time_str = version_str.split('.')[-1]
    year, month, day = int(time_str[0:2]), int(time_str[2:4]), int(time_str[4:6])
    return datetime.datetime(2000 + year, month, day)
