#!/bin/bash
# Assemble FIMsim.app, and with --dmg also FIMsim-mac.dmg.
#
#   installer/macos/build_app.sh <out-dir> <micromamba-arm64> <micromamba-x86_64> [--dmg]
#
# The app contains only scripts, FIMsim's own files and the two micromamba
# programs (Apple Silicon and Intel), so one app and one .dmg serve both kinds
# of Mac.  The Python environment is built on the user's Mac at first launch.
set -euo pipefail

if [ $# -lt 3 ]; then sed -n '2,8p' "$0"; exit 2; fi
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="$(mkdir -p "$1" && cd "$1" && pwd)"
MM_ARM="$2"; MM_X64="$3"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
APP="$OUT/FIMsim.app"
RES="$APP/Contents/Resources"

echo "Building FIMsim.app $VERSION in $OUT"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$RES/bin/arm64" "$RES/bin/x86_64"

cp "$HERE/launcher.sh" "$APP/Contents/MacOS/FIMsim"
cp "$HERE/common.sh" "$HERE/setup.sh" "$ROOT/installer/smoke_test.py" "$RES/"
cp "$ROOT/assets/icon.icns" "$RES/FIMsim.icns"
cp "$MM_ARM" "$RES/bin/arm64/micromamba"
cp "$MM_X64" "$RES/bin/x86_64/micromamba"
chmod +x "$APP/Contents/MacOS/FIMsim" "$RES/setup.sh" "$RES/common.sh" "$RES/bin/arm64/micromamba" "$RES/bin/x86_64/micromamba"
sed "s/@VERSION@/$VERSION/g" "$HERE/Info.plist.in" > "$APP/Contents/Info.plist"
python3 "$ROOT/installer/stage_payload.py" "$RES"

# Ad-hoc signature: makes the bundle self-consistent (not a developer identity,
# so macOS still asks the person to confirm the first launch).
codesign --force --sign - "$RES/bin/arm64/micromamba" "$RES/bin/x86_64/micromamba"
codesign --force --sign - "$APP"
codesign --verify --verbose=2 "$APP"
echo "Built $APP ($(du -sh "$APP" | cut -f1))"

if [ "${4:-}" = "--dmg" ]; then
    DMG="$OUT/FIMsim-mac.dmg"
    STAGE="$(mktemp -d)"
    cp -R "$APP" "$STAGE/"
    ln -s /Applications "$STAGE/Applications"
    cat > "$STAGE/Read me first.txt" <<'EOF'
FIMsim for macOS
================

1. Drag FIMsim into the Applications folder.
2. Open FIMsim from Applications.
   The first time, macOS may say it cannot verify the developer
   (FIMsim is not signed with a paid Apple developer account).
   - Right-click (or Control-click) FIMsim, choose Open, then Open again.
   - On macOS 15 or newer, if there is no Open button: open
     System Settings > Privacy & Security, scroll down, click
     "Open Anyway" next to FIMsim, and confirm.
3. FIMsim now sets itself up once. A Terminal window shows the progress.
   It needs internet, downloads about 2-3 GB, takes 10-20 minutes and uses
   about 6 GB of disk. FIMsim opens by itself when it is done.
4. From then on, FIMsim opens straight away.

Help and updates: https://github.com/sdmlua/FIMsim
EOF
    rm -f "$DMG"
    hdiutil create -volname "FIMsim" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
    rm -rf "$STAGE"
    echo "Built $DMG ($(du -sh "$DMG" | cut -f1))"
fi
