"""ARC-Curve2Flood Step 5 — Flowline.

Multi-AOI controller that mirrors the TRITON/ARC DEM and Land Cover steps:
  * 1 AOI   → one ArcFlowlineConfigPanel embedded directly.
  * >1 AOIs → accordion of AOIArcFlowlineCard widgets (Edit / Remove chrome)
              with an "Apply current AOI's settings to all" button.

Output per AOI:
  <AOI>/arc-files/flowline.shp — the stream network ARC builds rating curves on.
"""
import re
from pathlib import Path
from typing import List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QGroupBox, QProgressBar, QScrollArea, QStackedWidget, QMessageBox,
    QComboBox, QLineEdit, QFileDialog,
)
from PyQt6.QtCore import pyqtSignal, Qt

from core.arc_orchestrate import run_arc_flowline_for_all_aois
from gui.worker import Worker
from gui.run_button import set_running, set_ready
from gui.flowline_preview import FlowlinePreviewCanvas

_FLOWLINE_STEP_RE = re.compile(r"^▶\s+Flowline\s+\[(\d+)/(\d+)\]")
_FLOWLINE_DONE_RE = re.compile(r"^✓\s+Flowline\s+\[(\d+)/(\d+)\]\s+finished")


# ── Config panel (shared by single-AOI page and each card) ────────────────────

class ArcFlowlineConfigPanel(QWidget):
    """Flowline source chooser: NHDPlus download vs user shapefile."""

    config_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)

        src_row = QHBoxLayout()
        src_row.addWidget(QLabel("Flowline source:"))
        self._src_combo = QComboBox()
        self._src_combo.addItem("Download from NHDPlus (auto)", "nhd")
        self._src_combo.addItem("I have a stream shapefile", "user")
        self._src_combo.setFixedWidth(250)
        self._src_combo.currentIndexChanged.connect(self._on_src_changed)
        src_row.addWidget(self._src_combo)
        src_row.addStretch()
        layout.addLayout(src_row)

        self._file_row = QWidget()
        fr = QHBoxLayout(self._file_row)
        fr.setContentsMargins(0, 0, 0, 0)
        fr.addWidget(QLabel("Stream file:"))
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("Select a stream shapefile / GeoPackage")
        self._path_edit.textChanged.connect(lambda *_: self.config_changed.emit())
        fr.addWidget(self._path_edit, 1)
        self._browse_btn = QPushButton("Browse…")
        self._browse_btn.clicked.connect(self._on_browse)
        fr.addWidget(self._browse_btn)
        layout.addWidget(self._file_row)

        # Format note — shown ONLY for the user-shapefile option.
        self._note = QLabel(
            "The file must contain a reach-id attribute named "
            "<b>COMID</b> or <b>LINKNO</b> (a stream-order field is used when "
            "present). Any CRS is fine — it is reprojected to the DEM grid "
            "automatically."
        )
        self._note.setWordWrap(True)
        self._note.setStyleSheet(
            "color:#4a5568; font-size:11px; padding:6px 8px; "
            "background:#f7fafc; border:1px solid #cbd5e0; border-radius:4px;"
        )
        layout.addWidget(self._note)

        self._on_src_changed()

    def _on_src_changed(self, *_):
        is_user = (self._src_combo.currentData() == "user")
        self._file_row.setVisible(is_user)
        self._note.setVisible(is_user)
        self.config_changed.emit()

    def _on_browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select stream shapefile", str(Path.home()),
            "Vector files (*.shp *.gpkg *.geojson);;All files (*)")
        if path:
            self._path_edit.setText(path)

    def is_ready(self) -> bool:
        if self._src_combo.currentData() == "nhd":
            return True
        p = self._path_edit.text().strip()
        return bool(p) and Path(p).exists()

    def get_config(self) -> dict:
        if self._src_combo.currentData() == "nhd":
            return {"source": "nhd", "user_path": None}
        return {"source": "user", "user_path": self._path_edit.text().strip()}

    def set_config(self, cfg: dict):
        src = (cfg or {}).get("source", "nhd")
        idx = self._src_combo.findData("user" if src == "user" else "nhd")
        self._src_combo.setCurrentIndex(max(idx, 0))
        self._path_edit.setText((cfg or {}).get("user_path") or "")


# ── Per-AOI card (mirrors AOIDEMCard chrome) ──────────────────────────────────

class AOIArcFlowlineCard(QFrame):
    expand_requested = pyqtSignal(object)
    config_changed   = pyqtSignal(object)
    remove_requested = pyqtSignal(object)

    EXPANDED_STYLE = (
        "QFrame#card { background:#f9fafb; border:2px solid #a0aec0; "
        "border-radius:6px; padding:8px; }"
    )
    COLLAPSED_STYLE = (
        "QFrame#card { background:#f9fafb; border:1px solid #e2e8f0; "
        "border-radius:6px; padding:6px; }"
    )

    def __init__(self, aoi_name: str, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self._aoi_name = aoi_name
        self._expanded = False
        self._build_ui()
        self.setStyleSheet(self.COLLAPSED_STYLE)
        self._refresh_status()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 2)
        outer.setSpacing(6)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)

        self._caret = QLabel("▶")
        self._caret.setFixedWidth(14)
        self._caret.setStyleSheet("color:#4a5568; font-weight:bold;")
        header.addWidget(self._caret)

        self._name_lbl = QLabel(f"<b>{self._aoi_name}</b>")
        self._name_lbl.setStyleSheet("color:#2d3748;")
        header.addWidget(self._name_lbl)
        header.addStretch()

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet("color:#666; font-size:11px;")
        header.addWidget(self._status_lbl)

        self._toggle_btn = QPushButton("Edit")
        self._toggle_btn.setFixedWidth(80)
        self._toggle_btn.clicked.connect(self._on_toggle_clicked)
        header.addWidget(self._toggle_btn)

        self._remove_btn = QPushButton("Remove")
        self._remove_btn.setFixedWidth(70)
        self._remove_btn.setStyleSheet(
            "background:#e53e3e; color:white; border-radius:3px; "
            "font-size:11px; padding:2px 4px;"
        )
        self._remove_btn.setToolTip(f"Remove {self._aoi_name} from this run")
        self._remove_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        header.addWidget(self._remove_btn)

        outer.addLayout(header)

        self._panel = ArcFlowlineConfigPanel(self)
        self._panel.setVisible(False)
        self._panel.config_changed.connect(self._forward_config_changed)
        outer.addWidget(self._panel)

    # ── expand / collapse ─────────────────────────────────────────────────────

    def is_expanded(self) -> bool:
        return self._expanded

    def expand(self):
        if self._expanded:
            return
        self._expanded = True
        self._panel.setVisible(True)
        self._toggle_btn.setText("Done")
        self._caret.setText("▼")
        self.setStyleSheet(self.EXPANDED_STYLE)

    def collapse(self):
        if not self._expanded:
            return
        self._expanded = False
        self._panel.setVisible(False)
        self._toggle_btn.setText("Edit")
        self._caret.setText("▶")
        self.setStyleSheet(self.COLLAPSED_STYLE)
        self._refresh_status()

    def _on_toggle_clicked(self):
        if self._expanded:
            self.collapse()
        else:
            self.expand_requested.emit(self)

    # ── status line ───────────────────────────────────────────────────────────

    def _refresh_status(self):
        cfg = self._panel.get_config()
        if cfg["source"] == "user":
            p = cfg.get("user_path") or ""
            if p:
                src = f"<i>Source:</i> uploaded <code>{Path(p).name}</code>"
            else:
                src = ("<span style='color:#c53030;'>"
                       "<i>Source:</i> uploaded — pick a file</span>")
        else:
            src = "<i>Source:</i> NHDPlus download"
        self._status_lbl.setText(src)

    def _forward_config_changed(self):
        self._refresh_status()
        self.config_changed.emit(self)

    # ── public proxies ────────────────────────────────────────────────────────

    def panel(self) -> ArcFlowlineConfigPanel:
        return self._panel

    def is_ready(self) -> bool:
        return self._panel.is_ready()

    def get_config(self) -> dict:
        return self._panel.get_config()

    def set_config(self, cfg: dict):
        self._panel.set_config(cfg)
        self._refresh_status()


# ── Step widget ───────────────────────────────────────────────────────────────

class StepArcFlowlineWidget(QWidget):
    step_completed = pyqtSignal(dict)

    def __init__(self, log_fn=print, parent=None):
        super().__init__(parent)
        self._log = log_fn
        self._worker = None
        self._ctx_path = None
        self._ctx = None
        self._aoi_features: list = []
        self._cards: List[AOIArcFlowlineCard] = []
        self._cards_layout: QVBoxLayout = None
        self._stack: QStackedWidget = None
        self._single_panel: ArcFlowlineConfigPanel = None
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
            self._single_panel.set_config({"source": "nhd"})
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
        layout.addWidget(self._stack)

        # Page 0 — single-AOI form
        single_page = QWidget()
        sp_layout = QVBoxLayout(single_page)
        sp_layout.setContentsMargins(0, 0, 0, 0)
        gb = QGroupBox("5. Flowline")
        gb_layout = QVBoxLayout(gb)
        self._single_panel = ArcFlowlineConfigPanel(self)
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
            "Copy the currently expanded AOI's flowline configuration "
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

        # Run button
        btn_row = QHBoxLayout()
        self._run_btn = QPushButton("Prepare Flowline")
        self._run_btn.setStyleSheet(
            "font-weight:bold; padding:7px 20px; background:#2b6cb0; "
            "color:white; border-radius:4px;"
        )
        self._run_btn.clicked.connect(self._run_step)
        self._run_btn.setVisible(False)
        btn_row.addWidget(self._run_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Progress + status + error
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

        # Post-run results: clickable AOI list
        self._results_gb = QGroupBox(
            "Per-AOI flowline outputs  —  click an AOI to preview its flowlines"
        )
        self._results_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        rgl = QVBoxLayout(self._results_gb)
        self._results_inner = QVBoxLayout()
        self._results_inner.setSpacing(0)
        rgl.addLayout(self._results_inner)
        self._results_gb.setVisible(False)
        layout.addWidget(self._results_gb)

        # Preview
        self._gb_preview = QGroupBox("Flowline preview")
        self._gb_preview.setStyleSheet("QGroupBox { font-weight:bold; }")
        self._gb_preview.setMinimumHeight(400)
        pv = QVBoxLayout(self._gb_preview)
        pv.setSpacing(6)
        pv.setContentsMargins(6, 8, 6, 6)

        self._preview_placeholder = QLabel(
            "<i>Click an AOI above to preview its AOI polygon and stream "
            "network.</i>"
        )
        self._preview_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_placeholder.setStyleSheet(
            "color:#888; padding:30px; background:#fafafa; "
            "border:1px dashed #cbd5e0; border-radius:4px;"
        )
        pv.addWidget(self._preview_placeholder)

        self._preview_canvas = FlowlinePreviewCanvas(self, width=8, height=4.2)
        self._preview_canvas.setVisible(False)
        pv.addWidget(self._preview_canvas, 1)

        self._gb_preview.setVisible(False)
        layout.addWidget(self._gb_preview, 1)

        layout.addStretch()

    # ── Layout switching ──────────────────────────────────────────────────────

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
            self._run_btn.setText("Prepare Flowline")
            self._run_btn.setVisible(self._single_panel.is_ready())
            return
        self._aoi_count_lbl.setText(
            f"<b>{n}</b> AOI(s) confirmed — configure the flowline source for "
            "each AOI below.  Click an AOI to expand its settings."
        )
        self._aoi_count_lbl.setVisible(True)
        self._stack.setCurrentIndex(1)
        self._run_btn.setText("Prepare Flowline for all")
        self._build_cards()

    def _clear_cards(self):
        for c in list(self._cards):
            c.setParent(None)
            c.deleteLater()
        self._cards.clear()

    def _build_cards(self):
        self._clear_cards()
        for feat in self._aoi_features:
            card = AOIArcFlowlineCard(feat.get("name", "(unnamed)"), self)
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
        self._aoi_count_lbl.setText(
            f"<b>{len(self._aoi_features)}</b> AOI(s) remaining."
        )

    # ── Accordion ─────────────────────────────────────────────────────────────

    def _on_expand_requested(self, card: AOIArcFlowlineCard):
        for c in self._cards:
            if c is card:
                c.expand()
            else:
                c.collapse()
        self._apply_all_btn.setEnabled(True)

    def _expanded_card(self) -> Optional[AOIArcFlowlineCard]:
        for c in self._cards:
            if c.is_expanded():
                return c
        return None

    def _on_card_config_changed(self, _card):
        all_ready = bool(self._cards) and all(c.is_ready() for c in self._cards)
        self._run_btn.setVisible(all_ready)

    def _on_single_config_changed(self):
        if self._stack.currentIndex() == 0 and len(self._aoi_features) <= 1:
            self._run_btn.setVisible(self._single_panel.is_ready())

    def _apply_to_all(self):
        src = self._expanded_card()
        if src is None:
            return
        cfg = src.get_config()
        for c in self._cards:
            if c is not src:
                c.set_config(cfg)
        self._on_card_config_changed(None)

    # ── Run ───────────────────────────────────────────────────────────────────

    def _run_step(self):
        if not self._ctx_path or not self._ctx:
            self._log("Complete earlier steps first.")
            return
        if not self._aoi_features:
            self._error_lbl.setText(
                "No AOIs are confirmed.  Go back to the AOI step and "
                "confirm at least one feature first."
            )
            self._error_lbl.setVisible(True)
            return

        if len(self._aoi_features) <= 1:
            per_aoi = [self._single_panel.get_config()]
        else:
            per_aoi = [c.get_config() for c in self._cards]

        # Validate user-supplied paths
        for cfg, feat in zip(per_aoi, self._aoi_features):
            if cfg.get("source") == "user":
                p = cfg.get("user_path") or ""
                if not p or not Path(p).exists():
                    self._error_lbl.setText(
                        f"AOI '{feat.get('name')}' is set to 'I have a stream "
                        "shapefile' but no valid file was selected."
                    )
                    self._error_lbl.setVisible(True)
                    return

        self._error_lbl.setVisible(False)
        self._clear_results()
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._status_lbl.setVisible(False)
        set_running(self._run_btn)

        self._worker = Worker(
            run_arc_flowline_for_all_aois,
            ctx_path=self._ctx_path, ctx=self._ctx,
            per_aoi_configs=per_aoi,
        )
        self._worker.message.connect(self._on_message)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    # ── progress / log ────────────────────────────────────────────────────────

    def _on_message(self, msg):
        self._log(msg)
        n = max(len(self._aoi_features), 1)
        m = _FLOWLINE_STEP_RE.match(msg)
        if m:
            i, total = int(m.group(1)), int(m.group(2))
            self._status_lbl.setText(f"Preparing flowline {i} / {total} …")
            self._status_lbl.setVisible(True)
            return
        m = _FLOWLINE_DONE_RE.match(msg)
        if m:
            i, total = int(m.group(1)), int(m.group(2))
            self._progress.setValue(int(i / max(total, 1) * 100))

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

    # ── results + preview ───────────────────────────────────────────────────────

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
            self._preview_canvas.setVisible(False)
            self._preview_canvas.clear()

    def _build_results(self, ctx):
        self._clear_results()
        per_aoi = ctx.get("arc_flowline_per_aoi", []) or []
        if not per_aoi:
            fl = ctx.get("arc_flowline_path")
            if fl:
                per_aoi = [{
                    "name":          ctx.get("aoi_name", "AOI"),
                    "flowline":      fl,
                    "count":         ctx.get("arc_flowline_count"),
                    "source":        ctx.get("arc_flowline_source", "nhd"),
                    "source_file":   ctx.get("aoi_path"),
                    "feature_index": ctx.get("aoi_feature_index", 0),
                }]
        if not per_aoi:
            return

        for entry in per_aoi:
            name = entry.get("name", "?")
            row = QFrame()
            row.setStyleSheet(
                "QFrame { background:#f9fafb; border:1px solid #e2e8f0; "
                "border-radius:3px; padding:3px 6px; }"
                "QFrame:hover { background:#f0f2f5; }"
            )
            rl = QHBoxLayout(row)
            rl.setContentsMargins(6, 2, 6, 2)
            rl.setSpacing(8)
            if entry.get("failed"):
                lbl = QLabel(f"✗  <b>{name}</b> — {entry.get('error', 'failed')}")
                lbl.setStyleSheet("color:#c53030; font-size:11px;")
                lbl.setWordWrap(True)
                rl.addWidget(lbl, 1)
                self._results_inner.addWidget(row)
                continue
            btn = QPushButton(f"  {name}")
            btn.setStyleSheet(
                "QPushButton { text-align:left; background:transparent; "
                "border:none; color:#2d3748; font-weight:bold; padding:2px; }"
                "QPushButton:hover { color:#1a202c; text-decoration:underline; }"
            )
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(
                lambda _checked, e=entry: self._show_preview_for(e)
            )
            rl.addWidget(btn, 1)
            src_txt = "user file" if entry.get("source") == "user" else "NHDPlus"
            hint = QLabel(
                f"<small>{entry.get('count')} reach(es) · {src_txt}</small>")
            hint.setStyleSheet("color:#718096; font-size:10px;")
            rl.addWidget(hint)
            self._results_inner.addWidget(row)

        self._results_gb.setVisible(True)
        self._gb_preview.setVisible(True)
        self._preview_placeholder.setVisible(True)
        self._preview_canvas.setVisible(False)

    def _show_preview_for(self, entry: dict):
        fl = entry.get("flowline")
        if not fl or not Path(fl).exists():
            self._preview_placeholder.setText(
                f"<span style='color:#c53030;'>Flowline file not found: "
                f"{fl}</span>"
            )
            self._preview_placeholder.setVisible(True)
            self._preview_canvas.setVisible(False)
            return
        self._preview_canvas.show_flowlines(
            aoi_path=entry.get("source_file"),
            feature_index=int(entry.get("feature_index", 0) or 0),
            all_flowlines_path=fl,
            title=f"Flowlines — {entry.get('name')} "
                  f"({entry.get('count')} reaches)",
        )
        self._preview_placeholder.setVisible(False)
        self._preview_canvas.setVisible(True)
