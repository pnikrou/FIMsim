"""ARC step 7 — Run ARC-Curve2Flood.

Assembles each AOI's inputs (DEM, LandCover, Manning table, stream shapefile,
flow file) into the folder layout ARC expects, then runs the full pipeline
in-app:

    Process_ARC_Geospatial_Data  →  Arc().run()  →  Curve2Flood

Outputs per AOI (inside <AOI>/arc-files/):
    VDT/CurveFile.csv        — ARC rating curves
    FloodMap/Curve2Flood_FIM.tif — the flood inundation map
"""
import re
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QCheckBox,
    QPushButton, QGroupBox, QProgressBar,
)
from PyQt6.QtCore import pyqtSignal

from core.arc_orchestrate import run_arc_curve2flood_for_all_aois
from gui.worker import Worker
from gui.run_button import set_running, set_ready

# Real Curve2Flood mapper options (see curve2flood.core).
_MAPPERS = [
    "Curve2Flood-Kernel Weighted",
    "Curve2Flood-FLDPLNpy",
    "Curve2Flood-Multi-Point Interpolation",
]

_RUN_STYLE = (
    "font-weight:bold; padding:8px 20px; background:#276749; "
    "color:white; border-radius:4px; font-size:13px;"
)


class StepArcConfigWidget(QWidget):
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
            "★ Runs the full ARC-Curve2Flood pipeline for each AOI: the stream "
            "network is rasterized, ARC builds a rating curve for every reach, "
            "and Curve2Flood maps the peak flow from step 6 into a flood "
            "inundation raster. This step can take a while for large domains.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#718096; font-size:12px;")
        layout.addWidget(note)

        self._header = QLabel("")
        self._header.setStyleSheet("color:#2d3748; font-size:12px; font-weight:bold;")
        layout.addWidget(self._header)

        gb = QGroupBox("Run options")
        gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        gv = QVBoxLayout(gb)
        gv.setSpacing(8)

        mp_row = QHBoxLayout()
        mp_row.addWidget(QLabel("Flood mapper:"))
        self._mapper = QComboBox()
        self._mapper.addItems(_MAPPERS)
        self._mapper.setFixedWidth(300)
        mp_row.addWidget(self._mapper)
        mp_row.addStretch()
        gv.addLayout(mp_row)

        self._gpkg = QCheckBox("Also write the flood map as GeoPackage  (Make_Output_GPKG)")
        self._gpkg.setChecked(True)
        gv.addWidget(self._gpkg)

        self._banks_lc = QCheckBox("Find banks from land cover  (use_land_cover_to_find_banks)")
        self._banks_lc.setChecked(True)
        gv.addWidget(self._banks_lc)

        self._bathy_banks = QCheckBox("Use bank elevations for bathymetry  (bathy_use_banks)")
        self._bathy_banks.setChecked(False)
        gv.addWidget(self._bathy_banks)

        layout.addWidget(gb)

        run_row = QHBoxLayout()
        self._run_btn = QPushButton("Run ARC-Curve2Flood for all")
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

        self._results_gb = QGroupBox("Flood map results")
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
            self._header.setText("1 AOI — ARC-Curve2Flood will run for it.")
            self._run_btn.setText("Run ARC-Curve2Flood")
            self._run_btn.setVisible(True)
        else:
            self._header.setText(f"{n} AOIs — ARC-Curve2Flood will run for each.")
            self._run_btn.setText("Run ARC-Curve2Flood for all")
            self._run_btn.setVisible(True)

    def _clear_results(self):
        while self._results_inner.count():
            it = self._results_inner.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self._results_gb.setVisible(False)

    # ── run ───────────────────────────────────────────────────────────────────

    def _run_cfg(self) -> dict:
        return {
            "mapper":    self._mapper.currentText(),
            "make_gpkg": self._gpkg.isChecked(),
            "use_land_cover_to_find_banks": self._banks_lc.isChecked(),
            "bathy_use_banks": self._bathy_banks.isChecked(),
        }

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
        self._status.setText(
            "Running ARC (rating curves) + Curve2Flood (flood mapping) … this "
            "can take several minutes per AOI.")
        self._status.setStyleSheet("color:#744210; font-size:12px; font-weight:bold;")
        self._status.setVisible(True)
        set_running(self._run_btn)
        self._worker = Worker(
            run_arc_curve2flood_for_all_aois,
            ctx_path=self._ctx_path, ctx=self._ctx, run_cfg=self._run_cfg())
        self._worker.message.connect(self._on_message)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_message(self, msg):
        self._log(msg)
        m = re.search(r"ARC-Curve2Flood \[(\d+)/(\d+)\] finished", msg)
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
        per = ctx.get("arc_run_per_aoi")
        if per:
            rows = per
        else:
            rows = [{"name": ctx.get("aoi_name", "AOI"),
                     "flood_map": ctx.get("arc_flood_map"),
                     "curve_file": ctx.get("arc_curve_file")}]
        any_ok = False
        for r in rows:
            if r.get("failed"):
                txt = f"✗  {r.get('name')} — {r.get('error', 'failed')}"
                color = "#c53030"
            elif r.get("flood_map"):
                txt = f"✓  {r.get('name')} — flood map: {r['flood_map']}"
                color = "#276749"
                any_ok = True
            else:
                txt = (f"⚠  {r.get('name')} — ARC ran but no flood raster was "
                       "written (check the log)")
                color = "#744210"
            lbl = QLabel(txt)
            lbl.setWordWrap(True)
            lbl.setTextInteractionFlags(lbl.textInteractionFlags())
            lbl.setStyleSheet(f"color:{color}; font-size:11px; padding:2px 0;")
            self._results_inner.addWidget(lbl)
        if any_ok:
            hint = QLabel("Open the flood map(s) in QGIS / ArcGIS to inspect "
                          "the inundation extent.")
            hint.setStyleSheet("color:#718096; font-size:11px; padding:2px 0;")
            self._results_inner.addWidget(hint)
        self._results_gb.setVisible(True)
