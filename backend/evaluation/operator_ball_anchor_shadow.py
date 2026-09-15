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

from app.services.ball_tracking import (
    BALL_SELECTION_POLICY_V1,
    BALL_SELECTION_POLICY_V2,
    DEFAULT_MAX_LINK_SPEED_MPS,
    DEFAULT_MIN_START_CONF,
    select_ball_detections_from_trusted_seed,
)

SCHEMA_VERSION = "operator-ball-anchor-shadow:v1"
DEFAULT_WINDOW_SEC = 2.0
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
    anchor_frame = _resolve_anchor_frame(frames, source_time_sec=float(anchor["source_time_sec"]), fps=fps)
    if anchor_frame is None:
        return {**base, "classification": "detector_miss", "reason": "no_persisted_frame_within_anchor_rounding_tolerance"}
    candidate_diagnostics = _anchor_candidate_diagnostics([anchor_frame], anchor, tracks)
    current = _current_at_anchor(anchor_frame, tracks, anchor)
    base["current"] = current
    if not candidate_diagnostics:
        base["comparison"]["current_anchor_error_px"] = current.get("distance_px_to_operator")
        base["anchored"]["resolved_anchor_frame"] = _frame_reference(anchor_frame, anchor)
        return {**base, "classification": "detector_miss", "reason": "resolved_anchor_frame_has_no_candidates"}

    matches = [row for row in candidate_diagnostics if row["within_anchor_distance"]]
    if not matches:
        base["anchored"]["anchor_candidates"] = _public_anchor_candidates(candidate_diagnostics)
        base["comparison"]["current_anchor_error_px"] = current.get("distance_px_to_operator")
        base["anchored"]["resolved_anchor_frame"] = _frame_reference(anchor_frame, anchor)
        return {**base, "classification": "detector_miss", "reason": "no_matching_candidate_within_plausible_anchor_distance"}
    seed = min(matches, key=lambda row: (row["distance_px"], -row["confidence"], row["candidate_id"]))
    seed_candidate = seed["_candidate"]
    policy = _source_selection_policy(source)
    if policy is None:
        return {**base, "classification": "inconclusive", "reason": "source_ball_selection_policy_unsupported"}
    path, continuity = _build_local_shadow_path(
        frames,
        seed_candidate,
        anchor_time_sec=float(anchor["source_time_sec"]),
        window_sec=window_sec,
        max_link_speed_mps=_number(source.get("max_link_speed_mps")) or DEFAULT_MAX_LINK_SPEED_MPS,
        min_start_conf=_number(source.get("min_start_conf")) or DEFAULT_MIN_START_CONF,
        fps=fps,
        policy_version=policy,
    )
    shadow_error = float(seed["distance_px"])
    base["anchored"] = {
        "anchor_candidate_id": seed["candidate_id"],
        "anchor_candidate_distance_px": shadow_error,
        "anchor_distance_threshold_px": seed["anchor_distance_threshold_px"],
        "resolved_anchor_frame": _frame_reference(anchor_frame, anchor),
        "anchor_candidates": _public_anchor_candidates(candidate_diagnostics),
        "local_shadow_path": [_public_candidate(row) for row in path],
        "frames_covered": [int(row["frame"]) for row in path],
        "time_range_sec": {"start": path[0]["time_sec"], "end": path[-1]["time_sec"]},
        "continuity": continuity,
    }
    current_path = _current_local_path(
        tracks,
        start_frame=int(path[0]["frame"]),
        end_frame=int(path[-1]["frame"]),
    )
    base["comparison"] = _compare_paths(
        current,
        path,
        tracks,
        current_error=current.get("distance_px_to_operator"),
        shadow_error=shadow_error,
        current_path=current_path,
        max_link_speed_mps=_number(source.get("max_link_speed_mps")) or DEFAULT_MAX_LINK_SPEED_MPS,
    )
    if current.get("distance_px_to_operator") is not None and current["distance_px_to_operator"] <= seed["anchor_distance_threshold_px"]:
        regression_reason = _shadow_regression_reason(base["comparison"])
        if regression_reason is not None:
            classification, reason = "shadow_regression", regression_reason
        else:
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


def _current_at_anchor(frame: Mapping[str, Any], tracks: Mapping[int, Mapping[str, Any]], anchor: Mapping[str, Any]) -> dict[str, Any]:
    frame_number = int(frame["frame"])
    if frame_number not in tracks:
        return _empty_current()
    row = _mapping(tracks[frame_number])
    point = _point(row.get("position_px"))
    return {
        "frame": frame_number,
        "time_sec": _number(row.get("time_sec")),
        "candidate_id": str(row.get("candidate_id") or "") or None,
        "position_px": point,
        "source": str(row.get("source") or "unknown"),
        "confidence": _number(row.get("confidence")),
        "distance_px_to_operator": round(_distance_px(point, [float(anchor["x_px"]), float(anchor["y_px"])]), 3) if point is not None else None,
    }


def _build_local_shadow_path(
    frames: list[Mapping[str, Any]],
    seed: Mapping[str, Any],
    *,
    anchor_time_sec: float,
    window_sec: float,
    max_link_speed_mps: float,
    min_start_conf: float,
    fps: float,
    policy_version: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Reuse the persisted production selector from the trusted seed outward."""

    window = [frame for frame in frames if abs(float(frame["time_sec"]) - anchor_time_sec) <= window_sec]
    seed_frame = int(seed["frame"])
    before = [frame for frame in window if int(frame["frame"]) < seed_frame]
    after = [frame for frame in window if int(frame["frame"]) > seed_frame]
    forward = select_ball_detections_from_trusted_seed(
        [dict(frame) for frame in after],
        seed=dict(seed),
        fps=fps,
        max_link_speed_mps=max_link_speed_mps,
        min_start_conf=min_start_conf,
        policy_version=policy_version,
    )
    # Production selection is forward-only. Reversing the bounded local slice
    # lets it apply the same policy compatibility rules backwards without
    # inventing an independent transition score.
    backward = select_ball_detections_from_trusted_seed(
        _reverse_frames(before, fps=fps),
        seed=_reverse_candidate(dict(seed)),
        fps=fps,
        max_link_speed_mps=max_link_speed_mps,
        min_start_conf=min_start_conf,
        policy_version=policy_version,
    )
    backward_rows = [_restore_reversed_candidate(row) for row in backward.values()]
    path = sorted([*backward_rows, dict(seed), *forward.values()], key=lambda row: int(row["frame"]))
    speeds = _path_speeds(path)
    return path, {
        "selection_policy": policy_version,
        "max_link_speed_mps": round(max_link_speed_mps, 3),
        "max_observed_speed_mps": round(max(speeds), 3) if speeds else None,
        "backward_stop_reason": "window_boundary" if backward_rows else "no_policy_compatible_candidate",
        "forward_stop_reason": "window_boundary" if forward else "no_policy_compatible_candidate",
        "uses_interpolation": False,
    }


def _reverse_frames(frames: list[Mapping[str, Any]], *, fps: float) -> list[dict[str, Any]]:
    reversed_frames: list[dict[str, Any]] = []
    for index, frame in enumerate(reversed(frames), start=1):
        candidates = []
        for candidate in frame.get("candidates") or []:
            if not isinstance(candidate, Mapping):
                continue
            candidates.append({**dict(candidate), "_source_frame": candidate.get("frame"), "_source_time_sec": candidate.get("time_sec"), "frame": index, "time_sec": index / max(fps, 0.001)})
        reversed_frames.append({"frame": index, "time_sec": index / max(fps, 0.001), "candidates": candidates})
    return reversed_frames


def _reverse_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {**candidate, "_source_frame": candidate.get("frame"), "_source_time_sec": candidate.get("time_sec"), "frame": 0, "time_sec": 0.0}


def _restore_reversed_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **dict(candidate),
        "frame": int(candidate.get("_source_frame") or 0),
        "time_sec": float(candidate.get("_source_time_sec") or 0.0),
    }


def _compare_paths(
    current: Mapping[str, Any],
    path: list[Mapping[str, Any]],
    tracks: Mapping[int, Mapping[str, Any]],
    *,
    current_error: Any,
    shadow_error: float,
    current_path: list[dict[str, Any]],
    max_link_speed_mps: float,
) -> dict[str, Any]:
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
        "current_continuity": _continuity_metrics(current_path, max_link_speed_mps=max_link_speed_mps),
        "shadow_continuity": _continuity_metrics([dict(row) for row in path], max_link_speed_mps=max_link_speed_mps),
    }


def _resolve_anchor_frame(frames: list[Mapping[str, Any]], *, source_time_sec: float, fps: float) -> dict[str, Any] | None:
    if not frames:
        return None
    closest = min(frames, key=lambda frame: (abs(float(frame["time_sec"]) - source_time_sec), int(frame["frame"])))
    return dict(closest) if abs(float(closest["time_sec"]) - source_time_sec) <= 1.0 / fps + 0.001 else None


def _frame_reference(frame: Mapping[str, Any], anchor: Mapping[str, Any]) -> dict[str, Any]:
    return {"frame": int(frame["frame"]), "time_sec": float(frame["time_sec"]), "time_delta_sec": round(float(frame["time_sec"]) - float(anchor["source_time_sec"]), 6)}


def _source_selection_policy(source: Mapping[str, Any]) -> str | None:
    policy = str(source.get("ball_selection_policy") or BALL_SELECTION_POLICY_V1)
    return policy if policy in {BALL_SELECTION_POLICY_V1, BALL_SELECTION_POLICY_V2} else None


def _current_local_path(
    tracks: Mapping[int, Mapping[str, Any]],
    *,
    start_frame: int,
    end_frame: int,
) -> list[dict[str, Any]]:
    """Use the current path over exactly the shadow path's covered frames."""

    return [
        dict(row)
        for frame, row in sorted(tracks.items())
        if start_frame <= frame <= end_frame
        and str(row.get("candidate_id") or "")
        and _point(row.get("position_m")) is not None
    ]


def _path_speeds(rows: list[Mapping[str, Any]]) -> list[float]:
    speeds: list[float] = []
    for previous, current in zip(rows, rows[1:]):
        previous_point, current_point = _point(previous.get("position_m")), _point(current.get("position_m"))
        dt = abs(float(current.get("time_sec") or 0.0) - float(previous.get("time_sec") or 0.0))
        if previous_point is not None and current_point is not None and dt > 0:
            speeds.append(_distance_m(previous_point, current_point) / dt)
    return speeds


def _continuity_metrics(rows: list[Mapping[str, Any]], *, max_link_speed_mps: float) -> dict[str, Any]:
    speeds = _path_speeds(rows)
    return {
        "candidate_point_count": len(rows),
        "max_speed_mps": round(max(speeds), 3) if speeds else None,
        "plausible": len(rows) >= 2 and all(speed <= max_link_speed_mps for speed in speeds),
    }


def _shadow_regression_reason(comparison: Mapping[str, Any]) -> str | None:
    current = _mapping(comparison.get("current_continuity"))
    shadow = _mapping(comparison.get("shadow_continuity"))
    if not current.get("plausible"):
        return None
    current_count, shadow_count = int(current.get("candidate_point_count") or 0), int(shadow.get("candidate_point_count") or 0)
    if shadow_count < current_count:
        return "anchored_path_is_shorter_than_plausible_current_path"
    current_speed, shadow_speed = _number(current.get("max_speed_mps")), _number(shadow.get("max_speed_mps"))
    if int(comparison.get("frames_changed") or 0) > 0 and current_speed is not None and shadow_speed is not None and shadow_speed > current_speed + max(1.0, current_speed * 0.5):
        return "anchored_path_has_materially_worse_continuity_speed"
    return None


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
    return {"anchor_candidate_id": None, "anchor_candidate_distance_px": None, "anchor_distance_threshold_px": None, "resolved_anchor_frame": None, "anchor_candidates": [], "local_shadow_path": [], "frames_covered": [], "time_range_sec": None, "continuity": None}


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
