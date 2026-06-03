"""Working coordinate system selection.

Every raster the app produces (DEM, LULC, Manning) is reprojected into a
single, metric, projected CRS so that "10 m cell size" really means 10
metres on the ground.  The CRS is chosen automatically from the AOI's
centroid:

  * AOI in the NAD83 UTM footprint (zones 1N – 23N, i.e. essentially
    North America)  →  ``EPSG:269xx`` where xx = zone number (10N → 26910,
    16N → 26916, etc.).  Matches the NAD83 datum NLCD is published in.
  * AOI outside the NAD83 footprint           →  WGS84 UTM
    (``EPSG:326xx`` / ``EPSG:327xx``) as a graceful fallback so the
    pipeline still works on non-CONUS data, with a log warning.

This module is intentionally tiny and dependency-light: any module that
takes ``aoi_gdf`` can call :func:`pick_working_crs_epsg` and then use the
returned EPSG without having to know how the choice was made.

Reference: NAD83 UTM zones cover zone 1N (EPSG:26901) through zone 23N
(EPSG:26923), which spans longitudes 180°W to 42°W in the Northern
Hemisphere.  CONUS uses zones 10N (W Coast) – 19N (Maine).
"""
from __future__ import annotations

import math
from typing import Tuple


# ── Pure helpers ─────────────────────────────────────────────────────────────


def utm_zone_from_lon(lon: float) -> int:
    """Standard 6°-wide UTM zone number (1–60) for a given longitude."""
    # Wrap longitudes into [-180, 180) so values like 181 or -181 still work.
    lon = ((float(lon) + 180.0) % 360.0) - 180.0
    return int(math.floor((lon + 180.0) / 6.0)) + 1


def nad83_utm_epsg_from_lonlat(lon: float, lat: float) -> int | None:
    """Return the NAD83 UTM EPSG code for (lon, lat) if it falls in NAD83's
    UTM footprint (zones 1N–23N), else ``None``.

    NAD83 only defines official UTM projections for the Northern Hemisphere,
    zones 1 through 23 — covering essentially all of North America.
    Anything outside that range cannot be expressed as ``NAD83 / UTM zone …``
    and the caller should fall back to WGS84 UTM.
    """
    if lat < 0:
        return None
    zone = utm_zone_from_lon(lon)
    if 1 <= zone <= 23:
        return 26900 + zone
    return None


def wgs84_utm_epsg_from_lonlat(lon: float, lat: float) -> int:
    """Return the WGS84 UTM EPSG code for any (lon, lat) on Earth."""
    zone = utm_zone_from_lon(lon)
    return (32600 if lat >= 0 else 32700) + zone


# ── AOI-driven picker ────────────────────────────────────────────────────────


def _aoi_centroid_lonlat(aoi_gdf) -> Tuple[float, float]:
    """Return the AOI centroid as (lon, lat) in EPSG:4326.

    Works whether the input GeoDataFrame is geographic or projected.
    """
    if aoi_gdf is None or len(aoi_gdf) == 0:
        raise ValueError("AOI is empty — cannot pick a working CRS.")
    if aoi_gdf.crs is None:
        raise ValueError(
            "AOI has no CRS defined.  Assign a CRS in QGIS / GeoPandas "
            "before passing it to the pipeline."
        )
    # Reproject to 4326 *only* for the centroid lookup, never modify caller.
    try:
        g4326 = aoi_gdf.to_crs("EPSG:4326")
    except Exception as ex:
        raise RuntimeError(
            f"Could not reproject AOI to EPSG:4326 for working-CRS "
            f"selection.  Source CRS: {aoi_gdf.crs}.  ({ex})"
        ) from ex
    c = g4326.geometry.unary_union.centroid
    if not (math.isfinite(c.x) and math.isfinite(c.y)):
        raise RuntimeError(
            "AOI centroid is non-finite — geometry is likely invalid."
        )
    return float(c.x), float(c.y)


def pick_working_crs_epsg(aoi_gdf, log_fn=print) -> int:
    """Auto-pick the working CRS EPSG code for an AOI.

    Prefers NAD83 / UTM zone N when the AOI centroid lies within zones
    1N–23N (essentially North America); falls back to WGS84 / UTM
    otherwise, with a log warning so users outside the NAD83 footprint
    know what they got.

    The returned EPSG is the canonical "working" CRS for the AOI: every
    raster (DEM, LULC, Manning) the pipeline writes for this AOI will be
    in this CRS, at metric cell sizes.
    """
    lon, lat = _aoi_centroid_lonlat(aoi_gdf)
    nad83 = nad83_utm_epsg_from_lonlat(lon, lat)
    if nad83 is not None:
        zone = nad83 - 26900
        log_fn(
            f"Working CRS: NAD83 / UTM zone {zone}N (EPSG:{nad83}) — "
            f"picked from AOI centroid ({lon:.4f}, {lat:.4f})."
        )
        return nad83
    wgs = wgs84_utm_epsg_from_lonlat(lon, lat)
    hemi = "N" if lat >= 0 else "S"
    zone = wgs - (32600 if lat >= 0 else 32700)
    log_fn(
        f"Working CRS: WGS84 / UTM zone {zone}{hemi} (EPSG:{wgs}) — "
        f"AOI centroid ({lon:.4f}, {lat:.4f}) is outside the NAD83 "
        "footprint, falling back to WGS84 UTM."
    )
    return wgs


def working_crs_label(epsg: int) -> str:
    """Pretty label for a working-CRS EPSG code (for logs / UI)."""
    if 26901 <= epsg <= 26923:
        return f"NAD83 / UTM zone {epsg - 26900}N (EPSG:{epsg})"
    if 32601 <= epsg <= 32660:
        return f"WGS84 / UTM zone {epsg - 32600}N (EPSG:{epsg})"
    if 32701 <= epsg <= 32760:
        return f"WGS84 / UTM zone {epsg - 32700}S (EPSG:{epsg})"
    return f"EPSG:{epsg}"
