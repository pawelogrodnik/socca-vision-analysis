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
from app.services.identity_reviewed_mixed_store import (
    FILENAME as MIXED_PLAYERS_FILENAME,
    inline_temporal_split_for_source,
    mixed_case_for_subject,
    render_mixed_review_evidence,
    temporal_evidence_for_observations,
)
from app.services.identity_reviewed_material_continuity import (
    load_material_continuity_decisions,
)
from app.services.identity_reviewed_segments import (
    build_segment_review_document,
    load_segment_review,
    load_segment_decisions,
    target_for_id,
)
from app.services.identity_reviewed_action_scope import (
    reviewed_identity_action_capabilities,
    scope_copy,
)
from app.services.identity_reviewed_review_source import resolve_review_source
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
    if candidate_subject_id.startswith("continuity:"):
        return _material_continuity_correction_context(
            match_path,
            match_doc,
            candidate_subject_id,
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
    source = resolve_review_source(
        match_path,
        match_doc,
        candidate_subject_id=candidate_subject_id,
    )
    scope_unit = {
        "scope_kind": "whole_subject",
        "detected_observation_count": source["detected_observation_count"],
    }
    temporal_evidence = _source_temporal_evidence(match_path, match_doc, source)
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
        "source_ownership_digest": source["source_ownership_digest"],
        "detected_observation_count": source["detected_observation_count"],
        "action_capabilities": reviewed_identity_action_capabilities(scope_unit),
        "scope_copy": scope_copy("whole_subject"),
        "frame_ranges": [],
        "visual_evidence": temporal_evidence,
        "temporal_split": _inline_temporal_split_context(match_path, source),
        "legacy_suggestion": None,
    }


def _material_continuity_correction_context(
    match_path: Path,
    match_doc: dict[str, Any],
    continuity_group_id: str,
) -> dict[str, Any]:
    """Return a server-authoritative context for a grouped safe gap."""
    # Import here to avoid a progress -> correction-context module cycle.
    from app.services.identity_reviewed_progress import build_reviewed_identity_progress

    progress = build_reviewed_identity_progress(match_path, match_doc)
    unit = next(
        (
            row
            for row in progress.get("next_cases") or []
            if str(row.get("candidate_subject_id") or "") == continuity_group_id
            and row.get("scope_kind") == "material_continuity"
        ),
        None,
    )
    if not isinstance(unit, dict):
        raise ValueError(f"Unknown material continuity case: {continuity_group_id}")
    team_label = str(unit.get("effective_team_label") or "A").upper()
    if team_label not in {"A", "B"}:
        raise ValueError("Material continuity case has no safe team")
    candidate_document = load_required(match_path / "identity_candidate_shadow.json")
    slot_document = load_reviewed_slot_assignments(match_path)
    registry = build_materialized_reviewed_slot_registry(candidate_document, slot_document)
    if not registry:
        registry = build_reviewed_slot_registry(match_path, slot_document)
    source = resolve_review_source(
        match_path,
        match_doc,
        candidate_subject_id=continuity_group_id,
        continuity_group_id=continuity_group_id,
        source_ownership_digest=str(unit.get("source_ownership_digest") or ""),
    )
    temporal_evidence = _source_temporal_evidence(
        match_path,
        match_doc,
        source,
    )
    return {
        "candidate_subject_id": continuity_group_id,
        "review_target_id": None,
        "scope_kind": "material_continuity",
        "team_label": team_label,
        "source_team_label": team_label,
        "effective_team_label": team_label,
        "available_team_labels": ["A", "B"],
        "tracklet_ids": list(unit.get("tracklet_ids") or []),
        "continuity_subject_ids": list(unit.get("continuity_subject_ids") or []),
        "continuity_group_id": continuity_group_id,
        "review_card_key": None,
        "roster_options": match_roster(match_doc),
        "slot_options": [
            registry[key]
            for key in sorted(registry)
            if registry[key].get("team_label") in {"A", "B"}
        ],
        "current_decision": unit.get("current_decision"),
        "semantic_decision_digest": reviewed_decisions_semantic_digest(match_path),
        "source_ownership_digest": unit.get("source_ownership_digest"),
        "frame_ranges": list(unit.get("frame_ranges") or []),
        "frame_start": unit.get("frame_start"),
        "frame_end": unit.get("frame_end"),
        "detected_observation_count": unit.get("detected_observation_count"),
        "visual_evidence": temporal_evidence,
        "temporal_split": _inline_temporal_split_context(match_path, source),
        "legacy_suggestion": None,
        "action_capabilities": reviewed_identity_action_capabilities(unit),
        "scope_copy": scope_copy("material_continuity"),
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
    slot_document = load_reviewed_slot_assignments(match_path)
    registry = build_reviewed_slot_registry(match_path, slot_document)
    source = resolve_review_source(
        match_path,
        match_doc,
        candidate_subject_id=candidate_subject_id,
        review_target_id=review_target_id,
        source_ownership_digest=str(target.get("source_ownership_digest") or ""),
    )
    temporal_evidence = _source_temporal_evidence(
        match_path,
        match_doc,
        source,
    )
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
        "slot_options": [
            registry[key]
            for key in sorted(registry)
            if registry[key].get("team_label") in available_team_labels
        ],
        "current_decision": target.get("current_decision"),
        "semantic_decision_digest": reviewed_decisions_semantic_digest(match_path),
        "source_ownership_digest": target.get("source_ownership_digest"),
        "frame_ranges": list(target.get("frame_ranges") or []),
        "frame_start": target.get("frame_start"),
        "frame_end": target.get("frame_end"),
        "detected_observation_count": target.get("detected_observation_count"),
        "visual_evidence": temporal_evidence,
        "temporal_split": _inline_temporal_split_context(match_path, source),
        "legacy_suggestion": target.get("legacy_suggestion"),
        "action_capabilities": reviewed_identity_action_capabilities(target),
        "scope_copy": scope_copy("canonical_segment"),
    }


def _source_temporal_evidence(
    match_path: Path,
    match_doc: dict[str, Any],
    source: dict[str, Any],
) -> dict[str, Any]:
    crops = temporal_evidence_for_observations(
        str(source["candidate_subject_id"]),
        list(source["observations"]),
        limit=12,
    )
    try:
        render_mixed_review_evidence(
            match_path,
            match_doc,
            {"cases": [{"temporal_evidence": {"anchor_crops": crops}}]},
        )
    except FileNotFoundError:
        # Context remains useful to an API consumer even if an old local match
        # no longer has its source video. The UI will show its normal image
        # failure state instead of failing the whole correction card.
        pass
    return {
        "kind": "identity_continuity",
        "status": "ready" if crops else "missing",
        "selected_crop_count": len(crops),
        "anchor_crops": crops,
    }


def _inline_temporal_split_context(
    match_path: Path,
    source: dict[str, Any],
) -> dict[str, Any] | None:
    """Expose a saved split as editable operator state, not technical JSON."""
    case = inline_temporal_split_for_source(match_path, source)
    if case is None:
        return None
    assignments = [
        {
            key: row.get(key)
            for key in ("action", "player_id", "stable_slot_id", "team_label")
            if row.get(key) is not None
        }
        for row in case.get("segment_assignments") or []
        if isinstance(row, dict)
    ]
    return {
        "resolution_status": case.get("resolution_status"),
        "split_after_frames": list(case.get("split_after_frames") or []),
        "split_semantic_digest": case.get("split_semantic_digest"),
        "segment_assignments": assignments,
    }


def reviewed_decisions_semantic_digest(match_path: Path) -> str:
    roster = load_optional(match_path / REVIEW_DECISIONS_FILENAME)
    slots = load_reviewed_slot_assignments(match_path)
    segments = load_segment_decisions(match_path)
    material = load_material_continuity_decisions(match_path)
    mixed = load_optional(match_path / MIXED_PLAYERS_FILENAME)
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
            "material_continuity": sorted(
                (
                    {
                        "continuity_group_id": row.get("continuity_group_id"),
                        "source_ownership_digest": row.get("source_ownership_digest"),
                        "action": row.get("action"),
                        "player_id": row.get("player_id"),
                        "owned_observations": row.get("owned_observations") or [],
                    }
                    for row in material.get("decisions") or []
                ),
                key=lambda row: str(row.get("continuity_group_id") or ""),
            ),
            "mixed_players": sorted(
                (
                    {
                        "case_id": row.get("case_id"),
                        "candidate_subject_id": row.get("candidate_subject_id"),
                        "original_issue": row.get("original_issue"),
                        "mixed_hint": row.get("mixed_hint"),
                        "resolution_status": row.get("resolution_status"),
                        "source_subject_digest": row.get("source_subject_digest"),
                        "split_after_frames": row.get("split_after_frames") or [],
                        "segment_target_ids": row.get("segment_target_ids") or [],
                        "split_semantic_digest": row.get("split_semantic_digest"),
                        "source": row.get("source") or {},
                    }
                    for row in mixed.get("cases") or []
                ),
                key=lambda row: (str(row.get("case_id") or ""), str(row.get("candidate_subject_id") or "")),
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
    mixed = mixed_case_for_subject(match_path, subject_id)
    if mixed:
        return {
            "candidate_subject_id": subject_id,
            "action": "mixed_players",
            "mixed_hint": mixed.get("mixed_hint"),
            "resolution_status": mixed.get("resolution_status"),
        }
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
