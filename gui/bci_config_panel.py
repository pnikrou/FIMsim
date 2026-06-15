"""Reusable Boundary-Condition (BCI) configuration panel.

Self-contained form widget with the same structure as the legacy single-AOI
BCI page: coordinate detection (NHD / Manual), upstream boundary
(QVAR / QFIX) and downstream boundary (FREE / HFIX), plus the conditional
spin boxes that appear for each.

Used in two places:
  * single-AOI workflow         → one panel embedded directly in step_bci.
  * multi-AOI accordion (>1 AOI) → one panel per AOI inside its
    AOIBCICard, so each AOI gets its own boundary configuration.

Public surface mirrors ManningConfigPanel — config_changed signal, plus
get_config() / set_config() so the "Apply to all" button can broadcast
one AOI's selections to every other AOI.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QComboBox, QDoubleSpinBox, QFrame,
)
from PyQt6.QtCore import pyqtSignal


def _sep() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("color:#e2e8f0;")
    return line


class BCIConfigPanel(QWidget):
    config_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        form = QFormLayout()
        outer.addLayout(form)

        # ── Coordinate detection ─────────────────────────────────────────
        # Single combo replaces the NHD/Manual radio pair.  Index 0 is the
        # placeholder so the user must make an explicit choice before
        # anything else appears.
        self._detect_combo = QComboBox()
        self._detect_combo.addItems([
            "—  pick a method  —",
            "Auto-detect from NHD (USA)",
            "Enter coordinates manually",
        ])
        form.addRow("<b>Coordinate detection:</b>", self._detect_combo)

        # CRS hint — shown only when Manual is picked.  Tells the user
        # exactly what coordinate system / units to type their values in.
        # Filled in by set_aoi_path() if the AOI is known.
        self._crs_hint = QLabel(
            "<small><i>Enter coordinates in your AOI's CRS (the same CRS "
            "as your AOI shapefile).</i></small>"
        )
        self._crs_hint.setWordWrap(True)
        self._crs_hint.setStyleSheet(
            "padding:6px 10px; background:#fffbeb; border:1px solid #f6e05e; "
            "border-radius:4px; color:#744210;"
        )
        form.addRow(self._crs_hint)
        self._crs_hint.setVisible(False)

        # Manual coordinate fields (shown only when Manual is picked)
        self._up_x_spin = self._make_coord_spin()
        self._up_x_lbl  = QLabel("Upstream X:")
        form.addRow(self._up_x_lbl, self._up_x_spin)

        self._up_y_spin = self._make_coord_spin()
        self._up_y_lbl  = QLabel("Upstream Y:")
        form.addRow(self._up_y_lbl, self._up_y_spin)

        self._dn_x_spin = self._make_coord_spin()
        self._dn_x_lbl  = QLabel("Downstream X:")
        form.addRow(self._dn_x_lbl, self._dn_x_spin)

        self._dn_y_spin = self._make_coord_spin()
        self._dn_y_lbl  = QLabel("Downstream Y:")
        form.addRow(self._dn_y_lbl, self._dn_y_spin)

        for w in (self._up_x_lbl, self._up_x_spin,
                  self._up_y_lbl, self._up_y_spin,
                  self._dn_x_lbl, self._dn_x_spin,
                  self._dn_y_lbl, self._dn_y_spin):
            w.setVisible(False)

        # Separator + the boundary GroupBoxes are wrapped in a holder
        # widget so the whole "post-detection" section can be hidden when
        # the user hasn't picked a detection method yet.
        self._sep = _sep()
        form.addRow(self._sep)
        self._sep.setVisible(False)

        # ── Upstream boundary ────────────────────────────────────────────
        self._up_combo = QComboBox()
        self._up_combo.addItems([
            "Varying discharge (QVAR — requires BDY file)",
            "Fixed discharge (QFIX)",
        ])
        self._up_combo_lbl = QLabel("Upstream boundary:")
        form.addRow(self._up_combo_lbl, self._up_combo)

        self._fixed_q_spin = QDoubleSpinBox()
        self._fixed_q_spin.setRange(0, 1e9)
        self._fixed_q_spin.setValue(100.0)
        self._fixed_q_spin.setSuffix(" m³/s")
        self._fixed_q_lbl = QLabel("Fixed discharge:")
        form.addRow(self._fixed_q_lbl, self._fixed_q_spin)
        self._fixed_q_lbl.setVisible(False)
        self._fixed_q_spin.setVisible(False)

        # ── Downstream boundary ──────────────────────────────────────────
        self._dn_combo = QComboBox()
        self._dn_combo.addItems([
            "Free normal depth (FREE)",
            "Fixed water level (HFIX)",
        ])
        self._dn_combo_lbl = QLabel("Downstream boundary:")
        form.addRow(self._dn_combo_lbl, self._dn_combo)

        self._slope_spin = QDoubleSpinBox()
        self._slope_spin.setRange(1e-7, 1.0)
        self._slope_spin.setDecimals(6)
        self._slope_spin.setValue(0.0001)
        self._slope_lbl = QLabel("Bed slope (FREE):")
        form.addRow(self._slope_lbl, self._slope_spin)

        self._hfix_spin = QDoubleSpinBox()
        self._hfix_spin.setRange(-1000, 10000)
        self._hfix_spin.setValue(0.0)
        self._hfix_spin.setSuffix(" m")
        self._hfix_lbl = QLabel("Fixed water elevation (HFIX):")
        form.addRow(self._hfix_lbl, self._hfix_spin)
        self._hfix_lbl.setVisible(False)
        self._hfix_spin.setVisible(False)

        # Hide upstream / downstream rows until a detection method is picked
        for w in (self._up_combo_lbl, self._up_combo,
                  self._dn_combo_lbl, self._dn_combo,
                  self._slope_lbl, self._slope_spin):
            w.setVisible(False)

        # ── wire signals
        self._detect_combo.currentIndexChanged.connect(self._on_detect_changed)
        self._up_combo.currentIndexChanged.connect(self._on_up_changed)
        self._dn_combo.currentIndexChanged.connect(self._on_dn_changed)

        # Default: Auto-detect from NHD (index 1)
        self._detect_combo.setCurrentIndex(1)
        for spin in (self._up_x_spin, self._up_y_spin,
                     self._dn_x_spin, self._dn_y_spin,
                     self._fixed_q_spin, self._slope_spin, self._hfix_spin):
            spin.valueChanged.connect(self._emit_changed)

    @staticmethod
    def _make_coord_spin() -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setRange(-1e8, 1e8)
        s.setDecimals(3)
        return s

    # ── visibility ────────────────────────────────────────────────────────────

    def _on_detect_changed(self, *_):
        idx = self._detect_combo.currentIndex()
        nhd       = idx == 1
        manual    = idx == 2
        any_picked = idx >= 1

        # CRS hint and manual coord fields — only when Manual is picked
        if hasattr(self, "_crs_hint"):
            self._crs_hint.setVisible(manual)
        for w in (self._up_x_lbl, self._up_x_spin,
                  self._up_y_lbl, self._up_y_spin,
                  self._dn_x_lbl, self._dn_x_spin,
                  self._dn_y_lbl, self._dn_y_spin):
            w.setVisible(manual)

        # Upstream / downstream sections + separator only after EITHER
        # detection method is picked
        if hasattr(self, "_sep"):
            self._sep.setVisible(any_picked)
        for w in (self._up_combo_lbl, self._up_combo,
                  self._dn_combo_lbl, self._dn_combo):
            w.setVisible(any_picked)
        # Conditional rows depend on the dropdown selections
        self._on_up_changed()
        self._on_dn_changed()
        self._emit_changed()

    def _on_up_changed(self, *_):
        # Only show the fixed-Q spin when a detection method is picked AND
        # the upstream combo is on QFIX.  Otherwise the row stays hidden.
        any_picked = self._detect_combo.currentIndex() >= 1
        is_fixed = any_picked and (self._up_combo.currentIndex() == 1)
        self._fixed_q_lbl.setVisible(is_fixed)
        self._fixed_q_spin.setVisible(is_fixed)
        self._emit_changed()

    def _on_dn_changed(self, *_):
        any_picked = self._detect_combo.currentIndex() >= 1
        is_hfix = self._dn_combo.currentIndex() == 1
        self._slope_lbl.setVisible(any_picked and not is_hfix)
        self._slope_spin.setVisible(any_picked and not is_hfix)
        self._hfix_lbl.setVisible(any_picked and is_hfix)
        self._hfix_spin.setVisible(any_picked and is_hfix)
        self._emit_changed()

    def _emit_changed(self, *_):
        self.config_changed.emit()

    # ── public API ────────────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        # User must explicitly pick a detection method before the run
        # button appears.
        return self._detect_combo.currentIndex() >= 1

    def set_aoi_path(self, source_file: str, feature_index: int = 0):
        """Read the AOI's CRS and update the CRS hint text so the user
        knows what units to type the manual coordinates in."""
        try:
            import geopandas as gpd
            gdf = gpd.read_file(source_file)
            crs = gdf.crs
            if crs is None:
                hint = (
                    "<small><i>Enter coordinates in your AOI's CRS — "
                    "the AOI shapefile has no CRS defined; please assign "
                    "one in a GIS tool first.</i></small>"
                )
            else:
                # Show the most user-friendly identifier we can find
                crs_str = (
                    crs.to_string() if hasattr(crs, "to_string") else str(crs)
                )
                if crs.is_geographic:
                    units_str = "degrees (longitude, latitude)"
                else:
                    units_str = "metres in this CRS (easting, northing)"
                hint = (
                    f"<small><b>AOI CRS:</b> <code>{crs_str}</code><br>"
                    f"Enter <b>X</b> and <b>Y</b> in <b>{units_str}</b>."
                    f"</small>"
                )
            self._crs_hint.setText(hint)
        except Exception as ex:
            self._crs_hint.setText(
                f"<small><i>Enter coordinates in your AOI's CRS "
                f"(could not read CRS: {ex})</i></small>"
            )

    def get_config(self) -> dict:
        return {
            "use_nhd":            self._detect_combo.currentIndex() == 1,
            "upstream_mode":      ("fixed_discharge"
                                   if self._up_combo.currentIndex() == 1
                                   else "varying_discharge"),
            "downstream_type":    "HFIX" if self._dn_combo.currentIndex() == 1 else "FREE",
            "fixed_q":            float(self._fixed_q_spin.value()),
            "slope":              float(self._slope_spin.value()),
            "hfix":               float(self._hfix_spin.value()),
            "up_x":               float(self._up_x_spin.value()),
            "up_y":               float(self._up_y_spin.value()),
            "dn_x":               float(self._dn_x_spin.value()),
            "dn_y":               float(self._dn_y_spin.value()),
        }

    def set_config(self, cfg: dict):
        if not cfg:
            return
        # Detection
        self._detect_combo.setCurrentIndex(1 if cfg.get("use_nhd", True) else 2)
        # Upstream
        self._up_combo.setCurrentIndex(
            1 if cfg.get("upstream_mode") == "fixed_discharge" else 0
        )
        self._fixed_q_spin.setValue(float(cfg.get("fixed_q", 100.0)))
        # Downstream
        self._dn_combo.setCurrentIndex(
            1 if cfg.get("downstream_type") == "HFIX" else 0
        )
        self._slope_spin.setValue(float(cfg.get("slope", 0.0001)))
        self._hfix_spin.setValue(float(cfg.get("hfix", 0.0)))
        # Manual coords
        self._up_x_spin.setValue(float(cfg.get("up_x", 0.0)))
        self._up_y_spin.setValue(float(cfg.get("up_y", 0.0)))
        self._dn_x_spin.setValue(float(cfg.get("dn_x", 0.0)))
        self._dn_y_spin.setValue(float(cfg.get("dn_y", 0.0)))
