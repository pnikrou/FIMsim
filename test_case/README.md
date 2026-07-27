# FIMsim Test Cases

Two ready-to-use study areas so you can try FIMsim end to end without any input data of
your own. Each test case walks through one complete flood-model setup:

| Test case | AOI shapefile | Location | Model | Flood event |
|---|---|---|---|---|
| **1** | [`AOI_1_Neuse/AOI_1.shp`](AOI_1_Neuse) | Neuse River, North Carolina | **LISFLOOD-FP** | Hurricane Matthew — October 2016 |
| **2** | [`AOI_2_Texas/AOI_2.shp`](AOI_2_Texas) | Village Creek, Texas | **TRITON** | Hurricane Harvey — August 2017 |

**What you need:** FIMsim (web app or desktop installer — see the
[main README](../README.md#getting-started)) and an internet connection. You provide
the AOI shapefile plus the start and end dates of the flood event you want to
simulate — all terrain, land-cover, river-network, and discharge data are downloaded
automatically.

---

## Test Case 1 — LISFLOOD-FP · Neuse River, North Carolina

*AOI: `AOI_1_Neuse/AOI_1.shp` · 293.4 km² · CRS EPSG:26917 (NAD83 / UTM 17N) ·
Event: Hurricane Matthew, **2016-10-05 → 2016-10-20***

In early October 2016, Hurricane Matthew brought extreme rainfall to eastern North
Carolina, producing record flooding along the Neuse River. This walkthrough builds the
complete LISFLOOD-FP input package for a 15-day simulation of that event, using the
USGS stream gage inside the AOI for the inflow hydrograph.

### Step 1 — Project

Open FIMsim and choose the **LISFLOOD-FP** model. Create a new project — pick any
project name and an empty folder where all outputs will be written.

![Step 1 — Project setup](images/lisflood_01_project.png)

### Step 2 — AOI

Click **Browse** and select `AOI_1.shp`. You can load several AOI files, or pick one or
more features from a shapefile that contains multiple polygons — for this test case it
is a single feature: tick it and click **Add to confirmed AOIs**.

The panel gives an overview of the selected AOI: geographic location, area, HUC6/HUC8
codes, CRS, the main river, and any USGS gages found inside the AOI.

**Expected:**
- Area **293.40 km²**, State **North Carolina (NC)**, CRS **EPSG:26917**
- HUC6 **030202** | HUC8 03020201, 03020202, 03020203
- Main river: **Neuse River**
- USGS gage found: **02089000 — Neuse River near Goldsboro, NC**

![Step 2 — AOI selection and overview](images/lisflood_02_aoi.png)

### Step 3 — DEM

Keep **Download from 3DEP (USGS)** and set the DEM cell size to **10 m**. Click
**Run**. FIMsim downloads the elevation tiles, mosaics, resamples, reprojects to the
AOI's UTM zone, and clips to the AOI.

**Expected:** a 1630 × 1800 px DEM at 10 m resolution (`DEM_AOI_1.tif`), previewed
with elevations of roughly 15–65 m, plus `dem.ascii` in `lisflood-files/`.

![Step 3 — DEM download and preview](images/lisflood_03_dem.png)

### Step 4 — Manning

First choose between **Fixed** roughness (one value everywhere) and **Varying**
roughness (derived from land cover):

![Step 4 — Manning mode selection](images/lisflood_04_manning_mode.png)

Select **Varying**, then pick the LULC source and year — for this test case use
**NLCD**. FIMsim downloads the land-cover raster, resamples/reprojects/clips it to the
DEM grid, and converts it to a Manning's n map using the editable lookup table:

![Step 4 — LULC source and Manning table](images/lisflood_05_manning_source.png)

**Expected:** side-by-side LULC and Manning's n previews with per-class percentages
(the AOI is dominated by cultivated crops and woody wetlands). Files produced:
`LULC_AOI_1_<year>.tif`, `ManningN_AOI_1.tif`, and `lulc.ascii`.

![Step 4 — LULC and Manning's n maps](images/lisflood_06_manning_maps.png)

### Step 5 — BCI (Boundary Conditions)

Keep **Auto-detect from NHD (USA)**. Set:

- **Upstream boundary:** Varying discharge (QVAR — requires BDY file)
- **Downstream boundary:** Free normal depth (FREE), bed slope `0.0001`

Click **Write .bci file(s)**. FIMsim downloads the NHD river network, identifies the
main river, and derives the upstream/downstream boundary points from DEM elevations.

**Expected:** the BCI preview shows the Neuse River crossing the domain with the
upstream point (orange) on the west edge and the downstream point (red) on the
southeast corner, and the generated `AOI_1.bci` contains a `P … QVAR upstream1` line
and an `S … FREE 0.0001` line.

![Step 5 — BCI auto-detection](images/lisflood_07_bci.png)

### Step 6 — BDY (Hydrograph)

The hydrograph step offers several data sources: **NWM Retrospective** (1979–2023),
**NWM Forecast** (2019–present; short/medium/long range), **USGS Stream Gage**, or
your own CSV file:

![Step 6 — BDY data source options](images/lisflood_08_bdy_options.png)

For this test case select **USGS Stream Gage** with the gage found in Step 2:

- **Gage number:** `02089000`
- **Event start:** `2016-10-05 00:00`
- **Event end:** `2016-10-20 00:00`
- **Time interval:** 1.00 hours

Click **Create BDY File**.

**Expected:** the hydrograph preview shows the Hurricane Matthew flood wave — rising
sharply on **October 9** and peaking near **1,500 m³/s around October 12–13** — and
`AOI_1.bdy` is written.

![Step 6 — Hurricane Matthew hydrograph](images/lisflood_09_bdy_hydrograph.png)

### Step 7 — PAR (Parameter file)

The final step assembles the LISFLOOD-FP parameter file. You can choose between
solvers — the **Acceleration (ACC)** solver is recommended for most cases:

![Step 7 — Solver options](images/lisflood_10_par_solver.png)

…and set the initial condition (keep **Dry start — no initial water**):

![Step 7 — Initial condition options](images/lisflood_11_par_initial.png)

The simulation time is derived automatically from the hydrograph. Keep the remaining
defaults (1 s initial timestep, 3600 s save interval) — there is also an *Extra PAR
keywords* box for any additional LISFLOOD-FP keywords you may want:

![Step 7 — PAR file overview](images/lisflood_12_par_overview.png)

**Expected:** `AOI_1.par` is written. The `lisflood-files/` folder now contains the
complete LISFLOOD-FP input package — `dem.ascii`, `lulc.ascii`, `AOI_1.bci`,
`AOI_1.bdy`, `AOI_1.par` — ready to run with any LISFLOOD-FP executable.

---

## Test Case 2 — TRITON · Village Creek, Texas (Hurricane Harvey)

*AOI: `AOI_2_Texas/AOI_2.shp` · 390.1 km² · CRS EPSG:26914 (NAD83 / UTM 14N) ·
Event: Hurricane Harvey, **2017-08-24 → 2017-09-10***

In late August 2017, Hurricane Harvey stalled over southeast Texas and produced the
heaviest tropical rainfall ever recorded in the United States. Village Creek — a
tributary of the Neches River near Kountze, TX — experienced record flooding. This
walkthrough builds the complete TRITON input package for that event using the NWM
Retrospective discharge for the automatically detected reach.

> ℹ️ In the screenshots the AOI file carries the author's original name `AOI_02shp` —
> with the shapefile shipped here your run will show `AOI_2` instead.

### Step 1 — Project

Open FIMsim and choose the **TRITON** model. Create a new project — pick any project
name and an empty output folder.

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

Select **Varying**, then pick the LULC source and year (use **NLCD**):

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

Select **NWM Retrospective (1979–2023)** and keep **Feature ID: Auto-detect** (you can
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

## Troubleshooting

- **A download step fails or times out** — the USGS / NOAA data services occasionally
  have outages; simply re-run the step.
- **NWM Retrospective date limits** — the archive covers **Feb 1979 – Jan 2023**; the
  NWM Forecast archive starts in 2019.
- **USGS gage data** — retrieved from waterdata.usgs.gov; 15-minute readings are
  resampled to your chosen time interval.
