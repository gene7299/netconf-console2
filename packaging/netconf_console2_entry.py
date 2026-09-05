"""PyInstaller entry point for the Windows standalone executable."""

from __future__ import annotations

import sys


def bundle_self_test() -> int:
    """Verify the frozen runtime and all four supported connection modes."""

    checks: list[tuple[str, object]] = []
    try:
        import ssl

        import bcrypt
        import lxml.etree
        import nacl.bindings
        import paramiko
        import prompt_toolkit
        from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
        from ncclient.transport import SSHSession, TLSSession

        from netconf_console.ncc import argparser, resolve_namespace, settings_from_namespace
        from netconf_console.operations import ConsoleDeviceHandler
        from netconf_console.session import (
            CallHomeListener,
            TracedSSHSession,
            TracedTLSSession,
            open_call_home,
            open_direct,
        )

        def parsed_settings(arguments: list[str]):
            namespace = resolve_namespace(argparser().parse_args(arguments))
            return settings_from_namespace(namespace, call_home=namespace.call_home)

        direct_ssh = parsed_settings([
            "--transport", "ssh", "--host", "192.0.2.10", "--port", "830",
            "--username", "bundle-test",
        ])
        direct_tls = parsed_settings([
            "--transport", "tls", "--host", "192.0.2.10", "--port", "6513",
            "--cert", "client.crt", "--key", "client.key", "--trusted-ca", "ca.pem",
        ])
        call_home_ssh = parsed_settings([
            "--call-home", "--transport", "ssh", "--listen-host", "127.0.0.1",
            "--listen-port", "4334", "--username", "bundle-test",
        ])
        call_home_tls = parsed_settings([
            "--call-home", "--transport", "tls", "--listen-host", "127.0.0.1",
            "--listen-port", "4335", "--cert", "client.crt", "--key", "client.key",
            "--trusted-ca", "ca.pem",
        ])

        ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        with CallHomeListener("127.0.0.1", 0, timeout=0.1) as listener:
            listener_ready = bool(listener.socket and listener.listen_address)
        checks.extend([
            (
                "Direct SSH",
                issubclass(TracedSSHSession, SSHSession)
                and callable(open_direct)
                and direct_ssh.transport == "ssh"
                and direct_ssh.port == 830
                and not direct_ssh.call_home,
            ),
            (
                "Direct TLS",
                issubclass(TracedTLSSession, TLSSession)
                and callable(open_direct)
                and direct_tls.transport == "tls"
                and direct_tls.port == 6513
                and direct_tls.cert == "client.crt"
                and direct_tls.key == "client.key"
                and direct_tls.trusted_ca == "ca.pem"
                and not direct_tls.call_home,
            ),
            (
                "SSH Call Home",
                callable(open_call_home)
                and listener_ready
                and call_home_ssh.transport == "ssh"
                and call_home_ssh.call_home
                and call_home_ssh.listen_port == 4334,
            ),
            (
                "TLS Call Home",
                callable(open_call_home)
                and callable(TracedTLSSession.connect_accepted)
                and listener_ready
                and call_home_tls.transport == "tls"
                and call_home_tls.call_home
                and call_home_tls.listen_port == 4335
                and call_home_tls.cert == "client.crt"
                and call_home_tls.key == "client.key"
                and call_home_tls.trusted_ca == "ca.pem",
            ),
            ("NETCONF XML", lxml.etree.LXML_VERSION),
            ("SSH crypto", paramiko.Transport and rsa and ec and ed25519 and bcrypt),
            ("PyNaCl", nacl.bindings.crypto_sign_BYTES),
            ("Interactive console", prompt_toolkit.__version__),
            ("Device handler", ConsoleDeviceHandler),
            ("Frozen executable", bool(getattr(sys, "frozen", False))),
        ])
    except Exception as exc:
        print("[FAIL] Bundle import check: %s: %s" % (exc.__class__.__name__, exc))
        return 1

    failed = False
    print("netconf-console2 bundle self-test")
    for name, value in checks:
        passed = bool(value)
        failed |= not passed
        print("[%s] %s" % ("OK" if passed else "FAIL", name))
    print("No remote peer connection or certificate/SSH handshake was performed.")
    return 1 if failed else 0


def main() -> int:
    if "--bundle-self-test" in sys.argv[1:]:
        return bundle_self_test()

    from netconf_console.ncc import main as console_main

    return int(console_main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
