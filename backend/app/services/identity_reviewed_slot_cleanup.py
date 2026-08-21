from __future__ import annotations

"""Conservative cleanup for reviewed-only slots retired with split children."""

from pathlib import Path
from typing import Any

from app.services.identity_initial_audit_store import write_identity_json_atomic
from app.services.identity_reviewed_material_continuity import (
    load_material_continuity_decisions,
)
from app.services.identity_reviewed_mixed_store import load_mixed_player_cases
from app.services.identity_reviewed_segments import load_segment_decisions
from app.services.identity_reviewed_slot_registry import (
    build_reviewed_slot_registry,
    normalize_reviewed_slot_id,
)
from app.services.identity_reviewed_slot_review import (
    FILENAME,
    load_reviewed_slot_assignments,
)


def cleanup_unreferenced_manual_reviewed_slots(
    match_path: Path,
    removed_segment_decisions: list[dict[str, Any]],
) -> set[str]:
    """Remove manual slots created only by retired split children.

    The candidates are intentionally derived from the decisions just removed,
    never from a global scan.  Canonical registry entries are immutable, and a
    manual slot remains whenever any active reviewed decision still references
    it.
    """
    candidates = {
        slot_id
        for row in removed_segment_decisions
        if str(row.get("action") or "") == "create_new_stable_player"
        for slot_id in [normalize_reviewed_slot_id(row.get("stable_slot_id"))]
        if slot_id is not None
    }
    if not candidates:
        return set()

    manual_document = load_reviewed_slot_assignments(match_path)
    canonical_slots = set(build_reviewed_slot_registry(match_path, {"reviewed_slots": [], "decisions": []}))
    surviving = _surviving_slot_references(match_path, manual_document)
    removable = candidates - canonical_slots - surviving
    if not removable:
        return set()

    reviewed_slots = [
        row
        for row in manual_document.get("reviewed_slots") or []
        if not (
            normalize_reviewed_slot_id(row.get("stable_slot_id")) in removable
            and str(row.get("source") or "") == "manual_new_player_confirmation"
        )
    ]
    if len(reviewed_slots) == len(manual_document.get("reviewed_slots") or []):
        return set()
    write_identity_json_atomic(
        match_path / FILENAME,
        {**manual_document, "reviewed_slots": reviewed_slots},
    )
    return removable


def _surviving_slot_references(
    match_path: Path,
    manual_document: dict[str, Any],
) -> set[str]:
    references: set[str] = set()
    for document, collection in (
        (manual_document, "decisions"),
        (load_segment_decisions(match_path), "decisions"),
        (load_material_continuity_decisions(match_path), "decisions"),
    ):
        for row in document.get(collection) or []:
            slot_id = normalize_reviewed_slot_id(row.get("stable_slot_id"))
            if slot_id is not None:
                references.add(slot_id)
    for case in load_mixed_player_cases(match_path).get("cases") or []:
        for assignment in case.get("segment_assignments") or []:
            if not isinstance(assignment, dict):
                continue
            slot_id = normalize_reviewed_slot_id(assignment.get("stable_slot_id"))
            if slot_id is not None:
                references.add(slot_id)
    return references
