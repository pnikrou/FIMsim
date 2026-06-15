"""Step 6 — Download NHD flowlines, find main river, write <AOI>.bci."""
import math
import ssl
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from shapely.geometry import Point, LineString, MultiLineString
from shapely.ops import linemerge

from core.context import save_context


# ── helpers ──────────────────────────────────────────────────────────────────

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
    gdf["river_name"] = gdf["GNIS_NAME"].apply(_safe_name) if "GNIS_NAME" in gdf.columns else "Unnamed"

    max_order = gdf["StreamOrde"].max()
    top = gdf[gdf["StreamOrde"] == max_order].copy()

    summary = (
        top.groupby("river_name", dropna=False)
        .agg(stream_order=("StreamOrde", "max"), segment_count=("river_name", "size"),
             total_length_m=("geom_len", "sum"))
        .reset_index()
        .sort_values(["stream_order", "total_length_m"], ascending=[False, False])
    )

    main_river_name = summary.iloc[0]["river_name"]
    main_order = int(summary.iloc[0]["stream_order"])
    main_total_length_m = float(summary.iloc[0]["total_length_m"])

    main_segments = top[top["river_name"] == main_river_name].copy()
    unioned = (main_segments.geometry.union_all() if hasattr(main_segments.geometry, 'union_all')
               else main_segments.geometry.unary_union)
    merged_geom = unioned if isinstance(unioned, LineString) else linemerge(unioned)
    main_line = _to_single_linestring(merged_geom)

    if main_line is None or main_line.is_empty:
        lines = [_to_single_linestring(g) for g in main_segments.geometry if g is not None]
        lines = [g for g in lines if g is not None]
        if not lines:
            raise RuntimeError("Could not create a valid main river line.")
        main_line = max(lines, key=lambda g: g.length)

    return main_segments, summary, main_line, main_river_name, main_order, main_total_length_m


def _mean_end_elevation(line, dem_path, cell_size):
    L = float(line.length)
    if L <= 0:
        raise RuntimeError("Main river line has zero length.")

    dists = [max(cell_size * 2.0, 5.0), max(cell_size * 5.0, 20.0), max(cell_size * 10.0, 50.0)]
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


def _shift_toward(pt, target, distance=100.0):
    dx, dy = target.x - pt.x, target.y - pt.y
    L = math.hypot(dx, dy)
    if L == 0:
        return pt
    return Point(pt.x + (dx / L) * distance, pt.y + (dy / L) * distance)


def _choose_fid_col(gdf):
    for c in ["COMID", "comid", "featureid", "FEATUREID", "nhdplusid", "NHDPlusID", "id", "ID"]:
        if c in gdf.columns:
            return c
    return None


# ── public API ────────────────────────────────────────────────────────────────

def create_bci(
    ctx_path,
    ctx: dict,
    upstream_mode: str,          # "fixed_discharge" | "varying_discharge"
    downstream_type: str,        # "FREE" | "HFIX"
    fixed_discharge_cms: float = None,
    downstream_slope: float = None,
    downstream_hfix: float = None,
    # Coordinate detection mode
    use_nhd: bool = True,
    manual_upstream_x: float = None,
    manual_upstream_y: float = None,
    manual_downstream_x: float = None,
    manual_downstream_y: float = None,
    log_fn=print,
):
    """Download NHD flowlines (or use manual coordinates), determine boundary
    points, and write <AOI>.bci.

    When use_nhd=True the function downloads NHD flowlines, identifies the
    main river via stream order and DEM elevation, and derives the upstream /
    downstream points automatically.  This works for USA only.

    When use_nhd=False the caller supplies the boundary coordinates manually
    (manual_upstream_x/y, manual_downstream_x/y).

    Returns updated ctx.
    """
    project_dir  = Path(ctx["project_dir"])
    lisflood_dir = Path(ctx["lisflood_dir"])
    aoi_path     = Path(ctx["aoi_path"])
    dem_tif_path = Path(ctx["dem_tif_path"])
    aoi_name     = ctx["aoi_name"]

    upstream_reach_id = None
    upstream_pt = downstream_pt = None

    if use_nhd:
        ssl._create_default_https_context = ssl._create_unverified_context

        try:
            from pynhd import NHD
        except ImportError:
            raise ImportError("pynhd is required. Install it: pip install pynhd")

        from core.aoi import read_aoi
        aoi_gdf = read_aoi(ctx)
        if aoi_gdf.crs is None:
            raise ValueError("AOI has no CRS.")

        with rasterio.open(dem_tif_path) as src:
            dem_cell_size = float(abs(src.res[0]))

        aoi_centroid = _union_geometry(aoi_gdf).centroid

        log_fn("Downloading NHD flowlines...")
        aoi_ll  = aoi_gdf.to_crs("EPSG:4326")
        geom_ll = _union_geometry(aoi_ll)

        nhd = NHD("flowline_mr")
        try:
            flowlines = nhd.bygeom(geom_ll)
        except Exception as ex:
            msg = str(ex)
            # NHD's bygeom rejects MultiPolygon — fall back to the bbox.
            if "should be of type" in msg or "MultiPolygon" in msg:
                flowlines = nhd.bygeom(tuple(geom_ll.bounds))
            else:
                raise
        if flowlines is None or flowlines.empty:
            raise RuntimeError("No NHD flowlines found for this AOI.")

        flowlines      = flowlines.to_crs(aoi_gdf.crs)
        flowlines_clip = gpd.overlay(flowlines, aoi_gdf[["geometry"]], how="intersection")
        flowlines_clip = flowlines_clip[
            flowlines_clip.geometry.type.isin(["LineString", "MultiLineString"])
        ].copy()
        if flowlines_clip.empty:
            raise RuntimeError("No flowlines remain after clipping to AOI.")

        # Save diagnostic files — use next_free_path so re-runs don't crash
        from core.export import next_free_path as _nfp
        flowlines_path = _nfp(project_dir, f"NHD_flowlines_{aoi_name}", "gpkg")
        if flowlines_path.exists():
            flowlines_path.unlink(missing_ok=True)
        flowlines_clip.to_file(flowlines_path, driver="GPKG")
        log_fn(f"Flowlines saved: {flowlines_path.name}")

        # Build main river from highest stream-order segments
        (
            main_segments,
            summary,
            main_line,
            main_river_name,
            main_order,
            main_total_length_m,
        ) = _build_main_river(flowlines_clip)
        log_fn(f"Main river: {main_river_name}  (stream order {main_order})")

        summary.to_csv(project_dir / "main_river_summary.csv", index=False)
        # Remove stale GPKG files before writing — GPKG driver raises if they exist
        for _gpkg in (
            project_dir / "main_river_segments.gpkg",
            project_dir / "main_river_line.gpkg",
        ):
            if _gpkg.exists():
                try:
                    _gpkg.unlink()
                except Exception:
                    pass
        main_segments.to_file(project_dir / "main_river_segments.gpkg", driver="GPKG")
        gpd.GeoDataFrame(
            [{"river_name": main_river_name, "stream_order": main_order,
              "total_length_m": main_total_length_m}],
            geometry=[main_line], crs=flowlines_clip.crs,
        ).to_file(project_dir / "main_river_line.gpkg", driver="GPKG")

        # ── Reproject main_line + AOI centroid to DEM CRS ───────────────
        # The flowlines are in the AOI's CRS (often EPSG:4326).  The DEM is
        # reprojected to a local UTM by run_lisflood_triton_dem_all, so its
        # CRS is different.  Sampling a UTM raster with lat/lon coords always
        # returns nodata → "Could not sample DEM elevations".  Fix: bring the
        # river geometry into the DEM's CRS before any elevation work AND
        # before writing the BCI file (coordinates must match the model grid).
        with rasterio.open(dem_tif_path) as _dsrc:
            dem_crs = _dsrc.crs

        _flowline_crs = flowlines_clip.crs
        _need_reproject = (
            dem_crs is not None
            and _flowline_crs is not None
            and dem_crs.to_epsg() != _flowline_crs.to_epsg()
        )
        if _need_reproject:
            from pyproj import Transformer
            from shapely.ops import transform as _shp_transform
            _tf = Transformer.from_crs(_flowline_crs, dem_crs, always_xy=True)
            main_line       = _shp_transform(_tf.transform, main_line)
            aoi_centroid_bci = Point(*_tf.transform(aoi_centroid.x, aoi_centroid.y))
            log_fn(
                f"  Reprojected river line: {_flowline_crs.to_epsg()} → "
                f"{dem_crs.to_epsg()} (DEM CRS)."
            )
        else:
            aoi_centroid_bci = aoi_centroid

        # Determine upstream/downstream ends from DEM elevation
        coords = list(main_line.coords)
        end1, end2 = Point(coords[0]), Point(coords[-1])
        mean1, mean2 = _mean_end_elevation(main_line, dem_tif_path, dem_cell_size)

        if mean1 >= mean2:
            upstream_pt, downstream_pt = end1, end2
        else:
            upstream_pt, downstream_pt = end2, end1

        log_fn(f"Upstream end elevation (mean):   {max(mean1, mean2):.2f} m")
        log_fn(f"Downstream end elevation (mean): {min(mean1, mean2):.2f} m")

        # Find upstream reach id for NWM (used by BDY step).
        # main_segments is still in the original flowline CRS; upstream_pt is
        # now in DEM CRS — reproject it back for an accurate distance lookup.
        fid_col = _choose_fid_col(main_segments)
        if fid_col:
            if _need_reproject:
                _tf_back = Transformer.from_crs(dem_crs, _flowline_crs, always_xy=True)
                _up_pt_fl_crs = Point(*_tf_back.transform(upstream_pt.x, upstream_pt.y))
            else:
                _up_pt_fl_crs = upstream_pt
            main_segments = main_segments.copy()
            main_segments["_dist"] = main_segments.geometry.distance(_up_pt_fl_crs)
            nearest = main_segments.sort_values("_dist").iloc[0]
            try:
                upstream_reach_id = str(nearest[fid_col])
            except Exception:
                pass

        # Shift 100 m toward centroid so the point is safely inside the domain
        bci_up_pt = _shift_toward(upstream_pt, aoi_centroid_bci, 100.0)
        bci_dn_pt = _shift_toward(downstream_pt, aoi_centroid_bci, 100.0)

        ctx["flowlines_path"]           = str(flowlines_path)
        ctx["main_river_name"]          = main_river_name
        ctx["main_river_stream_order"]  = int(main_order)
        ctx["main_feature_name"]        = main_river_name
        ctx["upstream_x"]               = float(upstream_pt.x)
        ctx["upstream_y"]               = float(upstream_pt.y)
        ctx["downstream_x"]             = float(downstream_pt.x)
        ctx["downstream_y"]             = float(downstream_pt.y)

    else:
        # Manual coordinate mode
        for name, val in [
            ("manual_upstream_x",   manual_upstream_x),
            ("manual_upstream_y",   manual_upstream_y),
            ("manual_downstream_x", manual_downstream_x),
            ("manual_downstream_y", manual_downstream_y),
        ]:
            if val is None:
                raise ValueError(f"{name} must be provided when use_nhd=False.")

        bci_up_pt = Point(float(manual_upstream_x),   float(manual_upstream_y))
        bci_dn_pt = Point(float(manual_downstream_x), float(manual_downstream_y))
        log_fn(
            f"Manual boundary coordinates — "
            f"upstream: ({bci_up_pt.x:.3f}, {bci_up_pt.y:.3f}), "
            f"downstream: ({bci_dn_pt.x:.3f}, {bci_dn_pt.y:.3f})"
        )

        ctx["upstream_x"]   = float(manual_upstream_x)
        ctx["upstream_y"]   = float(manual_upstream_y)
        ctx["downstream_x"] = float(manual_downstream_x)
        ctx["downstream_y"] = float(manual_downstream_y)

    # ── Write <AOI name>.bci ────────────────────────────────────────────────────
    # The .bci file is named after this AOI so each AOI's boundary file is
    # uniquely identifiable.  ``next_free_path`` versions a re-run as
    # "<AOI> (1).bci", "<AOI> (2).bci" … instead of overwriting the previous
    # file.  The PAR step reads the actual filename from ctx and writes it
    # into the .par file.
    from core.export import next_free_path
    bci_path = next_free_path(lisflood_dir, aoi_name, "bci")

    if upstream_mode == "fixed_discharge":
        up_line = (
            f"P\t{bci_up_pt.x:.3f}\t{bci_up_pt.y:.3f}\tQFIX\t{fixed_discharge_cms}"
        )
    else:
        up_line = (
            f"P\t{bci_up_pt.x:.3f}\t{bci_up_pt.y:.3f}\tQVAR\tupstream1"
        )

    if downstream_type == "FREE":
        dn_line = (
            f"E\t{bci_dn_pt.x:.3f}\t{bci_dn_pt.y:.3f}\tFREE\t{downstream_slope}"
        )
    else:
        dn_line = (
            f"E\t{bci_dn_pt.x:.3f}\t{bci_dn_pt.y:.3f}\tHFIX\t{downstream_hfix}"
        )

    bci_path.write_text(up_line + "\n" + dn_line + "\n", encoding="utf-8")
    log_fn(f"{bci_path.name} written: {bci_path}")

    # ── Update context ────────────────────────────────────────────────────────
    ctx["upstream_mode"]        = upstream_mode
    ctx["fixed_discharge_cms"]  = fixed_discharge_cms
    ctx["downstream_type"]      = downstream_type
    ctx["downstream_slope"]     = downstream_slope
    ctx["downstream_hfix"]      = downstream_hfix
    ctx["upstream_reach_id"]    = upstream_reach_id
    ctx["bci_path"]             = str(bci_path)
    # Placeholder companion .bdy path (named after this AOI); the BDY step
    # overwrites this with the real file once it runs.
    ctx["bdy_path"]             = str(lisflood_dir / f"{aoi_name}.bdy")
    ctx["bci_written"]          = True
    save_context(ctx_path, ctx)
    return ctx
