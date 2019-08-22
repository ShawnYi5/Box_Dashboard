import json
import os
import base64
import re
import subprocess
import threading
import glob
from box_dashboard import xlogging, functions
from django.http import HttpResponse

_logger = xlogging.getLogger(__name__)
_lock = threading.RLock()
router = functions.Router(globals())

g_clerware_linux_driver_magics_path = r'/sbin/aio/logic_service/clerware_linux_driver_magics.json'
g_clerware_linux_driver_user_db_file = r'/home/clerware_linux_driver_user/clerware_linux_driver_user.json'
g_clerware_linux_driver_user_dir = r'/home/clerware_linux_driver_user/'
g_clerware_linux_driver_compatible_vermagic_db_file = r'/sbin/aio/logic_service/clerware_linux_Module.symvers/compatible_*.json'
g_clerware_linux_driver_compatible_vermagic_dir = r'/sbin/aio/logic_service/clerware_linux_Module.symvers'


def sort_key(s):
    ss = list(map(int, re.findall('\d+', s)))
    if ss:
        return ss
    return s


def _get_compatible_vermagic_db():
    _compatible_vermagic_db = dict()
    try:
        list_compatible_file = glob.glob(g_clerware_linux_driver_compatible_vermagic_db_file)
        for _one in list_compatible_file:
            with open(_one, 'r') as fout:
                try:
                    _new_dict = json.loads(fout.read())
                    if _new_dict is not None:
                        _compatible_vermagic_db.update(_new_dict)
                except Exception as e:
                    _logger.info('linuxcompatible.py: json.loads:{} Exception:{}'.format(_one, e))
    except:
        pass
    return _compatible_vermagic_db


def _get_user_magics_db():
    _user_magic_db = dict()
    try:
        if os.path.isfile(g_clerware_linux_driver_user_db_file):
            with open(g_clerware_linux_driver_user_db_file, 'r') as fout:
                _user_magic_db = json.loads(fout.read())
    except:
        pass
    return _user_magic_db


def _get_clerware_linux_driver_magics_obj():
    clerware_linux_driver_magics_obj = dict()
    try:
        if os.path.isfile(g_clerware_linux_driver_magics_path):
            with open(g_clerware_linux_driver_magics_path, 'r') as fout:
                clerware_linux_driver_magics_obj = json.loads(fout.read())
    except:
        pass
    return clerware_linux_driver_magics_obj


def _write_user_magics_db(user_db):
    try:
        os.makedirs(g_clerware_linux_driver_user_dir)
    except:
        pass
    with open(g_clerware_linux_driver_user_db_file, 'w') as file_object:
        file_object.write(json.dumps(user_db, ensure_ascii=False))


def _insert_into_vermagic_list(vermagic_list, src_vermagic, user_vermagic):
    for vermagic in vermagic_list:
        if vermagic['src_vermagic'] == src_vermagic:
            vermagic['user_vermagic_list'].append(user_vermagic)


def _insert_into_compatible_vermagic_list(vermagic_list, src_vermagic, user_vermagic):
    for vermagic in vermagic_list:
        if vermagic['src_vermagic'] == src_vermagic:
            vermagic['compatible_vermagic_list'].append(user_vermagic)


def _have_vermagic(vermagic_list, src_vermagic):
    for vermagic in vermagic_list:
        if vermagic['src_vermagic'] == src_vermagic:
            return True
    return False


def getlist(request):
    linux_name = request.GET.get('name')
    vermagic_list = list()
    clerware_linux_driver_user_obj = _get_user_magics_db()
    clerware_linux_driver_magics_obj = _get_clerware_linux_driver_magics_obj()

    # {disksbd_linux:{},sbdfun_linux:{}}
    for _disksbd_linux_key in clerware_linux_driver_magics_obj.keys():
        _disksbd_linux_value = clerware_linux_driver_magics_obj[_disksbd_linux_key]

        # {'3.10.xx':'3.10.xxc'}
        for subkey in _disksbd_linux_value.keys():
            ipos = subkey.find('ClerwareBuildOrgDriver')
            if ipos == -1:
                continue
            if not _have_vermagic(vermagic_list, subkey):
                _subvalue = _disksbd_linux_value[subkey]
                _post = _subvalue.find('.ClerwareBuildOrgDriver')
                if -1 != _post:
                    _subvalue = _subvalue[:_post]
                _post = _subvalue.find('ClerwareBuildOrgDriver')
                if -1 != _post:
                    _subvalue = _subvalue[:_post]
                if _subvalue[:len(linux_name)] == linux_name:
                    vermagic_list.append(
                        {"ko": _disksbd_linux_key, "src_vermagic": subkey, "src_vermagic_show": '{}'.format(_subvalue),
                         "user_vermagic_list": list()})

    vermagic_list = sorted(vermagic_list, key=lambda x: sort_key(x['src_vermagic_show']))

    for key in clerware_linux_driver_user_obj.keys():
        user_vermagic = key
        src_vermagic = clerware_linux_driver_user_obj[key]['src_vermagic']
        _insert_into_vermagic_list(vermagic_list, src_vermagic, user_vermagic)

    page = 1
    total = 0
    records = 0
    object_list = list()
    index = 1
    for vermagic in vermagic_list:
        dest_vermagic = list()
        id = '{}|{}'.format(vermagic['ko'], vermagic['src_vermagic'])
        for user_vermagic in vermagic['user_vermagic_list']:
            dest_vermagic.append(
                '{}<span style="color:blue;cursor:pointer;" onclick="del_user_vermagic(\'{}\',\'{}\')">删除</span>'.format(
                    user_vermagic, id, user_vermagic))

        dest_vermagic_ui = '{}<br><span style="color:blue;cursor:pointer;" onclick="add_user_vermagic(\'{}\',\'{}\')">增加</span>'.format(
            '<br>'.join(dest_vermagic), id, vermagic['src_vermagic_show'])
        object_list.append([index, id, vermagic['src_vermagic_show'], dest_vermagic_ui,
                            vermagic['src_vermagic_show'].split('.')[0]])
        index = index + 1
    result = {'r': 0, 'a': 'list', 'page': page, 'total': total, 'records': records, 'rows': object_list}
    return HttpResponse(json.dumps(result, ensure_ascii=False))


@xlogging.LockDecorator(_lock)
def add_user_vermagic(request):
    result = {'r': 0}
    id = request.POST.get('id')
    user_vermagic = request.POST.get('user_vermagic')
    src_vermagic = id.split('|')[1]

    if not os.path.isdir(r'/home/clerware_linux_driver_user'):
        os.mkdir(r'/home/clerware_linux_driver_user')

    clerware_linux_driver_user_obj = _get_user_magics_db()

    for key in clerware_linux_driver_user_obj.keys():
        file_user_vermagic = key
        file_src_vermagic = clerware_linux_driver_user_obj[key]['src_vermagic']
        if file_user_vermagic == user_vermagic and src_vermagic == file_src_vermagic:
            return HttpResponse(json.dumps(result, ensure_ascii=False))

    clerware_linux_driver_user_obj[user_vermagic] = {'src_vermagic': src_vermagic}

    _write_user_magics_db(clerware_linux_driver_user_obj)

    return HttpResponse(json.dumps(result, ensure_ascii=False))


@xlogging.LockDecorator(_lock)
def del_user_vermagic(request):
    result = {'r': 0}
    id = request.POST.get('id')
    user_vermagic = request.POST.get('user_vermagic')
    src_vermagic = id.split('|')[1]

    clerware_linux_driver_user_obj = _get_user_magics_db()

    for key in clerware_linux_driver_user_obj.keys():
        file_user_vermagic = key
        file_src_vermagic = clerware_linux_driver_user_obj[key]['src_vermagic']
        if file_user_vermagic == user_vermagic and src_vermagic == file_src_vermagic:
            clerware_linux_driver_user_obj.pop(key)
            break

    _write_user_magics_db(clerware_linux_driver_user_obj)

    return HttpResponse(json.dumps(result, ensure_ascii=False))


def _get_vermagic(filepath):
    cmd = 'modinfo -F vermagic "{}"'.format(filepath)
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, universal_newlines=True, shell=True)
    p.wait()

    if p.returncode != 0:
        return 'error,returncode={},cmd={}'.format(p.returncode, cmd)

    for line in p.stdout:
        real_magic = line.strip('\n')
        real_magic = real_magic.strip('\r')
        return real_magic
    return 'error,cmd={}'.format(cmd)


def upload(request):
    file_data = request.body
    name = request.GET.get('name', 'none.bin')
    start = int(request.GET.get('start', '0'))
    step = int(request.GET.get('step', 1024 * 1024))
    total = int(request.GET.get('total', 0))
    tmp_dir = '/tmp'
    vermagic = 'wait update'

    r = 0
    try:
        os.makedirs(os.path.join(tmp_dir, 'update'))
    except OSError as e:
        pass

    filepath = os.path.join(tmp_dir, 'update', name)

    if start == 0:
        try:
            os.remove(filepath)
        except OSError as e:
            pass

    binfile = open(filepath, 'ab')
    vec = str(file_data).split(';base64,')
    if len(vec) == 2:
        strbase64 = vec[1]
    else:
        return HttpResponse(json.dumps({"r": 1, "e": "忽略"}, ensure_ascii=False))
    binfile.write(base64.b64decode(strbase64))
    binfile.close()
    start = start + step
    if start >= total:
        if os.path.getsize(filepath) == total:
            r = 200
            _logger.info('file upload ok filename={}'.format(filepath))
            vermagic = _get_vermagic(filepath)
            os.remove(filepath)

    return HttpResponse(
        json.dumps({"r": r, "e": "操作成功", "name": name, "start": start, 'vermagic': vermagic}, ensure_ascii=False))


def _count_records_tab(tabs_list, tabname):
    for tab in tabs_list:
        if tab['name'] == tabname:
            tab['count'] = tab['count'] + 1
            return True
    return False


def get_linux_tabs(request):
    clerware_linux_driver_magics_obj = _get_clerware_linux_driver_magics_obj()
    vermagic_list = list()
    # {disksbd_linux:{},sbdfun_linux:{}}
    for _disksbd_linux_key in clerware_linux_driver_magics_obj.keys():
        _disksbd_linux_value = clerware_linux_driver_magics_obj[_disksbd_linux_key]

        # {'3.10.xx':'3.10.xxc'}
        for subkey in _disksbd_linux_value.keys():
            ipos = subkey.find('ClerwareBuildOrgDriver')
            if ipos == -1:
                continue
            if not _have_vermagic(vermagic_list, subkey):
                _subvalue = _disksbd_linux_value[subkey]
                _post = _subvalue.find('.ClerwareBuildOrgDriver')
                if -1 != _post:
                    _subvalue = _subvalue[:_post]
                _post = _subvalue.find('ClerwareBuildOrgDriver')
                if -1 != _post:
                    _subvalue = _subvalue[:_post]
                vermagic_list.append(
                    {"ko": _disksbd_linux_key, "src_vermagic": subkey, "src_vermagic_show": '{}'.format(_subvalue),
                     "user_vermagic_list": list()})

    vermagic_list = sorted(vermagic_list, key=lambda x: sort_key(x['src_vermagic_show']))

    object_list = list()
    for vermagic in vermagic_list:
        name = vermagic['src_vermagic_show'].split('-')[0]
        if not _count_records_tab(object_list, name):
            object_list.append({'name': name, 'count': 1})
    object_list = sorted(object_list, key=lambda x: x['name'])

    result = {'r': 0, 'tabs': object_list}
    return HttpResponse(json.dumps(result, ensure_ascii=False))


def _get_src_vermagic_show(clerware_linux_driver_magics_obj, src_vermagic):
    for _disksbd_linux_key in clerware_linux_driver_magics_obj.keys():
        _disksbd_linux_value = clerware_linux_driver_magics_obj[_disksbd_linux_key]
        for subkey in _disksbd_linux_value.keys():
            if subkey == src_vermagic:
                _subvalue = _disksbd_linux_value[subkey]
                _post = _subvalue.find('.ClerwareBuildOrgDriver')
                if -1 != _post:
                    _subvalue = _subvalue[:_post]
                _post = _subvalue.find('ClerwareBuildOrgDriver')
                if -1 != _post:
                    _subvalue = _subvalue[:_post]
                return _subvalue
    return 'unknown-{}'.format(src_vermagic)


def get_internal_list(request):
    linux_name = request.GET.get('name')
    clerware_linux_driver_magics_obj = _get_clerware_linux_driver_magics_obj()
    vermagic_list = list()
    compatible_vermagic_dict = _get_compatible_vermagic_db()

    for key in compatible_vermagic_dict.keys():
        sym_file = compatible_vermagic_dict[key]['sym_file']
        src_vermagic = compatible_vermagic_dict[key]['src_vermagic']
        if not _have_vermagic(vermagic_list, src_vermagic):
            src_vermagic_show = _get_src_vermagic_show(clerware_linux_driver_magics_obj, src_vermagic)
            os_name = src_vermagic_show.split('-')[0]
            if not os.path.isfile(os.path.join(g_clerware_linux_driver_compatible_vermagic_dir, sym_file)):
                os_name = 'no_sym_file'
            if os_name != linux_name:
                continue
            vermagic_list.append(
                {"src_vermagic": src_vermagic, "src_vermagic_show": '{}'.format(src_vermagic_show),
                 "compatible_vermagic_list": list()})

        if sym_file[-3:] == '.gz':
            sym_file = sym_file[:-3]
        sym_file = sym_file.split('_')[0]
        src_vermagic = compatible_vermagic_dict[key]['src_vermagic']
        compatible_vermagic = '{}__{}'.format(sym_file, key)
        _insert_into_compatible_vermagic_list(vermagic_list, src_vermagic, compatible_vermagic)

    vermagic_list = sorted(vermagic_list, key=lambda x: sort_key(x['src_vermagic_show']))

    page = 1
    total = 0
    records = 0
    object_list = list()
    id = 0
    index = 1
    for vermagic in vermagic_list:
        vermagic['compatible_vermagic_list'].sort(key=sort_key)
    for vermagic in vermagic_list:
        compatible_vermagic = '<br>'.join(vermagic['compatible_vermagic_list'])
        object_list.append(
            [index, id, vermagic['src_vermagic_show'], compatible_vermagic,
             vermagic['src_vermagic_show'].split('.')[0]])
        index = index + 1
        id = id + 1
    result = {'r': 0, 'a': 'list', 'page': page, 'total': total, 'records': records, 'rows': object_list}
    return HttpResponse(json.dumps(result, ensure_ascii=False))


def get_linux_internal_tabs(request):
    object_list = list()
    clerware_linux_driver_magics_obj = _get_clerware_linux_driver_magics_obj()
    compatible_vermagic_dict = _get_compatible_vermagic_db()
    for key in compatible_vermagic_dict.keys():
        src_vermagic = compatible_vermagic_dict[key]['src_vermagic']
        sym_file = compatible_vermagic_dict[key]['sym_file']
        src_vermagic_show = _get_src_vermagic_show(clerware_linux_driver_magics_obj, src_vermagic)
        os_name = src_vermagic_show.split('-')[0]
        if not os.path.isfile(os.path.join(g_clerware_linux_driver_compatible_vermagic_dir, sym_file)):
            os_name = 'no_sym_file'
        if not _count_records_tab(object_list, os_name):
            object_list.append({'name': os_name, 'count': 1})
    object_list = sorted(object_list, key=lambda x: x['name'])
    result = {'r': 0, 'tabs': object_list}
    return HttpResponse(json.dumps(result, ensure_ascii=False))
