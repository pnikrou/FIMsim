"""Step 3-4 — DEM: use existing raster or download from 3DEP, then export ASCII."""
import math
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.io import MemoryFile
from rasterio.mask import mask
from rasterio.merge import merge
from rasterio.transform import array_bounds
from rasterio.warp import calculate_default_transform, reproject, Resampling
from shapely.geometry import Polygon

from core.context import save_context
from core.crs_utils import pick_working_crs_epsg, working_crs_label


# ── internal helpers ────────────────────────────────────────────────────────

def _quick_tile_check(path: Path) -> bool:
    """Return True when *path* can be read at four scattered positions.

    Reads 64×64-pixel windows at the top-left, top-right, centre, and
    bottom-right of the tile so corruption anywhere in the file — not just
    at byte 0 — is detected before the tile is used.

    A simple ``stat().st_size > 0`` check is not enough: a tile can be
    partially downloaded (non-zero bytes) but have entire compressed blocks
    missing, which rasterio only discovers during the actual read.
    """
    try:
        import rasterio.windows as _rw
        with rasterio.open(path) as src:
            h, w = src.height, src.width
            for row_frac, col_frac in [
                (0.00, 0.00),   # top-left
                (0.00, 0.75),   # top-right
                (0.50, 0.50),   # centre
                (0.75, 0.75),   # bottom-right
            ]:
                row = min(int(h * row_frac), max(0, h - 1))
                col = min(int(w * col_frac), max(0, w - 1))
                ww  = _rw.Window(col, row,
                                 min(64, w - col),
                                 min(64, h - row))
                src.read(1, window=ww)
        return True
    except Exception:
        return False


def _bounds_to_wgs84(aoi_gdf):
    """Return (minx, miny, maxx, maxy) in EPSG:4326.

    Uses pyproj Transformer directly on the four corner coordinates,
    which is more reliable than geopandas .to_crs() in some environments.
    """
    from pyproj import Transformer

    orig = aoi_gdf.total_bounds          # [minx, miny, maxx, maxy] in source CRS
    src_crs = aoi_gdf.crs

    # Transform all four corners
    xs = [orig[0], orig[2], orig[0], orig[2]]
    ys = [orig[1], orig[1], orig[3], orig[3]]

    try:
        # Convert to WKT string so pyproj accepts it reliably
        src_crs_str = src_crs.to_wkt() if hasattr(src_crs, "to_wkt") else str(src_crs)
        t = Transformer.from_crs(src_crs_str, "EPSG:4326", always_xy=True)
        lons, lats = t.transform(xs, ys)
    except Exception as e:
        raise RuntimeError(
            f"Could not reproject AOI to EPSG:4326 using pyproj: {e}\n"
            f"Source CRS: {src_crs}  |  bounds: {orig}"
        )

    if not all(np.isfinite(lons)) or not all(np.isfinite(lats)):
        # Fallback: try rasterio.warp.transform
        from rasterio.warp import transform as rio_transform
        try:
            lons, lats = rio_transform(src_crs, "EPSG:4326", xs, ys)
            lons, lats = list(lons), list(lats)
        except Exception as e2:
            raise RuntimeError(
                f"AOI reprojection to EPSG:4326 failed (both pyproj and rasterio).\n"
                f"Source CRS: {src_crs}  |  bounds: {orig}\n"
                f"Error: {e2}"
            )

    if not all(np.isfinite(lons)) or not all(np.isfinite(lats)):
        raise RuntimeError(
            f"AOI reprojection to EPSG:4326 produced non-finite coordinates.\n"
            f"Source CRS: {src_crs}  |  bounds: {orig}\n"
            "Check that your shapefile has a valid, defined CRS."
        )

    return min(lons), min(lats), max(lons), max(lats)


def _download_3dep_tiles(aoi_gdf, dem_res_m, dem_tiles_dir, log_fn):
    DEM_RESOLUTION = "13"
    N_THREADS = 6

    minx, miny, maxx, maxy = _bounds_to_wgs84(aoi_gdf)
    log_fn(f"AOI bounds EPSG:4326: ({minx:.5f}, {miny:.5f}, {maxx:.5f}, {maxy:.5f})")

    min_lon_idx = math.floor(minx)
    max_lon_idx = math.ceil(maxx)
    min_lat_idx = math.floor(miny) + 1
    max_lat_idx = math.ceil(maxy)

    tile_names = []
    for lat_idx in range(min_lat_idx, max_lat_idx + 1):
        for lon_idx in range(min_lon_idx, max_lon_idx):
            # Simple bbox overlap — no geopandas needed here
            tile_minx, tile_miny = lon_idx, lat_idx - 1
            tile_maxx, tile_maxy = lon_idx + 1, lat_idx
            if (minx < tile_maxx and maxx > tile_minx and
                    miny < tile_maxy and maxy > tile_miny):
                ns = "n" if lat_idx >= 0 else "s"
                ew = "e" if lon_idx >= 0 else "w"
                tile_name = f"{ns}{abs(lat_idx):02d}{ew}{abs(lon_idx):03d}"
                tile_names.append(tile_name)

    tile_names = sorted(set(tile_names))
    log_fn(f"Tiles needed ({len(tile_names)}): {tile_names}")

    if not tile_names:
        raise RuntimeError("No 3DEP tiles intersect the AOI.")

    def _dl(tile_name):
        url = (
            f"https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/"
            f"{DEM_RESOLUTION}/TIFF/current/{tile_name}/"
            f"USGS_{DEM_RESOLUTION}_{tile_name}.tif"
        )
        local_path = dem_tiles_dir / f"USGS_{DEM_RESOLUTION}_{tile_name}.tif"

        # ── Layer 1: validate cache before reuse ───────────────────────────
        if local_path.exists() and local_path.stat().st_size > 0:
            if _quick_tile_check(local_path):
                return local_path          # cache is valid — skip download
            log_fn(
                f"  Cached tile {tile_name} is corrupt (partial download?) "
                f"— deleting and re-downloading…"
            )
            try:
                local_path.unlink()
            except Exception:
                pass

        # ── Layer 2: fresh download ────────────────────────────────────────
        try:
            urllib.request.urlretrieve(url, local_path)
        except Exception as e:
            log_fn(f"  Failed to download {tile_name}: {e}")
            try:
                local_path.unlink(missing_ok=True)
            except Exception:
                pass
            return None

        # ── Layer 3: validate the freshly downloaded file ──────────────────
        if not _quick_tile_check(local_path):
            log_fn(
                f"  Downloaded tile {tile_name} failed read-validation "
                f"— discarding (network issue?)."
            )
            try:
                local_path.unlink(missing_ok=True)
            except Exception:
                pass
            return None

        return local_path

    tile_paths = []
    with ThreadPoolExecutor(max_workers=N_THREADS) as executor:
        futures = {executor.submit(_dl, tn): tn for tn in tile_names}
        done, total = 0, len(futures)
        for future in as_completed(futures):
            p = future.result()
            done += 1
            if p is not None:
                tile_paths.append(p)
            log_fn(f"  Download progress: {done}/{total}")

    tile_paths = sorted(tile_paths)
    if not tile_paths:
        raise RuntimeError("No DEM tiles downloaded successfully.")
    return tile_paths


def _clip_and_reproject(tile_paths, aoi_gdf, dem_res_m, dem_path, log_fn,
                        working_crs_epsg=None):
    """Clip the downloaded DEM tiles to the AOI and reproject into a
    consistent metric working CRS.

    The destination CRS is the AOI's auto-picked working CRS — NAD83 /
    UTM zone N for CONUS, WGS84 / UTM elsewhere.  Picking a projected
    CRS up front means ``dem_res_m`` always means real metres, no matter
    what CRS the user's AOI shapefile is in.
    """
    if working_crs_epsg is None:
        working_crs_epsg = int(pick_working_crs_epsg(aoi_gdf, log_fn=log_fn))
    dst_crs = rasterio.crs.CRS.from_epsg(int(working_crs_epsg))
    log_fn(
        f"DEM destination CRS: {working_crs_label(int(working_crs_epsg))} — "
        f"target cell size {dem_res_m} m."
    )

    if len(tile_paths) == 1:
        with rasterio.open(tile_paths[0]) as src:
            dem_src_crs = src.crs
            aoi_in_dem_crs = aoi_gdf.to_crs(src.crs)
            shapes = [feat["geometry"] for feat in aoi_in_dem_crs.__geo_interface__["features"]]
            out_image, out_transform = mask(src, shapes=shapes, crop=True)
            clipped_meta = src.meta.copy()
    else:
        srcs = [rasterio.open(fp) for fp in tile_paths]
        mosaic, mosaic_transform = merge(srcs)
        dem_src_crs = srcs[0].crs
        src0_nodata = srcs[0].nodata
        for s in srcs:
            s.close()
        aoi_in_dem_crs = aoi_gdf.to_crs(dem_src_crs)
        shapes = [feat["geometry"] for feat in aoi_in_dem_crs.__geo_interface__["features"]]
        # Use a clean meta for the MemoryFile to avoid bad keys from tile profile
        mem_meta = {
            "driver": "GTiff",
            "count": mosaic.shape[0],
            "dtype": mosaic.dtype.name,
            "width": mosaic.shape[2],
            "height": mosaic.shape[1],
            "crs": dem_src_crs,
            "transform": mosaic_transform,
        }
        if src0_nodata is not None:
            try:
                v = float(src0_nodata)
                if np.isfinite(v):
                    mem_meta["nodata"] = v
            except (TypeError, ValueError):
                pass
        with MemoryFile() as mf:
            with mf.open(**mem_meta) as tmp:
                tmp.write(mosaic)
                out_image, out_transform = mask(tmp, shapes=shapes, crop=True)
        clipped_meta = mem_meta

    log_fn(f"Clipped DEM shape: {out_image.shape}")

    # --- sanitize nodata --------------------------------------------------
    # We always use a clean sentinel (-9999.0) for the OUTPUT file so
    # downstream readers see a predictable nodata value, but we faithfully
    # pass the SOURCE nodata to rasterio.reproject so it can mask those
    # cells before bilinear interpolation.  Previously this code rejected
    # any |nodata| >= 1e20 — but HAND tiles use -3.4e38, a perfectly
    # valid (though large-magnitude) sentinel.  That filter caused every
    # nodata cell to be treated as real data, contaminating the output
    # with huge negatives via the bilinear kernel.
    raw_nodata = clipped_meta.get("nodata")
    dst_nodata = -9999.0
    safe_src_nodata = None   # only set if it's a real, finite value
    if raw_nodata is not None:
        try:
            v = float(raw_nodata)
            if np.isfinite(v):
                safe_src_nodata = v
        except (TypeError, ValueError):
            pass

    bands, src_h, src_w = out_image.shape
    if src_h == 0 or src_w == 0:
        raise RuntimeError(
            "DEM clip is empty — the AOI does not overlap any DEM tile.\n"
            "Possible causes:\n"
            "  • The AOI's CRS is different from the DEM's and the reprojected\n"
            "    AOI falls outside the DEM extent.\n"
            "  • The AOI polygon is empty or has invalid geometry.\n"
            "  • The selected feature index points at an empty feature.\n"
            "Check the AOI in a GIS tool (e.g. QGIS) and try again."
        )
    src_bounds = array_bounds(src_h, src_w, out_transform)
    if not all(np.isfinite(b) for b in src_bounds):
        raise RuntimeError(
            f"DEM clip produced non-finite bounds ({src_bounds}). "
            "The AOI geometry is likely invalid — open it in QGIS and "
            "repair/simplify before re-running."
        )

    # The destination CRS is always a metric UTM projection (picked by
    # crs_utils.pick_working_crs_epsg), so ``dem_res_m`` passes straight
    # through to ``calculate_default_transform`` as real metres — no
    # metres→degrees gymnastics needed any more.  This is what makes
    # "10 m DEM" actually 10 m on the ground regardless of the input
    # AOI's CRS.
    res_arg = dem_res_m

    transform, width, height = calculate_default_transform(
        dem_src_crs, dst_crs, src_w, src_h, *src_bounds, resolution=res_arg
    )
    if not (np.isfinite(width) and np.isfinite(height)):
        raise RuntimeError(
            f"Could not compute a valid output grid for the DEM "
            f"(width={width}, height={height}). Check the AOI geometry and "
            f"CRS, and that the DEM resolution ({dem_res_m} m) is reasonable."
        )
    width  = int(width)
    height = int(height)

    # Build a clean GeoTIFF profile (don't copy tile meta — it may have bad keys)
    out_meta = {
        "driver": "GTiff",
        "crs": dst_crs,
        "transform": transform,
        "width": width,
        "height": height,
        "dtype": "float32",
        "nodata": dst_nodata,
        "count": 1,
        "compress": "lzw",
    }

    dest = np.full((1, height, width), dst_nodata, dtype="float32")
    reproject(
        source=out_image.astype("float32"), destination=dest,
        src_transform=out_transform, src_crs=dem_src_crs,
        dst_transform=transform, dst_crs=dst_crs,
        src_nodata=safe_src_nodata, dst_nodata=dst_nodata,
        resampling=Resampling.bilinear,
    )

    # Replace any remaining inf/nan with nodata
    dest[~np.isfinite(dest)] = dst_nodata

    with rasterio.open(dem_path, "w", **out_meta) as dst:
        dst.write(dest)

    # ── Strict polygon mask AFTER reprojection ──────────────────────────────
    # The initial mask(crop=True) clips the source extent to the AOI bbox,
    # but bilinear resampling at the polygon edge can leak real values into
    # cells just outside the polygon.  Re-apply the mask in the destination
    # CRS so cells outside the polygon are guaranteed to be nodata.
    try:
        aoi_in_dst = aoi_gdf.to_crs(dst_crs)
        shapes_dst = [feat["geometry"]
                      for feat in aoi_in_dst.__geo_interface__["features"]]
        with rasterio.open(dem_path) as src:
            masked, masked_t = mask(src, shapes=shapes_dst, crop=True,
                                    nodata=dst_nodata)
            masked_meta = src.meta.copy()
        masked_meta.update({
            "driver":   "GTiff",
            "height":   int(masked.shape[1]),
            "width":    int(masked.shape[2]),
            "transform": masked_t,
            "compress": "lzw",
            "nodata":   dst_nodata,
        })
        with rasterio.open(dem_path, "w", **masked_meta) as dst:
            dst.write(masked)
    except Exception as ex:
        log_fn(f"  ⚠ Final polygon mask step skipped ({ex}).")

    log_fn(f"DEM GeoTIFF saved: {dem_path}")


def _enforce_non_negative(tif_path, log_fn):
    """Clamp valid cells in a DEM/HAND raster to ≥ 0.

    HAND values are physically ≥ 0, but bilinear resampling can produce
    small negative numbers right at the edge of nodata patches.  We:
      1. Read the array
      2. Identify valid cells (not nodata, finite)
      3. For valid cells with value < 0, set to 0
      4. Write back in place
    """
    with rasterio.open(tif_path) as src:
        arr = src.read(1).astype("float32")
        nodata = src.nodata
        meta = src.meta.copy()

    # Build mask of valid cells
    if nodata is not None:
        try:
            nd = float(nodata)
            valid = (arr != nd) & np.isfinite(arr)
        except (TypeError, ValueError):
            valid = np.isfinite(arr)
    else:
        valid = np.isfinite(arr)

    n_neg = int(((arr < 0) & valid).sum())
    if n_neg == 0:
        log_fn("  No negative cells found — no clamping needed.")
        return

    # Clamp negatives to 0 (within valid mask only — nodata cells stay nodata)
    n_total_valid = int(valid.sum())
    pct = 100.0 * n_neg / max(n_total_valid, 1)
    log_fn(
        f"  Clamping {n_neg:,} / {n_total_valid:,} valid cells "
        f"({pct:.2f}%) from negative to 0.0 m"
    )
    arr[(arr < 0) & valid] = 0.0

    meta.update(dtype="float32")
    with rasterio.open(tif_path, "w", **meta) as dst:
        dst.write(arr, 1)


def _nearest_neighbour_fill(arr, invalid):
    """Fill cells marked True in *invalid* with the value of their
    nearest valid neighbour.  Returns the filled array.

    Uses scipy.ndimage.distance_transform_edt.
    The *invalid* mask must not cover the ENTIRE array (at least one valid cell).
    """
    from scipy.ndimage import distance_transform_edt
    # distance_transform_edt with return_indices=True and return_distances=False
    # returns an ndarray of shape (2, H, W) — the row/col of each cell's
    # nearest *valid* (False in the mask) neighbour.
    idx = distance_transform_edt(invalid, return_distances=False, return_indices=True)
    filled = arr.copy()
    filled[invalid] = arr[idx[0][invalid], idx[1][invalid]]
    return filled


def _fill_dem_nodata(tif_path, log_fn):
    """Fill every nodata / NaN / negative-nodata cell in a DEM GeoTIFF.

    TRITON treats every cell value as a real terrain elevation, including the
    NODATA_value in the header.  Negative sentinel values (e.g. -9999) are
    interpreted as -9999 m depressions and will absorb all flood water.

    Strategy:
      1. Identify all invalid cells (nodata sentinel + NaN + ±Inf).
      2. Fill them with the nearest valid neighbour elevation.
      3. Clip any remaining non-finite values to the observed valid range.
      4. Write the filled array back; set the GeoTIFF nodata to None so
         _export_ascii writes NODATA_value 0 in the header (a harmless sentinel
         since no cell will equal 0 after filling).
    """
    try:
        from scipy.ndimage import distance_transform_edt  # noqa – imported in helper
    except ImportError:
        raise ImportError(
            "scipy is required for TRITON DEM nodata fill.\n"
            "Install it with:  pip install scipy"
        )

    with rasterio.open(tif_path) as src:
        arr     = src.read(1).astype("float32")
        nodata  = src.nodata
        profile = src.profile.copy()

    # ── Build invalid mask ────────────────────────────────────────────────────
    try:
        nd_val = float(nodata) if nodata is not None else None
    except (TypeError, ValueError):
        nd_val = None

    invalid = ~np.isfinite(arr)
    if nd_val is not None and np.isfinite(nd_val):
        invalid |= (arr == nd_val)

    n_invalid = int(invalid.sum())
    n_total   = arr.size

    if n_invalid == n_total:
        raise RuntimeError(
            "DEM has no valid cells at all — the entire raster is nodata. "
            "Check that the DEM overlaps your AOI."
        )

    if n_invalid == 0:
        log_fn("TRITON DEM: no nodata cells found — all cells are valid.")
        # Still re-write to clear the nodata sentinel from the file header
        profile.update({"nodata": None, "dtype": "float32"})
        profile.pop("compress", None); profile.pop("blockxsize", None)
        profile.pop("blockysize", None); profile.pop("tiled", None)
        with rasterio.open(tif_path, "w", **profile) as dst:
            dst.write(arr, 1)
        return

    log_fn(
        f"TRITON DEM: filling {n_invalid:,} / {n_total:,} nodata/invalid cells "
        f"({100 * n_invalid / n_total:.1f}%) with nearest-neighbour elevation…"
    )

    filled = _nearest_neighbour_fill(arr, invalid)

    # ── Final safety: clip any residual non-finite values ─────────────────────
    # Use the observed valid range (no negative sentinels left).
    valid_min = float(filled[~invalid].min())
    valid_max = float(filled[~invalid].max())
    still_bad = ~np.isfinite(filled)
    if still_bad.any():
        log_fn(f"  Clamping {int(still_bad.sum())} residual non-finite cells to {valid_min:.2f} m.")
        filled[still_bad] = valid_min

    log_fn(
        f"  Fill complete.  Elevation range: {float(filled.min()):.2f} – "
        f"{float(filled.max()):.2f} m"
    )

    # ── Write filled TIF ──────────────────────────────────────────────────────
    # Set nodata=None so the ASCII export writes NODATA_value 0, which is a safe
    # sentinel (no real terrain cell will equal exactly 0 after filling).
    profile.update({"nodata": None, "dtype": "float32"})
    profile.pop("compress", None); profile.pop("blockxsize", None)
    profile.pop("blockysize", None); profile.pop("tiled", None)
    with rasterio.open(tif_path, "w", **profile) as dst:
        dst.write(filled, 1)


def _export_ascii(dem_path, dem_ascii_path, log_fn, is_triton=False):
    with rasterio.open(dem_path) as src:
        dem_arr   = src.read(1)
        nodata_in = src.nodata
        crs       = src.crs
        transform = src.transform
        width     = src.width
        height    = src.height
        log_fn(f"DEM CRS: {crs}  Size: {width}x{height}  Res: {src.res}")

    dem_arr = dem_arr.astype("float32")

    if is_triton:
        # After _fill_dem_nodata the array has no invalid cells and nodata=None.
        # TRITON treats the NODATA_value in the header as real terrain elevation,
        # so we use 0 as a harmless sentinel (no cell should equal exactly 0).
        # Any residual NaN/Inf is clamped to the observed valid range.
        finite_mask = np.isfinite(dem_arr)
        if finite_mask.any():
            vmin = float(dem_arr[finite_mask].min())
            dem_arr[~finite_mask] = vmin
        nodata_out = 0.0
    else:
        try:
            nodata_out = (
                -9999.0
                if (nodata_in is None or not np.isfinite(float(nodata_in)))
                else float(nodata_in)
            )
        except (TypeError, ValueError):
            nodata_out = -9999.0
        dem_arr[~np.isfinite(dem_arr)] = nodata_out

    # Build a clean minimal profile — do NOT copy the GeoTIFF profile
    # (it contains blockxsize/blockysize/compress that AAIGrid rejects)
    ascii_profile = {
        "driver": "AAIGrid",
        "dtype":  "float32",
        "count":  1,
        "crs":    crs,
        "transform": transform,
        "width":  width,
        "height": height,
        "nodata": nodata_out,
    }

    with rasterio.open(dem_ascii_path, "w", **ascii_profile) as dst:
        dst.write(dem_arr, 1)

    log_fn(f"DEM ASCII saved: {dem_ascii_path}")


# ── public API ───────────────────────────────────────────────────────────────

def prepare_dem(ctx_path, ctx: dict, dem_res_m: float,
                has_dem: bool, user_dem_path=None,
                dem_source: str = "3dep",
                skip_ascii: bool = False,
                log_fn=print):
    """Prepare DEM (download or use existing) and export ASCII.

    Parameters
    ----------
    dem_source : str
        When `has_dem=False`, selects the download source:
          - "3dep"  — USGS 3DEP 1/3 arc-second tiles (default, continental US)
          - "hand"  — HAND (Height Above Nearest Drainage) from UT Austin TACC,
                     organised by HUC6.  All values ≥ 0.
    user_dem_path :
        When `has_dem=True`, either a single path (str / Path) or a list of
        paths (tiles will be filtered to those overlapping the AOI and
        then merged by _clip_and_reproject).
    skip_ascii : bool
        When True, do NOT write a `.ascii` / `.asc` ESRI grid alongside the
        GeoTIFF.  LISFLOOD-FP and TRITON workflows leave this False (they
        need the ASCII format).  Standalone DEM mode passes True so the
        user only gets the format they explicitly requested.

    Returns updated ctx dict.
    """
    project_dir = Path(ctx["project_dir"])
    # Support both LISFLOOD (lisflood_dir) and TRITON (triton_dir) workflows
    _model_dir_raw = (
        ctx.get("model_dir")
        or ctx.get("lisflood_dir")
        or ctx.get("triton_dir")
    )
    if not _model_dir_raw:
        raise KeyError("Context missing model output directory (model_dir / lisflood_dir / triton_dir).")
    model_dir = Path(_model_dir_raw)
    model_dir.mkdir(parents=True, exist_ok=True)

    # TRITON uses .asc extension; LISFLOOD uses .ascii
    _is_triton = bool(ctx.get("triton_dir"))
    dem_ascii_stem = "dem"
    dem_ascii_ext  = "asc" if _is_triton else "ascii"

    aoi_path = Path(ctx["aoi_path"])
    aoi_name = ctx["aoi_name"]

    from core.aoi import read_aoi, get_working_crs_epsg
    from core.export import next_free_path
    aoi_gdf = read_aoi(ctx)
    if aoi_gdf.crs is None:
        raise ValueError(f"AOI shapefile has no CRS: {aoi_path}")
    log_fn(f"AOI path: {aoi_path}  CRS: {aoi_gdf.crs}")
    # Resolve the working CRS once and pass it to every clip-and-reproject
    # call so all three branches (user DEM / HAND / 3DEP) write the DEM in
    # the same metric projection.
    working_epsg = int(get_working_crs_epsg(ctx, aoi_gdf=aoi_gdf, log_fn=log_fn))
    # ``next_free_path`` returns the canonical name (e.g. ``dem.ascii``)
    # if it doesn't exist yet, otherwise ``dem (1).ascii``, ``dem (2)
    # .ascii``, …  This keeps previous runs' outputs intact instead of
    # overwriting them.
    dem_ascii_path = next_free_path(model_dir, dem_ascii_stem, dem_ascii_ext)

    if has_dem and user_dem_path:
        # user_dem_path can be either a single path (str/Path) or a list of
        # tile paths — if multiple tiles are supplied, we keep only those that
        # actually overlap the AOI, then _clip_and_reproject mosaics them.
        if isinstance(user_dem_path, (str, Path)):
            user_paths = [Path(user_dem_path)]
        else:
            user_paths = [Path(p) for p in user_dem_path]

        if not user_paths:
            raise ValueError("No user DEM path supplied.")

        # Verify each file exists + has a CRS
        for p in user_paths:
            if not p.exists():
                raise FileNotFoundError(f"DEM not found: {p}")
            with rasterio.open(p) as src:
                if src.crs is None:
                    raise ValueError(
                        f"Provided DEM has no CRS defined: {p.name}\n"
                        "Please assign a coordinate reference system in a GIS tool first."
                    )

        # Filter tiles to those intersecting the AOI bbox.
        # Uses rasterio.warp.transform_bounds for robust CRS handling, plus
        # a small epsilon so tiles that touch the AOI edge are accepted.
        if len(user_paths) > 1:
            from rasterio.warp import transform_bounds as _tb
            aoi_bounds = tuple(aoi_gdf.total_bounds)
            aoi_minx, aoi_miny, aoi_maxx, aoi_maxy = aoi_bounds
            eps = 1e-6  # tiny tolerance against floating-point edge cases
            log_fn(
                f"AOI bounds ({aoi_gdf.crs}): "
                f"({aoi_minx:.4f}, {aoi_miny:.4f}, {aoi_maxx:.4f}, {aoi_maxy:.4f})"
            )

            overlapping = []
            skipped = []
            for p in user_paths:
                with rasterio.open(p) as src:
                    src_crs = src.crs
                    src_b = src.bounds
                # Project source bounds into the AOI's CRS
                try:
                    t_minx, t_miny, t_maxx, t_maxy = _tb(
                        src_crs, aoi_gdf.crs,
                        src_b.left, src_b.bottom, src_b.right, src_b.top,
                        densify_pts=21,
                    )
                except Exception as ex:
                    log_fn(f"  ⚠ {p.name}: bounds reprojection failed ({ex}) — "
                           f"keeping it anyway.")
                    overlapping.append(p)
                    continue

                # Normalise in case transform_bounds returns min/max swapped
                lo_x, hi_x = sorted([t_minx, t_maxx])
                lo_y, hi_y = sorted([t_miny, t_maxy])

                overlaps = (
                    lo_x <= aoi_maxx + eps and hi_x >= aoi_minx - eps and
                    lo_y <= aoi_maxy + eps and hi_y >= aoi_miny - eps
                )
                log_fn(
                    f"  {p.name}: tile bounds in AOI CRS = "
                    f"({lo_x:.4f}, {lo_y:.4f}, {hi_x:.4f}, {hi_y:.4f})  "
                    f"→ {'OVERLAPS' if overlaps else 'no overlap'}"
                )
                if overlaps:
                    overlapping.append(p)
                else:
                    skipped.append(p)

            log_fn(f"User supplied {len(user_paths)} DEM tile(s); "
                   f"{len(overlapping)} overlap, {len(skipped)} skipped.")
            if not overlapping:
                raise RuntimeError(
                    "None of the provided DEM tiles overlap the AOI.\n"
                    "See the log above for each tile's re-projected bounds vs "
                    "the AOI bounds to diagnose the mismatch."
                )
            tiles_to_use = overlapping
        else:
            log_fn(f"Using provided DEM: {user_paths[0].name}")
            with rasterio.open(user_paths[0]) as src:
                log_fn(f"  Source CRS : {src.crs}")
                log_fn(
                    f"  Source size: {src.width} x {src.height} px  |  "
                    f"res: {src.res[0]:.4f} m"
                )
            tiles_to_use = user_paths

        # Clip to AOI, reproject to AOI CRS, resample to requested resolution
        # (exactly the same pipeline as for downloaded 3DEP tiles — handles
        # 1 or many tiles via merge.mosaic).
        dem_path = project_dir / f"dem_clip_to_{aoi_name}.tif"
        log_fn(f"Clipping, reprojecting and resampling to {dem_res_m} m → AOI CRS...")
        _clip_and_reproject(tiles_to_use, aoi_gdf, dem_res_m, dem_path, log_fn,
                            working_crs_epsg=working_epsg)
    elif dem_source == "hand":
        from core.hand import download_hand_for_aoi
        from core.export import next_free_path
        log_fn("Downloading HAND tiles from UT Austin TACC …")
        # Auto-rename with (1), (2), … if a HAND_{aoi}.tif already exists
        dem_path = next_free_path(project_dir, f"HAND_{aoi_name}", "tif")
        hand_cache_dir = project_dir / f"HAND_raw_{aoi_name}"
        tile_paths = download_hand_for_aoi(aoi_gdf, hand_cache_dir, log_fn)
        log_fn(f"Merging + clipping {len(tile_paths)} HAND tile(s) to AOI …")
        _clip_and_reproject(tile_paths, aoi_gdf, dem_res_m, dem_path, log_fn,
                            working_crs_epsg=working_epsg)
        # HAND is physically ≥ 0.  Bilinear resampling at the edges of
        # nodata regions can introduce small negatives — clamp them away.
        log_fn("Enforcing HAND ≥ 0 (clamping bilinear-resample artefacts) …")
        _enforce_non_negative(dem_path, log_fn)

    else:   # 3dep (default)
        from core.export import next_free_path
        log_fn("Downloading DEM from 3DEP...")
        dem_path = next_free_path(project_dir, f"DEM_{aoi_name}", "tif")
        dem_tiles_dir = project_dir / f"DEM_raw_{aoi_name}"
        dem_tiles_dir.mkdir(parents=True, exist_ok=True)

        # Retry up to 2 times when a tile read fails mid-clip.
        # Layer-1/2/3 validation in _dl catches most corrupt tiles before
        # we reach here, but a small window remains (e.g. a tile block that
        # passes a 64×64 spot-check but fails on a different block during the
        # full AOI clip).  On error: delete all cached tiles → re-download.
        _MAX_RETRIES = 2
        for _attempt in range(_MAX_RETRIES + 1):
            tile_paths = _download_3dep_tiles(
                aoi_gdf, dem_res_m, dem_tiles_dir, log_fn
            )
            try:
                _clip_and_reproject(
                    tile_paths, aoi_gdf, dem_res_m, dem_path, log_fn,
                    working_crs_epsg=working_epsg,
                )
                break   # success — exit retry loop
            except Exception as _exc:
                _is_tile_read_err = any(
                    kw in str(_exc)
                    for kw in ("TIFFRead", "IReadBlock",
                               "Read failed", "CPLE_AppDefined",
                               "TIFFFillTile", "RasterioIOError")
                )
                if _attempt < _MAX_RETRIES and _is_tile_read_err:
                    log_fn(
                        f"  ⚠ Corrupt tile detected on attempt "
                        f"{_attempt + 1}/{_MAX_RETRIES + 1} — "
                        f"purging cache and re-downloading all tiles…"
                    )
                    for _tp in tile_paths:
                        try:
                            _tp.unlink()
                        except Exception:
                            pass
                    continue   # retry
                raise           # non-tile error or retries exhausted

    # TRITON requires a DEM with NO nodata cells — fill before ASCII export
    if _is_triton:
        log_fn("TRITON mode: filling any nodata cells in DEM before ASCII export…")
        _fill_dem_nodata(dem_path, log_fn)

    if skip_ascii:
        log_fn("Skipping ASCII export (standalone mode — only GeoTIFF needed).")
        dem_ascii_path = None
    else:
        log_fn("Converting DEM to ASCII...")
        _export_ascii(dem_path, dem_ascii_path, log_fn, is_triton=_is_triton)

    ctx["has_dem"] = has_dem
    if has_dem:
        ctx["dem_source"] = "user_provided"
    elif dem_source == "hand":
        ctx["dem_source"] = "download_hand"
    else:
        ctx["dem_source"] = "download_3dep"
    ctx["dem_res_m"] = dem_res_m
    ctx["dem_path"] = str(dem_path)
    ctx["dem_tif_path"] = str(dem_path)
    ctx["dem_ascii_path"] = str(dem_ascii_path) if dem_ascii_path else None
    # Record the ACTUAL filename written (may be dem (1).ascii etc.) so
    # the PAR step references the right file.
    ctx["par_dem_name"] = dem_ascii_path.name if dem_ascii_path else "dem.ascii"
    ctx["dem_prepared"] = True
    save_context(ctx_path, ctx)

    log_fn("DEM step complete.")
    return ctx
