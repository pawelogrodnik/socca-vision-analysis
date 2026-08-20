from __future__ import annotations

"""Fail-closed action contracts for specialized Reviewed Identity evidence."""

from typing import Any


TEAM_ATTRIBUTION_ACTIONS = frozenset(
    {
        "assign_team",
        "referee",
        "false_detection",
        "team_unknown",
        "unresolved",
    }
)


class ReviewedIdentityActionScopeError(ValueError):
    """A requested correction violates the current review-unit contract."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def validate_review_unit_action_scope(
    payload: dict[str, Any],
    review_unit: dict[str, Any] | None,
) -> None:
    """Reject actions that are unsafe for the materialized evidence contract.

    Team-attribution crops show enough to resolve a team or a detection type,
    never enough to safely identify a named player or a canonical slot.  The
    check belongs at the service boundary so deferred, synchronous, and
    programmatic callers share exactly the same rule.
    """
    if not isinstance(review_unit, dict):
        return
    evidence = review_unit.get("visual_evidence") or {}
    if not isinstance(evidence, dict) or evidence.get("kind") != "team_attribution":
        return
    action = str(payload.get("action") or "").strip()
    if action not in TEAM_ATTRIBUTION_ACTIONS:
        raise ReviewedIdentityActionScopeError("team_attribution_action_not_allowed")
    if action == "assign_team" and str(payload.get("team_label") or "").upper() not in {
        "A",
        "B",
    }:
        raise ReviewedIdentityActionScopeError("team_attribution_team_label_invalid")


def review_unit_for_payload(
    progress: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Find the current queue unit addressed by a correction payload."""
    subject_id = str(payload.get("candidate_subject_id") or "").strip()
    target_id = str(payload.get("review_target_id") or "").strip() or None
    for queue_name in ("next_cases", "optional_audit_cases"):
        for unit in progress.get(queue_name) or []:
            if not isinstance(unit, dict):
                continue
            if str(unit.get("candidate_subject_id") or "") != subject_id:
                continue
            unit_target_id = str(unit.get("review_target_id") or "").strip() or None
            if unit_target_id == target_id:
                return unit
    return None
