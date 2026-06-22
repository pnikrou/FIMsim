"""Base for ARC-Curve2Flood steps 3–7 (DEM … Config).

Two layout rules these steps follow (per design):
  1. No per-page "Step N — …" heading — the tab bar already labels the step.
  2. Multi-AOI → a per-AOI accordion that looks exactly like the
     LISFLOOD-FP / TRITON cards (collapsible ``#card`` frame, caret, bold AOI
     name, status line, Edit/Done toggle, single card expanded at a time).
     Single AOI → one plain panel.

Real per-AOI data controls get filled into ``_build_card_body`` as each step
is implemented; until then the body shows what the step will produce.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton, QSizePolicy,
)
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QFont


class _AOICard(QFrame):
    """Collapsible per-AOI card — visually identical to AOIDEMCard etc."""

    expand_requested = pyqtSignal(object)

    EXPANDED_STYLE = (
        "QFrame#card { background:#f9fafb; border:2px solid #a0aec0; "
        "border-radius:6px; padding:8px; }"
    )
    COLLAPSED_STYLE = (
        "QFrame#card { background:#f9fafb; border:1px solid #e2e8f0; "
        "border-radius:6px; padding:6px; }"
    )

    def __init__(self, aoi_name: str, body: QWidget, status_text: str = "",
                 parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self._aoi_name = aoi_name
        self._expanded = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 2)
        outer.setSpacing(6)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)

        self._caret = QLabel("▶")
        self._caret.setFixedWidth(14)
        self._caret.setStyleSheet("color:#4a5568; font-weight:bold;")
        header.addWidget(self._caret)

        self._name_lbl = QLabel(f"<b>{aoi_name}</b>")
        self._name_lbl.setStyleSheet("color:#2d3748;")
        header.addWidget(self._name_lbl)
        header.addStretch()

        self._status_lbl = QLabel(status_text)
        self._status_lbl.setStyleSheet("color:#666; font-size:11px;")
        header.addWidget(self._status_lbl)

        self._toggle_btn = QPushButton("Edit")
        self._toggle_btn.setFixedWidth(80)
        self._toggle_btn.clicked.connect(self._on_toggle_clicked)
        header.addWidget(self._toggle_btn)

        outer.addLayout(header)

        self._body = body
        self._body.setVisible(False)
        outer.addWidget(self._body)

        self.setStyleSheet(self.COLLAPSED_STYLE)

    def is_expanded(self) -> bool:
        return self._expanded

    def expand(self):
        if self._expanded:
            return
        self._expanded = True
        self._body.setVisible(True)
        self._toggle_btn.setText("Done")
        self._caret.setText("▼")
        self.setStyleSheet(self.EXPANDED_STYLE)

    def collapse(self):
        if not self._expanded:
            return
        self._expanded = False
        self._body.setVisible(False)
        self._toggle_btn.setText("Edit")
        self._caret.setText("▶")
        self.setStyleSheet(self.COLLAPSED_STYLE)

    def _on_toggle_clicked(self):
        if self._expanded:
            self.collapse()
        else:
            self.expand_requested.emit(self)


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

    def _count_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color:#2d3748; font-size:12px; border:none;")
        return lbl

    def _rebuild(self, features):
        self._clear()
        self._features = list(features or [])
        n = len(self._features)

        if n <= 1:
            # Single AOI (or none yet): one plain panel, no accordion chrome.
            if n == 1:
                self._root.addWidget(self._count_label("<b>1</b> AOI confirmed."))
            self._root.addWidget(self._build_card_body())
            self._root.addStretch()
            return

        self._root.addWidget(self._count_label(
            f"<b>{n}</b> AOI(s) confirmed — click an AOI to expand its settings."
        ))
        for i, feat in enumerate(self._features):
            name = getattr(feat, "name", None) or f"AOI {i + 1}"
            card = _AOICard(name, self._build_card_body(name))
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            card.expand_requested.connect(self._on_expand_requested)
            self._cards.append(card)
            self._root.addWidget(card)
        self._root.addStretch()
        if self._cards:
            self._cards[0].expand()   # first card open, like the other models

    def _on_expand_requested(self, card):
        """Single-expand accordion: open the clicked card, close the rest."""
        for c in self._cards:
            if c is card:
                c.expand()
            else:
                c.collapse()

    # ── step interface ─────────────────────────────────────────────────────────

    def set_context(self, ctx_path, ctx):
        self._ctx_path = ctx_path
        self._ctx = ctx or {}
        feats = self._ctx.get("aoi_features", []) or []
        if len(feats) != len(self._features):
            self._rebuild(feats)

    def reset(self):
        self._rebuild([])
