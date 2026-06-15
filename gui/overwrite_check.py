"""No-op overwrite check — kept as a stub so existing call sites can stay.

The app no longer prompts the user before replacing the model input
files (``dem.ascii``, ``lulc.ascii``, ``<AOI>.bci``, ``<AOI>.bdy``,
``<project>.par``).  The .par file references its companion ``.bci`` /
``.bdy`` by their actual (AOI-named) filenames, so re-running a step
simply replaces that AOI's file.  The user is intentionally re-running
the step, so we silently replace.

Intermediate GeoTIFFs (``DEM_<aoi>.tif``, ``LULC_<aoi>.tif``,
``ManningN_<aoi>.tif`` …) already auto-rename to ``name (1).tif``,
``name (2).tif``, … via ``core.export.next_free_path`` — that's the
Downloads-style behaviour the user asked for.
"""
from pathlib import Path


def confirm_overwrite(parent, files, step_name="this step"):
    """Always allow the step to proceed.  Logs which files (if any) will
    be replaced so the action stays auditable in the worker logs.

    Kept as a function so every step's existing call site keeps working
    without edits — the user just won't see a popup any more.
    """
    existing = [str(Path(f).name) for f in files if f and Path(f).exists()]
    if existing:
        # Print to stderr / stdout so it lands in the dev log if anyone
        # is running from a terminal.  The GUI log panel is updated by
        # the worker's own log lines (e.g. "DEM ASCII saved: …").
        print(
            f"[overwrite] {step_name}: silently replacing existing file(s): "
            + ", ".join(existing)
        )
    return True
