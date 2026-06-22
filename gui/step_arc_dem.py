"""ARC step 3 — DEM.  (Placeholder — real 3DEP GeoTIFF prep coming next.)"""
from gui.arc_step_placeholder import ArcStepPlaceholder


class StepArcDEMWidget(ArcStepPlaceholder):
    def __init__(self, log_fn=print, parent=None):
        super().__init__(
            "Step 3 — DEM",
            "Download USGS 3DEP terrain for each AOI, clip to the boundary, "
            "reproject to the working CRS, and write GeoTIFF DEM tile(s) into "
            "the AOI's dem/ folder — this becomes NenCarta's dem_dir.",
            produces="dem/ (GeoTIFF DEM tiles)",
            log_fn=log_fn, parent=parent,
        )
