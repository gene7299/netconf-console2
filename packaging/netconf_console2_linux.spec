# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Ubuntu/Linux CLI bundle."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata


project_root = Path(SPECPATH).parent
entry_point = project_root / "packaging" / "netconf_console2_entry.py"

hiddenimports = sorted(set(sum((collect_submodules(module) for module in (
    "ncclient",
    "paramiko",
    "prompt_toolkit",
    "cryptography",
    "nacl",
    "pyang",
)), [])))

datas = []
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
):
    try:
        datas += copy_metadata(distribution)
    except Exception:
        pass

a = Analysis(
    [str(entry_point)],
    pathex=[str(project_root / "ncc")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc", "doctest"],
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
    name="netconf-console2",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)
