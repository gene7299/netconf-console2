"""Explicit datastore operations with preview, locks and optimistic conflict checks."""
from copy import deepcopy
from dataclasses import dataclass
import uuid

from lxml import etree
from ncclient.xml_ import to_xml

from .client import ReadOptions
from .model import EditError, NC, semantic
from .workspace import xml_diff

OPERATIONS = {
    "save": ("保存 running → startup", "running", "startup", ":startup:"),
    "compare": ("比較 running / startup", "running", "startup", ":startup:"),
    "commit": ("提交 candidate → running", "candidate", "running", ":candidate:"),
    "discard": ("捨棄 candidate（恢復為 running）", "running", "candidate", ":candidate:"),
    "validate": ("驗證 datastore", None, None, ":validate:"),
}


def supported(client, operation):
    return operation in OPERATIONS and any(OPERATIONS[operation][3] in c for c in client.capabilities)


@dataclass
class PreparedOperation:
    operation: str
    source: str
    target: str
    baseline: dict
    manager: object
    rpc: object
    wire_xml: str
    diff: str


def prepare(client, operation, source="running"):
    if not supported(client, operation):
        raise EditError("Server 未宣告此操作需要的 capability。")
    _, origin, target, _ = OPERATIONS[operation]
    if operation == "validate":
        if source not in {"running", "candidate", "startup"}:
            raise EditError("不支援的 datastore。")
        origin = target = source
    mgr = client.manager
    baseline = {name: client.read(ReadOptions(name)).data for name in dict.fromkeys((origin, target))}
    rpc = etree.Element("{%s}rpc" % NC, nsmap={"nc": NC}, attrib={"message-id": "gui-" + uuid.uuid4().hex})
    name = {"save": "copy-config", "compare": "copy-config", "commit": "commit",
            "discard": "discard-changes", "validate": "validate"}[operation]
    op = etree.SubElement(rpc, "{%s}%s" % (NC, name))
    if operation in {"save", "compare"}:
        etree.SubElement(etree.SubElement(op, "{%s}target" % NC), "{%s}%s" % (NC, target))
        etree.SubElement(etree.SubElement(op, "{%s}source" % NC), "{%s}%s" % (NC, origin))
    if operation == "validate":
        etree.SubElement(etree.SubElement(op, "{%s}source" % NC), "{%s}%s" % (NC, origin))
    return PreparedOperation(operation, origin, target, baseline, mgr, rpc, to_xml(rpc),
                             xml_diff(baseline[target], baseline[origin], target, origin))


def execute(client, prepared):
    """Never automatically retry. Failure may mean an unknown device outcome."""
    if prepared.operation == "compare":
        raise EditError("比較是唯讀操作。")
    if client.manager is not prepared.manager or not supported(client, prepared.operation):
        raise EditError("連線已改變；請重新預覽。")
    if to_xml(prepared.rpc) != prepared.wire_xml:
        raise EditError("RPC 已改變；請重新預覽。")
    manager = prepared.manager
    locked = []
    warnings = []
    try:
        # Stable ordering across all operations avoids client-side lock inversion.
        for name in ("running", "candidate", "startup"):
            if name in prepared.baseline:
                manager.lock(target=name)
                locked.append(name)
        for name, baseline in prepared.baseline.items():
            current = client.read(ReadOptions(name)).data
            if semantic(current) != semantic(baseline):
                raise EditError(name + " 已在預覽後改變；未送出操作，請重新比較。")
        reply = manager.xrpc(deepcopy(prepared.rpc)).xml
        if prepared.operation != "validate":
            try:
                actual = client.read(ReadOptions(prepared.target)).data
                if semantic(actual) != semantic(prepared.baseline[prepared.source]):
                    warnings.append("RPC 成功，但讀回與來源有差異（可能為 server 正規化或存取權限）；請重新比較。")
            except Exception:
                warnings.append("RPC 成功，但讀回失敗；結果待確認，不可直接重送。")
    finally:
        for name in reversed(locked):
            try:
                manager.unlock(target=name)
            except Exception:
                warnings.append("Unlock 失敗，已關閉自己的 session；請重新連線確認結果。")
                client.disconnect()
                break
    return reply, warnings
