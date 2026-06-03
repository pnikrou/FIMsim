"""Step 4 of TRITON workflow — Boundary Conditions editor.

Two-table interface:

  • Inflow Sources    — 1 row per upstream inflow point (→ src_loc_file)
  • External BC rows  — 1 row per downstream / lateral BC (→ .extbc)

Either table can be populated manually, or prefilled with a single click by
running NHD main-river auto-detection.  Output filenames are editable.
"""
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QFormLayout, QComboBox, QDoubleSpinBox, QPushButton,
    QProgressBar, QFrame, QLineEdit, QFileDialog,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QMessageBox,
)
from PyQt6.QtCore import pyqtSignal, Qt

from core.triton_bc import prepare_triton_bc, detect_main_river
from gui.worker import Worker
from gui.run_button import set_running, set_ready


# BC type metadata
_BC_TYPES = [
    ("0 — Free flow",   0),
    ("1 — Stage file",  1),
    ("2 — Slope",       2),
    ("3 — Froude",      3),
]


def _sep():
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("color:#e2e8f0;")
    return line


class StepTritonBCWidget(QWidget):
    step_completed = pyqtSignal(dict)

    def __init__(self, log_fn, parent=None):
        super().__init__(parent)
        self._log      = log_fn
        self._worker   = None
        self._detector = None
        self._ctx_path = None
        self._ctx      = None
        self._setup_ui()

    # ── context ───────────────────────────────────────────────────────────────
    def set_context(self, ctx_path, ctx):
        self._ctx_path = ctx_path
        self._ctx      = ctx
        if not ctx:
            return
        proj = ctx.get("project_name", "triton")
        if not self._extbc_name_edit.text().strip():
            self._extbc_name_edit.setText(f"{proj}.extbc")
        if not self._src_loc_name_edit.text().strip():
            self._src_loc_name_edit.setText(f"{proj}_inflow_loc.txt")

    # ── UI ────────────────────────────────────────────────────────────────────
    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)

        # Info banner
        info = QLabel(
            "<b>Two files are written:</b><br>"
            "• <b>Inflow sources file</b> — one coordinate per upstream inflow (also sets <code>num_sources</code>).<br>"
            "• <b>.extbc file</b> — one line per external boundary (also sets <code>num_extbc</code>).<br>"
            "Use <b>Auto-populate from NHD</b> to prefill a single inflow point plus a downstream segment "
            "based on the main river in the AOI (USA only). Add or edit rows manually as needed."
        )
        info.setWordWrap(True)
        info.setStyleSheet(
            "padding:8px; background:#fffbeb; border:1px solid #f6e05e; border-radius:4px;"
        )
        root.addWidget(info)

        # ── NHD detection controls ───────────────────────────────────────────
        nhd_gb = QGroupBox("NHD main-river auto-populate (optional)")
        nhd_form = QFormLayout(nhd_gb)

        self._seg_width_spin = QDoubleSpinBox()
        self._seg_width_spin.setRange(50.0, 50000.0)
        self._seg_width_spin.setDecimals(0)
        self._seg_width_spin.setValue(500.0)
        self._seg_width_spin.setSuffix(" m")
        nhd_form.addRow("BC segment half-width:", self._seg_width_spin)

        self._nhd_type_combo = QComboBox()
        for lbl, _ in _BC_TYPES:
            self._nhd_type_combo.addItem(lbl)
        self._nhd_type_combo.setCurrentIndex(2)  # default Type 2 (slope)
        self._nhd_type_combo.currentIndexChanged.connect(self._toggle_nhd_bc_value)
        nhd_form.addRow("Downstream BC type to stage:", self._nhd_type_combo)

        self._nhd_slope_spin = QDoubleSpinBox()
        self._nhd_slope_spin.setRange(0.000001, 1.0)
        self._nhd_slope_spin.setDecimals(6)
        self._nhd_slope_spin.setValue(0.0005)
        nhd_form.addRow("Slope (for Type 2):", self._nhd_slope_spin)

        self._nhd_froude_spin = QDoubleSpinBox()
        self._nhd_froude_spin.setRange(0.0, 10.0)
        self._nhd_froude_spin.setDecimals(3)
        self._nhd_froude_spin.setValue(1.0)
        self._nhd_froude_spin.setVisible(False)
        self._nhd_froude_lbl = QLabel("Froude value (for Type 3):")
        self._nhd_froude_lbl.setVisible(False)
        nhd_form.addRow(self._nhd_froude_lbl, self._nhd_froude_spin)

        nhd_stage_row = QHBoxLayout()
        self._nhd_stage_edit = QLineEdit()
        self._nhd_stage_edit.setPlaceholderText("Browse for stage file…")
        nhd_stage_btn = QPushButton("Browse…")
        nhd_stage_btn.setFixedWidth(80)
        nhd_stage_btn.clicked.connect(lambda: self._browse_into(self._nhd_stage_edit))
        nhd_stage_row.addWidget(self._nhd_stage_edit)
        nhd_stage_row.addWidget(nhd_stage_btn)
        self._nhd_stage_lbl = QLabel("Stage file (for Type 1):")
        self._nhd_stage_lbl.setVisible(False)
        self._nhd_stage_edit.setVisible(False)
        nhd_stage_btn.setVisible(False)
        self._nhd_stage_btn = nhd_stage_btn
        nhd_form.addRow(self._nhd_stage_lbl, nhd_stage_row)

        nhd_btn_row = QHBoxLayout()
        self._nhd_btn = QPushButton("↻  Run NHD & add rows to tables")
        self._nhd_btn.setStyleSheet(
            "font-weight:bold; padding:6px 16px; background:#276749; color:white; border-radius:4px;"
        )
        self._nhd_btn.clicked.connect(self._run_nhd)
        nhd_btn_row.addWidget(self._nhd_btn)
        nhd_btn_row.addStretch()
        nhd_form.addRow(nhd_btn_row)

        root.addWidget(nhd_gb)

        # ── Inflow Sources table ─────────────────────────────────────────────
        src_gb = QGroupBox("Inflow sources  (→ src_loc_file)")
        src_v  = QVBoxLayout(src_gb)
        self._src_table = QTableWidget(0, 2)
        self._src_table.setHorizontalHeaderLabels(["X (easting)", "Y (northing)"])
        self._src_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._src_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._src_table.setMinimumHeight(110)
        src_v.addWidget(self._src_table)
        src_btn_row = QHBoxLayout()
        add_src = QPushButton("+ Add source")
        rm_src  = QPushButton("− Remove selected")
        add_src.clicked.connect(lambda: self._add_src_row(0.0, 0.0))
        rm_src.clicked.connect(lambda: self._remove_selected(self._src_table))
        src_btn_row.addWidget(add_src)
        src_btn_row.addWidget(rm_src)
        src_btn_row.addStretch()
        src_v.addLayout(src_btn_row)
        root.addWidget(src_gb)

        # ── External BC table ────────────────────────────────────────────────
        bc_gb = QGroupBox("External boundary conditions  (→ .extbc)")
        bc_v  = QVBoxLayout(bc_gb)
        self._bc_table = QTableWidget(0, 6)
        self._bc_table.setHorizontalHeaderLabels([
            "Type", "X1", "Y1", "X2", "Y2", "Value / stage file"
        ])
        h = self._bc_table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._bc_table.setColumnWidth(0, 120)
        for i in range(1, 5):
            self._bc_table.setColumnWidth(i, 110)
        h.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self._bc_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._bc_table.setMinimumHeight(140)
        bc_v.addWidget(self._bc_table)
        bc_btn_row = QHBoxLayout()
        add_bc = QPushButton("+ Add BC")
        rm_bc  = QPushButton("− Remove selected")
        add_bc.clicked.connect(lambda: self._add_bc_row(2, 0.0, 0.0, 0.0, 0.0, "0.0005"))
        rm_bc.clicked.connect(lambda: self._remove_selected(self._bc_table))
        bc_btn_row.addWidget(add_bc)
        bc_btn_row.addWidget(rm_bc)
        bc_btn_row.addStretch()
        bc_v.addLayout(bc_btn_row)
        root.addWidget(bc_gb)

        # ── Output filenames ─────────────────────────────────────────────────
        fn_gb = QGroupBox("Output filenames")
        fn_form = QFormLayout(fn_gb)
        self._extbc_name_edit = QLineEdit()
        self._extbc_name_edit.setPlaceholderText("e.g. Coordinates.extbc")
        fn_form.addRow(".extbc filename:", self._extbc_name_edit)
        self._src_loc_name_edit = QLineEdit()
        self._src_loc_name_edit.setPlaceholderText("e.g. inflow_loc.txt")
        fn_form.addRow("src_loc filename:", self._src_loc_name_edit)
        root.addWidget(fn_gb)

        # ── Run ──────────────────────────────────────────────────────────────
        run_row = QHBoxLayout()
        self._run_btn = QPushButton("✔  Write BC files")
        self._run_btn.setStyleSheet(
            "font-weight:bold; padding:7px 20px; background:#2b6cb0; color:white; border-radius:4px;"
        )
        self._run_btn.clicked.connect(self._run_step)
        run_row.addWidget(self._run_btn)
        run_row.addStretch()
        root.addLayout(run_row)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100); self._progress.setValue(0)
        self._progress.setVisible(False)
        root.addWidget(self._progress)

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
            "padding:10px; background:#f0fff4; border:1px solid #9ae6b4; "
            "border-radius:4px; font-size:12px;"
        )
        self._report.setVisible(False)
        root.addWidget(self._report)

        root.addStretch()

    # ── Row helpers ────────────────────────────────────────────────────────────
    def _add_src_row(self, x, y):
        r = self._src_table.rowCount()
        self._src_table.insertRow(r)
        self._src_table.setItem(r, 0, QTableWidgetItem(f"{float(x):.3f}"))
        self._src_table.setItem(r, 1, QTableWidgetItem(f"{float(y):.3f}"))

    def _add_bc_row(self, bc_type, x1, y1, x2, y2, value):
        r = self._bc_table.rowCount()
        self._bc_table.insertRow(r)

        combo = QComboBox()
        for lbl, _ in _BC_TYPES:
            combo.addItem(lbl)
        combo.setCurrentIndex(self._type_index(bc_type))
        combo.currentIndexChanged.connect(self._on_bc_type_changed)
        self._bc_table.setCellWidget(r, 0, combo)

        for i, v in enumerate([x1, y1, x2, y2]):
            self._bc_table.setItem(r, i + 1, QTableWidgetItem(f"{float(v):.3f}"))

        val_item = QTableWidgetItem(str(value) if value is not None else "")
        self._bc_table.setItem(r, 5, val_item)
        self._on_bc_type_changed()

    def _type_index(self, bc_type_val):
        for i, (_, v) in enumerate(_BC_TYPES):
            if v == bc_type_val:
                return i
        return 0

    def _remove_selected(self, table):
        rows = sorted({i.row() for i in table.selectedIndexes()}, reverse=True)
        for r in rows:
            table.removeRow(r)

    def _on_bc_type_changed(self, *_):
        # When any type combobox flips to Type 1, allow the value cell to
        # accept a filename.  Provide a "Browse" shortcut via double-click.
        pass  # validation handled at run-time in _collect_bc_entries

    def _toggle_nhd_bc_value(self, idx):
        bc_type = _BC_TYPES[idx][1]
        self._nhd_slope_spin.setVisible(bc_type == 2)
        self._nhd_froude_lbl.setVisible(bc_type == 3)
        self._nhd_froude_spin.setVisible(bc_type == 3)
        self._nhd_stage_lbl.setVisible(bc_type == 1)
        self._nhd_stage_edit.setVisible(bc_type == 1)
        self._nhd_stage_btn.setVisible(bc_type == 1)

    def _browse_into(self, line_edit):
        f, _ = QFileDialog.getOpenFileName(
            self, "Select stage file", "",
            "Text/CSV (*.txt *.csv);;All files (*)"
        )
        if f:
            line_edit.setText(f)

    # ── NHD auto-populate ──────────────────────────────────────────────────────
    def _run_nhd(self):
        if not self._ctx_path or not self._ctx:
            self._log("Complete earlier steps first.")
            return
        self._error_lbl.setVisible(False)
        self._nhd_btn.setEnabled(False)
        self._progress.setValue(0)
        self._progress.setVisible(True)

        def _do_detect(ctx_path, ctx, segment_width, log_fn=print):
            return detect_main_river(
                ctx, downstream_segment_width=segment_width, log_fn=log_fn
            )

        self._detector = Worker(
            _do_detect,
            ctx_path=self._ctx_path,
            ctx=self._ctx,
            segment_width=self._seg_width_spin.value(),
        )
        self._detector.message.connect(self._on_message)
        self._detector.finished.connect(self._on_nhd_done)
        self._detector.error.connect(self._on_error)
        self._detector.start()

    def _on_nhd_done(self, result):
        self._nhd_btn.setEnabled(True)
        self._progress.setValue(100)
        up = result.get("upstream_pt")
        dn = result.get("downstream_segment")
        if up:
            self._add_src_row(up[0], up[1])
        if dn:
            idx = self._nhd_type_combo.currentIndex()
            bc_type = _BC_TYPES[idx][1]
            if bc_type == 2:
                val = f"{self._nhd_slope_spin.value():.6f}"
            elif bc_type == 3:
                val = f"{self._nhd_froude_spin.value():.3f}"
            elif bc_type == 1:
                val = self._nhd_stage_edit.text().strip()
                if not val:
                    QMessageBox.warning(
                        self, "Stage file missing",
                        "Type-1 needs a stage file path. Pick one before running NHD."
                    )
                    return
            else:
                val = ""
            self._add_bc_row(bc_type, dn[0], dn[1], dn[2], dn[3], val)

        # Stash reach id / river name for downstream steps
        if result.get("upstream_reach_id"):
            self._ctx["upstream_reach_id"] = result["upstream_reach_id"]
        if result.get("main_river_name"):
            self._ctx["main_river_name"]   = result["main_river_name"]
            self._ctx["main_feature_name"] = result["main_river_name"]
        if result.get("flowlines_path"):
            self._ctx["flowlines_path"]    = result["flowlines_path"]

        QMessageBox.information(
            self, "NHD populated",
            f"Main river: {result.get('main_river_name', '?')}\n"
            f"Upstream inflow row added.\n"
            f"Downstream BC row staged — review the value before writing."
        )

    # ── Collect & run ──────────────────────────────────────────────────────────
    def _collect_inflow_sources(self):
        rows = []
        for r in range(self._src_table.rowCount()):
            try:
                x = float(self._src_table.item(r, 0).text())
                y = float(self._src_table.item(r, 1).text())
            except (AttributeError, ValueError):
                raise ValueError(f"Inflow row {r + 1} has invalid coordinates.")
            rows.append((x, y))
        return rows

    def _collect_bc_entries(self):
        entries = []
        for r in range(self._bc_table.rowCount()):
            combo = self._bc_table.cellWidget(r, 0)
            bc_type = _BC_TYPES[combo.currentIndex()][1]
            try:
                x1 = float(self._bc_table.item(r, 1).text())
                y1 = float(self._bc_table.item(r, 2).text())
                x2 = float(self._bc_table.item(r, 3).text())
                y2 = float(self._bc_table.item(r, 4).text())
            except (AttributeError, ValueError):
                raise ValueError(f"BC row {r + 1} has invalid segment coordinates.")
            val_item = self._bc_table.item(r, 5)
            val_txt  = val_item.text().strip() if val_item else ""

            entry = {"bc_type": bc_type, "x1": x1, "y1": y1, "x2": x2, "y2": y2}
            if bc_type == 0:
                pass  # no value
            elif bc_type in (2, 3):
                try:
                    entry["value"] = float(val_txt)
                except ValueError:
                    raise ValueError(
                        f"BC row {r + 1} (type {bc_type}) needs a numeric value, got '{val_txt}'."
                    )
            else:  # type 1
                if not val_txt:
                    raise ValueError(f"BC row {r + 1} (type 1) needs a stage file path or filename.")
                # If it's an absolute path to an existing file, pass via stage_file_path.
                p = Path(val_txt)
                if p.exists():
                    entry["stage_file_path"] = str(p)
                else:
                    entry["value"] = val_txt
            entries.append(entry)
        return entries

    def _run_step(self):
        if not self._ctx_path or not self._ctx:
            self._log("Complete earlier steps first.")
            return
        try:
            inflow_sources = self._collect_inflow_sources()
            bc_entries     = self._collect_bc_entries()
        except ValueError as ex:
            self._error_lbl.setText(f"❌ <b>Input error:</b> {ex}")
            self._error_lbl.setVisible(True)
            return
        if not inflow_sources:
            self._error_lbl.setText("❌ <b>Add at least one inflow source.</b>")
            self._error_lbl.setVisible(True)
            return
        if not bc_entries:
            self._error_lbl.setText("❌ <b>Add at least one external BC row.</b>")
            self._error_lbl.setVisible(True)
            return

        extbc_name = self._extbc_name_edit.text().strip() or None
        src_name   = self._src_loc_name_edit.text().strip() or None

        self._error_lbl.setVisible(False)
        self._report.setVisible(False)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        set_running(self._run_btn)

        self._worker = Worker(
            prepare_triton_bc,
            ctx_path=self._ctx_path,
            ctx=self._ctx,
            inflow_sources=inflow_sources,
            bc_entries=bc_entries,
            extbc_filename=extbc_name,
            src_loc_filename=src_name,
            main_river_name=self._ctx.get("main_river_name"),
            upstream_reach_id=self._ctx.get("upstream_reach_id"),
        )
        self._worker.message.connect(self._on_message)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    # ── callbacks ──────────────────────────────────────────────────────────────
    def _on_message(self, msg):
        self._log(msg)
        ml = msg.lower()
        if "downloading nhd" in ml:
            self._progress.setValue(25)
        elif "flowlines saved" in ml:
            self._progress.setValue(55)
        elif "main river" in ml:
            self._progress.setValue(75)
        elif "source locations written" in ml:
            self._progress.setValue(85)
        elif "external bc file written" in ml:
            self._progress.setValue(100)

    def _on_done(self, ctx):
        self._ctx = ctx
        self._progress.setValue(100)
        set_ready(self._run_btn)
        self._show_report(ctx)
        self.step_completed.emit({"ctx_path": self._ctx_path, "ctx": ctx})

    def _on_error(self, msg):
        self._log(f"ERROR: {msg}")
        self._progress.setVisible(False)
        set_ready(self._run_btn)
        self._nhd_btn.setEnabled(True)
        self._error_lbl.setText(
            f"❌ <b>Error:</b> {msg.split(chr(10))[0]}<br>"
            "<small>(See log panel below for full details)</small>"
        )
        self._error_lbl.setVisible(True)

    def _show_report(self, ctx):
        extbc_path   = ctx.get("triton_extbc_path", "")
        src_loc_path = ctx.get("triton_src_loc_path", "")
        ns           = ctx.get("num_sources", 0)
        nb           = ctx.get("num_extbc", 0)
        river        = ctx.get("main_river_name", "")
        reach        = ctx.get("upstream_reach_id", "")
        html = (
            "<b>✅ BC files written.</b><br><br>"
            f"<b>Inflow sources (num_sources):</b> {ns}<br>"
            f"<b>External BCs (num_extbc):</b> {nb}<br>"
        )
        if river:
            html += f"<b>Main river:</b> {river}  (NWM reach ID: {reach or 'n/a'})<br>"
        html += (
            f"<b>src_loc file:</b> {src_loc_path}<br>"
            f"<b>.extbc file:</b> {extbc_path}<br>"
        )
        self._report.setText(html)
        self._report.setVisible(True)
