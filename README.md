# Godot Translation Import Tool

[![build](https://github.com/flyingmongoose/godot_translation_importer/actions/workflows/build.yml/badge.svg?branch=main)](https://github.com/flyingmongoose/godot_translation_importer/actions/workflows/build.yml?query=branch%3Amain)
[![Latest Release](https://img.shields.io/github/v/release/flyingmongoose/godot_translation_importer)](https://github.com/flyingmongoose/godot_translation_importer/releases)

A generic gettext merge + audit toolkit for Godot translation workflows.

It is designed around a common Godot layout where catalogs live under `res://locale` (filesystem: `<godot-project>/locale`).

## Files

- `godot_translation_import.py` - canonical CLI implementation
- `godot_translation_import_gui.py` - Tkinter GUI
- `godot_translation_import.sh` - Bash/Zsh/Fish wrapper
- `godot_translation_import.ps1` - PowerShell wrapper

## Prerequisites

- Python 3
- GNU gettext tools in PATH:
  - `msgmerge`
  - `msgfmt`

## Licensing and Third-Party Dependencies

- This tool's source code is licensed under the MIT License (see `LICENSE`).
- Runtime gettext tools (`msgmerge`, `msgfmt`) are third-party software and are
  not bundled by default in this repository.
- See `THIRD_PARTY_NOTICES.md` for dependency and redistribution notes.

## What It Does

### Merge mode

1. Reads source `.po` files from `--source-po-dir`.
2. Copies each language file to `--target-po-dir`.
3. Runs `msgmerge --update --backup=none <target.po> <target.pot>`.
4. Validates each merged file with `msgfmt --check`.
5. Prints per-file status (`[ok]` / `[fail]`) and summary totals.

### Audit mode

1. Compares `--target-pot` and optional `--source-pot`.
2. Runs project scans from `--project-root` for `tr(...)` / `tr_n(...)` coverage.
3. Scans likely hardcoded user-facing strings in supported project files.
4. Uses `--target-po-dir` to include existing PO msgids in catalog usage checks.
5. Suggests `.gd` locations where `tr(...)` may be missing.

Supported scan types: `.gd`, `.tscn`, `.res`, `.scn`, `.dae`, `.obj`, `.escn`, `.fbx`, `.gltf`, `.glb`, `.blend`.

Skipped by default: `.git`, `.godot`, `addons`, `.import`, `.cache`, `build`, `dist`.

Also respects `.gitignore` by default when available; disable with `--no-respect-gitignore`.

## CLI Usage

### Merge

```bash
python3 ./tools/i18n/godot_translation_import.py merge \
  --source-po-dir /path/to/source/translations \
  --target-po-dir /path/to/godot-project/locale \
  --target-pot /path/to/godot-project/locale/messages.pot
```

### Audit

```bash
python3 ./tools/i18n/godot_translation_import.py audit \
  --target-pot /path/to/godot-project/locale/messages.pot \
  --source-pot /path/to/source/translations/messages.pot \
  --project-root /path/to/godot-project \
  --target-po-dir /path/to/godot-project/locale
```

### Config file

```bash
python3 ./tools/i18n/godot_translation_import.py merge --config /path/to/i18n-config.json
```

## GUI Usage

```bash
python3 ./tools/i18n/godot_translation_import_gui.py
```

GUI tabs:

- `Config` (default): set all paths/options in `UI Configuration`, then load/save config
- `Merge`: read-only view of config values + run merge
- `Audit`: read-only view of config values + run audit
- `Help / Usage`: quick in-app usage notes

## Wrappers

### Bash/Zsh/Fish

```bash
./tools/i18n/godot_translation_import.sh
```

### PowerShell

```powershell
pwsh ./tools/i18n/godot_translation_import.ps1
```

Both wrappers default to `merge` if no subcommand is supplied.

## Native Builds (PyInstaller)

Install build dependency:

```bash
python -m pip install -r requirements-build.txt
```

Build native binaries:

```bash
python scripts/build_native.py --target all --onefile
```

Build options:

- `--target gui` for GUI only
- `--target cli` for CLI only
- `--target all` for both (default)
- omit `--onefile` for one-folder bundles

Platform note:

- On macOS, GUI builds are forced to `onedir` even when `--onefile` is passed (PyInstaller deprecates onefile `.app` bundles). CLI still supports onefile.

Output is written to `dist/`.

Default binary names:

- GUI: `godot-translation-import-gui`
- CLI/headless: `godot-translation-import-cli`

### CI builds

GitHub Actions workflow:

- `.github/workflows/build.yml`
- `.github/workflows/release.yml`

`build.yml` (build) runs for:

- manual workflow dispatch
- pushes to `main`
- pull requests

`release.yml` (release) is triggered by successful `build.yml` completions via
`workflow_run` and reuses the uploaded build artifacts from that exact run. It
publishes only when the build commit has a `v*` tag, then creates/updates a
GitHub Release and attaches compiled binaries from all three platforms without
rebuilding.

Linux compatibility note:

- PyInstaller binaries are not universally portable across every distro/libc combination.
- The CI Linux artifact targets the GitHub Actions Ubuntu runner baseline and is usually compatible with many modern glibc-based distros.
- For broadest coverage, publish additional Linux artifacts built on older glibc baselines (or separate glibc and musl builds).

## App Icon (GUI)

The GUI auto-loads a window icon from:

- `assets/icon/icon-256.png` (all platforms)
- `assets/icon/icon.ico` (optional Windows override)

Recommended workflow:

1. Keep your source icon as SVG (for example `icon-concept-1-globe-doc-check.svg`).
2. Export PNG from SVG at 256x256 (or larger).
3. Save to `assets/icon/icon-256.png`.

Example export with Inkscape:

```bash
inkscape icon-concept-1-globe-doc-check.svg -w 256 -h 256 -o assets/icon/icon-256.png
```

## Config Schema

```json
{
  "source_po_dir": "/abs/path/to/source/translations",
  "target_po_dir": "/abs/path/to/godot-project/locale",
  "target_pot": "/abs/path/to/godot-project/locale/messages.pot",
  "source_pot": "/abs/path/to/source/translations/messages.pot",
  "project_root": "/abs/path/to/godot-project",
  "respect_gitignore": true
}
```

## Exit Codes

- `0` success
- `1` setup/input error
- `2` validation or audit findings require attention

## Localization Notes (`.gd` vs `.tscn`)

- In `.gd`, use `tr(...)` / `tr_n(...)` for user-facing strings.
- In `.tscn`, many built-in text properties are localization-aware and extracted by Godot.
- Not all properties auto-localize; verify POT output and runtime locale switching.
