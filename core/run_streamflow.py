"""Standalone streamflow download backend.

Entry point:
  run_streamflow_mode  — download NWM retrospective, NWM forecast, or USGS
                         discharge for given feature IDs / gage numbers.
"""
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

import numpy as np
import pandas as pd

from core.nwm_discharge import (
    download_nwm_retrospective,
    download_nwm_forecast,
    _coerce_feature_ids,
)
from core.flowline_mode import _download_usgs_discharge, _coerce_gage_ids


def _to_time_hours_csv(df: pd.DataFrame, datetime_col: str, flow_col: str, out_path) -> pd.DataFrame:
    """Convert a datetime+flow DataFrame to time_hours/discharge_cms and save as CSV."""
    out = df[[datetime_col, flow_col]].copy()
    dt = pd.to_datetime(out[datetime_col], utc=True, errors="coerce")
    out["time_hours"] = (dt - dt.iloc[0]).dt.total_seconds() / 3600.0
    out["discharge_cms"] = out[flow_col].astype(float)
    out = out[["time_hours", "discharge_cms"]]
    out.to_csv(out_path, index=False)
    return out


def _check_coverage(df, dt_col, req_start, req_end, interval_hours, label, log_fn):
    """Warn if the downloaded data doesn't cover the full requested date range.

    Returns a list of warning strings (empty if full coverage).
    """
    warnings = []
    try:
        dates = pd.to_datetime(df[dt_col], errors="coerce").dropna()
        if dates.empty:
            return warnings
        # Strip timezone from all timestamps so comparisons always work
        actual_start = dates.min()
        actual_end   = dates.max()
        if hasattr(actual_start, "tzinfo") and actual_start.tzinfo is not None:
            actual_start = actual_start.tz_convert(None)
        if hasattr(actual_end, "tzinfo") and actual_end.tzinfo is not None:
            actual_end = actual_end.tz_convert(None)
        _req_s = pd.Timestamp(req_start)
        req_s  = _req_s.tz_convert(None) if _req_s.tzinfo else _req_s
        _req_e = pd.Timestamp(req_end)
        req_e  = _req_e.tz_convert(None) if _req_e.tzinfo else _req_e
        threshold = pd.Timedelta(hours=float(interval_hours) * 1.5)
        if (actual_start - req_s) > threshold:
            msg = (
                f"No data available before {actual_start.strftime('%Y-%m-%d')} "
                f"(requested from {req_s.strftime('%Y-%m-%d')}). "
                f"Downloaded from first available record."
            )
            warnings.append(msg)
            log_fn(f"  ⚠ WARNING ({label}): {msg}")
        if (req_e - actual_end) > threshold:
            msg = (
                f"No data available after {actual_end.strftime('%Y-%m-%d')} "
                f"(requested until {req_e.strftime('%Y-%m-%d')}). "
                f"Downloaded up to last available record."
            )
            warnings.append(msg)
            log_fn(f"  ⚠ WARNING ({label}): {msg}")
    except Exception:
        pass
    return warnings


def run_streamflow_mode(
    project_dir: str,
    configs: List[Dict[str, Any]],
    log_fn=print,
) -> Dict:
    """Download streamflow data from NWM retrospective, NWM forecast, or USGS.

    Each config dict has:
        source         : "nwm_retro" | "nwm_forecast" | "usgs"
        ids            : str | list  — comma-sep COMIDs/gages or path to CSV
        start_dt       : datetime
        end_dt         : datetime
        interval_hours : float (NWM retro / USGS only, default 1.0)

    Returns:
        {"results": [{"source", "id", "csv_path", "n_timesteps", "peak_flow_cms"}]}
    """
    out_dir = Path(project_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    all_warnings: list = []

    for cfg in configs:
        source = cfg.get("source", "")
        ids_raw = cfg.get("ids", "")
        start_dt = cfg.get("start_dt")
        end_dt = cfg.get("end_dt")
        interval_hours = float(cfg.get("interval_hours", 1.0))

        if source in ("nwm_retro", "nwm_forecast"):
            feature_ids = _coerce_feature_ids(ids_raw)
            if not feature_ids:
                log_fn(f"  No feature IDs for source={source}, skipping.")
                continue

            if source == "nwm_retro":
                # Download all IDs into one CSV, then split per feature
                combined_csv = out_dir / f"nwm_retro_combined.csv"
                log_fn(
                    f"Downloading NWM retrospective for {len(feature_ids)} "
                    f"feature ID(s) …"
                )
                try:
                    download_nwm_retrospective(
                        feature_ids=feature_ids,
                        start_dt=start_dt,
                        end_dt=end_dt,
                        interval_hours=interval_hours,
                        out_csv=combined_csv,
                        log_fn=log_fn,
                    )
                except Exception as exc:
                    log_fn(f"  NWM retrospective download failed: {exc}")
                    continue

                # Split the wide CSV into one CSV per feature ID
                try:
                    df_wide = pd.read_csv(combined_csv)
                except Exception as exc:
                    log_fn(f"  Error reading combined NWM retro CSV: {exc}")
                    df_wide = None

                if df_wide is not None:
                    for fid in feature_ids:
                        col = str(fid)
                        if col not in df_wide.columns:
                            log_fn(f"  Feature ID {fid} not found in result CSV.")
                            continue
                        csv_path = out_dir / f"nwm_retro_{fid}.csv"
                        try:
                            df_single = _to_time_hours_csv(df_wide, "datetime", col, csv_path)
                            n_ts = len(df_single.dropna(subset=["discharge_cms"]))
                            try:
                                peak = float(df_single["discharge_cms"].max())
                            except Exception:
                                peak = None
                            # Coverage check
                            cov_warns = _check_coverage(df_single, "datetime", start_dt, end_dt,
                                            interval_hours, f"NWM Retro feature {fid}", log_fn)
                            all_warnings.extend(
                                f"NWM Retro {fid}: {w}" for w in cov_warns
                            )
                            results.append({
                                "source": "nwm_retro",
                                "id": str(fid),
                                "csv_path": str(csv_path),
                                "n_timesteps": n_ts,
                                "peak_flow_cms": peak,
                            })
                            log_fn(f"  ✓ Saved {csv_path.name} ({n_ts} rows)")
                        except Exception as exc:
                            log_fn(f"  Error processing feature ID {fid}: {exc}")
                            # File may have been partially written — recover from disk
                            if csv_path.exists():
                                try:
                                    df_rec = pd.read_csv(csv_path)
                                    q_col = next((c for c in ("discharge_cms", "streamflow_m3s")
                                                  if c in df_rec.columns), None)
                                    n_ts = len(df_rec.dropna(subset=[q_col])) if q_col else 0
                                    peak = float(df_rec[q_col].max()) if q_col else None
                                except Exception:
                                    n_ts, peak = 0, None
                                results.append({
                                    "source": "nwm_retro",
                                    "id": str(fid),
                                    "csv_path": str(csv_path),
                                    "n_timesteps": n_ts,
                                    "peak_flow_cms": peak,
                                })
                # Remove combined CSV
                try:
                    combined_csv.unlink()
                except Exception:
                    pass

            else:  # nwm_forecast
                combined_csv = out_dir / f"nwm_forecast_combined.csv"
                log_fn(
                    f"Downloading NWM forecast for {len(feature_ids)} "
                    f"feature ID(s) …"
                )
                try:
                    download_nwm_forecast(
                        feature_ids=feature_ids,
                        start_dt=start_dt,
                        end_dt=end_dt,
                        out_csv=combined_csv,
                        log_fn=log_fn,
                    )
                except Exception as exc:
                    log_fn(f"  NWM forecast download failed: {exc}")
                    continue

                try:
                    df_wide = pd.read_csv(combined_csv)
                except Exception as exc:
                    log_fn(f"  Error reading combined NWM forecast CSV: {exc}")
                    df_wide = None

                if df_wide is not None:
                    for fid in feature_ids:
                        col = str(fid)
                        if col not in df_wide.columns:
                            log_fn(f"  Feature ID {fid} not found in forecast CSV.")
                            continue
                        csv_path = out_dir / f"nwm_forecast_{fid}.csv"
                        try:
                            df_single = _to_time_hours_csv(df_wide, "datetime", col, csv_path)
                            n_ts = len(df_single.dropna(subset=["discharge_cms"]))
                            try:
                                peak = float(df_single["discharge_cms"].max())
                            except Exception:
                                peak = None
                            # Coverage check
                            cov_warns = _check_coverage(df_single, "datetime", start_dt, end_dt,
                                            interval_hours, f"NWM Forecast feature {fid}", log_fn)
                            all_warnings.extend(
                                f"NWM Forecast {fid}: {w}" for w in cov_warns
                            )
                            results.append({
                                "source": "nwm_forecast",
                                "id": str(fid),
                                "csv_path": str(csv_path),
                                "n_timesteps": n_ts,
                                "peak_flow_cms": peak,
                            })
                            log_fn(f"  ✓ Saved {csv_path.name} ({n_ts} rows)")
                        except Exception as exc:
                            log_fn(f"  Error processing forecast feature ID {fid}: {exc}")
                            if csv_path.exists():
                                try:
                                    df_rec = pd.read_csv(csv_path)
                                    q_col = next((c for c in ("discharge_cms", "streamflow_m3s")
                                                  if c in df_rec.columns), None)
                                    n_ts = len(df_rec.dropna(subset=[q_col])) if q_col else 0
                                    peak = float(df_rec[q_col].max()) if q_col else None
                                except Exception:
                                    n_ts, peak = 0, None
                                results.append({
                                    "source": "nwm_forecast",
                                    "id": str(fid),
                                    "csv_path": str(csv_path),
                                    "n_timesteps": n_ts,
                                    "peak_flow_cms": peak,
                                })
                try:
                    combined_csv.unlink()
                except Exception:
                    pass

        elif source == "usgs":
            gage_ids = _coerce_gage_ids(ids_raw)
            if not gage_ids:
                log_fn("  No gage IDs for source=usgs, skipping.")
                continue
            log_fn(
                f"Downloading USGS discharge for {len(gage_ids)} gage(s) …"
            )
            try:
                saved_paths = _download_usgs_discharge(
                    gage_ids=gage_ids,
                    start_dt=start_dt,
                    end_dt=end_dt,
                    out_folder=out_dir,
                    interval_hours=interval_hours,
                    log_fn=log_fn,
                )
            except Exception as exc:
                log_fn(f"  USGS download failed: {exc}")
                continue

            # Map saved paths back to gage IDs
            saved_by_site = {}
            for p in (saved_paths or []):
                stem = Path(p).stem  # e.g. "usgs_discharge_01234567"
                parts = stem.split("_")
                site_id = parts[-1] if parts else stem
                saved_by_site[site_id] = p

            for gid in gage_ids:
                # Try to find matching saved file
                csv_path_str = saved_by_site.get(gid)
                if not csv_path_str:
                    # Try zero-padded variants
                    for k, v in saved_by_site.items():
                        if k.lstrip("0") == gid.lstrip("0"):
                            csv_path_str = v
                            break

                if not csv_path_str:
                    # Gage returned no data — still add to results so GUI can report it
                    log_fn(f"  ✗ No data returned for USGS gage {gid} (not available for requested period).")
                    results.append({
                        "source": "usgs",
                        "id": gid,
                        "csv_path": None,
                        "n_timesteps": 0,
                        "peak_flow_cms": None,
                        "status": "unavailable",
                        "warnings": [],
                    })
                    continue

                csv_path = Path(csv_path_str)
                n_ts = 0
                peak = None
                gid_warnings: list = []
                try:
                    df = pd.read_csv(csv_path)
                    q_col = next(
                        (c for c in ("discharge_cms", "streamflow_m3s")
                         if c in df.columns), None
                    )
                    if q_col:
                        n_ts = len(df.dropna(subset=[q_col]))
                        peak = float(df[q_col].max())
                    # Coverage check: warn if data doesn't span the requested range
                    cov_warns = _check_coverage(
                        df, "datetime", start_dt, end_dt,
                        interval_hours, f"USGS gage {gid}", log_fn,
                    )
                    gid_warnings.extend(cov_warns)
                    all_warnings.extend(
                        f"USGS gage {gid}: {w}" for w in cov_warns
                    )
                except Exception:
                    pass
                results.append({
                    "source": "usgs",
                    "id": gid,
                    "csv_path": str(csv_path),
                    "n_timesteps": n_ts,
                    "peak_flow_cms": peak,
                    "status": "ok",
                    "warnings": gid_warnings,
                })
        else:
            log_fn(f"  Unknown source '{source}', skipping.")

    n_ok = sum(1 for r in results if r.get("status") != "unavailable")
    n_fail = len(results) - n_ok
    msg = f"Streamflow download complete: {n_ok} time series saved."
    if n_fail:
        msg += f"  {n_fail} gage(s) had no data for the requested period."
    log_fn(msg)
    return {"results": results, "warnings": all_warnings}
