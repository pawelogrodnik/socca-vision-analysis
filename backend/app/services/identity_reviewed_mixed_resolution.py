from __future__ import annotations

"""Deferred resolution of one operator-classified mixed-player subject."""

from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any

from app.services.identity_reviewed_correction_context import reviewed_decisions_semantic_digest
from app.services.identity_reviewed_action_scope import (
    validate_review_unit_action_scope,
)
from app.services.identity_reviewed_mixed_store import (
    UNRESOLVED_STATUSES,
    current_mixed_subject_digest,
    load_mixed_player_cases,
    observations_for_case,
    operator_mixed_targets,
    save_mixed_case_document,
    validate_split_frames,
)
from app.services.identity_reviewed_mixed_topology import require_simple_temporal_split
from app.services.identity_reviewed_review_source import (
    ReviewedIdentityReviewSourceError,
    resolve_review_source,
    source_case_id,
    source_storage_payload,
)
from app.services.identity_reviewed_recompute_state import mark_reviewed_identity_recompute_required
from app.services.identity_reviewed_segments import (
    DECISIONS_FILENAME,
    build_segment_review_document,
    load_segment_decisions,
    project_segment_decisions_onto_materialized_review,
    save_segment_decisions_batch,
)
from app.services.identity_initial_audit_store import write_identity_json_atomic
from app.services.identity_reviewed_slot_review import FILENAME as SLOT_REVIEW_FILENAME
from app.services.identity_reviewed_slot_cleanup import (
    cleanup_unreferenced_manual_reviewed_slots,
)


def save_mixed_player_resolution(
    match_path: Path,
    match_doc: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    started = time.perf_counter()
    performance = _mixed_split_performance()
    subject_id = str(payload.get("candidate_subject_id") or "").strip()
    phase_started = time.perf_counter()
    document = load_mixed_player_cases(match_path)
    cases = {
        str(row.get("case_id") or row.get("candidate_subject_id")): dict(row)
        for row in document.get("cases") or []
        if row.get("candidate_subject_id")
    }
    case_id = str(payload.get("case_id") or subject_id)
    case = cases.get(case_id)
    performance["mixed_case_load_ms"] = _elapsed_ms(phase_started)
    if case is None:
        raise ValueError(f"Unknown mixed-player case: {subject_id or '<missing>'}")
    supplied_digest = str(payload.get("source_subject_digest") or "")
    if supplied_digest != str(case.get("source_subject_digest") or ""):
        raise MixedPlayerTargetError("mixed_player_case_stale")
    if str(case.get("resolution_status")) not in UNRESOLVED_STATUSES:
        raise ValueError("Mixed-player case is already resolved")
    if isinstance(case.get("source"), dict):
        # Modern staged markers use the exact inline split engine.  It
        # re-resolves ownership and rejects a stale source before persistence.
        source = dict(case["source"])
        if supplied_digest != str(source.get("source_ownership_digest") or ""):
            raise MixedPlayerTargetError("mixed_player_case_stale")
        return save_inline_temporal_split(
            match_path,
            match_doc,
            {
                **payload,
                "candidate_subject_id": source.get("candidate_subject_id"),
                "review_target_id": source.get("review_target_id"),
                "continuity_group_id": source.get("continuity_group_id"),
                "source_ownership_digest": source.get("source_ownership_digest"),
            },
        )
    phase_started = time.perf_counter()
    current_digest = current_mixed_subject_digest(match_path, subject_id)
    performance["source_resolution_ms"] = _elapsed_ms(phase_started)
    if supplied_digest != current_digest:
        raise MixedPlayerTargetError("mixed_player_case_stale")

    resolution = str(payload.get("resolution") or "split")
    now = datetime.now(timezone.utc).isoformat()
    if resolution == "unresolved_complex_mix":
        case.update(
            {
                "resolution_status": "unresolved_complex_mix",
                "resolution_reason": "no_simple_temporal_split",
                "updated_at": now,
                "comment": str(payload.get("comment") or "").strip() or case.get("comment"),
            }
        )
        cases[case_id] = case
        phase_started = time.perf_counter()
        save_mixed_case_document(match_path, {**document, "cases": list(cases.values())})
        performance["mixed_case_persistence_ms"] = _elapsed_ms(phase_started)
        # The classification changes which operator targets are materialized,
        # so this branch still needs one canonical topology refresh.
        phase_started = time.perf_counter()
        build_segment_review_document(match_path, match_doc)
        performance["segment_review_build_ms"] = _elapsed_ms(phase_started)
        phase_started = time.perf_counter()
        digest = reviewed_decisions_semantic_digest(match_path)
        performance["semantic_digest_ms"] = _elapsed_ms(phase_started)
        phase_started = time.perf_counter()
        mark_reviewed_identity_recompute_required(match_path, semantic_decision_digest=digest)
        performance["recompute_marker_ms"] = _elapsed_ms(phase_started)
        performance["total_ms"] = _elapsed_ms(started)
        return {**_response(case, digest), "performance": performance}
    if resolution != "split":
        raise ValueError(f"Unsupported mixed resolution: {resolution}")

    phase_started = time.perf_counter()
    observations = observations_for_case(match_path, case)
    require_simple_temporal_split(observations)
    split_frames = sorted({int(value) for value in payload.get("split_after_frames") or []})
    validate_split_frames(observations, split_frames)
    performance["split_validation_ms"] = _elapsed_ms(phase_started)
    case.update(
        {
            "resolution_status": "unresolved",
            "split_after_frames": split_frames,
            "updated_at": now,
        }
    )
    pending_document = {**document, "cases": [case if key == case_id else row for key, row in cases.items()]}
    phase_started = time.perf_counter()
    targets = [
        row
        for row in operator_mixed_targets(match_path, pending_document)
        if str(row.get("candidate_subject_id") or "") == subject_id
    ]
    assignments = payload.get("segment_assignments") or []
    if len(assignments) != len(targets):
        raise ValueError("Every mixed segment requires one assignment")
    performance["target_derivation_ms"] = _elapsed_ms(phase_started)

    rollback_paths = {
        path: path.read_bytes() if path.exists() else None
        for path in (
            match_path / "reviewed_identity_mixed_players.json",
            match_path / "reviewed_identity_segment_review.json",
            match_path / DECISIONS_FILENAME,
            match_path / SLOT_REVIEW_FILENAME,
        )
    }
    try:
        phase_started = time.perf_counter()
        save_mixed_case_document(match_path, pending_document)
        performance["mixed_case_persistence_ms"] += _elapsed_ms(phase_started)
        phase_started = time.perf_counter()
        review = build_segment_review_document(match_path, match_doc)
        performance["segment_review_build_ms"] = _elapsed_ms(phase_started)
        phase_started = time.perf_counter()
        saved = save_segment_decisions_batch(
            match_path,
            match_doc,
            [
                {
                    **dict(assignment),
                    "review_target_id": target["review_target_id"],
                    "source_ownership_digest": target["source_ownership_digest"],
                }
                for target, assignment in zip(targets, assignments, strict=True)
            ],
            materialized_review=review,
            performance=performance,
        )
        performance["segment_decision_batch_ms"] = _elapsed_ms(phase_started)
        case.update(
            {
                "resolution_status": "resolved",
                "resolved_at": now,
                "segment_count": len(targets),
                "segment_target_ids": [row["review_target_id"] for row in targets],
                "comment": str(payload.get("comment") or "").strip() or case.get("comment"),
            }
        )
        cases[case_id] = case
        phase_started = time.perf_counter()
        save_mixed_case_document(match_path, {**document, "cases": list(cases.values())})
        performance["mixed_case_persistence_ms"] += _elapsed_ms(phase_started)
        phase_started = time.perf_counter()
        project_segment_decisions_onto_materialized_review(match_path, review)
        performance["segment_review_projection_ms"] = _elapsed_ms(phase_started)
    except Exception:
        for path, previous in rollback_paths.items():
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(previous)
        raise

    phase_started = time.perf_counter()
    digest = reviewed_decisions_semantic_digest(match_path)
    performance["semantic_digest_ms"] = _elapsed_ms(phase_started)
    phase_started = time.perf_counter()
    mark_reviewed_identity_recompute_required(match_path, semantic_decision_digest=digest)
    performance["recompute_marker_ms"] = _elapsed_ms(phase_started)
    performance["total_ms"] = _elapsed_ms(started)
    return {
        **_response(case, digest),
        "saved_segment_decisions": saved,
        "performance": performance,
    }


def _response(case: dict[str, Any], digest: str) -> dict[str, Any]:
    return {
        "saved_case": case,
        "semantic_decision_digest": digest,
        "recompute_deferred": True,
        "persistence": {"status": "saved", "downstream_recompute_triggered": False},
    }


class MixedPlayerTargetError(ValueError):
    pass


def _mixed_split_performance() -> dict[str, float]:
    return {
        "source_resolution_ms": 0.0,
        "mixed_case_load_ms": 0.0,
        "split_validation_ms": 0.0,
        "target_derivation_ms": 0.0,
        "segment_review_build_ms": 0.0,
        "segment_decision_batch_ms": 0.0,
        "segment_assignment_validation_ms": 0.0,
        "segment_decision_persistence_ms": 0.0,
        "reviewed_slot_persistence_ms": 0.0,
        "mixed_case_persistence_ms": 0.0,
        "superseded_decision_cleanup_ms": 0.0,
        "slot_cleanup_ms": 0.0,
        "segment_review_projection_ms": 0.0,
        "semantic_digest_ms": 0.0,
        "recompute_marker_ms": 0.0,
    }


def save_inline_temporal_split(
    match_path: Path,
    match_doc: dict[str, Any],
    payload: dict[str, Any],
    *,
    materialized_review_unit: dict[str, Any] | None = None,
    resolved_source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Atomically create/update an exact-source temporal split from a card.

    This is intentionally stored in the existing mixed-player artifact. V2
    entries add a generic source object while V1 markers remain readable by
    the compatibility endpoints above.
    """
    started = time.perf_counter()
    performance = _mixed_split_performance()
    subject_id = str(payload.get("candidate_subject_id") or "").strip()
    phase_started = time.perf_counter()
    source = resolved_source or resolve_review_source(
        match_path,
        match_doc,
        candidate_subject_id=subject_id,
        review_target_id=str(payload.get("review_target_id") or "").strip() or None,
        continuity_group_id=str(payload.get("continuity_group_id") or "").strip() or None,
        source_ownership_digest=str(payload.get("source_ownership_digest") or ""),
        materialized_review_unit=materialized_review_unit,
    )
    performance["source_resolution_ms"] = _elapsed_ms(phase_started)
    resolution = str(payload.get("resolution") or "split")
    phase_started = time.perf_counter()
    document = load_mixed_player_cases(match_path)
    case_id = source_case_id(source)
    cases = [dict(row) for row in document.get("cases") or [] if isinstance(row, dict)]
    existing = next((row for row in cases if str(row.get("case_id") or "") == case_id), None)
    performance["mixed_case_load_ms"] = _elapsed_ms(phase_started)
    now = datetime.now(timezone.utc).isoformat()

    if resolution == "unresolved_complex_mix":
        old_target_ids = {
            str(target_id) for target_id in (existing or {}).get("segment_target_ids") or []
            if str(target_id)
        }
        if (
            existing
            and str(existing.get("resolution_status") or "") == "resolved"
            and str(payload.get("existing_split_semantic_digest") or "")
            != str(existing.get("split_semantic_digest") or "")
        ):
            raise MixedPlayerTargetError("temporal_split_conflict")
        case = {
            **(existing or {}),
            "case_id": case_id,
            "candidate_subject_id": subject_id,
            "original_issue": "inline_temporal_split",
            "source": _source_payload(source),
            "source_subject_digest": source["source_ownership_digest"],
            "resolution_status": "unresolved_complex_mix",
            "resolution_reason": "no_simple_temporal_split",
            "frame_start": source["frame_start"],
            "frame_end": source["frame_end"],
            "observation_count": source["detected_observation_count"],
            "updated_at": now,
            "comment": str(payload.get("comment") or "").strip() or None,
            "split_after_frames": [],
            "segment_target_ids": [],
            "segment_assignments": [],
        }
        rollback_paths = {
            path: path.read_bytes() if path.exists() else None
            for path in (
                match_path / "reviewed_identity_mixed_players.json",
                match_path / "reviewed_identity_segment_review.json",
                match_path / DECISIONS_FILENAME,
                match_path / SLOT_REVIEW_FILENAME,
            )
        }
        try:
            phase_started = time.perf_counter()
            _replace_case(match_path, document, cases, case)
            performance["mixed_case_persistence_ms"] = _elapsed_ms(phase_started)
            if old_target_ids:
                phase_started = time.perf_counter()
                removed = _remove_superseded_segment_decisions(match_path, old_target_ids)
                performance["superseded_decision_cleanup_ms"] = _elapsed_ms(phase_started)
                phase_started = time.perf_counter()
                cleanup_unreferenced_manual_reviewed_slots(match_path, removed)
                performance["slot_cleanup_ms"] = _elapsed_ms(phase_started)
            # A resolved split has persisted child targets. Once it becomes a
            # complex blocker, refresh the review snapshot in the same atomic
            # operation so those now-retired targets cannot remain displayed.
            phase_started = time.perf_counter()
            build_segment_review_document(match_path, match_doc)
            performance["segment_review_build_ms"] = _elapsed_ms(phase_started)
        except Exception:
            for path, previous in rollback_paths.items():
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(previous)
            raise
        phase_started = time.perf_counter()
        digest = reviewed_decisions_semantic_digest(match_path)
        performance["semantic_digest_ms"] = _elapsed_ms(phase_started)
        phase_started = time.perf_counter()
        mark_reviewed_identity_recompute_required(match_path, semantic_decision_digest=digest)
        performance["recompute_marker_ms"] = _elapsed_ms(phase_started)
        performance["total_ms"] = _elapsed_ms(started)
        return {**_response(case, digest), "complex_mix": True, "performance": performance}
    if resolution != "split":
        raise ValueError("Unsupported temporal split resolution")

    observations = list(source["observations"])
    phase_started = time.perf_counter()
    require_simple_temporal_split(observations)
    split_frames = sorted({int(value) for value in payload.get("split_after_frames") or []})
    validate_split_frames(observations, split_frames)
    assignments = payload.get("segment_assignments") or []
    semantic = _split_semantic_digest(split_frames, assignments)
    performance["split_validation_ms"] = _elapsed_ms(phase_started)
    old_target_ids: set[str] = set()
    if existing and str(existing.get("resolution_status") or "") == "resolved":
        if str(existing.get("split_semantic_digest") or "") == semantic:
            phase_started = time.perf_counter()
            digest = reviewed_decisions_semantic_digest(match_path)
            performance["semantic_digest_ms"] = _elapsed_ms(phase_started)
            performance["total_ms"] = _elapsed_ms(started)
            return {**_response(existing, digest), "idempotent": True, "performance": performance}
        supplied_existing_digest = str(payload.get("existing_split_semantic_digest") or "")
        if supplied_existing_digest != str(existing.get("split_semantic_digest") or ""):
            raise MixedPlayerTargetError("temporal_split_conflict")
        old_target_ids = {
            str(target_id) for target_id in existing.get("segment_target_ids") or []
            if str(target_id)
        }

    case = {
        **(existing or {}),
        "case_id": case_id,
        "candidate_subject_id": subject_id,
        "original_issue": "inline_temporal_split",
        "source": _source_payload(source),
        "source_subject_digest": source["source_ownership_digest"],
        "resolution_status": "unresolved",
        "frame_start": source["frame_start"],
        "frame_end": source["frame_end"],
        "observation_count": source["detected_observation_count"],
        "split_after_frames": split_frames,
        "segment_target_ids": [],
        "segment_assignments": _normalized_assignments(assignments),
        "updated_at": now,
    }
    pending_cases = [row for row in cases if str(row.get("case_id") or "") != case_id] + [case]
    pending_document = {**document, "cases": pending_cases}
    phase_started = time.perf_counter()
    targets = [
        row for row in operator_mixed_targets(match_path, pending_document)
        if str(row.get("split_parent_case_id") or "") == case_id
    ]
    if len(assignments) != len(targets):
        raise ValueError("Every split segment requires one assignment")
    performance["target_derivation_ms"] = _elapsed_ms(phase_started)
    # A child inherits the decision vocabulary of the parent source. This is
    # the mutation-side counterpart of the capability map sent to the inline
    # editor; a forged payload cannot restore an advanced action that the
    # material-continuity scope deliberately hides.
    source_scope = {
        "scope_kind": source["scope_kind"],
        "detected_observation_count": source["detected_observation_count"],
    }
    for assignment in assignments:
        if not isinstance(assignment, dict):
            raise ValueError("Every split segment requires one assignment")
        validate_review_unit_action_scope(assignment, source_scope)

    rollback_paths = {
        path: path.read_bytes() if path.exists() else None
        for path in (
            match_path / "reviewed_identity_mixed_players.json",
            match_path / "reviewed_identity_segment_review.json",
            match_path / DECISIONS_FILENAME,
            match_path / SLOT_REVIEW_FILENAME,
        )
    }
    try:
        phase_started = time.perf_counter()
        save_mixed_case_document(match_path, pending_document)
        performance["mixed_case_persistence_ms"] += _elapsed_ms(phase_started)
        phase_started = time.perf_counter()
        removed = (
            _remove_superseded_segment_decisions(match_path, old_target_ids)
            if old_target_ids
            else []
        )
        performance["superseded_decision_cleanup_ms"] = _elapsed_ms(phase_started)
        phase_started = time.perf_counter()
        review = build_segment_review_document(match_path, match_doc)
        performance["segment_review_build_ms"] = _elapsed_ms(phase_started)
        phase_started = time.perf_counter()
        saved = save_segment_decisions_batch(
            match_path,
            match_doc,
            [
                {
                    **dict(assignment),
                    "review_target_id": target["review_target_id"],
                    "source_ownership_digest": target["source_ownership_digest"],
                }
                for target, assignment in zip(targets, assignments, strict=True)
            ],
            materialized_review=review,
            performance=performance,
        )
        performance["segment_decision_batch_ms"] = _elapsed_ms(phase_started)
        case.update(
            {
                "resolution_status": "resolved",
                "resolved_at": now,
                "segment_count": len(targets),
                "segment_target_ids": [target["review_target_id"] for target in targets],
                "split_semantic_digest": semantic,
                "segment_assignments": _normalized_assignments(assignments),
                "comment": str(payload.get("comment") or "").strip() or case.get("comment"),
            }
        )
        phase_started = time.perf_counter()
        _replace_case(match_path, document, pending_cases, case)
        performance["mixed_case_persistence_ms"] += _elapsed_ms(phase_started)
        phase_started = time.perf_counter()
        cleanup_unreferenced_manual_reviewed_slots(match_path, removed)
        performance["slot_cleanup_ms"] = _elapsed_ms(phase_started)
        # Topology/ownership came from the canonical build above. Decisions do
        # not alter it, so project only decision-derived fields instead of
        # parsing and rebuilding the complete segment graph a second time.
        phase_started = time.perf_counter()
        project_segment_decisions_onto_materialized_review(match_path, review)
        performance["segment_review_projection_ms"] = _elapsed_ms(phase_started)
    except Exception:
        for path, previous in rollback_paths.items():
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(previous)
        raise
    phase_started = time.perf_counter()
    digest = reviewed_decisions_semantic_digest(match_path)
    performance["semantic_digest_ms"] = _elapsed_ms(phase_started)
    phase_started = time.perf_counter()
    mark_reviewed_identity_recompute_required(match_path, semantic_decision_digest=digest)
    performance["recompute_marker_ms"] = _elapsed_ms(phase_started)
    performance["total_ms"] = _elapsed_ms(started)
    return {
        **_response(case, digest),
        "saved_segment_decisions": saved,
        "performance": performance,
    }


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def _source_payload(source: dict[str, Any]) -> dict[str, Any]:
    return source_storage_payload(source)


def _replace_case(
    match_path: Path,
    document: dict[str, Any],
    cases: list[dict[str, Any]],
    case: dict[str, Any],
) -> None:
    case_id = str(case.get("case_id") or "")
    rows = [row for row in cases if str(row.get("case_id") or "") != case_id]
    rows.append(case)
    save_mixed_case_document(match_path, {**document, "cases": rows})


def _split_semantic_digest(boundaries: list[int], assignments: list[Any]) -> str:
    from app.services.identity_jersey_number_common import canonical_digest

    return canonical_digest({
        "split_after_frames": boundaries,
        "segment_assignments": [
            {
                "action": row.get("action"),
                "player_id": row.get("player_id"),
                "stable_slot_id": row.get("stable_slot_id"),
                "team_label": row.get("team_label"),
            }
            for row in assignments if isinstance(row, dict)
        ],
    })


def _normalized_assignments(assignments: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            key: row.get(key)
            for key in ("action", "player_id", "stable_slot_id", "team_label")
            if row.get(key) is not None
        }
        for row in assignments
        if isinstance(row, dict)
    ]


def _remove_superseded_segment_decisions(
    match_path: Path,
    target_ids: set[str],
) -> list[dict[str, Any]]:
    document = load_segment_decisions(match_path)
    removed = [
        dict(row)
        for row in document.get("decisions") or []
        if str(row.get("review_target_id") or "") in target_ids
    ]
    retained = [
        row for row in document.get("decisions") or []
        if str(row.get("review_target_id") or "") not in target_ids
    ]
    write_identity_json_atomic(match_path / DECISIONS_FILENAME, {**document, "decisions": retained})
    return removed
