"""
NLCD download and Manning's n lookup tables for NLCD and Sentinel-2 LULC sources.
"""

import numpy as np
import geopandas as gpd
import rasterio
from rasterio.transform import from_bounds
from rasterio.enums import Resampling
from rasterio.mask import mask as rio_mask
from pathlib import Path

# ---------------------------------------------------------------------------
# Manning's n lookup tables
# Each maps int_code -> (class_name, min_n, max_n, default_n)
# ---------------------------------------------------------------------------

NLCD_MANNING = {
    11: ("Open Water",                   0.025, 0.035, 0.030),
    12: ("Perennial Ice/Snow",           0.030, 0.050, 0.040),
    21: ("Developed, Open Space",        0.035, 0.065, 0.050),
    22: ("Developed, Low Intensity",     0.050, 0.110, 0.080),
    23: ("Developed, Medium Intensity",  0.070, 0.130, 0.100),
    24: ("Developed, High Intensity",    0.090, 0.150, 0.120),
    31: ("Barren Land",                  0.025, 0.045, 0.035),
    41: ("Deciduous Forest",             0.080, 0.120, 0.100),
    42: ("Evergreen Forest",             0.090, 0.130, 0.110),
    43: ("Mixed Forest",                 0.085, 0.125, 0.105),
    52: ("Shrub/Scrub",                  0.050, 0.090, 0.070),
    71: ("Grassland/Herbaceous",         0.030, 0.060, 0.045),
    81: ("Pasture/Hay",                  0.030, 0.060, 0.045),
    82: ("Cultivated Crops",             0.025, 0.055, 0.040),
    90: ("Woody Wetlands",               0.090, 0.150, 0.120),
    95: ("Emergent Herbaceous Wetlands", 0.060, 0.100, 0.080),
}

SENTINEL2_MANNING = {
    1:  ("Water",              0.025, 0.035, 0.030),
    2:  ("Trees",              0.080, 0.140, 0.110),
    3:  ("Grass",              0.030, 0.060, 0.045),
    4:  ("Flooded Vegetation", 0.060, 0.120, 0.090),
    5:  ("Crops",              0.025, 0.055, 0.040),
    6:  ("Scrub/Shrub",        0.050, 0.090, 0.070),
    7:  ("Built Area",         0.080, 0.140, 0.110),
    8:  ("Bare Ground",        0.025, 0.045, 0.035),
    9:  ("Snow/Ice",           0.020, 0.050, 0.035),
    10: ("Clouds",             None,  None,  None),
    # ESRI 10 m LULC v3 (2017–present) consolidated short grass + shrub
    # into a single "Rangeland" class with code 11.
    11: ("Rangeland",          0.030, 0.060, 0.045),
}

# ---------------------------------------------------------------------------
# Layer name mapping for NLCD WMS
# ---------------------------------------------------------------------------

_NLCD_LAYERS = {
    "2021": "NLCD_2021_Land_Cover_L48",
    "2019": "NLCD_2019_Land_Cover_L48",
    "2016": "NLCD_2016_Land_Cover_L48",
    "2013": "NLCD_2013_Land_Cover_L48",
    "2011": "NLCD_2011_Land_Cover_L48",
    "2008": "NLCD_2008_Land_Cover_L48",
    "2006": "NLCD_2006_Land_Cover_L48",
    "2004": "NLCD_2004_Land_Cover_L48",
    "2001": "NLCD_2001_Land_Cover_L48",
}


def download_nlcd(aoi_gdf, cell_size_m, out_path, year="2021", log_fn=print):
    """
    Download NLCD land cover data for an area of interest via WMS.

    Parameters
    ----------
    aoi_gdf : geopandas.GeoDataFrame
        Area of interest geometry (any CRS; will be reprojected to EPSG:4326).
    cell_size_m : float
        Desired output cell size in metres.
    out_path : str or Path
        Output GeoTIFF path.
    year : str
        NLCD year — one of 2001, 2004, 2006, 2008, 2011, 2013, 2016, 2019, 2021.
    log_fn : callable
        Logging function (default: print).

    Returns
    -------
    Path
        Path to the output GeoTIFF.
    """
    from pygeoogc import WMS

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Validate year
    if year not in _NLCD_LAYERS:
        raise ValueError(f"Unsupported NLCD year '{year}'. Choose from {list(_NLCD_LAYERS.keys())}")

    # Defensive guard: reject impossible cell sizes early instead of
    # spending minutes building a multi-gigapixel array.  This catches
    # callers that forgot to convert geographic-CRS degrees → metres.
    try:
        cell_size_m = float(cell_size_m)
    except (TypeError, ValueError) as e:
        raise ValueError(f"cell_size_m must be numeric, got {cell_size_m!r}") from e
    if not (0.5 <= cell_size_m <= 5000.0):
        raise ValueError(
            f"NLCD cell_size_m={cell_size_m} is outside the supported "
            "range (0.5–5000 m).  This usually means a geographic-CRS "
            "DEM's degree-based resolution was passed through without "
            "converting to metres — see core/manning.py for the "
            "expected conversion."
        )

    layer = _NLCD_LAYERS[year]
    wms_url = "https://www.mrlc.gov/geoserver/mrlc_download/wms"

    # Reproject AOI to EPSG:4326 for WMS request
    aoi_4326 = aoi_gdf.to_crs(epsg=4326)
    bounds = aoi_4326.total_bounds  # (minx, miny, maxx, maxy)
    bbox = tuple(bounds)

    log_fn(
        f"Downloading NLCD {year} for bbox "
        f"({bounds[0]:.4f}, {bounds[1]:.4f}, {bounds[2]:.4f}, {bounds[3]:.4f}) "
        f"at {cell_size_m:.1f} m/pixel ..."
    )
    log_fn(
        "  (The MRLC WMS server can take a minute or two for the first "
        "response — the next log line appears once the bytes have been "
        "received.)"
    )

    try:
        wms = WMS(wms_url, layers=layer, outformat="image/geotiff", crs="epsg:4326")
        result = wms.getmap_bybox(bbox, resolution=cell_size_m)
    except Exception as e:
        raise RuntimeError(f"Failed to download NLCD from WMS: {e}") from e

    # Newer pygeoogc returns ``{layer_name: bytes}``; older versions
    # returned raw bytes directly.  Handle both.
    #
    # Some NLCD years / fine resolutions cause the MRLC WMS to split the
    # response into download-domain tiles keyed as
    #   NLCD_2001_Land_Cover_L48_dd_0, _dd_1, …, _dd_N
    # We detect that pattern, write each tile to a temp file, mosaic them
    # with rasterio.merge, and continue as if we had one response.
    if isinstance(result, dict):
        if layer in result:
            raw_bytes = result[layer]
            tile_bytes_list = None
        elif len(result) == 1:
            raw_bytes = next(iter(result.values()))
            tile_bytes_list = None
        else:
            # Check for download-domain tiles (_dd_N suffix)
            dd_prefix = layer + "_dd_"
            dd_tiles = {k: v for k, v in result.items()
                        if k.startswith(dd_prefix) or k == layer}
            if dd_tiles:
                tile_bytes_list = list(dd_tiles.values())
                raw_bytes = None   # will be assembled below
                log_fn(
                    f"  NLCD WMS returned {len(tile_bytes_list)} download-domain "
                    f"tile(s) — mosaicking …"
                )
            else:
                raise RuntimeError(
                    f"NLCD WMS returned an unexpected dict with keys "
                    f"{list(result.keys())!r} — could not find layer {layer!r}."
                )
    else:
        raw_bytes = result
        tile_bytes_list = None

    # ── Mosaic download-domain tiles into one GeoTIFF ─────────────────────────
    tmp_path = out_path.with_suffix(".tmp.tif")
    if tile_bytes_list is not None:
        import tempfile, os as _os
        tile_paths = []
        for idx, tb in enumerate(tile_bytes_list):
            if not isinstance(tb, (bytes, bytearray, memoryview)):
                continue   # skip any non-bytes entries
            tp = out_path.parent / f"_nlcd_tile_{idx}.tif"
            with open(tp, "wb") as fh:
                fh.write(tb)
            tile_paths.append(tp)
        if not tile_paths:
            raise RuntimeError(
                "NLCD WMS returned download-domain tiles but none contained "
                "valid bytes.  Check the bounding box / year."
            )
        from rasterio.merge import merge as _merge
        srcs = [rasterio.open(str(tp)) for tp in tile_paths]
        mosaic_arr, mosaic_transform = _merge(srcs, method="first")
        mosaic_meta = srcs[0].meta.copy()
        for s in srcs:
            s.close()
        mosaic_meta.update(
            driver="GTiff",
            height=mosaic_arr.shape[1],
            width=mosaic_arr.shape[2],
            transform=mosaic_transform,
        )
        with rasterio.open(tmp_path, "w", **mosaic_meta) as dst:
            dst.write(mosaic_arr)
        for tp in tile_paths:
            try:
                tp.unlink()
            except Exception:
                pass
    else:
        if not isinstance(raw_bytes, (bytes, bytearray, memoryview)):
            raise RuntimeError(
                f"NLCD WMS returned {type(raw_bytes).__name__} instead of bytes "
                f"(value: {raw_bytes!r}).  This usually means the WMS service "
                "returned a JSON error response — check the bounding box / year."
            )
        # Write raw WMS response to a temporary file first
        with open(tmp_path, "wb") as f:
            f.write(raw_bytes)

    log_fn("WMS bytes received. Processing raster ...")

    # Resample if cell size differs from 30m native resolution
    with rasterio.open(tmp_path) as src:
        src_res = src.res[0]  # assume square pixels
        profile = src.profile.copy()

        if abs(cell_size_m - 30.0) > 0.5:
            # Convert metric cell size → degrees per pixel using a proper
            # cos(latitude) factor on the longitude axis.  The previous
            # equator-only formula (cell_size_m / 111320) accidentally
            # halved the pixel count at higher latitudes, but more
            # importantly: this is the line that previously hung when a
            # caller passed a sub-metre value (think 0.00027) — now the
            # range guard above stops that long before we get here.
            center_lat = (bounds[1] + bounds[3]) / 2.0
            import math as _math
            cos_lat = max(abs(_math.cos(_math.radians(center_lat))), 0.05)
            m_per_deg_lat = 111_320.0
            m_per_deg_lon = 111_320.0 * cos_lat
            width  = int((bounds[2] - bounds[0]) * m_per_deg_lon / cell_size_m)
            height = int((bounds[3] - bounds[1]) * m_per_deg_lat / cell_size_m)
            width = max(width, 1)
            height = max(height, 1)
            # Defense-in-depth: refuse to allocate a > 50 000 × 50 000
            # raster — that's already > 10 GB at int16 and almost
            # certainly indicates a unit / CRS bug upstream.
            if width > 50_000 or height > 50_000:
                raise ValueError(
                    f"NLCD resampled grid would be {width}×{height} pixels — "
                    "refusing to allocate.  Cell size = "
                    f"{cell_size_m} m, bounds span = "
                    f"{bounds[2]-bounds[0]:.4f}° × {bounds[3]-bounds[1]:.4f}°. "
                    "Check the DEM CRS / resolution upstream."
                )
            log_fn(
                f"Resampling NLCD to {width}×{height} pixels "
                f"(~{cell_size_m:.1f} m/pixel) ..."
            )
            data = src.read(
                out_shape=(src.count, height, width),
                resampling=Resampling.nearest,
            )
            transform = from_bounds(*bounds, width, height)
            profile.update(
                width=width,
                height=height,
                transform=transform,
            )
        else:
            log_fn("Cell size matches NLCD native 30 m — no resample needed.")
            data = src.read()
            transform = src.transform

    # Write resampled data
    profile.update(driver="GTiff", dtype=data.dtype)
    with rasterio.open(tmp_path, "w", **profile) as dst:
        dst.write(data)

    # Clip to AOI geometry
    log_fn("Clipping NLCD raster to AOI polygon ...")
    aoi_geom = aoi_4326.geometry.values
    with rasterio.open(tmp_path) as src:
        clipped, clipped_transform = rio_mask(src, aoi_geom, crop=True, nodata=0)
        clip_profile = src.profile.copy()
        clip_profile.update(
            height=clipped.shape[1],
            width=clipped.shape[2],
            transform=clipped_transform,
            nodata=0,
        )

    # Remove stale file so GDAL doesn't raise "file already exists"
    if out_path.exists():
        try:
            out_path.unlink()
        except Exception:
            pass
    with rasterio.open(out_path, "w", **clip_profile) as dst:
        dst.write(clipped)

    # Clean up temp file
    try:
        tmp_path.unlink()
    except OSError:
        pass

    log_fn(f"NLCD raster saved to {out_path}")
    return out_path


def create_manning_from_lulc(lulc_tif, manning_out_path, mapping, log_fn=print):
    """
    Create a Manning's n raster from a land-use/land-cover classification raster.

    Parameters
    ----------
    lulc_tif : str or Path
        Path to the input LULC GeoTIFF (integer class codes).
    manning_out_path : str or Path
        Output path for the Manning's n GeoTIFF (float32).
    mapping : dict
        Manning lookup table (e.g. NLCD_MANNING or SENTINEL2_MANNING).
        Maps int_code -> (class_name, min_n, max_n, default_n).
    log_fn : callable
        Logging function (default: print).

    Returns
    -------
    Path
        Path to the output Manning's n GeoTIFF.
    """
    lulc_tif = Path(lulc_tif)
    manning_out_path = Path(manning_out_path)
    manning_out_path.parent.mkdir(parents=True, exist_ok=True)

    default_n = 0.045
    MANNING_NODATA = -9999.0   # sentinel for outside-AOI / background cells

    log_fn(f"Creating Manning's n raster from {lulc_tif.name} ...")

    with rasterio.open(lulc_tif) as src:
        lulc_data = src.read(1)
        lulc_nodata = src.nodata   # typically 0 for NLCD/Sentinel-2 downloads
        profile = src.profile.copy()

    # ManningTableWidget.get_mapping() adds a "default" key for unmapped
    # pixels — use it as the fill value if present, then skip it in the loop.
    if "default" in mapping:
        try:
            v = mapping["default"]
            default_n = float(v) if isinstance(v, (int, float)) else default_n
        except Exception:
            pass

    # Build Manning's n array — start with the default, then stamp each class
    manning_arr = np.full(lulc_data.shape, default_n, dtype=np.float32)

    for code, values in mapping.items():
        # Skip non-integer keys (e.g. the "default" fallback key)
        try:
            int_code = int(code)
        except (ValueError, TypeError):
            continue
        # Accept both {code: float} (from ManningTableWidget.get_mapping()) and
        # {code: (name, min, max, default)} (from NLCD_MANNING / SENTINEL2_MANNING).
        if isinstance(values, (int, float)):
            n_default = float(values)
        else:
            _, _, _, n_default = values
        if n_default is None:
            # For classes like "Clouds" with no valid n, use the fallback
            continue
        manning_arr[lulc_data == int_code] = n_default

    # Propagate LULC nodata → Manning nodata so background pixels stay masked
    # when the raster is displayed. This keeps the AOI boundary clearly visible.
    if lulc_nodata is not None:
        try:
            manning_arr[lulc_data == int(lulc_nodata)] = MANNING_NODATA
        except Exception:
            manning_arr[lulc_data == 0] = MANNING_NODATA
    else:
        # NLCD / Sentinel-2 downloads use 0 as the background sentinel
        manning_arr[lulc_data == 0] = MANNING_NODATA

    # Write output — remove stale file first so GDAL never raises "file exists"
    if manning_out_path.exists():
        try:
            manning_out_path.unlink()
        except Exception:
            pass
    profile.update(dtype="float32", count=1, nodata=MANNING_NODATA, driver="GTiff")
    with rasterio.open(manning_out_path, "w", **profile) as dst:
        dst.write(manning_arr, 1)

    log_fn(f"Manning's n raster saved to {manning_out_path}")
    return manning_out_path
