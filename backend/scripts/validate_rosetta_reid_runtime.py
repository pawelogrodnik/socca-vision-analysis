#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2

from app.services.identity_rosetta_openvino_reid import (
    discover_rosetta_openvino_runtime,
    probe_rosetta_openvino_runtime,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--crop", required=True)
    parser.add_argument("--output")
    arguments = parser.parse_args()
    repository_root = Path(__file__).resolve().parents[2]
    candidate = discover_rosetta_openvino_runtime(
        repository_root / "backend" / "models"
    )
    crop = cv2.imread(arguments.crop)
    if crop is None or not crop.size:
        return 2
    result = probe_rosetta_openvino_runtime(
        candidate,
        real_crop_bgr=crop,
        diagnostics_directory=(
            repository_root
            / "backend"
            / "storage"
            / "benchmarks"
            / "player_identity"
            / "product-flow-20260730-v4"
            / "cross_capture_reid_diagnostic"
            / "runtime_lab"
        ),
    )
    rendered = json.dumps(result, ensure_ascii=True, indent=2)
    print(rendered)
    if arguments.output:
        Path(arguments.output).write_text(rendered + "\n", encoding="utf-8")
    if result.get("available"):
        return 0
    reason = str(result.get("reason") or "")
    if "candidate" in reason or reason == "smoke_crop_missing":
        return 2
    if "compile" in reason:
        return 3
    if "inference" in reason or "embedding" in reason:
        return 4
    if "ARCHITECTURE_MISMATCH" in reason:
        return 6
    if "repeatability" in reason:
        return 7
    return 5


if __name__ == "__main__":
    raise SystemExit(main())
