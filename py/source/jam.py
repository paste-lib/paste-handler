import datetime
import hashlib
import httplib
import types

from ..service.jammer import Jammer
from ..service.speed import Speed

DATE_FMT = '%a, %d %b %Y %H:%M:%S GMT'


def _if_modified_since(if_modified_since, last_modified):
    modified = datetime.datetime.fromtimestamp(last_modified).strftime(DATE_FMT)
    if modified == if_modified_since:
        return True

    return False


class JamResponse(object):
    def __init__(self, jam, headers=None, code=httplib.OK):
        super(JamResponse, self).__init__()
        self._jam = jam

        self.headers = headers or {}
        self.code = code

    @property
    def body(self):
        return self._jam.contents if isinstance(self._jam, Jammer) else ''


def handle_jam_request(request_path, if_modified_since, require_dependencies=False, excluded_dependencies=None):
    # !warning: if you feel the urge to make changes here, please consult pagespeed and yslow docs
    # step 0. sometimes the dynamic mod time or dependency order can change depending on how the request uri was formed.
    #         if there is an old request out there, redirect to the correct url
    # step 1. check the dynamic last mod. if its cool, return the correct status code
    # step 2a. if we can't return 304, build out the response by using jammer content
    # step 2b. check the gel_page_cache for the generated file
    # step 2c. add headers that browsers love for assets
    # step 3. return the correct code, message, and body
    # note 1. this bypasses a renderer and returns directly to dispatcher
    # note 2. we gzip in nginx. in dev/debug, we manually deflate

    # test cases:
    # 1. /paste/ -> 400
    # 2. /paste/<ver-1>/paste.js -> 302
    # 3. /paste/<ver+1>/paste.js -> 404
    # 4. /paste/paste.*js -> 302
    # 5. /paste/paste.js -> 302
    # 6. /paste/require/paste.event.js -> 302
    # 7. /paste/require/paste.js?filter=paste -> 400
    # 8. /paste/require/paste.event.js?filter=paste.oop -> 302
    # 9. /paste/require/paste.event%2Cpaste.framerate.js -> 302

    _jam = Jammer(request_path=request_path, require_dependencies=require_dependencies)

    path_dependencies = _jam.parse_request_path_dependencies(request_path)
    path_last_modified = _jam.parse_request_path_last_modified(request_path)

    if isinstance(excluded_dependencies, types.StringTypes):
        excluded_dependencies = set(dependency_name.strip() for dependency_name in excluded_dependencies.split(','))
    if isinstance(excluded_dependencies, set):
        _jam.filter_loaded(excluded_dependencies)

    has_valid_jam = _jam is not None and _jam.uri

    if not has_valid_jam:
        return JamResponse(None, code=httplib.BAD_REQUEST)

    last_modified_required = not _jam.is_debug
    if (_jam.checksum != path_dependencies or
            (last_modified_required and
                 (not path_last_modified or not path_last_modified.isdigit() or
                          int(path_last_modified) < _jam.last_modified))):
        return JamResponse(
            _jam,
            code=httplib.FOUND,
            headers={
                'Access-Control-Allow-Origin': '*',
                'Cache-Control': 'private, no-cache, no-store, max-age=0, must-revalidate',
                'Expires': datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT'),
                'ETag': hashlib.md5(_jam.checksum + '-' + str(_jam.last_modified)).hexdigest(),
                'Location': _jam.uri
            }
        )
    elif last_modified_required and _jam.last_modified != int(path_last_modified):
        return JamResponse(None, code=httplib.NOT_FOUND)
    elif _if_modified_since(if_modified_since, path_last_modified):
        return JamResponse(None, code=httplib.NOT_MODIFIED)
    else:
        headers = {}
        Speed.header_caching(request_path, headers.setdefault, _jam.last_modified,
                             hashlib.md5(_jam.checksum + '-' + str(_jam.last_modified)).hexdigest())
        return JamResponse(
            _jam,
            code=httplib.OK,
            headers=headers
        )