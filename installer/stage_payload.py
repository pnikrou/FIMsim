"""Copy the files FIMsim needs at run time into <dest>/app.

Used by both installers (macOS .app and Windows Inno Setup) so they ship exactly
the same application files.  Only what the program reads is copied - no tests,
no docs, no CI files, no build leftovers.

    python installer/stage_payload.py <dest-dir>
"""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Folders and files the running application uses.
FOLDERS = ["core", "gui", "data", "assets", "manual_site"]
FILES = ["main.py", "environment.yml", "requirements-pip.txt", "VERSION"]


def _ignore(_dir, names):
    return [n for n in names
            if n == "__pycache__" or n.endswith((".pyc", ".pyo", ".DS_Store"))]


def stage(dest: Path) -> None:
    app = dest / "app"
    if app.exists():
        shutil.rmtree(app)
    app.mkdir(parents=True)
    for name in FOLDERS:
        src = ROOT / name
        if not src.is_dir():
            raise SystemExit(f"missing folder: {src}")
        shutil.copytree(src, app / name, ignore=_ignore)
    for name in FILES:
        src = ROOT / name
        if not src.is_file():
            raise SystemExit(f"missing file: {src}")
        shutil.copy2(src, app / name)
    n = sum(1 for p in app.rglob("*") if p.is_file())
    size = sum(p.stat().st_size for p in app.rglob("*") if p.is_file())
    print(f"staged {n} files ({size / 1e6:.0f} MB) into {app}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    stage(Path(sys.argv[1]).resolve())
