from __future__ import annotations

"""Shadow-only local ball reassociation from durable Shot Review clicks.

The operator is authoritative only for the one source-video frame they clicked.
This module therefore never interpolates a ball trajectory: every shadow row is
an existing accepted ball candidate and every transition is bounded by the
current tracker’s pitch-space continuity limit.
"""

from collections import Counter
import math
from statistics import mean, median
from typing import Any, Mapping


SCHEMA_VERSION = "operator-ball-anchor-shadow:v1"
DEFAULT_WINDOW_SEC = 2.0
DEFAULT_MAX_LINK_SPEED_MPS = 22.0
MAX_CONTINUITY_GAP_SEC = 0.25
MIN_ANCHOR_DISTANCE_PX = 8.0
MAX_ANCHOR_DISTANCE_PX = 36.0
ANCHOR_DIAMETER_MULTIPLIER = 2.5


def extract_operator_ball_anchors(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Normalize only valid internal frame-correction provenance into anchors."""

    published_id = str(document.get("published_id") or "")
    anchors: list[dict[str, Any]] = []
    for shot in document.get("canonical_shots") or []:
        if not isinstance(shot, Mapping):
            continue
        provenance = _mapping(shot.get("location_correction_provenance"))
        shot_id = str(shot.get("shot_id") or "")
        source_match_id = str(provenance.get("source_match_id") or "")
        values = {
            "source_time_sec": _number(provenance.get("source_time_sec")),
            "logical_frame_time_sec": _number(provenance.get("logical_frame_time_sec")),
            "x_px": _number(provenance.get("x_px")),
            "y_px": _number(provenance.get("y_px")),
            "frame_width": _number(provenance.get("frame_width")),
            "frame_height": _number(provenance.get("frame_height")),
        }
        if not published_id or not shot_id or not source_match_id or any(value is None for value in values.values()):
            continue
        if values["source_time_sec"] < 0 or values["frame_width"] <= 0 or values["frame_height"] <= 0:
            continue
        if not (0 <= values["x_px"] <= values["frame_width"] and 0 <= values["y_px"] <= values["frame_height"]):
            continue
        anchors.append(
            {
                "canonical_shot_id": shot_id,
                "published_id": published_id,
                "source_match_id": source_match_id,
                "source_time_sec": round(values["source_time_sec"], 6),
                "logical_frame_time_sec": round(values["logical_frame_time_sec"], 6),
                "x_px": round(values["x_px"], 3),
                "y_px": round(values["y_px"], 3),
                "frame_width": round(values["frame_width"], 3),
                "frame_height": round(values["frame_height"], 3),
            }
        )
    return sorted(anchors, key=lambda row: (row["source_match_id"], row["source_time_sec"], row["canonical_shot_id"]))


def evaluate_operator_ball_anchors(
    anchors: list[Mapping[str, Any]],
    sources: Mapping[str, Mapping[str, Any]],
    *,
    window_sec: float = DEFAULT_WINDOW_SEC,
) -> dict[str, Any]:
    """Evaluate all durable anchors from persisted candidates/tracks only."""

    if window_sec <= 0:
        raise ValueError("window_sec must be positive")
    rows = [_evaluate_anchor(dict(anchor), sources.get(str(anchor.get("source_match_id") or "")), window_sec=window_sec) for anchor in anchors]
    classifications = Counter(str(row["classification"]) for row in rows)
    current_errors = [float(row["comparison"]["current_anchor_error_px"]) for row in rows if _number(row["comparison"].get("current_anchor_error_px")) is not None]
    shadow_errors = [float(row["comparison"]["shadow_anchor_error_px"]) for row in rows if _number(row["comparison"].get("shadow_anchor_error_px")) is not None]
    return {
        "schema_version": SCHEMA_VERSION,
        "evaluation_only": True,
        "inference_invoked": False,
        "canonical_artifacts_mutated": False,
        "window_sec": round(window_sec, 3),
        "anchor_count": len(rows),
        "anchors": rows,
        "summary": {
            "matched_anchor_candidate_count": sum(1 for row in rows if row["anchored"]["anchor_candidate_id"] is not None),
            "detector_miss_count": classifications["detector_miss"],
            "already_correct_count": classifications["already_correct"],
            "recovered_count": classifications["wrong_candidate_recovered"] + classifications["stale_track_recovered"],
            "regression_count": classifications["shadow_regression"],
            "classification_counts": dict(sorted(classifications.items())),
            "current_mean_anchor_error_px": _summary_value(current_errors, mean),
            "current_median_anchor_error_px": _summary_value(current_errors, median),
            "shadow_mean_anchor_error_px": _summary_value(shadow_errors, mean),
            "shadow_median_anchor_error_px": _summary_value(shadow_errors, median),
            "changed_frames_total": sum(int(row["comparison"]["frames_changed"]) for row in rows),
        },
    }


def _evaluate_anchor(anchor: dict[str, Any], source: Mapping[str, Any] | None, *, window_sec: float) -> dict[str, Any]:
    base = {"anchor": anchor, "current": _empty_current(), "anchored": _empty_anchored(), "comparison": _empty_comparison()}
    if source is None:
        return {**base, "classification": "inconclusive", "reason": "source_artifacts_unavailable"}
    fps = _number(source.get("fps"))
    if fps is None or fps <= 0:
        return {**base, "classification": "inconclusive", "reason": "source_fps_unavailable"}
    frames = _candidate_frames(source.get("ball_candidates"))
    tracks = _track_rows_by_frame(source.get("ball_tracks"))
    frame_tolerance_sec = 1.0 / fps + 0.001
    nearby = [
        item for item in frames
        if abs(item["time_sec"] - float(anchor["source_time_sec"])) <= frame_tolerance_sec
    ]
    candidate_diagnostics = _anchor_candidate_diagnostics(nearby, anchor, tracks)
    current = _current_at_anchor(nearby, tracks, anchor)
    base["current"] = current
    if not candidate_diagnostics:
        base["comparison"]["current_anchor_error_px"] = current.get("distance_px_to_operator")
        return {**base, "classification": "detector_miss", "reason": "no_candidate_near_source_anchor_frame"}

    matches = [row for row in candidate_diagnostics if row["within_anchor_distance"]]
    if not matches:
        base["anchored"]["anchor_candidates"] = _public_anchor_candidates(candidate_diagnostics)
        base["comparison"]["current_anchor_error_px"] = current.get("distance_px_to_operator")
        return {**base, "classification": "detector_miss", "reason": "no_matching_candidate_within_plausible_anchor_distance"}
    seed = min(matches, key=lambda row: (row["distance_px"], -row["confidence"], row["candidate_id"]))
    seed_candidate = seed["_candidate"]
    path, continuity = _build_local_shadow_path(
        frames,
        seed_candidate,
        anchor_time_sec=float(anchor["source_time_sec"]),
        window_sec=window_sec,
        max_link_speed_mps=_number(source.get("max_link_speed_mps")) or DEFAULT_MAX_LINK_SPEED_MPS,
    )
    shadow_error = float(seed["distance_px"])
    base["anchored"] = {
        "anchor_candidate_id": seed["candidate_id"],
        "anchor_candidate_distance_px": shadow_error,
        "anchor_distance_threshold_px": seed["anchor_distance_threshold_px"],
        "anchor_candidates": _public_anchor_candidates(candidate_diagnostics),
        "local_shadow_path": [_public_candidate(row) for row in path],
        "frames_covered": [int(row["frame"]) for row in path],
        "time_range_sec": {"start": path[0]["time_sec"], "end": path[-1]["time_sec"]},
        "continuity": continuity,
    }
    base["comparison"] = _compare_paths(current, path, tracks, current_error=current.get("distance_px_to_operator"), shadow_error=shadow_error)
    if current.get("distance_px_to_operator") is not None and current["distance_px_to_operator"] <= seed["anchor_distance_threshold_px"]:
        classification, reason = "already_correct", "current_track_is_within_anchor_distance"
    elif len(path) <= 1:
        classification, reason = "anchor_without_plausible_path", "anchor_candidate_has_no_plausible_local_continuation"
    elif current.get("candidate_id") is None:
        classification, reason = "stale_track_recovered", "current_track_has_no_detected_candidate_at_anchor"
    elif current.get("candidate_id") != seed["candidate_id"]:
        classification, reason = "wrong_candidate_recovered", "current_track_selected_a_different_candidate_at_anchor"
    else:
        classification, reason = "inconclusive", "anchor_matches_current_candidate_but_current_error_is_unavailable"
    return {**base, "classification": classification, "reason": reason}


def _candidate_frames(document: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for frame in _mapping(document).get("frames") or []:
        if not isinstance(frame, Mapping):
            continue
        frame_number, time_sec = _number(frame.get("frame")), _number(frame.get("time_sec"))
        if frame_number is None or time_sec is None:
            continue
        candidates = []
        for candidate in frame.get("candidates") or []:
            normalized = _candidate(candidate, frame=int(frame_number), time_sec=time_sec)
            if normalized is not None:
                candidates.append(normalized)
        result.append({"frame": int(frame_number), "time_sec": round(time_sec, 6), "candidates": candidates})
    return sorted(result, key=lambda row: row["frame"])


def _candidate(value: Any, *, frame: int, time_sec: float) -> dict[str, Any] | None:
    row = _mapping(value)
    candidate_id = str(row.get("candidate_id") or "")
    point = _point(row.get("position_px"))
    if not candidate_id or point is None:
        return None
    return {
        "candidate_id": candidate_id,
        "frame": frame,
        "time_sec": round(_number(row.get("time_sec")) or time_sec, 6),
        "position_px": point,
        "position_m": _point(row.get("position_m")),
        "confidence": round(_number(row.get("confidence")) or 0.0, 4),
        "width_px": _number(row.get("width_px")) or 0.0,
        "height_px": _number(row.get("height_px")) or 0.0,
    }


def _track_rows_by_frame(document: Any) -> dict[int, dict[str, Any]]:
    rows: dict[int, dict[str, Any]] = {}
    for value in _mapping(document).get("positions") or []:
        row = _mapping(value)
        frame = _number(row.get("frame"))
        if frame is None:
            continue
        rows[int(frame)] = dict(row)
    return rows


def _anchor_candidate_diagnostics(frames: list[Mapping[str, Any]], anchor: Mapping[str, Any], tracks: Mapping[int, Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for frame in frames:
        for candidate in frame.get("candidates") or []:
            point = _point(candidate.get("position_px"))
            if point is None:
                continue
            threshold = _anchor_distance_threshold(candidate)
            rows.append({
                "candidate_id": str(candidate["candidate_id"]),
                "frame": int(candidate["frame"]),
                "time_sec": candidate["time_sec"],
                "frame_time_delta_sec": round(candidate["time_sec"] - float(anchor["source_time_sec"]), 6),
                "distance_px": round(_distance_px(point, [float(anchor["x_px"]), float(anchor["y_px"])]), 3),
                "anchor_distance_threshold_px": threshold,
                "within_anchor_distance": _distance_px(point, [float(anchor["x_px"]), float(anchor["y_px"])]) <= threshold,
                "confidence": candidate["confidence"],
                "currently_selected": str(_mapping(tracks.get(int(candidate["frame"]))).get("candidate_id") or "") == str(candidate["candidate_id"]),
                "_candidate": candidate,
            })
    return sorted(rows, key=lambda row: (abs(row["frame_time_delta_sec"]), row["distance_px"], row["candidate_id"]))


def _current_at_anchor(frames: list[Mapping[str, Any]], tracks: Mapping[int, Mapping[str, Any]], anchor: Mapping[str, Any]) -> dict[str, Any]:
    choices = [(abs(float(frame["time_sec"]) - float(anchor["source_time_sec"])), int(frame["frame"])) for frame in frames if int(frame["frame"]) in tracks]
    if not choices:
        return _empty_current()
    _, frame = min(choices)
    row = _mapping(tracks[frame])
    point = _point(row.get("position_px"))
    return {
        "frame": frame,
        "time_sec": _number(row.get("time_sec")),
        "candidate_id": str(row.get("candidate_id") or "") or None,
        "position_px": point,
        "source": str(row.get("source") or "unknown"),
        "confidence": _number(row.get("confidence")),
        "distance_px_to_operator": round(_distance_px(point, [float(anchor["x_px"]), float(anchor["y_px"])]), 3) if point is not None else None,
    }


def _build_local_shadow_path(frames: list[Mapping[str, Any]], seed: Mapping[str, Any], *, anchor_time_sec: float, window_sec: float, max_link_speed_mps: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    window = [frame for frame in frames if abs(float(frame["time_sec"]) - anchor_time_sec) <= window_sec]
    seed_frame = int(seed["frame"])
    before = [frame for frame in window if int(frame["frame"]) < seed_frame]
    after = [frame for frame in window if int(frame["frame"]) > seed_frame]
    backward, backward_reason, backward_speeds = _extend_path(list(reversed(before)), dict(seed), max_link_speed_mps=max_link_speed_mps)
    forward, forward_reason, forward_speeds = _extend_path(after, dict(seed), max_link_speed_mps=max_link_speed_mps)
    path = [*reversed(backward), dict(seed), *forward]
    speeds = [*backward_speeds, *forward_speeds]
    return path, {
        "max_link_speed_mps": round(max_link_speed_mps, 3),
        "max_observed_speed_mps": round(max(speeds), 3) if speeds else None,
        "backward_stop_reason": backward_reason,
        "forward_stop_reason": forward_reason,
        "uses_interpolation": False,
    }


def _extend_path(frames: list[Mapping[str, Any]], seed: dict[str, Any], *, max_link_speed_mps: float) -> tuple[list[dict[str, Any]], str, list[float]]:
    path: list[dict[str, Any]] = []
    current = seed
    speeds: list[float] = []
    for frame in frames:
        gap = abs(float(frame["time_sec"]) - float(current["time_sec"]))
        if gap > MAX_CONTINUITY_GAP_SEC:
            return path, "candidate_gap", speeds
        candidates = [candidate for candidate in frame.get("candidates") or [] if _transition_speed(current, candidate) is not None and _transition_speed(current, candidate) <= max_link_speed_mps]
        if not candidates:
            return path, "no_plausible_candidate", speeds
        candidate = min(candidates, key=lambda item: (_transition_score(current, item, max_link_speed_mps), -float(item["confidence"]), str(item["candidate_id"])))
        speed = _transition_speed(current, candidate)
        if speed is None:
            return path, "missing_pitch_geometry", speeds
        path.append(candidate)
        speeds.append(speed)
        current = candidate
    return path, "window_boundary", speeds


def _transition_speed(previous: Mapping[str, Any], candidate: Mapping[str, Any]) -> float | None:
    previous_point, candidate_point = _point(previous.get("position_m")), _point(candidate.get("position_m"))
    dt = abs(float(candidate.get("time_sec") or 0.0) - float(previous.get("time_sec") or 0.0))
    if previous_point is None or candidate_point is None or dt <= 0:
        return None
    return _distance_m(previous_point, candidate_point) / dt


def _transition_score(previous: Mapping[str, Any], candidate: Mapping[str, Any], max_link_speed_mps: float) -> float:
    speed = _transition_speed(previous, candidate)
    if speed is None:
        return math.inf
    return speed / max(max_link_speed_mps, 0.001) * 0.8 + (1.0 - float(candidate["confidence"])) * 0.2


def _compare_paths(current: Mapping[str, Any], path: list[Mapping[str, Any]], tracks: Mapping[int, Mapping[str, Any]], *, current_error: Any, shadow_error: float) -> dict[str, Any]:
    changed = added = removed = 0
    for candidate in path:
        current_row = _mapping(tracks.get(int(candidate["frame"])))
        current_candidate_id = str(current_row.get("candidate_id") or "") or None
        if current_candidate_id != candidate["candidate_id"]:
            changed += 1
        if current_candidate_id is None:
            added += 1
    path_frames = {int(candidate["frame"]) for candidate in path}
    for frame, row in tracks.items():
        if frame in path_frames or str(row.get("candidate_id") or "") == "":
            continue
        if path and path[0]["frame"] <= frame <= path[-1]["frame"]:
            removed += 1
    return {
        "frames_changed": changed,
        "points_added": added,
        "points_removed": removed,
        "current_anchor_error_px": current_error,
        "shadow_anchor_error_px": round(shadow_error, 3),
    }


def _anchor_distance_threshold(candidate: Mapping[str, Any]) -> float:
    diameter = max(float(candidate.get("width_px") or 0.0), float(candidate.get("height_px") or 0.0))
    return round(min(MAX_ANCHOR_DISTANCE_PX, max(MIN_ANCHOR_DISTANCE_PX, diameter * ANCHOR_DIAMETER_MULTIPLIER)), 3)


def _public_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return {key: candidate[key] for key in ("candidate_id", "frame", "time_sec", "position_px", "position_m", "confidence")}


def _public_anchor_candidates(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [{key: value for key, value in row.items() if key != "_candidate"} for row in rows]


def _empty_current() -> dict[str, Any]:
    return {"frame": None, "time_sec": None, "candidate_id": None, "position_px": None, "source": None, "confidence": None, "distance_px_to_operator": None}


def _empty_anchored() -> dict[str, Any]:
    return {"anchor_candidate_id": None, "anchor_candidate_distance_px": None, "anchor_distance_threshold_px": None, "anchor_candidates": [], "local_shadow_path": [], "frames_covered": [], "time_range_sec": None, "continuity": None}


def _empty_comparison() -> dict[str, Any]:
    return {"frames_changed": 0, "points_added": 0, "points_removed": 0, "current_anchor_error_px": None, "shadow_anchor_error_px": None}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) else None


def _point(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    x, y = _number(value[0]), _number(value[1])
    return [x, y] if x is not None and y is not None else None


def _distance_px(first: list[float] | None, second: list[float]) -> float:
    if first is None:
        return math.nan
    return math.hypot(first[0] - second[0], first[1] - second[1])


def _distance_m(first: list[float], second: list[float]) -> float:
    return math.hypot(first[0] - second[0], first[1] - second[1])


def _summary_value(rows: list[float], aggregate: Any) -> float | None:
    return round(float(aggregate(rows)), 3) if rows else None
