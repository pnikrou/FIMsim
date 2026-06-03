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

    first = summary[0]
    ctx["par_path"]    = first["par_path"]
    ctx["par_per_aoi"] = summary

    try:
        with open(ctx_path, "w", encoding="utf-8") as wf:
            json.dump(ctx, wf, indent=2, default=str)
    except Exception:
        pass

    log_fn(f"🎉 PAR prepared for all {n} AOI(s).")
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
            "name":       feat["name"],
            "folder":     folder,
            "bdy_path":   feat_ctx.get("bdy_path"),
            "bdy_source": feat_ctx.get("bdy_source"),
            "written":    feat_ctx.get("bdy_written", False),
            "helper_csv": helper_csv,
        })
        log_fn(f"✓ BDY [{i}/{n}] finished: '{feat['name']}'")

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

    first = summary[0]
    ctx["bdy_path"]    = first["bdy_path"]
    ctx["bdy_source"]  = first["bdy_source"]
    ctx["bdy_written"] = first["written"]
    ctx["bdy_per_aoi"] = summary

    try:
        with open(ctx_path, "w", encoding="utf-8") as wf:
            json.dump(ctx, wf, indent=2, default=str)
    except Exception:
        pass

    log_fn(f"🎉 BDY prepared for all {n} AOI(s).")
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
            "name":            feat["name"],
            "folder":          folder,
            "bci_path":        feat_ctx.get("bci_path"),
            "upstream_mode":   feat_ctx.get("upstream_mode"),
            "downstream_type": feat_ctx.get("downstream_type"),
            "river":           feat_ctx.get("main_river_name"),
            # Coordinates (in the AOI CRS) so the GUI can draw stars on
            # the post-run preview map.
            "upstream_x":      feat_ctx.get("upstream_x"),
            "upstream_y":      feat_ctx.get("upstream_y"),
            "downstream_x":    feat_ctx.get("downstream_x"),
            "downstream_y":    feat_ctx.get("downstream_y"),
            # Files needed by the preview map
            "source_file":     feat["source_file"],
            "feature_index":   feat["feature_index"],
            "main_river_line": str(Path(folder) / "main_river_line.gpkg"),
            "flowlines_path":  feat_ctx.get("flowlines_path"),
        })
        log_fn(f"✓ BCI [{i}/{n}] finished: '{feat['name']}'")

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

    first = summary[0]
    ctx["bci_path"]         = first["bci_path"]
    ctx["upstream_mode"]    = first["upstream_mode"]
    ctx["downstream_type"]  = first["downstream_type"]
    ctx["bci_per_aoi"]      = summary

    try:
        with open(ctx_path, "w", encoding="utf-8") as wf:
            json.dump(ctx, wf, indent=2, default=str)
    except Exception:
        pass

    log_fn(f"🎉 BCI prepared for all {n} AOI(s).")
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

    first = summary[0]
    ctx["fric_mode"]            = first["fric_mode"]
    ctx["manning_ascii_path"]   = first["manning_ascii"]
    ctx["par_fpfric"]           = first["fpfric"]
    ctx["manning_per_aoi"]      = summary

    try:
        with open(ctx_path, "w", encoding="utf-8") as wf:
            json.dump(ctx, wf, indent=2, default=str)
    except Exception:
        pass

    log_fn(f"🎉 Manning prepared for all {n} AOI(s).")
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

    first = summary_paths[0]
    # Parent ctx mirrors the FIRST AOI's outputs / source choice so the
    # legacy single-AOI downstream steps still read sensible values.
    if per_aoi_configs is not None:
        first_cfg = per_aoi_configs[0] or {}
        first_has_dem = bool(first_cfg.get("has_dem", False))
    else:
        first_has_dem = has_dem
    ctx["dem_path"]       = first["dem_tif"]
    ctx["dem_tif_path"]   = first["dem_tif"]
    ctx["dem_ascii_path"] = first["dem_ascii"]
    # Parent ctx mirrors the FIRST AOI's cell size for the legacy
    # single-AOI bridge.  Per-AOI cell sizes are preserved in dem_per_aoi
    # so downstream steps can still find their own AOI's value if needed.
    ctx["dem_res_m"]      = float(first.get("cell_m", dem_res_m))
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

    log_fn(f"🎉 All {n} AOI(s) processed successfully.")
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
    log_fn(f"🎉 All {n} AOI(s) processed successfully.")
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
                    f"  ⚠ Could not polygonize LULC to SHP ({_conv_err}). "
                    "Keeping TIF as the LULC output."
                )
        elif _fmt != "tif":
            try:
                out_lulc = next_free_path(folder, f"LULC_{f.folder_name}", _fmt)
                out_lulc = export_raster(str(lulc_tif), str(out_lulc), _fmt, _cell, log_fn)
                lulc_out_path = str(out_lulc)
            except Exception as _conv_err:
                log_fn(
                    f"  ⚠ Could not export LULC to {_fmt.upper()} ({_conv_err}). "
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
                        f"  ⚠ Could not polygonize Manning to SHP ({_mn_conv_err}). "
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
                        f"  ⚠ Could not export Manning to {_mn_fmt.upper()} "
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

    log_fn(f"🎉 All {n} AOI(s) processed successfully.")
    return summary


# ── HEC-RAS mode ──────────────────────────────────────────────────────────────

def run_hecras_mode(
    project_dir: str,
    features: List[AOIFeatureInfo],
    dem_cell_size_m: float,
    lulc_source: str,
    lulc_cell_size_m: float,
    nlcd_year: str = "2021",
    sentinel2_year: int = 2023,
    manning_mapping: Optional[Dict[int, float]] = None,
    log_fn=print,
) -> Dict:
    """Per AOI, produce 4 files in a HECRAS_files subfolder:

      dem.tif
      manning_n.shp
      flowline.shp
      Geometry_Shapefile/geometry.shp
    """
    import ssl
    from core.bci import _build_main_river  # reuse main-river detection

    project_dir = Path(project_dir)
    summary = {"features": []}
    n = len(features)

    for i, f in enumerate(features, 1):
        log_fn(f"▶ Running [{i}/{n}]: '{f.name}' ...")
        folder = Path(f.folder_path) / "HECRAS_files"
        folder.mkdir(parents=True, exist_ok=True)

        # 1. DEM (TIF only)
        ctx = _make_feature_ctx(project_dir, f)
        ctx["lisflood_dir"] = str(folder)
        ctx["model_dir"] = str(folder)
        ctx_path = _save_feature_ctx(ctx)
        ctx = prepare_dem(
            ctx_path=str(ctx_path), ctx=ctx,
            dem_res_m=dem_cell_size_m,
            has_dem=False, user_dem_path=None,
            log_fn=log_fn,
        )
        dem_tif_src = Path(ctx["dem_tif_path"])
        dem_out = next_free_path(folder, "dem", "tif")
        if dem_tif_src.resolve() != dem_out.resolve():
            shutil.copy2(dem_tif_src, dem_out)

        # 2. LULC + Manning shapefile
        aoi_gdf = get_single_feature_gdf(f.source_file, f.feature_index)
        lulc_tif = next_free_path(folder, "lulc", "tif")
        if lulc_source == "nlcd":
            download_nlcd(aoi_gdf, lulc_cell_size_m, str(lulc_tif),
                          year=nlcd_year, log_fn=log_fn)
        else:
            from core.manning import _download_lulc_to_dem_grid
            _make_blank_snap(aoi_gdf, lulc_cell_size_m, folder / "_snap.tif")
            _download_lulc_to_dem_grid(
                aoi_gdf, str(folder / "_snap.tif"), int(sentinel2_year),
                str(lulc_tif), log_fn,
            )
            try:
                (folder / "_snap.tif").unlink()
            except Exception:
                pass

        manning_tif = next_free_path(folder, "manning_n", "tif")
        create_manning_from_lulc(str(lulc_tif), str(manning_tif),
                                 manning_mapping or {}, log_fn=log_fn)
        manning_shp = next_free_path(folder, "manning_n", "shp")
        try:
            manning_shp = raster_to_shapefile(str(manning_tif), str(manning_shp),
                                              field_name="manning_n", log_fn=log_fn)
        except Exception as _e:
            log_fn(f"  ⚠ Could not polygonize Manning to SHP ({_e}). Skipping manning_n.shp.")
            manning_shp = None

        # 3. NHD main-river flowline as shapefile
        flowline_shp = next_free_path(folder, "flowline", "shp")
        try:
            ssl._create_default_https_context = ssl._create_unverified_context
            from pynhd import NHD
            aoi_4326 = aoi_gdf.to_crs(4326)
            geom_ll = aoi_4326.geometry.union_all() if hasattr(aoi_4326.geometry, "union_all") \
                      else aoi_4326.unary_union
            nhd = NHD("flowline_mr")
            flowlines = nhd.bygeom(geom_ll)
            if flowlines is not None and not flowlines.empty:
                flowlines = flowlines.to_crs(aoi_gdf.crs)
                clipped = gpd.overlay(flowlines, aoi_gdf[["geometry"]], how="intersection")
                clipped = clipped[clipped.geometry.type.isin(["LineString", "MultiLineString"])]
                if not clipped.empty:
                    main_segments, summary_df, main_line, main_river_name, main_order, _ = \
                        _build_main_river(clipped)
                    main_gdf = gpd.GeoDataFrame(
                        [{"river_name": main_river_name,
                          "stream_order": int(main_order)}],
                        geometry=[main_line], crs=aoi_gdf.crs,
                    )
                    flowline_shp = gdf_to_shapefile(main_gdf, str(flowline_shp), log_fn=log_fn)
                    log_fn(f"Flowline saved: {main_river_name} (order {main_order})")
                else:
                    log_fn("No NHD flowlines after clipping — skipping flowline.shp")
            else:
                log_fn("No NHD flowlines found — skipping flowline.shp")
        except Exception as ex:
            log_fn(f"NHD lookup failed: {ex} — skipping flowline.shp")

        # 4. Geometry polygon (clipped AOI) as shapefile
        geom_dir = folder / "Geometry_Shapefile"
        geom_dir.mkdir(parents=True, exist_ok=True)
        geom_shp = next_free_path(geom_dir, "geometry", "shp")
        geom_shp = gdf_to_shapefile(aoi_gdf, str(geom_shp), log_fn=log_fn)

        summary["features"].append({
            "name": f.name, "folder": str(folder),
            "dem": str(dem_out), "manning": str(manning_shp) if manning_shp else None,
            "flowline": str(flowline_shp) if Path(flowline_shp).exists() else None,
            "geometry": str(geom_shp),
        })
        hecras_rel = f"{project_dir.name}/{f.folder_name}/HECRAS_files/"
        log_fn(f"✓ Done [{i}/{n}]: {hecras_rel}")
    log_fn(f"🎉 All {n} AOI(s) processed successfully.")
    return summary


# ── HEC-RAS wizard step helpers ───────────────────────────────────────────────

def _buffer_aoi_for_hecras(aoi_gdf, buffer_m: float, tmp_path: Path):
    """Buffer AOI geometry by buffer_m metres, save to tmp_path as shapefile.
    Returns (buffered_gdf, tmp_path). If buffer_m <= 0 saves original."""
    from core.crs_utils import pick_working_crs_epsg
    if buffer_m > 0:
        epsg = pick_working_crs_epsg(aoi_gdf)
        aoi_m = aoi_gdf.to_crs(epsg=int(epsg))
        buf = aoi_m.copy()
        buf["geometry"] = aoi_m.geometry.buffer(float(buffer_m))
        buffered = buf.to_crs(aoi_gdf.crs)
    else:
        buffered = aoi_gdf.copy()
    for sib in tmp_path.parent.glob(tmp_path.stem + ".*"):
        try: sib.unlink()
        except Exception: pass
    buffered.to_file(tmp_path, driver="ESRI Shapefile")
    return buffered, tmp_path


def run_hecras_dem(
    project_dir: str,
    features: List[AOIFeatureInfo],
    dem_cell_size_m: float,
    dem_buffer_m: float = 500.0,
    log_fn=print,
) -> Dict:
    """Download DEM (expanded by dem_buffer_m metres) for each AOI.
    Saves HECRAS_files/dem.tif. Returns summary with 'dem_per_aoi' list."""
    project_dir = Path(project_dir)
    summary: Dict = {"features": [], "dem_per_aoi": []}
    n = len(features)
    for i, f in enumerate(features, 1):
        log_fn(f"▶ Downloading DEM [{i}/{n}]: '{f.name}' ...")
        folder = Path(f.folder_path) / "HECRAS_files"
        folder.mkdir(parents=True, exist_ok=True)

        aoi_gdf = get_single_feature_gdf(f.source_file, f.feature_index)
        tmp_shp = folder / "_aoi_buf_dem.shp"
        _buffer_aoi_for_hecras(aoi_gdf, dem_buffer_m, tmp_shp)

        ctx = _make_feature_ctx(project_dir, f)
        ctx["lisflood_dir"] = str(folder)
        ctx["model_dir"]    = str(folder)
        ctx["aoi_path"]     = str(tmp_shp)
        ctx["aoi_feature_index"] = 0
        if getattr(f, "working_crs_epsg", None) is not None:
            ctx["working_crs_epsg"] = int(f.working_crs_epsg)
        if getattr(f, "working_crs_label", None):
            ctx["working_crs_label"] = f.working_crs_label
        ctx_path = str(folder / "hecras_dem_ctx.json")

        ctx = prepare_dem(
            ctx_path=ctx_path, ctx=ctx,
            dem_res_m=dem_cell_size_m,
            has_dem=False, user_dem_path=None,
            dem_source="3dep", skip_ascii=True,
            log_fn=log_fn,
        )
        dem_src = Path(ctx.get("dem_tif_path", ""))
        dem_out = folder / "dem.tif"
        if dem_src.exists() and dem_src.resolve() != dem_out.resolve():
            shutil.copy2(dem_src, dem_out)
        if not dem_out.exists():
            dem_out = dem_src

        for sib in folder.glob("_aoi_buf_dem.*"):
            try: sib.unlink()
            except Exception: pass

        entry = {"name": f.name, "folder": str(folder),
                 "dem_tif": str(dem_out), "cell_m": dem_cell_size_m,
                 "source_file": f.source_file, "feature_index": f.feature_index}
        summary["dem_per_aoi"].append(entry)
        summary["features"].append(entry)
        log_fn(f"✓ DEM [{i}/{n}] finished: '{f.name}'")
    log_fn(f"🎉 DEM prepared for all {n} AOI(s).")
    return summary


def run_hecras_manning(
    project_dir: str,
    features: List[AOIFeatureInfo],
    lulc_source: str = "nlcd",
    lulc_cell_size_m: float = 30.0,
    dem_buffer_m: float = 500.0,
    nlcd_year: str = "2021",
    sentinel2_year: int = 2023,
    manning_mapping: Optional[Dict[int, float]] = None,
    log_fn=print,
) -> Dict:
    """Download LULC (with buffer) → Manning raster → manning_n.shp for HEC-RAS.
    Saves HECRAS_files/lulc.tif + HECRAS_files/manning_n.shp (+ manning_n.tif).
    Returns summary with 'manning_per_aoi' list."""
    project_dir = Path(project_dir)
    summary: Dict = {"features": [], "manning_per_aoi": []}
    n = len(features)
    for i, f in enumerate(features, 1):
        log_fn(f"▶ Manning [{i}/{n}]: '{f.name}' ...")
        folder = Path(f.folder_path) / "HECRAS_files"
        folder.mkdir(parents=True, exist_ok=True)

        aoi_gdf = get_single_feature_gdf(f.source_file, f.feature_index)
        tmp_shp = folder / "_aoi_buf_mn.shp"
        buf_gdf, _ = _buffer_aoi_for_hecras(aoi_gdf, dem_buffer_m, tmp_shp)

        lulc_tif = folder / "lulc.tif"
        if lulc_tif.exists():
            try: lulc_tif.unlink()
            except Exception: pass

        if lulc_source == "nlcd":
            log_fn("  Downloading NLCD ...")
            download_nlcd(buf_gdf, lulc_cell_size_m, str(lulc_tif),
                          year=nlcd_year, log_fn=log_fn)
        else:
            from core.manning import _download_lulc_to_dem_grid
            log_fn("  Downloading ESRI Sentinel-2 LULC ...")
            snap_tif = folder / "_snap_mn.tif"
            _make_blank_snap(buf_gdf, lulc_cell_size_m, snap_tif)
            _download_lulc_to_dem_grid(buf_gdf, str(snap_tif),
                                       int(sentinel2_year), str(lulc_tif), log_fn)
            try: snap_tif.unlink(missing_ok=True)
            except Exception: pass

        manning_tif = folder / "manning_n.tif"
        create_manning_from_lulc(str(lulc_tif), str(manning_tif),
                                 manning_mapping or {}, log_fn=log_fn)

        manning_shp = folder / "manning_n.shp"
        for sib in folder.glob("manning_n.*"):
            if sib.suffix in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
                try: sib.unlink()
                except Exception: pass
        try:
            manning_shp = raster_to_shapefile(str(manning_tif), str(manning_shp),
                                              field_name="manning_n", log_fn=log_fn)
        except Exception as _e:
            log_fn(f"  ⚠ Could not polygonize Manning ({_e}). Keeping TIF only.")
            manning_shp = None

        for sib in folder.glob("_aoi_buf_mn.*"):
            try: sib.unlink()
            except Exception: pass

        lulc_src_tag = "download_nlcd" if lulc_source == "nlcd" else "download_esri"
        entry = {
            "name":        f.name,
            "folder":      str(folder),
            "manning_shp": str(manning_shp) if manning_shp else None,
            "manning_tif": str(manning_tif) if manning_tif.exists() else None,
            "lulc_tif":    str(lulc_tif) if lulc_tif.exists() else None,
            "lulc_source": lulc_src_tag,
            "source_file": f.source_file,
            "feature_index": f.feature_index,
        }
        summary["manning_per_aoi"].append(entry)
        summary["features"].append(entry)
        log_fn(f"✓ Manning [{i}/{n}] finished: '{f.name}'")
    log_fn(f"🎉 Manning prepared for all {n} AOI(s).")
    return summary


def run_hecras_flowline(
    project_dir: str,
    features: List[AOIFeatureInfo],
    dem_summary: Optional[Dict] = None,
    log_fn=print,
) -> Dict:
    """Query NHD, detect main river, find upstream/downstream via DEM elevation,
    detect NWM feature ID, save HECRAS_files/flowline.shp.
    Returns summary with 'flowline_per_aoi' (includes upstream_xy, downstream_xy,
    upstream_reach_id, main_river_line path)."""
    import ssl
    from core.bci import _build_main_river, _mean_end_elevation, _choose_fid_col

    ssl._create_default_https_context = ssl._create_unverified_context
    project_dir = Path(project_dir)
    summary: Dict = {"features": [], "flowline_per_aoi": []}
    n = len(features)

    dem_lookup: dict = {}
    if dem_summary:
        for de in dem_summary.get("dem_per_aoi", []):
            dem_lookup[de["name"]] = de.get("dem_tif")

    for i, f in enumerate(features, 1):
        log_fn(f"▶ Flowline [{i}/{n}]: '{f.name}' ...")
        folder = Path(f.folder_path) / "HECRAS_files"
        folder.mkdir(parents=True, exist_ok=True)

        aoi_gdf = get_single_feature_gdf(f.source_file, f.feature_index)
        dem_tif = dem_lookup.get(f.name) or str(folder / "dem.tif")

        entry: dict = {
            "name":              f.name,
            "folder":            str(folder),
            "source_file":       f.source_file,
            "feature_index":     f.feature_index,
            "upstream_xy":       None,
            "downstream_xy":     None,
            "upstream_reach_id": None,
            "main_river_line":   str(folder / "main_river_line.gpkg"),
            "flowline_shp":      None,
            "river_name":        None,
        }

        try:
            from pynhd import NHD
            aoi_ll  = aoi_gdf.to_crs("EPSG:4326")
            geom_ll = (aoi_ll.geometry.union_all()
                       if hasattr(aoi_ll.geometry, "union_all")
                       else aoi_ll.unary_union)
            nhd = NHD("flowline_mr")
            try:
                flowlines = nhd.bygeom(geom_ll)
            except Exception as _ex:
                msg = str(_ex)
                if "should be of type" in msg or "MultiPolygon" in msg:
                    flowlines = nhd.bygeom(tuple(geom_ll.bounds))
                else:
                    raise
            if flowlines is None or flowlines.empty:
                raise RuntimeError("No NHD flowlines found for this AOI.")

            flowlines      = flowlines.to_crs(aoi_gdf.crs)
            flowlines_clip = gpd.overlay(flowlines, aoi_gdf[["geometry"]],
                                         how="intersection")
            flowlines_clip = flowlines_clip[
                flowlines_clip.geometry.type.isin(["LineString", "MultiLineString"])
            ].copy()
            if flowlines_clip.empty:
                raise RuntimeError("No flowlines remain after clipping to AOI.")

            (main_segments, _, main_line,
             main_river_name, main_order, _) = _build_main_river(flowlines_clip)
            log_fn(f"  Main river: {main_river_name} (order {main_order})")

            # Save main_river_line.gpkg for preview map
            river_gpkg = folder / "main_river_line.gpkg"
            if river_gpkg.exists():
                try: river_gpkg.unlink()
                except Exception: pass
            gpd.GeoDataFrame(
                [{"river_name": main_river_name, "stream_order": int(main_order)}],
                geometry=[main_line], crs=flowlines_clip.crs,
            ).to_file(river_gpkg, driver="GPKG")

            # Save flowline.shp
            flowline_shp = folder / "flowline.shp"
            for sib in folder.glob("flowline.*"):
                if sib.suffix in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
                    try: sib.unlink()
                    except Exception: pass
            gpd.GeoDataFrame(
                [{"river_name": main_river_name, "stream_order": int(main_order)}],
                geometry=[main_line], crs=flowlines_clip.crs,
            ).to_file(flowline_shp, driver="ESRI Shapefile")
            log_fn(f"  Saved: {flowline_shp.name}")
            entry["flowline_shp"] = str(flowline_shp)
            entry["river_name"]   = main_river_name

            # Reproject to DEM CRS for elevation sampling
            import rasterio as _rio
            dem_crs = dem_cell_size = None
            if Path(dem_tif).exists():
                with _rio.open(dem_tif) as _ds:
                    dem_crs       = _ds.crs
                    dem_cell_size = float(abs(_ds.res[0]))

            flowline_crs = flowlines_clip.crs
            _need_repr = (dem_crs is not None and flowline_crs is not None
                          and dem_crs.to_epsg() != flowline_crs.to_epsg())
            if _need_repr:
                from pyproj import Transformer
                from shapely.ops import transform as _st
                _tf = Transformer.from_crs(flowline_crs, dem_crs, always_xy=True)
                main_line_dem = _st(_tf.transform, main_line)
                log_fn(f"  Reprojected: {flowline_crs.to_epsg()} → {dem_crs.to_epsg()}")
            else:
                main_line_dem = main_line
                _need_repr = False

            if Path(dem_tif).exists() and dem_cell_size is not None:
                from shapely.geometry import Point as _Pt
                coords = list(main_line_dem.coords)
                end1, end2 = _Pt(coords[0]), _Pt(coords[-1])
                mean1, mean2 = _mean_end_elevation(main_line_dem, dem_tif, dem_cell_size)
                if mean1 >= mean2:
                    up_dem, dn_dem = end1, end2
                else:
                    up_dem, dn_dem = end2, end1

                if _need_repr:
                    from pyproj import Transformer
                    _tf_b = Transformer.from_crs(dem_crs, flowline_crs, always_xy=True)
                    up_pt = _Pt(*_tf_b.transform(up_dem.x, up_dem.y))
                    dn_pt = _Pt(*_tf_b.transform(dn_dem.x, dn_dem.y))
                    _up_fl = _Pt(*_tf_b.transform(up_dem.x, up_dem.y))
                else:
                    up_pt = up_dem; dn_pt = dn_dem; _up_fl = up_dem

                entry["upstream_xy"]   = (float(up_pt.x), float(up_pt.y))
                entry["downstream_xy"] = (float(dn_pt.x), float(dn_pt.y))
                log_fn(f"  Upstream:   ({up_pt.x:.4f}, {up_pt.y:.4f})")
                log_fn(f"  Downstream: ({dn_pt.x:.4f}, {dn_pt.y:.4f})")

                fid_col = _choose_fid_col(main_segments)
                if fid_col:
                    main_segments["_dist"] = main_segments.geometry.distance(_up_fl)
                    reach_id = int(main_segments.nsmallest(1, "_dist").iloc[0][fid_col])
                    entry["upstream_reach_id"] = reach_id
                    log_fn(f"  NWM upstream reach ID: {reach_id}")
            else:
                log_fn("  ⚠ DEM not found — skipping elevation-based detection.")

        except Exception as ex:
            log_fn(f"  ⚠ Flowline failed for '{f.name}': {ex}")

        summary["flowline_per_aoi"].append(entry)
        summary["features"].append(entry)
        log_fn(f"✓ Flowline [{i}/{n}] finished: '{f.name}'")

    log_fn(f"🎉 Flowline prepared for all {n} AOI(s).")
    return summary


def run_hecras_flowdata(
    project_dir: str,
    features: List[AOIFeatureInfo],
    per_aoi_configs: list,
    flowline_summary: Optional[Dict] = None,
    log_fn=print,
) -> Dict:
    """Download discharge (NWM or USGS) as CSV for HEC-RAS.
    For NWM: if no feature_ids supplied, auto-uses upstream_reach_id from flowline_summary.
    Saves HECRAS_files/discharge.csv. Returns summary with 'flowdata_per_aoi'."""
    import pandas as _pd
    from datetime import datetime as _dt

    project_dir = Path(project_dir)
    summary: Dict = {"features": [], "flowdata_per_aoi": []}
    n = len(features)

    reach_lookup: dict = {}
    if flowline_summary:
        for fe in flowline_summary.get("flowline_per_aoi", []):
            rid = fe.get("upstream_reach_id")
            if rid is not None:
                reach_lookup[fe["name"]] = rid

    RETRO_END = _dt(2020, 12, 31, 23, 59)

    for i, (f, cfg) in enumerate(zip(features, per_aoi_configs), 1):
        log_fn(f"▶ Flowdata [{i}/{n}]: '{f.name}' ...")
        folder = Path(f.folder_path) / "HECRAS_files"
        folder.mkdir(parents=True, exist_ok=True)
        flow_source = cfg.get("flow_source", "nwm")
        entry: dict = {"name": f.name, "folder": str(folder),
                       "flow_source": flow_source, "csv_path": None}
        try:
            if flow_source == "nwm":
                from core.nwm_discharge import (download_nwm_retrospective,
                                                 download_nwm_forecast,
                                                 _coerce_feature_ids)
                raw_fids = (cfg.get("feature_ids") or "").strip()
                if not raw_fids:
                    rid = reach_lookup.get(f.name)
                    if rid is not None:
                        raw_fids = str(rid)
                        log_fn(f"  Auto feature ID from flowline step: {rid}")
                    else:
                        raise ValueError("No NWM feature IDs and none found in flowline step.")
                fids = _coerce_feature_ids(raw_fids)
                if not fids:
                    raise ValueError("No valid feature IDs.")

                start_dt  = cfg.get("event_start_dt")
                end_dt    = cfg.get("event_end_dt")
                interval  = float(cfg.get("interval_hours", 1.0))
                use_fcst  = (end_dt and hasattr(end_dt, "year") and end_dt > RETRO_END)

                tmp_csv = folder / "_nwm_flowdata_tmp.csv"
                if use_fcst:
                    log_fn("  NWM operational forecast ...")
                    download_nwm_forecast(
                        fids, tmp_csv,
                        run_date=cfg.get("forecast_run_date"),
                        cycle_hour=int(cfg.get("forecast_cycle", 0)),
                        forecast_set=cfg.get("forecast_set", "medium_range_mem1"),
                        log_fn=log_fn,
                    )
                else:
                    log_fn("  NWM retrospective ...")
                    download_nwm_retrospective(
                        fids, start_dt, end_dt, interval, tmp_csv, log_fn=log_fn,
                    )
                df_all = _pd.read_csv(tmp_csv, index_col=0)
                out_csv = folder / "discharge.csv"
                col = str(int(fids[0]))
                df_out = (df_all[[col]].rename(columns={col: "streamflow_m3s"})
                          if col in df_all.columns else df_all)
                df_out.index.name = "datetime"
                df_out.to_csv(out_csv)
                tmp_csv.unlink(missing_ok=True)
                entry["csv_path"] = str(out_csv)
                log_fn(f"  ✓ Saved: {out_csv.name}")

            elif flow_source == "usgs":
                from core.flowline_mode import _download_usgs_discharge, _coerce_gage_ids
                gage_ids = _coerce_gage_ids(cfg.get("gage_ids", ""))
                if not gage_ids:
                    raise ValueError("No USGS gage IDs.")
                out_csvs = _download_usgs_discharge(
                    gage_ids, cfg.get("event_start_dt"), cfg.get("event_end_dt"),
                    folder, interval_hours=float(cfg.get("usgs_interval_hours", 1.0)),
                    log_fn=log_fn,
                )
                if out_csvs:
                    entry["csv_path"] = out_csvs[0]
        except Exception as ex:
            log_fn(f"  ⚠ Flowdata failed for '{f.name}': {ex}")

        summary["flowdata_per_aoi"].append(entry)
        summary["features"].append(entry)
        log_fn(f"✓ Flowdata [{i}/{n}] finished: '{f.name}'")

    log_fn(f"🎉 Discharge data prepared for all {n} AOI(s).")
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
