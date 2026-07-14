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


def prepare_arc_flowline(ctx_path, ctx: dict, log_fn=print, **_opts) -> dict:
    """Download + save the AOI's NHD flowlines as a shapefile.

    Reads ``aoi_path`` / ``aoi_feature_index`` / ``arc_dir`` from ctx, writes
    ``arc_flowline_path`` (+ ``arc_flowline_count``) back into ctx, and returns
    the updated ctx.  Raises on failure so the caller can report it per AOI.
    """
    aoi_path = ctx.get("aoi_path")
    if not aoi_path:
        raise RuntimeError("No AOI selected — complete the AOI step first.")
    fidx = int(ctx.get("aoi_feature_index", 0) or 0)

    arc_dir = ctx.get("arc_dir") or str(
        Path(ctx.get("project_dir", ".")) / "arc-files")
    Path(arc_dir).mkdir(parents=True, exist_ok=True)

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

    ctx["arc_flowline_path"]  = str(saved)
    ctx["arc_flowline_count"] = int(len(clipped))
    log_fn(f"✓ Saved {len(clipped)} flowline reach(es) → {Path(saved).name}")

    if ctx_path:
        try:
            save_context(ctx_path, ctx)
        except Exception:
            pass
    return ctx
