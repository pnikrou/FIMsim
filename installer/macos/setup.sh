#!/bin/bash
# One-time set-up of FIMsim's Python environment (macOS).
#
#   setup.sh <path-to-FIMsim.app> interactive   run in a Terminal window; start FIMsim when done
#   setup.sh <path-to-FIMsim.app> inline        plain run (used by the build checks and --setup)
#
# It does exactly the two install steps of the source install, with the same
# files:   1. environment.yml  (conda-forge packages)
#          2. python -m core.deps --fix  (requirements-pip.txt: HAND-FIM + ARC)
# plus git, which fimserve needs to fetch the OWP mapping scripts.

APP_BUNDLE="$1"
MODE="${2:-interactive}"
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
RES="$SELF_DIR"
. "$RES/common.sh"

NEED_GB=8
mkdir -p "$HOME_DIR"
LOG="$HOME_DIR/setup.log"
LOCK="$HOME_DIR/setup.lock"

say()  { printf '%s\n' "$*"; }
fail() {
    say ""
    say "============================================================"
    say "  FIMsim could not be set up."
    say "  $*"
    say "  Full log: $LOG"
    say "  Open FIMsim again to retry; it starts from a clean slate."
    say "============================================================"
    rm -rf "$LOCK"
    if [ "$MODE" = "interactive" ]; then
        printf '\nPress Return to close this window. '
        read -r _
    fi
    exit 1
}

# ── another set-up already running? ──────────────────────────────────────────
if ! mkdir "$LOCK" 2>/dev/null; then
    OLD_PID="$(cat "$LOCK/pid" 2>/dev/null)"
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        say "FIMsim is already being set up in another window. Please wait for it to finish."
        [ "$MODE" = "interactive" ] && { printf 'Press Return to close this window. '; read -r _; }
        exit 0
    fi
    rm -rf "$LOCK"; mkdir "$LOCK" || fail "Cannot create $LOCK"
fi
echo $$ > "$LOCK/pid"

{
say ""
say "============================================================"
say "  FIMsim - one-time set-up"
say "============================================================"
say ""
say "  FIMsim runs flood models that need a large set of scientific"
say "  packages (GDAL, rasterio, ARC, OWP HAND-FIM, ...). They are"
say "  downloaded and installed ONCE, now. Later launches are instant."
say ""
say "  - needs an internet connection"
say "  - downloads about 1.5 GB; needs about 5 GB free while installing (about 3 GB after)"
say "  - takes about 5-20 minutes, depending on the connection"
say "  Please keep this window open."
say ""
} 2>&1 | tee "$LOG"

# ── disk space ───────────────────────────────────────────────────────────────
FREE_KB="$(df -k "$HOME_DIR" | awk 'NR==2 {print $4}')"
if [ -n "$FREE_KB" ] && [ "$FREE_KB" -lt $((NEED_GB * 1024 * 1024)) ]; then
    fail "Not enough free disk space: about ${NEED_GB} GB are needed, $((FREE_KB / 1024 / 1024)) GB are free."
fi

[ -x "$MAMBA" ] || fail "The package installer is missing from the app: $MAMBA"
use_env
rm -rf "$ENV_DIR" "$STAMP_FILE"

# Build exactly from FIMsim's files: ignore the person's own conda settings
# (~/.condarc can add other channels or turn off SSL checks) and pip settings.
MAMBA_ISOLATE=(--no-rc --override-channels -c conda-forge --strict-channel-priority)
export PIP_CONFIG_FILE=/dev/null

# set -o pipefail so a failing step is not hidden by `tee`.
set -o pipefail

say "[1/3] Installing the scientific packages (conda-forge) ..." | tee -a "$LOG"
"$MAMBA" create -y "${MAMBA_ISOLATE[@]}" -p "$ENV_DIR" -f "$APP_DIR/environment.yml" git 2>&1 | tee -a "$LOG" \
    || fail "Step 1 failed (conda-forge packages). Check the internet connection and try again."

say "" | tee -a "$LOG"
say "[2/3] Installing the HAND-FIM and ARC-Curve2Flood backends ..." | tee -a "$LOG"
( cd "$APP_DIR" && run_in_env python -m core.deps --fix ) 2>&1 | tee -a "$LOG" \
    || fail "Step 2 failed (HAND-FIM / ARC backends). Check the internet connection and try again."

say "" | tee -a "$LOG"
say "[3/3] Checking the installation ..." | tee -a "$LOG"
( cd "$APP_DIR" && run_in_env python -m core.deps ) 2>&1 | tee -a "$LOG" \
    || fail "The final check found a problem (see above)."

# The download cache is only needed during install - free the space.
"$MAMBA" clean --all -y --no-rc >/dev/null 2>&1
rm -rf "$HOME_DIR/mamba/pkgs"

want_stamp > "$STAMP_FILE"
rm -rf "$LOCK"

# FIMsim 1.2.0 kept its environment in "~/Library/Application Support/FIMsim"
# (a path with a space - see common.sh).  Free that space once the new one works.
# Only a folder FIMsim made is removed: it must hold FIMsim's env.stamp.
# (Tested on HOME_DIR, not on FIMSIM_HOME: the launcher's set-up window always
# exports FIMSIM_HOME, so checking that it is unset skipped this every time.)
if [ "$HOME_DIR" = "$HOME/Library/FIMsim" ] \
   && [ -f "$OLD_HOME_DIR/env.stamp" ] && [ -d "$OLD_HOME_DIR/env" ]; then
    say "Removing the environment of the previous FIMsim version ($OLD_HOME_DIR) ..." | tee -a "$LOG"
    rm -rf "${OLD_HOME_DIR:?}"
fi

say "" | tee -a "$LOG"
say "============================================================" | tee -a "$LOG"
say "  Done. FIMsim is ready." | tee -a "$LOG"
say "============================================================" | tee -a "$LOG"

if [ "$MODE" = "interactive" ]; then
    say "  Starting FIMsim - you can close this window."
    sleep 3
    open "$APP_BUNDLE"
fi
exit 0
