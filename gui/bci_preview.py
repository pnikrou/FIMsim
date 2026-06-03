"""Lightweight matplotlib canvas that visualises one AOI's BCI result.

Renders the AOI polygon, the main-river flowline (when available), and
two stars marking the upstream + downstream boundary points.  Used by
step_bci to give the user a visual confirmation of what was written
into ``BC.bci`` for each AOI.
"""
from typing import Optional, Tuple

from PyQt6.QtWidgets import QSizePolicy
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure


class BCIPreviewCanvas(FigureCanvas):
    """Show one AOI polygon + main-river flowline + upstream/downstream stars."""

    def __init__(self, parent=None, width: float = 9.0, height: float = 4.0):
        self._fig = Figure(figsize=(width, height), constrained_layout=True)
        super().__init__(self._fig)
        self.setParent(parent)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.clear()

    def clear(self):
        self._fig.clear()
        ax = self._fig.add_subplot(1, 1, 1)
        ax.set_xticks([]); ax.set_yticks([])
        try:
            self.draw_idle()
        except Exception:
            pass

    def show_bci(
        self,
        aoi_path: str,
        feature_index: int,
        main_river_path: Optional[str],
        upstream_xy: Optional[Tuple[float, float]],
        downstream_xy: Optional[Tuple[float, float]],
        title: Optional[str] = None,
        points_crs=None,
    ):
        """Render the BCI preview for one AOI.

        Parameters
        ----------
        aoi_path
            Path to the AOI shapefile (a single feature is selected).
        feature_index
            Which feature to draw if the shapefile has multiple.
        main_river_path
            Path to ``main_river_line.gpkg`` (NHD), or None for manual mode.
        upstream_xy / downstream_xy
            Tuples in the AOI CRS, or None.
        title
            Optional axes title.
        """
        try:
            import geopandas as gpd
        except Exception as ex:
            self._render_error(f"Cannot render BCI preview:\n{ex}")
            return

        try:
            aoi = gpd.read_file(aoi_path)
            if feature_index is not None and len(aoi) > 1:
                aoi = aoi.iloc[[feature_index]].reset_index(drop=True)
        except Exception as ex:
            self._render_error(f"Cannot open AOI shapefile:\n{ex}")
            return

        # 1×3 GridSpec keeps the axes centered horizontally.
        self._fig.clear()
        gs = self._fig.add_gridspec(
            1, 3, width_ratios=[1, 6, 1], wspace=0.0,
        )
        for col in (0, 2):
            sp = self._fig.add_subplot(gs[0, col])
            sp.set_xticks([]); sp.set_yticks([])
            sp.set_frame_on(False)
        ax = self._fig.add_subplot(gs[0, 1])
        ax.set_aspect("equal", adjustable="datalim")
        ax.set_xticks([]); ax.set_yticks([])
        if title:
            ax.set_title(title, fontsize=10)

        # AOI polygon — soft fill + dark edge
        try:
            aoi.plot(
                ax=ax, facecolor="#bee3f8", edgecolor="#2c5282",
                linewidth=1.0, alpha=0.5,
            )
        except Exception:
            pass

        # Flowline (if NHD auto-detect found one) — reproject to AOI CRS
        river_plotted = None
        if main_river_path:
            try:
                river = gpd.read_file(main_river_path)
                if aoi.crs is not None and river.crs is not None and \
                        river.crs != aoi.crs:
                    river = river.to_crs(aoi.crs)
                river.plot(
                    ax=ax, color="#2b6cb0", linewidth=2.0,
                    label="Main river",
                )
                river_plotted = river
            except Exception:
                pass

        # Reproject upstream/downstream points to AOI CRS when a source
        # CRS is known (points come from the DEM's projected CRS which
        # differs from the AOI shapefile CRS in most workflows).
        if points_crs is not None and aoi.crs is not None:
            try:
                from pyproj import Transformer
                tr = Transformer.from_crs(
                    points_crs, aoi.crs, always_xy=True
                )
                if upstream_xy is not None:
                    x, y = tr.transform(
                        float(upstream_xy[0]), float(upstream_xy[1])
                    )
                    upstream_xy = (x, y)
                if downstream_xy is not None:
                    x, y = tr.transform(
                        float(downstream_xy[0]), float(downstream_xy[1])
                    )
                    downstream_xy = (x, y)
            except Exception:
                pass

        # Upstream / downstream markers — filled circles
        if upstream_xy is not None:
            try:
                ax.plot(
                    float(upstream_xy[0]), float(upstream_xy[1]),
                    marker="o", markersize=12,
                    markerfacecolor="#f6ad55", markeredgecolor="#744210",
                    markeredgewidth=1.5,
                    linestyle="None", label="Upstream",
                )
            except Exception:
                pass
        if downstream_xy is not None:
            try:
                ax.plot(
                    float(downstream_xy[0]), float(downstream_xy[1]),
                    marker="o", markersize=12,
                    markerfacecolor="#f56565", markeredgecolor="#742a2a",
                    markeredgewidth=1.5,
                    linestyle="None", label="Downstream",
                )
            except Exception:
                pass

        # Force zoom to AOI + river bounds so the map always fills the
        # panel even if some markers ended up outside the visible area.
        try:
            b = list(aoi.total_bounds)   # [minx, miny, maxx, maxy]
            if river_plotted is not None:
                rb = river_plotted.total_bounds
                b = [min(b[0], rb[0]), min(b[1], rb[1]),
                     max(b[2], rb[2]), max(b[3], rb[3])]
            mx = (b[2] - b[0]) * 0.12
            my = (b[3] - b[1]) * 0.12
            ax.set_xlim(b[0] - mx, b[2] + mx)
            ax.set_ylim(b[1] - my, b[3] + my)
        except Exception:
            pass

        # Legend top-right
        if main_river_path or upstream_xy or downstream_xy:
            try:
                ax.legend(
                    loc="upper right", fontsize=9, frameon=True,
                    labelspacing=1.2, handletextpad=0.8, borderpad=0.6,
                )
            except Exception:
                pass

        self.draw_idle()

    # ── helpers ───────────────────────────────────────────────────────────────

    def _render_error(self, msg: str):
        self._fig.clear()
        ax = self._fig.add_subplot(1, 1, 1)
        ax.text(
            0.5, 0.5, msg,
            ha="center", va="center", transform=ax.transAxes,
            fontsize=10, color="red",
        )
        ax.set_xticks([]); ax.set_yticks([])
        self.draw_idle()
