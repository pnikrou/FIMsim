"""Entry point for the Flood Model Preprocessing Tool (5-mode)."""
import sys
import os

# On Windows, add conda env's Library\bin to the DLL search path so that
# GDAL, PROJ, and other native libraries are found before any system copies.
if sys.platform == "win32":
    _conda_bin = os.path.join(os.path.dirname(sys.executable), "Library", "bin")
    if os.path.isdir(_conda_bin):
        os.add_dll_directory(_conda_bin)

# Configure matplotlib for Qt6 BEFORE any figure is created
import matplotlib
matplotlib.use("QtAgg")

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

from gui.app import MainWindow


def main():
    # Global exception hook: PyQt6 calls qFatal() (a hard abort — the macOS
    # "python quit unexpectedly" dialog) when a Python exception escapes a Qt
    # slot while sys.excepthook is still the default.  Install our own hook so
    # any such bug prints a traceback and appears in the log panel instead of
    # killing the whole application.
    import traceback

    def _excepthook(exc_type, exc_value, exc_tb):
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        sys.stderr.write(text)
        win = getattr(_excepthook, "window", None)
        if win is not None:
            try:
                win._append_log("UNEXPECTED ERROR (work continues; please report):")
                for line in text.rstrip().splitlines():
                    win._append_log(f"  {line}")
            except Exception:
                pass

    sys.excepthook = _excepthook

    # High-DPI support
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("Flood Model Prep Tool")
    app.setOrganizationName("YourLab")
    # macOS NSOpenPanel assertion failure workaround: use Qt's own file dialogs
    # instead of the native macOS picker, which crashes with PyQt6 on newer macOS.
    app.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, True)

    window = MainWindow()
    _excepthook.window = window
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
