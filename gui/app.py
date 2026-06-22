"""Main application window — home selector + LISFLOOD-FP and TRITON workflows."""
import json

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget,
    QTabWidget, QTextEdit, QSplitter, QLabel, QPushButton,
    QFileDialog, QMessageBox, QScrollArea,
)
from PyQt6.QtCore import Qt, QDateTime
from PyQt6.QtGui import QFont, QTextCursor

# ── Model selector ───────────────────────────────────────────────────────────
from gui.model_selector import ModelSelectorWidget

# ── Shared step widgets ──────────────────────────────────────────────────────
from gui.step_project import StepProjectWidget
from gui.step_aoi     import StepAOIWidget
from gui.step_dem     import StepDEMWidget

# ── LISFLOOD-FP step widgets ─────────────────────────────────────────────────
from gui.step_manning import StepManningWidget
from gui.step_bci     import StepBCIWidget
from gui.step_bdy     import StepBDYWidget
from gui.step_par     import StepPARWidget

# ── TRITON step widgets (standalone — no shared workflow code) ────────────────
from gui.step_triton_project import StepTritonProjectWidget
from gui.step_triton_dem     import StepTritonDEMWidget

# ── ARC-Curve2Flood step widgets (standalone) ────────────────────────────────
from gui.step_arc_project    import StepArcProjectWidget
from gui.step_arc_aoi        import StepArcAOIWidget
from gui.step_arc_dem        import StepArcDEMWidget
from gui.step_arc_landcover  import StepArcLandCoverWidget
from gui.step_arc_flowline   import StepArcFlowlineWidget
from gui.step_arc_streamflow import StepArcStreamflowWidget
from gui.step_arc_config     import StepArcConfigWidget
from gui.step_triton_manning import StepTritonManningWidget
from gui.step_triton_bc      import StepTritonBCWidget
from gui.step_triton_hydro   import StepTritonHydroWidget
from gui.step_triton_cfg     import StepTritonCfgWidget

# ── Standalone mode widgets ──────────────────────────────────────────────────
from gui.mode_dem            import ModeDEMWidget
from gui.mode_lulc_manning   import ModeLULCManningWidget
from gui.mode_flowline       import ModeFlowlineWidget
from gui.mode_streamflow     import ModeStreamflowWidget


# ── Page indices inside the QStackedWidget ───────────────────────────────────
_PAGE_SELECTOR     = 0
_PAGE_DEM          = 1
_PAGE_LULC_MANNING = 2
_PAGE_FLOWLINE     = 3
_PAGE_LISFLOOD     = 4
_PAGE_TRITON       = 5
_PAGE_STREAMFLOW   = 6
_PAGE_ARC          = 7


class MainWindow(QMainWindow):
    APP_TITLE = "FIMsim"
    VERSION   = "1.0"

    def __init__(self):
        super().__init__()
        # Per-model state
        self._ctx_path = {"lisflood": None, "triton": None, "arc_curve2flood": None}
        self._ctx      = {"lisflood": {},    "triton": {},   "arc_curve2flood": {}}
        self._active_model = None          # "lisflood" | "triton" | "arc_curve2flood"
        self._setup_ui()

    # ── UI construction ──────────────────────────────────────────────────────

    def _setup_ui(self):
        self.setWindowTitle(
            "FIMsim  |  Flood Inundation Model Simulation Tool  v1.0"
        )
        self.resize(1000, 860)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # ── Splitter: top = stacked pages, bottom = log ──────────────────────
        splitter = QSplitter(Qt.Orientation.Vertical)

        self._stack = QStackedWidget()

        self._log_panel = QTextEdit()
        self._log_panel.setReadOnly(True)
        self._log_panel.setFont(QFont("Courier", 10))
        self._log_panel.setMinimumHeight(120)
        self._log_panel.setMaximumHeight(220)
        self._log_panel.setStyleSheet("background:#1a1a2e; color:#e0e0e0;")
        self._log_panel.setPlaceholderText("Log output will appear here…")

        log_fn = self._append_log

        # ── Page 0: Home selector ─────────────────────────────────────────────
        self._selector = ModelSelectorWidget()
        self._selector.mode_selected.connect(self._on_mode_selected)
        self._stack.addWidget(self._selector)               # index 0

        # ── Page 1: DEM standalone mode ──────────────────────────────────────
        self._mode_dem = ModeDEMWidget(log_fn)
        self._mode_dem.mode_finished.connect(self._go_to_selector)
        self._stack.addWidget(self._mode_dem)               # index 1

        # ── Page 2: LULC + Manning standalone mode ───────────────────────────
        self._mode_lulc = ModeLULCManningWidget(log_fn)
        self._mode_lulc.mode_finished.connect(self._go_to_selector)
        self._stack.addWidget(self._mode_lulc)              # index 2

        # ── Page 3: Flowline standalone mode ─────────────────────────────────
        self._mode_flowline = ModeFlowlineWidget(log_fn)
        self._mode_flowline.mode_finished.connect(self._go_to_selector)
        self._stack.addWidget(self._mode_flowline)          # index 3

        # ── Page 4: LISFLOOD-FP tabs ──────────────────────────────────────────
        self._lfp_tabs, self._lfp_steps = self._build_lisflood_tabs(log_fn)
        self._stack.addWidget(self._lfp_tabs)               # index 4

        # ── Page 5: TRITON tabs ───────────────────────────────────────────────
        self._triton_tabs, self._triton_steps = self._build_triton_tabs(log_fn)
        self._stack.addWidget(self._triton_tabs)            # index 5

        # ── Page 6: Streamflow Data standalone mode ───────────────────────────
        self._mode_streamflow = ModeStreamflowWidget(log_fn)
        self._mode_streamflow.mode_finished.connect(self._go_to_selector)
        self._stack.addWidget(self._mode_streamflow)        # index 6

        # ── Page 7: ARC-Curve2Flood tabs ─────────────────────────────────────
        self._arc_tabs, self._arc_steps = self._build_arc_tabs(log_fn)
        self._stack.addWidget(self._arc_tabs)               # index 7

        splitter.addWidget(self._stack)
        splitter.addWidget(self._log_panel)
        splitter.setSizes([600, 180])
        root.addWidget(splitter)

        # ── Bottom navigation bar ────────────────────────────────────────────
        bottom = QHBoxLayout()
        bottom.setSpacing(8)

        self._back_btn = QPushButton("◀  Back to main page")
        self._back_btn.setFixedWidth(180)
        self._back_btn.clicked.connect(self._go_to_selector)
        self._back_btn.setVisible(False)

        self._prev_btn = QPushButton("◀  Previous step")
        self._prev_btn.setFixedWidth(140)
        self._prev_btn.clicked.connect(self._go_prev)

        self._next_btn = QPushButton("Next step  ▶")
        self._next_btn.setFixedWidth(140)
        self._next_btn.clicked.connect(self._go_next)

        clear_btn = QPushButton("Clear log")
        clear_btn.setFixedWidth(80)
        clear_btn.clicked.connect(self._log_panel.clear)

        bottom.addWidget(self._back_btn)
        bottom.addWidget(self._prev_btn)
        bottom.addWidget(self._next_btn)
        bottom.addWidget(clear_btn)
        bottom.addStretch()
        root.addLayout(bottom)

        # Connect tab-change for each workflow (after buttons exist)
        self._lfp_tabs.currentChanged.connect(self._on_tab_changed)
        self._triton_tabs.currentChanged.connect(self._on_tab_changed)
        self._arc_tabs.currentChanged.connect(self._on_tab_changed)

        self._stack.setCurrentIndex(_PAGE_SELECTOR)
        self._update_nav()
        self._append_log(
            "Welcome to FIMsim  |  Flood Inundation Model Simulation Tool  v1.0"
        )
        self._append_log("Select a category on the home screen to begin.")

    # ── Build LISFLOOD-FP tab widget ─────────────────────────────────────────

    def _build_lisflood_tabs(self, log_fn):
        from gui.step_multi_aoi import StepMultiAOIWidget   # local to avoid circular

        tabs = QTabWidget()
        tabs.setTabPosition(QTabWidget.TabPosition.North)

        # Project step uses model="generic" — NO lisflood-files folder is
        # created at this step.  Each AOI gets its own subfolder named after
        # its feature in the AOI step instead.
        proj    = StepProjectWidget(log_fn, model="generic")
        aoi     = StepMultiAOIWidget(log_fn, model="lisflood")
        dem     = StepDEMWidget(log_fn)
        manning = StepManningWidget(log_fn)
        bci     = StepBCIWidget(log_fn)
        bdy     = StepBDYWidget(log_fn)
        par     = StepPARWidget(log_fn)

        step_list = [
            ("1. Project",   proj),
            ("2. AOI",       aoi),
            ("3. DEM",       dem),
            ("4. Manning",   manning),
            ("5. BCI",       bci),
            ("6. BDY",       bdy),
            ("7. PAR File",  par),
        ]
        widgets = [w for _, w in step_list]

        for label, w in step_list:
            sa = QScrollArea(); sa.setWidgetResizable(True); sa.setWidget(w)
            tabs.addTab(sa, label)
        # NOTE: tabs are NOT disabled — the user can navigate to any step at
        # any time.  If a step's run requires data from an earlier step that
        # hasn't been completed, the run itself will raise a clear error.

        # Connect signals — when a step completes, we update the per-model
        # context dict and propagate it to the next step.  We no longer
        # toggle tab enabled-state.
        proj.step_completed.connect(
            self._make_project_done_slot("lisflood", tabs, widgets))
        for i in range(1, len(widgets)):
            nxt = i + 1 if i + 1 < len(widgets) else None
            widgets[i].step_completed.connect(
                self._make_step_done_slot("lisflood", tabs, widgets, i, nxt))

        return tabs, widgets

    # ── Build TRITON tab widget ──────────────────────────────────────────────

    def _build_triton_tabs(self, log_fn):
        from gui.step_triton_aoi import StepTritonAOIWidget   # local to avoid circular

        tabs = QTabWidget()
        tabs.setTabPosition(QTabWidget.TabPosition.North)

        # Project step uses model="generic" — NO triton-files folder created
        # at this step.  Each AOI gets its own subfolder in the AOI step.
        # These are TRITON-only step widgets — they share no workflow code
        # with the LISFLOOD-FP tabs.
        proj    = StepTritonProjectWidget(log_fn, model="generic")
        aoi     = StepTritonAOIWidget(log_fn, model="triton")
        dem     = StepTritonDEMWidget(log_fn)
        manning = StepTritonManningWidget(log_fn)
        bc      = StepTritonBCWidget(log_fn)
        hydro   = StepTritonHydroWidget(log_fn)
        cfg     = StepTritonCfgWidget(log_fn)

        step_list = [
            ("1. Project",   proj),
            ("2. AOI",       aoi),
            ("3. DEM",       dem),
            ("4. Friction",  manning),
            ("5. BC",        bc),
            ("6. Hydrograph", hydro),
            ("7. Config",    cfg),
        ]
        widgets = [w for _, w in step_list]

        for label, w in step_list:
            sa = QScrollArea(); sa.setWidgetResizable(True); sa.setWidget(w)
            tabs.addTab(sa, label)
        # All tabs always enabled — user can navigate to any step.

        # Connect signals
        proj.step_completed.connect(
            self._make_project_done_slot("triton", tabs, widgets))
        for i in range(1, len(widgets)):
            nxt = i + 1 if i + 1 < len(widgets) else None
            widgets[i].step_completed.connect(
                self._make_step_done_slot("triton", tabs, widgets, i, nxt))

        return tabs, widgets

    # ── Build ARC-Curve2Flood tab widget ─────────────────────────────────────

    def _build_arc_tabs(self, log_fn):
        tabs = QTabWidget()
        tabs.setTabPosition(QTabWidget.TabPosition.North)

        # ARC-Curve2Flood prepares a NenCarta input package.  These are
        # ARC-only step widgets — they share no workflow code with the
        # LISFLOOD-FP or TRITON tabs.
        proj       = StepArcProjectWidget(log_fn, model="generic")
        aoi        = StepArcAOIWidget(log_fn, model="arc_curve2flood")
        dem        = StepArcDEMWidget(log_fn)
        landcover  = StepArcLandCoverWidget(log_fn)
        flowline   = StepArcFlowlineWidget(log_fn)
        streamflow = StepArcStreamflowWidget(log_fn)
        config     = StepArcConfigWidget(log_fn)

        step_list = [
            ("1. Project",          proj),
            ("2. AOI",              aoi),
            ("3. DEM",              dem),
            ("4. Land Cover",       landcover),
            ("5. Flowline",         flowline),
            ("6. Streamflow",       streamflow),
            ("7. Config",           config),
        ]
        widgets = [w for _, w in step_list]

        for label, w in step_list:
            sa = QScrollArea(); sa.setWidgetResizable(True); sa.setWidget(w)
            tabs.addTab(sa, label)

        proj.step_completed.connect(
            self._make_project_done_slot("arc_curve2flood", tabs, widgets))
        for i in range(1, len(widgets)):
            nxt = i + 1 if i + 1 < len(widgets) else None
            widgets[i].step_completed.connect(
                self._make_step_done_slot("arc_curve2flood", tabs, widgets, i, nxt))

        return tabs, widgets

    # ── Mode selection ───────────────────────────────────────────────────────

    _MODE_TO_PAGE = {
        "dem":          _PAGE_DEM,
        "lulc_manning": _PAGE_LULC_MANNING,
        "flowline":     _PAGE_FLOWLINE,
        "lisflood":     _PAGE_LISFLOOD,
        "triton":       _PAGE_TRITON,
        "streamflow":   _PAGE_STREAMFLOW,
        "arc_curve2flood": _PAGE_ARC,
    }
    _MODE_LABELS = {
        "dem":          ("DEM",
                         "Prepare DEM(s) for one or more AOIs in your chosen format and cell size"),
        "lulc_manning": ("LULC & Manning's n",
                         "Prepare LULC and Manning's n for one or more AOIs"),
        "flowline":     ("Flowline",
                         "NHD flowlines, USGS gages, and feature IDs per AOI"),
        "lisflood":     ("LISFLOOD-FP",
                         "Prepare all input files for a LISFLOOD-FP flood simulation"),
        "triton":       ("TRITON",
                         "Prepare all input files for a TRITON flood simulation"),
        "streamflow":   ("Streamflow Data",
                         "Download NWM or USGS discharge time series by feature ID or gage number"),
        "arc_curve2flood": ("ARC-Curve2Flood",
                         "Prepare a NenCarta input package (rapid flood mapping)"),
    }

    def _on_mode_selected(self, mode: str):
        if mode not in self._MODE_TO_PAGE:
            return
        self._active_model = mode
        # Always start with a clean slate when (re-)entering a mode, so the
        # user can begin a new project after finishing the previous one.
        self._reset_mode(mode)
        page = self._MODE_TO_PAGE[mode]
        self._stack.setCurrentIndex(page)

        label, desc = self._MODE_LABELS[mode]
        self.setWindowTitle(
            f"FIMsim  |  Flood Inundation Model Simulation Tool  v1.0  ({label})"
        )

        is_tab_mode = mode in ("lisflood", "triton", "arc_curve2flood")
        self._back_btn.setVisible(True)   # show for all modes
        self._prev_btn.setVisible(True)
        self._next_btn.setVisible(True)
        if not is_tab_mode:
            widget = self._standalone_widget(mode)
            if widget:
                widget.nav_changed.connect(self._update_nav_from_standalone)
                # Trigger initial state (works for both _stack and _tabs)
                idx, count = self._widget_nav_state(widget)
                self._update_nav_from_standalone(idx, count)
        self._update_nav()
        self._append_log(f"Mode selected: {label}")
        if is_tab_mode:
            self._append_log("Start with Step 1: create or open a project folder.")

    # Backward-compat alias (model_selector signal name fallback)
    _on_model_selected = _on_mode_selected

    def _go_to_selector(self):
        """Return from a mode back to the category model-selector page."""
        for w in (self._mode_dem, self._mode_lulc, self._mode_flowline,
                  self._mode_streamflow):
            try:
                w.nav_changed.disconnect()
            except Exception:
                pass
        self._stack.setCurrentIndex(_PAGE_SELECTOR)
        self._active_model = None
        # Keep the category title in the window title
        self.setWindowTitle(
            "FIMsim  |  Flood Inundation Model Simulation Tool  v1.0"
        )
        self._back_btn.setVisible(False)
        self._prev_btn.setVisible(False)
        self._next_btn.setVisible(False)
        self._update_nav()

    # ── Mode reset ───────────────────────────────────────────────────────────

    def _reset_mode(self, mode: str):
        """Wipe the chosen mode's state so the user can start a fresh project.

        Called every time _on_mode_selected fires.  For standalone modes we
        invoke their `reset()` method.  For LISFLOOD / TRITON we re-init the
        per-model context dict, disable all tabs except the first, and tell
        every step widget to reset itself if it has a reset() method.
        """
        if mode == "dem":
            self._mode_dem.reset()
        elif mode == "lulc_manning":
            self._mode_lulc.reset()
        elif mode == "flowline":
            self._mode_flowline.reset()
        elif mode == "streamflow":
            self._mode_streamflow.reset()
        elif mode == "lisflood":
            self._reset_workflow("lisflood", self._lfp_tabs, self._lfp_steps)
        elif mode == "triton":
            self._reset_workflow("triton", self._triton_tabs, self._triton_steps)
        elif mode == "arc_curve2flood":
            self._reset_workflow("arc_curve2flood", self._arc_tabs, self._arc_steps)

    def _reset_workflow(self, model: str, tabs, widgets):
        """Reset a tab-based workflow (LISFLOOD-FP / TRITON) for a fresh start."""
        # Drop saved per-model context so step widgets don't pick up stale data
        self._ctx_path[model] = None
        self._ctx[model]      = {}

        # Switch back to tab 0 (Project).  Tabs stay enabled so the user
        # can still navigate between them — skipping is allowed.
        tabs.blockSignals(True)
        try:
            tabs.setCurrentIndex(0)
            for i in range(tabs.count()):
                tabs.setTabEnabled(i, True)
        finally:
            tabs.blockSignals(False)

        # Hide stale "report" / "error" panels and clear context references
        # in each step widget.  We use hasattr() to stay tolerant of widgets
        # that don't expose every helper.
        for w in widgets:
            for attr in ("_report", "_error_lbl"):
                lbl = getattr(w, attr, None)
                if lbl is not None and hasattr(lbl, "setVisible"):
                    lbl.setVisible(False)
            for attr in ("_ctx", "_ctx_path"):
                if hasattr(w, attr):
                    setattr(w, attr, None if attr == "_ctx_path" else {})
            if hasattr(w, "reset") and callable(w.reset):
                try:
                    w.reset()
                except Exception:
                    pass

    # ── Navigation ───────────────────────────────────────────────────────────

    def _current_tabs(self):
        if self._active_model == "lisflood":
            return self._lfp_tabs
        if self._active_model == "triton":
            return self._triton_tabs
        if self._active_model == "arc_curve2flood":
            return self._arc_tabs
        return None

    def _standalone_widget(self, mode):
        return {
            "dem":          self._mode_dem,
            "lulc_manning": self._mode_lulc,
            "flowline":     self._mode_flowline,
            "streamflow":   self._mode_streamflow,
        }.get(mode)

    @staticmethod
    def _widget_nav_state(w) -> tuple:
        """Return (current_idx, page_count) for a standalone mode widget.
        Works for both QStackedWidget (_stack) and QTabWidget (_tabs)."""
        if w is None:
            return 0, 1
        if hasattr(w, "_tabs"):
            return w._tabs.currentIndex(), w._tabs.count()
        if hasattr(w, "_stack"):
            return w._stack.currentIndex(), w._stack.count()
        return 0, 1

    def _update_nav_from_standalone(self, idx: int, count: int):
        self._prev_btn.setEnabled(idx > 0)
        self._prev_btn.setVisible(True)
        self._next_btn.setEnabled(idx < count - 1)
        self._next_btn.setVisible(True)

    def _on_tab_changed(self, _idx):
        self._sync_aoi_to_entered_step()
        self._update_nav()

    def _sync_aoi_to_entered_step(self):
        """Push the confirmed AOIs to whatever downstream step the user just
        navigated to.

        Steps normally receive their context only via the chained
        ``step_completed`` hand-off (each step feeds the next).  That means
        navigating by *clicking a tab* — instead of using 'Next step ▶' —
        would leave a step with no ``aoi_features`` and it would fall back to
        single-AOI mode.  Here we commit the confirmed AOIs and set_context
        the entered step so every step shows the full per-AOI list no matter
        how the user navigates.
        """
        model = self._active_model
        if model not in ("lisflood", "triton", "arc_curve2flood"):
            return
        tabs = self._current_tabs()
        widgets = {
            "lisflood":        self._lfp_steps,
            "triton":          self._triton_steps,
            "arc_curve2flood": self._arc_steps,
        }[model]
        if tabs is None or not widgets:
            return
        idx = tabs.currentIndex()
        # Tabs: 0=Project, 1=AOI, 2.. = downstream steps.  Only feed downstream.
        if idx < 2 or idx >= len(widgets):
            return
        aoi_step = widgets[1]
        commit = getattr(aoi_step, "commit_confirmed_to_ctx", None)
        if not callable(commit):
            return
        data = commit()
        if not data:
            return
        self._update_context(model, data)
        confirmed_n = len(data["ctx"].get("aoi_features", []) or [])
        entered = widgets[idx]
        # Re-push only when the entered step isn't already showing this AOI
        # set, so we don't reset a step the user has already run.
        cur = getattr(entered, "_aoi_features", None)
        if cur is None:
            cur = getattr(entered, "_features", [])
        if len(cur or []) != confirmed_n:
            entered.set_context(self._ctx_path[model], self._ctx[model])

    def _update_nav(self):
        tabs = self._current_tabs()
        on_selector = (self._stack.currentIndex() == _PAGE_SELECTOR)
        is_tab_mode = self._active_model in ("lisflood", "triton") and tabs is not None
        is_standalone = self._active_model in (
            "dem", "lulc_manning", "flowline", "streamflow"
        )
        show_nav = not on_selector
        self._prev_btn.setVisible(show_nav and (is_tab_mode or is_standalone))
        self._next_btn.setVisible(show_nav and (is_tab_mode or is_standalone))
        if is_tab_mode and tabs:
            idx = tabs.currentIndex()
            n   = tabs.count()
            # All tabs are always enabled now (skip-step navigation), so
            # Next is enabled whenever there's a next tab to go to.
            self._prev_btn.setEnabled(idx > 0)
            self._next_btn.setEnabled(idx < n - 1)
        elif is_standalone:
            w = self._standalone_widget(self._active_model)
            idx, n = self._widget_nav_state(w)
            self._prev_btn.setEnabled(idx > 0)
            self._next_btn.setEnabled(idx < n - 1)
        else:
            self._prev_btn.setEnabled(False)
            self._next_btn.setEnabled(False)

    def _go_prev(self):
        tabs = self._current_tabs()
        if tabs:
            idx = tabs.currentIndex()
            if idx > 0:
                tabs.setCurrentIndex(idx - 1)
        else:
            w = self._standalone_widget(self._active_model)
            if w:
                w.go_prev()

    def _go_next(self):
        tabs = self._current_tabs()
        if not tabs:
            w = self._standalone_widget(self._active_model)
            if w:
                w.go_next()
            return
        idx = tabs.currentIndex()

        # Special-case the AOI tab (index 1).  The user has no per-step
        # "Proceed" button there — clicking the bottom-bar "Next step ▶"
        # is what commits the confirmed AOIs to ctx.  proceed_to_next()
        # emits step_completed when there's at least one confirmed AOI;
        # the matching done-slot then advances the tab automatically.
        # If no AOIs are confirmed, proceed_to_next() pops a warning and
        # we stay on the AOI tab.
        if idx == 1 and self._active_model in ("lisflood", "triton", "arc_curve2flood"):
            widgets = {
                "lisflood":        self._lfp_steps,
                "triton":          self._triton_steps,
                "arc_curve2flood": self._arc_steps,
            }[self._active_model]
            aoi_widget = widgets[1]
            if hasattr(aoi_widget, "proceed_to_next"):
                aoi_widget.proceed_to_next()
                return

        # All tabs are always enabled — user can advance whenever there's
        # a next tab.  Each step's run-button validates upstream data
        # internally and shows a clear error if something is missing.
        if idx < tabs.count() - 1:
            tabs.setCurrentIndex(idx + 1)

    # ── Context propagation ──────────────────────────────────────────────────

    def _update_context(self, model: str, data: dict):
        if "ctx_path" in data:
            self._ctx_path[model] = data["ctx_path"]
        if "ctx" in data:
            self._ctx[model] = data["ctx"]

    def _make_project_done_slot(self, model, tabs, widgets):
        def _slot(data: dict):
            self._update_context(model, data)
            # Tabs are always enabled now — just propagate context to the
            # AOI step (tab 1) so it knows the project_dir.
            widgets[1].set_context(self._ctx_path[model], self._ctx[model])
            self._update_nav()
        return _slot

    def _make_step_done_slot(self, model, tabs, widgets, current_idx, next_idx):
        def _slot(data: dict):
            self._update_context(model, data)
            if next_idx is not None:
                widgets[next_idx].set_context(
                    self._ctx_path[model], self._ctx[model])
                # The AOI step (index 1) finishes via an explicit
                # "Proceed to next step ▶" click, so jump the user to the
                # next tab automatically.  Other steps stay put so the user
                # can read the run report before moving on.
                if current_idx == 1:
                    tabs.setCurrentIndex(next_idx)
            else:
                self._append_log(
                    f"All steps complete! {model.upper()} input files are ready.")
            self._update_nav()
        return _slot

    # ── Logging ──────────────────────────────────────────────────────────────

    def _append_log(self, text: str):
        ts = QDateTime.currentDateTime().toString("HH:mm:ss")
        self._log_panel.append(f"[{ts}] {text}")
        self._log_panel.moveCursor(QTextCursor.MoveOperation.End)

    # ── Graceful shutdown ────────────────────────────────────────────────────

    def _all_step_widgets(self):
        """Yield every step / mode widget that may own a background worker."""
        for w in (self._lfp_steps or []):
            yield w
        for w in (self._triton_steps or []):
            yield w
        for w in (self._arc_steps or []):
            yield w
        for w in (self._mode_dem, self._mode_lulc,
                  self._mode_flowline, self._mode_streamflow):
            if w is not None:
                yield w

    def _running_workers(self):
        """Collect any ``Worker`` QThread instances that are still running.

        Step widgets store their background thread under a few different
        names (``_worker``, ``_detector``, ``_river_worker``, …).  We
        scan each widget's ``__dict__`` AND every QObject child to catch
        workers held by nested widgets (e.g. the multi-AOI step's
        river / HUC / gauge downloaders live in a child widget).
        """
        from gui.worker import Worker

        seen, live = set(), []

        def _consider(obj):
            if not isinstance(obj, Worker):
                return
            if id(obj) in seen:
                return
            seen.add(id(obj))
            try:
                if obj.isRunning():
                    live.append(obj)
            except RuntimeError:
                # Underlying Qt object already deleted — skip.
                pass

        for w in self._all_step_widgets():
            # Direct attribute scan
            for attr_val in vars(w).values():
                _consider(attr_val)
            # Recursive QObject child scan (covers nested widgets that
            # own their own workers — e.g. the multi-AOI sub-widget).
            try:
                for child in w.findChildren(Worker):
                    _consider(child)
            except Exception:
                pass
        return live

    def closeEvent(self, event):
        """Confirm before exiting if a background worker is still running.

        Without this, Qt destroys live ``QThread`` instances on shutdown
        which surfaces as ``QThread: Destroyed while thread is still
        running`` and a hard crash.  This is exactly the "I got an error
        when I tried to close it" path users hit while a long Manning /
        DEM / etc. download is in flight.
        """
        live = self._running_workers()
        if not live:
            event.accept()
            return

        resp = QMessageBox.question(
            self,
            "Background task running",
            f"{len(live)} background task(s) are still running "
            "(downloads / file preparation).\n\n"
            "Quit anyway?  Any in-progress download will be cancelled "
            "and partially-written files may be discarded.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            event.ignore()
            return

        # First, ask each worker to cancel cooperatively (will trigger on
        # the next log_fn call inside the worker).
        for w in live:
            try:
                w.cancel()
            except Exception:
                pass

        # Give cooperative cancellation up to ~3 s per worker, then fall
        # back to terminate() so the QThread is stopped before Qt tears
        # the QApplication down.
        for w in live:
            try:
                if not w.wait(3000):
                    w.terminate()
                    w.wait(1000)
            except Exception:
                pass

        event.accept()
