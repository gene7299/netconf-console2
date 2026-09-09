"""Owned SSH direct-tcpip channel, without shell commands or external sshpass."""
import socket
from pathlib import Path

import paramiko

from .sshauth import authenticate


def open_jump(settings):
    if settings.transport != "ssh" or settings.call_home:
        raise ValueError("SSH 跳板只適用 Direct SSH；不支援 TLS 或 Call Home。")
    if not settings.jump_host or not settings.jump_username or not 1 <= settings.jump_port <= 65535:
        raise ValueError("請填寫跳板主機、port 與帳號。")
    sock = socket.create_connection((settings.jump_host, settings.jump_port), timeout=settings.timeout)
    transport = None
    try:
        transport = paramiko.Transport(sock)
        transport.auth_timeout = settings.timeout
        transport.banner_timeout = settings.timeout
        transport.start_client(timeout=settings.timeout)
        if settings.jump_verify:
            keys = paramiko.HostKeys()
            path = Path(settings.jump_known_hosts).expanduser() if settings.jump_known_hosts else Path.home() / ".ssh" / "known_hosts"
            keys.load(str(path))
            host = settings.jump_host if settings.jump_port == 22 else "[%s]:%s" % (settings.jump_host, settings.jump_port)
            if not keys.check(host, transport.get_remote_server_key()):
                raise paramiko.SSHException("跳板 host key 不符或未知；請核對跳板 known_hosts。")
        authenticate(transport, settings.jump_username, settings.jump_password,
                     [settings.jump_key] if settings.jump_key else [],
                     settings.jump_auth in {"auto", "agent"}, False,
                     settings.jump_auth, settings.jump_passphrase)
        channel = transport.open_channel("direct-tcpip", (settings.host, settings.port),
                                         ("127.0.0.1", 0), timeout=settings.timeout)
        return transport, channel
    except Exception:
        if transport is not None:
            transport.close()
        sock.close()
        raise
