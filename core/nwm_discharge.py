"""NWM (National Water Model) discharge download for one or many feature IDs.

Two sources:
  • Retrospective v2.1 (1979–2020) — Zarr on AWS, time-resampled to a chosen interval.
  • Operational forecast — the latest medium-range run (~7 days hourly) from
    the NWM operational PDS S3 bucket.

Outputs:
  - One CSV per call (time × feature_id grid), saved to ``out_csv``.
"""
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Union

import numpy as np
import pandas as pd
import xarray as xr


RETRO_START = pd.Timestamp("1979-02-01")
RETRO_END   = pd.Timestamp("2020-12-31")
RETRO_URL   = "s3://noaa-nwm-retrospective-2-1-zarr-pds/chrtout.zarr"
FORECAST_BUCKET = "noaa-nwm-pds"


# ── helpers ───────────────────────────────────────────────────────────────────

def _coerce_feature_ids(feature_ids) -> List[int]:
    """Accept a single int/str, list of int/str, or a path to a 1-column CSV
    (no header) and return a list of unique int IDs."""
    if feature_ids is None:
        return []
    if isinstance(feature_ids, (int, np.integer)):
        return [int(feature_ids)]
    if isinstance(feature_ids, str):
        s = feature_ids.strip()
        # If it looks like a file path → load it
        if any(s.lower().endswith(ext) for ext in (".csv", ".txt")) and Path(s).exists():
            df = pd.read_csv(s, header=None)
            result = []
            for v in df.iloc[:, 0].dropna().tolist():
                try:
                    result.append(int(float(str(v).strip())))
                except (ValueError, TypeError):
                    # Skip non-numeric values (e.g. column headers like
                    # "OBJECTID" or "feature_id" if the user points to a
                    # flowlines CSV rather than the feature-IDs-only CSV).
                    continue
            return result
        # Otherwise, comma- or whitespace-separated single line of IDs
        parts = [p for p in s.replace(",", " ").split() if p]
        return [int(p) for p in parts]
    if isinstance(feature_ids, Iterable):
        return [int(x) for x in feature_ids]
    raise ValueError(f"Unsupported feature_ids type: {type(feature_ids)!r}")


def _validate_event_window(start_dt, end_dt):
    start_ts = pd.Timestamp(start_dt)
    end_ts   = pd.Timestamp(end_dt)
    if end_ts <= start_ts:
        raise ValueError("end_dt must be after start_dt.")
    return start_ts, end_ts


# ── retrospective ─────────────────────────────────────────────────────────────

def download_nwm_retrospective(
    feature_ids,
    start_dt,
    end_dt,
    interval_hours: float,
    out_csv: Union[str, Path],
    log_fn=print,
) -> Path:
    """Pull NWM v2.1 retrospective streamflow for many feature IDs and write
    one CSV with columns ``datetime`` + one column per ``feature_id``.

    Discharge units: m³/s (NWM native).
    """
    try:
        import zarr   # noqa
        import s3fs   # noqa
    except ImportError:
        raise ImportError(
            "zarr and s3fs are required for NWM retrospective download.\n"
            "Install with:  pip install zarr s3fs"
        )

    fids = _coerce_feature_ids(feature_ids)
    if not fids:
        raise ValueError("No feature IDs provided.")
    start_ts, end_ts = _validate_event_window(start_dt, end_dt)
    if start_ts < RETRO_START or end_ts > RETRO_END:
        raise ValueError(
            f"NWM v2.1 retrospective only covers {RETRO_START.date()} to "
            f"{RETRO_END.date()}.  Your window: {start_ts.date()} → {end_ts.date()}."
        )

    log_fn(f"Opening NWM retrospective Zarr store … ({len(fids)} feature_id(s))")
    ds = xr.open_zarr(RETRO_URL, consolidated=True, storage_options={"anon": True})

    fids_in_store = set(int(x) for x in ds["feature_id"].values)
    missing = [f for f in fids if f not in fids_in_store]
    if missing:
        log_fn(f"  ⚠ {len(missing)} feature_id(s) NOT in NWM v2.1: {missing[:10]}…")
    keep = [f for f in fids if f in fids_in_store]
    if not keep:
        raise RuntimeError("None of the requested feature IDs are in the NWM v2.1 store.")

    log_fn("Slicing time + feature_id …")
    da = ds["streamflow"].sel(time=slice(start_ts, end_ts)).sel(feature_id=keep)
    df = da.to_pandas()    # rows = time, cols = feature_id
    df.index.name = "datetime"
    df.columns = [str(int(c)) for c in df.columns]

    # Resample to requested interval
    target = pd.date_range(start=start_ts, end=end_ts, freq=pd.Timedelta(hours=interval_hours))
    if len(target) == 0:
        raise ValueError("interval_hours is too large for this event window.")
    df = df.reindex(df.index.union(target)).sort_index().interpolate(method="time")
    df = df.reindex(target)

    # Add columns for any missing ID with NaN values, in original order
    for f in fids:
        col = str(int(f))
        if col not in df.columns:
            df[col] = np.nan
    df = df[[str(int(f)) for f in fids]]

    out_csv = Path(out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_csv, index_label="datetime")
    log_fn(f"  ✓ Wrote {out_csv.name} ({len(df)} rows × {len(fids)} columns)")
    return out_csv


# ── forecast (operational) ────────────────────────────────────────────────────

def latest_forecast_run() -> str:
    """Return the latest available medium-range run as ``YYYYMMDD`` (UTC).

    NWM publishes 4 medium-range cycles a day (00, 06, 12, 18 UTC).  We use
    today's UTC date and let the caller pick a cycle.  Most users only care
    about the most recent run, so default to the previous day if today is not
    yet populated.
    """
    return pd.Timestamp.utcnow().strftime("%Y%m%d")


def download_nwm_forecast(
    feature_ids,
    out_csv: Union[str, Path],
    run_date: Optional[str] = None,
    cycle_hour: int = 0,
    forecast_set: str = "medium_range_mem1",
    max_hours: int = 240,
    log_fn=print,
) -> Path:
    """Download NWM operational forecast streamflow for the given feature_ids.

    Parameters
    ----------
    feature_ids : int / str / list / path-to-csv
        See _coerce_feature_ids.
    out_csv : str | Path
    run_date : str ``YYYYMMDD``, default = today's UTC date.
    cycle_hour : int (0/6/12/18), default 0.
    forecast_set : "short_range" | "medium_range_mem1" | "long_range_mem1" …
        See https://registry.opendata.aws/nwm-archive/ — most useful are
        "medium_range_mem1" (~10 days hourly) and "short_range" (~18 hours).
    max_hours : int — cap how far ahead to read.

    Discharge units: m³/s.
    """
    try:
        import s3fs
    except ImportError:
        raise ImportError(
            "s3fs is required for NWM forecast download.  pip install s3fs"
        )

    fids = _coerce_feature_ids(feature_ids)
    if not fids:
        raise ValueError("No feature IDs provided.")
    run_date = run_date or latest_forecast_run()

    fs = s3fs.S3FileSystem(anon=True)
    prefix = f"{FORECAST_BUCKET}/nwm.{run_date}/{forecast_set}/"
    log_fn(f"Listing NWM forecast files at s3://{prefix} …")
    try:
        files = fs.ls(prefix)
    except FileNotFoundError:
        raise RuntimeError(
            f"No NWM forecast at s3://{prefix}.  "
            f"Try a previous run_date / cycle_hour."
        )

    # Filter to the cycle and the CHRTOUT (channel) files.  Filenames look like:
    #   nwm.t00z.medium_range.channel_rt_1.f001.conus.nc   (mem1 only)
    cycle_tag = f".t{cycle_hour:02d}z."
    chrt_files = sorted(
        f for f in files
        if cycle_tag in f and "channel_rt" in f and f.endswith(".nc")
    )
    if not chrt_files:
        raise RuntimeError(
            f"No CHRTOUT files for {run_date} cycle {cycle_hour:02d}z in "
            f"forecast set {forecast_set!r}."
        )

    log_fn(f"  {len(chrt_files)} CHRTOUT files found; reading first {max_hours} h …")
    rows = []
    for f in chrt_files[:max_hours]:
        with fs.open(f, "rb") as fh, xr.open_dataset(fh, engine="h5netcdf") as ds:
            t = pd.Timestamp(ds["time"].values[0])
            sub = ds["streamflow"].sel(feature_id=fids, drop=False)
            rows.append((t, sub.values.copy()))

    if not rows:
        raise RuntimeError("No forecast hours could be read.")

    times = [t for t, _ in rows]
    arr = np.vstack([v for _, v in rows]).astype("float32")
    df = pd.DataFrame(arr, columns=[str(int(f)) for f in fids], index=pd.DatetimeIndex(times))
    df.index.name = "datetime"

    out_csv = Path(out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_csv, index_label="datetime")
    log_fn(f"  ✓ Wrote {out_csv.name} ({len(df)} rows × {len(fids)} columns)")
    return out_csv
