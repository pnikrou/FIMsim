"""Reusable TRITON BC configuration panel.

Deliberately simple (per app design): the inflow source point and the
downstream boundary *segment* are auto-derived from the flowline + DEM by the
core (detect_main_river); the user only chooses the downstream boundary TYPE
and, where relevant, one value:

    0 = Free flow / supercritical outflow   (no value)
    1 = Water level vs time                 (upload a stage time-series .txt)
    2 = Normal slope                        (enter slope)
    3 = Froude number                       (enter Froude)

Used in two places, like the LISFLOOD config panels:
  * single-AOI workflow → embedded directly in step_triton_bc
  * multi-AOI accordion → one panel per AOI inside an AOITritonBCCard

get_config() returns the kwargs the BC orchestrator needs to build the single
.extbc entry; the file-writing logic itself is unchanged.
"""
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLabel,
    QComboBox, QDoubleSpinBox, QLineEdit, QPushButton, QFileDialog,
)
from PyQt6.QtCore import pyqtSignal


# combo index → BC type code
_BC_TYPES = [0, 1, 2, 3]
_BC_LABELS = [
    "0 — Free flow / supercritical outflow",
    "1 — Water level vs time  (upload stage file)",
    "2 — Normal slope",
    "3 — Froude number",
]


class TritonBCConfigPanel(QWidget):
    config_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        hint = QLabel(
            "<small>The inflow point and the downstream boundary segment are "
            "found automatically from the river + DEM. Choose how water leaves "
            "the domain at the downstream boundary:<br>"
            "<b>0</b> free outflow · <b>1</b> water level vs time · "
            "<b>2</b> normal slope · <b>3</b> Froude number.</small>"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(
            "padding:6px 8px; background:#fffbeb; border:1px solid #f6e05e; "
            "border-radius:4px; color:#5b4708;"
        )
        outer.addWidget(hint)

        gb = QGroupBox("Downstream boundary condition")
        form = QFormLayout(gb)

        self._type_combo = QComboBox()
        self._type_combo.addItems(_BC_LABELS)
        self._type_combo.setCurrentIndex(0)
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        form.addRow("Boundary type:", self._type_combo)

        # Type 2 — normal slope
        self._slope_spin = QDoubleSpinBox()
        self._slope_spin.setDecimals(5)
        self._slope_spin.setRange(0.00001, 1.0)
        self._slope_spin.setSingleStep(0.0005)
        self._slope_spin.setValue(0.001)
        self._slope_lbl = QLabel("Normal slope:")
        form.addRow(self._slope_lbl, self._slope_spin)

        # Type 3 — Froude number
        self._froude_spin = QDoubleSpinBox()
        self._froude_spin.setDecimals(3)
        self._froude_spin.setRange(0.001, 10.0)
        self._froude_spin.setSingleStep(0.05)
        self._froude_spin.setValue(0.5)
        self._froude_lbl = QLabel("Froude number:")
        form.addRow(self._froude_lbl, self._froude_spin)

        # Type 1 — stage time-series file
        stage_row = QHBoxLayout()
        self._stage_edit = QLineEdit()
        self._stage_edit.setPlaceholderText("Path to water-level time-series .txt")
        self._stage_browse = QPushButton("Browse…")
        self._stage_browse.setFixedWidth(80)
        self._stage_browse.clicked.connect(self._browse_stage)
        stage_row.addWidget(self._stage_edit)
        stage_row.addWidget(self._stage_browse)
        self._stage_lbl = QLabel("Stage file:")
        form.addRow(self._stage_lbl, stage_row)

        self._stage_note = QLabel(
            "<small><i>Stage file = water-surface ELEVATION over time (not "
            "discharge). Two columns with a % header:<br>"
            "<code>% time(hr), water level(m)</code><br>"
            "<code>0,255.0</code> &nbsp; <code>1,255.2</code> &nbsp; "
            "<code>2,255.5</code></i></small>"
        )
        self._stage_note.setWordWrap(True)
        self._stage_note.setStyleSheet("color:#718096;")
        form.addRow(self._stage_note)

        outer.addWidget(gb)

        self._type_combo.currentIndexChanged.connect(lambda *_: self.config_changed.emit())
        self._slope_spin.valueChanged.connect(lambda *_: self.config_changed.emit())
        self._froude_spin.valueChanged.connect(lambda *_: self.config_changed.emit())
        self._stage_edit.textChanged.connect(lambda *_: self.config_changed.emit())

        self._on_type_changed()

    # ── visibility state machine ──────────────────────────────────────────────

    def _on_type_changed(self, *_):
        bt = _BC_TYPES[self._type_combo.currentIndex()]
        self._slope_lbl.setVisible(bt == 2)
        self._slope_spin.setVisible(bt == 2)
        self._froude_lbl.setVisible(bt == 3)
        self._froude_spin.setVisible(bt == 3)
        is1 = (bt == 1)
        for w in (self._stage_lbl, self._stage_edit, self._stage_browse, self._stage_note):
            w.setVisible(is1)

    def _browse_stage(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "Select stage time-series file", "", "Text files (*.txt *.csv);;All files (*)"
        )
        if f:
            self._stage_edit.setText(f)

    # ── public API ────────────────────────────────────────────────────────────

    def bc_type(self) -> int:
        return _BC_TYPES[self._type_combo.currentIndex()]

    def is_ready(self) -> bool:
        bt = self.bc_type()
        if bt == 1:
            p = self._stage_edit.text().strip()
            return bool(p) and Path(p).exists()
        return True   # 0, 2, 3 always have a value/default

    def summary(self) -> str:
        bt = self.bc_type()
        if bt == 0:
            return "Type 0 — Free outflow"
        if bt == 1:
            p = self._stage_edit.text().strip()
            return f"Type 1 — Stage file: {Path(p).name}" if p else "Type 1 — pick stage file"
        if bt == 2:
            return f"Type 2 — Normal slope {self._slope_spin.value():g}"
        return f"Type 3 — Froude {self._froude_spin.value():g}"

    def get_config(self) -> dict:
        bt = self.bc_type()
        cfg = {"bc_type": bt}
        if bt == 1:
            cfg["stage_file_path"] = self._stage_edit.text().strip()
        elif bt == 2:
            cfg["value"] = float(self._slope_spin.value())
        elif bt == 3:
            cfg["value"] = float(self._froude_spin.value())
        return cfg

    def set_config(self, cfg: dict):
        if not cfg:
            return
        bt = int(cfg.get("bc_type", 0))
        try:
            self._type_combo.setCurrentIndex(_BC_TYPES.index(bt))
        except ValueError:
            self._type_combo.setCurrentIndex(0)
        if bt == 1 and cfg.get("stage_file_path"):
            self._stage_edit.setText(str(cfg["stage_file_path"]))
        elif bt == 2 and cfg.get("value") is not None:
            self._slope_spin.setValue(float(cfg["value"]))
        elif bt == 3 and cfg.get("value") is not None:
            self._froude_spin.setValue(float(cfg["value"]))
        self._on_type_changed()
