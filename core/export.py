"""Multi-format raster and vector export utilities."""

import shutil
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import shapes
from rasterio.transform import from_bounds
from shapely.geometry import shape


def next_free_path(folder, stem: str, ext: str) -> Path:
    """Return a path in `folder` whose file does not exist yet.

    If `{stem}.{ext}` exists, tries `{stem} (1).{ext}`, `{stem} (2).{ext}`, …
    in the same macOS Finder / Downloads style.
    """
    folder = Path(folder)
    ext = ext.lstrip(".")
    candidate = folder / f"{stem}.{ext}"
    if not candidate.exists():
        return candidate
    i = 1
    while True:
        candidate = folder / f"{stem} ({i}).{ext}"
        if not candidate.exists():
            return candidate
        i += 1


# Keys that are GeoTIFF-specific and must be stripped when writing other formats
_GTIFF_ONLY_KEYS = {
    "compress", "blockxsize", "blockysize", "tiled",
    "BIGTIFF", "bigtiff", "interleave", "photometric",
}


def _clean_profile_for_driver(profile: dict, driver: str) -> dict:
    """Return a copy of *profile* with keys illegal for *driver* removed.

    GTiff-specific creation options (compress, blockxsize, …) cause GDAL to
    raise "SetGeoTransform" or "not correctly initialized" errors when passed
    verbatim to GPKG or AAIGrid writers.
    """
    if driver in ("GTiff",):
        return dict(profile)   # GTiff accepts all its own keys
    cleaned = {k: v for k, v in profile.items() if k not in _GTIFF_ONLY_KEYS}
    return cleaned


def _safe_remove(path: Path):
    """Delete *path* (and shapefile siblings) silently — never raises."""
    try:
        if path.is_file():
            path.unlink()
    except Exception:
        pass
    # Shapefile sidecars (.shx, .dbf, .prj, .cpg, .sbn, .sbx, .shp.xml …)
    if path.suffix.lower() == ".shp":
        for sib in path.parent.glob(path.stem + ".*"):
            try:
                sib.unlink()
            except Exception:
                pass


def export_raster(src_tif, out_path, out_format, cell_size_m=None, log_fn=print):
    """Export a raster to tif, gpkg, or asc format with optional resampling.

    Parameters
    ----------
    src_tif : str or Path
        Path to the source GeoTIFF.
    out_path : str or Path
        Destination file path.
    out_format : str
        One of "tif", "gpkg", "asc".
    cell_size_m : float, optional
        Target cell size in metres. If provided and differs from source,
        the raster is resampled (bilinear for float/continuous data,
        nearest for int/categorical data).
    log_fn : callable
        Logging function.

    Returns
    -------
    Path
        The output file path.
    """
    src_tif = Path(src_tif)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    driver_map = {
        "tif": "GTiff",
        "gpkg": "GPKG",
        "asc": "AAIGrid",
    }
    if out_format not in driver_map:
        raise ValueError(f"Unsupported format '{out_format}'. Use one of: {list(driver_map.keys())}")

    driver = driver_map[out_format]

    with rasterio.open(src_tif) as src:
        profile = src.profile.copy()
        transform = src.transform
        src_cell_x = abs(transform.a)
        src_cell_y = abs(transform.e)

        need_resample = (
            cell_size_m is not None
            and not (np.isclose(cell_size_m, src_cell_x) and np.isclose(cell_size_m, src_cell_y))
        )

        if need_resample:
            # Determine resampling method based on dtype
            is_categorical = np.issubdtype(src.dtypes[0], np.integer)
            resample_method = Resampling.nearest if is_categorical else Resampling.bilinear

            # Compute new dimensions
            new_width = max(1, int(round((src.bounds.right - src.bounds.left) / cell_size_m)))
            new_height = max(1, int(round((src.bounds.top - src.bounds.bottom) / cell_size_m)))
            new_transform = from_bounds(
                src.bounds.left, src.bounds.bottom,
                src.bounds.right, src.bounds.top,
                new_width, new_height,
            )

            data = src.read(
                out_shape=(src.count, new_height, new_width),
                resampling=resample_method,
            )

            profile.update(
                width=new_width,
                height=new_height,
                transform=new_transform,
            )
            log_fn(f"Resampled {src_tif.name} to {cell_size_m}m ({resample_method.name}) -> {out_path.name}")
        else:
            data = src.read()
            if not need_resample and out_format == "tif" and cell_size_m is None:
                log_fn(f"Copying {src_tif.name} -> {out_path.name}")
            else:
                log_fn(f"Exporting {src_tif.name} -> {out_path.name} ({out_format})")

        # AAIGrid only supports single-band
        if out_format == "asc":
            if data.shape[0] > 1:
                data = data[0:1]
                profile.update(count=1)

    # Strip GTiff-only creation options for non-GTiff drivers, then set driver.
    profile = _clean_profile_for_driver(profile, driver)
    profile["driver"] = driver

    # GPKG and AAIGrid cannot overwrite an existing file — remove it first so
    # that GDAL doesn't raise "file already exists / SetGeoTransform" errors.
    # (Callers are responsible for passing a versioned next_free_path if they
    # want to keep previous runs; this only prevents a hard crash on re-runs.)
    if out_format in ("gpkg", "asc") and out_path.exists():
        _safe_remove(out_path)

    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(data)

    return out_path


def raster_to_shapefile(raster_tif, out_shp, field_name="value", log_fn=print):
    """Polygonize a raster into a shapefile.

    Each unique pixel value becomes a polygon feature with the value stored
    in the specified field name column.

    Parameters
    ----------
    raster_tif : str or Path
        Path to input GeoTIFF.
    out_shp : str or Path
        Output shapefile path.
    field_name : str
        Column name for the pixel values.
    log_fn : callable
        Logging function.

    Returns
    -------
    Path
        The output shapefile path.
    """
    raster_tif = Path(raster_tif)
    out_shp = Path(out_shp)
    out_shp.parent.mkdir(parents=True, exist_ok=True)

    with rasterio.open(raster_tif) as src:
        band = src.read(1)
        mask = band != src.nodata if src.nodata is not None else None
        crs = src.crs
        transform = src.transform

    results = list(shapes(band, mask=mask, transform=transform))

    records = []
    for geom, value in results:
        records.append({"geometry": shape(geom), field_name: value})

    gdf = gpd.GeoDataFrame(records, crs=crs)
    # Remove stale shapefile sidecars so fiona doesn't hit a schema mismatch
    _safe_remove(out_shp)
    gdf.to_file(out_shp, driver="ESRI Shapefile")
    log_fn(f"Polygonized {raster_tif.name} -> {out_shp.name} ({len(gdf)} features)")

    return out_shp


def gdf_to_shapefile(gdf, out_shp, log_fn=print):
    """Write a GeoDataFrame to a shapefile.

    Parameters
    ----------
    gdf : geopandas.GeoDataFrame
        The GeoDataFrame to export.
    out_shp : str or Path
        Output shapefile path.
    log_fn : callable
        Logging function.

    Returns
    -------
    Path
        The output shapefile path.
    """
    out_shp = Path(out_shp)
    out_shp.parent.mkdir(parents=True, exist_ok=True)

    _safe_remove(out_shp)
    gdf.to_file(out_shp, driver="ESRI Shapefile")
    log_fn(f"Exported GeoDataFrame -> {out_shp.name} ({len(gdf)} features)")

    return out_shp
