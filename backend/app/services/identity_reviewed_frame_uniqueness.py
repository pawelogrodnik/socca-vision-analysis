from __future__ import annotations

"""Prevent one stable anonymous slot from being rendered twice in a frame."""

from collections import defaultdict
from typing import Any


_SOURCE_PRIORITY = {
    "manual_new_player_confirmation": 50,
    "manual_review": 50,
    "global_identity": 30,
    "stable_players": 30,
    "canonical_consensus": 30,
    "identity_review_gallery": 20,
    "candidate_shadow": 10,
}


def build_frame_slot_demotions(
    tracklets: dict[str, dict[str, Any]],
    assignments: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    assignment_by_tracklet = {
        str(row.get("tracklet_id")): row for row in assignments
    }
    claims: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for tracklet_id, tracklet in tracklets.items():
        assignment = assignment_by_tracklet.get(tracklet_id)
        if not assignment or not assignment.get("stable_anonymous_slot_id"):
            continue
        if assignment.get("identity_status") in {
            "blocked",
            "conflicted",
            "false_detection",
            "referee",
        }:
            continue
        for position in tracklet.get("positions_m") or []:
            if str(position.get("status") or "detected") != "detected":
                continue
            frame = int(position.get("frame") or 0)
            claims[(frame, str(assignment["stable_anonymous_slot_id"]))].append(
                assignment
            )

    demotions: list[dict[str, Any]] = []
    duplicate_groups = 0
    for (frame, slot_id), rows in sorted(claims.items()):
        tracklet_rows = {
            str(row["tracklet_id"]): row for row in rows
        }
        if len(tracklet_rows) < 2:
            continue
        duplicate_groups += 1
        priorities = {
            tracklet_id: _priority(row)
            for tracklet_id, row in tracklet_rows.items()
        }
        best = max(priorities.values())
        winners = [key for key, value in priorities.items() if value == best]
        demoted = (
            sorted(tracklet_rows)
            if len(winners) != 1
            else sorted(set(tracklet_rows) - {winners[0]})
        )
        for tracklet_id in demoted:
            row = tracklet_rows[tracklet_id]
            team = str(row.get("team_label") or "U")
            team = team if team in {"A", "B"} else "U"
            demotions.append(
                {
                    "tracklet_id": tracklet_id,
                    "frame": frame,
                    "identity_status": "conflicted",
                    "stable_anonymous_slot_id": None,
                    "stable_anonymous_entity_id": None,
                    "fallback_label": f"{team}?",
                    "display_label": f"{team}? !",
                    "identity_source": "frame_slot_uniqueness_guard",
                    "conflicts": [
                        {
                            "code": "duplicate_stable_slot_in_frame",
                            "rejected_stable_slot_id": slot_id,
                        }
                    ],
                }
            )
    per_frame = defaultdict(int)
    for row in demotions:
        per_frame[int(row["frame"])] += 1
    return demotions, {
        "duplicate_stable_slot_claim_groups": duplicate_groups,
        "demoted_observation_claims": len(demotions),
        "frames_with_duplicate_slot_claims": len(per_frame),
        "duplicate_stable_labels_rendered": 0,
    }


def _priority(row: dict[str, Any]) -> int:
    source = str(row.get("stable_anchor_source") or "")
    if source in _SOURCE_PRIORITY:
        return _SOURCE_PRIORITY[source]
    claims = row.get("stable_anchor_claims") or []
    return max(
        (_SOURCE_PRIORITY.get(str(claim.get("source") or ""), 0) for claim in claims),
        default=0,
    )
