"""Self-check for an installed FIMsim environment.

Run by the installers' build (CI) and available to anyone:

    FIMsim --smoke-test            (macOS: Contents/MacOS/FIMsim)
    launch.ps1 -SmokeTest          (Windows)

It does what a real session needs, in the order a real session needs it:
the dependency check, the programs FIMsim launches as separate processes
(OWP HAND-FIM runs ``python``, ``git`` and ``aws``; ARC-Curve2Flood runs
``flood-mapping``), and the main window.  Exit code 0 means all of it worked.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent / "app"
if not APP_DIR.is_dir():                      # run from a source checkout
    APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))
os.chdir(APP_DIR)

FAILED = []


def check(label, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    if not ok:
        FAILED.append(label)


print(f"Python {sys.version.split()[0]} at {sys.executable}")
print(f"Application files: {APP_DIR}\n")

# 1. The environment check FIMsim runs at start-up (imports fimserve, ARC, ...).
from core import deps                                            # noqa: E402
check("core.deps reports 'Environment OK'", deps.cli(show_header=False) == 0)

# 2. Programs that FIMsim's backends start as separate processes - found on
#    PATH (that is how fimserve calls them) AND actually able to start.
#    Finding them is not enough: a script whose "#!" line holds a path with a
#    space is found but cannot run ("bad interpreter").
for tool, args in (("git", ["--version"]), ("aws", ["--version"]),
                   ("flood-mapping", ["--help"])):
    exe = shutil.which(tool)
    check(f"'{tool}' is on PATH", exe is not None, exe or "")
    if exe:
        try:
            out = subprocess.run([exe, *args], capture_output=True, text=True,
                                 timeout=300)
            ok = out.returncode == 0
            detail = (out.stdout or out.stderr).strip().splitlines()[:1]
        except OSError as exc:
            ok, detail = False, [str(exc)]
        check(f"'{tool}' runs", ok, detail[0] if detail else "")

# Every script in the environment must name an interpreter that exists.  The
# system reads a "#!" line up to the first space, so a path with a space in it
# ("…/Application Support/…/python") names a file that does not exist.
if os.name != "nt":
    bad = []
    bindir = Path(sys.executable).parent
    for f in sorted(bindir.iterdir()):
        try:
            with open(f, "rb") as fh:
                first = fh.readline(4096)
        except OSError:
            continue
        if not first.startswith(b"#!"):
            continue
        parts = first[2:].strip().split()
        interp = parts[0].decode("utf-8", "replace") if parts else ""
        if not interp or not os.path.exists(interp):
            bad.append(f.name)
    check("every script's '#!' interpreter exists", not bad,
          (", ".join(bad[:5]) + (" …" if len(bad) > 5 else "")) if bad else
          str(bindir))

# fimserve starts its mapping scripts with sys.executable: that has to be a
# real Python, not a copy of the application.
out = subprocess.run([sys.executable, "-c", "print(6*7)"],
                     capture_output=True, text=True, timeout=120)
check("sys.executable runs Python code", out.stdout.strip() == "42")

# 3. The main window, without a screen.
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("QT_API", "pyqt6")
try:
    import matplotlib
    matplotlib.use("QtAgg")
    from PyQt6.QtWidgets import QApplication
    app = QApplication([])
    from gui.app import MainWindow
    win = MainWindow()
    win.show()
    app.processEvents()
    check("main window opens", win.isVisible(), win.windowTitle())
    win.close()
except Exception as exc:                                         # pragma: no cover
    check("main window opens", False, f"{type(exc).__name__}: {exc}")

print()
if FAILED:
    print("SMOKE TEST FAILED: " + "; ".join(FAILED))
    sys.exit(1)
print("SMOKE TEST PASSED")
