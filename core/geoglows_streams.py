"""Download the GEOGLOWS v2 stream network for an AOI.

NenCarta with ``streamflow_source: "GEOGLOWS"`` reads the reach ids off the
flowline's ``LINKNO`` field and uses them to select from
``s3://geoglows-v2-forecasts/<date>00.zarr``.  Those ids must therefore be
genuine GEOGLOWS v2 (TDX-Hydro) reach ids — NHD ``COMID`` values renamed to
LINKNO would silently select nothing.

The authoritative source is the public GEOGLOWS v2 bucket
(https://geoglows-v2.s3-us-west-2.amazonaws.com, listed at
s3://geoglows-v2, licence in that bucket's licences.md)::

    hydrography-global/vpu-boundaries.gpkg      the 125 VPU polygons
    hydrography/vpu=<VPU>/streams_<VPU>.gpkg    that VPU's stream network

Both are large (1.9 GB and ~250 MB), so nothing is bulk-downloaded: GDAL reads
them over ``/vsicurl/`` with range requests and a bounding-box filter, which
returns just the reaches inside the AOI in a few seconds.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Tuple

import geopandas as gpd

BUCKET_HTTPS = "https://geoglows-v2.s3-us-west-2.amazonaws.com"
VPU_BOUNDARIES = f"/vsicurl/{BUCKET_HTTPS}/hydrography-global/vpu-boundaries.gpkg"


def _streams_uri(vpu) -> str:
    return f"/vsicurl/{BUCKET_HTTPS}/hydrography/vpu={int(vpu)}/streams_{int(vpu)}.gpkg"


def _prepare_gdal_env():
    """Anonymous access + a sane range-request cache for the remote reads."""
    os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")
    os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    os.environ.setdefault("CPL_VSIL_CURL_CACHE_SIZE", "200000000")
    os.environ.setdefault("VSI_CACHE", "TRUE")


def _aoi_bbox_in(aoi_path: str, crs) -> Tuple[float, float, float, float]:
    aoi = gpd.read_file(aoi_path)
    if aoi.crs is None:
        raise ValueError(f"AOI has no CRS: {aoi_path}")
    return tuple(aoi.to_crs(crs).total_bounds)


VPU_INDEX = Path(__file__).parent / "data" / "geoglows_vpu_index.json"


def _load_vpu_index() -> Optional[dict]:
    if not VPU_INDEX.exists():
        return None
    import json
    try:
        with open(VPU_INDEX, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def find_vpu(aoi_path: str, log_fn=print) -> int:
    """The GEOGLOWS VPU code covering this AOI.

    Uses the small bundled index of per-VPU bounds (built from each VPU's own
    streams_<VPU>.gpkg).  Querying vpu-boundaries.gpkg directly instead is
    correct but useless in practice: a single bounding-box read of that 1.9 GB
    file over HTTP measured ~18 minutes, because the spatial filter still has
    to scan it.  The index answers the same question offline in milliseconds.

    VPU bounding boxes overlap, so when several match, each candidate's stream
    network is probed for reaches actually inside the AOI (a fast bbox read)
    and the richest one wins.
    """
    _prepare_gdal_env()
    import pyogrio

    idx = _load_vpu_index()
    if not idx:
        log_fn("VPU index missing — falling back to vpu-boundaries.gpkg "
               "(this can take many minutes).")
        info = pyogrio.read_info(VPU_BOUNDARIES)
        gdf = pyogrio.read_dataframe(
            VPU_BOUNDARIES, bbox=_aoi_bbox_in(aoi_path, info["crs"]))
        if gdf.empty:
            raise ValueError("No GEOGLOWS VPU covers this AOI.")
        return int(gdf.iloc[0]["VPU"])

    minx, miny, maxx, maxy = _aoi_bbox_in(aoi_path, idx.get("crs", "EPSG:3857"))
    cands = [int(v) for v, meta in idx["vpus"].items()
             if not (meta["bounds"][2] < minx or meta["bounds"][0] > maxx
                     or meta["bounds"][3] < miny or meta["bounds"][1] > maxy)]
    if not cands:
        raise ValueError(
            "No GEOGLOWS VPU covers this AOI — check the AOI's location/CRS.")
    if len(cands) == 1:
        log_fn(f"GEOGLOWS VPU for this AOI: {cands[0]}")
        return cands[0]

    # Probe the most plausible candidate first — the one whose bounds overlap
    # the AOI most — and stop as soon as one actually has reaches.  Each probe
    # is a remote read, so ordering turns the usual case into a single one.
    def _overlap(v):
        b = idx["vpus"][str(v)]["bounds"]
        return (max(0.0, min(b[2], maxx) - max(b[0], minx))
                * max(0.0, min(b[3], maxy) - max(b[1], miny)))

    cands.sort(key=_overlap, reverse=True)
    log_fn(f"AOI falls in {len(cands)} candidate VPU(s) {cands} — "
           f"checking which actually has reaches here …")
    best, best_n = None, 0
    for v in cands:
        try:
            g = pyogrio.read_dataframe(_streams_uri(v),
                                       bbox=(minx, miny, maxx, maxy),
                                       columns=["LINKNO"])
            log_fn(f"    VPU {v}: {len(g)} reach(es)")
            if len(g) > best_n:
                best, best_n = v, len(g)
            if best_n:
                break
        except Exception as exc:
            log_fn(f"    VPU {v}: could not read ({type(exc).__name__})")
    if best is None:
        raise ValueError("No GEOGLOWS reaches found in any candidate VPU.")
    log_fn(f"GEOGLOWS VPU for this AOI: {best} ({best_n} reach(es))")
    return best


def download_geoglows_streams(aoi_path: str, out_path: str,
                              vpu: Optional[int] = None,
                              buffer_m: float = 2000.0,
                              log_fn=print) -> str:
    """Write the GEOGLOWS reaches covering ``aoi_path`` to ``out_path``.

    The AOI box is buffered slightly so reaches entering and leaving the domain
    are kept whole, which matters for the upstream/downstream topology NenCarta
    walks via LINKNO/DSLINKNO.  Returns the written path.
    """
    _prepare_gdal_env()
    import pyogrio

    if vpu is None:
        vpu = find_vpu(aoi_path, log_fn=log_fn)
    uri = _streams_uri(vpu)

    info = pyogrio.read_info(uri)
    crs = info["crs"]
    minx, miny, maxx, maxy = _aoi_bbox_in(aoi_path, crs)
    bbox = (minx - buffer_m, miny - buffer_m, maxx + buffer_m, maxy + buffer_m)

    log_fn(f"Reading GEOGLOWS streams for VPU {vpu} "
           f"({info['features']:,} reaches in the VPU) …")
    gdf = pyogrio.read_dataframe(uri, bbox=bbox)
    if gdf.empty:
        raise ValueError(
            f"No GEOGLOWS reaches fall inside this AOI (VPU {vpu}). "
            "The AOI may be outside the modelled network.")

    missing = [c for c in ("LINKNO", "DSLINKNO") if c not in gdf.columns]
    if missing:
        raise ValueError(
            f"GEOGLOWS streams are missing {missing} — NenCarta needs both to "
            "resolve reach topology for streamflow_source 'GEOGLOWS'.")

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    # Shapefile truncates field names to 10 chars; every field we depend on
    # (LINKNO, DSLINKNO, strmOrder) is already within that, so the shapefile
    # NenCarta expects round-trips safely.
    gdf.to_file(out)
    log_fn(f"GEOGLOWS flowline: {len(gdf):,} reach(es) -> {out}")
    log_fn(f"  LINKNO range {int(gdf['LINKNO'].min())} … {int(gdf['LINKNO'].max())}"
           + (f", stream orders {int(gdf['strmOrder'].min())}–"
              f"{int(gdf['strmOrder'].max())}" if "strmOrder" in gdf.columns else ""))
    return str(out)
