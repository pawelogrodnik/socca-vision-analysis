from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import statistics
from typing import Any

from app.services.identity_stitching_shadow import DEFAULT_PARAMETERS as EDGE_DEFAULT_PARAMETERS
from app.services.identity_stitching_shadow import score_stitching_edge


SCHEMA_VERSION = "0.2.0"
ALGORITHM_NAME = "shadow_joint_occlusion_assignment"
ALGORITHM_VERSION = "0.2.0"

DEFAULT_PARAMETERS: dict[str, Any] = {
    "transition_context_frames": 15,
    "minimum_event_confidence": 0.55,
    "recommended_max_mean_cost": 0.48,
    "recommended_min_assignment_margin": 0.12,
    "endpoint_low_confidence_threshold": 0.25,
    "endpoint_low_bbox_height_px": 45.0,
    "partial_recommended_max_edge_cost": 0.32,
    "partial_recommended_min_edge_margin": 0.08,
    "partial_unreliable_source_edge_cost": 0.06,
    "edge_parameters": EDGE_DEFAULT_PARAMETERS,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_shadow_occlusion_assignments(
    tracklets: list[dict[str, Any]],
    quality_doc: dict[str, Any],
    occlusion_doc: dict[str, Any],
    global_identity: dict[str, Any],
    *,
    fps: float,
    generated_at: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare both two-person assignments around an occlusion without applying either one."""
    params = _merge_parameters(parameters)
    tracklet_by_id = {
        str(row.get("tracklet_id")): row
        for row in tracklets
        if row.get("tracklet_id") is not None
    }
    quality_by_id = {
        str(row.get("tracklet_id")): row
        for row in quality_doc.get("tracklets") or []
        if row.get("status") == "clean"
    }
    eligible_ids = {
        tracklet_id
        for tracklet_id, row in quality_by_id.items()
        if row.get("quality_class") not in {"noise", "duplicate", None}
        and tracklet_id in tracklet_by_id
    }
    subject_by_tracklet = _subject_membership(global_identity)
    groups = _joint_event_groups(
        occlusion_doc,
        tracklet_by_id=tracklet_by_id,
        eligible_ids=eligible_ids,
        fps=fps,
        parameters=params,
    )

    cases = [
        _score_group(
            group,
            tracklet_by_id=tracklet_by_id,
            quality_by_id=quality_by_id,
            subject_by_tracklet=subject_by_tracklet,
            fps=fps,
            parameters=params,
        )
        for group in groups
    ]
    decision_counts = Counter(str(row["decision"]["status"]) for row in cases)
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
            "eligible_tracklets": len(eligible_ids),
            "joint_occlusion_cases": len(cases),
            "recommended_cases": sum(1 for row in cases if row["decision"].get("recommended_assignment_id")),
            "recommended_full_assignments": sum(
                1
                for row in cases
                if row["decision"].get("recommended_assignment_id") in {"assignment_a", "assignment_b"}
            ),
            "recommended_partial_continuations": decision_counts["partial_continuation"],
            "keep_current": decision_counts["keep_current"],
            "suspected_swap": decision_counts["suspected_swap"],
            "unresolved_current": decision_counts["unresolved_current"],
            "identity_contradiction": decision_counts["identity_contradiction"],
            "ambiguous": decision_counts["ambiguous"],
            "blocked": decision_counts["blocked"],
        },
        "cases": cases,
    }


def _score_group(
    group: dict[str, Any],
    *,
    tracklet_by_id: dict[str, dict[str, Any]],
    quality_by_id: dict[str, dict[str, Any]],
    subject_by_tracklet: dict[str, list[str]],
    fps: float,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    sources = list(group["source_tracklet_ids"])
    targets = list(group["target_tracklet_ids"])
    source_views = {
        tracklet_id: _endpoint_view(
            tracklet_by_id[tracklet_id],
            end_frame=int(group["start_frame"]) - 1,
            fps=fps,
        )
        for tracklet_id in sources
    }
    target_views = {
        tracklet_id: _endpoint_view(
            tracklet_by_id[tracklet_id],
            start_frame=int(group["end_frame"]) + 1,
            fps=fps,
        )
        for tracklet_id in targets
    }
    pairings = (
        ("assignment_a", ((sources[0], targets[0]), (sources[1], targets[1]))),
        ("assignment_b", ((sources[0], targets[1]), (sources[1], targets[0]))),
    )
    assignments = [
        _score_assignment(
            assignment_id,
            pairs,
            event_ids=group["occlusion_event_ids"],
            source_views=source_views,
            target_views=target_views,
            quality_by_id=quality_by_id,
            subject_by_tracklet=subject_by_tracklet,
            fps=fps,
            edge_parameters=parameters["edge_parameters"],
        )
        for assignment_id, pairs in pairings
    ]
    endpoint_reliability = {
        "sources": {
            tracklet_id: _endpoint_reliability(
                view,
                endpoint="source",
                parameters=parameters,
            )
            for tracklet_id, view in source_views.items()
        },
        "targets": {
            tracklet_id: _endpoint_reliability(
                view,
                endpoint="target",
                parameters=parameters,
            )
            for tracklet_id, view in target_views.items()
        },
    }
    decision = _assignment_decision(
        assignments,
        endpoint_reliability=endpoint_reliability,
        parameters=parameters,
    )
    return {
        "case_key": _case_key(group),
        **group,
        "endpoint_reliability": endpoint_reliability,
        "assignments": assignments,
        "decision": decision,
    }


def _score_assignment(
    assignment_id: str,
    pairs: tuple[tuple[str, str], tuple[str, str]],
    *,
    event_ids: list[str],
    source_views: dict[str, dict[str, Any]],
    target_views: dict[str, dict[str, Any]],
    quality_by_id: dict[str, dict[str, Any]],
    subject_by_tracklet: dict[str, list[str]],
    fps: float,
    edge_parameters: dict[str, Any],
) -> dict[str, Any]:
    edges = [
        score_stitching_edge(
            source_views[source_id],
            target_views[target_id],
            quality_by_id=quality_by_id,
            occlusion_event_ids=event_ids,
            subject_by_tracklet=subject_by_tracklet,
            fps=fps,
            parameters=edge_parameters,
        )
        for source_id, target_id in pairs
    ]
    blocked_reasons = sorted(
        {
            str(reason)
            for edge in edges
            for reason in edge.get("blocked_reasons") or []
        }
    )
    blocked = bool(blocked_reasons)
    mean_cost = None if blocked else sum(float(edge["cost"]) for edge in edges) / len(edges)
    identity_relations = [str(edge.get("current_identity_relation") or "unresolved") for edge in edges]
    if all(relation == "same_subject" for relation in identity_relations):
        matches_current_identity: bool | None = True
    elif any(relation == "different_subjects" for relation in identity_relations):
        matches_current_identity = False
    else:
        matches_current_identity = None
    guard_reasons = sorted(
        {
            str(reason)
            for edge in edges
            for reason in edge.get("recommendation_guard_reasons") or []
        }
    )
    return {
        "assignment_id": assignment_id,
        "pairs": [
            {"source_tracklet_id": source_id, "target_tracklet_id": target_id}
            for source_id, target_id in pairs
        ],
        "blocked": blocked,
        "blocked_reasons": blocked_reasons,
        "mean_cost": round(mean_cost, 4) if mean_cost is not None else None,
        "base_confidence": round(1.0 - mean_cost, 4) if mean_cost is not None else 0.0,
        "matches_current_identity": matches_current_identity,
        "current_identity_relations": identity_relations,
        "recommendation_guard_reasons": guard_reasons,
        "edges": edges,
    }


def _assignment_decision(
    assignments: list[dict[str, Any]],
    *,
    endpoint_reliability: dict[str, dict[str, dict[str, Any]]],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    valid = sorted(
        (row for row in assignments if not row["blocked"] and row["mean_cost"] is not None),
        key=lambda row: (float(row["mean_cost"]), str(row["assignment_id"])),
    )
    if not valid:
        return {
            "status": "blocked",
            "recommended_assignment_id": None,
            "confidence": 0.0,
            "margin": None,
            "reasons": ["both_assignments_blocked"],
        }
    best = valid[0]
    second_cost = float(valid[1]["mean_cost"]) if len(valid) > 1 else 1.0
    margin = max(0.0, second_cost - float(best["mean_cost"]))
    partial = _partial_continuation_decision(
        assignments,
        best_assignment=best,
        endpoint_reliability=endpoint_reliability,
        parameters=parameters,
    )
    if partial is not None:
        return partial
    reasons = list(best.get("recommendation_guard_reasons") or [])
    if float(best["mean_cost"]) > float(parameters["recommended_max_mean_cost"]):
        reasons.append("mean_cost_above_recommended_threshold")
    if len(valid) > 1 and margin < float(parameters["recommended_min_assignment_margin"]):
        reasons.append("assignment_margin_too_small")
    reasons = sorted(set(reasons))
    if reasons:
        return {
            "status": "ambiguous",
            "recommended_assignment_id": None,
            "best_assignment_id": best["assignment_id"],
            "confidence": 0.0,
            "margin": round(margin, 4),
            "reasons": reasons,
        }

    current_assignments = [row for row in assignments if row["matches_current_identity"] is True]
    if best["matches_current_identity"] is True:
        status = "keep_current"
    elif current_assignments:
        status = "suspected_swap"
    elif best["matches_current_identity"] is None:
        status = "unresolved_current"
    else:
        status = "identity_contradiction"
    confidence = max(0.0, min(1.0, (1.0 - float(best["mean_cost"])) * (0.55 + min(0.45, margin * 2.0))))
    return {
        "status": status,
        "recommended_assignment_id": best["assignment_id"],
        "current_assignment_id": current_assignments[0]["assignment_id"] if len(current_assignments) == 1 else None,
        "confidence": round(confidence, 4),
        "margin": round(margin, 4),
        "reasons": [],
    }


def _partial_continuation_decision(
    assignments: list[dict[str, Any]],
    *,
    best_assignment: dict[str, Any],
    endpoint_reliability: dict[str, dict[str, dict[str, Any]]],
    parameters: dict[str, Any],
) -> dict[str, Any] | None:
    source_reliability = endpoint_reliability.get("sources") or {}
    target_reliability = endpoint_reliability.get("targets") or {}
    unreliable_sources = sorted(
        tracklet_id for tracklet_id, row in source_reliability.items() if not row.get("reliable", True)
    )
    unreliable_targets = sorted(
        tracklet_id for tracklet_id, row in target_reliability.items() if not row.get("reliable", True)
    )

    eligible_edges: list[dict[str, Any]] = []
    reason: str | None = None
    if len(unreliable_targets) == 1 and not unreliable_sources:
        reliable_target_ids = set(target_reliability) - set(unreliable_targets)
        eligible_edges = [
            edge
            for edge in _unique_edges(assignments)
            if edge.get("target_tracklet_id") in reliable_target_ids
        ]
        reason = "one_unreliable_target_endpoint"
    elif len(unreliable_sources) == 1 and not unreliable_targets:
        unreliable_source_id = unreliable_sources[0]
        assigned_unreliable_edge = next(
            (
                edge
                for edge in best_assignment.get("edges") or []
                if edge.get("source_tracklet_id") == unreliable_source_id
            ),
            None,
        )
        if (
            assigned_unreliable_edge is None
            or assigned_unreliable_edge.get("blocked")
            or assigned_unreliable_edge.get("recommendation_guard_reasons")
            or float(assigned_unreliable_edge.get("cost") or 0.0)
            > float(parameters["partial_unreliable_source_edge_cost"])
        ):
            reliable_source_ids = set(source_reliability) - set(unreliable_sources)
            eligible_edges = [
                edge
                for edge in _unique_edges(assignments)
                if edge.get("source_tracklet_id") in reliable_source_ids
            ]
            reason = "one_unreliable_source_endpoint"

    ordered = sorted(
        (
            edge
            for edge in eligible_edges
            if not edge.get("blocked")
            and edge.get("cost") is not None
            and not edge.get("recommendation_guard_reasons")
        ),
        key=lambda row: (float(row["cost"]), str(row["candidate_key"])),
    )
    if reason is None or not ordered:
        return None
    best_edge = ordered[0]
    second_cost = float(ordered[1]["cost"]) if len(ordered) > 1 else 1.0
    edge_margin = max(0.0, second_cost - float(best_edge["cost"]))
    if (
        float(best_edge["cost"]) > float(parameters["partial_recommended_max_edge_cost"])
        or edge_margin < float(parameters["partial_recommended_min_edge_margin"])
    ):
        return None
    confidence = max(
        0.0,
        min(1.0, (1.0 - float(best_edge["cost"])) * (0.55 + min(0.45, edge_margin * 2.0))),
    )
    pair = {
        "source_tracklet_id": str(best_edge["source_tracklet_id"]),
        "target_tracklet_id": str(best_edge["target_tracklet_id"]),
    }
    return {
        "status": "partial_continuation",
        "recommended_assignment_id": "partial",
        "recommended_pairs": [pair],
        "best_assignment_id": best_assignment.get("assignment_id"),
        "confidence": round(confidence, 4),
        "margin": round(edge_margin, 4),
        "reasons": [reason],
    }


def _unique_edges(assignments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for assignment in assignments:
        for edge in assignment.get("edges") or []:
            key = (str(edge.get("source_tracklet_id")), str(edge.get("target_tracklet_id")))
            rows[key] = edge
    return [rows[key] for key in sorted(rows)]


def _endpoint_reliability(
    tracklet: dict[str, Any],
    *,
    endpoint: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    positions = _positions(tracklet)
    position = positions[-1] if endpoint == "source" and positions else positions[0] if positions else {}
    bbox = position.get("bbox_xyxy") or (
        tracklet.get("last_bbox_xyxy") if endpoint == "source" else tracklet.get("first_bbox_xyxy")
    )
    width, height = _bbox_size(bbox)
    confidence = float(position.get("confidence") or 0.0)
    heights = [candidate_height for row in positions if (candidate_height := _bbox_size(row.get("bbox_xyxy"))[1]) > 0]
    median_height = statistics.median(heights) if heights else 0.0
    reasons: list[str] = []
    if (
        confidence <= float(parameters["endpoint_low_confidence_threshold"])
        and height <= float(parameters["endpoint_low_bbox_height_px"])
    ):
        reasons.append("low_confidence_small_bbox_fragment")
    return {
        "reliable": not reasons,
        "reasons": reasons,
        "confidence": round(confidence, 4),
        "bbox_width_px": round(width, 2),
        "bbox_height_px": round(height, 2),
        "tracklet_median_bbox_height_px": round(float(median_height), 2),
        "tracklet_positions": len(positions),
    }


def _bbox_size(value: Any) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return 0.0, 0.0
    x1, y1, x2, y2 = [float(item) for item in value]
    return max(0.0, x2 - x1), max(0.0, y2 - y1)


def _joint_event_groups(
    occlusion_doc: dict[str, Any],
    *,
    tracklet_by_id: dict[str, dict[str, Any]],
    eligible_ids: set[str],
    fps: float,
    parameters: dict[str, Any],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, tuple[str, ...], tuple[str, ...]], dict[str, Any]] = {}
    context = int(parameters["transition_context_frames"])
    minimum_confidence = float(parameters["minimum_event_confidence"])
    eligible = [tracklet_by_id[item] for item in sorted(eligible_ids)]
    for event in sorted(occlusion_doc.get("events") or [], key=_event_sort_key):
        teams = [str(item) for item in event.get("team_labels") or []]
        if len(teams) != 1 or teams[0] not in {"A", "B"}:
            continue
        if float(event.get("confidence") or 0.0) < minimum_confidence:
            continue
        team = teams[0]
        start_frame = int(event.get("start_frame") or 0)
        end_frame = int(event.get("end_frame") or start_frame)
        sources = sorted(
            str(row["tracklet_id"])
            for row in eligible
            if _team_label(row) == team
            and _start_frame(row, fps=fps) < start_frame
            and start_frame - context <= _end_frame(row, fps=fps) <= end_frame + context
        )
        targets = sorted(
            str(row["tracklet_id"])
            for row in eligible
            if _team_label(row) == team
            and _end_frame(row, fps=fps) > end_frame
            and start_frame - context <= _start_frame(row, fps=fps) <= end_frame + context
        )
        if len(sources) != 2 or len(targets) != 2 or set(sources) & set(targets):
            continue
        key = (team, tuple(sources), tuple(targets))
        row = grouped.setdefault(
            key,
            {
                "team_label": team,
                "start_frame": start_frame,
                "end_frame": end_frame,
                "source_tracklet_ids": sources,
                "target_tracklet_ids": targets,
                "occlusion_event_ids": [],
                "event_confidences": [],
            },
        )
        row["start_frame"] = min(int(row["start_frame"]), start_frame)
        row["end_frame"] = max(int(row["end_frame"]), end_frame)
        row["occlusion_event_ids"].append(str(event.get("event_id")))
        row["event_confidences"].append(float(event.get("confidence") or 0.0))

    result: list[dict[str, Any]] = []
    for row in grouped.values():
        row["occlusion_event_ids"] = sorted(set(row["occlusion_event_ids"]))
        row["event_confidence"] = round(max(row.pop("event_confidences")), 4)
        row["start_time_sec"] = round(int(row["start_frame"]) / max(fps, 1e-6), 3)
        row["end_time_sec"] = round(int(row["end_frame"]) / max(fps, 1e-6), 3)
        result.append(row)
    return sorted(result, key=lambda row: (int(row["start_frame"]), row["team_label"], row["source_tracklet_ids"]))


def _merge_parameters(parameters: dict[str, Any] | None) -> dict[str, Any]:
    incoming = parameters or {}
    return {
        **DEFAULT_PARAMETERS,
        **incoming,
        "edge_parameters": {
            **EDGE_DEFAULT_PARAMETERS,
            **(incoming.get("edge_parameters") or {}),
            "weights": {
                **EDGE_DEFAULT_PARAMETERS["weights"],
                **((incoming.get("edge_parameters") or {}).get("weights") or {}),
            },
        },
    }


def _subject_membership(global_identity: dict[str, Any]) -> dict[str, list[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    for slot in global_identity.get("slots") or []:
        subject_id = str(slot.get("stable_subject_id") or slot.get("slot_id") or "")
        for tracklet_id in slot.get("tracklet_ids") or []:
            if subject_id:
                result[str(tracklet_id)].add(subject_id)
    return {key: sorted(value) for key, value in result.items()}


def _case_key(group: dict[str, Any]) -> str:
    payload = json.dumps(
        {
            "kind": "joint-occlusion-assignment",
            "sources": group["source_tracklet_ids"],
            "targets": group["target_tracklet_ids"],
            "team": group["team_label"],
            "version": 1,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"occlusion-assignment:v1:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _event_sort_key(event: dict[str, Any]) -> tuple[int, int, str]:
    return (
        int(event.get("start_frame") or 0),
        int(event.get("end_frame") or 0),
        str(event.get("event_id") or ""),
    )


def _positions(tracklet: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        tracklet.get("positions") or tracklet.get("positions_m") or [],
        key=lambda row: (int(row.get("frame") or 0), float(row.get("time_sec") or 0.0)),
    )


def _endpoint_view(
    tracklet: dict[str, Any],
    *,
    fps: float,
    start_frame: int | None = None,
    end_frame: int | None = None,
) -> dict[str, Any]:
    positions = [
        row
        for row in _positions(tracklet)
        if (start_frame is None or int(row.get("frame") or 0) >= start_frame)
        and (end_frame is None or int(row.get("frame") or 0) <= end_frame)
    ]
    if not positions:
        return tracklet
    first = positions[0]
    last = positions[-1]
    first_frame = int(first.get("frame") or 0)
    last_frame = int(last.get("frame") or first_frame)
    return {
        **tracklet,
        "positions": positions,
        "positions_m": positions,
        "start_time_sec": float(first.get("time_sec") or first_frame / max(fps, 1e-6)),
        "end_time_sec": float(last.get("time_sec") or last_frame / max(fps, 1e-6)),
        "first_pitch_m": first.get("smoothed_pitch_m") or first.get("pitch_m"),
        "last_pitch_m": last.get("smoothed_pitch_m") or last.get("pitch_m"),
        "first_bbox_xyxy": first.get("bbox_xyxy"),
        "last_bbox_xyxy": last.get("bbox_xyxy"),
    }


def _start_frame(tracklet: dict[str, Any], *, fps: float) -> int:
    positions = _positions(tracklet)
    return int(positions[0].get("frame") or 0) if positions else int(round(float(tracklet.get("start_time_sec") or 0.0) * fps))


def _end_frame(tracklet: dict[str, Any], *, fps: float) -> int:
    positions = _positions(tracklet)
    return int(positions[-1].get("frame") or 0) if positions else int(round(float(tracklet.get("end_time_sec") or 0.0) * fps))


def _team_label(tracklet: dict[str, Any]) -> str:
    value = str(tracklet.get("team_label") or tracklet.get("team_candidate") or "U").upper()
    return value if value in {"A", "B"} else "U"
