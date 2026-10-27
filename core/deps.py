"""Startup check that the environment actually has what FIMsim needs.

FIMsim is meant to be installed from ``environment.yml``.  Installing the
packages by hand produces an environment that *starts* perfectly well and then
fails in the middle of a workflow — a user hit exactly that with a hand-built
env and lost a retrospective download to

    NWM retrospective download failed: zarr and s3fs are required …

by which point they had already chosen an AOI, a date range and 309 reaches.
The imports below are checked at startup instead, so a half-built environment
is reported once, up front, with the command that repairs it.

Only modules FIMsim imports itself are listed.  Each entry is

    (import name, conda/pip package, what stops working without it)
"""
from __future__ import annotations

import importlib.util
import sys
from typing import List, Tuple

# Required for the app to be usable at all.
_REQUIRED: Tuple[Tuple[str, str, str], ...] = (
    ("PyQt6",      "pyqt6",        "the user interface"),
    ("numpy",      "numpy",        "all raster and array maths"),
    ("pandas",     "pandas",       "every table, CSV and hydrograph"),
    ("geopandas",  "geopandas",    "reading AOI shapefiles"),
    ("shapely",    "shapely",      "AOI geometry"),
    ("pyproj",     "pyproj",       "coordinate transforms"),
    ("rasterio",   "rasterio",     "reading and writing DEMs"),
    ("matplotlib", "matplotlib",   "map and raster previews"),
    ("requests",   "requests",     "every data download"),
    ("scipy",      "scipy",        "DEM nodata fill and Manning rasters"),
)

# Required by a specific mode or step.  Missing one of these does not stop the
# app opening, but it does stop that step, so say which step up front.
_PER_FEATURE: Tuple[Tuple[str, str, str], ...] = (
    ("zarr",        "zarr",        "NWM retrospective discharge (Flow Data, BDY, TRITON hydro)"),
    ("s3fs",        "s3fs",        "NWM retrospective discharge (Flow Data, BDY, TRITON hydro)"),
    ("xarray",      "xarray",      "NWM discharge and ARC flow series"),
    ("netCDF4",     "netcdf4",     "NWM short-range forecast discharge"),
    ("pynhd",       "pynhd",       "NHD flowlines, HUC lookup and USGS gages"),
    ("pygeoogc",    "pygeoogc",    "NLCD land-cover download"),
    ("pyogrio",     "pyogrio",     "GEOGLOWS stream network"),
    ("openpyxl",    "openpyxl",    "reading .xlsx discharge files"),
    ("fimserve",    "fimserve",    "OWP HAND-FIM mode"),
    ("arc",         "arc",         "ARC-Curve2Flood mode"),
    ("curve2flood", "curve2flood", "ARC-Curve2Flood mode"),
)

_ENV_CMD = (
    "conda env create -f environment.yml\n"
    "conda activate FIMsim"
)


def _missing(entries) -> List[Tuple[str, str, str]]:
    out = []
    for mod, pkg, why in entries:
        try:
            found = importlib.util.find_spec(mod) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            out.append((mod, pkg, why))
    return out


def check(log_fn=None) -> Tuple[List, List]:
    """Return (missing_required, missing_optional), logging a report if asked."""
    req = _missing(_REQUIRED)
    opt = _missing(_PER_FEATURE)
    if log_fn and (req or opt):
        log_fn("This environment is missing packages FIMsim needs:")
        for mod, pkg, why in req + opt:
            log_fn(f"    {mod:12s} — {why}")
        log_fn("  FIMsim must be installed from environment.yml, not package "
               "by package. From the FIMsim folder:")
        for line in _ENV_CMD.splitlines():
            log_fn(f"      {line}")
    return req, opt


def report() -> str:
    """A message for a dialog, or '' when the environment is complete."""
    req, opt = check()
    if not req and not opt:
        return ""
    lines = []
    if req:
        lines.append("FIMsim cannot run — these are missing:")
        lines += [f"  • {m} — {w}" for m, _, w in req]
    if opt:
        if req:
            lines.append("")
        lines.append("These steps will fail (the rest of FIMsim works):")
        lines += [f"  • {m} — {w}" for m, _, w in opt]
    lines += [
        "",
        "This happens when the packages were installed by hand.  FIMsim needs a",
        "matched geospatial stack (GDAL, PROJ) that only the env file gets right.",
        "From the FIMsim folder, run:",
        "",
        f"    conda env remove -n FIMsim",
        f"    {_ENV_CMD.splitlines()[0]}",
        f"    {_ENV_CMD.splitlines()[1]}",
        "    python main.py",
    ]
    return "\n".join(lines)


def cli() -> int:
    """`python -m core.deps` — check an environment without opening the GUI."""
    req, opt = check(log_fn=lambda s: print(s))
    if not req and not opt:
        print(f"Environment OK — all {len(_REQUIRED) + len(_PER_FEATURE)} "
              f"required packages present.")
        print(f"Python {sys.version.split()[0]} at {sys.executable}")
        return 0
    return 1 if req else 2


if __name__ == "__main__":
    raise SystemExit(cli())
