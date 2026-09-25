"""O-RAN MP v17.01 clauses 8 / 9.5, software-management YANG operations.

No network or Qt dependencies here. Inventory values are device observations;
an absent active/running leaf is not displayed as a fabricated false value.
"""

from dataclasses import dataclass, field
import base64
from urllib.parse import unquote, urlsplit

from lxml import etree

from . import raw_rpc
from .model import EditError, NC

SW = "urn:o-ran:software-management:1.0"
FM = "urn:o-ran:file-management:1.0"
OPS = "urn:o-ran:operations:1.0"
ALGORITHMS = ("rsa1024", "rsa2048", "rsa3072", "rsa4096", "rsa7680", "rsa15360",
              "secp192r1", "secp224r1", "secp256r1", "secp384r1", "secp521r1")
EVENTS = {"download": "download-event", "install": "install-event", "activate": "activation-event"}
WORKFLOWS = (
    ("Download → Install → Activate → Reset／重連確認", ("download", "install", "activate", "reset")),
    ("Download → Install → Activate（不重啟）", ("download", "install", "activate")),
    ("Download → Install（不切換 active）", ("download", "install")),
    ("Install → Activate → Reset（已下載檔案）", ("install", "activate", "reset")),
    ("Activate → Reset（已安裝 VALID slot）", ("activate", "reset")),
)


def leaf(parent, name, value, namespace=SW):
    node = etree.SubElement(parent, "{%s}%s" % (namespace, name))
    node.text = str(value) if value is not None else None
    return node


def value(parent, name, default=""):
    return parent.findtext("{%s}%s" % (SW, name), default)


def prepare(node):
    return raw_rpc.prepare(etree.tostring(node, encoding="unicode"))


def inventory_rpc():
    node = etree.Element("{%s}get" % NC, nsmap={None: NC})
    query = etree.SubElement(node, "{%s}filter" % NC, type="subtree")
    etree.SubElement(query, "{%s}software-inventory" % SW, nsmap={None: SW})
    return prepare(node)


def subscription_rpc():
    branches = [etree.Element("{%s}%s" % (SW, name), nsmap={None: SW}) for name in EVENTS.values()]
    return raw_rpc.subscription_rpc({"stream_name": "NETCONF", "filter": ("subtree", branches)})


def restart_state_rpc():
    node = etree.Element("{%s}get" % NC, nsmap={None: NC})
    query = etree.SubElement(node, "{%s}filter" % NC, type="subtree")
    info = etree.SubElement(query, "{%s}operational-info" % OPS, nsmap={None: OPS})
    state = leaf(info, "operational-state", None, OPS)
    leaf(state, "restart-datetime", None, OPS)
    leaf(state, "restart-cause", None, OPS)
    return prepare(node)


@dataclass
class Inventory:
    slots: list[dict] = field(default_factory=list)
    individual_files: bool = False
    integrity_at_download: bool = False

    def slot(self, name):
        found = [slot for slot in self.slots if slot["name"] == name]
        if len(found) != 1:
            raise EditError("目標 slot 不存在或名稱不唯一；請重新讀取 inventory。")
        return found[0]


def parse_inventory(xml):
    root = raw_rpc.parse_xml(xml)
    nodes = list(root.iter("{%s}software-inventory" % SW))
    if len(nodes) != 1:
        raise EditError("DUT 未回傳 software-inventory；請確認模型支援與讀取權限。")
    inventory = nodes[0]
    slots = []
    for node in inventory.findall("{%s}software-slot" % SW):
        slot = {key: value(node, key) for key in (
            "name", "status", "active", "running", "product-code", "vendor-code",
            "build-id", "build-name", "build-version")}
        slot["access"] = value(node, "access", "READ_WRITE")
        slot["files"] = [{key: value(item, key) for key in ("name", "version", "local-path", "integrity")}
                         for item in node.findall("{%s}files" % SW)]
        slots.append(slot)
    return Inventory(slots, inventory.find("{%s}build-content-download" % SW) is not None,
                     inventory.find("{%s}integrity-check-at-download-enabled" % SW) is not None)


def validate_slot(inventory, name, operation):
    slot = inventory.slot(name)
    if slot["access"] != "READ_WRITE":
        raise EditError("READ_ONLY 是 factory slot；本更新流程只操作 READ_WRITE slots。")
    if operation == "install":
        if slot["active"] in {"true", "1"} or slot["running"] in {"true", "1"}:
            raise EditError("Install 目標不可是 active 或 running slot。")
        if slot["status"] not in {"VALID", "INVALID", "EMPTY"}:
            raise EditError("Slot status 未知，無法確認可安裝。")
        if slot["status"] == "VALID" and (slot["active"] not in {"false", "0"}
                                            or slot["running"] not in {"false", "0"}):
            raise EditError("VALID slot 必須明確回報 active=false、running=false 才能安裝。")
    elif slot["status"] != "VALID":
        raise EditError("只能 Activate 狀態為 VALID 的 slot。")
    if operation == "activate" and any(entry["integrity"] == "NOK" for entry in slot["files"]):
        raise EditError("目標 slot 含 integrity=NOK 的檔案，不可 Activate。")
    return slot


def lines(text):
    return [line.strip() for line in text.splitlines() if line.strip()]


def validate_uri(uri, transport=None):
    parts = urlsplit(uri)
    if (parts.scheme not in {"sftp", "ftpes"} or not parts.hostname or not parts.username
            or not parts.path or parts.path.endswith("/") or parts.fragment or parts.query
            or any(c.isspace() for c in uri)):
        raise EditError("下載 URI 請用 sftp://user@host/path/file 或 ftpes://user@host/path/file；特殊字元須 URL encode。")
    if parts.password is not None:
        raise EditError("請把密碼填在認證欄位，勿放入 URI。")
    if parts.port is not None and not 1 <= parts.port <= 65535:
        raise EditError("檔案伺服器 port 必須介於 1–65535。")
    if transport is not None and parts.scheme == "ftpes" and transport != "tls":
        raise EditError("MP §8.5 規定 FTPES 下載須透過 NETCONF/TLS 下達。")
    return parts


def operation_rpc(operation, options, uri=None):
    if operation == "reset":
        return prepare(etree.Element("{%s}reset" % OPS, nsmap={None: OPS}))
    node = etree.Element("{%s}software-%s" % (SW, operation), nsmap={None: SW, "fm": FM})
    if operation == "download":
        validate_uri(uri)
        leaf(node, "remote-file-path", uri)
        auth = options.get("auth", "password")
        if auth == "password":
            password = options.get("password", "")
            if not password:
                raise EditError("請填寫檔案伺服器密碼，或選擇 certificate／設備既有認證。")
            leaf(leaf(node, "password", None), "password", password)
            keys = lines(options.get("keys", ""))
            if keys:
                server = leaf(node, "server", None)
                seen = set()
                for line in keys:
                    fields = line.split()
                    if len(fields) != 2 or fields[0] not in ALGORITHMS or fields[0] in seen:
                        raise EditError("Server key 每行填 algorithm base64-key；algorithm 不可重複。可用：" + ", ".join(ALGORITHMS))
                    try:
                        base64.b64decode(fields[1], validate=True)
                    except ValueError as exc:
                        raise EditError("Server public-key 必須是合法 base64。") from exc
                    seen.add(fields[0])
                    key = leaf(server, "keys", None)
                    leaf(key, "algorithm", "fm:" + fields[0])
                    leaf(key, "public-key", fields[1])
        elif auth == "certificate":
            leaf(node, "certificate", None)
        if options.get("appl_password"):
            leaf(leaf(node, "application-layer-credential", None), "appl-password", options["appl_password"])
    else:
        slot = options.get("slot", "").strip()
        if not slot:
            raise EditError("請先讀取 inventory 並選擇目標 slot。")
        leaf(node, "slot-name", slot)
        if operation == "install":
            names = options.get("files", [])
            if not names or len(names) != len(set(names)):
                raise EditError("Install file-names 至少一筆且不可重複；請使用 manifest 中的 fileName。")
            for name in names:
                leaf(node, "file-names", name)
    return prepare(node)


def reply_status(xml, operation, install_timeout=600):
    root = raw_rpc.parse_xml(xml)
    errors = list(root.iter("{%s}rpc-error" % NC))
    if errors:
        raise EditError("DUT 拒絕 RPC：" + " ".join(" ".join(e.itertext()) for e in errors))
    if operation in {"reset", "subscribe"}:
        if root.find("{%s}ok" % NC) is None:
            raise EditError("RPC 未回傳 <ok/>，無法確認已接受。")
        return 0
    status = value(root, "status")
    if status != "STARTED":
        raise EditError("%s 回應 %s：%s" % (operation, status or "缺少 status", value(root, "error-message")))
    timeout_name = "sw-install-timeout" if operation == "install" else "notification-timeout"
    timeout = int(value(root, timeout_name, str(install_timeout if operation == "install" else 30)))
    if timeout < 1:
        raise EditError("DUT 回傳無效的 notification timeout。")
    return timeout


def matching_event(xml, operation, identifier):
    root = raw_rpc.parse_xml(xml)
    event = root.find("{%s}%s" % (SW, EVENTS[operation]))
    if event is None:
        return None
    if operation == "download":
        actual = unquote(value(event, "file-name"))
        path = unquote(urlsplit(identifier).path)
        # DUTs report either the URI, a full path, or a basename. Do not match
        # an arbitrary foreign URI merely because its basename is the same.
        if actual not in {unquote(identifier), path, path.rsplit("/", 1)[-1]}:
            return None
    elif value(event, "slot-name") != identifier:
        return None
    return value(event, "status"), value(event, "error-message"), value(event, "return-code")


def read_manifest(text):
    root = raw_rpc.parse_xml(text)
    builds = []
    for build in root.xpath("//*[local-name()='manifest']/*[local-name()='builds']/*[local-name()='build']"):
        files = [node.get("fileName", "") for node in build
                 if isinstance(node.tag, str) and etree.QName(node).localname == "file"]
        if files and all(files) and len(files) == len(set(files)):
            builds.append({"name": build.get("bldName", ""), "version": build.get("bldVersion", ""),
                           "id": build.get("id", ""), "files": files})
    if not builds:
        raise EditError("找不到 MP §8.3 manifest/builds/build/file（fileName）內容。")
    return builds
