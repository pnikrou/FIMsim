"""The elevation products FIMsim can download, and where each one exists.

FIMsim used to offer one download source — 3DEP 1/3 arc-second — with the
resolution baked into a module constant.  Asking for a 1 m DEM therefore did not
fetch 1 m data: it resampled ~10 m data onto a 1 m grid, which looks like detail
without being detail.  This module adds the products that really are finer, and
makes each one's identity explicit in the UI.

    3dep_13  3DEP (USGS, 1/3 arc-second ~10 m)   nationwide
    3dep_19  3DEP (USGS, 1/9 arc-second ~3 m)    partial
    3dep_1m  3DEP (USGS, 1 meter)                partial
    hand     HAND (UT Austin TACC, ~10 m)        CONUS, by HUC6

Coverage is the whole point of the two new ones: 1/9 arc-second and 1 m exist
only where somebody flew lidar, so "which tiles cover this AOI" has to be asked
rather than computed.  The USGS TNM Access API is that index, and its answer is
also the answer to "is there any 1 m data here at all" — no products returned
means no coverage, which the caller can say plainly instead of failing later
with an empty mosaic.

Layouts, all verified against the live bucket rather than documentation:

  1/3 arc-second  1x1 degree COG        .../Elevation/13/TIFF/current/<tile>/USGS_13_<tile>.tif
  1/9 arc-second  15x15 minute IMG      .../Elevation/19/IMG/<tile>.zip  (read via /vsizip/)
                                        EPSG:4269, 3.086e-05 deg (~3.4 m)
  1 meter         10 km UTM GeoTIFF     .../Elevation/1m/Projects/<project>/TIFF/USGS_1M_<zone>_x<X>y<Y>_<project>.tif

Discovery for 1/3 stays with the existing degree-tile URL builder in core/dem.py
— it is nationwide, needs no query, and reads only the AOI window of each tile.
TNM is used for the two sparse products, where the tile name cannot be derived
from coordinates.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Dict, List, Optional

TNM_API = "https://tnmaccess.nationalmap.gov/api/v1/products"

# Everything the UI and the downloader need to know about a source, in one place
# so a label can never drift from the data it describes.
SOURCES: Dict[str, dict] = {
    "3dep_13": {
        "label": "3DEP (USGS, 1/3 arc-second ≈ 10 m)",
        "short": "3DEP 1/3\"",
        "native_m": 10.0,
        "tnm": "National Elevation Dataset (NED) 1/3 arc-second",
        "coverage": "nationwide",
        "discovery": "degree-tiles",
    },
    "3dep_19": {
        "label": "3DEP (USGS, 1/9 arc-second ≈ 3 m)",
        "short": "3DEP 1/9\"",
        "native_m": 3.0,
        "tnm": "National Elevation Dataset (NED) 1/9 arc-second",
        "coverage": "partial",
        "discovery": "tnm",
    },
    "3dep_1m": {
        "label": "3DEP (USGS, 1 meter)",
        "short": "3DEP 1 m",
        "native_m": 1.0,
        "tnm": "Digital Elevation Model (DEM) 1 meter",
        "coverage": "partial",
        "discovery": "tnm",
    },
    "hand": {
        "label": "HAND (UT Austin TACC, ≈ 10 m)",
        "short": "HAND",
        "native_m": 10.0,
        "tnm": None,
        "coverage": "CONUS (by HUC6)",
        "discovery": "huc6",
    },
}

# Accepted spellings for the source id, including the pre-existing ones so saved
# projects and older call sites keep working.
ALIASES = {
    "3dep": "3dep_13", "13": "3dep_13", "1/3": "3dep_13",
    "19": "3dep_19", "1/9": "3dep_19",
    "1m": "3dep_1m", "1 m": "3dep_1m",
    "download_3dep": "3dep_13",
}


class NoCoverageError(RuntimeError):
    """Raised when a product simply does not exist over the AOI."""


def normalise(source: Optional[str]) -> str:
    s = str(source or "3dep_13").strip().lower()
    s = ALIASES.get(s, s)
    if s not in SOURCES:
        raise ValueError(f"Unknown DEM source {source!r}. "
                         f"Choose one of: {', '.join(SOURCES)}.")
    return s


def label_for(source: Optional[str]) -> str:
    return SOURCES[normalise(source)]["label"]


def native_res_m(source: Optional[str]) -> float:
    return float(SOURCES[normalise(source)]["native_m"])


def wgs84_bounds(aoi_gdf):
    """(minx, miny, maxx, maxy) of the AOI in EPSG:4326."""
    g = aoi_gdf if str(aoi_gdf.crs).upper().endswith("4326") else aoi_gdf.to_crs(4326)
    return tuple(float(v) for v in g.total_bounds)


class TNMUnavailable(RuntimeError):
    """The index could not be reached — which is NOT the same as no coverage."""


# One query per (bounds, dataset) per session.  The availability check and the
# download ask the same question, and TNM is slow enough to be worth not
# asking twice.
_TNM_CACHE: Dict[tuple, List[dict]] = {}

# TNM's `max` is not a plain limit: small values come back with no items and no
# total even where data exists (verified — max=1 and max=5 returned nothing for
# an Iowa AOI that really has three 1 m tiles, while max=500 returned all
# three).  A too-small max therefore reads as "no coverage", which is the one
# answer this module must never get wrong.  So always ask for the full page.
_TNM_MAX = 500


def query_tnm(bounds_wgs84, dataset: str, timeout: int = 90,
              attempts: int = 4, log_fn=print) -> List[dict]:
    """Ask TNM which products of ``dataset`` intersect the bounds.

    Retries: the service returns 504 Gateway Timeout often enough that a single
    attempt would regularly be mistaken for an empty area.
    """
    import time

    key = (tuple(round(float(v), 6) for v in bounds_wgs84), dataset)
    if key in _TNM_CACHE:
        return _TNM_CACHE[key]

    url = (f"{TNM_API}?bbox=" + ",".join(f"{v:.6f}" for v in bounds_wgs84)
           + "&datasets=" + urllib.parse.quote(dataset)
           + f"&max={_TNM_MAX}&outputFormat=JSON")
    last = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                payload = json.load(resp)
            items = payload.get("items") or []
            # An empty page with no "total" is a bad response, not an empty
            # area: a real empty area answers total=0.
            if not items and payload.get("total") is None:
                last = "the index returned an incomplete response"
            else:
                _TNM_CACHE[key] = items
                return items
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
        if attempt < attempts:
            log_fn(f"  the USGS index did not answer ({last}); retrying "
                   f"({attempt}/{attempts - 1}) …")
            time.sleep(2 * attempt)
    raise TNMUnavailable(
        f"Could not reach the USGS product index ({TNM_API}) — {last}. "
        "This says nothing about whether data exists here; try again, or use "
        f"{SOURCES['3dep_13']['label']}, which needs no lookup.")


def _gdal_path(item: dict) -> Optional[str]:
    """A GDAL-openable path for one TNM product record.

    1 m tiles are plain GeoTIFFs.  1/9 arc-second tiles are zipped ERDAS IMG,
    where the member is the archive's own stem — so they open through /vsizip/
    without downloading and unpacking the archive first.
    """
    url = item.get("downloadURL") or ""
    if not url:
        return None
    low = url.lower()
    if low.endswith(".tif") or low.endswith(".tiff"):
        return f"/vsicurl/{url}"
    if low.endswith(".zip"):
        stem = url.rsplit("/", 1)[-1][:-4]
        return f"/vsizip//vsicurl/{url}/{stem}.img"
    return None


def _newest_per_footprint(items: List[dict]) -> List[dict]:
    """One product per patch of ground — the most recently published.

    TNM returns every vintage it holds, so a lidar area reflown in 2021 also
    offers its 2013 tiles.  Mosaicking both would blend two surveys.
    """
    best: Dict[tuple, dict] = {}
    for it in items:
        bb = it.get("boundingBox") or {}
        key = (round(float(bb.get("minX", 0)), 4), round(float(bb.get("minY", 0)), 4),
               round(float(bb.get("maxX", 0)), 4), round(float(bb.get("maxY", 0)), 4))
        cur = best.get(key)
        if cur is None or str(it.get("publicationDate") or "") > str(
                cur.get("publicationDate") or ""):
            best[key] = it
    return list(best.values())


def count_available(aoi_gdf, source: str, timeout: int = 90, log_fn=print) -> int:
    """Tiles of ``source`` covering this AOI.

    ``0`` means the product genuinely does not exist here; ``-1`` means the
    question could not be asked (no lookup needed, or the index was down).  The
    two must stay distinguishable — reporting "no data for your area" because a
    server timed out is exactly the wrong thing to tell someone.
    """
    src = normalise(source)
    spec = SOURCES[src]
    if spec["discovery"] != "tnm":
        return -1          # nationwide / HUC6 — not a TNM question
    try:
        items = query_tnm(wgs84_bounds(aoi_gdf), spec["tnm"], timeout=timeout,
                          log_fn=log_fn)
    except TNMUnavailable:
        return -1
    return len(_newest_per_footprint(items))


def availability_report(aoi_gdf, timeout: int = 90, log_fn=print) -> Dict[str, int]:
    """``{source_id: tile count}`` for every source that has to be looked up."""
    return {s: count_available(aoi_gdf, s, timeout=timeout, log_fn=log_fn)
            for s, spec in SOURCES.items() if spec["discovery"] == "tnm"}


def no_coverage_message(source: str, place: Optional[str] = None) -> str:
    """Why the run cannot proceed, and what the user can do instead."""
    spec = SOURCES[normalise(source)]
    where = f" for {place}" if place else " for this AOI's location"
    return (
        f"No {spec['label']} data exists{where}. "
        f"{spec['short']} is flown project by project and only covers part of "
        f"the country. Choose {SOURCES['3dep_13']['label']}, which covers the "
        f"whole of the United States, or supply your own raster."
    )


def tile_paths(aoi_gdf, source: str, log_fn=print, timeout: int = 60) -> List[str]:
    """GDAL paths for every tile of ``source`` covering the AOI.

    Raises ``NoCoverageError`` when the product does not exist here — which is a
    fact about the location, not a failure, and reads very differently to the
    user than an empty mosaic three steps later.
    """
    src = normalise(source)
    spec = SOURCES[src]
    if spec["discovery"] != "tnm":
        raise ValueError(f"{spec['label']} is not discovered through TNM.")

    bounds = wgs84_bounds(aoi_gdf)
    log_fn(f"Looking up {spec['label']} tiles covering the AOI …")
    items = query_tnm(bounds, spec["tnm"], timeout=timeout, log_fn=log_fn)
    items = _newest_per_footprint(items)
    paths = [p for p in (_gdal_path(i) for i in items) if p]

    if not paths:
        place = None
        try:
            from core.state_lookup import detect_us_state
            place = (detect_us_state(aoi_gdf) or {}).get("state_name")
        except Exception:
            pass
        raise NoCoverageError(no_coverage_message(src, place))

    years = sorted({str(i.get("publicationDate") or "")[:4]
                    for i in items if i.get("publicationDate")})
    log_fn(f"  {len(paths)} tile(s)" + (f", published {', '.join(years)}" if years else ""))
    return paths
