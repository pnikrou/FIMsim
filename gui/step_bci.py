"""Step 5 — Boundary Conditions (BC.bci) — LISFLOOD-FP.

Controller that picks the right layout for the BCI step:

  * 1 AOI         → one BCIConfigPanel embedded directly.
  * >1 AOIs       → an accordion of AOIBCICard widgets, one per AOI, with
                    a top "Apply current AOI's settings to all" button.

The Run button dispatches to either ``create_bci`` (single AOI) or
``run_lisflood_bci_for_all_aois`` (multi-AOI).
"""
import re
from pathlib import Path
from typing import List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QGroupBox, QProgressBar, QScrollArea, QStackedWidget, QMessageBox,
)
from PyQt6.QtCore import pyqtSignal, Qt

from core.bci import create_bci
from core.orchestrate import run_lisflood_bci_for_all_aois
from gui.worker import Worker
from gui.run_button import set_running, set_ready
from gui.bci_config_panel import BCIConfigPanel
from gui.aoi_bci_card import AOIBCICard
from gui.bci_preview import BCIPreviewCanvas


_BCI_STEP_RE = re.compile(r"^▶\s+BCI\s+\[(\d+)/(\d+)\]")
_BCI_DONE_RE = re.compile(r"^✓\s+BCI\s+\[(\d+)/(\d+)\]")


class StepBCIWidget(QWidget):
    step_completed = pyqtSignal(dict)

    def __init__(self, log_fn, parent=None):
        super().__init__(parent)
        self._log = log_fn
        self._worker = None
        self._ctx_path = None
        self._ctx = None
        self._aoi_features: list = []
        self._cards: List[AOIBCICard] = []
        self._setup_ui()

    # ── public API used by app.py ─────────────────────────────────────────────

    def set_context(self, ctx_path, ctx):
        self._ctx_path = ctx_path
        self._ctx = ctx or {}
        self._aoi_features = list(self._ctx.get("aoi_features", []) or [])
        # Push AOI source-file info into the single panel so its "AOI CRS"
        # hint shows the right value when the user picks Manual.
        if self._aoi_features:
            f0 = self._aoi_features[0]
            try:
                self._single_panel.set_aoi_path(
                    f0.get("source_file"),
                    int(f0.get("feature_index", 0)),
                )
            except Exception:
                pass
        self._clear_results()
        self._rebuild_for_aoi_count()

    def reset(self):
        self._aoi_features = []
        self._clear_cards()
        self._clear_results()
        self._error_lbl.setVisible(False)
        self._report.setVisible(False)
        self._progress.setValue(0)
        self._progress.setVisible(False)
        self._status_lbl.setVisible(False)
        self._stack.setCurrentIndex(0)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Info banner — same wording as before, applies to both layouts.
        info = QLabel(
            "<b>ℹ️  NHD auto-detect</b> downloads NHD flowlines, identifies the "
            "highest-order river, and derives upstream / downstream boundary "
            "points from DEM elevations.  Works for <b>USA only</b>.<br>"
            "Use <b>Manual</b> mode outside the USA or to override "
            "auto-detection."
        )
        info.setWordWrap(True)
        info.setStyleSheet(
            "padding:8px; background:#fffbeb; border:1px solid #f6e05e; "
            "border-radius:4px;"
        )
        layout.addWidget(info)

        # AOI count line
        self._aoi_count_lbl = QLabel("")
        self._aoi_count_lbl.setStyleSheet(
            "padding:6px 10px; background:#f7fafc; border:1px solid #cbd5e0; "
            "border-radius:4px; color:#2d3748; font-size:11px;"
        )
        self._aoi_count_lbl.setWordWrap(True)
        self._aoi_count_lbl.setVisible(False)
        layout.addWidget(self._aoi_count_lbl)

        # Stack: page 0 = single, page 1 = multi.  Stretch factor 1 +
        # a generous min-height so an expanded AOI card stays usable
        # when the post-run map preview is also showing.
        self._stack = QStackedWidget()
        layout.addWidget(self._stack, 1)
        self._stack.setMinimumHeight(360)

        # ── Page 0: single-AOI form ──
        single_page = QWidget()
        sp_layout = QVBoxLayout(single_page)
        sp_layout.setContentsMargins(0, 0, 0, 0)
        gb = QGroupBox("5. Boundary Conditions (BC.bci)")
        gb_layout = QVBoxLayout(gb)
        self._single_panel = BCIConfigPanel(self)
        self._single_panel.config_changed.connect(self._on_single_config_changed)
        gb_layout.addWidget(self._single_panel)
        sp_layout.addWidget(gb)
        sp_layout.addStretch()
        self._stack.addWidget(single_page)

        # ── Page 1: multi-AOI accordion ──
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
            "Copy the currently expanded AOI's BCI configuration to every "
            "other AOI in this list."
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
        self._run_btn = QPushButton("Write BC.bci")
        self._run_btn.setStyleSheet(
            "font-weight:bold; padding:7px 20px; background:#2b6cb0; "
            "color:white; border-radius:4px;"
        )
        self._run_btn.clicked.connect(self._run_step)
        # Hidden until the user picks a detection method
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

        # ── Post-run results: clickable AOI list + BCI preview map ──────
        # Same look as DEM / Manning steps.
        self._results_gb = QGroupBox(
            "Per-AOI BCI outputs  —  click an AOI to preview its BCI on the map"
        )
        self._results_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        rgl = QVBoxLayout(self._results_gb)
        self._results_inner = QVBoxLayout()
        self._results_inner.setSpacing(0)
        rgl.addLayout(self._results_inner)
        self._results_gb.setVisible(False)
        layout.addWidget(self._results_gb)

        self._gb_preview = QGroupBox("BCI map preview")
        self._gb_preview.setFixedHeight(330)
        pv = QVBoxLayout(self._gb_preview)
        self._preview_placeholder = QLabel(
            "<i>Click an AOI above to see its AOI polygon, flowline, "
            "and upstream/downstream points.</i>"
        )
        self._preview_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_placeholder.setStyleSheet(
            "color:#888; padding:30px; background:#fafafa; "
            "border:1px dashed #cbd5e0; border-radius:4px;"
        )
        self._bci_preview = BCIPreviewCanvas(self, width=9, height=3.5)
        self._bci_preview.setVisible(False)
        pv.addWidget(self._preview_placeholder)
        pv.addWidget(self._bci_preview)
        self._gb_preview.setVisible(False)
        layout.addWidget(self._gb_preview)

    # ── layout switching ──────────────────────────────────────────────────────

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

        # Multi-AOI
        self._aoi_count_lbl.setText(
            f"<b>{n}</b> AOI(s) confirmed — configure boundary conditions "
            "for each AOI below.  Click an AOI to expand its settings."
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
            card = AOIBCICard(feat.get("name", "(unnamed)"), self)
            card.expand_requested.connect(self._on_expand_requested)
            card.config_changed.connect(self._on_card_config_changed)
            # Push AOI source-file info so the panel's CRS hint can read it.
            try:
                card.panel().set_aoi_path(
                    feat.get("source_file"),
                    int(feat.get("feature_index", 0)),
                )
            except Exception:
                pass
            self._cards_layout.insertWidget(
                self._cards_layout.count() - 1, card
            )
            self._cards.append(card)
        # Re-evaluate buttons (cards start with no detection selected, so
        # the global Run button stays hidden until every card is ready).
        self._on_card_config_changed(None)

    # ── accordion behaviour ───────────────────────────────────────────────────

    def _on_expand_requested(self, card: AOIBCICard):
        for c in self._cards:
            if c is card:
                c.expand()
            else:
                c.collapse()
        self._apply_all_btn.setEnabled(True)

    def _expanded_card(self) -> Optional[AOIBCICard]:
        for c in self._cards:
            if c.is_expanded():
                return c
        return None

    def _on_card_config_changed(self, _card):
        # Apply-to-all is enabled whenever a card is expanded.
        self._apply_all_btn.setEnabled(self._expanded_card() is not None)
        # Run button only appears when EVERY AOI has a detection method.
        all_ready = bool(self._cards) and all(c.is_ready() for c in self._cards)
        self._run_btn.setVisible(all_ready)

    def _on_single_config_changed(self):
        if self._stack.currentIndex() == 0 and len(self._aoi_features) <= 1:
            self._run_btn.setVisible(self._single_panel.is_ready())

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
        for c in self._cards:
            if c is src:
                continue
            c.set_config(cfg)

    # ── run ───────────────────────────────────────────────────────────────────

    def _run_step(self):
        if not self._ctx_path or not self._ctx:
            self._log("Complete earlier steps first.")
            return

        from gui.overwrite_check import confirm_overwrite

        self._error_lbl.setVisible(False)
        self._report.setVisible(False)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        set_running(self._run_btn)

        if len(self._aoi_features) <= 1:
            # Overwrite check on the single-AOI bridge bci_path
            if not confirm_overwrite(self, [self._ctx.get("bci_path")], "BCI"):
                set_ready(self._run_btn)
                self._progress.setVisible(False)
                return
            self._run_single()
        else:
            # Multi-AOI: warn for each AOI's BC.bci
            check = []
            for f in self._aoi_features:
                folder = f.get("folder_path", "")
                if folder:
                    check.append(str(Path(folder) / "BC.bci"))
            if not confirm_overwrite(self, check, "BCI"):
                set_ready(self._run_btn)
                self._progress.setVisible(False)
                return
            self._run_multi()

    def _run_single(self):
        cfg = self._single_panel.get_config()
        kw = self._build_create_bci_kwargs(cfg)
        kw.update(ctx_path=self._ctx_path, ctx=self._ctx)

        self._status_lbl.setText("Preparing BC.bci…")
        self._status_lbl.setVisible(True)

        self._worker = Worker(create_bci, **kw)
        self._worker.message.connect(self._on_message)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _run_multi(self):
        per_aoi = [self._build_create_bci_kwargs(c.get_config())
                   for c in self._cards]
        self._status_lbl.setText(
            f"Preparing BC.bci for {len(self._aoi_features)} AOI(s)…"
        )
        self._status_lbl.setVisible(True)
        self._worker = Worker(
            run_lisflood_bci_for_all_aois,
            ctx_path=self._ctx_path, ctx=self._ctx,
            per_aoi_configs=per_aoi,
        )
        self._worker.message.connect(self._on_message)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    @staticmethod
    def _build_create_bci_kwargs(cfg: dict) -> dict:
        """Translate a BCIConfigPanel.get_config() dict into create_bci
        kwargs (matches the legacy single-AOI behaviour)."""
        use_nhd = bool(cfg.get("use_nhd", True))
        upstream_mode = cfg.get("upstream_mode", "varying_discharge")
        downstream_type = cfg.get("downstream_type", "FREE")
        kw = dict(
            upstream_mode=upstream_mode,
            downstream_type=downstream_type,
            fixed_discharge_cms=(cfg["fixed_q"]
                                 if upstream_mode == "fixed_discharge" else None),
            downstream_slope=(cfg["slope"]
                              if downstream_type == "FREE" else None),
            downstream_hfix=(cfg["hfix"]
                             if downstream_type == "HFIX" else None),
            use_nhd=use_nhd,
        )
        if not use_nhd:
            kw.update(
                manual_upstream_x=cfg["up_x"],
                manual_upstream_y=cfg["up_y"],
                manual_downstream_x=cfg["dn_x"],
                manual_downstream_y=cfg["dn_y"],
            )
        return kw

    # ── worker callbacks ──────────────────────────────────────────────────────

    def _on_message(self, msg):
        self._log(msg)

        m = _BCI_STEP_RE.match(msg)
        if m:
            i, total = int(m.group(1)), int(m.group(2))
            self._progress.setValue(0)
            self._status_lbl.setText(f"Preparing BCI {i} / {total} …")
            return

        m = _BCI_DONE_RE.match(msg)
        if m:
            i, total = int(m.group(1)), int(m.group(2))
            self._progress.setValue(100)
            self._status_lbl.setText(
                f"BCI {i} / {total} finished."
                + (f"  Starting BCI {i + 1} / {total} …"
                   if i < total else "")
            )
            return

        ml = msg.lower()
        if "downloading" in ml or "querying nhd" in ml:
            self._progress.setValue(20)
        elif "flowlines saved" in ml:
            self._progress.setValue(50)
        elif "main river" in ml:
            self._progress.setValue(70)
        elif "bc.bci written" in ml:
            self._progress.setValue(95)

    # ─────────────────────────────────────────────────────────────────────────
    # Post-run results: clickable AOI list + BCI map preview
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
            self._bci_preview.setVisible(False)
            self._preview_placeholder.setVisible(True)
            self._bci_preview.clear()

    def _build_results(self, ctx):
        """Populate the clickable AOI list from ctx['bci_per_aoi'].

        Falls back to a synthesised one-row list from the bridge keys when
        only one AOI was processed (single-AOI workflow)."""
        self._clear_results()
        per_aoi = ctx.get("bci_per_aoi", []) or []
        if not per_aoi:
            f0 = (self._aoi_features[0] if self._aoi_features else {})
            single = {
                "name":            ctx.get("aoi_name", f0.get("name", "AOI")),
                "bci_path":        ctx.get("bci_path"),
                "upstream_mode":   ctx.get("upstream_mode"),
                "downstream_type": ctx.get("downstream_type"),
                "river":           ctx.get("main_river_name"),
                "upstream_x":      ctx.get("upstream_x"),
                "upstream_y":      ctx.get("upstream_y"),
                "downstream_x":    ctx.get("downstream_x"),
                "downstream_y":    ctx.get("downstream_y"),
                "source_file":     f0.get("source_file") or ctx.get("aoi_path"),
                "feature_index":   f0.get("feature_index", 0),
                "main_river_line": str(
                    Path(ctx.get("project_dir", "")) / "main_river_line.gpkg"
                ),
                "flowlines_path":  ctx.get("flowlines_path"),
                # DEM path — used to read the CRS for reprojecting
                # upstream/downstream points in the map preview.
                "dem_path": (
                    ctx.get("dem_path")
                    or (ctx.get("dem_per_aoi") or [{}])[0].get("dem_tif")
                ),
            }
            if single.get("bci_path"):
                per_aoi = [single]
        if not per_aoi:
            return

        for entry in per_aoi:
            name = entry.get("name", "?")
            up = ("QFIX" if entry.get("upstream_mode") == "fixed_discharge"
                  else "QVAR")
            dn = entry.get("downstream_type", "")
            river = entry.get("river") or ""
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
                "QPushButton:hover { color:#1a202c; "
                "text-decoration:underline; }"
            )
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(
                lambda _checked, e=entry: self._show_bci_for_aoi(e)
            )
            rl.addWidget(btn, 1)
            self._results_inner.addWidget(row)
        self._results_gb.setVisible(True)
        self._gb_preview.setVisible(True)
        self._preview_placeholder.setVisible(True)
        self._bci_preview.setVisible(False)

    def _show_bci_for_aoi(self, entry: dict):
        src = entry.get("source_file")
        if not src or not Path(src).exists():
            self._preview_placeholder.setText(
                f"<span style='color:#c53030;'>AOI shapefile not found: "
                f"{src}</span>"
            )
            self._preview_placeholder.setVisible(True)
            self._bci_preview.setVisible(False)
            return

        # Resolve flowline path (only if NHD ran for this AOI).  Fall back
        # to None for manual coord runs so we just draw AOI + stars.
        river_path = entry.get("main_river_line")
        if river_path and not Path(river_path).exists():
            river_path = None

        up_xy = (entry.get("upstream_x"), entry.get("upstream_y"))
        if up_xy[0] is None or up_xy[1] is None:
            up_xy = None
        dn_xy = (entry.get("downstream_x"), entry.get("downstream_y"))
        if dn_xy[0] is None or dn_xy[1] is None:
            dn_xy = None

        # Read the DEM's CRS so the upstream/downstream point coordinates
        # (which are in the DEM/project CRS) can be reprojected to the
        # AOI shapefile CRS before plotting.
        points_crs = None
        dem_path = (
            entry.get("dem_path")
            or self._ctx.get("dem_path")
            or (self._ctx.get("dem_per_aoi") or [{}])[0].get("dem_tif")
        )
        if dem_path and Path(dem_path).exists():
            try:
                import rasterio
                with rasterio.open(dem_path) as _ds:
                    points_crs = _ds.crs
            except Exception:
                pass

        self._bci_preview.show_bci(
            aoi_path=src,
            feature_index=int(entry.get("feature_index") or 0),
            main_river_path=river_path,
            upstream_xy=up_xy,
            downstream_xy=dn_xy,
            title=f"BCI — {entry.get('name', '')}",
            points_crs=points_crs,
        )
        self._preview_placeholder.setVisible(False)
        self._bci_preview.setVisible(True)

    def _on_done(self, ctx):
        self._error_lbl.setVisible(False)
        self._ctx = ctx
        self._progress.setValue(100)
        # Match DEM / Manning wording.
        n = max(len(self._aoi_features), 1)
        self._status_lbl.setText(f"BCI processed for {n} AOI(s)")
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

    def _show_report(self, ctx):
        per_aoi = ctx.get("bci_per_aoi", [])
        if per_aoi:
            rows = ""
            for entry in per_aoi:
                up = ("QFIX" if entry.get("upstream_mode") == "fixed_discharge"
                      else "QVAR")
                dn = entry.get("downstream_type", "")
                river = entry.get("river") or ""
                rows += (
                    f"&nbsp;&nbsp;• <b>{entry['name']}</b>: "
                    f"Up={up}, Down={dn}"
                    + (f", River: {river}" if river else "")
                    + f" → <code>{entry.get('bci_path', '?')}</code><br>"
                )
            self._report.setText(
                f"<b>BC.bci file(s) prepared successfully.</b><br><br>"
                f"<b>Per-AOI outputs:</b><br>{rows}"
            )
            self._report.setVisible(True)
            return

        # Single-AOI report (preserves the old detailed view)
        lisflood_dir  = ctx.get("lisflood_dir", "")
        project_dir   = ctx.get("project_dir", "")
        upstream_mode = ctx.get("upstream_mode", "")
        dn_type       = ctx.get("downstream_type", "")
        bci_path      = ctx.get("bci_path", str(Path(lisflood_dir) / "BC.bci"))
        up_x          = ctx.get("upstream_x", "")
        up_y          = ctx.get("upstream_y", "")
        dn_x          = ctx.get("downstream_x", "")
        dn_y          = ctx.get("downstream_y", "")
        river_name    = ctx.get("main_river_name", "")
        if river_name:
            reach_id   = ctx.get("upstream_reach_id", "n/a")
            nhd_path   = ctx.get("flowlines_path", "")
            river_path = str(Path(project_dir) / "main_river_line.gpkg")
            detect_line = (
                f"<b>River:</b> {river_name}  (NWM reach ID: {reach_id})<br>"
                f"<b>NHD flowlines:</b> {nhd_path}<br>"
                f"<b>Main river line:</b> {river_path}<br>"
            )
        else:
            detect_line = (
                f"<b>Mode:</b> Manual coordinates<br>"
                f"<b>Upstream (X, Y):</b> {up_x}, {up_y}<br>"
                f"<b>Downstream (X, Y):</b> {dn_x}, {dn_y}<br>"
            )

        if upstream_mode == "fixed_discharge":
            q_val = ctx.get("fixed_discharge_cms", "")
            up_line = f"<b>Upstream:</b> Fixed discharge ({q_val} m³/s)<br>"
        else:
            up_line = "<b>Upstream:</b> Varying discharge (QVAR)<br>"

        html = (
            "<b>BC.bci file(s) prepared successfully.</b><br><br>"
            + detect_line
            + up_line
            + f"<b>Downstream:</b> {dn_type}<br>"
            + f"<b>BC.bci saved:</b> {bci_path}"
        )
        self._report.setText(html)
        self._report.setVisible(True)
