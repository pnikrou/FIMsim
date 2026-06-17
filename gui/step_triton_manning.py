"""Step 4 — Friction (Manning's n) — TRITON.

Multi-AOI controller for the TRITON friction step.  Mirrors the LISFLOOD-FP
Manning step (gui/step_manning.py) so the two feel identical; the only
differences are that this writes each AOI's friction raster into its
``triton-files`` folder (as ``<AOI>.asc``) and dispatches to the TRITON
builders:

  * 1 AOI   → one ``ManningConfigPanel`` embedded directly.
  * >1 AOI  → an accordion of ``AOIManningCard`` widgets (one per AOI), with
              a top "Apply current AOI's settings to all" button.

Run dispatches to ``prepare_triton_manning`` (single-AOI) or
``run_triton_manning_for_all_aois`` (multi-AOI).  The friction OPTIONS are
identical to LISFLOOD's (Fixed / Varying → NLCD / Sentinel-2 / upload), so we
reuse LISFLOOD's panel + card widgets unchanged.
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

from core.triton_manning import prepare_triton_manning
from core.orchestrate import run_triton_manning_for_all_aois
from gui.worker import Worker
from gui.run_button import set_running, set_ready
from gui.triton_manning_config_panel import ManningConfigPanel
from gui.aoi_triton_manning_card import AOIManningCard
from gui.triton_raster_preview import RasterPreviewCanvas


_FRICTION_STEP_RE = re.compile(r"^▶\s+Friction\s+\[(\d+)/(\d+)\]")
_FRICTION_DONE_RE = re.compile(r"^✓\s+Friction\s+\[(\d+)/(\d+)\]")


class StepTritonManningWidget(QWidget):
    step_completed = pyqtSignal(dict)

    def __init__(self, log_fn, parent=None):
        super().__init__(parent)
        self._log = log_fn
        self._worker = None
        self._ctx_path = None
        self._ctx = None
        self._aoi_features: list = []

        self._stack: QStackedWidget = None        # type: ignore[assignment]
        self._single_panel: ManningConfigPanel = None  # type: ignore[assignment]
        self._cards: List[AOIManningCard] = []
        self._cards_layout: QVBoxLayout = None    # type: ignore[assignment]

        self._setup_ui()

    # ── Public API used by app.py ─────────────────────────────────────────────

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
        layout.addWidget(self._stack, 1)
        self._stack.setMinimumHeight(420)

        # Page 0 — single-AOI form
        single_page = QWidget()
        sp_layout = QVBoxLayout(single_page)
        sp_layout.setContentsMargins(0, 0, 0, 0)
        gb = QGroupBox("4. Friction (Manning's n)")
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

        top_row = QHBoxLayout()
        self._apply_all_btn = QPushButton("Apply current AOI's settings to all")
        self._apply_all_btn.setStyleSheet(
            "background:#2b6cb0; color:white; padding:6px 14px; "
            "border-radius:3px; font-weight:bold;"
        )
        self._apply_all_btn.setToolTip(
            "Copy the currently expanded AOI's friction configuration "
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

        # Run button + progress + status
        btn_row = QHBoxLayout()
        self._run_btn = QPushButton("Prepare Friction File")
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

        # Post-run results: clickable AOI list + raster preview
        self._results_gb = QGroupBox(
            "Per-AOI Friction outputs  —  click an AOI to preview its Manning map"
        )
        rgl = QVBoxLayout(self._results_gb)
        self._results_inner = QVBoxLayout()
        self._results_inner.setSpacing(0)
        rgl.addLayout(self._results_inner)
        self._results_gb.setVisible(False)
        layout.addWidget(self._results_gb)

        self._gb_preview = QGroupBox("LULC & Manning preview")
        self._gb_preview.setMinimumHeight(440)
        pv = QVBoxLayout(self._gb_preview)
        pv.setSpacing(6)
        pv.setContentsMargins(6, 8, 6, 6)

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

        three_col = QHBoxLayout()
        three_col.setSpacing(10)

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

        lulc_col = QVBoxLayout()
        lulc_col.setContentsMargins(0, 0, 0, 0)
        self._lulc_title_lbl = QLabel("<b>LULC Map</b>")
        self._lulc_title_lbl.setStyleSheet("color:#22543d; font-size:10px;")
        self._lulc_title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lulc_col.addWidget(self._lulc_title_lbl)
        self._lulc_canvas = RasterPreviewCanvas(self, width=5.5, height=4.0)
        lulc_col.addWidget(self._lulc_canvas, 1)
        three_col.addLayout(lulc_col, 5)

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

    # ── Layout switching (single vs multi) ────────────────────────────────────

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
            self._run_btn.setVisible(self._single_panel.is_ready())
            return

        self._aoi_count_lbl.setText(
            f"<b>{n}</b> AOI(s) confirmed — configure friction for each AOI "
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
            card.remove_requested.connect(self._on_remove_requested)
            self._cards_layout.insertWidget(
                self._cards_layout.count() - 1, card
            )
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
        n = len(self._aoi_features)
        self._aoi_count_lbl.setText(
            f"<b>{n}</b> AOI(s) remaining — configure each below."
        )

    # ── Accordion behaviour ───────────────────────────────────────────────────

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
        all_ready = bool(self._cards) and all(c.is_ready() for c in self._cards)
        self._run_btn.setVisible(all_ready)
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

    # ── Single-panel readiness ────────────────────────────────────────────────

    def _on_ready_changed(self, ready: bool):
        if self._stack.currentIndex() == 0 and len(self._aoi_features) <= 1:
            self._run_btn.setVisible(ready)

    # ── Run ────────────────────────────────────────────────────────────────────

    def _run_step(self):
        if not self._ctx_path or not self._ctx:
            self._log("Complete earlier steps first.")
            return
        self._error_lbl.setVisible(False)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        set_running(self._run_btn)
        if len(self._aoi_features) <= 1:
            self._run_single()
        else:
            self._run_multi()

    def _run_single(self):
        cfg = self._single_panel.get_config()
        kw = self._build_triton_manning_kwargs(cfg)
        if kw is None:
            set_ready(self._run_btn)
            self._progress.setVisible(False)
            return
        kw.update(ctx_path=self._ctx_path, ctx=self._ctx)
        self._status_lbl.setText("Preparing friction file…")
        self._status_lbl.setVisible(True)
        self._worker = Worker(prepare_triton_manning, **kw)
        self._worker.message.connect(self._on_message)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _run_multi(self):
        per_aoi = []
        for c in self._cards:
            cfg = c.get_config()
            kw = self._build_triton_manning_kwargs(cfg)
            if kw is None:
                set_ready(self._run_btn)
                self._progress.setVisible(False)
                return
            per_aoi.append(kw)
        self._status_lbl.setText(
            f"Preparing friction for {len(self._aoi_features)} AOI(s)…"
        )
        self._status_lbl.setVisible(True)
        self._worker = Worker(
            run_triton_manning_for_all_aois,
            ctx_path=self._ctx_path, ctx=self._ctx,
            per_aoi_configs=per_aoi,
        )
        self._worker.message.connect(self._on_message)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _build_triton_manning_kwargs(self, cfg: dict) -> Optional[dict]:
        """Translate a ManningConfigPanel.get_config() dict into the kwargs
        accepted by core.triton_manning.prepare_triton_manning.  Returns None
        (and shows an error) if the config is not runnable."""
        mode = cfg.get("mode")
        dem_res_m = float(self._ctx.get("dem_res_m", 10.0) or 10.0)
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
            if ds_idx == 0:   # NLCD
                return dict(
                    fric_mode="varying", lulc_source="download_nlcd",
                    nlcd_year=str(cfg.get("year", "2021")),
                    lulc_class_to_n=cfg.get("table_mapping"),
                    dem_res_m=dem_res_m,
                )
            # ESRI Sentinel-2
            return dict(
                fric_mode="varying", lulc_source="download",
                lulc_year=int(cfg["year"]),
                lulc_class_to_n=cfg.get("table_mapping"),
                dem_res_m=dem_res_m,
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
                fric_mode="varying", lulc_source="user_lulc",
                user_lulc_path=raster_path,
                lulc_class_to_n=cfg.get("table_mapping"),
                dem_res_m=dem_res_m,
            )
        self._show_err(
            "Pick a LULC source (Download / I have a LULC raster) for every "
            "Varying AOI."
        )
        return None

    # ── Worker callbacks ────────────────────────────────────────────────────

    def _on_message(self, msg):
        self._log(msg)
        msg_l = msg.lower()

        m = _FRICTION_STEP_RE.match(msg)
        if m:
            i, total = int(m.group(1)), int(m.group(2))
            self._progress.setValue(0)
            self._status_lbl.setText(f"Preparing friction {i} / {total} …")
            return

        m = _FRICTION_DONE_RE.match(msg)
        if m:
            i, total = int(m.group(1)), int(m.group(2))
            self._progress.setValue(100)
            self._status_lbl.setText(
                f"Friction {i} / {total} finished."
                + (f"  Starting friction {i + 1} / {total} …"
                   if i < total else "")
            )
            return

        m_lulc = re.search(r"lulc progress:\s*(\d+)\s*/\s*(\d+)", msg_l)
        if m_lulc:
            done, total = int(m_lulc.group(1)), int(m_lulc.group(2))
            if total > 0:
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
        elif ("writing" in msg_l and "asc" in msg_l) or "ascii saved" in msg_l \
                or ".asc written" in msg_l:
            self._progress.setValue(92)
        elif "complete" in msg_l:
            self._progress.setValue(100)

    # ── Post-run results: clickable AOI list + Manning raster preview ─────────

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
            self._gb_preview.setVisible(True)
            self._active_row.setVisible(False)
            self._preview_placeholder.setVisible(True)
            self._raster_preview.clear()
            if hasattr(self, "_lulc_canvas"):
                self._lulc_canvas.clear()
            self._lulc_table.setRowCount(0)

    def _build_results(self, ctx):
        """Populate the clickable AOI list from ctx['triton_manning_per_aoi']."""
        self._clear_results()
        per_aoi = ctx.get("triton_manning_per_aoi", []) or []
        if not per_aoi:
            tif = ctx.get("manning_tif_path")
            if tif or ctx.get("triton_friction_path"):
                per_aoi = [{
                    "name":          ctx.get("aoi_name", "AOI"),
                    "fric_mode":     ctx.get("triton_fric_mode", "varying"),
                    "manning_tif":   tif,
                    "manning_ascii": ctx.get("triton_friction_path"),
                    "lulc_tif":      ctx.get("lulc_path"),
                    "lulc_source":   ctx.get("lulc_source"),
                    "fpfric":        ctx.get("par_fpfric"),
                }]
        if not per_aoi:
            return

        for entry in per_aoi:
            name = entry.get("name", "?")
            fric_mode = entry.get("fric_mode", "")
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
                lbl = QLabel(f"<b>{name}</b>  →  Fixed n = {entry.get('fpfric')}")
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

        self._raster_preview.show_raster(
            manning_path, title=f"Manning n — {name}",
            cmap="YlGnBu", colorbar_label="Manning n",
        )
        if lulc_path and Path(lulc_path).exists():
            self._lulc_canvas.show_raster(
                lulc_path, title=f"LULC — {name}",
                cmap="tab20", colorbar_label="LULC class",
            )
        else:
            self._lulc_canvas.clear()
        self._populate_lulc_table(entry)
        self._preview_placeholder.setVisible(False)
        self._active_row.setVisible(True)

    # ── LULC class breakdown ──────────────────────────────────────────────────

    @staticmethod
    def _lulc_class_lookup(lulc_source: str):
        from core.nlcd import NLCD_MANNING, SENTINEL2_MANNING
        if lulc_source in ("download_esri", "download"):
            return {k: v[0] for k, v in SENTINEL2_MANNING.items()}
        if lulc_source == "download_nlcd":
            return {k: v[0] for k, v in NLCD_MANNING.items()}
        return {**{k: v[0] for k, v in SENTINEL2_MANNING.items()},
                **{k: v[0] for k, v in NLCD_MANNING.items()}}

    def _populate_lulc_table(self, entry: dict):
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

        order = np.argsort(-counts)
        codes = codes[order]
        counts = counts[order]
        total_pixels = int(counts.sum())

        names = self._lulc_class_lookup(entry.get("lulc_source") or "")
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

    def _show_err(self, msg: str):
        self._error_lbl.setText(f"{msg}")
        self._error_lbl.setVisible(True)
