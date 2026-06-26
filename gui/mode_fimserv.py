"""
Author: Supath Dhital (sdhital@crimson.ua.edu)
Date updated: June 2026

FIMserv (OWP HAND FIM) standalone mode — 5-step wizard.

Tabs:
  1. Project    — same StepTritonProjectWidget as TRITON / LISFLOOD-FP
  2. AOI        — same StepTritonAOIWidget (multi-AOI) as TRITON / LISFLOOD-FP
  3. Download HUC8 — resolve HUC8 IDs from AOI (or enter directly) + download rasters
  4. Streamflow — NWM discharge (retrospective / forecast)
  5. Generate FIM — produce the flood inundation map
"""

from pathlib import Path
from typing import Optional, List, Dict

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QPushButton,
    QScrollArea, QTabWidget, QProgressBar, QGroupBox, QRadioButton,
    QLineEdit, QDateTimeEdit, QComboBox, QCheckBox, QFileDialog,
    QListWidget, QListWidgetItem, QAbstractItemView,
    QButtonGroup, QMessageBox, QSizePolicy,
)
from PyQt6.QtCore import pyqtSignal, Qt, QDateTime
from PyQt6.QtGui import QFont

from gui.step_triton_project import StepTritonProjectWidget
from gui.run_button import set_running, set_ready
from gui.worker import Worker
from gui.map_viewer import USMapCanvas
from gui.hydrograph_preview import HydrographPreviewCanvas
from gui.raster_preview import RasterPreviewCanvas
from core.state_lookup import detect_us_state
from core.FIMserv_api import (
    FIMservAPI,
    resolve_huc8_mode, download_huc8_mode, streamflow_mode, generate_fim_mode,
    discover_existing,
)


# ── Module-level worker functions for HUC8 detail lookups ────────────────────

def _huc8_river_lookup(gdf):
    """Return main river name for a HUC8 boundary GeoDataFrame (EPSG:4326).
    Reuses the NHD flowline query from river_lookup without needing a file path.
    """
    import ssl
    ssl._create_default_https_context = ssl._create_unverified_context
    try:
        from pynhd import NHD
        import geopandas as gpd
        nhd = NHD("flowline_mr")
        geom = (
            gdf.geometry.union_all()
            if hasattr(gdf.geometry, "union_all")
            else gdf.unary_union
        )
        try:
            flowlines = nhd.bygeom(geom)
        except Exception as ex:
            msg = str(ex)
            if "should be of type" in msg or "MultiPolygon" in msg:
                flowlines = nhd.bygeom(tuple(geom.bounds))
            else:
                raise
        if flowlines is None or flowlines.empty:
            return None
        flowlines = flowlines.to_crs(gdf.crs)
        clipped = gpd.overlay(flowlines, gdf[["geometry"]], how="intersection")
        clipped = clipped[
            clipped.geometry.type.isin(["LineString", "MultiLineString"])
        ].copy()
        if clipped.empty or "StreamOrde" not in clipped.columns:
            return None
        clipped["geom_len"] = clipped.geometry.length
        max_order = clipped["StreamOrde"].max()
        top = clipped[clipped["StreamOrde"] == max_order]
        names = (
            top["GNIS_NAME"].dropna().str.strip()
            if "GNIS_NAME" in top.columns else None
        )
        if names is not None and not names.empty:
            return names.mode().iloc[0] or None
        return None
    except Exception:
        return None


def _huc8_gages_lookup(gdf):
    """Return USGS gages list for a HUC8 boundary GeoDataFrame (EPSG:4326)."""
    import ssl
    ssl._create_default_https_context = ssl._create_unverified_context
    try:
        from pynhd import WaterData
        geom = (
            gdf.geometry.union_all()
            if hasattr(gdf.geometry, "union_all")
            else gdf.unary_union
        )
        try:
            result = WaterData("gagesii").bygeom(geom)
        except Exception as ex:
            msg = str(ex)
            if "should be of type" in msg or "MultiPolygon" in msg:
                result = WaterData("gagesii").bygeom(tuple(geom.bounds))
            else:
                raise
        if result is None or result.empty:
            return []
        gages = []
        for _, row in result.iterrows():
            site = (
                str(row.get("STAID") or row.get("site_no") or "").strip() or None
            )
            if site:
                site = site.zfill(8)
            name = str(row.get("STANAME") or row.get("station_nm") or "").strip() or None
            pt = row.geometry.centroid if row.geometry else None
            gages.append({
                "site_no":    site,
                "station_nm": name,
                "lat": float(pt.y) if pt else None,
                "lon": float(pt.x) if pt else None,
            })
        return gages
    except Exception:
        return []


_GB_STYLE = (
    "QGroupBox { background:#f9fafb; border:1px solid #e2e8f0; "
    "border-radius:6px; padding-top:8px; }"
)
_NOTE_STYLE = "color:#718096; font-size:11px;"
_RUN_STYLE = (
    "font-weight:bold; padding:8px 22px; background:#276749; "
    "color:white; border-radius:4px; font-size:13px;"
)

# Tab indices
_TAB_PROJECT    = 0
_TAB_AOI        = 1
_TAB_STREAMFLOW = 2
_TAB_FIM        = 3

_CONUS_ABBRS = {
    "AL","AR","AZ","CA","CO","CT","DC","DE","FL","GA","IA","ID",
    "IL","IN","KS","KY","LA","MA","MD","ME","MI","MN","MO","MS",
    "MT","NC","ND","NE","NH","NJ","NM","NV","NY","OH","OK","OR",
    "PA","RI","SC","SD","TN","TX","UT","VA","VT","WA","WI","WV","WY",
}


class _HUC8MapCanvas:
    """Simple CONUS map that highlights entered HUC8 polygons.

    The selected HUC8 (user clicked in the list) is drawn in orange;
    all others are drawn in light blue.  Created lazily so it shares
    the matplotlib/Qt lifecycle without circular imports.
    """
    def __new__(cls, parent=None, width=10.0, height=4.0):
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure

        class _Canvas(FigureCanvasQTAgg):
            def __init__(self, parent, width, height):
                self._fig = Figure(figsize=(width, height), tight_layout=True)
                super().__init__(self._fig)
                self.setParent(parent)
                self._states_gdf = None
                self._placeholder()

            def _placeholder(self):
                self._fig.clear()
                ax = self._fig.add_subplot(1, 1, 1)
                ax.set_xticks([]); ax.set_yticks([])
                ax.set_title(
                    "HUC8 Preview — add IDs above and click one to highlight",
                    fontsize=9, color="#718096",
                )
                try:
                    self.draw_idle()
                except Exception:
                    pass

            def show_huc8s(self, gdf, selected_id=None):
                from core.state_lookup import get_states_gdf
                if self._states_gdf is None:
                    try:
                        st = get_states_gdf()
                        self._states_gdf = (
                            st.set_crs(4326, inplace=True)
                            if st.crs is None else st.to_crs(4326)
                        )
                    except Exception:
                        self._states_gdf = None

                self._fig.clear()
                ax = self._fig.add_subplot(1, 1, 1)
                ax.set_xticks([]); ax.set_yticks([])

                if self._states_gdf is not None:
                    conus = self._states_gdf[
                        self._states_gdf["state_abbr"].str.upper().isin(_CONUS_ABBRS)
                    ]
                    conus.plot(ax=ax, facecolor="#f0f0f0",
                               edgecolor="#aaa", linewidth=0.4)

                gdf_4326 = gdf.to_crs("EPSG:4326")
                huc_col = (
                    "huc8" if "huc8" in gdf_4326.columns
                    else next(
                        (c for c in gdf_4326.columns if c.lower() == "huc8"),
                        gdf_4326.columns[0],
                    )
                )

                for _, row in gdf_4326.iterrows():
                    hid = str(row[huc_col]).zfill(8)
                    is_sel = (selected_id and hid == str(selected_id).zfill(8))
                    face = "#f6ad55" if is_sel else "#bee3f8"
                    edge = "#c05621" if is_sel else "#2b6cb0"
                    lw   = 2.0 if is_sel else 1.0
                    import geopandas as _gpd
                    _gpd.GeoDataFrame([row], crs=gdf_4326.crs).plot(
                        ax=ax, facecolor=face, edgecolor=edge,
                        linewidth=lw, alpha=0.85,
                    )
                    c = row.geometry.centroid
                    ax.annotate(
                        hid, (c.x, c.y),
                        fontsize=7, ha="center", va="center",
                        color="#1a365d", weight="bold",
                        bbox=dict(boxstyle="round,pad=0.1",
                                  facecolor="white", alpha=0.6, edgecolor="none"),
                    )

                minx, miny, maxx, maxy = gdf_4326.total_bounds
                px = max((maxx - minx) * 0.15, 1.5)
                py = max((maxy - miny) * 0.15, 1.5)
                ax.set_xlim(max(-126, minx - px), min(-65, maxx + px))
                ax.set_ylim(max(23, miny - py), min(51, maxy + py))
                n = len(gdf_4326)
                ax.set_title(
                    f"{n} HUC8 region{'s' if n != 1 else ''}"
                    + (f" — {selected_id} highlighted" if selected_id else ""),
                    fontsize=10,
                )
                try:
                    self.draw_idle()
                except Exception:
                    pass

        return _Canvas(parent, width, height)


class ModeFIMservWidget(QWidget):
    mode_finished = pyqtSignal()
    nav_changed   = pyqtSignal(int, int)

    def __init__(self, log_fn, parent=None):
        super().__init__(parent)
        self._log = log_fn
        self._state: Dict = {
            "project_dir": None,
            "ctx_path":    None,
            "ctx":         {},
            "aoi_path":    None,
            "huc8_ids":    [],
            "downloaded":  [],
        }
        self._worker: Optional[Worker] = None
        self._setup_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setSpacing(0)
        outer.setContentsMargins(0, 0, 0, 0)

        # ── fimserve availability banner ──────────────────────────────────────
        try:
            import fimserve  # noqa: F401
            self._fimserve_ok = True
        except ImportError:
            self._fimserve_ok = False

        if not self._fimserve_ok:
            banner = QLabel(
                "⚠  The <b>fimserve</b> package is not installed — "
                "Streamflow and Generate FIM steps will not work.  "
                "Install it first:  <code>pip install fimserve</code>"
            )
            banner.setWordWrap(True)
            banner.setStyleSheet(
                "background:#fffbeb; color:#744210; border:1px solid #f6ad55; "
                "border-radius:4px; padding:8px 12px; font-size:12px;"
            )
            outer.addWidget(banner)

        self._tabs = QTabWidget()
        self._tabs.setTabPosition(QTabWidget.TabPosition.North)
        self._tabs.currentChanged.connect(self._update_nav)
        outer.addWidget(self._tabs)

        # Step 1 — Project (identical widget to TRITON / LISFLOOD-FP)
        self._proj_step = StepTritonProjectWidget(self._log, model="generic")
        self._proj_step.step_completed.connect(self._on_project_done)

        self._tabs.addTab(self._wrap(self._proj_step),               "1. Project")
        self._tabs.addTab(self._wrap(self._build_aoi_choice_tab()), "2. AOI")
        self._tabs.addTab(self._wrap(self._build_streamflow_tab()), "3. Streamflow Data")
        self._tabs.addTab(self._wrap(self._build_fim_tab()),        "4. Generate FIM")

        self._tabs.setCurrentIndex(0)
        self._update_nav(0)

    def _wrap(self, w: QWidget) -> QScrollArea:
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setWidget(w)
        return sa

    # ── Step 2: AOI choice — AOI file OR HUC8 IDs ────────────────────────────

    def _build_aoi_choice_tab(self) -> QWidget:
        from PyQt6.QtWidgets import QStackedWidget
        from gui.step_triton_aoi import StepTritonAOIWidget

        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(0)
        v.setContentsMargins(0, 0, 0, 0)

        # ── Input-type selector — QGroupBox + QFormLayout, same as Manning step ─
        gb = QGroupBox("2. Area of Interest")
        gb_layout = QVBoxLayout(gb)

        form = QFormLayout()
        form.setContentsMargins(0, 4, 0, 4)

        self._aoi_type_combo = QComboBox()
        self._aoi_type_combo.addItem("—  pick an input type  —")
        self._aoi_type_combo.addItem("AOI file  (shapefile / GeoPackage)")
        self._aoi_type_combo.addItem("HUC8 IDs  (type them in directly)")
        self._aoi_type_combo.setFixedWidth(280)
        self._aoi_type_combo.currentIndexChanged.connect(self._on_aoi_choice_changed)
        form.addRow("<b>Input type:</b>", self._aoi_type_combo)
        gb_layout.addLayout(form)
        v.addWidget(gb)

        # ── Stacked content area (hidden until user picks a type) ────────────
        self._aoi_mode_stack = QStackedWidget()
        self._aoi_mode_stack.setVisible(False)

        # Page 0: full multi-AOI widget (same as TRITON / LISFLOOD-FP)
        self._aoi_step = StepTritonAOIWidget(self._log, model="generic")
        self._aoi_step.step_completed.connect(self._on_aoi_done)
        self._aoi_mode_stack.addWidget(self._aoi_step)           # index 0

        # Page 1: simple HUC8 entry panel
        self._aoi_mode_stack.addWidget(self._build_huc8_step_panel())  # index 1

        v.addWidget(self._aoi_mode_stack, 1)
        return page

    def _on_aoi_choice_changed(self, combo_index: int):
        # 0 = placeholder, 1 = AOI file, 2 = HUC8 IDs
        # Switching modes clears the OTHER mode so only one can be confirmed at a time.
        if combo_index == 0:
            self._aoi_mode_stack.setVisible(False)
        elif combo_index == 1:
            # Switching to AOI file — wipe any HUC8 data
            self._clear_huc8_selection()
            self._aoi_mode_stack.setCurrentIndex(0)
            self._aoi_mode_stack.setVisible(True)
        else:
            # Switching to HUC8 — wipe any AOI file data
            self._clear_aoi_selection()
            self._aoi_mode_stack.setCurrentIndex(1)
            self._aoi_mode_stack.setVisible(True)

    def _clear_huc8_selection(self):
        """Wipe all HUC8 entries and state so AOI file becomes the active mode."""
        self._aoi_huc8_ids.clear()
        self._aoi_huc8_gdf = None
        self._aoi_huc8_entry.clear()
        self._rebuild_huc8_rows()
        self._aoi_huc8_map.setVisible(False)
        self._aoi_huc8_map_placeholder.setVisible(True)
        self._aoi_huc8_status.setVisible(False)
        self._huc8_specific_list.clear()
        self._rb_huc8_range.setChecked(True)
        self._huc8_range_box.setVisible(True)
        self._huc8_specific_box.setVisible(False)
        self._huc8_detail_gb.setVisible(False)
        self._huc8_detail_lbl.setText("(click any HUC8 ID above to see details here)")
        self._huc8_detail_cache.clear()
        self._huc8_selected_id = None
        self._state["huc8_ids"] = []
        self._state.get("ctx", {}).pop("huc8_ids", None)
        self._state.get("ctx", {}).pop("huc8_date", None)

    def _clear_aoi_selection(self):
        """Wipe AOI file selection and state so HUC8 becomes the active mode."""
        if hasattr(self._aoi_step, "reset"):
            self._aoi_step.reset()
        self._state["aoi_path"] = None
        self._state.get("ctx", {}).pop("aoi_path", None)
        self._state.get("ctx", {}).pop("aoi_features", None)

    # ── HUC8 entry panel (AOI tab, page 1) ───────────────────────────────────

    def _build_huc8_step_panel(self) -> QWidget:
        panel = QWidget()
        v = QVBoxLayout(panel)
        v.setSpacing(10)
        v.setContentsMargins(14, 14, 14, 14)

        # ── HUC8 ID entry — matches Feature ID(s) style ───────────────────────
        gb = QGroupBox(); gb.setStyleSheet(_GB_STYLE)
        gv = QVBoxLayout(gb); gv.setSpacing(8)

        id_row = QHBoxLayout()
        id_lbl = QLabel("HUC8 ID(s):")
        id_lbl.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        id_row.addWidget(id_lbl)
        self._aoi_huc8_entry = QLineEdit()
        self._aoi_huc8_entry.setPlaceholderText("e.g. 03020201  (press Enter to add)")
        self._aoi_huc8_entry.returnPressed.connect(self._add_aoi_huc8)
        id_row.addWidget(self._aoi_huc8_entry, 1)
        csv_btn = QPushButton("Browse CSV…")
        csv_btn.setFixedWidth(110)
        csv_btn.clicked.connect(self._load_huc8_csv)
        id_row.addWidget(csv_btn)
        gv.addLayout(id_row)

        csv_note = QLabel(
            "★  CSV: one HUC8 ID per line, no header required.  "
            "Zero-padding applied automatically."
        )
        csv_note.setWordWrap(True)
        csv_note.setStyleSheet(_NOTE_STYLE)
        gv.addWidget(csv_note)

        # ── Date selection ────────────────────────────────────────────────────
        date_sep = QLabel("Date")
        date_sep.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        date_sep.setStyleSheet("color:#2d3748; margin-top:4px;")
        gv.addWidget(date_sep)

        date_mode_row = QHBoxLayout()
        self._huc8_date_grp = QButtonGroup(self)
        self._rb_huc8_range    = QRadioButton("Date range")
        self._rb_huc8_specific = QRadioButton("Specific date(s)")
        self._rb_huc8_range.setChecked(True)
        self._huc8_date_grp.addButton(self._rb_huc8_range,    0)
        self._huc8_date_grp.addButton(self._rb_huc8_specific, 1)
        self._rb_huc8_range.toggled.connect(self._on_huc8_date_mode_toggled)
        date_mode_row.addWidget(self._rb_huc8_range)
        date_mode_row.addWidget(self._rb_huc8_specific)
        date_mode_row.addStretch()
        gv.addLayout(date_mode_row)

        # Range panel
        self._huc8_range_box = QWidget()
        rr = QHBoxLayout(self._huc8_range_box)
        rr.setContentsMargins(0, 0, 0, 0); rr.setSpacing(10)
        rr.addWidget(QLabel("Start date:"))
        self._huc8_start = QDateTimeEdit()
        self._huc8_start.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._huc8_start.setCalendarPopup(True)
        self._huc8_start.setDateTime(QDateTime.fromString("2020-05-20 00:00", "yyyy-MM-dd HH:mm"))
        rr.addWidget(self._huc8_start)
        rr.addSpacing(16)
        rr.addWidget(QLabel("End date:"))
        self._huc8_end = QDateTimeEdit()
        self._huc8_end.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._huc8_end.setCalendarPopup(True)
        self._huc8_end.setDateTime(QDateTime.fromString("2020-05-22 00:00", "yyyy-MM-dd HH:mm"))
        rr.addWidget(self._huc8_end)
        rr.addStretch()
        gv.addWidget(self._huc8_range_box)

        # Specific-dates panel
        self._huc8_specific_box = QWidget()
        sv = QVBoxLayout(self._huc8_specific_box)
        sv.setContentsMargins(0, 0, 0, 0); sv.setSpacing(4)
        sp_row = QHBoxLayout()
        sp_row.addWidget(QLabel("Date / time:"))
        self._huc8_specific_dt = QDateTimeEdit()
        self._huc8_specific_dt.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._huc8_specific_dt.setCalendarPopup(True)
        self._huc8_specific_dt.setDateTime(
            QDateTime.fromString("2020-05-21 00:00", "yyyy-MM-dd HH:mm"))
        sp_row.addWidget(self._huc8_specific_dt)
        sp_add = QPushButton("Add"); sp_add.setFixedWidth(60)
        sp_add.clicked.connect(self._add_huc8_specific_date)
        sp_row.addWidget(sp_add)
        sp_rem = QPushButton("Remove"); sp_rem.setFixedWidth(70)
        sp_rem.clicked.connect(self._remove_huc8_specific_date)
        sp_row.addWidget(sp_rem)
        sp_row.addStretch()
        sv.addLayout(sp_row)
        self._huc8_specific_list = QListWidget()
        self._huc8_specific_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self._huc8_specific_list.setMaximumHeight(80)
        sv.addWidget(self._huc8_specific_list)
        self._huc8_specific_box.setVisible(False)
        gv.addWidget(self._huc8_specific_box)

        v.addWidget(gb)

        # ── HUC8 list — link-button rows, same style as AOI confirmed list ───
        self._aoi_huc8_list_gb = QGroupBox("HUC8 IDs — click a row to highlight on map")
        self._aoi_huc8_list_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        list_gb_layout = QVBoxLayout(self._aoi_huc8_list_gb)
        list_gb_layout.setContentsMargins(4, 8, 4, 4)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(130)
        scroll.setStyleSheet("QScrollArea { border:none; }")

        self._aoi_huc8_rows_widget = QWidget()
        self._aoi_huc8_rows_layout = QVBoxLayout(self._aoi_huc8_rows_widget)
        self._aoi_huc8_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._aoi_huc8_rows_layout.setSpacing(2)
        self._aoi_huc8_rows_layout.addStretch()
        scroll.setWidget(self._aoi_huc8_rows_widget)
        list_gb_layout.addWidget(scroll)

        add_more_btn = QPushButton("+ Add more HUC8 IDs")
        add_more_btn.setStyleSheet(
            "QPushButton { background:transparent; border:none; color:#2b6cb0; "
            "padding:4px 2px; font-size:12px; text-align:left; }"
            "QPushButton:hover { text-decoration:underline; }"
        )
        add_more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_more_btn.clicked.connect(lambda: (
            self._aoi_huc8_entry.setFocus(),
            self._aoi_huc8_entry.selectAll(),
        ))
        list_gb_layout.addWidget(add_more_btn)
        v.addWidget(self._aoi_huc8_list_gb)

        # ── Selected HUC8 details — same style as AOI's "Selected AOI details" ─
        self._huc8_detail_gb = QGroupBox("Selected HUC8 details")
        self._huc8_detail_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        dgl = QVBoxLayout(self._huc8_detail_gb)
        self._huc8_detail_lbl = QLabel("(click any HUC8 ID above to see details here)")
        self._huc8_detail_lbl.setWordWrap(True)
        self._huc8_detail_lbl.setStyleSheet("color:#2d3748; padding:4px 2px;")
        dgl.addWidget(self._huc8_detail_lbl)
        self._huc8_detail_gb.setVisible(False)
        v.addWidget(self._huc8_detail_gb)

        # ── Map preview — identical style to AOI map preview ─────────────────
        self._aoi_huc8_map_gb = QGroupBox("Map preview")
        self._aoi_huc8_map_gb.setStyleSheet("QGroupBox { font-weight:bold; }")
        self._aoi_huc8_map_gb.setFixedHeight(320)
        map_gb_layout = QVBoxLayout(self._aoi_huc8_map_gb)

        self._aoi_huc8_map_placeholder = QLabel(
            "<i>Load a HUC8 ID or click a confirmed HUC8 to see it on the map.</i>"
        )
        self._aoi_huc8_map_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._aoi_huc8_map_placeholder.setStyleSheet(
            "color:#888; padding:40px; background:#fafafa; "
            "border:1px dashed #cbd5e0; border-radius:4px;"
        )
        map_gb_layout.addWidget(self._aoi_huc8_map_placeholder)

        self._aoi_huc8_map = USMapCanvas(self, width=10, height=4.0)
        self._aoi_huc8_map.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._aoi_huc8_map.setVisible(False)
        map_gb_layout.addWidget(self._aoi_huc8_map)

        v.addWidget(self._aoi_huc8_map_gb)

        # ── Confirm button + status — same style/position as AOI option ─────
        confirm_row = QHBoxLayout()
        self._aoi_huc8_status = QLabel("")
        self._aoi_huc8_status.setWordWrap(True)
        self._aoi_huc8_status.setStyleSheet("color:#555;")
        confirm_row.addWidget(self._aoi_huc8_status)
        confirm_row.addStretch()
        confirm_btn = QPushButton("Add to confirmed HUC8")
        confirm_btn.setStyleSheet(
            "font-weight:bold; padding:7px 20px; background:#2b6cb0; "
            "color:white; border-radius:4px;"
        )
        confirm_btn.clicked.connect(self._confirm_aoi_huc8)
        confirm_row.addWidget(confirm_btn)
        v.addLayout(confirm_row)

        # internal state
        self._aoi_huc8_ids: List[str] = []
        self._aoi_huc8_gdf = None
        self._huc8_detail_cache: Dict[str, dict] = {}
        self._huc8_selected_id: Optional[str] = None
        self._huc8_river_worker: Optional[Worker] = None
        self._huc8_gages_worker: Optional[Worker] = None

        return panel

    def _rebuild_huc8_rows(self):
        """Rebuild the link-button rows from self._aoi_huc8_ids."""
        # Remove all widgets except the trailing stretch
        while self._aoi_huc8_rows_layout.count() > 1:
            item = self._aoi_huc8_rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for i, hid in enumerate(self._aoi_huc8_ids, 1):
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(4, 0, 4, 0)
            rl.setSpacing(6)

            btn = QPushButton(f"{i}.  {hid}")
            btn.setStyleSheet(
                "QPushButton { text-align:left; background:transparent; "
                "border:none; color:#2d3748; padding:2px; font-family:monospace; }"
                "QPushButton:hover { color:#2b6cb0; text-decoration:underline; }"
            )
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _checked, h=hid: self._on_aoi_huc8_clicked(h))
            rl.addWidget(btn, 1)

            rm = QPushButton("Remove")
            rm.setFixedWidth(66)
            rm.setStyleSheet(
                "background:transparent; color:#c53030; border:none; "
                "padding:2px 4px; font-size:11px;"
            )
            rm.setCursor(Qt.CursorShape.PointingHandCursor)
            rm.clicked.connect(lambda _checked, h=hid: self._remove_aoi_huc8(h))
            rl.addWidget(rm)

            self._aoi_huc8_rows_layout.insertWidget(
                self._aoi_huc8_rows_layout.count() - 1, row
            )

    def _add_aoi_huc8(self):
        raw = self._aoi_huc8_entry.text().strip()
        if not raw:
            return
        new_ids = [t.strip().zfill(8) for t in raw.replace(",", " ").split() if t.strip()]
        added = 0
        for hid in new_ids:
            if hid not in self._aoi_huc8_ids:
                self._aoi_huc8_ids.append(hid)
                added += 1
        self._aoi_huc8_entry.clear()
        if added:
            self._rebuild_huc8_rows()
            self._refresh_aoi_huc8_map()

    def _load_huc8_csv(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select HUC8 CSV file", "", "CSV files (*.csv);;All files (*)"
        )
        if not path:
            return
        try:
            added = 0
            with open(path, "r") as fh:
                for line in fh:
                    val = line.strip().strip('"').strip("'")
                    if not val:
                        continue
                    hid = val.zfill(8)
                    if hid not in self._aoi_huc8_ids:
                        self._aoi_huc8_ids.append(hid)
                        added += 1
            if added:
                self._rebuild_huc8_rows()
                self._refresh_aoi_huc8_map()
                self._log(f"Loaded {added} HUC8 ID(s) from {Path(path).name}")
            else:
                QMessageBox.information(
                    self, "No new IDs",
                    "All HUC8 IDs in the CSV are already in the list."
                )
        except Exception as ex:
            QMessageBox.critical(self, "CSV read error", str(ex))

    def _remove_aoi_huc8(self, huc_id: str):
        if huc_id in self._aoi_huc8_ids:
            self._aoi_huc8_ids.remove(huc_id)
        self._aoi_huc8_gdf = None
        self._rebuild_huc8_rows()
        if not self._aoi_huc8_ids:
            self._aoi_huc8_map.setVisible(False)
            self._aoi_huc8_map_placeholder.setVisible(True)
        else:
            self._refresh_aoi_huc8_gdf()

    def _on_aoi_huc8_clicked(self, huc_id: str):
        """Show map + details panel for the clicked HUC8."""
        if self._aoi_huc8_gdf is None:
            self._refresh_aoi_huc8_gdf()
        if self._aoi_huc8_gdf is None:
            return
        try:
            col = next(
                c for c in self._aoi_huc8_gdf.columns
                if c.lower() == "huc8"
            )
            single = self._aoi_huc8_gdf[
                self._aoi_huc8_gdf[col].astype(str).str.zfill(8) == huc_id
            ]
            if single.empty:
                return
            single_4326 = single.to_crs("EPSG:4326")
            centroid = single_4326.geometry.union_all().centroid
            lon, lat = centroid.x, centroid.y
            st = detect_us_state(single_4326)
            state_abbrs = [st["state_abbr"]] if st.get("state_abbr") else []

            # ── Map ───────────────────────────────────────────────────────────
            self._aoi_huc8_map_placeholder.setVisible(False)
            self._aoi_huc8_map.setVisible(True)
            self._aoi_huc8_map.update_plots(
                highlighted_state_abbrs=state_abbrs,
                aoi_points=[(lon, lat)],
                aoi_labels=[huc_id],
                huc8_gdf=single_4326,
            )

            # ── Details panel ─────────────────────────────────────────────────
            self._huc8_selected_id = huc_id

            # Seed the cache with sync data if not already present
            if huc_id not in self._huc8_detail_cache:
                try:
                    area_km2 = (
                        single.to_crs("EPSG:5070").geometry.area.sum() / 1e6
                    )
                except Exception:
                    area_km2 = None
                self._huc8_detail_cache[huc_id] = {
                    "area_km2":   area_km2,
                    "state_name": st.get("state_name"),
                    "state_abbr": st.get("state_abbr"),
                    "river_name": None,   # filled by async worker
                    "usgs_gages": None,   # filled by async worker
                }

            self._huc8_detail_gb.setVisible(True)
            self._render_huc8_detail(huc_id)

            cache = self._huc8_detail_cache[huc_id]

            # Async river lookup (only if not yet resolved)
            if cache["river_name"] is None:
                self._huc8_river_worker = Worker(
                    _huc8_river_lookup, single_4326.copy()
                )
                self._huc8_river_worker.message.connect(self._log)
                self._huc8_river_worker.finished.connect(
                    lambda r, h=huc_id: self._on_huc8_river_resolved(h, r)
                )
                self._huc8_river_worker.error.connect(
                    lambda _msg, h=huc_id: self._on_huc8_river_resolved(h, None)
                )
                self._huc8_river_worker.start()

            # Async USGS gages lookup (only if not yet resolved)
            if cache["usgs_gages"] is None:
                self._huc8_gages_worker = Worker(
                    _huc8_gages_lookup, single_4326.copy()
                )
                self._huc8_gages_worker.message.connect(self._log)
                self._huc8_gages_worker.finished.connect(
                    lambda r, h=huc_id: self._on_huc8_gages_resolved(h, r)
                )
                self._huc8_gages_worker.error.connect(
                    lambda _msg, h=huc_id: self._on_huc8_gages_resolved(h, [])
                )
                self._huc8_gages_worker.start()

        except Exception as ex:
            self._log(f"HUC8 map preview failed: {ex}")

    def _render_huc8_detail(self, huc_id: str):
        """Populate the 'Selected HUC8 details' label from the cache."""
        c = self._huc8_detail_cache.get(huc_id, {})
        area_str = (
            f"{c['area_km2']:.2f} km²" if c.get("area_km2") is not None else "—"
        )
        state_str = (
            f"{c['state_name']} ({c['state_abbr']})"
            if c.get("state_name") and c.get("state_abbr")
            else (c.get("state_abbr") or c.get("state_name") or "—")
        )
        river = c.get("river_name")
        river_str = river if river else "<i>(looking up…)</i>"

        gages = c.get("usgs_gages")
        if gages is None:
            gages_str = "<i>(looking up…)</i>"
        elif not gages:
            gages_str = "<i>None found inside this HUC8</i>"
        else:
            rows = []
            for g in gages[:8]:
                site = g.get("site_no") or "?"
                name = g.get("station_nm") or ""
                rows.append(f"&nbsp;&nbsp;<b>{site}</b> &nbsp; {name}")
            gages_str = f"{len(gages)} found:<br>" + "<br>".join(rows)
            if len(gages) > 8:
                gages_str += f"<br>&nbsp;&nbsp;… and {len(gages) - 8} more"

        html = (
            f"<b>HUC8 ID:</b> {huc_id}<br>"
            f"<b>Area:</b> {area_str}<br>"
            f"<b>State:</b> {state_str}<br>"
            f"<b>Main river:</b> {river_str}<br>"
            f"<b>USGS gages in HUC8:</b> {gages_str}"
        )
        self._huc8_detail_lbl.setText(html)

    def _on_huc8_river_resolved(self, huc_id: str, river_name):
        self._huc8_detail_cache.setdefault(huc_id, {})["river_name"] = river_name or "—"
        if self._huc8_selected_id == huc_id:
            self._render_huc8_detail(huc_id)

    def _on_huc8_gages_resolved(self, huc_id: str, gages):
        self._huc8_detail_cache.setdefault(huc_id, {})["usgs_gages"] = gages or []
        if self._huc8_selected_id == huc_id:
            self._render_huc8_detail(huc_id)

    def _refresh_aoi_huc8_gdf(self):
        """Load HUC8 boundary polygons for all current IDs into self._aoi_huc8_gdf."""
        ids = self._aoi_huc8_ids
        if not ids:
            return
        try:
            from core.aoi_info import _load_huc8_boundaries
            gdf = _load_huc8_boundaries()
            if gdf is None or gdf.empty:
                return
            col = ("huc8" if "huc8" in gdf.columns
                   else next((c for c in gdf.columns if c.lower() == "huc8"),
                              gdf.columns[0]))
            want = {str(x).zfill(8) for x in ids}
            hits = gdf[gdf[col].astype(str).str.zfill(8).isin(want)]
            if not hits.empty:
                self._aoi_huc8_gdf = hits
        except Exception as ex:
            self._log(f"HUC8 boundary load failed: {ex}")

    # keep old name as alias so reset() and _add_aoi_huc8 still work
    def _refresh_aoi_huc8_map(self, selected_id=None):
        self._refresh_aoi_huc8_gdf()

    # ── HUC8 date-mode helpers ────────────────────────────────────────────────

    def _on_huc8_date_mode_toggled(self, checked: bool):
        is_range = self._rb_huc8_range.isChecked()
        self._huc8_range_box.setVisible(is_range)
        self._huc8_specific_box.setVisible(not is_range)

    def _add_huc8_specific_date(self):
        dt_str = self._huc8_specific_dt.dateTime().toString("yyyy-MM-dd HH:mm")
        existing = [self._huc8_specific_list.item(i).text()
                    for i in range(self._huc8_specific_list.count())]
        if dt_str not in existing:
            self._huc8_specific_list.addItem(dt_str)

    def _remove_huc8_specific_date(self):
        for item in self._huc8_specific_list.selectedItems():
            self._huc8_specific_list.takeItem(
                self._huc8_specific_list.row(item))

    def _confirm_aoi_huc8(self):
        ids = self._aoi_huc8_ids
        if not ids:
            QMessageBox.warning(self, "No HUC8 IDs", "Add at least one HUC8 ID first.")
            return
        self._state["huc8_ids"] = list(ids)

        # Collect date selection
        if self._rb_huc8_range.isChecked():
            date_info = {
                "mode": "range",
                "start": self._huc8_start.dateTime().toString("yyyy-MM-dd HH:mm"),
                "end":   self._huc8_end.dateTime().toString("yyyy-MM-dd HH:mm"),
            }
        else:
            dates = [self._huc8_specific_list.item(i).text()
                     for i in range(self._huc8_specific_list.count())]
            date_info = {"mode": "specific", "dates": dates}
        self._state["huc8_date"] = date_info

        # Persist to context file so the IDs survive an app restart
        ctx = self._state.get("ctx") or {}
        ctx["huc8_ids"]  = list(ids)
        ctx["huc8_date"] = date_info
        self._state["ctx"] = ctx
        ctx_path = self._state.get("ctx_path")
        if ctx_path:
            try:
                from core.context import save_context
                save_context(ctx_path, ctx)
            except Exception:
                pass

        self._aoi_huc8_status.setText(
            f"✓  {len(ids)} HUC8 ID(s) confirmed and saved.  "
            "Proceed to step 3 (Streamflow Data)."
        )
        self._aoi_huc8_status.setStyleSheet(
            "color:#276749; font-size:12px; font-weight:bold;"
        )
        self._aoi_huc8_status.setVisible(True)
        self._log(
            f"AOI step (HUC8 mode) — confirmed {len(ids)} HUC8(s): "
            + ", ".join(ids)
        )

    # ── Step 3: Streamflow ────────────────────────────────────────────────────

    def _build_streamflow_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(12); v.setContentsMargins(14, 14, 14, 14)

        title = QLabel("NWM streamflow / discharge")
        title.setFont(QFont("Arial", 13, QFont.Weight.Bold))
        title.setStyleSheet("color:#2d3748;")
        v.addWidget(title)

        gb = QGroupBox(); gb.setStyleSheet(_GB_STYLE)
        gv = QVBoxLayout(gb); gv.setSpacing(8)

        src_row = QHBoxLayout()
        src_row.addWidget(QLabel("Source:"))
        self._src_grp = QButtonGroup(self)
        self._rb_retro = QRadioButton("Retrospective  (before 2023)")
        self._rb_fore  = QRadioButton("Forecast  (2023 onward)")
        self._rb_retro.setChecked(True)
        self._src_grp.addButton(self._rb_retro)
        self._src_grp.addButton(self._rb_fore)
        self._rb_retro.toggled.connect(self._on_source_toggled)
        src_row.addWidget(self._rb_retro)
        src_row.addWidget(self._rb_fore)
        src_row.addStretch()
        gv.addLayout(src_row)

        gv.addWidget(self._build_retro_group())
        gv.addWidget(self._build_forecast_group())

        self._sf_note = QLabel("")
        self._sf_note.setWordWrap(True); self._sf_note.setStyleSheet(_NOTE_STYLE)
        gv.addWidget(self._sf_note)
        v.addWidget(gb)

        run_row = QHBoxLayout()
        self._sf_btn = QPushButton("Get streamflow data")
        self._sf_btn.setStyleSheet(_RUN_STYLE)
        self._sf_btn.clicked.connect(self._get_streamflow)
        run_row.addWidget(self._sf_btn)
        run_row.addStretch()
        v.addLayout(run_row)

        self._sf_progress = QProgressBar(); self._sf_progress.setRange(0, 0)
        self._sf_progress.setVisible(False)
        v.addWidget(self._sf_progress)

        self._sf_status = QLabel("")
        self._sf_status.setWordWrap(True)
        self._sf_status.setStyleSheet(
            "color:#276749; font-size:12px; font-weight:bold;")
        self._sf_status.setVisible(False)
        v.addWidget(self._sf_status)

        self._hydro = HydrographPreviewCanvas(self, width=9, height=3.5)
        self._hydro.setVisible(False)
        v.addWidget(self._hydro)

        v.addStretch()
        self._on_source_toggled()
        return page

    def _build_retro_group(self) -> QWidget:
        box = QWidget()
        lv = QVBoxLayout(box); lv.setContentsMargins(0, 0, 0, 0); lv.setSpacing(6)

        self._retro_grp = QButtonGroup(self)
        self._rb_specific = QRadioButton("Specific event date(s) / time(s)")
        self._rb_range    = QRadioButton("Date range")
        self._rb_specific.setChecked(True)
        self._retro_grp.addButton(self._rb_specific)
        self._retro_grp.addButton(self._rb_range)
        self._rb_specific.toggled.connect(self._on_retro_submode_toggled)
        lv.addWidget(self._rb_specific)

        self._specific_box = QWidget()
        sb = QVBoxLayout(self._specific_box)
        sb.setContentsMargins(20, 0, 0, 0); sb.setSpacing(4)
        ev_row = QHBoxLayout()
        self._event_edit = QLineEdit()
        self._event_edit.setPlaceholderText("YYYY-MM-DD  or  YYYY-MM-DD HH:MM:SS")
        ev_row.addWidget(self._event_edit, 1)
        add_btn = QPushButton("Add"); add_btn.setFixedWidth(60)
        add_btn.clicked.connect(self._add_event_time)
        ev_row.addWidget(add_btn)
        del_btn = QPushButton("Remove"); del_btn.setFixedWidth(70)
        del_btn.clicked.connect(self._remove_event_time)
        ev_row.addWidget(del_btn)
        sb.addLayout(ev_row)
        self._event_list = QListWidget()
        self._event_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self._event_list.setMaximumHeight(90)
        self._event_list.setMinimumWidth(240)
        sb.addWidget(self._event_list)
        lv.addWidget(self._specific_box)

        lv.addWidget(self._rb_range)

        self._range_box = QWidget()
        rb = QVBoxLayout(self._range_box)
        rb.setContentsMargins(20, 0, 0, 0); rb.setSpacing(4)

        dt_row = QHBoxLayout()
        dt_row.addWidget(QLabel("Start date:"))
        self._sf_start = QDateTimeEdit()
        self._sf_start.setDisplayFormat("yyyy-MM-dd")
        self._sf_start.setCalendarPopup(True)
        self._sf_start.setDateTime(QDateTime.fromString("2020-05-20", "yyyy-MM-dd"))
        dt_row.addWidget(self._sf_start)
        dt_row.addSpacing(12)
        dt_row.addWidget(QLabel("End date:"))
        self._sf_end = QDateTimeEdit()
        self._sf_end.setDisplayFormat("yyyy-MM-dd")
        self._sf_end.setCalendarPopup(True)
        self._sf_end.setDateTime(QDateTime.fromString("2020-05-22", "yyyy-MM-dd"))
        dt_row.addWidget(self._sf_end)
        dt_row.addStretch()
        rb.addLayout(dt_row)

        rng_note = QLabel(
            "Start/end set the download window and the hydrograph preview. "
            "Add event time(s) within the range to save those hours (one FIM "
            "each) — or add none to use the aggregation below."
        )
        rng_note.setWordWrap(True); rng_note.setStyleSheet(_NOTE_STYLE)
        rb.addWidget(rng_note)

        self._timestep_lbl = QLabel("Event time(s) within the range:")
        rb.addWidget(self._timestep_lbl)
        tev_row = QHBoxLayout()
        self._range_event_edit = QLineEdit()
        self._range_event_edit.setPlaceholderText("YYYY-MM-DD  or  YYYY-MM-DD HH:MM:SS")
        tev_row.addWidget(self._range_event_edit, 1)
        radd = QPushButton("Add"); radd.setFixedWidth(60)
        radd.clicked.connect(self._add_range_event_time)
        tev_row.addWidget(radd)
        rdel = QPushButton("Remove"); rdel.setFixedWidth(70)
        rdel.clicked.connect(self._remove_range_event_time)
        tev_row.addWidget(rdel)
        rb.addLayout(tev_row)

        self._timestep_list = QListWidget()
        self._timestep_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self._timestep_list.setMaximumHeight(90)
        self._timestep_list.setMinimumWidth(240)
        self._timestep_list.itemSelectionChanged.connect(self._on_timesteps_picked)
        rb.addWidget(self._timestep_list)

        agg_row = QHBoxLayout()
        self._agg_lbl = QLabel("Aggregation (used when no event time picked):")
        agg_row.addWidget(self._agg_lbl)
        self._sort_by = QComboBox()
        self._sort_by.addItems(["maximum", "median", "minimum"])
        agg_row.addWidget(self._sort_by)
        agg_row.addStretch()
        rb.addLayout(agg_row)
        lv.addWidget(self._range_box)

        self._retro_box = box
        return box

    def _build_forecast_group(self) -> QWidget:
        box = QWidget()
        lv = QVBoxLayout(box); lv.setContentsMargins(0, 0, 0, 0); lv.setSpacing(6)

        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Forecast range:"))
        self._fc_range = QComboBox()
        self._fc_range.addItems(["shortrange", "mediumrange", "longrange"])
        self._fc_range.setCurrentText("mediumrange")
        self._fc_range.currentTextChanged.connect(self._on_fc_range_changed)
        r1.addWidget(self._fc_range)
        r1.addStretch()
        lv.addLayout(r1)

        self._fc_latest_chk = QCheckBox("Use latest available run")
        self._fc_latest_chk.setChecked(True)
        self._fc_latest_chk.toggled.connect(self._on_fc_latest_toggled)
        lv.addWidget(self._fc_latest_chk)

        r2 = QHBoxLayout()
        r2.addSpacing(20)
        self._fc_date_lbl = QLabel("Forecast date:")
        r2.addWidget(self._fc_date_lbl)
        self._fc_date = QDateTimeEdit()
        self._fc_date.setDisplayFormat("yyyy-MM-dd")
        self._fc_date.setCalendarPopup(True)
        self._fc_date.setDateTime(QDateTime.fromString("2024-06-01", "yyyy-MM-dd"))
        r2.addWidget(self._fc_date)
        r2.addSpacing(12)
        self._fc_hour_lbl = QLabel("Hour (UTC):")
        r2.addWidget(self._fc_hour_lbl)
        self._fc_hour = QComboBox()
        self._fc_hour.addItems([f"{h:02d}" for h in range(0, 24)])
        r2.addWidget(self._fc_hour)
        r2.addStretch()
        lv.addLayout(r2)

        far = QHBoxLayout()
        self._fc_agg_lbl = QLabel("Aggregation (medium / long range only):")
        far.addWidget(self._fc_agg_lbl)
        self._fc_sort_by = QComboBox()
        self._fc_sort_by.addItems(["maximum", "median", "minimum"])
        far.addWidget(self._fc_sort_by)
        far.addStretch()
        lv.addLayout(far)

        self._forecast_box = box
        return box

    # ── Step 4: Generate FIM ──────────────────────────────────────────────────

    def _build_fim_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(12); v.setContentsMargins(14, 14, 14, 14)

        title = QLabel("Generate flood inundation map")
        title.setFont(QFont("Arial", 13, QFont.Weight.Bold))
        title.setStyleSheet("color:#2d3748;")
        v.addWidget(title)

        gb = QGroupBox(); gb.setStyleSheet(_GB_STYLE)
        gv = QVBoxLayout(gb); gv.setSpacing(6)

        self._depth_chk = QCheckBox("Also produce a water-depth map  (optional)")
        self._depth_chk.setChecked(False)
        gv.addWidget(self._depth_chk)

        self._fim_note = QLabel(
            "★ Clicking 'Generate FIM' will automatically:\n"
            "  1. Resolve HUC8 IDs from your AOI (if AOI mode was used in step 2)\n"
            "  2. Download the OWP HAND HUC8 rasters\n"
            "  3. Generate the flood inundation map\n"
            "Previously-downloaded rasters are reused automatically."
        )
        self._fim_note.setWordWrap(True); self._fim_note.setStyleSheet(_NOTE_STYLE)
        gv.addWidget(self._fim_note)
        v.addWidget(gb)

        run_row = QHBoxLayout()
        self._fim_btn = QPushButton("Generate FIM")
        self._fim_btn.setStyleSheet(_RUN_STYLE)
        self._fim_btn.clicked.connect(self._generate)
        run_row.addWidget(self._fim_btn)
        run_row.addStretch()
        v.addLayout(run_row)

        self._fim_progress = QProgressBar(); self._fim_progress.setRange(0, 0)
        self._fim_progress.setVisible(False)
        v.addWidget(self._fim_progress)

        self._fim_status = QLabel("")
        self._fim_status.setWordWrap(True)
        self._fim_status.setStyleSheet("color:#2d3748; font-size:12px;")
        self._fim_status.setVisible(False)
        v.addWidget(self._fim_status)

        self._extent_canvas = RasterPreviewCanvas(self, width=9, height=3.8)
        self._extent_canvas.setVisible(False)
        v.addWidget(self._extent_canvas)

        self._depth_canvas = RasterPreviewCanvas(self, width=9, height=3.8)
        self._depth_canvas.setVisible(False)
        v.addWidget(self._depth_canvas)

        self._fim_files = QLabel("")
        self._fim_files.setWordWrap(True)
        self._fim_files.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self._fim_files.setStyleSheet("color:#4a5568; font-size:11px;")
        self._fim_files.setVisible(False)
        v.addWidget(self._fim_files)

        v.addStretch()
        return page

    # ── Slots: Project & AOI steps ────────────────────────────────────────────

    def _on_project_done(self, data: dict):
        ctx      = data.get("ctx", {})
        ctx_path = data.get("ctx_path")
        project_dir = ctx.get("project_dir")
        self._state["project_dir"] = project_dir
        self._state["ctx_path"]    = ctx_path
        self._state["ctx"]         = ctx

        # Pass the project context to the AOI step so it can write AOI info
        # into the same workflow_context.json file.
        self._aoi_step.set_context(ctx_path, ctx)

        # Re-opened project: detect any existing HUC8 data so the user can
        # resume rather than re-download everything from scratch.
        existing = {}
        try:
            existing = discover_existing(project_dir, log_fn=self._log)
        except Exception as ex:
            self._log(f"Could not scan existing project ({ex}).")

        ids = existing.get("huc8_ids") or []
        if ids:
            self._state["huc8_ids"]   = ids
            self._state["downloaded"] = existing.get("downloaded") or []
            self._log(
                f"Found existing data for {len(ids)} HUC8(s): {', '.join(ids)}. "
                "Already-finished steps will be skipped — go straight to the step you need."
            )
            # Jump to the first step that still has work remaining.
            if not existing.get("with_fim"):
                if not existing.get("with_discharge"):
                    self._tabs.setCurrentIndex(_TAB_STREAMFLOW)
                else:
                    self._tabs.setCurrentIndex(_TAB_FIM)
        else:
            self._log("Project ready — complete the AOI step, then generate the FIM.")

    def _on_aoi_done(self, data: dict):
        ctx      = data.get("ctx", {})
        ctx_path = data.get("ctx_path")
        self._state["ctx"]      = ctx
        self._state["ctx_path"] = ctx_path
        aoi_path = ctx.get("aoi_path")
        if aoi_path:
            self._state["aoi_path"] = aoi_path
        self._log(
            "AOI step complete — move to step 3 (Streamflow Data) then step 4 (Generate FIM)."
        )

    # ── Streamflow enable / disable logic ─────────────────────────────────────

    def _on_source_toggled(self, *_):
        retro = self._rb_retro.isChecked()
        self._retro_box.setVisible(retro)
        self._forecast_box.setVisible(not retro)
        if retro:
            self._on_retro_submode_toggled()
        else:
            self._on_fc_latest_toggled()
            self._on_fc_range_changed()
        self._hydro.setVisible(False)
        self._refresh_sf_note()

    def _on_retro_submode_toggled(self, *_):
        specific = self._rb_specific.isChecked()
        self._specific_box.setEnabled(specific)
        self._range_box.setEnabled(not specific)
        self._on_timesteps_picked()
        self._refresh_sf_note()

    def _on_timesteps_picked(self, *_):
        if self._rb_specific.isChecked():
            self._sort_by.setEnabled(False)
            self._agg_lbl.setEnabled(False)
            return
        has_event = bool(self._selected_event_times())
        self._sort_by.setEnabled(not has_event)
        self._agg_lbl.setEnabled(not has_event)

    def _add_range_event_time(self):
        txt = self._range_event_edit.text().strip()
        if not txt:
            return
        if not self._valid_event_str(txt):
            QMessageBox.warning(self, "Event time",
                                "Use YYYY-MM-DD or YYYY-MM-DD HH:MM:SS.")
            return
        for ph in ("(fetch the range first)", "(no timesteps available)"):
            for it in self._timestep_list.findItems(ph, Qt.MatchFlag.MatchExactly):
                self._timestep_list.takeItem(self._timestep_list.row(it))
        item = QListWidgetItem(txt)
        self._timestep_list.addItem(item)
        item.setSelected(True)
        self._timestep_list.setEnabled(True)
        self._range_event_edit.clear()
        self._on_timesteps_picked()
        self._refresh_sf_note()

    def _remove_range_event_time(self):
        for it in self._timestep_list.selectedItems():
            self._timestep_list.takeItem(self._timestep_list.row(it))
        self._on_timesteps_picked()
        self._refresh_sf_note()

    def _on_fc_latest_toggled(self, *_):
        manual = not self._fc_latest_chk.isChecked()
        for w in (self._fc_date_lbl, self._fc_date,
                  self._fc_hour_lbl, self._fc_hour):
            w.setEnabled(manual)

    def _on_fc_range_changed(self, *_):
        agg_ok = self._fc_range.currentText() in ("mediumrange", "longrange")
        self._fc_sort_by.setEnabled(agg_ok)
        self._fc_agg_lbl.setEnabled(agg_ok)

    @staticmethod
    def _valid_event_str(txt: str) -> bool:
        import datetime as _dt
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                _dt.datetime.strptime(txt, fmt)
                return True
            except ValueError:
                continue
        return False

    def _add_event_time(self):
        txt = self._event_edit.text().strip()
        if not txt:
            return
        if not self._valid_event_str(txt):
            QMessageBox.warning(self, "Event time",
                                "Use YYYY-MM-DD or YYYY-MM-DD HH:MM:SS.")
            return
        self._event_list.addItem(txt)
        self._event_edit.clear()
        self._refresh_sf_note()

    def _remove_event_time(self):
        for it in self._event_list.selectedItems():
            self._event_list.takeItem(self._event_list.row(it))
        self._refresh_sf_note()

    def _refresh_sf_note(self, *_):
        if self._rb_fore.isChecked():
            rng = self._fc_range.currentText()
            when = ("latest available run" if self._fc_latest_chk.isChecked()
                    else f"{self._fc_date.dateTime().toString('yyyy-MM-dd')} "
                         f"{self._fc_hour.currentText()}:00 UTC")
            self._sf_note.setText(f"★ NWM {rng} forecast — {when}.")
        elif self._rb_specific.isChecked():
            n = self._event_list.count()
            self._sf_note.setText(
                f"★ NWM retrospective — {n} specific event time(s); "
                "one discharge CSV (and one FIM) per time."
            )
        else:
            picked = self._selected_event_times()
            if picked:
                self._sf_note.setText(
                    f"★ NWM retrospective range — {len(picked)} event time(s) "
                    "picked; those hours are saved (aggregation ignored)."
                )
            else:
                self._sf_note.setText(
                    f"★ NWM retrospective range — aggregation "
                    f"({self._sort_by.currentText()}) over the window."
                )
        self._sf_note.setStyleSheet(_NOTE_STYLE)

    def _get_streamflow(self):
        if not self._fimserve_ok:
            QMessageBox.critical(
                self, "fimserve not installed",
                "The fimserve package is required for this step.\n\n"
                "Install it with:\n    pip install fimserve\n\n"
                "Then restart the app."
            )
            return
        ids = self._state.get("huc8_ids") or []
        if not ids:
            QMessageBox.warning(self, "No HUC8",
                                "Complete step 2 (AOI) first — enter an AOI file or HUC8 IDs.")
            return

        if self._rb_fore.isChecked():
            kwargs = dict(
                source="forecast",
                forecast_range=self._fc_range.currentText(),
                sort_by=self._fc_sort_by.currentText(),
            )
            if not self._fc_latest_chk.isChecked():
                kwargs["forecast_date"] = self._fc_date.dateTime().toString("yyyy-MM-dd")
                kwargs["forecast_hour"] = int(self._fc_hour.currentText())
        elif self._rb_specific.isChecked():
            times = [self._event_list.item(i).text()
                     for i in range(self._event_list.count())]
            if not times:
                QMessageBox.warning(self, "Event time",
                                    "Add at least one event date/time.")
                return
            kwargs = dict(source="retrospective", value_times=times)
        else:
            start = self._sf_start.dateTime().toPyDateTime()
            end   = self._sf_end.dateTime().toPyDateTime()
            if end <= start:
                QMessageBox.warning(self, "Dates",
                                    "End date must be after the start date.")
                return
            picked = self._selected_event_times()
            kwargs = dict(
                source="retrospective",
                start_date=start.strftime("%Y-%m-%d"),
                end_date=end.strftime("%Y-%m-%d"),
                value_times=picked,
                sort_by=self._sort_by.currentText(),
            )

        self._sf_progress.setVisible(True)
        self._set_busy(self._sf_status,
                       "Fetching NWM discharge — this can take a few minutes, "
                       "hold tight …")
        self._hydro.setVisible(False)
        set_running(self._sf_btn)
        self._start_worker(
            streamflow_mode,
            done=self._on_streamflow,
            project_dir=self._state["project_dir"],
            huc8_ids=ids,
            **kwargs,
        )

    def _selected_event_times(self):
        if not self._timestep_list.isEnabled():
            return None
        picked = [it.text() for it in self._timestep_list.selectedItems()
                  if it.text() not in ("(fetch the range first)",
                                       "(no timesteps available)")]
        return picked or None

    def _on_streamflow(self, result: dict):
        set_ready(self._sf_btn)
        self._sf_progress.setVisible(False)
        mode   = result.get("discharge_mode", "—")
        hydros = result.get("hydrographs", {})
        timesteps = result.get("timesteps", {})
        self._sf_status.setText(f"NWM {mode} discharge ready.")
        self._sf_status.setStyleSheet(
            "color:#276749; font-size:12px; font-weight:bold;")
        self._sf_status.setVisible(True)
        if hydros:
            huc, csv = next(iter(hydros.items()))
            stamps = timesteps.get(huc, [])
            existing = [
                self._timestep_list.item(i).text()
                for i in range(self._timestep_list.count())
                if self._timestep_list.item(i).text()
                not in ("(fetch the range first)", "(no timesteps available)")
            ]
            self._timestep_list.clear()
            merged = existing + [s for s in stamps if s not in existing]
            if merged:
                self._timestep_list.addItems(merged)
                self._timestep_list.setEnabled(True)
                self._on_timesteps_picked()
                self._sf_status.setText(
                    f"NWM {mode} discharge ready — pick in-range event time(s) "
                    "and re-run to save a FIM per time, or run as-is to aggregate."
                )
            else:
                self._timestep_list.addItem("(no timesteps available)")
                self._timestep_list.setEnabled(False)
            if csv and Path(csv).exists():
                self._hydro.show_hydrograph(
                    csv,
                    title=f"NWM {mode} — HUC8 {huc} (feature with max discharge)",
                )
                self._hydro.setVisible(True)

    # ── Generate FIM (auto-chain: resolve → download → generate) ─────────────

    def _generate(self):
        if not self._fimserve_ok:
            QMessageBox.critical(
                self, "fimserve not installed",
                "The fimserve package is required for this step.\n\n"
                "Install it with:\n    pip install fimserve\n\n"
                "Then restart the app."
            )
            return
        if not self._state.get("project_dir"):
            QMessageBox.warning(self, "No project",
                                "Complete the project setup in step 1 first.")
            return

        self._fim_progress.setVisible(True)
        self._extent_canvas.setVisible(False)
        self._depth_canvas.setVisible(False)
        self._fim_files.setVisible(False)
        set_running(self._fim_btn)

        # Phase 1: if no HUC8 IDs yet, resolve them from the AOI.
        if not self._state.get("huc8_ids"):
            aoi_path = self._state.get("aoi_path")
            if not aoi_path:
                set_ready(self._fim_btn)
                self._fim_progress.setVisible(False)
                QMessageBox.warning(
                    self, "No input",
                    "Complete step 2 first — select an AOI file or enter HUC8 IDs directly."
                )
                return
            self._set_busy(self._fim_status,
                           "Phase 1/3 — Resolving HUC8 IDs from AOI …")
            self._start_worker(
                resolve_huc8_mode,
                done=self._on_fim_resolved,
                project_dir=self._state["project_dir"],
                aoi_path=aoi_path,
                huc8_ids=None,
            )
        else:
            # HUC8 IDs already known (entered in step 2), skip resolve.
            self._do_download_then_generate()

    def _on_fim_resolved(self, result: dict):
        ids = result.get("huc8_ids", [])
        self._state["huc8_ids"] = ids
        if result.get("aoi_path"):
            self._state["aoi_path"] = result["aoi_path"]
        if not ids:
            set_ready(self._fim_btn)
            self._fim_progress.setVisible(False)
            self._fim_status.setText("Could not resolve HUC8 IDs from the AOI — check the AOI file.")
            self._fim_status.setStyleSheet("color:#c53030; font-size:12px; font-weight:bold;")
            self._fim_status.setVisible(True)
            return
        self._set_busy(self._fim_status,
                       f"Phase 2/3 — Downloading {len(ids)} HUC8 raster(s) …")
        self._do_download_then_generate()

    def _do_download_then_generate(self):
        ids = self._state.get("huc8_ids", [])
        self._set_busy(self._fim_status,
                       f"Phase 2/3 — Downloading {len(ids)} HUC8 raster(s) "
                       "(already-cached rasters are skipped) …")
        self._start_worker(
            download_huc8_mode,
            done=self._on_fim_downloaded,
            project_dir=self._state["project_dir"],
            huc8_ids=ids,
        )

    def _on_fim_downloaded(self, result: dict):
        ok = result.get("downloaded", [])
        self._state["downloaded"] = ok
        ids = ok or self._state.get("huc8_ids", [])
        self._set_busy(self._fim_status,
                       "Phase 3/3 — Generating flood inundation map …")
        self._start_worker(
            generate_fim_mode,
            done=self._on_fim,
            project_dir=self._state["project_dir"],
            huc8_ids=ids,
            aoi_path=self._state.get("aoi_path"),
            depth=self._depth_chk.isChecked(),
            binary=True,
            clip=True,
        )

    def _on_fim(self, result: dict):
        set_ready(self._fim_btn)
        self._fim_progress.setVisible(False)
        outputs = result.get("outputs", {})
        if not outputs:
            self._fim_status.setText(
                "No FIM produced — see the log for details.")
            self._fim_status.setStyleSheet(
                "color:#c53030; font-size:12px; font-weight:bold;")
            self._fim_status.setVisible(True)
            return
        self._fim_status.setText("Flood inundation map ready.")
        self._fim_status.setStyleSheet(
            "color:#276749; font-weight:bold; font-size:12px;")
        self._fim_status.setVisible(True)

        aoi_gdf = None
        aoi_path = self._state.get("aoi_path")
        if aoi_path:
            try:
                import geopandas as gpd
                aoi_gdf = gpd.read_file(aoi_path)
            except Exception:
                aoi_gdf = None

        extent_path = outputs.get("extent_clipped") or outputs.get("extent_mosaic")
        if extent_path and Path(extent_path).exists():
            self._extent_canvas.show_raster(
                extent_path, title="Flood extent (wet = 1 / dry = 0)",
                cmap="Blues", colorbar_label="Inundation",
                overlay_gdf=aoi_gdf,
            )
            self._extent_canvas.setVisible(True)

        depth_path = outputs.get("depth_clipped") or outputs.get("depth_mosaic")
        if depth_path and Path(depth_path).exists():
            self._depth_canvas.show_raster(
                depth_path, title="Water depth",
                cmap="viridis", colorbar_label="Depth (m)",
                overlay_gdf=aoi_gdf,
            )
            self._depth_canvas.setVisible(True)

        lines = []
        for key in ("extent_clipped", "extent_mosaic", "extent_binary",
                    "depth_clipped", "depth_mosaic"):
            val = outputs.get(key)
            if not val:
                continue
            if isinstance(val, list):
                names = ", ".join(Path(p).name for p in val if p)
                lines.append(f"{key}: {names}")
            else:
                lines.append(f"{key}: {Path(val).name}")
        if lines:
            self._fim_files.setText("Files: " + "  |  ".join(lines))
            self._fim_files.setVisible(True)

    # ── Worker plumbing ───────────────────────────────────────────────────────

    def _set_busy(self, label: QLabel, text: str):
        label.setText(text)
        label.setStyleSheet("color:#744210; font-size:12px; font-weight:bold;")
        label.setVisible(True)

    def _start_worker(self, fn, done, **kwargs):
        if self._worker is not None:
            try:
                self._worker.message.disconnect(self._log)
            except Exception:
                pass
            self._worker = None
        self._worker = Worker(fn, **kwargs)
        self._worker.message.connect(self._log)
        self._worker.finished.connect(done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_error(self, msg: str):
        for btn in (self._sf_btn, self._fim_btn):
            try:
                set_ready(btn)
            except Exception:
                pass
        for pb in (self._sf_progress, self._fim_progress):
            pb.setVisible(False)
        self._log(f"ERROR: {msg}")
        QMessageBox.critical(self, "FIMserv error", msg.splitlines()[0])

    # ── Navigation ────────────────────────────────────────────────────────────

    def _update_nav(self, idx: int):
        self.nav_changed.emit(idx, self._tabs.count())

    def go_prev(self):
        i = self._tabs.currentIndex()
        if i > 0:
            self._tabs.setCurrentIndex(i - 1)

    def go_next(self):
        i = self._tabs.currentIndex()
        if i < self._tabs.count() - 1:
            self._tabs.setCurrentIndex(i + 1)

    # ── Reset ─────────────────────────────────────────────────────────────────

    def reset(self):
        self._state = {
            "project_dir": None, "ctx_path": None, "ctx": {},
            "aoi_path": None, "huc8_ids": [], "downloaded": [],
        }
        if hasattr(self._proj_step, "reset"):
            self._proj_step.reset()
        if hasattr(self._aoi_step, "reset"):
            self._aoi_step.reset()
        # Reset AOI combo + HUC8 entry panel
        self._aoi_type_combo.setCurrentIndex(0)
        self._aoi_mode_stack.setVisible(False)
        self._aoi_mode_stack.setCurrentIndex(0)
        self._aoi_huc8_entry.clear()
        self._aoi_huc8_ids.clear()
        self._rebuild_huc8_rows()
        self._aoi_huc8_gdf = None
        self._aoi_huc8_map.setVisible(False)
        self._aoi_huc8_map_placeholder.setVisible(True)
        self._aoi_huc8_status.setVisible(False)
        self._huc8_detail_gb.setVisible(False)
        self._huc8_detail_lbl.setText("(click any HUC8 ID above to see details here)")
        self._huc8_detail_cache.clear()
        self._huc8_selected_id = None
        # Reset date selection to defaults
        self._rb_huc8_range.setChecked(True)
        self._huc8_start.setDateTime(
            QDateTime.fromString("2020-05-20 00:00", "yyyy-MM-dd HH:mm"))
        self._huc8_end.setDateTime(
            QDateTime.fromString("2020-05-22 00:00", "yyyy-MM-dd HH:mm"))
        self._huc8_specific_dt.setDateTime(
            QDateTime.fromString("2020-05-21 00:00", "yyyy-MM-dd HH:mm"))
        self._huc8_specific_list.clear()
        self._huc8_range_box.setVisible(True)
        self._huc8_specific_box.setVisible(False)
        self._rb_retro.setChecked(True)
        self._rb_specific.setChecked(True)
        self._event_edit.clear()
        self._event_list.clear()
        self._fc_latest_chk.setChecked(True)
        self._timestep_list.clear()
        self._timestep_list.addItem("(fetch the range first)")
        self._timestep_list.setEnabled(False)
        self._on_source_toggled()
        self._hydro.setVisible(False)
        self._extent_canvas.setVisible(False)
        self._depth_canvas.setVisible(False)
        self._fim_files.setVisible(False)
        for lbl in (self._sf_status, self._fim_status):
            lbl.setVisible(False)
        for pb in (self._sf_progress, self._fim_progress):
            pb.setVisible(False)
        for btn in (self._sf_btn, self._fim_btn):
            try:
                set_ready(btn)
            except Exception:
                pass
        self._refresh_sf_note()
        self._tabs.setCurrentIndex(0)
