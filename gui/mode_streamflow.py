"""Streamflow Data standalone mode.

Pages:
  0 — Project
  1 — Source configuration + results

Sources: NWM Retrospective, NWM Forecast, USGS Gage.
No AOI required — user supplies feature IDs or gage numbers directly.
"""
from pathlib import Path
from typing import Optional, List

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QStackedWidget, QProgressBar, QGroupBox,
    QLineEdit, QDateTimeEdit, QComboBox, QCheckBox, QFileDialog,
    QFrame, QTableWidget, QTableWidgetItem, QAbstractItemView,
    QHeaderView, QMessageBox,
)
from PyQt6.QtCore import pyqtSignal, Qt, QDateTime
from PyQt6.QtGui import QFont

from gui.step_project import StepProjectWidget
from gui.run_button import set_running, set_ready
from gui.hydrograph_preview import HydrographPreviewCanvas
from gui.worker import Worker
from core.run_streamflow import run_streamflow_mode


class ModeStreamflowWidget(QWidget):
    mode_finished = pyqtSignal()
    nav_changed   = pyqtSignal(int, int)

    def __init__(self, log_fn, parent=None):
        super().__init__(parent)
        self._log = log_fn
        self._project_dir: Optional[str] = None
        self._worker: Optional[Worker] = None
        self._last_results: List[dict] = []
        self._setup_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setSpacing(0)
        outer.setContentsMargins(0, 0, 0, 0)

        self._stack = QStackedWidget()
        outer.addWidget(self._stack)

        self._stack.currentChanged.connect(self._update_nav)

        # Page 0: Project
        self._proj = StepProjectWidget(self._log, model="generic")
        self._proj.step_completed.connect(self._on_project_done)
        self._stack.addWidget(self._wrap(self._proj))        # 0

        # Page 1: Config + Results
        self._stack.addWidget(self._wrap(self._build_config_page()))  # 1

        self._stack.setCurrentIndex(0)
        self._update_nav(0)

    def _wrap(self, w: QWidget) -> QScrollArea:
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setWidget(w)
        return sa

    # ── Page 1: Config ────────────────────────────────────────────────────────

    def _build_config_page(self) -> QWidget:
        page = QWidget()
        # Raise the default font for all child labels / combos / edits
        page.setStyleSheet("QLabel { font-size:13px; } "
                           "QLineEdit { font-size:13px; } "
                           "QComboBox { font-size:13px; } "
                           "QDateTimeEdit { font-size:13px; } "
                           "QPushButton { font-size:12px; } "
                           "QCheckBox { font-size:13px; }")
        v = QVBoxLayout(page)
        v.setSpacing(12)
        v.setContentsMargins(14, 14, 14, 14)

        title_lbl = QLabel("Streamflow Data Download")
        title_lbl.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        title_lbl.setStyleSheet("color:#2d3748; font-size:14px;")
        v.addWidget(title_lbl)

        # ── Section 1: NWM Retrospective ─────────────────────────────────────
        self._retro_gb = QGroupBox()
        self._retro_gb.setStyleSheet(
            "QGroupBox { background:#f9fafb; border:1px solid #e2e8f0; "
            "border-radius:6px; padding-top:8px; }"
        )
        retro_outer = QVBoxLayout(self._retro_gb)
        retro_outer.setSpacing(6)

        retro_hdr = QHBoxLayout()
        self._retro_chk = QCheckBox("NWM Retrospective  (NOAA — USA only)")
        self._retro_chk.setChecked(False)
        self._retro_chk.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        self._retro_chk.toggled.connect(self._on_retro_toggled)
        retro_hdr.addWidget(self._retro_chk)
        retro_hdr.addStretch()
        retro_outer.addLayout(retro_hdr)

        self._retro_form = QWidget()
        rf = QVBoxLayout(self._retro_form)
        rf.setContentsMargins(10, 4, 4, 4)
        rf.setSpacing(6)

        ids_row = QHBoxLayout()
        ids_row.addWidget(QLabel("Feature ID(s):"))
        self._retro_ids = QLineEdit()
        self._retro_ids.setPlaceholderText("e.g. 12345678, 23456789  or  path/to/ids.csv")
        ids_row.addWidget(self._retro_ids, 1)
        retro_browse = QPushButton("Browse CSV…")
        retro_browse.setFixedWidth(100)
        retro_browse.clicked.connect(lambda: self._browse_csv(self._retro_ids))
        ids_row.addWidget(retro_browse)
        rf.addLayout(ids_row)

        retro_note = QLabel(
            "★ CSV: one COMID per line, no header required."
        )
        retro_note.setStyleSheet("color:#718096; font-size:11px;")
        rf.addWidget(retro_note)

        dt_row = QHBoxLayout()
        dt_row.addWidget(QLabel("Start date:"))
        self._retro_start = QDateTimeEdit()
        self._retro_start.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._retro_start.setCalendarPopup(True)
        self._retro_start.setDateTime(
            QDateTime.fromString("2020-11-01 00:00", "yyyy-MM-dd HH:mm")
        )
        dt_row.addWidget(self._retro_start)
        dt_row.addSpacing(12)
        dt_row.addWidget(QLabel("End date:"))
        self._retro_end = QDateTimeEdit()
        self._retro_end.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._retro_end.setCalendarPopup(True)
        self._retro_end.setDateTime(
            QDateTime.fromString("2020-12-01 00:00", "yyyy-MM-dd HH:mm")
        )
        dt_row.addWidget(self._retro_end)
        dt_row.addStretch()
        rf.addLayout(dt_row)

        cov_note = QLabel(
            "★ Available 1979-02-01 → 2020-12-31  |  "
            "15-min data resampled to chosen interval  |  USA only"
        )
        cov_note.setWordWrap(True)
        cov_note.setStyleSheet("color:#718096; font-size:11px;")
        rf.addWidget(cov_note)

        ivl_row = QHBoxLayout()
        ivl_row.addWidget(QLabel("Interval:"))
        self._retro_interval = QComboBox()
        self._retro_interval.addItems(["0.5h", "1h", "3h", "6h", "12h", "24h"])
        self._retro_interval.setCurrentText("1h")
        ivl_row.addWidget(self._retro_interval)
        ivl_row.addStretch()
        rf.addLayout(ivl_row)

        retro_outer.addWidget(self._retro_form)
        v.addWidget(self._retro_gb)

        # ── Section 2: NWM Forecast ───────────────────────────────────────────
        self._fore_gb = QGroupBox()
        self._fore_gb.setStyleSheet(
            "QGroupBox { background:#f9fafb; border:1px solid #e2e8f0; "
            "border-radius:6px; padding-top:8px; }"
        )
        fore_outer = QVBoxLayout(self._fore_gb)
        fore_outer.setSpacing(6)

        fore_hdr = QHBoxLayout()
        self._fore_chk = QCheckBox(
            "NWM Forecast  (NOAA — USA only, ~10-day horizon)"
        )
        self._fore_chk.setChecked(False)
        self._fore_chk.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        self._fore_chk.toggled.connect(self._on_fore_toggled)
        fore_hdr.addWidget(self._fore_chk)
        fore_hdr.addStretch()
        fore_outer.addLayout(fore_hdr)

        self._fore_form = QWidget()
        ff = QVBoxLayout(self._fore_form)
        ff.setContentsMargins(10, 4, 4, 4)
        ff.setSpacing(6)

        fore_ids_row = QHBoxLayout()
        fore_ids_row.addWidget(QLabel("Feature ID(s):"))
        self._fore_ids = QLineEdit()
        self._fore_ids.setPlaceholderText("e.g. 12345678, 23456789  or  path/to/ids.csv")
        fore_ids_row.addWidget(self._fore_ids, 1)
        fore_browse = QPushButton("Browse CSV…")
        fore_browse.setFixedWidth(100)
        fore_browse.clicked.connect(lambda: self._browse_csv(self._fore_ids))
        fore_ids_row.addWidget(fore_browse)
        ff.addLayout(fore_ids_row)

        fore_csv_note = QLabel("★ CSV: one COMID per line, no header required.")
        fore_csv_note.setStyleSheet("color:#718096; font-size:11px;")
        ff.addWidget(fore_csv_note)

        fore_cov_note = QLabel(
            "★ NWM operational forecast running since 2016  |  "
            "Rolling ~10-day window from current date  |  "
            "Updated every 6 hours  |  USA only  |  No historical archive"
        )
        fore_cov_note.setWordWrap(True)
        fore_cov_note.setStyleSheet("color:#718096; font-size:11px;")
        ff.addWidget(fore_cov_note)

        fore_dt_row = QHBoxLayout()
        fore_dt_row.addWidget(QLabel("Start date:"))
        self._fore_start = QDateTimeEdit()
        self._fore_start.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._fore_start.setCalendarPopup(True)
        self._fore_start.setDateTime(QDateTime.currentDateTime())
        fore_dt_row.addWidget(self._fore_start)
        fore_dt_row.addSpacing(12)
        fore_dt_row.addWidget(QLabel("End date:"))
        self._fore_end = QDateTimeEdit()
        self._fore_end.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._fore_end.setCalendarPopup(True)
        self._fore_end.setDateTime(QDateTime.currentDateTime().addDays(7))
        fore_dt_row.addWidget(self._fore_end)
        fore_dt_row.addStretch()
        ff.addLayout(fore_dt_row)

        fore_outer.addWidget(self._fore_form)
        v.addWidget(self._fore_gb)

        # ── Section 3: USGS ───────────────────────────────────────────────────
        self._usgs_gb = QGroupBox()
        self._usgs_gb.setStyleSheet(
            "QGroupBox { background:#f9fafb; border:1px solid #e2e8f0; "
            "border-radius:6px; padding-top:8px; }"
        )
        usgs_outer = QVBoxLayout(self._usgs_gb)
        usgs_outer.setSpacing(6)

        usgs_hdr = QHBoxLayout()
        self._usgs_chk = QCheckBox("USGS Stream Gage")
        self._usgs_chk.setChecked(False)
        self._usgs_chk.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        self._usgs_chk.toggled.connect(self._on_usgs_toggled)
        usgs_hdr.addWidget(self._usgs_chk)
        usgs_hdr.addStretch()
        usgs_outer.addLayout(usgs_hdr)

        self._usgs_form = QWidget()
        uf = QVBoxLayout(self._usgs_form)
        uf.setContentsMargins(10, 4, 4, 4)
        uf.setSpacing(6)

        usgs_ids_row = QHBoxLayout()
        usgs_ids_row.addWidget(QLabel("Gage number(s):"))
        self._usgs_ids = QLineEdit()
        self._usgs_ids.setPlaceholderText("e.g. 01234567, 02345678  or  path/to/gages.csv")
        usgs_ids_row.addWidget(self._usgs_ids, 1)
        usgs_browse = QPushButton("Browse CSV…")
        usgs_browse.setFixedWidth(100)
        usgs_browse.clicked.connect(lambda: self._browse_csv(self._usgs_ids))
        usgs_ids_row.addWidget(usgs_browse)
        uf.addLayout(usgs_ids_row)

        usgs_note = QLabel(
            "★ CSV: one gage ID per line, no header required."
        )
        usgs_note.setStyleSheet("color:#718096; font-size:11px;")
        uf.addWidget(usgs_note)

        usgs_dt_row = QHBoxLayout()
        usgs_dt_row.addWidget(QLabel("Start date:"))
        self._usgs_start = QDateTimeEdit()
        self._usgs_start.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._usgs_start.setCalendarPopup(True)
        self._usgs_start.setDateTime(QDateTime.currentDateTime().addDays(-30))
        usgs_dt_row.addWidget(self._usgs_start)
        usgs_dt_row.addSpacing(12)
        usgs_dt_row.addWidget(QLabel("End date:"))
        self._usgs_end = QDateTimeEdit()
        self._usgs_end.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._usgs_end.setCalendarPopup(True)
        self._usgs_end.setDateTime(QDateTime.currentDateTime())
        usgs_dt_row.addWidget(self._usgs_end)
        usgs_dt_row.addStretch()
        uf.addLayout(usgs_dt_row)

        usgs_ivl_row = QHBoxLayout()
        usgs_ivl_row.addWidget(QLabel("Interval:"))
        self._usgs_interval = QComboBox()
        self._usgs_interval.addItems(["15min", "30min", "1h", "3h", "6h", "12h", "24h"])
        self._usgs_interval.setCurrentText("1h")
        usgs_ivl_row.addWidget(self._usgs_interval)
        usgs_ivl_row.addStretch()
        uf.addLayout(usgs_ivl_row)

        usgs_outer.addWidget(self._usgs_form)
        v.addWidget(self._usgs_gb)

        # ── Run button ────────────────────────────────────────────────────────
        run_row = QHBoxLayout()
        self._run_btn = QPushButton("Download Streamflow Data")
        self._run_btn.setStyleSheet(
            "font-weight:bold; padding:8px 22px; background:#276749; "
            "color:white; border-radius:4px; font-size:13px;"
        )
        self._run_btn.clicked.connect(self._run)
        run_row.addWidget(self._run_btn)
        run_row.addStretch()
        v.addLayout(run_row)

        # Progress bar (hidden until run)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # indeterminate
        self._progress.setStyleSheet("QProgressBar { height: 18px; }")
        self._progress.setVisible(False)
        v.addWidget(self._progress)

        # Status label
        self._status_lbl = QLabel("")
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setStyleSheet("color:#2d3748; font-size:12px;")
        self._status_lbl.setVisible(False)
        v.addWidget(self._status_lbl)

        # Error label
        self._error_lbl = QLabel("")
        self._error_lbl.setWordWrap(True)
        self._error_lbl.setStyleSheet(
            "padding:10px; background:#fff5f5; border:1px solid #fc8181; "
            "border-radius:4px; color:#c53030;"
        )
        self._error_lbl.setVisible(False)
        v.addWidget(self._error_lbl)

        # ── Results section (hidden until a run completes) ────────────────────
        self._results_frame = QFrame()
        self._results_frame.setStyleSheet(
            "QFrame { background:#f9fafb; border:1px solid #e2e8f0; "
            "border-radius:6px; padding:8px; }"
        )
        self._results_frame.setVisible(False)
        rv = QVBoxLayout(self._results_frame)
        rv.setSpacing(8)

        self._results_summary_lbl = QLabel("")
        self._results_summary_lbl.setWordWrap(True)
        self._results_summary_lbl.setStyleSheet(
            "font-weight:bold; color:#276749; font-size:12px;"
        )
        rv.addWidget(self._results_summary_lbl)

        # Single-series: hydrograph canvas shown directly
        self._single_canvas_widget = QWidget()
        sc_v = QVBoxLayout(self._single_canvas_widget)
        sc_v.setContentsMargins(0, 0, 0, 0)
        self._single_canvas = HydrographPreviewCanvas(self, width=9, height=3.5)
        sc_v.addWidget(self._single_canvas)
        self._single_canvas_widget.setVisible(False)
        rv.addWidget(self._single_canvas_widget)

        # Multi-series: table + detail hydrograph
        self._table_widget = QWidget()
        tv = QVBoxLayout(self._table_widget)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.setSpacing(6)

        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(
            ["Source", "ID", "Status", "Date range", "Peak flow (m³/s)"]
        )
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setAlternatingRowColors(True)
        self._table.cellClicked.connect(self._on_table_row_clicked)
        self._table.setMinimumHeight(160)
        tv.addWidget(self._table)

        self._detail_canvas = HydrographPreviewCanvas(self, width=9, height=3.5)
        self._detail_canvas.setVisible(False)
        tv.addWidget(self._detail_canvas)

        self._table_widget.setVisible(False)
        rv.addWidget(self._table_widget)

        v.addWidget(self._results_frame)

        v.addStretch()

        # All sections start collapsed — user must check to expand
        self._retro_form.setVisible(False)
        self._fore_form.setVisible(False)
        self._usgs_form.setVisible(False)

        return page

    # ── checkbox toggles ──────────────────────────────────────────────────────

    def _on_retro_toggled(self, checked: bool):
        self._retro_form.setVisible(checked)

    def _on_fore_toggled(self, checked: bool):
        self._fore_form.setVisible(checked)

    def _on_usgs_toggled(self, checked: bool):
        self._usgs_form.setVisible(checked)

    # ── file browse ───────────────────────────────────────────────────────────

    def _browse_csv(self, line_edit: QLineEdit):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select CSV file", "", "CSV files (*.csv *.txt);;All files (*)"
        )
        if path:
            line_edit.setText(path)

    # ── interval parsing ──────────────────────────────────────────────────────

    @staticmethod
    def _parse_interval(text: str) -> float:
        """Convert combo-box text like '0.5h', '15min', '3h' to float hours."""
        text = text.strip().lower()
        if text.endswith("min"):
            return float(text[:-3]) / 60.0
        if text.endswith("h"):
            return float(text[:-1])
        return 1.0

    # ── validation ────────────────────────────────────────────────────────────

    def _validate(self) -> Optional[str]:
        """Return an error message string if validation fails, else None."""
        any_enabled = (
            self._retro_chk.isChecked()
            or self._fore_chk.isChecked()
            or self._usgs_chk.isChecked()
        )
        if not any_enabled:
            return "Enable at least one source."

        if self._retro_chk.isChecked():
            if not self._retro_ids.text().strip():
                return "NWM Retrospective: Feature ID(s) field is empty."
            if self._retro_start.dateTime() >= self._retro_end.dateTime():
                return "NWM Retrospective: End date must be after start date."

        if self._fore_chk.isChecked():
            if not self._fore_ids.text().strip():
                return "NWM Forecast: Feature ID(s) field is empty."
            if self._fore_start.dateTime() >= self._fore_end.dateTime():
                return "NWM Forecast: End date must be after start date."

        if self._usgs_chk.isChecked():
            if not self._usgs_ids.text().strip():
                return "USGS Gage: Gage number(s) field is empty."
            if self._usgs_start.dateTime() >= self._usgs_end.dateTime():
                return "USGS Gage: End date must be after start date."

        return None

    # ── run ───────────────────────────────────────────────────────────────────

    def _run(self):
        if not self._project_dir:
            QMessageBox.warning(self, "No project", "Please set up a project first.")
            return

        err = self._validate()
        if err:
            QMessageBox.warning(self, "Validation error", err)
            return

        # Disconnect and discard any previous worker to avoid stale signals
        if self._worker is not None:
            try:
                self._worker.finished.disconnect(self._on_done)
                self._worker.error.disconnect(self._on_error)
                self._worker.message.disconnect(self._log)
            except Exception:
                pass
            self._worker = None

        # Reset UI for the new run
        self._error_lbl.setVisible(False)
        self._results_frame.setVisible(False)
        self._single_canvas.clear()
        self._single_canvas_widget.setVisible(False)
        self._detail_canvas.clear()
        self._detail_canvas.setVisible(False)
        self._table.setRowCount(0)
        self._table_widget.setVisible(False)
        self._progress.setVisible(True)
        self._status_lbl.setText("Downloading streamflow data …")
        self._status_lbl.setVisible(True)
        set_running(self._run_btn)

        configs = []

        if self._retro_chk.isChecked():
            configs.append({
                "source": "nwm_retro",
                "ids": self._retro_ids.text().strip(),
                "start_dt": self._retro_start.dateTime().toPyDateTime(),
                "end_dt": self._retro_end.dateTime().toPyDateTime(),
                "interval_hours": self._parse_interval(
                    self._retro_interval.currentText()
                ),
            })

        if self._fore_chk.isChecked():
            configs.append({
                "source": "nwm_forecast",
                "ids": self._fore_ids.text().strip(),
                "start_dt": self._fore_start.dateTime().toPyDateTime(),
                "end_dt": self._fore_end.dateTime().toPyDateTime(),
                "interval_hours": 1.0,
            })

        if self._usgs_chk.isChecked():
            configs.append({
                "source": "usgs",
                "ids": self._usgs_ids.text().strip(),
                "start_dt": self._usgs_start.dateTime().toPyDateTime(),
                "end_dt": self._usgs_end.dateTime().toPyDateTime(),
                "interval_hours": self._parse_interval(
                    self._usgs_interval.currentText()
                ),
            })

        self._worker = Worker(
            run_streamflow_mode,
            project_dir=self._project_dir,
            configs=configs,
            log_fn=self._log,
        )
        self._worker.message.connect(self._log)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_done(self, summary: dict):
        set_ready(self._run_btn)
        self._progress.setVisible(False)
        results = summary.get("results", [])
        warnings = summary.get("warnings", [])
        self._last_results = results

        n_ok   = sum(1 for r in results if r.get("status") != "unavailable")
        n_fail = len(results) - n_ok

        # Status bar above results frame
        if n_fail and n_ok:
            self._status_lbl.setText(
                f"Download complete: {n_ok} saved, {n_fail} not available."
            )
        elif n_fail and not n_ok:
            self._status_lbl.setText(
                f"Download complete: no data returned ({n_fail} gage(s) not available)."
            )
        else:
            self._status_lbl.setText(f"Download complete: {n_ok} time series saved.")
        self._status_lbl.setStyleSheet("color:#276749; font-weight:bold; font-size:12px; padding:2px 0px;")

        # Summary label inside results frame
        if warnings:
            warn_lines = "<br>".join(f"⚠&nbsp;&nbsp;{w}" for w in warnings)
            self._results_summary_lbl.setText(
                f"Downloaded {n_ok} of {len(results)} time series<br><br>{warn_lines}"
            )
            self._results_summary_lbl.setStyleSheet(
                "padding:8px 10px; background:#fffbeb; border:1px solid #f6ad55; "
                "border-radius:4px; color:#744210; font-size:12px;"
            )
        elif n_fail:
            self._results_summary_lbl.setText(
                f"Downloaded {n_ok} of {len(results)} time series  —  "
                f"{n_fail} gage(s) had no data for the requested period."
            )
            self._results_summary_lbl.setStyleSheet(
                "font-weight:bold; color:#744210; font-size:12px;"
            )
        else:
            self._results_summary_lbl.setText(f"✓  Downloaded {n_ok} time series")
            self._results_summary_lbl.setStyleSheet(
                "font-weight:bold; color:#276749; font-size:12px;"
            )

        self._build_results(results)
        self._results_frame.setVisible(True)

    def _on_error(self, msg: str):
        set_ready(self._run_btn)
        self._progress.setVisible(False)
        self._status_lbl.setVisible(False)
        self._log(f"ERROR: {msg}")
        self._error_lbl.setText(
            f"<b>Error:</b> {msg.splitlines()[0]}<br>"
            "<small>(See log panel for full details)</small>"
        )
        self._error_lbl.setVisible(True)

    # ── results rendering ─────────────────────────────────────────────────────

    def _build_results(self, results: list):
        self._single_canvas.clear()
        self._single_canvas_widget.setVisible(False)
        self._detail_canvas.clear()
        self._detail_canvas.setVisible(False)
        self._table_widget.setVisible(False)

        if not results:
            return

        # Single successful result — show hydrograph directly
        if len(results) == 1 and results[0].get("status") != "unavailable":
            entry = results[0]
            csv_path = entry.get("csv_path", "")
            source = entry.get("source", "")
            fid = entry.get("id", "")
            if csv_path and Path(csv_path).exists():
                self._single_canvas.show_hydrograph(
                    csv_path, title=f"{source} — {fid}"
                )
                self._single_canvas_widget.setVisible(True)
            return

        # Multiple results (or single unavailable) — always show table
        import pandas as pd
        from PyQt6.QtGui import QColor

        self._table.setRowCount(0)
        for row_idx, entry in enumerate(results):
            self._table.insertRow(row_idx)
            source    = entry.get("source", "")
            fid       = entry.get("id", "")
            csv_path  = entry.get("csv_path") or ""
            peak      = entry.get("peak_flow_cms")
            unavail   = entry.get("status") == "unavailable"

            # Status cell
            if unavail:
                status_text = "✗  No data for period"
            elif entry.get("warnings"):
                status_text = "⚠  Partial coverage"
            else:
                status_text = "✓  Downloaded"

            # Date range from CSV
            date_range = ""
            if csv_path and Path(csv_path).exists():
                try:
                    df = pd.read_csv(csv_path, usecols=["datetime"])
                    dates = pd.to_datetime(df["datetime"], errors="coerce").dropna()
                    if not dates.empty:
                        date_range = (
                            f"{dates.min().strftime('%Y-%m-%d')}  →  "
                            f"{dates.max().strftime('%Y-%m-%d')}"
                        )
                except Exception:
                    pass

            peak_str = f"{peak:.2f}" if peak is not None else "—"

            row_data = [source, fid, status_text, date_range, peak_str]
            for col_idx, text in enumerate(row_data):
                item = QTableWidgetItem(str(text))
                item.setData(Qt.ItemDataRole.UserRole, entry)
                if unavail:
                    item.setForeground(QColor("#c53030"))
                    item.setBackground(QColor("#fff5f5"))
                elif entry.get("warnings"):
                    item.setForeground(QColor("#744210"))
                    item.setBackground(QColor("#fffbeb"))
                self._table.setItem(row_idx, col_idx, item)

        self._table_widget.setVisible(True)

    def _on_table_row_clicked(self, row: int, _col: int):
        item = self._table.item(row, 0)
        if item is None:
            return
        entry = item.data(Qt.ItemDataRole.UserRole)
        if not entry:
            return

        if entry.get("status") == "unavailable":
            self._detail_canvas.setVisible(False)
            self._detail_canvas.clear()
            return

        csv_path = entry.get("csv_path", "")
        source   = entry.get("source", "")
        fid      = entry.get("id", "")
        if csv_path and Path(csv_path).exists():
            self._detail_canvas.show_hydrograph(
                csv_path, title=f"{source} — {fid}"
            )
            self._detail_canvas.setVisible(True)
        else:
            self._detail_canvas.setVisible(False)
            self._detail_canvas.clear()

    # ── navigation ────────────────────────────────────────────────────────────

    def _goto_prev(self):
        cur = self._stack.currentIndex()
        if cur > 0:
            self._stack.setCurrentIndex(cur - 1)

    def _goto_next(self):
        cur = self._stack.currentIndex()
        if cur < self._stack.count() - 1:
            self._stack.setCurrentIndex(cur + 1)

    def _update_nav(self, idx: int):
        self.nav_changed.emit(idx, self._stack.count())

    def go_prev(self):
        self._goto_prev()

    def go_next(self):
        self._goto_next()

    # ── slots ─────────────────────────────────────────────────────────────────

    def _on_project_done(self, data: dict):
        self._project_dir = data.get("ctx", {}).get("project_dir")
        self._stack.setCurrentIndex(1)

    # ── reset ─────────────────────────────────────────────────────────────────

    def reset(self):
        self._project_dir = None
        self._last_results = []
        if hasattr(self._proj, "reset"):
            self._proj.reset()
        self._retro_ids.clear()
        self._fore_ids.clear()
        self._usgs_ids.clear()
        self._retro_chk.setChecked(False)
        self._fore_chk.setChecked(False)
        self._usgs_chk.setChecked(False)
        self._results_frame.setVisible(False)
        self._single_canvas_widget.setVisible(False)
        self._table_widget.setVisible(False)
        self._detail_canvas.setVisible(False)
        self._progress.setVisible(False)
        self._status_lbl.setVisible(False)
        self._error_lbl.setVisible(False)
        self._table.setRowCount(0)
        self._single_canvas.clear()
        self._detail_canvas.clear()
        try:
            set_ready(self._run_btn)
        except Exception:
            pass
        self._stack.setCurrentIndex(0)
