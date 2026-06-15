"""Orchestrators for the three standalone modes.

Each function loops over a list of AOIFeatureInfo and runs the appropriate
per-feature pipeline.  Returns a summary dict.

These functions are blocking — call them from a Worker thread.
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


# ── per-feature helpers ───────────────────────────────────────────────────────

def _make_feature_ctx(project_dir: Path, feature: AOIFeatureInfo) -> dict:
    """Build a single-feature context dict for the standalone modes.

    NOTE: only `model_dir` is set — NOT `lisflood_dir` or `triton_dir`.
    The latter two are markers that route prepare_dem into the LISFLOOD-FP
    or TRITON pipelines (TRITON in particular does a nearest-neighbour
    nodata fill that destroys the AOI polygon mask).  Standalone modes
    must stay neutral.
    """
    folder = Path(feature.folder_path)
    folder.mkdir(parents=True, exist_ok=True)
    ctx = {
        "base_dir":       str(project_dir.parent),
        "project_name":   feature.folder_name,
        "project_dir":    str(folder),
        "model_dir":      str(folder),
        "aoi_path":       feature.source_file,
        "aoi_name":       Path(feature.source_file).stem + f"_f{feature.feature_index}",
        "aoi_feature_index": feature.feature_index,
        "dem_path":       None,
        "dem_tif_path":   None,
    }
    # Forward the AOI's auto-picked working CRS so the standalone DEM /
    # LULC / Manning modes land their rasters in the same metric
    # projection as the LISFLOOD-FP / TRITON workflows.
    if getattr(feature, "working_crs_epsg", None) is not None:
        ctx["working_crs_epsg"] = int(feature.working_crs_epsg)
    if getattr(feature, "working_crs_label", None):
        ctx["working_crs_label"] = feature.working_crs_label
    return ctx


def _save_feature_ctx(ctx: dict) -> Path:
    p = Path(ctx["project_dir"]) / "workflow_context.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(ctx, f, indent=2, default=str)
    return p


# ── LISFLOOD-FP: multi-AOI PAR step ───────────────────────────────────────────

def run_lisflood_par_for_all_aois(
    ctx_path: str,
    ctx: dict,
    per_aoi_configs: list,
    log_fn=print,
) -> dict:
    """Run create_par for every confirmed AOI in LISFLOOD-FP.

    ``per_aoi_configs`` is a list of kwargs dicts (one per AOI in the
    same order as ``ctx['aoi_features']``).  Each dict carries the full
    set of PAR knobs from one AOI's PARConfigPanel.

    Emits ``▶ PAR [N/M]`` / ``✓ PAR [N/M]`` markers for the GUI.
    """
    from core.par import create_par

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
            log_fn(f"▶ PAR [{i}/{n}]: '{feat['name']}' …")
            folder = feat["folder_path"]
            Path(folder).mkdir(parents=True, exist_ok=True)
            mf_dir = model_files_subdir(folder, is_triton=False)

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
            feat_ctx["lisflood_dir"]      = mf_dir
            feat_ctx["model_dir"]         = mf_dir
            feat_ctx.pop("triton_dir", None)

            # Pull this AOI's per-AOI ctx (DEM, Manning, BCI, BDY paths) so
            # create_par can find every input file it needs.
            per_aoi_ctx_path = Path(folder) / "workflow_context.json"
            if per_aoi_ctx_path.exists():
                try:
                    with open(per_aoi_ctx_path, "r", encoding="utf-8") as fr:
                        saved = json.load(fr)
                    for k in (
                        "dem_path", "dem_tif_path", "dem_ascii_path",
                        "manning_ascii_path", "manning_tif_path",
                        "fric_mode", "par_fpfric", "par_use_manningfile",
                        "par_use_fpfric", "par_dem_name",
                        "bci_path", "bdy_path", "bdy_written",
                        "upstream_mode", "downstream_type",
                        "event_start", "event_end",
                    ):
                        if k in saved:
                            feat_ctx[k] = saved[k]
                except Exception:
                    pass

            feat_ctx_path = str(per_aoi_ctx_path)

            feat_ctx = create_par(
                ctx_path=feat_ctx_path, ctx=feat_ctx, log_fn=log_fn, **cfg,
            )
            summary.append({
                "name":      feat["name"],
                "folder":    folder,
                "par_path":  feat_ctx.get("par_path"),
                "par_name":  cfg.get("par_name"),
            })
            log_fn(f"✓ PAR [{i}/{n}] finished: '{feat['name']}'")
        except Exception as _aoi_exc:
            import traceback
            log_fn(f"✗ PAR [{i}/{n}] ERROR for '{feat['name']}': {_aoi_exc}")
            log_fn(traceback.format_exc())
            summary.append({
                "name":    feat.get("name", f"AOI {i}"),
                "failed":  True,
                "error":   str(_aoi_exc),
            })

    f0 = aoi_features[0]
    folder0 = f0["folder_path"]
    mf_dir0 = model_files_subdir(folder0, is_triton=False)
    ctx["aoi_path"]          = f0["source_file"]
    ctx["aoi_name"]          = f0["folder_name"]
    ctx["aoi_feature_index"] = f0["feature_index"]
    ctx["lisflood_dir"]      = mf_dir0
    ctx["model_dir"]         = mf_dir0
    if parent_project_dir:
        ctx["project_dir"] = parent_project_dir

    first_ok = next((s for s in summary if not s.get("failed")), None)
    ctx["par_path"]    = first_ok["par_path"] if first_ok else None
    ctx["par_per_aoi"] = summary

    try:
        with open(ctx_path, "w", encoding="utf-8") as wf:
            json.dump(ctx, wf, indent=2, default=str)
    except Exception:
        pass

    log_fn(f"PAR prepared for all {n} AOI(s).")
    return ctx


# ── LISFLOOD-FP: multi-AOI BDY step ───────────────────────────────────────────

def run_lisflood_bdy_for_all_aois(
    ctx_path: str,
    ctx: dict,
    per_aoi_configs: list,
    log_fn=print,
) -> dict:
    """Run create_bdy for every confirmed AOI in LISFLOOD-FP.

    Each entry in ``per_aoi_configs`` carries ``bdy_source`` plus the
    relevant fields (file_path, start_dt, end_dt, interval_hours,
    gap_handling).  AOIs whose upstream_mode is fixed_discharge are
    skipped automatically (no BDY needed).

    Emits ``▶ BDY [N/M]`` / ``✓ BDY [N/M]`` markers so the GUI can show
    per-AOI progress.
    """
    from core.bdy import create_bdy

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
            log_fn(f"▶ BDY [{i}/{n}]: '{feat['name']}' …")
            folder = feat["folder_path"]
            Path(folder).mkdir(parents=True, exist_ok=True)
            mf_dir = model_files_subdir(folder, is_triton=False)

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
            feat_ctx["lisflood_dir"]      = mf_dir
            feat_ctx["model_dir"]         = mf_dir
            feat_ctx.pop("triton_dir", None)

            # Pull this AOI's per-AOI ctx (DEM + BCI info) so create_bdy knows
            # the upstream mode and reach id.
            per_aoi_ctx_path = Path(folder) / "workflow_context.json"
            if per_aoi_ctx_path.exists():
                try:
                    with open(per_aoi_ctx_path, "r", encoding="utf-8") as fr:
                        saved = json.load(fr)
                    for k in (
                        "dem_path", "dem_tif_path", "dem_ascii_path",
                        "upstream_mode", "upstream_reach_id",
                        "main_river_name", "downstream_type",
                    ):
                        if k in saved:
                            feat_ctx[k] = saved[k]
                except Exception:
                    pass

            feat_ctx_path = str(per_aoi_ctx_path)

            kw = dict(
                start_dt=cfg["start_dt"],
                end_dt=cfg["end_dt"],
                interval_hours=float(cfg["interval_hours"]),
                bdy_source=cfg["bdy_source"],
                existing_bdy_path=(cfg.get("file_path")
                                   if cfg["bdy_source"] == "existing" else None),
                user_csv_path=(cfg.get("file_path")
                               if cfg["bdy_source"] == "csv" else None),
                gap_handling=cfg.get("gap_handling", "interpolate"),
                gage_id=cfg.get("gage_id"),
            )

            feat_ctx = create_bdy(
                ctx_path=feat_ctx_path, ctx=feat_ctx, log_fn=log_fn, **kw,
            )
            # Helper CSV for the hydrograph preview — created by core/bdy.py
            # alongside the .bdy file when source ∈ {nwm, csv, existing}.
            proj_name = feat_ctx.get("project_name", "")
            helper_csv = (
                str(Path(folder) / f"{proj_name}_upstream_timeseries.csv")
                if proj_name else None
            )
            summary.append({
                "name":              feat["name"],
                "folder":            folder,
                "bdy_path":          feat_ctx.get("bdy_path"),
                "bdy_source":        feat_ctx.get("bdy_source"),
                "written":           feat_ctx.get("bdy_written", False),
                "upstream_reach_id": feat_ctx.get("upstream_reach_id"),
                "helper_csv":        helper_csv,
                "warnings":          feat_ctx.get("bdy_warnings", []),
            })
            log_fn(f"✓ BDY [{i}/{n}] finished: '{feat['name']}'")
        except Exception as _aoi_exc:
            import traceback
            log_fn(f"✗ BDY [{i}/{n}] ERROR for '{feat['name']}': {_aoi_exc}")
            log_fn(traceback.format_exc())
            summary.append({
                "name":    feat.get("name", f"AOI {i}"),
                "failed":  True,
                "error":   str(_aoi_exc),
            })

    # Rewire parent ctx → FIRST AOI
    f0 = aoi_features[0]
    folder0 = f0["folder_path"]
    mf_dir0 = model_files_subdir(folder0, is_triton=False)
    ctx["aoi_path"]          = f0["source_file"]
    ctx["aoi_name"]          = f0["folder_name"]
    ctx["aoi_feature_index"] = f0["feature_index"]
    ctx["lisflood_dir"]      = mf_dir0
    ctx["model_dir"]         = mf_dir0
    if parent_project_dir:
        ctx["project_dir"] = parent_project_dir

    first_ok = next((s for s in summary if not s.get("failed")), None)
    ctx["bdy_path"]    = first_ok["bdy_path"] if first_ok else None
    ctx["bdy_source"]  = first_ok["bdy_source"] if first_ok else None
    ctx["bdy_written"] = first_ok["written"] if first_ok else False
    ctx["bdy_per_aoi"] = summary

    try:
        with open(ctx_path, "w", encoding="utf-8") as wf:
            json.dump(ctx, wf, indent=2, default=str)
    except Exception:
        pass

    log_fn(f"BDY prepared for all {n} AOI(s).")
    return ctx


# ── LISFLOOD-FP: multi-AOI BCI step ───────────────────────────────────────────

def run_lisflood_bci_for_all_aois(
    ctx_path: str,
    ctx: dict,
    per_aoi_configs: list,
    log_fn=print,
) -> dict:
    """Run create_bci for every confirmed AOI in LISFLOOD-FP.

    ``per_aoi_configs`` is a list of kwargs dicts, one per AOI in the same
    order as ``ctx['aoi_features']``.  Each dict carries the selections
    from one AOI's BCIConfigPanel (use_nhd, upstream_mode, downstream_type,
    fixed_q, slope, hfix, manual_*).

    Emits ``▶ BCI [N/M]`` / ``✓ BCI [N/M]`` markers so the GUI can show
    per-AOI progress.  Restores parent ctx's bridge keys to the FIRST AOI
    when done.
    """
    from core.bci import create_bci

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
            log_fn(f"▶ BCI [{i}/{n}]: '{feat['name']}' …")
            folder = feat["folder_path"]
            Path(folder).mkdir(parents=True, exist_ok=True)
            mf_dir = model_files_subdir(folder, is_triton=False)

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
            feat_ctx["lisflood_dir"]      = mf_dir
            feat_ctx["model_dir"]         = mf_dir
            feat_ctx.pop("triton_dir", None)

            # Pull this AOI's per-AOI ctx (DEM + Manning paths) so create_bci
            # knows where the DEM lives.
            per_aoi_ctx_path = Path(folder) / "workflow_context.json"
            if per_aoi_ctx_path.exists():
                try:
                    with open(per_aoi_ctx_path, "r", encoding="utf-8") as fr:
                        saved = json.load(fr)
                    for k in ("dem_path", "dem_tif_path", "dem_ascii_path",
                              "manning_ascii_path", "fric_mode", "par_fpfric"):
                        if k in saved:
                            feat_ctx[k] = saved[k]
                except Exception:
                    pass

            feat_ctx_path = str(per_aoi_ctx_path)

            feat_ctx = create_bci(
                ctx_path=feat_ctx_path, ctx=feat_ctx, log_fn=log_fn, **cfg,
            )
            summary.append({
                "name":              feat["name"],
                "folder":            folder,
                "bci_path":          feat_ctx.get("bci_path"),
                "upstream_mode":     feat_ctx.get("upstream_mode"),
                "downstream_type":   feat_ctx.get("downstream_type"),
                "upstream_reach_id": feat_ctx.get("upstream_reach_id"),
                "river":             feat_ctx.get("main_river_name"),
                # Coordinates (in the AOI CRS) so the GUI can draw stars on
                # the post-run preview map.
                "upstream_x":        feat_ctx.get("upstream_x"),
                "upstream_y":        feat_ctx.get("upstream_y"),
                "downstream_x":      feat_ctx.get("downstream_x"),
                "downstream_y":      feat_ctx.get("downstream_y"),
                # Files needed by the preview map
                "source_file":       feat["source_file"],
                "feature_index":     feat["feature_index"],
                "main_river_line":   str(Path(folder) / "main_river_line.gpkg"),
                "flowlines_path":    feat_ctx.get("flowlines_path"),
                # DEM path for this specific AOI — used to read the correct CRS
                # when reprojecting upstream/downstream points for the map preview.
                "dem_path": (
                    feat_ctx.get("dem_path")
                    or feat_ctx.get("dem_tif")
                    or feat.get("dem_path")
                ),
            })
            log_fn(f"✓ BCI [{i}/{n}] finished: '{feat['name']}'")
        except Exception as _aoi_exc:
            import traceback
            log_fn(f"✗ BCI [{i}/{n}] ERROR for '{feat['name']}': {_aoi_exc}")
            log_fn(traceback.format_exc())
            summary.append({
                "name":    feat.get("name", f"AOI {i}"),
                "failed":  True,
                "error":   str(_aoi_exc),
            })

    # Rewire parent ctx → FIRST AOI
    f0 = aoi_features[0]
    folder0 = f0["folder_path"]
    mf_dir0 = model_files_subdir(folder0, is_triton=False)
    ctx["aoi_path"]          = f0["source_file"]
    ctx["aoi_name"]          = f0["folder_name"]
    ctx["aoi_feature_index"] = f0["feature_index"]
    ctx["lisflood_dir"]      = mf_dir0
    ctx["model_dir"]         = mf_dir0
    if parent_project_dir:
        ctx["project_dir"] = parent_project_dir

    first_ok = next((s for s in summary if not s.get("failed")), None)
    ctx["bci_path"]         = first_ok["bci_path"] if first_ok else None
    ctx["upstream_mode"]    = first_ok["upstream_mode"] if first_ok else None
    ctx["downstream_type"]  = first_ok["downstream_type"] if first_ok else None
    ctx["upstream_reach_id"] = first_ok["upstream_reach_id"] if first_ok else None
    ctx["bci_per_aoi"]      = summary

    try:
        with open(ctx_path, "w", encoding="utf-8") as wf:
            json.dump(ctx, wf, indent=2, default=str)
    except Exception:
        pass

    log_fn(f"BCI prepared for all {n} AOI(s).")
    return ctx


# ── LISFLOOD-FP: multi-AOI Manning step ───────────────────────────────────────

def run_lisflood_manning_for_all_aois(
    ctx_path: str,
    ctx: dict,
    per_aoi_configs: list,
    log_fn=print,
) -> dict:
    """Run prepare_manning for every confirmed AOI in LISFLOOD-FP.

    ``per_aoi_configs`` is a list of (folder_name → kwargs) dicts in the
    same order as ``ctx['aoi_features']`` — each kwargs dict carries the
    selections from one AOI's ManningConfigPanel.

    Emits ``▶ Manning [N/M]: 'name' …`` and ``✓ Manning [N/M] finished``
    log markers so the GUI can show per-AOI progress.

    The parent ctx's single-AOI bridge keys are restored to point at the
    FIRST AOI when done.
    """
    from core.manning import prepare_manning

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
            log_fn(f"▶ Manning [{i}/{n}]: '{feat['name']}' …")
            folder = feat["folder_path"]
            Path(folder).mkdir(parents=True, exist_ok=True)
            mf_dir = model_files_subdir(folder, is_triton=False)

            # Per-AOI ctx — model-files dir points at the lisflood-files
            # sub-folder so prepare_manning writes lulc.ascii into the right
            # location, while project_dir stays at the AOI folder for
            # intermediate scratch files.
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
            feat_ctx["lisflood_dir"]      = mf_dir
            feat_ctx["model_dir"]         = mf_dir
            feat_ctx.pop("triton_dir", None)

            # Pull this AOI's DEM info from its own per-AOI ctx (written by
            # the DEM step's orchestrator).  Fall back to the parent ctx so a
            # single-AOI legacy run still works.
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

            feat_ctx = prepare_manning(
                ctx_path=feat_ctx_path, ctx=feat_ctx, log_fn=log_fn, **cfg,
            )
            summary.append({
                "name":          feat["name"],
                "folder":        folder,
                "fric_mode":     feat_ctx.get("fric_mode"),
                "manning_tif":   feat_ctx.get("manning_tif_path"),
                "manning_ascii": feat_ctx.get("manning_ascii_path"),
                "lulc_tif":      feat_ctx.get("lulc_path"),
                "lulc_source":   feat_ctx.get("lulc_source"),
                "fpfric":        feat_ctx.get("par_fpfric"),
            })
            log_fn(f"✓ Manning [{i}/{n}] finished: '{feat['name']}'")
        except Exception as _aoi_exc:
            import traceback
            log_fn(f"✗ Manning [{i}/{n}] ERROR for '{feat['name']}': {_aoi_exc}")
            log_fn(traceback.format_exc())
            summary.append({
                "name":    feat.get("name", f"AOI {i}"),
                "failed":  True,
                "error":   str(_aoi_exc),
            })

    # Rewire parent ctx's single-AOI bridge keys to the FIRST AOI
    f0 = aoi_features[0]
    folder0 = f0["folder_path"]
    mf_dir0 = model_files_subdir(folder0, is_triton=False)
    ctx["aoi_path"]          = f0["source_file"]
    ctx["aoi_name"]          = f0["folder_name"]
    ctx["aoi_feature_index"] = f0["feature_index"]
    ctx["lisflood_dir"]      = mf_dir0
    ctx["model_dir"]         = mf_dir0
    if parent_project_dir:
        ctx["project_dir"] = parent_project_dir

    first_ok = next((s for s in summary if not s.get("failed")), None)
    ctx["fric_mode"]            = first_ok["fric_mode"] if first_ok else None
    ctx["manning_ascii_path"]   = first_ok["manning_ascii"] if first_ok else None
    ctx["par_fpfric"]           = first_ok["fpfric"] if first_ok else None
    ctx["manning_per_aoi"]      = summary

    try:
        with open(ctx_path, "w", encoding="utf-8") as wf:
            json.dump(ctx, wf, indent=2, default=str)
    except Exception:
        pass

    log_fn(f"Manning prepared for all {n} AOI(s).")
    return ctx


# ── LISFLOOD-FP / TRITON: multi-AOI DEM step ──────────────────────────────────

def run_lisflood_triton_dem_all(
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


# ── DEM mode ──────────────────────────────────────────────────────────────────

def run_dem_mode(
    project_dir: str,
    features: List[AOIFeatureInfo],
    dem_cell_size_m: float,
    out_format: str,                           # global fallback
    dem_source: str = "3dep",                  # global fallback
    dem_sources: Optional[List[str]] = None,   # per-AOI, overrides dem_source
    dem_formats: Optional[List[str]] = None,   # per-AOI, overrides out_format
    dem_cell_sizes: Optional[List[float]] = None,  # per-AOI, overrides dem_cell_size_m
    log_fn=print,
) -> Dict:
    """For each AOI feature, download/prepare DEM and export in the chosen format."""
    project_dir = Path(project_dir)
    summary = {"features": [], "format": out_format, "cell_size_m": dem_cell_size_m}
    n = len(features)

    for i, f in enumerate(features, 1):
        idx = i - 1
        src    = dem_sources[idx]    if dem_sources    and idx < len(dem_sources)    else dem_source
        fmt    = dem_formats[idx]    if dem_formats    and idx < len(dem_formats)    else out_format
        cell_m = dem_cell_sizes[idx] if dem_cell_sizes and idx < len(dem_cell_sizes) else dem_cell_size_m
        log_fn(f"▶ Running [{i}/{n}]: '{f.name}' ...")
        ctx = _make_feature_ctx(project_dir, f)
        ctx_path = _save_feature_ctx(ctx)

        # 1. Run the selected DEM source pipeline.  skip_ascii=True so we
        #    don't produce a stray .ascii file the user didn't request.
        ctx = prepare_dem(
            ctx_path=str(ctx_path), ctx=ctx,
            dem_res_m=cell_m,
            has_dem=False, user_dem_path=None,
            dem_source=src,
            skip_ascii=True,
            log_fn=log_fn,
        )

        # 2. Export to user-requested format with a source-tagged filename,
        #    auto-renaming (1), (2), … if the target already exists.
        dem_tif = Path(ctx["dem_tif_path"])
        src_tag = "HAND" if src == "hand" else "3DEP"
        stem = f"DEM_{src_tag}_{f.folder_name}"
        out_path = next_free_path(Path(f.folder_path), stem, fmt)
        export_raster(
            src_tif=str(dem_tif),
            out_path=str(out_path),
            out_format=fmt,
            cell_size_m=cell_m,
            log_fn=log_fn,
        )

        # 3. Remove the intermediate prepare_dem output now that we've
        #    written the user-requested file — keep only ONE file in the
        #    AOI folder (plus the cached raw download in its subfolder).
        try:
            if dem_tif.resolve() != out_path.resolve() and dem_tif.exists():
                dem_tif.unlink()
                # Also drop the auxiliary .tif.aux.xml if rasterio wrote one
                aux = dem_tif.with_suffix(dem_tif.suffix + ".aux.xml")
                if aux.exists():
                    aux.unlink()
        except Exception as ex:
            log_fn(f"  (kept intermediate {dem_tif.name}: {ex})")

        # Compact relative path: <project_folder>/<aoi_folder>/<filename>
        rel_path = f"{project_dir.name}/{f.folder_name}/{out_path.name}"
        log_fn(f"✓ Done [{i}/{n}]: {rel_path}")
        summary["features"].append({
            "name": f.name,
            "folder": f.folder_path,
            "dem_path": str(out_path),
        })
    log_fn(f"All {n} AOI(s) processed successfully.")
    return summary


# ── LULC + Manning mode ───────────────────────────────────────────────────────

def _compute_lulc_stats(lulc_tif: Path, lulc_source: str) -> list:
    """Compute per-class pixel statistics from a LULC GeoTIFF.

    Returns a list of dicts sorted by area fraction (descending):
        [{"code": int, "name": str, "manning_n": float|None,
          "area_frac": float}, ...]
    """
    import numpy as np_
    from core.nlcd import NLCD_MANNING, SENTINEL2_MANNING
    import rasterio as _rio

    lookup = NLCD_MANNING if lulc_source == "nlcd" else SENTINEL2_MANNING
    try:
        with _rio.open(lulc_tif) as src:
            arr = src.read(1).ravel()
            nodata = src.nodata if src.nodata is not None else 0
    except Exception:
        return []

    valid = arr[arr != int(nodata)]
    total = int(valid.size)
    if total == 0:
        return []

    unique, counts = np_.unique(valid, return_counts=True)
    rows = []
    for code_raw, cnt in zip(unique.tolist(), counts.tolist()):
        code = int(code_raw)
        entry = lookup.get(code)
        if entry:
            name, _, _, dflt = entry
        else:
            name, dflt = f"Class {code}", None
        rows.append({
            "code":       code,
            "name":       name,
            "manning_n":  dflt,
            "area_frac":  cnt / total,
        })
    rows.sort(key=lambda r: r["area_frac"], reverse=True)
    return rows


def run_lulc_mode(
    project_dir: str,
    features: List[AOIFeatureInfo],
    per_aoi_configs: Optional[List[Dict]] = None,   # NEW: one dict per AOI
    # ── Legacy flat params (used when per_aoi_configs is None) ──────────────
    lulc_source: str = "nlcd",          # "nlcd" | "sentinel2"
    cell_size_m: float = 30.0,
    lulc_format: str = "tif",           # "tif" | "gpkg" | "asc"
    nlcd_year: str = "2021",
    sentinel2_year: int = 2023,
    do_manning: bool = False,
    manning_format: str = "tif",        # "tif" | "gpkg" | "asc" | "shp"
    manning_mapping: Optional[Dict[int, float]] = None,
    log_fn=print,
) -> Dict:
    """Per AOI, download LULC and optionally compute Manning n raster/shapefile.

    When ``per_aoi_configs`` is provided, each AOI uses its own config dict
    (keys mirror the flat parameters above).  The flat parameters act as
    defaults / fallback for backward compatibility.
    """
    project_dir = Path(project_dir)

    # Build a per-AOI config list; fall back to broadcasting flat params.
    _flat = {
        "lulc_source":     lulc_source,
        "cell_size_m":     cell_size_m,
        "lulc_format":     lulc_format,
        "nlcd_year":       nlcd_year,
        "sentinel2_year":  sentinel2_year,
        "do_manning":      do_manning,
        "manning_format":  manning_format,
        "manning_mapping": manning_mapping,
    }
    if per_aoi_configs is None:
        per_aoi_configs = [_flat] * len(features)

    summary = {"features": [], "do_manning": do_manning}
    n = len(features)

    for i, (f, cfg) in enumerate(zip(features, per_aoi_configs), 1):
        log_fn(f"▶ Running [{i}/{n}]: '{f.name}' ...")
        folder = Path(f.folder_path)
        folder.mkdir(parents=True, exist_ok=True)

        # Resolve per-AOI parameters (fall back to flat defaults)
        _src        = cfg.get("lulc_source",    lulc_source)
        _cell       = float(cfg.get("cell_size_m",   cell_size_m))
        _fmt        = cfg.get("lulc_format",    lulc_format)
        _nlcd_yr    = cfg.get("nlcd_year",      nlcd_year)
        _s2_yr      = int(cfg.get("sentinel2_year", sentinel2_year))
        _do_mn      = bool(cfg.get("do_manning",     do_manning))
        _mn_fmt     = cfg.get("manning_format",  manning_format)
        _mn_map     = cfg.get("manning_mapping", manning_mapping) or {}

        aoi_gdf  = get_single_feature_gdf(f.source_file, f.feature_index)
        # Use next_free_path for every output so re-runs create versioned
        # copies (LULC.tif, LULC (1).tif, …) instead of raising "file exists".
        lulc_tif = next_free_path(folder, f"LULC_{f.folder_name}", "tif")

        # 1. Download LULC
        if _src == "nlcd":
            log_fn("Downloading NLCD ...")
            download_nlcd(aoi_gdf, _cell, str(lulc_tif),
                          year=_nlcd_yr, log_fn=log_fn)
        else:
            from core.manning import _download_lulc_to_dem_grid
            log_fn("Downloading ESRI Sentinel-2 LULC ...")
            _make_blank_snap(aoi_gdf, _cell, folder / "_snap.tif")
            _download_lulc_to_dem_grid(
                aoi_gdf, str(folder / "_snap.tif"), _s2_yr,
                str(lulc_tif), log_fn,
            )
            try:
                (folder / "_snap.tif").unlink()
            except Exception:
                pass

        # 2. Export LULC to the user's chosen format.
        #    The TIF written in step 1 (lulc_tif) is ALWAYS kept — it is the
        #    internal working copy used for stats, preview, and Manning.
        #    The format conversion is best-effort: if it fails (e.g. memory
        #    error polygonizing a large NLCD raster to SHP) we log a warning
        #    and fall back to the TIF so Manning and stats still succeed.
        lulc_out_path = str(lulc_tif)   # default = the TIF we just downloaded
        if _fmt == "shp":
            try:
                out_lulc = next_free_path(folder, f"LULC_{f.folder_name}", "shp")
                out_lulc = raster_to_shapefile(str(lulc_tif), str(out_lulc),
                                               field_name="lulc_code", log_fn=log_fn)
                lulc_out_path = str(out_lulc)
            except Exception as _conv_err:
                log_fn(
                    f"  Could not polygonize LULC to SHP ({_conv_err}). "
                    "Keeping TIF as the LULC output."
                )
        elif _fmt != "tif":
            try:
                out_lulc = next_free_path(folder, f"LULC_{f.folder_name}", _fmt)
                out_lulc = export_raster(str(lulc_tif), str(out_lulc), _fmt, _cell, log_fn)
                lulc_out_path = str(out_lulc)
            except Exception as _conv_err:
                log_fn(
                    f"  Could not export LULC to {_fmt.upper()} ({_conv_err}). "
                    "Keeping TIF as the LULC output."
                )

        _year_label = _nlcd_yr if _src == "nlcd" else str(_s2_yr)
        feature_out = {
            "name":        f.name,
            "folder":      f.folder_path,
            "lulc_path":   lulc_out_path,
            "lulc_tif":    str(lulc_tif),   # always TIF for preview
            "lulc_source": _src,             # "nlcd" | "sentinel2"
            "lulc_year":   _year_label,      # e.g. "2021" or "2023"
        }

        # 3. Compute LULC stats (always, since TIF is always written)
        feature_out["lulc_stats"] = _compute_lulc_stats(lulc_tif, _src)

        # 4. Manning (optional per-AOI).
        #    lulc_tif (the internal TIF) was written in step 1 and is never
        #    deleted by the format-conversion in step 2 — so it is always
        #    available here regardless of which LULC output format was chosen.
        if _do_mn:
            manning_tif = next_free_path(folder, f"ManningN_{f.folder_name}", "tif")
            create_manning_from_lulc(str(lulc_tif), str(manning_tif),
                                     _mn_map, log_fn=log_fn)
            feature_out["manning_tif"] = str(manning_tif)  # always TIF for preview
            if _mn_fmt == "tif":
                feature_out["manning_path"] = str(manning_tif)
            elif _mn_fmt == "shp":
                try:
                    manning_shp = next_free_path(folder, f"ManningN_{f.folder_name}", "shp")
                    manning_shp = raster_to_shapefile(str(manning_tif), str(manning_shp),
                                                      field_name="manning_n", log_fn=log_fn)
                    feature_out["manning_path"] = str(manning_shp)
                except Exception as _mn_conv_err:
                    log_fn(
                        f"  Could not polygonize Manning to SHP ({_mn_conv_err}). "
                        "Keeping TIF as the Manning output."
                    )
                    feature_out["manning_path"] = str(manning_tif)
            else:
                try:
                    manning_out = next_free_path(folder, f"ManningN_{f.folder_name}", _mn_fmt)
                    manning_out = export_raster(str(manning_tif), str(manning_out),
                                                _mn_fmt, _cell, log_fn)
                    feature_out["manning_path"] = str(manning_out)
                except Exception as _mn_conv_err:
                    log_fn(
                        f"  Could not export Manning to {_mn_fmt.upper()} "
                        f"({_mn_conv_err}). Keeping TIF as the Manning output."
                    )
                    feature_out["manning_path"] = str(manning_tif)

        # Compact log line
        try:
            lulc_rel = (f"{project_dir.name}/{f.folder_name}/"
                        f"{Path(feature_out['lulc_path']).name}")
        except Exception:
            lulc_rel = feature_out.get("lulc_path", "")
        log_fn(f"✓ Done [{i}/{n}]: {lulc_rel}")
        summary["features"].append(feature_out)

    log_fn(f"All {n} AOI(s) processed successfully.")
    return summary


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_blank_snap(aoi_gdf, cell_size_m, snap_path: Path):
    """Create a tiny blank GeoTIFF aligned to the AOI bounds at cell_size_m.

    Used as the snap raster for the Sentinel-2 downloader when there is no
    DEM available yet (standalone LULC-only mode).
    """
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    # If AOI is geographic, pick a metric CRS for proper cell sizing
    if aoi_gdf.crs is None or aoi_gdf.crs.is_geographic:
        proj = aoi_gdf.to_crs(aoi_gdf.estimate_utm_crs())
    else:
        proj = aoi_gdf
    minx, miny, maxx, maxy = proj.total_bounds
    width = max(1, int((maxx - minx) / cell_size_m))
    height = max(1, int((maxy - miny) / cell_size_m))
    transform = from_origin(minx, maxy, cell_size_m, cell_size_m)

    snap_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        snap_path, "w",
        driver="GTiff", width=width, height=height, count=1,
        dtype="uint8", crs=proj.crs, transform=transform, nodata=0,
    ) as dst:
        dst.write(np.zeros((1, height, width), dtype="uint8"))
