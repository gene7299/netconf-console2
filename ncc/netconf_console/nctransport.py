"""NETCONF over plain TCP implementation. Derives heavily from SSH to
reuse in/out processing methods."""

from ncclient import transport
import socket
from contextlib import contextmanager
import os
import threading
from pathlib import Path


class SSHSession(transport.SSHSession):
    "SSHSession extension capable of saving the raw data."
    def __init__(self, device_handler, raw_file):
        self.raw_file = raw_file
        self._raw_stream = None
        self._owns_raw_stream = False
        if isinstance(raw_file, (str, bytes, os.PathLike)):
            raw_path = Path(os.fspath(raw_file)).expanduser()
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            self._raw_stream = raw_path.open("a", encoding="utf-8", newline="")
            self._owns_raw_stream = True
        elif raw_file is not None and hasattr(raw_file, "write"):
            self._raw_stream = raw_file
        super(SSHSession, self).__init__(device_handler)
        self.last_pos = 0

    def _parse10(self):
        with self.raw_processing(self._parsing_pos10):
            super(SSHSession, self)._parse10()

    def _parse11(self):
        with self.raw_processing(self._parsing_pos11):
            super(SSHSession, self)._parse11()

    @contextmanager
    def raw_processing(self, position):
        if self._raw_stream is not None:
            self._raw_stream.write(self._buffer.getvalue()[self.last_pos:].decode("utf8"))
            self._raw_stream.flush()
        try:
            yield
        finally:
            self.last_pos = self._buffer.tell()

    def close(self):
        try:
            super(SSHSession, self).close()
        finally:
            if self._owns_raw_stream and self._raw_stream is not None:
                self._raw_stream.close()
                self._raw_stream = None


class TCPChannel(object):
    def __init__(self, host, port):
        for res in socket.getaddrinfo(host, port,
                                      socket.AF_UNSPEC, socket.SOCK_STREAM):
            af, socktype, proto, canonname, sa = res
            try:
                sock = socket.socket(af, socktype, proto)
            except socket.error:
                sock = None
                continue
            try:
                sock.connect(sa)
            except socket.error:
                sock.close()
                sock = None
                continue
            break
        if sock is None:
            raise Exception("Could not connect to the host")
        self.sock = sock
        self.active = True
        self.lock = threading.Lock()

    def fileno(self):
        return self.sock.fileno()

    def close(self):
        with self.lock:
            self.sock.close()
            self.active = False

    def is_active(self):
        return self.active

    def deactivate(self):
        self.active = False

    def recv(self, *args):
        with self.lock:
            if self.is_active():
                return self.sock.recv(*args)
            # force the session to terminate
            return b''

    def send(self, data):
        return self.sock.send(data)

    def send_ready(self):
        return True


class TCPTransport(object):
    def __init__(self, channel):
        self.channel = channel

    def close(self):
        # don't close the socket yet, just deactivate the channel
        self.channel.deactivate()

    def is_active(self):
        return self.channel.is_active()


class TCPSession(SSHSession):
    """TCP session as an extension of SSH session - parsing is the same,
but connect and authentication differs greatly.

    """
    def connect(self, host, port=2023, username=None, password=None, **ignored):
        self._channel = TCPChannel(host, port)
        sockname = self._channel.sock.getsockname()
        # os.getuid/getgid and HOME are Unix-only.  TCP mode is legacy and
        # non-standard, but keeping it importable on Windows avoids making
        # the old compatibility path a platform trap.
        getuid = getattr(os, "getuid", lambda: 0)
        getgid = getattr(os, "getgid", lambda: 0)
        home = os.getenv("HOME") or os.getenv("USERPROFILE") or os.path.expanduser("~")
        login_msg = "[%s;%s;tcp;%d;%d;%s;%s;%s;]\n" % (username, sockname[0],
                                                       getuid(), getgid(), "",
                                                       home, "")
        self._channel.send(login_msg.encode())
        self._transport = TCPTransport(self._channel)
        self._connected = True
        self._post_connect()
