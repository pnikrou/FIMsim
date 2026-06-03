"""Reusable DEM-source configuration panel (per AOI).

Self-contained widget for picking how one AOI's DEM will be obtained:
  * Download from 3DEP   (default — no extra inputs)
  * I have a DEM raster  → file picker that accepts one or many tiles.

Shared between the single-AOI DEM step and the multi-AOI accordion (each
AOI card embeds one of these).  Cell size lives outside the panel — it's
a study-wide setting that applies to every AOI.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QRadioButton, QButtonGroup, QLineEdit, QPushButton, QFileDialog,
    QDoubleSpinBox,
)
from PyQt6.QtCore import pyqtSignal


class DEMConfigPanel(QWidget):
    config_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        form = QFormLayout()
        outer.addLayout(form)

        # ── Source radios — wrapped widget that spans both columns so
        # the radios stay anchored when the file picker shows / hides.
        self._rb_download = QRadioButton("Download from 3DEP (USGS)")
        self._rb_existing = QRadioButton("I have a DEM raster")
        self._rb_download.setChecked(True)
        self._bg = QButtonGroup(self)
        self._bg.addButton(self._rb_download, 0)
        self._bg.addButton(self._rb_existing, 1)

        src_widget = QWidget()
        src_inner = QHBoxLayout(src_widget)
        src_inner.setContentsMargins(0, 0, 0, 0)
        src_inner.addWidget(QLabel("<b>DEM source:</b>"))
        src_inner.addSpacing(8)
        src_inner.addWidget(self._rb_download)
        src_inner.addSpacing(16)
        src_inner.addWidget(self._rb_existing)
        src_inner.addStretch()
        form.addRow(src_widget)

        # ── Cell size (per-AOI — different AOIs may need different
        # resolutions).  Sits under the source row so the user always sees
        # it whether they're downloading from 3DEP or supplying their own
        # raster (the user-supplied DEM is resampled to this resolution).
        self._cell_lbl  = QLabel("DEM cell size:")
        self._cell_spin = QDoubleSpinBox()
        self._cell_spin.setRange(1, 1000)
        self._cell_spin.setValue(10)
        self._cell_spin.setSuffix(" m")
        self._cell_spin.setDecimals(1)
        form.addRow(self._cell_lbl, self._cell_spin)

        # ── File picker (visible only when "I have a DEM raster") ──
        self._dem_path_lbl  = QLabel("DEM file(s):")
        self._dem_path_edit = QLineEdit()
        self._dem_path_edit.setPlaceholderText(
            "Select one or more DEM GeoTIFFs (will be merged if >1)"
        )
        self._browse_btn = QPushButton("Browse…")
        self._browse_btn.setFixedWidth(80)
        self._browse_btn.clicked.connect(self._browse)

        path_row = QHBoxLayout()
        path_row.addWidget(self._dem_path_edit)
        path_row.addWidget(self._browse_btn)
        form.addRow(self._dem_path_lbl, path_row)

        self._note = QLabel(
            "<small><i>Tip: you can select multiple tiles — only those "
            "that overlap this AOI will be used and merged automatically."
            "</i></small>"
        )
        self._note.setWordWrap(True)
        form.addRow(self._note)

        self._dem_path_lbl.setVisible(False)
        self._dem_path_edit.setVisible(False)
        self._browse_btn.setVisible(False)
        self._note.setVisible(False)

        # ── wire signals
        self._rb_download.toggled.connect(self._on_source_changed)
        self._rb_existing.toggled.connect(self._on_source_changed)
        self._dem_path_edit.textChanged.connect(self._emit_changed)
        self._cell_spin.valueChanged.connect(self._emit_changed)

    # ── visibility ────────────────────────────────────────────────────────────

    def _on_source_changed(self, *_):
        existing = self._rb_existing.isChecked()
        self._dem_path_lbl.setVisible(existing)
        self._dem_path_edit.setVisible(existing)
        self._browse_btn.setVisible(existing)
        self._note.setVisible(existing)
        if not existing:
            self._dem_path_edit.clear()
        self._emit_changed()

    def _browse(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select one or more DEM rasters", "",
            "GeoTIFF (*.tif *.tiff)",
        )
        if files:
            self._dem_path_edit.setText(";".join(files))

    def _emit_changed(self, *_):
        self.config_changed.emit()

    # ── public API ────────────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        if self._rb_download.isChecked():
            return True
        # "I have a DEM raster" → must have at least one path.
        return bool(self._dem_path_edit.text().strip())

    def get_config(self) -> dict:
        existing = self._rb_existing.isChecked()
        raw = self._dem_path_edit.text().strip()
        paths = [p.strip() for p in raw.split(";") if p.strip()]
        return {
            "has_dem":       existing,
            "user_dem_path": paths,    # always a list (possibly empty)
            "dem_res_m":     float(self._cell_spin.value()),
        }

    def set_config(self, cfg: dict):
        if not cfg:
            return
        if cfg.get("has_dem"):
            self._rb_existing.setChecked(True)
            paths = cfg.get("user_dem_path") or []
            if isinstance(paths, list):
                self._dem_path_edit.setText(";".join(paths))
            else:
                self._dem_path_edit.setText(str(paths))
        else:
            self._rb_download.setChecked(True)
            self._dem_path_edit.clear()
        if "dem_res_m" in cfg:
            try:
                self._cell_spin.setValue(float(cfg["dem_res_m"]))
            except Exception:
                pass
