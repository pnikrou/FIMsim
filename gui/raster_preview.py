"""Lightweight matplotlib canvas that renders ONE GeoTIFF.

Used after the DEM / Manning steps complete: the user clicks an AOI from a
results list and the canvas shows just that AOI's raster — no states,
no borders, no overlays.  Single-band rasters get a colour map; multi-
band rasters fall back to band 1.
"""
from typing import Optional

from PyQt6.QtWidgets import QSizePolicy
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure


class RasterPreviewCanvas(FigureCanvas):
    """Show a single raster file with one matplotlib axes."""

    def __init__(self, parent=None, width: float = 9.0, height: float = 4.0):
        # constrained_layout keeps the axes + colorbar properly centered
        # within the figure rather than anchored to one edge.
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

    def show_raster(
        self,
        path: str,
        title: Optional[str] = None,
        cmap: str = "terrain",
        colorbar_label: str = "",
        colorbar_location: str = "right",
    ):
        """Render ``path`` (a single-band GeoTIFF) as a flat image.

        Parameters
        ----------
        colorbar_location
            ``"right"`` (vertical, default) or ``"bottom"`` (horizontal).
            Use ``"bottom"`` to maximise the map's width at the cost of a
            thin horizontal strip below the image.
        """
        try:
            import rasterio
            from rasterio.plot import plotting_extent
            import numpy as np
        except Exception as ex:
            self._fig.clear()
            ax = self._fig.add_subplot(1, 1, 1)
            ax.text(
                0.5, 0.5, f"Cannot render raster:\n{ex}",
                ha="center", va="center", transform=ax.transAxes,
                fontsize=9, color="red",
            )
            ax.set_xticks([]); ax.set_yticks([])
            self.draw_idle()
            return

        try:
            with rasterio.open(path) as src:
                arr = src.read(1, masked=True)
                extent = plotting_extent(src)
                nodata = src.nodata
        except Exception as ex:
            self._fig.clear()
            ax = self._fig.add_subplot(1, 1, 1)
            ax.text(
                0.5, 0.5, f"Cannot open raster:\n{ex}",
                ha="center", va="center", transform=ax.transAxes,
                fontsize=9, color="red",
            )
            ax.set_xticks([]); ax.set_yticks([])
            self.draw_idle()
            return

        # Mask nodata explicitly.  Use masked_values (tolerant float comparison)
        # rather than masked_equal (exact equality) so float32 sentinels like
        # -9999.0 are reliably caught even with minor precision differences.
        if nodata is not None:
            try:
                import math
                if isinstance(nodata, float) and not math.isfinite(nodata):
                    # inf / nan sentinels — mask by non-finite test
                    arr = np.ma.masked_invalid(arr)
                else:
                    arr = np.ma.masked_values(arr, nodata, rtol=1e-5)
            except Exception:
                pass

        self._fig.clear()
        self._fig.patch.set_facecolor("white")
        ax = self._fig.add_subplot(1, 1, 1)
        ax.set_facecolor("white")

        # Ensure masked / nodata pixels render as white, not the colormap's
        # default "bad" colour (which can be dark or semi-transparent).
        import matplotlib.cm as _cm
        import copy as _copy
        cmap_obj = _copy.copy(_cm.get_cmap(cmap))
        cmap_obj.set_bad(color="white", alpha=1.0)

        im = ax.imshow(arr, extent=extent, cmap=cmap_obj, origin="upper")
        ax.set_xticks([]); ax.set_yticks([])
        if title:
            ax.set_title(title, fontsize=10, pad=3)

        if colorbar_location == "bottom":
            cbar = self._fig.colorbar(
                im, ax=ax,
                orientation="horizontal", location="bottom",
                fraction=0.045, pad=0.03, shrink=0.85,
            )
        else:
            cbar = self._fig.colorbar(
                im, ax=ax,
                fraction=0.04, pad=0.02,
            )
        if colorbar_label:
            cbar.set_label(colorbar_label, fontsize=7)
        cbar.ax.tick_params(labelsize=6)
        self.draw_idle()
