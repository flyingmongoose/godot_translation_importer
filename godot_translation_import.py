#!/usr/bin/env python3
"""Generic gettext migration/audit tooling for Godot projects.

Subcommands:
- merge: copy/merge source .po catalogs into target catalogs
- audit: compare templates and detect localization wiring gaps

Exit codes:
  0: success
  1: setup/input error
  2: completed with validation/actionable failures
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class MergeSummary:
    merged_count: int
    valid_count: int
    failed_count: int
    total_count: int


@dataclass
class AuditSummary:
    godot_msgids: int
    fife_msgids: int
    common_msgids: int
    only_in_fife: int
    only_in_godot: int
    tr_literals_found: int
    tr_literals_missing_from_pot: int
    source_candidates_found: int
    source_candidates_missing_from_pot: int
    catalog_msgids_total: int
    catalog_msgids_referenced_in_source: int
    catalog_msgids_not_found_in_source: int
    raw_gd_msgid_occurrences_without_tr: int


ProgressCallback = Callable[[int, int, str, str], None]
LogCallback = Callable[[str], None]
AuditProgressCallback = Callable[[str, int, int], None]
REQUIRED_GETTEXT_TOOLS = ("msgmerge", "msgfmt")


def get_missing_gettext_tools() -> list[str]:
    return [tool for tool in REQUIRED_GETTEXT_TOOLS if shutil.which(tool) is None]


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"{name} not found. Install gettext first.")


def run(cmd: list[str], quiet: bool = False) -> int:
    if quiet:
        completed = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        completed = subprocess.run(cmd)
    return completed.returncode


def null_sink() -> str:
    if os.name == "nt":
        return "NUL"
    return "/dev/null"


def load_json_config(path: Path | None) -> dict:
    if path is None:
        return {}
    if not path.is_file():
        raise RuntimeError(f"Config file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in config: {path} ({exc})") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"Config root must be an object: {path}")
    return data


def resolve_path(value: str | None, conf: dict, key: str, label: str) -> Path:
    resolved = value if value else conf.get(key)
    if not resolved:
        raise RuntimeError(
            f"Missing required path for {label}. Provide CLI arg or config key '{key}'."
        )
    return Path(str(resolved)).expanduser().resolve()


def _decode_po_quoted(quoted: str) -> str:
    if len(quoted) < 2:
        return ""
    inner = quoted[1:-1]
    return ast.literal_eval(f'"{inner}"')


def _decode_escaped_fragment(fragment: str) -> str:
    return ast.literal_eval(f'"{fragment}"')


def extract_msgids_from_po_like(path: Path) -> set[str]:
    msgids: set[str] = set()
    lines = path.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line.startswith("msgid "):
            i += 1
            continue
        first = line[len("msgid ") :].strip()
        if not first.startswith('"'):
            i += 1
            continue
        parts = [_decode_po_quoted(first)]
        i += 1
        while i < len(lines):
            cont = lines[i].strip()
            if not cont.startswith('"'):
                break
            parts.append(_decode_po_quoted(cont))
            i += 1
        msgid = "".join(parts)
        if msgid:
            msgids.add(msgid)
    return msgids


_TR_CALL_RE = re.compile(r'(?<![A-Za-z0-9_])tr(?:_n)?\(\s*"((?:\\.|[^"\\])*)"')
_TSCN_TEXT_PROP_RE = re.compile(
    r'^\s*(?:text|placeholder_text|tooltip_text)\s*=\s*"((?:\\.|[^"\\])*)"\s*$'
)
_GD_TEXT_ASSIGN_RE = re.compile(
    r'(?<![A-Za-z0-9_])(?:text|placeholder_text|tooltip_text)\s*=\s*"((?:\\.|[^"\\])*)"'
)
_GD_UI_METHOD_RE = re.compile(
    r'(?<![A-Za-z0-9_])(?:add_item|add_tab|set_tab_title|set_item_text)\s*\([^"\n]*"((?:\\.|[^"\\])*)"'
)
_QUOTED_STRING_RE = re.compile(r'"((?:\\.|[^"\\])*)"')
SUPPORTED_AUDIT_EXTENSIONS = {
    ".gd",
    ".tscn",
    ".res",
    ".scn",
    ".dae",
    ".obj",
    ".escn",
    ".fbx",
    ".gltf",
    ".glb",
    ".blend",
}
HARD_SKIPPED_DIR_NAMES = {
    ".git",
    ".godot",
    "addons",
    ".import",
    ".cache",
    "build",
    "dist",
}


class GitIgnorePolicy:
    def __init__(self, project_root: Path, enabled: bool) -> None:
        self.project_root = project_root
        self.enabled = enabled
        self._cache: dict[str, bool] = {}
        self._available = (
            enabled
            and (project_root / ".git").exists()
            and shutil.which("git") is not None
        )

    def is_ignored(self, relative: Path) -> bool:
        if not self._available:
            return False
        key = relative.as_posix()
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            completed = subprocess.run(
                ["git", "check-ignore", "-q", key],
                cwd=str(self.project_root),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            self._available = False
            return False
        ignored = completed.returncode == 0
        self._cache[key] = ignored
        return ignored


def iter_audit_files(project_root: Path, respect_gitignore: bool) -> list[Path]:
    policy = GitIgnorePolicy(project_root, enabled=respect_gitignore)
    files: list[Path] = []
    for root_str, dirnames, filenames in os.walk(project_root, topdown=True):
        root_path = Path(root_str)
        rel_root = root_path.relative_to(project_root)

        kept_dirs: list[str] = []
        for dirname in dirnames:
            if dirname in HARD_SKIPPED_DIR_NAMES:
                continue
            rel_dir = rel_root / dirname if rel_root != Path(".") else Path(dirname)
            if policy.is_ignored(rel_dir):
                continue
            kept_dirs.append(dirname)
        dirnames[:] = kept_dirs

        for filename in filenames:
            path = root_path / filename
            if path.suffix not in SUPPORTED_AUDIT_EXTENSIONS:
                continue
            rel_file = path.relative_to(project_root)
            if policy.is_ignored(rel_file):
                continue
            files.append(path)
    return files


def extract_tr_literals(
    project_root: Path,
    respect_gitignore: bool,
    progress_cb: AuditProgressCallback | None = None,
) -> set[str]:
    literals: set[str] = set()
    files = iter_audit_files(project_root, respect_gitignore)
    total = len(files)
    for idx, path in enumerate(files, start=1):
        if progress_cb is not None:
            progress_cb("tr_literals", idx, total)
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for match in _TR_CALL_RE.finditer(content):
            raw = match.group(1)
            decoded = _decode_escaped_fragment(raw)
            if decoded:
                literals.add(decoded)
    return literals


def extract_source_string_candidates(project_root: Path, respect_gitignore: bool) -> set[str]:
    """Heuristic extraction of likely UI-visible literals in source files.

    This is intentionally broader than tr(...) extraction to find probable hardcoded
    UI strings that may need localization wiring.
    """
    literals: set[str] = set()
    for path in iter_audit_files(project_root, respect_gitignore):
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        if path.suffix == ".tscn":
            for line in content.splitlines():
                match = _TSCN_TEXT_PROP_RE.match(line)
                if not match:
                    continue
                decoded = _decode_escaped_fragment(match.group(1))
                if decoded.strip():
                    literals.add(decoded)
            continue

        # GDScript/TRES heuristics.
        for regex in (_GD_TEXT_ASSIGN_RE, _GD_UI_METHOD_RE):
            for match in regex.finditer(content):
                decoded = _decode_escaped_fragment(match.group(1))
                if decoded.strip():
                    literals.add(decoded)
    return literals


def extract_source_string_candidates_with_paths(
    project_root: Path,
    respect_gitignore: bool,
    progress_cb: AuditProgressCallback | None = None,
) -> dict[str, set[tuple[Path, int]]]:
    """Heuristic extraction of likely UI-visible literals with source paths."""
    found: dict[str, set[tuple[Path, int]]] = {}

    def add(value: str, src_path: Path, line_no: int) -> None:
        if not value.strip():
            return
        found.setdefault(value, set()).add((src_path, line_no))

    files = iter_audit_files(project_root, respect_gitignore)
    total = len(files)
    for idx, path in enumerate(files, start=1):
        if progress_cb is not None:
            progress_cb("deep_scan", idx, total)
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        if path.suffix == ".tscn":
            for line_no, line in enumerate(content.splitlines(), start=1):
                match = _TSCN_TEXT_PROP_RE.match(line)
                if not match:
                    continue
                add(_decode_escaped_fragment(match.group(1)), path, line_no)
            continue

        for line_no, line in enumerate(content.splitlines(), start=1):
            for regex in (_GD_TEXT_ASSIGN_RE, _GD_UI_METHOD_RE):
                for match in regex.finditer(line):
                    add(_decode_escaped_fragment(match.group(1)), path, line_no)

    return found


def collect_catalog_msgids(target_pot: Path, target_po_dir: Path | None = None) -> set[str]:
    msgids = extract_msgids_from_po_like(target_pot)
    if target_po_dir is None:
        return msgids
    if not target_po_dir.is_dir():
        return msgids
    for po_path in sorted(target_po_dir.glob("*.po")):
        msgids.update(extract_msgids_from_po_like(po_path))
    return msgids


def scan_catalog_msgid_usage(
    project_root: Path,
    catalog_msgids: set[str],
    respect_gitignore: bool,
    progress_cb: AuditProgressCallback | None = None,
) -> tuple[set[str], dict[str, set[tuple[Path, int]]]]:
    """Find msgids referenced as raw literals in source files.

    Returns:
    - set of msgids that appear at least once in scanned sources.
    - mapping msgid -> gd locations where raw literal appears on a line without tr(...).
    """
    referenced: set[str] = set()
    missing_tr_locations: dict[str, set[tuple[Path, int]]] = {}
    if not catalog_msgids:
        return referenced, missing_tr_locations

    files = iter_audit_files(project_root, respect_gitignore)
    total = len(files)
    for idx, path in enumerate(files, start=1):
        if progress_cb is not None:
            progress_cb("catalog_usage", idx, total)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue

        for line_no, line in enumerate(lines, start=1):
            for match in _QUOTED_STRING_RE.finditer(line):
                literal = _decode_escaped_fragment(match.group(1))
                if not literal or literal not in catalog_msgids:
                    continue
                referenced.add(literal)
                if path.suffix != ".gd":
                    continue

                # Heuristic: if the same line contains tr(, assume literal is localized.
                # Otherwise flag as potential place to wrap with tr(...).
                if "tr(" in line or "tr_n(" in line:
                    continue
                missing_tr_locations.setdefault(literal, set()).add((path, line_no))

    return referenced, missing_tr_locations


def merge_catalogs(
    source_po_dir: Path,
    target_po_dir: Path,
    target_pot: Path,
    progress_cb: ProgressCallback | None = None,
    log_cb: LogCallback | None = None,
) -> MergeSummary:
    require_tool("msgmerge")
    require_tool("msgfmt")

    if not source_po_dir.is_dir():
        raise RuntimeError(f"Source PO directory not found: {source_po_dir}")
    if not target_po_dir.is_dir():
        raise RuntimeError(f"Target PO directory not found: {target_po_dir}")
    if not target_pot.is_file():
        raise RuntimeError(f"Target POT not found: {target_pot}")

    po_files = sorted(source_po_dir.glob("*.po"))
    if not po_files:
        raise RuntimeError(f"no .po files found in {source_po_dir}")

    def emit(msg: str) -> None:
        if log_cb is not None:
            log_cb(msg)

    emit(f"Merging {len(po_files)} language files...")
    emit(f"  from: {source_po_dir}")
    emit(f"  into: {target_po_dir}")
    emit(f"   pot: {target_pot}")
    emit("")

    merged_count = 0
    valid_count = 0
    failed_count = 0
    total = len(po_files)

    for idx, src in enumerate(po_files, start=1):
        lang_file = src.name
        dst = target_po_dir / lang_file

        if progress_cb is not None:
            progress_cb(idx, total, lang_file, "merge")

        shutil.copy2(src, dst)

        merge_exit = run(
            ["msgmerge", "--update", "--backup=none", str(dst), str(target_pot)],
            quiet=True,
        )
        if merge_exit != 0:
            failed_count += 1
            emit(f"[fail]  {lang_file} (msgmerge failed)")
            continue

        merged_count += 1

        if progress_cb is not None:
            progress_cb(idx, total, lang_file, "validate")

        validate_exit = run(
            ["msgfmt", "--check", "-o", null_sink(), str(dst)],
            quiet=True,
        )
        if validate_exit == 0:
            valid_count += 1
            emit(f"[ok]    {lang_file}")
        else:
            failed_count += 1
            emit(f"[fail]  {lang_file} (msgfmt --check failed)")

    emit("")
    emit("Done.")
    emit(f"Merged:      {merged_count}")
    emit(f"Validated:   {valid_count}")
    emit(f"Failed check:{failed_count}")

    return MergeSummary(
        merged_count=merged_count,
        valid_count=valid_count,
        failed_count=failed_count,
        total_count=total,
    )


def audit_catalogs(
    target_pot: Path,
    source_pot: Path | None = None,
    project_root: Path | None = None,
    target_po_dir: Path | None = None,
    deep_scan: bool = True,
    respect_gitignore: bool = True,
    log_cb: LogCallback | None = None,
    progress_cb: AuditProgressCallback | None = None,
) -> AuditSummary:
    if not target_pot.is_file():
        raise RuntimeError(f"Target POT not found: {target_pot}")
    if target_po_dir is not None and not target_po_dir.is_dir():
        raise RuntimeError(f"Target PO directory not found: {target_po_dir}")
    if source_pot is not None and not source_pot.is_file():
        raise RuntimeError(f"Source POT not found: {source_pot}")
    if project_root is not None and not project_root.is_dir():
        raise RuntimeError(f"Project root not found: {project_root}")

    def emit(msg: str) -> None:
        if log_cb is not None:
            log_cb(msg)

    target_msgids = extract_msgids_from_po_like(target_pot)
    source_msgids: set[str] = set()
    if source_pot is not None:
        source_msgids = extract_msgids_from_po_like(source_pot)

    common = target_msgids & source_msgids if source_msgids else set()
    only_source = source_msgids - target_msgids if source_msgids else set()
    only_target = target_msgids - source_msgids if source_msgids else set()

    tr_literals: set[str] = set()
    tr_missing: set[str] = set()
    source_candidates: set[str] = set()
    source_missing: set[str] = set()
    source_candidate_paths: dict[str, set[tuple[Path, int]]] = {}
    catalog_msgids: set[str] = collect_catalog_msgids(target_pot, target_po_dir)
    referenced_msgids: set[str] = set()
    raw_missing_tr: dict[str, set[tuple[Path, int]]] = {}
    if project_root is not None:
        tr_literals = extract_tr_literals(project_root, respect_gitignore, progress_cb=progress_cb)
        tr_missing = tr_literals - target_msgids
        referenced_msgids, raw_missing_tr = scan_catalog_msgid_usage(
            project_root, catalog_msgids, respect_gitignore, progress_cb=progress_cb
        )
        if deep_scan:
            source_candidate_paths = extract_source_string_candidates_with_paths(
                project_root, respect_gitignore, progress_cb=progress_cb
            )
            source_candidates = set(source_candidate_paths.keys())
            source_missing = source_candidates - target_msgids

    emit("Audit summary:")
    emit(f"  Target POT msgids: {len(target_msgids)}")
    if source_pot is not None:
        emit(f"  Source POT msgids: {len(source_msgids)}")
        emit(f"  Common:            {len(common)}")
        emit(f"  Only in source:    {len(only_source)}")
        emit(f"  Only in target:    {len(only_target)}")
    if project_root is not None:
        emit(f"  respect .gitignore: {respect_gitignore}")
        emit(f"  tr(...) literals found in project: {len(tr_literals)}")
        emit(f"  tr(...) literals missing from POT: {len(tr_missing)}")
        emit(f"  catalog msgids considered (pot+po): {len(catalog_msgids)}")
        emit(f"  catalog msgids referenced in source: {len(referenced_msgids)}")
        emit(f"  catalog msgids not found in source:  {len(catalog_msgids - referenced_msgids)}")
        emit(f"  raw gd literal occurrences missing tr(...): {sum(len(v) for v in raw_missing_tr.values())}")
        if raw_missing_tr:
            emit("  sample places to add tr(...):")
            for msgid in sorted(raw_missing_tr.keys())[:15]:
                emit(f"    - {msgid}")
                for src_path, line_no in sorted(raw_missing_tr[msgid])[:3]:
                    try:
                        rel = src_path.relative_to(project_root)
                        shown = str(rel)
                    except ValueError:
                        shown = str(src_path)
                    emit(f"      @ {shown}:{line_no}")
        if deep_scan:
            emit(f"  source string candidates found:  {len(source_candidates)}")
            emit(f"  candidates missing from POT:     {len(source_missing)}")
            sample_missing = sorted(source_missing)[:20]
            if sample_missing:
                emit("  sample missing candidates:")
                for item in sample_missing:
                    emit(f"    - {item}")
                    sample_refs = sorted(source_candidate_paths.get(item, set()))
                    for src_path, line_no in sample_refs[:3]:
                        try:
                            rel = src_path.relative_to(project_root)
                            shown = str(rel)
                        except ValueError:
                            shown = str(src_path)
                        emit(f"      @ {shown}:{line_no}")

    return AuditSummary(
        godot_msgids=len(target_msgids),
        fife_msgids=len(source_msgids),
        common_msgids=len(common),
        only_in_fife=len(only_source),
        only_in_godot=len(only_target),
        tr_literals_found=len(tr_literals),
        tr_literals_missing_from_pot=len(tr_missing),
        source_candidates_found=len(source_candidates),
        source_candidates_missing_from_pot=len(source_missing),
        catalog_msgids_total=len(catalog_msgids),
        catalog_msgids_referenced_in_source=len(referenced_msgids),
        catalog_msgids_not_found_in_source=len(catalog_msgids - referenced_msgids),
        raw_gd_msgid_occurrences_without_tr=sum(len(v) for v in raw_missing_tr.values()),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Godot gettext import and audit helper.")
    sub = parser.add_subparsers(dest="command", required=True)

    merge = sub.add_parser("merge", help="Merge source gettext catalogs into Godot catalogs.")
    merge.add_argument("--config", type=str, default=None, help="Path to JSON config.")
    merge.add_argument(
        "--source-po-dir",
        type=str,
        default=None,
        help="Source translation directory (.po files).",
    )
    merge.add_argument(
        "--target-po-dir",
        type=str,
        default=None,
        help="Target translation directory (.po files).",
    )
    merge.add_argument(
        "--target-pot", type=str, default=None, help="Target POT template path."
    )

    audit = sub.add_parser("audit", help="Audit template alignment and localization coverage.")
    audit.add_argument("--config", type=str, default=None, help="Path to JSON config.")
    audit.add_argument(
        "--target-pot", type=str, default=None, help="Target POT template path."
    )
    audit.add_argument("--source-pot", type=str, default=None, help="Optional source POT path.")
    audit.add_argument(
        "--project-root",
        type=str,
        default=None,
        required=False,
        help="Godot project root for source scans.",
    )
    audit.add_argument(
        "--target-po-dir",
        type=str,
        default=None,
        required=False,
        help="Target PO directory to include additional msgids.",
    )
    audit.add_argument(
        "--no-respect-gitignore",
        action="store_false",
        dest="respect_gitignore",
        default=True,
        help="Scan ignored files too (default respects .gitignore when available).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = getattr(args, "config", None)
    try:
        missing_tools = get_missing_gettext_tools()
        if missing_tools:
            missing = ", ".join(missing_tools)
            raise RuntimeError(
                f"Missing required gettext tool(s): {missing}. Install GNU gettext and ensure tools are on PATH."
            )
        conf = load_json_config(Path(config_path).resolve() if config_path else None)
        if args.command == "merge":
            source_po_dir = resolve_path(args.source_po_dir, conf, "source_po_dir", "Source PO dir")
            target_po_dir = resolve_path(args.target_po_dir, conf, "target_po_dir", "Target PO dir")
            target_pot = resolve_path(args.target_pot, conf, "target_pot", "Target POT")
            summary = merge_catalogs(
                source_po_dir=source_po_dir,
                target_po_dir=target_po_dir,
                target_pot=target_pot,
                log_cb=print,
            )
            if summary.failed_count != 0:
                return 2
            return 0

        if args.command == "audit":
            target_pot = resolve_path(args.target_pot, conf, "target_pot", "Target POT")
            source_pot: Path | None = None
            if args.source_pot or conf.get("source_pot"):
                source_pot = resolve_path(args.source_pot, conf, "source_pot", "Source POT")
            project_root = resolve_path(args.project_root, conf, "project_root", "Godot project root")
            target_po_dir = resolve_path(args.target_po_dir, conf, "target_po_dir", "Target PO dir")
            respect_gitignore = args.respect_gitignore and bool(conf.get("respect_gitignore", True))
            summary = audit_catalogs(
                target_pot=target_pot,
                source_pot=source_pot,
                project_root=project_root,
                target_po_dir=target_po_dir,
                deep_scan=True,
                respect_gitignore=respect_gitignore,
                log_cb=print,
            )
            if (
                summary.tr_literals_missing_from_pot > 0
                or summary.source_candidates_missing_from_pot > 0
                or summary.raw_gd_msgid_occurrences_without_tr > 0
            ):
                return 2
            return 0
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
