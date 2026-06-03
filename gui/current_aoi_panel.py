"""Reusable 'Current AOI' info panel for standalone-mode run pages.

The mode widget keeps a list of AOIFeatureInfo for the run.  Whenever the
status banner sees a `▶ Running [N/T]: …` line, the mode calls
`update_for_index(N - 1)` and the panel renders the rich info for that AOI:
state, area, centroid, HUC8, main river, USGS gages.
"""
import re
from pathlib import Path
from typing import List, Optional

from PyQt6.QtWidgets import QGroupBox, QVBoxLayout, QLabel
from PyQt6.QtCore import pyqtSlot

from core.multi_aoi import AOIFeatureInfo


_RUNNING_RE = re.compile(r"^▶\s+Running\s+\[(\d+)/\d+\]")


class CurrentAOIPanel(QGroupBox):
    """A QGroupBox titled "Current AOI" that auto-updates from worker logs."""

    def __init__(self, parent=None):
        super().__init__("Current AOI", parent)
        self._features: List[AOIFeatureInfo] = []
        self._lbl = QLabel("(no AOI being processed)")
        self._lbl.setWordWrap(True)
        self._lbl.setStyleSheet("color:#2d3748; font-size:11px; padding:2px 0px;")
        v = QVBoxLayout(self)
        v.addWidget(self._lbl)
        self.setVisible(False)

    # ── public API ────────────────────────────────────────────────────────────

    def set_features(self, features: List[AOIFeatureInfo]):
        """Set the list of AOIs that will be processed (in order)."""
        self._features = list(features)

    def reset(self):
        self._lbl.setText("(no AOI being processed)")
        self.setVisible(False)

    def update_for_index(self, idx: int):
        """Show the AOI at the given 0-based index."""
        if idx < 0 or idx >= len(self._features):
            return
        f = self._features[idx]
        self._lbl.setText(self._render(idx, f))
        self.setVisible(True)

    def consume_log_line(self, msg: str):
        """If the message is a `▶ Running [N/T]:` marker, update the panel."""
        m = _RUNNING_RE.match(msg)
        if m:
            try:
                self.update_for_index(int(m.group(1)) - 1)
            except Exception:
                pass

    # ── rendering ─────────────────────────────────────────────────────────────

    def _render(self, idx: int, f: AOIFeatureInfo) -> str:
        # Lazy/optional fields render with sensible placeholders
        river   = f.river_name or "—"
        huc6 = (
            ", ".join(f.huc6_codes) if f.huc6_codes
            else ("<i>(not fetched)</i>" if f.huc6_codes is None else "—")
        )
        huc8 = (
            ", ".join(f.huc8_codes) if f.huc8_codes
            else ("<i>(not fetched)</i>" if f.huc8_codes is None else "—")
        )
        if f.usgs_gages:
            top = "; ".join(
                f"{g.get('site_no', '?')}"
                + (f" ({g.get('station_nm','')})" if g.get('station_nm') else "")
                for g in f.usgs_gages[:5]
            )
            extra = f" … +{len(f.usgs_gages) - 5}" if len(f.usgs_gages) > 5 else ""
            gages = f"{len(f.usgs_gages)}: {top}{extra}"
        elif f.usgs_gages is None:
            gages = "<i>(not fetched)</i>"
        else:
            gages = "none"

        return (
            f"<b>AOI #{idx + 1}: {f.name}</b>  "
            f"<span style='color:#888;'>(from {Path(f.source_file).name})</span><br>"
            f"<b>Area:</b> {f.area_km2:.2f} km²  "
            f"&nbsp;|&nbsp;  <b>Centroid:</b> {f.centroid_lon:.4f}°, "
            f"{f.centroid_lat:.4f}°<br>"
            f"<b>State:</b> {f.state_name or '—'} "
            f"({f.state_abbr or '—'})<br>"
            f"<b>HUC6:</b> {huc6}  &nbsp;|&nbsp;  <b>HUC8:</b> {huc8}<br>"
            f"<b>Main river:</b> {river}<br>"
            f"<b>USGS gages:</b> {gages}<br>"
            f"<b>Folder:</b> <code>{f.folder_path}</code>"
        )
