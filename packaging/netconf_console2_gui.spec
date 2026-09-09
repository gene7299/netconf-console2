# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, copy_metadata

project_root = Path(SPECPATH).parent
hiddenimports = sorted(set(sum((collect_submodules(module) for module in (
    "ncclient", "paramiko", "cryptography", "nacl", "pyang",
)), [])))
datas = [(str(project_root / "packaging" / "assets" / "netconf-console2.ico"), ".")]
for distribution in ("ncclient", "paramiko", "cryptography", "bcrypt", "PyNaCl", "lxml", "pyang"):
    datas += copy_metadata(distribution)
a = Analysis(
    [str(project_root / "packaging" / "netconf_console2_gui_entry.py")],
    pathex=[str(project_root / "ncc")], binaries=[], datas=datas,
    hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["unittest", "pydoc", "doctest"], noarchive=False, optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [], name="netconf-console2-gui",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False,
    icon=str(project_root / "packaging" / "assets" / "netconf-console2.ico"),
    version=str(project_root / "packaging" / "version_info_gui.txt"),
)
