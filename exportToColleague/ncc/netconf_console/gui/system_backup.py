"""Safe, timestamped remote Sysrepo backup and restore scripts.

The scripts are sent over the already authenticated system-SSH channel.  They
are deliberately generated from a small allow-list of paths, executables,
datastores and service names; callers cannot provide an arbitrary shell
command.
"""
from dataclasses import dataclass
import re
import shlex

from .model import EditError


DATASTORES = ("running", "candidate", "startup")
DEFAULT_BASE = "/data/backup-yang-baseline"
DEFAULT_INIT_MODULE = "o-ran-sync"
DEFAULT_SYSREPOCTL = "sysrepoctl"

# Stop order follows the operator's reference command.  Start in dependency
# order, but only services that were active before the stop are restarted.
STOP_SERVICES = (
    "meta-oran-mplaned.service",
    "rumanager.service",
    "netopeer2-server.service",
    "mplane-dependency.service",
    "meta-oran-dbus.service",
)
START_SERVICES = (
    "meta-oran-dbus.service",
    "mplane-dependency.service",
    "netopeer2-server.service",
    "meta-oran-mplaned.service",
    "rumanager.service",
)

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*\Z")
_PATH_SEGMENT = re.compile(r"[A-Za-z0-9_.-]+\Z")
_STAMP = re.compile(r"20\d{6}-\d{6}(?:-\d+)?\Z")


def validate_base(value):
    """Validate a remote absolute directory without allowing shell syntax."""
    value = str(value or "").strip().rstrip("/")
    if not value or len(value) > 240 or not value.startswith("/"):
        raise EditError("遠端備份 BASE 必須是短的絕對路徑，例如 /data/backup-yang-baseline。")
    parts = value.split("/")[1:]
    if not parts or any(segment in {".", ".."} or not _PATH_SEGMENT.fullmatch(segment)
                       for segment in parts):
        raise EditError("遠端備份 BASE 只能包含英數字、底線、句點、連字號與路徑分隔符。")
    return value


def validate_program(value, expected):
    value = str(value or "").strip()
    pattern = r"(?:/[A-Za-z0-9_.-]+)*/?" + re.escape(expected)
    if not re.fullmatch(pattern, value):
        raise EditError("只接受 %s 或以 / 開頭的 %s 完整路徑；不接受 shell 指令。" % (expected, expected))
    return value


def validate_module(value):
    value = str(value or "").strip()
    if not _TOKEN.fullmatch(value):
        raise EditError("初始 YANG module 名稱格式不合法。")
    return value


def validate_datastores(values):
    if isinstance(values, str):
        values = (values,)
    try:
        selected = set(values)
    except TypeError:
        raise EditError("備份 datastore 選項不合法。") from None
    if not selected or not selected.issubset(set(DATASTORES)) or "running" not in selected:
        raise EditError("running XML 是還原所需的必要備份；請至少勾選 running。")
    return tuple(datastore for datastore in DATASTORES if datastore in selected)


@dataclass(frozen=True)
class BackupRequest:
    base: str
    sysrepocfg: str
    sysrepoctl: str
    init_module: str
    datastores: tuple


def prepare(base=DEFAULT_BASE, sysrepocfg="sysrepocfg", sysrepoctl=DEFAULT_SYSREPOCTL,
            init_module=DEFAULT_INIT_MODULE, datastores=("running",)):
    return BackupRequest(validate_base(base), validate_program(sysrepocfg, "sysrepocfg"),
                         validate_program(sysrepoctl, "sysrepoctl"), validate_module(init_module),
                         validate_datastores(datastores))


def shell_command():
    """Return the fixed remote command used for all generated scripts."""
    return "sh -s"


def _q(value):
    return shlex.quote(value)


def initialization_script(request):
    request = prepare(request.base, request.sysrepocfg, request.sysrepoctl,
                      request.init_module, request.datastores)
    ctl, module = _q(request.sysrepoctl), _q(request.init_module)
    return "\n".join((
        "set -eu",
        "if %s -l | grep -F -q -- %s; then" % (ctl, module),
        "  printf 'NCC_YANG_INITIALIZED=%s\\n' %s" % (request.init_module, module),
        "else",
        "  printf 'NCC_YANG_INITIALIZATION_FAILED=%s\\n' %s >&2" % (request.init_module, module),
        "  exit 20",
        "fi",
        ""))


def backup_script(request):
    """Create one private timestamped directory and export selected datastores."""
    request = prepare(request.base, request.sysrepocfg, request.sysrepoctl,
                      request.init_module, request.datastores)
    base, ctl, cfg, module = (_q(request.base), _q(request.sysrepoctl),
                              _q(request.sysrepocfg), _q(request.init_module))
    lines = [
        "set -eu",
        "umask 077",
        "BASE=%s" % base,
        "mkdir -p \"$BASE\"",
        "chmod 700 \"$BASE\"",
        "if ! %s -l | grep -F -q -- %s; then" % (ctl, module),
        "  printf 'NCC_YANG_INITIALIZATION_FAILED=%s\\n' %s >&2" % (request.init_module, module),
        "  exit 20",
        "fi",
        "STAMP=$(date -u +%Y%m%d-%H%M%S)",
        "DEST=\"$BASE/$STAMP\"",
        "if [ -e \"$DEST\" ]; then DEST=\"$BASE/${STAMP}-$$\"; fi",
        "mkdir -p \"$DEST\"",
        "chmod 700 \"$DEST\"",
        "completed=0",
        "cleanup() {",
        "  rc=$?",
        "  if [ \"$completed\" -eq 0 ]; then rm -rf \"$DEST\"; fi",
        "  exit \"$rc\"",
        "}",
        "trap cleanup EXIT",
    ]
    for datastore in request.datastores:
        q_datastore = _q(datastore)
        lines.append("%s --export=\"$DEST/%s.xml\" --datastore=%s --format=xml" %
                     (cfg, datastore, q_datastore))
    lines += [
        "%s -l > \"$DEST/modules.txt\"" % ctl,
        "(cd \"$DEST\" && sha256sum ./*.xml > SHA256SUMS)",
        "chmod 600 \"$DEST\"/*",
        "printf 'NCC_BACKUP_DIR=%s\\n' \"$DEST\"",
        "printf 'NCC_BACKUP_DATASTORES=%%s\\n' %s" % _q(",".join(request.datastores)),
        "completed=1",
        "exit 0",
        "",
    ]
    return "\n".join(lines)


def latest_script(request):
    """Find and checksum the newest generated backup without changing Sysrepo."""
    request = prepare(request.base, request.sysrepocfg, request.sysrepoctl,
                      request.init_module, request.datastores)
    base = _q(request.base)
    return "\n".join((
        "set -eu",
        "BASE=%s" % base,
        "if [ ! -d \"$BASE\" ]; then printf 'NCC_BACKUP_NOT_FOUND=%s\\n' \"$BASE\" >&2; exit 21; fi",
        "LATEST=$(find \"$BASE\" -mindepth 1 -maxdepth 1 -type d -name '20??????-??????*' -print | sort | tail -n 1)",
        "if [ -z \"$LATEST\" ]; then printf 'NCC_BACKUP_NOT_FOUND=%s\\n' \"$BASE\" >&2; exit 21; fi",
        "if [ ! -f \"$LATEST/running.xml\" ] || [ ! -f \"$LATEST/SHA256SUMS\" ]; then",
        "  printf 'NCC_BACKUP_INCOMPLETE=%s\\n' \"$LATEST\" >&2; exit 22",
        "fi",
        "if ! (cd \"$LATEST\" && sha256sum -c SHA256SUMS); then",
        "  printf 'NCC_CHECKSUM_FAILED=%s\\n' \"$LATEST\" >&2; exit 24",
        "fi",
        "printf 'NCC_LATEST_BACKUP_DIR=%s\\n' \"$LATEST\"",
        "",
    ))


def _validate_backup_dir(path, base):
    base = validate_base(base)
    path = str(path or "").strip().rstrip("/")
    if not path.startswith(base + "/"):
        raise EditError("遠端最新備份不在設定的 BASE 目錄內。")
    leaf = path[len(base) + 1:]
    if "/" in leaf or not _STAMP.fullmatch(leaf):
        # A collision suffix is numeric and still retains the timestamp.
        if "/" in leaf or not re.fullmatch(r"20\d{6}-\d{6}-\d+", leaf):
            raise EditError("遠端最新備份目錄名稱格式不合法。")
    return path


def marker_value(raw, marker):
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
    match = re.findall(r"^%s=([^\r\n]+)$" % re.escape(marker), text, re.MULTILINE)
    if not match:
        raise EditError("遠端沒有回報必要的操作結果。")
    return match[-1].strip()


def parse_marker(raw, marker, base):
    return _validate_backup_dir(marker_value(raw, marker), base)


def restore_script(request, expected_dir):
    """Verify the previewed newest backup, restore running, and recover services."""
    request = prepare(request.base, request.sysrepocfg, request.sysrepoctl,
                      request.init_module, request.datastores)
    expected = _validate_backup_dir(expected_dir, request.base)
    base = _q(request.base)
    expected = _q(expected)
    lines = [
        "set -u",
        "umask 077",
        "BASE=%s" % base,
        "EXPECTED=%s" % expected,
        "LATEST=$(find \"$BASE\" -mindepth 1 -maxdepth 1 -type d -name '20??????-??????*' -print | sort | tail -n 1)",
        "if [ -z \"$LATEST\" ] || [ \"$LATEST\" != \"$EXPECTED\" ]; then",
        "  printf 'NCC_LATEST_CHANGED=%s\\n' \"$LATEST\" >&2; exit 23",
        "fi",
        "if [ ! -f \"$LATEST/running.xml\" ] || [ ! -f \"$LATEST/SHA256SUMS\" ]; then",
        "  printf 'NCC_BACKUP_INCOMPLETE=%s\\n' \"$LATEST\" >&2; exit 22",
        "fi",
        "if ! (cd \"$LATEST\" && sha256sum -c SHA256SUMS); then",
        "  printf 'NCC_CHECKSUM_FAILED=%s\\n' \"$LATEST\" >&2; exit 24",
        "fi",
        "active=''",
        "failures=''",
        "stop_one() {",
        "  service=\"$1\"",
        "  if ! systemctl is-active --quiet \"$service\"; then return 0; fi",
        "  active=\"$service $active\"",
        "  if systemctl stop \"$service\"; then return 0; fi",
        "  printf 'NCC_SERVICE_STOP_FAILED=%s\\n' \"$service\" >&2; return 1",
        "}",
        "start_one() {",
        "  service=\"$1\"",
        "  if ! systemctl start \"$service\"; then failures=\"$service $failures\"; printf 'NCC_SERVICE_START_FAILED=%s\\n' \"$service\" >&2; fi",
        "}",
        "cleanup() {",
        "  rc=$?",
        "  for service in meta-oran-dbus.service mplane-dependency.service netopeer2-server.service meta-oran-mplaned.service rumanager.service; do",
        "    case \" $active \" in *\" $service \"*) start_one \"$service\";; esac",
        "  done",
        "  if [ -n \"$failures\" ] && [ \"$rc\" -eq 0 ]; then rc=21; fi",
        "  exit \"$rc\"",
        "}",
        "trap cleanup EXIT",
    ]
    for service in STOP_SERVICES:
        lines.append("stop_one %s || exit 30" % _q(service))
    lines += [
        "if ! %s --copy-from=\"$LATEST/running.xml\" --datastore=running --format=xml; then" % _q(request.sysrepocfg),
        "  printf 'NCC_RESTORE_FAILED=%s\\n' \"$LATEST\" >&2; exit 31",
        "fi",
        "printf 'NCC_RESTORE_APPLIED=%s\\n' \"$LATEST\"",
        "printf 'NCC_RESTORE_APPLIED=%s\\n' \"$LATEST\" >&2",
        "exit 0",
        "",
    ]
    return "\n".join(lines)
