"""ARC step 4 — Land Cover & Manning.  (Placeholder.)"""
from gui.arc_step_placeholder import ArcStepPlaceholder


class StepArcLandCoverWidget(ArcStepPlaceholder):
    def __init__(self, log_fn=print, parent=None):
        super().__init__(
            "Step 4 — Land Cover & Manning",
            "Fetch a land-cover raster (NLCD or Sentinel-2) for each AOI and "
            "write a Manning's-n lookup table (mannings_n.txt).  ARC reads the "
            "land-cover raster + this table to assign roughness per cell.",
            produces="land-cover raster + mannings_n.txt",
            log_fn=log_fn, parent=parent,
        )
