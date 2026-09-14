from __future__ import annotations

"""Evaluation-only A/B reporting against durable Shot Review decisions.

This module is intentionally outside ``app.services``. Production candidate
generation never imports it and therefore cannot use operator labels to decide
which shot hypotheses to emit.
"""

from collections import Counter
from typing import Any, Iterable, Mapping

from app.services.shot_review_editor import build_review_clusters


def normalize_durable_shot_review_truth(
    editorial_doc: Mapping[str, Any],
    *,
    require_canonical_shot_id_for_accepted: bool = False,
) -> dict[str, dict[str, str | None]]:
    """Project durable candidate decisions into explicit evaluation truth.

    This intentionally reads only the candidate-level review lineage. A
    canonical shot's origin is not sufficient to identify the accepted
    suggestion that produced it.
    """

    result: dict[str, dict[str, str | None]] = {}
    rows = [row for row in editorial_doc.get("suggested_candidate_reviews") or [] if isinstance(row, Mapping)]
    for row in sorted(rows, key=lambda value: (str(value.get("candidate_id") or ""), str(value.get("reviewed_at") or ""))):
        candidate_id = str(row.get("candidate_id") or "").strip()
        review_status = str(row.get("review_status") or "").strip()
        if not candidate_id or review_status not in {"accepted", "rejected"}:
            continue
        canonical_shot_id = str(row.get("canonical_shot_id") or "").strip() or None
        if review_status == "accepted" and canonical_shot_id is None and require_canonical_shot_id_for_accepted:
            raise ValueError(f"Accepted durable review is missing canonical_shot_id: {candidate_id}")
        truth = {
            "candidate_id": candidate_id,
            "review_status": review_status,
            "canonical_shot_id": canonical_shot_id,
        }
        if candidate_id in result:
            raise ValueError(f"Duplicate durable candidate review: {candidate_id}")
        result[candidate_id] = truth
    return result


def evaluate_operator_review_ab(
    current_candidates_doc: Mapping[str, Any],
    v3_candidates_doc: Mapping[str, Any],
    editorial_doc: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare candidate documents to historical accepted/rejected decisions."""

    reviews = {candidate_id: str(row["review_status"] or "") for candidate_id, row in normalize_durable_shot_review_truth(editorial_doc).items()}
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


def evaluate_operator_review_three_way(
    current_candidates_doc: Mapping[str, Any],
    v3_candidates_doc: Mapping[str, Any],
    v4_candidates_doc: Mapping[str, Any],
    editorial_doc: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate v2/v3/v4 against the same historical review labels only.

    This is deliberately evaluation-only.  v4 candidate generation never sees
    this report or the editorial input.
    """

    reviews = {candidate_id: str(row["review_status"] or "") for candidate_id, row in normalize_durable_shot_review_truth(editorial_doc).items()}
    documents = {"v2": current_candidates_doc, "v3": v3_candidates_doc, "v4": v4_candidates_doc}
    candidates_by_policy = {key: _candidates(document) for key, document in documents.items()}
    baseline_ids = {_candidate_id(row) for row in candidates_by_policy["v2"]}
    accepted_ids = {candidate_id for candidate_id in baseline_ids if reviews.get(candidate_id) == "accepted"}
    rejected_ids = {candidate_id for candidate_id in baseline_ids if reviews.get(candidate_id) == "rejected"}
    policy_ids = {key: {_candidate_id(row) for row in rows} for key, rows in candidates_by_policy.items()}
    policy_metrics = {
        key: _policy_metrics(
            candidates_by_policy[key],
            _cluster_count(candidates_by_policy[key], documents[key]),
            accepted_ids,
            rejected_ids,
            available_candidate_ids=policy_ids[key],
        )
        for key in documents
    }
    baseline_reviewed = accepted_ids | rejected_ids
    return {
        "schema_version": "shot-candidate-operator-review-3way:v1",
        "evaluation_only": True,
        "policy_versions": {key: document.get("policy_version") for key, document in documents.items()},
        "operator_review_baseline": {
            "reviewed_candidates": len(baseline_reviewed),
            "accepted": len(accepted_ids),
            "rejected": len(rejected_ids),
        },
        "policies": policy_metrics,
        "comparisons": {
            key: {
                "delta_raw_candidates_vs_v2": policy_metrics[key]["raw_candidates"] - policy_metrics["v2"]["raw_candidates"],
                "delta_review_clusters_vs_v2": policy_metrics[key]["review_clusters"] - policy_metrics["v2"]["review_clusters"],
                "delta_review_clusters_vs_v3": policy_metrics[key]["review_clusters"] - policy_metrics["v3"]["review_clusters"],
                "rejected_reviewed_cases_newly_introduced_vs_v2": len((rejected_ids & policy_ids[key]) - (rejected_ids & baseline_ids)),
                "rejected_reviewed_cases_present_in_v4_absent_in_v3": len((rejected_ids & policy_ids[key]) - (rejected_ids & policy_ids["v3"])) if key == "v4" else 0,
                "rejected_reviewed_cases_unchanged_from_v2": len(rejected_ids & policy_ids[key]),
                "new_raw_candidates_without_prior_review_lineage": len(policy_ids[key] - baseline_ids),
            }
            for key in ("v3", "v4")
        },
        "limitations": [
            "Operator decisions are historical evaluation labels only and are never read by production candidate generation.",
            "New candidates cannot have historical review lineage until an operator reviews them.",
        ],
    }


def evaluate_operator_review_four_way(
    v2_candidates_doc: Mapping[str, Any],
    v3_candidates_doc: Mapping[str, Any],
    v4_candidates_doc: Mapping[str, Any],
    v5_candidates_doc: Mapping[str, Any],
    editorial_doc: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate v2/v3/v4/v5 against read-only historical review labels."""

    reviews = {candidate_id: str(row["review_status"] or "") for candidate_id, row in normalize_durable_shot_review_truth(editorial_doc).items()}
    documents = {"v2": v2_candidates_doc, "v3": v3_candidates_doc, "v4": v4_candidates_doc, "v5": v5_candidates_doc}
    candidates_by_policy = {key: _candidates(document) for key, document in documents.items()}
    baseline_ids = {_candidate_id(row) for row in candidates_by_policy["v2"]}
    accepted_ids = {candidate_id for candidate_id in baseline_ids if reviews.get(candidate_id) == "accepted"}
    rejected_ids = {candidate_id for candidate_id in baseline_ids if reviews.get(candidate_id) == "rejected"}
    policy_ids = {key: {_candidate_id(row) for row in rows} for key, rows in candidates_by_policy.items()}
    metrics = {
        key: _policy_metrics(
            candidates_by_policy[key],
            _cluster_count(candidates_by_policy[key], documents[key]),
            accepted_ids,
            rejected_ids,
            available_candidate_ids=policy_ids[key],
        )
        for key in documents
    }
    return {
        "schema_version": "shot-candidate-operator-review-4way:v1",
        "evaluation_only": True,
        "policy_versions": {key: document.get("policy_version") for key, document in documents.items()},
        "operator_review_baseline": {
            "reviewed_candidates": len(accepted_ids | rejected_ids),
            "accepted": len(accepted_ids),
            "rejected": len(rejected_ids),
        },
        "policies": metrics,
        "comparisons": {
            "v5": {
                "delta_raw_candidates_vs_v2": metrics["v5"]["raw_candidates"] - metrics["v2"]["raw_candidates"],
                "delta_raw_candidates_vs_v4": metrics["v5"]["raw_candidates"] - metrics["v4"]["raw_candidates"],
                "delta_review_clusters_vs_v2": metrics["v5"]["review_clusters"] - metrics["v2"]["review_clusters"],
                "delta_review_clusters_vs_v4": metrics["v5"]["review_clusters"] - metrics["v4"]["review_clusters"],
                "historical_accepted_delta_vs_v4": metrics["v5"]["accepted_operator_shots_kept"] - metrics["v4"]["accepted_operator_shots_kept"],
                "historical_rejected_delta_vs_v4": metrics["v5"]["rejected_reviewed_candidates_surviving"] - metrics["v4"]["rejected_reviewed_candidates_surviving"],
                "new_candidates_without_prior_review_lineage": len(policy_ids["v5"] - baseline_ids),
            },
        },
        "limitations": [
            "Operator decisions are historical evaluation labels only and are never read by production candidate generation.",
            "New v5 candidates have no historical review lineage until an operator reviews them.",
        ],
    }


def evaluate_operator_review_five_way(
    v2_candidates_doc: Mapping[str, Any],
    v3_candidates_doc: Mapping[str, Any],
    v4_candidates_doc: Mapping[str, Any],
    v5_candidates_doc: Mapping[str, Any],
    v6_candidates_doc: Mapping[str, Any],
    editorial_doc: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate v2 through v6 against read-only historical review labels."""

    reviews = {candidate_id: str(row["review_status"] or "") for candidate_id, row in normalize_durable_shot_review_truth(editorial_doc).items()}
    documents = {
        "v2": v2_candidates_doc,
        "v3": v3_candidates_doc,
        "v4": v4_candidates_doc,
        "v5": v5_candidates_doc,
        "v6": v6_candidates_doc,
    }
    candidates_by_policy = {key: _candidates(document) for key, document in documents.items()}
    baseline_ids = {_candidate_id(row) for row in candidates_by_policy["v2"]}
    accepted_ids = {candidate_id for candidate_id in baseline_ids if reviews.get(candidate_id) == "accepted"}
    rejected_ids = {candidate_id for candidate_id in baseline_ids if reviews.get(candidate_id) == "rejected"}
    policy_ids = {key: {_candidate_id(row) for row in rows} for key, rows in candidates_by_policy.items()}
    metrics = {
        key: _policy_metrics(
            candidates_by_policy[key],
            _cluster_count(candidates_by_policy[key], documents[key]),
            accepted_ids,
            rejected_ids,
            available_candidate_ids=policy_ids[key],
        )
        for key in documents
    }
    return {
        "schema_version": "shot-candidate-operator-review-5way:v1",
        "evaluation_only": True,
        "policy_versions": {key: document.get("policy_version") for key, document in documents.items()},
        "operator_review_baseline": {
            "reviewed_candidates": len(accepted_ids | rejected_ids),
            "accepted": len(accepted_ids),
            "rejected": len(rejected_ids),
        },
        "policies": metrics,
        "comparisons": {
            "v6_vs_v5": {
                "delta_raw_candidates": metrics["v6"]["raw_candidates"] - metrics["v5"]["raw_candidates"],
                "delta_review_clusters": metrics["v6"]["review_clusters"] - metrics["v5"]["review_clusters"],
                "historical_accepted_delta": metrics["v6"]["accepted_operator_shots_kept"] - metrics["v5"]["accepted_operator_shots_kept"],
                "historical_rejected_surviving_delta": metrics["v6"]["rejected_reviewed_candidates_surviving"] - metrics["v5"]["rejected_reviewed_candidates_surviving"],
                "historical_rejected_removed_delta": metrics["v6"]["rejected_reviewed_candidates_removed"] - metrics["v5"]["rejected_reviewed_candidates_removed"],
            },
        },
        "limitations": [
            "Operator decisions are historical evaluation labels only and are never read by production candidate generation.",
            "New candidates cannot have historical review lineage until an operator reviews them.",
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
