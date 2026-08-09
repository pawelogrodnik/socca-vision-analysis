from __future__ import annotations

"""Reviewed-only targets and decisions for frame-owned mixed tracklets."""

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.identity_canonical_ownership import global_observation_ownership
from app.services.identity_initial_audit_store import write_identity_json_atomic
from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_roster_anchor_crop_renderer import (
    render_identity_roster_anchor_crops,
)
from app.services.video import resolve_match_video_path


REVIEW_FILENAME = "reviewed_identity_segment_review.json"
DECISIONS_FILENAME = "reviewed_identity_segment_decisions.json"
SCHEMA_VERSION = "1.0.0"
ALLOWED_ACTIONS = frozenset(
    {
        "assign_roster_player",
        "assign_team",
        "referee",
        "false_detection",
        "team_unknown",
        "unresolved",
    }
)


def build_segment_review_document(
    match_path: Path,
    match_doc: dict[str, Any],
) -> dict[str, Any]:
    tracklets_doc = _load(match_path / "tracklets.json")
    subjects_doc = _load(match_path / "identity_candidate_shadow.json")
    global_doc = _load(match_path / "global_identity.json")
    card_doc = _load(match_path / "identity_roster_subject_review_shadow.json")
    decisions_doc = load_segment_decisions(match_path)
    tracklets = {
        str(row.get("tracklet_id")): row
        for row in tracklets_doc.get("tracklets") or []
        if row.get("tracklet_id")
    }
    subjects = {
        str(row.get("candidate_subject_id")): {
            str(value) for value in row.get("tracklet_ids") or []
        }
        for row in subjects_doc.get("subjects") or []
        if row.get("candidate_subject_id")
    }
    subject_by_tracklet: dict[str, set[str]] = defaultdict(set)
    for subject_id, tracklet_ids in subjects.items():
        for tracklet_id in tracklet_ids:
            subject_by_tracklet[tracklet_id].add(subject_id)

    ownership = global_observation_ownership(global_doc)
    owner_slots: dict[str, set[str]] = defaultdict(set)
    for row in ownership:
        owner_slots[str(row.get("tracklet_id") or "")].add(
            str(row.get("stable_slot_id") or "")
        )
    mixed_tracklets = {
        tracklet_id for tracklet_id, slots in owner_slots.items() if len(slots) > 1
    }
    cards = {
        str(row.get("candidate_subject_id")): row
        for row in card_doc.get("cards") or []
        if row.get("candidate_subject_id")
    }
    legacy = _legacy_roster_decisions(match_path)
    terminal_whole_subjects = _terminal_whole_subject_decisions(match_path)
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for claim in ownership:
        tracklet_id = str(claim.get("tracklet_id") or "")
        if tracklet_id not in mixed_tracklets:
            continue
        for subject_id in subject_by_tracklet.get(tracklet_id) or set():
            if not _requires_segment_review(
                cards.get(subject_id),
                legacy.get(subject_id),
                subject_id in terminal_whole_subjects,
            ):
                continue
            slot_id = str(claim.get("stable_slot_id") or "")
            team_label = str(claim.get("team_label") or slot_id[:1] or "U")
            groups[(subject_id, tracklet_id, slot_id, team_label)].append(claim)

    stored_decisions = {
        str(row.get("review_target_id")): row
        for row in decisions_doc.get("decisions") or []
        if row.get("review_target_id")
    }
    roster = _roster(match_doc)
    targets: list[dict[str, Any]] = []
    matched_decision_ids: set[str] = set()
    for (subject_id, tracklet_id, slot_id, team_label), claims in sorted(groups.items()):
        for frames in _contiguous_frame_runs(claims):
            ownership_payload = [
                {
                    "tracklet_id": tracklet_id,
                    "frame": frame,
                    "stable_slot_id": slot_id,
                    "team_label": team_label,
                }
                for frame in frames
            ]
            ownership_digest = canonical_digest(ownership_payload)
            target_id = _target_id(
                subject_id,
                tracklet_id,
                slot_id,
                team_label,
                frames,
            )
            evidence = _target_evidence(
                cards.get(subject_id),
                tracklets.get(tracklet_id) or {},
                frames,
                target_id,
            )
            decision = stored_decisions.get(target_id)
            decision_current = bool(
                decision
                and decision.get("source_ownership_digest") == ownership_digest
            )
            if decision is not None:
                matched_decision_ids.add(target_id)
            targets.append(
                {
                    "review_target_id": target_id,
                    "scope_kind": "canonical_segment",
                    "candidate_subject_id": subject_id,
                    "tracklet_ids": [tracklet_id],
                    "stable_slot_id": slot_id,
                    "source_team_label": team_label,
                    "effective_team_label": str(
                        (decision or {}).get("team_label") or team_label
                    ),
                    "frame_start": frames[0],
                    "frame_end": frames[-1],
                    "frame_ranges": [[frames[0], frames[-1]]],
                    # This exact list is authoritative. Presentation ranges must
                    # never be used to synthesize an unowned observation.
                    "owned_frames": frames,
                    "detected_observation_count": len(frames),
                    "source_ownership_digest": ownership_digest,
                    "reason_codes": ["mixed_tracklet_canonical_owners"],
                    "visual_evidence": evidence,
                    "current_decision": decision if decision_current else None,
                    "decision_status": "reviewed" if decision_current else "pending",
                    "stale_decision": bool(decision and not decision_current),
                    "legacy_suggestion": None,
                }
            )

    dominant_by_subject: dict[str, dict[str, Any]] = {}
    for target in targets:
        subject_id = str(target["candidate_subject_id"])
        current = dominant_by_subject.get(subject_id)
        if current is None or int(target["detected_observation_count"]) > int(
            current["detected_observation_count"]
        ):
            dominant_by_subject[subject_id] = target
    for subject_id, target in dominant_by_subject.items():
        player_id = legacy.get(subject_id)
        player = roster.get(player_id or "")
        if player and str(player.get("team_label")) == str(target["source_team_label"]):
            target["legacy_suggestion"] = {
                "action": "assign_roster_player",
                "player_id": player_id,
                "player_name": player.get("player_name"),
                "roster_number": player.get("roster_number"),
                "team_label": player.get("team_label"),
                "requires_confirmation": True,
            }

    _attach_boundary_evidence(targets)

    document = {
        "schema_version": SCHEMA_VERSION,
        "mode": "reviewed_identity_segment_review",
        "match_id": str(match_doc.get("id") or match_path.name),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "global_identity_digest": canonical_digest(global_doc),
            "subjects_digest": canonical_digest(subjects_doc),
            "decisions_digest": canonical_digest(decisions_doc.get("decisions") or []),
        },
        "summary": {
            "mixed_tracklets": len({key[1] for key in groups}),
            "targets_total": len(targets),
            "targets_reviewed": sum(
                target["decision_status"] == "reviewed" for target in targets
            ),
            "targets_pending": sum(
                target["decision_status"] == "pending" for target in targets
            ),
            "stale_decisions": (
                sum(bool(target["stale_decision"]) for target in targets)
                + len(set(stored_decisions) - matched_decision_ids)
            ),
            "orphaned_decisions_requiring_review": len(
                set(stored_decisions) - matched_decision_ids
            ),
        },
        "targets": sorted(
            targets,
            key=lambda row: (
                str(row["candidate_subject_id"]),
                int(row["frame_start"]),
                str(row["stable_slot_id"]),
            ),
        ),
        "safety": {
            "mutates_raw_tracklets": False,
            "fills_unowned_observation_gaps": False,
            "reruns_yolo": False,
        },
    }
    write_identity_json_atomic(match_path / REVIEW_FILENAME, document)
    return document


def load_segment_review(match_path: Path) -> dict[str, Any]:
    return _load(match_path / REVIEW_FILENAME)


def render_segment_review_evidence(
    match_path: Path,
    match_doc: dict[str, Any],
    review: dict[str, Any],
) -> set[str]:
    crops = [
        crop
        for target in review.get("targets") or []
        for crop in (target.get("visual_evidence") or {}).get("anchor_crops") or []
        if crop.get("generated_for_segment_review")
        and crop.get("artifact")
        and not (match_path / str(crop["artifact"])).exists()
    ]
    if not crops:
        return set()
    preferred = str(match_doc.get("video_filename") or "") or None
    video = resolve_match_video_path(match_path, preferred)
    return render_identity_roster_anchor_crops(
        video,
        match_path,
        {"cards": [{"anchor_crops": crops}]},
    )


def load_segment_decisions(match_path: Path) -> dict[str, Any]:
    document = _load(match_path / DECISIONS_FILENAME)
    if document:
        return document
    return _decision_document([])


def save_segment_decision(
    match_path: Path,
    match_doc: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    review = build_segment_review_document(match_path, match_doc)
    target_id = str(payload.get("review_target_id") or "")
    target = next(
        (
            row
            for row in review.get("targets") or []
            if str(row.get("review_target_id") or "") == target_id
        ),
        None,
    )
    if target is None:
        raise SegmentTargetError("review_target_unknown")
    supplied_digest = str(payload.get("source_ownership_digest") or "")
    if supplied_digest != str(target.get("source_ownership_digest") or ""):
        raise SegmentTargetError("review_target_stale")
    action = str(payload.get("action") or "")
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"Unsupported segment correction action: {action}")
    roster = _roster(match_doc)
    player_id = str(payload.get("player_id") or "") or None
    team_label = str(payload.get("team_label") or "").upper() or None
    if action == "assign_roster_player":
        player = roster.get(player_id or "")
        if player is None:
            raise ValueError(f"Invalid player_id: {player_id or '<missing>'}")
        if str(player["team_label"]) != str(target["source_team_label"]):
            raise ValueError("player_id must match the selected segment team")
        team_label = str(player["team_label"])
    elif action == "assign_team":
        if team_label not in {"A", "B"}:
            raise ValueError("assign_team requires team_label A or B")
        player_id = None
    else:
        player_id = None
        team_label = (
            "U" if action == "team_unknown" else str(target["source_team_label"])
        )

    existing = load_segment_decisions(match_path)
    decisions = {
        str(row.get("review_target_id")): dict(row)
        for row in existing.get("decisions") or []
        if row.get("review_target_id")
    }
    decision = {
        "review_target_id": target_id,
        "candidate_subject_id": target["candidate_subject_id"],
        "tracklet_ids": list(target["tracklet_ids"]),
        "stable_slot_id": target["stable_slot_id"],
        "source_ownership_digest": target["source_ownership_digest"],
        "action": action,
        "player_id": player_id,
        "team_label": team_label,
        "source": "manual_segment_review",
        "supersedes_legacy_whole_subject_decision": bool(
            target.get("legacy_suggestion")
        ),
        "comment": str(payload.get("comment") or "").strip() or None,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }
    decisions[target_id] = decision
    document = _decision_document(
        sorted(decisions.values(), key=lambda row: str(row["review_target_id"]))
    )
    write_identity_json_atomic(match_path / DECISIONS_FILENAME, document)
    return decision


def segment_observation_assignments(
    review: dict[str, Any],
    decisions: dict[str, Any],
    roster: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    decisions_by_target = {
        str(row.get("review_target_id")): row
        for row in decisions.get("decisions") or []
        if row.get("review_target_id")
    }
    output: list[dict[str, Any]] = []
    for target in review.get("targets") or []:
        decision = decisions_by_target.get(str(target.get("review_target_id") or ""))
        if not decision or decision.get("source_ownership_digest") != target.get(
            "source_ownership_digest"
        ):
            continue
        action = str(decision.get("action") or "")
        player = roster.get(str(decision.get("player_id") or ""))
        for frame in target.get("owned_frames") or []:
            row = _segment_assignment_row(target, decision, action, player, int(frame))
            if row is not None:
                output.append(row)
    return sorted(output, key=lambda row: (int(row["frame"]), str(row["tracklet_id"])))


def target_for_id(review: dict[str, Any], target_id: str) -> dict[str, Any] | None:
    return next(
        (
            row
            for row in review.get("targets") or []
            if str(row.get("review_target_id") or "") == target_id
        ),
        None,
    )


class SegmentTargetError(ValueError):
    pass


def _segment_assignment_row(
    target: dict[str, Any],
    decision: dict[str, Any],
    action: str,
    player: dict[str, Any] | None,
    frame: int,
) -> dict[str, Any] | None:
    tracklet_id = str((target.get("tracklet_ids") or [""])[0])
    slot_id = str(target.get("stable_slot_id") or "") or None
    team = str(decision.get("team_label") or target.get("source_team_label") or "U")
    common = {
        "review_target_id": target.get("review_target_id"),
        "tracklet_id": tracklet_id,
        "frame": frame,
        "identity_source": "manual_segment_review",
        "hard_blockers": [],
        "conflicts": [],
        "eligible_for_player_stats": False,
    }
    if action == "assign_roster_player" and player:
        player_name = str(player.get("player_name") or player.get("name") or decision.get("player_id"))
        return {
            **common,
            "stable_anonymous_slot_id": slot_id,
            "stable_anonymous_entity_id": slot_id,
            "team_label": str(player["team_label"]),
            "fallback_label": slot_id or f"{player['team_label']}?",
            "identity_status": "confirmed",
            "canonical_player_id": str(decision.get("player_id")),
            "player_name": player_name,
            "roster_number": player.get("roster_number", player.get("number")),
            "display_label": player_name,
            "eligible_for_player_stats": True,
        }
    if action == "assign_team":
        return {
            **common,
            "stable_anonymous_slot_id": None,
            "stable_anonymous_entity_id": None,
            "team_label": team,
            "fallback_label": f"{team}?",
            "display_label": f"{team}?",
            "identity_status": "unresolved",
            "canonical_player_id": None,
            "player_name": None,
        }
    if action == "referee":
        return {
            **common,
            "stable_anonymous_slot_id": None,
            "stable_anonymous_entity_id": None,
            "team_label": "U",
            "fallback_label": "Sędzia",
            "display_label": "Sędzia",
            "identity_status": "referee",
            "canonical_player_id": None,
            "player_name": None,
        }
    if action == "false_detection":
        return {
            **common,
            "stable_anonymous_slot_id": None,
            "stable_anonymous_entity_id": None,
            "team_label": "U",
            "fallback_label": "Fałszywa detekcja",
            "display_label": "Fałszywa detekcja",
            "identity_status": "false_detection",
            "canonical_player_id": None,
            "player_name": None,
        }
    if action in {"team_unknown", "unresolved"}:
        unknown_team = "U" if action == "team_unknown" else team
        fallback = slot_id if action == "unresolved" and slot_id else f"{unknown_team}?"
        return {
            **common,
            "stable_anonymous_slot_id": slot_id if action == "unresolved" else None,
            "stable_anonymous_entity_id": slot_id if action == "unresolved" else None,
            "team_label": unknown_team,
            "fallback_label": fallback,
            "display_label": fallback,
            "identity_status": "team_unknown" if action == "team_unknown" else "unresolved",
            "canonical_player_id": None,
            "player_name": None,
        }
    return None


def _target_evidence(
    card: dict[str, Any] | None,
    tracklet: dict[str, Any],
    frames: list[int],
    target_id: str,
) -> dict[str, Any]:
    frame_set = set(frames)
    existing = [
        dict(row)
        for row in ((card or {}).get("visual_evidence") or {}).get("anchor_crops") or []
        if int(row.get("frame") or -1) in frame_set
    ][:5]
    positions = {
        int(row.get("frame") or 0): row
        for row in tracklet.get("positions_m") or []
        if int(row.get("frame") or 0) in frame_set
    }
    selected_frames = _representative_frames(frames, 5)
    generated = []
    safe_target = target_id.rsplit(":", 1)[-1]
    existing_frames = {int(row.get("frame") or 0) for row in existing}
    for index, frame in enumerate(selected_frames, start=1):
        if frame in existing_frames:
            continue
        position = positions.get(frame) or {}
        bbox = position.get("bbox_xyxy")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        generated.append(
            {
                "anchor_crop_id": f"segment-crop:{safe_target}:{frame}",
                "artifact": f"reviewed_identity_segments/{safe_target}/{index:02d}_f{frame:06d}.jpg",
                "frame": frame,
                "time_sec": position.get("time_sec"),
                "tracklet_id": tracklet.get("tracklet_id"),
                "bbox_xyxy": bbox,
                "generated_for_segment_review": True,
            }
        )
    crops = sorted([*existing, *generated], key=lambda row: int(row.get("frame") or 0))[:5]
    return {
        "status": "ready" if crops else "missing",
        "selected_crop_count": len(crops),
        "anchor_crops": crops,
    }


def _representative_frames(frames: list[int], limit: int) -> list[int]:
    if len(frames) <= limit:
        return frames
    indexes = {
        round(index * (len(frames) - 1) / (limit - 1)) for index in range(limit)
    }
    return [frames[index] for index in sorted(indexes)]


def _attach_boundary_evidence(targets: list[dict[str, Any]]) -> None:
    for target in targets:
        subject_id = str(target.get("candidate_subject_id") or "")
        tracklet_ids = set(target.get("tracklet_ids") or [])
        candidates = [
            {**crop, "outside_target": True}
            for other in targets
            if other is not target
            and str(other.get("candidate_subject_id") or "") == subject_id
            and tracklet_ids.intersection(other.get("tracklet_ids") or [])
            for crop in (other.get("visual_evidence") or {}).get("anchor_crops") or []
        ]
        start = int(target.get("frame_start") or 0)
        end = int(target.get("frame_end") or start)
        ranked = sorted(
            candidates,
            key=lambda crop: min(
                abs(int(crop.get("frame") or 0) - start),
                abs(int(crop.get("frame") or 0) - end),
            ),
        )
        (target.get("visual_evidence") or {})["boundary_crops"] = ranked[:2]


def _contiguous_frame_runs(claims: list[dict[str, Any]]) -> list[list[int]]:
    runs: list[list[int]] = []
    for frame in sorted({int(row.get("frame") or 0) for row in claims}):
        if not runs or frame != runs[-1][-1] + 1:
            runs.append([frame])
        else:
            runs[-1].append(frame)
    return runs


def _target_id(
    subject_id: str,
    tracklet_id: str,
    slot_id: str,
    team_label: str,
    frames: list[int],
) -> str:
    digest = canonical_digest(
        {
            "candidate_subject_id": subject_id,
            "tracklet_id": tracklet_id,
            "stable_slot_id": slot_id,
            "team_label": team_label,
            "frame_start": frames[0],
            "frame_end": frames[-1],
            "frames_digest": canonical_digest(frames),
        }
    )
    return f"review-segment:v1:{digest}"


def _legacy_roster_decisions(match_path: Path) -> dict[str, str]:
    roster_doc = _load(
        match_path / "identity_roster_subject_review_decisions_shadow.json"
    )
    slot_doc = _load(match_path / "reviewed_identity_slot_assignments.json")
    output = {
        str(row.get("candidate_subject_id")): str(row.get("player_id"))
        for row in roster_doc.get("decisions") or []
        if row.get("candidate_subject_id")
        and row.get("player_id")
        and row.get("decision") in {"assign_roster_player", "confirm_recommended_player"}
    }
    output.update(
        {
            str(row.get("candidate_subject_id")): str(row.get("player_id"))
            for row in slot_doc.get("decisions") or []
            if row.get("candidate_subject_id")
            and row.get("player_id")
            and row.get("action") == "assign_roster_player"
            and not row.get("stable_slot_id")
        }
    )
    return output


def _requires_segment_review(
    card: dict[str, Any] | None,
    legacy_player_id: str | None,
    terminal_whole_subject_decision: bool,
) -> bool:
    if terminal_whole_subject_decision:
        return False
    if legacy_player_id:
        return True
    if not card or card.get("requires_operator_review") is False:
        return False
    signals = {
        str(value).lower()
        for key in ("quality_flags", "reason_codes", "blockers")
        for value in card.get(key) or []
    }
    return any(
        marker in signal
        for signal in signals
        for marker in ("merge", "multiple", "conflict", "switch")
    )


def _terminal_whole_subject_decisions(match_path: Path) -> set[str]:
    # Only conservative identity removal can safely terminate review for a
    # mixed raw tracklet. assign_team, referee and false_detection affect the
    # whole raw fragment and may therefore misclassify another physical person.
    slot_doc = _load(match_path / "reviewed_identity_slot_assignments.json")
    return {
        str(row.get("candidate_subject_id"))
        for row in slot_doc.get("decisions") or []
        if row.get("candidate_subject_id")
        and row.get("action")
        in {"team_unknown", "unresolved"}
    }


def _roster(match_doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for index, team in enumerate(match_doc.get("teams") or []):
        team_label = str(team.get("team_label") or chr(ord("A") + index))
        for player in team.get("players") or []:
            player_id = str(player.get("id") or "")
            if player_id:
                output[player_id] = {
                    "player_id": player_id,
                    "player_name": str(player.get("name") or player_id),
                    "roster_number": player.get("number"),
                    "team_label": team_label,
                }
    return output


def _decision_document(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "reviewed_identity_segment_decisions",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "decisions": decisions,
        "safety": {
            "mutates_raw_tracklets": False,
            "fills_unowned_observation_gaps": False,
        },
    }


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    import json

    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}
