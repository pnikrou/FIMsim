# FIMsim — Flood Inundation Model Simulation Tool

> **v1.0** · Web Application · Desktop installers available for macOS and Windows

FIMsim is a web-based tool designed to eliminate the technical barrier of setting up 2D flood simulations. It serves two distinct purposes: **(1) automated preparation of individual hydraulic model input data** — including terrain, land cover, river networks, and streamflow time series — and **(2) end-to-end configuration and cloud execution of complete flood mapping simulations** for four supported flood mapping models (LISFLOOD-FP, TRITON, ARC-Curve2Flood, and OWP HAND-FIM). Users define a study area, and FIMsim handles all data downloading, processing, and file formatting automatically. For users who prefer a local setup, standalone desktop installers for macOS and Windows are also available — no Python installation or technical configuration required.

---

## What the app does

FIMsim addresses two separate but related challenges in flood modeling:

**1. Preparing individual model inputs independently:**
Hydrologists often need specific geospatial datasets — a DEM for one project, LULC for another, streamflow records for analysis — without running a full simulation. FIMsim provides four standalone tools that each produce one type of input file in different formats, ready to use in any model or workflow.

**2. Running a complete flood simulation end to end:**
When the goal is a full flood inundation map, FIMsim can take over the entire pipeline. After the user defines a study area, it downloads every required input, writes all model-specific configuration files, and submits the simulation to run on cloud infrastructure — no local software installation or GIS expertise required.

| Track | What it does |
|---|---|
| **Independent Hydraulic Model Inputs** | Four standalone tools for downloading and processing individual geospatial inputs (DEM, LULC & Manning's n, Flowlines, Streamflow Data), each usable independently of any specific flood model |
| **Flood Mapping Models** | Four complete flood-mapping pipelines — each downloads all required inputs, writes all model files, and runs the simulation on cloud infrastructure |

---

## Workflow overview

<img src="assets/FIMsim_workflow.png" width="130%" alt="FIMsim Workflow">

---

## Data sources

FIMsim connects to the following public data services. An internet connection is required during data downloads.

| Dataset | Provider | Coverage |
|---|---|---|
| Digital Elevation Model (DEM) | USGS 3DEP (1 m, 10 m, 30 m) | USA |
| Height Above Nearest Drainage (HAND) | TACC | USA |
| Land Use / Land Cover (LULC) | NLCD — USGS | USA |
| Land Use / Land Cover (LULC) | Sentinel-2 — Esri | Global |
| River flowlines | NHD — USGS | USA |
| USGS stream gages | USGS Water Services | USA |
| Streamflow time series | NWM Retrospective v2.1 — NOAA | USA · 1979–2020 |
| Streamflow forecast | NWM Operational — NOAA | USA · ~10-day horizon |

---

## Supported flood models

| Model | Type | Input files generated |
|---|---|---|
| **LISFLOOD-FP** | 2D raster-based | `.par` · `.bci` · `.bdy` · DEM and Manning ASCII grids |
| **TRITON** | 2D GPU-accelerated | `.cfg` · `.extbc` · `.hyg` · DEM and friction ASCII grids |
| **ARC-Curve2Flood** | Rapid rating-curve flood mapping | NenCarta input package — configuration JSON · DEM · land-cover raster · Manning's n table · flowlines · streamflow |
| **OWP HAND-FIM** | HAND-based inundation mapping | Flood inundation maps generated directly from the AOI (HUC8 → NWM discharge → FIM) |

---

## Key features

- **Multi-AOI batch processing** — define multiple study areas in a single shapefile or GeoPackage; all downloads and outputs are handled per AOI automatically
- **Editable Manning's n table** — the LULC step generates a land-cover lookup table with Min / Avg / Max roughness values that the user can edit before export
- **Upstream / downstream detection** — the flowline step automatically identifies the upstream and downstream endpoints of the main river and marks them on the map
- **Map preview** — at each step, the app plots the results for visual inspection before saving

---

## Getting started

### Web application
FIMsim is deployed as a web application — no installation required. Open it in any browser and start a project immediately.

> 🔗 **Live app:** *(link coming soon)*

### Desktop installers
For users who prefer a local installation, standalone installers are available on the [Releases](../../releases) page — no Python or conda setup required.

| Platform | File |
|---|---|
| macOS | `FIMsim-mac.dmg` — drag to Applications |
| Windows | `FIMsim-setup-windows.exe` — run installer wizard |

### Run from source (contributors)
```bash
git clone https://github.com/pnikrou/FIMsim.git
cd FIMsim
conda env create -f environment.yml
conda activate FIMsim
python main.py
```

---

## 🧪 Try it — no input data needed

Don't have a study area of your own? The [**`test_case/`**](test_case) folder ships two
ready-to-use AOI shapefiles with complete step-by-step walkthroughs — you provide the
AOI polygon and the event start/end dates; FIMsim downloads everything else
automatically.

| Test case | Location | Model | Flood event |
|---|---|---|---|
| [AOI 1](test_case/test_case_1_lisflood.md) | Neuse River, North Carolina | LISFLOOD-FP | Hurricane Matthew (Oct 2016) |
| [AOI 2](test_case/test_case_2_triton.md) | Village Creek, Texas | TRITON | Hurricane Harvey (Aug 2017) |
| [AOI 3](test_case/test_case_3_standalone_tools.md) | Lumber River, North Carolina | Standalone input tools | Hurricane Matthew (Oct 2016) |

Each walkthrough includes a screenshot of every step and states the expected result
(detected river, USGS gage, generated files) so you can verify your installation is
working correctly.


