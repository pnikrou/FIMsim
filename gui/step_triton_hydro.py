"""Step 5 of TRITON workflow — Hydrograph file (.hyg).

Writes one multi-column .hyg with one discharge column per inflow source.
When ``num_sources > 1`` the user picks a separate data source per inflow
(NWM reach / CSV / existing / constant); when ``num_sources == 1`` the UI
shows the classic single-source form.
"""
from pathlib import Path
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFileDialog, QGroupBox, QFormLayout, QComboBox, QProgressBar,
    QDateTimeEdit, QDoubleSpinBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QFrame,
)
from PyQt6.QtCore import pyqtSignal, QDateTime, Qt

from core.triton_hydro import prepare_triton_hydro, finalize_hyg
from gui.worker import Worker
from gui.run_button import set_running, set_ready


_SRC_LABELS = [
    "Constant discharge",
    "NWM retrospective",
    "CSV/XLSX file",
    "Existing .hyg file",
]
_SRC_KEYS = ["constant", "nwm", "csv", "existing"]


def _sep():
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("color:#e2e8f0;")
    return line


class StepTritonHydroWidget(QWidget):
    step_completed = pyqtSignal(dict)

    def __init__(self, log_fn, parent=None):
        super().__init__(parent)
        self._log    = log_fn
        self._worker = None
        self._ctx_path = None
        self._ctx      = None
        self._finalize_pending = False
        self._setup_ui()

    # ── context ────────────────────────────────────────────────────────────────
    def set_context(self, ctx_path, ctx):
        self._ctx_path = ctx_path
        self._ctx      = ctx
        if not ctx:
            return

        # Event window
        for key, dt_widget in (("event_start", self._start_date),
                               ("event_end",   self._end_date)):
            s = ctx.get(key)
            if s:
                try:
                    dt = datetime.strptime(s, "%Y-%m-%d %H:%M")
                    dt_widget.setDateTime(
                        QDateTime(dt.year, dt.month, dt.day, dt.hour, dt.minute)
                    )
                except ValueError:
                    pass

        # Filename default
        if not self._hyg_name_edit.text().strip():
            self._hyg_name_edit.setText(f"{ctx.get('project_name', 'triton')}.hyg")

        # Single vs multi-source
        num_sources = int(ctx.get("num_sources", 1))
        self._rebuild_sources_table(num_sources, ctx)

    # ── UI ─────────────────────────────────────────────────────────────────────
    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)

        info = QLabel(
            "<b>Builds one .hyg with one discharge column per inflow source.</b> "
            "The number of columns is driven by <code>num_sources</code>, which the BC step sets. "
            "Pick a data source per row; the step iterates and writes a single combined file."
        )
        info.setWordWrap(True)
        info.setStyleSheet(
            "padding:8px; background:#fffbeb; border:1px solid #f6e05e; border-radius:4px;"
        )
        root.addWidget(info)

        # ── Event window & interval ──────────────────────────────────────────
        win_gb = QGroupBox("Event window & interval")
        win_form = QFormLayout(win_gb)

        self._start_date = QDateTimeEdit()
        self._start_date.setDisplayFormat("yyyy-MM-dd  HH:mm")
        self._start_date.setCalendarPopup(True)
        self._start_date.setDateTime(QDateTime(2010, 1, 1, 0, 0))
        win_form.addRow("Event start:", self._start_date)

        self._end_date = QDateTimeEdit()
        self._end_date.setDisplayFormat("yyyy-MM-dd  HH:mm")
        self._end_date.setCalendarPopup(True)
        self._end_date.setDateTime(QDateTime(2010, 1, 8, 0, 0))
        win_form.addRow("Event end:", self._end_date)

        self._interval_combo = QComboBox()
        self._interval_values = [0.5, 1.0, 3.0, 6.0, 12.0, 24.0]
        for v in self._interval_values:
            self._interval_combo.addItem(f"{v:g} hours" if v != 1.0 else "1 hour")
        self._interval_combo.setCurrentIndex(1)
        win_form.addRow("Time interval:", self._interval_combo)

        root.addWidget(win_gb)

        # ── Per-source strategy table ────────────────────────────────────────
        src_gb = QGroupBox("Discharge source per inflow")
        src_v  = QVBoxLayout(src_gb)

        self._src_summary = QLabel("")
        self._src_summary.setStyleSheet("color:#555; font-size:11px;")
        src_v.addWidget(self._src_summary)

        self._src_table = QTableWidget(0, 3)
        self._src_table.setHorizontalHeaderLabels(
            ["Source idx", "Data source", "NWM reach / file path / const Q (cms)"]
        )
        h = self._src_table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._src_table.setMinimumHeight(120)
        src_v.addWidget(self._src_table)

        hint = QLabel(
            "<small>• <b>Constant</b> — put a numeric m³/s value in the last column.<br>"
            "• <b>NWM retrospective</b> — reach feature_id; default taken from "
            "<code>upstream_reach_id</code> if set by the BC step.<br>"
            "• <b>CSV/XLSX</b> — path to a file with columns <code>time_hours</code>, "
            "<code>discharge_cms</code>.<br>"
            "• <b>Existing .hyg</b> — path to a TRITON .hyg file; resampled onto the "
            "event window at the chosen interval.</small>"
        )
        hint.setWordWrap(True)
        src_v.addWidget(hint)
        root.addWidget(src_gb)

        # ── Filename ─────────────────────────────────────────────────────────
        fn_gb = QGroupBox("Output filename")
        fn_form = QFormLayout(fn_gb)
        self._hyg_name_edit = QLineEdit()
        self._hyg_name_edit.setPlaceholderText("e.g. Neuse_strmflow.hyg")
        fn_form.addRow(".hyg filename:", self._hyg_name_edit)
        root.addWidget(fn_gb)

        # ── Run button ────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._run_btn = QPushButton("Write hydrograph file")
        self._run_btn.setStyleSheet(
            "font-weight:bold; padding:7px 20px; background:#2b6cb0; color:white; border-radius:4px;"
        )
        self._run_btn.clicked.connect(self._run_all_sources)
        btn_row.addWidget(self._run_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

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

    # ── Per-source table builder ─────────────────────────────────────────────
    def _rebuild_sources_table(self, num_sources, ctx):
        self._src_table.setRowCount(0)
        default_reach = ctx.get("upstream_reach_id") or ""
        points = ctx.get("inflow_source_points", [])
        for i in range(max(num_sources, 1)):
            self._src_table.insertRow(i)
            self._src_table.setItem(i, 0, QTableWidgetItem(str(i)))
            combo = QComboBox()
            for lbl in _SRC_LABELS:
                combo.addItem(lbl)
            combo.setCurrentIndex(1 if default_reach else 0)
            self._src_table.setCellWidget(i, 1, combo)
            val_edit = QTableWidgetItem(default_reach if default_reach else "100.0")
            self._src_table.setItem(i, 2, val_edit)

        # Show a helpful summary
        if num_sources > 1:
            self._src_summary.setText(
                f"<b>num_sources = {num_sources}</b> — provide one data source per inflow row."
            )
        else:
            self._src_summary.setText(
                "<b>num_sources = 1</b> — single inflow; the file will have one discharge column."
            )

    # ── Run (iterate every source then finalize) ─────────────────────────────
    def _run_all_sources(self):
        if not self._ctx_path or not self._ctx:
            self._log("Complete earlier steps first.")
            return

        try:
            per_source = self._collect_sources()
        except ValueError as ex:
            self._error_lbl.setText(f"<b>Input error:</b> {ex}")
            self._error_lbl.setVisible(True)
            return

        self._error_lbl.setVisible(False)
        self._report.setVisible(False)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        set_running(self._run_btn)

        start_dt = self._start_date.dateTime().toPyDateTime()
        end_dt   = self._end_date.dateTime().toPyDateTime()
        interval = self._interval_values[self._interval_combo.currentIndex()]
        hyg_name = self._hyg_name_edit.text().strip() or None

        # Drive the loop from a chained worker queue — each source finishes
        # then fires the next.  Keeps the GUI responsive.
        self._queue = list(enumerate(per_source))
        self._start_dt = start_dt
        self._end_dt   = end_dt
        self._interval = interval
        self._hyg_name = hyg_name
        self._num_sources = len(per_source)

        # Reset the pending buffer before the loop starts
        self._ctx.pop("_hyg_pending", None)

        self._run_next_source()

    def _collect_sources(self):
        rows = []
        for r in range(self._src_table.rowCount()):
            combo = self._src_table.cellWidget(r, 1)
            key = _SRC_KEYS[combo.currentIndex()]
            val = self._src_table.item(r, 2).text().strip() if self._src_table.item(r, 2) else ""

            spec = {"hydro_source": key}
            if key == "constant":
                try:
                    spec["constant_discharge_cms"] = float(val)
                except ValueError:
                    raise ValueError(f"Source row {r}: constant Q must be numeric, got '{val}'.")
            elif key == "nwm":
                if not val:
                    raise ValueError(f"Source row {r}: provide an NWM reach feature_id.")
                spec["nwm_reach_id"] = val
            elif key == "csv":
                if not val or not Path(val).exists():
                    raise ValueError(f"Source row {r}: CSV path not found — '{val}'.")
                spec["user_csv_path"] = val
            elif key == "existing":
                if not val or not Path(val).exists():
                    raise ValueError(f"Source row {r}: .hyg path not found — '{val}'.")
                spec["existing_hydro_path"] = val
            rows.append(spec)
        if not rows:
            raise ValueError("No source rows in the table.")
        return rows

    def _run_next_source(self):
        if not self._queue:
            return
        idx, spec = self._queue.pop(0)
        kw = dict(
            ctx_path=self._ctx_path,
            ctx=self._ctx,
            start_dt=self._start_dt,
            end_dt=self._end_dt,
            interval_hours=self._interval,
            source_index=idx,
            hyg_filename=self._hyg_name,
        )
        kw.update(spec)
        self._worker = Worker(prepare_triton_hydro, **kw)
        self._worker.message.connect(self._on_message)
        self._worker.finished.connect(self._on_source_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    # ── callbacks ──────────────────────────────────────────────────────────────
    def _on_message(self, msg):
        self._log(msg)
        ml = msg.lower()
        if "opening nwm" in ml or "zarr" in ml:
            self._progress.setValue(self._progress.value() + 5)
        elif ".hyg written" in ml:
            self._progress.setValue(95)
        elif "helper csv saved" in ml:
            self._progress.setValue(100)

    def _on_source_done(self, ctx):
        self._ctx = ctx
        # Advance progress proportional to sources done
        done = self._num_sources - len(self._queue)
        self._progress.setValue(int(90 * done / max(1, self._num_sources)))
        if self._queue:
            self._run_next_source()
        else:
            # If the last call already wrote the file (num_sources matched the
            # buffer), we're done. Otherwise force a finalize.
            if not self._ctx.get("triton_hyg_written"):
                try:
                    self._ctx = finalize_hyg(self._ctx_path, self._ctx)
                except Exception as ex:
                    self._on_error(str(ex))
                    return
            self._progress.setValue(100)
            set_ready(self._run_btn)
            self._show_report(self._ctx)
            self.step_completed.emit({"ctx_path": self._ctx_path, "ctx": self._ctx})

    def _on_error(self, msg):
        self._log(f"ERROR: {msg}")
        self._progress.setVisible(False)
        set_ready(self._run_btn)
        self._error_lbl.setText(
            f"<b>Error:</b> {msg.split(chr(10))[0]}<br>"
            "<small>(See log panel below for full details)</small>"
        )
        self._error_lbl.setVisible(True)

    def _show_report(self, ctx):
        hyg_path     = ctx.get("triton_hyg_path", "")
        event_start  = ctx.get("event_start", "")
        event_end    = ctx.get("event_end", "")
        interval     = ctx.get("series_interval_hours", "")
        ns           = ctx.get("num_sources", 1)
        per_idx      = ctx.get("triton_hydro_source_per_idx", {})
        helper_csv   = ctx.get("triton_hydro_helper_csv", "")

        html = (
            "<b>.hyg written.</b><br><br>"
            f"<b>Event window:</b> {event_start} → {event_end}<br>"
            f"<b>Interval:</b> {interval} h<br>"
            f"<b>num_sources:</b> {ns}<br>"
            f"<b>File:</b> {hyg_path}<br>"
        )
        if per_idx:
            rows = ", ".join(f"{k}:{v}" for k, v in sorted(per_idx.items()))
            html += f"<b>Source providers:</b> {rows}<br>"
        if helper_csv:
            html += f"<b>Helper CSV:</b> {helper_csv}<br>"
        self._report.setText(html)
        self._report.setVisible(True)
