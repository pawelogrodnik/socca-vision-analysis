from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "shadow_tracklet_stitching_candidates"
ALGORITHM_VERSION = "0.1.0"

DEFAULT_PARAMETERS: dict[str, Any] = {
    "max_gap_sec": 3.0,
    "max_distance_m": 12.0,
    "max_speed_mps": 9.5,
    "certain_team_confidence": 0.75,
    "certain_role_confidence": 0.70,
    "certain_goal_end_confidence": 0.70,
    "min_inside_pitch_ratio": 0.30,
    "max_candidates_per_direction": 5,
    "recommended_max_cost": 0.48,
    "recommended_min_margin": 0.08,
    "recommended_max_speed_mps": 6.5,
    "recommended_max_appearance_distance_rgb": 90.0,
    "recommended_short_gap_sec": 0.50,
    "recommended_short_gap_max_distance_m": 2.0,
    "recommended_short_gap_max_speed_mps": 4.5,
    "ambiguous_max_cost": 0.68,
    "occlusion_bonus": 0.12,
    "same_raw_tracker_bonus": 0.08,
    "unknown_team_penalty": 0.08,
    "uncertain_team_mismatch_penalty": 0.22,
    "weights": {
        "gap": 0.14,
        "distance": 0.22,
        "speed": 0.16,
        "velocity_prediction": 0.18,
        "appearance": 0.10,
        "bbox_profile": 0.07,
        "quality": 0.13,
    },
    "blocked_evidence_limit": 1000,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_shadow_stitching_candidates(
    tracklets: list[dict[str, Any]],
    quality_doc: dict[str, Any],
    occlusion_doc: dict[str, Any],
    global_identity: dict[str, Any],
    *,
    fps: float,
    generated_at: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Rank possible tracklet continuations without changing production identity."""
    params = _merge_parameters(parameters)
    quality_by_id = {
        str(row.get("tracklet_id")): row
        for row in quality_doc.get("tracklets") or []
        if row.get("status") == "clean"
    }
    clean = sorted(
        (
            row
            for row in tracklets
            if (quality_by_id.get(str(row.get("tracklet_id"))) or {}).get("quality_class")
            not in {"noise", "duplicate", None}
        ),
        key=_tracklet_sort_key,
    )
    focus_ids = {
        tracklet_id
        for tracklet_id, quality in quality_by_id.items()
        if quality.get("quality_class") in {"recoverable", "ambiguous"}
    }
    subject_by_tracklet = _subject_membership(global_identity)
    occlusion_edges = _occlusion_edge_evidence(occlusion_doc)

    edges: list[dict[str, Any]] = []
    blocked_counts: Counter[str] = Counter()
    blocked_pairs = 0
    blocked_evidence: list[dict[str, Any]] = []
    blocked_limit = int(params["blocked_evidence_limit"])
    for source in clean:
        source_id = str(source.get("tracklet_id"))
        source_end = _end_time(source)
        for target in clean:
            target_id = str(target.get("tracklet_id"))
            if source_id == target_id or (source_id not in focus_ids and target_id not in focus_ids):
                continue
            target_start = _start_time(target)
            if target_start <= source_end:
                continue
            gap_sec = target_start - source_end
            if gap_sec > float(params["max_gap_sec"]):
                continue
            result = score_stitching_edge(
                source,
                target,
                quality_by_id=quality_by_id,
                occlusion_event_ids=occlusion_edges.get((source_id, target_id)) or [],
                subject_by_tracklet=subject_by_tracklet,
                fps=fps,
                parameters=params,
            )
            if result.get("blocked"):
                blocked_pairs += 1
                for reason in result.get("blocked_reasons") or []:
                    blocked_counts[str(reason)] += 1
                if len(blocked_evidence) < blocked_limit:
                    blocked_evidence.append(result)
                continue
            edges.append(result)

    edges.sort(key=lambda row: (float(row["cost"]), row["source_tracklet_id"], row["target_tracklet_id"]))
    incoming: dict[str, list[dict[str, Any]]] = defaultdict(list)
    outgoing: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        incoming[str(edge["target_tracklet_id"])].append(edge)
        outgoing[str(edge["source_tracklet_id"])].append(edge)

    focus_rows: list[dict[str, Any]] = []
    recommendation_votes: Counter[str] = Counter()
    for tracklet_id in sorted(focus_ids):
        quality = quality_by_id[tracklet_id]
        predecessor = _direction_decision(
            incoming.get(tracklet_id) or [],
            focus_tracklet_id=tracklet_id,
            direction="predecessor",
            parameters=params,
        )
        successor = _direction_decision(
            outgoing.get(tracklet_id) or [],
            focus_tracklet_id=tracklet_id,
            direction="successor",
            parameters=params,
        )
        for decision in (predecessor, successor):
            if decision.get("status") == "recommended" and decision.get("candidate_key"):
                recommendation_votes[str(decision["candidate_key"])] += 1
        focus_rows.append(
            {
                "tracklet_id": tracklet_id,
                "quality_class": quality.get("quality_class"),
                "quality_confidence": quality.get("quality_confidence"),
                "team_label": quality.get("team_label"),
                "stable_subject_ids": subject_by_tracklet.get(tracklet_id) or [],
                "predecessor_decision": predecessor,
                "successor_decision": successor,
            }
        )

    for edge in edges:
        required_votes = int(edge["source_tracklet_id"] in focus_ids) + int(edge["target_tracklet_id"] in focus_ids)
        edge["recommendation_votes"] = recommendation_votes.get(str(edge["candidate_key"]), 0)
        edge["recommendation_votes_required"] = max(1, required_votes)
        edge["recommended"] = edge["recommendation_votes"] >= edge["recommendation_votes_required"]
    decisions = [row[key] for row in focus_rows for key in ("predecessor_decision", "successor_decision")]
    recommended_edges = [edge for edge in edges if edge.get("recommended")]
    contradictions = [
        edge
        for edge in recommended_edges
        if edge.get("current_identity_relation") == "different_subjects"
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or now_iso(),
        "mode": "shadow_read_only",
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION},
        "parameters": params,
        "source": {
            "tracklets": "tracklets_before_conservative_identity_v2",
            "quality": "identity_tracklet_quality.json",
            "occlusions": "identity_occlusion_events.json",
            "production_identity_usage": "evaluation_only_not_scoring",
        },
        "summary": {
            "eligible_tracklets": len(clean),
            "focus_tracklets": len(focus_ids),
            "candidate_edges": len(edges),
            "recommended_edges": len(recommended_edges),
            "ambiguous_direction_decisions": sum(1 for row in decisions if row.get("status") == "ambiguous"),
            "no_candidate_direction_decisions": sum(1 for row in decisions if row.get("status") == "no_candidate"),
            "blocked_pairs": blocked_pairs,
            "blocked_reason_counts": dict(sorted(blocked_counts.items())),
            "blocked_evidence_rows": len(blocked_evidence),
            "blocked_evidence_truncated": blocked_pairs > len(blocked_evidence),
            "recommended_current_same_subject": sum(
                1 for row in recommended_edges if row.get("current_identity_relation") == "same_subject"
            ),
            "recommended_current_different_subjects": len(contradictions),
            "recommended_current_unresolved": sum(
                1 for row in recommended_edges if row.get("current_identity_relation") == "unresolved"
            ),
        },
        "focus_tracklets": focus_rows,
        "candidate_edges": edges,
        "blocked_evidence": blocked_evidence,
        "recommended_identity_contradictions": contradictions,
    }


def score_stitching_edge(
    source: dict[str, Any],
    target: dict[str, Any],
    *,
    quality_by_id: dict[str, dict[str, Any]],
    occlusion_event_ids: list[str],
    subject_by_tracklet: dict[str, list[str]],
    fps: float,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    params = _merge_parameters(parameters)
    source_id = str(source.get("tracklet_id"))
    target_id = str(target.get("tracklet_id"))
    source_quality = quality_by_id.get(source_id) or {}
    target_quality = quality_by_id.get(target_id) or {}
    gap_sec = _start_time(target) - _end_time(source)
    distance_m = _distance(_last_pitch(source), _first_pitch(target))
    required_speed = (
        distance_m / max(gap_sec, 1.0 / max(fps, 1e-6))
        if distance_m is not None
        else None
    )
    blocked = _hard_constraint_reasons(
        source,
        target,
        source_quality=source_quality,
        target_quality=target_quality,
        gap_sec=gap_sec,
        distance_m=distance_m,
        required_speed_mps=required_speed,
        parameters=params,
    )
    base = {
        "candidate_key": _candidate_key(source_id, target_id),
        "source_tracklet_id": source_id,
        "target_tracklet_id": target_id,
        "source_quality_class": source_quality.get("quality_class"),
        "target_quality_class": target_quality.get("quality_class"),
        "source_stable_subject_ids": subject_by_tracklet.get(source_id) or [],
        "target_stable_subject_ids": subject_by_tracklet.get(target_id) or [],
        "current_identity_relation": _identity_relation(
            subject_by_tracklet.get(source_id) or [],
            subject_by_tracklet.get(target_id) or [],
        ),
        "gap_sec": _round_optional(gap_sec, 3),
        "distance_m": _round_optional(distance_m, 3),
        "required_speed_mps": _round_optional(required_speed, 3),
        "occlusion_event_ids": sorted(set(str(item) for item in occlusion_event_ids)),
    }
    if blocked:
        return {**base, "blocked": True, "blocked_reasons": blocked}

    velocity_distance = _velocity_prediction_distance(source, target, gap_sec=gap_sec)
    appearance_distance = _appearance_distance(source, target)
    bbox_ratio = _bbox_area_ratio(source.get("last_bbox_xyxy"), target.get("first_bbox_xyxy"))
    costs = {
        "gap": _clamp(gap_sec / float(params["max_gap_sec"])),
        "distance": _clamp(float(distance_m or 0.0) / float(params["max_distance_m"])),
        "speed": _clamp(float(required_speed or 0.0) / float(params["max_speed_mps"])),
        "velocity_prediction": (
            _clamp(velocity_distance / float(params["max_distance_m"]))
            if velocity_distance is not None
            else 0.45
        ),
        "appearance": _clamp(appearance_distance / 180.0) if appearance_distance is not None else 0.45,
        "bbox_profile": _bbox_ratio_cost(bbox_ratio),
        "quality": 1.0
        - _clamp(
            (
                float(source_quality.get("quality_confidence") or 0.0)
                + float(target_quality.get("quality_confidence") or 0.0)
            )
            / 2.0
        ),
    }
    weights = params["weights"]
    cost = sum(float(weights[key]) * value for key, value in costs.items())
    evidence: list[str] = ["temporal_gap", "pitch_distance", "required_speed"]
    bonuses: dict[str, float] = {}
    if occlusion_event_ids:
        bonuses["shared_occlusion_event"] = float(params["occlusion_bonus"])
        evidence.append("shared_occlusion_event")
    same_raw_tracker = bool(
        _raw_tracker_id(source) is not None
        and _raw_tracker_id(source) == _raw_tracker_id(target)
    )
    if same_raw_tracker:
        bonuses["same_raw_tracker"] = float(params["same_raw_tracker_bonus"])
        evidence.append("same_raw_tracker")
    source_team = _team_label(source)
    target_team = _team_label(target)
    penalties: dict[str, float] = {}
    if "U" in {source_team, target_team}:
        penalties["unknown_team"] = float(params["unknown_team_penalty"])
    elif source_team != target_team:
        penalties["uncertain_team_mismatch"] = float(params["uncertain_team_mismatch_penalty"])
    if appearance_distance is not None:
        evidence.append("appearance_profile")
    if velocity_distance is not None:
        evidence.append("velocity_prediction")
    cost = _clamp(cost - sum(bonuses.values()) + sum(penalties.values()))
    return {
        **base,
        "blocked": False,
        "cost": round(cost, 4),
        "base_confidence": round(1.0 - cost, 4),
        "feature_costs": {key: round(value, 4) for key, value in costs.items()},
        "bonuses": bonuses,
        "penalties": penalties,
        "velocity_prediction_distance_m": _round_optional(velocity_distance, 3),
        "appearance_distance_rgb": _round_optional(appearance_distance, 3),
        "bbox_area_ratio": _round_optional(bbox_ratio, 3),
        "evidence": evidence,
        "recommendation_guard_reasons": _recommendation_guard_reasons(
            cost=cost,
            gap_sec=gap_sec,
            distance_m=float(distance_m or 0.0),
            required_speed_mps=float(required_speed or 0.0),
            appearance_distance_rgb=appearance_distance,
            has_occlusion_evidence=bool(occlusion_event_ids),
            same_raw_tracker=same_raw_tracker,
            has_known_team_mismatch=(
                source_team in {"A", "B"}
                and target_team in {"A", "B"}
                and source_team != target_team
            ),
            parameters=params,
        ),
    }


def _hard_constraint_reasons(
    source: dict[str, Any],
    target: dict[str, Any],
    *,
    source_quality: dict[str, Any],
    target_quality: dict[str, Any],
    gap_sec: float,
    distance_m: float | None,
    required_speed_mps: float | None,
    parameters: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    if gap_sec <= 0:
        reasons.append("temporal_overlap")
    elif gap_sec > float(parameters["max_gap_sec"]):
        reasons.append("gap_too_large")
    if distance_m is None:
        reasons.append("missing_pitch_position")
    elif distance_m > float(parameters["max_distance_m"]):
        reasons.append("distance_too_large")
    if required_speed_mps is not None and required_speed_mps > float(parameters["max_speed_mps"]):
        reasons.append("impossible_required_speed")
    source_team = _team_label(source)
    target_team = _team_label(target)
    team_confidence = min(float(source.get("team_confidence") or 0.0), float(target.get("team_confidence") or 0.0))
    if (
        source_team in {"A", "B"}
        and target_team in {"A", "B"}
        and source_team != target_team
        and team_confidence >= float(parameters["certain_team_confidence"])
    ):
        reasons.append("certain_team_mismatch")
    source_role = str(source.get("role") or "unknown")
    target_role = str(target.get("role") or "unknown")
    role_confidence = min(float(source.get("role_confidence") or 0.0), float(target.get("role_confidence") or 0.0))
    if (
        {source_role, target_role} == {"goalkeeper", "field_player"}
        and role_confidence >= float(parameters["certain_role_confidence"])
    ):
        reasons.append("certain_role_mismatch")
    source_goal = source.get("goal_end")
    target_goal = target.get("goal_end")
    if (
        source_role == target_role == "goalkeeper"
        and source_goal
        and target_goal
        and source_goal != target_goal
        and role_confidence >= float(parameters["certain_goal_end_confidence"])
    ):
        reasons.append("certain_goal_end_mismatch")
    if min(
        float(source_quality.get("inside_pitch_ratio") or 0.0),
        float(target_quality.get("inside_pitch_ratio") or 0.0),
    ) < float(parameters["min_inside_pitch_ratio"]):
        reasons.append("mostly_outside_pitch")
    return sorted(set(reasons))


def _direction_decision(
    edges: list[dict[str, Any]],
    *,
    focus_tracklet_id: str,
    direction: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    ordered = sorted(edges, key=lambda row: (float(row["cost"]), str(row["candidate_key"])))
    limit = int(parameters["max_candidates_per_direction"])
    if not ordered:
        return {"status": "no_candidate", "candidate_key": None, "confidence": 0.0, "margin": None, "candidates": []}
    best = ordered[0]
    second_cost = float(ordered[1]["cost"]) if len(ordered) > 1 else 1.0
    margin = max(0.0, second_cost - float(best["cost"]))
    if (
        float(best["cost"]) <= float(parameters["recommended_max_cost"])
        and margin >= float(parameters["recommended_min_margin"])
        and not best.get("recommendation_guard_reasons")
    ):
        status = "recommended"
    elif float(best["cost"]) <= float(parameters["ambiguous_max_cost"]):
        status = "ambiguous"
    else:
        status = "no_candidate"
    confidence = _clamp((1.0 - float(best["cost"])) * (0.55 + min(0.45, margin * 2.0)))
    return {
        "status": status,
        "direction": direction,
        "focus_tracklet_id": focus_tracklet_id,
        "candidate_key": best["candidate_key"] if status != "no_candidate" else None,
        "counterpart_tracklet_id": (
            best["source_tracklet_id"] if direction == "predecessor" else best["target_tracklet_id"]
        ),
        "cost": best["cost"],
        "confidence": round(confidence, 4),
        "margin": round(margin, 4),
        "candidates": [edge["candidate_key"] for edge in ordered[:limit]],
        "recommendation_guard_reasons": best.get("recommendation_guard_reasons") or [],
    }


def _recommendation_guard_reasons(
    *,
    cost: float,
    gap_sec: float,
    distance_m: float,
    required_speed_mps: float,
    appearance_distance_rgb: float | None,
    has_occlusion_evidence: bool,
    same_raw_tracker: bool,
    has_known_team_mismatch: bool,
    parameters: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    if cost > float(parameters["recommended_max_cost"]):
        reasons.append("cost_above_recommended_threshold")
    if required_speed_mps > float(parameters["recommended_max_speed_mps"]):
        reasons.append("required_speed_too_high_for_recommendation")
    if (
        appearance_distance_rgb is not None
        and appearance_distance_rgb > float(parameters["recommended_max_appearance_distance_rgb"])
    ):
        reasons.append("appearance_distance_too_high_for_recommendation")
    if has_known_team_mismatch:
        reasons.append("team_mismatch_not_safe_for_recommendation")
    short_continuity = bool(
        gap_sec <= float(parameters["recommended_short_gap_sec"])
        and distance_m <= float(parameters["recommended_short_gap_max_distance_m"])
        and required_speed_mps <= float(parameters["recommended_short_gap_max_speed_mps"])
    )
    if not (has_occlusion_evidence or same_raw_tracker or short_continuity):
        reasons.append("missing_strong_continuity_evidence")
    return reasons


def _merge_parameters(parameters: dict[str, Any] | None) -> dict[str, Any]:
    incoming = parameters or {}
    return {
        **DEFAULT_PARAMETERS,
        **incoming,
        "weights": {**DEFAULT_PARAMETERS["weights"], **(incoming.get("weights") or {})},
    }


def _subject_membership(global_identity: dict[str, Any]) -> dict[str, list[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    for slot in global_identity.get("slots") or []:
        subject_id = str(slot.get("stable_subject_id") or slot.get("slot_id") or "")
        for tracklet_id in slot.get("tracklet_ids") or []:
            if subject_id:
                result[str(tracklet_id)].add(subject_id)
    return {key: sorted(value) for key, value in result.items()}


def _occlusion_edge_evidence(occlusion_doc: dict[str, Any]) -> dict[tuple[str, str], list[str]]:
    result: dict[tuple[str, str], set[str]] = defaultdict(set)
    for event in occlusion_doc.get("events") or []:
        event_id = str(event.get("event_id"))
        sources = [str(item) for item in event.get("outgoing_tracklet_ids") or []]
        targets = [str(item) for item in event.get("incoming_tracklet_ids") or []]
        for source_id in sources:
            for target_id in targets:
                if source_id != target_id:
                    result[(source_id, target_id)].add(event_id)
    return {key: sorted(value) for key, value in result.items()}


def _identity_relation(source_subjects: list[str], target_subjects: list[str]) -> str:
    if not source_subjects or not target_subjects:
        return "unresolved"
    return "same_subject" if set(source_subjects) & set(target_subjects) else "different_subjects"


def _candidate_key(source_id: str, target_id: str) -> str:
    payload = json.dumps(
        {"kind": "tracklet-stitch", "source": source_id, "target": target_id, "version": 1},
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"stitch:v1:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _velocity_prediction_distance(source: dict[str, Any], target: dict[str, Any], *, gap_sec: float) -> float | None:
    positions = _positions(source)
    target_point = _first_pitch(target)
    if len(positions) < 2 or target_point is None:
        return None
    last = positions[-1]
    previous = next(
        (row for row in reversed(positions[:-1]) if _pitch(row) is not None and _time(row) < _time(last)),
        None,
    )
    last_pitch = _pitch(last)
    previous_pitch = _pitch(previous) if previous else None
    if last_pitch is None or previous_pitch is None or previous is None:
        return None
    dt = _time(last) - _time(previous)
    if dt <= 0:
        return None
    velocity = ((last_pitch[0] - previous_pitch[0]) / dt, (last_pitch[1] - previous_pitch[1]) / dt)
    predicted = (last_pitch[0] + velocity[0] * gap_sec, last_pitch[1] + velocity[1] * gap_sec)
    return _distance(predicted, target_point)


def _appearance_distance(source: dict[str, Any], target: dict[str, Any]) -> float | None:
    left = source.get("appearance_rgb")
    right = target.get("appearance_rgb")
    if not isinstance(left, (list, tuple)) or not isinstance(right, (list, tuple)) or len(left) < 3 or len(right) < 3:
        return None
    return math.sqrt(sum((float(left[index]) - float(right[index])) ** 2 for index in range(3)))


def _bbox_area_ratio(left: Any, right: Any) -> float | None:
    left_area = _bbox_area(left)
    right_area = _bbox_area(right)
    if left_area <= 0 or right_area <= 0:
        return None
    return max(left_area, right_area) / min(left_area, right_area)


def _bbox_area(value: Any) -> float:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return 0.0
    x1, y1, x2, y2 = [float(item) for item in value]
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def _bbox_ratio_cost(ratio: float | None) -> float:
    if ratio is None:
        return 0.45
    return _clamp(math.log(max(1.0, ratio)) / math.log(4.0))


def _positions(tracklet: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        tracklet.get("positions") or tracklet.get("positions_m") or [],
        key=lambda row: (int(row.get("frame") or 0), float(row.get("time_sec") or 0.0)),
    )


def _first_pitch(tracklet: dict[str, Any]) -> tuple[float, float] | None:
    value = tracklet.get("first_pitch_m")
    return _point(value) or (_pitch(_positions(tracklet)[0]) if _positions(tracklet) else None)


def _last_pitch(tracklet: dict[str, Any]) -> tuple[float, float] | None:
    value = tracklet.get("last_pitch_m")
    return _point(value) or (_pitch(_positions(tracklet)[-1]) if _positions(tracklet) else None)


def _pitch(position: dict[str, Any] | None) -> tuple[float, float] | None:
    return _point((position or {}).get("smoothed_pitch_m") or (position or {}).get("pitch_m"))


def _point(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    return float(value[0]), float(value[1])


def _time(position: dict[str, Any]) -> float:
    return float(position.get("time_sec") or 0.0)


def _start_time(tracklet: dict[str, Any]) -> float:
    return float(tracklet.get("start_time_sec") or 0.0)


def _end_time(tracklet: dict[str, Any]) -> float:
    return float(tracklet.get("end_time_sec") or 0.0)


def _team_label(tracklet: dict[str, Any]) -> str:
    value = str(tracklet.get("team_label") or tracklet.get("team_candidate") or "U").upper()
    return value if value in {"A", "B"} else "U"


def _raw_tracker_id(tracklet: dict[str, Any]) -> str | None:
    value = tracklet.get("source_tracker_id")
    if value is None:
        value = tracklet.get("source_track_id")
    return str(value) if value is not None else None


def _distance(left: Any, right: Any) -> float | None:
    left_point = _point(left)
    right_point = _point(right)
    if left_point is None or right_point is None:
        return None
    return math.hypot(left_point[0] - right_point[0], left_point[1] - right_point[1])


def _tracklet_sort_key(tracklet: dict[str, Any]) -> tuple[float, float, str]:
    return _start_time(tracklet), _end_time(tracklet), str(tracklet.get("tracklet_id") or "")


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def _round_optional(value: float | None, digits: int) -> float | None:
    return round(float(value), digits) if value is not None else None
