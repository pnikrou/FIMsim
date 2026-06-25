"""Reusable BDY (hydrograph time-series) configuration panel.

Sources:
  1. NWM Retrospective  — 1979-02-01 → 2020-12-31 (NOAA v2.1)
  2. USGS Stream Gage   — any gage with instantaneous (15-min) data
  3. CSV / XLSX file    — user-supplied discharge table

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
    _SRC_KEYS = ["", "nwm_retro", "usgs", "csv"]

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

        # ── NWM feature ID (auto-detect vs manual) ───────────────────────
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
        is_usgs    = (idx == 2)
        is_csv     = (idx == 3)
        any_picked = (idx >= 1)
        need_dates = any_picked and not is_csv
        need_file  = is_csv
        need_gage  = is_usgs
        need_fid   = is_retro

        self._gage_lbl.setVisible(need_gage)
        self._gage_edit.setVisible(need_gage)
        self._usgs_note.setVisible(need_gage)

        self._fid_lbl_widget.setVisible(need_fid)
        self._fid_row_widget.setVisible(need_fid)
        if not need_fid:
            self._fid_auto_rb.setChecked(True)

        self._file_lbl.setVisible(need_file)
        self._file_edit.setVisible(need_file)
        self._browse_btn.setVisible(need_file)
        self._csv_note.setVisible(is_csv)

        self._start_lbl.setVisible(need_dates)
        self._start_date.setVisible(need_dates)
        self._end_lbl.setVisible(need_dates)
        self._end_date.setVisible(need_dates)

        self._retro_note.setVisible(is_retro)

        self._interval_spin.setVisible(any_picked)
        self._interval_lbl_widget.setVisible(any_picked)

        if is_retro and self._start_date.dateTime() > QDateTime.fromString(
            "2020-12-31 23:00", "yyyy-MM-dd HH:mm"
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
        if idx == 1:            # NWM retro
            if self._fid_manual_rb.isChecked():
                return bool(self._fid_edit.text().strip())
            return True
        if idx == 2:            # USGS — needs gage number
            return bool(self._gage_edit.text().strip())
        if idx == 3:            # CSV — needs a file
            return bool(self._file_edit.text().strip())
        return False

    def source_label(self) -> str:
        labels = ["—", "NWM Retro", "USGS", "CSV"]
        idx = self._src_combo.currentIndex()
        return labels[idx] if idx < len(labels) else "—"

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
        }

    def set_config(self, cfg: dict):
        if not cfg:
            return
        src_idx = {
            "nwm_retro": 1, "nwm": 1,      # legacy "nwm" → retro
            "usgs": 2,
            "csv": 3,
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
