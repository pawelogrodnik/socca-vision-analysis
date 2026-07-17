from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from app.services.identity_shadow_timeline import build_shadow_resolved_timeline


SCHEMA_VERSION = "0.3.0"
ALGORITHM_NAME = "offline_identity_graph_shadow"
ALGORITHM_VERSION = "0.3.0"

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
    "global_path_selection_enabled": True,
    "global_temporal_window_sec": 30.0,
    "global_min_link_value": 0.05,
    "global_min_candidate_confidence": 0.5,
    "global_max_candidate_cost": 0.75,
    "global_unmatched_cost": 0.75,
    "global_blocking_guard_reasons": [
        "appearance_distance_too_high_for_recommendation",
        "missing_strong_continuity_evidence",
        "required_speed_too_high_for_recommendation",
        "team_mismatch_not_safe_for_recommendation",
    ],
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
    accepted_groups, rejected_groups, path_audit = _select_recommendations(
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
        "global_path_selection": path_audit,
        "summary": _timeline_summary(tracklet_by_id, subjects, accepted_edges, accepted_groups, rejected_groups),
        "accepted_recommendation_groups": accepted_groups,
        "accepted_edges": accepted_edges,
        "rejected_recommendation_groups": rejected_groups,
        "subjects": subjects,
    }
    resolved_timeline = build_shadow_resolved_timeline(
        timeline,
        tracklets,
        quality_doc,
        fps=fps,
        generated_at=generated,
    )
    report = _comparison_report(
        timeline,
        resolved_timeline=resolved_timeline,
        global_identity=global_identity,
        fragmentation_doc=fragmentation_doc or {},
        current_subjects=current_subjects,
        generated_at=generated,
    )
    return {
        "identity_offline_shadow": timeline,
        "identity_offline_shadow_timeline": resolved_timeline,
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
        pair = _normalize_pairs([edge])
        if not pair:
            continue
        edge_cost = edge.get("cost", edge.get("path_cost"))
        edge_confidence = edge.get("base_confidence") or edge.get("confidence") or edge.get("score")
        if edge_confidence is None and edge_cost is not None:
            edge_confidence = max(0.0, min(1.0, 1.0 - float(edge_cost)))
        groups.append(
            {
                "recommendation_key": f"stitch:{edge.get('candidate_key')}",
                "source": "stitching",
                "source_key": edge.get("candidate_key"),
                "decision": "single_edge",
                "priority": int(params["stitching_recommendation_priority"]),
                "confidence": round(float(edge_confidence or 0.0), 4),
                "cost": edge_cost,
                "recommended": bool(edge.get("recommended")),
                "recommendation_guard_reasons": sorted(
                    set(str(item) for item in edge.get("recommendation_guard_reasons") or [])
                ),
                "evidence": sorted(set(str(item) for item in edge.get("evidence") or [])),
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
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    parent = {tracklet_id: tracklet_id for tracklet_id in tracklet_by_id}
    outgoing: dict[str, str] = {}
    incoming: dict[str, str] = {}
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    accepted_pair_keys: set[tuple[str, str]] = set()

    forced = [group for group in groups if group.get("source") == "joint_assignment"]
    ordinary = [group for group in groups if group.get("source") != "joint_assignment"]
    valid_forced: list[dict[str, Any]] = []

    for group in _sort_recommendations(forced):
        standalone_reasons = _group_rejection_reasons(
            group,
            tracklet_by_id=tracklet_by_id,
            parent=dict(parent),
            outgoing={},
            incoming={},
            fps=fps,
            parameters=parameters,
        )
        if standalone_reasons:
            rejected.append({**group, "rejection_reasons": standalone_reasons})
        else:
            valid_forced.append(group)

    forced_components = _forced_conflict_components(valid_forced)
    conflict_indexes = {index for component in forced_components for index in component}
    conflict_winners = {
        min(
            component,
            key=lambda index: (
                -int(valid_forced[index].get("priority") or 0),
                -float(valid_forced[index].get("confidence") or 0.0),
                len(valid_forced[index].get("pairs") or []),
                str(valid_forced[index].get("recommendation_key") or ""),
            ),
        )
        for component in forced_components
    }

    for index, group in enumerate(valid_forced):
        if index in conflict_indexes and index not in conflict_winners:
            conflict_reasons = _group_rejection_reasons(
                group,
                tracklet_by_id=tracklet_by_id,
                parent=parent,
                outgoing=outgoing,
                incoming=incoming,
                fps=fps,
                parameters=parameters,
            )
            rejected.append(
                {
                    **group,
                    "rejection_reasons": sorted(
                        set(conflict_reasons + ["forced_constraint_conflict"])
                    ),
                }
            )
            continue
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

    if not parameters.get("global_path_selection_enabled", True):
        for group in _sort_recommendations(ordinary):
            if group.get("source") == "stitching" and not group.get("recommended"):
                rejected.append({**group, "rejection_reasons": ["global_path_disabled_not_recommended"]})
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
            admissible, admissibility_reasons = _candidate_admissibility(group, parameters)
            if len(group.get("pairs") or []) != 1 or not admissible:
                rejected.append(
                    {
                        **group,
                        "rejection_reasons": admissibility_reasons or ["global_path_disabled_non_singleton"],
                    }
                )
                continue
            pair = group["pairs"][0]
            source_id, target_id = str(pair["source_tracklet_id"]), str(pair["target_tracklet_id"])
            outgoing[source_id] = target_id
            incoming[target_id] = source_id
            _union(parent, source_id, target_id)
            accepted.append(group)
        return accepted, rejected, {
            "enabled": False,
            "forced_conflict_count": len(forced_components),
            "forced_conflicting_group_count": len(conflict_indexes),
        }

    selected, optimizer_audit = _global_scored_edges(
        ordinary,
        tracklet_by_id=tracklet_by_id,
        parent=parent,
        outgoing=outgoing,
        incoming=incoming,
        fps=fps,
        parameters=parameters,
    )
    selected_keys = {str(group["recommendation_key"]) for group in selected}
    accepted_selected_keys: set[str] = set()

    selected_in_time_order = sorted(
        selected,
        key=lambda group: (
            _tracklet_sort_key(
                tracklet_by_id[str(group["pairs"][0]["source_tracklet_id"])],
                fps=fps,
            ),
            str(group["recommendation_key"]),
        ),
    )
    for group in selected_in_time_order:
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
            rejected.append(
                {
                    **group,
                    "rejection_reasons": sorted(set(reasons + ["global_path_selected_but_constraint_failed"])),
                }
            )
            continue
        pair = group["pairs"][0]
        source_id = str(pair["source_tracklet_id"])
        target_id = str(pair["target_tracklet_id"])
        outgoing[source_id] = target_id
        incoming[target_id] = source_id
        _union(parent, source_id, target_id)
        accepted_pair_keys.add((source_id, target_id))
        accepted_selected_keys.add(str(group["recommendation_key"]))
        accepted.append(group)

    for group in _sort_recommendations(ordinary):
        recommendation_key = str(group["recommendation_key"])
        if recommendation_key in accepted_selected_keys:
            continue
        if recommendation_key in selected_keys:
            # Already recorded above with the post-optimizer constraint failure.
            continue
        structural_reasons = _group_rejection_reasons(
            group,
            tracklet_by_id=tracklet_by_id,
            parent=parent,
            outgoing=outgoing,
            incoming=incoming,
            fps=fps,
            parameters=parameters,
        )
        _, admissibility_reasons = _candidate_admissibility(group, parameters)
        rejected.append(
            {
                **group,
                "rejection_reasons": (
                    structural_reasons
                    or admissibility_reasons
                    or ["global_path_not_selected"]
                ),
            }
        )

    return accepted, rejected, {
        "enabled": True,
        **optimizer_audit,
        "forced_conflict_count": len(forced_components),
        "forced_conflicting_group_count": len(conflict_indexes),
        "forced_conflict_winner_keys": sorted(
            str(valid_forced[index]["recommendation_key"]) for index in conflict_winners
        ),
        "admissibility": {
            "min_confidence": parameters.get("global_min_candidate_confidence"),
            "max_cost": parameters.get("global_max_candidate_cost"),
            "unmatched_cost": parameters.get("global_unmatched_cost"),
            "min_link_value": parameters.get("global_min_link_value"),
            "blocking_guard_reasons": parameters.get("global_blocking_guard_reasons"),
        },
    }


def _forced_conflict_components(groups: list[dict[str, Any]]) -> list[list[int]]:
    parent = list(range(len(groups)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    endpoint_owner: dict[tuple[str, str], int] = {}
    for index, group in enumerate(groups):
        endpoints = {
            ("out", str(pair["source_tracklet_id"]))
            for pair in group.get("pairs") or []
        } | {
            ("in", str(pair["target_tracklet_id"]))
            for pair in group.get("pairs") or []
        }
        for endpoint in endpoints:
            if endpoint in endpoint_owner:
                union(index, endpoint_owner[endpoint])
            else:
                endpoint_owner[endpoint] = index

    components: dict[int, list[int]] = defaultdict(list)
    for index in range(len(groups)):
        components[find(index)].append(index)
    return [members for _, members in sorted(components.items()) if len(members) > 1]


def _global_scored_edges(
    groups: list[dict[str, Any]],
    *,
    tracklet_by_id: dict[str, dict[str, Any]],
    parent: dict[str, str],
    outgoing: dict[str, str],
    incoming: dict[str, str],
    fps: float,
    parameters: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    partitions: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    best_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    singleton_candidate_count = 0
    admissible_candidate_count = 0
    for group in groups:
        if len(group.get("pairs") or []) != 1:
            continue
        singleton_candidate_count += 1
        pair = group["pairs"][0]
        key = (str(pair["source_tracklet_id"]), str(pair["target_tracklet_id"]))
        admissible, _ = _candidate_admissibility(group, parameters)
        if not admissible:
            continue
        if key[0] not in tracklet_by_id or key[1] not in tracklet_by_id:
            continue
        admissible_candidate_count += 1
        previous = best_by_pair.get(key)
        if previous is None or _candidate_rank(group) < _candidate_rank(previous):
            best_by_pair[key] = group

    for group in best_by_pair.values():
        partition = _path_partition(
            group,
            tracklet_by_id=tracklet_by_id,
            fps=fps,
            parameters=parameters,
        )
        partitions[partition].append(group)

    result: list[dict[str, Any]] = []
    components = _partition_components(partitions)
    for component in components:
        candidates = sorted(component, key=lambda item: str(item["recommendation_key"]))
        valid = [
            group
            for group in candidates
            if not _group_rejection_reasons(
                group,
                tracklet_by_id=tracklet_by_id,
                parent=dict(parent),
                outgoing=dict(outgoing),
                incoming=dict(incoming),
                fps=fps,
                parameters=parameters,
            )
        ]
        result.extend(
            _polynomial_matching(
                valid,
                unmatched_cost=float(parameters.get("global_unmatched_cost", 0.75)),
            )
        )
    return result, {
        "candidate_pool_size": singleton_candidate_count,
        "admissible_candidate_count": admissible_candidate_count,
        "deduplicated_candidate_count": len(best_by_pair),
        "partition_count": len(partitions),
        "connected_partition_component_count": len(components),
        "selected_scored_edges": len(result),
    }


def _candidate_admissibility(group: dict[str, Any], parameters: dict[str, Any]) -> tuple[bool, list[str]]:
    confidence = float(group.get("confidence") or 0.0)
    cost = group.get("cost")
    reasons: list[str] = []
    if confidence < float(parameters.get("global_min_candidate_confidence", 0.5)):
        reasons.append("candidate_confidence_below_threshold")
    if cost is not None and float(cost) > float(parameters.get("global_max_candidate_cost", 0.75)):
        reasons.append("candidate_cost_above_threshold")
    if cost is None and confidence <= 0.0:
        reasons.append("candidate_has_no_scored_value")
    link_value = float(parameters.get("global_unmatched_cost", 0.75)) - _link_cost(group)
    if link_value < float(parameters.get("global_min_link_value", 0.0)):
        reasons.append("candidate_link_value_below_abstention_threshold")
    blocking_guard_reasons = set(str(item) for item in parameters.get("global_blocking_guard_reasons") or [])
    for guard_reason in sorted(
        blocking_guard_reasons
        & set(str(item) for item in group.get("recommendation_guard_reasons") or [])
    ):
        reasons.append(f"candidate_guard_blocks_global_link:{guard_reason}")
    return not reasons, reasons


def _candidate_rank(group: dict[str, Any]) -> tuple[float, int, str]:
    return (
        _link_cost(group),
        -int(group.get("priority") or 0),
        str(group.get("recommendation_key") or ""),
    )


def _partition_components(partitions: dict[tuple[str, int], list[dict[str, Any]]]) -> list[list[dict[str, Any]]]:
    keys = sorted(partitions)
    parent = {key: key for key in keys}

    def find(item: tuple[str, int]) -> tuple[str, int]:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left: tuple[str, int], right: tuple[str, int]) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)
    endpoint_buckets: dict[tuple[str, str], tuple[str, int]] = {}
    for key in keys:
        for group in partitions[key]:
            pair = group["pairs"][0]
            for endpoint in (("out", str(pair["source_tracklet_id"])), ("in", str(pair["target_tracklet_id"]))):
                if endpoint in endpoint_buckets:
                    union(key, endpoint_buckets[endpoint])
                endpoint_buckets[endpoint] = key
    components: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for key in keys:
        components[find(key)].extend(partitions[key])
    return [components[key] for key in sorted(components)]


def _polynomial_matching(groups: list[dict[str, Any]], *, unmatched_cost: float) -> list[dict[str, Any]]:
    """Maximum-benefit bipartite matching with explicit zero-benefit abstention."""
    sources = sorted({str(g["pairs"][0]["source_tracklet_id"]) for g in groups})
    targets = sorted({str(g["pairs"][0]["target_tracklet_id"]) for g in groups})
    source_index = {value: index for index, value in enumerate(sources)}
    target_index = {value: index for index, value in enumerate(targets)}
    edges = {(source_index[str(g["pairs"][0]["source_tracklet_id"])], target_index[str(g["pairs"][0]["target_tracklet_id"])]): g for g in groups}
    node_count = 2 + len(sources) + len(targets)
    source_node, sink = 0, node_count - 1
    graph: list[list[list[Any]]] = [[] for _ in range(node_count)]

    def add(
        left: int,
        right: int,
        capacity: int,
        cost: int,
        marker: str | None = None,
    ) -> None:
        graph[left].append([right, capacity, cost, len(graph[right]), marker])
        graph[right].append([left, 0, -cost, len(graph[left]) - 1, None])

    for index in range(len(sources)):
        add(source_node, 1 + index, 1, 0)
    for index in range(len(targets)):
        add(1 + len(sources) + index, sink, 1, 0)
    for (left, right), group in sorted(edges.items()):
        # Lower scored cost is better; equal costs use stable recommendation key.
        cost = int(round((_link_cost(group) - unmatched_cost) * 1_000_000))
        add(1 + left, 1 + len(sources) + right, 1, cost, str(group["recommendation_key"]))
    while True:
        distance = [10**18] * node_count
        previous: list[tuple[int, int] | None] = [None] * node_count
        distance[source_node] = 0
        for _ in range(node_count - 1):
            changed = False
            for left in range(node_count):
                if distance[left] == 10**18:
                    continue
                for edge_index, edge in enumerate(graph[left]):
                    right, capacity, cost = edge[:3]
                    if capacity and distance[right] > distance[left] + cost:
                        distance[right] = distance[left] + cost
                        previous[right] = (left, edge_index)
                        changed = True
            if not changed:
                break
        if distance[sink] >= 0 or previous[sink] is None:
            break
        current = sink
        while current != source_node:
            step = previous[current]
            if step is None:
                break
            left, edge_index = step
            edge = graph[left][edge_index]
            edge[1] = 0
            graph[current][edge[3]][1] = 1
            current = left
    selected: list[dict[str, Any]] = []
    for (left, right), group in edges.items():
        node = 1 + left
        if any(
            edge[0] == 1 + len(sources) + right
            and edge[4] == str(group["recommendation_key"])
            and edge[1] == 0
            for edge in graph[node]
        ):
            selected.append(group)
    return sorted(selected, key=lambda g: str(g["recommendation_key"]))


def _link_cost(group: dict[str, Any]) -> float:
    base = float(group["cost"]) if group.get("cost") is not None else 1.0 - float(group.get("confidence") or 0.0)
    return base - int(group.get("priority") or 0) * 1e-6


def _path_partition(
    group: dict[str, Any],
    *,
    tracklet_by_id: dict[str, dict[str, Any]],
    fps: float,
    parameters: dict[str, Any],
) -> tuple[str, int]:
    source = tracklet_by_id[str(group["pairs"][0]["source_tracklet_id"])]
    target = tracklet_by_id[str(group["pairs"][0]["target_tracklet_id"])]
    window = max(float(parameters.get("global_temporal_window_sec") or 30.0), 0.001)
    transition_time = (_end_frame(source, fps=fps) + _start_frame(target, fps=fps)) / (2.0 * max(fps, 1e-6))
    return _team_label(source), int(transition_time // window)


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
    parallel_conflicts = sum(
        not _subject_has_no_parallel_detected_segments(row)
        for row in subjects
    )
    estimated_manual_review_subjects = sum(
        any(
            segment.get("status") == "detected"
            and segment.get("quality_class") in {"recoverable", "ambiguous"}
            for segment in row.get("timeline_segments") or []
        )
        for row in subjects
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
        "accepted_cross_production_subject_edges": relation_counts["different_subjects"],
        "cross_production_subject_edges_requiring_review": relation_counts["different_subjects"],
        "parallel_detected_conflicts": parallel_conflicts,
        "estimated_manual_review_subjects": estimated_manual_review_subjects,
        "manual_review_estimate_method": "one_item_per_shadow_subject_with_recoverable_or_ambiguous_tracklet",
    }


def _comparison_report(
    timeline: dict[str, Any],
    *,
    resolved_timeline: dict[str, Any],
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
    raw_time = baseline.get("raw_timeline_seconds", baseline.get("unresolved_timeline_seconds"))
    ambiguous_time = baseline.get("ambiguous_timeline_seconds")
    switches = baseline.get("suspected_switches", baseline.get("switch_count"))
    shadow_summary = timeline.get("summary") or {}
    cross_subject_links = int(shadow_summary.get("accepted_cross_production_subject_edges") or 0)
    duplicate_conflicts_after = int(shadow_summary.get("parallel_detected_conflicts") or 0)
    baseline_coverage = baseline.get("coverage", baseline.get("coverage_ratio"))
    resolved_summary = resolved_timeline.get("summary") or {}
    shadow_coverage = resolved_summary.get("observed_coverage_ratio")
    manual_review_after = shadow_summary.get("estimated_manual_review_subjects")
    manual_review_delta = (
        int(manual_review_after) - int(baseline_review)
        if manual_review_after is not None and baseline_review is not None
        else None
    )
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
            "cross_production_subject_link_count": cross_subject_links,
            "baseline_unresolved_timeline_seconds": baseline.get("unresolved_timeline_seconds"),
            "baseline_ambiguous_timeline_seconds": baseline.get("ambiguous_timeline_seconds"),
            "raw_time_seconds": raw_time,
            "ambiguous_time_seconds": ambiguous_time,
            "coverage": baseline_coverage,
            "coverage_before": baseline_coverage,
            "coverage_after": shadow_coverage,
            "coverage_delta": None,
            "switches_before": switches,
            "switches_after": None,
            "switches_delta": None,
            "switches_after_status": "requires_manual_validation_of_cross_production_subject_edges",
            "duplicate_conflicts": duplicate_conflicts_after,
            "duplicate_conflicts_before": baseline.get("duplicate_conflicts"),
            "duplicate_conflicts_after": duplicate_conflicts_after,
            "raw_time_before_seconds": raw_time,
            "raw_time_after_seconds": None,
            "ambiguous_time_before_seconds": ambiguous_time,
            "ambiguous_time_after_seconds": None,
            "shadow_status_frame_counts": resolved_summary.get("status_frame_counts") or {},
            "shadow_status_seconds": resolved_summary.get("status_seconds") or {},
            "shadow_transition_events": resolved_summary.get("transition_events"),
            "shadow_cross_production_transition_events": resolved_summary.get(
                "cross_production_transition_events"
            ),
            "shadow_trusted_detected_frames": resolved_summary.get("trusted_detected_frames"),
            "shadow_trusted_detected_ratio": resolved_summary.get("trusted_detected_ratio"),
            "manual_review_effort_before": baseline_review,
            "manual_review_effort_after": manual_review_after,
            "manual_review_effort_delta": manual_review_delta,
            "estimated_manual_review_items_before": baseline_review,
            "estimated_manual_review_items_after": manual_review_after,
            "coverage_delta_status": (
                "shadow_tracklet_timeline_not_roster_comparable"
                if shadow_coverage is not None
                else "missing_shadow_frame_labels"
            ),
            "manual_review_delta_status": "shadow_subject_estimate_with_event_level_timeline",
            "manual_review_estimate_method": shadow_summary.get("manual_review_estimate_method"),
        },
        "gates": {
            "production_identity_untouched": True,
            "no_parallel_tracklet_conflicts": _subjects_have_no_parallel_detected_segments(
                timeline.get("subjects") or []
            ),
            "no_duplicate_predecessor_or_successor": _has_unique_endpoints(timeline.get("accepted_edges") or []),
            "forced_joint_constraints_feasible": int((timeline.get("global_path_selection") or {}).get("forced_conflict_count") or 0) == 0,
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
    return all(_subject_has_no_parallel_detected_segments(subject) for subject in subjects)


def _subject_has_no_parallel_detected_segments(subject: dict[str, Any]) -> bool:
    detected = sorted(
        (
            int(row.get("start_frame") or 0),
            int(row.get("end_frame") or 0),
        )
        for row in subject.get("timeline_segments") or []
        if row.get("status") == "detected"
    )
    return not any(
        next_start <= current_end
        for (_, current_end), (next_start, _) in zip(detected, detected[1:])
    )
