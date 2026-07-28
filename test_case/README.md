# FIMsim Test Cases

Three ready-to-use test cases so you can try FIMsim end to end without any input data
of your own — two complete flood-model setups and one tour of the standalone input
tools:

| Test case | AOI shapefile | Location | Model / Tool | Flood event |
|---|---|---|---|---|
| **1** | [`AOI_1_Neuse/AOI_1.shp`](AOI_1_Neuse) | Neuse River, North Carolina | **LISFLOOD-FP** | Hurricane Matthew — October 2016 |
| **2** | [`AOI_2_Texas/AOI_2.shp`](AOI_2_Texas) | Village Creek, Texas | **TRITON** | Hurricane Harvey — August 2017 |
| **3** | [`AOI_3_Lumber/AOI_03.shp`](AOI_3_Lumber) | Lumber River, North Carolina | **Standalone input tools** (DEM · LULC & Manning · Flowline · Streamflow) | Hurricane Matthew — October 2016 |

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

On the FIMsim main page, open the **Flood Mapping** category and click **Start** on
the **LISFLOOD-FP** card:

![FIMsim main page — LISFLOOD-FP](images/lisflood_00_mainpage.png)

Create a new project — pick any project name and an empty folder where all outputs
will be written.

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

Keep **Auto-detect from NHD (USA)**.

The upstream boundary can be a **Varying discharge (QVAR)** — driven by the hydrograph
from the next step — or a **Fixed discharge (QFIX)**. Select **QVAR**:

![Step 5 — Upstream boundary options](images/lisflood_07a_bci_upstream_options.png)

The downstream boundary can be a **Free normal depth (FREE)** or a **Fixed water level
(HFIX)**. Select **FREE** with bed slope `0.0001`:

![Step 5 — Downstream boundary options](images/lisflood_07b_bci_downstream_options.png)

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

## Test Case 3 — Standalone Input Data Tools · Lumber River, North Carolina

*AOI: `AOI_3_Lumber/AOI_03.shp` · 118.3 km² · CRS EPSG:26917 (NAD83 / UTM 17N)*

Besides the complete model pipelines, FIMsim's **Preparing Input Data** category
offers four standalone tools that each produce one type of input, usable in any model
or workflow. This test case runs all four on a Lumber River AOI in North Carolina
(HUC6 030402, HUC8 03040203; USGS gage **02134170 — Lumber River at Lumberton, NC**
inside the AOI), which also flooded during Hurricane Matthew in October 2016.

Each tool follows the same short pattern: pick the tool on the main page → create a
project folder → select the AOI → choose options → run.

### Tool 1 — DEM

On the main page, open **Preparing Input Data** and start the **DEM** tool:

![Main page — DEM tool](images/sa_dem_01_mainpage.png)

Create a project folder:

![DEM — project folder](images/sa_dem_02_project.png)

Select `AOI_03.shp` — exactly like the model pipelines, the AOI step shows the
domain's location, area, CRS, HUC codes, main river, and USGS gages:

![DEM — AOI selection](images/sa_dem_03_aoi.png)

Choose the output **format** — GeoTIFF (TIF), GeoPackage (GPKG), or ASCII grid (ASC):

![DEM — output format options](images/sa_dem_04_format_options.png)

…and the elevation **source** — USGS 3DEP or HAND (TACC) — plus the cell size:

![DEM — source options](images/sa_dem_05_source_options.png)

Run. **Expected:** a 10 m DEM (`DEM_3DEP_AOI_03.tif`) clipped to the AOI, with a
preview and summary statistics (elevations ≈ 30–55 m for this domain):

![DEM — result preview](images/sa_dem_06_result.png)

### Tool 2 — LULC & Manning

Start the **LULC & Manning** tool:

![Main page — LULC & Manning tool](images/sa_lulc_01_mainpage.png)

Create a project folder:

![LULC — project folder](images/sa_lulc_02_project.png)

Select the AOI:

![LULC — AOI selection](images/sa_lulc_03_aoi.png)

Choose the **LULC output format** (TIF / GPKG / ASC / polygonized SHP):

![LULC — output format options](images/sa_lulc_04_lulc_format.png)

…the **Manning's n output format** — the editable Manning lookup table (Min/Max
reference bounds, editable Avg per class) is right below:

![Manning — output format and lookup table](images/sa_lulc_05_manning_format.png)

…and the **LULC source** — NLCD (USGS, 30 m, USA) or Sentinel-2 (ESRI, 10 m, global):

![LULC — source options](images/sa_lulc_06_source_options.png)

Run. **Expected:** the land-cover breakdown table (this AOI is dominated by
cultivated crops and woody wetlands) with the LULC map and the derived Manning's n
map side by side:

![LULC and Manning's n result](images/sa_lulc_07_result.png)

### Tool 3 — Flowline

Start the **Flowline** tool:

![Main page — Flowline tool](images/sa_flowline_01_mainpage.png)

Create a project folder:

![Flowline — project folder](images/sa_flowline_02_project.png)

Select the AOI:

![Flowline — AOI selection](images/sa_flowline_03_aoi.png)

Choose what to download and in which format — the **main river** (NHD highest stream
order) as SHP / GPKG / TIF raster / CSV:

![Flowline — main river format options](images/sa_flowline_04_mainriver_format.png)

…optionally **all flowlines** (the full NHD reach set) and the list of **USGS gages**
in the domain as CSV:

![Flowline — all flowlines format options](images/sa_flowline_05_allflowlines_format.png)

Run. **Expected:** a map with the main Lumber River channel, the full flowline
network, gage 02134170, and the detected upstream/downstream endpoints:

![Flowline — result map](images/sa_flowline_06_result.png)

### Tool 4 — Streamflow Data

Start the **Streamflow Data** tool — this one needs **no AOI**; it downloads
discharge time series directly by NWM feature ID or USGS gage number:

![Main page — Streamflow tool](images/sa_streamflow_01_mainpage.png)

Create a project folder:

![Streamflow — project folder](images/sa_streamflow_02_project.png)

Three sources are available. **NWM Retrospective (1979–2023)** — one or more feature
IDs (or a CSV of IDs), a date window, and an interval:

![Streamflow — NWM Retrospective](images/sa_streamflow_03_nwm_retro.png)

**NWM Forecast (2019–now)** — feature IDs plus forecast range (short / medium /
long), issue date, and cycle:

![Streamflow — NWM Forecast](images/sa_streamflow_04_nwm_forecast.png)

**USGS Stream Gage** — gage numbers (or a CSV of gages), date window, and interval:

![Streamflow — USGS gage](images/sa_streamflow_05_usgs.png)

Run. **Expected** (USGS gage `02134170`, 2016-10-01 → 2016-10-19, 1 h): the Hurricane
Matthew flood wave on the Lumber River, peaking near **410 m³/s around October 11**,
saved as a CSV time series:

![Streamflow — downloaded hydrograph](images/sa_streamflow_06_result.png)

---

## Troubleshooting

- **A download step fails or times out** — the USGS / NOAA data services occasionally
  have outages; simply re-run the step.
- **NWM Retrospective date limits** — the archive covers **Feb 1979 – Jan 2023**; the
  NWM Forecast archive starts in 2019.
- **USGS gage data** — retrieved from waterdata.usgs.gov; 15-minute readings are
  resampled to your chosen time interval.
