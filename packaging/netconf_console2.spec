# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata


project_root = Path(SPECPATH).parent
entry_point = project_root / "packaging" / "netconf_console2_entry.py"

hiddenimports = sorted(set(
    collect_submodules("ncclient")
    + collect_submodules("paramiko")
    + collect_submodules("prompt_toolkit")
    + collect_submodules("cryptography")
    + collect_submodules("nacl")
))

datas = []
for distribution in (
    "ncclient",
    "paramiko",
    "cryptography",
    "bcrypt",
    "PyNaCl",
    "lxml",
    "prompt_toolkit",
    "wcwidth",
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
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project_root / "packaging" / "assets" / "netconf-console2.ico"),
    version=str(project_root / "packaging" / "version_info.txt"),
)
