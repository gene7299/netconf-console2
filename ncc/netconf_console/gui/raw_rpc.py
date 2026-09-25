"""Prepare custom NETCONF RPCs without treating them as edit-config drafts."""

from copy import deepcopy
from dataclasses import dataclass
import uuid

from lxml import etree
from ncclient.xml_ import to_xml

from .events import NOTIFICATION_NS
from .model import EditError, NC, children
from ..xmloutput import serialize_xml

MAX_XML_BYTES = 2 * 1024 * 1024
SUBSCRIBE = "{%s}create-subscription" % NOTIFICATION_NS
READ_OPERATIONS = {"{%s}%s" % (NC, name) for name in ("get", "get-config")}
READ_OPERATIONS.add("{urn:ietf:params:xml:ns:yang:ietf-netconf-monitoring}get-schema")

TEMPLATES = {
    "get": '<get xmlns="%s"/>' % NC,
    "get-config (running)": '<get-config xmlns="%s">\n  <source><running/></source>\n</get-config>' % NC,
    "get active-alarm-list": '<get xmlns="%s">\n  <filter type="subtree">\n'
        '    <active-alarm-list xmlns="urn:o-ran:fm:1.0"/>\n  </filter>\n</get>' % NC,
    "create-subscription (NETCONF)": '<create-subscription xmlns="%s">\n'
        '  <stream>NETCONF</stream>\n</create-subscription>' % NOTIFICATION_NS,
    "create-subscription (alarm-notif)": '<create-subscription xmlns="%s">\n'
        '  <stream>NETCONF</stream>\n  <filter type="subtree">\n'
        '    <alarm-notif xmlns="urn:o-ran:fm:1.0"/>\n  </filter>\n</create-subscription>' % NOTIFICATION_NS,
    "create-subscription (measurement-result-stats)": '<create-subscription xmlns="%s">\n'
        '  <stream>NETCONF</stream>\n  <filter type="subtree">\n'
        '    <measurement-result-stats xmlns="urn:o-ran:performance-management:1.0">\n'
        '      <epe-statistics><measurement-object>POWER</measurement-object></epe-statistics>\n'
        '    </measurement-result-stats>\n  </filter>\n</create-subscription>' % NOTIFICATION_NS,
    "create-subscription (supervision)": '<create-subscription xmlns="%s">\n'
        '  <stream>NETCONF</stream>\n  <filter type="subtree">\n'
        '    <supervision-notification xmlns="urn:o-ran:supervision:1.0"/>\n'
        '  </filter>\n</create-subscription>' % NOTIFICATION_NS,
    "supervision-watchdog-reset": '<supervision-watchdog-reset xmlns="urn:o-ran:supervision:1.0">\n'
        '  <supervision-notification-interval>60</supervision-notification-interval>\n'
        '  <guard-timer-overhead>10</guard-timer-overhead>\n</supervision-watchdog-reset>',
    "edit-config EPE POWER (60s)": '<edit-config xmlns="urn:ietf:params:xml:ns:netconf:base:1.0"\n'
        '    xmlns:pm="urn:o-ran:performance-management:1.0"\n'
        '    xmlns:or-hw="urn:o-ran:hardware:1.0">\n'
        '  <target><running/></target>\n  <default-operation>none</default-operation>\n'
        '  <config>\n    <pm:performance-measurement-objects>\n'
        '      <pm:epe-measurement-interval nc:operation="replace"\n'
        '          xmlns:nc="urn:ietf:params:xml:ns:netconf:base:1.0">60</pm:epe-measurement-interval>\n'
        '      <pm:epe-measurement-objects nc:operation="replace"\n'
        '          xmlns:nc="urn:ietf:params:xml:ns:netconf:base:1.0">\n'
        '        <pm:measurement-object>POWER</pm:measurement-object>\n'
        '        <pm:active>true</pm:active>\n'
        '        <pm:object-unit>or-hw:O-RAN-RADIO</pm:object-unit>\n'
        '        <pm:report-info>AVERAGE</pm:report-info>\n'
        '      </pm:epe-measurement-objects>\n'
        '    </pm:performance-measurement-objects>\n  </config>\n</edit-config>',
}


def parse_xml(text):
    payload = text.encode("utf-8")
    if len(payload) > MAX_XML_BYTES:
        raise EditError("RPC XML 上限 2 MiB。")
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True,
                             encoding="utf-8", remove_blank_text=False)
    root = etree.fromstring(payload, parser)
    if root.getroottree().docinfo.doctype or any(isinstance(n, etree._Entity) for n in root.iter()):
        raise EditError("RPC XML 不接受 DOCTYPE 或 entity。")
    return root


def pretty_xml(text):
    """Pretty-print a complete XML reply while preserving non-XML errors."""
    try:
        root = parse_xml(str(text))
        return serialize_xml(root, mode="pretty").decode("utf-8").split("\n", 1)[1].rstrip()
    except Exception:
        return str(text)


@dataclass(frozen=True)
class PreparedRpc:
    rpc: object
    wire_xml: str
    operation: str

    @property
    def subscription(self):
        return self.operation == SUBSCRIBE

    @property
    def may_change_data(self):
        return self.operation not in READ_OPERATIONS and not self.subscription

    @property
    def requires_readback(self):
        # This O-RAN action resets the supervision timer; it never edits the
        # DATA TREE, so its routine reply must not invalidate an XML draft.
        return self.may_change_data and self.operation != "{urn:o-ran:supervision:1.0}supervision-watchdog-reset"

    @property
    def stream(self):
        return children(self.rpc)[0].findtext("{%s}stream" % NOTIFICATION_NS) or "NETCONF"


def prepare(text):
    root = parse_xml(text)
    name = etree.QName(root).localname
    if name == "rpc":
        if root.tag != "{%s}rpc" % NC:
            raise EditError("rpc 必須使用 NETCONF base:1.0 namespace。")
        operations = children(root)
        if (len(operations) != 1 or (root.text or "").strip()
                or any((node.tail or "").strip() for node in root)):
            raise EditError("每個 rpc 必須只包含一個 operation。")
        operation = operations[0]
        if root.get("message-id") is not None and not root.get("message-id").strip():
            raise EditError("message-id 不可為空白。")
    else:
        operation = root
        root = etree.Element("{%s}rpc" % NC, nsmap={"nc": NC})
        root.append(operation)
    if etree.QName(operation).localname in {"rpc", "rpc-reply", "hello", "notification"}:
        raise EditError("請輸入 RPC operation；Notification 由設備發送，請使用通知頁接收。")
    if not etree.QName(operation).namespace:
        raise EditError("RPC operation 必須指定 namespace。")
    if root.get("message-id") is None:
        root.set("message-id", "gui-rpc-" + uuid.uuid4().hex)
    return PreparedRpc(root, to_xml(root), operation.tag)


def subscription_rpc(options):
    """Use RFC 5277's notification namespace for subscription parameters."""
    op = etree.Element(SUBSCRIBE, nsmap={None: NOTIFICATION_NS})
    etree.SubElement(op, "{%s}stream" % NOTIFICATION_NS).text = options["stream_name"]
    if options.get("filter") is not None:
        kind, subtree = options["filter"]
        if kind != "subtree":
            raise EditError("訂閱表單只接受 subtree filter；XPath 可使用自訂 RPC。")
        node = etree.SubElement(op, "{%s}filter" % NOTIFICATION_NS, type="subtree")
        for branch in subtree if isinstance(subtree, (list, tuple)) else (subtree,):
            node.append(deepcopy(branch))
    for key, tag in (("start_time", "startTime"), ("stop_time", "stopTime")):
        if options.get(key):
            etree.SubElement(op, "{%s}%s" % (NOTIFICATION_NS, tag)).text = options[key]
    return prepare(to_xml(op))


def execute(client, manager, prepared):
    if not client.connected or client.manager is not manager:
        raise EditError("NETCONF session 已改變，請重新送出。")
    if getattr(client, "pending_commit", None) is not None:
        raise EditError("限時提交尚未結束，不能送出自訂 RPC。")
    if to_xml(prepared.rpc) != prepared.wire_xml:
        raise EditError("RPC 與預覽不同，請重新產生預覽。")
    return manager.xrpc(deepcopy(prepared.rpc)).xml
