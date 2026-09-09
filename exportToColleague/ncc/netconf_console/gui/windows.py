"""Windows shell identity and shared native-window icon resources."""
import ctypes
import os
import sys
from pathlib import Path

APP_ID = "netconf-console2.XMLWorkspace.GUI"


def set_app_id():
    if os.name == "nt":
        function = ctypes.WinDLL("shell32").SetCurrentProcessExplicitAppUserModelID
        function.argtypes = [ctypes.c_wchar_p]
        function.restype = ctypes.c_long
        if function(APP_ID) != 0:
            raise OSError("Cannot set NETCONF GUI taskbar identity")


def icon_path():
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "netconf-console2.ico"
    return Path(__file__).parent / "assets" / "netconf-console2.ico"


def has_window_icons(root):
    """Read native big/small icon handles for packaged-runtime diagnostics."""
    if os.name != "nt":
        return icon_path().is_file()
    from ctypes import wintypes
    send = ctypes.WinDLL("user32").SendMessageW
    send.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
    send.restype = ctypes.c_ssize_t
    hwnd = int(root.wm_frame(), 0)
    return all(send(hwnd, 0x7F, size, 0) != 0 for size in (0, 1))
