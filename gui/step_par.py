"""Step 7 — PAR file builder.

Two layouts based on how many AOIs are confirmed:

  * 1 AOI    → one PARConfigPanel embedded directly.
  * >1 AOIs  → an accordion of AOIPARCard widgets + a top
               "Apply current AOI's settings to all" button.

The Run button dispatches to either ``create_par`` (single AOI) or
``run_lisflood_par_for_all_aois`` (multi-AOI).
"""
import re
from pathlib import Path
from typing import List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QGroupBox,
    QProgressBar, QScrollArea, QStackedWidget, QMessageBox,
)
from PyQt6.QtCore import pyqtSignal

from core.par import create_par
from core.orchestrate import run_lisflood_par_for_all_aois
from gui.worker import Worker
from gui.run_button import set_running, set_ready
from gui.par_config_panel import PARConfigPanel
from gui.aoi_par_card import AOIPARCard


_PAR_STEP_RE = re.compile(r"^▶\s+PAR\s+\[(\d+)/(\d+)\]")
_PAR_DONE_RE = re.compile(r"^✓\s+PAR\s+\[(\d+)/(\d+)\]")


class StepPARWidget(QWidget):
    step_completed = pyqtSignal(dict)

    def __init__(self, log_fn, parent=None):
        super().__init__(parent)
        self._log = log_fn
        self._worker = None
        self._ctx_path = None
        self._ctx = None
        self._aoi_features: list = []
        self._cards: List[AOIPARCard] = []
        self._setup_ui()

    # ── public API ────────────────────────────────────────────────────────────

    def set_context(self, ctx_path, ctx):
        self._ctx_path = ctx_path
        self._ctx = ctx or {}
        self._aoi_features = list(self._ctx.get("aoi_features", []) or [])
        self._rebuild_for_aoi_count()

    def reset(self):
        self._aoi_features = []
        self._clear_cards()
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

        self._aoi_count_lbl = QLabel("")
        self._aoi_count_lbl.setStyleSheet(
            "padding:6px 10px; background:#f7fafc; border:1px solid #cbd5e0; "
            "border-radius:4px; color:#2d3748; font-size:11px;"
        )
        self._aoi_count_lbl.setWordWrap(True)
        self._aoi_count_lbl.setVisible(False)
        layout.addWidget(self._aoi_count_lbl)

        # Stretch factor 1 + a generous min-height — PAR's per-AOI panel
        # has many groups (timing, solver, output options, …) so an
        # expanded card needs real room to stay usable.
        self._stack = QStackedWidget()
        layout.addWidget(self._stack, 1)
        self._stack.setMinimumHeight(500)

        # Page 0 — single AOI
        single_page = QWidget()
        sp_layout = QVBoxLayout(single_page)
        sp_layout.setContentsMargins(0, 0, 0, 0)
        gb = QGroupBox("7. PAR file")
        gb_layout = QVBoxLayout(gb)
        self._single_panel = PARConfigPanel(self)
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
            "Copy the currently expanded AOI's PAR configuration to every "
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
        self._run_btn = QPushButton("Write PAR File")
        self._run_btn.setStyleSheet(
            "font-weight:bold; padding:7px 20px; background:#2b6cb0; "
            "color:white; border-radius:4px;"
        )
        self._run_btn.clicked.connect(self._run_step)
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
            "padding:12px; background:#ebf8ff; border:1px solid #63b3ed; "
            "border-radius:4px; font-size:12px;"
        )
        self._report.setVisible(False)
        layout.addWidget(self._report)

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
            # Pre-fill from the single AOI's ctx (project name, sim_time)
            self._single_panel.apply_ctx_defaults(self._ctx)
            self._run_btn.setVisible(self._single_panel.is_ready())
            return

        self._aoi_count_lbl.setText(
            f"<b>{n}</b> AOI(s) confirmed — configure the PAR file for "
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
            card = AOIPARCard(feat.get("name", "(unnamed)"), self)
            card.expand_requested.connect(self._on_expand_requested)
            card.config_changed.connect(self._on_card_config_changed)

            # Pre-fill each card from this AOI's per-AOI ctx (DEM / BCI /
            # BDY paths so sim_time can be inferred).
            try:
                folder = feat.get("folder_path", "")
                per_ctx_path = Path(folder) / "workflow_context.json"
                if per_ctx_path.exists():
                    import json
                    with open(per_ctx_path, "r", encoding="utf-8") as fr:
                        saved = json.load(fr)
                    card.apply_ctx_defaults(saved)
                else:
                    card.apply_ctx_defaults(self._ctx)
            except Exception:
                card.apply_ctx_defaults(self._ctx)

            self._cards_layout.insertWidget(
                self._cards_layout.count() - 1, card
            )
            self._cards.append(card)
        self._on_card_config_changed(None)

    # ── accordion behaviour ───────────────────────────────────────────────────

    def _on_expand_requested(self, card: AOIPARCard):
        for c in self._cards:
            if c is card:
                c.expand()
            else:
                c.collapse()
        self._apply_all_btn.setEnabled(True)

    def _expanded_card(self) -> Optional[AOIPARCard]:
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
            if not confirm_overwrite(self, [self._ctx.get("par_path")], "PAR"):
                set_ready(self._run_btn)
                self._progress.setVisible(False)
                return
            self._run_single()
        else:
            check = []
            for f in self._aoi_features:
                folder = f.get("folder_path", "")
                if folder:
                    proj = self._ctx.get("project_name", "model")
                    check.append(str(Path(folder) / f"{proj}.par"))
            if not confirm_overwrite(self, check, "PAR"):
                set_ready(self._run_btn)
                self._progress.setVisible(False)
                return
            self._run_multi()

    def _run_single(self):
        cfg = self._single_panel.get_config()
        self._status_lbl.setText("Writing PAR file…")
        self._status_lbl.setStyleSheet("color:#276749; font-weight:bold; font-size:12px; padding:2px 0px;")
        self._status_lbl.setVisible(True)
        self._worker = Worker(
            create_par,
            ctx_path=self._ctx_path, ctx=self._ctx,
            **cfg,
        )
        self._worker.message.connect(self._on_message)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _run_multi(self):
        per_aoi = [c.get_config() for c in self._cards]
        self._status_lbl.setText(
            f"Writing PAR for {len(self._aoi_features)} AOI(s)…"
        )
        self._status_lbl.setVisible(True)
        self._worker = Worker(
            run_lisflood_par_for_all_aois,
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

        m = _PAR_STEP_RE.match(msg)
        if m:
            i, total = int(m.group(1)), int(m.group(2))
            self._progress.setValue(0)
            self._status_lbl.setText(f"Writing PAR {i} / {total} …")
            self._status_lbl.setStyleSheet("color:#276749; font-weight:bold; font-size:12px; padding:2px 0px;")
            return
        m = _PAR_DONE_RE.match(msg)
        if m:
            i, total = int(m.group(1)), int(m.group(2))
            self._progress.setValue(100)
            self._status_lbl.setText(
                f"PAR {i} / {total} finished."
                + (f"  Starting PAR {i + 1} / {total} …"
                   if i < total else "")
            )
            return
        if "par written" in msg.lower() or "par file written" in msg.lower():
            self._progress.setValue(100)

    def _on_done(self, ctx):
        self._error_lbl.setVisible(False)
        self._ctx = ctx
        self._progress.setValue(100)
        n = max(len(self._aoi_features), 1)
        self._status_lbl.setText(f"All {n} AOI(s) processed.")
        self._status_lbl.setStyleSheet("color:#276749; font-weight:bold; font-size:12px; padding:2px 0px;")
        set_ready(self._run_btn)
        self._show_report(ctx)
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

    # ── report ────────────────────────────────────────────────────────────────

    def _show_report(self, ctx):
        per_aoi = ctx.get("par_per_aoi", []) or []
        if per_aoi:
            rows = ""
            for entry in per_aoi:
                rows += (
                    f"&nbsp;&nbsp;• <b>{entry['name']}</b> → "
                    f"<code>{entry.get('par_path', '?')}</code><br>"
                )
            html = (
                "<b>PAR file(s) prepared successfully — all "
                "preprocessing steps complete!</b><br><br>"
                "<b>Per-AOI PAR files:</b><br>"
                + rows +
                "<br><b>To run a simulation:</b><br>"
                "<code>lisflood -v &lt;par-file&gt;</code>"
            )
            self._report.setText(html)
            self._report.setVisible(True)
            self._log("All steps complete! LISFLOOD-FP input files are ready.")
            return

        # ── Single-AOI report (the rich version that lists every input file)
        lisflood_dir = ctx.get("lisflood_dir", "")
        project_dir  = ctx.get("project_dir", "")
        project_name = ctx.get("project_name", "")

        def _file_line(label, path):
            p = Path(path) if path else None
            exists = p and p.exists()
            icon = "" if exists else ""
            return f"{icon} <b>{label}:</b> {path}<br>"

        par_path   = ctx.get("par_path",          str(Path(lisflood_dir) / f"{project_name}.par"))
        dem_ascii  = ctx.get("dem_ascii_path",    str(Path(lisflood_dir) / "dem.ascii"))
        lulc_ascii = ctx.get("manning_ascii_path", str(Path(lisflood_dir) / "lulc.ascii"))
        bci_path   = ctx.get("bci_path",          str(Path(lisflood_dir) / "BC.bci"))
        bdy_path   = ctx.get("bdy_path", "")
        bdy_written = ctx.get("bdy_written", True)
        use_manningfile = ctx.get("par_use_manningfile", False)
        fpfric          = ctx.get("par_fpfric")

        lines = [_file_line("PAR file", par_path), _file_line("DEM ASCII grid", dem_ascii)]
        if use_manningfile:
            lines.append(_file_line("Manning n ASCII grid", lulc_ascii))
        else:
            lines.append(
                f"<b>Manning n:</b> Fixed value ({fpfric}) — "
                "written into PAR file<br>"
            )
        lines.append(_file_line("BCI boundary conditions", bci_path))
        if bdy_written and bdy_path:
            lines.append(_file_line("BDY hydrograph", bdy_path))
        else:
            lines.append("<b>BDY hydrograph:</b> Not required (fixed discharge)<br>")

        helper_csv = Path(project_dir) / f"{project_name}_upstream_timeseries.csv"
        if helper_csv.exists():
            lines.append(f"📊 <b>Discharge timeseries CSV:</b> {helper_csv}<br>")

        html = (
            "<b>All preprocessing steps complete!</b><br><br>"
            "All LISFLOOD-FP input files are ready in:<br>"
            f"<b>{lisflood_dir}</b><br><br>"
            "<b>Required input files:</b><br>"
            + "".join(lines) +
            "<br><b>To run the simulation:</b><br>"
            f"<code>lisflood -v {par_path}</code>"
        )
        self._report.setText(html)
        self._report.setVisible(True)
        self._log(f"All steps complete! Files in: {lisflood_dir}")
