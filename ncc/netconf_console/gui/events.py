"""RFC 5277 stream discovery, bounded event parsing and replay options."""
from dataclasses import dataclass
from datetime import datetime, timezone
import re

from lxml import etree

from .model import EditError, children, local, parse_editor
from .subscription_templates import bundled_catalog

STREAM_NS = "urn:ietf:params:xml:ns:netmod:notification"
NOTIFICATION_NS = "urn:ietf:params:xml:ns:netconf:notification:1.0"
PM_NS = "urn:o-ran:performance-management:1.0"
FM_NS = "urn:o-ran:fm:1.0"

FAULT_NOTIFICATION_FIELDS = (
    (("fault-id",), "fault-id", "必填"),
    (("fault-source",), "fault-source", "必填"),
    (("fault-severity",), "fault-severity", "必填"),
    (("is-cleared",), "is-cleared", "必填"),
    (("event-time",), "event-time", "必填"),
    (("fault-text",), "fault-text", "選用"),
    (("probable-cause",), "probable-cause", "選用"),
    (("specific-problem",), "specific-problem", "選用"),
    (("proposed-repair-actions",), "proposed-repair-actions", "選用"),
    (("alarm-type",), "alarm-type", "選用"),
    (("fault-name",), "fault-name", "選用"),
    (("affected-objects", "name"), "affected-objects / name", "選用"),
    (("affected-objects", "identifier"), "affected-objects / identifier", "選用"),
    (("additional-information", "identifier"), "additional-information / identifier", "選用"),
    (("additional-information", "information"), "additional-information / information", "選用"),
)


# O-RAN.WG4.TS.MP.0-R004-v17.01, clause 9.1.7.2.  NETCONF is
# mandatory; the other three streams are recommended and therefore must be
# discovered from the O-RU rather than assumed to exist.
STANDARD_STREAMS = {
    "NETCONF": ("預設、通用事件流", "所有允許的 YANG notifications"),
    "fault-management": ("Fault Management", "alarm-notif"),
    "measurement-result-stats": ("Performance Measurement", "measurement-result-stats"),
    "supervision-notification": ("M-Plane connectivity supervision", "supervision-notification"),
}

STANDARD_STREAM_ALIASES = {
    "fault-management": ("o-ran-fm", "NETCONF"),
    "measurement-result-stats": ("NETCONF", "o-ran-performance-management"),
    "supervision-notification": ("o-ran-supervision", "NETCONF"),
}


def default_stream_filter(stream):
    """Return a notification payload filter for a named O-RAN event stream."""
    roots = {
        "fault-management": ("urn:o-ran:fm:1.0", "alarm-notif"),
        "measurement-result-stats": (PM_NS, "measurement-result-stats"),
        "supervision-notification": ("urn:o-ran:supervision:1.0", "supervision-notification"),
    }
    target = roots.get(stream)
    if target is None:
        return ""
    namespace, name = target
    root = etree.Element("{%s}%s" % (namespace, name), nsmap={None: namespace})
    return etree.tostring(root, encoding="unicode", pretty_print=True).rstrip()


def fault_notification_filter(selected_paths=()):
    """Build an alarm-notif subtree selecting only the requested fields."""
    selected = {tuple(path) for path in selected_paths}
    if not selected:
        return ""
    root = etree.Element("{%s}alarm-notif" % FM_NS, nsmap={None: FM_NS})
    for path, _name, _requirement in FAULT_NOTIFICATION_FIELDS:
        if path not in selected:
            continue
        parent = root
        for name in path:
            tag = "{%s}%s" % (FM_NS, name)
            child = parent.find(tag)
            if child is None:
                child = etree.SubElement(parent, tag)
            parent = child
    return etree.tostring(root, encoding="unicode", pretty_print=True).rstrip()


def discover_active_alarm_fields(manager):
    """Return active-alarm count and which O-RAN FM fields currently have data.

    This is only a snapshot of current alarms; it cannot prove that a field or
    future alarm condition is unsupported when it is absent from the reply.
    """
    request = etree.Element("{%s}active-alarm-list" % FM_NS, nsmap={None: FM_NS})
    etree.SubElement(request, "{%s}active-alarms" % FM_NS)
    data = manager.get(filter=("subtree", request)).data
    alarms = data.findall(".//{%s}active-alarms" % FM_NS)
    present = set()
    for path, _name, _requirement in FAULT_NOTIFICATION_FIELDS:
        for alarm in alarms:
            nodes = [alarm]
            for name in path:
                nodes = [child for node in nodes for child in children(node)
                         if isinstance(child.tag, str)
                         and etree.QName(child).namespace == FM_NS
                         and local(child.tag) == name]
                if not nodes:
                    break
            if nodes:
                present.add(path)
                break
    return len(alarms), present


def resolve_stream(requested, available):
    """Map an O-RAN logical stream to a stream the DUT actually advertises.

    The PM stream is explicitly allowed to use the default NETCONF stream
    with a notification filter when the dedicated name is not implemented.
    Other O-RAN names also fall back to the module stream or NETCONF stream.
    """
    names = set(available or ())
    if not names or requested in names:
        return requested, ""
    for candidate in STANDARD_STREAM_ALIASES.get(requested, ()):
        if candidate in names:
            note = "DUT 未宣告 %s；已改用 %s stream 搭配原 filter。" % (requested, candidate)
            return candidate, note
    return requested, ""


# MP v17.01 Annex B / accompanying YANG: 11 groups, 59 current objects.
MEASUREMENT_GROUPS = {name: tuple(values) for name, values in
                      bundled_catalog()["measurement_groups"].items()}

MEASUREMENT_CAPABILITY_LISTS = {
    "transceiver-objects": "transceiver-stats",
    "rx-window-objects": "rx-window-stats",
    "tx-stats-objects": "tx-stats",
    "epe-stats-objects": "epe-statistics",
    "symbol-rssi-stats-objects": "symbol-rssi-stats",
    "shared-cell-stats-objects": "shared-cell-stats",
    "tx-antenna-stats-objects": "tx-antenna-stats",
    "tssi-stats-objects": "tssi-stats",
    "rssi-stats-objects": "rssi-stats",
    "tx-output-power-stats-objects": "tx-output-power-stats",
    "ethernet-objects": "ethernet-stats",
}


EVENT_CATEGORIES = {
    "download-event": "軟體管理", "install-event": "軟體管理",
    "activation-event": "軟體管理",
    "file-upload-notification": "檔案管理", "file-download-event": "檔案管理",
    "security-log-upload-notification": "檔案管理",
    "troubleshooting-log-generated": "檔案管理", "trace-log-generated": "檔案管理",
    "tx-array-carriers-state-change": "載波狀態",
    "rx-array-carriers-state-change": "載波狀態",
    "data-layer-control-wakeup-notification": "M-Plane 控制",
    "mplane-trx-control-ant-mask-update": "M-Plane 控制",
    "alarm-notif": "Fault Management",
    "measurement-result-stats": "Performance Measurement",
    "supervision-notification": "Supervision",
    "synchronization-state-change": "同步狀態", "ptp-state-change": "同步狀態",
    "synce-state-change": "同步狀態", "gnss-state-change": "同步狀態",
    "overcurrent-report": "硬體／外部 I/O", "dc-enabled-status-change": "硬體／外部 I/O",
    "external-input-change": "硬體／外部 I/O",
    "certificate-lcm": "安全／憑證",
    "emergency-wake-up-complete": "電源狀態", "deep-hibernate-activated": "電源狀態",
    "antenna-calibration-required": "天線／波束成形",
    "antenna-calibration-coordinated": "天線／波束成形",
    "antenna-calibration-result": "天線／波束成形",
    "antenna-calibration-multiple-time-resource-params": "天線／波束成形",
    "beamforming-information-update": "天線／波束成形",
    "capability-group-beamforming-information-update": "天線／波束成形",
    "predefined-beam-tilt-offset-complete": "天線／波束成形",
    "power-measurement-result": "量測作業", "measurement-result": "量測作業",
    "replayComplete": "NETCONF 控制", "notificationComplete": "NETCONF 控制",
}


def event_category(name):
    return EVENT_CATEGORIES.get(name, "其他")


@dataclass(frozen=True)
class StreamInfo:
    name: str
    description: str
    replay: bool
    earliest: str


def discover_streams(manager):
    netconf = etree.Element("{%s}netconf" % STREAM_NS)
    etree.SubElement(netconf, "{%s}streams" % STREAM_NS)
    data = manager.get(filter=("subtree", netconf)).data
    result = {}
    for stream in data.findall(".//{%s}streams/{%s}stream" % (STREAM_NS, STREAM_NS)):
        name = stream.findtext("{%s}name" % STREAM_NS)
        if name:
            result[name] = StreamInfo(name, stream.findtext("{%s}description" % STREAM_NS) or "",
                stream.findtext("{%s}replaySupport" % STREAM_NS) in {"true", "1"},
                stream.findtext("{%s}replayLogCreationTime" % STREAM_NS) or "")
    return result


def discover_measurements(manager):
    """Read the DUT's advertised PM measurement objects.

    ``measurement-capabilitites`` is intentionally spelled as it is in the
    published O-RAN YANG model.  An empty set is meaningful: the group was
    returned by the DUT but contained no measurement-object entries.
    """
    root = etree.Element("{%s}performance-measurement-objects" % PM_NS)
    etree.SubElement(root, "{%s}measurement-capabilitites" % PM_NS)
    data = manager.get(filter=("subtree", root)).data
    result = {}
    for list_name, group in MEASUREMENT_CAPABILITY_LISTS.items():
        objects = {
            value.strip()
            for value in data.xpath(
                ".//*[local-name()=$list_name]/*[local-name()='measurement-object']/text()",
                list_name=list_name,
            )
            if value and value.strip()
        }
        if objects or data.xpath("boolean(.//*[local-name()=$list_name])", list_name=list_name):
            result[group] = objects
    return result


def measurement_filter(selections=None, supported=None):
    """Build an O-RAN PM subtree filter from selected group/object pairs."""
    root = etree.Element("{%s}measurement-result-stats" % PM_NS, nsmap={None: PM_NS})
    for group, objects in (selections or {}).items():
        if group not in MEASUREMENT_GROUPS:
            continue
        values = list(objects)
        if not values:
            etree.SubElement(root, "{%s}%s" % (PM_NS, group))
            continue
        for value in values:
            if value not in MEASUREMENT_GROUPS[group] and value not in (supported or {}).get(group, ()):
                raise EditError("未在規格或 DUT capability 找到 measurement object：" + value)
            node = etree.SubElement(root, "{%s}%s" % (PM_NS, group))
            etree.SubElement(node, "{%s}measurement-object" % PM_NS).text = value
    return etree.tostring(root, encoding="unicode", pretty_print=True).rstrip()


def discover_epe_capabilities(manager):
    root = etree.Element("{%s}performance-measurement-objects" % PM_NS)
    cap = etree.SubElement(root, "{%s}measurement-capabilitites" % PM_NS)
    etree.SubElement(cap, "{%s}epe-stats-objects" % PM_NS)
    data = manager.get(filter=("subtree", root)).data
    result = {}
    for node in data.findall(".//{%s}epe-stats-objects" % PM_NS):
        name = node.findtext("{%s}measurement-object" % PM_NS)
        if not name:
            continue
        units = []
        for unit in node.findall("{%s}component-class" % PM_NS):
            prefix, _, local_name = (unit.text or "").partition(":")
            uri = unit.nsmap.get(prefix if local_name else None)
            canonical = {"urn:o-ran:hardware:1.0": "or-hw", "urn:ietf:params:xml:ns:yang:iana-hardware": "ianahw"}.get(uri)
            if canonical:
                units.append(canonical + ":" + (local_name or prefix))
        result[name] = dict(report_info=[n.text for n in node.findall("{%s}report-info" % PM_NS) if n.text],
                            units=units, max_bin_count=node.findtext("{%s}max-bin-count" % PM_NS))
    return result


def timestamp(value):
    if not re.fullmatch(r"\d{4}-\d\d-\d\d[Tt]\d\d:\d\d:\d\d(?:\.\d+)?(?:[Zz]|[+-]\d\d:\d\d)", value):
        raise EditError("時間須為含時區的 RFC3339，例如 2026-09-09T08:00:00+08:00。")
    try:
        return datetime.fromisoformat(value.upper().replace("Z", "+00:00"))
    except ValueError:
        raise EditError("日期／時間不合法。") from None


def subscription_options(stream, streams, filter_xml="", start="", stop="", now=None):
    stream, start, stop = stream.strip(), start.strip(), stop.strip()
    if not stream:
        raise EditError("請輸入 stream 名稱。")
    result = {"stream_name": stream}
    if filter_xml.strip():
        if len(filter_xml) > 65536:
            raise EditError("訂閱 filter 上限 65536 字元。")
        # RFC 6241 / MP 11.3 allow multiple sibling notification branches.
        # A synthetic wrapper is only used while parsing, never sent on wire.
        text = re.sub(r"^\s*<\?xml[^?]*\?>", "", filter_xml, count=1)
        try:
            wrapper = parse_editor("<subscription-fragments>" + text + "</subscription-fragments>")
        except etree.XMLSyntaxError as exc:
            raise EditError("訂閱 filter XML 不合法（不接受 DOCTYPE / entity）：" + str(exc)) from exc
        subtrees = children(wrapper)
        if not subtrees or (wrapper.text or "").strip() or any((n.tail or "").strip() for n in wrapper):
            raise EditError("訂閱 filter 必須是 notification payload XML。")
        if any(local(n.tag) in {"rpc", "filter", "notification"} for n in subtrees):
            raise EditError("請填通知 payload 的 subtree，不要包 rpc、filter 或 notification。")
        if any(not etree.QName(n).namespace for n in subtrees):
            raise EditError("Notification payload 必須指定 namespace。")
        if len(subtrees) > 128:
            raise EditError("訂閱 filter 上限 128 個 notification 分支。")
        result["filter"] = ("subtree", subtrees[0] if len(subtrees) == 1 else subtrees)
    if stop and not start:
        raise EditError("stopTime 必須搭配 startTime。")
    if start:
        info = streams.get(stream)
        if info is None or not info.replay:
            raise EditError("請先更新 streams，並選擇支援 replay 的 stream。")
        begin = timestamp(start)
        if begin > (now or datetime.now(timezone.utc)):
            raise EditError("startTime 不可晚於目前時間。")
        if stop and timestamp(stop) < begin:
            raise EditError("stopTime 不可早於 startTime。")
        result["start_time"] = start
        if stop:
            result["stop_time"] = stop
    return result


@dataclass(frozen=True)
class EventRecord:
    time: str
    severity: str
    source: str
    event: str
    xml: str
    completed: bool = False
    category: str = "其他"
    session_id: str = ""


def event_record(xml, session_id=""):
    # The caller has already redacted the display payload; never parse entities.
    try:
        root = parse_editor(xml)
        if root.tag != "{%s}notification" % NOTIFICATION_NS:
            raise ValueError()
        payload = [n for n in children(root) if n.tag != "{%s}eventTime" % NOTIFICATION_NS]
        values = {}
        for node in root.iter():
            if isinstance(node.tag, str) and not children(node) and node.text:
                values.setdefault(local(node.tag), node.text.strip())
        severity = next((values[k] for k in ("fault-severity", "perceived-severity", "severity") if k in values), "unknown")
        source = next((values[k] for k in ("fault-source", "source", "resource", "object-instance") if k in values), "")
        complete = any(n.tag == "{%s}notificationComplete" % NOTIFICATION_NS for n in payload)
        event = local(payload[0].tag) if payload else "notification"
        return EventRecord(values.get("eventTime", ""), severity.lower(), source,
                           event, xml, complete, event_category(event), session_id)
    except Exception:
        return EventRecord("", "unknown", "", "無法解析／已截斷", xml,
                           session_id=session_id)
