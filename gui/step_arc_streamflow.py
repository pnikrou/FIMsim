"""ARC-Curve2Flood Step 6 — Streamflow (per-AOI flow files from NWM).

Multi-AOI controller matching the other steps:
  * 1 AOI   → one ArcFlowConfigPanel embedded directly.
  * >1 AOIs → accordion of AOIArcFlowCard widgets (Edit / Remove chrome)
              with "Apply current AOI's settings to all" — each AOI keeps its
              own event window, since floods happen at different times.

Output per AOI:
  <AOI>/arc-files/flow.csv  (COMID,base,max) — ARC builds a rating curve per
  reach and Curve2Flood maps the 'max' (peak) flow.
"""
import csv
import re
from pathlib import Path
from typing import List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QGroupBox, QProgressBar, QScrollArea, QStackedWidget, QMessageBox,
    QComboBox, QDateEdit, QDoubleSpinBox, QSpinBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
)
from PyQt6.QtCore import pyqtSignal, Qt, QDate

from core.arc_orchestrate import run_arc_flowfile_for_all_aois
from gui.worker import Worker
from gui.run_button import set_running, set_ready

_FLOW_STEP_RE = re.compile(r"^▶\s+Flow file\s+\[(\d+)/(\d+)\]")
_FLOW_DONE_RE = re.compile(r"^✓\s+Flow file\s+\[(\d+)/(\d+)\]\s+finished")

_F_RANGES = ["short_range", "medium_range", "long_range"]
_F_CYCLES = ["Auto", "00", "06", "12", "18"]


# ── Config panel (shared by single-AOI page and each card) ────────────────────

class ArcFlowConfigPanel(QWidget):
    """NWM source + event window + baseflow percentile for ONE AOI."""

    config_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)

        src_row = QHBoxLayout()
        src_row.addWidget(QLabel("Streamflow source:"))
        self._src_combo = QComboBox()
        self._src_combo.addItem("NWM Retrospective (1979-2023)", "nwm_retro")
        self._src_combo.addItem("NWM Forecast (2019-now)", "nwm_forecast")
        self._src_combo.setFixedWidth(250)
        self._src_combo.currentIndexChanged.connect(self._on_src_changed)
        src_row.addWidget(self._src_combo)
        src_row.addStretch()
        layout.addLayout(src_row)

        # Retrospective event window (per AOI — floods differ per AOI)
        self._retro_row = QWidget()
        rr = QHBoxLayout(self._retro_row)
        rr.setContentsMargins(0, 0, 0, 0)
        rr.addWidget(QLabel("Event window:"))
        self._start = QDateEdit(QDate(2017, 8, 25))
        self._start.setCalendarPopup(True)
        self._start.setDisplayFormat("yyyy-MM-dd")
        self._start.dateChanged.connect(lambda *_: self.config_changed.emit())
        rr.addWidget(self._start)
        rr.addWidget(QLabel("to"))
        self._end = QDateEdit(QDate(2017, 9, 1))
        self._end.setCalendarPopup(True)
        self._end.setDisplayFormat("yyyy-MM-dd")
        self._end.dateChanged.connect(lambda *_: self.config_changed.emit())
        rr.addWidget(self._end)
        rr.addStretch()
        layout.addWidget(self._retro_row)

        # Forecast controls
        self._fcst_row = QWidget()
        fr = QHBoxLayout(self._fcst_row)
        fr.setContentsMargins(0, 0, 0, 0)
        fr.addWidget(QLabel("Issue date:"))
        self._fdate = QDateEdit(QDate.currentDate())
        self._fdate.setCalendarPopup(True)
        self._fdate.setDisplayFormat("yyyy-MM-dd")
        self._fdate.dateChanged.connect(lambda *_: self.config_changed.emit())
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
        layout.addWidget(self._fcst_row)

        # Baseflow — ARC carves the channel to convey this flow before any
        # water goes overbank, so it must be a long-term (climatological)
        # statistic, not a statistic of the flood window.
        bf_row = QHBoxLayout()
        bf_row.addWidget(QLabel("Baseflow (channel-forming):"))
        self._base_mode = QComboBox()
        self._base_mode.addItem("Long-term climatology (recommended)", "longterm")
        self._base_mode.addItem("From the event window only", "window")
        self._base_mode.setFixedWidth(250)
        self._base_mode.setToolTip(
            "ARC uses the baseflow column as the channel-forming (bankfull) "
            "discharge and carves the channel deep enough to convey it.\n\n"
            "Long-term climatology: median of a multi-year NWM retrospective "
            "record (what ARC's own tooling does).\n\n"
            "Event window only: median of your selected dates — this is much "
            "higher during a flood, over-carves the channel, and badly "
            "UNDER-predicts inundated area.")
        self._base_mode.currentIndexChanged.connect(self._on_base_mode_changed)
        bf_row.addWidget(self._base_mode)
        bf_row.addStretch()
        layout.addLayout(bf_row)

        bp_row = QHBoxLayout()
        bp_row.addWidget(QLabel("Baseflow percentile:"))
        self._base_pct = QDoubleSpinBox()
        self._base_pct.setRange(0.0, 90.0)
        self._base_pct.setValue(50.0)
        self._base_pct.setSuffix(" %")
        self._base_pct.setFixedWidth(90)
        self._base_pct.setToolTip(
            "Which percentile of the record is used as baseflow. ARC's own "
            "tooling uses the median (50%); the peak of the event window is "
            "always used as the flood flow.")
        self._base_pct.valueChanged.connect(lambda *_: self.config_changed.emit())
        bp_row.addWidget(self._base_pct)
        bp_row.addWidget(QLabel("over"))
        self._base_years = QSpinBox()
        self._base_years.setRange(1, 40)
        self._base_years.setValue(10)
        self._base_years.setSuffix(" yr")
        self._base_years.setFixedWidth(80)
        self._base_years.valueChanged.connect(lambda *_: self.config_changed.emit())
        bp_row.addWidget(self._base_years)
        bp_row.addStretch()
        layout.addLayout(bp_row)

        self._bp_hint = QLabel(
            "★ 50 % = median, ARC's convention. The flood flow is always the "
            "peak of the event window above.")
        self._bp_hint.setWordWrap(True)
        self._bp_hint.setStyleSheet("color:#718096; font-size:11px;")
        layout.addWidget(self._bp_hint)

        self._on_src_changed()
        self._on_base_mode_changed()

    def _on_base_mode_changed(self, *_):
        longterm = (self._base_mode.currentData() == "longterm")
        self._base_years.setVisible(longterm)
        if longterm:
            self._bp_hint.setText(
                "★ 50 % = median, ARC's convention. Baseflow is taken from a "
                "multi-year NWM record (an extra, quick daily download). The "
                "flood flow is always the peak of the event window above.")
            self._bp_hint.setStyleSheet("color:#718096; font-size:11px;")
        else:
            self._bp_hint.setText(
                "⚠ Baseflow from the flood window is much higher than the true "
                "channel-forming flow — ARC over-carves the channel and the "
                "flood map will show far too little inundated area.")
            self._bp_hint.setStyleSheet("color:#c05621; font-size:11px;")
        self.config_changed.emit()

    def _on_src_changed(self, *_):
        is_retro = (self._src_combo.currentData() == "nwm_retro")
        self._retro_row.setVisible(is_retro)
        self._fcst_row.setVisible(not is_retro)
        self.config_changed.emit()

    def is_ready(self) -> bool:
        return True

    def get_config(self) -> dict:
        cfg = {"base_percentile": float(self._base_pct.value()),
               "baseflow_mode": self._base_mode.currentData(),
               "baseflow_years": int(self._base_years.value())}
        if self._src_combo.currentData() == "nwm_retro":
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

    def set_config(self, cfg: dict):
        cfg = cfg or {}
        src = cfg.get("source", "nwm_retro")
        idx = self._src_combo.findData(src)
        self._src_combo.setCurrentIndex(max(idx, 0))
        if cfg.get("start_dt"):
            self._start.setDate(QDate.fromString(cfg["start_dt"], "yyyy-MM-dd"))
        if cfg.get("end_dt"):
            self._end.setDate(QDate.fromString(cfg["end_dt"], "yyyy-MM-dd"))
        if cfg.get("forecast_date"):
            self._fdate.setDate(
                QDate.fromString(cfg["forecast_date"], "yyyy-MM-dd"))
        if cfg.get("forecast_range") in _F_RANGES:
            self._frange.setCurrentText(cfg["forecast_range"])
        fh = cfg.get("forecast_hour")
        self._fcycle.setCurrentText("Auto" if fh is None else f"{int(fh):02d}")
        if cfg.get("base_percentile") is not None:
            self._base_pct.setValue(float(cfg["base_percentile"]))
        bm = cfg.get("baseflow_mode")
        if bm:
            i = self._base_mode.findData(bm)
            if i >= 0:
                self._base_mode.setCurrentIndex(i)
        if cfg.get("baseflow_years"):
            self._base_years.setValue(int(cfg["baseflow_years"]))
        self._on_base_mode_changed()


# ── Per-AOI card (mirrors AOIDEMCard chrome) ──────────────────────────────────

class AOIArcFlowCard(QFrame):
    expand_requested = pyqtSignal(object)
    config_changed   = pyqtSignal(object)
    remove_requested = pyqtSignal(object)

    EXPANDED_STYLE = (
        "QFrame#card { background:#f9fafb; border:2px solid #a0aec0; "
        "border-radius:6px; padding:8px; }"
    )
    COLLAPSED_STYLE = (
        "QFrame#card { background:#f9fafb; border:1px solid #e2e8f0; "
        "border-radius:6px; padding:6px; }"
    )

    def __init__(self, aoi_name: str, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self._aoi_name = aoi_name
        self._expanded = False
        self._build_ui()
        self.setStyleSheet(self.COLLAPSED_STYLE)
        self._refresh_status()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 2)
        outer.setSpacing(6)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)

        self._caret = QLabel("▶")
        self._caret.setFixedWidth(14)
        self._caret.setStyleSheet("color:#4a5568; font-weight:bold;")
        header.addWidget(self._caret)

        self._name_lbl = QLabel(f"<b>{self._aoi_name}</b>")
        self._name_lbl.setStyleSheet("color:#2d3748;")
        header.addWidget(self._name_lbl)
        header.addStretch()

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet("color:#666; font-size:11px;")
        header.addWidget(self._status_lbl)

        self._toggle_btn = QPushButton("Edit")
        self._toggle_btn.setFixedWidth(80)
        self._toggle_btn.clicked.connect(self._on_toggle_clicked)
        header.addWidget(self._toggle_btn)

        self._remove_btn = QPushButton("Remove")
        self._remove_btn.setFixedWidth(70)
        self._remove_btn.setStyleSheet(
            "background:#e53e3e; color:white; border-radius:3px; "
            "font-size:11px; padding:2px 4px;"
        )
        self._remove_btn.setToolTip(f"Remove {self._aoi_name} from this run")
        self._remove_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        header.addWidget(self._remove_btn)

        outer.addLayout(header)

        self._panel = ArcFlowConfigPanel(self)
        self._panel.setVisible(False)
        self._panel.config_changed.connect(self._forward_config_changed)
        outer.addWidget(self._panel)

    # ── expand / collapse ─────────────────────────────────────────────────────

    def is_expanded(self) -> bool:
        return self._expanded

    def expand(self):
        if self._expanded:
            return
        self._expanded = True
        self._panel.setVisible(True)
        self._toggle_btn.setText("Done")
        self._caret.setText("▼")
        self.setStyleSheet(self.EXPANDED_STYLE)

    def collapse(self):
        if not self._expanded:
            return
        self._expanded = False
        self._panel.setVisible(False)
        self._toggle_btn.setText("Edit")
        self._caret.setText("▶")
        self.setStyleSheet(self.COLLAPSED_STYLE)
        self._refresh_status()

    def _on_toggle_clicked(self):
        if self._expanded:
            self.collapse()
        else:
            self.expand_requested.emit(self)

    # ── status line ───────────────────────────────────────────────────────────

    def _refresh_status(self):
        cfg = self._panel.get_config()
        if cfg["source"] == "nwm_retro":
            src = (f"<i>NWM Retro:</i> {cfg.get('start_dt')} → "
                   f"{cfg.get('end_dt')}")
        else:
            cyc = cfg.get("forecast_hour")
            cyc_txt = "auto" if cyc is None else f"t{cyc:02d}z"
            src = (f"<i>NWM Forecast:</i> {cfg.get('forecast_date')} "
                   f"({cfg.get('forecast_range')}, {cyc_txt})")
        bm = ("long-term" if cfg.get("baseflow_mode") == "longterm"
              else "window")
        self._status_lbl.setText(
            f"{src} &nbsp;·&nbsp; <i>base:</i> p{cfg.get('base_percentile'):g} "
            f"({bm})")

    def _forward_config_changed(self):
        self._refresh_status()
        self.config_changed.emit(self)

    # ── public proxies ────────────────────────────────────────────────────────

    def panel(self) -> ArcFlowConfigPanel:
        return self._panel

    def is_ready(self) -> bool:
        return self._panel.is_ready()

    def get_config(self) -> dict:
        return self._panel.get_config()

    def set_config(self, cfg: dict):
        self._panel.set_config(cfg)
        self._refresh_status()


# ── Step widget ───────────────────────────────────────────────────────────────

class StepArcStreamflowWidget(QWidget):
    step_completed = pyqtSignal(dict)

    def __init__(self, log_fn=print, parent=None):
        super().__init__(parent)
        self._log = log_fn
        self._worker = None
        self._ctx_path = None
        self._ctx = None
        self._aoi_features: list = []
        self._cards: List[AOIArcFlowCard] = []
        self._cards_layout: QVBoxLayout = None
        self._stack: QStackedWidget = None
        self._single_panel: ArcFlowConfigPanel = None
        self._setup_ui()

    # ── public API ────────────────────────────────────────────────────────────

    def set_context(self, ctx_path, ctx):
        self._ctx_path = ctx_path
        self._ctx = ctx or {}
        self._aoi_features = list(self._ctx.get("aoi_features", []) or [])
        self._clear_results()
        self._rebuild_for_aoi_count()

    def reset(self):
        self._aoi_features = []
        self._clear_cards()
        self._clear_results()
        self._error_lbl.setVisible(False)
        self._progress.setValue(0)
        self._progress.setVisible(False)
        self._status_lbl.setVisible(False)
        self._stack.setCurrentIndex(0)

    # ── UI construction ───────────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        self._aoi_count_lbl = QLabel("")
        self._aoi_count_lbl.setStyleSheet(
            "padding:6px 10px; background:#f7fafc; border:1px solid #cbd5e0; "
            "border-radius:4px; color:#2d3748; font-size:11px;"
        )
        self._aoi_count_lbl.setWordWrap(True)
        self._aoi_count_lbl.setVisible(False)
        layout.addWidget(self._aoi_count_lbl)

        self._stack = QStackedWidget()
        layout.addWidget(self._stack)

        # Page 0 — single-AOI form
        single_page = QWidget()
        sp_layout = QVBoxLayout(single_page)
        sp_layout.setContentsMargins(0, 0, 0, 0)
        gb = QGroupBox("6. Streamflow")
        gb_layout = QVBoxLayout(gb)
        self._single_panel = ArcFlowConfigPanel(self)
        self._single_panel.config_changed.connect(self._on_single_config_changed)
        gb_layout.addWidget(self._single_panel)
        sp_layout.addWidget(gb)
        sp_layout.addStretch()
        self._stack.addWidget(single_page)

        # Page 1 — multi-AOI accordion
        multi_page = QWidget()
        mp_layout = QVBoxLayout(multi_page)
        mp_layout.setContentsMargins(0, 0, 0, 0)

        top_row = QHBoxLayout()
        self._apply_all_btn = QPushButton("Apply current AOI's settings to all")
        self._apply_all_btn.setStyleSheet(
            "background:#2b6cb0; color:white; padding:6px 14px; "
            "border-radius:3px; font-weight:bold;"
        )
        self._apply_all_btn.setToolTip(
            "Copy the currently expanded AOI's streamflow configuration "
            "to every other AOI in this list."
        )
        self._apply_all_btn.clicked.connect(self._apply_to_all)
        self._apply_all_btn.setEnabled(False)
        top_row.addStretch()
        top_row.addWidget(self._apply_all_btn)
        mp_layout.addLayout(top_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        cards_host = QWidget()
        self._cards_layout = QVBoxLayout(cards_host)
        self._cards_layout.setSpacing(6)
        self._cards_layout.addStretch()
        scroll.setWidget(cards_host)
        mp_layout.addWidget(scroll, 1)
        self._stack.addWidget(multi_page)

        # Run button
        btn_row = QHBoxLayout()
        self._run_btn = QPushButton("Build flow file")
        self._run_btn.setStyleSheet(
            "font-weight:bold; padding:7px 20px; background:#2b6cb0; "
            "color:white; border-radius:4px;"
        )
        self._run_btn.clicked.connect(self._run_step)
        self._run_btn.setVisible(False)
        btn_row.addWidget(self._run_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Progress + status + error
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setVisible(False)
        self._progress.setStyleSheet("QProgressBar { height: 18px; }")
        layout.addWidget(self._progress)

        self._status_lbl = QLabel("")
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setStyleSheet(
            "color:#276749; font-weight:bold; font-size:12px; padding:2px 0px;"
        )
        self._status_lbl.setVisible(False)
        layout.addWidget(self._status_lbl)

        self._error_lbl = QLabel("")
        self._error_lbl.setWordWrap(True)
        self._error_lbl.setStyleSheet(
            "padding:10px; background:#fff5f5; border:1px solid #fc8181; "
            "border-radius:4px; font-size:12px; color:#c53030;"
        )
        self._error_lbl.setVisible(False)
        layout.addWidget(self._error_lbl)

        # Post-run results: clickable AOI list
        self._results_gb = QGroupBox(
            "Per-AOI flow files  —  click an AOI to preview its flow table"
        )
        self._results_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        rgl = QVBoxLayout(self._results_gb)
        self._results_inner = QVBoxLayout()
        self._results_inner.setSpacing(0)
        rgl.addLayout(self._results_inner)
        self._results_gb.setVisible(False)
        layout.addWidget(self._results_gb)

        # Preview: the flow.csv table (COMID, base, max)
        self._gb_preview = QGroupBox("Flow file preview")
        self._gb_preview.setStyleSheet("QGroupBox { font-weight:bold; }")
        self._gb_preview.setMinimumHeight(320)
        pv = QVBoxLayout(self._gb_preview)
        pv.setSpacing(6)
        pv.setContentsMargins(6, 8, 6, 6)

        self._preview_placeholder = QLabel(
            "<i>Click an AOI above to preview its flow file (baseflow and "
            "peak flow per reach).</i>"
        )
        self._preview_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_placeholder.setStyleSheet(
            "color:#888; padding:30px; background:#fafafa; "
            "border:1px dashed #cbd5e0; border-radius:4px;"
        )
        pv.addWidget(self._preview_placeholder)

        self._flow_title_lbl = QLabel("")
        self._flow_title_lbl.setStyleSheet("color:#22543d; font-size:10px;")
        self._flow_title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._flow_title_lbl.setVisible(False)
        pv.addWidget(self._flow_title_lbl)

        self._flow_table = QTableWidget(0, 3)
        self._flow_table.setHorizontalHeaderLabels(
            ["COMID (reach)", "Baseflow (m³/s)", "Peak flow (m³/s)"]
        )
        self._flow_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._flow_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._flow_table.setAlternatingRowColors(True)
        self._flow_table.verticalHeader().setVisible(False)
        self._flow_table.verticalHeader().setDefaultSectionSize(20)
        self._flow_table.setSortingEnabled(True)
        self._flow_table.setStyleSheet(
            "QTableWidget { font-size: 10px; }"
            "QHeaderView::section { font-size: 10px; padding: 2px; }"
        )
        h = self._flow_table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._flow_table.setVisible(False)
        pv.addWidget(self._flow_table, 1)

        self._gb_preview.setVisible(False)
        layout.addWidget(self._gb_preview, 1)

        layout.addStretch()

    # ── Layout switching ──────────────────────────────────────────────────────

    def _rebuild_for_aoi_count(self):
        n = len(self._aoi_features)
        if n == 0:
            self._aoi_count_lbl.setText(
                "<i>No AOIs confirmed yet — go back to the AOI step first.</i>"
            )
            self._aoi_count_lbl.setVisible(True)
            self._stack.setCurrentIndex(0)
            self._run_btn.setVisible(False)
            return
        if n == 1:
            self._aoi_count_lbl.setText("<b>1</b> AOI confirmed.")
            self._aoi_count_lbl.setVisible(True)
            self._stack.setCurrentIndex(0)
            self._run_btn.setText("Build flow file")
            self._run_btn.setVisible(True)
            return
        self._aoi_count_lbl.setText(
            f"<b>{n}</b> AOI(s) confirmed — set each AOI's streamflow source "
            "and event window below (flood dates can differ per AOI).  Click "
            "an AOI to expand its settings."
        )
        self._aoi_count_lbl.setVisible(True)
        self._stack.setCurrentIndex(1)
        self._run_btn.setText("Build flow files for all")
        self._build_cards()

    def _clear_cards(self):
        for c in list(self._cards):
            c.setParent(None)
            c.deleteLater()
        self._cards.clear()

    def _build_cards(self):
        self._clear_cards()
        for feat in self._aoi_features:
            card = AOIArcFlowCard(feat.get("name", "(unnamed)"), self)
            card.expand_requested.connect(self._on_expand_requested)
            card.config_changed.connect(self._on_card_config_changed)
            card.remove_requested.connect(self._on_remove_requested)
            self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)
            self._cards.append(card)
        self._on_card_config_changed(None)

    def _on_remove_requested(self, card):
        idx = self._cards.index(card) if card in self._cards else -1
        if idx < 0:
            return
        aoi_name = (self._aoi_features[idx].get("name", f"AOI {idx+1}")
                    if idx < len(self._aoi_features) else "this AOI")
        reply = QMessageBox.question(
            self, "Remove AOI",
            f"Remove <b>{aoi_name}</b> from this step?\n\n"
            "The AOI's data folder is NOT deleted — only removed from the current run.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._cards.pop(idx)
        if idx < len(self._aoi_features):
            self._aoi_features.pop(idx)
        card.setParent(None)
        card.deleteLater()
        self._on_card_config_changed(None)
        self._aoi_count_lbl.setText(
            f"<b>{len(self._aoi_features)}</b> AOI(s) remaining."
        )

    # ── Accordion ─────────────────────────────────────────────────────────────

    def _on_expand_requested(self, card: AOIArcFlowCard):
        for c in self._cards:
            if c is card:
                c.expand()
            else:
                c.collapse()
        self._apply_all_btn.setEnabled(True)

    def _expanded_card(self) -> Optional[AOIArcFlowCard]:
        for c in self._cards:
            if c.is_expanded():
                return c
        return None

    def _on_card_config_changed(self, _card):
        all_ready = bool(self._cards) and all(c.is_ready() for c in self._cards)
        self._run_btn.setVisible(all_ready)

    def _on_single_config_changed(self):
        if self._stack.currentIndex() == 0 and len(self._aoi_features) <= 1:
            self._run_btn.setVisible(self._single_panel.is_ready())

    def _apply_to_all(self):
        src = self._expanded_card()
        if src is None:
            return
        cfg = src.get_config()
        for c in self._cards:
            if c is not src:
                c.set_config(cfg)
        self._on_card_config_changed(None)

    # ── Run ───────────────────────────────────────────────────────────────────

    def _run_step(self):
        if not self._ctx_path or not self._ctx:
            self._log("Complete earlier steps first.")
            return
        if not self._aoi_features:
            QMessageBox.warning(
                self, "No AOI Confirmed",
                "No AOIs are confirmed.\n\n"
                "Go back to the AOI step and confirm at least one feature first."
            )
            return

        if len(self._aoi_features) <= 1:
            per_aoi = [self._single_panel.get_config()]
        else:
            per_aoi = [c.get_config() for c in self._cards]

        self._error_lbl.setVisible(False)
        self._clear_results()
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._status_lbl.setText("Downloading NWM discharge …")
        self._status_lbl.setVisible(True)
        set_running(self._run_btn)

        self._worker = Worker(
            run_arc_flowfile_for_all_aois,
            ctx_path=self._ctx_path, ctx=self._ctx,
            per_aoi_configs=per_aoi,
        )
        self._worker.message.connect(self._on_message)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    # ── progress / log ────────────────────────────────────────────────────────

    def _on_message(self, msg):
        self._log(msg)
        m = _FLOW_STEP_RE.match(msg)
        if m:
            i, total = int(m.group(1)), int(m.group(2))
            self._status_lbl.setText(
                f"Building flow file {i} / {total} (NWM download) …")
            self._status_lbl.setVisible(True)
            return
        m = _FLOW_DONE_RE.match(msg)
        if m:
            i, total = int(m.group(1)), int(m.group(2))
            self._progress.setValue(int(i / max(total, 1) * 100))

    def _on_done(self, ctx):
        self._error_lbl.setVisible(False)
        self._ctx = ctx
        self._progress.setValue(100)
        n = max(len(self._aoi_features), 1)
        self._status_lbl.setText(f"All {n} AOI(s) processed.")
        self._status_lbl.setVisible(True)
        set_ready(self._run_btn)
        self._build_results(ctx)
        self.step_completed.emit({"ctx_path": self._ctx_path, "ctx": ctx})

    def _on_error(self, msg):
        self._log(f"ERROR: {msg}")
        self._progress.setVisible(False)
        set_ready(self._run_btn)
        first_line = msg.split("\n")[0]
        self._error_lbl.setText(
            f"<b>Error:</b> {first_line}<br>"
            "<small>(See log panel below for full details)</small>"
        )
        self._error_lbl.setVisible(True)

    # ── results + preview ───────────────────────────────────────────────────────

    def _clear_results(self):
        if not hasattr(self, "_results_inner"):
            return
        while self._results_inner.count():
            item = self._results_inner.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        if hasattr(self, "_results_gb"):
            self._results_gb.setVisible(False)
        if hasattr(self, "_gb_preview"):
            self._gb_preview.setVisible(False)
            self._preview_placeholder.setVisible(True)
            self._flow_table.setVisible(False)
            self._flow_title_lbl.setVisible(False)

    def _build_results(self, ctx):
        self._clear_results()
        per_aoi = ctx.get("arc_flowfile_per_aoi", []) or []
        if not per_aoi:
            fc = ctx.get("arc_flow_csv")
            if fc:
                per_aoi = [{
                    "name":     ctx.get("aoi_name", "AOI"),
                    "flow_csv": fc,
                    "reaches":  ctx.get("arc_flow_reaches"),
                    "source":   ctx.get("arc_flow_source", "nwm_retro"),
                }]
        if not per_aoi:
            return

        for entry in per_aoi:
            name = entry.get("name", "?")
            row = QFrame()
            row.setStyleSheet(
                "QFrame { background:#f9fafb; border:1px solid #e2e8f0; "
                "border-radius:3px; padding:3px 6px; }"
                "QFrame:hover { background:#f0f2f5; }"
            )
            rl = QHBoxLayout(row)
            rl.setContentsMargins(6, 2, 6, 2)
            rl.setSpacing(8)
            if entry.get("failed"):
                lbl = QLabel(f"✗  <b>{name}</b> — {entry.get('error', 'failed')}")
                lbl.setStyleSheet("color:#c53030; font-size:11px;")
                lbl.setWordWrap(True)
                rl.addWidget(lbl, 1)
                self._results_inner.addWidget(row)
                continue
            btn = QPushButton(f"  {name}")
            btn.setStyleSheet(
                "QPushButton { text-align:left; background:transparent; "
                "border:none; color:#2d3748; font-weight:bold; padding:2px; }"
                "QPushButton:hover { color:#1a202c; text-decoration:underline; }"
            )
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(
                lambda _checked, e=entry: self._show_preview_for(e)
            )
            rl.addWidget(btn, 1)
            src_txt = ("forecast" if entry.get("source") == "nwm_forecast"
                       else "retrospective")
            hint = QLabel(
                f"<small>{entry.get('reaches')} reach(es) · NWM {src_txt}</small>")
            hint.setStyleSheet("color:#718096; font-size:10px;")
            rl.addWidget(hint)
            self._results_inner.addWidget(row)

        self._results_gb.setVisible(True)
        self._gb_preview.setVisible(True)
        self._preview_placeholder.setVisible(True)
        self._flow_table.setVisible(False)
        self._flow_title_lbl.setVisible(False)

    def _show_preview_for(self, entry: dict):
        fc = entry.get("flow_csv")
        if not fc or not Path(fc).exists():
            self._preview_placeholder.setText(
                f"<span style='color:#c53030;'>Flow file not found: {fc}</span>"
            )
            self._preview_placeholder.setVisible(True)
            self._flow_table.setVisible(False)
            self._flow_title_lbl.setVisible(False)
            return

        rows = []
        try:
            with open(fc, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                header = next(reader, None)
                for r in reader:
                    if len(r) >= 3:
                        rows.append((r[0], r[1], r[2]))
        except Exception as ex:
            self._preview_placeholder.setText(
                f"<span style='color:#c53030;'>Could not read {fc}: {ex}</span>"
            )
            self._preview_placeholder.setVisible(True)
            return

        self._flow_table.setSortingEnabled(False)
        self._flow_table.setRowCount(len(rows))
        for r, (comid, base, mx) in enumerate(rows):
            for col, val in enumerate((comid, base, mx)):
                it = QTableWidgetItem()
                try:
                    it.setData(Qt.ItemDataRole.DisplayRole, float(val))
                except (TypeError, ValueError):
                    it.setText(str(val))
                it.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self._flow_table.setItem(r, col, it)
        self._flow_table.setSortingEnabled(True)
        self._flow_table.sortByColumn(2, Qt.SortOrder.DescendingOrder)

        self._flow_title_lbl.setText(
            f"<b>flow.csv — {entry.get('name')}</b> "
            f"({len(rows)} reaches; sorted by peak flow)")
        self._preview_placeholder.setVisible(False)
        self._flow_title_lbl.setVisible(True)
        self._flow_table.setVisible(True)
