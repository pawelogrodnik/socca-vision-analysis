from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import math
from typing import Any

from app.services.identity_fragment_consolidation_goldset import (
    classify_fragment_consolidation_proposal,
)


SCHEMA_VERSION = "0.2.0"
ALGORITHM_NAME = "identity_reid_fusion_shadow"
ALGORITHM_VERSION = "0.2.0"

UNTRUSTED_CANDIDATE_FLAGS = {
    "merges_production_subjects",
    "merges_multiple_production_subjects",
    "cross_production_transition",
    "uncertain_transition",
    "production_anchor_team_mismatch",
}

DEFAULT_PARAMETERS: dict[str, Any] = {
    "reid_weight": 0.15,
    "max_reid_distance": 1.0,
    "max_absolute_cost_adjustment": 0.08,
    "max_boundary_overlap_frames": 0,
    "max_required_speed_mps": 4.0,
    "min_accepted_embeddings": 3,
    "max_prototype_dispersion": 0.35,
}


def build_identity_reid_fusion_shadow(
    consolidation_doc: dict[str, Any],
    reid_doc: dict[str, Any],
    *,
    candidate_doc: dict[str, Any] | None = None,
    visual_content_doc: dict[str, Any] | None = None,
    generated_at: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fuse geometric continuity and ReID into an advisory review ranking.

    The result cannot merge subjects. ReID is applied only after hard constraints
    and its influence on the existing geometric cost is explicitly capped.
    """
    params = _parameters(parameters)
    reid_by_key = {
        str(row.get("proposal_key") or ""): row
        for row in reid_doc.get("pairs") or []
        if row.get("proposal_key")
    }
    reid_subject_by_id = {
        str(row.get("candidate_subject_id") or ""): row
        for row in reid_doc.get("subjects") or []
        if row.get("candidate_subject_id")
    }
    candidate_by_id = {
        str(row.get("candidate_subject_id") or ""): row
        for row in (candidate_doc or {}).get("subjects") or []
        if row.get("candidate_subject_id")
    }
    content_by_key = {
        str(row.get("proposal_key") or ""): row
        for row in (visual_content_doc or {}).get("pairs") or []
        if row.get("proposal_key")
    }
    proposals = sorted(
        consolidation_doc.get("proposals") or [],
        key=lambda row: str(row.get("proposal_key") or ""),
    )
    rows = [
        score_reid_fused_proposal(
            proposal,
            reid_by_key.get(str(proposal.get("proposal_key") or "")),
            source_reid_subject=reid_subject_by_id.get(
                str(proposal.get("source_candidate_subject_id") or "")
            ),
            target_reid_subject=reid_subject_by_id.get(
                str(proposal.get("target_candidate_subject_id") or "")
            ),
            source_candidate=candidate_by_id.get(
                str(proposal.get("source_candidate_subject_id") or "")
            ),
            target_candidate=candidate_by_id.get(
                str(proposal.get("target_candidate_subject_id") or "")
            ),
            visual_content_evidence=content_by_key.get(
                str(proposal.get("proposal_key") or "")
            ),
            parameters=params,
        )
        for proposal in proposals
    ]
    _assign_ranks(rows, "baseline_cost", "baseline_rank")
    _assign_ranks(rows, "fused_cost", "fused_rank")
    _assign_team_ranks(rows, "baseline_cost", "baseline_team_rank")
    _assign_team_ranks(rows, "fused_cost", "fused_team_rank")
    for row in rows:
        row["rank_delta"] = int(row["baseline_rank"]) - int(row["fused_rank"])
        row["team_rank_delta"] = int(row["baseline_team_rank"]) - int(
            row["fused_team_rank"]
        )

    status_counts = Counter(str(row["reid_status"]) for row in rows)
    constraint_counts = Counter(
        reason
        for row in rows
        for reason in row.get("hard_constraint_reasons") or []
    )
    adjusted = [row for row in rows if row["reid_applied"]]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "mode": "shadow_read_only",
        "algorithm": {
            "name": ALGORITHM_NAME,
            "version": ALGORITHM_VERSION,
            "parameters": params,
        },
        "source": {
            "fragment_consolidation": consolidation_doc.get("algorithm") or {},
            "same_match_reid": reid_doc.get("algorithm") or {},
        },
        "summary": {
            "proposals": len(rows),
            "reid_applied": len(adjusted),
            "reid_status_counts": dict(sorted(status_counts.items())),
            "hard_constraint_blocked": sum(
                bool(row.get("hard_constraint_reasons")) for row in rows
            ),
            "hard_constraint_reason_counts": dict(sorted(constraint_counts.items())),
            "mean_absolute_cost_adjustment": _mean(
                [abs(float(row["cost_adjustment"])) for row in adjusted]
            ),
            "max_absolute_cost_adjustment": max(
                (abs(float(row["cost_adjustment"])) for row in adjusted),
                default=0.0,
            ),
            "automatic_merges": 0,
        },
        "proposals": rows,
        "safety": {
            "mutates_candidate_identity": False,
            "mutates_production_identity": False,
            "automatically_merges_fragments": False,
            "reid_can_override_hard_constraints": False,
            "reid_can_change_strict_gate_decision": False,
            "statistics_untouched": True,
        },
        "limitations": [
            "The fused score ranks manual-review candidates; it is not a merge threshold.",
            "A reliable ReID distance can still collide for different players in identical kits.",
            "Missing or unreliable ReID evidence leaves the geometric baseline cost unchanged.",
        ],
    }


def score_reid_fused_proposal(
    proposal: dict[str, Any],
    reid_evidence: dict[str, Any] | None,
    *,
    source_reid_subject: dict[str, Any] | None = None,
    target_reid_subject: dict[str, Any] | None = None,
    source_candidate: dict[str, Any] | None = None,
    target_candidate: dict[str, Any] | None = None,
    visual_content_evidence: dict[str, Any] | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    params = _parameters(parameters)
    proposal_key = str(proposal.get("proposal_key") or "")
    baseline_cost = _clamp(1.0 - float(proposal.get("confidence") or 0.0))
    hard_reasons = _hard_constraint_reasons(
        proposal,
        params,
        source_candidate=source_candidate,
        target_candidate=target_candidate,
        visual_content_evidence=visual_content_evidence,
    )
    strict = classify_fragment_consolidation_proposal(proposal)
    reid_status = str((reid_evidence or {}).get("status") or "missing")
    distance = _finite_optional((reid_evidence or {}).get("prototype_distance"))
    appearance_reliable = bool((reid_evidence or {}).get("appearance_reliable"))
    evidence_reasons = _reid_evidence_reasons(
        reid_evidence,
        source_reid_subject=source_reid_subject,
        target_reid_subject=target_reid_subject,
        parameters=params,
    )
    reid_applied = bool(
        not hard_reasons
        and not evidence_reasons
        and float(params["reid_weight"]) > 0.0
        and reid_status == "available"
        and appearance_reliable
        and distance is not None
    )
    fused_cost = baseline_cost
    normalized_distance: float | None = None
    raw_adjustment = 0.0
    adjustment = 0.0
    reason_codes: list[str] = [*hard_reasons, *evidence_reasons]
    if reid_applied and distance is not None:
        normalized_distance = _clamp(distance / float(params["max_reid_distance"]))
        raw_fused = (
            (1.0 - float(params["reid_weight"])) * baseline_cost
            + float(params["reid_weight"]) * normalized_distance
        )
        raw_adjustment = raw_fused - baseline_cost
        cap = float(params["max_absolute_cost_adjustment"])
        adjustment = max(-cap, min(cap, raw_adjustment))
        fused_cost = _clamp(baseline_cost + adjustment)
        reason_codes.append("reid_bounded_tie_breaker_applied")
    else:
        if hard_reasons:
            reason_codes.append("reid_blocked_by_hard_constraint")
        elif evidence_reasons:
            reason_codes.append("reid_blocked_by_evidence_quality")
        elif float(params["reid_weight"]) == 0.0:
            reason_codes.append("reid_weight_zero")
        elif reid_status != "available":
            reason_codes.append(f"reid_{reid_status}")
        elif not appearance_reliable:
            reason_codes.append("reid_appearance_unreliable")
        else:
            reason_codes.append("reid_distance_missing")

    return {
        "proposal_key": proposal_key,
        "source_candidate_subject_id": proposal.get("source_candidate_subject_id"),
        "target_candidate_subject_id": proposal.get("target_candidate_subject_id"),
        "source_candidate_player_id": proposal.get("source_candidate_player_id"),
        "target_candidate_player_id": proposal.get("target_candidate_player_id"),
        "team_label": proposal.get("source_team_label"),
        "shared_production_anchor": proposal.get("shared_production_anchor"),
        "baseline_cost": round(baseline_cost, 6),
        "prototype_distance": round(distance, 6) if distance is not None else None,
        "normalized_reid_cost": (
            round(normalized_distance, 6) if normalized_distance is not None else None
        ),
        "raw_cost_adjustment": round(raw_adjustment, 6),
        "cost_adjustment": round(adjustment, 6),
        "fused_cost": round(fused_cost, 6),
        "reid_status": reid_status,
        "appearance_reliable": appearance_reliable,
        "source_accepted_embeddings": _integer_optional(
            (source_reid_subject or {}).get("accepted_embeddings")
        ),
        "target_accepted_embeddings": _integer_optional(
            (target_reid_subject or {}).get("accepted_embeddings")
        ),
        "source_prototype_dispersion": _finite_optional(
            (source_reid_subject or {}).get("prototype_dispersion")
        ),
        "target_prototype_dispersion": _finite_optional(
            (target_reid_subject or {}).get("prototype_dispersion")
        ),
        "source_candidate_flags": sorted(
            set((source_candidate or {}).get("quality_flags") or [])
        ),
        "target_candidate_flags": sorted(
            set((target_candidate or {}).get("quality_flags") or [])
        ),
        "visual_content_quality": str(
            (visual_content_evidence or {}).get("quality") or "missing"
        ),
        "reid_evidence_reasons": evidence_reasons,
        "reid_applied": reid_applied,
        "hard_constraint_reasons": hard_reasons,
        "strict_gate_passed": bool(strict.get("auto_accept")),
        "strict_gate_reason_codes": strict.get("reason_codes") or [],
        "decision": (
            "hard_constraint_blocked"
            if hard_reasons
            else "ranked_manual_review"
            if reid_applied
            else "manual_review_without_reid"
        ),
        "automatic_merge": False,
        "reason_codes": sorted(set(reason_codes)),
    }


def _hard_constraint_reasons(
    proposal: dict[str, Any],
    parameters: dict[str, Any],
    *,
    source_candidate: dict[str, Any] | None = None,
    target_candidate: dict[str, Any] | None = None,
    visual_content_evidence: dict[str, Any] | None = None,
) -> list[str]:
    reasons: list[str] = []
    source_team = str(proposal.get("source_team_label") or "U")
    target_team = str(proposal.get("target_team_label") or "U")
    if source_team not in {"A", "B"} or target_team not in {"A", "B"}:
        reasons.append("unknown_team")
    elif source_team != target_team:
        reasons.append("known_team_mismatch")
    if not str(proposal.get("shared_production_anchor") or ""):
        reasons.append("missing_safe_production_anchor")
    if int(proposal.get("overlap_frames") or 0) > int(
        parameters["max_boundary_overlap_frames"]
    ):
        reasons.append("parallel_temporal_overlap")
    required_speed = _finite_optional(proposal.get("required_speed_mps"))
    if required_speed is not None and required_speed > float(
        parameters["max_required_speed_mps"]
    ):
        reasons.append("impossible_required_speed")
    source_role = str((source_candidate or {}).get("role") or "")
    target_role = str((target_candidate or {}).get("role") or "")
    if source_role and target_role and source_role != target_role:
        reasons.append("role_conflict")
    if str((visual_content_evidence or {}).get("quality") or "") == "invalid_content":
        reasons.append("endpoint_not_person")
    for side, candidate in (
        ("source", source_candidate),
        ("target", target_candidate),
    ):
        flags = set((candidate or {}).get("quality_flags") or [])
        for flag in sorted(flags & UNTRUSTED_CANDIDATE_FLAGS):
            reasons.append(f"{side}_candidate_{flag}")
    return sorted(set(reasons))


def _reid_evidence_reasons(
    reid_evidence: dict[str, Any] | None,
    *,
    source_reid_subject: dict[str, Any] | None,
    target_reid_subject: dict[str, Any] | None,
    parameters: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    if str((reid_evidence or {}).get("status") or "missing") != "available":
        reasons.append("reid_pair_unavailable")
    if not bool((reid_evidence or {}).get("appearance_reliable")):
        reasons.append("reid_pair_appearance_unreliable")
    for side, subject in (
        ("source", source_reid_subject),
        ("target", target_reid_subject),
    ):
        if subject is None:
            reasons.append(f"{side}_reid_subject_missing")
            continue
        if not bool(subject.get("appearance_reliable")):
            reasons.append(f"{side}_prototype_unreliable")
        accepted = int(subject.get("accepted_embeddings") or 0)
        if accepted < int(parameters["min_accepted_embeddings"]):
            reasons.append(f"{side}_insufficient_embeddings")
        dispersion = _finite_optional(subject.get("prototype_dispersion"))
        if dispersion is None:
            reasons.append(f"{side}_prototype_dispersion_missing")
        elif dispersion > float(parameters["max_prototype_dispersion"]):
            reasons.append(f"{side}_prototype_too_disperse")
    return sorted(set(reasons))


def _assign_ranks(rows: list[dict[str, Any]], cost_key: str, rank_key: str) -> None:
    ordered = sorted(rows, key=lambda row: (float(row[cost_key]), str(row["proposal_key"])))
    for rank, row in enumerate(ordered, start=1):
        row[rank_key] = rank


def _assign_team_ranks(rows: list[dict[str, Any]], cost_key: str, rank_key: str) -> None:
    teams = sorted({str(row.get("team_label") or "U") for row in rows})
    for team in teams:
        ordered = sorted(
            (row for row in rows if str(row.get("team_label") or "U") == team),
            key=lambda row: (float(row[cost_key]), str(row["proposal_key"])),
        )
        for rank, row in enumerate(ordered, start=1):
            row[rank_key] = rank


def _parameters(parameters: dict[str, Any] | None) -> dict[str, Any]:
    result = {**DEFAULT_PARAMETERS, **(parameters or {})}
    if not 0.0 <= float(result["reid_weight"]) <= 1.0:
        raise ValueError("reid_weight must be between 0 and 1")
    if float(result["max_reid_distance"]) <= 0.0:
        raise ValueError("max_reid_distance must be positive")
    if float(result["max_absolute_cost_adjustment"]) < 0.0:
        raise ValueError("max_absolute_cost_adjustment must not be negative")
    return result


def _finite_optional(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _integer_optional(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 6) if values else 0.0
