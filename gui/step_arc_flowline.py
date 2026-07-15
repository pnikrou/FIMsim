"""ARC step 5 — Flowline.

Downloads the NHD stream network for each AOI and saves it as
``<AOI>/arc-files/flowline.shp`` (ARC's stream reaches).  Works for one AOI
(single run) or many (per-AOI loop with X / Y progress).
"""
import re
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QGroupBox,
    QProgressBar,
)
from PyQt6.QtCore import pyqtSignal

from core.arc_orchestrate import run_arc_flowline_for_all_aois
from gui.worker import Worker
from gui.run_button import set_running, set_ready

_RUN_STYLE = (
    "font-weight:bold; padding:8px 20px; background:#276749; "
    "color:white; border-radius:4px; font-size:13px;"
)


class StepArcFlowlineWidget(QWidget):
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
        self._clear_results()

    def reset(self):
        self._aoi_features = []
        self._clear_results()
        self._progress.setVisible(False)
        self._status.setVisible(False)
        self._refresh_header()

    # ── UI ──────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 22, 28, 22)
        layout.setSpacing(10)

        note = QLabel(
            "★ Downloads the NHD stream network for each AOI and saves it as "
            "flowline.shp (ARC's stream reaches) inside the AOI's arc-files/ "
            "folder.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#718096; font-size:12px;")
        layout.addWidget(note)

        self._header = QLabel("")
        self._header.setStyleSheet("color:#2d3748; font-size:12px; font-weight:bold;")
        layout.addWidget(self._header)

        run_row = QHBoxLayout()
        self._run_btn = QPushButton("Download flowlines for all")
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

        self._results_gb = QGroupBox("Flowline results")
        self._results_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        self._results_inner = QVBoxLayout(self._results_gb)
        self._results_inner.setSpacing(2)
        self._results_gb.setVisible(False)
        layout.addWidget(self._results_gb)

        layout.addStretch()

    def _refresh_header(self):
        n = len(self._aoi_features)
        if n == 0:
            self._header.setText("(Complete the AOI step first.)")
            self._run_btn.setVisible(False)
        elif n == 1:
            self._header.setText("1 AOI — flowlines will be downloaded for it.")
            self._run_btn.setText("Download flowlines")
            self._run_btn.setVisible(True)
        else:
            self._header.setText(f"{n} AOIs — flowlines will be downloaded for each.")
            self._run_btn.setText("Download flowlines for all")
            self._run_btn.setVisible(True)

    def _clear_results(self):
        while self._results_inner.count():
            it = self._results_inner.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self._results_gb.setVisible(False)

    # ── run ───────────────────────────────────────────────────────────────────

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
        self._status.setText("Downloading NHD flowlines …")
        self._status.setStyleSheet("color:#744210; font-size:12px; font-weight:bold;")
        self._status.setVisible(True)
        set_running(self._run_btn)
        # Always go through the per-AOI orchestrator (even for one AOI): it
        # writes arc_flowline_path into the AOI's workflow_context.json,
        # which is where step 6 (flow file) looks for it.
        self._worker = Worker(
            run_arc_flowline_for_all_aois,
            ctx_path=self._ctx_path, ctx=self._ctx)
        self._worker.message.connect(self._on_message)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_message(self, msg):
        self._log(msg)
        m = re.search(r"Flowline \[(\d+)/(\d+)\] finished", msg)
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
        per = ctx.get("arc_flowline_per_aoi")
        if per:
            rows = per
        else:
            rows = [{
                "name":     ctx.get("aoi_name", "AOI"),
                "flowline": ctx.get("arc_flowline_path"),
                "count":    ctx.get("arc_flowline_count"),
            }]
        for r in rows:
            if r.get("failed"):
                txt = f"✗  {r.get('name')} — {r.get('error', 'failed')}"
                color = "#c53030"
            else:
                fname = Path(r.get("flowline") or "").name
                txt = f"✓  {r.get('name')} — {r.get('count')} reach(es) → {fname}"
                color = "#276749"
            lbl = QLabel(txt)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(f"color:{color}; font-size:11px; padding:2px 0;")
            self._results_inner.addWidget(lbl)
        self._results_gb.setVisible(True)
