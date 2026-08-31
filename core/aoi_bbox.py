"""Rectangular (bounding-box) AOI support.

LISFLOOD-FP runs on a rectangular raster domain.  When the user uploads an
irregular AOI polygon, every downstream raster (DEM, Manning, …) is still
produced on the polygon's bounding box, but the polygon edges leave nodata
wedges and the boundary-condition edges are ambiguous.

This module detects whether an uploaded AOI is already an axis-aligned
rectangle and, when it is not, writes a new single-feature rectangle
shapefile spanning the polygon's min/max extent.  Downstream steps then use
that rectangle, while the original polygon is kept for display so the user can
see exactly what changed.

The rectangle is built in the shapefile's OWN CRS:
  * a geographic file (EPSG:4326) → min/max longitude & latitude;
  * a projected metric file (UTM …) → min/max easting & northing, which is a
    true rectangle on the model grid FIMsim will write the DEM onto.
"""
from pathlib import Path
from typing import Optional

import geopandas as gpd
from shapely.geometry import box


# A polygon counts as "already rectangular" when it fills at least this much
# of its own bounding box.  1.0 would be exact; 0.999 tolerates the tiny
# floating-point / vertex-densification noise real shapefiles carry.
_RECT_AREA_RATIO = 0.999


def is_axis_aligned_rectangle(geom, ratio: float = _RECT_AREA_RATIO) -> bool:
    """True when ``geom`` is (within tolerance) its own bounding box.

    Uses an area-ratio test rather than a corner count, so a rectangle whose
    edges carry extra collinear vertices still counts as rectangular, while a
    rotated rectangle correctly does not.
    """
    if geom is None or geom.is_empty:
        return False
    try:
        minx, miny, maxx, maxy = geom.bounds
        bbox_area = (maxx - minx) * (maxy - miny)
        if bbox_area <= 0:
            return False
        return (geom.area / bbox_area) >= ratio
    except Exception:
        return False


def make_bbox_aoi(
    src_path,
    feature_index: int,
    out_path,
    name: Optional[str] = None,
    log_fn=print,
) -> dict:
    """Write a rectangle shapefile spanning one AOI feature's extent.

    Returns ``{"path", "was_rectangular", "bounds", "crs", "area_km2"}``.
    When the source feature is already an axis-aligned rectangle, no file is
    written and ``path`` is None — the caller should keep using the original.
    """
    src_path = Path(src_path)
    gdf = gpd.read_file(src_path)
    if feature_index >= len(gdf):
        raise IndexError(
            f"Feature {feature_index} not in {src_path.name} "
            f"({len(gdf)} feature(s)).")
    geom = gdf.geometry.iloc[feature_index]

    already = is_axis_aligned_rectangle(geom)
    minx, miny, maxx, maxy = geom.bounds

    if already:
        log_fn(f"  AOI '{name or src_path.stem}' is already rectangular — "
               "using it as-is.")
        return {"path": None, "was_rectangular": True,
                "bounds": (minx, miny, maxx, maxy), "crs": str(gdf.crs),
                "area_km2": None}

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Remove stale sidecars so the shapefile driver never hits a schema clash.
    for sib in out_path.parent.glob(out_path.stem + ".*"):
        try:
            sib.unlink()
        except Exception:
            pass

    rect = gpd.GeoDataFrame(
        {"name": [name or src_path.stem]},
        geometry=[box(minx, miny, maxx, maxy)],
        crs=gdf.crs,
    )
    rect.to_file(out_path, driver="ESRI Shapefile")

    # Report the rectangle's area in km2 (metric CRS for an honest number).
    area_km2 = None
    try:
        r = rect.to_crs("EPSG:4326")
        c = r.geometry.iloc[0].centroid
        utm = 32600 + int((c.x + 180) / 6) + 1 + (0 if c.y >= 0 else 100)
        area_km2 = round(rect.to_crs(epsg=utm).geometry.iloc[0].area / 1e6, 4)
    except Exception:
        pass

    log_fn(f"  AOI '{name or src_path.stem}' is irregular → wrote rectangle "
           f"{out_path.name}" + (f" ({area_km2} km²)" if area_km2 else ""))
    return {"path": str(out_path), "was_rectangular": False,
            "bounds": (minx, miny, maxx, maxy), "crs": str(gdf.crs),
            "area_km2": area_km2}


def rectangularize_features(features, log_fn=print) -> list:
    """Give every feature a rectangular AOI, in place.

    For each ``AOIFeatureInfo`` whose polygon is not already a rectangle, write
    ``<folder>/<folder_name>_bbox.shp`` and repoint ``source_file`` /
    ``feature_index`` at it, remembering the original in
    ``original_source_file`` / ``original_feature_index`` so the map can show
    both.  Features that are already rectangular are left untouched.
    """
    for f in features:
        if getattr(f, "bbox_applied", False):
            continue          # already processed
        folder = f.folder_path
        if not folder:
            log_fn(f"  ⚠ {f.name}: no AOI folder yet — skipped rectangle step.")
            continue
        try:
            res = make_bbox_aoi(
                f.source_file, f.feature_index,
                Path(folder) / f"{f.folder_name or f.name}_bbox.shp",
                name=f.name, log_fn=log_fn,
            )
        except Exception as exc:
            log_fn(f"  ⚠ {f.name}: could not build rectangle ({exc}); "
                   "continuing with the original AOI.")
            continue

        # Remember the original either way, so the preview can draw it.
        f.original_source_file = f.source_file
        f.original_feature_index = f.feature_index
        f.was_rectangular = bool(res["was_rectangular"])

        if res["path"]:
            f.source_file = res["path"]
            f.feature_index = 0
            f.bbox_applied = True
            if res.get("area_km2"):
                f.area_km2 = res["area_km2"]
    return features
