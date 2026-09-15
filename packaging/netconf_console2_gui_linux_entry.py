"""Windowed PyInstaller entry point for the Ubuntu PySide6 GUI."""

import json
import os
import sys
from pathlib import Path


def main():
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    try:
        from netconf_console.gui.qt_app import main as gui_main
        return gui_main()
    except Exception as exc:
        if "--self-test" in sys.argv:
            index = sys.argv.index("--self-test")
            if index + 1 < len(sys.argv):
                Path(sys.argv[index + 1]).write_text(
                    json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False),
                    encoding="utf-8",
                )
        else:
            try:
                from PySide6.QtWidgets import QApplication, QMessageBox
                app = QApplication.instance() or QApplication(sys.argv)
                QMessageBox.critical(None, "NETCONF Qt GUI startup error", str(exc))
                app.processEvents()
            except Exception:
                pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
