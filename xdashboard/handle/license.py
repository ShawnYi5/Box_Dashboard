import json
from Crypto import Random
from Crypto.PublicKey import RSA
from box_dashboard import xlogging, functions
from django.http import HttpResponse
from django.http import FileResponse

_logger = xlogging.getLogger(__name__)
router = functions.Router(globals())


def get_pub_pri_key(request):
    result = {'r': 0, 'e': '操作成功'}
    random_generator = Random.new().read
    rsa = RSA.generate(1024, random_generator)
    result['pri'] = rsa.exportKey().decode()
    result['pub'] = rsa.publickey().exportKey().decode()
    return HttpResponse(json.dumps(result, ensure_ascii=False))


def download_key(request):
    key = request.GET.get('key')
    sn = request.GET.get('sn', None)
    filename = request.GET.get('filename')
    if sn is None:
        response = FileResponse(key)
    else:
        response = FileResponse('{}|{}'.format(sn, key))
    response['Content-Type'] = 'application/octet-stream'
    response['Content-Disposition'] = 'attachment;filename="{}"'.format(filename)
    return response
