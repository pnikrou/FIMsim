"""
Author: Supath Dhital (sdhital@crimson.ua.edu)
Date updated: June 2026

FIMserv (OWP HAND FIM) standalone mode — 4-step wizard.

Tabs:
  1. Input     — project + AOI file OR HUC8 ID(s); map preview of the HUC8 run
                 area and the AOI; HUC8 IDs resolved here.
  2. Download  — download the OWP HAND HUC8 rasters (model runs per HUC8 region).
  3. Streamflow— NWM discharge (retrospective before 2023, forecast after); a
                 start/end range draws a hydrograph, a single event date does not.
  4. FIM       — generate the flood inundation map and show it with the AOI.
"""

from pathlib import Path
from typing import Optional, List, Dict

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QTabWidget, QProgressBar, QGroupBox, QRadioButton,
    QLineEdit, QDateTimeEdit, QComboBox, QCheckBox, QFileDialog,
    QListWidget, QListWidgetItem, QAbstractItemView,
    QButtonGroup, QMessageBox,
)
from PyQt6.QtCore import pyqtSignal, Qt, QDateTime
from PyQt6.QtGui import QFont

from gui.step_project import StepProjectWidget
from gui.run_button import set_running, set_ready
from gui.worker import Worker
from gui.map_viewer import USMapCanvas
from gui.hydrograph_preview import HydrographPreviewCanvas
from gui.raster_preview import RasterPreviewCanvas
from core.state_lookup import detect_us_state
from core.FIMserv_api import (
    FIMservAPI,
    resolve_huc8_mode, download_huc8_mode, streamflow_mode, generate_fim_mode,
    discover_existing,
)


# Shared section style so every group box looks the same.
_GB_STYLE = (
    "QGroupBox { background:#f9fafb; border:1px solid #e2e8f0; "
    "border-radius:6px; padding-top:8px; }"
)
_NOTE_STYLE = "color:#718096; font-size:11px;"
_RUN_STYLE = (
    "font-weight:bold; padding:8px 22px; background:#276749; "
    "color:white; border-radius:4px; font-size:13px;"
)


class ModeFIMservWidget(QWidget):
    mode_finished = pyqtSignal()
    nav_changed   = pyqtSignal(int, int)

    def __init__(self, log_fn, parent=None):
        super().__init__(parent)
        self._log = log_fn
        # One shared state dict carried across the four tabs.
        self._state: Dict = {
            "project_dir": None,
            "aoi_path": None,
            "huc8_ids": [],
            "downloaded": [],
        }
        self._worker: Optional[Worker] = None
        self._setup_ui()

    # UI
    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setSpacing(0)
        outer.setContentsMargins(0, 0, 0, 0)

        self._tabs = QTabWidget()
        self._tabs.setTabPosition(QTabWidget.TabPosition.North)
        self._tabs.currentChanged.connect(self._update_nav)
        outer.addWidget(self._tabs)

        self._tabs.addTab(self._wrap(self._build_input_tab()),      "1. Input")
        self._tabs.addTab(self._wrap(self._build_download_tab()),   "2. Download HUC8")
        self._tabs.addTab(self._wrap(self._build_streamflow_tab()), "3. Streamflow")
        self._tabs.addTab(self._wrap(self._build_fim_tab()),        "4. Generate FIM")

        self._tabs.setCurrentIndex(0)
        self._update_nav(0)

    def _wrap(self, w: QWidget) -> QScrollArea:
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setWidget(w)
        return sa

    # Input (project + AOI / HUC8 + map preview)
    def _build_input_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(12)
        v.setContentsMargins(14, 14, 14, 14)

        title = QLabel("FIMserv — OWP HAND Flood Inundation Mapping")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        title.setStyleSheet("color:#2d3748;")
        v.addWidget(title)

        # Project setup (base dir + name / open existing).
        self._proj = StepProjectWidget(self._log, model="generic")
        self._proj.step_completed.connect(self._on_project_done)
        v.addWidget(self._proj)

        # Input source: AOI file OR HUC8 IDs.
        src_gb = QGroupBox(); src_gb.setStyleSheet(_GB_STYLE)
        src_v = QVBoxLayout(src_gb); src_v.setSpacing(6)

        src_hdr = QLabel("Run area — choose one")
        src_hdr.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        src_v.addWidget(src_hdr)

        self._src_group = QButtonGroup(self)
        self._rb_aoi  = QRadioButton("Area of interest (AOI file)")
        self._rb_huc8 = QRadioButton("HUC8 ID(s) directly")
        self._rb_aoi.setChecked(True)
        self._src_group.addButton(self._rb_aoi)
        self._src_group.addButton(self._rb_huc8)
        self._rb_aoi.toggled.connect(self._on_src_toggled)
        src_v.addWidget(self._rb_aoi)

        # AOI row
        aoi_row = QHBoxLayout()
        aoi_row.addSpacing(20)
        aoi_row.addWidget(QLabel("AOI file:"))
        self._aoi_edit = QLineEdit()
        self._aoi_edit.setPlaceholderText("path/to/aoi.shp  (.shp, .gpkg, .geojson, .kml)")
        aoi_row.addWidget(self._aoi_edit, 1)
        aoi_browse = QPushButton("Browse…"); aoi_browse.setFixedWidth(90)
        aoi_browse.clicked.connect(self._browse_aoi)
        aoi_row.addWidget(aoi_browse)
        src_v.addLayout(aoi_row)

        src_v.addWidget(self._rb_huc8)

        # HUC8 row
        huc_row = QHBoxLayout()
        huc_row.addSpacing(20)
        huc_row.addWidget(QLabel("HUC8 ID(s):"))
        self._huc8_edit = QLineEdit()
        self._huc8_edit.setPlaceholderText("e.g. 03020201, 03020202")
        self._huc8_edit.setEnabled(False)
        huc_row.addWidget(self._huc8_edit, 1)
        src_v.addLayout(huc_row)

        note = QLabel(
            "★ The model always runs over whole HUC8 region(s). With an AOI the "
            "result is clipped to it; with HUC8 IDs the full extent is kept. USA only."
        )
        note.setWordWrap(True); note.setStyleSheet(_NOTE_STYLE)
        src_v.addWidget(note)

        resolve_row = QHBoxLayout()
        self._resolve_btn = QPushButton("Resolve HUC8  &  Preview")
        self._resolve_btn.setStyleSheet(_RUN_STYLE)
        self._resolve_btn.clicked.connect(self._resolve)
        resolve_row.addWidget(self._resolve_btn)
        resolve_row.addStretch()
        src_v.addLayout(resolve_row)
        v.addWidget(src_gb)

        # Map preview (HUC8 run area + AOI).
        self._map = USMapCanvas(self, width=10.0, height=4.0)
        self._map.setVisible(False)
        v.addWidget(self._map)

        self._input_status = QLabel("")
        self._input_status.setWordWrap(True)
        self._input_status.setStyleSheet("color:#276749; font-size:12px; font-weight:bold;")
        self._input_status.setVisible(False)
        v.addWidget(self._input_status)

        self._input_progress = QProgressBar()
        self._input_progress.setRange(0, 0)
        self._input_progress.setVisible(False)
        v.addWidget(self._input_progress)

        v.addStretch()
        return page

    def _on_src_toggled(self, _checked=False):
        use_aoi = self._rb_aoi.isChecked()
        self._aoi_edit.setEnabled(use_aoi)
        self._huc8_edit.setEnabled(not use_aoi)

    def _browse_aoi(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select AOI file", "",
            "Vector files (*.shp *.gpkg *.geojson *.json *.kml);;All files (*)",
        )
        if path:
            self._aoi_edit.setText(path)

    def _resolve(self):
        if not self._state["project_dir"]:
            QMessageBox.warning(self, "No project", "Set up a project first.")
            return

        aoi_path, huc8_ids = None, None
        if self._rb_aoi.isChecked():
            aoi = self._aoi_edit.text().strip()
            if not aoi or not Path(aoi).exists():
                QMessageBox.warning(self, "AOI", "Select a valid AOI file.")
                return
            aoi_path = aoi
        else:
            raw = self._huc8_edit.text().strip()
            ids = [t.strip().zfill(8) for t in raw.replace(",", " ").split() if t.strip()]
            if not ids:
                QMessageBox.warning(self, "HUC8", "Enter at least one HUC8 ID.")
                return
            huc8_ids = ids

        self._input_progress.setVisible(True)
        self._set_busy(self._input_status, "Resolving HUC8 …")
        set_running(self._resolve_btn)

        self._start_worker(
            resolve_huc8_mode,
            done=self._on_resolved,
            project_dir=self._state["project_dir"],
            aoi_path=aoi_path,
            huc8_ids=huc8_ids,
        )

    def _on_resolved(self, result: dict):
        set_ready(self._resolve_btn)
        self._input_progress.setVisible(False)
        ids = result.get("huc8_ids", [])
        self._state["huc8_ids"] = ids
        self._state["aoi_path"] = result.get("aoi_path")

        if not ids:
            self._input_status.setText("No HUC8 IDs found — check the AOI / IDs.")
            self._input_status.setStyleSheet("color:#c53030; font-size:12px; font-weight:bold;")
            self._input_status.setVisible(True)
            return

        self._input_status.setText(
            f"Resolved {len(ids)} HUC8(s): {', '.join(ids)}.  "
            "Move to step 2 to download them."
        )
        self._input_status.setStyleSheet("color:#276749; font-size:12px; font-weight:bold;")
        self._input_status.setVisible(True)
        self._render_preview()

    def _render_preview(self):
        """Draw the HUC8 run-area polygon(s) and the AOI on the US map."""
        try:
            import geopandas as gpd

            api = FIMservAPI(self._state["project_dir"], log_fn=self._log)
            huc8_gdf = api.huc8_polygons(self._state["huc8_ids"])

            aoi_gdf = None
            state_abbrs: List[str] = []
            points = []
            labels = []
            aoi_path = self._state.get("aoi_path")
            if aoi_path:
                aoi_gdf = gpd.read_file(aoi_path)
                c = aoi_gdf.to_crs("EPSG:4326").geometry.union_all().centroid
                points = [(c.x, c.y)]
                labels = ["AOI"]
                st = detect_us_state(aoi_gdf)
                if st.get("state_abbr"):
                    state_abbrs = [st["state_abbr"]]
            elif huc8_gdf is not None:
                c = huc8_gdf.to_crs("EPSG:4326").geometry.union_all().centroid
                points = [(c.x, c.y)]
                labels = ["HUC8"]

            self._map.update_plots(
                highlighted_state_abbrs=state_abbrs,
                aoi_points=points,
                aoi_labels=labels,
                aoi_gdf=aoi_gdf,
                huc8_gdf=huc8_gdf,
            )
            self._map.setVisible(True)
        except Exception as ex:
            self._log(f"Map preview failed: {ex}")

    # Download HUC8
    def _build_download_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(12); v.setContentsMargins(14, 14, 14, 14)

        title = QLabel("Download HUC8 OWP HAND rasters")
        title.setFont(QFont("Arial", 13, QFont.Weight.Bold))
        title.setStyleSheet("color:#2d3748;")
        v.addWidget(title)

        self._dl_info = QLabel("Resolve HUC8 IDs in step 1 first.")
        self._dl_info.setWordWrap(True)
        self._dl_info.setStyleSheet("color:#4a5568; font-size:12px;")
        v.addWidget(self._dl_info)

        run_row = QHBoxLayout()
        self._dl_btn = QPushButton("Download HUC8 data")
        self._dl_btn.setStyleSheet(_RUN_STYLE)
        self._dl_btn.clicked.connect(self._download)
        run_row.addWidget(self._dl_btn)
        run_row.addStretch()
        v.addLayout(run_row)

        self._dl_progress = QProgressBar(); self._dl_progress.setRange(0, 0)
        self._dl_progress.setVisible(False)
        v.addWidget(self._dl_progress)

        self._dl_status = QLabel("")
        self._dl_status.setWordWrap(True)
        self._dl_status.setStyleSheet("color:#276749; font-size:12px; font-weight:bold;")
        self._dl_status.setVisible(False)
        v.addWidget(self._dl_status)

        v.addStretch()
        return page

    def _download(self):
        ids = self._state.get("huc8_ids") or []
        if not ids:
            QMessageBox.warning(self, "No HUC8", "Resolve HUC8 IDs in step 1 first.")
            return
        self._dl_progress.setVisible(True)
        self._set_busy(self._dl_status,
                       "Downloading HUC8 rasters — this can take a few minutes, "
                       "hold tight …")
        set_running(self._dl_btn)
        self._start_worker(
            download_huc8_mode,
            done=self._on_downloaded,
            project_dir=self._state["project_dir"],
            huc8_ids=ids,
        )

    def _on_downloaded(self, result: dict):
        set_ready(self._dl_btn)
        self._dl_progress.setVisible(False)
        ok = result.get("downloaded", [])
        self._state["downloaded"] = ok
        self._dl_status.setText(
            f"Downloaded {len(ok)} of {len(self._state['huc8_ids'])} HUC8(s): "
            f"{', '.join(ok) if ok else '—'}."
        )
        self._dl_status.setStyleSheet("color:#276749; font-size:12px; font-weight:bold;")
        self._dl_status.setVisible(True)

    # Streamflow — Retrospective vs Forecast, each with its own fields.
    def _build_streamflow_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(12); v.setContentsMargins(14, 14, 14, 14)

        title = QLabel("NWM streamflow / discharge")
        title.setFont(QFont("Arial", 13, QFont.Weight.Bold))
        title.setStyleSheet("color:#2d3748;")
        v.addWidget(title)

        gb = QGroupBox(); gb.setStyleSheet(_GB_STYLE)
        gv = QVBoxLayout(gb); gv.setSpacing(8)

        # ── Source: Retrospective vs Forecast ────────────────────────────────
        src_row = QHBoxLayout()
        src_row.addWidget(QLabel("Source:"))
        self._src_grp = QButtonGroup(self)
        self._rb_retro = QRadioButton("Retrospective  (before 2023)")
        self._rb_fore  = QRadioButton("Forecast  (2023 onward)")
        self._rb_retro.setChecked(True)
        self._src_grp.addButton(self._rb_retro)
        self._src_grp.addButton(self._rb_fore)
        self._rb_retro.toggled.connect(self._on_source_toggled)
        src_row.addWidget(self._rb_retro)
        src_row.addWidget(self._rb_fore)
        src_row.addStretch()
        gv.addLayout(src_row)

        gv.addWidget(self._build_retro_group())
        gv.addWidget(self._build_forecast_group())

        self._sf_note = QLabel("")
        self._sf_note.setWordWrap(True); self._sf_note.setStyleSheet(_NOTE_STYLE)
        gv.addWidget(self._sf_note)
        v.addWidget(gb)

        run_row = QHBoxLayout()
        self._sf_btn = QPushButton("Get streamflow data")
        self._sf_btn.setStyleSheet(_RUN_STYLE)
        self._sf_btn.clicked.connect(self._get_streamflow)
        run_row.addWidget(self._sf_btn)
        run_row.addStretch()
        v.addLayout(run_row)

        self._sf_progress = QProgressBar(); self._sf_progress.setRange(0, 0)
        self._sf_progress.setVisible(False)
        v.addWidget(self._sf_progress)

        self._sf_status = QLabel("")
        self._sf_status.setWordWrap(True)
        self._sf_status.setStyleSheet("color:#276749; font-size:12px; font-weight:bold;")
        self._sf_status.setVisible(False)
        v.addWidget(self._sf_status)

        # Max-discharge hydrograph (retrospective date range only).
        self._hydro = HydrographPreviewCanvas(self, width=9, height=3.5)
        self._hydro.setVisible(False)
        v.addWidget(self._hydro)

        v.addStretch()
        self._on_source_toggled()
        return page

    def _build_retro_group(self) -> QWidget:
        box = QWidget()
        lv = QVBoxLayout(box); lv.setContentsMargins(0, 0, 0, 0); lv.setSpacing(6)

        # Retro sub-mode: specific event(s) vs date range.
        self._retro_grp = QButtonGroup(self)
        self._rb_specific = QRadioButton("Specific event date(s) / time(s)")
        self._rb_range    = QRadioButton("Date range")
        self._rb_specific.setChecked(True)
        self._retro_grp.addButton(self._rb_specific)
        self._retro_grp.addButton(self._rb_range)
        self._rb_specific.toggled.connect(self._on_retro_submode_toggled)
        lv.addWidget(self._rb_specific)

        # ── Specific-events section (its own box so it can be fully dimmed) ──
        self._specific_box = QWidget()
        sb = QVBoxLayout(self._specific_box)
        sb.setContentsMargins(20, 0, 0, 0); sb.setSpacing(4)
        ev_row = QHBoxLayout()
        self._event_edit = QLineEdit()
        self._event_edit.setPlaceholderText("YYYY-MM-DD  or  YYYY-MM-DD HH:MM:SS")
        ev_row.addWidget(self._event_edit, 1)
        add_btn = QPushButton("Add"); add_btn.setFixedWidth(60)
        add_btn.clicked.connect(self._add_event_time)
        ev_row.addWidget(add_btn)
        del_btn = QPushButton("Remove"); del_btn.setFixedWidth(70)
        del_btn.clicked.connect(self._remove_event_time)
        ev_row.addWidget(del_btn)
        sb.addLayout(ev_row)
        self._event_list = QListWidget()
        self._event_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self._event_list.setMaximumHeight(90)
        self._event_list.setMinimumWidth(240)
        sb.addWidget(self._event_list)
        lv.addWidget(self._specific_box)

        lv.addWidget(self._rb_range)

        # ── Date-range section (its own box so it can be fully dimmed) ──────
        self._range_box = QWidget()
        rb = QVBoxLayout(self._range_box)
        rb.setContentsMargins(20, 0, 0, 0); rb.setSpacing(4)

        dt_row = QHBoxLayout()
        dt_row.addWidget(QLabel("Start date:"))
        self._sf_start = QDateTimeEdit()
        self._sf_start.setDisplayFormat("yyyy-MM-dd")
        self._sf_start.setCalendarPopup(True)
        self._sf_start.setDateTime(QDateTime.fromString("2020-05-20", "yyyy-MM-dd"))
        dt_row.addWidget(self._sf_start)
        dt_row.addSpacing(12)
        dt_row.addWidget(QLabel("End date:"))
        self._sf_end = QDateTimeEdit()
        self._sf_end.setDisplayFormat("yyyy-MM-dd")
        self._sf_end.setCalendarPopup(True)
        self._sf_end.setDateTime(QDateTime.fromString("2020-05-22", "yyyy-MM-dd"))
        dt_row.addWidget(self._sf_end)
        dt_row.addStretch()
        rb.addLayout(dt_row)

        rng_note = QLabel(
            "Start/end set the download window and the hydrograph preview. "
            "Add event time(s) within the range to save those hours (one FIM "
            "each) — or add none to use the aggregation below."
        )
        rng_note.setWordWrap(True); rng_note.setStyleSheet(_NOTE_STYLE)
        rb.addWidget(rng_note)

        # In-range event-time editor — type a date/time inside the range and Add.
        self._timestep_lbl = QLabel("Event time(s) within the range:")
        rb.addWidget(self._timestep_lbl)
        tev_row = QHBoxLayout()
        self._range_event_edit = QLineEdit()
        self._range_event_edit.setPlaceholderText("YYYY-MM-DD  or  YYYY-MM-DD HH:MM:SS")
        tev_row.addWidget(self._range_event_edit, 1)
        radd = QPushButton("Add"); radd.setFixedWidth(60)
        radd.clicked.connect(self._add_range_event_time)
        tev_row.addWidget(radd)
        rdel = QPushButton("Remove"); rdel.setFixedWidth(70)
        rdel.clicked.connect(self._remove_range_event_time)
        tev_row.addWidget(rdel)
        rb.addLayout(tev_row)

        # The list shows both the fetched in-range timesteps and any the user
        # typed; selected / present items become the event time(s).
        self._timestep_list = QListWidget()
        self._timestep_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self._timestep_list.setMaximumHeight(90)
        self._timestep_list.setMinimumWidth(240)
        self._timestep_list.itemSelectionChanged.connect(self._on_timesteps_picked)
        rb.addWidget(self._timestep_list)

        agg_row = QHBoxLayout()
        self._agg_lbl = QLabel("Aggregation (used when no event time picked):")
        agg_row.addWidget(self._agg_lbl)
        self._sort_by = QComboBox()
        self._sort_by.addItems(["maximum", "median", "minimum"])
        agg_row.addWidget(self._sort_by)
        agg_row.addStretch()
        rb.addLayout(agg_row)
        lv.addWidget(self._range_box)

        self._retro_box = box
        return box

    def _build_forecast_group(self) -> QWidget:
        box = QWidget()
        lv = QVBoxLayout(box); lv.setContentsMargins(0, 0, 0, 0); lv.setSpacing(6)

        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Forecast range:"))
        self._fc_range = QComboBox()
        self._fc_range.addItems(["shortrange", "mediumrange", "longrange"])
        self._fc_range.setCurrentText("mediumrange")
        self._fc_range.currentTextChanged.connect(self._on_fc_range_changed)
        r1.addWidget(self._fc_range)
        r1.addStretch()
        lv.addLayout(r1)

        # Optional forecast date + hour.  Left unchecked → latest available run.
        self._fc_latest_chk = QCheckBox("Use latest available run")
        self._fc_latest_chk.setChecked(True)
        self._fc_latest_chk.toggled.connect(self._on_fc_latest_toggled)
        lv.addWidget(self._fc_latest_chk)

        r2 = QHBoxLayout()
        r2.addSpacing(20)
        self._fc_date_lbl = QLabel("Forecast date:")
        r2.addWidget(self._fc_date_lbl)
        self._fc_date = QDateTimeEdit()
        self._fc_date.setDisplayFormat("yyyy-MM-dd")
        self._fc_date.setCalendarPopup(True)
        self._fc_date.setDateTime(QDateTime.fromString("2024-06-01", "yyyy-MM-dd"))
        r2.addWidget(self._fc_date)
        r2.addSpacing(12)
        self._fc_hour_lbl = QLabel("Hour (UTC):")
        r2.addWidget(self._fc_hour_lbl)
        self._fc_hour = QComboBox()
        self._fc_hour.addItems([f"{h:02d}" for h in range(0, 24)])
        r2.addWidget(self._fc_hour)
        r2.addStretch()
        lv.addLayout(r2)

        far = QHBoxLayout()
        self._fc_agg_lbl = QLabel("Aggregation (medium / long range only):")
        far.addWidget(self._fc_agg_lbl)
        self._fc_sort_by = QComboBox()
        self._fc_sort_by.addItems(["maximum", "median", "minimum"])
        far.addWidget(self._fc_sort_by)
        far.addStretch()
        lv.addLayout(far)

        self._forecast_box = box
        return box

    # ── enable / disable logic (dim everything not in play) ──────────────────
    def _on_source_toggled(self, *_):
        retro = self._rb_retro.isChecked()
        self._retro_box.setVisible(retro)
        self._forecast_box.setVisible(not retro)
        if retro:
            self._on_retro_submode_toggled()
        else:
            self._on_fc_latest_toggled()
            self._on_fc_range_changed()
        self._hydro.setVisible(False)
        self._refresh_sf_note()

    def _on_retro_submode_toggled(self, *_):
        specific = self._rb_specific.isChecked()
        # Whole-section dimming: only the active sub-mode is enabled.
        self._specific_box.setEnabled(specific)
        self._range_box.setEnabled(not specific)
        # Inside range mode, picked/typed events still override aggregation.
        self._on_timesteps_picked()
        self._refresh_sf_note()

    def _on_timesteps_picked(self, *_):
        """In range mode, aggregation is only used when NO in-range event time
        is present (typed or selected) — events override aggregation."""
        if self._rb_specific.isChecked():
            self._sort_by.setEnabled(False)
            self._agg_lbl.setEnabled(False)
            return
        has_event = bool(self._selected_event_times())
        self._sort_by.setEnabled(not has_event)
        self._agg_lbl.setEnabled(not has_event)

    def _add_range_event_time(self):
        """Add a typed in-range event time to the list (validated)."""
        txt = self._range_event_edit.text().strip()
        if not txt:
            return
        if not self._valid_event_str(txt):
            QMessageBox.warning(self, "Event time",
                                "Use YYYY-MM-DD or YYYY-MM-DD HH:MM:SS.")
            return
        # Drop the placeholder rows if present.
        for ph in ("(fetch the range first)", "(no timesteps available)"):
            for it in self._timestep_list.findItems(ph, Qt.MatchFlag.MatchExactly):
                self._timestep_list.takeItem(self._timestep_list.row(it))
        item = QListWidgetItem(txt)
        self._timestep_list.addItem(item)
        item.setSelected(True)           # a typed event counts as picked
        self._timestep_list.setEnabled(True)
        self._range_event_edit.clear()
        self._on_timesteps_picked()
        self._refresh_sf_note()

    def _remove_range_event_time(self):
        for it in self._timestep_list.selectedItems():
            self._timestep_list.takeItem(self._timestep_list.row(it))
        self._on_timesteps_picked()
        self._refresh_sf_note()

    def _on_fc_latest_toggled(self, *_):
        manual = not self._fc_latest_chk.isChecked()
        for w in (self._fc_date_lbl, self._fc_date, self._fc_hour_lbl, self._fc_hour):
            w.setEnabled(manual)

    def _on_fc_range_changed(self, *_):
        # Aggregation only applies to medium / long range.
        agg_ok = self._fc_range.currentText() in ("mediumrange", "longrange")
        self._fc_sort_by.setEnabled(agg_ok)
        self._fc_agg_lbl.setEnabled(agg_ok)

    @staticmethod
    def _valid_event_str(txt: str) -> bool:
        """Accept YYYY-MM-DD or YYYY-MM-DD HH:MM[:SS]."""
        import datetime as _dt
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                _dt.datetime.strptime(txt, fmt)
                return True
            except ValueError:
                continue
        return False

    def _add_event_time(self):
        txt = self._event_edit.text().strip()
        if not txt:
            return
        if not self._valid_event_str(txt):
            QMessageBox.warning(self, "Event time",
                                "Use YYYY-MM-DD or YYYY-MM-DD HH:MM:SS.")
            return
        self._event_list.addItem(txt)
        self._event_edit.clear()
        self._refresh_sf_note()

    def _remove_event_time(self):
        for it in self._event_list.selectedItems():
            self._event_list.takeItem(self._event_list.row(it))
        self._refresh_sf_note()

    def _refresh_sf_note(self, *_):
        if self._rb_fore.isChecked():
            rng = self._fc_range.currentText()
            when = ("latest available run" if self._fc_latest_chk.isChecked()
                    else f"{self._fc_date.dateTime().toString('yyyy-MM-dd')} "
                         f"{self._fc_hour.currentText()}:00 UTC")
            self._sf_note.setText(f"★ NWM {rng} forecast — {when}.")
        elif self._rb_specific.isChecked():
            n = self._event_list.count()
            self._sf_note.setText(
                f"★ NWM retrospective — {n} specific event time(s); "
                "one discharge CSV (and one FIM) per time."
            )
        else:
            picked = self._selected_event_times()
            if picked:
                self._sf_note.setText(
                    f"★ NWM retrospective range — {len(picked)} event time(s) "
                    "picked; those hours are saved (aggregation ignored)."
                )
            else:
                self._sf_note.setText(
                    f"★ NWM retrospective range — aggregation "
                    f"({self._sort_by.currentText()}) over the window."
                )
        self._sf_note.setStyleSheet(_NOTE_STYLE)

    def _get_streamflow(self):
        ids = self._state.get("downloaded") or self._state.get("huc8_ids") or []
        if not ids:
            QMessageBox.warning(self, "No HUC8", "Resolve and download HUC8 data first.")
            return

        if self._rb_fore.isChecked():
            kwargs = dict(
                source="forecast",
                forecast_range=self._fc_range.currentText(),
                sort_by=self._fc_sort_by.currentText(),
            )
            if not self._fc_latest_chk.isChecked():
                kwargs["forecast_date"] = self._fc_date.dateTime().toString("yyyy-MM-dd")
                kwargs["forecast_hour"] = int(self._fc_hour.currentText())
        elif self._rb_specific.isChecked():
            times = [self._event_list.item(i).text()
                     for i in range(self._event_list.count())]
            if not times:
                QMessageBox.warning(self, "Event time",
                                    "Add at least one event date/time.")
                return
            kwargs = dict(source="retrospective", value_times=times)
        else:
            start = self._sf_start.dateTime().toPyDateTime()
            end = self._sf_end.dateTime().toPyDateTime()
            if end <= start:
                QMessageBox.warning(self, "Dates", "End date must be after the start date.")
                return
            picked = self._selected_event_times()
            kwargs = dict(
                source="retrospective",
                start_date=start.strftime("%Y-%m-%d"),
                end_date=end.strftime("%Y-%m-%d"),
                value_times=picked,                       # None → aggregate
                sort_by=self._sort_by.currentText(),
            )

        self._sf_progress.setVisible(True)
        self._set_busy(self._sf_status,
                       "Fetching NWM discharge — this can take a few minutes, "
                       "hold tight …")
        self._hydro.setVisible(False)
        set_running(self._sf_btn)
        self._start_worker(
            streamflow_mode,
            done=self._on_streamflow,
            project_dir=self._state["project_dir"],
            huc8_ids=ids,
            **kwargs,
        )

    def _selected_event_times(self):
        """Timestamps highlighted in the in-range list, or None."""
        if not self._timestep_list.isEnabled():
            return None
        picked = [it.text() for it in self._timestep_list.selectedItems()
                  if it.text() not in ("(fetch the range first)",
                                       "(no timesteps available)")]
        return picked or None

    def _on_streamflow(self, result: dict):
        set_ready(self._sf_btn)
        self._sf_progress.setVisible(False)
        mode = result.get("discharge_mode", "—")
        hydros = result.get("hydrographs", {})
        timesteps = result.get("timesteps", {})
        self._sf_status.setText(f"NWM {mode} discharge ready.")
        self._sf_status.setStyleSheet("color:#276749; font-size:12px; font-weight:bold;")
        self._sf_status.setVisible(True)
        # Retrospective range: draw the max-discharge hydrograph + fill the
        # in-range event-time list, keeping any times the user already typed.
        if hydros:
            huc, csv = next(iter(hydros.items()))
            stamps = timesteps.get(huc, [])
            # Preserve user-typed entries; drop placeholder rows.
            existing = [
                self._timestep_list.item(i).text()
                for i in range(self._timestep_list.count())
                if self._timestep_list.item(i).text()
                not in ("(fetch the range first)", "(no timesteps available)")
            ]
            self._timestep_list.clear()
            merged = existing + [s for s in stamps if s not in existing]
            if merged:
                self._timestep_list.addItems(merged)
                self._timestep_list.setEnabled(True)
                self._on_timesteps_picked()
                self._sf_status.setText(
                    f"NWM {mode} discharge ready — pick in-range event time(s) "
                    "and re-run to save a FIM per time, or run as-is to aggregate."
                )
            else:
                self._timestep_list.addItem("(no timesteps available)")
                self._timestep_list.setEnabled(False)
            if csv and Path(csv).exists():
                self._hydro.show_hydrograph(
                    csv, title=f"NWM {mode} — HUC8 {huc} (feature with max discharge)"
                )
                self._hydro.setVisible(True)

    # Generate FIM
    def _build_fim_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(12); v.setContentsMargins(14, 14, 14, 14)

        title = QLabel("Generate flood inundation map")
        title.setFont(QFont("Arial", 13, QFont.Weight.Bold))
        title.setStyleSheet("color:#2d3748;")
        v.addWidget(title)

        gb = QGroupBox(); gb.setStyleSheet(_GB_STYLE)
        gv = QVBoxLayout(gb); gv.setSpacing(6)

        # Depth is the only choice — extent/binary/mosaic/clip happen
        # automatically and the AOI clip applies whenever an AOI was given.
        self._depth_chk = QCheckBox("Also produce a water-depth map  (optional)")
        self._depth_chk.setChecked(False)
        gv.addWidget(self._depth_chk)

        self._fim_note = QLabel(
            "★ The flood inundation map is generated for the HUC8 region(s), "
            "merged across HUC8s, and shown against your area of interest."
        )
        self._fim_note.setWordWrap(True); self._fim_note.setStyleSheet(_NOTE_STYLE)
        gv.addWidget(self._fim_note)
        v.addWidget(gb)

        run_row = QHBoxLayout()
        self._fim_btn = QPushButton("Generate FIM")
        self._fim_btn.setStyleSheet(_RUN_STYLE)
        self._fim_btn.clicked.connect(self._generate)
        run_row.addWidget(self._fim_btn)
        run_row.addStretch()
        v.addLayout(run_row)

        self._fim_progress = QProgressBar(); self._fim_progress.setRange(0, 0)
        self._fim_progress.setVisible(False)
        v.addWidget(self._fim_progress)

        self._fim_status = QLabel("")
        self._fim_status.setWordWrap(True)
        self._fim_status.setStyleSheet("color:#2d3748; font-size:12px;")
        self._fim_status.setVisible(False)
        v.addWidget(self._fim_status)

        # Result previews: flood extent and (optional) depth, each with a
        # colorbar and the AOI boundary overlaid.
        self._extent_canvas = RasterPreviewCanvas(self, width=9, height=3.8)
        self._extent_canvas.setVisible(False)
        v.addWidget(self._extent_canvas)

        self._depth_canvas = RasterPreviewCanvas(self, width=9, height=3.8)
        self._depth_canvas.setVisible(False)
        v.addWidget(self._depth_canvas)

        self._fim_files = QLabel("")
        self._fim_files.setWordWrap(True)
        self._fim_files.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._fim_files.setStyleSheet("color:#4a5568; font-size:11px;")
        self._fim_files.setVisible(False)
        v.addWidget(self._fim_files)

        v.addStretch()
        return page

    def _generate(self):
        ids = self._state.get("downloaded") or self._state.get("huc8_ids") or []
        if not ids:
            QMessageBox.warning(self, "No HUC8", "Resolve and download HUC8 data first.")
            return
        self._fim_progress.setVisible(True)
        self._extent_canvas.setVisible(False)
        self._depth_canvas.setVisible(False)
        self._fim_files.setVisible(False)
        self._set_busy(self._fim_status,
                       "Generating flood inundation map — this can take a few "
                       "minutes, hold tight …")
        set_running(self._fim_btn)
        self._start_worker(
            generate_fim_mode,
            done=self._on_fim,
            project_dir=self._state["project_dir"],
            huc8_ids=ids,
            aoi_path=self._state.get("aoi_path"),
            depth=self._depth_chk.isChecked(),
            binary=True,   # always produce the binary wet/dry map
            clip=True,     # clip to the AOI whenever one was given
        )

    def _on_fim(self, result: dict):
        set_ready(self._fim_btn)
        self._fim_progress.setVisible(False)
        outputs = result.get("outputs", {})
        if not outputs:
            self._fim_status.setText("No FIM produced — see the log for details.")
            self._fim_status.setStyleSheet("color:#c53030; font-size:12px; font-weight:bold;")
            self._fim_status.setVisible(True)
            return
        self._fim_status.setText("Flood inundation map ready.")
        self._fim_status.setStyleSheet("color:#276749; font-weight:bold; font-size:12px;")
        self._fim_status.setVisible(True)

        # AOI boundary for the overlay (when an AOI was provided).
        aoi_gdf = None
        aoi_path = self._state.get("aoi_path")
        if aoi_path:
            try:
                import geopandas as gpd
                aoi_gdf = gpd.read_file(aoi_path)
            except Exception:
                aoi_gdf = None

        # Flood extent: prefer the clipped product, else the mosaic.
        extent_path = outputs.get("extent_clipped") or outputs.get("extent_mosaic")
        if extent_path and Path(extent_path).exists():
            self._extent_canvas.show_raster(
                extent_path, title="Flood extent (wet = 1 / dry = 0)",
                cmap="Blues", colorbar_label="Inundation",
                overlay_gdf=aoi_gdf,
            )
            self._extent_canvas.setVisible(True)

        # Depth (when requested): clipped product preferred, with a depth ramp.
        depth_path = outputs.get("depth_clipped") or outputs.get("depth_mosaic")
        if depth_path and Path(depth_path).exists():
            self._depth_canvas.show_raster(
                depth_path, title="Water depth",
                cmap="viridis", colorbar_label="Depth (m)",
                overlay_gdf=aoi_gdf,
            )
            self._depth_canvas.setVisible(True)

        # List the written files (selectable) so the user can find them on disk.
        lines = []
        for key in ("extent_clipped", "extent_mosaic", "extent_binary",
                    "depth_clipped", "depth_mosaic"):
            val = outputs.get(key)
            if not val:
                continue
            if isinstance(val, list):
                names = ", ".join(Path(p).name for p in val if p)
                lines.append(f"{key}: {names}")
            else:
                lines.append(f"{key}: {Path(val).name}")
        if lines:
            self._fim_files.setText("Files: " + "  |  ".join(lines))
            self._fim_files.setVisible(True)

    # Worker plumbing
    def _set_busy(self, label: QLabel, text: str):
        """Show a 'working, hold tight' style status line."""
        label.setText(text)
        label.setStyleSheet("color:#744210; font-size:12px; font-weight:bold;")
        label.setVisible(True)

    def _start_worker(self, fn, done, **kwargs):
        """Spawn a Worker for `fn`, routing logs + result/errors."""
        if self._worker is not None:
            try:
                self._worker.message.disconnect(self._log)
            except Exception:
                pass
            self._worker = None
        self._worker = Worker(fn, **kwargs)
        self._worker.message.connect(self._log)
        self._worker.finished.connect(done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_error(self, msg: str):
        # Restore every run button (only one is ever running) and report.
        for btn in (self._resolve_btn, self._dl_btn, self._sf_btn, self._fim_btn):
            try:
                set_ready(btn)
            except Exception:
                pass
        for pb in (self._input_progress, self._dl_progress,
                   self._sf_progress, self._fim_progress):
            pb.setVisible(False)
        self._log(f"ERROR: {msg}")
        QMessageBox.critical(self, "FIMserv error", msg.splitlines()[0])

    # navigation
    def _update_nav(self, idx: int):
        self.nav_changed.emit(idx, self._tabs.count())

    def go_prev(self):
        i = self._tabs.currentIndex()
        if i > 0:
            self._tabs.setCurrentIndex(i - 1)

    def go_next(self):
        i = self._tabs.currentIndex()
        if i < self._tabs.count() - 1:
            self._tabs.setCurrentIndex(i + 1)

    # slots
    def _on_project_done(self, data: dict):
        project_dir = data.get("ctx", {}).get("project_dir")
        self._state["project_dir"] = project_dir

        # Re-opened folder: detect anything already on disk so we resume rather
        # than re-download.  Pre-fill the HUC8 list and jump to the first step
        # that still has work left.
        existing = {}
        try:
            existing = discover_existing(project_dir, log_fn=self._log)
        except Exception as ex:
            self._log(f"Could not scan existing project ({ex}).")

        ids = existing.get("huc8_ids") or []
        if ids:
            self._state["huc8_ids"] = ids
            self._state["downloaded"] = existing.get("downloaded") or []
            self._huc8_edit.setText(", ".join(ids))
            self._input_status.setText(
                f"Found existing data for {len(ids)} HUC8(s): {', '.join(ids)}. "
                "Already-finished steps will be skipped — go straight to the "
                "step you need."
            )
            self._input_status.setStyleSheet(
                "color:#276749; font-size:12px; font-weight:bold;")
            self._input_status.setVisible(True)
            # Draw the map for the recovered HUC8s.
            self._render_preview()
            # Jump to the first step that still has work to do.
            if not existing.get("with_fim"):
                if not existing.get("with_discharge"):
                    if existing.get("downloaded"):
                        self._tabs.setCurrentIndex(2)   # → Streamflow
                    else:
                        self._tabs.setCurrentIndex(1)   # → Download
                else:
                    self._tabs.setCurrentIndex(3)       # → Generate FIM
        else:
            self._log("Project ready — choose an AOI or HUC8 ID(s), then Resolve.")

    # reset
    def reset(self):
        self._state = {
            "project_dir": None, "aoi_path": None,
            "huc8_ids": [], "downloaded": [],
        }
        if hasattr(self._proj, "reset"):
            self._proj.reset()
        self._aoi_edit.clear()
        self._huc8_edit.clear()
        self._rb_aoi.setChecked(True)
        # Streamflow defaults: retrospective + specific-event(s), empty lists.
        self._rb_retro.setChecked(True)
        self._rb_specific.setChecked(True)
        self._event_edit.clear()
        self._event_list.clear()
        self._fc_latest_chk.setChecked(True)
        self._timestep_list.clear()
        self._timestep_list.addItem("(fetch the range first)")
        self._timestep_list.setEnabled(False)
        self._on_source_toggled()
        self._map.setVisible(False)
        self._hydro.setVisible(False)
        self._extent_canvas.setVisible(False)
        self._depth_canvas.setVisible(False)
        self._fim_files.setVisible(False)
        for lbl in (self._input_status, self._dl_status, self._sf_status,
                    self._fim_status):
            lbl.setVisible(False)
        for pb in (self._input_progress, self._dl_progress,
                   self._sf_progress, self._fim_progress):
            pb.setVisible(False)
        for btn in (self._resolve_btn, self._dl_btn, self._sf_btn, self._fim_btn):
            try:
                set_ready(btn)
            except Exception:
                pass
        self._refresh_sf_note()
        self._tabs.setCurrentIndex(0)
