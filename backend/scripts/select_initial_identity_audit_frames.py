from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

import cv2


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_initial_audit_frame_selection import (
    DEFAULT_PARAMETERS,
    build_initial_identity_audit_frame_selection,
    collect_candidate_frame_numbers,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Select easy high-value initial identity audit frames from frozen artifacts.",
    )
    parser.add_argument("--analysis-run", type=Path, required=True)
    parser.add_argument("--video", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--target-frames", type=int, default=8)
    parser.add_argument("--maximum-frames", type=int, default=10)
    parser.add_argument(
        "--candidate-stride",
        type=int,
        default=int(DEFAULT_PARAMETERS["candidate_stride_frames"]),
    )
    args = parser.parse_args()

    analysis_run = args.analysis_run.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    frames_dir = output_dir / "frames"
    frames_dir.mkdir()
    analysis_report = _load_json(analysis_run / "analysis_report.json")
    global_identity = _load_json(analysis_run / "global_identity.json")
    tracklets = _load_json(analysis_run / "tracklets.json")
    camera_motion_path = analysis_run / "camera_motion_report.json"
    camera_motion = (
        _load_json(camera_motion_path)
        if camera_motion_path.exists()
        else None
    )
    video_path = (
        args.video.resolve()
        if args.video
        else Path((analysis_report.get("video") or {}).get("path") or "").resolve()
    )
    if not video_path.exists():
        raise FileNotFoundError(f"Video does not exist: {video_path}")
    candidate_frames = collect_candidate_frame_numbers(
        global_identity,
        stride_frames=max(1, args.candidate_stride),
    )
    visual_metrics = _read_visual_metrics(video_path, candidate_frames)
    document = build_initial_identity_audit_frame_selection(
        global_identity,
        tracklets,
        analysis_report,
        camera_motion_report=camera_motion,
        frame_visual_metrics=visual_metrics,
        parameters={
            "target_frame_count": args.target_frames,
            "maximum_frame_count": args.maximum_frames,
            "candidate_stride_frames": args.candidate_stride,
        },
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    selected_frames = [
        int(row["frame"])
        for row in document["selected_frames"]
    ]
    _export_selected_frames(video_path, selected_frames, frames_dir)
    document["source"]["video_path"] = str(video_path)
    output_path = output_dir / "identity_initial_audit_frame_selection.json"
    output_path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(output_path),
                **document["summary"],
                "selection_digest": document["selection_digest"],
            },
            indent=2,
        )
    )


def _read_visual_metrics(
    video_path: Path,
    frames: list[int],
) -> dict[int, dict[str, float]]:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")
    metrics: dict[int, dict[str, float]] = {}
    try:
        for frame_number in frames:
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
            ok, frame = capture.read()
            if not ok:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            metrics[frame_number] = {
                "blur_variance": float(cv2.Laplacian(gray, cv2.CV_64F).var()),
            }
    finally:
        capture.release()
    return metrics


def _export_selected_frames(
    video_path: Path,
    frames: list[int],
    output_dir: Path,
) -> None:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")
    try:
        for frame_number in frames:
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
            ok, frame = capture.read()
            if not ok:
                raise RuntimeError(f"Could not read frame {frame_number}")
            full_path = output_dir / f"frame-{frame_number:06d}.jpg"
            thumbnail_path = output_dir / f"frame-{frame_number:06d}-thumb.jpg"
            cv2.imwrite(str(full_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 93])
            thumbnail = _fit_width(frame, 640)
            cv2.imwrite(
                str(thumbnail_path),
                thumbnail,
                [cv2.IMWRITE_JPEG_QUALITY, 88],
            )
    finally:
        capture.release()


def _fit_width(frame: Any, width: int) -> Any:
    if frame.shape[1] <= width:
        return frame
    scale = width / frame.shape[1]
    return cv2.resize(
        frame,
        (width, max(1, round(frame.shape[0] * scale))),
        interpolation=cv2.INTER_AREA,
    )


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
