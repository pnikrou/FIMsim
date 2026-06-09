"""LULC + Manning standalone mode.

Flow:
  Project (page 0)  →  AOI selection (page 1)  →  LULC + Manning step (page 2)

Page 2 mirrors the DEM step pattern:
  • Per-AOI accordion cards (AOILulcCard) — one card per confirmed AOI
  • "Apply current AOI's settings to all" broadcast button
  • Single "Download LULC & Assign Manning" run button
  • After a successful run: clickable AOI list that reveals a dual raster
    preview (LULC + Manning) and a LULC class breakdown table.

No "Continue to next step" — LULC + Manning is the final step.
The user can go back to AOI selection at any time to re-run with new AOIs.
"""
from pathlib import Path
from typing import List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QStackedWidget, QProgressBar, QGroupBox,
    QMessageBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QFrame, QSizePolicy,
)
from PyQt6.QtCore import pyqtSignal, Qt

from gui.step_project import StepProjectWidget
from gui.multi_aoi_widget import MultiAOIWidget
from gui.aoi_lulc_card import AOILulcCard
from gui.raster_preview import RasterPreviewCanvas
from gui.run_button import set_running, set_ready
from gui.worker import Worker
from core.orchestrate import run_lulc_mode
from core.multi_aoi import AOIFeatureInfo


class ModeLULCManningWidget(QWidget):
    """Self-contained LULC + Manning preparation mode."""

    mode_finished = pyqtSignal()
    nav_changed   = pyqtSignal(int, int)

    def __init__(self, log_fn, parent=None):
        super().__init__(parent)
        self._log = log_fn
        self._project_dir: Optional[str] = None
        self._features: List[AOIFeatureInfo] = []
        self._cards: List[AOILulcCard] = []
        self._worker = None
        self._setup_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setSpacing(0)
        outer.setContentsMargins(0, 0, 0, 0)

        self._stack = QStackedWidget()
        outer.addWidget(self._stack)

        self._stack.currentChanged.connect(self._update_nav)

        # Page 0 — project
        self._proj = StepProjectWidget(self._log, model="generic")
        self._proj.step_completed.connect(self._on_project_done)
        self._stack.addWidget(self._wrap(self._proj))

        # Page 1 — multi-AOI
        self._aoi = MultiAOIWidget(self._log)
        self._aoi.aoi_ready.connect(self._on_aoi_ready)
        self._aoi.back_requested.connect(lambda: self._stack.setCurrentIndex(0))
        self._stack.addWidget(self._wrap(self._aoi))

        # Page 2 — LULC + Manning step
        self._stack.addWidget(self._wrap(self._build_step_page()))

        self._stack.setCurrentIndex(0)
        self._update_nav(0)

    def _wrap(self, w: QWidget) -> QScrollArea:
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setWidget(w)
        return sa

    def _goto_previous(self):
        cur = self._stack.currentIndex()
        if cur > 0:
            self._stack.setCurrentIndex(cur - 1)

    def _goto_next(self):
        cur = self._stack.currentIndex()
        if cur == 1:
            # AOI page — commit confirmed AOIs; aoi_ready slot advances the stack
            self._aoi.proceed_to_next()
        elif cur < self._stack.count() - 1:
            self._stack.setCurrentIndex(cur + 1)

    def _update_nav(self, idx: int):
        self.nav_changed.emit(idx, self._stack.count())

    def go_prev(self):
        self._goto_previous()

    def go_next(self):
        self._goto_next()

    # ── Page 2: LULC + Manning step ───────────────────────────────────────────

    def _build_step_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(10)

        # AOI summary line
        self._aoi_count_lbl = QLabel("")
        self._aoi_count_lbl.setStyleSheet("color:#2d3748; font-size:11px; padding:2px 0px;")
        self._aoi_count_lbl.setWordWrap(True)
        self._aoi_count_lbl.setVisible(False)
        v.addWidget(self._aoi_count_lbl)

        # "Apply to all" button
        top_row = QHBoxLayout()
        self._apply_all_btn = QPushButton("Apply current AOI's settings to all")
        self._apply_all_btn.setStyleSheet(
            "background:#4a5568; color:white; padding:6px 14px; "
            "border-radius:3px; font-weight:bold;"
        )
        self._apply_all_btn.setToolTip(
            "Copy the currently expanded AOI's source, year, format, cell size, "
            "and Manning table to every other AOI."
        )
        self._apply_all_btn.clicked.connect(self._apply_to_all)
        self._apply_all_btn.setEnabled(False)
        top_row.addStretch()
        top_row.addWidget(self._apply_all_btn)
        v.addLayout(top_row)

        # Accordion scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        cards_host = QWidget()
        self._cards_layout = QVBoxLayout(cards_host)
        self._cards_layout.setSpacing(6)
        self._cards_layout.addStretch()
        scroll.setWidget(cards_host)
        scroll.setMinimumHeight(320)
        v.addWidget(scroll, 1)

        # Run button
        btn_row = QHBoxLayout()
        self._run_btn = QPushButton("Download LULC & Assign Manning for all AOIs")
        self._run_btn.setStyleSheet(
            "font-weight:bold; padding:8px 22px; background:#2b6cb0; "
            "color:white; border-radius:4px; font-size:13px;"
        )
        self._run_btn.clicked.connect(self._run)
        self._run_btn.setVisible(False)
        btn_row.addWidget(self._run_btn)
        btn_row.addStretch()
        v.addLayout(btn_row)

        # Progress bar
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setStyleSheet("QProgressBar { height: 18px; }")
        self._progress.setVisible(False)
        v.addWidget(self._progress)

        # Status label (blue during run; turns green on completion)
        self._status_lbl = QLabel("")
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setStyleSheet("color:#2d3748; font-size:12px; padding:2px 0px;")
        self._status_lbl.setVisible(False)
        v.addWidget(self._status_lbl)

        # Completion summary box (green, shown after a successful run)
        self._completion_lbl = QLabel("")
        self._completion_lbl.setWordWrap(True)
        self._completion_lbl.setStyleSheet("color:#2d3748; font-size:12px; padding:2px 0px;")
        self._completion_lbl.setVisible(False)
        v.addWidget(self._completion_lbl)

        # Error label (red)
        self._error_lbl = QLabel("")
        self._error_lbl.setWordWrap(True)
        self._error_lbl.setStyleSheet(
            "padding:10px; background:#fff5f5; border:1px solid #fc8181; "
            "border-radius:4px; font-size:12px; color:#c53030;"
        )
        self._error_lbl.setVisible(False)
        v.addWidget(self._error_lbl)

        # ── Post-run results ──────────────────────────────────────────────────

        # Clickable AOI result rows
        self._results_gb = QGroupBox(
            "Per-AOI outputs  —  click an AOI to view its LULC & Manning maps"
        )
        self._results_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        rgl = QVBoxLayout(self._results_gb)
        self._results_inner = QVBoxLayout()
        self._results_inner.setSpacing(0)
        rgl.addLayout(self._results_inner)
        self._results_gb.setVisible(False)
        v.addWidget(self._results_gb)

        # ── Combined 3-column view: table | LULC map | Manning map ───────────
        self._view_gb = QGroupBox(
            "LULC & Manning's n  —  click an AOI above to populate"
        )
        self._view_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        view_outer = QVBoxLayout(self._view_gb)
        view_outer.setSpacing(6)
        view_outer.setContentsMargins(6, 8, 6, 6)

        # Placeholder (shown before any AOI is clicked, hidden afterwards)
        self._view_placeholder = QLabel(
            "<i>Click an AOI above to preview its LULC map, Manning map, "
            "and class breakdown table.</i>"
        )
        self._view_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._view_placeholder.setStyleSheet(
            "color:#888; padding:30px; background:#fafafa; "
            "border:1px dashed #cbd5e0; border-radius:4px;"
        )
        view_outer.addWidget(self._view_placeholder)

        # 3-column row: [table] [LULC canvas] [Manning canvas]
        three_col = QHBoxLayout()
        three_col.setSpacing(10)

        # Left: class breakdown table  (~20% width, compact font)
        tbl_col = QVBoxLayout()
        tbl_header = QLabel("<b>Land Cover Breakdown</b>")
        tbl_header.setStyleSheet("color:#2d3748; font-size:10px;")
        tbl_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tbl_col.addWidget(tbl_header)

        self._stats_table = QTableWidget()
        self._stats_table.setColumnCount(4)
        self._stats_table.setHorizontalHeaderLabels(
            ["Code", "Type", "Area %", "n"]
        )
        self._stats_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._stats_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._stats_table.setAlternatingRowColors(True)
        self._stats_table.verticalHeader().setVisible(False)
        # Compact row height and font for more content per pixel
        self._stats_table.verticalHeader().setDefaultSectionSize(20)
        self._stats_table.setStyleSheet(
            "QTableWidget { font-size: 10px; }"
            "QHeaderView::section { font-size: 10px; padding: 2px; }"
        )
        h = self._stats_table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)        # Type gets all remaining space
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._stats_table.setMinimumWidth(220)
        tbl_col.addWidget(self._stats_table, 1)
        three_col.addLayout(tbl_col, 2)   # table gets ~18% width

        # Middle: LULC raster canvas (title set dynamically with source+year)
        lulc_col = QVBoxLayout()
        lulc_col.setContentsMargins(0, 0, 0, 0)
        self._lulc_title_lbl = QLabel("<b>LULC Map</b>")
        self._lulc_title_lbl.setStyleSheet("color:#2d3748; font-size:10px;")
        self._lulc_title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lulc_col.addWidget(self._lulc_title_lbl)
        self._lulc_canvas = RasterPreviewCanvas(self, width=6.0, height=4.5)
        lulc_col.addWidget(self._lulc_canvas, 1)
        three_col.addLayout(lulc_col, 5)  # ~41% width

        # Right: Manning raster canvas
        mn_col = QVBoxLayout()
        mn_col.setContentsMargins(0, 0, 0, 0)
        mn_header = QLabel("<b>Manning's n Map</b>")
        mn_header.setStyleSheet("color:#2d3748; font-size:10px;")
        mn_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mn_col.addWidget(mn_header)
        self._mn_canvas = RasterPreviewCanvas(self, width=6.0, height=4.5)
        mn_col.addWidget(self._mn_canvas, 1)
        three_col.addLayout(mn_col, 5)    # ~41% width

        self._three_col_widget = QWidget()
        self._three_col_widget.setLayout(three_col)
        self._three_col_widget.setMinimumHeight(460)
        self._three_col_widget.setVisible(False)
        view_outer.addWidget(self._three_col_widget, 1)

        self._view_gb.setVisible(False)
        v.addWidget(self._view_gb)

        return page

    # ── slots ─────────────────────────────────────────────────────────────────

    def _on_project_done(self, data: dict):
        ctx = data.get("ctx", {})
        self._project_dir = ctx.get("project_dir")
        self._aoi.set_project_dir(self._project_dir)
        self._stack.setCurrentIndex(1)

    def _on_aoi_ready(self, features: List[AOIFeatureInfo]):
        self._features = features
        n = len(features)
        self._aoi_count_lbl.setText(
            f"<b>{n}</b> AOI(s) confirmed — configure LULC & Manning for each "
            "AOI below, then click the run button."
        )
        self._aoi_count_lbl.setVisible(True)
        self._clear_results()
        self._build_cards()
        self._stack.setCurrentIndex(2)

    # ── accordion ─────────────────────────────────────────────────────────────

    def _clear_cards(self):
        for c in list(self._cards):
            c.setParent(None)
            c.deleteLater()
        self._cards.clear()

    def _build_cards(self):
        self._clear_cards()
        for feat in self._features:
            card = AOILulcCard(feat.name, self)
            card.expand_requested.connect(self._on_expand_requested)
            card.config_changed.connect(self._on_card_changed)
            self._cards_layout.insertWidget(
                self._cards_layout.count() - 1, card
            )
            self._cards.append(card)
        self._on_card_changed(None)

    def _on_expand_requested(self, card: AOILulcCard):
        for c in self._cards:
            if c is card:
                c.expand()
            else:
                c.collapse()
        self._apply_all_btn.setEnabled(True)

    def _expanded_card(self) -> Optional[AOILulcCard]:
        for c in self._cards:
            if c.is_expanded():
                return c
        return None

    def _on_card_changed(self, _card):
        all_ready = bool(self._cards) and all(c.is_ready() for c in self._cards)
        self._run_btn.setVisible(all_ready)
        self._apply_all_btn.setEnabled(self._expanded_card() is not None)

    def _apply_to_all(self):
        src = self._expanded_card()
        if src is None:
            QMessageBox.information(
                self, "Pick an AOI to copy from",
                "Expand an AOI card first, then click "
                "'Apply current AOI's settings to all'.",
            )
            return
        cfg = src.get_config()
        for c in self._cards:
            if c is src:
                continue
            c.set_config(cfg)
        self._on_card_changed(None)

    # ── run ───────────────────────────────────────────────────────────────────

    def _run(self):
        if not self._features:
            self._log("No AOI features confirmed.")
            return

        per_aoi = [c.get_config() for c in self._cards]

        self._error_lbl.setVisible(False)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._status_lbl.setText(
            f"Starting LULC download for {len(self._features)} AOI(s) …"
        )
        self._status_lbl.setVisible(True)
        self._clear_results()
        set_running(self._run_btn)

        def _msg(m):
            self._log(m)
            # Simple progress heuristic
            if "Downloading NLCD" in m or "Downloading ESRI" in m:
                cur = self._progress.value()
                self._progress.setValue(min(cur + 5, 80))
            elif "Manning's n raster saved" in m or "ManningN" in m:
                self._progress.setValue(min(self._progress.value() + 5, 95))
            elif "Done [" in m:
                # Extract progress from "✓ Done [i/n]"
                import re
                mat = re.search(r"Done \[(\d+)/(\d+)\]", m)
                if mat:
                    i, total = int(mat.group(1)), int(mat.group(2))
                    self._progress.setValue(int(i / total * 95))
                feat_name = m.split("]: ")[-1].strip() if "]: " in m else ""
                self._status_lbl.setText(
                    f"Processing AOI(s) … last finished: {feat_name or m}"
                )

        self._worker = Worker(
            run_lulc_mode,
            project_dir=self._project_dir,
            features=self._features,
            per_aoi_configs=per_aoi,
        )
        self._worker.message.connect(_msg)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_done(self, summary: dict):
        set_ready(self._run_btn)
        self._progress.setValue(100)
        n = len(summary.get("features", []))
        self._status_lbl.setText(
            f"LULC + Manning processed for {n} AOI(s)"
        )
        self._status_lbl.setStyleSheet(
            "color:#276749; font-weight:bold; font-size:12px; padding:2px 0px;"
        )
        self._completion_lbl.setText(
            f"<b>LULC + Manning processed for {n} AOI(s)</b>"
            "<br><small><i>Click an AOI name below to view its maps.</i></small>"
        )
        self._completion_lbl.setStyleSheet(
            "color:#276749; font-weight:bold; font-size:12px; padding:2px 0px;"
        )
        self._completion_lbl.setVisible(True)
        self._build_results(summary)

    def _on_error(self, msg: str):
        set_ready(self._run_btn)
        self._progress.setVisible(False)
        self._log(f"ERROR: {msg}")
        self._error_lbl.setText(
            f"<b>Error:</b> {msg.splitlines()[0]}<br>"
            "<small>(See log panel for full details)</small>"
        )
        self._error_lbl.setVisible(True)

    # ── post-run results ──────────────────────────────────────────────────────

    def _clear_results(self):
        while self._results_inner.count():
            item = self._results_inner.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        self._results_gb.setVisible(False)
        self._view_gb.setVisible(False)
        self._view_placeholder.setVisible(True)
        self._three_col_widget.setVisible(False)
        self._stats_table.setRowCount(0)
        self._lulc_canvas.clear()
        self._mn_canvas.clear()
        if hasattr(self, "_completion_lbl"):
            self._completion_lbl.setVisible(False)

    def _build_results(self, summary: dict):
        self._clear_results()
        features_out = summary.get("features", [])
        if not features_out:
            return

        for entry in features_out:
            name        = entry.get("name", "?")
            lulc_tif    = entry.get("lulc_tif", "")
            manning_tif = entry.get("manning_tif", "")
            lulc_stats  = entry.get("lulc_stats", [])
            lulc_source = entry.get("lulc_source", "nlcd")
            lulc_year   = entry.get("lulc_year", "")

            row = QFrame()
            row.setStyleSheet(
                "QFrame { background:#f9fafb; border:1px solid #e2e8f0; "
                "border-radius:3px; padding:3px 6px; }"
                "QFrame:hover { background:#f0f2f5; }"
            )
            rl = QHBoxLayout(row)
            rl.setContentsMargins(6, 2, 6, 2)
            rl.setSpacing(8)

            btn = QPushButton(f"  {name}")
            btn.setStyleSheet(
                "QPushButton { text-align:left; background:transparent; "
                "border:none; color:#2d3748; font-weight:bold; padding:2px; }"
                "QPushButton:hover { color:#1a202c; text-decoration:underline; }"
            )
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(
                lambda _checked, lt=lulc_tif, mt=manning_tif,
                       nm=name, st=lulc_stats,
                       src=lulc_source, yr=lulc_year:
                    self._show_aoi_results(nm, lt, mt, st, src, yr)
            )
            rl.addWidget(btn, 1)
            self._results_inner.addWidget(row)

        self._results_gb.setVisible(True)
        self._view_gb.setVisible(True)

    def _show_aoi_results(
        self,
        name: str,
        lulc_tif: str,
        manning_tif: str,
        lulc_stats: list,
        lulc_source: str = "nlcd",
        lulc_year: str = "",
    ):
        # Outer groupbox title
        self._view_gb.setTitle(f"LULC & Manning's n  —  {name}")

        # The canvas title already shows source + year; the column header stays static
        src_label = "NLCD" if lulc_source == "nlcd" else "Sentinel-2"
        year_str  = f", {lulc_year}" if lulc_year else ""

        # ── LULC raster ───────────────────────────────────────────────────────
        content_ok = False
        if lulc_tif and Path(lulc_tif).exists():
            self._lulc_canvas.show_raster(
                lulc_tif,
                title=f"LULC ({src_label}{year_str})",
                cmap="tab20",
                colorbar_label="LULC class code",
                colorbar_location="bottom",
            )
            content_ok = True

        # ── Manning raster ────────────────────────────────────────────────────
        if manning_tif and Path(manning_tif).exists():
            self._mn_canvas.show_raster(
                manning_tif,
                title="Manning's n",
                cmap="YlOrRd",
                colorbar_label="Manning n",
                colorbar_location="bottom",
            )
            content_ok = True

        self._three_col_widget.setVisible(content_ok)
        self._view_placeholder.setVisible(not content_ok)

        # ── Class breakdown table (left column) ───────────────────────────────
        # Columns: Code | Type | Area % | n
        self._stats_table.setRowCount(0)
        for r_data in lulc_stats:
            row = self._stats_table.rowCount()
            self._stats_table.insertRow(row)

            code_item = QTableWidgetItem(str(r_data["code"]))
            code_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._stats_table.setItem(row, 0, code_item)

            self._stats_table.setItem(row, 1, QTableWidgetItem(r_data["name"]))

            pct = r_data["area_frac"] * 100
            pct_item = QTableWidgetItem(f"{pct:.1f}")   # no "%" — header carries it
            pct_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._stats_table.setItem(row, 2, pct_item)

            n_val = r_data.get("manning_n")
            n_str = f"{n_val:.3f}" if n_val is not None else "—"
            n_item = QTableWidgetItem(n_str)
            n_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._stats_table.setItem(row, 3, n_item)

    # ── reset ─────────────────────────────────────────────────────────────────

    def reset(self):
        self._project_dir = None
        self._features = []
        if hasattr(self._proj, "reset"):
            self._proj.reset()
        self._aoi.reset()
        self._clear_cards()
        self._clear_results()
        self._aoi_count_lbl.setVisible(False)
        self._progress.setValue(0)
        self._progress.setVisible(False)
        self._status_lbl.setVisible(False)
        self._status_lbl.setStyleSheet("color:#2d3748; font-size:12px; padding:2px 0px;")
        self._error_lbl.setVisible(False)
        try:
            set_ready(self._run_btn)
        except Exception:
            pass
        self._run_btn.setVisible(False)
        self._stack.setCurrentIndex(0)
