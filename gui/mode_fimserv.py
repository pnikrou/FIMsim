"""
Author: Supath Dhital (sdhital@crimson.ua.edu)
Date updated: June 2026

FIMserv (OWP HAND FIM) standalone mode — 3-step wizard.

Tabs:
  1. Project — same StepTritonProjectWidget as TRITON / LISFLOOD-FP
  2. AOI     — AOI file (multi-shapefile) OR HUC8 IDs; on confirm, each item's
              HUC8 boundary is downloaded into its folder
  3. FIM     — one card per AOI / HUC8 (own Source + dates).  "Generate FIM
              for all" runs, per card: NWM discharge → download OWP HAND
              rasters → generate the flood inundation map, with X / Y progress.
"""

from pathlib import Path
from typing import Optional, List, Dict

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QPushButton,
    QScrollArea, QTabWidget, QProgressBar, QGroupBox, QRadioButton,
    QLineEdit, QDateTimeEdit, QComboBox, QCheckBox, QFileDialog,
    QListWidget, QListWidgetItem, QAbstractItemView,
    QButtonGroup, QMessageBox, QSizePolicy, QFrame,
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
# Collapsible streamflow card — same look as the LISFLOOD AOIDEMCard.
_SF_CARD_COLLAPSED = (
    "QFrame#card { background:#f9fafb; border:1px solid #e2e8f0; "
    "border-radius:6px; padding:6px; }"
)
_SF_CARD_EXPANDED = (
    "QFrame#card { background:#f9fafb; border:2px solid #a0aec0; "
    "border-radius:6px; padding:8px; }"
)

# ── Folder-setup worker functions (run in background thread) ─────────────────

def _download_huc8_boundary(huc8_ids: list, log_fn=print):
    """Download HUC8 boundary polygons from the USGS WBD service via pynhd.

    Returns a GeoDataFrame or None on failure.
    """
    try:
        import ssl
        ssl._create_default_https_context = ssl._create_unverified_context
        from pynhd import WaterData
        ids = [str(h).zfill(8) for h in huc8_ids]
        log_fn(f"  Downloading HUC8 boundary(s) from WBD: {', '.join(ids)} …")
        gdf = WaterData("wbd08").byid("huc8", ids)
        if gdf is not None and not gdf.empty:
            return gdf
    except Exception as ex:
        log_fn(f"  WBD download failed: {ex}")
    return None


def _setup_huc8_folders(project_dir: str, huc8_ids: list, log_fn=print) -> dict:
    """HUC8 mode: create one sub-folder per HUC8 ID and save its boundary polygon.

    Tries the bundled data/us_huc8.geojson first; falls back to the USGS WBD
    web service (pynhd) when the file is missing.
    Returns {"created": [huc8_id, ...], "skipped": [...]}
    """
    from pathlib import Path
    from core.aoi_info import _load_huc8_boundaries

    base    = Path(project_dir)
    bundled = _load_huc8_boundaries()          # None when file absent
    created, skipped = [], []

    for hid in huc8_ids:
        folder = base / hid
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except Exception as ex:
            log_fn(f"  Could not create folder {folder}: {ex}")
            skipped.append(hid)
            continue

        # Prefer bundled data (instant, offline); fall back to WBD download.
        row = None
        if bundled is not None and not bundled.empty:
            col = (
                "huc8" if "huc8" in bundled.columns
                else next((c for c in bundled.columns if c.lower() == "huc8"), None)
            )
            if col:
                row = bundled[bundled[col].astype(str).str.zfill(8) == hid]
                if row.empty:
                    row = None

        if row is None:
            row = _download_huc8_boundary([hid], log_fn=log_fn)

        out = folder / "huc8_bound.gpkg"
        if row is not None and not row.empty:
            try:
                row.to_file(str(out), driver="GPKG", layer="huc8_bound")
                log_fn(f"  Saved HUC8 boundary → {out.relative_to(base)}")
            except Exception as ex:
                log_fn(f"  Could not save boundary for {hid}: {ex}")
        else:
            log_fn(f"  Could not obtain boundary for {hid} — folder created, no .gpkg.")

        created.append(hid)

    return {"created": created, "skipped": skipped}


def _setup_aoi_huc8_folders(project_dir: str, aoi_features: list, log_fn=print) -> dict:
    """AOI mode: for each confirmed AOI, resolve its HUC8 IDs and save the
    HUC8 boundary polygon(s) in that AOI's project sub-folder.

    Tries the bundled data/us_huc8.geojson first; falls back to the USGS WBD
    web service (pynhd) when the file is missing.
    Returns {"processed": [aoi_name, ...], "skipped": [...]}
    """
    import re
    from pathlib import Path
    from core.aoi_info import _load_huc8_boundaries, lookup_huc8

    base    = Path(project_dir)
    bundled = _load_huc8_boundaries()          # None when file absent
    processed, skipped = [], []

    for feat in aoi_features:
        name        = feat.get("name") or "AOI"
        folder_path = feat.get("folder_path")
        src_file    = feat.get("source_file")
        feat_idx    = feat.get("feature_index", 0)

        # Resolve or create the AOI folder
        if folder_path:
            folder = Path(folder_path)
        else:
            safe = re.sub(r"[^\w\-]", "_", name)
            folder = base / safe
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except Exception as ex:
            log_fn(f"  Could not create folder for {name}: {ex}")
            skipped.append(name)
            continue

        # Resolve HUC8 codes (use pre-cached value from AOI confirm if present)
        huc8_codes = feat.get("huc8_codes") or []
        if not huc8_codes and src_file:
            log_fn(f"  Looking up HUC8 IDs for {name} …")
            huc8_codes = lookup_huc8(src_file, feat_idx, log_fn=log_fn)

        if not huc8_codes:
            log_fn(f"  No HUC8 IDs found for {name} — folder created, no .gpkg saved.")
            processed.append(name)
            continue

        # Fetch boundary polygons: bundled first, WBD download as fallback.
        rows = None
        want = {str(h).zfill(8) for h in huc8_codes}

        if bundled is not None and not bundled.empty:
            col = (
                "huc8" if "huc8" in bundled.columns
                else next((c for c in bundled.columns if c.lower() == "huc8"), None)
            )
            if col:
                rows = bundled[bundled[col].astype(str).str.zfill(8).isin(want)]
                if rows.empty:
                    rows = None

        if rows is None:
            rows = _download_huc8_boundary(list(want), log_fn=log_fn)

        if rows is not None and not rows.empty:
            out = folder / "huc8_bounds.gpkg"
            try:
                rows.to_file(str(out), driver="GPKG", layer="huc8_bounds")
                log_fn(
                    f"  Saved {len(rows)} HUC8 boundary(s) for {name} "
                    f"→ {out.relative_to(base)}"
                )
            except Exception as ex:
                log_fn(f"  Could not save HUC8 boundaries for {name}: {ex}")
        else:
            log_fn(f"  Could not obtain HUC8 boundaries for {name} — folder created, no .gpkg.")

        processed.append(name)

    return {"processed": processed, "skipped": skipped}


# Tab indices
_TAB_PROJECT    = 0
_TAB_AOI        = 1
_TAB_STREAMFLOW = 2     # merged "FIM" step (streamflow + FIM generation)
_TAB_FIM        = 2     # same tab — kept as an alias

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
        self._aoi_setup_worker: Optional[Worker] = None
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
        self._tabs.currentChanged.connect(self._on_tab_changed)
        outer.addWidget(self._tabs)

        # Step 1 — Project (identical widget to TRITON / LISFLOOD-FP)
        self._proj_step = StepTritonProjectWidget(self._log, model="generic")
        self._proj_step.step_completed.connect(self._on_project_done)

        self._tabs.addTab(self._wrap(self._proj_step),               "1. Project")
        self._tabs.addTab(self._wrap(self._build_aoi_choice_tab()), "2. AOI")
        self._tabs.addTab(self._wrap(self._build_streamflow_tab()), "3. FIM")

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
        self._huc8_detail_gb.setVisible(False)
        self._huc8_detail_lbl.setText("(click any HUC8 ID above to see details here)")
        self._huc8_detail_cache.clear()
        self._huc8_selected_id = None
        self._state["huc8_ids"] = []
        self._state.get("ctx", {}).pop("huc8_ids", None)

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

        # ── Confirm button + status — inside the entry groupbox ──────────────
        confirm_inner_row = QHBoxLayout()
        self._aoi_huc8_status = QLabel("")
        self._aoi_huc8_status.setWordWrap(True)
        self._aoi_huc8_status.setStyleSheet("color:#555;")
        confirm_inner_row.addWidget(self._aoi_huc8_status)
        confirm_inner_row.addStretch()
        confirm_btn = QPushButton("Add to confirmed HUC8")
        confirm_btn.setStyleSheet(
            "font-weight:bold; padding:7px 20px; background:#2b6cb0; "
            "color:white; border-radius:4px;"
        )
        confirm_btn.clicked.connect(self._confirm_aoi_huc8)
        confirm_inner_row.addWidget(confirm_btn)
        gv.addLayout(confirm_inner_row)

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

    def _confirm_aoi_huc8(self):
        ids = self._aoi_huc8_ids
        if not ids:
            QMessageBox.warning(self, "No HUC8 IDs", "Add at least one HUC8 ID first.")
            return
        self._state["huc8_ids"] = list(ids)
        self._rebuild_sf_cards()  # update Streamflow tab immediately

        # Persist to context file so the IDs survive an app restart
        ctx = self._state.get("ctx") or {}
        ctx["huc8_ids"] = list(ids)
        self._state["ctx"] = ctx
        ctx_path = self._state.get("ctx_path")
        if ctx_path:
            try:
                from core.context import save_context
                save_context(ctx_path, ctx)
            except Exception:
                pass

        # No on-screen status report in the AOI HUC8 panel (per request) —
        # keep only a quiet log line.
        self._aoi_huc8_status.setVisible(False)
        self._log(
            f"AOI step (HUC8 mode) — confirmed {len(ids)} HUC8(s): "
            + ", ".join(ids)
        )

        # Create per-HUC8 folders and save boundary polygons in the background
        project_dir = self._state.get("project_dir")
        if project_dir:
            self._aoi_setup_worker = Worker(
                _setup_huc8_folders,
                project_dir=project_dir,
                huc8_ids=list(ids),
            )
            self._aoi_setup_worker.message.connect(self._log)
            self._aoi_setup_worker.finished.connect(self._on_huc8_folders_done)
            self._aoi_setup_worker.error.connect(
                lambda msg: self._log(f"HUC8 folder setup error: {msg}")
            )
            self._aoi_setup_worker.start()
        else:
            self._aoi_huc8_status.setText(
                f"✓  {len(ids)} HUC8 ID(s) confirmed.  "
                "(Complete step 1 Project first to create folders.)"
            )
            self._aoi_huc8_status.setStyleSheet(
                "color:#276749; font-size:12px; font-weight:bold;"
            )

    def _on_huc8_folders_done(self, result: dict):
        created = result.get("created", [])
        skipped = result.get("skipped", [])
        # No on-screen status report (per request) — quiet log line only.
        self._aoi_huc8_status.setVisible(False)
        self._log(
            f"HUC8 folders ready: {len(created)} created"
            + (f", {len(skipped)} skipped" if skipped else "")
        )
        self._rebuild_sf_cards()  # ensure Streamflow cards reflect confirmed HUC8s

    # ── Step 3: FIM (streamflow + FIM generation) ──────────────────────────────

    def _build_streamflow_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(12); v.setContentsMargins(14, 14, 14, 14)

        title = QLabel("Flood Inundation Map (FIM)")
        title.setFont(QFont("Arial", 13, QFont.Weight.Bold))
        title.setStyleSheet("color:#2d3748;")
        v.addWidget(title)

        intro = QLabel("★ Each AOI / HUC8 has its own settings.")
        intro.setWordWrap(True)
        intro.setStyleSheet(_NOTE_STYLE)
        v.addWidget(intro)

        # ── Per-AOI/HUC8 cards: header row with an "apply to all" button ──────
        cards_hdr = QHBoxLayout()
        cards_hdr.addStretch()
        self._sf_apply_all_btn = QPushButton("Apply current card's settings to all")
        self._sf_apply_all_btn.setStyleSheet(
            "QPushButton { background:#2b6cb0; color:white; border-radius:4px; "
            "padding:4px 10px; font-size:11px; } "
            "QPushButton:disabled { background:#cbd5e0; color:#718096; }"
        )
        self._sf_apply_all_btn.setToolTip(
            "Expand (Edit) the card whose Source/dates you want to broadcast, "
            "then click here to copy them to every other card.")
        self._sf_apply_all_btn.clicked.connect(self._apply_sf_to_all)
        self._sf_apply_all_btn.setEnabled(False)
        cards_hdr.addWidget(self._sf_apply_all_btn)
        v.addLayout(cards_hdr)

        cards_scroll = QScrollArea()
        cards_scroll.setWidgetResizable(True)
        cards_scroll.setStyleSheet("QScrollArea { border:none; }")
        cards_scroll.setMinimumHeight(120)

        self._sf_cards_container = QWidget()
        self._sf_cards_layout = QVBoxLayout(self._sf_cards_container)
        self._sf_cards_layout.setSpacing(8)
        self._sf_cards_layout.setContentsMargins(0, 0, 0, 0)

        self._sf_no_items_lbl = QLabel(
            "(Complete step 2 (AOI) first — cards will appear here.)")
        self._sf_no_items_lbl.setStyleSheet("color:#888; font-style:italic; padding:8px;")
        self._sf_cards_layout.addWidget(self._sf_no_items_lbl)
        self._sf_cards_layout.addStretch()

        cards_scroll.setWidget(self._sf_cards_container)
        v.addWidget(cards_scroll, 1)

        # ── Options + Run + progress + status + result previews ───────────────
        self._depth_chk = QCheckBox("Also produce a water-depth map  (optional)")
        self._depth_chk.setChecked(False)
        v.addWidget(self._depth_chk)

        run_row = QHBoxLayout()
        self._sf_btn = QPushButton("Generate FIM for all")
        self._sf_btn.setStyleSheet(_RUN_STYLE)
        self._sf_btn.clicked.connect(self._run_fim_all)
        run_row.addWidget(self._sf_btn)
        run_row.addStretch()
        v.addLayout(run_row)

        # Determinate progress bar — shows "done / total" cards (like the DEM step).
        self._sf_progress = QProgressBar()
        self._sf_progress.setRange(0, 1)
        self._sf_progress.setValue(0)
        self._sf_progress.setFormat("%v / %m AOIs")
        self._sf_progress.setVisible(False)
        v.addWidget(self._sf_progress)

        self._sf_status = QLabel("")
        self._sf_status.setWordWrap(True)
        self._sf_status.setStyleSheet(
            "color:#276749; font-size:12px; font-weight:bold;")
        self._sf_status.setVisible(False)
        v.addWidget(self._sf_status)

        # FIM result previews (extent + optional depth) for the last AOI run.
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

        # Internal card / run state
        self._sf_cards: List[dict] = []
        self._sf_pending: List[dict] = []
        self._sf_current_card: Optional[dict] = None
        self._sf_cards_signature = None
        self._fim_total = 0
        self._fim_done = 0

        return page

    def _sf_signature(self):
        """Identity of the current AOI / HUC8 set — used to avoid rebuilding
        (and wiping) the cards when nothing actually changed."""
        huc8_ids = self._state.get("huc8_ids") or []
        if huc8_ids:
            return ("huc8", tuple(huc8_ids))
        aoi_features = self._state.get("ctx", {}).get("aoi_features") or []
        names = []
        for i, feat in enumerate(aoi_features, 1):
            nm = ((feat.get("name") or feat.get("id") or str(i))
                  if isinstance(feat, dict) else str(i))
            names.append(nm)
        return ("aoi", tuple(names))

    def _rebuild_sf_cards(self):
        """Recreate per-AOI/HUC8 cards in the Streamflow tab.

        Skips the rebuild when the AOI / HUC8 set is unchanged, so a user's
        per-card edits survive navigating away from and back to this tab.
        """
        sig = self._sf_signature()
        if self._sf_cards and sig == self._sf_cards_signature:
            return
        self._sf_cards_signature = sig

        self._sf_cards = []
        # Remove ALL items from layout — takeAt handles both widgets and spacers.
        while self._sf_cards_layout.count() > 0:
            item = self._sf_cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        huc8_ids = self._state.get("huc8_ids") or []
        aoi_features = self._state.get("ctx", {}).get("aoi_features") or []

        if huc8_ids:
            for hid in huc8_ids:
                label = f"HUC8: {hid}"
                card_widget, card_refs = self._build_one_sf_card(
                    label, hid, "huc8", source_obj=hid)
                self._sf_cards_layout.addWidget(card_widget)
                self._sf_cards.append(card_refs)
        elif aoi_features:
            for i, feat in enumerate(aoi_features, 1):
                name = (
                    feat.get("name") or feat.get("id") or str(i)
                    if isinstance(feat, dict) else str(i)
                )
                label = f"AOI {i}: {name}"
                item_id = str(i)
                card_widget, card_refs = self._build_one_sf_card(
                    label, item_id, "aoi", source_obj=feat)
                self._sf_cards_layout.addWidget(card_widget)
                self._sf_cards.append(card_refs)
        else:
            placeholder = QLabel("(Complete step 2 (AOI) first — cards will appear here.)")
            placeholder.setStyleSheet("color:#888; font-style:italic; padding:8px;")
            self._sf_cards_layout.addWidget(placeholder)

        self._sf_cards_layout.addStretch()
        # Force the scroll-area container to recalculate its size hint
        self._sf_cards_container.updateGeometry()
        if hasattr(self, "_sf_apply_all_btn"):
            self._sf_apply_all_btn.setEnabled(
                any(c.get("expanded") for c in self._sf_cards))

    def _build_one_sf_card(self, label: str, item_id: str, mode: str,
                           source_obj=None):
        """Build a single collapsible card for one HUC8/AOI.

        Matches the LISFLOOD accordion: header row with caret + name … status
        + Edit/Done + Remove; the body (Source + dates/forecast) shows only
        when expanded.  Each card carries its OWN Source (Retrospective /
        Forecast) and the matching inputs.

        Returns (QFrame widget, dict of widget refs).
        """
        card = QFrame()
        card.setObjectName("card")
        card.setStyleSheet(_SF_CARD_COLLAPSED)

        outer = QVBoxLayout(card)
        outer.setContentsMargins(2, 2, 2, 2)
        outer.setSpacing(6)

        # ── Header: caret + name … status + Edit + Remove ─────────────────────
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        caret = QLabel("▶"); caret.setFixedWidth(14)
        caret.setStyleSheet("color:#4a5568; font-weight:bold;")
        header.addWidget(caret)
        name_lbl = QLabel(f"<b>{label}</b>")
        name_lbl.setStyleSheet("color:#2d3748;")
        header.addWidget(name_lbl)
        header.addStretch()
        status_lbl = QLabel("")
        status_lbl.setStyleSheet("color:#666; font-size:11px;")
        header.addWidget(status_lbl)
        toggle_btn = QPushButton("Edit"); toggle_btn.setFixedWidth(80)
        header.addWidget(toggle_btn)
        remove_btn = QPushButton("Remove"); remove_btn.setFixedWidth(70)
        remove_btn.setStyleSheet(
            "background:#e53e3e; color:white; border-radius:3px; "
            "font-size:11px; padding:2px 4px;")
        remove_btn.setToolTip(f"Remove {label} from this run")
        header.addWidget(remove_btn)
        outer.addLayout(header)

        # ── Body — shown only when the card is expanded ───────────────────────
        body = QWidget()
        body.setVisible(False)
        cv = QVBoxLayout(body)
        cv.setContentsMargins(2, 2, 2, 2); cv.setSpacing(6)
        outer.addWidget(body)

        # ── Source: Retrospective / Forecast (per card) ───────────────────────
        src_row = QHBoxLayout()
        src_row.addWidget(QLabel("Source:"))
        src_grp = QButtonGroup(card)
        rb_src_retro = QRadioButton("Retrospective  (before 2023)")
        rb_src_fore  = QRadioButton("Forecast  (2023 onward)")
        rb_src_retro.setChecked(True)
        src_grp.addButton(rb_src_retro)
        src_grp.addButton(rb_src_fore)
        src_row.addWidget(rb_src_retro)
        src_row.addWidget(rb_src_fore)
        src_row.addStretch()
        cv.addLayout(src_row)

        # ── Retrospective box: date range / specific date(s) ──────────────────
        retro_box = QWidget()
        rbv = QVBoxLayout(retro_box)
        rbv.setContentsMargins(0, 0, 0, 0); rbv.setSpacing(6)

        date_grp = QButtonGroup(card)
        rb_range    = QRadioButton("Date range")
        rb_specific = QRadioButton("Specific date(s)")
        rb_range.setChecked(True)
        date_grp.addButton(rb_range,    0)
        date_grp.addButton(rb_specific, 1)
        mode_row = QHBoxLayout()
        mode_row.addWidget(rb_range)
        mode_row.addWidget(rb_specific)
        mode_row.addStretch()
        rbv.addLayout(mode_row)

        range_box = QWidget()
        rr = QHBoxLayout(range_box)
        rr.setContentsMargins(0, 0, 0, 0); rr.setSpacing(10)
        rr.addWidget(QLabel("Start date:"))
        start_dt = QDateTimeEdit()
        start_dt.setDisplayFormat("yyyy-MM-dd HH:mm")
        start_dt.setCalendarPopup(True)
        start_dt.setDateTime(QDateTime.fromString("2020-05-20 00:00", "yyyy-MM-dd HH:mm"))
        rr.addWidget(start_dt)
        rr.addSpacing(12)
        rr.addWidget(QLabel("End date:"))
        end_dt = QDateTimeEdit()
        end_dt.setDisplayFormat("yyyy-MM-dd HH:mm")
        end_dt.setCalendarPopup(True)
        end_dt.setDateTime(QDateTime.fromString("2020-05-22 00:00", "yyyy-MM-dd HH:mm"))
        rr.addWidget(end_dt)
        rr.addStretch()
        rbv.addWidget(range_box)

        specific_box = QWidget()
        sv = QVBoxLayout(specific_box)
        sv.setContentsMargins(0, 0, 0, 0); sv.setSpacing(4)
        sp_row = QHBoxLayout()
        sp_row.addWidget(QLabel("Date / time:"))
        specific_dt = QDateTimeEdit()
        specific_dt.setDisplayFormat("yyyy-MM-dd HH:mm")
        specific_dt.setCalendarPopup(True)
        specific_dt.setDateTime(QDateTime.fromString("2020-05-21 00:00", "yyyy-MM-dd HH:mm"))
        sp_row.addWidget(specific_dt)
        sp_add = QPushButton("Add"); sp_add.setFixedWidth(60)
        sp_row.addWidget(sp_add)
        sp_rem = QPushButton("Remove"); sp_rem.setFixedWidth(70)
        sp_row.addWidget(sp_rem)
        sp_row.addStretch()
        sv.addLayout(sp_row)
        specific_list = QListWidget()
        specific_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        specific_list.setMaximumHeight(80)
        sv.addWidget(specific_list)
        specific_box.setVisible(False)
        rbv.addWidget(specific_box)
        cv.addWidget(retro_box)

        # ── Forecast box (per card): range, latest run, date+hour, aggregation ─
        forecast_box = QWidget()
        fbv = QVBoxLayout(forecast_box)
        fbv.setContentsMargins(0, 0, 0, 0); fbv.setSpacing(6)

        f1 = QHBoxLayout()
        f1.addWidget(QLabel("Forecast range:"))
        fc_range = QComboBox()
        fc_range.addItems(["shortrange", "mediumrange", "longrange"])
        fc_range.setCurrentText("mediumrange")
        f1.addWidget(fc_range)
        f1.addStretch()
        fbv.addLayout(f1)

        fc_latest_chk = QCheckBox("Use latest available run")
        fc_latest_chk.setChecked(True)
        fbv.addWidget(fc_latest_chk)

        f2 = QHBoxLayout()
        f2.addSpacing(20)
        fc_date_lbl = QLabel("Forecast date:")
        f2.addWidget(fc_date_lbl)
        fc_date = QDateTimeEdit()
        fc_date.setDisplayFormat("yyyy-MM-dd")
        fc_date.setCalendarPopup(True)
        fc_date.setDateTime(QDateTime.fromString("2024-06-01", "yyyy-MM-dd"))
        f2.addWidget(fc_date)
        f2.addSpacing(12)
        fc_hour_lbl = QLabel("Hour (UTC):")
        f2.addWidget(fc_hour_lbl)
        fc_hour = QComboBox()
        fc_hour.addItems([f"{h:02d}" for h in range(0, 24)])
        f2.addWidget(fc_hour)
        f2.addStretch()
        fbv.addLayout(f2)

        f3 = QHBoxLayout()
        fc_agg_lbl = QLabel("Aggregation (medium / long range only):")
        f3.addWidget(fc_agg_lbl)
        fc_sort_by = QComboBox()
        fc_sort_by.addItems(["maximum", "median", "minimum"])
        f3.addWidget(fc_sort_by)
        f3.addStretch()
        fbv.addLayout(f3)

        forecast_box.setVisible(False)
        cv.addWidget(forecast_box)

        # ── Wiring ────────────────────────────────────────────────────────────
        # Source toggle: Retrospective shows retro_box, Forecast shows forecast_box.
        rb_src_retro.toggled.connect(
            lambda checked, rbx=retro_box, fbx=forecast_box:
                (rbx.setVisible(checked), fbx.setVisible(not checked))
        )
        # Date-mode toggle within Retrospective.
        rb_range.toggled.connect(
            lambda checked, rb=range_box, sb=specific_box:
                (rb.setVisible(checked), sb.setVisible(not checked))
        )
        # Forecast: "latest run" disables the manual date/hour pickers.
        def _sync_latest(checked, lbl=fc_date_lbl, dt=fc_date,
                         hl=fc_hour_lbl, hr=fc_hour):
            manual = not checked
            for w in (lbl, dt, hl, hr):
                w.setEnabled(manual)
        fc_latest_chk.toggled.connect(_sync_latest)
        _sync_latest(fc_latest_chk.isChecked())
        # Forecast: aggregation only applies to medium / long range.
        def _sync_agg(text, agg=fc_sort_by, aggl=fc_agg_lbl):
            ok = text in ("mediumrange", "longrange")
            agg.setEnabled(ok); aggl.setEnabled(ok)
        fc_range.currentTextChanged.connect(_sync_agg)
        _sync_agg(fc_range.currentText())

        # Add/Remove specific dates.
        sp_add.clicked.connect(
            lambda _checked, sdt=specific_dt, sl=specific_list: (
                sl.addItem(sdt.dateTime().toString("yyyy-MM-dd HH:mm"))
                if sdt.dateTime().toString("yyyy-MM-dd HH:mm")
                not in [sl.item(i).text() for i in range(sl.count())]
                else None
            )
        )
        sp_rem.clicked.connect(
            lambda _checked, sl=specific_list: [
                sl.takeItem(sl.row(it)) for it in sl.selectedItems()
            ]
        )

        refs = {
            "label":         label,
            "item_id":       item_id,
            "mode":          mode,
            "source_obj":    source_obj,
            # card chrome
            "card":          card,
            "caret":         caret,
            "name_lbl":      name_lbl,
            "toggle_btn":    toggle_btn,
            "remove_btn":    remove_btn,
            "body":          body,
            "expanded":      False,
            # source
            "rb_src_retro":  rb_src_retro,
            "rb_src_fore":   rb_src_fore,
            # retrospective dates
            "rb_range":      rb_range,
            "rb_specific":   rb_specific,
            "date_grp":      date_grp,
            "start_dt":      start_dt,
            "end_dt":        end_dt,
            "specific_dt":   specific_dt,
            "specific_list": specific_list,
            "range_box":     range_box,
            "specific_box":  specific_box,
            "retro_box":     retro_box,
            # forecast
            "fc_range":      fc_range,
            "fc_latest_chk": fc_latest_chk,
            "fc_date":       fc_date,
            "fc_hour":       fc_hour,
            "fc_sort_by":    fc_sort_by,
            "forecast_box":  forecast_box,
            # status (header)
            "status_lbl":    status_lbl,
        }
        # Edit/Done toggle (single-card accordion) and Remove.
        toggle_btn.clicked.connect(lambda _c=False, r=refs: self._on_sf_toggle(r))
        remove_btn.clicked.connect(lambda _c=False, r=refs: self._on_sf_remove(r))
        return card, refs

    # ── Streamflow card accordion: expand / remove / apply-to-all ─────────────

    def _sf_set_expanded(self, refs: dict, expanded: bool):
        refs["expanded"] = expanded
        refs["body"].setVisible(expanded)
        refs["caret"].setText("▼" if expanded else "▶")
        refs["toggle_btn"].setText("Done" if expanded else "Edit")
        refs["card"].setStyleSheet(
            _SF_CARD_EXPANDED if expanded else _SF_CARD_COLLAPSED)
        # When collapsing, show a one-line summary in the header status.
        if not expanded:
            refs["status_lbl"].setText(self._sf_status_summary(refs))
        else:
            refs["status_lbl"].setText("")

    def _on_sf_toggle(self, refs: dict):
        if refs.get("expanded"):
            self._sf_set_expanded(refs, False)
        else:
            for c in self._sf_cards:           # single-card accordion
                self._sf_set_expanded(c, c is refs)
        self._sf_apply_all_btn.setEnabled(
            any(c.get("expanded") for c in self._sf_cards))

    def _on_sf_remove(self, refs: dict):
        reply = QMessageBox.question(
            self, "Remove",
            f"Remove <b>{refs['label']}</b> from this run?\n\n"
            "The AOI / HUC8 data folder is NOT deleted — only removed from "
            "the current run.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        # Drop from the underlying source list so later steps agree.
        if refs["mode"] == "huc8":
            ids = self._state.get("huc8_ids") or []
            if refs["item_id"] in ids:
                ids.remove(refs["item_id"])
        else:
            feats = self._state.get("ctx", {}).get("aoi_features") or []
            so = refs.get("source_obj")
            if so is not None and so in feats:
                feats.remove(so)
        # Remove the card widget + ref.
        refs["card"].setParent(None)
        refs["card"].deleteLater()
        if refs in self._sf_cards:
            self._sf_cards.remove(refs)
        # Keep the signature in sync so re-entering the tab doesn't rebuild
        # (which would wipe the remaining cards' edits).
        self._sf_cards_signature = self._sf_signature()
        self._sf_apply_all_btn.setEnabled(
            any(c.get("expanded") for c in self._sf_cards))

    def _sf_status_summary(self, refs: dict) -> str:
        if refs["rb_src_fore"].isChecked():
            rng = refs["fc_range"].currentText()
            when = ("latest run" if refs["fc_latest_chk"].isChecked()
                    else refs["fc_date"].dateTime().toString("yyyy-MM-dd"))
            return f"Forecast · {rng} · {when}"
        if refs["rb_range"].isChecked():
            return (f"Retrospective · "
                    f"{refs['start_dt'].dateTime().toString('yyyy-MM-dd')} → "
                    f"{refs['end_dt'].dateTime().toString('yyyy-MM-dd')}")
        n = refs["specific_list"].count()
        return f"Retrospective · {n} specific date(s)"

    def _sf_get_cfg(self, refs: dict) -> dict:
        return {
            "source":  "forecast" if refs["rb_src_fore"].isChecked() else "retro",
            "date_mode": "range" if refs["rb_range"].isChecked() else "specific",
            "start":   refs["start_dt"].dateTime(),
            "end":     refs["end_dt"].dateTime(),
            "specific_times": [refs["specific_list"].item(i).text()
                               for i in range(refs["specific_list"].count())],
            "fc_range":  refs["fc_range"].currentText(),
            "fc_latest": refs["fc_latest_chk"].isChecked(),
            "fc_date":   refs["fc_date"].dateTime(),
            "fc_hour":   refs["fc_hour"].currentText(),
            "fc_sort":   refs["fc_sort_by"].currentText(),
        }

    def _sf_set_cfg(self, refs: dict, cfg: dict):
        (refs["rb_src_fore"] if cfg["source"] == "forecast"
         else refs["rb_src_retro"]).setChecked(True)
        (refs["rb_range"] if cfg["date_mode"] == "range"
         else refs["rb_specific"]).setChecked(True)
        refs["start_dt"].setDateTime(cfg["start"])
        refs["end_dt"].setDateTime(cfg["end"])
        refs["specific_list"].clear()
        for t in cfg["specific_times"]:
            refs["specific_list"].addItem(t)
        refs["fc_range"].setCurrentText(cfg["fc_range"])
        refs["fc_latest_chk"].setChecked(cfg["fc_latest"])
        refs["fc_date"].setDateTime(cfg["fc_date"])
        refs["fc_hour"].setCurrentText(cfg["fc_hour"])
        refs["fc_sort_by"].setCurrentText(cfg["fc_sort"])

    def _apply_sf_to_all(self):
        src = next((c for c in self._sf_cards if c.get("expanded")), None)
        if src is None:
            QMessageBox.information(
                self, "Pick a card to copy from",
                "Click Edit on the card whose Source / dates you want to copy, "
                "then click 'Apply current card's settings to all'.",
            )
            return
        cfg = self._sf_get_cfg(src)
        for c in self._sf_cards:
            if c is src:
                continue
            self._sf_set_cfg(c, cfg)
        self._log(f"Applied {src['label']} settings to all "
                  f"{len(self._sf_cards)} card(s).")

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

        self._rebuild_sf_cards()  # populate Streamflow cards as soon as AOI is confirmed
        self._ensure_aoi_huc8_folders()

    def _ensure_aoi_huc8_folders(self):
        """Create per-AOI sub-folders and download + save each AOI's HUC8
        boundary polygon(s).

        Called from BOTH the AOI step's confirm (_on_aoi_done) and from tab
        navigation (_on_tab_changed) so the boundaries get saved no matter how
        the user leaves the AOI tab.  A signature guard prevents re-running for
        the same confirmed AOI set.
        """
        project_dir  = self._state.get("project_dir")
        aoi_features = self._state.get("ctx", {}).get("aoi_features") or []
        if not (project_dir and aoi_features):
            if aoi_features and not project_dir:
                self._log("AOI confirmed, but complete step 1 (Project) first "
                          "so HUC8 boundaries can be saved into folders.")
            return

        sig = tuple(
            (f.get("name"), f.get("folder_path"))
            for f in aoi_features if isinstance(f, dict)
        )
        if sig == getattr(self, "_aoi_folders_signature", None):
            return  # already set up for this exact AOI set
        self._aoi_folders_signature = sig

        self._log(
            f"AOI confirmed — setting up {len(aoi_features)} project folder(s) "
            "and downloading + saving each AOI's HUC8 boundary…"
        )
        self._aoi_setup_worker = Worker(
            _setup_aoi_huc8_folders,
            project_dir=project_dir,
            aoi_features=aoi_features,
        )
        self._aoi_setup_worker.message.connect(self._log)
        self._aoi_setup_worker.finished.connect(self._on_aoi_folders_done)
        self._aoi_setup_worker.error.connect(
            lambda msg: self._log(f"AOI folder setup error: {msg}")
        )
        self._aoi_setup_worker.start()

    def _on_aoi_folders_done(self, result: dict):
        processed = result.get("processed", [])
        skipped   = result.get("skipped", [])
        msg = (
            f"✓  {len(processed)} AOI folder(s) ready with HUC8 boundary data."
        )
        if skipped:
            msg += f"  ({len(skipped)} skipped — see log.)"
        self._log(msg + "  Proceed to step 3 (FIM).")
        self._rebuild_sf_cards()  # refresh cards now that folder setup is complete

    # ── Streamflow enable / disable logic ─────────────────────────────────────


    def _run_fim_all(self):
        """Run the full per-AOI/HUC8 pipeline: for each card, fetch NWM
        discharge → download OWP HAND rasters → generate the FIM.  The progress
        bar shows how many of the cards are done (X / Y)."""
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
                                "Complete project setup in step 1 first.")
            return
        if not self._sf_cards:
            QMessageBox.warning(self, "No cards",
                "Complete step 2 (AOI) first — no AOI / HUC8 cards are configured.")
            return

        for card in self._sf_cards:
            card["status_lbl"].setText("")
            card["status_lbl"].setVisible(False)

        self._sf_pending = list(self._sf_cards)
        self._fim_total  = len(self._sf_pending)
        self._fim_done   = 0
        self._sf_progress.setRange(0, self._fim_total)
        self._sf_progress.setValue(0)
        self._sf_progress.setVisible(True)
        self._extent_canvas.setVisible(False)
        self._depth_canvas.setVisible(False)
        self._fim_files.setVisible(False)
        set_running(self._sf_btn)
        self._fim_start_card()

    def _fim_advance(self):
        self._fim_done += 1
        self._sf_progress.setValue(self._fim_done)
        self._fim_start_card()

    def _fim_skip_card(self, card, msg):
        card["status_lbl"].setText(msg)
        card["status_lbl"].setVisible(True)
        self._fim_advance()

    def _fim_start_card(self):
        if not self._sf_pending:
            set_ready(self._sf_btn)
            self._sf_progress.setVisible(False)
            self._sf_status.setText(
                f"FIM complete for {self._fim_done} / {self._fim_total} AOI(s).")
            self._sf_status.setStyleSheet(
                "color:#276749; font-size:12px; font-weight:bold;")
            self._sf_status.setVisible(True)
            return

        card = self._sf_pending.pop(0)
        self._sf_current_card = card
        idx = self._fim_done + 1

        # Discharge kwargs from this card.
        if card["rb_src_fore"].isChecked():
            kwargs = dict(source="forecast",
                          forecast_range=card["fc_range"].currentText(),
                          sort_by=card["fc_sort_by"].currentText())
            if not card["fc_latest_chk"].isChecked():
                kwargs["forecast_date"] = card["fc_date"].dateTime().toString("yyyy-MM-dd")
                kwargs["forecast_hour"] = int(card["fc_hour"].currentText())
        elif card["rb_range"].isChecked():
            start = card["start_dt"].dateTime().toPyDateTime()
            end   = card["end_dt"].dateTime().toPyDateTime()
            if end <= start:
                self._fim_skip_card(card, "⚠ End date must be after start — skipped.")
                return
            kwargs = dict(source="retrospective",
                          start_date=start.strftime("%Y-%m-%d"),
                          end_date=end.strftime("%Y-%m-%d"))
        else:
            times = [card["specific_list"].item(i).text()
                     for i in range(card["specific_list"].count())]
            if not times:
                self._fim_skip_card(card, "⚠ No dates added — skipped.")
                return
            kwargs = dict(source="retrospective", value_times=times)

        # Resolve HUC8(s) + output folder for this card.
        #   HUC8 mode → that HUC8, under the main project folder.
        #   AOI mode  → the HUC8(s) the AOI covers, INSIDE the AOI's own folder.
        if card["mode"] == "huc8":
            huc8_ids = [card["item_id"]]
            run_project_dir = self._state["project_dir"]
            aoi_path = None
        else:
            feat = card.get("source_obj") or {}
            huc8_ids = feat.get("huc8_codes") or []
            if not huc8_ids and feat.get("source_file"):
                from core.aoi_info import lookup_huc8
                huc8_ids = lookup_huc8(feat["source_file"],
                                       feat.get("feature_index", 0), log_fn=self._log)
            run_project_dir = feat.get("folder_path") or self._state["project_dir"]
            aoi_path = feat.get("source_file")
            if not huc8_ids:
                self._fim_skip_card(card, "⚠ No HUC8 found for this AOI — skipped.")
                return

        self._fim_cur_huc8s   = list(huc8_ids)
        self._fim_cur_projdir = run_project_dir
        self._fim_cur_aoipath = aoi_path

        self._set_busy(self._sf_status,
                       f"AOI {idx}/{self._fim_total} ({card['label']}) — "
                       "fetching NWM discharge …")
        card["status_lbl"].setText("⏳ discharge …")
        card["status_lbl"].setVisible(True)
        self._start_worker(
            streamflow_mode, done=self._fim_after_discharge,
            on_error=self._fim_card_failed,
            project_dir=run_project_dir, huc8_ids=huc8_ids, **kwargs,
        )

    def _fim_after_discharge(self, result: dict):
        card = self._sf_current_card
        idx = self._fim_done + 1
        if card is not None:
            card["status_lbl"].setText("⏳ HAND rasters …")
        self._set_busy(self._sf_status,
                       f"AOI {idx}/{self._fim_total} — downloading OWP HAND rasters …")
        self._start_worker(
            download_huc8_mode, done=self._fim_after_download,
            on_error=self._fim_card_failed,
            project_dir=self._fim_cur_projdir, huc8_ids=self._fim_cur_huc8s,
        )

    def _fim_after_download(self, result: dict):
        card = self._sf_current_card
        idx = self._fim_done + 1
        ok = (result.get("downloaded") if isinstance(result, dict) else None) \
            or self._fim_cur_huc8s
        if card is not None:
            card["status_lbl"].setText("⏳ generating FIM …")
        self._set_busy(self._sf_status,
                       f"AOI {idx}/{self._fim_total} — generating flood inundation map …")
        self._start_worker(
            generate_fim_mode, done=self._fim_after_generate,
            on_error=self._fim_card_failed,
            project_dir=self._fim_cur_projdir, huc8_ids=ok,
            aoi_path=self._fim_cur_aoipath,
            depth=self._depth_chk.isChecked(), binary=True, clip=True,
        )

    def _fim_after_generate(self, result: dict):
        card = self._sf_current_card
        outputs = result.get("outputs", {}) if isinstance(result, dict) else {}
        if card is not None:
            card["status_lbl"].setText("✓ FIM ready" if outputs
                                       else "⚠ no FIM produced — see log")
            card["status_lbl"].setVisible(True)
        self._show_fim_outputs(outputs)
        self._fim_advance()

    def _fim_card_failed(self, msg: str):
        card = self._sf_current_card
        self._log(f"FIM error: {msg}")
        if card is not None:
            card["status_lbl"].setText("⚠ failed — see log")
            card["status_lbl"].setVisible(True)
        self._fim_advance()

    def _show_fim_outputs(self, outputs: dict):
        if not outputs:
            return
        aoi_gdf = None
        if self._fim_cur_aoipath:
            try:
                import geopandas as gpd
                aoi_gdf = gpd.read_file(self._fim_cur_aoipath)
            except Exception:
                aoi_gdf = None
        extent_path = outputs.get("extent_clipped") or outputs.get("extent_mosaic")
        if extent_path and Path(extent_path).exists():
            self._extent_canvas.show_raster(
                extent_path, title="Flood extent (wet = 1 / dry = 0)",
                cmap="Blues", colorbar_label="Inundation", overlay_gdf=aoi_gdf)
            self._extent_canvas.setVisible(True)
        depth_path = outputs.get("depth_clipped") or outputs.get("depth_mosaic")
        if depth_path and Path(depth_path).exists():
            self._depth_canvas.show_raster(
                depth_path, title="Water depth", cmap="viridis",
                colorbar_label="Depth (m)", overlay_gdf=aoi_gdf)
            self._depth_canvas.setVisible(True)

    # ── Worker plumbing ───────────────────────────────────────────────────────

    def _set_busy(self, label: QLabel, text: str):
        label.setText(text)
        label.setStyleSheet("color:#744210; font-size:12px; font-weight:bold;")
        label.setVisible(True)

    def _start_worker(self, fn, done, on_error=None, **kwargs):
        if self._worker is not None:
            try:
                self._worker.message.disconnect(self._log)
            except Exception:
                pass
            self._worker = None
        self._worker = Worker(fn, **kwargs)
        self._worker.message.connect(self._log)
        self._worker.finished.connect(done)
        self._worker.error.connect(on_error or self._on_error)
        self._worker.start()

    def _on_error(self, msg: str):
        try:
            set_ready(self._sf_btn)
        except Exception:
            pass
        self._sf_progress.setVisible(False)
        self._log(f"ERROR: {msg}")
        QMessageBox.critical(self, "FIMserv error", msg.splitlines()[0])

    # ── Navigation ────────────────────────────────────────────────────────────

    def _on_tab_changed(self, idx: int):
        self._update_nav(idx)
        # When navigating past the AOI tab via tab-click (not "Next step ▶"),
        # commit any confirmed shapefile AOIs to ctx so aoi_features is populated.
        if idx > _TAB_AOI and self._aoi_type_combo.currentIndex() == 1:
            try:
                data = self._aoi_step.commit_confirmed_to_ctx()
                if data:
                    self._state["ctx"]      = data.get("ctx",      self._state.get("ctx", {}))
                    self._state["ctx_path"] = data.get("ctx_path", self._state.get("ctx_path"))
                # Download + save each AOI's HUC8 boundary if it hasn't been
                # done yet — covers the case where the user leaves the AOI tab
                # by clicking another tab instead of "Next step".
                self._ensure_aoi_huc8_folders()
            except Exception:
                pass
        if idx == _TAB_STREAMFLOW:
            self._rebuild_sf_cards()

    def _update_nav(self, idx: int):
        self.nav_changed.emit(idx, self._tabs.count())

    def go_prev(self):
        i = self._tabs.currentIndex()
        if i > 0:
            self._tabs.setCurrentIndex(i - 1)

    def go_next(self):
        i = self._tabs.currentIndex()
        # Commit shapefile AOIs when advancing past the AOI tab via the nav button.
        if i == _TAB_AOI and self._aoi_type_combo.currentIndex() == 1:
            try:
                data = self._aoi_step.commit_confirmed_to_ctx()
                if data:
                    self._state["ctx"]      = data.get("ctx",      self._state.get("ctx", {}))
                    self._state["ctx_path"] = data.get("ctx_path", self._state.get("ctx_path"))
            except Exception:
                pass
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
        # Reset streamflow tab — cards rebuild fresh (each defaults to
        # Retrospective + latest-run), so there are no global source widgets
        # to reset here anymore.
        self._sf_cards.clear()
        self._rebuild_sf_cards()
        self._extent_canvas.setVisible(False)
        self._depth_canvas.setVisible(False)
        self._fim_files.setVisible(False)
        self._sf_status.setVisible(False)
        self._sf_progress.setVisible(False)
        try:
            set_ready(self._sf_btn)
        except Exception:
            pass
        self._tabs.setCurrentIndex(0)
