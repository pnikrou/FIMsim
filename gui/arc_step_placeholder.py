"""Base for ARC-Curve2Flood steps 3–7 (DEM … Config).

Two layout rules these steps follow (per design):
  1. No per-page "Step N — …" heading — the tab bar already labels the step.
  2. Multi-AOI → a per-AOI accordion (one collapsible card per AOI), exactly
     like the LISFLOOD-FP / TRITON steps.  Single AOI → one plain panel.

Real per-AOI data controls get filled into ``_build_card_body`` as each step
is implemented; until then the body shows what the step will produce.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton, QSizePolicy,
)
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QFont


class _AOICard(QFrame):
    """A collapsible card: header (AOI name) toggles the body."""

    def __init__(self, aoi_name: str, body: QWidget, expanded: bool, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "QFrame{background:#ffffff; border:1px solid #cbd5e0; border-radius:8px;}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._expanded = expanded
        self._arrow = "▾" if expanded else "▸"
        self._name = aoi_name
        self._header = QPushButton(f"{self._arrow}  {aoi_name}")
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.setStyleSheet(
            "QPushButton{text-align:left; padding:10px 12px; border:none;"
            "background:transparent; color:#2b6cb0; font-size:13px; font-weight:bold;}"
            "QPushButton:hover{background:#f7fafc;}"
        )
        self._header.clicked.connect(self._toggle)
        lay.addWidget(self._header)

        self._body = body
        self._body.setVisible(expanded)
        lay.addWidget(self._body)

    def _toggle(self):
        self._expanded = not self._expanded
        self._body.setVisible(self._expanded)
        self._header.setText(f"{'▾' if self._expanded else '▸'}  {self._name}")


class ArcStepPlaceholder(QWidget):
    step_completed = pyqtSignal(dict)

    # Subclasses keep passing a `title` (e.g. "Step 3 — DEM") for now; it is
    # intentionally NOT rendered — the tab bar already labels the step.
    def __init__(self, title: str, description: str, produces: str = "",
                 log_fn=print, parent=None):
        super().__init__(parent)
        self._log = log_fn
        self._description = description
        self._produces = produces
        self._ctx = {}
        self._ctx_path = None
        self._features = []
        self._cards = []

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(28, 22, 28, 22)
        self._root.setSpacing(10)
        self._rebuild([])   # initial single-AOI panel

    # ── content ──────────────────────────────────────────────────────────────

    def _build_card_body(self, aoi_name: str = "") -> QWidget:
        """The per-AOI body.  Overridden by real steps; here it describes
        what the step will produce."""
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(14, 4, 14, 14)
        v.setSpacing(6)
        d = QLabel(self._description)
        d.setWordWrap(True)
        d.setStyleSheet("color:#4a5568; font-size:13px; border:none;")
        v.addWidget(d)
        if self._produces:
            p = QLabel(f"★  Produces:  {self._produces}")
            p.setWordWrap(True)
            p.setStyleSheet("color:#2d3748; font-size:12px; border:none;")
            v.addWidget(p)
        note = QLabel("⚙  This step is being built — coming next.")
        note.setStyleSheet("color:#a0aec0; font-size:12px; border:none; margin-top:6px;")
        v.addWidget(note)
        return w

    # ── (re)build the single-panel-vs-accordion layout ─────────────────────────

    def _clear(self):
        while self._root.count():
            item = self._root.takeAt(0)
            wdg = item.widget()
            if wdg is not None:
                wdg.setParent(None)   # detach now so it stops rendering
                wdg.deleteLater()
        self._cards = []

    def _rebuild(self, features):
        self._clear()
        self._features = list(features or [])

        if len(self._features) <= 1:
            # Single AOI (or none yet): one plain panel, no accordion chrome.
            self._root.addWidget(self._build_card_body())
            self._root.addStretch()
            return

        intro = QLabel(f"{len(self._features)} AOIs — one card each:")
        intro.setStyleSheet("color:#4a5568; font-size:12px; border:none;")
        self._root.addWidget(intro)
        for i, feat in enumerate(self._features):
            name = getattr(feat, "name", None) or f"AOI {i + 1}"
            card = _AOICard(name, self._build_card_body(name), expanded=(i == 0))
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._cards.append(card)
            self._root.addWidget(card)
        self._root.addStretch()

    # ── step interface ─────────────────────────────────────────────────────────

    def set_context(self, ctx_path, ctx):
        self._ctx_path = ctx_path
        self._ctx = ctx or {}
        feats = self._ctx.get("aoi_features", []) or []
        if len(feats) != len(self._features):
            self._rebuild(feats)

    def reset(self):
        self._rebuild([])
