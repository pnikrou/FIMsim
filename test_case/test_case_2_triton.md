# Test Case 2 — TRITON · Village Creek, Texas (Hurricane Harvey)

*AOI: `AOI_2_Texas/AOI_2.shp` · 390.1 km² · CRS EPSG:26914 (NAD83 / UTM 14N) ·
Event: Hurricane Harvey, **2017-08-24 → 2017-09-10***

In late August 2017, Hurricane Harvey stalled over southeast Texas and produced the
heaviest tropical rainfall ever recorded in the United States. Village Creek — a
tributary of the Neches River near Kountze, TX — experienced record flooding. This
walkthrough builds the complete TRITON input package for that event using the NWM
Retrospective discharge for the automatically detected reach.

### Step 1 — Project

On the FIMsim main page, open the **Flood Mapping** category and click **Start** on
the **TRITON** card:

![FIMsim main page — TRITON](images/triton_00_mainpage.png)

Create a new project — pick any project name and an empty output folder.

![Step 1 — Project setup](images/triton_01_project.png)

### Step 2 — AOI

Click **Browse** and select `AOI_2.shp`, tick the single feature, and click
**Add to confirmed AOIs**.

**Expected:**
- Area **390.13 km²**, State **Texas (TX)**, CRS **EPSG:26914**
- HUC6 **120200** | HUC8 12020003, 12020006, 12020007
- Main river: **Village Creek**
- USGS gage found: **08041500 — Village Ck nr Kountze, TX**

![Step 2 — AOI selection and overview](images/triton_02_aoi.png)

### Step 3 — DEM

Keep **Download from 3DEP (USGS)** with a **10 m** cell size and click **Run**.

**Expected:** a 1400 × 2800 px DEM at 10 m resolution (`DEM_<AOI>.tif`), with
elevations of roughly 3–38 m and Village Creek clearly visible as the low (blue)
corridor, plus `dem.asc` in `triton-files/`.

![Step 3 — DEM download and preview](images/triton_03_dem.png)

### Step 4 — Friction

First choose between **Fixed** and **Varying** roughness:

![Step 4 — Friction mode selection](images/triton_04_friction_mode.png)

Select **Varying**, then pick the LULC source and year. In Test Case 1 we used NLCD;
this time select **Sentinel-2 (ESRI, 10 m — global)** with year **2017** (the year of
the event) — demonstrating the globally available land-cover option:

![Step 4 — LULC source and Manning table](images/triton_05_friction_source.png)

**Expected:** LULC and Manning's n previews (the AOI is dominated by forest and woody
wetlands). Files produced: `LULC_<AOI>_<year>.tif`, `ManningN_<AOI>.tif`, and the
TRITON friction raster `friction.asc` snapped to the DEM grid.

![Step 4 — LULC and Manning's n maps](images/triton_06_friction_maps.png)

### Step 5 — BC (Boundary Conditions)

Keep **Auto-detect from NHD (USA)**. The downstream boundary type defaults to
**2 — Normal slope** with slope `0.001` — keep both:

![Step 5 — Downstream boundary type](images/triton_07_bc_type.png)

Click **Write .src + .extbc file(s)**.

**Expected:** the preview map shows Village Creek crossing the domain with the
upstream inflow point (orange) on the northwest edge and the downstream boundary
(red) at the southeast corner. Two files are generated and previewed: `<AOI>.src`
(inflow point coordinates) and `<AOI>.extbc` (the type-2 outflow boundary segment on
the DEM edge with slope 0.001).

![Step 5 — BC detection result and generated files](images/triton_08_bc_result.png)

### Step 6 — Hydrograph

The hydrograph step offers the same data sources as LISFLOOD's BDY step — NWM
Retrospective (1979–2023), NWM Forecast (2019–now), USGS Stream Gage, or your own
CSV/XLSX file:

![Step 6 — Hydrograph data source options](images/triton_09_hydro_sources.png)

In Test Case 1 (LISFLOOD-FP) we used a **USGS stream gage**; this time we download the
discharge from the **NWM** instead — simply to demonstrate both processes. Select
**NWM Retrospective (1979–2023)** and keep **Feature ID: Auto-detect** (you can
also enter a feature ID manually). Set:

- **Event start:** `2017-08-24 00:00`
- **Event end:** `2017-09-10 00:00`
- **Time interval:** 1.00 hours

Click **Write .hyg file(s)**.

**Expected:** the auto-detected feature ID is **1166365** (shown under the AOI name),
and the hydrograph preview shows the Hurricane Harvey flood wave peaking near
**3,250 m³/s around August 31**. The `<AOI>.hyg` file is written.

![Step 6 — Harvey hydrograph from NWM Retrospective](images/triton_10_hydro_hydrograph.png)

### Step 7 — Config

The final step assembles the TRITON configuration file. Keep the defaults (ASC output
format, time step, print interval — the simulation duration comes from the
hydrograph automatically) and run.

**Expected:** `<AOI>.cfg` is written. The `triton-files/` folder now contains the
complete TRITON input package — `dem.asc`, `friction.asc`, `<AOI>.src`,
`<AOI>.extbc`, `<AOI>.hyg`, `<AOI>.cfg` — ready to run with the TRITON solver.

![Step 7 — Config file](images/triton_11_config.png)

---

◀ [Test Case 1 — LISFLOOD-FP](test_case_1_lisflood.md)  ·  [Back to overview](README.md)  ·  [Next: Test Case 3 — Standalone tools](test_case_3_standalone_tools.md) ▶
