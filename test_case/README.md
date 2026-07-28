# FIMsim Test Cases

Three ready-to-use test cases so you can try FIMsim end to end without any input data
of your own — two complete flood-model setups and one tour of the standalone input
tools. **Open a test case, follow it step by step, then continue to the next one:**

### 1. [Test Case 1 — LISFLOOD-FP · Neuse River, North Carolina](test_case_1_lisflood.md)
Complete LISFLOOD-FP setup for the Hurricane Matthew flood (October 2016) —
AOI shapefile: [`AOI_1_Neuse/AOI_1.shp`](AOI_1_Neuse)

### 2. [Test Case 2 — TRITON · Village Creek, Texas](test_case_2_triton.md)
Complete TRITON setup for the Hurricane Harvey flood (August 2017) —
AOI shapefile: [`AOI_2_Texas/AOI_2.shp`](AOI_2_Texas)

### 3. [Test Case 3 — Standalone Input Data Tools · Lumber River, North Carolina](test_case_3_standalone_tools.md)
All four Preparing Input Data tools on one AOI — AOI shapefile:
[`AOI_3_Lumber/AOI_03.shp`](AOI_3_Lumber)
- [Tool 1 — DEM](test_case_3_standalone_tools.md#tool-1--dem)
- [Tool 2 — LULC & Manning](test_case_3_standalone_tools.md#tool-2--lulc--manning)
- [Tool 3 — Flowline](test_case_3_standalone_tools.md#tool-3--flowline)
- [Tool 4 — Streamflow Data](test_case_3_standalone_tools.md#tool-4--streamflow-data)

**What you need:** FIMsim (web app or desktop installer — see the
[main README](../README.md#getting-started)) and an internet connection. You provide
the AOI shapefile plus the start and end dates of the flood event you want to
simulate — all terrain, land-cover, river-network, and discharge data are downloaded
automatically.

---

## Troubleshooting

- **A download step fails or times out** — the USGS / NOAA data services occasionally
  have outages; simply re-run the step.
- **NWM Retrospective date limits** — the archive covers **Feb 1979 – Jan 2023**; the
  NWM Forecast archive starts in 2019.
- **USGS gage data** — retrieved from waterdata.usgs.gov; 15-minute readings are
  resampled to your chosen time interval.
