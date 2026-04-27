#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_SCRIPT="${SCRIPT_DIR}/godot_translation_import.py"

ARGS=("$@")
if [[ ${#ARGS[@]} -eq 0 ]]; then
  ARGS=("merge")
elif [[ "${ARGS[0]}" != "merge" && "${ARGS[0]}" != "audit" ]]; then
  ARGS=("merge" "${ARGS[@]}")
fi

if command -v python3 >/dev/null 2>&1; then
  exec python3 "${PY_SCRIPT}" "${ARGS[@]}"
fi

if command -v python >/dev/null 2>&1; then
  exec python "${PY_SCRIPT}" "${ARGS[@]}"
fi

echo "error: python3/python not found. Install Python 3 first." >&2
exit 1
