"""ARC step 6 — Streamflow (build the ARC flow file from NWM).

For every reach (COMID) in the step-5 flowline, downloads NWM discharge and
reduces it to a baseflow + max-flow pair — the flow file ARC uses to build
rating curves:

    <AOI>/arc-files/flow.csv    (COMID,base,max)

Sources: NWM Retrospective (1979–2023) or NWM Forecast (2019–now).  NWM's
feature_id is the NHD COMID, so reaches map 1:1 to NWM series.
"""
import re

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QGroupBox, QProgressBar, QDateEdit, QDoubleSpinBox,
)
from PyQt6.QtCore import pyqtSignal, QDate

from core.arc_orchestrate import run_arc_flowfile_for_all_aois
from gui.worker import Worker
from gui.run_button import set_running, set_ready

_RUN_STYLE = (
    "font-weight:bold; padding:8px 20px; background:#276749; "
    "color:white; border-radius:4px; font-size:13px;"
)

_SOURCES = [
    ("NWM Retrospective (1979-2023)", "nwm_retro"),
    ("NWM Forecast (2019-now)",       "nwm_forecast"),
]
_F_RANGES = ["short_range", "medium_range", "long_range"]
_F_CYCLES = ["Auto", "00", "06", "12", "18"]


class StepArcStreamflowWidget(QWidget):
    step_completed = pyqtSignal(dict)

    def __init__(self, log_fn=print, parent=None):
        super().__init__(parent)
        self._log = log_fn
        self._ctx_path = None
        self._ctx = {}
        self._aoi_features = []
        self._worker = None
        self._total = 0
        self._build_ui()

    # ── step interface ─────────────────────────────────────────────────────────

    def set_context(self, ctx_path, ctx):
        self._ctx_path = ctx_path
        self._ctx = ctx or {}
        self._aoi_features = list(self._ctx.get("aoi_features", []) or [])
        self._refresh_header()

    def reset(self):
        self._aoi_features = []
        self._progress.setVisible(False)
        self._status.setVisible(False)
        self._clear_results()
        self._refresh_header()

    # ── UI ──────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 22, 28, 22)
        layout.setSpacing(10)

        note = QLabel(
            "★ Builds the ARC flow file: for every reach in the step-5 flowline, "
            "NWM discharge is downloaded and reduced to a baseflow + peak-flow "
            "pair (flow.csv: COMID,base,max). ARC uses it to build the rating "
            "curves, and the peak is the flood that gets mapped.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#718096; font-size:12px;")
        layout.addWidget(note)

        self._header = QLabel("")
        self._header.setStyleSheet("color:#2d3748; font-size:12px; font-weight:bold;")
        layout.addWidget(self._header)

        gb = QGroupBox("NWM streamflow settings (applied to every AOI)")
        gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        gv = QVBoxLayout(gb)
        gv.setSpacing(8)

        src_row = QHBoxLayout()
        src_row.addWidget(QLabel("Source:"))
        self._src = QComboBox()
        for label, _key in _SOURCES:
            self._src.addItem(label)
        self._src.setFixedWidth(260)
        self._src.currentIndexChanged.connect(self._on_src_changed)
        src_row.addWidget(self._src)
        src_row.addStretch()
        gv.addLayout(src_row)

        # Retrospective window
        self._retro_w = QWidget()
        rr = QHBoxLayout(self._retro_w)
        rr.setContentsMargins(0, 0, 0, 0)
        rr.addWidget(QLabel("Event window:"))
        self._start = QDateEdit(QDate(2017, 8, 25))
        self._start.setCalendarPopup(True)
        self._start.setDisplayFormat("yyyy-MM-dd")
        rr.addWidget(self._start)
        rr.addWidget(QLabel("to"))
        self._end = QDateEdit(QDate(2017, 9, 1))
        self._end.setCalendarPopup(True)
        self._end.setDisplayFormat("yyyy-MM-dd")
        rr.addWidget(self._end)
        rr.addStretch()
        gv.addWidget(self._retro_w)

        # Forecast controls
        self._fcst_w = QWidget()
        fr = QHBoxLayout(self._fcst_w)
        fr.setContentsMargins(0, 0, 0, 0)
        fr.addWidget(QLabel("Issue date:"))
        self._fdate = QDateEdit(QDate.currentDate())
        self._fdate.setCalendarPopup(True)
        self._fdate.setDisplayFormat("yyyy-MM-dd")
        fr.addWidget(self._fdate)
        fr.addWidget(QLabel("Range:"))
        self._frange = QComboBox()
        self._frange.addItems(_F_RANGES)
        self._frange.setCurrentText("medium_range")
        fr.addWidget(self._frange)
        fr.addWidget(QLabel("Cycle:"))
        self._fcycle = QComboBox()
        self._fcycle.addItems(_F_CYCLES)
        fr.addWidget(self._fcycle)
        fr.addStretch()
        gv.addWidget(self._fcst_w)

        # Baseflow percentile
        bp_row = QHBoxLayout()
        bp_row.addWidget(QLabel("Baseflow percentile:"))
        self._base_pct = QDoubleSpinBox()
        self._base_pct.setRange(0.0, 50.0)
        self._base_pct.setValue(10.0)
        self._base_pct.setSuffix(" %")
        self._base_pct.setFixedWidth(90)
        bp_row.addWidget(self._base_pct)
        bp_hint = QLabel("(low-flow percentile used as ARC's baseflow; peak = max)")
        bp_hint.setStyleSheet("color:#718096; font-size:11px;")
        bp_row.addWidget(bp_hint)
        bp_row.addStretch()
        gv.addLayout(bp_row)

        layout.addWidget(gb)

        run_row = QHBoxLayout()
        self._run_btn = QPushButton("Build flow file for all")
        self._run_btn.setStyleSheet(_RUN_STYLE)
        self._run_btn.clicked.connect(self._run_step)
        run_row.addWidget(self._run_btn)
        run_row.addStretch()
        layout.addLayout(run_row)

        self._progress = QProgressBar()
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._progress.setFormat("%v / %m AOIs")
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color:#276749; font-size:12px; font-weight:bold;")
        self._status.setVisible(False)
        layout.addWidget(self._status)

        self._results_gb = QGroupBox("Flow file results")
        self._results_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        self._results_inner = QVBoxLayout(self._results_gb)
        self._results_inner.setSpacing(2)
        self._results_gb.setVisible(False)
        layout.addWidget(self._results_gb)

        layout.addStretch()
        self._on_src_changed()

    def _on_src_changed(self, *_):
        is_retro = self._src.currentIndex() == 0
        self._retro_w.setVisible(is_retro)
        self._fcst_w.setVisible(not is_retro)

    def _refresh_header(self):
        n = len(self._aoi_features)
        if n == 0:
            self._header.setText("(Complete the AOI step first.)")
            self._run_btn.setVisible(False)
        elif n == 1:
            self._header.setText("1 AOI — a flow.csv will be built for it.")
            self._run_btn.setText("Build flow file")
            self._run_btn.setVisible(True)
        else:
            self._header.setText(f"{n} AOIs — a flow.csv will be built for each.")
            self._run_btn.setText("Build flow file for all")
            self._run_btn.setVisible(True)

    def _clear_results(self):
        while self._results_inner.count():
            it = self._results_inner.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self._results_gb.setVisible(False)

    # ── run ───────────────────────────────────────────────────────────────────

    def _flow_cfg(self) -> dict:
        is_retro = self._src.currentIndex() == 0
        cfg = {"base_percentile": float(self._base_pct.value())}
        if is_retro:
            cfg["source"]   = "nwm_retro"
            cfg["start_dt"] = self._start.date().toString("yyyy-MM-dd")
            cfg["end_dt"]   = self._end.date().toString("yyyy-MM-dd")
        else:
            cfg["source"]         = "nwm_forecast"
            cfg["forecast_date"]  = self._fdate.date().toString("yyyy-MM-dd")
            cfg["forecast_range"] = self._frange.currentText()
            cyc = self._fcycle.currentText()
            cfg["forecast_hour"]  = None if cyc == "Auto" else int(cyc)
        return cfg

    def _run_step(self):
        if not self._ctx_path or not self._aoi_features:
            self._log("Complete the AOI step first.")
            return
        self._clear_results()
        n = len(self._aoi_features)
        self._total = n
        self._progress.setRange(0, n)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._status.setText("Downloading NWM discharge and building flow files …")
        self._status.setStyleSheet("color:#744210; font-size:12px; font-weight:bold;")
        self._status.setVisible(True)
        set_running(self._run_btn)
        self._worker = Worker(
            run_arc_flowfile_for_all_aois,
            ctx_path=self._ctx_path, ctx=self._ctx, flow_cfg=self._flow_cfg())
        self._worker.message.connect(self._on_message)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_message(self, msg):
        self._log(msg)
        m = re.search(r"Flow file \[(\d+)/(\d+)\] finished", msg)
        if m:
            self._progress.setValue(int(m.group(1)))

    def _on_done(self, ctx):
        self._ctx = ctx
        self._progress.setValue(self._total)
        n = max(len(self._aoi_features), 1)
        self._status.setText(f"All {n} AOI(s) processed.")
        self._status.setStyleSheet("color:#276749; font-size:12px; font-weight:bold;")
        self._status.setVisible(True)
        set_ready(self._run_btn)
        self._build_results(ctx)
        self.step_completed.emit({"ctx_path": self._ctx_path, "ctx": ctx})

    def _on_error(self, msg):
        set_ready(self._run_btn)
        self._progress.setVisible(False)
        self._status.setText(f"Error: {msg.splitlines()[0]}")
        self._status.setStyleSheet("color:#c53030; font-size:12px; font-weight:bold;")
        self._status.setVisible(True)
        self._log(f"ERROR: {msg}")

    def _build_results(self, ctx):
        self._clear_results()
        per = ctx.get("arc_flowfile_per_aoi")
        if per:
            rows = per
        else:
            rows = [{"name": ctx.get("aoi_name", "AOI"),
                     "flow_csv": ctx.get("arc_flow_csv"),
                     "reaches": ctx.get("arc_flow_reaches")}]
        for r in rows:
            if r.get("failed"):
                txt = f"✗  {r.get('name')} — {r.get('error', 'failed')}"
                color = "#c53030"
            else:
                txt = (f"✓  {r.get('name')} — {r.get('reaches')} reach(es) → "
                       f"flow.csv (COMID,base,max)")
                color = "#276749"
            lbl = QLabel(txt)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(f"color:{color}; font-size:11px; padding:2px 0;")
            self._results_inner.addWidget(lbl)
        self._results_gb.setVisible(True)
