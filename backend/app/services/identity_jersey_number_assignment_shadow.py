from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from app.services.identity_jersey_number_common import (
    algorithm_signature,
    canonical_digest,
    canonical_structural_blockers,
    lineage_entry,
    stable_key,
)


SCHEMA_VERSION = "0.2.0"
ALGORITHM_NAME = "identity_jersey_number_assignment_shadow"
ALGORITHM_VERSION = "1.1.0"
DEFAULT_BENCHMARK_GATE_PARAMETERS: dict[str, Any] = {
    "minimum_reviewed_numbered_subjects": 3,
    "minimum_reviewed_no_number_subjects": 1,
    "minimum_reviewed_unreadable_subjects": 1,
    "minimum_heldout_matches": 1,
    "required_precision": 1.0,
}


def build_identity_jersey_number_assignment_shadow(
    consensus_doc: dict[str, Any],
    subject_review_doc: dict[str, Any],
    jersey_report_doc: dict[str, Any],
    *,
    evidence_doc: dict[str, Any] | None = None,
    roster_doc: dict[str, Any] | None = None,
    activation_requested: bool = False,
    generated_at: str | None = None,
    benchmark_gate_parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a gated N4 assignment plan without mutating candidate or production identity."""
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    review_by_subject = {
        str(row.get("candidate_subject_id")): row
        for row in subject_review_doc.get("cards") or []
        if isinstance(row, dict) and row.get("candidate_subject_id")
    }
    gate_parameters = {
        **DEFAULT_BENCHMARK_GATE_PARAMETERS,
        **(benchmark_gate_parameters or {}),
    }
    benchmark_gate = _benchmark_gate(jersey_report_doc, gate_parameters)
    lineage_gate = _lineage_gate(
        consensus_doc,
        subject_review_doc,
        jersey_report_doc,
        evidence_doc=evidence_doc,
        roster_doc=roster_doc,
    )
    activation_enabled = bool(
        activation_requested and benchmark_gate["passed"] and lineage_gate["passed"]
    )
    candidates = [
        _candidate(
            row,
            review_by_subject.get(str(row.get("candidate_subject_id") or "")),
            activation_enabled,
            lineage_fresh=lineage_gate["passed"],
        )
        for row in consensus_doc.get("subjects") or []
        if isinstance(row, dict) and row.get("strong_consensus")
    ]
    statuses = Counter(row["status"] for row in candidates)
    source = {
        "consensus_digest": canonical_digest(consensus_doc),
        "subject_review_digest": canonical_digest(subject_review_doc),
        "jersey_report_digest": canonical_digest(jersey_report_doc),
        "evidence_digest": canonical_digest(evidence_doc or {}),
        "roster_digest": canonical_digest(roster_doc or {}),
        "lineage": {
            "consensus": lineage_entry(consensus_doc),
            "subject_review": lineage_entry(subject_review_doc),
            "jersey_report": lineage_entry(jersey_report_doc),
            "evidence": lineage_entry(evidence_doc or {}),
            "roster": lineage_entry(roster_doc or {}),
        },
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_candidate_plan",
        "algorithm": {
            "name": ALGORITHM_NAME,
            "version": ALGORITHM_VERSION,
            "parameters": {"benchmark_gate": gate_parameters},
        },
        "source": source,
        "safety": {
            "mutates_candidate_identity": False,
            "mutates_production_identity": False,
            "writes_player_identity_assignments": False,
            "activation_requested": bool(activation_requested),
            "activation_enabled": activation_enabled,
            "automatic_assignments": 0,
            "benchmark_gate": benchmark_gate,
            "lineage_gate": lineage_gate,
        },
        "summary": {
            "strong_consensus_subjects": len(candidates),
            "strictly_eligible": sum(row["strictly_eligible"] for row in candidates),
            "would_assign_if_enabled": sum(row["would_assign_if_enabled"] for row in candidates),
            "status_counts": dict(sorted(statuses.items())),
        },
        "candidates": candidates,
        "gates": {
            "zero_false_assignments_required": True,
            "benchmark_passed": benchmark_gate["passed"],
            "lineage_fresh": lineage_gate["passed"],
            "all_assignments_are_shadow_only": all(not row["automatic_assignment"] for row in candidates),
            "hard_constraints_cannot_be_bypassed": all(
                not row["strictly_eligible"] or not row["blockers"] for row in candidates
            ),
        },
    }


def _candidate(
    consensus: dict[str, Any],
    review_card: dict[str, Any] | None,
    activation_enabled: bool,
    *,
    lineage_fresh: bool,
) -> dict[str, Any]:
    subject_id = str(consensus.get("candidate_subject_id") or "")
    roster_match = consensus.get("roster_match") or {}
    blockers: list[str] = []
    if not review_card:
        blockers.append("missing_whole_subject_review_card")
    if not lineage_fresh:
        blockers.append("stale_or_missing_lineage")
    if not roster_match.get("player_id"):
        blockers.append("missing_unique_same_team_roster_match")
    if not consensus.get("strong_consensus"):
        blockers.append("weak_number_consensus")
    if int(consensus.get("conflicting_reads") or 0) > 0:
        blockers.append("conflicting_trusted_number_reads")
    if str(consensus.get("team_label") or "U") == "U":
        blockers.append("unknown_team")
    if roster_match and str(roster_match.get("team_label") or consensus.get("team_label")) != str(
        consensus.get("team_label")
    ):
        blockers.append("cross_team_evidence")
    if review_card:
        evidence = list(review_card.get("blockers") or [])
        evidence.extend(review_card.get("quality_flags") or [])
        evidence.extend(review_card.get("reason_codes") or [])
        blockers.extend(canonical_structural_blockers(evidence))
        recommended = (review_card.get("recommended_player") or {}).get("player_id")
        if recommended and str(recommended) != str(roster_match.get("player_id") or ""):
            blockers.append("jersey_number_roster_conflict")
    blockers = sorted(set(blockers))
    eligible = not blockers
    would_assign = bool(eligible and activation_enabled)
    return {
        "assignment_key": stable_key(
            "jersey-assignment",
            {"candidate_subject_id": subject_id, "player_id": roster_match.get("player_id")},
        ),
        "candidate_subject_id": subject_id,
        "team_label": consensus.get("team_label"),
        "jersey_number": consensus.get("consensus_number"),
        "consensus_confidence": consensus.get("consensus_confidence"),
        "supporting_reads": int(consensus.get("supporting_reads") or 0),
        "player_id": roster_match.get("player_id"),
        "player_name": roster_match.get("player_name"),
        "strictly_eligible": eligible,
        "would_assign_if_enabled": would_assign,
        "automatic_assignment": False,
        "status": "eligible_shadow" if eligible else "blocked",
        "blockers": blockers,
        "reason_codes": ["unique_team_number_multi_frame_consensus"] if eligible else [],
    }


def _benchmark_gate(
    report: dict[str, Any],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    evaluation = report.get("goldset_evaluation") or {}
    available = bool(evaluation.get("available"))
    false_assignments = evaluation.get("identity_false_assignments")
    false_positive = evaluation.get("false_positive")
    precision = evaluation.get("precision")
    numbered = int(evaluation.get("reviewed_numbered_subjects") or 0)
    no_number = int(evaluation.get("reviewed_no_number_subjects") or 0)
    unreadable = int(evaluation.get("reviewed_unreadable_subjects") or 0)
    heldout = int(evaluation.get("heldout_matches") or 0)
    reasons: list[str] = []
    if not available:
        reasons.append("jersey_number_goldset_missing")
    elif int(evaluation.get("reviewed_subjects") or 0) <= 0:
        reasons.append("jersey_number_goldset_empty")
    if available and false_assignments != 0:
        reasons.append("jersey_number_goldset_has_false_assignments")
    if available and false_positive != 0:
        reasons.append("jersey_number_goldset_has_false_positives")
    if available and precision != float(parameters["required_precision"]):
        reasons.append("jersey_number_precision_below_required")
    if numbered < int(parameters["minimum_reviewed_numbered_subjects"]):
        reasons.append("insufficient_reviewed_numbered_subjects")
    if no_number < int(parameters["minimum_reviewed_no_number_subjects"]):
        reasons.append("insufficient_reviewed_no_number_subjects")
    if unreadable < int(parameters["minimum_reviewed_unreadable_subjects"]):
        reasons.append("insufficient_reviewed_unreadable_subjects")
    if heldout < int(parameters["minimum_heldout_matches"]):
        reasons.append("heldout_match_missing")
    passed = bool(available and not reasons)
    return {
        "passed": passed,
        "goldset_available": available,
        "reviewed_subjects": int(evaluation.get("reviewed_subjects") or 0),
        "reviewed_numbered_subjects": numbered,
        "reviewed_no_number_subjects": no_number,
        "reviewed_unreadable_subjects": unreadable,
        "heldout_matches": heldout,
        "identity_false_assignments": false_assignments,
        "false_positive": false_positive,
        "precision": precision,
        "parameters": parameters,
        "reason_codes": reasons,
    }


def _lineage_gate(
    consensus_doc: dict[str, Any],
    subject_review_doc: dict[str, Any],
    jersey_report_doc: dict[str, Any],
    *,
    evidence_doc: dict[str, Any] | None,
    roster_doc: dict[str, Any] | None,
) -> dict[str, Any]:
    consensus_source = consensus_doc.get("source") or {}
    review_source = subject_review_doc.get("source") or {}
    report_source = jersey_report_doc.get("source") or {}
    consensus_digest = canonical_digest(consensus_doc)
    reasons: list[str] = []
    if evidence_doc is None or roster_doc is None:
        reasons.append("current_lineage_inputs_missing")
    if not consensus_source.get("evidence_digest") or not consensus_source.get("roster_digest"):
        reasons.append("consensus_lineage_missing")
    if evidence_doc is not None and consensus_source.get("evidence_digest") != canonical_digest(evidence_doc):
        reasons.append("consensus_evidence_lineage_mismatch")
    if roster_doc is not None and consensus_source.get("roster_digest") != canonical_digest(roster_doc):
        reasons.append("consensus_roster_lineage_mismatch")
    if review_source.get("jersey_consensus_digest") != consensus_digest:
        reasons.append("subject_review_lineage_mismatch")
    if report_source != consensus_source:
        reasons.append("jersey_report_lineage_mismatch")
    for name, document in (
        ("consensus", consensus_doc),
        ("subject_review", subject_review_doc),
        ("jersey_report", jersey_report_doc),
        ("evidence", evidence_doc or {}),
        ("roster", roster_doc or {}),
    ):
        if algorithm_signature(document) is None:
            reasons.append(f"{name}_algorithm_signature_missing")
    return {
        "passed": not reasons,
        "consensus_digest": consensus_digest,
        "status": "fresh" if not reasons else "stale",
        "blocking_reason": None if not reasons else "stale_jersey_number_lineage",
        "reason_codes": reasons,
    }
