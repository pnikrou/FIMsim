"""Landing page — two top-level category choices."""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
)
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QFont


class LandingWidget(QWidget):
    """Emits category_selected('input_data' | 'flood_mapping')."""
    category_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(60, 40, 60, 40)
        root.setSpacing(0)

        # ── App title ────────────────────────────────────────────────────────
        title = QLabel("FIMsim")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(QFont("Arial", 28, QFont.Weight.Bold))
        title.setStyleSheet("color:#1a365d; border:none;")
        root.addWidget(title)

        subtitle = QLabel("Flood Inundation Model Simulation Tool  ·  v1.0")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setFont(QFont("Arial", 12))
        subtitle.setStyleSheet("color:#718096; border:none; margin-bottom:8px;")
        root.addWidget(subtitle)

        # ── Divider ──────────────────────────────────────────────────────────
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color:#e2e8f0; margin:16px 0 24px 0;")
        root.addWidget(line)

        prompt = QLabel("What would you like to do?")
        prompt.setAlignment(Qt.AlignmentFlag.AlignCenter)
        prompt.setFont(QFont("Arial", 14))
        prompt.setStyleSheet("color:#4a5568; border:none; margin-bottom:20px;")
        root.addWidget(prompt)

        # ── Two category cards ────────────────────────────────────────────────
        cards_row = QHBoxLayout()
        cards_row.setSpacing(32)

        cards_row.addStretch(1)
        cards_row.addWidget(self._make_card(
            key="input_data",
            title="Preparing Input Data",
            description=(
                "Download and process the geospatial data\n"
                "needed to configure a flood model."
            ),
            items=["DEM", "LULC & Manning's n", "Flowline", "Streamflow Data"],
            accent="#276749",
            bg="#f0fff4",
            border="#9ae6b4",
        ))
        cards_row.addWidget(self._make_card(
            key="flood_mapping",
            title="Flood Mapping",
            description=(
                "Build a complete input package for a\n"
                "supported 2D flood simulation model."
            ),
            items=["LISFLOOD-FP", "TRITON", "HEC-RAS"],
            accent="#2b6cb0",
            bg="#ebf8ff",
            border="#90cdf4",
        ))
        cards_row.addStretch(1)

        root.addLayout(cards_row)
        root.addStretch(1)

    # ─────────────────────────────────────────────────────────────────────────

    def _make_card(self, *, key, title, description, items,
                   accent, bg, border):
        card = QFrame()
        card.setFixedWidth(360)
        card.setMinimumHeight(320)
        card.setStyleSheet(
            f"QFrame {{ background:{bg}; border:2px solid {border}; "
            f"border-radius:14px; padding:24px; }}"
        )

        layout = QVBoxLayout(card)
        layout.setSpacing(12)

        # Title
        lbl_title = QLabel(title)
        lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_title.setFont(QFont("Arial", 17, QFont.Weight.Bold))
        lbl_title.setStyleSheet(f"color:{accent}; border:none;")
        layout.addWidget(lbl_title)

        # Description
        lbl_desc = QLabel(description)
        lbl_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_desc.setWordWrap(True)
        lbl_desc.setStyleSheet("color:#4a5568; border:none; font-size:12px;")
        layout.addWidget(lbl_desc)

        # Divider
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color:{border};")
        layout.addWidget(sep)

        # Item list
        for item in items:
            row = QHBoxLayout()
            row.setSpacing(8)
            dot = QLabel("•")
            dot.setFixedWidth(14)
            dot.setStyleSheet(f"color:{accent}; font-size:16px; border:none;")
            lbl = QLabel(item)
            lbl.setFont(QFont("Arial", 12))
            lbl.setStyleSheet("color:#2d3748; border:none;")
            row.addWidget(dot)
            row.addWidget(lbl)
            row.addStretch()
            layout.addLayout(row)

        layout.addStretch()

        # Button
        btn = QPushButton("Select  ▶")
        btn.setMinimumHeight(44)
        btn.setStyleSheet(
            f"font-weight:bold; font-size:13px; padding:10px 20px; "
            f"background:{accent}; color:white; border-radius:8px; border:none;"
        )
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda _checked, k=key: self.category_selected.emit(k))
        layout.addWidget(btn)

        return card
