"""Step 2 — Load AOI shapefile and update workflow context."""
from pathlib import Path
import geopandas as gpd
from core.context import save_context
from core.crs_utils import pick_working_crs_epsg, working_crs_label


def read_aoi(ctx):
    """Read the AOI shapefile and honour the feature_index in ctx.

    All downstream steps should use this instead of gpd.read_file(aoi_path)
    directly, so a multi-feature shapefile is always filtered to the single
    feature the user selected in Step 2.

    Returns a GeoDataFrame with exactly 1 row (or all rows if no filter set).
    """
    aoi_gdf = gpd.read_file(ctx["aoi_path"])
    fi = ctx.get("aoi_feature_index")
    if fi is not None and len(aoi_gdf) > 1:
        aoi_gdf = aoi_gdf.iloc[[fi]].reset_index(drop=True)
    return aoi_gdf


def get_working_crs_epsg(ctx, aoi_gdf=None, log_fn=print) -> int:
    """Return the working CRS EPSG for this ctx, computing it if missing.

    The working CRS is the metric, projected CRS every DEM / LULC /
    Manning raster lands in.  Step 2 (load_aoi) stores it in ctx; this
    helper is a safety net for callers that arrive at later steps via
    an older ctx file or a multi-AOI per-feature ctx where the value
    may not yet be set.  Result is cached back into ``ctx``.
    """
    epsg = ctx.get("working_crs_epsg")
    if epsg is not None:
        try:
            return int(epsg)
        except (TypeError, ValueError):
            pass
    if aoi_gdf is None:
        aoi_gdf = read_aoi(ctx)
    epsg = int(pick_working_crs_epsg(aoi_gdf, log_fn=log_fn))
    ctx["working_crs_epsg"] = epsg
    return epsg


def read_aoi_in_working_crs(ctx, log_fn=print):
    """Return (aoi_gdf_in_working_crs, working_crs_epsg).

    Convenience wrapper so consumers can ask for the AOI already
    reprojected into the working CRS without repeating the boilerplate
    in every step.
    """
    aoi_gdf = read_aoi(ctx)
    epsg = get_working_crs_epsg(ctx, aoi_gdf=aoi_gdf, log_fn=log_fn)
    return aoi_gdf.to_crs(epsg=epsg), epsg


def inspect_aoi(aoi_path: str):
    """Read the AOI shapefile and return (GeoDataFrame, feature_summaries).

    feature_summaries is a list of dicts, one per feature, with:
        index, geom_type, area_km2, and any text attribute columns.
    Useful for letting the user pick a feature when count > 1.
    """
    aoi_path = Path(aoi_path)
    if not aoi_path.exists():
        raise FileNotFoundError(f"AOI shapefile not found: {aoi_path}")

    aoi_gdf = gpd.read_file(aoi_path)
    if aoi_gdf.empty:
        raise ValueError("AOI shapefile contains no features.")
    if aoi_gdf.crs is None:
        raise ValueError("AOI shapefile has no CRS defined. Please assign a CRS in a GIS tool.")

    # Compute areas in km²
    if aoi_gdf.crs.is_geographic:
        proj = aoi_gdf.to_crs(aoi_gdf.estimate_utm_crs())
    else:
        proj = aoi_gdf
    areas_km2 = proj.area / 1e6

    # Build per-feature summaries
    text_cols = [c for c in aoi_gdf.columns
                 if c != "geometry" and aoi_gdf[c].dtype == object]
    summaries = []
    for i, row in aoi_gdf.iterrows():
        info = {
            "index": i,
            "geom_type": row.geometry.geom_type if row.geometry else "None",
            "area_km2": float(areas_km2.iloc[summaries.__len__()]),
        }
        for c in text_cols:
            info[c] = str(row[c]) if row[c] is not None else ""
        summaries.append(info)

    return aoi_gdf, summaries


def load_aoi(ctx_path, ctx: dict, aoi_path: str,
             feature_index: int = None, log_fn=print):
    """Read the AOI shapefile and update context.

    If feature_index is not None, only that single feature is kept — the
    shapefile is re-saved to a temporary single-feature file so downstream
    steps always see exactly one polygon.

    Returns updated ctx dict.
    """
    aoi_path = Path(aoi_path)
    if not aoi_path.exists():
        raise FileNotFoundError(f"AOI shapefile not found: {aoi_path}")

    aoi_gdf = gpd.read_file(aoi_path)

    if aoi_gdf.empty:
        raise ValueError("AOI shapefile contains no features.")
    if aoi_gdf.crs is None:
        raise ValueError("AOI shapefile has no CRS defined. Please assign a CRS in a GIS tool.")

    # If the user selected a specific feature, filter to just that one
    if feature_index is not None and len(aoi_gdf) > 1:
        aoi_gdf = aoi_gdf.iloc[[feature_index]].reset_index(drop=True)
        log_fn(f"Feature {feature_index} selected from {aoi_path.name} ({len(aoi_gdf)} kept).")

    ctx["aoi_path"] = str(aoi_path)
    ctx["aoi_name"] = aoi_path.stem
    ctx["aoi_feature_index"] = feature_index

    # Pick the working CRS now so every downstream step (DEM, LULC,
    # Manning, …) writes its rasters in a consistent metric projection,
    # no matter what CRS the user's AOI happens to be in.
    try:
        working_epsg = int(pick_working_crs_epsg(aoi_gdf, log_fn=log_fn))
        ctx["working_crs_epsg"] = working_epsg
        ctx["working_crs_label"] = working_crs_label(working_epsg)
    except Exception as ex:
        # Don't let a CRS-pick failure block AOI loading — log it and let
        # downstream steps surface a clearer error when they need the CRS.
        log_fn(f"WARNING: could not pre-compute working CRS for this AOI: {ex}")

    save_context(ctx_path, ctx)

    log_fn(f"AOI loaded:   {aoi_path.name}")
    log_fn(f"CRS:          {aoi_gdf.crs}")
    log_fn(f"Bounds:       {aoi_gdf.total_bounds}")
    log_fn(f"Features:     {len(aoi_gdf)}")
    if ctx.get("working_crs_label"):
        log_fn(f"Working CRS:  {ctx['working_crs_label']}")
    return ctx
