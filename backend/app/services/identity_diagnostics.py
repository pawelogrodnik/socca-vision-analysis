from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import math
from typing import Any


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "shadow_identity_diagnostics"
ALGORITHM_VERSION = "1.0.0"

DEFAULT_PARAMETERS: dict[str, Any] = {
    "overlap_min_iou": 0.20,
    "overlap_min_containment": 0.55,
    "close_footpoint_distance_m": 1.0,
    "occlusion_frame_gap": 2,
    "occlusion_min_frames": 2,
    "transition_context_frames": 15,
    "recovery_max_gap_sec": 3.0,
    "recovery_max_distance_m": 12.0,
    "recovery_max_speed_mps": 9.5,
    "trusted_min_duration_sec": 2.0,
    "trusted_min_positions": 15,
    "trusted_min_confidence": 0.45,
    "trusted_min_team_confidence": 0.65,
    "trusted_min_footpoint_ratio": 0.70,
    "trusted_min_appearance_ratio": 0.55,
    "recoverable_min_duration_sec": 0.4,
    "recoverable_min_positions": 6,
    "recoverable_min_confidence": 0.25,
    "noise_max_duration_sec": 0.2,
    "noise_max_positions": 4,
    "noise_max_confidence": 0.12,
    "duplicate_min_ratio": 0.40,
    "footpoint_min_confidence": 0.35,
    "footpoint_max_speed_mps": 16.0,
    "appearance_min_quality": 0.22,
    "appearance_min_samples": 2,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_identity_diagnostics(
    tracklets: list[dict[str, Any]],
    rejected_tracklets: list[dict[str, Any]],
    global_identity: dict[str, Any],
    *,
    fps: float,
    manual_assignments_doc: dict[str, Any] | None = None,
    generated_at: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build read-only identity diagnostics without mutating resolver inputs."""
    params = {**DEFAULT_PARAMETERS, **(parameters or {})}
    timestamp = generated_at or now_iso()
    clean = sorted(tracklets, key=_tracklet_sort_key)
    rejected = sorted(rejected_tracklets, key=_tracklet_sort_key)

    occlusion_events = _build_occlusion_events(clean, fps=fps, parameters=params)
    quality = _build_tracklet_quality(
        clean,
        rejected,
        global_identity,
        occlusion_events,
        fps=fps,
        parameters=params,
    )
    fragmentation = _build_fragmentation_report(
        clean,
        rejected,
        global_identity,
        quality,
        occlusion_events,
        fps=fps,
        manual_assignments_doc=manual_assignments_doc,
        parameters=params,
    )
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": timestamp,
        "mode": "shadow_read_only",
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION},
        "parameters": params,
    }
    return {
        "identity_tracklet_quality": {**metadata, **quality},
        "identity_occlusion_events": {**metadata, **occlusion_events},
        "identity_fragmentation_report": {**metadata, **fragmentation},
    }


def _build_occlusion_events(
    tracklets: list[dict[str, Any]],
    *,
    fps: float,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    by_frame: dict[int, list[dict[str, Any]]] = defaultdict(list)
    tracklet_by_id = {str(row.get("tracklet_id")): row for row in tracklets}
    for tracklet in tracklets:
        tracklet_id = str(tracklet.get("tracklet_id"))
        for position in tracklet.get("positions") or []:
            by_frame[int(position.get("frame") or 0)].append(
                {
                    "tracklet_id": tracklet_id,
                    "team_label": _team_label(tracklet),
                    "bbox_xyxy": position.get("bbox_xyxy"),
                    "pitch_m": position.get("pitch_m"),
                    "confidence": float(position.get("confidence") or 0.0),
                }
            )

    pair_rows: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for frame in sorted(by_frame):
        rows = sorted(by_frame[frame], key=lambda item: item["tracklet_id"])
        for index, left in enumerate(rows):
            for right in rows[index + 1 :]:
                overlap = _bbox_overlap_metrics(left.get("bbox_xyxy"), right.get("bbox_xyxy"))
                distance = _distance(left.get("pitch_m"), right.get("pitch_m"))
                is_close = distance is not None and distance <= float(parameters["close_footpoint_distance_m"])
                if not (
                    overlap["iou"] >= float(parameters["overlap_min_iou"])
                    or overlap["containment"] >= float(parameters["overlap_min_containment"])
                    or (is_close and overlap["iou"] > 0.04)
                ):
                    continue
                pair = tuple(sorted((left["tracklet_id"], right["tracklet_id"])))
                pair_rows[pair].append(
                    {
                        "frame": frame,
                        "bbox_iou": overlap["iou"],
                        "bbox_containment": overlap["containment"],
                        "footpoint_distance_m": distance,
                    }
                )

    events: list[dict[str, Any]] = []
    event_index = 1
    for pair in sorted(pair_rows):
        for run in _split_contiguous_rows(pair_rows[pair], int(parameters["occlusion_frame_gap"])):
            max_iou = max(float(row["bbox_iou"]) for row in run)
            max_containment = max(float(row["bbox_containment"]) for row in run)
            if len(run) < int(parameters["occlusion_min_frames"]) and max_containment < 0.8:
                continue
            start_frame = int(run[0]["frame"])
            end_frame = int(run[-1]["frame"])
            incoming, outgoing = _transition_candidates(
                pair,
                start_frame,
                end_frame,
                tracklet_by_id,
                fps=fps,
                parameters=parameters,
            )
            confidence = min(
                1.0,
                0.35
                + min(0.25, len(run) / max(1.0, fps) * 0.5)
                + max_iou * 0.2
                + max_containment * 0.2,
            )
            teams = sorted({_team_label(tracklet_by_id[item]) for item in pair})
            events.append(
                {
                    "event_id": f"occlusion-{event_index:06d}",
                    "start_frame": start_frame,
                    "end_frame": end_frame,
                    "start_time_sec": round(start_frame / max(fps, 1e-6), 3),
                    "end_time_sec": round(end_frame / max(fps, 1e-6), 3),
                    "duration_frames": end_frame - start_frame + 1,
                    "tracklet_ids": list(pair),
                    "team_labels": teams,
                    "max_bbox_iou": round(max_iou, 4),
                    "max_bbox_containment": round(max_containment, 4),
                    "min_footpoint_distance_m": _round_optional(
                        min(
                            (float(row["footpoint_distance_m"]) for row in run if row["footpoint_distance_m"] is not None),
                            default=None,
                        ),
                        3,
                    ),
                    "incoming_tracklet_ids": incoming,
                    "outgoing_tracklet_ids": outgoing,
                    "confidence": round(confidence, 4),
                    "evidence": ["bbox_overlap", "footpoint_proximity"] if any(
                        row["footpoint_distance_m"] is not None for row in run
                    ) else ["bbox_overlap"],
                }
            )
            event_index += 1

    return {
        "source": "tracklets_before_conservative_identity_v2",
        "summary": {
            "events": len(events),
            "tracklets_in_events": len({item for event in events for item in event["tracklet_ids"]}),
            "same_team_events": sum(1 for event in events if len(event["team_labels"]) == 1),
            "cross_team_events": sum(1 for event in events if len(event["team_labels"]) > 1),
        },
        "events": events,
    }


def _build_tracklet_quality(
    tracklets: list[dict[str, Any]],
    rejected_tracklets: list[dict[str, Any]],
    global_identity: dict[str, Any],
    occlusion_doc: dict[str, Any],
    *,
    fps: float,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    occluded_frames: dict[str, set[int]] = defaultdict(set)
    event_ids: dict[str, list[str]] = defaultdict(list)
    for event in occlusion_doc.get("events") or []:
        frames = range(int(event["start_frame"]), int(event["end_frame"]) + 1)
        for tracklet_id in event.get("tracklet_ids") or []:
            occluded_frames[str(tracklet_id)].update(frames)
            event_ids[str(tracklet_id)].append(str(event["event_id"]))

    duplicate_frames: dict[str, set[int]] = defaultdict(set)
    for row in global_identity.get("suppressed_duplicate_observations") or []:
        tracklet_id = row.get("tracklet_id")
        if tracklet_id:
            duplicate_frames[str(tracklet_id)].add(int(row.get("frame") or 0))

    recovery_candidates = _recovery_candidates(tracklets, parameters=parameters)
    rows: list[dict[str, Any]] = []
    inputs = [(tracklet, False) for tracklet in tracklets] + [
        (tracklet, True) for tracklet in rejected_tracklets
    ]
    for tracklet, is_rejected in inputs:
        tracklet_id = str(tracklet.get("tracklet_id"))
        positions = sorted(tracklet.get("positions") or [], key=lambda item: int(item.get("frame") or 0))
        frames = [int(item.get("frame") or 0) for item in positions]
        frame_count = max(1, len(frames))
        unreliable_footpoint: list[int] = []
        unreliable_appearance: list[int] = []
        inside_frames: list[int] = []
        previous: dict[str, Any] | None = None
        for position in positions:
            frame = int(position.get("frame") or 0)
            if str(position.get("play_area_status") or "inside_play") == "inside_play":
                inside_frames.append(frame)
            if not _footpoint_reliable(
                position,
                previous,
                occluded=frame in occluded_frames.get(tracklet_id, set()),
                fps=fps,
                parameters=parameters,
            ):
                unreliable_footpoint.append(frame)
            if not _appearance_reliable(
                tracklet,
                occluded=frame in occluded_frames.get(tracklet_id, set()),
                parameters=parameters,
            ):
                unreliable_appearance.append(frame)
            previous = position

        duplicate_ratio = len(duplicate_frames.get(tracklet_id, set())) / frame_count
        footpoint_ratio = 1.0 - len(set(unreliable_footpoint)) / frame_count
        appearance_ratio = 1.0 - len(set(unreliable_appearance)) / frame_count
        inside_ratio = len(set(inside_frames)) / frame_count
        occlusion_ratio = len(set(frames) & occluded_frames.get(tracklet_id, set())) / frame_count
        status, reasons = _quality_classification(
            tracklet,
            rejected=is_rejected,
            duplicate_ratio=duplicate_ratio,
            footpoint_ratio=footpoint_ratio,
            appearance_ratio=appearance_ratio,
            inside_ratio=inside_ratio,
            occlusion_ratio=occlusion_ratio,
            recovery_candidates=recovery_candidates.get(tracklet_id) or [],
            parameters=parameters,
        )
        confidence = _quality_confidence(
            tracklet,
            status=status,
            footpoint_ratio=footpoint_ratio,
            appearance_ratio=appearance_ratio,
            inside_ratio=inside_ratio,
            duplicate_ratio=duplicate_ratio,
        )
        rows.append(
            {
                "tracklet_id": tracklet_id,
                "diagnostic_tracklet_key": f"{'rejected' if is_rejected else 'clean'}:{tracklet_id}",
                "source_tracker_id": tracklet.get("source_track_id"),
                "status": "rejected" if is_rejected else "clean",
                "quality_class": status,
                "quality_confidence": round(confidence, 4),
                "reasons": reasons,
                "team_label": _team_label(tracklet),
                "team_confidence": round(float(tracklet.get("team_confidence") or 0.0), 4),
                "duration_sec": round(float(tracklet.get("duration_sec") or 0.0), 3),
                "positions_count": len(positions),
                "mean_confidence": round(float(tracklet.get("mean_confidence") or 0.0), 4),
                "occlusion_ratio": round(occlusion_ratio, 4),
                "footpoint_reliable_ratio": round(footpoint_ratio, 4),
                "appearance_reliable_ratio": round(appearance_ratio, 4),
                "inside_pitch_ratio": round(inside_ratio, 4),
                "duplicate_ratio": round(duplicate_ratio, 4),
                "occlusion_event_ids": sorted(event_ids.get(tracklet_id) or []),
                "recovery_candidates": recovery_candidates.get(tracklet_id) or [],
                "unreliable_footpoint_ranges": _frame_ranges(unreliable_footpoint, fps=fps),
                "unreliable_appearance_ranges": _frame_ranges(unreliable_appearance, fps=fps),
                "position_source": "existing_pitch_position_shadow_assessment",
            }
        )

    rows.sort(key=lambda item: (item["tracklet_id"], item["status"]))
    counts = Counter(str(row["quality_class"]) for row in rows)
    return {
        "source": "tracklets_and_global_identity_read_only",
        "summary": {
            "tracklets": len(rows),
            "clean_tracklets": sum(1 for row in rows if row["status"] == "clean"),
            "rejected_tracklets": sum(1 for row in rows if row["status"] == "rejected"),
            "quality_counts": {key: counts.get(key, 0) for key in ("trusted", "recoverable", "ambiguous", "duplicate", "noise")},
        },
        "tracklets": rows,
    }


def _build_fragmentation_report(
    tracklets: list[dict[str, Any]],
    rejected_tracklets: list[dict[str, Any]],
    global_identity: dict[str, Any],
    quality_doc: dict[str, Any],
    occlusion_doc: dict[str, Any],
    *,
    fps: float,
    manual_assignments_doc: dict[str, Any] | None,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    quality_by_id = {
        str(row["tracklet_id"]): row
        for row in quality_doc.get("tracklets") or []
        if row.get("status") == "clean"
    }
    occlusion_events = occlusion_doc.get("events") or []
    occlusion_by_frame: dict[int, list[str]] = defaultdict(list)
    for event in occlusion_events:
        for frame in range(int(event["start_frame"]), int(event["end_frame"]) + 1):
            occlusion_by_frame[frame].append(str(event["event_id"]))

    subject_rows: list[dict[str, Any]] = []
    suspected_switches: list[dict[str, Any]] = []
    transition_context = int(parameters["transition_context_frames"])
    for slot in sorted(global_identity.get("slots") or [], key=lambda item: str(item.get("stable_subject_id") or item.get("slot_id"))):
        subject_id = str(slot.get("stable_subject_id") or slot.get("slot_id"))
        tracklet_ids = [str(item) for item in slot.get("tracklet_ids") or []]
        subject_rows.append(
            {
                "stable_subject_id": subject_id,
                "stable_player_id": slot.get("stable_player_id") or slot.get("slot_id"),
                "team_label": slot.get("team_label"),
                "raw_tracklets": len(tracklet_ids),
                "tracklet_ids": tracklet_ids,
                "quality_counts": dict(Counter((quality_by_id.get(item) or {}).get("quality_class", "missing") for item in tracklet_ids)),
                "unresolved_frames": int(slot.get("ambiguous_frames") or 0) + int(slot.get("missing_frames") or 0),
            }
        )
        transitions = _slot_tracklet_transitions(slot)
        event_frames = _slot_risky_event_frames(slot)
        for transition in transitions:
            frame = int(transition["frame"])
            nearby_occlusions = sorted(
                {
                    event_id
                    for candidate_frame in range(max(0, frame - transition_context), frame + transition_context + 1)
                    for event_id in occlusion_by_frame.get(candidate_frame, [])
                }
            )
            nearby_conflict = any(abs(frame - event_frame) <= transition_context for event_frame in event_frames)
            if not nearby_occlusions and not nearby_conflict:
                continue
            suspected_switches.append(
                {
                    "switch_id": f"switch-{len(suspected_switches) + 1:06d}",
                    "stable_subject_id": subject_id,
                    "stable_player_id": slot.get("stable_player_id") or slot.get("slot_id"),
                    **transition,
                    "after_overlap": bool(nearby_occlusions),
                    "occlusion_event_ids": nearby_occlusions,
                    "conflict_evidence": bool(nearby_conflict),
                    "evidence": (["nearby_occlusion"] if nearby_occlusions else []) + (["resolver_conflict"] if nearby_conflict else []),
                }
            )

    durations = [float(row.get("duration_sec") or 0.0) for row in tracklets]
    quality_counts = Counter(str(row.get("quality_class")) for row in quality_doc.get("tracklets") or [])
    unmatched = global_identity.get("unmatched_observations") or []
    unmatched_frames = {int(row.get("frame") or 0) for row in unmatched}
    frame_rows = global_identity.get("frames") or []
    ambiguous_timeline_frames = {
        int(row.get("frame") or 0)
        for row in frame_rows
        if int(row.get("slot_ambiguous") or 0) > 0
    }
    ambiguous_observations = int((global_identity.get("summary") or {}).get("ambiguous_frames") or 0)
    duplicate_rows = global_identity.get("suppressed_duplicate_observations") or []
    duplicate_total = int(
        (global_identity.get("summary") or {}).get("duplicate_observations_suppressed")
        or len(duplicate_rows)
    )
    manual_assignments = (manual_assignments_doc or {}).get("assignments") or []
    review_duration = (manual_assignments_doc or {}).get("review_duration_sec")
    if review_duration is None:
        review_duration = ((manual_assignments_doc or {}).get("summary") or {}).get("review_duration_sec")

    return {
        "source": "conservative_identity_v2_outputs_read_only",
        "summary": {
            "stable_subjects": len(subject_rows),
            "stints": sum(int(slot.get("stint_count") or len(slot.get("stints") or [])) for slot in global_identity.get("slots") or []),
            "clean_tracklets": len(tracklets),
            "rejected_tracklets": len(rejected_tracklets),
            "tracklet_duration_sec_p10": _round_optional(_percentile(durations, 0.10), 3),
            "tracklet_duration_sec_p50": _round_optional(_percentile(durations, 0.50), 3),
            "tracklet_duration_sec_p90": _round_optional(_percentile(durations, 0.90), 3),
            "short_tracklets": sum(1 for value in durations if value < 0.4),
            "noise_tracklets": quality_counts.get("noise", 0),
            "recoverable_tracklets": quality_counts.get("recoverable", 0),
            "ambiguous_tracklets": quality_counts.get("ambiguous", 0),
            "orphan_tracklets": sum(
                1
                for row in quality_doc.get("tracklets") or []
                if row.get("status") == "clean"
                and row.get("quality_class") in {"recoverable", "ambiguous"}
                and not row.get("recovery_candidates")
            ),
            "suspected_switch_events": len(suspected_switches),
            "switches_after_overlap": sum(1 for row in suspected_switches if row.get("after_overlap")),
            "duplicate_identity_conflicts": duplicate_total,
            "duplicate_evidence_rows_available": len(duplicate_rows),
            "duplicate_evidence_truncated": duplicate_total > len(duplicate_rows),
            "unresolved_observations": len(unmatched),
            "unresolved_observation_seconds": round(len(unmatched) / max(fps, 1e-6), 3),
            "frames_with_unresolved": len(unmatched_frames),
            "unresolved_timeline_seconds": round(len(unmatched_frames) / max(fps, 1e-6), 3),
            "ambiguous_observations": ambiguous_observations,
            "ambiguous_observation_seconds": round(ambiguous_observations / max(fps, 1e-6), 3),
            "frames_with_ambiguous": len(ambiguous_timeline_frames),
            "ambiguous_timeline_seconds": round(len(ambiguous_timeline_frames) / max(fps, 1e-6), 3),
            "manual_assignments": len(manual_assignments),
            "manual_review_duration_sec": review_duration,
            "estimated_manual_review_items": quality_counts.get("recoverable", 0) + quality_counts.get("ambiguous", 0) + len(suspected_switches),
        },
        "subjects": subject_rows,
        "suspected_switches": suspected_switches,
    }


def _quality_classification(
    tracklet: dict[str, Any],
    *,
    rejected: bool,
    duplicate_ratio: float,
    footpoint_ratio: float,
    appearance_ratio: float,
    inside_ratio: float,
    occlusion_ratio: float,
    recovery_candidates: list[dict[str, Any]],
    parameters: dict[str, Any],
) -> tuple[str, list[str]]:
    duration = float(tracklet.get("duration_sec") or 0.0)
    positions = int(tracklet.get("positions_count") or len(tracklet.get("positions") or []))
    confidence = float(tracklet.get("mean_confidence") or 0.0)
    team_confidence = float(tracklet.get("team_confidence") or 0.0)
    team = _team_label(tracklet)

    if duplicate_ratio >= float(parameters["duplicate_min_ratio"]):
        return "duplicate", ["suppressed_as_duplicate_for_large_tracklet_fraction"]
    if rejected or (
        duration <= float(parameters["noise_max_duration_sec"])
        or positions < int(parameters["noise_max_positions"])
        or confidence < float(parameters["noise_max_confidence"])
    ):
        return "noise", ["splitter_rejected" if rejected else "too_short_or_low_confidence"]
    if team not in {"A", "B"} and team_confidence < 0.35:
        return "ambiguous", ["uncertain_team"]
    trusted_failures: list[str] = []
    if duration < float(parameters["trusted_min_duration_sec"]):
        trusted_failures.append("short_duration")
    if positions < int(parameters["trusted_min_positions"]):
        trusted_failures.append("few_positions")
    if confidence < float(parameters["trusted_min_confidence"]):
        trusted_failures.append("low_detection_confidence")
    if team not in {"A", "B"} or team_confidence < float(parameters["trusted_min_team_confidence"]):
        trusted_failures.append("uncertain_team")
    if footpoint_ratio < float(parameters["trusted_min_footpoint_ratio"]):
        trusted_failures.append("unreliable_footpoint")
    if appearance_ratio < float(parameters["trusted_min_appearance_ratio"]):
        trusted_failures.append("unreliable_appearance")
    if inside_ratio < 0.7:
        trusted_failures.append("mostly_outside_pitch")
    if occlusion_ratio >= 0.35:
        trusted_failures.append("heavy_occlusion")
    if not trusted_failures:
        return "trusted", ["stable_duration_team_appearance_and_position"]
    if (
        recovery_candidates
        and duration >= float(parameters["recoverable_min_duration_sec"])
        and positions >= int(parameters["recoverable_min_positions"])
        and confidence >= float(parameters["recoverable_min_confidence"])
        and inside_ratio >= 0.5
    ):
        return "recoverable", ["plausible_continuation_candidate", *trusted_failures]
    return "ambiguous", trusted_failures or ["insufficient_quality_evidence"]


def _quality_confidence(
    tracklet: dict[str, Any],
    *,
    status: str,
    footpoint_ratio: float,
    appearance_ratio: float,
    inside_ratio: float,
    duplicate_ratio: float,
) -> float:
    detection = min(1.0, max(0.0, float(tracklet.get("mean_confidence") or 0.0)))
    team = min(1.0, max(0.0, float(tracklet.get("team_confidence") or 0.0)))
    base = 0.30 * detection + 0.20 * team + 0.20 * footpoint_ratio + 0.15 * appearance_ratio + 0.15 * inside_ratio
    if status == "duplicate":
        return max(base, duplicate_ratio)
    if status == "noise":
        return max(0.5, 1.0 - base)
    return base


def _recovery_candidates(
    tracklets: list[dict[str, Any]],
    *,
    parameters: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rows = sorted(tracklets, key=_tracklet_sort_key)
    for index, source in enumerate(rows):
        source_end = float(source.get("end_time_sec") or 0.0)
        source_point = source.get("last_pitch_m")
        source_team = _team_label(source)
        for target in rows[index + 1 :]:
            gap = float(target.get("start_time_sec") or 0.0) - source_end
            if gap < 0:
                continue
            if gap > float(parameters["recovery_max_gap_sec"]):
                break
            target_team = _team_label(target)
            if source_team in {"A", "B"} and target_team in {"A", "B"} and source_team != target_team:
                continue
            distance = _distance(source_point, target.get("first_pitch_m"))
            if distance is None or distance > float(parameters["recovery_max_distance_m"]):
                continue
            speed = distance / max(gap, 1.0 / 30.0)
            if speed > float(parameters["recovery_max_speed_mps"]):
                continue
            row = {
                "tracklet_id": str(target.get("tracklet_id")),
                "gap_sec": round(gap, 3),
                "distance_m": round(distance, 3),
                "speed_mps": round(speed, 3),
            }
            candidates[str(source.get("tracklet_id"))].append(row)
            reverse = {
                "tracklet_id": str(source.get("tracklet_id")),
                "gap_sec": round(gap, 3),
                "distance_m": round(distance, 3),
                "speed_mps": round(speed, 3),
            }
            candidates[str(target.get("tracklet_id"))].append(reverse)
    return {key: sorted(value, key=lambda item: (item["gap_sec"], item["distance_m"], item["tracklet_id"]))[:5] for key, value in candidates.items()}


def _footpoint_reliable(
    position: dict[str, Any],
    previous: dict[str, Any] | None,
    *,
    occluded: bool,
    fps: float,
    parameters: dict[str, Any],
) -> bool:
    if occluded or float(position.get("confidence") or 0.0) < float(parameters["footpoint_min_confidence"]):
        return False
    if not position.get("pitch_m") or not position.get("bbox_xyxy"):
        return False
    if str(position.get("play_area_status") or "inside_play") == "outside_play":
        return False
    if bool(position.get("pitch_m_clamped")):
        return False
    if previous and previous.get("pitch_m"):
        frame_gap = int(position.get("frame") or 0) - int(previous.get("frame") or 0)
        distance = _distance(position.get("pitch_m"), previous.get("pitch_m"))
        if frame_gap > 0 and distance is not None:
            speed = distance / (frame_gap / max(fps, 1e-6))
            if speed > float(parameters["footpoint_max_speed_mps"]):
                return False
    return True


def _appearance_reliable(tracklet: dict[str, Any], *, occluded: bool, parameters: dict[str, Any]) -> bool:
    return bool(
        not occluded
        and float(tracklet.get("appearance_quality") or 0.0) >= float(parameters["appearance_min_quality"])
        and int(tracklet.get("appearance_samples") or 0) >= int(parameters["appearance_min_samples"])
        and tracklet.get("appearance_feature")
    )


def _slot_tracklet_transitions(slot: dict[str, Any]) -> list[dict[str, Any]]:
    rows = sorted(
        (
            row
            for row in slot.get("overlay_positions") or []
            if row.get("source") == "detected" and row.get("tracklet_id")
        ),
        key=lambda item: int(item.get("frame") or 0),
    )
    transitions: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    for row in rows:
        if previous and str(row.get("tracklet_id")) != str(previous.get("tracklet_id")):
            transitions.append(
                {
                    "frame": int(row.get("frame") or 0),
                    "time_sec": round(float(row.get("time_sec") or 0.0), 3),
                    "from_tracklet_id": str(previous.get("tracklet_id")),
                    "to_tracklet_id": str(row.get("tracklet_id")),
                    "frame_gap": int(row.get("frame") or 0) - int(previous.get("frame") or 0),
                    "pitch_distance_m": _round_optional(_distance(previous.get("pitch_m"), row.get("pitch_m")), 3),
                }
            )
        previous = row
    return transitions


def _slot_risky_event_frames(slot: dict[str, Any]) -> list[int]:
    risky_types = {"ambiguous_candidate", "identity_switch_blocked", "team_switch_blocked", "candidate_rejected"}
    return sorted(
        int(row.get("frame") or 0)
        for row in slot.get("identity_events") or []
        if str(row.get("type")) in risky_types or "ambiguous" in str(row.get("type") or "")
    )


def _transition_candidates(
    pair: tuple[str, str],
    start_frame: int,
    end_frame: int,
    tracklet_by_id: dict[str, dict[str, Any]],
    *,
    fps: float,
    parameters: dict[str, Any],
) -> tuple[list[str], list[str]]:
    context = int(parameters["transition_context_frames"])
    incoming: list[str] = []
    outgoing: list[str] = []
    for tracklet_id in pair:
        row = tracklet_by_id[tracklet_id]
        start = _tracklet_start_frame(row, fps=fps)
        end = _tracklet_end_frame(row, fps=fps)
        if abs(start - end_frame) <= context or abs(start - start_frame) <= context:
            incoming.append(tracklet_id)
        if abs(end - start_frame) <= context or abs(end - end_frame) <= context:
            outgoing.append(tracklet_id)
    return sorted(incoming), sorted(outgoing)


def _tracklet_start_frame(tracklet: dict[str, Any], *, fps: float) -> int:
    positions = tracklet.get("positions") or []
    if positions:
        return int(positions[0].get("frame") or 0)
    return int(round(float(tracklet.get("start_time_sec") or 0.0) * fps))


def _tracklet_end_frame(tracklet: dict[str, Any], *, fps: float) -> int:
    positions = tracklet.get("positions") or []
    if positions:
        return int(positions[-1].get("frame") or 0)
    return int(round(float(tracklet.get("end_time_sec") or 0.0) * fps))


def _split_contiguous_rows(rows: list[dict[str, Any]], max_gap: int) -> list[list[dict[str, Any]]]:
    result: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: int(item["frame"])):
        if current and int(row["frame"]) - int(current[-1]["frame"]) > max_gap:
            result.append(current)
            current = []
        current.append(row)
    if current:
        result.append(current)
    return result


def _frame_ranges(frames: list[int], *, fps: float) -> list[dict[str, Any]]:
    unique = sorted(set(frames))
    if not unique:
        return []
    ranges: list[dict[str, Any]] = []
    start = previous = unique[0]
    for frame in unique[1:]:
        if frame > previous + 1:
            ranges.append(_frame_range_doc(start, previous, fps=fps))
            start = frame
        previous = frame
    ranges.append(_frame_range_doc(start, previous, fps=fps))
    return ranges


def _frame_range_doc(start: int, end: int, *, fps: float) -> dict[str, Any]:
    return {
        "start_frame": start,
        "end_frame": end,
        "start_time_sec": round(start / max(fps, 1e-6), 3),
        "end_time_sec": round(end / max(fps, 1e-6), 3),
        "frames": end - start + 1,
    }


def _bbox_overlap_metrics(a: Any, b: Any) -> dict[str, float]:
    if not isinstance(a, (list, tuple)) or not isinstance(b, (list, tuple)) or len(a) != 4 or len(b) != 4:
        return {"iou": 0.0, "containment": 0.0}
    ax1, ay1, ax2, ay2 = [float(value) for value in a]
    bx1, by1, bx2, by2 = [float(value) for value in b]
    width = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    height = max(0.0, min(ay2, by2) - max(ay1, by1))
    intersection = width * height
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - intersection
    smaller = min(area_a, area_b)
    return {
        "iou": intersection / union if union > 0 else 0.0,
        "containment": intersection / smaller if smaller > 0 else 0.0,
    }


def _distance(a: Any, b: Any) -> float | None:
    if not isinstance(a, (list, tuple)) or not isinstance(b, (list, tuple)) or len(a) < 2 or len(b) < 2:
        return None
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _tracklet_sort_key(tracklet: dict[str, Any]) -> tuple[float, float, str]:
    return (
        float(tracklet.get("start_time_sec") or 0.0),
        float(tracklet.get("end_time_sec") or 0.0),
        str(tracklet.get("tracklet_id") or ""),
    )


def _team_label(tracklet: dict[str, Any]) -> str:
    value = str(tracklet.get("team_label") or tracklet.get("team_candidate") or "U").upper()
    return value if value in {"A", "B"} else "U"


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile
    low = int(math.floor(index))
    high = int(math.ceil(index))
    if low == high:
        return ordered[low]
    ratio = index - low
    return ordered[low] * (1.0 - ratio) + ordered[high] * ratio


def _round_optional(value: float | None, digits: int) -> float | None:
    return round(float(value), digits) if value is not None else None
