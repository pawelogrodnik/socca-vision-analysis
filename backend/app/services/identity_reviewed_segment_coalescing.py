from __future__ import annotations

"""Pure grouping policy for meaningful Reviewed Identity conflict episodes."""

from collections import defaultdict
from typing import Any, Mapping


MAX_SEGMENT_REVIEW_GAP_SEC = 0.25

ConflictGroup = tuple[str, str, str, str]


def coalesced_conflict_episodes(
    groups: Mapping[ConflictGroup, list[dict[str, Any]]],
    ownership: list[dict[str, Any]],
    *,
    fps: float,
) -> dict[ConflictGroup, list[list[int]]]:
    """Coalesce nearby same-owner runs without claiming or crossing gap frames."""
    max_gap_frames = max_segment_review_gap_frames(fps)
    owners_by_tracklet_frame: dict[tuple[str, int], set[tuple[str, str]]] = (
        defaultdict(set)
    )
    for claim in ownership:
        tracklet_id = str(claim.get("tracklet_id") or "")
        frame = int(claim.get("frame") or 0)
        slot_id = str(claim.get("stable_slot_id") or "")
        team_label = str(claim.get("team_label") or slot_id[:1] or "U")
        owners_by_tracklet_frame[(tracklet_id, frame)].add((slot_id, team_label))

    output: dict[ConflictGroup, list[list[int]]] = {}
    for group, claims in sorted(groups.items()):
        _, tracklet_id, slot_id, team_label = group
        runs = contiguous_frame_runs(claims)
        episodes: list[list[int]] = []
        for run in runs:
            if not episodes:
                episodes.append(run)
                continue
            gap_start = episodes[-1][-1] + 1
            gap_end = run[0] - 1
            gap_frames = max(0, gap_end - gap_start + 1)
            crosses_owner_transition = any(
                owners_by_tracklet_frame.get((tracklet_id, frame), set())
                - {(slot_id, team_label)}
                for frame in range(gap_start, gap_end + 1)
            )
            if gap_frames <= max_gap_frames and not crosses_owner_transition:
                episodes[-1].extend(run)
            else:
                episodes.append(run)
        output[group] = episodes
    return output


def max_segment_review_gap_frames(fps: float) -> int:
    safe_fps = fps if fps > 0 else 30.0
    return int(safe_fps * MAX_SEGMENT_REVIEW_GAP_SEC)


def contiguous_frame_runs(claims: list[dict[str, Any]]) -> list[list[int]]:
    runs: list[list[int]] = []
    for frame in sorted({int(row.get("frame") or 0) for row in claims}):
        if not runs or frame != runs[-1][-1] + 1:
            runs.append([frame])
        else:
            runs[-1].append(frame)
    return runs


def exact_frame_ranges(frames: list[int]) -> list[list[int]]:
    return [
        [run[0], run[-1]]
        for run in contiguous_frame_runs([{"frame": frame} for frame in frames])
    ]
