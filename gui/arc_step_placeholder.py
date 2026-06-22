"""Placeholder base for ARC-Curve2Flood steps that are still being built.

Implements the small interface the tab controller in gui/app.py expects of
every workflow step — a ``step_completed`` signal, ``set_context`` and
``reset`` — so the 7-tab ARC workflow renders and navigates while the real
data-preparation logic for each step is filled in.
"""
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QFrame
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QFont


class ArcStepPlaceholder(QWidget):
    step_completed = pyqtSignal(dict)

    def __init__(self, title: str, description: str, produces: str = "",
                 log_fn=print, parent=None):
        super().__init__(parent)
        self._log = log_fn
        self._ctx = {}
        self._ctx_path = None

        root = QVBoxLayout(self)
        root.setContentsMargins(40, 32, 40, 32)
        root.setSpacing(6)

        t = QLabel(title)
        t.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        t.setStyleSheet("color:#2b6cb0; border:none;")
        root.addWidget(t)

        d = QLabel(description)
        d.setWordWrap(True)
        d.setStyleSheet("color:#4a5568; font-size:13px; border:none; margin-top:6px;")
        root.addWidget(d)

        if produces:
            p = QLabel(f"★  Produces:  {produces}")
            p.setWordWrap(True)
            p.setStyleSheet("color:#2d3748; font-size:12px; border:none; margin-top:10px;")
            root.addWidget(p)

        line = QFrame(); line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color:#e2e8f0; margin:16px 0;")
        root.addWidget(line)

        note = QLabel("⚙  This step is being built — coming next.")
        note.setStyleSheet("color:#a0aec0; font-size:12px; border:none;")
        root.addWidget(note)
        root.addStretch()

    def set_context(self, ctx_path, ctx):
        self._ctx_path = ctx_path
        self._ctx = ctx or {}

    def reset(self):
        pass
