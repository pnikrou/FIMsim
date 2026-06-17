"""Per-AOI orchestrators for the TRITON workflow (standalone from LISFLOOD).

Kept separate from core/orchestrate.py so the two models never share
workflow code.  Blocking — call from a Worker thread.
"""
import json
import shutil
from pathlib import Path
from typing import List, Dict, Optional

import geopandas as gpd

from core.aoi import load_aoi
from core.dem import prepare_dem
from core.export import export_raster, raster_to_shapefile, gdf_to_shapefile, next_free_path
from core.multi_aoi import (
    AOIFeatureInfo, get_single_feature_gdf, model_files_subdir,
)
from core.nlcd import (
    download_nlcd, create_manning_from_lulc,
    NLCD_MANNING, SENTINEL2_MANNING,
)



# -- TRITON: multi-AOI DEM step ------------------------------------------------
def run_triton_dem_all(
    ctx_path: str,
    ctx: dict,
    dem_res_m: float,
    has_dem: bool = False,
    user_dem_path=None,
    per_aoi_configs: list = None,
    log_fn=print,
) -> dict:
    """Run prepare_dem for every confirmed AOI in LISFLOOD-FP / TRITON.

    When ``per_aoi_configs`` is provided, each AOI uses its own
    ``has_dem`` + ``user_dem_path`` from that list (length must match
    ``ctx['aoi_features']``).  Otherwise the global ``has_dem`` /
    ``user_dem_path`` are applied to every AOI.

    Each AOI gets its own DEM GeoTIFF + ASCII inside its own subfolder.
    Emits ``▶ Downloading DEM [N/M]: 'name'`` and ``✓ DEM [N/M] finished``
    log lines so the GUI can show per-AOI progress, info, and a map.

    After processing, the parent ctx's single-AOI bridge keys are rewired
    to point at the FIRST confirmed AOI so existing single-AOI downstream
    steps (Manning, BCI, BDY, PAR …) keep working unchanged.
    """
    aoi_features = ctx.get("aoi_features", [])
    if not aoi_features:
        # Backwards-compat: nothing in aoi_features → single-AOI legacy path
        return prepare_dem(
            ctx_path=ctx_path, ctx=ctx, dem_res_m=dem_res_m,
            has_dem=has_dem, user_dem_path=user_dem_path,
            dem_source="3dep", log_fn=log_fn,
        )

    n = len(aoi_features)
    if per_aoi_configs is not None and len(per_aoi_configs) != n:
        raise RuntimeError(
            f"per_aoi_configs has {len(per_aoi_configs)} entries but "
            f"there are {n} AOIs."
        )
    is_triton = bool(ctx.get("triton_dir"))
    parent_project_dir = ctx.get("project_dir")

    summary_paths = []
    for i, feat in enumerate(aoi_features, 1):
        try:
            log_fn(f"▶ Downloading DEM [{i}/{n}]: '{feat['name']}' ...")
            folder = feat["folder_path"]
            Path(folder).mkdir(parents=True, exist_ok=True)

            # Resolve has_dem / user_dem_path / cell size for THIS AOI.  Cell
            # size in per_aoi_configs (when present) overrides the global
            # ``dem_res_m`` argument so different AOIs can use different
            # resolutions.
            if per_aoi_configs is not None:
                cfg = per_aoi_configs[i - 1] or {}
                this_has_dem = bool(cfg.get("has_dem", False))
                this_user_paths = cfg.get("user_dem_path") or None
                this_res_m = float(cfg.get("dem_res_m", dem_res_m))
            else:
                this_has_dem = has_dem
                this_user_paths = user_dem_path
                this_res_m = float(dem_res_m)

            # Per-AOI ctx — points project_dir at this AOI's folder so the raw
            # tile cache (DEM_raw_<aoi>) lives inside the AOI subfolder rather
            # than the project root.  The model_dir points at the
            # ``lisflood-files`` (or ``triton-files``) sub-folder so the
            # ``dem.ascii`` / ``dem.asc`` lands where LISFLOOD-FP / TRITON
            # expects to find it.
            mf_dir = model_files_subdir(folder, is_triton=is_triton)
            feat_ctx = dict(ctx)
            feat_ctx["aoi_path"]          = feat["source_file"]
            feat_ctx["aoi_name"]          = feat["folder_name"]
            feat_ctx["aoi_feature_index"] = feat["feature_index"]
            # Forward the working CRS so DEM / Manning / LULC steps land in
            # this AOI's metric projection (NAD83 / UTM zone … for CONUS).
            if feat.get("working_crs_epsg") is not None:
                feat_ctx["working_crs_epsg"]  = feat["working_crs_epsg"]
            if feat.get("working_crs_label"):
                feat_ctx["working_crs_label"] = feat["working_crs_label"]
            feat_ctx["project_dir"]       = folder
            if is_triton:
                feat_ctx["triton_dir"] = mf_dir
                feat_ctx.pop("lisflood_dir", None)
            else:
                feat_ctx["lisflood_dir"] = mf_dir
                feat_ctx.pop("triton_dir", None)
            feat_ctx["model_dir"] = mf_dir

            feat_ctx_path = str(Path(folder) / "workflow_context.json")
            try:
                with open(feat_ctx_path, "w", encoding="utf-8") as wf:
                    json.dump(feat_ctx, wf, indent=2, default=str)
            except Exception:
                pass

            feat_ctx = prepare_dem(
                ctx_path=feat_ctx_path, ctx=feat_ctx,
                dem_res_m=this_res_m,
                has_dem=this_has_dem, user_dem_path=this_user_paths,
                dem_source="3dep",
                log_fn=log_fn,
            )

            summary_paths.append({
                "name":      feat["name"],
                "folder":    folder,
                "dem_tif":   feat_ctx.get("dem_tif_path"),
                "dem_ascii": feat_ctx.get("dem_ascii_path"),
                "cell_m":    this_res_m,
            })
            log_fn(f"✓ DEM [{i}/{n}] finished: '{feat['name']}'")
        except Exception as _aoi_exc:
            import traceback
            log_fn(f"✗ DEM [{i}/{n}] ERROR for '{feat['name']}': {_aoi_exc}")
            log_fn(traceback.format_exc())
            summary_paths.append({
                "name":    feat.get("name", f"AOI {i}"),
                "failed":  True,
                "error":   str(_aoi_exc),
            })

    # Rewire parent ctx's single-AOI bridge keys to the FIRST AOI
    f0 = aoi_features[0]
    folder0 = f0["folder_path"]
    mf_dir0 = model_files_subdir(folder0, is_triton=is_triton)
    ctx["aoi_path"]          = f0["source_file"]
    ctx["aoi_name"]          = f0["folder_name"]
    ctx["aoi_feature_index"] = f0["feature_index"]
    if is_triton:
        ctx["triton_dir"] = mf_dir0
    else:
        ctx["lisflood_dir"] = mf_dir0
    ctx["model_dir"]    = mf_dir0
    if parent_project_dir:
        ctx["project_dir"] = parent_project_dir

    # Parent ctx mirrors the FIRST SUCCESSFUL AOI's outputs so the
    # legacy single-AOI downstream steps still read sensible values.
    first_ok_idx = next(
        (i for i, s in enumerate(summary_paths) if not s.get("failed")), None
    )
    first_ok = summary_paths[first_ok_idx] if first_ok_idx is not None else None
    if per_aoi_configs is not None:
        if first_ok_idx is not None:
            first_cfg = per_aoi_configs[first_ok_idx] or {}
        else:
            first_cfg = {}
        first_has_dem = bool(first_cfg.get("has_dem", False))
    else:
        first_has_dem = has_dem
    ctx["dem_path"]       = first_ok["dem_tif"] if first_ok else None
    ctx["dem_tif_path"]   = first_ok["dem_tif"] if first_ok else None
    ctx["dem_ascii_path"] = first_ok["dem_ascii"] if first_ok else None
    # Parent ctx mirrors the FIRST AOI's cell size for the legacy
    # single-AOI bridge.  Per-AOI cell sizes are preserved in dem_per_aoi
    # so downstream steps can still find their own AOI's value if needed.
    ctx["dem_res_m"]      = float(first_ok.get("cell_m", dem_res_m)) if first_ok else float(dem_res_m)
    ctx["has_dem"]        = first_has_dem
    ctx["dem_source"]     = "user_provided" if first_has_dem else "download_3dep"
    ctx["par_dem_name"]   = "dem.asc" if is_triton else "dem.ascii"
    ctx["dem_prepared"]   = True
    ctx["dem_per_aoi"]    = summary_paths

    try:
        with open(ctx_path, "w", encoding="utf-8") as wf:
            json.dump(ctx, wf, indent=2, default=str)
    except Exception:
        pass

    log_fn(f"All {n} AOI(s) processed successfully.")
    return ctx




# ── TRITON: multi-AOI Friction (Manning) step ─────────────────────────────────

def run_triton_manning_for_all_aois(
    ctx_path: str,
    ctx: dict,
    per_aoi_configs: list,
    log_fn=print,
) -> dict:
    """Run prepare_triton_manning for every confirmed AOI (TRITON).

    Mirrors run_lisflood_manning_for_all_aois, but writes into each AOI's
    ``triton-files`` sub-folder and calls the TRITON friction builder.
    ``per_aoi_configs`` is a list of prepare_triton_manning kwargs dicts in
    the same order as ``ctx['aoi_features']``.
    """
    from core.triton_manning import prepare_triton_manning

    aoi_features = ctx.get("aoi_features", [])
    if not aoi_features:
        raise RuntimeError("No AOIs in ctx — go back to the AOI step first.")
    if len(per_aoi_configs) != len(aoi_features):
        raise RuntimeError(
            f"per_aoi_configs has {len(per_aoi_configs)} entries but "
            f"there are {len(aoi_features)} AOIs."
        )

    n = len(aoi_features)
    parent_project_dir = ctx.get("project_dir")
    summary = []

    for i, (feat, cfg) in enumerate(zip(aoi_features, per_aoi_configs), 1):
        try:
            log_fn(f"▶ Friction [{i}/{n}]: '{feat['name']}' …")
            folder = feat["folder_path"]
            Path(folder).mkdir(parents=True, exist_ok=True)
            mf_dir = model_files_subdir(folder, is_triton=True)

            feat_ctx = dict(ctx)
            feat_ctx["aoi_path"]          = feat["source_file"]
            feat_ctx["aoi_name"]          = feat["folder_name"]
            feat_ctx["aoi_feature_index"] = feat["feature_index"]
            if feat.get("working_crs_epsg") is not None:
                feat_ctx["working_crs_epsg"]  = feat["working_crs_epsg"]
            if feat.get("working_crs_label"):
                feat_ctx["working_crs_label"] = feat["working_crs_label"]
            feat_ctx["project_dir"]       = folder
            feat_ctx["triton_dir"]        = mf_dir
            feat_ctx["model_dir"]         = mf_dir
            feat_ctx.pop("lisflood_dir", None)

            # Pull this AOI's DEM info from its own per-AOI ctx.
            per_aoi_ctx_path = Path(folder) / "workflow_context.json"
            if per_aoi_ctx_path.exists():
                try:
                    with open(per_aoi_ctx_path, "r", encoding="utf-8") as fr:
                        saved = json.load(fr)
                    for k in ("dem_path", "dem_tif_path", "dem_ascii_path",
                              "dem_res_m", "dem_source"):
                        if k in saved:
                            feat_ctx[k] = saved[k]
                except Exception:
                    pass

            feat_ctx_path = str(per_aoi_ctx_path)

            feat_ctx = prepare_triton_manning(
                ctx_path=feat_ctx_path, ctx=feat_ctx, log_fn=log_fn, **cfg,
            )
            summary.append({
                "name":          feat["name"],
                "folder":        folder,
                "fric_mode":     feat_ctx.get("triton_fric_mode"),
                "manning_tif":   feat_ctx.get("manning_tif_path"),
                "manning_ascii": feat_ctx.get("triton_friction_path"),
                "lulc_tif":      feat_ctx.get("lulc_path"),
                "lulc_source":   feat_ctx.get("lulc_source"),
                "fpfric":        feat_ctx.get("par_fpfric"),
            })
            log_fn(f"✓ Friction [{i}/{n}] finished: '{feat['name']}'")
        except Exception as _aoi_exc:
            import traceback
            log_fn(f"✗ Friction [{i}/{n}] ERROR for '{feat['name']}': {_aoi_exc}")
            log_fn(traceback.format_exc())
            summary.append({
                "name":    feat.get("name", f"AOI {i}"),
                "failed":  True,
                "error":   str(_aoi_exc),
            })

    # Rewire parent ctx's single-AOI bridge keys to the FIRST AOI
    f0 = aoi_features[0]
    folder0 = f0["folder_path"]
    mf_dir0 = model_files_subdir(folder0, is_triton=True)
    ctx["aoi_path"]          = f0["source_file"]
    ctx["aoi_name"]          = f0["folder_name"]
    ctx["aoi_feature_index"] = f0["feature_index"]
    ctx["triton_dir"]        = mf_dir0
    ctx["model_dir"]         = mf_dir0
    if parent_project_dir:
        ctx["project_dir"] = parent_project_dir

    first_ok = next((s for s in summary if not s.get("failed")), None)
    ctx["triton_fric_mode"]     = first_ok["fric_mode"] if first_ok else None
    ctx["triton_friction_path"] = first_ok["manning_ascii"] if first_ok else None
    ctx["par_fpfric"]           = first_ok["fpfric"] if first_ok else None
    ctx["triton_manning_per_aoi"] = summary

    try:
        with open(ctx_path, "w", encoding="utf-8") as wf:
            json.dump(ctx, wf, indent=2, default=str)
    except Exception:
        pass

    log_fn(f"Friction prepared for all {n} AOI(s).")
    return ctx


# ── TRITON: multi-AOI BC step (.src + .extbc) ─────────────────────────────────

def run_triton_bc_for_all_aois(
    ctx_path: str,
    ctx: dict,
    per_aoi_configs: list,
    log_fn=print,
) -> dict:
    """Per AOI: auto-detect the inflow point + downstream boundary segment
    from the flowline/DEM and write that AOI's <AOI>.src + <AOI>.extbc.

    ``per_aoi_configs`` is a list (one per AOI) of dicts from the BC panel:
    ``{"bc_type": 0|1|2|3, "value": <slope/froude>, "stage_file_path": <path>}``.
    The file-writing logic (detect_main_river / prepare_triton_bc) is reused
    unchanged — only the inflow point and downstream segment are auto-derived
    and combined with the user's chosen boundary type.
    """
    from core.triton_bc import detect_main_river, prepare_triton_bc

    aoi_features = ctx.get("aoi_features", [])
    if not aoi_features:
        raise RuntimeError("No AOIs in ctx — go back to the AOI step first.")
    if len(per_aoi_configs) != len(aoi_features):
        raise RuntimeError(
            f"per_aoi_configs has {len(per_aoi_configs)} entries but "
            f"there are {len(aoi_features)} AOIs."
        )

    n = len(aoi_features)
    parent_project_dir = ctx.get("project_dir")
    summary = []

    for i, (feat, cfg) in enumerate(zip(aoi_features, per_aoi_configs), 1):
        try:
            log_fn(f"▶ BC [{i}/{n}]: '{feat['name']}' …")
            folder = feat["folder_path"]
            Path(folder).mkdir(parents=True, exist_ok=True)
            mf_dir = model_files_subdir(folder, is_triton=True)

            feat_ctx = dict(ctx)
            feat_ctx["aoi_path"]          = feat["source_file"]
            feat_ctx["aoi_name"]          = feat["folder_name"]
            feat_ctx["aoi_feature_index"] = feat["feature_index"]
            if feat.get("working_crs_epsg") is not None:
                feat_ctx["working_crs_epsg"]  = feat["working_crs_epsg"]
            if feat.get("working_crs_label"):
                feat_ctx["working_crs_label"] = feat["working_crs_label"]
            feat_ctx["project_dir"]       = folder
            feat_ctx["triton_dir"]        = mf_dir
            feat_ctx["model_dir"]         = mf_dir
            feat_ctx.pop("lisflood_dir", None)

            # Pull this AOI's DEM info (detect_main_river needs dem_tif_path).
            per_aoi_ctx_path = Path(folder) / "workflow_context.json"
            if per_aoi_ctx_path.exists():
                try:
                    with open(per_aoi_ctx_path, "r", encoding="utf-8") as fr:
                        saved = json.load(fr)
                    for k in ("dem_path", "dem_tif_path", "dem_ascii_path",
                              "dem_res_m"):
                        if k in saved:
                            feat_ctx[k] = saved[k]
                except Exception:
                    pass
            feat_ctx_path = str(per_aoi_ctx_path)

            # Inflow point + downstream segment: either auto-detected from the
            # flowline/DEM (unchanged core) or taken from manual coordinates.
            if cfg.get("detect_mode") == "manual":
                up = cfg.get("inflow_xy")
                seg = cfg.get("segment")
                if not up or not seg:
                    raise RuntimeError(
                        "Manual mode needs an inflow point and an outflow segment."
                    )
                main_river_name = None
                upstream_reach_id = None
                log_fn(f"  Manual coordinates: inflow {up}, outflow segment {seg}")
            else:
                detected = detect_main_river(feat_ctx, log_fn=log_fn)
                up = detected["upstream_pt"]
                seg = detected["downstream_segment"]
                main_river_name = detected.get("main_river_name")
                upstream_reach_id = detected.get("upstream_reach_id")

            bt = int(cfg["bc_type"])
            entry = {"bc_type": bt, "x1": seg[0], "y1": seg[1],
                     "x2": seg[2], "y2": seg[3]}
            if bt == 1:
                entry["stage_file_path"] = cfg.get("stage_file_path")
            elif bt in (2, 3):
                entry["value"] = cfg.get("value")

            feat_ctx = prepare_triton_bc(
                ctx_path=feat_ctx_path, ctx=feat_ctx,
                inflow_sources=[up], bc_entries=[entry],
                main_river_name=main_river_name,
                upstream_reach_id=upstream_reach_id,
                log_fn=log_fn,
            )
            summary.append({
                "name":              feat["name"],
                "folder":            folder,
                "bc_type":           bt,
                "extbc_path":        feat_ctx.get("triton_extbc_path"),
                "src_path":          feat_ctx.get("triton_src_loc_path"),
                "num_sources":       feat_ctx.get("num_sources"),
                "upstream_reach_id": feat_ctx.get("upstream_reach_id"),
                "main_river_name":   feat_ctx.get("main_river_name"),
            })
            log_fn(f"✓ BC [{i}/{n}] finished: '{feat['name']}'")
        except Exception as _aoi_exc:
            import traceback
            log_fn(f"✗ BC [{i}/{n}] ERROR for '{feat['name']}': {_aoi_exc}")
            log_fn(traceback.format_exc())
            summary.append({
                "name":    feat.get("name", f"AOI {i}"),
                "failed":  True,
                "error":   str(_aoi_exc),
            })

    # Rewire parent ctx's single-AOI bridge keys to the FIRST AOI
    f0 = aoi_features[0]
    folder0 = f0["folder_path"]
    mf_dir0 = model_files_subdir(folder0, is_triton=True)
    ctx["aoi_path"]          = f0["source_file"]
    ctx["aoi_name"]          = f0["folder_name"]
    ctx["aoi_feature_index"] = f0["feature_index"]
    ctx["triton_dir"]        = mf_dir0
    ctx["model_dir"]         = mf_dir0
    if parent_project_dir:
        ctx["project_dir"] = parent_project_dir

    first_ok = next((s for s in summary if not s.get("failed")), None)
    ctx["num_sources"]        = first_ok["num_sources"] if first_ok else None
    ctx["upstream_reach_id"]  = first_ok["upstream_reach_id"] if first_ok else None
    ctx["triton_bc_per_aoi"]  = summary

    try:
        with open(ctx_path, "w", encoding="utf-8") as wf:
            json.dump(ctx, wf, indent=2, default=str)
    except Exception:
        pass

    log_fn(f"BC prepared for all {n} AOI(s).")
    return ctx


# ── TRITON: multi-AOI Hydrograph step (.hyg) ──────────────────────────────────

def run_triton_hydro_for_all_aois(
    ctx_path: str,
    ctx: dict,
    per_aoi_configs: list,
    log_fn=print,
) -> dict:
    """Per AOI: fetch the inflow discharge from the chosen source and write
    that AOI's <AOI>.hyg.  ``per_aoi_configs`` is a list (one per AOI) of
    BDY-panel dicts: ``{"bdy_source","gage_id","file_path","start_dt",
    "end_dt","interval_hours"}``.  Reuses the BDY fetchers + the existing
    TRITON .hyg writer (write_triton_hyg_single)."""
    from core.triton_hydro import write_triton_hyg_single

    aoi_features = ctx.get("aoi_features", [])
    if not aoi_features:
        raise RuntimeError("No AOIs in ctx — go back to the AOI step first.")
    if len(per_aoi_configs) != len(aoi_features):
        raise RuntimeError(
            f"per_aoi_configs has {len(per_aoi_configs)} entries but "
            f"there are {len(aoi_features)} AOIs."
        )

    n = len(aoi_features)
    parent_project_dir = ctx.get("project_dir")
    summary = []

    for i, (feat, cfg) in enumerate(zip(aoi_features, per_aoi_configs), 1):
        try:
            log_fn(f"▶ Hydrograph [{i}/{n}]: '{feat['name']}' …")
            folder = feat["folder_path"]
            Path(folder).mkdir(parents=True, exist_ok=True)
            mf_dir = model_files_subdir(folder, is_triton=True)

            feat_ctx = dict(ctx)
            feat_ctx["aoi_name"]    = feat["folder_name"]
            feat_ctx["project_dir"] = folder
            feat_ctx["triton_dir"]  = mf_dir
            feat_ctx["model_dir"]   = mf_dir
            feat_ctx.pop("lisflood_dir", None)

            # This AOI's upstream feature/reach id (written by the BC step) —
            # needed for NWM sources.
            reach_id = None
            per_aoi_ctx_path = Path(folder) / "workflow_context.json"
            if per_aoi_ctx_path.exists():
                try:
                    with open(per_aoi_ctx_path, "r", encoding="utf-8") as fr:
                        saved = json.load(fr)
                    reach_id = saved.get("upstream_reach_id")
                except Exception:
                    pass
            feat_ctx_path = str(per_aoi_ctx_path)

            bdy_source = cfg.get("bdy_source") or ""
            feat_ctx = write_triton_hyg_single(
                ctx_path=feat_ctx_path, ctx=feat_ctx,
                bdy_source=bdy_source,
                start_dt=cfg["start_dt"], end_dt=cfg["end_dt"],
                interval_hours=float(cfg["interval_hours"]),
                gage_id=cfg.get("gage_id") or None,
                user_csv_path=(cfg.get("file_path") if bdy_source == "csv" else None),
                nwm_reach_id=reach_id,
                log_fn=log_fn,
            )
            summary.append({
                "name":        feat["name"],
                "folder":      folder,
                "hyg_path":    feat_ctx.get("triton_hyg_path"),
                "helper_csv":  feat_ctx.get("triton_hydro_helper_csv"),
                "source":      feat_ctx.get("triton_hydro_source"),
                "sim_duration": feat_ctx.get("sim_duration"),
            })
            log_fn(f"✓ Hydrograph [{i}/{n}] finished: '{feat['name']}'")
        except Exception as _aoi_exc:
            import traceback
            log_fn(f"✗ Hydrograph [{i}/{n}] ERROR for '{feat['name']}': {_aoi_exc}")
            log_fn(traceback.format_exc())
            summary.append({
                "name":    feat.get("name", f"AOI {i}"),
                "failed":  True,
                "error":   str(_aoi_exc),
            })

    f0 = aoi_features[0]
    mf_dir0 = model_files_subdir(f0["folder_path"], is_triton=True)
    ctx["aoi_name"]   = f0["folder_name"]
    ctx["triton_dir"] = mf_dir0
    ctx["model_dir"]  = mf_dir0
    if parent_project_dir:
        ctx["project_dir"] = parent_project_dir
    first_ok = next((s for s in summary if not s.get("failed")), None)
    ctx["triton_hydro_per_aoi"] = summary
    if first_ok:
        ctx["sim_duration"] = first_ok.get("sim_duration")

    try:
        with open(ctx_path, "w", encoding="utf-8") as wf:
            json.dump(ctx, wf, indent=2, default=str)
    except Exception:
        pass

    log_fn(f"Hydrograph prepared for all {n} AOI(s).")
    return ctx


# ── TRITON: multi-AOI Config step (.cfg) ──────────────────────────────────────

def run_triton_cfg_for_all_aois(
    ctx_path: str,
    ctx: dict,
    per_aoi_configs: list,
    log_fn=print,
) -> dict:
    """Per AOI: auto-generate that AOI's <AOI>.cfg from its prepared inputs.
    ``per_aoi_configs`` is a list (one per AOI) of cfg-panel dicts
    (output_format, print_option, time_step, print_interval, courant); the
    rest of the .cfg is filled in by create_triton_cfg from the AOI's saved
    context (sim_duration, num_sources/num_extbc, file refs, projection)."""
    from core.triton_cfg import create_triton_cfg

    aoi_features = ctx.get("aoi_features", [])
    if not aoi_features:
        raise RuntimeError("No AOIs in ctx — go back to the AOI step first.")
    if len(per_aoi_configs) != len(aoi_features):
        raise RuntimeError(
            f"per_aoi_configs has {len(per_aoi_configs)} entries but "
            f"there are {len(aoi_features)} AOIs."
        )

    n = len(aoi_features)
    parent_project_dir = ctx.get("project_dir")
    summary = []

    for i, (feat, cfg) in enumerate(zip(aoi_features, per_aoi_configs), 1):
        try:
            log_fn(f"▶ Config [{i}/{n}]: '{feat['name']}' …")
            folder = feat["folder_path"]
            Path(folder).mkdir(parents=True, exist_ok=True)
            mf_dir = model_files_subdir(folder, is_triton=True)

            feat_ctx = dict(ctx)
            # Merge this AOI's full saved context (sim_duration, num_sources,
            # num_extbc, file refs, dem_epsg, fric_mode, … written by the
            # earlier steps) so create_triton_cfg has everything it needs.
            per_aoi_ctx_path = Path(folder) / "workflow_context.json"
            if per_aoi_ctx_path.exists():
                try:
                    with open(per_aoi_ctx_path, "r", encoding="utf-8") as fr:
                        feat_ctx.update(json.load(fr))
                except Exception:
                    pass
            feat_ctx["aoi_name"]    = feat["folder_name"]
            feat_ctx["project_dir"] = folder
            feat_ctx["triton_dir"]  = mf_dir
            feat_ctx["model_dir"]   = mf_dir
            feat_ctx.pop("lisflood_dir", None)
            feat_ctx_path = str(per_aoi_ctx_path)

            feat_ctx = create_triton_cfg(
                ctx_path=feat_ctx_path, ctx=feat_ctx,
                output_format=cfg.get("output_format", "ASC"),
                output_option="SEQ",
                input_format="ASC",
                print_option=cfg.get("print_option", "huv"),
                time_step=float(cfg.get("time_step", 10.0)),
                print_interval=float(cfg.get("print_interval", 3600.0)),
                courant=float(cfg.get("courant", 0.5)),
                open_boundaries=1,   # an explicit .extbc boundary is present
                log_fn=log_fn,
            )
            summary.append({
                "name":     feat["name"],
                "folder":   folder,
                "cfg_path": feat_ctx.get("triton_cfg_path"),
            })
            log_fn(f"✓ Config [{i}/{n}] finished: '{feat['name']}'")
        except Exception as _aoi_exc:
            import traceback
            log_fn(f"✗ Config [{i}/{n}] ERROR for '{feat['name']}': {_aoi_exc}")
            log_fn(traceback.format_exc())
            summary.append({
                "name":    feat.get("name", f"AOI {i}"),
                "failed":  True,
                "error":   str(_aoi_exc),
            })

    f0 = aoi_features[0]
    mf_dir0 = model_files_subdir(f0["folder_path"], is_triton=True)
    ctx["aoi_name"]   = f0["folder_name"]
    ctx["triton_dir"] = mf_dir0
    ctx["model_dir"]  = mf_dir0
    if parent_project_dir:
        ctx["project_dir"] = parent_project_dir
    ctx["triton_cfg_per_aoi"] = summary

    try:
        with open(ctx_path, "w", encoding="utf-8") as wf:
            json.dump(ctx, wf, indent=2, default=str)
    except Exception:
        pass

    log_fn(f"Config prepared for all {n} AOI(s).")
    return ctx


