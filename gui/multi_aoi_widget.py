"""Reusable multi-AOI step widget.

UX flow:
  1. Start with one expanded block: [path] [Browse] [+ Add another AOI file]
  2. Loading a file inspects features into a checkbox table.
  3. Clicking "Add another AOI file" collapses the current block and inserts
     a new expanded block at the top.  The map preview shows ONLY the
     newest block's features (so 100 AOIs don't all clutter the map).
  4. Collapsed blocks show a compact one-line summary with [Edit] [Remove].
  5. "Confirm AOIs" creates per-feature subfolders and shows a Report panel
     listing every confirmed AOI as one short row (name + state + Remove).
  6. Click any row → details and map for that AOI appear in the bottom
     "Selected AOI details" panel.
  7. The widget DOES NOT auto-advance — the user clicks the big "Proceed
     to next step ▶" button when ready.
"""
from pathlib import Path
from typing import List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFileDialog, QGroupBox, QFormLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QCheckBox, QFrame, QScrollArea,
    QMessageBox, QSizePolicy,
)
from PyQt6.QtCore import pyqtSignal, Qt

import re

from core.multi_aoi import (
    AOIFeatureInfo, inspect_features, create_aoi_subfolders,
)
from core.river_lookup import lookup_main_river
from core.aoi_info import (
    lookup_huc6, lookup_huc8, lookup_usgs_gages,
    lookup_nhd_flowlines_clipped,
)
from core.multi_aoi import get_single_feature_gdf
from gui.map_viewer import USMapCanvas
from gui.worker import Worker


def _short_label(name: str) -> str:
    """Drop a trailing _NNN suffix for the map star label.

    e.g.  'case01_000' → 'case01',  'Waccamaw_001' → 'Waccamaw',
    but   'case01' stays 'case01'.
    """
    return re.sub(r"_\d+$", "", str(name)) or str(name)


# ─────────────────────────────────────────────────────────────────────────────
# Per-file block (expanded ↔ collapsed)
# ─────────────────────────────────────────────────────────────────────────────

class _AOIFileBlock(QFrame):
    """One row in the AOI list — has a collapsed and an expanded layout."""

    file_changed     = pyqtSignal()             # selection or load changed
    add_requested    = pyqtSignal(object, list)  # block ref, extra paths to auto-load
    remove_requested = pyqtSignal(object)       # block ref
    expand_requested = pyqtSignal(object)       # block ref — Edit on collapsed

    EXPANDED_STYLE = (
        "QFrame { background:#fafafa; border:2px solid #2b6cb0; "
        "border-radius:4px; padding:6px; }"
    )
    COLLAPSED_STYLE = (
        "QFrame { background:#edf2f7; border:1px solid #cbd5e0; "
        "border-radius:4px; padding:6px; }"
    )

    def __init__(self, log_fn, parent=None):
        super().__init__(parent)
        self._log = log_fn
        self._features: List[AOIFeatureInfo] = []
        self._collapsed = False
        self._build_layouts()
        self._show_expanded()

    # ── public ────────────────────────────────────────────────────────────────

    def file_path(self) -> str:
        return self._path_edit.text().strip()

    def features(self) -> List[AOIFeatureInfo]:
        return list(self._features)

    def selected_features(self) -> List[AOIFeatureInfo]:
        out = []
        for r, f in enumerate(self._features):
            chk = self._table.cellWidget(r, 0)
            if chk and chk.isChecked():
                out.append(f)
        return out

    def is_collapsed(self) -> bool:
        return self._collapsed

    def collapse(self):
        if self._collapsed:
            return
        self._collapsed = True
        self._update_collapsed_summary()
        self._expanded_widget.setVisible(False)
        self._collapsed_widget.setVisible(True)
        self.setStyleSheet(self.COLLAPSED_STYLE)

    def expand(self):
        if not self._collapsed:
            return
        self._collapsed = False
        self._show_expanded()

    def load_file(self, path: str):
        self._path_edit.setText(path)
        try:
            self._features = inspect_features(path, log_fn=self._log)
        except Exception as ex:
            self._log(f"ERROR loading {path}: {ex}")
            self._features = []
            self._table.setRowCount(0)
            return
        self._populate_table()
        self.file_changed.emit()

    # ── layouts ───────────────────────────────────────────────────────────────

    def _build_layouts(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 2)
        outer.setSpacing(0)

        # ── Expanded view ────────────────────────────────────────────────────
        self._expanded_widget = QWidget()
        v = QVBoxLayout(self._expanded_widget)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        row = QHBoxLayout()
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("path/to/aoi.shp  or  aoi.gpkg")
        browse = QPushButton("Browse…")
        browse.setFixedWidth(80)
        browse.clicked.connect(self._browse)
        add_btn = QPushButton("➕  Add another AOI file")
        add_btn.setStyleSheet(
            "background:#2b6cb0; color:white; padding:4px 12px; border-radius:3px;"
        )
        add_btn.clicked.connect(lambda: self.add_requested.emit(self, []))
        row.addWidget(QLabel("AOI file:"))
        row.addWidget(self._path_edit)
        row.addWidget(browse)
        row.addWidget(add_btn)
        v.addLayout(row)

        # 6 columns — first column shows the map star number (1, 2, 3, …) so
        # the user can match a table row to its dot on the map even if the
        # name is long.
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            ["Use", "#", "Name", "Area (km²)", "State", "Centroid (lon, lat)"]
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        # Use an explicit QFont on the header (Qt setFont overrides any QSS-
        # based rendering issue we hit on macOS) and give the header a
        # generous fixed height.  Apply NO stylesheet to QHeaderView::section
        # so the system style draws the background reliably.
        from PyQt6.QtGui import QFont
        header_font = QFont("Helvetica Neue", 12)
        header_font.setBold(True)
        header = self._table.horizontalHeader()
        header.setFont(header_font)
        header.setFixedHeight(34)
        header.setMinimumSectionSize(60)
        header.setDefaultSectionSize(120)
        header.setSectionsClickable(False)
        header.setHighlightSections(False)
        # Light styling on the body only — keep the header alone.
        self._table.setStyleSheet("""
            QTableWidget {
                background: #ffffff;
                alternate-background-color: #f7fafc;
                gridline-color: #e2e8f0;
            }
            QTableWidget::item { padding: 2px 6px; }
        """)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setDefaultSectionSize(26)
        h = self._table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)   # Use
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)   # #
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)            # Name
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)   # Area
        h.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)   # State
        h.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)   # Centroid
        # At least 3 rows + header visible so the user can see multi-feature files
        self._table.setMinimumHeight(140)
        self._table.setMaximumHeight(220)
        v.addWidget(self._table)

        sel_row = QHBoxLayout()
        all_btn = QPushButton("Select all features")
        all_btn.setFixedHeight(22)
        all_btn.clicked.connect(lambda: self._set_all(True))
        none_btn = QPushButton("Deselect all")
        none_btn.setFixedHeight(22)
        none_btn.clicked.connect(lambda: self._set_all(False))
        sel_row.addWidget(all_btn)
        sel_row.addWidget(none_btn)
        sel_row.addStretch()
        v.addLayout(sel_row)

        outer.addWidget(self._expanded_widget)

        # ── Collapsed view ───────────────────────────────────────────────────
        self._collapsed_widget = QWidget()
        cv = QHBoxLayout(self._collapsed_widget)
        cv.setContentsMargins(2, 2, 2, 2)
        cv.setSpacing(8)
        self._summary_lbl = QLabel("(empty)")
        self._summary_lbl.setStyleSheet("color:#2d3748;")
        edit_btn = QPushButton("Edit")
        edit_btn.setFixedWidth(60)
        edit_btn.clicked.connect(lambda: self.expand_requested.emit(self))
        rm_btn = QPushButton("Remove")
        rm_btn.setFixedWidth(80)
        rm_btn.setStyleSheet(
            "background:#fed7d7; color:#c53030; border-radius:3px; padding:3px 6px;"
        )
        rm_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        cv.addWidget(self._summary_lbl, 1)
        cv.addWidget(edit_btn)
        cv.addWidget(rm_btn)
        outer.addWidget(self._collapsed_widget)

    def _show_expanded(self):
        self._collapsed_widget.setVisible(False)
        self._expanded_widget.setVisible(True)
        self.setStyleSheet(self.EXPANDED_STYLE)

    def _update_collapsed_summary(self):
        path = self._path_edit.text().strip()
        name = Path(path).name if path else "(no file)"
        n = len(self._features)
        sel = sum(1 for f in self.selected_features())
        self._summary_lbl.setText(
            f"<b>{name}</b>  —  {sel} of {n} feature(s) selected"
        )

    # ── helpers ───────────────────────────────────────────────────────────────

    def _browse(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select AOI file(s)", "",
            "AOI files (*.shp *.gpkg);;Shapefile (*.shp);;GeoPackage (*.gpkg)"
        )
        if not files:
            return
        self.load_file(files[0])
        if len(files) > 1:
            self.add_requested.emit(self, files[1:])

    def _populate_table(self):
        self._table.setRowCount(len(self._features))
        for r, f in enumerate(self._features):
            chk = QCheckBox()
            chk.setChecked(True)
            chk.stateChanged.connect(lambda _s: self.file_changed.emit())
            self._table.setCellWidget(r, 0, chk)

            # "#" index column — will be overwritten with the actual star
            # number by MultiAOIWidget._on_block_changed() when the block is
            # active, so the same number is visible on the map.
            num_it = QTableWidgetItem(str(r + 1))
            name_it = QTableWidgetItem(f.name)
            area_it = QTableWidgetItem(f"{f.area_km2:.2f}")
            state_it = QTableWidgetItem(f.state_abbr or "—")
            ctr_it = QTableWidgetItem(f"{f.centroid_lon:.3f}, {f.centroid_lat:.3f}")
            for it in (num_it, name_it, area_it, state_it, ctr_it):
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
            for it in (num_it, area_it, state_it, ctr_it):
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(r, 1, num_it)
            self._table.setItem(r, 2, name_it)
            self._table.setItem(r, 3, area_it)
            self._table.setItem(r, 4, state_it)
            self._table.setItem(r, 5, ctr_it)

    def set_star_numbers(self, mapping):
        """Update the "#" column.  `mapping` is {row_index: star_number}.

        Must be keyed by integer row index because AOIFeatureInfo is a plain
        (unhashable) dataclass and can't be used as a dict key.
        """
        for r in range(self._table.rowCount()):
            if r in mapping:
                it = self._table.item(r, 1)
                if it:
                    it.setText(str(mapping[r]))

    def _set_all(self, checked: bool):
        for r in range(self._table.rowCount()):
            chk = self._table.cellWidget(r, 0)
            if chk:
                chk.setChecked(checked)


# ─────────────────────────────────────────────────────────────────────────────
# Multi-AOI widget
# ─────────────────────────────────────────────────────────────────────────────

class MultiAOIWidget(QWidget):
    """Reusable AOI step.

    Usage:
        w = MultiAOIWidget(log_fn)
        w.set_project_dir(project_dir)
        w.aoi_ready.connect(my_handler)   # receives list[AOIFeatureInfo]
        w.back_requested.connect(my_back_handler)   # user clicked back arrow

    aoi_ready is emitted ONLY when the user clicks the bottom "Proceed" button.
    """

    aoi_ready = pyqtSignal(list)   # list[AOIFeatureInfo]
    back_requested = pyqtSignal()  # user wants to return to the project step

    def __init__(self, log_fn, parent=None):
        super().__init__(parent)
        self._log = log_fn
        self._project_dir = None
        self._blocks: List[_AOIFileBlock] = []
        self._confirmed_features: List[AOIFeatureInfo] = []
        # Per-feature flow_result cache — used so that subsequent
        # ``_refresh_detail_map`` calls (e.g. from the gages worker) keep
        # showing the river that the flowlines worker already drew.
        # Keyed by the feature's id() since AOIFeatureInfo isn't hashable
        # by value.
        self._flow_result_cache: dict = {}
        self._selected_detail: Optional[AOIFeatureInfo] = None
        self._setup_ui()
        self._add_block()  # start with one empty AOI row

    # ── public ────────────────────────────────────────────────────────────────

    def set_project_dir(self, project_dir: str):
        self._project_dir = project_dir

    def _show_map(self):
        self._map_placeholder.setVisible(False)
        self._map_row.setVisible(True)
        self._map.setVisible(True)

    def _hide_map(self):
        self._map.setVisible(False)
        self._map_row.setVisible(False)
        self._aoi_side_table.setVisible(False)
        self._aoi_side_table.setRowCount(0)
        self._map_placeholder.setVisible(True)

    def _populate_side_table(self, features):
        """Fill the side table with one row per AOI in ``features``.
        Columns: #, Name, State."""
        self._aoi_side_table.setRowCount(len(features))
        for r, f in enumerate(features):
            num = QTableWidgetItem(str(r + 1))
            num.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._aoi_side_table.setItem(r, 0, num)
            self._aoi_side_table.setItem(r, 1, QTableWidgetItem(str(f.name)))
            state = (
                f"{f.state_name} ({f.state_abbr})"
                if (f.state_name and f.state_abbr)
                else (f.state_abbr or f.state_name or "—")
            )
            st = QTableWidgetItem(state)
            st.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._aoi_side_table.setItem(r, 2, st)
        self._aoi_side_table.setVisible(True)

    def reset(self):
        for b in list(self._blocks):
            self._remove_block(b, refresh=False)
        self._add_block()
        self._map.clear_plots()
        self._hide_map()
        self._report_gb.setVisible(False)
        self._detail_gb.setVisible(False)
        self._confirmed_features = []
        self._flow_result_cache.clear()
        # Reset upload area to expanded; hide the "Add more" button
        if hasattr(self, "_upload_area"):
            self._upload_area.setVisible(True)
        if hasattr(self, "_add_more_btn"):
            self._add_more_btn.setVisible(False)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setSpacing(8)

        # Short instructional banner — replaces the bare "Area of Interest"
        # title.  Tells the user the workflow in one sentence so they know
        # what to do without hunting for the Add / Confirm buttons.
        # Vertical size policy is Fixed so the banner always hugs its text
        # exactly — neither the upload-area collapse nor any other layout
        # shift can stretch or shrink it.
        intro = QLabel(
            "★ <b>Select your Area(s) of Interest (AOI).</b>  "
            "Browse for a shapefile or GeoPackage and pick the feature(s) you want.  "
            "To add more AOIs from another file, click "
            "<b>“➕ Add another AOI file”</b>.  "
            "When you're done, click <b>“Add to confirmed AOIs”</b> — "
            "then click <b>“Next step ▶”</b> at the bottom to continue."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#2d3748; font-size:12px; padding:2px 0px;")
        # Fixed vertical policy — the layout assigns this label exactly
        # ``sizeHint().height()`` (i.e. just enough to hold the wrapped
        # text at whatever width the parent has).  No more, no less.
        intro.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed,
        )
        outer.addWidget(intro, 0)

        # ── Upload area: scroll of blocks + confirm row ──
        # Wrapped in its own widget so it can be hidden after the user
        # confirms — the page then has more vertical space for the
        # confirmed-AOIs list.  An "Add more AOIs" button below brings
        # the upload area back when the user wants to add more.
        self._upload_area = QWidget()
        ua = QVBoxLayout(self._upload_area)
        ua.setContentsMargins(0, 0, 0, 0)
        ua.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        self._blocks_layout = QVBoxLayout(inner)
        self._blocks_layout.setSpacing(8)
        self._blocks_layout.addStretch()
        scroll.setWidget(inner)
        scroll.setFixedHeight(300)
        ua.addWidget(scroll)

        conf_row = QHBoxLayout()
        self._status = QLabel("0 AOI features selected.")
        self._status.setStyleSheet("color:#555;")
        self._confirm_btn = QPushButton("Add to confirmed AOIs")
        self._confirm_btn.setStyleSheet(
            "font-weight:bold; padding:7px 20px; background:#2b6cb0; "
            "color:white; border-radius:4px;"
        )
        self._confirm_btn.clicked.connect(self._confirm)
        conf_row.addWidget(self._status)
        conf_row.addStretch()
        conf_row.addWidget(self._confirm_btn)
        ua.addLayout(conf_row)

        outer.addWidget(self._upload_area)

        # "Add more AOIs" — visible only when the upload area is hidden,
        # i.e. after at least one confirm.  Clicking it re-shows the
        # upload area with a fresh empty block.
        more_row = QHBoxLayout()
        self._add_more_btn = QPushButton("➕  Add more AOIs")
        self._add_more_btn.setStyleSheet(
            "background:#2b6cb0; color:white; padding:6px 14px; "
            "border-radius:3px; font-weight:bold;"
        )
        self._add_more_btn.clicked.connect(self._on_add_more)
        self._add_more_btn.setVisible(False)
        more_row.addWidget(self._add_more_btn)
        more_row.addStretch()
        outer.addLayout(more_row)

        # Compact report panel (hidden until Confirm)
        self._report_gb = QGroupBox("Confirmed AOIs  —  click a row to see details below")
        self._report_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        rgl = QVBoxLayout(self._report_gb)
        self._report_inner = QVBoxLayout()
        self._report_inner.setSpacing(0)
        self._report_inner.setContentsMargins(0, 0, 0, 0)
        rgl.addLayout(self._report_inner)
        self._report_gb.setVisible(False)
        outer.addWidget(self._report_gb)

        # Bottom: detail card + map
        self._detail_gb = QGroupBox("Selected AOI details")
        self._detail_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        dgl = QVBoxLayout(self._detail_gb)
        self._detail_lbl = QLabel("(click any row above to see details here)")
        self._detail_lbl.setWordWrap(True)
        self._detail_lbl.setStyleSheet("color:#2d3748; padding:4px 2px;")
        dgl.addWidget(self._detail_lbl)
        self._detail_gb.setVisible(False)
        outer.addWidget(self._detail_gb)

        # ── Map preview: AOI side-table on the LEFT, canvas on the RIGHT ──
        # The side table replaces the in-figure legend overlay so the map
        # itself is no longer obscured when many AOIs are selected.
        self._gb_map = QGroupBox("Map preview")
        self._gb_map.setFixedHeight(320)
        m_outer = QVBoxLayout(self._gb_map)

        # Placeholder fills the whole group while no AOI is loaded.
        self._map_placeholder = QLabel(
            "<i>Load an AOI or click a confirmed AOI to see it on the map.</i>"
        )
        self._map_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._map_placeholder.setStyleSheet(
            "color:#888; padding:40px; background:#fafafa; "
            "border:1px dashed #cbd5e0; border-radius:4px;"
        )
        m_outer.addWidget(self._map_placeholder)

        # Active row: side table + canvas.  Visible when a feature set is
        # loaded; hidden when the placeholder is showing.
        self._map_row = QWidget()
        m_row = QHBoxLayout(self._map_row)
        m_row.setContentsMargins(0, 0, 0, 0)
        m_row.setSpacing(8)

        self._aoi_side_table = QTableWidget(0, 3)
        self._aoi_side_table.setHorizontalHeaderLabels(["#", "Name", "State"])
        self._aoi_side_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._aoi_side_table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        self._aoi_side_table.setAlternatingRowColors(True)
        self._aoi_side_table.verticalHeader().setVisible(False)
        sh = self._aoi_side_table.horizontalHeader()
        sh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        sh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        sh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._aoi_side_table.setMinimumWidth(220)
        self._aoi_side_table.setMaximumWidth(330)
        self._aoi_side_table.setVisible(False)
        m_row.addWidget(self._aoi_side_table, 0)

        self._map = USMapCanvas(self, width=10, height=4.0)
        self._map.setSizePolicy(QSizePolicy.Policy.Expanding,
                                QSizePolicy.Policy.Expanding)
        m_row.addWidget(self._map, 1)

        self._map_row.setVisible(False)
        m_outer.addWidget(self._map_row)

        outer.addWidget(self._gb_map)

        # NOTE: there is no per-step "Proceed to next step" button here on
        # purpose — the bottom-bar "Next step ▶" handles advancing.  When
        # the user clicks it on this tab, app.py calls proceed_to_next()
        # which commits the confirmed AOIs to ctx and emits step_completed.

    # ── block management ──────────────────────────────────────────────────────

    def _add_block(self, _from_block=None, pending_files=None):
        # Collapse all currently-expanded blocks (only one stays expanded)
        for b in self._blocks:
            if not b.is_collapsed():
                b.collapse()

        block = _AOIFileBlock(self._log, parent=self)
        block.file_changed.connect(self._on_block_changed)
        block.add_requested.connect(self._add_block)
        block.remove_requested.connect(self._remove_block)
        block.expand_requested.connect(self._on_expand_requested)
        # Insert at the TOP so newest is at top
        self._blocks_layout.insertWidget(0, block)
        self._blocks.append(block)

        if pending_files:
            block.load_file(pending_files[0])
            if len(pending_files) > 1:
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, lambda pf=pending_files[1:]: self._add_block(pending_files=pf))

        self._on_block_changed()

    def _check_duplicate_filename(self, new_path: str, source_block) -> bool:
        """Return True if another block (or a confirmed feature) already
        uses a shapefile with the same basename as new_path."""
        new_name = Path(new_path).name
        for b in self._blocks:
            if b is source_block:
                continue
            other = b.file_path()
            if other and Path(other).name == new_name:
                return True
        for f in self._confirmed_features:
            if Path(f.source_file).name == new_name:
                return True
        return False

    def _remove_block(self, block, refresh=True):
        if block in self._blocks:
            self._blocks.remove(block)
        block.setParent(None)
        block.deleteLater()
        if not self._blocks:
            self._add_block()
            return
        if refresh:
            self._on_block_changed()

    def _on_expand_requested(self, block):
        for b in self._blocks:
            if b is block:
                b.expand()
            else:
                b.collapse()
        self._on_block_changed()

    # ── helpers ───────────────────────────────────────────────────────────────

    def _all_selected(self) -> List[AOIFeatureInfo]:
        out: List[AOIFeatureInfo] = []
        for b in self._blocks:
            out.extend(b.selected_features())
        return out

    def _active_block(self) -> Optional[_AOIFileBlock]:
        for b in self._blocks:
            if not b.is_collapsed():
                return b
        return self._blocks[0] if self._blocks else None

    def _on_block_changed(self):
        # Duplicate-filename check: if any active block has the same shapefile
        # basename as another block (or a confirmed feature), warn the user.
        active = self._active_block()
        if active and active.file_path():
            if self._check_duplicate_filename(active.file_path(), active):
                QMessageBox.warning(
                    self, "Duplicate AOI filename",
                    f"An AOI file named "
                    f"<b>{Path(active.file_path()).name}</b> "
                    f"is already in this project. "
                    f"Two AOIs cannot share the same filename — "
                    f"they would write to the same subfolder.<br><br>"
                    f"Please choose a different file or rename it on disk.",
                )
                # Clear the duplicate path so the user can re-pick
                active._path_edit.clear()
                active._features = []
                active._table.setRowCount(0)

        # Refresh collapsed summaries
        for b in self._blocks:
            if b.is_collapsed():
                b._update_collapsed_summary()

        feats = self._all_selected()
        self._status.setText(
            f"{len(feats)} AOI feature(s) ready to confirm "
            f"from {len(self._blocks)} file(s)."
        )

        # Map shows the active (expanded) block's features.  Stars are
        # numbered 1, 2, 3, … and the same numbers are written into the "#"
        # column of the active block's table so the user can match them.
        active = self._active_block()
        if active and active.selected_features():
            active_feats = active.selected_features()
            # ── Single-AOI case: show full detail panel + 3-panel map for it
            #    (same UX as clicking a confirmed AOI in the report).
            if len(active_feats) == 1:
                self._show_feature_detail(active_feats[0])
                # Number the row "1" in the active block's table
                star_map_by_row = {}
                for r, f in enumerate(active.features()):
                    if f is active_feats[0]:
                        star_map_by_row[r] = 1
                        break
                active.set_star_numbers(star_map_by_row)
            else:
                # ── Multiple-AOI case: numbered dots on a single CONUS map.
                # The legend is rendered in a side table (populated below)
                # rather than as an overlay inside the figure, so the map
                # itself stays uncluttered.
                states = sorted({f.state_abbr for f in active_feats if f.state_abbr})
                points = [(f.centroid_lon, f.centroid_lat) for f in active_feats]
                labels = [str(i + 1) for i in range(len(active_feats))]
                self._map.update_plots(states, points, labels)
                self._populate_side_table(active_feats)
                self._show_map()
                star_map_by_row = {}
                star_counter = 1
                for r, f in enumerate(active.features()):
                    if f in active_feats:
                        star_map_by_row[r] = star_counter
                        star_counter += 1
                active.set_star_numbers(star_map_by_row)
                # Hide the per-AOI detail panel — it doesn't apply when many
                # AOIs are selected at once
                self._detail_gb.setVisible(False)
                self._selected_detail = None
        elif not self._confirmed_features:
            self._hide_map()
            self._detail_gb.setVisible(False)
            self._selected_detail = None
        else:
            # post-confirm state, no row clicked yet
            self._hide_map()
            self._detail_gb.setVisible(False)
            self._selected_detail = None
        # NOTE: the confirmed report stays visible until user explicitly
        # removes entries — adding new uploads doesn't dismiss it.

    # ── confirm + report ──────────────────────────────────────────────────────

    def _confirm(self):
        new_feats = self._all_selected()
        if not new_feats:
            QMessageBox.warning(
                self, "No AOIs selected",
                "Please add at least one AOI file and select at least one feature.",
            )
            return

        if not self._project_dir:
            QMessageBox.warning(
                self, "No project folder",
                "Project folder is not set.  Complete the project step first.",
            )
            return

        # Deduplicate against already-confirmed features (same source file +
        # same feature index counts as a duplicate)
        existing_keys = {(f.source_file, f.feature_index)
                         for f in self._confirmed_features}
        truly_new = [f for f in new_feats
                     if (f.source_file, f.feature_index) not in existing_keys]
        if not truly_new:
            QMessageBox.information(
                self, "Already confirmed",
                "All selected AOIs are already in the confirmed list.",
            )
            return

        try:
            truly_new = create_aoi_subfolders(
                self._project_dir, truly_new, log_fn=self._log
            )
        except Exception as ex:
            QMessageBox.critical(self, "Error creating subfolders", str(ex))
            return

        self._confirmed_features.extend(truly_new)
        self._log(
            f"{len(truly_new)} new AOI(s) confirmed.  "
            f"Total confirmed: {len(self._confirmed_features)}."
        )

        # Reset the upload area — clear all blocks so a click on
        # "Add more AOIs" later lands the user on a fresh empty block.
        # (_remove_block auto-recreates an empty block when the last one
        # goes, so no explicit _add_block() call is needed.)
        for b in list(self._blocks):
            self._remove_block(b, refresh=False)

        # Collapse the upload area to free vertical space for the
        # confirmed-AOIs list.  The "Add more AOIs" button lets the user
        # expand it again if they want more shapefiles.
        self._upload_area.setVisible(False)
        self._add_more_btn.setVisible(True)

        # Show the confirmed-AOIs report.  The bottom-bar "Next step ▶"
        # button is what advances; nothing extra to reveal here.
        self._build_report()
        self._report_gb.setVisible(True)
        # Clear any old detail panel; hide the map until a row is clicked
        self._detail_gb.setVisible(False)
        self._detail_lbl.setText("(click any row above to see details here)")
        self._map.clear_plots()
        self._hide_map()
        self._status.setText("0 AOI feature(s) ready to confirm.")

    def _build_report(self):
        # Clear previous content
        while self._report_inner.count():
            item = self._report_inner.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)

        for i, f in enumerate(self._confirmed_features, 1):
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(6)

            # Name button — plain link, no box
            label = f"{i}. {f.name}" + (f"  ({f.state_abbr})" if f.state_abbr else "")
            name_btn = QPushButton(label)
            name_btn.setStyleSheet(
                "QPushButton { text-align:left; background:transparent; "
                "border:none; color:#2d3748; padding:2px; }"
                "QPushButton:hover { color:#2b6cb0; text-decoration:underline; }"
            )
            name_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            name_btn.clicked.connect(lambda _checked, feat=f: self._show_feature_detail(feat))
            rl.addWidget(name_btn, 1)

            rm_btn = QPushButton("Remove")
            rm_btn.setFixedWidth(70)
            rm_btn.setStyleSheet(
                "background:transparent; color:#c53030; border:none; "
                "padding:2px 4px; font-size:11px;"
            )
            rm_btn.clicked.connect(lambda _checked, feat=f: self._remove_confirmed(feat))
            rl.addWidget(rm_btn)

            self._report_inner.addWidget(row)

    def _show_feature_detail(self, feature: AOIFeatureInfo):
        self._selected_detail = feature
        self._render_detail_panel(feature)
        self._detail_gb.setVisible(True)

        # Map: show this AOI's location.  Two-panel (US + state) at first;
        # third panel (AOI + river + gages) appears once those data land.
        states = [feature.state_abbr] if feature.state_abbr else []
        aoi_gdf = None
        try:
            aoi_gdf = get_single_feature_gdf(
                feature.source_file, feature.feature_index
            )
        except Exception:
            pass
        self._map.update_plots(
            states,
            [(feature.centroid_lon, feature.centroid_lat)],
            [_short_label(feature.name)],
            aoi_gdf=aoi_gdf,
        )
        # Single-AOI view doesn't need the side table
        self._aoi_side_table.setVisible(False)
        self._aoi_side_table.setRowCount(0)
        self._show_map()

        # Async river lookup
        if not feature.river_name:
            self._river_worker = Worker(
                lookup_main_river,
                aoi_path=feature.source_file,
                feature_index=feature.feature_index,
            )
            self._river_worker.message.connect(self._log)
            self._river_worker.finished.connect(
                lambda result, f=feature: self._on_river_resolved(f, result)
            )
            self._river_worker.error.connect(
                lambda msg, f=feature: self._on_river_resolved(f, None)
            )
            self._river_worker.start()

        # Async HUC6 lookup (bundled file → instant)
        if feature.huc6_codes is None:
            self._huc6_worker = Worker(
                lookup_huc6,
                aoi_path=feature.source_file,
                feature_index=feature.feature_index,
            )
            self._huc6_worker.message.connect(self._log)
            self._huc6_worker.finished.connect(
                lambda result, f=feature: self._on_huc6_resolved(f, result)
            )
            self._huc6_worker.error.connect(
                lambda msg, f=feature: self._on_huc6_resolved(f, [])
            )
            self._huc6_worker.start()

        # Async HUC8 lookup
        if feature.huc8_codes is None:
            self._huc8_worker = Worker(
                lookup_huc8,
                aoi_path=feature.source_file,
                feature_index=feature.feature_index,
            )
            self._huc8_worker.message.connect(self._log)
            self._huc8_worker.finished.connect(
                lambda result, f=feature: self._on_huc8_resolved(f, result)
            )
            self._huc8_worker.error.connect(
                lambda msg, f=feature: self._on_huc8_resolved(f, [])
            )
            self._huc8_worker.start()

        # Async USGS gages lookup
        if feature.usgs_gages is None:
            self._gages_worker = Worker(
                lookup_usgs_gages,
                aoi_path=feature.source_file,
                feature_index=feature.feature_index,
            )
            self._gages_worker.message.connect(self._log)
            self._gages_worker.finished.connect(
                lambda result, f=feature: self._on_gages_resolved(f, result)
            )
            self._gages_worker.error.connect(
                lambda msg, f=feature: self._on_gages_resolved(f, [])
            )
            self._gages_worker.start()

        # Async NHD flowlines (for the 3rd map panel — only when single AOI)
        self._flow_worker = Worker(
            lookup_nhd_flowlines_clipped,
            aoi_path=feature.source_file,
            feature_index=feature.feature_index,
        )
        self._flow_worker.message.connect(self._log)
        self._flow_worker.finished.connect(
            lambda result, f=feature: self._on_flowlines_resolved(f, result)
        )
        self._flow_worker.error.connect(
            lambda msg, f=feature: self._on_flowlines_resolved(f, (None, None))
        )
        self._flow_worker.start()

    def _render_detail_panel(self, feature: AOIFeatureInfo):
        # River
        if feature.river_name is None:
            river_str = "<i>(looking up…)</i>"
        else:
            river_str = feature.river_name or "—"
        # HUC6
        if feature.huc6_codes is None:
            huc6_str = "<i>(looking up…)</i>"
        elif not feature.huc6_codes:
            huc6_str = "—"
        else:
            huc6_str = ", ".join(feature.huc6_codes)
        # HUC8
        if feature.huc8_codes is None:
            huc8_str = "<i>(looking up…)</i>"
        elif not feature.huc8_codes:
            huc8_str = "—"
        else:
            huc8_str = ", ".join(feature.huc8_codes)
        # USGS gages
        if feature.usgs_gages is None:
            gages_str = "<i>(looking up…)</i>"
        elif not feature.usgs_gages:
            gages_str = "<i>None inside the AOI</i>"
        else:
            rows = []
            for g in feature.usgs_gages[:8]:
                site = g.get("site_no") or "?"
                name = g.get("station_nm") or ""
                rows.append(f"&nbsp;&nbsp;<b>{site}</b> &nbsp; {name}")
            gages_str = (
                f"{len(feature.usgs_gages)} found:<br>" + "<br>".join(rows)
            )
            if len(feature.usgs_gages) > 8:
                gages_str += f"<br>&nbsp;&nbsp;… and {len(feature.usgs_gages) - 8} more"

        html = (
            f"<b>{feature.name}</b>  "
            f"<span style='color:#888;'>(from {Path(feature.source_file).name})</span><br>"
            f"<b>Area:</b> {feature.area_km2:.2f} km²<br>"
            f"<b>Centroid:</b> {feature.centroid_lon:.4f}°, "
            f"{feature.centroid_lat:.4f}°<br>"
            f"<b>State:</b> {feature.state_name or '—'} "
            f"({feature.state_abbr or '—'})<br>"
            f"<b>HUC6:</b> {huc6_str}  &nbsp;|&nbsp;  "
            f"<b>HUC8:</b> {huc8_str}<br>"
            f"<b>Main river:</b> {river_str}<br>"
            f"<b>USGS gages in AOI:</b> {gages_str}"
        )
        self._detail_lbl.setText(html)

    def _on_river_resolved(self, feature: AOIFeatureInfo, river_name):
        feature.river_name = river_name or "—"
        if self._selected_detail is feature:
            self._render_detail_panel(feature)

    def _on_huc6_resolved(self, feature: AOIFeatureInfo, codes):
        feature.huc6_codes = list(codes) if codes else []
        if self._selected_detail is feature:
            self._render_detail_panel(feature)

    def _on_huc8_resolved(self, feature: AOIFeatureInfo, codes):
        feature.huc8_codes = list(codes) if codes else []
        if self._selected_detail is feature:
            self._render_detail_panel(feature)

    def _on_gages_resolved(self, feature: AOIFeatureInfo, gages):
        feature.usgs_gages = list(gages) if gages else []
        if self._selected_detail is feature:
            self._render_detail_panel(feature)
            # Refresh the map so the 3rd panel can include gages
            self._refresh_detail_map(feature)

    def _on_flowlines_resolved(self, feature: AOIFeatureInfo, result):
        # result is (clipped_flowlines_gdf, main_river_gdf) — both GeoDataFrames
        # in the AOI's CRS, or (None, None) on failure.
        # Cache the result per feature so a later redraw (e.g. when the
        # gages worker finishes) doesn't blank the river back out.
        self._flow_result_cache[id(feature)] = result
        if self._selected_detail is feature:
            self._refresh_detail_map(feature, flow_result=result)

    def _refresh_detail_map(self, feature: AOIFeatureInfo, flow_result=None):
        """Re-render the map for `feature`, including AOI/river/gage overlay
        if we have the data.  When the caller doesn't pass ``flow_result``,
        fall back to whatever the flowlines worker last cached for this
        feature so the river stays visible across redraws."""
        states = [feature.state_abbr] if feature.state_abbr else []
        try:
            aoi_gdf = get_single_feature_gdf(
                feature.source_file, feature.feature_index
            )
        except Exception:
            aoi_gdf = None
        if flow_result is None:
            flow_result = self._flow_result_cache.get(id(feature))
        river_gdf = flow_result[1] if flow_result else None
        self._map.update_plots(
            states,
            [(feature.centroid_lon, feature.centroid_lat)],
            [_short_label(feature.name)],
            aoi_gdf=aoi_gdf,
            main_river_gdf=river_gdf,
            usgs_gages=feature.usgs_gages,
        )

    def _remove_confirmed(self, feature: AOIFeatureInfo):
        # Confirm with user
        ans = QMessageBox.question(
            self, "Remove AOI?",
            f"Are you sure you want to delete <b>{feature.name}</b>?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        self._confirmed_features = [
            f for f in self._confirmed_features if f is not feature
        ]
        if self._selected_detail is feature:
            self._selected_detail = None
            self._detail_gb.setVisible(False)
            self._detail_lbl.setText("(click any row above to see details here)")
            self._map.clear_plots()
            self._hide_map()

        if not self._confirmed_features:
            self._report_gb.setVisible(False)
        else:
            self._build_report()

    def _on_add_more(self):
        """Re-show the upload area when the user wants to add more AOIs.

        ``_confirm`` already cleared the blocks list — make sure we have
        one fresh empty block ready, then expand the upload area.
        """
        if not self._blocks:
            self._add_block()
        self._upload_area.setVisible(True)
        self._add_more_btn.setVisible(False)

    def has_confirmed_aois(self) -> bool:
        """True iff the user has at least one AOI in the confirmed list."""
        return bool(self._confirmed_features)

    def confirmed_features(self) -> List[AOIFeatureInfo]:
        """The AOIs the user has added to the confirmed list (may be empty)."""
        return list(self._confirmed_features)

    def proceed_to_next(self) -> bool:
        """Commit confirmed AOIs to the parent ctx (via aoi_ready signal).

        Called by the bottom-bar "Next step ▶" button.  Warns the user with
        a popup if no AOIs are confirmed yet, instead of silently doing
        nothing — the user otherwise has no clue why nothing happened.

        Returns True if aoi_ready was emitted, False if blocked.
        """
        if not self._confirmed_features:
            QMessageBox.warning(
                self, "No AOIs confirmed",
                "Please confirm at least one AOI before proceeding to the "
                "next step.<br><br>"
                "Browse for an AOI file (.shp or .gpkg), select the feature(s) you "
                "want, then click <b>“Add to confirmed AOIs”</b>.",
            )
            return False
        self.aoi_ready.emit(self._confirmed_features)
        return True
