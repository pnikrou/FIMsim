"""Orchestrators for the Flowline & Flow Data mode.

Two separate entry points:
  run_flowline_mode   — NHD flowlines, gages CSV, feature IDs CSV  (Step 2)
  run_flowdata_mode   — NWM or USGS discharge download              (Step 3)

All outputs land under  ``{project_dir}/Flowline_for_AOI/{aoi_folder}/``.
"""
from pathlib import Path
from typing import List, Dict, Any

import numpy as np
import pandas as pd

from core.multi_aoi import AOIFeatureInfo, get_single_feature_gdf
from core.aoi_info import lookup_nhd_flowlines_clipped, lookup_usgs_gages
from core.nwm_discharge import (
    download_nwm_retrospective, download_nwm_forecast,
    _coerce_feature_ids,
)
from core.export import next_free_path


# ── small helpers ─────────────────────────────────────────────────────────────

def _coerce_gage_ids(gage_ids) -> List[str]:
    """Accept single gage string, comma-separated list, or path to a CSV
    (one gage per line, no header) and return a list of zero-padded USGS
    site-number strings.

    USGS site numbers are 8-digit strings.  Numeric storage (int, pandas
    int64, CSV integer column) silently drops leading zeros; this function
    restores them so the NWIS API receives the correct identifier.
    """
    def _pad(s: str) -> str:
        """Zero-pad a pure-digit string shorter than 8 chars → 8 digits."""
        s = s.strip()
        return s.zfill(8) if s.isdigit() and len(s) < 8 else s

    if gage_ids is None:
        return []
    if isinstance(gage_ids, (int, np.integer)):
        return [_pad(str(int(gage_ids)))]
    if isinstance(gage_ids, str):
        s = gage_ids.strip()
        if not s:
            return []
        if s.lower().endswith((".csv", ".txt")) and Path(s).exists():
            df = pd.read_csv(s, header=None, dtype=str)   # dtype=str preserves leading zeros
            return [_pad(str(v).strip())
                    for v in df.iloc[:, 0].dropna().tolist()
                    if str(v).strip()]
        return [_pad(p) for p in s.replace(",", " ").split() if p.strip()]
    try:
        return [_pad(str(x).strip()) for x in gage_ids if str(x).strip()]
    except TypeError:
        return [_pad(str(gage_ids))]


def _save_shapefile(gdf, out_path: Path, log_fn) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Remove stale sidecars so fiona never raises a schema mismatch
    for sib in out_path.parent.glob(out_path.stem + ".*"):
        try:
            sib.unlink()
        except Exception:
            pass
    gdf.to_file(out_path, driver="ESRI Shapefile")
    log_fn(f"  ✓ Saved {out_path.name}")
    return out_path


def _save_geopackage(gdf, out_path: Path, log_fn) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # GPKG cannot be opened "w" if it already exists — delete first
    if out_path.exists():
        try:
            out_path.unlink()
        except Exception:
            pass
    gdf.to_file(out_path, driver="GPKG")
    log_fn(f"  ✓ Saved {out_path.name}")
    return out_path


def _save_flowlines_csv(gdf, out_path: Path, log_fn) -> Path:
    """Save flowlines as CSV with WKT geometry column plus any attributes."""
    import geopandas as gpd  # noqa: F401 — ensure geopandas is available
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df = gdf.copy()
    df["geometry"] = df["geometry"].apply(lambda g: g.wkt if g is not None else "")
    df.to_csv(out_path, index=False)
    log_fn(f"  ✓ Saved {out_path.name} ({len(df)} features)")
    return out_path


def _rasterize_flowlines_tif(gdf, aoi_gdf, cell_size_m: float,
                              out_path: Path, log_fn) -> Path:
    """Burn flowlines into a binary GeoTIFF (1 = flowline cell, 0 = background)."""
    import rasterio
    from rasterio.features import rasterize as rio_rasterize
    from rasterio.transform import from_bounds
    from core.crs_utils import pick_working_crs_epsg

    epsg = pick_working_crs_epsg(aoi_gdf)
    gdf_m   = gdf.to_crs(epsg=epsg)
    aoi_m   = aoi_gdf.to_crs(epsg=epsg)

    minx, miny, maxx, maxy = aoi_m.total_bounds
    width  = max(1, int(np.ceil((maxx - minx) / cell_size_m)))
    height = max(1, int(np.ceil((maxy - miny) / cell_size_m)))
    transform = from_bounds(minx, miny, maxx, maxy, width, height)

    shapes = [
        (geom, 1)
        for geom in gdf_m.geometry
        if geom is not None and not geom.is_empty
    ]
    if shapes:
        arr = rio_rasterize(
            shapes, out_shape=(height, width),
            transform=transform, fill=0, dtype=np.uint8,
        )
    else:
        arr = np.zeros((height, width), dtype=np.uint8)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # GTiff overwrites fine, but ensure any stale file is gone first
    if out_path.exists():
        try:
            out_path.unlink()
        except Exception:
            pass
    with rasterio.open(
        out_path, "w", driver="GTiff",
        height=height, width=width,
        count=1, dtype=np.uint8,
        crs=f"EPSG:{epsg}", transform=transform,
        compress="lzw",
    ) as dst:
        dst.write(arr, 1)

    log_fn(f"  ✓ Saved {out_path.name} ({width}×{height} px @ {cell_size_m:.0f} m)")
    return out_path


def _save_gages_csv(gages: List[Dict], out_csv: Path, log_fn) -> Path:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    if not gages:
        pd.DataFrame(
            columns=["site_no", "station_nm", "lat", "lon", "drain_sqkm", "state"]
        ).to_csv(out_csv, index=False)
        log_fn(f"  ✓ Saved (empty) {out_csv.name}")
    else:
        df = pd.DataFrame(gages)
        cols = [c for c in
                ("site_no", "station_nm", "lat", "lon", "drain_sqkm", "state")
                if c in df.columns]
        df[cols].to_csv(out_csv, index=False)
        log_fn(f"  ✓ Saved {out_csv.name} ({len(df)} gages)")
    return out_csv


def _save_feature_ids_csv(flowlines_gdf, out_csv: Path, log_fn) -> Path:
    """Save COMIDs with a ``feature_id`` header — readable directly by the
    Flow Data step.  A named header row lets spreadsheet viewers display row 1
    as the column title (styled) and the data rows below it as plain rows."""
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    if flowlines_gdf is None or flowlines_gdf.empty:
        pd.DataFrame(columns=["feature_id"]).to_csv(out_csv, index=False)
        log_fn(f"  ✓ Saved (empty) {out_csv.name}")
        return out_csv

    df = flowlines_gdf.copy()
    comid_col = next(
        (c for c in ["COMID", "comid", "FEATUREID", "featureid",
                     "NHDPlusID", "nhdplusid", "ID", "id"]
         if c in df.columns),
        None,
    )
    if comid_col is None:
        log_fn(f"  No COMID column found — writing empty {out_csv.name}")
        pd.DataFrame(columns=["feature_id"]).to_csv(out_csv, index=False)
        return out_csv

    comids = df[comid_col].dropna().astype(int).unique()
    pd.DataFrame({"feature_id": comids}).to_csv(out_csv, index=False)
    log_fn(f"  ✓ Saved {out_csv.name} ({len(comids)} feature IDs, one per line)")
    return out_csv


def _download_usgs_discharge(
    gage_ids: List[str],
    start_dt,
    end_dt,
    out_folder: Path,
    interval_hours: float = 1.0,
    log_fn=print,
) -> List[str]:
    """Download USGS instantaneous streamflow for each gage via the NWIS IV API.

    The IV service returns 15-minute data; the result is resampled to
    *interval_hours* (mean of sub-interval values).

    Saves one CSV per gage:  datetime, streamflow_m3s
    Returns list of saved file paths.
    """
    import requests

    start_str = pd.Timestamp(start_dt).strftime("%Y-%m-%d")
    end_str   = pd.Timestamp(end_dt).strftime("%Y-%m-%d")
    out_folder.mkdir(parents=True, exist_ok=True)
    saved = []

    # Build pandas offset string from interval_hours
    interval_hours = float(interval_hours) if interval_hours else 1.0
    if interval_hours < 1.0:
        # e.g. 0.25 h → "15min", 0.5 h → "30min"
        mins = int(round(interval_hours * 60))
        resample_rule = f"{mins}min"
    else:
        resample_rule = f"{int(interval_hours)}h"

    for site in gage_ids:
        log_fn(
            f"  Downloading USGS gage {site} "
            f"({start_str} → {end_str}, interval={resample_rule}) …"
        )
        url = (
            "https://waterservices.usgs.gov/nwis/iv/"
            f"?sites={site}&parameterCd=00060"
            f"&startDT={start_str}&endDT={end_str}&format=json"
        )
        try:
            resp = requests.get(url, timeout=120)
            resp.raise_for_status()
            data = resp.json()

            ts_list = data.get("value", {}).get("timeSeries", [])
            if not ts_list:
                log_fn(f"  No time series returned for gage {site}")
                continue

            values = ts_list[0].get("values", [{}])[0].get("value", [])
            if not values:
                log_fn(f"  Empty value list for gage {site}")
                continue

            rows = []
            for v in values:
                try:
                    q_cfs = float(v["value"])
                    rows.append({
                        "datetime":       pd.Timestamp(v["dateTime"]),
                        "streamflow_m3s": round(q_cfs * 0.0283168, 6),
                    })
                except (ValueError, KeyError):
                    continue

            if not rows:
                log_fn(f"  No valid readings for gage {site}")
                continue

            df = pd.DataFrame(rows).set_index("datetime")
            df.index = pd.to_datetime(df.index, utc=True)

            # Resample to requested interval (mean within each window)
            df = df.resample(resample_rule).mean().dropna()

            out_csv = next_free_path(out_folder, f"usgs_discharge_{site}", "csv")
            df.to_csv(out_csv, index_label="datetime")
            log_fn(f"  ✓ Saved {out_csv.name} ({len(df)} rows @ {resample_rule})")
            saved.append(str(out_csv))

        except Exception as ex:
            log_fn(f"  USGS download failed for gage {site}: {ex}")

    return saved


# ── Step 2: Flowline outputs ──────────────────────────────────────────────────

def run_flowline_mode(
    project_dir: str,
    features: List[AOIFeatureInfo],
    per_aoi_configs: List[Dict[str, Any]] = None,
    opts: Dict[str, Any] = None,
    log_fn=print,
) -> Dict:
    """Download NHD flowlines and save selected outputs for each AOI.

    Accepts either ``per_aoi_configs`` (one dict per AOI, from the card
    accordion) or the legacy ``opts`` dict (applied to all AOIs).
    """
    project_dir = Path(project_dir)
    if opts is None:
        opts = {}
    if per_aoi_configs is None:
        per_aoi_configs = [opts] * len(features)

    summary = {"features": []}
    n = len(features)

    for i, (f, cfg) in enumerate(zip(features, per_aoi_configs), 1):
        log_fn(f"▶ Running [{i}/{n}]: '{f.name}' …")
        # Use the AOI's existing subfolder (created during AOI confirmation).
        # Fall back to project_dir/folder_name if folder_path is not set.
        out_folder = Path(f.folder_path) if f.folder_path else (
            project_dir / f.folder_name
        )
        out_folder.mkdir(parents=True, exist_ok=True)
        feat_out = {"name": f.name, "folder": str(out_folder), "files": {}}

        save_main  = bool(cfg.get("save_main_river",    True))
        main_fmt   = cfg.get("main_format",              "shp")
        save_all   = bool(cfg.get("save_all_flowlines",  False))
        all_fmt    = cfg.get("all_format",               "shp")
        cell_size  = float(cfg.get("cell_size_m",        30.0))
        save_gages = bool(cfg.get("save_gages_csv",      True))
        save_fids  = bool(cfg.get("save_feature_ids",    True))

        clipped, main_river = lookup_nhd_flowlines_clipped(
            f.source_file, f.feature_index, log_fn=log_fn
        )

        # Always stash the raw GeoDataFrames in the summary so the map-view
        # can render regardless of which output format / checkbox the user
        # chose (CSV, TIF, or "don't save" all still produce a map).
        feat_out["_main_river_gdf"]    = main_river  # GeoDataFrame | None
        feat_out["_all_flowlines_gdf"] = clipped     # GeoDataFrame | None

        if save_main and main_river is not None and not main_river.empty:
            stem = f"main_river_{f.folder_name}"
            if main_fmt == "tif":
                aoi_gdf = get_single_feature_gdf(f.source_file, f.feature_index)
                p = _rasterize_flowlines_tif(
                    main_river, aoi_gdf, cell_size,
                    next_free_path(out_folder, stem, "tif"), log_fn,
                )
            elif main_fmt == "csv":
                p = _save_flowlines_csv(
                    main_river, next_free_path(out_folder, stem, "csv"), log_fn)
            elif main_fmt == "gpkg":
                p = _save_geopackage(
                    main_river, next_free_path(out_folder, stem, "gpkg"), log_fn)
            else:
                p = _save_shapefile(
                    main_river, next_free_path(out_folder, stem, "shp"), log_fn)
            feat_out["files"]["main_river"] = str(p)

        if save_all and clipped is not None and not clipped.empty:
            stem = f"all_flowlines_{f.folder_name}"
            if all_fmt == "tif":
                aoi_gdf = get_single_feature_gdf(f.source_file, f.feature_index)
                p = _rasterize_flowlines_tif(
                    clipped, aoi_gdf, cell_size,
                    next_free_path(out_folder, stem, "tif"), log_fn,
                )
            elif all_fmt == "csv":
                p = _save_flowlines_csv(
                    clipped, next_free_path(out_folder, stem, "csv"), log_fn)
            elif all_fmt == "gpkg":
                p = _save_geopackage(
                    clipped, next_free_path(out_folder, stem, "gpkg"), log_fn)
            else:
                p = _save_shapefile(
                    clipped, next_free_path(out_folder, stem, "shp"), log_fn)
            feat_out["files"]["all_flowlines"] = str(p)

        if save_gages:
            gages = lookup_usgs_gages(f.source_file, f.feature_index, log_fn=log_fn)
            p = _save_gages_csv(
                gages,
                next_free_path(out_folder, f"usgs_gages_{f.folder_name}", "csv"),
                log_fn,
            )
            feat_out["files"]["gages_csv"] = str(p)

        if save_fids:
            p = _save_feature_ids_csv(
                clipped,
                next_free_path(out_folder, f"feature_ids_{f.folder_name}", "csv"),
                log_fn,
            )
            feat_out["files"]["feature_ids_csv"] = str(p)

        log_fn(f"✓ Done [{i}/{n}]: {f.folder_name}/")
        summary["features"].append(feat_out)

    log_fn(f"Flowline step complete — {n} AOI(s) processed.")
    return summary


# ── Step 3: Flow Data download ────────────────────────────────────────────────

def run_flowdata_mode(
    project_dir: str,
    features: List[AOIFeatureInfo],
    per_aoi_configs: List[Dict[str, Any]] = None,
    opts: Dict[str, Any] = None,
    log_fn=print,
) -> Dict:
    """Download discharge data (NWM or USGS) and save one CSV per ID per AOI.

    opts keys:
        flow_source        : "nwm" | "usgs"
        --- NWM ---
        discharge_source   : "retrospective" | "forecast"
        feature_ids        : str / int / list / path-to-csv
        event_start_dt     : datetime (retrospective)
        event_end_dt       : datetime (retrospective)
        interval_hours     : float    (retrospective, default 1.0)
        forecast_run_date  : str YYYYMMDD (forecast, optional)
        forecast_cycle     : int 0/6/12/18 (forecast)
        forecast_set       : str (forecast)
        --- USGS ---
        gage_ids           : str / list / path-to-csv
        event_start_dt     : datetime
        event_end_dt       : datetime
    """
    project_dir = Path(project_dir)

    if opts is None:
        opts = {}
    if per_aoi_configs is None:
        per_aoi_configs = [opts] * len(features)

    summary = {"features": []}
    n = len(features)

    for i, (f, cfg) in enumerate(zip(features, per_aoi_configs), 1):
        log_fn(f"▶ Running [{i}/{n}]: '{f.name}' …")
        # Use the AOI's existing subfolder (created during AOI confirmation).
        out_folder = Path(f.folder_path) if f.folder_path else (
            project_dir / f.folder_name
        )
        out_folder.mkdir(parents=True, exist_ok=True)
        flow_source = cfg.get("flow_source", "retrospective")
        # Normalise: "nwm" → auto-pick retrospective vs forecast by event end date.
        # Honour an explicit "retrospective"/"forecast" discharge_source if present
        # (legacy configs written before the combo was merged).
        if flow_source == "nwm":
            explicit = cfg.get("discharge_source", "")
            if explicit in ("retrospective", "forecast"):
                flow_source = explicit
            else:
                from datetime import datetime as _dt
                _retro_end = _dt(2020, 12, 31, 23, 59)
                _end_dt = cfg.get("event_end_dt")
                if _end_dt and hasattr(_end_dt, "year") and _end_dt > _retro_end:
                    flow_source = "forecast"
                else:
                    flow_source = "retrospective"
        feat_out = {"name": f.name, "folder": str(out_folder),
                    "flow_source": flow_source, "files": {}}

        if flow_source in ("retrospective", "forecast"):
            fids = _coerce_feature_ids(cfg.get("feature_ids", ""))
            if not fids:
                log_fn("  No NWM feature IDs supplied — skipping.")
            else:
                src = flow_source
                tmp_csv = out_folder / f"_nwm_tmp_{f.folder_name}.csv"
                try:
                    if src == "retrospective":
                        download_nwm_retrospective(
                            fids,
                            cfg.get("event_start_dt"),
                            cfg.get("event_end_dt"),
                            float(cfg.get("interval_hours", 1.0)),
                            tmp_csv, log_fn=log_fn,
                        )
                    else:
                        download_nwm_forecast(
                            fids, tmp_csv,
                            run_date=cfg.get("forecast_run_date"),
                            cycle_hour=int(cfg.get("forecast_cycle", 0)),
                            forecast_set=cfg.get("forecast_set", "medium_range_mem1"),
                            log_fn=log_fn,
                        )
                    # Split into one CSV per feature ID
                    df_all = pd.read_csv(tmp_csv, index_col=0)
                    tag = "retro" if src == "retrospective" else "forecast"
                    for fid in fids:
                        col = str(int(fid))
                        if col not in df_all.columns:
                            continue
                        out_csv = next_free_path(
                            out_folder, f"nwm_{tag}_{col}", "csv"
                        )
                        (df_all[[col]]
                         .rename(columns={col: "streamflow_m3s"})
                         .to_csv(out_csv, index_label="datetime"))
                        log_fn(f"  ✓ Saved {out_csv.name}")
                        feat_out["files"][f"nwm_{col}"] = str(out_csv)
                    tmp_csv.unlink(missing_ok=True)
                except Exception as ex:
                    log_fn(f"  NWM download failed for '{f.name}': {ex}")
                    tmp_csv.unlink(missing_ok=True)

        elif flow_source == "usgs":
            gage_ids = _coerce_gage_ids(cfg.get("gage_ids", ""))
            if not gage_ids:
                log_fn("  No USGS gage IDs supplied — skipping.")
            else:
                saved = _download_usgs_discharge(
                    gage_ids,
                    cfg.get("event_start_dt"),
                    cfg.get("event_end_dt"),
                    out_folder,
                    interval_hours=float(cfg.get("usgs_interval_hours", 1.0)),
                    log_fn=log_fn,
                )
                for fp in saved:
                    feat_out["files"][Path(fp).stem] = fp

        log_fn(f"✓ Done [{i}/{n}]: {f.folder_name}/")
        summary["features"].append(feat_out)

    log_fn(f"Flow Data step complete — {n} AOI(s) processed.")
    return summary
