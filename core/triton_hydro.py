"""TRITON step — Generate the upstream inflow hydrograph (.hyg) file.

TRITON .hyg format (per docs + example files):

    Single source:
        %Time(hr) Discharge(cms)
        0,48.64999891
        1,48.86999891
        ...

    Multi-source (num_sources > 1):
        % Hydrograph
        % Time(hr) Discharge(cms)
        0,2.81,15.46
        1,2.81,21.08
        ...

Time is in HOURS from sim_start_time (row 0 = t=0 h).
Discharge is m³/s for each source column.
No count line at the top; rows are comma-separated.

This module writes:
  - {triton_dir}/{hyg_filename}                  — the TRITON-format hydrograph
  - {project_dir}/{project}_strmflow_timeseries.csv  — helper CSV (human inspection)

Multi-source flow
-----------------
When num_sources > 1 (set by the BC step), call prepare_triton_hydro() once per
source with source_index=0,1,…; each call appends its series into
ctx["_hyg_pending"]. When every source has been populated, the final call
automatically writes the combined .hyg. The GUI layer drives this loop.
"""
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from core.context import save_context


# ── helpers ───────────────────────────────────────────────────────────────────

def _read_user_discharge_table(path):
    """Read a user-supplied discharge table (CSV / XLSX / TXT).

    Expected columns: time_hours, discharge_cms.
    Returns a DataFrame sorted by time_hours with NaN rows dropped.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in (".csv", ".txt"):
        df = pd.read_csv(path)
    elif suffix in (".xlsx", ".xls"):
        df = pd.read_excel(path)
    else:
        raise ValueError(f"Unsupported file type: {suffix}")

    cols = {c.lower().strip(): c for c in df.columns}
    if "time_hours" not in cols or "discharge_cms" not in cols:
        raise ValueError(
            "Discharge file must have columns: time_hours  and  discharge_cms"
        )
    df = df[[cols["time_hours"], cols["discharge_cms"]]].copy()
    df.columns = ["time_hours", "discharge_cms"]
    return df.dropna().sort_values("time_hours").reset_index(drop=True)


def _get_nwm_timeseries(feature_id, start_dt, end_dt, interval_hours, log_fn):
    """Download streamflow from NOAA NWM v2.1 retrospective Zarr store.

    Returns a DataFrame with columns: datetime, discharge_cms.
    """
    try:
        import zarr   # noqa
        import s3fs   # noqa
    except ImportError:
        raise ImportError(
            "zarr and s3fs are required for NWM download.  pip install zarr s3fs"
        )

    url = "s3://noaa-nwm-retrospective-2-1-zarr-pds/chrtout.zarr"
    log_fn("Opening NWM retrospective Zarr store (NOAA v2.1)…")
    ds = xr.open_zarr(url, consolidated=True, storage_options={"anon": True})

    feature_id = int(feature_id)
    start_ts   = pd.Timestamp(start_dt)
    end_ts     = pd.Timestamp(end_dt)

    nwm_start = pd.Timestamp("1979-02-01")
    nwm_end   = pd.Timestamp("2020-12-31")
    if start_ts < nwm_start or end_ts > nwm_end:
        raise ValueError(
            f"NWM v2.1 retrospective only covers {nwm_start.date()} to {nwm_end.date()}.\n"
            f"Your selected window: {start_ts.date()} to {end_ts.date()}\n"
            "Please adjust your event dates or use a CSV file instead."
        )

    log_fn(f"Extracting streamflow for feature_id={feature_id} …")

    fids = ds["feature_id"].values
    if feature_id not in fids:
        raise ValueError(
            f"feature_id={feature_id} not found in the NWM Zarr store.\n"
            "This reach may not be in the NWM network.  Use a CSV file instead."
        )

    da = ds["streamflow"].sel(time=slice(start_ts, end_ts)).sel(feature_id=feature_id)
    ser = da.to_series().sort_index()

    if ser.empty:
        raise RuntimeError(
            f"No NWM streamflow returned for feature_id={feature_id} "
            f"between {start_ts.date()} and {end_ts.date()}."
        )

    log_fn(f"Retrieved {len(ser)} hourly NWM values.")

    target_times = pd.date_range(
        start=start_ts, end=end_ts, freq=pd.Timedelta(hours=interval_hours)
    )
    if len(target_times) == 0 or target_times[-1] != end_ts:
        target_times = target_times.union(pd.DatetimeIndex([end_ts]))

    ser2 = (
        ser.reindex(ser.index.union(target_times))
        .sort_index()
        .interpolate(method="time")
        .reindex(target_times)
    )

    return pd.DataFrame(
        {"datetime": ser2.index, "discharge_cms": ser2.values.astype(float)}
    ).reset_index(drop=True)


def _write_triton_hyg(series_list, start_dt, interval_hours, out_path):
    """Write a TRITON .hyg file.

    Parameters
    ----------
    series_list : list[pandas.Series]
        One Series per source, all indexed by the SAME target DatetimeIndex
        (built from start_dt + interval_hours).  The first entry must be the
        hydrograph for source_index=0, etc.
    start_dt : datetime-like
        Event start — used to anchor t=0 h.
    interval_hours : float
        Sample spacing in hours.  Used only for the comment line.
    out_path : Path
    """
    if not series_list:
        raise ValueError("series_list is empty")

    n_sources = len(series_list)
    idx       = series_list[0].index
    t0        = pd.Timestamp(start_dt)
    t_hours   = (idx - t0).total_seconds().values / 3600.0

    # Comment header — single source vs multi-source layout
    if n_sources == 1:
        lines = ["%Time(hr) Discharge(cms)"]
    else:
        lines = ["% Hydrograph",
                 "% Time(hr) " + " ".join(f"Discharge(cms)_{i + 1}" for i in range(n_sources))]

    # Data rows — integer time when it lands on whole hours, else 6 decimals
    def _fmt_time(t):
        return f"{int(round(t))}" if abs(t - round(t)) < 1e-6 else f"{t:.6f}"

    def _fmt_q(q):
        if not np.isfinite(q):
            return "0"
        return f"{float(q):.6f}" if abs(q) >= 1e-3 or q == 0 else f"{float(q):.8f}"

    for i, t in enumerate(t_hours):
        row = [_fmt_time(float(t))]
        for ser in series_list:
            row.append(_fmt_q(float(ser.iloc[i])))
        lines.append(",".join(row))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── discharge fetchers (TRITON-owned copies — kept independent of core.bdy) ─────
# These mirror the proven LISFLOOD discharge fetchers but live here so the TRITON
# workflow has no import dependency on the LISFLOOD BDY module.

def _resample_to_interval(ser, start_ts, end_ts, interval_hours):
    """Time-interpolate ``ser`` (datetime-indexed) onto a regular grid."""
    target_times = pd.date_range(
        start=start_ts, end=end_ts,
        freq=pd.Timedelta(hours=float(interval_hours)),
    )
    if len(target_times) == 0 or target_times[-1] != end_ts:
        target_times = target_times.union(pd.DatetimeIndex([end_ts]))
    ser2 = (
        ser.reindex(ser.index.union(target_times))
           .sort_index().interpolate(method="time")
    )
    ser2 = ser2.reindex(target_times)
    return pd.DataFrame({
        "datetime":      ser2.index,
        "discharge_cms": ser2.values.astype(float),
    }).reset_index(drop=True)


def _get_nwm_retrospective(feature_id, start_ts, end_ts, interval_hours, log_fn):
    """Pull discharge from the NWM v2.1 retrospective Zarr store."""
    url = "s3://noaa-nwm-retrospective-2-1-zarr-pds/chrtout.zarr"
    log_fn("Opening NWM retrospective Zarr store (NOAA v2.1) …")
    ds = xr.open_zarr(url, consolidated=True, storage_options={"anon": True})

    feature_id = int(feature_id)
    log_fn(f"Extracting streamflow for feature_id={feature_id} …")
    fids = ds["feature_id"].values
    if feature_id not in fids:
        raise ValueError(
            f"feature_id={feature_id} not found in the NWM Zarr store.\n"
            "This reach may not be in the NWM network. Use a CSV file instead."
        )

    da = ds["streamflow"].sel(time=slice(start_ts, end_ts)) \
                          .sel(feature_id=feature_id)
    ser = da.to_series().sort_index()
    if ser.empty:
        raise RuntimeError(
            f"No NWM retrospective streamflow returned for feature_id="
            f"{feature_id} between {start_ts.date()} and {end_ts.date()}."
        )
    log_fn(f"Retrieved {len(ser)} hourly NWM retrospective values.")
    return _resample_to_interval(ser, start_ts, end_ts, interval_hours)


def _get_nwm_forecast(feature_id, start_ts, end_ts, interval_hours, log_fn):
    """Pull discharge from the NWM operational medium-range forecast."""
    from core.nwm_discharge import download_nwm_forecast
    import tempfile

    feature_id = int(feature_id)
    with tempfile.NamedTemporaryFile(
        prefix="nwm_forecast_", suffix=".csv", delete=False,
    ) as tf:
        tmp_csv = Path(tf.name)
    try:
        download_nwm_forecast(
            feature_ids=feature_id,
            out_csv=tmp_csv,
            forecast_set="medium_range_mem1",
            log_fn=log_fn,
        )
        fc = pd.read_csv(tmp_csv, parse_dates=["datetime"])
    finally:
        try:
            tmp_csv.unlink()
        except Exception:
            pass

    feat_col = str(feature_id)
    if feat_col not in fc.columns:
        raise RuntimeError(
            f"feature_id={feature_id} not found in the NWM forecast output."
        )
    ser = pd.Series(
        fc[feat_col].astype(float).values,
        index=pd.DatetimeIndex(fc["datetime"]),
    ).sort_index()

    in_window = ser[(ser.index >= start_ts) & (ser.index <= end_ts)]
    if in_window.empty:
        raise RuntimeError(
            f"NWM forecast did not return values inside your event window "
            f"({start_ts.date()} to {end_ts.date()}).\n"
            "The medium-range forecast covers ~10 days from today's run — "
            "your window may be too far in the future.  "
            "Pull back the dates or use a CSV file."
        )
    log_fn(
        f"Retrieved {len(in_window)} hourly NWM forecast values inside the "
        f"event window."
    )
    return _resample_to_interval(in_window, start_ts, end_ts, interval_hours)


def _trim_nan_boundaries(df_flow, source_name, log_fn):
    """Remove leading/trailing NaN discharge rows from df_flow.

    Returns (trimmed_df, warning_strings).
    """
    valid = df_flow["discharge_cms"].notna()
    warns = []
    if not valid.any():
        return df_flow.iloc[0:0].reset_index(drop=True), [
            f"{source_name}: all discharge values are NaN for the requested period — "
            "no valid data to write."
        ]
    first = int(valid.idxmax())
    last  = int(valid[::-1].idxmax())
    if first > 0:
        t0 = pd.Timestamp(df_flow["datetime"].iloc[0])
        t1 = pd.Timestamp(df_flow["datetime"].iloc[first])
        h  = round((t1 - t0).total_seconds() / 3600, 1)
        w  = (f"{source_name}: no discharge data for the first {h}h of the requested "
              f"period — {first} timestep(s) removed. "
              f"Hydrograph starts at {t1.strftime('%Y-%m-%d %H:%M')}.")
        warns.append(w)
        log_fn(f"  ⚠ {w}")
    trailing = len(df_flow) - last - 1
    if trailing > 0:
        t_end_orig = pd.Timestamp(df_flow["datetime"].iloc[-1])
        t_end_trim = pd.Timestamp(df_flow["datetime"].iloc[last])
        h = round((t_end_orig - t_end_trim).total_seconds() / 3600, 1)
        w  = (f"{source_name}: no discharge data for the last {h}h of the requested "
              f"period — {trailing} timestep(s) removed. "
              f"Hydrograph ends at {t_end_trim.strftime('%Y-%m-%d %H:%M')}.")
        warns.append(w)
        log_fn(f"  ⚠ {w}")
    return df_flow.iloc[first:last + 1].reset_index(drop=True), warns


def _bdy_read_user_discharge_table(path):
    """Read a user discharge table, returning (df, has_datetimes, start_dt, end_dt).

    df has columns time_hours (float, relative hours) and discharge_cms (float).
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in (".csv", ".txt"):
        df = pd.read_csv(path)
    elif suffix in (".xlsx", ".xls"):
        df = pd.read_excel(path)
    else:
        raise ValueError(f"Unsupported file type: {suffix}")

    cols = {c.lower().strip(): c for c in df.columns}
    if "time_hours" not in cols or "discharge_cms" not in cols:
        raise ValueError(
            "File must have columns: time_hours  and  discharge_cms"
        )
    df = df[[cols["time_hours"], cols["discharge_cms"]]].copy()
    df.columns = ["time_hours", "discharge_cms"]
    df = df.dropna().reset_index(drop=True)

    has_datetimes = False
    start_dt = end_dt = None
    is_numeric = pd.api.types.is_numeric_dtype(df["time_hours"])
    if not is_numeric:
        try:
            dt_series = pd.to_datetime(df["time_hours"], utc=True)
            has_datetimes = True
            dt_series = dt_series.sort_values().reset_index(drop=True)
            start_dt = dt_series.iloc[0]
            end_dt   = dt_series.iloc[-1]
            df["time_hours"] = (dt_series - start_dt).dt.total_seconds() / 3600.0
            df["discharge_cms"] = df["discharge_cms"].astype(float)
            start_dt = start_dt.tz_localize(None)
            end_dt   = end_dt.tz_localize(None)
        except Exception:
            raise ValueError(
                "The time_hours column contains non-numeric values that could not "
                "be parsed as datetimes.  Provide either numeric hours (0, 1, 2, …) "
                "or datetime strings (e.g. 2018-08-26 00:00:00)."
            )

    df = df.sort_values("time_hours").reset_index(drop=True)
    return df, has_datetimes, start_dt, end_dt


def write_triton_hyg_single(ctx_path, ctx, *, bdy_source, start_dt, end_dt,
                            interval_hours, gage_id=None, user_csv_path=None,
                            nwm_reach_id=None, log_fn=print):
    """One-inflow TRITON .hyg from a chosen source (NWM retro/forecast, USGS,
    or uploaded CSV), using TRITON's own discharge fetchers and the existing
    ``_write_triton_hyg`` writer.  Writes <AOI>.hyg into triton-files and a
    helper CSV (datetime, discharge_cms) in the case folder for previewing.
    Sets ctx['sim_duration'] (seconds) from the last hydrograph time.
    """
    triton_dir  = Path(ctx["triton_dir"])
    project_dir = Path(ctx["project_dir"])
    aoi_name    = ctx.get("aoi_name") or ctx.get("project_name", "triton")
    start_ts = pd.Timestamp(start_dt)
    end_ts   = pd.Timestamp(end_dt)

    if bdy_source == "nwm_retro":
        if nwm_reach_id is None:
            raise ValueError("NWM source needs an upstream reach/feature ID (run BC first).")
        df_flow = _get_nwm_retrospective(nwm_reach_id, start_ts, end_ts, interval_hours, log_fn)
    elif bdy_source == "nwm_forecast":
        if nwm_reach_id is None:
            raise ValueError("NWM source needs an upstream reach/feature ID (run BC first).")
        df_flow = _get_nwm_forecast(nwm_reach_id, start_ts, end_ts, interval_hours, log_fn)
    elif bdy_source == "usgs":
        if not gage_id:
            raise ValueError("gage_id is required when the source is USGS.")
        import tempfile
        from core.flowline_mode import _download_usgs_discharge
        log_fn(f"Downloading USGS discharge for gage {gage_id} …")
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = _download_usgs_discharge(
                gage_ids=[gage_id], start_dt=start_dt, end_dt=end_dt,
                out_folder=Path(tmpdir), interval_hours=interval_hours, log_fn=log_fn,
            )
            if not paths:
                raise RuntimeError(f"No USGS data for gage {gage_id} in the requested period.")
            raw = pd.read_csv(paths[0])
        if "datetime" not in raw.columns and raw.index.name == "datetime":
            raw = raw.reset_index()
        qcol = next((c for c in ("streamflow_m3s", "discharge_cms") if c in raw.columns), None)
        if qcol is None:
            raise RuntimeError(f"USGS CSV for gage {gage_id} has no discharge column.")
        raw["datetime"] = pd.to_datetime(raw["datetime"], errors="coerce")
        raw = raw.dropna(subset=["datetime"])
        di = pd.DatetimeIndex(raw["datetime"])
        if di.tz is not None:
            di = di.tz_convert(None)
        ser0 = pd.Series(raw[qcol].astype(float).values, index=di)
        df_flow = _resample_to_interval(ser0, start_ts, end_ts, interval_hours)
    elif bdy_source == "csv":
        if not user_csv_path:
            raise ValueError("A CSV path is required when the source is 'csv'.")
        tbl, has_dt, csv_start, _csv_end = _bdy_read_user_discharge_table(user_csv_path)
        anchor = pd.Timestamp(csv_start) if (has_dt and csv_start is not None) else start_ts
        di = anchor + pd.to_timedelta(tbl["time_hours"].astype(float), unit="h")
        ser0 = pd.Series(tbl["discharge_cms"].astype(float).values, index=pd.DatetimeIndex(di))
        s2 = anchor if (has_dt and csv_start is not None) else start_ts
        e2 = pd.Timestamp(ser0.index.max())
        df_flow = _resample_to_interval(ser0, s2, e2, interval_hours)
    else:
        raise ValueError(f"Unsupported hydrograph source: {bdy_source!r}")

    df_flow, _trim = _trim_nan_boundaries(df_flow, bdy_source, log_fn)
    if df_flow.empty:
        raise RuntimeError(f"{bdy_source}: no valid discharge data for the requested period.")

    ser = pd.Series(
        df_flow["discharge_cms"].astype(float).values,
        index=pd.DatetimeIndex(df_flow["datetime"]),
    )
    t0 = pd.Timestamp(df_flow["datetime"].iloc[0])
    hyg_path = triton_dir / f"{aoi_name}.hyg"
    _write_triton_hyg([ser], t0, interval_hours, hyg_path)
    log_fn(f"{hyg_path.name} written: {hyg_path}")

    helper = project_dir / f"{aoi_name}_hydrograph.csv"
    df_flow.to_csv(helper, index=False)

    last_hr = (pd.Timestamp(df_flow["datetime"].iloc[-1]) - t0).total_seconds() / 3600.0
    ctx["triton_hyg_path"]          = str(hyg_path)
    ctx["triton_hyg_filename"]      = hyg_path.name
    ctx["triton_hydro_path"]        = str(hyg_path)
    ctx["triton_hydro_helper_csv"]  = str(helper)
    ctx["triton_hydro_source"]      = bdy_source
    ctx["triton_hyg_written"]       = True
    ctx["num_sources"]              = 1
    ctx["sim_duration"]             = float(last_hr) * 3600.0   # seconds
    ctx["event_start"]              = start_ts.strftime("%Y-%m-%d %H:%M")
    ctx["event_end"]                = end_ts.strftime("%Y-%m-%d %H:%M")
    save_context(ctx_path, ctx)
    return ctx


def _parse_triton_hyg(path, start_dt):
    """Read an existing TRITON .hyg file into a DataFrame.

    Accepts the current format (time in hours, comma-separated, optional
    `%...` comment header) AND the legacy format produced by earlier versions
    of this app (count line on row 1, time in seconds, space-separated).  The
    result always has columns datetime + discharge_cms_0, discharge_cms_1, ….
    """
    raw = path.read_text(encoding="utf-8", errors="replace").splitlines()

    # Separate comments / data
    header_lines, data_lines = [], []
    for ln in raw:
        s = ln.strip()
        if not s:
            continue
        if s.startswith("%") or s.startswith("#"):
            header_lines.append(s)
            continue
        data_lines.append(s)

    if not data_lines:
        raise ValueError(f"TRITON .hyg file has no data rows: {path}")

    # Legacy detection — first non-comment row is a single integer = count
    legacy_count = None
    if data_lines[0].split(maxsplit=1) and len(data_lines[0].split()) == 1:
        try:
            legacy_count = int(data_lines[0])
        except ValueError:
            legacy_count = None
    if legacy_count is not None and legacy_count + 1 <= len(data_lines):
        data_lines = data_lines[1:1 + legacy_count]

    # Parse rows — split on comma or whitespace
    def _split(ln):
        if "," in ln:
            return [p.strip() for p in ln.split(",") if p.strip() != ""]
        return ln.split()

    parsed = []
    for ln in data_lines:
        parts = _split(ln)
        if len(parts) < 2:
            raise ValueError(f"TRITON .hyg row has <2 columns: '{ln}'")
        try:
            row = [float(p) for p in parts]
        except ValueError:
            raise ValueError(f"TRITON .hyg row has non-numeric values: '{ln}'")
        parsed.append(row)

    max_cols = max(len(r) for r in parsed)
    n_src    = max_cols - 1

    times_col = [r[0] for r in parsed]
    # Auto-detect time units: if max time >> realistic sim_duration in hours
    # (here: values > 1e4 strongly suggest seconds), convert to hours.
    time_hours = [t / 3600.0 for t in times_col] if max(times_col) > 1e4 else times_col

    t0 = pd.Timestamp(start_dt)
    dt_col = [t0 + pd.Timedelta(hours=float(h)) for h in time_hours]

    out = {"datetime": dt_col}
    for i in range(n_src):
        out[f"discharge_cms_{i}"] = [
            r[i + 1] if i + 1 < len(r) else float("nan") for r in parsed
        ]
    return pd.DataFrame(out)


def _make_constant_flow_df(start_dt, end_dt, discharge_cms):
    """Return a two-row DataFrame for a constant-discharge hydrograph."""
    return pd.DataFrame({
        "datetime":      [pd.Timestamp(start_dt), pd.Timestamp(end_dt)],
        "discharge_cms": [float(discharge_cms),  float(discharge_cms)],
    })


def _resample_to_target(ser, start_ts, end_ts, interval_hours):
    target_times = pd.date_range(
        start=start_ts, end=end_ts, freq=pd.Timedelta(hours=interval_hours)
    )
    if len(target_times) == 0:
        raise ValueError("interval_hours is too large for the event window duration.")
    if target_times[-1] != end_ts:
        target_times = target_times.union(pd.DatetimeIndex([end_ts]))
    out = (
        ser.reindex(ser.index.union(target_times))
        .sort_index()
        .interpolate(method="time")
        .reindex(target_times)
    )
    return out


# ── public API ────────────────────────────────────────────────────────────────

def prepare_triton_hydro(
    ctx_path,
    ctx: dict,
    start_dt,
    end_dt,
    interval_hours: float,
    hydro_source: str,                 # "nwm" | "csv" | "existing" | "constant"
    user_csv_path: str = None,
    existing_hydro_path: str = None,
    constant_discharge_cms: float = None,
    # Multi-source support
    source_index: int = 0,             # 0-based — which source column this call fills
    hyg_filename: str = None,          # default {project_name}.hyg
    # NWM-specific override (used when the reach per source differs)
    nwm_reach_id: str = None,
    log_fn=print,
):
    """Build one source-column of the TRITON upstream hydrograph.

    In single-source projects (num_sources == 1 in ctx) this writes the final
    .hyg immediately.  In multi-source projects the call appends to a buffer
    in ctx["_hyg_pending"] and only writes the final file when every source
    has been populated.  Call finalize_hyg() explicitly if the GUI prefers to
    force a write.

    Returns updated ctx.
    """
    if hydro_source not in ("nwm", "csv", "existing", "constant"):
        raise ValueError(
            f"hydro_source must be 'nwm', 'csv', 'existing', or 'constant'.  "
            f"Got '{hydro_source}'."
        )

    project_dir  = Path(ctx["project_dir"])
    triton_dir   = Path(ctx["triton_dir"])
    project_name = ctx.get("project_name", "triton")
    num_sources  = int(ctx.get("num_sources", 1))

    if source_index < 0 or source_index >= num_sources:
        raise ValueError(
            f"source_index={source_index} is out of range for num_sources={num_sources}."
        )

    start_ts = pd.Timestamp(start_dt)
    end_ts   = pd.Timestamp(end_dt)
    if end_ts <= start_ts:
        raise ValueError("end_dt must be after start_dt.")

    # Name the hydrograph after this AOI (mirrors LISFLOOD's <AOI>.bdy).
    aoi_name     = ctx.get("aoi_name") or project_name
    hyg_filename = hyg_filename or f"{aoi_name}.hyg"
    hyg_path     = triton_dir / hyg_filename

    # ── build df_flow for this source ─────────────────────────────────────────
    if hydro_source == "constant":
        if constant_discharge_cms is None or constant_discharge_cms < 0:
            raise ValueError("constant_discharge_cms must be ≥ 0.")
        # A constant trace still needs to land on the shared target index
        target_times = pd.date_range(
            start=start_ts, end=end_ts, freq=pd.Timedelta(hours=interval_hours)
        )
        if len(target_times) == 0:
            raise ValueError("interval_hours is too large for the event window duration.")
        if target_times[-1] != end_ts:
            target_times = target_times.union(pd.DatetimeIndex([end_ts]))
        df_flow = pd.DataFrame({
            "datetime":      target_times,
            "discharge_cms": [float(constant_discharge_cms)] * len(target_times),
        })
        log_fn(f"[src {source_index}] Constant discharge: {constant_discharge_cms:.3f} m³/s")

    elif hydro_source == "nwm":
        reach = nwm_reach_id or ctx.get("upstream_reach_id")
        if reach is None:
            raise ValueError(
                "NWM reach id not provided (pass nwm_reach_id or run the BC step first)."
            )
        df_flow = _get_nwm_timeseries(reach, start_dt, end_dt, interval_hours, log_fn)
        log_fn(f"[src {source_index}] NWM reach {reach}: {len(df_flow)} time steps.")

    elif hydro_source == "csv":
        if not user_csv_path:
            raise ValueError("user_csv_path must be provided when hydro_source='csv'.")
        log_fn(f"[src {source_index}] Reading CSV: {Path(user_csv_path).name}")
        df_user = _read_user_discharge_table(user_csv_path)
        csv_times = start_ts + pd.to_timedelta(
            df_user["time_hours"].astype(float), unit="h"
        )
        ser = pd.Series(
            df_user["discharge_cms"].astype(float).values, index=csv_times
        ).sort_index()
        ser2 = _resample_to_target(ser, start_ts, end_ts, interval_hours)
        ser2 = ser2[(ser2.index >= start_ts) & (ser2.index <= end_ts)]
        if ser2.dropna().empty:
            raise ValueError(
                "No valid discharge values after resampling.  Check your CSV time range."
            )
        df_flow = pd.DataFrame(
            {"datetime": ser2.index, "discharge_cms": ser2.values.astype(float)}
        ).reset_index(drop=True)
        log_fn(f"[src {source_index}] Resampled CSV → {len(df_flow)} steps.")

    else:  # existing
        if not existing_hydro_path:
            raise ValueError("existing_hydro_path must be provided when hydro_source='existing'.")
        src = Path(existing_hydro_path)
        if not src.exists():
            raise FileNotFoundError(f"Existing .hyg file not found: {src}")
        log_fn(f"[src {source_index}] Parsing existing .hyg: {src.name}")
        df_src = _parse_triton_hyg(src, start_dt)
        # Pick the right source column.  If the file has only one discharge
        # column use it regardless of source_index; otherwise select column
        # source_index (0-based).
        disch_cols = [c for c in df_src.columns if c.startswith("discharge_cms")]
        if not disch_cols:
            raise ValueError("Parsed .hyg has no discharge columns.")
        col = disch_cols[min(source_index, len(disch_cols) - 1)]
        ser = pd.Series(df_src[col].values, index=df_src["datetime"]).sort_index()
        ser2 = _resample_to_target(ser, start_ts, end_ts, interval_hours).clip(lower=0)
        if ser2.dropna().empty:
            raise ValueError(
                "No valid discharge values after resampling the existing .hyg."
            )
        df_flow = pd.DataFrame(
            {"datetime": ser2.index, "discharge_cms": ser2.values.astype(float)}
        ).reset_index(drop=True)
        log_fn(f"[src {source_index}] Resampled existing .hyg → {len(df_flow)} steps.")

    # ── buffer this source's series in ctx ────────────────────────────────────
    pending = ctx.setdefault("_hyg_pending", {})
    # Store the series as {ts_iso: value} so json-save round-trips safely
    pending[str(source_index)] = {
        "datetime":      [d.isoformat() for d in df_flow["datetime"]],
        "discharge_cms": [float(v) for v in df_flow["discharge_cms"]],
        "hydro_source":  hydro_source,
    }
    ctx["_hyg_start"]          = start_ts.isoformat()
    ctx["_hyg_end"]            = end_ts.isoformat()
    ctx["_hyg_interval_hours"] = float(interval_hours)
    ctx["triton_hyg_filename"] = hyg_filename

    # Meta
    ctx["event_start"]           = start_ts.strftime("%Y-%m-%d %H:%M")
    ctx["event_end"]             = end_ts.strftime("%Y-%m-%d %H:%M")
    ctx["sim_duration"]          = (end_ts - start_ts).total_seconds()
    ctx["series_interval_hours"] = float(interval_hours)
    # Track per-source provenance
    ctx.setdefault("triton_hydro_source_per_idx", {})[str(source_index)] = hydro_source

    # ── If every source has been populated, finalize now ──────────────────────
    if len(pending) >= num_sources:
        ctx = _finalize_hyg_from_pending(ctx_path, ctx, hyg_path, log_fn)
    else:
        log_fn(
            f"[src {source_index}] Buffered.  {len(pending)}/{num_sources} sources populated. "
            "Run the step for each remaining source."
        )
        save_context(ctx_path, ctx)
    return ctx


def finalize_hyg(ctx_path, ctx):
    """Force-write the pending .hyg file from whatever buffered sources exist.

    Useful if the GUI wants to emit the file partway through (e.g. the user
    aborts the multi-source loop after N-1 sources).
    """
    triton_dir   = Path(ctx["triton_dir"])
    project_name = ctx.get("project_name", "triton")
    aoi_name     = ctx.get("aoi_name") or project_name
    hyg_filename = ctx.get("triton_hyg_filename") or f"{aoi_name}.hyg"
    hyg_path     = triton_dir / hyg_filename
    return _finalize_hyg_from_pending(ctx_path, ctx, hyg_path, print)


def _finalize_hyg_from_pending(ctx_path, ctx, hyg_path, log_fn):
    pending = ctx.get("_hyg_pending", {})
    if not pending:
        raise RuntimeError("No buffered source series found; nothing to write.")
    num_sources = int(ctx.get("num_sources", 1))
    start_ts    = pd.Timestamp(ctx["_hyg_start"])
    interval    = float(ctx["_hyg_interval_hours"])

    series_list = []
    for i in range(num_sources):
        blob = pending.get(str(i))
        if blob is None:
            raise RuntimeError(
                f"Source {i} has no buffered hydrograph yet; run it before finalizing."
            )
        ts  = pd.DatetimeIndex(blob["datetime"])
        ser = pd.Series(blob["discharge_cms"], index=ts, name=f"q_{i}")
        series_list.append(ser)

    # Enforce a common index (just in case any source has different timestamps)
    common_idx = series_list[0].index
    for ser in series_list[1:]:
        if not ser.index.equals(common_idx):
            common_idx = common_idx.union(ser.index)
    series_list = [ser.reindex(common_idx).interpolate(method="time")
                   for ser in series_list]

    _write_triton_hyg(series_list, start_ts, interval, hyg_path)
    log_fn(f".hyg written: {hyg_path}")

    # Helper CSV with all source columns — useful for inspection
    df_all = pd.DataFrame({"datetime": common_idx})
    for i, ser in enumerate(series_list):
        df_all[f"discharge_cms_src{i}"] = ser.values
    helper_csv = Path(ctx["project_dir"]) / f"{ctx.get('project_name', 'triton')}_strmflow_timeseries.csv"
    df_all.to_csv(helper_csv, index=False)
    log_fn(f"Helper CSV saved: {helper_csv.name}")

    # Flush pending buffer and set final paths
    ctx["triton_hyg_path"]      = str(hyg_path)
    ctx["triton_hydro_path"]    = str(hyg_path)  # alias kept for older cfg code
    ctx["triton_hydro_helper_csv"] = str(helper_csv)
    ctx["triton_hyg_filename"]  = hyg_path.name
    ctx["triton_hyg_written"]   = True
    ctx["triton_hydro_written"] = True   # legacy alias
    ctx.pop("_hyg_pending", None)
    save_context(ctx_path, ctx)
    return ctx
