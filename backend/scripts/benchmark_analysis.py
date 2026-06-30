from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.runtime import build_performance_report, collect_runtime_info, normalize_yolo_device


def _slug(value: str) -> str:
    safe = "".join(ch if ch.isalnum() else "-" for ch in value.lower()).strip("-")
    return safe or "benchmark"


def _find_video(match_dir: Path) -> Path:
    for candidate in sorted(match_dir.glob("video.*")):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Video file not found in {match_dir}")


def _resolve_inputs(args: argparse.Namespace, matches_dir: Path) -> tuple[Path, Path, str]:
    if args.match_id:
        match_dir = matches_dir / args.match_id
        if not match_dir.exists():
            raise FileNotFoundError(f"Match not found: {args.match_id}")
        pitch_config = match_dir / "pitch_config.json"
        if not pitch_config.exists():
            raise FileNotFoundError(f"pitch_config.json not found for match: {args.match_id}")
        return _find_video(match_dir), pitch_config, args.match_id
    if not args.video or not args.pitch_config:
        raise ValueError("Use --match-id or provide both --video and --pitch-config.")
    return args.video, args.pitch_config, _slug(args.video.stem)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a native Orlik Vision analysis benchmark.")
    parser.add_argument("--match-id", default=None, help="Existing match id from backend/storage/matches.")
    parser.add_argument("--video", type=Path, default=None, help="Video path when not using --match-id.")
    parser.add_argument("--pitch-config", type=Path, default=None, help="pitch_config.json path when not using --match-id.")
    parser.add_argument("--label", default="", help="Human label, for example macbook-m4-mps or dell-gtx1650-cuda.")
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--adapter", choices=["motion", "yolo"], default="yolo")
    parser.add_argument("--max-seconds", type=float, default=60.0)
    parser.add_argument("--frame-stride", type=int, default=2)
    parser.add_argument("--yolo-model", default="yolov8n.pt")
    parser.add_argument("--yolo-conf", type=float, default=0.05)
    parser.add_argument("--yolo-imgsz", type=int, default=960)
    parser.add_argument("--yolo-tracker", default="centroid_high_recall")
    parser.add_argument("--device", "--yolo-device", dest="yolo_device", default="auto")
    args = parser.parse_args()

    from app.config import MATCHES_DIR, STORAGE_DIR

    video_path, pitch_config_path, input_label = _resolve_inputs(args, MATCHES_DIR)
    normalized_device = normalize_yolo_device(args.yolo_device)
    output_root = args.output_root or STORAGE_DIR / "benchmarks"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    label = args.label or f"{input_label}-{args.yolo_device or 'auto'}"
    output_dir = output_root / f"{timestamp}-{_slug(label)}"
    output_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(pitch_config_path, output_dir / "pitch_config.json")

    runtime_info = collect_runtime_info()
    input_report = {
        "schema_version": "0.1.0",
        "label": label,
        "video_path": str(video_path),
        "pitch_config_path": str(pitch_config_path),
        "output_dir": str(output_dir),
        "parameters": {
            "adapter": args.adapter,
            "max_seconds": args.max_seconds,
            "frame_stride": max(1, args.frame_stride),
            "yolo_model": args.yolo_model,
            "yolo_conf": args.yolo_conf,
            "yolo_imgsz": args.yolo_imgsz,
            "yolo_tracker": args.yolo_tracker,
            "yolo_device_requested": args.yolo_device,
            "yolo_device": normalized_device or "auto",
        },
        "runtime": runtime_info,
    }
    (output_dir / "benchmark_input.json").write_text(json.dumps(input_report, indent=2), encoding="utf-8")

    from app.services.analysis import analyze_match

    started = time.perf_counter()
    analysis_report = analyze_match(
        output_dir,
        video_path,
        adapter=args.adapter,
        max_seconds=args.max_seconds,
        frame_stride=max(1, args.frame_stride),
        yolo_model=args.yolo_model,
        yolo_conf=args.yolo_conf,
        yolo_imgsz=args.yolo_imgsz,
        yolo_tracker=args.yolo_tracker,
        yolo_device=normalized_device,
    )
    elapsed = time.perf_counter() - started
    performance_report = build_performance_report(
        label=label,
        requested_device=args.yolo_device,
        normalized_device=normalized_device,
        elapsed_wall_sec=elapsed,
        analysis_report=analysis_report,
        runtime_info=runtime_info,
    )
    performance_report["artifacts"]["output_dir"] = str(output_dir)
    (output_dir / "performance_report.json").write_text(json.dumps(performance_report, indent=2), encoding="utf-8")
    print(json.dumps(performance_report, indent=2))


if __name__ == "__main__":
    main()
