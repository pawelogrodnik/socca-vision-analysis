from __future__ import annotations

"""Reviewed-only operator decisions for candidate fragment to stable slot mapping."""

from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from app.services.identity_initial_audit_store import write_identity_json_atomic
from app.services.identity_reviewed_slot_registry import (
    build_reviewed_slot_registry,
    manual_reviewed_slot_records,
    next_free_reviewed_slot,
    normalize_reviewed_slot_id,
)


FILENAME = "reviewed_identity_slot_assignments.json"
ALLOWED_ACTIONS = frozenset(
    {
        "assign_existing_slot",
        "create_new_stable_player",
        "referee",
        "false_detection",
        "team_unknown",
        "unresolved",
    }
)


def load_reviewed_slot_assignments(match_path: Path) -> dict[str, Any]:
    path = match_path / FILENAME
    if not path.exists():
        return _document([])
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("decisions"), list):
        raise ValueError(f"{FILENAME} must contain a decisions array")
    return value


def reviewed_slot_assignment_read_model(match_path: Path) -> dict[str, Any]:
    document = load_reviewed_slot_assignments(match_path)
    registry = build_reviewed_slot_registry(match_path, document)
    return {
        **document,
        "slots": [registry[key] for key in sorted(registry)],
    }


def save_reviewed_slot_assignments(
    match_path: Path,
    candidate_document: dict[str, Any],
    updates: list[dict[str, Any]],
) -> dict[str, Any]:
    document = prepare_reviewed_slot_assignments(
        match_path,
        candidate_document,
        updates,
    )
    write_identity_json_atomic(match_path / FILENAME, document)
    return document


def prepare_reviewed_slot_assignments(
    match_path: Path,
    candidate_document: dict[str, Any],
    updates: list[dict[str, Any]],
) -> dict[str, Any]:
    existing = load_reviewed_slot_assignments(match_path)
    decisions = {
        str(row.get("candidate_subject_id")): dict(row)
        for row in existing.get("decisions") or []
        if row.get("candidate_subject_id")
    }
    tracklets = _load_tracklets(match_path)
    known_subjects, ambiguous_subjects, subject_teams = _candidate_context(
        candidate_document,
        tracklets,
    )
    normalized_updates = _normalize_updates(updates, known_subjects)
    if len({row["candidate_subject_id"] for row in normalized_updates}) != len(
        normalized_updates
    ):
        raise ValueError("Each candidate subject may be updated only once per request")

    registry = build_reviewed_slot_registry(match_path, existing)
    reviewed_slots = {
        str(row["stable_slot_id"]): dict(row)
        for row in manual_reviewed_slot_records(existing)
    }
    updated_subjects = {str(row["candidate_subject_id"]) for row in normalized_updates}
    _migrate_legacy_create_decisions(
        decisions,
        updated_subjects,
        registry,
        reviewed_slots,
        known_subjects,
        ambiguous_subjects,
        subject_teams,
    )
    pending_allocations: list[dict[str, Any]] = []
    for update in normalized_updates:
        if update["action"] != "create_new_stable_player":
            continue
        subject_id = str(update["candidate_subject_id"])
        team_label = str(update["team_label"])
        _validate_subject_team(
            subject_id,
            team_label,
            ambiguous_subjects,
            subject_teams,
        )
        previous = decisions.get(subject_id) or {}
        previous_slot = normalize_reviewed_slot_id(previous.get("stable_slot_id"))
        preserves_previous = (
            previous.get("action") == "create_new_stable_player"
            and previous_slot is not None
            and previous_slot[0] == team_label
        )
        supplied_slot = normalize_reviewed_slot_id(update.get("stable_slot_id"))
        if supplied_slot and (not preserves_previous or supplied_slot != previous_slot):
            raise ValueError(
                "stable_slot_id for create_new_stable_player is allocated by the server"
            )
        if preserves_previous:
            update["stable_slot_id"] = previous_slot
            reviewed_slots.setdefault(
                previous_slot,
                _manual_slot_record(previous_slot, subject_id),
            )
            registry.setdefault(previous_slot, dict(reviewed_slots[previous_slot]))
        else:
            pending_allocations.append(update)

    # A create action is saved immediately by the UI. Rejecting multiple fresh
    # allocations avoids assigning identity from arbitrary JSON list order.
    if len(pending_allocations) > 1:
        raise ValueError(
            "Save new reviewed players one at a time so slot allocation is unambiguous"
        )
    for update in pending_allocations:
        team_label = str(update["team_label"])
        slot_id = next_free_reviewed_slot(team_label, registry)
        if slot_id is None:
            raise ValueError(f"bounded pool exhausted for team {team_label}")
        subject_id = str(update["candidate_subject_id"])
        update["stable_slot_id"] = slot_id
        reviewed_slots[slot_id] = _manual_slot_record(slot_id, subject_id)
        registry[slot_id] = dict(reviewed_slots[slot_id])

    for raw in normalized_updates:
        subject_id = str(raw["candidate_subject_id"])
        action = str(raw["action"])
        previous = decisions.get(subject_id) or {}
        if action == "assign_existing_slot":
            stable_slot_id = normalize_reviewed_slot_id(raw.get("stable_slot_id"))
            if not stable_slot_id or stable_slot_id not in registry:
                raise ValueError(
                    f"manual reviewed slot does not exist: {raw.get('stable_slot_id')}"
                )
            team_label = str(registry[stable_slot_id]["team_label"])
            _validate_subject_team(
                subject_id,
                team_label,
                ambiguous_subjects,
                subject_teams,
            )
        elif action == "create_new_stable_player":
            stable_slot_id = str(raw["stable_slot_id"])
            team_label = str(raw["team_label"])
        else:
            stable_slot_id = None
            team_label = str(raw.get("team_label") or "").upper() or None
        decision = {
            "candidate_subject_id": subject_id,
            "action": action,
            "stable_slot_id": stable_slot_id,
            "team_label": team_label,
            "source": "manual_review",
            "comment": (
                str(raw.get("comment") or "").strip() or None
                if "comment" in raw
                else previous.get("comment")
            ),
            "reviewed_at": _now(),
        }
        if _semantic_decision(previous) == _semantic_decision(decision):
            decision["reviewed_at"] = (
                previous.get("reviewed_at") or decision["reviewed_at"]
            )
        decisions[subject_id] = decision

    referenced_slots = {
        str(row["stable_slot_id"])
        for row in decisions.values()
        if row.get("action") in {"assign_existing_slot", "create_new_stable_player"}
        and row.get("stable_slot_id")
    }
    for slot_id, row in reviewed_slots.items():
        row["status"] = "active" if slot_id in referenced_slots else "orphaned"
    document = _document(
        sorted(decisions.values(), key=lambda row: str(row["candidate_subject_id"])),
        [reviewed_slots[key] for key in sorted(reviewed_slots)],
    )
    return document


def clear_reviewed_slot_assignment(
    match_path: Path,
    candidate_subject_id: str,
) -> dict[str, Any]:
    existing = load_reviewed_slot_assignments(match_path)
    decisions = [
        dict(row)
        for row in existing.get("decisions") or []
        if str(row.get("candidate_subject_id") or "") != candidate_subject_id
    ]
    referenced_slots = {
        str(row["stable_slot_id"])
        for row in decisions
        if row.get("action") in {"assign_existing_slot", "create_new_stable_player"}
        and row.get("stable_slot_id")
    }
    reviewed_slots = manual_reviewed_slot_records(existing)
    for row in reviewed_slots:
        row["status"] = (
            "active" if row["stable_slot_id"] in referenced_slots else "orphaned"
        )
    document = _document(
        sorted(decisions, key=lambda row: str(row.get("candidate_subject_id") or "")),
        reviewed_slots,
    )
    write_identity_json_atomic(match_path / FILENAME, document)
    return document


def _normalize_updates(
    updates: list[dict[str, Any]], known_subjects: set[str]
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for raw in updates:
        subject_id = str(raw.get("candidate_subject_id") or "")
        if subject_id not in known_subjects:
            raise ValueError(f"Unknown candidate_subject_id: {subject_id}")
        action = str(raw.get("action") or "")
        if action not in ALLOWED_ACTIONS:
            raise ValueError(f"Unsupported reviewed slot action: {action}")
        team_label = str(raw.get("team_label") or "").upper() or None
        if action == "create_new_stable_player" and team_label not in {"A", "B"}:
            raise ValueError("create_new_stable_player requires team_label A or B")
        output.append(
            {
                **raw,
                "candidate_subject_id": subject_id,
                "action": action,
                "team_label": team_label,
            }
        )
    return output


def _candidate_context(
    candidate_document: dict[str, Any],
    tracklets: dict[str, dict[str, Any]],
) -> tuple[set[str], set[str], dict[str, set[str]]]:
    subjects: dict[str, set[str]] = defaultdict(set)
    memberships: dict[str, set[str]] = defaultdict(set)
    for row in candidate_document.get("subjects") or []:
        subject_id = str(row.get("candidate_subject_id") or "")
        if not subject_id:
            continue
        for tracklet_id in row.get("tracklet_ids") or []:
            tracklet = str(tracklet_id)
            subjects[subject_id].add(tracklet)
            memberships[tracklet].add(subject_id)
        subjects.setdefault(subject_id, set())
    ambiguous = {
        subject_id
        for tracklet_subjects in memberships.values()
        if len(tracklet_subjects) > 1
        for subject_id in tracklet_subjects
    }
    teams = {
        subject_id: {
            str(tracklets.get(tracklet_id, {}).get("team_label") or "U")
            for tracklet_id in tracklet_ids
            if tracklet_id in tracklets
        }
        for subject_id, tracklet_ids in subjects.items()
    }
    return set(subjects), ambiguous, teams


def _validate_subject_team(
    subject_id: str,
    expected_team: str,
    ambiguous_subjects: set[str],
    subject_teams: dict[str, set[str]],
) -> None:
    if subject_id in ambiguous_subjects:
        raise ValueError(f"ambiguous subject: {subject_id}")
    teams = subject_teams.get(subject_id) or set()
    if len(teams) > 1:
        raise ValueError(f"mixed-team subject: {subject_id}")
    if teams and teams != {expected_team}:
        actual = next(iter(teams))
        raise ValueError(
            f"team mismatch: subject {subject_id} is team {actual}, slot is team {expected_team}"
        )


def _manual_slot_record(slot_id: str, subject_id: str) -> dict[str, Any]:
    return {
        "stable_slot_id": slot_id,
        "team_label": slot_id[0],
        "source": "manual_new_player_confirmation",
        "created_for_candidate_subject_id": subject_id,
        "status": "active",
    }


def _migrate_legacy_create_decisions(
    decisions: dict[str, dict[str, Any]],
    updated_subjects: set[str],
    registry: dict[str, dict[str, Any]],
    reviewed_slots: dict[str, dict[str, Any]],
    known_subjects: set[str],
    ambiguous_subjects: set[str],
    subject_teams: dict[str, set[str]],
) -> None:
    legacy = [
        (subject_id, decision)
        for subject_id, decision in decisions.items()
        if subject_id not in updated_subjects
        and decision.get("action") == "create_new_stable_player"
        and not normalize_reviewed_slot_id(decision.get("stable_slot_id"))
    ]
    timestamps = [str(row.get("reviewed_at") or "") for _, row in legacy]
    if len(legacy) > 1 and (
        not all(timestamps) or len(set(timestamps)) != len(timestamps)
    ):
        raise ValueError(
            "Legacy manual player decisions require one-at-a-time resave before allocation"
        )
    for subject_id, decision in sorted(
        legacy,
        key=lambda item: str(item[1].get("reviewed_at") or ""),
    ):
        if subject_id not in known_subjects:
            raise ValueError(f"Unknown candidate_subject_id: {subject_id}")
        team_label = str(decision.get("team_label") or "").upper()
        if team_label not in {"A", "B"}:
            raise ValueError(
                f"Legacy create_new_stable_player for {subject_id} requires team A or B"
            )
        _validate_subject_team(
            subject_id,
            team_label,
            ambiguous_subjects,
            subject_teams,
        )
        slot_id = next_free_reviewed_slot(team_label, registry)
        if slot_id is None:
            raise ValueError(f"bounded pool exhausted for team {team_label}")
        decisions[subject_id] = {**decision, "stable_slot_id": slot_id}
        reviewed_slots[slot_id] = _manual_slot_record(slot_id, subject_id)
        registry[slot_id] = dict(reviewed_slots[slot_id])


def _semantic_decision(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "candidate_subject_id",
            "action",
            "stable_slot_id",
            "team_label",
            "source",
        )
    }


def _load_tracklets(match_path: Path) -> dict[str, dict[str, Any]]:
    path = match_path / "tracklets.json"
    if not path.exists():
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(row.get("tracklet_id")): row
        for row in document.get("tracklets") or []
        if row.get("tracklet_id")
    }


def _document(
    decisions: list[dict[str, Any]],
    reviewed_slots: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "1.1.0",
        "mode": "reviewed_identity_slot_assignments",
        "updated_at": _now(),
        "decisions": decisions,
        "reviewed_slots": reviewed_slots or [],
        "safety": {
            "mutates_raw_detections": False,
            "mutates_production_identity": False,
            "mutates_published_packages": False,
        },
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
