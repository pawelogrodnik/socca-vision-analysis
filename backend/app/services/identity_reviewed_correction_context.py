from __future__ import annotations

"""Read models and deterministic context for whole-subject corrections."""

from collections import defaultdict
import json
from pathlib import Path
from typing import Any

from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_reviewed_slot_registry import (
    build_materialized_reviewed_slot_registry,
    build_reviewed_slot_registry,
)
from app.services.identity_reviewed_slot_review import load_reviewed_slot_assignments
from app.services.identity_reviewed_segments import (
    build_segment_review_document,
    load_segment_review,
    load_segment_decisions,
    target_for_id,
)
from app.services.identity_roster_subject_review_store import (
    REVIEW_ARTIFACT_FILENAME,
    REVIEW_DECISIONS_FILENAME,
)


def reviewed_correction_context(
    match_path: Path,
    match_doc: dict[str, Any],
    candidate_subject_id: str,
    review_target_id: str | None = None,
) -> dict[str, Any]:
    if review_target_id:
        return _segment_correction_context(
            match_path,
            match_doc,
            candidate_subject_id,
            review_target_id,
        )
    candidate_document = load_required(match_path / "identity_candidate_shadow.json")
    context = build_materialized_subject_context(
        candidate_document,
        candidate_subject_id,
    )
    if context["team_label"] == "U" and not _candidate_has_materialized_team(
        candidate_document,
        candidate_subject_id,
    ):
        context = build_subject_context(match_path, candidate_subject_id)
    source_team_label = context["team_label"]
    current = current_reviewed_decision(match_path, candidate_subject_id)
    effective_team_label = str(
        (current or {}).get("team_label") or source_team_label
    ).upper()
    available_team_labels = ["A", "B"] if source_team_label == "U" else [source_team_label]
    # A certain named-player choice is authoritative for both identity and team.
    # Keep slot/team-only actions scoped to the detected team, but expose both
    # rosters so the operator can correct a wrong automatic team assignment in
    # one action.
    roster_options = match_roster(match_doc)
    slot_document = load_reviewed_slot_assignments(match_path)
    registry = build_materialized_reviewed_slot_registry(
        candidate_document,
        slot_document,
    )
    if not registry:
        registry = build_reviewed_slot_registry(match_path, slot_document)
    return {
        "candidate_subject_id": candidate_subject_id,
        "review_target_id": None,
        "scope_kind": "whole_subject",
        "team_label": source_team_label,
        "source_team_label": source_team_label,
        "effective_team_label": effective_team_label,
        "available_team_labels": available_team_labels,
        "tracklet_ids": context["tracklet_ids"],
        "review_card_key": review_card_key(match_path, candidate_subject_id),
        "roster_options": roster_options,
        "slot_options": [
            registry[key]
            for key in sorted(registry)
            if registry[key].get("team_label") in available_team_labels
        ],
        "current_decision": current,
        "semantic_decision_digest": reviewed_decisions_semantic_digest(match_path),
        "source_ownership_digest": None,
        "frame_ranges": [],
        "visual_evidence": None,
        "legacy_suggestion": None,
    }


def _segment_correction_context(
    match_path: Path,
    match_doc: dict[str, Any],
    candidate_subject_id: str,
    review_target_id: str,
) -> dict[str, Any]:
    review = load_segment_review(match_path)
    if not review:
        review = build_segment_review_document(match_path, match_doc)
    target = target_for_id(review, review_target_id)
    if target is None or str(target.get("candidate_subject_id")) != candidate_subject_id:
        raise ValueError(f"Unknown review_target_id: {review_target_id}")
    team_label = str(target.get("source_team_label") or "U")
    available_team_labels = ["A", "B"] if team_label == "U" else [team_label]
    roster_options = match_roster(match_doc)
    return {
        "candidate_subject_id": candidate_subject_id,
        "review_target_id": review_target_id,
        "scope_kind": "canonical_segment",
        "team_label": team_label,
        "source_team_label": team_label,
        "effective_team_label": str(target.get("effective_team_label") or team_label),
        "available_team_labels": available_team_labels,
        "tracklet_ids": list(target.get("tracklet_ids") or []),
        "review_card_key": None,
        "roster_options": roster_options,
        "slot_options": [],
        "current_decision": target.get("current_decision"),
        "semantic_decision_digest": reviewed_decisions_semantic_digest(match_path),
        "source_ownership_digest": target.get("source_ownership_digest"),
        "frame_ranges": list(target.get("frame_ranges") or []),
        "frame_start": target.get("frame_start"),
        "frame_end": target.get("frame_end"),
        "detected_observation_count": target.get("detected_observation_count"),
        "visual_evidence": target.get("visual_evidence") or {},
        "legacy_suggestion": target.get("legacy_suggestion"),
    }


def reviewed_decisions_semantic_digest(match_path: Path) -> str:
    roster = load_optional(match_path / REVIEW_DECISIONS_FILENAME)
    slots = load_reviewed_slot_assignments(match_path)
    segments = load_segment_decisions(match_path)
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
                        "player_id": row.get("player_id"),
                        "team_label": row.get("team_label"),
                    }
                    for row in slots.get("decisions") or []
                ),
                key=lambda row: str(row.get("candidate_subject_id") or ""),
            ),
            "segments": sorted(
                (
                    {
                        "review_target_id": row.get("review_target_id"),
                        "source_ownership_digest": row.get("source_ownership_digest"),
                        "action": row.get("action"),
                        "player_id": row.get("player_id"),
                        "team_label": row.get("team_label"),
                    }
                    for row in segments.get("decisions") or []
                ),
                key=lambda row: str(row.get("review_target_id") or ""),
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


def build_materialized_subject_context(
    candidate_document: dict[str, Any],
    subject_id: str,
) -> dict[str, Any]:
    """Validate a correction subject without reparsing full tracklets."""
    subjects: dict[str, set[str]] = defaultdict(set)
    memberships: dict[str, set[str]] = defaultdict(set)
    teams: dict[str, set[str]] = defaultdict(set)
    for row in candidate_document.get("subjects") or []:
        current_id = str(row.get("candidate_subject_id") or "")
        if not current_id:
            continue
        tracklet_ids = {str(value) for value in row.get("tracklet_ids") or []}
        subjects[current_id].update(tracklet_ids)
        for tracklet_id in tracklet_ids:
            memberships[tracklet_id].add(current_id)
        team_label = str(row.get("team_label") or "U").upper()
        if team_label in {"A", "B"}:
            teams[current_id].add(team_label)
    if subject_id not in subjects:
        raise ValueError(f"Unknown candidate_subject_id: {subject_id or '<missing>'}")
    if any(len(memberships[tracklet_id]) > 1 for tracklet_id in subjects[subject_id]):
        raise ValueError(f"Ambiguous candidate subject membership: {subject_id}")
    subject_teams = teams.get(subject_id) or set()
    if len(subject_teams) > 1:
        raise ValueError(f"Mixed-team candidate subject: {subject_id}")
    return {
        "candidate_subject_id": subject_id,
        "tracklet_ids": sorted(subjects[subject_id]),
        "team_label": next(iter(subject_teams), "U"),
    }


def _candidate_has_materialized_team(
    candidate_document: dict[str, Any],
    subject_id: str,
) -> bool:
    return any(
        str(row.get("candidate_subject_id") or "") == subject_id
        and str(row.get("team_label") or "").upper() in {"A", "B", "U"}
        for row in candidate_document.get("subjects") or []
    )


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
