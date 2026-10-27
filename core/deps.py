"""Startup check that the environment actually has what FIMsim needs.

FIMsim is installed in two steps, and the reason is worth knowing.

``conda env create -f environment.yml`` builds the core: the geospatial stack,
the NWM/S3 libraries and the Qt GUI.  That is everything LISFLOOD-FP and TRITON
need.  The two optional backends - OWP HAND-FIM (``fimserve``) and
ARC-Curve2Flood (``arc``/``curve2flood``/``nencarta``) - come from
``requirements-pip.txt`` and are installed with ``--no-deps``:

    python -m core.deps --fix

That cannot be one resolver run.  ``fimserve`` hard-requires ``teehr==0.5.0``,
and TEEHR pins ``zarr<3``, ``s3fs<2026`` and ``fsspec<2026``, while the S3
stack FIMsim runs on is ``zarr 3`` / ``s3fs 2026``.  pip cannot satisfy both,
so a plain ``pip install fimserve`` either deadlocks (a user watched it
backtrack through fimserve 0.1.87 → 0.1.6 pulling a 450 MB pyspark source
tarball, then fail) or, worse, "succeeds" by downgrading zarr and s3fs and
breaks every NWM download.  TEEHR is only used for its NWM fetch helpers, which
work fine on the newer libraries; ``--no-deps`` installs it as it is run here.

The checks below run at startup so a half-built environment is reported once,
up front, with the command that repairs it - not after an AOI, a date range and
309 reaches have been chosen.
"""
from __future__ import annotations

import importlib.metadata as _md
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

_ROOT = Path(__file__).resolve().parent.parent
_PIP_REQS = _ROOT / "requirements-pip.txt"

# Each entry: (import name, package, what stops working without it)

# Needed for the app to be usable at all.
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

# Needed by a specific step.  The app opens without them but that step fails.
_PER_FEATURE: Tuple[Tuple[str, str, str], ...] = (
    ("zarr",        "zarr",        "NWM retrospective discharge (Flow Data, BDY, TRITON hydro)"),
    ("s3fs",        "s3fs",        "NWM retrospective discharge (Flow Data, BDY, TRITON hydro)"),
    ("xarray",      "xarray",      "NWM discharge and ARC flow series"),
    ("netCDF4",     "netcdf4",     "NWM short-range forecast discharge"),
    ("pynhd",       "pynhd",       "NHD flowlines, HUC lookup and USGS gages"),
    ("pygeoogc",    "pygeoogc",    "NLCD land-cover download"),
    ("pyogrio",     "pyogrio",     "GEOGLOWS stream network"),
    ("openpyxl",    "openpyxl",    "reading .xlsx discharge files"),
    ("botocore",    "botocore",    "anonymous S3 access"),
    ("aiobotocore", "aiobotocore", "anonymous S3 access"),
    # ── step 2 (requirements-pip.txt): the two optional backends ──────────────
    ("fimserve",    "fimserve",    "OWP HAND-FIM mode"),
    ("teehr",       "teehr",       "OWP HAND-FIM mode (NWM fetch helpers)"),
    ("fimeval",     "fimeval",     "OWP HAND-FIM mode"),
    ("geocube",     "geocube",     "OWP HAND-FIM mode (flood-map mosaic step)"),
    ("arc",         "arc",         "ARC-Curve2Flood mode"),
    ("curve2flood", "curve2flood", "ARC-Curve2Flood mode"),
    ("nencarta",    "nencarta",    "ARC-Curve2Flood mode"),
    ("geoglows",    "geoglows",    "ARC-Curve2Flood mode"),
)

# Modules that arrive with step 2.  If ONLY these are missing, the repair is
# `python -m core.deps --fix`; if anything else is missing, recreate the env.
_STEP2 = frozenset({"fimserve", "teehr", "fimeval", "geocube", "arc",
                    "curve2flood", "nencarta", "geoglows", "polars", "rich"})

# What to really import for the optional backends.  find_spec only proves a
# package is on disk: fimserve was "present" in an environment where
# `import fimserve` died on a missing psutil, deep inside teehr, and the app
# then reported the backend as not installed.  (module to import, key, why)
_DEEP: Tuple[Tuple[str, str, str], ...] = (
    ("fimserve",                                  "fimserve",    "OWP HAND-FIM mode"),
    ("teehr.fetching.nwm.retrospective_points",   "teehr",       "OWP HAND-FIM mode (NWM fetch helpers)"),
    ("fimeval",                                   "fimeval",     "OWP HAND-FIM mode"),
    ("geocube.api.core",                          "geocube",     "OWP HAND-FIM mode (flood-map mosaic step)"),
    ("arc",                                       "arc",         "ARC-Curve2Flood mode"),
    ("curve2flood",                               "curve2flood", "ARC-Curve2Flood mode"),
    ("nencarta.main",                             "nencarta",    "ARC-Curve2Flood mode"),
    ("geoglows",                                  "geoglows",    "ARC-Curve2Flood mode"),
)

# Things that import without error and are still broken.  polars prints a
# warning ("Polars binary is missing!") and carries on until the first
# DataFrame is built, which is deep inside an ARC run.  (key, code, why)
_SMOKE: Tuple[Tuple[str, str, str], ...] = (
    ("polars", "import polars as pl; pl.DataFrame({'a': [1]})",
     "ARC-Curve2Flood mode (polars compiled core)"),
    ("rich",   "import rich.progress",
     "ARC-Curve2Flood mode (NenCarta progress display)"),
)

# Command-line tools FIMsim shells out to.  find_spec cannot see these.
#   aws           - fimserve's DownloadHUC8 runs `aws s3 sync --no-sign-request`
#   flood-mapping - the NenCarta CLI that the ARC-Curve2Flood mode drives
_BINARIES: Tuple[Tuple[str, str, str], ...] = (
    ("aws",           "awscli",   "OWP HAND-FIM mode (downloads the HUC8 HAND data)"),
    ("flood-mapping", "nencarta", "ARC-Curve2Flood mode"),
)
_BINARIES_STEP2 = frozenset({"flood-mapping"})

# The anonymous-S3 libraries must agree about which botocore they use.  When
# they do not, every S3 read dies with
#   TypeError: ClientArgsCreator.compute_endpoint_resolver_builtin_defaults()
#   missing 1 required positional argument: 's3_disable_express_session_auth'
# and HAND-FIM then generates a flood map with no discharge in it.
_S3_STACK = ("aiobotocore", "s3fs", "boto3", "awscli", "s3transfer")

# Appended to every "package X is missing" error in the app.  The old messages
# said "pip install zarr s3fs" / "pip install fimserve", which is how a working
# install gets broken (see the module docstring).
FIX_HINT = (
    "This environment is incomplete. Run  python -m core.deps  to see what is "
    "missing and the exact command that repairs it. Do not install packages "
    "one by one - that breaks FIMsim's pinned S3 stack."
)

# Measured 2026-10-27 on one small case (HWM_42): with numba's compile cache
# empty, ARC-Curve2Flood's first run gives different bathymetry than every later
# run (590 of 54,780 cells of FS_Bathy; 1,820 cells over the four affected
# rasters), and every later run matches the project's original results to the
# last cell.  Identical in the long-lived working environment and in a fresh
# one, so it is ARC's behaviour, not an install fault.  Which result is
# numerically right is not known.
ARC_COLD_CACHE_NOTE = (
    "  ARC's first run in a new environment gives slightly different bathymetry\n"
    "  (and flood maps) than every later run, because numba's compile cache is\n"
    "  empty. Run one throwaway ARC case before producing results you keep.\n"
    "  The same happens after reinstalling arc/curve2flood/nencarta."
)

_ENV_CMD = (
    "conda env create -f environment.yml\n"
    "conda activate FIMsim"
)


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _has_binary(name: str) -> bool:
    if shutil.which(name):
        return True
    # A GUI started from outside the activated env may not have its bin dir on
    # PATH even though the tool is installed right next to the interpreter.
    here = Path(sys.executable).resolve().parent
    return any((d / (name + ext)).exists()
               for d in (here, here / "Scripts") for ext in ("", ".exe", ".bat"))


def _missing(entries) -> List[Tuple[str, str, str]]:
    return [(m, p, w) for m, p, w in entries if not _has_module(m)]


def _missing_binaries() -> List[Tuple[str, str, str]]:
    return [(b, p, w) for b, p, w in _BINARIES if not _has_binary(b)]


def s3_stack_problems(versions=None, requires=None) -> List[str]:
    """Return one line per violated requirement inside the S3 library group.

    Only these packages are compared against each other.  TEEHR/fimserve are
    deliberately NOT checked: their declared pins contradict the S3 stack and
    are knowingly overridden (see the module docstring), so including them
    would report a problem that is not one.

    ``versions`` / ``requires`` exist so the logic can be tested without
    installing anything: {dist: version} and {dist: [requirement strings]}.
    """
    try:
        from packaging.requirements import Requirement
    except ImportError:                      # packaging ships with pip/setuptools
        return []
    if versions is None:
        versions = {}
        for d in _S3_STACK + ("botocore", "fsspec"):
            try:
                versions[d] = _md.version(d)
            except _md.PackageNotFoundError:
                pass
    if requires is None:
        requires = {}
        for d in _S3_STACK:
            try:
                requires[d] = _md.requires(d) or []
            except _md.PackageNotFoundError:
                pass
    problems = []
    for dist, reqs in requires.items():
        for line in reqs:
            try:
                r = Requirement(line)
            except Exception:
                continue
            if r.marker is not None and r.extras:
                continue                     # optional extra
            if r.marker is not None and not r.marker.evaluate({"extra": ""}):
                continue                     # not for this platform
            have = versions.get(r.name.lower())
            if have is None or not r.specifier:
                continue
            if not r.specifier.contains(have, prereleases=True):
                problems.append(f"{dist} {versions.get(dist, '?')} needs "
                                f"{r.name}{r.specifier}, but {have} is installed")
    return problems


def _broken_imports(already_missing) -> List[Tuple[str, str, str]]:
    """Backends that are on disk but fail to import, with the real error."""
    gone = {m for m, _, _ in already_missing}
    out = []
    for modpath, key, why in _DEEP:
        if key in gone:
            continue
        try:
            importlib.import_module(modpath)
        except BaseException as exc:             # a broken import can raise anything
            if isinstance(exc, KeyboardInterrupt):
                raise
            out.append((key, key, f"{why} - installed but does not import "
                                  f"({type(exc).__name__}: {str(exc)[:100]})"))
    return out


def _failed_smoke() -> List[Tuple[str, str, str]]:
    import warnings
    out = []
    for key, code, why in _SMOKE:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error")          # a warning IS the symptom
                exec(code, {})
        except BaseException as exc:
            if isinstance(exc, KeyboardInterrupt):
                raise
            out.append((key, key, f"{why} - installed but broken "
                                  f"({type(exc).__name__}: {str(exc)[:80]})"))
    return out


def check(log_fn=None, deep: bool = False):
    """Return (missing_required, missing_optional, s3_problems).

    ``deep`` also imports the optional backends (slow: fimserve alone takes
    several seconds), which is what `python -m core.deps` does.  Startup uses
    the fast check; the FIMserv tab imports fimserve for real.
    """
    req = _missing(_REQUIRED)
    opt = _missing(_PER_FEATURE) + _missing_binaries()
    if deep:
        opt = opt + _broken_imports(opt) + _failed_smoke()
    s3 = s3_stack_problems()
    if log_fn and (req or opt or s3):
        log_fn("This environment has problems FIMsim cares about:")
        for mod, pkg, why in req + opt:
            log_fn(f"    missing  {mod:13s} - {why}")
        for line in s3:
            log_fn(f"    S3 mismatch - {line}")
        log_fn("  Run  python -m core.deps  for the repair command.")
    return req, opt, s3


def _only_step2(items) -> bool:
    return all(m in _STEP2 or m in _BINARIES_STEP2 for m, _, _ in items)


def repair_steps(req, opt, s3) -> List[str]:
    """The commands that fix what check() found, in order."""
    if req or s3 or not _only_step2(opt):
        return ["conda env remove -n FIMsim",
                *_ENV_CMD.splitlines(),
                "python -m core.deps --fix"]
    return ["python -m core.deps --fix"]


def report(deep: bool = False) -> str:
    """A message for a dialog, or '' when the environment is complete."""
    req, opt, s3 = check(deep=deep)
    if not req and not opt and not s3:
        return ""
    lines = []
    if req:
        lines.append("FIMsim cannot run - these are missing:")
        lines += [f"  - {m} - {w}" for m, _, w in req]
    if opt:
        if req:
            lines.append("")
        lines.append("These steps will fail (the rest of FIMsim works):")
        lines += [f"  - {m} - {w}" for m, _, w in opt]
    if s3:
        if req or opt:
            lines.append("")
        lines.append("The S3 libraries disagree, so NWM downloads will fail:")
        lines += [f"  - {x}" for x in s3]
    lines += ["", "To repair, run these from the FIMsim folder:", ""]
    lines += [f"    {c}" for c in repair_steps(req, opt, s3)]
    return "\n".join(lines)


def fix() -> int:
    """Install requirements-pip.txt (the HAND-FIM and ARC backends).

    ``--no-deps`` is the whole point - see the module docstring.  Every package
    in that file is pinned, so pip installs exactly those versions and
    resolves nothing.  Safe to run again: already-satisfied pins are skipped.
    """
    if not _PIP_REQS.exists():
        print(f"Cannot find {_PIP_REQS}.  Run this from a full FIMsim checkout.")
        return 1
    cmd = [sys.executable, "-m", "pip", "install", "--no-deps",
           "--disable-pip-version-check", "-r", str(_PIP_REQS)]
    print("Installing the HAND-FIM and ARC-Curve2Flood backends:")
    print("  " + " ".join(cmd) + "\n")
    rc = subprocess.call(cmd)
    if rc != 0:
        print("\npip failed - see the messages above.  Nothing is resolved, so "
              "a failure here stops at the first package that cannot be "
              "installed; fix that one (for the GitHub packages check that git "
              "is on PATH) and run  python -m core.deps --fix  again.")
        return rc
    print("\nDone.  Re-checking the environment ...\n")
    rc = cli(show_header=False)
    if rc == 0:
        print("\nBefore your first ARC-Curve2Flood run:\n" + ARC_COLD_CACHE_NOTE)
    return rc


def cli(show_header: bool = True) -> int:
    """`python -m core.deps [--fix]` - check (and optionally repair) an env."""
    if show_header:
        print(f"Python {sys.version.split()[0]} at {sys.executable}\n")
    print("Checking (this imports fimserve and the ARC backend, so it takes "
          "a few seconds) ...\n")
    req, opt, s3 = check(deep=True)
    if not req and not opt and not s3:
        print(f"Environment OK - all {len(_REQUIRED) + len(_PER_FEATURE)} "
              f"packages and {len(_BINARIES)} tools present and importable, "
              f"S3 libraries consistent.")
        return 0
    print(report(deep=True))
    return 1 if (req or s3) else 2


if __name__ == "__main__":
    if "--fix" in sys.argv[1:]:
        raise SystemExit(fix())
    raise SystemExit(cli())
