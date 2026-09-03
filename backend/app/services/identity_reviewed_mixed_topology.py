from __future__ import annotations

"""Fail-closed topology analysis for temporal Mixed-player splits."""

from collections import defaultdict
from typing import Any


TEMPORAL_SPLIT_NOT_SEPARABLE = "temporal_split_not_separable"


class MixedTemporalTopologyError(ValueError):
    """The exact source cannot be represented by global time boundaries."""

    def __init__(self, code: str = TEMPORAL_SPLIT_NOT_SEPARABLE) -> None:
        super().__init__(code)
        self.code = code


def analyze_temporal_split_topology(
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Classify exact owned tracklet lifetimes as serial or concurrent.

    Lifetimes are inclusive. Sparse detections therefore remain concurrent
    when their owned tracklet extents overlap even without an identical
    observed frame.
    """
    frames_by_tracklet: dict[str, list[int]] = defaultdict(list)
    for observation in observations:
        tracklet_id = str(observation.get("tracklet_id") or "").strip()
        frame = observation.get("frame")
        if not tracklet_id or frame is None:
            raise ValueError("temporal_topology_unclassifiable")
        frames_by_tracklet[tracklet_id].append(int(frame))

    if not frames_by_tracklet:
        raise ValueError("temporal_topology_unclassifiable")

    tracklets = sorted(
        (
            {
                "tracklet_id": tracklet_id,
                "frame_start": min(frames),
                "frame_end": max(frames),
                "observation_count": len(frames),
            }
            for tracklet_id, frames in frames_by_tracklet.items()
        ),
        key=lambda row: (
            int(row["frame_start"]),
            int(row["frame_end"]),
            str(row["tracklet_id"]),
        ),
    )

    additions: dict[int, set[str]] = defaultdict(set)
    removals: dict[int, set[str]] = defaultdict(set)
    event_frames: set[int] = set()
    for tracklet in tracklets:
        start = int(tracklet["frame_start"])
        after_end = int(tracklet["frame_end"]) + 1
        tracklet_id = str(tracklet["tracklet_id"])
        additions[start].add(tracklet_id)
        removals[after_end].add(tracklet_id)
        event_frames.update((start, after_end))

    active: set[str] = set()
    previous_frame: int | None = None
    overlap_ranges: list[dict[str, Any]] = []
    max_concurrent_tracklets = 0
    for event_frame in sorted(event_frames):
        if (
            previous_frame is not None
            and previous_frame <= event_frame - 1
            and len(active) >= 2
        ):
            tracklet_ids = sorted(active)
            if (
                overlap_ranges
                and overlap_ranges[-1]["frame_end"] + 1 == previous_frame
                and overlap_ranges[-1]["tracklet_ids"] == tracklet_ids
            ):
                overlap_ranges[-1]["frame_end"] = event_frame - 1
            else:
                overlap_ranges.append(
                    {
                        "frame_start": previous_frame,
                        "frame_end": event_frame - 1,
                        "tracklet_ids": tracklet_ids,
                    }
                )
        active.difference_update(removals.get(event_frame, set()))
        active.update(additions.get(event_frame, set()))
        max_concurrent_tracklets = max(max_concurrent_tracklets, len(active))
        previous_frame = event_frame

    concurrent = bool(overlap_ranges)
    return {
        "kind": "concurrent" if concurrent else "serial",
        "simple_split_allowed": not concurrent,
        "tracklet_count": len(tracklets),
        "max_concurrent_tracklets": max_concurrent_tracklets,
        "overlap_ranges": overlap_ranges,
        "tracklets": tracklets,
    }


def require_simple_temporal_split(observations: list[dict[str, Any]]) -> None:
    topology = analyze_temporal_split_topology(observations)
    if not topology["simple_split_allowed"]:
        raise MixedTemporalTopologyError()
