"""Runtime hook — executed by the frozen app before main.py.

Sets the environment variables that GDAL, PROJ, rasterio, and pyproj need
to find their data files (coordinate databases, projection grids, etc.)
when running inside a PyInstaller bundle.  Without this, any rasterio/
pyproj call that touches CRS data raises a PROJ / GDAL init error.
"""
import os
import sys

if hasattr(sys, "_MEIPASS"):
    base = sys._MEIPASS  # root of the unpacked bundle

    # ── GDAL data (CSV tables, coordinate system definitions) ─────────────────
    for gdal_candidate in [
        os.path.join(base, "rasterio", "gdal_data"),
        os.path.join(base, "gdal_data"),
        os.path.join(base, "rasterio"),
    ]:
        if os.path.isdir(gdal_candidate):
            os.environ["GDAL_DATA"] = gdal_candidate
            break

    # ── PROJ data (proj.db, grids) ────────────────────────────────────────────
    for proj_candidate in [
        os.path.join(base, "pyproj", "proj_dir", "share", "proj"),
        os.path.join(base, "proj_dir", "share", "proj"),
        os.path.join(base, "share", "proj"),
        os.path.join(base, "proj"),
    ]:
        if os.path.isdir(proj_candidate):
            os.environ["PROJ_LIB"] = proj_candidate
            os.environ["PROJ_DATA"] = proj_candidate
            break

    # Disable PROJ network lookups — the user may be offline and CDN grids
    # are not bundled; prevents long hangs on startup.
    os.environ.setdefault("PROJ_NETWORK", "OFF")

    # ── SSL certs (needed for HTTPS data downloads) ───────────────────────────
    for cert_candidate in [
        os.path.join(base, "certifi", "cacert.pem"),
        os.path.join(base, "cacert.pem"),
    ]:
        if os.path.isfile(cert_candidate):
            os.environ.setdefault("SSL_CERT_FILE", cert_candidate)
            os.environ.setdefault("REQUESTS_CA_BUNDLE", cert_candidate)
            break

    # ── matplotlib backend (must be set before any figure is created) ─────────
    os.environ.setdefault("MPLBACKEND", "QtAgg")
