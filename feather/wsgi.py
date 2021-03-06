try:
    from cStringIO import StringIO
except ImportError:
    from StringIO import StringIO
import inspect

from feather import http, servers
import greenhouse


__all__ = ["WSGIRequestHandler", "serve"]


# the hoops one has to jump through to let the 'wsgiapp'
# attribute be set on a class without it becoming a bound method
class _wsgiapp_callable(type):
    def __new__(metacls, name, bases, attrs):
        attrs['_wsgiapp_container'] = (attrs['wsgiapp'],)
        return super(_wsgiapp_callable, metacls).__new__(
                metacls, name, bases, attrs)


class WSGIRequestHandler(http.HTTPRequestHandler):
    """a fully implemented HTTPRequestHandler, ready to run a WSGI app.
    
    subclass and override the wsgiapp attribute to your wsgi application and
    you are off to the races.
    """
    __metaclass__ = _wsgiapp_callable

    wsgiapp = None

    def do_everything(self, request):
        environ = {
            'wsgi.version': (1, 0),
            'wsgi.url_scheme': request.scheme or 'http',
            'wsgi.input': request.content,
            'wsgi.errors': greenhouse.exception_file,
            'wsgi.multithread': False,
            'wsgi.multiprocess': True, #XXX: set this appropriately
            'wsgi.run_once': False,
            'SCRIPT_NAME': '',
            'PATH_INFO': request.path,
            'SERVER_NAME': self.server_address[0],
            'SERVER_PORT': self.server_address[1],
            'REQUEST_METHOD': request.method,
            'SERVER_PROTOCOL': "HTTP/%s.%s" % tuple(request.version),
        }

        if request.querystring:
            environ['QUERY_STRING'] = request.querystring

        if 'content-length' in request.headers:
            environ['CONTENT_LENGTH'] = int(request.headers['content-length'])

        if 'content-type' in request.headers:
            environ['CONTENT_TYPE'] = request.headers['content-type']

        for name, value in request.headers.items():
            environ['HTTP_%s' % name.replace('-', '_').title()] = value

        collector = (StringIO(), False) # (write()en data, headers sent)

        def write(data):
            collector[0].write(data)
            collector[1] = True

        def start_response(status, headers, exc_info=None):
            if exc_info and collector[1]:
                raise exc_info[0], exc_info[1], exc_info[2]
            else:
                exc_info = None

            for i, c in enumerate(status):
                if i and not status[:i].isdigit():
                    break
            self.set_code(int(status[:i - 1]))
            self.add_headers(headers)

            return write

        body = self._wsgiapp_container[0](environ, start_response)
        prefix = collector[0].getvalue()

        if prefix:
            body_iterable = iter(body)
            try:
                first_chunk = body_iterable.next()
            except StopIteration:
                first_chunk = ''
            body = itertools.chain((prefix + first_chunk,), body_iterable)

        self.set_body(body)

    do_GET = do_POST = do_PUT = do_HEAD = do_DELETE = do_everything

    def __getattr__(self, name):
        if name.startswith("do_"):
            return do_everything
        raise AttributeError(name)


def serve(address, app, debug=False):
    "shortcut function to serve a wsgiapp on an address"
    class RequestHandler(WSGIRequestHandler):
        wsgiapp = app
        traceback_debug = debug

    class Connection(http.HTTPConnection):
        request_handler = RequestHandler

    server = servers.TCPServer(address)
    server.connection_handler = Connection
    server.serve()
