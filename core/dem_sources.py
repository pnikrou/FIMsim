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


def _footprint_key(item: dict) -> tuple:
    bb = item.get("boundingBox") or {}
    return (round(float(bb.get("minX", 0)), 4), round(float(bb.get("minY", 0)), 4),
            round(float(bb.get("maxX", 0)), 4), round(float(bb.get("maxY", 0)), 4))


def _newest_per_footprint(items: List[dict]) -> List[dict]:
    """One product per patch of ground — the most recently published.

    TNM returns every vintage it holds, so a lidar area reflown in 2021 also
    offers its 2013 tiles.  Mosaicking both would blend two surveys.
    """
    best: Dict[tuple, dict] = {}
    for it in items:
        key = _footprint_key(it)
        cur = best.get(key)
        if cur is None or str(it.get("publicationDate") or "") > str(
                cur.get("publicationDate") or ""):
            best[key] = it
    return list(best.values())


_YEAR_RE = None


def _survey_years(item: dict) -> Optional[str]:
    """The acquisition year embedded in a 3DEP product name.

    Publication is not acquisition: ``ned19_n43x25_w095x25_ia_northwest_2008``
    was published in 2012 but flown in 2008, and ``IA_NorthCentral_2020_D20``
    was published in 2022 and flown in 2020.  For deciding whether a DEM
    predates the flood you are modelling, the survey year is the one that
    matters, so it is reported alongside.
    """
    global _YEAR_RE
    if _YEAR_RE is None:
        import re
        _YEAR_RE = re.compile(r"(?<!\d)(19[89]\d|20[0-4]\d)(?!\d)")
    # Read the FILENAME, not the title.  A title carries both years —
    # "USGS NED ned19_n43x25_w095x25_ia_northwest_2008 1/9 arc-second 2012 …" —
    # and reporting "surveyed 2008, 2012" for one flight is worse than saying
    # nothing.  The filename holds only the project's own year.
    name = (item.get("downloadURL") or "").rsplit("/", 1)[-1]
    years = sorted(set(_YEAR_RE.findall(name)))
    if not years:                      # unnamed vintage — fall back to the title
        years = sorted(set(_YEAR_RE.findall(item.get("title") or "")))
    return ", ".join(years) if years else None


def describe_selection(source: str, chosen: List[dict],
                       all_items: List[dict]) -> dict:
    """What is about to be downloaded: how much, from when, and what was not.

    ``bytes_transferred`` is the honest figure, and it differs by product: the
    1 m GeoTIFFs are read window-by-window so only the AOI's part crosses the
    network, while the 1/9 arc-second archives have to be fetched whole (ERDAS
    IMG inside a zip is not laid out for random access).
    """
    src = normalise(source)
    total = sum(int(i.get("sizeInBytes") or 0) for i in chosen)
    whole_file = src == "3dep_19"
    pub = sorted({str(i.get("publicationDate") or "")[:4]
                  for i in chosen if i.get("publicationDate")})
    surveys = sorted({y for i in chosen for y in (_survey_years(i) or "").split(", ") if y})

    # Vintages TNM offered for the same ground that were passed over.
    chosen_keys = {_footprint_key(i) for i in chosen}
    chosen_ids = {id(i) for i in chosen}
    superseded = sorted({str(i.get("publicationDate") or "")[:4]
                         for i in all_items
                         if _footprint_key(i) in chosen_keys
                         and id(i) not in chosen_ids
                         and i.get("publicationDate")})
    return {
        "source": src,
        "n_tiles": len(chosen),
        "bytes_on_server": total,
        "bytes_transferred": total if whole_file else None,
        "published": pub,
        "survey_years": surveys,
        "superseded": superseded,
        "projects": sorted({(i.get("title") or "").split()[-1] for i in chosen
                            if i.get("title")}),
    }


def _human(n: Optional[int]) -> str:
    if not n:
        return "unknown size"
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


def selection_notes(info: dict) -> List[str]:
    """Lines describing a pending download — vintage first, then cost."""
    spec = SOURCES[info["source"]]
    out = []

    when = ""
    if info["survey_years"]:
        when = f"surveyed {', '.join(info['survey_years'])}"
        if info["published"]:
            when += f" (published {', '.join(info['published'])})"
    elif info["published"]:
        when = f"published {', '.join(info['published'])}"
    out.append(f"{info['n_tiles']} tile(s)" + (f", {when}" if when else ""))

    if info["superseded"]:
        out.append(f"Newer data is being used: {', '.join(info['superseded'])} "
                   f"also covers this AOI and was passed over, so the mosaic "
                   f"is one survey rather than a blend.")

    if info["bytes_transferred"]:
        out.append(f"~{_human(info['bytes_transferred'])} will be downloaded — "
                   f"{spec['short']} tiles are zipped archives that must be "
                   f"fetched whole, so expect several minutes.")
    elif info["bytes_on_server"] and spec["native_m"] <= 3.0:
        out.append(f"These are large tiles ({_human(info['bytes_on_server'])} "
                   f"on the server); only the part covering your AOI is read, "
                   f"but expect this to be slower than 1/3 arc-second.")
    return out


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


def tile_selection(aoi_gdf, source: str, log_fn=print, timeout: int = 60):
    """``(gdal_paths, info)`` for every tile of ``source`` covering the AOI.

    ``info`` is what describe_selection() returns — how many tiles, from which
    survey, how much data, and which older vintages were passed over — so the
    run can both tell the user and record it in the project.

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
    all_items = query_tnm(bounds, spec["tnm"], timeout=timeout, log_fn=log_fn)
    chosen = _newest_per_footprint(all_items)
    paths = [p for p in (_gdal_path(i) for i in chosen) if p]

    if not paths:
        place = None
        try:
            from core.state_lookup import detect_us_state
            place = (detect_us_state(aoi_gdf) or {}).get("state_name")
        except Exception:
            pass
        raise NoCoverageError(no_coverage_message(src, place))

    info = describe_selection(src, chosen, all_items)
    for line in selection_notes(info):
        log_fn(f"  {line}")
    return paths, info


def tile_paths(aoi_gdf, source: str, log_fn=print, timeout: int = 60) -> List[str]:
    """Just the GDAL paths, for callers that do not need the provenance."""
    return tile_selection(aoi_gdf, source, log_fn=log_fn, timeout=timeout)[0]
