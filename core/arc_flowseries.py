"""Build a per-timestep flow series for ARC-Curve2Flood.

ARC-Curve2Flood is steady state: ARC fits a rating curve per reach and
Curve2Flood turns ONE discharge per reach into ONE map.  A "duration" is
therefore not a simulation marching through time — it is a set of independent
snapshots, one per timestep.

NenCarta already supports exactly that, via its own inputs::

    "floodmap_mode": "user",
    "user_flow_files": [ ...one CSV per timestep... ]

``run_user_floodmaps()`` iterates those files and writes one flood raster for
each, reusing the ARC rating curves (the expensive part) built once.

The CSV shape matters.  Curve2Flood reads the flow file positionally —
``pd.read_csv(FlowFileName, usecols=[0, flow_event_num + 1])`` — and treats
EVERY column after the id as another ensemble member::

    num_flows = pd.read_csv(FlowFileName, nrows=0).shape[1] - 1

With more than one flow column it accumulates them into a single
percent-of-ensemble raster instead of separate maps.  So each per-timestep file
here has exactly TWO columns, ``rivid,flow``, giving one deterministic map per
timestep.

Discharge comes from the same GEOGLOWS store NenCarta itself falls back to,
``s3://geoglows-v2/retrospective/daily.zarr`` (daily, 1940 → present).
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Dict, List, Optional, Sequence

RETRO_DAILY_URI  = "s3://geoglows-v2/retrospective/daily.zarr"
# GEOGLOWS also publishes an HOURLY retrospective covering the same period
# (1940-01-01 07:00 -> present, 759,785 steps).  NenCarta's own fallback only
# reads the daily store, so a historic request there can only ever be a daily
# mean — the hour is ignored.  Driving user_flow_files from this store instead
# gives a genuine sub-daily map without needing an NWM API key.
RETRO_HOURLY_URI = "s3://geoglows-v2/retrospective/hourly.zarr"


# What each source actually covers.  Verified against the stores themselves,
# not from documentation: the GEOGLOWS forecast bucket was listed (800 zarr
# dates), and both retrospective zarrs were opened and their time axes read.
COVERAGE = {
    "GEOGLOWS": {
        "forecast":      ("2024-07-01", "today"),
        "retro_daily":   ("1940-01-01", "2026-09-02"),
        "retro_hourly":  ("1940-01-01", "2026-09-03"),
        "note": ("Dates before 2024-07-01 have no forecast — the retrospective "
                 "record is used automatically.  Hourly detail needs the hourly "
                 "store (Duration mode); the automatic fallback is DAILY."),
    },
    "NWM": {
        "forecast":      ("2018-09-17", "today"),
        "retro_hourly":  ("1979-02-01", "2023-02-01"),
        "note": ("No API key needed — FIMsim reads the same public sources "
                 "FIMserv does (NOAA retrospective zarr, Google NWM mirror) "
                 "and hands NenCarta finished flow files.  Retrospective ENDS "
                 "2023-02-01, so later dates use a forecast.  Needs the "
                 "NHDPlus flowline (COMID)."),
    },
}


def coverage_text(source: str, html: bool = True) -> str:
    """A short, honest statement of what the chosen source covers."""
    key = "NWM" if str(source).upper().startswith("NWM") else "GEOGLOWS"
    c = COVERAGE[key]
    b = (lambda t: f"<b>{t}</b>") if html else (lambda t: t)
    lines = [f"{b(key)} coverage:"]
    if "forecast" in c:
        lines.append(f"  • Forecast: {c['forecast'][0]} → {c['forecast'][1]}")
    if "retro_hourly" in c:
        lo, hi = c["retro_hourly"]
        lines.append(f"  • Retrospective (hourly): {lo} → {hi}")
    if "retro_daily" in c:
        lo, hi = c["retro_daily"]
        lines.append(f"  • Retrospective (daily): {lo} → {hi}")
    lines.append("  " + c["note"])
    return ("<br>".join(lines) if html else "\n".join(lines))


def which_source_for(source: str, when) -> str:
    """Say which record a given date will actually come from."""
    d = _as_dt(when)
    if d is None:
        return ""
    key = "NWM" if str(source).upper().startswith("NWM") else "GEOGLOWS"
    ds = d.strftime("%Y-%m-%d")
    if key == "GEOGLOWS":
        if ds < "2024-07-01":
            return (f"{ds} predates the GEOGLOWS forecast archive "
                    f"(starts 2024-07-01) → the RETROSPECTIVE record is used.")
        return f"{ds} is in the GEOGLOWS forecast archive."
    if ds <= "2023-01-31":
        return f"{ds} is in the NWM retrospective (1979-02-01 → 2023-01-31)."
    if ds >= "2018-09-17":
        return f"{ds} is past the NWM retrospective → a FORECAST is used."
    return f"{ds} predates the NWM forecast archive (starts 2018-09-17)."


def expand_timesteps(start, end, step_hours: int = 24) -> List[_dt.datetime]:
    """Timestamps from ``start`` to ``end`` inclusive at ``step_hours`` spacing."""
    a, b = _as_dt(start), _as_dt(end)
    if a is None or b is None or b < a:
        return []
    step = max(1, int(step_hours))
    out, t = [], a
    while t <= b:
        out.append(t)
        t += _dt.timedelta(hours=step)
    return out


def _as_dt(v) -> Optional[_dt.datetime]:
    if isinstance(v, _dt.datetime):
        return v
    if isinstance(v, _dt.date):
        return _dt.datetime(v.year, v.month, v.day)
    text = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y%m%d"):
        try:
            return _dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def reach_ids_from_flowline(flowline_path: str, id_field: str = "LINKNO") -> List[int]:
    """The reach ids NenCarta will key the flow file on."""
    import geopandas as gpd
    gdf = gpd.read_file(flowline_path)
    col = next((c for c in gdf.columns if c.upper() == id_field.upper()), None)
    if col is None:
        raise ValueError(
            f"Flowline {Path(flowline_path).name} has no '{id_field}' column "
            f"— columns: {[c for c in gdf.columns if c != 'geometry']}")
    return [int(v) for v in gdf[col].dropna().unique()]


def fetch_geoglows(reach_ids: Sequence[int], timestamps: Sequence[_dt.datetime],
                   hourly: bool = True, log_fn=print
                   ) -> Dict[_dt.datetime, Dict[int, float]]:
    """GEOGLOWS discharge for these reaches at these timestamps.

    ``hourly=True`` reads the hourly retrospective and matches each timestamp
    exactly, so 16:00 really is 16:00.  ``hourly=False`` reads the daily store,
    where every hour of a day carries that day's mean — which is what NenCarta
    itself falls back to.

    One request covers the whole window and is then sliced per timestep; the
    store is a zarr, so asking per timestep would re-open it every time.
    """
    import xarray as xr
    import numpy as np

    if not reach_ids or not timestamps:
        return {}
    lo, hi = min(timestamps), max(timestamps)
    log_fn(f"Reading GEOGLOWS daily discharge for {len(reach_ids)} reach(es), "
           f"{lo:%Y-%m-%d} → {hi:%Y-%m-%d} …")
    ds = xr.open_zarr(RETRO_HOURLY_URI if hourly else RETRO_DAILY_URI,
                      storage_options={"anon": True})
    have = set(int(v) for v in ds.river_id.values.tolist()) \
        if ds.river_id.size < 2_000_000 else None
    ids = [r for r in reach_ids if have is None or r in have]
    missing = len(reach_ids) - len(ids)
    if missing:
        log_fn(f"  ⚠ {missing} reach(es) are not in the GEOGLOWS retrospective store.")
    if not ids:
        raise ValueError("None of the flowline's reaches exist in GEOGLOWS.")

    sub = ds["Q"].sel(river_id=ids,
                      time=slice(lo.strftime("%Y-%m-%d %H:00:00"),
                                 (hi + _dt.timedelta(days=1)).strftime("%Y-%m-%d")))
    df = sub.to_dataframe().reset_index()
    df["_key"] = df["time"].dt.floor("h" if hourly else "D")

    out: Dict[_dt.datetime, Dict[int, float]] = {}
    for t in timestamps:
        key = (t.replace(minute=0, second=0, microsecond=0) if hourly
               else _dt.datetime(t.year, t.month, t.day))
        rows = df[df["_key"] == key]
        if rows.empty:
            log_fn(f"  ⚠ no GEOGLOWS data for {key:%Y-%m-%d %H:%M} — skipping.")
            continue
        out[t] = {int(r): float(q) for r, q in
                  zip(rows["river_id"], rows["Q"]) if not np.isnan(q)}
    log_fn(f"  got {'hourly' if hourly else 'daily'} discharge for "
           f"{len(out)} of {len(timestamps)} timestep(s).")
    return out


# Backwards-compatible name.
def fetch_geoglows_daily(reach_ids, timestamps, log_fn=print):
    return fetch_geoglows(reach_ids, timestamps, hourly=False, log_fn=log_fn)


def write_flow_series(series: Dict[_dt.datetime, Dict[int, float]], out_dir,
                      id_header: str = "rivid", log_fn=print) -> List[str]:
    """One ``rivid,flow`` CSV per timestep.  Returns the paths, in time order.

    Exactly two columns on purpose: Curve2Flood treats every column after the
    id as another ensemble member and would merge them into a single
    percent-of-ensemble raster rather than one map per timestep.
    """
    import csv
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    paths = []
    for t in sorted(series):
        flows = series[t]
        if not flows:
            log_fn(f"  ⚠ {t:%Y-%m-%d %H:%M} has no discharge — skipped.")
            continue
        p = d / f"flow_{t:%Y%m%d_%H%M}.csv"
        with open(p, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow([id_header, "flow"])
            for rid in sorted(flows):
                w.writerow([rid, round(flows[rid], 4)])
        paths.append(str(p))
    log_fn(f"Wrote {len(paths)} per-timestep flow file(s) in {d}")
    return paths


def build_flow_series(flowline_path: str, start, end, step_hours: int,
                      out_dir, id_field: str = "LINKNO", hourly: bool = True,
                      source: str = "GEOGLOWS", frange: str = "short_range",
                      cycle_date=None, cycle_hour=None,
                      log_fn=print) -> List[str]:
    """Flowline + window -> one flow CSV per timestep.  Returns their paths.

    ``hourly`` picks the GEOGLOWS store: the hourly retrospective (so a
    requested hour is that hour) or the daily one (a daily mean).  A single
    timestep is simply a window whose start and end are the same instant.
    """
    steps = expand_timesteps(start, end, step_hours)
    if not steps:
        raise ValueError("The period is empty — check the start and end dates.")
    is_nwm = str(source).upper().startswith("NWM")
    # NWM keys on the NHD COMID, GEOGLOWS on its own LINKNO.
    if is_nwm and id_field.upper() == "LINKNO":
        id_field = "COMID"
    ids = reach_ids_from_flowline(flowline_path, id_field=id_field)
    log_fn(f"{len(steps)} timestep(s) over {len(ids)} reach(es), "
           + (f"NWM ({frange})." if is_nwm
              else f"{'hourly' if hourly else 'daily'} GEOGLOWS."))
    if is_nwm:
        from core.nwm_flows import fetch_nwm
        series = fetch_nwm(ids, steps, frange=frange, cycle_date=cycle_date,
                           cycle_hour=cycle_hour, log_fn=log_fn)
        header = "COMID"
    else:
        series = fetch_geoglows(ids, steps, hourly=hourly, log_fn=log_fn)
        header = "rivid"
    return write_flow_series(series, out_dir, id_header=header, log_fn=log_fn)
