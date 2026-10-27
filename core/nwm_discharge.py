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
RETRO_END   = pd.Timestamp("2023-01-31")
# NWM v3.0 retrospective read over plain HTTPS (fsspec http) — the anonymous-S3
# client is broken by a botocore/aiobotocore/s3fs version clash in this env.
RETRO_URL   = (
    "https://noaa-nwm-retrospective-3-0-pds.s3.amazonaws.com/CONUS/zarr/chrtout.zarr"
)
FORECAST_BUCKET = "noaa-nwm-pds"


# ── helpers ───────────────────────────────────────────────────────────────────

# Column names that hold a reach id, in the order we prefer them.
_ID_COLUMNS = ("feature_id", "featureid", "comid", "nhdplusid", "station_id",
               "reach_id", "linkno", "id")

# An NWM feature_id is a large integer.  Row numbers are not, and a column of
# them is the classic way a CSV goes wrong silently.
_MIN_PLAUSIBLE_ID = 1000


def _looks_like_row_numbers(values) -> bool:
    """True when the values are just an index: 0,1,2… or 1,2,3…"""
    if len(values) < 2:
        return False
    return values == list(range(len(values))) or values == list(range(1, len(values) + 1))


def _ids_from_csv(path: Path) -> List[int]:
    """Read reach ids from a CSV, choosing the column by NAME where possible.

    This used to read column 0 unconditionally, which is right for the
    feature_ids CSV the Flowline step writes and wrong for two files people
    actually have: a frame saved with its pandas index (column 0 is 0,1,2…) and
    a GIS export where OBJECTID comes first.  Both parse cleanly as integers,
    so the download ran against ids that do not exist and came back empty with
    nothing to say for itself.
    """
    try:
        df = pd.read_csv(path)
    except Exception as exc:
        raise ValueError(f"Could not read {path.name}: {exc}") from exc
    if df.empty:
        raise ValueError(f"{path.name} has no rows.")

    # 1. A column named like a reach id wins, wherever it sits.
    lower = {str(c).strip().lower(): c for c in df.columns}
    col = next((lower[n] for n in _ID_COLUMNS if n in lower), None)

    # 2. Otherwise a single-column file is unambiguous — including the
    #    headerless case, where the first id was read AS the header.
    if col is None and df.shape[1] == 1:
        ids = []
        head = str(df.columns[0]).strip()
        try:
            ids.append(int(float(head)))        # headerless: row 1 is data
        except (TypeError, ValueError):
            pass
        col = df.columns[0]
        ids += [int(float(str(v).strip())) for v in df[col].dropna()
                if str(v).strip().lstrip("-").replace(".", "", 1).isdigit()]
        return _checked(ids, path)

    # 3. Several unnamed columns: take the first that actually looks like ids.
    if col is None:
        for c in df.columns:
            vals = pd.to_numeric(df[c], errors="coerce").dropna()
            if len(vals) and not _looks_like_row_numbers([int(v) for v in vals]) \
                    and float(vals.median()) >= _MIN_PLAUSIBLE_ID:
                col = c
                break
    if col is None:
        raise ValueError(
            f"{path.name} has no column holding reach ids. Columns found: "
            f"{', '.join(str(c) for c in df.columns)}. Name the column "
            f"'feature_id' (or COMID), or give a file with one id per line.")

    vals = pd.to_numeric(df[col], errors="coerce").dropna()
    return _checked([int(v) for v in vals], path, str(col))


def _checked(ids: List[int], path: Path, col: str = None) -> List[int]:
    """Reject ids that are plainly not reach ids rather than querying for them."""
    where = f"column '{col}' of " if col else ""
    if not ids:
        raise ValueError(f"No numeric reach ids found in {where}{path.name}.")
    if _looks_like_row_numbers(ids) or max(ids) < _MIN_PLAUSIBLE_ID:
        raise ValueError(
            f"{where}{path.name} looks like row numbers, not reach ids "
            f"(values {ids[0]}…{ids[-1]}). This usually means the file was "
            f"saved with its index, or the id column is not the first one — "
            f"name it 'feature_id' and FIMsim will find it wherever it sits.")
    return list(dict.fromkeys(ids))


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
            return _ids_from_csv(Path(s))
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
            f"NWM Retrospective only covers {RETRO_START.date()} to "
            f"{RETRO_END.date()}.  Your window {start_ts.date()} → {end_ts.date()} "
            "is outside that range — this data is not available for this time.  "
            "Use the NWM Forecast (2019–now) source for more recent dates."
        )

    log_fn(f"Opening NWM v3.0 retrospective Zarr store … ({len(fids)} feature_id(s))")
    ds = xr.open_zarr(RETRO_URL, consolidated=True)

    fids_in_store = set(int(x) for x in ds["feature_id"].values)
    missing = [f for f in fids if f not in fids_in_store]
    if missing:
        log_fn(f"  {len(missing)} feature_id(s) NOT in NWM v2.1: {missing[:10]}…")
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
