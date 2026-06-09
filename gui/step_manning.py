"""Step 4 — Manning's n / LULC.

Controller for the LISFLOOD-FP Manning step.  Two layouts based on how
many AOIs are confirmed in ctx:

  * 1 AOI         → one ``ManningConfigPanel`` embedded directly.
  * >1 AOI        → an accordion of ``AOIManningCard`` widgets (one per
                    AOI), with a top "Apply current AOI's settings to
                    everyone" button.  Only one card is expanded at a time.

The Run button dispatches to either ``prepare_manning`` (single-AOI) or
``run_lisflood_manning_for_all_aois`` (multi-AOI).
"""
import re
from pathlib import Path
from typing import List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QGroupBox, QProgressBar, QScrollArea, QStackedWidget, QMessageBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
)
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QColor

from core.manning import prepare_manning
from core.orchestrate import run_lisflood_manning_for_all_aois
from gui.worker import Worker
from gui.run_button import set_running, set_ready
from gui.manning_config_panel import ManningConfigPanel
from gui.aoi_manning_card import AOIManningCard
from gui.raster_preview import RasterPreviewCanvas


_MANNING_STEP_RE = re.compile(r"^▶\s+Manning\s+\[(\d+)/(\d+)\]")
_MANNING_DONE_RE = re.compile(r"^✓\s+Manning\s+\[(\d+)/(\d+)\]")


class StepManningWidget(QWidget):
    step_completed = pyqtSignal(dict)

    def __init__(self, log_fn, parent=None):
        super().__init__(parent)
        self._log = log_fn
        self._worker = None
        self._ctx_path = None
        self._ctx = None
        self._aoi_features: list = []

        # Set in _setup_ui
        self._stack: QStackedWidget = None        # type: ignore[assignment]
        self._single_panel: ManningConfigPanel = None  # type: ignore[assignment]
        self._cards: List[AOIManningCard] = []
        self._cards_layout: QVBoxLayout = None    # type: ignore[assignment]

        self._setup_ui()

    # ─────────────────────────────────────────────────────────────────────────
    # Public API used by app.py
    # ─────────────────────────────────────────────────────────────────────────

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
        if self._single_panel is not None:
            self._single_panel.set_config({"mode": ""})
        self._error_lbl.setVisible(False)
        self._report.setVisible(False)
        self._progress.setValue(0)
        self._progress.setVisible(False)
        self._status_lbl.setVisible(False)
        self._stack.setCurrentIndex(0)

    # ─────────────────────────────────────────────────────────────────────────
    # UI construction
    # ─────────────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # ── AOI count line ──
        self._aoi_count_lbl = QLabel("")
        self._aoi_count_lbl.setStyleSheet(
            "padding:6px 10px; background:#f7fafc; border:1px solid #cbd5e0; "
            "border-radius:4px; color:#2d3748; font-size:11px;"
        )
        self._aoi_count_lbl.setWordWrap(True)
        self._aoi_count_lbl.setVisible(False)
        layout.addWidget(self._aoi_count_lbl)

        # ── Stack switches between single-panel and multi-AOI accordion.
        # Stretch factor 1 + a generous min-height so an expanded AOI
        # card (varying-Manning panels are tall) stays usable even when
        # the user navigates back here with the post-run preview still
        # visible.
        self._stack = QStackedWidget()
        layout.addWidget(self._stack, 1)
        self._stack.setMinimumHeight(420)

        # Page 0 — single-AOI form
        single_page = QWidget()
        sp_layout = QVBoxLayout(single_page)
        sp_layout.setContentsMargins(0, 0, 0, 0)
        gb = QGroupBox("4. Floodplain Manning's n")
        gb_layout = QVBoxLayout(gb)
        self._single_panel = ManningConfigPanel(self)
        self._single_panel.config_ready_changed.connect(self._on_ready_changed)
        gb_layout.addWidget(self._single_panel)
        sp_layout.addWidget(gb)
        sp_layout.addStretch()
        self._stack.addWidget(single_page)

        # Page 1 — multi-AOI accordion
        multi_page = QWidget()
        mp_layout = QVBoxLayout(multi_page)
        mp_layout.setContentsMargins(0, 0, 0, 0)

        # Top row: "Apply current AOI's settings to all" button
        top_row = QHBoxLayout()
        self._apply_all_btn = QPushButton("Apply current AOI's settings to all")
        self._apply_all_btn.setStyleSheet(
            "background:#2b6cb0; color:white; padding:6px 14px; "
            "border-radius:3px; font-weight:bold;"
        )
        self._apply_all_btn.setToolTip(
            "Copy the currently expanded AOI's Manning configuration "
            "to every other AOI in this list."
        )
        self._apply_all_btn.clicked.connect(self._apply_to_all)
        self._apply_all_btn.setEnabled(False)
        top_row.addStretch()
        top_row.addWidget(self._apply_all_btn)
        mp_layout.addLayout(top_row)

        # Scrollable card stack
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        cards_host = QWidget()
        self._cards_layout = QVBoxLayout(cards_host)
        self._cards_layout.setSpacing(6)
        self._cards_layout.addStretch()
        scroll.setWidget(cards_host)
        mp_layout.addWidget(scroll, 1)

        self._stack.addWidget(multi_page)

        # ── Run button + progress + status ──
        btn_row = QHBoxLayout()
        self._run_btn = QPushButton("Prepare Manning File")
        self._run_btn.setStyleSheet(
            "font-weight:bold; padding:7px 20px; background:#2b6cb0; "
            "color:white; border-radius:4px;"
        )
        self._run_btn.clicked.connect(self._run_step)
        self._run_btn.setVisible(False)
        btn_row.addWidget(self._run_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setVisible(False)
        self._progress.setStyleSheet("QProgressBar { height: 18px; }")
        layout.addWidget(self._progress)

        self._status_lbl = QLabel("")
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setStyleSheet(
            "padding:6px 10px; background:#ebf8ff; border:1px solid #90cdf4; "
            "border-radius:4px; color:#2c5282; font-weight:bold; font-size:12px;"
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

        self._report = QLabel("")
        self._report.setWordWrap(True)
        self._report.setStyleSheet(
            "padding:10px; background:#f0fff4; border:1px solid #9ae6b4; "
            "border-radius:4px; font-size:12px;"
        )
        self._report.setVisible(False)
        layout.addWidget(self._report)

        # ── Post-run results: clickable AOI list + raster preview ────────
        # Same look as the DEM step.  Hidden until a successful run.
        self._results_gb = QGroupBox(
            "Per-AOI Manning outputs  —  click an AOI to preview its Manning map"
        )
        self._results_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        rgl = QVBoxLayout(self._results_gb)
        self._results_inner = QVBoxLayout()
        self._results_inner.setSpacing(0)
        rgl.addLayout(self._results_inner)
        self._results_gb.setVisible(False)
        layout.addWidget(self._results_gb)

        # Preview group: 3-column layout matching mode_lulc_manning.py
        #   left  → LULC class breakdown table (~20 %)
        #   middle → LULC raster canvas            (~40 %)
        #   right  → Manning raster canvas          (~40 %)
        self._gb_preview = QGroupBox(
            "LULC & Manning's n  —  click an AOI above to populate"
        )
        self._gb_preview.setStyleSheet("QGroupBox { font-weight:bold; }")
        self._gb_preview.setMinimumHeight(440)
        pv = QVBoxLayout(self._gb_preview)
        pv.setSpacing(6)
        pv.setContentsMargins(6, 8, 6, 6)

        # Placeholder fills the whole group while no AOI is picked.
        self._preview_placeholder = QLabel(
            "<i>Click an AOI above to preview its LULC map, Manning map, "
            "and class breakdown table.</i>"
        )
        self._preview_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_placeholder.setStyleSheet(
            "color:#888; padding:30px; background:#fafafa; "
            "border:1px dashed #cbd5e0; border-radius:4px;"
        )
        pv.addWidget(self._preview_placeholder)

        # Three-column row: [table] [LULC canvas] [Manning canvas]
        three_col = QHBoxLayout()
        three_col.setSpacing(10)

        # Left: class breakdown table  (~20 %)
        tbl_col = QVBoxLayout()
        tbl_hdr = QLabel("<b>Land Cover Breakdown</b>")
        tbl_hdr.setStyleSheet("color:#22543d; font-size:10px;")
        tbl_hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tbl_col.addWidget(tbl_hdr)

        self._lulc_table = QTableWidget(0, 4)
        self._lulc_table.setHorizontalHeaderLabels(
            ["Code", "Type", "Area (km²)", "% area"]
        )
        self._lulc_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._lulc_table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        self._lulc_table.setAlternatingRowColors(True)
        self._lulc_table.verticalHeader().setVisible(False)
        self._lulc_table.verticalHeader().setDefaultSectionSize(20)
        self._lulc_table.setStyleSheet(
            "QTableWidget { font-size: 10px; }"
            "QHeaderView::section { font-size: 10px; padding: 2px; }"
        )
        h = self._lulc_table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._lulc_table.setMinimumWidth(220)
        tbl_col.addWidget(self._lulc_table, 1)
        three_col.addLayout(tbl_col, 2)

        # Middle: LULC raster canvas
        lulc_col = QVBoxLayout()
        lulc_col.setContentsMargins(0, 0, 0, 0)
        self._lulc_title_lbl = QLabel("<b>LULC Map</b>")
        self._lulc_title_lbl.setStyleSheet("color:#22543d; font-size:10px;")
        self._lulc_title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lulc_col.addWidget(self._lulc_title_lbl)
        self._lulc_canvas = RasterPreviewCanvas(self, width=5.5, height=4.0)
        lulc_col.addWidget(self._lulc_canvas, 1)
        three_col.addLayout(lulc_col, 5)

        # Right: Manning raster canvas
        mn_col = QVBoxLayout()
        mn_col.setContentsMargins(0, 0, 0, 0)
        mn_hdr = QLabel("<b>Manning's n Map</b>")
        mn_hdr.setStyleSheet("color:#22543d; font-size:10px;")
        mn_hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mn_col.addWidget(mn_hdr)
        self._raster_preview = RasterPreviewCanvas(self, width=5.5, height=4.0)
        mn_col.addWidget(self._raster_preview, 1)
        three_col.addLayout(mn_col, 5)

        self._active_row = QWidget()
        self._active_row.setLayout(three_col)
        self._active_row.setMinimumHeight(400)
        self._active_row.setVisible(False)
        pv.addWidget(self._active_row, 1)

        self._gb_preview.setVisible(False)
        layout.addWidget(self._gb_preview)

    # ─────────────────────────────────────────────────────────────────────────
    # Layout switching (single vs multi)
    # ─────────────────────────────────────────────────────────────────────────

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
            self._aoi_count_lbl.setText(
                "<b>1</b> AOI confirmed."
            )
            self._aoi_count_lbl.setVisible(True)
            self._stack.setCurrentIndex(0)
            self._run_btn.setVisible(self._single_panel.is_ready())
            return

        # Multi-AOI: build cards
        self._aoi_count_lbl.setText(
            f"<b>{n}</b> AOI(s) confirmed — configure Manning for each AOI "
            "below.  Click an AOI to expand its settings."
        )
        self._aoi_count_lbl.setVisible(True)
        self._stack.setCurrentIndex(1)
        self._build_cards()

    def _clear_cards(self):
        for c in list(self._cards):
            c.setParent(None)
            c.deleteLater()
        self._cards.clear()

    def _build_cards(self):
        self._clear_cards()
        for feat in self._aoi_features:
            card = AOIManningCard(feat.get("name", "(unnamed)"), self)
            card.expand_requested.connect(self._on_expand_requested)
            card.config_changed.connect(self._on_card_config_changed)
            # Insert before the trailing stretch
            self._cards_layout.insertWidget(
                self._cards_layout.count() - 1, card
            )
            self._cards.append(card)
        # Re-evaluate buttons
        self._on_card_config_changed(None)

    # ─────────────────────────────────────────────────────────────────────────
    # Accordion behaviour
    # ─────────────────────────────────────────────────────────────────────────

    def _on_expand_requested(self, card: AOIManningCard):
        for c in self._cards:
            if c is card:
                c.expand()
            else:
                c.collapse()
        self._apply_all_btn.setEnabled(True)

    def _expanded_card(self) -> Optional[AOIManningCard]:
        for c in self._cards:
            if c.is_expanded():
                return c
        return None

    def _on_card_config_changed(self, _card):
        # Refresh the global Run button: every card must be ready.
        all_ready = bool(self._cards) and all(c.is_ready() for c in self._cards)
        self._run_btn.setVisible(all_ready)
        # The Apply-to-all button only makes sense when at least one card
        # has a non-empty config.
        any_configured = any(c.panel().mode() for c in self._cards)
        self._apply_all_btn.setEnabled(
            any_configured and self._expanded_card() is not None
        )

    def _apply_to_all(self):
        src = self._expanded_card()
        if src is None:
            QMessageBox.information(
                self, "Pick an AOI to copy from",
                "Click on the AOI whose settings you want to broadcast first, "
                "then click 'Apply current AOI's settings to all'.",
            )
            return
        cfg = src.get_config()
        if not cfg.get("mode"):
            QMessageBox.information(
                self, "Pick a mode first",
                "The selected AOI has no Fixed/Varying mode set yet.  "
                "Configure it, then apply to all.",
            )
            return
        for c in self._cards:
            if c is src:
                continue
            c.set_config(cfg)
        self._on_card_config_changed(None)

    # ─────────────────────────────────────────────────────────────────────────
    # Single-panel readiness
    # ─────────────────────────────────────────────────────────────────────────

    def _on_ready_changed(self, ready: bool):
        if self._stack.currentIndex() == 0 and len(self._aoi_features) <= 1:
            self._run_btn.setVisible(ready)

    # ─────────────────────────────────────────────────────────────────────────
    # Run
    # ─────────────────────────────────────────────────────────────────────────

    def _run_step(self):
        if not self._ctx_path or not self._ctx:
            self._log("Complete earlier steps first.")
            return

        self._error_lbl.setVisible(False)
        self._report.setVisible(False)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        set_running(self._run_btn)

        if len(self._aoi_features) <= 1:
            self._run_single()
        else:
            self._run_multi()

    def _run_single(self):
        cfg = self._single_panel.get_config()
        kw = self._build_prepare_manning_kwargs(cfg)
        if kw is None:
            set_ready(self._run_btn)
            self._progress.setVisible(False)
            return
        kw.update(ctx_path=self._ctx_path, ctx=self._ctx)

        self._status_lbl.setText("Preparing Manning file…")
        self._status_lbl.setVisible(True)

        self._worker = Worker(prepare_manning, **kw)
        self._worker.message.connect(self._on_message)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _run_multi(self):
        per_aoi = []
        for c in self._cards:
            cfg = c.get_config()
            kw = self._build_prepare_manning_kwargs(cfg)
            if kw is None:
                set_ready(self._run_btn)
                self._progress.setVisible(False)
                return
            per_aoi.append(kw)
        self._status_lbl.setText(
            f"Preparing Manning for {len(self._aoi_features)} AOI(s)…"
        )
        self._status_lbl.setVisible(True)

        self._worker = Worker(
            run_lisflood_manning_for_all_aois,
            ctx_path=self._ctx_path, ctx=self._ctx,
            per_aoi_configs=per_aoi,
        )
        self._worker.message.connect(self._on_message)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _build_prepare_manning_kwargs(self, cfg: dict) -> Optional[dict]:
        """Translate one ManningConfigPanel.get_config() dict into the
        kwargs accepted by core.manning.prepare_manning.  Returns None
        and shows an error label if the config is not runnable."""
        mode = cfg.get("mode")
        if mode == "fixed":
            return dict(fric_mode="fixed", fpfric_val=float(cfg["fixed_value"]))
        if mode != "varying":
            self._show_err(
                "Please pick Fixed or Varying for every AOI before running."
            )
            return None
        src = cfg.get("source", "")
        if src == "download":
            ds_idx = int(cfg.get("dataset_idx", 0))
            lulc_dl_src = "nlcd" if ds_idx == 0 else "esri"
            return dict(
                fric_mode="varying",
                have_manning_raster=False,
                manning_src_path=None,
                have_lulc=False,
                lulc_src_path=None,
                lulc_year=(int(cfg["year"]) if lulc_dl_src == "esri" else None),
                lulc_download_source=lulc_dl_src,
                nlcd_year=(cfg["year"] if lulc_dl_src == "nlcd" else "2021"),
                manning_mapping=cfg["table_mapping"],
            )
        if src == "upload":
            raster_path = cfg.get("raster_path", "")
            if not raster_path or not Path(raster_path).exists():
                self._show_err(
                    "One or more AOIs are set to 'I have a LULC raster' but "
                    "no valid file path is provided."
                )
                return None
            return dict(
                fric_mode="varying",
                have_manning_raster=False,
                manning_src_path=None,
                have_lulc=True,
                lulc_src_path=raster_path,
                lulc_year=None,
                lulc_download_source="esri",   # ignored when have_lulc=True
                nlcd_year="2021",
                manning_mapping=cfg["table_mapping"],
            )
        self._show_err(
            "Pick a LULC source (Download / I have a LULC raster) for every "
            "Varying AOI."
        )
        return None

    # ─────────────────────────────────────────────────────────────────────────
    # Worker callbacks
    # ─────────────────────────────────────────────────────────────────────────

    def _on_message(self, msg):
        self._log(msg)
        msg_l = msg.lower()

        m = _MANNING_STEP_RE.match(msg)
        if m:
            i, total = int(m.group(1)), int(m.group(2))
            self._progress.setValue(0)
            self._status_lbl.setText(
                f"Preparing Manning {i} / {total} …"
            )
            return

        m = _MANNING_DONE_RE.match(msg)
        if m:
            i, total = int(m.group(1)), int(m.group(2))
            self._progress.setValue(100)
            self._status_lbl.setText(
                f"Manning {i} / {total} finished."
                + (f"  Starting Manning {i + 1} / {total} …"
                   if i < total else "")
            )
            return

        # The Sentinel-2 LULC downloader logs "LULC progress: N/M …" once
        # per tile — match this first (most frequent message during long
        # downloads), then NLCD's "Downloading NLCD …", then the generic
        # fallbacks.
        m_lulc = re.search(r"lulc progress:\s*(\d+)\s*/\s*(\d+)", msg_l)
        if m_lulc:
            done, total = int(m_lulc.group(1)), int(m_lulc.group(2))
            if total > 0:
                # Map tile progress to the 10–70 % range so it advances
                # visibly while the rest of the work (merge/clip/ascii)
                # uses the remaining 30 %.
                self._progress.setValue(10 + int(done / total * 60))
            return

        if "downloading lulc" in msg_l or "fetching lulc" in msg_l \
                or "downloading nlcd" in msg_l or "requesting" in msg_l:
            self._progress.setValue(10)
        elif "tile" in msg_l and ("download" in msg_l or "fetch" in msg_l):
            mt = re.search(r"(\d+)\s*/\s*(\d+)", msg)
            if mt:
                done, total = int(mt.group(1)), int(mt.group(2))
                if total > 0:
                    self._progress.setValue(10 + int(done / total * 60))
        elif "merging" in msg_l or "mosaic" in msg_l:
            self._progress.setValue(75)
        elif "clipping" in msg_l or "reprojecting" in msg_l:
            self._progress.setValue(85)
        elif "writing manning" in msg_l or "writing ascii" in msg_l \
                or "ascii saved" in msg_l:
            self._progress.setValue(92)
        elif "manning step complete" in msg_l or "complete" in msg_l:
            self._progress.setValue(100)

    # ─────────────────────────────────────────────────────────────────────────
    # Post-run results: clickable AOI list + Manning raster preview
    # ─────────────────────────────────────────────────────────────────────────

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
            self._active_row.setVisible(False)
            self._preview_placeholder.setVisible(True)
            self._raster_preview.clear()
            if hasattr(self, "_lulc_canvas"):
                self._lulc_canvas.clear()
            self._lulc_table.setRowCount(0)

    def _build_results(self, ctx):
        """Populate the clickable AOI list from ctx['manning_per_aoi']."""
        self._clear_results()
        per_aoi = ctx.get("manning_per_aoi", []) or []
        # Single-AOI fallback — synthesise one row from the bridge keys
        if not per_aoi:
            tif = ctx.get("manning_tif_path")
            if tif or ctx.get("manning_ascii_path"):
                per_aoi = [{
                    "name":          ctx.get("aoi_name", "AOI"),
                    "fric_mode":     ctx.get("fric_mode", "varying"),
                    "manning_tif":   tif,
                    "manning_ascii": ctx.get("manning_ascii_path"),
                    "lulc_tif":      ctx.get("lulc_path"),
                    "lulc_source":   ctx.get("lulc_source"),
                    "fpfric":        ctx.get("par_fpfric"),
                }]
        if not per_aoi:
            return

        for entry in per_aoi:
            name = entry.get("name", "?")
            fric_mode = entry.get("fric_mode", "")
            tif = entry.get("manning_tif", "")
            row = QFrame()
            row.setStyleSheet(
                "QFrame { background:#f9fafb; border:1px solid #e2e8f0; "
                "border-radius:3px; padding:3px 6px; }"
                "QFrame:hover { background:#f0f2f5; }"
            )
            rl = QHBoxLayout(row)
            rl.setContentsMargins(6, 2, 6, 2)
            rl.setSpacing(8)
            if fric_mode == "fixed":
                lbl = QLabel(
                    f"<b>{name}</b>  →  Fixed n = {entry.get('fpfric')}"
                )
                lbl.setStyleSheet("color:#2d3748;")
                rl.addWidget(lbl, 1)
            else:
                btn = QPushButton(f"  {name}")
                btn.setStyleSheet(
                    "QPushButton { text-align:left; background:transparent; "
                    "border:none; color:#2d3748; font-weight:bold; padding:2px; }"
                    "QPushButton:hover { color:#1a202c; "
                    "text-decoration:underline; }"
                )
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(
                    lambda _checked, e=entry: self._show_raster_for_aoi(e)
                )
                rl.addWidget(btn, 1)
            self._results_inner.addWidget(row)
        self._results_gb.setVisible(True)
        # Preview group only matters if at least one AOI is varying
        any_varying = any(
            e.get("fric_mode") != "fixed" and e.get("manning_tif")
            for e in per_aoi
        )
        self._gb_preview.setVisible(any_varying)
        self._preview_placeholder.setVisible(any_varying)
        self._active_row.setVisible(False)

    def _show_raster_for_aoi(self, entry: dict):
        name = entry.get("name", "")
        manning_path = entry.get("manning_tif", "")
        lulc_path    = entry.get("lulc_tif", "")

        if not manning_path or not Path(manning_path).exists():
            self._preview_placeholder.setText(
                f"<span style='color:#c53030;'>Manning raster not found: "
                f"{manning_path}</span>"
            )
            self._preview_placeholder.setVisible(True)
            self._active_row.setVisible(False)
            return

        # Render the Manning raster (right column)
        self._raster_preview.show_raster(
            manning_path, title=f"Manning n — {name}",
            cmap="YlGnBu", colorbar_label="Manning n",
        )

        # Render the LULC raster (middle column) if available
        if lulc_path and Path(lulc_path).exists():
            self._lulc_canvas.show_raster(
                lulc_path, title=f"LULC — {name}",
                cmap="tab20", colorbar_label="LULC class",
            )
        else:
            self._lulc_canvas.clear()

        # Populate the LULC class breakdown table (left column)
        self._populate_lulc_table(entry)
        self._preview_placeholder.setVisible(False)
        self._active_row.setVisible(True)

    # ─────────────────────────────────────────────────────────────────────────
    # LULC class breakdown
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _lulc_class_lookup(lulc_source: str):
        """Pick the right code → name mapping for the AOI's LULC source.

        Returns a dict {int_code: class_name}.  Falls back to 'Class N'.
        """
        from core.nlcd import NLCD_MANNING, SENTINEL2_MANNING
        # lulc_source values written by core/manning.py:
        #   'download_nlcd', 'download_esri', 'user_lulc_raster'
        if lulc_source == "download_esri":
            return {k: v[0] for k, v in SENTINEL2_MANNING.items()}
        if lulc_source == "download_nlcd":
            return {k: v[0] for k, v in NLCD_MANNING.items()}
        # User-uploaded — try NLCD then S2 to label codes; unknowns get
        # rendered as "Class N".
        return {**{k: v[0] for k, v in SENTINEL2_MANNING.items()},
                **{k: v[0] for k, v in NLCD_MANNING.items()}}

    def _populate_lulc_table(self, entry: dict):
        """Read the AOI's LULC raster, count pixels per class, compute
        area + percentage, and render in self._lulc_table."""
        self._lulc_table.setRowCount(0)

        lulc_path = entry.get("lulc_tif")
        if not lulc_path or not Path(lulc_path).exists():
            self._lulc_table.setRowCount(1)
            it = QTableWidgetItem(
                "(LULC raster not available — Fixed n or "
                "user-supplied Manning raster)"
            )
            it.setForeground(QColor("#888"))
            self._lulc_table.setSpan(0, 0, 1, 4)
            self._lulc_table.setItem(0, 0, it)
            return

        try:
            import rasterio
            import numpy as np
            with rasterio.open(lulc_path) as src:
                arr = src.read(1)
                nodata = src.nodata
                tx = src.transform
                pixel_area_m2 = abs(tx.a * tx.e)
                if src.crs is not None and src.crs.is_geographic:
                    # Approximate metres at the raster's centre.  A degree of
                    # latitude is ~111_320 m; longitude shrinks by cos(lat).
                    cy = (src.bounds.bottom + src.bounds.top) / 2.0
                    pixel_area_m2 = (
                        abs(tx.a) * 111_320.0
                        * abs(tx.e) * 111_320.0
                        * np.cos(np.deg2rad(cy))
                    )
        except Exception as ex:
            self._lulc_table.setRowCount(1)
            it = QTableWidgetItem(f"(Could not read LULC: {ex})")
            it.setForeground(QColor("#c53030"))
            self._lulc_table.setSpan(0, 0, 1, 4)
            self._lulc_table.setItem(0, 0, it)
            return

        flat = arr.ravel()
        if nodata is not None:
            flat = flat[flat != nodata]
        if flat.dtype.kind == "f":
            flat = flat[~np.isnan(flat)]
            flat = flat.astype(np.int64)
        codes, counts = np.unique(flat, return_counts=True)
        if codes.size == 0:
            self._lulc_table.setRowCount(1)
            it = QTableWidgetItem("(No valid pixels in LULC raster.)")
            it.setForeground(QColor("#888"))
            self._lulc_table.setSpan(0, 0, 1, 4)
            self._lulc_table.setItem(0, 0, it)
            return

        # Sort by descending count so the dominant class is on top.
        order = np.argsort(-counts)
        codes = codes[order]
        counts = counts[order]
        total_pixels = int(counts.sum())

        names = self._lulc_class_lookup(entry.get("lulc_source") or "")
        # Show one row per class plus a footer "Total" row.  4 columns:
        # Code | Type | Area (km²) | % area
        self._lulc_table.setRowCount(len(codes) + 1)
        for r, (code, cnt) in enumerate(zip(codes, counts)):
            label = names.get(int(code), f"Class {int(code)}")
            cell_code = QTableWidgetItem(str(int(code)))
            cell_type = QTableWidgetItem(label)
            area_km2 = float(cnt) * pixel_area_m2 / 1e6
            cell_area = QTableWidgetItem(f"{area_km2:.3f}")
            pct = 100.0 * cnt / total_pixels
            cell_pct  = QTableWidgetItem(f"{pct:.1f}%")
            cell_code.setTextAlignment(
                Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
            )
            cell_area.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            cell_pct.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self._lulc_table.setItem(r, 0, cell_code)
            self._lulc_table.setItem(r, 1, cell_type)
            self._lulc_table.setItem(r, 2, cell_area)
            self._lulc_table.setItem(r, 3, cell_pct)

        total_area_km2 = float(total_pixels) * pixel_area_m2 / 1e6
        last = len(codes)
        for col, text in enumerate((
            "", "Total", f"{total_area_km2:.3f}", "100.0%",
        )):
            it = QTableWidgetItem(text)
            it.setBackground(QColor("#edf2f7"))
            f = it.font(); f.setBold(True); it.setFont(f)
            if col >= 2:
                it.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
            self._lulc_table.setItem(last, col, it)

    def _on_done(self, ctx):
        self._error_lbl.setVisible(False)
        self._ctx = ctx
        self._progress.setValue(100)
        # Match DEM step's wording so the two pages feel consistent.
        n = max(len(self._aoi_features), 1)
        self._status_lbl.setText(f"Manning processed for {n} AOI(s)")
        self._status_lbl.setStyleSheet("color:#276749; font-weight:bold; font-size:12px; padding:2px 0px;")
        self._status_lbl.setStyleSheet("color:#276749; font-weight:bold; font-size:12px; padding:2px 0px;")
        self._status_lbl.setStyleSheet(
            "padding:6px 10px; background:#f0fff4; border:1px solid #9ae6b4; "
            "border-radius:4px; color:#276749; font-weight:bold; font-size:12px;"
        )
        self._status_lbl.setVisible(True)
        set_ready(self._run_btn)
        self._show_report(ctx)
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

    def _show_err(self, msg: str):
        self._error_lbl.setText(f"{msg}")
        self._error_lbl.setVisible(True)

    def _show_report(self, ctx):
        per_aoi = ctx.get("manning_per_aoi", [])
        if per_aoi:
            rows = ""
            for entry in per_aoi:
                if entry.get("fric_mode") == "fixed":
                    rows += (
                        f"&nbsp;&nbsp;• <b>{entry['name']}</b>: "
                        f"Fixed n = {entry.get('fpfric')}<br>"
                    )
                else:
                    rows += (
                        f"&nbsp;&nbsp;• <b>{entry['name']}</b>: "
                        f"Varying → <code>{entry.get('manning_ascii', '?')}</code><br>"
                    )
            self._report.setText(
                f"<b>Manning Map(s) prepared successfully.</b><br><br>"
                f"<b>Per-AOI outputs:</b><br>{rows}"
            )
            self._report.setVisible(True)
            return

        # Single-AOI case
        fric_mode = ctx.get("fric_mode", "")
        if fric_mode == "fixed":
            fpfric = ctx.get("par_fpfric", "")
            html = (
                f"<b>Manning Map(s) prepared successfully.</b><br><br>"
                f"<b>Mode:</b> Fixed value<br>"
                f"<b>Manning n:</b> {fpfric}"
            )
        else:
            manning_ascii = ctx.get("manning_ascii_path", "")
            html = (
                f"<b>Manning Map(s) prepared successfully.</b><br><br>"
                f"<b>Mode:</b> Varying from LULC<br>"
                f"<b>Manning ASCII:</b> {manning_ascii}"
            )
        self._report.setText(html)
        self._report.setVisible(True)
