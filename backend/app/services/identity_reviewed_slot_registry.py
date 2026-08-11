from __future__ import annotations

"""Bounded registry of canonical and reviewed-only manual player slots."""

import json
from pathlib import Path
import re
from typing import Any


MAX_REVIEWED_SLOTS_PER_TEAM = 14
_SLOT_ID = re.compile(r"^(?P<team>[AB])(?P<number>0[1-9]|1[0-4])$")


def normalize_reviewed_slot_id(value: Any) -> str | None:
    text = str(value or "").removeprefix("slot-").upper()
    match = _SLOT_ID.fullmatch(text)
    if not match:
        return None
    return f"{match.group('team')}{int(match.group('number')):02d}"


def build_reviewed_slot_registry(
    match_path: Path,
    manual_document: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    registry: dict[str, dict[str, Any]] = {}
    for filename, collection, source in (
        ("global_identity.json", "slots", "global_identity"),
        ("stable_players.json", "players", "stable_players"),
    ):
        document = _optional(match_path / filename)
        for row in document.get(collection) or []:
            slot_id = normalize_reviewed_slot_id(
                row.get("stable_player_id")
                or row.get("slot_id")
                or row.get("stable_subject_id")
            )
            if not slot_id:
                continue
            registry.setdefault(
                slot_id,
                {
                    "stable_slot_id": slot_id,
                    "team_label": slot_id[0],
                    "source": source,
                    "created_for_candidate_subject_id": None,
                    "status": "canonical",
                },
            )

    document = manual_document
    if document is None:
        document = _optional(match_path / "reviewed_identity_slot_assignments.json")
    for row in manual_reviewed_slot_records(document):
        slot_id = str(row["stable_slot_id"])
        if slot_id in registry:
            continue
        registry[slot_id] = dict(row)
    return dict(sorted(registry.items()))


def build_materialized_reviewed_slot_registry(
    candidate_document: dict[str, Any],
    manual_document: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build the correction-time registry from already materialized identity data.

    Candidate identity records contain the canonical production slots used to
    prepare Review.  Reusing those server-generated bindings avoids parsing the
    full global identity document for every operator click.
    """
    registry: dict[str, dict[str, Any]] = {}
    for subject in candidate_document.get("subjects") or []:
        for raw_slot_id in (
            list(subject.get("production_player_ids") or [])
            + list(subject.get("production_subject_ids") or [])
        ):
            slot_id = normalize_reviewed_slot_id(raw_slot_id)
            if not slot_id:
                continue
            registry.setdefault(
                slot_id,
                {
                    "stable_slot_id": slot_id,
                    "team_label": slot_id[0],
                    "source": "materialized_candidate_identity",
                    "created_for_candidate_subject_id": None,
                    "status": "canonical",
                },
            )
    for row in manual_reviewed_slot_records(manual_document):
        registry.setdefault(str(row["stable_slot_id"]), dict(row))
    return dict(sorted(registry.items()))


def manual_reviewed_slot_records(
    manual_document: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    document = manual_document or {}
    records: dict[str, dict[str, Any]] = {}
    for row in document.get("reviewed_slots") or []:
        slot_id = normalize_reviewed_slot_id(row.get("stable_slot_id"))
        if not slot_id:
            continue
        records[slot_id] = {
            "stable_slot_id": slot_id,
            "team_label": slot_id[0],
            "source": "manual_new_player_confirmation",
            "created_for_candidate_subject_id": str(
                row.get("created_for_candidate_subject_id") or ""
            )
            or None,
            "status": str(row.get("status") or "active"),
        }

    # Backward-compatible recovery for decisions written before reviewed_slots
    # became explicit in the document contract.
    for row in document.get("decisions") or []:
        if row.get("action") != "create_new_stable_player":
            continue
        slot_id = normalize_reviewed_slot_id(row.get("stable_slot_id"))
        if not slot_id or slot_id in records:
            continue
        records[slot_id] = {
            "stable_slot_id": slot_id,
            "team_label": slot_id[0],
            "source": "manual_new_player_confirmation",
            "created_for_candidate_subject_id": str(
                row.get("candidate_subject_id") or ""
            )
            or None,
            "status": "active",
        }
    return [records[key] for key in sorted(records)]


def next_free_reviewed_slot(
    team_label: str,
    registry: dict[str, dict[str, Any]],
) -> str | None:
    team = str(team_label or "").upper()
    if team not in {"A", "B"}:
        return None
    return next(
        (
            f"{team}{number:02d}"
            for number in range(1, MAX_REVIEWED_SLOTS_PER_TEAM + 1)
            if f"{team}{number:02d}" not in registry
        ),
        None,
    )


def _optional(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
