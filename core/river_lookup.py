"""Detect the main river covering an AOI by querying NHD flowlines.

Reuses the same approach as core/bci.py and core/triton_bc.py:
  1. Download NHD medium-resolution flowlines covering the AOI bbox
  2. Clip to AOI
  3. Pick the highest stream-order river by GNIS name + total length

This is network-bound so it should be called from a Worker thread.
Results are cached per (source_file, feature_index) so re-clicks are instant.
"""
import ssl
from typing import Optional, Tuple

import geopandas as gpd
from shapely.ops import linemerge
from shapely.geometry import LineString, MultiLineString


# Process-local cache: (source_file, feature_index) -> river_name (str | None)
_CACHE = {}


def _safe_name(x):
    x = "" if x is None else str(x).strip()
    return x if x else "Unnamed"


def _to_single_linestring(geom):
    if geom is None or geom.is_empty:
        return None
    if isinstance(geom, LineString):
        return geom
    if isinstance(geom, MultiLineString):
        merged = linemerge(geom)
        if isinstance(merged, LineString):
            return merged
        parts = [g for g in merged.geoms if g is not None and not g.is_empty]
        return max(parts, key=lambda g: g.length) if parts else None
    return None


def _union_geom(gdf):
    try:
        g = gdf.geometry.union_all()
    except Exception:
        g = gdf.unary_union
    return g


def lookup_main_river(
    aoi_path: str, feature_index: int, log_fn=print
) -> Optional[str]:
    """Return the name of the main river covering an AOI feature.

    Network-bound — call from a worker thread.  Returns None if no NHD
    flowlines are found or the call fails.  Results are cached.
    """
    key = (str(aoi_path), int(feature_index))
    if key in _CACHE:
        return _CACHE[key]

    try:
        gdf = gpd.read_file(aoi_path)
        if gdf.crs is None:
            log_fn(f"AOI {aoi_path} has no CRS — skipping river lookup.")
            _CACHE[key] = None
            return None
        feature = gdf.iloc[[feature_index]].reset_index(drop=True)
        feature_4326 = feature.to_crs("EPSG:4326")

        ssl._create_default_https_context = ssl._create_unverified_context
        try:
            from pynhd import NHD
        except ImportError:
            log_fn("pynhd not installed — skipping river lookup.")
            _CACHE[key] = None
            return None

        log_fn(f"Looking up main river for {aoi_path} feature {feature_index}…")
        nhd = NHD("flowline_mr")
        union = _union_geom(feature_4326)
        try:
            flowlines = nhd.bygeom(union)
        except Exception as ex:
            msg = str(ex)
            # NHD's bygeom rejects MultiPolygon — fall back to the bbox.
            if "should be of type" in msg or "MultiPolygon" in msg:
                flowlines = nhd.bygeom(tuple(union.bounds))
            else:
                raise
        if flowlines is None or flowlines.empty:
            _CACHE[key] = None
            return None

        flowlines = flowlines.to_crs(feature.crs)
        clipped = gpd.overlay(flowlines, feature[["geometry"]], how="intersection")
        clipped = clipped[
            clipped.geometry.type.isin(["LineString", "MultiLineString"])
        ].copy()
        if clipped.empty:
            _CACHE[key] = None
            return None

        if "StreamOrde" not in clipped.columns:
            _CACHE[key] = None
            return None

        clipped["geom_len"] = clipped.geometry.length
        clipped["river_name"] = (
            clipped["GNIS_NAME"].apply(_safe_name)
            if "GNIS_NAME" in clipped.columns else "Unnamed"
        )

        max_order = clipped["StreamOrde"].max()
        top = clipped[clipped["StreamOrde"] == max_order]
        summary = (
            top.groupby("river_name", dropna=False)
               .agg(total_len=("geom_len", "sum"))
               .reset_index()
               .sort_values("total_len", ascending=False)
        )
        if summary.empty:
            _CACHE[key] = None
            return None
        river_name = str(summary.iloc[0]["river_name"])
        _CACHE[key] = river_name
        log_fn(f"  → main river: {river_name} (stream order {int(max_order)})")
        return river_name

    except Exception as ex:
        log_fn(f"River lookup failed for {aoi_path} feature {feature_index}: {ex}")
        _CACHE[key] = None
        return None
