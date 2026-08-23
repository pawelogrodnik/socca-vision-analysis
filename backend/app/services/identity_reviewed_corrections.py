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
    FILENAME as RECOMPUTE_FILENAME,
    mark_reviewed_identity_recompute_required,
)
from app.services.identity_reviewed_segments import (
    DECISIONS_FILENAME as SEGMENT_DECISIONS_FILENAME,
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
from app.services.identity_reviewed_mixed_store import (
    FILENAME as MIXED_PLAYERS_FILENAME,
    inline_temporal_split_for_source,
    load_mixed_player_cases,
    save_mixed_case_document,
    save_mixed_player_classification,
    staged_mixed_case_for_source,
)
from app.services.identity_reviewed_review_source import (
    ReviewedIdentityReviewSourceError,
    resolve_review_source,
    source_case_id,
    source_storage_payload,
)
from app.services.identity_reviewed_material_continuity import (
    DECISIONS_FILENAME as MATERIAL_CONTINUITY_DECISIONS_FILENAME,
    save_material_continuity_decision,
)
from app.services.identity_reviewed_response_shaping import (
    correction_response_decision,
)
from app.services.identity_reviewed_slot_cleanup import (
    cleanup_unreferenced_manual_reviewed_slots,
)
from app.services.identity_roster_subject_review_store import (
    REVIEW_DECISIONS_FILENAME,
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
        authorized_review_unit = review_unit_for_payload(progress_before, payload)
        if (
            isinstance(authorized_review_unit, dict)
            and authorized_review_unit.get("scope_kind") == "material_continuity"
        ):
            internal_progress = build_reviewed_identity_progress(
                match_path,
                match_doc,
                include_internal_units=True,
            )
            authorized_review_unit = review_unit_for_payload(internal_progress, payload)
        result = persist_reviewed_identity_correction(
            match_path,
            match_doc,
            payload,
            use_materialized_context=False,
            authorized_review_unit=authorized_review_unit,
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
    """Persist a direct correction, retiring an exact saved split if present.

    A parent source has one active operator intent: either its direct decision
    or a durable inline split plus child decisions.  The transaction below is
    deliberately scoped by the full source key/digest, never presentation
    ranges, so neighboring or unrelated splits cannot be touched.
    """
    action = str(payload.get("action") or "").strip()
    if action == "mixed_players":
        result = _persist_reviewed_identity_correction(
            match_path,
            match_doc,
            payload,
            use_materialized_context=use_materialized_context,
            trusted_materialized_detected_team_labels=trusted_materialized_detected_team_labels,
            authorized_review_unit=authorized_review_unit,
        )
        return {**result, "review_topology_changed": True}
    if (
        isinstance(authorized_review_unit, dict)
        and authorized_review_unit.get("scope_kind") == "material_continuity"
        and not _has_potential_inline_split(match_path, payload)
    ):
        # The regular material save will perform its own authoritative stale
        # check.  Avoid resolving the parent solely to learn that no split
        # exists to supersede.
        result = _persist_reviewed_identity_correction(
            match_path,
            match_doc,
            payload,
            use_materialized_context=use_materialized_context,
            trusted_materialized_detected_team_labels=trusted_materialized_detected_team_labels,
            authorized_review_unit=authorized_review_unit,
        )
        return {**result, "review_topology_changed": action == "create_new_stable_player"}
    if (
        isinstance(authorized_review_unit, dict)
        and authorized_review_unit.get("_hot_state_authorized") is True
        and not _has_potential_inline_split(match_path, payload)
    ):
        # A normal whole-subject or canonical-segment save has already been
        # validated against the exact versioned source in the hot state. Do
        # not rescan raw observations merely to prove that there is no inline
        # split to retire.
        result = _persist_reviewed_identity_correction(
            match_path,
            match_doc,
            payload,
            use_materialized_context=use_materialized_context,
            trusted_materialized_detected_team_labels=trusted_materialized_detected_team_labels,
            authorized_review_unit=authorized_review_unit,
        )
        return {**result, "review_topology_changed": action == "create_new_stable_player"}
    try:
        source = _direct_correction_source(match_path, match_doc, payload)
    except ReviewedIdentityReviewSourceError:
        # Preserve the existing, scope-specific stale-target validation and
        # error contract in the direct persistence path.
        result = _persist_reviewed_identity_correction(
            match_path,
            match_doc,
            payload,
            use_materialized_context=use_materialized_context,
            trusted_materialized_detected_team_labels=trusted_materialized_detected_team_labels,
            authorized_review_unit=authorized_review_unit,
        )
        return {**result, "review_topology_changed": action == "create_new_stable_player"}
    saved_split = (
        inline_temporal_split_for_source(match_path, source)
        or staged_mixed_case_for_source(match_path, source)
        if source
        else None
    )
    if saved_split is None:
        result = _persist_reviewed_identity_correction(
            match_path,
            match_doc,
            payload,
            use_materialized_context=use_materialized_context,
            trusted_materialized_detected_team_labels=trusted_materialized_detected_team_labels,
            authorized_review_unit=authorized_review_unit,
        )
        return {**result, "review_topology_changed": action == "create_new_stable_player"}

    rollback_paths = _direct_correction_rollback_paths(match_path)
    try:
        result = _persist_reviewed_identity_correction(
            match_path,
            match_doc,
            payload,
            use_materialized_context=use_materialized_context,
            trusted_materialized_detected_team_labels=trusted_materialized_detected_team_labels,
            authorized_review_unit=authorized_review_unit,
        )
        _retire_exact_inline_split(match_path, source)
        # Persisted target status must reflect retired children before the next
        # workflow read.  The raw detector/tracker files are never touched.
        build_segment_review_document(match_path, match_doc)
        semantic_digest = reviewed_decisions_semantic_digest(match_path)
        mark_reviewed_identity_recompute_required(
            match_path,
            semantic_decision_digest=semantic_digest,
        )
        return {
            **result,
            "semantic_decision_digest": semantic_digest,
            # Retiring a parent split can remove child targets and orphaned
            # manual slots, so an incremental card mutation is unsafe.
            "review_topology_changed": True,
        }
    except Exception:
        _restore_paths(rollback_paths)
        raise


def _persist_reviewed_identity_correction(
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
    if action == "mixed_players":
        has_exact_source = (
            isinstance(authorized_review_unit, dict)
            and bool(authorized_review_unit.get("source_ownership_digest"))
        ) or any(
            payload.get(key)
            for key in ("review_target_id", "continuity_group_id", "source_ownership_digest")
        )
        source = resolve_review_source(
            match_path,
            match_doc,
            candidate_subject_id=subject_id,
            review_target_id=review_target_id,
            continuity_group_id=str(payload.get("continuity_group_id") or "").strip() or None,
            source_ownership_digest=str(payload.get("source_ownership_digest") or "") or None,
            materialized_review_unit=authorized_review_unit,
        ) if has_exact_source else None
        saved = save_mixed_player_classification(
            match_path,
            match_doc,
            subject_id,
            payload.get("mixed_hint"),
            payload.get("comment"),
            source=source,
            case_id=source_case_id(source) if source else None,
            source_payload=source_storage_payload(source) if source else None,
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
            and str(row.get("target_origin") or "") != "operator_temporal_split"
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
                allow_detected_team_override=corrects_detected_team,
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
            # Team-only is an explicit operator correction, just like a named
            # cross-team roster choice. Source detection must not lock it.
            corrects_detected_team = bool(
                action == "assign_team"
                and str(context["team_label"]).upper() in {"A", "B"}
                and str(update.get("team_label") or "").upper()
                != str(context["team_label"]).upper()
            )
            prepared = prepare_reviewed_slot_assignments(
                match_path,
                candidate_document,
                [update],
                use_materialized_candidate_context=use_exact_materialized_context,
                materialized_detected_team_labels=(
                    detected_team_labels if use_exact_materialized_context else None
                ),
                allow_detected_team_override=corrects_detected_team,
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
    """Persist only the exact owned pairs of the reviewed continuity case."""
    action = str(payload.get("action") or "").strip()
    saved = save_material_continuity_decision(
        match_path,
        match_doc,
        payload,
        review_unit,
    )
    semantic_digest = reviewed_decisions_semantic_digest(match_path)
    mark_reviewed_identity_recompute_required(
        match_path,
        semantic_decision_digest=semantic_digest,
    )
    return {
        "saved_decision": correction_response_decision({
            "scope_kind": "material_continuity",
            "continuity_group_id": saved.get("continuity_group_id"),
            "continuity_subject_ids": saved.get("continuity_subject_ids"),
            "owned_observations": saved.get("owned_observations"),
            "decision": saved,
        }),
        "effective_action": action,
        "allocated_stable_slot_id": None,
        "semantic_decision_digest": semantic_digest,
        "recompute_deferred": True,
        "persistence": {
            "status": "saved",
            "downstream_recompute_triggered": False,
        },
    }


def _direct_correction_source(
    match_path: Path,
    match_doc: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Resolve the exact parent only when a saved split could be superseded."""
    subject_id = str(payload.get("candidate_subject_id") or "").strip()
    if not subject_id:
        return None
    return resolve_review_source(
        match_path,
        match_doc,
        candidate_subject_id=subject_id,
        review_target_id=str(payload.get("review_target_id") or "").strip() or None,
        continuity_group_id=str(payload.get("continuity_group_id") or "").strip() or None,
        source_ownership_digest=str(payload.get("source_ownership_digest") or "") or None,
    )


def _has_potential_inline_split(match_path: Path, payload: dict[str, Any]) -> bool:
    """Cheap non-authoritative gate before resolving a material parent.

    Browser data can only make this return true; deletion still requires the
    authoritative exact source and digest resolution below.
    """
    wanted = {
        "candidate_subject_id": str(payload.get("candidate_subject_id") or ""),
        "review_target_id": str(payload.get("review_target_id") or "") or None,
        "continuity_group_id": str(payload.get("continuity_group_id") or "") or None,
        "source_ownership_digest": str(payload.get("source_ownership_digest") or "") or None,
    }
    return any(
        str(case.get("original_issue") or "") in {"inline_temporal_split", "mixed_players"}
        and isinstance(case.get("source"), dict)
        and all(
            value is None or case["source"].get(key) == value
            for key, value in wanted.items()
        )
        for case in load_mixed_player_cases(match_path).get("cases") or []
    )


def _direct_correction_rollback_paths(match_path: Path) -> dict[Path, bytes | None]:
    """Snapshot every reviewed decision artifact a direct save can change."""
    names = (
        MIXED_PLAYERS_FILENAME,
        SEGMENT_DECISIONS_FILENAME,
        "reviewed_identity_segment_review.json",
        SLOT_REVIEW_FILENAME,
        MATERIAL_CONTINUITY_DECISIONS_FILENAME,
        REVIEW_DECISIONS_FILENAME,
        RECOMPUTE_FILENAME,
    )
    return {
        match_path / name: (match_path / name).read_bytes()
        if (match_path / name).exists()
        else None
        for name in names
    }


def _restore_paths(paths: dict[Path, bytes | None]) -> None:
    for path, previous in paths.items():
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(previous)


def _retire_exact_inline_split(match_path: Path, source: dict[str, Any]) -> None:
    """Retire only the child state belonging to this exact direct parent."""
    document = load_mixed_player_cases(match_path)
    case_id = source_case_id(source)
    matching = next(
        (
            row
            for row in document.get("cases") or []
            if isinstance(row, dict) and str(row.get("case_id") or "") == case_id
        ),
        None,
    )
    if matching is None:
        return
    # Defend against a manually forged/corrupt case id: the complete source
    # tuple is the authority for deletion, not a frame range or subject alone.
    stored_source = matching.get("source")
    source_keys = (
        "scope_kind",
        "candidate_subject_id",
        "review_target_id",
        "continuity_group_id",
        "source_ownership_digest",
    )
    if not isinstance(stored_source, dict) or any(
        stored_source.get(key) != source.get(key) for key in source_keys
    ):
        raise ValueError("inline_temporal_split_source_conflict")
    target_ids = {
        str(value)
        for value in matching.get("segment_target_ids") or []
        if str(value)
    }
    if target_ids:
        from app.services.identity_reviewed_mixed_resolution import _remove_superseded_segment_decisions

        removed = _remove_superseded_segment_decisions(match_path, target_ids)
    else:
        removed = []
    retained = [
        row
        for row in document.get("cases") or []
        if not (isinstance(row, dict) and str(row.get("case_id") or "") == case_id)
    ]
    save_mixed_case_document(match_path, {**document, "cases": retained})
    cleanup_unreferenced_manual_reviewed_slots(match_path, removed)


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
