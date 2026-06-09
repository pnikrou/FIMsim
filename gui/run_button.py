"""Reusable run-button styling helpers.

Call set_running(btn) before launching a worker, and set_ready(btn) when
done or on error.  The button text changes to "Working…" with a yellow
background so the user sees it was pressed.
"""

_READY_STYLE = (
    "font-weight:bold; padding:7px 20px; "
    "background:#2b6cb0; color:white; border-radius:4px;"
)
_RUNNING_STYLE = (
    "font-weight:bold; padding:7px 20px; "
    "background:#d69e2e; color:white; border-radius:4px;"
)


def set_running(btn, text="Working…"):
    """Mark button as 'working' — disable + change colour + text."""
    btn._original_text = btn.text()
    btn.setText(text)
    btn.setStyleSheet(_RUNNING_STYLE)
    btn.setEnabled(False)


def set_ready(btn, text=None):
    """Restore button to its ready state."""
    btn.setText(text or getattr(btn, "_original_text", "Run"))
    btn.setStyleSheet(_READY_STYLE)
    btn.setEnabled(True)
