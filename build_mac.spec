# =============================================================================
# FIMsim — macOS PyInstaller spec
#
# BUILD (from the lisflood_prep_app/ directory, inside the conda env):
#   conda activate lisflood_workflow
#   pip install pyinstaller
#   pyinstaller build_mac.spec --noconfirm
#
# OUTPUT:
#   dist/FIMsim.app   ← double-click to run on any Mac (no Python needed)
#
# DISTRIBUTE:
#   cd dist && zip -r FIMsim-mac.zip FIMsim.app
#   Share FIMsim-mac.zip via Google Drive / Dropbox / WeTransfer.
#
# NOTE: Build must run ON a Mac.  The result only works on macOS.
# =============================================================================

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files

ROOT = Path(SPECPATH)   # absolute path to lisflood_prep_app/

# ── Sanity check ──────────────────────────────────────────────────────────────
assert sys.platform == "darwin", (
    "build_mac.spec must be run on macOS. "
    "Use build_windows.spec on Windows."
)

# =============================================================================
# Step 1 — Collect every package that ships native binaries or data files.
#           collect_all() finds .so/.dylib libraries AND data files so nothing
#           slips through.
# =============================================================================

datas    = []
binaries = []
hidden   = []

COLLECT_ALL_PKGS = [
    # ── Geospatial stack ──────────────────────────────────────────────────────
    "rasterio",           # GDAL .dylib + proj data + gdal_data CSV tables
    "pyproj",             # PROJ .dylib + proj.db
    "pyogrio",            # GDAL-based vector I/O (geopandas backend)
    "shapely",            # GEOS .dylib
    "geopandas",          # pure Python + Fiona/pyogrio data
    "fiona",              # may be used by older geopandas installs
    # ── Scientific / numerical ────────────────────────────────────────────────
    "numpy",              # OpenBLAS
    "scipy",              # LAPACK/BLAS, _ni_support, ndimage, spatial
    "pandas",             # core data
    "openpyxl",           # Excel (Manning table export)
    "h5py",               # HDF5 .dylib
    "netCDF4",            # NetCDF (NWM retrospective files)
    "h5netcdf",           # Alternative HDF5/NetCDF backend
    # ── Remote sensing / NWM ─────────────────────────────────────────────────
    "xarray",             # N-D arrays (NWM zarr store)
    "zarr",               # cloud-native array storage
    "s3fs",               # S3 filesystem (NOAA NWM retrospective)
    "fsspec",             # generic filesystem (zarr/s3fs dependency)
    "numcodecs",          # compression codecs (zarr)
    # ── Hydrography data downloads ────────────────────────────────────────────
    "pynhd",              # NHD flowline API
    "pygeoogc",           # WMS/WFS client (NLCD download)
    "pyflwdir",           # flow direction (optional pynhd dep)
    "hydrosignatures",    # hydrologic signatures (optional pynhd dep)
    # ── HTTP / networking ─────────────────────────────────────────────────────
    "requests",           # HTTP client
    "certifi",            # SSL root certificates (HTTPS downloads)
    "charset_normalizer", # requests dependency
    "urllib3",            # requests dependency
    "aiohttp",            # async HTTP (pynhd/pygeoogc)
    "aiofiles",           # async file I/O
    # ── Visualisation ────────────────────────────────────────────────────────
    "matplotlib",         # fonts, stylesheets, backends
    "matplotlib.backends",
    # ── GUI ──────────────────────────────────────────────────────────────────
    "PyQt6",              # Qt6 frameworks + plugins
    # ── Mesh / misc ───────────────────────────────────────────────────────────
    "gmsh",               # gmsh .dylib + Python API
]

for pkg in COLLECT_ALL_PKGS:
    try:
        d, b, h = collect_all(pkg)
        datas    += d
        binaries += b
        hidden   += h
    except Exception:
        pass   # package not installed — skip silently

# =============================================================================
# Step 2 — App data files (GeoJSON, assets) must travel with the bundle.
# =============================================================================

datas += [
    # USGS boundary / watershed data
    (str(ROOT / "data" / "us_states.geojson"), "data"),
    (str(ROOT / "data" / "us_huc6.geojson"),   "data"),
    (str(ROOT / "data" / "us_huc8.geojson"),   "data"),
    # App assets (logo, workflow diagram)
    (str(ROOT / "assets"),                      "assets"),
]

# =============================================================================
# Step 3 — Hidden imports: things PyInstaller cannot detect statically because
#           they are imported inside try/except blocks, loaded via importlib,
#           or are Qt plugins that must be present even if never referenced.
# =============================================================================

hidden += [
    # ── PyQt6 essentials ──────────────────────────────────────────────────────
    "PyQt6.QtWidgets",
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtNetwork",
    "PyQt6.QtSvg",
    "PyQt6.QtSvgWidgets",
    "PyQt6.QtPrintSupport",
    "PyQt6.QtOpenGL",
    "PyQt6.sip",
    # ── matplotlib Qt backend ─────────────────────────────────────────────────
    "matplotlib.backends.backend_qtagg",
    "matplotlib.backends.backend_qt",
    "matplotlib.backends.backend_agg",
    # ── scipy sub-modules (conditional imports in core/) ──────────────────────
    "scipy.ndimage",
    "scipy.ndimage._ni_support",
    "scipy.ndimage._ni_label",
    "scipy.spatial",
    "scipy.spatial.distance",
    "scipy.interpolate",
    "scipy._lib.messagestream",
    # ── zarr / numcodecs internals ─────────────────────────────────────────────
    "numcodecs.blosc",
    "numcodecs.lz4",
    "numcodecs.zstd",
    "numcodecs.compat_ext",
    "zarr.codecs",
    "zarr.storage",
    # ── xarray backends ───────────────────────────────────────────────────────
    "xarray.backends",
    "xarray.backends.zarr",
    "xarray.backends.netCDF4_",
    "xarray.backends.h5netcdf_",
    "xarray.backends.scipy_",
    # ── pynhd / pygeoogc internals ────────────────────────────────────────────
    "pynhd.core",
    "pynhd.nhdplus_derived",
    "pynhd.waterdata",
    "pygeoogc.core",
    # ── async networking ──────────────────────────────────────────────────────
    "aiohttp.resolver",
    "aiohttp.connector",
    "aiofiles",
    # ── SSL ───────────────────────────────────────────────────────────────────
    "ssl",
    "_ssl",
    # ── netCDF / HDF ─────────────────────────────────────────────────────────
    "netCDF4",
    "h5netcdf",
    "h5netcdf.legacyapi",
    # ── pandas extras ────────────────────────────────────────────────────────
    "pandas.io.formats.style",
    # ── app core modules (every module, no exceptions) ────────────────────────
    "core",
    "core.aoi",
    "core.aoi_info",
    "core.arc_manning",
    "core.arc_orchestrate",
    "core.bci",
    "core.bdy",
    "core.context",
    "core.crs_utils",
    "core.dem",
    "core.export",
    "core.flowline_mode",
    "core.hand",
    "core.manning",
    "core.multi_aoi",
    "core.nlcd",
    "core.nwm_discharge",
    "core.orchestrate",
    "core.par",
    "core.project",
    "core.river_lookup",
    "core.run_streamflow",
    "core.state_lookup",
    "core.triton_bc",
    "core.triton_cfg",
    "core.triton_hydro",
    "core.triton_manning",
    "core.triton_orchestrate",
    # ── app gui modules (every module) ────────────────────────────────────────
    "gui",
    "gui.aoi_bci_card",
    "gui.aoi_bdy_card",
    "gui.aoi_dem_card",
    "gui.aoi_flowdata_card",
    "gui.aoi_flowline_card",
    "gui.aoi_lulc_card",
    "gui.aoi_manning_card",
    "gui.aoi_par_card",
    "gui.aoi_triton_bc_card",
    "gui.aoi_triton_bdy_card",
    "gui.aoi_triton_cfg_card",
    "gui.aoi_triton_dem_card",
    "gui.aoi_triton_manning_card",
    "gui.app",
    "gui.arc_multi_aoi_widget",
    "gui.arc_step_placeholder",
    "gui.bci_config_panel",
    "gui.bci_preview",
    "gui.bdy_config_panel",
    "gui.current_aoi_panel",
    "gui.dem_config_panel",
    "gui.flowline_preview",
    "gui.hydrograph_preview",
    "gui.landing",
    "gui.manning_config_panel",
    "gui.manning_table_widget",
    "gui.map_viewer",
    "gui.mode_dem",
    "gui.mode_flowline",
    "gui.mode_lulc_manning",
    "gui.mode_streamflow",
    "gui.model_selector",
    "gui.multi_aoi_widget",
    "gui.overwrite_check",
    "gui.par_config_panel",
    "gui.raster_preview",
    "gui.run_button",
    "gui.status_banner",
    "gui.step_aoi",
    "gui.step_arc_aoi",
    "gui.step_arc_config",
    "gui.step_arc_dem",
    "gui.step_arc_flowline",
    "gui.step_arc_landcover",
    "gui.step_arc_project",
    "gui.step_arc_streamflow",
    "gui.step_bci",
    "gui.step_bdy",
    "gui.step_dem",
    "gui.step_manning",
    "gui.step_multi_aoi",
    "gui.step_par",
    "gui.step_project",
    "gui.step_triton_aoi",
    "gui.step_triton_bc",
    "gui.step_triton_cfg",
    "gui.step_triton_dem",
    "gui.step_triton_hydro",
    "gui.step_triton_manning",
    "gui.step_triton_project",
    "gui.triton_bc_config_panel",
    "gui.triton_bdy_config_panel",
    "gui.triton_cfg_config_panel",
    "gui.triton_dem_config_panel",
    "gui.triton_hydrograph_preview",
    "gui.triton_manning_config_panel",
    "gui.triton_multi_aoi_widget",
    "gui.triton_raster_preview",
    "gui.worker",
]

# =============================================================================
# Step 4 — Analysis
# =============================================================================

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=list(set(hidden)),
    hookspath=[str(ROOT / "hooks")],
    hooksconfig={},
    runtime_hooks=[str(ROOT / "hooks" / "runtime_hook_geo.py")],
    excludes=[
        # Other GUI toolkits — prevent Qt version conflicts
        "tkinter", "_tkinter",
        "PyQt5", "PySide2", "PySide6",
        "wx", "gi",
        # Dev / notebook tools (large, not needed at runtime)
        "IPython", "jupyter", "notebook", "nbformat",
        "sphinx", "pytest", "setuptools",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

# ── Executable ────────────────────────────────────────────────────────────────
icns = ROOT / "assets" / "icon.icns"
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FIMsim",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # UPX corrupts GDAL/Qt dylibs — always keep off
    console=False,        # no terminal window
    argv_emulation=False, # macOS only; keep False to avoid issues
    target_arch=None,     # native arch of build machine (x86_64 or arm64)
    icon=str(icns) if icns.exists() else None,
)

# ── Collect all binaries + data into dist/FIMsim/ ─────────────────────────────
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
app = BUNDLE(
    coll,
    name="FIMsim.app",
    icon=str(icns) if icns.exists() else None,
    bundle_identifier="edu.ua.ce.fimsim",
    info_plist={
        "CFBundleDisplayName":        "FIMsim",
        "CFBundleName":               "FIMsim",
        "CFBundleShortVersionString": "2.0.0",
        "CFBundleVersion":            "2.0.0",
        "CFBundleExecutable":         "FIMsim",
        "NSHighResolutionCapable":    True,
        "NSRequiresAquaSystemAppearance": False,   # allow dark mode
        # Allow opening files from network drives / Downloads folder
        "LSEnvironment": {
            "PROJ_NETWORK": "OFF",
        },
    },
)
