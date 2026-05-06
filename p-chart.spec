# -*- mode: python ; coding: utf-8 -*-
# pyright: reportUndefinedVariable=false

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, collect_all

app_name = 'p-chart'

np_datas, np_binaries, np_hiddenimports = collect_all('numpy')
pd_datas, pd_binaries, pd_hiddenimports = collect_all('pandas')
datas = [
   ('mainwindow.ui', '.'),
   ('mainwindow-win.ui','.'),
   ('mainwindow-mac.ui', '.'),
   ('plotly.min.js', '.'),
   ('CascadiaNextTC.wght.ttf', '.'),
   ('w2l.png',  '.'),
   ('AppIcon.appiconset/icon-ios-marketing-1024x1024-1x.png', 'AppIcon.appiconset'),
]
datas += collect_data_files('plotly')
datas += collect_data_files('kaleido')
datas += np_datas + pd_datas
hiddenimports = [
   'PySide6.QtWebEngineWidgets',
   'PySide6.QtWebEngineCore',
   'PySide6.QtWebEngineQuick',
]
hiddenimports += collect_submodules('plotly')
hiddenimports += collect_submodules('openpyxl')
#hiddenimports += collect_submodules('kaleido')
hiddenimports += np_hiddenimports + pd_hiddenimports
hiddenimports += ['kaleido', 'kaleido.scopes', 'kaleido._version']
a = Analysis(
   ['app.py'],
   pathex=[],
   binaries=np_binaries + pd_binaries,
   datas=datas, hiddenimports=hiddenimports,
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
