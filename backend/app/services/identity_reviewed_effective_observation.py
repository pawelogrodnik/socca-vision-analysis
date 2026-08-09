from __future__ import annotations

"""One canonical merge order for every reviewed frame observation."""

from collections import defaultdict
from typing import Any, Iterator


ObservationIndex = dict[tuple[str, int], dict[str, Any]]


def observation_index(rows: list[dict[str, Any]]) -> ObservationIndex:
    return {
        (str(row.get("tracklet_id")), int(row.get("frame") or 0)): row
        for row in rows
    }


def is_real_detected_position(position: dict[str, Any]) -> bool:
    status = str(position.get("status") or "detected")
    source = str(position.get("source") or "detected")
    return status == "detected" and source not in {
        "predicted",
        "interpolated",
        "unknown",
        "missing",
        "ambiguous",
    }


def effective_reviewed_observation(
    assignment: dict[str, Any],
    position: dict[str, Any],
    exact_overrides: ObservationIndex,
    safety_demotions: ObservationIndex,
    canonical_ownership: ObservationIndex | None = None,
    segment_overrides: ObservationIndex | None = None,
) -> dict[str, Any]:
    key = (
        str(assignment.get("tracklet_id") or position.get("tracklet_id") or ""),
        int(position.get("frame") or 0),
    )
    return {
        **position,
        **assignment,
        **((canonical_ownership or {}).get(key) or {}),
        **(exact_overrides.get(key) or {}),
        **((segment_overrides or {}).get(key) or {}),
        **(safety_demotions.get(key) or {}),
    }


def iter_effective_reviewed_observations(
    tracklets: dict[str, dict[str, Any]],
    assignments: list[dict[str, Any]],
    exact_overrides: list[dict[str, Any]],
    safety_demotions: list[dict[str, Any]],
    canonical_ownership: list[dict[str, Any]] | None = None,
    segment_overrides: list[dict[str, Any]] | None = None,
) -> Iterator[dict[str, Any]]:
    exact_index = observation_index(exact_overrides)
    segment_index = observation_index(segment_overrides or [])
    safety_index = observation_index(safety_demotions)
    canonical_index = observation_index(canonical_ownership or [])
    for assignment in assignments:
        tracklet_id = str(assignment.get("tracklet_id") or "")
        for position in tracklets.get(tracklet_id, {}).get("positions_m") or []:
            if not is_real_detected_position(position):
                continue
            yield effective_reviewed_observation(
                assignment,
                position,
                exact_index,
                safety_index,
                canonical_index,
                segment_index,
            )


def effective_observations_by_frame(
    tracklets: dict[str, dict[str, Any]],
    snapshot: dict[str, Any],
) -> dict[int, list[dict[str, Any]]]:
    output: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in iter_effective_reviewed_observations(
        tracklets,
        list(snapshot.get("tracklet_assignments") or []),
        list(snapshot.get("observation_overrides") or []),
        list(snapshot.get("observation_demotions") or []),
        list(snapshot.get("canonical_observation_assignments") or []),
        list(snapshot.get("segment_observation_assignments") or []),
    ):
        output[int(row.get("frame") or 0)].append(row)
    return dict(output)


def visible_reviewed_overlay(row: dict[str, Any]) -> bool:
    return str(row.get("identity_status") or "unresolved") not in {
        "false_detection",
        "ignored",
        "blocked",
    }


def visible_reviewed_player(row: dict[str, Any]) -> bool:
    return visible_reviewed_overlay(row) and str(
        row.get("identity_status") or "unresolved"
    ) != "referee"
