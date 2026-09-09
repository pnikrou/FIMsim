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

            # Start from the AOI's SAVED context, not a fresh copy of the
            # project ctx: re-running the DEM step otherwise overwrites
            # workflow_context.json and throws away the flowline and
            # streamflow keys later steps put there.
            _, _saved = _load_feat_ctx(folder)
            feat_ctx = {**dict(ctx), **(_saved or {})}
            feat_ctx["aoi_path"]          = feat["source_file"]
            feat_ctx["aoi_name"]          = feat["folder_name"]
            feat_ctx["aoi_feature_index"] = feat["feature_index"]
            # ARC-Curve2Flood needs a GEOGRAPHIC DEM.  NenCarta writes
            # "Spatial_Units\tdeg" into the ARC input file unconditionally
            # (nencarta/main.py :: _write_arc_input_section), so a projected
            # metre DEM makes ARC size its cross-sections in degrees against a
            # 10 m grid; it then produces no rating curves and the run dies
            # later with "No VDT data was generated".  Force EPSG:4326 and give
            # the resolution in degrees, matching 3DEP's native 1/3 arc-second.
            feat_ctx["working_crs_epsg"]  = 4326
            feat_ctx["working_crs_label"] = "WGS 84 (geographic)"
            this_res_deg = this_res_m / 111320.0
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
                dem_res_m=this_res_deg,
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


# ── Flowline ───────────────────────────────────────────────────────────────────

def run_arc_flowline_for_all_aois(ctx_path: str, ctx: dict,
                                  per_aoi_configs: list = None,
                                  log_fn=print) -> dict:
    """Save the ARC flowline shapefile for every confirmed ARC AOI.

    ``per_aoi_configs`` (optional): one dict per AOI with the card settings —
    ``{"source": "nhd"|"user", "user_path": str|None}``.  Emits
    ``▶ Flowline [N/M]`` / ``✓ Flowline [N/M] finished`` log lines.  Each AOI's
    existing per-AOI workflow_context.json (DEM + Manning keys) is preserved.
    """
    from core.arc_flowline import prepare_arc_flowline

    aoi_features = ctx.get("aoi_features", [])
    if not aoi_features:
        cfg = (per_aoi_configs or [{}])[0] or {}
        return prepare_arc_flowline(ctx_path=ctx_path, ctx=ctx, log_fn=log_fn, **cfg)

    n = len(aoi_features)
    if per_aoi_configs is not None and len(per_aoi_configs) != n:
        raise RuntimeError(
            f"per_aoi_configs has {len(per_aoi_configs)} entries but "
            f"there are {n} AOIs.")

    summary = []
    for i, feat in enumerate(aoi_features, 1):
        try:
            log_fn(f"▶ Flowline [{i}/{n}]: '{feat['name']}' ...")
            folder = feat["folder_path"]
            Path(folder).mkdir(parents=True, exist_ok=True)
            arc_dir = _arc_model_dir(folder)

            feat_ctx_path = str(Path(folder) / "workflow_context.json")
            # Start from the saved per-AOI context so DEM / Manning keys survive.
            feat_ctx = {}
            if Path(feat_ctx_path).exists():
                try:
                    with open(feat_ctx_path, "r", encoding="utf-8") as fr:
                        feat_ctx = json.load(fr)
                except Exception:
                    feat_ctx = {}
            feat_ctx["aoi_path"]          = feat["source_file"]
            feat_ctx["aoi_name"]          = feat["folder_name"]
            feat_ctx["aoi_feature_index"] = feat["feature_index"]
            feat_ctx["project_dir"]       = folder
            feat_ctx["arc_dir"]           = arc_dir

            cfg = (per_aoi_configs[i - 1] if per_aoi_configs else {}) or {}
            feat_ctx = prepare_arc_flowline(
                ctx_path=feat_ctx_path, ctx=feat_ctx, log_fn=log_fn, **cfg)

            summary.append({
                "name":          feat["name"],
                "folder":        folder,
                "flowline":      feat_ctx.get("arc_flowline_path"),
                "count":         feat_ctx.get("arc_flowline_count"),
                "source":        feat_ctx.get("arc_flowline_source", "nhd"),
                "source_file":   feat["source_file"],
                "feature_index": feat["feature_index"],
            })
            log_fn(f"✓ Flowline [{i}/{n}] finished: '{feat['name']}'")

        except Exception as _exc:
            import traceback
            log_fn(f"✗ Flowline [{i}/{n}] ERROR for '{feat['name']}': {_exc}")
            log_fn(traceback.format_exc())
            summary.append({
                "name":   feat.get("name", f"AOI {i}"),
                "failed": True,
                "error":  str(_exc),
            })

    # Rewire parent ctx to the first AOI.
    f0 = aoi_features[0]
    ctx["aoi_path"] = f0["source_file"]
    ctx["aoi_name"] = f0["folder_name"]
    ctx["arc_dir"]  = _arc_model_dir(f0["folder_path"])
    ctx["arc_flowline_per_aoi"] = summary
    try:
        with open(ctx_path, "w", encoding="utf-8") as wf:
            json.dump(ctx, wf, indent=2, default=str)
    except Exception:
        pass
    return ctx


# ── Step 6: Flow file (NWM base/max per reach) ────────────────────────────────

def _load_feat_ctx(folder: str) -> tuple:
    """Return (feat_ctx_path, feat_ctx_dict) for one AOI folder."""
    feat_ctx_path = str(Path(folder) / "workflow_context.json")
    feat_ctx = {}
    if Path(feat_ctx_path).exists():
        try:
            with open(feat_ctx_path, "r", encoding="utf-8") as fr:
                feat_ctx = json.load(fr)
        except Exception:
            feat_ctx = {}
    return feat_ctx_path, feat_ctx


def _save_feat_ctx(feat_ctx_path: str, feat_ctx: dict):
    try:
        with open(feat_ctx_path, "w", encoding="utf-8") as wf:
            json.dump(feat_ctx, wf, indent=2, default=str)
    except Exception:
        pass


def run_arc_flowfile_for_all_aois(ctx_path: str, ctx: dict,
                                  per_aoi_configs: list = None,
                                  flow_cfg: dict = None, log_fn=print) -> dict:
    """Record each AOI's NenCarta streamflow settings.

    FIMsim used to download NWM flows here and write ARC a flow.csv.  It no
    longer does: NenCarta fetches its own streamflow (GEOGLOWS or NWM) as part
    of the run, so this step only captures WHICH source and event to ask it
    for.  The settings are validated against NenCarta's rules now — a bad
    forecast hour or a missing API key is far cheaper to catch here than part
    way into the run — then saved for the Run step to put in the JSON.
    """
    from core.nencarta_run import (STREAMFLOW_SOURCES, FORECAST_HOURS,
                                   _as_yyyymmdd, NenCartaError)

    def _check(cfg: dict, where: str) -> dict:
        cfg = dict(cfg or {})
        src = cfg.get("streamflow_source", "GEOGLOWS")
        if src not in STREAMFLOW_SOURCES:
            raise RuntimeError(
                f"{where}: streamflow_source must be one of "
                f"{', '.join(STREAMFLOW_SOURCES)} — got {src!r}.")
        # No API-key check: FIMsim fetches NWM itself from the public NOAA
        # zarr and Google mirror (core/nwm_flows.py) and hands NenCarta
        # finished flow files, so NenCarta never calls the CIROH API.  A key is
        # only needed if NenCarta is left to fetch NWM on its own.
        if cfg.get("forensic_forecast_date"):
            cfg["forensic_forecast_date"] = _as_yyyymmdd(
                cfg["forensic_forecast_date"])
        hour = cfg.get("forensic_forecast_hour")
        if hour not in (None, "") and src != "GEOGLOWS":
            hour = f"{int(hour):02d}"
            allowed = FORECAST_HOURS.get(src)
            if allowed and hour not in allowed:
                raise RuntimeError(
                    f"{where}: cycle hour {hour} is not valid for {src} — "
                    f"allowed: {', '.join(allowed)}.")
            cfg["forensic_forecast_hour"] = hour
        elif src == "GEOGLOWS":
            # GEOGLOWS forecasts are daily; NenCarta ignores the hour.
            cfg.pop("forensic_forecast_hour", None)
        return cfg

    def _describe(cfg: dict) -> str:
        d = cfg.get("forensic_forecast_date")
        when = (f"{d[:4]}-{d[4:6]}-{d[6:8]}" if d else "latest forecast")
        h = cfg.get("forensic_forecast_hour")
        return (f"{cfg.get('streamflow_source', 'GEOGLOWS')}, {when}"
                + (f" t{h}z" if h else ""))

    aoi_features = ctx.get("aoi_features", [])

    if not aoi_features:
        cfg = _check((per_aoi_configs or [flow_cfg or {}])[0] or {}, "Streamflow")
        ctx["nencarta_streamflow"] = cfg
        log_fn(f"✓ Streamflow: {_describe(cfg)}")
        if ctx_path:
            try:
                with open(ctx_path, "w", encoding="utf-8") as wf:
                    json.dump(ctx, wf, indent=2, default=str)
            except Exception:
                pass
        return ctx

    n = len(aoi_features)
    if per_aoi_configs is not None and len(per_aoi_configs) != n:
        raise RuntimeError(
            f"per_aoi_configs has {len(per_aoi_configs)} entries but "
            f"there are {n} AOIs.")

    summary = []
    for i, feat in enumerate(aoi_features, 1):
        name = feat.get("name", f"AOI {i}")
        log_fn(f"▶ Streamflow [{i}/{n}]: '{name}' ...")
        folder = feat["folder_path"]
        feat_ctx_path, feat_ctx = _load_feat_ctx(folder)
        cfg = _check((per_aoi_configs[i - 1] if per_aoi_configs else flow_cfg)
                     or {}, f"'{name}'")

        # NenCarta reads GEOGLOWS reach ids from LINKNO and NWM ids from COMID,
        # so the flowline chosen in step 5 has to match the source picked here.
        src_is_geoglows = cfg.get("streamflow_source", "GEOGLOWS") == "GEOGLOWS"
        fl_src = feat_ctx.get("arc_flowline_source")
        if fl_src and src_is_geoglows != (fl_src == "geoglows"):
            log_fn(f"  ⚠ '{name}': streamflow is "
                   f"{cfg.get('streamflow_source')} but the flowline came from "
                   f"'{fl_src}'. GEOGLOWS needs the GEOGLOWS network (LINKNO) "
                   f"and NWM needs NHDPlus (COMID) — re-run the Flowline step "
                   f"with the matching source or the run will find no flow.")

        if src_is_geoglows and feat_ctx.get("geoglows_vpu"):
            cfg.setdefault("geoglows_vpu", feat_ctx["geoglows_vpu"])

        # Duration -> one flow file per timestep.  ARC-Curve2Flood is steady
        # state, so a duration is a set of independent snapshots; NenCarta maps
        # them via floodmap_mode "user" + user_flow_files, writing one raster
        # per file (run_user_floodmaps) off the single set of ARC curves.
        if cfg.pop("period_mode", "snapshot") == "duration":
            from core.arc_flowseries import build_flow_series
            flowline = feat_ctx.get("arc_flowline_path")
            if not flowline:
                raise RuntimeError(
                    f"'{name}': a duration needs the flowline — run step 5 first.")
            files = build_flow_series(
                flowline,
                cfg.pop("start_date", None), cfg.pop("end_date", None),
                int(cfg.pop("step_hours", 24) or 24),
                Path(folder) / "arc-files" / "flow_series",
                id_field=("LINKNO" if src_is_geoglows else "COMID"),
                source=cfg.get("streamflow_source", "GEOGLOWS"),
                frange=str(cfg.get("streamflow_source", "")).replace(
                    "NWM_", "") or "short_range",
                cycle_hour=cfg.get("forensic_forecast_hour"),
                log_fn=lambda m: log_fn("  " + str(m)))
            if not files:
                raise RuntimeError(f"'{name}': no discharge found for that period.")
            feat_ctx["nencarta_flow_files"] = files
            cfg["n_timesteps"] = len(files)
        else:
            for k in ("start_date", "end_date", "step_hours"):
                cfg.pop(k, None)
            feat_ctx.pop("nencarta_flow_files", None)

        feat_ctx["nencarta_streamflow"] = cfg
        _save_feat_ctx(feat_ctx_path, feat_ctx)
        summary.append({"name": name, "folder": folder, **cfg})
        log_fn(f"✓ Streamflow [{i}/{n}] finished: '{name}' — {_describe(cfg)}")

    ctx["arc_flow_per_aoi"] = summary
    try:
        with open(ctx_path, "w", encoding="utf-8") as wf:
            json.dump(ctx, wf, indent=2, default=str)
    except Exception:
        pass
    return ctx


def _arc_manning_or_none(path, log_fn=print):
    """Return ``path`` only if it is a Manning table ARC can actually use.

    NenCarta downloads ESA WorldCover itself and writes a matching table
    (Create_BaseLine_Manning_n_File_ESA):

        LC_ID<TAB>Description<TAB>Manning_n
        10<TAB>Tree Cover<TAB>0.120

    FIMsim's Land Cover step writes a different thing — comma separated
    ``LULC_Code,Manning_n`` keyed on ESRI Sentinel-2 classes 1-11 — which is
    what LISFLOOD-FP and TRITON consume.  Handing that to NenCarta is worse
    than handing it nothing: ARC looks up roughness for ESA classes 10/30/40…,
    finds no match at all, and silently produces NO rating curves, so the run
    dies later with "No VDT data was generated".  Only pass a file that really
    is in ARC's format; otherwise let NenCarta build its own.
    """
    if not path or not Path(path).exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            header = fh.readline()
    except OSError:
        return None
    if "\t" in header and header.split("\t")[0].strip().upper() == "LC_ID":
        return str(path)
    log_fn("  Manning table is FIMsim's LULC format (comma separated, ESRI "
           "classes), not ARC's tab-separated LC_ID/ESA format — letting "
           "NenCarta build its own from ESA WorldCover instead.")
    return None


def _nencarta_entry(name: str, folder: str, feat_ctx: dict, cfg: dict,
                    log_fn=print) -> dict:
    """One NenCarta watersheds[] entry built from an AOI's saved context."""
    from core.nencarta_run import build_watershed

    # The Streamflow step stored NenCarta's streamflow keys on this AOI; the
    # Run step's own panel supplies the mapping keys.  Run-panel values win so
    # the user can still override at run time.
    cfg = {**(feat_ctx.get("nencarta_streamflow") or {}), **(cfg or {})}

    flowline = feat_ctx.get("arc_flowline_path")
    if not flowline:
        raise RuntimeError("No flowline for this AOI — run the Flowline step first.")

    # NenCarta globs dem_dir with dem_filter, so both must point at the DEM
    # that was actually written.  Take them from dem_tif_path rather than
    # trusting ctx["dem_dir"]: prepare_dem saves the GeoTIFF in the AOI folder
    # while the DEM step records dem_dir as <AOI>/dem (which holds only the
    # ASCII), so using dem_dir alone finds no DEM at all.  Pinning the filter
    # also keeps lulc.tif and the Land Cover step's temp rasters out.
    dem_tif = feat_ctx.get("dem_tif_path")
    if dem_tif and Path(dem_tif).exists():
        dem_dir = str(Path(dem_tif).parent)
        dem_filter = Path(dem_tif).name
    else:
        dem_dir = feat_ctx.get("dem_dir") or str(Path(folder) / "dem")
        dem_filter = "*.tif"
    if not any(Path(dem_dir).glob(dem_filter)):
        raise RuntimeError(
            f"No DEM matching '{dem_filter}' in {dem_dir} — run the DEM step.")

    out_dir = str(Path(folder) / "nencarta-output")
    # A duration run maps each timestep's flow file separately.
    flow_files = [f for f in (feat_ctx.get("nencarta_flow_files") or [])
                  if Path(f).exists()]
    if flow_files:
        log_fn(f"  '{name}': duration run — {len(flow_files)} timestep map(s).")
    # Build the stream network the first time; on a re-run the gpkg is already
    # there and NenCarta's own default (skip) is the faster, correct choice.
    strm_done = any(Path(out_dir, name, "STRM").glob("*StrmShp*.gpkg"))
    if strm_done:
        log_fn(f"  '{name}': reusing the existing STRM network.")
    return build_watershed(
        process_stream_network=not strm_done,
        name=name,
        flowline=flowline,
        dem_dir=dem_dir,
        output_dir=out_dir,
        dem_filter=dem_filter,
        streamflow_source=cfg.get("streamflow_source", "GEOGLOWS"),
        age_of_forecast_days=cfg.get("age_of_forecast_days", 7),
        geoglows_vpu=cfg.get("geoglows_vpu", feat_ctx.get("geoglows_vpu")),
        nwm_api_key=cfg.get("nwm_api_key"),
        specified_bathyflow_field=cfg.get("specified_bathyflow_field"),
        specified_highflow_field=cfg.get("specified_highflow_field"),
        forensic_forecast_date=cfg.get("forensic_forecast_date"),
        forensic_forecast_hour=cfg.get("forensic_forecast_hour"),
        mapper=cfg.get("mapper", "Curve2Flood-Kernel Weighted"),
        floodmap_mode=("user" if flow_files else "forecast"),
        user_flow_files=(flow_files or None),
        mannings_text_file=_arc_manning_or_none(
            feat_ctx.get("arc_mannings_n_path"), log_fn),
        bathy_use_banks=cfg.get("bathy_use_banks", False),
        find_banks_based_on_landcover=cfg.get("find_banks_based_on_landcover", True),
        clean_dem=cfg.get("clean_dem", False),
        make_depth_maps=cfg.get("make_depth_maps", True),
        make_velocity_maps=cfg.get("make_velocity_maps", False),
        make_wse_maps=cfg.get("make_wse_maps", False),
    )


def run_arc_curve2flood_for_all_aois(ctx_path: str, ctx: dict,
                                     per_aoi_configs: list = None,
                                     run_cfg: dict = None, log_fn=print) -> dict:
    """Write nencarta.json for every AOI and run NenCarta's flood-mapping CLI.

    FIMsim does not call ARC or Curve2Flood itself — NenCarta is the authors'
    orchestrator and drives both.  All AOIs go into ONE json as separate
    ``watersheds`` entries, which is the batch form NenCarta is built for, and
    it is run with --serial so its log lines stay interleaved in order.
    """
    from core.nencarta_run import (write_nencarta_json, validate_with_nencarta,
                                   run_flood_mapping, find_flood_maps,
                                   NenCartaError)

    aoi_features = ctx.get("aoi_features", [])
    entries, targets = [], []

    if not aoi_features:
        cfg = (per_aoi_configs or [run_cfg or {}])[0] or {}
        folder = ctx.get("project_dir", ".")
        name = ctx.get("aoi_name") or Path(folder).name
        entries.append(_nencarta_entry(name, folder, ctx, cfg, log_fn))
        targets.append((ctx_path, ctx, name, folder))
    else:
        n = len(aoi_features)
        if per_aoi_configs is not None and len(per_aoi_configs) != n:
            raise RuntimeError(
                f"per_aoi_configs has {len(per_aoi_configs)} entries but "
                f"there are {n} AOIs.")
        for i, feat in enumerate(aoi_features, 1):
            folder = feat["folder_path"]
            feat_ctx_path, feat_ctx = _load_feat_ctx(folder)
            cfg = ((per_aoi_configs[i - 1] if per_aoi_configs else run_cfg) or {})
            name = feat.get("folder_name") or feat["name"]
            try:
                entries.append(_nencarta_entry(name, folder, feat_ctx, cfg, log_fn))
                targets.append((feat_ctx_path, feat_ctx, name, folder))
            except Exception as exc:
                log_fn(f"✗ Skipping '{name}': {exc}")

    if not entries:
        raise RuntimeError(
            "No AOI had the inputs NenCarta needs (DEM folder + flowline).")

    # Fail on a bad entry in milliseconds rather than mid-run.
    validate_with_nencarta(entries, log_fn=log_fn)

    json_path = str(Path(ctx.get("project_dir", ".")) / "nencarta.json")
    write_nencarta_json(json_path, entries, log_fn=log_fn)
    ctx["nencarta_json"] = json_path

    log_fn(f"▶ NenCarta: {len(entries)} watershed(s) …")
    run_flood_mapping(json_path, serial=True, log_fn=log_fn)

    summary = []
    cfg_all = ((per_aoi_configs[0] if per_aoi_configs else run_cfg) or {})
    for (feat_ctx_path, feat_ctx, name, folder) in targets:
        out_dir = str(Path(folder) / "nencarta-output")
        maps = find_flood_maps(out_dir, name)

        # NenCarta must RUN in EPSG:4326 (Spatial_Units is hardcoded to "deg"
        # in both nencarta and arc), but the products belong in the AOI's own
        # CRS on a metric grid so they can be differenced against LISFLOOD-FP,
        # TRITON and OWP HAND-FIM outputs.
        aoi_for_crs = (feat_ctx.get("aoi_path")
                       or (aoi_features[0]["source_file"] if aoi_features else None))
        if maps and aoi_for_crs:
            try:
                from core.arc_reproject import reproject_flood_maps, summarise
                made = reproject_flood_maps(
                    maps, aoi_for_crs, Path(folder) / "FloodMaps",
                    res_m=float(cfg_all.get("output_res_m", 10.0)),
                    log_fn=lambda m: log_fn("  " + str(m)))
                if made:
                    feat_ctx["floodmaps_dir"] = str(Path(folder) / "FloodMaps")
                    feat_ctx["floodmaps"] = [m["path"] for m in made]
                    summarise([m["path"] for m in made
                               if m["kind"] == "extent"][:4],
                              log_fn=lambda m: log_fn("  " + str(m)))
            except Exception as exc:
                log_fn(f"  ⚠ could not reproject the flood maps: {exc}")

        n_steps = len(feat_ctx.get("nencarta_flow_files") or [])
        if n_steps:
            log_fn(f"  '{name}': duration produced {len(maps)} raster(s) "
                   f"from {n_steps} timestep(s).")
        feat_ctx["nencarta_output_dir"] = out_dir
        feat_ctx["arc_flood_map"] = maps[0] if maps else None
        feat_ctx["nencarta_flood_maps"] = maps
        if feat_ctx_path:
            _save_feat_ctx(feat_ctx_path, feat_ctx)
        summary.append({"name": name, "folder": folder,
                        "flood_map": maps[0] if maps else None,
                        "flood_maps": maps,
                        "output_dir": out_dir})
        log_fn(f"  '{name}': {len(maps)} flood map(s) in {out_dir}"
               if maps else
               f"  ⚠ '{name}': NenCarta produced no flood raster in {out_dir}")

    ctx["arc_run_per_aoi"] = summary
    if not aoi_features and summary:
        ctx["arc_flood_map"] = summary[0]["flood_map"]
    try:
        with open(ctx_path, "w", encoding="utf-8") as wf:
            json.dump(ctx, wf, indent=2, default=str)
    except Exception:
        pass
    return ctx
