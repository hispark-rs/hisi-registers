#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"$ROOT/scripts/generate.sh"
uv run python "$ROOT/scripts/check.py"

if command -v svd2rust >/dev/null 2>&1; then
    TMP="$(mktemp -d)"
    trap 'rm -rf "$TMP"' EXIT
    mkdir -p "$TMP/ws63" "$TMP/bs2x"
    svd2rust -i "$ROOT/generated/WS63.experimental.svd" -o "$TMP/ws63"
    svd2rust -i "$ROOT/generated/BS2X.experimental.svd" -o "$TMP/bs2x"
else
    echo "warning: svd2rust is not installed; skipping PAC parser smoke" >&2
fi
