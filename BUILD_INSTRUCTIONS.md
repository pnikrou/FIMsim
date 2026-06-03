# Flood Model Preprocessing Tool — Setup & Build Instructions

---

## 0. What to copy to a new computer

Copy **only** the `lisflood_prep_app/` folder (the folder that contains `main.py`).

```
lisflood_prep_app/
├── main.py               ← entry point
├── requirements.txt      ← all Python dependencies
├── BUILD_INSTRUCTIONS.md
├── build_app.spec        ← PyInstaller spec (only needed to build EXE)
├── core/                 ← all backend logic
├── gui/                  ← all GUI code
└── data/                 ← bundled GeoJSON files (states, HUC6, HUC8)
```

> **Do NOT copy** the `cache/` or `__pycache__/` folders — they are auto-generated
> and machine-specific.

---

## 1. Set up the Python environment

Python **3.11** is required (3.12+ has not been tested).

```bash
# Create a fresh conda environment
conda create -n lisflood_app python=3.11
conda activate lisflood_app

# Install all dependencies
pip install -r requirements.txt
```

### What gets installed (`requirements.txt`)

| Package | Purpose |
|---------|---------|
| `PyQt6` | Desktop GUI framework |
| `matplotlib` | Maps, raster previews, hydrograph plots |
| `numpy` | Array / numerical operations |
| `scipy` | DEM nodata fill, TRITON Manning raster |
| `pandas` | Tabular data, CSV I/O |
| `openpyxl` | Excel file support (discharge XLSX import) |
| `geopandas` | Vector GIS (AOI, flowlines, Manning shapefiles) |
| `shapely` | Geometry operations |
| `pyproj` | CRS / coordinate reprojection |
| `rasterio` | Raster read/write/clip/warp |
| `requests` | General HTTP downloads |
| `pynhd` | NHD flowlines + USGS gage lookup (USA only) |
| `xarray` | N-D arrays for NWM Zarr data |
| `zarr` | NWM retrospective Zarr store on S3 |
| `s3fs` | S3 filesystem access for NWM download |
| `numcodecs` | Zarr codec support |

---

## 2. Run the app

```bash
cd lisflood_prep_app
python main.py
```

---

## 3. Build a standalone executable with PyInstaller

PyInstaller is **only** needed when creating a distributable EXE/app bundle.
Install it separately so it does not pollute the run-time environment:

```bash
pip install pyinstaller
cd lisflood_prep_app
pyinstaller build_app.spec
```

The output will be in `dist/LISFLOOD_FP_PrepTool/`.
Zip that folder and share it — users just unzip and run `LISFLOOD_FP_PrepTool.exe`
(Windows) or `LISFLOOD_FP_PrepTool` (Mac/Linux). No Python installation needed.

---

## 4. Building a single-file EXE (Windows only, slower startup)

Change the `COLLECT` block in `build_app.spec` to an `EXE` with `onefile=True`:

```python
exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas,
    name='LISFLOOD_FP_PrepTool',
    console=False,
    onefile=True,
)
```

---

## Notes

- The app requires an **internet connection** for downloading DEM (3DEP), LULC (ESRI),
  NHD flowlines (pynhd), and NWM retrospective streamflow (NOAA Zarr).
- **3DEP DEM** covers the **USA only**. For other countries, provide your own DEM.
- **LULC** download covers **global** extents via ESRI Sentinel-2 service.
- **NWM retrospective** covers **USA, 1979–2020**. Dates after 2020-12-31 will
  automatically switch to the NWM operational forecast (~10-day horizon).
