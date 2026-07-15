"""ARC step 5 — Flowline.

Download the NHD stream network for an AOI and save it as a shapefile that ARC
uses as its ``flowline`` input (the stream reaches).  Saved to:

    <AOI>/arc-files/flowline.shp

The whole clipped NHD network is saved (not just the main river) because ARC
builds a rating curve for every reach; NenCarta's ``process_stream_network``
option then derives the connectivity it needs.
"""
from pathlib import Path

from core.context import save_context
from core.aoi_info import lookup_nhd_flowlines_clipped


_ID_CANDIDATES = ("comid", "linkno", "featureid", "feature_id", "nhdplusid")


def _load_user_flowline(user_path, log_fn=print):
    """Read a user-provided stream vector and verify it carries a reach id."""
    import geopandas as gpd

    if not user_path or not Path(user_path).exists():
        raise RuntimeError(
            "No user flowline file selected — browse to a stream shapefile / "
            "GeoPackage first.")
    log_fn(f"Reading user flowline: {Path(user_path).name}")
    gdf = gpd.read_file(user_path)
    if gdf.empty:
        raise RuntimeError(f"{Path(user_path).name} contains no features.")
    lower = {c.lower() for c in gdf.columns}
    if not any(k in lower for k in _ID_CANDIDATES):
        raise RuntimeError(
            f"{Path(user_path).name} has no reach-id column "
            "(COMID / LINKNO / feature_id) — ARC needs one to link "
            f"streamflow to reaches. Columns: {list(gdf.columns)}")
    return gdf


def prepare_arc_flowline(ctx_path, ctx: dict, source: str = "nhd",
                         user_path=None, log_fn=print, **_opts) -> dict:
    """Save the AOI's stream network as ARC's flowline shapefile.

    ``source``: "nhd" downloads the NHD network clipped to the AOI;
    "user" imports the user's own stream vector (``user_path``).
    Writes ``arc_flowline_path`` (+ ``arc_flowline_count``) back into ctx and
    returns the updated ctx.  Raises on failure so the caller can report it
    per AOI.
    """
    aoi_path = ctx.get("aoi_path")
    if not aoi_path:
        raise RuntimeError("No AOI selected — complete the AOI step first.")
    fidx = int(ctx.get("aoi_feature_index", 0) or 0)

    arc_dir = ctx.get("arc_dir") or str(
        Path(ctx.get("project_dir", ".")) / "arc-files")
    Path(arc_dir).mkdir(parents=True, exist_ok=True)

    if source == "user":
        clipped = _load_user_flowline(user_path, log_fn)
    else:
        log_fn("Downloading NHD flowlines for the AOI …")
        clipped, _main = lookup_nhd_flowlines_clipped(aoi_path, fidx, log_fn=log_fn)
    if clipped is None or clipped.empty:
        raise RuntimeError(
            "No NHD flowlines were found for this AOI (check the AOI extent / "
            "internet connection).")

    # ARC keys everything on the stream id (COMID == NWM feature_id).  Fail
    # loudly here rather than at ARC run time if the column is missing.
    comid_col = next(
        (c for c in clipped.columns
         if c.lower() in ("comid", "featureid", "feature_id", "nhdplusid")),
        None)
    if comid_col is None:
        raise RuntimeError(
            "The downloaded NHD flowlines have no COMID / feature-id column — "
            "ARC cannot link streamflow to reaches. Columns: "
            f"{list(clipped.columns)}")
    n_ids = int(clipped[comid_col].dropna().nunique())
    log_fn(f"  Stream id column: '{comid_col}' ({n_ids} unique reach ids)")

    out = Path(arc_dir) / "flowline.shp"
    # Remove any stale shapefile sidecars before writing.
    for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
        p = out.with_suffix(ext)
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass
    from core.flowline_mode import _save_shapefile
    saved = _save_shapefile(clipped, out, log_fn)

    ctx["arc_flowline_path"]   = str(saved)
    ctx["arc_flowline_count"]  = int(len(clipped))
    ctx["arc_flowline_source"] = source
    log_fn(f"✓ Saved {len(clipped)} flowline reach(es) → {Path(saved).name}")

    if ctx_path:
        try:
            save_context(ctx_path, ctx)
        except Exception:
            pass
    return ctx
