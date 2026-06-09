"""Step 7 of TRITON workflow — Configuration (.cfg).

Exposes every documented TRITON cfg keyword: simulation control, output
control, projection, solver params, runoff, observation / time-series,
initial conditions (warm restart), and user-editable .cfg filename.
"""
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QGroupBox, QFormLayout, QDoubleSpinBox, QSpinBox, QCheckBox,
    QComboBox, QScrollArea, QFrame, QFileDialog,
)
from PyQt6.QtCore import pyqtSignal

from core.triton_cfg import create_triton_cfg
from gui.worker import Worker
from gui.run_button import set_running, set_ready


def _sep():
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("color:#e2e8f0;")
    return line


def _browse_row(placeholder="Browse for file…"):
    """Return (QLineEdit, QPushButton, QHBoxLayout)."""
    edit = QLineEdit()
    edit.setPlaceholderText(placeholder)
    btn = QPushButton("Browse…")
    btn.setFixedWidth(80)
    row = QHBoxLayout()
    row.addWidget(edit)
    row.addWidget(btn)
    return edit, btn, row


class StepTritonCfgWidget(QWidget):
    step_completed = pyqtSignal(dict)

    def __init__(self, log_fn, parent=None):
        super().__init__(parent)
        self._log    = log_fn
        self._worker = None
        self._ctx_path = None
        self._ctx      = None
        self._setup_ui()

    # ── context ────────────────────────────────────────────────────────────────
    def set_context(self, ctx_path, ctx):
        self._ctx_path = ctx_path
        self._ctx      = ctx
        if not ctx:
            return
        proj = ctx.get("project_name", "output")
        if not self._cfg_name_edit.text().strip():
            self._cfg_name_edit.setText(f"{proj}.cfg")
        if not self._output_folder_edit.text().strip():
            self._output_folder_edit.setText(f"output_{proj}")

        sim_dur = ctx.get("sim_duration")
        if sim_dur and float(sim_dur) > 0:
            self._sim_dur_spin.setValue(float(sim_dur))

        epsg = ctx.get("dem_epsg") or ctx.get("crs_epsg")
        if epsg and not self._proj_edit.text().strip():
            self._proj_edit.setText(f"EPSG:{epsg}")

        ns = int(ctx.get("num_sources", 1))
        nb = int(ctx.get("num_extbc", 1))
        self._counts_lbl.setText(
            f"<small><b>From BC step:</b> num_sources={ns}, num_extbc={nb} "
            "(set by the BC / Hydro steps — edit there if wrong).</small>"
        )

    # ── UI ─────────────────────────────────────────────────────────────────────
    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setSpacing(12)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        root = QVBoxLayout(inner)
        root.setSpacing(10)
        scroll.setWidget(inner)
        outer.addWidget(scroll)

        # Info line
        self._counts_lbl = QLabel("")
        self._counts_lbl.setWordWrap(True)
        root.addWidget(self._counts_lbl)

        # ── CFG filename ─────────────────────────────────────────────────────
        fn_gb = QGroupBox("Configuration filename")
        fn_form = QFormLayout(fn_gb)
        self._cfg_name_edit = QLineEdit()
        self._cfg_name_edit.setPlaceholderText("e.g. NeuseRiver_20day.cfg")
        fn_form.addRow(".cfg filename:", self._cfg_name_edit)
        root.addWidget(fn_gb)

        # ── Simulation control ────────────────────────────────────────────────
        sim_gb = QGroupBox("Simulation control")
        sim_form = QFormLayout(sim_gb)

        self._sim_dur_spin = QDoubleSpinBox()
        self._sim_dur_spin.setRange(1, 1e9); self._sim_dur_spin.setDecimals(0)
        self._sim_dur_spin.setValue(86400); self._sim_dur_spin.setSuffix(" s")
        sim_form.addRow("sim_duration:", self._sim_dur_spin)

        self._dt_spin = QSpinBox()
        self._dt_spin.setRange(1, 3600); self._dt_spin.setValue(10); self._dt_spin.setSuffix(" s")
        sim_form.addRow("time_step:", self._dt_spin)

        self._fixed_dt_chk = QCheckBox("time_increment_fixed = 1 (constant dt)")
        sim_form.addRow(self._fixed_dt_chk)

        self._sim_start_spin = QSpinBox()
        self._sim_start_spin.setRange(0, 10_000_000); self._sim_start_spin.setValue(0)
        self._sim_start_spin.setSuffix(" s")
        sim_form.addRow("sim_start_time:", self._sim_start_spin)

        self._checkpoint_spin = QSpinBox()
        self._checkpoint_spin.setRange(0, 10_000); self._checkpoint_spin.setValue(0)
        sim_form.addRow("checkpoint_id:", self._checkpoint_spin)

        root.addWidget(sim_gb)

        # ── Output control ────────────────────────────────────────────────────
        out_gb = QGroupBox("Output control")
        out_form = QFormLayout(out_gb)

        self._print_interval_spin = QDoubleSpinBox()
        self._print_interval_spin.setRange(1, 1e8); self._print_interval_spin.setDecimals(0)
        self._print_interval_spin.setValue(3600); self._print_interval_spin.setSuffix(" s")
        out_form.addRow("print_interval:", self._print_interval_spin)

        self._print_opt_combo = QComboBox()
        self._print_opt_combo.addItems(["huv  (depth + u + v)", "h  (depth only)"])
        out_form.addRow("print_option:", self._print_opt_combo)

        self._out_fmt_combo = QComboBox()
        self._out_fmt_combo.addItems([
            "GTIFF  (GeoTIFF — recommended)",
            "ASC    (ASCII raster)",
            "BIN    (Binary)",
        ])
        out_form.addRow("output_format:", self._out_fmt_combo)

        self._in_fmt_combo = QComboBox()
        self._in_fmt_combo.addItems(["ASC", "BIN"])
        out_form.addRow("input_format:", self._in_fmt_combo)

        self._out_opt_combo = QComboBox()
        self._out_opt_combo.addItems(["SEQ  (sequential)", "PAR  (parallel)"])
        out_form.addRow("output_option:", self._out_opt_combo)

        self._output_folder_edit = QLineEdit()
        self._output_folder_edit.setPlaceholderText("e.g. output_myproject")
        out_form.addRow("output_folder:", self._output_folder_edit)

        self._outfile_pattern_edit = QLineEdit()
        self._outfile_pattern_edit.setText('"%s/%s/%s_%02d_%02d"')
        out_form.addRow("outfile_pattern:", self._outfile_pattern_edit)

        root.addWidget(out_gb)

        # ── Projection ────────────────────────────────────────────────────────
        proj_gb = QGroupBox("Projection (GeoTIFF output)")
        proj_form = QFormLayout(proj_gb)
        self._proj_edit = QLineEdit()
        self._proj_edit.setPlaceholderText("e.g. EPSG:32615  (auto-filled from DEM if available)")
        proj_form.addRow("projection:", self._proj_edit)
        root.addWidget(proj_gb)

        # ── Runoff ───────────────────────────────────────────────────────────
        run_gb = QGroupBox("Runoff (optional — leave disabled if unused)")
        run_form = QFormLayout(run_gb)

        self._num_runoffs_spin = QSpinBox()
        self._num_runoffs_spin.setRange(0, 1000); self._num_runoffs_spin.setValue(0)
        self._num_runoffs_spin.valueChanged.connect(self._toggle_runoff)
        run_form.addRow("num_runoffs:", self._num_runoffs_spin)

        self._runoff_file_edit, rfbtn, rfrow = _browse_row("Path to runoff hydrograph (.hyg)")
        rfbtn.clicked.connect(lambda: self._browse_into(
            self._runoff_file_edit, "Runoff hydrograph", "HYG/Text (*.hyg *.txt *.csv);;All files (*)"))
        self._runoff_file_lbl = QLabel("runoff_filename:")
        run_form.addRow(self._runoff_file_lbl, rfrow)

        self._runoff_map_edit, rmbtn, rmrow = _browse_row("Path to runoff map (.asc / .rmap)")
        rmbtn.clicked.connect(lambda: self._browse_into(
            self._runoff_map_edit, "Runoff map", "ASC/RMAP (*.asc *.rmap);;All files (*)"))
        self._runoff_map_lbl = QLabel("runoff_map:")
        run_form.addRow(self._runoff_map_lbl, rmrow)

        root.addWidget(run_gb)
        self._runoff_widgets = [
            self._runoff_file_lbl, self._runoff_file_edit,
            self._runoff_map_lbl,  self._runoff_map_edit,
        ]
        self._runoff_browse_btns = [rfbtn, rmbtn]
        self._toggle_runoff(0)

        # ── Observation / time series ────────────────────────────────────────
        obs_gb = QGroupBox("Observation / time-series output")
        obs_form = QFormLayout(obs_gb)
        self._time_series_chk = QCheckBox("time_series_flag = 1  (emit per-gauge hydrographs)")
        obs_form.addRow(self._time_series_chk)
        self._obs_file_edit, obtn, obrow = _browse_row("observation_loc_file path")
        obtn.clicked.connect(lambda: self._browse_into(
            self._obs_file_edit, "Observation location file", "Text/CSV (*.txt *.csv *.obs);;All files (*)"))
        obs_form.addRow("observation_loc_file:", obrow)
        root.addWidget(obs_gb)

        # ── Initial conditions (restart) ─────────────────────────────────────
        ic_gb = QGroupBox("Initial conditions (leave blank for clean start)")
        ic_form = QFormLayout(ic_gb)
        self._h_edit,  hbtn,  hrow  = _browse_row("h_infile path")
        self._qx_edit, qxbtn, qxrow = _browse_row("qx_infile path")
        self._qy_edit, qybtn, qyrow = _browse_row("qy_infile path")
        hbtn.clicked.connect(lambda:  self._browse_into(self._h_edit,  "h_infile",  "All files (*)"))
        qxbtn.clicked.connect(lambda: self._browse_into(self._qx_edit, "qx_infile", "All files (*)"))
        qybtn.clicked.connect(lambda: self._browse_into(self._qy_edit, "qy_infile", "All files (*)"))
        ic_form.addRow("h_infile:",  hrow)
        ic_form.addRow("qx_infile:", qxrow)
        ic_form.addRow("qy_infile:", qyrow)
        root.addWidget(ic_gb)

        # ── Numerics ──────────────────────────────────────────────────────────
        num_gb = QGroupBox("Numerical parameters")
        num_form = QFormLayout(num_gb)

        self._courant_spin = QDoubleSpinBox()
        self._courant_spin.setRange(0.01, 1.0); self._courant_spin.setDecimals(2)
        self._courant_spin.setValue(0.5)
        num_form.addRow("courant:", self._courant_spin)

        self._hextra_spin = QDoubleSpinBox()
        self._hextra_spin.setRange(0.0001, 1.0); self._hextra_spin.setDecimals(4)
        self._hextra_spin.setValue(0.001)
        num_form.addRow("hextra:", self._hextra_spin)

        root.addWidget(num_gb)

        # ── GPU / parallelism ────────────────────────────────────────────────
        gpu_gb = QGroupBox("GPU / parallelism")
        gpu_form = QFormLayout(gpu_gb)

        self._gpu_direct_chk = QCheckBox("gpu_direct_flag = 1  (CUDA-aware MPI)")
        gpu_form.addRow(self._gpu_direct_chk)

        self._dd_combo = QComboBox()
        self._dd_combo.addItems(["static", "dynamic"])
        gpu_form.addRow("domain_decomposition:", self._dd_combo)

        self._factor_dd_spin = QSpinBox()
        self._factor_dd_spin.setRange(1, 64); self._factor_dd_spin.setValue(4)
        gpu_form.addRow("factor_interval_domain_decomposition:", self._factor_dd_spin)

        self._open_bc_chk = QCheckBox("open_boundaries = 1")
        self._open_bc_chk.setChecked(True)
        gpu_form.addRow(self._open_bc_chk)

        root.addWidget(gpu_gb)

        # ── Run ──────────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._run_btn = QPushButton("Write TRITON CFG file")
        self._run_btn.setStyleSheet(
            "font-weight:bold; padding:7px 20px; background:#2b6cb0; color:white; border-radius:4px;"
        )
        self._run_btn.clicked.connect(self._run_step)
        btn_row.addWidget(self._run_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

        self._error_lbl = QLabel("")
        self._error_lbl.setWordWrap(True)
        self._error_lbl.setStyleSheet(
            "padding:10px; background:#fff5f5; border:1px solid #fc8181; "
            "border-radius:4px; font-size:12px; color:#c53030;"
        )
        self._error_lbl.setVisible(False)
        root.addWidget(self._error_lbl)

        self._report = QLabel("")
        self._report.setWordWrap(True)
        self._report.setStyleSheet(
            "padding:12px; background:#ebf8ff; border:1px solid #63b3ed; "
            "border-radius:4px; font-size:12px;"
        )
        self._report.setVisible(False)
        root.addWidget(self._report)
        root.addStretch()

    # ── helpers ────────────────────────────────────────────────────────────────
    def _browse_into(self, edit, title, filt):
        f, _ = QFileDialog.getOpenFileName(self, f"Select {title}", "", filt)
        if f:
            edit.setText(f)

    def _toggle_runoff(self, n):
        enabled = n > 0
        for w in self._runoff_widgets:
            w.setEnabled(enabled)
        for b in self._runoff_browse_btns:
            b.setEnabled(enabled)

    def _print_opt(self):
        return "h" if self._print_opt_combo.currentIndex() == 1 else "huv"

    def _out_fmt(self):
        return ["GTIFF", "ASC", "BIN"][self._out_fmt_combo.currentIndex()]

    def _in_fmt(self):
        return ["ASC", "BIN"][self._in_fmt_combo.currentIndex()]

    def _out_opt(self):
        return ["SEQ", "PAR"][self._out_opt_combo.currentIndex()]

    # ── run ────────────────────────────────────────────────────────────────────
    def _run_step(self):
        if not self._ctx_path or not self._ctx:
            self._log("Complete earlier steps first.")
            return

        kw = dict(
            ctx_path=self._ctx_path,
            ctx=self._ctx,
            cfg_filename=self._cfg_name_edit.text().strip() or None,
            sim_duration=self._sim_dur_spin.value(),
            time_step=float(self._dt_spin.value()),
            time_increment_fixed=1 if self._fixed_dt_chk.isChecked() else 0,
            sim_start_time=self._sim_start_spin.value(),
            checkpoint_id=self._checkpoint_spin.value(),
            print_interval=self._print_interval_spin.value(),
            print_option=self._print_opt(),
            output_format=self._out_fmt(),
            input_format=self._in_fmt(),
            output_option=self._out_opt(),
            output_folder=self._output_folder_edit.text().strip() or None,
            outfile_pattern=self._outfile_pattern_edit.text().strip() or '"%s/%s/%s_%02d_%02d"',
            projection=self._proj_edit.text().strip(),
            num_runoffs=self._num_runoffs_spin.value(),
            runoff_filename=self._runoff_file_edit.text().strip(),
            runoff_map=self._runoff_map_edit.text().strip(),
            time_series_flag=1 if self._time_series_chk.isChecked() else 0,
            observation_loc_file=self._obs_file_edit.text().strip(),
            h_infile=self._h_edit.text().strip(),
            qx_infile=self._qx_edit.text().strip(),
            qy_infile=self._qy_edit.text().strip(),
            courant=self._courant_spin.value(),
            hextra=self._hextra_spin.value(),
            gpu_direct_flag=1 if self._gpu_direct_chk.isChecked() else 0,
            domain_decomposition=self._dd_combo.currentText(),
            factor_interval_domain_decomposition=self._factor_dd_spin.value(),
            open_boundaries=1 if self._open_bc_chk.isChecked() else 0,
        )

        self._error_lbl.setVisible(False)
        self._report.setVisible(False)
        set_running(self._run_btn)

        self._worker = Worker(create_triton_cfg, **kw)
        self._worker.message.connect(self._log)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    # ── callbacks ──────────────────────────────────────────────────────────────
    def _on_done(self, ctx):
        self._error_lbl.setVisible(False)
        self._ctx = ctx
        set_ready(self._run_btn)
        self._show_final_summary(ctx)
        self.step_completed.emit({"ctx_path": self._ctx_path, "ctx": ctx})

    def _on_error(self, msg):
        self._log(f"ERROR: {msg}")
        set_ready(self._run_btn)
        self._error_lbl.setText(
            f"<b>Error:</b> {msg.split(chr(10))[0]}<br>"
            "<small>(See log panel below for full details)</small>"
        )
        self._error_lbl.setVisible(True)

    def _show_final_summary(self, ctx):
        triton_dir   = ctx.get("triton_dir", "")
        project_name = ctx.get("project_name", "")
        fric_mode    = ctx.get("triton_fric_mode", "fixed")
        fpfric       = ctx.get("par_fpfric", "0.035")
        ns           = ctx.get("num_sources", 1)
        nb           = ctx.get("num_extbc",   1)
        nr           = ctx.get("triton_num_runoffs", 0)
        ts_flag      = ctx.get("triton_time_series_flag", 0)

        def _fl(label, path):
            p      = Path(path) if path else None
            exists = p and p.exists()
            icon   = "" if exists else ""
            return f"{icon} <b>{label}:</b> {path}<br>"

        cfg_path     = ctx.get("triton_cfg_path", "")
        dem_asc      = str(Path(triton_dir) / "dem.asc") if triton_dir else ""
        extbc_path   = ctx.get("triton_extbc_path", "")
        src_loc_path = ctx.get("triton_src_loc_path", "")
        hyg_path     = ctx.get("triton_hyg_path", ctx.get("triton_hydro_path", ""))

        lines = [
            _fl("CFG file", cfg_path),
            _fl("DEM ASCII", dem_asc),
        ]
        if fric_mode == "varying":
            lines.append(_fl("Friction ASCII", str(Path(triton_dir) / f"{project_name}.asc")))
        else:
            lines.append(f"<b>const_mann:</b> {fpfric}<br>")
        lines += [
            _fl("External BC", extbc_path),
            _fl("Source locations", src_loc_path),
            _fl("Hydrograph", hyg_path) if hyg_path else "",
        ]

        sim_s = ctx.get("triton_sim_duration", ctx.get("sim_duration", ""))
        sim_h = f"{float(sim_s) / 3600:.1f} h" if sim_s else "n/a"

        html = (
            "<b>TRITON preprocessing complete.</b><br><br>"
            f"<b>Input directory:</b> {triton_dir}<br>"
            f"<b>Simulation duration:</b> {sim_s} s  ({sim_h})<br>"
            f"<b>num_sources:</b> {ns}  |  <b>num_extbc:</b> {nb}  |  "
            f"<b>num_runoffs:</b> {nr}  |  <b>time_series_flag:</b> {ts_flag}<br><br>"
            "<b>Required input files:</b><br>"
            + "".join(lines)
            + f"<br><b>Run the simulation (from {triton_dir}):</b><br>"
            f"<code>triton {Path(cfg_path).name if cfg_path else '<cfg>'}</code>"
        )
        self._report.setText(html)
        self._report.setVisible(True)
