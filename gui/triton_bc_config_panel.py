"""Reusable TRITON BC configuration panel.

Per AOI the user chooses:
  * how the boundary geometry is found — Auto-detect from NHD (USA) OR Manual
    coordinates (inflow point + downstream segment), like the LISFLOOD BCI step.
  * the downstream boundary TYPE and, where relevant, one value:
        0 = Free flow / supercritical outflow   (no value)
        1 = Water level vs time                 (upload a stage time-series)
        2 = Normal slope                        (enter slope)
        3 = Froude number                       (enter Froude)

The .src / .extbc file-writing logic itself (core/triton_bc.py) is unchanged;
get_config() just returns the kwargs the BC orchestrator needs.
"""
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLabel,
    QComboBox, QDoubleSpinBox, QLineEdit, QPushButton, QFileDialog,
)
from PyQt6.QtCore import pyqtSignal


_BC_TYPES = [0, 1, 2, 3]
_BC_LABELS = [
    "0 — Free flow / supercritical outflow",
    "1 — Water level vs time  (upload stage file)",
    "2 — Normal slope",
    "3 — Froude number",
]


def _coord_spin():
    s = QDoubleSpinBox()
    s.setRange(-1e9, 1e9)
    s.setDecimals(3)
    s.setGroupSeparatorShown(False)
    return s


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

        # ── Geometry source: Auto-detect vs Manual ──
        gb_geom = QGroupBox("Boundary geometry")
        gf = QFormLayout(gb_geom)
        self._mode_combo = QComboBox()
        self._mode_combo.addItems([
            "Auto-detect from NHD (USA)",
            "Manual coordinates",
        ])
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        gf.addRow("Detect inflow / outflow:", self._mode_combo)

        # Manual coords (shown only in Manual mode)
        self._inflow_x = _coord_spin()
        self._inflow_y = _coord_spin()
        in_row = QHBoxLayout()
        in_row.addWidget(QLabel("X")); in_row.addWidget(self._inflow_x)
        in_row.addWidget(QLabel("Y")); in_row.addWidget(self._inflow_y)
        self._inflow_lbl = QLabel("Inflow point:")
        gf.addRow(self._inflow_lbl, _wrap(in_row))

        self._seg_x1 = _coord_spin(); self._seg_y1 = _coord_spin()
        self._seg_x2 = _coord_spin(); self._seg_y2 = _coord_spin()
        seg_row = QHBoxLayout()
        for lab, w in (("X1", self._seg_x1), ("Y1", self._seg_y1),
                       ("X2", self._seg_x2), ("Y2", self._seg_y2)):
            seg_row.addWidget(QLabel(lab)); seg_row.addWidget(w)
        self._seg_lbl = QLabel("Outflow segment:")
        gf.addRow(self._seg_lbl, _wrap(seg_row))

        self._manual_note = QLabel(
            "<small><i>Coordinates must be in the DEM's projected CRS. The "
            "outflow segment is a straight line along one DEM edge "
            "(start X1,Y1 → end X2,Y2).</i></small>"
        )
        self._manual_note.setWordWrap(True)
        self._manual_note.setStyleSheet("color:#718096;")
        gf.addRow(self._manual_note)
        outer.addWidget(gb_geom)

        # ── Downstream boundary type ──
        gb = QGroupBox("Downstream boundary condition")
        form = QFormLayout(gb)
        self._type_combo = QComboBox()
        self._type_combo.addItems(_BC_LABELS)
        self._type_combo.setCurrentIndex(0)
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        form.addRow("Boundary type:", self._type_combo)

        self._slope_spin = QDoubleSpinBox()
        self._slope_spin.setDecimals(5); self._slope_spin.setRange(0.00001, 1.0)
        self._slope_spin.setSingleStep(0.0005); self._slope_spin.setValue(0.001)
        self._slope_lbl = QLabel("Normal slope:")
        form.addRow(self._slope_lbl, self._slope_spin)

        self._froude_spin = QDoubleSpinBox()
        self._froude_spin.setDecimals(3); self._froude_spin.setRange(0.001, 10.0)
        self._froude_spin.setSingleStep(0.05); self._froude_spin.setValue(0.5)
        self._froude_lbl = QLabel("Froude number:")
        form.addRow(self._froude_lbl, self._froude_spin)

        stage_row = QHBoxLayout()
        self._stage_edit = QLineEdit()
        self._stage_edit.setPlaceholderText("Path to water-level time-series .txt")
        self._stage_browse = QPushButton("Browse…"); self._stage_browse.setFixedWidth(80)
        self._stage_browse.clicked.connect(self._browse_stage)
        stage_row.addWidget(self._stage_edit); stage_row.addWidget(self._stage_browse)
        self._stage_lbl = QLabel("Stage file:")
        form.addRow(self._stage_lbl, _wrap(stage_row))
        self._stage_note = QLabel(
            "<small><i>Water-surface ELEVATION over time (not discharge), two "
            "columns with a % header:<br><code>% time(hr), water level(m)</code> "
            "→ <code>0,255.0</code> / <code>1,255.2</code></i></small>"
        )
        self._stage_note.setWordWrap(True)
        self._stage_note.setStyleSheet("color:#718096;")
        form.addRow(self._stage_note)
        outer.addWidget(gb)

        # wire change signals
        for w in (self._mode_combo, self._type_combo):
            w.currentIndexChanged.connect(lambda *_: self.config_changed.emit())
        for w in (self._slope_spin, self._froude_spin, self._inflow_x, self._inflow_y,
                  self._seg_x1, self._seg_y1, self._seg_x2, self._seg_y2):
            w.valueChanged.connect(lambda *_: self.config_changed.emit())
        self._stage_edit.textChanged.connect(lambda *_: self.config_changed.emit())

        self._on_mode_changed()
        self._on_type_changed()

    # ── state machines ──────────────────────────────────────────────────────

    def _on_mode_changed(self, *_):
        manual = self._mode_combo.currentIndex() == 1
        for w in (self._inflow_lbl, self._inflow_x, self._inflow_y,
                  self._seg_lbl, self._seg_x1, self._seg_y1, self._seg_x2,
                  self._seg_y2, self._manual_note):
            w.setVisible(manual)

    def _on_type_changed(self, *_):
        bt = _BC_TYPES[self._type_combo.currentIndex()]
        self._slope_lbl.setVisible(bt == 2); self._slope_spin.setVisible(bt == 2)
        self._froude_lbl.setVisible(bt == 3); self._froude_spin.setVisible(bt == 3)
        is1 = (bt == 1)
        for w in (self._stage_lbl, self._stage_edit, self._stage_browse, self._stage_note):
            w.setVisible(is1)

    def _browse_stage(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "Select stage time-series file", "",
            "Text files (*.txt *.csv);;All files (*)")
        if f:
            self._stage_edit.setText(f)

    # ── public API ────────────────────────────────────────────────────────────

    def bc_type(self) -> int:
        return _BC_TYPES[self._type_combo.currentIndex()]

    def detect_mode(self) -> str:
        return "manual" if self._mode_combo.currentIndex() == 1 else "auto"

    def is_ready(self) -> bool:
        if self.bc_type() == 1:
            p = self._stage_edit.text().strip()
            return bool(p) and Path(p).exists()
        return True

    def summary(self) -> str:
        mode = "Manual" if self.detect_mode() == "manual" else "Auto-NHD"
        bt = self.bc_type()
        if bt == 0:
            t = "Free outflow"
        elif bt == 1:
            p = self._stage_edit.text().strip()
            t = f"Stage: {Path(p).name}" if p else "Stage (pick file)"
        elif bt == 2:
            t = f"Slope {self._slope_spin.value():g}"
        else:
            t = f"Froude {self._froude_spin.value():g}"
        return f"{mode} · Type {bt} — {t}"

    def get_config(self) -> dict:
        bt = self.bc_type()
        cfg = {"detect_mode": self.detect_mode(), "bc_type": bt}
        if cfg["detect_mode"] == "manual":
            cfg["inflow_xy"] = (float(self._inflow_x.value()), float(self._inflow_y.value()))
            cfg["segment"] = (float(self._seg_x1.value()), float(self._seg_y1.value()),
                              float(self._seg_x2.value()), float(self._seg_y2.value()))
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
        self._mode_combo.setCurrentIndex(1 if cfg.get("detect_mode") == "manual" else 0)
        if cfg.get("inflow_xy"):
            self._inflow_x.setValue(float(cfg["inflow_xy"][0]))
            self._inflow_y.setValue(float(cfg["inflow_xy"][1]))
        if cfg.get("segment"):
            s = cfg["segment"]
            self._seg_x1.setValue(float(s[0])); self._seg_y1.setValue(float(s[1]))
            self._seg_x2.setValue(float(s[2])); self._seg_y2.setValue(float(s[3]))
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
        self._on_mode_changed()
        self._on_type_changed()


def _wrap(layout):
    w = QWidget()
    w.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)
    return w
