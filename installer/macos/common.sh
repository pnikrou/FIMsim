#!/bin/bash
# Shared settings for the macOS launcher and setup script.  Source it after
# setting RES (the app's Contents/Resources folder).

APP_DIR="$RES/app"

# Where FIMsim keeps its Python environment.  FIMSIM_HOME exists so the build
# checks can run in a scratch folder instead of the real one.
HOME_DIR="${FIMSIM_HOME:-$HOME/Library/Application Support/FIMsim}"
ENV_DIR="$HOME_DIR/env"
STAMP_FILE="$HOME_DIR/env.stamp"

# Apple Silicon or Intel (a Rosetta shell reports x86_64 on an M-series Mac).
if [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ]; then ARCH=arm64; else ARCH=x86_64; fi
# micromamba must keep its own file name (it re-runs itself by that name).
MAMBA="$RES/bin/$ARCH/micromamba"

# The environment is rebuilt when these two files change (a new release that
# needs different packages), and only then.
want_stamp() {
    cat "$APP_DIR/environment.yml" "$APP_DIR/requirements-pip.txt" | shasum -a 256 | cut -d' ' -f1
}

env_ready() {
    [ -x "$ENV_DIR/bin/python" ] && [ "$(cat "$STAMP_FILE" 2>/dev/null)" = "$(want_stamp)" ]
}

# Settings every FIMsim process, and every process FIMsim starts, should see.
use_env() {
    export MAMBA_ROOT_PREFIX="$HOME_DIR/mamba"
    export PYTHONNOUSERSITE=1                       # ignore packages in ~/.local
    export PYTHONPYCACHEPREFIX="$HOME_DIR/pycache"  # keep .pyc out of the app bundle
    unset PYTHONPATH PYTHONHOME
    export FIMSIM_INSTALLED=1
    export FIMSIM_ENV_DIR="$ENV_DIR"
}

# Run a command inside the environment (activates it first, the way conda does).
run_in_env() {
    "$MAMBA" run -p "$ENV_DIR" "$@"
}
