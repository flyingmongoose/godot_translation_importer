#!/usr/bin/env python3
"""Build native binaries with PyInstaller."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def _run(cmd: list[str], cwd: Path) -> None:
    completed = subprocess.run(cmd, cwd=str(cwd))
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def _is_valid_icon(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        from PIL import Image  # type: ignore

        with Image.open(path) as img:
            img.verify()
        return True
    except Exception:
        return False


def _build(
    repo_root: Path,
    entrypoint: str,
    name: str,
    onefile: bool,
    windowed: bool,
    icon_path: Path | None,
) -> None:
    sep = ";" if os.name == "nt" else ":"
    icon_assets_src = (repo_root / "assets" / "icon").resolve()
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name",
        name,
        "--distpath",
        "dist",
        "--workpath",
        "build",
        "--specpath",
        "build/spec",
        "--add-data",
        f"{icon_assets_src}{sep}assets/icon",
    ]
    if onefile:
        cmd.append("--onefile")
    if windowed:
        cmd.append("--windowed")
    if icon_path is not None:
        cmd.extend(["--icon", str(icon_path)])
    cmd.append(entrypoint)
    _run(cmd, cwd=repo_root)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build native binaries with PyInstaller.")
    parser.add_argument(
        "--target",
        choices=("gui", "cli", "all"),
        default="all",
        help="Which targets to build.",
    )
    parser.add_argument(
        "--onefile",
        action="store_true",
        help="Build single-file executables instead of one-folder bundles.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    icon_dir = repo_root / "assets" / "icon"
    icon_path: Path | None = None
    platform_candidates: list[Path]
    if sys.platform == "darwin":
        platform_candidates = [icon_dir / "icon.icns", icon_dir / "icon-256.png"]
    elif os.name == "nt":
        platform_candidates = [icon_dir / "icon.ico", icon_dir / "icon-256.png"]
    else:
        platform_candidates = [icon_dir / "icon-256.png", icon_dir / "icon.ico"]

    for candidate in platform_candidates:
        if _is_valid_icon(candidate):
            icon_path = candidate
            break

    if icon_path is None:
        print(
            "[build] No valid icon file detected; continuing without --icon. "
            "Check assets/icon files."
        )
    else:
        print(f"[build] Using icon: {icon_path}")

    gui_onefile = args.onefile
    if sys.platform == "darwin" and gui_onefile:
        # macOS windowed apps are .app bundles; onefile mode is deprecated.
        print(
            "[build] macOS GUI build ignores --onefile and uses onedir "
            "to avoid PyInstaller bundle issues."
        )
        gui_onefile = False

    if args.target in ("gui", "all"):
        _build(
            repo_root=repo_root,
            entrypoint="godot_translation_import_gui.py",
            name="godot-translation-import-gui",
            onefile=gui_onefile,
            windowed=True,
            icon_path=icon_path,
        )

    if args.target in ("cli", "all"):
        _build(
            repo_root=repo_root,
            entrypoint="godot_translation_import.py",
            name="godot-translation-import-cli",
            onefile=args.onefile,
            windowed=False,
            icon_path=icon_path,
        )

    print("Build complete. See dist/ directory.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
