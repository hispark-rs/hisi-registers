#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

mkdir -p "$ROOT/generated"
uv run python "$ROOT/scripts/export_svd.py" \
    "$ROOT/rdl/chips/ws63.rdl" "$ROOT/generated/WS63.experimental.svd"
uv run python "$ROOT/scripts/export_svd.py" \
    "$ROOT/rdl/chips/bs2x.rdl" "$ROOT/generated/BS2X.experimental.svd"
uv run python "$ROOT/scripts/export_svd.py" \
    "$ROOT/rdl/chips/ws53.rdl" "$ROOT/generated/WS53.experimental.svd"
