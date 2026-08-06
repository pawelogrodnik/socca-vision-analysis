from __future__ import annotations

"""Reviewed-only operator decisions for candidate fragment to stable slot mapping."""

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any

from app.services.identity_initial_audit_store import write_identity_json_atomic


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
_SLOT_ID = re.compile(r"^[AB](?:0[1-9]|1[0-4])$")


def load_reviewed_slot_assignments(match_path: Path) -> dict[str, Any]:
    path = match_path / FILENAME
    if not path.exists():
        return _document([])
    import json

    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("decisions"), list):
        raise ValueError(f"{FILENAME} must contain a decisions array")
    return value


def save_reviewed_slot_assignments(
    match_path: Path,
    candidate_document: dict[str, Any],
    updates: list[dict[str, Any]],
) -> dict[str, Any]:
    known_subjects = {
        str(row.get("candidate_subject_id"))
        for row in candidate_document.get("subjects") or []
        if row.get("candidate_subject_id")
    }
    existing = load_reviewed_slot_assignments(match_path)
    decisions = {
        str(row.get("candidate_subject_id")): dict(row)
        for row in existing.get("decisions") or []
        if row.get("candidate_subject_id")
    }
    for raw in updates:
        subject_id = str(raw.get("candidate_subject_id") or "")
        if subject_id not in known_subjects:
            raise ValueError(f"Unknown candidate_subject_id: {subject_id}")
        action = str(raw.get("action") or "")
        if action not in ALLOWED_ACTIONS:
            raise ValueError(f"Unsupported reviewed slot action: {action}")
        if action == "assign_existing_slot":
            stable_slot_id = str(raw.get("stable_slot_id") or "").upper()
            if not _SLOT_ID.fullmatch(stable_slot_id):
                raise ValueError("stable_slot_id must be an existing bounded slot A01-A14 or B01-B14")
        else:
            stable_slot_id = None
        team_label = str(raw.get("team_label") or "").upper() or None
        if action == "create_new_stable_player" and team_label not in {"A", "B"}:
            raise ValueError("create_new_stable_player requires team_label A or B")
        decisions[subject_id] = {
            "candidate_subject_id": subject_id,
            "action": action,
            "stable_slot_id": stable_slot_id,
            "team_label": team_label,
            "source": "manual_review",
            "comment": str(raw.get("comment") or "").strip() or None,
            "reviewed_at": _now(),
        }
    document = _document(
        sorted(decisions.values(), key=lambda row: str(row["candidate_subject_id"]))
    )
    write_identity_json_atomic(match_path / FILENAME, document)
    return document


def _document(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "mode": "reviewed_identity_slot_assignments",
        "updated_at": _now(),
        "decisions": decisions,
        "safety": {
            "mutates_raw_detections": False,
            "mutates_production_identity": False,
            "mutates_published_packages": False,
        },
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
