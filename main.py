"""Entry point for the Flood Model Preprocessing Tool (5-mode)."""
import sys

# Configure matplotlib for Qt6 BEFORE any figure is created
import matplotlib
matplotlib.use("QtAgg")

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

from gui.app import MainWindow


def main():
    # High-DPI support
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("Flood Model Prep Tool")
    app.setOrganizationName("YourLab")

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
