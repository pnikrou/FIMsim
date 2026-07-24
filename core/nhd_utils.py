"""Shared NHD flowline utilities used by both LISFLOOD BCI and TRITON BC steps."""
import rasterio
from shapely.geometry import Point, LineString
from shapely.ops import linemerge


def _nhd_bygeom(nhd, geom, max_attempts=3, retry_delay=5.0):
    """Call nhd.bygeom() with retry on transient 5xx/network errors.

    Falls back to bbox when the service rejects a MultiPolygon geometry.
    Raises RuntimeError with a friendly message after all attempts are exhausted.
    """
    import time

    def _is_transient(msg):
        return any(k in msg for k in ("504", "503", "502", "Gateway", "Timeout",
                                       "timed out", "timeout", "Connection", "connection"))

    def _is_multipoly(msg):
        return "should be of type" in msg or "MultiPolygon" in msg

    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            return nhd.bygeom(geom)
        except Exception as ex:
            msg = str(ex)
            if _is_multipoly(msg):
                return nhd.bygeom(tuple(geom.bounds))
            if _is_transient(msg):
                last_err = ex
                if attempt < max_attempts:
                    time.sleep(retry_delay * attempt)
                continue
            raise

    raise RuntimeError(
        f"NHD service temporarily unavailable after {max_attempts} attempts "
        f"(last error: {last_err}). Please try again in a few minutes."
    )


def _extend_to_boundary(main_segs, all_clips, aoi_geom, snap_tol=500.0, max_iters=40):
    """Extend main river segments toward the AOI boundary by snapping to connected flowlines.

    After picking the named main-river segments, their merged line may stop
    in the middle of the domain (the named river ends but a different-named
    reach continues to the boundary).  This function iteratively adds the
    nearest unselected flowline whose endpoint is within snap_tol of any
    current open endpoint (an endpoint that is not already on the AOI
    boundary), until both ends reach the boundary or no close segment exists.
    """
    import pandas as pd

    aoi_boundary = aoi_geom.boundary
    selected_idx = set(main_segs.index)
    combined = main_segs.copy()

    def _open_endpoints(segs):
        """Return Points of merged-line endpoints not touching the AOI boundary."""
        try:
            union = segs.geometry.union_all()
        except Exception:
            union = segs.geometry.unary_union
        merged = union if isinstance(union, LineString) else linemerge(union)
        lines = [merged] if isinstance(merged, LineString) else (
            list(merged.geoms) if hasattr(merged, "geoms") else []
        )
        open_pts = []
        for g in lines:
            if g is None or g.is_empty:
                continue
            for coord in [g.coords[0], g.coords[-1]]:
                pt = Point(coord)
                if aoi_boundary.distance(pt) > snap_tol * 0.3:
                    open_pts.append(pt)
        return open_pts

    for _ in range(max_iters):
        open_pts = _open_endpoints(combined)
        if not open_pts:
            break

        remaining = all_clips[~all_clips.index.isin(selected_idx)]
        if remaining.empty:
            break

        candidates = []
        for idx, row in remaining.iterrows():
            g = row.geometry
            if g is None or g.is_empty:
                continue
            if isinstance(g, LineString):
                ep_coords = [g.coords[0], g.coords[-1]]
            else:
                try:
                    ep_coords = [list(g.geoms[0].coords)[0], list(g.geoms[-1].coords)[-1]]
                except Exception:
                    continue
            order = int(row.get("StreamOrde", 0)) if hasattr(row, "get") else 0
            for coord in ep_coords:
                ep = Point(coord)
                for op in open_pts:
                    d = op.distance(ep)
                    candidates.append((d, -order, idx))

        if not candidates:
            break

        candidates.sort(key=lambda x: (x[0], x[1]))
        best_d, _, best_idx = candidates[0]

        if best_d > snap_tol:
            break

        combined = pd.concat([combined, all_clips.loc[[best_idx]]])
        selected_idx.add(best_idx)

    return combined


def _extrapolate_to_dem_bounds(line, dem_path, snap_tol=50.0, log_fn=print):
    """Snap line endpoints to the nearest point on the DEM bounding box.

    When NHD flowlines run out before the domain boundary, `_extend_to_boundary`
    stops with the line still interior.  This function finishes the job by
    projecting each open endpoint to the geometrically NEAREST point on the DEM
    bounding box.
    """
    from shapely.geometry import box as _box

    with rasterio.open(dem_path) as src:
        b = src.bounds
    dem_bdry = _box(b.left, b.bottom, b.right, b.top).boundary

    coords = list(line.coords)
    if len(coords) < 2:
        return line

    def _nearest_on_boundary(coord):
        pt = Point(coord)
        gap = dem_bdry.distance(pt)
        if gap <= snap_tol:
            return None, 0.0
        nearest = dem_bdry.interpolate(dem_bdry.project(pt))
        return (nearest.x, nearest.y), gap

    new_end,   gap_end   = _nearest_on_boundary(coords[-1])
    new_start, gap_start = _nearest_on_boundary(coords[0])

    new_coords = list(coords)
    if new_start is not None:
        new_coords.insert(0, new_start)
        log_fn(f"  Snapped start endpoint to nearest domain boundary ({gap_start:.0f} m gap closed)")
    if new_end is not None:
        new_coords.append(new_end)
        log_fn(f"  Snapped end endpoint to nearest domain boundary ({gap_end:.0f} m gap closed)")

    return LineString(new_coords) if (new_start or new_end) else line
