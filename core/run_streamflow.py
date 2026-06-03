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

    for cfg in configs:
        source = cfg.get("source", "")
        ids_raw = cfg.get("ids", "")
        start_dt = cfg.get("start_dt")
        end_dt = cfg.get("end_dt")
        interval_hours = float(cfg.get("interval_hours", 1.0))

        if source in ("nwm_retro", "nwm_forecast"):
            feature_ids = _coerce_feature_ids(ids_raw)
            if not feature_ids:
                log_fn(f"  ⚠ No feature IDs for source={source}, skipping.")
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
                    log_fn(f"  ❌ NWM retrospective download failed: {exc}")
                    continue

                # Split the wide CSV into one CSV per feature ID
                try:
                    df_wide = pd.read_csv(combined_csv)
                    for fid in feature_ids:
                        col = str(fid)
                        if col not in df_wide.columns:
                            log_fn(f"  ⚠ Feature ID {fid} not found in result CSV.")
                            continue
                        df_single = df_wide[["datetime", col]].copy()
                        df_single.columns = ["datetime", "streamflow_m3s"]
                        csv_path = out_dir / f"nwm_retro_{fid}.csv"
                        df_single.to_csv(csv_path, index=False)
                        n_ts = len(df_single.dropna(subset=["streamflow_m3s"]))
                        try:
                            peak = float(df_single["streamflow_m3s"].max())
                        except Exception:
                            peak = None
                        results.append({
                            "source": "nwm_retro",
                            "id": str(fid),
                            "csv_path": str(csv_path),
                            "n_timesteps": n_ts,
                            "peak_flow_cms": peak,
                        })
                        log_fn(f"  ✓ Saved {csv_path.name} ({n_ts} rows)")
                    # Remove combined CSV
                    try:
                        combined_csv.unlink()
                    except Exception:
                        pass
                except Exception as exc:
                    log_fn(f"  ❌ Error splitting NWM retro result: {exc}")

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
                    log_fn(f"  ❌ NWM forecast download failed: {exc}")
                    continue

                try:
                    df_wide = pd.read_csv(combined_csv)
                    for fid in feature_ids:
                        col = str(fid)
                        if col not in df_wide.columns:
                            log_fn(f"  ⚠ Feature ID {fid} not found in forecast CSV.")
                            continue
                        df_single = df_wide[["datetime", col]].copy()
                        df_single.columns = ["datetime", "streamflow_m3s"]
                        csv_path = out_dir / f"nwm_forecast_{fid}.csv"
                        df_single.to_csv(csv_path, index=False)
                        n_ts = len(df_single.dropna(subset=["streamflow_m3s"]))
                        try:
                            peak = float(df_single["streamflow_m3s"].max())
                        except Exception:
                            peak = None
                        results.append({
                            "source": "nwm_forecast",
                            "id": str(fid),
                            "csv_path": str(csv_path),
                            "n_timesteps": n_ts,
                            "peak_flow_cms": peak,
                        })
                        log_fn(f"  ✓ Saved {csv_path.name} ({n_ts} rows)")
                    try:
                        combined_csv.unlink()
                    except Exception:
                        pass
                except Exception as exc:
                    log_fn(f"  ❌ Error splitting NWM forecast result: {exc}")

        elif source == "usgs":
            gage_ids = _coerce_gage_ids(ids_raw)
            if not gage_ids:
                log_fn("  ⚠ No gage IDs for source=usgs, skipping.")
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
                log_fn(f"  ❌ USGS download failed: {exc}")
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
                    log_fn(f"  ⚠ No saved file found for USGS gage {gid}.")
                    continue
                csv_path = Path(csv_path_str)
                n_ts = 0
                peak = None
                try:
                    df = pd.read_csv(csv_path)
                    q_col = next(
                        (c for c in ("streamflow_m3s", "discharge_cms")
                         if c in df.columns), None
                    )
                    if q_col:
                        n_ts = len(df.dropna(subset=[q_col]))
                        peak = float(df[q_col].max())
                except Exception:
                    pass
                results.append({
                    "source": "usgs",
                    "id": gid,
                    "csv_path": str(csv_path),
                    "n_timesteps": n_ts,
                    "peak_flow_cms": peak,
                })
        else:
            log_fn(f"  ⚠ Unknown source '{source}', skipping.")

    log_fn(f"Streamflow download complete: {len(results)} time series saved.")
    return {"results": results}
