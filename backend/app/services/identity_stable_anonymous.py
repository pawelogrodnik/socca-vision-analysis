from __future__ import annotations

"""Resolve candidate fragments onto stable anonymous match-level entities."""

from collections import Counter, defaultdict
from pathlib import Path
import re
from typing import Any

from app.services.identity_review_gallery import MIN_TRACK_RUN_FRAMES


_STABLE_LABEL = re.compile(r"^(?P<team>[AB])(?P<number>\d+)(?:~\d+)?$")


def resolve_stable_anonymous_entities(
    match_path: Path,
    tracklets: dict[str, dict[str, Any]],
    candidate_document: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    subject_membership = _subject_membership(candidate_document)
    candidates = {
        str(row.get("candidate_subject_id")): row
        for row in candidate_document.get("subjects") or []
        if row.get("candidate_subject_id")
    }
    source_maps = [
        ("identity_review_gallery", _gallery_map(_optional(match_path / "identity_review_gallery.json"))),
        ("stable_players", _players_map(_optional(match_path / "stable_players.json"), "players")),
        ("global_identity", _players_map(_optional(match_path / "global_identity.json"), "slots")),
        ("candidate_shadow", _candidate_map(candidate_document)),
    ]
    used_by_team: dict[str, set[str]] = defaultdict(set)
    resolved: dict[str, dict[str, Any]] = {}
    rejected_anchors = 0
    ambiguous_memberships = 0
    subject_teams: dict[str, set[str]] = defaultdict(set)
    for tracklet_id, subjects in subject_membership.items():
        team = str(tracklets.get(tracklet_id, {}).get("team_label") or "U")
        if team != "U":
            for subject in subjects:
                subject_teams[subject].add(team)

    for tracklet_id, tracklet in sorted(tracklets.items()):
        team = str(tracklet.get("team_label") or "U")
        subjects = sorted(subject_membership.get(tracklet_id) or [])
        blockers: list[str] = []
        if len(subjects) > 1:
            blockers.append("ambiguous_candidate_subject_membership")
            ambiguous_memberships += 1
        if any(len(subject_teams[subject]) > 1 for subject in subjects):
            blockers.append("mixed_team_candidate_subject")
        anchor_label: str | None = None
        anchor_source: str | None = None
        anchor_candidates: list[str] = []
        for source_name, source_map in source_maps:
            values = sorted(source_map.get(tracklet_id) or [])
            if not values:
                continue
            anchor_candidates = values
            if len(values) == 1:
                anchor_label = values[0]
                anchor_source = source_name
            else:
                blockers.append("ambiguous_stable_anchor_membership")
            break
        rejected_anchor = None
        if anchor_label and _label_team(anchor_label) not in {team, "U"}:
            rejected_anchor = anchor_label
            anchor_label = None
            blockers.append("stable_anchor_team_mismatch")
            rejected_anchors += 1
        if anchor_label:
            used_by_team[team].add(anchor_label)
        subject_id = subjects[0] if len(subjects) == 1 else None
        resolved[tracklet_id] = {
            "candidate_subject_id": subject_id,
            "candidate_subject_ids": subjects,
            "stable_anonymous_entity_id": anchor_label,
            "stable_anchor_source": anchor_source,
            "stable_anchor_candidates": anchor_candidates,
            "rejected_stable_anchor": rejected_anchor,
            "hard_blockers": blockers,
            "ephemeral": False,
        }

    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for tracklet_id, row in resolved.items():
        if row["stable_anonymous_entity_id"]:
            continue
        team = str(tracklets[tracklet_id].get("team_label") or "U")
        subject = row["candidate_subject_id"] or f"tracklet:{tracklet_id}"
        groups[(team, subject)].append(tracklet_id)

    new_allocations = 0
    ephemeral = 0
    for (team, subject), tracklet_ids in sorted(
        groups.items(),
        key=lambda item: (item[0][0], _group_start(item[1], tracklets), item[0][1]),
    ):
        detected = sum(_detected_observations(tracklets[item]) for item in tracklet_ids)
        if detected < MIN_TRACK_RUN_FRAMES:
            label = f"{team}?"
            source = "ephemeral_short_fragment"
            is_ephemeral = True
            ephemeral += 1
        else:
            label = _next_label(team, used_by_team[team])
            used_by_team[team].add(label)
            source = "deterministic_new_allocation"
            is_ephemeral = False
            new_allocations += 1
        for tracklet_id in tracklet_ids:
            resolved[tracklet_id].update(
                stable_anonymous_entity_id=label,
                stable_anchor_source=source,
                ephemeral=is_ephemeral,
            )

    entity_subjects: dict[str, set[str]] = defaultdict(set)
    entity_tracklets: dict[str, set[str]] = defaultdict(set)
    for tracklet_id, row in resolved.items():
        if row["ephemeral"]:
            continue
        entity = str(row["stable_anonymous_entity_id"])
        entity_tracklets[entity].add(tracklet_id)
        entity_subjects[entity].update(row["candidate_subject_ids"])
    for entity, entity_tracklet_ids in entity_tracklets.items():
        values = sorted(entity_tracklet_ids)
        conflicted: set[str] = set()
        for index, left in enumerate(values):
            left_subjects = set(resolved[left]["candidate_subject_ids"])
            left_frames = _detected_frames(tracklets[left])
            for right in values[index + 1 :]:
                if left_subjects == set(resolved[right]["candidate_subject_ids"]):
                    continue
                if left_frames & _detected_frames(tracklets[right]):
                    conflicted.update({left, right})
        for tracklet_id in conflicted:
            resolved[tracklet_id]["hard_blockers"].append(
                "overlapping_candidate_subjects_share_stable_entity"
            )
    distribution = Counter(len(values) for values in entity_subjects.values())
    highest = {
        team: max((_label_number(value) for value in labels), default=None)
        for team, labels in sorted(used_by_team.items())
    }
    diagnostics = {
        "candidate_subjects_total": len(candidates),
        "stable_anonymous_entities_total": len(entity_tracklets),
        "stable_anonymous_entities_by_team": dict(sorted(Counter(_label_team(key) for key in entity_tracklets).items())),
        "candidate_subjects_per_stable_entity_distribution": {str(key): value for key, value in sorted(distribution.items())},
        "unanchored_candidate_subjects": len(groups),
        "new_stable_entity_allocations": new_allocations,
        "ephemeral_fragments": ephemeral,
        "ambiguous_tracklet_memberships": ambiguous_memberships,
        "rejected_cross_team_anchors": rejected_anchors,
        "highest_fallback_number_by_team": highest,
    }
    return resolved, diagnostics


def _subject_membership(document: dict[str, Any]) -> dict[str, set[str]]:
    output: dict[str, set[str]] = defaultdict(set)
    for subject in document.get("subjects") or []:
        subject_id = str(subject.get("candidate_subject_id") or "")
        for tracklet_id in subject.get("tracklet_ids") or []:
            if subject_id:
                output[str(tracklet_id)].add(subject_id)
    return output


def _players_map(document: dict[str, Any], key: str) -> dict[str, set[str]]:
    output: dict[str, set[str]] = defaultdict(set)
    for player in document.get(key) or []:
        label = _normalize_label(
            player.get("stable_player_id") or player.get("slot_id") or player.get("stable_subject_id")
        )
        if not label:
            continue
        for tracklet_id in player.get("tracklet_ids") or []:
            output[str(tracklet_id)].add(label)
    return output


def _gallery_map(document: dict[str, Any]) -> dict[str, set[str]]:
    output = _players_map(document, "players")
    for player in document.get("players") or []:
        label = _normalize_label(player.get("stable_player_id") or player.get("slot_id"))
        if not label:
            continue
        for stint in player.get("stints") or []:
            for tracklet_id in stint.get("tracklet_ids") or []:
                output[str(tracklet_id)].add(label)
    return output


def _candidate_map(document: dict[str, Any]) -> dict[str, set[str]]:
    output: dict[str, set[str]] = defaultdict(set)
    for subject in document.get("subjects") or []:
        label = _normalize_label(subject.get("candidate_player_id"))
        if not label:
            continue
        for tracklet_id in subject.get("tracklet_ids") or []:
            output[str(tracklet_id)].add(label)
    return output


def _normalize_label(value: Any) -> str | None:
    text = str(value or "").removeprefix("slot-")
    match = _STABLE_LABEL.fullmatch(text)
    return f"{match.group('team')}{int(match.group('number')):02d}" if match else None


def _label_team(label: str) -> str:
    return label[:1] if label else "U"


def _label_number(label: str) -> int | None:
    match = re.search(r"(\d+)$", label)
    return int(match.group(1)) if match else None


def _next_label(team: str, used: set[str]) -> str:
    number = 1
    while f"{team}{number:02d}" in used:
        number += 1
    return f"{team}{number:02d}"


def _detected_observations(tracklet: dict[str, Any]) -> int:
    positions = tracklet.get("positions_m") or []
    if not positions:
        start = int(tracklet.get("start_frame") or 0)
        end = int(tracklet.get("end_frame") or start)
        return max(MIN_TRACK_RUN_FRAMES, end - start + 1)
    return sum(
        str(row.get("status") or "detected") == "detected"
        for row in positions
    )


def _detected_frames(tracklet: dict[str, Any]) -> set[int]:
    return {
        int(row.get("frame") or 0)
        for row in tracklet.get("positions_m") or []
        if str(row.get("status") or "detected") == "detected"
    }


def _group_start(tracklet_ids: list[str], tracklets: dict[str, dict[str, Any]]) -> int:
    return min(int(tracklets[item].get("start_frame") or 0) for item in tracklet_ids)


def _optional(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    import json

    return json.loads(path.read_text(encoding="utf-8"))
