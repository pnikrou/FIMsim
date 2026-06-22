"""ARC step 6 — Streamflow.  (Placeholder.)"""
from gui.arc_step_placeholder import ArcStepPlaceholder


class StepArcStreamflowWidget(ArcStepPlaceholder):
    def __init__(self, log_fn=print, parent=None):
        super().__init__(
            "Step 6 — Streamflow",
            "Choose the streamflow source — NWM (US) or GEOGLOWS (global) — and "
            "fetch the peak and base flow per reach, or supply your own flow "
            "file.  These drive ARC's rating curves and the Curve2Flood map.",
            produces="streamflow source settings / user flow file",
            log_fn=log_fn, parent=parent,
        )
