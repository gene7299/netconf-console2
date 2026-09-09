# Changelog

## 3.4.0 - 2026-09-09

- Added capability-gated explicit startup save, candidate commit/discard, datastore
  validation and running/startup comparison; preview, locks, conflict rechecks and
  readback guard whole-datastore operations without implicit retries/persistence.
- Added keyed old/new value diffs, snapshot tree/path and XML search, and YANG field
  help with types, typedef constraints, enums, units and descriptions.
- Added staged selected-subtree XML import with deletion preview, safe parsing,
  schema/config guards, existing state preservation and no automatic device writes.
- Separated GUI SSH login passwords/private-key passphrases and explicit auth modes;
  DPAPI profiles persist both, public exports omit both, legacy CLI behavior remains.
- Added bounded persistent metadata-only operation history and RFC 5277 event views;
  subscription without interleave blocks other RPCs; stopping closes the session.
- Extended source/frozen four-mode loopbacks to lifecycle RPCs and notifications,
  and Direct/Call Home key-only peers to independently encrypted SSH credentials.

## 3.3.6 - 2026-09-09

- Move DATA TREE 匯出XML immediately to the right of 更新 YANG in the same
  toolbar, preserving export behavior and compact-window visibility.

## 3.3.5 - 2026-09-09

- Rename the editor's UTF-8 export button to 匯出XML without changing encoding.
- Add DATA TREE XML export for the whole loaded snapshot, including collapsed
  branches and the defaults/state actually retrieved, excluding unsent editor changes.
  Export never fetches or writes server data and is disabled without a snapshot or while busy.

## 3.3.4 - 2026-09-09

- Enlarge the GUI connect/listen primary action with bold white text on blue,
  visible keyboard focus and grey disabled state, independent of Windows ttk theme.
- Preserve compact-window XML space and existing connection/disabled behavior.

## 3.3.3 - 2026-09-09

- Added searchable connection/SSH-account managers with load-for-edit, collision-safe
  rename and confirmed multi-select deletion, persisted atomically via DPAPI.
- Stop recreating deleted catalog entries at close; retain independent last fields.
  Update explicitly selected history records and ignore view-only differences when
  matching anonymous connections. Existing user records are not auto-deleted.
- Added isolated catalog/UI/restart/failure tests and publickey-only SSH diagnostics
  documenting password-versus-private-key-passphrase behavior without changing auth policy.

## 3.3.2 - 2026-09-09

- Added password-free JSON settings export and full current-user DPAPI backups
  under the advanced GUI tab; exports include current fields and saved profiles.
- Fixed tree indicator clicks triggering selection/reread; preserve expanded and
  collapsed branches across asynchronous refresh, including hidden selections.
- Added a persistent collapsible connection panel and opt-in automatic transport
  reconnect/Call Home re-listen with capped backoff, cancellation and draft retention.
  Reconnect never replays writes and requires fresh device data before sending.
- Set Windows AppUserModelID and native default window icons; ship the same icon
  in source/wheel packages as in the independent GUI executable.
- Added native widget, export, reconnect safety and real four-mode dropped-peer tests.

## 3.3.1 - 2026-09-09

- Added selectable GUI connection snapshots and independent SSH account history,
  named saves, automatic last-field restoration and Windows current-user DPAPI
  encryption for settings/passwords, without a plaintext fallback.
- Defaulted GUI SSH host-key and TLS hostname verification to unchecked; removed
  the unchecked SSH connection warning while retaining TLS CA-chain validation.
- Removed the GUI workspace banner and renamed the formatting button to Pretty.
- Isolated demo/self-tests from personal settings and added encrypted persistence,
  restart, account-switching, corrupt-file and frozen-DPAPI regression tests.

## 3.3.0 - 2026-09-08

- Added a separate native Windows GUI executable with Direct SSH/TLS and
  SSH/TLS Call Home settings, background operations and cancellation.
- Added running-first expandable data browsing, device YANG compilation/cache,
  explicit defaults/state options and metadata-aware XML colours.
- Added live minimal edit-config previews with ancestor list keys, write guards,
  explicit confirmation, locking/conflict checks and no implicit persistence.
- Added an offline demo, GUI widget/model tests and source/frozen four-transport
  loopbacks that compare preview XML with the actual request received by the peer.
- Verified read-only SSH interoperability with the local NETCONF server, including
  strict get-schema identityrefs, disabled-feature grouping expansion and the
  libyang/sysrepo with-defaults metadata spelling.

## 3.2.1 - 2026-09-06

- Added `--out` / `--output-file` for get, get-config, get-data and raw RPC
  replies, saving UTF-8 XML directly without PowerShell redirection.
- Added `--pretty` as an alias for `--output pretty`, including per-command
  formatting in the interactive console. Leaf text, mixed content and
  `xml:space="preserve"` remain intact.
- Added offline `--format-xml FILE`, including UTF-16/32 input whose XML
  declaration was left as UTF-8 by shell redirection.
- Added regression coverage for encoding, indentation, XML-only output,
  failed queries, and file export over all four frozen transport modes.

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
