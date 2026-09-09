"""Read-only draft testing and token-bound confirmed commits."""
from copy import deepcopy
from dataclasses import dataclass
import time
import uuid

from lxml import etree
from ncclient.xml_ import to_xml

from .client import ReadOptions, config_semantic, locate
from .model import EditError, NC


def has_cap(client, name):
    uri = "urn:ietf:params:netconf:capability:" + name
    return any(str(c).split("?", 1)[0] == uri for c in client.capabilities)


def draft_rpc(plan):
    if plan is None or plan.rpc is None or to_xml(plan.rpc) != plan.wire_xml:
        raise EditError("請先建立有效的修改預覽。")
    rpc = deepcopy(plan.rpc)
    rpc.set("message-id", "gui-test-" + uuid.uuid4().hex)
    op = rpc.find("{%s}edit-config" % NC)
    if len(rpc) != 1 or op is None or op.find("{%s}test-option" % NC) is not None:
        raise EditError("只接受標準 edit-config 草稿。")
    option = etree.Element("{%s}test-option" % NC)
    option.text = "test-only"
    op.insert(list(op).index(op.find("{%s}config" % NC)), option)
    return rpc


def test_draft(client, selection, plan, options, rpc):
    if not has_cap(client, "validate:1.1"):
        raise EditError("Server 必須支援 :validate:1.1；不會降級成實際 edit-config。")
    if getattr(client, "pending_commit", None) is not None:
        raise EditError("限時提交尚未結束。")
    expected = draft_rpc(plan)
    expected.set("message-id", rpc.get("message-id"))
    if to_xml(rpc) != to_xml(expected):
        raise EditError("test-only RPC 與草稿不一致。")
    target = rpc.find("{%s}edit-config/{%s}target" % (NC, NC))
    if options.source not in {"running", "candidate"} or target is None or len(target) != 1 or target[0].tag != "{%s}%s" % (NC, options.source):
        raise EditError("草稿 target 不符。")
    manager = client.manager
    locked = False
    try:
        manager.lock(target=options.source)
        locked = True
        current = client.read(ReadOptions(options.source, options.defaults, False), selection.path[0])
        latest = locate(current.data, selection, client.schema)
        if config_semantic(latest, selection.path, client.schema) != config_semantic(selection.node, selection.path, client.schema):
            raise EditError("Server 設定已改變，請重新讀取。未送出測試。")
        return manager.xrpc(deepcopy(rpc)).xml
    finally:
        if locked:
            try:
                manager.unlock(target=options.source)
            except Exception:
                client.disconnect()
                raise EditError("test-only 後解鎖失敗；已關閉自己的 session，請重新連線。") from None


@dataclass
class PendingCommit:
    manager: object
    token: str
    deadline: float
    release_after: float
    locks: tuple
    state: str = "unknown"


def begin_pending(client, prepared, locked):
    token = prepared.rpc.findtext("{%s}commit/{%s}persist" % (NC, NC))
    seconds = int(prepared.rpc.findtext("{%s}commit/{%s}confirm-timeout" % (NC, NC)))
    if not token or not 30 <= seconds <= 600:
        raise EditError("無效的限時提交參數。")
    now = time.monotonic()
    rpc_timeout = float(client.context.settings.rpc_timeout)
    client.pending_commit = PendingCommit(client.manager, token, now + seconds,
                                          now + seconds + rpc_timeout + 2, tuple(locked))


def pending_rpc(pending, confirm):
    rpc = etree.Element("{%s}rpc" % NC, nsmap={"nc": NC}, attrib={"message-id": "gui-confirm-" + uuid.uuid4().hex})
    op = etree.SubElement(rpc, "{%s}%s" % (NC, "commit" if confirm else "cancel-commit"))
    # Even a late packet cannot become an ordinary commit after expiration.
    etree.SubElement(op, "{%s}persist-id" % NC).text = pending.token
    return rpc


def finish_pending(client, pending, confirm, rpc):
    if client.pending_commit is not pending or client.manager is not pending.manager:
        raise EditError("限時提交 session 已改變；不會在另一台設備送出確認。")
    if confirm and (pending.state != "pending" or time.monotonic() >= pending.deadline):
        raise EditError("提交結果不明或倒數已到；不能確認保留。請取消或等待讀回確認。")
    expected = pending_rpc(pending, confirm)
    expected.set("message-id", rpc.get("message-id"))
    if to_xml(expected) != to_xml(rpc):
        raise EditError("確認 RPC 與預覽不符。")
    try:
        reply = client.manager.xrpc(deepcopy(rpc)).xml
    except Exception:
        pending.state = "unknown"
        raise
    warnings = []
    for name in reversed(pending.locks):
        try:
            pending.manager.unlock(target=name)
        except Exception:
            warnings.append("操作成功但解鎖失敗，已中斷自己的連線。")
            client.disconnect()
            break
    client.pending_commit = None
    return reply, warnings
