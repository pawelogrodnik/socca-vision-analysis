#!/bin/sh
set -eu

repository_root="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
runtime_root="${ROSETTA_REID_RUNTIME_DIR:-$repository_root/backend/.reid-runtime-lab/ov-2026.1-rosetta-x86}"
requirements="$repository_root/backend/runtime/rosetta-reid-requirements.txt"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "Rosetta ReID runtime is supported only on macOS." >&2
  exit 2
fi
if [ "$(uname -m)" != "arm64" ]; then
  echo "Rosetta setup expects an Apple Silicon host." >&2
  exit 2
fi
if ! /usr/bin/arch -x86_64 /usr/bin/python3 -c 'import platform; raise SystemExit(0 if platform.machine() == "x86_64" else 1)'; then
  echo "Rosetta 2 or the x86_64 system Python is unavailable." >&2
  exit 2
fi

/usr/bin/arch -x86_64 /usr/bin/python3 -m venv "$runtime_root"
/usr/bin/arch -x86_64 "$runtime_root/bin/python" -m pip install \
  --disable-pip-version-check \
  --requirement "$requirements"
/usr/bin/arch -x86_64 "$runtime_root/bin/python" -c \
  'import platform, numpy, openvino; assert platform.machine() == "x86_64"; print(openvino.__version__, numpy.__version__)'

"$repository_root/backend/scripts/validate_rosetta_reid_runtime.sh"
