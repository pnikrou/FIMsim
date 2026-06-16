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


def _check_flow_coverage(df_flow, start_dt, end_dt, interval_hours, source_name, log_fn):
    """Check that df_flow covers the full requested window.

    Warns about:
      • data starting later than requested
      • data ending earlier than requested
      • more than 10 % of expected timesteps missing (internal gaps)

    Returns a list of plain-text warning strings (empty = full coverage).
    """
    warns = []
    if df_flow is None or df_flow.empty:
        w = f"{source_name}: No data returned for the requested period."
        warns.append(w); log_fn(f"  ⚠ WARNING: {w}")
        return warns

    start_ts = pd.Timestamp(start_dt)
    end_ts   = pd.Timestamp(end_dt)
    gap_thr  = pd.Timedelta(hours=float(interval_hours) * 1.5)

    dts = pd.to_datetime(df_flow["datetime"], errors="coerce").dropna()
    if dts.empty:
        return warns

    # Strip tz if present so comparisons work
    if dts.dt.tz is not None:
        dts = dts.dt.tz_convert(None)

    # NOTE: a record that simply starts/ends later than the requested window
    # is NOT "missing data" — the gage's period of record just begins later,
    # and the BDY file is trimmed to start at the first available timestamp.
    # We therefore do NOT warn about the leading/trailing offset here (that
    # would duplicate the trim notice).  Only genuine internal gaps below.

    # Internal gaps: compare actual row count vs expected
    expected_n = max(1, round((end_ts - start_ts).total_seconds() / 3600 / float(interval_hours))) + 1
    actual_n   = len(dts)
    if actual_n < expected_n * 0.90:
        pct = round(100 * (expected_n - actual_n) / expected_n)
        w = (f"{source_name}: ~{pct}% of expected timesteps are missing "
             f"({actual_n} of {expected_n} rows present).")
        warns.append(w); log_fn(f"  ⚠ WARNING: {w}")

    return warns


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


def _trim_nan_boundaries(df_flow, source_name, log_fn):
    """Remove leading/trailing NaN discharge rows from df_flow.

    Returns (trimmed_df, warning_strings).  The caller should append the
    warnings to ctx['bdy_warnings'] and raise if trimmed_df is empty.
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
              f"BDY file starts at {t1.strftime('%Y-%m-%d %H:%M')}.")
        warns.append(w)
        log_fn(f"  ⚠ {w}")
    trailing = len(df_flow) - last - 1
    if trailing > 0:
        t_end_orig = pd.Timestamp(df_flow["datetime"].iloc[-1])
        t_end_trim = pd.Timestamp(df_flow["datetime"].iloc[last])
        h = round((t_end_orig - t_end_trim).total_seconds() / 3600, 1)
        w  = (f"{source_name}: no discharge data for the last {h}h of the requested "
              f"period — {trailing} timestep(s) removed. "
              f"BDY file ends at {t_end_trim.strftime('%Y-%m-%d %H:%M')}.")
        warns.append(w)
        log_fn(f"  ⚠ {w}")
    return df_flow.iloc[first:last + 1].reset_index(drop=True), warns


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
               bdy_source: str,   # "existing"|"csv"|"nwm"|"nwm_retro"|"nwm_forecast"|"usgs"
               existing_bdy_path: str = None,
               user_csv_path: str = None,
               gap_handling: str = "interpolate",  # "interpolate" | "as_is"
               gage_id: str = None,                # USGS source only
               log_fn=print):
    """Create the <AOI>.bdy file.  Returns updated ctx."""

    project_dir = Path(ctx["project_dir"])
    lisflood_dir = Path(ctx["lisflood_dir"])
    project_name = ctx["project_name"]
    # Name the .bdy file after this AOI so each AOI's boundary file is
    # uniquely identifiable.  ``next_free_path`` versions a re-run as
    # "<AOI> (1).bdy", "<AOI> (2).bdy" … instead of overwriting.
    aoi_name = ctx.get("aoi_name") or project_name
    from core.export import next_free_path
    bdy_path = next_free_path(lisflood_dir, aoi_name, "bdy")
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

    _dem_path = (ctx.get("dem_tif_path") or ctx.get("dem_path") or
                 ctx.get("dem_ascii_path"))
    if not _dem_path:
        raise ValueError(
            "DEM not found in project context — make sure the DEM step "
            "completed successfully before running BDY."
        )
    dem_tif_path = Path(_dem_path)
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

        helper_csv = Path(bdy_path).parent / f"{aoi_name}_discharge.csv"
        df_flow.to_csv(helper_csv, index=False)
        ctx["bdy_helper_csv"] = str(helper_csv)
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

        # Dates come from the CSV itself — the user does not enter them.
        # If the file has datetime strings, parse directly; otherwise build
        # relative timestamps anchored to an arbitrary epoch (t=0).
        if has_datetimes:
            start_ts = pd.Timestamp(csv_start)
            end_ts   = pd.Timestamp(csv_end)
            log_fn(f"  CSV datetimes detected: {start_ts} → {end_ts}")
        else:
            # Relative-hours CSV: anchor to Unix epoch as a neutral origin.
            start_ts = pd.Timestamp("1970-01-01")
            end_ts   = start_ts + pd.Timedelta(hours=t_max)
            log_fn(f"  Relative CSV: {len(df_user)} rows, "
                   f"{t_min:.3g}h – {t_max:.3g}h")
        # Keep function-level variables aligned so ctx["event_start/end"] is correct.
        start_dt = start_ts.to_pydatetime()
        end_dt   = end_ts.to_pydatetime()

        # Build a datetime series from the CSV's relative time_hours column
        csv_times = start_ts + pd.to_timedelta(df_user["time_hours"].astype(float), unit="h")
        ser = pd.Series(df_user["discharge_cms"].astype(float).values, index=csv_times)
        ser = ser.sort_index()

        # ── Detect actual CSV interval and log it ──────────────────────────
        csv_interval_h = None
        if len(ser) >= 2:
            diffs = ser.index.to_series().diff().dropna()
            csv_interval_h = diffs.median().total_seconds() / 3600.0
            log_fn(
                f"  Detected CSV time interval: ~{csv_interval_h:.3g}h "
                f"(requested output interval: {interval_hours}h)"
            )

        # Build target time grid at the requested interval
        target_times = pd.date_range(start=start_ts, end=end_ts,
                                     freq=pd.Timedelta(hours=interval_hours))
        if len(target_times) == 0:
            raise ValueError("Interval is too large for the event window duration.")
        if target_times[-1] != end_ts:
            target_times = target_times.union(pd.DatetimeIndex([end_ts]))

        # ── Choose resampling strategy ──────────────────────────────────────
        if csv_interval_h is not None and csv_interval_h < interval_hours * 0.9:
            # CSV is finer-resolution than requested → aggregate by mean
            log_fn(
                f"  CSV has finer resolution ({csv_interval_h:.3g}h) than "
                f"requested ({interval_hours}h) — aggregating by mean."
            )
            freq = pd.Timedelta(hours=float(interval_hours))
            ser_agg = ser.resample(freq, origin=start_ts).mean()
            # Align to the exact target grid (should already match, but snap for safety)
            ser2 = ser_agg.reindex(target_times, method="nearest",
                                   tolerance=freq * 0.6)
        elif gap_handling == "interpolate":
            # Same or coarser resolution → interpolate to fill any gaps
            ser2 = (ser.reindex(ser.index.union(target_times))
                       .sort_index()
                       .interpolate(method="time"))
            ser2 = ser2.reindex(target_times)
            log_fn(f"  Gap handling: interpolated missing timesteps onto "
                   f"{interval_hours}h grid.")
        else:
            # "as_is" — snap CSV timestamps to nearest target grid point
            ser2 = ser.reindex(target_times, method="nearest",
                               tolerance=pd.Timedelta(hours=interval_hours * 0.4))
            n_kept    = ser2.dropna().shape[0]
            n_dropped = len(target_times) - n_kept
            if n_dropped > 0:
                log_fn(f"  Gap handling: as-is — {n_dropped} timestep(s) with "
                       f"no nearby CSV data will be skipped.")
            ser2 = ser2.dropna()
            if ser2.empty:
                raise ValueError(
                    "No CSV data points fall on the requested interval grid. "
                    "Try 'Interpolate' instead or check the file."
                )

        # Clip to event window
        ser2 = ser2[(ser2.index >= start_ts) & (ser2.index <= end_ts)]
        if ser2.dropna().empty:
            raise ValueError(
                "No valid discharge values after resampling to the requested interval. "
                "Check that the CSV contains data and the interval is not larger than "
                "the file's total duration."
            )

        df_flow = pd.DataFrame({
            "datetime":      ser2.index,
            "discharge_cms": ser2.values.astype(float),
        }).reset_index(drop=True)
        actual_start = df_flow["datetime"].iloc[0]
        actual_end   = df_flow["datetime"].iloc[-1]
        log_fn(
            f"  Output: {len(df_flow)} time steps at {interval_hours}h interval "
            f"({actual_start} → {actual_end})."
        )
        if csv_interval_h is not None and abs(csv_interval_h - interval_hours) > 0.05:
            log_fn(
                f"  Note: CSV native interval ({csv_interval_h:.3g}h) resampled "
                f"to requested {interval_hours}h."
            )
        # No coverage check for CSV — dates come from the file, not a user window.

        _write_bdy_file(df_flow, bdy_path, "upstream1", project_name, dem_cell_size)
        helper_csv = Path(bdy_path).parent / f"{aoi_name}_discharge.csv"
        df_flow.to_csv(helper_csv, index=False)
        ctx["bdy_helper_csv"] = str(helper_csv)
        log_fn(f"BDY written: {bdy_path}")
        ctx["bdy_source"] = "user_table"
        ctx["user_discharge_file"] = str(user_csv_path)

    elif bdy_source == "usgs":
        # ── USGS instantaneous-value download ─────────────────────────────
        if not gage_id:
            raise ValueError("gage_id is required when bdy_source='usgs'.")
        log_fn(f"Downloading USGS discharge for gage {gage_id} …")
        from core.flowline_mode import _download_usgs_discharge
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = _download_usgs_discharge(
                gage_ids=[gage_id],
                start_dt=start_dt,
                end_dt=end_dt,
                out_folder=Path(tmpdir),
                interval_hours=interval_hours,
                log_fn=log_fn,
            )
            if not paths:
                raise RuntimeError(
                    f"No data returned for USGS gage {gage_id} for the "
                    f"requested period ({start_dt} → {end_dt}).\n"
                    "Check the gage number at waterdata.usgs.gov/nwis/rt "
                    "and verify the date range has data."
                )
            df_raw = pd.read_csv(paths[0])

        # Normalise columns — file has datetime as index label
        if "datetime" not in df_raw.columns and df_raw.index.name == "datetime":
            df_raw = df_raw.reset_index()
        q_col = next((c for c in ("streamflow_m3s", "discharge_cms")
                      if c in df_raw.columns), None)
        if q_col is None:
            raise RuntimeError(
                f"USGS CSV for gage {gage_id} is missing a discharge column."
            )
        df_raw["datetime"] = pd.to_datetime(df_raw["datetime"], errors="coerce")
        df_raw = df_raw.dropna(subset=["datetime"])
        df_raw = df_raw.rename(columns={q_col: "discharge_cms"})
        # Build a proper datetime-indexed Series so _resample_to_interval works
        _dt_idx = pd.DatetimeIndex(df_raw["datetime"])
        if _dt_idx.tz is not None:
            _dt_idx = _dt_idx.tz_convert(None)   # strip tz (UTC → naive UTC)
        ser = pd.Series(df_raw["discharge_cms"].astype(float).values, index=_dt_idx)
        start_ts = pd.Timestamp(start_dt)
        end_ts   = pd.Timestamp(end_dt)
        df_flow  = _resample_to_interval(ser, start_ts, end_ts, interval_hours)
        _bdy_warns = ctx.get("bdy_warnings", [])
        df_flow, trim_warns = _trim_nan_boundaries(df_flow, f"USGS gage {gage_id}", log_fn)
        if df_flow.empty:
            ctx["bdy_warnings"] = _bdy_warns
            raise RuntimeError(
                f"USGS gage {gage_id}: no valid discharge data for the entire requested "
                f"period ({start_dt} → {end_dt}). Cannot write BDY file."
            )
        cov_warns = _check_flow_coverage(df_flow, start_dt, end_dt,
                                         interval_hours, f"USGS gage {gage_id}", log_fn)
        # A record that merely starts/ends later than requested is normal —
        # only surface the leading/trailing trim notice when there are ALSO
        # genuine internal gaps (otherwise no data is actually "missing").
        if cov_warns:
            _bdy_warns.extend(trim_warns)
            _bdy_warns.extend(cov_warns)
        ctx["bdy_warnings"] = _bdy_warns
        _write_bdy_file(df_flow, bdy_path, "upstream1", project_name, dem_cell_size)
        helper_csv = Path(bdy_path).parent / f"USGS_{gage_id}_discharge.csv"
        df_flow.to_csv(helper_csv, index=False)
        ctx["bdy_helper_csv"] = str(helper_csv)
        log_fn(f"BDY written from USGS gage {gage_id}: {bdy_path}")
        ctx["bdy_source"] = "USGS"

    else:  # "nwm" | "nwm_retro" | "nwm_forecast"
        if upstream_reach_id is None:
            raise ValueError("upstream_reach_id not found in context. Run the BCI step first.")
        start_ts = pd.Timestamp(start_dt)
        end_ts   = pd.Timestamp(end_dt)
        if bdy_source == "nwm_forecast":
            df_flow = _get_nwm_forecast(
                upstream_reach_id, start_ts, end_ts, interval_hours, log_fn
            )
            src_label = "NWM Forecast"
        elif bdy_source == "nwm_retro":
            df_flow = _get_nwm_retrospective(
                upstream_reach_id, start_ts, end_ts, interval_hours, log_fn
            )
            src_label = "NWM Retrospective"
        else:  # legacy "nwm" — auto-pick retro vs forecast by date
            df_flow   = _get_nwm_timeseries(
                upstream_reach_id, start_dt, end_dt, interval_hours, log_fn
            )
            src_label = "NWM"
        _bdy_warns = ctx.get("bdy_warnings", [])
        df_flow, trim_warns = _trim_nan_boundaries(df_flow, src_label, log_fn)
        if df_flow.empty:
            ctx["bdy_warnings"] = _bdy_warns
            raise RuntimeError(
                f"{src_label}: no valid discharge data for the requested period "
                f"({start_dt} → {end_dt}). Cannot write BDY file."
            )
        cov_warns = _check_flow_coverage(df_flow, start_dt, end_dt,
                                         interval_hours, src_label, log_fn)
        # Only surface the leading/trailing trim notice when there are ALSO
        # genuine internal gaps (a later record start/end is not missing data).
        if cov_warns:
            _bdy_warns.extend(trim_warns)
            _bdy_warns.extend(cov_warns)
        ctx["bdy_warnings"] = _bdy_warns
        _write_bdy_file(df_flow, bdy_path, "upstream1", project_name, dem_cell_size)
        helper_csv = Path(bdy_path).parent / f"NWM_{upstream_reach_id}_discharge.csv"
        df_flow.to_csv(helper_csv, index=False)
        ctx["bdy_helper_csv"] = str(helper_csv)
        log_fn(f"BDY written from {src_label}: {bdy_path}")
        ctx["bdy_source"] = src_label

    ctx["event_start"] = start_dt.strftime("%Y-%m-%d %H:%M")
    ctx["event_end"] = end_dt.strftime("%Y-%m-%d %H:%M")
    ctx["series_interval_hours"] = interval_hours
    ctx["bdy_path"] = str(bdy_path)
    ctx["bdy_written"] = True
    save_context(ctx_path, ctx)
    return ctx
