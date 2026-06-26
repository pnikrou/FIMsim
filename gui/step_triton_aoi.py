"""Adapter that wraps MultiAOIWidget for the LISFLOOD-FP / TRITON tab interface.

The tab-based workflows expect each step to expose:
  - `set_context(ctx_path, ctx)` — called by the parent app when the
    previous step completes.
  - `step_completed = pyqtSignal(dict)` — emitted with `{"ctx_path", "ctx"}`
    so the next tab can be enabled and updated.

This widget:
  1. Uses MultiAOIWidget for the heavy lifting (multi-file upload, feature
     selection, map preview, USGS / HUC / river lookups, per-AOI subfolders).
  2. Persists the confirmed feature list as ``ctx['aoi_features']`` so the
     downstream steps can iterate (or, in the bridge phase, just pick the
     first one).
  3. Sets legacy keys (``aoi_path``, ``aoi_name``, ``aoi_feature_index``,
     ``lisflood_dir`` / ``triton_dir``) to the FIRST confirmed feature so
     existing single-AOI steps keep working until they're refactored to
     iterate over the full list.
"""
from pathlib import Path

from PyQt6.QtWidgets import QWidget, QVBoxLayout
from PyQt6.QtCore import pyqtSignal

from gui.triton_multi_aoi_widget import MultiAOIWidget
from core.context import save_context


class StepTritonAOIWidget(QWidget):
    """Tab-mode adapter around the multi-AOI widget."""

    step_completed = pyqtSignal(dict)

    def __init__(self, log_fn, model: str = "lisflood", parent=None):
        super().__init__(parent)
        self._log = log_fn
        self._model = model.lower()
        self._ctx_path = None
        self._ctx = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._inner = MultiAOIWidget(log_fn)
        self._inner.aoi_ready.connect(self._on_aoi_ready)
        # The internal "Back to project step" button is already hidden;
        # tab navigation handles it.
        layout.addWidget(self._inner)

    # ── public API expected by the tab workflow ──────────────────────────────

    def set_context(self, ctx_path, ctx):
        self._ctx_path = ctx_path
        self._ctx = ctx
        if ctx and ctx.get("project_dir"):
            self._inner.set_project_dir(ctx["project_dir"])

    def reset(self):
        self._inner.reset()

    def proceed_to_next(self) -> bool:
        """Forward to the inner widget so the bottom-bar 'Next step ▶' can
        commit confirmed AOIs and advance the tab."""
        return self._inner.proceed_to_next()

    def has_confirmed_aois(self) -> bool:
        return self._inner.has_confirmed_aois()

    # ── slot ────────────────────────────────────────────────────────────────

    def _on_aoi_ready(self, features):
        """Persist confirmed AOIs into context and emit step_completed
        (fired when the user clicks the bottom 'Next step ▶' button)."""
        self._persist_features(features)
        self.step_completed.emit({"ctx_path": self._ctx_path, "ctx": self._ctx})

    def commit_confirmed_to_ctx(self):
        """Write the currently-confirmed AOIs into ctx WITHOUT advancing the
        tab or emitting step_completed.  Lets the host (MainWindow) push the
        AOI list to a downstream step the instant the user navigates to it by
        clicking its tab — so tab navigation works the same as 'Next step ▶'.

        Returns ``{"ctx_path", "ctx"}`` when there is at least one confirmed
        AOI, else ``None``."""
        features = self._inner.confirmed_features()
        if not features:
            return None
        self._persist_features(features)
        return {"ctx_path": self._ctx_path, "ctx": self._ctx}

    def _persist_features(self, features):
        """Serialise the confirmed AOIs into ctx (aoi_features + first-AOI
        bridge keys) and save to disk.  Shared by _on_aoi_ready and
        commit_confirmed_to_ctx."""
        if self._ctx is None:
            self._ctx = {}

        # Serialise the AOIFeatureInfo objects (dataclass → dict)
        aoi_list = []
        for f in features:
            aoi_list.append({
                "source_file":      f.source_file,
                "feature_index":    f.feature_index,
                "name":             f.name,
                "folder_name":      f.folder_name,
                "folder_path":      f.folder_path,
                "area_km2":         f.area_km2,
                "centroid_lon":     f.centroid_lon,
                "centroid_lat":     f.centroid_lat,
                "state_name":       f.state_name,
                "state_abbr":       f.state_abbr,
                "huc6_codes":       list(f.huc6_codes) if f.huc6_codes else None,
                "huc8_codes":       list(f.huc8_codes) if f.huc8_codes else None,
                "river_name":       f.river_name,
                "usgs_gages":       list(f.usgs_gages) if f.usgs_gages else None,
                # Auto-picked metric CRS for this AOI — every DEM / LULC /
                # Manning raster the downstream steps write lands here.
                "working_crs_epsg":  f.working_crs_epsg,
                "working_crs_label": f.working_crs_label,
            })
        self._ctx["aoi_features"] = aoi_list

        # Bridge: common AOI keys always set regardless of model.
        # Model-specific subdir (lisflood-files / triton-files) and the
        # file-path defaults (.bci, .bdy, .par, …) are only created for
        # LISFLOOD-FP and TRITON — NOT for "generic" (HAND-FIM), which has
        # its own folder structure and must not get a stray lisflood-files/.
        if features:
            f0 = features[0]
            is_triton = (self._model == "triton")

            self._ctx["aoi_path"]            = f0.source_file
            self._ctx["aoi_name"]            = f0.name
            self._ctx["aoi_feature_index"]   = f0.feature_index
            if f0.working_crs_epsg is not None:
                self._ctx["working_crs_epsg"]  = f0.working_crs_epsg
            if f0.working_crs_label:
                self._ctx["working_crs_label"] = f0.working_crs_label
            if len(features) == 1:
                self._ctx["project_dir"] = f0.folder_path

            # Model-specific subdir + file-path defaults (LISFLOOD-FP / TRITON only)
            if self._model != "generic":
                from core.multi_aoi import model_files_subdir
                mf_dir = model_files_subdir(f0.folder_path, is_triton=is_triton)
                if is_triton:
                    self._ctx["triton_dir"]   = mf_dir
                    self._ctx.pop("lisflood_dir", None)
                else:
                    self._ctx["lisflood_dir"] = mf_dir
                    self._ctx.pop("triton_dir", None)
                self._ctx["model_dir"] = mf_dir
                mf = Path(mf_dir)
                self._ctx.setdefault("dem_ascii_path",
                                     str(mf / ("dem.asc" if is_triton else "dem.ascii")))
                self._ctx.setdefault("manning_ascii_path",
                                     str(mf / "lulc.ascii"))
                self._ctx.setdefault("bci_path",
                                     str(mf / f"{f0.name}.bci"))
                self._ctx.setdefault("bdy_path",
                                     str(mf / f"{f0.name}.bdy"))
                self._ctx.setdefault(
                    "par_path",
                    str(mf / f"{self._ctx.get('project_name', 'model')}.par"),
                )

        # Save to disk
        if self._ctx_path:
            save_context(self._ctx_path, self._ctx)

        self._log(
            f"AOI step complete — {len(features)} AOI(s) confirmed. "
            f"Every following step (DEM → PAR) will list all {len(features)} "
            f"AOI(s) with their own options."
        )
