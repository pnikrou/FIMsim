"""Reusable Manning's n configuration panel.

A self-contained widget that lets a user pick Fixed vs Varying Manning,
choose a LULC source (Download / Upload), and edit the LULC → n table.

Used by the Manning step in two contexts:
  * single-AOI workflows (LISFLOOD-FP / TRITON when only one AOI is
    confirmed)  →  one panel embedded directly in the step page.
  * multi-AOI workflows  →  one panel embedded inside each per-AOI card,
    so each AOI can carry its own Fixed/Varying choice + sub-config.

Public API:
  * config_ready_changed(bool)  →  emitted whenever ``is_ready()``
    transitions; the host widget can use it to enable/disable the run
    button.
  * is_ready()                  →  True if the user's selections form a
    runnable config.
  * get_config() / set_config() →  serialise / restore selections, used
    by the "Apply to all" button.
  * mode()                      →  "fixed" | "varying" | "" (none yet)
"""
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLabel,
    QLineEdit, QPushButton, QDoubleSpinBox,
    QComboBox, QFileDialog,
)
from PyQt6.QtCore import pyqtSignal, Qt

from core.nlcd import NLCD_MANNING, SENTINEL2_MANNING
from gui.manning_table_widget import ManningTableWidget


class ManningConfigPanel(QWidget):
    """One self-contained Fixed/Varying Manning configuration form."""

    config_changed = pyqtSignal()
    config_ready_changed = pyqtSignal(bool)
    mode_changed = pyqtSignal(str)        # "fixed" | "varying" | ""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._user_table_data = None
        self._was_ready = False
        self._build_ui()

    # ─────────────────────────────────────────────────────────────────────────
    # UI construction
    # ─────────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        form = QFormLayout()
        outer.addLayout(form)
        self._form = form

        # ── Mode (Fixed / Varying) — single combo replaces radio pair
        self._mode_combo = QComboBox()
        self._mode_combo.addItems([
            "—  pick a mode  —",
            "Fixed value",
            "Varying (from LULC)",
        ])
        form.addRow("<b>Manning:</b>", self._mode_combo)

        # ── Fixed branch: single n-value spin
        self._fixed_lbl = QLabel("Fixed n value:")
        self._fixed_spin = QDoubleSpinBox()
        self._fixed_spin.setRange(0.001, 1.0)
        self._fixed_spin.setDecimals(4)
        self._fixed_spin.setValue(0.06)
        form.addRow(self._fixed_lbl, self._fixed_spin)

        # ── Varying branch: GroupBox with sub-source picker + details
        self._varying_gb = QGroupBox("Varying Manning settings")
        v_form = QFormLayout(self._varying_gb)

        # LULC source combo — NLCD / Sentinel-2 / Upload (3 options in one row)
        self._lulc_src_combo = QComboBox()
        self._lulc_src_combo.addItem("NLCD  (USGS, 30 m  —  USA only)",     "nlcd")
        self._lulc_src_combo.addItem("Sentinel-2  (ESRI, 10 m  —  global)", "esri")
        self._lulc_src_combo.addItem("Upload a LULC raster…",               "upload")
        v_form.addRow("<b>LULC source:</b>", self._lulc_src_combo)

        # Year combo — only relevant for Download
        self._year_lbl   = QLabel("Year:")
        self._year_combo = QComboBox()
        v_form.addRow(self._year_lbl, self._year_combo)

        # Upload branch — file picker.  The raster is auto-analyzed as
        # soon as the user selects a file (no separate button click).
        self._raster_lbl = QLabel("LULC raster file:")
        self._raster_edit = QLineEdit()
        self._raster_edit.setPlaceholderText("Path to your LULC raster (.tif)")
        self._raster_browse_btn = QPushButton("Browse…")
        self._raster_browse_btn.setFixedWidth(80)
        self._raster_browse_btn.clicked.connect(self._browse_raster)
        raster_row = QHBoxLayout()
        raster_row.addWidget(self._raster_edit)
        raster_row.addWidget(self._raster_browse_btn)
        v_form.addRow(self._raster_lbl, raster_row)

        self._analyze_status = QLabel("")
        self._analyze_status.setWordWrap(True)
        self._analyze_status.setStyleSheet(
            "padding:4px 8px; color:#2c5282; font-size:11px;"
        )
        v_form.addRow(self._analyze_status)

        # Manning table (shared between Download / Upload branches)
        self._table_lbl = QLabel(
            "LULC class → Manning n mapping  "
            "(Min/Max are reference bounds; Avg is editable and clamped):"
        )
        v_form.addRow(self._table_lbl)
        self._table = ManningTableWidget(NLCD_MANNING)
        v_form.addRow(self._table)

        form.addRow(self._varying_gb)

        # ── wire signals
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self._lulc_src_combo.currentIndexChanged.connect(self._on_source_changed)
        self._year_combo.currentIndexChanged.connect(self._emit_changed)
        self._fixed_spin.valueChanged.connect(self._emit_changed)
        self._raster_edit.textChanged.connect(self._on_raster_path_changed)

        # ── Friendly defaults: Varying → Sentinel-2 → most recent year.
        self._mode_combo.setCurrentIndex(2)          # Varying (from LULC)
        self._lulc_src_combo.setCurrentIndex(1)      # Sentinel-2 (ESRI, 10 m)

        # Belt-and-suspenders: re-run the visibility handlers.
        self._on_mode_changed()
        self._on_source_changed()

    # ─────────────────────────────────────────────────────────────────────────
    # Visibility state machine
    # ─────────────────────────────────────────────────────────────────────────

    def _on_mode_changed(self, *_):
        idx = self._mode_combo.currentIndex()
        fixed   = idx == 1
        varying = idx == 2
        any_picked = idx >= 1

        self._fixed_lbl.setVisible(fixed)
        self._fixed_spin.setVisible(fixed)
        self._varying_gb.setVisible(varying)

        self._emit_changed()
        if fixed:
            self.mode_changed.emit("fixed")
        elif varying:
            self.mode_changed.emit("varying")
        else:
            self.mode_changed.emit("")

    def _on_source_changed(self, *_):
        src = self._lulc_src_combo.currentData()   # "nlcd" | "esri" | "upload"
        is_download = src in ("nlcd", "esri")
        is_upload   = (src == "upload")

        # Populate year combo (and update Manning table) based on source
        self._year_combo.blockSignals(True)
        self._year_combo.clear()
        if src == "nlcd":
            self._year_combo.addItems(["2021", "2019", "2016"])
            self._table.set_table_data(NLCD_MANNING)
        elif src == "esri":
            for yr in range(2017, 2025):
                self._year_combo.addItem(str(yr))
            self._year_combo.setCurrentIndex(self._year_combo.count() - 1)
            self._table.set_table_data(SENTINEL2_MANNING)
        self._year_combo.blockSignals(False)

        for w in (self._year_lbl, self._year_combo):
            w.setVisible(is_download)
        for w in (self._raster_lbl, self._raster_edit,
                  self._raster_browse_btn, self._analyze_status):
            w.setVisible(is_upload)

        if is_download:
            self._table_lbl.setVisible(True)
            self._table.setVisible(True)
        elif is_upload:
            show_table = self._user_table_data is not None
            self._table_lbl.setVisible(show_table)
            self._table.setVisible(show_table)
        else:
            self._table_lbl.setVisible(False)
            self._table.setVisible(False)

        self._analyze_status.setText("")
        self._emit_changed()

    def _on_raster_path_changed(self, *_):
        # Whenever the path changes, throw away any prior analysis…
        self._user_table_data = None
        self._analyze_status.setText("")
        if self._lulc_src_combo.currentData() == "upload":
            self._table_lbl.setVisible(False)
            self._table.setVisible(False)
        self._emit_changed()
        # …and if the new path points at an existing file, auto-analyze it
        # so the user gets a Manning table without an extra click.
        path = self._raster_edit.text().strip()
        if path and Path(path).exists() and self._lulc_src_combo.currentData() == "upload":
            self._analyze_raster()

    # ─────────────────────────────────────────────────────────────────────────
    # File helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _browse_raster(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "Select LULC raster", "", "GeoTIFF (*.tif *.tiff)"
        )
        if f:
            self._raster_edit.setText(f)

    def _analyze_raster(self):
        path = self._raster_edit.text().strip()
        if not path:
            self._analyze_status.setText(
                "<span style='color:#c53030;'>Please browse for a raster first.</span>"
            )
            return
        if not Path(path).exists():
            self._analyze_status.setText(
                f"<span style='color:#c53030;'>File not found: {path}</span>"
            )
            return
        self._analyze_status.setText("Analyzing raster…")
        try:
            import rasterio
            import numpy as np
            with rasterio.open(path) as src:
                arr = src.read(1)
                nodata = src.nodata
            if nodata is not None:
                arr = arr[arr != nodata]
            if arr.dtype.kind == "f":
                arr = arr[~np.isnan(arr)]
                arr = arr.astype(np.int64)
            uniq = np.unique(arr)
            uniq = [int(v) for v in uniq if v >= 0]
        except Exception as ex:
            self._analyze_status.setText(
                f"<span style='color:#c53030;'>Failed to read raster: {ex}</span>"
            )
            return

        if not uniq:
            self._analyze_status.setText(
                "<span style='color:#c53030;'>No valid class values found in the raster.</span>"
            )
            return
        if len(uniq) > 50:
            self._analyze_status.setText(
                f"<span style='color:#c53030;'>Raster has {len(uniq)} unique values — "
                f"that doesn't look categorical.  Please provide a classified LULC raster.</span>"
            )
            return

        # Build per-code (name, min, max, default).  Prefer NLCD then Sentinel-2
        # defaults when the code is recognised; otherwise fall back to a
        # generic bound.
        table = {}
        for c in uniq:
            if c in NLCD_MANNING:
                table[c] = NLCD_MANNING[c]
            elif c in SENTINEL2_MANNING:
                table[c] = SENTINEL2_MANNING[c]
            else:
                table[c] = (f"Class {c}", 0.005, 0.30, 0.06)
        self._user_table_data = table
        self._table.set_table_data(table)
        self._table_lbl.setVisible(True)
        self._table.setVisible(True)
        self._analyze_status.setText(
            f"<span style='color:#22543d;'>Found {len(uniq)} class value(s): "
            f"{', '.join(str(v) for v in uniq[:20])}"
            f"{'…' if len(uniq) > 20 else ''}.  "
            f"Edit the n values below if needed.</span>"
        )
        self._emit_changed()

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def mode(self) -> str:
        idx = self._mode_combo.currentIndex()
        if idx == 1:
            return "fixed"
        if idx == 2:
            return "varying"
        return ""

    def is_ready(self) -> bool:
        idx = self._mode_combo.currentIndex()
        if idx == 1:   # Fixed
            return True
        if idx != 2:   # Nothing picked
            return False
        # Varying — check source
        src = self._lulc_src_combo.currentData()
        if src in ("nlcd", "esri"):   # Download
            return True
        if src == "upload":           # Upload
            return self._user_table_data is not None
        return False

    def set_mode_radios(self, fixed: bool, varying: bool):
        """Used by the multi-AOI card to wire the inline Fixed/Varying picker
        on its collapsed row to this panel's combo.  Either both False
        (initial) or exactly one True."""
        self._mode_combo.blockSignals(True)
        if fixed:
            self._mode_combo.setCurrentIndex(1)
        elif varying:
            self._mode_combo.setCurrentIndex(2)
        else:
            self._mode_combo.setCurrentIndex(0)
        self._mode_combo.blockSignals(False)
        self._on_mode_changed()

    def get_config(self) -> dict:
        """Snapshot the current form selections so they can be copied to
        another panel via set_config (used by Apply-to-all)."""
        src = self._lulc_src_combo.currentData()   # "nlcd" | "esri" | "upload"
        # dataset_idx: 0 = NLCD, 1 = ESRI — read by _build_prepare_manning_kwargs
        ds_idx = {"nlcd": 0, "esri": 1}.get(src, 0)
        cfg = {
            "mode":           self.mode(),
            "fixed_value":    float(self._fixed_spin.value()),
            "source":         "upload" if src == "upload" else "download",
            "dataset_idx":    ds_idx,
            "year":           self._year_combo.currentText(),
            "raster_path":    self._raster_edit.text().strip(),
            "user_table":     dict(self._user_table_data) if self._user_table_data else None,
            "table_mapping":  self._table.get_mapping(),
        }
        return cfg

    def set_config(self, cfg: dict):
        """Restore selections from a config dict."""
        if not cfg:
            return
        # Mode
        self.set_mode_radios(
            fixed=(cfg.get("mode") == "fixed"),
            varying=(cfg.get("mode") == "varying"),
        )
        if cfg.get("mode") == "fixed":
            self._fixed_spin.setValue(float(cfg.get("fixed_value", 0.06)))
        elif cfg.get("mode") == "varying":
            src = cfg.get("source", "")
            if src == "download":
                # dataset_idx: 0 = NLCD, 1 = ESRI → direct combo index
                ds_idx = int(cfg.get("dataset_idx", 0))
                self._lulc_src_combo.setCurrentIndex(ds_idx)
                # _on_source_changed fires and populates the year combo
                year = cfg.get("year", "")
                idx = self._year_combo.findText(str(year))
                if idx >= 0:
                    self._year_combo.setCurrentIndex(idx)
            elif src == "upload":
                self._lulc_src_combo.setCurrentIndex(2)
                self._raster_edit.setText(cfg.get("raster_path", ""))
                user_table = cfg.get("user_table")
                if user_table:
                    # Codes serialised as strings in JSON-ish dicts may
                    # come back as str — coerce keys to int.
                    self._user_table_data = {int(k): v for k, v in user_table.items()}
                    self._table.set_table_data(self._user_table_data)
                    self._table_lbl.setVisible(True)
                    self._table.setVisible(True)

    def get_table_mapping(self) -> dict:
        return self._table.get_mapping()

    # ─────────────────────────────────────────────────────────────────────────
    # Change notification
    # ─────────────────────────────────────────────────────────────────────────

    def _emit_changed(self, *_):
        self.config_changed.emit()
        ready = self.is_ready()
        if ready != self._was_ready:
            self._was_ready = ready
            self.config_ready_changed.emit(ready)
