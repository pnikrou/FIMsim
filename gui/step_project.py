"""Step 1 — Create new project folder or open existing one."""
import json
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFileDialog, QGroupBox, QFormLayout,
    QRadioButton, QButtonGroup, QFrame, QMessageBox,
)
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont

from core.project import create_project
from gui.worker import Worker
from gui.run_button import set_running, set_ready


class StepProjectWidget(QWidget):
    step_completed = pyqtSignal(dict)

    def __init__(self, log_fn, model: str = "lisflood", parent=None):
        """
        model: "lisflood" → creates lisflood_files/ subfolder
               "triton"   → creates triton_files/ subfolder
               "generic"  → no model-specific subfolder (used by DEM/LULC/HEC-RAS modes)
        """
        super().__init__(parent)
        self._log = log_fn
        self._model = model.lower()
        if self._model == "generic":
            self._subdir_name = None             # no extra subfolder
            self._ctx_key = "model_dir"          # generic key
        else:
            self._subdir_name = f"{self._model}_files"
            self._ctx_key = f"{self._model}_dir"
        self._worker = None
        self._setup_ui()

    def set_context(self, ctx_path, ctx):
        pass  # Step 1 is self-contained

    def reset(self):
        """Wipe the form back to its initial state — used when the user
        re-enters a mode from the main page and we want a fresh start."""
        # Default to "New project"
        if hasattr(self, "_rb_new"):
            self._rb_new.setChecked(True)
        if hasattr(self, "_new_gb"):
            self._new_gb.setVisible(True)
        if hasattr(self, "_exist_gb"):
            self._exist_gb.setVisible(False)
        # Clear text inputs
        if hasattr(self, "_proj_name_edit"):
            self._proj_name_edit.clear()
        if hasattr(self, "_exist_dir_edit"):
            self._exist_dir_edit.clear()
        # Reset the base-dir back to the user's home/Documents (the default)
        if hasattr(self, "_base_dir_edit"):
            from pathlib import Path as _P
            self._base_dir_edit.setText(str(_P.home() / "Documents"))
        # Hide error / success banners from any previous run
        if hasattr(self, "_error_lbl"):
            self._error_lbl.setVisible(False)
            self._error_lbl.setText("")
        if hasattr(self, "_report"):
            self._report.setVisible(False)
            self._report.setText("")
        # Make sure the run button is re-enabled and back to its ready style
        try:
            from gui.run_button import set_ready
            set_ready(self._run_btn)
        except Exception:
            self._run_btn.setEnabled(True)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # ── Mode selector — plain radio buttons, no frame ────────────────────
        self._options_widget = QWidget()
        mode_row = QHBoxLayout(self._options_widget)
        mode_row.setContentsMargins(0, 0, 0, 4)
        self._rb_new      = QRadioButton("New project")
        self._rb_existing = QRadioButton("Open existing project")
        self._rb_new.setChecked(True)
        bg = QButtonGroup(self)
        bg.addButton(self._rb_new, 0)
        bg.addButton(self._rb_existing, 1)
        mode_row.addWidget(self._rb_new)
        mode_row.addWidget(self._rb_existing)
        mode_row.addStretch()
        layout.addWidget(self._options_widget)

        # ── New project panel — no frame, no title ────────────────────────
        self._new_gb = QWidget()
        new_form = QFormLayout(self._new_gb)
        new_form.setContentsMargins(0, 0, 0, 0)

        base_row = QHBoxLayout()
        self._base_dir_edit = QLineEdit(str(Path.home() / "Documents"))
        browse_base = QPushButton("Browse…")
        browse_base.setFixedWidth(80)
        browse_base.clicked.connect(self._browse_base_dir)
        base_row.addWidget(self._base_dir_edit)
        base_row.addWidget(browse_base)
        new_form.addRow("Base directory:", base_row)

        self._proj_name_edit = QLineEdit()
        self._proj_name_edit.setPlaceholderText("e.g. MyFloodProject")
        new_form.addRow("Project name:", self._proj_name_edit)
        layout.addWidget(self._new_gb)

        # ── Existing project panel — no frame, no title ───────────────────
        self._exist_gb = QWidget()
        exist_form = QFormLayout(self._exist_gb)
        exist_form.setContentsMargins(0, 0, 0, 0)

        exist_row = QHBoxLayout()
        self._exist_dir_edit = QLineEdit()
        self._exist_dir_edit.setPlaceholderText("Select your existing project folder")
        browse_exist = QPushButton("Browse…")
        browse_exist.setFixedWidth(80)
        browse_exist.clicked.connect(self._browse_existing)
        exist_row.addWidget(self._exist_dir_edit)
        exist_row.addWidget(browse_exist)
        exist_form.addRow("Project folder:", exist_row)

        if self._subdir_name:
            note = QLabel(
                f"<small><i>A <b>{self._subdir_name}</b> subfolder will be created inside it "
                "(or kept if it already exists).</i></small>"
            )
            exist_form.addRow(note)
        layout.addWidget(self._exist_gb)
        self._exist_gb.setVisible(False)

        self._rb_existing.toggled.connect(self._toggle_mode)

        # ── Run button ────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._run_btn = QPushButton("Create / Open Project")
        self._run_btn.setStyleSheet(
            "font-weight:bold; padding:7px 20px; background:#2b6cb0; color:white; border-radius:4px;"
        )
        btn_row.addWidget(self._run_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # ── Inline error label ────────────────────────────────────────────
        self._error_lbl = QLabel("")
        self._error_lbl.setWordWrap(True)
        self._error_lbl.setStyleSheet(
            "padding:8px 10px; background:#fff5f5; border:1px solid #fc8181; "
            "border-radius:4px; font-size:12px; color:#c53030;"
        )
        self._error_lbl.setVisible(False)
        layout.addWidget(self._error_lbl)

        # ── Result report ─────────────────────────────────────────────────
        self._report = QLabel("")
        self._report.setWordWrap(True)
        self._report.setStyleSheet(
            "padding:10px; background:#f0fff4; border:1px solid #9ae6b4; "
            "border-radius:4px; font-size:12px;"
        )
        self._report.setVisible(False)
        layout.addWidget(self._report)
        layout.addStretch()

        self._run_btn.clicked.connect(self._run_step)

    def _toggle_mode(self, existing_checked):
        self._new_gb.setVisible(not existing_checked)
        self._exist_gb.setVisible(existing_checked)

    def _browse_base_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Select base directory",
                                             self._base_dir_edit.text())
        if d:
            self._base_dir_edit.setText(d)

    def _browse_existing(self):
        d = QFileDialog.getExistingDirectory(self, "Select existing project folder")
        if d:
            self._exist_dir_edit.setText(d)

    def _show_error(self, msg: str):
        self._error_lbl.setText(f" {msg}")
        self._error_lbl.setVisible(True)
        self._report.setVisible(False)

    def _run_step(self):
        self._error_lbl.setVisible(False)
        self._report.setVisible(False)
        if self._rb_existing.isChecked():
            self._open_existing()
        else:
            self._create_new()

    def _create_new(self):
        base_dir     = self._base_dir_edit.text().strip()
        project_name = self._proj_name_edit.text().strip()
        if not project_name:
            self._show_error("Please enter a project name.")
            return
        if not base_dir:
            self._show_error("Please select a base directory.")
            return

        # Overwrite check — warn if folder already exists
        target = Path(base_dir) / project_name
        if target.exists():
            ctx_exists = (target / "workflow_context.json").exists()
            detail = (
                f"The folder <b>{target}</b> already exists"
                + (" and contains a workflow_context.json." if ctx_exists else ".")
            )
            ans = QMessageBox.question(
                self, "Folder already exists",
                f"{detail}<br><br>Do you want to overwrite it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return

        set_running(self._run_btn)
        self._worker = Worker(
            create_project,
            base_dir=base_dir,
            project_name=project_name,
            subdir_name=self._subdir_name,   # None for generic mode
        )
        self._worker.message.connect(self._log)
        self._worker.finished.connect(self._on_created)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _open_existing(self):
        raw = self._exist_dir_edit.text().strip()
        if not raw:
            self._show_error("Please select a project folder first.")
            return
        folder = Path(raw)
        if not folder.is_dir():
            self._show_error(f"Folder not found: {raw}")
            return

        # Create the model subfolder (skip when no subdir is configured, e.g. generic mode)
        if self._subdir_name:
            model_dir = folder / self._subdir_name
            model_dir.mkdir(parents=True, exist_ok=True)
        else:
            model_dir = folder   # generic — project_dir IS the model_dir

        ctx_file = folder / "workflow_context.json"
        project_name = folder.name
        ctx = None

        # If a context file already exists, load and patch paths
        if ctx_file.exists():
            try:
                with open(ctx_file, "r", encoding="utf-8") as f:
                    ctx = json.load(f)
                ctx["project_dir"]      = str(folder)
                ctx[self._ctx_key]      = str(model_dir)
                ctx["model_dir"]        = str(model_dir)
                project_name = ctx.get("project_name", folder.name)
                self._log(f"Loaded existing workflow_context.json from: {folder}")
            except Exception as e:
                self._log(f"Warning: could not read existing context ({e}) — creating fresh one.")
                ctx = None

        # No valid context found — build a fresh one
        if ctx is None:
            ctx = self._build_fresh_ctx(folder, model_dir, project_name)

        # Save / update context on disk
        try:
            with open(ctx_file, "w", encoding="utf-8") as f:
                json.dump(ctx, f, indent=2)
        except Exception as e:
            self._log(f"ERROR saving workflow_context.json: {e}")
            return

        self._log(f"Project folder : {folder}")
        self._log(f"Model files folder: {model_dir}")
        self._show_report(ctx, new=False)
        self.step_completed.emit({"ctx_path": str(ctx_file), "ctx": ctx})

    def _build_fresh_ctx(self, folder, model_dir, project_name):
        """Build a fresh context dict for either LISFLOOD or TRITON."""
        base = {
            "base_dir":       str(folder.parent),
            "project_name":   project_name,
            "project_dir":    str(folder),
            self._ctx_key:    str(model_dir),
            "model_dir":      str(model_dir),
            "aoi_path":       None,
            "aoi_name":       None,
            "dem_path":       None,
            "dem_tif_path":   None,
        }
        if self._model == "lisflood":
            base.update({
                "lulc_path":            None,
                "manning_tif_path":     None,
                "dem_ascii_path":       str(model_dir / "dem.ascii"),
                "manning_ascii_path":   str(model_dir / "lulc.ascii"),
                "bci_path":             str(model_dir / "BC.bci"),
                "bdy_path":             str(model_dir / "BC.bdy"),
                "par_path":             str(model_dir / f"{project_name}.par"),
                "par_dem_name":         "dem.ascii",
                "par_manningfile_name": "lulc.ascii",
            })
        else:  # triton
            base.update({
                "dem_ascii_path":       str(model_dir / "dem.asc"),
                "triton_friction_path": None,
                "triton_extbc_path":    str(model_dir / "extbc.txt"),
                "triton_hydro_path":    str(model_dir / "upstream_hydrograph.txt"),
                "triton_cfg_path":      str(model_dir / f"{project_name}.cfg"),
            })
        return base

    def _on_created(self, result):
        ctx_path, ctx = result
        # Patch the returned context to also carry model_dir / model-specific dir key
        if self._subdir_name:
            model_dir_path = Path(ctx.get("project_dir", "")) / self._subdir_name
            ctx[self._ctx_key] = str(model_dir_path)
            ctx["model_dir"]   = str(model_dir_path)
            model_dir_path.mkdir(parents=True, exist_ok=True)
        else:
            # Generic mode — project_dir IS the model_dir
            model_dir_path = Path(ctx.get("project_dir", ""))
            ctx["model_dir"] = str(model_dir_path)
        # If TRITON, add TRITON-specific keys that core/project.py doesn't know about
        if self._model == "triton":
            project_name = ctx.get("project_name", "project")
            ctx.setdefault("dem_ascii_path",       str(model_dir_path / "dem.asc"))
            ctx.setdefault("triton_friction_path", None)
            ctx.setdefault("triton_extbc_path",    str(model_dir_path / "extbc.txt"))
            ctx.setdefault("triton_hydro_path",    str(model_dir_path / "upstream_hydrograph.txt"))
            ctx.setdefault("triton_cfg_path",      str(model_dir_path / f"{project_name}.cfg"))
        import json
        with open(ctx_path, "w", encoding="utf-8") as f:
            json.dump(ctx, f, indent=2)
        set_ready(self._run_btn)
        self._show_report(ctx, new=True)
        self.step_completed.emit({"ctx_path": str(ctx_path), "ctx": ctx})

    def _on_error(self, msg):
        self._log(f"ERROR: {msg}")
        set_ready(self._run_btn)

    def _show_report(self, ctx, new: bool):
        action = "created" if new else "selected"
        model_label = "LISFLOOD files" if self._model == "lisflood" else "TRITON files"
        model_dir_val = ctx.get(self._ctx_key, ctx.get("model_dir", ""))
        html = (
            f"<b>Project folder successfully {action}.</b><br><br>"
            f"<b>Project name:</b> {ctx.get('project_name', '')}<br>"
            f"<b>Project folder:</b> {ctx.get('project_dir', '')}<br>"
            f"<b>{model_label} folder:</b> {model_dir_val}"
        )
        self._report.setText(html)
        self._report.setVisible(True)
