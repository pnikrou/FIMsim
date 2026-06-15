"""Step 2 — Load AOI shapefile."""
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFileDialog, QGroupBox, QFormLayout,
    QDialog, QDialogButtonBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QMessageBox,
)
from PyQt6.QtCore import pyqtSignal

from core.aoi import load_aoi, inspect_aoi
from gui.worker import Worker
from gui.run_button import set_running, set_ready


class _FeaturePickerDialog(QDialog):
    """Modal dialog that lists shapefile features so the user picks one."""

    def __init__(self, summaries, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Multiple features detected")
        self.resize(600, 320)
        self.chosen_index = None

        layout = QVBoxLayout(self)

        lbl = QLabel(
            f"<b>The shapefile has {len(summaries)} features.</b><br>"
            "Select the one you want to use as the Area of Interest:"
        )
        lbl.setWordWrap(True)
        layout.addWidget(lbl)

        # Build column headers from the first summary's keys
        keys = list(summaries[0].keys())
        self._table = QTableWidget(len(summaries), len(keys))
        self._table.setHorizontalHeaderLabels(keys)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        for r, s in enumerate(summaries):
            for c, k in enumerate(keys):
                val = s.get(k, "")
                if isinstance(val, float):
                    val = f"{val:.2f}"
                self._table.setItem(r, c, QTableWidgetItem(str(val)))

        h = self._table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        h.setStretchLastSection(True)
        self._table.selectRow(0)
        layout.addWidget(self._table)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _accept(self):
        rows = self._table.selectionModel().selectedRows()
        if rows:
            self.chosen_index = rows[0].row()
        self.accept()


class StepAOIWidget(QWidget):
    step_completed = pyqtSignal(dict)

    def __init__(self, log_fn, parent=None):
        super().__init__(parent)
        self._log = log_fn
        self._worker = None
        self._ctx_path = None
        self._ctx = None
        self._feature_index = None
        self._setup_ui()

    def set_context(self, ctx_path, ctx):
        self._ctx_path = ctx_path
        self._ctx = ctx

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        gb = QGroupBox("2. Load Area of Interest (AOI)")
        form = QFormLayout(gb)

        row = QHBoxLayout()
        self._aoi_edit = QLineEdit()
        self._aoi_edit.setPlaceholderText("path/to/your_aoi.shp  or  aoi.gpkg")
        browse_btn = QPushButton("Browse…")
        browse_btn.setFixedWidth(80)
        browse_btn.clicked.connect(self._browse_aoi)
        row.addWidget(self._aoi_edit)
        row.addWidget(browse_btn)
        form.addRow("AOI file (.shp / .gpkg):", row)
        layout.addWidget(gb)

        # Run button
        btn_row = QHBoxLayout()
        self._run_btn = QPushButton("Load AOI")
        self._run_btn.setStyleSheet(
            "font-weight:bold; padding:7px 20px; background:#2b6cb0; color:white; border-radius:4px;"
        )
        btn_row.addWidget(self._run_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Error label
        self._error_lbl = QLabel("")
        self._error_lbl.setWordWrap(True)
        self._error_lbl.setStyleSheet(
            "padding:10px; background:#fff5f5; border:1px solid #fc8181; "
            "border-radius:4px; font-size:12px; color:#c53030;"
        )
        self._error_lbl.setVisible(False)
        layout.addWidget(self._error_lbl)

        # Report
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

    def _browse_aoi(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select AOI file", "",
            "AOI files (*.shp *.gpkg);;Shapefile (*.shp);;GeoPackage (*.gpkg)"
        )
        if files:
            self._aoi_edit.setText(files[0])

    def _run_step(self):
        if not self._ctx_path or not self._ctx:
            self._log("Complete Step 1 first.")
            return
        aoi_path = self._aoi_edit.text().strip()
        if not aoi_path:
            self._log("Please select an AOI file (.shp or .gpkg).")
            return

        self._error_lbl.setVisible(False)
        self._report.setVisible(False)

        # ── Inspect for multiple features ────────────────────────────────────
        try:
            aoi_gdf, summaries = inspect_aoi(aoi_path)
        except Exception as ex:
            self._error_lbl.setText(f"<b>Error reading AOI:</b> {ex}")
            self._error_lbl.setVisible(True)
            return

        self._feature_index = None

        if len(summaries) > 1:
            dlg = _FeaturePickerDialog(summaries, parent=self)
            if dlg.exec() != QDialog.DialogCode.Accepted or dlg.chosen_index is None:
                self._log("AOI load cancelled — no feature selected.")
                return
            self._feature_index = dlg.chosen_index
            self._log(f"User selected feature index {self._feature_index}.")

        # ── Launch the worker ────────────────────────────────────────────────
        set_running(self._run_btn)
        self._worker = Worker(load_aoi,
                              ctx_path=self._ctx_path, ctx=self._ctx,
                              aoi_path=aoi_path,
                              feature_index=self._feature_index)
        self._worker.message.connect(self._log)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_done(self, ctx):
        self._ctx = ctx
        set_ready(self._run_btn)
        self._show_report(ctx)
        self.step_completed.emit({"ctx_path": self._ctx_path, "ctx": ctx})

    def _on_error(self, msg):
        self._log(f"ERROR: {msg}")
        set_ready(self._run_btn)
        self._error_lbl.setText(
            f"<b>Error:</b> {msg.split(chr(10))[0]}"
        )
        self._error_lbl.setVisible(True)

    def _show_report(self, ctx):
        import geopandas as gpd
        try:
            aoi_gdf = gpd.read_file(ctx["aoi_path"])
            fi = ctx.get("aoi_feature_index")
            if fi is not None and len(aoi_gdf) > 1:
                aoi_gdf = aoi_gdf.iloc[[fi]]
            bounds = aoi_gdf.total_bounds
            if aoi_gdf.crs and aoi_gdf.crs.is_geographic:
                aoi_proj = aoi_gdf.to_crs(aoi_gdf.estimate_utm_crs())
            else:
                aoi_proj = aoi_gdf
            area_km2 = aoi_proj.area.sum() / 1e6

            try:
                aoi_ll = aoi_gdf.to_crs(epsg=4326)
                b = aoi_ll.total_bounds
                coord_str = (
                    f"Lon: {b[0]:.5f}° to {b[2]:.5f}°  |  "
                    f"Lat: {b[1]:.5f}° to {b[3]:.5f}°"
                )
            except Exception:
                coord_str = (
                    f"X: {bounds[0]:.1f} to {bounds[2]:.1f}  |  "
                    f"Y: {bounds[1]:.1f} to {bounds[3]:.1f}  ({aoi_gdf.crs})"
                )

            feat_note = ""
            if fi is not None:
                feat_note = f"<b>Selected feature:</b> index {fi} (out of {gpd.read_file(ctx['aoi_path']).shape[0]} in file)<br>"

            html = (
                f"<b>AOI loaded successfully.</b><br><br>"
                f"<b>File:</b> {Path(ctx['aoi_path']).name}<br>"
                f"<b>CRS:</b> {aoi_gdf.crs}<br>"
                + feat_note +
                f"<b>Coordinates:</b> {coord_str}<br>"
                f"<b>Area:</b> {area_km2:.2f} km²"
            )
        except Exception as e:
            html = f"<b>AOI loaded.</b>  (Could not compute area: {e})"
        self._report.setText(html)
        self._report.setVisible(True)
