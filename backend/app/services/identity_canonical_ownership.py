from __future__ import annotations

"""Read frozen global identity ownership without flattening it by tracklet."""

from collections import defaultdict
import re
from typing import Any


_STABLE_SLOT = re.compile(r"^(?P<team>[AB])(?P<number>\d+)(?:~\d+)?$")
_OBSERVATION_FIELDS = (
    "overlay_positions",
    "history",
    "positions_m",
    "trajectory_m",
)


def stable_slot_id(value: Any) -> str | None:
    match = _STABLE_SLOT.fullmatch(str(value or "").removeprefix("slot-"))
    return (
        f"{match.group('team')}{int(match.group('number')):02d}"
        if match
        else None
    )


def slot_claims(
    document: dict[str, Any],
    key: str,
    *,
    source: str,
) -> dict[str, list[dict[str, Any]]]:
    """Return every slot claim for each tracklet, including duplicate slots."""
    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in document.get(key) or []:
        slot_id = stable_slot_id(
            row.get("stable_player_id")
            or row.get("slot_id")
            or row.get("stable_subject_id")
        )
        if not slot_id:
            continue
        claim = {
            "source": source,
            "stable_slot_id": slot_id,
            "stable_subject_id": row.get("stable_subject_id"),
            "team_label": str(row.get("team_label") or slot_id[0]),
            "slot_stints": list(row.get("stints") or []),
            "tracklet_id": None,
        }
        for tracklet_id in row.get("tracklet_ids") or []:
            output[str(tracklet_id)].append({**claim, "tracklet_id": str(tracklet_id)})
    return {
        tracklet_id: sorted(
            claims,
            key=lambda claim: (
                str(claim["stable_slot_id"]),
                str(claim.get("stable_subject_id") or ""),
            ),
        )
        for tracklet_id, claims in output.items()
    }


def artifact_membership_integrity(
    global_document: dict[str, Any],
    stable_document: dict[str, Any],
) -> dict[str, Any]:
    """Compare the derived stable view with the global slot membership source."""
    global_membership = _slot_membership(global_document, "slots")
    stable_membership = _slot_membership(stable_document, "players")
    exact_mirror = global_membership == stable_membership
    global_multi = _multi_slot_membership(global_membership)
    stable_multi = _multi_slot_membership(stable_membership)
    return {
        "classification": "exact_mirror" if exact_mirror else "stale_derived_artifact",
        "exact_mirror": exact_mirror,
        "global_slot_tracklet_membership": global_membership,
        "stable_slot_tracklet_membership": stable_membership,
        "global_only_membership": _membership_difference(
            global_membership, stable_membership
        ),
        "stable_only_membership": _membership_difference(
            stable_membership, global_membership
        ),
        "internal_multi_slot_tracklet_membership": {
            "global_identity": global_multi,
            "stable_players": stable_multi,
        },
    }


def global_observation_ownership(
    global_document: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return unambiguous global ownership only for multi-slot tracklets.

    A single global slot remains a fast tracklet-level assignment.  A multi-slot
    tracklet is deliberately assigned only at frames that the frozen global
    artifact itself attributes to exactly one slot.
    """
    claims = slot_claims(global_document, "slots", source="global_identity")
    multi_tracklets = {
        tracklet_id
        for tracklet_id, values in claims.items()
        if len({str(value["stable_slot_id"]) for value in values}) > 1
    }
    frame_claims: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for slot in global_document.get("slots") or []:
        slot_id = stable_slot_id(
            slot.get("stable_player_id")
            or slot.get("slot_id")
            or slot.get("stable_subject_id")
        )
        if not slot_id:
            continue
        for field in _OBSERVATION_FIELDS:
            for row in slot.get(field) or []:
                tracklet_id = str(row.get("tracklet_id") or "")
                if tracklet_id not in multi_tracklets or not _is_detected(row):
                    continue
                frame_claims[(tracklet_id, int(row.get("frame") or 0))].append(
                    {
                        "tracklet_id": tracklet_id,
                        "frame": int(row.get("frame") or 0),
                        "stable_slot_id": slot_id,
                        "stable_subject_id": slot.get("stable_subject_id"),
                        "team_label": str(slot.get("team_label") or slot_id[0]),
                        "ownership_evidence_field": field,
                        "ownership_evidence_source": "global_identity",
                    }
                )
    output = []
    for key, values in sorted(frame_claims.items()):
        slots = {str(value["stable_slot_id"]) for value in values}
        if len(slots) != 1:
            continue
        output.append(
            sorted(
                values,
                key=lambda value: (
                    _OBSERVATION_FIELDS.index(str(value["ownership_evidence_field"])),
                    str(value["stable_slot_id"]),
                ),
            )[0]
        )
    return output


def _slot_membership(document: dict[str, Any], key: str) -> dict[str, list[str]]:
    output: dict[str, set[str]] = defaultdict(set)
    for row in document.get(key) or []:
        slot_id = stable_slot_id(
            row.get("stable_player_id")
            or row.get("slot_id")
            or row.get("stable_subject_id")
        )
        if slot_id:
            output[slot_id].update(str(item) for item in row.get("tracklet_ids") or [])
    return {slot_id: sorted(tracklets) for slot_id, tracklets in sorted(output.items())}


def _membership_difference(
    left: dict[str, list[str]], right: dict[str, list[str]]
) -> dict[str, list[str]]:
    output = {}
    for slot_id in sorted(set(left) | set(right)):
        difference = sorted(set(left.get(slot_id, [])) - set(right.get(slot_id, [])))
        if difference:
            output[slot_id] = difference
    return output


def _multi_slot_membership(
    membership: dict[str, list[str]]
) -> list[dict[str, Any]]:
    slots_by_tracklet: dict[str, list[str]] = defaultdict(list)
    for slot_id, tracklets in membership.items():
        for tracklet_id in tracklets:
            slots_by_tracklet[tracklet_id].append(slot_id)
    return [
        {"tracklet_id": tracklet_id, "stable_slot_ids": sorted(slot_ids)}
        for tracklet_id, slot_ids in sorted(slots_by_tracklet.items())
        if len(slot_ids) > 1
    ]


def _is_detected(row: dict[str, Any]) -> bool:
    return (
        str(row.get("status") or "detected") == "detected"
        and str(row.get("source") or "detected")
        not in {"predicted", "interpolated", "unknown", "missing", "ambiguous"}
    )
