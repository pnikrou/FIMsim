"""Step 5 — Boundary Conditions (.src + .extbc) — TRITON.

Multi-AOI controller, mirroring the LISFLOOD BCI step:

  * 1 AOI   → one TritonBCConfigPanel embedded directly.
  * >1 AOI  → an accordion of AOITritonBCCard widgets (one per AOI) + a top
              "Apply current AOI's settings to all" button.

The inflow source point and the downstream boundary segment are auto-derived
from the flowline + DEM by the (unchanged) core (detect_main_river); the user
only picks the downstream boundary TYPE (0/1/2/3) per AOI.  Both single- and
multi-AOI runs go through ``run_triton_bc_for_all_aois`` (a 1-AOI run is just a
loop of one), which calls the unchanged ``prepare_triton_bc`` writer.
"""
import re
from pathlib import Path
from typing import List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QGroupBox, QProgressBar, QScrollArea, QStackedWidget, QMessageBox,
    QPlainTextEdit,
)
from PyQt6.QtCore import pyqtSignal, Qt

from core.orchestrate import run_triton_bc_for_all_aois
from gui.worker import Worker
from gui.run_button import set_running, set_ready
from gui.triton_bc_config_panel import TritonBCConfigPanel
from gui.aoi_triton_bc_card import AOITritonBCCard


_BC_STEP_RE = re.compile(r"^▶\s+BC\s+\[(\d+)/(\d+)\]")
_BC_DONE_RE = re.compile(r"^✓\s+BC\s+\[(\d+)/(\d+)\]")


class StepTritonBCWidget(QWidget):
    step_completed = pyqtSignal(dict)

    def __init__(self, log_fn, parent=None):
        super().__init__(parent)
        self._log = log_fn
        self._worker = None
        self._ctx_path = None
        self._ctx = None
        self._aoi_features: list = []
        self._stack: QStackedWidget = None        # type: ignore[assignment]
        self._single_panel: TritonBCConfigPanel = None  # type: ignore[assignment]
        self._cards: List[AOITritonBCCard] = []
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
            self._single_panel.set_config({"bc_type": 0})
        self._error_lbl.setVisible(False)
        self._progress.setValue(0)
        self._progress.setVisible(False)
        self._status_lbl.setVisible(False)
        self._stack.setCurrentIndex(0)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        info = QLabel(
            "★ <b>NHD auto-detect</b> downloads NHD flowlines, picks the "
            "highest-order river, and derives the inflow point + downstream "
            "boundary segment from the DEM. Works for <b>USA only</b>. "
            "Outside the USA (or to override), choose <b>Manual coordinates</b> "
            "in an AOI and enter the inflow point + outflow segment, or edit the "
            "generated <code>.src</code> / <code>.extbc</code> files directly."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#4a5568; font-size:11px; padding:2px 0px;")
        layout.addWidget(info)

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
        self._stack.setMinimumHeight(320)

        # Page 0 — single-AOI form
        single_page = QWidget()
        sp_layout = QVBoxLayout(single_page)
        sp_layout.setContentsMargins(0, 0, 0, 0)
        gb = QGroupBox("5. Boundary Conditions (.src + .extbc)")
        gb_layout = QVBoxLayout(gb)
        self._single_panel = TritonBCConfigPanel(self)
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

        # Run + progress + status
        btn_row = QHBoxLayout()
        self._run_btn = QPushButton("Write .src + .extbc file(s)")
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

        # Post-run results: simple clickable per-AOI lines + two-pane preview
        self._results_gb = QGroupBox(
            "Per-AOI BC outputs  —  click an AOI to preview its files"
        )
        rgl = QVBoxLayout(self._results_gb)
        self._results_inner = QVBoxLayout()
        self._results_inner.setSpacing(1)
        rgl.addLayout(self._results_inner)
        self._results_gb.setVisible(False)
        layout.addWidget(self._results_gb)

        self._preview_gb = QGroupBox("File preview")
        pv = QHBoxLayout(self._preview_gb)
        src_col = QVBoxLayout()
        src_col.addWidget(QLabel("<b>.src</b>  (inflow points)"))
        self._src_view = QPlainTextEdit()
        self._src_view.setReadOnly(True)
        self._src_view.setStyleSheet("font-family:monospace; font-size:11px;")
        src_col.addWidget(self._src_view)
        pv.addLayout(src_col, 1)
        extbc_col = QVBoxLayout()
        extbc_col.addWidget(QLabel("<b>.extbc</b>  (outflow boundary)"))
        self._extbc_view = QPlainTextEdit()
        self._extbc_view.setReadOnly(True)
        self._extbc_view.setStyleSheet("font-family:monospace; font-size:11px;")
        extbc_col.addWidget(self._extbc_view)
        pv.addLayout(extbc_col, 1)
        self._preview_gb.setMinimumHeight(180)
        self._preview_gb.setVisible(False)
        layout.addWidget(self._preview_gb)

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
            f"<b>{n}</b> AOI(s) confirmed — choose the downstream boundary type "
            "for each AOI below."
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
            card = AOITritonBCCard(feat.get("name", "(unnamed)"), self)
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

    def _expanded_card(self) -> Optional[AOITritonBCCard]:
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
            return
        cfg = src.get_config()
        for c in self._cards:
            if c is not src:
                c.set_config(cfg)
        self._on_card_config_changed(None)

    def _on_single_config_changed(self):
        if self._stack.currentIndex() == 0 and len(self._aoi_features) <= 1:
            self._run_btn.setVisible(self._single_panel.is_ready())

    # ── run (always via the orchestrator; 1 AOI = loop of one) ─────────────────

    def _run_step(self):
        if not self._ctx_path or not self._ctx:
            self._log("Complete earlier steps first.")
            return
        if len(self._aoi_features) <= 1:
            cfgs = [self._single_panel.get_config()]
        else:
            cfgs = [c.get_config() for c in self._cards]
        self._error_lbl.setVisible(False)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        set_running(self._run_btn)
        self._status_lbl.setText(
            f"Preparing BC for {max(len(self._aoi_features), 1)} AOI(s)…"
        )
        self._status_lbl.setVisible(True)
        self._worker = Worker(
            run_triton_bc_for_all_aois,
            ctx_path=self._ctx_path, ctx=self._ctx, per_aoi_configs=cfgs,
        )
        self._worker.message.connect(self._on_message)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_message(self, msg):
        self._log(msg)
        ml = msg.lower()
        m = _BC_STEP_RE.match(msg)
        if m:
            self._progress.setValue(20)
            self._status_lbl.setText(
                f"Preparing BC {m.group(1)} / {m.group(2)} …"
            )
            return
        m = _BC_DONE_RE.match(msg)
        if m:
            self._progress.setValue(100)
            return
        if "main river" in ml:
            self._progress.setValue(50)
        elif "external bc file written" in ml or "source locations written" in ml:
            self._progress.setValue(85)

    # ── results ────────────────────────────────────────────────────────────────

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
        if hasattr(self, "_preview_gb"):
            self._preview_gb.setVisible(False)
            self._src_view.clear()
            self._extbc_view.clear()

    def _build_results(self, ctx):
        self._clear_results()
        per_aoi = ctx.get("triton_bc_per_aoi", []) or []
        if not per_aoi:
            if ctx.get("triton_extbc_path"):
                per_aoi = [{
                    "name":        ctx.get("aoi_name", "AOI"),
                    "bc_type":     None,
                    "extbc_path":  ctx.get("triton_extbc_path"),
                    "src_path":    ctx.get("triton_src_loc_path"),
                }]
        if not per_aoi:
            return
        # One simple line per AOI (clickable name + plain-text file names).
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
            btn.clicked.connect(lambda _c, e=entry: self._show_files_for_aoi(e))
            row.addWidget(btn)
            src = Path(entry.get("src_path") or "").name
            extbc = Path(entry.get("extbc_path") or "").name
            files_lbl = QLabel(f"{src}   {extbc}")
            files_lbl.setStyleSheet("color:#718096; font-size:11px;")
            row.addWidget(files_lbl)
            row.addStretch()
            line = QWidget()
            line.setLayout(row)
            self._results_inner.addWidget(line)
        self._results_gb.setVisible(True)

    def _show_files_for_aoi(self, entry: dict):
        def _load(path):
            try:
                if path and Path(path).exists():
                    return Path(path).read_text(encoding="utf-8", errors="replace")
                return f"(file not found: {path})"
            except Exception as ex:
                return f"(could not read: {ex})"
        self._src_view.setPlainText(_load(entry.get("src_path")))
        self._extbc_view.setPlainText(_load(entry.get("extbc_path")))
        self._preview_gb.setVisible(True)

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
