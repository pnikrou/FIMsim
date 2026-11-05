#!/bin/bash
# FIMsim launcher - the program inside FIMsim.app (Contents/MacOS/FIMsim).
#
#   FIMsim               open FIMsim; the very first time, set it up first
#   FIMsim --setup       (re)build the Python environment here, no Terminal window
#   FIMsim --smoke-test  set up if needed, then run the self-check
#
# Why a set-up step exists: HAND-FIM and ARC-Curve2Flood start Python, git and
# aws as separate programs, which cannot work from a single frozen executable.
# So FIMsim runs in a real Python environment, built from environment.yml and
# requirements-pip.txt - the same files the tested source install uses.

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
RES="$(cd "$SELF_DIR/../Resources" && pwd)"
APP_BUNDLE="$(cd "$SELF_DIR/../.." && pwd)"
. "$RES/common.sh"

# A downloaded app is "quarantined"; macOS then refuses to run the helper
# programs inside it from a script.  The person already chose to open FIMsim.
xattr -dr com.apple.quarantine "$APP_BUNDLE" 2>/dev/null

case "${1:-}" in
    --setup)
        exec bash "$RES/setup.sh" "$APP_BUNDLE" inline
        ;;
    --smoke-test)
        if ! env_ready; then
            bash "$RES/setup.sh" "$APP_BUNDLE" inline || exit 1
        fi
        use_env
        mkdir -p "$HOME_DIR/work"; cd "$HOME_DIR/work" || exit 1
        exec "$MAMBA" run -p "$ENV_DIR" python "$RES/smoke_test.py"
        ;;
esac

if env_ready; then
    use_env
    mkdir -p "$HOME_DIR/work"; cd "$HOME_DIR/work" || exit 1   # libraries keep small caches in the current folder
    eval "$("$MAMBA" shell hook -s bash)"
    micromamba activate "$ENV_DIR"
    exec python "$APP_DIR/main.py"
fi

# First launch (or a release that changed the packages): set up in a Terminal
# window so the person can watch it work.  The command file is written here,
# not shipped, so macOS does not treat it as a quarantined download.
mkdir -p "$HOME_DIR"
CMD_FILE="$HOME_DIR/Set up FIMsim.command"
{
    echo '#!/bin/bash'
    printf 'export FIMSIM_HOME=%q\n' "$HOME_DIR"
    printf 'exec bash %q %q interactive\n' "$RES/setup.sh" "$APP_BUNDLE"
} > "$CMD_FILE"
chmod +x "$CMD_FILE"
exec open -a Terminal "$CMD_FILE"
