from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from typing import Any


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "offline_identity_graph_shadow"
ALGORITHM_VERSION = "0.1.0"

DEFAULT_PARAMETERS: dict[str, Any] = {
    "eligible_quality_classes": ["trusted", "recoverable", "ambiguous"],
    "max_link_gap_sec": 3.5,
    "joint_recommendation_priority": 2,
    "stitching_recommendation_priority": 1,
    "baseline_continuity_enabled": True,
    "baseline_continuity_priority": 0,
    "baseline_quality_classes": ["trusted", "recoverable"],
    "baseline_max_gap_sec": 1.5,
    "baseline_min_footpoint_reliable_ratio": 0.8,
    "baseline_min_appearance_reliable_ratio": 0.75,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_shadow_offline_identity(
    tracklets: list[dict[str, Any]],
    quality_doc: dict[str, Any],
    stitching_doc: dict[str, Any],
    joint_assignment_doc: dict[str, Any],
    global_identity: dict[str, Any],
    *,
    fps: float,
    fragmentation_doc: dict[str, Any] | None = None,
    generated_at: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build a parallel identity graph without mutating production identity artifacts."""
    params = {**DEFAULT_PARAMETERS, **(parameters or {})}
    quality_by_id = {
        str(row.get("tracklet_id")): row
        for row in quality_doc.get("tracklets") or []
        if row.get("status") == "clean"
    }
    eligible_classes = set(str(item) for item in params["eligible_quality_classes"])
    all_tracklets_by_id = {
        str(row.get("tracklet_id")): row
        for row in tracklets
        if row.get("tracklet_id")
    }
    tracklet_by_id = {
        str(row.get("tracklet_id")): row
        for row in tracklets
        if str(row.get("tracklet_id")) in quality_by_id
        and quality_by_id[str(row.get("tracklet_id"))].get("quality_class") in eligible_classes
    }
    current_subjects = _current_subject_membership(global_identity)
    recommendation_groups = _recommendation_groups(stitching_doc, joint_assignment_doc, params=params)
    baseline_groups, baseline_audit = _production_continuity_groups(
        all_tracklets_by_id,
        quality_by_id=quality_by_id,
        current_subjects=current_subjects,
        fragmentation_doc=fragmentation_doc or {},
        fps=fps,
        parameters=params,
    )
    recommendation_groups = _sort_recommendations(recommendation_groups + baseline_groups)
    accepted_groups, rejected_groups = _select_recommendations(
        recommendation_groups,
        tracklet_by_id=tracklet_by_id,
        fps=fps,
        parameters=params,
    )
    accepted_edges = [
        _accepted_edge(pair, group=group, tracklet_by_id=tracklet_by_id, current_subjects=current_subjects, fps=fps)
        for group in accepted_groups
        for pair in group["pairs"]
    ]
    subjects = _build_subjects(
        tracklet_by_id,
        quality_by_id=quality_by_id,
        accepted_edges=accepted_edges,
        current_subjects=current_subjects,
        fps=fps,
    )
    generated = generated_at or now_iso()
    timeline = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_read_only",
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION},
        "parameters": params,
        "source": {
            "tracklets": "tracklets_before_conservative_identity_v2",
            "quality": "identity_tracklet_quality.json",
            "stitching": "identity_stitching_candidates.json",
            "joint_assignments": "identity_occlusion_assignments.json",
            "baseline_continuity": "current_global_identity_safe_edges",
            "production_identity_usage": "comparison_only_not_scoring",
        },
        "baseline_continuity_audit": baseline_audit,
        "summary": _timeline_summary(tracklet_by_id, subjects, accepted_edges, accepted_groups, rejected_groups),
        "accepted_recommendation_groups": accepted_groups,
        "accepted_edges": accepted_edges,
        "rejected_recommendation_groups": rejected_groups,
        "subjects": subjects,
    }
    report = _comparison_report(
        timeline,
        global_identity=global_identity,
        fragmentation_doc=fragmentation_doc or {},
        current_subjects=current_subjects,
        generated_at=generated,
    )
    return {
        "identity_offline_shadow": timeline,
        "identity_offline_shadow_report": report,
    }


def _recommendation_groups(
    stitching_doc: dict[str, Any],
    joint_doc: dict[str, Any],
    *,
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for case in joint_doc.get("cases") or []:
        decision = case.get("decision") or {}
        recommendation = decision.get("recommended_assignment_id")
        if recommendation == "partial":
            pairs = decision.get("recommended_pairs") or []
        elif recommendation in {"assignment_a", "assignment_b"}:
            assignment = next(
                (
                    row
                    for row in case.get("assignments") or []
                    if row.get("assignment_id") == recommendation
                ),
                None,
            )
            pairs = (assignment or {}).get("pairs") or []
        else:
            continue
        normalized_pairs = _normalize_pairs(pairs)
        if not normalized_pairs:
            continue
        groups.append(
            {
                "recommendation_key": f"joint:{case.get('case_key')}:{recommendation}",
                "source": "joint_assignment",
                "source_key": case.get("case_key"),
                "decision": recommendation,
                "priority": int(params["joint_recommendation_priority"]),
                "confidence": round(float(decision.get("confidence") or 0.0), 4),
                "cost": None,
                "pairs": normalized_pairs,
                "occlusion_event_ids": sorted(set(str(item) for item in case.get("occlusion_event_ids") or [])),
            }
        )
    for edge in stitching_doc.get("candidate_edges") or []:
        if not edge.get("recommended"):
            continue
        pair = _normalize_pairs([edge])
        if not pair:
            continue
        groups.append(
            {
                "recommendation_key": f"stitch:{edge.get('candidate_key')}",
                "source": "stitching",
                "source_key": edge.get("candidate_key"),
                "decision": "single_edge",
                "priority": int(params["stitching_recommendation_priority"]),
                "confidence": round(float(edge.get("base_confidence") or 0.0), 4),
                "cost": edge.get("cost"),
                "pairs": pair,
                "occlusion_event_ids": sorted(set(str(item) for item in edge.get("occlusion_event_ids") or [])),
            }
        )
    return _sort_recommendations(groups)


def _sort_recommendations(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        groups,
        key=lambda row: (
            -int(row["priority"]),
            -float(row["confidence"]),
            float(row["cost"]) if row.get("cost") is not None else -1.0,
            str(row["recommendation_key"]),
        ),
    )


def _production_continuity_groups(
    tracklet_by_id: dict[str, dict[str, Any]],
    *,
    quality_by_id: dict[str, dict[str, Any]],
    current_subjects: dict[str, list[str]],
    fragmentation_doc: dict[str, Any],
    fps: float,
    parameters: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not parameters.get("baseline_continuity_enabled"):
        return [], {"enabled": False, "considered_pairs": 0, "eligible_pairs": 0, "skipped_reason_counts": {}}
    tracklets_by_subject: dict[str, list[str]] = defaultdict(list)
    for tracklet_id, subject_ids in current_subjects.items():
        for subject_id in subject_ids:
            if tracklet_id in tracklet_by_id:
                tracklets_by_subject[subject_id].append(tracklet_id)
    suspected_pairs = {
        (str(row.get("from_tracklet_id") or ""), str(row.get("to_tracklet_id") or ""))
        for row in fragmentation_doc.get("suspected_switches") or []
    }
    allowed_quality = set(str(item) for item in parameters["baseline_quality_classes"])
    skipped = Counter()
    considered = 0
    groups: list[dict[str, Any]] = []
    for subject_id, subject_tracklets in sorted(tracklets_by_subject.items()):
        ordered = sorted(
            set(subject_tracklets),
            key=lambda item: _tracklet_sort_key(tracklet_by_id[item], fps=fps),
        )
        for source_id, target_id in zip(ordered, ordered[1:]):
            considered += 1
            source = tracklet_by_id[source_id]
            target = tracklet_by_id[target_id]
            source_quality = quality_by_id.get(source_id) or {}
            target_quality = quality_by_id.get(target_id) or {}
            reasons: set[str] = set()
            if source_quality.get("quality_class") not in allowed_quality or target_quality.get("quality_class") not in allowed_quality:
                reasons.add("baseline_quality_not_trusted")
            if min(
                float(source_quality.get("footpoint_reliable_ratio") or 0.0),
                float(target_quality.get("footpoint_reliable_ratio") or 0.0),
            ) < float(parameters["baseline_min_footpoint_reliable_ratio"]):
                reasons.add("baseline_footpoint_unreliable")
            if min(
                float(source_quality.get("appearance_reliable_ratio") or 0.0),
                float(target_quality.get("appearance_reliable_ratio") or 0.0),
            ) < float(parameters["baseline_min_appearance_reliable_ratio"]):
                reasons.add("baseline_appearance_unreliable")
            if (source_id, target_id) in suspected_pairs:
                reasons.add("baseline_suspected_switch")
            source_end = _end_frame(source, fps=fps)
            target_start = _start_frame(target, fps=fps)
            if target_start <= source_end:
                reasons.add("baseline_temporal_overlap")
            gap_sec = (target_start - source_end - 1) / max(fps, 1e-6)
            if gap_sec > float(parameters["baseline_max_gap_sec"]):
                reasons.add("baseline_gap_too_large")
            source_team = _team_label(source)
            target_team = _team_label(target)
            if source_team in {"A", "B"} and target_team in {"A", "B"} and source_team != target_team:
                reasons.add("baseline_team_mismatch")
            if reasons:
                skipped.update(reasons)
                continue
            groups.append(
                {
                    "recommendation_key": f"baseline:{subject_id}:{source_id}:{target_id}",
                    "source": "production_continuity",
                    "source_key": subject_id,
                    "decision": "safe_baseline_edge",
                    "priority": int(parameters["baseline_continuity_priority"]),
                    "confidence": round(
                        min(
                            float(source_quality.get("quality_confidence") or 0.0),
                            float(target_quality.get("quality_confidence") or 0.0),
                        ),
                        4,
                    ),
                    "cost": None,
                    "pairs": [{"source_tracklet_id": source_id, "target_tracklet_id": target_id}],
                    "occlusion_event_ids": [],
                }
            )
    return groups, {
        "enabled": True,
        "considered_pairs": considered,
        "eligible_pairs": len(groups),
        "skipped_pairs": considered - len(groups),
        "skipped_reason_counts": dict(sorted(skipped.items())),
    }


def _select_recommendations(
    groups: list[dict[str, Any]],
    *,
    tracklet_by_id: dict[str, dict[str, Any]],
    fps: float,
    parameters: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    parent = {tracklet_id: tracklet_id for tracklet_id in tracklet_by_id}
    outgoing: dict[str, str] = {}
    incoming: dict[str, str] = {}
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    accepted_pair_keys: set[tuple[str, str]] = set()
    for group in groups:
        pair_keys = {
            (str(pair["source_tracklet_id"]), str(pair["target_tracklet_id"]))
            for pair in group["pairs"]
        }
        if pair_keys and pair_keys <= accepted_pair_keys:
            continue
        reasons = _group_rejection_reasons(
            group,
            tracklet_by_id=tracklet_by_id,
            parent=parent,
            outgoing=outgoing,
            incoming=incoming,
            fps=fps,
            parameters=parameters,
        )
        if reasons:
            rejected.append({**group, "rejection_reasons": reasons})
            continue
        for source_id, target_id in sorted(pair_keys):
            outgoing[source_id] = target_id
            incoming[target_id] = source_id
            _union(parent, source_id, target_id)
            accepted_pair_keys.add((source_id, target_id))
        accepted.append(group)
    return accepted, rejected


def _group_rejection_reasons(
    group: dict[str, Any],
    *,
    tracklet_by_id: dict[str, dict[str, Any]],
    parent: dict[str, str],
    outgoing: dict[str, str],
    incoming: dict[str, str],
    fps: float,
    parameters: dict[str, Any],
) -> list[str]:
    reasons: set[str] = set()
    temporary_parent = dict(parent)
    temporary_outgoing = dict(outgoing)
    temporary_incoming = dict(incoming)
    pairs = [
        (str(pair["source_tracklet_id"]), str(pair["target_tracklet_id"]))
        for pair in group.get("pairs") or []
    ]
    if len(set(source for source, _ in pairs)) != len(pairs):
        reasons.add("duplicate_source_in_atomic_group")
    if len(set(target for _, target in pairs)) != len(pairs):
        reasons.add("duplicate_target_in_atomic_group")
    for source_id, target_id in pairs:
        source = tracklet_by_id.get(source_id)
        target = tracklet_by_id.get(target_id)
        if source is None or target is None:
            reasons.add("ineligible_or_missing_tracklet")
            continue
        if source_id == target_id:
            reasons.add("self_link")
        if source_id in temporary_outgoing and temporary_outgoing[source_id] != target_id:
            reasons.add("source_successor_already_assigned")
        if target_id in temporary_incoming and temporary_incoming[target_id] != source_id:
            reasons.add("target_predecessor_already_assigned")
        source_end = _end_frame(source, fps=fps)
        target_start = _start_frame(target, fps=fps)
        if target_start <= source_end:
            reasons.add("temporal_overlap")
        gap_sec = (target_start - source_end) / max(fps, 1e-6)
        if gap_sec > float(parameters["max_link_gap_sec"]):
            reasons.add("gap_too_large")
        source_team = _team_label(source)
        target_team = _team_label(target)
        if source_team in {"A", "B"} and target_team in {"A", "B"} and source_team != target_team:
            reasons.add("team_mismatch")
        if source_id in temporary_parent and target_id in temporary_parent:
            source_members = _component_members(temporary_parent, source_id)
            target_members = _component_members(temporary_parent, target_id)
            if source_members == target_members:
                reasons.add("cycle_or_duplicate_link")
            elif _components_overlap(source_members, target_members, tracklet_by_id=tracklet_by_id, fps=fps):
                reasons.add("component_temporal_conflict")
        if reasons:
            continue
        temporary_outgoing[source_id] = target_id
        temporary_incoming[target_id] = source_id
        _union(temporary_parent, source_id, target_id)
    return sorted(reasons)


def _accepted_edge(
    pair: dict[str, str],
    *,
    group: dict[str, Any],
    tracklet_by_id: dict[str, dict[str, Any]],
    current_subjects: dict[str, list[str]],
    fps: float,
) -> dict[str, Any]:
    source_id = str(pair["source_tracklet_id"])
    target_id = str(pair["target_tracklet_id"])
    source_end = _end_frame(tracklet_by_id[source_id], fps=fps)
    target_start = _start_frame(tracklet_by_id[target_id], fps=fps)
    source_subjects = current_subjects.get(source_id) or []
    target_subjects = current_subjects.get(target_id) or []
    return {
        "edge_key": _edge_key(source_id, target_id),
        "source_tracklet_id": source_id,
        "target_tracklet_id": target_id,
        "recommendation_key": group["recommendation_key"],
        "recommendation_source": group["source"],
        "confidence": group["confidence"],
        "gap_frames": max(0, target_start - source_end - 1),
        "gap_sec": round(max(0, target_start - source_end - 1) / max(fps, 1e-6), 4),
        "occlusion_event_ids": group.get("occlusion_event_ids") or [],
        "current_source_subject_ids": source_subjects,
        "current_target_subject_ids": target_subjects,
        "current_identity_relation": _identity_relation(source_subjects, target_subjects),
    }


def _build_subjects(
    tracklet_by_id: dict[str, dict[str, Any]],
    *,
    quality_by_id: dict[str, dict[str, Any]],
    accepted_edges: list[dict[str, Any]],
    current_subjects: dict[str, list[str]],
    fps: float,
) -> list[dict[str, Any]]:
    parent = {tracklet_id: tracklet_id for tracklet_id in tracklet_by_id}
    edge_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    predecessor: dict[str, str] = {}
    successor: dict[str, str] = {}
    for edge in accepted_edges:
        source_id = str(edge["source_tracklet_id"])
        target_id = str(edge["target_tracklet_id"])
        _union(parent, source_id, target_id)
        edge_by_pair[(source_id, target_id)] = edge
        predecessor[target_id] = source_id
        successor[source_id] = target_id
    components: dict[str, list[str]] = defaultdict(list)
    for tracklet_id in sorted(tracklet_by_id):
        components[_find(parent, tracklet_id)].append(tracklet_id)
    rows: list[dict[str, Any]] = []
    for members in components.values():
        member_set = set(members)
        starts = sorted(member_set - set(predecessor))
        ordered: list[str] = []
        for start in starts:
            current = start
            while current in member_set and current not in ordered:
                ordered.append(current)
                current = successor.get(current, "")
        ordered.extend(sorted(member_set - set(ordered), key=lambda item: _tracklet_sort_key(tracklet_by_id[item], fps=fps)))
        teams = Counter(_team_label(tracklet_by_id[item]) for item in ordered)
        known_teams = [(count, team) for team, count in teams.items() if team in {"A", "B"}]
        team_label = max(known_teams)[1] if known_teams else "U"
        subject_id = _subject_id(team_label, ordered)
        segments: list[dict[str, Any]] = []
        for index, tracklet_id in enumerate(ordered):
            tracklet = tracklet_by_id[tracklet_id]
            if index:
                previous = ordered[index - 1]
                edge = edge_by_pair.get((previous, tracklet_id))
                if edge and edge["gap_frames"] > 0:
                    segments.append(
                        {
                            "status": "occluded" if edge.get("occlusion_event_ids") else "missing",
                            "start_frame": _end_frame(tracklet_by_id[previous], fps=fps) + 1,
                            "end_frame": _start_frame(tracklet, fps=fps) - 1,
                            "duration_sec": edge["gap_sec"],
                            "edge_key": edge["edge_key"],
                        }
                    )
            positions = _positions(tracklet)
            segments.append(
                {
                    "status": "detected",
                    "tracklet_id": tracklet_id,
                    "quality_class": quality_by_id[tracklet_id].get("quality_class"),
                    "start_frame": _start_frame(tracklet, fps=fps),
                    "end_frame": _end_frame(tracklet, fps=fps),
                    "positions_count": len(positions),
                }
            )
        production_ids = sorted({subject for item in ordered for subject in current_subjects.get(item) or []})
        subject_edges = [edge_by_pair[(left, right)] for left, right in zip(ordered, ordered[1:]) if (left, right) in edge_by_pair]
        rows.append(
            {
                "shadow_subject_id": subject_id,
                "team_label": team_label,
                "tracklet_ids": ordered,
                "tracklet_count": len(ordered),
                "start_frame": min(_start_frame(tracklet_by_id[item], fps=fps) for item in ordered),
                "end_frame": max(_end_frame(tracklet_by_id[item], fps=fps) for item in ordered),
                "detected_positions": sum(len(_positions(tracklet_by_id[item])) for item in ordered),
                "bridged_gap_frames": sum(int(edge["gap_frames"]) for edge in subject_edges),
                "link_confidence": (
                    round(sum(float(edge["confidence"]) for edge in subject_edges) / len(subject_edges), 4)
                    if subject_edges
                    else None
                ),
                "production_subject_ids": production_ids,
                "quality_flags": ["merges_multiple_production_subjects"] if len(production_ids) > 1 else [],
                "timeline_segments": segments,
            }
        )
    return sorted(rows, key=lambda row: (row["team_label"], int(row["start_frame"]), row["shadow_subject_id"]))


def _timeline_summary(
    tracklet_by_id: dict[str, dict[str, Any]],
    subjects: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    accepted_groups: list[dict[str, Any]],
    rejected_groups: list[dict[str, Any]],
) -> dict[str, Any]:
    relation_counts = Counter(str(edge["current_identity_relation"]) for edge in edges)
    rejection_counts = Counter(
        str(reason)
        for group in rejected_groups
        for reason in group.get("rejection_reasons") or []
    )
    return {
        "eligible_tracklets": len(tracklet_by_id),
        "shadow_subjects": len(subjects),
        "singleton_subjects": sum(row["tracklet_count"] == 1 for row in subjects),
        "linked_subjects": sum(row["tracklet_count"] > 1 for row in subjects),
        "accepted_recommendation_groups": len(accepted_groups),
        "accepted_edges": len(edges),
        "accepted_joint_groups": sum(row["source"] == "joint_assignment" for row in accepted_groups),
        "accepted_stitching_groups": sum(row["source"] == "stitching" for row in accepted_groups),
        "accepted_baseline_continuity_groups": sum(
            row["source"] == "production_continuity" for row in accepted_groups
        ),
        "rejected_recommendation_groups": len(rejected_groups),
        "rejection_reason_counts": dict(sorted(rejection_counts.items())),
        "detected_positions": sum(int(row["detected_positions"]) for row in subjects),
        "bridged_gap_frames": sum(int(row["bridged_gap_frames"]) for row in subjects),
        "current_same_subject_edges": relation_counts["same_subject"],
        "current_different_subject_edges": relation_counts["different_subjects"],
        "current_unresolved_edges": relation_counts["unresolved"],
        "subjects_merging_multiple_production_subjects": sum(
            "merges_multiple_production_subjects" in row["quality_flags"] for row in subjects
        ),
    }


def _comparison_report(
    timeline: dict[str, Any],
    *,
    global_identity: dict[str, Any],
    fragmentation_doc: dict[str, Any],
    current_subjects: dict[str, list[str]],
    generated_at: str,
) -> dict[str, Any]:
    shadow_by_tracklet = {
        tracklet_id: str(subject["shadow_subject_id"])
        for subject in timeline.get("subjects") or []
        for tracklet_id in subject.get("tracklet_ids") or []
    }
    production_to_shadow: dict[str, set[str]] = defaultdict(set)
    for tracklet_id, production_ids in current_subjects.items():
        shadow_id = shadow_by_tracklet.get(tracklet_id)
        if shadow_id:
            for production_id in production_ids:
                production_to_shadow[production_id].add(shadow_id)
    split_subjects = [
        {"production_subject_id": subject_id, "shadow_subject_ids": sorted(shadow_ids)}
        for subject_id, shadow_ids in sorted(production_to_shadow.items())
        if len(shadow_ids) > 1
    ]
    baseline = fragmentation_doc.get("summary") or {}
    production_count = len(global_identity.get("slots") or [])
    shadow_count = int((timeline.get("summary") or {}).get("shadow_subjects") or 0)
    accepted_edges = int((timeline.get("summary") or {}).get("accepted_edges") or 0)
    baseline_review = baseline.get("estimated_manual_review_items")
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "mode": "shadow_read_only",
        "algorithm": {"name": f"{ALGORITHM_NAME}_comparison", "version": ALGORITHM_VERSION},
        "summary": {
            "production_subjects": production_count,
            "shadow_subjects": shadow_count,
            "subject_count_delta": shadow_count - production_count,
            "accepted_edges": accepted_edges,
            "production_subjects_split_in_shadow": len(split_subjects),
            "shadow_subjects_merging_production_subjects": int(
                (timeline.get("summary") or {}).get("subjects_merging_multiple_production_subjects") or 0
            ),
            "baseline_unresolved_timeline_seconds": baseline.get("unresolved_timeline_seconds"),
            "baseline_ambiguous_timeline_seconds": baseline.get("ambiguous_timeline_seconds"),
            "estimated_manual_review_items_before": baseline_review,
            "estimated_manual_review_items_after": (
                max(0, int(baseline_review) - accepted_edges) if baseline_review is not None else None
            ),
            "coverage_delta_status": "not_modeled_until_shadow_timeline_integration",
        },
        "gates": {
            "production_identity_untouched": True,
            "no_parallel_tracklet_conflicts": _subjects_have_no_parallel_detected_segments(
                timeline.get("subjects") or []
            ),
            "no_duplicate_predecessor_or_successor": _has_unique_endpoints(timeline.get("accepted_edges") or []),
        },
        "production_subject_splits": split_subjects,
        "shadow_subject_merges": [
            {
                "shadow_subject_id": row["shadow_subject_id"],
                "production_subject_ids": row["production_subject_ids"],
                "tracklet_ids": row["tracklet_ids"],
            }
            for row in timeline.get("subjects") or []
            if len(row.get("production_subject_ids") or []) > 1
        ],
    }


def _normalize_pairs(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    result = {
        (str(row.get("source_tracklet_id") or ""), str(row.get("target_tracklet_id") or ""))
        for row in rows
    }
    return [
        {"source_tracklet_id": source, "target_tracklet_id": target}
        for source, target in sorted(result)
        if source and target
    ]


def _current_subject_membership(global_identity: dict[str, Any]) -> dict[str, list[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    for slot in global_identity.get("slots") or []:
        subject_id = str(slot.get("stable_subject_id") or slot.get("slot_id") or "")
        if not subject_id:
            continue
        for tracklet_id in slot.get("tracklet_ids") or []:
            result[str(tracklet_id)].add(subject_id)
    return {key: sorted(value) for key, value in result.items()}


def _identity_relation(source_subjects: list[str], target_subjects: list[str]) -> str:
    if not source_subjects or not target_subjects:
        return "unresolved"
    return "same_subject" if set(source_subjects) & set(target_subjects) else "different_subjects"


def _components_overlap(
    left: set[str],
    right: set[str],
    *,
    tracklet_by_id: dict[str, dict[str, Any]],
    fps: float,
) -> bool:
    return any(
        max(_start_frame(tracklet_by_id[a], fps=fps), _start_frame(tracklet_by_id[b], fps=fps))
        <= min(_end_frame(tracklet_by_id[a], fps=fps), _end_frame(tracklet_by_id[b], fps=fps))
        for a in left
        for b in right
    )


def _component_members(parent: dict[str, str], tracklet_id: str) -> set[str]:
    root = _find(parent, tracklet_id)
    return {item for item in parent if _find(parent, item) == root}


def _find(parent: dict[str, str], item: str) -> str:
    while parent[item] != item:
        parent[item] = parent[parent[item]]
        item = parent[item]
    return item


def _union(parent: dict[str, str], left: str, right: str) -> None:
    left_root = _find(parent, left)
    right_root = _find(parent, right)
    if left_root != right_root:
        parent[max(left_root, right_root)] = min(left_root, right_root)


def _positions(tracklet: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        tracklet.get("positions") or tracklet.get("positions_m") or [],
        key=lambda row: (int(row.get("frame") or 0), float(row.get("time_sec") or 0.0)),
    )


def _start_frame(tracklet: dict[str, Any], *, fps: float) -> int:
    positions = _positions(tracklet)
    return int(positions[0].get("frame") or 0) if positions else int(round(float(tracklet.get("start_time_sec") or 0.0) * fps))


def _end_frame(tracklet: dict[str, Any], *, fps: float) -> int:
    positions = _positions(tracklet)
    return int(positions[-1].get("frame") or 0) if positions else int(round(float(tracklet.get("end_time_sec") or 0.0) * fps))


def _tracklet_sort_key(tracklet: dict[str, Any], *, fps: float) -> tuple[int, int, str]:
    return (
        _start_frame(tracklet, fps=fps),
        _end_frame(tracklet, fps=fps),
        str(tracklet.get("tracklet_id") or ""),
    )


def _team_label(tracklet: dict[str, Any]) -> str:
    value = str(tracklet.get("team_label") or tracklet.get("team_candidate") or "U").upper()
    return value if value in {"A", "B"} else "U"


def _edge_key(source_id: str, target_id: str) -> str:
    payload = json.dumps({"source": source_id, "target": target_id, "version": 1}, sort_keys=True, separators=(",", ":"))
    return f"shadow-edge:v1:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _subject_id(team_label: str, tracklet_ids: list[str]) -> str:
    payload = json.dumps({"team": team_label, "tracklets": sorted(tracklet_ids), "version": 1}, sort_keys=True, separators=(",", ":"))
    return f"shadow-{team_label.lower()}-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"


def _has_unique_endpoints(edges: list[dict[str, Any]]) -> bool:
    sources = [str(row.get("source_tracklet_id")) for row in edges]
    targets = [str(row.get("target_tracklet_id")) for row in edges]
    return len(sources) == len(set(sources)) and len(targets) == len(set(targets))


def _subjects_have_no_parallel_detected_segments(subjects: list[dict[str, Any]]) -> bool:
    for subject in subjects:
        detected = sorted(
            (
                int(row.get("start_frame") or 0),
                int(row.get("end_frame") or 0),
            )
            for row in subject.get("timeline_segments") or []
            if row.get("status") == "detected"
        )
        if any(next_start <= current_end for (_, current_end), (next_start, _) in zip(detected, detected[1:])):
            return False
    return True
