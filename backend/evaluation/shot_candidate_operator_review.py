from __future__ import annotations

"""Evaluation-only A/B reporting against durable Shot Review decisions.

This module is intentionally outside ``app.services``. Production candidate
generation never imports it and therefore cannot use operator labels to decide
which shot hypotheses to emit.
"""

from collections import Counter
from typing import Any, Iterable, Mapping

from app.services.shot_review_editor import build_review_clusters


def evaluate_operator_review_ab(
    current_candidates_doc: Mapping[str, Any],
    v3_candidates_doc: Mapping[str, Any],
    editorial_doc: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare candidate documents to historical accepted/rejected decisions."""

    reviews = {
        str(row.get("candidate_id") or ""): str(row.get("review_status") or "")
        for row in editorial_doc.get("suggested_candidate_reviews") or []
        if isinstance(row, Mapping) and row.get("candidate_id")
    }
    current = _candidates(current_candidates_doc)
    v3 = _candidates(v3_candidates_doc)
    current_by_id = {_candidate_id(row): row for row in current}
    v3_ids = {_candidate_id(row) for row in v3}
    reviewed = [row for candidate_id, row in current_by_id.items() if reviews.get(candidate_id) in {"accepted", "rejected"}]
    accepted_ids = {candidate_id for candidate_id in current_by_id if reviews.get(candidate_id) == "accepted"}
    rejected_ids = {candidate_id for candidate_id in current_by_id if reviews.get(candidate_id) == "rejected"}
    accepted_kept = accepted_ids & v3_ids
    rejected_surviving = rejected_ids & v3_ids
    current_clusters = _cluster_count(current, current_candidates_doc)
    v3_clusters = _cluster_count(v3, v3_candidates_doc)

    return {
        "schema_version": "shot-candidate-operator-review-ab:v1",
        "evaluation_only": True,
        "current_policy_version": current_candidates_doc.get("policy_version"),
        "v3_policy_version": v3_candidates_doc.get("policy_version"),
        "operator_review_baseline": {
            "reviewed_candidates": len(reviewed),
            "accepted": len(accepted_ids),
            "rejected": len(rejected_ids),
        },
        "root_cause_diagnostics": _root_cause_diagnostics(
            [current_by_id[candidate_id] for candidate_id in sorted(rejected_ids)],
            [current_by_id[candidate_id] for candidate_id in sorted(accepted_ids)],
        ),
        "policies": {
            "current": _policy_metrics(current, current_clusters, accepted_ids, rejected_ids, available_candidate_ids=current_by_id),
            "v3": _policy_metrics(v3, v3_clusters, accepted_ids, rejected_ids, available_candidate_ids=v3_ids),
        },
        "comparison": {
            "accepted_suggestions_kept": len(accepted_kept),
            "accepted_suggestions_lost": len(accepted_ids - v3_ids),
            "rejected_candidates_surviving": len(rejected_surviving),
            "rejected_candidates_removed": len(rejected_ids - v3_ids),
            "review_action_reduction": current_clusters - v3_clusters,
            "review_action_reduction_ratio": _ratio(current_clusters - v3_clusters, current_clusters),
        },
        "limitations": [
            "Operator decisions are historical evaluation labels only and are never read by production candidate generation.",
            "The completed review covers generated suggestions, not the complete set of manually missed shots; it is not universal detector recall.",
        ],
    }


def _policy_metrics(
    candidates: list[dict[str, Any]],
    clusters: int,
    accepted_ids: set[str],
    rejected_ids: set[str],
    *,
    available_candidate_ids: Iterable[str],
) -> dict[str, int]:
    candidate_ids = {_candidate_id(row) for row in candidates}
    available_ids = set(available_candidate_ids)
    return {
        "raw_candidates": len(candidates),
        "review_clusters": clusters,
        "accepted_operator_shots_kept": len(accepted_ids & available_ids),
        "accepted_operator_shots_lost": len(accepted_ids - available_ids),
        "rejected_reviewed_candidates_surviving": len(rejected_ids & available_ids),
        "rejected_reviewed_candidates_removed": len(rejected_ids - available_ids),
        "unreviewed_or_unmapped_candidates": len(candidate_ids - accepted_ids - rejected_ids),
    }


def _root_cause_diagnostics(rejected: list[dict[str, Any]], accepted: list[dict[str, Any]]) -> dict[str, Any]:
    primary = Counter()
    overlapping = Counter()
    for candidate in rejected:
        pre_shot = _later_accepted_within(candidate, accepted, 3.0)
        same_receiver = bool(_record(candidate.get("receiver_evidence")).get("same_team_receiver_before_goal"))
        cross_like = bool(_record(candidate.get("trajectory_evidence")).get("cross_like"))
        nonterminal = not _terminal_goal_approach(candidate, goal_distance=8.0, corridor_distance=6.0)
        if pre_shot:
            overlapping["candidate_shortly_before_accepted_shot"] += 1
        if same_receiver:
            overlapping["same_team_receiver_shortly_after"] += 1
        if nonterminal:
            overlapping["progressive_goalward_without_terminal_evidence"] += 1
        if cross_like:
            overlapping["cross_like_trajectory"] += 1
        if pre_shot:
            primary["pre_shot_or_duplicate_action"] += 1
        elif same_receiver:
            primary["pass_continuation_to_same_team_receiver"] += 1
        elif nonterminal:
            primary["progressive_goalward_without_terminal_evidence"] += 1
        elif cross_like:
            primary["cross_like_or_wide_delivery"] += 1
        else:
            primary["other_or_unknown"] += 1
    return {
        "rejected_candidates": len(rejected),
        "primary_categories": dict(sorted(primary.items())),
        "overlapping_signals": dict(sorted(overlapping.items())),
    }


def _later_accepted_within(candidate: Mapping[str, Any], accepted: list[dict[str, Any]], horizon_sec: float) -> bool:
    source_match_id, timestamp = str(candidate.get("source_match_id") or ""), _timestamp(candidate)
    return any(
        str(other.get("source_match_id") or "") == source_match_id
        and 0 < _timestamp(other) - timestamp <= horizon_sec
        for other in accepted
    )


def _cluster_count(candidates: list[dict[str, Any]], document: Mapping[str, Any]) -> int:
    return len(build_review_clusters(
        candidates,
        candidate_generation_digest=f"evaluation:{document.get('policy_version') or 'unknown'}",
        timeline_span_sec=_number(document.get("timeline_span_sec")),
    ))


def _terminal_goal_approach(candidate: Mapping[str, Any], *, goal_distance: float, corridor_distance: float) -> bool:
    trajectory = _record(candidate.get("trajectory_evidence"))
    endpoint = _number(trajectory.get("endpoint_goal_distance_m"))
    corridor = _number(trajectory.get("goal_corridor_distance_m"))
    return endpoint is not None and corridor is not None and endpoint <= goal_distance and corridor <= corridor_distance


def _candidates(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in document.get("candidates") or [] if isinstance(row, Mapping)]


def _candidate_id(candidate: Mapping[str, Any]) -> str:
    return str(candidate.get("candidate_id") or candidate.get("candidate_key") or "")


def _timestamp(candidate: Mapping[str, Any]) -> float:
    return _number(candidate.get("source_timestamp_sec")) or _number(candidate.get("candidate_timestamp_sec")) or 0.0


def _record(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None
