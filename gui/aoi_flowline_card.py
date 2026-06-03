"""Per-AOI Flowline configuration card — same accordion pattern as AOIDEMCard."""
from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QComboBox, QDoubleSpinBox, QWidget,
)
from PyQt6.QtCore import pyqtSignal


class AOIFlowlineCard(QFrame):
    expand_requested = pyqtSignal(object)
    config_changed   = pyqtSignal(object)

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
        self._apply_collapsed_style()
        self._refresh_status()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 2)
        outer.setSpacing(6)

        # ── Header row ────────────────────────────────────────────────────────
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

        outer.addLayout(header)

        # ── Config panel (hidden when collapsed) ──────────────────────────────
        self._panel = QWidget()
        pl = QVBoxLayout(self._panel)
        pl.setContentsMargins(18, 4, 4, 4)
        pl.setSpacing(8)

        _FMT_ITEMS = [("Shapefile (.shp)",    "shp"),
                      ("GeoPackage (.gpkg)", "gpkg"),
                      ("TIF raster (.tif)",  "tif"),
                      ("CSV (.csv)",         "csv")]

        # Main river row
        main_row = QHBoxLayout()
        self._chk_main = QCheckBox("Main river  (NHD highest stream order)")
        self._chk_main.setChecked(True)
        self._main_fmt_combo = QComboBox()
        for lbl, val in _FMT_ITEMS:
            self._main_fmt_combo.addItem(lbl, val)
        self._main_fmt_combo.setFixedWidth(165)
        main_row.addWidget(self._chk_main)
        main_row.addSpacing(12)
        main_row.addWidget(self._main_fmt_combo)
        main_row.addStretch()
        pl.addLayout(main_row)

        # All flowlines row
        all_row = QHBoxLayout()
        self._chk_all = QCheckBox("All flowlines  (full NHD reach set)")
        self._chk_all.setChecked(False)
        self._all_fmt_combo = QComboBox()
        for lbl, val in _FMT_ITEMS:
            self._all_fmt_combo.addItem(lbl, val)
        self._all_fmt_combo.setFixedWidth(165)
        all_row.addWidget(self._chk_all)
        all_row.addSpacing(12)
        all_row.addWidget(self._all_fmt_combo)
        all_row.addStretch()
        pl.addLayout(all_row)

        # TIF cell size (appears only when either combo is set to TIF)
        cell_row = QHBoxLayout()
        self._cell_lbl = QLabel("Cell size for TIF:")
        self._cell_spin = QDoubleSpinBox()
        self._cell_spin.setRange(1.0, 10000.0)
        self._cell_spin.setValue(30.0)
        self._cell_spin.setSuffix(" m")
        self._cell_spin.setFixedWidth(100)
        cell_row.addSpacing(20)
        cell_row.addWidget(self._cell_lbl)
        cell_row.addWidget(self._cell_spin)
        cell_row.addStretch()
        self._cell_size_row = QWidget()
        self._cell_size_row.setLayout(cell_row)
        self._cell_size_row.setVisible(False)
        pl.addWidget(self._cell_size_row)

        # USGS gages only
        self._chk_gages = QCheckBox("Save USGS gages as CSV")
        self._chk_gages.setChecked(True)
        pl.addWidget(self._chk_gages)

        self._panel.setVisible(False)
        outer.addWidget(self._panel)

        # Wire signals
        for combo in (self._main_fmt_combo, self._all_fmt_combo):
            combo.currentIndexChanged.connect(self._on_config_changed)
        for chk in (self._chk_main, self._chk_all, self._chk_gages):
            chk.toggled.connect(self._on_config_changed)
        self._cell_spin.valueChanged.connect(self._on_config_changed)

    def _on_config_changed(self):
        need_tif = (
            (self._chk_main.isChecked() and self._main_fmt_combo.currentData() == "tif") or
            (self._chk_all.isChecked()  and self._all_fmt_combo.currentData() == "tif")
        )
        self._cell_size_row.setVisible(need_tif)
        self._refresh_status()
        self.config_changed.emit(self)

    # ── expand / collapse ─────────────────────────────────────────────────────

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

    def is_expanded(self) -> bool:
        return self._expanded

    def _on_toggle_clicked(self):
        if self._expanded:
            self.collapse()
        else:
            self.expand_requested.emit(self)

    def _apply_collapsed_style(self):
        self.setStyleSheet(self.COLLAPSED_STYLE)

    # ── status summary ────────────────────────────────────────────────────────

    def _refresh_status(self):
        parts = []
        if self._chk_main.isChecked():
            fmt = self._main_fmt_combo.currentData().upper()
            parts.append(f"Main river ({fmt})")
        if self._chk_all.isChecked():
            fmt = self._all_fmt_combo.currentData().upper()
            parts.append(f"All flowlines ({fmt})")
        if self._chk_gages.isChecked():
            parts.append("Gages CSV")
        self._status_lbl.setText(
            "  ·  ".join(parts) if parts else "<i>nothing selected</i>"
        )

    # ── public API ────────────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        return True  # no required inputs for flowline step

    def get_config(self) -> dict:
        return {
            "save_main_river":    self._chk_main.isChecked(),
            "main_format":        self._main_fmt_combo.currentData(),
            "save_all_flowlines": self._chk_all.isChecked(),
            "all_format":         self._all_fmt_combo.currentData(),
            "cell_size_m":        self._cell_spin.value(),
            "save_gages_csv":     self._chk_gages.isChecked(),
        }

    def set_config(self, cfg: dict):
        self._chk_main.setChecked(cfg.get("save_main_river", True))
        idx = self._main_fmt_combo.findData(cfg.get("main_format", "shp"))
        if idx >= 0:
            self._main_fmt_combo.setCurrentIndex(idx)
        self._chk_all.setChecked(cfg.get("save_all_flowlines", False))
        idx = self._all_fmt_combo.findData(cfg.get("all_format", "shp"))
        if idx >= 0:
            self._all_fmt_combo.setCurrentIndex(idx)
        self._cell_spin.setValue(float(cfg.get("cell_size_m", 30.0)))
        self._chk_gages.setChecked(cfg.get("save_gages_csv", True))
        self._refresh_status()
