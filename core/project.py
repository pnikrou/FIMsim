"""Step 1 — Create project folder and initialise workflow_context.json."""
import json
import re
from pathlib import Path


def clean_name(name: str) -> str:
    name = name.strip()
    name = re.sub(r'[<>:"/\\|?*]+', "_", name)
    name = re.sub(r"\s+", "_", name)
    name = re.sub(r"_+", "_", name)
    return name.strip("._")


def create_project(base_dir: str, project_name: str,
                   subdir_name: str = "lisflood_files", log_fn=print):
    """Create project folder structure.

    Parameters
    ----------
    subdir_name : str or None
        Optional model-specific subfolder to create inside the project.
        Set to None or empty for "generic" mode (no model subfolder) — used
        by the standalone DEM / LULC / HEC-RAS modes that organise output
        per-AOI instead.

    Returns (ctx_path, ctx_dict).
    """
    base_dir = Path(base_dir).resolve()
    project_name = clean_name(project_name)
    if not project_name:
        raise ValueError("Project name is invalid after cleaning.")

    project_dir = base_dir / project_name
    project_dir.mkdir(parents=True, exist_ok=True)

    if subdir_name:
        model_dir = project_dir / subdir_name
        model_dir.mkdir(parents=True, exist_ok=True)
    else:
        model_dir = project_dir   # generic — outputs go into per-AOI subfolders

    ctx = {
        "base_dir": str(base_dir),
        "project_name": project_name,
        "project_dir": str(project_dir),
        "aoi_path": None,
        "aoi_name": None,
        "dem_path": None,
        "dem_tif_path": None,
        "lulc_path": None,
        "manning_tif_path": None,
    }
    if subdir_name == "lisflood_files":
        ctx.update({
            "lisflood_dir": str(model_dir),
            "dem_ascii_path": str(model_dir / "dem.ascii"),
            "manning_ascii_path": str(model_dir / "lulc.ascii"),
            "bci_path": str(model_dir / "BC.bci"),
            "bdy_path": str(model_dir / "BC.bdy"),
            "par_path": str(model_dir / "model.par"),
            "par_dem_name": "dem.ascii",
            "par_manningfile_name": "lulc.ascii",
        })

    ctx_path = project_dir / "workflow_context.json"
    with open(ctx_path, "w", encoding="utf-8") as f:
        json.dump(ctx, f, indent=2)

    log_fn(f"Project folder created: {project_dir}")
    if subdir_name:
        log_fn(f"Model subfolder:        {model_dir}")
    log_fn(f"Context file:           {ctx_path}")
    return ctx_path, ctx
