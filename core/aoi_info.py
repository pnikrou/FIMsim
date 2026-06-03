"""HUC8 and USGS gage lookups for an AOI feature.

Both helpers prefer a bundled GeoJSON when available, then fall back to
``pynhd.WaterData``.  Results are cached per (source_file, feature_index)
so repeated lookups are instant.

These calls may be network-bound — invoke from a Worker thread.
"""
import ssl
from pathlib import Path
from typing import List, Optional, Tuple

import geopandas as gpd

from core.multi_aoi import get_single_feature_gdf


# Process-local caches
_HUC6_CACHE: dict = {}     # (path, idx) -> list[str]
_HUC8_CACHE: dict = {}     # (path, idx) -> list[str]
_GAGES_CACHE: dict = {}    # (path, idx) -> list[dict]
_RIVER_GDF_CACHE: dict = {}  # (path, idx) -> GeoDataFrame (NHD flowlines clipped to AOI)

# Bundled HUC8 polygons (drop a us_huc8.geojson into data/ to enable).
_HUC8_DATA_PATH = Path(__file__).parent.parent / "data" / "us_huc8.geojson"
_HUC8_GDF: Optional[gpd.GeoDataFrame] = None


def _load_huc8_boundaries() -> Optional[gpd.GeoDataFrame]:
    """Load bundled HUC8 polygons; return None if the file isn't present."""
    global _HUC8_GDF
    if _HUC8_GDF is not None:
        return _HUC8_GDF
    if not _HUC8_DATA_PATH.exists():
        return None
    _HUC8_GDF = gpd.read_file(_HUC8_DATA_PATH)
    return _HUC8_GDF


def _bygeom_safe(layer: str, geom):
    """Call ``pynhd.WaterData(layer).bygeom(geom)`` with a fallback that
    converts MultiPolygon → bbox tuple, since pynhd's bygeom rejects
    MultiPolygons in some versions."""
    from pynhd import WaterData
    try:
        return WaterData(layer).bygeom(geom)
    except Exception as ex:
        msg = str(ex)
        # Retry with the bounding box if the geom type is unsupported.
        if "should be of type" in msg or "MultiPolygon" in msg:
            try:
                bbox = tuple(geom.bounds)   # (minx, miny, maxx, maxy)
                return WaterData(layer).bygeom(bbox)
            except Exception:
                raise
        raise


def _aoi_geom_4326_for(aoi_path: str, feature_index: int):
    """Return (single-feature-AOI in EPSG:4326, geometry-union)."""
    feature = get_single_feature_gdf(aoi_path, feature_index).to_crs("EPSG:4326")
    geom = (
        feature.geometry.union_all()
        if hasattr(feature.geometry, "union_all")
        else feature.unary_union
    )
    return feature, geom


# ── HUC6 ──────────────────────────────────────────────────────────────────────

def lookup_huc6(aoi_path: str, feature_index: int, log_fn=print) -> List[str]:
    """Return a sorted list of 6-digit HUC codes covering the AOI feature.

    Uses the bundled HUC6 boundaries shipped at data/us_huc6.geojson when
    available (fast, offline) and falls back to ``pynhd.WaterData('wbd06')``.
    """
    key = (str(aoi_path), int(feature_index))
    if key in _HUC6_CACHE:
        return _HUC6_CACHE[key]

    # First, try the bundled HUC6 GeoJSON (offline, instant)
    try:
        from core.hand import _load_huc6_boundaries
        huc_gdf = _load_huc6_boundaries()
        feature, _ = _aoi_geom_4326_for(aoi_path, feature_index)
        feature_in_huc_crs = feature.to_crs(huc_gdf.crs)
        import geopandas as gpd
        hits = gpd.sjoin(
            huc_gdf, feature_in_huc_crs[["geometry"]],
            how="inner", predicate="intersects",
        )
        if not hits.empty:
            codes = sorted({str(c).zfill(6) for c in hits["huc6"].tolist()})
            _HUC6_CACHE[key] = codes
            log_fn(f"  HUC6: {', '.join(codes)} (bundled)")
            return codes
    except Exception as ex:
        log_fn(f"HUC6 bundled lookup failed ({ex}) — falling back to network.")

    # Fallback: pynhd
    try:
        ssl._create_default_https_context = ssl._create_unverified_context
        import pynhd  # noqa
        _, geom = _aoi_geom_4326_for(aoi_path, feature_index)
        gdf = _bygeom_safe("wbd06", geom)
        if gdf is None or gdf.empty:
            _HUC6_CACHE[key] = []
            return []
        col = "huc6" if "huc6" in gdf.columns else next(
            (c for c in gdf.columns if c.lower() == "huc6"), None
        )
        if not col:
            _HUC6_CACHE[key] = []
            return []
        codes = sorted({str(c).zfill(6) for c in gdf[col].tolist()})
        _HUC6_CACHE[key] = codes
        log_fn(f"  HUC6: {', '.join(codes)}")
        return codes
    except Exception as ex:
        log_fn(f"HUC6 lookup failed: {ex}")
        _HUC6_CACHE[key] = []
        return []


# ── HUC8 ──────────────────────────────────────────────────────────────────────

def lookup_huc8(aoi_path: str, feature_index: int, log_fn=print) -> List[str]:
    """Return a sorted list of 8-digit HUC codes covering the AOI feature.

    Tries the bundled ``data/us_huc8.geojson`` first (fast, offline) and
    falls back to ``pynhd.WaterData('wbd08')``.  Returns an empty list if
    no HUC8 intersects or on any error.
    """
    key = (str(aoi_path), int(feature_index))
    if key in _HUC8_CACHE:
        return _HUC8_CACHE[key]

    # 1) Bundled GeoJSON (offline, instant).  Drop a us_huc8.geojson into
    #    data/ to enable; the column should be named "huc8" (case-insensitive).
    try:
        huc_gdf = _load_huc8_boundaries()
        if huc_gdf is not None:
            feature, _ = _aoi_geom_4326_for(aoi_path, feature_index)
            feature_in_huc_crs = feature.to_crs(huc_gdf.crs)
            hits = gpd.sjoin(
                huc_gdf, feature_in_huc_crs[["geometry"]],
                how="inner", predicate="intersects",
            )
            if not hits.empty:
                col = "huc8" if "huc8" in hits.columns else next(
                    (c for c in hits.columns if c.lower() == "huc8"), None
                )
                if col:
                    codes = sorted({str(c).zfill(8) for c in hits[col].tolist()})
                    _HUC8_CACHE[key] = codes
                    log_fn(f"  HUC8: {', '.join(codes)} (bundled)")
                    return codes
    except Exception as ex:
        log_fn(f"HUC8 bundled lookup failed ({ex}) — falling back to network.")

    # 2) Network fallback via pynhd.  pynhd may be missing, throw on
    #    unsupported geom types, or hit a WFS lambda-arity bug — all of
    #    which are tolerated by returning an empty list.
    try:
        ssl._create_default_https_context = ssl._create_unverified_context
        import pynhd  # noqa
    except ImportError:
        log_fn("pynhd not installed — skipping HUC8 network lookup.")
        _HUC8_CACHE[key] = []
        return []

    try:
        _, geom = _aoi_geom_4326_for(aoi_path, feature_index)
        log_fn(f"Looking up HUC8 for {aoi_path} feature {feature_index} …")
        gdf = _bygeom_safe("wbd08", geom)
        if gdf is None or gdf.empty:
            _HUC8_CACHE[key] = []
            return []
        col = "huc8" if "huc8" in gdf.columns else next(
            (c for c in gdf.columns if c.lower() == "huc8"), None
        )
        if not col:
            _HUC8_CACHE[key] = []
            return []
        codes = sorted({str(c).zfill(8) for c in gdf[col].tolist()})
        _HUC8_CACHE[key] = codes
        log_fn(f"  HUC8: {', '.join(codes)}")
        return codes
    except Exception as ex:
        log_fn(f"HUC8 lookup failed: {ex}")
        _HUC8_CACHE[key] = []
        return []


# ── USGS gages ────────────────────────────────────────────────────────────────

def _pad_site_no(raw) -> str:
    """Return a correctly zero-padded USGS site-number string.

    USGS NWIS site numbers are standardised 8-digit strings (e.g. "02089000").
    GIS databases (GAGES-II shapefile, pynhd) often store them as plain integers,
    which silently drops the leading zero (2089000 → "2089000").  This function
    restores it: any all-digit string shorter than 8 characters is left-padded
    with zeros to reach 8 digits.

    Examples
    --------
    >>> _pad_site_no(2089000)   -> "02089000"
    >>> _pad_site_no("2089000") -> "02089000"
    >>> _pad_site_no("02089000")-> "02089000"  (already correct — unchanged)
    >>> _pad_site_no(12345678)  -> "12345678"  (already 8 digits — unchanged)
    """
    if raw is None:
        return ""
    s = str(raw).strip()
    # Only pad all-digit strings; leave alphanumeric IDs alone.
    if s.isdigit() and len(s) < 8:
        s = s.zfill(8)
    return s


# Map possible pynhd column names → our standard keys
_GAGE_COL_ALIASES = {
    "site_no":       ("staid", "site_no", "sta_id", "stationid"),
    "station_nm":    ("staname", "station_nm", "stationname", "sta_nm"),
    "lat":           ("lat_gage", "lat", "latitude"),
    "lon":           ("lng_gage", "lon", "longitude"),
    "drain_sqkm":    ("drain_sqkm", "drnarea_sqkm", "da_sqkm"),
    "state":         ("state", "state_cd", "state_abbr"),
}


def _pick(row, options):
    for c in options:
        if c in row and row[c] not in (None, "") and not (
            isinstance(row[c], float) and row[c] != row[c]   # NaN
        ):
            return row[c]
    return None


def lookup_usgs_gages(aoi_path: str, feature_index: int, log_fn=print) -> List[dict]:
    """Return a list of dicts describing USGS GAGES-II stations inside the AOI.

    Each dict has keys: ``site_no``, ``station_nm``, ``lat``, ``lon``,
    ``drain_sqkm`` (drainage area), ``state``.  Returns ``[]`` on error or
    if no gages fall inside the AOI.
    """
    key = (str(aoi_path), int(feature_index))
    if key in _GAGES_CACHE:
        return _GAGES_CACHE[key]

    try:
        ssl._create_default_https_context = ssl._create_unverified_context
        from pynhd import WaterData
    except ImportError:
        log_fn("pynhd not installed — skipping USGS gage lookup.")
        _GAGES_CACHE[key] = []
        return []

    try:
        _, geom = _aoi_geom_4326_for(aoi_path, feature_index)
        log_fn(f"Looking up USGS gages for {aoi_path} feature {feature_index} …")
        gdf = _bygeom_safe("gagesii", geom)
        if gdf is None or gdf.empty:
            _GAGES_CACHE[key] = []
            log_fn("  No USGS gages found.")
            return []

        gages = []
        for _, row in gdf.iterrows():
            d = {}
            for std_key, candidates in _GAGE_COL_ALIASES.items():
                d[std_key] = _pick(row, candidates)
            # Fall back to centroid for lat/lon if not present
            if d["lat"] is None or d["lon"] is None:
                pt = row.geometry.centroid if row.geometry else None
                if pt is not None:
                    d["lat"] = float(pt.y)
                    d["lon"] = float(pt.x)
            # Normalise site number: zero-pad to 8 digits so "2089000" → "02089000"
            if d["site_no"] is not None:
                d["site_no"] = _pad_site_no(d["site_no"])
            gages.append(d)

        _GAGES_CACHE[key] = gages
        log_fn(f"  Found {len(gages)} USGS gage(s).")
        return gages
    except Exception as ex:
        log_fn(f"USGS gage lookup failed: {ex}")
        _GAGES_CACHE[key] = []
        return []


# ── NHD flowlines clipped to AOI (for the 3-panel map overlay) ────────────────

def lookup_nhd_flowlines_clipped(
    aoi_path: str, feature_index: int, log_fn=print
) -> Tuple[Optional[gpd.GeoDataFrame], Optional[gpd.GeoDataFrame]]:
    """Return (clipped_flowlines, main_river_geom) for the AOI feature.

    `clipped_flowlines` is a GeoDataFrame of NHD flowlines clipped to the AOI.
    `main_river_geom` is a single-row GeoDataFrame holding the main river
    line (highest stream order, longest by length).  Both are in the AOI's
    native CRS so the map viewer can plot them on the same axes as the AOI.
    Returns (None, None) on any failure.
    """
    key = (str(aoi_path), int(feature_index))
    if key in _RIVER_GDF_CACHE:
        return _RIVER_GDF_CACHE[key]

    try:
        ssl._create_default_https_context = ssl._create_unverified_context
        from pynhd import NHD
        from core.bci import _build_main_river, _to_single_linestring
    except Exception:
        _RIVER_GDF_CACHE[key] = (None, None)
        return None, None

    try:
        feature_gdf = get_single_feature_gdf(aoi_path, feature_index)
        feature_4326 = feature_gdf.to_crs("EPSG:4326")
        geom = (
            feature_4326.geometry.union_all()
            if hasattr(feature_4326.geometry, "union_all")
            else feature_4326.unary_union
        )

        log_fn(f"Loading NHD flowlines for AOI feature {feature_index} …")
        try:
            flowlines = NHD("flowline_mr").bygeom(geom)
        except Exception as ex:
            msg = str(ex)
            # NHD's bygeom rejects MultiPolygon — retry with the bbox
            # tuple, which it does accept.
            if "should be of type" in msg or "MultiPolygon" in msg:
                bbox = tuple(geom.bounds)
                flowlines = NHD("flowline_mr").bygeom(bbox)
            else:
                raise
        if flowlines is None or flowlines.empty:
            _RIVER_GDF_CACHE[key] = (None, None)
            return None, None

        flowlines = flowlines.to_crs(feature_gdf.crs)
        clipped = gpd.overlay(
            flowlines, feature_gdf[["geometry"]], how="intersection"
        )
        clipped = clipped[
            clipped.geometry.type.isin(["LineString", "MultiLineString"])
        ].copy()
        if clipped.empty:
            _RIVER_GDF_CACHE[key] = (None, None)
            return None, None

        # Best-effort main river extraction
        main_gdf = None
        try:
            ms, _, main_line, river_name, order, _ = _build_main_river(clipped)
            if main_line is not None:
                main_gdf = gpd.GeoDataFrame(
                    [{"river_name": river_name, "stream_order": int(order)}],
                    geometry=[main_line], crs=clipped.crs,
                )
        except Exception:
            pass

        _RIVER_GDF_CACHE[key] = (clipped, main_gdf)
        return clipped, main_gdf
    except Exception as ex:
        log_fn(f"NHD flowline lookup failed: {ex}")
        _RIVER_GDF_CACHE[key] = (None, None)
        return None, None
