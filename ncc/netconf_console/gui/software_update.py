"""Cancellable software workflow, run by the window's existing worker thread.

All UI updates cross a queue. The dedicated subscription is consumed here, so
the general Notification timer must not consume this session concurrently.
Mutating operations are sent once; only inventory reads / reconnects retry.
"""

from copy import deepcopy
from datetime import datetime
import time

from lxml import etree
from ncclient.operations.rpc import RPCError

from . import raw_rpc, software
from .client import GuiClient
from .model import EditError
from .workflow_supervision import SupervisionKeeper


def display_xml(text):
    try:
        root = raw_rpc.parse_xml(text)
        for node in root.iter():
            if isinstance(node.tag, str) and etree.QName(node).localname in {"password", "appl-password"} and not len(node):
                node.text = "***REDACTED***"
        return raw_rpc.pretty_xml(etree.tostring(root, encoding="unicode"))
    except Exception:
        from ..trace import redact_secrets
        return redact_secrets(text)


class SoftwareUpdate:
    def __init__(self, client, settings, options, stages, record, updates, cancel, watchdog_records=()):
        self.client = client
        self.settings = deepcopy(settings)
        self.options = deepcopy(options)
        self.stages = tuple(stages)
        self.record = record
        self.updates = updates
        self.cancel = cancel
        self.subscriber = None
        self.inventory = None
        self.reconnected = False
        self.completed = 0
        self.total = sum(len(options["uris"]) if stage == "download" else 1 for stage in stages)
        self.supervision = SupervisionKeeper(watchdog_records,
                                             lambda record, result: self.emit("watchdog", (record, result)))
        self.resetting = False

    def emit(self, kind, payload):
        self.updates.put((kind, payload))

    def safe_error(self, error):
        message = str(error)
        for secret in (self.options.get("password"), self.options.get("appl_password")):
            if secret:
                message = message.replace(secret, "***REDACTED***")
        return display_xml(message)

    def log(self, message):
        self.emit("log", datetime.now().strftime("%H:%M:%S") + "  " + message)

    def check_cancel(self):
        if self.cancel.is_set():
            raise InterruptedError("已停止後續步驟；已送出的 DUT 作業可能仍在執行，請重新讀取 inventory 確認。")
        if self.supervision.failure and not self.resetting:
            raise EditError(self.supervision.failure)

    def pause(self, seconds):
        if self.cancel.wait(seconds):
            self.check_cancel()

    def rpc(self, prepared, operation, client=None, display=True):
        self.check_cancel()
        target = client or self.client
        if display:
            self.emit("rpc", (operation, display_xml(prepared.wire_xml)))
        self.emit("action", (client is not None, operation, "已送出"))
        try:
            reply = raw_rpc.execute(target, target.manager, prepared)
        except Exception as exc:
            error_xml = getattr(exc, "xml", None)
            if isinstance(error_xml, etree._Element):
                error_xml = etree.tostring(error_xml, encoding="unicode")
            self.emit("reply", self.safe_error(error_xml if error_xml is not None else exc))
            self.emit("action", (client is not None, operation, "失敗／待確認：" + type(exc).__name__))
            raise
        if display:
            self.emit("reply", display_xml(reply))
        self.emit("action", (client is not None, operation, "收到 RPC 回應"))
        return reply

    def read_inventory(self, quiet=False):
        self.check_cancel()
        if quiet:
            reply = raw_rpc.execute(self.client, self.client.manager, software.inventory_rpc())
        else:
            reply = self.rpc(software.inventory_rpc(), "get software-inventory", display=False)
        self.inventory = software.parse_inventory(reply)
        self.emit("inventory", self.inventory)
        self.emit("inventory_xml", display_xml(reply))
        return self.inventory

    def restart_state(self):
        self.check_cancel()
        try:
            reply = raw_rpc.execute(self.client, self.client.manager, software.restart_state_rpc())
            root = raw_rpc.parse_xml(reply)
            return tuple(root.findtext(".//{%s}%s" % (software.OPS, key), "") for key in ("restart-datetime", "restart-cause"))
        except Exception:
            return "", ""

    def subscribe(self):
        self.check_cancel()
        self.log("建立 Software Update 專用 NETCONF 通知 session…")
        self.subscriber = GuiClient()
        self.record.client = self.subscriber
        # A raw wire dump is opt-in debugging of another connection; do not
        # duplicate it onto a new session or persist file-server credentials.
        settings = self.settings.copy(raw_file=None)
        self.subscriber.connect(settings, lambda message: self.log(message))
        self.record.manager = self.subscriber.manager
        self.record.server_id = str(self.subscriber.manager.session_id or "")
        if not any(":notification:" in cap for cap in self.subscriber.capabilities):
            raise EditError("DUT 未提供 RFC 5277 notification capability。")
        reply = self.rpc(software.subscription_rpc(), "create-subscription (software events)", self.subscriber)
        software.reply_status(reply, "subscribe")
        self.record.status = "已訂閱"
        self.emit("session", None)
        self.log("已訂閱 NETCONF stream：download-event、install-event、activation-event。")

    def take_notification(self, block=False):
        if not self.subscriber.connected:
            raise EditError("Software Update 通知 session 已中斷，停止流程；已送出的 RPC 不會自動重送。")
        item = self.subscriber.manager.take_notification(block=block, timeout=0.2 if block else None)
        if item is None:
            return None
        text = item.notification_xml
        self.emit("notification", display_xml(text))
        return text

    def drain_notifications(self):
        # Discard completion events queued before this particular operation.
        # They remain visible in Notification but cannot advance the workflow.
        for _ in range(1000):
            self.check_cancel()
            if self.take_notification() is None:
                return
        raise EditError("通知過量，無法建立新操作的通知邊界；已停止送出。")

    def wait_event(self, operation, identifier, timeout):
        deadline = time.monotonic() + timeout
        next_inventory = time.monotonic() + 5
        self.log("等待 %s（最長 %d 秒）…" % (software.EVENTS[operation], timeout))
        while time.monotonic() < deadline:
            self.check_cancel()
            text = self.take_notification(block=True)
            if text is None:
                if time.monotonic() >= next_inventory and deadline - time.monotonic() > 4:
                    manager = self.client.manager
                    old_timeout = manager.timeout
                    try:
                        manager.timeout = min(2, old_timeout)
                        self.read_inventory(quiet=True)
                    except InterruptedError:
                        raise
                    except Exception as exc:
                        self.log("Inventory 暫時無法更新（%s）；繼續等待完成通知。" % type(exc).__name__)
                    finally:
                        manager.timeout = old_timeout
                        next_inventory = time.monotonic() + 5
                continue
            event = software.matching_event(text, operation, identifier)
            if event is None:
                continue
            status, error, code = event
            self.emit("event", display_xml(text))
            if status != "COMPLETED":
                raise EditError("%s: %s %s（return-code=%s）" % (software.EVENTS[operation], status or "缺少 status", error, code or "—"))
            self.log("%s：COMPLETED%s" % (software.EVENTS[operation], " / return-code=" + code if code else ""))
            return
        raise EditError("等待 %s 逾時；結果待確認，停止後續步驟，不重送 RPC。" % software.EVENTS[operation])

    def confirm_slot(self, operation):
        # Some devices publish the datastore just after their completion event.
        deadline = time.monotonic() + 15
        while True:
            inventory = self.read_inventory()
            slot = inventory.slot(self.options["slot"])
            valid = slot["status"] == "VALID"
            active = [s for s in inventory.slots if s["active"] in {"true", "1"}]
            if valid and (operation == "install" or (
                    slot["active"] in {"true", "1"} and len(active) == 1)):
                expected = self.options.get("expected_version", "")
                if expected and slot["build-version"] != expected:
                    raise EditError("安裝後 build-version=%s，與預期 %s 不符；停止後續步驟。" % (slot["build-version"] or "未提供", expected))
                return
            if time.monotonic() >= deadline:
                raise EditError("收到完成通知，但 inventory 尚未確認 %s 成功；停止後續步驟。" % operation)
            self.pause(1)

    def reset_and_reconnect(self):
        inventory = self.read_inventory()
        active = [slot for slot in inventory.slots if slot["active"] in {"true", "1"}]
        expected_slot = self.options["slot"] if "activate" in self.stages else (active[0]["name"] if len(active) == 1 else "")
        expected_version = inventory.slot(expected_slot)["build-version"] if expected_slot else ""
        old_running = {slot["name"] for slot in inventory.slots if slot["running"] in {"true", "1"}}
        old_restart, _old_cause = self.restart_state()
        self.resetting = True
        self.log("送出一次 reset；等待設備重新啟動及 NETCONF 重連。")
        try:
            reply = self.rpc(software.operation_rpc("reset", self.options), "reset")
        except (RPCError, EditError):
            raise
        except Exception as exc:
            self.log("Reset 回應未能確認（%s）；只重連讀取狀態，不重送 reset。" % type(exc).__name__)
        else:
            software.reply_status(reply, "reset")
            self.log("Reset RPC 已接受；<ok/> 尚不代表設備已重啟完成。")
        self.supervision.stop.set()
        # Observe a server-side disconnect before closing our transport. For a
        # manual reset of the already-running slot, a fresh session alone would
        # otherwise be mistaken for evidence that the equipment restarted.
        down_deadline = time.monotonic() + 15
        while self.client.connected and time.monotonic() < down_deadline:
            self.pause(0.2)
        observed_down = not self.client.connected
        if self.subscriber is not None:
            self.subscriber.disconnect()
            self.record.manager = None
            self.record.status = "已結束"
            self.emit("session", None)
        self.client.disconnect()
        self.emit("main_disconnected", None)
        deadline = time.monotonic() + self.options["reconnect_timeout"]
        self.pause(5)
        while time.monotonic() < deadline:
            self.check_cancel()
            try:
                if not self.client.connected:
                    remaining = max(1, deadline - time.monotonic())
                    settings = self.settings.copy(timeout=min(10, remaining),
                                                  rpc_timeout=min(self.settings.rpc_timeout, remaining))
                    self.client.connect(settings, lambda message: self.log(message))
                    self.reconnected = True
                    self.emit("main_connected", None)
                self.client.manager.timeout = max(0.1, min(5, deadline - time.monotonic()))
                inventory = self.read_inventory()
                running = [slot for slot in inventory.slots if slot["running"] in {"true", "1"}]
                active = [slot for slot in inventory.slots if slot["active"] in {"true", "1"}]
                if len(running) == 1 and len(active) == 1 and running[0]["name"] == active[0]["name"]:
                    slot = running[0]
                    restarted_at, cause = self.restart_state()
                    restart_evidence = (observed_down or (old_running and slot["name"] not in old_running)
                                        or (old_restart and restarted_at and old_restart != restarted_at))
                    if (slot["status"] == "VALID" and (not expected_slot or slot["name"] == expected_slot)
                            and (not expected_version or slot["build-version"] == expected_version) and restart_evidence):
                        self.log("重連確認：%s / %s / VALID / active=true / running=true。" %
                                 (slot["name"], slot["build-version"] or "版本未提供"))
                        if restarted_at or cause:
                            self.log("restart-datetime=%s；restart-cause=%s" % (restarted_at or "—", cause or "—"))
                        return
                self.log("已連線，等待 inventory 的 active／running 切換至目標…")
            except InterruptedError:
                raise
            except Exception as exc:
                self.log("尚未完成重連／讀取（%s）；稍後重試唯讀確認。" % type(exc).__name__)
                if not self.client.connected:
                    observed_down = True
                self.client.disconnect()
                self.emit("main_disconnected", None)
            self.pause(3)
        raise EditError("Reset 後重連／running slot 確認逾時；結果待確認，不重送 reset。")

    def run(self, _progress):
        try:
            self.supervision.start()
            self.read_inventory()
            if "install" in self.stages:
                software.validate_slot(self.inventory, self.options["slot"], "install")
            elif "activate" in self.stages:
                software.validate_slot(self.inventory, self.options["slot"], "activate")
            if any(stage in software.EVENTS for stage in self.stages):
                self.subscribe()
            for stage in self.stages:
                self.check_cancel()
                if stage == "reset":
                    self.reset_and_reconnect()
                    self.completed += 1
                    self.emit("progress", (self.completed, self.total))
                    continue
                if stage in {"install", "activate"}:
                    software.validate_slot(self.read_inventory(), self.options["slot"], stage)
                for uri in self.options["uris"] if stage == "download" else (None,):
                    self.check_cancel()
                    self.drain_notifications()
                    self.log("送出 software-%s%s" % (stage, "：" + uri if uri else "：" + self.options["slot"]))
                    reply = self.rpc(software.operation_rpc(stage, self.options, uri), "software-" + stage)
                    timeout = software.reply_status(reply, stage, self.options["install_timeout"])
                    self.wait_event(stage, uri if stage == "download" else self.options["slot"], timeout)
                    if stage in {"install", "activate"}:
                        self.confirm_slot(stage)
                    self.completed += 1
                    self.emit("progress", (self.completed, self.total))
            if "reset" not in self.stages:
                self.read_inventory()
            return {"ok": True, "message": "流程完成。" + (
                "已切換 active；running 需執行 Reset 後才切換。" if self.stages[-1] == "activate" else "")}
        except Exception as exc:
            return {"ok": False, "message": self.safe_error(exc), "cancelled": isinstance(exc, InterruptedError)}
        finally:
            self.supervision.close()
            if self.reconnected and self.client.connected:
                self.client.manager.timeout = self.settings.rpc_timeout
            if self.subscriber is not None:
                try:
                    self.subscriber.disconnect()
                finally:
                    self.record.manager = None
                    self.record.status = "已中斷"
                    self.emit("session", None)
