"""Windowed PyInstaller entry point; credentials use per-user Windows DPAPI."""

import json
import os
import sys
from pathlib import Path


def main():
    # Windowed Windows processes have no stdout/stderr. Some dependencies use
    # these streams for nonfatal diagnostics, so provide harmless sinks.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    try:
        from netconf_console.gui.app import main as gui_main
        return gui_main()
    except Exception as exc:
        if "--self-test" in sys.argv:
            index = sys.argv.index("--self-test")
            if index + 1 < len(sys.argv):
                Path(sys.argv[index + 1]).write_text(json.dumps({"passed": False, "error": str(exc)}), encoding="utf-8")
        else:
            from tkinter import messagebox
            messagebox.showerror("NETCONF GUI startup error", str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
