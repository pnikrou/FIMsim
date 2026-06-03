"""TRITON boundary-condition step.

Writes two files used by a TRITON simulation:

  * src_loc_file   — coordinates of upstream inflow source points
  * extbc file     — downstream / lateral external boundary conditions

TRITON .extbc format (per the docs + real examples):

    % BC Type, X1, Y1, X2, Y2, BC
    <type>, x1, y1, x2, y2[, <value>]

Where <type> is one of:

  * 0 — free flow (supercritical).  No final BC value column.
  * 1 — level vs time.  Final column = quoted stage-file filename.
  * 2 — normal slope.    Final column = slope value.
  * 3 — Froude number.   Final column = Froude value.

TRITON src_loc_file format:

    %X-Location,Y-Location
    x1,y1
    x2,y2
    ...

The number of lines in src_loc_file must match ``num_sources`` in the cfg,
and the order must match the discharge columns in the .hyg.

NHD main-river detection is exposed via ``detect_main_river()`` so the GUI can
suggest an inflow point + downstream segment that the user then composes into
the final inflow_sources / bc_entries lists.
"""
import math
import shutil
import ssl
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from shapely.geometry import Point, LineString, MultiLineString
from shapely.ops import linemerge

from core.context import save_context


# ── shared NHD helpers ────────────────────────────────────────────────────────

def _union_geometry(gdf):
    try:
        return gdf.geometry.union_all()
    except Exception:
        return gdf.unary_union


def _safe_name(x):
    x = "" if x is None else str(x).strip()
    return x if x else "Unnamed"


def _to_single_linestring(geom):
    if geom is None or geom.is_empty:
        return None
    if isinstance(geom, LineString):
        return geom
    if isinstance(geom, MultiLineString):
        merged = linemerge(geom)
        if isinstance(merged, LineString):
            return merged
        parts = [g for g in merged.geoms if g is not None and not g.is_empty]
        return max(parts, key=lambda g: g.length) if parts else None
    return None


def _sample_dem(dem_path, x, y):
    with rasterio.open(dem_path) as src:
        val = list(src.sample([(x, y)]))[0][0]
        nodata = src.nodata
    if nodata is not None and np.isfinite(nodata) and val == nodata:
        return np.nan
    return float(val) if np.isfinite(val) else np.nan


def _build_main_river(flowlines_clip):
    if "StreamOrde" not in flowlines_clip.columns:
        raise ValueError("StreamOrde field not found in clipped flowlines.")

    gdf = flowlines_clip.copy()
    gdf["geom_len"] = gdf.geometry.length
    gdf["river_name"] = (
        gdf["GNIS_NAME"].apply(_safe_name) if "GNIS_NAME" in gdf.columns else "Unnamed"
    )

    max_order = gdf["StreamOrde"].max()
    top = gdf[gdf["StreamOrde"] == max_order].copy()

    summary = (
        top.groupby("river_name", dropna=False)
        .agg(
            stream_order=("StreamOrde", "max"),
            segment_count=("river_name", "size"),
            total_length_m=("geom_len", "sum"),
        )
        .reset_index()
        .sort_values(["stream_order", "total_length_m"], ascending=[False, False])
    )

    main_river_name = summary.iloc[0]["river_name"]
    main_order = int(summary.iloc[0]["stream_order"])
    main_total_length_m = float(summary.iloc[0]["total_length_m"])

    main_segments = top[top["river_name"] == main_river_name].copy()
    merged_geom = linemerge(
        main_segments.geometry.union_all()
        if hasattr(main_segments.geometry, "union_all")
        else main_segments.geometry.unary_union
    )
    main_line = _to_single_linestring(merged_geom)

    if main_line is None or main_line.is_empty:
        lines = [
            _to_single_linestring(g)
            for g in main_segments.geometry
            if g is not None
        ]
        lines = [g for g in lines if g is not None]
        if not lines:
            raise RuntimeError("Could not create a valid main river line.")
        main_line = max(lines, key=lambda g: g.length)

    return (
        main_segments,
        summary,
        main_line,
        main_river_name,
        main_order,
        main_total_length_m,
    )


def _mean_end_elevation(line, dem_path, cell_size):
    L = float(line.length)
    if L <= 0:
        raise RuntimeError("Main river line has zero length.")

    dists = [
        max(cell_size * 2.0, 5.0),
        max(cell_size * 5.0, 20.0),
        max(cell_size * 10.0, 50.0),
    ]
    dists = [min(d, max(L * 0.25, 1.0)) for d in dists]

    start_vals, end_vals = [], []
    for d in dists:
        p_start = line.interpolate(d)
        p_end = line.interpolate(max(L - d, 0.0))
        z_s = _sample_dem(dem_path, p_start.x, p_start.y)
        z_e = _sample_dem(dem_path, p_end.x, p_end.y)
        if np.isfinite(z_s):
            start_vals.append(z_s)
        if np.isfinite(z_e):
            end_vals.append(z_e)

    if not start_vals or not end_vals:
        raise RuntimeError("Could not sample DEM elevations near the river ends.")
    return float(np.mean(start_vals)), float(np.mean(end_vals))


def _choose_fid_col(gdf):
    for c in ["COMID", "comid", "featureid", "FEATUREID", "nhdplusid", "NHDPlusID", "id", "ID"]:
        if c in gdf.columns:
            return c
    return None


def _shift_toward(pt, target, distance=100.0):
    dx, dy = target.x - pt.x, target.y - pt.y
    L = math.hypot(dx, dy)
    if L == 0:
        return pt
    return Point(pt.x + (dx / L) * distance, pt.y + (dy / L) * distance)


def _flow_direction_at_point(line, near_start=False):
    """Normalised (dx, dy) of the flow direction at one end of a LineString."""
    coords = list(line.coords)
    if near_start:
        dx = coords[1][0] - coords[0][0]
        dy = coords[1][1] - coords[0][1]
    else:
        dx = coords[-1][0] - coords[-2][0]
        dy = coords[-1][1] - coords[-2][1]
    L = math.hypot(dx, dy)
    if L == 0:
        return 1.0, 0.0
    return dx / L, dy / L


def _perpendicular_segment(cx, cy, flow_dx, flow_dy, half_width):
    """Line segment perpendicular to flow, centred at (cx, cy)."""
    px, py = -flow_dy, flow_dx
    x1 = cx - px * half_width
    y1 = cy - py * half_width
    x2 = cx + px * half_width
    y2 = cy + py * half_width
    return x1, y1, x2, y2


# ── NHD detection exposed as a helper ─────────────────────────────────────────

def detect_main_river(
    ctx: dict,
    *,
    downstream_segment_width: float = None,
    save_diagnostics: bool = True,
    log_fn=print,
):
    """Query NHD, pick the main river, and return a suggested upstream inflow
    point plus a suggested downstream BC line segment.

    Returns a dict with:
        upstream_pt          : (x, y)   — already shifted 100 m into the domain
        downstream_segment   : (x1, y1, x2, y2)
        upstream_reach_id    : str or None
        main_river_name      : str
        main_river_stream_order : int
        flowlines_path       : str (written if save_diagnostics)

    Does not touch the .extbc / src_loc files.
    """
    project_dir  = Path(ctx["project_dir"])
    aoi_path     = Path(ctx["aoi_path"])
    dem_tif_path = Path(ctx["dem_tif_path"])
    aoi_name     = ctx["aoi_name"]

    ssl._create_default_https_context = ssl._create_unverified_context
    try:
        from pynhd import NHD
    except ImportError:
        raise ImportError("pynhd is required.  Install: pip install pynhd")

    from core.aoi import read_aoi
    aoi_gdf = read_aoi(ctx)
    if aoi_gdf.crs is None:
        raise ValueError("AOI has no CRS.")

    with rasterio.open(dem_tif_path) as src:
        dem_cell_size = float(abs(src.res[0]))

    aoi_centroid = _union_geometry(aoi_gdf).centroid

    log_fn("Downloading NHD flowlines…")
    aoi_ll  = aoi_gdf.to_crs("EPSG:4326")
    geom_ll = _union_geometry(aoi_ll)
    nhd     = NHD("flowline_mr")
    flowlines = nhd.bygeom(geom_ll)
    if flowlines is None or flowlines.empty:
        raise RuntimeError("No NHD flowlines found for this AOI.")

    flowlines = flowlines.to_crs(aoi_gdf.crs)
    flowlines_clip = gpd.overlay(
        flowlines, aoi_gdf[["geometry"]], how="intersection"
    )
    flowlines_clip = flowlines_clip[
        flowlines_clip.geometry.type.isin(["LineString", "MultiLineString"])
    ].copy()
    if flowlines_clip.empty:
        raise RuntimeError("No flowlines remain after clipping to AOI.")

    flowlines_path = project_dir / f"NHD_flowlines_{aoi_name}.gpkg"
    if save_diagnostics:
        flowlines_clip.to_file(flowlines_path, driver="GPKG")
        log_fn(f"Flowlines saved: {flowlines_path.name}")

    (
        main_segments,
        summary,
        main_line,
        main_river_name,
        main_order,
        main_total_length_m,
    ) = _build_main_river(flowlines_clip)
    log_fn(f"Main river: {main_river_name}  (stream order {main_order})")

    if save_diagnostics:
        summary.to_csv(project_dir / "main_river_summary.csv", index=False)
        main_segments.to_file(project_dir / "main_river_segments.gpkg", driver="GPKG")
        gpd.GeoDataFrame(
            [{"river_name": main_river_name, "stream_order": main_order,
              "total_length_m": main_total_length_m}],
            geometry=[main_line],
            crs=flowlines_clip.crs,
        ).to_file(project_dir / "main_river_line.gpkg", driver="GPKG")

    coords = list(main_line.coords)
    end1, end2 = Point(coords[0]), Point(coords[-1])
    mean1, mean2 = _mean_end_elevation(main_line, dem_tif_path, dem_cell_size)

    if mean1 >= mean2:
        upstream_pt, downstream_pt = end1, end2
        dn_near_start = False
    else:
        upstream_pt, downstream_pt = end2, end1
        dn_near_start = True

    log_fn(f"Upstream end elevation (mean):   {max(mean1, mean2):.2f} m")
    log_fn(f"Downstream end elevation (mean): {min(mean1, mean2):.2f} m")

    reach_id = None
    fid_col = _choose_fid_col(main_segments)
    if fid_col:
        main_segments = main_segments.copy()
        main_segments["_dist"] = main_segments.geometry.distance(upstream_pt)
        nearest = main_segments.sort_values("_dist").iloc[0]
        try:
            reach_id = str(nearest[fid_col])
        except Exception:
            reach_id = None

    # Shift upstream point 100 m toward centroid so it is inside the domain
    up_inside = _shift_toward(upstream_pt, aoi_centroid, 100.0)

    seg_half_width = (
        downstream_segment_width
        if downstream_segment_width
        else max(dem_cell_size * 5.0, 250.0)
    )
    flow_dx, flow_dy = _flow_direction_at_point(main_line, near_start=dn_near_start)
    dn_x1, dn_y1, dn_x2, dn_y2 = _perpendicular_segment(
        float(downstream_pt.x), float(downstream_pt.y),
        flow_dx, flow_dy, seg_half_width
    )
    log_fn(
        f"Suggested downstream segment: ({dn_x1:.2f}, {dn_y1:.2f}) → ({dn_x2:.2f}, {dn_y2:.2f})"
    )

    return {
        "upstream_pt":             (float(up_inside.x), float(up_inside.y)),
        "downstream_segment":      (dn_x1, dn_y1, dn_x2, dn_y2),
        "upstream_reach_id":       reach_id,
        "main_river_name":         main_river_name,
        "main_river_stream_order": int(main_order),
        "flowlines_path":          str(flowlines_path) if save_diagnostics else None,
    }


# ── file writers ──────────────────────────────────────────────────────────────

def _fmt_num(v):
    """Format a float without Python's repr quirks."""
    if v == int(v):
        return f"{int(v)}"
    return f"{float(v):.6f}".rstrip("0").rstrip(".")


def _write_extbc(extbc_path, entries):
    """Write a TRITON .extbc file.

    entries : list[dict] — each with keys:
        bc_type (int in {0,1,2,3})
        x1, y1, x2, y2 (float)
        value (optional): slope (type 2), Froude (type 3), or filename (type 1)
    """
    lines = ["% BC Type, X1, Y1, X2, Y2, BC"]
    for e in entries:
        bt = int(e["bc_type"])
        if bt not in (0, 1, 2, 3):
            raise ValueError(f"Unsupported BC type: {bt} (expected 0, 1, 2, or 3)")
        x1 = _fmt_num(float(e["x1"]))
        y1 = _fmt_num(float(e["y1"]))
        x2 = _fmt_num(float(e["x2"]))
        y2 = _fmt_num(float(e["y2"]))
        if bt == 0:
            lines.append(f"{bt},{x1},{y1},{x2},{y2}")
        elif bt == 1:
            val = e.get("value")
            if not isinstance(val, str) or not val.strip():
                raise ValueError("Type 1 BC requires a stage filename in `value`.")
            lines.append(f'{bt},{x1},{y1},{x2},{y2},"{val}"')
        else:  # 2 or 3
            val = e.get("value")
            if val is None:
                raise ValueError(f"Type {bt} BC requires a numeric value.")
            lines.append(f"{bt},{x1},{y1},{x2},{y2},{_fmt_num(float(val))}")

    extbc_path.parent.mkdir(parents=True, exist_ok=True)
    extbc_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_src_loc_file(src_loc_path, points):
    """Write the src_loc_file with the documented comment header.

    points : iterable of (x, y) tuples
    """
    lines = ["%X-Location,Y-Location"]
    for x, y in points:
        lines.append(f"{_fmt_num(float(x))},{_fmt_num(float(y))}")
    src_loc_path.parent.mkdir(parents=True, exist_ok=True)
    src_loc_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── public API ────────────────────────────────────────────────────────────────

def prepare_triton_bc(
    ctx_path,
    ctx: dict,
    *,
    inflow_sources,          # list[tuple[float, float]]
    bc_entries,              # list[dict] — see _write_extbc()
    extbc_filename: str = None,     # default {project_name}.extbc
    src_loc_filename: str = None,   # default {project_name}_inflow_loc.txt
    # Optional — for records
    main_river_name: str = None,
    upstream_reach_id: str = None,
    log_fn=print,
):
    """Write the .extbc and src_loc_file from user-staged lists.

    The caller (GUI) is responsible for composing the lists.  Use
    detect_main_river() to prefill a suggested inflow point and downstream
    segment; the user then reviews and possibly adds more BCs / sources
    before running this.

    Type-1 entries may supply either ``value`` already set to the stage
    filename inside triton_dir, or ``stage_file_path`` — an absolute path to
    a stage file that this function will copy into triton_dir and rewrite the
    entry's ``value`` to the basename.
    """
    if not inflow_sources:
        raise ValueError("inflow_sources is empty — at least one upstream point required.")
    if not bc_entries:
        raise ValueError("bc_entries is empty — at least one external BC required.")

    project_dir  = Path(ctx["project_dir"])
    triton_dir   = Path(ctx["triton_dir"])
    project_name = ctx.get("project_name", "triton")

    extbc_filename   = extbc_filename   or f"{project_name}.extbc"
    src_loc_filename = src_loc_filename or f"{project_name}_inflow_loc.txt"

    # Copy stage files for type-1 entries + rewrite `value`
    processed = []
    for e in bc_entries:
        entry = dict(e)
        bt = int(entry["bc_type"])
        if bt == 1:
            if "value" in entry and isinstance(entry["value"], str) and entry["value"].strip():
                # Value already set to a filename — trust it.
                pass
            else:
                stage_src = entry.get("stage_file_path")
                if not stage_src:
                    raise ValueError(
                        "Type 1 BC entry missing both `value` and `stage_file_path`."
                    )
                stage_src = Path(stage_src)
                if not stage_src.exists():
                    raise FileNotFoundError(f"Stage file not found: {stage_src}")
                dest = triton_dir / stage_src.name
                if dest.resolve() != stage_src.resolve():
                    shutil.copy2(stage_src, dest)
                    log_fn(f"Stage file copied: {dest.name}")
                entry["value"] = dest.name
            entry.pop("stage_file_path", None)
        processed.append(entry)

    # Write src_loc_file
    src_loc_path = triton_dir / src_loc_filename
    _write_src_loc_file(src_loc_path, inflow_sources)
    log_fn(f"Source locations written: {src_loc_path.name}  ({len(inflow_sources)} points)")

    # Write .extbc
    extbc_path = triton_dir / extbc_filename
    _write_extbc(extbc_path, processed)
    log_fn(f"External BC file written: {extbc_path.name}  ({len(processed)} entries)")

    # Update context
    ctx["num_sources"]              = len(inflow_sources)
    ctx["num_extbc"]                = len(processed)
    ctx["inflow_source_points"]     = [[float(x), float(y)] for x, y in inflow_sources]
    ctx["triton_extbc_path"]        = str(extbc_path)
    ctx["triton_extbc_filename"]    = extbc_filename
    ctx["triton_src_loc_path"]      = str(src_loc_path)
    ctx["triton_src_loc_filename"]  = src_loc_filename
    ctx["triton_extbc_entries"]     = [
        {k: v for k, v in e.items() if k != "stage_file_path"}
        for e in processed
    ]
    ctx["triton_extbc_dir"]         = str(triton_dir) + "/"
    ctx["triton_extbc_written"]     = True
    if main_river_name:
        ctx["main_river_name"]     = main_river_name
        ctx["main_feature_name"]   = main_river_name
    if upstream_reach_id:
        ctx["upstream_reach_id"]   = upstream_reach_id

    # Back-compat: keep single-source convenience keys when there's only one
    if len(inflow_sources) == 1:
        ctx["upstream_x"] = float(inflow_sources[0][0])
        ctx["upstream_y"] = float(inflow_sources[0][1])

    save_context(ctx_path, ctx)
    return ctx
