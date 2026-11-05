"""FIMsim's version number, read from the VERSION file at the top of the repository.

The release build checks that the git tag (vX.Y.Z) matches this file, so the
number shown in the app is the number of the release it came from.
"""
from pathlib import Path

try:
    APP_VERSION = (Path(__file__).resolve().parent.parent / "VERSION").read_text().strip()
except OSError:
    APP_VERSION = ""

# "v1.2.0", or "" if the file is missing.
VERSION_LABEL = f"v{APP_VERSION}" if APP_VERSION else ""
