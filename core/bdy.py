"""Step 7 — Create the LISFLOOD-FP .bdy boundary conditions file."""
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import xarray as xr

from core.context import save_context


# ── helpers ──────────────────────────────────────────────────────────────────

def _get_dem_cell_size(dem_tif_path):
    with rasterio.open(dem_tif_path) as src:
        return float(abs(src.res[0]))


def _read_user_discharge_table(path):
    """Read a user-supplied discharge table (CSV / XLSX / TXT).

    Accepts two column layouts:
      A) time_hours (numeric, relative hours from t=0), discharge_cms
      B) time_hours (datetime strings), discharge_cms

    Returns (df, has_datetimes, start_dt, end_dt):
      - df has columns: time_hours (float, relative hours), discharge_cms (float)
      - has_datetimes: True when the file contained datetime timestamps
      - start_dt / end_dt: actual Timestamps when has_datetimes, else None
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

    # Detect whether time_hours contains datetime strings or numeric values
    has_datetimes = False
    start_dt = end_dt = None
    sample = df["time_hours"].iloc[0] if len(df) > 0 else None
    is_numeric = pd.api.types.is_numeric_dtype(df["time_hours"])
    if not is_numeric:
        # Try parsing as datetimes
        try:
            dt_series = pd.to_datetime(df["time_hours"], utc=True)
            has_datetimes = True
            dt_series = dt_series.sort_values().reset_index(drop=True)
            start_dt = dt_series.iloc[0]
            end_dt   = dt_series.iloc[-1]
            # Convert to relative hours from the first timestamp
            df["time_hours"] = (dt_series - start_dt).dt.total_seconds() / 3600.0
            df["discharge_cms"] = df["discharge_cms"].astype(float)
            # Strip timezone so downstream code works with naive Timestamps
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


def check_csv_gaps(csv_path, interval_hours):
    """Check a user CSV for missing timesteps at the given interval.

    Returns a dict:
        ok       : True if no gaps found
        n_rows   : total rows in the CSV
        n_expected : how many rows the interval implies (from first to last)
        n_missing  : how many timesteps are absent
        missing_times : list of the first ≤20 missing time_hours values
        has_datetimes : whether the file used datetime strings
        start_dt / end_dt : parsed Timestamps (or None)
    """
    df, has_datetimes, start_dt, end_dt = _read_user_discharge_table(csv_path)
    t_min = float(df["time_hours"].min())
    t_max = float(df["time_hours"].max())

    # Build the expected regular grid
    expected = set()
    t = t_min
    while t <= t_max + 1e-9:
        expected.add(round(t, 6))
        t += interval_hours

    present = set(round(float(v), 6) for v in df["time_hours"])
    missing = sorted(expected - present)

    return {
        "ok":             len(missing) == 0,
        "n_rows":         len(df),
        "n_expected":     len(expected),
        "n_missing":      len(missing),
        "missing_times":  missing[:20],
        "has_datetimes":  has_datetimes,
        "start_dt":       start_dt,
        "end_dt":         end_dt,
    }


# NWM v2.1 retrospective covers exactly this range
_RETRO_START = pd.Timestamp("1979-02-01")
_RETRO_END   = pd.Timestamp("2020-12-31")


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

    # Restrict to the user's window
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


def _get_nwm_timeseries(feature_id, start_dt, end_dt, interval_hours, log_fn):
    """Pick retrospective or operational forecast based on the date range,
    log the chosen source + reason, and return a (datetime, discharge_cms)
    DataFrame at the user's interval."""
    try:
        import s3fs  # noqa
    except ImportError:
        raise ImportError(
            "s3fs is required for NWM download.  pip install s3fs"
        )

    start_ts = pd.Timestamp(start_dt)
    end_ts   = pd.Timestamp(end_dt)

    # Entirely within the retrospective range → use retrospective
    if end_ts <= _RETRO_END:
        if start_ts < _RETRO_START:
            raise ValueError(
                f"Event window starts {start_ts.date()}, before the NWM "
                f"retrospective coverage ({_RETRO_START.date()})."
            )
        log_fn(
            f"NWM source: retrospective v2.1 — your window "
            f"({start_ts.date()} to {end_ts.date()}) is within the "
            f"retrospective coverage (1979-02-01 to 2020-12-31)."
        )
        try:
            import zarr  # noqa
        except ImportError:
            raise ImportError(
                "zarr is required for NWM retrospective download. "
                "pip install zarr s3fs"
            )
        return _get_nwm_retrospective(
            feature_id, start_ts, end_ts, interval_hours, log_fn,
        )

    # Entirely after the retrospective range → use operational forecast
    if start_ts > _RETRO_END:
        log_fn(
            f"NWM source: operational forecast (medium-range, ~10-day "
            f"horizon) — your window ({start_ts.date()} to {end_ts.date()}) "
            f"is after the retrospective end date ({_RETRO_END.date()}), "
            f"so retrospective data is not available."
        )
        return _get_nwm_forecast(
            feature_id, start_ts, end_ts, interval_hours, log_fn,
        )

    # Straddles the boundary
    raise ValueError(
        f"Your event window ({start_ts.date()} to {end_ts.date()}) crosses "
        f"the NWM retrospective/forecast boundary ({_RETRO_END.date()}).\n"
        "Pick a window entirely on or before that date (uses retrospective) "
        "or entirely after (uses operational forecast)."
    )


def _write_bdy_file(df_flow, bdy_path, series_name, project_name, dem_cell_size):
    """Write LISFLOOD-FP .bdy format."""
    df = df_flow.copy()
    t0 = pd.Timestamp(df["datetime"].iloc[0])
    df["time_seconds"] = (pd.to_datetime(df["datetime"]) - t0).dt.total_seconds().astype(int)
    df["q_unit"] = df["discharge_cms"].astype(float) / float(dem_cell_size)

    lines = [f"#{project_name}", series_name, f"{len(df)}\tseconds"]
    for _, row in df.iterrows():
        lines.append(f"{row['q_unit']:.6f}\t{int(row['time_seconds'])}")

    bdy_path.parent.mkdir(parents=True, exist_ok=True)
    bdy_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _validate_bdy_file(path):
    """Basic sanity check on an existing .bdy file."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    data = [l for l in lines if l.strip()]
    if len(data) < 4:
        raise ValueError(
            f"BDY file appears too short ({len(data)} non-blank lines). "
            "Expected: comment line, series name, count line, and at least one data row."
        )
    # Line index 2 (after comment + series name) should start with an integer count
    count_line = data[2].split()
    try:
        n = int(count_line[0])
    except (ValueError, IndexError):
        raise ValueError(
            f"BDY file: expected 'N  seconds' on line 3, got: '{data[2]}'"
        )
    if n <= 0:
        raise ValueError(f"BDY file: record count must be > 0, got {n}.")
    # Check a few data rows are parseable
    for i in range(3, min(3 + n, len(data))):
        parts = data[i].split()
        if len(parts) < 2:
            raise ValueError(f"BDY file: data row {i+1} has fewer than 2 columns: '{data[i]}'")
        try:
            float(parts[0])
            float(parts[1])
        except ValueError:
            raise ValueError(f"BDY file: non-numeric values on data row {i+1}: '{data[i]}'")


def _parse_bdy_to_dataframe(path, start_dt, dem_cell_size):
    """
    Read an existing LISFLOOD-FP .bdy file and return:
      - df:  DataFrame with columns datetime, discharge_cms
             (q_unit * cell_size converts back to m³/s)
      - series_name: the series identifier string from the file (line 2)

    The timestamps are anchored at start_dt + time_seconds from the file.
    """
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    data  = [l.strip() for l in lines if l.strip()]
    # data[0] = comment  (#project_name or similar)
    # data[1] = series name
    # data[2] = "N  seconds"
    series_name = data[1]
    n = int(data[2].split()[0])
    rows = []
    for i in range(3, 3 + n):
        parts = data[i].split()
        q_unit = float(parts[0])
        t_sec  = float(parts[1])
        rows.append((t_sec, q_unit * dem_cell_size))
    df = pd.DataFrame(rows, columns=["time_seconds", "discharge_cms"])
    t0 = pd.Timestamp(start_dt)
    df["datetime"] = t0 + pd.to_timedelta(df["time_seconds"], unit="s")
    return df[["datetime", "discharge_cms"]], series_name


# ── public API ────────────────────────────────────────────────────────────────

def create_bdy(ctx_path, ctx: dict,
               start_dt: datetime,
               end_dt: datetime,
               interval_hours: float,
               bdy_source: str,      # "existing" | "csv" | "nwm"
               existing_bdy_path: str = None,
               user_csv_path: str = None,
               gap_handling: str = "interpolate",  # "interpolate" | "as_is"
               log_fn=print):
    """Create BC.bdy file.  Returns updated ctx."""

    project_dir = Path(ctx["project_dir"])
    lisflood_dir = Path(ctx["lisflood_dir"])
    project_name = ctx["project_name"]
    dem_tif_path = Path(ctx["dem_tif_path"])
    # Use ``next_free_path`` so a re-run produces BC (1).bdy, BC (2).bdy
    # … instead of overwriting.
    from core.export import next_free_path
    bdy_path = next_free_path(lisflood_dir, "BC", "bdy")
    upstream_mode = ctx.get("upstream_mode")
    upstream_reach_id = ctx.get("upstream_reach_id")

    if upstream_mode == "fixed_discharge":
        log_fn("Upstream boundary is FIXED DISCHARGE — no BDY file needed.")
        ctx["bdy_written"] = False
        ctx["bdy_source"] = None
        save_context(ctx_path, ctx)
        return ctx

    if end_dt <= start_dt:
        raise ValueError("End date-time must be after start date-time.")

    dem_cell_size = _get_dem_cell_size(dem_tif_path)

    if bdy_source == "existing":
        if not existing_bdy_path:
            raise ValueError("Path to existing BDY file is required.")
        src = Path(existing_bdy_path)
        if not src.exists():
            raise FileNotFoundError(f"BDY file not found: {src}")

        log_fn("Validating BDY file format...")
        _validate_bdy_file(src)
        log_fn(f"BDY file validation passed  ({src.name}).")

        # Parse the file back into a discharge timeseries
        log_fn("Parsing BDY file to timeseries...")
        df_src, series_name = _parse_bdy_to_dataframe(src, start_dt, dem_cell_size)
        src_steps = len(df_src)
        src_dur   = (df_src["datetime"].iloc[-1] - df_src["datetime"].iloc[0]).total_seconds() / 3600
        log_fn(f"  Source BDY: {src_steps} time steps, "
               f"spanning {src_dur:.1f} h from {df_src['datetime'].iloc[0]} "
               f"to {df_src['datetime'].iloc[-1]}.")

        # Resample to the requested interval and event window
        start_ts = pd.Timestamp(start_dt)
        end_ts   = pd.Timestamp(end_dt)
        target_times = pd.date_range(start=start_ts, end=end_ts,
                                     freq=pd.Timedelta(hours=interval_hours))
        if len(target_times) == 0:
            raise ValueError("Interval is too large for the event window duration.")
        if target_times[-1] != end_ts:
            target_times = target_times.union(pd.DatetimeIndex([end_ts]))

        ser = pd.Series(df_src["discharge_cms"].values, index=df_src["datetime"]).sort_index()
        ser2 = (ser.reindex(ser.index.union(target_times))
                   .sort_index()
                   .interpolate(method="time")
                   .reindex(target_times))
        ser2 = ser2.clip(lower=0)

        if ser2.dropna().empty:
            raise ValueError(
                "No valid discharge values after resampling the BDY file to the requested "
                "interval. Check that the BDY file's time range covers the event window."
            )

        df_flow = pd.DataFrame({
            "datetime":      ser2.index,
            "discharge_cms": ser2.values.astype(float),
        }).reset_index(drop=True)

        log_fn(f"Resampled to {interval_hours}h interval → {len(df_flow)} time steps.")

        # Rewrite with current project name in header (fixes original filename mismatch)
        _write_bdy_file(df_flow, bdy_path, series_name, project_name, dem_cell_size)

        helper_csv = project_dir / f"{project_name}_upstream_timeseries.csv"
        df_flow.to_csv(helper_csv, index=False)
        log_fn(f"BDY written (resampled, renamed): {bdy_path}")
        ctx["bdy_source"] = "user_bdy_copy"
        ctx["user_bdy_file"] = str(src)

    elif bdy_source == "csv":
        if not user_csv_path:
            raise ValueError("Path to discharge CSV/XLSX file is required.")
        log_fn(f"Reading discharge table: {Path(user_csv_path).name}")
        df_user, has_datetimes, csv_start, csv_end = _read_user_discharge_table(user_csv_path)

        t_min = float(df_user["time_hours"].min())
        t_max = float(df_user["time_hours"].max())
        log_fn(f"  CSV has {len(df_user)} rows, time range: "
               f"{t_min:.2f} – {t_max:.2f} hours")

        # When the CSV contains datetime timestamps, use them directly
        # (overrides the user-entered start/end).
        if has_datetimes:
            start_ts = pd.Timestamp(csv_start)
            end_ts   = pd.Timestamp(csv_end)
            log_fn(f"  CSV datetimes detected: {start_ts} → {end_ts}")
        else:
            start_ts = pd.Timestamp(start_dt)
            end_ts   = pd.Timestamp(end_dt)

        # Build a datetime series from the CSV's relative time_hours column
        csv_times = start_ts + pd.to_timedelta(df_user["time_hours"].astype(float), unit="h")
        ser = pd.Series(df_user["discharge_cms"].astype(float).values, index=csv_times)
        ser = ser.sort_index()

        # Build target time grid at the requested interval
        target_times = pd.date_range(start=start_ts, end=end_ts,
                                     freq=pd.Timedelta(hours=interval_hours))
        if len(target_times) == 0:
            raise ValueError("Interval is too large for the event window duration.")
        if target_times[-1] != end_ts:
            target_times = target_times.union(pd.DatetimeIndex([end_ts]))

        if gap_handling == "interpolate":
            # Fill missing timesteps by linear interpolation
            ser2 = ser.reindex(ser.index.union(target_times)).sort_index().interpolate(method="time")
            ser2 = ser2.reindex(target_times)
            log_fn(f"  Gap handling: interpolated missing timesteps onto {interval_hours}h grid.")
        else:
            # "as_is" — only keep timesteps that already exist in the CSV
            # Snap CSV timestamps to the nearest target grid point, then drop
            # any target times that had no nearby CSV data.
            ser2 = ser.reindex(target_times, method="nearest", tolerance=pd.Timedelta(hours=interval_hours * 0.4))
            n_kept = ser2.dropna().shape[0]
            n_dropped = len(target_times) - n_kept
            if n_dropped > 0:
                log_fn(f"  Gap handling: as-is — {n_dropped} timesteps with no data will be skipped.")
            ser2 = ser2.dropna()
            if ser2.empty:
                raise ValueError(
                    "No CSV data points fall on the requested interval grid. "
                    "Try 'interpolate' instead or check the file."
                )

        # Clip to event window
        ser2 = ser2[(ser2.index >= start_ts) & (ser2.index <= end_ts)]
        if ser2.dropna().empty:
            raise ValueError(
                "No valid discharge values after resampling to the requested interval. "
                "Check that your CSV time range covers the event window."
            )

        df_flow = pd.DataFrame({"datetime": ser2.index, "discharge_cms": ser2.values.astype(float)}).reset_index(drop=True)
        log_fn(f"Output: {len(df_flow)} time steps at {interval_hours}h interval.")
        _write_bdy_file(df_flow, bdy_path, "upstream1", project_name, dem_cell_size)
        helper_csv = project_dir / f"{project_name}_upstream_timeseries.csv"
        df_flow.to_csv(helper_csv, index=False)
        log_fn(f"BDY written: {bdy_path}")
        ctx["bdy_source"] = "user_table"
        ctx["user_discharge_file"] = str(user_csv_path)

    else:  # nwm
        if upstream_reach_id is None:
            raise ValueError("upstream_reach_id not found in context. Run the BCI step first.")
        df_flow = _get_nwm_timeseries(upstream_reach_id, start_dt, end_dt, interval_hours, log_fn)
        _write_bdy_file(df_flow, bdy_path, "upstream1", project_name, dem_cell_size)
        helper_csv = project_dir / f"{project_name}_upstream_timeseries.csv"
        df_flow.to_csv(helper_csv, index=False)
        log_fn(f"BDY written from NWM: {bdy_path}")
        ctx["bdy_source"] = "NWM"

    ctx["event_start"] = start_dt.strftime("%Y-%m-%d %H:%M")
    ctx["event_end"] = end_dt.strftime("%Y-%m-%d %H:%M")
    ctx["series_interval_hours"] = interval_hours
    ctx["bdy_path"] = str(bdy_path)
    ctx["bdy_written"] = True
    save_context(ctx_path, ctx)
    return ctx
