#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# build_mac.sh  —  Build FIMsim.app and package it as FIMsim-mac.dmg
#
# Usage (from lisflood_prep_app/ directory):
#   chmod +x build_mac.sh
#   ./build_mac.sh
#
# Requirements:
#   - conda env "lisflood_workflow" with all packages installed
#   - pip install pyinstaller  (inside that env)
#   - Optional: brew install create-dmg   (for the nice .dmg step)
# ─────────────────────────────────────────────────────────────────────────────
set -e

CONDA_ENV="lisflood_workflow"
APP_NAME="FIMsim"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Activating conda env: $CONDA_ENV"
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate "$CONDA_ENV"

echo "==> Installing / upgrading PyInstaller"
pip install --quiet --upgrade pyinstaller

echo "==> Cleaning previous build artifacts"
rm -rf "$SCRIPT_DIR/build" "$SCRIPT_DIR/dist"

echo "==> Running PyInstaller..."
cd "$SCRIPT_DIR"
pyinstaller build_app.spec --noconfirm

echo ""
echo "✓ App bundle created: dist/FIMsim.app"

# ── Package as .dmg ───────────────────────────────────────────────────────────
if command -v create-dmg &>/dev/null; then
    echo "==> Packaging as .dmg with create-dmg..."
    create-dmg \
        --volname "$APP_NAME" \
        --window-size 600 400 \
        --icon-size 128 \
        --icon "${APP_NAME}.app" 150 185 \
        --app-drop-link 450 185 \
        --no-internet-enable \
        "dist/${APP_NAME}-mac.dmg" \
        "dist/${APP_NAME}.app"
    echo "✓ Installer ready: dist/${APP_NAME}-mac.dmg"
else
    echo ""
    echo "  (create-dmg not found — skipping .dmg step)"
    echo "  To install: brew install create-dmg"
    echo "  Or just zip the .app: cd dist && zip -r FIMsim-mac.zip FIMsim.app"
    cd dist && zip -r "${APP_NAME}-mac.zip" "${APP_NAME}.app"
    echo "✓ Created dist/${APP_NAME}-mac.zip instead"
fi

echo ""
echo "Done! Distribute dist/${APP_NAME}-mac.dmg (or .zip) to Mac users."
echo "They just double-click — no Python, no terminal, no setup required."
