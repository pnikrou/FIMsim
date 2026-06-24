"""Per-AOI orchestrators for the ARC-Curve2Flood workflow.

Kept fully separate from orchestrate.py and triton_orchestrate.py so the
three models never share workflow code.  Blocking — call from a Worker thread.
"""
import json
from pathlib import Path

from core.dem import prepare_dem
from core.multi_aoi import AOIFeatureInfo
from core.arc_manning import prepare_arc_manning


# ── helpers ───────────────────────────────────────────────────────────────────

def _arc_model_dir(aoi_folder: str) -> str:
    """Return (and create) the arc-files subdirectory for one AOI."""
    d = Path(aoi_folder) / "arc-files"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def _arc_dem_dir(aoi_folder: str) -> str:
    """Return (and create) the dem/ subdirectory for one AOI."""
    d = Path(aoi_folder) / "dem"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


# ── DEM ───────────────────────────────────────────────────────────────────────

def run_arc_dem_all(
    ctx_path: str,
    ctx: dict,
    dem_res_m: float = 10.0,
    has_dem: bool = False,
    user_dem_path=None,
    per_aoi_configs: list = None,
    log_fn=print,
) -> dict:
    """Download / import a DEM GeoTIFF for every confirmed ARC AOI.

    ARC-Curve2Flood does NOT need an ASCII grid — NenCarta reads the raw
    GeoTIFF tiles from ``<AOI>/dem/``.  So this orchestrator calls
    prepare_dem but the result stored is ``dem_tif_path`` only.

    Emits ``▶ Downloading DEM [N/M]`` / ``✓ DEM [N/M] finished`` lines so
    the GUI progress bar works identically to the TRITON DEM step.
    """
    aoi_features = ctx.get("aoi_features", [])
    if not aoi_features:
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

    summary_paths = []
    for i, feat in enumerate(aoi_features, 1):
        try:
            log_fn(f"▶ Downloading DEM [{i}/{n}]: '{feat['name']}' ...")
            folder = feat["folder_path"]
            Path(folder).mkdir(parents=True, exist_ok=True)

            # Resolve per-AOI settings
            if per_aoi_configs is not None:
                cfg = per_aoi_configs[i - 1] or {}
                this_has_dem  = bool(cfg.get("has_dem", False))
                this_user_paths = cfg.get("user_dem_path") or None
                this_res_m    = float(cfg.get("dem_res_m", dem_res_m))
            else:
                this_has_dem  = has_dem
                this_user_paths = user_dem_path
                this_res_m    = float(dem_res_m)

            arc_dir = _arc_model_dir(folder)
            dem_dir = _arc_dem_dir(folder)

            feat_ctx = dict(ctx)
            feat_ctx["aoi_path"]          = feat["source_file"]
            feat_ctx["aoi_name"]          = feat["folder_name"]
            feat_ctx["aoi_feature_index"] = feat["feature_index"]
            if feat.get("working_crs_epsg") is not None:
                feat_ctx["working_crs_epsg"]  = feat["working_crs_epsg"]
            if feat.get("working_crs_label"):
                feat_ctx["working_crs_label"] = feat["working_crs_label"]
            feat_ctx["project_dir"] = folder
            feat_ctx["arc_dir"]     = arc_dir
            feat_ctx["dem_dir"]     = dem_dir
            feat_ctx["model_dir"]   = dem_dir

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

            # Save per-AOI workflow_context so downstream steps can find paths
            try:
                with open(feat_ctx_path, "w", encoding="utf-8") as wf:
                    json.dump(feat_ctx, wf, indent=2, default=str)
            except Exception:
                pass

            summary_paths.append({
                "name":    feat["name"],
                "folder":  folder,
                "dem_tif": feat_ctx.get("dem_tif_path"),
                "cell_m":  this_res_m,
            })
            log_fn(f"✓ DEM [{i}/{n}] finished: '{feat['name']}'")

        except Exception as _aoi_exc:
            import traceback
            log_fn(f"✗ DEM [{i}/{n}] ERROR for '{feat['name']}': {_aoi_exc}")
            log_fn(traceback.format_exc())
            summary_paths.append({
                "name":   feat.get("name", f"AOI {i}"),
                "failed": True,
                "error":  str(_aoi_exc),
            })

    # Rewire parent ctx bridge keys to the FIRST AOI
    f0     = aoi_features[0]
    folder0 = f0["folder_path"]
    ctx["aoi_path"]          = f0["source_file"]
    ctx["aoi_name"]          = f0["folder_name"]
    ctx["aoi_feature_index"] = f0["feature_index"]
    ctx["arc_dir"]           = _arc_model_dir(folder0)
    ctx["dem_dir"]           = _arc_dem_dir(folder0)
    ctx["model_dir"]         = ctx["dem_dir"]

    # Expose the per-AOI summary so the GUI can build the clickable list
    ctx["dem_per_aoi"] = summary_paths

    # Save parent ctx
    try:
        with open(ctx_path, "w", encoding="utf-8") as wf:
            json.dump(ctx, wf, indent=2, default=str)
    except Exception:
        pass

    return ctx


# ── Manning ───────────────────────────────────────────────────────────────────

def run_arc_manning_for_all_aois(
    ctx_path: str,
    ctx: dict,
    per_aoi_configs: list,
    log_fn=print,
) -> dict:
    """Download LULC + write mannings_n.txt for every confirmed ARC AOI.

    Each entry in per_aoi_configs is a kwargs dict for prepare_arc_manning
    (fric_mode, fpfric_val, lulc_source, lulc_year, nlcd_year,
     lulc_class_to_n, user_lulc_path, dem_res_m).

    Emits ``▶ Manning [N/M]`` / ``✓ Manning [N/M] finished`` log lines.
    """
    aoi_features = ctx.get("aoi_features", [])
    if not aoi_features:
        cfg = per_aoi_configs[0] if per_aoi_configs else {}
        return prepare_arc_manning(ctx_path=ctx_path, ctx=ctx, log_fn=log_fn, **cfg)

    n = len(aoi_features)
    if len(per_aoi_configs) != n:
        raise RuntimeError(
            f"per_aoi_configs has {len(per_aoi_configs)} entries but "
            f"there are {n} AOIs."
        )

    summary = []
    for i, feat in enumerate(aoi_features, 1):
        try:
            log_fn(f"▶ Manning [{i}/{n}]: '{feat['name']}' ...")
            folder = feat["folder_path"]
            Path(folder).mkdir(parents=True, exist_ok=True)

            arc_dir = _arc_model_dir(folder)
            dem_dir = _arc_dem_dir(folder)

            feat_ctx = dict(ctx)
            feat_ctx["aoi_path"]          = feat["source_file"]
            feat_ctx["aoi_name"]          = feat["folder_name"]
            feat_ctx["aoi_feature_index"] = feat["feature_index"]
            if feat.get("working_crs_epsg") is not None:
                feat_ctx["working_crs_epsg"]  = feat["working_crs_epsg"]
            if feat.get("working_crs_label"):
                feat_ctx["working_crs_label"] = feat["working_crs_label"]
            feat_ctx["project_dir"] = folder
            feat_ctx["arc_dir"]     = arc_dir
            feat_ctx["dem_dir"]     = dem_dir
            feat_ctx["model_dir"]   = dem_dir

            # Pull DEM path from per-AOI workflow_context.json
            feat_ctx_path = str(Path(folder) / "workflow_context.json")
            if Path(feat_ctx_path).exists():
                try:
                    with open(feat_ctx_path, "r", encoding="utf-8") as fr:
                        saved = json.load(fr)
                    for k in ("dem_tif_path", "dem_path", "dem_res_m"):
                        if k in saved:
                            feat_ctx[k] = saved[k]
                except Exception:
                    pass

            cfg = per_aoi_configs[i - 1] or {}
            feat_ctx = prepare_arc_manning(
                ctx_path=feat_ctx_path, ctx=feat_ctx, log_fn=log_fn, **cfg
            )

            # Save updated per-AOI context
            try:
                with open(feat_ctx_path, "w", encoding="utf-8") as wf:
                    json.dump(feat_ctx, wf, indent=2, default=str)
            except Exception:
                pass

            summary.append({
                "name":            feat["name"],
                "folder":          folder,
                "fric_mode":       feat_ctx.get("arc_fric_mode", "varying"),
                "fpfric":          feat_ctx.get("arc_fpfric"),
                "lulc_tif":        feat_ctx.get("arc_lulc_tif_path"),
                "mannings_n_path": feat_ctx.get("arc_mannings_n_path"),
                "lulc_source":     feat_ctx.get("lulc_source"),
            })
            log_fn(f"✓ Manning [{i}/{n}] finished: '{feat['name']}'")

        except Exception as _exc:
            import traceback
            log_fn(f"✗ Manning [{i}/{n}] ERROR for '{feat['name']}': {_exc}")
            log_fn(traceback.format_exc())
            summary.append({
                "name":   feat.get("name", f"AOI {i}"),
                "failed": True,
                "error":  str(_exc),
            })

    # Rewire parent ctx to first AOI
    f0 = aoi_features[0]
    folder0 = f0["folder_path"]
    ctx["aoi_path"]  = f0["source_file"]
    ctx["aoi_name"]  = f0["folder_name"]
    ctx["arc_dir"]   = _arc_model_dir(folder0)
    ctx["dem_dir"]   = _arc_dem_dir(folder0)
    ctx["arc_manning_per_aoi"] = summary

    try:
        with open(ctx_path, "w", encoding="utf-8") as wf:
            json.dump(ctx, wf, indent=2, default=str)
    except Exception:
        pass

    return ctx
