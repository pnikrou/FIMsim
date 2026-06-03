"""Multi-AOI data structures and orchestration."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple, Union

import geopandas as gpd
import numpy as np

from core.crs_utils import (
    nad83_utm_epsg_from_lonlat,
    wgs84_utm_epsg_from_lonlat,
    working_crs_label,
)
from core.project import clean_name
from core.state_lookup import detect_us_state


# Per-AOI sub-folders that hold the files needed to RUN the model.
# DEM ASCII, Manning ASCII, BCI, BDY and PAR all live inside one of
# these.  Intermediate / scratch files (raw DEM tiles, LULC GeoTIFFs,
# helper CSVs …) stay in the AOI folder root.
LISFLOOD_FILES_SUBDIR = "lisflood-files"
TRITON_FILES_SUBDIR   = "triton-files"


def model_files_subdir(
    aoi_folder: Union[str, Path], *, is_triton: bool = False,
) -> str:
    """Return ``<aoi_folder>/lisflood-files`` (or ``triton-files``).

    The directory is created if it doesn't exist.  Use this helper
    everywhere a per-AOI model directory is needed so the convention
    stays consistent across DEM / Manning / BCI / BDY / PAR steps.
    """
    sub = Path(aoi_folder) / (
        TRITON_FILES_SUBDIR if is_triton else LISFLOOD_FILES_SUBDIR
    )
    sub.mkdir(parents=True, exist_ok=True)
    return str(sub)


@dataclass(eq=False)   # identity-based equality + hash (safer for dict/set use)
class AOIFeatureInfo:
    """Information about a single AOI feature within a multi-feature shapefile."""

    source_file: str
    feature_index: int
    name: str
    area_km2: float
    centroid_lon: float
    centroid_lat: float
    state_name: Optional[str] = None
    state_abbr: Optional[str] = None
    river_name: Optional[str] = None
    folder_name: str = ""
    folder_path: Optional[str] = None
    # Lazily filled by core/aoi_info.py
    huc6_codes: Optional[list] = None       # list[str] when fetched
    huc8_codes: Optional[list] = None       # list[str] when fetched
    usgs_gages: Optional[list] = None       # list[dict] when fetched
    # Auto-picked working CRS (e.g. NAD83 / UTM zone 16N → 26916).
    # Filled by inspect_features() so each AOI's DEM, LULC, and Manning
    # rasters land in the same metric projection regardless of the
    # input shapefile's CRS.
    working_crs_epsg: Optional[int] = None
    working_crs_label: Optional[str] = None


# Columns to check for a usable feature name, in priority order
_NAME_COLUMNS = ["Name", "NAME", "name", "HUC_NAME", "GNIS_NAME", "FID"]


def inspect_features(aoi_path, selected_indices=None, log_fn=print) -> List[AOIFeatureInfo]:
    """Inspect features in an AOI shapefile and return metadata for each.

    Parameters
    ----------
    aoi_path : str or Path
        Path to the AOI shapefile.
    selected_indices : list of int, optional
        Feature indices to inspect. If None, all features are used.
    log_fn : callable
        Logging function.

    Returns
    -------
    list of AOIFeatureInfo
    """
    aoi_path = Path(aoi_path)
    gdf = gpd.read_file(aoi_path)

    if selected_indices is None:
        selected_indices = list(range(len(gdf)))

    # Reproject to EPSG:4326 for centroid coordinates
    gdf_4326 = gdf.to_crs("EPSG:4326")

    # Use a projected CRS for area calculation (UTM based on first feature centroid)
    first_centroid = gdf_4326.geometry.iloc[selected_indices[0]].centroid
    utm_zone = int((first_centroid.x + 180) / 6) + 1
    hemisphere = "north" if first_centroid.y >= 0 else "south"
    utm_epsg = 32600 + utm_zone if hemisphere == "north" else 32700 + utm_zone
    gdf_proj = gdf.to_crs(epsg=utm_epsg)

    features = []
    for idx in selected_indices:
        row = gdf.iloc[idx]
        geom_4326 = gdf_4326.geometry.iloc[idx]
        geom_proj = gdf_proj.geometry.iloc[idx]

        # Area in km2
        area_km2 = geom_proj.area / 1e6

        # Centroid in EPSG:4326
        centroid = geom_4326.centroid
        centroid_lon = centroid.x
        centroid_lat = centroid.y

        # Detect US state
        single_gdf = gpd.GeoDataFrame([row], geometry="geometry", crs=gdf.crs)
        state_info = detect_us_state(single_gdf)
        state_name = state_info.get("state_name")
        state_abbr = state_info.get("state_abbr")

        # Determine name from attributes
        name = None
        for col in _NAME_COLUMNS:
            if col in gdf.columns:
                val = row.get(col)
                if val is not None and str(val).strip():
                    name = str(val).strip()
                    break
        if not name:
            # Fallback: use shapefile stem (e.g. "case01.shp" → "case01").
            # If the shapefile has multiple features, append the index so
            # they don't collide.
            stem = aoi_path.stem
            if len(selected_indices) > 1:
                name = f"{stem}_{idx:03d}"
            else:
                name = stem

        folder_name = clean_name(name)

        # Auto-pick the working (metric) CRS for this feature from its
        # centroid.  NAD83 / UTM for North America, WGS84 / UTM
        # elsewhere.  Stored per-AOI so two features in different UTM
        # zones each get the correct local projection.
        wcrs_epsg = nad83_utm_epsg_from_lonlat(centroid_lon, centroid_lat)
        if wcrs_epsg is None:
            wcrs_epsg = wgs84_utm_epsg_from_lonlat(centroid_lon, centroid_lat)
        wcrs_label = working_crs_label(int(wcrs_epsg))

        info = AOIFeatureInfo(
            source_file=str(aoi_path),
            feature_index=idx,
            name=name,
            area_km2=round(area_km2, 4),
            centroid_lon=round(centroid_lon, 6),
            centroid_lat=round(centroid_lat, 6),
            state_name=state_name,
            state_abbr=state_abbr,
            river_name=None,
            folder_name=folder_name,
            folder_path=None,
            working_crs_epsg=int(wcrs_epsg),
            working_crs_label=wcrs_label,
        )
        features.append(info)

    log_fn(f"Inspected {len(features)} features from {aoi_path.name}")
    return features


def create_aoi_subfolders(project_dir, features: List[AOIFeatureInfo], log_fn=print) -> List[AOIFeatureInfo]:
    """Create subfolders for each AOI feature within the project directory.

    Parameters
    ----------
    project_dir : str or Path
        Root project directory.
    features : list of AOIFeatureInfo
        Feature metadata list (folder_name must be set).
    log_fn : callable
        Logging function.

    Returns
    -------
    list of AOIFeatureInfo
        Updated features with folder_path set.
    """
    project_dir = Path(project_dir)

    # Deduplicate folder names — if two AOIs would share the same folder_name,
    # disambiguate by prefixing with the source-file stem and/or adding a counter.
    seen = {}
    for feat in features:
        base = feat.folder_name or f"AOI_{feat.feature_index:03d}"
        candidate = base
        if candidate in seen:
            # Try source-file stem + base
            src_stem = clean_name(Path(feat.source_file).stem)
            candidate = f"{src_stem}_{base}"
        if candidate in seen:
            # Append a counter
            i = 2
            while f"{candidate}_{i}" in seen:
                i += 1
            candidate = f"{candidate}_{i}"
        seen[candidate] = True
        feat.folder_name = candidate

        folder = project_dir / candidate
        folder.mkdir(parents=True, exist_ok=True)
        feat.folder_path = str(folder)
        log_fn(f"Created subfolder: {folder.name}/")

    return features


def get_single_feature_gdf(aoi_path, feature_index) -> gpd.GeoDataFrame:
    """Return a single-row GeoDataFrame for a specific feature index.

    Parameters
    ----------
    aoi_path : str or Path
        Path to the AOI shapefile.
    feature_index : int
        Row index of the desired feature.

    Returns
    -------
    geopandas.GeoDataFrame
        Single-row GeoDataFrame preserving the source CRS.
    """
    gdf = gpd.read_file(aoi_path)
    return gdf.iloc[[feature_index]].reset_index(drop=True)
