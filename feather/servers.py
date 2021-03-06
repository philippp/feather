import errno
import os
import socket

import greenhouse
from feather import connections


__all__ = ["BaseServer", "TCPServer", "UDPServer"]


class BaseServer(object):
    """purely abstract server class.

    subclass TCPServer or UDPServer instead (or just use them as they are).
    """
    address_family = socket.AF_INET
    socket_protocol = socket.SOL_IP
    worker_count = 5

    def __init__(self, address):
        self.address = address
        self.is_setup = False
        self.shutting_down = False

    def init_socket(self):
        self.socket = greenhouse.Socket(
                self.address_family,
                self.socket_type,
                self.socket_protocol)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    def setup(self):
        self.pre_fork_setup()
        self.setup_children()
        self.is_setup = True

    def pre_fork_setup(self):
        if not hasattr(self, "socket"):
            self.init_socket()
        self.socket.bind(self.address)

    def setup_children(self):
        for i in xrange(self.worker_count - 1):
            if not os.fork():
                # children will need their own epoll object
                greenhouse.poller.set()
                break # no grandchildren

    def serve(self):
        raise NotImplementedError()

    @property
    def host(self):
        return self.address[0]

    @host.setter
    def host(self, value):
        self.address[0] = value

    @property
    def port(self):
        return self.address[1]

    @port.setter
    def port(self, value):
        self.address[1] = value


class TCPServer(BaseServer):
    """the master TCP server

    to use, create an instance with a (host, port) address pair, customize it
    by setting certain attributes, and then just call its serve() method.

    * connection_handler is an attribute that should be a subclass of
      TCPConnection that will handle individual client connections. each
      instance of the connection_handler (for each connection) will be run in
      its own coroutine.

    * worker_count is the number of processes to have run the server. it will
      fork worker_count - 1 children, as the original process itself acts as
      a worker. it defaults to 5, so if your application is utilizing
      module-global memory be sure to set it to 1.

    * listen_backlog is the number of connections to allow to queue up when the
      server can't accept them fast enough. its default is the maximum allowed
      by the system.
    """
    socket_type = socket.SOCK_STREAM
    listen_backlog = socket.SOMAXCONN
    connection_handler = connections.TCPConnection

    def __init__(self, *args, **kwargs):
        super(TCPServer, self).__init__(*args, **kwargs)
        self.killable = {}

    def pre_fork_setup(self):
        super(TCPServer, self).pre_fork_setup()
        self.socket.listen(self.listen_backlog)

    def serve(self):
        """run the server at the provided address forever.

        this method will remove the calling greenlet (generally the main
        greenlet) from the scheduler, so don't expect anything else to run in
        the calling greenlet until the server has been shut down.
        """
        if not self.is_setup:
            self.setup()

        try:
            while not self.shutting_down:
                try:
                    client_sock, client_address = self.socket.accept()
                    handler = self.connection_handler(
                            client_sock,
                            client_address,
                            self.address,
                            self.killable)
                    greenhouse.schedule(handler.serve_all)
                except socket.error, error:
                    if err.args[0] == errno.EMFILE:
                        # max open connections for the process
                        if not self.killable:
                            # if all connections are active, just wait a
                            # while before accepting a new connection again
                            greenhouse.pause_for(0.01)
                            continue

                        # close all the connections that are
                        # only open for keep-alive anyway
                        for fd in self.killable.keys():
                            handler = self.killable.pop(fd)
                            handler.socket.close()
                            handler.closed = True
                    elif err.args[0] == errno.ENFILE:
                        # max open connections for the machine
                        greenhouse.pause_for(0.01)
                    else:
                        raise
        except KeyboardInterrupt:
            pass
        finally:
            self.socket.close()


class UDPServer(BaseServer):
    socket_type = socket.SOCK_DGRAM

    def serve(self):
        if not self.is_setup:
            self.setup()
        ##XXX: finish this
