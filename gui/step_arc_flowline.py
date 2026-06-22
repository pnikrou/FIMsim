"""ARC step 5 — Flowline.  (Placeholder.)"""
from gui.arc_step_placeholder import ArcStepPlaceholder


class StepArcFlowlineWidget(ArcStepPlaceholder):
    def __init__(self, log_fn=print, parent=None):
        super().__init__(
            "Step 5 — Flowline",
            "Download the NHD stream network for each AOI and write a flowline "
            "shapefile carrying the reach IDs and stream order that ARC needs "
            "to build a rating curve for every reach.",
            produces="stream-network shapefile (reach ID, order)",
            log_fn=log_fn, parent=parent,
        )
