"""Welcome / mode-selector screen — 5 cards for the supported modes."""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QFrame,
)
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QFont


# Two shared palettes — every "standalone tool" card on the top row uses
# _TOP_*; every "full model package" card on the bottom row uses _BOT_*.
# This is what gives each row a consistent shade.
_TOP_BTN, _TOP_BG, _TOP_BORDER = "#276749", "#f0fff4", "#9ae6b4"   # green
_BOT_BTN, _BOT_BG, _BOT_BORDER = "#2b6cb0", "#ebf8ff", "#90cdf4"   # blue


# Card metadata: each entry is a dict with title, mode_key, colours,
# 'rows' (key:value summary lines), and 'footer' (italic note about scope).
_CARDS = [
    # ── Top row: standalone preprocessing tools (amber shade) ────────────
    {
        "title": "DEM", "mode_key": "dem",
        "btn": _TOP_BTN, "bg": _TOP_BG, "border": _TOP_BORDER,
        "rows": [
            ("Source",   "3DEP (USGS) · HAND (TACC)"),
            ("Cell size","User-defined"),
            ("Format",   "TIF · GPKG · ASC"),
        ],
        "footer": "Works on multiple AOIs and multi-feature shapefiles.",
    },
    {
        "title": "LULC & Manning's n", "mode_key": "lulc_manning",
        "btn": _TOP_BTN, "bg": _TOP_BG, "border": _TOP_BORDER,
        "rows": [
            ("Source",   "NLCD (USGS) · Sentinel-2 (Esri)"),
            ("Cell size","User-defined"),
            ("Format",   "TIF · GPKG · ASC · SHP"),
            ("Manning",  "Min/Max bounds + editable Avg"),
        ],
        "footer": "Works on multiple AOIs and multi-feature shapefiles.",
    },
    {
        "title": "Flowline", "mode_key": "flowline",
        "btn": _TOP_BTN, "bg": _TOP_BG, "border": _TOP_BORDER,
        "rows": [
            ("Flowlines", "NHD main river or all reaches → SHP"),
            ("Gages",     "USGS gages in AOI → CSV"),
            ("IDs",       "Feature IDs + stream order → CSV"),
        ],
    },
    {
        "title": "Streamflow Data", "mode_key": "streamflow",
        "btn": _TOP_BTN, "bg": _TOP_BG, "border": _TOP_BORDER,
        "rows": [
            ("Sources",   "NWM Retrospective · NWM Forecast · USGS Gage"),
            ("Input",     "Feature ID(s) or gage number(s), or CSV file"),
            ("Output",    "Discharge time series CSV per feature"),
        ],
    },
    # ── Bottom row: full model-package workflows (blue shade) ────────────
    # Each card uses the SAME structure: a single "Outputs" line.  This is
    # what makes the three look like siblings.
    {
        "title": "HEC-RAS Files", "mode_key": "hecras",
        "btn": _BOT_BTN, "bg": _BOT_BG, "border": _BOT_BORDER,
        "rows": [
            ("Outputs", "DEM (TIF) · Manning (SHP) · "
                        "Flowline (SHP) · Geometry polygon (SHP)"),
        ],
        "footer": "Full input set for a HEC-RAS model.",
    },
    {
        "title": "LISFLOOD-FP Files", "mode_key": "lisflood",
        "btn": _BOT_BTN, "bg": _BOT_BG, "border": _BOT_BORDER,
        "rows": [
            ("Outputs", "*.par · *.bci · *.bdy · "
                        "ASCII grids (DEM and Manning)"),
        ],
        "footer": "Full input set for a LISFLOOD-FP model.",
    },
    {
        "title": "TRITON Files", "mode_key": "triton",
        "btn": _BOT_BTN, "bg": _BOT_BG, "border": _BOT_BORDER,
        "rows": [
            ("Outputs", "*.cfg · *.extbc · *.hyg · "
                        "ASCII grids (DEM and Manning)"),
        ],
        "footer": "Full input set for a TRITON model.",
    },
]


class ModelSelectorWidget(QWidget):
    """Emits mode_selected('dem' | 'lulc_manning' | 'flowline' | 'streamflow' | 'hecras' | 'lisflood' | 'triton')."""
    mode_selected = pyqtSignal(str)

    # Backward-compat alias for existing code that imports model_selected
    model_selected = mode_selected

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 20, 30, 20)
        root.setSpacing(16)

        # 7 cards in a 4+3 grid:
        #   top row:    DEM, LULC & Manning, Flowline, Streamflow Data
        #   bottom row: HEC-RAS, LISFLOOD-FP, TRITON
        grid = QGridLayout()
        grid.setSpacing(16)
        for i, card in enumerate(_CARDS[:4]):
            grid.addWidget(self._make_card(card), 0, i)
        for i, card in enumerate(_CARDS[4:7]):
            grid.addWidget(self._make_card(card), 1, i)
        root.addLayout(grid)

        root.addStretch(1)

    def _make_card(self, card_data: dict):
        title       = card_data["title"]
        mode_key    = card_data["mode_key"]
        btn_color   = card_data["btn"]
        bg_color    = card_data["bg"]
        border_color = card_data["border"]
        rows        = card_data.get("rows", [])
        footer      = card_data.get("footer", "")

        card = QFrame()
        card.setMinimumWidth(280)
        card.setMaximumWidth(380)
        card.setStyleSheet(
            f"QFrame {{ background:{bg_color}; border:2px solid {border_color}; "
            f"border-radius:10px; padding:10px; }}"
        )
        layout = QVBoxLayout(card)
        layout.setSpacing(8)

        # Title
        lbl_title = QLabel(title)
        lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_title.setFont(QFont("Arial", 15, QFont.Weight.Bold))
        lbl_title.setStyleSheet(f"color:{btn_color}; border:none;")
        layout.addWidget(lbl_title)

        # Key:value summary rows in a small inner box
        for key, val in rows:
            html = (
                f"<span style='color:#444; font-weight:bold;'>{key}:</span> "
                f"<span style='color:#222;'>{val}</span>"
            )
            lbl = QLabel(html)
            lbl.setStyleSheet("border:none; font-size:11px;")
            lbl.setWordWrap(True)
            layout.addWidget(lbl)

        layout.addStretch()

        btn = QPushButton("Start  ▶")
        btn.setStyleSheet(
            f"font-weight:bold; padding:10px 16px; font-size:12px; "
            f"background:{btn_color}; color:white; border-radius:6px; border:none;"
        )
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda _checked, k=mode_key: self.mode_selected.emit(k))
        layout.addWidget(btn)

        return card
