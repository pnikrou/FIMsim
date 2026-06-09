"""Lightweight matplotlib canvas that visualises one AOI's downloaded flowlines.

Shows the AOI polygon, optionally the main-river line, and optionally all
NHD flowlines — whatever the user downloaded.  Used by mode_flowline after
the flowline step completes (mirrors the BCIPreviewCanvas pattern).
"""
from typing import Optional, List, Dict

from PyQt6.QtWidgets import QSizePolicy
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure


class FlowlinePreviewCanvas(FigureCanvas):
    """Show one AOI polygon + downloaded flowline(s)."""

    def __init__(self, parent=None, width: float = 9.0, height: float = 4.0):
        self._fig = Figure(figsize=(width, height), constrained_layout=True)
        super().__init__(self._fig)
        self.setParent(parent)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.clear()

    # ── public API ────────────────────────────────────────────────────────────

    def clear(self):
        self._fig.clear()
        ax = self._fig.add_subplot(1, 1, 1)
        ax.set_xticks([]); ax.set_yticks([])
        try:
            self.draw_idle()
        except Exception:
            pass

    def show_flowlines(
        self,
        aoi_path: str,
        feature_index: int = 0,
        main_river_path: Optional[str] = None,
        all_flowlines_path: Optional[str] = None,
        main_river_gdf=None,
        all_flowlines_gdf=None,
        title: Optional[str] = None,
        usgs_gages: Optional[List[Dict]] = None,
        upstream_xy=None,
        downstream_xy=None,
    ):
        """Render AOI + flowline(s) + optional USGS gage markers for one AOI.

        Parameters
        ----------
        aoi_path
            Path to the AOI shapefile / GeoPackage.
        feature_index
            Which feature to select if the file has multiple.
        main_river_path
            Path to the saved main-river file (.shp/.gpkg/.csv with WKT).
            Ignored when *main_river_gdf* is supplied.
        all_flowlines_path
            Path to the saved all-flowlines file (.shp/.gpkg/.csv with WKT).
            Ignored when *all_flowlines_gdf* is supplied.
        main_river_gdf
            GeoDataFrame for the main river.  Takes priority over
            *main_river_path* — use this to guarantee the map always renders
            regardless of which file format the user chose for saving.
        all_flowlines_gdf
            GeoDataFrame for all flowlines.  Takes priority over
            *all_flowlines_path*.
        title
            Optional axes title.
        usgs_gages
            Optional list of dicts with keys ``lat``, ``lon``, ``site_no``.
            Each gage is plotted as a red circle.
        """
        try:
            import geopandas as gpd
        except Exception as ex:
            self._render_error(f"Cannot render flowline preview:\n{ex}")
            return

        # Load AOI polygon
        try:
            aoi = gpd.read_file(aoi_path)
            if feature_index is not None and len(aoi) > 1:
                aoi = aoi.iloc[[feature_index]].reset_index(drop=True)
            if aoi.crs is None:
                aoi = aoi.set_crs("EPSG:4326")
        except Exception as ex:
            self._render_error(f"Cannot open AOI file:\n{ex}")
            return

        # Single full-figure axes — no side-spacer frames
        self._fig.clear()
        ax = self._fig.add_subplot(1, 1, 1)
        ax.set_aspect("equal", adjustable="datalim")
        ax.set_xticks([]); ax.set_yticks([])
        if title:
            ax.set_title(title, fontsize=9, pad=3)

        # ── AOI polygon ───────────────────────────────────────────────────────
        try:
            aoi.plot(
                ax=ax, facecolor="#bee3f8", edgecolor="#2c5282",
                linewidth=1.0, alpha=0.5,
            )
        except Exception:
            pass

        import matplotlib.lines as mlines
        legend_handles = []

        # ── All flowlines (light underlay, drawn first) ───────────────────────
        # Prefer the in-memory GDF (always available) over the saved file path
        # so the map renders regardless of output format or save checkboxes.
        if all_flowlines_gdf is not None and not all_flowlines_gdf.empty:
            try:
                all_fl = (all_flowlines_gdf.to_crs(aoi.crs)
                          if all_flowlines_gdf.crs is not None and all_flowlines_gdf.crs != aoi.crs
                          else all_flowlines_gdf)
            except Exception:
                all_fl = all_flowlines_gdf
        else:
            all_fl = _load_gdf(all_flowlines_path, aoi.crs)
        if all_fl is not None and not all_fl.empty:
            try:
                all_fl.plot(ax=ax, color="#90cdf4", linewidth=0.8, alpha=0.8)
                legend_handles.append(
                    mlines.Line2D([], [], color="#90cdf4", linewidth=1.5,
                                  label="All flowlines")
                )
            except Exception:
                pass

        # ── Main river (thicker, drawn on top) ───────────────────────────────
        if main_river_gdf is not None and not main_river_gdf.empty:
            try:
                main_fl = (main_river_gdf.to_crs(aoi.crs)
                           if main_river_gdf.crs is not None and main_river_gdf.crs != aoi.crs
                           else main_river_gdf)
            except Exception:
                main_fl = main_river_gdf
        else:
            main_fl = _load_gdf(main_river_path, aoi.crs)
        if main_fl is not None and not main_fl.empty:
            try:
                main_fl.plot(ax=ax, color="#2b6cb0", linewidth=2.2)
                legend_handles.append(
                    mlines.Line2D([], [], color="#2b6cb0", linewidth=2.2,
                                  label="Main river")
                )
            except Exception:
                pass

        # ── USGS gage markers (red) ───────────────────────────────────────────
        if usgs_gages:
            # Build a list of valid (px, py, site_no) tuples first
            valid_gages = []
            for g in usgs_gages:
                try:
                    lat = float(g.get("lat") or g.get("latitude") or 0)
                    lon = float(g.get("lon") or g.get("longitude") or 0)
                    if lat == 0 and lon == 0:
                        continue
                    px, py = lon, lat
                    try:
                        if aoi.crs is not None and not aoi.crs.is_geographic:
                            from pyproj import Transformer
                            tf = Transformer.from_crs(
                                "EPSG:4326", aoi.crs.to_epsg(), always_xy=True
                            )
                            px, py = tf.transform(lon, lat)
                    except Exception:
                        pass
                    site_no = str(g.get("site_no") or "").strip()
                    valid_gages.append((px, py, site_no))
                except Exception:
                    continue

            multi = len(valid_gages) > 1

            for num, (px, py, site_no) in enumerate(valid_gages, 1):
                ax.plot(
                    px, py,
                    marker="o", markersize=10,
                    color="#e53e3e", markeredgecolor="white",
                    markeredgewidth=0.8, zorder=5, linestyle="none",
                )
                if multi:
                    # White number drawn at the centre of the marker
                    ax.text(
                        px, py, str(num),
                        ha="center", va="center",
                        fontsize=6, fontweight="bold", color="white",
                        zorder=6,
                    )

            # Legend entries
            if len(valid_gages) == 1:
                site_no = valid_gages[0][2]
                lbl = f"USGS gage  {site_no}" if site_no else "USGS gage"
                legend_handles.append(
                    mlines.Line2D(
                        [], [], marker="o", color="#e53e3e",
                        markeredgecolor="white", markeredgewidth=0.8,
                        markersize=8, linestyle="none", label=lbl,
                    )
                )
            else:
                for num, (_px, _py, site_no) in enumerate(valid_gages, 1):
                    lbl = f"{num}  ·  USGS {site_no}" if site_no else f"Gage {num}"
                    legend_handles.append(
                        mlines.Line2D(
                            [], [], marker="o", color="#e53e3e",
                            markeredgecolor="white", markeredgewidth=0.8,
                            markersize=8, linestyle="none", label=lbl,
                        )
                    )

        # ── Upstream / downstream endpoint markers ────────────────────────────
        # The upstream/downstream coords come from the main-river GDF which
        # may be in EPSG:4326 (geographic) or any other CRS.  We need to
        # reproject them to the AOI CRS so they overlay correctly.
        # Strategy: check if the GDF has a known CRS; if it differs from the
        # AOI CRS, reproject; otherwise plot directly.
        main_gdf_crs = None
        if main_fl is not None and main_fl.crs is not None:
            main_gdf_crs = main_fl.crs
        elif all_fl is not None and all_fl.crs is not None:
            main_gdf_crs = all_fl.crs

        def _reproject_pt(xy):
            if xy is None:
                return None
            try:
                src_crs = main_gdf_crs if main_gdf_crs is not None else "EPSG:4326"
                tgt_crs = aoi.crs if aoi.crs is not None else "EPSG:4326"
                if str(src_crs) != str(tgt_crs):
                    from pyproj import Transformer
                    tf = Transformer.from_crs(src_crs, tgt_crs, always_xy=True)
                    return tf.transform(float(xy[0]), float(xy[1]))
            except Exception:
                pass
            return (float(xy[0]), float(xy[1]))

        up_pt = _reproject_pt(upstream_xy)
        dn_pt = _reproject_pt(downstream_xy)

        if up_pt is not None:
            try:
                ax.plot(
                    up_pt[0], up_pt[1],
                    marker="o", markersize=11,
                    markerfacecolor="#f6ad55", markeredgecolor="#744210",
                    markeredgewidth=1.2, zorder=7, linestyle="none",
                )
                legend_handles.append(
                    mlines.Line2D([], [], marker="o",
                                  markerfacecolor="#f6ad55",
                                  markeredgecolor="#744210",
                                  markeredgewidth=1.2,
                                  markersize=9, linestyle="none",
                                  label="Upstream")
                )
            except Exception:
                pass

        if dn_pt is not None:
            try:
                ax.plot(
                    dn_pt[0], dn_pt[1],
                    marker="o", markersize=11,
                    markerfacecolor="#f56565", markeredgecolor="#742a2a",
                    markeredgewidth=1.2, zorder=7, linestyle="none",
                )
                legend_handles.append(
                    mlines.Line2D([], [], marker="o",
                                  markerfacecolor="#f56565",
                                  markeredgecolor="#742a2a",
                                  markeredgewidth=1.2,
                                  markersize=9, linestyle="none",
                                  label="Downstream")
                )
            except Exception:
                pass

        # Force zoom to AOI bounds so markers outside don't shrink the view
        try:
            b = list(aoi.total_bounds)
            mx = (b[2] - b[0]) * 0.12
            my = (b[3] - b[1]) * 0.12
            ax.set_xlim(b[0] - mx, b[2] + mx)
            ax.set_ylim(b[1] - my, b[3] + my)
        except Exception:
            pass

        if legend_handles:
            try:
                ax.legend(
                    handles=legend_handles,
                    loc="upper left", fontsize=8, frameon=True,
                    framealpha=0.85,
                    labelspacing=0.5, handletextpad=0.6, borderpad=0.5,
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


# ── module-level helper ───────────────────────────────────────────────────────

def _load_gdf(path: Optional[str], target_crs=None):
    """Load a SHP or CSV-with-WKT as a GeoDataFrame, reprojected to
    *target_crs* if supplied.  Returns None on any failure."""
    if not path:
        return None
    from pathlib import Path as _Path
    p = _Path(path)
    if not p.exists():
        return None
    try:
        import geopandas as gpd
        if p.suffix.lower() in (".shp", ".gpkg"):
            gdf = gpd.read_file(str(p))
        elif p.suffix.lower() == ".csv":
            import pandas as pd
            from shapely import wkt as _wkt
            df = pd.read_csv(str(p))
            if "geometry" not in df.columns:
                return None
            df["geometry"] = df["geometry"].apply(
                lambda g: _wkt.loads(g) if isinstance(g, str) else None
            )
            df = df.dropna(subset=["geometry"])
            gdf = gpd.GeoDataFrame(df, geometry="geometry", crs="EPSG:4326")
        else:
            return None

        if target_crs is not None and gdf.crs is not None and gdf.crs != target_crs:
            gdf = gdf.to_crs(target_crs)
        return gdf
    except Exception:
        return None
