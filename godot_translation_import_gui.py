#!/usr/bin/env python3
"""Tabbed Tkinter UI for a generic Godot gettext import/audit tool."""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from godot_translation_import import (
    AuditSummary,
    MergeSummary,
    audit_catalogs,
    get_missing_gettext_tools,
    merge_catalogs,
)


class MergeApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Godot Translation Import Tool")
        self.root.geometry("980x720")
        self._icon_image: tk.PhotoImage | None = None
        self.style = ttk.Style(self.root)
        self._configure_progress_styles()
        self._configure_window_icon()

        self.source_po_dir_var = tk.StringVar()
        self.target_po_dir_var = tk.StringVar()
        self.target_pot_var = tk.StringVar()
        self.source_pot_var = tk.StringVar()
        self.project_root_var = tk.StringVar()
        self.config_path_var = tk.StringVar()

        self.merge_status_var = tk.StringVar(value="Ready.")
        self.merge_current_file_var = tk.StringVar(value="Current file: -")
        self.audit_status_var = tk.StringVar(value="Ready.")
        self.audit_current_phase_var = tk.StringVar(value="Current scan: -")
        self.respect_gitignore_var = tk.BooleanVar(value=True)
        self._updating_vars = False

        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.is_running = False

        missing_tools = get_missing_gettext_tools()
        if missing_tools:
            missing = ", ".join(missing_tools)
            messagebox.showerror(
                "Missing gettext tools",
                (
                    "Required GNU gettext tools were not found on PATH.\n\n"
                    f"Missing: {missing}\n\n"
                    "Install gettext and restart the application."
                ),
            )
            self.root.after(0, self.root.destroy)
            return

        self._build_ui()
        self._attach_var_traces()
        self._initialize_default_paths()
        self._try_autoload_startup_config()
        self._refresh_config_preview()
        self.root.after(60, self._pump_events)

    def _configure_progress_styles(self) -> None:
        # Render percentage text inside progress bars.
        base_layout = [
            (
                "Horizontal.Progressbar.trough",
                {
                    "children": [
                        ("Horizontal.Progressbar.pbar", {"side": "left", "sticky": "ns"})
                    ],
                    "sticky": "nswe",
                },
            ),
            ("Horizontal.Progressbar.label", {"sticky": ""}),
        ]
        self.style.layout("MergeText.Horizontal.TProgressbar", base_layout)
        self.style.layout("AuditText.Horizontal.TProgressbar", base_layout)
        self.style.configure(
            "MergeText.Horizontal.TProgressbar",
            text="0%",
            anchor="center",
            foreground="black",
        )
        self.style.configure(
            "AuditText.Horizontal.TProgressbar",
            text="0%",
            anchor="center",
            foreground="black",
        )

    def _configure_window_icon(self) -> None:
        """Load a PNG app icon when available.

        Place icon assets at:
        - ./assets/icon/icon-256.png (used on all platforms)
        - ./assets/icon/icon.ico (optional Windows fallback)
        """
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            base_dir = Path(getattr(sys, "_MEIPASS"))
        else:
            base_dir = Path(__file__).resolve().parent
        icon_dir = base_dir / "assets" / "icon"
        png_path = icon_dir / "icon-256.png"
        if png_path.is_file():
            try:
                self._icon_image = tk.PhotoImage(file=str(png_path))
                self.root.iconphoto(True, self._icon_image)
            except tk.TclError:
                pass
        if os.name == "nt":
            ico_path = icon_dir / "icon.ico"
            if ico_path.is_file():
                try:
                    self.root.iconbitmap(str(ico_path))
                except tk.TclError:
                    pass

    def _set_progress_style_text(self, style_name: str, percentage: int) -> None:
        text_color = "white" if percentage >= 50 else "black"
        self.style.configure(style_name, text=f"{percentage}%", foreground=text_color)

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)

        notebook = ttk.Notebook(frame)
        notebook.pack(fill=tk.BOTH, expand=True)

        self.config_tab = ttk.Frame(notebook)
        self.merge_tab = ttk.Frame(notebook)
        self.audit_tab = ttk.Frame(notebook)
        self.help_tab = ttk.Frame(notebook)
        notebook.add(self.config_tab, text="Config")
        notebook.add(self.merge_tab, text="Merge")
        notebook.add(self.audit_tab, text="Audit")
        notebook.add(self.help_tab, text="Help / Usage")

        self._build_merge_tab()
        self._build_audit_tab()
        self._build_config_tab()
        self._build_help_tab()

    def _make_path_row(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        var: tk.StringVar,
        ask_title: str,
        select_file: bool = False,
        filetypes: list[tuple[str, str]] | None = None,
        initialdir_getter: Callable[[], str] | None = None,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w")
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="ew", padx=8)

        def browse() -> None:
            initialdir = ""
            if initialdir_getter is not None:
                initialdir = initialdir_getter()
            if select_file:
                selected = filedialog.askopenfilename(
                    title=ask_title,
                    filetypes=filetypes if filetypes else [("All files", "*.*")],
                    initialdir=initialdir,
                )
            else:
                selected = filedialog.askdirectory(title=ask_title, initialdir=initialdir)
            if selected:
                var.set(selected)

        ttk.Button(parent, text="Browse...", command=browse).grid(row=row, column=2, sticky="e")

    def _build_merge_tab(self) -> None:
        paths = ttk.LabelFrame(self.merge_tab, text="Merge Configuration (read from Config tab)", padding=10)
        paths.pack(fill=tk.X, padx=8, pady=8)
        paths.columnconfigure(1, weight=1)

        ttk.Label(paths, text="Source translation directory:").grid(row=0, column=0, sticky="w")
        ttk.Entry(paths, textvariable=self.source_po_dir_var, state="readonly").grid(
            row=0, column=1, sticky="ew", padx=8
        )
        ttk.Label(paths, text="Target translation directory:").grid(row=1, column=0, sticky="w")
        ttk.Entry(paths, textvariable=self.target_po_dir_var, state="readonly").grid(
            row=1, column=1, sticky="ew", padx=8
        )
        ttk.Label(paths, text="Target POT file:").grid(row=2, column=0, sticky="w")
        ttk.Entry(paths, textvariable=self.target_pot_var, state="readonly").grid(
            row=2, column=1, sticky="ew", padx=8
        )

        actions = ttk.Frame(self.merge_tab)
        actions.pack(fill=tk.X, padx=8, pady=(0, 8))
        self.merge_start_btn = ttk.Button(actions, text="Start Merge", command=self._start_merge)
        self.merge_start_btn.pack(side=tk.LEFT)

        prog = ttk.LabelFrame(self.merge_tab, text="Progress", padding=10)
        prog.pack(fill=tk.X, padx=8, pady=(0, 8))
        self.merge_progress = ttk.Progressbar(
            prog,
            mode="determinate",
            maximum=100,
            style="MergeText.Horizontal.TProgressbar",
        )
        self.merge_progress.pack(fill=tk.X)
        ttk.Label(prog, textvariable=self.merge_current_file_var).pack(anchor="w", pady=(6, 0))
        ttk.Label(prog, textvariable=self.merge_status_var).pack(anchor="w", pady=(2, 0))

        logs = ttk.LabelFrame(self.merge_tab, text="Console", padding=10)
        logs.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self.merge_log = tk.Text(logs, wrap="word", state=tk.DISABLED)
        self.merge_log.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
        sb = ttk.Scrollbar(logs, orient="vertical", command=self.merge_log.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.merge_log.configure(yscrollcommand=sb.set)

    def _build_audit_tab(self) -> None:
        paths = ttk.LabelFrame(self.audit_tab, text="Audit Configuration (read from Config tab)", padding=10)
        paths.pack(fill=tk.X, padx=8, pady=8)
        paths.columnconfigure(1, weight=1)

        ttk.Label(paths, text="Source POT file (optional for comparison):").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Entry(paths, textvariable=self.source_pot_var, state="readonly").grid(
            row=0, column=1, sticky="ew", padx=8
        )
        ttk.Label(paths, text="Godot project root (required):").grid(row=1, column=0, sticky="w")
        ttk.Entry(paths, textvariable=self.project_root_var, state="readonly").grid(
            row=1, column=1, sticky="ew", padx=8
        )
        ttk.Label(paths, text="Target translation directory (required):").grid(
            row=2, column=0, sticky="w"
        )
        ttk.Entry(paths, textvariable=self.target_po_dir_var, state="readonly").grid(
            row=2, column=1, sticky="ew", padx=8
        )
        ttk.Label(paths, text="Target POT file (required):").grid(row=3, column=0, sticky="w")
        ttk.Entry(paths, textvariable=self.target_pot_var, state="readonly").grid(
            row=3, column=1, sticky="ew", padx=8
        )

        actions = ttk.Frame(self.audit_tab)
        actions.pack(fill=tk.X, padx=8, pady=(0, 8))
        self.audit_start_btn = ttk.Button(actions, text="Run Audit", command=self._start_audit)
        self.audit_start_btn.pack(side=tk.LEFT)
        ttk.Checkbutton(
            actions,
            text="Respect .gitignore",
            variable=self.respect_gitignore_var,
        ).pack(side=tk.LEFT, padx=12)
        ttk.Label(actions, textvariable=self.audit_status_var).pack(side=tk.LEFT, padx=10)

        prog = ttk.LabelFrame(self.audit_tab, text="Progress", padding=10)
        prog.pack(fill=tk.X, padx=8, pady=(0, 8))
        self.audit_progress = ttk.Progressbar(
            prog,
            mode="determinate",
            maximum=100,
            style="AuditText.Horizontal.TProgressbar",
        )
        self.audit_progress.pack(fill=tk.X)
        ttk.Label(prog, textvariable=self.audit_current_phase_var).pack(anchor="w", pady=(6, 0))

        logs = ttk.LabelFrame(self.audit_tab, text="Audit Output", padding=10)
        logs.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self.audit_log = tk.Text(logs, wrap="word", state=tk.DISABLED)
        self.audit_log.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
        sb = ttk.Scrollbar(logs, orient="vertical", command=self.audit_log.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.audit_log.configure(yscrollcommand=sb.set)

    def _build_config_tab(self) -> None:
        sections = ttk.Notebook(self.config_tab)
        sections.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        ui_tab = ttk.Frame(sections)
        live_tab = ttk.Frame(sections)
        schema_tab = ttk.Frame(sections)
        sections.add(ui_tab, text="UI Configuration")
        sections.add(live_tab, text="Live Config")
        sections.add(schema_tab, text="Schema Example")

        box = ttk.LabelFrame(ui_tab, text="Paths and Options", padding=10)
        box.pack(fill=tk.X, padx=8, pady=8)
        box.columnconfigure(1, weight=1)
        self._make_path_row(
            box,
            0,
            "Source translation directory:",
            self.source_po_dir_var,
            "Select source translation directory",
        )
        self._make_path_row(
            box,
            1,
            "Godot project root:",
            self.project_root_var,
            "Select Godot project root",
        )
        self._make_path_row(
            box,
            2,
            "Target translation directory:",
            self.target_po_dir_var,
            "Select target translation directory",
            initialdir_getter=lambda: self.project_root_var.get().strip(),
        )
        self._make_path_row(
            box,
            3,
            "Target POT file:",
            self.target_pot_var,
            "Select target POT file",
            select_file=True,
            filetypes=[("Gettext template", "*.pot"), ("All files", "*.*")],
            initialdir_getter=lambda: self.target_po_dir_var.get().strip(),
        )
        self._make_path_row(
            box,
            4,
            "Source POT file (optional):",
            self.source_pot_var,
            "Select source POT file",
            select_file=True,
            filetypes=[("Gettext template", "*.pot"), ("All files", "*.*")],
            initialdir_getter=lambda: self.source_po_dir_var.get().strip(),
        )
        self._make_path_row(
            box,
            5,
            "Config JSON path:",
            self.config_path_var,
            "Select config JSON path",
            select_file=True,
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        ttk.Checkbutton(
            box,
            text="Respect .gitignore during audit scans",
            variable=self.respect_gitignore_var,
        ).grid(row=6, column=1, sticky="w", padx=8, pady=(6, 0))

        btns = ttk.Frame(ui_tab)
        btns.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Button(btns, text="Load Config", command=self._load_config_from_dialog).pack(side=tk.LEFT)
        ttk.Button(btns, text="Save Config", command=self._save_config).pack(side=tk.LEFT, padx=8)

        info = ttk.LabelFrame(schema_tab, text="Config Schema", padding=10)
        info.pack(fill=tk.BOTH, expand=True)
        text = tk.Text(info, wrap="word", height=16)
        text.insert(
            "1.0",
            "{\n"
            '  "source_po_dir": "/abs/path/to/source/translations",\n'
            '  "target_po_dir": "/abs/path/to/godot-project/locale",\n'
            '  "target_pot": "/abs/path/to/godot-project/locale/messages.pot",\n'
            '  "source_pot": "/abs/path/to/source/translations/messages.pot",\n'
            '  "project_root": "/abs/path/to/godot-project",\n'
            '  "respect_gitignore": true\n'
            "}\n"
        )
        text.configure(state=tk.DISABLED)
        text.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
        schema_sb = ttk.Scrollbar(info, orient="vertical", command=text.yview)
        schema_sb.pack(side=tk.RIGHT, fill=tk.Y)
        text.configure(yscrollcommand=schema_sb.set)

        live = ttk.LabelFrame(live_tab, text="Live Config (in-memory)", padding=10)
        live.pack(fill=tk.BOTH, expand=True)
        self.config_preview = tk.Text(live, wrap="word", height=18, state=tk.DISABLED)
        self.config_preview.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
        live_sb = ttk.Scrollbar(live, orient="vertical", command=self.config_preview.yview)
        live_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.config_preview.configure(yscrollcommand=live_sb.set)

    def _attach_var_traces(self) -> None:
        watched_vars = [
            self.source_po_dir_var,
            self.target_po_dir_var,
            self.target_pot_var,
            self.source_pot_var,
            self.project_root_var,
            self.config_path_var,
            self.respect_gitignore_var,
        ]
        for var in watched_vars:
            var.trace_add("write", self._on_form_var_changed)

    def _initialize_default_paths(self) -> None:
        script_dir = Path(__file__).resolve().parent
        if not self.config_path_var.get().strip():
            self.config_path_var.set(str((script_dir / "i18n-config.json").resolve()))

    def _try_autoload_startup_config(self) -> None:
        cfg = Path(self.config_path_var.get().strip()) if self.config_path_var.get().strip() else None
        if cfg is not None and cfg.is_file():
            self._load_config_from_path(cfg, show_message=False)

    def _on_form_var_changed(self, *_args: object) -> None:
        if self._updating_vars:
            return
        self._refresh_config_preview()

    def _current_config_dict(self) -> dict:
        return {
            "source_po_dir": self.source_po_dir_var.get().strip(),
            "target_po_dir": self.target_po_dir_var.get().strip(),
            "target_pot": self.target_pot_var.get().strip(),
            "source_pot": self.source_pot_var.get().strip(),
            "project_root": self.project_root_var.get().strip(),
            "respect_gitignore": self.respect_gitignore_var.get(),
        }

    def _refresh_config_preview(self) -> None:
        if not hasattr(self, "config_preview"):
            return
        rendered = json.dumps(self._current_config_dict(), indent=2) + "\n"
        self.config_preview.configure(state=tk.NORMAL)
        self.config_preview.delete("1.0", tk.END)
        self.config_preview.insert("1.0", rendered)
        self.config_preview.configure(state=tk.DISABLED)

    def _build_help_tab(self) -> None:
        frame = ttk.Frame(self.help_tab, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        help_text = tk.Text(frame, wrap="word")
        help_text.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
        sb = ttk.Scrollbar(frame, orient="vertical", command=help_text.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        help_text.configure(yscrollcommand=sb.set)

        help_text.insert(
            "1.0",
            "Quick start\n"
            "===========\n"
            "1) Set source translation directory (.po source).\n"
            "2) Set target translation directory (.po target, often res://locale on Godot).\n"
            "3) Set target POT path (often res://locale/messages.pot).\n"
            "4) Optionally set source POT and project root for audit.\n"
            "5) Run Merge, then run Audit.\n"
        )
        help_text.configure(state=tk.DISABLED)

    def _append_log(self, widget: tk.Text, line: str) -> None:
        widget.configure(state=tk.NORMAL)
        widget.insert(tk.END, line + "\n")
        widget.see(tk.END)
        widget.configure(state=tk.DISABLED)

    def _set_running(self, running: bool) -> None:
        self.is_running = running
        state = tk.DISABLED if running else tk.NORMAL
        self.merge_start_btn.configure(state=state)
        self.audit_start_btn.configure(state=state)

    def _validate_merge_paths(self) -> tuple[Path, Path, Path]:
        return (
            Path(self.source_po_dir_var.get()).resolve(),
            Path(self.target_po_dir_var.get()).resolve(),
            Path(self.target_pot_var.get()).resolve(),
        )

    def _start_merge(self) -> None:
        if self.is_running:
            return
        try:
            source_po_dir, target_po_dir, target_pot = self._validate_merge_paths()
        except Exception:
            messagebox.showerror("Invalid path", "Please provide valid merge paths.")
            return
        if not source_po_dir.is_dir():
            messagebox.showerror("Invalid path", f"Missing source folder:\n{source_po_dir}")
            return
        if not target_po_dir.is_dir():
            messagebox.showerror("Invalid path", f"Missing target folder:\n{target_po_dir}")
            return
        if not target_pot.is_file():
            messagebox.showerror("Missing POT", f"Missing POT file:\n{target_pot}")
            return

        self.merge_log.configure(state=tk.NORMAL)
        self.merge_log.delete("1.0", tk.END)
        self.merge_log.configure(state=tk.DISABLED)
        self.merge_progress.configure(value=0, maximum=100)
        self._set_progress_style_text("MergeText.Horizontal.TProgressbar", 0)
        self.merge_current_file_var.set("Current file: -")
        self.merge_status_var.set("Running merge...")
        self._set_running(True)

        threading.Thread(
            target=self._run_merge_worker,
            args=(source_po_dir, target_po_dir, target_pot),
            daemon=True,
        ).start()

    def _start_audit(self) -> None:
        if self.is_running:
            return
        target_pot = Path(self.target_pot_var.get()).resolve()
        source_pot = Path(self.source_pot_var.get()).resolve() if self.source_pot_var.get() else None
        project_root = (
            Path(self.project_root_var.get()).resolve()
            if self.project_root_var.get()
            else None
        )
        target_po_dir = (
            Path(self.target_po_dir_var.get()).resolve()
            if self.target_po_dir_var.get()
            else None
        )
        if not target_pot.is_file():
            messagebox.showerror("Missing POT", f"Missing target POT:\n{target_pot}")
            return
        if project_root is None or not project_root.is_dir():
            messagebox.showerror(
                "Missing Godot project root",
                "Set a valid Godot project root to run audit scans.",
            )
            return
        if target_po_dir is None or not target_po_dir.is_dir():
            messagebox.showerror(
                "Missing target translation directory",
                "Set a valid target translation directory for catalog usage scan.",
            )
            return
        self.audit_log.configure(state=tk.NORMAL)
        self.audit_log.delete("1.0", tk.END)
        self.audit_log.configure(state=tk.DISABLED)
        self.audit_progress.configure(value=0, maximum=100)
        self._set_progress_style_text("AuditText.Horizontal.TProgressbar", 0)
        self.audit_current_phase_var.set("Current scan: starting...")
        self.audit_status_var.set("Running audit...")
        self._set_running(True)
        threading.Thread(
            target=self._run_audit_worker,
            args=(
                target_pot,
                source_pot,
                project_root,
                target_po_dir,
                self.respect_gitignore_var.get(),
            ),
            daemon=True,
        ).start()

    def _run_merge_worker(self, source_po_dir: Path, target_po_dir: Path, target_pot: Path) -> None:
        try:
            summary = merge_catalogs(
                source_po_dir=source_po_dir,
                target_po_dir=target_po_dir,
                target_pot=target_pot,
                progress_cb=lambda i, total, lang, stage: self.events.put(
                    ("merge_progress", (i, total, lang, stage))
                ),
                log_cb=lambda line: self.events.put(("merge_log", line)),
            )
            self.events.put(("merge_done", summary))
        except Exception as exc:  # noqa: BLE001
            self.events.put(("merge_error", str(exc)))

    def _run_audit_worker(
        self,
        target_pot: Path,
        source_pot: Path | None,
        project_root: Path | None,
        target_po_dir: Path | None,
        respect_gitignore: bool,
    ) -> None:
        try:
            summary = audit_catalogs(
                target_pot=target_pot,
                source_pot=source_pot,
                project_root=project_root,
                target_po_dir=target_po_dir,
                deep_scan=True,
                respect_gitignore=respect_gitignore,
                log_cb=lambda line: self.events.put(("audit_log", line)),
                progress_cb=lambda phase, current, total: self.events.put(
                    ("audit_progress", (phase, current, total))
                ),
            )
            self.events.put(("audit_done", summary))
        except Exception as exc:  # noqa: BLE001
            self.events.put(("audit_error", str(exc)))

    def _load_config_from_dialog(self) -> None:
        selected = filedialog.askopenfilename(
            title="Select config JSON path",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialdir=str(Path(self.config_path_var.get()).expanduser().resolve().parent)
            if self.config_path_var.get().strip()
            else "",
        )
        if not selected:
            return
        self.config_path_var.set(selected)
        self._load_config_from_path(Path(selected).resolve(), show_message=True)

    def _load_config_from_path(self, cfg_path: Path, show_message: bool) -> None:
        if not cfg_path.is_file():
            if show_message:
                messagebox.showerror("Missing file", f"Config does not exist:\n{cfg_path}")
            return
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            if show_message:
                messagebox.showerror("Invalid JSON", str(exc))
            return

        self._updating_vars = True
        try:
            self.source_po_dir_var.set(data.get("source_po_dir", self.source_po_dir_var.get()))
            self.target_po_dir_var.set(data.get("target_po_dir", self.target_po_dir_var.get()))
            self.target_pot_var.set(data.get("target_pot", self.target_pot_var.get()))
            self.source_pot_var.set(data.get("source_pot", self.source_pot_var.get()))
            self.project_root_var.set(data.get("project_root", self.project_root_var.get()))
            self.respect_gitignore_var.set(
                bool(data.get("respect_gitignore", self.respect_gitignore_var.get()))
            )
        finally:
            self._updating_vars = False
        self._refresh_config_preview()

    def _save_config(self) -> None:
        selected = filedialog.asksaveasfilename(
            title="Save config JSON",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialdir=str(Path(self.config_path_var.get()).expanduser().resolve().parent)
            if self.config_path_var.get().strip()
            else "",
            initialfile=Path(self.config_path_var.get()).name
            if self.config_path_var.get().strip()
            else "i18n-config.json",
        )
        if not selected:
            return
        self.config_path_var.set(selected)
        cfg_path = Path(selected).resolve()
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps(self._current_config_dict(), indent=2) + "\n", encoding="utf-8")

    def _pump_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "merge_log":
                    self._append_log(self.merge_log, str(payload))
                elif kind == "merge_progress":
                    idx, total, lang, stage = payload  # type: ignore[misc]
                    percentage = int((idx / total) * 100)
                    self.merge_progress.configure(value=percentage, maximum=100)
                    self._set_progress_style_text("MergeText.Horizontal.TProgressbar", percentage)
                    self.merge_current_file_var.set(
                        f"Current file: {lang} ({stage}) [{idx}/{total}]"
                    )
                elif kind == "merge_done":
                    summary: MergeSummary = payload  # type: ignore[assignment]
                    self._set_running(False)
                    self.merge_progress.configure(value=100)
                    self._set_progress_style_text("MergeText.Horizontal.TProgressbar", 100)
                    self.merge_status_var.set(
                        f"Done. Merged {summary.merged_count}, "
                        f"Validated {summary.valid_count}, Failed {summary.failed_count}."
                    )
                elif kind == "merge_error":
                    self._set_running(False)
                    self.merge_status_var.set("Failed.")
                    messagebox.showerror("Merge failed", str(payload))
                elif kind == "audit_log":
                    self._append_log(self.audit_log, str(payload))
                elif kind == "audit_progress":
                    phase, current, total = payload  # type: ignore[misc]
                    percentage = int((current / total) * 100) if total > 0 else 0
                    self.audit_progress.configure(value=percentage, maximum=100)
                    self._set_progress_style_text("AuditText.Horizontal.TProgressbar", percentage)
                    labels = {
                        "tr_literals": "tr(...) literal scan",
                        "catalog_usage": "catalog usage scan",
                        "deep_scan": "deep candidate scan",
                    }
                    self.audit_current_phase_var.set(
                        f"Current scan: {labels.get(phase, str(phase))} [{current}/{total}]"
                    )
                elif kind == "audit_done":
                    summary: AuditSummary = payload  # type: ignore[assignment]
                    self._set_running(False)
                    self.audit_progress.configure(value=100)
                    self._set_progress_style_text("AuditText.Horizontal.TProgressbar", 100)
                    self.audit_current_phase_var.set("Current scan: complete")
                    self.audit_status_var.set(
                        "Done. "
                        f"Target={summary.godot_msgids}, Source={summary.fife_msgids}, "
                        f"Missing tr={summary.tr_literals_missing_from_pot}, "
                        f"Missing candidates={summary.source_candidates_missing_from_pot}, "
                        f"Raw literals w/o tr={summary.raw_gd_msgid_occurrences_without_tr}"
                    )
                elif kind == "audit_error":
                    self._set_running(False)
                    self.audit_status_var.set("Failed.")
                    messagebox.showerror("Audit failed", str(payload))
        except queue.Empty:
            pass
        finally:
            self.root.after(60, self._pump_events)


def main() -> int:
    root = tk.Tk()
    app = MergeApp(root)
    _ = app
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
