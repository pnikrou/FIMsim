"""HEC-RAS preparation mode — QTabWidget layout (all tabs always enabled).

Tabs:
  0  Project
  1  AOI
  2  DEM
  3  LULC & Manning
  4  Flowline
  5  Flowdata
"""
import re
import sys
from pathlib import Path
from typing import List, Optional

try:
    import h5py  # noqa: F401
except ImportError:
    h5py = None  # app still opens; mesh/run steps show a clear install message

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QGroupBox,
    QFormLayout, QComboBox, QDoubleSpinBox, QScrollArea, QTextEdit,
    QFrame, QProgressBar, QTabWidget, QLineEdit, QFileDialog,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView,
    QSizePolicy, QDateTimeEdit,
)
from PyQt6.QtCore import pyqtSignal, Qt, QDateTime
from PyQt6.QtGui import QColor, QFont as _QFont

from gui.step_project import StepProjectWidget
from gui.multi_aoi_widget import MultiAOIWidget
from gui.aoi_lulc_card import AOILulcCard
from gui.aoi_dem_card import AOIDEMCard
from gui.aoi_flowline_card import AOIFlowlineCard
from gui.aoi_flowdata_card import AOIFlowdataCard as _AOIFlowdataCard
from gui.raster_preview import RasterPreviewCanvas
from gui.flowline_preview import FlowlinePreviewCanvas
from gui.hydrograph_preview import HydrographPreviewCanvas
from gui.run_button import set_running, set_ready
from gui.worker import Worker
from core.orchestrate import (
    run_hecras_dem, run_hecras_manning, run_hecras_flowline, run_hecras_flowdata,
)
from core.multi_aoi import AOIFeatureInfo


# ── coverage check helper ─────────────────────────────────────────────────────

def _check_dem_coverage(dem_path, aoi_source_file, feature_index):
    """Return (bool, message) indicating whether dem_path covers the AOI."""
    try:
        import rasterio
        import geopandas as gpd
        from rasterio.crs import CRS

        aoi = gpd.read_file(aoi_source_file)
        if feature_index is not None and len(aoi) > 1:
            aoi = aoi.iloc[[feature_index]]
        aoi_4326 = aoi.to_crs("EPSG:4326")
        aoi_bounds = aoi_4326.total_bounds  # [minx, miny, maxx, maxy]

        with rasterio.open(dem_path) as src:
            dem_crs = src.crs
            dem_bounds = src.bounds

        from pyproj import Transformer
        tf = Transformer.from_crs(dem_crs, "EPSG:4326", always_xy=True)
        dem_minx, dem_miny = tf.transform(dem_bounds.left, dem_bounds.bottom)
        dem_maxx, dem_maxy = tf.transform(dem_bounds.right, dem_bounds.top)

        covers = (
            dem_minx <= aoi_bounds[0] and dem_maxx >= aoi_bounds[2]
            and dem_miny <= aoi_bounds[1] and dem_maxy >= aoi_bounds[3]
        )
        if covers:
            return True, "DEM covers the AOI."
        else:
            return False, "DEM does not fully cover the AOI extent."
    except Exception as ex:
        return False, f"Coverage check failed: {ex}"


# ── Per-AOI Flowdata accordion card ──────────────────────────────────────────

class _HECRASFlowdataCard(QFrame):
    """Accordion card for one AOI's flowdata configuration."""

    expand_requested = pyqtSignal(object)

    _EXPANDED = (
        "QFrame#card { background:#f9fafb; border:2px solid #a0aec0; "
        "border-radius:6px; padding:6px; }"
    )
    _COLLAPSED = (
        "QFrame#card { background:#f9fafb; border:1px solid #e2e8f0; "
        "border-radius:6px; padding:4px; }"
    )

    def __init__(self, aoi_name: str, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self._aoi_name = aoi_name
        self._expanded = False
        self._build_ui()
        self.setStyleSheet(self._COLLAPSED)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 2, 4, 2)
        outer.setSpacing(4)

        # Header
        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        self._caret = QLabel("▶")
        self._caret.setFixedWidth(14)
        self._caret.setStyleSheet("color:#4a5568; font-weight:bold;")
        hdr.addWidget(self._caret)
        self._name_lbl = QLabel(f"<b>{self._aoi_name}</b>")
        self._name_lbl.setStyleSheet("color:#2d3748;")
        hdr.addWidget(self._name_lbl)
        hdr.addStretch()
        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet("color:#718096; font-size:11px;")
        hdr.addWidget(self._status_lbl)
        self._toggle_btn = QPushButton("Edit")
        self._toggle_btn.setFixedWidth(60)
        self._toggle_btn.clicked.connect(self._toggle)
        hdr.addWidget(self._toggle_btn)
        outer.addLayout(hdr)

        # Config panel
        self._panel = QWidget()
        pf = QFormLayout(self._panel)
        pf.setContentsMargins(18, 4, 4, 4)
        pf.setVerticalSpacing(6)

        self._src_combo = QComboBox()
        self._src_combo.addItem("NWM Retrospective", "nwm_retro")
        self._src_combo.addItem("NWM Forecast", "nwm_forecast")
        self._src_combo.addItem("USGS Gage", "usgs")
        pf.addRow("Source:", self._src_combo)

        # NWM feature ID row
        self._nwm_widget = QWidget()
        nwm_row = QVBoxLayout(self._nwm_widget)
        nwm_row.setContentsMargins(0, 0, 0, 0)
        nwm_row.setSpacing(2)
        self._nwm_id_edit = QLineEdit()
        self._nwm_id_edit.setPlaceholderText("e.g. 22164566")
        nwm_row.addWidget(self._nwm_id_edit)
        nwm_auto_note = QLabel(
            "<small><i>Auto-detected from flowline step — edit if needed.</i></small>"
        )
        nwm_auto_note.setStyleSheet("color:#718096;")
        nwm_row.addWidget(nwm_auto_note)
        self._nwm_lbl = QLabel("NWM Feature ID (COMID):")
        pf.addRow(self._nwm_lbl, self._nwm_widget)

        # USGS gage row
        self._usgs_widget = QWidget()
        usgs_row = QVBoxLayout(self._usgs_widget)
        usgs_row.setContentsMargins(0, 0, 0, 0)
        usgs_row.setSpacing(2)
        self._usgs_id_edit = QLineEdit()
        self._usgs_id_edit.setPlaceholderText("e.g. 02082770")
        usgs_row.addWidget(self._usgs_id_edit)
        usgs_auto_note = QLabel(
            "<small><i>Auto-detected from flowline step — edit if needed.</i></small>"
        )
        usgs_auto_note.setStyleSheet("color:#718096;")
        usgs_row.addWidget(usgs_auto_note)
        self._usgs_lbl = QLabel("USGS Gage number:")
        pf.addRow(self._usgs_lbl, self._usgs_widget)

        # Date range
        self._start_dt = QDateTimeEdit()
        self._start_dt.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._start_dt.setCalendarPopup(True)
        self._start_dt.setDateTime(
            QDateTime.fromString("2026-05-01 00:00", "yyyy-MM-dd HH:mm")
        )
        pf.addRow("Start date:", self._start_dt)

        self._end_dt = QDateTimeEdit()
        self._end_dt.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._end_dt.setCalendarPopup(True)
        self._end_dt.setDateTime(
            QDateTime.fromString("2026-05-31 23:00", "yyyy-MM-dd HH:mm")
        )
        pf.addRow("End date:", self._end_dt)

        self._interval_combo = QComboBox()
        self._interval_combo.addItems(["0.5h", "1h", "3h", "6h", "12h", "24h"])
        self._interval_combo.setCurrentText("1h")
        pf.addRow("Interval:", self._interval_combo)

        self._panel.setVisible(False)
        outer.addWidget(self._panel)

        self._src_combo.currentIndexChanged.connect(self._on_src_changed)
        self._src_combo.currentIndexChanged.connect(self._refresh_status)
        self._nwm_id_edit.textChanged.connect(self._refresh_status)
        self._usgs_id_edit.textChanged.connect(self._refresh_status)
        self._start_dt.dateTimeChanged.connect(self._refresh_status)
        self._end_dt.dateTimeChanged.connect(self._refresh_status)
        self._on_src_changed()
        self._refresh_status()

    def _on_src_changed(self):
        src = self._src_combo.currentData()
        show_nwm = src in ("nwm_retro", "nwm_forecast")
        show_usgs = src == "usgs"
        self._nwm_lbl.setVisible(show_nwm)
        self._nwm_widget.setVisible(show_nwm)
        self._usgs_lbl.setVisible(show_usgs)
        self._usgs_widget.setVisible(show_usgs)

    def _toggle(self):
        if self._expanded:
            self.collapse()
        else:
            self.expand_requested.emit(self)

    def expand(self):
        if self._expanded:
            return
        self._expanded = True
        self._panel.setVisible(True)
        self._caret.setText("▼")
        self._toggle_btn.setText("Done")
        self.setStyleSheet(self._EXPANDED)

    def collapse(self):
        if not self._expanded:
            return
        self._expanded = False
        self._panel.setVisible(False)
        self._caret.setText("▶")
        self._toggle_btn.setText("Edit")
        self.setStyleSheet(self._COLLAPSED)

    def _refresh_status(self):
        src = self._src_combo.currentData()
        src_label = {
            "nwm_retro": "NWM Retro",
            "nwm_forecast": "NWM Forecast",
            "usgs": "USGS",
        }.get(src, str(src))
        if src in ("nwm_retro", "nwm_forecast"):
            fid = self._nwm_id_edit.text().strip() or "—"
        else:
            fid = self._usgs_id_edit.text().strip() or "—"
        start = self._start_dt.dateTime().toString("yyyy-MM-dd")
        end = self._end_dt.dateTime().toString("yyyy-MM-dd")
        self._status_lbl.setText(f"{src_label}  ·  ID:{fid}  ·  {start}→{end}")

    def is_expanded(self) -> bool:
        return self._expanded

    def get_config(self) -> dict:
        src = self._src_combo.currentData()
        ivl = self._parse_interval(self._interval_combo.currentText())
        return {
            "flow_source":         src,
            "feature_ids":         self._nwm_id_edit.text().strip(),
            "gage_ids":            self._usgs_id_edit.text().strip(),
            "event_start_dt":      self._start_dt.dateTime().toPyDateTime(),
            "event_end_dt":        self._end_dt.dateTime().toPyDateTime(),
            "interval_hours":      ivl,
            "usgs_interval_hours": ivl,
        }

    def set_config(self, cfg: dict):
        src_idx = self._src_combo.findData(cfg.get("flow_source", "nwm_retro"))
        if src_idx >= 0:
            self._src_combo.setCurrentIndex(src_idx)
        self._nwm_id_edit.setText(cfg.get("feature_ids", "") or "")
        self._usgs_id_edit.setText(cfg.get("gage_ids", "") or "")
        if cfg.get("event_start_dt"):
            try:
                import datetime
                dt = cfg["event_start_dt"]
                if isinstance(dt, datetime.datetime):
                    self._start_dt.setDateTime(
                        QDateTime.fromString(
                            dt.strftime("%Y-%m-%d %H:%M"), "yyyy-MM-dd HH:mm"
                        )
                    )
            except Exception:
                pass
        if cfg.get("event_end_dt"):
            try:
                import datetime
                dt = cfg["event_end_dt"]
                if isinstance(dt, datetime.datetime):
                    self._end_dt.setDateTime(
                        QDateTime.fromString(
                            dt.strftime("%Y-%m-%d %H:%M"), "yyyy-MM-dd HH:mm"
                        )
                    )
            except Exception:
                pass
        self._refresh_status()

    def set_upstream_id(self, feature_id: str):
        self._nwm_id_edit.setText(str(feature_id))

    def set_usgs_gage(self, gage_id: str):
        self._usgs_id_edit.setText(str(gage_id))

    @staticmethod
    def _parse_interval(text: str) -> float:
        text = text.strip().lower()
        if text.endswith("h"):
            return float(text[:-1])
        if text.endswith("min"):
            return float(text[:-3]) / 60.0
        return 1.0


# ── Per-AOI Flowline card (HEC-RAS) — SHP only, two options ──────────────────

class _HECRASFlowlineCard(QFrame):
    """Simplified flowline card for HEC-RAS: main river / all flowlines, SHP fixed."""

    expand_requested = pyqtSignal(object)

    _EXPANDED  = ("QFrame#card { background:#f9fafb; border:2px solid #a0aec0; "
                  "border-radius:6px; padding:8px; }")
    _COLLAPSED = ("QFrame#card { background:#f9fafb; border:1px solid #e2e8f0; "
                  "border-radius:6px; padding:6px; }")

    def __init__(self, aoi_name: str, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self._aoi_name = aoi_name
        self._expanded = False
        self._build_ui()
        self.setStyleSheet(self._COLLAPSED)
        self._refresh_status()

    def _build_ui(self):
        from PyQt6.QtWidgets import QCheckBox
        outer = QVBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 2)
        outer.setSpacing(6)

        # Header
        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        self._caret = QLabel("▶")
        self._caret.setFixedWidth(14)
        self._caret.setStyleSheet("color:#4a5568; font-weight:bold;")
        hdr.addWidget(self._caret)
        self._name_lbl = QLabel(f"<b>{self._aoi_name}</b>")
        self._name_lbl.setStyleSheet("color:#2d3748;")
        hdr.addWidget(self._name_lbl)
        hdr.addStretch()
        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet("color:#666; font-size:11px;")
        hdr.addWidget(self._status_lbl)
        self._toggle_btn = QPushButton("Edit")
        self._toggle_btn.setFixedWidth(80)
        self._toggle_btn.clicked.connect(self._on_toggle)
        hdr.addWidget(self._toggle_btn)
        outer.addLayout(hdr)

        # Panel
        self._panel = QWidget()
        pl = QVBoxLayout(self._panel)
        pl.setContentsMargins(18, 4, 4, 4)
        pl.setSpacing(8)

        self._chk_main = QCheckBox("Main river  (highest stream order)  →  SHP")
        self._chk_main.setChecked(True)
        pl.addWidget(self._chk_main)

        self._chk_all = QCheckBox("All flowlines  (full NHD reach set)  →  SHP")
        self._chk_all.setChecked(False)
        pl.addWidget(self._chk_all)

        self._panel.setVisible(False)
        outer.addWidget(self._panel)

        self._chk_main.toggled.connect(self._refresh_status)
        self._chk_all.toggled.connect(self._refresh_status)

    def _on_toggle(self):
        if self._expanded:
            self.collapse()
        else:
            self.expand_requested.emit(self)

    def expand(self):
        if self._expanded:
            return
        self._expanded = True
        self._panel.setVisible(True)
        self._caret.setText("▼")
        self._toggle_btn.setText("Done")
        self.setStyleSheet(self._EXPANDED)

    def collapse(self):
        if not self._expanded:
            return
        self._expanded = False
        self._panel.setVisible(False)
        self._caret.setText("▶")
        self._toggle_btn.setText("Edit")
        self.setStyleSheet(self._COLLAPSED)
        self._refresh_status()

    def is_expanded(self) -> bool:
        return self._expanded

    def _refresh_status(self, *_):
        parts = []
        if self._chk_main.isChecked():
            parts.append("Main river")
        if self._chk_all.isChecked():
            parts.append("All flowlines")
        self._status_lbl.setText(
            "  ·  ".join(parts) if parts else "<i>nothing selected</i>"
        )

    def get_config(self) -> dict:
        return {
            "save_main_river":    self._chk_main.isChecked(),
            "main_format":        "shp",
            "save_all_flowlines": self._chk_all.isChecked(),
            "all_format":         "shp",
            "save_gages_csv":     True,
        }

    def set_config(self, cfg: dict):
        self._chk_main.setChecked(cfg.get("save_main_river", True))
        self._chk_all.setChecked(cfg.get("save_all_flowlines", False))
        self._refresh_status()


# ── Per-AOI Mesh accordion card ───────────────────────────────────────────────

class _HECRASMeshCard(QFrame):
    """Accordion card for one AOI's mesh options."""

    expand_requested = pyqtSignal(object)

    _EXPANDED = (
        "QFrame#card { background:#f9fafb; border:2px solid #a0aec0; "
        "border-radius:6px; padding:6px; }"
    )
    _COLLAPSED = (
        "QFrame#card { background:#f9fafb; border:1px solid #e2e8f0; "
        "border-radius:6px; padding:4px; }"
    )

    def __init__(self, aoi_name: str, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self._aoi_name = aoi_name
        self._expanded = False
        self._build_ui()
        self.setStyleSheet(self._COLLAPSED)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 2, 4, 2)
        outer.setSpacing(4)

        # Header
        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        self._caret = QLabel("▶")
        self._caret.setFixedWidth(14)
        self._caret.setStyleSheet("color:#4a5568; font-weight:bold;")
        hdr.addWidget(self._caret)
        self._name_lbl = QLabel(f"<b>{self._aoi_name}</b>")
        self._name_lbl.setStyleSheet("color:#2d3748;")
        hdr.addWidget(self._name_lbl)
        hdr.addStretch()
        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet("color:#718096; font-size:11px;")
        hdr.addWidget(self._status_lbl)
        self._toggle_btn = QPushButton("Edit")
        self._toggle_btn.setFixedWidth(60)
        self._toggle_btn.clicked.connect(self._toggle)
        hdr.addWidget(self._toggle_btn)
        outer.addLayout(hdr)

        # Config panel
        self._panel = QWidget()
        pf = QFormLayout(self._panel)
        pf.setContentsMargins(18, 4, 4, 4)
        pf.setVerticalSpacing(6)

        self._near_spin = QDoubleSpinBox()
        self._near_spin.setRange(1.0, 1000.0)
        self._near_spin.setDecimals(1)
        self._near_spin.setValue(10.0)
        self._near_spin.setSuffix(" m")
        pf.addRow("Cell size near channel (m):", self._near_spin)

        self._far_spin = QDoubleSpinBox()
        self._far_spin.setRange(1.0, 5000.0)
        self._far_spin.setDecimals(1)
        self._far_spin.setValue(100.0)
        self._far_spin.setSuffix(" m")
        pf.addRow("Cell size in floodplain (m):", self._far_spin)

        self._buf_spin = QDoubleSpinBox()
        self._buf_spin.setRange(10.0, 5000.0)
        self._buf_spin.setDecimals(1)
        self._buf_spin.setValue(150.0)
        self._buf_spin.setSuffix(" m")
        pf.addRow("Refinement buffer (m):", self._buf_spin)

        self._panel.setVisible(False)
        outer.addWidget(self._panel)

        for spin in (self._near_spin, self._far_spin, self._buf_spin):
            spin.valueChanged.connect(self._refresh_status)
        self._refresh_status()

    def _toggle(self):
        if self._expanded:
            self.collapse()
        else:
            self.expand_requested.emit(self)

    def expand(self):
        if self._expanded:
            return
        self._expanded = True
        self._panel.setVisible(True)
        self._caret.setText("▼")
        self._toggle_btn.setText("Done")
        self.setStyleSheet(self._EXPANDED)

    def collapse(self):
        if not self._expanded:
            return
        self._expanded = False
        self._panel.setVisible(False)
        self._caret.setText("▶")
        self._toggle_btn.setText("Edit")
        self.setStyleSheet(self._COLLAPSED)

    def _refresh_status(self):
        near = self._near_spin.value()
        far = self._far_spin.value()
        buf = self._buf_spin.value()
        self._status_lbl.setText(
            f"Near: {near:.0f} m  ·  Far: {far:.0f} m  ·  Buffer: {buf:.0f} m"
        )

    def is_expanded(self) -> bool:
        return self._expanded

    def get_config(self) -> dict:
        return {
            "cell_size_near": float(self._near_spin.value()),
            "cell_size_far":  float(self._far_spin.value()),
            "refine_buffer_m": float(self._buf_spin.value()),
        }

    def set_config(self, cfg: dict):
        try:
            self._near_spin.setValue(float(cfg.get("cell_size_near", 10.0)))
        except Exception:
            pass
        try:
            self._far_spin.setValue(float(cfg.get("cell_size_far", 100.0)))
        except Exception:
            pass
        try:
            self._buf_spin.setValue(float(cfg.get("refine_buffer_m", 150.0)))
        except Exception:
            pass
        self._refresh_status()


# ── Main widget ───────────────────────────────────────────────────────────────

class ModeHECRASWidget(QWidget):
    """HEC-RAS preparation mode — QTabWidget with 6 always-enabled tabs."""

    mode_finished = pyqtSignal()
    nav_changed   = pyqtSignal(int, int)

    def __init__(self, log_fn, parent=None):
        super().__init__(parent)
        self._log = log_fn

        # State
        self._project_dir: Optional[str] = None
        self._features: List[AOIFeatureInfo] = []
        self._dem_summary: Optional[dict] = None
        self._manning_summary: Optional[dict] = None
        self._flowline_summary: Optional[dict] = None
        self._flowdata_summary: Optional[dict] = None
        self._mesh_summary: Optional[dict] = None
        self._model_summary: Optional[dict] = None
        self._dem_cards: List = []   # AOIDEMCard instances
        self._lulc_cards: List[AOILulcCard] = []
        self._flowline_cards: List = []   # _HECRASFlowlineCard instances
        self._flowdata_cards: List[_AOIFlowdataCard] = []
        self._mesh_cards: List[_HECRASMeshCard] = []

        # Workers (kept as references to avoid GC)
        self._dem_worker: Optional[Worker] = None
        self._manning_worker: Optional[Worker] = None
        self._flowline_worker: Optional[Worker] = None
        self._flowdata_worker: Optional[Worker] = None
        self._fl_workers: list = []

        self._setup_ui()

    # ── UI setup ──────────────────────────────────────────────────────────────

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setSpacing(4)
        outer.setContentsMargins(4, 4, 4, 4)

        self._tabs = QTabWidget()
        self._tabs.currentChanged.connect(self._on_tab_changed)

        # Tab 0: Project
        self._proj = StepProjectWidget(self._log, model="generic")
        self._proj.step_completed.connect(self._on_project_done)
        self._tabs.addTab(self._wrap(self._proj), "1. Project")

        # Tab 1: AOI
        self._aoi = MultiAOIWidget(self._log)
        self._aoi.aoi_ready.connect(self._on_aoi_ready)
        self._tabs.addTab(self._wrap(self._aoi), "2. AOI")

        # Tab 2: DEM
        self._dem_tab_widget = self._build_dem_tab()
        self._tabs.addTab(self._wrap(self._dem_tab_widget), "3. DEM")

        # Tab 3: LULC & Manning
        self._lulc_tab_widget = self._build_lulc_tab()
        self._tabs.addTab(self._wrap(self._lulc_tab_widget), "4. LULC & Manning")

        # Tab 4: Flowline
        self._flowline_tab_widget = self._build_flowline_tab()
        self._tabs.addTab(self._wrap(self._flowline_tab_widget), "5. Flowline")

        # Tab 5: Flowdata
        self._flowdata_tab_widget = self._build_flowdata_tab()
        self._tabs.addTab(self._wrap(self._flowdata_tab_widget), "6. Flowdata")

        # Tab 6: Build Mesh
        self._mesh_tab_widget = self._build_mesh_tab()
        self._tabs.addTab(self._wrap(self._mesh_tab_widget), "7. Build Mesh")

        # Tab 7: Build & Run
        self._run_tab_widget = self._build_run_tab()
        self._tabs.addTab(self._wrap(self._run_tab_widget), "8. Build & Run")

        outer.addWidget(self._tabs, 1)

        # Bottom nav
        bot = QHBoxLayout()
        back_btn = QPushButton("◀  Back to main page")
        back_btn.setFixedWidth(180)
        back_btn.clicked.connect(self.mode_finished.emit)
        bot.addWidget(back_btn)
        bot.addStretch()
        outer.addLayout(bot)

    def _wrap(self, w: QWidget) -> QScrollArea:
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setWidget(w)
        return sa

    def _on_tab_changed(self, idx: int):
        self.nav_changed.emit(idx, self._tabs.count())

    def go_prev(self):
        cur = self._tabs.currentIndex()
        if cur > 0:
            self._tabs.setCurrentIndex(cur - 1)

    def go_next(self):
        cur = self._tabs.currentIndex()
        if cur == 1:
            # AOI tab — commit confirmed AOIs; _on_aoi_ready will advance to tab 2
            self._aoi.proceed_to_next()
            return
        if cur < self._tabs.count() - 1:
            self._tabs.setCurrentIndex(cur + 1)

    # ── Tab 2: DEM ─────────────────────────────────────────────────────────────

    def _build_dem_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(10)
        v.setContentsMargins(10, 10, 10, 10)

        # Card list groupbox
        self._dem_cards_gb = QGroupBox("DEM options per AOI")
        self._dem_cards_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        gb_v = QVBoxLayout(self._dem_cards_gb)
        gb_v.setSpacing(6)
        gb_v.setContentsMargins(6, 6, 6, 6)

        apply_row = QHBoxLayout()
        self._dem_apply_all_btn = QPushButton("Apply current AOI's settings to all")
        self._dem_apply_all_btn.setStyleSheet(
            "background:#4a5568; color:white; padding:5px 12px; "
            "border-radius:3px; font-weight:bold;"
        )
        self._dem_apply_all_btn.clicked.connect(self._dem_apply_to_all)
        self._dem_apply_all_btn.setEnabled(False)
        apply_row.addStretch()
        apply_row.addWidget(self._dem_apply_all_btn)
        gb_v.addLayout(apply_row)

        self._dem_cards_layout = QVBoxLayout()
        self._dem_cards_layout.setSpacing(4)
        self._dem_cards_layout.setContentsMargins(0, 0, 0, 0)
        gb_v.addLayout(self._dem_cards_layout)
        v.addWidget(self._dem_cards_gb)

        # Run button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._dem_run_btn = QPushButton("Download / Prepare DEM for all AOIs")
        self._dem_run_btn.setStyleSheet(
            "font-weight:bold; padding:7px 20px; background:#2b6cb0; "
            "color:white; border-radius:4px;"
        )
        self._dem_run_btn.clicked.connect(self._dem_run)
        btn_row.addWidget(self._dem_run_btn)
        v.addLayout(btn_row)

        # Progress + status
        self._dem_progress = QProgressBar()
        self._dem_progress.setRange(0, 100)
        self._dem_progress.setValue(0)
        self._dem_progress.setStyleSheet("QProgressBar { height:18px; }")
        self._dem_progress.setVisible(False)
        v.addWidget(self._dem_progress)

        self._dem_status_lbl = QLabel("")
        self._dem_status_lbl.setWordWrap(True)
        self._dem_status_lbl.setStyleSheet("color:#2d3748; font-size:12px; padding:2px 0px;")
        self._dem_status_lbl.setVisible(False)
        v.addWidget(self._dem_status_lbl)

        # Results
        self._dem_results_gb = QGroupBox("")
        self._dem_results_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        rgl = QVBoxLayout(self._dem_results_gb)
        self._dem_results_inner = QVBoxLayout()
        self._dem_results_inner.setSpacing(0)
        rgl.addLayout(self._dem_results_inner)
        self._dem_results_gb.setVisible(False)
        v.addWidget(self._dem_results_gb)

        # Preview
        self._dem_preview_gb = QGroupBox("DEM preview")
        self._dem_preview_gb.setMinimumHeight(400)
        pv = QVBoxLayout(self._dem_preview_gb)

        self._dem_preview_placeholder = QLabel(
            "<i>Click an AOI above to preview its DEM here.</i>"
        )
        self._dem_preview_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._dem_preview_placeholder.setStyleSheet(
            "color:#888; padding:30px; background:#fafafa; "
            "border:1px dashed #cbd5e0; border-radius:4px;"
        )
        pv.addWidget(self._dem_preview_placeholder)

        self._dem_preview_2col = QWidget()
        h2 = QHBoxLayout(self._dem_preview_2col)
        h2.setContentsMargins(0, 0, 0, 0)
        h2.setSpacing(10)
        self._dem_raster_preview = RasterPreviewCanvas(self, width=9, height=3.8)
        h2.addWidget(self._dem_raster_preview)
        self._dem_preview_2col.setVisible(False)
        pv.addWidget(self._dem_preview_2col, 1)

        self._dem_preview_gb.setVisible(False)
        v.addWidget(self._dem_preview_gb)
        v.addStretch()
        return page

    # ── Tab 3: LULC & Manning ─────────────────────────────────────────────────

    def _build_lulc_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(10)
        v.setContentsMargins(10, 10, 10, 10)

        # Apply-to-all
        top_row = QHBoxLayout()
        self._lulc_apply_all_btn = QPushButton("Apply current AOI's settings to all")
        self._lulc_apply_all_btn.setStyleSheet(
            "background:#4a5568; color:white; padding:6px 14px; "
            "border-radius:3px; font-weight:bold;"
        )
        self._lulc_apply_all_btn.clicked.connect(self._lulc_apply_to_all)
        self._lulc_apply_all_btn.setEnabled(False)
        top_row.addStretch()
        top_row.addWidget(self._lulc_apply_all_btn)
        v.addLayout(top_row)

        # Accordion scroll area
        lulc_scroll = QScrollArea()
        lulc_scroll.setWidgetResizable(True)
        lulc_scroll.setFrameShape(QFrame.Shape.NoFrame)
        lulc_host = QWidget()
        self._lulc_cards_layout = QVBoxLayout(lulc_host)
        self._lulc_cards_layout.setSpacing(6)
        self._lulc_cards_layout.addStretch()
        lulc_scroll.setWidget(lulc_host)
        lulc_scroll.setMinimumHeight(280)
        v.addWidget(lulc_scroll, 1)

        # Run button
        lulc_btn_row = QHBoxLayout()
        self._lulc_run_btn = QPushButton("Download LULC & Assign Manning")
        self._lulc_run_btn.setStyleSheet(
            "font-weight:bold; padding:7px 20px; background:#2b6cb0; "
            "color:white; border-radius:4px;"
        )
        self._lulc_run_btn.clicked.connect(self._lulc_run)
        lulc_btn_row.addWidget(self._lulc_run_btn)
        lulc_btn_row.addStretch()
        v.addLayout(lulc_btn_row)

        # Progress + status
        self._lulc_progress = QProgressBar()
        self._lulc_progress.setRange(0, 100)
        self._lulc_progress.setValue(0)
        self._lulc_progress.setStyleSheet("QProgressBar { height:18px; }")
        self._lulc_progress.setVisible(False)
        v.addWidget(self._lulc_progress)

        self._lulc_status_lbl = QLabel("")
        self._lulc_status_lbl.setWordWrap(True)
        self._lulc_status_lbl.setStyleSheet("color:#2d3748; font-size:12px; padding:2px 0px;")
        self._lulc_status_lbl.setVisible(False)
        v.addWidget(self._lulc_status_lbl)

        # Results
        self._lulc_results_gb = QGroupBox(
            "Per-AOI outputs — click an AOI to view its LULC & Manning maps"
        )
        self._lulc_results_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        lulc_rgl = QVBoxLayout(self._lulc_results_gb)
        self._lulc_results_inner = QVBoxLayout()
        self._lulc_results_inner.setSpacing(0)
        lulc_rgl.addLayout(self._lulc_results_inner)
        self._lulc_results_gb.setVisible(False)
        v.addWidget(self._lulc_results_gb)

        # 3-column view
        self._lulc_view_gb = QGroupBox(
            "LULC & Manning's n — click an AOI above to populate"
        )
        self._lulc_view_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        view_outer = QVBoxLayout(self._lulc_view_gb)
        view_outer.setSpacing(6)
        view_outer.setContentsMargins(6, 8, 6, 6)

        self._lulc_view_placeholder = QLabel(
            "<i>Click an AOI above to preview its LULC map, Manning map, "
            "and class breakdown table.</i>"
        )
        self._lulc_view_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lulc_view_placeholder.setStyleSheet(
            "color:#888; padding:30px; background:#fafafa; "
            "border:1px dashed #cbd5e0; border-radius:4px;"
        )
        view_outer.addWidget(self._lulc_view_placeholder)

        three_col = QHBoxLayout()
        three_col.setSpacing(10)

        tbl_col = QVBoxLayout()
        tbl_hdr = QLabel("<b>Land Cover Breakdown</b>")
        tbl_hdr.setStyleSheet("color:#2d3748; font-size:10px;")
        tbl_hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tbl_col.addWidget(tbl_hdr)
        self._lulc_stats_table = QTableWidget()
        self._lulc_stats_table.setColumnCount(4)
        self._lulc_stats_table.setHorizontalHeaderLabels(["Code", "Type", "Area %", "n"])
        self._lulc_stats_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._lulc_stats_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._lulc_stats_table.setAlternatingRowColors(True)
        self._lulc_stats_table.verticalHeader().setVisible(False)
        self._lulc_stats_table.verticalHeader().setDefaultSectionSize(20)
        self._lulc_stats_table.setStyleSheet(
            "QTableWidget { font-size:10px; }"
            "QHeaderView::section { font-size:10px; padding:2px; }"
        )
        h = self._lulc_stats_table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._lulc_stats_table.setMinimumWidth(220)
        tbl_col.addWidget(self._lulc_stats_table, 1)
        three_col.addLayout(tbl_col, 2)

        lulc_col = QVBoxLayout()
        lulc_col.setContentsMargins(0, 0, 0, 0)
        self._lulc_title_lbl = QLabel("<b>LULC Map</b>")
        self._lulc_title_lbl.setStyleSheet("color:#2d3748; font-size:10px;")
        self._lulc_title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lulc_col.addWidget(self._lulc_title_lbl)
        self._lulc_canvas = RasterPreviewCanvas(self, width=6.0, height=4.5)
        lulc_col.addWidget(self._lulc_canvas, 1)
        three_col.addLayout(lulc_col, 5)

        mn_col = QVBoxLayout()
        mn_col.setContentsMargins(0, 0, 0, 0)
        mn_hdr = QLabel("<b>Manning's n Map</b>")
        mn_hdr.setStyleSheet("color:#2d3748; font-size:10px;")
        mn_hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mn_col.addWidget(mn_hdr)
        self._mn_canvas = RasterPreviewCanvas(self, width=6.0, height=4.5)
        mn_col.addWidget(self._mn_canvas, 1)
        three_col.addLayout(mn_col, 5)

        self._lulc_three_col_widget = QWidget()
        self._lulc_three_col_widget.setLayout(three_col)
        self._lulc_three_col_widget.setMinimumHeight(460)
        self._lulc_three_col_widget.setVisible(False)
        view_outer.addWidget(self._lulc_three_col_widget, 1)

        self._lulc_view_gb.setVisible(False)
        v.addWidget(self._lulc_view_gb)
        v.addStretch()
        return page

    # ── Tab 4: Flowline ───────────────────────────────────────────────────────

    def _build_flowline_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(10)
        v.setContentsMargins(10, 10, 10, 10)

        # Card list groupbox
        self._fl_cards_gb = QGroupBox("Flowline options per AOI")
        self._fl_cards_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        fl_gb_v = QVBoxLayout(self._fl_cards_gb)
        fl_gb_v.setSpacing(6)
        fl_gb_v.setContentsMargins(6, 6, 6, 6)

        fl_apply_row = QHBoxLayout()
        self._fl_apply_all_btn = QPushButton("Apply current AOI's settings to all")
        self._fl_apply_all_btn.setStyleSheet(
            "background:#4a5568; color:white; padding:5px 12px; "
            "border-radius:3px; font-weight:bold;"
        )
        self._fl_apply_all_btn.clicked.connect(self._flowline_apply_to_all)
        self._fl_apply_all_btn.setEnabled(False)
        fl_apply_row.addStretch()
        fl_apply_row.addWidget(self._fl_apply_all_btn)
        fl_gb_v.addLayout(fl_apply_row)

        self._fl_cards_layout = QVBoxLayout()
        self._fl_cards_layout.setSpacing(4)
        self._fl_cards_layout.setContentsMargins(0, 0, 0, 0)
        fl_gb_v.addLayout(self._fl_cards_layout)
        v.addWidget(self._fl_cards_gb)

        # Run button
        fl_btn_row = QHBoxLayout()
        fl_btn_row.addStretch()
        self._fl_dl_all_btn = QPushButton("Download Flowlines for all AOIs")
        self._fl_dl_all_btn.setStyleSheet(
            "font-weight:bold; padding:7px 20px; background:#2b6cb0; "
            "color:white; border-radius:4px;"
        )
        self._fl_dl_all_btn.clicked.connect(self._flowline_run_all)
        fl_btn_row.addWidget(self._fl_dl_all_btn)
        v.addLayout(fl_btn_row)

        # Progress + status
        self._fl_progress = QProgressBar()
        self._fl_progress.setRange(0, 0)
        self._fl_progress.setStyleSheet("QProgressBar { height:18px; }")
        self._fl_progress.setVisible(False)
        v.addWidget(self._fl_progress)

        self._fl_status_lbl = QLabel("")
        self._fl_status_lbl.setWordWrap(True)
        self._fl_status_lbl.setStyleSheet("color:#2d3748; font-size:12px; padding:2px 0px;")
        self._fl_status_lbl.setVisible(False)
        v.addWidget(self._fl_status_lbl)

        # Results
        self._fl_results_gb = QGroupBox("")
        self._fl_results_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        fl_rgl = QVBoxLayout(self._fl_results_gb)
        self._fl_results_inner = QVBoxLayout()
        self._fl_results_inner.setSpacing(0)
        fl_rgl.addLayout(self._fl_results_inner)
        self._fl_results_gb.setVisible(False)
        v.addWidget(self._fl_results_gb)

        # Flowline map preview
        self._fl_preview_gb = QGroupBox("Flowline map preview")
        self._fl_preview_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        fl_pv = QVBoxLayout(self._fl_preview_gb)
        self._fl_preview_placeholder = QLabel(
            "<i>Click a downloaded AOI row to preview its flowline here.</i>"
        )
        self._fl_preview_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._fl_preview_placeholder.setStyleSheet(
            "color:#888; padding:30px; background:#fafafa; "
            "border:1px dashed #cbd5e0; border-radius:4px;"
        )
        fl_pv.addWidget(self._fl_preview_placeholder)
        self._fl_canvas = FlowlinePreviewCanvas(self, width=9, height=4.0)
        self._fl_canvas.setVisible(False)
        fl_pv.addWidget(self._fl_canvas, 1)
        self._fl_preview_gb.setVisible(False)
        v.addWidget(self._fl_preview_gb)
        v.addStretch()
        return page

    # ── Tab 5: Flowdata ───────────────────────────────────────────────────────

    def _build_flowdata_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(10)
        v.setContentsMargins(10, 10, 10, 10)

        # Cards groupbox
        self._fd_cards_gb = QGroupBox("Flowdata options per AOI")
        self._fd_cards_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        fd_gb_v = QVBoxLayout(self._fd_cards_gb)
        fd_gb_v.setSpacing(6)
        fd_gb_v.setContentsMargins(6, 6, 6, 6)

        fd_apply_row = QHBoxLayout()
        self._fd_apply_all_btn = QPushButton("Apply current AOI's settings to all")
        self._fd_apply_all_btn.setStyleSheet(
            "background:#4a5568; color:white; padding:5px 12px; "
            "border-radius:3px; font-weight:bold;"
        )
        self._fd_apply_all_btn.clicked.connect(self._flowdata_apply_to_all)
        self._fd_apply_all_btn.setEnabled(False)
        fd_apply_row.addStretch()
        fd_apply_row.addWidget(self._fd_apply_all_btn)
        fd_gb_v.addLayout(fd_apply_row)

        self._fd_cards_layout = QVBoxLayout()
        self._fd_cards_layout.setSpacing(4)
        self._fd_cards_layout.setContentsMargins(0, 0, 0, 0)
        fd_gb_v.addLayout(self._fd_cards_layout)
        v.addWidget(self._fd_cards_gb)

        # Run button
        fd_btn_row = QHBoxLayout()
        fd_btn_row.addStretch()
        self._fd_run_btn = QPushButton("Download Flowdata for all AOIs")
        self._fd_run_btn.setStyleSheet(
            "font-weight:bold; padding:7px 20px; background:#2b6cb0; "
            "color:white; border-radius:4px;"
        )
        self._fd_run_btn.clicked.connect(self._flowdata_run)
        fd_btn_row.addWidget(self._fd_run_btn)
        v.addLayout(fd_btn_row)

        # Progress + status
        self._fd_progress = QProgressBar()
        self._fd_progress.setRange(0, 0)
        self._fd_progress.setStyleSheet("QProgressBar { height:18px; }")
        self._fd_progress.setVisible(False)
        v.addWidget(self._fd_progress)

        self._fd_status_lbl = QLabel("")
        self._fd_status_lbl.setWordWrap(True)
        self._fd_status_lbl.setStyleSheet("color:#2d3748; font-size:12px; padding:2px 0px;")
        self._fd_status_lbl.setVisible(False)
        v.addWidget(self._fd_status_lbl)

        # Results
        self._fd_results_gb = QGroupBox(
            "Per-AOI outputs — click an AOI to view its hydrograph"
        )
        self._fd_results_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        fd_rgl = QVBoxLayout(self._fd_results_gb)
        self._fd_results_inner = QVBoxLayout()
        self._fd_results_inner.setSpacing(0)
        fd_rgl.addLayout(self._fd_results_inner)
        self._fd_results_gb.setVisible(False)
        v.addWidget(self._fd_results_gb)

        self._fd_hydro_canvas = HydrographPreviewCanvas(self, width=9, height=3.5)
        self._fd_hydro_canvas.setVisible(False)
        v.addWidget(self._fd_hydro_canvas)

        v.addStretch()
        return page

    # ── Slot: project done ────────────────────────────────────────────────────

    def _on_project_done(self, data: dict):
        ctx = data.get("ctx", {})
        self._project_dir = ctx.get("project_dir")
        self._aoi.set_project_dir(self._project_dir)
        self._tabs.setCurrentIndex(1)

    # ── Slot: AOI ready ───────────────────────────────────────────────────────

    def _on_aoi_ready(self, features: List[AOIFeatureInfo]):
        self._features = features
        self._rebuild_dem_cards(features)
        self._rebuild_lulc_cards(features)
        self._rebuild_flowline_cards(features)
        self._rebuild_flowdata_cards(features)
        self._rebuild_mesh_cards(features)
        self._tabs.setCurrentIndex(2)

    # ── DEM card management ───────────────────────────────────────────────────

    def _rebuild_dem_cards(self, features: List[AOIFeatureInfo]):
        for card in self._dem_cards:
            card.setParent(None)
            card.deleteLater()
        self._dem_cards = []
        for f in features:
            card = AOIDEMCard(f.name, self, show_buffer=True)
            card.expand_requested.connect(self._on_dem_card_expand)
            self._dem_cards_layout.addWidget(card)
            self._dem_cards.append(card)
        self._dem_apply_all_btn.setEnabled(len(features) > 1)

    def _on_dem_card_expand(self, card):
        for c in self._dem_cards:
            if c is card:
                c.expand()
            else:
                c.collapse()

    def _dem_apply_to_all(self):
        src = next((c for c in self._dem_cards if c.is_expanded()), None)
        if src is None and self._dem_cards:
            src = self._dem_cards[0]
        if src is None:
            return
        cfg = src.get_config()
        for c in self._dem_cards:
            if c is not src:
                c.set_config(cfg)

    # ── DEM run ───────────────────────────────────────────────────────────────

    def _dem_run(self):
        if not self._features:
            self._log("No AOI features confirmed.")
            return
        if not self._project_dir:
            self._log("No project directory set.")
            return

        # Per-AOI coverage checks for user-supplied DEMs
        valid_features = []
        for f, card in zip(self._features, self._dem_cards):
            cfg = card.get_config()
            if cfg.get("has_dem"):
                paths = cfg.get("user_dem_path") or []
                if not paths:
                    self._log(f"  '{f.name}': no DEM file specified — skipping.")
                    continue
                dem_path = paths[0]   # check coverage with first tile
                ok, msg = _check_dem_coverage(dem_path, f.source_file, f.feature_index)
                if not ok:
                    self._log(f"  '{f.name}': coverage check failed — {msg} Skipping.")
                    continue
                self._log(f"  '{f.name}': {msg}")
            valid_features.append(f)

        if not valid_features:
            self._dem_status_lbl.setText("No valid AOIs to process.")
            self._dem_status_lbl.setVisible(True)
            return

        set_running(self._dem_run_btn)
        self._dem_progress.setValue(0)
        self._dem_progress.setVisible(True)
        self._dem_status_lbl.setText(
            f"Preparing DEM for {len(valid_features)} AOI(s)…"
        )
        self._dem_status_lbl.setVisible(True)

        # Use first card's cell size and buffer (per-AOI values now live in each card)
        first_cfg = self._dem_cards[0].get_config() if self._dem_cards else {}
        cell_size_m = float(first_cfg.get("dem_res_m", 10.0))
        buffer_m    = float(first_cfg.get("buffer_m", 100.0))

        self._dem_worker = Worker(
            run_hecras_dem,
            project_dir=self._project_dir,
            features=valid_features,
            dem_cell_size_m=cell_size_m,
            dem_buffer_m=buffer_m,
        )
        self._dem_worker.message.connect(self._on_dem_message)
        self._dem_worker.finished.connect(self._on_dem_done)
        self._dem_worker.error.connect(self._on_dem_error)
        self._dem_worker.start()

    def _on_dem_message(self, msg: str):
        self._log(msg)
        # ▶ Downloading DEM [i/n]: 'name' ...
        m = re.match(r"▶\s+Downloading DEM\s+\[(\d+)/(\d+)\]", msg)
        if m:
            i, total = int(m.group(1)), int(m.group(2))
            self._dem_progress.setValue(0)
            self._dem_status_lbl.setText(
                f"Downloading DEM {i} / {total} …"
            )
            self._dem_status_lbl.setVisible(True)
            return
        # ✓ DEM [i/n] finished: 'name'
        m = re.match(r"✓\s+DEM\s+\[(\d+)/(\d+)\]", msg)
        if m:
            i, total = int(m.group(1)), int(m.group(2))
            pct = int(i / total * 95)
            self._dem_progress.setValue(pct)
            nxt = f"  Starting DEM {i+1} / {total} …" if i < total else ""
            self._dem_status_lbl.setText(f"DEM {i} / {total} finished.{nxt}")
            return
        # Download progress: N/M tiles
        m = re.search(r"[Dd]ownload progress:\s*(\d+)/(\d+)", msg)
        if m:
            done, total = int(m.group(1)), int(m.group(2))
            if total > 0:
                self._dem_progress.setValue(int(done / total * 70))

    def _on_dem_done(self, summary: dict):
        set_ready(self._dem_run_btn)
        self._dem_progress.setValue(100)
        n = len(summary.get("features", []))
        self._dem_summary = summary
        self._dem_status_lbl.setText(f"DEM processed for {n} AOI(s).")
        self._dem_status_lbl.setStyleSheet("color:#276749; font-weight:bold; font-size:12px; padding:2px 0px;")
        self._dem_status_lbl.setStyleSheet("color:#276749; font-weight:bold; font-size:12px; padding:2px 0px;")
        self._dem_status_lbl.setVisible(True)
        self._dem_build_results(summary)
        self._refresh_run_checklist()

    def _on_dem_error(self, msg: str):
        set_ready(self._dem_run_btn)
        self._dem_progress.setVisible(False)
        self._log(f"ERROR: {msg}")
        self._dem_status_lbl.setText(f"Error: {msg.splitlines()[0]}")
        self._dem_status_lbl.setVisible(True)

    def _dem_build_results(self, summary: dict):
        while self._dem_results_inner.count():
            item = self._dem_results_inner.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)

        features = summary.get("features", [])
        if not features:
            return

        for f in features:
            name = f.get("name", "?")
            path = f.get("dem_tif", "")
            row = QFrame()
            row.setStyleSheet(
                "QFrame { background:#f9fafb; border:1px solid #e2e8f0; "
                "border-radius:3px; }"
                "QFrame:hover { background:#f0f2f5; }"
            )
            rl = QHBoxLayout(row)
            rl.setContentsMargins(4, 0, 4, 0)
            rl.setSpacing(6)
            btn = QPushButton(name)
            btn.setStyleSheet(
                "QPushButton { text-align:left; background:transparent; border:none; "
                "color:#2d3748; font-weight:bold; padding:2px; }"
                "QPushButton:hover { color:#1a202c; text-decoration:underline; }"
            )
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(
                lambda _c, n=name, p=path: self._dem_show_raster(n, p)
            )
            rl.addWidget(btn, 1)
            self._dem_results_inner.addWidget(row)

        self._dem_results_gb.setVisible(True)
        self._dem_preview_gb.setVisible(True)
        self._dem_preview_placeholder.setVisible(True)
        self._dem_preview_2col.setVisible(False)

    def _dem_show_raster(self, name: str, path: str):
        if not path or not Path(path).exists():
            self._dem_preview_placeholder.setText(
                f"DEM file not found: {path}"
            )
            self._dem_preview_placeholder.setVisible(True)
            self._dem_preview_2col.setVisible(False)
            return
        self._dem_raster_preview.show_raster(
            path, title=f"DEM — {name}", cmap="terrain",
            colorbar_label="Elevation (m)",
        )
        self._dem_preview_placeholder.setVisible(False)
        self._dem_preview_2col.setVisible(True)

    # ── LULC card management ──────────────────────────────────────────────────

    def _rebuild_lulc_cards(self, features: List[AOIFeatureInfo]):
        for card in self._lulc_cards:
            card.setParent(None)
            card.deleteLater()
        self._lulc_cards = []
        for f in features:
            card = AOILulcCard(f.name, self, show_buffer=True, hecras_mode=True)
            card.expand_requested.connect(self._on_lulc_card_expand)
            self._lulc_cards_layout.insertWidget(
                self._lulc_cards_layout.count() - 1, card
            )
            self._lulc_cards.append(card)
        self._lulc_apply_all_btn.setEnabled(len(features) > 1)

    def _on_lulc_card_expand(self, card: AOILulcCard):
        for c in self._lulc_cards:
            if c is card:
                c.expand()
            else:
                c.collapse()

    def _lulc_apply_to_all(self):
        src = next((c for c in self._lulc_cards if c.is_expanded()), None)
        if src is None and self._lulc_cards:
            src = self._lulc_cards[0]
        if src is None:
            return
        cfg = src.get_config()
        for c in self._lulc_cards:
            if c is not src:
                c.set_config(cfg)

    # ── LULC run ──────────────────────────────────────────────────────────────

    def _lulc_run(self):
        if not self._features:
            self._log("No AOI features confirmed.")
            return
        if not self._project_dir:
            self._log("No project directory set.")
            return

        per_aoi = [c.get_config() for c in self._lulc_cards]
        first = per_aoi[0] if per_aoi else {}

        set_running(self._lulc_run_btn)
        self._lulc_progress.setValue(0)
        self._lulc_progress.setVisible(True)
        self._lulc_status_lbl.setText(
            f"Starting LULC download for {len(self._features)} AOI(s)…"
        )
        self._lulc_status_lbl.setVisible(True)
        self._lulc_clear_results()

        feats = self._features
        proj_dir = self._project_dir
        lulc_source = first.get("lulc_source", "nlcd")
        cell_size_m = float(first.get("cell_size_m", 30.0))
        nlcd_year = first.get("nlcd_year", "2021")
        s2_year = int(first.get("sentinel2_year", 2023))
        manning_mapping = first.get("manning_mapping")

        first_lulc_cfg = self._lulc_cards[0].get_config() if self._lulc_cards else {}
        lulc_buffer_m = float(first_lulc_cfg.get("buffer_m", 100.0))

        def _msg(m):
            self._log(m)
            mat = re.search(r"Done \[(\d+)/(\d+)\]", m)
            if mat:
                i, total = int(mat.group(1)), int(mat.group(2))
                self._lulc_progress.setValue(int(i / total * 95))

        self._manning_worker = Worker(
            run_hecras_manning,
            project_dir=proj_dir,
            features=feats,
            lulc_source=lulc_source,
            lulc_cell_size_m=cell_size_m,
            dem_buffer_m=lulc_buffer_m,
            nlcd_year=nlcd_year,
            sentinel2_year=s2_year,
            manning_mapping=manning_mapping,
        )
        self._manning_worker.message.connect(_msg)
        self._manning_worker.finished.connect(self._on_lulc_done)
        self._manning_worker.error.connect(self._on_lulc_error)
        self._manning_worker.start()

    def _on_lulc_done(self, summary: dict):
        set_ready(self._lulc_run_btn)
        self._lulc_progress.setValue(100)
        n = len(summary.get("features", []))
        self._manning_summary = summary
        self._lulc_status_lbl.setText(f"LULC + Manning processed for {n} AOI(s).")
        self._lulc_status_lbl.setStyleSheet("color:#276749; font-weight:bold; font-size:12px; padding:2px 0px;")
        self._lulc_status_lbl.setStyleSheet("color:#276749; font-weight:bold; font-size:12px; padding:2px 0px;")
        self._lulc_status_lbl.setVisible(True)
        self._lulc_build_results(summary)
        self._refresh_run_checklist()

    def _on_lulc_error(self, msg: str):
        set_ready(self._lulc_run_btn)
        self._lulc_progress.setVisible(False)
        self._log(f"ERROR: {msg}")
        self._lulc_status_lbl.setText(f"Error: {msg.splitlines()[0]}")
        self._lulc_status_lbl.setVisible(True)

    def _lulc_clear_results(self):
        while self._lulc_results_inner.count():
            item = self._lulc_results_inner.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        self._lulc_results_gb.setVisible(False)
        self._lulc_view_gb.setVisible(False)
        self._lulc_view_placeholder.setVisible(True)
        self._lulc_three_col_widget.setVisible(False)
        self._lulc_stats_table.setRowCount(0)
        self._lulc_canvas.clear()
        self._mn_canvas.clear()

    def _lulc_build_results(self, summary: dict):
        self._lulc_clear_results()
        features_out = summary.get("features", [])
        if not features_out:
            return

        for entry in features_out:
            name        = entry.get("name", "?")
            lulc_tif    = entry.get("lulc_tif", "")
            manning_tif = entry.get("manning_tif", "")
            lulc_stats  = entry.get("lulc_stats", [])
            lulc_source = entry.get("lulc_source", "nlcd")
            lulc_year   = entry.get("lulc_year", "")

            row = QFrame()
            row.setStyleSheet(
                "QFrame { background:#f9fafb; border:1px solid #e2e8f0; "
                "border-radius:3px; padding:3px 6px; }"
                "QFrame:hover { background:#f0f2f5; }"
            )
            rl = QHBoxLayout(row)
            rl.setContentsMargins(6, 2, 6, 2)
            rl.setSpacing(8)
            btn = QPushButton(f"  {name}")
            btn.setStyleSheet(
                "QPushButton { text-align:left; background:transparent; border:none; "
                "color:#2d3748; font-weight:bold; padding:2px; }"
                "QPushButton:hover { color:#1a202c; text-decoration:underline; }"
            )
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(
                lambda _c, lt=lulc_tif, mt=manning_tif, nm=name,
                       st=lulc_stats, src=lulc_source, yr=lulc_year:
                    self._lulc_show_aoi(nm, lt, mt, st, src, yr)
            )
            rl.addWidget(btn, 1)
            self._lulc_results_inner.addWidget(row)

        self._lulc_results_gb.setVisible(True)
        self._lulc_view_gb.setVisible(True)

    def _lulc_show_aoi(
        self,
        name: str,
        lulc_tif: str,
        manning_tif: str,
        lulc_stats: list,
        lulc_source: str,
        lulc_year: str,
    ):
        self._lulc_view_gb.setTitle(f"LULC & Manning's n  —  {name}")
        src_label = (
            "NLCD" if lulc_source in ("nlcd", "download_nlcd") else "Sentinel-2"
        )
        year_str = f", {lulc_year}" if lulc_year else ""

        content_ok = False
        if lulc_tif and Path(lulc_tif).exists():
            self._lulc_canvas.show_raster(
                lulc_tif, title=f"LULC ({src_label}{year_str})",
                cmap="tab20", colorbar_label="LULC class code",
                colorbar_location="bottom",
            )
            content_ok = True

        if manning_tif and Path(manning_tif).exists():
            self._mn_canvas.show_raster(
                manning_tif, title="Manning's n",
                cmap="YlOrRd", colorbar_label="Manning n",
                colorbar_location="bottom",
            )
            content_ok = True

        self._lulc_three_col_widget.setVisible(content_ok)
        self._lulc_view_placeholder.setVisible(not content_ok)

        self._lulc_stats_table.setRowCount(0)
        for r_data in lulc_stats:
            row_idx = self._lulc_stats_table.rowCount()
            self._lulc_stats_table.insertRow(row_idx)
            code_item = QTableWidgetItem(str(r_data.get("code", "")))
            code_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._lulc_stats_table.setItem(row_idx, 0, code_item)
            self._lulc_stats_table.setItem(row_idx, 1, QTableWidgetItem(r_data.get("name", "")))
            pct = r_data.get("area_frac", 0.0) * 100
            pct_item = QTableWidgetItem(f"{pct:.1f}")
            pct_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._lulc_stats_table.setItem(row_idx, 2, pct_item)
            n_val = r_data.get("manning_n")
            n_str = f"{n_val:.3f}" if n_val is not None else "—"
            n_item = QTableWidgetItem(n_str)
            n_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._lulc_stats_table.setItem(row_idx, 3, n_item)

    # ── Flowline card management ──────────────────────────────────────────────

    def _rebuild_flowline_cards(self, features: List[AOIFeatureInfo]):
        """Build one _HECRASFlowlineCard per AOI for the flowline tab."""
        for card in self._flowline_cards:
            card.setParent(None)
            card.deleteLater()
        self._flowline_cards = []
        for f in features:
            card = _HECRASFlowlineCard(f.name, self)
            card.expand_requested.connect(self._on_flowline_card_expand)
            self._fl_cards_layout.addWidget(card)
            self._flowline_cards.append(card)
        self._fl_apply_all_btn.setEnabled(len(features) > 1)
        self._fl_preview_gb.setVisible(False)

    def _on_flowline_card_expand(self, card):
        for c in self._flowline_cards:
            if c is card:
                c.expand()
            else:
                c.collapse()

    def _flowline_apply_to_all(self):
        src = next((c for c in self._flowline_cards if c.is_expanded()), None)
        if src is None and self._flowline_cards:
            src = self._flowline_cards[0]
        if src is None:
            return
        cfg = src.get_config()
        for c in self._flowline_cards:
            if c is not src:
                c.set_config(cfg)

    def _flowline_run_all(self):
        if not self._features:
            self._log("No AOI features confirmed.")
            return
        if not self._project_dir:
            self._log("No project directory set.")
            return

        self._fl_progress.setVisible(True)
        self._fl_status_lbl.setText(
            f"Downloading flowlines for all {len(self._features)} AOI(s)…"
        )
        self._fl_status_lbl.setVisible(True)

        feats = self._features
        proj_dir = self._project_dir
        dem_summary = self._dem_summary
        per_aoi = [c.get_config() for c in self._flowline_cards]

        self._flowline_worker = Worker(
            run_hecras_flowline,
            project_dir=proj_dir,
            features=feats,
            dem_summary=dem_summary,
        )
        self._flowline_worker.message.connect(self._log)
        self._flowline_worker.finished.connect(self._on_flowline_all_done)
        self._flowline_worker.error.connect(self._on_flowline_error)
        self._flowline_worker.start()

    def _on_flowline_all_done(self, summary: dict):
        self._fl_progress.setVisible(False)
        n = len(summary.get("features", []))
        self._flowline_summary = summary
        self._fl_status_lbl.setText(f"Flowlines downloaded for {n} AOI(s).")
        self._fl_status_lbl.setStyleSheet("color:#276749; font-weight:bold; font-size:12px; padding:2px 0px;")
        self._fl_status_lbl.setStyleSheet("color:#276749; font-weight:bold; font-size:12px; padding:2px 0px;")
        self._fl_status_lbl.setVisible(True)
        self._refresh_run_checklist()
        self._fl_build_results(summary)
        for entry in summary.get("features", []):
            aoi_name = entry.get("name")
            self._update_flowdata_card_from_flowline(aoi_name, entry)
        self._fl_preview_gb.setVisible(True)

    def _on_flowline_error(self, msg: str):
        self._fl_progress.setVisible(False)
        self._log(f"ERROR: {msg}")
        self._fl_status_lbl.setText(f"Error: {msg.splitlines()[0]}")
        self._fl_status_lbl.setVisible(True)

    def _fl_build_results(self, summary: dict):
        while self._fl_results_inner.count():
            item = self._fl_results_inner.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        features = summary.get("features", [])
        if not features:
            return
        for entry in features:
            feat_name = entry.get("name", "?")
            feat_obj = next(
                (f for f in self._features if f.name == feat_name), None
            )
            row = QFrame()
            row.setStyleSheet(
                "QFrame { background:#f9fafb; border:1px solid #e2e8f0; "
                "border-radius:3px; padding:3px 6px; }"
                "QFrame:hover { background:#f0f2f5; }"
            )
            rl = QHBoxLayout(row)
            rl.setContentsMargins(6, 2, 6, 2)
            rl.setSpacing(8)
            btn = QPushButton(f"  {feat_name}")
            btn.setStyleSheet(
                "QPushButton { text-align:left; background:transparent; border:none; "
                "color:#2d3748; font-weight:bold; padding:2px; }"
                "QPushButton:hover { color:#1a202c; text-decoration:underline; }"
            )
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(
                lambda _c, fo=feat_obj, e=entry: self._flowline_show_preview(fo, e)
            )
            rl.addWidget(btn, 1)
            self._fl_results_inner.addWidget(row)
        self._fl_results_gb.setVisible(True)

    def _flowline_show_preview(self, feat: Optional[AOIFeatureInfo], entry: Optional[dict] = None):
        if feat is None:
            return
        if entry is None:
            if self._flowline_summary is None:
                return
            entry = next(
                (e for e in self._flowline_summary.get("features", [])
                 if e.get("name") == feat.name),
                None,
            )
        if entry is None:
            return
        self._fl_preview_gb.setVisible(True)
        self._fl_preview_placeholder.setVisible(False)
        self._fl_canvas.setVisible(True)
        self._fl_canvas.show_flowlines(
            aoi_path=feat.source_file,
            feature_index=feat.feature_index,
            main_river_path=entry.get("main_river_line"),
            title=f"Flowline — {feat.name}",
            upstream_xy=entry.get("upstream_xy"),
            downstream_xy=entry.get("downstream_xy"),
        )

    # ── Flowdata card management ──────────────────────────────────────────────

    def _rebuild_flowdata_cards(self, features: List[AOIFeatureInfo]):
        for card in self._flowdata_cards:
            card.setParent(None)
            card.deleteLater()
        self._flowdata_cards = []
        for f in features:
            card = _AOIFlowdataCard(f.name, self)
            card.expand_requested.connect(self._on_fd_card_expand)
            self._fd_cards_layout.addWidget(card)
            self._flowdata_cards.append(card)
        self._fd_apply_all_btn.setEnabled(len(features) > 1)

    def _on_fd_card_expand(self, card: _AOIFlowdataCard):
        for c in self._flowdata_cards:
            if c is card:
                c.expand()
            else:
                c.collapse()

    def _flowdata_apply_to_all(self):
        src = next((c for c in self._flowdata_cards if c.is_expanded()), None)
        if src is None and self._flowdata_cards:
            src = self._flowdata_cards[0]
        if src is None:
            return
        cfg = src.get_config()
        for c in self._flowdata_cards:
            if c is not src:
                c.set_config(cfg)

    def _update_flowdata_card_from_flowline(self, aoi_name: str, flowline_entry: dict):
        """Pre-populate the flowdata card for aoi_name from flowline results."""
        card: Optional[_AOIFlowdataCard] = None
        feat = None
        for i, f in enumerate(self._features):
            if f.name == aoi_name:
                feat = f
                if i < len(self._flowdata_cards):
                    card = self._flowdata_cards[i]
                break
        if card is None:
            return

        # Build a partial config update preserving existing dates/interval
        existing = card.get_config()
        updates: dict = {}

        upstream_reach_id = flowline_entry.get("upstream_reach_id")
        if upstream_reach_id is not None:
            updates["feature_ids"] = str(upstream_reach_id)

        if feat is not None:
            usgs_gages = getattr(feat, "usgs_gages", None)
            if usgs_gages and len(usgs_gages) > 0:
                site_no = str(usgs_gages[0].get("site_no", "")).strip()
                if site_no:
                    updates["gage_ids"] = site_no

        if updates:
            merged = {**existing, **updates}
            card.set_config(merged)

    # ── Flowdata run ──────────────────────────────────────────────────────────

    def _flowdata_run(self):
        if not self._features:
            self._log("No AOI features confirmed.")
            return
        if not self._project_dir:
            self._log("No project directory set.")
            return

        per_aoi = [c.get_config() for c in self._flowdata_cards]

        self._fd_progress.setVisible(True)
        self._fd_status_lbl.setText(
            f"Downloading flowdata for {len(self._features)} AOI(s)…"
        )
        self._fd_status_lbl.setVisible(True)
        set_running(self._fd_run_btn)

        while self._fd_results_inner.count():
            item = self._fd_results_inner.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        self._fd_results_gb.setVisible(False)
        self._fd_hydro_canvas.setVisible(False)

        feats = self._features
        proj_dir = self._project_dir
        fl_summary = self._flowline_summary

        self._flowdata_worker = Worker(
            run_hecras_flowdata,
            project_dir=proj_dir,
            features=feats,
            per_aoi_configs=per_aoi,
            flowline_summary=fl_summary,
        )
        self._flowdata_worker.message.connect(self._log)
        self._flowdata_worker.finished.connect(self._on_flowdata_done)
        self._flowdata_worker.error.connect(self._on_flowdata_error)
        self._flowdata_worker.start()

    def _on_flowdata_done(self, summary: dict):
        set_ready(self._fd_run_btn)
        self._fd_progress.setVisible(False)
        n = len(summary.get("features", []))
        self._flowdata_summary = summary
        self._fd_status_lbl.setText(f"Flowdata downloaded for {n} AOI(s).")
        self._fd_status_lbl.setStyleSheet("color:#276749; font-weight:bold; font-size:12px; padding:2px 0px;")
        self._fd_status_lbl.setStyleSheet("color:#276749; font-weight:bold; font-size:12px; padding:2px 0px;")
        self._fd_status_lbl.setVisible(True)
        self._fd_build_results(summary)
        self._refresh_run_checklist()

    def _on_flowdata_error(self, msg: str):
        set_ready(self._fd_run_btn)
        self._fd_progress.setVisible(False)
        self._log(f"ERROR: {msg}")
        self._fd_status_lbl.setText(f"Error: {msg.splitlines()[0]}")
        self._fd_status_lbl.setVisible(True)

    def _fd_build_results(self, summary: dict):
        features = summary.get("features", [])
        if not features:
            return

        for entry in features:
            name = entry.get("name", "?")
            csv_path = entry.get("csv_path", "")

            row = QFrame()
            row.setStyleSheet(
                "QFrame { background:#f9fafb; border:1px solid #e2e8f0; "
                "border-radius:3px; padding:3px 6px; }"
                "QFrame:hover { background:#f0f2f5; }"
            )
            rl = QHBoxLayout(row)
            rl.setContentsMargins(6, 2, 6, 2)
            rl.setSpacing(8)
            btn = QPushButton(f"  {name}")
            btn.setStyleSheet(
                "QPushButton { text-align:left; background:transparent; border:none; "
                "color:#2d3748; font-weight:bold; padding:2px; }"
                "QPushButton:hover { color:#1a202c; text-decoration:underline; }"
            )
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(
                lambda _c, nm=name, p=csv_path: self._fd_show_hydro(nm, p)
            )
            rl.addWidget(btn, 1)
            self._fd_results_inner.addWidget(row)

        self._fd_results_gb.setVisible(True)

    def _fd_show_hydro(self, name: str, csv_path: str):
        if not csv_path or not Path(csv_path).exists():
            self._fd_hydro_canvas.setVisible(False)
            return
        self._fd_hydro_canvas.show_hydrograph(csv_path, title=f"Discharge — {name}")
        self._fd_hydro_canvas.setVisible(True)

    # ── Tab 6: Build Mesh ─────────────────────────────────────────────────────

    def _build_mesh_tab(self) -> QWidget:
        """Return the "Build Mesh" tab widget."""
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(10)
        v.setContentsMargins(10, 10, 10, 10)

        # Card list groupbox
        self._mesh_cards_gb = QGroupBox("Mesh options per AOI")
        self._mesh_cards_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        mesh_gb_v = QVBoxLayout(self._mesh_cards_gb)
        mesh_gb_v.setSpacing(6)
        mesh_gb_v.setContentsMargins(6, 6, 6, 6)

        mesh_apply_row = QHBoxLayout()
        self._mesh_apply_all_btn = QPushButton("Apply current AOI's settings to all")
        self._mesh_apply_all_btn.setStyleSheet(
            "background:#4a5568; color:white; padding:5px 12px; "
            "border-radius:3px; font-weight:bold;"
        )
        self._mesh_apply_all_btn.clicked.connect(self._mesh_apply_to_all)
        self._mesh_apply_all_btn.setEnabled(False)
        mesh_apply_row.addStretch()
        mesh_apply_row.addWidget(self._mesh_apply_all_btn)
        mesh_gb_v.addLayout(mesh_apply_row)

        self._mesh_cards_layout = QVBoxLayout()
        self._mesh_cards_layout.setSpacing(4)
        self._mesh_cards_layout.setContentsMargins(0, 0, 0, 0)
        mesh_gb_v.addLayout(self._mesh_cards_layout)
        v.addWidget(self._mesh_cards_gb)

        # Run button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._mesh_run_btn = QPushButton("Build Mesh for all AOIs")
        self._mesh_run_btn.setStyleSheet(
            "font-weight:bold; padding:7px 20px; background:#2b6cb0; "
            "color:white; border-radius:4px;"
        )
        self._mesh_run_btn.clicked.connect(self._mesh_run)
        btn_row.addWidget(self._mesh_run_btn)
        v.addLayout(btn_row)

        # Progress bar
        self._mesh_progress = QProgressBar()
        self._mesh_progress.setRange(0, 100)
        self._mesh_progress.setValue(0)
        self._mesh_progress.setStyleSheet("QProgressBar { height:18px; }")
        self._mesh_progress.setVisible(False)
        v.addWidget(self._mesh_progress)

        # Status label
        self._mesh_status_lbl = QLabel("")
        self._mesh_status_lbl.setWordWrap(True)
        self._mesh_status_lbl.setStyleSheet(
            "color:#2d3748; font-size:12px; padding:2px 0px;"
        )
        self._mesh_status_lbl.setVisible(False)
        v.addWidget(self._mesh_status_lbl)

        # Results section (hidden until done)
        self._mesh_results_gb = QGroupBox("Mesh results")
        self._mesh_results_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        rgl = QVBoxLayout(self._mesh_results_gb)
        self._mesh_results_inner = QVBoxLayout()
        self._mesh_results_inner.setSpacing(4)
        rgl.addLayout(self._mesh_results_inner)
        self._mesh_results_gb.setVisible(False)
        v.addWidget(self._mesh_results_gb)

        v.addStretch()
        return page

    # ── Mesh card management ──────────────────────────────────────────────────

    def _rebuild_mesh_cards(self, features: List[AOIFeatureInfo]):
        for card in self._mesh_cards:
            card.setParent(None)
            card.deleteLater()
        self._mesh_cards = []
        for f in features:
            card = _HECRASMeshCard(f.name, self)
            card.expand_requested.connect(self._on_mesh_card_expand)
            self._mesh_cards_layout.addWidget(card)
            self._mesh_cards.append(card)
        self._mesh_apply_all_btn.setEnabled(len(features) > 1)

    def _on_mesh_card_expand(self, card: _HECRASMeshCard):
        for c in self._mesh_cards:
            if c is card:
                c.expand()
            else:
                c.collapse()

    def _mesh_apply_to_all(self):
        src = next((c for c in self._mesh_cards if c.is_expanded()), None)
        if src is None and self._mesh_cards:
            src = self._mesh_cards[0]
        if src is None:
            return
        cfg = src.get_config()
        for c in self._mesh_cards:
            if c is not src:
                c.set_config(cfg)

    def _mesh_run(self):
        if not self._features:
            self._log("No AOI features confirmed.")
            return
        if not self._project_dir:
            self._log("No project directory set.")
            return

        per_aoi_mesh = [c.get_config() for c in self._mesh_cards]
        # Fallback defaults if cards list is shorter than features
        _default_mesh = {"cell_size_near": 10.0, "cell_size_far": 100.0, "refine_buffer_m": 150.0}

        self._mesh_progress.setVisible(True)
        self._mesh_status_lbl.setText(
            f"Building mesh for {len(self._features)} AOI(s)…"
        )
        self._mesh_status_lbl.setVisible(True)
        set_running(self._mesh_run_btn)

        # Clear previous results
        while self._mesh_results_inner.count():
            item = self._mesh_results_inner.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        self._mesh_results_gb.setVisible(False)

        feats = self._features
        dem_summary = self._dem_summary
        fl_summary = self._flowline_summary
        mn_summary = self._manning_summary

        def _run_fn(log_fn=None):
            _log = log_fn or self._log
            from core.hecras_mesh import build_hecras_geometry
            results = []
            for i, f in enumerate(feats):
                folder = Path(f.folder_path) / "HECRAS_files"
                folder.mkdir(parents=True, exist_ok=True)

                mesh_cfg = per_aoi_mesh[i] if i < len(per_aoi_mesh) else _default_mesh
                cell_near = mesh_cfg.get("cell_size_near", 10.0)
                cell_far = mesh_cfg.get("cell_size_far", 100.0)
                refine_buf = mesh_cfg.get("refine_buffer_m", 150.0)

                # Resolve DEM path
                dem_path = None
                if dem_summary:
                    for entry in dem_summary.get("dem_per_aoi", []):
                        if entry.get("name") == f.name:
                            dem_path = entry.get("dem_tif")
                            break
                if not dem_path:
                    dem_path = str(folder / "dem.tif")

                # Resolve river path from flowline summary
                river_path = None
                if fl_summary:
                    for entry in fl_summary.get("features", []):
                        if entry.get("name") == f.name:
                            river_path = entry.get("flowline")
                            break

                # Resolve Manning shapefile
                manning_shp = None
                if mn_summary:
                    for entry in mn_summary.get("manning_per_aoi", []):
                        if entry.get("name") == f.name:
                            manning_shp = entry.get("manning_shp")
                            break

                _log(f"  Building mesh for '{f.name}' …")
                summary = build_hecras_geometry(
                    aoi_path=f.source_file,
                    feature_index=f.feature_index,
                    river_path=river_path,
                    dem_path=dem_path,
                    manning_shp_path=manning_shp,
                    output_dir=str(folder),
                    area_name=f.name,
                    cell_size_near=cell_near,
                    cell_size_far=cell_far,
                    refine_buffer_m=refine_buf,
                    log_fn=_log,
                )
                summary["aoi_name"] = f.name
                results.append(summary)
            return {"features": results}

        self._mesh_worker = Worker(_run_fn)
        self._mesh_worker.message.connect(self._log)
        self._mesh_worker.finished.connect(self._on_mesh_done)
        self._mesh_worker.error.connect(self._on_mesh_error)
        self._mesh_worker.start()

    def _on_mesh_done(self, summary: dict):
        set_ready(self._mesh_run_btn)
        self._mesh_progress.setVisible(False)
        features = summary.get("features", [])
        n = len(features)
        self._mesh_summary = summary
        self._mesh_status_lbl.setText(f"Mesh built for {n} AOI(s).")
        self._mesh_status_lbl.setStyleSheet("color:#276749; font-weight:bold; font-size:12px; padding:2px 0px;")
        self._mesh_status_lbl.setStyleSheet("color:#276749; font-weight:bold; font-size:12px; padding:2px 0px;")
        self._mesh_status_lbl.setVisible(True)
        self._mesh_build_results(features)
        # Refresh the run-tab checklist
        self._refresh_run_checklist()

    def _on_mesh_error(self, msg: str):
        set_ready(self._mesh_run_btn)
        self._mesh_progress.setVisible(False)
        self._log(f"ERROR: {msg}")
        self._mesh_status_lbl.setText(f"Error: {msg.splitlines()[0]}")
        self._mesh_status_lbl.setVisible(True)

    def _mesh_build_results(self, features: list):
        for entry in features:
            name = entry.get("aoi_name", "?")
            n_cells = entry.get("n_cells", 0)
            near = entry.get("cell_size_near", 0)
            far = entry.get("cell_size_far", 0)

            row = QFrame()
            row.setStyleSheet(
                "QFrame { background:#f9fafb; border:1px solid #e2e8f0; "
                "border-radius:3px; padding:3px 6px; }"
            )
            rl = QHBoxLayout(row)
            rl.setContentsMargins(6, 2, 6, 2)
            rl.setSpacing(8)

            name_lbl = QLabel(f"<b>{name}</b>")
            name_lbl.setStyleSheet("color:#2d3748;")
            rl.addWidget(name_lbl, 1)

            info_lbl = QLabel(
                f"Cells: {n_cells:,}   |   Near: {near:.0f} m   |   Far: {far:.0f} m"
            )
            info_lbl.setStyleSheet("color:#4a5568; font-size:11px;")
            rl.addWidget(info_lbl)

            self._mesh_results_inner.addWidget(row)

        self._mesh_results_gb.setVisible(True)

    # ── Tab 7: Build & Run ────────────────────────────────────────────────────

    def _build_run_tab(self) -> QWidget:
        """Return the "Build & Run" tab widget."""
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(12)
        v.setContentsMargins(10, 10, 10, 10)

        # ── Input checklist ────────────────────────────────────────────────
        cl_gb = QGroupBox("Input checklist")
        cl_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        cl_v = QVBoxLayout(cl_gb)
        cl_v.setSpacing(2)
        cl_v.setContentsMargins(8, 6, 8, 6)

        self._run_check_rows = {}   # key → (icon_lbl, text_lbl)
        for key, label in [
            ("dem",       "DEM"),
            ("manning",   "LULC & Manning"),
            ("flowline",  "Flowline"),
            ("flowdata",  "Flowdata"),
            ("mesh",      "Mesh"),
            ("hecras_exe", "HEC-RAS executable"),
        ]:
            row_w = QWidget()
            rh = QHBoxLayout(row_w)
            rh.setContentsMargins(0, 0, 0, 0)
            rh.setSpacing(6)
            icon_lbl = QLabel("✗")
            icon_lbl.setFixedWidth(16)
            icon_lbl.setStyleSheet("color:#e53e3e; font-weight:bold;")
            text_lbl = QLabel(label)
            text_lbl.setStyleSheet("color:#4a5568;")
            rh.addWidget(icon_lbl)
            rh.addWidget(text_lbl, 1)
            cl_v.addWidget(row_w)
            self._run_check_rows[key] = (icon_lbl, text_lbl)

        v.addWidget(cl_gb)

        # ── HEC-RAS executable ─────────────────────────────────────────────
        exe_gb = QGroupBox("HEC-RAS executable")
        exe_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        exe_v = QVBoxLayout(exe_gb)
        exe_v.setContentsMargins(8, 6, 8, 6)

        exe_row = QHBoxLayout()
        self._hecras_exe_edit = QLineEdit()
        self._hecras_exe_edit.setPlaceholderText(
            "C:/Program Files (x86)/HEC/HEC-RAS/6.5/RasUnsteady64.exe"
        )
        self._hecras_exe_edit.textChanged.connect(self._refresh_run_checklist)
        exe_row.addWidget(self._hecras_exe_edit, 1)
        exe_browse_btn = QPushButton("Browse…")
        exe_browse_btn.setFixedWidth(75)
        exe_browse_btn.clicked.connect(self._browse_hecras_exe)
        exe_row.addWidget(exe_browse_btn)
        exe_v.addLayout(exe_row)

        if sys.platform != "win32":
            plat_note = QLabel(
                "Running HEC-RAS requires Windows. "
                "You can still generate all input files below."
            )
            plat_note.setWordWrap(True)
            plat_note.setStyleSheet("color:#c05621; font-size:11px;")
            exe_v.addWidget(plat_note)
        else:
            win_note = QLabel("Windows only. HEC-RAS 6.x must be installed.")
            win_note.setStyleSheet("color:#718096; font-size:11px;")
            exe_v.addWidget(win_note)

        v.addWidget(exe_gb)

        # ── Simulation settings ────────────────────────────────────────────
        sim_gb = QGroupBox("Simulation settings")
        sim_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        sim_form = QFormLayout(sim_gb)
        sim_form.setVerticalSpacing(8)
        sim_form.setContentsMargins(8, 8, 8, 8)

        self._run_start_dt = QDateTimeEdit()
        self._run_start_dt.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._run_start_dt.setCalendarPopup(True)
        self._run_start_dt.setDateTime(
            QDateTime.fromString("2026-05-01 00:00", "yyyy-MM-dd HH:mm")
        )
        sim_form.addRow("Start datetime:", self._run_start_dt)

        self._run_end_dt = QDateTimeEdit()
        self._run_end_dt.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._run_end_dt.setCalendarPopup(True)
        self._run_end_dt.setDateTime(
            QDateTime.fromString("2026-05-31 23:00", "yyyy-MM-dd HH:mm")
        )
        sim_form.addRow("End datetime:", self._run_end_dt)

        self._run_timestep_spin = QDoubleSpinBox()
        self._run_timestep_spin.setRange(1.0, 3600.0)
        self._run_timestep_spin.setDecimals(1)
        self._run_timestep_spin.setValue(60.0)
        self._run_timestep_spin.setSuffix(" s")
        sim_form.addRow("Time step (s):", self._run_timestep_spin)

        self._run_slope_spin = QDoubleSpinBox()
        self._run_slope_spin.setRange(0.00001, 1.0)
        self._run_slope_spin.setDecimals(6)
        self._run_slope_spin.setValue(0.001)
        sim_form.addRow("Downstream normal depth slope:", self._run_slope_spin)

        v.addWidget(sim_gb)

        # ── Action buttons ─────────────────────────────────────────────────
        btns_row = QHBoxLayout()
        btns_row.setSpacing(10)
        btns_row.addStretch()

        self._gen_files_btn = QPushButton("Generate Model Files")
        self._gen_files_btn.setStyleSheet(
            "font-weight:bold; padding:7px 16px; background:#4a5568; "
            "color:white; border-radius:4px;"
        )
        self._gen_files_btn.clicked.connect(self._generate_model_files)
        btns_row.addWidget(self._gen_files_btn)

        self._run_hecras_btn = QPushButton("Build & Run HEC-RAS")
        self._run_hecras_btn.setStyleSheet(
            "font-weight:bold; padding:7px 16px; background:#2b6cb0; "
            "color:white; border-radius:4px;"
        )
        self._run_hecras_btn.clicked.connect(self._build_and_run_hecras)
        btns_row.addWidget(self._run_hecras_btn)

        v.addLayout(btns_row)

        # Progress bar
        self._run_progress = QProgressBar()
        self._run_progress.setRange(0, 100)
        self._run_progress.setValue(0)
        self._run_progress.setStyleSheet("QProgressBar { height:18px; }")
        self._run_progress.setVisible(False)
        v.addWidget(self._run_progress)

        # Status label
        self._run_status_lbl = QLabel("")
        self._run_status_lbl.setWordWrap(True)
        self._run_status_lbl.setStyleSheet(
            "color:#2d3748; font-size:12px; padding:2px 0px;"
        )
        self._run_status_lbl.setVisible(False)
        v.addWidget(self._run_status_lbl)

        # Results section
        self._run_results_gb = QGroupBox("Run results")
        self._run_results_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        rgl = QVBoxLayout(self._run_results_gb)
        self._run_results_inner = QVBoxLayout()
        self._run_results_inner.setSpacing(4)
        rgl.addLayout(self._run_results_inner)
        view_note = QLabel(
            "For full result visualisation, open the project in HEC-RAS / RAS Mapper."
        )
        view_note.setWordWrap(True)
        view_note.setStyleSheet("color:#718096; font-size:11px; padding-top:4px;")
        rgl.addWidget(view_note)
        self._run_results_gb.setVisible(False)
        v.addWidget(self._run_results_gb)

        v.addStretch()
        return page

    def _browse_hecras_exe(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select HEC-RAS executable", "",
            "Executable (*.exe);;All files (*)"
        )
        if path:
            self._hecras_exe_edit.setText(path)

    def _refresh_run_checklist(self):
        """Update the green/red checklist on the Build & Run tab."""
        if not hasattr(self, "_run_check_rows"):
            return

        n = len(self._features) if self._features else 0

        checks = {
            "dem":        (self._dem_summary is not None,
                           f"{n} AOI(s)" if self._dem_summary else "not ready"),
            "manning":    (self._manning_summary is not None,
                           f"{n} AOI(s)" if self._manning_summary else "not ready"),
            "flowline":   (self._flowline_summary is not None,
                           f"{n} AOI(s)" if self._flowline_summary else "not ready"),
            "flowdata":   (self._flowdata_summary is not None,
                           f"{n} AOI(s)" if self._flowdata_summary else "not ready"),
            "mesh":       (self._mesh_summary is not None,
                           f"{n} AOI(s)" if self._mesh_summary else "not ready"),
            "hecras_exe": (bool(self._hecras_exe_edit.text().strip()),
                           "set" if self._hecras_exe_edit.text().strip() else "not set"),
        }

        labels = {
            "dem": "DEM",
            "manning": "LULC & Manning",
            "flowline": "Flowline",
            "flowdata": "Flowdata",
            "mesh": "Mesh",
            "hecras_exe": "HEC-RAS executable",
        }

        for key, (ok, status) in checks.items():
            icon_lbl, text_lbl = self._run_check_rows[key]
            if ok:
                icon_lbl.setText("✓")
                icon_lbl.setStyleSheet("color:#38a169; font-weight:bold;")
            else:
                icon_lbl.setText("✗")
                icon_lbl.setStyleSheet("color:#e53e3e; font-weight:bold;")
            text_lbl.setText(f"{labels[key]} — {status}")

    def _get_simulation_start_str(self) -> str:
        dt = self._run_start_dt.dateTime().toPyDateTime()
        months = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                  "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
        return dt.strftime(f"%d{months[dt.month-1]}%Y %H:%M:%S")

    def _get_simulation_end_str(self) -> str:
        dt = self._run_end_dt.dateTime().toPyDateTime()
        months = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                  "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
        return dt.strftime(f"%d{months[dt.month-1]}%Y %H:%M:%S")

    def _generate_model_files(self):
        """Build project files without running HEC-RAS."""
        if not self._features:
            self._log("No AOI features confirmed.")
            return
        if not self._project_dir:
            self._log("No project directory set.")
            return

        self._run_progress.setVisible(True)
        self._run_status_lbl.setText(
            f"Generating model files for {len(self._features)} AOI(s)…"
        )
        self._run_status_lbl.setVisible(True)
        set_running(self._gen_files_btn)

        while self._run_results_inner.count():
            item = self._run_results_inner.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        self._run_results_gb.setVisible(False)

        feats = self._features
        fd_summary = self._flowdata_summary
        sim_start = self._get_simulation_start_str()
        sim_end = self._get_simulation_end_str()
        time_step = self._run_timestep_spin.value()
        ds_slope = self._run_slope_spin.value()

        def _run_fn(log_fn=None):
            _log = log_fn or self._log
            from core.hecras_model import build_hecras_project
            results = []
            for f in feats:
                folder = Path(f.folder_path) / "HECRAS_files"
                folder.mkdir(parents=True, exist_ok=True)

                discharge_csv = None
                if fd_summary:
                    for entry in fd_summary.get("features", []):
                        if entry.get("name") == f.name:
                            discharge_csv = entry.get("csv_path")
                            break
                if not discharge_csv:
                    discharge_csv = str(folder / "discharge.csv")

                _log(f"  Generating model files for '{f.name}' …")
                proj_summary = build_hecras_project(
                    output_dir=str(folder),
                    project_name=f.name,
                    geom_summary={},
                    discharge_csv=discharge_csv,
                    simulation_start=sim_start,
                    simulation_end=sim_end,
                    time_step_sec=time_step,
                    downstream_slope=ds_slope,
                    log_fn=_log,
                )
                proj_summary["aoi_name"] = f.name
                results.append(proj_summary)
            return {"features": results}

        self._model_worker = Worker(_run_fn)
        self._model_worker.message.connect(self._log)
        self._model_worker.finished.connect(self._on_gen_files_done)
        self._model_worker.error.connect(self._on_run_error)
        self._model_worker.start()

    def _on_gen_files_done(self, summary: dict):
        set_ready(self._gen_files_btn)
        self._run_progress.setVisible(False)
        features = summary.get("features", [])
        n = len(features)
        self._model_summary = summary
        self._run_status_lbl.setText(f"Model files generated for {n} AOI(s).")
        self._run_status_lbl.setStyleSheet("color:#276749; font-weight:bold; font-size:12px; padding:2px 0px;")
        self._run_status_lbl.setStyleSheet("color:#276749; font-weight:bold; font-size:12px; padding:2px 0px;")
        self._run_status_lbl.setVisible(True)
        self._run_build_file_results(features)

    def _run_build_file_results(self, features: list):
        for entry in features:
            name = entry.get("aoi_name", "?")
            prj = entry.get("prj_path", "")
            row = QFrame()
            row.setStyleSheet(
                "QFrame { background:#f9fafb; border:1px solid #e2e8f0; "
                "border-radius:3px; padding:3px 6px; }"
            )
            rl = QVBoxLayout(row)
            rl.setContentsMargins(6, 2, 6, 4)
            rl.setSpacing(2)
            name_lbl = QLabel(f"<b>{name}</b>")
            name_lbl.setStyleSheet("color:#2d3748;")
            rl.addWidget(name_lbl)
            if prj:
                path_lbl = QLabel(prj)
                path_lbl.setStyleSheet("color:#4a5568; font-size:10px;")
                path_lbl.setWordWrap(True)
                rl.addWidget(path_lbl)
            self._run_results_inner.addWidget(row)
        self._run_results_gb.setVisible(True)

    def _build_and_run_hecras(self):
        """Build project files and execute RasUnsteady64.exe."""
        if not self._features:
            self._log("No AOI features confirmed.")
            return
        if not self._project_dir:
            self._log("No project directory set.")
            return

        hecras_exe = self._hecras_exe_edit.text().strip()
        if not hecras_exe:
            self._log("Please set the path to RasUnsteady64.exe first.")
            self._run_status_lbl.setText(
                "Error: HEC-RAS executable path is not set."
            )
            self._run_status_lbl.setVisible(True)
            return

        self._run_progress.setVisible(True)
        self._run_status_lbl.setText(
            f"Building and running HEC-RAS for {len(self._features)} AOI(s)…"
        )
        self._run_status_lbl.setVisible(True)
        set_running(self._run_hecras_btn)

        while self._run_results_inner.count():
            item = self._run_results_inner.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        self._run_results_gb.setVisible(False)

        feats = self._features
        fd_summary = self._flowdata_summary
        sim_start = self._get_simulation_start_str()
        sim_end = self._get_simulation_end_str()
        time_step = self._run_timestep_spin.value()
        ds_slope = self._run_slope_spin.value()
        exe = hecras_exe

        def _run_fn(log_fn=None):
            _log = log_fn or self._log
            from core.hecras_model import (
                build_hecras_project,
                run_hecras,
                read_hecras_results,
            )
            results = []
            for f in feats:
                folder = Path(f.folder_path) / "HECRAS_files"
                folder.mkdir(parents=True, exist_ok=True)

                discharge_csv = None
                if fd_summary:
                    for entry in fd_summary.get("features", []):
                        if entry.get("name") == f.name:
                            discharge_csv = entry.get("csv_path")
                            break
                if not discharge_csv:
                    discharge_csv = str(folder / "discharge.csv")

                _log(f"  Building model files for '{f.name}' …")
                proj_summary = build_hecras_project(
                    output_dir=str(folder),
                    project_name=f.name,
                    geom_summary={},
                    discharge_csv=discharge_csv,
                    simulation_start=sim_start,
                    simulation_end=sim_end,
                    time_step_sec=time_step,
                    downstream_slope=ds_slope,
                    log_fn=_log,
                )

                prj_path = proj_summary["prj_path"]
                _log(f"  Running HEC-RAS for '{f.name}' …")
                run_result = run_hecras(
                    hecras_exe=exe,
                    project_prj=prj_path,
                    log_fn=_log,
                )

                entry = {
                    "aoi_name": f.name,
                    "prj_path": prj_path,
                    "elapsed_s": run_result.get("elapsed_s", 0),
                }
                results_hdf = str(folder / f"{f.name}.p01.hdf")
                if Path(results_hdf).exists():
                    try:
                        res = read_hecras_results(results_hdf, area_name=f.name)
                        entry["max_depth"] = float(res["max_depth"].max())
                        entry["max_velocity"] = float(res["max_velocity"].max())
                    except Exception as ex:
                        _log(f"  Could not read results HDF: {ex}")
                results.append(entry)
            return {"features": results}

        self._run_worker = Worker(_run_fn)
        self._run_worker.message.connect(self._log)
        self._run_worker.finished.connect(self._on_run_done)
        self._run_worker.error.connect(self._on_run_error)
        self._run_worker.start()

    def _on_run_done(self, summary: dict):
        set_ready(self._run_hecras_btn)
        self._run_progress.setVisible(False)
        features = summary.get("features", [])
        n = len(features)
        self._model_summary = summary
        self._run_status_lbl.setText(f"HEC-RAS completed for {n} AOI(s).")
        self._run_status_lbl.setStyleSheet("color:#276749; font-weight:bold; font-size:12px; padding:2px 0px;")
        self._run_status_lbl.setStyleSheet("color:#276749; font-weight:bold; font-size:12px; padding:2px 0px;")
        self._run_status_lbl.setVisible(True)
        self._run_build_run_results(features)

    def _on_run_error(self, msg: str):
        set_ready(self._gen_files_btn)
        set_ready(self._run_hecras_btn)
        self._run_progress.setVisible(False)
        self._log(f"ERROR: {msg}")
        self._run_status_lbl.setText(f"Error: {msg.splitlines()[0]}")
        self._run_status_lbl.setVisible(True)

    def _run_build_run_results(self, features: list):
        for entry in features:
            name = entry.get("aoi_name", "?")
            elapsed = entry.get("elapsed_s", 0)
            max_d = entry.get("max_depth")
            max_v = entry.get("max_velocity")

            row = QFrame()
            row.setStyleSheet(
                "QFrame { background:#f9fafb; border:1px solid #e2e8f0; "
                "border-radius:3px; padding:3px 6px; }"
            )
            rl = QVBoxLayout(row)
            rl.setContentsMargins(6, 2, 6, 4)
            rl.setSpacing(2)

            name_lbl = QLabel(f"<b>{name}</b>  — {elapsed:.1f} s")
            name_lbl.setStyleSheet("color:#2d3748;")
            rl.addWidget(name_lbl)

            if max_d is not None:
                depth_lbl = QLabel(
                    f"Max depth: {max_d:.2f} m   |   Max velocity: {max_v:.2f} m/s"
                )
                depth_lbl.setStyleSheet("color:#4a5568; font-size:11px;")
                rl.addWidget(depth_lbl)

            self._run_results_inner.addWidget(row)

        self._run_results_gb.setVisible(True)

    # ── Reset ─────────────────────────────────────────────────────────────────

    def reset(self):
        """Reset all state and return to tab 0."""
        self._project_dir = None
        self._features = []
        self._dem_summary = None
        self._manning_summary = None
        self._flowline_summary = None
        self._flowdata_summary = None
        self._mesh_summary = None
        self._model_summary = None

        if hasattr(self._proj, "reset"):
            self._proj.reset()
        self._aoi.reset()

        # Clear DEM cards
        for card in list(self._dem_cards):
            card.setParent(None)
            card.deleteLater()
        self._dem_cards = []

        # Clear LULC cards
        for card in list(self._lulc_cards):
            card.setParent(None)
            card.deleteLater()
        self._lulc_cards = []

        # Clear flowline cards
        for card in list(self._flowline_cards):
            card.setParent(None)
            card.deleteLater()
        self._flowline_cards = []

        # Clear flowdata cards
        for card in list(self._flowdata_cards):
            card.setParent(None)
            card.deleteLater()
        self._flowdata_cards = []

        # Clear mesh cards
        for card in list(self._mesh_cards):
            card.setParent(None)
            card.deleteLater()
        self._mesh_cards = []

        # Reset DEM tab UI
        self._dem_progress.setValue(0)
        self._dem_progress.setVisible(False)
        self._dem_status_lbl.setVisible(False)
        while self._dem_results_inner.count():
            item = self._dem_results_inner.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        self._dem_results_gb.setVisible(False)
        self._dem_preview_gb.setVisible(False)
        self._dem_preview_2col.setVisible(False)
        try:
            set_ready(self._dem_run_btn)
        except Exception:
            pass

        # Reset LULC tab UI
        self._lulc_progress.setValue(0)
        self._lulc_progress.setVisible(False)
        self._lulc_status_lbl.setVisible(False)
        self._lulc_clear_results()
        try:
            set_ready(self._lulc_run_btn)
        except Exception:
            pass

        # Reset Flowline tab UI
        self._fl_progress.setVisible(False)
        self._fl_status_lbl.setVisible(False)
        self._fl_preview_gb.setVisible(False)
        self._fl_canvas.setVisible(False)
        self._fl_canvas.clear()

        # Reset Flowdata tab UI
        self._fd_progress.setVisible(False)
        self._fd_status_lbl.setVisible(False)
        self._fd_results_gb.setVisible(False)
        self._fd_hydro_canvas.setVisible(False)
        while self._fd_results_inner.count():
            item = self._fd_results_inner.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        try:
            set_ready(self._fd_run_btn)
        except Exception:
            pass

        # Reset Mesh tab UI
        self._mesh_progress.setValue(0)
        self._mesh_progress.setVisible(False)
        self._mesh_status_lbl.setVisible(False)
        while self._mesh_results_inner.count():
            item = self._mesh_results_inner.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        self._mesh_results_gb.setVisible(False)
        try:
            set_ready(self._mesh_run_btn)
        except Exception:
            pass

        # Reset Build & Run tab UI
        self._run_progress.setValue(0)
        self._run_progress.setVisible(False)
        self._run_status_lbl.setVisible(False)
        while self._run_results_inner.count():
            item = self._run_results_inner.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        self._run_results_gb.setVisible(False)
        try:
            set_ready(self._gen_files_btn)
            set_ready(self._run_hecras_btn)
        except Exception:
            pass
        self._refresh_run_checklist()

        self._tabs.setCurrentIndex(0)
