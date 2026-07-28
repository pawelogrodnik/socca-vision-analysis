from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2


def render_identity_roster_anchor_crops(
    video_path: Path,
    output_root: Path,
    artifact: dict[str, Any],
) -> set[str]:
    """Render the sparse crops referenced by the shadow roster artifact."""
    requests: dict[int, list[dict[str, Any]]] = {}
    for card in artifact.get("cards") or []:
        for crop in card.get("anchor_crops") or []:
            if not isinstance(crop, dict) or crop.get("frame") is None:
                continue
            requests.setdefault(int(crop["frame"]), []).append(crop)
    if not requests:
        return set()

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    rendered: set[str] = set()
    try:
        target_frames = set(requests)
        frame_index = 0
        while target_frames:
            ok, frame = capture.read()
            if not ok:
                break
            if frame_index in target_frames:
                height, width = frame.shape[:2]
                for crop in requests[frame_index]:
                    bbox = crop.get("bbox_xyxy") or []
                    if len(bbox) != 4 or not crop.get("artifact"):
                        continue
                    x1, y1, x2, y2 = [float(value) for value in bbox]
                    margin_x = max(8, int(round((x2 - x1) * 0.30)))
                    margin_y = max(8, int(round((y2 - y1) * 0.20)))
                    left = max(0, int(x1) - margin_x)
                    top = max(0, int(y1) - margin_y)
                    right = min(width, int(x2) + margin_x)
                    bottom = min(height, int(y2) + margin_y)
                    image = frame[top:bottom, left:right]
                    artifact_path = output_root / str(crop["artifact"])
                    artifact_path.parent.mkdir(parents=True, exist_ok=True)
                    if image.size and cv2.imwrite(str(artifact_path), image):
                        rendered.add(str(crop["artifact"]))
                target_frames.remove(frame_index)
            frame_index += 1
    finally:
        capture.release()
    return rendered
