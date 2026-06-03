"""Lightweight matplotlib canvas that plots one AOI's BDY hydrograph.

Reads the helper CSV (datetime, discharge_cms) that core/bdy.py writes
alongside each AOI's BC.bdy file, and draws a simple discharge-vs-time
line plot.  Used by step_bdy after the run completes.
"""
from typing import Optional

from PyQt6.QtWidgets import QSizePolicy
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure


class HydrographPreviewCanvas(FigureCanvas):
    """Plot one AOI's discharge time series."""

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

    def show_hydrograph(
        self,
        csv_path: str,
        title: Optional[str] = None,
    ):
        """Plot ``csv_path`` (columns: ``datetime`` and ``discharge_cms``)."""
        try:
            import pandas as pd
        except Exception as ex:
            self._render_error(f"Cannot render hydrograph:\n{ex}")
            return

        try:
            df = pd.read_csv(csv_path)
        except Exception as ex:
            self._render_error(f"Cannot read CSV:\n{ex}")
            return

        # Accept either column name: BDY step uses "discharge_cms",
        # flowline/flow-data steps use "streamflow_m3s".
        q_col = None
        for candidate in ("discharge_cms", "streamflow_m3s"):
            if candidate in df.columns:
                q_col = candidate
                break
        if "datetime" not in df.columns or q_col is None:
            self._render_error(
                "CSV is missing the expected columns.\n"
                "Need 'datetime' and either 'discharge_cms' or 'streamflow_m3s'."
            )
            return

        try:
            df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
            df = df.dropna(subset=["datetime"])
        except Exception:
            pass

        if df.empty:
            self._render_error("Hydrograph CSV is empty.")
            return

        # 1×3 grid — middle column hosts the plot, side columns absorb
        # extra width (same trick as the raster + BCI previews).
        self._fig.clear()
        gs = self._fig.add_gridspec(
            1, 3, width_ratios=[1, 12, 1], wspace=0.0,
        )
        for col in (0, 2):
            sp = self._fig.add_subplot(gs[0, col])
            sp.set_xticks([]); sp.set_yticks([])
            sp.set_frame_on(False)
        ax = self._fig.add_subplot(gs[0, 1])

        ax.plot(
            df["datetime"], df[q_col],
            color="#2b6cb0", linewidth=1.6,
        )
        ax.fill_between(
            df["datetime"], df[q_col],
            color="#bee3f8", alpha=0.6,
        )
        ax.set_ylabel("Discharge (m³/s)", fontsize=9)
        ax.set_xlabel("Time", fontsize=9)
        ax.tick_params(axis="x", labelsize=8, rotation=20)
        ax.tick_params(axis="y", labelsize=8)
        ax.grid(True, linestyle=":", alpha=0.5)
        if title:
            ax.set_title(title, fontsize=10)
        self._fig.autofmt_xdate()
        self.draw_idle()

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
