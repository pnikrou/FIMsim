"""Per-AOI LULC + Manning configuration card.

Each card hosts:
  • Source combo  (NLCD 30 m  /  Sentinel-2 10 m)
  • Year selector  (NLCD dropdown  or  Sentinel-2 spin — swapped on source change)
  • LULC output format  (TIF / GPKG / ASC)
  • Cell size spin
  • Manning section:
      – output format combo
      – ManningTableWidget  (NLCD table or Sentinel-2 table — swapped with source)

Follows the same accordion pattern as AOIDEMCard / AOIManningCard.
"""
from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QPushButton,
    QComboBox, QDoubleSpinBox, QWidget, QGroupBox, QSizePolicy,
)
from PyQt6.QtCore import pyqtSignal

from gui.manning_table_widget import ManningTableWidget
from core.nlcd import NLCD_MANNING, SENTINEL2_MANNING


_LULC_SOURCES = [
    ("NLCD  (USGS, 30 m, USA)",           "nlcd"),
    ("Sentinel-2 / Esri  (10 m, global)", "sentinel2"),
]

_FMT_ITEMS = [
    ("TIF (GeoTIFF)",                "tif"),
    ("GPKG (GeoPackage)",            "gpkg"),
    ("ASC (ASCII grid)",             "asc"),
    ("SHP (Shapefile, polygonized)", "shp"),
]

_MN_FMT_ITEMS = [
    ("TIF (GeoTIFF)",                "tif"),
    ("GPKG (GeoPackage)",            "gpkg"),
    ("ASC (ASCII grid)",             "asc"),
    ("SHP (Shapefile, polygonized)", "shp"),
]


class AOILulcCard(QFrame):
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

    def __init__(self, aoi_name: str, parent=None, show_buffer: bool = False,
                 hecras_mode: bool = False):
        super().__init__(parent)
        self.setObjectName("card")
        self._aoi_name = aoi_name
        self._show_buffer = show_buffer
        self._hecras_mode = hecras_mode
        self._expanded = False
        self._build_ui()
        self.setStyleSheet(self.COLLAPSED_STYLE)
        self._refresh_status()

    # ── UI ────────────────────────────────────────────────────────────────────

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

        form = QFormLayout()
        form.setVerticalSpacing(6)
        form.setContentsMargins(0, 0, 0, 0)

        # Source combo
        self._src_combo = QComboBox()
        for lbl, val in _LULC_SOURCES:
            self._src_combo.addItem(lbl, val)
        self._src_combo.setFixedWidth(290)
        form.addRow("LULC source:", self._src_combo)

        # NLCD year row  (all official NLCD releases via MRLC WMS)
        self._nlcd_year_lbl = QLabel("NLCD year:")
        self._nlcd_year = QComboBox()
        for y in ("2021", "2019", "2016", "2013", "2011", "2008", "2006", "2004", "2001"):
            self._nlcd_year.addItem(y, y)
        form.addRow(self._nlcd_year_lbl, self._nlcd_year)

        # Sentinel-2 / ESRI year row  (ESRI Sentinel-2 10m LULC: 2017 – 2023)
        self._s2_year_lbl = QLabel("Sentinel-2 year:")
        self._s2_year = QComboBox()
        for y in ("2023", "2022", "2021", "2020", "2019", "2018", "2017"):
            self._s2_year.addItem(y, y)
        form.addRow(self._s2_year_lbl, self._s2_year)

        # LULC output format (hidden in HEC-RAS mode — always TIF)
        self._fmt_lbl = QLabel("LULC output format:")
        self._fmt_combo = QComboBox()
        for lbl, val in _FMT_ITEMS:
            self._fmt_combo.addItem(lbl, val)
        form.addRow(self._fmt_lbl, self._fmt_combo)
        if self._hecras_mode:
            self._fmt_lbl.setVisible(False)
            self._fmt_combo.setVisible(False)
            # Force TIF
            idx = self._fmt_combo.findData("tif")
            if idx >= 0:
                self._fmt_combo.setCurrentIndex(idx)

        # Cell size
        self._cell_spin = QDoubleSpinBox()
        self._cell_spin.setRange(1.0, 1000.0)
        self._cell_spin.setDecimals(1)
        self._cell_spin.setValue(30.0)
        self._cell_spin.setSuffix(" m")
        self._cell_spin.setFixedWidth(100)
        form.addRow("Cell size:", self._cell_spin)

        pl.addLayout(form)

        # ── Manning section ───────────────────────────────────────────────────
        mn_gb = QGroupBox("Manning's n")
        mn_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        mn_layout = QVBoxLayout(mn_gb)

        mn_fmt_form = QFormLayout()
        mn_fmt_form.setContentsMargins(0, 0, 0, 0)
        self._mn_fmt_lbl = QLabel("Manning output format:")
        self._mn_fmt_combo = QComboBox()
        for lbl, val in _MN_FMT_ITEMS:
            self._mn_fmt_combo.addItem(lbl, val)
        mn_fmt_form.addRow(self._mn_fmt_lbl, self._mn_fmt_combo)
        mn_layout.addLayout(mn_fmt_form)
        if self._hecras_mode:
            self._mn_fmt_lbl.setVisible(False)
            self._mn_fmt_combo.setVisible(False)
            # Force SHP — required by HEC-RAS RAS Mapper
            idx = self._mn_fmt_combo.findData("shp")
            if idx >= 0:
                self._mn_fmt_combo.setCurrentIndex(idx)
            hecras_note = QLabel(
                "<small><i>Output: LULC → TIF (reference)  ·  "
                "Manning → SHP (required by HEC-RAS RAS Mapper)</i></small>"
            )
            hecras_note.setStyleSheet("color:#718096;")
            hecras_note.setWordWrap(True)
            mn_layout.addWidget(hecras_note)

        info_lbl = QLabel(
            "<small><b>Manning's n table.</b>  Min/Max are reference bounds "
            "(read-only).  Edit the Avg column — values are clamped to "
            "[min, max].</small>"
        )
        info_lbl.setWordWrap(True)
        mn_layout.addWidget(info_lbl)

        self._manning_table = ManningTableWidget(NLCD_MANNING, self)
        self._manning_table.setMaximumHeight(260)
        mn_layout.addWidget(self._manning_table)

        pl.addWidget(mn_gb)

        # ── Buffer (optional) ─────────────────────────────────────────────────
        self._buffer_spin = QDoubleSpinBox()
        self._buffer_spin.setRange(0.0, 50000.0)
        self._buffer_spin.setDecimals(0)
        self._buffer_spin.setValue(100.0)
        self._buffer_spin.setSuffix(" m")
        self._buffer_lbl = QLabel("Buffer (each side):")
        form.addRow(self._buffer_lbl, self._buffer_spin)
        self._buffer_lbl.setVisible(self._show_buffer)
        self._buffer_spin.setVisible(self._show_buffer)

        self._panel.setVisible(False)
        outer.addWidget(self._panel)

        # Wire signals
        self._src_combo.currentIndexChanged.connect(self._on_source_changed)
        self._nlcd_year.currentIndexChanged.connect(self._on_config_changed)
        self._s2_year.currentIndexChanged.connect(self._on_config_changed)
        self._fmt_combo.currentIndexChanged.connect(self._on_config_changed)
        self._cell_spin.valueChanged.connect(self._on_config_changed)
        self._mn_fmt_combo.currentIndexChanged.connect(self._on_config_changed)

        # Set initial year-row visibility
        self._on_source_changed()

    # ── signal handlers ───────────────────────────────────────────────────────

    def _on_source_changed(self, *_):
        is_nlcd = self._src_combo.currentData() == "nlcd"
        self._nlcd_year_lbl.setVisible(is_nlcd)
        self._nlcd_year.setVisible(is_nlcd)
        self._s2_year_lbl.setVisible(not is_nlcd)
        self._s2_year.setVisible(not is_nlcd)
        self._cell_spin.setValue(30.0 if is_nlcd else 10.0)
        # Swap Manning table to match source
        self._manning_table.set_table_data(
            NLCD_MANNING if is_nlcd else SENTINEL2_MANNING
        )
        self._on_config_changed()

    def _on_config_changed(self, *_):
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

    # ── status summary ────────────────────────────────────────────────────────

    def _refresh_status(self):
        src = "NLCD" if self._src_combo.currentData() == "nlcd" else "Sentinel-2"
        year = (self._nlcd_year.currentText()
                if self._src_combo.currentData() == "nlcd"
                else self._s2_year.currentText())
        fmt  = self._fmt_combo.currentData().upper()
        cell = self._cell_spin.value()
        self._status_lbl.setText(f"{src} {year}  ·  {fmt}  ·  {cell:g} m")

    # ── public API ────────────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        return True   # no required fields — all have defaults

    def get_config(self) -> dict:
        cfg = {
            "lulc_source":    self._src_combo.currentData(),
            "cell_size_m":    self._cell_spin.value(),
            "lulc_format":    self._fmt_combo.currentData(),
            "nlcd_year":      self._nlcd_year.currentText(),
            "sentinel2_year": int(self._s2_year.currentData()),
            "do_manning":     True,
            "manning_format": self._mn_fmt_combo.currentData(),
            "manning_mapping": self._manning_table.get_mapping(),
        }
        if self._show_buffer:
            cfg["buffer_m"] = float(self._buffer_spin.value())
        return cfg

    def set_config(self, cfg: dict):
        # Source (triggers table swap + year-row visibility + cell size default)
        idx = self._src_combo.findData(cfg.get("lulc_source", "nlcd"))
        if idx >= 0:
            self._src_combo.setCurrentIndex(idx)
        # Year
        yr_idx = self._nlcd_year.findData(cfg.get("nlcd_year", "2021"))
        if yr_idx >= 0:
            self._nlcd_year.setCurrentIndex(yr_idx)
        s2_idx = self._s2_year.findData(str(cfg.get("sentinel2_year", "2023")))
        if s2_idx >= 0:
            self._s2_year.setCurrentIndex(s2_idx)
        # Format / cell
        fmt_idx = self._fmt_combo.findData(cfg.get("lulc_format", "tif"))
        if fmt_idx >= 0:
            self._fmt_combo.setCurrentIndex(fmt_idx)
        self._cell_spin.setValue(float(cfg.get("cell_size_m", 30.0)))
        if self._show_buffer and "buffer_m" in cfg:
            try:
                self._buffer_spin.setValue(float(cfg["buffer_m"]))
            except Exception:
                pass
        mn_idx = self._mn_fmt_combo.findData(cfg.get("manning_format", "tif"))
        if mn_idx >= 0:
            self._mn_fmt_combo.setCurrentIndex(mn_idx)
        # Manning table values (apply after source swap which reloaded the table)
        mapping = cfg.get("manning_mapping") or {}
        if mapping:
            int_mapping = {k: v for k, v in mapping.items()
                           if isinstance(k, int)}
            if int_mapping:
                self._manning_table.set_values(int_mapping)
        self._refresh_status()
