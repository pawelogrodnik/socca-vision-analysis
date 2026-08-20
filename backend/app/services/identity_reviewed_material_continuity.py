from __future__ import annotations

"""Conservative, operator-safe continuity cases for Reviewed Identity.

Coverage answers whether a team has enough named observations overall.  It does
not answer whether one otherwise known player has disappeared for a material
continuous interval.  This module keeps that second question isolated from the
coverage policy so its threshold can be calibrated independently.
"""

from collections import defaultdict
from math import ceil
from typing import Any


MATERIAL_CONTINUITY_POLICY_VERSION = "material-continuity:v1-safe-team-a-20s-4fragments"
# V1 intentionally promotes only the single, severe class observed in the
# acceptance match.  It is not a claim that 20 seconds is the final product
# threshold for longer matches.
MATERIAL_CONTINUITY_MIN_SPAN_SEC = 20.0
# A lone long anonymous tracker fragment is still an optional naming task. V1
# promotes only a material *continuity failure* split across several exact,
# safe subjects. This prevents the coverage-independent queue from expanding
# to every long anonymous player until we calibrate the policy on longer games.
MATERIAL_CONTINUITY_MIN_FRAGMENT_COUNT = 4
MATERIAL_CONTINUITY_MAX_JOIN_GAP_SEC = 1.0
MATERIAL_CONTINUITY_MAX_EVIDENCE_CROPS = 5


def coalesce_material_continuity_units(
    units: list[dict[str, Any]],
    fps: float,
) -> list[dict[str, Any]]:
    """Replace safe, adjacent anonymous Team-A fragments with one case.

    The grouped unit owns the exact union of its members' detected pairs.
    Stable slots are used only as a local continuity hypothesis; the resulting
    decision still writes separate subject-scoped operator decisions.
    """
    safe_fps = fps if fps > 0 else 30.0
    by_slot: dict[str, list[dict[str, Any]]] = defaultdict(list)
    max_frame_gap = max(1, int(ceil(MATERIAL_CONTINUITY_MAX_JOIN_GAP_SEC * safe_fps)))
    for unit in units:
        slot = str(unit.get("stable_slot_id") or "")
        if _eligible(unit, slot):
            by_slot[slot].extend(_continuous_member_runs(unit, max_frame_gap))

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
    if len(subject_ids) < MATERIAL_CONTINUITY_MIN_FRAGMENT_COUNT:
        return None
    crops = _balanced_anchor_crops(members)
    if not crops:
        return None
    frame_start, frame_end = frames[0], frames[-1]
    group_id = f"continuity:{slot}:{frame_start}-{frame_end}"
    return {
        "candidate_subject_id": group_id,
        "continuity_group_id": group_id,
        "continuity_subject_ids": subject_ids,
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
    }


def _continuous_member_runs(
    unit: dict[str, Any],
    max_frame_gap: int,
) -> list[dict[str, Any]]:
    pairs_by_frame: dict[int, list[tuple[str, int]]] = defaultdict(list)
    for raw_pair in unit.get("detected_pairs") or []:
        if not isinstance(raw_pair, (tuple, list)) or len(raw_pair) < 2:
            continue
        pair = (str(raw_pair[0]), int(raw_pair[1]))
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
        pairs = {
            (str(raw_pair[0]), int(raw_pair[1]))
            for raw_pair in unit.get("detected_pairs") or []
            if isinstance(raw_pair, (tuple, list)) and len(raw_pair) >= 2
        }
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
