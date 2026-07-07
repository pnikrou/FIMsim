"""NWM operational-forecast archive fetcher.

Pulls an archived NWM operational forecast trajectory for one (or many) stream
reach(es) from the Google-Cloud ``national-water-model`` bucket — the same
archive HAND-FIM / fimserve use.  Issue dates from 2018-09-17 to today.

A "forecast" is a run issued at a date + cycle hour that extends forward:
  * short_range   — ~18 h hourly
  * medium_range  — ~10 days, 3-hourly
  * long_range    — ~30 days, 6-hourly

Cycle hour = which daily run to use (medium/long publish at 00/06/12/18 UTC).
The caller usually doesn't care which run, so cycle_hour=None auto-picks the
first cycle that has data for the date (00 → 06 → 12 → 18).

We stream one forecast-hour file at a time (download → read → delete) so peak
disk stays ~15 MB even for a 10-day medium-range run.

NOTE on file names: for medium/long range the DIRECTORY is the mem1 variant
(``medium_range_mem1``) but the FILE keeps the base range name
(``nwm.tHHz.medium_range.channel_rt_1.fFFF.conus.nc``).
"""
from datetime import datetime, timedelta
import os
import tempfile

import numpy as np
import pandas as pd

_GCS = "https://storage.googleapis.com/national-water-model"
ARCHIVE_START = datetime(2018, 9, 17)


def _spec(forecast_range: str, date_obj: datetime):
    """Return (dir_name, file_range, var_tag, forecast-hour iterable)."""
    r = forecast_range.lower().replace("-", "").replace("_", "").replace(" ", "")
    if r.startswith("short"):
        return "short_range", "short_range", "channel_rt", range(1, 18)
    if r.startswith("long"):
        return "long_range_mem1", "long_range", "channel_rt_1", range(6, 720, 6)
    # medium range — file naming changed after 2019-06-18 (mem1 dir + channel_rt_1)
    if date_obj <= datetime(2019, 6, 18):
        return "medium_range", "medium_range", "channel_rt", range(3, 240, 3)
    return "medium_range_mem1", "medium_range", "channel_rt_1", range(3, 240, 3)


def _candidate_cycles(cycle_hour):
    """Cycle hours to try, in order.  When cycle_hour is given we try it first
    then fall back to the always-published cycles (00/06/12/18)."""
    base = [0, 6, 12, 18]
    if cycle_hour is None:
        return base
    c = int(cycle_hour)
    return [c] + [h for h in base if h != c]


def _validate_date(forecast_date):
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
    return date_obj


def _fetch_cycle(feature_ids, date_obj, cycle_hour, forecast_range, log_fn):
    """Download one cycle's forecast for the given reaches.

    Returns (times, records, idx_map) where records maps feature_id → list of
    discharge values aligned with times.  Returns ([], {}, {}) if the cycle has
    no files (so the caller can try another cycle).
    """
    import requests
    import netCDF4 as nc

    dir_name, file_range, var_tag, fhours = _spec(forecast_range, date_obj)
    date_str = date_obj.strftime("%Y%m%d")
    base = f"{_GCS}/nwm.{date_str}/{dir_name}"
    issue = datetime(date_obj.year, date_obj.month, date_obj.day, int(cycle_hour))

    idx_map = None
    records = {int(f): [] for f in feature_ids}
    times = []
    tmp = tempfile.mkdtemp(prefix="nwm_fc_")
    try:
        for f in fhours:
            fname = (f"nwm.t{int(cycle_hour):02d}z.{file_range}.{var_tag}."
                     f"f{f:03d}.conus.nc")
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
                    if idx_map is None:
                        fids_arr = np.asarray(ds.variables["feature_id"][:])
                        idx_map = {}
                        for fid in records:
                            w = np.where(fids_arr == fid)[0]
                            if len(w):
                                idx_map[fid] = int(w[0])
                        if not idx_map:
                            raise RuntimeError(
                                "None of the requested feature IDs are in the "
                                "NWM network — check the reach/feature ID.")
                    q = ds.variables["streamflow"][:]
                    times.append(issue + timedelta(hours=int(f)))
                    for fid, ix in idx_map.items():
                        records[fid].append(float(q[ix]))
                finally:
                    ds.close()
                if len(times) % 10 == 0:
                    log_fn(f"  … {len(times)} forecast step(s) fetched")
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

    if not times:
        return [], {}, {}
    return times, records, (idx_map or {})


def _fetch(feature_ids, forecast_date, forecast_range, cycle_hour, log_fn):
    """Try candidate cycles until one has data.  Returns (times, records, idx_map)."""
    date_obj = _validate_date(forecast_date)
    dir_name = _spec(forecast_range, date_obj)[0]
    tried = []
    for ch in _candidate_cycles(cycle_hour):
        log_fn(f"Fetching NWM {dir_name} forecast issued {date_obj.date()} "
               f"t{ch:02d}z …")
        times, records, idx_map = _fetch_cycle(
            feature_ids, date_obj, ch, forecast_range, log_fn)
        if times:
            log_fn(f"✓ Using the t{ch:02d}z run "
                   f"({len(times)} step(s), {len(idx_map)} reach(es)).")
            return times, records, idx_map
        tried.append(f"t{ch:02d}z")
    raise RuntimeError(
        f"No NWM {dir_name} forecast was found for {date_obj.date()} "
        f"(tried cycles {', '.join(tried)}).  That date may not be in the "
        "archive yet — try another date.")


def get_nwm_forecast_series(feature_id, forecast_date, forecast_range="medium_range",
                            cycle_hour=None, log_fn=print) -> pd.DataFrame:
    """Return DataFrame(datetime, discharge_cms) for one reach's forecast run.

    cycle_hour=None auto-picks the first cycle with data (00/06/12/18)."""
    fid = int(feature_id)
    times, records, _ = _fetch([fid], forecast_date, forecast_range,
                               cycle_hour, log_fn)
    df = (pd.DataFrame({"datetime": times, "discharge_cms": records[fid]})
          .sort_values("datetime").reset_index(drop=True))
    return df


def get_nwm_forecast_multi(feature_ids, forecast_date, forecast_range="medium_range",
                           cycle_hour=None, out_csv=None, log_fn=print):
    """Fetch a forecast trajectory for MANY reaches at once.  Returns a wide
    DataFrame ('datetime' + one column per feature_id); writes out_csv if given."""
    fids = [int(f) for f in feature_ids]
    times, records, idx_map = _fetch(fids, forecast_date, forecast_range,
                                     cycle_hour, log_fn)
    df = pd.DataFrame({"datetime": times})
    for fid in idx_map:
        df[str(fid)] = records[fid]
    df = df.sort_values("datetime").reset_index(drop=True)
    if out_csv:
        df.to_csv(out_csv, index=False)
    return df
