# netconf-console2 headless test client

`netconf-console2` is the locked, PyInstaller-built Ubuntu CLI used by the
test311 transport driver. It intentionally excludes PySide6 and tkinter.

Build or refresh it from the repository root:

```bash
./packaging/build-ubuntu-cli.sh
sha256sum -c netconf-console2-cli/SHA256SUMS.txt
```

Machine-readable Direct TLS example:

```bash
./netconf-console2-cli/netconf-console2 \
  --test-api --transport tls --host 192.168.9.9 --port 6513 \
  --bind 192.168.9.252 \
  --cert testkey/netconf-client-chain.pem \
  --key testkey/netconf-client.key.pem \
  --trusted-ca testkey/ca-chain.pem \
  --no-hostname-verify \
  --events-file events.jsonl --result-file result.json
```

TLS Call Home uses the bundled GnuTLS backend and advertises RFC 8071 C4
`peer_allowed_to_send`. Use `--tls-server-name` whenever identity matching is
part of the test.
