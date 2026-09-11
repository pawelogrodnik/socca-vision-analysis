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
) -> dict[str, Any]:
    """Overlay persisted selected ball evidence without changing the source video.

    The input is the already-rendered reviewed video, preserving its canonical
    reviewed player boxes and labels. This function never invokes YOLO or a
    tracker and writes only the requested diagnostic output path.
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

    source_fps = fps
    encoded_fps = fps
    source_frame_count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
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
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            state = draw_ball_evidence_on_main_frame(frame, balls.get(frame_count))
            counts[state] += 1
            process.stdin.write(frame.tobytes())
            frame_count += 1
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
        "frames": frame_count,
        "source_fps": source_fps,
        "fps": encoded_fps,
        "resolution": [width, height],
        "duration_sec": round(frame_count / encoded_fps, 3),
        "media_duration_sec": round(probe_media_duration(output_path), 6),
        "file_size_bytes": output_path.stat().st_size,
        "ball_evidence_frames": counts,
    }


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
