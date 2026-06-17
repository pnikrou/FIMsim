"""Step 6 — Hydrograph (.hyg) — TRITON.

Multi-AOI controller, mirroring the LISFLOOD BDY step.  This version assumes a
single inflow per AOI, so each AOI just picks one discharge source:

  * 1 AOI   → one BDYConfigPanel embedded directly.
  * >1 AOI  → an accordion of AOIBDYCard widgets (one per AOI) + Apply-to-all.

Source options are identical to the LISFLOOD BDY step (NWM Retro / NWM Forecast
/ USGS / Upload CSV), so we reuse those widgets.  Both single- and multi-AOI
runs go through ``run_triton_hydro_for_all_aois`` which fetches the discharge
(reusing the BDY fetchers) and writes each AOI's ``<AOI>.hyg``.
"""
import re
from pathlib import Path
from typing import List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QProgressBar, QScrollArea, QStackedWidget, QMessageBox,
)
from PyQt6.QtCore import pyqtSignal, Qt

from core.orchestrate import run_triton_hydro_for_all_aois
from gui.worker import Worker
from gui.run_button import set_running, set_ready
from gui.bdy_config_panel import BDYConfigPanel
from gui.aoi_bdy_card import AOIBDYCard
from gui.hydrograph_preview import HydrographPreviewCanvas


_HYG_STEP_RE = re.compile(r"^▶\s+Hydrograph\s+\[(\d+)/(\d+)\]")
_HYG_DONE_RE = re.compile(r"^✓\s+Hydrograph\s+\[(\d+)/(\d+)\]")


class StepTritonHydroWidget(QWidget):
    step_completed = pyqtSignal(dict)

    def __init__(self, log_fn, parent=None):
        super().__init__(parent)
        self._log = log_fn
        self._worker = None
        self._ctx_path = None
        self._ctx = None
        self._aoi_features: list = []
        self._stack: QStackedWidget = None        # type: ignore[assignment]
        self._single_panel: BDYConfigPanel = None  # type: ignore[assignment]
        self._cards: List[AOIBDYCard] = []
        self._cards_layout: QVBoxLayout = None    # type: ignore[assignment]
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
        if self._single_panel is not None:
            self._single_panel.set_config({"bdy_source": ""})
        self._error_lbl.setVisible(False)
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

        self._stack = QStackedWidget()
        layout.addWidget(self._stack, 1)
        self._stack.setMinimumHeight(340)

        single_page = QWidget()
        sp_layout = QVBoxLayout(single_page)
        sp_layout.setContentsMargins(0, 0, 0, 0)
        gb = QGroupBox("6. Hydrograph / inflow discharge (.hyg)")
        gb_layout = QVBoxLayout(gb)
        self._single_panel = BDYConfigPanel(self)
        self._single_panel.config_changed.connect(self._on_single_config_changed)
        gb_layout.addWidget(self._single_panel)
        sp_layout.addWidget(gb)
        sp_layout.addStretch()
        self._stack.addWidget(single_page)

        multi_page = QWidget()
        mp_layout = QVBoxLayout(multi_page)
        mp_layout.setContentsMargins(0, 0, 0, 0)
        top_row = QHBoxLayout()
        self._apply_all_btn = QPushButton("Apply current AOI's settings to all")
        self._apply_all_btn.setStyleSheet(
            "background:#2b6cb0; color:white; padding:6px 14px; "
            "border-radius:3px; font-weight:bold;"
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

        btn_row = QHBoxLayout()
        self._run_btn = QPushButton("Write .hyg file(s)")
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

        # Results list (simple clickable lines) + hydrograph preview
        self._results_gb = QGroupBox(
            "Per-AOI hydrographs  —  click an AOI to preview"
        )
        rgl = QVBoxLayout(self._results_gb)
        self._results_inner = QVBoxLayout()
        self._results_inner.setSpacing(1)
        rgl.addLayout(self._results_inner)
        self._results_gb.setVisible(False)
        layout.addWidget(self._results_gb)

        self._gb_preview = QGroupBox("Hydrograph preview")
        pv = QVBoxLayout(self._gb_preview)
        self._preview_placeholder = QLabel(
            "<i>Click an AOI above to preview its hydrograph.</i>"
        )
        self._preview_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_placeholder.setStyleSheet(
            "color:#888; padding:24px; background:#fafafa; "
            "border:1px dashed #cbd5e0; border-radius:4px;"
        )
        pv.addWidget(self._preview_placeholder)
        self._hydro_preview = HydrographPreviewCanvas(self, width=9, height=3.0)
        self._hydro_preview.setVisible(False)
        pv.addWidget(self._hydro_preview)
        self._gb_preview.setVisible(False)
        layout.addWidget(self._gb_preview)

    # ── layout switching ───────────────────────────────────────────────────────

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
            f"<b>{n}</b> AOI(s) confirmed — choose the discharge source for each "
            "AOI below."
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
            self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)
            self._cards.append(card)
        self._on_card_config_changed(None)

    def _on_remove_requested(self, card):
        idx = self._cards.index(card) if card in self._cards else -1
        if idx < 0:
            return
        aoi_name = (self._aoi_features[idx].get("name", f"AOI {idx+1}")
                    if idx < len(self._aoi_features) else "this AOI")
        if QMessageBox.question(
            self, "Remove AOI", f"Remove <b>{aoi_name}</b> from this step?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        self._cards.pop(idx)
        if idx < len(self._aoi_features):
            self._aoi_features.pop(idx)
        card.setParent(None)
        card.deleteLater()
        self._on_card_config_changed(None)

    def _on_expand_requested(self, card):
        for c in self._cards:
            c.expand() if c is card else c.collapse()
        self._apply_all_btn.setEnabled(True)

    def _expanded_card(self) -> Optional[AOIBDYCard]:
        for c in self._cards:
            if c.is_expanded():
                return c
        return None

    def _card_ready(self, c) -> bool:
        try:
            return c.panel().is_ready()
        except Exception:
            return False

    def _on_card_config_changed(self, _card):
        all_ready = bool(self._cards) and all(self._card_ready(c) for c in self._cards)
        self._run_btn.setVisible(all_ready)
        self._apply_all_btn.setEnabled(self._expanded_card() is not None)

    def _apply_to_all(self):
        src = self._expanded_card()
        if src is None:
            return
        cfg = src.panel().get_config()
        for c in self._cards:
            if c is not src:
                c.panel().set_config(cfg)
        self._on_card_config_changed(None)

    def _on_single_config_changed(self):
        if self._stack.currentIndex() == 0 and len(self._aoi_features) <= 1:
            self._run_btn.setVisible(self._single_panel.is_ready())

    # ── run ────────────────────────────────────────────────────────────────────

    def _run_step(self):
        if not self._ctx_path or not self._ctx:
            self._log("Complete earlier steps first.")
            return
        if len(self._aoi_features) <= 1:
            cfgs = [self._single_panel.get_config()]
        else:
            cfgs = [c.panel().get_config() for c in self._cards]
        self._error_lbl.setVisible(False)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        set_running(self._run_btn)
        self._status_lbl.setText(
            f"Preparing hydrographs for {max(len(self._aoi_features), 1)} AOI(s)…"
        )
        self._status_lbl.setVisible(True)
        self._worker = Worker(
            run_triton_hydro_for_all_aois,
            ctx_path=self._ctx_path, ctx=self._ctx, per_aoi_configs=cfgs,
        )
        self._worker.message.connect(self._on_message)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_message(self, msg):
        self._log(msg)
        ml = msg.lower()
        m = _HYG_STEP_RE.match(msg)
        if m:
            self._progress.setValue(15)
            self._status_lbl.setText(f"Hydrograph {m.group(1)} / {m.group(2)} …")
            return
        m = _HYG_DONE_RE.match(msg)
        if m:
            self._progress.setValue(100)
            return
        if "extracting streamflow" in ml or "downloading usgs" in ml:
            self._progress.setValue(45)
        elif "retrieved" in ml or "saved" in ml or ".hyg written" in ml:
            self._progress.setValue(80)

    # ── results + preview ──────────────────────────────────────────────────────

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
            self._hydro_preview.setVisible(False)
            self._hydro_preview.clear()

    def _build_results(self, ctx):
        self._clear_results()
        per_aoi = ctx.get("triton_hydro_per_aoi", []) or []
        if not per_aoi and ctx.get("triton_hydro_helper_csv"):
            per_aoi = [{
                "name":       ctx.get("aoi_name", "AOI"),
                "helper_csv": ctx.get("triton_hydro_helper_csv"),
                "source":     ctx.get("triton_hydro_source"),
            }]
        if not per_aoi:
            return
        for entry in per_aoi:
            name = entry.get("name", "?")
            if entry.get("failed"):
                lbl = QLabel(
                    f"<b>{name}</b>  —  ⚠ "
                    f"{str(entry.get('error', 'error')).splitlines()[0]}"
                )
                lbl.setStyleSheet("color:#c53030; font-size:11px;")
                lbl.setWordWrap(True)
                self._results_inner.addWidget(lbl)
                continue
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            btn = QPushButton(name)
            btn.setStyleSheet(
                "QPushButton { text-align:left; background:transparent; "
                "border:none; color:#2d3748; font-weight:bold; padding:1px; }"
                "QPushButton:hover { color:#1a202c; text-decoration:underline; }"
            )
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _c, e=entry: self._show_hydro_for_aoi(e))
            row.addWidget(btn)
            hyg = Path(entry.get("hyg_path") or "").name
            files_lbl = QLabel(f"{hyg}   ({entry.get('source') or '—'})")
            files_lbl.setStyleSheet("color:#718096; font-size:11px;")
            row.addWidget(files_lbl)
            row.addStretch()
            line = QWidget()
            line.setLayout(row)
            self._results_inner.addWidget(line)
        self._results_gb.setVisible(True)

    def _show_hydro_for_aoi(self, entry: dict):
        csv = entry.get("helper_csv")
        self._gb_preview.setVisible(True)
        if not csv or not Path(csv).exists():
            self._preview_placeholder.setText(
                f"<span style='color:#c53030;'>Hydrograph CSV not found for "
                f"{entry.get('name', '?')}.</span>"
            )
            self._preview_placeholder.setVisible(True)
            self._hydro_preview.setVisible(False)
            return
        self._hydro_preview.show_hydrograph(
            csv, title=f"Hydrograph — {entry.get('name', '')}"
        )
        self._preview_placeholder.setVisible(False)
        self._hydro_preview.setVisible(True)

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
        self._error_lbl.setText(
            f"<b>Error:</b> {msg.split(chr(10))[0]}<br>"
            "<small>(See log panel below for full details)</small>"
        )
        self._error_lbl.setVisible(True)
