import BaseHTTPServer
import httplib
import itertools
import socket
import traceback
import urlparse

from feather import connections, requests
import greenhouse


__all__ = ["InputFile", "HTTPError", "HTTPRequest", "HTTPRequestHandler",
        "HTTPConnection"]

responses = BaseHTTPServer.BaseHTTPRequestHandler.responses


class HTTPRequest(object):
    '''a straightforward attribute holder that supports the following names:

    method
        GET, POST, PUT, HEAD, DELETE, etc.

    version
        the HTTP version as a tuple of integers. generally (1, 0) or (1, 1)

    scheme
        string of either "http" or "https"

    host
        the server host - tried to get first from Host: header, then from host
        in the path, then (if the path was relative) takes it from the server

    path
        the accessed path from the first line of the request

    querystring
        the full querystring from the path

    headers
        an object representing the HTTP headers. unless overridden,
        HTTPConnection provides an instance of httplib.HTTPMessage

    content
        a file-like object from which you can read[line[s]]() the body of the
        request
    '''
    __slots__ = [
            "method",
            "version",
            "scheme",
            "host",
            "path",
            "querystring",
            "headers",
            "content"]

    def __init__(self, **kwargs):
        for name in self.__slots__:
            setattr(self, name, kwargs.get(name, None))


class HTTPError(Exception):
    """raise this in an HTTPRequestHandler instance/subclass to bail out and
    send an error response
    """
    def __init__(self, code=500, body=None, headers=None):
        self.code = code
        self.body = body
        self.headers = headers or []


class InputFile(socket._fileobject):
    """a file object that doesn't attempt to read past a specified length.

    unless overridden, HTTPConnection uses this as request.content
    """
    def __init__(self, sock, length, mode='rb', bufsize=-1, close=False):
        self.length = length
        super(InputFile, self).__init__(sock, mode, bufsize, close)

    def read(self, size=-1):
        size = min(size, self.length)
        if size < 0: size = self.length
        rc = super(InputFile, self).read(size)
        self.length -= max(self.length, len(rc))
        return rc

    def readlines(self):
        text = self.read()
        if text[-1] == "\n":
            text = text[:-1]
        return map(self._line_mapper, text.split("\n"))

    @staticmethod
    def _line_mapper(l):
        return l + '\n'


def _strip_first(iterable):
    iterator = iter(iterable)
    try:
        first = iterator.next()
    except StopIteration:
        first = ''
    return first, iterator


class HTTPRequestHandler(requests.RequestHandler):
    """the main application entry-point, this class handles a single request
    
    subclass this and provide do_METHOD methods for every HTTP method you wish
    to support (do_GET and do_POST is a good place to start).

    those methods should call methods provided by HTTPRequestHandler to set the
    proper response:

    set_code(code)
        the integer HTTP response code (200 for successful)

    set_body(body)
        the argument may be either a string or an iterable of strings. in the
        latter case it is acceptable for it to be a generator or other lazy
        iterator to allow a long response to be generated and sent gradually
        without blocking the whole server process.

    add_header(name, value)
        add a single response header

    add_headers(headers)
        add a group of headers.  provide two-tuples of (name, value) pairs
    """
    traceback_debug = False

    def __init__(self, *args, **kwargs):
        super(HTTPRequestHandler, self).__init__(*args, **kwargs)
        self._headers = []
        self._code = self._body = None

    def set_code(self, code):
        self._code = code

    def set_body(self, body):
        self._body = body

    def add_header(self, name, value):
        self._headers.append((name, value))

    def add_headers(self, headers):
        self._headers.extend(headers)

    def handle(self, request):
        handler = getattr(self, "do_%s" % request.method, None)

        try:
            if not handler:
                raise HTTPError(405) # Method Not Allowed

            handler(request)

        except HTTPError, error:
            self.translate_http_error(error)

        except NotImplemented:
            self.translate_http_error(HTTPError(405))

        except:
            if self.traceback_debug:
                self.set_body(traceback.format_exc())
            self.set_code(500)
            self.add_header('Content-type', 'text/plain')

        return self.format_response()

    def _have_header(self, header, required_value=None):
        header = header.lower()
        for name, value in self._headers:
            if name.lower() == header and \
                    (required_value is None or value == required_value):
                return True
        return False

    def translate_http_error(error):
        self.set_code(error.code)
        if not self._have_header('content-type'):
            self.add_header('Content-type', 'text/plain')
        self.add_headers(error.headers)
        self.set_body(error.body)

    def format_response(self):
        http_version = '.'.join(map(str, self._connection.http_version))
        code = self._code or 200
        status, long_status = responses[code]
        if self._body is None:
            self._body = long_status

        closed = self._have_header('connection', 'close')

        # we MUST either send a Content-Length or close the connection
        if not self._have_header('content-length'):
            if isinstance(self._body, str):
                self.add_header('Content-Length', len(self._body))
            else:
                if not closed:
                    closed = True
                    self.add_header('Connection', 'close')
                self._connection.closing = True

        if not self._connection.keepalive_timeout and not closed:
            self.add_header('Connection', 'close')
            close = True

        headers = '\r\n'.join('%s: %s' % pair for pair in self._headers)

        head = 'HTTP/%s %d %s\r\n%s\r\n\r\n' % (
                http_version, code, status, headers)

        if isinstance(self._body, str):
            return (head + self._body,)

        # we don't want the headers to go in their own send() call, so prefix
        # them to the first item in the body iterable, then re-prefix that item
        iterator = iter(self._body)
        try:
            first_chunk = iterator.next()
        except StopIteration:
            first_chunk = ''
        return itertools.chain([head + first_chunk], iterator)

    def do_GET(self, request):
        raise NotImplementedError()

    do_POST = do_PUT = do_HEAD = do_DELETE = do_GET


class HTTPConnection(connections.TCPConnection):
    """TCPConnection that speaks HTTP
    
    there is not much overriding to be done at this level, but there are a few
    attributes that might be useful to change:

    request_handler
        this must be your HTTPRequestHandler subclass

    keepalive_timeout
        time in seconds before an inactive connection is closed. set to 0 to
        disable keepalive entirely

    http_version
        HTTP version number for responses as a tuple of ints. default is (1, 1)

    header_class
        callable that accepts a file-like object and returns a representation
        of the HTTP headers which will be used as request.headers. must not
        read more input than necessary. the default of httplib.HTTPMessage is a
        good one
    """
    request_handler = HTTPRequestHandler

    # header-parsing class from the stdlib
    header_class = httplib.HTTPMessage

    # we don't support changing the HTTP version inside a connection,
    # because that's just silliness
    http_version = (1, 1)

    keepalive_timeout = 30

    def __init__(self, *args, **kwargs):
        super(HTTPConnection, self).__init__(*args, **kwargs)
        self.keepalive_timer = None
        self._timer = None

    def _hit_timer(self):
        self.closing = True
        self._timer = None

    def start_timer(self):
        self.cancel_timer()
        if self.keepalive_timeout:
            self._timer = greenhouse.Timer(self.keepalive_timeout,
                    self._hit_timer)

    def cancel_timer(self):
        if self.keepalive_timeout and self._timer:
            self._timer.cancel()
            self._timer = None

    def get_request(self):
        try:
            self.killable = False
            self.socket.settimeout(self.keepalive_timeout)
            content = InputFile(self.socket, 0)
            request_line = content.readline()

            if request_line in ('\n', '\r\n'):
                request_line = content.readline()

            if not request_line:
                return None

            method, path, version_string = request_line.split(' ', 2)
            version_string = version_string.rstrip()

            if not method.isalpha() or method != method.upper():
                return None

            url = urlparse.urlsplit(path)

            if version_string[:5] != 'HTTP/':
                return None

            try:
                version = tuple(int(v) for v in version_string[5:].split("."))
            except ValueError:
                return None

            headers = self.header_class(content)

            scheme = url.scheme or "http"
            host = headers.get('host') or url.netloc or self.server_address

            if version < (1, 1):
                self.closing = True
            else:
                for name, val in headers.items():
                    if name.lower() == 'connection' and val.lower() == 'close':
                        self.closing = True
                        break

            if 'content-length' in headers:
                content.length = int(headers['content-length'])

            self.cancel_timer()

            return HTTPRequest(
                    method=method,
                    version=version,
                    scheme=scheme,
                    host=host,
                    path=url.path,
                    querystring=url.query,
                    fragment=url.fragment,
                    headers=headers,
                    content=content)

        except:
            return None
