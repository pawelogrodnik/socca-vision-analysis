#!/bin/sh
set -eu

repository_root="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
crop_path="${1:-}"
if [ -z "$crop_path" ]; then
  crop_path="$(find "$repository_root/backend/storage/benchmarks/player_identity/product-flow-20260730-v4/cross_capture_reid_diagnostic/h1" -type f -name '*.jpg' -print -quit)"
fi
if [ -z "$crop_path" ] || [ ! -f "$crop_path" ]; then
  echo "A real existing ReID crop is required." >&2
  exit 2
fi

PYTHONPATH="$repository_root/backend" \
  "$repository_root/backend/.venv-mps/bin/python" \
  "$repository_root/backend/scripts/validate_rosetta_reid_runtime.py" \
  --crop "$crop_path"
