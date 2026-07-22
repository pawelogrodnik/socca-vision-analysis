from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from app.services.identity_jersey_number_common import canonical_digest, stable_key


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "identity_jersey_number_assignment_shadow"
ALGORITHM_VERSION = "1.0.0"
STRUCTURAL_BLOCKERS = {
    "jersey_number_roster_conflict",
    "parallel_roster_candidate_conflict",
    "roster_identity_conflict",
    "temporal_overlap_conflict",
    "parallel_distant_observation",
    "structural_identity_conflict",
    "cross_team_evidence",
}


def build_identity_jersey_number_assignment_shadow(
    consensus_doc: dict[str, Any],
    subject_review_doc: dict[str, Any],
    jersey_report_doc: dict[str, Any],
    *,
    activation_requested: bool = False,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build a gated N4 assignment plan without mutating candidate or production identity."""
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    review_by_subject = {
        str(row.get("candidate_subject_id")): row
        for row in subject_review_doc.get("cards") or []
        if isinstance(row, dict) and row.get("candidate_subject_id")
    }
    benchmark_gate = _benchmark_gate(jersey_report_doc)
    lineage_gate = _lineage_gate(consensus_doc, subject_review_doc, jersey_report_doc)
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
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_candidate_plan",
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION},
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
        evidence = set(str(value) for value in review_card.get("blockers") or [])
        evidence.update(str(value) for value in review_card.get("quality_flags") or [])
        evidence.update(str(value) for value in review_card.get("reason_codes") or [])
        blockers.extend(sorted(evidence & STRUCTURAL_BLOCKERS))
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


def _benchmark_gate(report: dict[str, Any]) -> dict[str, Any]:
    evaluation = report.get("goldset_evaluation") or {}
    available = bool(evaluation.get("available"))
    false_assignments = evaluation.get("identity_false_assignments")
    reviewed = int(evaluation.get("expected_subjects") or 0)
    passed = bool(available and reviewed > 0 and false_assignments == 0)
    reasons: list[str] = []
    if not available:
        reasons.append("jersey_number_goldset_missing")
    elif reviewed <= 0:
        reasons.append("jersey_number_goldset_empty")
    if available and false_assignments != 0:
        reasons.append("jersey_number_goldset_has_false_assignments")
    return {
        "passed": passed,
        "goldset_available": available,
        "reviewed_subjects": reviewed,
        "identity_false_assignments": false_assignments,
        "reason_codes": reasons,
    }


def _lineage_gate(
    consensus_doc: dict[str, Any],
    subject_review_doc: dict[str, Any],
    jersey_report_doc: dict[str, Any],
) -> dict[str, Any]:
    consensus_source = consensus_doc.get("source") or {}
    review_source = subject_review_doc.get("source") or {}
    report_source = jersey_report_doc.get("source") or {}
    consensus_digest = canonical_digest(consensus_doc)
    reasons: list[str] = []
    if not consensus_source.get("evidence_digest") or not consensus_source.get("roster_digest"):
        reasons.append("consensus_lineage_missing")
    if review_source.get("jersey_consensus_digest") != consensus_digest:
        reasons.append("subject_review_lineage_mismatch")
    if report_source != consensus_source:
        reasons.append("jersey_report_lineage_mismatch")
    return {
        "passed": not reasons,
        "consensus_digest": consensus_digest,
        "reason_codes": reasons,
    }
