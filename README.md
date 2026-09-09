# netconf-console2

`netconf-console2` is a Windows-native NETCONF CLI built on `ncclient`, with
O-RAN O-RU management-plane testing as the primary use case.  It supports
direct SSH/TLS sessions, SSH and TLS Call Home, RFC 6241 core operations,
RFC 5277 notifications, capability/session inspection, raw RPCs, profiles and
redacted wire tracing.

## Install on Windows 11

For a colleague who only needs to run the client, use the bundled Windows x64
executable. It embeds Python and all runtime dependencies:

```powershell
.\exportToColleagueEXE\netconf-console2.exe --bundle-self-test
.\exportToColleagueEXE\netconf-console2.exe --interactive
```

No Python or pip installation is required for that executable. TLS
certificates/keys, trusted CA files and optional SSH `known_hosts` files remain
external deployment inputs and are intentionally not embedded.

For source development, install from this directory instead:

```powershell
py -3 -m pip install .
netconf-console2 --help
```

Command history, line editing and completion are installed by default on
Windows. `.[interactive]` remains accepted as a compatibility install target.

Interactive commands are appended to
`%USERPROFILE%\\.netconf-console2\\history`. Press Up/Down to browse commands
from both the current and previous client runs, then press Enter to execute the
recalled command. Inline password values are removed from history; replaying a
stored `--password` option requests the password again through a masked prompt.

The source installation uses the Windows Python environment; the standalone
executable carries its own Python runtime. WSL is only a reference environment
for comparing behavior with Netopeer2-cli.

The colleague exports are intentionally separate:

- `exportToColleague/` contains the minimum installable Python source package,
  source-install scripts, wheel fallback and documentation.
- `exportToColleagueEXE/` contains only the standalone executable and its
  required usage, command-reference, license and checksum files.

To rebuild the executable on Windows from a fully installed development tree:

```powershell
py -3 -m pip install PyInstaller
.\packaging\build-windows-exe.ps1
```

The build script runs the unit suite, creates the one-file executable, runs its
frozen transport/dependency self-test, then performs real loopback NETCONF
hello handshakes over Direct SSH, Direct TLS/mTLS, SSH Call Home and TLS Call
Home/mTLS. It also regenerates `exportToColleagueEXE/SHA256SUMS.txt`.

## Direct SSH

```powershell
netconf-console2 --host 172.29.234.130 --port 830 `
  --username oranuser --transport ssh --password --interactive
```

Inside the console:

```text
status
capabilities
namespaces
get
get-config --db running
edit-config .\config.xml --target candidate --test-option set `
  --default-operation merge --error-option rollback-on-error
commit
get-schema ietf-interfaces --out .\yang\ietf-interfaces.yang
rpc .\request.xml
disconnect
```

Use `--key`, `--agent`, `--known-hosts` and `--hostkey-verify` for SSH key and
host-key based authentication.  Passwords passed as `--password` without a
value are requested with a masked prompt.  The client does not embed a
default username or password; provide credentials, a key, or an agent.

## Direct TLS / mTLS

```powershell
netconf-console2 --host 192.168.1.100 --port 6513 --transport tls `
  --cert .\certs\client.crt --key .\certs\client.key `
  --trusted-ca .\certs\ca.pem --tls-server-name oru.example --interactive
```

TLS verifies the peer certificate chain and hostname by default.  Use
`--tls-version 1.2|1.3`, `--crl`, or `--no-hostname-verify` when the deployment
requires those explicit policies.

## Call Home

SSH Call Home:

```powershell
netconf-console2 --call-home --transport ssh --listen-host 0.0.0.0 `
  --listen-port 4334 --username oranuser --password --interactive
```

TLS Call Home:

```powershell
netconf-console2 --call-home --transport tls --listen-host 0.0.0.0 `
  --listen-port 4335 --cert .\certs\client.crt --key .\certs\client.key `
  --trusted-ca .\certs\ca.pem --tls-server-name oru.example --interactive
```

The TLS Call Home listener accepts TCP, then starts a TLS **client** handshake
on that accepted socket before starting NETCONF.  This follows RFC 8071: only
the TCP initiator role is reversed; the NETCONF client remains the TLS client.

## Non-interactive RPC and diagnostics

```powershell
netconf-console2 --host 172.29.234.130 --username oranuser --password `
  --rpc .\request.xml --output pretty
Get-Content .\request.xml | netconf-console2 --host 172.29.234.130 `
  --username oranuser --password --rpc - --output raw
netconf-console2 --host 172.29.234.130 --username oranuser --password `
  --trace --trace-file .\logs\session.log --get-config --db running
```

`rpc` accepts an operation body or a full `<rpc message-id="...">` envelope.
RPC replies are XML; `--output raw` disables pretty printing.  Trace records
mask password/secret/private-key XML content and never read private-key files
for logging.

The aliases `user-rpc`, `subscribe`, `notifications`, `watch`, `namespace`, `auth`,
`knownhosts`, `cert`, `connect`, `listen`, `disconnect`, `outputformat`,
`get-data` and `edit-data` are also available in the interactive console.

## Native Windows GUI

Version 3.3.0 adds a separate `exportToColleagueEXE/netconf-console2-gui.exe`.
The CLI remains independent. GUI 3.3.6 includes manageable connection/account history
and current-user Windows DPAPI storage, including passwords and last-used fields.
Both catalogs support search, rename, load-for-edit and confirmed batch deletion.
Both the XML editor and the complete loaded DATA TREE can be exported as UTF-8 XML.
The advanced tab exports password-free JSON or encrypted full backups. The connection
panel can collapse, tree folds survive refresh, and opt-in reconnection preserves
drafts without replaying writes. Native window/taskbar icons use a dedicated app ID.
SSH host-key and TLS hostname verification default to off (TLS CA verification
remains required); saved user choices override these initial defaults.
The GUI uses the same Direct SSH/TLS and SSH/TLS
Call Home adapters, with running as its default source. It provides an expandable
instance tree, schema-aware default/state/change colours, an XML editor and an
exact outgoing edit-config preview. Network and schema work runs in the background.

```powershell
.\exportToColleagueEXE\netconf-console2-gui.exe
# Safe offline demonstration; no remote connection or device changes:
.\exportToColleagueEXE\netconf-console2-gui.exe --demo
```

Writes require complete device schemas, an explicit confirmation and a datastore
lock/conflict check. State and unknown nodes cannot be written. Only changes and
required list keys are sent; there is no implicit candidate commit or startup copy.
See [the GUI guide](GUI_GUIDE_ZH_TW.md) for connection fields, defaults negotiation,
limitations and testing. Build with `packaging/build-windows-gui.ps1` after installing
the project dependencies and PyInstaller. Source launch: `py -3 -m netconf_console.gui.app`.

## Pretty XML and UTF-8 file export

Use `--pretty` (equivalent to `--output pretty`) and `--out FILE` to save an
indented XML document directly as UTF-8, without a BOM. This avoids Windows
PowerShell 5.1 rewriting `>` output as UTF-16 while leaving a UTF-8 XML declaration.

```powershell
.\exportToColleagueEXE\netconf-console2.exe `
  --host 192.168.9.9 --port 830 --transport ssh --username oranuser --password `
  --get-config --db running --pretty --out .\cobra-running-config.xml
```

On an already connected session (including SSH/TLS Call Home):

```text
get-config --db running --pretty --out .\cobra-running-config.xml
get --pretty --out .\cobra-all-data.xml
get-config --db running --output raw --out .\running-raw.xml
```

`--out` / `--output-file` works with `get`, `get-config`, `get-data`, `rpc` and
`get-schema`. XML exports contain only the reply, not progress messages. For
XML exports, existing output files are replaced after a successful query and
missing parent directories are created. Use one query per output file. A per-command format does not change
the console's `outputformat` setting. `--out -` writes to stdout.

To format an existing file offline, without connecting to a NETCONF server:

```powershell
.\exportToColleagueEXE\netconf-console2.exe `
  --format-xml .\cobra-running-config1.xml --pretty --out .\cobra-running-config1s.xml
```

The offline formatter also handles UTF-16/32 files with a stale UTF-8 declaration,
preserving the source when a different output path is used. Invalid XML is
reported before the output file is written. No Python, WSL or `xmllint` installation
is needed when using the standalone EXE.

## Automatic YANG namespaces

On connection, the client learns module-to-namespace mappings from the server
hello capabilities and, when advertised, RFC 8525 or legacy RFC 7895 YANG
Library data. Results are cached per server in
`%USERPROFILE%\.netconf-console2\namespaces.json`; the cache contains no
credentials. Use `namespaces` to inspect the effective aliases and
`namespaces refresh` to force another YANG Library query.

YANG Library supplies module names, so a discovered module name can be used as
an XPath alias directly:

```text
get --xpath "/ietf-interfaces:interfaces/ietf-interfaces:interface[ietf-interfaces:name='eth0']"
```

A successful `get-schema` additionally parses the YANG module's declared
`namespace` and `prefix` statements and caches both the module name and prefix.
For frequently used IETF/O-RAN modules, built-in aliases remain available as an
offline fallback. For example, `if` maps to
`urn:ietf:params:xml:ns:yang:ietf-interfaces` and `oran` maps to
`urn:o-ran:interfaces:1.0`, so this concise command works immediately:

```text
get --xpath "/if:interfaces/if:interface[if:name='eth0']/oran:mac-address"
```

An ordinary `<get>` reply is not treated as an authoritative prefix registry:
XML prefixes in instance data are optional and can be renamed by the server.
Use `get-schema vendor-module` to learn its declared YANG prefix, or the global
`--ns vendor=URI` option when a device exposes neither YANG Library nor schema
retrieval. Resolution precedence is explicit `--ns`, then device/cache data,
then built-in aliases.

## Profiles

Create `%USERPROFILE%\\.netconf-console2\\config.toml`:

```toml
[profiles.oru]
host = "172.29.234.130"
port = 830
transport = "ssh"
username = "oranuser"
known_hosts = "C:/Users/me/.ssh/known_hosts"
hostkey_verify = true
```

Then run:

```powershell
netconf-console2 --profile oru --password --interactive
```

CLI TOML profiles never provide a password. Use a runtime prompt or an SSH agent/key.
The Windows GUI has separate encrypted credential storage described in its guide.

## Verification

```powershell
py -3 -m unittest discover -s tests -v
py -3 setup.py --name --version
.\exportToColleagueEXE\netconf-console2.exe --bundle-self-test
py -3 .\tests\integration\frozen_transport_smoke.py --exe `
  .\exportToColleagueEXE\netconf-console2.exe
wsl.exe -d Ubuntu-22.04 -- bash -lc "printf 'help\\nquit\\n' | netopeer2-cli"
```

Detailed architecture notes, protocol references, and manual O-RU checklists
are kept outside the source tree in `../nectconf-client_backup_reference/docs`.
