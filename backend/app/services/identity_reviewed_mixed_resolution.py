from __future__ import annotations

"""Deferred resolution of one operator-classified mixed-player subject."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.identity_reviewed_correction_context import reviewed_decisions_semantic_digest
from app.services.identity_reviewed_mixed_store import (
    UNRESOLVED_STATUSES,
    current_mixed_subject_digest,
    load_mixed_player_cases,
    observations_for_case,
    operator_mixed_targets,
    save_mixed_case_document,
    validate_split_frames,
)
from app.services.identity_reviewed_recompute_state import mark_reviewed_identity_recompute_required
from app.services.identity_reviewed_segments import (
    DECISIONS_FILENAME,
    build_segment_review_document,
    save_segment_decision,
)
from app.services.identity_reviewed_slot_review import FILENAME as SLOT_REVIEW_FILENAME


def save_mixed_player_resolution(
    match_path: Path,
    match_doc: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    subject_id = str(payload.get("candidate_subject_id") or "").strip()
    document = load_mixed_player_cases(match_path)
    cases = {
        str(row.get("candidate_subject_id")): dict(row)
        for row in document.get("cases") or []
        if row.get("candidate_subject_id")
    }
    case = cases.get(subject_id)
    if case is None:
        raise ValueError(f"Unknown mixed-player case: {subject_id or '<missing>'}")
    supplied_digest = str(payload.get("source_subject_digest") or "")
    if supplied_digest != str(case.get("source_subject_digest") or ""):
        raise MixedPlayerTargetError("mixed_player_case_stale")
    if supplied_digest != current_mixed_subject_digest(match_path, subject_id):
        raise MixedPlayerTargetError("mixed_player_case_stale")
    if str(case.get("resolution_status")) not in UNRESOLVED_STATUSES:
        raise ValueError("Mixed-player case is already resolved")

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
        cases[subject_id] = case
        save_mixed_case_document(match_path, {**document, "cases": list(cases.values())})
        digest = reviewed_decisions_semantic_digest(match_path)
        mark_reviewed_identity_recompute_required(match_path, semantic_decision_digest=digest)
        return _response(case, digest)
    if resolution != "split":
        raise ValueError(f"Unsupported mixed resolution: {resolution}")

    observations = observations_for_case(match_path, case)
    split_frames = sorted({int(value) for value in payload.get("split_after_frames") or []})
    validate_split_frames(observations, split_frames)
    case.update(
        {
            "resolution_status": "unresolved",
            "split_after_frames": split_frames,
            "updated_at": now,
        }
    )
    pending_document = {**document, "cases": [case if key == subject_id else row for key, row in cases.items()]}
    targets = [
        row
        for row in operator_mixed_targets(match_path, pending_document)
        if str(row.get("candidate_subject_id") or "") == subject_id
    ]
    assignments = payload.get("segment_assignments") or []
    if len(assignments) != len(targets):
        raise ValueError("Every mixed segment requires one assignment")

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
        save_mixed_case_document(match_path, pending_document)
        review = build_segment_review_document(match_path, match_doc)
        saved = []
        for target, assignment in zip(targets, assignments, strict=True):
            saved.append(
                save_segment_decision(
                    match_path,
                    match_doc,
                    {
                        **dict(assignment),
                        "review_target_id": target["review_target_id"],
                        "source_ownership_digest": target["source_ownership_digest"],
                    },
                    materialized_review=review,
                )
            )
        case.update(
            {
                "resolution_status": "resolved",
                "resolved_at": now,
                "segment_count": len(targets),
                "segment_target_ids": [row["review_target_id"] for row in targets],
                "comment": str(payload.get("comment") or "").strip() or case.get("comment"),
            }
        )
        cases[subject_id] = case
        save_mixed_case_document(match_path, {**document, "cases": list(cases.values())})
    except Exception:
        for path, previous in rollback_paths.items():
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(previous)
        raise

    digest = reviewed_decisions_semantic_digest(match_path)
    mark_reviewed_identity_recompute_required(match_path, semantic_decision_digest=digest)
    return {**_response(case, digest), "saved_segment_decisions": saved}


def _response(case: dict[str, Any], digest: str) -> dict[str, Any]:
    return {
        "saved_case": case,
        "semantic_decision_digest": digest,
        "recompute_deferred": True,
        "persistence": {"status": "saved", "downstream_recompute_triggered": False},
    }


class MixedPlayerTargetError(ValueError):
    pass
