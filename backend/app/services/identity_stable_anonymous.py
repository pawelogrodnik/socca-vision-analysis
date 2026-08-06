from __future__ import annotations

"""Map technical identity fragments onto bounded, canonical match slots."""

from collections import Counter, defaultdict
from pathlib import Path
import re
from typing import Any

from app.services.identity_reviewed_slot_registry import (
    build_reviewed_slot_registry,
)


DEFAULT_MAX_SUBJECTS_PER_TEAM = 14
DEFAULT_ACTIVE_PLAYERS_PER_TEAM = 7
_STABLE_LABEL = re.compile(r"^(?P<team>[AB])(?P<number>\d+)(?:~\d+)?$")


def resolve_stable_anonymous_entities(
    match_path: Path,
    tracklets: dict[str, dict[str, Any]],
    candidate_document: dict[str, Any],
    manual_document: dict[str, Any] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if manual_document is None:
        manual_document = _optional(
            match_path / "reviewed_identity_slot_assignments.json"
        )
    global_document = _optional(match_path / "global_identity.json")
    stable_document = _optional(match_path / "stable_players.json")
    gallery_document = _optional(match_path / "identity_review_gallery.json")
    max_subjects = min(
        DEFAULT_MAX_SUBJECTS_PER_TEAM,
        max(
            1,
            int(
                (global_document.get("parameters") or {}).get(
                    "max_subjects_per_team"
                )
                or DEFAULT_MAX_SUBJECTS_PER_TEAM
            ),
        ),
    )
    active_players_per_team = min(
        DEFAULT_ACTIVE_PLAYERS_PER_TEAM,
        max(
            1,
            int(
                (global_document.get("parameters") or {}).get(
                    "players_per_team"
                )
                or DEFAULT_ACTIVE_PLAYERS_PER_TEAM
            ),
        ),
    )
    subject_membership = _subject_membership(candidate_document)
    candidates = {
        str(row.get("candidate_subject_id")): row
        for row in candidate_document.get("subjects") or []
        if row.get("candidate_subject_id")
    }
    claims_by_tracklet = _all_anchor_claims(
        global_document,
        stable_document,
        gallery_document,
        candidate_document,
    )
    manual_by_subject = {
        str(row.get("candidate_subject_id")): row
        for row in manual_document.get("decisions") or []
        if row.get("candidate_subject_id")
    }
    canonical_slots = _canonical_slots(global_document, stable_document)
    reviewed_slot_registry = build_reviewed_slot_registry(
        match_path,
        manual_document,
    )
    subject_teams = _subject_teams(subject_membership, tracklets)
    manual_new_slots, manual_new_rejections = _allocate_manual_new_slots(
        manual_by_subject,
        candidates,
        subject_membership,
        tracklets,
        global_document,
        set(reviewed_slot_registry),
        max_subjects=max_subjects,
        active_players_per_team=active_players_per_team,
    )
    resolved: dict[str, dict[str, Any]] = {}

    for tracklet_id, tracklet in sorted(tracklets.items()):
        team = str(tracklet.get("team_label") or "U")
        subjects = sorted(subject_membership.get(tracklet_id) or [])
        subject_id = subjects[0] if len(subjects) == 1 else None
        manual = manual_by_subject.get(subject_id or "")
        claims = claims_by_tracklet.get(tracklet_id, [])
        claim_labels = sorted({str(row["stable_slot_id"]) for row in claims})
        blockers: list[str] = []
        manual_action = str((manual or {}).get("action") or "") or None
        stable_slot_id: str | None = None
        anchor_source: str | None = None
        anchor_status = "unanchored"

        if len(subjects) > 1:
            blockers.append("ambiguous_candidate_subject_membership")
        if any(len(subject_teams[subject]) > 1 for subject in subjects):
            blockers.append("mixed_team_candidate_subject")
        detected_evidence_count = _detected_observations(tracklet)

        if manual_action == "assign_existing_slot" and not blockers:
            requested = str(manual.get("stable_slot_id") or "")
            if requested not in reviewed_slot_registry:
                blockers.append("manual_stable_slot_not_found")
            elif str(reviewed_slot_registry[requested].get("team_label")) != team:
                blockers.append("manual_stable_slot_team_mismatch")
            else:
                stable_slot_id = requested
                anchor_source = "manual_review"
                anchor_status = "manual_existing_slot"
        elif manual_action == "create_new_stable_player" and not blockers:
            stable_slot_id = manual_new_slots.get(subject_id or "")
            rejection = manual_new_rejections.get(subject_id or "")
            if rejection:
                blockers.append(rejection)
            elif stable_slot_id:
                if _label_team(stable_slot_id) != team:
                    blockers.append("manual_new_player_team_mismatch")
                else:
                    anchor_source = "manual_new_player_confirmation"
                    anchor_status = "manual_new_slot"
        elif manual_action in {"referee", "false_detection", "team_unknown"}:
            stable_slot_id = None
            anchor_source = "manual_review"
            anchor_status = manual_action
            blockers = []
        elif not blockers:
            if len(claim_labels) > 1:
                blockers.append("conflicting_stable_anchor_sources")
                anchor_status = "conflicting_claims"
            elif len(claim_labels) == 1:
                candidate_slot = claim_labels[0]
                if candidate_slot not in canonical_slots:
                    blockers.append("stable_anchor_not_in_canonical_pool")
                elif not 1 <= _label_number(candidate_slot) <= max_subjects:
                    blockers.append("stable_anchor_exceeds_bounded_pool")
                elif team not in {"A", "B"}:
                    blockers.append("unknown_team_cannot_receive_stable_slot")
                elif _label_team(candidate_slot) != team:
                    blockers.append("stable_anchor_team_mismatch")
                else:
                    stable_slot_id = candidate_slot
                    sources = sorted({str(row["source"]) for row in claims})
                    anchor_source = sources[0] if len(sources) == 1 else "canonical_consensus"
                    anchor_status = "anchored"

        if blockers:
            stable_slot_id = None
            anchor_status = "blocked"
        fallback_team = (
            "U"
            if manual_action == "team_unknown"
            else team
            if team in {"A", "B"}
            else "U"
        )
        fallback_label = stable_slot_id or f"{fallback_team}?"
        conflicted = bool(blockers)
        resolved[tracklet_id] = {
            "candidate_subject_id": subject_id,
            "candidate_subject_ids": subjects,
            "fragment_id": subject_id or f"tracklet:{tracklet_id}",
            "stable_anonymous_slot_id": stable_slot_id,
            "stable_anonymous_entity_id": stable_slot_id,
            "stable_anchor_source": anchor_source,
            "stable_anchor_claims": claims,
            "stable_anchor_status": anchor_status,
            "fallback_label": fallback_label,
            "unanchored": stable_slot_id is None,
            "ephemeral": detected_evidence_count > 0 and detected_evidence_count < 5,
            "requires_review": stable_slot_id is None
            and manual_action not in {"referee", "false_detection"},
            "detected_evidence_count": detected_evidence_count,
            "insufficient_evidence": detected_evidence_count == 0,
            "manual_action": manual_action,
            "hard_blockers": sorted(set(blockers)),
        }

    diagnostics = _diagnostics(
        resolved,
        candidates_total=len(candidates),
        max_subjects=max_subjects,
        active_players_per_team=active_players_per_team,
        manual_document=manual_document,
        reviewed_slot_registry=reviewed_slot_registry,
    )
    return resolved, diagnostics


def _all_anchor_claims(
    global_document: dict[str, Any],
    stable_document: dict[str, Any],
    gallery_document: dict[str, Any],
    candidate_document: dict[str, Any],
) -> dict[str, list[dict[str, str]]]:
    output: dict[str, list[dict[str, str]]] = defaultdict(list)
    sources = (
        ("global_identity", _players_map(global_document, "slots")),
        ("stable_players", _players_map(stable_document, "players")),
        ("identity_review_gallery", _gallery_map(gallery_document)),
        ("candidate_shadow", _candidate_map(candidate_document)),
    )
    for source, values in sources:
        for tracklet_id, labels in values.items():
            for label in sorted(labels):
                output[tracklet_id].append(
                    {"source": source, "stable_slot_id": label}
                )
    return {
        key: sorted(value, key=lambda row: (row["source"], row["stable_slot_id"]))
        for key, value in output.items()
    }


def _allocate_manual_new_slots(
    manual_by_subject: dict[str, dict[str, Any]],
    candidates: dict[str, dict[str, Any]],
    subject_membership: dict[str, set[str]],
    tracklets: dict[str, dict[str, Any]],
    global_document: dict[str, Any],
    available_slots: set[str],
    *,
    max_subjects: int,
    active_players_per_team: int,
) -> tuple[dict[str, str], dict[str, str]]:
    allocated: dict[str, str] = {}
    rejected: dict[str, str] = {}
    used = set(available_slots)
    manual_visible: dict[tuple[int, str], int] = Counter()
    canonical_visible = _canonical_visible_counts(global_document)
    for subject_id, decision in sorted(
        manual_by_subject.items(),
        key=lambda item: _manual_new_allocation_order(item[0], item[1]),
    ):
        if decision.get("action") != "create_new_stable_player":
            continue
        team = str(decision.get("team_label") or "")
        if team not in {"A", "B"}:
            rejected[subject_id] = "manual_new_player_team_missing"
            continue
        subject = candidates.get(subject_id)
        if subject is None:
            rejected[subject_id] = "manual_new_player_subject_missing"
            continue
        tracklet_ids = [
            tracklet_id
            for tracklet_id, subjects in subject_membership.items()
            if subject_id in subjects
        ]
        observed_teams = {
            str(tracklets.get(tracklet_id, {}).get("team_label") or "U")
            for tracklet_id in tracklet_ids
        }
        if observed_teams != {team}:
            rejected[subject_id] = "manual_new_player_team_mismatch"
            continue
        frames = {
            frame
            for tracklet_id in tracklet_ids
            for frame in _detected_frames(tracklets.get(tracklet_id, {}))
        }
        if not frames:
            rejected[subject_id] = "manual_new_player_requires_detected_evidence"
            continue
        if any(
            canonical_visible.get((frame, team), 0)
            + manual_visible.get((frame, team), 0)
            >= active_players_per_team
            for frame in frames
        ):
            rejected[subject_id] = "manual_new_player_active_team_cap_exceeded"
            continue
        persisted_slot = _normalize_label(decision.get("stable_slot_id"))
        if persisted_slot:
            slot = persisted_slot if persisted_slot in available_slots else None
        else:
            slot = next(
                (
                    f"{team}{number:02d}"
                    for number in range(1, max_subjects + 1)
                    if f"{team}{number:02d}" not in used
                ),
                None,
            )
        if slot is None:
            rejected[subject_id] = (
                "manual_new_player_slot_not_found"
                if persisted_slot
                else "manual_new_player_bounded_pool_exhausted"
            )
            continue
        if _label_team(slot) != team:
            rejected[subject_id] = "manual_new_player_team_mismatch"
            continue
        used.add(slot)
        allocated[subject_id] = slot
        for frame in frames:
            manual_visible[(frame, team)] += 1
    return allocated, rejected


def _manual_new_allocation_order(
    subject_id: str,
    decision: dict[str, Any],
) -> tuple[int, str, str, str]:
    slot_id = _normalize_label(decision.get("stable_slot_id"))
    return (
        0 if slot_id else 1,
        slot_id or "",
        str(decision.get("reviewed_at") or ""),
        subject_id,
    )


def _canonical_visible_counts(document: dict[str, Any]) -> Counter[tuple[int, str]]:
    counts: Counter[tuple[int, str]] = Counter()
    authoritative_keys: set[tuple[int, str]] = set()
    for row in document.get("frames") or []:
        frame = int(row.get("frame") or 0)
        for team, key in (("A", "active_team_a"), ("B", "active_team_b")):
            if row.get(key) is None:
                continue
            counts[(frame, team)] = int(row[key])
            authoritative_keys.add((frame, team))
    for slot in document.get("slots") or []:
        team = str(slot.get("team_label") or "U")
        counted_frames: set[int] = set()
        for row in (
            slot.get("overlay_positions")
            or slot.get("history")
            or slot.get("positions_m")
            or slot.get("trajectory_m")
            or []
        ):
            if str(row.get("source") or row.get("status") or "detected") != "detected":
                continue
            frame = int(row.get("frame") or 0)
            if frame in counted_frames or (frame, team) in authoritative_keys:
                continue
            counted_frames.add(frame)
            counts[(frame, team)] += 1
    return counts


def _canonical_slots(
    global_document: dict[str, Any], stable_document: dict[str, Any]
) -> set[str]:
    return {
        label
        for document, key in ((global_document, "slots"), (stable_document, "players"))
        for row in document.get(key) or []
        if (label := _normalize_label(row.get("stable_player_id") or row.get("slot_id")))
    }


def _subject_membership(document: dict[str, Any]) -> dict[str, set[str]]:
    output: dict[str, set[str]] = defaultdict(set)
    for subject in document.get("subjects") or []:
        subject_id = str(subject.get("candidate_subject_id") or "")
        for tracklet_id in subject.get("tracklet_ids") or []:
            if subject_id:
                output[str(tracklet_id)].add(subject_id)
    return output


def _subject_teams(
    membership: dict[str, set[str]], tracklets: dict[str, dict[str, Any]]
) -> dict[str, set[str]]:
    output: dict[str, set[str]] = defaultdict(set)
    for tracklet_id, subjects in membership.items():
        team = str(tracklets.get(tracklet_id, {}).get("team_label") or "U")
        for subject in subjects:
            output[subject].add(team)
    return output


def _players_map(document: dict[str, Any], key: str) -> dict[str, set[str]]:
    output: dict[str, set[str]] = defaultdict(set)
    for player in document.get(key) or []:
        label = _normalize_label(
            player.get("stable_player_id")
            or player.get("slot_id")
            or player.get("stable_subject_id")
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


def _label_number(label: str) -> int:
    match = re.search(r"(\d+)$", label)
    return int(match.group(1)) if match else 0


def _detected_observations(tracklet: dict[str, Any]) -> int:
    return len(_detected_frames(tracklet))


def _detected_frames(tracklet: dict[str, Any]) -> set[int]:
    return {
        int(row.get("frame") or 0)
        for row in tracklet.get("positions_m") or []
        if str(row.get("status") or "detected") == "detected"
    }


def _diagnostics(
    resolved: dict[str, dict[str, Any]],
    *,
    candidates_total: int,
    max_subjects: int,
    active_players_per_team: int,
    manual_document: dict[str, Any],
    reviewed_slot_registry: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    slots = {
        str(row["stable_anonymous_slot_id"])
        for row in resolved.values()
        if row.get("stable_anonymous_slot_id")
    }
    by_team = Counter(_label_team(value) for value in slots)
    manual = Counter(str(row.get("action") or "") for row in manual_document.get("decisions") or [])
    return {
        "candidate_subjects_total": candidates_total,
        "tracklets_total": len(resolved),
        "stable_anonymous_entities_total": len(slots),
        "stable_anonymous_entities_by_team": dict(sorted(by_team.items())),
        "unanchored_fragments": sum(bool(row["unanchored"]) for row in resolved.values()),
        "automatic_permanent_allocations": 0,
        "manual_assignments": manual["assign_existing_slot"],
        "manual_new_player_allocations": len(
            {
                row.get("candidate_subject_id")
                for row in resolved.values()
                if row.get("stable_anchor_source") == "manual_new_player_confirmation"
            }
        ),
        "reviewed_slot_registry_entries": len(reviewed_slot_registry),
        "manual_reviewed_slot_registry_entries": sum(
            row.get("source") == "manual_new_player_confirmation"
            for row in reviewed_slot_registry.values()
        ),
        "orphaned_manual_reviewed_slots": sum(
            row.get("source") == "manual_new_player_confirmation"
            and row.get("status") == "orphaned"
            for row in reviewed_slot_registry.values()
        ),
        "ephemeral_fragments": sum(bool(row["ephemeral"]) for row in resolved.values()),
        "conflicting_anchor_sources": sum("conflicting_stable_anchor_sources" in row["hard_blockers"] for row in resolved.values()),
        "max_subjects_per_team": max_subjects,
        "active_players_per_team": active_players_per_team,
        "highest_fallback_number_by_team": {
            team: max((_label_number(value) for value in slots if _label_team(value) == team), default=None)
            for team in ("A", "B")
        },
    }


def _optional(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    import json

    return json.loads(path.read_text(encoding="utf-8"))
