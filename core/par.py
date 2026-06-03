"""Step 8 — Build the LISFLOOD-FP .par parameter file."""
from pathlib import Path
from core.context import save_context


def create_par(ctx_path, ctx: dict,
               par_name: str,
               resroot: str,
               results_dir_name: str,
               sim_time: float,
               initial_tstep: float,
               saveint: float,
               massint: float,
               # Solver  -----------------------------------------------------------
               solver_mode: str,           # acceleration | adaptive_default |
                                           # adaptive_fixed_timestep |
                                           # acceleration_with_routing | diffusion
               drycheck_mode: str,         # leave_default_off | drycheckon | drycheckoff
               # Initial condition ------------------------------------------------
               start_mode: str,            # none | startfile | startelev | loadcheck
               startfile_path=None,
               startelev_path=None,
               loadcheck_path=None,
               # Routing parameters (for acceleration_with_routing only) ----------
               routing_speed: float = None,
               routesfthresh: float = None,
               depththresh: float = None,
               # Checkpointing ----------------------------------------------------
               use_checkpoint: bool = False,
               checkpoint_hours: float = None,
               # Output options ---------------------------------------------------
               use_overpass: bool = False,
               overpass_time: float = None,
               use_elevoff: bool = True,
               use_depthoff: bool = False,
               use_binary_out: bool = False,
               use_hazard: bool = False,
               use_mint_hk: bool = True,
               use_qoutput: bool = False,
               # Extra ------------------------------------------------------------
               extra_lines: list = None,
               log_fn=print):
    """Write .par file to lisflood_files folder.  Returns updated ctx."""

    project_dir  = Path(ctx["project_dir"])
    lisflood_dir = Path(ctx["lisflood_dir"])
    project_name = ctx["project_name"]
    upstream_mode    = ctx.get("upstream_mode", "fixed_discharge")
    bdy_written      = bool(ctx.get("bdy_written", False))
    use_manningfile  = bool(ctx.get("par_use_manningfile", False))
    use_fpfric       = bool(ctx.get("par_use_fpfric", False))
    fpfric_val       = ctx.get("par_fpfric")

    # ── Resolve the actual filenames the upstream steps wrote ───────────
    # Each prep step now uses ``next_free_path`` so re-runs produce
    # versioned outputs (``dem.ascii`` → ``dem (1).ascii`` → …).  Read
    # the actual filenames from ctx and reference those — NOT the
    # hard-coded canonical names.  Fall back to canonical names when
    # ctx doesn't have a value (legacy / first-run case).
    dem_ascii_name = Path(
        ctx.get("dem_ascii_path") or "dem.ascii"
    ).name
    bci_file_name = Path(
        ctx.get("bci_path") or "BC.bci"
    ).name
    manning_file_name = Path(
        ctx.get("manning_ascii_path") or "lulc.ascii"
    ).name
    bdy_file_name = Path(
        ctx.get("bdy_path") or "BC.bdy"
    ).name

    # ── Validate required input files ─────────────────────────────────────
    dem_ascii = lisflood_dir / dem_ascii_name
    bci_file  = lisflood_dir / bci_file_name
    if not dem_ascii.exists():
        raise FileNotFoundError(f"{dem_ascii_name} not found: {dem_ascii}")
    if not bci_file.exists():
        raise FileNotFoundError(f"{bci_file_name} not found: {bci_file}")
    if use_manningfile and not (lisflood_dir / manning_file_name).exists():
        raise FileNotFoundError(
            f"{manning_file_name} not found: {lisflood_dir / manning_file_name}"
        )
    if upstream_mode == "varying_discharge" and not bdy_written:
        raise RuntimeError(
            "upstream_mode is varying_discharge but BDY file has not been written yet. "
            "Complete Step 6 first."
        )

    # ── Validate initial-condition file path ──────────────────────────────
    if start_mode == "startfile":
        if not startfile_path:
            raise ValueError(
                "Initial condition 'startfile' requires a water-depth raster path. "
                "Please provide the file."
            )
        if not Path(startfile_path).exists():
            raise FileNotFoundError(f"startfile not found: {startfile_path}")

    elif start_mode == "startelev":
        if not startelev_path:
            raise ValueError(
                "Initial condition 'startelev' requires a water-surface elevation raster path. "
                "Please provide the file."
            )
        if not Path(startelev_path).exists():
            raise FileNotFoundError(f"startelev file not found: {startelev_path}")

    elif start_mode == "loadcheck":
        if not loadcheck_path:
            raise ValueError(
                "Initial condition 'loadcheck' requires a checkpoint file path. "
                "Please provide the file."
            )
        if not Path(loadcheck_path).exists():
            raise FileNotFoundError(f"loadcheck file not found: {loadcheck_path}")

    # ── Validate routing parameters ───────────────────────────────────────
    use_routing = (solver_mode == "acceleration_with_routing")
    if use_routing and routing_speed is None:
        raise ValueError(
            "Solver 'acceleration_with_routing' requires a routing wave speed (routingspeed). "
            "Please set it in the routing parameters."
        )

    if not par_name.lower().endswith(".par"):
        par_name += ".par"
    # Version the .par itself with ``next_free_path`` so re-running
    # doesn't clobber the previous run's .par either.  The stem comes
    # from the user-supplied name minus its .par extension.
    from core.export import next_free_path
    par_stem = par_name[:-4]   # drop the trailing ".par"
    par_path = next_free_path(lisflood_dir, par_stem, "par")

    dirroot = project_dir / results_dir_name
    dirroot.mkdir(parents=True, exist_ok=True)

    # Solver flags
    use_acceleration = solver_mode in {"acceleration", "acceleration_with_routing"}
    use_adaptoff     = solver_mode == "adaptive_fixed_timestep"
    use_diffusion    = solver_mode == "diffusion"

    # ── Build PAR lines ───────────────────────────────────────────────────
    lines = []
    lines.append(f"# {project_name}")
    lines.append("# LISFLOOD-FP parameter file generated by LISFLOOD-FP Prep App")
    lines.append("")

    # Core file references — point to the ACTUAL filenames each prep
    # step generated (versioned names like "dem (1).ascii" when
    # re-running).  This lets the user keep prior runs intact and have
    # this new .par reference its own freshly-written companions.
    lines.append(f"DEMfile         {dem_ascii_name}")
    if use_manningfile:
        lines.append(f"manningfile     {manning_file_name}")
    elif use_fpfric:
        lines.append(f"fpfric          {fpfric_val}")

    lines.append(f"bcifile         {bci_file_name}")
    if upstream_mode == "varying_discharge":
        lines.append(f"bdyfile         {bdy_file_name}")

    lines.append("")
    lines.append(f"saveint         {saveint}")
    lines.append(f"massint         {massint}")
    lines.append(f"resroot         {resroot}")
    lines.append(f"dirroot         {dirroot}")
    lines.append(f"sim_time        {sim_time}")
    lines.append(f"initial_tstep   {initial_tstep}")

    # Solver
    lines.append("")
    if use_acceleration:
        lines.append("acceleration")
    if use_adaptoff:
        lines.append("adaptoff")
    if use_diffusion:
        lines.append("diffusion")
    if use_routing:
        lines.append("routing")
        lines.append(f"routingspeed    {routing_speed}")
        if routesfthresh is not None:
            lines.append(f"routesfthresh   {routesfthresh}")
        if depththresh is not None:
            lines.append(f"depththresh     {depththresh}")

    # Dry-cell check
    if drycheck_mode == "drycheckon":
        lines += ["", "drycheckon"]
    elif drycheck_mode == "drycheckoff":
        lines += ["", "drycheckoff"]

    # Initial condition
    if start_mode == "startfile" and startfile_path:
        lines += ["", f"startfile       {startfile_path}"]
    elif start_mode == "startelev" and startelev_path:
        lines += ["", f"startelev       {startelev_path}"]
    elif start_mode == "loadcheck" and loadcheck_path:
        lines += ["", f"loadcheck       {loadcheck_path}"]

    # Checkpointing
    if use_checkpoint and checkpoint_hours:
        lines += ["", f"checkpoint      {checkpoint_hours}"]

    # Output options
    lines.append("")
    if use_elevoff:
        lines.append("elevoff")
    if use_depthoff:
        lines.append("depthoff")
    if use_binary_out:
        lines.append("binary_out")
    if use_hazard:
        lines.append("hazard")
    if use_mint_hk:
        lines.append("mint_hk")
    if use_qoutput:
        lines.append("qoutput")
    if use_overpass and overpass_time is not None:
        lines.append(f"overpass        {overpass_time}")

    # Extra user keywords
    if extra_lines:
        lines += ["", "# Extra user-specified keywords"] + list(extra_lines)

    # ── Write file ────────────────────────────────────────────────────────
    par_text = "\n".join(lines).strip() + "\n"
    par_path.write_text(par_text, encoding="utf-8")

    log_fn(f"PAR file written: {par_path}")
    log_fn("\n--- PAR preview ---\n" + par_text)

    # ── Update context ────────────────────────────────────────────────────
    ctx["par_path"]            = str(par_path)
    ctx["par_resroot"]         = resroot
    ctx["par_dirroot"]         = str(dirroot)
    ctx["par_sim_time"]        = sim_time
    ctx["par_initial_tstep"]   = initial_tstep
    ctx["par_saveint"]         = saveint
    ctx["par_massint"]         = massint
    ctx["par_solver_mode"]     = solver_mode
    ctx["par_drycheck_mode"]   = drycheck_mode
    ctx["par_start_mode"]      = start_mode
    ctx["par_use_checkpoint"]  = use_checkpoint
    ctx["par_checkpoint_hours"] = checkpoint_hours
    ctx["par_use_overpass"]    = use_overpass
    ctx["par_overpass_time"]   = overpass_time
    ctx["par_use_elevoff"]     = use_elevoff
    ctx["par_use_depthoff"]    = use_depthoff
    ctx["par_use_binary_out"]  = use_binary_out
    ctx["par_use_hazard"]      = use_hazard
    ctx["par_use_mint_hk"]     = use_mint_hk
    ctx["par_use_qoutput"]     = use_qoutput
    ctx["par_routing_speed"]   = routing_speed
    ctx["par_routesfthresh"]   = routesfthresh
    ctx["par_depththresh"]     = depththresh
    ctx["par_extra_lines"]     = extra_lines or []
    ctx["par_written"]         = True
    save_context(ctx_path, ctx)
    return ctx
