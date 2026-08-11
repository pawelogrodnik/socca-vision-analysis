from __future__ import annotations

"""Materialized exact active-player cap validation for deferred corrections."""

from collections import Counter
import json
from pathlib import Path
from typing import Any

from app.services.identity_reviewed_slot_registry import normalize_reviewed_slot_id
from app.services.identity_stable_anonymous import DEFAULT_ACTIVE_PLAYERS_PER_TEAM


CONTEXT_SCHEMA_VERSION = "1.0.0"


def build_reviewed_active_cap_context(
    match_path: Path,
    units: list[dict[str, Any]],
) -> dict[str, Any]:
    subjects = []
    relevant_frames: set[int] = set()
    for unit in units:
        if unit.get("review_target_id"):
            continue
        frames = sorted(
            {
                int(pair[1])
                for pair in unit.get("detected_pairs") or []
                if isinstance(pair, (list, tuple)) and len(pair) == 2
            }
        )
        relevant_frames.update(frames)
        subjects.append(
            {
                "candidate_subject_id": str(unit.get("candidate_subject_id") or ""),
                "source_team_label": str(unit.get("source_team_label") or "U"),
                "detected_team_labels": list(unit.get("detected_team_labels") or []),
                "detected_frames": frames,
            }
        )

    base_context = {
        "schema_version": CONTEXT_SCHEMA_VERSION,
        "detected_team_evidence_status": "ready",
        "subjects": sorted(
            subjects,
            key=lambda row: str(row["candidate_subject_id"]),
        ),
    }
    frame_document = _load_json_object(match_path / "frame_detection_counts.json")
    if frame_document is None or not isinstance(frame_document.get("frames"), list):
        return {**base_context, "status": "unavailable"}

    counts: dict[int, tuple[int, int]] = {}
    for row in frame_document.get("frames") or []:
        frame = int(row.get("frame") or 0)
        if frame not in relevant_frames:
            continue
        if row.get("active_team_a") is None or row.get("active_team_b") is None:
            continue
        counts[frame] = (int(row["active_team_a"]), int(row["active_team_b"]))
    if len(counts) != len(relevant_frames):
        return {**base_context, "status": "incomplete"}

    return {
        **base_context,
        "status": "ready",
        "active_players_per_team": _active_players_per_team(
            match_path,
            frame_document,
        ),
        "canonical_visible_counts": [
            {"frame": frame, "A": values[0], "B": values[1]}
            for frame, values in sorted(counts.items())
        ],
    }


def detected_team_labels_from_progress(
    progress: dict[str, Any],
) -> dict[str, set[str]] | None:
    context = progress.get("deferred_correction_context")
    if not isinstance(context, dict):
        return None
    if context.get("schema_version") != CONTEXT_SCHEMA_VERSION:
        return None
    if context.get("detected_team_evidence_status") != "ready":
        return None
    rows = context.get("subjects")
    if not isinstance(rows, list):
        return None
    output: dict[str, set[str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            return None
        subject_id = str(row.get("candidate_subject_id") or "")
        labels = row.get("detected_team_labels")
        if not subject_id or subject_id in output or not isinstance(labels, list):
            return None
        normalized = [str(value).upper() for value in labels]
        if normalized != sorted(set(normalized)) or any(
            value not in {"A", "B"} for value in normalized
        ):
            return None
        output[subject_id] = set(normalized)
    return output


def load_reviewed_detected_team_labels(
    match_path: Path,
) -> dict[str, set[str]] | None:
    progress = _load_json_object(match_path / "reviewed_identity_progress.json")
    return detected_team_labels_from_progress(progress or {})


def validate_new_player_active_cap_from_progress(
    match_path: Path,
    prepared: dict[str, Any],
    subject_id: str,
) -> bool:
    """Return True when exact cached validation ran, False for exact fallback."""
    progress = _load_json_object(match_path / "reviewed_identity_progress.json")
    context = (progress or {}).get("deferred_correction_context")
    if not isinstance(context, dict) or context.get("status") != "ready":
        return False
    if context.get("schema_version") != CONTEXT_SCHEMA_VERSION:
        return False

    subject_rows = {
        str(row.get("candidate_subject_id") or ""): row
        for row in context.get("subjects") or []
        if isinstance(row, dict) and row.get("candidate_subject_id")
    }
    canonical_visible = {
        (int(row.get("frame") or 0), team): int(row.get(team) or 0)
        for row in context.get("canonical_visible_counts") or []
        if isinstance(row, dict)
        for team in ("A", "B")
    }
    active_cap = int(context.get("active_players_per_team") or 0)
    if active_cap <= 0 or subject_id not in subject_rows:
        return False

    manual_visible: Counter[tuple[int, str]] = Counter()
    decisions = sorted(
        (
            row
            for row in prepared.get("decisions") or []
            if row.get("action") == "create_new_stable_player"
        ),
        key=_manual_new_allocation_order,
    )
    for decision in decisions:
        decision_subject_id = str(decision.get("candidate_subject_id") or "")
        row = subject_rows.get(decision_subject_id)
        if row is None:
            return False
        team = str(decision.get("team_label") or "")
        detected_teams = {
            str(value).upper()
            for value in row.get("detected_team_labels") or []
            if str(value).upper() in {"A", "B"}
        }
        frames = {int(frame) for frame in row.get("detected_frames") or []}
        if team not in {"A", "B"} or len(detected_teams) > 1 or not frames:
            return False
        if detected_teams and detected_teams != {team}:
            return False
        if any((frame, team) not in canonical_visible for frame in frames):
            return False
        if any(
            canonical_visible[(frame, team)] + manual_visible[(frame, team)]
            >= active_cap
            for frame in frames
        ):
            if decision_subject_id == subject_id:
                raise ValueError("Eighth simultaneous active player is not allowed")
            continue
        for frame in frames:
            manual_visible[(frame, team)] += 1
        if decision_subject_id == subject_id:
            return True
    return False


def _manual_new_allocation_order(decision: dict[str, Any]) -> tuple[int, str, str, str]:
    slot_id = normalize_reviewed_slot_id(decision.get("stable_slot_id"))
    return (
        0 if slot_id else 1,
        slot_id or "",
        str(decision.get("reviewed_at") or ""),
        str(decision.get("candidate_subject_id") or ""),
    )


def _active_players_per_team(
    match_path: Path,
    frame_document: dict[str, Any],
) -> int:
    report = _load_json_object(match_path / "global_identity_report.json") or {}
    configured = (
        ((report.get("parameters") or {}).get("global_identity_parameters") or {}).get(
            "players_per_team"
        )
        or frame_document.get("active_players_per_team")
        or int(frame_document.get("target_players") or 0) // 2
        or DEFAULT_ACTIVE_PLAYERS_PER_TEAM
    )
    return min(DEFAULT_ACTIVE_PLAYERS_PER_TEAM, max(1, int(configured)))


def _load_json_object(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None
