from __future__ import annotations

"""Server-owned lane sources for concurrent Mixed identity resolution."""

from collections import defaultdict
from typing import Any

from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_reviewed_mixed_topology import analyze_temporal_split_topology


CONCURRENT_LANE_TOPOLOGY_STALE = "concurrent_lane_topology_stale"
CONCURRENT_LANE_SET_STALE = "concurrent_lane_set_stale"
CONCURRENT_LANE_SOURCE_STALE = "concurrent_lane_source_stale"


class ConcurrentLaneResolutionError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def derive_concurrent_lanes(
    parent_case_id: str,
    parent_source_digest: str,
    observations: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Derive stable exact lanes from one current concurrent parent source."""
    topology = analyze_temporal_split_topology(observations)
    if topology["kind"] != "concurrent":
        raise ConcurrentLaneResolutionError(CONCURRENT_LANE_TOPOLOGY_STALE)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for observation in observations:
        grouped[str(observation["tracklet_id"])].append(observation)
    overlaps_by_tracklet: dict[str, set[str]] = defaultdict(set)
    for overlap in topology.get("overlap_ranges") or []:
        tracklet_ids = [str(value) for value in overlap.get("tracklet_ids") or []]
        for tracklet_id in tracklet_ids:
            overlaps_by_tracklet[tracklet_id].update(
                value for value in tracklet_ids if value != tracklet_id
            )

    lanes: list[dict[str, Any]] = []
    for tracklet in topology["tracklets"]:
        tracklet_id = str(tracklet["tracklet_id"])
        lane_observations = sorted(
            grouped[tracklet_id],
            key=lambda row: int(row["frame"]),
        )
        owned = [
            {"tracklet_id": tracklet_id, "frame": int(row["frame"])}
            for row in lane_observations
        ]
        digest = canonical_digest(
            {
                "parent_case_id": parent_case_id,
                "parent_source_digest": parent_source_digest,
                "tracklet_id": tracklet_id,
                "owned_observations": owned,
            }
        )
        lanes.append(
            {
                "lane_id": f"review-mixed-lane:v1:{digest}",
                "tracklet_id": tracklet_id,
                "source_ownership_digest": digest,
                "frame_start": int(tracklet["frame_start"]),
                "frame_end": int(tracklet["frame_end"]),
                "observation_count": len(lane_observations),
                "overlap_tracklet_ids": sorted(overlaps_by_tracklet[tracklet_id]),
                "owned_observations": owned,
                "observations": lane_observations,
            }
        )
    lane_id_by_tracklet = {
        str(lane["tracklet_id"]): str(lane["lane_id"])
        for lane in lanes
    }
    for lane in lanes:
        lane["overlap_lane_ids"] = [
            lane_id_by_tracklet[tracklet_id]
            for tracklet_id in lane.pop("overlap_tracklet_ids")
            if tracklet_id in lane_id_by_tracklet
        ]
    return topology, lanes


def validate_concurrent_lane_resolutions(
    lanes: list[dict[str, Any]],
    submitted: list[Any],
) -> list[dict[str, Any]]:
    """Require one current, complete and non-duplicated resolution per lane."""
    expected = {str(lane["lane_id"]): lane for lane in lanes}
    rows = [dict(row) for row in submitted if isinstance(row, dict)]
    supplied_ids = [str(row.get("lane_id") or "") for row in rows]
    if len(rows) != len(submitted) or len(set(supplied_ids)) != len(supplied_ids):
        raise ConcurrentLaneResolutionError(CONCURRENT_LANE_SET_STALE)
    if set(supplied_ids) != set(expected):
        raise ConcurrentLaneResolutionError(CONCURRENT_LANE_SET_STALE)

    normalized: list[dict[str, Any]] = []
    for row in rows:
        lane = expected[str(row["lane_id"])]
        if str(row.get("lane_source_digest") or "") != str(
            lane["source_ownership_digest"]
        ):
            raise ConcurrentLaneResolutionError(CONCURRENT_LANE_SOURCE_STALE)
        kind = str(row.get("resolution") or "")
        if kind == "direct":
            assignment = row.get("assignment")
            if not isinstance(assignment, dict):
                raise ValueError("Direct lane resolution requires an assignment")
            normalized.append(
                {
                    "lane_id": lane["lane_id"],
                    "lane_source_digest": lane["source_ownership_digest"],
                    "resolution": "direct",
                    "assignment": dict(assignment),
                }
            )
            continue
        if kind != "temporal_split":
            raise ValueError("Unsupported concurrent lane resolution")
        boundaries = sorted({int(value) for value in row.get("split_after_frames") or []})
        groups = split_lane_observations(list(lane["observations"]), boundaries)
        assignments = row.get("segment_assignments") or []
        if not boundaries or len(assignments) != len(groups):
            raise ValueError("Every lane split segment requires one assignment")
        if any(not isinstance(assignment, dict) for assignment in assignments):
            raise ValueError("Every lane split segment requires one assignment")
        normalized.append(
            {
                "lane_id": lane["lane_id"],
                "lane_source_digest": lane["source_ownership_digest"],
                "resolution": "temporal_split",
                "split_after_frames": boundaries,
                "segment_assignments": [dict(value) for value in assignments],
            }
        )
    return normalized


def split_lane_observations(
    observations: list[dict[str, Any]],
    boundaries: list[int],
) -> list[list[dict[str, Any]]]:
    frames = sorted({int(row["frame"]) for row in observations})
    if not frames:
        raise ValueError("Concurrent lane has no observations")
    normalized = sorted({int(value) for value in boundaries})
    if normalized and (normalized[0] < frames[0] or normalized[-1] >= frames[-1]):
        raise ValueError("Lane split point must be inside the lane observation range")
    groups = [[] for _ in range(len(normalized) + 1)]
    for observation in observations:
        index = sum(int(observation["frame"]) > boundary for boundary in normalized)
        groups[index].append(observation)
    if any(not group for group in groups):
        raise ValueError("Every lane split segment must contain observations")
    return groups


def concurrent_resolution_semantic_digest(
    resolutions: list[dict[str, Any]],
) -> str:
    return canonical_digest(
        [
            {
                "lane_id": row.get("lane_id"),
                "lane_source_digest": row.get("lane_source_digest"),
                "resolution": row.get("resolution"),
                "assignment": _assignment(row.get("assignment")),
                "split_after_frames": row.get("split_after_frames") or [],
                "segment_assignments": [
                    _assignment(value)
                    for value in row.get("segment_assignments") or []
                ],
            }
            for row in sorted(resolutions, key=lambda value: str(value.get("lane_id") or ""))
        ]
    )


def expanded_concurrent_lane_segments(
    lanes: list[dict[str, Any]],
    resolutions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Expand validated lane drafts into exact child groups and assignments."""
    resolution_by_lane = {
        str(row["lane_id"]): row
        for row in resolutions
    }
    expanded: list[dict[str, Any]] = []
    for lane_index, lane in enumerate(lanes):
        resolution = resolution_by_lane[str(lane["lane_id"])]
        if resolution["resolution"] == "direct":
            groups = [list(lane["observations"])]
            assignments = [dict(resolution["assignment"])]
        else:
            groups = split_lane_observations(
                list(lane["observations"]),
                list(resolution["split_after_frames"]),
            )
            assignments = [dict(value) for value in resolution["segment_assignments"]]
        for segment_index, (group, assignment) in enumerate(
            zip(groups, assignments, strict=True)
        ):
            expanded.append(
                {
                    "lane": lane,
                    "lane_index": lane_index,
                    "segment_index": segment_index,
                    "observations": group,
                    "assignment": assignment,
                }
            )
    return expanded


def _assignment(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        key: value.get(key)
        for key in ("action", "player_id", "stable_slot_id", "team_label")
        if value.get(key) is not None
    }
