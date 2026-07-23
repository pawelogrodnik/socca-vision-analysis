from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.identity_jersey_number_common import (
    canonical_digest,
    canonical_structural_blockers,
    stable_key,
)


SCHEMA_VERSION = "0.3.0"
ALGORITHM_NAME = "identity_jersey_number_candidate_integration_shadow"
ALGORITHM_VERSION = "0.4.0"


def build_identity_jersey_number_candidate_integration_shadow(
    assignment_doc: dict[str, Any],
    propagation_doc: dict[str, Any],
    *,
    targeted_evaluation_doc: dict[str, Any] | None = None,
    heldout_validation_doc: dict[str, Any] | None = None,
    production_identity_unchanged: bool | None = None,
    activation_requested: bool = False,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build reversible candidate suggestions; never mutate candidate or production identity."""
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    assignment_gate = (assignment_doc.get("safety") or {}).get("benchmark_gate") or {}
    lineage_gate = (propagation_doc.get("safety") or {}).get("lineage_gate") or {}
    assignment_digest = canonical_digest(assignment_doc)
    propagation_digest = canonical_digest(propagation_doc)
    reason_codes: list[str] = []
    if not activation_requested:
        reason_codes.append("candidate_integration_not_requested")
    if not assignment_gate.get("passed"):
        reason_codes.append("jersey_number_benchmark_gate_failed")
    if not lineage_gate.get("passed") or propagation_doc.get("status") != "fresh":
        reason_codes.append("stale_jersey_number_lineage")
    if int((propagation_doc.get("summary") or {}).get("cross_subject_propagations") or 0) != 0:
        reason_codes.append("cross_subject_propagation_detected")
    if int((propagation_doc.get("summary") or {}).get("automatic_assignments") or 0) != 0:
        reason_codes.append("upstream_automatic_assignment_detected")
    targeted_summary = (targeted_evaluation_doc or {}).get("summary") or {}
    if not targeted_evaluation_doc:
        reason_codes.append("heldout_targeted_evaluation_missing")
    elif not targeted_summary.get("safety_passed"):
        reason_codes.append("heldout_targeted_evaluation_failed")
    if int(targeted_summary.get("unexpected_propagated_tracklets") or 0) != 0:
        reason_codes.append("unexpected_propagated_target")
    heldout_summary = (heldout_validation_doc or {}).get("summary") or {}
    if not heldout_validation_doc:
        reason_codes.append("heldout_multi_match_validation_missing")
    elif not heldout_summary.get("activation_gate_passed"):
        reason_codes.append("heldout_multi_match_validation_failed")
    if any(
        int(heldout_summary.get(name) or 0) != 0
        for name in (
            "identity_false_assignments",
            "false_number_reads",
            "unexpected_propagated_tracklets",
            "automatic_assignments",
        )
    ):
        reason_codes.append("heldout_safety_failures_detected")
    targeted_evaluation_digest = (
        canonical_digest(targeted_evaluation_doc) if targeted_evaluation_doc else None
    )
    matching_heldout_cases = []
    for case in (heldout_validation_doc or {}).get("cases") or []:
        if not isinstance(case, dict):
            continue
        source_digests = case.get("source_digests") or {}
        if not isinstance(source_digests, dict):
            continue
        if (
            source_digests.get("assignment") == assignment_digest
            and source_digests.get("propagation") == propagation_digest
            and source_digests.get("targeted_evaluation") == targeted_evaluation_digest
        ):
            matching_heldout_cases.append(case)
    if not matching_heldout_cases:
        reason_codes.append("matching_canonical_heldout_case_missing")
    elif not any(
        case.get("canonical_case_origin") is True
        and case.get("held_out") is True
        and case.get("case_contract_valid") is True
        and case.get("production_identity_unchanged") is True
        and int(case.get("identity_false_assignments") or 0) == 0
        and int(case.get("false_number_reads") or 0) == 0
        and int(case.get("unexpected_propagated_tracklets") or 0) == 0
        and int(case.get("automatic_assignments") or 0) == 0
        for case in matching_heldout_cases
    ):
        reason_codes.append("matching_canonical_heldout_case_invalid")
    if int(heldout_summary.get("distinct_source_matches") or 0) < 2:
        reason_codes.append("insufficient_external_match_coverage")
    enabled = bool(activation_requested and not reason_codes)
    assignments = {
        str(row.get("candidate_subject_id")): row
        for row in assignment_doc.get("candidates") or []
        if isinstance(row, dict) and row.get("candidate_subject_id")
    }
    suggestions = []
    if enabled:
        for row in propagation_doc.get("subjects") or []:
            assignment = assignments.get(str(row.get("candidate_subject_id") or "")) or {}
            blockers = canonical_structural_blockers(
                set(row.get("subject_blockers") or []) | set(assignment.get("blockers") or [])
            )
            propagated = list(row.get("number_propagated_tracklet_ids") or [])
            if blockers or not assignment.get("strictly_eligible") or not propagated:
                continue
            suggestions.append(
                {
                    "suggestion_key": stable_key(
                        "jersey-candidate-suggestion",
                        {
                            "candidate_subject_id": row.get("candidate_subject_id"),
                            "player_id": assignment.get("player_id"),
                        },
                    ),
                    "candidate_subject_id": row.get("candidate_subject_id"),
                    "player_id": assignment.get("player_id"),
                    "player_name": assignment.get("player_name"),
                    "team_label": assignment.get("team_label"),
                    "jersey_number": assignment.get("jersey_number"),
                    "number_seed_tracklet_ids": row.get("number_seed_tracklet_ids") or [],
                    "number_propagated_tracklet_ids": propagated,
                    "action": "suggest_roster_player_for_candidate_review",
                    "automatic_assignment": False,
                }
            )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "candidate_only_shadow",
        "status": "ready_shadow" if enabled else "disabled",
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION, "parameters": {}},
        "source": {
            "assignment_digest": assignment_digest,
            "propagation_digest": propagation_digest,
            "targeted_evaluation_digest": targeted_evaluation_digest,
            "heldout_validation_digest": (
                canonical_digest(heldout_validation_doc) if heldout_validation_doc else None
            ),
        },
        "safety": {
            "activation_requested": bool(activation_requested),
            "activation_enabled": enabled,
            "production_identity_unchanged": production_identity_unchanged,
            "mutates_candidate_identity": False,
            "mutates_production_identity": False,
            "writes_player_identity_assignments": False,
            "publishes_player_stats": False,
            "merges_subjects": False,
            "creates_lineage_edges": False,
            "automatic_assignments": 0,
            "reason_codes": reason_codes,
        },
        "summary": {"candidate_suggestions": len(suggestions), "automatic_assignments": 0},
        "suggestions": suggestions,
    }
