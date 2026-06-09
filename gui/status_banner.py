"""Cumulative progress panel for standalone modes.

A small QTextEdit driven by a state-machine: every `▶ Running [N/T]: …`
message creates an entry; the matching `✓ Done [N/T]: …` message *replaces*
that entry so the user sees one line per AOI that flips from "Running…" to
"Done" when the step finishes.

Orchestrators are expected to emit messages in one of these forms
(see core/orchestrate.py):

    ▶ Running [1/23]: 'test' ...
    ✓ Done    [1/23]: 'test' → case/subfolder/DEM_3DEP_test.tif
    All 23 AOI(s) processed successfully.
    Preparing …

Any message starting with one of those markers updates the banner; other
log lines are ignored here (they still go to the main log panel).
"""
import html
import re

from PyQt6.QtWidgets import QTextEdit
from PyQt6.QtGui import QFont


_FRAME_STYLE = (
    "QTextEdit { background:#fffbf0; border:2px solid #f6e05e; "
    "border-radius:6px; padding:8px; }"
)

# Match the step index at the start of a progress line
_STEP_RE = re.compile(r"^[▶✓]\s+\w+\s+\[(\d+)/(\d+)\]")


def make_status_banner() -> QTextEdit:
    box = QTextEdit()
    box.setReadOnly(True)
    box.setFont(QFont("Menlo", 11))
    box.setStyleSheet(_FRAME_STYLE)
    box.setMinimumHeight(120)
    box.setMaximumHeight(260)
    box.setVisible(False)
    # Per-widget state:
    #   box._banner_entries: list of dicts with {kind, step, html}
    box._banner_entries = []
    return box


def _render(box: QTextEdit):
    """Re-render the whole banner from box._banner_entries."""
    lines = []
    for e in box._banner_entries:
        lines.append(e["html"])
    box.setHtml("<br>".join(lines))
    # scroll to bottom
    sb = box.verticalScrollBar()
    sb.setValue(sb.maximum())


def set_starting(box: QTextEdit, n_features: int):
    box._banner_entries = [{
        "kind":  "intro",
        "step":  0,
        "html":  (
            f"<span style='color:#744210;'><b>Preparing to process "
            f"{n_features} AOI(s)…</b></span>"
        ),
    }]
    _render(box)
    box.setVisible(True)


def clear_banner(box: QTextEdit):
    """Wipe all entries and hide the banner — for re-entering a mode."""
    box._banner_entries = []
    box.clear()
    box.setVisible(False)


def update_banner(box: QTextEdit, msg: str) -> bool:
    """Handle one message from a worker.  Returns True if we updated the view."""
    if not hasattr(box, "_banner_entries"):
        box._banner_entries = []

    if msg.startswith("▶"):
        # Start of a step
        m = _STEP_RE.match(msg)
        step = int(m.group(1)) if m else None
        safe = html.escape(msg)
        entry = {
            "kind": "running",
            "step": step,
            "html": f"<span style='color:#2c5282;'><b>{safe}</b></span>",
        }
        box._banner_entries.append(entry)
        _render(box)
        return True

    if msg.startswith("✓"):
        # End of a step — replace the matching running entry, if any
        m = _STEP_RE.match(msg)
        step = int(m.group(1)) if m else None
        safe = html.escape(msg)
        done_html = f"<span style='color:#22543d;'>{safe}</span>"

        replaced = False
        if step is not None:
            for e in box._banner_entries:
                if e.get("kind") == "running" and e.get("step") == step:
                    e["kind"] = "done"
                    e["html"] = done_html
                    replaced = True
                    break
        if not replaced:
            box._banner_entries.append({
                "kind": "done", "step": step, "html": done_html,
            })
        _render(box)
        return True

    if msg.startswith(""):
        safe = html.escape(msg)
        box._banner_entries.append({
            "kind": "final",
            "step": None,
            "html": (
                f"<span style='color:#22543d; font-weight:bold; "
                f"font-size:13px;'>{safe}</span>"
            ),
        })
        _render(box)
        return True

    if msg.startswith(""):
        safe = html.escape(msg)
        box._banner_entries.append({
            "kind": "warn", "step": None,
            "html": f"<span style='color:#9b2c2c;'>{safe}</span>",
        })
        _render(box)
        return True

    return False
