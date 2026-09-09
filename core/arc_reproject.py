"""Put ARC-Curve2Flood outputs into the AOI's own CRS at a metric resolution.

ARC must be RUN in geographic coordinates: both NenCarta and ARC write
``Spatial_Units\tdeg`` into the ARC input file unconditionally
(nencarta/main.py :: _write_arc_input_section, arc/process_geospatial_data.py),
with no key to change it.  Feeding ARC a projected DEM makes it size
cross-sections in degrees against a metre grid, and it then produces no rating
curves at all.

So the run stays in EPSG:4326 and the *products* are reprojected afterwards,
which is what actually matters for comparison: LISFLOOD-FP, TRITON and
OWP HAND-FIM all deliver rasters in the AOI's projected CRS at a fixed cell
size, and a flood map can only be differenced against them on a shared grid.

The 4326 run is ~8.983e-05 deg (the native 1/3 arc-second 3DEP grid, about
10 m), so resampling to 10 m is close to 1:1 rather than a real change of
information.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling


def aoi_crs(aoi_path: str):
    """The CRS of the AOI every product should land in."""
    gdf = gpd.read_file(aoi_path)
    if gdf.crs is None:
        raise ValueError(f"AOI has no CRS: {aoi_path}")
    return gdf.crs


def reproject_raster(src_path: str, dst_path: str, dst_crs, res_m: float = 10.0,
                     categorical: bool = False, log_fn=print) -> Optional[str]:
    """Reproject one raster to ``dst_crs`` on an exact ``res_m`` grid.

    ``categorical`` picks nearest-neighbour, which is what a flood EXTENT needs
    (its values are class codes — 1/0, or a percent-of-ensemble); depth, WSE and
    velocity are continuous and get bilinear.
    """
    src_path = str(src_path)
    if not Path(src_path).exists():
        return None
    with rasterio.open(src_path) as src:
        transform, width, height = calculate_default_transform(
            src.crs, dst_crs, src.width, src.height, *src.bounds,
            resolution=(res_m, res_m))
        profile = src.profile.copy()
        profile.update(crs=dst_crs, transform=transform,
                       width=width, height=height,
                       compress="lzw", tiled=True,
                       blockxsize=256, blockysize=256)
        Path(dst_path).parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(dst_path, "w", **profile) as dst:
            for b in range(1, src.count + 1):
                reproject(
                    source=rasterio.band(src, b),
                    destination=rasterio.band(dst, b),
                    src_transform=src.transform, src_crs=src.crs,
                    dst_transform=transform, dst_crs=dst_crs,
                    src_nodata=src.nodata, dst_nodata=src.nodata,
                    resampling=(Resampling.nearest if categorical
                                else Resampling.bilinear))
    return str(dst_path)


def _pretty_name(src_name: str) -> str:
    """A readable output name: the timestep or event, not NenCarta's full stem.

    ``GEOGLOWS_DEM_10_15_ARC_FloodDepth_flow_20161010_0000.tif``
        -> ``depth_2016-10-10_0000.tif``
    ``GEOGLOWS_DEM_10_15_ARC_Flood_Forecast_20161010.tif``
        -> ``FIM_2016-10-10.tif``
    """
    import re
    kind = "depth" if "FloodDepth" in src_name else (
        "WSE" if "FloodWSE" in src_name else (
            "velocity" if "FloodVEL" in src_name else "FIM"))
    m = re.search(r"(\d{8})_(\d{4})", src_name)
    if m:
        d = m.group(1)
        return f"{kind}_{d[:4]}-{d[4:6]}-{d[6:]}_{m.group(2)}.tif"
    m = re.search(r"(\d{8})", src_name)
    if m:
        d = m.group(1)
        return f"{kind}_{d[:4]}-{d[4:6]}-{d[6:]}.tif"
    tag = "bathy" if "Bathy" in src_name else (
        "initial" if "Initial" in src_name else "map")
    return f"{kind}_{tag}.tif"


def reproject_flood_maps(maps: List[str], aoi_path: str, out_dir,
                         res_m: float = 10.0, log_fn=print) -> List[dict]:
    """Reproject every NenCarta flood raster into the AOI's CRS at ``res_m``.

    Returns ``[{"source", "path", "kind"}, …]`` for the products written.
    """
    if not maps:
        return []
    crs = aoi_crs(aoi_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    log_fn(f"Reprojecting {len(maps)} raster(s) to {crs.to_string()} "
           f"at {res_m:g} m …")
    made = []
    for src in maps:
        name = Path(src).name
        # Extent rasters carry class codes, so they must not be interpolated.
        categorical = ("FloodDepth" not in name and "FloodWSE" not in name
                       and "FloodVEL" not in name)
        dst = out / _pretty_name(name)
        try:
            p = reproject_raster(src, dst, crs, res_m=res_m,
                                 categorical=categorical, log_fn=log_fn)
        except Exception as exc:
            log_fn(f"  ⚠ could not reproject {name}: {exc}")
            continue
        if p:
            made.append({"source": src, "path": p,
                         "kind": ("extent" if categorical else "continuous")})
    log_fn(f"  wrote {len(made)} raster(s) in {out}")
    return made


def summarise(paths: List[str], log_fn=print) -> None:
    """Log inundated area per product — a cheap check that they are not empty."""
    for p in paths:
        try:
            with rasterio.open(p) as d:
                a = d.read(1).astype("float64")
                nod = d.nodata
                wet = int(((a > 0) & (a != nod)).sum()) if nod is not None \
                    else int((a > 0).sum())
                cs = abs(d.transform.a)
                log_fn(f"    {Path(p).name}: {wet:,} wet cells "
                       f"({wet * cs * cs / 1e6:.1f} km²)")
        except Exception:
            continue
