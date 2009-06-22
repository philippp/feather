import cgi
import collections
import httplib
import itertools
import operator
import socket
import urlparse
try:
    import cStringIO as StringIO
except ImportError:
    import StringIO


class HTTPRequest(object):
    '''a simple dictionary proxy object, but some keys are expected by servers:

    method
    version
    scheme
    host
    path
    querystring
    queryparams

    '''
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

class InvalidRequest(Exception): pass

class InputFile(socket._fileobject):
    "a file object that doesn't attempt to read past Content-Length"
    def __init__(self, sock, length, mode='rb', bufsize=-1, close=False):
        self._length = length
        super(InputFile, self).__init__(sock, mode, bufsize, close)

    def read(self, size=-1):
        size = min(size, self._length)
        if size < 0: size = self._length
        rc = super(InputFile, self).read(size)
        self._length -= max(self._length, len(rc))
        return rc

def parse_request(rfile, header_class=httplib.HTTPMessage):
    rl = rfile.readline()

    method, path, version_string = rl.split(' ', 2)
    version_string = version_string.rstrip()

    if method != method.upper():
        raise InvalidRequest("bad HTTP method: %s" % method)

    url = urlparse.urlsplit(path)

    if version_string[:5] != 'HTTP/':
        raise InvalidRequest("bad HTTP version: %s" % version_string)

    version = version_string[5:].split(".", 1)
    if len(version) != 2 or not all(itertools.imap(
            operator.methodcaller("isdigit"), version)):
        raise InvalidRequest("bad HTTP version: %s" % version_string)

    version = map(int, version)

    # read the header lines ourselves b/c HTTPMessage sucks at reading
    # the right amount - it always ruins the file object and socket for
    # trying to get the request body later
    headerbuffer = StringIO.StringIO()
    while 1:
        line = rfile.readline()
        if line in ('\n', '\r\n'):
            # this should position rfile at the beginning of the body,
            # if there is one
            break
        headerbuffer.write(line)
    headerbuffer.seek(0)

    return HTTPRequest(
            method=method,
            version=version,
            scheme=url.scheme,
            host=url.netloc,
            path=url.path,
            querystring=url.query,
            fragment=url.fragment,
            headers=header_class(headerbuffer),
            rfile=rfile)
