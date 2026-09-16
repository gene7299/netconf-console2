from netconf_console.gnutls import GnuTLSError
from netconf_console.testapi import classify_exception


def test_gnutls_close_notify_is_a_peer_observation():
    error = GnuTLSError(
        "TLS handshake", -110,
        "The TLS connection was non-properly terminated.",
        alert="Close notify",
    )
    result = classify_exception(error, "auth")
    assert result["code"] == "TLS_PEER_ALERT"
    assert result["origin"] == "peer"
