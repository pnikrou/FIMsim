#!/usr/bin/env bash
# =============================================================================
# build_mac.sh  —  Build FIMsim.app for macOS and package as a .zip
#
# Usage (from the lisflood_prep_app/ directory):
#   chmod +x build_mac.sh
#   ./build_mac.sh
#
# Requirements:
#   - conda environment "lisflood_workflow" with all packages installed
#   - PyInstaller is installed automatically by this script
#
# Output:
#   dist/FIMsim.app       ← the macOS app bundle
#   dist/FIMsim-mac.zip   ← zip this and share it
# =============================================================================
set -euo pipefail

CONDA_ENV="lisflood_workflow"
APP_NAME="FIMsim"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "============================================================"
echo "  FIMsim — macOS Build"
echo "============================================================"
echo ""

# ── Activate conda env ────────────────────────────────────────────────────────
echo "► Activating conda env: $CONDA_ENV"
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate "$CONDA_ENV"

PYTHON="$(which python)"
PIP="$(which pip)"
PYINSTALLER="$(which pyinstaller)"

echo "  Python    : $PYTHON"
echo "  PyInstaller: $PYINSTALLER"
echo ""

# ── Install / upgrade PyInstaller ─────────────────────────────────────────────
echo "► Installing/upgrading PyInstaller..."
"$PIP" install --quiet --upgrade pyinstaller
echo "  PyInstaller $(pyinstaller --version) ready"
echo ""

# ── Clean previous build ──────────────────────────────────────────────────────
echo "► Cleaning previous build artifacts..."
rm -rf "$SCRIPT_DIR/build" "$SCRIPT_DIR/dist"
echo "  Cleaned."
echo ""

# ── Run PyInstaller ───────────────────────────────────────────────────────────
echo "► Running PyInstaller (this takes 3–8 minutes)..."
cd "$SCRIPT_DIR"
"$PYINSTALLER" build_mac.spec --noconfirm
echo ""

# ── Verify output ─────────────────────────────────────────────────────────────
if [ ! -d "$SCRIPT_DIR/dist/${APP_NAME}.app" ]; then
    echo "ERROR: dist/${APP_NAME}.app was not created. Check the output above."
    exit 1
fi
echo "✓ App bundle created: dist/${APP_NAME}.app"
echo ""

# ── Remove quarantine flag so macOS does not block the app ────────────────────
echo "► Removing quarantine flags (prevents 'damaged app' warnings)..."
xattr -cr "$SCRIPT_DIR/dist/${APP_NAME}.app" 2>/dev/null || true
echo "  Done."
echo ""

# ── Package as zip ────────────────────────────────────────────────────────────
echo "► Creating zip archive for distribution..."
(cd "$SCRIPT_DIR/dist" && zip -r --symlinks "${APP_NAME}-mac.zip" "${APP_NAME}.app")
ZIP_SIZE=$(du -sh "$SCRIPT_DIR/dist/${APP_NAME}-mac.zip" | cut -f1)
echo ""
echo "============================================================"
echo "  BUILD COMPLETE"
echo "============================================================"
echo ""
echo "  File to share:  dist/${APP_NAME}-mac.zip  (${ZIP_SIZE})"
echo ""
echo "  HOW TO DISTRIBUTE:"
echo "  1. Upload dist/${APP_NAME}-mac.zip to Google Drive, Dropbox,"
echo "     Dropbox, or WeTransfer."
echo "  2. Share the download link."
echo ""
echo "  HOW THE RECIPIENT OPENS IT:"
echo "  1. Download and unzip — FIMsim.app appears."
echo "  2. Right-click FIMsim.app → Open  (first time only, to bypass"
echo "     Gatekeeper on unsigned apps)."
echo "  3. After the first launch, double-click works normally."
echo ""
