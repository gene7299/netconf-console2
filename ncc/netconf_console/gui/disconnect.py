"""Concurrent, UI-independent cleanup of owned connection resources."""

from queue import Empty, Queue
from threading import Thread


class DisconnectBatch:
    """Run each independent closer once and publish results to the UI thread.

    GuiClient bounds its close-session reply wait. Daemon workers avoid tying
    application exit to a library thread join; widgets are only touched by the
    owner's Qt timer when it drains this queue.
    """

    def __init__(self, operations):
        self.total = len(operations)
        self.completed = 0
        self.errors = {}
        self._results = Queue()
        for key, close in operations.items():
            worker = Thread(target=self._close, args=(key, close),
                            name="netconf-disconnect", daemon=True)
            try:
                worker.start()
            except Exception as exc:
                self._results.put((key, exc))

    def _close(self, key, close):
        error = None
        try:
            close()
        except Exception as exc:
            error = exc
        finally:
            self._results.put((key, error))

    def poll(self):
        while True:
            try:
                key, error = self._results.get_nowait()
            except Empty:
                break
            self.completed += 1
            if error is not None:
                self.errors[key] = error
        return self.completed == self.total
