"""ARC-Curve2Flood Step 7 — Run.

Multi-AOI controller matching the other steps:
  * 1 AOI   → one ArcRunConfigPanel embedded directly.
  * >1 AOIs → accordion of AOIArcRunCard widgets (Edit / Remove chrome)
              with "Apply current AOI's settings to all".

Per AOI it assembles the inputs (DEM, LandCover, Manning table, streams, flow
file) into the layout ARC expects, then runs the full pipeline in-app:
Process_ARC_Geospatial_Data → Arc().run() → Curve2Flood.  Clicking a finished
AOI previews its flood-inundation raster.

All options are real switches of the two tools (verified in their source):
  * mapper                        — Curve2Flood flood-spreading algorithm
  * Make_Output_GPKG              — also save the flood map as GeoPackage
  * use_land_cover_to_find_banks  — ARC: find channel banks from land cover
                                    (vs the flat-surface approach)
  * bathy_use_banks               — ARC: use bank elevations to estimate
                                    bathymetry
"""
import re
from pathlib import Path
from typing import List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QGroupBox, QProgressBar, QScrollArea, QStackedWidget, QMessageBox,
    QComboBox, QCheckBox,
)
from PyQt6.QtCore import pyqtSignal, Qt

from core.arc_orchestrate import run_arc_curve2flood_for_all_aois
from gui.worker import Worker
from gui.run_button import set_running, set_ready
from gui.triton_raster_preview import RasterPreviewCanvas

_RUN_STEP_RE = re.compile(r"^▶\s+ARC-Curve2Flood\s+\[(\d+)/(\d+)\]")
_RUN_DONE_RE = re.compile(r"^✓\s+ARC-Curve2Flood\s+\[(\d+)/(\d+)\]\s+finished")

# Real Curve2Flood mapper options (see curve2flood.core).
_MAPPERS = [
    "Curve2Flood-Kernel Weighted",
    "Curve2Flood-FLDPLNpy",
    "Curve2Flood-Multi-Point Interpolation",
]


# ── Config panel (shared by single-AOI page and each card) ────────────────────

class ArcRunConfigPanel(QWidget):
    """Run options for ONE AOI (mapper + ARC bank/bathymetry switches)."""

    config_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)

        mp_row = QHBoxLayout()
        mp_row.addWidget(QLabel("Flood mapper:"))
        self._mapper = QComboBox()
        self._mapper.addItems(_MAPPERS)
        self._mapper.setFixedWidth(300)
        self._mapper.setToolTip(
            "Curve2Flood's flood-spreading algorithm. Kernel Weighted is the "
            "default; FLDPLNpy mimics the Kansas FLDPLN model; Multi-Point "
            "Interpolation interpolates the water surface between stream "
            "points.")
        self._mapper.currentIndexChanged.connect(
            lambda *_: self.config_changed.emit())
        mp_row.addWidget(self._mapper)
        mp_row.addStretch()
        layout.addLayout(mp_row)

        self._gpkg = QCheckBox(
            "Also write the flood map as GeoPackage  (Make_Output_GPKG)")
        self._gpkg.setChecked(True)
        self._gpkg.toggled.connect(lambda *_: self.config_changed.emit())
        layout.addWidget(self._gpkg)

        self._banks_lc = QCheckBox(
            "Find channel banks from land cover  (use_land_cover_to_find_banks)")
        self._banks_lc.setChecked(True)
        self._banks_lc.setToolTip(
            "ARC option: locate the channel banks from the land-cover raster "
            "(water class). Unchecked uses ARC's flat-surface approach.")
        self._banks_lc.toggled.connect(lambda *_: self.config_changed.emit())
        layout.addWidget(self._banks_lc)

        self._bathy_banks = QCheckBox(
            "Use bank elevations to estimate bathymetry  (bathy_use_banks)")
        self._bathy_banks.setChecked(False)
        self._bathy_banks.setToolTip(
            "ARC option: estimate the (unseen) channel bathymetry from the "
            "bank elevations instead of the water surface.")
        self._bathy_banks.toggled.connect(lambda *_: self.config_changed.emit())
        layout.addWidget(self._bathy_banks)

        note = QLabel(
            "The run rasterizes the streams, builds a rating curve for every "
            "reach (ARC), then maps the step-6 peak flow (Curve2Flood). It can "
            "take several minutes per AOI.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#718096; font-size:11px;")
        layout.addWidget(note)

    def is_ready(self) -> bool:
        return True

    def get_config(self) -> dict:
        return {
            "mapper":    self._mapper.currentText(),
            "make_gpkg": self._gpkg.isChecked(),
            "use_land_cover_to_find_banks": self._banks_lc.isChecked(),
            "bathy_use_banks": self._bathy_banks.isChecked(),
        }

    def set_config(self, cfg: dict):
        cfg = cfg or {}
        if cfg.get("mapper") in _MAPPERS:
            self._mapper.setCurrentText(cfg["mapper"])
        if "make_gpkg" in cfg:
            self._gpkg.setChecked(bool(cfg["make_gpkg"]))
        if "use_land_cover_to_find_banks" in cfg:
            self._banks_lc.setChecked(bool(cfg["use_land_cover_to_find_banks"]))
        if "bathy_use_banks" in cfg:
            self._bathy_banks.setChecked(bool(cfg["bathy_use_banks"]))


# ── Per-AOI card (mirrors AOIDEMCard chrome) ──────────────────────────────────

class AOIArcRunCard(QFrame):
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
        self.setStyleSheet(self.COLLAPSED_STYLE)
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

        self._panel = ArcRunConfigPanel(self)
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
        self.setStyleSheet(self.EXPANDED_STYLE)

    def collapse(self):
        if not self._expanded:
            return
        self._expanded = False
        self._panel.setVisible(False)
        self._toggle_btn.setText("Edit")
        self._caret.setText("▶")
        self.setStyleSheet(self.COLLAPSED_STYLE)
        self._refresh_status()

    def _on_toggle_clicked(self):
        if self._expanded:
            self.collapse()
        else:
            self.expand_requested.emit(self)

    # ── status line ───────────────────────────────────────────────────────────

    def _refresh_status(self):
        cfg = self._panel.get_config()
        mapper_short = cfg["mapper"].replace("Curve2Flood-", "")
        opts = []
        if cfg.get("make_gpkg"):
            opts.append("GPKG")
        if cfg.get("use_land_cover_to_find_banks"):
            opts.append("LC banks")
        if cfg.get("bathy_use_banks"):
            opts.append("bank bathy")
        self._status_lbl.setText(
            f"<i>Mapper:</i> {mapper_short}"
            + (f" &nbsp;·&nbsp; {', '.join(opts)}" if opts else ""))

    def _forward_config_changed(self):
        self._refresh_status()
        self.config_changed.emit(self)

    # ── public proxies ────────────────────────────────────────────────────────

    def panel(self) -> ArcRunConfigPanel:
        return self._panel

    def is_ready(self) -> bool:
        return self._panel.is_ready()

    def get_config(self) -> dict:
        return self._panel.get_config()

    def set_config(self, cfg: dict):
        self._panel.set_config(cfg)
        self._refresh_status()


# ── Step widget ───────────────────────────────────────────────────────────────

class StepArcConfigWidget(QWidget):
    step_completed = pyqtSignal(dict)

    def __init__(self, log_fn=print, parent=None):
        super().__init__(parent)
        self._log = log_fn
        self._worker = None
        self._ctx_path = None
        self._ctx = None
        self._aoi_features: list = []
        self._cards: List[AOIArcRunCard] = []
        self._cards_layout: QVBoxLayout = None
        self._stack: QStackedWidget = None
        self._single_panel: ArcRunConfigPanel = None
        self._setup_ui()

    # ── public API ────────────────────────────────────────────────────────────

    def set_context(self, ctx_path, ctx):
        self._ctx_path = ctx_path
        self._ctx = ctx or {}
        self._aoi_features = list(self._ctx.get("aoi_features", []) or [])
        self._clear_results()
        self._rebuild_for_aoi_count()

    def reset(self):
        self._aoi_features = []
        self._clear_cards()
        self._clear_results()
        self._error_lbl.setVisible(False)
        self._progress.setValue(0)
        self._progress.setVisible(False)
        self._status_lbl.setVisible(False)
        self._stack.setCurrentIndex(0)

    # ── UI construction ───────────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        self._aoi_count_lbl = QLabel("")
        self._aoi_count_lbl.setStyleSheet(
            "padding:6px 10px; background:#f7fafc; border:1px solid #cbd5e0; "
            "border-radius:4px; color:#2d3748; font-size:11px;"
        )
        self._aoi_count_lbl.setWordWrap(True)
        self._aoi_count_lbl.setVisible(False)
        layout.addWidget(self._aoi_count_lbl)

        self._stack = QStackedWidget()
        layout.addWidget(self._stack)

        # Page 0 — single-AOI form
        single_page = QWidget()
        sp_layout = QVBoxLayout(single_page)
        sp_layout.setContentsMargins(0, 0, 0, 0)
        gb = QGroupBox("7. Run ARC-Curve2Flood")
        gb_layout = QVBoxLayout(gb)
        self._single_panel = ArcRunConfigPanel(self)
        self._single_panel.config_changed.connect(self._on_single_config_changed)
        gb_layout.addWidget(self._single_panel)
        sp_layout.addWidget(gb)
        sp_layout.addStretch()
        self._stack.addWidget(single_page)

        # Page 1 — multi-AOI accordion
        multi_page = QWidget()
        mp_layout = QVBoxLayout(multi_page)
        mp_layout.setContentsMargins(0, 0, 0, 0)

        top_row = QHBoxLayout()
        self._apply_all_btn = QPushButton("Apply current AOI's settings to all")
        self._apply_all_btn.setStyleSheet(
            "background:#2b6cb0; color:white; padding:6px 14px; "
            "border-radius:3px; font-weight:bold;"
        )
        self._apply_all_btn.setToolTip(
            "Copy the currently expanded AOI's run configuration "
            "to every other AOI in this list."
        )
        self._apply_all_btn.clicked.connect(self._apply_to_all)
        self._apply_all_btn.setEnabled(False)
        top_row.addStretch()
        top_row.addWidget(self._apply_all_btn)
        mp_layout.addLayout(top_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        cards_host = QWidget()
        self._cards_layout = QVBoxLayout(cards_host)
        self._cards_layout.setSpacing(6)
        self._cards_layout.addStretch()
        scroll.setWidget(cards_host)
        mp_layout.addWidget(scroll, 1)
        self._stack.addWidget(multi_page)

        # Run button
        btn_row = QHBoxLayout()
        self._run_btn = QPushButton("Run ARC-Curve2Flood")
        self._run_btn.setStyleSheet(
            "font-weight:bold; padding:7px 20px; background:#2b6cb0; "
            "color:white; border-radius:4px;"
        )
        self._run_btn.clicked.connect(self._run_step)
        self._run_btn.setVisible(False)
        btn_row.addWidget(self._run_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Progress + status + error
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setVisible(False)
        self._progress.setStyleSheet("QProgressBar { height: 18px; }")
        layout.addWidget(self._progress)

        self._status_lbl = QLabel("")
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setStyleSheet(
            "color:#276749; font-weight:bold; font-size:12px; padding:2px 0px;"
        )
        self._status_lbl.setVisible(False)
        layout.addWidget(self._status_lbl)

        self._error_lbl = QLabel("")
        self._error_lbl.setWordWrap(True)
        self._error_lbl.setStyleSheet(
            "padding:10px; background:#fff5f5; border:1px solid #fc8181; "
            "border-radius:4px; font-size:12px; color:#c53030;"
        )
        self._error_lbl.setVisible(False)
        layout.addWidget(self._error_lbl)

        # Post-run results: clickable AOI list
        self._results_gb = QGroupBox(
            "Per-AOI flood maps  —  click an AOI to preview its flood raster"
        )
        self._results_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        rgl = QVBoxLayout(self._results_gb)
        self._results_inner = QVBoxLayout()
        self._results_inner.setSpacing(0)
        rgl.addLayout(self._results_inner)
        self._results_gb.setVisible(False)
        layout.addWidget(self._results_gb)

        # Preview: the flood raster
        self._gb_preview = QGroupBox("Flood map preview")
        self._gb_preview.setStyleSheet("QGroupBox { font-weight:bold; }")
        self._gb_preview.setMinimumHeight(400)
        pv = QVBoxLayout(self._gb_preview)
        pv.setSpacing(6)
        pv.setContentsMargins(6, 8, 6, 6)

        self._preview_placeholder = QLabel(
            "<i>Click an AOI above to preview its flood inundation raster.</i>"
        )
        self._preview_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_placeholder.setStyleSheet(
            "color:#888; padding:30px; background:#fafafa; "
            "border:1px dashed #cbd5e0; border-radius:4px;"
        )
        pv.addWidget(self._preview_placeholder)

        self._raster_preview = RasterPreviewCanvas(self, width=8, height=4.5)
        self._raster_preview.setVisible(False)
        pv.addWidget(self._raster_preview, 1)

        self._gb_preview.setVisible(False)
        layout.addWidget(self._gb_preview, 1)

        layout.addStretch()

    # ── Layout switching ──────────────────────────────────────────────────────

    def _rebuild_for_aoi_count(self):
        n = len(self._aoi_features)
        if n == 0:
            self._aoi_count_lbl.setText(
                "<i>No AOIs confirmed yet — go back to the AOI step first.</i>"
            )
            self._aoi_count_lbl.setVisible(True)
            self._stack.setCurrentIndex(0)
            self._run_btn.setVisible(False)
            return
        if n == 1:
            self._aoi_count_lbl.setText("<b>1</b> AOI confirmed.")
            self._aoi_count_lbl.setVisible(True)
            self._stack.setCurrentIndex(0)
            self._run_btn.setText("Run ARC-Curve2Flood")
            self._run_btn.setVisible(True)
            return
        self._aoi_count_lbl.setText(
            f"<b>{n}</b> AOI(s) confirmed — set each AOI's run options below. "
            "Click an AOI to expand its settings."
        )
        self._aoi_count_lbl.setVisible(True)
        self._stack.setCurrentIndex(1)
        self._run_btn.setText("Run ARC-Curve2Flood for all")
        self._build_cards()

    def _clear_cards(self):
        for c in list(self._cards):
            c.setParent(None)
            c.deleteLater()
        self._cards.clear()

    def _build_cards(self):
        self._clear_cards()
        for feat in self._aoi_features:
            card = AOIArcRunCard(feat.get("name", "(unnamed)"), self)
            card.expand_requested.connect(self._on_expand_requested)
            card.config_changed.connect(self._on_card_config_changed)
            card.remove_requested.connect(self._on_remove_requested)
            self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)
            self._cards.append(card)
        self._on_card_config_changed(None)

    def _on_remove_requested(self, card):
        idx = self._cards.index(card) if card in self._cards else -1
        if idx < 0:
            return
        aoi_name = (self._aoi_features[idx].get("name", f"AOI {idx+1}")
                    if idx < len(self._aoi_features) else "this AOI")
        reply = QMessageBox.question(
            self, "Remove AOI",
            f"Remove <b>{aoi_name}</b> from this step?\n\n"
            "The AOI's data folder is NOT deleted — only removed from the current run.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._cards.pop(idx)
        if idx < len(self._aoi_features):
            self._aoi_features.pop(idx)
        card.setParent(None)
        card.deleteLater()
        self._on_card_config_changed(None)
        self._aoi_count_lbl.setText(
            f"<b>{len(self._aoi_features)}</b> AOI(s) remaining."
        )

    # ── Accordion ─────────────────────────────────────────────────────────────

    def _on_expand_requested(self, card: AOIArcRunCard):
        for c in self._cards:
            if c is card:
                c.expand()
            else:
                c.collapse()
        self._apply_all_btn.setEnabled(True)

    def _expanded_card(self) -> Optional[AOIArcRunCard]:
        for c in self._cards:
            if c.is_expanded():
                return c
        return None

    def _on_card_config_changed(self, _card):
        all_ready = bool(self._cards) and all(c.is_ready() for c in self._cards)
        self._run_btn.setVisible(all_ready)

    def _on_single_config_changed(self):
        if self._stack.currentIndex() == 0 and len(self._aoi_features) <= 1:
            self._run_btn.setVisible(self._single_panel.is_ready())

    def _apply_to_all(self):
        src = self._expanded_card()
        if src is None:
            return
        cfg = src.get_config()
        for c in self._cards:
            if c is not src:
                c.set_config(cfg)
        self._on_card_config_changed(None)

    # ── Run ───────────────────────────────────────────────────────────────────

    def _run_step(self):
        if not self._ctx_path or not self._ctx:
            self._log("Complete earlier steps first.")
            return
        if not self._aoi_features:
            QMessageBox.warning(
                self, "No AOI Confirmed",
                "No AOIs are confirmed.\n\n"
                "Go back to the AOI step and confirm at least one feature first."
            )
            return

        if len(self._aoi_features) <= 1:
            per_aoi = [self._single_panel.get_config()]
        else:
            per_aoi = [c.get_config() for c in self._cards]

        self._error_lbl.setVisible(False)
        self._clear_results()
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._status_lbl.setText(
            "Running ARC (rating curves) + Curve2Flood (flood mapping) … "
            "this can take several minutes per AOI.")
        self._status_lbl.setVisible(True)
        set_running(self._run_btn)

        self._worker = Worker(
            run_arc_curve2flood_for_all_aois,
            ctx_path=self._ctx_path, ctx=self._ctx,
            per_aoi_configs=per_aoi,
        )
        self._worker.message.connect(self._on_message)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    # ── progress / log ────────────────────────────────────────────────────────

    def _on_message(self, msg):
        self._log(msg)
        m = _RUN_STEP_RE.match(msg)
        if m:
            i, total = int(m.group(1)), int(m.group(2))
            self._status_lbl.setText(
                f"Running ARC-Curve2Flood {i} / {total} …")
            self._status_lbl.setVisible(True)
            return
        m = _RUN_DONE_RE.match(msg)
        if m:
            i, total = int(m.group(1)), int(m.group(2))
            self._progress.setValue(int(i / max(total, 1) * 100))

    def _on_done(self, ctx):
        self._error_lbl.setVisible(False)
        self._ctx = ctx
        self._progress.setValue(100)
        n = max(len(self._aoi_features), 1)
        self._status_lbl.setText(f"All {n} AOI(s) processed.")
        self._status_lbl.setVisible(True)
        set_ready(self._run_btn)
        self._build_results(ctx)
        self.step_completed.emit({"ctx_path": self._ctx_path, "ctx": ctx})

    def _on_error(self, msg):
        self._log(f"ERROR: {msg}")
        self._progress.setVisible(False)
        set_ready(self._run_btn)
        first_line = msg.split("\n")[0]
        self._error_lbl.setText(
            f"<b>Error:</b> {first_line}<br>"
            "<small>(See log panel below for full details)</small>"
        )
        self._error_lbl.setVisible(True)

    # ── results + preview ───────────────────────────────────────────────────────

    def _clear_results(self):
        if not hasattr(self, "_results_inner"):
            return
        while self._results_inner.count():
            item = self._results_inner.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        if hasattr(self, "_results_gb"):
            self._results_gb.setVisible(False)
        if hasattr(self, "_gb_preview"):
            self._gb_preview.setVisible(False)
            self._preview_placeholder.setVisible(True)
            self._raster_preview.setVisible(False)
            self._raster_preview.clear()

    def _build_results(self, ctx):
        self._clear_results()
        per_aoi = ctx.get("arc_run_per_aoi", []) or []
        if not per_aoi:
            fm = ctx.get("arc_flood_map")
            if fm:
                per_aoi = [{
                    "name":       ctx.get("aoi_name", "AOI"),
                    "flood_map":  fm,
                    "curve_file": ctx.get("arc_curve_file"),
                }]
        if not per_aoi:
            return

        for entry in per_aoi:
            name = entry.get("name", "?")
            row = QFrame()
            row.setStyleSheet(
                "QFrame { background:#f9fafb; border:1px solid #e2e8f0; "
                "border-radius:3px; padding:3px 6px; }"
                "QFrame:hover { background:#f0f2f5; }"
            )
            rl = QHBoxLayout(row)
            rl.setContentsMargins(6, 2, 6, 2)
            rl.setSpacing(8)
            if entry.get("failed"):
                lbl = QLabel(f"✗  <b>{name}</b> — {entry.get('error', 'failed')}")
                lbl.setStyleSheet("color:#c53030; font-size:11px;")
                lbl.setWordWrap(True)
                rl.addWidget(lbl, 1)
                self._results_inner.addWidget(row)
                continue
            btn = QPushButton(f"  {name}")
            btn.setStyleSheet(
                "QPushButton { text-align:left; background:transparent; "
                "border:none; color:#2d3748; font-weight:bold; padding:2px; }"
                "QPushButton:hover { color:#1a202c; text-decoration:underline; }"
            )
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(
                lambda _checked, e=entry: self._show_preview_for(e)
            )
            rl.addWidget(btn, 1)
            fm = entry.get("flood_map")
            hint = QLabel(f"<small>{fm or '(no flood raster)'}</small>")
            hint.setStyleSheet("color:#718096; font-size:10px;")
            rl.addWidget(hint)
            self._results_inner.addWidget(row)

        self._results_gb.setVisible(True)
        self._gb_preview.setVisible(True)
        self._preview_placeholder.setVisible(True)
        self._raster_preview.setVisible(False)

    def _show_preview_for(self, entry: dict):
        fm = entry.get("flood_map")
        if not fm or not Path(fm).exists():
            self._preview_placeholder.setText(
                f"<span style='color:#c53030;'>Flood raster not found: "
                f"{fm}</span>"
            )
            self._preview_placeholder.setVisible(True)
            self._raster_preview.setVisible(False)
            return
        self._raster_preview.show_raster(
            fm, title=f"Flood inundation — {entry.get('name')}",
            cmap="Blues", colorbar_label="Flood depth / extent",
        )
        self._preview_placeholder.setVisible(False)
        self._raster_preview.setVisible(True)
