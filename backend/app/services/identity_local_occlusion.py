from __future__ import annotations

import math
from typing import Any


def build_local_occlusion_context(
    tracklets: list[dict[str, Any]],
    offline_identity_doc: dict[str, Any],
    occlusion_doc: dict[str, Any],
) -> dict[str, Any]:
    """Index passive image-space context used only for shadow gap classification."""
    subject_by_tracklet = {
        str(tracklet_id): str(subject.get("shadow_subject_id") or "")
        for subject in offline_identity_doc.get("subjects") or []
        for tracklet_id in subject.get("tracklet_ids") or []
    }
    team_by_tracklet: dict[str, str] = {}
    observations_by_frame: dict[int, list[dict[str, Any]]] = {}
    for tracklet in tracklets:
        tracklet_id = str(tracklet.get("tracklet_id") or "")
        if not tracklet_id:
            continue
        team_by_tracklet[tracklet_id] = str(tracklet.get("team_label") or "U")
        for position in tracklet.get("positions") or tracklet.get("positions_m") or []:
            bbox = _bbox(position.get("bbox_xyxy"))
            if bbox is None:
                continue
            frame = int(position.get("frame") or 0)
            observations_by_frame.setdefault(frame, []).append(
                {
                    "tracklet_id": tracklet_id,
                    "shadow_subject_id": subject_by_tracklet.get(tracklet_id),
                    "team_label": team_by_tracklet[tracklet_id],
                    "bbox_xyxy": bbox,
                }
            )

    event_ids_by_frame_tracklet: dict[tuple[int, str], set[str]] = {}
    event_cross_team: dict[str, bool] = {}
    for event in occlusion_doc.get("events") or []:
        event_id = str(event.get("event_id") or "")
        if not event_id:
            continue
        team_labels = {
            str(value)
            for value in event.get("team_labels") or []
            if str(value) in {"A", "B"}
        }
        event_cross_team[event_id] = len(team_labels) > 1
        start_frame = int(event.get("start_frame") or 0)
        end_frame = int(event.get("end_frame") or start_frame)
        for frame in range(start_frame, end_frame + 1):
            for tracklet_id in event.get("tracklet_ids") or []:
                event_ids_by_frame_tracklet.setdefault(
                    (frame, str(tracklet_id)),
                    set(),
                ).add(event_id)

    return {
        "observations_by_frame": observations_by_frame,
        "event_ids_by_frame_tracklet": event_ids_by_frame_tracklet,
        "event_cross_team": event_cross_team,
    }


def assess_local_occlusion(
    start_frame: int,
    end_frame: int,
    *,
    previous: dict[str, Any],
    following: dict[str, Any],
    shadow_subject_id: str,
    team_label: str,
    context: dict[str, Any],
    min_predicted_bbox_coverage: float,
    short_gap_max_frames: int,
    longer_gap_min_cross_team_ratio: float,
) -> dict[str, Any]:
    """Find a spatial blocker along an interpolated short-gap bbox path."""
    previous_bbox = _bbox(previous.get("bbox_xyxy"))
    following_bbox = _bbox(following.get("bbox_xyxy"))
    frame_count = end_frame - start_frame + 1
    empty = {
        "supported": False,
        "event_ids": [],
        "evidence": [],
        "cross_team": False,
        "blocker_tracklet_ids": [],
        "overlap_frame_count": 0,
        "cross_team_overlap_frame_count": 0,
        "event_overlap_frame_count": 0,
        "endpoint_overlap_count": 0,
        "endpoint_blocker_tracklet_ids": [],
        "max_endpoint_bbox_coverage": None,
        "max_predicted_bbox_coverage": None,
    }
    if previous_bbox is None or following_bbox is None or frame_count <= 0:
        return empty

    observations_by_frame = context.get("observations_by_frame") or {}
    event_ids_by_frame_tracklet = context.get("event_ids_by_frame_tracklet") or {}
    event_cross_team = context.get("event_cross_team") or {}
    source_tracklet_ids = {
        str(previous.get("tracklet_id") or ""),
        str(following.get("tracklet_id") or ""),
    }
    overlap_frames: set[int] = set()
    cross_team_frames: set[int] = set()
    event_frames: set[int] = set()
    blocker_tracklet_ids: set[str] = set()
    related_event_ids: set[str] = set()
    endpoint_blocker_tracklet_ids: set[str] = set()
    max_coverage = 0.0
    max_endpoint_coverage = 0.0
    interpolation_span = max(1, end_frame - start_frame + 2)

    for frame in range(start_frame, end_frame + 1):
        progress = (frame - start_frame + 1) / interpolation_span
        predicted_bbox = _interpolate_bbox(previous_bbox, following_bbox, progress)
        for row in observations_by_frame.get(frame) or []:
            blocker_tracklet_id = str(row.get("tracklet_id") or "")
            if blocker_tracklet_id in source_tracklet_ids:
                continue
            if row.get("shadow_subject_id") == shadow_subject_id:
                continue
            coverage = _intersection_over_left(predicted_bbox, row.get("bbox_xyxy"))
            if coverage < min_predicted_bbox_coverage:
                continue
            max_coverage = max(max_coverage, coverage)
            overlap_frames.add(frame)
            blocker_tracklet_ids.add(blocker_tracklet_id)
            blocker_team = str(row.get("team_label") or "U")
            known_cross_team = bool(
                team_label in {"A", "B"}
                and blocker_team in {"A", "B"}
                and blocker_team != team_label
            )
            if known_cross_team:
                cross_team_frames.add(frame)
            for event_id in event_ids_by_frame_tracklet.get(
                (frame, blocker_tracklet_id),
                set(),
            ):
                if known_cross_team or bool(event_cross_team.get(event_id)):
                    related_event_ids.add(event_id)
                    event_frames.add(frame)

    for frame, endpoint_bbox in (
        (start_frame - 1, previous_bbox),
        (end_frame + 1, following_bbox),
    ):
        for row in observations_by_frame.get(frame) or []:
            blocker_tracklet_id = str(row.get("tracklet_id") or "")
            if blocker_tracklet_id in source_tracklet_ids:
                continue
            if row.get("shadow_subject_id") == shadow_subject_id:
                continue
            blocker_team = str(row.get("team_label") or "U")
            if not (
                team_label in {"A", "B"}
                and blocker_team in {"A", "B"}
                and blocker_team != team_label
            ):
                continue
            coverage = _intersection_over_left(endpoint_bbox, row.get("bbox_xyxy"))
            if coverage < min_predicted_bbox_coverage:
                continue
            max_endpoint_coverage = max(max_endpoint_coverage, coverage)
            endpoint_blocker_tracklet_ids.add(blocker_tracklet_id)

    required_cross_team_frames = (
        1
        if frame_count <= short_gap_max_frames
        else max(1, math.ceil(frame_count * longer_gap_min_cross_team_ratio))
    )
    supported = bool(
        event_frames
        or (
            len(cross_team_frames) >= required_cross_team_frames
            and endpoint_blocker_tracklet_ids
        )
    )
    evidence: list[str] = []
    if overlap_frames:
        evidence.append("interpolated_bbox_overlap")
    if cross_team_frames:
        evidence.append("cross_team_spatial_blocker")
    if event_frames:
        evidence.append("spatially_related_occlusion_event")
    if endpoint_blocker_tracklet_ids:
        evidence.append("cross_team_endpoint_contact")
    return {
        "supported": supported,
        "event_ids": sorted(related_event_ids),
        "evidence": evidence,
        "cross_team": bool(cross_team_frames or event_frames),
        "blocker_tracklet_ids": sorted(blocker_tracklet_ids),
        "overlap_frame_count": len(overlap_frames),
        "cross_team_overlap_frame_count": len(cross_team_frames),
        "event_overlap_frame_count": len(event_frames),
        "endpoint_overlap_count": len(endpoint_blocker_tracklet_ids),
        "endpoint_blocker_tracklet_ids": sorted(endpoint_blocker_tracklet_ids),
        "max_endpoint_bbox_coverage": (
            round(max_endpoint_coverage, 4)
            if endpoint_blocker_tracklet_ids
            else None
        ),
        "max_predicted_bbox_coverage": round(max_coverage, 4) if overlap_frames else None,
    }


def _bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    bbox = [float(item) for item in value]
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return None
    return bbox


def _interpolate_bbox(left: list[float], right: list[float], progress: float) -> list[float]:
    return [
        left[index] + (right[index] - left[index]) * progress
        for index in range(4)
    ]


def _intersection_over_left(left: Any, right: Any) -> float:
    left_bbox = _bbox(left)
    right_bbox = _bbox(right)
    if left_bbox is None or right_bbox is None:
        return 0.0
    intersection_width = max(
        0.0,
        min(left_bbox[2], right_bbox[2]) - max(left_bbox[0], right_bbox[0]),
    )
    intersection_height = max(
        0.0,
        min(left_bbox[3], right_bbox[3]) - max(left_bbox[1], right_bbox[1]),
    )
    left_area = max(
        1.0,
        (left_bbox[2] - left_bbox[0]) * (left_bbox[3] - left_bbox[1]),
    )
    return intersection_width * intersection_height / left_area
