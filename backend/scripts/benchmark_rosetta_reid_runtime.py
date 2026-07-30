#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

import cv2
import numpy as np

from app.services.identity_rosetta_openvino_reid import (
    activate_rosetta_openvino_runtime,
    discover_rosetta_openvino_runtime,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--crop-root", required=True)
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()
    crop_paths = sorted(Path(arguments.crop_root).rglob("*.jpg"))[:10]
    crops = [cv2.imread(str(path)) for path in crop_paths]
    crops = [crop for crop in crops if crop is not None and crop.size]
    if not crops:
        return 2
    repository_root = Path(__file__).resolve().parents[2]
    candidate = discover_rosetta_openvino_runtime(
        repository_root / "backend" / "models"
    )
    cold_started = time.perf_counter()
    embedder, status = activate_rosetta_openvino_runtime(
        candidate,
        real_crop_bgr=crops[0],
    )
    cold_ms = (time.perf_counter() - cold_started) * 1000.0
    if embedder is None:
        return 5
    one_started = time.perf_counter()
    one = embedder.embed_batch(crops[:1])
    one_ms = (time.perf_counter() - one_started) * 1000.0
    ten_inputs = (crops * 10)[:10]
    ten_started = time.perf_counter()
    ten = embedder.embed_batch(ten_inputs)
    ten_ms = (time.perf_counter() - ten_started) * 1000.0
    repeat = embedder.embed_batch(ten_inputs)
    document = {
        "schema_version": "1.0.0",
        "mode": "rosetta_reid_runtime_performance_smoke",
        "runtime": status.get("runtime_details") or {},
        "model": status.get("model") or {},
        "runtime_manifest_digest": status.get("runtime_manifest_digest"),
        "probe_timings_ms": status.get("timings_ms") or {},
        "performance_ms": {
            "cold_probe_and_activation": round(cold_ms, 3),
            "one_crop_total": round(one_ms, 3),
            "ten_crop_total": round(ten_ms, 3),
            "ten_crop_average": round(ten_ms / 10.0, 3),
        },
        "contract": {
            "one_shape": list(one.shape),
            "ten_shape": list(ten.shape),
            "finite": bool(np.isfinite(ten).all()),
            "normalized": bool(
                np.allclose(np.linalg.norm(ten, axis=1), 1.0, atol=1e-6)
            ),
            "repeatable": bool(
                np.allclose(ten, repeat, rtol=1e-5, atol=1e-6)
            ),
        },
    }
    output = Path(arguments.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(document, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(document, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
