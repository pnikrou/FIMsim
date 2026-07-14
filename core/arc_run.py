"""ARC-Curve2Flood — assemble the Main_Directory and run ARC + Curve2Flood.

ARC's ``Process_ARC_Geospatial_Data(Main_Directory, ...)`` reads a fixed folder
layout (names are hardcoded in ARC):

    <Main>/DEM/DEM.tif
    <Main>/LandCover/LandCover.tif
    <Main>/StrmShp/StreamShapefile.shp     (stream vector, carries the id_field)
    <Main>/LAND/AR_Manning_n_for_NLCD_MED.txt   (tab-separated Manning table)

It then creates STRM/, LAND/, VDT/, FloodMap/, ARC_InputFiles/ itself, writes
``ARC_InputFiles/ARC_Input_File.txt``, and ARC's rating curves land in
``VDT/CurveFile.csv``.  Curve2Flood consumes those to make ``FloodMap/*.tif``.

This module (1) copies each FIMsim-produced file into that layout, reformatting
the Manning table into ARC's tab-separated 3-column form, and (2) runs the
ARC -> Curve2Flood pipeline in-process.
"""
import csv
import shutil
from pathlib import Path


# NLCD land-cover code -> short description, matching ARC's baseline Manning
# file (arc.process_geospatial_data.Create_BaseLine_Manning_n_File).  ARC keys
# on the code (column 0) and the n value (column 2); the description (column 1)
# is required to be a non-empty, tab-free token.
NLCD_DESCRIPTIONS = {
    11: "Water",              12: "Perennial_Ice_Snow",
    21: "Dev_Open_Space",     22: "Dev_Low_Intesity",
    23: "Dev_Med_Intensity",  24: "Dev_High_Intensity",
    31: "Barren_Land",        41: "Decid_Forest",
    42: "Evergreen_Forest",   43: "Mixed_Forest",
    51: "Dwarf_Scrub",        52: "Shrub",
    71: "Grass_Herb",         72: "Sedge_Herb",
    73: "Lichens",            74: "Moss",
    81: "Pasture_Hay",        82: "Cultivated_Crops",
    90: "Woody_Wetlands",     95: "Emergent_Herb_Wet",
}


def _read_fimsim_manning(src_path: Path) -> list:
    """Read FIMsim's mannings_n.txt (CSV: LULC_Code,Manning_n) -> [(code, n)]."""
    rows = []
    with open(src_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if not row:
                continue
            if i == 0 and not row[0].strip().lstrip("-").isdigit():
                continue  # header line
            try:
                code = int(float(row[0]))
                n_val = float(row[1])
            except (ValueError, IndexError):
                continue
            rows.append((code, n_val))
    return rows


def write_arc_manning_file(src_manning_txt, out_path, log_fn=print) -> str:
    """Reformat FIMsim's Manning table into ARC's tab-separated 3-column form.

        LC_ID<TAB>Description<TAB>Manning_n
        11    Water            0.030
        ...

    ARC's read_manning_table splits on whitespace/tab and uses column 0 (code)
    and column 2 (n); column 1 must be a single tab-free token.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = _read_fimsim_manning(Path(src_manning_txt))
    if not rows:
        raise RuntimeError(
            f"No Manning classes read from {src_manning_txt}; run the "
            "Land Cover step first.")
    lines = ["LC_ID\tDescription\tManning_n"]
    for code, n_val in sorted(rows, key=lambda kv: kv[0]):
        desc = NLCD_DESCRIPTIONS.get(code, f"Class_{code}")
        lines.append(f"{code}\t{desc}\t{n_val:.3f}")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log_fn(f"  ✓ Manning table -> {out_path.name} ({len(rows)} classes, ARC tab format)")
    return str(out_path)


def _write_stream_shapefile(src_shp, dst_shp, log_fn=print) -> str:
    """Write the ARC/Curve2Flood stream shapefile with normalized columns.

    Curve2Flood hardcodes the reach-id column name ``LINKNO`` (TDX-Hydro
    convention) and optionally uses ``StrmOrder`` (top-width weighting) and a
    downstream-link column.  NHD flowlines carry ``comid``/``streamorde``/
    ``tocomid`` instead — so we load the vector and add the expected columns.
    """
    import geopandas as gpd

    src_shp = Path(src_shp)
    dst_shp = Path(dst_shp)
    dst_shp.parent.mkdir(parents=True, exist_ok=True)

    gdf = gpd.read_file(src_shp)
    lower = {c.lower(): c for c in gdf.columns}

    # LINKNO = the reach id (COMID). Required by Curve2Flood.
    id_col = next((lower[k] for k in
                   ("linkno", "comid", "featureid", "feature_id", "nhdplusid")
                   if k in lower), None)
    if id_col is None:
        raise RuntimeError(
            f"No reach-id column (COMID/LINKNO) in {src_shp.name} — "
            f"columns: {list(gdf.columns)}")
    # ARC's Process_ARC_Geospatial_Data is called with id_field="COMID" —
    # rename the source id column to exactly that (avoids the shapefile
    # driver laundering a case-insensitive duplicate to COMID_1), then add
    # LINKNO for Curve2Flood.
    if id_col != "COMID":
        gdf = gdf.rename(columns={id_col: "COMID"})
    gdf["COMID"]  = gdf["COMID"].astype(float).astype(int)
    gdf["LINKNO"] = gdf["COMID"]

    # StrmOrder = stream order (optional, improves top-width weighting).
    order_col = next((lower[k] for k in
                      ("strmorder", "streamorde", "stream_order", "order")
                      if k in lower), None)
    if order_col is not None:
        gdf["StrmOrder"] = (
            gdf[order_col].fillna(1).astype(float).astype(int).clip(lower=1))
    else:
        gdf["StrmOrder"] = 1

    # DSLINKNO = downstream reach id (optional).
    ds_col = next((lower[k] for k in ("dslinkno", "tocomid", "to_comid", "ds_id")
                   if k in lower), None)
    has_ds = ds_col is not None
    if has_ds:
        gdf["DSLINKNO"] = (
            gdf[ds_col].fillna(-1).astype(float).astype(int))

    # Drop any stale files, then write.
    for sib in dst_shp.parent.glob(dst_shp.stem + ".*"):
        try:
            sib.unlink()
        except Exception:
            pass
    gdf.to_file(dst_shp, driver="ESRI Shapefile")
    log_fn(f"  ✓ Stream shapefile -> {dst_shp.name} "
           f"({len(gdf)} reaches; LINKNO, StrmOrder"
           f"{', DSLINKNO' if has_ds else ''})")
    return str(dst_shp)


def assemble_arc_main_directory(arc_dir, *, dem_tif, lulc_tif, mannings_txt,
                                flowline_shp, log_fn=print) -> dict:
    """Populate <arc_dir> with the layout Process_ARC_Geospatial_Data expects.

    Returns a dict of the destination paths plus a ``missing`` list of any
    required inputs that were absent (so the caller can warn per-AOI).
    """
    main = Path(arc_dir)
    main.mkdir(parents=True, exist_ok=True)

    missing = []
    dest = {"main_directory": str(main)}

    # DEM
    if dem_tif and Path(dem_tif).exists():
        d = main / "DEM" / "DEM.tif"
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dem_tif, d)
        dest["dem"] = str(d)
        log_fn(f"  ✓ DEM -> DEM/DEM.tif")
    else:
        missing.append("DEM (step 3)")

    # LandCover
    if lulc_tif and Path(lulc_tif).exists():
        d = main / "LandCover" / "LandCover.tif"
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(lulc_tif, d)
        dest["landcover"] = str(d)
        log_fn(f"  ✓ Land cover -> LandCover/LandCover.tif")
    else:
        missing.append("LandCover raster (step 4 — use Varying mode)")

    # Manning table (reformatted to ARC tab format)
    if mannings_txt and Path(mannings_txt).exists():
        d = main / "LAND" / "AR_Manning_n_for_NLCD_MED.txt"
        dest["manning"] = write_arc_manning_file(mannings_txt, d, log_fn)
    else:
        missing.append("Manning table (step 4)")

    # Stream shapefile (normalized: LINKNO / StrmOrder / DSLINKNO)
    if flowline_shp and Path(flowline_shp).exists():
        d = main / "StrmShp" / "StreamShapefile.shp"
        dest["strmshp"] = _write_stream_shapefile(flowline_shp, d, log_fn)
    else:
        missing.append("stream shapefile (step 5)")

    dest["missing"] = missing
    return dest


# ── Curve2Flood input file + the flow to simulate ─────────────────────────────

def write_comid_q_file(flow_csv, out_path, log_fn=print) -> str:
    """Write the COMID_Flow_File Curve2Flood simulates (COMID, Q = max flow).

    Curve2Flood reads column 0 as the reach id and column 1+ as flow event(s);
    we map each reach's peak (the 'max' column of the ARC flow file) to Q.
    """
    import pandas as pd
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(flow_csv)
    q = df[["COMID", "max"]].rename(columns={"max": "Q"})
    q.to_csv(out_path, index=False)
    log_fn(f"  ✓ Flow-to-map -> {out_path.name} ({len(q)} reaches)")
    return str(out_path)


def write_curve2flood_input(main_dir, *, mapper="Curve2Flood-Kernel Weighted",
                            set_depth=0.0, make_gpkg=True, out_vel=False,
                            comid_q_file=None, out_path=None, log_fn=print) -> str:
    """Write the Curve2Flood key-value input .txt referencing ARC's outputs."""
    main = Path(main_dir)
    if out_path is None:
        out_path = main / "ARC_InputFiles" / "Curve2Flood_Input_File.txt"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    flood_tif = main / "FloodMap" / "Curve2Flood_FIM.tif"
    (main / "FloodMap").mkdir(parents=True, exist_ok=True)

    kv = [
        ("DEM_File",          main / "DEM" / "DEM.tif"),
        ("Stream_File",       main / "STRM" / "STRM_Raster.tif"),      # ARC-made
        ("StrmShp_File",      main / "StrmShp" / "StreamShapefile.shp"),
        ("LU_Raster_SameRes", main / "LAND" / "LAND_Raster.tif"),      # ARC-made
        ("LU_Manning_n",      main / "LAND" / "AR_Manning_n_for_NLCD_MED.txt"),
        ("Print_VDT_Database", main / "VDT" / "VDT_Database.txt"),     # ARC output
        ("Print_Curve_File",  main / "VDT" / "CurveFile.csv"),         # ARC output
        ("COMID_Flow_File",   comid_q_file or (main / "FlowData" / "comid_q.csv")),
        ("OutFLD",            flood_tif),
        ("mapper",            mapper),
        ("Set_Depth",         set_depth),
        ("Make_Output_GPKG",  "True" if make_gpkg else "False"),
        # Column names in StreamShapefile.shp (normalized by
        # _write_stream_shapefile; Curve2Flood ignores missing ones).
        ("StrmOrder_Field",       "StrmOrder"),
        ("Downstream_Link_Field", "DSLINKNO"),
    ]
    if out_vel:
        kv.append(("OutVEL", main / "FloodMap" / "Curve2Flood_VEL.tif"))

    lines = [f"{k}\t{v}" for k, v in kv]
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log_fn(f"  ✓ Curve2Flood input -> {out_path.name}")
    return str(out_path)


# ── Full per-AOI run: ARC geospatial -> ARC rating curves -> Curve2Flood ───────

def run_arc_curve2flood(arc_dir, *, dem_tif, lulc_tif, mannings_txt, flowline_shp,
                        flow_csv, mapper="Curve2Flood-Kernel Weighted",
                        set_depth=0.0, make_gpkg=True, bathy_use_banks=False,
                        use_land_cover_to_find_banks=True, log_fn=print) -> dict:
    """Assemble the Main_Directory and run ARC + Curve2Flood for one AOI.

    Returns ``{"flood_map", "curve_file", "main_directory"}`` on success.
    Raises with a clear message if a required input or ARC output is missing.
    """
    from arc import Process_ARC_Geospatial_Data, Arc
    from curve2flood import Curve2Flood_MainFunction

    # 1) lay out the inputs ARC expects
    dest = assemble_arc_main_directory(
        arc_dir, dem_tif=dem_tif, lulc_tif=lulc_tif, mannings_txt=mannings_txt,
        flowline_shp=flowline_shp, log_fn=log_fn)
    if dest["missing"]:
        raise RuntimeError("Missing ARC inputs: " + "; ".join(dest["missing"]))
    if not flow_csv or not Path(flow_csv).exists():
        raise RuntimeError("Missing flow file (step 6) — run the Streamflow step.")

    main = Path(dest["main_directory"])

    # 2) ARC geospatial prep — rasterizes streams, aligns land, writes the ARC
    #    input file (id/max/base column names come from our flow.csv).
    log_fn("Running ARC geospatial preprocessing …")
    Process_ARC_Geospatial_Data(
        str(main), "COMID", "max", "base", str(flow_csv),
        bathy_use_banks, use_land_cover_to_find_banks)

    arc_input = main / "ARC_InputFiles" / "ARC_Input_File.txt"
    if not arc_input.exists():
        raise RuntimeError(
            f"ARC did not write its input file at {arc_input} — check the log above.")

    # 3) ARC — build the rating curves (VDT database + CurveFile.csv)
    log_fn("Running ARC (building rating curves) …")
    Arc(mifn=str(arc_input)).run()
    curve_file = main / "VDT" / "CurveFile.csv"
    if not curve_file.exists():
        raise RuntimeError(
            f"ARC finished but no CurveFile.csv at {curve_file} — cannot map flooding.")

    # 4) Curve2Flood — turn curves + the flow-to-map into a flood raster
    comid_q = write_comid_q_file(flow_csv, main / "FlowData" / "comid_q.csv", log_fn)
    c2f_input = write_curve2flood_input(
        main, mapper=mapper, set_depth=set_depth, make_gpkg=make_gpkg,
        comid_q_file=comid_q, log_fn=log_fn)
    log_fn("Running Curve2Flood (mapping flood inundation) …")
    Curve2Flood_MainFunction(input_file=str(c2f_input))

    flood_map = main / "FloodMap" / "Curve2Flood_FIM.tif"
    result = {
        "main_directory": str(main),
        "curve_file":     str(curve_file),
        "flood_map":      str(flood_map) if flood_map.exists() else None,
    }
    if flood_map.exists():
        log_fn(f"✓ Flood map -> {flood_map}")
    else:
        log_fn("⚠ Curve2Flood finished but no flood raster was found — check the log.")
    return result
