"""RFC 5277 stream discovery, bounded event parsing and replay options."""
from dataclasses import dataclass
from datetime import datetime, timezone
import re

from lxml import etree

from .model import EditError, children, local, parse_editor

STREAM_NS = "urn:ietf:params:xml:ns:netmod:notification"
NOTIFICATION_NS = "urn:ietf:params:xml:ns:netconf:notification:1.0"


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
        subtree = parse_editor(filter_xml)
        if local(subtree.tag) in {"rpc", "filter", "notification"}:
            raise EditError("請填通知 payload 的 subtree，不要包 rpc、filter 或 notification。")
        result["filter"] = ("subtree", subtree)
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


def event_record(xml):
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
        return EventRecord(values.get("eventTime", ""), severity.lower(), source,
                           local(payload[0].tag) if payload else "notification", xml, complete)
    except Exception:
        return EventRecord("", "unknown", "", "無法解析／已截斷", xml)
