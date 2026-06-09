"""HEC-RAS project file generation and execution helpers.

Provides three public functions:

* ``build_hecras_project`` — writes .prj / .u01 / .p01 files from a
  discharge CSV and geometry summary produced by ``build_hecras_geometry``.
* ``run_hecras`` — calls RasUnsteady64.exe (Windows only).
* ``read_hecras_results`` — reads max depth / velocity from the results HDF.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional


# ── Project / input-file writer ───────────────────────────────────────────────

def build_hecras_project(
    output_dir: str,
    project_name: str,
    geom_summary: dict,        # from build_hecras_geometry
    discharge_csv: str,        # path to discharge CSV
    simulation_start: str,     # e.g. "01JAN2026 00:00:00"
    simulation_end: str,       # e.g. "31MAY2026 23:00:00"
    time_step_sec: float = 60.0,
    downstream_slope: float = 0.001,
    log_fn=print,
) -> dict:
    """Write HEC-RAS project, unsteady-flow and plan files.

    Parameters
    ----------
    output_dir:        Directory to write files into.
    project_name:      Base name used for all three files.
    geom_summary:      Dict returned by ``build_hecras_geometry``.
    discharge_csv:     Path to a CSV with time and discharge columns.
    simulation_start:  Start datetime string (HEC-RAS format).
    simulation_end:    End datetime string (HEC-RAS format).
    time_step_sec:     Computational time-step in seconds.
    downstream_slope:  Normal-depth slope for the downstream BC.
    log_fn:            Callable for log messages.

    Returns
    -------
    dict with keys: prj_path, u01_path, p01_path.
    """
    import pandas as pd

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # ── 1. Read discharge CSV ─────────────────────────────────────────────────
    log_fn(f"  Reading discharge CSV: {discharge_csv}")
    df = pd.read_csv(discharge_csv)

    if "discharge_cms" not in df.columns:
        for col in df.columns:
            if (
                "discharge" in col.lower()
                or "flow" in col.lower()
                or col.lower() == "q"
            ):
                df = df.rename(columns={col: "discharge_cms"})
                break

    if "discharge_cms" not in df.columns:
        raise ValueError(
            f"Could not find a discharge column in {discharge_csv}. "
            f"Columns present: {list(df.columns)}"
        )

    flows = df["discharge_cms"].values
    n_vals = len(flows)
    flow_str = " ".join(f"{q:.3f}" for q in flows)

    # ── 2. Write .prj ─────────────────────────────────────────────────────────
    prj_path = out / f"{project_name}.prj"
    prj_path.write_text(
        f"Proj Title={project_name}\n"
        f"Current Plan=p01\n"
        f"Plan File=p01\n"
        f"Unsteady File=u01\n"
        f"Geom File=g01\n"
        f"English Units=False\n"
    )
    log_fn(f"  Written: {prj_path}")

    # ── 3. Write .u01 (unsteady flow) ─────────────────────────────────────────
    u01_path = out / f"{project_name}.u01"
    u01_path.write_text(
        f"Flow Title={project_name}\n"
        f"Program Version=6.5\n"
        f"Number of Profiles= 1\n"
        f"Profile Names=Flow\n"
        f"Boundary Location=                ,                ,                ,                ,  \n"
        f"Flow Hydrograph= {n_vals}\n"
        f"{flow_str}\n"
        f"Downstream=Normal Depth={downstream_slope:.6f}\n"
    )
    log_fn(f"  Written: {u01_path}")

    # ── 4. Write .p01 (plan) ──────────────────────────────────────────────────
    p01_path = out / f"{project_name}.p01"
    p01_path.write_text(
        f"Plan Title={project_name}\n"
        f"Program Version=6.5\n"
        f"Short Identifier=Plan01\n"
        f"Simulation Date={simulation_start},{simulation_end}\n"
        f"Computation Interval={time_step_sec}\n"
        f"Output Interval=3600\n"
        f"Geometry File=g01\n"
        f"Flow File=u01\n"
        f"Plan File=p01\n"
        f"Run HTab= -1 \n"
        f"Run UNet= -1 \n"
        f"Run Sediment= 0\n"
        f"Run WQ= 0\n"
    )
    log_fn(f"  Written: {p01_path}")

    return {
        "prj_path": str(prj_path),
        "u01_path": str(u01_path),
        "p01_path": str(p01_path),
    }


# ── HEC-RAS executor ──────────────────────────────────────────────────────────

def run_hecras(
    hecras_exe: str,    # path to RasUnsteady64.exe
    project_prj: str,   # path to .prj file
    log_fn=print,
) -> dict:
    """Run HEC-RAS (RasUnsteady64.exe) on a project file.

    Parameters
    ----------
    hecras_exe:   Full path to ``RasUnsteady64.exe``.
    project_prj:  Full path to the ``.prj`` project file.
    log_fn:       Callable for log messages.

    Returns
    -------
    dict with keys: success (bool), elapsed_s (float), stdout (str).

    Raises
    ------
    RuntimeError if HEC-RAS exits with a non-zero return code.
    """
    import subprocess
    import time

    log_fn(f"Running HEC-RAS: {hecras_exe}")
    log_fn(f"Project: {project_prj}")
    t0 = time.time()
    result = subprocess.run(
        [hecras_exe, project_prj, "-silent"],
        capture_output=True,
        text=True,
        timeout=3600,
    )
    elapsed = time.time() - t0
    if result.returncode == 0:
        log_fn(f"HEC-RAS completed in {elapsed:.1f}s")
        return {"success": True, "elapsed_s": elapsed, "stdout": result.stdout}
    else:
        raise RuntimeError(
            f"HEC-RAS failed (code {result.returncode}):\n{result.stderr}"
        )


# ── Results reader ────────────────────────────────────────────────────────────

def read_hecras_results(results_hdf: str, area_name: str = "Domain") -> dict:
    """Read max depth and velocity from a HEC-RAS results HDF file.

    Parameters
    ----------
    results_hdf:  Path to the HEC-RAS results HDF (``*.p01.hdf``).
    area_name:    Name of the 2-D flow area (must match geometry).

    Returns
    -------
    dict with keys: max_depth (ndarray, shape (N,)), max_velocity (ndarray).
    """
    import h5py
    import numpy as np

    with h5py.File(results_hdf, "r") as f:
        base = f[
            "Results/Unsteady/Output/Output Blocks/"
            "Base Output/Unsteady Time Series"
        ]
        area = base[f"2D Flow Areas/{area_name}"]
        depth = np.array(area["Depth"])           # (T, N) time × cells
        max_depth = depth.max(axis=0)              # (N,)
        vel_ds = area.get("Velocity")
        if vel_ds is not None:
            vel = np.array(vel_ds)
        else:
            vel = np.zeros_like(depth)
        max_vel = vel.max(axis=0)

    return {"max_depth": max_depth, "max_velocity": max_vel}
