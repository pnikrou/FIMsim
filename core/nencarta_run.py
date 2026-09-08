"""Drive ARC + Curve2Flood through NenCarta — the authors' own orchestrator.

FIMsim does NOT call ARC or Curve2Flood itself.  NenCarta
(https://github.com/jlgutenson/nencarta) is what the tool's authors built to
run them, so FIMsim's job is only to write the watershed JSON NenCarta expects
and invoke its ``flood-mapping`` CLI:

    flood-mapping json <file> --serial

The JSON is ``{"watersheds": [ {...}, ... ]}`` with one entry per AOI.  Four
keys are required (nencarta/main.py :: verify_required_keys)::

    name, flowline, dem_dir, output_dir

Everything else is optional and NenCarta supplies its own defaults; the keys
below mirror those defaults exactly rather than inventing any, so a FIMsim run
and a hand-written NenCarta run behave identically.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

# NenCarta accepts GEOGLOWS or an NWM forecast range.  get_streamids_from_source
# matches any name starting "NWM", but validate_forecast_hours only knows these
# three, and each constrains the forecast hour differently.
STREAMFLOW_SOURCES = ("GEOGLOWS", "NWM_short_range",
                      "NWM_medium_range", "NWM_long_range")

# nencarta/main.py :: validate_forecast_hours
FORECAST_HOURS = {
    "NWM_short_range":  [f"{i:02d}" for i in range(24)],
    "NWM_medium_range": ["00", "06", "12", "18"],
    "NWM_long_range":   ["00"],
}

# The reach-id / downstream-id fields NenCarta reads off the flowline for each
# source.  GEOGLOWS keys on its own TDX-Hydro network, NWM on NHD COMIDs.
STREAM_ID_FIELDS = {
    "GEOGLOWS": ("LINKNO", "DSLINKNO"),
    "NWM": ("COMID", "TOCOMID"),
}

# nencarta/main.py :: CURVE2FLOOD_MAPPERS / ALL_MAPPERS
CURVE2FLOOD_MAPPERS = [
    "Curve2Flood-Kernel Weighted",
    "Curve2Flood-FLDPLNpy",
    "Curve2Flood-Multi-Point Interpolation",
]
ALL_MAPPERS = ["FloodSpreader"] + CURVE2FLOOD_MAPPERS

FLOODMAP_MODES = ("forecast", "user")

# NenCarta reads several of these with bathy_args["key"] — direct indexing, not
# .get() — so an empty dict dies with KeyError: 'X_Section_Dist' part-way into
# the run.  These are NenCarta's OWN defaults, copied verbatim from the values
# its GUI ships with (nencarta/gui_app.py), so a FIMsim run matches what a user
# of their GUI would get.  Nothing here is invented.
DEFAULT_BATHY_ARGS = {
    "VDT_Database_NumIterations": 30,
    "Make_Output_GPKG": "True",
    "FS_ADJUST_FLOW_BY_FRACTION": 1.0,
    "TW_MultFact": 1.5,
    "TopWidthPlausibleLimit": 2000,
    "Bathy_Trap_H": 0.2,
    "X_Section_Dist": 5000.0,
    "Degree_Manip": 6.1,
    "Degree_Interval": 1.5,
    "Low_Spot_Range": 2,
    "Str_Limit_Val": 1,
    "Gen_Dir_Dist": 10,
    "Gen_Slope_Dist": 10,
    "Stream_Slope_Method": "local_average_corrected",
}

DEFAULT_FLOODMAP_ARGS = {
    "Make_Output_GPKG": "True",
    "FS_ADJUST_FLOW_BY_FRACTION": 1.0,
    "TW_MultFact": 1.5,
    "TopWidthPlausibleLimit": 6000,
}


class NenCartaError(RuntimeError):
    """NenCarta could not be run, or reported a failure."""


def _as_yyyymmdd(value) -> str:
    """NenCarta's forensic_forecast_date format, from anything date-like."""
    import datetime as _dt
    text = str(value).strip()
    if isinstance(value, (_dt.date, _dt.datetime)):
        return value.strftime("%Y%m%d")
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M"):
        try:
            return _dt.datetime.strptime(text, fmt).strftime("%Y%m%d")
        except ValueError:
            continue
    raise NenCartaError(
        f"Could not read {value!r} as a date — NenCarta wants YYYYMMDD.")


def flood_mapping_cli() -> Optional[str]:
    """Absolute path to the ``flood-mapping`` CLI, or None when missing.

    It is installed beside the interpreter running FIMsim, which is not
    necessarily on PATH when the app is launched from a desktop shortcut.
    """
    beside = Path(sys.executable).parent / "flood-mapping"
    if beside.exists():
        return str(beside)
    return shutil.which("flood-mapping")


def build_watershed(
    *,
    name: str,
    flowline: str,
    dem_dir: str,
    output_dir: str,
    streamflow_source: str = "GEOGLOWS",
    geoglows_vpu=None,
    nwm_api_key: Optional[str] = None,
    forensic_forecast_date: Optional[str] = None,
    forensic_forecast_hour=None,
    age_of_forecast_days: int = 7,
    mapper: str = "Curve2Flood-Kernel Weighted",
    floodmap_mode: str = "forecast",
    user_flow_files: Optional[List[str]] = None,
    mannings_text_file: Optional[str] = None,
    process_stream_network: bool = True,
    bathy_args: Optional[Dict] = None,
    floodmap_args: Optional[Dict] = None,
    specify_depths_for_bathy_mask: Optional[List[float]] = None,
    dem_filter: str = "*",
    clean_dem: bool = False,
    bathy_use_banks: bool = False,
    find_banks_based_on_landcover: bool = True,
    make_depth_maps: bool = True,
    make_velocity_maps: bool = True,
    make_wse_maps: bool = True,
    make_curvefile: bool = True,
    overwrite_floodmaps: bool = True,
    quiet: bool = False,
    extra: Optional[Dict] = None,
) -> Dict:
    """One ``watersheds[]`` entry, validated the way NenCarta validates it.

    Only keys NenCarta actually reads are emitted.  Optional values left as
    None are omitted entirely so NenCarta applies its own default rather than
    being handed a null it did not expect.
    """
    src = streamflow_source or "GEOGLOWS"
    # Match NenCarta's own casing: it compares GEOGLOWS upper-cased but the NWM
    # names carry their lowercase range suffix.
    src = "GEOGLOWS" if src.upper() == "GEOGLOWS" else src
    if src not in STREAMFLOW_SOURCES:
        raise NenCartaError(
            f"streamflow_source must be one of {', '.join(STREAMFLOW_SOURCES)} "
            f"— got {streamflow_source!r}.")
    if src.upper().startswith("NWM") and not nwm_api_key:
        raise NenCartaError(
            "NenCarta requires nwm_api_key when streamflow_source is NWM "
            "(apply for one through CIROH).")
    if mapper not in ALL_MAPPERS:
        raise NenCartaError(
            f"mapper must be one of {ALL_MAPPERS} — got {mapper!r}.")
    if floodmap_mode not in FLOODMAP_MODES:
        raise NenCartaError(
            f"floodmap_mode must be 'forecast' or 'user' — got {floodmap_mode!r}.")
    if floodmap_mode == "user" and not user_flow_files:
        raise NenCartaError(
            "floodmap_mode 'user' requires user_flow_files.")

    for label, p in (("flowline", flowline), ("dem_dir", dem_dir)):
        if not p or not Path(p).exists():
            raise NenCartaError(f"{label} does not exist: {p}")

    w: Dict = {
        # required
        "name": name,
        "flowline": os.path.normpath(str(flowline)),
        "dem_dir": os.path.normpath(str(dem_dir)),
        "output_dir": os.path.normpath(str(output_dir)),
        # streamflow
        "streamflow_source": src,
        "age_of_forecast_days": int(age_of_forecast_days),
        # mapping
        "mapper": mapper,
        "floodmap_mode": floodmap_mode,
        "dem_filter": dem_filter or "*",
        "clean_dem": bool(clean_dem),
        "bathy_use_banks": bool(bathy_use_banks),
        "find_banks_based_on_landcover": bool(find_banks_based_on_landcover),
        "make_depth_maps": bool(make_depth_maps),
        "make_velocity_maps": bool(make_velocity_maps),
        "make_wse_maps": bool(make_wse_maps),
        # NenCarta defaults this to False, which SKIPS building
        # <output>/<name>/STRM/..._StrmShp.gpkg from the supplied flowline and
        # then reads it anyway — fine when a previous run made it, fatal on a
        # first run with "DataSourceError: ... No such file or directory".
        "process_stream_network": bool(process_stream_network),
        # Start from NenCarta's defaults so every directly-indexed key exists,
        # then let the caller override individual entries.
        "bathy_args": {**DEFAULT_BATHY_ARGS, **(bathy_args or {})},
        "floodmap_args": {**DEFAULT_FLOODMAP_ARGS, **(floodmap_args or {})},
        "make_curvefile": bool(make_curvefile),
        "overwrite_floodmaps": bool(overwrite_floodmaps),
        "quiet": bool(quiet),
    }
    if src == "GEOGLOWS" and geoglows_vpu not in (None, ""):
        w["geoglows_vpu"] = int(geoglows_vpu)
    if src.upper().startswith("NWM"):
        w["nwm_api_key"] = nwm_api_key
    if forensic_forecast_date:
        # NenCarta parses this as YYYYMMDD (or "%Y-%m-%d %H:%M:%S %Z") and
        # raises on anything else — and it does so at RUN time, long after the
        # config looks fine, so normalise here.
        w["forensic_forecast_date"] = _as_yyyymmdd(forensic_forecast_date)
        # GEOGLOWS is daily — NenCarta ignores the hour for it.
        if forensic_forecast_hour not in (None, "") and src != "GEOGLOWS":
            # It must be a TWO-DIGIT STRING, and each NWM range allows a
            # different set (validate_forecast_hours).
            hour = f"{int(forensic_forecast_hour):02d}"
            allowed = FORECAST_HOURS.get(src)
            if allowed and hour not in allowed:
                raise NenCartaError(
                    f"forecast hour {hour} is not valid for {src} — "
                    f"allowed: {', '.join(allowed)}.")
            w["forensic_forecast_hour"] = hour

    # NenCarta defaults use_specified_depth_for_bathy_mask to True and then
    # REQUIRES specify_depths_for_bathy_mask — one float when clean_dem is
    # False, two when it is True — so its own defaults cannot run.  State the
    # choice explicitly rather than tripping that at run time.
    if specify_depths_for_bathy_mask:
        depths = [float(d) for d in specify_depths_for_bathy_mask]
        want = 2 if clean_dem else 1
        if len(depths) != want:
            raise NenCartaError(
                f"specify_depths_for_bathy_mask needs exactly {want} value(s) "
                f"when clean_dem is {bool(clean_dem)} — got {len(depths)}.")
        w["use_specified_depth_for_bathy_mask"] = True
        w["specify_depths_for_bathy_mask"] = depths
    else:
        w["use_specified_depth_for_bathy_mask"] = False
    # Always emit user_flow_files, even empty.  NenCarta 0.2.1's
    # validate_user_floodmaps() ends with
    #     return floodmap_mode, [os.path.normpath(f) for f in user_flow_files]
    # without guarding None, so a plain forecast run that omits the key dies
    # with "TypeError: 'NoneType' object is not iterable" before any work
    # starts.  An empty list is a valid value and takes the same path.
    w["user_flow_files"] = [os.path.normpath(str(f)) for f in (user_flow_files or [])]
    if mannings_text_file:
        w["mannings_text_file"] = os.path.normpath(str(mannings_text_file))
    if extra:
        w.update(extra)
    return w


def write_nencarta_json(json_path, watersheds: List[Dict], log_fn=print) -> str:
    """Write ``{"watersheds": [...]}`` and return the path."""
    if not watersheds:
        raise NenCartaError("No watersheds to write.")
    p = Path(json_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump({"watersheds": watersheds}, fh, indent=2)
    log_fn(f"Wrote NenCarta input: {p}  ({len(watersheds)} watershed(s))")
    return str(p)


_VALIDATE_SNIPPET = """
import json, sys
from nencarta.main import (verify_required_keys, validate_user_floodmaps,
                           normalize_mapper_name)
ws = json.load(open(sys.argv[1]))["watersheds"]
for w in ws:
    verify_required_keys(w)
    validate_user_floodmaps(w)
    normalize_mapper_name(w.get("mapper"))
print("OK %d" % len(ws))
"""


def validate_with_nencarta(watersheds: List[Dict], log_fn=print) -> None:
    """Run NenCarta's OWN validators over the entries before launching.

    Catches a bad JSON in milliseconds instead of after the CLI has started
    processing.

    This runs in a SUBPROCESS on purpose.  ``import nencarta.main`` pulls in
    PyQt5 (via its gui_app), and FIMsim is PyQt6 — loading both into one
    process is what makes Qt abort with "python quit unexpectedly".  A failure
    to validate is never fatal here; the CLI run itself remains the real gate.
    """
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump({"watersheds": watersheds}, fh)
        tmp = fh.name
    env = dict(os.environ)
    env.pop("QT_API", None)
    try:
        r = subprocess.run([sys.executable, "-c", _VALIDATE_SNIPPET, tmp],
                           capture_output=True, text=True, timeout=120, env=env)
    except Exception as exc:                       # pragma: no cover
        log_fn(f"(Skipping NenCarta pre-validation — {exc})")
        return
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    if r.returncode == 0:
        log_fn(f"NenCarta accepted all {len(watersheds)} watershed entry(s).")
        return
    detail = (r.stderr or r.stdout or "").strip().splitlines()
    raise NenCartaError(
        "NenCarta rejected the watershed configuration: "
        + (detail[-1] if detail else "unknown error"))


def run_flood_mapping(json_path, serial: bool = True, num_workers=None,
                      log_fn=print, cwd=None) -> int:
    """Run ``flood-mapping json <file>`` and stream its output to the log.

    Returns the exit code; raises NenCartaError when the CLI is missing or the
    run fails, so a failure cannot be reported as success.
    """
    cli = flood_mapping_cli()
    if not cli:
        raise NenCartaError(
            "The 'flood-mapping' command was not found.  Install NenCarta:\n"
            "  pip install git+https://github.com/jlgutenson/nencarta.git")

    cmd = [cli, "json", str(json_path), "--serial" if serial else "--parallel"]
    if not serial and num_workers:
        cmd += ["--num_workers", str(int(num_workers))]
    log_fn(f"Running NenCarta: {' '.join(cmd)}")

    env = dict(os.environ)
    # NenCarta imports PyQt5 at module load; FIMsim is PyQt6.  Running it as a
    # separate process keeps them apart, and this makes matplotlib inside that
    # process bind to PyQt5 rather than fighting FIMsim's binding.
    env.pop("QT_API", None)
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, cwd=str(cwd) if cwd else None, env=env)
    except OSError as exc:
        raise NenCartaError(f"Could not start flood-mapping: {exc}") from exc

    for line in proc.stdout:
        line = line.rstrip()
        if line:
            log_fn(f"  [nencarta] {line}")
    code = proc.wait()
    if code != 0:
        raise NenCartaError(
            f"NenCarta exited with code {code} — see the [nencarta] lines above.")
    log_fn("NenCarta finished successfully.")
    return code


def find_flood_maps(output_dir, watershed_name: str) -> List[str]:
    """Flood rasters NenCarta produced for one watershed."""
    base = Path(output_dir) / watershed_name
    if not base.is_dir():
        base = Path(output_dir)
    hits = []
    for pat in ("**/*FloodMap*.tif", "**/*flood*.tif", "**/*Flood*.tif"):
        hits += [str(p) for p in base.glob(pat)]
    return sorted(set(hits))
