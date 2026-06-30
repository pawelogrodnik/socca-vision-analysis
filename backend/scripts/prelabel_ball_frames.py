from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.ball_prelabeling import (
    DEFAULT_BALL_PRELABEL_CONF,
    DEFAULT_BALL_PRELABEL_IMGSZ,
    DEFAULT_BALL_PRELABEL_IOU,
    DEFAULT_MAX_DETECTIONS_PER_IMAGE,
    prelabel_ball_frames,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a YOLO ball detector on exported training frames and create Roboflow-ready pre-labels.",
    )
    parser.add_argument("frames_dir", nargs="?", type=Path, help="Folder with extracted training frame images.")
    parser.add_argument("--frames-dir", dest="frames_dir_option", type=Path, help="Folder with extracted training frame images.")
    parser.add_argument("--out", "--output-dir", dest="output_dir", type=Path, default=None)
    parser.add_argument("--model", default="models/best.pt", help="YOLO model name or path. Supports backend/models/*.pt paths.")
    parser.add_argument("--conf", type=float, default=DEFAULT_BALL_PRELABEL_CONF)
    parser.add_argument("--iou", type=float, default=DEFAULT_BALL_PRELABEL_IOU)
    parser.add_argument("--imgsz", type=int, default=DEFAULT_BALL_PRELABEL_IMGSZ)
    parser.add_argument("--device", default=None, help='Ultralytics device, for example "cpu" or "0". Default: auto.')
    parser.add_argument(
        "--max-detections",
        type=int,
        default=DEFAULT_MAX_DETECTIONS_PER_IMAGE,
        help="Maximum ball boxes exported per image. Use 0 for no limit.",
    )
    parser.add_argument("--overwrite", action="store_true", help="Replace the output directory if it already exists.")
    parser.add_argument("--no-zip", action="store_true", help="Do not create roboflow_yolo.zip.")
    args = parser.parse_args()

    frames_dir = args.frames_dir_option or args.frames_dir
    if frames_dir is None:
        parser.error("Missing frames directory. Pass it positionally or with --frames-dir.")

    model = _load_yolo_model(args.model)
    result = prelabel_ball_frames(
        frames_dir,
        args.output_dir,
        model=model,
        model_name=args.model,
        conf=args.conf,
        iou=args.iou,
        imgsz=args.imgsz,
        device=args.device,
        max_detections_per_image=args.max_detections,
        overwrite=args.overwrite,
        create_zip=not args.no_zip,
    )
    print(json.dumps(_cli_summary(result), indent=2))


def _load_yolo_model(model_name: str):
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise RuntimeError("Ball pre-labeling requires ultralytics. Install backend requirements first.") from exc
    return YOLO(_resolve_yolo_model_name(model_name))


def _resolve_yolo_model_name(model_name: str) -> str:
    raw = str(model_name or "").strip()
    if not raw:
        raise ValueError("YOLO model name/path cannot be empty.")

    direct = Path(raw)
    if direct.is_absolute() or direct.exists():
        return str(direct)

    normalized = raw.replace("\\", "/")
    candidates = [
        BACKEND_DIR / raw,
        BACKEND_DIR.parent / raw,
    ]
    if normalized.startswith("backend/"):
        candidates.append(BACKEND_DIR / normalized[len("backend/") :])
    if normalized.startswith("models/"):
        candidates.append(BACKEND_DIR / normalized)

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    return raw


def _cli_summary(result: dict) -> dict:
    summary = result.get("summary") or {}
    return {
        "processed_images": summary.get("processed_images"),
        "images_with_detections": summary.get("images_with_detections"),
        "label_count": summary.get("label_count"),
        "roboflow_yolo_dir": result.get("roboflow_yolo_dir"),
        "roboflow_yolo_zip": result.get("roboflow_yolo_zip"),
        "annotated_preview_dir": result.get("annotated_preview_dir"),
        "predictions_json": str(Path(result.get("output_dir", "")) / "predictions.json"),
    }


if __name__ == "__main__":
    main()
