# =============================================================================
# FIMsim — Windows PyInstaller spec
#
# BUILD (from the lisflood_prep_app/ directory, inside the conda env):
#   conda activate lisflood_workflow
#   pip install pyinstaller
#   pyinstaller build_windows.spec --noconfirm
#
# OUTPUT:
#   dist\FIMsim\FIMsim.exe  ← double-click to run (no Python needed)
#
# DISTRIBUTE:
#   Zip dist\FIMsim\ and share via Google Drive / Dropbox / WeTransfer.
#   Recipient extracts the zip, opens the FIMsim folder, double-clicks FIMsim.exe
#
# NOTE: Build must run ON a Windows machine.  The result only works on Windows.
# =============================================================================

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files

ROOT = Path(SPECPATH)   # absolute path to lisflood_prep_app\

# ── Sanity check ──────────────────────────────────────────────────────────────
assert sys.platform == "win32", (
    "build_windows.spec must be run on Windows. "
    "Use build_mac.spec on macOS."
)

# =============================================================================
# Step 1 — Collect every package that ships native binaries or data files.
# =============================================================================

datas    = []
binaries = []
hidden   = []

COLLECT_ALL_PKGS = [
    # ── Geospatial stack ──────────────────────────────────────────────────────
    "rasterio",           # GDAL .dll + proj data + gdal_data CSV tables
    "pyproj",             # PROJ .dll + proj.db
    "pyogrio",            # GDAL-based vector I/O (geopandas backend)
    "shapely",            # GEOS .dll
    "geopandas",          # pure Python + Fiona/pyogrio data
    "fiona",              # may be used by older geopandas installs
    # ── Scientific / numerical ────────────────────────────────────────────────
    "numpy",              # OpenBLAS .dll
    "scipy",              # LAPACK/BLAS .dll, ndimage, spatial
    "pandas",
    "openpyxl",
    "h5py",               # HDF5 .dll
    "netCDF4",            # NetCDF .dll (NWM retrospective)
    "h5netcdf",
    # ── Remote sensing / NWM ─────────────────────────────────────────────────
    "xarray",
    "zarr",
    "s3fs",
    "fsspec",
    "numcodecs",
    # ── Hydrography data downloads ────────────────────────────────────────────
    "pynhd",
    "pygeoogc",
    "pyflwdir",
    "hydrosignatures",
    # ── HTTP / networking ─────────────────────────────────────────────────────
    "requests",
    "certifi",
    "charset_normalizer",
    "urllib3",
    "aiohttp",
    "aiofiles",
    # ── Visualisation ────────────────────────────────────────────────────────
    "matplotlib",
    "matplotlib.backends",
    # ── GUI ──────────────────────────────────────────────────────────────────
    "PyQt6",
    # ── Mesh / misc ───────────────────────────────────────────────────────────
    "gmsh",
]

for pkg in COLLECT_ALL_PKGS:
    try:
        d, b, h = collect_all(pkg)
        datas    += d
        binaries += b
        hidden   += h
    except Exception:
        pass

# =============================================================================
# Step 2 — App data files
# =============================================================================

datas += [
    (str(ROOT / "data" / "us_states.geojson"), "data"),
    (str(ROOT / "data" / "us_huc6.geojson"),   "data"),
    (str(ROOT / "data" / "us_huc8.geojson"),   "data"),
    (str(ROOT / "assets"),                      "assets"),
]

# =============================================================================
# Step 3 — Hidden imports
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
    # ── scipy sub-modules ────────────────────────────────────────────────────
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
    # ── app core modules ──────────────────────────────────────────────────────
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
    # ── app gui modules ───────────────────────────────────────────────────────
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
        "tkinter", "_tkinter",
        "PyQt5", "PySide2", "PySide6",
        "wx", "gi",
        "IPython", "jupyter", "notebook", "nbformat",
        "sphinx", "pytest", "setuptools",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

# ── Executable ────────────────────────────────────────────────────────────────
ico = ROOT / "assets" / "icon.ico"
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FIMsim",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # UPX corrupts GDAL/Qt DLLs — always keep off
    console=False,        # no black console window behind the GUI
    argv_emulation=False,
    target_arch=None,
    icon=str(ico) if ico.exists() else None,
)

# ── Collect all into dist\FIMsim\ ─────────────────────────────────────────────
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="FIMsim",
)

# Windows has no BUNDLE() step — the output folder IS the distributable.
# Zip dist\FIMsim\ and share it.  Recipient extracts and runs FIMsim.exe.
