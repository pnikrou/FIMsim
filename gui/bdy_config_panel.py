"""Reusable BDY (hydrograph time-series) configuration panel.

Self-contained form widget for one AOI's BDY settings:
  * Data source — NWM retrospective / CSV file / existing BDY file.
  * Conditional file picker (CSV or existing BDY).
  * Event window (start + end datetimes) — hidden in CSV mode because
    timestamps come from the file.
  * Time interval combo (0.5 / 1 / 3 / 6 / 12 / 24 hours).

Embedded directly in step_bdy for a single-AOI workflow, or one panel per
AOI inside an AOIBDYCard for the multi-AOI accordion.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QPushButton, QFileDialog, QComboBox, QDateTimeEdit, QDoubleSpinBox,
)
from PyQt6.QtCore import pyqtSignal, QDateTime, QDate, QTime


class BDYConfigPanel(QWidget):
    config_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        form = QFormLayout()
        outer.addLayout(form)

        # ── Data source ──────────────────────────────────────────────────
        # Index 0 is a non-runnable placeholder so the user has to make an
        # explicit choice before the form below reveals itself.  The
        # "I already have a .bdy file" option was removed — if the user
        # already has a BDY they can just skip this step from the bottom
        # navigation bar.
        self._src_combo = QComboBox()
        self._src_combo.addItems([
            "— pick a data source —",
            "Download from NWM (NOAA — USA only)",
            "I have a discharge CSV/XLSX file",
        ])
        form.addRow("Data source:", self._src_combo)

        # ── File picker (CSV or existing BDY) ────────────────────────────
        self._file_edit = QLineEdit()
        self._file_edit.setPlaceholderText("Browse for file…")
        self._browse_btn = QPushButton("Browse…")
        self._browse_btn.setFixedWidth(80)
        self._browse_btn.clicked.connect(self._browse_file)
        file_row = QHBoxLayout()
        file_row.addWidget(self._file_edit)
        file_row.addWidget(self._browse_btn)
        self._file_lbl = QLabel("File:")
        form.addRow(self._file_lbl, file_row)

        self._csv_note = QLabel(
            "<small>CSV format: columns <b>time_hours</b> and "
            "<b>discharge_cms</b>.<br>"
            "If <code>time_hours</code> contains datetime strings (e.g. "
            "<code>2018-08-26 00:00:00</code>), the event start/end will be "
            "read from the file automatically.</small>"
        )
        self._csv_note.setWordWrap(True)
        form.addRow(self._csv_note)

        # ── Event window ────────────────────────────────────────────────
        # Defaults: today 00:00 → today + 7 days 00:00.  NWM retrospective
        # only goes through 2020-12-31, so the user will need to roll back
        # for that source — the dates can be edited freely.
        today = QDateTime(QDate.currentDate(), QTime(0, 0))
        end_default = today.addDays(7)

        self._start_lbl = QLabel("Event start:")
        self._start_date = QDateTimeEdit()
        self._start_date.setDisplayFormat("yyyy-MM-dd  HH:mm")
        self._start_date.setCalendarPopup(True)
        self._start_date.setDateTime(today)
        form.addRow(self._start_lbl, self._start_date)

        self._end_lbl = QLabel("Event end:")
        self._end_date = QDateTimeEdit()
        self._end_date.setDisplayFormat("yyyy-MM-dd  HH:mm")
        self._end_date.setCalendarPopup(True)
        self._end_date.setDateTime(end_default)
        form.addRow(self._end_lbl, self._end_date)

        self._nwm_note = QLabel(
            "★  NWM retrospective covers 1979-02-01 to 2020-12-31. "
            "After that date the app uses the NWM operational forecast "
            "(medium-range, ~10-day horizon)."
        )
        self._nwm_note.setWordWrap(False)
        form.addRow(self._nwm_note)

        # ── Interval — user-typed spin so any value is allowed ──────────
        # Common values: 0.5, 1, 2, 3, 6, 12, 24 hours.  Default = 1 hr.
        self._interval_spin = QDoubleSpinBox()
        self._interval_spin.setRange(0.05, 168.0)
        self._interval_spin.setDecimals(2)
        self._interval_spin.setValue(1.0)
        self._interval_spin.setSingleStep(0.5)
        self._interval_spin.setSuffix(" hours")
        self._interval_lbl_widget = QLabel("Time interval:")
        form.addRow(self._interval_lbl_widget, self._interval_spin)

        # ── wire signals + initial visibility ───────────────────────────
        self._src_combo.currentIndexChanged.connect(self._on_source_changed)
        self._file_edit.textChanged.connect(self._emit_changed)
        self._start_date.dateTimeChanged.connect(self._emit_changed)
        self._end_date.dateTimeChanged.connect(self._emit_changed)
        self._interval_spin.valueChanged.connect(self._emit_changed)
        self._on_source_changed()

    # ── visibility ────────────────────────────────────────────────────────────

    def _on_source_changed(self, *_):
        # Combo indices after the BDY-file option was removed:
        #   0 = placeholder (nothing picked),  1 = NWM,  2 = CSV.
        idx = self._src_combo.currentIndex()
        any_picked = (idx >= 1)
        is_nwm = (idx == 1)
        is_csv = (idx == 2)
        need_file  = is_csv
        need_dates = any_picked and not is_csv  # CSV reads dates from the file

        self._file_lbl.setVisible(need_file)
        self._file_edit.setVisible(need_file)
        self._browse_btn.setVisible(need_file)
        self._csv_note.setVisible(is_csv)
        self._start_lbl.setVisible(need_dates)
        self._start_date.setVisible(need_dates)
        self._end_lbl.setVisible(need_dates)
        self._end_date.setVisible(need_dates)
        self._nwm_note.setVisible(is_nwm)
        self._interval_spin.setVisible(any_picked)
        if hasattr(self, "_interval_lbl_widget"):
            self._interval_lbl_widget.setVisible(any_picked)
        self._file_edit.clear()
        self._emit_changed()

    def _browse_file(self):
        idx = self._src_combo.currentIndex()
        if idx == 1:
            f, _ = QFileDialog.getOpenFileName(
                self, "Select discharge file", "",
                "CSV/Excel (*.csv *.xlsx *.xls *.txt)",
            )
        else:
            f, _ = QFileDialog.getOpenFileName(
                self, "Select BDY file", "",
                "BDY files (*.bdy);;All files (*)",
            )
        if f:
            self._file_edit.setText(f)

    def _emit_changed(self, *_):
        self.config_changed.emit()

    # ── public API ────────────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        # Index 0 = placeholder, user hasn't picked a source yet.
        idx = self._src_combo.currentIndex()
        if idx == 0:
            return False
        if idx == 1:                # NWM — needs nothing extra
            return True
        # CSV — must have a path
        return bool(self._file_edit.text().strip())

    def source_label(self) -> str:
        idx = self._src_combo.currentIndex()
        return ["—", "NWM", "CSV"][idx]

    def get_config(self) -> dict:
        idx = self._src_combo.currentIndex()
        bdy_source = ["", "nwm", "csv"][idx]
        return {
            "bdy_source":     bdy_source,
            "file_path":      self._file_edit.text().strip(),
            "start_dt":       self._start_date.dateTime().toPyDateTime(),
            "end_dt":         self._end_date.dateTime().toPyDateTime(),
            "interval_hours": float(self._interval_spin.value()),
        }

    def set_config(self, cfg: dict):
        if not cfg:
            return
        src_idx = {"nwm": 1, "csv": 2}.get(cfg.get("bdy_source", ""), 0)
        self._src_combo.setCurrentIndex(src_idx)
        self._file_edit.setText(cfg.get("file_path", ""))
        try:
            from datetime import datetime
            sd = cfg.get("start_dt")
            if isinstance(sd, datetime):
                self._start_date.setDateTime(QDateTime(
                    sd.year, sd.month, sd.day, sd.hour, sd.minute,
                ))
            ed = cfg.get("end_dt")
            if isinstance(ed, datetime):
                self._end_date.setDateTime(QDateTime(
                    ed.year, ed.month, ed.day, ed.hour, ed.minute,
                ))
        except Exception:
            pass
        try:
            self._interval_spin.setValue(float(cfg.get("interval_hours", 1.0)))
        except Exception:
            pass
