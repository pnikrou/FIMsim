"""ARC step 7 — Config.  (Placeholder — NenCarta JSON writer coming next.)"""
from gui.arc_step_placeholder import ArcStepPlaceholder


class StepArcConfigWidget(ArcStepPlaceholder):
    def __init__(self, log_fn=print, parent=None):
        super().__init__(
            "Step 7 — Config",
            "Assemble all prepared inputs into a ready-to-run NenCarta JSON "
            "config (flowline, dem_dir, mannings table, streamflow source, "
            "mapper = Curve2Flood, output_dir).  Run it with: "
            "flood-mapping json <AOI>.json --serial",
            produces="<AOI>.json (NenCarta run config)",
            log_fn=log_fn, parent=parent,
        )
