from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from app.model_defaults import DEFAULT_PLAYER_YOLO_MODEL
from app.services.analysis import analyze_match
from app.services.video import read_video_metadata


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Orlik Vision analysis from CLI")
    parser.add_argument("video", type=Path)
    parser.add_argument("pitch_config", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--adapter", choices=["motion", "yolo"], default="yolo")
    parser.add_argument("--max-seconds", type=float, default=30)
    parser.add_argument("--frame-stride", type=int, default=1)
    parser.add_argument("--yolo-model", default=DEFAULT_PLAYER_YOLO_MODEL)
    parser.add_argument("--yolo-conf", type=float, default=0.25)
    parser.add_argument("--yolo-imgsz", type=int, default=960)
    parser.add_argument("--yolo-tracker", default="botsort.yaml")
    parser.add_argument("--yolo-device", default=None)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.pitch_config, args.output_dir / "pitch_config.json")
    report = analyze_match(
        args.output_dir,
        args.video,
        adapter=args.adapter,
        max_seconds=args.max_seconds,
        frame_stride=args.frame_stride,
        yolo_model=args.yolo_model,
        yolo_conf=args.yolo_conf,
        yolo_imgsz=args.yolo_imgsz,
        yolo_tracker=args.yolo_tracker,
        yolo_device=args.yolo_device,
    )
    print(json.dumps({"video": read_video_metadata(args.video), "report": report}, indent=2))


if __name__ == "__main__":
    main()
