import json
import os

from box_dashboard import xlogging
from .models import HostMaintainConfig
from .xmaintainance import enter_maintain_mode, leave_maintain_mode

_logger = xlogging.getLogger(__name__)

DEFAULT_CONFIG = json.dumps({
    "ports": [
        80,
        8000
    ]
})

DEFAULT_CACHE = json.dumps(
    {
        "services": [
        ]
    })


class WebMaintain(object):
    @staticmethod
    def enter(host_object):
        web_maintain_object = WebMaintain.get_or_create_web_maintain_object(host_object)
        config = json.loads(web_maintain_object.config)
        cache = json.loads(web_maintain_object.cache)

        image_path = WebMaintain.get_maintain_pic(host_object.ident)
        image_buffer_list = list()
        for _ in config['ports']:
            image_buffer_list.append(WebMaintain.get_image_buffer(image_path))

        stop_script = config.get('stop_script', '')
        is_linux = WebMaintain._is_linux(host_object)
        returned, details = enter_maintain_mode(
            host_object.ident, config['ports'], image_buffer_list, stop_script, is_linux)
        if 0 == returned:
            if is_linux:
                pass  # do nothing
            else:
                for _key in details.keys():
                    detail = details[_key]

                    service_name = detail[1]
                    if (service_name is not None) and (service_name != 'clrw_httpd') and \
                            (service_name not in cache["services"]):
                        cache["services"].append(service_name)

                web_maintain_object.cache = json.dumps(cache)
                web_maintain_object.save(update_fields=['cache'])
        else:
            _logger.error(r'call enter_maintain_mode ({}) failed. {} {}'.format(host_object.ident, returned, details))

    @staticmethod
    def leave(host_object):
        web_maintain_object = WebMaintain.get_or_create_web_maintain_object(host_object)
        config = json.loads(web_maintain_object.config)
        cache = json.loads(web_maintain_object.cache)
        start_script = config.get('start_script', '')
        is_linux = WebMaintain._is_linux(host_object)
        leave_maintain_mode(host_object.ident, cache["services"], start_script, is_linux)

    @staticmethod
    def get_or_create_web_maintain_object(host_object):
        try:
            return HostMaintainConfig.objects.get(host=host_object)
        except Exception as e:
            _logger.info(r'create WebMaintain config : {}'.format(host_object.ident, e))
            return HostMaintainConfig.objects.create(host=host_object, config=DEFAULT_CONFIG, cache=DEFAULT_CACHE)

    @staticmethod
    def get_maintain_pic(host_ident):
        home_page_pic = '/var/www/static/web_guard/homepage_pic/{}.jpg'.format(host_ident)
        return home_page_pic if os.path.isfile(home_page_pic) else ''

    @staticmethod
    @xlogging.convert_exception_to_value(None)
    def get_image_buffer(image_path):
        if image_path == '':
            return None
        with open(image_path, 'br') as f:
            return f.read()

    @staticmethod
    def _is_linux(host_object):
        system_infos = json.loads(host_object.ext_info)['system_infos']
        return 'LINUX' in system_infos['System']['SystemCaption'].upper()
