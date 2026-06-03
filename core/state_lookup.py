"""Detect which US state an AOI GeoDataFrame is located in."""

from pathlib import Path

import geopandas as gpd

_states_gdf = None

_GEOJSON_PATH = Path(__file__).parent.parent / "data" / "us_states.geojson"


def get_states_gdf():
    """Load and cache the US states GeoDataFrame."""
    global _states_gdf
    if _states_gdf is None:
        _states_gdf = gpd.read_file(_GEOJSON_PATH)
    return _states_gdf


def _lookup_single_point(point, states_gdf):
    """Return state info dict for a single shapely Point in EPSG:4326."""
    from shapely.geometry import Point as ShapelyPoint

    point_gdf = gpd.GeoDataFrame(
        [{"geometry": point}], crs="EPSG:4326"
    )

    # Spatial join — point within state polygon
    joined = gpd.sjoin(point_gdf, states_gdf, how="left", predicate="within")

    if not joined.empty and joined.iloc[0]["state_name"] is not None and str(joined.iloc[0]["state_name"]) != "nan":
        row = joined.iloc[0]
        return {
            "state_name": row["state_name"],
            "state_abbr": row["state_abbr"],
            "state_fips": row["state_fips"],
        }

    # Fallback: nearest state by distance
    states_projected = states_gdf.to_crs("EPSG:4326")
    distances = states_projected.geometry.distance(point)
    nearest_idx = distances.idxmin()
    nearest = states_projected.loc[nearest_idx]
    return {
        "state_name": nearest["state_name"],
        "state_abbr": nearest["state_abbr"],
        "state_fips": nearest["state_fips"],
    }


def detect_us_state(aoi_gdf):
    """Detect which US state the AOI centroid falls in.

    Parameters
    ----------
    aoi_gdf : geopandas.GeoDataFrame
        AOI geometry in any CRS.

    Returns
    -------
    dict
        {"state_name": str, "state_abbr": str, "state_fips": str}
        or all None values if outside the US (should not happen with fallback).
    """
    states_gdf = get_states_gdf().to_crs("EPSG:4326")

    # Reproject AOI to 4326 and get centroid of the union
    aoi_4326 = aoi_gdf.to_crs("EPSG:4326")
    centroid = aoi_4326.union_all().centroid

    return _lookup_single_point(centroid, states_gdf)


def detect_us_states_for_features(aoi_gdf):
    """Detect which US state each feature in the GeoDataFrame falls in.

    Parameters
    ----------
    aoi_gdf : geopandas.GeoDataFrame
        AOI geometries in any CRS.

    Returns
    -------
    list of dict
        One dict per row: {"state_name": str, "state_abbr": str, "state_fips": str}
    """
    states_gdf = get_states_gdf().to_crs("EPSG:4326")

    aoi_4326 = aoi_gdf.to_crs("EPSG:4326")
    centroids = aoi_4326.geometry.centroid

    results = []
    for centroid in centroids:
        results.append(_lookup_single_point(centroid, states_gdf))

    return results
