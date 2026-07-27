# FIMsim Test Cases

Two ready-to-use study areas so you can try FIMsim end to end without any input data of
your own. Each test case walks through one complete flood-model setup:

| Test case | AOI shapefile | Location | Model | Flood event |
|---|---|---|---|---|
| **1** | [`AOI_1_Neuse/AOI_1.shp`](AOI_1_Neuse) | Neuse River, North Carolina | **LISFLOOD-FP** | Hurricane Matthew — October 2016 |
| **2** | [`AOI_2_Texas/AOI_2.shp`](AOI_2_Texas) | Southeast Texas | **TRITON** | Hurricane Harvey — August 2017 |

**What you need:** FIMsim (web app or desktop installer — see the
[main README](../README.md#getting-started)) and an internet connection. All terrain,
land-cover, river-network, and discharge data are downloaded automatically — the AOI
shapefile is the only input you provide.

> 💡 **Expected results are stated at every step** (detected river, gage, generated
> files). If your run shows the same values, everything is working correctly.

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

## Test Case 2 — TRITON · Southeast Texas (Hurricane Harvey)

*AOI: `AOI_2_Texas/AOI_2.shp` · ~390 km² · CRS EPSG:26914 (NAD83 / UTM 14N) ·
Event: Hurricane Harvey, **August–September 2017***

In late August 2017, Hurricane Harvey stalled over southeast Texas and produced the
heaviest tropical rainfall ever recorded in the United States, causing catastrophic
riverine flooding. This test case builds the complete TRITON input package
(DEM → Friction → BC → Hydrograph → Config) for that event.

> 🚧 **Step-by-step walkthrough with screenshots coming soon.** The AOI shapefile is
> already available in [`AOI_2_Texas/`](AOI_2_Texas) — the TRITON workflow follows the
> same 7 steps as Test Case 1 (Project → AOI → DEM → Friction → BC → Hydrograph →
> Config) and produces `dem.asc`, `friction.asc`, `AOI_2.src`, `AOI_2.extbc`,
> `AOI_2.hyg`, and `AOI_2.cfg`.

---

## Troubleshooting

- **A download step fails or times out** — the USGS / NOAA data services occasionally
  have outages; simply re-run the step.
- **NWM Retrospective date limits** — the archive covers **Feb 1979 – Jan 2023**; the
  NWM Forecast archive starts in 2019.
- **USGS gage data** — retrieved from waterdata.usgs.gov; 15-minute readings are
  resampled to your chosen time interval.
