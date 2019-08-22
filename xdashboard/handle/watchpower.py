import json
import os
from box_dashboard import xlogging
from box_dashboard import functions
from django.http import HttpResponse

_logger = xlogging.getLogger(__name__)
router = functions.Router(globals())


def get_process(request):
    result = dict()
    if os.path.isfile(r'/run/watchpower_check_process.json'):
        try:
            with open(r'/run/watchpower_check_process.json', 'r') as fout:
                try:
                    result = json.loads(fout.read())
                except Exception as e:
                    _logger.info('get_process read Failed.e={}'.format(e))
        except Exception as e:
            pass
        result['is_process'] = True
    else:
        result['is_process'] = False

    return HttpResponse(json.dumps(result, ensure_ascii=False))
