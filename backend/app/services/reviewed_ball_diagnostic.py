from __future__ import annotations

"""Local-only renderer for reviewing persisted ball evidence over reviewed video."""

from pathlib import Path
import shutil
import subprocess
from typing import Any, Iterable, Mapping

from app.services.identity_reviewed_video import draw_ball_evidence_on_main_frame
from app.services.video import probe_media_duration


DIAGNOSTIC_RENDERER_VERSION = "reviewed-ball-diagnostic:v1"


def render_reviewed_ball_diagnostic(
    reviewed_video_path: Path,
    ball_tracks_doc: Mapping[str, Any],
    output_path: Path,
    *,
    target_duration_sec: float | None = None,
    start_sec: float | None = None,
    end_sec: float | None = None,
    operator_title: str | None = None,
    highlight_time_sec: float | None = None,
) -> dict[str, Any]:
    """Overlay persisted selected ball evidence without changing the source video.

    The input is the already-rendered reviewed video, preserving its canonical
    reviewed player boxes and labels. This function never invokes YOLO or a
    tracker and writes only the requested diagnostic output path.  ``start_sec``
    and ``end_sec`` allow a small operator-review clip to be rendered without
    decoding a separate full-match diagnostic artifact.
    """

    import cv2

    if not reviewed_video_path.is_file():
        raise FileNotFoundError(reviewed_video_path)
    if reviewed_video_path.resolve() == output_path.resolve():
        raise ValueError("diagnostic_output_must_not_overwrite_reviewed_video")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    balls = _ball_rows_by_frame(ball_tracks_doc)
    capture = cv2.VideoCapture(str(reviewed_video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open reviewed video: {reviewed_video_path}")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if fps <= 0 or width <= 0 or height <= 0:
        capture.release()
        raise RuntimeError("reviewed_video_metadata_unavailable")
    source_duration_sec = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0) / fps
    clip_start_sec, clip_end_sec = _clip_bounds(start_sec, end_sec, source_duration_sec)
    first_frame = max(0, int(round(clip_start_sec * fps)))
    last_frame_exclusive = min(
        int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0),
        int(round(clip_end_sec * fps)),
    )
    if last_frame_exclusive <= first_frame:
        capture.release()
        raise ValueError("diagnostic_clip_has_no_frames")
    capture.set(cv2.CAP_PROP_POS_FRAMES, first_frame)

    source_fps = fps
    encoded_fps = fps
    source_frame_count = float(last_frame_exclusive - first_frame)
    if target_duration_sec is not None:
        if target_duration_sec <= 0 or source_frame_count <= 0:
            capture.release()
            raise ValueError("diagnostic_target_duration_invalid")
        encoded_fps = source_frame_count / target_duration_sec
    partial = output_path.with_name(f".{output_path.stem}.partial.mp4")
    partial.unlink(missing_ok=True)
    command = [
        _require_ffmpeg(), "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s:v", f"{width}x{height}",
        "-r", f"{encoded_fps:.12g}", "-i", "pipe:0", "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(partial),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if process.stdin is None or process.stderr is None:
        capture.release()
        process.kill()
        raise RuntimeError("diagnostic_encoder_stdin_unavailable")

    counts = {"detected_bbox": 0, "detected_marker": 0, "interpolated_marker": 0, "unknown": 0}
    frame_count = 0
    try:
        frame_index = first_frame
        while frame_index < last_frame_exclusive:
            ok, frame = capture.read()
            if not ok:
                break
            state = draw_ball_evidence_on_main_frame(frame, balls.get(frame_index))
            _draw_operator_review_overlay(
                frame,
                operator_title=operator_title,
                frame_time_sec=frame_index / fps,
                highlight_time_sec=highlight_time_sec,
            )
            counts[state] += 1
            process.stdin.write(frame.tobytes())
            frame_count += 1
            frame_index += 1
        process.stdin.close()
        stderr = process.stderr.read().decode("utf-8", errors="replace")
        if process.wait() != 0:
            raise RuntimeError(f"diagnostic_video_encoding_failed: {stderr.strip()}")
        if frame_count == 0:
            raise RuntimeError("diagnostic_renderer_produced_zero_frames")
        partial.replace(output_path)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    finally:
        capture.release()
        if process.poll() is None:
            process.kill()
            process.wait()

    return {
        "renderer_version": DIAGNOSTIC_RENDERER_VERSION,
        "source_reviewed_video": str(reviewed_video_path),
        "source_window_sec": [round(clip_start_sec, 3), round(clip_end_sec, 3)],
        "highlight_time_sec": round(highlight_time_sec, 3) if highlight_time_sec is not None else None,
        "frames": frame_count,
        "source_fps": source_fps,
        "fps": encoded_fps,
        "resolution": [width, height],
        "duration_sec": round(frame_count / encoded_fps, 3),
        "media_duration_sec": round(probe_media_duration(output_path), 6),
        "file_size_bytes": output_path.stat().st_size,
        "ball_evidence_frames": counts,
    }


def _clip_bounds(
    start_sec: float | None,
    end_sec: float | None,
    source_duration_sec: float,
) -> tuple[float, float]:
    if source_duration_sec <= 0:
        raise ValueError("diagnostic_source_duration_invalid")
    start = 0.0 if start_sec is None else max(0.0, float(start_sec))
    end = source_duration_sec if end_sec is None else min(source_duration_sec, float(end_sec))
    if end <= start:
        raise ValueError("diagnostic_clip_bounds_invalid")
    return start, end


def _draw_operator_review_overlay(
    frame: Any,
    *,
    operator_title: str | None,
    frame_time_sec: float,
    highlight_time_sec: float | None,
) -> None:
    """Add only human-readable operator context to a short review clip."""

    if not operator_title and highlight_time_sec is None:
        return
    import cv2

    height, width = frame.shape[:2]
    cv2.rectangle(frame, (0, 0), (width, 52), (8, 20, 34), -1)
    if operator_title:
        cv2.putText(
            frame,
            operator_title,
            (18, 33),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.78,
            (240, 248, 255),
            2,
            cv2.LINE_AA,
        )
    if highlight_time_sec is None or abs(frame_time_sec - highlight_time_sec) > 0.28:
        return
    label = "MOZLIWY KONTAKT"
    (label_width, label_height), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.95, 3)
    left = max(16, (width - label_width) // 2 - 16)
    top = max(62, height - label_height - baseline - 44)
    cv2.rectangle(frame, (left, top), (left + label_width + 32, top + label_height + baseline + 22), (0, 110, 245), -1)
    cv2.putText(
        frame,
        label,
        (left + 16, top + label_height + 4),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.95,
        (255, 255, 255),
        3,
        cv2.LINE_AA,
    )


def concatenate_reviewed_ball_diagnostics(inputs: Iterable[Path], output_path: Path) -> dict[str, Any]:
    """Concatenate local diagnostics without touching match-group video state."""

    paths = list(inputs)
    if not paths or not all(path.is_file() for path in paths):
        raise ValueError("diagnostic_concat_inputs_missing")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial = output_path.with_name(f".{output_path.stem}.partial.mp4")
    partial.unlink(missing_ok=True)
    command = [_require_ffmpeg(), "-y", "-loglevel", "error"]
    for path in paths:
        command.extend(["-i", str(path)])
    chains = ";".join(f"[{index}:v]setpts=PTS-STARTPTS,fps=30000/1001[v{index}]" for index in range(len(paths)))
    joined = "".join(f"[v{index}]" for index in range(len(paths)))
    command.extend([
        "-filter_complex", f"{chains};{joined}concat=n={len(paths)}:v=1:a=0[v]",
        "-map", "[v]", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(partial),
    ])
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"diagnostic_concat_failed: {completed.stderr.strip()}")
    partial.replace(output_path)
    return {
        "renderer_version": DIAGNOSTIC_RENDERER_VERSION,
        "source_count": len(paths),
        "media_duration_sec": round(probe_media_duration(output_path), 6),
        "file_size_bytes": output_path.stat().st_size,
    }


def _ball_rows_by_frame(document: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
    return {
        int(row.get("frame") or 0): dict(row)
        for row in document.get("positions") or []
        if isinstance(row, Mapping)
    }


def _require_ffmpeg() -> str:
    executable = shutil.which("ffmpeg")
    if not executable:
        raise RuntimeError("ffmpeg_required_for_diagnostic_video")
    return executable
