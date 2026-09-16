"""Structured machine-API classification regression tests."""

from ncclient.transport.errors import AuthenticationError

from netconf_console.testapi import classify_exception


def test_ncclient_authentication_error_is_peer_ssh_rejection():
    result = classify_exception(AuthenticationError("Authentication failed."), "auth")
    assert result == {
        "code": "SSH_AUTHENTICATION_REJECTED",
        "origin": "peer",
        "layer": "ssh",
        "summary": "Authentication failed.",
    }
