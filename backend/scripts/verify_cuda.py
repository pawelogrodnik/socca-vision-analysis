from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.analysis import _resolve_yolo_model_name
from app.services.runtime import collect_runtime_info, ensure_yolo_device_available, normalize_yolo_device


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify native CUDA runtime for Orlik Vision YOLO analysis.")
    parser.add_argument("--device", default="cuda", help="CUDA alias/index to verify, for example cuda or 0.")
    parser.add_argument("--model", default="yolov8n.pt", help="YOLO model path/name used for a short inference.")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--conf", type=float, default=0.25)
    args = parser.parse_args()

    runtime_info = collect_runtime_info()
    try:
        normalized_device = ensure_yolo_device_available(
            args.device,
            runtime_info=runtime_info,
            context="verify_cuda",
        )
    except RuntimeError as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "requested_device": args.device,
                    "normalized_yolo_device": normalize_yolo_device(args.device) or "auto",
                    "error": str(exc),
                    "runtime": runtime_info,
                },
                indent=2,
            )
        )
        raise SystemExit(2) from exc
    yolo_summary = run_yolo_smoke(
        model_name=args.model,
        device=normalized_device,
        imgsz=args.imgsz,
        conf=args.conf,
    )
    result: dict[str, Any] = {
        "status": "ok",
        "requested_device": args.device,
        "normalized_yolo_device": normalize_yolo_device(args.device) or "auto",
        "runtime": runtime_info,
        "yolo_smoke": yolo_summary,
    }
    print(json.dumps(result, indent=2))


def run_yolo_smoke(*, model_name: str, device: str | None, imgsz: int, conf: float) -> dict[str, Any]:
    from ultralytics import YOLO

    model = YOLO(_resolve_yolo_model_name(model_name))
    frame = np.zeros((int(imgsz), int(imgsz), 3), dtype=np.uint8)
    started = time.perf_counter()
    kwargs: dict[str, Any] = {
        "source": frame,
        "conf": float(conf),
        "imgsz": int(imgsz),
        "verbose": False,
    }
    if device:
        kwargs["device"] = device
    results = model.predict(**kwargs)
    elapsed = time.perf_counter() - started
    boxes = results[0].boxes if results else None
    return {
        "model": model_name,
        "device": device or "auto",
        "elapsed_sec": round(elapsed, 3),
        "prediction_count": int(len(boxes) if boxes is not None else 0),
    }


if __name__ == "__main__":
    main()
