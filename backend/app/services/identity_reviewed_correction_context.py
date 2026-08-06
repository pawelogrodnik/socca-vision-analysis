from __future__ import annotations

"""Read models and deterministic context for whole-subject corrections."""

from collections import defaultdict
import json
from pathlib import Path
from typing import Any

from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_reviewed_slot_registry import build_reviewed_slot_registry
from app.services.identity_reviewed_slot_review import load_reviewed_slot_assignments
from app.services.identity_roster_subject_review_store import (
    REVIEW_ARTIFACT_FILENAME,
    REVIEW_DECISIONS_FILENAME,
)


def reviewed_correction_context(
    match_path: Path,
    match_doc: dict[str, Any],
    candidate_subject_id: str,
) -> dict[str, Any]:
    context = build_subject_context(match_path, candidate_subject_id)
    team_label = context["team_label"]
    roster_options = [
        player for player in match_roster(match_doc) if player["team_label"] == team_label
    ]
    slot_document = load_reviewed_slot_assignments(match_path)
    registry = build_reviewed_slot_registry(match_path, slot_document)
    return {
        "candidate_subject_id": candidate_subject_id,
        "team_label": team_label,
        "tracklet_ids": context["tracklet_ids"],
        "review_card_key": review_card_key(match_path, candidate_subject_id),
        "roster_options": roster_options,
        "slot_options": [
            registry[key]
            for key in sorted(registry)
            if registry[key].get("team_label") == team_label
        ],
        "current_decision": current_reviewed_decision(
            match_path, candidate_subject_id
        ),
        "semantic_decision_digest": reviewed_decisions_semantic_digest(match_path),
    }


def reviewed_decisions_semantic_digest(match_path: Path) -> str:
    roster = load_optional(match_path / REVIEW_DECISIONS_FILENAME)
    slots = load_reviewed_slot_assignments(match_path)
    return canonical_digest(
        {
            "roster": sorted(
                (
                    {
                        "candidate_subject_id": row.get("candidate_subject_id"),
                        "decision": row.get("decision"),
                        "player_id": row.get("player_id"),
                    }
                    for row in roster.get("decisions") or []
                ),
                key=lambda row: str(row.get("candidate_subject_id") or ""),
            ),
            "slots": sorted(
                (
                    {
                        "candidate_subject_id": row.get("candidate_subject_id"),
                        "action": row.get("action"),
                        "stable_slot_id": row.get("stable_slot_id"),
                        "team_label": row.get("team_label"),
                    }
                    for row in slots.get("decisions") or []
                ),
                key=lambda row: str(row.get("candidate_subject_id") or ""),
            ),
        }
    )


def build_subject_context(match_path: Path, subject_id: str) -> dict[str, Any]:
    candidate_document = load_required(match_path / "identity_candidate_shadow.json")
    tracklets_document = load_required(match_path / "tracklets.json")
    tracklets = {
        str(row.get("tracklet_id")): row
        for row in tracklets_document.get("tracklets") or []
        if row.get("tracklet_id")
    }
    subjects: dict[str, set[str]] = defaultdict(set)
    memberships: dict[str, set[str]] = defaultdict(set)
    for row in candidate_document.get("subjects") or []:
        current_id = str(row.get("candidate_subject_id") or "")
        if not current_id:
            continue
        tracklet_ids = {str(value) for value in row.get("tracklet_ids") or []}
        subjects[current_id].update(tracklet_ids)
        for tracklet_id in tracklet_ids:
            memberships[tracklet_id].add(current_id)
    if subject_id not in subjects:
        raise ValueError(f"Unknown candidate_subject_id: {subject_id or '<missing>'}")
    if any(len(memberships[tracklet_id]) > 1 for tracklet_id in subjects[subject_id]):
        raise ValueError(f"Ambiguous candidate subject membership: {subject_id}")
    teams = {
        str(tracklets.get(tracklet_id, {}).get("team_label") or "U")
        for tracklet_id in subjects[subject_id]
    }
    if len(teams) > 1:
        raise ValueError(f"Mixed-team candidate subject: {subject_id}")
    return {
        "candidate_subject_id": subject_id,
        "tracklet_ids": sorted(subjects[subject_id]),
        "team_label": next(iter(teams), "U"),
    }


def review_card_key(match_path: Path, subject_id: str) -> str | None:
    artifact = load_optional(match_path / REVIEW_ARTIFACT_FILENAME)
    keys = sorted(
        {
            str(row.get("review_card_key"))
            for row in artifact.get("cards") or []
            if str(row.get("candidate_subject_id") or "") == subject_id
            and row.get("review_card_key")
        }
    )
    if len(keys) > 1:
        raise ValueError(f"Ambiguous whole-subject review cards: {subject_id}")
    return keys[0] if keys else None


def current_reviewed_decision(
    match_path: Path,
    subject_id: str,
) -> dict[str, Any] | None:
    slots = load_reviewed_slot_assignments(match_path)
    slot_decision = next(
        (
            dict(row)
            for row in slots.get("decisions") or []
            if str(row.get("candidate_subject_id") or "") == subject_id
        ),
        None,
    )
    if slot_decision:
        return slot_decision
    roster = load_optional(match_path / REVIEW_DECISIONS_FILENAME)
    roster_decision = next(
        (
            dict(row)
            for row in roster.get("decisions") or []
            if str(row.get("candidate_subject_id") or "") == subject_id
        ),
        None,
    )
    if not roster_decision:
        return None
    return {
        **roster_decision,
        "action": (
            "assign_roster_player"
            if roster_decision.get("decision") != "mark_unresolved"
            else "unresolved"
        ),
    }


def match_roster(match_doc: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for index, team in enumerate(match_doc.get("teams") or []):
        team_label = str(team.get("team_label") or chr(ord("A") + index))
        for player in team.get("players") or []:
            player_id = str(player.get("id") or "")
            if player_id:
                output.append(
                    {
                        "player_id": player_id,
                        "player_name": str(player.get("name") or player_id),
                        "roster_number": player.get("number"),
                        "team_label": team_label,
                    }
                )
    return sorted(
        output,
        key=lambda row: (
            row["team_label"],
            str(row["player_name"]).casefold(),
            row["player_id"],
        ),
    )


def load_required(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path.name)
    return json.loads(path.read_text(encoding="utf-8"))


def load_optional(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
