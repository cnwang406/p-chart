# -*- mode: python ; coding: utf-8 -*-
# pyright: reportUndefinedVariable=false

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


app_name = 'p-chart'

datas = [
    ('mainwindow.ui', '.'),
    ('CascadiaNextTC.wght.ttf', '.'),
    ('w2l.png', '.'),
    ('AppIcon.appiconset/icon-ios-marketing-1024x1024-1x.png', 'AppIcon.appiconset'),
]
datas += collect_data_files('plotly')

hiddenimports = [
    'PySide6.QtWebEngineWidgets',
    'PySide6.QtWebEngineCore',
    'PySide6.QtWebEngineQuick',
]
hiddenimports += collect_submodules('plotly')


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
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
    [],
    exclude_binaries=True,
    name=app_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app.ico',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=app_name,
)
