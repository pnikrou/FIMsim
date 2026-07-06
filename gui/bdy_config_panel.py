"""Reusable BDY (hydrograph time-series) configuration panel.

Sources:
  1. NWM Retrospective (1979-2023) — NOAA v3.0 reanalysis, pick an event window
  2. NWM Forecast (2019-now)       — archived operational forecast run
  3. USGS Stream Gage              — any gage with instantaneous (15-min) data
  4. CSV / XLSX file               — user-supplied discharge table

Used in step_bdy directly (single AOI) and inside AOIBDYCard (multi-AOI).
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QPushButton, QFileDialog, QComboBox, QDateTimeEdit, QDoubleSpinBox,
    QRadioButton, QButtonGroup,
)
from PyQt6.QtCore import pyqtSignal, QDateTime, QDate, QTime


class BDYConfigPanel(QWidget):
    config_changed = pyqtSignal()

    # Combo index → internal key
    _SRC_KEYS = ["", "nwm_retro", "nwm_forecast", "usgs", "csv"]
    _IDX_RETRO, _IDX_FORECAST, _IDX_USGS, _IDX_CSV = 1, 2, 3, 4

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
            "NWM Retrospective (1979-2023)",
            "NWM Forecast (2019-now)",
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

        # ── NWM feature ID (auto-detect vs manual) — retro + forecast ─────
        self._fid_lbl = QLabel("Feature ID:")
        fid_row = QHBoxLayout()
        fid_row.setSpacing(10)
        self._fid_auto_rb  = QRadioButton("Auto-detect")
        self._fid_manual_rb = QRadioButton("Enter manually:")
        self._fid_auto_rb.setChecked(True)
        self._fid_group = QButtonGroup(self)
        self._fid_group.addButton(self._fid_auto_rb,  0)
        self._fid_group.addButton(self._fid_manual_rb, 1)
        self._fid_edit = QLineEdit()
        self._fid_edit.setPlaceholderText("e.g.  23212900")
        self._fid_edit.setFixedWidth(130)
        self._fid_edit.setEnabled(False)
        fid_row.addWidget(self._fid_auto_rb)
        fid_row.addWidget(self._fid_manual_rb)
        fid_row.addWidget(self._fid_edit)
        fid_row.addStretch()
        self._fid_manual_rb.toggled.connect(
            lambda checked: self._fid_edit.setEnabled(checked)
        )
        self._fid_manual_rb.toggled.connect(self._emit_changed)
        self._fid_edit.textChanged.connect(self._emit_changed)

        fid_widget = QWidget()
        fid_widget.setLayout(fid_row)
        self._fid_lbl_widget = self._fid_lbl
        form.addRow(self._fid_lbl_widget, fid_widget)
        self._fid_row_widget = fid_widget

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

        # ── Event window (retrospective + USGS) ───────────────────────────
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

        self._retro_note = QLabel(
            "★ NWM v3.0 reanalysis, available 1979-02-01 → 2023-01-31  |  "
            "resampled to chosen interval  |  USA only"
        )
        self._retro_note.setWordWrap(True)
        self._retro_note.setStyleSheet("color:#718096; font-size:11px;")
        form.addRow(self._retro_note)

        # ── Forecast controls (issue date + range + cycle) ────────────────
        self._fc_range_lbl = QLabel("Forecast range:")
        self._fc_range_combo = QComboBox()
        self._fc_range_combo.addItems(["medium_range", "short_range", "long_range"])
        form.addRow(self._fc_range_lbl, self._fc_range_combo)

        self._fc_date_lbl = QLabel("Issue date:")
        self._fc_date = QDateTimeEdit()
        self._fc_date.setDisplayFormat("yyyy-MM-dd")
        self._fc_date.setCalendarPopup(True)
        self._fc_date.setDateTime(
            QDateTime.fromString("2024-06-01 00:00", "yyyy-MM-dd HH:mm")
        )
        form.addRow(self._fc_date_lbl, self._fc_date)

        self._fc_hour_lbl = QLabel("Cycle hour (UTC):")
        self._fc_hour_combo = QComboBox()
        self._fc_hour_combo.addItems(["00", "06", "12", "18"])
        form.addRow(self._fc_hour_lbl, self._fc_hour_combo)

        self._forecast_note = QLabel(
            "★ Archived NWM operational forecast (2018-09 → today).  A run issued "
            "on the chosen date/cycle: short ~18 h, medium ~10 days, long ~30 days."
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
        self._fc_range_combo.currentIndexChanged.connect(self._emit_changed)
        self._fc_date.dateTimeChanged.connect(self._emit_changed)
        self._fc_hour_combo.currentIndexChanged.connect(self._emit_changed)
        self._on_source_changed()

    # ── visibility ────────────────────────────────────────────────────────────

    def _on_source_changed(self, *_):
        idx         = self._src_combo.currentIndex()
        is_retro    = (idx == self._IDX_RETRO)
        is_forecast = (idx == self._IDX_FORECAST)
        is_usgs     = (idx == self._IDX_USGS)
        is_csv      = (idx == self._IDX_CSV)
        any_picked  = (idx >= 1)
        need_dates  = is_retro or is_usgs         # event-window sources
        need_fid    = is_retro or is_forecast     # NWM reach ID

        self._gage_lbl.setVisible(is_usgs)
        self._gage_edit.setVisible(is_usgs)
        self._usgs_note.setVisible(is_usgs)

        self._fid_lbl_widget.setVisible(need_fid)
        self._fid_row_widget.setVisible(need_fid)
        if not need_fid:
            self._fid_auto_rb.setChecked(True)

        self._file_lbl.setVisible(is_csv)
        self._file_edit.setVisible(is_csv)
        self._browse_btn.setVisible(is_csv)
        self._csv_note.setVisible(is_csv)

        self._start_lbl.setVisible(need_dates)
        self._start_date.setVisible(need_dates)
        self._end_lbl.setVisible(need_dates)
        self._end_date.setVisible(need_dates)
        self._retro_note.setVisible(is_retro)

        # Forecast-only controls
        for w in (self._fc_range_lbl, self._fc_range_combo, self._fc_date_lbl,
                  self._fc_date, self._fc_hour_lbl, self._fc_hour_combo,
                  self._forecast_note):
            w.setVisible(is_forecast)

        self._interval_spin.setVisible(any_picked)
        self._interval_lbl_widget.setVisible(any_picked)

        # Keep the retrospective window inside its coverage.
        if is_retro and self._start_date.dateTime() > QDateTime.fromString(
            "2023-01-31 23:00", "yyyy-MM-dd HH:mm"
        ):
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
        if idx in (self._IDX_RETRO, self._IDX_FORECAST):   # NWM — needs a reach
            if self._fid_manual_rb.isChecked():
                return bool(self._fid_edit.text().strip())
            return True
        if idx == self._IDX_USGS:
            return bool(self._gage_edit.text().strip())
        if idx == self._IDX_CSV:
            return bool(self._file_edit.text().strip())
        return False

    def source_label(self) -> str:
        labels = {
            self._IDX_RETRO: "NWM Retro", self._IDX_FORECAST: "NWM Forecast",
            self._IDX_USGS: "USGS", self._IDX_CSV: "CSV",
        }
        return labels.get(self._src_combo.currentIndex(), "—")

    def get_config(self) -> dict:
        idx        = self._src_combo.currentIndex()
        bdy_source = self._SRC_KEYS[idx] if idx < len(self._SRC_KEYS) else ""
        manual_fid = (
            self._fid_edit.text().strip()
            if self._fid_manual_rb.isChecked()
            else ""
        )
        return {
            "bdy_source":        bdy_source,
            "gage_id":           self._gage_edit.text().strip(),
            "file_path":         self._file_edit.text().strip(),
            "start_dt":          self._start_date.dateTime().toPyDateTime(),
            "end_dt":            self._end_date.dateTime().toPyDateTime(),
            "interval_hours":    float(self._interval_spin.value()),
            "manual_feature_id": manual_fid,
            "forecast_range":    self._fc_range_combo.currentText(),
            "forecast_date":     self._fc_date.dateTime().toString("yyyy-MM-dd"),
            "forecast_hour":     int(self._fc_hour_combo.currentText()),
        }

    def set_config(self, cfg: dict):
        if not cfg:
            return
        src_idx = {
            "nwm_retro": self._IDX_RETRO, "nwm": self._IDX_RETRO,  # legacy → retro
            "nwm_forecast": self._IDX_FORECAST,
            "usgs": self._IDX_USGS,
            "csv": self._IDX_CSV,
        }.get(cfg.get("bdy_source", ""), 0)
        self._src_combo.setCurrentIndex(src_idx)
        self._gage_edit.setText(cfg.get("gage_id", ""))
        self._file_edit.setText(cfg.get("file_path", ""))
        manual_fid = cfg.get("manual_feature_id", "")
        if manual_fid:
            self._fid_manual_rb.setChecked(True)
            self._fid_edit.setText(manual_fid)
        else:
            self._fid_auto_rb.setChecked(True)
            self._fid_edit.clear()
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
        if cfg.get("forecast_range"):
            self._fc_range_combo.setCurrentText(cfg["forecast_range"])
        if cfg.get("forecast_date"):
            self._fc_date.setDateTime(
                QDateTime.fromString(str(cfg["forecast_date"]), "yyyy-MM-dd"))
        if cfg.get("forecast_hour") is not None:
            self._fc_hour_combo.setCurrentText(f"{int(cfg['forecast_hour']):02d}")
