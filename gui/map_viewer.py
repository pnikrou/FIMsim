"""US map widget — adaptive layout.

Chooses its layout from the number of AOI points:
  • **1 AOI**  → two panels: (left) CONUS with highlighted state,
                             (right) zoomed view of that state with a star.
  • **>1 AOI** → single panel: CONUS with highlighted states + numbered stars.
  • **0 AOI**  → single-panel title-only placeholder (no axes shown).

The number labels next to stars correspond to the `#` column in the
MultiAOIWidget feature table so users can match entries when names are long.
"""
from typing import List, Optional, Tuple

import matplotlib
matplotlib.use("QtAgg")

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from core.state_lookup import get_states_gdf


_CONUS_ABBRS = {
    "AL","AR","AZ","CA","CO","CT","DC","DE","FL","GA","IA","ID",
    "IL","IN","KS","KY","LA","MA","MD","ME","MI","MN","MO","MS",
    "MT","NC","ND","NE","NH","NJ","NM","NV","NY","OH","OK","OR",
    "PA","RI","SC","SD","TN","TX","UT","VA","VT","WA","WI","WV","WY",
}
CONUS_BOUNDS = (-125.0, 24.0, -66.5, 50.0)


class USMapCanvas(FigureCanvasQTAgg):
    """Adaptive US map canvas (1 or 2 panels depending on AOI count)."""

    def __init__(self, parent=None, width: float = 10.0, height: float = 4.5):
        self._fig = Figure(figsize=(width, height), tight_layout=True)
        super().__init__(self._fig)
        self.setParent(parent)
        self._states = None
        self.clear_plots()

    # ── public API ────────────────────────────────────────────────────────────

    def clear_plots(self):
        self._fig.clear()
        ax = self._fig.add_subplot(1, 1, 1)
        ax.set_xticks([]); ax.set_yticks([])
        ax.set_title("United States", fontsize=10)
        try:
            self.draw_idle()
        except Exception:
            pass

    def update_plots(
        self,
        highlighted_state_abbrs: List[str],
        aoi_points: List[Tuple[float, float]],
        aoi_labels: Optional[List[str]] = None,
        aoi_gdf=None,                # GeoDataFrame in any CRS — single AOI polygon
        main_river_gdf=None,         # GeoDataFrame — main river (highest order)
        all_flowlines_gdf=None,      # GeoDataFrame — all NHD reaches (thin underlay)
        usgs_gages: Optional[List[dict]] = None,
        legend_entries: Optional[List[str]] = None,
    ):
        """Refresh the map.

        Chooses layout automatically based on the data passed in:
          * 1 AOI + aoi_gdf provided        → 3 panels (US + state + AOI close-up)
          * 1 AOI                            → 2 panels (US + state zoom)
          * other                            → 1 CONUS panel with numbered stars
        """
        if self._states is None:
            try:
                self._states = get_states_gdf()
                if self._states.crs is None:
                    self._states.set_crs(4326, inplace=True)
                else:
                    self._states = self._states.to_crs(4326)
            except Exception as ex:
                self._fig.clear()
                ax = self._fig.add_subplot(1, 1, 1)
                ax.set_xticks([]); ax.set_yticks([])
                ax.text(0.5, 0.5, f"Could not load US states:\n{ex}",
                        ha="center", va="center", transform=ax.transAxes,
                        fontsize=9, color="red")
                self.draw_idle()
                return

        states = self._states
        highlighted = {a.upper() for a in (highlighted_state_abbrs or [])}
        points = aoi_points or []
        labels = aoi_labels or [str(i + 1) for i in range(len(points))]
        n = len(points)

        # ── reset and rebuild subplot layout ─────────────────────────────────
        self._fig.clear()
        ax_close = None
        if n == 1 and aoi_gdf is not None:
            # 3-panel: US + state zoom + AOI close-up with rivers / gages
            ax_us    = self._fig.add_subplot(1, 3, 1)
            ax_zoom  = self._fig.add_subplot(1, 3, 2)
            ax_close = self._fig.add_subplot(1, 3, 3)
        elif n == 1:
            ax_us = self._fig.add_subplot(1, 2, 1)
            ax_zoom = self._fig.add_subplot(1, 2, 2)
        else:
            ax_us = self._fig.add_subplot(1, 1, 1)
            ax_zoom = None

        # ── CONUS panel ──────────────────────────────────────────────────────
        ax_us.set_xticks([]); ax_us.set_yticks([])
        conus = states[states["state_abbr"].str.upper().isin(_CONUS_ABBRS)]
        conus.plot(ax=ax_us, facecolor="#f0f0f0", edgecolor="#888", linewidth=0.4)

        hl = conus[conus["state_abbr"].str.upper().isin(highlighted)]
        if not hl.empty:
            hl.plot(ax=ax_us, facecolor="#3182ce", edgecolor="#1a4480",
                    linewidth=0.8, alpha=0.7)
            for _, row in hl.iterrows():
                c = row.geometry.centroid
                ax_us.text(c.x, c.y, row["state_abbr"],
                           fontsize=9, ha="center", va="center",
                           color="white", weight="bold")

        # Stars on the CONUS panel only when there's >1 AOI (single-AOI case
        # shows the star on the zoomed state panel instead)
        if n != 1:
            for i, (lon, lat) in enumerate(points):
                ax_us.plot(lon, lat, marker="o", markersize=9,
                           color="#e53e3e", markeredgecolor="black",
                           markeredgewidth=0.6, zorder=5)
                lbl = labels[i] if i < len(labels) else str(i + 1)
                ax_us.annotate(lbl, (lon, lat),
                               xytext=(5, 5), textcoords="offset points",
                               fontsize=9, color="#c53030", weight="bold")
            # Legend block in the upper-left of the CONUS axes — when stars
            # overlap, this is the only way to tell which # is where.
            if legend_entries:
                # If there are many entries, split into 2 columns to fit
                MAX_PER_COL = 14
                cols = []
                for i in range(0, len(legend_entries), MAX_PER_COL):
                    cols.append("\n".join(legend_entries[i:i + MAX_PER_COL]))
                # Render each column in upper-left, side by side
                base_x = 0.01
                col_dx = 0.13
                for ci, col_text in enumerate(cols):
                    ax_us.text(
                        base_x + ci * col_dx, 0.99, col_text,
                        transform=ax_us.transAxes,
                        fontsize=7.5, family="monospace",
                        verticalalignment="top",
                        bbox=dict(
                            boxstyle="round,pad=0.3",
                            facecolor="white",
                            edgecolor="#888",
                            alpha=0.92,
                        ),
                    )

        ax_us.set_xlim(CONUS_BOUNDS[0], CONUS_BOUNDS[2])
        ax_us.set_ylim(CONUS_BOUNDS[1], CONUS_BOUNDS[3])

        # Title for CONUS panel
        if hl.empty and highlighted:
            title_us = f"United States — {', '.join(sorted(highlighted))}"
        elif not hl.empty:
            names = ", ".join(hl["state_name"].tolist())
            title_us = f"United States — {names}"
            if n > 1:
                title_us += f"  ({n} AOIs)"
        else:
            title_us = "United States"
        ax_us.set_title(title_us, fontsize=10)

        # ── State zoom panel (single-AOI case) ───────────────────────────────
        if ax_zoom is not None:
            ax_zoom.set_xticks([]); ax_zoom.set_yticks([])
            if not hl.empty:
                hl.plot(ax=ax_zoom, facecolor="#ebf8ff", edgecolor="#2c5282",
                        linewidth=1.2)
                lon, lat = points[0]
                ax_zoom.plot(lon, lat, marker="o", markersize=14,
                             color="#e53e3e", markeredgecolor="black",
                             markeredgewidth=0.8, zorder=5)
                if labels:
                    ax_zoom.annotate(labels[0], (lon, lat),
                                     xytext=(8, 8),
                                     textcoords="offset points",
                                     fontsize=10, color="#c53030",
                                     weight="bold")
                minx, miny, maxx, maxy = hl.total_bounds
                mx = (maxx - minx) * 0.08 or 0.5
                my = (maxy - miny) * 0.08 or 0.5
                ax_zoom.set_xlim(minx - mx, maxx + mx)
                ax_zoom.set_ylim(miny - my, maxy + my)
                ax_zoom.set_title(
                    f"{', '.join(hl['state_name'].tolist())} — AOI location",
                    fontsize=10,
                )
            else:
                ax_zoom.set_title("State view (AOI outside CONUS)", fontsize=10)

        # ── AOI close-up panel (3-panel layout only) ─────────────────────────
        if ax_close is not None and aoi_gdf is not None:
            ax_close.set_xticks([]); ax_close.set_yticks([])
            try:
                # Reproject AOI / river / gages to EPSG:4326 so overlays match.
                aoi_4326 = aoi_gdf.to_crs("EPSG:4326")
                aoi_4326.plot(ax=ax_close, facecolor="#ebf8ff",
                              edgecolor="#2c5282", linewidth=1.2, alpha=0.7)
                # All flowlines thin underlay (light blue/gray, drawn first)
                if all_flowlines_gdf is not None and not all_flowlines_gdf.empty:
                    all_flowlines_gdf.to_crs("EPSG:4326").plot(
                        ax=ax_close, color="#a0c4e8", linewidth=0.8, alpha=0.7,
                    )
                # Main river polyline (if known) — drawn on top
                if main_river_gdf is not None and not main_river_gdf.empty:
                    main_river_gdf.to_crs("EPSG:4326").plot(
                        ax=ax_close, color="#2b6cb0", linewidth=1.8,
                    )
                # USGS gages — plot each as a numbered green dot
                if usgs_gages:
                    for i, g in enumerate(usgs_gages, 1):
                        if g.get("lon") is None or g.get("lat") is None:
                            continue
                        ax_close.plot(g["lon"], g["lat"], marker="o",
                                      markersize=7, color="#2f855a",
                                      markeredgecolor="black",
                                      markeredgewidth=0.5, zorder=6)
                        ax_close.annotate(
                            str(i), (g["lon"], g["lat"]),
                            xytext=(4, 4), textcoords="offset points",
                            fontsize=7, color="#22543d", weight="bold",
                        )
                # Title: river name only — no "AOI close-up" label
                river_title = ""
                if main_river_gdf is not None and not main_river_gdf.empty:
                    try:
                        for col in ("river_name", "gnis_name", "name"):
                            val = main_river_gdf.iloc[0].get(col, "")
                            if val and str(val).strip() not in ("", "nan"):
                                river_title = str(val).strip()
                                break
                    except Exception:
                        pass
                # Tight bounds with a small margin
                minx, miny, maxx, maxy = aoi_4326.total_bounds
                mx = (maxx - minx) * 0.10 or 0.01
                my = (maxy - miny) * 0.10 or 0.01
                ax_close.set_xlim(minx - mx, maxx + mx)
                ax_close.set_ylim(miny - my, maxy + my)
                ax_close.set_title(river_title, fontsize=8)
            except Exception as ex:
                ax_close.text(0.5, 0.5, f"AOI close-up failed:\n{ex}",
                              ha="center", va="center",
                              transform=ax_close.transAxes,
                              fontsize=8, color="red")

        try:
            self.draw_idle()
        except Exception:
            pass
