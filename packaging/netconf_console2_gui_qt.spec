# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, copy_metadata

project_root = Path(SPECPATH).parent
gnutls_runtime = project_root / "packaging" / "runtime" / "windows-gnutls"
gnutls_binaries = [(str(path), ".") for path in sorted(gnutls_runtime.glob("*.dll"))]
if not gnutls_binaries:
    raise SystemExit(
        "Windows GnuTLS runtime DLLs are missing from packaging/runtime/windows-gnutls"
    )
hiddenimports = sorted(set(sum((collect_submodules(module) for module in (
    "ncclient", "paramiko", "cryptography", "nacl", "pyang",
)), [])))
hiddenimports += ["PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets"]
datas = [(str(project_root / "packaging" / "assets" / "netconf-console2.ico"), ".")]
for distribution in ("ncclient", "paramiko", "cryptography", "bcrypt", "PyNaCl", "lxml", "pyang"):
    datas += copy_metadata(distribution)
a = Analysis(
    [str(project_root / "packaging" / "netconf_console2_gui_qt_entry.py")],
    pathex=[str(project_root / "ncc")], binaries=gnutls_binaries, datas=datas,
    hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["unittest", "pydoc", "doctest"], noarchive=False, optimize=0,
)
# PyInstaller can discover an unrelated ICU 78 DLL from the developer
# runtime (for example a Poppler installation).  Qt6Core expects the Windows
# ICU ABI with unversioned exports; bundling that unrelated DLL causes
# ``DLL load failed ... WinError 127`` on startup.  Windows 10/11 provides
# the compatible system ICU DLL, so keep the Qt bundle from shadowing it.
a.binaries = [entry for entry in a.binaries
              if Path(entry[0]).name.lower() not in {"icuuc.dll", "icudt78.dll"}]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [], name="netconf-console2-gui",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False,
    icon=str(project_root / "packaging" / "assets" / "netconf-console2.ico"),
    version=str(project_root / "packaging" / "version_info_gui_qt.txt"),
)
