from __future__ import annotations

"""Final per-frame safety for stable slots and confirmed roster players."""

from collections import defaultdict
from typing import Any

from app.services.identity_reviewed_effective_observation import (
    effective_reviewed_observation,
    is_real_detected_position,
    observation_index,
)
from app.services.play_area import is_on_pitch_product_observation


_SOURCE_PRIORITY = {
    "manual_segment_review": 70,
    "operator_seed_exact_observation": 60,
    "manual_new_player_confirmation": 50,
    "manual_review": 50,
    "manual_stable_slot_binding": 50,
    "legacy_subject_to_stable_slot_binding": 50,
    "operator_review": 50,
    "operator_seed_safe_lineage": 40,
    "canonical_frame_global_identity": 30,
    "global_identity": 30,
    "stable_players": 30,
    "canonical_consensus": 30,
    "identity_review_gallery": 20,
    "candidate_shadow": 10,
}
_NON_PLAYER_STATUSES = {
    "blocked",
    "conflicted",
    "false_detection",
    "ignored",
    "referee",
    "team_unknown",
}


def build_frame_slot_demotions(
    tracklets: dict[str, dict[str, Any]],
    assignments: list[dict[str, Any]],
    exact_overrides: list[dict[str, Any]] | None = None,
    canonical_ownership: list[dict[str, Any]] | None = None,
    segment_overrides: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    exact_index = observation_index(exact_overrides or [])
    segment_index = observation_index(segment_overrides or [])
    canonical_index = observation_index(canonical_ownership or [])
    stable_claims: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    player_claims: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    assignment_by_tracklet = {
        str(row.get("tracklet_id")): row for row in assignments
    }
    for tracklet_id, tracklet in tracklets.items():
        assignment = assignment_by_tracklet.get(tracklet_id)
        if not assignment:
            continue
        for position in tracklet.get("positions_m") or []:
            if not is_real_detected_position(position):
                continue
            if not is_on_pitch_product_observation(position):
                continue
            effective = effective_reviewed_observation(
                assignment,
                position,
                exact_index,
                {},
                canonical_index,
                segment_index,
            )
            status = str(effective.get("identity_status") or "unresolved")
            if status in _NON_PLAYER_STATUSES:
                continue
            frame = int(position.get("frame") or 0)
            slot_id = effective.get("stable_anonymous_slot_id")
            propagation_diagnostics = {
                str(value)
                for value in effective.get("propagation_diagnostics") or []
            }
            if (
                slot_id
                and "stable_slot_propagation_conflicted"
                not in propagation_diagnostics
            ):
                stable_claims[(frame, str(slot_id))].append(effective)
            player_id = effective.get("canonical_player_id")
            if status == "confirmed" and player_id:
                player_claims[(frame, str(player_id))].append(effective)

    demotions: dict[tuple[str, int], dict[str, Any]] = {}
    stable_groups, stable_demoted = _resolve_claim_groups(
        stable_claims,
        demotions,
        conflict_code="duplicate_stable_slot_in_frame",
        rejected_key="rejected_stable_slot_id",
        clear_stable_slot=True,
    )
    player_groups, player_demoted = _resolve_claim_groups(
        player_claims,
        demotions,
        conflict_code="duplicate_canonical_player_in_frame",
        rejected_key="rejected_canonical_player_id",
        clear_stable_slot=False,
    )
    rows = sorted(
        demotions.values(),
        key=lambda row: (int(row["frame"]), str(row["tracklet_id"])),
    )
    stable_frames = _conflict_frames(rows, "duplicate_stable_slot_in_frame")
    player_frames = _conflict_frames(rows, "duplicate_canonical_player_in_frame")
    return rows, {
        "duplicate_stable_slot_claim_groups": stable_groups,
        "demoted_stable_slot_observations": stable_demoted,
        "duplicate_canonical_player_claim_groups": player_groups,
        "demoted_canonical_player_observations": player_demoted,
        "demoted_observation_claims": len(rows),
        "frames_with_duplicate_slot_claims": len(stable_frames),
        "frames_with_duplicate_canonical_player_claims": len(player_frames),
        "frames_with_duplicate_observation_claims": len(stable_frames | player_frames),
        "duplicate_stable_labels_rendered": 0,
        "duplicate_canonical_players_rendered": 0,
    }


def _conflict_frames(rows: list[dict[str, Any]], code: str) -> set[int]:
    return {
        int(row["frame"])
        for row in rows
        if any(conflict.get("code") == code for conflict in row.get("conflicts") or [])
    }


def _resolve_claim_groups(
    groups: dict[tuple[int, str], list[dict[str, Any]]],
    demotions: dict[tuple[str, int], dict[str, Any]],
    *,
    conflict_code: str,
    rejected_key: str,
    clear_stable_slot: bool,
) -> tuple[int, int]:
    duplicate_groups = 0
    demoted_count = 0
    for (frame, claim_id), rows in sorted(groups.items()):
        tracklet_rows = {str(row["tracklet_id"]): row for row in rows}
        if len(tracklet_rows) < 2:
            continue
        duplicate_groups += 1
        priorities = {
            tracklet_id: _priority(row)
            for tracklet_id, row in tracklet_rows.items()
        }
        best = max(priorities.values())
        winners = [key for key, value in priorities.items() if value == best]
        rejected = (
            sorted(tracklet_rows)
            if len(winners) != 1
            else sorted(set(tracklet_rows) - {winners[0]})
        )
        for tracklet_id in rejected:
            _demote(
                demotions,
                tracklet_rows[tracklet_id],
                frame,
                conflict={"code": conflict_code, rejected_key: claim_id},
                clear_stable_slot=clear_stable_slot,
            )
            demoted_count += 1
    return duplicate_groups, demoted_count


def _demote(
    demotions: dict[tuple[str, int], dict[str, Any]],
    row: dict[str, Any],
    frame: int,
    *,
    conflict: dict[str, Any],
    clear_stable_slot: bool,
) -> None:
    tracklet_id = str(row["tracklet_id"])
    key = (tracklet_id, frame)
    existing = demotions.get(key, {})
    team = str(row.get("team_label") or "U")
    team = team if team in {"A", "B"} else "U"
    stable_slot = None if clear_stable_slot else row.get("stable_anonymous_slot_id")
    if existing.get("stable_anonymous_slot_id") is None and existing:
        stable_slot = None
    fallback = str(
        existing.get("fallback_label")
        or stable_slot
        or (f"{team}?" if clear_stable_slot else row.get("fallback_label"))
        or f"{team}?"
    )
    conflicts = [*(existing.get("conflicts") or []), conflict]
    demotions[key] = {
        **existing,
        "tracklet_id": tracklet_id,
        "frame": frame,
        "identity_status": "conflicted",
        "canonical_player_id": None,
        "player_name": None,
        "eligible_for_player_stats": False,
        "stable_anonymous_slot_id": stable_slot,
        "stable_anonymous_entity_id": stable_slot,
        "fallback_label": fallback,
        "display_label": f"{fallback} !",
        "identity_source": "frame_observation_uniqueness_guard",
        "conflicts": conflicts,
    }


def _priority(row: dict[str, Any]) -> int:
    identity_source = str(row.get("identity_source") or "")
    if identity_source in _SOURCE_PRIORITY:
        return _SOURCE_PRIORITY[identity_source]
    anchor_source = str(row.get("stable_anchor_source") or "")
    if anchor_source in _SOURCE_PRIORITY:
        return _SOURCE_PRIORITY[anchor_source]
    claims = row.get("stable_anchor_claims") or []
    return max(
        (
            _SOURCE_PRIORITY.get(str(claim.get("source") or ""), 0)
            for claim in claims
        ),
        default=0,
    )
