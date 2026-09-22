"""HAND (Height Above Nearest Drainage) download from UT Austin TACC.

Data source:
    https://web.corral.tacc.utexas.edu/nfiedata/HAND/{huc6}/{huc6}hand.tif

The dataset is organised by 6-digit USGS Hydrologic Unit Codes (HUC6).  For a
given AOI, we:
  1. Spatial-join the AOI against a bundled HUC6 polygon layer
     (data/us_huc6.geojson) to determine which HUC6 tiles cover it.
  2. Open each HUC6 GeoTIFF *remotely* via GDAL VSI CURL and read ONLY the
     AOI's bounding-box window — drops a download from ~700 MB to a few MB.
  3. Let the caller (core/dem.py:_clip_and_reproject) merge those windows,
     clip to the AOI, reproject to the AOI CRS, and resample.

If the remote read fails for any reason, we fall back to the full file
download (slow but reliable).
"""
import os
from pathlib import Path
from typing import List, Optional

import geopandas as gpd
import numpy as np
import rasterio
import requests
from rasterio.windows import from_bounds, transform as window_transform


# GDAL VSI tuning — keep small reads efficient and tolerate slow TLS handshakes
os.environ.setdefault("GDAL_HTTP_VERSION", "2")
os.environ.setdefault("GDAL_HTTP_TIMEOUT", "120")
os.environ.setdefault("GDAL_HTTP_CONNECTTIMEOUT", "30")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "3")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")
os.environ.setdefault("VSI_CACHE", "TRUE")
os.environ.setdefault("VSI_CACHE_SIZE", "1000000000")   # 1 GB process-wide cache


HAND_BASE_URL = "https://web.corral.tacc.utexas.edu/nfiedata/HAND"

HUC6_DATA_PATH = Path(__file__).parent.parent / "data" / "us_huc6.geojson"

# Cached GeoDataFrame of HUC6 polygons (loaded lazily, once per process)
_HUC6_GDF: Optional[gpd.GeoDataFrame] = None


def _load_huc6_boundaries() -> gpd.GeoDataFrame:
    global _HUC6_GDF
    if _HUC6_GDF is None:
        if not HUC6_DATA_PATH.exists():
            raise FileNotFoundError(
                f"HUC6 boundary file not found: {HUC6_DATA_PATH}\n"
                "This file is required for HAND source lookup."
            )
        gdf = gpd.read_file(HUC6_DATA_PATH)
        # One of the 407 regions (180500) is an invalid polygon.  A single bad
        # geometry can make GEOS throw part way through a predicate over the
        # whole layer — "Points of LinearRing do not form a closed linestring" —
        # and the failure lands on whatever AOI happened to be asking.  Repair
        # it once, here, rather than leaving it to surface unpredictably.
        try:
            bad = ~gdf.geometry.is_valid
            if bad.any():
                gdf.loc[bad, "geometry"] = gdf.loc[bad, "geometry"].buffer(0)
        except Exception:
            pass
        _HUC6_GDF = gdf
    return _HUC6_GDF


def _aoi_bounds_in(aoi_gdf, epsg: int):
    """AOI bounds in ``epsg``, transformed WITHOUT touching GEOS.

    Only the four corner coordinates go through pyproj, so a polygon GEOS
    dislikes — an unclosed ring, a self-intersection — cannot make this fail.
    The geometry engine was never needed to answer "where is this AOI".
    """
    from pyproj import Transformer

    b = aoi_gdf.total_bounds                      # minx, miny, maxx, maxy
    src = aoi_gdf.crs
    if src is None:
        raise ValueError("The AOI has no CRS, so its location cannot be found.")
    src_str = src.to_wkt() if hasattr(src, "to_wkt") else str(src)
    t = Transformer.from_crs(src_str, f"EPSG:{int(epsg)}", always_xy=True)
    xs, ys = t.transform([b[0], b[2], b[0], b[2]], [b[1], b[1], b[3], b[3]])
    if not all(np.isfinite(v) for v in list(xs) + list(ys)):
        raise ValueError(
            f"The AOI's corners did not transform to EPSG:{epsg} "
            f"(got {list(xs)}, {list(ys)}) — check the AOI's CRS.")
    return min(xs), min(ys), max(xs), max(ys)


def find_huc6_for_aoi(aoi_gdf: gpd.GeoDataFrame, log_fn=print) -> List[str]:
    """Return the 6-digit HUC6 codes covering the AOI.

    Selection is by BOUNDING BOX, compared numerically against the boundary
    layer's own bounds.  It used to be a gpd.sjoin, which reported "0 HUC6
    regions" — indistinguishable from an AOI outside the US — whenever anything
    in the geometry engine misbehaved.  Two Missouri AOIs failed that way while
    seven others in the same run succeeded, and the re-check finally named the
    reason: "Points of LinearRing do not form a closed linestring", thrown from
    inside GEOS over a layer holding one invalid polygon.

    A rectangle-overlap test is pure arithmetic: it cannot throw, cannot depend
    on process state, and for a HUC6 region against an AOI bbox it is the same
    answer a precise intersects would give in all but pathological cases.  The
    precise test is still applied afterwards to trim candidates, but only as a
    refinement — if it fails, the bbox answer stands rather than the run being
    told the AOI is outside the country.
    """
    huc_gdf = _load_huc6_boundaries()
    minx, miny, maxx, maxy = _aoi_bounds_in(aoi_gdf, 4269)

    hb = huc_gdf.geometry.bounds                  # per-polygon minx/miny/maxx/maxy
    overlaps = ((hb["minx"] <= maxx) & (hb["maxx"] >= minx)
                & (hb["miny"] <= maxy) & (hb["maxy"] >= miny))
    hits = huc_gdf[overlaps]

    # Refine: a bbox can straddle a neighbouring region it does not really
    # touch.  Guarded, because a GEOS failure here must not turn coverage into
    # "no coverage" — that is exactly the bug this function is recovering from.
    if len(hits) > 1:
        try:
            from shapely.geometry import box
            aoi_box = box(minx, miny, maxx, maxy)
            precise = hits[hits.geometry.intersects(aoi_box)]
            if not precise.empty:
                hits = precise
        except Exception as exc:
            log_fn(f"  (using bounding-box overlap; the precise test failed: "
                   f"{type(exc).__name__})")

    codes = sorted({str(c).zfill(6) for c in hits["huc6"].tolist()})
    log_fn(f"AOI intersects {len(codes)} HUC6 region(s): {', '.join(codes) or '(none)'}")
    if hits.empty:
        return []
    # Also log human-readable names
    for _, row in hits.drop_duplicates(subset=["huc6"]).iterrows():
        log_fn(f"  • {row['huc6']}  {row.get('name', '')}  ({row.get('states', '')})")
    return codes


def download_hand_tile(huc6: str, out_path: Path, log_fn=print) -> Path:
    """Download a single HAND GeoTIFF for a HUC6 code.

    The files are large (hundreds of MB).  Downloaded with urllib; streams
    to disk in 1 MB chunks so we don't blow up memory.
    """
    huc6 = str(huc6).zfill(6)
    url = f"{HAND_BASE_URL}/{huc6}/{huc6}hand.tif"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if out_path.exists() and out_path.stat().st_size > 1024 * 1024:
        log_fn(f"  HAND tile already cached: {out_path.name}")
        return out_path

    log_fn(f"  Downloading HAND tile for HUC6 {huc6} …")
    log_fn(f"    {url}")

    # HAND files are large (~100–900 MB).  `requests` handles TACC's server
    # reliably where Python stdlib urllib was timing out.  We stream the
    # response directly to disk with a very generous timeout because:
    #   • TACC's TLS handshake can take 10–30 s (measured empirically)
    #   • The file download itself easily takes 10+ minutes on slow links
    # Fallback to unverified TLS if the OS cert bundle doesn't recognise
    # TACC's cert.
    def _stream(verify=True):
        with requests.get(
            url, stream=True,
            headers={"User-Agent": "FloodPrepApp"},
            timeout=(120, 1800),   # 2 min connect, 30 min read
            verify=verify,
        ) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            last_reported = 0
            chunk = 1024 * 1024   # 1 MB
            with open(out_path, "wb") as fh:
                for buf in resp.iter_content(chunk_size=chunk):
                    if not buf:
                        continue
                    fh.write(buf)
                    downloaded += len(buf)
                    if total and (downloaded - last_reported) > 50 * 1024 * 1024:
                        pct = downloaded * 100 / total
                        log_fn(
                            f"    {downloaded / 1e6:.0f} / "
                            f"{total / 1e6:.0f} MB ({pct:.0f}%)"
                        )
                        last_reported = downloaded
        log_fn(
            f"    ✓ Saved {out_path.name}  "
            f"({out_path.stat().st_size / 1e6:.0f} MB)"
        )

    # Try up to 3 attempts (TACC handshake can fail intermittently)
    last_exc = None
    for attempt in range(1, 4):
        if attempt > 1:
            log_fn(f"    retry {attempt}/3 …")
        try:
            _stream(verify=True)
            last_exc = None
            break
        except requests.exceptions.SSLError as ex:
            # Skip retries — go straight to unverified fallback below
            last_exc = ex
            break
        except (requests.exceptions.Timeout,
                requests.exceptions.ConnectionError) as ex:
            last_exc = ex
            if out_path.exists():
                try:
                    out_path.unlink()
                except Exception:
                    pass
            log_fn(f"    attempt {attempt} failed: {type(ex).__name__}: {ex}")
            continue
        except Exception as ex:
            last_exc = ex
            break

    if last_exc is None:
        return out_path

    try:
        raise last_exc
    except requests.exceptions.SSLError as ex:
        log_fn(f"    SSL verification failed ({ex}); retrying without TLS "
               f"verification.")
        if out_path.exists():
            try:
                out_path.unlink()
            except Exception:
                pass
        try:
            _stream(verify=False)
        except Exception as ex2:
            if out_path.exists():
                try:
                    out_path.unlink()
                except Exception:
                    pass
            raise RuntimeError(
                f"Failed to download HAND tile for HUC6 {huc6}: {ex2}\n"
                f"URL: {url}"
            )
    except Exception as ex:
        if out_path.exists():
            try:
                out_path.unlink()
            except Exception:
                pass
        raise RuntimeError(
            f"Failed to download HAND tile for HUC6 {huc6}: "
            f"{type(ex).__name__}: {ex}\n"
            f"URL: {url}"
        )

    return out_path


def fetch_hand_window(huc6: str, aoi_gdf, out_path: Path, log_fn=print) -> Path:
    """Read ONLY the AOI's bounding-box window from the remote HAND tile.

    Uses GDAL's `/vsicurl/` so the download is bounded by the AOI extent —
    typically a few MB instead of the full ~700 MB tile.

    Falls back to a full download if the windowed read fails (e.g. file is
    not internally tiled, or the server blocks range requests).
    """
    huc6 = str(huc6).zfill(6)
    url = f"{HAND_BASE_URL}/{huc6}/{huc6}hand.tif"
    vsi_url = f"/vsicurl/{url}"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if out_path.exists() and out_path.stat().st_size > 1024:
        log_fn(f"  HAND window already cached: {out_path.name}")
        return out_path

    log_fn(f"  Reading HAND window for HUC6 {huc6} via /vsicurl/ …")
    try:
        with rasterio.open(vsi_url) as src:
            # AOI bounds in the source CRS
            aoi_in_src = aoi_gdf.to_crs(src.crs)
            minx, miny, maxx, maxy = aoi_in_src.total_bounds

            # Add a small safety margin (10 cells worth) so reprojection
            # near the AOI edge doesn't lose pixels.
            res = float(abs(src.res[0]))
            pad = 10 * res
            minx -= pad; miny -= pad; maxx += pad; maxy += pad

            # Clip the bbox to the source extent so from_bounds doesn't crash
            sb = src.bounds
            minx = max(minx, sb.left); maxx = min(maxx, sb.right)
            miny = max(miny, sb.bottom); maxy = min(maxy, sb.top)
            if maxx <= minx or maxy <= miny:
                raise RuntimeError(
                    f"AOI does not overlap HAND tile for HUC6 {huc6} "
                    f"(tile bounds {sb}, AOI {aoi_in_src.total_bounds})."
                )

            window = from_bounds(minx, miny, maxx, maxy, transform=src.transform)
            window = window.round_offsets().round_lengths()
            data = src.read(1, window=window)
            log_fn(
                f"    window {data.shape[1]}×{data.shape[0]} px "
                f"(~{data.nbytes / 1e6:.1f} MB read)"
            )

            out_meta = src.meta.copy()
            out_meta.update({
                "driver":    "GTiff",
                "height":    int(data.shape[0]),
                "width":     int(data.shape[1]),
                "transform": window_transform(window, src.transform),
                "compress":  "lzw",
            })
            with rasterio.open(out_path, "w", **out_meta) as dst:
                dst.write(data, 1)
        log_fn(
            f"    ✓ Saved {out_path.name} "
            f"({out_path.stat().st_size / 1e6:.1f} MB on disk)"
        )
        return out_path
    except Exception as ex:
        log_fn(
            f"    Windowed read failed ({ex}); falling back to full tile "
            f"download (slow)."
        )
        # Fallback: download the full tile.
        full_path = out_path.with_name(f"{huc6}hand_full.tif")
        download_hand_tile(huc6, full_path, log_fn=log_fn)
        return full_path


def download_hand_for_aoi(aoi_gdf, cache_dir, log_fn=print) -> List[Path]:
    """Fetch (windowed) HAND data for every HUC6 intersecting the AOI.

    Each call reads ONLY the AOI's bbox-sized window via /vsicurl/.  Returns
    a list of local GeoTIFF paths suitable for `_clip_and_reproject`.
    """
    # Distinguish the two ways this can come back empty.  They need different
    # actions from the user, and "no HUC6 intersects" was being shown for both.
    n_rows = 0 if aoi_gdf is None else len(aoi_gdf)
    empty_geom = (n_rows == 0) or bool(aoi_gdf.geometry.is_empty.all())
    where = ""
    if not empty_geom:
        try:
            b = aoi_gdf.to_crs(4326).total_bounds
            where = (f" The AOI covers {b[1]:.3f}–{b[3]:.3f}°N, "
                     f"{b[0]:.3f}–{b[2]:.3f}°W.")
        except Exception:
            pass
    if empty_geom:
        raise RuntimeError(
            "The AOI has no usable geometry — it read back as "
            f"{n_rows} feature(s) with an empty shape, so nothing can "
            "intersect it. This is a problem with the AOI itself, not with "
            "HAND coverage: re-select the AOI in Step 2 and check the "
            "shapefile opens in a GIS.")

    codes = find_huc6_for_aoi(aoi_gdf, log_fn=log_fn)
    if not codes:
        raise RuntimeError(
            f"No HUC6 region intersects this AOI.{where} HAND is only "
            "available for the continental US.\n"
            "Use the 3DEP source or supply a user DEM."
        )

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, code in enumerate(codes, 1):
        log_fn(f"[{i}/{len(codes)}] HUC6 {code}")
        out = cache_dir / f"{code}hand_aoi.tif"
        paths.append(fetch_hand_window(code, aoi_gdf, out, log_fn=log_fn))
    return paths
