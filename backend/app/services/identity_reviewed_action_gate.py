from __future__ import annotations

"""Cheap fail-closed authorization for deferred exception-review saves."""

import json
from pathlib import Path
from typing import Any

from app.services.identity_reviewed_active_cap import (
    detected_team_labels_from_progress,
)
from app.services.identity_reviewed_correction_context import current_reviewed_decision
from app.services.identity_reviewed_segments import load_segment_decisions


PROGRESS_FILENAME = "reviewed_identity_progress.json"
REPORT_FILENAME = "reviewed_identity_report.json"


class DeferredReviewActionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def validate_deferred_review_action(
    match_path: Path,
    match_doc: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Authorize one save from the last materialized high-priority queue.

    The queue is a deliberate batch baseline. A dirty recompute marker does not
    invalidate it, so decisions two and three can still be saved before the one
    authoritative final recompute.
    """
    progress = _load_batch_baseline(match_path, match_doc)
    subject_id = str(payload.get("candidate_subject_id") or "").strip()
    target_id = str(payload.get("review_target_id") or "").strip() or None
    unit = _actionable_unit(progress, subject_id, target_id)
    if unit is None:
        raise DeferredReviewActionError(
            "review_unit_not_actionable",
            "Ten przypadek nie znajduje się już w aktualnej kolejce Review. "
            "Odśwież Review i spróbuj ponownie.",
        )

    saved = _saved_decision(match_path, subject_id, target_id, unit)
    if saved is not None and _semantic_decision(saved) != _semantic_decision(payload):
        raise DeferredReviewActionError(
            "review_unit_already_decided",
            "Ten przypadek ma już zapisaną inną decyzję. Odśwież Review przed zmianą.",
        )
    detected_team_labels_by_subject = None
    if target_id is None:
        detected_team_labels_by_subject = detected_team_labels_from_progress(progress)
        if (
            detected_team_labels_by_subject is None
            or subject_id not in detected_team_labels_by_subject
        ):
            raise DeferredReviewActionError(
                "review_queue_stale",
                "Kolejka Review nie zawiera pełnego kontekstu drużyn. "
                "Uruchom odświeżenie Review.",
            )
    return {
        "review_unit": unit,
        "idempotent_replay": saved is not None,
        "batch_source_snapshot_digest": progress["source_snapshot_digest"],
        "detected_team_labels_by_subject": detected_team_labels_by_subject,
    }


def _load_batch_baseline(
    match_path: Path,
    match_doc: dict[str, Any],
) -> dict[str, Any]:
    progress = _load_json_object(match_path / PROGRESS_FILENAME)
    report = _load_json_object(match_path / REPORT_FILENAME)
    expected_match_id = str(match_doc.get("id") or match_path.name)
    valid = (
        progress is not None
        and progress.get("schema_version") == "1.0.0"
        and progress.get("status") == "ready"
        and str(progress.get("match_id") or "") == expected_match_id
        and isinstance(progress.get("next_cases"), list)
        and bool(str(progress.get("source_snapshot_digest") or ""))
        and report is not None
        and str(report.get("snapshot_digest") or "")
        == str(progress.get("source_snapshot_digest") or "")
    )
    if not valid:
        raise DeferredReviewActionError(
            "review_queue_stale",
            "Nie można potwierdzić aktualnej kolejki Review. Uruchom odświeżenie Review.",
        )
    return progress


def _actionable_unit(
    progress: dict[str, Any],
    subject_id: str,
    target_id: str | None,
) -> dict[str, Any] | None:
    for raw in progress.get("next_cases") or []:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("candidate_subject_id") or "") != subject_id:
            continue
        raw_target_id = str(raw.get("review_target_id") or "").strip() or None
        if raw_target_id != target_id:
            continue
        if raw.get("priority") != "high":
            continue
        if raw.get("current_resolution_status") != "pending_high_priority":
            continue
        if target_id is None:
            if raw_target_id is None and raw.get("scope_kind") in {None, "whole_subject"}:
                return raw
        elif raw.get("scope_kind") == "canonical_segment":
            return raw
    return None


def _saved_decision(
    match_path: Path,
    subject_id: str,
    target_id: str | None,
    unit: dict[str, Any],
) -> dict[str, Any] | None:
    if target_id is None:
        return current_reviewed_decision(match_path, subject_id)
    saved = next(
        (
            dict(row)
            for row in load_segment_decisions(match_path).get("decisions") or []
            if str(row.get("review_target_id") or "") == target_id
        ),
        None,
    )
    if saved is None:
        return None
    if str(saved.get("source_ownership_digest") or "") != str(
        unit.get("source_ownership_digest") or ""
    ):
        return None
    return saved


def _semantic_decision(
    value: dict[str, Any],
) -> tuple[str, str | None, str | None, str | None]:
    action = str(value.get("action") or "")
    return (
        action,
        str(value.get("player_id") or "") or None
        if action == "assign_roster_player"
        else None,
        str(value.get("stable_slot_id") or "") or None
        if action == "assign_existing_slot"
        else None,
        str(value.get("team_label") or "").upper() or None
        if action in {"assign_team", "create_new_stable_player"}
        else None,
    )


def _load_json_object(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None
