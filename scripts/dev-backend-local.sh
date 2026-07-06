#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ -f "$ROOT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

if [ -f "$ROOT_DIR/backend/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/backend/.env"
  set +a
fi

PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/backend/.venv-mps/bin/python}"
if [ ! -x "$PYTHON_BIN" ]; then
  echo "Python env not found: $PYTHON_BIN" >&2
  echo "Create it first or set PYTHON_BIN to another interpreter." >&2
  exit 1
fi

mkdir -p "$ROOT_DIR/.cache/ultralytics" "$ROOT_DIR/.cache/matplotlib" "$ROOT_DIR/backend/storage/database"

export PATH="$ROOT_DIR/backend/.venv-mps/bin:$PATH"
export PYTHONPATH="$ROOT_DIR/backend${PYTHONPATH:+:$PYTHONPATH}"
export YOLO_CONFIG_DIR="${YOLO_CONFIG_DIR:-$ROOT_DIR/.cache/ultralytics}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-$ROOT_DIR/.cache/matplotlib}"

exec "$PYTHON_BIN" -m uvicorn app.main:app \
  --app-dir "$ROOT_DIR/backend" \
  --host "${BACKEND_HOST:-0.0.0.0}" \
  --port "${BACKEND_PORT:-8000}" \
  --reload
