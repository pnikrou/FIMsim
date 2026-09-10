"""NWM discharge for ARC-Curve2Flood, from the same public sources FIMserv uses.

NenCarta's own NWM path calls a CIROH-gated API and refuses to run without
``nwm_api_key``.  The DATA, though, is open — FIMserv (OWP HAND-FIM) fetches it
with no credentials at all:

    retrospective : s3://noaa-nwm-retrospective-3-0-pds/CONUS/zarr/chrtout.zarr
                    (anonymous; hourly, 1979-02-01 → 2023-02-01,
                     2,776,734 feature_ids, variable "streamflow")
    forecast      : https://storage.googleapis.com/national-water-model
                    (public GCS mirror, the same one fimserve reads)

So FIMsim fetches the discharge itself and hands NenCarta finished flow files
through its own ``user_flow_files`` input, which keeps the *event* discharge off
the CIROH API — whose forecast endpoint returns "Internal Server Error" for
dates the public mirror serves perfectly well.

That does NOT remove the key.  NenCarta validates ``nwm_api_key`` for any NWM
source before it reads ``floodmap_mode`` at all, and its bathymetry step asks
nwm-api.ciroh.org for the rp2/rp100 return periods it sizes channels with.  An
NWM run still needs a working key; see ``core/api_keys.py``.

``feature_id`` in NWM IS the NHD COMID, so the flowline for these runs must be
the NHDPlus one (NenCarta reads COMID/TOCOMID for NWM, LINKNO/DSLINKNO for
GEOGLOWS).
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Dict, List, Optional, Sequence

RETRO_ZARR = "s3://noaa-nwm-retrospective-3-0-pds/CONUS/zarr/chrtout.zarr"
FORECAST_BASE = "https://storage.googleapis.com/national-water-model"

# Verified against the stores themselves.
RETRO_START = _dt.datetime(1979, 2, 1, 1)
RETRO_END   = _dt.datetime(2023, 2, 1, 0)
FORECAST_START = _dt.datetime(2018, 9, 17)

# Forecast cycles each range publishes, and how far ahead it reaches (hours).
RANGES = {
    "short_range":  {"cycles": list(range(24)), "max_ahead": 18},
    "medium_range": {"cycles": [0, 6, 12, 18],  "max_ahead": 240},
    "long_range":   {"cycles": [0, 6, 12, 18],  "max_ahead": 720},
}


def covers_retrospective(when: _dt.datetime) -> bool:
    return RETRO_START <= when <= RETRO_END


def fetch_retrospective(comids: Sequence[int], timestamps: Sequence[_dt.datetime],
                        log_fn=print) -> Dict[_dt.datetime, Dict[int, float]]:
    """Hourly NWM retrospective discharge, straight from the public zarr."""
    import xarray as xr
    import numpy as np

    if not comids or not timestamps:
        return {}
    lo, hi = min(timestamps), max(timestamps)
    log_fn(f"Reading NWM retrospective for {len(comids)} reach(es), "
           f"{lo:%Y-%m-%d %H:%M} → {hi:%Y-%m-%d %H:%M} …")
    ds = xr.open_zarr(RETRO_ZARR, storage_options={"anon": True})

    want = [int(c) for c in comids]
    have = np.isin(want, ds.feature_id.values)
    ids = [c for c, ok in zip(want, have) if ok]
    if len(ids) < len(want):
        log_fn(f"  ⚠ {len(want) - len(ids)} reach(es) are not in the NWM network.")
    if not ids:
        raise ValueError("None of the flowline's COMIDs exist in the NWM network.")

    sub = ds["streamflow"].sel(
        feature_id=ids,
        time=slice(lo.strftime("%Y-%m-%d %H:00:00"),
                   (hi + _dt.timedelta(hours=1)).strftime("%Y-%m-%d %H:00:00")))
    df = sub.to_dataframe().reset_index()
    df["_key"] = df["time"].dt.floor("h")

    out: Dict[_dt.datetime, Dict[int, float]] = {}
    for t in timestamps:
        key = t.replace(minute=0, second=0, microsecond=0)
        rows = df[df["_key"] == key]
        if rows.empty:
            log_fn(f"  ⚠ no NWM data for {key:%Y-%m-%d %H:%M} — skipping.")
            continue
        out[t] = {int(f): float(q) for f, q in
                  zip(rows["feature_id"], rows["streamflow"])
                  if not np.isnan(q)}
    log_fn(f"  got discharge for {len(out)} of {len(timestamps)} timestep(s).")
    return out


def _forecast_url(date: _dt.date, cycle: int, step: int,
                  frange: str = "short_range") -> str:
    tag = {"short_range": "short_range", "medium_range": "medium_range",
           "long_range": "long_range"}[frange]
    mem = "_1" if frange in ("medium_range", "long_range") else ""
    return (f"{FORECAST_BASE}/nwm.{date:%Y%m%d}/{tag}/"
            f"nwm.t{cycle:02d}z.{tag}{mem}.channel_rt.f{step:03d}.conus.nc")


def fetch_forecast(comids: Sequence[int], valid_times: Sequence[_dt.datetime],
                   cycle_date: Optional[_dt.date] = None,
                   cycle_hour: Optional[int] = None,
                   frange: str = "short_range",
                   log_fn=print) -> Dict[_dt.datetime, Dict[int, float]]:
    """NWM forecast discharge at the requested VALID times.

    A forecast file is identified by its cycle (when the model ran) plus a lead
    step; the valid time is cycle + step.  Given the times you want, this picks
    the cycle that covers them and downloads exactly those steps.
    """
    import numpy as np
    import requests
    import tempfile
    import xarray as xr

    if not comids or not valid_times:
        return {}
    want = sorted(set(t.replace(minute=0, second=0, microsecond=0)
                      for t in valid_times))
    spec = RANGES.get(frange, RANGES["short_range"])

    if cycle_date is None or cycle_hour is None:
        # Pick the latest cycle that runs STRICTLY BEFORE the first valid time.
        # A forecast is never valid at its own cycle hour — the first lead step
        # is f001 — so a cycle equal to the valid hour yields f000 and nothing
        # to read.
        first = want[0]
        earlier = [h for h in spec["cycles"] if h < first.hour]
        if earlier:
            cycle_date, cycle_hour = first.date(), max(earlier)
        else:
            # Valid time is early in the day: use the previous day's last cycle.
            prev = first.date() - _dt.timedelta(days=1)
            cycle_date, cycle_hour = prev, max(spec["cycles"])
    else:
        # An explicit cycle that cannot reach the valid time is a mistake worth
        # correcting rather than failing on: fall back to auto-selection.
        cyc_dt = _dt.datetime.combine(cycle_date, _dt.time(int(cycle_hour)))
        if (want[0] - cyc_dt).total_seconds() // 3600 < 1:
            log_fn(f"  cycle t{int(cycle_hour):02d}z is not before "
                   f"{want[0]:%H:%M} — choosing an earlier cycle instead.")
            earlier = [h for h in spec["cycles"] if h < want[0].hour]
            if earlier:
                cycle_date, cycle_hour = want[0].date(), max(earlier)
            else:
                cycle_date = want[0].date() - _dt.timedelta(days=1)
                cycle_hour = max(spec["cycles"])
    cycle_dt = _dt.datetime.combine(cycle_date, _dt.time(int(cycle_hour)))
    log_fn(f"NWM {frange} cycle {cycle_dt:%Y-%m-%d} t{int(cycle_hour):02d}z")

    ids = np.array([int(c) for c in comids])
    out: Dict[_dt.datetime, Dict[int, float]] = {}
    for t in want:
        step = int((t - cycle_dt).total_seconds() // 3600)
        if step < 1 or step > spec["max_ahead"]:
            log_fn(f"  ⚠ {t:%Y-%m-%d %H:%M} is f{step:03d}, outside "
                   f"{frange} (f001–f{spec['max_ahead']:03d}) — skipping.")
            continue
        url = _forecast_url(cycle_date, int(cycle_hour), step, frange)
        try:
            r = requests.get(url, timeout=120)
            if r.status_code != 200:
                log_fn(f"  ⚠ {Path(url).name}: HTTP {r.status_code} — skipping.")
                continue
            with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as fh:
                fh.write(r.content)
                tmp = fh.name
            with xr.open_dataset(tmp) as nc:
                fid = nc["feature_id"].values
                q = nc["streamflow"].values
                idx = np.isin(fid, ids)
                out[t] = {int(f): float(v) for f, v in zip(fid[idx], q[idx])
                          if not np.isnan(v)}
            Path(tmp).unlink(missing_ok=True)
            log_fn(f"  f{step:03d} → {t:%Y-%m-%d %H:%M}: "
                   f"{len(out[t])} reach(es)")
        except Exception as exc:
            log_fn(f"  ⚠ {t:%Y-%m-%d %H:%M}: {type(exc).__name__}: {exc}")
    return out


def fetch_nwm(comids: Sequence[int], timestamps: Sequence[_dt.datetime],
              frange: str = "short_range", cycle_date=None, cycle_hour=None,
              record: str = "auto",
              log_fn=print) -> Dict[_dt.datetime, Dict[int, float]]:
    """NWM discharge from the record the caller asks for.

    ``record`` is "retrospective", "forecast", or "auto".  The two overlap
    between 2018-09-17 and 2023-02-01, and they are NOT the same quantity — the
    retrospective is a reanalysis of what the model says happened, a forecast is
    what it predicted beforehand — so in that window the choice belongs to the
    caller rather than to a silent default.
    """
    if not timestamps:
        return {}
    want = str(record or "auto").lower()

    if want == "retrospective":
        bad = [t for t in timestamps if not covers_retrospective(t)]
        if bad:
            raise ValueError(
                f"{bad[0]:%Y-%m-%d %H:%M} is outside the NWM retrospective "
                f"({RETRO_START:%Y-%m-%d} → {RETRO_END:%Y-%m-%d}). "
                "Choose Forecast for this date.")
        return fetch_retrospective(comids, timestamps, log_fn=log_fn)

    if want == "forecast":
        early = [t for t in timestamps if t < FORECAST_START]
        if early:
            raise ValueError(
                f"{early[0]:%Y-%m-%d %H:%M} predates the NWM forecast archive "
                f"(starts {FORECAST_START:%Y-%m-%d}). "
                "Choose Retrospective for this date.")
        return fetch_forecast(comids, timestamps, cycle_date=cycle_date,
                              cycle_hour=cycle_hour, frange=frange, log_fn=log_fn)

    # auto — prefer the retrospective where it exists, and say so.
    if all(covers_retrospective(t) for t in timestamps):
        log_fn("Both records cover this date; using the RETROSPECTIVE "
               "(reanalysis).  Select Forecast explicitly to use that instead.")
        return fetch_retrospective(comids, timestamps, log_fn=log_fn)
    if any(covers_retrospective(t) for t in timestamps):
        log_fn("⚠ The period straddles the end of the NWM retrospective "
               f"({RETRO_END:%Y-%m-%d}) — using the forecast for all of it.")
    return fetch_forecast(comids, timestamps, cycle_date=cycle_date,
                          cycle_hour=cycle_hour, frange=frange, log_fn=log_fn)
