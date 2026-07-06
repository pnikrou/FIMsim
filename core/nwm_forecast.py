"""NWM operational-forecast archive fetcher (single reach).

Pulls an archived NWM operational forecast trajectory for ONE stream reach
(feature_id) from the Google-Cloud ``national-water-model`` bucket — the same
archive HAND-FIM / fimserve use.  Issue dates from 2018-09-17 to today.

A "forecast" is a run issued at a date + cycle hour that extends forward:
  * short_range   — ~18 h hourly
  * medium_range  — ~10 days, 3-hourly
  * long_range    — ~30 days, 6-hourly

We stream one forecast-hour file at a time (download → read the reach → delete)
so peak disk stays ~15 MB even for a 10-day medium-range run.
"""
from datetime import datetime, timedelta
import os
import tempfile

import numpy as np
import pandas as pd

_GCS = "https://storage.googleapis.com/national-water-model"
ARCHIVE_START = datetime(2018, 9, 17)


def _spec(forecast_range: str, date_obj: datetime):
    """Return (forecast_type, channel_rt variable tag, forecast-hour iterable)."""
    r = forecast_range.lower().replace("-", "").replace("_", "").replace(" ", "")
    if r.startswith("short"):
        return "short_range", "channel_rt", range(1, 18)          # f001..f017 hourly
    if r.startswith("long"):
        return "long_range_mem1", "channel_rt_1", range(6, 720, 6)  # 6-hourly, ~30 d
    # medium range — the file naming changed after 2019-06-18 (mem1 + channel_rt_1)
    if date_obj <= datetime(2019, 6, 18):
        return "medium_range", "channel_rt", range(3, 240, 3)
    return "medium_range_mem1", "channel_rt_1", range(3, 240, 3)   # 3-hourly, ~10 d


def valid_cycle_hours(forecast_range: str):
    """Cycle (init) hours the NWM publishes for a range."""
    r = forecast_range.lower()
    if r.startswith("short"):
        return list(range(24))          # hourly
    return [0, 6, 12, 18]               # medium / long: 4 cycles a day


def get_nwm_forecast_series(feature_id, forecast_date, forecast_range="medium_range",
                            cycle_hour=0, log_fn=print) -> pd.DataFrame:
    """Return DataFrame(datetime, discharge_cms) for the reach's forecast run.

    Raises RuntimeError with a clear, user-facing message when the date is
    outside the archive or the run/reach isn't found.
    """
    import requests
    import netCDF4 as nc

    feature_id = int(feature_id)
    date_obj = pd.Timestamp(forecast_date).to_pydatetime().replace(
        hour=0, minute=0, second=0, microsecond=0)

    if date_obj < ARCHIVE_START:
        raise RuntimeError(
            f"NWM operational forecast data is only available from "
            f"{ARCHIVE_START.date()} onward.  Forecast date {date_obj.date()} "
            "is before that — no data available for this time.")
    if date_obj.date() > datetime.utcnow().date():
        raise RuntimeError(
            f"Forecast date {date_obj.date()} is in the future — "
            "no NWM data available yet for this time.")

    cycle_hour = int(cycle_hour)
    date_str = date_obj.strftime("%Y%m%d")
    ftype, var_tag, fhours = _spec(forecast_range, date_obj)
    base = f"{_GCS}/nwm.{date_str}/{ftype}"
    issue = datetime(date_obj.year, date_obj.month, date_obj.day, cycle_hour)

    log_fn(f"Fetching NWM {ftype} forecast issued {date_obj.date()} "
           f"t{cycle_hour:02d}z for reach {feature_id} …")

    rows = []
    fid_index = None
    tmp = tempfile.mkdtemp(prefix="nwm_fc_")
    try:
        for f in fhours:
            fname = f"nwm.t{cycle_hour:02d}z.{ftype}.{var_tag}.f{f:03d}.conus.nc"
            url = f"{base}/{fname}"
            fpath = os.path.join(tmp, fname)
            try:
                resp = requests.get(url, timeout=120)
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
                with open(fpath, "wb") as fp:
                    fp.write(resp.content)
                ds = nc.Dataset(fpath)
                try:
                    if fid_index is None:
                        fids = np.asarray(ds.variables["feature_id"][:])
                        where = np.where(fids == feature_id)[0]
                        if len(where) == 0:
                            raise RuntimeError(
                                f"feature_id {feature_id} is not in the NWM "
                                "network — check the reach/feature ID.")
                        fid_index = int(where[0])
                    q = float(ds.variables["streamflow"][fid_index])
                finally:
                    ds.close()
                rows.append((issue + timedelta(hours=int(f)), q))
                if len(rows) % 10 == 0:
                    log_fn(f"  … {len(rows)} forecast step(s) fetched")
            except requests.RequestException:
                continue
            finally:
                try:
                    os.remove(fpath)
                except OSError:
                    pass
    finally:
        try:
            os.rmdir(tmp)
        except OSError:
            pass

    if not rows:
        raise RuntimeError(
            f"No NWM {ftype} forecast files were found for {date_obj.date()} "
            f"cycle t{cycle_hour:02d}z.  The run may not exist for that date / "
            "hour — try a different cycle hour (0/6/12/18) or another date.")

    df = (pd.DataFrame(rows, columns=["datetime", "discharge_cms"])
          .sort_values("datetime").reset_index(drop=True))
    log_fn(f"✓ NWM {ftype} forecast: {len(df)} step(s) for reach {feature_id}.")
    return df
