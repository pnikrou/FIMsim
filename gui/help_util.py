"""Help / About helpers — open the bundled HTML user manual in the browser.

The manual is a self-contained static site under ``<repo>/manual_site/``.
Every mode has its own anchor inside ``index.html`` so a mode's Help button can
jump straight to the matching section.  Opening happens through the operating
system's default browser via ``QDesktopServices`` — no bundled viewer and no
extra dependency.
"""
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout, QMessageBox,
)
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QPixmap, QFont

# ── Paths ─────────────────────────────────────────────────────────────────────
_REPO_ROOT   = Path(__file__).resolve().parent.parent
_MANUAL_HTML = _REPO_ROOT / "manual_site" / "index.html"
_LOGO_PATH   = _MANUAL_HTML.parent / "assets" / "fimsim_logo.png"

# FIM ecosystem web page (SDML).
FIM_ECOSYSTEM_URL = (
    "https://sdml.ua.edu/wp-content/uploads/2026/07/"
    "FIM-Ecosystem-Webpage.html#fimsim"
)

# ── Mode-key → manual anchor ───────────────────────────────────────────────────
# Keys match the mode keys used in gui.app / gui.model_selector.
MANUAL_ANCHORS = {
    None:              "overview",
    "overview":        "overview",
    "aoi":             "aoi-prep",
    "dem":             "dem-mode",
    "lulc_manning":    "lulc-mode",
    "flowline":        "flowline-mode",
    "streamflow":      "streamflow-mode",
    "lisflood":        "lisflood-model",
    "triton":          "triton-model",
    "arc_curve2flood": "arc-model",
    "fimserv":         "fimserv-model",
}


def _anchor_for(mode_key) -> str:
    return MANUAL_ANCHORS.get(mode_key, "overview")


def open_manual(mode_key=None, parent=None) -> None:
    """Open the user manual in the default browser.

    ``mode_key`` selects which section to jump to (see ``MANUAL_ANCHORS``).
    Pass ``None`` (or "overview") to open at the top of the manual.
    """
    if not _MANUAL_HTML.exists():
        QMessageBox.warning(
            parent,
            "Manual not found",
            "The user manual could not be located at:\n"
            f"{_MANUAL_HTML}\n\n"
            "Make sure the 'manual_site' folder is present in the FIMsim "
            "directory (run 'git pull' to fetch the latest files).",
        )
        return

    url = QUrl.fromLocalFile(str(_MANUAL_HTML))
    url.setFragment(_anchor_for(mode_key))
    QDesktopServices.openUrl(url)


def show_about(parent=None) -> None:
    """Show the About dialog (University of Alabama · SDML · FIM ecosystem)."""
    dlg = QDialog(parent)
    dlg.setWindowTitle("About FIMsim")
    dlg.setMinimumWidth(540)

    root = QVBoxLayout(dlg)
    root.setContentsMargins(28, 24, 28, 20)
    root.setSpacing(12)

    # Logo (falls back silently to the text title if the image is missing).
    pix = QPixmap(str(_LOGO_PATH)) if _LOGO_PATH.exists() else QPixmap()
    if not pix.isNull():
        logo = QLabel()
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setPixmap(
            pix.scaledToHeight(96, Qt.TransformationMode.SmoothTransformation)
        )
        root.addWidget(logo)

    title = QLabel("FIMsim")
    title.setAlignment(Qt.AlignmentFlag.AlignCenter)
    title.setFont(QFont("Arial", 20, QFont.Weight.Bold))
    title.setStyleSheet("color:#1a365d;")
    root.addWidget(title)

    sub = QLabel("Flood Inundation Model Simulation Tool  ·  v1.0")
    sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
    sub.setStyleSheet("color:#718096; font-size:12px;")
    root.addWidget(sub)

    body = QLabel(
        "<div style='line-height:1.5;'>"
        "FIMsim is part of the <b>CIROH Flood Inundation Mapping Ecosystem</b> "
        "&mdash; a web-based tool that makes hydraulic flood modeling more "
        "accessible, fast, and user-friendly. It lets users rapidly prepare "
        "model-ready input data and run flood simulations <b>without</b> "
        "installing complex modeling software, configuring technical computing "
        "environments, or owning high-performance hardware such as GPUs. Through "
        "an automated, cloud-enabled workflow it handles both data preparation "
        "and model execution, generating flood inundation maps for "
        "regional-scale study areas in the United States."
        "<br><br>"
        f"&#128279; <a href='{FIM_ECOSYSTEM_URL}'>Learn more about the CIROH "
        "FIM Ecosystem</a>"
        "<br><br>"
        "FIMsim serves two purposes: <b>(1)</b> automated preparation of "
        "individual hydraulic model inputs &mdash; terrain, land cover, river "
        "networks, and streamflow time series; and <b>(2)</b> end-to-end "
        "configuration and cloud execution of complete flood simulations for "
        "<b>LISFLOOD-FP</b> and <b>TRITON</b>. Users only define a study area; "
        "FIMsim downloads, processes, and formats everything automatically. "
        "Standalone desktop installers for macOS and Windows are also available "
        "&mdash; no Python installation and no technical configuration required."
        "<br><br>"
        "Developed at the <b>University of Alabama</b>, "
        "<b>Surface Dynamics Modeling Lab (SDML)</b>."
        "</div>"
    )
    body.setWordWrap(True)
    body.setTextFormat(Qt.TextFormat.RichText)
    body.setOpenExternalLinks(True)
    body.setAlignment(Qt.AlignmentFlag.AlignLeft)
    root.addWidget(body)

    btn_row = QHBoxLayout()
    btn_row.addStretch(1)

    close_btn = QPushButton("Close")
    close_btn.clicked.connect(dlg.accept)
    btn_row.addWidget(close_btn)

    btn_row.addStretch(1)
    root.addLayout(btn_row)

    dlg.exec()
