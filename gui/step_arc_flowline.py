"""ARC step 5 — Flowline.

Per-AOI accordion cards: each AOI chooses its stream-network source —
download from NHDPlus (auto) or upload the user's own shapefile — then one
"Prepare Flowline" run saves ``<AOI>/arc-files/flowline.shp`` for every AOI.
After the run, click an AOI in the results list to preview its flowlines on
the map below (AOI polygon + stream network).
"""
import re
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QGroupBox,
    QProgressBar, QComboBox, QLineEdit, QFileDialog, QFrame, QToolButton,
)
from PyQt6.QtCore import pyqtSignal, Qt

from core.arc_orchestrate import run_arc_flowline_for_all_aois
from gui.worker import Worker
from gui.run_button import set_running, set_ready
from gui.flowline_preview import FlowlinePreviewCanvas

_RUN_STYLE = (
    "font-weight:bold; padding:8px 20px; background:#276749; "
    "color:white; border-radius:4px; font-size:13px;"
)

_SRC_NHD, _SRC_USER = 0, 1


class ArcFlowlineCard(QFrame):
    """One AOI's flowline settings (accordion card)."""

    expand_requested = pyqtSignal(object)
    config_changed = pyqtSignal(object)

    def __init__(self, aoi_name: str, parent=None):
        super().__init__(parent)
        self._aoi_name = aoi_name
        self.setStyleSheet(
            "ArcFlowlineCard { border:1px solid #cbd5e0; border-radius:6px; "
            "background:#ffffff; }")
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 4, 8, 6)
        root.setSpacing(4)

        # Header (click to expand)
        self._head = QToolButton()
        self._head.setText(f"  {aoi_name}")
        self._head.setCheckable(True)
        self._head.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._head.setArrowType(Qt.ArrowType.RightArrow)
        self._head.setStyleSheet(
            "QToolButton { border:none; font-weight:bold; font-size:12px; "
            "color:#2d3748; text-align:left; }")
        self._head.clicked.connect(lambda: self.expand_requested.emit(self))
        head_row = QHBoxLayout()
        head_row.setContentsMargins(0, 0, 0, 0)
        head_row.addWidget(self._head)
        head_row.addStretch()
        self._summary = QLabel("NHDPlus (auto)")
        self._summary.setStyleSheet("color:#718096; font-size:11px;")
        head_row.addWidget(self._summary)
        root.addLayout(head_row)

        # Body
        self._body = QWidget()
        bv = QVBoxLayout(self._body)
        bv.setContentsMargins(18, 2, 4, 2)
        bv.setSpacing(6)

        src_row = QHBoxLayout()
        src_row.addWidget(QLabel("Flowline source:"))
        self._src = QComboBox()
        self._src.addItems([
            "Download from NHDPlus (auto)",
            "Use my own stream shapefile…",
        ])
        self._src.setFixedWidth(240)
        self._src.currentIndexChanged.connect(self._on_src_changed)
        src_row.addWidget(self._src)
        src_row.addStretch()
        bv.addLayout(src_row)

        self._file_row = QWidget()
        fr = QHBoxLayout(self._file_row)
        fr.setContentsMargins(0, 0, 0, 0)
        fr.addWidget(QLabel("Stream file:"))
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText(
            "Shapefile / GeoPackage with a COMID or LINKNO field")
        self._path_edit.textChanged.connect(
            lambda *_: self._refresh_summary())
        fr.addWidget(self._path_edit, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._on_browse)
        fr.addWidget(browse)
        bv.addWidget(self._file_row)

        hint = QLabel(
            "NHDPlus downloads every reach clipped to the AOI. A user file "
            "must carry a reach-id field (COMID / LINKNO); it is reprojected "
            "to the DEM grid automatically at run time.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#718096; font-size:11px;")
        bv.addWidget(hint)

        self._body.setVisible(False)
        root.addWidget(self._body)
        self._on_src_changed()

    # ── accordion ────────────────────────────────────────────────────────────
    def expand(self):
        self._body.setVisible(True)
        self._head.setArrowType(Qt.ArrowType.DownArrow)

    def collapse(self):
        self._body.setVisible(False)
        self._head.setArrowType(Qt.ArrowType.RightArrow)

    def is_expanded(self) -> bool:
        return self._body.isVisible()

    # ── config ───────────────────────────────────────────────────────────────
    def _on_src_changed(self, *_):
        self._file_row.setVisible(self._src.currentIndex() == _SRC_USER)
        self._refresh_summary()

    def _on_browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select stream shapefile",
            str(Path.home()),
            "Vector files (*.shp *.gpkg *.geojson);;All files (*)")
        if path:
            self._path_edit.setText(path)

    def _refresh_summary(self):
        if self._src.currentIndex() == _SRC_NHD:
            self._summary.setText("NHDPlus (auto)")
        else:
            p = self._path_edit.text().strip()
            self._summary.setText(Path(p).name if p else "(no file selected)")
        self.config_changed.emit(self)

    def is_ready(self) -> bool:
        if self._src.currentIndex() == _SRC_NHD:
            return True
        p = self._path_edit.text().strip()
        return bool(p) and Path(p).exists()

    def get_config(self) -> dict:
        if self._src.currentIndex() == _SRC_NHD:
            return {"source": "nhd", "user_path": None}
        return {"source": "user", "user_path": self._path_edit.text().strip()}

    def set_config(self, cfg: dict):
        src = (cfg or {}).get("source", "nhd")
        self._src.setCurrentIndex(_SRC_USER if src == "user" else _SRC_NHD)
        self._path_edit.setText((cfg or {}).get("user_path") or "")


class StepArcFlowlineWidget(QWidget):
    step_completed = pyqtSignal(dict)

    def __init__(self, log_fn=print, parent=None):
        super().__init__(parent)
        self._log = log_fn
        self._ctx_path = None
        self._ctx = {}
        self._aoi_features = []
        self._cards = []
        self._worker = None
        self._total = 0
        self._build_ui()

    # ── step interface ─────────────────────────────────────────────────────────

    def set_context(self, ctx_path, ctx):
        self._ctx_path = ctx_path
        self._ctx = ctx or {}
        self._aoi_features = list(self._ctx.get("aoi_features", []) or [])
        self._refresh_header()
        self._build_cards()
        self._clear_results()
        self._preview_canvas.clear()
        self._gb_preview.setVisible(False)

    def reset(self):
        self._aoi_features = []
        self._clear_cards()
        self._clear_results()
        self._progress.setVisible(False)
        self._status.setVisible(False)
        self._preview_canvas.clear()
        self._gb_preview.setVisible(False)
        self._refresh_header()

    # ── UI ──────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 22, 28, 22)
        layout.setSpacing(10)

        note = QLabel(
            "★ Saves the stream network ARC uses (flowline.shp, one per AOI). "
            "Choose per AOI: download from NHDPlus, or use your own stream "
            "shapefile. ARC builds a rating curve for every reach.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#718096; font-size:12px;")
        layout.addWidget(note)

        self._header = QLabel("")
        self._header.setStyleSheet("color:#2d3748; font-size:12px; font-weight:bold;")
        layout.addWidget(self._header)

        # Per-AOI accordion cards
        self._cards_holder = QVBoxLayout()
        self._cards_holder.setSpacing(6)
        layout.addLayout(self._cards_holder)

        apply_row = QHBoxLayout()
        self._apply_all_btn = QPushButton("Apply current AOI's settings to all")
        self._apply_all_btn.setStyleSheet("font-size:11px; padding:4px 10px;")
        self._apply_all_btn.clicked.connect(self._apply_to_all)
        self._apply_all_btn.setVisible(False)
        apply_row.addWidget(self._apply_all_btn)
        apply_row.addStretch()
        layout.addLayout(apply_row)

        run_row = QHBoxLayout()
        self._run_btn = QPushButton("Prepare Flowline for all")
        self._run_btn.setStyleSheet(_RUN_STYLE)
        self._run_btn.clicked.connect(self._run_step)
        run_row.addWidget(self._run_btn)
        run_row.addStretch()
        layout.addLayout(run_row)

        self._progress = QProgressBar()
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._progress.setFormat("%v / %m AOIs")
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color:#276749; font-size:12px; font-weight:bold;")
        self._status.setVisible(False)
        layout.addWidget(self._status)

        self._results_gb = QGroupBox("Flowline results — click an AOI to preview")
        self._results_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        self._results_inner = QVBoxLayout(self._results_gb)
        self._results_inner.setSpacing(2)
        self._results_gb.setVisible(False)
        layout.addWidget(self._results_gb)

        # Preview map
        self._gb_preview = QGroupBox("Flowline preview")
        self._gb_preview.setStyleSheet("QGroupBox { font-weight:bold; }")
        self._gb_preview.setMinimumHeight(380)
        pv = QVBoxLayout(self._gb_preview)
        pv.setContentsMargins(6, 8, 6, 6)
        self._preview_canvas = FlowlinePreviewCanvas(self, width=8, height=4.2)
        pv.addWidget(self._preview_canvas, 1)
        self._gb_preview.setVisible(False)
        layout.addWidget(self._gb_preview, 1)

        layout.addStretch()

    def _refresh_header(self):
        n = len(self._aoi_features)
        if n == 0:
            self._header.setText("(Complete the AOI step first.)")
            self._run_btn.setVisible(False)
        elif n == 1:
            self._header.setText(
                "1 AOI — choose its flowline source, then prepare.")
            self._run_btn.setText("Prepare Flowline")
            self._run_btn.setVisible(True)
        else:
            self._header.setText(
                f"{n} AOIs — choose each AOI's flowline source, then prepare.")
            self._run_btn.setText("Prepare Flowline for all")
            self._run_btn.setVisible(True)
        self._apply_all_btn.setVisible(n > 1)

    # ── cards ───────────────────────────────────────────────────────────────────

    def _clear_cards(self):
        for c in list(self._cards):
            c.setParent(None)
            c.deleteLater()
        self._cards.clear()

    def _build_cards(self):
        self._clear_cards()
        for i, feat in enumerate(self._aoi_features):
            card = ArcFlowlineCard(feat.get("name", f"AOI {i+1}"), self)
            card.expand_requested.connect(self._on_expand_requested)
            card.config_changed.connect(self._on_card_changed)
            self._cards_holder.addWidget(card)
            self._cards.append(card)
        if len(self._cards) == 1:
            self._cards[0].expand()

    def _on_expand_requested(self, card):
        for c in self._cards:
            if c is card:
                if c.is_expanded():
                    c.collapse()
                else:
                    c.expand()
            else:
                c.collapse()

    def _on_card_changed(self, _card):
        ready = bool(self._cards) and all(c.is_ready() for c in self._cards)
        self._run_btn.setEnabled(ready)

    def _apply_to_all(self):
        src = next((c for c in self._cards if c.is_expanded()), None)
        if src is None:
            return
        cfg = src.get_config()
        for c in self._cards:
            if c is not src:
                c.set_config(cfg)

    # ── run ───────────────────────────────────────────────────────────────────

    def _run_step(self):
        if not self._ctx_path or not self._aoi_features:
            self._log("Complete the AOI step first.")
            return
        if not all(c.is_ready() for c in self._cards):
            self._log("Select a stream file for every AOI set to 'my own shapefile'.")
            return
        self._clear_results()
        self._preview_canvas.clear()
        self._gb_preview.setVisible(False)
        n = len(self._aoi_features)
        self._total = n
        self._progress.setRange(0, n)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._status.setText("Preparing flowlines …")
        self._status.setStyleSheet("color:#744210; font-size:12px; font-weight:bold;")
        self._status.setVisible(True)
        set_running(self._run_btn)
        per_aoi = [c.get_config() for c in self._cards]
        self._worker = Worker(
            run_arc_flowline_for_all_aois,
            ctx_path=self._ctx_path, ctx=self._ctx,
            per_aoi_configs=per_aoi)
        self._worker.message.connect(self._on_message)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_message(self, msg):
        self._log(msg)
        m = re.search(r"Flowline \[(\d+)/(\d+)\] finished", msg)
        if m:
            self._progress.setValue(int(m.group(1)))

    def _on_done(self, ctx):
        self._ctx = ctx
        self._progress.setValue(self._total)
        n = max(len(self._aoi_features), 1)
        self._status.setText(f"All {n} AOI(s) processed. Click an AOI below to preview.")
        self._status.setStyleSheet("color:#276749; font-size:12px; font-weight:bold;")
        self._status.setVisible(True)
        set_ready(self._run_btn)
        self._build_results(ctx)
        self.step_completed.emit({"ctx_path": self._ctx_path, "ctx": ctx})

    def _on_error(self, msg):
        set_ready(self._run_btn)
        self._progress.setVisible(False)
        self._status.setText(f"Error: {msg.splitlines()[0]}")
        self._status.setStyleSheet("color:#c53030; font-size:12px; font-weight:bold;")
        self._status.setVisible(True)
        self._log(f"ERROR: {msg}")

    # ── results + preview ───────────────────────────────────────────────────────

    def _clear_results(self):
        while self._results_inner.count():
            it = self._results_inner.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self._results_gb.setVisible(False)

    def _build_results(self, ctx):
        self._clear_results()
        per = ctx.get("arc_flowline_per_aoi")
        if not per:
            # single-AOI fallback (no aoi_features case)
            per = [{
                "name":          ctx.get("aoi_name", "AOI"),
                "flowline":      ctx.get("arc_flowline_path"),
                "count":         ctx.get("arc_flowline_count"),
                "source":        ctx.get("arc_flowline_source", "nhd"),
                "source_file":   ctx.get("aoi_path"),
                "feature_index": ctx.get("aoi_feature_index", 0),
            }]
        for entry in per:
            if entry.get("failed"):
                lbl = QLabel(f"✗  {entry.get('name')} — {entry.get('error', 'failed')}")
                lbl.setWordWrap(True)
                lbl.setStyleSheet("color:#c53030; font-size:11px; padding:2px 0;")
                self._results_inner.addWidget(lbl)
                continue
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            src_txt = "user file" if entry.get("source") == "user" else "NHDPlus"
            btn = QPushButton(
                f"✓  {entry.get('name')} — {entry.get('count')} reach(es) "
                f"({src_txt})")
            btn.setStyleSheet(
                "QPushButton { text-align:left; border:none; color:#276749; "
                "font-size:11px; padding:3px 4px; }"
                "QPushButton:hover { background:#f0fff4; }")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(
                lambda _checked, e=entry: self._show_preview_for(e))
            rl.addWidget(btn, 1)
            self._results_inner.addWidget(row)
        self._results_gb.setVisible(True)

    def _show_preview_for(self, entry: dict):
        fl = entry.get("flowline")
        if not fl or not Path(fl).exists():
            self._log(f"Flowline file not found: {fl}")
            return
        self._gb_preview.setVisible(True)
        self._preview_canvas.show_flowlines(
            aoi_path=entry.get("source_file"),
            feature_index=int(entry.get("feature_index", 0) or 0),
            all_flowlines_path=fl,
            title=f"Flowlines — {entry.get('name')} ({entry.get('count')} reaches)",
        )
