# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for backend.exe (CI + local onefile builds).
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

_hiddenimports = []
for _package in ('uvicorn', 'fastapi', 'starlette', 'anyio'):
    _hiddenimports += collect_submodules(_package)

a = Analysis(
    ['backend.py'],
    pathex=[],
    binaries=[],
    datas=[('static', 'static'), ('templates', 'templates')],
    hiddenimports=_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='backend',
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
)
