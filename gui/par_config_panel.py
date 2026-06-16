"""Reusable LISFLOOD-FP PAR configuration panel.

Self-contained widget with all PAR-file knobs (file names, timing,
solver, initial condition, checkpointing, output flags, extra keywords).
Used in two places:
  * single-AOI workflow → embedded directly in step_par.
  * multi-AOI accordion → one panel per AOI inside an AOIPARCard.
"""
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLabel,
    QLineEdit, QPushButton, QFileDialog, QComboBox, QDoubleSpinBox,
    QCheckBox, QPlainTextEdit, QFrame,
)
from PyQt6.QtCore import pyqtSignal


_SOLVER_ITEMS = [
    ("acceleration",              "Acceleration (ACC) — recommended for most cases"),
    ("adaptive_default",          "Adaptive (auto timestep, default tolerances)"),
    ("adaptive_fixed_timestep",   "Adaptive fixed timestep  (adaptoff)"),
    ("acceleration_with_routing", "ACC + 1D sub-grid channel routing"),
    ("diffusion",                 "Diffusion wave  (simple / slow shallow flows)"),
]
_SOLVER_KEYS   = [k for k, _ in _SOLVER_ITEMS]
_SOLVER_LABELS = [v for _, v in _SOLVER_ITEMS]

_START_ITEMS = [
    ("none",       "Dry start — no initial water"),
    ("startfile",  "Initial water-depth raster  (startfile)"),
    ("startelev",  "Initial water-surface elevation raster  (startelev)"),
    ("loadcheck",  "Restart from checkpoint file  (loadcheck)"),
]
_START_KEYS   = [k for k, _ in _START_ITEMS]
_START_LABELS = [v for _, v in _START_ITEMS]


def _read_bdy_sim_time(bdy_path: str):
    """Read the last time value (seconds) from a LISFLOOD-FP .bdy file.
    Returns None on any failure or when the file is malformed."""
    if not bdy_path:
        return None
    p = Path(bdy_path)
    if not p.exists():
        return None
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return None
    data = [l.strip() for l in lines if l.strip()]
    if len(data) < 4:
        return None
    parts = data[-1].split()
    if len(parts) >= 2:
        try:
            return float(parts[1])
        except ValueError:
            pass
    return None


class PARConfigPanel(QWidget):
    config_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self._wire_signals()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        # ── Output file names ──
        gb_files = QGroupBox("Output file names")
        ff = QFormLayout(gb_files)
        self._par_name_edit = QLineEdit("model.par")
        ff.addRow("PAR filename:", self._par_name_edit)
        self._resroot_edit = QLineEdit("output")
        ff.addRow("Result prefix (resroot):", self._resroot_edit)
        self._results_dir_edit = QLineEdit("results")
        ff.addRow("Results folder:", self._results_dir_edit)
        outer.addWidget(gb_files)

        # ── Simulation timing ──
        gb_time = QGroupBox("Simulation timing")
        ft = QFormLayout(gb_time)
        self._sim_time_spin = QDoubleSpinBox()
        self._sim_time_spin.setRange(1, 1e10)
        self._sim_time_spin.setValue(86400)
        self._sim_time_spin.setSuffix(" s")
        ft.addRow("Simulation time:", self._sim_time_spin)

        self._tstep_spin = QDoubleSpinBox()
        self._tstep_spin.setRange(0.001, 3600)
        self._tstep_spin.setDecimals(3)
        self._tstep_spin.setValue(1.0)
        self._tstep_spin.setSuffix(" s")
        ft.addRow("Initial timestep:", self._tstep_spin)

        self._saveint_spin = QDoubleSpinBox()
        self._saveint_spin.setRange(1, 1e9)
        self._saveint_spin.setValue(3600)
        self._saveint_spin.setSuffix(" s")
        ft.addRow("Save interval:", self._saveint_spin)

        self._massint_spin = QDoubleSpinBox()
        self._massint_spin.setRange(1, 1e9)
        self._massint_spin.setValue(3600)
        self._massint_spin.setSuffix(" s")
        ft.addRow("Mass-balance interval:", self._massint_spin)
        outer.addWidget(gb_time)

        # ── Solver ──
        gb_solver = QGroupBox("Solver")
        fs = QFormLayout(gb_solver)
        self._solver_combo = QComboBox()
        self._solver_combo.addItems(_SOLVER_LABELS)
        self._solver_combo.setCurrentIndex(0)
        self._solver_combo.currentIndexChanged.connect(self._toggle_routing)
        fs.addRow("Floodplain solver:", self._solver_combo)

        self._routing_frame = QFrame()
        rf = QFormLayout(self._routing_frame)
        rf.setContentsMargins(0, 0, 0, 0)
        self._routingspeed_spin = QDoubleSpinBox()
        self._routingspeed_spin.setRange(0.01, 100.0)
        self._routingspeed_spin.setDecimals(2)
        self._routingspeed_spin.setValue(1.0)
        self._routingspeed_spin.setSuffix(" m/s")
        rf.addRow("Routing wave speed:", self._routingspeed_spin)
        self._routesfthresh_spin = QDoubleSpinBox()
        self._routesfthresh_spin.setRange(0.0, 10.0)
        self._routesfthresh_spin.setDecimals(4)
        self._routesfthresh_spin.setValue(0.0)
        self._routesfthresh_spin.setSuffix(" m")
        rf.addRow("Route surface-flow threshold:", self._routesfthresh_spin)
        self._depththresh_spin = QDoubleSpinBox()
        self._depththresh_spin.setRange(0.0, 10.0)
        self._depththresh_spin.setDecimals(5)
        self._depththresh_spin.setValue(0.001)
        self._depththresh_spin.setSuffix(" m")
        rf.addRow("Depth threshold (dry cell):", self._depththresh_spin)
        self._routing_frame.setVisible(False)
        fs.addRow(self._routing_frame)

        self._drycheck_combo = QComboBox()
        self._drycheck_combo.addItems([
            "leave_default_off  (LISFLOOD default)",
            "drycheckon         (check dry cells at every step)",
            "drycheckoff        (skip dry-cell check)",
        ])
        fs.addRow("Dry-cell check:", self._drycheck_combo)
        outer.addWidget(gb_solver)

        # ── Initial condition ──
        gb_ic = QGroupBox("Initial condition")
        fi = QFormLayout(gb_ic)
        self._start_combo = QComboBox()
        self._start_combo.addItems(_START_LABELS)
        self._start_combo.currentIndexChanged.connect(self._toggle_startfile)
        fi.addRow("Start condition:", self._start_combo)

        file_row = QHBoxLayout()
        self._startfile_edit = QLineEdit()
        self._startfile_edit.setPlaceholderText(
            "Browse for initial-condition file…"
        )
        self._start_browse_btn = QPushButton("Browse…")
        self._start_browse_btn.setFixedWidth(80)
        self._start_browse_btn.clicked.connect(self._browse_startfile)
        file_row.addWidget(self._startfile_edit)
        file_row.addWidget(self._start_browse_btn)
        self._startfile_lbl = QLabel("File:")
        fi.addRow(self._startfile_lbl, file_row)
        self._startfile_lbl.setVisible(False)
        self._startfile_edit.setVisible(False)
        self._start_browse_btn.setVisible(False)
        outer.addWidget(gb_ic)

        # ── Checkpointing ──
        gb_chk = QGroupBox("Checkpointing & restart")
        fck = QFormLayout(gb_chk)
        chk_row = QHBoxLayout()
        self._chk_checkpoint = QCheckBox("Enable checkpointing every")
        self._checkpoint_spin = QDoubleSpinBox()
        self._checkpoint_spin.setRange(0.001, 1000)
        self._checkpoint_spin.setValue(1.0)
        self._checkpoint_spin.setSuffix(" computation-hours")
        self._checkpoint_spin.setEnabled(False)
        self._chk_checkpoint.toggled.connect(self._checkpoint_spin.setEnabled)
        chk_row.addWidget(self._chk_checkpoint)
        chk_row.addWidget(self._checkpoint_spin)
        chk_row.addStretch()
        fck.addRow(chk_row)
        outer.addWidget(gb_chk)

        # ── Output options ──
        gb_out = QGroupBox("Output options")
        fo = QFormLayout(gb_out)
        self._chk_elevoff = QCheckBox(
            "elevoff   — suppress water-surface elevation grids"
        )
        self._chk_elevoff.setChecked(True)
        fo.addRow(self._chk_elevoff)
        self._chk_depthoff = QCheckBox(
            "depthoff  — suppress water-depth grids"
        )
        fo.addRow(self._chk_depthoff)
        self._chk_binary = QCheckBox(
            "binary_out — write rasters as binary instead of ASCII"
        )
        fo.addRow(self._chk_binary)
        self._chk_hazard = QCheckBox(
            "hazard    — write hazard (depth × velocity) grids"
        )
        fo.addRow(self._chk_hazard)
        self._chk_mint_hk = QCheckBox(
            "mint_hk   — record max depth/velocity at each massint interval"
        )
        self._chk_mint_hk.setChecked(True)
        fo.addRow(self._chk_mint_hk)
        self._chk_qoutput = QCheckBox(
            "qoutput   — write boundary-discharge timeseries to file"
        )
        fo.addRow(self._chk_qoutput)
        self._chk_sgc_enable = QCheckBox(
            "sgc_enable  — activate sub-grid channel scheme (SGC)"
        )
        self._chk_sgc_enable.setChecked(True)
        fo.addRow(self._chk_sgc_enable)

        op_row = QHBoxLayout()
        self._chk_overpass = QCheckBox("overpass  — save snapshot at time")
        self._overpass_spin = QDoubleSpinBox()
        self._overpass_spin.setRange(1, 1e10)
        self._overpass_spin.setValue(100000)
        self._overpass_spin.setSuffix(" s")
        self._overpass_spin.setEnabled(False)
        self._chk_overpass.toggled.connect(self._overpass_spin.setEnabled)
        op_row.addWidget(self._chk_overpass)
        op_row.addWidget(self._overpass_spin)
        op_row.addStretch()
        fo.addRow(op_row)
        outer.addWidget(gb_out)

        # ── Extra keywords ──
        gb_extra = QGroupBox("Extra PAR keywords")
        fe = QFormLayout(gb_extra)
        self._extra_edit = QPlainTextEdit()
        self._extra_edit.setPlaceholderText(
            "One keyword (or keyword   value) per line\n"
            "e.g.    cuda\n"
            "        SGCwidth   5.0"
        )
        self._extra_edit.setMaximumHeight(80)
        fe.addRow(self._extra_edit)
        outer.addWidget(gb_extra)

    # ── visibility helpers ────────────────────────────────────────────────────

    def _toggle_routing(self, idx):
        self._routing_frame.setVisible(
            _SOLVER_KEYS[idx] == "acceleration_with_routing"
        )
        self._emit_changed()

    def _toggle_startfile(self, idx):
        need = _START_KEYS[idx] != "none"
        self._startfile_lbl.setVisible(need)
        self._startfile_edit.setVisible(need)
        self._start_browse_btn.setVisible(need)
        if not need:
            self._startfile_edit.clear()
        self._emit_changed()

    def _browse_startfile(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "Select initial-condition file", "", "All files (*)"
        )
        if f:
            self._startfile_edit.setText(f)

    # ── change emitter ────────────────────────────────────────────────────────

    def _wire_signals(self):
        for w in (
            self._par_name_edit, self._resroot_edit, self._results_dir_edit,
            self._startfile_edit,
        ):
            w.textChanged.connect(self._emit_changed)
        for w in (
            self._sim_time_spin, self._tstep_spin, self._saveint_spin,
            self._massint_spin, self._routingspeed_spin, self._routesfthresh_spin,
            self._depththresh_spin, self._checkpoint_spin, self._overpass_spin,
        ):
            w.valueChanged.connect(self._emit_changed)
        for w in (
            self._solver_combo, self._drycheck_combo, self._start_combo,
        ):
            w.currentIndexChanged.connect(self._emit_changed)
        for w in (
            self._chk_checkpoint, self._chk_overpass,
            self._chk_elevoff, self._chk_depthoff, self._chk_binary,
            self._chk_hazard, self._chk_mint_hk, self._chk_qoutput,
            self._chk_sgc_enable,
        ):
            w.toggled.connect(self._emit_changed)
        self._extra_edit.textChanged.connect(self._emit_changed)

    def _emit_changed(self, *_):
        self.config_changed.emit()

    # ── public API ────────────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        """All PAR fields have defaults; the form is always runnable."""
        # If start condition is not 'none', a path must be present.
        idx = self._start_combo.currentIndex()
        if _START_KEYS[idx] != "none":
            return bool(self._startfile_edit.text().strip())
        return True

    def apply_ctx_defaults(self, ctx: dict, aoi_name: str = None):
        """Pre-fill the form from the current per-AOI context.

        aoi_name, if provided, drives the PAR filename, resroot, and
        results folder.  Falls back to ctx['aoi_name'] then
        ctx['project_name'].
        """
        if not ctx:
            return
        name = (aoi_name
                or ctx.get("aoi_name")
                or ctx.get("project_name")
                or "model") or "model"
        self._par_name_edit.setText(f"{name}.par")
        self._resroot_edit.setText(name)
        self._results_dir_edit.setText(f"{name}_Results")

        bdy_path = ctx.get("bdy_path")
        if bdy_path:
            t = _read_bdy_sim_time(bdy_path)
            if t and t > 0:
                self._sim_time_spin.setValue(t)
                return

        # Fallback — event_start / event_end
        from datetime import datetime
        start = ctx.get("event_start")
        end = ctx.get("event_end")
        if start and end:
            try:
                d0 = datetime.strptime(start, "%Y-%m-%d %H:%M")
                d1 = datetime.strptime(end, "%Y-%m-%d %H:%M")
                self._sim_time_spin.setValue((d1 - d0).total_seconds())
            except Exception:
                pass

    def get_config(self) -> dict:
        solver_key = _SOLVER_KEYS[self._solver_combo.currentIndex()]
        use_routing = (solver_key == "acceleration_with_routing")
        start_mode = _START_KEYS[self._start_combo.currentIndex()]
        sf = self._startfile_edit.text().strip() or None
        drycheck_raw = self._drycheck_combo.currentText().split()[0]
        extra_lines = [
            ln.strip()
            for ln in self._extra_edit.toPlainText().splitlines()
            if ln.strip()
        ]
        return {
            "par_name":         self._par_name_edit.text().strip() or "model.par",
            "resroot":          self._resroot_edit.text().strip() or "output",
            "results_dir_name": self._results_dir_edit.text().strip() or "results",
            "sim_time":         self._sim_time_spin.value(),
            "initial_tstep":    self._tstep_spin.value(),
            "saveint":          self._saveint_spin.value(),
            "massint":          self._massint_spin.value(),
            "solver_mode":      solver_key,
            "drycheck_mode":    drycheck_raw,
            "start_mode":       start_mode,
            "startfile_path":   sf if start_mode == "startfile" else None,
            "startelev_path":   sf if start_mode == "startelev" else None,
            "loadcheck_path":   sf if start_mode == "loadcheck" else None,
            "routing_speed":    self._routingspeed_spin.value() if use_routing else None,
            "routesfthresh":    self._routesfthresh_spin.value() if use_routing else None,
            "depththresh":      self._depththresh_spin.value() if use_routing else None,
            "use_checkpoint":   self._chk_checkpoint.isChecked(),
            "checkpoint_hours": self._checkpoint_spin.value() if self._chk_checkpoint.isChecked() else None,
            "use_overpass":     self._chk_overpass.isChecked(),
            "overpass_time":    self._overpass_spin.value() if self._chk_overpass.isChecked() else None,
            "use_elevoff":      self._chk_elevoff.isChecked(),
            "use_depthoff":     self._chk_depthoff.isChecked(),
            "use_binary_out":   self._chk_binary.isChecked(),
            "use_hazard":       self._chk_hazard.isChecked(),
            "use_mint_hk":      self._chk_mint_hk.isChecked(),
            "use_qoutput":      self._chk_qoutput.isChecked(),
            "sgc_enable":       self._chk_sgc_enable.isChecked(),
            "extra_lines":      extra_lines,
        }

    def reset(self):
        """Start clean for a new run: drop any picked initial-condition file
        and return the start mode to 'none'.  Other numeric defaults are left
        as-is (they are re-seeded per AOI from ctx)."""
        try:
            self._start_combo.setCurrentIndex(0)
            self._startfile_edit.clear()
        except Exception:
            pass

    def set_config(self, cfg: dict):
        """Restore selections from a config dict (used by Apply-to-all)."""
        if not cfg:
            return
        self._par_name_edit.setText(cfg.get("par_name", "model.par"))
        self._resroot_edit.setText(cfg.get("resroot", "output"))
        self._results_dir_edit.setText(cfg.get("results_dir_name", "results"))
        self._sim_time_spin.setValue(float(cfg.get("sim_time", 86400)))
        self._tstep_spin.setValue(float(cfg.get("initial_tstep", 1.0)))
        self._saveint_spin.setValue(float(cfg.get("saveint", 3600)))
        self._massint_spin.setValue(float(cfg.get("massint", 3600)))

        try:
            self._solver_combo.setCurrentIndex(
                _SOLVER_KEYS.index(cfg.get("solver_mode", "acceleration"))
            )
        except ValueError:
            pass

        # Drycheck — match the keyword text at the start of each combo item
        drycheck = cfg.get("drycheck_mode", "leave_default_off")
        for i in range(self._drycheck_combo.count()):
            if self._drycheck_combo.itemText(i).split()[0] == drycheck:
                self._drycheck_combo.setCurrentIndex(i)
                break

        try:
            self._start_combo.setCurrentIndex(
                _START_KEYS.index(cfg.get("start_mode", "none"))
            )
        except ValueError:
            pass

        # Whichever start-path we have, drop into the line edit.
        for k in ("startfile_path", "startelev_path", "loadcheck_path"):
            v = cfg.get(k)
            if v:
                self._startfile_edit.setText(str(v))
                break

        if cfg.get("routing_speed") is not None:
            self._routingspeed_spin.setValue(float(cfg["routing_speed"]))
        if cfg.get("routesfthresh") is not None:
            self._routesfthresh_spin.setValue(float(cfg["routesfthresh"]))
        if cfg.get("depththresh") is not None:
            self._depththresh_spin.setValue(float(cfg["depththresh"]))

        self._chk_checkpoint.setChecked(bool(cfg.get("use_checkpoint", False)))
        if cfg.get("checkpoint_hours") is not None:
            self._checkpoint_spin.setValue(float(cfg["checkpoint_hours"]))

        self._chk_overpass.setChecked(bool(cfg.get("use_overpass", False)))
        if cfg.get("overpass_time") is not None:
            self._overpass_spin.setValue(float(cfg["overpass_time"]))

        self._chk_elevoff.setChecked(bool(cfg.get("use_elevoff", True)))
        self._chk_depthoff.setChecked(bool(cfg.get("use_depthoff", False)))
        self._chk_binary.setChecked(bool(cfg.get("use_binary_out", False)))
        self._chk_hazard.setChecked(bool(cfg.get("use_hazard", False)))
        self._chk_mint_hk.setChecked(bool(cfg.get("use_mint_hk", True)))
        self._chk_qoutput.setChecked(bool(cfg.get("use_qoutput", False)))
        self._chk_sgc_enable.setChecked(bool(cfg.get("sgc_enable", True)))

        extra = cfg.get("extra_lines") or []
        self._extra_edit.setPlainText("\n".join(str(x) for x in extra))
