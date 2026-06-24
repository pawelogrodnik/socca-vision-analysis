from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2


def read_video_metadata(video_path: Path) -> dict[str, Any]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"Could not open video: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    duration = float(frame_count / fps) if fps else 0.0
    cap.release()
    return {
        "path": str(video_path),
        "fps": round(float(fps), 3),
        "frame_count": frame_count,
        "width": width,
        "height": height,
        "duration_sec": round(duration, 3),
    }


def extract_frame(video_path: Path, second: float, output_path: Path) -> Path:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"Could not open video: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    frame_idx = max(0, int(second * fps))
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise ValueError(f"Could not extract frame at {second}s")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), frame)
    return output_path
