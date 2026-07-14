"""ARC step 6 — build the flow file that ARC uses to construct rating curves.

ARC's Process_ARC_Geospatial_Data needs a per-reach flow table keyed by the
stream id (COMID) with a baseflow column and a max-flow column:

    COMID,base,max
    22226835,12.4,318.7
    ...

NWM's ``feature_id`` is the NHDPlus v2 COMID, so every reach in the flowline
maps 1:1 to an NWM series.  We pull NWM discharge for all reaches over the
chosen window and reduce each reach to:

    base = a low-flow percentile (baseflow / channel-forming discharge)
    max  = the peak discharge (the flood to map)

GEOGLOWS is intentionally not supported here — NWM only.
"""
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd


# Candidate column names (case-insensitive) that hold the NHDPlus COMID.
_ID_CANDIDATES = ("comid", "featureid", "feature_id", "nhdplusid", "permanent_")


def extract_comids(flowline_path) -> list:
    """Return the sorted unique integer COMIDs from a flowline vector."""
    gdf = gpd.read_file(flowline_path)
    lower = {c.lower(): c for c in gdf.columns}
    col = next((lower[k] for k in _ID_CANDIDATES if k in lower), None)
    if col is None:
        raise RuntimeError(
            f"No COMID/feature-id column found in {Path(flowline_path).name} "
            f"(columns: {list(gdf.columns)}).")
    ids = []
    for v in gdf[col].dropna().tolist():
        try:
            ids.append(int(float(v)))
        except (TypeError, ValueError):
            continue
    return sorted(set(ids))


def _base_max_from_grid(df: pd.DataFrame, base_percentile: float) -> pd.DataFrame:
    """Reduce a (time × feature_id) discharge grid to per-COMID base + max.

    ``df`` has a datetime index and one column per feature_id (column labels
    are the COMID as str or int).  Returns a DataFrame [COMID, base, max].
    """
    rows = []
    for col in df.columns:
        s = pd.to_numeric(df[col], errors="coerce").dropna()
        if s.empty:
            continue
        try:
            comid = int(float(col))
        except (TypeError, ValueError):
            continue
        base = float(np.nanpercentile(s.values, base_percentile))
        mx = float(s.max())
        # Guard: ARC needs max strictly above base to build a curve range.
        if not (mx > base):
            mx = base + max(base * 0.05, 0.01)
        rows.append((comid, round(base, 4), round(mx, 4)))
    return pd.DataFrame(rows, columns=["COMID", "base", "max"])


def build_arc_flow_file(
    flowline_path,
    out_csv,
    *,
    source: str = "nwm_retro",          # "nwm_retro" | "nwm_forecast"
    start_dt=None,
    end_dt=None,
    forecast_date=None,
    forecast_range: str = "medium_range",
    forecast_hour=None,
    base_percentile: float = 10.0,
    interval_hours: float = 1.0,
    log_fn=print,
) -> dict:
    """Build ``out_csv`` = COMID,base,max for every reach in ``flowline_path``.

    Returns ``{"flow_csv", "n_reaches", "id_field", "max_field", "base_field"}``.
    """
    out_csv = Path(out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    comids = extract_comids(flowline_path)
    if not comids:
        raise RuntimeError("No COMIDs found in the flowline — run step 5 first.")
    log_fn(f"Building ARC flow file for {len(comids)} reach(es) via {source} …")

    grid_csv = out_csv.parent / "_nwm_grid.csv"
    if source == "nwm_retro":
        from core.nwm_discharge import download_nwm_retrospective
        if start_dt is None or end_dt is None:
            raise RuntimeError("NWM retrospective needs a start and end date.")
        download_nwm_retrospective(
            comids, start_dt, end_dt, interval_hours, grid_csv, log_fn=log_fn)
    elif source == "nwm_forecast":
        from core.nwm_forecast import get_nwm_forecast_multi
        if forecast_date is None:
            raise RuntimeError("NWM forecast needs an issue date.")
        get_nwm_forecast_multi(
            comids, forecast_date, forecast_range=forecast_range,
            cycle_hour=forecast_hour, out_csv=str(grid_csv), log_fn=log_fn)
    else:
        raise RuntimeError(f"Unknown source '{source}' (NWM only).")

    grid = pd.read_csv(grid_csv)
    # First column is the datetime index; the rest are feature_id columns.
    dt_col = grid.columns[0]
    grid = grid.set_index(dt_col)
    table = _base_max_from_grid(grid, base_percentile)
    if table.empty:
        raise RuntimeError(
            "NWM returned no usable discharge for these reaches / this window.")

    table.to_csv(out_csv, index=False)
    try:
        grid_csv.unlink()
    except Exception:
        pass
    log_fn(f"  ✓ Wrote {out_csv.name} — {len(table)} reach(es) "
           f"(base = p{base_percentile:g}, max = peak)")
    return {
        "flow_csv":   str(out_csv),
        "n_reaches":  int(len(table)),
        "id_field":   "COMID",
        "max_field":  "max",
        "base_field": "base",
    }
