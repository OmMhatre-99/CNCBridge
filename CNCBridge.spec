# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

font_dir = Path(SPECPATH) / 'assets' / 'fonts'
font_data = [(str(path), 'assets/fonts') for path in font_dir.iterdir() if path.is_file()]

a = Analysis(
    ['CNCBridge.pyw'],
    pathex=[],
    binaries=[],
    datas=font_data,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='CNCBridge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
