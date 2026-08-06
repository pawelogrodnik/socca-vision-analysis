from __future__ import annotations

"""Validated whole-subject corrections over the existing reviewed stores."""

from pathlib import Path
from typing import Any

from app.services.identity_initial_audit_store import write_identity_json_atomic
from app.services.identity_reviewed_correction_context import (
    build_subject_context,
    current_reviewed_decision,
    load_required,
    match_roster,
    review_card_key,
    reviewed_correction_context,
    reviewed_decisions_semantic_digest,
)
from app.services.identity_reviewed_slot_review import (
    FILENAME as SLOT_REVIEW_FILENAME,
    clear_reviewed_slot_assignment,
    prepare_reviewed_slot_assignments,
)
from app.services.identity_reviewed_snapshot import get_reviewed_identity_status
from app.services.identity_roster_subject_review_store import (
    save_identity_roster_subject_review,
)
from app.services.identity_stable_anonymous import resolve_stable_anonymous_entities


CORRECTION_ACTIONS = frozenset(
    {
        "assign_roster_player",
        "assign_existing_slot",
        "create_new_stable_player",
        "referee",
        "false_detection",
        "team_unknown",
        "unresolved",
    }
)


def save_reviewed_identity_correction(
    match_path: Path,
    match_doc: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    subject_id = str(payload.get("candidate_subject_id") or "").strip()
    action = str(payload.get("action") or "").strip()
    if action not in CORRECTION_ACTIONS:
        raise ValueError(f"Unsupported reviewed identity correction action: {action}")
    context = build_subject_context(match_path, subject_id)
    card_key = review_card_key(match_path, subject_id)
    comment = str(payload.get("comment") or "").strip() or None

    if action == "assign_roster_player":
        if not card_key:
            raise ValueError(f"Candidate subject has no whole-subject review card: {subject_id}")
        player_id = str(payload.get("player_id") or "").strip()
        player = next(
            (row for row in match_roster(match_doc) if row["player_id"] == player_id),
            None,
        )
        if player is None:
            raise ValueError(f"Invalid player_id: {player_id or '<missing>'}")
        if player["team_label"] != context["team_label"]:
            raise ValueError(
                f"Cross-team roster assignment is not allowed: subject {subject_id} is "
                f"team {context['team_label']}, player is team {player['team_label']}"
            )
        save_identity_roster_subject_review(
            match_path,
            [
                {
                    "review_card_key": card_key,
                    "decision": "assign_roster_player",
                    "player_id": player_id,
                    "comment": comment,
                }
            ],
            match_doc=match_doc,
            allow_seeded_override=True,
        )
        clear_reviewed_slot_assignment(match_path, subject_id)
        allocated_slot = None
    else:
        candidate_document = load_required(
            match_path / "identity_candidate_shadow.json"
        )
        update = {
            "candidate_subject_id": subject_id,
            "action": action,
            "comment": comment,
        }
        if action == "assign_existing_slot":
            update["stable_slot_id"] = payload.get("stable_slot_id")
        if action == "create_new_stable_player":
            requested_team = str(payload.get("team_label") or context["team_label"]).upper()
            update["team_label"] = requested_team
        prepared = prepare_reviewed_slot_assignments(
            match_path,
            candidate_document,
            [update],
        )
        if action == "create_new_stable_player":
            _validate_new_player_active_cap(
                match_path,
                candidate_document,
                prepared,
                subject_id,
            )
        write_identity_json_atomic(match_path / SLOT_REVIEW_FILENAME, prepared)
        if card_key:
            save_identity_roster_subject_review(
                match_path,
                [{"review_card_key": card_key, "decision": "clear_decision"}],
                match_doc=match_doc,
            )
        saved_slot_decision = next(
            row
            for row in prepared.get("decisions") or []
            if row.get("candidate_subject_id") == subject_id
        )
        allocated_slot = (
            saved_slot_decision.get("stable_slot_id")
            if action == "create_new_stable_player"
            else None
        )

    saved = current_reviewed_decision(match_path, subject_id)
    snapshot = get_reviewed_identity_status(match_path)
    return {
        "saved_decision": saved,
        "effective_action": action,
        "allocated_stable_slot_id": allocated_slot,
        "snapshot": {
            "status": snapshot.get("status"),
            "stale": bool(snapshot.get("stale") or snapshot.get("status") == "stale"),
        },
        "semantic_decision_digest": reviewed_decisions_semantic_digest(match_path),
    }


def _validate_new_player_active_cap(
    match_path: Path,
    candidate_document: dict[str, Any],
    prepared: dict[str, Any],
    subject_id: str,
) -> None:
    tracklets_document = load_required(match_path / "tracklets.json")
    tracklets = {
        str(row.get("tracklet_id")): row
        for row in tracklets_document.get("tracklets") or []
        if row.get("tracklet_id")
    }
    resolved, _diagnostics = resolve_stable_anonymous_entities(
        match_path,
        tracklets,
        candidate_document,
        prepared,
    )
    subject_rows = [
        row for row in resolved.values() if row.get("candidate_subject_id") == subject_id
    ]
    blockers = sorted(
        {
            str(blocker)
            for row in subject_rows
            for blocker in row.get("hard_blockers") or []
        }
    )
    if "manual_new_player_active_team_cap_exceeded" in blockers:
        raise ValueError("Eighth simultaneous active player is not allowed")
    if blockers:
        raise ValueError(
            f"New stable player correction is structurally blocked: {', '.join(blockers)}"
        )
