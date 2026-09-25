"""Keep existing supervision subscriptions alive during long worker jobs."""

from threading import Event, Thread

from . import raw_rpc
from .model import EditError


class SupervisionKeeper:
    def __init__(self, records, report):
        self.records = tuple(records)
        self.report = report
        self.stop = Event()
        self.failure = ""
        self.thread = None

    def start(self):
        if self.records:
            self.thread = Thread(target=self._run, daemon=True, name="workflow-supervision")
            self.thread.start()

    def _run(self):
        while not self.stop.wait(0.1):
            for record in self.records:
                if self.stop.is_set():
                    return
                if not record.watchdog_pending or record.status != "已訂閱":
                    continue
                record.watchdog_pending = False
                target, manager = record.client, record.manager
                if target is None or not target.connected or manager is None:
                    continue
                old_timeout = manager.timeout
                try:
                    if not any(":interleave:" in cap for cap in target.capabilities):
                        raise EditError("DUT 未宣告 :interleave，無法在 supervision session 回覆 watchdog。")
                    manager.timeout = min(5, old_timeout)
                    plan = raw_rpc.prepare(raw_rpc.TEMPLATES["supervision-watchdog-reset"])
                    raw_rpc.execute(target, manager, plan)
                    self.report(record, "已收到 RPC 回應")
                except Exception as exc:
                    self.report(record, "失敗：" + type(exc).__name__)
                    self.failure = "Supervision watchdog reset 失敗，停止後續步驟；請檢查連線／通知。"
                finally:
                    manager.timeout = old_timeout

    def close(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(6)
