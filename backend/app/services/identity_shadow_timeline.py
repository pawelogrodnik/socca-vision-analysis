from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any

from app.services.identity_local_occlusion import (
    assess_local_occlusion,
    build_local_occlusion_context,
)


SCHEMA_VERSION = "0.3.0"
ALGORITHM_NAME = "offline_identity_shadow_timeline"
ALGORITHM_VERSION = "0.3.0"

DEFAULT_PARAMETERS: dict[str, Any] = {
    "predicted_max_gap_sec": 0.6,
    "predicted_single_reliable_endpoint_max_gap_sec": 0.5,
    "predicted_max_required_speed_mps": 15.0,
    "occluded_max_gap_sec": 1.5,
    "occlusion_nearby_context_frames": 2,
    "occlusion_low_appearance_context_frames": 6,
    "occlusion_low_appearance_ratio": 0.7,
    "local_occlusion_min_predicted_bbox_coverage": 0.2,
    "local_occlusion_short_gap_max_frames": 4,
    "local_occlusion_longer_gap_min_cross_team_ratio": 0.5,
    "identity_risk_max_team_confidence": 0.7,
    "detected_status": "detected",
    "non_observed_statuses": ["predicted", "occluded", "missing"],
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_shadow_resolved_timeline(
    offline_identity_doc: dict[str, Any],
    tracklets: list[dict[str, Any]],
    quality_doc: dict[str, Any],
    *,
    fps: float,
    occlusion_doc: dict[str, Any] | None = None,
    generated_at: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Expand the P1 graph into a read-only, frame-addressable subject timeline."""
    params = {**DEFAULT_PARAMETERS, **(parameters or {})}
    safe_fps = max(float(fps), 1e-6)
    tracklet_by_id = {
        str(row.get("tracklet_id")): row
        for row in tracklets
        if row.get("tracklet_id") is not None
    }
    quality_by_id = {
        str(row.get("tracklet_id")): row
        for row in quality_doc.get("tracklets") or []
        if row.get("tracklet_id") is not None
    }
    edge_by_pair = {
        (str(row.get("source_tracklet_id")), str(row.get("target_tracklet_id"))): row
        for row in offline_identity_doc.get("accepted_edges") or []
    }
    occlusions_by_tracklet = _occlusions_by_tracklet(occlusion_doc or {})
    local_occlusion_context = build_local_occlusion_context(
        tracklets,
        offline_identity_doc,
        occlusion_doc or {},
    )

    subjects: list[dict[str, Any]] = []
    transition_events: list[dict[str, Any]] = []
    duplicate_observation_frames = 0
    for subject in offline_identity_doc.get("subjects") or []:
        subject_doc, subject_events, duplicate_frames = _build_subject_timeline(
            subject,
            tracklet_by_id=tracklet_by_id,
            quality_by_id=quality_by_id,
            edge_by_pair=edge_by_pair,
            occlusions_by_tracklet=occlusions_by_tracklet,
            local_occlusion_context=local_occlusion_context,
            fps=safe_fps,
            parameters=params,
        )
        subjects.append(subject_doc)
        transition_events.extend(subject_events)
        duplicate_observation_frames += duplicate_frames

    status_frames: Counter[str] = Counter()
    trusted_detected_frames = 0
    for subject in subjects:
        status_frames.update(subject.get("status_frame_counts") or {})
        trusted_detected_frames += int(subject.get("trusted_detected_frames") or 0)
    detected_frames = int(status_frames["detected"])
    active_frames = sum(int(value) for value in status_frames.values())
    continuity_statuses = Counter(
        str(run.get("identity_continuity_status") or "supported")
        for subject in subjects
        for run in subject.get("state_runs") or []
        if run.get("status") != "detected"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or now_iso(),
        "mode": "shadow_read_only",
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION},
        "parameters": params,
        "identity_semantics": {
            "detected": "A real tracklet observation exists for this subject and frame.",
            "predicted": "A short non-occlusion gap is bridged only in the shadow timeline.",
            "occluded": "The subject is held through a short gap supported by an occlusion event.",
            "missing": "The graph links both sides, but the gap has insufficient evidence for prediction.",
            "statistics": "Only reliable detected observations are eligible for distance and heatmaps.",
        },
        "state_encoding": {
            "observations": "Frame-addressable detected rows after deterministic duplicate resolution.",
            "state_runs": "Inclusive frame ranges for detected, predicted, occluded and missing states.",
            "transition_events": "Accepted graph edges, including direct and overlapping transitions without gaps.",
        },
        "source": {
            "offline_identity_algorithm": offline_identity_doc.get("algorithm") or {},
            "offline_identity_subjects": len(offline_identity_doc.get("subjects") or []),
            "tracklet_quality_algorithm": quality_doc.get("algorithm") or {},
        },
        "summary": {
            "shadow_subjects": len(subjects),
            "transition_events": len(transition_events),
            "cross_production_transition_events": sum(
                event.get("current_identity_relation") == "different_subjects"
                for event in transition_events
            ),
            "status_frame_counts": dict(sorted(status_frames.items())),
            "status_seconds": {
                status: round(int(frames) / safe_fps, 3)
                for status, frames in sorted(status_frames.items())
            },
            "detected_frames": detected_frames,
            "trusted_detected_frames": trusted_detected_frames,
            "active_timeline_frames": active_frames,
            "trusted_detected_ratio": round(
                trusted_detected_frames / detected_frames,
                4,
            ) if detected_frames else None,
            "observed_coverage_ratio": round(
                detected_frames / active_frames,
                4,
            ) if active_frames else None,
            "duplicate_observation_frames_resolved": duplicate_observation_frames,
            "gap_identity_continuity": dict(sorted(continuity_statuses.items())),
            "gap_identity_reviews_required": int(continuity_statuses["uncertain"]),
            "statistics_eligible_statuses": ["detected"],
        },
        "transition_events": sorted(
            transition_events,
            key=lambda row: (
                int(row.get("start_frame") or 0),
                str(row.get("shadow_subject_id") or ""),
                str(row.get("edge_key") or ""),
            ),
        ),
        "subjects": sorted(
            subjects,
            key=lambda row: (
                str(row.get("team_label") or "U"),
                int(row.get("start_frame") or 0),
                str(row.get("shadow_subject_id") or ""),
            ),
        ),
    }


def _build_subject_timeline(
    subject: dict[str, Any],
    *,
    tracklet_by_id: dict[str, dict[str, Any]],
    quality_by_id: dict[str, dict[str, Any]],
    edge_by_pair: dict[tuple[str, str], dict[str, Any]],
    occlusions_by_tracklet: dict[str, list[dict[str, Any]]],
    local_occlusion_context: dict[str, Any],
    fps: float,
    parameters: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    observations_by_frame: dict[int, list[dict[str, Any]]] = {}
    observations_by_tracklet: dict[str, list[dict[str, Any]]] = {}
    tracklet_ids = [
        str(value)
        for value in subject.get("tracklet_ids") or []
        if str(value) in tracklet_by_id
    ]
    for tracklet_id in tracklet_ids:
        tracklet = tracklet_by_id[tracklet_id]
        quality = quality_by_id.get(tracklet_id) or {}
        for position in _positions(tracklet):
            frame = int(position.get("frame") or 0)
            pitch_m = _point(position.get("smoothed_pitch_m") or position.get("pitch_m"))
            inside_play = position.get("play_area_status") == "inside_play"
            footpoint_reliable = not _frame_in_ranges(
                frame,
                quality.get("unreliable_footpoint_ranges") or [],
            )
            appearance_reliable = not _frame_in_ranges(
                frame,
                quality.get("unreliable_appearance_ranges") or [],
            )
            confidence = float(position.get("confidence") or tracklet.get("mean_confidence") or 0.0)
            observation = {
                "frame": frame,
                "time_sec": round(float(position.get("time_sec") or frame / fps), 3),
                "status": "detected",
                "tracklet_id": tracklet_id,
                "pitch_m": pitch_m,
                "bbox_xyxy": position.get("bbox_xyxy"),
                "confidence": round(confidence, 4),
                "quality_class": quality.get("quality_class"),
                "quality_reasons": quality.get("reasons") or [],
                "team_confidence": quality.get("team_confidence"),
                "appearance_reliable_ratio": quality.get("appearance_reliable_ratio"),
                "footpoint_reliable": footpoint_reliable,
                "appearance_reliable": appearance_reliable,
                "play_area_status": position.get("play_area_status"),
                "eligible_for_distance": bool(footpoint_reliable and inside_play and pitch_m is not None),
                "eligible_for_heatmap": bool(footpoint_reliable and inside_play and pitch_m is not None),
            }
            observations_by_frame.setdefault(frame, []).append(observation)
            observations_by_tracklet.setdefault(tracklet_id, []).append(observation)

    duplicate_frames = sum(len(rows) - 1 for rows in observations_by_frame.values() if len(rows) > 1)
    observations = [
        _select_observation(rows)
        for _, rows in sorted(observations_by_frame.items())
    ]
    runs: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    for observation in observations:
        if previous is not None and int(observation["frame"]) > int(previous["frame"]) + 1:
            gap_start = int(previous["frame"]) + 1
            gap_end = int(observation["frame"]) - 1
            pair = (str(previous["tracklet_id"]), str(observation["tracklet_id"]))
            edge = edge_by_pair.get(pair)
            gap_run = _gap_run(
                gap_start,
                gap_end,
                previous=previous,
                following=observation,
                edge=edge,
                occlusions_by_tracklet=occlusions_by_tracklet,
                local_occlusion_context=local_occlusion_context,
                shadow_subject_id=str(subject.get("shadow_subject_id") or ""),
                team_label=str(subject.get("team_label") or "U"),
                fps=fps,
                parameters=parameters,
            )
            _append_run(runs, gap_run)
        _append_run(
            runs,
            {
                "status": "detected",
                "start_frame": int(observation["frame"]),
                "end_frame": int(observation["frame"]),
                "tracklet_id": observation["tracklet_id"],
                "footpoint_reliable": observation["footpoint_reliable"],
                "appearance_reliable": observation["appearance_reliable"],
                "eligible_for_distance": observation["eligible_for_distance"],
                "eligible_for_heatmap": observation["eligible_for_heatmap"],
            },
        )
        previous = observation

    transitions = _subject_transition_events(
        subject,
        tracklet_ids=tracklet_ids,
        observations_by_tracklet=observations_by_tracklet,
        edge_by_pair=edge_by_pair,
        occlusions_by_tracklet=occlusions_by_tracklet,
        local_occlusion_context=local_occlusion_context,
        fps=fps,
        parameters=parameters,
    )

    status_counts = Counter()
    for run in runs:
        status_counts[str(run["status"])] += int(run["end_frame"]) - int(run["start_frame"]) + 1
    trusted_detected = sum(
        1
        for row in observations
        if row.get("eligible_for_distance")
    )
    return {
        "shadow_subject_id": subject.get("shadow_subject_id"),
        "team_label": subject.get("team_label"),
        "tracklet_ids": tracklet_ids,
        "production_subject_ids": subject.get("production_subject_ids") or [],
        "start_frame": observations[0]["frame"] if observations else subject.get("start_frame"),
        "end_frame": observations[-1]["frame"] if observations else subject.get("end_frame"),
        "observations": observations,
        "state_runs": runs,
        "status_frame_counts": dict(sorted(status_counts.items())),
        "trusted_detected_frames": trusted_detected,
        "quality_flags": sorted(
            set(subject.get("quality_flags") or [])
            | ({"duplicate_observations_resolved"} if duplicate_frames else set())
        ),
    }, transitions, duplicate_frames


def _gap_run(
    start_frame: int,
    end_frame: int,
    *,
    previous: dict[str, Any],
    following: dict[str, Any],
    edge: dict[str, Any] | None,
    occlusions_by_tracklet: dict[str, list[dict[str, Any]]],
    local_occlusion_context: dict[str, Any],
    shadow_subject_id: str,
    team_label: str,
    fps: float,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    frame_count = end_frame - start_frame + 1
    duration_sec = frame_count / fps
    endpoint_reliability = (
        bool(previous.get("footpoint_reliable")),
        bool(following.get("footpoint_reliable")),
    )
    positions_available = bool(
        previous.get("pitch_m") is not None
        and following.get("pitch_m") is not None
    )
    endpoints_reliable = bool(
        all(endpoint_reliability)
        and positions_available
    )
    occlusion = _occlusion_support(
        start_frame,
        end_frame,
        previous=previous,
        following=following,
        edge=edge,
        occlusions_by_tracklet=occlusions_by_tracklet,
        nearby_context_frames=int(parameters["occlusion_nearby_context_frames"]),
        low_appearance_context_frames=int(parameters["occlusion_low_appearance_context_frames"]),
        low_appearance_ratio=float(parameters["occlusion_low_appearance_ratio"]),
    )
    local_occlusion = assess_local_occlusion(
        start_frame,
        end_frame,
        previous=previous,
        following=following,
        shadow_subject_id=shadow_subject_id,
        team_label=team_label,
        context=local_occlusion_context,
        min_predicted_bbox_coverage=float(
            parameters["local_occlusion_min_predicted_bbox_coverage"]
        ),
        short_gap_max_frames=int(parameters["local_occlusion_short_gap_max_frames"]),
        longer_gap_min_cross_team_ratio=float(
            parameters["local_occlusion_longer_gap_min_cross_team_ratio"]
        ),
    )
    occlusion["event_ids"] = sorted(
        set(occlusion["event_ids"]) | set(local_occlusion["event_ids"])
    )
    occlusion["evidence"] = sorted(
        set(occlusion["evidence"]) | set(local_occlusion["evidence"])
    )
    occlusion["cross_team"] = bool(
        occlusion["cross_team"] or local_occlusion["cross_team"]
    )
    identity_continuity = _identity_continuity_assessment(
        previous,
        following,
        occlusion=occlusion,
        parameters=parameters,
    )
    endpoint_distance_m = _distance(previous.get("pitch_m"), following.get("pitch_m"))
    required_speed_mps = endpoint_distance_m / max(duration_sec, 1e-6) if endpoint_distance_m is not None else None
    same_raw_tracklet = previous.get("tracklet_id") == following.get("tracklet_id")
    endpoints_predictable = bool(
        positions_available
        and identity_continuity["identity_continuity_status"] == "supported"
        and required_speed_mps is not None
        and required_speed_mps <= float(parameters["predicted_max_required_speed_mps"])
        and (
            endpoints_reliable
            or (
                edge is not None
                and any(endpoint_reliability)
                and duration_sec <= float(parameters["predicted_single_reliable_endpoint_max_gap_sec"])
            )
            or same_raw_tracklet
        )
    )
    has_occlusion = bool(
        occlusion["event_ids"] or local_occlusion["supported"]
    )
    if has_occlusion and duration_sec <= float(parameters["occluded_max_gap_sec"]):
        status = "occluded"
        reason = (
            "gap_with_local_contact_occlusion_evidence"
            if local_occlusion["supported"]
            else "gap_with_nearby_occlusion_evidence"
        )
    elif endpoints_predictable and duration_sec <= float(parameters["predicted_max_gap_sec"]):
        status = "predicted"
        reason = "short_gap_with_reliable_endpoints"
    else:
        status = "missing"
        reason = "insufficient_evidence_for_shadow_prediction"
    return {
        "status": status,
        "start_frame": start_frame,
        "end_frame": end_frame,
        "frame_count": frame_count,
        "duration_sec": round(duration_sec, 4),
        "reason": reason,
        "edge_key": edge.get("edge_key") if edge else None,
        "occlusion_event_ids": occlusion["event_ids"],
        "occlusion_evidence": occlusion["evidence"],
        "cross_team_occlusion": occlusion["cross_team"],
        "local_occlusion_evidence": local_occlusion,
        **identity_continuity,
        "required_speed_mps": round(required_speed_mps, 4) if required_speed_mps is not None else None,
        "position_source": "linear_endpoint_prediction" if status in {"predicted", "occluded"} and positions_available else None,
        "start_pitch_m": previous.get("pitch_m") if status in {"predicted", "occluded"} and positions_available else None,
        "end_pitch_m": following.get("pitch_m") if status in {"predicted", "occluded"} and positions_available else None,
        "eligible_for_distance": False,
        "eligible_for_heatmap": False,
    }


def _transition_event(
    subject: dict[str, Any],
    *,
    edge: dict[str, Any],
    gap_run: dict[str, Any],
    previous: dict[str, Any],
    following: dict[str, Any],
) -> dict[str, Any]:
    return {
        "event_key": _event_key(str(edge.get("edge_key") or ""), str(subject.get("shadow_subject_id") or "")),
        "shadow_subject_id": subject.get("shadow_subject_id"),
        "team_label": subject.get("team_label"),
        "edge_key": edge.get("edge_key"),
        "source_tracklet_id": edge.get("source_tracklet_id"),
        "target_tracklet_id": edge.get("target_tracklet_id"),
        "start_frame": gap_run.get("start_frame"),
        "end_frame": gap_run.get("end_frame"),
        "status": gap_run.get("status"),
        "confidence": edge.get("confidence"),
        "recommendation_source": edge.get("recommendation_source"),
        "occlusion_event_ids": edge.get("occlusion_event_ids") or [],
        "current_source_subject_ids": edge.get("current_source_subject_ids") or [],
        "current_target_subject_ids": edge.get("current_target_subject_ids") or [],
        "current_identity_relation": edge.get("current_identity_relation"),
        "identity_continuity_status": gap_run.get("identity_continuity_status", "supported"),
        "identity_risk_reasons": gap_run.get("identity_risk_reasons") or [],
        "source_frame": previous.get("frame"),
        "target_frame": following.get("frame"),
        "requires_review": bool(
            edge.get("current_identity_relation") == "different_subjects"
            or gap_run.get("identity_continuity_status") == "uncertain"
        ),
    }


def _subject_transition_events(
    subject: dict[str, Any],
    *,
    tracklet_ids: list[str],
    observations_by_tracklet: dict[str, list[dict[str, Any]]],
    edge_by_pair: dict[tuple[str, str], dict[str, Any]],
    occlusions_by_tracklet: dict[str, list[dict[str, Any]]],
    local_occlusion_context: dict[str, Any],
    fps: float,
    parameters: dict[str, Any],
) -> list[dict[str, Any]]:
    transitions: list[dict[str, Any]] = []
    subject_tracklets = set(tracklet_ids)
    for pair, edge in sorted(edge_by_pair.items()):
        source_tracklet_id, target_tracklet_id = pair
        if not {source_tracklet_id, target_tracklet_id} <= subject_tracklets:
            continue
        source_observations = observations_by_tracklet.get(source_tracklet_id) or []
        target_observations = observations_by_tracklet.get(target_tracklet_id) or []
        if not source_observations or not target_observations:
            continue
        previous = max(source_observations, key=lambda row: int(row["frame"]))
        following = min(target_observations, key=lambda row: int(row["frame"]))
        frame_delta = int(following["frame"]) - int(previous["frame"])
        if frame_delta > 1:
            transition_state = _gap_run(
                int(previous["frame"]) + 1,
                int(following["frame"]) - 1,
                previous=previous,
                following=following,
                edge=edge,
                occlusions_by_tracklet=occlusions_by_tracklet,
                local_occlusion_context=local_occlusion_context,
                shadow_subject_id=str(subject.get("shadow_subject_id") or ""),
                team_label=str(subject.get("team_label") or "U"),
                fps=fps,
                parameters=parameters,
            )
        else:
            transition_state = {
                "status": "direct_transition" if frame_delta == 1 else "overlap_transition",
                "start_frame": int(previous["frame"]),
                "end_frame": int(following["frame"]),
                "identity_continuity_status": "supported",
                "identity_risk_reasons": [],
            }
        event = _transition_event(
            subject,
            edge=edge,
            gap_run=transition_state,
            previous=previous,
            following=following,
        )
        event["frame_delta"] = frame_delta
        event["overlap_frames"] = max(0, 1 - frame_delta)
        transitions.append(event)
    return transitions


def _append_run(runs: list[dict[str, Any]], row: dict[str, Any]) -> None:
    if not runs or not _runs_compatible(runs[-1], row):
        runs.append(row)
        return
    runs[-1]["end_frame"] = row["end_frame"]


def _runs_compatible(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if int(left.get("end_frame") or 0) + 1 != int(right.get("start_frame") or 0):
        return False
    comparable = (
        "status",
        "tracklet_id",
        "footpoint_reliable",
        "appearance_reliable",
        "eligible_for_distance",
        "eligible_for_heatmap",
        "reason",
        "edge_key",
        "position_source",
        "identity_continuity_status",
    )
    return all(left.get(key) == right.get(key) for key in comparable)


def _select_observation(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return min(
        rows,
        key=lambda row: (
            not bool(row.get("footpoint_reliable")),
            -float(row.get("confidence") or 0.0),
            str(row.get("tracklet_id") or ""),
        ),
    )


def _positions(tracklet: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        tracklet.get("positions") or tracklet.get("positions_m") or [],
        key=lambda row: (int(row.get("frame") or 0), float(row.get("time_sec") or 0.0)),
    )


def _frame_in_ranges(frame: int, ranges: list[dict[str, Any]]) -> bool:
    return any(
        int(row.get("start_frame") or 0) <= frame <= int(row.get("end_frame") or 0)
        for row in ranges
    )


def _occlusions_by_tracklet(occlusion_doc: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for event in occlusion_doc.get("events") or []:
        for tracklet_id in event.get("tracklet_ids") or []:
            result.setdefault(str(tracklet_id), []).append(event)
    for events in result.values():
        events.sort(
            key=lambda row: (
                int(row.get("start_frame") or 0),
                str(row.get("event_id") or ""),
            )
        )
    return result


def _occlusion_support(
    start_frame: int,
    end_frame: int,
    *,
    previous: dict[str, Any],
    following: dict[str, Any],
    edge: dict[str, Any] | None,
    occlusions_by_tracklet: dict[str, list[dict[str, Any]]],
    nearby_context_frames: int,
    low_appearance_context_frames: int,
    low_appearance_ratio: float,
) -> dict[str, Any]:
    edge_ids = set(str(value) for value in (edge or {}).get("occlusion_event_ids") or [])
    tracklet_ids = {
        str(previous.get("tracklet_id") or ""),
        str(following.get("tracklet_id") or ""),
    }
    events_by_id: dict[str, dict[str, Any]] = {}
    appearance_ratios = [
        float(value)
        for value in (
            previous.get("appearance_reliable_ratio"),
            following.get("appearance_reliable_ratio"),
        )
        if value is not None
    ]
    weak_appearance = bool(appearance_ratios) and min(appearance_ratios) <= low_appearance_ratio
    same_raw_tracklet = previous.get("tracklet_id") == following.get("tracklet_id")
    endpoint_confidence = min(
        float(previous.get("confidence") or 0.0),
        float(following.get("confidence") or 0.0),
    )
    for tracklet_id in tracklet_ids:
        for event in occlusions_by_tracklet.get(tracklet_id) or []:
            event_start = int(event.get("start_frame") or 0)
            event_end = int(event.get("end_frame") or event_start)
            distance = max(start_frame - event_end, event_start - end_frame, 0)
            event_id = str(event.get("event_id") or "")
            cross_team = len(set(str(value) for value in event.get("team_labels") or [])) > 1
            qualifies = bool(
                event_id in edge_ids
                or (
                    same_raw_tracklet
                    and (
                        (cross_team and distance <= nearby_context_frames)
                        or (weak_appearance and distance <= low_appearance_context_frames)
                    )
                )
                or (
                    not same_raw_tracklet
                    and cross_team
                    and distance <= nearby_context_frames
                    and endpoint_confidence < 0.35
                )
            )
            if qualifies:
                events_by_id[event_id] = event
    evidence = sorted(
        {
            str(value)
            for event in events_by_id.values()
            for value in event.get("evidence") or []
        }
    )
    cross_team = any(
        len(set(str(value) for value in event.get("team_labels") or [])) > 1
        for event in events_by_id.values()
    )
    return {
        "event_ids": sorted(set(events_by_id) | edge_ids),
        "evidence": evidence,
        "cross_team": cross_team,
    }


def _identity_continuity_assessment(
    previous: dict[str, Any],
    following: dict[str, Any],
    *,
    occlusion: dict[str, Any],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    team_confidences = [
        float(value)
        for value in (previous.get("team_confidence"), following.get("team_confidence"))
        if value is not None
    ]
    risky = bool(
        previous.get("tracklet_id") == following.get("tracklet_id")
        and (not previous.get("appearance_reliable") or not following.get("appearance_reliable"))
        and occlusion.get("cross_team")
        and team_confidences
        and min(team_confidences) < float(parameters["identity_risk_max_team_confidence"])
    )
    reasons = (
        [
            "same_raw_tracklet_crosses_cross_team_occlusion",
            "unreliable_endpoint_appearance",
            "low_tracklet_team_confidence",
        ]
        if risky
        else []
    )
    return {
        "identity_continuity_status": "uncertain" if risky else "supported",
        "identity_risk_reasons": reasons,
    }


def _point(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    return [round(float(value[0]), 3), round(float(value[1]), 3)]


def _distance(left: Any, right: Any) -> float | None:
    left_point = _point(left)
    right_point = _point(right)
    if left_point is None or right_point is None:
        return None
    return math.hypot(right_point[0] - left_point[0], right_point[1] - left_point[1])


def _event_key(edge_key: str, subject_id: str) -> str:
    payload = json.dumps(
        {"edge_key": edge_key, "shadow_subject_id": subject_id, "version": 1},
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"shadow-transition:v1:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"
