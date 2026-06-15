"""Step 6 — Hydrograph / BDY file (LISFLOOD-FP).

Controller that picks the right layout for the BDY step:

  * 1 AOI    → one BDYConfigPanel embedded directly.
  * >1 AOIs  → an accordion of AOIBDYCard widgets, one per AOI, with a
               top "Apply current AOI's settings to all" button.

The Run button dispatches to either ``create_bdy`` (single AOI) or
``run_lisflood_bdy_for_all_aois`` (multi-AOI).  AOIs whose upstream
mode is fixed_discharge are skipped automatically by the orchestrator.

CSV gap-handling pre-flight is run for every AOI that selects a CSV
source before kicking off the worker, so the user can resolve all gaps
up-front instead of being interrupted mid-run.
"""
import re
from pathlib import Path
from typing import List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QProgressBar, QScrollArea, QStackedWidget, QMessageBox,
)
from PyQt6.QtCore import pyqtSignal, Qt

from core.bdy import create_bdy, check_csv_gaps
from core.orchestrate import run_lisflood_bdy_for_all_aois
from gui.worker import Worker
from gui.run_button import set_running, set_ready
from gui.bdy_config_panel import BDYConfigPanel
from gui.aoi_bdy_card import AOIBDYCard
from gui.hydrograph_preview import HydrographPreviewCanvas


_BDY_STEP_RE = re.compile(r"^▶\s+BDY\s+\[(\d+)/(\d+)\]")
_BDY_DONE_RE = re.compile(r"^✓\s+BDY\s+\[(\d+)/(\d+)\]")


class StepBDYWidget(QWidget):
    step_completed = pyqtSignal(dict)

    def __init__(self, log_fn, parent=None):
        super().__init__(parent)
        self._log = log_fn
        self._worker = None
        self._ctx_path = None
        self._ctx = None
        self._aoi_features: list = []
        self._cards: List[AOIBDYCard] = []
        self._setup_ui()

    # ── public API ────────────────────────────────────────────────────────────

    def set_context(self, ctx_path, ctx):
        self._ctx_path = ctx_path
        self._ctx = ctx or {}
        self._aoi_features = list(self._ctx.get("aoi_features", []) or [])

        # Fixed-discharge banner — only meaningful in single-AOI mode.
        # In multi-AOI mode the orchestrator skips per-AOI fixed-discharge
        # entries automatically; we hide the banner.
        if len(self._aoi_features) <= 1:
            is_fixed = self._ctx.get("upstream_mode") == "fixed_discharge"
            self._fixed_note.setVisible(is_fixed)
            self._run_btn.setText(
                "Skip (Fixed Discharge — No BDY Needed)"
                if is_fixed else "Create BDY File"
            )
        else:
            self._fixed_note.setVisible(False)
            self._run_btn.setText("Create BDY File(s)")

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

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        self._fixed_note = QLabel(
            "<b>ℹ️  Fixed discharge selected in Step 5 — no BDY file is needed.</b><br>"
            "Click the button below to mark this step as complete and continue."
        )
        self._fixed_note.setWordWrap(True)
        self._fixed_note.setStyleSheet(
            "padding:10px; background:#fffbeb; border:1px solid #f6e05e; "
            "border-radius:4px;"
        )
        self._fixed_note.setVisible(False)
        layout.addWidget(self._fixed_note)

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
        # when the post-run hydrograph preview is also showing.
        self._stack = QStackedWidget()
        layout.addWidget(self._stack, 1)
        self._stack.setMinimumHeight(340)

        # ── Page 0: single-AOI form ──
        single_page = QWidget()
        sp_layout = QVBoxLayout(single_page)
        sp_layout.setContentsMargins(0, 0, 0, 0)
        gb = QGroupBox("6. Hydrograph / Boundary Conditions Time Series (BC.bdy)")
        gb_layout = QVBoxLayout(gb)
        self._single_panel = BDYConfigPanel(self)
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
            "Copy the currently expanded AOI's BDY configuration to every "
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
        self._run_btn = QPushButton("Create BDY File")
        self._run_btn.setStyleSheet(
            "font-weight:bold; padding:7px 20px; background:#2b6cb0; "
            "color:white; border-radius:4px;"
        )
        self._run_btn.clicked.connect(self._run_step)
        # Hidden until the user picks a data source
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

        self._results_gb = QGroupBox(
            "Per-AOI BDY outputs  —  click an AOI to preview its hydrograph"
        )
        self._results_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        rgl = QVBoxLayout(self._results_gb)
        self._results_inner = QVBoxLayout()
        self._results_inner.setSpacing(0)
        rgl.addLayout(self._results_inner)
        self._results_gb.setVisible(False)
        layout.addWidget(self._results_gb)

        self._gb_preview = QGroupBox("Hydrograph preview")
        self._gb_preview.setMinimumHeight(300)
        pv = QVBoxLayout(self._gb_preview)
        self._preview_placeholder = QLabel(
            "<i>Click an AOI above to plot its discharge hydrograph.</i>"
        )
        self._preview_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_placeholder.setStyleSheet(
            "color:#888; padding:30px; background:#fafafa; "
            "border:1px dashed #cbd5e0; border-radius:4px;"
        )
        self._hydro_preview = HydrographPreviewCanvas(self, width=9, height=3.5)
        self._hydro_preview.setVisible(False)
        pv.addWidget(self._preview_placeholder)
        pv.addWidget(self._hydro_preview)
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

        self._aoi_count_lbl.setText(
            f"<b>{n}</b> AOI(s) confirmed — configure the hydrograph for "
            "each AOI below.  Click an AOI to expand its settings."
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
            card = AOIBDYCard(feat.get("name", "(unnamed)"), self)
            card.expand_requested.connect(self._on_expand_requested)
            card.config_changed.connect(self._on_card_config_changed)
            card.remove_requested.connect(self._on_remove_requested)
            self._cards_layout.insertWidget(
                self._cards_layout.count() - 1, card
            )
            self._cards.append(card)
        # Re-evaluate Run-button visibility (cards start with no source
        # picked, so it stays hidden until every card is ready).
        self._on_card_config_changed(None)

    def _on_remove_requested(self, card):
        from PyQt6.QtWidgets import QMessageBox
        idx = self._cards.index(card) if card in self._cards else -1
        if idx < 0:
            return
        aoi_name = self._aoi_features[idx].get("name", f"AOI {idx+1}") if idx < len(self._aoi_features) else "this AOI"
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
        if idx < len(self._aoi_features):
            self._aoi_features.pop(idx)
        card.setParent(None)
        card.deleteLater()
        # Re-evaluate run button + apply-all button
        self._on_card_config_changed(None)
        # If only 1 AOI left, show count label
        n = len(self._aoi_features)
        if hasattr(self, '_aoi_count_lbl'):
            self._aoi_count_lbl.setText(
                f"<b>{n}</b> AOI(s) remaining — configure each below."
            )

    # ── accordion behaviour ───────────────────────────────────────────────────

    def _on_expand_requested(self, card: AOIBDYCard):
        for c in self._cards:
            if c is card:
                c.expand()
            else:
                c.collapse()
        self._apply_all_btn.setEnabled(True)

    def _expanded_card(self) -> Optional[AOIBDYCard]:
        for c in self._cards:
            if c.is_expanded():
                return c
        return None

    def _on_card_config_changed(self, _card):
        self._apply_all_btn.setEnabled(self._expanded_card() is not None)
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

    # ── csv gap pre-flight ────────────────────────────────────────────────────

    def _csv_preflight(self, file_path: str, interval_hours: float,
                       aoi_label: str = "") -> Optional[str]:
        """Show the gap-handling dialog for a CSV.  Returns the chosen
        gap_handling ("interpolate" / "as_is") or None if cancelled.
        Returns "interpolate" silently when the file has no gaps."""
        try:
            report = check_csv_gaps(file_path, interval_hours)
        except Exception as ex:
            self._error_lbl.setText(
                f"<b>Error reading CSV{(' for ' + aoi_label) if aoi_label else ''}:</b> {ex}"
            )
            self._error_lbl.setVisible(True)
            return None

        if report["ok"]:
            self._log(
                f"CSV gap check passed{(' for ' + aoi_label) if aoi_label else ''} "
                f"— no missing timesteps at {interval_hours:g}h interval."
            )
            return "interpolate"

        n_miss = report["n_missing"]
        sample = report["missing_times"][:10]
        sample_str = ", ".join(f"{t:g}h" for t in sample)
        if n_miss > 10:
            sample_str += f" … ({n_miss} total)"

        prefix = f"AOI '{aoi_label}': " if aoi_label else ""
        body = (
            f"{prefix}The CSV has {report['n_rows']} rows but at a "
            f"{interval_hours:g}-hour interval, {report['n_expected']} are "
            f"expected.\n\n"
            f"{n_miss} timesteps are missing:\n"
            f"{sample_str}\n\n"
            "How would you like to handle the gaps?"
        )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Missing timesteps detected")
        box.setText(body)
        btn_interp = box.addButton(
            "Interpolate missing values", QMessageBox.ButtonRole.AcceptRole
        )
        btn_asis = box.addButton(
            "Skip gaps (write only existing data)",
            QMessageBox.ButtonRole.RejectRole,
        )
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked == btn_interp:
            return "interpolate"
        if clicked == btn_asis:
            return "as_is"
        return None

    # ── run ───────────────────────────────────────────────────────────────────

    def _run_step(self):
        if not self._ctx_path or not self._ctx:
            self._log("Complete earlier steps first.")
            return

        from gui.overwrite_check import confirm_overwrite

        if len(self._aoi_features) <= 1:
            if not confirm_overwrite(self, [self._ctx.get("bdy_path")], "BDY"):
                return
            self._run_single()
        else:
            check = []
            for f in self._aoi_features:
                folder = f.get("folder_path", "")
                if folder:
                    check.append(str(Path(folder) / "BC.bdy"))
            if not confirm_overwrite(self, check, "BDY"):
                return
            self._run_multi()

    def _run_single(self):
        self._clear_results()
        cfg = self._single_panel.get_config()
        bdy_source = cfg["bdy_source"]
        file_path  = cfg.get("file_path") or None
        gap_handling = "interpolate"

        if bdy_source in ("csv", "existing") and not file_path:
            self._error_lbl.setText("Please select a file first.")
            self._error_lbl.setVisible(True)
            return

        if bdy_source == "usgs" and not cfg.get("gage_id"):
            self._error_lbl.setText("Please enter a USGS gage number.")
            self._error_lbl.setVisible(True)
            return

        if bdy_source == "csv" and file_path:
            gh = self._csv_preflight(file_path, float(cfg["interval_hours"]))
            if gh is None:
                return
            gap_handling = gh

        self._error_lbl.setVisible(False)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._status_lbl.setText("Preparing BDY file…")
        self._status_lbl.setStyleSheet("color:#276749; font-weight:bold; font-size:12px; padding:2px 0px;")
        self._status_lbl.setVisible(True)
        set_running(self._run_btn)

        kw = dict(
            ctx_path=self._ctx_path, ctx=self._ctx,
            start_dt=cfg["start_dt"],
            end_dt=cfg["end_dt"],
            interval_hours=float(cfg["interval_hours"]),
            bdy_source=bdy_source,
            existing_bdy_path=file_path if bdy_source == "existing" else None,
            user_csv_path=file_path if bdy_source == "csv" else None,
            gap_handling=gap_handling,
            gage_id=cfg.get("gage_id"),
        )
        self._worker = Worker(create_bdy, **kw)
        self._worker.message.connect(self._on_message)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _run_multi(self):
        self._clear_results()
        per_aoi = []
        for c, feat in zip(self._cards, self._aoi_features):
            cfg = c.get_config()
            src = cfg["bdy_source"]
            file_path = cfg.get("file_path") or None
            if src in ("csv", "existing") and not file_path:
                self._error_lbl.setText(
                    f"AOI '{feat.get('name', '?')}': source is "
                    f"{'CSV' if src == 'csv' else 'existing BDY'} but no "
                    f"file is selected."
                )
                self._error_lbl.setVisible(True)
                return
            gap_handling = "interpolate"
            if src == "csv" and file_path:
                gh = self._csv_preflight(
                    file_path, float(cfg["interval_hours"]),
                    aoi_label=feat.get("name", ""),
                )
                if gh is None:
                    return
                gap_handling = gh
            per_aoi.append({**cfg, "gap_handling": gap_handling})

        self._error_lbl.setVisible(False)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._status_lbl.setText(
            f"Preparing BDY for {len(self._aoi_features)} AOI(s)…"
        )
        self._status_lbl.setVisible(True)
        set_running(self._run_btn)

        self._worker = Worker(
            run_lisflood_bdy_for_all_aois,
            ctx_path=self._ctx_path, ctx=self._ctx,
            per_aoi_configs=per_aoi,
        )
        self._worker.message.connect(self._on_message)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    # ── worker callbacks ──────────────────────────────────────────────────────

    def _on_message(self, msg):
        self._log(msg)

        m = _BDY_STEP_RE.match(msg)
        if m:
            i, total = int(m.group(1)), int(m.group(2))
            self._progress.setValue(0)
            self._status_lbl.setText(f"Preparing BDY {i} / {total} …")
            self._status_lbl.setStyleSheet("color:#276749; font-weight:bold; font-size:12px; padding:2px 0px;")
            return
        m = _BDY_DONE_RE.match(msg)
        if m:
            i, total = int(m.group(1)), int(m.group(2))
            self._progress.setValue(100)
            self._status_lbl.setText(
                f"BDY {i} / {total} finished."
                + (f"  Starting BDY {i + 1} / {total} …"
                   if i < total else "")
            )
            return

        ml = msg.lower()
        if "opening nwm" in ml or "zarr" in ml:
            self._progress.setValue(15)
        elif "extracting streamflow" in ml:
            self._progress.setValue(40)
        elif "retrieved" in ml and "nwm values" in ml:
            self._progress.setValue(70)
        elif "reading discharge" in ml or "reading" in ml:
            self._progress.setValue(30)
        elif "bdy written" in ml or "bdy file copied" in ml:
            self._progress.setValue(95)

    # ─────────────────────────────────────────────────────────────────────────
    # Post-run results: clickable AOI list + hydrograph preview
    # ─────────────────────────────────────────────────────────────────────────

    _SRC_DISPLAY = {
        "NWM": "NWM",
        "NWM Retrospective": "NWM Retrospective",
        "NWM Forecast": "NWM Forecast",
        "nwm": "NWM",
        "nwm_retro": "NWM Retrospective",
        "nwm_forecast": "NWM Forecast",
        "USGS": "USGS",
        "usgs": "USGS",
        "user_table": "CSV",
        "csv": "CSV",
        "user_bdy_copy": "Existing BDY",
        "existing": "Existing BDY",
    }
    _NWM_SRCS = {"NWM", "NWM Retrospective", "NWM Forecast",
                 "nwm", "nwm_retro", "nwm_forecast"}

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
            self._hydro_preview.setVisible(False)
            self._preview_placeholder.setText(
                "<i>Click an AOI above to plot its discharge hydrograph.</i>"
            )
            self._preview_placeholder.setVisible(True)
            self._hydro_preview.clear()

    def _build_results(self, ctx):
        self._clear_results()
        per_aoi = ctx.get("bdy_per_aoi", []) or []
        if not per_aoi:
            f0 = self._aoi_features[0] if self._aoi_features else {}
            proj_name = ctx.get("project_name", "")
            folder = f0.get("folder_path") or ctx.get("project_dir", "")
            single = {
                "name":              ctx.get("aoi_name", f0.get("name", "AOI")),
                "bdy_source":        ctx.get("bdy_source"),
                "written":           ctx.get("bdy_written", False),
                "upstream_reach_id": ctx.get("upstream_reach_id", ""),
                "warnings":          ctx.get("bdy_warnings", []),
                "helper_csv": (
                    str(Path(folder) / f"{proj_name}_upstream_timeseries.csv")
                    if proj_name and folder else None
                ),
            }
            if ctx.get("bdy_path") or ctx.get("bdy_written") is not None:
                per_aoi = [single]
        if not per_aoi:
            return

        for entry in per_aoi:
            name = entry.get("name", "?")
            row = QWidget()
            rl = QVBoxLayout(row)
            rl.setContentsMargins(0, 2, 0, 2)
            rl.setSpacing(1)

            if entry.get("failed"):
                err_short = str(entry.get("error", "unknown error")).split("\n")[0]
                name_lbl = QLabel(f"<b>{name}</b>")
                name_lbl.setStyleSheet("color:#c53030;")
                rl.addWidget(name_lbl)
                err_lbl = QLabel(f"⚠ {err_short}")
                err_lbl.setWordWrap(True)
                err_lbl.setStyleSheet("color:#c53030; font-size:11px;")
                rl.addWidget(err_lbl)

            elif not entry.get("written"):
                name_lbl = QLabel(f"<b>{name}</b>")
                name_lbl.setStyleSheet("color:#2d3748;")
                rl.addWidget(name_lbl)
                note_lbl = QLabel("Fixed discharge upstream — no BDY file needed")
                note_lbl.setStyleSheet(
                    "color:#718096; font-size:11px; font-style:italic;"
                )
                rl.addWidget(note_lbl)

            else:
                src = entry.get("bdy_source", "")
                src_display = self._SRC_DISPLAY.get(src, src or "—")
                reach_id = entry.get("upstream_reach_id") or ""
                if reach_id and src in self._NWM_SRCS:
                    btn_label = f"  {name}  (Feature ID: {reach_id})"
                else:
                    btn_label = f"  {name}"
                btn = QPushButton(btn_label)
                btn.setStyleSheet(
                    "QPushButton { text-align:left; background:transparent; "
                    "border:none; color:#2d3748; font-weight:bold; padding:2px; }"
                    "QPushButton:hover { color:#1a202c; text-decoration:underline; }"
                )
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(
                    lambda _c, e=entry: self._show_hydrograph_for_aoi(e)
                )
                rl.addWidget(btn)

                detail_lbl = QLabel(src_display)
                detail_lbl.setStyleSheet(
                    "color:#718096; font-size:11px; padding-left:4px;"
                )
                rl.addWidget(detail_lbl)

                for w in entry.get("warnings", []):
                    warn_lbl = QLabel(f"⚠ {w}")
                    warn_lbl.setWordWrap(True)
                    warn_lbl.setStyleSheet(
                        "color:#744210; font-size:11px; padding-left:4px;"
                    )
                    rl.addWidget(warn_lbl)

            self._results_inner.addWidget(row)

        self._results_gb.setVisible(True)
        self._gb_preview.setVisible(True)
        self._preview_placeholder.setVisible(True)
        self._hydro_preview.setVisible(False)

    def _show_hydrograph_for_aoi(self, entry: dict):
        csv = entry.get("helper_csv")
        if not csv or not Path(csv).exists():
            self._preview_placeholder.setText(
                f"<span style='color:#c53030;'>Hydrograph CSV not found for "
                f"{entry.get('name', '?')}.</span>"
            )
            self._preview_placeholder.setVisible(True)
            self._hydro_preview.setVisible(False)
            return
        self._hydro_preview.show_hydrograph(
            csv, title=f"Hydrograph — {entry.get('name', '')}",
        )
        self._preview_placeholder.setVisible(False)
        self._hydro_preview.setVisible(True)

    def _on_done(self, ctx):
        self._error_lbl.setVisible(False)
        self._ctx = ctx
        self._progress.setValue(100)
        n = max(len(self._aoi_features), 1)
        self._status_lbl.setText(f"All {n} AOI(s) processed.")
        self._status_lbl.setStyleSheet("color:#276749; font-weight:bold; font-size:12px; padding:2px 0px;")
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

