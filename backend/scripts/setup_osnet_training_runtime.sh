#!/usr/bin/env bash
set -euo pipefail

runtime_root="backend/.reid-runtime-lab/osnet-training"
python_bin="${PYTHON_BIN:-backend/.venv-mps/bin/python}"
"${python_bin}" -m venv "${runtime_root}"
"${runtime_root}/bin/python" -m pip install --upgrade pip
"${runtime_root}/bin/python" -m pip install -r backend/runtime/osnet-training-requirements.txt
