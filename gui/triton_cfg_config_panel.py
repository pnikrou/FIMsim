"""Reusable TRITON config (.cfg) panel.

The .cfg is generated automatically from the previous steps; this panel only
exposes the handful of knobs a user typically tweaks.  Everything else (file
references, num_sources/num_extbc, projection, sim_duration) is filled in by
core.triton_cfg.create_triton_cfg from the AOI's context.

Defaults follow the app design recommendation:
    output_format = ASC, output_option = SEQ, print_option = huv,
    time_step = 10, courant = 0.5, open_boundaries = 0 (explicit .extbc).
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QGroupBox, QComboBox, QDoubleSpinBox,
)
from PyQt6.QtCore import pyqtSignal


class TritonCfgConfigPanel(QWidget):
    config_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        gb = QGroupBox("Config options  (the rest is auto-generated)")
        form = QFormLayout(gb)

        self._out_fmt = QComboBox()
        self._out_fmt.addItems(["ASC", "GTIFF", "BIN"])
        form.addRow("Output format:", self._out_fmt)

        self._print_opt = QComboBox()
        self._print_opt.addItems(["huv", "h"])
        form.addRow("Print option:", self._print_opt)

        self._time_step = QDoubleSpinBox()
        self._time_step.setRange(0.001, 3600); self._time_step.setDecimals(3)
        self._time_step.setValue(10.0); self._time_step.setSuffix(" s")
        form.addRow("Time step:", self._time_step)

        self._print_int = QDoubleSpinBox()
        self._print_int.setRange(1, 1e7); self._print_int.setValue(3600)
        self._print_int.setSuffix(" s")
        form.addRow("Print interval:", self._print_int)

        self._courant = QDoubleSpinBox()
        self._courant.setRange(0.05, 1.0); self._courant.setDecimals(2)
        self._courant.setValue(0.5)
        form.addRow("Courant number:", self._courant)

        outer.addWidget(gb)

        for w in (self._out_fmt, self._print_opt):
            w.currentIndexChanged.connect(lambda *_: self.config_changed.emit())
        for w in (self._time_step, self._print_int, self._courant):
            w.valueChanged.connect(lambda *_: self.config_changed.emit())

    def is_ready(self) -> bool:
        return True

    def summary(self) -> str:
        return (f"{self._out_fmt.currentText()} · {self._print_opt.currentText()} "
                f"· dt {self._time_step.value():g}s")

    def get_config(self) -> dict:
        return {
            "output_format":  self._out_fmt.currentText(),
            "print_option":   self._print_opt.currentText(),
            "time_step":      float(self._time_step.value()),
            "print_interval": float(self._print_int.value()),
            "courant":        float(self._courant.value()),
        }

    def set_config(self, cfg: dict):
        if not cfg:
            return
        i = self._out_fmt.findText(cfg.get("output_format", "ASC"))
        if i >= 0:
            self._out_fmt.setCurrentIndex(i)
        i = self._print_opt.findText(cfg.get("print_option", "huv"))
        if i >= 0:
            self._print_opt.setCurrentIndex(i)
        if cfg.get("time_step") is not None:
            self._time_step.setValue(float(cfg["time_step"]))
        if cfg.get("print_interval") is not None:
            self._print_int.setValue(float(cfg["print_interval"]))
        if cfg.get("courant") is not None:
            self._courant.setValue(float(cfg["courant"]))
