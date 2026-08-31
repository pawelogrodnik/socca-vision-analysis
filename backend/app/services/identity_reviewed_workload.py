from __future__ import annotations

"""Compact workload presentation derived from reviewed player observations.

This intentionally operates on the already-filtered Reviewed Identity rows.  It
does not infer official stints or materialize a client-facing observation
timeline: the result is one small row per player and available-video window.
"""

from typing import Any

from app.services.global_identity import (
    HIGH_INTENSITY_THRESHOLD_KMH,
    MAX_STATS_ESTIMATED_GAP_SEC,
    MAX_STATS_SPEED_MPS,
    STATS_OBSERVED_GAP_FRAMES,
    STATS_PEAK_SPEED_MAX_SEGMENT_GAP_SEC,
)


RATE_WINDOW_SEC = 300.0
WORKLOAD_MIN_RATE_SAMPLE_SEC = 120.0
WORKLOAD_BEST_WINDOW_MIN_SAMPLE_SEC = 180.0
WORKLOAD_SEMANTICS = "reviewed_confirmed_detected_in_play"


def build_reviewed_player_workload(
    fragments: list[list[dict[str, Any]]],
    *,
    fps: float,
    video_duration_sec: float,
    canonical: dict[str, Any],
) -> dict[str, Any]:
    """Build actual-source-video workload windows for one reviewed player.

    Accepted movement segments and sprint runs are allocated once to the window
    containing their start time.  This deliberately avoids copying a segment or
    sprint across a five-minute boundary.
    """
    windows = _empty_windows(video_duration_sec)
    fps_safe = max(float(fps), 0.001)
    rows_by_frame: dict[int, dict[str, Any]] = {}
    for fragment in fragments:
        for row in fragment:
            frame = int(row.get("frame") or 0)
            existing = rows_by_frame.get(frame)
            if existing is None or str(row.get("tracklet_id") or "") < str(existing.get("tracklet_id") or ""):
                rows_by_frame[frame] = row

    for row in rows_by_frame.values():
        _window_for_time(windows, _time_sec(row, fps_safe))["detected_time_sec"] += 1.0 / fps_safe

    accepted_segments: list[dict[str, Any]] = []
    for fragment in fragments:
        accepted_segments.extend(_accepted_segments(fragment, fps_safe))
    for segment in accepted_segments:
        window = _window_for_time(windows, float(segment["start_time_sec"]))
        distance = float(segment["distance_m"])
        if segment["kind"] == "observed":
            window["observed_distance_m"] += distance
        else:
            window["estimated_short_gap_distance_m"] += distance
        if (
            segment["kind"] == "observed"
            and float(segment["end_time_sec"]) - float(segment["start_time_sec"])
            <= STATS_PEAK_SPEED_MAX_SEGMENT_GAP_SEC
            and float(segment["speed_mps"]) >= HIGH_INTENSITY_THRESHOLD_KMH / 3.6
        ):
            window["high_intensity_distance_m"] += distance
            window["high_intensity_time_sec"] += float(segment["end_time_sec"]) - float(segment["start_time_sec"])

    # The event list is supplied by the Reviewed sprint classifier.  Windows
    # only allocate that canonical result; they must never re-classify sprints.
    for sprint in canonical.get("sprint_events") or []:
        if not isinstance(sprint, dict):
            continue
        _window_for_time(windows, float(sprint["start_time_sec"]))["sprint_count"] += 1

    for window in windows:
        window["detected_time_sec"] = round(window["detected_time_sec"], 3)
        window["observed_distance_m"] = round(window["observed_distance_m"], 2)
        window["estimated_short_gap_distance_m"] = round(window["estimated_short_gap_distance_m"], 2)
        window["total_distance_m"] = round(
            window["observed_distance_m"] + window["estimated_short_gap_distance_m"], 2
        )
        window["high_intensity_distance_m"] = round(window["high_intensity_distance_m"], 2)
        eligible = window["detected_time_sec"] >= WORKLOAD_MIN_RATE_SAMPLE_SEC
        window["rate_status"] = "reportable" if eligible else "insufficient_detected_sample"
        window["distance_per_5min_m"] = _rate(window["total_distance_m"], window["detected_time_sec"], eligible)
        window["high_intensity_distance_per_5min_m"] = _rate(
            window["high_intensity_distance_m"], window["detected_time_sec"], eligible
        )
        window["sprints_per_5min"] = _rate(window["sprint_count"], window["detected_time_sec"], eligible)

    # Full-player rates deliberately use the existing Reviewed Stats authority.
    # Windows remain a compact temporal presentation breakdown and must never
    # become a second source of truth for player totals.
    detected_time = _number(canonical.get("detected_time_sec"))
    total_distance = _number(canonical.get("total_distance_m"))
    high_intensity_distance = _number(canonical.get("high_intensity_distance_m"))
    high_intensity_time = _number(canonical.get("high_intensity_time_sec"))
    sprint_count = int(canonical.get("sprint_count") or 0)
    eligible_total = detected_time >= WORKLOAD_MIN_RATE_SAMPLE_SEC
    best = max(
        (
            window
            for window in windows
            if window["distance_per_5min_m"] is not None
            and window["detected_time_sec"] >= WORKLOAD_BEST_WINDOW_MIN_SAMPLE_SEC
        ),
        key=lambda window: float(window["distance_per_5min_m"]),
        default=None,
    )
    return {
        "semantics": WORKLOAD_SEMANTICS,
        "rate_window_sec": RATE_WINDOW_SEC,
        "minimum_rate_sample_sec": WORKLOAD_MIN_RATE_SAMPLE_SEC,
        "minimum_best_window_sample_sec": WORKLOAD_BEST_WINDOW_MIN_SAMPLE_SEC,
        "detected_time_sec": detected_time,
        "distance_per_5min_m": _rate(total_distance, detected_time, eligible_total),
        "high_intensity_distance_per_5min_m": _rate(
            high_intensity_distance, detected_time, eligible_total
        ),
        "sprints_per_5min": _rate(sprint_count, detected_time, eligible_total),
        "high_intensity_distance_ratio": (
            round(high_intensity_distance / total_distance, 4) if total_distance > 0 else None
        ),
        "high_intensity_time_sec": high_intensity_time,
        "high_intensity_distance_m": high_intensity_distance,
        "sprint_count": sprint_count,
        "sprint_time_sec": _number(canonical.get("sprint_time_sec")),
        "sprint_distance_m": _number(canonical.get("sprint_distance_m")),
        "max_sprint_speed_kmh": _number(canonical.get("max_sprint_speed_kmh")),
        "activity_windows": windows,
        "best_activity_window": _best_window(best),
    }


def _empty_windows(video_duration_sec: float) -> list[dict[str, Any]]:
    duration = max(0.0, float(video_duration_sec or 0.0))
    windows: list[dict[str, Any]] = []
    start = 0.0
    while start < duration:
        end = min(duration, start + RATE_WINDOW_SEC)
        windows.append(
            {
                "window_index": len(windows),
                "start_time_sec": round(start, 3),
                "end_time_sec": round(end, 3),
                "duration_sec": round(end - start, 3),
                "display_label": _compact_window_label(start, end),
                "detected_time_sec": 0.0,
                "observed_distance_m": 0.0,
                "estimated_short_gap_distance_m": 0.0,
                "high_intensity_distance_m": 0.0,
                "high_intensity_time_sec": 0.0,
                "sprint_count": 0,
            }
        )
        start = end
    return windows


def _compact_window_label(start_time_sec: float, end_time_sec: float) -> str:
    """Return a compact minute range without collapsing a non-empty interval."""
    start_minute = int(start_time_sec // 60)
    end_minute = max(start_minute + 1, int(end_time_sec // 60))
    return f"{start_minute}–{end_minute}"


def _window_for_time(windows: list[dict[str, Any]], time_sec: float) -> dict[str, Any]:
    if not windows:
        raise ValueError("Workload windows require a positive source-video duration")
    index = min(len(windows) - 1, max(0, int(max(0.0, time_sec) // RATE_WINDOW_SEC)))
    return windows[index]


def _accepted_segments(rows: list[dict[str, Any]], fps: float) -> list[dict[str, Any]]:
    accepted: list[dict[str, Any]] = []
    for previous, current in zip(rows, rows[1:]):
        if not _valid_pitch(previous.get("pitch_m")) or not _valid_pitch(current.get("pitch_m")):
            continue
        previous_frame = int(previous.get("frame") or 0)
        current_frame = int(current.get("frame") or 0)
        frame_gap = current_frame - previous_frame
        if frame_gap <= 0:
            continue
        start_time = _time_sec(previous, fps)
        end_time = _time_sec(current, fps)
        elapsed = max(1.0 / fps, end_time - start_time)
        distance = _distance(previous["pitch_m"], current["pitch_m"])
        speed = distance / elapsed
        if speed > MAX_STATS_SPEED_MPS:
            continue
        if frame_gap <= STATS_OBSERVED_GAP_FRAMES:
            kind = "observed"
        elif elapsed <= MAX_STATS_ESTIMATED_GAP_SEC:
            kind = "estimated"
        else:
            continue
        accepted.append(
            {
                "start_frame": previous_frame,
                "end_frame": current_frame,
                "start_time_sec": start_time,
                "end_time_sec": end_time,
                "distance_m": distance,
                "speed_mps": speed,
                "kind": kind,
                "tracklet_id": str(previous.get("tracklet_id") or ""),
            }
        )
    return accepted


def _rate(value: float | int, detected_time_sec: float, eligible: bool) -> float | None:
    if not eligible or detected_time_sec <= 0:
        return None
    return round(float(value) / detected_time_sec * RATE_WINDOW_SEC, 2)


def _best_window(window: dict[str, Any] | None) -> dict[str, Any] | None:
    if window is None:
        return None
    keys = (
        "window_index", "display_label", "start_time_sec", "end_time_sec", "detected_time_sec",
        "total_distance_m", "distance_per_5min_m", "high_intensity_distance_m", "sprint_count",
    )
    return {key: window[key] for key in keys}


def _time_sec(row: dict[str, Any], fps: float) -> float:
    return float(row.get("time_sec") if row.get("time_sec") is not None else int(row.get("frame") or 0) / fps)


def _valid_pitch(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2])


def _distance(left: list[float], right: list[float]) -> float:
    return ((float(right[0]) - float(left[0])) ** 2 + (float(right[1]) - float(left[1])) ** 2) ** 0.5


def _number(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
