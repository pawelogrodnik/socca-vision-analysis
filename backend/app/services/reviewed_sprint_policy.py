from __future__ import annotations

"""One conservative, player-relative sprint classifier for Reviewed Stats.

This deliberately lives outside the legacy global movement helper: legacy
artifacts retain their historic fixed 20 km/h semantics, while the Reviewed
report and its five-minute windows must use exactly the same event list.
"""

from typing import Any

from app.services.global_identity import (
    MAX_STATS_ESTIMATED_GAP_SEC,
    MAX_STATS_SPEED_MPS,
    STATS_OBSERVED_GAP_FRAMES,
    STATS_PEAK_SPEED_MAX_SEGMENT_GAP_SEC,
    sustained_speed_windows,
)


SPRINT_POLICY = "player_relative_v3_burst_calibration"
SPRINT_START_RATIO = 0.82
SPRINT_START_FLOOR_KMH = 16.5
SPRINT_CONTINUE_RATIO = 0.75
SPRINT_CONTINUE_FLOOR_KMH = 15.0
SPRINT_FALLBACK_START_KMH = 18.0
SPRINT_FALLBACK_CONTINUE_KMH = 16.0
SPRINT_MIN_DURATION_SEC = 0.4
SPRINT_ALLOWED_DIP_SEC = 0.2
SPRINT_MIN_REFERENCE_SAMPLE_SEC = 120.0
SPRINT_COALESCE_MAX_GAP_SEC = 0.75
SPRINT_ACCELERATION_CONTEXT_SEC = 0.5
SPRINT_ACCELERATION_BAND_KMH = 1.5
SPRINT_MIN_ACCELERATION_KMH = 1.5
SPRINT_COALESCE_MAX_BRIDGE_SPEED_MPS = 12.0


def reviewed_sprint_policy_matches_artifact(artifact: dict[str, Any] | None) -> bool:
    """Return whether a Reviewed artifact was built with this sprint policy."""
    return bool(artifact and artifact.get("sprint_policy_version") == SPRINT_POLICY)


def reviewed_sprint_policy(
    *,
    peak_sustained_speed_kmh: float,
    speed_quality: str,
    detected_time_sec: float,
) -> dict[str, Any]:
    reliable_reference = (
        peak_sustained_speed_kmh > 0
        and speed_quality != "low"
        and detected_time_sec >= SPRINT_MIN_REFERENCE_SAMPLE_SEC
    )
    if reliable_reference:
        start = max(SPRINT_START_FLOOR_KMH, peak_sustained_speed_kmh * SPRINT_START_RATIO)
        continuation = max(SPRINT_CONTINUE_FLOOR_KMH, peak_sustained_speed_kmh * SPRINT_CONTINUE_RATIO)
        source = "current_match_peak_sustained"
    else:
        start = SPRINT_FALLBACK_START_KMH
        continuation = SPRINT_FALLBACK_CONTINUE_KMH
        source = "fallback_absolute"
    return {
        "policy": SPRINT_POLICY,
        "reference_source": source,
        "reference_peak_sustained_speed_kmh": round(float(peak_sustained_speed_kmh or 0.0), 2),
        "start_threshold_kmh": round(start, 2),
        "continue_threshold_kmh": round(continuation, 2),
        "minimum_duration_sec": SPRINT_MIN_DURATION_SEC,
        "allowed_dip_sec": SPRINT_ALLOWED_DIP_SEC,
        "coalesce_max_gap_sec": SPRINT_COALESCE_MAX_GAP_SEC,
        "acceleration_context_sec": SPRINT_ACCELERATION_CONTEXT_SEC,
        "acceleration_band_kmh": SPRINT_ACCELERATION_BAND_KMH,
        "minimum_acceleration_kmh": SPRINT_MIN_ACCELERATION_KMH,
        "coalesce_max_bridge_speed_mps": SPRINT_COALESCE_MAX_BRIDGE_SPEED_MPS,
    }


def classify_reviewed_sprints(
    fragments: list[list[dict[str, Any]]],
    *,
    fps: float,
    policy: dict[str, Any],
) -> dict[str, Any]:
    """Return accepted sprint events plus compact diagnostics.

    A run can only continue through immediately adjacent observations in the
    same tracklet.  A short below-threshold dip is tolerated only inside such
    a trusted run and is never counted as sprint duration or distance.
    """
    events: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    fps_safe = max(float(fps), 0.001)
    for fragment_index, fragment in enumerate(fragments):
        result = _classify_fragment(
            fragment,
            fps=fps_safe,
            policy=policy,
            fragment_index=fragment_index,
        )
        events.extend(result["events"])
        candidates.extend(result["candidates"])
        rejected.extend(result["rejected"])
    events = _coalesce_adjacent_events(events, fragments, fps=fps_safe, policy=policy)
    total_time = sum(float(event["qualifying_time_sec"]) for event in events)
    total_distance = sum(float(event["qualifying_distance_m"]) for event in events)
    max_speed_kmh = max((float(event["max_speed_mps"]) * 3.6 for event in events), default=0.0)
    raw_segment_peak_kmh = max(
        (float(event["raw_segment_peak_mps"]) * 3.6 for event in events),
        default=0.0,
    )
    best_candidate = _best_candidate(candidates)
    best_rejected = _best_candidate(rejected)
    return {
        "events": [_public_event(event) for event in events],
        "sprint_count": len(events),
        "sprint_time_sec": round(total_time, 3),
        "sprint_distance_m": round(total_distance, 2),
        "max_sprint_speed_kmh": round(max_speed_kmh, 2),
        "raw_sprint_segment_peak_kmh": round(raw_segment_peak_kmh, 2),
        "sprint_candidate_count": len(candidates),
        "rejected_sprint_candidate_count": len(rejected),
        "best_sprint_candidate_speed_kmh": _candidate_speed_kmh(best_candidate),
        "best_sprint_candidate_duration_sec": _candidate_duration(best_candidate),
        "best_sprint_candidate_distance_m": _candidate_distance(best_candidate),
        "best_sprint_candidate_reason": str(best_candidate.get("reason") or "none") if best_candidate else "none",
        "best_rejected_sprint_candidate": _serialize_candidate(best_rejected),
    }


def _classify_fragment(
    rows: list[dict[str, Any]],
    *,
    fps: float,
    policy: dict[str, Any],
    fragment_index: int,
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    last_accepted: dict[str, Any] | None = None
    grace_sec = 0.0
    minimum_duration = _policy_number(policy, "minimum_duration_sec", SPRINT_MIN_DURATION_SEC)
    allowed_dip = _policy_number(policy, "allowed_dip_sec", SPRINT_ALLOWED_DIP_SEC)

    def close_current() -> None:
        nonlocal current, grace_sec, last_accepted
        if current is None:
            return
        if current["qualifying_time_sec"] >= minimum_duration:
            current["reason"] = "accepted"
            events.append(current)
            last_accepted = current
        else:
            current["reason"] = "too_short"
            rejected.append(current)
        candidates.append(current)
        current = None
        grace_sec = 0.0

    segments = [
        segment
        for left, right in zip(rows, rows[1:])
        if (segment := _trusted_segment(left, right, fps)) is not None
        and int(segment["frame_gap"]) == 1
    ]
    sustained_by_end_frame = _sustained_window_by_end_frame(rows, segments, fps)

    for left, right in zip(rows, rows[1:]):
        segment = _trusted_segment(left, right, fps)
        # A non-contiguous or untrusted pair is a hard evidence boundary.  It
        # must not be bridged by the permitted speed dip.
        if segment is None or segment["frame_gap"] != 1:
            close_current()
            continue
        sustained_window = sustained_by_end_frame.get(int(segment["end_frame"]))
        sustained_speed_mps = (
            float(sustained_window["speed_mps"])
            if sustained_window is not None
            else None
        )
        speed_kmh = float(sustained_speed_mps or 0.0) * 3.6
        duration = float(segment["duration_sec"])
        if current is None:
            if (
                sustained_speed_mps is not None
                and speed_kmh >= float(policy["start_threshold_kmh"])
                and _is_meaningful_burst_start(
                    sustained_window,
                    rows=rows,
                    segments=segments,
                    fps=fps,
                    policy=policy,
                    previous_accepted=last_accepted,
                )
            ):
                current = _new_event(
                    sustained_window,
                    segments,
                    rows=rows,
                    fragment_index=fragment_index,
                )
            continue
        if sustained_speed_mps is not None and speed_kmh >= float(policy["continue_threshold_kmh"]):
            _append_qualifying(current, segment, sustained_speed_mps)
            grace_sec = 0.0
            continue
        if grace_sec + duration <= allowed_dip:
            grace_sec += duration
            continue
        close_current()
        if (
            sustained_speed_mps is not None
            and speed_kmh >= float(policy["start_threshold_kmh"])
            and _is_meaningful_burst_start(
                sustained_window,
                rows=rows,
                segments=segments,
                fps=fps,
                policy=policy,
                previous_accepted=last_accepted,
            )
        ):
            current = _new_event(
                sustained_window,
                segments,
                rows=rows,
                fragment_index=fragment_index,
            )
    close_current()
    return {"events": events, "candidates": candidates, "rejected": rejected}


def _trusted_segment(left: dict[str, Any], right: dict[str, Any], fps: float) -> dict[str, Any] | None:
    if left.get("visual_trusted") is False or right.get("visual_trusted") is False:
        return None
    identity_states = {str(row.get("identity_status") or "") for row in (left, right)}
    if identity_states - {"", "confirmed"}:
        return None
    left_owner = str(left.get("canonical_player_id") or "")
    right_owner = str(right.get("canonical_player_id") or "")
    if left_owner and right_owner and left_owner != right_owner:
        return None
    if not _valid_pitch(left.get("pitch_m")) or not _valid_pitch(right.get("pitch_m")):
        return None
    if str(left.get("tracklet_id") or "") != str(right.get("tracklet_id") or ""):
        return None
    left_frame = int(left.get("frame") or 0)
    right_frame = int(right.get("frame") or 0)
    frame_gap = right_frame - left_frame
    if frame_gap <= 0 or frame_gap > STATS_OBSERVED_GAP_FRAMES:
        return None
    start = _time(left, fps)
    end = _time(right, fps)
    duration = max(1.0 / fps, end - start)
    if duration > min(MAX_STATS_ESTIMATED_GAP_SEC, STATS_PEAK_SPEED_MAX_SEGMENT_GAP_SEC):
        return None
    dx = float(right["pitch_m"][0]) - float(left["pitch_m"][0])
    dy = float(right["pitch_m"][1]) - float(left["pitch_m"][1])
    distance = (dx * dx + dy * dy) ** 0.5
    speed = distance / duration
    if speed > MAX_STATS_SPEED_MPS:
        return None
    return {
        "start_frame": left_frame,
        "end_frame": right_frame,
        "start_time_sec": start,
        "end_time_sec": end,
        "duration_sec": duration,
        "distance_m": distance,
        "speed_mps": speed,
        "frame_gap": frame_gap,
        "tracklet_id": str(left.get("tracklet_id") or ""),
    }


def _sustained_window_by_end_frame(
    rows: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    fps: float,
) -> dict[int, dict[str, float | int]]:
    """Index the same conservative peak-speed windows by their evidence end.

    A single raw point-to-point segment cannot cross the sprint threshold on
    its own: at least the shared sustained-speed window must support it.
    """
    movement_segments = [
        {
            "start_frame": segment["start_frame"],
            "end_frame": segment["end_frame"],
            "dt": segment["duration_sec"],
            "speed": segment["speed_mps"],
        }
        for segment in segments
    ]
    by_end_frame: dict[int, dict[str, float | int]] = {}
    for window in sustained_speed_windows(rows, movement_segments, fps):
        end_frame = int(window["end_frame"])
        current = by_end_frame.get(end_frame)
        if current is None or float(window["speed_mps"]) > float(current["speed_mps"]):
            by_end_frame[end_frame] = window
    return by_end_frame


def _new_event(
    sustained_window: dict[str, float | int],
    segments: list[dict[str, Any]],
    *,
    rows: list[dict[str, Any]],
    fragment_index: int,
) -> dict[str, Any]:
    start_frame = int(sustained_window["start_frame"])
    end_frame = int(sustained_window["end_frame"])
    qualifying_segments = [
        segment
        for segment in segments
        if start_frame <= int(segment["start_frame"]) and int(segment["end_frame"]) <= end_frame
    ]
    if not qualifying_segments:
        raise ValueError("Sustained sprint window must contain trusted segments")
    start_row = next(
        (row for row in rows if int(row.get("frame") or 0) == start_frame),
        {},
    )
    return {
        "start_frame": start_frame,
        "end_frame": end_frame,
        "start_time_sec": float(sustained_window["start_time_sec"]),
        "end_time_sec": float(sustained_window["end_time_sec"]),
        "tracklet_id": qualifying_segments[0]["tracklet_id"],
        "qualifying_time_sec": sum(float(segment["duration_sec"]) for segment in qualifying_segments),
        "qualifying_distance_m": sum(float(segment["distance_m"]) for segment in qualifying_segments),
        # This is intentionally the public event peak: a conservative speed
        # sustained over the same 0.5–1.25s authority as whole-player peak.
        "max_speed_mps": float(sustained_window["speed_mps"]),
        "raw_segment_peak_mps": max(
            float(segment["speed_mps"]) for segment in qualifying_segments
        ),
        "qualifying_segment_count": len(qualifying_segments),
        "merged_fragment_count": 1,
        "_fragment_index": fragment_index,
        "_canonical_player_id": str(start_row.get("canonical_player_id") or ""),
    }


def _append_qualifying(
    event: dict[str, Any], segment: dict[str, Any], sustained_speed_mps: float
) -> None:
    event["end_frame"] = segment["end_frame"]
    event["end_time_sec"] = segment["end_time_sec"]
    event["qualifying_time_sec"] += segment["duration_sec"]
    event["qualifying_distance_m"] += segment["distance_m"]
    event["max_speed_mps"] = max(float(event["max_speed_mps"]), sustained_speed_mps)
    event["raw_segment_peak_mps"] = max(
        float(event["raw_segment_peak_mps"]), float(segment["speed_mps"])
    )
    event["qualifying_segment_count"] = int(event["qualifying_segment_count"]) + 1


def _is_meaningful_burst_start(
    sustained_window: dict[str, float | int],
    *,
    rows: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    fps: float,
    policy: dict[str, Any],
    previous_accepted: dict[str, Any] | None,
) -> bool:
    """Require acceleration only for starts close to the player threshold.

    A clearly faster sustained window is already meaningful.  Near the dynamic
    player-relative threshold, a trusted preceding movement baseline avoids
    promoting steady moderate running while retaining events without enough
    earlier evidence to make a safe comparison.
    """
    speed_kmh = float(sustained_window["speed_mps"]) * 3.6
    start_threshold = float(policy["start_threshold_kmh"])
    band = _policy_number(policy, "acceleration_band_kmh", SPRINT_ACCELERATION_BAND_KMH)
    if speed_kmh >= start_threshold + band:
        return True
    if _continues_recent_accepted_burst(
        previous_accepted,
        sustained_window=sustained_window,
        rows=rows,
        fps=fps,
        policy=policy,
    ):
        return True
    baseline_kmh = _preceding_baseline_speed_kmh(
        int(sustained_window["start_frame"]),
        rows=rows,
        segments=segments,
        fps=fps,
        context_sec=_policy_number(
            policy,
            "acceleration_context_sec",
            SPRINT_ACCELERATION_CONTEXT_SEC,
        ),
    )
    if baseline_kmh is None:
        # The start of a trusted fragment has no earlier physical context.
        # Preserve the established sustained-speed decision in that case;
        # inventing a "steady" baseline would turn missing evidence into a
        # false rejection.  Where the context exists, use it below.
        return True
    minimum_acceleration = _policy_number(
        policy,
        "minimum_acceleration_kmh",
        SPRINT_MIN_ACCELERATION_KMH,
    )
    return speed_kmh - baseline_kmh >= minimum_acceleration


def _continues_recent_accepted_burst(
    previous: dict[str, Any] | None,
    *,
    sustained_window: dict[str, float | int],
    rows: list[dict[str, Any]],
    fps: float,
    policy: dict[str, Any],
) -> bool:
    if previous is None:
        return False
    gap_sec = float(sustained_window["start_time_sec"]) - float(previous["end_time_sec"])
    if gap_sec < 0 or gap_sec > _policy_number(policy, "coalesce_max_gap_sec", SPRINT_COALESCE_MAX_GAP_SEC):
        return False
    bridge = _trusted_bridge(
        rows,
        start_frame=int(previous["end_frame"]),
        end_frame=int(sustained_window["start_frame"]),
        fps=fps,
    )
    return (
        bridge is not None
        and float(bridge["net_speed_kmh"]) >= float(policy["continue_threshold_kmh"])
        and float(bridge["net_speed_mps"])
        <= _policy_number(policy, "coalesce_max_bridge_speed_mps", SPRINT_COALESCE_MAX_BRIDGE_SPEED_MPS)
    )


def _preceding_baseline_speed_kmh(
    start_frame: int,
    *,
    rows: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    fps: float,
    context_sec: float,
) -> float | None:
    """Return a net trusted pre-event speed, or None without enough evidence."""
    rows_by_frame = {int(row.get("frame") or 0): row for row in rows}
    ending = rows_by_frame.get(start_frame)
    if ending is None:
        return None
    segment_by_end = {int(segment["end_frame"]): segment for segment in segments}
    earliest = ending
    current_frame = start_frame
    while True:
        segment = segment_by_end.get(current_frame)
        if segment is None or int(segment["frame_gap"]) != 1:
            break
        candidate = rows_by_frame.get(int(segment["start_frame"]))
        if candidate is None:
            break
        earliest = candidate
        current_frame = int(segment["start_frame"])
        if _time(ending, fps) - _time(earliest, fps) >= context_sec:
            break
    duration = _time(ending, fps) - _time(earliest, fps)
    if duration < context_sec or not _valid_pitch(earliest.get("pitch_m")):
        return None
    dx = float(ending["pitch_m"][0]) - float(earliest["pitch_m"][0])
    dy = float(ending["pitch_m"][1]) - float(earliest["pitch_m"][1])
    return ((dx * dx + dy * dy) ** 0.5 / duration) * 3.6


def _coalesce_adjacent_events(
    events: list[dict[str, Any]],
    fragments: list[list[dict[str, Any]]],
    *,
    fps: float,
    policy: dict[str, Any],
) -> list[dict[str, Any]]:
    """Merge only adjacent event fragments with a continuous trusted bridge."""
    ordered = sorted(events, key=lambda event: (float(event["start_time_sec"]), int(event["start_frame"])))
    coalesced: list[dict[str, Any]] = []
    for event in ordered:
        previous = coalesced[-1] if coalesced else None
        if previous is not None and _can_coalesce(previous, event, fragments, fps=fps, policy=policy):
            _merge_event(previous, event)
        else:
            coalesced.append(event)
    return coalesced


def _can_coalesce(
    previous: dict[str, Any],
    current: dict[str, Any],
    fragments: list[list[dict[str, Any]]],
    *,
    fps: float,
    policy: dict[str, Any],
) -> bool:
    if str(previous.get("tracklet_id") or "") != str(current.get("tracklet_id") or ""):
        return False
    previous_fragment_index = int(previous.get("_fragment_index", -1))
    current_fragment_index = int(current.get("_fragment_index", -2))
    if previous_fragment_index != current_fragment_index:
        return False
    previous_owner = str(previous.get("_canonical_player_id") or "")
    current_owner = str(current.get("_canonical_player_id") or "")
    if previous_owner and current_owner and previous_owner != current_owner:
        return False
    gap_sec = float(current["start_time_sec"]) - float(previous["end_time_sec"])
    if gap_sec < 0 or gap_sec > _policy_number(policy, "coalesce_max_gap_sec", SPRINT_COALESCE_MAX_GAP_SEC):
        return False
    fragment_index = previous_fragment_index
    if fragment_index < 0 or fragment_index >= len(fragments):
        return False
    bridge = _trusted_bridge(
        fragments[fragment_index],
        start_frame=int(previous["end_frame"]),
        end_frame=int(current["start_frame"]),
        fps=fps,
    )
    if bridge is None:
        return False
    return (
        float(bridge["net_speed_kmh"]) >= float(policy["continue_threshold_kmh"])
        and float(bridge["net_speed_mps"])
        <= _policy_number(
            policy,
            "coalesce_max_bridge_speed_mps",
            SPRINT_COALESCE_MAX_BRIDGE_SPEED_MPS,
        )
    )


def _trusted_bridge(
    rows: list[dict[str, Any]],
    *,
    start_frame: int,
    end_frame: int,
    fps: float,
) -> dict[str, float] | None:
    """Verify every gap segment before using it as physical continuity evidence."""
    if end_frame <= start_frame:
        return None
    by_frame = {int(row.get("frame") or 0): row for row in rows}
    start = by_frame.get(start_frame)
    end = by_frame.get(end_frame)
    if start is None or end is None:
        return None
    if not _trusted_bridge_row(start, str(start.get("tracklet_id") or "")):
        return None
    tracklet_id = str(start.get("tracklet_id") or "")
    for frame in range(start_frame + 1, end_frame + 1):
        current = by_frame.get(frame)
        if current is None or not _trusted_bridge_row(current, tracklet_id):
            return None
    duration = _time(end, fps) - _time(start, fps)
    if duration <= 0 or not _valid_pitch(start.get("pitch_m")) or not _valid_pitch(end.get("pitch_m")):
        return None
    dx = float(end["pitch_m"][0]) - float(start["pitch_m"][0])
    dy = float(end["pitch_m"][1]) - float(start["pitch_m"][1])
    net_speed_mps = (dx * dx + dy * dy) ** 0.5 / duration
    return {"net_speed_kmh": net_speed_mps * 3.6, "net_speed_mps": net_speed_mps}


def _trusted_bridge_row(row: dict[str, Any], tracklet_id: str) -> bool:
    identity_status = str(row.get("identity_status") or "")
    return (
        bool(tracklet_id)
        and str(row.get("tracklet_id") or "") == tracklet_id
        and row.get("visual_trusted") is not False
        and identity_status in {"", "confirmed"}
        and row.get("play_area_status", "inside_play") == "inside_play"
        and _valid_pitch(row.get("pitch_m"))
    )


def _merge_event(previous: dict[str, Any], current: dict[str, Any]) -> None:
    """Keep only contributing segments; bridge movement is evidence, not distance."""
    previous["end_frame"] = current["end_frame"]
    previous["end_time_sec"] = current["end_time_sec"]
    previous["qualifying_time_sec"] += current["qualifying_time_sec"]
    previous["qualifying_distance_m"] += current["qualifying_distance_m"]
    previous["max_speed_mps"] = max(float(previous["max_speed_mps"]), float(current["max_speed_mps"]))
    previous["raw_segment_peak_mps"] = max(
        float(previous["raw_segment_peak_mps"]),
        float(current["raw_segment_peak_mps"]),
    )
    previous["qualifying_segment_count"] += int(current["qualifying_segment_count"])
    previous["merged_fragment_count"] = int(previous.get("merged_fragment_count") or 1) + int(
        current.get("merged_fragment_count") or 1
    )


def _public_event(event: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in event.items() if not key.startswith("_")}


def _policy_number(policy: dict[str, Any], key: str, default: float) -> float:
    try:
        value = float(policy.get(key))
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def _best_candidate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return max(
        rows,
        key=lambda row: (
            float(row.get("max_speed_mps") or 0.0),
            float(row.get("qualifying_time_sec") or 0.0),
            float(row.get("qualifying_distance_m") or 0.0),
        ),
        default={},
    )


def _candidate_speed_kmh(row: dict[str, Any]) -> float:
    return round(float(row.get("max_speed_mps") or 0.0) * 3.6, 2)


def _candidate_duration(row: dict[str, Any]) -> float:
    return round(float(row.get("qualifying_time_sec") or 0.0), 3)


def _candidate_distance(row: dict[str, Any]) -> float:
    return round(float(row.get("qualifying_distance_m") or 0.0), 2)


def _serialize_candidate(row: dict[str, Any]) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "start_frame": int(row.get("start_frame") or 0),
        "end_frame": int(row.get("end_frame") or 0),
        "start_time_sec": round(float(row.get("start_time_sec") or 0.0), 3),
        "end_time_sec": round(float(row.get("end_time_sec") or 0.0), 3),
        "duration_sec": _candidate_duration(row),
        "distance_m": _candidate_distance(row),
        "max_speed_kmh": _candidate_speed_kmh(row),
        "reason": str(row.get("reason") or "unknown"),
    }


def _time(row: dict[str, Any], fps: float) -> float:
    value = row.get("time_sec")
    return float(value) if isinstance(value, (int, float)) else int(row.get("frame") or 0) / fps


def _valid_pitch(value: Any) -> bool:
    return isinstance(value, list) and len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2])
