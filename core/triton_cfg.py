"""TRITON step — Generate the simulation configuration (.cfg) file.

Keyword coverage follows the official TRITON documentation
(https://triton.ornl.gov/documentation/) and the bundled NeuseRiver / 5476
example cfg files.  This writer honors:

  * Topography: dem_filename
  * Manning:    n_infile  OR  const_mann
  * Hydrologic forcing: num_sources, hydrograph_filename, src_loc_file,
                         num_runoffs, runoff_filename, runoff_map
  * External boundaries: num_extbc, extbc_dir, extbc_file
  * Output control: print_option, max_value_print_option, print_interval,
                     time_series_flag, observation_loc_file,
                     input_format, output_format, outfile_pattern,
                     output_option, output_folder, projection
  * Initial conditions (restart): h_infile, qx_infile, qy_infile
  * Simulation control: sim_start_time, sim_duration, checkpoint_id,
                         time_increment_fixed, time_step
  * Physical / numerical: courant, hextra, gpu_direct_flag,
                           domain_decomposition, open_boundaries,
                           factor_interval_domain_decomposition
"""
from pathlib import Path

from core.context import save_context


# ── public API ────────────────────────────────────────────────────────────────

def create_triton_cfg(
    ctx_path,
    ctx: dict,
    *,
    # Output filename
    cfg_filename: str = None,            # default {project_name}.cfg
    # Simulation control
    sim_duration: float = None,          # seconds; falls back to ctx["sim_duration"]
    time_step: float = 10.0,
    time_increment_fixed: int = 0,
    sim_start_time: float = 0.0,
    checkpoint_id: int = 0,
    # Output control
    print_interval: float = 3600.0,
    print_option: str = "huv",           # "h" | "huv"
    max_value_print_option: str = "h",
    output_format: str = "GTIFF",        # "GTIFF" | "ASC" | "BIN"
    input_format: str = "ASC",
    output_option: str = "SEQ",          # "SEQ" | "PAR"
    output_folder: str = None,           # default output_{project}
    outfile_pattern: str = '"%s/%s/%s_%02d_%02d"',
    projection: str = "",
    # Runoff (off by default; files may be blank)
    num_runoffs: int = 0,
    runoff_filename: str = "",           # absolute or triton-relative path; "" → emit blank
    runoff_map: str = "",                # same
    # Observation / time-series output
    time_series_flag: int = 0,
    observation_loc_file: str = "",
    # Initial conditions (blank → emitted as commented-out default)
    h_infile: str = "",
    qx_infile: str = "",
    qy_infile: str = "",
    # Solver / physics
    courant: float = 0.5,
    hextra: float = 0.001,
    gpu_direct_flag: int = 0,
    domain_decomposition: str = "static",
    open_boundaries: int = 1,
    factor_interval_domain_decomposition: int = 4,
    # Constant Manning (only used if fric_mode != "varying")
    const_mann: float = 0.035,
    log_fn=print,
):
    """Write a TRITON .cfg.  Returns updated ctx."""

    triton_dir   = Path(ctx["triton_dir"])
    project_name = ctx.get("project_name", "triton_project")
    # Each AOI gets its own .cfg (and references its own AOI-named companions),
    # mirroring how each LISFLOOD AOI gets its own .par.
    aoi_name     = ctx.get("aoi_name") or project_name
    fric_mode    = ctx.get("triton_fric_mode", "fixed")

    # ── Resolve sim_duration ─────────────────────────────────────────────────
    if sim_duration is None:
        sim_duration = float(ctx.get("sim_duration", 0))
    if sim_duration <= 0:
        raise ValueError(
            f"sim_duration must be > 0 (got {sim_duration}).  Run the hydro step first."
        )

    # ── Validate required inputs ─────────────────────────────────────────────
    dem_asc = triton_dir / "dem.asc"
    if not dem_asc.exists():
        raise FileNotFoundError(
            f"DEM file not found: {dem_asc}\nRun the DEM step before the CFG step."
        )

    extbc_path = ctx.get("triton_extbc_path")
    extbc_filename = ctx.get("triton_extbc_filename") or f"{aoi_name}.extbc"
    if not extbc_path or not Path(extbc_path).exists():
        extbc_path = str(triton_dir / extbc_filename)
        if not Path(extbc_path).exists():
            raise FileNotFoundError(
                f"External BC file not found: {extbc_path}\nRun the BC step before the CFG step."
            )

    src_loc_path = ctx.get("triton_src_loc_path")
    src_loc_filename = ctx.get("triton_src_loc_filename") or f"{aoi_name}.src"
    if not src_loc_path or not Path(src_loc_path).exists():
        src_loc_path = str(triton_dir / src_loc_filename)
        if not Path(src_loc_path).exists():
            raise FileNotFoundError(
                f"Source location file not found: {src_loc_path}\nRun the BC step before the CFG step."
            )

    hyg_path = ctx.get("triton_hyg_path") or ctx.get("triton_hydro_path")
    hyg_filename = ctx.get("triton_hyg_filename") or f"{aoi_name}.hyg"
    if not hyg_path or not Path(hyg_path).exists():
        hyg_path = str(triton_dir / hyg_filename)
        if not Path(hyg_path).exists():
            raise FileNotFoundError(
                f"Hydrograph file not found: {hyg_path}\nRun the hydro step before the CFG step."
            )

    friction_asc = Path(ctx.get("triton_friction_path") or (triton_dir / f"{aoi_name}.asc"))
    if fric_mode == "varying" and not friction_asc.exists():
        raise FileNotFoundError(
            f"Friction raster not found: {friction_asc}\nRun the friction step before the CFG step."
        )

    # ── Runoff validation (only if enabled) ──────────────────────────────────
    if num_runoffs and num_runoffs > 0:
        if not runoff_filename or not Path(runoff_filename).exists():
            raise FileNotFoundError(
                f"num_runoffs={num_runoffs} but runoff_filename is missing: "
                f"'{runoff_filename}'"
            )
        if not runoff_map or not Path(runoff_map).exists():
            raise FileNotFoundError(
                f"num_runoffs={num_runoffs} but runoff_map is missing: '{runoff_map}'"
            )

    # ── Path utilities ───────────────────────────────────────────────────────
    def _rel(abs_path):
        """Path relative to triton_dir for use inside the cfg (blank → '')."""
        if not abs_path:
            return ""
        p = Path(abs_path)
        try:
            return p.relative_to(triton_dir).as_posix()
        except ValueError:
            return str(p)

    dem_rel      = _rel(dem_asc)
    extbc_rel    = _rel(extbc_path)
    src_loc_rel  = _rel(src_loc_path)
    hyg_rel      = _rel(hyg_path)
    friction_rel = _rel(friction_asc) if fric_mode == "varying" else None

    runoff_filename_rel = _rel(runoff_filename) if runoff_filename else ""
    runoff_map_rel      = _rel(runoff_map)      if runoff_map      else ""
    observation_rel     = _rel(observation_loc_file) if observation_loc_file else ""
    h_infile_rel        = _rel(h_infile)  if h_infile  else ""
    qx_infile_rel       = _rel(qx_infile) if qx_infile else ""
    qy_infile_rel       = _rel(qy_infile) if qy_infile else ""

    # ── Output folder ────────────────────────────────────────────────────────
    # A bare folder name (not a path); TRITON creates it at run time, relative
    # to the .cfg's folder.  Named after the AOI for a per-AOI result set.
    if not output_folder:
        output_folder = f"{aoi_name}_output"

    # ── Counts come from context (written by BC step) ────────────────────────
    num_sources = int(ctx.get("num_sources", 1))
    num_extbc   = int(ctx.get("num_extbc",   1))

    # ── Assemble cfg ─────────────────────────────────────────────────────────
    lines = [
        "#---------------------------------------------------------------------------",
        f"# {aoi_name}",
        "# TRITON parameter file generated by FIMsim.",
        "#---------------------------------------------------------------------------",
        "# Input Data",
        "#---------------------------------------------------------------------------",
        "# Topography",
        f'dem_filename="{dem_rel}"',
        "",
        "# Manning / friction input",
    ]
    if fric_mode == "varying":
        lines.append(f'n_infile="{friction_rel}"')
        lines.append(f"#const_mann={const_mann}")
    else:
        fpfric = ctx.get("par_fpfric", const_mann)
        lines.append("#n_infile=")
        lines.append(f"const_mann={fpfric}")

    lines += [
        "",
        "# Hydrograph and source locations",
        f"num_sources={num_sources}",
        f'hydrograph_filename="{hyg_rel}"',
        f'src_loc_file="{src_loc_rel}"',
        "",
        "# Runoff",
        f"num_runoffs={int(num_runoffs)}",
        f'runoff_filename="{runoff_filename_rel}"',
        f'runoff_map="{runoff_map_rel}"',
        "",
        "#---------------------------------------------------------------------------",
        "# Output Control",
        "#---------------------------------------------------------------------------",
        f"print_option={print_option}",
        f"max_value_print_option={max_value_print_option}",
        "",
        "# Print interval in seconds of simulation time",
        f"print_interval={int(print_interval)}",
        "",
        "# time_series_flag=1 to activate, 0 to deactivate",
        f"time_series_flag={int(time_series_flag)}",
        f'observation_loc_file="{observation_rel}"',
        "",
        "#---------------------------------------------------------------------------",
        "# Simulation Control",
        "#---------------------------------------------------------------------------",
        "# Start and duration of simulation time in seconds",
        f"sim_start_time={int(sim_start_time)}",
        f"sim_duration={int(sim_duration)}",
        "",
        "# If checkpoint_id is 0 that means a clean start",
        f"checkpoint_id={int(checkpoint_id)}",
        "",
        "# time_increment_fixed=0 for variable dt, 1 for constant dt",
        f"time_increment_fixed={int(time_increment_fixed)}",
        f"time_step={int(time_step)}",
        "",
        "# Initial conditions (uncomment + provide files for a warm restart)",
    ]

    def _cond_line(keyword, rel):
        return f'{keyword}="{rel}"' if rel else f'#{keyword}=""'

    lines += [
        _cond_line("h_infile",  h_infile_rel),
        _cond_line("qx_infile", qx_infile_rel),
        _cond_line("qy_infile", qy_infile_rel),
        "",
        "#---------------------------------------------------------------------------",
        "# File I/O",
        "#---------------------------------------------------------------------------",
        f"input_format={input_format}",
        f"output_format={output_format}",
        f"outfile_pattern={outfile_pattern}",
        f"output_option={output_option}",
        f'output_folder="{output_folder}"',
        "",
        "# Projection (for GeoTIFF output). Defaults to WGS84 when blank.",
    ]
    if projection:
        lines.append(f"projection={projection}")
    else:
        epsg = ctx.get("dem_epsg") or ctx.get("crs_epsg")
        if epsg:
            lines.append(f"projection=EPSG:{epsg}")
        else:
            lines.append("# projection=EPSG:XXXXX   ← set your projected CRS here")

    lines += [
        "",
        "#---------------------------------------------------------------------------",
        "# External boundaries",
        "#---------------------------------------------------------------------------",
        f"num_extbc={num_extbc}",
        'extbc_dir="./"',
        f'extbc_file="{extbc_rel}"',
        "",
        "#---------------------------------------------------------------------------",
        "# Other variables",
        "#---------------------------------------------------------------------------",
        "it_count=0",
        "# Courant Number",
        f"courant={courant}",
        "",
        f"hextra={hextra}",
        f"gpu_direct_flag={int(gpu_direct_flag)}",
        f"domain_decomposition={domain_decomposition}",
        f"open_boundaries={int(open_boundaries)}",
        f"factor_interval_domain_decomposition={int(factor_interval_domain_decomposition)}",
        "#---------------------------------------------------------------------------",
    ]

    # ── Write cfg ─────────────────────────────────────────────────────────────
    cfg_filename = cfg_filename or f"{aoi_name}.cfg"
    cfg_path = triton_dir / cfg_filename
    cfg_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log_fn(f"TRITON cfg written: {cfg_path}")

    # ── Update context ────────────────────────────────────────────────────────
    ctx["triton_cfg_path"]        = str(cfg_path)
    ctx["triton_cfg_filename"]    = cfg_filename
    ctx["triton_sim_duration"]    = sim_duration
    ctx["triton_time_step"]       = time_step
    ctx["triton_cfg_written"]     = True
    ctx["triton_num_runoffs"]     = int(num_runoffs)
    ctx["triton_time_series_flag"] = int(time_series_flag)
    save_context(ctx_path, ctx)
    return ctx
