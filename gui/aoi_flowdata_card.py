"""Per-AOI Flow Data configuration card — same accordion pattern as AOIDEMCard."""
from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QPushButton,
    QLineEdit, QFileDialog, QComboBox, QDateTimeEdit, QWidget,
)
from PyQt6.QtCore import pyqtSignal, QDateTime


class AOIFlowdataCard(QFrame):
    expand_requested = pyqtSignal(object)
    config_changed   = pyqtSignal(object)

    EXPANDED_STYLE = (
        "QFrame#card { background:#f9fafb; border:2px solid #a0aec0; "
        "border-radius:6px; padding:8px; }"
    )
    COLLAPSED_STYLE = (
        "QFrame#card { background:#f9fafb; border:1px solid #e2e8f0; "
        "border-radius:6px; padding:6px; }"
    )

    def __init__(self, aoi_name: str, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self._aoi_name = aoi_name
        self._expanded = False
        self._build_ui()
        self._apply_collapsed_style()
        self._refresh_status()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 2)
        outer.setSpacing(6)

        # ── Header row ────────────────────────────────────────────────────────
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)

        self._caret = QLabel("▶")
        self._caret.setFixedWidth(14)
        self._caret.setStyleSheet("color:#4a5568; font-weight:bold;")
        header.addWidget(self._caret)

        self._name_lbl = QLabel(f"<b>{self._aoi_name}</b>")
        self._name_lbl.setStyleSheet("color:#2d3748;")
        header.addWidget(self._name_lbl)
        header.addStretch()

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet("color:#666; font-size:11px;")
        header.addWidget(self._status_lbl)

        self._toggle_btn = QPushButton("Edit")
        self._toggle_btn.setFixedWidth(80)
        self._toggle_btn.clicked.connect(self._on_toggle_clicked)
        header.addWidget(self._toggle_btn)

        outer.addLayout(header)

        # ── Config panel ──────────────────────────────────────────────────────
        self._panel = QWidget()
        pl = QVBoxLayout(self._panel)
        pl.setContentsMargins(18, 4, 4, 4)
        pl.setSpacing(8)

        # ── Source row — two options ──────────────────────────────────────────
        src_widget = QWidget()
        src_inner = QHBoxLayout(src_widget)
        src_inner.setContentsMargins(0, 0, 0, 0)
        src_inner.addWidget(QLabel("<b>Source:</b>"))
        src_inner.addSpacing(8)

        self._src_combo = QComboBox()
        self._src_combo.addItem("Download from NWM (NOAA — USA only)", "nwm")
        self._src_combo.addItem("USGS Gage",                           "usgs")
        self._src_combo.setFixedWidth(270)

        src_inner.addWidget(self._src_combo)
        src_inner.addStretch()
        pl.addWidget(src_widget)

        # ── NWM details form ──────────────────────────────────────────────────
        self._nwm_widget = QWidget()
        nf = QFormLayout(self._nwm_widget)
        nf.setContentsMargins(0, 0, 0, 0)
        nf.setVerticalSpacing(6)

        # Feature IDs
        fid_row = QHBoxLayout()
        self._fids_edit = QLineEdit()
        self._fids_edit.setPlaceholderText(
            "Single ID (e.g. 22164566), comma-separated, or path to .csv"
        )
        fid_browse = QPushButton("Browse…")
        fid_browse.setFixedWidth(75)
        fid_browse.clicked.connect(self._browse_fids)
        fid_row.addWidget(self._fids_edit)
        fid_row.addWidget(fid_browse)
        nf.addRow("Feature ID(s):", fid_row)

        nf.addRow(QLabel(
            "<small><i>CSV: one column, one feature_id per line "
            "(use the feature_ids_*.csv saved by the Flowline step).</i></small>"
        ))

        self._nwm_start = QDateTimeEdit()
        self._nwm_start.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._nwm_start.setCalendarPopup(True)
        self._nwm_start.setDateTime(QDateTime(2018, 9, 1, 0, 0))
        nf.addRow("Start date:", self._nwm_start)

        self._nwm_end = QDateTimeEdit()
        self._nwm_end.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._nwm_end.setCalendarPopup(True)
        self._nwm_end.setDateTime(QDateTime(2018, 9, 30, 23, 0))
        nf.addRow("End date:", self._nwm_end)

        self._nwm_interval = QComboBox()
        for lbl, val in [("0.5 hours", 0.5), ("1 hour", 1.0), ("3 hours", 3.0),
                          ("6 hours", 6.0), ("12 hours", 12.0), ("24 hours", 24.0)]:
            self._nwm_interval.addItem(lbl, val)
        self._nwm_interval.setCurrentIndex(1)   # 1 hour default
        nf.addRow("Interval:", self._nwm_interval)

        # One-line ★ note explaining retrospective/forecast auto-switching
        self._nwm_note = QLabel(
            "★  NWM retrospective covers 1979-02-01 to 2020-12-31. "
            "After that date the app uses the NWM operational forecast "
            "(medium-range, ~10-day horizon)."
        )
        self._nwm_note.setWordWrap(False)
        nf.addRow(self._nwm_note)

        pl.addWidget(self._nwm_widget)

        # ── USGS details form ─────────────────────────────────────────────────
        self._usgs_widget = QWidget()
        uf = QFormLayout(self._usgs_widget)
        uf.setContentsMargins(0, 0, 0, 0)
        uf.setVerticalSpacing(6)

        gage_row = QHBoxLayout()
        self._gage_edit = QLineEdit()
        self._gage_edit.setPlaceholderText(
            "Single gage (e.g. 02428400), comma-separated, or path to .csv"
        )
        gage_browse = QPushButton("Browse…")
        gage_browse.setFixedWidth(75)
        gage_browse.clicked.connect(self._browse_gages)
        gage_row.addWidget(self._gage_edit)
        gage_row.addWidget(gage_browse)
        uf.addRow("Gage number(s):", gage_row)

        gage_note = QLabel(
            "<small><i>CSV: one column, no header, one gage ID per line. "
            "You can use the USGS gages CSV from the Flowline step.</i></small>"
        )
        gage_note.setWordWrap(True)
        uf.addRow(gage_note)

        self._usgs_start = QDateTimeEdit()
        self._usgs_start.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._usgs_start.setCalendarPopup(True)
        self._usgs_start.setDateTime(QDateTime(2018, 9, 1, 0, 0))
        uf.addRow("Start date:", self._usgs_start)

        self._usgs_end = QDateTimeEdit()
        self._usgs_end.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._usgs_end.setCalendarPopup(True)
        self._usgs_end.setDateTime(QDateTime(2018, 9, 30, 23, 0))
        uf.addRow("End date:", self._usgs_end)

        # Interval — USGS IV service returns 15-min data; resample to desired step
        self._usgs_interval = QComboBox()
        for lbl, val in [
            ("15 min",    0.25),
            ("30 min",    0.5),
            ("1 hour",    1.0),
            ("3 hours",   3.0),
            ("6 hours",   6.0),
            ("12 hours", 12.0),
            ("24 hours", 24.0),
        ]:
            self._usgs_interval.addItem(lbl, val)
        self._usgs_interval.setCurrentIndex(2)   # 1 hour default
        uf.addRow("Interval:", self._usgs_interval)

        pl.addWidget(self._usgs_widget)

        self._panel.setVisible(False)
        outer.addWidget(self._panel)

        # Wire signals
        self._src_combo.currentIndexChanged.connect(self._on_source_changed)
        for w in (self._fids_edit, self._gage_edit):
            w.textChanged.connect(self._on_config_changed)
        for dt in (self._nwm_start, self._nwm_end, self._usgs_start, self._usgs_end):
            dt.dateTimeChanged.connect(self._on_config_changed)
        for cb in (self._nwm_interval, self._usgs_interval):
            cb.currentIndexChanged.connect(self._on_config_changed)

        # Default visibility
        self._on_source_changed()

    # ── signal handlers ───────────────────────────────────────────────────────

    def _on_source_changed(self, *_):
        src = self._src_combo.currentData()
        self._nwm_widget.setVisible(src == "nwm")
        self._usgs_widget.setVisible(src == "usgs")
        self._on_config_changed()

    def _on_config_changed(self):
        self._refresh_status()
        self.config_changed.emit(self)

    def _browse_fids(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "Select Feature ID CSV", "", "CSV (*.csv *.txt);;All (*)"
        )
        if f:
            self._fids_edit.setText(f)

    def _browse_gages(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "Select Gage CSV", "", "CSV (*.csv *.txt);;All (*)"
        )
        if f:
            self._gage_edit.setText(f)

    # ── expand / collapse ─────────────────────────────────────────────────────

    def expand(self):
        if self._expanded:
            return
        self._expanded = True
        self._panel.setVisible(True)
        self._toggle_btn.setText("Done")
        self._caret.setText("▼")
        self.setStyleSheet(self.EXPANDED_STYLE)

    def collapse(self):
        if not self._expanded:
            return
        self._expanded = False
        self._panel.setVisible(False)
        self._toggle_btn.setText("Edit")
        self._caret.setText("▶")
        self.setStyleSheet(self.COLLAPSED_STYLE)
        self._refresh_status()

    def is_expanded(self) -> bool:
        return self._expanded

    def _on_toggle_clicked(self):
        if self._expanded:
            self.collapse()
        else:
            self.expand_requested.emit(self)

    def _apply_collapsed_style(self):
        self.setStyleSheet(self.COLLAPSED_STYLE)

    # ── status summary ────────────────────────────────────────────────────────

    def _refresh_status(self):
        src = self._src_combo.currentData()
        if src == "usgs":
            ids      = self._gage_edit.text().strip() or "(no gages)"
            start    = self._usgs_start.date().toString("yyyy-MM-dd")
            end      = self._usgs_end.date().toString("yyyy-MM-dd")
            interval = self._usgs_interval.currentText()
            self._status_lbl.setText(
                f"USGS  ·  Gages: {ids}  ·  {start} → {end}  ·  {interval}"
            )
        else:
            ids      = self._fids_edit.text().strip() or "(no IDs)"
            start    = self._nwm_start.date().toString("yyyy-MM-dd")
            end      = self._nwm_end.date().toString("yyyy-MM-dd")
            interval = self._nwm_interval.currentText()
            self._status_lbl.setText(
                f"NWM  ·  IDs: {ids}  ·  {start} → {end}  ·  {interval}"
            )

    # ── public API ────────────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        # Always ready — feature IDs / gage IDs have sensible defaults and
        # the backend will log a warning and skip if nothing is supplied.
        return True

    def get_config(self) -> dict:
        src = self._src_combo.currentData()   # "nwm" or "usgs"
        cfg: dict = {
            "flow_source":      src,
            "discharge_source": src,
        }
        if src == "nwm":
            cfg["feature_ids"]    = self._fids_edit.text().strip()
            cfg["event_start_dt"] = self._nwm_start.dateTime().toPyDateTime()
            cfg["event_end_dt"]   = self._nwm_end.dateTime().toPyDateTime()
            cfg["interval_hours"] = float(self._nwm_interval.currentData())
        else:
            cfg["gage_ids"]            = self._gage_edit.text().strip()
            cfg["event_start_dt"]      = self._usgs_start.dateTime().toPyDateTime()
            cfg["event_end_dt"]        = self._usgs_end.dateTime().toPyDateTime()
            cfg["usgs_interval_hours"] = float(self._usgs_interval.currentData())
        return cfg

    def set_config(self, cfg: dict):
        raw_src = cfg.get("flow_source") or cfg.get("discharge_source", "nwm")
        # Map legacy "retrospective"/"forecast" values to "nwm"
        if raw_src in ("retrospective", "forecast"):
            raw_src = "nwm"
        idx = self._src_combo.findData(raw_src)
        if idx >= 0:
            self._src_combo.setCurrentIndex(idx)
        if raw_src == "nwm":
            self._fids_edit.setText(cfg.get("feature_ids", ""))
            if cfg.get("event_start_dt"):
                self._nwm_start.setDateTime(
                    QDateTime.fromString(
                        str(cfg["event_start_dt"])[:16], "yyyy-MM-dd HH:mm"
                    )
                )
            if cfg.get("event_end_dt"):
                self._nwm_end.setDateTime(
                    QDateTime.fromString(
                        str(cfg["event_end_dt"])[:16], "yyyy-MM-dd HH:mm"
                    )
                )
            iv_idx = self._nwm_interval.findData(
                float(cfg.get("interval_hours", 1.0))
            )
            if iv_idx >= 0:
                self._nwm_interval.setCurrentIndex(iv_idx)
        else:
            self._gage_edit.setText(cfg.get("gage_ids", ""))
            if cfg.get("event_start_dt"):
                self._usgs_start.setDateTime(
                    QDateTime.fromString(
                        str(cfg["event_start_dt"])[:16], "yyyy-MM-dd HH:mm"
                    )
                )
            if cfg.get("event_end_dt"):
                self._usgs_end.setDateTime(
                    QDateTime.fromString(
                        str(cfg["event_end_dt"])[:16], "yyyy-MM-dd HH:mm"
                    )
                )
            iv_idx = self._usgs_interval.findData(
                float(cfg.get("usgs_interval_hours", 1.0))
            )
            if iv_idx >= 0:
                self._usgs_interval.setCurrentIndex(iv_idx)
        self._refresh_status()
