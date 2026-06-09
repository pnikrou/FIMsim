"""Per-AOI Manning configuration card — collapsible accordion row.

Used by the Manning step when the user has confirmed multiple AOIs.
Each card shows:

  Collapsed view:
      ▶  <AOI name>      [○ Fixed]  [○ Varying]   <status>     [Edit]
  Expanded view:
      ▼  <AOI name>      [○ Fixed]  [○ Varying]                [Collapse]
      ┌─ ManningConfigPanel ─────────────────────────────────────────┐
      │   (Mode + sub-source + table, identical to single-AOI form)  │
      └──────────────────────────────────────────────────────────────┘

The parent (StepManningWidget) listens to ``expand_requested`` to enforce
the "only one expanded at a time" accordion rule.  ``config_changed`` is
forwarded so the parent can re-evaluate whether the global Run button
should appear (every card must be ready).
"""
from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
)
from PyQt6.QtCore import pyqtSignal

from gui.manning_config_panel import ManningConfigPanel


class AOIManningCard(QFrame):
    expand_requested = pyqtSignal(object)   # passes self
    config_changed   = pyqtSignal(object)   # passes self

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

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 2)
        outer.setSpacing(6)

        # ── Header row (visible in both states) ──
        # No inline Fixed/Varying radios here — the radios live inside the
        # embedded panel.  Showing them in both places confused users into
        # thinking they had to pick twice.
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

        # ── Embedded ManningConfigPanel (visible only when expanded) ──
        # Starts with no Fixed/Varying selected — the user must pick.
        self._panel = ManningConfigPanel(self)
        self._panel.setVisible(False)
        self._panel.config_changed.connect(self._forward_config_changed)
        self._panel.mode_changed.connect(self._on_panel_mode_changed)
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

    # ── panel mode-changed signal — refresh the collapsed status line ────

    def _on_panel_mode_changed(self, _mode: str):
        self._refresh_status()

    # ── status line on the collapsed card ─────────────────────────────────────

    def _refresh_status(self):
        m = self._panel.mode()
        if not m:
            self._status_lbl.setText(
                "<i style='color:#888;'>not configured</i>"
            )
            return
        if m == "fixed":
            cfg = self._panel.get_config()
            self._status_lbl.setText(
                f"Fixed n = {cfg['fixed_value']:.4f}"
                + (" " if self._panel.is_ready() else "")
            )
        else:
            cfg = self._panel.get_config()
            src = cfg.get("source", "")
            if not src:
                self._status_lbl.setText("Varying — pick source")
            elif src == "download":
                ds = "NLCD" if cfg["dataset_idx"] == 0 else "Sentinel-2"
                self._status_lbl.setText(
                    f"Varying — {ds} {cfg['year']} "
                )
            else:
                if self._panel.is_ready():
                    self._status_lbl.setText("Varying — uploaded LULC ")
                else:
                    self._status_lbl.setText(
                        "<span style='color:#c53030;'>Varying — analyse the raster</span>"
                    )

    # ── forward config changes ────────────────────────────────────────────────

    def _forward_config_changed(self):
        self._refresh_status()
        self.config_changed.emit(self)

    # ── public proxies ────────────────────────────────────────────────────────

    def panel(self) -> ManningConfigPanel:
        return self._panel

    def is_ready(self) -> bool:
        return self._panel.is_ready()

    def get_config(self) -> dict:
        return self._panel.get_config()

    def set_config(self, cfg: dict):
        self._panel.set_config(cfg)
        self._refresh_status()
