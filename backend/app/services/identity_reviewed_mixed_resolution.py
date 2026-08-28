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
    inline_temporal_split_for_source,
    operator_concurrent_targets_for_marker,
    load_mixed_player_cases,
    observations_for_case,
    operator_mixed_targets,
    save_mixed_case_document,
    validate_split_frames,
)
from app.services.identity_reviewed_mixed_topology import require_simple_temporal_split
from app.services.identity_reviewed_concurrent_lanes import (
    concurrent_resolution_semantic_digest,
    derive_concurrent_lanes,
    expanded_concurrent_lane_segments,
    validate_concurrent_lane_resolutions,
)
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
    resolution = str(payload.get("resolution") or "split")
    if resolution == "concurrent_lanes":
        stored_source = case.get("source") if isinstance(case.get("source"), dict) else {}
        source = resolve_review_source(
            match_path,
            match_doc,
            candidate_subject_id=str(stored_source.get("candidate_subject_id") or subject_id),
            review_target_id=str(stored_source.get("review_target_id") or "") or None,
            continuity_group_id=str(stored_source.get("continuity_group_id") or "") or None,
            source_ownership_digest=str(
                stored_source.get("source_ownership_digest") or supplied_digest
            ),
        )
        return save_inline_temporal_split(
            match_path,
            match_doc,
            {
                **payload,
                "candidate_subject_id": source["candidate_subject_id"],
                "review_target_id": source.get("review_target_id"),
                "continuity_group_id": source.get("continuity_group_id"),
                "source_ownership_digest": source["source_ownership_digest"],
            },
            resolved_source=source,
            case_id_override=case_id,
        )
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
        build_segment_review_document(match_path, match_doc, performance=performance)
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
        review = build_segment_review_document(match_path, match_doc, performance=performance)
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
        "segment_review_operator_targets_ms": 0.0,
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
    case_id_override: str | None = None,
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
    case_id = str(case_id_override or source_case_id(source))
    cases = [dict(row) for row in document.get("cases") or [] if isinstance(row, dict)]
    existing = next((row for row in cases if str(row.get("case_id") or "") == case_id), None)
    # Older inline splits may have a durable case id which predates the
    # canonical source-derived id.  Reuse that exact id during an explicit
    # repair: lane ids are deliberately scoped to their parent case and a new
    # parent would incorrectly make the correction context stale.
    if existing is None and case_id_override is None:
        existing = inline_temporal_split_for_source(match_path, source)
        if existing is not None:
            case_id = str(existing.get("case_id") or case_id)
    performance["mixed_case_load_ms"] = _elapsed_ms(phase_started)
    now = datetime.now(timezone.utc).isoformat()

    if resolution == "concurrent_lanes":
        return _save_concurrent_lane_resolution(
            match_path,
            match_doc,
            payload,
            source=source,
            document=document,
            cases=cases,
            existing=existing,
            case_id=case_id,
            now=now,
            performance=performance,
            started=started,
        )

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
            build_segment_review_document(match_path, match_doc, performance=performance)
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
        review = build_segment_review_document(match_path, match_doc, performance=performance)
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


def validate_concurrent_lane_resolution_request(
    source: dict[str, Any],
    payload: dict[str, Any],
    *,
    case_id: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Preflight current topology and exact lane ownership without persistence."""
    _topology, lanes = derive_concurrent_lanes(
        str(case_id or source_case_id(source)),
        str(source["source_ownership_digest"]),
        list(source["observations"]),
    )
    resolutions = validate_concurrent_lane_resolutions(
        lanes,
        list(payload.get("lane_resolutions") or []),
    )
    return lanes, resolutions


def _save_concurrent_lane_resolution(
    match_path: Path,
    match_doc: dict[str, Any],
    payload: dict[str, Any],
    *,
    source: dict[str, Any],
    document: dict[str, Any],
    cases: list[dict[str, Any]],
    existing: dict[str, Any] | None,
    case_id: str,
    now: str,
    performance: dict[str, float],
    started: float,
) -> dict[str, Any]:
    phase_started = time.perf_counter()
    lanes, resolutions = validate_concurrent_lane_resolution_request(
        source,
        payload,
        case_id=case_id,
    )
    expanded = expanded_concurrent_lane_segments(lanes, resolutions)
    source_scope = {
        "scope_kind": source["scope_kind"],
        "detected_observation_count": source["detected_observation_count"],
    }
    for row in expanded:
        validate_review_unit_action_scope(row["assignment"], source_scope)
    semantic = concurrent_resolution_semantic_digest(resolutions)
    performance["split_validation_ms"] = _elapsed_ms(phase_started)

    old_target_ids: set[str] = set()
    if existing and str(existing.get("resolution_status") or "") == "resolved":
        existing_semantic = str(
            existing.get("resolution_semantic_digest")
            or existing.get("split_semantic_digest")
            or ""
        )
        if existing_semantic == semantic:
            digest = reviewed_decisions_semantic_digest(match_path)
            performance["total_ms"] = _elapsed_ms(started)
            return {**_response(existing, digest), "idempotent": True, "performance": performance}
        supplied = str(
            payload.get("existing_resolution_semantic_digest")
            or payload.get("existing_split_semantic_digest")
            or ""
        )
        if supplied != existing_semantic:
            raise MixedPlayerTargetError("concurrent_lane_resolution_conflict")
        old_target_ids = {
            str(value) for value in existing.get("segment_target_ids") or [] if str(value)
        }

    case = {
        **(existing or {}),
        "case_id": case_id,
        "candidate_subject_id": source["candidate_subject_id"],
        "original_issue": (
            "inline_temporal_split"
            if not existing or isinstance(existing.get("source"), dict)
            else str(existing.get("original_issue") or "mixed_players")
        ),
        "source": _source_payload(source),
        "source_subject_digest": source["source_ownership_digest"],
        "resolution_status": "unresolved",
        "resolution_model": "concurrent_lanes",
        "frame_start": source["frame_start"],
        "frame_end": source["frame_end"],
        "observation_count": source["detected_observation_count"],
        "split_after_frames": [],
        "segment_assignments": [],
        "segment_target_ids": [],
        "lane_resolutions": _normalized_lane_resolutions(resolutions),
        "resolution_semantic_digest": semantic,
        "updated_at": now,
    }
    pending_cases = [
        row
        for row in cases
        if str(row.get("case_id") or row.get("candidate_subject_id") or "")
        != case_id
    ] + [case]
    pending_document = {**document, "cases": pending_cases}
    phase_started = time.perf_counter()
    # The exact current parent and validated lane resolutions are already in
    # hand. Do not materialize every sibling Mixed marker merely to filter
    # this one parent's deterministic targets.
    targets = operator_concurrent_targets_for_marker(match_path, case)
    if len(targets) != len(expanded):
        raise MixedPlayerTargetError("concurrent_lane_target_stale")
    target_pairs = [
        {
            (str(value["tracklet_id"]), int(value["frame"]))
            for value in target.get("owned_observations") or []
        }
        for target in targets
    ]
    source_pairs = {
        (str(row["tracklet_id"]), int(row["frame"]))
        for row in source["owned_observations"]
    }
    if set().union(*target_pairs) != source_pairs or sum(map(len, target_pairs)) != len(source_pairs):
        raise MixedPlayerTargetError("concurrent_lane_coverage_invalid")
    performance["target_derivation_ms"] = _elapsed_ms(phase_started)

    rollback_paths = _concurrent_rollback_paths(match_path)
    saved: list[dict[str, Any]] = []
    try:
        save_mixed_case_document(match_path, pending_document)
        removed = (
            _remove_superseded_segment_decisions(match_path, old_target_ids)
            if old_target_ids else []
        )
        review = build_segment_review_document(match_path, match_doc, performance=performance)
        saved = save_segment_decisions_batch(
            match_path,
            match_doc,
            [
                {
                    **dict(expanded_row["assignment"]),
                    "review_target_id": target["review_target_id"],
                    "source_ownership_digest": target["source_ownership_digest"],
                }
                for target, expanded_row in zip(targets, expanded, strict=True)
            ],
            materialized_review=review,
            performance=performance,
        )
        case.update(
            {
                "resolution_status": "resolved",
                "resolved_at": now,
                "segment_count": len(targets),
                "segment_target_ids": [str(row["review_target_id"]) for row in targets],
                "comment": str(payload.get("comment") or "").strip() or case.get("comment"),
            }
        )
        _replace_case(match_path, document, pending_cases, case)
        cleanup_unreferenced_manual_reviewed_slots(match_path, removed)
        project_segment_decisions_onto_materialized_review(match_path, review)
        digest = reviewed_decisions_semantic_digest(match_path)
        mark_reviewed_identity_recompute_required(
            match_path,
            semantic_decision_digest=digest,
        )
    except Exception:
        _restore_paths(rollback_paths)
        raise
    performance["total_ms"] = _elapsed_ms(started)
    return {
        **_response(case, digest),
        "saved_segment_decisions": saved,
        "performance": performance,
    }


def _normalized_lane_resolutions(
    resolutions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized = []
    for row in resolutions:
        value = {
            "lane_id": row["lane_id"],
            "lane_source_digest": row["lane_source_digest"],
            "resolution": row["resolution"],
        }
        if row["resolution"] == "direct":
            value["assignment"] = _normalized_assignments([row["assignment"]])[0]
        else:
            value["split_after_frames"] = list(row["split_after_frames"])
            value["segment_assignments"] = _normalized_assignments(
                list(row["segment_assignments"])
            )
        normalized.append(value)
    return normalized


def _concurrent_rollback_paths(match_path: Path) -> dict[Path, bytes | None]:
    names = (
        "reviewed_identity_mixed_players.json",
        "reviewed_identity_segment_review.json",
        DECISIONS_FILENAME,
        SLOT_REVIEW_FILENAME,
        "reviewed_identity_recompute_required.json",
        "reviewed_identity_hot_state.json",
        "reviewed_identity_hot_state_revision.json",
    )
    return {
        match_path / name: (match_path / name).read_bytes()
        if (match_path / name).exists() else None
        for name in names
    }


def _restore_paths(paths: dict[Path, bytes | None]) -> None:
    for path, previous in paths.items():
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(previous)


def _source_payload(source: dict[str, Any]) -> dict[str, Any]:
    return source_storage_payload(source)


def _replace_case(
    match_path: Path,
    document: dict[str, Any],
    cases: list[dict[str, Any]],
    case: dict[str, Any],
) -> None:
    case_id = str(case.get("case_id") or "")
    rows = [
        row
        for row in cases
        if str(row.get("case_id") or row.get("candidate_subject_id") or "")
        != case_id
    ]
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
