# ─────────────────────────────────────────────────────────────────────────────
# FIMsim — PyInstaller spec file
# Usage (from the lisflood_prep_app/ directory):
#   pip install pyinstaller
#   pyinstaller build_app.spec
#
# Output:  dist/FIMsim/   (folder mode — fast start-up, easiest to distribute)
#          Zip that folder on Mac/Linux, or run Inno Setup on Windows.
# ─────────────────────────────────────────────────────────────────────────────

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = Path(SPECPATH)          # absolute path to lisflood_prep_app/
IS_WIN  = sys.platform == "win32"
IS_MAC  = sys.platform == "darwin"

# ── Collect packages that ship native binaries or data ────────────────────────
datas     = []
binaries  = []
hidden    = []

for pkg in [
    "rasterio",     # GDAL binaries + proj
    "pyproj",       # PROJ data (proj.db, etc.)
    "pyogrio",      # GDAL-based vector I/O (geopandas backend)
    "shapely",      # GEOS binaries
    "geopandas",    # data files
    "matplotlib",   # fonts, style sheets, backends
    "h5py",         # HDF5 shared lib
    "gmsh",         # gmsh shared lib + API
    "scipy",        # LAPACK/BLAS etc.
    "numpy",        # OpenBLAS
    "xarray",
    "zarr",
    "s3fs",
    "fsspec",
    "numcodecs",
    "pynhd",
    "pygeoogc",
    "requests",
    "certifi",      # SSL certs (important for HTTPS downloads)
    "charset_normalizer",
    "pandas",
    "openpyxl",
    "PyQt6",
    "matplotlib.backends",
]:
    d, b, h = collect_all(pkg)
    datas    += d
    binaries += b
    hidden   += h

# ── App data files (bundled GeoJSON) ──────────────────────────────────────────
datas += [
    (str(ROOT / "data" / "us_states.geojson"), "data"),
    (str(ROOT / "data" / "us_huc6.geojson"),   "data"),
    (str(ROOT / "data" / "us_huc8.geojson"),   "data"),
]

# ── Hidden imports that collect_all misses ────────────────────────────────────
hidden += [
    # PyQt6 extras
    "PyQt6.QtWidgets", "PyQt6.QtCore", "PyQt6.QtGui",
    "PyQt6.QtNetwork", "PyQt6.QtSvg", "PyQt6.QtPrintSupport",
    # matplotlib Qt backend
    "matplotlib.backends.backend_qtagg",
    "matplotlib.backends.backend_qt",
    # scipy sub-modules used by the app
    "scipy.ndimage", "scipy.ndimage._ni_support",
    "scipy.spatial", "scipy.spatial.distance",
    "scipy.interpolate",
    # network / async
    "aiohttp", "aiofiles",
    # stdlib
    "json", "pathlib", "ssl", "math", "concurrent.futures",
    "urllib.request", "urllib.parse", "datetime", "shutil",
    "email", "email.mime", "email.mime.multipart",
    "xml", "xml.etree", "xml.etree.ElementTree",
    # zarr / numcodecs internals
    "numcodecs.blosc", "numcodecs.lz4", "numcodecs.zstd",
    "numcodecs.compat_ext",
    # xarray
    "xarray.backends", "xarray.backends.zarr",
    # pynhd / pygeoogc
    "pynhd.core", "pynhd.nhdplus_derived", "pynhd.waterdata",
    "pygeoogc.core",
]

# ── PyInstaller Analysis ──────────────────────────────────────────────────────
a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=list(set(hidden)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter", "IPython", "jupyter", "notebook",
        "PyQt5", "PySide2", "PySide6",      # avoid Qt version conflicts
        "wx", "gi",                           # other GUI toolkits
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FIMsim",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # UPX can break Qt/GDAL binaries — keep off
    console=False,        # no terminal window
    argv_emulation=False,
    target_arch=None,
    icon=str(ROOT / "assets" / "icon.ico") if (ROOT / "assets" / "icon.ico").exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="FIMsim",
)

# ── macOS .app bundle ─────────────────────────────────────────────────────────
if IS_MAC:
    app = BUNDLE(
        coll,
        name="FIMsim.app",
        icon=str(ROOT / "assets" / "icon.icns") if (ROOT / "assets" / "icon.icns").exists() else None,
        bundle_identifier="edu.ua.fimsim",
        info_plist={
            "CFBundleDisplayName": "FIMsim",
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1.0.0",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
        },
    )
