from __future__ import annotations

"""Validated whole-subject corrections over the existing reviewed stores."""

import logging
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
    load_reviewed_slot_assignments,
    prepare_reviewed_slot_assignments,
)
from app.services.identity_reviewed_segments import (
    SegmentTargetError,
    build_segment_review_document,
    save_segment_decision,
)
from app.services.identity_reviewed_snapshot import get_reviewed_identity_status
from app.services.identity_reviewed_progress import (
    build_reviewed_identity_progress,
    decision_impact,
)
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
    }
)
logger = logging.getLogger(__name__)


def save_reviewed_identity_correction(
    match_path: Path,
    match_doc: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    subject_id = str(payload.get("candidate_subject_id") or "").strip()
    action = str(payload.get("action") or "").strip()
    try:
        if action not in CORRECTION_ACTIONS:
            raise ValueError(f"Unsupported reviewed identity correction action: {action}")
        progress_before = build_reviewed_identity_progress(match_path, match_doc)
        review_target_id = str(payload.get("review_target_id") or "").strip() or None
        if review_target_id:
            saved = save_segment_decision(match_path, match_doc, payload)
            build_segment_review_document(match_path, match_doc)
            snapshot = get_reviewed_identity_status(match_path)
            progress_after = build_reviewed_identity_progress(match_path, match_doc)
            impact = decision_impact(
                progress_before,
                progress_after,
                subject_id,
                review_target_id,
            )
            return {
                "saved_decision": saved,
                "effective_action": action,
                "allocated_stable_slot_id": None,
                "snapshot": {
                    "status": snapshot.get("status"),
                    "stale": bool(
                        snapshot.get("stale") or snapshot.get("status") == "stale"
                    ),
                },
                "semantic_decision_digest": reviewed_decisions_semantic_digest(
                    match_path
                ),
                "review_progress": progress_after,
                "decision_impact": impact,
            }
        segment_review = build_segment_review_document(match_path, match_doc)
        if any(
            str(row.get("candidate_subject_id") or "") == subject_id
            for row in segment_review.get("targets") or []
        ):
            raise SegmentTargetError("review_target_required")
        context = build_subject_context(match_path, subject_id)
        card_key = review_card_key(match_path, subject_id)
        comment = str(payload.get("comment") or "").strip() or None

        if action == "assign_roster_player":
            player_id = str(payload.get("player_id") or "").strip()
            player = next(
            (row for row in match_roster(match_doc) if row["player_id"] == player_id),
            None,
            )
            if player is None:
                raise ValueError(f"Invalid player_id: {player_id or '<missing>'}")
            source_team_label = str(context["team_label"])
            if source_team_label not in {"U", player["team_label"]}:
                raise ValueError(
                    f"Cross-team roster assignment is not allowed: subject {subject_id} is "
                    f"team {context['team_label']}, player is team {player['team_label']}"
                )
            candidate_document = load_required(
                match_path / "identity_candidate_shadow.json"
            )
            stable_slot_id = _safe_subject_canonical_slot(
                match_path,
                candidate_document,
                subject_id,
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
                        "stable_slot_id": stable_slot_id,
                        "comment": comment,
                    }
                ],
            )
            if card_key:
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
            # Persist the slot overlay only after the same operator choice has
            # passed the review-card validation.  This prevents a rejected UI
            # correction from leaving a partial slot decision behind.
            write_identity_json_atomic(match_path / SLOT_REVIEW_FILENAME, prepared)
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
            if action in {"assign_team", "create_new_stable_player"}:
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
        progress_after = build_reviewed_identity_progress(match_path, match_doc)
        impact = decision_impact(progress_before, progress_after, subject_id)
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
        "saved_decision": saved,
        "effective_action": action,
        "allocated_stable_slot_id": allocated_slot,
        "snapshot": {
            "status": snapshot.get("status"),
            "stale": bool(snapshot.get("stale") or snapshot.get("status") == "stale"),
        },
            "semantic_decision_digest": reviewed_decisions_semantic_digest(match_path),
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
