from __future__ import annotations

from pathlib import Path
import csv
import hashlib
import json
import shutil
import subprocess
from typing import Any

import cv2


VIDEO_TIMEBASE_SCHEMA_VERSION = "1.0.0"
VIDEO_TIMEBASE_MODE = "decoded_cfr_frame_index"
# OpenCV normally rounds rational rates such as 30000/1001, while ffprobe
# exposes a finite decimal PTS interval.  This accepts that representation
# difference but is deliberately far below a one-frame-rate mismatch.
FPS_ABSOLUTE_TOLERANCE = 0.02
FPS_RELATIVE_TOLERANCE = 0.0005


class VideoTimebaseError(ValueError):
    """A source cannot safely support the frame-index analysis timeline."""


def resolve_match_video_path(
    match_path: Path,
    preferred_filename: str | None = None,
) -> Path:
    """Resolve the canonical uploaded video without assuming an MP4 container."""
    match_root = match_path.resolve()
    if preferred_filename:
        preferred = (match_path / Path(preferred_filename).name).resolve()
        if preferred.parent == match_root and preferred.is_file():
            return preferred
    candidates = sorted(
        candidate.resolve()
        for candidate in match_path.glob("video.*")
        if candidate.is_file()
    )
    if not candidates:
        raise FileNotFoundError("Video file not found")
    return candidates[0]


def read_match_video_metadata(
    match_path: Path,
    match_document: dict[str, Any] | None = None,
) -> dict[str, Any]:
    preferred = str((match_document or {}).get("video_filename") or "") or None
    video_path = resolve_match_video_path(match_path, preferred)
    persisted = (match_document or {}).get("video")
    if isinstance(persisted, dict) and persisted.get("timebase_schema_version") == VIDEO_TIMEBASE_SCHEMA_VERSION:
        return {**persisted, "path": str(video_path), "filename": video_path.name}
    # Historical documents predate the explicit timebase.  Preserve their
    # read-only behaviour; an upload or explicit publication rebuild records
    # a proven timebase before it may become canonical again.
    return {**read_video_metadata(video_path), "source": "opencv_nominal_metadata", "filename": video_path.name}


def fps_matches(left: float, right: float) -> bool:
    """Whether two representations of one CFR cadence agree strictly."""
    if left <= 0 or right <= 0:
        return False
    return abs(left - right) <= max(FPS_ABSOLUTE_TOLERANCE, right * FPS_RELATIVE_TOLERANCE)


def timebase_matches_source(video_path: Path, timebase: dict[str, Any]) -> bool:
    """Expensive proof check for mutation/render boundaries only.

    Ordinary report reads intentionally do not call this: SHA256 binds a
    persisted frame timeline to source bytes, not just its dimensions/rate.
    """
    fingerprint = timebase.get("source_fingerprint")
    return isinstance(fingerprint, dict) and fingerprint == _video_fingerprint(video_path)


def ensure_current_video_timebase(
    match_path: Path,
    match_document: dict[str, Any],
) -> dict[str, Any]:
    """Return the proven timebase, re-inspecting when source bytes changed.

    This is intentionally an explicit expensive-boundary helper.  Callers
    persist its returned value only after their surrounding lifecycle has
    passed validation.
    """
    preferred = str(match_document.get("video_filename") or "") or None
    video_path = resolve_match_video_path(match_path, preferred)
    persisted = match_document.get("video")
    if (
        isinstance(persisted, dict)
        and persisted.get("timebase_schema_version") == VIDEO_TIMEBASE_SCHEMA_VERSION
        and timebase_matches_source(video_path, persisted)
    ):
        return {**persisted, "path": str(video_path), "filename": video_path.name}
    return inspect_video_timebase(video_path)


def read_video_metadata(video_path: Path) -> dict[str, Any]:
    """Fast nominal OpenCV metadata; not proof of media duration."""
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


def inspect_video_timebase(video_path: Path) -> dict[str, Any]:
    """Prove a compact CFR frame-to-media contract during upload/rebuild only.

    This intentionally decodes the source and streams ffprobe PTS output.  It
    must never be used while serving normal report reads.
    """
    nominal = read_video_metadata(video_path)
    nominal_fps = float(nominal["fps"])
    if nominal_fps <= 0:
        raise VideoTimebaseError("video_timebase_unavailable: invalid nominal FPS")
    decoded = _decode_frame_count(video_path)
    if decoded <= 0:
        raise VideoTimebaseError("video_decode_truncated: no decodable video frames")
    pts = _inspect_pts(video_path)
    if pts["classification"] != "effectively_cfr":
        raise VideoTimebaseError(f"video_timestamp_discontinuous: {pts['classification']}")
    if int(pts["frame_timestamp_count"]) != decoded:
        raise VideoTimebaseError("video_decode_frame_count_mismatch: OpenCV and ffprobe disagree")
    pts_fps = float(pts["pts_derived_fps"])
    if not fps_matches(nominal_fps, pts_fps):
        raise VideoTimebaseError("video_fps_mismatch: OpenCV nominal FPS disagrees with ffprobe PTS cadence")
    # PTS cadence, not CAP_PROP_FPS, is the authority for frame-index time.
    fps = pts_fps
    return {
        "path": str(video_path),
        "fps": fps,
        "nominal_fps": nominal_fps,
        "pts_frame_interval_sec": float(pts["typical_frame_interval_sec"]),
        "pts_derived_fps": pts_fps,
        "frame_count": decoded,
        "width": int(nominal["width"]),
        "height": int(nominal["height"]),
        # Existing analytics use duration_sec as their frame-domain coverage.
        "duration_sec": round(decoded / fps, 3),
        "analysis_duration_sec": round(decoded / fps, 6),
        "media_duration_sec": round(float(pts["estimated_media_span_sec"]), 6),
        "first_media_timestamp_sec": pts["first_timestamp_sec"],
        "last_media_timestamp_sec": pts["last_timestamp_sec"],
        "timing_mode": VIDEO_TIMEBASE_MODE,
        "timebase_schema_version": VIDEO_TIMEBASE_SCHEMA_VERSION,
        # Existing stats/report consumers rely on this compatibility field.
        "source": "decoded_cfr_timebase",
        "timing_source": "sequential_decode_and_ffprobe_pts",
        "nominal_frame_count": int(nominal["frame_count"]),
        "nominal_duration_sec": float(nominal["duration_sec"]),
        "source_fingerprint": _video_fingerprint(video_path),
    }


def _decode_frame_count(video_path: Path) -> int:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise VideoTimebaseError(f"video_timebase_unavailable: could not open {video_path}")
    count = 0
    try:
        while True:
            ok, _frame = capture.read()
            if not ok:
                return count
            count += 1
    finally:
        capture.release()


def _inspect_pts(video_path: Path) -> dict[str, Any]:
    if not shutil.which("ffprobe"):
        raise VideoTimebaseError("video_timebase_unavailable: ffprobe is required")
    command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames",
        "-show_entries", "frame=best_effort_timestamp_time,pkt_duration_time", "-of", "csv=p=0", str(video_path),
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert process.stdout is not None
    first: float | None = None
    previous: float | None = None
    last: float | None = None
    intervals: list[float] = []
    count = duplicates = backwards = 0
    try:
        for row in csv.reader(process.stdout):
            try:
                timestamp = float(row[0])
            except (IndexError, ValueError):
                continue
            count += 1
            first = timestamp if first is None else first
            if previous is not None:
                interval = timestamp - previous
                intervals.append(interval)
                duplicates += interval == 0
                backwards += interval < 0
            previous = last = timestamp
    finally:
        process.stdout.close()
        stderr = process.stderr.read() if process.stderr else ""
        if process.stderr:
            process.stderr.close()
        if process.wait() != 0:
            raise VideoTimebaseError(f"video_timebase_unavailable: ffprobe failed: {stderr.strip()}")
    positive = sorted(interval for interval in intervals if interval > 0)
    if not positive or first is None or last is None:
        raise VideoTimebaseError("video_timebase_unavailable: frame timestamps unavailable")
    typical = positive[len(positive) // 2]
    unusual = sum(abs(interval - typical) > max(0.001, typical * 0.01) for interval in positive)
    classification = "effectively_cfr" if not (duplicates or backwards or unusual) else "irregular_timestamps"
    return {
        "frame_timestamp_count": count,
        "first_timestamp_sec": first,
        "last_timestamp_sec": last,
        "estimated_media_span_sec": last - first + typical,
        "typical_frame_interval_sec": typical,
        "pts_derived_fps": 1.0 / typical,
        "classification": classification,
    }


def _video_fingerprint(video_path: Path) -> dict[str, Any]:
    stat = video_path.stat()
    digest = hashlib.sha256()
    with video_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"size_bytes": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns), "sha256": digest.hexdigest()}


def probe_media_duration(video_path: Path) -> float:
    """Read container media duration independently of OpenCV frame metadata."""
    if not shutil.which("ffprobe"):
        raise VideoTimebaseError("video_timebase_unavailable: ffprobe is required")
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(video_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        duration = float(result.stdout.strip())
    except ValueError as exc:
        raise VideoTimebaseError("video_timebase_unavailable: media duration unavailable") from exc
    if result.returncode != 0 or duration <= 0:
        raise VideoTimebaseError("video_timebase_unavailable: ffprobe duration failed")
    return duration


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
