# FIMsim — Build & Distribution Guide

---

## Quick answer: yes, it produces a true installer

With PyInstaller, **everything is bundled** inside the installer —
Python, Qt libraries, GDAL/PROJ, rasterio, geopandas, scipy, h5py, gmsh, and
all other packages.  End users:

- Download one file (`.dmg` on Mac, `.exe` on Windows)
- Double-click → next → finish
- Launch FIMsim from their desktop / Start Menu

No Python, no conda, no terminal required.

---

## Files in this folder

| File | Purpose |
|------|---------|
| `build_app.spec` | PyInstaller spec (used on both Mac & Windows) |
| `build_mac.sh` | One-command Mac build → `.app` + `.dmg` |
| `build_windows.bat` | One-command Windows build → EXE folder |
| `installer_windows.iss` | Inno Setup script → `FIMsim-setup.exe` |
| `.github/workflows/build.yml` | GitHub Actions — builds both platforms automatically |

---

## Option A: Build on your Mac (produces Mac `.app` / `.dmg`)

### Step 1 — Install PyInstaller into your conda env
```bash
conda activate lisflood_workflow
pip install pyinstaller
```

### Step 2 — Run the build script
```bash
cd lisflood_prep_app
chmod +x build_mac.sh
./build_mac.sh
```

This takes 3–8 minutes.  Output:
```
dist/
├── FIMsim.app          ← the Mac app bundle
└── FIMsim-mac.dmg      ← drag-to-install disk image (if create-dmg installed)
    or FIMsim-mac.zip   ← zip of the .app (if create-dmg not installed)
```

### Step 3 — Optional: nicer .dmg
```bash
brew install create-dmg    # one-time
./build_mac.sh             # re-run; will now produce a proper .dmg
```

Share `FIMsim-mac.dmg` with Mac users.  They drag it to Applications — done.

---

## Option B: Build Windows `.exe` via GitHub Actions (recommended — no Windows needed)

This is the easiest way to produce a Windows installer **without owning a Windows PC**.
GitHub provides free Windows runner VMs.

### Step 1 — Push your code to GitHub

```bash
# One-time: create a GitHub repo and push
cd /path/to/Chapter2         # the parent of lisflood_prep_app/
git init
git add lisflood_prep_app/
git commit -m "Initial commit"
# Create a repo on github.com, then:
git remote add origin https://github.com/YOUR_USERNAME/fimsim.git
git push -u origin main
```

### Step 2 — Trigger the build by creating a version tag

```bash
git tag v1.0.0
git push origin v1.0.0
```

GitHub Actions will automatically:
1. Spin up a Windows VM
2. Install conda + all packages
3. Run PyInstaller → `dist\FIMsim\`
4. Run Inno Setup → `FIMsim-setup-windows.exe`
5. Spin up a Mac VM → produce `FIMsim-mac.dmg`
6. Create a GitHub Release with both files attached

### Step 3 — Download the installer

Go to **github.com/YOUR_USERNAME/fimsim/releases** → download
`FIMsim-setup-windows.exe` and share it.

> You can also trigger a build manually without creating a tag:
> GitHub → your repo → **Actions** tab → **Build FIMsim Installers** →
> **Run workflow** button.

---

## Option C: Build on a Windows PC directly

If you have access to a Windows machine:

```bat
:: In Anaconda Prompt on Windows:
conda create -n lisflood_workflow python=3.11
conda activate lisflood_workflow

:: Install geospatial packages (GDAL/PROJ binaries)
conda install -c conda-forge geopandas pyogrio rasterio pyproj shapely scipy numpy pandas openpyxl h5py requests

:: Install remaining packages
pip install PyQt6 matplotlib xarray zarr s3fs fsspec numcodecs pynhd pygeoogc gmsh certifi pyinstaller

:: Build the app
cd lisflood_prep_app
build_windows.bat
```

Then open `installer_windows.iss` in **Inno Setup Compiler**
([free download](https://jrsoftware.org/isdl.php)) and click **Build > Compile**.

Output: `dist\FIMsim-setup-windows.exe`

---

## Build output sizes (approximate)

| Platform | Folder size | Installer size |
|----------|-------------|----------------|
| Windows | ~350 MB | ~180 MB (compressed) |
| macOS | ~400 MB | ~200 MB (.dmg) |

Large because GDAL, Qt, rasterio, scipy, gmsh all bundle their own shared libraries.
This is normal for geospatial desktop apps.

---

## Running the app from source (development)

```bash
conda activate lisflood_workflow
cd lisflood_prep_app
python main.py
```

---

## Troubleshooting the build

### "ModuleNotFoundError: No module named X" at runtime
Add the missing package to `hiddenimports` in `build_app.spec`, then rebuild.

### App crashes silently on launch (Mac)
Run from Terminal to see error output:
```bash
./dist/FIMsim.app/Contents/MacOS/FIMsim
```

### App crashes silently on launch (Windows)
Temporarily set `console=True` in `build_app.spec` → `exe = EXE(... console=True ...)`,
rebuild, run the `.exe` from Command Prompt to see the traceback.

### GDAL / PROJ errors ("Unable to open EPSG support file")
The pyproj/rasterio PROJ data is not bundled correctly. Check that
`collect_all('pyproj')` and `collect_all('rasterio')` are in the spec (they are).

### Build is very slow (> 15 min)
Normal for the first build — PyInstaller collects thousands of files from
numpy/scipy/GDAL/Qt. Subsequent builds are faster if you do not delete `build/`.

---

## What gets bundled (user never installs these)

- Python 3.11 runtime
- PyQt6 + Qt 6 libraries
- GDAL + PROJ (rasterio, pyogrio, pyproj)
- GEOS (shapely)
- geopandas, numpy, scipy, pandas
- matplotlib (maps, hydrograph plots)
- h5py + HDF5 library (HEC-RAS files)
- gmsh + API (mesh generation)
- xarray, zarr, s3fs (NWM data download)
- requests, certifi (HTTPS downloads)
- pynhd, pygeoogc (NHD / WMS queries)
- All app GeoJSON data files (US states, HUC6, HUC8)
