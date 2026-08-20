from __future__ import annotations

"""Cheap fail-closed authorization for deferred exception-review saves."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from app.services.identity_reviewed_active_cap import (
    detected_team_labels_from_progress,
)
from app.services.identity_reviewed_action_scope import (
    validate_review_unit_action_scope,
)
from app.services.identity_reviewed_correction_context import current_reviewed_decision
from app.services.identity_reviewed_material_continuity import (
    load_material_continuity_decisions,
)
from app.services.identity_reviewed_segments import load_segment_decisions
from app.services.identity_reviewed_progress import PROGRESS_SCHEMA_VERSION
from app.services.identity_review_scope import review_scope_dependency_matches


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
    """Authorize one save from the last materialized coverage queue.

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

    validate_review_unit_action_scope(payload, unit)

    saved = _saved_decision(match_path, subject_id, target_id, unit)
    if saved is not None and _semantic_decision(saved) != _semantic_decision(payload):
        raise DeferredReviewActionError(
            "review_unit_already_decided",
            "Ten przypadek ma już zapisaną inną decyzję. Odśwież Review przed zmianą.",
        )
    detected_team_labels_by_subject = None
    if target_id is None and unit.get("scope_kind") != "material_continuity":
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
        and progress.get("schema_version") == PROGRESS_SCHEMA_VERSION
        and progress.get("status") == "ready"
        and str(progress.get("match_id") or "") == expected_match_id
        and isinstance(progress.get("next_cases"), list)
        and isinstance(progress.get("optional_audit_cases", []), list)
        and bool(str(progress.get("source_snapshot_digest") or ""))
        and review_scope_dependency_matches(match_doc, progress)
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
    for queue, raw in _iter_authorized_review_units(progress):
        if not isinstance(raw, dict):
            continue
        if str(raw.get("candidate_subject_id") or "") != subject_id:
            continue
        raw_target_id = str(raw.get("review_target_id") or "").strip() or None
        if raw_target_id != target_id:
            continue
        if raw.get("operator_actionable") is False:
            continue
        if not _authorized_queue_semantics(queue, raw):
            continue
        if target_id is None:
            if raw_target_id is None and raw.get("scope_kind") in {
                None,
                "whole_subject",
                "material_continuity",
            }:
                return raw
        elif raw.get("scope_kind") == "canonical_segment":
            return raw
    return None


def _iter_authorized_review_units(
    progress: dict[str, Any],
) -> Iterator[tuple[str, Any]]:
    for raw in progress.get("next_cases") or []:
        yield "required", raw
    for raw in progress.get("optional_audit_cases") or []:
        yield "optional", raw


def _authorized_queue_semantics(queue: str, unit: dict[str, Any]) -> bool:
    if queue == "required":
        return unit.get("priority") in {"high", "coverage", "continuity"} and unit.get(
            "current_resolution_status"
        ) in {
            "pending_high_priority",
            "pending_coverage_review",
            "pending_material_continuity_review",
        }
    return unit.get("priority") == "optional" and unit.get(
        "current_resolution_status"
    ) in {"optional_team_audit", "pending_optional_max_audit"}


def _saved_decision(
    match_path: Path,
    subject_id: str,
    target_id: str | None,
    unit: dict[str, Any],
) -> dict[str, Any] | None:
    if unit.get("scope_kind") == "material_continuity":
        continuity_group_id = str(unit.get("continuity_group_id") or "")
        source_ownership_digest = str(unit.get("source_ownership_digest") or "")
        return next(
            (
                dict(row)
                for row in load_material_continuity_decisions(match_path).get("decisions")
                or []
                if str(row.get("continuity_group_id") or "") == continuity_group_id
                and str(row.get("source_ownership_digest") or "")
                == source_ownership_digest
            ),
            None,
        )
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
) -> tuple[str, str | None, str | None, str | None, str | None]:
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
        str(value.get("mixed_hint") or "unknown")
        if action == "mixed_players"
        else None,
    )


def _load_json_object(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None
