"""Home screen — two category cards; clicking one reveals its mode options below."""
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QFrame,
)
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QFont, QPixmap

# Logo lives in <repo>/assets/fimsim_logo.png (this file is in <repo>/gui/).
_LOGO_PATH = Path(__file__).resolve().parent.parent / "assets" / "fimsim_logo.png"


# ── Palette ───────────────────────────────────────────────────────────────────
_GREEN_DARK   = "#276749"
_GREEN_BG     = "#f0fff4"
_GREEN_BORDER = "#9ae6b4"
_GREEN_SEL    = "#c6f6d5"   # selected background

_BLUE_DARK    = "#2b6cb0"
_BLUE_BG      = "#ebf8ff"
_BLUE_BORDER  = "#90cdf4"
_BLUE_SEL     = "#bee3f8"   # selected background

# ── Mode definitions ──────────────────────────────────────────────────────────
_INPUT_MODES = [
    {"title": "DEM",              "mode_key": "dem",          "desc": "3DEP · HAND · TIF / ASC"},
    {"title": "LULC & Manning",   "mode_key": "lulc_manning", "desc": "NLCD · Sentinel-2 · Manning table"},
    {"title": "Flowline",         "mode_key": "flowline",     "desc": "NHD flowlines · USGS gages"},
    {"title": "Streamflow Data",  "mode_key": "streamflow",   "desc": "NWM Retro / Forecast · USGS"},
]

_MODEL_MODES = [
    {"title": "LISFLOOD-FP", "mode_key": "lisflood", "desc": "7-step wizard → .par .bci .bdy"},
    {"title": "TRITON",      "mode_key": "triton",   "desc": "7-step wizard → .cfg .extbc .hyg"},
    {"title": "ARC-Curve2Flood", "mode_key": "arc_curve2flood",
     "desc": "7-step wizard → NenCarta .json (rapid flood mapping)"},
    {"title": "OWP HAND-FIM", "subtitle": "(FIMserv)", "mode_key": "fimserv",
     "desc": "OWP HAND · AOI → HUC8 → FIM"},
]


class ModelSelectorWidget(QWidget):
    """Emits mode_selected(mode_key) when a mode Start button is clicked."""
    mode_selected  = pyqtSignal(str)
    model_selected = mode_selected          # backward-compat alias

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selected_category = None      # "input_data" | "flood_mapping" | None
        self._setup_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(48, 32, 48, 24)
        root.setSpacing(0)

        # Logo — shown when assets/fimsim_logo.png exists; the image already
        # contains the "FIMsim" wordmark, so we hide the text title in that
        # case and only fall back to text when the file is missing.
        logo_shown = False
        pix = QPixmap(str(_LOGO_PATH)) if _LOGO_PATH.exists() else QPixmap()
        if not pix.isNull():
            logo = QLabel()
            logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
            logo.setPixmap(
                pix.scaledToHeight(
                    140,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            logo.setStyleSheet("border:none; margin-bottom:4px;")
            root.addWidget(logo)
            logo_shown = True

        # App title — fallback wordmark when the logo image is unavailable.
        title = QLabel("FIMsim")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(QFont("Arial", 26, QFont.Weight.Bold))
        title.setStyleSheet("color:#1a365d; border:none;")
        title.setVisible(not logo_shown)
        root.addWidget(title)

        sub = QLabel("Flood Inundation Model Simulation Tool  ·  v1.0")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet("color:#718096; font-size:12px; border:none; margin-bottom:4px;")
        root.addWidget(sub)

        # Divider
        line = QFrame(); line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color:#e2e8f0; margin:14px 0 20px 0;")
        root.addWidget(line)

        prompt = QLabel("Select a category to get started:")
        prompt.setAlignment(Qt.AlignmentFlag.AlignCenter)
        prompt.setStyleSheet("color:#4a5568; font-size:13px; border:none; margin-bottom:16px;")
        root.addWidget(prompt)

        # ── Two top-level category cards ──────────────────────────────────────
        cat_row = QHBoxLayout()
        cat_row.setSpacing(28)
        cat_row.addStretch(1)
        self._card_input  = self._make_category_card(
            key="input_data",
            title="Preparing Input Data",
            description="Download and process geospatial inputs\n(DEM, LULC, flowlines, streamflow)",
            accent=_GREEN_DARK, bg=_GREEN_BG, border=_GREEN_BORDER,
        )
        self._card_model  = self._make_category_card(
            key="flood_mapping",
            title="Flood Mapping",
            description="Generate complete input packages\nfor a 2D flood simulation model",
            accent=_BLUE_DARK, bg=_BLUE_BG, border=_BLUE_BORDER,
        )
        cat_row.addWidget(self._card_input)
        cat_row.addWidget(self._card_model)
        cat_row.addStretch(1)
        root.addLayout(cat_row)

        # ── Sub-options panel (hidden until a category is clicked) ─────────────
        # Wrapper keeps a fixed vertical slot so layout doesn't jump
        self._sub_panel = QWidget()
        sub_layout = QVBoxLayout(self._sub_panel)
        sub_layout.setContentsMargins(0, 20, 0, 0)
        sub_layout.setSpacing(10)

        # Arrow label + category title
        self._arrow_lbl = QLabel()
        self._arrow_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._arrow_lbl.setStyleSheet("color:#718096; font-size:12px; border:none;")
        sub_layout.addWidget(self._arrow_lbl)

        # Mode cards row (rebuilt on each selection)
        self._mode_row_widget = QWidget()
        self._mode_row_layout = QHBoxLayout(self._mode_row_widget)
        self._mode_row_layout.setSpacing(16)
        self._mode_row_layout.setContentsMargins(0, 0, 0, 0)
        sub_layout.addWidget(self._mode_row_widget)

        self._sub_panel.setVisible(False)
        root.addWidget(self._sub_panel)

        root.addStretch(1)

    # ── Category card (the big clickable tiles at the top) ────────────────────

    def _make_category_card(self, *, key, title, description, accent, bg, border):
        card = QFrame()
        card.setFixedWidth(340)
        card.setMinimumHeight(130)
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        card.setObjectName(f"cat_{key}")
        card.setProperty("cat_key", key)
        card.setProperty("accent", accent)
        card.setProperty("bg", bg)
        card.setProperty("border", border)
        self._apply_card_style(card, selected=False)

        layout = QVBoxLayout(card)
        layout.setSpacing(6)
        layout.setContentsMargins(20, 16, 20, 16)

        lbl_title = QLabel(title)
        lbl_title.setFont(QFont("Arial", 15, QFont.Weight.Bold))
        lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_title.setStyleSheet(f"color:{accent}; border:none;")
        layout.addWidget(lbl_title)

        lbl_desc = QLabel(description)
        lbl_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_desc.setWordWrap(True)
        lbl_desc.setStyleSheet("color:#4a5568; font-size:11px; border:none;")
        layout.addWidget(lbl_desc)

        # Make the whole card clickable via mousePressEvent on the frame
        card.mousePressEvent = lambda _ev, k=key: self._on_category_clicked(k)

        return card

    def _apply_card_style(self, card, selected: bool):
        bg     = card.property("bg")
        border = card.property("border")
        accent = card.property("accent")
        if selected:
            # Highlighted: stronger border, slightly darker bg
            card.setStyleSheet(
                f"QFrame {{ background:{bg}; border:3px solid {accent}; "
                f"border-radius:12px; }}"
            )
        else:
            card.setStyleSheet(
                f"QFrame {{ background:{bg}; border:2px solid {border}; "
                f"border-radius:12px; }}"
            )

    # ── Category click → show sub-options ────────────────────────────────────

    def _on_category_clicked(self, key: str):
        # Toggle: clicking the already-selected category collapses it
        if self._selected_category == key:
            self._selected_category = None
            self._apply_card_style(self._card_input,  selected=False)
            self._apply_card_style(self._card_model,  selected=False)
            self._sub_panel.setVisible(False)
            return

        self._selected_category = key

        # Update card highlight states
        self._apply_card_style(self._card_input, selected=(key == "input_data"))
        self._apply_card_style(self._card_model, selected=(key == "flood_mapping"))

        # Rebuild the mode cards row
        self._rebuild_mode_row(key)
        self._sub_panel.setVisible(True)

    def _rebuild_mode_row(self, category: str):
        # Clear old mode cards
        while self._mode_row_layout.count():
            item = self._mode_row_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        modes  = _INPUT_MODES if category == "input_data" else _MODEL_MODES
        accent = _GREEN_DARK  if category == "input_data" else _BLUE_DARK
        bg     = _GREEN_BG    if category == "input_data" else _BLUE_BG
        border = _GREEN_BORDER if category == "input_data" else _BLUE_BORDER
        label  = "Preparing Input Data" if category == "input_data" else "Flood Mapping"

        self._arrow_lbl.setText(f"▼  {label}")
        self._arrow_lbl.setStyleSheet(
            f"color:{accent}; font-size:12px; font-weight:bold; border:none;"
        )

        self._mode_row_layout.addStretch(1)
        for m in modes:
            self._mode_row_layout.addWidget(
                self._make_mode_card(m, accent=accent, bg=bg, border=border)
            )
        self._mode_row_layout.addStretch(1)

    # ── Small mode card ───────────────────────────────────────────────────────

    def _make_mode_card(self, mode_data: dict, *, accent, bg, border):
        card = QFrame()
        card.setMinimumWidth(200)
        card.setMaximumWidth(280)
        card.setStyleSheet(
            f"QFrame {{ background:{bg}; border:2px solid {border}; "
            f"border-radius:10px; padding:12px; }}"
        )
        layout = QVBoxLayout(card)
        layout.setSpacing(6)

        lbl_title = QLabel(mode_data["title"])
        lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_title.setFont(QFont("Arial", 13, QFont.Weight.Bold))
        lbl_title.setStyleSheet(f"color:{accent}; border:none;")
        layout.addWidget(lbl_title)

        lbl_sub = QLabel(mode_data.get("subtitle", ""))
        lbl_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_sub.setStyleSheet(
            f"color:{accent}; font-size:11px; font-style:italic; border:none;"
        )
        layout.addWidget(lbl_sub)

        lbl_desc = QLabel(mode_data.get("desc", ""))
        lbl_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_desc.setWordWrap(True)
        lbl_desc.setStyleSheet("color:#555; font-size:11px; border:none;")
        layout.addWidget(lbl_desc)

        layout.addStretch()

        btn = QPushButton("Start  ▶")
        btn.setStyleSheet(
            f"font-weight:bold; padding:8px 14px; font-size:12px; "
            f"background:{accent}; color:white; border-radius:6px; border:none;"
        )
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        k = mode_data["mode_key"]
        btn.clicked.connect(lambda _checked, mk=k: self.mode_selected.emit(mk))
        layout.addWidget(btn)

        return card
