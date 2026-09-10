"""The credentials FIMsim remembers, owned by the backend rather than a panel.

The NWM API key lives here because the RUN needs it, not the GUI.  It used to be
read inside the Streamflow panel and carried in that step's config; anything
that captured the config while the panel had not yet seen a key — a project
saved before the key was entered, or a key written by another process after the
app started — produced a run whose JSON said ``"nwm_api_key": null``, and
NenCarta refuses that before it does any work at all:

    ValueError: Watershed 'T2_04' requires 'nwm_api_key' when
                'streamflow_source' is NWM.

Reading the key here, at the moment the JSON is written, removes that whole
class of failure: it does not matter when or where it was entered.

The store is the platform's own (QSettings — ``com.sdml.FIMsim`` on macOS), so
the key never reaches the project folder or the repository.
"""
from __future__ import annotations

from typing import Optional

NWM_KEY = "nwm_api_key"
RP_URL = "https://nwm-api.ciroh.org/return-period"


def _settings():
    from PyQt6.QtCore import QSettings
    return QSettings("SDML", "FIMsim")


def load_nwm_api_key() -> str:
    """The saved CIROH key, or "" if there is none."""
    try:
        s = _settings()
        # Another process (or another FIMsim window) may have saved the key
        # after this one started.  Without sync() Qt can answer from the copy
        # it read at startup and report no key when one exists.
        s.sync()
        return str(s.value(NWM_KEY, "") or "").strip()
    except Exception:
        return ""


def save_nwm_api_key(key: str) -> bool:
    key = (key or "").strip()
    if not key:
        return False
    try:
        s = _settings()
        s.setValue(NWM_KEY, key)
        s.sync()
        return True
    except Exception:
        return False


def clear_nwm_api_key() -> None:
    try:
        s = _settings()
        s.remove(NWM_KEY)
        s.sync()
    except Exception:
        pass


def check_nwm_api_key(key: str, comid: int = 7226582,
                      timeout: int = 30) -> Optional[str]:
    """Ask CIROH for one return period.  Returns None if the key works.

    A key that is merely *present* is not enough — NenCarta calls this endpoint
    for the rp2/rp100 fields the bathymetry step needs, so a wrong or expired
    key fails part way into a run.  One cheap request up front turns that into
    an error in the Streamflow step instead.
    """
    if not key:
        return "no key"
    try:
        import requests
        r = requests.get(RP_URL,
                         params={"comids": str(int(comid)),
                                 "output_format": "csv",
                                 "order_by_comid": False},
                         headers={"x-api-key": key}, timeout=timeout)
    except Exception as exc:                       # network, DNS, timeout …
        return f"could not reach {RP_URL} ({type(exc).__name__})"
    if r.status_code == 200:
        return None
    if r.status_code in (401, 403):
        return "the CIROH API rejected this key (HTTP "f"{r.status_code})"
    return f"HTTP {r.status_code} from {RP_URL}"
