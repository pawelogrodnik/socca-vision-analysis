from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.identity_jersey_number_common import canonical_digest


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "identity_jersey_number_targeted_benchmark_evaluation"
ALGORITHM_VERSION = "1.0.0"


def evaluate_targeted_jersey_number_propagation(
    selection_doc: dict[str, Any],
    consensus_doc: dict[str, Any],
    propagation_doc: dict[str, Any],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Evaluate N5 against target tracklets hidden during number review."""
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    consensus_by_subject = {
        str(row.get("candidate_subject_id")): row
        for row in consensus_doc.get("subjects") or []
        if isinstance(row, dict) and row.get("candidate_subject_id")
    }
    propagation_by_subject = {
        str(row.get("candidate_subject_id")): row
        for row in propagation_doc.get("subjects") or []
        if isinstance(row, dict) and row.get("candidate_subject_id")
    }

    rows: list[dict[str, Any]] = []
    all_expected_targets: set[tuple[str, str]] = set()
    all_propagated_targets: set[tuple[str, str]] = set()
    eligible_expected_targets: set[tuple[str, str]] = set()
    eligible_propagated_targets: set[tuple[str, str]] = set()

    for card in selection_doc.get("cards") or []:
        if not isinstance(card, dict) or not card.get("candidate_subject_id"):
            continue
        subject_id = str(card["candidate_subject_id"])
        selection = card.get("benchmark_selection") or {}
        expected_targets = {
            str(value) for value in selection.get("target_tracklet_ids") or [] if value
        }
        consensus = consensus_by_subject.get(subject_id) or {}
        propagation = propagation_by_subject.get(subject_id) or {}
        propagated = {
            str(value) for value in propagation.get("propagated_tracklet_ids") or [] if value
        }
        is_eligible = bool(consensus.get("strong_consensus"))

        expected_pairs = {(subject_id, value) for value in expected_targets}
        propagated_pairs = {(subject_id, value) for value in propagated}
        all_expected_targets.update(expected_pairs)
        all_propagated_targets.update(propagated_pairs)
        if is_eligible:
            eligible_expected_targets.update(expected_pairs)
            eligible_propagated_targets.update(propagated_pairs)

        matched = expected_targets & propagated
        unexpected = propagated - expected_targets
        rows.append(
            {
                "candidate_subject_id": subject_id,
                "strong_consensus": is_eligible,
                "consensus_state": consensus.get("state") or "missing",
                "consensus_number": consensus.get("consensus_number") or consensus.get("number"),
                "seed_tracklet_id": selection.get("seed_tracklet_id"),
                "expected_target_tracklet_ids": sorted(expected_targets),
                "propagated_target_tracklet_ids": sorted(propagated),
                "matched_target_tracklet_ids": sorted(matched),
                "missing_target_tracklet_ids": sorted(expected_targets - propagated) if is_eligible else [],
                "unexpected_propagated_tracklet_ids": sorted(unexpected),
                "status": (
                    "not_eligible_no_strong_consensus"
                    if not is_eligible
                    else "matched"
                    if matched == expected_targets and not unexpected
                    else "partial"
                    if matched
                    else "missed"
                ),
            }
        )

    matched_all = all_expected_targets & all_propagated_targets
    matched_eligible = eligible_expected_targets & eligible_propagated_targets
    unexpected_pairs = all_propagated_targets - all_expected_targets
    cross_subject = int((propagation_doc.get("summary") or {}).get("cross_subject_propagations") or 0)
    automatic_assignments = int((propagation_doc.get("summary") or {}).get("automatic_assignments") or 0)
    safety_passed = not unexpected_pairs and cross_subject == 0 and automatic_assignments == 0

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_operator_benchmark",
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION},
        "source": {
            "selection_digest": canonical_digest(selection_doc),
            "consensus_digest": canonical_digest(consensus_doc),
            "propagation_digest": canonical_digest(propagation_doc),
        },
        "summary": {
            "selected_subjects": len(rows),
            "strong_consensus_subjects": sum(row["strong_consensus"] for row in rows),
            "all_hidden_target_tracklets": len(all_expected_targets),
            "all_matched_hidden_target_tracklets": len(matched_all),
            "eligible_hidden_target_tracklets": len(eligible_expected_targets),
            "eligible_matched_hidden_target_tracklets": len(matched_eligible),
            "eligible_target_recall": _ratio(len(matched_eligible), len(eligible_expected_targets)),
            "unexpected_propagated_tracklets": len(unexpected_pairs),
            "cross_subject_propagations": cross_subject,
            "automatic_assignments": automatic_assignments,
            "safety_passed": safety_passed,
            "coverage_benefit_demonstrated": bool(matched_eligible),
        },
        "subjects": sorted(rows, key=lambda row: row["candidate_subject_id"]),
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None
