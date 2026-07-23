from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from app.services.identity_jersey_number_common import (
    canonical_digest,
    canonical_structural_blockers,
)


SCHEMA_VERSION = "0.2.0"
ALGORITHM_NAME = "identity_roster_subject_review_shadow"
ALGORITHM_VERSION = "0.4.0"

DEFAULT_PARAMETERS: dict[str, Any] = {
    "min_visual_crops_for_ready": 3,
    "max_roster_candidates": 6,
}


def build_identity_roster_subject_review_shadow(
    roster_anchor_doc: dict[str, Any],
    anchor_crops_doc: dict[str, Any],
    *,
    jersey_consensus_doc: dict[str, Any] | None = None,
    generated_at: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build whole-stable-subject review cards from roster anchors and crops.

    This is a UI contract only. It intentionally does not write assignments,
    update identity, or make anything eligible for player statistics.
    """
    params = {**DEFAULT_PARAMETERS, **(parameters or {})}
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    crops_by_subject = {
        str(card.get("candidate_subject_id")): card
        for card in anchor_crops_doc.get("cards") or []
        if isinstance(card, dict) and card.get("candidate_subject_id")
    }
    jersey_by_subject = {
        str(row.get("candidate_subject_id")): row
        for row in (jersey_consensus_doc or {}).get("subjects") or []
        if isinstance(row, dict) and row.get("candidate_subject_id")
    }
    cards = [
        _review_card(
            roster_card,
            crops_by_subject.get(str(roster_card.get("candidate_subject_id") or "")),
            jersey_by_subject.get(str(roster_card.get("candidate_subject_id") or "")),
            params,
        )
        for roster_card in sorted(
            roster_anchor_doc.get("cards") or [],
            key=lambda row: (
                str(row.get("team_label") or "U"),
                int(row.get("start_frame") or 0),
                str(row.get("candidate_subject_id") or ""),
            ),
        )
    ]
    summary = _summary(cards)
    safety = {
        "mutates_candidate_identity": False,
        "mutates_production_identity": False,
        "writes_player_identity_assignments": False,
        "automatically_assigns_roster_players": False,
        "automatic_assignments": 0,
        "eligible_for_player_stats": False,
        "eligible_for_heatmaps": False,
        "operator_decision_required": True,
        "unit_of_review": "candidate_stable_subject",
    }
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_read_only",
        "algorithm": {
            "name": ALGORITHM_NAME,
            "version": ALGORITHM_VERSION,
            "parameters": params,
        },
        "source": {
            "roster_anchor_algorithm": roster_anchor_doc.get("algorithm") or {},
            "anchor_crops_algorithm": anchor_crops_doc.get("algorithm") or {},
            "jersey_consensus_algorithm": (jersey_consensus_doc or {}).get("algorithm") or {},
            "jersey_consensus_digest": canonical_digest(jersey_consensus_doc or {}),
        },
        "safety": safety,
        "summary": summary,
        "cards": cards,
    }
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_read_only",
        "algorithm": artifact["algorithm"],
        "status": "ready_for_operator_contract_audit" if summary["cards"] else "no_review_cards",
        "summary": summary,
        "gates": {
            "whole_subject_review_unit": all(card.get("review_unit") == "candidate_stable_subject" for card in cards),
            "no_crop_level_assignment_actions": all(
                "assign_single_crop" not in (card.get("allowed_actions") or []) for card in cards
            ),
            "automatic_assignments_disabled": safety["automatic_assignments"] == 0,
            "statistics_excluded": not safety["eligible_for_player_stats"],
            "conflicts_block_confirmation": all(
                "confirm_recommended_player" not in (card.get("allowed_actions") or [])
                for card in cards
                if card.get("review_status") == "blocked_conflict"
            ),
        },
        "limitations": [
            "This is a contract for UI/operator review; it does not persist decisions.",
            "Cards without enough visual evidence remain open and cannot be confirmed automatically.",
            "Conflict cards are visible for debugging but block confirmation actions.",
        ],
    }
    return {
        "identity_roster_subject_review_shadow": artifact,
        "identity_roster_subject_review_shadow_report": report,
    }


def _review_card(
    roster_card: dict[str, Any],
    crop_card: dict[str, Any] | None,
    jersey_consensus: dict[str, Any] | None,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    subject_id = str(roster_card.get("candidate_subject_id") or "")
    crops = [
        {**crop, "jersey_number_annotation": crop.get("jersey_number_annotation")}
        for crop in (crop_card or {}).get("anchor_crops") or []
        if isinstance(crop, dict)
    ]
    crop_count = len(crops)
    roster_status = str(roster_card.get("status") or "unresolved")
    jersey = _jersey_number_evidence(jersey_consensus)
    strong_jersey_consensus = bool((jersey_consensus or {}).get("strong_consensus"))
    jersey_player_id = (
        str(((jersey_consensus or {}).get("roster_match") or {}).get("player_id") or "")
        if strong_jersey_consensus
        else ""
    )
    current_player_id = str(roster_card.get("recommended_player_id") or "")
    jersey_conflict = bool(jersey_player_id and current_player_id and jersey_player_id != current_player_id)
    effective_roster_card = dict(roster_card)
    if jersey_player_id and not current_player_id:
        match = (jersey_consensus or {}).get("roster_match") or {}
        effective_roster_card.update(
            {
                "recommended_player_id": jersey_player_id,
                "recommended_player_name": match.get("player_name"),
                "recommendation_confidence": jersey.get("confidence"),
                "recommendation_source": "jersey_number_consensus",
            }
        )
    min_crops = int(parameters["min_visual_crops_for_ready"])
    blockers = _blockers(roster_status, crop_count, min_crops, roster_card)
    if jersey_conflict:
        blockers = sorted(set(blockers + ["jersey_number_roster_conflict"]))
    has_structural_blocker = bool(canonical_structural_blockers(blockers))
    review_status = _review_status(
        "conflict" if jersey_conflict or has_structural_blocker else roster_status,
        crop_count,
        min_crops,
    )
    allowed_actions = _allowed_actions(review_status, bool(effective_roster_card.get("recommended_player_id")))
    recommended_player = _recommended_player(effective_roster_card)
    roster_candidates = _roster_candidates(effective_roster_card, parameters=parameters)
    roster_candidates = _include_jersey_roster_candidate(roster_candidates, jersey_consensus)
    review_card_key = _review_card_key(subject_id, roster_card.get("anchor_key"))
    return {
        "review_card_key": review_card_key,
        "review_unit": "candidate_stable_subject",
        "candidate_subject_id": subject_id,
        "candidate_player_id": roster_card.get("candidate_player_id"),
        "production_subject_ids": sorted(
            str(value) for value in roster_card.get("production_subject_ids") or []
        ),
        "tracklet_ids": sorted(str(value) for value in roster_card.get("tracklet_ids") or []),
        "anchor_key": roster_card.get("anchor_key"),
        "team_label": roster_card.get("team_label"),
        "role": roster_card.get("role"),
        "start_frame": int(roster_card.get("start_frame") or 0),
        "end_frame": int(roster_card.get("end_frame") or 0),
        "detected_frames": int(roster_card.get("detected_frames") or 0),
        "roster_status": roster_status,
        "review_status": review_status,
        "recommended_player": recommended_player,
        "roster_candidates": roster_candidates,
        "jersey_number_evidence": jersey,
        "visual_evidence": {
            "status": (crop_card or {}).get("status") or "missing",
            "selected_crop_count": crop_count,
            "minimum_required": min_crops,
            "anchor_crops": crops,
            "rejected_observations": (crop_card or {}).get("rejected_observations") or {},
        },
        "quality_flags": sorted(set(str(value) for value in roster_card.get("quality_flags") or [])),
        "reason_codes": sorted(set(str(value) for value in roster_card.get("reason_codes") or [])),
        "blockers": blockers,
        "allowed_actions": allowed_actions,
        "decision_contract": _decision_contract(review_card_key, roster_candidates),
        "automatic_assignment": False,
        "eligible_for_player_stats": False,
        "requires_operator_review": True,
    }


def _review_status(roster_status: str, crop_count: int, minimum: int) -> str:
    if roster_status == "conflict":
        return "blocked_conflict"
    if crop_count >= minimum:
        return "ready_for_operator_review"
    if crop_count > 0:
        return "needs_more_visual_evidence"
    return "no_visual_evidence"


def _allowed_actions(review_status: str, has_recommendation: bool) -> list[str]:
    if review_status == "blocked_conflict":
        # A conflict blocks one-click confirmation, not an explicit operator choice.
        return ["assign_roster_player", "mark_unresolved", "open_debug_context"]
    if review_status == "ready_for_operator_review":
        actions = ["assign_roster_player", "mark_unresolved", "open_debug_context"]
        if has_recommendation:
            actions.insert(0, "confirm_recommended_player")
        return actions
    return ["mark_unresolved", "open_debug_context"]


def _recommended_player(roster_card: dict[str, Any]) -> dict[str, Any] | None:
    player_id = roster_card.get("recommended_player_id")
    if not player_id:
        return None
    return {
        "player_id": str(player_id),
        "player_name": roster_card.get("recommended_player_name"),
        "confidence": _round_or_none(roster_card.get("recommendation_confidence"), 4),
        "source": roster_card.get("recommendation_source") or "manual_anchor_or_p114_ranked_suggestion",
    }


def _roster_candidates(roster_card: dict[str, Any], *, parameters: dict[str, Any]) -> list[dict[str, Any]]:
    limit = int(parameters["max_roster_candidates"])
    rows: list[dict[str, Any]] = []
    for candidate in roster_card.get("roster_candidates") or []:
        if not isinstance(candidate, dict) or not candidate.get("player_id"):
            continue
        rows.append(
            {
                "player_id": str(candidate["player_id"]),
                "player_name": candidate.get("player_name"),
                "team_label": candidate.get("team_label") or roster_card.get("team_label"),
                "direct_coverage_ratio": _round_or_none(candidate.get("direct_coverage_ratio"), 6),
                "reid_support": _round_or_none(candidate.get("reid_support"), 6),
                "recommended": str(candidate.get("player_id")) == str(roster_card.get("recommended_player_id") or ""),
            }
        )
    return rows[:limit]


def _include_jersey_roster_candidate(
    candidates: list[dict[str, Any]],
    jersey_consensus: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not (jersey_consensus or {}).get("strong_consensus"):
        return candidates
    match = (jersey_consensus or {}).get("roster_match") or {}
    player_id = str(match.get("player_id") or "")
    if not player_id or any(row["player_id"] == player_id for row in candidates):
        return candidates
    return [
        {
            "player_id": player_id,
            "player_name": match.get("player_name"),
            "team_label": match.get("team_label") or (jersey_consensus or {}).get("team_label"),
            "direct_coverage_ratio": None,
            "reid_support": None,
            "recommended": False,
            "source": "jersey_number_consensus",
        },
        *candidates,
    ]


def _jersey_number_evidence(consensus: dict[str, Any] | None) -> dict[str, Any]:
    row = consensus or {}
    roster_match = row.get("roster_match") or {}
    return {
        "available": bool(row),
        "state": row.get("state") or "not_available",
        "detected_number": row.get("consensus_number"),
        "confidence": _round_or_none(row.get("consensus_confidence"), 4),
        "strong_consensus": bool(row.get("strong_consensus")),
        "supporting_reads": int(row.get("supporting_reads") or 0),
        "supporting_frames": [
            value for value in [row.get("first_support_frame"), row.get("last_support_frame")] if value is not None
        ],
        "conflicting_reads": int(row.get("conflicting_reads") or 0),
        "roster_match": roster_match or None,
        "reason_codes": list(row.get("reason_codes") or []),
        "shadow_only": True,
    }


def _blockers(
    roster_status: str,
    crop_count: int,
    minimum: int,
    roster_card: dict[str, Any],
) -> list[str]:
    blockers = canonical_structural_blockers(
        list(roster_card.get("quality_flags") or [])
        + list(roster_card.get("reason_codes") or [])
    )
    if roster_status == "conflict":
        blockers.append("roster_identity_conflict")
    if crop_count < minimum:
        blockers.append("insufficient_visual_evidence")
    if "parallel_roster_candidate_conflict" in (roster_card.get("reason_codes") or []):
        blockers.append("parallel_roster_candidate_conflict")
    return sorted(set(blockers))


def _decision_contract(review_card_key: str, roster_candidates: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "review_card_key": review_card_key,
        "decision_scope": "entire_candidate_stable_subject",
        "decision_schema": {
            "decision": [
                "confirm_recommended_player",
                "assign_roster_player",
                "mark_unresolved",
                "open_debug_context",
            ],
            "player_id": [row["player_id"] for row in roster_candidates],
            "comment": "optional",
        },
        "persistence_target": "future_player_identity_assignments_review",
        "writes_now": False,
    }


def _summary(cards: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = Counter(str(card.get("review_status") or "unknown") for card in cards)
    actions = Counter(action for card in cards for action in card.get("allowed_actions") or [])
    return {
        "cards": len(cards),
        "ready_for_operator_review": statuses.get("ready_for_operator_review", 0),
        "blocked_conflicts": statuses.get("blocked_conflict", 0),
        "needs_more_visual_evidence": statuses.get("needs_more_visual_evidence", 0),
        "no_visual_evidence": statuses.get("no_visual_evidence", 0),
        "selected_crops": sum(
            int((card.get("visual_evidence") or {}).get("selected_crop_count") or 0) for card in cards
        ),
        "cards_with_recommended_player": sum(1 for card in cards if card.get("recommended_player")),
        "status_counts": dict(sorted(statuses.items())),
        "allowed_action_counts": dict(sorted(actions.items())),
        "automatic_assignments": 0,
        "eligible_for_player_stats": 0,
    }


def _review_card_key(subject_id: str, anchor_key: Any) -> str:
    payload = json.dumps(
        {"candidate_subject_id": subject_id, "anchor_key": anchor_key},
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"subject-review:v1:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _round_or_none(value: Any, digits: int) -> float | None:
    return round(float(value), digits) if isinstance(value, (int, float)) else None
