"""Reusable BDY (hydrograph time-series) configuration panel.

Sources:
  1. NWM Retrospective  — 1979-02-01 → 2020-12-31 (NOAA v2.1)
  2. NWM Forecast       — rolling ~10-day window from today
  3. USGS Stream Gage   — any gage with instantaneous (15-min) data
  4. CSV / XLSX file    — user-supplied discharge table

Used in step_bdy directly (single AOI) and inside AOIBDYCard (multi-AOI).
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QPushButton, QFileDialog, QComboBox, QDateTimeEdit, QDoubleSpinBox,
)
from PyQt6.QtCore import pyqtSignal, QDateTime, QDate, QTime


class BDYConfigPanel(QWidget):
    config_changed = pyqtSignal()

    # Combo index → internal key
    _SRC_KEYS = ["", "nwm_retro", "nwm_forecast", "usgs", "csv"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        form = QFormLayout()
        form.setVerticalSpacing(6)
        outer.addLayout(form)

        # ── Source selector ───────────────────────────────────────────────
        self._src_combo = QComboBox()
        self._src_combo.addItems([
            "— pick a data source —",
            "NWM Retrospective  (NOAA — USA only)",
            "NWM Forecast  (NOAA — USA only)",
            "USGS Stream Gage",
            "I have a discharge CSV / XLSX file",
        ])
        form.addRow("Data source:", self._src_combo)

        # ── USGS gage ID ──────────────────────────────────────────────────
        self._gage_lbl = QLabel("Gage number:")
        self._gage_edit = QLineEdit()
        self._gage_edit.setPlaceholderText("e.g.  05064500")
        form.addRow(self._gage_lbl, self._gage_edit)

        usgs_avail = QLabel(
            "★ Data available at <a href='https://waterdata.usgs.gov/nwis/rt'>"
            "waterdata.usgs.gov</a>  |  15-min readings resampled to chosen interval"
        )
        usgs_avail.setOpenExternalLinks(True)
        usgs_avail.setWordWrap(True)
        usgs_avail.setStyleSheet("color:#718096; font-size:11px;")
        self._usgs_note = usgs_avail
        form.addRow(self._usgs_note)

        # ── File picker (CSV / existing BDY) ─────────────────────────────
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
            "★ Columns: <code>time_hours</code> (numeric hours or datetime) "
            "and <code>discharge_cms</code> (m³/s)"
        )
        self._csv_note.setWordWrap(True)
        self._csv_note.setStyleSheet("color:#718096; font-size:11px;")
        form.addRow(self._csv_note)

        # ── Event window ──────────────────────────────────────────────────
        today   = QDateTime(QDate.currentDate(), QTime(0, 0))
        retro_default_end = QDateTime.fromString("2020-12-01 00:00",
                                                  "yyyy-MM-dd HH:mm")

        self._start_lbl = QLabel("Event start:")
        self._start_date = QDateTimeEdit()
        self._start_date.setDisplayFormat("yyyy-MM-dd  HH:mm")
        self._start_date.setCalendarPopup(True)
        self._start_date.setDateTime(
            QDateTime.fromString("2020-11-01 00:00", "yyyy-MM-dd HH:mm")
        )
        form.addRow(self._start_lbl, self._start_date)

        self._end_lbl = QLabel("Event end:")
        self._end_date = QDateTimeEdit()
        self._end_date.setDisplayFormat("yyyy-MM-dd  HH:mm")
        self._end_date.setCalendarPopup(True)
        self._end_date.setDateTime(retro_default_end)
        form.addRow(self._end_lbl, self._end_date)

        # ── Per-source availability notes ─────────────────────────────────
        self._retro_note = QLabel(
            "★ Available 1979-02-01 → 2020-12-31  |  "
            "15-min data resampled to chosen interval  |  USA only"
        )
        self._retro_note.setWordWrap(True)
        self._retro_note.setStyleSheet("color:#718096; font-size:11px;")
        form.addRow(self._retro_note)

        self._forecast_note = QLabel(
            "★ NWM operational forecast running since 2016  |  "
            "Rolling ~10-day window from current date  |  "
            "Updated every 6 hours  |  USA only  |  No historical archive"
        )
        self._forecast_note.setWordWrap(True)
        self._forecast_note.setStyleSheet("color:#718096; font-size:11px;")
        form.addRow(self._forecast_note)

        # ── Interval ──────────────────────────────────────────────────────
        self._interval_spin = QDoubleSpinBox()
        self._interval_spin.setRange(0.05, 168.0)
        self._interval_spin.setDecimals(2)
        self._interval_spin.setValue(1.0)
        self._interval_spin.setSingleStep(0.5)
        self._interval_spin.setSuffix(" hours")
        self._interval_lbl_widget = QLabel("Time interval:")
        form.addRow(self._interval_lbl_widget, self._interval_spin)

        # ── wire signals + initial visibility ─────────────────────────────
        self._src_combo.currentIndexChanged.connect(self._on_source_changed)
        self._gage_edit.textChanged.connect(self._emit_changed)
        self._file_edit.textChanged.connect(self._emit_changed)
        self._start_date.dateTimeChanged.connect(self._emit_changed)
        self._end_date.dateTimeChanged.connect(self._emit_changed)
        self._interval_spin.valueChanged.connect(self._emit_changed)
        self._on_source_changed()

    # ── visibility ────────────────────────────────────────────────────────────

    def _on_source_changed(self, *_):
        idx        = self._src_combo.currentIndex()
        is_retro   = (idx == 1)
        is_fore    = (idx == 2)
        is_usgs    = (idx == 3)
        is_csv     = (idx == 4)
        any_picked = (idx >= 1)
        need_dates = any_picked and not is_csv
        need_file  = is_csv
        need_gage  = is_usgs

        self._gage_lbl.setVisible(need_gage)
        self._gage_edit.setVisible(need_gage)
        self._usgs_note.setVisible(need_gage)

        self._file_lbl.setVisible(need_file)
        self._file_edit.setVisible(need_file)
        self._browse_btn.setVisible(need_file)
        self._csv_note.setVisible(is_csv)

        self._start_lbl.setVisible(need_dates)
        self._start_date.setVisible(need_dates)
        self._end_lbl.setVisible(need_dates)
        self._end_date.setVisible(need_dates)

        self._retro_note.setVisible(is_retro)
        self._forecast_note.setVisible(is_fore)

        self._interval_spin.setVisible(any_picked)
        self._interval_lbl_widget.setVisible(any_picked)

        # Set sensible default date windows when switching source
        if is_fore:
            today_qt = QDateTime(QDate.currentDate(), QTime(0, 0))
            self._start_date.setDateTime(today_qt)
            self._end_date.setDateTime(today_qt.addDays(7))
        elif is_retro and self._start_date.dateTime() > QDateTime.fromString(
            "2020-12-31 23:00", "yyyy-MM-dd HH:mm"
        ):
            # Snap back into retrospective range if currently beyond it
            self._start_date.setDateTime(
                QDateTime.fromString("2020-11-01 00:00", "yyyy-MM-dd HH:mm")
            )
            self._end_date.setDateTime(
                QDateTime.fromString("2020-12-01 00:00", "yyyy-MM-dd HH:mm")
            )

        self._file_edit.clear()
        self._emit_changed()

    def _browse_file(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "Select discharge file", "",
            "CSV/Excel (*.csv *.xlsx *.xls *.txt);;All files (*)",
        )
        if f:
            self._file_edit.setText(f)

    def _emit_changed(self, *_):
        self.config_changed.emit()

    # ── public API ────────────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        idx = self._src_combo.currentIndex()
        if idx == 0:
            return False
        if idx in (1, 2):       # NWM retro / forecast — dates always set
            return True
        if idx == 3:            # USGS — needs gage number
            return bool(self._gage_edit.text().strip())
        if idx == 4:            # CSV — needs a file
            return bool(self._file_edit.text().strip())
        return False

    def source_label(self) -> str:
        labels = ["—", "NWM Retro", "NWM Forecast", "USGS", "CSV"]
        idx = self._src_combo.currentIndex()
        return labels[idx] if idx < len(labels) else "—"

    def get_config(self) -> dict:
        idx        = self._src_combo.currentIndex()
        bdy_source = self._SRC_KEYS[idx] if idx < len(self._SRC_KEYS) else ""
        return {
            "bdy_source":     bdy_source,
            "gage_id":        self._gage_edit.text().strip(),
            "file_path":      self._file_edit.text().strip(),
            "start_dt":       self._start_date.dateTime().toPyDateTime(),
            "end_dt":         self._end_date.dateTime().toPyDateTime(),
            "interval_hours": float(self._interval_spin.value()),
        }

    def set_config(self, cfg: dict):
        if not cfg:
            return
        src_idx = {
            "nwm_retro": 1, "nwm": 1,      # legacy "nwm" → retro
            "nwm_forecast": 2,
            "usgs": 3,
            "csv": 4,
        }.get(cfg.get("bdy_source", ""), 0)
        self._src_combo.setCurrentIndex(src_idx)
        self._gage_edit.setText(cfg.get("gage_id", ""))
        self._file_edit.setText(cfg.get("file_path", ""))
        try:
            from datetime import datetime as dt
            sd = cfg.get("start_dt")
            if isinstance(sd, dt):
                self._start_date.setDateTime(
                    QDateTime(sd.year, sd.month, sd.day, sd.hour, sd.minute)
                )
            ed = cfg.get("end_dt")
            if isinstance(ed, dt):
                self._end_date.setDateTime(
                    QDateTime(ed.year, ed.month, ed.day, ed.hour, ed.minute)
                )
        except Exception:
            pass
        try:
            self._interval_spin.setValue(float(cfg.get("interval_hours", 1.0)))
        except Exception:
            pass
