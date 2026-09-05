# Changelog

## 3.2.0 - 2026-09-04

- Added a Windows x64 one-file executable build, frozen-runtime transport
  self-test, reproducible build script and colleague delivery documentation.
- Added packaged-executable loopback integration coverage for Direct SSH,
  Direct TLS/mTLS, SSH Call Home and TLS Call Home/mTLS NETCONF hello exchange.
- Split colleague delivery into a minimum installable source package and a
  source-free standalone EXE package.
- Embedded the supplied NETCONF console artwork as a multi-resolution Windows
  executable icon with transparent outer corners.
- Discover module namespace URIs from server hello capabilities and RFC 8525
  or legacy RFC 7895 YANG Library data after connecting.
- Cache discovered aliases per server and add `namespaces` plus
  `namespaces refresh` commands for inspection and forced refresh.
- Learn and cache the declared YANG prefix and namespace from successful
  RFC 6022 `get-schema` responses.
- Resolve XPath aliases in the order `--ns` explicit mapping, device/cache
  mapping, then built-in IETF/O-RAN fallback, while emitting only aliases used
  by the request.

## 3.1.2 - 2026-09-04

- Made `prompt-toolkit` a default dependency so Windows always has native
  Up/Down command recall, editing and completion after a normal install.
- Persist interactive history in `%USERPROFILE%\\.netconf-console2\\history`
  and load it on every subsequent client run.
- Strip inline password values and redact secret XML before commands are
  written to persistent history; replaying a sanitized password option opens
  the secure password prompt.

## 3.1.1 - 2026-09-04

- Automatically resolve common `if`, `ianaift`, `hw`, and O-RAN XPath
  prefixes, while retaining explicit `--ns prefix=URI` overrides.
- Reject unknown XPath prefixes locally with a command-line correction instead
  of sending a request that the NETCONF server cannot resolve.

## 3.1.0 - 2026-09-03

- Added RFC-oriented feature matrix and layered architecture documentation.
- Added direct NETCONF over TLS with mTLS, CA, hostname, version and optional
  CRL controls.
- Added SSH and TLS Call Home listeners with Windows-safe cancellation,
  metadata, socket reuse and peer display.
- Added complete RFC 6241 CLI parameters for edit-config, confirmed commit,
  cancel-commit, copy/delete-config and close-session.
- Added raw/full-envelope RPC input, RFC 5277 replay parameters, notification
  watch mode, capability and session status commands, profiles and redacted
  SEND/RECV tracing.
- Added RFC 8526 NMDA get-data/edit-data XML adapters, including datastore
  identities, subtree/XPath filters, origin filters, max-depth and with-origin.
- Preserved legacy command names while avoiding embedded default credentials;
  passwords are supplied explicitly or requested at runtime.
- Added root packaging metadata, Windows-compatible interactive fallback and
  unit/manual test layout.
