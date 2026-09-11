"""Reading vector files that are not quite intact.

A shapefile is really several files, and the one most often lost in a copy,
a zip, or an ArcGIS lock-file cleanup is the ``.shx`` index.  Without it GDAL
refuses the whole dataset:

    pyogrio.errors.DataSourceError: Unable to open <name>.shx or <name>.SHX.
    Set SHAPE_RESTORE_SHX config option to YES to restore or create it.

The error names its own fix, and the fix is safe: the index is derived entirely
from the ``.shp``, so rebuilding it recovers the original rather than guessing
at it.  Doing that automatically is the difference between "FIMsim cannot open
my AOI" and a run that just works.
"""
from __future__ import annotations

import os
from pathlib import Path

import geopandas as gpd


def _missing_shx(exc: Exception) -> bool:
    msg = str(exc).lower()
    return ".shx" in msg and ("unable to open" in msg or "restore" in msg)


def read_vector(path, log_fn=print, **kwargs) -> gpd.GeoDataFrame:
    """``gpd.read_file`` that repairs a missing shapefile index and retries.

    Falls back from pyogrio to fiona the way the AOI step always has, so a file
    one engine dislikes still gets a second reading.
    """
    path = str(path)
    attempts = []
    for engine in ("pyogrio", "fiona"):
        try:
            return gpd.read_file(path, engine=engine, **kwargs)
        except Exception as exc:
            attempts.append((engine, exc))
            if not _missing_shx(exc):
                continue
            # Rebuild the index from the .shp, then read again.
            shx = Path(path).with_suffix(".shx")
            log_fn(f"  {Path(path).name} has no .shx index — rebuilding it "
                   f"from the .shp …")
            old = os.environ.get("SHAPE_RESTORE_SHX")
            os.environ["SHAPE_RESTORE_SHX"] = "YES"
            try:
                gdf = gpd.read_file(path, engine=engine, **kwargs)
                log_fn(f"  ✓ index rebuilt ({shx.name}) — "
                       f"read {len(gdf)} feature(s)")
                return gdf
            except Exception as exc2:
                attempts.append((f"{engine}+SHAPE_RESTORE_SHX", exc2))
            finally:
                if old is None:
                    os.environ.pop("SHAPE_RESTORE_SHX", None)
                else:
                    os.environ["SHAPE_RESTORE_SHX"] = old

    detail = "\n".join(f"  {eng}: {exc}" for eng, exc in attempts)
    raise RuntimeError(f"Could not read {path}:\n{detail}")
