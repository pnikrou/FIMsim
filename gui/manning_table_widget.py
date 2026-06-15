"""Reusable Manning's n table widget.

Columns: Code | Class Name | Min n | Max n | Avg n (editable, clamped to [min,max])

Used by the standalone LULC/Manning mode and the LISFLOOD/TRITON
Manning steps.  Accepts either the NLCD or Sentinel-2 mapping dict from
core/nlcd.py.
"""
from typing import Dict, Optional, Tuple

from PyQt6.QtWidgets import (
    QTableWidget, QTableWidgetItem, QHeaderView, QDoubleSpinBox,
    QAbstractItemView,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor


class ManningTableWidget(QTableWidget):
    """Display + edit a Manning n mapping with min/max bounds.

    Parameters
    ----------
    table_data : dict[int, tuple(name, min_n, max_n, default_n)]
        e.g. NLCD_MANNING from core/nlcd.py
    """

    COL_CODE = 0
    COL_NAME = 1
    COL_MIN  = 2
    COL_MAX  = 3
    COL_AVG  = 4

    def __init__(self, table_data: Optional[Dict] = None, parent=None):
        super().__init__(parent)
        self._table_data = {}
        self._spinboxes: Dict[int, QDoubleSpinBox] = {}
        self._setup_ui()
        if table_data is not None:
            self.set_table_data(table_data)

    # ── setup ─────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        self.setColumnCount(5)
        self.setHorizontalHeaderLabels(
            ["Code", "Land Cover Class", "Min n", "Max n", "Avg n (editable)"]
        )
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)

        h = self.horizontalHeader()
        h.setSectionResizeMode(self.COL_CODE, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(self.COL_NAME, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(self.COL_MIN, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(self.COL_MAX, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(self.COL_AVG, QHeaderView.ResizeMode.ResizeToContents)

    # ── public API ────────────────────────────────────────────────────────────

    def set_table_data(self, table_data: Dict[int, Tuple]):
        """Repopulate the table with a new mapping (NLCD or Sentinel-2).

        Rows where min/max/default are all None are kept (e.g. NLCD's "Clouds"
        class for Sentinel-2 — no Manning value).
        """
        self._table_data = dict(table_data)
        self._spinboxes = {}
        # Sort by code
        sorted_codes = sorted(self._table_data.keys())
        self.setRowCount(len(sorted_codes))

        for row, code in enumerate(sorted_codes):
            name, mn, mx, dflt = self._table_data[code]

            # Code (read-only)
            it_code = QTableWidgetItem(str(code))
            it_code.setFlags(it_code.flags() & ~Qt.ItemFlag.ItemIsEditable)
            it_code.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.setItem(row, self.COL_CODE, it_code)

            # Name (read-only)
            it_name = QTableWidgetItem(str(name))
            it_name.setFlags(it_name.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.setItem(row, self.COL_NAME, it_name)

            # Min / Max (read-only, light grey background)
            min_str = "—" if mn is None else f"{mn:.3f}"
            max_str = "—" if mx is None else f"{mx:.3f}"
            it_min = QTableWidgetItem(min_str)
            it_max = QTableWidgetItem(max_str)
            for it in (it_min, it_max):
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                it.setBackground(QColor("#f7fafc"))
                it.setForeground(QColor("#4a5568"))
            self.setItem(row, self.COL_MIN, it_min)
            self.setItem(row, self.COL_MAX, it_max)

            # Avg n (editable spin box clamped to [min, max])
            if dflt is None:
                it_avg = QTableWidgetItem("—")
                it_avg.setFlags(it_avg.flags() & ~Qt.ItemFlag.ItemIsEditable)
                it_avg.setBackground(QColor("#fafafa"))
                it_avg.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.setItem(row, self.COL_AVG, it_avg)
            else:
                spin = QDoubleSpinBox()
                spin.setDecimals(4)
                spin.setSingleStep(0.001)
                lo = float(mn) if mn is not None else 0.0
                hi = float(mx) if mx is not None else 1.0
                spin.setRange(lo, hi)
                spin.setValue(float(dflt))
                spin.setStyleSheet("padding:2px;")
                self.setCellWidget(row, self.COL_AVG, spin)
                self._spinboxes[code] = spin

    def set_values(self, values: Dict[int, float]):
        """Restore previously saved spinbox values (from get_mapping()).

        Only updates codes that have an editable spinbox; silently ignores
        others (e.g. a Sentinel-2 config applied to an NLCD table after a
        source change).
        """
        for code, n in values.items():
            spin = self._spinboxes.get(int(code))
            if spin is not None:
                # Clamp to the spinbox's own [min, max] range
                spin.setValue(max(spin.minimum(), min(spin.maximum(), float(n))))

    def get_mapping(self) -> Dict:
        """Return {code: avg_n} dict for the writable rows.

        Skips rows whose default is None (e.g. Sentinel-2 'Clouds').
        Adds a `"default"` key equal to the median of all values for unmapped
        pixel codes (used by `create_manning_from_lulc`).
        """
        out = {}
        for code, spin in self._spinboxes.items():
            out[int(code)] = float(spin.value())
        if out:
            vals = sorted(out.values())
            out["default"] = vals[len(vals) // 2]
        return out
