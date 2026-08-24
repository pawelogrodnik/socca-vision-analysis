from __future__ import annotations

"""Conservative, operator-safe continuity cases for Reviewed Identity.

Coverage answers whether a team has enough named observations overall.  It does
not answer whether one otherwise known player has disappeared for a material
continuous interval.  This module keeps that second question isolated from the
coverage policy so its threshold can be calibrated independently.
"""

from collections import defaultdict
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from typing import Any

from app.services.identity_canonical_io import load_json_cached_or
from app.services.identity_initial_audit_store import write_identity_json_atomic
from app.services.identity_jersey_number_common import canonical_digest


MATERIAL_CONTINUITY_POLICY_VERSION = "material-continuity:v1-safe-team-a-20s"
# V1 intentionally promotes only the single, severe class observed in the
# acceptance match.  It is not a claim that 20 seconds is the final product
# threshold for longer matches.
MATERIAL_CONTINUITY_MIN_SPAN_SEC = 20.0
# Fragment count is useful diagnostics, not a materiality gate: a clean
# tracker can retain one anonymous subject through a long identity gap. The
# versioned 20-second V1 threshold remains deliberately provisional.
MATERIAL_CONTINUITY_MAX_JOIN_GAP_SEC = 1.0
MATERIAL_CONTINUITY_MAX_EVIDENCE_CROPS = 5
DECISIONS_FILENAME = "reviewed_identity_material_continuity_decisions.json"
DECISIONS_SCHEMA_VERSION = "1.0.0"
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


def coalesce_material_continuity_units(
    units: list[dict[str, Any]],
    fps: float,
    decisions: dict[str, Any] | None = None,
    *,
    excluded_observation_pairs: set[tuple[str, int]] | None = None,
) -> list[dict[str, Any]]:
    """Replace safe, adjacent anonymous Team-A fragments with one case.

    The grouped unit owns the exact union of its members' detected pairs.
    Stable slots are used only as a local continuity hypothesis. Any saved
    decision is limited to this group's exact tracklet/frame ownership.
    """
    safe_fps = fps if fps > 0 else 30.0
    excluded_pairs = excluded_observation_pairs or set()
    by_slot: dict[str, list[dict[str, Any]]] = defaultdict(list)
    max_frame_gap = max(1, int(ceil(MATERIAL_CONTINUITY_MAX_JOIN_GAP_SEC * safe_fps)))
    for unit in units:
        slot = str(unit.get("stable_slot_id") or "")
        if _eligible(unit, slot):
            by_slot[slot].extend(
                _continuous_member_runs(unit, max_frame_gap, excluded_pairs)
            )

    grouped_pairs: set[tuple[str, int]] = set()
    continuity_units: list[dict[str, Any]] = []
    for slot, slot_units in sorted(by_slot.items()):
        runs: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        previous_end: int | None = None
        for unit in sorted(slot_units, key=_unit_sort_key):
            start = _frame_start(unit)
            if current and previous_end is not None and start - previous_end > max_frame_gap:
                runs.append(current)
                current = []
            current.append(unit)
            previous_end = max(previous_end or _frame_end(unit), _frame_end(unit))
        if current:
            runs.append(current)

        for run in runs:
            case = _continuity_case(slot, run, safe_fps)
            if case is None:
                continue
            decision = _current_decision(case, decisions or {})
            if decision is not None:
                case["current_decision"] = decision
                case["current_resolution_status"] = "reviewed_by_operator"
            continuity_units.append(case)
            grouped_pairs.update(
                (str(tracklet_id), int(frame))
                for member in run
                for tracklet_id, frame in member.get("detected_pairs") or []
            )

    # A material run supersedes only its exact observations in presentation.
    # A raw tracker fragment with a genuine long hole remains split, rather
    # than being silently treated as continuous because its outer timestamps
    # happen to be far apart.
    retained = _retain_non_grouped_observations(units, grouped_pairs, safe_fps)
    return [*retained, *continuity_units]


def trim_resolved_material_pairs_from_whole_subject_units(
    units: list[dict[str, Any]],
    resolved_pairs: set[tuple[str, int]],
    fps: float,
    *,
    tracklet_team_labels: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Remove resolved material-split ownership from competing parents only.

    A resolved inline temporal split delegates its exact source observations to
    canonical-segment children.  The raw whole-subject presentation unit can
    still contain those same observations, so it must be reduced before
    coverage and optional-audit policy see it.  This never receives segment
    children and never applies to unresolved complex mixes: callers pass only
    the fail-closed, verified resolved-pair set.
    """
    if not resolved_pairs:
        return units
    safe_fps = fps if fps > 0 else 30.0
    retained: list[dict[str, Any]] = []
    for unit in units:
        if str(unit.get("correction_scope") or "whole_subject") != "whole_subject":
            retained.append(unit)
            continue
        pairs = _unit_pairs(unit)
        remaining = pairs - resolved_pairs
        if remaining == pairs:
            retained.append(unit)
            continue
        if not remaining:
            continue
        frames = sorted({frame for _, frame in remaining})
        clone = dict(unit)
        clone["detected_pairs"] = sorted(remaining)
        clone["tracklet_ids"] = sorted({tracklet_id for tracklet_id, _ in remaining})
        clone["tracklet_count"] = len(clone["tracklet_ids"])
        clone["frame_start"] = frames[0]
        clone["frame_end"] = frames[-1]
        clone["detected_frame_count"] = len(frames)
        clone["detected_observation_count"] = len(remaining)
        clone["detected_time_sec"] = round(len(frames) / safe_fps, 3)
        if tracklet_team_labels is not None:
            detected_teams = {
                str(tracklet_team_labels.get(tracklet_id) or "U").upper()
                for tracklet_id, _ in remaining
            }
            clone["detected_team_labels"] = sorted(detected_teams & {"A", "B"})
            source_team = (
                next(iter(detected_teams)) if len(detected_teams) == 1 else "U"
            )
            clone["source_team_label"] = source_team
            if not (clone.get("current_decision") or {}).get("action"):
                clone["effective_team_label"] = source_team
        clone["visual_evidence"] = _evidence_within_pairs(
            unit.get("visual_evidence") or {}, remaining
        )
        clone["has_operator_visual_evidence"] = bool(
            (clone["visual_evidence"] or {}).get("anchor_crops")
        )
        if "owned_observations" in clone:
            clone["owned_observations"] = [
                {"tracklet_id": tracklet_id, "frame": frame}
                for tracklet_id, frame in sorted(remaining)
            ]
        clone["reason_codes"] = sorted(
            set(clone.get("reason_codes") or [])
            | {"resolved_material_split_owned_by_child"}
        )
        retained.append(clone)
    return retained


def is_material_continuity_case(unit: dict[str, Any]) -> bool:
    return unit.get("scope_kind") == "material_continuity" and bool(
        unit.get("material_continuity_required")
    )


def _eligible(unit: dict[str, Any], slot: str) -> bool:
    return bool(
        slot
        and str(unit.get("effective_team_label") or "").upper() == "A"
        and unit.get("canonical_player_id") is None
        and not unit.get("current_decision")
        and unit.get("operator_actionable") is not False
        and unit.get("has_operator_visual_evidence")
        and str(unit.get("current_resolution_status") or "") == "pending_optional"
        and str(unit.get("correction_scope") or "whole_subject") == "whole_subject"
    )


def _continuity_case(
    slot: str,
    members: list[dict[str, Any]],
    fps: float,
) -> dict[str, Any] | None:
    pairs = {
        (str(pair[0]), int(pair[1]))
        for member in members
        for pair in member.get("detected_pairs") or []
        if isinstance(pair, (tuple, list)) and len(pair) >= 2
    }
    if not pairs:
        return None
    frames = sorted({frame for _, frame in pairs})
    span_sec = round((frames[-1] - frames[0] + 1) / fps, 3)
    if span_sec < MATERIAL_CONTINUITY_MIN_SPAN_SEC:
        return None
    subject_ids = sorted(
        {
            str(member.get("candidate_subject_id") or "")
            for member in members
            if member.get("candidate_subject_id")
        }
    )
    if not subject_ids:
        return None
    crops = _balanced_anchor_crops(members)
    if not crops:
        return None
    frame_start, frame_end = frames[0], frames[-1]
    group_id = f"continuity:{slot}:{frame_start}-{frame_end}"
    members_payload = _members_payload(members)
    ownership_digest = _ownership_digest(
        group_id=group_id,
        slot=slot,
        team_label="A",
        members=members_payload,
    )
    return {
        "candidate_subject_id": group_id,
        "continuity_group_id": group_id,
        "continuity_subject_ids": subject_ids,
        "continuity_members": members_payload,
        "continuity_fragment_count": len(subject_ids),
        "scope_kind": "material_continuity",
        "correction_scope": "material_continuity",
        "operator_actionable": True,
        "non_actionable_reason": None,
        "tracklet_ids": sorted(
            {tracklet_id for member in members for tracklet_id in member.get("tracklet_ids") or []}
        ),
        "tracklet_count": len(
            {tracklet_id for member in members for tracklet_id in member.get("tracklet_ids") or []}
        ),
        "source_team_label": "A",
        "effective_team_label": "A",
        "stable_slot_id": slot,
        "frame_start": frame_start,
        "frame_end": frame_end,
        "frame_ranges": [[_frame_start(member), _frame_end(member)] for member in members],
        "detected_frame_count": len(frames),
        "detected_observation_count": len(pairs),
        "detected_time_sec": round(len(frames) / fps, 3),
        "continuity_span_sec": span_sec,
        "current_decision": None,
        "current_resolution_status": "pending_material_continuity_review",
        "canonical_player_id": None,
        "priority": "continuity",
        "reason_codes": ["material_identity_continuity_gap"],
        "material_continuity_required": True,
        "has_operator_visual_evidence": True,
        "visual_evidence": {
            "kind": "identity_continuity",
            "status": "ready_for_operator_review",
            "selected_crop_count": len(crops),
            "anchor_crops": crops,
        },
        "detected_pairs": sorted(pairs),
        "owned_observations": [
            {"tracklet_id": tracklet_id, "frame": frame}
            for tracklet_id, frame in sorted(pairs)
        ],
        "source_ownership_digest": ownership_digest,
    }


def _continuous_member_runs(
    unit: dict[str, Any],
    max_frame_gap: int,
    excluded_pairs: set[tuple[str, int]],
) -> list[dict[str, Any]]:
    pairs_by_frame: dict[int, list[tuple[str, int]]] = defaultdict(list)
    for raw_pair in unit.get("detected_pairs") or []:
        if not isinstance(raw_pair, (tuple, list)) or len(raw_pair) < 2:
            continue
        pair = (str(raw_pair[0]), int(raw_pair[1]))
        if pair in excluded_pairs:
            continue
        pairs_by_frame[pair[1]].append(pair)
    runs: list[list[tuple[str, int]]] = []
    current: list[tuple[str, int]] = []
    previous_frame: int | None = None
    for frame in sorted(pairs_by_frame):
        if current and previous_frame is not None and frame - previous_frame > max_frame_gap:
            runs.append(current)
            current = []
        current.extend(pairs_by_frame[frame])
        previous_frame = frame
    if current:
        runs.append(current)
    output: list[dict[str, Any]] = []
    for pairs in runs:
        clone = dict(unit)
        clone["detected_pairs"] = sorted(set(pairs))
        frames = sorted({frame for _, frame in clone["detected_pairs"]})
        clone["frame_start"] = frames[0]
        clone["frame_end"] = frames[-1]
        clone["detected_frame_count"] = len(frames)
        clone["detected_observation_count"] = len(clone["detected_pairs"])
        clone["visual_evidence"] = _evidence_within_frames(
            unit.get("visual_evidence") or {},
            set(frames),
        )
        if (clone["visual_evidence"] or {}).get("anchor_crops"):
            output.append(clone)
    return output


def _retain_non_grouped_observations(
    units: list[dict[str, Any]],
    grouped_pairs: set[tuple[str, int]],
    fps: float,
) -> list[dict[str, Any]]:
    retained: list[dict[str, Any]] = []
    for unit in units:
        pairs = _unit_pairs(unit)
        remaining = pairs - grouped_pairs
        if not pairs or remaining == pairs:
            retained.append(unit)
            continue
        if not remaining:
            continue
        frames = sorted({frame for _, frame in remaining})
        clone = dict(unit)
        clone["detected_pairs"] = sorted(remaining)
        clone["frame_start"] = frames[0]
        clone["frame_end"] = frames[-1]
        clone["detected_frame_count"] = len(frames)
        clone["detected_observation_count"] = len(remaining)
        clone["detected_time_sec"] = round(len(frames) / fps, 3)
        clone["visual_evidence"] = _evidence_within_frames(
            unit.get("visual_evidence") or {},
            set(frames),
        )
        clone["has_operator_visual_evidence"] = bool(
            (clone["visual_evidence"] or {}).get("anchor_crops")
        )
        retained.append(clone)
    return retained


def _evidence_within_frames(
    evidence: dict[str, Any],
    frames: set[int],
) -> dict[str, Any]:
    value = dict(evidence)
    value["anchor_crops"] = [
        dict(crop)
        for crop in evidence.get("anchor_crops") or []
        if crop.get("frame") is not None and int(crop["frame"]) in frames
    ]
    return value


def _evidence_within_pairs(
    evidence: dict[str, Any],
    pairs: set[tuple[str, int]],
) -> dict[str, Any]:
    value = dict(evidence)
    value["anchor_crops"] = [
        dict(crop)
        for crop in evidence.get("anchor_crops") or []
        if crop.get("tracklet_id") is not None
        and crop.get("frame") is not None
        and (str(crop["tracklet_id"]), int(crop["frame"])) in pairs
    ]
    return value


def _unit_pairs(unit: dict[str, Any]) -> set[tuple[str, int]]:
    return {
        (str(raw_pair[0]), int(raw_pair[1]))
        for raw_pair in unit.get("detected_pairs") or []
        if isinstance(raw_pair, (tuple, list)) and len(raw_pair) >= 2
    }


def _balanced_anchor_crops(members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets = [
        sorted(
            list(((member.get("visual_evidence") or {}).get("anchor_crops") or [])),
            key=lambda crop: int(crop.get("frame") or 0),
        )
        for member in members
    ]
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    # Round-robin means a long first tracker fragment cannot monopolize the
    # five visual samples of a multi-fragment continuity case.
    while len(output) < MATERIAL_CONTINUITY_MAX_EVIDENCE_CROPS and any(buckets):
        for bucket in buckets:
            if not bucket or len(output) >= MATERIAL_CONTINUITY_MAX_EVIDENCE_CROPS:
                continue
            crop = dict(bucket.pop(0))
            key = str(crop.get("anchor_crop_id") or crop.get("artifact") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            output.append(crop)
    return output


def _frame_start(unit: dict[str, Any]) -> int:
    return int(unit.get("frame_start") or 0)


def _frame_end(unit: dict[str, Any]) -> int:
    return int(unit.get("frame_end") or _frame_start(unit))


def _unit_sort_key(unit: dict[str, Any]) -> tuple[int, int, str]:
    return (_frame_start(unit), _frame_end(unit), str(unit.get("candidate_subject_id") or ""))


def load_material_continuity_decisions(match_path: Path) -> dict[str, Any]:
    document = _load(match_path / DECISIONS_FILENAME)
    return document if document else _decision_document([])


def save_material_continuity_decision(
    match_path: Path,
    match_doc: dict[str, Any],
    payload: dict[str, Any],
    review_unit: dict[str, Any],
) -> dict[str, Any]:
    """Persist a decision owned by the exact observations in one gap group."""
    group_id = str(review_unit.get("continuity_group_id") or "")
    expected_digest = str(review_unit.get("source_ownership_digest") or "")
    if not group_id or str(payload.get("source_ownership_digest") or "") != expected_digest:
        from app.services.identity_reviewed_segments import SegmentTargetError

        raise SegmentTargetError("material_continuity_target_stale")
    action = str(payload.get("action") or "")
    if action not in ALLOWED_ACTIONS:
        raise ValueError("material_continuity_action_not_allowed")
    player_id = str(payload.get("player_id") or "") or None

    # ``review_unit`` comes from the versioned, server-only hot state for the
    # deferred endpoint. It contains exact ownership but is never exposed to
    # the browser. Legacy callers retain the old fresh-rebuild guard.
    if review_unit.get("_hot_state_authorized") is True:
        fresh_unit = review_unit
    else:
        from app.services.identity_reviewed_progress import build_reviewed_identity_progress

        fresh_unit = next(
            (
                row
                for row in build_reviewed_identity_progress(
                    match_path,
                    match_doc,
                    include_internal_units=True,
                ).get("_internal_review_units") or []
                if str(row.get("continuity_group_id") or "") == group_id
                and row.get("scope_kind") == "material_continuity"
            ),
            None,
        )
    if not isinstance(fresh_unit, dict) or str(fresh_unit.get("source_ownership_digest") or "") != expected_digest:
        from app.services.identity_reviewed_segments import SegmentTargetError

        raise SegmentTargetError("material_continuity_target_stale")
    source_team_label = str(fresh_unit.get("effective_team_label") or "U").upper()
    team_label = source_team_label
    roster = _roster(match_doc)
    if action == "assign_roster_player":
        player = roster.get(player_id or "")
        if player is None:
            raise ValueError("Invalid player_id")
        team_label = str(player.get("team_label") or "U").upper()
    elif action == "assign_team":
        team_label = str(payload.get("team_label") or "").upper()
        if team_label not in {"A", "B"}:
            raise ValueError("assign_team requires team_label A or B")
        player_id = None
    elif action == "team_unknown":
        team_label = "U"
        player_id = None
    else:
        player_id = None

    existing = load_material_continuity_decisions(match_path)
    decisions = {
        str(row.get("continuity_group_id") or ""): dict(row)
        for row in existing.get("decisions") or []
        if row.get("continuity_group_id")
    }
    decision = {
        "continuity_group_id": group_id,
        "candidate_subject_id": group_id,
        "scope_kind": "material_continuity",
        "source_ownership_digest": expected_digest,
        "source_team_label": source_team_label,
        "team_label": team_label,
        "continuity_subject_ids": list(fresh_unit.get("continuity_subject_ids") or []),
        "continuity_members": list(fresh_unit.get("continuity_members") or []),
        "owned_observations": list(fresh_unit.get("owned_observations") or []),
        "action": action,
        "player_id": player_id,
        "comment": str(payload.get("comment") or "").strip() or None,
        "source": "manual_material_continuity_review",
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }
    decisions[group_id] = decision
    write_identity_json_atomic(
        match_path / DECISIONS_FILENAME,
        _decision_document(sorted(decisions.values(), key=lambda row: str(row["continuity_group_id"]))),
    )
    return decision


def material_continuity_observation_assignments(
    match_path: Path,
    match_doc: dict[str, Any],
    decisions: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return exact, fail-closed observation overlays for current decisions."""
    from app.services.identity_reviewed_progress import build_reviewed_identity_progress

    current_units = {
        str(unit.get("continuity_group_id") or ""): unit
        for unit in build_reviewed_identity_progress(
            match_path,
            match_doc,
            include_internal_units=True,
        ).get("_internal_review_units") or []
        if unit.get("scope_kind") == "material_continuity"
    }
    roster = _roster(match_doc)
    output: list[dict[str, Any]] = []
    for decision in decisions.get("decisions") or []:
        group_id = str(decision.get("continuity_group_id") or "")
        unit = current_units.get(group_id)
        if not unit or str(decision.get("source_ownership_digest") or "") != str(unit.get("source_ownership_digest") or ""):
            continue
        owned = _pairs_from_observations(unit.get("owned_observations"))
        if owned != _pairs_from_observations(decision.get("owned_observations")):
            continue
        action = str(decision.get("action") or "")
        player = roster.get(str(decision.get("player_id") or ""))
        if action == "assign_roster_player" and player is None:
            continue
        for tracklet_id, frame in owned:
            assignment = _assignment_row(unit, decision, player, tracklet_id, frame)
            if assignment is not None:
                output.append(assignment)
    return sorted(output, key=lambda row: (int(row["frame"]), str(row["tracklet_id"])))


def _assignment_row(
    unit: dict[str, Any],
    decision: dict[str, Any],
    player: dict[str, Any] | None,
    tracklet_id: str,
    frame: int,
) -> dict[str, Any] | None:
    action = str(decision.get("action") or "")
    source_slot_id = str(unit.get("stable_slot_id") or "") or None
    common = {
        "continuity_group_id": unit.get("continuity_group_id"),
        "tracklet_id": tracklet_id,
        "frame": frame,
        "identity_source": "manual_material_continuity_review",
        "hard_blockers": [],
        "conflicts": [],
        "eligible_for_player_stats": False,
    }
    if action == "assign_roster_player" and player is not None:
        name = str(player.get("player_name") or player.get("name") or decision.get("player_id"))
        player_team = str(player.get("team_label") or "U").upper()
        # A safe continuity slot is only a hypothesis.  Do not carry an A-slot
        # into a named Team-B correction (or vice versa).
        slot_id = source_slot_id if source_slot_id and source_slot_id.startswith(player_team) else None
        return {
            **common,
            "stable_anonymous_slot_id": slot_id,
            "stable_anonymous_entity_id": slot_id,
            "team_label": player_team,
            "fallback_label": slot_id or f"{player_team}?",
            "identity_status": "confirmed",
            "canonical_player_id": str(decision.get("player_id")),
            "player_name": name,
            "roster_number": player.get("roster_number", player.get("number")),
            "display_label": name,
            "eligible_for_player_stats": True,
        }
    if action == "false_detection":
        return {
            **common,
            "stable_anonymous_slot_id": None,
            "stable_anonymous_entity_id": None,
            "team_label": "U",
            "fallback_label": "Fałszywa detekcja",
            "identity_status": "false_detection",
            "canonical_player_id": None,
            "player_name": None,
            "display_label": "Fałszywa detekcja",
        }
    if action == "referee":
        return {**common, "stable_anonymous_slot_id": None, "stable_anonymous_entity_id": None,
                "team_label": "U", "fallback_label": "REF", "identity_status": "referee",
                "canonical_player_id": None, "player_name": None, "display_label": "Sędzia"}
    if action == "team_unknown":
        return {
            **common,
            "stable_anonymous_slot_id": None,
            "stable_anonymous_entity_id": None,
            "team_label": "U",
            "fallback_label": "U?",
            "identity_status": "team_unknown",
            "canonical_player_id": None,
            "player_name": None,
            "display_label": "U?",
        }
    team_label = str(decision.get("team_label") or unit.get("effective_team_label") or "U").upper()
    if action == "assign_team":
        return {
            **common,
            "stable_anonymous_slot_id": None,
            "stable_anonymous_entity_id": None,
            "team_label": team_label,
            "fallback_label": f"{team_label}?",
            "identity_status": "unresolved",
            "canonical_player_id": None,
            "player_name": None,
            "display_label": f"{team_label}?",
        }
    # Only an explicit unresolved choice is allowed to preserve the safe
    # anonymous continuity hypothesis.  It remains ineligible for stats.
    slot_id = source_slot_id
    return {
        **common,
        "stable_anonymous_slot_id": slot_id,
        "stable_anonymous_entity_id": slot_id,
        "team_label": team_label,
        "fallback_label": slot_id or f"{team_label}?",
        "identity_status": "unresolved",
        "canonical_player_id": None,
        "player_name": None,
        "display_label": slot_id or f"{team_label}?",
    }


def _current_decision(unit: dict[str, Any], decisions: dict[str, Any]) -> dict[str, Any] | None:
    group_id = str(unit.get("continuity_group_id") or "")
    decision = next(
        (
            row
            for row in decisions.get("decisions") or []
            if str(row.get("continuity_group_id") or "") == group_id
            and str(row.get("source_ownership_digest") or "") == str(unit.get("source_ownership_digest") or "")
        ),
        None,
    )
    return dict(decision) if isinstance(decision, dict) else None


def _members_payload(members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "candidate_subject_id": str(member.get("candidate_subject_id") or ""),
            "detected_pairs": [
                [str(tracklet_id), int(frame)]
                for tracklet_id, frame in sorted(
                    {
                        (str(pair[0]), int(pair[1]))
                        for pair in member.get("detected_pairs") or []
                        if isinstance(pair, (list, tuple)) and len(pair) >= 2
                    }
                )
            ],
        }
        for member in members
    ]


def _ownership_digest(*, group_id: str, slot: str, team_label: str, members: list[dict[str, Any]]) -> str:
    return canonical_digest(
        {
            "continuity_group_id": group_id,
            "stable_slot_id": slot,
            "team_label": team_label,
            "members": members,
            "policy_version": MATERIAL_CONTINUITY_POLICY_VERSION,
        }
    )


def _pairs_from_observations(value: Any) -> set[tuple[str, int]]:
    return {
        (str(row.get("tracklet_id") or ""), int(row.get("frame") or 0))
        for row in value or []
        if isinstance(row, dict) and row.get("tracklet_id") is not None and row.get("frame") is not None
    }


def _decision_document(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": DECISIONS_SCHEMA_VERSION,
        "mode": "reviewed_identity_material_continuity_decisions",
        "decisions": decisions,
    }


def _roster(match_doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(player.get("player_id") or player.get("id") or ""): {
            **player,
            "player_id": str(player.get("player_id") or player.get("id") or ""),
            "team_label": str(
                player.get("team_label")
                or team.get("team_label")
                or team.get("label")
                or chr(ord("A") + team_index)
            ).upper(),
        }
        for team_index, team in enumerate(match_doc.get("teams") or [])
        for player in team.get("players") or []
        if player.get("player_id") or player.get("id")
    }


def _load(path: Path) -> dict[str, Any]:
    # Fully tolerant loader (missing/corrupt -> {}, non-object -> {});
    # participates in the request-scoped source materialization.
    value = load_json_cached_or(path)
    return value if isinstance(value, dict) else {}
