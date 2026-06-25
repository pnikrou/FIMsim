"""
Author: Supath Dhital (sdhital@crimson.ua.edu)
Date updated: June 2026

This is a workflow for the Flood Inundation Mapping (FIM) generation using NOAA Office of Water Prediction (OWP) Height Above Nearest Drainage (HAND)
method by leveraging the FIM as a Service (FIMserv) python framework.

This will allow users to generate flood inundation maps for a given area of interest OR with Hydrologic Unit Codes (HUCs)8 code
within United States by providing the necessary input data and parameters. It can generate retrospective and forecast FIMs by using NWM discharge data.
"""

import os
import re
import glob
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Union

import numpy as np
import geopandas as gpd
import rasterio
from rasterio.merge import merge as rio_merge
from rasterio.mask import mask as rio_mask


from core.aoi_info import lookup_huc8

# NWM v3.0 retrospective runs up to early 2023; on/after this date use the
# operational forecast instead.
FORECAST_CUTOFF = datetime(2023, 1, 1)


def _import_fimserve():
    # Making sure it is imported
    try:
        import fimserve as fm
    except ImportError:
        raise ImportError(
            "fimserve is not installed.  Install it with: uv pip install fimserve"
        )
    return fm


class FIMservAPI:
    """OWP HAND FIM workflow driven by FIMserv.
    """

    def __init__(self, project_dir: Union[str, Path], log_fn=print):
        self.project_dir = Path(project_dir).resolve()
        self.log = log_fn
        self.project_dir.mkdir(parents=True, exist_ok=True)
        os.chdir(str(self.project_dir))
        self.output_dir = self.project_dir / "output"

    # AOI -> HUC8 IDs
    def get_huc8_ids(self, aoi_path: Union[str, Path]) -> List[str]:
        """Return the list of HUC8 IDs that intersect the AOI.
        """
        aoi_path = str(aoi_path)
        self.log(f"Resolving HUC8 IDs for {Path(aoi_path).name} …")

        codes: List[str] = []
        try:
            fm = _import_fimserve()
            result = fm.getIntersectedHUC8ID(aoi_path)
            codes = self._parse_huc8_ids(result)
        except Exception as exc:
            self.log(f"  FIMserv HUC8 lookup failed ({exc}) — using bundled lookup.")

        # Fallback: bundled us_huc8.geojson / pynhd lookup (feature 0).
        if not codes:
            codes = lookup_huc8(aoi_path, 0, log_fn=self.log)

        codes = sorted({str(c).zfill(8) for c in codes})
        if codes:
            self.log(f"  HUC8 IDs: {', '.join(codes)}")
        else:
            self.log("  No HUC8 IDs found for this AOI.")
        return codes

    @staticmethod
    def _parse_huc8_ids(result) -> List[str]:
        # getIntersectedHUC8ID returns a human-readable string; pull every
        # 8-digit run out of it.  Accept a list/tuple too in case the API
        # changes to return structured data.
        if result is None:
            return []
        if isinstance(result, (list, tuple, set)):
            return [str(c).zfill(8) for c in result]
        ids = re.findall(r"\b\d{8}\b", str(result))
        return ids

    # Download HUC8 terrain and hydro data for all intersecting HUC8s based on user defined AOI
    def download_huc8(self, huc8_ids: List[str], force: bool = False) -> List[str]:
        """Download OWP HAND inputs for each HUC8.  Returns the IDs that succeeded.

        Already-downloaded HUC8s are skipped so re-opening a project does not
        re-download data.  Pass ``force=True`` to download regardless.
        """
        fm = _import_fimserve()
        ok: List[str] = []
        for i, huc in enumerate(huc8_ids, 1):
            try:
                if not force and self.is_huc8_downloaded(huc):
                    self.log(f"HUC8 [{i}/{len(huc8_ids)}] already downloaded — skipping: {huc}")
                    ok.append(huc)
                    continue
                self.log(f"Downloading HUC8 [{i}/{len(huc8_ids)}]: {huc} …")
                fm.DownloadHUC8(huc)
                ok.append(huc)
                self.log(f"HUC8 [{i}/{len(huc8_ids)}] downloaded: {huc}")
            except Exception as exc:
                self.log(f"HUC8 [{i}/{len(huc8_ids)}] failed for {huc}: {exc}")
        return ok

    # NWM discharge (retrospective and forecast by date user input)
    def prepare_discharge(
        self,
        huc8_ids: List[str],
        source: str = "retrospective",
        start_date=None,
        end_date=None,
        value_times: Optional[List[str]] = None,
        sort_by: Optional[str] = None,
        forecast_range: str = "mediumrange",
        forecast_date: Optional[str] = None,
        forecast_hour: Optional[int] = None,
        force: bool = False,
    ) -> str:
        """Download NWM discharge for each HUC8, mirroring the FIMserv API.

        source="forecast":
            getNWMForecasteddata(huc, forecast_range, forecast_date, hour,
            sort_by).  forecast_date / forecast_hour may be None — FIMserv then
            uses the latest available run.  Aggregation (sort_by) only applies
            to medium / long range.

        source="retrospective":
            * value_times given  -> one CSV per timestamp (huc_event_dict),
              each a separate FIM.  Aggregation is ignored.
            * else start/end range with discharge_sortby aggregation.

        Returns the source used.
        """
        fm = _import_fimserve()
        specific = bool(value_times)
        self.log(
            f"Preparing NWM {source} discharge for {len(huc8_ids)} HUC8(s)"
            + (f" — {len(value_times)} event time(s)" if specific else "")
            + "."
        )

        agg = sort_by or "maximum"
        for i, huc in enumerate(huc8_ids, 1):
            try:
                if source == "forecast":
                    # Forecast date/hour vary per run; always fetch unless forced.
                    self.log(f"Discharge [{i}/{len(huc8_ids)}]: {huc} …")
                    kw = {}
                    if forecast_date:
                        kw["forecast_date"] = forecast_date
                    if forecast_hour is not None:
                        kw["hour"] = int(forecast_hour)
                    # Aggregation is only meaningful for medium / long range.
                    if forecast_range in ("mediumrange", "longrange") and sort_by:
                        kw["sort_by"] = sort_by
                    fm.getNWMForecasteddata(huc, forecast_range, **kw)

                elif specific:
                    # One CSV per requested timestamp — skip the timestamps whose
                    # exact CSV already exists; only fetch the missing ones.
                    todo = [t for t in value_times
                            if force or not self.expected_event_csv(huc, t).exists()]
                    have = len(value_times) - len(todo)
                    if have:
                        self.log(f"Discharge [{i}/{len(huc8_ids)}] {have} event time(s) already present — skipping those: {huc}")
                    if not todo:
                        self.log(f"Discharge [{i}/{len(huc8_ids)}] all event time(s) present: {huc}")
                        continue
                    self.log(f"Discharge [{i}/{len(huc8_ids)}]: {huc} — {len(todo)} event time(s) …")
                    fm.getNWMretrospectivedata(huc_event_dict={huc: todo})

                else:
                    # Range + aggregation: skip when this exact request's CSV
                    # (NWM_<start>_<end>_<sortby>_<huc>.csv) is already on disk.
                    expected = self.expected_range_csv(huc, start_date, end_date, agg)
                    if not force and expected.exists():
                        self.log(f"Discharge [{i}/{len(huc8_ids)}] already present — skipping: {expected.name}")
                        continue
                    self.log(f"Discharge [{i}/{len(huc8_ids)}]: {huc} …")
                    fm.getNWMretrospectivedata(
                        huc=huc,
                        start_date=str(start_date),
                        end_date=str(end_date),
                        discharge_sortby=agg,
                    )
                self.log(f"Discharge [{i}/{len(huc8_ids)}] ready: {huc}")
            except Exception as exc:
                self.log(f"Discharge [{i}/{len(huc8_ids)}] failed for {huc}: {exc}")
        return source

    # Generate FIM 
    def generate_fim(self, huc8_ids: List[str], depth: bool = False,
                     force: bool = False) -> List[str]:
        """Run OWP HAND FIM for each HUC8.  Returns the IDs that produced output.

        A HUC8 whose inundation raster already exists is skipped unless
        ``force=True`` (so a re-opened project does not regenerate the FIM).
        """
        fm = _import_fimserve()
        ok: List[str] = []
        for i, huc in enumerate(huc8_ids, 1):
            try:
                if not force and self.has_fim(huc):
                    self.log(f"FIM [{i}/{len(huc8_ids)}] already exists — skipping: {huc}")
                    ok.append(huc)
                    continue
                self.log(f"FIM [{i}/{len(huc8_ids)}]: {huc} (depth={depth}) …")
                fm.runOWPHANDFIM(huc, depth=depth)
                ok.append(huc)
                self.log(f"FIM [{i}/{len(huc8_ids)}] generated: {huc}")
            except Exception as exc:
                self.log(f"FIM [{i}/{len(huc8_ids)}] failed for {huc}: {exc}")
        return ok

    # Make binary FIM
    def make_binary(self, raster_paths: List[str]) -> List[str]:
        """Reclassify each FIM raster to wet (1) / dry (0).

        Any cell with a positive value (inundation extent or depth > 0) becomes
        1, everything else becomes 0.  Writes ``<name>_binary.tif`` next to the
        source and returns the new paths.
        """
        out_paths: List[str] = []
        for src in raster_paths:
            src = Path(src)
            try:
                with rasterio.open(src) as ds:
                    arr = ds.read(1)
                    nodata = ds.nodata
                    profile = ds.profile.copy()
                binary = np.where(arr > 0, 1, 0).astype("uint8")
                if nodata is not None:
                    binary[arr == nodata] = 255
                profile.update(dtype="uint8", count=1, nodata=255, compress="lzw")
                dst = src.with_name(src.stem + "_binary.tif")
                with rasterio.open(dst, "w", **profile) as out:
                    out.write(binary, 1)
                out_paths.append(str(dst))
                self.log(f"  Binary FIM: {dst.name}")
            except Exception as exc:
                self.log(f"  Could not binarize {src.name}: {exc}")
        return out_paths

    def _save_lzw(self, src_path: str, out_name: str) -> str:
        """Re-write a single raster into the project folder with LZW + tiling so
        the carried single-HUC8 product is as small as the mosaicked ones."""
        dst = self.project_dir / out_name
        with rasterio.open(src_path) as ds:
            profile = ds.profile.copy()
            profile.update(compress="lzw", tiled=True,
                           blockxsize=256, blockysize=256)
            data = ds.read()
        with rasterio.open(dst, "w", **profile) as out:
            out.write(data)
        return str(dst)

    def depth_mm_to_m(self, raster_paths: List[str]) -> List[str]:
        """Convert FIMserv depth rasters from millimetres to metres.

        OWP HAND depth output is in mm; we rewrite each as float metres
        (value / 1000) so the saved product and its 'Depth (m)' legend agree.
        Writes ``<name>_m.tif`` next to the source and returns the new paths.
        """
        out_paths: List[str] = []
        for src in raster_paths:
            src = Path(src)
            try:
                with rasterio.open(src) as ds:
                    arr = ds.read(1).astype("float32")
                    nodata = ds.nodata
                    profile = ds.profile.copy()
                mask = None
                if nodata is not None:
                    mask = (arr == nodata)
                arr = arr / 1000.0
                out_nodata = -9999.0
                if mask is not None:
                    arr[mask] = out_nodata
                profile.update(dtype="float32", nodata=out_nodata, compress="lzw")
                dst = src.with_name(src.stem + "_m.tif")
                with rasterio.open(dst, "w", **profile) as out:
                    out.write(arr, 1)
                out_paths.append(str(dst))
                self.log(f"  Depth mm→m: {dst.name}")
            except Exception as exc:
                self.log(f"  Could not convert depth {src.name} to metres: {exc}")
                out_paths.append(str(src))   # fall back to the original
        return out_paths

    #  Mosaic multiple HUCs
    def mosaic(self, raster_paths: List[str], out_name: str,
               method: str = "first") -> Optional[str]:
        """Merge multiple HUC8 rasters into one mosaic GeoTIFF.

        ``method`` is rasterio.merge's overlap rule.  Use ``"max"`` for depth so
        the larger depth wins where two HUC8 regions overlap; the default
        ``"first"`` is fine for the binary extent.

        Returns the mosaic path, or the single input path when there is only
        one raster (nothing to merge), or None when there is nothing to do.
        """
        raster_paths = [p for p in raster_paths if p and Path(p).exists()]
        if not raster_paths:
            return None
        if len(raster_paths) == 1:
            return raster_paths[0]

        self.log(f"Mosaicking {len(raster_paths)} rasters (overlap={method}) → {out_name} …")
        srcs = [rasterio.open(p) for p in raster_paths]
        try:
            mosaic_arr, mosaic_transform = rio_merge(srcs, method=method)
            profile = srcs[0].profile.copy()
            profile.update(
                height=mosaic_arr.shape[1],
                width=mosaic_arr.shape[2],
                transform=mosaic_transform,
                compress="lzw",
                tiled=True,
                blockxsize=256,
                blockysize=256,
            )
            dst = self.project_dir / out_name
            with rasterio.open(dst, "w", **profile) as out:
                out.write(mosaic_arr)
        finally:
            for s in srcs:
                s.close()
        self.log(f"  Mosaic written: {dst.name}")
        return str(dst)

    # Clip FIM to the AOI boundary
    def clip_to_aoi(self, raster_path: str, aoi_path: str, out_name: str) -> Optional[str]:
        """Clip a raster to the AOI polygon and write it into the project folder."""
        raster_path = str(raster_path)
        if not Path(raster_path).exists():
            return None
        try:
            with rasterio.open(raster_path) as ds:
                aoi = gpd.read_file(aoi_path).to_crs(ds.crs)
                geoms = [g.__geo_interface__ for g in aoi.geometry]
                clipped, transform = rio_mask(ds, geoms, crop=True)
                profile = ds.profile.copy()
            profile.update(
                height=clipped.shape[1],
                width=clipped.shape[2],
                transform=transform,
                compress="lzw",
            )
            dst = self.project_dir / out_name
            with rasterio.open(dst, "w", **profile) as out:
                out.write(clipped)
            self.log(f"  Clipped to AOI: {dst.name}")
            return str(dst)
        except Exception as exc:
            self.log(f"  Could not clip {Path(raster_path).name}: {exc}")
            return None

    def _find_fim_rasters(self, huc8_ids: List[str]) -> Dict[str, List[str]]:
        """Return {"extent": [...], "depth": [...]} of FIM GeoTIFFs per HUC8.

        FIMserv writes output to ``output/flood_<huc>/<huc>_inundation/``.  We
        pick the inundation extent rasters and any ``*_depth.tif`` rasters; the
        depth list is empty when depth output was not requested.
        """
        found = {"extent": [], "depth": []}
        for huc in huc8_ids:
            base = self.output_dir / f"flood_{huc}" / f"{huc}_inundation"
            if not base.exists():
                continue
            for tif in sorted(glob.glob(str(base / "*.tif"))):
                name = Path(tif).name.lower()
                if name.endswith("_depth.tif"):
                    found["depth"].append(tif)
                elif "inundation" in name and "binary" not in name:
                    found["extent"].append(tif)
        return found

    # Resume helpers — detect what an existing project folder already has so a
    # re-opened project runs only the missing steps instead of re-downloading.
    def is_huc8_downloaded(self, huc8: str) -> bool:
        """True when this HUC8's OWP HAND data is already on disk.

        DownloadHUC8 writes ``output/flood_<huc>/<huc>/`` plus a
        ``fim_inputs.csv``; either present means the download is done.
        """
        base = self.output_dir / f"flood_{huc8}"
        data_dir = base / str(huc8)
        if data_dir.is_dir() and any(data_dir.iterdir()):
            return True
        return (base / "fim_inputs.csv").exists()

    def discharge_csv_for(self, huc8: str) -> Optional[str]:
        """Return any existing NWM discharge CSV for this HUC8, else None."""
        data_dir = self.project_dir / "data" / "inputs"
        hits = sorted(glob.glob(str(data_dir / f"*{huc8}*.csv")))
        return hits[0] if hits else None

    def _inputs_dir(self) -> Path:
        return self.project_dir / "data" / "inputs"

    @staticmethod
    def _ymd(date_str) -> str:
        return str(date_str).replace("-", "").replace(":", "").replace(" ", "")[:8]

    def expected_range_csv(self, huc8: str, start_date, end_date, sort_by: str) -> Path:
        """The exact CSV getNWMretrospectivedata writes for a range+sortby:
        ``NWM_<start>_<end>_<sortby>_<huc>.csv``."""
        s = self._ymd(start_date); e = self._ymd(end_date)
        return self._inputs_dir() / f"NWM_{s}_{e}_{sort_by}_{huc8}.csv"

    def expected_event_csv(self, huc8: str, value_time: str) -> Path:
        """The exact CSV written for one event time: ``NWM_<stamp>_<huc>.csv``
        (stamp is YYYYMMDD for a date, YYYYMMDDHHMMSS for a datetime)."""
        import datetime as _dt
        stamp = None
        for fmt, out in (("%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M%S"),
                         ("%Y-%m-%d %H:%M", "%Y%m%d%H%M%S"),
                         ("%Y-%m-%d", "%Y%m%d")):
            try:
                stamp = _dt.datetime.strptime(str(value_time), fmt).strftime(out)
                break
            except ValueError:
                continue
        if stamp is None:
            stamp = self._ymd(value_time)
        return self._inputs_dir() / f"NWM_{stamp}_{huc8}.csv"

    def has_fim(self, huc8: str) -> bool:
        """True when a FIM inundation raster already exists for this HUC8."""
        base = self.output_dir / f"flood_{huc8}" / f"{huc8}_inundation"
        return base.is_dir() and bool(glob.glob(str(base / "*.tif")))

    # HUC8 polygons
    def huc8_polygons(self, huc8_ids: List[str]):
        """Return a GeoDataFrame of the HUC8 polygon(s) for the given IDs.

        Reads the bundled ``data/us_huc8.geojson`` so the map can show the
        region the model will actually run over.  Returns None if the file or
        the IDs aren't found.
        """
        try:
            from core.aoi_info import _load_huc8_boundaries
            gdf = _load_huc8_boundaries()
            if gdf is None or gdf.empty:
                return None
            col = "huc8" if "huc8" in gdf.columns else next(
                (c for c in gdf.columns if c.lower() == "huc8"), None
            )
            if not col:
                return None
            want = {str(c).zfill(8) for c in huc8_ids}
            hits = gdf[gdf[col].astype(str).str.zfill(8).isin(want)]
            return hits if not hits.empty else None
        except Exception as exc:
            self.log(f"  HUC8 polygon lookup failed: {exc}")
            return None

    # Hydrograph for the start/end range preview, based on maximum discharge.
    def max_discharge_hydrograph(
        self, huc8: str, start_date, end_date, out_csv: Optional[str] = None,
    ):
        """Build the hydrograph of the feature with the maximum discharge.

        Retrospective ranges use FIMserv's parquet store (the same
        getFeatureWithMaxDischarge / getFIDdata logic as the package's plot
        module).  Forecast ranges have no parquet, so we fall back to the
        max-across-reaches series from the NWM CSV.

        Returns ``(csv_path, timesteps)`` where ``timesteps`` is the list of
        available ``YYYY-MM-DD HH:MM`` stamps in the series (for the
        'pick a specific hour/day' option), or ``(None, [])`` when nothing is
        available.
        """
        import pandas as pd

        out_csv = out_csv or str(self.project_dir / f"hydrograph_{huc8}.csv")

        # 1) Retrospective: the FIMserv parquet path (feature with max discharge).
        df = self._retro_max_feature_series(huc8, start_date, end_date)

        # 2) Forecast / fallback: max across reaches from the NWM CSV.
        if df is None:
            df = self._csv_max_across_reaches(huc8)
        if df is None or df.empty:
            return None, []

        df.to_csv(out_csv, index=False)
        try:
            stamps = pd.to_datetime(df["datetime"], errors="coerce").dropna()
            timesteps = [t.strftime("%Y-%m-%d %H:%M") for t in stamps]
        except Exception:
            timesteps = []
        return out_csv, timesteps

    def _retro_max_feature_series(self, huc8, start_date, end_date):
        """Return a ``datetime,discharge_cms`` DataFrame for the reach with the
        largest discharge in the retrospective parquet, or None when there is
        no parquet (e.g. forecast dates).  Reads the time-series parquet
        FIMserv leaves under output/flood_<huc>/discharge/nwm30_retrospective/."""
        discharge_dir = (self.output_dir / f"flood_{huc8}" /
                         "discharge" / "nwm30_retrospective")
        if not discharge_dir.is_dir():
            self.log(f"  No retrospective parquet folder for {huc8} "
                     f"(expected {discharge_dir}).")
            return None
        # The parquet is named <start><end>.parquet — confirm one exists.
        s = self._ymd(start_date); e = self._ymd(end_date)
        expected_pq = discharge_dir / f"{s}_{e}.parquet"
        if not expected_pq.exists():
            any_pq = sorted(glob.glob(str(discharge_dir / "*.parquet")))
            if not any_pq:
                self.log(f"  No parquet in {discharge_dir} — cannot draw hydrograph.")
                return None
            self.log(f"  Using parquet {Path(any_pq[0]).name} for the hydrograph.")
        try:
            _import_fimserve()
            from fimserve.plot.nwmfid import getFeatureWithMaxDischarge, getFIDdata
        except Exception as exc:
            self.log(f"  fimserve.plot.nwmfid unavailable ({exc}).")
            return None
        try:
            max_fid = getFeatureWithMaxDischarge(str(discharge_dir), start_date, end_date)
            data = getFIDdata(str(discharge_dir), max_fid, start_date, end_date)
            if data is None or data.empty:
                self.log(f"  Max-discharge feature {max_fid} returned no rows.")
                return None
            out = data.rename(columns={"Date": "datetime", "Discharge": "discharge_cms"})
            self.log(f"  Hydrograph: feature {max_fid} ({len(out)} timesteps).")
            return out[["datetime", "discharge_cms"]]
        except Exception as exc:
            self.log(f"  Max-discharge hydrograph (retro) unavailable: {exc}")
            return None

    def _csv_max_across_reaches(self, huc8):
        """Return a ``datetime,discharge_cms`` DataFrame holding the maximum
        discharge across all reaches in the HUC8's NWM CSV, or None."""
        import pandas as pd

        data_dir = self.project_dir / "data" / "inputs"
        candidates = sorted(glob.glob(str(data_dir / f"*{huc8}*.csv")))
        if not candidates:
            return None
        src = max(candidates, key=lambda p: os.path.getmtime(p))
        try:
            df = pd.read_csv(src)
        except Exception as exc:
            self.log(f"  Could not read discharge CSV {Path(src).name}: {exc}")
            return None
        time_col = next(
            (c for c in df.columns if c.lower() in ("datetime", "time", "date")),
            None,
        )
        if time_col is None:
            return None
        reach_cols = [c for c in df.columns if c != time_col]
        if not reach_cols:
            return None
        series = df[reach_cols].apply(pd.to_numeric, errors="coerce").max(axis=1)
        return pd.DataFrame({"datetime": df[time_col], "discharge_cms": series})

    # Full workflow--> for the terminal run
    def run_full_workflow(
        self,
        aoi_path: Optional[Union[str, Path]] = None,
        huc8_ids: Optional[List[str]] = None,
        event_date: datetime = None,
        end_date: Optional[datetime] = None,
        depth: bool = False,
        binary: bool = True,
        clip: bool = True,
        forecast_range: str = "mediumrange",
        sort_by: str = "maximum",
    ) -> Dict:
        """Run the whole pipeline and return a summary dict.

        Provide an ``aoi_path`` (HUC8s are resolved from it and the result is
        clipped to the AOI) OR ``huc8_ids`` directly (full HUC8 extent, no clip).
        Steps: resolve HUC8 → download → discharge → FIM → binary → (mosaic when
        ≥2 HUCs) → clip to AOI (only when an AOI was given).
        """
        summary: Dict = {"project_dir": str(self.project_dir), "outputs": {}}

        # Resolve the HUC8 list: from the AOI when given, else the typed IDs.
        if aoi_path:
            huc8_ids = self.get_huc8_ids(aoi_path)
        else:
            huc8_ids = sorted({str(c).zfill(8) for c in (huc8_ids or [])})
        summary["huc8_ids"] = huc8_ids
        if not huc8_ids:
            self.log("No HUC8 IDs — stopping.")
            return summary

        # Clipping only makes sense when we have an AOI polygon to clip to.
        clip = clip and bool(aoi_path)

        huc8_ids = self.download_huc8(huc8_ids)
        if not huc8_ids:
            self.log("No HUC8 data downloaded — stopping.")
            return summary

        # Terminal convenience: derive the source from the event date's year.
        if event_date and event_date >= FORECAST_CUTOFF:
            mode = self.prepare_discharge(
                huc8_ids, source="forecast",
                forecast_range=forecast_range,
                forecast_date=event_date.strftime("%Y-%m-%d"),
                sort_by=sort_by,
            )
        else:
            mode = self.prepare_discharge(
                huc8_ids, source="retrospective",
                start_date=(event_date.strftime("%Y-%m-%d") if event_date else None),
                end_date=((end_date or event_date).strftime("%Y-%m-%d")
                          if (end_date or event_date) else None),
                sort_by=sort_by,
            )
        summary["discharge_mode"] = mode

        huc8_ids = self.generate_fim(huc8_ids, depth=depth)
        if not huc8_ids:
            self.log("No FIM generated — stopping.")
            return summary

        rasters = self._find_fim_rasters(huc8_ids)
        multi = len(huc8_ids) >= 2

        # Build each product family
        families = {"extent": rasters["extent"]}
        if depth:
            families["depth"] = rasters["depth"]

        for fam, paths in families.items():
            if not paths:
                continue

            # Binary reclass on the extent (and depth) rasters.
            work_paths = paths
            if binary:
                bin_paths = self.make_binary(paths)
                summary["outputs"].setdefault(f"{fam}_binary", bin_paths)
                # Mosaic/clip the binary version for the extent family; keep the
                # continuous depth raster for the depth family.
                if fam == "extent" and bin_paths:
                    work_paths = bin_paths

            # Mosaic when two or more HUCs; otherwise carry the single raster.
            if multi:
                mosaic_path = self.mosaic(
                    work_paths, f"mosaicked_allhuc_{fam}.tif"
                )
            else:
                mosaic_path = work_paths[0] if work_paths else None
                if mosaic_path:
                    # Single HUC8 — re-save with LZW so the size stays low.
                    mosaic_path = self._save_lzw(
                        mosaic_path, f"{fam}_{Path(mosaic_path).name}"
                    )
            summary["outputs"].setdefault(f"{fam}_mosaic", mosaic_path)

            # Clip the mosaic (depth especially) back to the AOI polygon.
            if clip and mosaic_path:
                clipped = self.clip_to_aoi(
                    mosaic_path, str(aoi_path), f"clipped_{fam}_FIM.tif"
                )
                summary["outputs"].setdefault(f"{fam}_clipped", clipped)

        self.log("FIM workflow complete.")
        return summary


# wrapper--> for GUI call
def run_fimserv_mode(
    project_dir: str,
    configs: List[Dict],
    log_fn=print,
) -> Dict:
    """Run the FIMserv FIM workflow for one or more AOI configurations.

    Each config dict has (provide aoi_path OR huc8_ids):
        aoi_path        : str | None  — AOI file; HUC8s resolved + output clipped
        huc8_ids        : list | None — HUC8 IDs to run directly (full extent)
        event_date      : datetime
        end_date        : datetime | None  (retrospective range end)
        depth           : bool   — also produce a depth raster
        binary          : bool   — reclassify FIM to wet/dry
        clip            : bool   — clip the mosaic to the AOI boundary
        forecast_range  : str    — "shortrange" | "mediumrange" | "longrange"
        sort_by         : str    — "maximum" | "median" | "minimum"

    Returns ``{"results": [summary_dict, ...]}``.
    """
    results = []
    for cfg in configs:
        api = FIMservAPI(project_dir, log_fn=log_fn)
        summary = api.run_full_workflow(
            aoi_path=cfg.get("aoi_path"),
            huc8_ids=cfg.get("huc8_ids"),
            event_date=cfg["event_date"],
            end_date=cfg.get("end_date"),
            depth=bool(cfg.get("depth", False)),
            binary=bool(cfg.get("binary", True)),
            clip=bool(cfg.get("clip", True)),
            forecast_range=cfg.get("forecast_range", "mediumrange"),
            sort_by=cfg.get("sort_by", "maximum"),
        )
        results.append(summary)
    log_fn(f"FIMserv workflow finished for {len(results)} AOI config(s).")
    return {"results": results}


# Step-wise wrappers
def resolve_huc8_mode(project_dir, aoi_path=None, huc8_ids=None, log_fn=print):
    """Tab 1 — resolve the HUC8 IDs to run over.

    From the AOI when given, else the typed IDs.  Returns the IDs so the GUI can
    draw the HUC8 run-area (and AOI) on the map.
    """
    api = FIMservAPI(project_dir, log_fn=log_fn)
    if aoi_path:
        ids = api.get_huc8_ids(aoi_path)
    else:
        ids = sorted({str(c).zfill(8) for c in (huc8_ids or [])})
        log_fn(f"Using HUC8 IDs: {', '.join(ids) if ids else '(none)'}")
    return {"huc8_ids": ids, "aoi_path": aoi_path}


def discover_existing(project_dir, log_fn=print):
    """Inspect a re-opened project folder and report what is already done.
    """
    api = FIMservAPI(project_dir, log_fn=log_fn)
    ids = []
    for d in sorted(glob.glob(str(api.output_dir / "flood_*"))):
        name = Path(d).name
        if name.startswith("flood_"):
            huc = name[len("flood_"):]
            if huc.isdigit():
                ids.append(huc.zfill(8))
    ids = sorted(set(ids))
    downloaded = [h for h in ids if api.is_huc8_downloaded(h)]
    with_discharge = [h for h in ids if api.discharge_csv_for(h)]
    with_fim = [h for h in ids if api.has_fim(h)]
    if ids:
        log_fn(f"Found existing project data for {len(ids)} HUC8(s): "
               f"{', '.join(ids)}")
    return {
        "huc8_ids": ids,
        "downloaded": downloaded,
        "with_discharge": with_discharge,
        "with_fim": with_fim,
    }


def download_huc8_mode(project_dir, huc8_ids, log_fn=print):
    """Tab 2 — download the OWP HAND inputs for each HUC8."""
    api = FIMservAPI(project_dir, log_fn=log_fn)
    ok = api.download_huc8(list(huc8_ids))
    return {"downloaded": ok}


def streamflow_mode(project_dir, huc8_ids, source="retrospective",
                    start_date=None, end_date=None, value_times=None,
                    sort_by=None, forecast_range="mediumrange",
                    forecast_date=None, forecast_hour=None, log_fn=print):
    """Tab 3 — fetch NWM discharge and (for a retrospective range) build the
    max-discharge hydrograph + available timesteps.

    source="retrospective":
        * value_times -> one CSV per specific event time (each a FIM).
        * else start/end range with sort_by aggregation; a hydrograph of the
          feature with the maximum discharge is drawn and the in-range
          timesteps are returned so the user can pick event time(s).
    source="forecast":
        getNWMForecasteddata with optional forecast_date/forecast_hour (None =
        latest run).  No hydrograph preview.

    Returns ``{"discharge_mode", "hydrographs": {huc: csv},
    "timesteps": {huc: [..]}}``.
    """
    api = FIMservAPI(project_dir, log_fn=log_fn)
    mode = api.prepare_discharge(
        list(huc8_ids), source=source,
        start_date=start_date, end_date=end_date,
        value_times=value_times, sort_by=sort_by,
        forecast_range=forecast_range,
        forecast_date=forecast_date, forecast_hour=forecast_hour,
    )
    hydrographs, timesteps = {}, {}
    # Hydrograph + timestep list only for a retrospective date range (the window
    # the user downloaded), so they can then pick one or more event times.
    if source == "retrospective" and start_date and end_date and not value_times:
        for huc in huc8_ids:
            csv, stamps = api.max_discharge_hydrograph(huc, start_date, end_date)
            if csv:
                hydrographs[huc] = csv
                timesteps[huc] = stamps
    return {"discharge_mode": mode, "hydrographs": hydrographs,
            "timesteps": timesteps}


def generate_fim_mode(project_dir, huc8_ids, aoi_path=None, depth=False,
                      binary=True, clip=True, log_fn=print):
    """Tab 4 — generate the FIM, make it binary, mosaic, and clip to the AOI.

    Reuses the existing per-step methods so this stays consistent with the
    full-workflow path.  Clipping only runs when an AOI was provided.
    """
    api = FIMservAPI(project_dir, log_fn=log_fn)
    clip = clip and bool(aoi_path)
    outputs: Dict = {}

    ok = api.generate_fim(list(huc8_ids), depth=depth)
    if not ok:
        log_fn("No FIM generated.")
        return {"outputs": outputs, "huc8_ids": ok}

    rasters = api._find_fim_rasters(ok)
    multi = len(ok) >= 2
    families = {"extent": rasters["extent"]}
    if depth:
        families["depth"] = rasters["depth"]

    for fam, paths in families.items():
        if not paths:
            continue
        work_paths = paths
        if fam == "depth":
            # FIMserv depth is in mm — convert to metres before mosaic/clip so
            # the saved product (and its 'Depth (m)' legend) are in metres.
            work_paths = api.depth_mm_to_m(paths)
        elif binary:
            bin_paths = api.make_binary(paths)
            outputs[f"{fam}_binary"] = bin_paths
            if bin_paths:
                work_paths = bin_paths
        if multi:
            merge_method = "max" if fam == "depth" else "first"
            mosaic_path = api.mosaic(work_paths, f"mosaicked_allhuc_{fam}.tif",
                                     method=merge_method)
        else:
            mosaic_path = work_paths[0] if work_paths else None
            if mosaic_path:
                # Single HUC8 — re-save with LZW so the size stays low.
                mosaic_path = api._save_lzw(
                    mosaic_path, f"{fam}_{Path(mosaic_path).name}"
                )
        outputs[f"{fam}_mosaic"] = mosaic_path
        if clip and mosaic_path:
            outputs[f"{fam}_clipped"] = api.clip_to_aoi(
                mosaic_path, str(aoi_path), f"clipped_{fam}_FIM.tif"
            )

    log_fn("FIM generation complete.")
    return {"outputs": outputs, "huc8_ids": ok}


# CLI entry point--> if some one wants to run the workflow from the terminal
def _cli():
    # Run the workflow straight from the terminal:
    #   python -m core.FIMserv_api --aoi aoi.shp --project ./run --date 2020-05-20
    import argparse

    p = argparse.ArgumentParser(
        description="Generate an OWP HAND flood inundation map from an AOI."
    )
    p.add_argument("--aoi", help="AOI file (.shp/.gpkg/.geojson/.kml)")
    p.add_argument("--huc8", help="HUC8 ID(s), comma-separated (instead of --aoi)")
    p.add_argument("--project", required=True, help="Project folder for outputs")
    p.add_argument("--date", required=True, help="Event date YYYY-MM-DD")
    p.add_argument("--end-date", help="End date YYYY-MM-DD (retrospective range)")
    p.add_argument("--depth", action="store_true", help="Also produce a depth raster")
    p.add_argument("--no-binary", action="store_true", help="Skip the binary FIM")
    p.add_argument("--no-clip", action="store_true", help="Skip clipping to the AOI")
    p.add_argument("--forecast-range", default="mediumrange",
                   choices=["shortrange", "mediumrange", "longrange"])
    p.add_argument("--sort-by", default="maximum",
                   choices=["maximum", "median", "minimum"])
    args = p.parse_args()
    if not args.aoi and not args.huc8:
        p.error("provide either --aoi or --huc8")

    event_date = datetime.strptime(args.date, "%Y-%m-%d")
    end_date = datetime.strptime(args.end_date, "%Y-%m-%d") if args.end_date else None
    huc8_ids = [h.strip() for h in args.huc8.split(",")] if args.huc8 else None

    api = FIMservAPI(args.project)
    summary = api.run_full_workflow(
        aoi_path=args.aoi,
        huc8_ids=huc8_ids,
        event_date=event_date,
        end_date=end_date,
        depth=args.depth,
        binary=not args.no_binary,
        clip=not args.no_clip,
        forecast_range=args.forecast_range,
        sort_by=args.sort_by,
    )
    print("\nSummary:")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    _cli()
