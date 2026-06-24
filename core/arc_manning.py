"""ARC-Curve2Flood step — LULC download and Manning's n table generation.

Outputs for each AOI:
  * <AOI>/dem/lulc.tif     — LULC GeoTIFF clipped + reprojected to DEM grid
  * <AOI>/arc-files/mannings_n.txt — per-class Manning n lookup table

ARC reads the LULC raster together with mannings_n.txt to assign roughness.
No Manning raster is written — that is ARC's job internally.

Fixed mode still downloads the LULC raster so ARC can use it, but writes
a uniform n value for every class in mannings_n.txt.
"""
import json
import math
import os
import time
from pathlib import Path

import numpy as np
import rasterio
import requests
from rasterio.features import geometry_mask
from rasterio.merge import merge
from rasterio.warp import reproject, Resampling

from core.context import save_context


# ── default n mappings (same as TRITON for consistency) ──────────────────────

SENTINEL2_DEFAULT_N = {
    1: 0.035,   # Water
    2: 0.110,   # Trees
    3: 0.045,   # Grass
    4: 0.090,   # Flooded Vegetation
    5: 0.040,   # Crops
    6: 0.060,   # Scrub/Shrub
    7: 0.025,   # Built Area
    8: 0.040,   # Bare Ground
    9: 0.012,   # Snow/Ice
    "default": 0.045,
}

NLCD_DEFAULT_N = {
    11: 0.030,  # Open Water
    12: 0.012,  # Perennial Ice/Snow
    21: 0.035,  # Developed, Open Space
    22: 0.060,  # Developed, Low Intensity
    23: 0.075,  # Developed, Medium Intensity
    24: 0.085,  # Developed, High Intensity
    31: 0.035,  # Barren Land
    41: 0.100,  # Deciduous Forest
    42: 0.110,  # Evergreen Forest
    43: 0.100,  # Mixed Forest
    51: 0.060,  # Dwarf Scrub
    52: 0.060,  # Shrub/Scrub
    71: 0.040,  # Grassland/Herbaceous
    72: 0.040,  # Sedge/Herbaceous
    73: 0.040,  # Lichens
    74: 0.040,  # Moss
    81: 0.040,  # Pasture/Hay
    82: 0.040,  # Cultivated Crops
    90: 0.100,  # Woody Wetlands
    95: 0.080,  # Emergent Herbaceous Wetlands
    "default": 0.045,
}


# ── internal helpers ──────────────────────────────────────────────────────────

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


def _reproject_to_snap(src_path, snap_path, out_path, categorical=True,
                       dst_nodata=None):
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
        _atomic_write_gtiff(dst_arr, out_path, snap.crs, snap.transform, dtype, nodata)


def _apply_aoi_mask(tif_path, aoi_gdf, nodata_value):
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


def _download_esri_lulc(aoi_gdf, snap_path, lulc_year, out_lulc_path, log_fn):
    """Download ESRI Sentinel-2 10 m LULC tiles, mosaic, reproject to DEM grid."""
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
    log_fn(f"Downloading LULC year {lulc_year} from ESRI Sentinel-2 10m service...")

    mosaic_rule = {"mosaicMethod": "esriMosaicAttribute", "where": f"Year = {lulc_year}"}
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
            "bbox": f"{x0},{y0},{x1},{y1}", "bboxSR": 3857, "imageSR": 3857,
            "size": f"{w},{h}", "format": "tiff", "f": "image",
            "mosaicRule": json.dumps(mosaic_rule),
        }
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                r = requests.get(EXPORT_URL, params=params, stream=True, timeout=300)
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
                log_fn(f"  LULC progress: {done}/{total_tiles} | ready: {ok_count}")

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
    _atomic_write_gtiff(lulc_reproj, out_lulc_path, snap_crs, snap_transform,
                        "int16", 0)
    _safe_delete(tmp_mosaic)
    log_fn(f"LULC raster saved: {out_lulc_path}")


def _write_mannings_n_table(out_path: Path, mapping: dict, log_fn=print):
    """Write mannings_n.txt — ARC's per-class Manning n lookup table.

    Format: CSV with header, one row per LULC class.
      LULC_Code,Manning_n
      1,0.035
      ...
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["LULC_Code,Manning_n"]
    for code, n_val in sorted(
        ((k, v) for k, v in mapping.items() if k != "default"),
        key=lambda kv: int(kv[0]),
    ):
        lines.append(f"{int(code)},{float(n_val):.6f}")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log_fn(f"Manning n table written: {out_path}  ({len(lines)-1} classes)")


# ── public API ────────────────────────────────────────────────────────────────

def prepare_arc_manning(
    ctx_path,
    ctx: dict,
    fric_mode: str,                          # "fixed" | "varying"
    fpfric_val: float = None,
    lulc_source: str = "download",           # "download" | "download_nlcd" | "user_lulc"
    user_lulc_path: str = None,
    dem_res_m: float = 10.0,
    lulc_class_to_n: dict = None,
    lulc_year: int = None,
    nlcd_year: str = "2021",
    log_fn=print,
) -> dict:
    """Prepare ARC-Curve2Flood Manning inputs.

    Always writes mannings_n.txt to <AOI>/arc-files/.
    Varying mode also downloads the LULC raster to <AOI>/dem/lulc.tif.
    Fixed mode writes a uniform-n table; no LULC raster is required.

    Returns updated ctx.
    """
    if fric_mode not in ("fixed", "varying"):
        raise ValueError(f"fric_mode must be 'fixed' or 'varying', got '{fric_mode}'.")

    project_dir = Path(ctx["project_dir"])
    arc_dir     = Path(ctx.get("arc_dir") or (Path(ctx["project_dir"]) / "arc-files"))
    dem_dir     = Path(ctx.get("dem_dir")  or (Path(ctx["project_dir"]) / "dem"))
    aoi_name    = ctx["aoi_name"]
    arc_dir.mkdir(parents=True, exist_ok=True)
    dem_dir.mkdir(parents=True, exist_ok=True)

    mannings_txt_path = arc_dir / "mannings_n.txt"

    # ── fixed mode ────────────────────────────────────────────────────────────
    if fric_mode == "fixed":
        if fpfric_val is None or fpfric_val <= 0:
            raise ValueError("Fixed Manning n (fpfric_val) must be > 0.")
        # Build a table with a representative set of NLCD/Sentinel-2 codes all
        # set to the same uniform value (ARC may encounter any class).
        all_codes = sorted(
            set(list(SENTINEL2_DEFAULT_N.keys()) + list(NLCD_DEFAULT_N.keys()))
            - {"default"}
        )
        mapping = {code: fpfric_val for code in all_codes}
        mapping["default"] = fpfric_val
        _write_mannings_n_table(mannings_txt_path, mapping, log_fn)
        log_fn(f"Fixed Manning n = {fpfric_val}  (uniform for all classes)")
        ctx["arc_fric_mode"]       = "fixed"
        ctx["arc_fpfric"]          = fpfric_val
        ctx["arc_lulc_tif_path"]   = None
        ctx["arc_mannings_n_path"] = str(mannings_txt_path)
        ctx["lulc_source"]         = "fixed"
        save_context(ctx_path, ctx)
        return ctx

    # ── varying mode ─────────────────────────────────────────────────────────
    log_fn("Varying Manning mode selected.")
    if lulc_source not in ("download", "download_nlcd", "user_lulc"):
        raise ValueError(
            f"lulc_source must be 'download', 'download_nlcd', or 'user_lulc', "
            f"got '{lulc_source}'."
        )

    dem_tif_path = Path(ctx.get("dem_tif_path") or ctx["dem_path"])
    if not dem_tif_path.exists():
        raise FileNotFoundError(f"DEM not found: {dem_tif_path}")

    from core.aoi import read_aoi
    aoi_gdf = read_aoi(ctx)

    default_mapping = (
        NLCD_DEFAULT_N if lulc_source == "download_nlcd" else SENTINEL2_DEFAULT_N
    )
    mapping = lulc_class_to_n or default_mapping

    lulc_tif_path = dem_dir / "lulc.tif"

    if lulc_source == "user_lulc":
        if not user_lulc_path:
            raise ValueError(
                "user_lulc_path must be provided when lulc_source='user_lulc'."
            )
        src_path = Path(user_lulc_path)
        if not src_path.exists():
            raise FileNotFoundError(f"LULC raster not found: {src_path}")
        log_fn(f"Reprojecting user LULC to DEM grid...")
        _reproject_to_snap(src_path, dem_tif_path, lulc_tif_path,
                           categorical=True, dst_nodata=0)
        _apply_aoi_mask(lulc_tif_path, aoi_gdf, 0)
        lulc_used = "user_lulc_raster"

    elif lulc_source == "download_nlcd":
        from core.nlcd import download_nlcd as _dl_nlcd
        with rasterio.open(dem_tif_path) as src:
            _dem_cell = float(abs(src.res[0]))
            _dem_is_geo = (src.crs is not None
                           and getattr(src.crs, "is_geographic", False))
        if _dem_is_geo:
            raise RuntimeError(
                f"DEM at {dem_tif_path} is in a geographic CRS. "
                "Re-run the DEM step."
            )
        if not (0.5 <= _dem_cell <= 5000.0):
            raise ValueError(f"DEM cell size {_dem_cell:.6f} m is out of range.")
        _tmp = dem_dir / f"_nlcd_tmp_{aoi_name}.tif"
        _dl_nlcd(aoi_gdf, _dem_cell, str(_tmp), year=nlcd_year, log_fn=log_fn)
        _reproject_to_snap(_tmp, dem_tif_path, lulc_tif_path,
                           categorical=True, dst_nodata=0)
        _safe_delete(_tmp)
        _apply_aoi_mask(lulc_tif_path, aoi_gdf, 0)
        ctx["nlcd_year"] = str(nlcd_year)
        lulc_used = "download_nlcd"

    else:  # download ESRI Sentinel-2
        if lulc_year is None:
            lulc_year = ctx.get("lulc_year")
        if lulc_year is None:
            raise ValueError("lulc_year is required for ESRI Sentinel-2 download.")
        ctx["lulc_year"] = int(lulc_year)
        _download_esri_lulc(aoi_gdf, dem_tif_path, lulc_year, lulc_tif_path, log_fn)
        lulc_used = "download_esri"

    # Display unique classes
    with rasterio.open(lulc_tif_path) as src:
        lulc_arr = src.read(1)
        lulc_nodata = src.nodata
    vals = np.unique(lulc_arr)
    if lulc_nodata is not None:
        vals = vals[vals != lulc_nodata]
    log_fn(f"Unique LULC classes: {vals.tolist()}")

    # Write mannings_n.txt — only include classes that actually appear + default
    present_codes = {int(v) for v in vals if v != 0}
    table_mapping = {}
    for code, nval in mapping.items():
        if code == "default":
            continue
        table_mapping[int(code)] = float(nval)
    # Add any present codes that aren't in the table (use default n)
    default_n = float(mapping.get("default", 0.045))
    for code in present_codes:
        if code not in table_mapping:
            table_mapping[code] = default_n
    table_mapping["default"] = default_n
    _write_mannings_n_table(mannings_txt_path, table_mapping, log_fn)

    ctx["arc_fric_mode"]       = "varying"
    ctx["arc_fpfric"]          = None
    ctx["arc_lulc_tif_path"]   = str(lulc_tif_path)
    ctx["arc_mannings_n_path"] = str(mannings_txt_path)
    ctx["lulc_source"]         = lulc_used
    ctx["lulc_path"]           = str(lulc_tif_path)
    ctx["manning_mapping"]     = {str(k): v for k, v in table_mapping.items()}
    save_context(ctx_path, ctx)
    log_fn("ARC Manning step complete.")
    return ctx
