# PyInstaller spec file for LISFLOOD-FP Prep Tool
# Usage: pyinstaller build_app.spec

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[str(Path('.').resolve())],
    binaries=[],
    datas=[],
    hiddenimports=[
        # geospatial
        'geopandas', 'geopandas.io', 'geopandas._compat',
        'rasterio', 'rasterio.crs', 'rasterio.warp',
        'rasterio.merge', 'rasterio.mask', 'rasterio.features',
        'rasterio.transform', 'rasterio._shim',
        'fiona', 'fiona.ogrext', 'fiona._shim',
        'pyproj', 'pyproj.transformer', 'pyproj._crs',
        'shapely', 'shapely.ops', 'shapely.geometry',
        # data
        'numpy', 'pandas', 'xarray',
        'zarr', 's3fs', 'fsspec',
        'numcodecs',
        'requests',
        'pynhd',
        # GUI
        'PyQt6', 'PyQt6.QtWidgets', 'PyQt6.QtCore', 'PyQt6.QtGui',
        # stdlib
        'json', 'pathlib', 'ssl', 'math', 'concurrent.futures',
        'urllib.request', 'datetime', 'shutil',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'scipy', 'IPython', 'jupyter'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='LISFLOOD_FP_PrepTool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,       # no terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,           # add .ico path here if you have an icon
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='LISFLOOD_FP_PrepTool',
)
