# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Ubuntu/Linux PySide6 GUI bundle."""

from ctypes.util import find_library
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata


project_root = Path(SPECPATH).parent
entry_point = project_root / "packaging" / "netconf_console2_gui_linux_entry.py"

hiddenimports = sorted(set(sum((collect_submodules(module) for module in (
    "ncclient",
    "paramiko",
    "prompt_toolkit",
    "cryptography",
    "nacl",
    "pyang",
)), [])))
hiddenimports += [
    "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets",
    "netconf_console.gnutls",
]

datas = [(str(project_root / "packaging" / "assets" / "netconf-console2.ico"), ".")]
datas += [(str(project_root / "ncc/netconf_console/gui/assets/notification_catalog.json"),
           "netconf_console/gui/assets")]
for distribution in (
    "ncclient",
    "paramiko",
    "cryptography",
    "bcrypt",
    "PyNaCl",
    "lxml",
    "prompt-toolkit",
    "wcwidth",
    "pyang",
    "PySide6",
    "shiboken6",
):
    try:
        datas += copy_metadata(distribution)
    except Exception:
        pass

gnutls_name = find_library("gnutls")
gnutls_candidates = []
if gnutls_name:
    for directory in (
        "/lib/x86_64-linux-gnu", "/usr/lib/x86_64-linux-gnu",
        "/lib/aarch64-linux-gnu", "/usr/lib/aarch64-linux-gnu",
        "/lib64", "/usr/lib64", "/lib", "/usr/lib",
    ):
        gnutls_candidates.append(Path(directory) / gnutls_name)
# Keep the SONAME filename (libgnutls.so.30) in the bundle.  GnuTLS is
# loaded through ctypes at runtime, so the name must be discoverable from
# PyInstaller's extraction directory on a target without system headers.
gnutls_path = next((path for path in gnutls_candidates if path.is_file()), None)
if gnutls_path is None:
    raise SystemExit(
        "GnuTLS runtime library not found; it is required for RFC 8071 TLS Call Home"
    )

a = Analysis(
    [str(entry_point)],
    pathex=[str(project_root / "ncc")],
    binaries=[(str(gnutls_path), ".")],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["unittest", "pydoc", "doctest"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="netconf-console2-gui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
