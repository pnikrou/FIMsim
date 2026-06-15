"""Step 3 — DEM step for LISFLOOD-FP and TRITON.

Two layouts based on how many AOIs are confirmed:

  * 1 AOI    → one ``DEMConfigPanel`` embedded directly.
  * >1 AOIs  → an accordion of ``AOIDEMCard`` widgets (one per AOI), with
               a top "Apply current AOI's settings to all" button so the
               user can broadcast a single source choice + path list to
               every AOI in one click.

Cell size is a study-wide setting (one spin at the top); per-AOI choice is
just Download from 3DEP vs I have a DEM raster + the file paths.
"""
import re
from pathlib import Path
from typing import List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QGroupBox,
    QProgressBar, QScrollArea, QStackedWidget, QMessageBox,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView,
)
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QFont as _QFont


def _bold_font() -> _QFont:
    f = _QFont()
    f.setBold(True)
    return f

from core.orchestrate import run_lisflood_triton_dem_all
from core.multi_aoi import AOIFeatureInfo
from gui.worker import Worker
from gui.run_button import set_running, set_ready
from gui.dem_config_panel import DEMConfigPanel
from gui.aoi_dem_card import AOIDEMCard
from gui.raster_preview import RasterPreviewCanvas


_DOWNLOADING_RE = re.compile(r"^▶\s+Downloading DEM\s+\[(\d+)/(\d+)\]")
_FINISHED_RE    = re.compile(r"^✓\s+DEM\s+\[(\d+)/(\d+)\]\s+finished")


def _features_from_ctx(ctx) -> list:
    """Reconstruct AOIFeatureInfo objects from the dicts stored in ctx."""
    out = []
    for d in (ctx or {}).get("aoi_features", []) or []:
        try:
            allowed = {
                "source_file", "feature_index", "name", "area_km2",
                "centroid_lon", "centroid_lat", "state_name", "state_abbr",
                "river_name", "folder_name", "folder_path",
                "huc6_codes", "huc8_codes", "usgs_gages",
            }
            kw = {k: v for k, v in d.items() if k in allowed}
            out.append(AOIFeatureInfo(**kw))
        except Exception:
            continue
    return out


class StepDEMWidget(QWidget):
    step_completed = pyqtSignal(dict)

    def __init__(self, log_fn, parent=None):
        super().__init__(parent)
        self._log = log_fn
        self._worker = None
        self._ctx_path = None
        self._ctx = None
        self._features: List[AOIFeatureInfo] = []
        self._cards: List[AOIDEMCard] = []
        self._setup_ui()

    # ── public API ───────────────────────────────────────────────────────────

    def set_context(self, ctx_path, ctx):
        self._ctx_path = ctx_path
        self._ctx = ctx
        self._features = _features_from_ctx(ctx)
        self._clear_results()
        self._rebuild_for_aoi_count()

    def reset(self):
        self._features = []
        self._clear_cards()
        self._clear_results()
        self._aoi_count_lbl.setVisible(False)
        self._progress.setValue(0)
        self._progress.setVisible(False)
        self._status_lbl.setVisible(False)
        self._error_lbl.setVisible(False)
        try:
            set_ready(self._run_btn)
        except Exception:
            self._run_btn.setEnabled(True)
        self._stack.setCurrentIndex(0)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # AOI count line
        self._aoi_count_lbl = QLabel("")
        self._aoi_count_lbl.setStyleSheet(
            "padding:6px 10px; background:#f7fafc; border:1px solid #cbd5e0; "
            "border-radius:4px; color:#2d3748; font-size:11px;"
        )
        self._aoi_count_lbl.setWordWrap(True)
        self._aoi_count_lbl.setVisible(False)
        layout.addWidget(self._aoi_count_lbl)

        # Stack: single-AOI form vs multi-AOI accordion.  Stretch factor 1
        # so the accordion (or single-AOI form) claims spare vertical
        # space — otherwise the post-run results + preview groups below
        # squeeze the cards too small to interact with when the user
        # navigates back here to edit.
        # (cell size now lives inside each AOI's DEMConfigPanel — see
        #  dem_config_panel.py — so different AOIs can use different
        #  resolutions.)
        self._stack = QStackedWidget()
        layout.addWidget(self._stack, 1)
        # Same min-height as the Manning step so the accordion stays
        # roomy when the user navigates back here after a run (the
        # post-run report + results list + preview group below would
        # otherwise squeeze the expanded card).
        self._stack.setMinimumHeight(420)

        # ── Page 0: single AOI ──
        single_page = QWidget()
        sp_layout = QVBoxLayout(single_page)
        sp_layout.setContentsMargins(0, 0, 0, 0)
        gb = QGroupBox("3. Digital Elevation Model (DEM)")
        gb_layout = QVBoxLayout(gb)
        self._single_panel = DEMConfigPanel(self)
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
            "Copy the currently expanded AOI's DEM source choice "
            "(and file paths if any) to every other AOI in this list."
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
        self._run_btn = QPushButton("Prepare DEM(s)")
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

        # ── Post-run results: clickable AOI list + raster preview ────────
        # Hidden until a successful run completes.  No map / info shown
        # *during* the run — only the blue status banner + progress bar.
        self._results_gb = QGroupBox("Per-AOI DEM outputs  —  click an AOI to preview its DEM")
        self._results_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        rgl = QVBoxLayout(self._results_gb)
        self._results_inner = QVBoxLayout()
        self._results_inner.setSpacing(0)
        rgl.addLayout(self._results_inner)
        self._results_gb.setVisible(False)
        layout.addWidget(self._results_gb)

        self._gb_preview = QGroupBox("DEM preview")
        self._gb_preview.setMinimumHeight(360)
        pv = QVBoxLayout(self._gb_preview)

        self._preview_placeholder = QLabel(
            "<i>Click an AOI above to preview its DEM here.</i>"
        )
        self._preview_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_placeholder.setStyleSheet(
            "color:#888; padding:30px; background:#fafafa; "
            "border:1px dashed #cbd5e0; border-radius:4px;"
        )
        pv.addWidget(self._preview_placeholder)

        # Two-column view: left = DEM info table (~28%), right = raster (~72%)
        self._preview_2col = QWidget()
        h2 = QHBoxLayout(self._preview_2col)
        h2.setContentsMargins(0, 0, 0, 0)
        h2.setSpacing(10)

        # Left: info table
        info_col = QVBoxLayout()
        info_col.setSpacing(4)
        info_hdr = QLabel("<b>DEM Information</b>")
        info_hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info_hdr.setStyleSheet(
            "color:#2c5282; font-size:10px; padding-bottom:2px;"
        )
        info_col.addWidget(info_hdr)

        self._info_table = QTableWidget()
        self._info_table.setColumnCount(2)
        self._info_table.horizontalHeader().setVisible(False)
        self._info_table.verticalHeader().setVisible(False)
        self._info_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._info_table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        self._info_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._info_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self._info_table.verticalHeader().setDefaultSectionSize(22)
        self._info_table.setStyleSheet(
            "QTableWidget { font-size:10px; border:1px solid #e2e8f0; }"
            "QTableWidget::item { padding:1px 4px; }"
        )
        self._info_table.setAlternatingRowColors(True)
        info_col.addWidget(self._info_table, 1)
        h2.addLayout(info_col, 3)

        # Right: raster canvas
        self._raster_preview = RasterPreviewCanvas(self, width=9, height=3.8)
        h2.addWidget(self._raster_preview, 7)

        self._preview_2col.setVisible(False)
        pv.addWidget(self._preview_2col, 1)

        self._gb_preview.setVisible(False)
        layout.addWidget(self._gb_preview)

    # ── layout switching ──────────────────────────────────────────────────────

    def _rebuild_for_aoi_count(self):
        n = len(self._features)
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
            f"<b>{n}</b> AOI(s) confirmed — pick a DEM source for each AOI "
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
        for feat in self._features:
            card = AOIDEMCard(feat.name, self)
            card.expand_requested.connect(self._on_expand_requested)
            card.config_changed.connect(self._on_card_config_changed)
            card.remove_requested.connect(self._on_remove_requested)
            self._cards_layout.insertWidget(
                self._cards_layout.count() - 1, card
            )
            self._cards.append(card)
        self._on_card_config_changed(None)

    def _on_remove_requested(self, card):
        from PyQt6.QtWidgets import QMessageBox
        idx = self._cards.index(card) if card in self._cards else -1
        if idx < 0:
            return
        aoi_name = self._features[idx].name if idx < len(self._features) else "this AOI"
        reply = QMessageBox.question(
            self, "Remove AOI",
            f"Remove <b>{aoi_name}</b> from this step?\n\n"
            "The AOI's data folder is NOT deleted — only removed from the current run.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        # Remove from both lists
        self._cards.pop(idx)
        if idx < len(self._features):
            self._features.pop(idx)
        card.setParent(None)
        card.deleteLater()
        # Re-evaluate run button + apply-all button
        self._on_card_config_changed(None)
        # If only 1 AOI left, show count label
        n = len(self._features)
        if hasattr(self, '_aoi_count_lbl'):
            self._aoi_count_lbl.setText(
                f"<b>{n}</b> AOI(s) remaining — configure each below."
            )

    # ── accordion behaviour ───────────────────────────────────────────────────

    def _on_expand_requested(self, card: AOIDEMCard):
        for c in self._cards:
            if c is card:
                c.expand()
            else:
                c.collapse()
        self._apply_all_btn.setEnabled(True)

    def _expanded_card(self) -> Optional[AOIDEMCard]:
        for c in self._cards:
            if c.is_expanded():
                return c
        return None

    def _on_card_config_changed(self, _card):
        all_ready = bool(self._cards) and all(c.is_ready() for c in self._cards)
        self._run_btn.setVisible(all_ready)
        self._apply_all_btn.setEnabled(self._expanded_card() is not None)

    def _apply_to_all(self):
        src = self._expanded_card()
        if src is None:
            QMessageBox.information(
                self, "Pick an AOI to copy from",
                "Click on the AOI whose DEM source you want to broadcast first, "
                "then click 'Apply current AOI's settings to all'.",
            )
            return
        cfg = src.get_config()
        for c in self._cards:
            if c is src:
                continue
            c.set_config(cfg)
        self._on_card_config_changed(None)

    def _on_single_config_changed(self):
        if self._stack.currentIndex() == 0 and len(self._features) <= 1:
            self._run_btn.setVisible(self._single_panel.is_ready())

    # ── post-run results panel ────────────────────────────────────────────────

    def _clear_results(self):
        """Remove the per-AOI clickable rows + hide the preview canvas."""
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
            self._preview_2col.setVisible(False)
            self._raster_preview.clear()

    def _build_results(self, ctx):
        """Populate the clickable AOI list from ctx['dem_per_aoi']."""
        from PyQt6.QtWidgets import QFrame as _QFrame
        from PyQt6.QtCore import Qt as _Qt

        self._clear_results()
        per_aoi = ctx.get("dem_per_aoi", []) or []
        # Single-AOI fallback — synthesise one row from the bridge keys
        if not per_aoi:
            single_path = ctx.get("dem_tif_path") or ctx.get("dem_path")
            if single_path:
                per_aoi = [{
                    "name": ctx.get("aoi_name", "AOI"),
                    "dem_tif": single_path,
                }]
        if not per_aoi:
            return

        for entry in per_aoi:
            name = entry.get("name", "?")
            path = entry.get("dem_tif", "")
            row = _QFrame()
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
            btn.setCursor(_Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(
                lambda _checked, n=name, p=path: self._show_raster_for_aoi(n, p)
            )
            rl.addWidget(btn, 1)
            self._results_inner.addWidget(row)
        self._results_gb.setVisible(True)
        self._gb_preview.setVisible(True)
        self._preview_placeholder.setVisible(True)
        self._preview_2col.setVisible(False)

    def _show_raster_for_aoi(self, name: str, path: str):
        if not path or not Path(path).exists():
            self._preview_placeholder.setText(
                f"<span style='color:#c53030;'>DEM file not found: {path}</span>"
            )
            self._preview_placeholder.setVisible(True)
            self._preview_2col.setVisible(False)
            return
        self._raster_preview.show_raster(
            path, title=f"DEM — {name}",
            cmap="terrain", colorbar_label="Elevation (m)",
        )
        self._populate_dem_info(name, path)
        self._preview_placeholder.setVisible(False)
        self._preview_2col.setVisible(True)

    def _populate_dem_info(self, name: str, path: str):
        """Fill the info table with DEM metadata."""
        rows = [("AOI", name)]
        try:
            import rasterio
            with rasterio.open(path) as src:
                rows.append(("CRS", src.crs.to_string() if src.crs else "—"))
                rows.append(("Width × Height", f"{src.width} × {src.height} px"))
                res_x = abs(src.transform.a)
                res_y = abs(src.transform.e)
                rows.append(("Resolution", f"{res_x:.2f} × {res_y:.2f} m"))
                b = src.bounds
                rows.append(("Bounds (W, S)", f"{b.left:.3f},  {b.bottom:.3f}"))
                rows.append(("Bounds (E, N)", f"{b.right:.3f},  {b.top:.3f}"))
                rows.append(("File", Path(path).name))
        except Exception as ex:
            rows.append(("Error", str(ex)[:60]))

        self._info_table.setRowCount(len(rows))
        for r, (key, val) in enumerate(rows):
            ki = QTableWidgetItem(key)
            ki.setFont(_bold_font())
            vi = QTableWidgetItem(str(val))
            self._info_table.setItem(r, 0, ki)
            self._info_table.setItem(r, 1, vi)
        self._info_table.resizeRowsToContents()

    # ── progress / log handling ───────────────────────────────────────────────

    def _on_message(self, msg):
        self._log(msg)

        m = _DOWNLOADING_RE.match(msg)
        if m:
            i, total = int(m.group(1)), int(m.group(2))
            self._progress.setValue(0)
            if 1 <= i <= len(self._features):
                feat = self._features[i - 1]
                self._status_lbl.setText(
                    f"Downloading DEM {i} / {total} — <i>{feat.name}</i>"
                )
                self._status_lbl.setVisible(True)
            return

        m = _FINISHED_RE.match(msg)
        if m:
            i, total = int(m.group(1)), int(m.group(2))
            self._progress.setValue(100)
            self._status_lbl.setText(
                f"DEM {i} / {total} finished."
                + (f"  Starting DEM {i + 1} / {total} …"
                   if i < total else "")
            )
            return

        m = re.search(r"Download progress:\s*(\d+)/(\d+)", msg)
        if m:
            done, total = int(m.group(1)), int(m.group(2))
            if total > 0:
                self._progress.setValue(int(done / total * 70))
        elif "Converting DEM to ASCII" in msg:
            self._progress.setValue(80)
        elif "DEM ASCII saved" in msg or "DEM step complete" in msg:
            self._progress.setValue(95)
        elif "Downloading DEM from 3DEP" in msg or "Using provided DEM" in msg:
            self._progress.setValue(5)

    # ── run ───────────────────────────────────────────────────────────────────

    def _run_step(self):
        if not self._ctx_path or not self._ctx:
            self._log("Complete Steps 1 and 2 first.")
            return
        if not self._features:
            self._error_lbl.setText(
                "No AOIs are confirmed.  Go back to the AOI step and "
                "confirm at least one feature first."
            )
            self._error_lbl.setVisible(True)
            return

        from gui.overwrite_check import confirm_overwrite

        # Overwrite check on FIRST AOI's folder (pragmatic — orchestrator
        # clobbers existing per-AOI files).
        f0 = self._features[0]
        check_files = [
            str(Path(f0.folder_path) / "dem.ascii"),
            str(Path(f0.folder_path) / "dem.asc"),
        ]
        if not confirm_overwrite(self, check_files, "DEM"):
            return

        # Build per-AOI configs
        if len(self._features) <= 1:
            cfg = self._single_panel.get_config()
            per_aoi = [cfg]
        else:
            per_aoi = [c.get_config() for c in self._cards]

        # Sanity check — uploaded paths must exist.
        for cfg, feat in zip(per_aoi, self._features):
            if cfg.get("has_dem"):
                paths = cfg.get("user_dem_path") or []
                if not paths:
                    self._error_lbl.setText(
                        f"AOI '{feat.name}' is set to 'I have a DEM "
                        f"raster' but no file was selected."
                    )
                    self._error_lbl.setVisible(True)
                    return
                for p in paths:
                    if not Path(p).exists():
                        self._error_lbl.setText(
                            f"DEM file not found for AOI '{feat.name}': {p}"
                        )
                        self._error_lbl.setVisible(True)
                        return

        self._error_lbl.setVisible(False)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._status_lbl.setVisible(False)
        set_running(self._run_btn)

        # Pass the FIRST AOI's cell size as the orchestrator's default;
        # the orchestrator now reads each AOI's own dem_res_m from
        # per_aoi_configs and that overrides this default.
        first_res = float(per_aoi[0].get("dem_res_m", 10.0)) if per_aoi else 10.0
        self._worker = Worker(
            run_lisflood_triton_dem_all,
            ctx_path=self._ctx_path, ctx=self._ctx,
            dem_res_m=first_res,
            per_aoi_configs=per_aoi,
        )
        self._worker.message.connect(self._on_message)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_done(self, ctx):
        self._error_lbl.setVisible(False)
        self._ctx = ctx
        self._progress.setValue(100)
        n = len(self._features)
        self._status_lbl.setText(f"DEM processed for {n} AOI(s)")
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

