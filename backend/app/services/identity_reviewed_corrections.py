from __future__ import annotations

"""Validated whole-subject corrections over the existing reviewed stores."""

import logging
from pathlib import Path
from typing import Any

from app.services.identity_initial_audit_store import write_identity_json_atomic
from app.services.identity_reviewed_active_cap import (
    load_reviewed_detected_team_labels,
    validate_new_player_active_cap_from_progress,
)
from app.services.identity_reviewed_action_scope import (
    review_unit_for_payload,
    validate_review_unit_action_scope,
)
from app.services.identity_reviewed_correction_context import (
    build_materialized_subject_context,
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
    load_reviewed_slot_assignments,
    prepare_reviewed_slot_assignments,
)
from app.services.identity_reviewed_slot_registry import normalize_reviewed_slot_id
from app.services.identity_reviewed_recompute_state import (
    mark_reviewed_identity_recompute_required,
)
from app.services.identity_reviewed_segments import (
    SegmentTargetError,
    build_segment_review_document,
    load_segment_review,
    save_segment_decision,
)
from app.services.identity_reviewed_snapshot import get_reviewed_identity_status
from app.services.identity_reviewed_progress import (
    build_reviewed_identity_progress,
    decision_impact,
)
from app.services.identity_reviewed_mixed_store import save_mixed_player_classification
from app.services.identity_roster_subject_review_store import (
    save_identity_roster_subject_review,
)
from app.services.identity_stable_anonymous import resolve_stable_anonymous_entities


CORRECTION_ACTIONS = frozenset(
    {
        "assign_roster_player",
        "assign_existing_slot",
        "assign_team",
        "create_new_stable_player",
        "referee",
        "false_detection",
        "team_unknown",
        "unresolved",
        "mixed_players",
    }
)
logger = logging.getLogger(__name__)


def save_reviewed_identity_correction(
    match_path: Path,
    match_doc: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Backward-compatible synchronous service response for legacy callers."""
    subject_id = str(payload.get("candidate_subject_id") or "").strip()
    action = str(payload.get("action") or "").strip()
    try:
        progress_before = build_reviewed_identity_progress(match_path, match_doc)
        result = persist_reviewed_identity_correction(
            match_path,
            match_doc,
            payload,
            use_materialized_context=False,
            authorized_review_unit=review_unit_for_payload(progress_before, payload),
        )
        review_target_id = str(payload.get("review_target_id") or "").strip() or None
        if review_target_id:
            build_segment_review_document(match_path, match_doc)
        snapshot = get_reviewed_identity_status(match_path)
        progress_after = build_reviewed_identity_progress(match_path, match_doc)
        impact = decision_impact(
            progress_before,
            progress_after,
            subject_id,
            review_target_id,
        )
        logger.info(
            "[review-progress] match=%s action=%s subject=%s affected_tracklets=%s "
            "affected_observations=%s reviewed_ratio=%.2f%%->%.2f%% important_remaining=%s->%s "
            "optional_remaining=%s structural_blockers=%s snapshot=%s",
            match_doc.get("id") or match_path.name,
            action,
            subject_id,
            impact["affected_tracklets"],
            impact["affected_detected_observations"],
            float(impact["operator_reviewed_ratio_before"]) * 100,
            float(impact["operator_reviewed_ratio_after"]) * 100,
            impact["important_decisions_remaining_before"],
            impact["important_decisions_remaining_after"],
            progress_after["summary"]["optional_cases_remaining"],
            progress_after["summary"]["structural_blockers"],
            snapshot.get("status"),
        )
        return {
            **result,
            "recompute_deferred": False,
            "snapshot": {
                "status": snapshot.get("status"),
                "stale": bool(
                    snapshot.get("stale") or snapshot.get("status") == "stale"
                ),
            },
            "review_progress": progress_after,
            "decision_impact": impact,
        }
    except ValueError as exc:
        logger.info(
            "[review-progress] match=%s action=%s subject=%s status=rejected reason=%s",
            match_doc.get("id") or match_path.name,
            action or "unknown",
            subject_id or "unknown",
            str(exc).replace(" ", "_")[:160],
        )
        raise


def persist_reviewed_identity_correction(
    match_path: Path,
    match_doc: dict[str, Any],
    payload: dict[str, Any],
    *,
    use_materialized_context: bool = True,
    trusted_materialized_detected_team_labels: dict[str, set[str]] | None = None,
    authorized_review_unit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate and durably save one decision without full-match recomputation."""
    subject_id = str(payload.get("candidate_subject_id") or "").strip()
    action = str(payload.get("action") or "").strip()
    if action not in CORRECTION_ACTIONS:
        raise ValueError(f"Unsupported reviewed identity correction action: {action}")
    validate_review_unit_action_scope(payload, authorized_review_unit)
    review_target_id = str(payload.get("review_target_id") or "").strip() or None
    if (
        isinstance(authorized_review_unit, dict)
        and authorized_review_unit.get("scope_kind") == "material_continuity"
    ):
        if review_target_id:
            raise ValueError("material_continuity_review_target_not_supported")
        return _persist_material_continuity_correction(
            match_path,
            match_doc,
            payload,
            authorized_review_unit,
        )
    if action == "mixed_players":
        if review_target_id:
            raise ValueError("mixed_players is a whole-subject classification")
        saved = save_mixed_player_classification(
            match_path,
            match_doc,
            subject_id,
            payload.get("mixed_hint"),
            payload.get("comment"),
        )
        semantic_digest = reviewed_decisions_semantic_digest(match_path)
        mark_reviewed_identity_recompute_required(
            match_path,
            semantic_decision_digest=semantic_digest,
        )
        return {
            "saved_decision": saved,
            "effective_action": action,
            "allocated_stable_slot_id": None,
            "semantic_decision_digest": semantic_digest,
            "recompute_deferred": True,
            "persistence": {
                "status": "saved",
                "downstream_recompute_triggered": False,
            },
        }
    if review_target_id:
        materialized_review = load_segment_review(match_path)
        saved = save_segment_decision(
            match_path,
            match_doc,
            payload,
            materialized_review=materialized_review,
        )
        allocated_slot = None
    else:
        materialized_review = load_segment_review(match_path)
        if any(
            str(row.get("candidate_subject_id") or "") == subject_id
            for row in materialized_review.get("targets") or []
        ):
            raise SegmentTargetError("review_target_required")
        candidate_document = load_required(
            match_path / "identity_candidate_shadow.json"
        )
        detected_team_labels = trusted_materialized_detected_team_labels
        if use_materialized_context and detected_team_labels is None:
            detected_team_labels = load_reviewed_detected_team_labels(match_path)
        use_exact_materialized_context = bool(
            use_materialized_context
            and detected_team_labels is not None
            and subject_id in detected_team_labels
        )
        context = (
            build_materialized_subject_context(candidate_document, subject_id)
            if use_exact_materialized_context
            else build_subject_context(match_path, subject_id)
        )
        card_key = review_card_key(match_path, subject_id)
        comment = str(payload.get("comment") or "").strip() or None
        if action == "assign_roster_player":
            player_id = str(payload.get("player_id") or "").strip()
            player = next(
                (
                    row
                    for row in match_roster(match_doc)
                    if row["player_id"] == player_id
                ),
                None,
            )
            if player is None:
                raise ValueError(f"Invalid player_id: {player_id or '<missing>'}")
            source_team_label = str(context["team_label"])
            corrects_detected_team = bool(
                source_team_label in {"A", "B"}
                and source_team_label != player["team_label"]
            )
            prepared = prepare_reviewed_slot_assignments(
                match_path,
                candidate_document,
                [
                    {
                        "candidate_subject_id": subject_id,
                        "action": action,
                        "player_id": player_id,
                        "team_label": player["team_label"],
                        "source_team_label": source_team_label,
                        "stable_slot_id": (
                            None
                            if corrects_detected_team
                            else (
                                _materialized_subject_canonical_slot(
                                    candidate_document,
                                    subject_id,
                                )
                                if use_exact_materialized_context
                                else _safe_subject_canonical_slot(
                                    match_path,
                                    candidate_document,
                                    subject_id,
                                )
                            )
                        ),
                        "comment": comment,
                    }
                ],
                use_materialized_candidate_context=use_exact_materialized_context,
                materialized_detected_team_labels=(
                    detected_team_labels if use_exact_materialized_context else None
                ),
                allow_roster_team_correction=corrects_detected_team,
            )
            # The legacy card store intentionally exposes same-team choices only.
            # A cross-team named correction is fully represented by the reviewed
            # slot decision, including its source-team contradiction.
            if card_key and not corrects_detected_team:
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
                    defer_seeded_reduction=use_materialized_context,
                )
            write_identity_json_atomic(match_path / SLOT_REVIEW_FILENAME, prepared)
            allocated_slot = None
        else:
            update = {
                "candidate_subject_id": subject_id,
                "action": action,
                "comment": comment,
            }
            if action == "assign_existing_slot":
                update["stable_slot_id"] = payload.get("stable_slot_id")
            if action in {"assign_team", "create_new_stable_player"}:
                update["team_label"] = str(
                    payload.get("team_label") or context["team_label"]
                ).upper()
            prepared = prepare_reviewed_slot_assignments(
                match_path,
                candidate_document,
                [update],
                use_materialized_candidate_context=use_exact_materialized_context,
                materialized_detected_team_labels=(
                    detected_team_labels if use_exact_materialized_context else None
                ),
            )
            if action == "create_new_stable_player":
                _validate_new_player_active_cap(
                    match_path,
                    candidate_document,
                    prepared,
                    subject_id,
                    use_materialized_context=use_exact_materialized_context,
                )
            write_identity_json_atomic(match_path / SLOT_REVIEW_FILENAME, prepared)
            if card_key:
                save_identity_roster_subject_review(
                    match_path,
                    [{"review_card_key": card_key, "decision": "clear_decision"}],
                    match_doc=match_doc,
                    defer_seeded_reduction=use_materialized_context,
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

    semantic_digest = reviewed_decisions_semantic_digest(match_path)
    mark_reviewed_identity_recompute_required(
        match_path,
        semantic_decision_digest=semantic_digest,
    )
    return {
        "saved_decision": saved,
        "effective_action": action,
        "allocated_stable_slot_id": allocated_slot,
        "semantic_decision_digest": semantic_digest,
        "recompute_deferred": True,
        "persistence": {
            "status": "saved",
            "downstream_recompute_triggered": False,
        },
    }


def _persist_material_continuity_correction(
    match_path: Path,
    match_doc: dict[str, Any],
    payload: dict[str, Any],
    review_unit: dict[str, Any],
) -> dict[str, Any]:
    """Persist one decision across the exact safe members of a gap group.

    The continuity group is rebuilt and authorized by the normal Review queue.
    It is deliberately not a stable-slot assignment: A12 is evidence of local
    continuity, never a global player binding.
    """
    action = str(payload.get("action") or "").strip()
    if action not in {"assign_roster_player", "unresolved"}:
        raise ValueError("material_continuity_action_not_allowed")
    subject_ids = sorted(
        {
            str(value).strip()
            for value in review_unit.get("continuity_subject_ids") or []
            if str(value).strip()
        }
    )
    if not subject_ids:
        raise ValueError("material_continuity_members_missing")
    team_label = str(review_unit.get("effective_team_label") or "").upper()
    if team_label != "A":
        raise ValueError("material_continuity_team_not_supported")
    candidate_document = load_required(match_path / "identity_candidate_shadow.json")
    comment = str(payload.get("comment") or "").strip() or None
    player_id: str | None = None
    if action == "assign_roster_player":
        player_id = str(payload.get("player_id") or "").strip()
        player = next(
            (row for row in match_roster(match_doc) if row["player_id"] == player_id),
            None,
        )
        if player is None:
            raise ValueError(f"Invalid player_id: {player_id or '<missing>'}")
        if str(player.get("team_label") or "").upper() != team_label:
            raise ValueError("player_id must be one of the same-team operator roster options")

    updates: list[dict[str, Any]] = []
    for member_subject_id in subject_ids:
        context = build_materialized_subject_context(candidate_document, member_subject_id)
        if str(context.get("team_label") or "").upper() != team_label:
            raise ValueError("material_continuity_member_team_mismatch")
        update: dict[str, Any] = {
            "candidate_subject_id": member_subject_id,
            "action": action,
            "comment": comment,
            # Preserve the exact, already-safe Team-A context for each raw
            # member.  This is provenance only; it does not promote the
            # continuity slot to a global player binding.
            "source_team_label": team_label,
        }
        if player_id is not None:
            update.update(
                {
                    "player_id": player_id,
                    "team_label": team_label,
                    # Keep this None: the correction applies only to the four
                    # exact subjects, not to every A12 observation.
                    "stable_slot_id": None,
                }
            )
        updates.append(update)

    prepared = prepare_reviewed_slot_assignments(
        match_path,
        candidate_document,
        updates,
        use_materialized_candidate_context=True,
    )
    write_identity_json_atomic(match_path / SLOT_REVIEW_FILENAME, prepared)
    saved_members = [
        row
        for row in prepared.get("decisions") or []
        if str(row.get("candidate_subject_id") or "") in set(subject_ids)
    ]
    semantic_digest = reviewed_decisions_semantic_digest(match_path)
    mark_reviewed_identity_recompute_required(
        match_path,
        semantic_decision_digest=semantic_digest,
    )
    return {
        "saved_decision": {
            "scope_kind": "material_continuity",
            "continuity_group_id": review_unit.get("continuity_group_id"),
            "continuity_subject_ids": subject_ids,
            "member_decisions": saved_members,
        },
        "effective_action": action,
        "allocated_stable_slot_id": None,
        "semantic_decision_digest": semantic_digest,
        "recompute_deferred": True,
        "persistence": {
            "status": "saved",
            "downstream_recompute_triggered": False,
        },
    }


def _validate_new_player_active_cap(
    match_path: Path,
    candidate_document: dict[str, Any],
    prepared: dict[str, Any],
    subject_id: str,
    *,
    use_materialized_context: bool,
) -> None:
    if use_materialized_context and validate_new_player_active_cap_from_progress(
        match_path,
        prepared,
        subject_id,
    ):
        return
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


def _safe_subject_canonical_slot(
    match_path: Path,
    candidate_document: dict[str, Any],
    subject_id: str,
) -> str | None:
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
        load_reviewed_slot_assignments(match_path),
    )
    slots = {
        str(row["stable_anonymous_slot_id"])
        for row in resolved.values()
        if row.get("candidate_subject_id") == subject_id
        and row.get("stable_anonymous_slot_id")
        and not row.get("hard_blockers")
    }
    return next(iter(slots)) if len(slots) == 1 else None


def _materialized_subject_canonical_slot(
    candidate_document: dict[str, Any],
    subject_id: str,
) -> str | None:
    rows = [
        row
        for row in candidate_document.get("subjects") or []
        if str(row.get("candidate_subject_id") or "") == subject_id
    ]
    if not rows:
        return None
    slots = {
        slot_id
        for row in rows
        for value in (
            list(row.get("production_player_ids") or [])
            + list(row.get("production_subject_ids") or [])
        )
        if (slot_id := normalize_reviewed_slot_id(value)) is not None
    }
    teams = {
        str(row.get("team_label") or "U").upper()
        for row in rows
        if str(row.get("team_label") or "U").upper() in {"A", "B"}
    }
    if len(slots) != 1 or len(teams) > 1:
        return None
    slot_id = next(iter(slots))
    return slot_id if not teams or slot_id[0] in teams else None
