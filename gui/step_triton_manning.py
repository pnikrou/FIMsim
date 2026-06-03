"""Step 3 of TRITON workflow — Manning's n / friction file (friction.asc)."""
import re
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFileDialog, QGroupBox, QFormLayout,
    QDoubleSpinBox, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QProgressBar,
)
from PyQt6.QtCore import pyqtSignal, Qt

from core.triton_manning import prepare_triton_manning
from core.nlcd import NLCD_MANNING, SENTINEL2_MANNING
from gui.worker import Worker
from gui.run_button import set_running, set_ready
from gui.manning_table_widget import ManningTableWidget


# ESRI Sentinel-2 LULC class names (same mapping as LISFLOOD Manning step)
LULC_CLASS_NAMES = {
    1: "Water",
    2: "Trees / Forest",
    4: "Flooded Vegetation",
    5: "Crops / Agriculture",
    7: "Built Area / Urban",
    8: "Bare Ground",
    "default": "Other / Unclassified",
}

# Default Manning n values per LULC class
DEFAULT_MANNING_MAP = {
    1: 0.030,
    2: 0.100,
    4: 0.060,
    5: 0.040,
    7: 0.015,
    8: 0.025,
    "default": 0.060,
}

# Mode combo index → (fric_mode, lulc_source) mapping
_MODE_PARAMS = [
    ("fixed",   None),                # 0: Fixed value
    ("varying", "download_nlcd"),     # 1: Download NLCD (USGS, default)
    ("varying", "download"),          # 2: Download ESRI Sentinel-2 (10m)
    ("varying", "user_lulc"),         # 3: User-provided LULC raster
    ("varying", "user_manning"),      # 4: User-provided Manning n raster
]
_MODE_LABELS = [
    "Fixed value",
    "Download NLCD (USGS, 30m, default)",
    "Download Sentinel-2 (ESRI, 10m)",
    "I have a LULC raster",
    "I have a Manning n raster",
]


class StepTritonManningWidget(QWidget):
    step_completed = pyqtSignal(dict)

    def __init__(self, log_fn, parent=None):
        super().__init__(parent)
        self._log = log_fn
        self._worker = None
        self._ctx_path = None
        self._ctx = None
        self._setup_ui()

    def set_context(self, ctx_path, ctx):
        self._ctx_path = ctx_path
        self._ctx = ctx

    # ── UI ─────────────────────────────────────────────────────────────────────
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        gb = QGroupBox("3. TRITON Friction / Manning's n (friction.asc)")
        form = QFormLayout(gb)

        # ── Mode combo ────────────────────────────────────────────────────────
        self._mode_combo = QComboBox()
        self._mode_combo.addItems(_MODE_LABELS)
        self._mode_combo.setCurrentIndex(1)   # default: NLCD
        self._mode_combo.currentIndexChanged.connect(self._toggle_mode)
        form.addRow("Mode:", self._mode_combo)

        # ── Fixed value spin ──────────────────────────────────────────────────
        self._fixed_spin = QDoubleSpinBox()
        self._fixed_spin.setRange(0.001, 1.0)
        self._fixed_spin.setDecimals(4)
        self._fixed_spin.setValue(0.06)
        self._fixed_lbl = QLabel("Fixed n value:")
        form.addRow(self._fixed_lbl, self._fixed_spin)

        # ── NLCD year ─────────────────────────────────────────────────────────
        self._nlcd_year_combo = QComboBox()
        self._nlcd_year_combo.addItems(["2021", "2019", "2016"])
        self._nlcd_year_lbl = QLabel("NLCD year:")
        form.addRow(self._nlcd_year_lbl, self._nlcd_year_combo)

        # ── Sentinel-2 year (ESRI download mode only) ─────────────────────────
        self._lulc_year_combo = QComboBox()
        for yr in range(2017, 2025):
            self._lulc_year_combo.addItem(str(yr))
        self._lulc_year_combo.setCurrentText("2023")
        self._lulc_year_lbl = QLabel("Sentinel-2 year:")
        form.addRow(self._lulc_year_lbl, self._lulc_year_combo)

        # ── Raster file browse (user LULC or user Manning raster) ─────────────
        raster_row = QHBoxLayout()
        self._raster_edit = QLineEdit()
        self._raster_edit.setPlaceholderText("Path to LULC or Manning n raster (.tif)")
        self._raster_browse_btn = QPushButton("Browse…")
        self._raster_browse_btn.setFixedWidth(80)
        self._raster_browse_btn.clicked.connect(self._browse_raster)
        raster_row.addWidget(self._raster_edit)
        raster_row.addWidget(self._raster_browse_btn)
        self._raster_lbl = QLabel("Raster file:")
        form.addRow(self._raster_lbl, raster_row)

        # ── LULC → Manning n table (Min/Max are bounds; Avg editable, clamped) ─
        self._table_lbl = QLabel(
            "LULC class → Manning n mapping  "
            "(Min/Max are reference bounds; Avg is editable and clamped):"
        )
        form.addRow(self._table_lbl)
        self._table = ManningTableWidget(NLCD_MANNING)   # default = NLCD
        form.addRow(self._table)

        # ── Output note ────────────────────────────────────────────────────────
        out_note = QLabel(
            "<small><i>Output will be a headerless ASCII matrix (no ESRI header) "
            "suitable for TRITON's friction.asc input.</i></small>"
        )
        out_note.setWordWrap(True)
        form.addRow(out_note)

        layout.addWidget(gb)

        # Trigger initial visibility state
        self._toggle_mode(0)

        # ── Run button ─────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._run_btn = QPushButton("✔  Prepare Friction File")
        self._run_btn.setStyleSheet(
            "font-weight:bold; padding:7px 20px; background:#2b6cb0; color:white; border-radius:4px;"
        )
        btn_row.addWidget(self._run_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # ── Progress bar ───────────────────────────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setVisible(False)
        self._progress.setStyleSheet("QProgressBar { height: 18px; }")
        layout.addWidget(self._progress)

        # ── Error label ────────────────────────────────────────────────────────
        self._error_lbl = QLabel("")
        self._error_lbl.setWordWrap(True)
        self._error_lbl.setStyleSheet(
            "padding:10px; background:#fff5f5; border:1px solid #fc8181; "
            "border-radius:4px; font-size:12px; color:#c53030;"
        )
        self._error_lbl.setVisible(False)
        layout.addWidget(self._error_lbl)

        # ── Report ─────────────────────────────────────────────────────────────
        self._report = QLabel("")
        self._report.setWordWrap(True)
        self._report.setStyleSheet(
            "padding:10px; background:#f0fff4; border:1px solid #9ae6b4; "
            "border-radius:4px; font-size:12px;"
        )
        self._report.setVisible(False)
        layout.addWidget(self._report)
        layout.addStretch()

        self._run_btn.clicked.connect(self._run_step)

    # ── Table builder ──────────────────────────────────────────────────────────
    def _build_default_table(self):
        t = QTableWidget(len(DEFAULT_MANNING_MAP), 3)
        t.setHorizontalHeaderLabels(["Class ID", "Land Cover Type", "Manning n"])
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        t.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        t.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        t.setColumnWidth(0, 72)
        t.setColumnWidth(2, 100)
        t.setMaximumHeight(220)
        for row, (k, v) in enumerate(DEFAULT_MANNING_MAP.items()):
            id_item   = QTableWidgetItem(str(k))
            name_item = QTableWidgetItem(LULC_CLASS_NAMES.get(k, ""))
            val_item  = QTableWidgetItem(str(v))
            id_item.setFlags(id_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            t.setItem(row, 0, id_item)
            t.setItem(row, 1, name_item)
            t.setItem(row, 2, val_item)
        return t

    def _get_table_mapping(self):
        # Delegated to ManningTableWidget
        return self._table.get_mapping()

    # ── Toggle helpers ──────────────────────────────────────────────────────────
    def _toggle_mode(self, idx):
        fric_mode, lulc_source = _MODE_PARAMS[idx]
        is_fixed        = (fric_mode == "fixed")
        is_dl_nlcd      = (lulc_source == "download_nlcd")
        is_dl_esri      = (lulc_source == "download")
        needs_raster    = (lulc_source in ("user_lulc", "user_manning"))
        show_table      = (lulc_source != "user_manning")

        self._fixed_lbl.setVisible(is_fixed)
        self._fixed_spin.setVisible(is_fixed)

        self._nlcd_year_lbl.setVisible(is_dl_nlcd)
        self._nlcd_year_combo.setVisible(is_dl_nlcd)

        self._lulc_year_lbl.setVisible(is_dl_esri)
        self._lulc_year_combo.setVisible(is_dl_esri)

        self._raster_lbl.setVisible(needs_raster)
        self._raster_edit.setVisible(needs_raster)
        self._raster_browse_btn.setVisible(needs_raster)

        self._table_lbl.setVisible(not is_fixed and show_table)
        self._table.setVisible(not is_fixed and show_table)

        # Swap table data to match source
        if not is_fixed and show_table:
            if is_dl_nlcd:
                self._table.set_table_data(NLCD_MANNING)
            elif is_dl_esri or lulc_source == "user_lulc":
                self._table.set_table_data(SENTINEL2_MANNING)

        # Clear browsed file when switching modes
        self._raster_edit.clear()
        if hasattr(self, "_report"):
            self._report.setVisible(False)
        if hasattr(self, "_error_lbl"):
            self._error_lbl.setVisible(False)

    def _browse_raster(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "Select raster", "", "GeoTIFF (*.tif *.tiff)"
        )
        if f:
            self._raster_edit.setText(f)

    # ── Worker callbacks ────────────────────────────────────────────────────────
    def _on_message(self, msg):
        self._log(msg)
        msg_l = msg.lower()
        if "downloading lulc" in msg_l or "fetching lulc" in msg_l or "requesting" in msg_l:
            self._progress.setValue(10)
        elif "tile" in msg_l and ("download" in msg_l or "fetch" in msg_l):
            m = re.search(r"(\d+)\s*/\s*(\d+)", msg)
            if m:
                done, total = int(m.group(1)), int(m.group(2))
                if total > 0:
                    self._progress.setValue(int(done / total * 60))
        elif "merging" in msg_l or "mosaic" in msg_l:
            self._progress.setValue(65)
        elif "clipping" in msg_l or "reprojecting" in msg_l:
            self._progress.setValue(75)
        elif "writing friction" in msg_l or "writing ascii" in msg_l or "ascii saved" in msg_l:
            self._progress.setValue(90)
        elif "manning step complete" in msg_l or "complete" in msg_l:
            self._progress.setValue(100)

    def _run_step(self):
        if not self._ctx_path or not self._ctx:
            self._log("Complete earlier steps first.")
            return

        idx = self._mode_combo.currentIndex()
        fric_mode, lulc_source = _MODE_PARAMS[idx]
        raster_path = self._raster_edit.text().strip() or None

        # Validate raster selection for modes that require a file
        if lulc_source in ("user_lulc", "user_manning"):
            if not raster_path:
                self._log("ERROR: Please browse and select a raster file.")
                return
            if not Path(raster_path).exists():
                self._log(f"ERROR: File not found: {raster_path}")
                return

        if fric_mode == "fixed":
            kw = dict(
                ctx_path=self._ctx_path,
                ctx=self._ctx,
                fric_mode="fixed",
                fpfric_val=self._fixed_spin.value(),
            )
        else:
            kw = dict(
                ctx_path=self._ctx_path,
                ctx=self._ctx,
                fric_mode="varying",
                lulc_source=lulc_source,
                # core expects user_lulc_path / user_manning_path, not raster_path
                user_lulc_path=raster_path if lulc_source == "user_lulc" else None,
                user_manning_path=raster_path if lulc_source == "user_manning" else None,
                # lulc_year is a direct param (not read from ctx)
                lulc_year=(
                    int(self._lulc_year_combo.currentText())
                    if lulc_source == "download"
                    else None
                ),
                nlcd_year=self._nlcd_year_combo.currentText(),
                # core expects lulc_class_to_n, not manning_mapping
                lulc_class_to_n=self._get_table_mapping(),
            )

        self._error_lbl.setVisible(False)
        self._report.setVisible(False)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        set_running(self._run_btn)

        self._worker = Worker(prepare_triton_manning, **kw)
        self._worker.message.connect(self._on_message)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_done(self, ctx):
        self._error_lbl.setVisible(False)
        self._ctx = ctx
        self._progress.setValue(100)
        set_ready(self._run_btn)
        self._show_report(ctx)
        self.step_completed.emit({"ctx_path": self._ctx_path, "ctx": ctx})

    def _on_error(self, msg):
        self._log(f"ERROR: {msg}")
        self._progress.setVisible(False)
        set_ready(self._run_btn)
        first_line = msg.split("\n")[0]
        self._error_lbl.setText(
            f"❌ <b>Error:</b> {first_line}<br>"
            "<small>(See log panel below for full details)</small>"
        )
        self._error_lbl.setVisible(True)

    def _show_report(self, ctx):
        # Core stores these under triton_fric_mode / par_fpfric
        fric_mode    = ctx.get("triton_fric_mode", "")
        project_dir  = ctx.get("project_dir", "")
        triton_dir   = ctx.get("triton_dir", "")
        aoi_name     = ctx.get("aoi_name", "")

        if fric_mode == "fixed":
            fpfric = ctx.get("par_fpfric", "")
            html = (
                f"<b>✅ Friction prepared (fixed value).</b><br><br>"
                f"<b>Manning n:</b> {fpfric}<br>"
                f"<b>Note:</b> Fixed n will be written directly into friction.asc "
                f"as a uniform matrix."
            )
        else:
            lulc_source   = ctx.get("lulc_source", "")
            lulc_tif      = ctx.get("lulc_path", "")
            manning_tif   = ctx.get("manning_tif_path", "")
            friction_asc  = ctx.get(
                "friction_asc_path",
                str(Path(triton_dir) / "friction.asc"),
            )

            source_label = {
                "download":     "Downloaded from ESRI Sentinel-2 LULC service",
                "user_lulc":    "User-provided LULC raster",
                "user_manning": "User-provided Manning n raster",
            }.get(lulc_source, lulc_source)

            if lulc_source == "download":
                lulc_year  = ctx.get("lulc_year", "")
                tiles_dir  = str(
                    Path(project_dir) / f"_tiles_LULC_{aoi_name}_{lulc_year}"
                )
                source_line = (
                    f"<b>Source:</b> {source_label} ({lulc_year})<br>"
                    f"<b>Raw LULC tiles folder:</b> {tiles_dir}<br>"
                )
            else:
                source_line = f"<b>Source:</b> {source_label}<br>"

            html = (
                f"<b>✅ Friction file prepared (spatially varying).</b><br><br>"
                + source_line
                + (f"<b>LULC GeoTIFF:</b> {lulc_tif}<br>" if lulc_tif else "")
                + (f"<b>Manning n GeoTIFF:</b> {manning_tif}<br>" if manning_tif else "")
                + f"<b>friction.asc (for TRITON):</b> {friction_asc}"
            )

        self._report.setText(html)
        self._report.setVisible(True)
