import contextlib
import os

from six.moves import urllib_parse
from taskflow.persistence import backends

from box_dashboard import xlogging

_logger = xlogging.getLogger(__name__)

__backend = None


def get_backend():
    global __backend
    if __backend is not None:
        return __backend

    backend_uri = get_backend_uri()

    try:
        __backend = backends.fetch(_make_conf(backend_uri))
    except Exception as e:
        _logger.error(r'call backends.fetch failed : {}'.format(e), exc_info=True)
        raise e

    # Ensure schema upgraded before we continue working.
    with contextlib.closing(__backend.get_connection()) as conn:
        conn.upgrade()
    return __backend


def get_backend_uri():
    _backend_dir = r'/var/db/tasks'
    os.makedirs(_backend_dir, exist_ok=True)
    _backend_uri = r"file:///{}".format(_backend_dir)
    return _backend_uri


def _make_conf(backend_uri):
    parsed_url = urllib_parse.urlparse(backend_uri)
    backend_type = parsed_url.scheme.lower()
    if not backend_type:
        raise ValueError("Unknown backend type for uri: {}".format(backend_type))
    if backend_type in ('file', 'dir'):
        conf = {
            'path': parsed_url.path,
            'connection': backend_uri,
        }
    elif backend_type in ('zookeeper',):
        conf = {
            'path': parsed_url.path,
            'hosts': parsed_url.netloc,
            'connection': backend_uri,
        }
    else:
        conf = {
            'connection': backend_uri,
        }
    return conf
