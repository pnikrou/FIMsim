"""Step 5 — LULC download and Manning raster generation."""
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
from rasterio.mask import mask as rio_mask

from core.context import save_context


# ── default ESRI LULC → Manning mapping ─────────────────────────────────────
DEFAULT_MANNING_MAP = {
    1: 0.035,   # Water
    2: 0.15,    # Trees
    4: 0.07,    # Flooded vegetation
    5: 0.05,    # Crops
    7: 0.025,   # Built area
    8: 0.05,    # Bare ground
    "default": 0.045,
}


# ── internal helpers ─────────────────────────────────────────────────────────

def _safe_delete(path):
    try:
        p = Path(path)
        if p.exists():
            p.unlink()
    except Exception:
        pass


def _atomic_write_gtiff(array2d, out_path, crs, transform, dtype, nodata, compress="lzw"):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.stem + "__tmp.tif")
    _safe_delete(tmp)
    _safe_delete(out_path)
    profile = {
        "driver": "GTiff", "width": int(array2d.shape[1]), "height": int(array2d.shape[0]),
        "count": 1, "crs": crs, "transform": transform, "dtype": dtype,
        "nodata": nodata, "compress": compress, "BIGTIFF": "IF_SAFER",
    }
    with rasterio.open(tmp, "w", **profile) as dst:
        dst.write(array2d.astype(dtype), 1)
    os.replace(tmp, out_path)


def _write_ascii_from_tif(src_tif, out_ascii, out_dtype="float32", nodata_fallback=-9999.0):
    src_tif = Path(src_tif)
    out_ascii = Path(out_ascii)
    out_ascii.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_ascii.with_name(out_ascii.stem + "__tmp" + out_ascii.suffix)
    _safe_delete(tmp)
    _safe_delete(out_ascii)
    # Also pre-clean any leftover projection / aux files from previous runs
    for ext in (".prj", ".asc.aux.xml", ".ascii.aux.xml"):
        _safe_delete(out_ascii.with_suffix("").with_suffix(ext) if ext.startswith(".asc")
                     else out_ascii.parent / (out_ascii.name + ext[ext.index("."):]))
    _safe_delete(tmp.parent / (tmp.stem + ".prj"))

    with rasterio.open(src_tif) as src:
        arr = src.read(1)
        nodata_in = src.nodata
        crs = src.crs
        transform = src.transform
        width, height = src.width, src.height

    try:
        nodata_out = nodata_fallback if (nodata_in is None or not np.isfinite(float(nodata_in))) else float(nodata_in)
    except (TypeError, ValueError):
        nodata_out = nodata_fallback

    # Clean minimal profile — avoid copying GeoTIFF-specific keys (blockxsize etc.)
    ascii_profile = {
        "driver": "AAIGrid",
        "dtype": out_dtype,
        "count": 1,
        "crs": crs,
        "transform": transform,
        "width": width,
        "height": height,
        "nodata": nodata_out,
    }
    with rasterio.open(tmp, "w", **ascii_profile) as dst:
        dst.write(arr.astype(out_dtype), 1)
    os.replace(tmp, out_ascii)
    # Rename the companion .prj file that AAIGrid driver creates
    tmp_prj = tmp.parent / (tmp.stem + ".prj")
    out_prj = out_ascii.parent / (out_ascii.stem + ".prj")
    if tmp_prj.exists():
        os.replace(tmp_prj, out_prj)


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
                    "Please assign a coordinate reference system in a GIS tool before using this file."
                )
            return src.crs, src.dtypes[0], src.nodata, src.width, src.height
    except rasterio.errors.RasterioIOError as e:
        raise ValueError(f"{label} could not be opened — is it a valid GeoTIFF? ({e})")


def _check_manning_values(tif_path, log_fn):
    """Warn if Manning n values look suspicious."""
    with rasterio.open(tif_path) as src:
        arr = src.read(1).astype("float32")
        nodata = src.nodata
    mask = np.isfinite(arr)
    if nodata is not None and np.isfinite(float(nodata)):
        mask &= (arr != float(nodata))
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


def _reproject_to_snap(src_path, snap_path, out_path, categorical=True, dst_nodata=None):
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
        # Sanitize src_nodata — rasterio reproject can't handle inf/nan
        try:
            if src_nodata is not None and not np.isfinite(float(src_nodata)):
                src_nodata = None
        except (TypeError, ValueError):
            src_nodata = None

        reproject(
            source=rasterio.band(src, 1), destination=dst_arr,
            src_transform=src.transform, src_crs=src.crs,
            dst_transform=snap.transform, dst_crs=snap.crs,
            src_nodata=src_nodata, dst_nodata=nodata, resampling=resampling,
        )
        _atomic_write_gtiff(dst_arr, out_path, snap.crs, snap.transform, dtype, nodata)


def _apply_aoi_mask(tif_path, aoi_gdf, nodata_value):
    """Zero-out (set to nodata) all cells outside the AOI polygon."""
    with rasterio.open(tif_path) as src:
        aoi_reproj = aoi_gdf.to_crs(src.crs)
        shapes = [g for g in aoi_reproj.geometry if g is not None and not g.is_empty]
        arr = src.read(1)
        crs, transform = src.crs, src.transform
        width, height = src.width, src.height
        dtype = arr.dtype.name

    mask_arr = geometry_mask(shapes, transform=transform, invert=True,
                             out_shape=(height, width))
    arr[~mask_arr] = nodata_value
    _atomic_write_gtiff(arr, tif_path, crs, transform, dtype, nodata_value)


def _create_manning_from_lulc(lulc_tif, manning_tif, mapping):
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


def _download_lulc_to_dem_grid(aoi_gdf, snap_path, lulc_year, out_lulc_path, log_fn):
    IMAGE_SERVER_URL = "https://ic.imagery1.arcgis.com/arcgis/rest/services/Sentinel2_10m_LandCover/ImageServer"
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
    log_fn(f"Downloading LULC year {lulc_year} from ESRI Sentinel-2 10m service...")

    mosaic_rule = {"mosaicMethod": "esriMosaicAttribute", "where": f"Year = {lulc_year}"}
    nx = int(math.ceil((xmax - xmin) / TILE_SIZE_M))
    ny = int(math.ceil((ymax - ymin) / TILE_SIZE_M))
    total_tiles = nx * ny

    def _dl_tile(ix, iy):
        x0, x1 = xmin + ix * TILE_SIZE_M, min(xmin + (ix + 1) * TILE_SIZE_M, xmax)
        y0, y1 = ymin + iy * TILE_SIZE_M, min(ymin + (iy + 1) * TILE_SIZE_M, ymax)
        w = min(int(max(1, round((x1 - x0) / PIXEL_SIZE_3857))), TILE_PX)
        h = min(int(max(1, round((y1 - y0) / PIXEL_SIZE_3857))), TILE_PX)
        tile_path = tiles_dir / f"lulc_{lulc_year}_{iy:03d}_{ix:03d}.tif"
        if tile_path.exists() and tile_path.stat().st_size > 0:
            return tile_path, True
        params = {
            "bbox": f"{x0},{y0},{x1},{y1}", "bboxSR": 3857, "imageSR": 3857,
            "size": f"{w},{h}", "format": "tiff", "f": "image",
            "mosaicRule": json.dumps(mosaic_rule),
        }
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                r = requests.get(EXPORT_URL, params=params, stream=True, timeout=300)
                if r.status_code == 200:
                    with open(tile_path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                f.write(chunk)
                    return tile_path, True
            except Exception:
                pass
            if attempt < MAX_RETRIES:
                time.sleep(BASE_SLEEP * attempt)
        return tile_path, False

    tile_paths, done, ok_count = [], 0, 0
    # Log every tile when there aren't many (so a small AOI doesn't look
    # frozen waiting for the next batch of 10).  For larger AOIs fall back
    # to every-5-tiles to avoid log spam.
    log_every = 1 if total_tiles <= 20 else 5
    log_fn(f"  LULC tile grid: {nx} × {ny} = {total_tiles} tile(s) to fetch.")
    for iy in range(ny):
        for ix in range(nx):
            p, ok = _dl_tile(ix, iy)
            done += 1
            if ok:
                tile_paths.append(p)
                ok_count += 1
            if done % log_every == 0 or done == total_tiles:
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

    _atomic_write_gtiff(mosaic_band, tmp_mosaic, mosaic_crs, mosaic_transform,
                        np.dtype(mosaic_band.dtype).name, 0)

    with rasterio.open(tmp_mosaic) as src:
        lulc_reproj = np.full((snap_height, snap_width), 0, dtype=np.int16)
        reproject(
            source=rasterio.band(src, 1), destination=lulc_reproj,
            src_transform=src.transform, src_crs=src.crs,
            dst_transform=snap_transform, dst_crs=snap_crs,
            dst_nodata=0, resampling=Resampling.nearest,
        )

    aoi_in_dst = aoi_gdf.to_crs(snap_crs)
    mask_arr = geometry_mask(
        [g for g in aoi_in_dst.geometry if g is not None and not g.is_empty],
        transform=snap_transform, invert=True, out_shape=(snap_height, snap_width),
    )
    lulc_reproj[~mask_arr] = 0
    _atomic_write_gtiff(lulc_reproj, out_lulc_path, snap_crs, snap_transform, "int16", 0)
    _safe_delete(tmp_mosaic)
    log_fn(f"LULC raster saved: {out_lulc_path}")


# ── public API ────────────────────────────────────────────────────────────────

def prepare_manning(ctx_path, ctx: dict,
                    fric_mode: str,         # "fixed" or "varying"
                    fpfric_val: float = None,
                    have_manning_raster: bool = False,
                    manning_src_path: str = None,
                    have_lulc: bool = False,
                    lulc_src_path: str = None,
                    lulc_year: int = None,
                    lulc_download_source: str = "esri",   # "esri" | "nlcd"
                    nlcd_year: str = "2021",
                    manning_mapping: dict = None,
                    log_fn=print):
    """Prepare Manning n file.  Returns updated ctx."""

    project_dir = Path(ctx["project_dir"])
    lisflood_dir = Path(ctx["lisflood_dir"])
    aoi_path = Path(ctx["aoi_path"])
    aoi_name = ctx["aoi_name"]
    dem_tif_path = Path(ctx.get("dem_tif_path") or ctx["dem_path"])

    if not dem_tif_path.exists():
        raise FileNotFoundError(f"DEM not found: {dem_tif_path}")

    from core.aoi import read_aoi
    from core.export import next_free_path
    aoi_gdf = read_aoi(ctx)
    # Use ``next_free_path`` so re-running the step doesn't clobber
    # previous outputs: ``lulc.ascii`` → ``lulc (1).ascii`` → …
    manning_ascii_path = next_free_path(lisflood_dir, "lulc", "ascii")

    if fric_mode == "fixed":
        if fpfric_val is None or fpfric_val <= 0:
            raise ValueError("Fixed Manning n must be > 0.")
        ctx["floodplain_friction_mode"] = "fixed"
        ctx["fric_mode"] = "fixed"
        ctx["par_fpfric"] = fpfric_val
        ctx["par_use_fpfric"] = True
        ctx["par_use_manningfile"] = False
        ctx["par_manningfile_name"] = None
        ctx["lulc_path"] = None
        ctx["manning_tif_path"] = None
        ctx["manning_ascii_path"] = None
        save_context(ctx_path, ctx)
        log_fn(f"Fixed Manning n = {fpfric_val}")
        return ctx

    # --- varying ---
    log_fn("Varying Manning mode selected.")
    mapping = manning_mapping or DEFAULT_MANNING_MAP

    manning_tif_path = project_dir / f"ManningN_{aoi_name}.tif"
    lulc_path = None

    if have_manning_raster and manning_src_path:
        src = Path(manning_src_path)
        log_fn(f"Validating Manning raster: {src.name}")
        crs, dtype, nodata, w, h = _validate_raster(src, "Manning raster")
        log_fn(f"  CRS: {crs}  |  Size: {w}x{h}  |  dtype: {dtype}")
        log_fn("Reprojecting/resampling Manning raster to match DEM grid (CRS, cell size, extent)...")
        _reproject_to_snap(src, dem_tif_path, manning_tif_path, categorical=False, dst_nodata=-9999.0)
        log_fn("Applying AOI mask (clipping to AOI polygon)...")
        _apply_aoi_mask(manning_tif_path, aoi_gdf, -9999.0)
        log_fn("Checking Manning n value range...")
        _check_manning_values(manning_tif_path, log_fn)
        lulc_source = "user_manning_raster"

    else:
        if have_lulc and lulc_src_path:
            src = Path(lulc_src_path)
            log_fn(f"Validating LULC raster: {src.name}")
            crs, dtype, nodata, w, h = _validate_raster(src, "LULC raster")
            log_fn(f"  CRS: {crs}  |  Size: {w}x{h}  |  dtype: {dtype}")
            lulc_path = project_dir / f"LULC_{aoi_name}.tif"
            log_fn("Reprojecting/resampling LULC raster to match DEM grid (CRS, cell size, extent)...")
            _reproject_to_snap(src, dem_tif_path, lulc_path, categorical=True, dst_nodata=0)
            log_fn("Applying AOI mask (clipping to AOI polygon)...")
            _apply_aoi_mask(lulc_path, aoi_gdf, 0)
            lulc_source = "user_lulc_raster"
        else:
            if lulc_download_source == "nlcd":
                from core.nlcd import download_nlcd as _dl_nlcd
                lulc_path = project_dir / f"LULC_NLCD_{aoi_name}_{nlcd_year}.tif"
                # The DEM step now writes the DEM in the working metric
                # CRS (NAD83 / UTM zone N or WGS84 UTM), so ``src.res[0]``
                # is always real metres.  Just pass it through — with a
                # defensive range check so a corrupt / mis-projected DEM
                # surfaces as a clear error instead of a hung download.
                with rasterio.open(dem_tif_path) as src:
                    dem_cell = float(abs(src.res[0]))
                    dem_crs_is_geo = (
                        src.crs is not None
                        and getattr(src.crs, "is_geographic", False)
                    )
                if dem_crs_is_geo:
                    raise RuntimeError(
                        f"DEM at {dem_tif_path} is in a geographic CRS "
                        f"({src.crs}).  The DEM step is supposed to project "
                        "every DEM into a metric working CRS — re-run the "
                        "DEM step before Manning."
                    )
                if not (0.5 <= dem_cell <= 5000.0):
                    raise ValueError(
                        f"DEM cell size {dem_cell:.6f} m is outside the "
                        "supported range (0.5–5000 m).  Check the DEM "
                        "step's output before re-running Manning."
                    )
                _dl_nlcd(aoi_gdf, dem_cell, str(lulc_path),
                         year=nlcd_year, log_fn=log_fn)
                # Resample/snap to DEM grid (categorical → nearest)
                snap_tmp = lulc_path.with_suffix(".snap.tif")
                _reproject_to_snap(lulc_path, dem_tif_path, snap_tmp,
                                   categorical=True, dst_nodata=0)
                snap_tmp.replace(lulc_path)
                _apply_aoi_mask(lulc_path, aoi_gdf, 0)
                lulc_source = "download_nlcd"
            else:
                if lulc_year is None:
                    raise ValueError("LULC year required when downloading LULC.")
                lulc_path = project_dir / f"LULC_{aoi_name}_{lulc_year}.tif"
                _download_lulc_to_dem_grid(aoi_gdf, dem_tif_path, lulc_year, lulc_path, log_fn)
                lulc_source = "download_esri"

        # Display unique LULC classes
        with rasterio.open(lulc_path) as src:
            lulc_arr = src.read(1)
            lulc_nodata = src.nodata
        vals = np.unique(lulc_arr)
        if lulc_nodata is not None:
            vals = vals[vals != lulc_nodata]
        log_fn(f"Unique LULC classes: {vals.tolist()}")

        log_fn("Creating Manning raster from LULC...")
        _create_manning_from_lulc(lulc_path, manning_tif_path, mapping)

    _write_ascii_from_tif(manning_tif_path, manning_ascii_path)
    log_fn(f"Manning ASCII saved: {manning_ascii_path}")

    ctx["floodplain_friction_mode"] = "varying"
    ctx["fric_mode"] = "varying"
    ctx["par_use_fpfric"] = False
    ctx["par_fpfric"] = None
    ctx["par_use_manningfile"] = True
    # Record the ACTUAL filename written (may be lulc (1).ascii, etc.)
    # so the PAR step can reference the right file.
    ctx["par_manningfile_name"] = manning_ascii_path.name
    ctx["lulc_source"] = lulc_source
    ctx["lulc_path"] = str(lulc_path) if lulc_path else None
    ctx["lulc_year"] = lulc_year
    ctx["manning_tif_path"] = str(manning_tif_path)
    ctx["manning_ascii_path"] = str(manning_ascii_path)
    ctx["manning_mapping"] = {str(k): v for k, v in mapping.items()}
    save_context(ctx_path, ctx)
    return ctx
