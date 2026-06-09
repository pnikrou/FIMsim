"""DEM standalone mode — Project → AOI(s) → DEM options → Download → back to main."""
import re
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QGroupBox,
    QFormLayout, QComboBox, QDoubleSpinBox, QStackedWidget, QScrollArea,
    QTextEdit, QFrame, QProgressBar,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView,
    QSizePolicy,
)
from PyQt6.QtGui import QColor, QFont as _QFont
from PyQt6.QtCore import pyqtSignal, Qt


# ── Per-AOI DEM option card ────────────────────────────────────────────────────

class _AOIDEMCard(QFrame):
    """Accordion card for one AOI's DEM options — neutral colors, no green/blue."""

    expand_requested = pyqtSignal(object)   # emitted when user clicks Edit

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

        # Config panel (shown when expanded)
        self._panel = QWidget()
        pf = QFormLayout(self._panel)
        pf.setContentsMargins(18, 4, 4, 4)
        pf.setVerticalSpacing(6)

        self._src_combo = QComboBox()
        self._src_combo.addItems(["3DEP  (USGS)", "HAND  (TACC)"])
        pf.addRow("Source:", self._src_combo)

        self._fmt_combo = QComboBox()
        self._fmt_combo.addItems(["TIF (GeoTIFF)", "GPKG (GeoPackage)", "ASC (ASCII grid)"])
        pf.addRow("Format:", self._fmt_combo)

        self._cell_spin = QDoubleSpinBox()
        self._cell_spin.setRange(1.0, 1000.0)
        self._cell_spin.setDecimals(1)
        self._cell_spin.setValue(10.0)
        self._cell_spin.setSuffix(" m")
        pf.addRow("Cell size:", self._cell_spin)

        self._panel.setVisible(False)
        outer.addWidget(self._panel)

        # Wire status refresh
        self._src_combo.currentIndexChanged.connect(self._refresh_status)
        self._fmt_combo.currentIndexChanged.connect(self._refresh_status)
        self._cell_spin.valueChanged.connect(self._refresh_status)
        self._refresh_status()

    def _toggle(self):
        if self._expanded:
            self.collapse()
        else:
            self.expand_requested.emit(self)   # let parent handle accordion

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
        src = "3DEP" if self._src_combo.currentIndex() == 0 else "HAND"
        fmt = ["TIF", "GPKG", "ASC"][self._fmt_combo.currentIndex()]
        cell = self._cell_spin.value()
        self._status_lbl.setText(f"{src}  ·  {fmt}  ·  {cell:.0f} m")

    def is_expanded(self) -> bool:
        return self._expanded

    def get_config(self) -> dict:
        return {
            "source":      "hand" if self._src_combo.currentIndex() == 1 else "3dep",
            "format":      ["tif", "gpkg", "asc"][self._fmt_combo.currentIndex()],
            "cell_size_m": float(self._cell_spin.value()),
        }

    def set_config(self, cfg: dict):
        src_idx = 1 if cfg.get("source") == "hand" else 0
        self._src_combo.setCurrentIndex(src_idx)
        fmt_map = {"tif": 0, "gpkg": 1, "asc": 2}
        self._fmt_combo.setCurrentIndex(fmt_map.get(cfg.get("format", "tif"), 0))
        try:
            self._cell_spin.setValue(float(cfg.get("cell_size_m", 10.0)))
        except Exception:
            pass
        self._refresh_status()

from typing import List

from gui.step_project import StepProjectWidget
from gui.multi_aoi_widget import MultiAOIWidget
from gui.run_button import set_running, set_ready
from gui.raster_preview import RasterPreviewCanvas
from gui.worker import Worker
from core.orchestrate import run_dem_mode


# Per-AOI progress markers emitted by run_dem_mode.  We re-render them in
# the LISFLOOD-FP DEM step's house style ("Downloading DEM N / M — name").
_RUNNING_RE = re.compile(r"^▶\s+Running\s+\[(\d+)/(\d+)\]")
_DONE_RE    = re.compile(r"^✓\s+Done\s+\[(\d+)/(\d+)\]")
_TILE_RE    = re.compile(r"Download progress:\s*(\d+)/(\d+)")


class ModeDEMWidget(QWidget):
    """Self-contained DEM preparation mode."""

    mode_finished = pyqtSignal()
    nav_changed   = pyqtSignal(int, int)

    def __init__(self, log_fn, parent=None):
        super().__init__(parent)
        self._log = log_fn
        self._project_dir = None
        self._features = []
        self._worker = None
        self._setup_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        outer = QVBoxLayout(self)

        self._stack = QStackedWidget()
        outer.addWidget(self._stack)

        # Show/hide the Prev/Next buttons as the inner stack changes
        self._stack.currentChanged.connect(self._update_prev_visible)

        # Page 0 — project (generic = no lisflood_files / triton_files subfolder)
        self._proj = StepProjectWidget(self._log, model="generic")
        self._proj.step_completed.connect(self._on_project_done)
        self._stack.addWidget(self._wrap(self._proj))

        # Page 1 — multi-AOI
        self._aoi = MultiAOIWidget(self._log)
        self._aoi.aoi_ready.connect(self._on_aoi_ready)
        self._aoi.back_requested.connect(lambda: self._stack.setCurrentIndex(0))
        self._stack.addWidget(self._wrap(self._aoi))

        # Page 2 — DEM options + run + results (all in one)
        self._stack.addWidget(self._wrap(self._build_options_page()))

        self._stack.setCurrentIndex(0)
        self._update_prev_visible(0)

    def _goto_previous_page(self):
        cur = self._stack.currentIndex()
        if cur > 0:
            self._stack.setCurrentIndex(cur - 1)

    def _goto_next_page(self):
        cur = self._stack.currentIndex()
        if cur == 1:
            # AOI page — commit the confirmed AOIs (the aoi_ready slot
            # then advances the stack to page 2 on its own).  If the
            # user hasn't confirmed any AOIs yet the widget pops a
            # warning and we stay put.
            self._aoi.proceed_to_next()
            return
        if cur == 0:
            # Project page — just advance to the AOI page.
            self._stack.setCurrentIndex(1)

    def _update_prev_visible(self, idx):
        self.nav_changed.emit(idx, self._stack.count())

    def go_prev(self):
        self._goto_previous_page()

    def go_next(self):
        self._goto_next_page()

    def _wrap(self, w):
        sa = QScrollArea(); sa.setWidgetResizable(True); sa.setWidget(w)
        return sa

    def _build_options_page(self):
        page = QWidget()
        v = QVBoxLayout(page)

        # Per-AOI accordion cards (populated in _rebuild_aoi_cards)
        self._aoi_cards_gb = QGroupBox("DEM options per AOI")
        self._aoi_cards_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        aoi_gb_v = QVBoxLayout(self._aoi_cards_gb)
        aoi_gb_v.setSpacing(6)
        aoi_gb_v.setContentsMargins(6, 6, 6, 6)

        # "Apply to all" row
        apply_row = QHBoxLayout()
        self._apply_all_btn = QPushButton("Apply current AOI's settings to all")
        self._apply_all_btn.setStyleSheet(
            "background:#4a5568; color:white; padding:5px 12px; "
            "border-radius:3px; font-weight:bold;"
        )
        self._apply_all_btn.setToolTip(
            "Copy the currently expanded AOI's settings to every other AOI."
        )
        self._apply_all_btn.clicked.connect(self._apply_settings_to_all)
        self._apply_all_btn.setEnabled(False)
        apply_row.addStretch()
        apply_row.addWidget(self._apply_all_btn)
        aoi_gb_v.addLayout(apply_row)

        self._aoi_cards_layout = QVBoxLayout()
        self._aoi_cards_layout.setSpacing(4)
        self._aoi_cards_layout.setContentsMargins(0, 0, 0, 0)
        aoi_gb_v.addLayout(self._aoi_cards_layout)

        self._aoi_dem_cards: List = []   # list of _AOIDEMCard
        v.addWidget(self._aoi_cards_gb)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._run_btn = QPushButton("Download / prepare DEM for all AOIs")
        self._run_btn.setStyleSheet(
            "font-weight:bold; padding:7px 20px; background:#2b6cb0; color:white; border-radius:4px;"
        )
        self._run_btn.clicked.connect(self._run)
        btn_row.addWidget(self._run_btn)
        v.addLayout(btn_row)

        # Per-AOI progress bar — resets to 0 each time a new AOI starts.
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setVisible(False)
        self._progress.setStyleSheet("QProgressBar { height: 18px; }")
        v.addWidget(self._progress)

        # Single-line live status — same blue style as the LISFLOOD-FP DEM
        # step so the two flows feel like one product.  Updated on every
        # ▶ Running [N/M] / ✓ Done [N/M] log marker.
        self._status_lbl = QLabel("")
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setStyleSheet("color:#2d3748; font-size:12px; padding:2px 0px;")
        self._status_lbl.setVisible(False)
        v.addWidget(self._status_lbl)

        # Summary line (shown on completion)
        self._summary_lbl = QLabel("")
        self._summary_lbl.setWordWrap(True)
        self._summary_lbl.setStyleSheet("color:#2d3748; font-size:12px; padding:2px 0px;")
        self._summary_lbl.setVisible(False)
        v.addWidget(self._summary_lbl)

        # Clickable AOI result rows
        self._results_gb = QGroupBox("")
        self._results_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        rgl = QVBoxLayout(self._results_gb)
        self._results_inner = QVBoxLayout()
        self._results_inner.setSpacing(0)
        rgl.addLayout(self._results_inner)
        self._results_gb.setVisible(False)
        v.addWidget(self._results_gb)

        # DEM preview (info table + raster canvas)
        self._gb_preview = QGroupBox("DEM preview")
        self._gb_preview.setMinimumHeight(400)
        pv = QVBoxLayout(self._gb_preview)

        self._preview_placeholder = QLabel("<i>Click an AOI above to preview its DEM here.</i>")
        self._preview_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_placeholder.setStyleSheet(
            "color:#888; padding:30px; background:#fafafa; "
            "border:1px dashed #cbd5e0; border-radius:4px;"
        )
        pv.addWidget(self._preview_placeholder)

        self._preview_2col = QWidget()
        h2 = QHBoxLayout(self._preview_2col)
        h2.setContentsMargins(0, 0, 0, 0)
        h2.setSpacing(10)

        info_col = QVBoxLayout()
        info_col.setSpacing(4)
        info_hdr = QLabel("<b>AOI Information</b>")
        info_hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info_hdr.setStyleSheet("color:#2d3748; font-size:10px; padding-bottom:2px;")
        info_col.addWidget(info_hdr)

        self._info_table = QTableWidget()
        self._info_table.setColumnCount(2)
        self._info_table.horizontalHeader().setVisible(False)
        self._info_table.verticalHeader().setVisible(False)
        self._info_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._info_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._info_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._info_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._info_table.verticalHeader().setDefaultSectionSize(22)
        self._info_table.setStyleSheet(
            "QTableWidget { font-size:10px; border:1px solid #e2e8f0; }"
            "QTableWidget::item { padding:1px 4px; }"
        )
        self._info_table.setAlternatingRowColors(True)
        info_col.addWidget(self._info_table, 1)
        h2.addLayout(info_col, 3)

        self._raster_preview = RasterPreviewCanvas(self, width=9, height=3.8)
        h2.addWidget(self._raster_preview, 7)

        self._preview_2col.setVisible(False)
        pv.addWidget(self._preview_2col, 1)

        self._gb_preview.setVisible(False)
        v.addWidget(self._gb_preview)

        v.addStretch()
        return page

    # ── slots ─────────────────────────────────────────────────────────────────

    def _on_project_done(self, data):
        ctx = data.get("ctx", {})
        self._project_dir = ctx.get("project_dir")
        self._aoi.set_project_dir(self._project_dir)
        self._stack.setCurrentIndex(1)

    def _on_aoi_ready(self, features):
        self._features = features
        self._rebuild_aoi_cards(features)
        self._stack.setCurrentIndex(2)

    def _rebuild_aoi_cards(self, features):
        """Rebuild per-AOI accordion cards (source + format + cell size)."""
        for card in self._aoi_dem_cards:
            card.setParent(None)
            card.deleteLater()
        self._aoi_dem_cards = []

        for f in features:
            card = _AOIDEMCard(f.name, self)
            card.expand_requested.connect(self._on_card_expand_requested)
            self._aoi_cards_layout.addWidget(card)
            self._aoi_dem_cards.append(card)

        # Enable Apply-to-all only when there is more than one AOI
        self._apply_all_btn.setEnabled(len(features) > 1)

    def _on_card_expand_requested(self, card: _AOIDEMCard):
        """Accordion: expand the requested card, collapse all others."""
        for c in self._aoi_dem_cards:
            if c is card:
                c.expand()
            else:
                c.collapse()

    def _apply_settings_to_all(self):
        """Copy the expanded card's settings to every other card."""
        src = next((c for c in self._aoi_dem_cards if c.is_expanded()), None)
        if src is None:
            # No card expanded — use the first one as reference
            src = self._aoi_dem_cards[0] if self._aoi_dem_cards else None
        if src is None:
            return
        cfg = src.get_config()
        for card in self._aoi_dem_cards:
            if card is not src:
                card.set_config(cfg)


    def _run(self):
        if not self._features:
            self._log("No AOI features confirmed.")
            return

        # No overwrite pre-check — the orchestrator tags filenames with the
        # source (3DEP vs HAND) and auto-renames with (1), (2), … if an
        # output file with the same name already exists (macOS-style).

        set_running(self._run_btn)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._status_lbl.setText(
            f"Preparing to download DEM for {len(self._features)} AOI(s)…"
        )
        self._status_lbl.setVisible(True)
        cfgs = [c.get_config() for c in self._aoi_dem_cards] or [
            {"source": "3dep", "format": "tif", "cell_size_m": 10.0}
        ]
        dem_sources    = [c["source"]      for c in cfgs]
        dem_formats    = [c["format"]      for c in cfgs]
        dem_cell_sizes = [c["cell_size_m"] for c in cfgs]
        kw = dict(
            project_dir=self._project_dir,
            features=self._features,
            dem_cell_size_m=dem_cell_sizes[0],
            out_format=dem_formats[0],
            dem_sources=dem_sources,
            dem_formats=dem_formats,
            dem_cell_sizes=dem_cell_sizes,
        )
        self._worker = Worker(run_dem_mode, **kw)
        self._worker.message.connect(self._on_worker_message)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_worker_message(self, msg):
        self._log(msg)

        # ── per-AOI start: refresh map + info panel + status line ─
        m = _RUNNING_RE.match(msg)
        if m:
            i, total = int(m.group(1)), int(m.group(2))
            self._progress.setValue(0)
            if 1 <= i <= len(self._features):
                feat = self._features[i - 1]
                self._status_lbl.setText(
                    f"Downloading DEM {i} / {total} — <i>{feat.name}</i>"
                )
                self._status_lbl.setVisible(True)
            return

        # ── per-AOI finished ─
        m = _DONE_RE.match(msg)
        if m:
            i, total = int(m.group(1)), int(m.group(2))
            self._progress.setValue(100)
            self._status_lbl.setText(
                f"DEM {i} / {total} finished."
                + (f"  Starting DEM {i + 1} / {total} …"
                   if i < total else "")
            )
            return

        # ── inner-AOI tile-download progress ─
        m = _TILE_RE.search(msg)
        if m:
            done, total = int(m.group(1)), int(m.group(2))
            if total > 0:
                self._progress.setValue(int(done / total * 70))
            return
        if "Converting DEM to ASCII" in msg or "Exporting" in msg:
            self._progress.setValue(80)
        elif "Downloading DEM from 3DEP" in msg or "Using provided DEM" in msg:
            self._progress.setValue(5)

    def _on_done(self, summary):
        set_ready(self._run_btn)
        self._progress.setValue(100)
        self._status_lbl.setText(
            f"All {len(self._features)} AOI(s) processed."
        )
        self._status_lbl.setStyleSheet(
            "color:#276749; font-weight:bold; font-size:12px; padding:2px 0px;"
        )
        # Store for the info table populated on AOI click
        self._run_summary = summary
        self._feat_by_name = {}
        for i, f in enumerate(summary.get("features", [])):
            if i < len(self._features):
                self._feat_by_name[f["name"]] = self._features[i]
        self._summary_lbl.setText(
            f"<b>DEM processed for "
            f"{len(summary.get('features', []))} AOI(s)</b>"
            "<br><small><i>Click an AOI name below to preview its DEM.</i></small>"
        )
        self._summary_lbl.setVisible(True)
        self._build_results(summary)

    def _build_results(self, summary):
        """Populate the clickable AOI list from the run summary."""
        from PyQt6.QtWidgets import QFrame as _QFrame
        from PyQt6.QtCore import Qt as _Qt

        # Clear any previous rows
        while self._results_inner.count():
            item = self._results_inner.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)

        features = summary.get("features", [])
        if not features:
            return

        for f in features:
            name = f.get("name", "?")
            path = f.get("dem_path", "")
            row = _QFrame()
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
                "QPushButton { text-align:left; background:transparent; "
                "border:none; color:#2d3748; font-weight:bold; padding:2px; }"
                "QPushButton:hover { color:#1a202c; text-decoration:underline; }"
            )
            btn.setCursor(_Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(
                lambda _checked, n=name, p=path: self._show_raster_for_aoi(n, p)
            )
            rl.addWidget(btn, 1)
            self._results_inner.addWidget(row)

        self._results_gb.setVisible(True)
        self._gb_preview.setVisible(True)
        self._preview_placeholder.setVisible(True)
        self._preview_2col.setVisible(False)
        self._info_table.setRowCount(0)

    def _show_raster_for_aoi(self, name: str, path: str):
        """Render the DEM raster and populate the left-side AOI info table."""
        if not path or not Path(path).exists():
            self._preview_placeholder.setText(
                f"<span style='color:#c53030;'>DEM file not found: {path}</span>"
            )
            self._preview_placeholder.setVisible(True)
            self._preview_2col.setVisible(False)
            return

        # ── Collect raster statistics ─────────────────────────────────────────
        elev_min = elev_max = elev_mean = mean_slope = None
        actual_cell_m = None
        try:
            import numpy as np
            import rasterio
            with rasterio.open(path) as src:
                arr = src.read(1, masked=True)
                actual_cell_m = float(abs(src.res[0]))
            if arr.count() > 0:
                elev_min  = float(np.ma.min(arr))
                elev_max  = float(np.ma.max(arr))
                elev_mean = float(np.ma.mean(arr))
                try:
                    filled = arr.filled(np.nan)
                    dz_dy, dz_dx = np.gradient(
                        filled, actual_cell_m, actual_cell_m
                    )
                    slope_deg = np.degrees(
                        np.arctan(np.sqrt(dz_dx ** 2 + dz_dy ** 2))
                    )
                    mean_slope = float(np.nanmean(slope_deg))
                except Exception:
                    pass
        except Exception:
            pass

        # ── Build info rows ───────────────────────────────────────────────────
        feat    = getattr(self, "_feat_by_name", {}).get(name)
        summary = getattr(self, "_run_summary", {})

        # Each entry: (label, value, is_section_header)
        rows: list = []
        rows.append(("AOI name",  name,  False))
        if feat is not None:
            sa = getattr(feat, "state_abbr", None)
            if sa:
                rows.append(("State", sa, False))
            fi = getattr(feat, "feature_index", None)
            if fi is not None:
                rows.append(("Feature #", str(fi), False))
            crs = (
                getattr(feat, "working_crs_label", None)
                or (f"EPSG:{feat.working_crs_epsg}"
                    if getattr(feat, "working_crs_epsg", None) else None)
            )
            if crs:
                rows.append(("CRS", crs, False))
            lat = getattr(feat, "centroid_lat", None)
            lon = getattr(feat, "centroid_lon", None)
            if lat is not None and lon is not None:
                rows.append(("Centroid",
                             f"{lat:.4f}°N,  {lon:.4f}°E", False))

        rows.append(("DEM Stats", "", True))
        if elev_min  is not None: rows.append(("Min elevation",  f"{elev_min:.2f} m",  False))
        if elev_max  is not None: rows.append(("Max elevation",  f"{elev_max:.2f} m",  False))
        if elev_mean is not None: rows.append(("Mean elevation", f"{elev_mean:.2f} m", False))
        if mean_slope is not None: rows.append(("Avg slope",     f"{mean_slope:.2f}°", False))

        rows.append(("Settings", "", True))
        cs = actual_cell_m or summary.get("cell_size_m")
        if cs:
            rows.append(("Cell size", f"{float(cs):.1f} m", False))
        fmt = summary.get("format", "")
        if fmt:
            rows.append(("Output format", fmt.upper(), False))
        rows.append(("File", Path(path).name, False))

        # ── Populate the QTableWidget ─────────────────────────────────────────
        sep_bg  = QColor("#dbeafe")   # light blue for section headers
        sep_fg  = QColor("#1e40af")
        lbl_fg  = QColor("#4a5568")

        self._info_table.setRowCount(len(rows))
        for r, (key, val, is_sep) in enumerate(rows):
            if is_sep:
                item = QTableWidgetItem(f"  {key}")
                item.setBackground(sep_bg)
                item.setForeground(sep_fg)
                f = _QFont()
                f.setBold(True)
                f.setPointSize(8)
                item.setFont(f)
                self._info_table.setItem(r, 0, item)
                self._info_table.setSpan(r, 0, 1, 2)
                # Ensure column 1 has no stale item after span
                self._info_table.setItem(r, 1, QTableWidgetItem(""))
            else:
                k_item = QTableWidgetItem(key)
                k_item.setForeground(lbl_fg)
                self._info_table.setItem(r, 0, k_item)
                self._info_table.setItem(r, 1, QTableWidgetItem(val))

        # ── Render raster ─────────────────────────────────────────────────────
        self._raster_preview.show_raster(
            path,
            title=f"DEM — {name}",
            cmap="terrain",
            colorbar_label="Elevation (m)",
        )
        self._preview_placeholder.setVisible(False)
        self._preview_2col.setVisible(True)

    def _on_error(self, msg):
        set_ready(self._run_btn)
        self._log(f"ERROR: {msg}")
        first_line = msg.splitlines()[0]
        self._status_lbl.setText(f"{first_line}")
        self._status_lbl.setStyleSheet(
            "padding:6px 10px; background:#fff5f5; border:1px solid #fc8181; "
            "border-radius:4px; color:#c53030; font-weight:bold; font-size:12px;"
        )
        self._status_lbl.setVisible(True)
        self._progress.setVisible(False)

    def reset(self):
        """Reset for a fresh run when user returns to this mode."""
        self._project_dir = None
        self._features = []
        # Project page — clear text fields + report banner
        if hasattr(self._proj, "reset"):
            self._proj.reset()
        # Multi-AOI page — clear blocks + map + confirmed list
        self._aoi.reset()
        # Live status + progress
        if hasattr(self, "_status_lbl"):
            self._status_lbl.setVisible(False)
            # Restore the default blue theme in case _on_error re-styled it
            self._status_lbl.setStyleSheet(
                "padding:6px 10px; background:#ebf8ff; border:1px solid #90cdf4; "
                "border-radius:4px; color:#2c5282; font-weight:bold; font-size:12px;"
            )
        if hasattr(self, "_progress"):
            self._progress.setValue(0)
            self._progress.setVisible(False)
        # Results summary + results panel + raster preview (now on page 2)
        if hasattr(self, "_summary_lbl"):
            self._summary_lbl.setText("")
            self._summary_lbl.setVisible(False)
        if hasattr(self, "_results_gb"):
            while self._results_inner.count():
                item = self._results_inner.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.setParent(None)
            self._results_gb.setVisible(False)
        if hasattr(self, "_gb_preview"):
            self._gb_preview.setVisible(False)
            self._raster_preview.clear()
            self._preview_placeholder.setVisible(True)
            if hasattr(self, "_preview_2col"):
                self._preview_2col.setVisible(False)
            if hasattr(self, "_info_table"):
                self._info_table.setRowCount(0)
        # Clear per-AOI option cards
        if hasattr(self, "_aoi_dem_cards"):
            for card in self._aoi_dem_cards:
                card.setParent(None)
                card.deleteLater()
            self._aoi_dem_cards = []
        # Make sure the run button isn't stuck in a "Working…" state
        try:
            set_ready(self._run_btn)
        except Exception:
            self._run_btn.setEnabled(True)
        self._stack.setCurrentIndex(0)
