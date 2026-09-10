"""ARC-Curve2Flood Step 5 — Streamflow (per-AOI flow files from NWM).

Multi-AOI controller matching the other steps:
  * 1 AOI   → one ArcFlowConfigPanel embedded directly.
  * >1 AOIs → accordion of AOIArcFlowCard widgets (Edit / Remove chrome)
              with "Apply current AOI's settings to all" — each AOI keeps its
              own event window, since floods happen at different times.

Output per AOI:
  <AOI>/arc-files/flow.csv  (COMID,base,max) — ARC builds a rating curve per
  reach and Curve2Flood maps the 'max' (peak) flow.
"""
import csv
import re
from pathlib import Path
from typing import List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QGroupBox, QProgressBar, QScrollArea, QStackedWidget, QMessageBox,
    QComboBox, QDateEdit, QDateTimeEdit, QDoubleSpinBox, QSpinBox, QCheckBox,
    QLineEdit, QRadioButton, QButtonGroup, QInputDialog,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
)
from PyQt6.QtCore import pyqtSignal, Qt, QDate, QDateTime, QTime

from core.arc_orchestrate import run_arc_flowfile_for_all_aois
from gui.worker import Worker
from gui.run_button import set_running, set_ready

_FLOW_STEP_RE = re.compile(r"^▶\s+Flow file\s+\[(\d+)/(\d+)\]")
_FLOW_DONE_RE = re.compile(r"^✓\s+Flow file\s+\[(\d+)/(\d+)\]\s+finished")

_F_RANGES = ["short_range", "medium_range", "long_range"]
_F_CYCLES = ["Auto", "00", "06", "12", "18"]


# ── Config panel (shared by single-AOI page and each card) ────────────────────

# The key is stored and read by the backend (core/api_keys.py), because the RUN
# is what needs it.  The panel only offers a place to type it the first time.
from core.api_keys import (load_nwm_api_key as _load_api_key,
                           save_nwm_api_key as _save_api_key)


class ArcFlowConfigPanel(QWidget):
    """NenCarta streamflow settings for ONE AOI.

    Laid out the way OWP HAND-FIM's FIM step is, because it is the same set of
    decisions: pick the source, then the record, then the period.  Each choice
    narrows what the next one offers, so no impossible combination is on screen.

    NenCarta fetches nothing here — FIMsim supplies finished flow files — so
    these are its watershed keys plus FIMsim's own record/period choices.
    """

    config_changed = pyqtSignal()

    # What each source covers.  Verified against the stores themselves.
    _WINDOWS = {
        "GEOGLOWS": {"retro": ("1940-01-01", "2026-09-03"),
                     "fore":  ("2024-07-01", "today")},
        "NWM":      {"retro": ("1979-02-01", "2023-02-01"),
                     "fore":  ("2018-09-17", "today")},
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)

        # ── 1. source ────────────────────────────────────────────────────────
        s_row = QHBoxLayout()
        s_row.addWidget(QLabel("Streamflow source:"))
        self._src_combo = QComboBox()
        self._src_combo.addItem("GEOGLOWS", "GEOGLOWS")
        self._src_combo.addItem("NWM", "NWM")
        self._src_combo.setFixedWidth(160)
        self._src_combo.currentIndexChanged.connect(self._on_changed)
        s_row.addWidget(self._src_combo)
        s_row.addStretch()
        layout.addLayout(s_row)

        # ── 2. record, with each window spelled out ──────────────────────────
        r_row = QHBoxLayout()
        r_row.addWidget(QLabel("Data record:"))
        self._rec_grp = QButtonGroup(self)
        self._rb_retro = QRadioButton("Retrospective")
        self._rb_fore  = QRadioButton("Forecast")
        self._rb_retro.setChecked(True)
        for rb in (self._rb_retro, self._rb_fore):
            self._rec_grp.addButton(rb)
            rb.toggled.connect(self._on_changed)
            r_row.addWidget(rb)
        r_row.addStretch()
        layout.addLayout(r_row)

        # ── 3a. retrospective: period ────────────────────────────────────────
        self._retro_box = QWidget()
        rv = QVBoxLayout(self._retro_box)
        rv.setContentsMargins(12, 0, 0, 0)

        pm = QHBoxLayout()
        pm.addWidget(QLabel("Simulation period:"))
        self._period = QComboBox()
        self._period.addItem("Specific time  (one flood map)", "snapshot")
        self._period.addItem("Duration  (one flood map per timestep)", "duration")
        self._period.setFixedWidth(300)
        self._period.currentIndexChanged.connect(self._on_changed)
        pm.addWidget(self._period)
        pm.addStretch()
        rv.addLayout(pm)

        # specific time
        self._spec_box = QWidget()
        sp = QHBoxLayout(self._spec_box)
        sp.setContentsMargins(0, 0, 0, 0)
        sp.addWidget(QLabel("Date & hour (UTC):"))
        self._spec_dt = QDateTimeEdit()
        self._spec_dt.setDisplayFormat("yyyy-MM-dd HH:00")
        self._spec_dt.setCalendarPopup(True)
        self._spec_dt.setDateTime(QDateTime.currentDateTime().addDays(-40))
        self._spec_dt.dateTimeChanged.connect(self._on_changed)
        sp.addWidget(self._spec_dt)
        sp.addStretch()
        rv.addWidget(self._spec_box)

        # duration
        self._dur_box = QWidget()
        dr = QHBoxLayout(self._dur_box)
        dr.setContentsMargins(0, 0, 0, 0)
        dr.addWidget(QLabel("From:"))
        self._start = QDateTimeEdit()
        self._start.setDisplayFormat("yyyy-MM-dd HH:00")
        self._start.setCalendarPopup(True)
        self._start.setDateTime(QDateTime.currentDateTime().addDays(-41))
        self._start.dateTimeChanged.connect(self._on_changed)
        dr.addWidget(self._start)
        dr.addWidget(QLabel("to:"))
        self._end = QDateTimeEdit()
        self._end.setDisplayFormat("yyyy-MM-dd HH:00")
        self._end.setCalendarPopup(True)
        self._end.setDateTime(QDateTime.currentDateTime().addDays(-40))
        self._end.dateTimeChanged.connect(self._on_changed)
        dr.addWidget(self._end)
        dr.addWidget(QLabel("every"))
        self._step = QComboBox()
        for lbl, hrs in (("1 h", 1), ("3 h", 3), ("6 h", 6),
                         ("12 h", 12), ("24 h (daily)", 24)):
            self._step.addItem(lbl, hrs)
        self._step.currentIndexChanged.connect(self._on_changed)
        dr.addWidget(self._step)
        dr.addStretch()
        rv.addWidget(self._dur_box)

        self._dur_note = QLabel("")
        self._dur_note.setWordWrap(True)
        self._dur_note.setStyleSheet("color:#975a16; font-size:11px;")
        rv.addWidget(self._dur_note)
        layout.addWidget(self._retro_box)

        # ── 3b. forecast: range, date, hour, aggregation ─────────────────────
        self._fore_box = QWidget()
        fv = QVBoxLayout(self._fore_box)
        fv.setContentsMargins(12, 0, 0, 0)

        f1 = QHBoxLayout()
        f1.addWidget(QLabel("Forecast range:"))
        self._fc_range = QComboBox()
        self._fc_range.addItems(["shortrange", "mediumrange", "longrange"])
        self._fc_range.currentIndexChanged.connect(self._on_changed)
        f1.addWidget(self._fc_range)
        f1.addStretch()
        fv.addLayout(f1)

        self._fc_latest = QCheckBox("Use latest available run")
        self._fc_latest.toggled.connect(self._on_changed)
        fv.addWidget(self._fc_latest)

        # ONE field for the moment to map, exactly like the retrospective
        # "specific time" above.  It used to be a date box plus a separate hour
        # combo that defaulted to 12: setting the date and leaving the combo
        # alone silently mapped noon, and the run log was the first place that
        # showed it.  Minutes are floored to the hour — NWM is hourly.
        f2 = QHBoxLayout()
        self._fc_hour_lbl = QLabel("Map at (UTC):")
        f2.addWidget(self._fc_hour_lbl)
        self._fc_dt = QDateTimeEdit()
        self._fc_dt.setDisplayFormat("yyyy-MM-dd  HH:00")
        self._fc_dt.setCalendarPopup(True)
        self._fc_dt.setDateTime(QDateTime(QDate.currentDate().addDays(-40),
                                          QTime(12, 0)))
        self._fc_dt.dateTimeChanged.connect(self._on_changed)
        f2.addWidget(self._fc_dt)
        f2.addStretch()
        fv.addLayout(f2)

        f3 = QHBoxLayout()
        self._fc_agg_lbl = QLabel("Aggregation (medium / long range only):")
        f3.addWidget(self._fc_agg_lbl)
        self._fc_agg = QComboBox()
        self._fc_agg.addItems(["maximum", "median", "minimum"])
        self._fc_agg.currentIndexChanged.connect(self._on_changed)
        f3.addWidget(self._fc_agg)
        f3.addStretch()
        fv.addLayout(f3)
        layout.addWidget(self._fore_box)

        # ── 4. API key: a GLOBAL credential, not a per-AOI setting ───────────
        # It is the same key for every AOI, so it is stored once (QSettings)
        # and only surfaced here when NWM is selected and none is saved yet.
        self._key_row = QWidget()
        kr = QHBoxLayout(self._key_row)
        kr.setContentsMargins(0, 0, 0, 0)
        self._key_lbl = QLabel("")
        self._key_lbl.setWordWrap(True)
        self._key_lbl.setStyleSheet("font-size:11px;")
        kr.addWidget(self._key_lbl, 1)
        self._key_btn = QPushButton("Set NWM API key…")
        self._key_btn.clicked.connect(self._ask_for_key)
        kr.addWidget(self._key_btn)
        layout.addWidget(self._key_row)

        self._note = QLabel("")
        self._note.setWordWrap(True)
        self._note.setStyleSheet("color:#718096; font-size:11px;")
        layout.addWidget(self._note)

        self._on_changed()

    # ── behaviour ────────────────────────────────────────────────────────────

    def _ask_for_key(self):
        """Ask once, store globally.  Every AOI then uses the same key."""
        cur = _load_api_key()
        text, ok = QInputDialog.getText(
            self, "NWM API key",
            "CIROH key for nwm-api.ciroh.org (used only for NWM return "
            "periods).\nRequest one at hub.ciroh.org → NWM BigQuery API.",
            QLineEdit.EchoMode.Password, cur)
        if ok:
            _save_api_key(text.strip())
            self._on_changed()

    def _src_key(self) -> str:
        return "NWM" if self._src_combo.currentData() == "NWM" else "GEOGLOWS"

    def _on_changed(self, *_):
        src = self._src_key()
        w = self._WINDOWS[src]
        # Put each window in the label itself, so the choice explains itself.
        self._rb_retro.setText(
            f"Retrospective  ({w['retro'][0]} → {w['retro'][1]})")
        self._rb_fore.setText(
            f"Forecast  ({w['fore'][0]} → {w['fore'][1]})")

        is_fore = self._rb_fore.isChecked()
        self._retro_box.setVisible(not is_fore)
        self._fore_box.setVisible(is_fore)
        # Once a key is stored FIMsim just uses it, so the row disappears —
        # it is only shown when NWM is selected and nothing is saved yet, which
        # is the one moment the user has to do something about it.
        needs_key = (src == "NWM" and not _load_api_key())
        self._key_row.setVisible(needs_key)
        if needs_key:
            self._key_lbl.setText(
                "<span style='color:#c53030;'>No NWM API key saved</span> "
                "<span style='color:#718096;'>— needed once, for the return "
                "periods (rp2 / rp100). Request one at hub.ciroh.org.</span>")
            self._key_btn.setText("Set NWM API key…")

        # retrospective sub-choice
        is_dur = (self._period.currentData() == "duration")
        self._dur_box.setVisible(is_dur)
        self._dur_note.setVisible(is_dur)
        self._spec_box.setVisible(not is_dur)
        if is_dur:
            secs = self._start.dateTime().secsTo(self._end.dateTime())
            step = self._step.currentData() or 1
            if secs < 0:
                self._dur_note.setText("⚠ End must be after start.")
            else:
                n = int(secs // 3600 // step) + 1
                self._dur_note.setText(f"→ {n} flood map(s), one per timestep.")

        # forecast sub-choices
        rng = self._fc_range.currentText()
        self._fc_agg_lbl.setEnabled(rng != "shortrange")
        self._fc_agg.setEnabled(rng != "shortrange")
        for wdg in (self._fc_dt, self._fc_hour_lbl):
            wdg.setEnabled(not self._fc_latest.isChecked())

        if src == "NWM":
            self._note.setText(
                "★ NWM needs the <b>NHDPlus</b> flowline (COMID) — the GEOGLOWS "
                "network will not match. FIMsim fetches NWM from the public "
                "NOAA / Google sources; the key is only used for return periods.")
        else:
            self._note.setText(
                "★ GEOGLOWS needs the <b>GEOGLOWS</b> flowline (LINKNO). No key "
                "required. Its retrospective is read hourly.")
        self.config_changed.emit()

    # ── config ───────────────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        return True

    def default_from_flowline(self, flowline_source: str):
        """Pair the source with the flowline this AOI actually has."""
        want = "GEOGLOWS" if str(flowline_source) == "geoglows" else "NWM"
        if self._src_combo.currentData() != want:
            i = self._src_combo.findData(want)
            if i >= 0:
                self._src_combo.setCurrentIndex(i)

    def get_config(self) -> dict:
        src = self._src_key()
        is_fore = self._rb_fore.isChecked()
        cfg = {"record": "forecast" if is_fore else "retrospective"}

        if is_fore:
            rng = self._fc_range.currentText()
            cfg["streamflow_source"] = (
                f"NWM_{rng.replace('range', '_range')}" if src == "NWM"
                else "GEOGLOWS")
            cfg["period_mode"] = "snapshot"
            if not self._fc_latest.isChecked():
                dt = self._fc_dt.dateTime()
                cfg["forensic_forecast_date"] = dt.toString("yyyyMMdd")
                cfg["forensic_forecast_hour"] = dt.time().hour()
            if rng != "shortrange":
                cfg["sort_by"] = self._fc_agg.currentText()
        else:
            cfg["streamflow_source"] = (
                "NWM_short_range" if src == "NWM" else "GEOGLOWS")
            cfg["period_mode"] = self._period.currentData()
            if cfg["period_mode"] == "duration":
                cfg["start_date"] = self._start.dateTime().toString("yyyy-MM-dd HH:00")
                cfg["end_date"]   = self._end.dateTime().toString("yyyy-MM-dd HH:00")
                cfg["step_hours"] = int(self._step.currentData() or 1)
            else:
                dt = self._spec_dt.dateTime()
                cfg["forensic_forecast_date"] = dt.toString("yyyyMMdd")
                cfg["forensic_forecast_hour"] = dt.time().hour()

        key = _load_api_key()
        if key:
            cfg["nwm_api_key"] = key
        return cfg

    def set_config(self, cfg: dict):
        cfg = cfg or {}
        src = ("NWM" if str(cfg.get("streamflow_source", "")).upper()
               .startswith("NWM") else "GEOGLOWS")
        i = self._src_combo.findData(src)
        self._src_combo.setCurrentIndex(max(i, 0))
        (self._rb_fore if cfg.get("record") == "forecast"
         else self._rb_retro).setChecked(True)
        pi = self._period.findData(cfg.get("period_mode", "snapshot"))
        self._period.setCurrentIndex(max(pi, 0))
        for key, wdg in (("start_date", self._start), ("end_date", self._end)):
            if cfg.get(key):
                dt = QDateTime.fromString(str(cfg[key]), "yyyy-MM-dd HH:00")
                if dt.isValid():
                    wdg.setDateTime(dt)
        if cfg.get("step_hours"):
            si = self._step.findData(int(cfg["step_hours"]))
            if si >= 0:
                self._step.setCurrentIndex(si)
        d = cfg.get("forensic_forecast_date")
        if d:
            qd = QDate.fromString(str(d), "yyyyMMdd")
            if qd.isValid():
                h = int(cfg.get("forensic_forecast_hour") or 12)
                self._spec_dt.setDateTime(QDateTime(qd, QTime(h, 0)))
                self._fc_dt.setDateTime(QDateTime(qd, QTime(h, 0)))
        rng = str(cfg.get("streamflow_source", "")).replace("NWM_", "").replace("_range", "range")
        if rng in ("shortrange", "mediumrange", "longrange"):
            self._fc_range.setCurrentText(rng)
        # A key arriving in a saved project is stored globally, not shown.
        if cfg.get("nwm_api_key") and not _load_api_key():
            _save_api_key(str(cfg["nwm_api_key"]))
        self._on_changed()


class AOIArcFlowCard(QFrame):
    expand_requested = pyqtSignal(object)
    config_changed   = pyqtSignal(object)
    remove_requested = pyqtSignal(object)

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
        self.setStyleSheet(self.COLLAPSED_STYLE)
        self._refresh_status()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 2)
        outer.setSpacing(6)

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

        self._remove_btn = QPushButton("Remove")
        self._remove_btn.setFixedWidth(70)
        self._remove_btn.setStyleSheet(
            "background:#e53e3e; color:white; border-radius:3px; "
            "font-size:11px; padding:2px 4px;"
        )
        self._remove_btn.setToolTip(f"Remove {self._aoi_name} from this run")
        self._remove_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        header.addWidget(self._remove_btn)

        outer.addLayout(header)

        self._panel = ArcFlowConfigPanel(self)
        self._panel.setVisible(False)
        self._panel.config_changed.connect(self._forward_config_changed)
        outer.addWidget(self._panel)

    # ── expand / collapse ─────────────────────────────────────────────────────

    def is_expanded(self) -> bool:
        return self._expanded

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

    def _on_toggle_clicked(self):
        if self._expanded:
            self.collapse()
        else:
            self.expand_requested.emit(self)

    # ── status line ───────────────────────────────────────────────────────────

    def _refresh_status(self):
        cfg = self._panel.get_config()
        src = cfg.get("streamflow_source", "GEOGLOWS")
        if cfg.get("period_mode") == "duration":
            self._status_lbl.setText(
                f"<i>{src}</i> &nbsp;·&nbsp; {cfg.get('start_date')} → "
                f"{cfg.get('end_date')} &nbsp;·&nbsp; every "
                f"{cfg.get('step_hours', 24)} h")
            return
        date = cfg.get("forensic_forecast_date")
        if date:
            when = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
            hour = cfg.get("forensic_forecast_hour")
            if hour:
                when += f" t{hour}z"
        else:
            when = "latest forecast"
        self._status_lbl.setText(
            f"<i>{src}</i> &nbsp;·&nbsp; {when} &nbsp;·&nbsp; "
            f"back to {cfg.get('age_of_forecast_days', 7)}d")

    def _forward_config_changed(self):
        self._refresh_status()
        self.config_changed.emit(self)

    # ── public proxies ────────────────────────────────────────────────────────

    def panel(self) -> ArcFlowConfigPanel:
        return self._panel

    def is_ready(self) -> bool:
        return self._panel.is_ready()

    def get_config(self) -> dict:
        return self._panel.get_config()

    def set_config(self, cfg: dict):
        self._panel.set_config(cfg)
        self._refresh_status()


# ── Step widget ───────────────────────────────────────────────────────────────

class StepArcStreamflowWidget(QWidget):
    step_completed = pyqtSignal(dict)

    def __init__(self, log_fn=print, parent=None):
        super().__init__(parent)
        self._log = log_fn
        self._worker = None
        self._ctx_path = None
        self._ctx = None
        self._aoi_features: list = []
        self._cards: List[AOIArcFlowCard] = []
        self._cards_layout: QVBoxLayout = None
        self._stack: QStackedWidget = None
        self._single_panel: ArcFlowConfigPanel = None
        self._setup_ui()

    # ── public API ────────────────────────────────────────────────────────────

    def set_context(self, ctx_path, ctx):
        self._ctx_path = ctx_path
        self._ctx = ctx or {}
        self._aoi_features = list(self._ctx.get("aoi_features", []) or [])
        self._clear_results()
        self._rebuild_for_aoi_count()
        self._pair_with_flowline()

    def _pair_with_flowline(self):
        """Default each card's streamflow source to match its AOI's flowline.

        GEOGLOWS reaches are keyed on LINKNO and NWM's on COMID, so a mismatch
        finds no flow at all rather than failing outright.  Whatever the
        Flowline step actually produced wins.
        """
        import json
        for i, feat in enumerate(self._aoi_features):
            folder = (feat or {}).get("folder_path")
            if not folder:
                continue
            try:
                with open(Path(folder) / "workflow_context.json",
                          "r", encoding="utf-8") as fh:
                    src = json.load(fh).get("arc_flowline_source")
            except Exception:
                continue
            if not src:
                continue
            panel = None
            if getattr(self, "_cards", None) and i < len(self._cards):
                panel = self._cards[i].panel()
            elif i == 0 and getattr(self, "_single_panel", None):
                panel = self._single_panel
            if panel is not None and hasattr(panel, "default_from_flowline"):
                panel.default_from_flowline(src)

    def reset(self):
        self._aoi_features = []
        self._clear_cards()
        self._clear_results()
        self._error_lbl.setVisible(False)
        self._progress.setValue(0)
        self._progress.setVisible(False)
        self._status_lbl.setVisible(False)
        self._stack.setCurrentIndex(0)

    # ── UI construction ───────────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        self._aoi_count_lbl = QLabel("")
        self._aoi_count_lbl.setStyleSheet(
            "padding:6px 10px; background:#f7fafc; border:1px solid #cbd5e0; "
            "border-radius:4px; color:#2d3748; font-size:11px;"
        )
        self._aoi_count_lbl.setWordWrap(True)
        self._aoi_count_lbl.setVisible(False)
        layout.addWidget(self._aoi_count_lbl)

        self._stack = QStackedWidget()
        layout.addWidget(self._stack)

        # Page 0 — single-AOI form
        single_page = QWidget()
        sp_layout = QVBoxLayout(single_page)
        sp_layout.setContentsMargins(0, 0, 0, 0)
        gb = QGroupBox("5. Streamflow")
        gb_layout = QVBoxLayout(gb)
        self._single_panel = ArcFlowConfigPanel(self)
        self._single_panel.config_changed.connect(self._on_single_config_changed)
        gb_layout.addWidget(self._single_panel)
        sp_layout.addWidget(gb)
        sp_layout.addStretch()
        self._stack.addWidget(single_page)

        # Page 1 — multi-AOI accordion
        multi_page = QWidget()
        mp_layout = QVBoxLayout(multi_page)
        mp_layout.setContentsMargins(0, 0, 0, 0)

        top_row = QHBoxLayout()
        self._apply_all_btn = QPushButton("Apply current AOI's settings to all")
        self._apply_all_btn.setStyleSheet(
            "background:#2b6cb0; color:white; padding:6px 14px; "
            "border-radius:3px; font-weight:bold;"
        )
        self._apply_all_btn.setToolTip(
            "Copy the currently expanded AOI's streamflow configuration "
            "to every other AOI in this list."
        )
        self._apply_all_btn.clicked.connect(self._apply_to_all)
        self._apply_all_btn.setEnabled(False)
        top_row.addStretch()
        top_row.addWidget(self._apply_all_btn)
        mp_layout.addLayout(top_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        cards_host = QWidget()
        self._cards_layout = QVBoxLayout(cards_host)
        self._cards_layout.setSpacing(6)
        self._cards_layout.addStretch()
        scroll.setWidget(cards_host)
        mp_layout.addWidget(scroll, 1)
        self._stack.addWidget(multi_page)

        # Run button
        btn_row = QHBoxLayout()
        self._run_btn = QPushButton("Build flow file")
        self._run_btn.setStyleSheet(
            "font-weight:bold; padding:7px 20px; background:#2b6cb0; "
            "color:white; border-radius:4px;"
        )
        self._run_btn.clicked.connect(self._run_step)
        self._run_btn.setVisible(False)
        btn_row.addWidget(self._run_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Progress + status + error
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setVisible(False)
        self._progress.setStyleSheet("QProgressBar { height: 18px; }")
        layout.addWidget(self._progress)

        self._status_lbl = QLabel("")
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setStyleSheet(
            "color:#276749; font-weight:bold; font-size:12px; padding:2px 0px;"
        )
        self._status_lbl.setVisible(False)
        layout.addWidget(self._status_lbl)

        self._error_lbl = QLabel("")
        self._error_lbl.setWordWrap(True)
        self._error_lbl.setStyleSheet(
            "padding:10px; background:#fff5f5; border:1px solid #fc8181; "
            "border-radius:4px; font-size:12px; color:#c53030;"
        )
        self._error_lbl.setVisible(False)
        layout.addWidget(self._error_lbl)

        # Post-run results: clickable AOI list
        self._results_gb = QGroupBox(
            "Per-AOI flow files  —  click an AOI to preview its flow table"
        )
        self._results_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        rgl = QVBoxLayout(self._results_gb)
        self._results_inner = QVBoxLayout()
        self._results_inner.setSpacing(0)
        rgl.addLayout(self._results_inner)
        self._results_gb.setVisible(False)
        layout.addWidget(self._results_gb)

        # Preview: the flow.csv table (COMID, base, max)
        self._gb_preview = QGroupBox("Flow file preview")
        self._gb_preview.setStyleSheet("QGroupBox { font-weight:bold; }")
        self._gb_preview.setMinimumHeight(320)
        pv = QVBoxLayout(self._gb_preview)
        pv.setSpacing(6)
        pv.setContentsMargins(6, 8, 6, 6)

        self._preview_placeholder = QLabel(
            "<i>Click an AOI above to preview its flow file (baseflow and "
            "peak flow per reach).</i>"
        )
        self._preview_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_placeholder.setStyleSheet(
            "color:#888; padding:30px; background:#fafafa; "
            "border:1px dashed #cbd5e0; border-radius:4px;"
        )
        pv.addWidget(self._preview_placeholder)

        self._flow_title_lbl = QLabel("")
        self._flow_title_lbl.setStyleSheet("color:#22543d; font-size:10px;")
        self._flow_title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._flow_title_lbl.setVisible(False)
        pv.addWidget(self._flow_title_lbl)

        self._flow_table = QTableWidget(0, 3)
        self._flow_table.setHorizontalHeaderLabels(
            ["COMID (reach)", "Baseflow (m³/s)", "Peak flow (m³/s)"]
        )
        self._flow_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._flow_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._flow_table.setAlternatingRowColors(True)
        self._flow_table.verticalHeader().setVisible(False)
        self._flow_table.verticalHeader().setDefaultSectionSize(20)
        self._flow_table.setSortingEnabled(True)
        self._flow_table.setStyleSheet(
            "QTableWidget { font-size: 10px; }"
            "QHeaderView::section { font-size: 10px; padding: 2px; }"
        )
        h = self._flow_table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._flow_table.setVisible(False)
        pv.addWidget(self._flow_table, 1)

        self._gb_preview.setVisible(False)
        layout.addWidget(self._gb_preview, 1)

        layout.addStretch()

    # ── Layout switching ──────────────────────────────────────────────────────

    def _rebuild_for_aoi_count(self):
        n = len(self._aoi_features)
        if n == 0:
            self._aoi_count_lbl.setText(
                "<i>No AOIs confirmed yet — go back to the AOI step first.</i>"
            )
            self._aoi_count_lbl.setVisible(True)
            self._stack.setCurrentIndex(0)
            self._run_btn.setVisible(False)
            return
        if n == 1:
            self._aoi_count_lbl.setText("<b>1</b> AOI confirmed.")
            self._aoi_count_lbl.setVisible(True)
            self._stack.setCurrentIndex(0)
            self._run_btn.setText("Build flow file")
            self._run_btn.setVisible(True)
            return
        self._aoi_count_lbl.setText(
            f"<b>{n}</b> AOI(s) confirmed — set each AOI's streamflow source "
            "and event window below (flood dates can differ per AOI).  Click "
            "an AOI to expand its settings."
        )
        self._aoi_count_lbl.setVisible(True)
        self._stack.setCurrentIndex(1)
        self._run_btn.setText("Build flow files for all")
        self._build_cards()

    def _clear_cards(self):
        for c in list(self._cards):
            c.setParent(None)
            c.deleteLater()
        self._cards.clear()

    def _build_cards(self):
        self._clear_cards()
        for feat in self._aoi_features:
            card = AOIArcFlowCard(feat.get("name", "(unnamed)"), self)
            card.expand_requested.connect(self._on_expand_requested)
            card.config_changed.connect(self._on_card_config_changed)
            card.remove_requested.connect(self._on_remove_requested)
            self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)
            self._cards.append(card)
        self._on_card_config_changed(None)

    def _on_remove_requested(self, card):
        idx = self._cards.index(card) if card in self._cards else -1
        if idx < 0:
            return
        aoi_name = (self._aoi_features[idx].get("name", f"AOI {idx+1}")
                    if idx < len(self._aoi_features) else "this AOI")
        reply = QMessageBox.question(
            self, "Remove AOI",
            f"Remove <b>{aoi_name}</b> from this step?\n\n"
            "The AOI's data folder is NOT deleted — only removed from the current run.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._cards.pop(idx)
        if idx < len(self._aoi_features):
            self._aoi_features.pop(idx)
        card.setParent(None)
        card.deleteLater()
        self._on_card_config_changed(None)
        self._aoi_count_lbl.setText(
            f"<b>{len(self._aoi_features)}</b> AOI(s) remaining."
        )

    # ── Accordion ─────────────────────────────────────────────────────────────

    def _on_expand_requested(self, card: AOIArcFlowCard):
        for c in self._cards:
            if c is card:
                c.expand()
            else:
                c.collapse()
        self._apply_all_btn.setEnabled(True)

    def _expanded_card(self) -> Optional[AOIArcFlowCard]:
        for c in self._cards:
            if c.is_expanded():
                return c
        return None

    def _on_card_config_changed(self, _card):
        all_ready = bool(self._cards) and all(c.is_ready() for c in self._cards)
        self._run_btn.setVisible(all_ready)

    def _on_single_config_changed(self):
        if self._stack.currentIndex() == 0 and len(self._aoi_features) <= 1:
            self._run_btn.setVisible(self._single_panel.is_ready())

    def _apply_to_all(self):
        src = self._expanded_card()
        if src is None:
            return
        cfg = src.get_config()
        for c in self._cards:
            if c is not src:
                c.set_config(cfg)
        self._on_card_config_changed(None)

    # ── Run ───────────────────────────────────────────────────────────────────

    def _run_step(self):
        if not self._ctx_path or not self._ctx:
            self._log("Complete earlier steps first.")
            return
        if not self._aoi_features:
            QMessageBox.warning(
                self, "No AOI Confirmed",
                "No AOIs are confirmed.\n\n"
                "Go back to the AOI step and confirm at least one feature first."
            )
            return

        if len(self._aoi_features) <= 1:
            per_aoi = [self._single_panel.get_config()]
        else:
            per_aoi = [c.get_config() for c in self._cards]

        self._error_lbl.setVisible(False)
        self._clear_results()
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._status_lbl.setText("Downloading NWM discharge …")
        self._status_lbl.setVisible(True)
        set_running(self._run_btn)

        self._worker = Worker(
            run_arc_flowfile_for_all_aois,
            ctx_path=self._ctx_path, ctx=self._ctx,
            per_aoi_configs=per_aoi,
        )
        self._worker.message.connect(self._on_message)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    # ── progress / log ────────────────────────────────────────────────────────

    def _on_message(self, msg):
        self._log(msg)
        m = _FLOW_STEP_RE.match(msg)
        if m:
            i, total = int(m.group(1)), int(m.group(2))
            self._status_lbl.setText(
                f"Building flow file {i} / {total} (NWM download) …")
            self._status_lbl.setVisible(True)
            return
        m = _FLOW_DONE_RE.match(msg)
        if m:
            i, total = int(m.group(1)), int(m.group(2))
            self._progress.setValue(int(i / max(total, 1) * 100))

    def _on_done(self, ctx):
        self._error_lbl.setVisible(False)
        self._ctx = ctx
        self._progress.setValue(100)
        n = max(len(self._aoi_features), 1)
        self._status_lbl.setText(f"All {n} AOI(s) processed.")
        self._status_lbl.setVisible(True)
        set_ready(self._run_btn)
        self._build_results(ctx)
        self.step_completed.emit({"ctx_path": self._ctx_path, "ctx": ctx})

    def _on_error(self, msg):
        self._log(f"ERROR: {msg}")
        self._progress.setVisible(False)
        set_ready(self._run_btn)
        first_line = msg.split("\n")[0]
        self._error_lbl.setText(
            f"<b>Error:</b> {first_line}<br>"
            "<small>(See log panel below for full details)</small>"
        )
        self._error_lbl.setVisible(True)

    # ── results + preview ───────────────────────────────────────────────────────

    def _clear_results(self):
        if not hasattr(self, "_results_inner"):
            return
        while self._results_inner.count():
            item = self._results_inner.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        if hasattr(self, "_results_gb"):
            self._results_gb.setVisible(False)
        if hasattr(self, "_gb_preview"):
            self._gb_preview.setVisible(False)
            self._preview_placeholder.setVisible(True)
            self._flow_table.setVisible(False)
            self._flow_title_lbl.setVisible(False)

    def _build_results(self, ctx):
        self._clear_results()
        per_aoi = ctx.get("arc_flowfile_per_aoi", []) or []
        if not per_aoi:
            fc = ctx.get("arc_flow_csv")
            if fc:
                per_aoi = [{
                    "name":     ctx.get("aoi_name", "AOI"),
                    "flow_csv": fc,
                    "reaches":  ctx.get("arc_flow_reaches"),
                    "source":   ctx.get("arc_flow_source", "nwm_retro"),
                }]
        if not per_aoi:
            return

        for entry in per_aoi:
            name = entry.get("name", "?")
            row = QFrame()
            row.setStyleSheet(
                "QFrame { background:#f9fafb; border:1px solid #e2e8f0; "
                "border-radius:3px; padding:3px 6px; }"
                "QFrame:hover { background:#f0f2f5; }"
            )
            rl = QHBoxLayout(row)
            rl.setContentsMargins(6, 2, 6, 2)
            rl.setSpacing(8)
            if entry.get("failed"):
                lbl = QLabel(f"✗  <b>{name}</b> — {entry.get('error', 'failed')}")
                lbl.setStyleSheet("color:#c53030; font-size:11px;")
                lbl.setWordWrap(True)
                rl.addWidget(lbl, 1)
                self._results_inner.addWidget(row)
                continue
            btn = QPushButton(f"  {name}")
            btn.setStyleSheet(
                "QPushButton { text-align:left; background:transparent; "
                "border:none; color:#2d3748; font-weight:bold; padding:2px; }"
                "QPushButton:hover { color:#1a202c; text-decoration:underline; }"
            )
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(
                lambda _checked, e=entry: self._show_preview_for(e)
            )
            rl.addWidget(btn, 1)
            src_txt = ("forecast" if entry.get("source") == "nwm_forecast"
                       else "retrospective")
            hint = QLabel(
                f"<small>{entry.get('reaches')} reach(es) · NWM {src_txt}</small>")
            hint.setStyleSheet("color:#718096; font-size:10px;")
            rl.addWidget(hint)
            self._results_inner.addWidget(row)

        self._results_gb.setVisible(True)
        self._gb_preview.setVisible(True)
        self._preview_placeholder.setVisible(True)
        self._flow_table.setVisible(False)
        self._flow_title_lbl.setVisible(False)

    def _show_preview_for(self, entry: dict):
        fc = entry.get("flow_csv")
        if not fc or not Path(fc).exists():
            self._preview_placeholder.setText(
                f"<span style='color:#c53030;'>Flow file not found: {fc}</span>"
            )
            self._preview_placeholder.setVisible(True)
            self._flow_table.setVisible(False)
            self._flow_title_lbl.setVisible(False)
            return

        rows = []
        try:
            with open(fc, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                header = next(reader, None)
                for r in reader:
                    if len(r) >= 3:
                        rows.append((r[0], r[1], r[2]))
        except Exception as ex:
            self._preview_placeholder.setText(
                f"<span style='color:#c53030;'>Could not read {fc}: {ex}</span>"
            )
            self._preview_placeholder.setVisible(True)
            return

        self._flow_table.setSortingEnabled(False)
        self._flow_table.setRowCount(len(rows))
        for r, (comid, base, mx) in enumerate(rows):
            for col, val in enumerate((comid, base, mx)):
                it = QTableWidgetItem()
                try:
                    it.setData(Qt.ItemDataRole.DisplayRole, float(val))
                except (TypeError, ValueError):
                    it.setText(str(val))
                it.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self._flow_table.setItem(r, col, it)
        self._flow_table.setSortingEnabled(True)
        self._flow_table.sortByColumn(2, Qt.SortOrder.DescendingOrder)

        self._flow_title_lbl.setText(
            f"<b>flow.csv — {entry.get('name')}</b> "
            f"({len(rows)} reaches; sorted by peak flow)")
        self._preview_placeholder.setVisible(False)
        self._flow_title_lbl.setVisible(True)
        self._flow_table.setVisible(True)
