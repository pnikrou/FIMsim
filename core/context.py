"""Workflow context helpers — load/save the JSON state file."""
import json
from pathlib import Path


def load_context(project_dir: str):
    """Load workflow_context.json from *project_dir*.

    Returns (ctx_path, ctx_dict).
    Raises FileNotFoundError if the file does not exist.
    """
    p = Path(project_dir) / "workflow_context.json"
    if not p.exists():
        raise FileNotFoundError(f"workflow_context.json not found in {project_dir}")
    with open(p, "r", encoding="utf-8") as f:
        ctx = json.load(f)
    return p, ctx


def save_context(ctx_path, ctx: dict):
    """Overwrite *ctx_path* with the updated context dict."""
    with open(Path(ctx_path), "w", encoding="utf-8") as f:
        json.dump(ctx, f, indent=2)
