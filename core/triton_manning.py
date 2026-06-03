"""TRITON step — LULC download and Manning friction raster generation.

Output for varying friction mode is a HEADERLESS ASCII matrix
(friction.asc) — rows of space-separated float values with no ESRI
AAIGrid header.  Fixed friction mode stores fpfric in context only;
no file is written.
"""
import json
import math
import os
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
import requests
from rasterio.features import geometry_mask
from rasterio.merge import merge
from rasterio.warp import reproject, Resampling

from core.context import save_context


# ── default ESRI LULC → Manning mapping ──────────────────────────────────────

LULC_TO_N = {
    1: 0.035,    # Water
    2: 0.15,     # Trees
    4: 0.07,     # Flooded vegetation
    5: 0.05,     # Crops
    7: 0.025,    # Built area
    8: 0.05,     # Bare ground
    "default": 0.045,
}


# ── internal helpers (inlined from manning.py) ────────────────────────────────

def _safe_delete(path):
    try:
        p = Path(path)
        if p.exists():
            p.unlink()
    except Exception:
        pass


def _atomic_write_gtiff(array2d, out_path, crs, transform, dtype, nodata,
                        compress="lzw"):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.stem + "__tmp.tif")
    _safe_delete(tmp)
    _safe_delete(out_path)
    profile = {
        "driver": "GTiff",
        "width": int(array2d.shape[1]),
        "height": int(array2d.shape[0]),
        "count": 1,
        "crs": crs,
        "transform": transform,
        "dtype": dtype,
        "nodata": nodata,
        "compress": compress,
        "BIGTIFF": "IF_SAFER",
    }
    with rasterio.open(tmp, "w", **profile) as dst:
        dst.write(array2d.astype(dtype), 1)
    os.replace(tmp, out_path)


def _validate_raster(path, label="Raster"):
    """Validate a user-supplied raster file before processing."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"{label} not found: {p}")
    try:
        with rasterio.open(p) as src:
            if src.count < 1:
                raise ValueError(f"{label} has no bands: {p}")
            if src.crs is None:
                raise ValueError(
                    f"{label} has no CRS defined: {p}\n"
                    "Please assign a coordinate reference system in a GIS tool "
                    "before using this file."
                )
            return src.crs, src.dtypes[0], src.nodata, src.width, src.height
    except rasterio.errors.RasterioIOError as e:
        raise ValueError(
            f"{label} could not be opened — is it a valid GeoTIFF? ({e})"
        )


def _check_manning_values(tif_path, log_fn):
    """Warn if Manning n values look suspicious."""
    with rasterio.open(tif_path) as src:
        arr = src.read(1).astype("float32")
        nodata = src.nodata
    mask = np.isfinite(arr)
    if nodata is not None and np.isfinite(float(nodata)):
        mask &= arr != float(nodata)
    valid = arr[mask]
    if valid.size == 0:
        raise ValueError("Manning raster contains no valid (non-nodata) cells.")
    vmin, vmax = float(valid.min()), float(valid.max())
    log_fn(f"Manning n value range: {vmin:.4f} – {vmax:.4f}")
    if vmin < 0.001 or vmax > 2.0:
        log_fn(
            f"WARNING: Manning n values outside typical range (0.001–1.0). "
            f"Min={vmin:.4f}, Max={vmax:.4f}. Please verify your raster."
        )


def _reproject_to_snap(src_path, snap_path, out_path, categorical=True,
                       dst_nodata=None):
    """Reproject and resample src_path to match the grid of snap_path."""
    with rasterio.open(src_path) as src, rasterio.open(snap_path) as snap:
        if categorical:
            dtype, nodata = "int16", (0 if dst_nodata is None else dst_nodata)
            dst_arr = np.full((snap.height, snap.width), nodata, dtype=np.int16)
            resampling = Resampling.nearest
        else:
            dtype, nodata = "float32", (-9999.0 if dst_nodata is None else dst_nodata)
            dst_arr = np.full((snap.height, snap.width), nodata, dtype=np.float32)
            resampling = Resampling.bilinear

        src_nodata = src.nodata
        try:
            if src_nodata is not None and not np.isfinite(float(src_nodata)):
                src_nodata = None
        except (TypeError, ValueError):
            src_nodata = None

        reproject(
            source=rasterio.band(src, 1),
            destination=dst_arr,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=snap.transform,
            dst_crs=snap.crs,
            src_nodata=src_nodata,
            dst_nodata=nodata,
            resampling=resampling,
        )
        _atomic_write_gtiff(
            dst_arr, out_path, snap.crs, snap.transform, dtype, nodata
        )


def _apply_aoi_mask(tif_path, aoi_gdf, nodata_value):
    """Zero-out (set to nodata) all cells outside the AOI polygon."""
    with rasterio.open(tif_path) as src:
        aoi_reproj = aoi_gdf.to_crs(src.crs)
        shapes = [
            g for g in aoi_reproj.geometry if g is not None and not g.is_empty
        ]
        arr = src.read(1)
        crs, transform = src.crs, src.transform
        width, height = src.width, src.height
        dtype = arr.dtype.name

    mask_arr = geometry_mask(
        shapes, transform=transform, invert=True, out_shape=(height, width)
    )
    arr[~mask_arr] = nodata_value
    _atomic_write_gtiff(arr, tif_path, crs, transform, dtype, nodata_value)


def _create_manning_from_lulc(lulc_tif, manning_tif, mapping):
    """Convert an integer LULC raster to a float Manning n raster."""
    with rasterio.open(lulc_tif) as src:
        lulc = src.read(1)
        crs, transform, nodata_lulc = src.crs, src.transform, src.nodata

    nodata_manning = -9999.0
    manning = np.full(lulc.shape, float(mapping["default"]), dtype=np.float32)
    for cls, nval in mapping.items():
        if cls == "default":
            continue
        manning[lulc == int(cls)] = float(nval)

    if nodata_lulc is not None:
        manning[lulc == nodata_lulc] = nodata_manning
    else:
        manning[lulc == 0] = nodata_manning

    _atomic_write_gtiff(manning, manning_tif, crs, transform, "float32", nodata_manning)


def _download_esri_lulc(aoi_gdf, snap_path, lulc_year, out_lulc_path, log_fn):
    """Download ESRI Sentinel-2 10 m LULC tiles and mosaic/reproject to snap grid."""
    IMAGE_SERVER_URL = (
        "https://ic.imagery1.arcgis.com/arcgis/rest/services/"
        "Sentinel2_10m_LandCover/ImageServer"
    )
    EXPORT_URL = IMAGE_SERVER_URL + "/exportImage"
    PIXEL_SIZE_3857 = 10.0
    TILE_PX = 1024
    TILE_SIZE_M = TILE_PX * PIXEL_SIZE_3857
    MAX_RETRIES, BASE_SLEEP = 6, 1.5

    out_lulc_path = Path(out_lulc_path)
    tiles_dir = out_lulc_path.parent / f"_tiles_{out_lulc_path.stem}"
    tmp_mosaic = out_lulc_path.parent / f"_tmp_{out_lulc_path.stem}_3857.tif"
    tiles_dir.mkdir(parents=True, exist_ok=True)
    _safe_delete(tmp_mosaic)
    _safe_delete(out_lulc_path)

    with rasterio.open(snap_path) as snap:
        snap_crs, snap_transform = snap.crs, snap.transform
        snap_width, snap_height = snap.width, snap.height

    aoi_3857 = aoi_gdf.to_crs(epsg=3857)
    xmin, ymin, xmax, ymax = map(float, aoi_3857.total_bounds)
    log_fn(
        f"Downloading LULC year {lulc_year} from ESRI Sentinel-2 10m service..."
    )

    mosaic_rule = {
        "mosaicMethod": "esriMosaicAttribute",
        "where": f"Year = {lulc_year}",
    }
    nx = int(math.ceil((xmax - xmin) / TILE_SIZE_M))
    ny = int(math.ceil((ymax - ymin) / TILE_SIZE_M))
    total_tiles = nx * ny

    def _dl_tile(ix, iy):
        x0 = xmin + ix * TILE_SIZE_M
        x1 = min(xmin + (ix + 1) * TILE_SIZE_M, xmax)
        y0 = ymin + iy * TILE_SIZE_M
        y1 = min(ymin + (iy + 1) * TILE_SIZE_M, ymax)
        w = min(int(max(1, round((x1 - x0) / PIXEL_SIZE_3857))), TILE_PX)
        h = min(int(max(1, round((y1 - y0) / PIXEL_SIZE_3857))), TILE_PX)
        tile_path = tiles_dir / f"lulc_{lulc_year}_{iy:03d}_{ix:03d}.tif"
        if tile_path.exists() and tile_path.stat().st_size > 0:
            return tile_path, True
        params = {
            "bbox": f"{x0},{y0},{x1},{y1}",
            "bboxSR": 3857,
            "imageSR": 3857,
            "size": f"{w},{h}",
            "format": "tiff",
            "f": "image",
            "mosaicRule": json.dumps(mosaic_rule),
        }
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                r = requests.get(
                    EXPORT_URL, params=params, stream=True, timeout=300
                )
                if r.status_code == 200:
                    with open(tile_path, "wb") as fh:
                        for chunk in r.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                fh.write(chunk)
                    return tile_path, True
            except Exception:
                pass
            if attempt < MAX_RETRIES:
                time.sleep(BASE_SLEEP * attempt)
        return tile_path, False

    tile_paths, done, ok_count = [], 0, 0
    for iy in range(ny):
        for ix in range(nx):
            p, ok = _dl_tile(ix, iy)
            done += 1
            if ok:
                tile_paths.append(p)
                ok_count += 1
            if done % 10 == 0 or done == total_tiles:
                log_fn(
                    f"  LULC progress: {done}/{total_tiles} | ready: {ok_count}"
                )

    if ok_count == 0:
        raise RuntimeError("No LULC tiles downloaded successfully.")

    srcs = [rasterio.open(str(p)) for p in tile_paths]
    mosaic_arr, mosaic_transform = merge(srcs, method="first")
    mosaic_band, mosaic_crs = mosaic_arr[0], srcs[0].crs
    for s in srcs:
        s.close()

    _atomic_write_gtiff(
        mosaic_band,
        tmp_mosaic,
        mosaic_crs,
        mosaic_transform,
        np.dtype(mosaic_band.dtype).name,
        0,
    )

    with rasterio.open(tmp_mosaic) as src:
        lulc_reproj = np.full((snap_height, snap_width), 0, dtype=np.int16)
        reproject(
            source=rasterio.band(src, 1),
            destination=lulc_reproj,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=snap_transform,
            dst_crs=snap_crs,
            dst_nodata=0,
            resampling=Resampling.nearest,
        )

    aoi_in_dst = aoi_gdf.to_crs(snap_crs)
    mask_arr = geometry_mask(
        [g for g in aoi_in_dst.geometry if g is not None and not g.is_empty],
        transform=snap_transform,
        invert=True,
        out_shape=(snap_height, snap_width),
    )
    lulc_reproj[~mask_arr] = 0
    _atomic_write_gtiff(
        lulc_reproj, out_lulc_path, snap_crs, snap_transform, "int16", 0
    )
    _safe_delete(tmp_mosaic)
    log_fn(f"LULC raster saved: {out_lulc_path}")


def _write_headerless_ascii(tif_path, out_path):
    """Write a GeoTIFF band as a headerless space-separated ASCII matrix for TRITON.

    TRITON's friction file has NO header and NO nodata mechanism — every cell
    must contain a valid Manning n value.  Cells outside the AOI (nodata / NaN)
    are filled with the nearest valid neighbour before writing.
    """
    try:
        from scipy.ndimage import distance_transform_edt
        have_scipy = True
    except ImportError:
        have_scipy = False

    with rasterio.open(tif_path) as src:
        data = src.read(1).astype(np.float32)
        nd   = src.nodata

    # Build invalid mask (nodata sentinel + NaN/Inf)
    try:
        nd_val = float(nd) if nd is not None else None
    except (TypeError, ValueError):
        nd_val = None

    invalid = ~np.isfinite(data)
    if nd_val is not None and np.isfinite(nd_val):
        invalid |= (data == nd_val)

    n_invalid = int(invalid.sum())

    if n_invalid > 0:
        if not have_scipy:
            raise ImportError(
                "scipy is required to fill nodata cells in the friction file.\n"
                "Install it with:  pip install scipy"
            )
        if invalid.all():
            raise RuntimeError(
                "Friction raster has no valid Manning n cells — "
                "the entire raster is nodata. Check your AOI and raster overlap."
            )
        idx = distance_transform_edt(invalid, return_distances=False, return_indices=True)
        data[invalid] = data[idx[0][invalid], idx[1][invalid]]
        # Final safety — clamp any residual non-finite to valid range min
        still_bad = ~np.isfinite(data)
        if still_bad.any():
            data[still_bad] = float(data[~still_bad].min())

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        for row in data:
            f.write(" ".join(f"{v:.6f}" for v in row) + "\n")


# ── public API ────────────────────────────────────────────────────────────────

def prepare_triton_manning(
    ctx_path,
    ctx: dict,
    fric_mode: str,                         # "fixed" | "varying"
    fpfric_val: float = None,
    lulc_source: str = "download",          # "download" | "download_nlcd" | "user_lulc" | "user_manning"
    user_lulc_path: str = None,
    user_manning_path: str = None,
    dem_res_m: float = 10.0,
    lulc_class_to_n: dict = None,
    lulc_year: int = None,                  # passed directly from GUI (no longer read from ctx)
    nlcd_year: str = "2021",
    log_fn=print,
):
    """Prepare TRITON Manning friction input.

    Fixed mode: stores fpfric in context; no file written.
    Varying mode: produces {triton_dir}/friction.asc as a headerless ASCII matrix.

    Returns updated ctx.
    """
    if fric_mode not in ("fixed", "varying"):
        raise ValueError(
            f"fric_mode must be 'fixed' or 'varying', got '{fric_mode}'."
        )

    project_dir = Path(ctx["project_dir"])
    triton_dir = Path(ctx["triton_dir"])
    aoi_path = Path(ctx["aoi_path"])
    aoi_name = ctx["aoi_name"]
    dem_tif_path = Path(ctx.get("dem_tif_path") or ctx["dem_path"])

    if not dem_tif_path.exists():
        raise FileNotFoundError(f"DEM not found: {dem_tif_path}")

    # ── fixed friction ────────────────────────────────────────────────────────
    if fric_mode == "fixed":
        if fpfric_val is None or fpfric_val <= 0:
            raise ValueError("Fixed Manning n (fpfric_val) must be > 0.")
        ctx["triton_fric_mode"] = "fixed"
        ctx["par_fpfric"] = fpfric_val
        ctx["par_use_fpfric"] = True
        ctx["par_use_manningfile"] = False
        ctx["triton_friction_path"] = None
        save_context(ctx_path, ctx)
        log_fn(f"Fixed Manning n = {fpfric_val}  (no friction.asc written)")
        return ctx

    # ── varying friction ──────────────────────────────────────────────────────
    log_fn("Varying Manning mode selected.")
    if lulc_source not in ("download", "download_nlcd", "user_lulc", "user_manning"):
        raise ValueError(
            f"lulc_source must be 'download', 'download_nlcd', 'user_lulc', or "
            f"'user_manning', got '{lulc_source}'."
        )

    from core.aoi import read_aoi
    aoi_gdf = read_aoi(ctx)
    mapping = lulc_class_to_n or LULC_TO_N
    manning_tif_path = project_dir / f"ManningN_{aoi_name}.tif"
    lulc_tif_path = None
    friction_asc_path = triton_dir / "friction.asc"

    if lulc_source == "user_manning":
        # User supplies a ready-made Manning raster
        if not user_manning_path:
            raise ValueError(
                "user_manning_path must be provided when lulc_source='user_manning'."
            )
        src = Path(user_manning_path)
        log_fn(f"Validating Manning raster: {src.name}")
        crs, dtype, nodata, w, h = _validate_raster(src, "Manning raster")
        log_fn(f"  CRS: {crs}  |  Size: {w}x{h}  |  dtype: {dtype}")
        log_fn(
            "Reprojecting/resampling Manning raster to match DEM grid "
            "(CRS, cell size, extent)..."
        )
        _reproject_to_snap(
            src, dem_tif_path, manning_tif_path, categorical=False, dst_nodata=-9999.0
        )
        log_fn("Applying AOI mask (clipping to AOI polygon)...")
        _apply_aoi_mask(manning_tif_path, aoi_gdf, -9999.0)
        log_fn("Checking Manning n value range...")
        _check_manning_values(manning_tif_path, log_fn)
        lulc_used = "user_manning_raster"

    else:
        # LULC → Manning pathway
        if lulc_source == "user_lulc":
            if not user_lulc_path:
                raise ValueError(
                    "user_lulc_path must be provided when lulc_source='user_lulc'."
                )
            src = Path(user_lulc_path)
            log_fn(f"Validating LULC raster: {src.name}")
            crs, dtype, nodata, w, h = _validate_raster(src, "LULC raster")
            log_fn(f"  CRS: {crs}  |  Size: {w}x{h}  |  dtype: {dtype}")
            lulc_tif_path = project_dir / f"LULC_{aoi_name}.tif"
            log_fn(
                "Reprojecting/resampling LULC raster to match DEM grid "
                "(CRS, cell size, extent)..."
            )
            _reproject_to_snap(
                src, dem_tif_path, lulc_tif_path, categorical=True, dst_nodata=0
            )
            log_fn("Applying AOI mask (clipping to AOI polygon)...")
            _apply_aoi_mask(lulc_tif_path, aoi_gdf, 0)
            lulc_used = "user_lulc_raster"

        elif lulc_source == "download_nlcd":
            from core.nlcd import download_nlcd as _dl_nlcd
            lulc_tif_path = (
                project_dir / f"LULC_NLCD_{aoi_name}_{nlcd_year}.tif"
            )
            # DEM is always in a metric working CRS now, so ``src.res[0]``
            # is real metres.  Surface clear errors if either invariant is
            # somehow violated instead of letting download_nlcd try to
            # allocate a multi-gigapixel array.
            with rasterio.open(dem_tif_path) as src:
                _dem_cell = float(abs(src.res[0]))
                _dem_is_geo = (
                    src.crs is not None
                    and getattr(src.crs, "is_geographic", False)
                )
            if _dem_is_geo:
                raise RuntimeError(
                    f"DEM at {dem_tif_path} is in a geographic CRS "
                    f"({src.crs}).  Re-run the DEM step — every DEM "
                    "should land in the working metric CRS."
                )
            if not (0.5 <= _dem_cell <= 5000.0):
                raise ValueError(
                    f"DEM cell size {_dem_cell:.6f} m is outside the "
                    "supported range (0.5–5000 m).  Check the DEM step."
                )
            _dl_nlcd(aoi_gdf, _dem_cell, str(lulc_tif_path),
                     year=nlcd_year, log_fn=log_fn)
            # Snap NLCD to DEM grid (categorical → nearest)
            _snap_tmp = lulc_tif_path.with_suffix(".snap.tif")
            _reproject_to_snap(lulc_tif_path, dem_tif_path, _snap_tmp,
                               categorical=True, dst_nodata=0)
            _snap_tmp.replace(lulc_tif_path)
            _apply_aoi_mask(lulc_tif_path, aoi_gdf, 0)
            ctx["nlcd_year"] = str(nlcd_year)
            lulc_used = "download_nlcd"

        else:  # download (ESRI Sentinel-2)
            if lulc_year is None:
                lulc_year = ctx.get("lulc_year")  # fall back to ctx if not passed
            if lulc_year is None:
                raise ValueError(
                    "lulc_year is required for LULC download mode. "
                    "Please select a year in the Friction step."
                )
            ctx["lulc_year"] = int(lulc_year)     # save for reference
            lulc_tif_path = (
                project_dir / f"LULC_{aoi_name}_{lulc_year}.tif"
            )
            _download_esri_lulc(
                aoi_gdf, dem_tif_path, lulc_year, lulc_tif_path, log_fn
            )
            lulc_used = "download_esri"

        # Display unique LULC classes
        with rasterio.open(lulc_tif_path) as src:
            lulc_arr = src.read(1)
            lulc_nodata = src.nodata
        vals = np.unique(lulc_arr)
        if lulc_nodata is not None:
            vals = vals[vals != lulc_nodata]
        log_fn(f"Unique LULC classes: {vals.tolist()}")

        log_fn("Creating Manning raster from LULC...")
        _create_manning_from_lulc(lulc_tif_path, manning_tif_path, mapping)
        _check_manning_values(manning_tif_path, log_fn)

    # Write headerless ASCII friction file for TRITON.
    # Nodata cells (outside AOI) are filled with nearest-neighbour before writing —
    # TRITON has no nodata mechanism for this file; every cell must be a valid n.
    log_fn(f"Writing headerless friction.asc (filling any nodata cells)…")
    _write_headerless_ascii(manning_tif_path, friction_asc_path)
    log_fn(f"friction.asc written: {friction_asc_path}")

    # Update context
    ctx["triton_fric_mode"] = "varying"
    ctx["par_fpfric"] = None
    ctx["par_use_fpfric"] = False
    ctx["par_use_manningfile"] = True
    ctx["triton_friction_path"] = str(friction_asc_path)
    ctx["lulc_source"] = lulc_used
    ctx["lulc_path"] = str(lulc_tif_path) if lulc_tif_path else None
    ctx["manning_tif_path"] = str(manning_tif_path)
    ctx["manning_mapping"] = {str(k): v for k, v in mapping.items()}
    save_context(ctx_path, ctx)
    return ctx
