"""HEC-RAS mode — 4-step wizard.

Project → AOI → DEM (with buffer) → LULC & Manning → Flowline → Flowdata

Per AOI outputs in HECRAS_files/:
  dem.tif           — DEM expanded by buffer on each side
  lulc.tif          — LULC raster (also buffered)
  manning_n.tif     — Manning n raster
  manning_n.shp     — Manning n as polygon shapefile
  flowline.shp      — Main river (highest stream order)
  main_river_line.gpkg — For preview map
  discharge.csv     — Discharge time series
"""
from pathlib import Path
from typing import List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QGroupBox,
    QFormLayout, QComboBox, QDoubleSpinBox, QStackedWidget, QScrollArea,
    QSpinBox, QFrame, QProgressBar, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QHeaderView, QDateTimeEdit, QLineEdit, QFileDialog,
)
from PyQt6.QtCore import pyqtSignal, Qt, QDateTime
from PyQt6.QtGui import QFont as _QFont, QColor

from gui.raster_preview import RasterPreviewCanvas
from gui.step_project import StepProjectWidget
from gui.multi_aoi_widget import MultiAOIWidget
from gui.manning_table_widget import ManningTableWidget
from gui.aoi_flowdata_card import AOIFlowdataCard
from gui.run_button import set_running, set_ready
from gui.bci_preview import BCIPreviewCanvas
from gui.hydrograph_preview import HydrographPreviewCanvas
from gui.worker import Worker
from core.orchestrate import (
    run_hecras_dem, run_hecras_manning, run_hecras_flowline, run_hecras_flowdata,
)
from core.nlcd import NLCD_MANNING, SENTINEL2_MANNING

# ── Style constants ────────────────────────────────────────────────────────────
_RUN_STYLE  = "font-weight:bold; padding:7px 20px; background:#C05621; color:white; border-radius:4px;"
_OK_STYLE   = "padding:6px 10px; background:#f0fff4; border:1px solid #9ae6b4; border-radius:4px; color:#276749; font-weight:bold; font-size:12px;"
_BLUE_STYLE = "padding:6px 10px; background:#ebf8ff; border:1px solid #90cdf4; border-radius:4px; color:#2c5282; font-weight:bold; font-size:12px;"
_ERR_STYLE  = "padding:10px; background:#fff5f5; border:1px solid #fc8181; border-radius:4px; font-size:12px; color:#c53030;"
_RPT_STYLE  = "padding:10px; background:#f0fff4; border:1px solid #9ae6b4; border-radius:4px; font-size:12px;"

_ROW_STYLE = (
    "QFrame { background:#f9fafb; border:1px solid #e2e8f0; border-radius:3px; padding:3px 6px; }"
    "QFrame:hover { background:#f0f2f5; }"
)
_BTN_STYLE = (
    "QPushButton { text-align:left; background:transparent; border:none; "
    "color:#2d3748; font-weight:bold; padding:2px; }"
    "QPushButton:hover { color:#1a202c; text-decoration:underline; }"
)


# ── Inline single-AOI flowdata panel ──────────────────────────────────────────

class _SingleFlowdataPanel(QWidget):
    """Inline flow-data configuration widget for a single AOI.
    Mirrors AOIFlowdataCard._panel without the accordion chrome."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        # Source row
        src_row = QHBoxLayout()
        src_row.addWidget(QLabel("<b>Source:</b>"))
        src_row.addSpacing(8)
        self._src_combo = QComboBox()
        self._src_combo.addItem("Download from NWM (NOAA — USA only)", "nwm")
        self._src_combo.addItem("USGS Gage", "usgs")
        self._src_combo.setFixedWidth(270)
        src_row.addWidget(self._src_combo)
        src_row.addStretch()
        lay.addLayout(src_row)

        # NWM widget
        self._nwm_widget = QWidget()
        nf = QFormLayout(self._nwm_widget)
        nf.setContentsMargins(0, 0, 0, 0)
        nf.setVerticalSpacing(6)

        fid_row = QHBoxLayout()
        self._fids_edit = QLineEdit()
        self._fids_edit.setPlaceholderText(
            "Single ID (e.g. 22164566), comma-separated, or path to .csv"
        )
        fid_browse = QPushButton("Browse…")
        fid_browse.setFixedWidth(75)
        fid_browse.clicked.connect(self._browse_fids)
        fid_row.addWidget(self._fids_edit)
        fid_row.addWidget(fid_browse)
        nf.addRow("Feature ID(s):", fid_row)

        self._autofill_lbl = QLabel("")
        self._autofill_lbl.setStyleSheet("color:#276749; font-size:11px;")
        self._autofill_lbl.setVisible(False)
        nf.addRow(self._autofill_lbl)

        self._nwm_start = QDateTimeEdit()
        self._nwm_start.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._nwm_start.setCalendarPopup(True)
        self._nwm_start.setDateTime(QDateTime(2018, 9, 1, 0, 0))
        nf.addRow("Start date:", self._nwm_start)

        self._nwm_end = QDateTimeEdit()
        self._nwm_end.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._nwm_end.setCalendarPopup(True)
        self._nwm_end.setDateTime(QDateTime(2018, 9, 30, 23, 0))
        nf.addRow("End date:", self._nwm_end)

        self._nwm_interval = QComboBox()
        for lbl, val in [("0.5 hours", 0.5), ("1 hour", 1.0), ("3 hours", 3.0),
                          ("6 hours", 6.0), ("12 hours", 12.0), ("24 hours", 24.0)]:
            self._nwm_interval.addItem(lbl, val)
        self._nwm_interval.setCurrentIndex(1)
        nf.addRow("Interval:", self._nwm_interval)

        nwm_note = QLabel(
            "★  NWM retrospective covers 1979-02-01 to 2020-12-31. "
            "After that date the app uses the NWM operational forecast."
        )
        nwm_note.setWordWrap(True)
        nwm_note.setStyleSheet("color:#555; font-size:11px;")
        nf.addRow(nwm_note)

        lay.addWidget(self._nwm_widget)

        # USGS widget
        self._usgs_widget = QWidget()
        uf = QFormLayout(self._usgs_widget)
        uf.setContentsMargins(0, 0, 0, 0)
        uf.setVerticalSpacing(6)

        gage_row = QHBoxLayout()
        self._gage_edit = QLineEdit()
        self._gage_edit.setPlaceholderText(
            "Single gage (e.g. 02428400), comma-separated, or path to .csv"
        )
        gage_browse = QPushButton("Browse…")
        gage_browse.setFixedWidth(75)
        gage_browse.clicked.connect(self._browse_gages)
        gage_row.addWidget(self._gage_edit)
        gage_row.addWidget(gage_browse)
        uf.addRow("Gage number(s):", gage_row)

        self._usgs_start = QDateTimeEdit()
        self._usgs_start.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._usgs_start.setCalendarPopup(True)
        self._usgs_start.setDateTime(QDateTime(2018, 9, 1, 0, 0))
        uf.addRow("Start date:", self._usgs_start)

        self._usgs_end = QDateTimeEdit()
        self._usgs_end.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._usgs_end.setCalendarPopup(True)
        self._usgs_end.setDateTime(QDateTime(2018, 9, 30, 23, 0))
        uf.addRow("End date:", self._usgs_end)

        self._usgs_interval = QComboBox()
        for lbl, val in [("15 min", 0.25), ("30 min", 0.5), ("1 hour", 1.0),
                          ("3 hours", 3.0), ("6 hours", 6.0),
                          ("12 hours", 12.0), ("24 hours", 24.0)]:
            self._usgs_interval.addItem(lbl, val)
        self._usgs_interval.setCurrentIndex(2)
        uf.addRow("Interval:", self._usgs_interval)

        lay.addWidget(self._usgs_widget)

        self._src_combo.currentIndexChanged.connect(self._on_source_changed)
        self._on_source_changed()

    def _on_source_changed(self, *_):
        src = self._src_combo.currentData()
        self._nwm_widget.setVisible(src == "nwm")
        self._usgs_widget.setVisible(src == "usgs")

    def _browse_fids(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "Select Feature ID CSV", "", "CSV (*.csv *.txt);;All (*)"
        )
        if f:
            self._fids_edit.setText(f)

    def _browse_gages(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "Select Gage CSV", "", "CSV (*.csv *.txt);;All (*)"
        )
        if f:
            self._gage_edit.setText(f)

    def get_config(self) -> dict:
        src = self._src_combo.currentData()
        cfg: dict = {"flow_source": src, "discharge_source": src}
        if src == "nwm":
            cfg["feature_ids"]    = self._fids_edit.text().strip()
            cfg["event_start_dt"] = self._nwm_start.dateTime().toPyDateTime()
            cfg["event_end_dt"]   = self._nwm_end.dateTime().toPyDateTime()
            cfg["interval_hours"] = float(self._nwm_interval.currentData())
        else:
            cfg["gage_ids"]            = self._gage_edit.text().strip()
            cfg["event_start_dt"]      = self._usgs_start.dateTime().toPyDateTime()
            cfg["event_end_dt"]        = self._usgs_end.dateTime().toPyDateTime()
            cfg["usgs_interval_hours"] = float(self._usgs_interval.currentData())
        return cfg

    def set_config(self, cfg: dict):
        raw_src = cfg.get("flow_source") or cfg.get("discharge_source", "nwm")
        if raw_src in ("retrospective", "forecast"):
            raw_src = "nwm"
        idx = self._src_combo.findData(raw_src)
        if idx >= 0:
            self._src_combo.setCurrentIndex(idx)
        if raw_src == "nwm":
            fids = cfg.get("feature_ids", "")
            self._fids_edit.setText(str(fids) if fids else "")
            if cfg.get("event_start_dt"):
                self._nwm_start.setDateTime(
                    QDateTime.fromString(str(cfg["event_start_dt"])[:16], "yyyy-MM-dd HH:mm")
                )
            if cfg.get("event_end_dt"):
                self._nwm_end.setDateTime(
                    QDateTime.fromString(str(cfg["event_end_dt"])[:16], "yyyy-MM-dd HH:mm")
                )
            iv_idx = self._nwm_interval.findData(float(cfg.get("interval_hours", 1.0)))
            if iv_idx >= 0:
                self._nwm_interval.setCurrentIndex(iv_idx)
            # Show autofill label if reach_id was supplied
            reach_id = cfg.get("_reach_id_autofill")
            if reach_id is not None:
                self._autofill_lbl.setText(f"★ Auto-filled from flowline step (reach ID: {reach_id})")
                self._autofill_lbl.setVisible(True)
            else:
                self._autofill_lbl.setVisible(False)
        else:
            self._gage_edit.setText(cfg.get("gage_ids", ""))
            if cfg.get("event_start_dt"):
                self._usgs_start.setDateTime(
                    QDateTime.fromString(str(cfg["event_start_dt"])[:16], "yyyy-MM-dd HH:mm")
                )
            if cfg.get("event_end_dt"):
                self._usgs_end.setDateTime(
                    QDateTime.fromString(str(cfg["event_end_dt"])[:16], "yyyy-MM-dd HH:mm")
                )
            iv_idx = self._usgs_interval.findData(float(cfg.get("usgs_interval_hours", 1.0)))
            if iv_idx >= 0:
                self._usgs_interval.setCurrentIndex(iv_idx)


# ── Main widget ────────────────────────────────────────────────────────────────

class ModeHECRASWidget(QWidget):
    """Self-contained HEC-RAS preparation mode — 4-step wizard."""

    mode_finished = pyqtSignal()
    nav_changed   = pyqtSignal(int, int)

    def __init__(self, log_fn, parent=None):
        super().__init__(parent)
        self._log = log_fn
        self._project_dir: Optional[str] = None
        self._features: List = []
        self._worker: Optional[Worker] = None
        self._dem_summary: Optional[dict] = None
        self._manning_summary: Optional[dict] = None
        self._flowline_summary: Optional[dict] = None
        self._flowdata_summary: Optional[dict] = None
        self._setup_ui()

    # ── UI setup ───────────────────────────────────────────────────────────────

    def _setup_ui(self):
        outer = QVBoxLayout(self)

        self._stack = QStackedWidget()
        outer.addWidget(self._stack)

        self._stack.currentChanged.connect(self._update_nav_buttons)

        # Page 0 — project
        self._proj = StepProjectWidget(self._log, model="generic")
        self._proj.step_completed.connect(self._on_project_done)
        self._stack.addWidget(self._wrap(self._proj))

        # Page 1 — multi-AOI
        self._aoi = MultiAOIWidget(self._log)
        self._aoi.aoi_ready.connect(self._on_aoi_ready)
        self._aoi.back_requested.connect(lambda: self._stack.setCurrentIndex(0))
        self._stack.addWidget(self._wrap(self._aoi))

        # Page 2 — DEM
        self._stack.addWidget(self._wrap(self._build_dem_page()))

        # Page 3 — Manning
        self._stack.addWidget(self._wrap(self._build_manning_page()))

        # Page 4 — Flowline
        self._stack.addWidget(self._wrap(self._build_flowline_page()))

        # Page 5 — Flowdata
        self._stack.addWidget(self._wrap(self._build_flowdata_page()))

        self._stack.setCurrentIndex(0)
        self._update_nav_buttons(0)

    def _wrap(self, w: QWidget) -> QScrollArea:
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setWidget(w)
        return sa

    def _goto_previous_page(self):
        cur = self._stack.currentIndex()
        if cur > 0:
            self._stack.setCurrentIndex(cur - 1)

    def _goto_next_page(self):
        cur = self._stack.currentIndex()
        n   = self._stack.count()
        if cur == 1:
            self._aoi.proceed_to_next()
            return
        if cur == 3:
            # Manning → Flowline: pre-populate flowdata with any existing flowline data
            self._configure_flowdata_page(self._features, self._flowline_summary)
        if cur < n - 1:
            self._stack.setCurrentIndex(cur + 1)

    def _update_nav_buttons(self, idx: int):
        self.nav_changed.emit(idx, self._stack.count())

    def go_prev(self):
        self._goto_previous_page()

    def go_next(self):
        self._goto_next_page()

    # ── DEM page ───────────────────────────────────────────────────────────────

    def _build_dem_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(12)

        # Config group
        gb = QGroupBox("Step 3 — DEM Download")
        gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        fl = QFormLayout(gb)
        fl.setVerticalSpacing(8)

        self._dem_cell_spin = QDoubleSpinBox()
        self._dem_cell_spin.setRange(1.0, 500.0)
        self._dem_cell_spin.setDecimals(1)
        self._dem_cell_spin.setValue(10.0)
        self._dem_cell_spin.setSuffix(" m")
        fl.addRow("DEM cell size:", self._dem_cell_spin)

        self._dem_buffer_spin = QDoubleSpinBox()
        self._dem_buffer_spin.setRange(0.0, 50000.0)
        self._dem_buffer_spin.setDecimals(0)
        self._dem_buffer_spin.setValue(500.0)
        self._dem_buffer_spin.setSuffix(" m")
        fl.addRow("Buffer around AOI:", self._dem_buffer_spin)

        buf_note = QLabel(
            "★  DEM and LULC are downloaded slightly larger than the AOI boundary "
            "for proper external boundary condition placement."
        )
        buf_note.setWordWrap(True)
        buf_note.setStyleSheet("color:#555; font-size:11px; padding-top:4px;")
        fl.addRow(buf_note)

        v.addWidget(gb)

        # Run button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._dem_run_btn = QPushButton("✔  Download DEM")
        self._dem_run_btn.setStyleSheet(_RUN_STYLE)
        self._dem_run_btn.clicked.connect(self._run_dem)
        btn_row.addWidget(self._dem_run_btn)
        v.addLayout(btn_row)

        self._dem_progress = QProgressBar()
        self._dem_progress.setRange(0, 0)
        self._dem_progress.setVisible(False)
        v.addWidget(self._dem_progress)

        self._dem_status_lbl = QLabel("")
        self._dem_status_lbl.setWordWrap(True)
        self._dem_status_lbl.setStyleSheet(_BLUE_STYLE)
        self._dem_status_lbl.setVisible(False)
        v.addWidget(self._dem_status_lbl)

        self._dem_error_lbl = QLabel("")
        self._dem_error_lbl.setWordWrap(True)
        self._dem_error_lbl.setStyleSheet(_ERR_STYLE)
        self._dem_error_lbl.setVisible(False)
        v.addWidget(self._dem_error_lbl)

        self._dem_report_lbl = QLabel("")
        self._dem_report_lbl.setWordWrap(True)
        self._dem_report_lbl.setStyleSheet(_RPT_STYLE)
        self._dem_report_lbl.setVisible(False)
        v.addWidget(self._dem_report_lbl)

        # Results section
        self._dem_results_gb = QGroupBox("Per-AOI DEM outputs  —  click an AOI to preview")
        self._dem_results_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        rgl = QVBoxLayout(self._dem_results_gb)
        self._dem_results_inner = QVBoxLayout()
        self._dem_results_inner.setSpacing(0)
        rgl.addLayout(self._dem_results_inner)
        self._dem_results_gb.setVisible(False)
        v.addWidget(self._dem_results_gb)

        # Preview
        self._dem_preview_gb = QGroupBox("DEM preview")
        self._dem_preview_gb.setMinimumHeight(360)
        pvl = QVBoxLayout(self._dem_preview_gb)

        self._dem_preview_placeholder = QLabel(
            "<i>Click an AOI above to preview its DEM here.</i>"
        )
        self._dem_preview_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._dem_preview_placeholder.setStyleSheet(
            "color:#888; padding:30px; background:#fafafa; "
            "border:1px dashed #cbd5e0; border-radius:4px;"
        )
        pvl.addWidget(self._dem_preview_placeholder)

        # 2-col: left info table, right raster
        self._dem_preview_2col = QWidget()
        h2 = QHBoxLayout(self._dem_preview_2col)
        h2.setContentsMargins(0, 0, 0, 0)
        h2.setSpacing(10)

        info_col = QVBoxLayout()
        info_hdr = QLabel("<b>DEM Information</b>")
        info_hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info_hdr.setStyleSheet("color:#2c5282; font-size:10px; padding-bottom:2px;")
        info_col.addWidget(info_hdr)

        self._dem_info_table = QTableWidget()
        self._dem_info_table.setColumnCount(2)
        self._dem_info_table.horizontalHeader().setVisible(False)
        self._dem_info_table.verticalHeader().setVisible(False)
        self._dem_info_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._dem_info_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._dem_info_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._dem_info_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self._dem_info_table.verticalHeader().setDefaultSectionSize(22)
        self._dem_info_table.setStyleSheet(
            "QTableWidget { font-size:10px; border:1px solid #e2e8f0; }"
            "QTableWidget::item { padding:1px 4px; }"
        )
        self._dem_info_table.setAlternatingRowColors(True)
        info_col.addWidget(self._dem_info_table, 1)
        h2.addLayout(info_col, 3)

        self._dem_raster_preview = RasterPreviewCanvas(self, width=9, height=3.8)
        h2.addWidget(self._dem_raster_preview, 7)

        self._dem_preview_2col.setVisible(False)
        pvl.addWidget(self._dem_preview_2col, 1)

        self._dem_preview_gb.setVisible(False)
        v.addWidget(self._dem_preview_gb)

        v.addStretch()
        return page

    # ── Manning page ───────────────────────────────────────────────────────────

    def _build_manning_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(12)

        gb = QGroupBox("Step 4 — LULC & Manning's n")
        gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        fl = QFormLayout(gb)
        fl.setVerticalSpacing(8)

        buf_info = QLabel(
            "★  Same buffer as DEM step is applied automatically to LULC download."
        )
        buf_info.setWordWrap(True)
        buf_info.setStyleSheet("color:#555; font-size:11px;")
        fl.addRow(buf_info)

        self._mn_src_combo = QComboBox()
        self._mn_src_combo.addItem("NLCD (USGS, USA only)", "nlcd")
        self._mn_src_combo.addItem("Sentinel-2 / Esri (global, 10m)", "sentinel2")
        self._mn_src_combo.currentIndexChanged.connect(self._on_manning_source_changed)
        fl.addRow("Source:", self._mn_src_combo)

        self._mn_nlcd_year = QComboBox()
        self._mn_nlcd_year.addItems(["2021", "2019", "2016"])
        fl.addRow("NLCD year:", self._mn_nlcd_year)

        self._mn_s2_year_spin = QSpinBox()
        self._mn_s2_year_spin.setRange(2017, 2024)
        self._mn_s2_year_spin.setValue(2023)
        self._mn_s2_year_lbl = QLabel("Sentinel-2 year:")
        fl.addRow(self._mn_s2_year_lbl, self._mn_s2_year_spin)
        self._mn_s2_year_lbl.setVisible(False)
        self._mn_s2_year_spin.setVisible(False)

        self._mn_cell_spin = QDoubleSpinBox()
        self._mn_cell_spin.setRange(1.0, 1000.0)
        self._mn_cell_spin.setDecimals(1)
        self._mn_cell_spin.setValue(30.0)
        self._mn_cell_spin.setSuffix(" m")
        fl.addRow("Cell size:", self._mn_cell_spin)

        v.addWidget(gb)

        mn_table_lbl = QLabel("<b>Manning's n table</b> — edit Avg column (clamped to [Min, Max]).")
        mn_table_lbl.setWordWrap(True)
        v.addWidget(mn_table_lbl)

        self._manning_table = ManningTableWidget(NLCD_MANNING)
        v.addWidget(self._manning_table)

        # Run button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._mn_run_btn = QPushButton("✔  Download LULC & Compute Manning")
        self._mn_run_btn.setStyleSheet(_RUN_STYLE)
        self._mn_run_btn.clicked.connect(self._run_manning)
        btn_row.addWidget(self._mn_run_btn)
        v.addLayout(btn_row)

        self._mn_progress = QProgressBar()
        self._mn_progress.setRange(0, 0)
        self._mn_progress.setVisible(False)
        v.addWidget(self._mn_progress)

        self._mn_status_lbl = QLabel("")
        self._mn_status_lbl.setWordWrap(True)
        self._mn_status_lbl.setStyleSheet(_BLUE_STYLE)
        self._mn_status_lbl.setVisible(False)
        v.addWidget(self._mn_status_lbl)

        self._mn_error_lbl = QLabel("")
        self._mn_error_lbl.setWordWrap(True)
        self._mn_error_lbl.setStyleSheet(_ERR_STYLE)
        self._mn_error_lbl.setVisible(False)
        v.addWidget(self._mn_error_lbl)

        self._mn_report_lbl = QLabel("")
        self._mn_report_lbl.setWordWrap(True)
        self._mn_report_lbl.setStyleSheet(_RPT_STYLE)
        self._mn_report_lbl.setVisible(False)
        v.addWidget(self._mn_report_lbl)

        # Results section
        self._mn_results_gb = QGroupBox("Per-AOI Manning outputs  —  click an AOI to preview")
        self._mn_results_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        rgl = QVBoxLayout(self._mn_results_gb)
        self._mn_results_inner = QVBoxLayout()
        self._mn_results_inner.setSpacing(0)
        rgl.addLayout(self._mn_results_inner)
        self._mn_results_gb.setVisible(False)
        v.addWidget(self._mn_results_gb)

        # 3-column preview
        self._mn_active_row = QWidget()
        mn_row_lay = QHBoxLayout(self._mn_active_row)
        mn_row_lay.setContentsMargins(0, 0, 0, 0)
        mn_row_lay.setSpacing(8)

        # Left: LULC table
        lulc_col = QVBoxLayout()
        lulc_hdr = QLabel("<b>LULC Statistics</b>")
        lulc_hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lulc_col.addWidget(lulc_hdr)
        self._mn_lulc_table = QTableWidget()
        self._mn_lulc_table.setColumnCount(4)
        self._mn_lulc_table.setHorizontalHeaderLabels(["Code", "Type", "Area km²", "% area"])
        self._mn_lulc_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self._mn_lulc_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._mn_lulc_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._mn_lulc_table.setAlternatingRowColors(True)
        self._mn_lulc_table.setStyleSheet(
            "QTableWidget { font-size:10px; } QTableWidget::item { padding:1px 4px; }"
        )
        lulc_col.addWidget(self._mn_lulc_table, 1)
        mn_row_lay.addLayout(lulc_col, 3)

        # Middle: LULC raster
        self._mn_lulc_canvas = RasterPreviewCanvas(self, width=5, height=3.5)
        mn_row_lay.addWidget(self._mn_lulc_canvas, 4)

        # Right: Manning raster
        self._mn_raster_preview = RasterPreviewCanvas(self, width=5, height=3.5)
        mn_row_lay.addWidget(self._mn_raster_preview, 4)

        self._mn_active_row.setVisible(False)
        v.addWidget(self._mn_active_row)

        v.addStretch()
        return page

    # ── Flowline page ──────────────────────────────────────────────────────────

    def _build_flowline_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(12)

        # Info banner
        info_banner = QLabel(
            "<b>ℹ️  NHD Auto-detect</b> downloads NHD flowlines, identifies the "
            "highest-order river, and derives upstream / downstream boundary points "
            "from DEM elevation.  USA only.  The DEM step must be completed first."
        )
        info_banner.setWordWrap(True)
        info_banner.setStyleSheet(
            "padding:10px; background:#fffff0; border:1px solid #d69e2e; "
            "border-radius:4px; font-size:12px;"
        )
        v.addWidget(info_banner)

        # Run button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._fl_run_btn = QPushButton("✔  Detect Main River")
        self._fl_run_btn.setStyleSheet(_RUN_STYLE)
        self._fl_run_btn.clicked.connect(self._run_flowline)
        btn_row.addWidget(self._fl_run_btn)
        v.addLayout(btn_row)

        self._fl_progress = QProgressBar()
        self._fl_progress.setRange(0, 0)
        self._fl_progress.setVisible(False)
        v.addWidget(self._fl_progress)

        self._fl_status_lbl = QLabel("")
        self._fl_status_lbl.setWordWrap(True)
        self._fl_status_lbl.setStyleSheet(_BLUE_STYLE)
        self._fl_status_lbl.setVisible(False)
        v.addWidget(self._fl_status_lbl)

        self._fl_error_lbl = QLabel("")
        self._fl_error_lbl.setWordWrap(True)
        self._fl_error_lbl.setStyleSheet(_ERR_STYLE)
        self._fl_error_lbl.setVisible(False)
        v.addWidget(self._fl_error_lbl)

        self._fl_report_lbl = QLabel("")
        self._fl_report_lbl.setWordWrap(True)
        self._fl_report_lbl.setStyleSheet(_RPT_STYLE)
        self._fl_report_lbl.setVisible(False)
        v.addWidget(self._fl_report_lbl)

        # Results
        self._fl_results_gb = QGroupBox("Per-AOI Flowline  —  click an AOI to preview")
        self._fl_results_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        rgl = QVBoxLayout(self._fl_results_gb)
        self._fl_results_inner = QVBoxLayout()
        self._fl_results_inner.setSpacing(2)
        rgl.addLayout(self._fl_results_inner)
        self._fl_results_gb.setVisible(False)
        v.addWidget(self._fl_results_gb)

        self._flowline_bci_preview = BCIPreviewCanvas(self, width=9, height=4.0)
        self._flowline_bci_preview.setVisible(False)
        v.addWidget(self._flowline_bci_preview)

        v.addStretch()
        return page

    # ── Flowdata page ──────────────────────────────────────────────────────────

    def _build_flowdata_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(12)

        title_lbl = QLabel("<b>Step 6 — Download Discharge Data</b>")
        title_lbl.setStyleSheet("font-size:14px;")
        v.addWidget(title_lbl)

        # Stacked: single-AOI (page 0) vs multi-AOI accordion (page 1)
        self._fd_stack = QStackedWidget()
        v.addWidget(self._fd_stack, 1)

        # Page 0 — single AOI inline form
        single_page = QWidget()
        sp_lay = QVBoxLayout(single_page)
        sp_lay.setContentsMargins(0, 0, 0, 0)
        fd_gb = QGroupBox("Flow Data Configuration")
        fd_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        gb_lay = QVBoxLayout(fd_gb)
        self._flowdata_single_panel = _SingleFlowdataPanel()
        gb_lay.addWidget(self._flowdata_single_panel)
        sp_lay.addWidget(fd_gb)
        sp_lay.addStretch()
        self._fd_stack.addWidget(single_page)

        # Page 1 — multi-AOI accordion
        multi_page = QWidget()
        mp_lay = QVBoxLayout(multi_page)
        mp_lay.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        cards_host = QWidget()
        self._fd_cards_layout = QVBoxLayout(cards_host)
        self._fd_cards_layout.setSpacing(6)
        self._fd_cards_layout.addStretch()
        scroll.setWidget(cards_host)
        mp_lay.addWidget(scroll, 1)
        self._fd_stack.addWidget(multi_page)

        self._fd_cards: List[AOIFlowdataCard] = []

        # Run button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._fd_run_btn = QPushButton("✔  Download Discharge Data")
        self._fd_run_btn.setStyleSheet(_RUN_STYLE)
        self._fd_run_btn.clicked.connect(self._run_flowdata)
        btn_row.addWidget(self._fd_run_btn)
        v.addLayout(btn_row)

        self._fd_progress = QProgressBar()
        self._fd_progress.setRange(0, 0)
        self._fd_progress.setVisible(False)
        v.addWidget(self._fd_progress)

        self._fd_status_lbl = QLabel("")
        self._fd_status_lbl.setWordWrap(True)
        self._fd_status_lbl.setStyleSheet(_BLUE_STYLE)
        self._fd_status_lbl.setVisible(False)
        v.addWidget(self._fd_status_lbl)

        self._fd_error_lbl = QLabel("")
        self._fd_error_lbl.setWordWrap(True)
        self._fd_error_lbl.setStyleSheet(_ERR_STYLE)
        self._fd_error_lbl.setVisible(False)
        v.addWidget(self._fd_error_lbl)

        self._fd_report_lbl = QLabel("")
        self._fd_report_lbl.setWordWrap(True)
        self._fd_report_lbl.setStyleSheet(_RPT_STYLE)
        self._fd_report_lbl.setVisible(False)
        v.addWidget(self._fd_report_lbl)

        # Results
        self._fd_results_gb = QGroupBox("Per-AOI Discharge outputs  —  click an AOI to preview")
        self._fd_results_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        rgl = QVBoxLayout(self._fd_results_gb)
        self._fd_results_inner = QVBoxLayout()
        self._fd_results_inner.setSpacing(2)
        rgl.addLayout(self._fd_results_inner)
        self._fd_results_gb.setVisible(False)
        v.addWidget(self._fd_results_gb)

        self._flowdata_hydro_preview = HydrographPreviewCanvas(self, width=9, height=4.0)
        self._flowdata_hydro_preview.setVisible(False)
        v.addWidget(self._flowdata_hydro_preview)

        v.addStretch()
        return page

    # ── Configuration helpers ──────────────────────────────────────────────────

    def _configure_dem_page(self, features):
        """Called after AOI step — nothing to reconfigure, just sets features."""
        pass  # DEM page reads features from self._features at run time

    def _configure_flowdata_page(self, features, flowline_summary: Optional[dict]):
        """Populate flowdata page with AOI cards and auto-fill NWM reach IDs."""
        if not features:
            return

        reach_by_name: dict = {}
        if flowline_summary:
            for fe in flowline_summary.get("flowline_per_aoi", []):
                rid = fe.get("upstream_reach_id")
                if rid is not None:
                    reach_by_name[fe["name"]] = rid

        n = len(features)
        if n == 1:
            self._fd_stack.setCurrentIndex(0)
            rid = reach_by_name.get(features[0].name)
            if rid is not None:
                self._flowdata_single_panel.set_config({
                    "flow_source": "nwm",
                    "feature_ids": str(rid),
                    "_reach_id_autofill": rid,
                })
        else:
            self._fd_stack.setCurrentIndex(1)
            # Rebuild cards
            while self._fd_cards_layout.count() > 1:
                item = self._fd_cards_layout.takeAt(0)
                w = item.widget()
                if w:
                    w.setParent(None)
            self._fd_cards.clear()

            for f in features:
                card = AOIFlowdataCard(f.name)
                rid = reach_by_name.get(f.name)
                if rid is not None:
                    card.set_config({
                        "flow_source": "nwm",
                        "feature_ids": str(rid),
                    })
                card.expand_requested.connect(self._on_fd_card_expand)
                self._fd_cards_layout.insertWidget(
                    self._fd_cards_layout.count() - 1, card
                )
                self._fd_cards.append(card)

            if self._fd_cards:
                self._fd_cards[0].expand()

    def _on_fd_card_expand(self, card: AOIFlowdataCard):
        for c in self._fd_cards:
            if c is not card:
                c.collapse()
        card.expand()

    # ── Slots: project / AOI ──────────────────────────────────────────────────

    def _on_project_done(self, data: dict):
        ctx = data.get("ctx", {})
        self._project_dir = ctx.get("project_dir")
        self._aoi.set_project_dir(self._project_dir)
        self._stack.setCurrentIndex(1)

    def _on_aoi_ready(self, features):
        self._features = features
        self._configure_dem_page(features)
        self._stack.setCurrentIndex(2)

    # ── Run: DEM ──────────────────────────────────────────────────────────────

    def _run_dem(self):
        if not self._features:
            self._dem_status_lbl.setText("No AOIs confirmed.")
            self._dem_status_lbl.setVisible(True)
            return

        set_running(self._dem_run_btn)
        self._dem_progress.setVisible(True)
        self._dem_status_lbl.setText("Downloading DEM…")
        self._dem_status_lbl.setVisible(True)
        self._dem_error_lbl.setVisible(False)
        self._dem_report_lbl.setVisible(False)

        kw = dict(
            project_dir=self._project_dir,
            features=self._features,
            dem_cell_size_m=self._dem_cell_spin.value(),
            dem_buffer_m=self._dem_buffer_spin.value(),
        )

        def _msg(m):
            self._log(m)
            self._dem_status_lbl.setText(m)

        self._worker = Worker(run_hecras_dem, **kw)
        self._worker.message.connect(_msg)
        self._worker.finished.connect(self._on_dem_done)
        self._worker.error.connect(self._on_dem_error)
        self._worker.start()

    def _on_dem_done(self, summary: dict):
        set_ready(self._dem_run_btn)
        self._dem_progress.setVisible(False)
        self._dem_summary = summary
        n = len(summary.get("dem_per_aoi", []))
        self._dem_status_lbl.setVisible(False)
        self._dem_report_lbl.setText(f"✅ DEM downloaded for {n} AOI(s).")
        self._dem_report_lbl.setVisible(True)
        self._build_dem_results(summary)

    def _on_dem_error(self, msg: str):
        set_ready(self._dem_run_btn)
        self._dem_progress.setVisible(False)
        self._dem_status_lbl.setVisible(False)
        self._log(f"DEM ERROR: {msg}")
        self._dem_error_lbl.setText(f"❌ {msg.splitlines()[0]}")
        self._dem_error_lbl.setVisible(True)

    def _build_dem_results(self, summary: dict):
        _clear_layout(self._dem_results_inner)
        entries = summary.get("dem_per_aoi", [])
        if not entries:
            return
        for e in entries:
            name     = e["name"]
            dem_path = e.get("dem_tif", "")
            row = _make_result_row(name, lambda _c, n=name, p=dem_path: self._show_dem_preview(n, p))
            self._dem_results_inner.addWidget(row)
        self._dem_results_gb.setVisible(True)
        self._dem_preview_gb.setVisible(True)

    def _show_dem_preview(self, name: str, path: str):
        if not path or not Path(path).exists():
            self._dem_preview_placeholder.setText(
                f"<span style='color:#c53030;'>DEM not found: {path}</span>"
            )
            self._dem_preview_placeholder.setVisible(True)
            self._dem_preview_2col.setVisible(False)
            return
        # Populate info table
        try:
            import rasterio as _rio
            with _rio.open(path) as ds:
                rows_data = [
                    ("AOI",         name),
                    ("CRS",         str(ds.crs)),
                    ("Size",        f"{ds.width} × {ds.height} px"),
                    ("Resolution",  f"{abs(ds.res[0]):.2f} m"),
                    ("Bounds W",    f"{ds.bounds.left:.4f}"),
                    ("Bounds S",    f"{ds.bounds.bottom:.4f}"),
                    ("Bounds E",    f"{ds.bounds.right:.4f}"),
                    ("Bounds N",    f"{ds.bounds.top:.4f}"),
                    ("Filename",    Path(path).name),
                ]
        except Exception:
            rows_data = [("AOI", name), ("Path", path)]
        self._dem_info_table.setRowCount(len(rows_data))
        for r, (k, val) in enumerate(rows_data):
            self._dem_info_table.setItem(r, 0, QTableWidgetItem(k))
            self._dem_info_table.setItem(r, 1, QTableWidgetItem(str(val)))
        self._dem_raster_preview.show_raster(
            path, title=f"DEM — {name}", cmap="terrain", colorbar_label="Elevation (m)"
        )
        self._dem_preview_placeholder.setVisible(False)
        self._dem_preview_2col.setVisible(True)

    # ── Run: Manning ──────────────────────────────────────────────────────────

    def _on_manning_source_changed(self):
        is_nlcd = self._mn_src_combo.currentData() == "nlcd"
        self._mn_nlcd_year.setVisible(is_nlcd)
        self._mn_s2_year_lbl.setVisible(not is_nlcd)
        self._mn_s2_year_spin.setVisible(not is_nlcd)
        self._mn_cell_spin.setValue(30.0 if is_nlcd else 10.0)
        self._manning_table.set_table_data(NLCD_MANNING if is_nlcd else SENTINEL2_MANNING)

    def _run_manning(self):
        if not self._features:
            self._mn_status_lbl.setText("No AOIs confirmed.")
            self._mn_status_lbl.setVisible(True)
            return

        set_running(self._mn_run_btn)
        self._mn_progress.setVisible(True)
        self._mn_status_lbl.setText("Downloading LULC and computing Manning…")
        self._mn_status_lbl.setVisible(True)
        self._mn_error_lbl.setVisible(False)
        self._mn_report_lbl.setVisible(False)

        is_nlcd = self._mn_src_combo.currentData() == "nlcd"
        kw = dict(
            project_dir=self._project_dir,
            features=self._features,
            lulc_source="nlcd" if is_nlcd else "sentinel2",
            lulc_cell_size_m=self._mn_cell_spin.value(),
            dem_buffer_m=self._dem_buffer_spin.value(),
            nlcd_year=self._mn_nlcd_year.currentText(),
            sentinel2_year=int(self._mn_s2_year_spin.value()),
            manning_mapping=self._manning_table.get_mapping(),
        )

        def _msg(m):
            self._log(m)
            self._mn_status_lbl.setText(m)

        self._worker = Worker(run_hecras_manning, **kw)
        self._worker.message.connect(_msg)
        self._worker.finished.connect(self._on_manning_done)
        self._worker.error.connect(self._on_manning_error)
        self._worker.start()

    def _on_manning_done(self, summary: dict):
        set_ready(self._mn_run_btn)
        self._mn_progress.setVisible(False)
        self._manning_summary = summary
        n = len(summary.get("manning_per_aoi", []))
        self._mn_status_lbl.setVisible(False)
        self._mn_report_lbl.setText(f"✅ Manning computed for {n} AOI(s).")
        self._mn_report_lbl.setVisible(True)
        self._build_manning_results(summary)

    def _on_manning_error(self, msg: str):
        set_ready(self._mn_run_btn)
        self._mn_progress.setVisible(False)
        self._mn_status_lbl.setVisible(False)
        self._log(f"Manning ERROR: {msg}")
        self._mn_error_lbl.setText(f"❌ {msg.splitlines()[0]}")
        self._mn_error_lbl.setVisible(True)

    def _build_manning_results(self, summary: dict):
        _clear_layout(self._mn_results_inner)
        entries = summary.get("manning_per_aoi", [])
        if not entries:
            return
        for e in entries:
            name = e["name"]
            row = _make_result_row(
                name,
                lambda _c, en=e: self._show_manning_preview(en)
            )
            self._mn_results_inner.addWidget(row)
        self._mn_results_gb.setVisible(True)

    def _show_manning_preview(self, entry: dict):
        lulc_tif    = entry.get("lulc_tif", "")
        manning_tif = entry.get("manning_tif", "")
        name        = entry.get("name", "")
        lulc_src    = entry.get("lulc_source", "nlcd")

        # Populate LULC table
        if lulc_tif and Path(lulc_tif).exists():
            try:
                from core.orchestrate import _compute_lulc_stats
                import rasterio as _rio
                stats = _compute_lulc_stats(Path(lulc_tif), lulc_src)
                with _rio.open(lulc_tif) as ds:
                    px_area = abs(ds.res[0] * ds.res[1]) / 1e6  # km²
                self._mn_lulc_table.setRowCount(len(stats))
                for r, st in enumerate(stats):
                    area_km2 = st["area_frac"] * px_area * ds.width * ds.height
                    pct      = st["area_frac"] * 100
                    self._mn_lulc_table.setItem(r, 0, QTableWidgetItem(str(st["code"])))
                    self._mn_lulc_table.setItem(r, 1, QTableWidgetItem(st["name"]))
                    self._mn_lulc_table.setItem(r, 2, QTableWidgetItem(f"{area_km2:.2f}"))
                    self._mn_lulc_table.setItem(r, 3, QTableWidgetItem(f"{pct:.1f}%"))
            except Exception:
                pass
            self._mn_lulc_canvas.show_raster(
                lulc_tif, title=f"LULC — {name}", cmap="tab20"
            )

        if manning_tif and Path(manning_tif).exists():
            self._mn_raster_preview.show_raster(
                manning_tif, title=f"Manning n — {name}", cmap="YlGn",
                colorbar_label="Manning n"
            )

        self._mn_active_row.setVisible(True)

    # ── Run: Flowline ─────────────────────────────────────────────────────────

    def _run_flowline(self):
        if not self._features:
            self._fl_status_lbl.setText("No AOIs confirmed.")
            self._fl_status_lbl.setVisible(True)
            return

        set_running(self._fl_run_btn)
        self._fl_progress.setVisible(True)
        self._fl_status_lbl.setText("Querying NHD flowlines…")
        self._fl_status_lbl.setVisible(True)
        self._fl_error_lbl.setVisible(False)
        self._fl_report_lbl.setVisible(False)

        kw = dict(
            project_dir=self._project_dir,
            features=self._features,
            dem_summary=self._dem_summary,
        )

        def _msg(m):
            self._log(m)
            self._fl_status_lbl.setText(m)

        self._worker = Worker(run_hecras_flowline, **kw)
        self._worker.message.connect(_msg)
        self._worker.finished.connect(self._on_flowline_done)
        self._worker.error.connect(self._on_flowline_error)
        self._worker.start()

    def _on_flowline_done(self, summary: dict):
        set_ready(self._fl_run_btn)
        self._fl_progress.setVisible(False)
        self._flowline_summary = summary
        n = len(summary.get("flowline_per_aoi", []))
        self._fl_status_lbl.setVisible(False)
        self._fl_report_lbl.setText(f"✅ Flowlines detected for {n} AOI(s).")
        self._fl_report_lbl.setVisible(True)
        self._build_flowline_results(summary)
        # Auto-populate flowdata page with detected reach IDs
        self._configure_flowdata_page(self._features, summary)

    def _on_flowline_error(self, msg: str):
        set_ready(self._fl_run_btn)
        self._fl_progress.setVisible(False)
        self._fl_status_lbl.setVisible(False)
        self._log(f"Flowline ERROR: {msg}")
        self._fl_error_lbl.setText(f"❌ {msg.splitlines()[0]}")
        self._fl_error_lbl.setVisible(True)

    def _build_flowline_results(self, summary: dict):
        _clear_layout(self._fl_results_inner)
        entries = summary.get("flowline_per_aoi", [])
        if not entries:
            return
        for e in entries:
            name        = e["name"]
            river       = e.get("river_name") or "—"
            reach_id    = e.get("upstream_reach_id")
            detail      = f"{river}"
            if reach_id:
                detail += f"  (reach ID: {reach_id})"
            row = _make_result_row(
                f"{name}  —  {detail}",
                lambda _c, en=e: self._show_flowline_preview(en)
            )
            self._fl_results_inner.addWidget(row)
        self._fl_results_gb.setVisible(True)
        self._flowline_bci_preview.setVisible(True)

    def _show_flowline_preview(self, entry: dict):
        try:
            self._flowline_bci_preview.show_bci(
                aoi_path=entry["source_file"],
                feature_index=entry["feature_index"],
                main_river_path=entry.get("main_river_line"),
                upstream_xy=entry.get("upstream_xy"),
                downstream_xy=entry.get("downstream_xy"),
                title=entry.get("river_name") or entry["name"],
            )
        except Exception as ex:
            self._log(f"Flowline preview error: {ex}")

    # ── Run: Flowdata ─────────────────────────────────────────────────────────

    def _run_flowdata(self):
        if not self._features:
            self._fd_status_lbl.setText("No AOIs confirmed.")
            self._fd_status_lbl.setVisible(True)
            return

        # Collect per-AOI configs
        n = len(self._features)
        if n == 1:
            per_aoi_configs = [self._flowdata_single_panel.get_config()]
        else:
            per_aoi_configs = [
                card.get_config() for card in self._fd_cards
            ] if self._fd_cards else [{}] * n

        set_running(self._fd_run_btn)
        self._fd_progress.setVisible(True)
        self._fd_status_lbl.setText("Downloading discharge data…")
        self._fd_status_lbl.setVisible(True)
        self._fd_error_lbl.setVisible(False)
        self._fd_report_lbl.setVisible(False)

        kw = dict(
            project_dir=self._project_dir,
            features=self._features,
            per_aoi_configs=per_aoi_configs,
            flowline_summary=self._flowline_summary,
        )

        def _msg(m):
            self._log(m)
            self._fd_status_lbl.setText(m)

        self._worker = Worker(run_hecras_flowdata, **kw)
        self._worker.message.connect(_msg)
        self._worker.finished.connect(self._on_flowdata_done)
        self._worker.error.connect(self._on_flowdata_error)
        self._worker.start()

    def _on_flowdata_done(self, summary: dict):
        set_ready(self._fd_run_btn)
        self._fd_progress.setVisible(False)
        self._flowdata_summary = summary
        n = len(summary.get("flowdata_per_aoi", []))
        self._fd_status_lbl.setVisible(False)
        self._fd_report_lbl.setText(f"✅ Discharge data downloaded for {n} AOI(s).")
        self._fd_report_lbl.setVisible(True)
        self._build_flowdata_results(summary)

    def _on_flowdata_error(self, msg: str):
        set_ready(self._fd_run_btn)
        self._fd_progress.setVisible(False)
        self._fd_status_lbl.setVisible(False)
        self._log(f"Flowdata ERROR: {msg}")
        self._fd_error_lbl.setText(f"❌ {msg.splitlines()[0]}")
        self._fd_error_lbl.setVisible(True)

    def _build_flowdata_results(self, summary: dict):
        _clear_layout(self._fd_results_inner)
        entries = summary.get("flowdata_per_aoi", [])
        if not entries:
            return
        for e in entries:
            name     = e["name"]
            csv_path = e.get("csv_path", "")
            src      = e.get("flow_source", "")
            detail   = f"{src.upper()}" if src else ""
            if csv_path:
                detail += f"  →  {Path(csv_path).name}"
            row = _make_result_row(
                f"{name}  —  {detail}",
                lambda _c, n=name, p=csv_path: self._show_flowdata_preview(n, p)
            )
            self._fd_results_inner.addWidget(row)
        self._fd_results_gb.setVisible(True)
        self._flowdata_hydro_preview.setVisible(True)

    def _show_flowdata_preview(self, name: str, csv_path: str):
        if not csv_path or not Path(csv_path).exists():
            return
        self._flowdata_hydro_preview.show_hydrograph(
            csv_path, title=f"Discharge — {name}"
        )

    # ── Reset ──────────────────────────────────────────────────────────────────

    def reset(self):
        self._project_dir = None
        self._features = []
        self._dem_summary = None
        self._manning_summary = None
        self._flowline_summary = None
        self._flowdata_summary = None

        if hasattr(self._proj, "reset"):
            self._proj.reset()
        self._aoi.reset()

        # Reset run buttons
        for btn in (self._dem_run_btn, self._mn_run_btn,
                    self._fl_run_btn, self._fd_run_btn):
            try:
                set_ready(btn)
            except Exception:
                pass

        # Hide progress / status / error / report
        for w in (self._dem_progress, self._dem_status_lbl, self._dem_error_lbl,
                  self._dem_report_lbl, self._mn_progress, self._mn_status_lbl,
                  self._mn_error_lbl, self._mn_report_lbl, self._fl_progress,
                  self._fl_status_lbl, self._fl_error_lbl, self._fl_report_lbl,
                  self._fd_progress, self._fd_status_lbl, self._fd_error_lbl,
                  self._fd_report_lbl):
            w.setText("") if hasattr(w, "setText") else None
            w.setVisible(False)

        # Clear result rows
        for inner in (self._dem_results_inner, self._mn_results_inner,
                      self._fl_results_inner, self._fd_results_inner):
            _clear_layout(inner)

        # Hide result group boxes
        for gb in (self._dem_results_gb, self._dem_preview_gb,
                   self._mn_results_gb, self._fl_results_gb,
                   self._fd_results_gb):
            gb.setVisible(False)

        self._dem_preview_2col.setVisible(False)
        self._dem_preview_placeholder.setVisible(True)
        self._mn_active_row.setVisible(False)

        try:
            self._flowline_bci_preview.clear()
            self._flowline_bci_preview.setVisible(False)
        except Exception:
            pass

        try:
            self._flowdata_hydro_preview.clear()
            self._flowdata_hydro_preview.setVisible(False)
        except Exception:
            pass

        # Clear flowdata cards
        while self._fd_cards_layout.count() > 1:
            item = self._fd_cards_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
        self._fd_cards.clear()

        self._stack.setCurrentIndex(0)


# ── Utility functions ──────────────────────────────────────────────────────────

def _clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w:
            w.setParent(None)


def _make_result_row(label: str, on_click) -> QFrame:
    row = QFrame()
    row.setStyleSheet(_ROW_STYLE)
    rl = QHBoxLayout(row)
    rl.setContentsMargins(6, 2, 6, 2)
    btn = QPushButton(label)
    btn.setStyleSheet(_BTN_STYLE)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.clicked.connect(on_click)
    rl.addWidget(btn, 1)
    return row
