import json
import html
import operator

from django.http import HttpResponse

from box_dashboard import xlogging


class Router(object):
    def __init__(self, g_func):
        self.g = g_func

    def start_response(self, request):
        a = request.GET.get('a', None)
        a = request.POST.get('a', None) if (a is None) else a
        if a in self.g and callable(self.g[a]):
            return self.g[a](request)
        else:
            return HttpResponse(json.dumps({"r": "1", "e": "没有对应的action:{}".format(html.escape(a))}))


def tag(name, *content, attrs=None):
    """
    examples:

    tag('p', 'hello', attrs={'id':'demo', 'class':'demo'})
        -->'<p class="demo" id="demo">hello</p>'

    tag('img', attrs={'id':'demo', 'class':'demo', 'url':'/static/demo.jpg'})
        -->'<img class="demo" id="demo" url="/static/demo.jpg" />'

    tag('p','hello','wold', attrs={'class':'demo', 'url':'/static/demo.jpg'})
        -->'<p class="demo" url="/static/demo.jpg">hello</p><p class="demo" url="/static/demo.jpg">wold</p>'
    """
    if attrs and isinstance(attrs, dict):
        attr_str = ''.join(
            (' {}="{}"'.format(k, v) for k, v in sorted(attrs.items()))
        )

    else:
        attr_str = ''

    if content:
        return ''.join(
            ('<{}{}>{}</{}>'.format(name, attr_str, c, name) for c in content)
        )
    else:
        return '<{}{} />'.format(name, attr_str)


def show_names(model, query_ids):
    """
    show_names(User, [1,2,3])
        -->'admin,test,test1'
    get model name str
    """
    try:
        query_ids = list(query_ids)
        instance = model()
        if hasattr(instance, 'name'):
            show = operator.attrgetter('name')
        elif hasattr(instance, 'username'):
            show = operator.attrgetter('username')
        elif hasattr(instance, 'display_name'):
            show = operator.attrgetter('display_name')
        else:
            raise Exception
        return ','.join((show(obj) for obj in model.objects.filter(id__in=query_ids)))
    except:
        return query_ids


def sort_gird_rows(req, res):
    """
    res: {'rows': [
        {'cell': [lab_id, name, credible_time, crawl_site_name, is_on, last_run, lab_status, crawl_curr_site_name], 'id': lab_id},
        {'cell': [lab_id, name, credible_time, crawl_site_name, is_on, last_run, lab_status, crawl_curr_site_name], 'id': lab_id},
        {'cell': [lab_id, name, credible_time, crawl_site_name, is_on, last_run, lab_status, crawl_curr_site_name], 'id': lab_id}
    ]}
    """

    sord, sidx = req.GET.get('sord', None), req.GET.get('sidx', None)
    if not any([sord, sidx]):
        return None

    if sord not in ['asc', 'desc']:
        return None

    if not sidx.isdigit() or int(sidx) < 0:
        return None

    col_index, reverse = int(sidx), sord == 'desc'
    res['rows'] = sorted(res['rows'], key=lambda item: item['cell'][col_index], reverse=reverse)


@xlogging.convert_exception_to_value('0')
def format_size(bytes, precision=2):
    bytes = int(bytes)
    if bytes < 1024:
        return '{}B'.format(bytes)
    elif 1024 <= bytes < 1024 ** 2:
        return '{:.{precision}f}KB'.format(bytes / 1024, precision=precision)
    elif 1024 ** 2 <= bytes < 1024 ** 3:
        return '{:.{precision}f}MB'.format(bytes / 1024 ** 2, precision=precision)
    elif 1024 ** 3 <= bytes < 1024 ** 4:
        return '{:.{precision}f}GB'.format(bytes / 1024 ** 3, precision=precision)
    elif 1024 ** 4 <= bytes < 1024 ** 5:
        return '{:.{precision}f}TB'.format(bytes / 1024 ** 4, precision=precision)
    elif 1024 ** 5 <= bytes:
        return '{:.{precision}f}PB'.format(bytes / 1024 ** 4, precision=precision)

    return '{}B'.format(bytes)


@xlogging.convert_exception_to_value('0.00%')
def format_progress(member, denominator, precision=2):
    return '{:.{precision}%}'.format(int(member) / int(denominator), precision=precision)
