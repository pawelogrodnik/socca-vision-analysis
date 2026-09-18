"""Small, read-only bridge experiment for one known production ball gap."""
from __future__ import annotations

from typing import Any, Mapping

from app.services.ball_tracking import DEFAULT_MAX_LINK_SPEED_MPS, DEFAULT_MIN_START_CONF, select_ball_detections_from_trusted_seed
from evaluation.operator_ball_anchor_shadow import anchor_distance_threshold_px

UNIQUE_BRIDGE = "UNIQUE_BRIDGE"
AMBIGUOUS = "AMBIGUOUS"
NO_BRIDGE = "NO_BRIDGE"
NO_LOW_CONF_CANDIDATES = "NO_LOW_CONF_CANDIDATES"
INCONCLUSIVE = "INCONCLUSIVE"


def find_target_gap_boundaries(
    tracks: Mapping[str, Any],
    *,
    target_time_sec: float,
) -> dict[str, Any] | None:
    """Find the non-detected run containing the requested playback time."""
    rows = sorted(
        (dict(row) for row in tracks.get("positions") or [] if isinstance(row, Mapping)),
        key=lambda row: int(row.get("frame") or -1),
    )
    if not rows:
        return None
    target_index = min(
        range(len(rows)),
        key=lambda index: abs(float(rows[index].get("time_sec") or 0.0) - target_time_sec),
    )
    if rows[target_index].get("source") == "detected":
        return None
    start = target_index
    while start > 0 and rows[start - 1].get("source") != "detected":
        start -= 1
    end = target_index
    while end + 1 < len(rows) and rows[end + 1].get("source") != "detected":
        end += 1
    left = rows[start - 1] if start else None
    right = rows[end + 1] if end + 1 < len(rows) else None
    if left is None or right is None or left.get("source") != "detected" or right.get("source") != "detected":
        return None
    return {
        "start_frame": int(rows[start]["frame"]),
        "end_frame": int(rows[end]["frame"]),
        "start_time_sec": float(rows[start]["time_sec"]),
        "end_time_sec": float(rows[end]["time_sec"]),
        "duration_sec": round(float(right["time_sec"]) - float(left["time_sec"]), 6),
        "left_boundary": _public_boundary(left),
        "right_boundary": _public_boundary(right),
    }


def recover_targeted_gap(
    tracks: Mapping[str, Any],
    *,
    target_time_sec: float,
    low_confidence_frames: list[Mapping[str, Any]],
    fps: float,
) -> dict[str, Any]:
    """Select one two-sided, production-policy bridge without operator input."""
    boundaries = find_target_gap_boundaries(tracks, target_time_sec=target_time_sec)
    if boundaries is None:
        return {"outcome": INCONCLUSIVE, "boundaries": None, "bridge": _empty_bridge()}
    left = _boundary_candidate(boundaries["left_boundary"])
    right = _boundary_candidate(boundaries["right_boundary"])
    parameters = tracks.get("parameters") if isinstance(tracks.get("parameters"), Mapping) else {}
    max_speed = float(parameters.get("max_link_speed_mps") or DEFAULT_MAX_LINK_SPEED_MPS)
    min_start_conf = float(parameters.get("min_start_conf") or DEFAULT_MIN_START_CONF)
    policy = str(parameters.get("ball_selection_policy") or "ball-selection:v1")
    local_frames = _accepted_frames_between(
        low_confidence_frames,
        left_frame=int(left["frame"]),
        right_frame=int(right["frame"]),
    )
    accepted_count = sum(len(frame["candidates"]) for frame in local_frames)
    raw_count = sum(len(frame.get("raw_prediction_rows") or []) for frame in local_frames)
    rejected_count = sum(len(frame.get("rejected_candidates") or []) for frame in local_frames)
    base = {
        "boundaries": boundaries,
        "evidence": {
            "raw_candidates": raw_count,
            "accepted_candidates": accepted_count,
            "rejected_candidates": rejected_count,
        },
    }
    if not accepted_count:
        return {**base, "outcome": NO_LOW_CONF_CANDIDATES, "bridge": _empty_bridge()}
    forward = _forward_path(local_frames, left=left, right=right, fps=fps, max_speed=max_speed, min_start_conf=min_start_conf, policy=policy)
    backward = _backward_path(local_frames, left=left, right=right, fps=fps, max_speed=max_speed, min_start_conf=min_start_conf, policy=policy)
    if not forward["reaches_boundary"] or not backward["reaches_boundary"]:
        return {**base, "outcome": NO_BRIDGE, "bridge": _bridge_metrics([], forward, backward, left, right)}
    if forward["candidate_ids"] != backward["candidate_ids"]:
        return {**base, "outcome": AMBIGUOUS, "bridge": _bridge_metrics([], forward, backward, left, right)}
    selected = forward["rows"]
    if not selected:
        return {**base, "outcome": NO_BRIDGE, "bridge": _bridge_metrics([], forward, backward, left, right)}
    return {**base, "outcome": UNIQUE_BRIDGE, "bridge": _bridge_metrics(selected, forward, backward, left, right)}


def evaluate_operator_after_recovery(
    recovery: Mapping[str, Any],
    anchor: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Compare a completed result with an anchor; never feeds selection."""
    if anchor is None:
        return None
    rows = list((recovery.get("bridge") or {}).get("selected_rows") or [])
    if not rows:
        return {"used_during_recovery": False, "distance_px_to_operator": None, "threshold_px": None, "within_operator_threshold": False}
    nearest = min(rows, key=lambda row: abs(float(row["time_sec"]) - float(anchor["source_time_sec"])))
    point = nearest.get("position_px")
    if not isinstance(point, list) or len(point) != 2:
        return {"used_during_recovery": False, "distance_px_to_operator": None, "threshold_px": None, "within_operator_threshold": False}
    distance = ((float(point[0]) - float(anchor["x_px"])) ** 2 + (float(point[1]) - float(anchor["y_px"])) ** 2) ** .5
    threshold = anchor_distance_threshold_px(nearest)
    return {
        "used_during_recovery": False,
        "candidate_id": nearest.get("candidate_id"),
        "frame": nearest.get("frame"),
        "distance_px_to_operator": round(distance, 3),
        "threshold_px": threshold,
        "within_operator_threshold": distance <= threshold,
    }


def _accepted_frames_between(
    frames: list[Mapping[str, Any]],
    *,
    left_frame: int,
    right_frame: int,
) -> list[dict[str, Any]]:
    result = []
    for frame in sorted(frames, key=lambda row: int(row.get("frame") or -1)):
        frame_number = int(frame.get("frame") or -1)
        if not left_frame < frame_number < right_frame:
            continue
        candidates = [_candidate(row, frame) for row in frame.get("candidates") or []]
        result.append({
            "frame": frame_number,
            "time_sec": float(frame.get("time_sec") or 0.0),
            "candidates": [row for row in candidates if row is not None],
            "raw_prediction_rows": list(frame.get("raw_prediction_rows") or []),
            "rejected_candidates": list(frame.get("rejected_candidates") or []),
        })
    return result


def _forward_path(
    frames: list[Mapping[str, Any]],
    *,
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    fps: float,
    max_speed: float,
    min_start_conf: float,
    policy: str,
) -> dict[str, Any]:
    selected = select_ball_detections_from_trusted_seed(
        [*frames, {"frame": int(right["frame"]), "time_sec": float(right["time_sec"]), "candidates": [dict(right)]}],
        seed=dict(left), fps=fps, max_link_speed_mps=max_speed, min_start_conf=min_start_conf, policy_version=policy,
    )
    rows = [dict(row) for _, row in sorted(selected.items())]
    reaches = any(str(row.get("candidate_id")) == str(right.get("candidate_id")) for row in rows)
    inner = [row for row in rows if int(left["frame"]) < int(row["frame"]) < int(right["frame"])]
    return {"rows": inner, "candidate_ids": [str(row.get("candidate_id")) for row in inner], "reaches_boundary": reaches}


def _backward_path(
    frames: list[Mapping[str, Any]],
    *,
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    fps: float,
    max_speed: float,
    min_start_conf: float,
    policy: str,
) -> dict[str, Any]:
    reverse_rows = []
    originals = {str(left["candidate_id"]): dict(left)}
    for frame in sorted(frames, key=lambda row: int(row["frame"]), reverse=True):
        frame_delta = int(right["frame"]) - int(frame["frame"])
        time_delta = float(right["time_sec"]) - float(frame["time_sec"])
        candidates = []
        for candidate in frame["candidates"]:
            originals[str(candidate["candidate_id"])] = dict(candidate)
            candidates.append({**candidate, "frame": frame_delta, "time_sec": time_delta})
        reverse_rows.append({"frame": frame_delta, "time_sec": time_delta, "candidates": candidates})
    left_delta = int(right["frame"]) - int(left["frame"])
    left_time = float(right["time_sec"]) - float(left["time_sec"])
    reverse_rows.append({"frame": left_delta, "time_sec": left_time, "candidates": [{**left, "frame": left_delta, "time_sec": left_time}]})
    selected = select_ball_detections_from_trusted_seed(
        reverse_rows,
        seed={**right, "frame": 0, "time_sec": 0.0},
        fps=fps, max_link_speed_mps=max_speed, min_start_conf=min_start_conf, policy_version=policy,
    )
    rows = [originals[str(row["candidate_id"])] for _, row in sorted(selected.items()) if str(row.get("candidate_id")) in originals]
    reaches = any(str(row.get("candidate_id")) == str(left.get("candidate_id")) for row in rows)
    inner = [row for row in rows if int(left["frame"]) < int(row["frame"]) < int(right["frame"])]
    inner.reverse()
    return {"rows": inner, "candidate_ids": [str(row.get("candidate_id")) for row in inner], "reaches_boundary": reaches}


def _bridge_metrics(
    rows: list[Mapping[str, Any]],
    forward: Mapping[str, Any],
    backward: Mapping[str, Any],
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> dict[str, Any]:
    speeds = _speeds([left, *rows, right]) if rows else []
    return {
        "candidate_ids": [str(row.get("candidate_id")) for row in rows],
        "frames": [int(row["frame"]) for row in rows],
        "max_speed_mps": round(max(speeds), 3) if speeds else None,
        "left_link_speed_mps": round(speeds[0], 3) if speeds else None,
        "right_link_speed_mps": round(speeds[-1], 3) if speeds else None,
        "forward_candidate_ids": list(forward["candidate_ids"]),
        "backward_candidate_ids": list(backward["candidate_ids"]),
        "selected_rows": [
            {key: row.get(key) for key in ("candidate_id", "frame", "time_sec", "position_px", "position_m", "confidence", "width_px", "height_px")}
            for row in rows
        ],
    }


def _public_boundary(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in ("candidate_id", "frame", "time_sec", "position_px", "position_m", "confidence")}


def _boundary_candidate(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in ("candidate_id", "frame", "time_sec", "position_px", "position_m", "confidence")}


def _candidate(row: Any, frame: Mapping[str, Any]) -> dict[str, Any] | None:
    if not isinstance(row, Mapping) or not row.get("candidate_id"):
        return None
    return {**dict(row), "frame": int(row.get("frame") or frame["frame"]), "time_sec": float(row.get("time_sec") or frame["time_sec"])}


def _speeds(rows: list[Mapping[str, Any]]) -> list[float]:
    speeds = []
    for earlier, later in zip(rows, rows[1:]):
        a, b = earlier.get("position_m"), later.get("position_m")
        dt = float(later.get("time_sec") or 0.0) - float(earlier.get("time_sec") or 0.0)
        if not isinstance(a, list) or not isinstance(b, list) or len(a) != 2 or len(b) != 2 or dt <= 0:
            continue
        speeds.append((((float(a[0]) - float(b[0])) ** 2 + (float(a[1]) - float(b[1])) ** 2) ** .5) / dt)
    return speeds


def _empty_bridge() -> dict[str, Any]:
    return {"candidate_ids": [], "frames": [], "max_speed_mps": None, "left_link_speed_mps": None, "right_link_speed_mps": None, "forward_candidate_ids": [], "backward_candidate_ids": [], "selected_rows": []}
