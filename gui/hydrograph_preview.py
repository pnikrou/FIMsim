"""Lightweight matplotlib canvas that plots one AOI's BDY hydrograph.

Reads the helper CSV (datetime, discharge_cms) that core/bdy.py writes
alongside each AOI's <AOI>.bdy file, and draws a simple discharge-vs-time
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
        """Plot a discharge CSV.

        Accepted column layouts (in priority order):
          1. ``datetime``  + ``discharge_cms`` or ``streamflow_m3s``
          2. ``time_hours`` + ``discharge_cms`` or ``streamflow_m3s``

        The ``datetime`` column is preferred for the x-axis; if absent,
        ``time_hours`` is used so NWM/USGS CSVs always render.
        """
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

        if df.empty:
            self._render_error("Hydrograph CSV is empty.")
            return

        # Discharge column — prefer discharge_cms, fall back to streamflow_m3s
        q_col = None
        for candidate in ("discharge_cms", "streamflow_m3s"):
            if candidate in df.columns:
                q_col = candidate
                break
        if q_col is None:
            self._render_error(
                "CSV is missing a discharge column.\n"
                "Expected 'discharge_cms' or 'streamflow_m3s'."
            )
            return

        # X-axis: use time_hours (may hold actual timestamps or numeric hours).
        # Also accept legacy CSVs that have a separate 'datetime' column.
        if "time_hours" in df.columns:
            df = df.dropna(subset=["time_hours", q_col])
            if df.empty:
                self._render_error("Hydrograph CSV is empty.")
                return
            if not pd.api.types.is_numeric_dtype(df["time_hours"]):
                # time_hours contains datetime strings — parse and display as dates
                try:
                    df["time_hours"] = pd.to_datetime(df["time_hours"], errors="coerce")
                    df = df.dropna(subset=["time_hours"])
                    auto_fmt_date = True
                except Exception:
                    auto_fmt_date = False
            else:
                auto_fmt_date = False
            x_data  = df["time_hours"]
            x_label = "Time" if auto_fmt_date else "Time (hours)"
        elif "datetime" in df.columns:
            # Legacy CSV format
            try:
                df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
                df = df.dropna(subset=["datetime"])
            except Exception:
                pass
            if df.empty:
                self._render_error("Hydrograph CSV is empty.")
                return
            x_data  = df["datetime"]
            x_label = "Time"
            auto_fmt_date = True
        else:
            self._render_error(
                "CSV is missing a time column.\n"
                "Expected 'time_hours' or 'datetime'."
            )
            return

        # 1×3 grid — middle column hosts the plot, side columns absorb extra width
        self._fig.clear()
        gs = self._fig.add_gridspec(
            1, 3, width_ratios=[1, 12, 1], wspace=0.0,
        )
        for col in (0, 2):
            sp = self._fig.add_subplot(gs[0, col])
            sp.set_xticks([]); sp.set_yticks([])
            sp.set_frame_on(False)
        ax = self._fig.add_subplot(gs[0, 1])

        ax.plot(x_data, df[q_col], color="#2b6cb0", linewidth=1.6)
        ax.fill_between(x_data, df[q_col], color="#bee3f8", alpha=0.6)
        ax.set_ylabel("Discharge (m³/s)", fontsize=9)
        ax.set_xlabel(x_label, fontsize=9)
        ax.tick_params(axis="x", labelsize=8, rotation=20)
        ax.tick_params(axis="y", labelsize=8)
        ax.grid(True, linestyle=":", alpha=0.5)
        if title:
            ax.set_title(title, fontsize=10)
        if auto_fmt_date:
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
