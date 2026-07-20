from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import shutil
import subprocess
from typing import Any


TEAM_COLORS = {
    "A": (0, 110, 255),
    "B": (255, 170, 20),
    "U": (0, 230, 230),
}


def render_identity_candidate_overlay(
    video_path: Path,
    output_path: Path,
    candidate_overlay_doc: dict[str, Any],
    *,
    start_sec: float = 0.0,
    max_seconds: float | None = None,
) -> Path:
    """Render a lightweight identity-only comparison video without pitch or stats."""
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")
    fps = max(float(cap.get(cv2.CAP_PROP_FPS) or 25.0), 1.0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    start_frame = max(0, int(round(float(start_sec) * fps)))
    end_frame = (
        start_frame + max(1, int(round(float(max_seconds) * fps))) - 1
        if max_seconds is not None and max_seconds > 0
        else int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) - 1
    )
    rows_by_frame = _positions_by_frame(candidate_overlay_doc)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(".raw.avi")
    writer = cv2.VideoWriter(
        str(temp_path),
        cv2.VideoWriter_fourcc(*"MJPG"),
        fps,
        (width, height),
    )
    if not writer.isOpened():
        cap.release()
        raise RuntimeError(f"Could not open video writer: {temp_path}")
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    global_frame = start_frame
    written = 0
    try:
        while global_frame <= end_frame:
            ok, frame = cap.read()
            if not ok:
                break
            rows = rows_by_frame.get(global_frame) or []
            _draw_rows(frame, rows)
            _draw_hud(frame, global_frame, fps, rows, candidate_overlay_doc)
            writer.write(frame)
            written += 1
            global_frame += 1
    finally:
        cap.release()
        writer.release()
    if written == 0:
        temp_path.unlink(missing_ok=True)
        raise RuntimeError("Identity candidate overlay rendered zero frames.")
    _convert_to_mp4(temp_path, output_path)
    return output_path


def _positions_by_frame(candidate_overlay_doc: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    rows: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for player in candidate_overlay_doc.get("players") or []:
        for position in player.get("overlay_positions") or []:
            bbox = position.get("bbox_xyxy")
            if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
                continue
            rows[int(position.get("frame") or 0)].append(
                {
                    **position,
                    "candidate_player_id": player.get("stable_player_id"),
                    "candidate_subject_id": player.get("candidate_subject_id"),
                    "team_label": player.get("team_label"),
                    "requires_review": bool(player.get("requires_review")),
                }
            )
    for frame_rows in rows.values():
        frame_rows.sort(
            key=lambda row: (
                str(row.get("team_label") or "U"),
                str(row.get("candidate_player_id") or ""),
            )
        )
    return rows


def _draw_rows(frame: Any, rows: list[dict[str, Any]]) -> None:
    import cv2

    for row in rows:
        bbox = [int(round(float(value))) for value in row["bbox_xyxy"][:4]]
        x1, y1, x2, y2 = bbox
        color = TEAM_COLORS.get(str(row.get("team_label") or "U"), TEAM_COLORS["U"])
        source = str(row.get("source") or "detected")
        if source == "detected":
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        else:
            _draw_dashed_box(frame, (x1, y1, x2, y2), color)
        suffix = "" if source == "detected" else f" [{source[:3]}]"
        review = " !" if row.get("requires_review") else ""
        label = f"{row.get('candidate_player_id')}{suffix}{review}"
        _draw_label(frame, label, x1, max(18, y1 - 5), color)


def _draw_dashed_box(frame: Any, bbox: tuple[int, int, int, int], color: tuple[int, int, int]) -> None:
    import cv2

    x1, y1, x2, y2 = bbox
    dash = 8
    for start in range(x1, x2, dash * 2):
        cv2.line(frame, (start, y1), (min(start + dash, x2), y1), color, 2)
        cv2.line(frame, (start, y2), (min(start + dash, x2), y2), color, 2)
    for start in range(y1, y2, dash * 2):
        cv2.line(frame, (x1, start), (x1, min(start + dash, y2)), color, 2)
        cv2.line(frame, (x2, start), (x2, min(start + dash, y2)), color, 2)


def _draw_label(frame: Any, text: str, x: int, y: int, color: tuple[int, int, int]) -> None:
    import cv2

    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.5
    thickness = 1
    (width, height), baseline = cv2.getTextSize(text, font, scale, thickness)
    cv2.rectangle(frame, (x, y - height - 5), (x + width + 6, y + baseline + 2), (12, 16, 22), -1)
    cv2.putText(frame, text, (x + 3, y), font, scale, color, thickness, cv2.LINE_AA)


def _draw_hud(
    frame: Any,
    frame_index: int,
    fps: float,
    rows: list[dict[str, Any]],
    candidate_overlay_doc: dict[str, Any],
) -> None:
    import cv2

    statuses = Counter(str(row.get("source") or "detected") for row in rows)
    teams = Counter(str(row.get("team_label") or "U") for row in rows)
    review_count = sum(bool(row.get("requires_review")) for row in rows)
    active_roster = candidate_overlay_doc.get("mode") == "shadow_active_roster_validation"
    lines = [
        (
            "P1.6/P1.7 SHADOW ACTIVE ROSTER - VISUAL VALIDATION ONLY"
            if active_roster
            else "P1.5 SHADOW CANDIDATE - VISUAL VALIDATION ONLY"
        ),
        f"frame={frame_index}  t={frame_index / fps:.1f}s  visible={len(rows)}  A={teams['A']} B={teams['B']}",
        f"det={statuses['detected']} pred={statuses['predicted']} occ={statuses['occluded']} review={review_count}",
        f"subjects={int((candidate_overlay_doc.get('summary') or {}).get('candidate_subjects') or 0)}  stats=DISABLED",
    ]
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.52
    line_height = 22
    width = max(cv2.getTextSize(line, font, scale, 1)[0][0] for line in lines) + 18
    height = line_height * len(lines) + 12
    cv2.rectangle(frame, (8, 8), (8 + width, 8 + height), (8, 12, 18), -1)
    cv2.rectangle(frame, (8, 8), (8 + width, 8 + height), (180, 190, 200), 1)
    for index, line in enumerate(lines):
        color = (0, 210, 255) if index == 0 else (235, 235, 235)
        cv2.putText(frame, line, (16, 29 + index * line_height), font, scale, color, 1, cv2.LINE_AA)


def _convert_to_mp4(temp_path: Path, output_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        temp_path.unlink(missing_ok=True)
        raise RuntimeError("ffmpeg is required to encode the identity candidate overlay.")
    output_path.unlink(missing_ok=True)
    completed = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(temp_path),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    temp_path.unlink(missing_ok=True)
    if completed.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {completed.stderr.strip()}")
