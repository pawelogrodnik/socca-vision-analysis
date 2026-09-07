#!/usr/bin/env python3
"""Read-only forensic audit of an input video's decode and media timelines.

Use this for a match directory or a video path. It deliberately decodes to EOF
and asks ffprobe for frame timestamps, so it is not used by ordinary API reads.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from statistics import median
from typing import Any, Iterator

import cv2


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_video(path: Path) -> Path:
    if path.is_file():
        return path
    candidates = sorted(candidate for candidate in path.glob("video.*") if candidate.is_file())
    if len(candidates) != 1:
        raise ValueError(f"Expected exactly one video.* file in {path}")
    return candidates[0]


def _opencv_metadata_and_decode(path: Path) -> dict[str, Any]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError(f"OpenCV could not open {path}")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    nominal_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    decoded_frames = 0
    last_position_msec: float | None = None
    while True:
        ok, _frame = capture.read()
        if not ok:
            break
        decoded_frames += 1
        last_position_msec = float(capture.get(cv2.CAP_PROP_POS_MSEC) or 0.0)
    capture.release()
    return {
        "fps": fps,
        "nominal_frame_count": nominal_frames,
        "nominal_duration_sec": nominal_frames / fps if fps else None,
        "width": width,
        "height": height,
        "decoded_frame_count": decoded_frames,
        "decoded_duration_at_nominal_fps_sec": decoded_frames / fps if fps else None,
        "decoded_minus_nominal_frames": decoded_frames - nominal_frames,
        "last_decoded_capture_pos_msec": last_position_msec,
    }


def _ffprobe_json(path: Path, *, count_frames: bool = False) -> dict[str, Any]:
    command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=index,codec_name,pix_fmt,time_base,start_time,duration,avg_frame_rate,r_frame_rate,nb_frames",
        "-show_entries", "format=start_time,duration", "-of", "json",
    ]
    if count_frames:
        command[1:1] = ["-count_frames"]
        command[command.index("-show_entries")] = "-show_entries"
        command[command.index("stream=index,codec_name,pix_fmt,time_base,start_time,duration,avg_frame_rate,r_frame_rate,nb_frames")] = "stream=nb_read_frames,nb_frames,duration,time_base,avg_frame_rate,r_frame_rate"
    completed = subprocess.run([*command, str(path)], check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def _frame_pts(path: Path) -> Iterator[tuple[float | None, float | None]]:
    command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames",
        "-show_entries", "frame=best_effort_timestamp_time,pkt_duration_time", "-of", "csv=p=0", str(path),
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert process.stdout is not None
    try:
        for row in csv.reader(process.stdout):
            values = [value.strip() for value in row]
            timestamp = _number_or_none(values[0] if values else None)
            duration = _number_or_none(values[1] if len(values) > 1 else None)
            yield timestamp, duration
    finally:
        process.stdout.close()
        stderr = process.stderr.read() if process.stderr else ""
        result = process.wait()
        if result:
            raise RuntimeError(f"ffprobe frame timestamp read failed: {stderr.strip()}")


def _number_or_none(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "", "N/A") else None
    except ValueError:
        return None


def _pts_summary(path: Path) -> dict[str, Any]:
    first: float | None = None
    last: float | None = None
    last_duration: float | None = None
    intervals: list[float] = []
    previous: float | None = None
    duplicates = 0
    backwards = 0
    count = 0
    for timestamp, duration in _frame_pts(path):
        if timestamp is None:
            continue
        count += 1
        if first is None:
            first = timestamp
        if previous is not None:
            interval = timestamp - previous
            intervals.append(interval)
            if interval == 0:
                duplicates += 1
            elif interval < 0:
                backwards += 1
        previous = timestamp
        last = timestamp
        last_duration = duration
    positive = [interval for interval in intervals if interval > 0]
    typical = median(positive) if positive else None
    unusual = (
        sum(1 for interval in positive if typical and abs(interval - typical) > max(0.001, typical * 0.01))
        if typical else 0
    )
    final_duration = last_duration if last_duration and last_duration > 0 else typical
    span = (last - first + final_duration) if first is not None and last is not None and final_duration else None
    return {
        "frame_timestamp_count": count,
        "first_timestamp_sec": first,
        "last_timestamp_sec": last,
        "last_frame_duration_sec": last_duration,
        "estimated_media_span_sec": span,
        "median_frame_interval_sec": typical,
        "min_frame_interval_sec": min(positive) if positive else None,
        "max_frame_interval_sec": max(positive) if positive else None,
        "unusual_positive_gap_count": unusual,
        "duplicate_timestamp_count": duplicates,
        "backwards_timestamp_count": backwards,
        "classification": _pts_classification(typical, unusual, duplicates, backwards),
    }


def _pts_classification(typical: float | None, unusual: int, duplicates: int, backwards: int) -> str:
    if not typical:
        return "timestamp_unavailable"
    if backwards:
        return "timestamp_discontinuous"
    if duplicates or unusual:
        return "variable_or_irregular_timestamps"
    return "effectively_cfr"


def audit(path: Path) -> dict[str, Any]:
    if not shutil.which("ffprobe"):
        raise RuntimeError("ffprobe is required for a timebase audit")
    video = _resolve_video(path)
    stat = video.stat()
    return {
        "path": str(video.resolve()),
        "filename": video.name,
        "size_bytes": stat.st_size,
        "sha256": _sha256(video),
        "opencv": _opencv_metadata_and_decode(video),
        "ffprobe": {
            "metadata": _ffprobe_json(video),
            "count_frames": _ffprobe_json(video, count_frames=True),
            "pts": _pts_summary(video),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", type=Path, nargs="+", help="Video files or match directories containing video.*")
    arguments = parser.parse_args()
    try:
        print(json.dumps([audit(path) for path in arguments.paths], ensure_ascii=False, indent=2))
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"timebase audit failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
