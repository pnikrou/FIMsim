"""Flowline standalone mode.

Pages:
  0 — Project
  1 — AOI selection
  2 — Flowline step   (per-AOI accordion + post-run flowline map preview)

Map pattern mirrors step_bci: AOI polygon + downloaded river(s) on one
simple matplotlib canvas.
"""
import re
from pathlib import Path
from typing import List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QStackedWidget, QProgressBar, QGroupBox,
    QMessageBox, QFrame,
)
from PyQt6.QtCore import pyqtSignal, Qt

from gui.step_project import StepProjectWidget
from gui.multi_aoi_widget import MultiAOIWidget
from gui.run_button import set_running, set_ready
from gui.aoi_flowline_card import AOIFlowlineCard
from gui.flowline_preview import FlowlinePreviewCanvas
from gui.worker import Worker
from core.flowline_mode import run_flowline_mode
from core.multi_aoi import AOIFeatureInfo

_DONE_RE = re.compile(r"Done \[(\d+)/(\d+)\]")


class ModeFlowlineWidget(QWidget):
    mode_finished = pyqtSignal()
    nav_changed   = pyqtSignal(int, int)

    def __init__(self, log_fn, parent=None):
        super().__init__(parent)
        self._log = log_fn
        self._project_dir: Optional[str] = None
        self._features: List[AOIFeatureInfo] = []
        self._worker: Optional[Worker] = None
        self._fl_cards: List[AOIFlowlineCard] = []
        self._setup_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setSpacing(0)
        outer.setContentsMargins(0, 0, 0, 0)

        self._stack = QStackedWidget()
        outer.addWidget(self._stack)

        self._stack.currentChanged.connect(self._update_nav)

        # Page 0: Project
        self._proj = StepProjectWidget(self._log, model="generic")
        self._proj.step_completed.connect(self._on_project_done)
        self._stack.addWidget(self._wrap(self._proj))        # 0

        # Page 1: AOI
        self._aoi = MultiAOIWidget(self._log)
        self._aoi.aoi_ready.connect(self._on_aoi_ready)
        self._aoi.back_requested.connect(lambda: self._stack.setCurrentIndex(0))
        self._stack.addWidget(self._wrap(self._aoi))         # 1

        # Page 2: Flowline step
        self._stack.addWidget(self._wrap(self._build_flowline_page()))  # 2

        self._stack.setCurrentIndex(0)
        self._update_nav(0)

    def _wrap(self, w: QWidget) -> QScrollArea:
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setWidget(w)
        return sa

    # ── Page 2: Flowline ──────────────────────────────────────────────────────

    def _build_flowline_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(10)

        self._fl_count_lbl = QLabel("")
        self._fl_count_lbl.setStyleSheet(
            "padding:6px 10px; background:#f7fafc; border:1px solid #cbd5e0; "
            "border-radius:4px; color:#2d3748; font-size:11px;"
        )
        self._fl_count_lbl.setWordWrap(True)
        v.addWidget(self._fl_count_lbl)

        # Apply-to-all
        top_row = QHBoxLayout()
        self._fl_apply_btn = QPushButton("Apply current AOI's settings to all")
        self._fl_apply_btn.setStyleSheet(
            "background:#4a5568; color:white; padding:6px 14px; "
            "border-radius:3px; font-weight:bold;"
        )
        self._fl_apply_btn.clicked.connect(self._fl_apply_to_all)
        self._fl_apply_btn.setEnabled(False)
        top_row.addStretch()
        top_row.addWidget(self._fl_apply_btn)
        v.addLayout(top_row)

        # Cards scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        cards_host = QWidget()
        self._fl_cards_layout = QVBoxLayout(cards_host)
        self._fl_cards_layout.setSpacing(6)
        self._fl_cards_layout.addStretch()
        scroll.setWidget(cards_host)
        scroll.setMinimumHeight(280)
        v.addWidget(scroll, 1)

        # Run button
        btn_row = QHBoxLayout()
        self._fl_run_btn = QPushButton("✔  Download Flowlines for all AOIs")
        self._fl_run_btn.setStyleSheet(
            "font-weight:bold; padding:8px 22px; background:#2b6cb0; "
            "color:white; border-radius:4px; font-size:13px;"
        )
        self._fl_run_btn.clicked.connect(self._run_flowlines)
        self._fl_run_btn.setVisible(False)
        btn_row.addWidget(self._fl_run_btn)
        btn_row.addStretch()
        v.addLayout(btn_row)

        # Progress + status
        self._fl_progress = QProgressBar()
        self._fl_progress.setRange(0, 100)
        self._fl_progress.setStyleSheet("QProgressBar { height: 18px; }")
        self._fl_progress.setVisible(False)
        v.addWidget(self._fl_progress)

        self._fl_status_lbl = QLabel("")
        self._fl_status_lbl.setWordWrap(True)
        self._fl_status_lbl.setStyleSheet(
            "color:#2d3748; font-size:12px; padding:2px 0px;"
        )
        self._fl_status_lbl.setVisible(False)
        v.addWidget(self._fl_status_lbl)

        self._fl_error_lbl = QLabel("")
        self._fl_error_lbl.setWordWrap(True)
        self._fl_error_lbl.setStyleSheet(
            "padding:10px; background:#fff5f5; border:1px solid #fc8181; "
            "border-radius:4px; color:#c53030;"
        )
        self._fl_error_lbl.setVisible(False)
        v.addWidget(self._fl_error_lbl)

        # Completion summary box (green, shown after a successful run)
        self._fl_completion_lbl = QLabel("")
        self._fl_completion_lbl.setWordWrap(True)
        self._fl_completion_lbl.setStyleSheet(
            "color:#2d3748; font-size:12px; padding:2px 0px;"
        )
        self._fl_completion_lbl.setVisible(False)
        v.addWidget(self._fl_completion_lbl)

        # ── Post-run results (BCI pattern) ────────────────────────────────────
        self._fl_results_gb = QGroupBox(
            "Per-AOI flowline outputs  —  click an AOI to view its map"
        )
        self._fl_results_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        rgl = QVBoxLayout(self._fl_results_gb)
        self._fl_results_inner = QVBoxLayout()
        self._fl_results_inner.setSpacing(0)
        rgl.addLayout(self._fl_results_inner)
        self._fl_results_gb.setVisible(False)
        v.addWidget(self._fl_results_gb)

        self._fl_preview_gb = QGroupBox("Flowline map")
        self._fl_preview_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        self._fl_preview_gb.setFixedHeight(340)
        pvl = QVBoxLayout(self._fl_preview_gb)
        self._fl_placeholder = QLabel(
            "<i>Click an AOI above to see its AOI polygon and downloaded flowlines.</i>"
        )
        self._fl_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._fl_placeholder.setStyleSheet(
            "color:#888; padding:30px; background:#fafafa; "
            "border:1px dashed #cbd5e0; border-radius:4px;"
        )
        self._fl_canvas = FlowlinePreviewCanvas(self, width=9, height=3.5)
        self._fl_canvas.setVisible(False)
        pvl.addWidget(self._fl_placeholder)
        pvl.addWidget(self._fl_canvas)
        self._fl_preview_gb.setVisible(False)
        v.addWidget(self._fl_preview_gb)

        return page

    # ── card building ─────────────────────────────────────────────────────────

    def _build_fl_cards(self):
        for c in list(self._fl_cards):
            c.setParent(None); c.deleteLater()
        self._fl_cards.clear()
        n = len(self._features)
        self._fl_count_lbl.setText(
            f"<b>{n}</b> AOI(s) confirmed — configure flowline options for each."
        )
        for feat in self._features:
            card = AOIFlowlineCard(feat.name, self)
            card.expand_requested.connect(self._fl_on_expand)
            card.config_changed.connect(self._fl_on_config_changed)
            self._fl_cards_layout.insertWidget(
                self._fl_cards_layout.count() - 1, card
            )
            self._fl_cards.append(card)
        self._fl_on_config_changed(None)

    # ── accordion behaviour ───────────────────────────────────────────────────

    def _fl_on_expand(self, card):
        for c in self._fl_cards:
            c.expand() if c is card else c.collapse()
        self._fl_apply_btn.setEnabled(True)

    def _fl_on_config_changed(self, _card):
        ready = bool(self._fl_cards) and all(c.is_ready() for c in self._fl_cards)
        self._fl_run_btn.setVisible(ready)
        expanded = next((c for c in self._fl_cards if c.is_expanded()), None)
        self._fl_apply_btn.setEnabled(expanded is not None)

    def _fl_apply_to_all(self):
        src = next((c for c in self._fl_cards if c.is_expanded()), None)
        if src is None:
            QMessageBox.information(self, "Pick an AOI first", "Expand an AOI card first.")
            return
        cfg = src.get_config()
        for c in self._fl_cards:
            if c is not src:
                c.set_config(cfg)

    # ── navigation ────────────────────────────────────────────────────────────

    def _goto_prev(self):
        cur = self._stack.currentIndex()
        if cur > 0:
            self._stack.setCurrentIndex(cur - 1)

    def _goto_next(self):
        cur = self._stack.currentIndex()
        if cur == 1:
            self._aoi.proceed_to_next()
            return
        if cur < self._stack.count() - 1:
            self._stack.setCurrentIndex(cur + 1)

    def _update_nav(self, idx: int):
        self.nav_changed.emit(idx, self._stack.count())

    def go_prev(self):
        self._goto_prev()

    def go_next(self):
        self._goto_next()

    # ── slots ─────────────────────────────────────────────────────────────────

    def _on_project_done(self, data: dict):
        self._project_dir = data.get("ctx", {}).get("project_dir")
        self._aoi.set_project_dir(self._project_dir)
        self._stack.setCurrentIndex(1)

    def _on_aoi_ready(self, features: List[AOIFeatureInfo]):
        self._features = features
        self._clear_fl_results()
        self._build_fl_cards()
        self._stack.setCurrentIndex(2)

    # ── run: flowlines ────────────────────────────────────────────────────────

    def _run_flowlines(self):
        if not self._features:
            return
        self._fl_error_lbl.setVisible(False)
        self._clear_fl_results()
        self._fl_progress.setValue(0)
        self._fl_progress.setVisible(True)
        self._fl_status_lbl.setText(
            f"⏳ Downloading flowlines for {len(self._features)} AOI(s) …"
        )
        self._fl_status_lbl.setVisible(True)
        set_running(self._fl_run_btn)

        def _msg(m):
            self._log(m)
            mat = _DONE_RE.search(m)
            if mat:
                i, total = int(mat.group(1)), int(mat.group(2))
                self._fl_progress.setValue(int(i / total * 100))
                self._fl_status_lbl.setText(f"⏳ Flowlines {i}/{total} done.")

        self._worker = Worker(
            run_flowline_mode,
            project_dir=self._project_dir,
            features=self._features,
            per_aoi_configs=[c.get_config() for c in self._fl_cards],
        )
        self._worker.message.connect(_msg)
        self._worker.finished.connect(self._on_fl_done)
        self._worker.error.connect(
            lambda m: self._on_error(m, self._fl_run_btn,
                                     self._fl_error_lbl, self._fl_progress)
        )
        self._worker.start()

    def _on_fl_done(self, summary: dict):
        set_ready(self._fl_run_btn)
        self._fl_progress.setValue(100)
        n = len(summary.get("features", []))
        self._fl_status_lbl.setText(
            f"✅ Flowlines processed for {n} AOI(s)"
        )
        self._fl_status_lbl.setStyleSheet(
            "color:#2d3748; font-size:12px; padding:2px 0px;"
        )
        self._fl_completion_lbl.setText(
            f"<b>✅ Flowlines processed for {n} AOI(s)</b>"
            "<br><small><i>Click an AOI name below to view its flowline map.</i></small>"
        )
        self._fl_completion_lbl.setVisible(True)
        self._build_fl_results(summary)

    # ── shared error handler ──────────────────────────────────────────────────

    def _on_error(self, msg: str, run_btn, error_lbl, progress):
        set_ready(run_btn)
        progress.setVisible(False)
        self._log(f"ERROR: {msg}")
        error_lbl.setText(
            f"❌ <b>Error:</b> {msg.splitlines()[0]}<br>"
            "<small>(See log panel for full details)</small>"
        )
        error_lbl.setVisible(True)

    # ── post-run: flowline results (BCI pattern) ──────────────────────────────

    def _clear_fl_results(self):
        while self._fl_results_inner.count():
            item = self._fl_results_inner.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
        self._fl_results_gb.setVisible(False)
        self._fl_preview_gb.setVisible(False)
        self._fl_canvas.setVisible(False)
        self._fl_placeholder.setVisible(True)
        self._fl_canvas.clear()
        if hasattr(self, "_fl_completion_lbl"):
            self._fl_completion_lbl.setVisible(False)
        if hasattr(self, "_fl_status_lbl"):
            self._fl_status_lbl.setStyleSheet(
                "color:#2d3748; font-size:12px; padding:2px 0px;"
            )

    def _build_fl_results(self, summary: dict):
        self._clear_fl_results()
        feat_entries = summary.get("features", [])
        if not feat_entries:
            return

        feat_by_name = {f.name: f for f in self._features}

        for entry in feat_entries:
            name  = entry.get("name", "?")
            files = entry.get("files", {})
            feat  = feat_by_name.get(name)

            row = QFrame()
            row.setStyleSheet(
                "QFrame { background:#f9fafb; border:1px solid #e2e8f0; "
                "border-radius:3px; padding:3px 6px; }"
                "QFrame:hover { background:#f0f2f5; }"
            )
            rl = QHBoxLayout(row)
            rl.setContentsMargins(6, 2, 6, 2)
            btn = QPushButton(f"  {name}")
            btn.setStyleSheet(
                "QPushButton { text-align:left; background:transparent; "
                "border:none; color:#2d3748; font-weight:bold; padding:2px; }"
                "QPushButton:hover { color:#1a202c; text-decoration:underline; }"
            )
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(
                lambda _checked, e=entry, f=feat:
                    self._show_fl_preview(e, f)
            )
            rl.addWidget(btn, 1)
            self._fl_results_inner.addWidget(row)

        self._fl_results_gb.setVisible(True)
        self._fl_preview_gb.setVisible(True)

    def _show_fl_preview(self, entry: dict, feat: Optional[AOIFeatureInfo]):
        """Render AOI polygon + downloaded flowlines + USGS gages on the canvas."""
        files = entry.get("files", {})
        name  = entry.get("name", "?")

        aoi_path  = getattr(feat, "source_file", None) if feat else None
        feat_idx  = getattr(feat, "feature_index", 0) if feat else 0

        # In-memory GeoDataFrames always available regardless of save format/checkboxes
        main_gdf = entry.get("_main_river_gdf")
        all_gdf  = entry.get("_all_flowlines_gdf")

        # Fallback file paths (used only if GDFs are absent, e.g. older summaries)
        main_path = files.get("main_river", "")
        all_path  = files.get("all_flowlines", "")
        gages_csv = files.get("gages_csv", "")

        if not aoi_path or not Path(aoi_path).exists():
            self._fl_placeholder.setText(
                f"<span style='color:#c53030;'>AOI file not found: {aoi_path}</span>"
            )
            self._fl_placeholder.setVisible(True)
            self._fl_canvas.setVisible(False)
            return

        # Load USGS gage rows from the saved CSV (columns: site_no, station_nm, lat, lon)
        usgs_gages = None
        if gages_csv and Path(gages_csv).exists():
            try:
                import pandas as pd
                df = pd.read_csv(gages_csv)
                if not df.empty and {"lat", "lon"}.issubset(df.columns):
                    usgs_gages = df.to_dict(orient="records")
            except Exception:
                pass

        # Derive upstream/downstream endpoints from the main river line.
        # NHD lines run from upstream to downstream, so the first coordinate
        # of the merged line is the upstream end and the last is downstream.
        upstream_xy, downstream_xy = None, None
        gdf_for_pts = main_gdf
        if gdf_for_pts is None and main_path and Path(main_path).exists():
            try:
                import geopandas as _gpd
                gdf_for_pts = _gpd.read_file(main_path)
            except Exception:
                pass
        if gdf_for_pts is not None and not gdf_for_pts.empty:
            try:
                from shapely.ops import linemerge as _linemerge
                geoms = [g for g in gdf_for_pts.geometry if g is not None]
                merged = _linemerge(geoms)
                if hasattr(merged, "geoms"):   # MultiLineString
                    coords_first = list(merged.geoms[0].coords)
                    coords_last  = list(merged.geoms[-1].coords)
                    upstream_xy   = coords_first[0]
                    downstream_xy = coords_last[-1]
                else:                           # LineString
                    coords = list(merged.coords)
                    upstream_xy   = coords[0]
                    downstream_xy = coords[-1]
            except Exception:
                pass

        self._fl_canvas.show_flowlines(
            aoi_path=aoi_path,
            feature_index=int(feat_idx),
            main_river_path=main_path if main_path and Path(main_path).exists() else None,
            all_flowlines_path=all_path if all_path and Path(all_path).exists() else None,
            main_river_gdf=main_gdf,
            all_flowlines_gdf=all_gdf,
            title=f"Flowlines — {name}",
            usgs_gages=usgs_gages,
            upstream_xy=upstream_xy,
            downstream_xy=downstream_xy,
        )
        self._fl_placeholder.setVisible(False)
        self._fl_canvas.setVisible(True)

    # ── reset ─────────────────────────────────────────────────────────────────

    def reset(self):
        self._project_dir = None
        self._features = []
        if hasattr(self._proj, "reset"):
            self._proj.reset()
        self._aoi.reset()
        for c in list(self._fl_cards):
            c.setParent(None); c.deleteLater()
        self._fl_cards.clear()
        self._clear_fl_results()
        for w in (self._fl_progress, self._fl_status_lbl, self._fl_error_lbl):
            w.setVisible(False)
        self._fl_run_btn.setVisible(False)
        try:
            set_ready(self._fl_run_btn)
        except Exception:
            pass
        self._stack.setCurrentIndex(0)
