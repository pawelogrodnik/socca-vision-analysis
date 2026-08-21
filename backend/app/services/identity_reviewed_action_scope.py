from __future__ import annotations

"""Server-authoritative Reviewed Identity action capabilities.

The review scope controls *where* a decision is applied. It must not make the
operator learn a different correction vocabulary for each internal source.
This module deliberately owns both the context contract and mutation gate so
the two cannot drift.
"""

from typing import Any


PRIMARY_ACTIONS = (
    "assign_roster_player",
    "assign_team",
    "split",
    "referee",
    "false_detection",
    "team_unknown",
    "unresolved",
)
ADVANCED_ACTIONS = ("assign_existing_slot", "create_new_stable_player")
MUTATION_ACTIONS = frozenset(
    set(PRIMARY_ACTIONS) - {"split"} | set(ADVANCED_ACTIONS) | {"mixed_players"}
)


class ReviewedIdentityActionScopeError(ValueError):
    """A requested correction violates the current review-unit contract."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def reviewed_identity_action_capabilities(
    review_unit: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Return the only action policy exposed to clients and accepted on save.

    Team-attribution imagery remains weak automatic evidence, but it does not
    prohibit an explicit human roster selection. Roster membership is checked
    later by the persistence layer and is therefore the authoritative team.
    """
    scope_kind = str((review_unit or {}).get("scope_kind") or "whole_subject")
    observation_count = int((review_unit or {}).get("detected_observation_count") or 0)
    can_split = observation_count >= 2
    capabilities: dict[str, dict[str, Any]] = {
        "assign_roster_player": {"allowed": True, "requires_player_id": True},
        "assign_team": {"allowed": True, "requires_team_label": True},
        "split": {
            "allowed": can_split,
            "mode": "temporal",
            "minimum_observations": 2,
            **({} if can_split else {"reason": "not_enough_observations"}),
        },
        "referee": {"allowed": True},
        "false_detection": {"allowed": True},
        "team_unknown": {"allowed": True},
        "unresolved": {"allowed": True},
        "assign_existing_slot": {
            "allowed": scope_kind != "material_continuity",
            "requires_slot_id": True,
        },
        "create_new_stable_player": {
            "allowed": scope_kind != "material_continuity",
            "requires_team_label": True,
        },
    }
    for action in ADVANCED_ACTIONS:
        if not capabilities[action]["allowed"]:
            capabilities[action]["reason"] = "scope_does_not_support_advanced_identity"
    return capabilities


def validate_review_unit_action_scope(
    payload: dict[str, Any],
    review_unit: dict[str, Any] | None,
) -> None:
    """Fail closed using :func:`reviewed_identity_action_capabilities`."""
    action = str(payload.get("action") or "").strip()
    if action not in MUTATION_ACTIONS:
        raise ReviewedIdentityActionScopeError("reviewed_identity_action_not_supported")
    # Legacy markers remain readable/writable for existing mixed-stage flows;
    # new inline split UI never sends this action.
    if action == "mixed_players":
        return
    capability = reviewed_identity_action_capabilities(review_unit).get(action)
    if not isinstance(capability, dict) or not capability.get("allowed"):
        raise ReviewedIdentityActionScopeError("reviewed_identity_action_not_allowed")
    if capability.get("requires_team_label") and action == "assign_team":
        if str(payload.get("team_label") or "").upper() not in {"A", "B"}:
            raise ReviewedIdentityActionScopeError("reviewed_identity_team_label_invalid")


def scope_copy(scope_kind: str) -> str:
    return {
        "canonical_segment": "Decyzja obejmie tylko pokazany fragment.",
        "material_continuity": "Decyzja obejmie tylko pokazane fragmenty tego ciągu.",
        "split_child": "Decyzja obejmie tylko ten fragment po podziale.",
    }.get(scope_kind, "Decyzja obejmie cały pokazany fragment zawodnika.")


def review_unit_for_payload(
    progress: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Find the current queue unit addressed by a correction payload."""
    subject_id = str(payload.get("candidate_subject_id") or "").strip()
    target_id = str(payload.get("review_target_id") or "").strip() or None
    queued_unit: dict[str, Any] | None = None
    for queue_name in ("next_cases", "optional_audit_cases"):
        for unit in progress.get(queue_name) or []:
            if not isinstance(unit, dict):
                continue
            if str(unit.get("candidate_subject_id") or "") != subject_id:
                continue
            unit_target_id = str(unit.get("review_target_id") or "").strip() or None
            if unit_target_id == target_id:
                queued_unit = unit
                break
        if queued_unit is not None:
            break
    if not isinstance(queued_unit, dict):
        return None
    if queued_unit.get("scope_kind") != "material_continuity":
        return queued_unit

    # Material cases are displayed without raw tracklet/frame pairs. The
    # correction service supplies this server-only collection so saving can be
    # exact without exposing technical ownership through the operator API.
    group_id = str(queued_unit.get("continuity_group_id") or subject_id)
    digest = str(queued_unit.get("source_ownership_digest") or "")
    for unit in progress.get("_internal_review_units") or []:
        if (
            isinstance(unit, dict)
            and unit.get("scope_kind") == "material_continuity"
            and str(unit.get("continuity_group_id") or "") == group_id
            and str(unit.get("source_ownership_digest") or "") == digest
        ):
            return unit
    return queued_unit
