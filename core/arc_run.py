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


def _copy_shapefile(src_shp, dst_shp, log_fn=print) -> str:
    """Copy a shapefile and all sidecars, renaming the stem to dst."""
    src_shp = Path(src_shp)
    dst_shp = Path(dst_shp)
    dst_shp.parent.mkdir(parents=True, exist_ok=True)
    copied = 0
    for sib in src_shp.parent.glob(src_shp.stem + ".*"):
        target = dst_shp.with_suffix(sib.suffix)
        shutil.copy2(sib, target)
        copied += 1
    if copied == 0:
        raise RuntimeError(f"No shapefile sidecars found for {src_shp}")
    log_fn(f"  ✓ Stream shapefile -> {dst_shp.name} ({copied} files)")
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

    # Stream shapefile
    if flowline_shp and Path(flowline_shp).exists():
        d = main / "StrmShp" / "StreamShapefile.shp"
        dest["strmshp"] = _copy_shapefile(flowline_shp, d, log_fn)
    else:
        missing.append("stream shapefile (step 5)")

    dest["missing"] = missing
    return dest
