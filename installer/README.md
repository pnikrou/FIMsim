# FIMsim installers

How the macOS `.dmg` and the Windows `.exe` work, and why they are built this way.

## Why the installer builds the Python environment on first start

FIMsim is a PyQt desktop program, but two of its four flood models start **other
programs** while they run:

| Model | What it starts |
|---|---|
| OWP HAND-FIM (`fimserve`) | `python` (the OWP mapping scripts), `git` (to fetch them), `aws` (HAND data) |
| ARC-Curve2Flood (`nencarta`) | the `flood-mapping` command, and `python -c` for input validation |

A single frozen executable (PyInstaller) cannot do that: inside it, "python" is
FIMsim itself. So the installers ship FIMsim's files plus a small package
installer (`micromamba`), and the first start builds a real Python environment
from the **same two files the source install uses**:

1. `environment.yml` — the conda-forge packages, plus `git`
2. `requirements-pip.txt` — HAND-FIM and ARC-Curve2Flood, via `python -m core.deps --fix`

Then `python -m core.deps` must report `Environment OK`. Nothing is resolved
differently from the verified environment, so what was tested is what ships.
Later starts skip all of this (a stamp file records which two files the
environment was built from; a release that changes either one rebuilds it).

The environment lives outside the app, in the user's profile
(`~/Library/FIMsim` on macOS, `%LOCALAPPDATA%\FIMsim` on
Windows), so the app itself stays small (about 50-100 MB) and read-only.

**The environment path must not contain a space.** conda writes the path into
the first line (`#!…/python`) of its scripts, and macOS cuts that line at the
first space. Version 1.2.0 used `~/Library/Application Support/FIMsim` and every
such script failed - `aws` included, so OWP HAND-FIM could not download its
data. 1.2.1 uses `~/Library/FIMsim` (and removes the 1.2.0 folder after setting
up). `smoke_test.py` now runs `aws`, `git` and `flood-mapping` and checks every
script's interpreter, and CI uses the real default location.

## Layout

```
installer/
  stage_payload.py      copies the files FIMsim needs at run time (shared by both installers)
  smoke_test.py         self-check: core.deps, git/aws/flood-mapping, python subprocess, main window
  macos/
    build_app.sh        assembles FIMsim.app (and, with --dmg, FIMsim-mac.dmg)
    launcher.sh         becomes Contents/MacOS/FIMsim
    setup.sh            the one-time set-up (runs in a Terminal window)
    common.sh           shared settings
    Info.plist.in
  windows/
    FIMsim.iss          Inno Setup script
    launch.ps1          what the Start Menu shortcut runs (hidden)
    setup.ps1           the one-time set-up (runs in a console window)
    common.ps1          shared settings
```

## What CI does (`.github/workflows/build.yml`)

On every build, for each OS: assemble the installer, **install it the way a user
would**, start it for the first time on an empty machine (this builds the whole
environment), and run `smoke_test.py`. Only if that passes is the installer
uploaded; only a `vX.Y.Z` tag publishes a Release.

* `git push origin vX.Y.Z` — build, check, publish a Release.
* push a branch named `ci/<anything>` — build and check, publish nothing.

## Making a release

1. Put the new number in `VERSION` (for example `1.3.0`) and commit it.
2. `git tag v1.3.0 && git push origin v1.3.0` (the build refuses a tag that does
   not match `VERSION`).
3. When the build is green, the Release page has `FIMsim-mac.dmg` and
   `FIMsim-setup-windows.exe`.

## Trying it locally (macOS)

```bash
bash installer/macos/build_app.sh /tmp/fimsim-build /path/to/micromamba /path/to/micromamba --dmg
FIMSIM_HOME=/tmp/fimsim-home /tmp/fimsim-build/FIMsim.app/Contents/MacOS/FIMsim --smoke-test
```

`FIMSIM_HOME` keeps the test environment away from the real one. Without
`--smoke-test`, opening the app does what a user's double-click does.

## Not code-signed

The installers are not signed with a paid developer certificate, so macOS
Gatekeeper and Windows SmartScreen ask the user to confirm the first launch.
The Release notes and the `.dmg` explain how.
