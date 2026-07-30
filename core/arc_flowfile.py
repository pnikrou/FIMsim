"""ARC step 6 — build the flow file that ARC uses to construct rating curves.

ARC's Process_ARC_Geospatial_Data needs a per-reach flow table keyed by the
stream id (COMID) with a baseflow column and a max-flow column:

    COMID,base,max
    22226835,12.4,318.7
    ...

NWM's ``feature_id`` is the NHDPlus v2 COMID, so every reach in the flowline
maps 1:1 to an NWM series.  We pull NWM discharge for all reaches over the
chosen window and reduce each reach to:

    base = a percentile of the series (default 50 = the median — ARC's own
           streamflow tool uses the median of the retrospective record as
           baseflow)
    max  = the peak discharge (the flood to map; ARC's tool likewise uses
           the record maximum)

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


# ── Long-term baseflow ────────────────────────────────────────────────────────
#
# ARC uses the baseflow column as the channel-forming (bankfull) discharge: it
# carves the unseen channel bathymetry deep enough to convey that flow before
# any water goes overbank.  So baseflow MUST be a long-term climatological
# statistic, not a statistic of the flood window.
#
# Taking the median *inside* a flood window inflates baseflow enormously (on
# real AOIs we measured 169 m3/s instead of the true 19 m3/s, and 733 instead
# of 249).  ARC then over-carves the channel and the flood stays inside it,
# producing far too little inundation.
#
# ARC's own tool (arc.streamflow_processing.GetMedianFlowValues) takes the
# median over the multi-year retrospective record — that is what we replicate.

BASEFLOW_YEARS = 10          # length of the climatology window
_BASEFLOW_STEP_H = 24.0      # daily sampling is plenty for a median


def longterm_baseflow(comids, *, percentile: float = 50.0,
                      years: int = BASEFLOW_YEARS, end_dt=None,
                      work_dir=None, log_fn=print) -> dict:
    """Median (climatological) discharge per COMID from the NWM retrospective.

    Returns ``{comid: baseflow_m3s}``.  Sampled daily over ``years`` ending at
    ``end_dt`` (clamped to the retrospective coverage).
    """
    from core.nwm_discharge import download_nwm_retrospective, RETRO_START, RETRO_END

    end_ts = pd.Timestamp(end_dt) if end_dt is not None else pd.Timestamp(RETRO_END)
    if end_ts > pd.Timestamp(RETRO_END):
        end_ts = pd.Timestamp(RETRO_END)
    start_ts = end_ts - pd.DateOffset(years=years)
    if start_ts < pd.Timestamp(RETRO_START):
        start_ts = pd.Timestamp(RETRO_START)

    log_fn(f"Baseflow: NWM retrospective climatology "
           f"{start_ts.date()} → {end_ts.date()} (daily) …")
    work_dir = Path(work_dir) if work_dir else Path(".")
    tmp = work_dir / "_nwm_baseflow.csv"
    download_nwm_retrospective(
        comids, start_ts, end_ts, _BASEFLOW_STEP_H, tmp, log_fn=lambda *_a: None)
    grid = pd.read_csv(tmp).set_index(pd.read_csv(tmp).columns[0])
    out = {}
    for col in grid.columns:
        s = pd.to_numeric(grid[col], errors="coerce").dropna()
        if s.empty:
            continue
        try:
            out[int(float(col))] = float(np.nanpercentile(s.values, percentile))
        except (TypeError, ValueError):
            continue
    try:
        tmp.unlink()
    except Exception:
        pass
    log_fn(f"  ✓ Long-term baseflow for {len(out)} reach(es)")
    return out


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
    base_percentile: float = 50.0,
    baseflow_mode: str = "longterm",     # "longterm" | "window"
    baseflow_years: int = BASEFLOW_YEARS,
    interval_hours: float = 1.0,
    log_fn=print,
) -> dict:
    """Build ``out_csv`` = COMID,base,max for every reach in ``flowline_path``.

    ``max`` always comes from the selected event / forecast window.  ``base``
    (ARC's channel-forming discharge) comes from a multi-year NWM retrospective
    climatology when ``baseflow_mode="longterm"`` — the ARC convention.  Using
    "window" reproduces the old behaviour and will over-carve the channel,
    under-predicting inundation.

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

    # Replace the window-derived baseflow with the long-term climatology —
    # ARC carves the channel to convey `base`, so it must not be a flood-window
    # statistic (see longterm_baseflow docstring).
    if baseflow_mode == "longterm":
        try:
            end_for_base = end_dt if source == "nwm_retro" else None
            lt = longterm_baseflow(
                comids, percentile=base_percentile, years=baseflow_years,
                end_dt=end_for_base, work_dir=out_csv.parent, log_fn=log_fn)
            if lt:
                old = table["base"].median()
                table["base"] = [
                    round(float(lt.get(int(c), b)), 4)
                    for c, b in zip(table["COMID"], table["base"])
                ]
                # ARC needs max strictly above base.
                table["max"] = np.where(
                    table["max"] > table["base"], table["max"],
                    table["base"] + np.maximum(table["base"] * 0.05, 0.01))
                log_fn(f"  Baseflow: window median {old:.2f} → "
                       f"long-term {table['base'].median():.2f} m³/s (median reach)")
        except Exception as exc:
            log_fn(f"  ⚠ Long-term baseflow unavailable ({exc}); "
                   "falling back to the event-window median. Inundation may be "
                   "under-predicted.")

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
