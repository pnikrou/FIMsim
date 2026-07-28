# Test Case 3 — Standalone Input Data Tools · Lumber River, North Carolina

*AOI: `AOI_3_Lumber/AOI_03.shp` · 118.3 km² · CRS EPSG:26917 (NAD83 / UTM 17N)*

Besides the complete model pipelines, FIMsim's **Preparing Input Data** category
offers four standalone tools that each produce one type of input, usable in any model
or workflow. This test case runs all four on a Lumber River AOI in North Carolina
(HUC6 030402, HUC8 03040203; USGS gage **02134170 — Lumber River at Lumberton, NC**
inside the AOI), which also flooded during Hurricane Matthew in October 2016.

Each tool follows the same short pattern: pick the tool on the main page → create a
project folder → select the AOI → choose options → run.


**Tools in this test case:**
[Tool 1 — DEM](#tool-1--dem) · [Tool 2 — LULC & Manning](#tool-2--lulc--manning) · [Tool 3 — Flowline](#tool-3--flowline) · [Tool 4 — Streamflow Data](#tool-4--streamflow-data)

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

◀ [Test Case 2 — TRITON](test_case_2_triton.md)  ·  [Back to overview](README.md)
