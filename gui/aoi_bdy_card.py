"""Per-AOI BDY configuration card — collapsible accordion row."""
from pathlib import Path

from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
)
from PyQt6.QtCore import pyqtSignal

from gui.bdy_config_panel import BDYConfigPanel


class AOIBDYCard(QFrame):
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
        self._apply_collapsed_style()
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

        self._panel = BDYConfigPanel(self)
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
        self._apply_expanded_style()

    def collapse(self):
        if not self._expanded:
            return
        self._expanded = False
        self._panel.setVisible(False)
        self._toggle_btn.setText("Edit")
        self._caret.setText("▶")
        self._apply_collapsed_style()
        self._refresh_status()

    def _on_toggle_clicked(self):
        if self._expanded:
            self.collapse()
        else:
            self.expand_requested.emit(self)

    def _apply_expanded_style(self):
        self.setStyleSheet(self.EXPANDED_STYLE)

    def _apply_collapsed_style(self):
        self.setStyleSheet(self.COLLAPSED_STYLE)

    # ── status line ───────────────────────────────────────────────────────────

    def _refresh_status(self):
        cfg = self._panel.get_config()
        src = cfg["bdy_source"]
        ivl = cfg.get("interval_hours", 1.0)
        if not src:
            self._status_lbl.setText(
                "<i style='color:#888;'>not configured</i>"
            )
            return
        if src in ("nwm", "nwm_retro"):
            self._status_lbl.setText(
                f"<i>Source:</i> NWM Retrospective &nbsp;·&nbsp; "
                f"<i>Interval:</i> {ivl:g}h"
            )
        elif src == "nwm_forecast":
            self._status_lbl.setText(
                f"<i>Source:</i> NWM Forecast &nbsp;·&nbsp; "
                f"<i>Interval:</i> {ivl:g}h"
            )
        elif src == "usgs":
            gage = cfg.get("gage_id") or "—"
            ok = bool(cfg.get("gage_id"))
            colour = "#22543d" if ok else "#c53030"
            self._status_lbl.setText(
                f"<i>Source:</i> USGS &nbsp;·&nbsp; "
                f"<span style='color:{colour};'>Gage {gage}</span> &nbsp;·&nbsp; "
                f"<i>Interval:</i> {ivl:g}h"
            )
        elif src == "csv":
            file_str = (Path(cfg['file_path']).name
                        if cfg.get('file_path') else "—")
            ok = bool(cfg.get('file_path'))
            colour = "#22543d" if ok else "#c53030"
            self._status_lbl.setText(
                f"<i>Source:</i> CSV &nbsp;·&nbsp; "
                f"<span style='color:{colour};'>"
                f"<code>{file_str}</code></span> &nbsp;·&nbsp; "
                f"<i>Interval:</i> {ivl:g}h"
            )

    def _forward_config_changed(self):
        self._refresh_status()
        self.config_changed.emit(self)

    # ── public proxies ────────────────────────────────────────────────────────

    def panel(self) -> BDYConfigPanel:
        return self._panel

    def is_ready(self) -> bool:
        return self._panel.is_ready()

    def get_config(self) -> dict:
        return self._panel.get_config()

    def set_config(self, cfg: dict):
        self._panel.set_config(cfg)
        self._refresh_status()
