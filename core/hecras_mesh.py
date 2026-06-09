"""HEC-RAS 2D mesh generation.

Generates a triangular computational mesh for HEC-RAS 2D using gmsh,
samples DEM elevations at cell centres, assigns Manning's n, and writes
an HEC-RAS-compatible HDF5 geometry file (Geometry.g01.hdf) plus a
plain-text header (Geometry.g01).
"""
from __future__ import annotations

import h5py
import numpy as np
from pathlib import Path
from typing import Optional


def build_hecras_geometry(
    aoi_path: str,
    feature_index: int,
    river_path: Optional[str],
    dem_path: str,
    manning_shp_path: Optional[str],
    output_dir: str,
    area_name: str = "Domain",
    cell_size_near: float = 10.0,
    cell_size_far: float = 100.0,
    refine_buffer_m: float = 150.0,
    default_manning: float = 0.04,
    log_fn=print,
) -> dict:
    """Build a triangular HEC-RAS 2D mesh and write geometry files.

    Parameters
    ----------
    aoi_path:          Path to AOI shapefile / GeoPackage.
    feature_index:     Index of the feature to use (None → use all / first).
    river_path:        Optional path to river centreline shapefile for channel refinement.
    dem_path:          Path to the DEM raster used for cell elevation sampling.
    manning_shp_path:  Optional path to a Manning's-n polygon shapefile.
    output_dir:        Directory where output files are written.
    area_name:         Name of the 2-D flow area (used inside the HDF).
    cell_size_near:    Target mesh size (m) near the channel.
    cell_size_far:     Target mesh size (m) in the far-field / floodplain.
    refine_buffer_m:   Distance (m) over which the mesh size transitions
                       from cell_size_near to cell_size_far.
    default_manning:   Fall-back Manning's n applied to every cell.
    log_fn:            Callable for log messages (default: print).

    Returns
    -------
    dict with keys: hdf_path, geom_path, n_cells, n_faces, n_points,
                    cell_size_near, cell_size_far, epsg.
    """

    # ── A. Load and reproject geometry ────────────────────────────────────────
    import geopandas as gpd
    from shapely.ops import unary_union

    log_fn(f"  Loading AOI from {aoi_path}")
    aoi = gpd.read_file(aoi_path)
    if feature_index is not None and len(aoi) > 1:
        aoi = aoi.iloc[[feature_index]]

    from core.crs_utils import pick_working_crs_epsg
    epsg = pick_working_crs_epsg(aoi)
    aoi_proj = aoi.to_crs(epsg)
    poly = aoi_proj.geometry.iloc[0]

    river_line = None
    if river_path and Path(river_path).exists():
        log_fn(f"  Loading river centreline from {river_path}")
        river_gdf = gpd.read_file(river_path).to_crs(epsg)
        river_line = unary_union(river_gdf.geometry)

    # ── B. Generate triangular mesh with gmsh ─────────────────────────────────
    try:
        import gmsh
    except ImportError:
        log_fn("gmsh not installed — run: pip install gmsh")
        raise

    log_fn("  Initialising gmsh …")
    gmsh.initialize()
    gmsh.model.add(area_name)
    gmsh.option.setNumber("General.Verbosity", 1)

    # Add AOI polygon boundary
    coords = list(poly.exterior.coords)
    pt_tags = []
    for x, y in coords[:-1]:
        pt_tags.append(gmsh.model.geo.addPoint(x, y, 0.0, cell_size_far))

    line_tags = []
    for i in range(len(pt_tags)):
        line_tags.append(
            gmsh.model.geo.addLine(pt_tags[i], pt_tags[(i + 1) % len(pt_tags)])
        )
    loop = gmsh.model.geo.addCurveLoop(line_tags)
    surf = gmsh.model.geo.addPlaneSurface([loop])

    # Embed river line for channel refinement
    river_pt_tags = []
    river_line_tags = []
    if river_line is not None:
        log_fn("  Embedding river centreline for channel refinement …")
        from shapely.geometry import LineString  # noqa: F401 (used implicitly)
        lines = list(river_line.geoms) if hasattr(river_line, "geoms") else [river_line]
        for seg in lines:
            seg_pts = []
            for x, y in list(seg.coords):
                tag = gmsh.model.geo.addPoint(x, y, 0.0, cell_size_near)
                seg_pts.append(tag)
            for i in range(len(seg_pts) - 1):
                lt = gmsh.model.geo.addLine(seg_pts[i], seg_pts[i + 1])
                river_line_tags.append(lt)
                river_pt_tags.extend([seg_pts[i], seg_pts[i + 1]])

    gmsh.model.geo.synchronize()

    if river_line_tags:
        gmsh.model.mesh.embed(1, river_line_tags, 2, surf)
        gmsh.model.mesh.embed(0, list(set(river_pt_tags)), 2, surf)
        gmsh.model.geo.synchronize()

    # Distance field for smooth size transition
    if river_line_tags:
        f_dist = gmsh.model.mesh.field.add("Distance")
        gmsh.model.mesh.field.setNumbers(f_dist, "CurvesList", river_line_tags)
        f_thresh = gmsh.model.mesh.field.add("Threshold")
        gmsh.model.mesh.field.setNumber(f_thresh, "InField", f_dist)
        gmsh.model.mesh.field.setNumber(f_thresh, "SizeMin", cell_size_near)
        gmsh.model.mesh.field.setNumber(f_thresh, "SizeMax", cell_size_far)
        gmsh.model.mesh.field.setNumber(f_thresh, "DistMin", 0.0)
        gmsh.model.mesh.field.setNumber(f_thresh, "DistMax", refine_buffer_m)
        gmsh.model.mesh.field.setAsBackgroundMesh(f_thresh)

    gmsh.option.setNumber("Mesh.Algorithm", 8)  # Frontal-Delaunay
    log_fn("  Generating 2-D mesh …")
    gmsh.model.mesh.generate(2)
    gmsh.model.mesh.optimize("Netgen")

    # Extract mesh data
    node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
    # node_coords is flat [x0,y0,z0, x1,y1,z1, ...]
    pts = node_coords.reshape(-1, 3)[:, :2]   # (M, 2) XY

    elem_types, elem_tags, elem_node_tags = gmsh.model.mesh.getElements(2)
    # Find triangles (type 2)
    tri_nodes = None
    for etype, etags, enodes in zip(elem_types, elem_tags, elem_node_tags):
        if etype == 2:  # triangle
            tri_nodes = enodes.reshape(-1, 3) - 1  # 0-based indices
            break

    gmsh.finalize()

    if tri_nodes is None:
        raise RuntimeError("gmsh produced no triangular elements.")

    log_fn(f"  Mesh: {len(pts)} nodes, {len(tri_nodes)} triangles")

    # ── C. Build mesh topology (cells, faces) ─────────────────────────────────
    # cell_centers: centroid of each triangle
    cell_centers = pts[tri_nodes].mean(axis=1)   # (N, 2)
    N = len(tri_nodes)

    # Build face (edge) list and cell adjacency
    face_dict = {}   # (a,b) → [cell_idx, ...]
    for ci, tri in enumerate(tri_nodes):
        for j in range(3):
            a, b = int(tri[j]), int(tri[(j + 1) % 3])
            edge = (min(a, b), max(a, b))
            face_dict.setdefault(edge, []).append(ci)

    faces = list(face_dict.keys())              # list of (a,b)
    face_cells = [face_dict[e] for e in faces]  # list of [cell] or [cell,cell]

    # Build arrays for HDF5
    face_pt_indexes = np.array(faces, dtype=np.int32)           # (F, 2)
    face_cell_indexes = np.array(
        [fc if len(fc) == 2 else [fc[0], -1] for fc in face_cells],
        dtype=np.int32,
    )   # (F, 2)  -1 = boundary

    # Face normals and lengths
    face_normals_lengths = []
    for (a, b), _ in zip(faces, face_cells):
        dx = pts[b, 0] - pts[a, 0]
        dy = pts[b, 1] - pts[a, 1]
        length = np.hypot(dx, dy)
        nx = dy / length if length > 0 else 0.0
        ny = -dx / length if length > 0 else 0.0
        face_normals_lengths.append([nx, ny, length])
    face_nl = np.array(face_normals_lengths, dtype=np.float32)  # (F, 3)

    # ── D. Sample DEM for cell elevations ─────────────────────────────────────
    log_fn("  Sampling DEM elevations at cell centres …")
    import rasterio
    from pyproj import Transformer

    with rasterio.open(dem_path) as dem_src:
        tf = Transformer.from_crs(epsg, dem_src.crs, always_xy=True)
        cx_dem, cy_dem = tf.transform(cell_centers[:, 0], cell_centers[:, 1])
        cell_elevs = np.array(
            list(dem_src.sample(zip(cx_dem, cy_dem))), dtype=np.float32
        ).ravel()
        nodata = dem_src.nodata
        if nodata is not None:
            cell_elevs = np.where(cell_elevs == nodata, 0.0, cell_elevs)

    # ── E. Assign Manning's n per cell ────────────────────────────────────────
    mannings = np.full(N, default_manning, dtype=np.float32)
    if manning_shp_path and Path(manning_shp_path).exists():
        log_fn("  Assigning Manning's n from shapefile …")
        try:
            mn_gdf = gpd.read_file(manning_shp_path).to_crs(epsg)
            from shapely.geometry import Point
            for ci, (cx, cy) in enumerate(cell_centers):
                pt = Point(cx, cy)
                for _, row in mn_gdf.iterrows():
                    if row.geometry and row.geometry.contains(pt):
                        n_val = (
                            row.get("Manning_n")
                            or row.get("n")
                            or default_manning
                        )
                        mannings[ci] = float(n_val)
                        break
        except Exception as ex:
            log_fn(
                f"  Manning assignment failed: {ex} — using default {default_manning}"
            )

    # ── F. Write HEC-RAS HDF5 geometry file ───────────────────────────────────
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    out_hdf = Path(output_dir) / "Geometry.g01.hdf"
    log_fn(f"  Writing HDF geometry: {out_hdf}")

    with h5py.File(str(out_hdf), "w") as f:
        f.attrs["File Version"] = "6.0"
        f.attrs["Geometry Title"] = area_name

        geom = f.create_group("Geometry")
        geom.attrs["Units System (US Customary=0, SI=1)"] = np.int32(1)

        areas = geom.create_group("2D Flow Areas")
        dt = h5py.special_dtype(vlen=str)
        areas.create_dataset(
            "Names", data=np.array([area_name], dtype=object), dtype=dt
        )

        area = areas.create_group(area_name)
        area.create_dataset(
            "FacePoints Coordinate", data=pts.astype(np.float64)
        )
        area.create_dataset(
            "Cells Center Coordinate", data=cell_centers.astype(np.float64)
        )
        area.create_dataset("Cells Minimum Elevation", data=cell_elevs)
        area.create_dataset("Manning's n", data=mannings)
        area.create_dataset("Faces FacePoint Indexes", data=face_pt_indexes)
        area.create_dataset("Faces Cell Indexes", data=face_cell_indexes)
        area.create_dataset(
            "Faces NormalUnitVector and Length", data=face_nl
        )

        # Cell surface areas (triangle area via cross-product)
        def _tri_area(a, b, c):
            ab = pts[b] - pts[a]
            ac = pts[c] - pts[a]
            return 0.5 * abs(ab[0] * ac[1] - ab[1] * ac[0])

        cell_areas = np.array(
            [_tri_area(*t) for t in tri_nodes], dtype=np.float32
        )
        area.create_dataset("Cells Surface Area", data=cell_areas)

    # ── G. Write geometry text header file (.g01) ─────────────────────────────
    out_geom = Path(output_dir) / "Geometry.g01"
    out_geom.write_text(
        f"Geom Title={area_name}\n"
        f"Program Version=6.5\n"
        f"Viewing Rectangle= 0 0 0 0\n"
    )
    log_fn(f"  Written: {out_geom}")

    # ── H. Return summary ─────────────────────────────────────────────────────
    return {
        "hdf_path": str(out_hdf),
        "geom_path": str(out_geom),
        "n_cells": N,
        "n_faces": len(faces),
        "n_points": len(pts),
        "cell_size_near": cell_size_near,
        "cell_size_far": cell_size_far,
        "epsg": epsg,
    }
