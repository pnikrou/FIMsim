# FIMsim Test Cases

Two ready-to-use study areas so you can try FIMsim end to end without any input data of
your own. Each test case walks through one complete flood-model setup:

| Test case | AOI shapefile | River | Model | Flood event |
|---|---|---|---|---|
| **1** | [`AOI_1_Susquehanna/AOI_1.shp`](AOI_1_Susquehanna) | West Branch Susquehanna River, PA | **LISFLOOD-FP** | Tropical Storm Lee — September 2011 |
| **2** | [`AOI_2_LittlePeeDee/AOI_2.shp`](AOI_2_LittlePeeDee) | Little Pee Dee River, SC | **TRITON** | Hurricane Matthew — October 2016 |

Both areas were selected from documented USGS **High Water Mark (HWM)** sites, so the
simulated flood extents can be compared against real observed flooding.

**What you need:** FIMsim (web app or desktop installer — see the
[main README](../README.md#getting-started)) and an internet connection. All terrain,
land-cover, river-network, and discharge data are downloaded automatically — the AOI
shapefile is the only input you provide.

> 💡 **Expected results are stated at every step** (river name, NWM reach ID, generated
> files). If your run shows the same values, everything is working correctly.

---

## Test Case 1 — LISFLOOD-FP · West Branch Susquehanna River (PA)

*AOI: `AOI_1_Susquehanna/AOI_1.shp` · ~33 km² · CRS EPSG:26918 (UTM 18N) ·
Event: Tropical Storm Lee, **2011-09-03 → 2011-09-13***

In September 2011, Tropical Storm Lee produced record flooding along the Susquehanna
basin in Pennsylvania. This walkthrough builds the complete LISFLOOD-FP input package
for a 10-day simulation of that event.

### Step 1 — Project

Open FIMsim and choose the **LISFLOOD-FP** model. Create a new project — pick any project
name (e.g. `Susquehanna_test`) and an empty folder for the outputs.

<!-- ![LISFLOOD Step 1 — Project](images/lisflood_01_project.png) -->

### Step 2 — AOI

Click **Browse** and select `AOI_1.shp` from this folder. The file contains a single
polygon feature — tick it and confirm.

**Expected:** the map preview shows one polygon in central Pennsylvania;
detected state: *Pennsylvania*; main river lookup returns
*West Branch Susquehanna River*.

<!-- ![LISFLOOD Step 2 — AOI](images/lisflood_02_aoi.png) -->

### Step 3 — DEM

Keep the default source (**USGS 3DEP**) and select a **10 m** resolution. Run the step.

**Expected:** the DEM downloads, is clipped to the AOI, and reprojected to the AOI's
UTM zone. Files produced: `DEM_<AOI>.tif` and `dem.ascii` in the AOI's
`lisflood-files/` folder.

<!-- ![LISFLOOD Step 3 — DEM](images/lisflood_03_dem.png) -->

### Step 4 — Manning

Choose **Varying** roughness → source **NLCD** (year 2019 or 2021). The Manning's n
lookup table is shown and editable — keep the defaults. Run the step.

**Expected:** the NLCD land-cover raster downloads and is converted to a Manning's n
raster snapped to the DEM grid. Files produced: `LULC_<AOI>_<year>.tif`,
`ManningN_<AOI>.tif`, and `lulc.ascii`.

<!-- ![LISFLOOD Step 4 — Manning](images/lisflood_04_manning.png) -->

### Step 5 — BCI (Boundary Conditions)

Keep **Auto-detect from NHD** with **Varying discharge (QVAR)** upstream. Run the step.

**Expected:** FIMsim downloads the NHD river network, identifies
*West Branch Susquehanna River* as the main river, determines the upstream end from
DEM elevations, and reports **NWM reach ID 8153791**. The `<AOI>.bci` file is written
with the upstream inflow point and the downstream boundary on the correct DEM edge.

<!-- ![LISFLOOD Step 5 — BCI](images/lisflood_05_bci.png) -->

### Step 6 — BDY (Hydrograph)

Select source **NWM Retrospective** and enter the event window:

- **Start:** `2011-09-03 00:00`
- **End:** `2011-09-13 00:00`
- **Interval:** 1 hour

Run the step.

**Expected:** the discharge time series for reach **8153791** downloads from the NWM
retrospective archive and the hydrograph preview shows a sharp flood peak around
**September 7–9, 2011**. The `<AOI>.bdy` file is written.

<!-- ![LISFLOOD Step 6 — BDY](images/lisflood_06_bdy.png) -->

### Step 7 — PAR (Parameter file)

Keep the defaults (simulation length is derived from the hydrograph automatically) and
run the step.

**Expected:** the `.par` file is written. The `lisflood-files/` folder now contains the
complete LISFLOOD-FP input package: `dem.ascii`, `lulc.ascii`, `<AOI>.bci`,
`<AOI>.bdy`, `<AOI>.par` — ready to run with any LISFLOOD-FP executable.

<!-- ![LISFLOOD Step 7 — PAR](images/lisflood_07_par.png) -->

---

## Test Case 2 — TRITON · Little Pee Dee River (SC)

*AOI: `AOI_2_LittlePeeDee/AOI_2.shp` · ~425 km² · CRS EPSG:26917 (UTM 17N) ·
Event: Hurricane Matthew, **2016-10-05 → 2016-10-25***

In October 2016, Hurricane Matthew caused severe flooding across the Pee Dee basin in
South Carolina. This walkthrough builds the complete TRITON input package for a 20-day
simulation of that event.

### Step 1 — Project

Open FIMsim and choose the **TRITON** model. Create a new project — pick any project
name (e.g. `LittlePeeDee_test`) and an empty output folder.

<!-- ![TRITON Step 1 — Project](images/triton_01_project.png) -->

### Step 2 — AOI

Click **Browse** and select `AOI_2.shp` from this folder. Tick the single polygon
feature and confirm.

**Expected:** the map preview shows one polygon in eastern South Carolina;
detected state: *South Carolina*; main river lookup returns *Little Pee Dee River*.

<!-- ![TRITON Step 2 — AOI](images/triton_02_aoi.png) -->

### Step 3 — DEM

Keep the default source (**USGS 3DEP**). This AOI is larger (~425 km²), so a **30 m**
resolution keeps download and simulation times reasonable. Run the step.

**Expected:** the DEM is downloaded, clipped, and reprojected. Files produced:
`DEM_<AOI>.tif` and `dem.asc` in the AOI's `triton-files/` folder.

<!-- ![TRITON Step 3 — DEM](images/triton_03_dem.png) -->

### Step 4 — Friction

Choose **Varying** roughness → source **NLCD** (year 2019 or 2021), keep the default
Manning table, and run the step.

**Expected:** files produced: `LULC_<AOI>_<year>.tif`, `ManningN_<AOI>.tif`, and the
TRITON friction raster `friction.asc` snapped to the DEM grid.

<!-- ![TRITON Step 4 — Friction](images/triton_04_friction.png) -->

### Step 5 — BC (Boundary Conditions)

Keep **Auto-detect from NHD (USA)**. The downstream boundary type defaults to
**2 — Normal slope** with slope `0.001` — keep it. Run the step.

**Expected:** FIMsim identifies *Little Pee Dee River* as the main river and reports
**NWM reach ID 9110054**. Files produced: `<AOI>.src` (upstream inflow point) and
`<AOI>.extbc` (downstream boundary segment snapped to the DEM edge).

<!-- ![TRITON Step 5 — BC](images/triton_05_bc.png) -->

### Step 6 — Hydrograph

Select source **NWM Retrospective** and enter the event window:

- **Start:** `2016-10-05 00:00`
- **End:** `2016-10-25 00:00`
- **Interval:** 1 hour

Run the step.

**Expected:** the discharge series for reach **9110054** downloads and the preview
shows the Hurricane Matthew flood wave rising after **October 8, 2016**. The
`<AOI>.hyg` file is written.

<!-- ![TRITON Step 6 — Hydrograph](images/triton_06_hydrograph.png) -->

### Step 7 — Config

Keep the defaults (output format ASC, time step, print interval) and run the step.

**Expected:** the `<AOI>.cfg` file is written with `num_sources`, `num_extbc`,
simulation duration (from the hydrograph), and relative references to all input files.
The `triton-files/` folder now contains the complete TRITON input package:
`dem.asc`, `friction.asc`, `<AOI>.src`, `<AOI>.extbc`, `<AOI>.hyg`, `<AOI>.cfg`.

<!-- ![TRITON Step 7 — Config](images/triton_07_config.png) -->

---

## Troubleshooting

- **A download step fails or times out** — the USGS / NOAA data services occasionally
  have outages; simply re-run the step.
- **Different reach ID than stated** — make sure you are on the latest FIMsim version;
  reach detection was significantly improved in July 2026.
- **NWM Retrospective date limits** — the archive covers **Feb 1979 – Jan 2023**. Both
  test events fall inside this window.
