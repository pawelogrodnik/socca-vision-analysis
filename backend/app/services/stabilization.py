from __future__ import annotations

from collections import defaultdict, deque
import json
import math
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Callable

from app.services.analysis_quality import build_analysis_quality_report
from app.services.ball_tracking import _draw_ball_position, build_ball_tracks_document, refine_ball_tracks_against_players
from app.services.change_candidates import write_change_candidate_artifacts
from app.services.conservative_identity import (
    build_frame_detection_counts_from_global_identity,
    build_global_identity_report,
    build_stable_players_from_global_identity,
    resolve_conservative_identity,
)
from app.services.identity_diagnostics import build_identity_diagnostics
from app.services.identity_offline_resolver_shadow import build_shadow_offline_identity
from app.services.identity_occlusion_assignment_shadow import build_shadow_occlusion_assignments
from app.services.identity_stitching_shadow import build_shadow_stitching_candidates


MAX_INTERPOLATION_GAP_FRAMES = 15
MAX_INTERPOLATION_GAP_SEC = 0.6
MAX_INTERPOLATION_SPEED_MPS = 9.5
DEFAULT_ACTIVE_PLAYERS_CAP = 14
DEFAULT_ACTIVE_PLAYERS_PER_TEAM_CAP = 7
TEAM_COLOR_MIN_REFERENCE_SAMPLES = 2
TEAM_COLOR_MIN_REFERENCE_QUALITY = 0.22
TEAM_COLOR_UNKNOWN_CONFIDENCE = 0.42
TEAM_COLOR_MAX_ASSIGNMENT_DISTANCE = 95.0
LIVE_STATS_MAX_SPEED_MPS = 8.5
LIVE_STATS_SUSTAINED_SPEED_MPS = 8.0
LIVE_STATS_ESTIMATED_GAP_SEC = 2.0
LIVE_STATS_OBSERVED_GAP_FRAMES = 2
LIVE_STATS_SPEED_MIN_WINDOW_SEC = 0.5
LIVE_STATS_SPEED_MAX_WINDOW_SEC = 1.25
STABLE_OVERLAY_VISUAL_HOLD_MAX_GAP_FRAMES = 6
STABLE_OVERLAY_VISUAL_HOLD_MAX_GAP_SEC = 0.35
STABLE_OVERLAY_VISUAL_HOLD_MAX_SPEED_MPS = 8.5
POSSESSION_INDICATOR_MIN_CONFIDENCE = 0.65
PASSING_LANE_MIN_POSSESSION_CONFIDENCE = 0.68
PASSING_LANE_MAX_OPTIONS = 5
PASSING_LANE_MIN_LENGTH_PX = 32.0
PASSING_LANE_CORRIDOR_PX = 20.0


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _distance_m(a: list[float] | tuple[float, float] | None, b: list[float] | tuple[float, float] | None) -> float | None:
    if not a or not b or len(a) < 2 or len(b) < 2:
        return None
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _round_point(point: list[float] | tuple[float, float] | None, digits: int = 3) -> list[float] | None:
    if not point or len(point) < 2:
        return None
    return [round(float(point[0]), digits), round(float(point[1]), digits)]


def _round_vector(values: list[float] | tuple[float, ...] | None, digits: int = 3) -> list[float] | None:
    if not values:
        return None
    return [round(float(value), digits) for value in values]


def _color_distance(a: list[float] | tuple[float, float, float] | None, b: list[float] | tuple[float, float, float] | None) -> float | None:
    if not a or not b or len(a) < 3 or len(b) < 3:
        return None
    return math.sqrt(sum((float(a[idx]) - float(b[idx])) ** 2 for idx in range(3)))


def _hex_to_rgb(value: str | None) -> list[float] | None:
    if not value:
        return None
    value = value.strip().lstrip("#")
    if len(value) != 6:
        return None
    try:
        return [float(int(value[idx : idx + 2], 16)) for idx in (0, 2, 4)]
    except ValueError:
        return None


def _rgb_to_hex(rgb: list[float] | tuple[float, float, float] | None) -> str | None:
    if not rgb or len(rgb) < 3:
        return None
    return "#" + "".join(f"{max(0, min(255, int(round(channel)))):02x}" for channel in rgb[:3])


def _smoothed_positions(points: list[list[float]], radius: int = 2) -> list[list[float]]:
    smoothed: list[list[float]] = []
    for index in range(len(points)):
        window = points[max(0, index - radius) : min(len(points), index + radius + 1)]
        smoothed.append(
            [
                round(sum(point[0] for point in window) / len(window), 3),
                round(sum(point[1] for point in window) / len(window), 3),
            ]
        )
    return smoothed


def split_tracks_into_tracklets(
    tracks: list[dict[str, Any]],
    *,
    max_internal_gap_sec: float = 0.7,
    split_speed_mps: float = 16.0,
    min_duration_sec: float = 0.2,
    min_positions: int = 4,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tracklets: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for track in tracks:
        raw_track_id = int(track.get("track_id"))
        current: list[dict[str, Any]] = []
        segment_index = 1
        previous: dict[str, Any] | None = None

        def finish_segment(reason: str | None = None) -> None:
            nonlocal current, segment_index
            if not current:
                return
            prepared = _build_tracklet(raw_track_id, segment_index, current)
            if (
                prepared["duration_sec"] < min_duration_sec
                or prepared["positions_count"] < min_positions
                or not prepared["first_pitch_m"]
                or not prepared["last_pitch_m"]
            ):
                rejected.append({**prepared, "reject_reason": reason or "short_or_missing_position"})
            else:
                tracklets.append(prepared)
            segment_index += 1
            current = []

        for position in sorted(track.get("positions") or [], key=lambda item: (int(item.get("frame", 0)), float(item.get("time_sec", 0)))):
            pitch_m = position.get("pitch_m")
            if not pitch_m:
                continue
            should_split = False
            split_reason = None
            if previous is not None:
                dt = float(position.get("time_sec") or 0) - float(previous.get("time_sec") or 0)
                distance = _distance_m(previous.get("pitch_m"), pitch_m)
                if dt > max_internal_gap_sec:
                    should_split = True
                    split_reason = "internal_gap"
                elif dt > 0 and distance is not None and distance / max(dt, 0.25) > split_speed_mps:
                    should_split = True
                    split_reason = "unrealistic_jump"
            if should_split:
                finish_segment(split_reason)
            current.append(dict(position))
            previous = position
        finish_segment()

    return tracklets, rejected


def build_tracklets_document(
    tracklets: list[dict[str, Any]],
    rejected_tracklets: list[dict[str, Any]],
    *,
    raw_tracks_count: int,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    clean_docs = [_tracklet_public_doc(tracklet, status="clean") for tracklet in tracklets]
    rejected_docs = [
        {
            **_tracklet_public_doc(tracklet, status="rejected"),
            "reject_reason": tracklet.get("reject_reason") or "unknown",
        }
        for tracklet in rejected_tracklets
    ]
    durations = [float(item.get("duration_sec") or 0.0) for item in clean_docs]
    return {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "source": "tracks_json_splitter",
        "parameters": parameters,
        "summary": {
            "raw_tracks": raw_tracks_count,
            "clean_tracklets": len(clean_docs),
            "rejected_tracklets": len(rejected_docs),
            "tracklets_total": len(clean_docs) + len(rejected_docs),
            "duration_sec_avg": round(_mean(durations) or 0.0, 3),
            "duration_sec_median": round(float(median(durations)), 3) if durations else 0.0,
            "positions_total": sum(int(item.get("positions_count") or 0) for item in clean_docs),
            "missing_frames_total": sum(int(item.get("missing_frames_count") or 0) for item in clean_docs),
            "team_counts": _tracklet_team_counts(clean_docs),
        },
        "tracklets": clean_docs,
        "rejected_tracklets": rejected_docs,
    }


def build_tracking_quality_report(
    tracklets: list[dict[str, Any]],
    rejected_tracklets: list[dict[str, Any]],
    *,
    raw_tracks_count: int,
    parameters: dict[str, Any],
    target_players_per_team: int = DEFAULT_ACTIVE_PLAYERS_PER_TEAM_CAP,
) -> dict[str, Any]:
    clean_docs = [_tracklet_public_doc(tracklet, status="clean", include_positions=False) for tracklet in tracklets]
    rejected_docs = [
        {
            **_tracklet_public_doc(tracklet, status="rejected", include_positions=False),
            "reject_reason": tracklet.get("reject_reason") or "unknown",
        }
        for tracklet in rejected_tracklets
    ]
    durations = [float(item.get("duration_sec") or 0.0) for item in clean_docs]
    confidences = [float(item.get("mean_confidence") or 0.0) for item in clean_docs]
    frame_rows = _tracklet_frame_quality_rows(tracklets, target_players_per_team=target_players_per_team)
    suspicious_events = _tracklet_suspicious_events(clean_docs, rejected_docs)
    return {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "source": "tracklets_json",
        "parameters": parameters,
        "summary": {
            "raw_tracks": raw_tracks_count,
            "clean_tracklets": len(clean_docs),
            "rejected_tracklets": len(rejected_docs),
            "duration_sec_min": round(min(durations), 3) if durations else 0.0,
            "duration_sec_max": round(max(durations), 3) if durations else 0.0,
            "duration_sec_avg": round(_mean(durations) or 0.0, 3),
            "duration_sec_median": round(float(median(durations)), 3) if durations else 0.0,
            "mean_confidence_avg": round(_mean(confidences) or 0.0, 4),
            "missing_frames_total": sum(int(item.get("missing_frames_count") or 0) for item in clean_docs),
            "team_counts": _tracklet_team_counts(clean_docs),
            "frames_with_team_over_cap": sum(1 for row in frame_rows if row.get("team_over_cap")),
            "suspicious_events": len(suspicious_events),
        },
        "frame_team_counts": frame_rows[:1000],
        "suspicious_events": suspicious_events[:1000],
        "rejected_tracklets": [
            {
                "tracklet_id": item.get("tracklet_id"),
                "source_tracker_id": item.get("source_tracker_id"),
                "duration_sec": item.get("duration_sec"),
                "positions_count": item.get("positions_count"),
                "reject_reason": item.get("reject_reason") or "unknown",
            }
            for item in rejected_docs[:500]
        ],
    }


def _tracklet_public_doc(tracklet: dict[str, Any], *, status: str, include_positions: bool = True) -> dict[str, Any]:
    positions = sorted(tracklet.get("positions") or [], key=lambda item: (int(item.get("frame") or 0), float(item.get("time_sec") or 0.0)))
    first = positions[0] if positions else {}
    last = positions[-1] if positions else {}
    doc = {
        "tracklet_id": tracklet.get("tracklet_id"),
        "source_tracker_id": tracklet.get("source_track_id"),
        "segment_index": tracklet.get("segment_index"),
        "status": status,
        "start_time_sec": tracklet.get("start_time_sec"),
        "end_time_sec": tracklet.get("end_time_sec"),
        "duration_sec": tracklet.get("duration_sec"),
        "frames_count": tracklet.get("positions_count") or len(positions),
        "positions_count": tracklet.get("positions_count") or len(positions),
        "mean_confidence": tracklet.get("mean_confidence"),
        "missing_frames_count": _tracklet_missing_frames_count(positions),
        "team_candidate": tracklet.get("team_label") or "unknown",
        "team_label": tracklet.get("team_label") or "unknown",
        "team_confidence": round(float(tracklet.get("team_confidence") or 0.0), 4),
        "team_id": tracklet.get("team_id"),
        "team_name": tracklet.get("team_name"),
        "team_cluster_id": tracklet.get("team_cluster_id"),
        "team_assignment_reason": tracklet.get("team_assignment_reason"),
        "role": tracklet.get("role") or "field_player",
        "role_confidence": round(float(tracklet.get("role_confidence") or 0.0), 4),
        "goal_end": tracklet.get("goal_end"),
        "goal_zone_ratio": tracklet.get("goal_zone_ratio"),
        "appearance_rgb": _round_vector(tracklet.get("appearance_rgb"), digits=2),
        "appearance_hsv": _round_vector(tracklet.get("appearance_hsv"), digits=2),
        "appearance_lab": _round_vector(tracklet.get("appearance_lab"), digits=2),
        "appearance_feature": _round_vector(tracklet.get("appearance_feature"), digits=3),
        "appearance_quality": round(float(tracklet.get("appearance_quality") or 0.0), 4),
        "appearance_samples": int(tracklet.get("appearance_samples") or 0),
        "first_pitch_m": tracklet.get("first_pitch_m") or _round_point(first.get("pitch_m")),
        "last_pitch_m": tracklet.get("last_pitch_m") or _round_point(last.get("pitch_m")),
        "first_bbox_xyxy": first.get("bbox_xyxy") if isinstance(first, dict) else None,
        "last_bbox_xyxy": last.get("bbox_xyxy") if isinstance(last, dict) else None,
    }
    if tracklet.get("parent_tracklet_id"):
        doc["parent_tracklet_id"] = tracklet.get("parent_tracklet_id")
        doc["appearance_segment_index"] = tracklet.get("appearance_segment_index")
        doc["appearance_split_reason"] = tracklet.get("appearance_split_reason")
    if include_positions:
        doc["positions_m"] = [
            {
                "frame": int(position.get("frame") or 0),
                "time_sec": round(float(position.get("time_sec") or 0.0), 3),
                "pitch_m": _round_point(position.get("pitch_m")),
                "pitch_m_raw": _round_point(position.get("pitch_m_raw")),
                "play_area_status": position.get("play_area_status") or "inside_play",
                "pitch_boundary_distance_m": round(float(position.get("pitch_boundary_distance_m") or 0.0), 3),
                "smoothed_pitch_m": _round_point(position.get("smoothed_pitch_m") or position.get("pitch_m")),
                "bbox_xyxy": position.get("bbox_xyxy"),
                "confidence": round(float(position.get("confidence") or 0.0), 4),
            }
            for position in positions
        ]
    return doc


def _tracklet_missing_frames_count(positions: list[dict[str, Any]]) -> int:
    missing = 0
    previous_frame: int | None = None
    for position in positions:
        frame = int(position.get("frame") or 0)
        if previous_frame is not None:
            missing += max(0, frame - previous_frame - 1)
        previous_frame = frame
    return missing


def _tracklet_team_counts(tracklets: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for tracklet in tracklets:
        label = str(tracklet.get("team_label") or tracklet.get("team_candidate") or "unknown")
        counts[label] = counts.get(label, 0) + 1
    return counts


def _tracklet_frame_quality_rows(tracklets: list[dict[str, Any]], *, target_players_per_team: int) -> list[dict[str, Any]]:
    per_frame: dict[int, dict[str, Any]] = {}
    for tracklet in tracklets:
        label = str(tracklet.get("team_label") or "unknown")
        for position in tracklet.get("positions") or []:
            frame = int(position.get("frame") or 0)
            row = per_frame.setdefault(
                frame,
                {
                    "frame": frame,
                    "time_sec": round(float(position.get("time_sec") or 0.0), 3),
                    "active_tracklets": 0,
                    "team_counts": {},
                    "team_over_cap": False,
                },
            )
            row["active_tracklets"] += 1
            row["team_counts"][label] = int(row["team_counts"].get(label, 0)) + 1
    rows = []
    for row in sorted(per_frame.values(), key=lambda item: int(item.get("frame") or 0)):
        row["team_over_cap"] = any(int(count) > target_players_per_team for count in row.get("team_counts", {}).values())
        rows.append(row)
    return rows


def _tracklet_suspicious_events(clean_tracklets: list[dict[str, Any]], rejected_tracklets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for tracklet in rejected_tracklets:
        events.append(
            {
                "type": "rejected_tracklet",
                "tracklet_id": tracklet.get("tracklet_id"),
                "source_tracker_id": tracklet.get("source_tracker_id"),
                "duration_sec": tracklet.get("duration_sec"),
                "positions_count": tracklet.get("positions_count"),
                "reason": tracklet.get("reject_reason") or "short_or_missing_position",
            }
        )
    for tracklet in clean_tracklets:
        if float(tracklet.get("duration_sec") or 0.0) < 1.0:
            events.append(
                {
                    "type": "short_tracklet",
                    "tracklet_id": tracklet.get("tracklet_id"),
                    "duration_sec": tracklet.get("duration_sec"),
                    "positions_count": tracklet.get("positions_count"),
                }
            )
        if float(tracklet.get("mean_confidence") or 0.0) < 0.25:
            events.append(
                {
                    "type": "low_confidence_tracklet",
                    "tracklet_id": tracklet.get("tracklet_id"),
                    "mean_confidence": tracklet.get("mean_confidence"),
                }
            )
        if int(tracklet.get("missing_frames_count") or 0) > 0:
            events.append(
                {
                    "type": "internal_frame_gap",
                    "tracklet_id": tracklet.get("tracklet_id"),
                    "missing_frames_count": tracklet.get("missing_frames_count"),
                }
            )
    return events


def _build_tracklet(raw_track_id: int, segment_index: int, positions: list[dict[str, Any]]) -> dict[str, Any]:
    tracklet_id = f"{raw_track_id}:{segment_index}"
    pitch_points = [[float(pos["pitch_m"][0]), float(pos["pitch_m"][1])] for pos in positions]
    smoothed = _smoothed_positions(pitch_points)
    prepared_positions: list[dict[str, Any]] = []
    for position, smooth in zip(positions, smoothed):
        row = dict(position)
        row["smoothed_pitch_m"] = smooth
        row["stable_tracklet_id"] = tracklet_id
        prepared_positions.append(row)

    confidences = [float(pos.get("confidence")) for pos in positions if pos.get("confidence") is not None]
    start_time = float(prepared_positions[0].get("time_sec") or 0)
    end_time = float(prepared_positions[-1].get("time_sec") or start_time)
    return {
        "tracklet_id": tracklet_id,
        "source_track_id": raw_track_id,
        "segment_index": segment_index,
        "start_time_sec": round(start_time, 3),
        "end_time_sec": round(end_time, 3),
        "duration_sec": round(max(0.0, end_time - start_time), 3),
        "positions_count": len(prepared_positions),
        "mean_confidence": round(_mean(confidences) or 0.0, 4),
        "first_pitch_m": _round_point(prepared_positions[0].get("smoothed_pitch_m")),
        "last_pitch_m": _round_point(prepared_positions[-1].get("smoothed_pitch_m")),
        "positions": prepared_positions,
        "appearance_rgb": None,
        "appearance_samples": 0,
        "team_cluster_id": None,
        "team_confidence": 0.0,
    }


def _sample_indices(length: int, max_samples: int) -> list[int]:
    if length <= max_samples:
        return list(range(length))
    return sorted({round(idx * (length - 1) / (max_samples - 1)) for idx in range(max_samples)})


def sample_tracklet_appearance(video_path: Path, tracklets: list[dict[str, Any]], *, max_samples_per_tracklet: int = 14) -> None:
    if not tracklets:
        return

    import cv2
    import numpy as np

    requests: dict[int, list[tuple[str, int, float, list[int]]]] = {}
    by_id = {tracklet["tracklet_id"]: tracklet for tracklet in tracklets}
    for tracklet in tracklets:
        positions = tracklet.get("positions") or []
        for index in _sample_indices(len(positions), max_samples_per_tracklet):
            position = positions[index]
            bbox = position.get("bbox_xyxy")
            frame = position.get("frame")
            if bbox and frame is not None:
                requests.setdefault(int(frame), []).append(
                    (
                        tracklet["tracklet_id"],
                        int(index),
                        float(position.get("time_sec") or 0.0),
                        [int(v) for v in bbox],
                    )
                )

    if not requests:
        return

    samples: dict[str, list[dict[str, Any]]] = {tracklet["tracklet_id"]: [] for tracklet in tracklets}
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return
    try:
        requested_frames = set(requests)
        max_requested_frame = max(requested_frames)
        frame_idx = 0
        while frame_idx <= max_requested_frame:
            ok, frame = cap.read()
            if not ok:
                break
            if frame_idx not in requested_frames:
                frame_idx += 1
                continue
            height, width = frame.shape[:2]
            for tracklet_id, position_index, time_sec, bbox in requests[frame_idx]:
                x1, y1, x2, y2 = bbox
                box_w = max(1, x2 - x1)
                box_h = max(1, y2 - y1)
                crop_x1 = max(0, min(width - 1, int(x1 + box_w * 0.24)))
                crop_x2 = max(0, min(width, int(x2 - box_w * 0.24)))
                crop_y1 = max(0, min(height - 1, int(y1 + box_h * 0.12)))
                crop_y2 = max(0, min(height, int(y1 + box_h * 0.56)))
                if crop_x2 <= crop_x1 or crop_y2 <= crop_y1:
                    continue
                crop = frame[crop_y1:crop_y2, crop_x1:crop_x2]
                if crop.size == 0:
                    continue
                sample = _extract_torso_color_sample(crop)
                if sample is not None:
                    samples[tracklet_id].append(
                        {
                            **sample,
                            "frame": frame_idx,
                            "time_sec": round(time_sec, 3),
                            "position_index": position_index,
                        }
                    )
            frame_idx += 1
    finally:
        cap.release()

    for tracklet_id, colors in samples.items():
        by_id[tracklet_id]["appearance_sample_rows"] = colors
        _apply_appearance_samples(by_id[tracklet_id], colors)


def _apply_appearance_samples(tracklet: dict[str, Any], colors: list[dict[str, Any]]) -> None:
    if not colors:
        return
    tracklet["appearance_rgb"] = [
        round(median([sample["rgb"][idx] for sample in colors]), 2)
        for idx in range(3)
    ]
    tracklet["appearance_hsv"] = [
        round(median([sample["hsv"][idx] for sample in colors]), 2)
        for idx in range(3)
    ]
    tracklet["appearance_lab"] = [
        round(median([sample["lab"][idx] for sample in colors]), 2)
        for idx in range(3)
    ]
    tracklet["appearance_feature"] = [
        round(median([sample["feature"][idx] for sample in colors]), 3)
        for idx in range(len(colors[0]["feature"]))
    ]
    tracklet["appearance_quality"] = round(_mean([float(sample["quality"]) for sample in colors]) or 0.0, 4)
    tracklet["appearance_samples"] = len(colors)


def split_tracklets_by_appearance_changes(
    tracklets: list[dict[str, Any]],
    *,
    min_run_samples: int = 2,
    min_positions: int = 4,
    min_duration_sec: float = 0.2,
) -> list[dict[str, Any]]:
    segmented: list[tuple[int, float, list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]] = []
    for tracklet in tracklets:
        positions = tracklet.get("positions") or []
        cut_indices = _appearance_split_cut_indices(tracklet, min_run_samples=min_run_samples)
        if not cut_indices:
            segmented.append(
                (
                    int(tracklet.get("source_track_id") or 0),
                    float(tracklet.get("start_time_sec") or 0.0),
                    positions,
                    tracklet.get("appearance_sample_rows") or [],
                    None,
                )
            )
            continue
        start = 0
        candidate_segments: list[tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]] = []
        for segment_number, end in enumerate([*cut_indices, len(positions)], start=1):
            segment_positions = positions[start:end]
            segment_samples = [
                sample
                for sample in tracklet.get("appearance_sample_rows") or []
                if start <= int(sample.get("position_index") or 0) < end
            ]
            if _tracklet_segment_is_too_short(segment_positions, min_positions=min_positions, min_duration_sec=min_duration_sec):
                candidate_segments = []
                break
            candidate_segments.append(
                (
                    segment_positions,
                    segment_samples,
                    {
                        "parent_tracklet_id": tracklet.get("tracklet_id"),
                        "appearance_segment_index": segment_number,
                        "appearance_split_reason": "torso_color_change",
                    },
                )
            )
            start = end
        if not candidate_segments:
            segmented.append(
                (
                    int(tracklet.get("source_track_id") or 0),
                    float(tracklet.get("start_time_sec") or 0.0),
                    positions,
                    tracklet.get("appearance_sample_rows") or [],
                    None,
                )
            )
            continue
        for segment_positions, segment_samples, split_meta in candidate_segments:
            segmented.append(
                (
                    int(tracklet.get("source_track_id") or 0),
                    float(segment_positions[0].get("time_sec") or 0.0),
                    segment_positions,
                    segment_samples,
                    split_meta,
                )
            )

    result: list[dict[str, Any]] = []
    per_raw_index: dict[int, int] = {}
    for raw_track_id, _start_time, positions, samples, split_meta in sorted(segmented, key=lambda item: (item[0], item[1])):
        per_raw_index[raw_track_id] = per_raw_index.get(raw_track_id, 0) + 1
        rebuilt = _build_tracklet(raw_track_id, per_raw_index[raw_track_id], positions)
        if split_meta:
            rebuilt.update(split_meta)
        rebuilt["appearance_sample_rows"] = _reindexed_appearance_samples(samples, positions)
        _apply_appearance_samples(rebuilt, rebuilt["appearance_sample_rows"])
        result.append(rebuilt)
    return result


def _appearance_split_cut_indices(tracklet: dict[str, Any], *, min_run_samples: int) -> list[int]:
    return sorted(
        set(
            _team_color_appearance_split_cut_indices(tracklet, min_run_samples=min_run_samples)
            + _neutral_identity_appearance_split_cut_indices(tracklet, min_run_samples=min_run_samples)
        )
    )


def _team_color_appearance_split_cut_indices(tracklet: dict[str, Any], *, min_run_samples: int) -> list[int]:
    labeled = [
        {
            **sample,
            "appearance_label": _appearance_sample_team_label(sample),
        }
        for sample in sorted(tracklet.get("appearance_sample_rows") or [], key=lambda item: int(item.get("position_index") or 0))
    ]
    labeled = [sample for sample in labeled if sample["appearance_label"] in {"neutral", "colored"}]
    if len(labeled) < min_run_samples * 2:
        return []

    runs: list[list[dict[str, Any]]] = []
    for sample in labeled:
        if not runs or runs[-1][0]["appearance_label"] != sample["appearance_label"]:
            runs.append([sample])
        else:
            runs[-1].append(sample)

    cuts: list[int] = []
    for previous, following in zip(runs, runs[1:]):
        if len(previous) < min_run_samples or len(following) < min_run_samples:
            continue
        previous_index = int(previous[-1].get("position_index") or 0)
        following_index = int(following[0].get("position_index") or 0)
        cut = max(previous_index + 1, (previous_index + following_index) // 2 + 1)
        cuts.append(cut)
    return sorted(set(cuts))


def _neutral_identity_appearance_split_cut_indices(tracklet: dict[str, Any], *, min_run_samples: int) -> list[int]:
    labeled = [
        {
            **sample,
            "appearance_label": _neutral_identity_appearance_label(sample),
        }
        for sample in sorted(tracklet.get("appearance_sample_rows") or [], key=lambda item: int(item.get("position_index") or 0))
    ]
    labeled = [sample for sample in labeled if sample["appearance_label"] in {"neutral_bright", "neutral_muted"}]
    if len(labeled) < min_run_samples * 2:
        return []

    runs: list[list[dict[str, Any]]] = []
    for sample in labeled:
        if not runs or runs[-1][0]["appearance_label"] != sample["appearance_label"]:
            runs.append([sample])
        else:
            runs[-1].append(sample)

    cuts: list[int] = []
    for previous, following in zip(runs, runs[1:]):
        if len(previous) < min_run_samples or len(following) < min_run_samples:
            continue
        previous_index = int(previous[-1].get("position_index") or 0)
        following_index = int(following[0].get("position_index") or 0)
        cut = max(previous_index + 1, (previous_index + following_index) // 2 + 1)
        cuts.append(cut)
    return sorted(set(cuts))


def _neutral_identity_appearance_label(sample: dict[str, Any]) -> str:
    if _appearance_sample_team_label(sample) != "neutral":
        return "unknown"
    if float(sample.get("quality") or 0.0) < 0.45:
        return "unknown"
    rgb = sample.get("rgb")
    lab = sample.get("lab")
    if not isinstance(rgb, list | tuple) or len(rgb) < 3:
        return "unknown"
    mean_rgb = sum(float(value) for value in rgb[:3]) / 3.0
    lightness = float(lab[0]) if isinstance(lab, list | tuple) and lab else mean_rgb
    if lightness >= 212.0 or mean_rgb >= 210.0:
        return "neutral_bright"
    if lightness <= 202.0 and mean_rgb <= 202.0:
        return "neutral_muted"
    return "unknown"


def _appearance_sample_team_label(sample: dict[str, Any]) -> str:
    fake_tracklet = {
        "appearance_rgb": sample.get("rgb"),
        "appearance_hsv": sample.get("hsv"),
        "appearance_lab": sample.get("lab"),
        "appearance_feature": sample.get("feature"),
        "appearance_quality": sample.get("quality"),
        "appearance_samples": 1,
    }
    if _is_team_color_outlier(fake_tracklet):
        return "outlier"
    if _is_white_or_neutral_color(fake_tracklet):
        return "neutral"
    if _is_colored_team_color(fake_tracklet):
        return "colored"
    return "unknown"


def _tracklet_segment_is_too_short(
    positions: list[dict[str, Any]],
    *,
    min_positions: int,
    min_duration_sec: float,
) -> bool:
    if len(positions) < min_positions:
        return True
    start = float(positions[0].get("time_sec") or 0.0)
    end = float(positions[-1].get("time_sec") or start)
    return max(0.0, end - start) < min_duration_sec


def _reindexed_appearance_samples(samples: list[dict[str, Any]], positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    frame_to_index = {int(position.get("frame") or 0): index for index, position in enumerate(positions)}
    rows = []
    for sample in samples:
        frame = int(sample.get("frame") or 0)
        row = dict(sample)
        if frame in frame_to_index:
            row["position_index"] = frame_to_index[frame]
        rows.append(row)
    return rows


def _extract_torso_color_sample(crop: Any) -> dict[str, Any] | None:
    import cv2
    import numpy as np

    if crop.size == 0:
        return None
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB)
    hue = hsv[:, :, 0]
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    bright_enough = value > 42
    not_turf = ~((hue >= 35) & (hue <= 95) & (saturation > 25) & (value < 190))
    jersey_like = bright_enough & not_turf & ((saturation > 30) | (value > 105))
    if int(jersey_like.sum()) < 8:
        jersey_like = bright_enough & not_turf
    if int(jersey_like.sum()) < 8:
        return None

    bgr_pixels = crop[jersey_like]
    hsv_pixels = hsv[jersey_like]
    lab_pixels = lab[jersey_like]
    rgb_pixels = bgr_pixels[:, ::-1]
    median_rgb = np.median(rgb_pixels, axis=0)
    median_hsv = np.median(hsv_pixels, axis=0)
    median_lab = np.median(lab_pixels, axis=0)
    selected_ratio = float(jersey_like.sum()) / float(jersey_like.size)
    saturation_score = float(np.median(hsv_pixels[:, 1])) / 255.0
    value_score = float(np.median(hsv_pixels[:, 2])) / 255.0
    quality = max(0.0, min(1.0, selected_ratio * 0.45 + saturation_score * 0.25 + value_score * 0.3))
    return {
        "rgb": [float(median_rgb[0]), float(median_rgb[1]), float(median_rgb[2])],
        "hsv": [float(median_hsv[0]), float(median_hsv[1]), float(median_hsv[2])],
        "lab": [float(median_lab[0]), float(median_lab[1]), float(median_lab[2])],
        "feature": _team_color_feature_from_hsv_lab(median_hsv, median_lab),
        "quality": quality,
    }


def cluster_tracklet_teams(tracklets: list[dict[str, Any]], teams: list[dict[str, Any]]) -> dict[str, Any]:
    colored = [tracklet for tracklet in tracklets if _tracklet_team_feature(tracklet) is not None]
    references = [
        tracklet
        for tracklet in colored
        if int(tracklet.get("appearance_samples") or 0) >= TEAM_COLOR_MIN_REFERENCE_SAMPLES
        and float(tracklet.get("appearance_quality") if tracklet.get("appearance_quality") is not None else 0.35) >= TEAM_COLOR_MIN_REFERENCE_QUALITY
        and int(tracklet.get("positions_count") or 0) >= 4
    ]
    if len(references) < 2:
        return _empty_team_clusters(tracklets, teams)

    neutral_references, colored_references = _neutral_vs_colored_references(references)
    use_neutral_colored_strategy = len(neutral_references) >= 2 and len(colored_references) >= 2
    if use_neutral_colored_strategy:
        centers = [_weighted_feature_center(neutral_references), _weighted_feature_center(colored_references)]
        cluster_to_team = {
            0: teams[0] if len(teams) >= 1 else {"id": None, "name": "Team A"},
            1: teams[1] if len(teams) >= 2 else {"id": None, "name": "Team B"},
        }
        method = "torso_color_neutral_vs_colored_v1"
    else:
        kmeans_references = _team_color_references_without_outliers(references)
        centers = _kmeans_two_team_features(kmeans_references if len(kmeans_references) >= 2 else references)
        team_refs = [(team, _hex_to_rgb(team.get("color"))) for team in teams[:2]]
        cluster_to_team = _map_clusters_to_teams(centers, kmeans_references if len(kmeans_references) >= 2 else references, team_refs)
        method = "torso_color_lab_hsv_weighted_kmeans_v2"

    assignments: dict[str, int] = {}
    tracklet_confidences: dict[str, float] = {}
    for tracklet in colored:
        feature = _tracklet_team_feature(tracklet)
        if feature is None:
            continue
        if _is_team_color_outlier(tracklet):
            tracklet["team_assignment_reason"] = "team_color_outlier"
            continue
        distances = [_feature_distance(feature, center) for center in centers]
        cluster_idx = 0 if distances[0] <= distances[1] else 1
        own = distances[cluster_idx]
        other = distances[1 - cluster_idx]
        margin = max(0.0, other - own)
        confidence = max(0.0, min(1.0, 0.35 + margin / max(45.0, _feature_distance(centers[0], centers[1]))))
        assignments[tracklet["tracklet_id"]] = cluster_idx
        tracklet_confidences[tracklet["tracklet_id"]] = confidence

    clusters: list[dict[str, Any]] = []
    for cluster_idx, center in enumerate(centers):
        members = [tracklet for tracklet in colored if assignments.get(tracklet["tracklet_id"]) == cluster_idx]
        confident_members = [
            member
            for member in members
            if tracklet_confidences.get(member["tracklet_id"], 0.0) >= TEAM_COLOR_UNKNOWN_CONFIDENCE
            and _feature_distance(_tracklet_team_feature(member) or center, center) <= TEAM_COLOR_MAX_ASSIGNMENT_DISTANCE
        ]
        other_center = centers[1 - cluster_idx] if len(centers) > 1 else None
        distances = [_feature_distance(_tracklet_team_feature(member) or center, center) for member in confident_members]
        other_distances = [_feature_distance(_tracklet_team_feature(member) or center, other_center) for member in confident_members] if other_center else []
        own = _mean(distances) or 0.0
        other = _mean(other_distances) or own
        confidence = max(0.0, min(1.0, (other - own) / 85.0 + 0.45)) if other_center else 0.35
        team = cluster_to_team.get(cluster_idx)
        team_label = chr(ord("A") + cluster_idx)
        if team:
            try:
                team_index = teams.index(team)
                team_label = chr(ord("A") + team_index)
            except ValueError:
                pass
        cluster_doc = {
            "cluster_id": f"cluster-{cluster_idx + 1}",
            "team_label": team_label,
            "team_id": team.get("id") if team else None,
            "team_name": team.get("name") if team else f"Team {team_label}",
            "center_rgb": _cluster_center_rgb(confident_members),
            "center_hsv": _cluster_center_vector(confident_members, "appearance_hsv"),
            "center_lab": _cluster_center_vector(confident_members, "appearance_lab"),
            "color_hex": _rgb_to_hex(_cluster_center_rgb(confident_members)),
            "tracklets_count": len(confident_members),
            "candidate_tracklets_count": len(members),
            "reference_tracklets_count": (
                len(neutral_references)
                if use_neutral_colored_strategy and cluster_idx == 0
                else len(colored_references)
                if use_neutral_colored_strategy and cluster_idx == 1
                else sum(1 for item in references if assignments.get(item["tracklet_id"]) == cluster_idx)
            ),
            "confidence": round(confidence, 3),
        }
        clusters.append(cluster_doc)
        for member in members:
            member_confidence = tracklet_confidences.get(member["tracklet_id"], 0.0)
            member_distance = _feature_distance(_tracklet_team_feature(member) or center, center)
            if member_confidence < TEAM_COLOR_UNKNOWN_CONFIDENCE or member_distance > TEAM_COLOR_MAX_ASSIGNMENT_DISTANCE:
                continue
            member["team_cluster_id"] = cluster_doc["cluster_id"]
            member["team_label"] = cluster_doc["team_label"]
            member["team_id"] = cluster_doc["team_id"]
            member["team_name"] = cluster_doc["team_name"]
            member["team_confidence"] = round(member_confidence, 4)

    for tracklet in tracklets:
        if not tracklet.get("team_cluster_id"):
            tracklet["team_label"] = "U"
            tracklet["team_id"] = None
            tracklet["team_name"] = "Unknown"
            tracklet["team_confidence"] = 0.0

    return {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "method": method,
        "clusters": clusters,
        "reference_tracklets_count": len(references),
        "candidate_tracklets_count": len(colored),
        "neutral_reference_tracklets_count": len(neutral_references),
        "colored_reference_tracklets_count": len(colored_references),
        "white_reference_tracklets_count": len(neutral_references),
        "bib_reference_tracklets_count": len(colored_references),
        "team_color_outliers_count": sum(1 for tracklet in colored if _is_team_color_outlier(tracklet)),
        "goalkeeper_color_outliers_count": sum(1 for tracklet in colored if _is_team_color_outlier(tracklet)),
        "unknown_tracklets": [tracklet["tracklet_id"] for tracklet in tracklets if tracklet.get("team_label") == "U"],
    }


def _empty_team_clusters(tracklets: list[dict[str, Any]], teams: list[dict[str, Any]]) -> dict[str, Any]:
    for tracklet in tracklets:
        tracklet["team_label"] = "U"
        tracklet["team_id"] = None
        tracklet["team_name"] = "Unknown"
        tracklet["team_confidence"] = 0.0
    return {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "method": "torso_color_lab_hsv_weighted_kmeans_v2",
        "clusters": [],
        "reference_tracklets_count": 0,
        "candidate_tracklets_count": len([tracklet for tracklet in tracklets if _tracklet_team_feature(tracklet) is not None]),
        "unknown_tracklets": [tracklet["tracklet_id"] for tracklet in tracklets],
        "warning": "Not enough appearance samples to create team clusters.",
    }


def _team_color_feature_from_hsv_lab(hsv: Any, lab: Any) -> list[float]:
    hue = float(hsv[0])
    saturation = float(hsv[1])
    value = float(hsv[2])
    lab_l = float(lab[0])
    lab_a = float(lab[1])
    lab_b = float(lab[2])
    hue_radians = (hue / 180.0) * math.tau
    hue_weight = saturation / 255.0
    return [
        lab_l * 0.35,
        (lab_a - 128.0) * 1.2,
        (lab_b - 128.0) * 1.2,
        math.cos(hue_radians) * hue_weight * 45.0,
        math.sin(hue_radians) * hue_weight * 45.0,
        saturation * 0.22,
        value * 0.12,
    ]


def _tracklet_team_feature(tracklet: dict[str, Any]) -> list[float] | None:
    feature = tracklet.get("appearance_feature")
    if isinstance(feature, list) and len(feature) >= 3:
        return [float(value) for value in feature]
    hsv = tracklet.get("appearance_hsv")
    lab = tracklet.get("appearance_lab")
    if isinstance(hsv, list) and len(hsv) >= 3 and isinstance(lab, list) and len(lab) >= 3:
        return _team_color_feature_from_hsv_lab(hsv, lab)
    rgb = tracklet.get("appearance_rgb")
    if isinstance(rgb, list) and len(rgb) >= 3:
        return [float(rgb[0]), float(rgb[1]), float(rgb[2])]
    return None


def _feature_distance(a: list[float], b: list[float]) -> float:
    size = min(len(a), len(b))
    if size == 0:
        return 999.0
    return math.sqrt(sum((float(a[idx]) - float(b[idx])) ** 2 for idx in range(size)))


def _appearance_weight(tracklet: dict[str, Any]) -> float:
    sample_score = min(1.0, float(tracklet.get("appearance_samples") or 0) / 8.0)
    quality_score = max(0.0, min(1.0, float(tracklet.get("appearance_quality") or 0.35)))
    duration_score = min(1.0, float(tracklet.get("duration_sec") or 0.0) / 1.5)
    confidence_score = max(0.0, min(1.0, float(tracklet.get("mean_confidence") or 0.0)))
    return max(0.15, sample_score * 0.3 + quality_score * 0.35 + duration_score * 0.15 + confidence_score * 0.2)


def _tracklet_color_profile(tracklet: dict[str, Any]) -> dict[str, float] | None:
    rgb = tracklet.get("appearance_rgb")
    hsv = tracklet.get("appearance_hsv")
    if not isinstance(rgb, list) or len(rgb) < 3:
        return None
    red = float(rgb[0])
    green = float(rgb[1])
    blue = float(rgb[2])
    max_channel = max(red, green, blue)
    min_channel = min(red, green, blue)
    saturation = float(hsv[1]) if isinstance(hsv, list) and len(hsv) >= 3 else (0.0 if max_channel <= 0 else (max_channel - min_channel) / max_channel * 255.0)
    value = float(hsv[2]) if isinstance(hsv, list) and len(hsv) >= 3 else max_channel
    channel_spread = max_channel - min_channel
    red_advantage = red - max(green, blue)
    green_advantage = green - max(red, blue)
    neutral_score = value - saturation * 0.8 - channel_spread * 0.25
    bib_score = red_advantage + saturation * 0.22
    return {
        "red": red,
        "green": green,
        "blue": blue,
        "saturation": saturation,
        "value": value,
        "channel_spread": channel_spread,
        "red_advantage": red_advantage,
        "green_advantage": green_advantage,
        "neutral_score": neutral_score,
        "bib_score": bib_score,
    }


def _is_bib_color(tracklet: dict[str, Any]) -> bool:
    profile = _tracklet_color_profile(tracklet)
    if profile is None:
        return False
    return (
        profile["red_advantage"] >= 22.0
        and profile["saturation"] >= 70.0
        and profile["value"] >= 95.0
    ) or profile["bib_score"] >= 55.0


def _is_colored_team_color(tracklet: dict[str, Any]) -> bool:
    profile = _tracklet_color_profile(tracklet)
    if profile is None or _is_team_color_outlier(tracklet) or _is_white_or_neutral_color(tracklet):
        return False
    return (
        profile["saturation"] >= 62.0
        and profile["value"] >= 70.0
        and profile["channel_spread"] >= 32.0
    )


def _is_white_or_neutral_color(tracklet: dict[str, Any]) -> bool:
    profile = _tracklet_color_profile(tracklet)
    if profile is None:
        return False
    if _is_team_color_outlier(tracklet):
        return False
    return (
        profile["value"] >= 95.0
        and profile["saturation"] <= 95.0
        and profile["channel_spread"] <= 75.0
        and profile["neutral_score"] >= 40.0
    )


def _is_team_color_outlier(tracklet: dict[str, Any]) -> bool:
    profile = _tracklet_color_profile(tracklet)
    if profile is None:
        return False
    green_outlier = (
        profile["green_advantage"] >= 24.0
        and profile["saturation"] >= 55.0
        and profile["value"] >= 95.0
    )
    fluorescent_outlier = (
        profile["green"] >= 175.0
        and profile["red"] >= 100.0
        and profile["blue"] <= 120.0
        and profile["saturation"] >= 70.0
    )
    dark_saturated_outlier = profile["value"] <= 82.0 and profile["saturation"] >= 80.0
    return green_outlier or fluorescent_outlier or dark_saturated_outlier


def _is_goalkeeper_color_outlier(tracklet: dict[str, Any]) -> bool:
    return _is_team_color_outlier(tracklet)


def _neutral_vs_colored_references(tracklets: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    neutral = [tracklet for tracklet in tracklets if _is_white_or_neutral_color(tracklet)]
    colored = [tracklet for tracklet in tracklets if _is_colored_team_color(tracklet)]
    neutral = sorted(neutral, key=_appearance_weight, reverse=True)[:32]
    colored = sorted(colored, key=_appearance_weight, reverse=True)[:32]
    return neutral, colored


def _team_color_references_without_outliers(tracklets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [tracklet for tracklet in tracklets if not _is_team_color_outlier(tracklet)]


def _weighted_feature_center(tracklets: list[dict[str, Any]]) -> list[float]:
    features = [(_tracklet_team_feature(tracklet), _appearance_weight(tracklet)) for tracklet in tracklets]
    features = [(feature, weight) for feature, weight in features if feature is not None]
    if not features:
        return [0.0, 0.0, 0.0]
    total_weight = sum(weight for _, weight in features)
    return [
        sum(feature[channel] * weight for feature, weight in features) / max(total_weight, 0.001)
        for channel in range(len(features[0][0]))
    ]


def _kmeans_two_team_features(tracklets: list[dict[str, Any]]) -> list[list[float]]:
    first_tracklet = max(tracklets, key=_appearance_weight)
    first = _tracklet_team_feature(first_tracklet) or [0.0, 0.0, 0.0]
    second_tracklet = max(tracklets, key=lambda item: _feature_distance(first, _tracklet_team_feature(item) or first) * _appearance_weight(item))
    second = _tracklet_team_feature(second_tracklet) or list(first)
    centers = [list(first), list(second)]
    for _ in range(12):
        buckets: list[list[tuple[list[float], float]]] = [[], []]
        for tracklet in tracklets:
            feature = _tracklet_team_feature(tracklet)
            if feature is None:
                continue
            distances = [_feature_distance(feature, center) for center in centers]
            buckets[0 if distances[0] <= distances[1] else 1].append((feature, _appearance_weight(tracklet)))
        next_centers = []
        for idx, bucket in enumerate(buckets):
            if not bucket:
                next_centers.append(centers[idx])
            else:
                total_weight = sum(weight for _, weight in bucket)
                next_centers.append(
                    [
                        sum(feature[channel] * weight for feature, weight in bucket) / max(total_weight, 0.001)
                        for channel in range(len(bucket[0][0]))
                    ]
                )
        if all(_feature_distance(centers[idx], next_centers[idx]) < 0.5 for idx in range(2)):
            centers = next_centers
            break
        centers = next_centers
    return centers


def _cluster_center_vector(members: list[dict[str, Any]], key: str) -> list[float] | None:
    vectors = [member.get(key) for member in members if isinstance(member.get(key), list) and len(member.get(key)) >= 3]
    if not vectors:
        return None
    return [round(_mean([float(vector[idx]) for vector in vectors]) or 0.0, 2) for idx in range(3)]


def _cluster_center_rgb(members: list[dict[str, Any]]) -> list[float] | None:
    return _cluster_center_vector(members, "appearance_rgb")


def _cluster_white_score(tracklets: list[dict[str, Any]]) -> float:
    hsv_values = [item.get("appearance_hsv") for item in tracklets if isinstance(item.get("appearance_hsv"), list)]
    if not hsv_values:
        rgb = _cluster_center_rgb(tracklets)
        return _mean(rgb or []) or 0.0
    saturation = _mean([float(hsv[1]) for hsv in hsv_values]) or 0.0
    value = _mean([float(hsv[2]) for hsv in hsv_values]) or 0.0
    return value - saturation * 0.7


def _map_clusters_to_teams(
    centers: list[list[float]],
    references: list[dict[str, Any]],
    team_refs: list[tuple[dict[str, Any], list[float] | None]],
) -> dict[int, dict[str, Any]]:
    cluster_members = []
    if len(team_refs) >= 2:
        for center in centers:
            cluster_members.append(
                [
                    tracklet
                    for tracklet in references
                    if _tracklet_team_feature(tracklet) is not None
                    and _feature_distance(_tracklet_team_feature(tracklet) or center, center)
                    <= min(_feature_distance(_tracklet_team_feature(tracklet) or center, other) for other in centers)
                ]
            )
        if len(cluster_members) >= 2:
            white_scores = [_cluster_white_score(members) for members in cluster_members]
            if abs(white_scores[0] - white_scores[1]) >= 28.0:
                whiter_idx = 0 if white_scores[0] > white_scores[1] else 1
                other_idx = 1 - whiter_idx
                return {whiter_idx: team_refs[0][0], other_idx: team_refs[1][0]}

    usable_refs = [(team, rgb) for team, rgb in team_refs if rgb]
    if len(usable_refs) < 2:
        return {idx: team_refs[idx][0] for idx in range(min(len(centers), len(team_refs)))}

    if not cluster_members:
        cluster_members = [[] for _ in centers]
    cluster_rgbs = [_cluster_center_rgb(members) for members in cluster_members]
    if len(cluster_rgbs) >= 2 and cluster_rgbs[0] and cluster_rgbs[1]:
        direct = (_color_distance(cluster_rgbs[0], usable_refs[0][1]) or 999.0) + (_color_distance(cluster_rgbs[1], usable_refs[1][1]) or 999.0)
        swapped = (_color_distance(cluster_rgbs[0], usable_refs[1][1]) or 999.0) + (_color_distance(cluster_rgbs[1], usable_refs[0][1]) or 999.0)
        if min(direct, swapped) <= 220.0:
            if swapped < direct:
                return {0: usable_refs[1][0], 1: usable_refs[0][0]}
            return {0: usable_refs[0][0], 1: usable_refs[1][0]}
    return {0: usable_refs[0][0], 1: usable_refs[1][0]}


def apply_goalkeeper_role_adjustments(tracklets: list[dict[str, Any]], *, pitch_length_m: float) -> dict[str, Any]:
    adjusted: list[dict[str, Any]] = []
    for tracklet in tracklets:
        candidate = _goalkeeper_role_candidate(tracklet, pitch_length_m=pitch_length_m)
        if candidate is None:
            continue
        tracklet["role"] = "goalkeeper"
        tracklet["role_confidence"] = candidate["role_confidence"]
        tracklet["goal_end"] = candidate["goal_end"]
        tracklet["goal_zone_ratio"] = candidate["goal_zone_ratio"]
        previous_team = tracklet.get("team_label")
        color_is_unreliable = bool(candidate.get("color_outlier") or candidate.get("dark_goalkeeper_color"))
        if color_is_unreliable:
            tracklet["team_label"] = "U"
            tracklet["team_id"] = None
            tracklet["team_name"] = "Unknown"
            tracklet["team_confidence"] = 0.0
            tracklet["team_assignment_reason"] = "goalkeeper_outlier_requires_review"
        else:
            tracklet["team_assignment_reason"] = tracklet.get("team_assignment_reason") or "goalkeeper_role_detected"
        adjusted.append(
            {
                "tracklet_id": tracklet.get("tracklet_id"),
                "raw_track_id": tracklet.get("source_track_id"),
                "goal_end": candidate["goal_end"],
                "role_confidence": candidate["role_confidence"],
                "goal_zone_ratio": candidate["goal_zone_ratio"],
                "previous_team_label": previous_team,
                "team_label": tracklet.get("team_label"),
                "team_assignment_reason": tracklet.get("team_assignment_reason"),
            }
        )
    return {
        "method": "goal_zone_color_outlier_v1",
        "goal_zone_edge_ratio": 0.18,
        "adjusted_tracklets": len(adjusted),
        "review_required_tracklets": sum(1 for row in adjusted if row.get("team_label") == "U"),
        "examples": adjusted[:100],
    }


def _goalkeeper_role_candidate(tracklet: dict[str, Any], *, pitch_length_m: float) -> dict[str, Any] | None:
    positions = [
        position
        for position in (tracklet.get("positions") or [])
        if isinstance(position, dict) and isinstance(position.get("pitch_m"), list) and len(position.get("pitch_m") or []) >= 2
    ]
    if len(positions) < 8:
        return None
    edge_m = max(2.5, float(pitch_length_m) * 0.18)
    near = sum(1 for position in positions if float(position["pitch_m"][1]) <= edge_m)
    far = sum(1 for position in positions if float(position["pitch_m"][1]) >= float(pitch_length_m) - edge_m)
    count = max(1, len(positions))
    near_ratio = near / count
    far_ratio = far / count
    goal_zone_ratio = max(near_ratio, far_ratio)
    if goal_zone_ratio < 0.72:
        return None
    duration_sec = float(tracklet.get("duration_sec") or 0.0)
    color_outlier = _is_goalkeeper_color_outlier(tracklet)
    dark_goalkeeper_color = _is_dark_goalkeeper_color(tracklet)
    if not (color_outlier or dark_goalkeeper_color or (goal_zone_ratio >= 0.92 and duration_sec >= 4.0)):
        return None
    confidence = min(
        1.0,
        0.35
        + goal_zone_ratio * 0.45
        + min(duration_sec / 10.0, 1.0) * 0.1
        + (0.1 if color_outlier or dark_goalkeeper_color else 0.0),
    )
    return {
        "goal_end": "near" if near_ratio >= far_ratio else "far",
        "role_confidence": round(confidence, 4),
        "goal_zone_ratio": round(goal_zone_ratio, 4),
        "color_outlier": color_outlier,
        "dark_goalkeeper_color": dark_goalkeeper_color,
    }


def _is_dark_goalkeeper_color(tracklet: dict[str, Any]) -> bool:
    profile = _tracklet_color_profile(tracklet)
    if profile is None:
        return False
    return profile["value"] <= 92.0 and profile["saturation"] <= 115.0


def build_stable_players(
    tracklets: list[dict[str, Any]],
    *,
    max_link_gap_sec: float = 3.0,
    max_link_speed_mps: float = 9.5,
    max_link_distance_m: float = 12.0,
) -> list[dict[str, Any]]:
    stable_players: list[dict[str, Any]] = []
    for tracklet in sorted(tracklets, key=lambda item: (float(item["start_time_sec"]), -float(item["duration_sec"]))):
        best_player: dict[str, Any] | None = None
        best_score: float | None = None
        best_link: dict[str, Any] | None = None
        for player in stable_players:
            link = _score_tracklet_link(
                player,
                tracklet,
                max_link_gap_sec=max_link_gap_sec,
                max_link_speed_mps=max_link_speed_mps,
                max_link_distance_m=max_link_distance_m,
            )
            if not link:
                continue
            if best_score is None or link["score"] < best_score:
                best_player = player
                best_score = link["score"]
                best_link = link
        if best_player is None or best_link is None:
            stable_players.append(_new_stable_player(len(stable_players) + 1, tracklet))
        else:
            _append_tracklet_to_player(best_player, tracklet, best_link)

    for player in stable_players:
        _finalize_stable_player(player)
    renumber_stable_players(stable_players)
    return stable_players


def _score_tracklet_link(
    player: dict[str, Any],
    tracklet: dict[str, Any],
    *,
    max_link_gap_sec: float,
    max_link_speed_mps: float,
    max_link_distance_m: float,
) -> dict[str, Any] | None:
    start = float(tracklet["start_time_sec"])
    previous_end = float(player["end_time_sec"])
    if start <= previous_end - 0.05:
        return None
    gap = start - previous_end
    if gap > max_link_gap_sec:
        return None
    distance = _distance_m(player.get("last_pitch_m"), tracklet.get("first_pitch_m"))
    if distance is None:
        return None
    if distance > max_link_distance_m:
        return None
    required_speed = distance / max(gap, 0.4)
    if required_speed > max_link_speed_mps:
        return None

    player_team = player.get("team_label")
    tracklet_team = tracklet.get("team_label")
    if player_team not in {None, "U"} and tracklet_team not in {None, "U"} and player_team != tracklet_team:
        return None

    color_distance = _color_distance(player.get("appearance_rgb"), tracklet.get("appearance_rgb"))
    normalized_color = min(1.0, color_distance / 180.0) if color_distance is not None else 0.35
    score = distance + gap * 1.2 + normalized_color * 4.0
    confidence_score = max(0.0, min(1.0, 1.0 - (distance / 8.0) - (gap / 6.0) - normalized_color * 0.25))
    return {
        "score": round(score, 4),
        "gap_sec": round(gap, 3),
        "distance_m": round(distance, 3),
        "required_speed_mps": round(required_speed, 3),
        "confidence_score": round(confidence_score, 4),
        "confidence": confidence_level(confidence_score),
    }


def confidence_level(score: float) -> str:
    if score >= 0.72:
        return "high"
    if score >= 0.45:
        return "medium"
    return "low"


def _new_stable_player(index: int, tracklet: dict[str, Any]) -> dict[str, Any]:
    return {
        "stable_subject_id": f"sp-{index:03d}",
        "stable_player_id": "",
        "status": "active",
        "team_label": tracklet.get("team_label") or "U",
        "team_id": tracklet.get("team_id"),
        "team_name": tracklet.get("team_name") or "Unknown",
        "team_confidence": tracklet.get("team_confidence") or 0.0,
        "tracklet_ids": [tracklet["tracklet_id"]],
        "raw_track_ids": [tracklet["source_track_id"]],
        "tracklet_count": 1,
        "positions": list(tracklet.get("positions") or []),
        "start_time_sec": tracklet["start_time_sec"],
        "end_time_sec": tracklet["end_time_sec"],
        "first_pitch_m": tracklet.get("first_pitch_m"),
        "last_pitch_m": tracklet.get("last_pitch_m"),
        "appearance_rgb": tracklet.get("appearance_rgb"),
        "appearance_samples": tracklet.get("appearance_samples") or 0,
        "link_confidences": [],
        "risky_links": [],
    }


def _append_tracklet_to_player(player: dict[str, Any], tracklet: dict[str, Any], link: dict[str, Any]) -> None:
    previous_tracklet_id = player["tracklet_ids"][-1]
    player["tracklet_ids"].append(tracklet["tracklet_id"])
    if tracklet["source_track_id"] not in player["raw_track_ids"]:
        player["raw_track_ids"].append(tracklet["source_track_id"])
    player["tracklet_count"] += 1
    player["positions"].extend(tracklet.get("positions") or [])
    player["end_time_sec"] = max(float(player["end_time_sec"]), float(tracklet["end_time_sec"]))
    player["last_pitch_m"] = tracklet.get("last_pitch_m") or player.get("last_pitch_m")
    player["link_confidences"].append(link["confidence_score"])
    if link["confidence"] != "high":
        player["risky_links"].append(
            {
                "from_tracklet_id": previous_tracklet_id,
                "to_tracklet_id": tracklet["tracklet_id"],
                "gap_sec": link["gap_sec"],
                "distance_m": link["distance_m"],
                "required_speed_mps": link["required_speed_mps"],
                "confidence": link["confidence"],
            }
        )
    if player.get("appearance_rgb") and tracklet.get("appearance_rgb"):
        player["appearance_rgb"] = [
            round((float(player["appearance_rgb"][idx]) * (player["tracklet_count"] - 1) + float(tracklet["appearance_rgb"][idx])) / player["tracklet_count"], 2)
            for idx in range(3)
        ]
    elif tracklet.get("appearance_rgb"):
        player["appearance_rgb"] = tracklet["appearance_rgb"]
    player["appearance_samples"] = int(player.get("appearance_samples") or 0) + int(tracklet.get("appearance_samples") or 0)
    if player.get("team_label") in {None, "U"} and tracklet.get("team_label") not in {None, "U"}:
        player["team_label"] = tracklet.get("team_label")
        player["team_id"] = tracklet.get("team_id")
        player["team_name"] = tracklet.get("team_name")
        player["team_confidence"] = tracklet.get("team_confidence") or 0.0


def _finalize_stable_player(player: dict[str, Any]) -> None:
    detected_positions = sorted(player.get("positions") or [], key=lambda item: (int(item.get("frame", 0)), float(item.get("time_sec", 0))))
    positions, interpolation_stats = _positions_with_short_gap_interpolation(detected_positions)
    player["positions"] = positions
    player["positions_count"] = len(detected_positions)
    player["real_positions_count"] = len(detected_positions)
    player["overlay_positions_count"] = len([position for position in positions if position.get("bbox_xyxy") is not None])
    player.update(interpolation_stats)
    player["duration_sec"] = round(max(0.0, float(player["end_time_sec"]) - float(player["start_time_sec"])), 3)
    confidences = [float(pos.get("confidence")) for pos in detected_positions if pos.get("confidence") is not None]
    player["mean_detection_confidence"] = round(_mean(confidences) or 0.0, 4)
    link_scores = player.get("link_confidences") or [1.0]
    team_score = float(player.get("team_confidence") or 0.0)
    player["confidence_score"] = round(min(_mean(link_scores) or 1.0, max(team_score, 0.35)), 4)
    player["confidence"] = confidence_level(float(player["confidence_score"]))
    player["jersey_color_hex"] = _rgb_to_hex(player.get("appearance_rgb"))
    player["trajectory_m"] = _downsample_trajectory(positions)
    player["overlay_positions"] = [
        {
            "frame": int(position.get("frame") or 0),
            "time_sec": round(float(position.get("time_sec") or 0), 3),
            "bbox_xyxy": position.get("bbox_xyxy"),
            "tracklet_id": position.get("stable_tracklet_id"),
            "confidence": position.get("confidence"),
            "source": position.get("source") or "detected",
        }
        for position in positions
        if position.get("bbox_xyxy") is not None
    ]
    player.pop("positions", None)
    player.pop("link_confidences", None)


def _positions_with_short_gap_interpolation(positions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    stats = {
        "interpolated_positions_count": 0,
        "interpolated_gaps_count": 0,
        "skipped_interpolation_gaps_count": 0,
        "longest_interpolated_gap_frames": 0,
    }
    if not positions:
        return [], stats

    interpolated: list[dict[str, Any]] = []
    detected = [dict(position, source=position.get("source") or "detected") for position in positions]
    for index, current in enumerate(detected):
        interpolated.append(current)
        if index >= len(detected) - 1:
            continue
        following = detected[index + 1]
        frame_gap = int(following.get("frame") or 0) - int(current.get("frame") or 0)
        missing_frames = frame_gap - 1
        if missing_frames <= 0:
            continue
        if not _can_interpolate_gap(current, following, missing_frames=missing_frames):
            stats["skipped_interpolation_gaps_count"] += 1
            continue
        stats["interpolated_gaps_count"] += 1
        stats["interpolated_positions_count"] += missing_frames
        stats["longest_interpolated_gap_frames"] = max(stats["longest_interpolated_gap_frames"], missing_frames)
        for offset in range(1, frame_gap):
            ratio = offset / frame_gap
            interpolated.append(_interpolate_position(current, following, offset=offset, ratio=ratio))
    return interpolated, stats


def _can_interpolate_gap(current: dict[str, Any], following: dict[str, Any], *, missing_frames: int) -> bool:
    if missing_frames > MAX_INTERPOLATION_GAP_FRAMES:
        return False
    time_gap = float(following.get("time_sec") or 0.0) - float(current.get("time_sec") or 0.0)
    if time_gap <= 0 or time_gap > MAX_INTERPOLATION_GAP_SEC:
        return False
    distance = _distance_m(_position_pitch_point(current), _position_pitch_point(following))
    if distance is None or distance / max(time_gap, 0.001) > MAX_INTERPOLATION_SPEED_MPS:
        return False
    if not _valid_bbox(current.get("bbox_xyxy")) or not _valid_bbox(following.get("bbox_xyxy")):
        return False
    return _bbox_shape_is_close(current["bbox_xyxy"], following["bbox_xyxy"])


def _position_pitch_point(position: dict[str, Any]) -> list[float] | None:
    point = position.get("smoothed_pitch_m") or position.get("pitch_m")
    return [float(point[0]), float(point[1])] if point and len(point) >= 2 else None


def _valid_bbox(bbox_xyxy: Any) -> bool:
    if not bbox_xyxy or len(bbox_xyxy) != 4:
        return False
    x1, y1, x2, y2 = [float(value) for value in bbox_xyxy]
    return x2 > x1 and y2 > y1


def _bbox_shape_is_close(a: list[float], b: list[float], *, max_ratio: float = 2.4) -> bool:
    aw = max(1.0, float(a[2]) - float(a[0]))
    ah = max(1.0, float(a[3]) - float(a[1]))
    bw = max(1.0, float(b[2]) - float(b[0]))
    bh = max(1.0, float(b[3]) - float(b[1]))
    return max(aw / bw, bw / aw) <= max_ratio and max(ah / bh, bh / ah) <= max_ratio


def _interpolate_position(current: dict[str, Any], following: dict[str, Any], *, offset: int, ratio: float) -> dict[str, Any]:
    current_frame = int(current.get("frame") or 0)
    current_time = float(current.get("time_sec") or 0.0)
    following_time = float(following.get("time_sec") or current_time)
    current_pitch = _position_pitch_point(current)
    following_pitch = _position_pitch_point(following)
    current_tracklet_id = current.get("stable_tracklet_id")
    following_tracklet_id = following.get("stable_tracklet_id")
    interpolated_tracklet_id = current_tracklet_id if current_tracklet_id == following_tracklet_id else f"{current_tracklet_id}->{following_tracklet_id}"
    return {
        "frame": current_frame + offset,
        "time_sec": round(_lerp(current_time, following_time, ratio), 3),
        "bbox_xyxy": [int(round(_lerp(float(current["bbox_xyxy"][idx]), float(following["bbox_xyxy"][idx]), ratio))) for idx in range(4)],
        "footpoint": _interpolate_optional_point(current.get("footpoint"), following.get("footpoint"), ratio, digits=2),
        "pitch_m": _round_point(_interpolate_optional_point(current_pitch, following_pitch, ratio, digits=4)),
        "smoothed_pitch_m": _round_point(_interpolate_optional_point(current_pitch, following_pitch, ratio, digits=4)),
        "stable_tracklet_id": interpolated_tracklet_id,
        "source": "interpolated",
        "confidence": round(min(float(current.get("confidence") or 0.0), float(following.get("confidence") or 0.0), 0.5), 4),
        "interpolated_from_frame": current_frame,
        "interpolated_to_frame": int(following.get("frame") or current_frame),
        "interpolation_ratio": round(ratio, 3),
    }


def _interpolate_optional_point(a: Any, b: Any, ratio: float, *, digits: int) -> list[float] | None:
    if not a or not b or len(a) < 2 or len(b) < 2:
        return None
    return [round(_lerp(float(a[0]), float(b[0]), ratio), digits), round(_lerp(float(a[1]), float(b[1]), ratio), digits)]


def _lerp(a: float, b: float, ratio: float) -> float:
    return a + (b - a) * ratio


def _downsample_trajectory(positions: list[dict[str, Any]], max_points: int = 180) -> list[dict[str, Any]]:
    if not positions:
        return []
    indices = _sample_indices(len(positions), min(max_points, len(positions)))
    trajectory = []
    for index in indices:
        position = positions[index]
        point = position.get("smoothed_pitch_m") or position.get("pitch_m")
        if not point:
            continue
        trajectory.append(
            {
                "frame": int(position.get("frame") or 0),
                "time_sec": round(float(position.get("time_sec") or 0), 3),
                "pitch_m": _round_point(point),
                "source": position.get("source") or "detected",
            }
        )
    return trajectory


def renumber_stable_players(players: list[dict[str, Any]]) -> None:
    counters: dict[str, int] = {}
    for player in sorted(players, key=lambda item: (str(item.get("team_label") or "U"), -float(item.get("duration_sec") or 0), str(item.get("stable_subject_id")))):
        label = str(player.get("team_label") or "U")
        if label not in {"A", "B"}:
            label = "U"
        counters[label] = counters.get(label, 0) + 1
        player["team_label"] = label
        player["stable_player_id"] = f"{label}{counters[label]:02d}"


def select_active_stable_players(
    players: list[dict[str, Any]],
    *,
    total_cap: int = DEFAULT_ACTIVE_PLAYERS_CAP,
    per_team_cap: int = DEFAULT_ACTIVE_PLAYERS_PER_TEAM_CAP,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if total_cap <= 0 or len(players) <= total_cap:
        return list(players), []

    ranked = sorted(players, key=_stable_player_rank, reverse=True)
    active_subject_ids: set[str] = set()
    team_counts: dict[str, int] = {"A": 0, "B": 0}
    for player in ranked:
        label = str(player.get("team_label") or "U")
        if label in {"A", "B"} and team_counts[label] >= per_team_cap:
            continue
        active_subject_ids.add(str(player.get("stable_subject_id")))
        if label in {"A", "B"}:
            team_counts[label] += 1
        if len(active_subject_ids) >= total_cap:
            break

    if len(active_subject_ids) < total_cap:
        for player in ranked:
            subject_id = str(player.get("stable_subject_id"))
            if subject_id in active_subject_ids:
                continue
            active_subject_ids.add(subject_id)
            if len(active_subject_ids) >= total_cap:
                break

    active: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    for player in players:
        if str(player.get("stable_subject_id")) in active_subject_ids:
            active.append(player)
        else:
            suppressed.append(
                {
                    **player,
                    "status": "suppressed_extra_candidate",
                    "suppress_reason": "active_players_cap",
                }
            )
    return active, suppressed


def _stable_player_rank(player: dict[str, Any]) -> tuple[float, int, float, float]:
    return (
        float(player.get("duration_sec") or 0.0),
        int(player.get("positions_count") or 0),
        float(player.get("confidence_score") or 0.0),
        float(player.get("mean_detection_confidence") or 0.0),
    )


def build_stable_players_document(
    *,
    stable_players: list[dict[str, Any]],
    raw_tracks_count: int,
    tracklets_count: int,
    rejected_tracklets: list[dict[str, Any]],
    pitch_width_m: float,
    pitch_length_m: float,
) -> dict[str, Any]:
    active_players, suppressed_candidates = select_active_stable_players(stable_players)
    return {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "pitch_dimensions_m": {"width_m": pitch_width_m, "length_m": pitch_length_m},
        "players": sorted(active_players, key=lambda item: item["stable_player_id"]),
        "suppressed_candidates": sorted(suppressed_candidates, key=lambda item: item["stable_player_id"]),
        "summary": {
            "raw_tracks": raw_tracks_count,
            "clean_tracklets": tracklets_count,
            "rejected_tracklets": len(rejected_tracklets),
            "stable_players": len(active_players),
            "stable_player_candidates": len(stable_players),
            "suppressed_extra_candidates": len(suppressed_candidates),
            "team_counts": _team_counts(active_players),
            "risky_links": sum(len(player.get("risky_links") or []) for player in active_players),
            "low_confidence_players": sum(1 for player in active_players if player.get("confidence") == "low"),
            "interpolated_frames": sum(int(player.get("interpolated_positions_count") or 0) for player in active_players),
            "interpolated_gaps": sum(int(player.get("interpolated_gaps_count") or 0) for player in active_players),
            "skipped_interpolation_gaps": sum(int(player.get("skipped_interpolation_gaps_count") or 0) for player in active_players),
            "players_with_interpolation": sum(1 for player in active_players if int(player.get("interpolated_positions_count") or 0) > 0),
            "longest_interpolated_gap_frames": max((int(player.get("longest_interpolated_gap_frames") or 0) for player in active_players), default=0),
        },
    }


def build_frame_detection_counts(
    tracks: list[dict[str, Any]],
    stable_doc: dict[str, Any],
    *,
    fps: float,
    target_players: int = DEFAULT_ACTIVE_PLAYERS_CAP,
) -> dict[str, Any]:
    raw_counts: dict[int, int] = defaultdict(int)
    for track in tracks:
        for position in track.get("positions") or []:
            if position.get("frame") is not None:
                raw_counts[int(position["frame"])] += 1

    stable_detected_counts: dict[int, int] = defaultdict(int)
    stable_interpolated_counts: dict[int, int] = defaultdict(int)
    for player in stable_doc.get("players", []):
        for position in player.get("overlay_positions") or []:
            if position.get("frame") is None:
                continue
            frame = int(position["frame"])
            if position.get("source") == "interpolated":
                stable_interpolated_counts[frame] += 1
            else:
                stable_detected_counts[frame] += 1

    max_frame = max(
        [0]
        + list(raw_counts.keys())
        + list(stable_detected_counts.keys())
        + list(stable_interpolated_counts.keys())
    )
    frames = []
    for frame in range(max_frame + 1):
        raw = raw_counts.get(frame, 0)
        stable_detected = stable_detected_counts.get(frame, 0)
        stable_interpolated = stable_interpolated_counts.get(frame, 0)
        stable_total = stable_detected + stable_interpolated
        frames.append(
            {
                "frame": frame,
                "time_sec": round(frame / max(fps, 0.001), 3),
                "raw_detections": raw,
                "stable_detected": stable_detected,
                "stable_interpolated": stable_interpolated,
                "stable_total": stable_total,
                "raw_missing_vs_target": max(0, target_players - raw),
                "stable_missing_vs_target": max(0, target_players - stable_total),
                "raw_extra_vs_target": max(0, raw - target_players),
            }
        )

    raw_values = [frame["raw_detections"] for frame in frames]
    stable_values = [frame["stable_total"] for frame in frames]
    return {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "target_players": target_players,
        "summary": {
            "frames": len(frames),
            "raw_min": min(raw_values) if raw_values else 0,
            "raw_max": max(raw_values) if raw_values else 0,
            "raw_avg": round(_mean([float(value) for value in raw_values]) or 0.0, 3),
            "stable_min": min(stable_values) if stable_values else 0,
            "stable_max": max(stable_values) if stable_values else 0,
            "stable_avg": round(_mean([float(value) for value in stable_values]) or 0.0, 3),
            "raw_frames_below_target": sum(1 for value in raw_values if value < target_players),
            "stable_frames_below_target": sum(1 for value in stable_values if value < target_players),
            "raw_frames_at_or_above_target": sum(1 for value in raw_values if value >= target_players),
            "stable_frames_at_or_above_target": sum(1 for value in stable_values if value >= target_players),
        },
        "frames": frames,
    }


def _team_counts(players: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for player in players:
        label = str(player.get("team_label") or "U")
        counts[label] = counts.get(label, 0) + 1
    return counts


def build_stabilization_report(
    *,
    stable_doc: dict[str, Any],
    rejected_tracklets: list[dict[str, Any]],
    team_clusters: dict[str, Any],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "status": "completed",
        "parameters": parameters,
        "summary": stable_doc["summary"],
        "frame_detection_summary": stable_doc.get("frame_detection_summary"),
        "movement_stats_summary": stable_doc.get("movement_stats_summary"),
        "team_clusters_summary": [
            {
                "cluster_id": cluster.get("cluster_id"),
                "team_label": cluster.get("team_label"),
                "team_name": cluster.get("team_name"),
                "tracklets_count": cluster.get("tracklets_count"),
                "confidence": cluster.get("confidence"),
                "color_hex": cluster.get("color_hex"),
            }
            for cluster in team_clusters.get("clusters", [])
        ],
        "rejected_tracklets": [
            {
                "tracklet_id": item.get("tracklet_id"),
                "source_track_id": item.get("source_track_id"),
                "duration_sec": item.get("duration_sec"),
                "positions_count": item.get("positions_count"),
                "reject_reason": item.get("reject_reason"),
            }
            for item in rejected_tracklets[:500]
        ],
        "risky_links": [
            {"stable_player_id": player["stable_player_id"], **link}
            for player in stable_doc.get("players", [])
            for link in (player.get("risky_links") or [])
        ],
        "suppressed_extra_candidates": [
            {
                "stable_player_id": player.get("stable_player_id"),
                "team_label": player.get("team_label"),
                "duration_sec": player.get("duration_sec"),
                "positions_count": player.get("positions_count"),
                "tracklet_count": player.get("tracklet_count"),
                "confidence": player.get("confidence"),
                "mean_detection_confidence": player.get("mean_detection_confidence"),
                "suppress_reason": player.get("suppress_reason"),
            }
            for player in stable_doc.get("suppressed_candidates", [])
        ],
        "interpolation": [
            {
                "stable_player_id": player["stable_player_id"],
                "interpolated_frames": player.get("interpolated_positions_count"),
                "interpolated_gaps": player.get("interpolated_gaps_count"),
                "skipped_gaps": player.get("skipped_interpolation_gaps_count"),
                "longest_gap_frames": player.get("longest_interpolated_gap_frames"),
            }
            for player in stable_doc.get("players", [])
            if int(player.get("interpolated_positions_count") or 0) > 0
        ],
    }


def build_movement_stats_document(stable_doc: dict[str, Any]) -> dict[str, Any]:
    players = []
    for player in stable_doc.get("players", []):
        stats = player.get("movement_stats") or {}
        players.append(
            {
                "stable_player_id": player.get("stable_player_id"),
                "stable_subject_id": player.get("stable_subject_id"),
                "slot_id": player.get("slot_id"),
                "team_label": player.get("team_label"),
                "team_id": player.get("team_id"),
                "team_name": player.get("team_name"),
                "confidence": player.get("confidence"),
                "confidence_score": player.get("confidence_score"),
                "movement_stats": stats,
            }
        )
    summary = {
        "players": len(players),
        "total_distance_m": round(sum(float((item.get("movement_stats") or {}).get("total_distance_m") or 0.0) for item in players), 2),
        "observed_distance_m": round(sum(float((item.get("movement_stats") or {}).get("observed_distance_m") or 0.0) for item in players), 2),
        "estimated_gap_distance_m": round(sum(float((item.get("movement_stats") or {}).get("estimated_gap_distance_m") or 0.0) for item in players), 2),
        "players_with_estimated_distance": sum(
            1 for item in players if float((item.get("movement_stats") or {}).get("estimated_gap_distance_m") or 0.0) > 0.0
        ),
        "players_low_quality": sum(
            1 for item in players if (item.get("movement_stats") or {}).get("distance_quality") == "low"
        ),
        "players_medium_quality": sum(
            1 for item in players if (item.get("movement_stats") or {}).get("distance_quality") == "medium"
        ),
        "players_high_quality": sum(
            1 for item in players if (item.get("movement_stats") or {}).get("distance_quality") == "high"
        ),
        "peak_sustained_speed_kmh": round(
            max(
                [
                    float(
                        (item.get("movement_stats") or {}).get("peak_sustained_speed_kmh")
                        or (item.get("movement_stats") or {}).get("top_speed_kmh")
                        or 0.0
                    )
                    for item in players
                ]
                or [0.0]
            ),
            2,
        ),
        "high_intensity_time_sec": round(sum(float(((item.get("movement_stats") or {}).get("intensity") or {}).get("high_intensity_time_sec") or 0.0) for item in players), 2),
        "high_intensity_distance_m": round(sum(float(((item.get("movement_stats") or {}).get("intensity") or {}).get("high_intensity_distance_m") or 0.0) for item in players), 2),
        "sprint_count": sum(int(((item.get("movement_stats") or {}).get("intensity") or {}).get("sprint_count") or 0) for item in players),
        "sprint_time_sec": round(sum(float(((item.get("movement_stats") or {}).get("intensity") or {}).get("sprint_time_sec") or 0.0) for item in players), 2),
        "sprint_distance_m": round(sum(float(((item.get("movement_stats") or {}).get("intensity") or {}).get("sprint_distance_m") or 0.0) for item in players), 2),
        "max_sprint_speed_kmh": round(max([float(((item.get("movement_stats") or {}).get("intensity") or {}).get("max_sprint_speed_kmh") or 0.0) for item in players] or [0.0]), 2),
        "sprint_candidate_count": sum(int(((item.get("movement_stats") or {}).get("intensity") or {}).get("sprint_candidate_count") or 0) for item in players),
        "rejected_sprint_candidate_count": sum(int(((item.get("movement_stats") or {}).get("intensity") or {}).get("rejected_sprint_candidate_count") or 0) for item in players),
        "best_sprint_candidate_speed_kmh": round(max([float(((item.get("movement_stats") or {}).get("intensity") or {}).get("best_sprint_candidate_speed_kmh") or 0.0) for item in players] or [0.0]), 2),
        "best_sprint_candidate_duration_sec": round(max([float(((item.get("movement_stats") or {}).get("intensity") or {}).get("best_sprint_candidate_duration_sec") or 0.0) for item in players] or [0.0]), 3),
    }
    summary["best_rejected_sprint_candidate"] = _best_sprint_candidate_from_rows(
        [
            ((item.get("movement_stats") or {}).get("intensity") or {}).get("best_rejected_sprint_candidate")
            for item in players
        ]
    )
    summary["top_speed_kmh"] = summary["peak_sustained_speed_kmh"]
    return {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "source": stable_doc.get("source") or "conservative_identity_v2",
        "identity_semantics": stable_doc.get("identity_semantics") or "stint_first",
        "units": {
            "distance": "meters",
            "speed": "mps_and_kmh",
            "time": "seconds",
            "intensity": "counts_seconds_meters_kmh",
        },
        "summary": summary,
        "players": sorted(players, key=lambda item: str(item.get("stable_player_id") or "")),
    }


def build_player_stats_document(stable_doc: dict[str, Any]) -> dict[str, Any]:
    player_rows = []
    team_rows: dict[str, dict[str, Any]] = {}
    for player in stable_doc.get("players", []):
        stats = player.get("movement_stats") or {}
        team_label = str(player.get("team_label") or "U")
        row = {
            "stable_player_id": player.get("stable_player_id"),
            "stable_subject_id": player.get("stable_subject_id"),
            "slot_id": player.get("slot_id"),
            "identity_semantics": player.get("identity_semantics") or stable_doc.get("identity_semantics") or "stint_first",
            "status": player.get("status") or "active",
            "team_label": team_label,
            "team_id": player.get("team_id"),
            "team_name": player.get("team_name"),
            "confidence": player.get("confidence"),
            "confidence_score": player.get("confidence_score"),
            "tracklet_ids": player.get("tracklet_ids") or [],
            "raw_track_ids": player.get("raw_track_ids") or [],
            "stint_count": int(player.get("stint_count") or 0),
            "time": {
                "playing_time_sec": _stats_float(stats, "playing_time_sec"),
                "detected_time_sec": _stats_float(stats, "detected_time_sec"),
                "missing_time_sec": _stats_float(stats, "missing_time_sec"),
                "ambiguous_time_sec": _stats_float(stats, "ambiguous_time_sec"),
            },
            "distance": {
                "observed_distance_m": _stats_float(stats, "observed_distance_m"),
                "estimated_short_gap_distance_m": _stats_float(stats, "estimated_gap_distance_m"),
                "total_distance_m": _stats_float(stats, "total_distance_m"),
                "estimated_distance_ratio": _stats_float(stats, "estimated_distance_ratio"),
                "quality": stats.get("distance_quality") or "unknown",
            },
            "speed": {
                "avg_speed_mps": _stats_float(stats, "avg_speed_mps"),
                "avg_speed_kmh": _stats_float(stats, "avg_speed_kmh"),
                "observed_avg_speed_mps": _stats_float(stats, "observed_avg_speed_mps"),
                "peak_sustained_speed_mps": _stats_float(stats, "peak_sustained_speed_mps", _stats_float(stats, "top_speed_mps")),
                "peak_sustained_speed_kmh": _stats_float(stats, "peak_sustained_speed_kmh", _stats_float(stats, "top_speed_kmh")),
                "top_speed_mps": _stats_float(stats, "top_speed_mps"),
                "top_speed_kmh": _stats_float(stats, "top_speed_kmh"),
                "raw_segment_top_speed_mps": _stats_float(stats, "raw_segment_top_speed_mps"),
                "raw_segment_top_speed_kmh": _stats_float(stats, "raw_segment_top_speed_kmh"),
                "quality": stats.get("speed_quality") or "unknown",
            },
            "intensity": {
                "high_intensity_threshold_kmh": _stats_nested_float(stats, "intensity", "high_intensity_threshold_kmh"),
                "sprint_threshold_kmh": _stats_nested_float(stats, "intensity", "sprint_threshold_kmh"),
                "min_sprint_duration_sec": _stats_nested_float(stats, "intensity", "min_sprint_duration_sec"),
                "high_intensity_time_sec": _stats_nested_float(stats, "intensity", "high_intensity_time_sec"),
                "high_intensity_distance_m": _stats_nested_float(stats, "intensity", "high_intensity_distance_m"),
                "high_intensity_segments": _stats_nested_int(stats, "intensity", "high_intensity_segments"),
                "high_intensity_distance_ratio": _stats_nested_float(stats, "intensity", "high_intensity_distance_ratio"),
                "sprint_count": _stats_nested_int(stats, "intensity", "sprint_count"),
                "sprint_time_sec": _stats_nested_float(stats, "intensity", "sprint_time_sec"),
                "sprint_distance_m": _stats_nested_float(stats, "intensity", "sprint_distance_m"),
                "sprint_distance_ratio": _stats_nested_float(stats, "intensity", "sprint_distance_ratio"),
                "longest_sprint_time_sec": _stats_nested_float(stats, "intensity", "longest_sprint_time_sec"),
                "longest_sprint_distance_m": _stats_nested_float(stats, "intensity", "longest_sprint_distance_m"),
                "max_sprint_speed_kmh": _stats_nested_float(stats, "intensity", "max_sprint_speed_kmh"),
                "trusted_speed_segments": _stats_nested_int(stats, "intensity", "trusted_speed_segments"),
                "sprint_candidate_count": _stats_nested_int(stats, "intensity", "sprint_candidate_count"),
                "rejected_sprint_candidate_count": _stats_nested_int(stats, "intensity", "rejected_sprint_candidate_count"),
                "best_sprint_candidate_speed_kmh": _stats_nested_float(stats, "intensity", "best_sprint_candidate_speed_kmh"),
                "best_sprint_candidate_duration_sec": _stats_nested_float(stats, "intensity", "best_sprint_candidate_duration_sec"),
                "best_sprint_candidate_distance_m": _stats_nested_float(stats, "intensity", "best_sprint_candidate_distance_m"),
                "best_sprint_candidate_reason": str(_stats_record(stats, "intensity").get("best_sprint_candidate_reason") or "none"),
                "best_rejected_sprint_candidate": _stats_nested_record(stats, "intensity", "best_rejected_sprint_candidate"),
                "rejected_sprint_candidates": _stats_record(stats, "intensity").get("rejected_sprint_candidates") or [],
            },
            "frames": {
                "active_frames": int(stats.get("active_frames") or 0),
                "detected_frames": int(stats.get("detected_frames") or 0),
                "missing_frames": int(stats.get("missing_frames") or 0),
                "ambiguous_frames": int(stats.get("ambiguous_frames") or 0),
                "predicted_frames": int(stats.get("predicted_frames") or 0),
                "samples_used": int(stats.get("samples_used") or 0),
            },
            "segments": {
                "observed_segments": int(stats.get("observed_segments") or 0),
                "estimated_gap_segments": int(stats.get("estimated_gap_segments") or 0),
                "skipped_outlier_segments": int(stats.get("skipped_outlier_segments") or 0),
                "skipped_speed_outlier_segments": int(stats.get("skipped_speed_outlier_segments") or 0),
                "skipped_long_gap_segments": int(stats.get("skipped_long_gap_segments") or 0),
                "sustained_speed_windows": int(stats.get("sustained_speed_windows") or 0),
            },
            "tracking_only": True,
            "stats_note": stats.get("stats_note")
            or "tracking-only stats from stable slot/stint positions; ball events are not included",
        }
        player_rows.append(row)

        team_row = team_rows.setdefault(
            team_label,
            {
                "team_label": team_label,
                "players": 0,
                "playing_time_sec": 0.0,
                "detected_time_sec": 0.0,
                "missing_time_sec": 0.0,
                "ambiguous_time_sec": 0.0,
                "total_distance_m": 0.0,
                "observed_distance_m": 0.0,
                "estimated_short_gap_distance_m": 0.0,
                "peak_sustained_speed_kmh": 0.0,
                "top_speed_kmh": 0.0,
                "high_intensity_time_sec": 0.0,
                "high_intensity_distance_m": 0.0,
                "sprint_count": 0,
                "sprint_time_sec": 0.0,
                "sprint_distance_m": 0.0,
                "longest_sprint_distance_m": 0.0,
                "max_sprint_speed_kmh": 0.0,
                "sprint_candidate_count": 0,
                "rejected_sprint_candidate_count": 0,
                "best_sprint_candidate_speed_kmh": 0.0,
                "best_sprint_candidate_duration_sec": 0.0,
                "best_rejected_sprint_candidate": {},
                "players_low_quality": 0,
                "players_medium_quality": 0,
                "players_high_quality": 0,
            },
        )
        team_row["players"] += 1
        team_row["playing_time_sec"] += row["time"]["playing_time_sec"]
        team_row["detected_time_sec"] += row["time"]["detected_time_sec"]
        team_row["missing_time_sec"] += row["time"]["missing_time_sec"]
        team_row["ambiguous_time_sec"] += row["time"]["ambiguous_time_sec"]
        team_row["total_distance_m"] += row["distance"]["total_distance_m"]
        team_row["observed_distance_m"] += row["distance"]["observed_distance_m"]
        team_row["estimated_short_gap_distance_m"] += row["distance"]["estimated_short_gap_distance_m"]
        team_row["high_intensity_time_sec"] += row["intensity"]["high_intensity_time_sec"]
        team_row["high_intensity_distance_m"] += row["intensity"]["high_intensity_distance_m"]
        team_row["sprint_count"] += row["intensity"]["sprint_count"]
        team_row["sprint_time_sec"] += row["intensity"]["sprint_time_sec"]
        team_row["sprint_distance_m"] += row["intensity"]["sprint_distance_m"]
        team_row["sprint_candidate_count"] += row["intensity"]["sprint_candidate_count"]
        team_row["rejected_sprint_candidate_count"] += row["intensity"]["rejected_sprint_candidate_count"]
        team_row["best_sprint_candidate_speed_kmh"] = max(
            float(team_row["best_sprint_candidate_speed_kmh"]),
            row["intensity"]["best_sprint_candidate_speed_kmh"],
        )
        team_row["best_sprint_candidate_duration_sec"] = max(
            float(team_row["best_sprint_candidate_duration_sec"]),
            row["intensity"]["best_sprint_candidate_duration_sec"],
        )
        team_row["best_rejected_sprint_candidate"] = _best_sprint_candidate_from_rows(
            [
                team_row.get("best_rejected_sprint_candidate") or {},
                row["intensity"].get("best_rejected_sprint_candidate") or {},
            ]
        )
        team_row["longest_sprint_distance_m"] = max(
            float(team_row["longest_sprint_distance_m"]),
            row["intensity"]["longest_sprint_distance_m"],
        )
        team_row["max_sprint_speed_kmh"] = max(
            float(team_row["max_sprint_speed_kmh"]),
            row["intensity"]["max_sprint_speed_kmh"],
        )
        team_row["peak_sustained_speed_kmh"] = max(
            float(team_row["peak_sustained_speed_kmh"]),
            row["speed"]["peak_sustained_speed_kmh"],
        )
        team_row["top_speed_kmh"] = team_row["peak_sustained_speed_kmh"]
        quality_key = f"players_{row['distance']['quality']}_quality"
        if quality_key in team_row:
            team_row[quality_key] += 1

    for team_row in team_rows.values():
        for key in [
            "playing_time_sec",
            "detected_time_sec",
            "missing_time_sec",
            "ambiguous_time_sec",
            "total_distance_m",
            "observed_distance_m",
            "estimated_short_gap_distance_m",
            "peak_sustained_speed_kmh",
            "top_speed_kmh",
            "high_intensity_time_sec",
            "high_intensity_distance_m",
            "sprint_time_sec",
            "sprint_distance_m",
            "longest_sprint_distance_m",
            "max_sprint_speed_kmh",
            "best_sprint_candidate_speed_kmh",
            "best_sprint_candidate_duration_sec",
        ]:
            team_row[key] = round(float(team_row[key]), 2)

    summary = {
        "players": len(player_rows),
        "team_counts": _team_counts(stable_doc.get("players", [])),
        "total_distance_m": round(sum(row["distance"]["total_distance_m"] for row in player_rows), 2),
        "observed_distance_m": round(sum(row["distance"]["observed_distance_m"] for row in player_rows), 2),
        "estimated_short_gap_distance_m": round(
            sum(row["distance"]["estimated_short_gap_distance_m"] for row in player_rows),
            2,
        ),
        "playing_time_sec": round(sum(row["time"]["playing_time_sec"] for row in player_rows), 2),
        "detected_time_sec": round(sum(row["time"]["detected_time_sec"] for row in player_rows), 2),
        "missing_time_sec": round(sum(row["time"]["missing_time_sec"] for row in player_rows), 2),
        "ambiguous_time_sec": round(sum(row["time"]["ambiguous_time_sec"] for row in player_rows), 2),
        "peak_sustained_speed_kmh": round(
            max([row["speed"]["peak_sustained_speed_kmh"] for row in player_rows] or [0.0]),
            2,
        ),
        "top_speed_kmh": round(max([row["speed"]["top_speed_kmh"] for row in player_rows] or [0.0]), 2),
        "high_intensity_time_sec": round(sum(row["intensity"]["high_intensity_time_sec"] for row in player_rows), 2),
        "high_intensity_distance_m": round(sum(row["intensity"]["high_intensity_distance_m"] for row in player_rows), 2),
        "sprint_count": sum(row["intensity"]["sprint_count"] for row in player_rows),
        "sprint_time_sec": round(sum(row["intensity"]["sprint_time_sec"] for row in player_rows), 2),
        "sprint_distance_m": round(sum(row["intensity"]["sprint_distance_m"] for row in player_rows), 2),
        "longest_sprint_distance_m": round(max([row["intensity"]["longest_sprint_distance_m"] for row in player_rows] or [0.0]), 2),
        "max_sprint_speed_kmh": round(max([row["intensity"]["max_sprint_speed_kmh"] for row in player_rows] or [0.0]), 2),
        "sprint_candidate_count": sum(row["intensity"]["sprint_candidate_count"] for row in player_rows),
        "rejected_sprint_candidate_count": sum(row["intensity"]["rejected_sprint_candidate_count"] for row in player_rows),
        "best_sprint_candidate_speed_kmh": round(max([row["intensity"]["best_sprint_candidate_speed_kmh"] for row in player_rows] or [0.0]), 2),
        "best_sprint_candidate_duration_sec": round(max([row["intensity"]["best_sprint_candidate_duration_sec"] for row in player_rows] or [0.0]), 3),
        "best_rejected_sprint_candidate": _best_sprint_candidate_from_rows(
            [row["intensity"].get("best_rejected_sprint_candidate") or {} for row in player_rows]
        ),
        "players_with_estimated_distance": sum(
            1 for row in player_rows if row["distance"]["estimated_short_gap_distance_m"] > 0
        ),
        "players_low_quality": sum(1 for row in player_rows if row["distance"]["quality"] == "low"),
        "players_medium_quality": sum(1 for row in player_rows if row["distance"]["quality"] == "medium"),
        "players_high_quality": sum(1 for row in player_rows if row["distance"]["quality"] == "high"),
    }
    return {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "source": stable_doc.get("source") or "conservative_identity_v2",
        "identity_semantics": stable_doc.get("identity_semantics") or "stint_first",
        "scope": "tracking_only_no_ball",
        "units": {
            "distance": "meters",
            "speed": "mps_and_kmh",
            "time": "seconds",
            "intensity": "counts_seconds_meters_kmh",
        },
        "summary": summary,
        "teams": sorted(team_rows.values(), key=lambda item: str(item.get("team_label") or "")),
        "players": sorted(player_rows, key=lambda item: str(item.get("stable_player_id") or "")),
    }


def build_team_config_document(
    meta: dict[str, Any],
    team_clusters: dict[str, Any] | None,
    stable_doc: dict[str, Any] | None = None,
    existing_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    teams = meta.get("teams") if isinstance(meta.get("teams"), list) else []
    clusters = team_clusters.get("clusters") if isinstance(team_clusters, dict) and isinstance(team_clusters.get("clusters"), list) else []
    existing_by_label = {
        str(item.get("team_label")): item
        for item in (existing_config or {}).get("teams", [])
        if isinstance(item, dict) and item.get("team_label")
    }
    player_counts = _team_counts(stable_doc.get("players", []) if isinstance(stable_doc, dict) else [])
    config_teams = []
    for index, label in enumerate(["A", "B"]):
        match_team = teams[index] if index < len(teams) and isinstance(teams[index], dict) else {}
        cluster = next((item for item in clusters if item.get("team_label") == label), {})
        existing = existing_by_label.get(label, {})
        locked = bool(existing.get("locked")) if existing else False
        config_teams.append(
            {
                "team_label": label,
                "team_id": existing.get("team_id") if existing.get("team_id") is not None else match_team.get("id"),
                "team_name": existing.get("team_name") or match_team.get("name") or f"Team {label}",
                "display_color": existing.get("display_color") or match_team.get("color"),
                "detected_color_hex": cluster.get("color_hex") or existing.get("detected_color_hex"),
                "cluster_id": cluster.get("cluster_id"),
                "cluster_confidence": cluster.get("confidence"),
                "reference_tracklets_count": cluster.get("reference_tracklets_count", 0),
                "candidate_tracklets_count": cluster.get("candidate_tracklets_count", 0),
                "stable_players_count": player_counts.get(label, 0),
                "locked": locked,
                "assignment_source": existing.get("assignment_source") or ("manual_lock" if locked else "auto_cluster"),
                "goalkeeper_exceptions": existing.get("goalkeeper_exceptions") if isinstance(existing.get("goalkeeper_exceptions"), list) else [],
                "notes": existing.get("notes") or "",
            }
        )
    unknown_count = player_counts.get("U", 0) + player_counts.get("unknown", 0)
    return {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "updated_at": now_iso(),
        "source": "team_clusters_review",
        "match_id": meta.get("id"),
        "team_assignment_semantics": "stable_slot_team_label",
        "locked": any(item["locked"] for item in config_teams),
        "teams": config_teams,
        "unknown_stable_players": unknown_count,
        "team_clusters_method": team_clusters.get("method") if isinstance(team_clusters, dict) else None,
        "team_clusters_summary": {
            "reference_tracklets_count": team_clusters.get("reference_tracklets_count") if isinstance(team_clusters, dict) else None,
            "candidate_tracklets_count": team_clusters.get("candidate_tracklets_count") if isinstance(team_clusters, dict) else None,
            "unknown_tracklets_count": len(team_clusters.get("unknown_tracklets", [])) if isinstance(team_clusters, dict) else 0,
        },
    }


def build_team_stats_document(player_stats: dict[str, Any], team_config: dict[str, Any] | None = None) -> dict[str, Any]:
    config_by_label = {
        str(item.get("team_label")): item
        for item in (team_config or {}).get("teams", [])
        if isinstance(item, dict) and item.get("team_label")
    }
    teams = []
    for team in player_stats.get("teams", []):
        if not isinstance(team, dict):
            continue
        label = str(team.get("team_label") or "U")
        config = config_by_label.get(label, {})
        teams.append(
            {
                "team_label": label,
                "team_id": config.get("team_id"),
                "team_name": config.get("team_name") or f"Team {label}",
                "display_color": config.get("display_color"),
                "detected_color_hex": config.get("detected_color_hex"),
                "locked": bool(config.get("locked")),
                "players": int(team.get("players") or 0),
                "playing_time_sec": round(float(team.get("playing_time_sec") or 0.0), 2),
                "detected_time_sec": round(float(team.get("detected_time_sec") or 0.0), 2),
                "missing_time_sec": round(float(team.get("missing_time_sec") or 0.0), 2),
                "ambiguous_time_sec": round(float(team.get("ambiguous_time_sec") or 0.0), 2),
                "total_distance_m": round(float(team.get("total_distance_m") or 0.0), 2),
                "observed_distance_m": round(float(team.get("observed_distance_m") or 0.0), 2),
                "estimated_short_gap_distance_m": round(float(team.get("estimated_short_gap_distance_m") or 0.0), 2),
                "high_intensity_time_sec": round(float(team.get("high_intensity_time_sec") or 0.0), 2),
                "high_intensity_distance_m": round(float(team.get("high_intensity_distance_m") or 0.0), 2),
                "sprint_count": int(team.get("sprint_count") or 0),
                "sprint_time_sec": round(float(team.get("sprint_time_sec") or 0.0), 2),
                "sprint_distance_m": round(float(team.get("sprint_distance_m") or 0.0), 2),
                "longest_sprint_distance_m": round(float(team.get("longest_sprint_distance_m") or 0.0), 2),
                "max_sprint_speed_kmh": round(float(team.get("max_sprint_speed_kmh") or 0.0), 2),
                "sprint_candidate_count": int(team.get("sprint_candidate_count") or 0),
                "rejected_sprint_candidate_count": int(team.get("rejected_sprint_candidate_count") or 0),
                "best_sprint_candidate_speed_kmh": round(float(team.get("best_sprint_candidate_speed_kmh") or 0.0), 2),
                "best_sprint_candidate_duration_sec": round(float(team.get("best_sprint_candidate_duration_sec") or 0.0), 3),
                "best_rejected_sprint_candidate": team.get("best_rejected_sprint_candidate") or {},
                "peak_sustained_speed_kmh": round(
                    float(team.get("peak_sustained_speed_kmh") or team.get("top_speed_kmh") or 0.0),
                    2,
                ),
                "top_speed_kmh": round(
                    float(team.get("peak_sustained_speed_kmh") or team.get("top_speed_kmh") or 0.0),
                    2,
                ),
                "players_low_quality": int(team.get("players_low_quality") or 0),
                "players_medium_quality": int(team.get("players_medium_quality") or 0),
                "players_high_quality": int(team.get("players_high_quality") or 0),
            }
        )
    return {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "source": player_stats.get("source") or "player_stats",
        "scope": player_stats.get("scope") or "tracking_only_no_ball",
        "units": player_stats.get("units") or {},
        "summary": {
            "teams": len(teams),
            "players": sum(int(team.get("players") or 0) for team in teams),
            "total_distance_m": round(sum(float(team.get("total_distance_m") or 0.0) for team in teams), 2),
            "observed_distance_m": round(sum(float(team.get("observed_distance_m") or 0.0) for team in teams), 2),
            "estimated_short_gap_distance_m": round(
                sum(float(team.get("estimated_short_gap_distance_m") or 0.0) for team in teams),
                2,
            ),
            "peak_sustained_speed_kmh": round(
                max([float(team.get("peak_sustained_speed_kmh") or team.get("top_speed_kmh") or 0.0) for team in teams] or [0.0]),
                2,
            ),
            "top_speed_kmh": round(max([float(team.get("top_speed_kmh") or 0.0) for team in teams] or [0.0]), 2),
            "high_intensity_time_sec": round(sum(float(team.get("high_intensity_time_sec") or 0.0) for team in teams), 2),
            "high_intensity_distance_m": round(sum(float(team.get("high_intensity_distance_m") or 0.0) for team in teams), 2),
            "sprint_count": sum(int(team.get("sprint_count") or 0) for team in teams),
            "sprint_time_sec": round(sum(float(team.get("sprint_time_sec") or 0.0) for team in teams), 2),
            "sprint_distance_m": round(sum(float(team.get("sprint_distance_m") or 0.0) for team in teams), 2),
            "longest_sprint_distance_m": round(max([float(team.get("longest_sprint_distance_m") or 0.0) for team in teams] or [0.0]), 2),
            "max_sprint_speed_kmh": round(max([float(team.get("max_sprint_speed_kmh") or 0.0) for team in teams] or [0.0]), 2),
            "sprint_candidate_count": sum(int(team.get("sprint_candidate_count") or 0) for team in teams),
            "rejected_sprint_candidate_count": sum(int(team.get("rejected_sprint_candidate_count") or 0) for team in teams),
            "best_sprint_candidate_speed_kmh": round(max([float(team.get("best_sprint_candidate_speed_kmh") or 0.0) for team in teams] or [0.0]), 2),
            "best_sprint_candidate_duration_sec": round(max([float(team.get("best_sprint_candidate_duration_sec") or 0.0) for team in teams] or [0.0]), 3),
            "best_rejected_sprint_candidate": _best_sprint_candidate_from_rows(
                [team.get("best_rejected_sprint_candidate") or {} for team in teams]
            ),
            "tracking_only": True,
        },
        "teams": sorted(teams, key=lambda item: str(item.get("team_label") or "")),
    }


def build_player_heatmaps_document(
    stable_doc: dict[str, Any],
    match_dir: Path,
    *,
    width_px: int = 360,
    length_px: int = 720,
) -> dict[str, Any]:
    heatmap_dir = match_dir / "player_heatmaps"
    heatmap_dir.mkdir(parents=True, exist_ok=True)
    pitch = stable_doc.get("pitch_dimensions_m") if isinstance(stable_doc.get("pitch_dimensions_m"), dict) else {}
    pitch_width_m = float(pitch.get("width_m") or 30.0)
    pitch_length_m = float(pitch.get("length_m") or 47.4)
    rows = []
    total_samples = 0
    detected_samples_total = 0
    interpolated_samples_total = 0
    for player in stable_doc.get("players", []):
        if not isinstance(player, dict):
            continue
        heatmap_rows = _player_heatmap_rows(player)
        stable_subject_id = str(player.get("stable_subject_id") or player.get("stable_player_id") or "player")
        stable_player_id = str(player.get("stable_player_id") or stable_subject_id)
        filename = f"heatmap_{_safe_artifact_id(stable_subject_id)}.png"
        relative_path = f"player_heatmaps/{filename}"
        output_path = heatmap_dir / filename
        _write_player_heatmap_png(
            output_path,
            heatmap_rows,
            pitch_width_m=pitch_width_m,
            pitch_length_m=pitch_length_m,
            width_px=width_px,
            length_px=length_px,
        )
        detected_samples = sum(1 for row in heatmap_rows if row.get("source") == "detected")
        interpolated_samples = len(heatmap_rows) - detected_samples
        quality = _heatmap_quality(
            samples=len(heatmap_rows),
            detected_samples=detected_samples,
            detected_frames=int(player.get("detected_frames") or 0),
            ambiguous_frames=int(player.get("ambiguous_frames") or 0),
        )
        player["heatmap_path"] = relative_path
        player["heatmap_samples"] = len(heatmap_rows)
        player["heatmap_quality"] = quality
        row = {
            "stable_player_id": stable_player_id,
            "stable_subject_id": stable_subject_id,
            "slot_id": player.get("slot_id"),
            "team_label": player.get("team_label"),
            "team_id": player.get("team_id"),
            "team_name": player.get("team_name"),
            "path": relative_path,
            "samples": len(heatmap_rows),
            "detected_samples": detected_samples,
            "interpolated_samples": interpolated_samples,
            "quality": quality,
            "included_sources": sorted({str(row.get("source") or "detected") for row in heatmap_rows}),
            "ignored_sources": ["missing", "ambiguous", "inactive"],
        }
        rows.append(row)
        total_samples += len(heatmap_rows)
        detected_samples_total += detected_samples
        interpolated_samples_total += interpolated_samples
    summary = {
        "players": len(rows),
        "players_with_samples": sum(1 for row in rows if int(row.get("samples") or 0) > 0),
        "samples_total": total_samples,
        "detected_samples_total": detected_samples_total,
        "interpolated_samples_total": interpolated_samples_total,
        "players_low_quality": sum(1 for row in rows if row.get("quality") == "low"),
        "players_medium_quality": sum(1 for row in rows if row.get("quality") == "medium"),
        "players_high_quality": sum(1 for row in rows if row.get("quality") == "high"),
    }
    return {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "source": stable_doc.get("source") or "conservative_identity_v2",
        "identity_semantics": stable_doc.get("identity_semantics") or "stint_first",
        "method": "pitch_meter_gaussian_heatmap_v1",
        "pitch_dimensions_m": {"width_m": pitch_width_m, "length_m": pitch_length_m},
        "image_size_px": {"width": width_px, "height": length_px},
        "summary": summary,
        "heatmaps": sorted(rows, key=lambda item: str(item.get("stable_player_id") or "")),
    }


def _safe_artifact_id(value: str) -> str:
    safe = "".join(char.lower() if char.isalnum() else "-" for char in value.strip())
    safe = "-".join(part for part in safe.split("-") if part)
    return safe or "player"


def _player_heatmap_rows(player: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for row in player.get("overlay_positions") or []:
        if not isinstance(row, dict):
            continue
        source = str(row.get("source") or row.get("status") or "detected")
        if source in {"missing", "ambiguous", "inactive"}:
            continue
        if source not in {"detected", "interpolated", "short_gap_interpolated", "predicted"}:
            continue
        pitch_m = row.get("pitch_m")
        if not pitch_m or len(pitch_m) < 2:
            continue
        if source == "predicted" and row.get("visual_trusted") is False:
            continue
        rows.append({"pitch_m": pitch_m, "source": source})
    return rows


def _write_player_heatmap_png(
    output_path: Path,
    rows: list[dict[str, Any]],
    *,
    pitch_width_m: float,
    pitch_length_m: float,
    width_px: int,
    length_px: int,
) -> None:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter, ImageOps

    heat = np.zeros((length_px, width_px), dtype=np.float32)
    for row in rows:
        pitch_m = row.get("pitch_m")
        if not pitch_m or len(pitch_m) < 2:
            continue
        x_m, y_m = float(pitch_m[0]), float(pitch_m[1])
        x = int(np.clip(x_m / max(pitch_width_m, 0.001) * (width_px - 1), 0, width_px - 1))
        y = int(np.clip(y_m / max(pitch_length_m, 0.001) * (length_px - 1), 0, length_px - 1))
        heat[y, x] += 1.0
    if heat.max() > 0:
        normalized = (heat / heat.max() * 255).astype(np.uint8)
        heat_image = Image.fromarray(normalized, mode="L").filter(ImageFilter.GaussianBlur(radius=12))
        blurred = np.asarray(heat_image, dtype=np.float32)
        if blurred.max() > 0:
            heat_image = Image.fromarray((blurred / blurred.max() * 255).astype(np.uint8), mode="L")
        colored = ImageOps.colorize(heat_image, black="#163d2b", mid="#facc15", white="#ef4444")
    else:
        colored = Image.new("RGB", (width_px, length_px), "#1a4630")
    _draw_pitch_on_heatmap(colored, ImageDraw.Draw(colored))
    colored.save(output_path)


def _draw_pitch_on_heatmap(image: Any, draw: Any) -> None:
    width, height = image.size
    line = "#f5f5f5"
    draw.rectangle((2, 2, width - 3, height - 3), outline=line, width=2)
    draw.line((2, height // 2, width - 3, height // 2), fill=line, width=1)
    box_depth = max(20, int(height * 0.18))
    box_width = max(40, int(width * 0.62))
    box_x1 = (width - box_width) // 2
    box_x2 = box_x1 + box_width
    draw.rectangle((box_x1, 2, box_x2, box_depth), outline=line, width=1)
    draw.rectangle((box_x1, height - box_depth, box_x2, height - 3), outline=line, width=1)


def _heatmap_quality(samples: int, detected_samples: int, detected_frames: int, ambiguous_frames: int) -> str:
    if samples < 8 or detected_samples < 5:
        return "low"
    detected_ratio = detected_samples / max(1, samples)
    ambiguous_ratio = ambiguous_frames / max(1, detected_frames + ambiguous_frames)
    if detected_ratio < 0.65 or ambiguous_ratio > 0.25:
        return "medium"
    return "high"


def _stats_float(stats: dict[str, Any], key: str, fallback: float = 0.0) -> float:
    return round(float(stats.get(key) if stats.get(key) is not None else fallback), 4)


def _stats_record(stats: dict[str, Any], key: str) -> dict[str, Any]:
    value = stats.get(key)
    return value if isinstance(value, dict) else {}


def _stats_nested_float(stats: dict[str, Any], group: str, key: str, fallback: float = 0.0) -> float:
    return _stats_float(_stats_record(stats, group), key, fallback)


def _stats_nested_int(stats: dict[str, Any], group: str, key: str, fallback: int = 0) -> int:
    value = _stats_record(stats, group).get(key)
    return int(value) if isinstance(value, (int, float)) else fallback


def _stats_nested_record(stats: dict[str, Any], group: str, key: str) -> dict[str, Any]:
    return _stats_record(_stats_record(stats, group), key)


def _best_sprint_candidate_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = [
        row
        for row in rows
        if isinstance(row, dict) and float(row.get("max_speed_kmh") or 0.0) > 0.0
    ]
    if not candidates:
        return {}
    best = max(
        candidates,
        key=lambda row: (
            float(row.get("max_speed_kmh") or 0.0),
            float(row.get("duration_sec") or 0.0),
            float(row.get("distance_m") or 0.0),
        ),
    )
    return {
        "start_frame": int(best.get("start_frame") or 0),
        "end_frame": int(best.get("end_frame") or 0),
        "start_time_sec": round(float(best.get("start_time_sec") or 0.0), 3),
        "end_time_sec": round(float(best.get("end_time_sec") or 0.0), 3),
        "duration_sec": round(float(best.get("duration_sec") or 0.0), 3),
        "distance_m": round(float(best.get("distance_m") or 0.0), 2),
        "max_speed_kmh": round(float(best.get("max_speed_kmh") or 0.0), 2),
        "reason": str(best.get("reason") or "none"),
    }


def _strip_overlay_positions(stable_doc: dict[str, Any]) -> dict[str, Any]:
    public_doc = dict(stable_doc)
    public_doc.pop("frame_detection_counts", None)
    public_players = []
    for player in stable_doc.get("players", []):
        public_player = dict(player)
        public_player.pop("overlay_positions", None)
        public_players.append(public_player)
    public_doc["players"] = public_players
    public_suppressed = []
    for player in stable_doc.get("suppressed_candidates", []):
        public_player = dict(player)
        public_player.pop("overlay_positions", None)
        public_suppressed.append(public_player)
    public_doc["suppressed_candidates"] = public_suppressed
    return public_doc


class _StableOverlayWriter:
    def __init__(self, match_dir: Path, output_name: str, fps: float, frame_size: tuple[int, int]) -> None:
        self.frame_size = frame_size
        self.fps = max(1.0, float(fps))
        self.final_path = match_dir / output_name
        self.temp_path = match_dir / f"{output_name}.raw.avi"
        self.frames_written = 0
        import cv2

        self._writer = cv2.VideoWriter(str(self.temp_path), cv2.VideoWriter_fourcc(*"MJPG"), self.fps, frame_size)
        if not self._writer.isOpened():
            raise RuntimeError(f"Could not open OpenCV VideoWriter for {self.temp_path.name}.")

    def write(self, frame: Any) -> None:
        expected_w, expected_h = self.frame_size
        if frame.shape[1] != expected_w or frame.shape[0] != expected_h:
            import cv2

            frame = cv2.resize(frame, (expected_w, expected_h))
        self._writer.write(frame)
        self.frames_written += 1

    def close(self) -> Path:
        self._writer.release()
        if self.frames_written == 0:
            self.temp_path.unlink(missing_ok=True)
            raise RuntimeError("Stable overlay was not generated because zero frames were processed.")
        if self.final_path.exists():
            self.final_path.unlink()
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise RuntimeError("ffmpeg is not available, so stable overlay could not be converted to MP4.")
        cmd = [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(self.temp_path),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(self.final_path),
        ]
        completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
        self.temp_path.unlink(missing_ok=True)
        if completed.returncode != 0:
            raise RuntimeError(f"ffmpeg failed while converting stable overlay: {completed.stderr.strip()}")
        return self.final_path


def write_stable_overlay(
    video_path: Path,
    match_dir: Path,
    stable_doc: dict[str, Any],
    pitch_polygon: Any,
    *,
    fps: float,
    frame_size: tuple[int, int],
    output_name: str = "stable_overlay_preview.mp4",
    include_untrusted: bool = False,
    camera_motion: Any | None = None,
    ball_tracks_doc: dict[str, Any] | None = None,
    pitch_homography: Any | None = None,
    possession_doc: dict[str, Any] | None = None,
    pass_candidates_doc: dict[str, Any] | None = None,
) -> Path:
    import cv2
    import numpy as np

    player_colors: dict[str, tuple[int, int, int]] = {}
    stats_rows = _overlay_stats_rows(stable_doc.get("players", []))
    player_labels = _player_display_labels(stable_doc)
    for player in stable_doc.get("players", []):
        stable_player_id = player["stable_player_id"]
        player_colors[stable_player_id] = _stable_bgr_color(player.get("team_label"))
    frame_rows = _stable_overlay_frame_rows(
        stable_doc,
        pitch_polygon,
        fps=fps,
        include_untrusted=include_untrusted,
        include_unmatched_raw=True,
        camera_motion=camera_motion,
    )
    ball_positions = _ball_overlay_positions_by_frame(ball_tracks_doc)
    possession_rows = _possession_rows_by_frame(possession_doc)
    live_possession_rows = _live_possession_by_frame(possession_doc)
    possession_summary = possession_doc.get("summary") if isinstance(possession_doc, dict) else {}
    pass_rows_by_frame = _pass_candidates_by_frame(pass_candidates_doc, fps=fps)
    live_pass_rows = _live_pass_counts_by_frame(pass_candidates_doc)
    pass_summary = pass_candidates_doc.get("summary") if isinstance(pass_candidates_doc, dict) else {}
    inverse_homography = _safe_inverse_homography(pitch_homography)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video for stable overlay: {video_path}")
    writer = _StableOverlayWriter(match_dir, output_name, fps=fps, frame_size=frame_size)
    overlay_frames = set(frame_rows) | set(ball_positions)
    max_frame = max(overlay_frames) if overlay_frames else int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) - 1
    live_pass_rows = _extend_live_rows(live_pass_rows, max_frame)
    frame_idx = 0
    trail_points: dict[str, deque[tuple[int, tuple[int, int]]]] = defaultdict(deque)
    summary = stable_doc.get("summary") or {}
    frame_counts = {
        int(item.get("frame") or 0): item
        for item in (stable_doc.get("frame_detection_counts") or {}).get("frames", [])
    }
    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame_idx > max_frame:
                break
            overlay = frame.copy()
            overlay_polygon = camera_motion.polygon_for_frame(frame_idx, pitch_polygon) if camera_motion is not None else pitch_polygon
            cv2.polylines(overlay, [overlay_polygon.astype(np.int32)], isClosed=True, color=(0, 255, 255), thickness=2)
            current_rows = frame_rows.get(frame_idx, [])
            for row in current_rows:
                center = _bbox_center(row["bbox_xyxy"])
                if center is not None and row.get("source") == "detected":
                    trail_points[row["stable_player_id"]].append((frame_idx, center))
            _trim_trails(trail_points, frame_idx, trail_frame_window=45)
            _draw_stable_trails(overlay, trail_points, player_colors)
            current_possession_row = possession_rows.get(frame_idx)
            _draw_open_passing_lanes(overlay, current_possession_row, current_rows, player_labels)
            for row in current_rows:
                _draw_stable_row(overlay, row)
            _draw_possession_indicator(overlay, current_possession_row, current_rows)
            ball_position = ball_positions.get(frame_idx)
            if ball_position is not None:
                _draw_ball_position(overlay, ball_position)
            current_pass_rows = pass_rows_by_frame.get(frame_idx, [])
            _draw_pass_candidate_arrows(
                overlay,
                current_pass_rows,
                frame_idx=frame_idx,
                inverse_homography=inverse_homography,
                camera_motion=camera_motion,
                player_labels=player_labels,
            )
            visual_counts = _visual_counts(current_rows)
            _draw_stable_hud(overlay, frame_idx, fps, summary, frame_counts.get(frame_idx), visual_counts)
            if camera_motion is not None:
                _draw_camera_motion_hud(overlay, camera_motion.sample_for_frame(frame_idx))
            _draw_possession_pass_panel(
                overlay,
                current_possession_row,
                live_possession_rows.get(frame_idx),
                live_pass_rows.get(frame_idx),
                possession_summary,
                current_pass_rows,
                pass_summary,
                player_labels,
            )
            _draw_player_stats_panel(overlay, stats_rows)
            _draw_frame_stamp(overlay, frame_idx)
            writer.write(overlay)
            frame_idx += 1
    finally:
        cap.release()
    return writer.close()


def _ball_overlay_positions_by_frame(ball_tracks_doc: dict[str, Any] | None) -> dict[int, dict[str, Any]]:
    if not ball_tracks_doc:
        return {}
    positions = ball_tracks_doc.get("positions")
    if not isinstance(positions, list):
        return {}
    frame_rows: dict[int, dict[str, Any]] = {}
    for position in positions:
        if not isinstance(position, dict):
            continue
        if position.get("source") == "unknown" or not position.get("position_px"):
            continue
        frame_rows[int(position.get("frame") or 0)] = position
    return frame_rows


def _possession_rows_by_frame(possession_doc: dict[str, Any] | None) -> dict[int, dict[str, Any]]:
    if not isinstance(possession_doc, dict):
        return {}
    frames = possession_doc.get("frames")
    if not isinstance(frames, list):
        return {}
    return {
        int(row.get("frame") or 0): row
        for row in frames
        if isinstance(row, dict) and row.get("frame") is not None
    }


def _live_possession_by_frame(possession_doc: dict[str, Any] | None) -> dict[int, dict[str, Any]]:
    if not isinstance(possession_doc, dict):
        return {}
    frames = possession_doc.get("frames")
    if not isinstance(frames, list):
        return {}
    summary = possession_doc.get("summary") if isinstance(possession_doc.get("summary"), dict) else {}
    frame_interval_sec = float(summary.get("frame_interval_sec") or (1.0 / 30.0))
    team_counts = {"A": 0, "B": 0}
    skipped_counts = {"free": 0, "contested": 0, "unknown": 0}
    live_rows: dict[int, dict[str, Any]] = {}
    for row in sorted(
        [item for item in frames if isinstance(item, dict) and item.get("frame") is not None],
        key=lambda item: int(item.get("frame") or 0),
    ):
        frame_idx = int(row.get("frame") or 0)
        status = str(row.get("status") or "unknown")
        team = str(row.get("team_label") or "")
        action = f"skip {status}"
        action_team = None
        if status == "controlled" and team in team_counts:
            team_counts[team] += 1
            action = f"+{team}"
            action_team = team
        elif status in skipped_counts:
            skipped_counts[status] += 1
        else:
            skipped_counts["unknown"] += 1
        controlled_total = team_counts["A"] + team_counts["B"]
        live_rows[frame_idx] = {
            "frame": frame_idx,
            "time_sec": row.get("time_sec"),
            "frame_interval_sec": frame_interval_sec,
            "action": action,
            "action_team": action_team,
            "team_controlled_frames": dict(team_counts),
            "controlled_frames": controlled_total,
            "processed_frames": team_counts["A"] + team_counts["B"] + sum(skipped_counts.values()),
            "skipped_frames": sum(skipped_counts.values()),
            "skipped_counts": dict(skipped_counts),
            "team_a_ratio": team_counts["A"] / controlled_total if controlled_total else None,
            "team_b_ratio": team_counts["B"] / controlled_total if controlled_total else None,
            "source_status": status,
            "source_reason": row.get("reason"),
        }
    return live_rows


def _live_pass_counts_by_frame(pass_candidates_doc: dict[str, Any] | None) -> dict[int, dict[str, Any]]:
    if not isinstance(pass_candidates_doc, dict):
        return {}
    candidates = pass_candidates_doc.get("candidates")
    if not isinstance(candidates, list):
        return {}
    team_attempts = {"A": 0, "B": 0}
    team_completed = {"A": 0, "B": 0}
    team_failed = {"A": 0, "B": 0}
    excluded_count = 0
    legacy_turnover_count = 0
    live_rows: dict[int, dict[str, Any]] = {}
    for candidate in sorted(
        [item for item in candidates if isinstance(item, dict) and item.get("end_frame") is not None],
        key=lambda item: int(item.get("end_frame") or 0),
    ):
        frame_idx = int(candidate.get("end_frame") or 0)
        has_explicit_outcome = candidate.get("outcome") is not None
        outcome = str(candidate.get("outcome") or _legacy_pass_outcome(candidate))
        team = str(candidate.get("count_for_team_label") or candidate.get("from_team_label") or "")
        action = "skip pass?"
        action_team = None
        if outcome == "completed_pass" and team in team_attempts:
            team_attempts[team] += 1
            action_team = team
            team_completed[team] += 1
            action = f"+{team} completed" if has_explicit_outcome else f"+{team} pass"
        elif outcome == "failed_pass":
            if has_explicit_outcome and team in team_attempts:
                team_attempts[team] += 1
                team_failed[team] += 1
                action_team = team
                action = f"+{team} failed"
            else:
                legacy_turnover_count += 1
                action = "turnover?"
        elif outcome == "excluded_non_pass":
            excluded_count += 1
            action = "excluded"
        live_rows[frame_idx] = {
            "frame": frame_idx,
            "action": action,
            "action_team": action_team,
            "team_pass_candidates": dict(team_completed),
            "same_team_pass_candidates": team_completed["A"] + team_completed["B"],
            "turnover_or_interception_candidates": team_failed["A"] + team_failed["B"] + legacy_turnover_count,
            "team_pass_attempts": dict(team_attempts),
            "team_completed_passes": dict(team_completed),
            "team_failed_passes": dict(team_failed),
            "pass_attempts": team_attempts["A"] + team_attempts["B"],
            "completed_passes": team_completed["A"] + team_completed["B"],
            "failed_passes": team_failed["A"] + team_failed["B"],
            "excluded_non_pass_candidates": excluded_count,
        }
    return _fill_live_rows(live_rows)


def _fill_live_rows(rows: dict[int, dict[str, Any]]) -> dict[int, dict[str, Any]]:
    if not rows:
        return {}
    filled: dict[int, dict[str, Any]] = {}
    last: dict[str, Any] | None = None
    for frame in range(max(rows) + 1):
        if frame in rows:
            last = rows[frame]
        if last is not None:
            filled[frame] = {**last, "frame": frame}
    return filled


def _extend_live_rows(rows: dict[int, dict[str, Any]], max_frame: int) -> dict[int, dict[str, Any]]:
    if not rows or max_frame <= max(rows):
        return rows
    filled = dict(rows)
    last = rows[max(rows)]
    for frame in range(max(rows) + 1, max_frame + 1):
        filled[frame] = {**last, "frame": frame, "action": "hold"}
    return filled


def _pass_candidates_by_frame(
    pass_candidates_doc: dict[str, Any] | None,
    *,
    fps: float,
    hold_sec: float = 1.25,
) -> dict[int, list[dict[str, Any]]]:
    if not isinstance(pass_candidates_doc, dict):
        return {}
    candidates = pass_candidates_doc.get("candidates")
    if not isinstance(candidates, list):
        return {}
    hold_frames = max(1, int(round(max(0.0, hold_sec) * max(float(fps or 0.0), 1.0))))
    rows_by_frame: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        start_frame = int(candidate.get("start_frame") or 0)
        end_frame = int(candidate.get("end_frame") or start_frame)
        if end_frame < start_frame:
            end_frame = start_frame
        display_end = end_frame + hold_frames
        for frame in range(start_frame, display_end + 1):
            rows_by_frame[frame].append(candidate)
    return rows_by_frame


def _player_display_labels(stable_doc: dict[str, Any]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for player in stable_doc.get("players") or []:
        if not isinstance(player, dict):
            continue
        stable_player_id = str(player.get("stable_player_id") or "")
        if stable_player_id:
            labels[stable_player_id] = str(player.get("display_label") or stable_player_id)
    return labels


def _safe_inverse_homography(homography: Any | None) -> Any | None:
    if homography is None:
        return None
    try:
        import numpy as np

        return np.linalg.inv(np.asarray(homography, dtype=np.float32))
    except Exception:
        return None


def _stable_overlay_frame_rows(
    stable_doc: dict[str, Any],
    pitch_polygon: Any,
    *,
    fps: float,
    include_untrusted: bool = False,
    include_unmatched_raw: bool = False,
    camera_motion: Any | None = None,
) -> dict[int, list[dict[str, Any]]]:
    frame_rows: dict[int, list[dict[str, Any]]] = {}
    for player in stable_doc.get("players", []):
        stable_player_id = player["stable_player_id"]
        overlay_positions = player.get("overlay_positions") or []
        bbox_stats = _player_bbox_stats(overlay_positions)
        live_movement = _live_movement_by_frame(overlay_positions, fps)
        visual_positions = _stable_overlay_visual_positions(
            overlay_positions,
            bbox_stats,
            pitch_polygon,
            fps=fps,
            include_untrusted=include_untrusted,
            camera_motion=camera_motion,
        )
        for position in visual_positions:
            row = dict(position)
            row["stable_player_id"] = stable_player_id
            row["team_label"] = player.get("team_label")
            row["player_confidence"] = player.get("confidence")
            row["player_confidence_score"] = player.get("confidence_score")
            row["team_confidence"] = player.get("team_confidence")
            row["mean_detection_confidence"] = player.get("mean_detection_confidence")
            row["tracklet_count"] = player.get("tracklet_count")
            row["duration_sec"] = player.get("duration_sec")
            row["display_label"] = player.get("display_label")
            row["risky_link_count"] = len(player.get("risky_links") or [])
            row["source"] = _overlay_position_source(position)
            row["live_movement"] = live_movement.get(int(row.get("frame") or 0))
            frame_rows.setdefault(int(row.get("frame") or 0), []).append(row)
    if include_unmatched_raw:
        for observation in stable_doc.get("unmatched_observations") or []:
            if not isinstance(observation, dict) or not observation.get("bbox_xyxy"):
                continue
            frame = int(observation.get("frame") or 0)
            if not _should_draw_unmatched_raw(frame_rows.get(frame, []), observation):
                continue
            row = dict(observation)
            row["stable_player_id"] = "RAW"
            row["source"] = "unmatched_raw"
            row["status"] = "unmatched_raw"
            row["visual_trusted"] = False
            frame_rows.setdefault(frame, []).append(row)
    return frame_rows


def _should_draw_unmatched_raw(existing_rows: list[dict[str, Any]], observation: dict[str, Any]) -> bool:
    team_label = str(observation.get("team_label") or "U")
    stable_rows = [row for row in existing_rows if row.get("source") != "unmatched_raw"]
    if team_label in {"A", "B"}:
        visible_same_team = sum(1 for row in stable_rows if row.get("team_label") == team_label)
        return visible_same_team < DEFAULT_ACTIVE_PLAYERS_PER_TEAM_CAP
    return len(stable_rows) < DEFAULT_ACTIVE_PLAYERS_CAP


def _stable_overlay_visual_positions(
    overlay_positions: list[dict[str, Any]],
    bbox_stats: dict[str, float] | None,
    pitch_polygon: Any,
    *,
    fps: float,
    include_untrusted: bool,
    camera_motion: Any | None = None,
) -> list[dict[str, Any]]:
    visual_by_frame: dict[int, dict[str, Any]] = {}
    trusted_detected: list[dict[str, Any]] = []
    sorted_positions = sorted(
        overlay_positions,
        key=lambda item: (int(item.get("frame") or 0), float(item.get("time_sec") or 0.0)),
    )
    for position in sorted_positions:
        if not position.get("bbox_xyxy"):
            continue
        frame = int(position.get("frame") or 0)
        frame_pitch_polygon = camera_motion.polygon_for_frame(frame, pitch_polygon) if camera_motion is not None else pitch_polygon
        source = _overlay_position_source(position)
        if source == "detected":
            if not _overlay_bbox_is_safe(position, bbox_stats, frame_pitch_polygon):
                continue
            row = dict(position)
            row["source"] = "detected"
            visual_by_frame[int(row.get("frame") or 0)] = row
            trusted_detected.append(row)
            continue
        if source == "ambiguous":
            if not _bbox_is_visual_candidate(position, bbox_stats):
                continue
            visual_by_frame.setdefault(frame, dict(position, source=source))
            continue
        if not include_untrusted:
            continue
        if source in {"predicted", "interpolated", "short_gap_interpolated"}:
            if not _overlay_bbox_is_safe(position, bbox_stats, frame_pitch_polygon):
                continue
        else:
            continue
        frame = int(position.get("frame") or 0)
        visual_by_frame.setdefault(frame, dict(position, source=source))

    for position in _stable_overlay_short_gap_positions(trusted_detected, pitch_polygon, fps=fps, camera_motion=camera_motion):
        visual_by_frame.setdefault(int(position.get("frame") or 0), position)

    return [visual_by_frame[frame] for frame in sorted(visual_by_frame)]


def _stable_overlay_short_gap_positions(
    trusted_detected: list[dict[str, Any]],
    pitch_polygon: Any,
    *,
    fps: float,
    camera_motion: Any | None = None,
) -> list[dict[str, Any]]:
    interpolated: list[dict[str, Any]] = []
    fps_safe = max(float(fps or 0.0), 0.001)
    for index, current in enumerate(trusted_detected[:-1]):
        following = trusted_detected[index + 1]
        frame_gap = int(following.get("frame") or 0) - int(current.get("frame") or 0)
        missing_frames = frame_gap - 1
        if missing_frames <= 0:
            continue
        if not _can_visual_hold_gap(current, following, missing_frames=missing_frames, fps=fps_safe):
            continue
        for offset in range(1, frame_gap):
            ratio = offset / frame_gap
            row = _interpolate_position(current, following, offset=offset, ratio=ratio)
            row["source"] = "short_gap_interpolated"
            row["status"] = "short_gap_interpolated"
            row["visual_trusted"] = True
            frame = int(row.get("frame") or 0)
            frame_pitch_polygon = camera_motion.polygon_for_frame(frame, pitch_polygon) if camera_motion is not None else pitch_polygon
            if _overlay_bbox_is_safe(row, None, frame_pitch_polygon):
                interpolated.append(row)
    return interpolated


def _can_visual_hold_gap(
    current: dict[str, Any],
    following: dict[str, Any],
    *,
    missing_frames: int,
    fps: float,
) -> bool:
    if missing_frames > STABLE_OVERLAY_VISUAL_HOLD_MAX_GAP_FRAMES:
        return False
    frame_gap = int(following.get("frame") or 0) - int(current.get("frame") or 0)
    fallback_time_gap = frame_gap / max(fps, 0.001)
    time_gap = float(following.get("time_sec") or 0.0) - float(current.get("time_sec") or 0.0)
    if time_gap <= 0:
        time_gap = fallback_time_gap
    if time_gap <= 0 or time_gap > STABLE_OVERLAY_VISUAL_HOLD_MAX_GAP_SEC:
        return False
    distance = _distance_m(_position_pitch_point(current), _position_pitch_point(following))
    if distance is None or distance / max(time_gap, 0.001) > STABLE_OVERLAY_VISUAL_HOLD_MAX_SPEED_MPS:
        return False
    if not _valid_bbox(current.get("bbox_xyxy")) or not _valid_bbox(following.get("bbox_xyxy")):
        return False
    return _bbox_shape_is_close(current["bbox_xyxy"], following["bbox_xyxy"], max_ratio=1.8)


def _overlay_bbox_is_safe(position: dict[str, Any], bbox_stats: dict[str, float] | None, pitch_polygon: Any) -> bool:
    return (
        _bbox_footpoint_inside_polygon(position.get("bbox_xyxy"), pitch_polygon)
        and _bbox_is_visual_candidate(position, bbox_stats)
    )


def _overlay_position_source(position: dict[str, Any]) -> str:
    return str(position.get("source") or position.get("status") or "detected")


def _stable_bgr_color(team_label: Any) -> tuple[int, int, int]:
    if team_label == "A":
        return (0, 80, 255)
    if team_label == "B":
        return (255, 180, 40)
    return (0, 255, 255)


def _overlay_stats_rows(players: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for player in players:
        stats = player.get("movement_stats") or {}
        stable_player_id = player.get("stable_player_id")
        rows.append(
            {
                "stable_player_id": stable_player_id,
                "display_label": player.get("display_label") or stable_player_id,
                "team_label": player.get("team_label"),
                "distance_m": float(stats.get("total_distance_m") or 0.0),
                "estimated_distance_m": float(stats.get("estimated_gap_distance_m") or 0.0),
                "avg_speed_kmh": float(stats.get("avg_speed_kmh") or 0.0),
                "peak_sustained_speed_kmh": float(
                    stats.get("peak_sustained_speed_kmh") or stats.get("top_speed_kmh") or 0.0
                ),
                "raw_segment_top_speed_kmh": float(stats.get("raw_segment_top_speed_kmh") or 0.0),
                "playing_time_sec": float(stats.get("playing_time_sec") or player.get("duration_sec") or 0.0),
                "quality": stats.get("speed_quality") or stats.get("distance_quality") or "unknown",
            }
        )
    return sorted(rows, key=lambda item: str(item.get("stable_player_id") or ""))


def _live_movement_by_frame(positions: list[dict[str, Any]], fps: float) -> dict[int, dict[str, Any]]:
    detected = [
        position
        for position in sorted(positions, key=lambda item: (int(item.get("frame") or 0), float(item.get("time_sec") or 0.0)))
        if (position.get("source") or "detected") == "detected" and position.get("pitch_m")
    ]
    fps_safe = max(float(fps or 0.0), 0.001)
    live_by_frame: dict[int, dict[str, Any]] = {}
    cumulative_distance = 0.0
    estimated_distance = 0.0
    last_speed_kmh: float | None = None
    previous: dict[str, Any] | None = None

    for index, current in enumerate(detected):
        frame = int(current.get("frame") or 0)
        segment_source = "start"
        if previous is not None:
            previous_frame = int(previous.get("frame") or 0)
            frame_gap = max(1, frame - previous_frame)
            previous_time = float(previous.get("time_sec") or previous_frame / fps_safe)
            current_time = float(current.get("time_sec") or frame / fps_safe)
            dt = max(1.0 / fps_safe, current_time - previous_time)
            distance = _distance_m(previous.get("pitch_m"), current.get("pitch_m"))
            if distance is not None:
                speed_mps = distance / dt
                if speed_mps <= LIVE_STATS_MAX_SPEED_MPS and dt <= LIVE_STATS_ESTIMATED_GAP_SEC:
                    cumulative_distance += distance
                    windowed_speed = _windowed_live_speed_mps(detected, index, fps_safe)
                    last_speed_kmh = windowed_speed * 3.6 if windowed_speed is not None else None
                    if frame_gap <= LIVE_STATS_OBSERVED_GAP_FRAMES:
                        segment_source = "observed"
                    else:
                        segment_source = "estimated"
                        estimated_distance += distance
                else:
                    segment_source = "skipped"

        live_by_frame[frame] = {
            "current_speed_kmh": round(last_speed_kmh, 1) if last_speed_kmh is not None else None,
            "cumulative_distance_m": round(cumulative_distance, 1),
            "estimated_distance_m": round(estimated_distance, 1),
            "segment_source": segment_source,
            "has_estimated_distance": estimated_distance > 0.0,
        }
        previous = current
    return live_by_frame


def _windowed_live_speed_mps(detected: list[dict[str, Any]], current_index: int, fps: float) -> float | None:
    current = detected[current_index]
    current_frame = int(current.get("frame") or 0)
    current_time = float(current.get("time_sec") or current_frame / fps)
    current_point = current.get("pitch_m")
    best_candidate: float | None = None
    for previous in reversed(detected[:current_index]):
        previous_frame = int(previous.get("frame") or 0)
        previous_time = float(previous.get("time_sec") or previous_frame / fps)
        dt = current_time - previous_time
        if dt < LIVE_STATS_SPEED_MIN_WINDOW_SEC:
            continue
        if dt > LIVE_STATS_SPEED_MAX_WINDOW_SEC:
            break
        distance = _distance_m(previous.get("pitch_m"), current_point)
        if distance is None:
            continue
        speed = distance / max(dt, 0.001)
        if speed <= LIVE_STATS_SUSTAINED_SPEED_MPS:
            best_candidate = speed
            break
    return best_candidate


def _bbox_center(bbox_xyxy: Any) -> tuple[int, int] | None:
    if not bbox_xyxy or len(bbox_xyxy) != 4:
        return None
    x1, y1, x2, y2 = [int(value) for value in bbox_xyxy]
    return ((x1 + x2) // 2, (y1 + y2) // 2)


def _bbox_footpoint_inside_polygon(bbox_xyxy: Any, pitch_polygon: Any) -> bool:
    if not bbox_xyxy or len(bbox_xyxy) != 4:
        return False
    x1, _, x2, y2 = [float(value) for value in bbox_xyxy]
    footpoint = ((x1 + x2) / 2.0, y2)
    try:
        import cv2

        return cv2.pointPolygonTest(pitch_polygon.astype("float32"), footpoint, False) >= 0
    except ModuleNotFoundError:
        return _point_inside_polygon(footpoint, pitch_polygon)


def _point_inside_polygon(point: tuple[float, float], polygon: Any) -> bool:
    vertices = polygon.tolist() if hasattr(polygon, "tolist") else polygon
    if not vertices:
        return False
    x, y = point
    inside = False
    previous = vertices[-1]
    for current in vertices:
        x1, y1 = float(previous[0]), float(previous[1])
        x2, y2 = float(current[0]), float(current[1])
        if _point_on_segment(x, y, x1, y1, x2, y2):
            return True
        intersects = (y1 > y) != (y2 > y)
        if intersects:
            crossing_x = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x <= crossing_x:
                inside = not inside
        previous = current
    return inside


def _point_on_segment(x: float, y: float, x1: float, y1: float, x2: float, y2: float) -> bool:
    cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
    if abs(cross) > 1e-6:
        return False
    return min(x1, x2) - 1e-6 <= x <= max(x1, x2) + 1e-6 and min(y1, y2) - 1e-6 <= y <= max(y1, y2) + 1e-6


def _player_bbox_stats(positions: list[dict[str, Any]]) -> dict[str, float] | None:
    widths: list[float] = []
    heights: list[float] = []
    areas: list[float] = []
    for position in positions:
        bbox = position.get("bbox_xyxy")
        if not bbox or len(bbox) != 4:
            continue
        width = max(1.0, float(bbox[2]) - float(bbox[0]))
        height = max(1.0, float(bbox[3]) - float(bbox[1]))
        widths.append(width)
        heights.append(height)
        areas.append(width * height)
    if len(areas) < 6:
        return None
    return {
        "median_width": float(median(widths)),
        "median_height": float(median(heights)),
        "median_area": float(median(areas)),
    }


def _bbox_is_visual_candidate(position: dict[str, Any], stats: dict[str, float] | None) -> bool:
    bbox = position.get("bbox_xyxy")
    if not bbox or len(bbox) != 4:
        return False
    x1, y1, x2, y2 = [float(value) for value in bbox]
    width = max(1.0, x2 - x1)
    height = max(1.0, y2 - y1)
    area = width * height
    if width < 3 or height < 8 or area < 24:
        return False
    if not stats:
        return True
    median_area = max(1.0, stats["median_area"])
    median_width = max(1.0, stats["median_width"])
    median_height = max(1.0, stats["median_height"])
    area_ratio = area / median_area
    width_ratio = width / median_width
    height_ratio = height / median_height
    confidence = float(position.get("confidence") or 0.0)
    if area_ratio > 4.0 or width_ratio > 2.4 or height_ratio > 2.4:
        return confidence >= 0.2 and area_ratio <= 6.0 and width_ratio <= 3.0 and height_ratio <= 3.0
    return True


def _trim_trails(trail_points: dict[str, deque[tuple[int, tuple[int, int]]]], frame_idx: int, *, trail_frame_window: int) -> None:
    for stable_player_id in list(trail_points.keys()):
        points = trail_points[stable_player_id]
        while points and frame_idx - points[0][0] > trail_frame_window:
            points.popleft()
        if not points:
            trail_points.pop(stable_player_id, None)


def _draw_stable_trails(
    frame: Any,
    trail_points: dict[str, deque[tuple[int, tuple[int, int]]]],
    player_colors: dict[str, tuple[int, int, int]],
) -> None:
    import cv2
    import numpy as np

    for stable_player_id, points in trail_points.items():
        if len(points) < 2:
            continue
        color = player_colors.get(stable_player_id, (0, 255, 255))
        polyline = np.array([point for _, point in points], dtype=np.int32).reshape((-1, 1, 2))
        cv2.polylines(frame, [polyline], isClosed=False, color=color, thickness=1, lineType=cv2.LINE_AA)


def _draw_stable_row(frame: Any, row: dict[str, Any]) -> None:
    import cv2

    source = row.get("source")
    if source == "unmatched_raw":
        color = (0, 230, 255)
        x1, y1, x2, y2 = [int(v) for v in row["bbox_xyxy"]]
        _draw_dashed_rectangle(frame, x1, y1, x2, y2, color, dash_length=5)
        _draw_minimal_label(frame, "RAW?", x1, y1, color)
        return

    color = _stable_bgr_color(row.get("team_label"))
    x1, y1, x2, y2 = [int(v) for v in row["bbox_xyxy"]]
    is_untrusted = source in {"interpolated", "predicted", "ambiguous"}
    is_short_gap = source == "short_gap_interpolated"
    if is_untrusted:
        _draw_dashed_rectangle(frame, x1, y1, x2, y2, color)
    else:
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
    display_label = str(row.get("display_label") or row.get("stable_player_id") or "?")
    label = f"{display_label}?" if source == "ambiguous" else f"{display_label}*" if is_untrusted else display_label
    _draw_minimal_label(frame, label, x1, y1, color)
    if row.get("source") == "detected" or is_short_gap:
        _draw_live_stats_label(frame, row.get("live_movement"), x1, y1, y2, color)


def _draw_dashed_rectangle(
    frame: Any,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    color: tuple[int, int, int],
    *,
    dash_length: int = 8,
) -> None:
    import cv2

    for start in range(x1, x2, dash_length * 2):
        cv2.line(frame, (start, y1), (min(start + dash_length, x2), y1), color, 1, cv2.LINE_AA)
        cv2.line(frame, (start, y2), (min(start + dash_length, x2), y2), color, 1, cv2.LINE_AA)
    for start in range(y1, y2, dash_length * 2):
        cv2.line(frame, (x1, start), (x1, min(start + dash_length, y2)), color, 1, cv2.LINE_AA)
        cv2.line(frame, (x2, start), (x2, min(start + dash_length, y2)), color, 1, cv2.LINE_AA)


def _draw_minimal_label(
    frame: Any,
    label: str,
    x1: int,
    y1: int,
    color: tuple[int, int, int],
) -> None:
    import cv2

    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.48
    thickness = 1
    y = max(16, y1 - 5)
    cv2.putText(frame, label, (x1, y), font, font_scale, (0, 0, 0), thickness + 2, cv2.LINE_AA)
    cv2.putText(frame, label, (x1, y), font, font_scale, color, thickness, cv2.LINE_AA)


def _draw_live_stats_label(
    frame: Any,
    live_movement: dict[str, Any] | None,
    x1: int,
    y1: int,
    y2: int,
    color: tuple[int, int, int],
) -> None:
    if not live_movement:
        return

    import cv2

    speed = live_movement.get("current_speed_kmh")
    distance = float(live_movement.get("cumulative_distance_m") or 0.0)
    marker = "*" if live_movement.get("segment_source") == "estimated" else ""
    speed_label = "--" if speed is None else f"{float(speed):.1f}"
    label = f"v={speed_label}km/h d={distance:.1f}m{marker}"
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.36
    thickness = 1
    frame_height = frame.shape[0]
    y = y2 + 15
    if y > frame_height - 8:
        y = max(16, y1 - 20)
    cv2.putText(frame, label, (x1, y), font, font_scale, (0, 0, 0), thickness + 2, cv2.LINE_AA)
    cv2.putText(frame, label, (x1, y), font, font_scale, color, thickness, cv2.LINE_AA)


def _draw_possession_indicator(
    frame: Any,
    possession_row: dict[str, Any] | None,
    current_rows: list[dict[str, Any]],
) -> None:
    owner = _possession_owner_row(
        possession_row,
        current_rows,
        min_confidence=POSSESSION_INDICATOR_MIN_CONFIDENCE,
    )
    if not owner:
        return

    import cv2
    import numpy as np

    x1, y1, x2, _y2 = [int(round(float(value))) for value in owner["bbox_xyxy"]]
    center_x = (x1 + x2) // 2
    bbox_width = max(12, x2 - x1)
    half_width = int(max(7, min(14, bbox_width * 0.28)))
    tip_y = max(6, y1 - 5)
    base_y = max(1, tip_y - int(half_width * 1.25))
    points = np.array(
        [
            [center_x, tip_y],
            [center_x - half_width, base_y],
            [center_x + half_width, base_y],
        ],
        dtype=np.int32,
    )
    cv2.fillConvexPoly(frame, points, (35, 35, 245), cv2.LINE_AA)
    cv2.polylines(frame, [points.reshape((-1, 1, 2))], isClosed=True, color=(0, 0, 0), thickness=2, lineType=cv2.LINE_AA)


def _draw_open_passing_lanes(
    frame: Any,
    possession_row: dict[str, Any] | None,
    current_rows: list[dict[str, Any]],
    player_labels: dict[str, str],
) -> None:
    lanes = _open_passing_lanes(possession_row, current_rows, player_labels)
    if not lanes:
        return

    import cv2

    layer = frame.copy()
    for lane in lanes:
        color = _passing_lane_color(lane.get("team_label"))
        start = lane["start_px"]
        end = lane["end_px"]
        cv2.line(layer, start, end, color, 1, cv2.LINE_AA)
        cv2.circle(layer, end, 4, color, 1, cv2.LINE_AA)
    cv2.addWeighted(layer, 0.42, frame, 0.58, 0, dst=frame)


def _open_passing_lanes(
    possession_row: dict[str, Any] | None,
    current_rows: list[dict[str, Any]],
    player_labels: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    owner = _possession_owner_row(
        possession_row,
        current_rows,
        min_confidence=PASSING_LANE_MIN_POSSESSION_CONFIDENCE,
    )
    if not owner:
        return []
    owner_team = str(owner.get("team_label") or "")
    if owner_team not in {"A", "B"}:
        return []
    start = _row_footpoint(owner)
    if start is None:
        return []

    trusted_rows = [row for row in current_rows if _row_is_trusted_for_overlay(row) and _valid_bbox(row.get("bbox_xyxy"))]
    receivers = [
        row
        for row in trusted_rows
        if row.get("stable_player_id") != owner.get("stable_player_id") and row.get("team_label") == owner_team
    ]
    opponents = [
        row
        for row in trusted_rows
        if row.get("team_label") in {"A", "B"} and row.get("team_label") != owner_team
    ]
    lanes: list[dict[str, Any]] = []
    labels = player_labels or {}
    for receiver in receivers:
        end = _row_footpoint(receiver)
        if end is None:
            continue
        length = _point_distance(start, end)
        if length < PASSING_LANE_MIN_LENGTH_PX:
            continue
        blocker = next(
            (
                opponent
                for opponent in opponents
                if _row_blocks_passing_lane(opponent, start, end, corridor_px=PASSING_LANE_CORRIDOR_PX)
            ),
            None,
        )
        if blocker is not None:
            continue
        receiver_id = str(receiver.get("stable_player_id") or "")
        lanes.append(
            {
                "from_stable_player_id": owner.get("stable_player_id"),
                "to_stable_player_id": receiver_id,
                "to_label": labels.get(receiver_id, receiver_id),
                "team_label": owner_team,
                "start_px": (int(round(start[0])), int(round(start[1]))),
                "end_px": (int(round(end[0])), int(round(end[1]))),
                "distance_px": round(length, 2),
            }
        )
    return sorted(lanes, key=lambda lane: float(lane.get("distance_px") or 0.0))[:PASSING_LANE_MAX_OPTIONS]


def _possession_owner_row(
    possession_row: dict[str, Any] | None,
    current_rows: list[dict[str, Any]],
    *,
    min_confidence: float,
) -> dict[str, Any] | None:
    if not isinstance(possession_row, dict):
        return None
    if str(possession_row.get("status") or "") != "controlled":
        return None
    if float(possession_row.get("confidence") or 0.0) < min_confidence:
        return None
    stable_player_id = str(possession_row.get("stable_player_id") or "")
    if not stable_player_id:
        return None
    for row in current_rows:
        if str(row.get("stable_player_id") or "") != stable_player_id:
            continue
        if _row_is_trusted_for_overlay(row) and _valid_bbox(row.get("bbox_xyxy")):
            return row
    return None


def _row_is_trusted_for_overlay(row: dict[str, Any]) -> bool:
    source = str(row.get("source") or "")
    if source == "detected":
        return True
    return source == "short_gap_interpolated" and row.get("visual_trusted") is True


def _row_footpoint(row: dict[str, Any]) -> tuple[float, float] | None:
    bbox = row.get("bbox_xyxy")
    if not _valid_bbox(bbox):
        return None
    x1, _y1, x2, y2 = [float(value) for value in bbox]
    return ((x1 + x2) / 2.0, y2)


def _row_lower_bbox_segment(row: dict[str, Any]) -> tuple[tuple[float, float], tuple[float, float]] | None:
    bbox = row.get("bbox_xyxy")
    if not _valid_bbox(bbox):
        return None
    x1, _y1, x2, y2 = [float(value) for value in bbox]
    return (x1, y2), (x2, y2)


def _row_blocks_passing_lane(
    row: dict[str, Any],
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    corridor_px: float,
) -> bool:
    footpoint = _row_footpoint(row)
    if footpoint is not None and _point_to_segment_distance(footpoint, start, end) <= corridor_px:
        projection = _segment_projection_ratio(footpoint, start, end)
        if 0.05 <= projection <= 0.95:
            return True
    lower_segment = _row_lower_bbox_segment(row)
    if lower_segment is None:
        return False
    return _segment_to_segment_distance(start, end, lower_segment[0], lower_segment[1]) <= corridor_px


def _point_distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _point_to_segment_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    px, py = point
    sx, sy = start
    ex, ey = end
    dx = ex - sx
    dy = ey - sy
    denom = dx * dx + dy * dy
    if denom <= 1e-9:
        return _point_distance(point, start)
    ratio = max(0.0, min(1.0, ((px - sx) * dx + (py - sy) * dy) / denom))
    closest = (sx + ratio * dx, sy + ratio * dy)
    return _point_distance(point, closest)


def _segment_projection_ratio(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    sx, sy = start
    ex, ey = end
    dx = ex - sx
    dy = ey - sy
    denom = dx * dx + dy * dy
    if denom <= 1e-9:
        return 0.0
    return ((point[0] - sx) * dx + (point[1] - sy) * dy) / denom


def _segment_to_segment_distance(
    a1: tuple[float, float],
    a2: tuple[float, float],
    b1: tuple[float, float],
    b2: tuple[float, float],
) -> float:
    if _segments_intersect(a1, a2, b1, b2):
        return 0.0
    return min(
        _point_to_segment_distance(a1, b1, b2),
        _point_to_segment_distance(a2, b1, b2),
        _point_to_segment_distance(b1, a1, a2),
        _point_to_segment_distance(b2, a1, a2),
    )


def _segments_intersect(
    a1: tuple[float, float],
    a2: tuple[float, float],
    b1: tuple[float, float],
    b2: tuple[float, float],
) -> bool:
    def orientation(p: tuple[float, float], q: tuple[float, float], r: tuple[float, float]) -> float:
        return (q[1] - p[1]) * (r[0] - q[0]) - (q[0] - p[0]) * (r[1] - q[1])

    def on_segment(p: tuple[float, float], q: tuple[float, float], r: tuple[float, float]) -> bool:
        return (
            min(p[0], r[0]) - 1e-9 <= q[0] <= max(p[0], r[0]) + 1e-9
            and min(p[1], r[1]) - 1e-9 <= q[1] <= max(p[1], r[1]) + 1e-9
        )

    o1 = orientation(a1, a2, b1)
    o2 = orientation(a1, a2, b2)
    o3 = orientation(b1, b2, a1)
    o4 = orientation(b1, b2, a2)
    if o1 * o2 < 0 and o3 * o4 < 0:
        return True
    return (
        abs(o1) <= 1e-9 and on_segment(a1, b1, a2)
        or abs(o2) <= 1e-9 and on_segment(a1, b2, a2)
        or abs(o3) <= 1e-9 and on_segment(b1, a1, b2)
        or abs(o4) <= 1e-9 and on_segment(b1, a2, b2)
    )


def _passing_lane_color(team_label: Any) -> tuple[int, int, int]:
    if team_label == "A":
        return (70, 190, 255)
    if team_label == "B":
        return (255, 210, 90)
    return (120, 220, 120)


def _format_score(value: Any) -> str:
    if value is None:
        return "--"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "--"


def _rebuild_ball_tracks_from_candidates(
    ball_tracks_doc: dict[str, Any] | None,
    ball_candidates_doc: dict[str, Any] | None,
    *,
    fps: float,
) -> dict[str, Any] | None:
    if not ball_candidates_doc:
        return ball_tracks_doc
    candidate_frames = ball_candidates_doc.get("frames")
    if not isinstance(candidate_frames, list):
        return ball_tracks_doc
    processed_frames = [
        int(position.get("frame") or 0)
        for position in (ball_tracks_doc or {}).get("positions", [])
        if isinstance(position, dict)
    ]
    if not processed_frames:
        processed_frames = [
            int(frame.get("frame") or 0)
            for frame in candidate_frames
            if isinstance(frame, dict) and frame.get("frame") is not None
        ]
    parameters = {
        **(ball_candidates_doc.get("parameters") or {}),
        **((ball_tracks_doc or {}).get("parameters") or {}),
        "candidate_recovery_source": "ball_candidates",
    }
    return build_ball_tracks_document(
        candidate_frames,
        processed_frames=processed_frames,
        fps=fps,
        parameters=parameters,
    )


def _apply_player_label_overrides(stable_doc: dict[str, Any], label_overrides: dict[str, str] | None) -> None:
    if not label_overrides:
        return
    normalized = {
        str(key).strip(): str(value).strip()
        for key, value in label_overrides.items()
        if str(key).strip() and str(value).strip()
    }
    if not normalized:
        return
    for player in stable_doc.get("players") or []:
        stable_player_id = str(player.get("stable_player_id") or "")
        display_label = normalized.get(stable_player_id)
        if display_label:
            player["display_label"] = display_label


def _visual_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    stable_rows = [row for row in rows if row.get("source") != "unmatched_raw"]
    visible_detected = sum(1 for row in stable_rows if row.get("source") == "detected")
    visible_predicted = sum(1 for row in stable_rows if row.get("source") in {"predicted", "interpolated"})
    visible_interpolated = sum(1 for row in stable_rows if row.get("source") == "short_gap_interpolated")
    visible_ambiguous = sum(1 for row in stable_rows if row.get("source") == "ambiguous")
    team_a = sum(1 for row in stable_rows if row.get("team_label") == "A")
    team_b = sum(1 for row in stable_rows if row.get("team_label") == "B")
    return {
        "visible_boxes": len(stable_rows),
        "visible_detected": visible_detected,
        "visible_predicted": visible_predicted,
        "visible_interpolated": visible_interpolated,
        "visible_ambiguous": visible_ambiguous,
        "visible_unmatched_raw": len(rows) - len(stable_rows),
        "visible_team_a": team_a,
        "visible_team_b": team_b,
    }


def apply_stable_overlay_visual_counts(
    frame_detection_counts: dict[str, Any],
    stable_doc: dict[str, Any],
    pitch_polygon: Any,
    *,
    fps: float = 25.0,
    camera_motion: Any | None = None,
) -> dict[str, Any]:
    frame_rows = _stable_overlay_frame_rows(
        stable_doc,
        pitch_polygon,
        fps=fps,
        include_untrusted=False,
        camera_motion=camera_motion,
    )
    counts_by_frame = {frame: _visual_counts(rows) for frame, rows in frame_rows.items()}

    frames = []
    stable_values: list[int] = []
    predicted_visible_values: list[int] = []
    visual_interpolated_values: list[int] = []
    ambiguous_visible_values: list[int] = []
    target_players = int(frame_detection_counts.get("target_players") or DEFAULT_ACTIVE_PLAYERS_CAP)
    for frame in frame_detection_counts.get("frames", []):
        frame_index = int(frame.get("frame") or 0)
        visual = counts_by_frame.get(frame_index, {})
        visible = int(visual.get("visible_boxes") or 0)
        trusted_detected = int(visual.get("visible_detected") or 0)
        visual_interpolated = int(visual.get("visible_interpolated") or 0)
        predicted_visible = int(visual.get("visible_predicted") or 0)
        ambiguous_visible = int(visual.get("visible_ambiguous") or 0)
        updated = {
            **frame,
            "stable_detected": trusted_detected,
            "stable_interpolated": visual_interpolated,
            "stable_total": visible,
            "trusted_detected": trusted_detected,
            "visible_stable_boxes": visible,
            "ambiguous_visible_boxes": ambiguous_visible,
            "visual_interpolated_boxes": visual_interpolated,
            "predicted_visible_boxes": predicted_visible,
            "stable_missing_vs_target": max(0, target_players - visible),
        }
        frames.append(updated)
        stable_values.append(visible)
        predicted_visible_values.append(predicted_visible)
        visual_interpolated_values.append(visual_interpolated)
        ambiguous_visible_values.append(ambiguous_visible)

    summary = dict(frame_detection_counts.get("summary") or {})
    summary.update(
        {
            "stable_min": min(stable_values) if stable_values else 0,
            "stable_max": max(stable_values) if stable_values else 0,
            "stable_avg": round(_mean([float(value) for value in stable_values]) or 0.0, 3),
            "stable_frames_below_target": sum(1 for value in stable_values if value < target_players),
            "stable_frames_at_or_above_target": sum(1 for value in stable_values if value >= target_players),
            "predicted_visible_boxes": sum(predicted_visible_values),
            "ambiguous_visible_boxes": sum(ambiguous_visible_values),
            "ambiguous_visible_frames": sum(1 for value in ambiguous_visible_values if value > 0),
            "visual_interpolated_boxes": sum(visual_interpolated_values),
            "visual_interpolated_frames": sum(1 for value in visual_interpolated_values if value > 0),
            "ghost_bbox_count": sum(predicted_visible_values),
        }
    )
    return {**frame_detection_counts, "summary": summary, "frames": frames}


def _draw_stable_hud(
    frame: Any,
    frame_idx: int,
    fps: float,
    summary: dict[str, Any],
    frame_count: dict[str, Any] | None = None,
    visual_counts: dict[str, int] | None = None,
) -> None:
    import cv2

    time_sec = frame_idx / max(fps, 0.001)
    active_slots = _count_value(frame_count, "active_slots")
    slot_detected = _count_value(frame_count, "slot_detected")
    slot_predicted = _count_value(frame_count, "slot_predicted")
    slot_missing = _count_value(frame_count, "slot_missing")
    visible = visual_counts or {}
    lines = [
        f"frame={frame_idx} t={time_sec:.1f}s raw={_count_value(frame_count, 'raw_detections')}",
        f"visible boxes={visible.get('visible_boxes', 0)} det={visible.get('visible_detected', 0)} hold={visible.get('visible_interpolated', 0)} amb={visible.get('visible_ambiguous', 0)} pred={visible.get('visible_predicted', 0)} raw?={visible.get('visible_unmatched_raw', 0)}",
        f"slots active={active_slots} det={slot_detected} amb={_count_value(frame_count, 'slot_ambiguous')} miss={slot_missing} A={_count_value(frame_count, 'active_team_a')} B={_count_value(frame_count, 'active_team_b')}",
        f"match slots={summary.get('stable_players', 0)} risky={summary.get('risky_links', 0)} low={summary.get('low_confidence_players', 0)}",
    ]
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.42
    thickness = 1
    line_height = 16
    widths = [cv2.getTextSize(line, font, font_scale, thickness)[0][0] for line in lines]
    width = max(widths, default=320) + 18
    height = line_height * len(lines) + 14
    frame_height, frame_width = frame.shape[:2]
    x1 = 12
    y1 = 12
    x2 = min(frame_width - 12, x1 + width)
    y2 = min(frame_height - 12, y1 + height)
    cv2.rectangle(frame, (x1, y1), (x2, y2), (20, 20, 20), -1)
    cv2.rectangle(frame, (x1, y1), (x2, y2), (230, 230, 230), 1)
    for index, line in enumerate(lines):
        y = y1 + 20 + index * line_height
        cv2.putText(frame, line, (x1 + 8, y), font, font_scale, (245, 245, 245), thickness, cv2.LINE_AA)


def _draw_camera_motion_hud(frame: Any, sample: Any) -> None:
    import cv2

    frame_height, _frame_width = frame.shape[:2]
    inlier_ratio = getattr(sample, "inlier_ratio", None)
    label = (
        f"camera motion: {getattr(sample, 'status', 'unknown')} "
        f"sample={getattr(sample, 'frame', 'n/a')} "
        f"ir={float(inlier_ratio or 0.0):.2f} "
        f"dx={float(getattr(sample, 'dx_px', 0.0)):.1f} dy={float(getattr(sample, 'dy_px', 0.0)):.1f}"
    )
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.42
    thickness = 1
    x1 = 12
    y1 = min(frame_height - 36, 82)
    (text_width, text_height), baseline = cv2.getTextSize(label, font, font_scale, thickness)
    cv2.rectangle(frame, (x1, y1), (x1 + text_width + 16, y1 + text_height + baseline + 12), (20, 20, 20), -1)
    cv2.rectangle(frame, (x1, y1), (x1 + text_width + 16, y1 + text_height + baseline + 12), (230, 230, 230), 1)
    cv2.putText(frame, label, (x1 + 8, y1 + text_height + 6), font, font_scale, (245, 245, 245), thickness, cv2.LINE_AA)


def _draw_possession_pass_panel(
    frame: Any,
    possession_row: dict[str, Any] | None,
    live_possession_row: dict[str, Any] | None,
    live_pass_row: dict[str, Any] | None,
    possession_summary: Any,
    pass_rows: list[dict[str, Any]],
    pass_summary: Any,
    player_labels: dict[str, str],
) -> None:
    if not possession_row and not pass_rows and not isinstance(pass_summary, dict):
        return

    import cv2

    summary = possession_summary if isinstance(possession_summary, dict) else {}
    pass_summary_doc = pass_summary if isinstance(pass_summary, dict) else {}
    lines = [
        _team_possession_line(summary),
        _live_possession_line(live_possession_row),
        _current_possession_line(possession_row, player_labels),
        _live_pass_line(live_pass_row),
        _pass_candidate_summary_line(pass_summary_doc),
    ]
    for candidate in pass_rows[:3]:
        lines.append(_pass_candidate_line(candidate, player_labels))

    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.42
    thickness = 1
    line_height = 16
    widths = [cv2.getTextSize(line, font, font_scale, thickness)[0][0] for line in lines]
    panel_width = max(widths, default=300) + 18
    panel_height = line_height * len(lines) + 14
    frame_height, frame_width = frame.shape[:2]
    x2 = frame_width - 12
    y1 = 12
    x1 = max(12, x2 - panel_width)
    y2 = min(frame_height - 12, y1 + panel_height)
    cv2.rectangle(frame, (x1, y1), (x2, y2), (20, 20, 20), -1)
    cv2.rectangle(frame, (x1, y1), (x2, y2), (230, 230, 230), 1)
    current_team = (possession_row or {}).get("team_label")
    live_possession_team = (live_possession_row or {}).get("action_team")
    live_pass_team = (live_pass_row or {}).get("action_team")
    candidate_line_start = 5
    for index, line in enumerate(lines):
        y = y1 + 20 + index * line_height
        color = (245, 245, 245)
        if index == 1 and live_possession_team in {"A", "B"}:
            color = _stable_bgr_color(live_possession_team)
        if index == 2 and current_team in {"A", "B"}:
            color = _stable_bgr_color(current_team)
        if index == 3 and live_pass_team in {"A", "B"}:
            color = _stable_bgr_color(live_pass_team)
        if index >= candidate_line_start:
            color = _pass_candidate_color(pass_rows[index - candidate_line_start])
        cv2.putText(frame, line, (x1 + 8, y), font, font_scale, color, thickness, cv2.LINE_AA)


def _draw_pass_candidate_arrows(
    frame: Any,
    pass_rows: list[dict[str, Any]],
    *,
    frame_idx: int,
    inverse_homography: Any | None,
    camera_motion: Any | None,
    player_labels: dict[str, str],
) -> None:
    if inverse_homography is None or not pass_rows:
        return

    import cv2

    for candidate in pass_rows[:3]:
        start = _pitch_m_to_frame_px(candidate.get("start_position_m"), inverse_homography, frame_idx, camera_motion)
        end = _pitch_m_to_frame_px(candidate.get("end_position_m"), inverse_homography, frame_idx, camera_motion)
        if start is None or end is None:
            continue
        color = _pass_candidate_color(candidate)
        cv2.arrowedLine(frame, start, end, color, 2, cv2.LINE_AA, tipLength=0.18)
        label = _pass_candidate_arrow_label(candidate, player_labels)
        midpoint = ((start[0] + end[0]) // 2, (start[1] + end[1]) // 2)
        cv2.putText(frame, label, (midpoint[0] + 6, max(18, midpoint[1] - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(frame, label, (midpoint[0] + 6, max(18, midpoint[1] - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1, cv2.LINE_AA)


def _pitch_m_to_frame_px(
    pitch_m: Any,
    inverse_homography: Any,
    frame_idx: int,
    camera_motion: Any | None,
) -> tuple[int, int] | None:
    if not _valid_pair(pitch_m):
        return None
    import cv2
    import numpy as np

    src = np.array([[[float(pitch_m[0]), float(pitch_m[1])]]], dtype=np.float32)
    reference = cv2.perspectiveTransform(src, inverse_homography.astype(np.float32))
    if camera_motion is not None:
        sample = camera_motion.sample_for_frame(frame_idx)
        matrix = np.asarray(sample.matrix_reference_to_current, dtype=np.float32)
        reference = cv2.perspectiveTransform(reference, matrix)
    x = int(round(float(reference[0][0][0])))
    y = int(round(float(reference[0][0][1])))
    return (x, y)


def _team_possession_line(summary: dict[str, Any]) -> str:
    counts = summary.get("team_controlled_frames") if isinstance(summary.get("team_controlled_frames"), dict) else {}
    team_a = int(counts.get("A") or 0)
    team_b = int(counts.get("B") or 0)
    total = team_a + team_b
    if total <= 0:
        return "clip poss: A -- | B --"
    return f"clip poss: A {team_a / total * 100:4.1f}% | B {team_b / total * 100:4.1f}%"


def _live_possession_line(live_row: dict[str, Any] | None) -> str:
    if not live_row:
        return "live poss frames: A -- | B -- action=--"
    counts = live_row.get("team_controlled_frames") if isinstance(live_row.get("team_controlled_frames"), dict) else {}
    team_a = int(counts.get("A") or 0)
    team_b = int(counts.get("B") or 0)
    total = team_a + team_b
    a_ratio = "--" if total <= 0 else f"{team_a / total * 100:4.1f}%"
    b_ratio = "--" if total <= 0 else f"{team_b / total * 100:4.1f}%"
    frame_interval = float(live_row.get("frame_interval_sec") or (1.0 / 30.0))
    team_a_sec = team_a * frame_interval
    team_b_sec = team_b * frame_interval
    return (
        f"live poss frames: A {team_a} ({team_a_sec:.1f}s {a_ratio}) | "
        f"B {team_b} ({team_b_sec:.1f}s {b_ratio}) {live_row.get('action') or '--'}"
    )


def _current_possession_line(possession_row: dict[str, Any] | None, player_labels: dict[str, str]) -> str:
    if not possession_row:
        return "now: possession unavailable"
    status = str(possession_row.get("status") or "unknown")
    confidence = float(possession_row.get("confidence") or 0.0)
    if status == "controlled":
        player_id = str(possession_row.get("stable_player_id") or "")
        player = player_labels.get(player_id, player_id or "?")
        team = possession_row.get("team_label") or "?"
        return f"now: {team} {player} controlled {confidence:.2f}"
    if status == "contested":
        return f"now: contested {confidence:.2f}"
    if status == "free":
        return f"now: free ball {confidence:.2f}"
    return f"now: unknown ({possession_row.get('reason') or 'n/a'})"


def _live_pass_line(live_row: dict[str, Any] | None) -> str:
    if not live_row:
        return "live pass: A 0/0 | B 0/0 failed=0 action=--"
    counts = live_row.get("team_pass_attempts") if isinstance(live_row.get("team_pass_attempts"), dict) else {}
    completed = live_row.get("team_completed_passes") if isinstance(live_row.get("team_completed_passes"), dict) else {}
    team_a = int(counts.get("A") or 0)
    team_b = int(counts.get("B") or 0)
    comp_a = int(completed.get("A") or 0)
    comp_b = int(completed.get("B") or 0)
    failed = int(live_row.get("failed_passes") or 0)
    return f"live pass: A {comp_a}/{team_a} | B {comp_b}/{team_b} failed={failed} {live_row.get('action') or '--'}"


def _pass_candidate_summary_line(summary: dict[str, Any]) -> str:
    attempts = int(summary.get("pass_attempts") or 0)
    completed = int(summary.get("completed_passes") or 0)
    failed = int(summary.get("failed_passes") or 0)
    excluded = int(summary.get("excluded_non_pass_candidates") or 0)
    progressive = int(summary.get("progressive_passes") or summary.get("progressive_pass_candidates") or 0)
    total = int(summary.get("pass_candidates") or attempts + excluded)
    return f"clip pass: att={attempts} comp={completed} fail={failed} excl={excluded} cand={total} prog={progressive}"


def _pass_candidate_line(candidate: dict[str, Any], player_labels: dict[str, str]) -> str:
    source = _candidate_player_label(candidate.get("from_stable_player_id"), player_labels)
    target = _candidate_player_label(candidate.get("to_stable_player_id"), player_labels)
    kind = _pass_candidate_kind(candidate)
    confidence = float(candidate.get("confidence") or 0.0)
    return f"{source}->{target} {kind} {confidence:.2f}"


def _pass_candidate_arrow_label(candidate: dict[str, Any], player_labels: dict[str, str]) -> str:
    source = _candidate_player_label(candidate.get("from_stable_player_id"), player_labels)
    target = _candidate_player_label(candidate.get("to_stable_player_id"), player_labels)
    return f"{source}->{target}?"


def _candidate_player_label(value: Any, player_labels: dict[str, str]) -> str:
    player_id = str(value or "")
    return player_labels.get(player_id, player_id or "?")


def _pass_candidate_kind(candidate: dict[str, Any]) -> str:
    outcome = str(candidate.get("outcome") or "")
    if outcome == "completed_pass":
        return "pass"
    if outcome == "failed_pass":
        return "failed"
    if outcome == "excluded_non_pass":
        return "no-pass"
    pass_type = str(candidate.get("pass_type") or "unknown")
    if pass_type == "same_team_pass":
        return "pass?"
    if pass_type == "turnover_or_interception":
        return "turnover?"
    return "unknown?"


def _pass_candidate_color(candidate: dict[str, Any]) -> tuple[int, int, int]:
    outcome = str(candidate.get("outcome") or "")
    if outcome == "completed_pass":
        team = candidate.get("count_for_team_label") or candidate.get("from_team_label")
        return _stable_bgr_color(team) if team in {"A", "B"} else (60, 220, 60)
    if outcome == "failed_pass":
        return (0, 180, 255)
    if outcome == "excluded_non_pass":
        return (130, 130, 130)
    pass_type = str(candidate.get("pass_type") or "unknown")
    if pass_type == "same_team_pass":
        team = candidate.get("from_team_label")
        return _stable_bgr_color(team) if team in {"A", "B"} else (60, 220, 60)
    if pass_type == "turnover_or_interception":
        return (0, 180, 255)
    return (210, 210, 210)


def _legacy_pass_outcome(candidate: dict[str, Any]) -> str:
    pass_type = str(candidate.get("pass_type") or "")
    if pass_type == "same_team_pass":
        return "completed_pass"
    if pass_type == "turnover_or_interception":
        return "failed_pass"
    return "unknown_pass_attempt"


def _valid_pair(value: Any) -> bool:
    return isinstance(value, list | tuple) and len(value) == 2 and value[0] is not None and value[1] is not None


def _draw_player_stats_panel(frame: Any, stats_rows: list[dict[str, Any]]) -> None:
    if not stats_rows:
        return

    import cv2

    frame_height, frame_width = frame.shape[:2]
    x1 = 12
    y1 = 124
    line_height = 15
    max_rows = max(0, min(len(stats_rows), (frame_height - y1 - 24) // line_height))
    if max_rows <= 0:
        return
    visible_rows = stats_rows[:max_rows]
    font = cv2.FONT_HERSHEY_SIMPLEX
    title_scale = 0.42
    row_scale = 0.36
    thickness = 1
    lines = ["player stats: dist avg peak time q"]
    for row in visible_rows:
        quality = str(row.get("quality") or "?")[:1]
        estimated = float(row.get("estimated_distance_m") or 0.0)
        estimated_marker = "*" if estimated > 0.0 else " "
        lines.append(
            f"{row.get('display_label') or row.get('stable_player_id')}{estimated_marker} "
            f"d={float(row.get('distance_m') or 0.0):4.1f}m "
            f"avg={float(row.get('avg_speed_kmh') or 0.0):4.1f} "
            f"pk={float(row.get('peak_sustained_speed_kmh') or 0.0):4.1f} "
            f"t={float(row.get('playing_time_sec') or 0.0):4.1f}s "
            f"{quality}"
        )
    if max_rows < len(stats_rows):
        lines.append(f"+{len(stats_rows) - max_rows} more")
    widths = [
        cv2.getTextSize(line, font, title_scale if index == 0 else row_scale, thickness)[0][0]
        for index, line in enumerate(lines)
    ]
    width = min(frame_width - 24, max(widths, default=300) + 18)
    height = line_height * len(lines) + 12
    y2 = min(frame_height - 12, y1 + height)
    cv2.rectangle(frame, (x1, y1), (x1 + width, y2), (20, 20, 20), -1)
    cv2.rectangle(frame, (x1, y1), (x1 + width, y2), (180, 180, 180), 1)
    for index, line in enumerate(lines):
        y = y1 + 18 + index * line_height
        color = (245, 245, 245)
        if index > 0 and index <= len(visible_rows):
            color = _stable_bgr_color(visible_rows[index - 1].get("team_label"))
        scale = title_scale if index == 0 else row_scale
        cv2.putText(frame, line, (x1 + 8, y), font, scale, color, thickness, cv2.LINE_AA)


def _count_value(frame_count: dict[str, Any] | None, key: str) -> str:
    if not frame_count:
        return "--"
    value = frame_count.get(key)
    return str(value) if value is not None else "--"


def _draw_frame_stamp(frame: Any, frame_idx: int) -> None:
    import cv2

    label = f"FRAME {frame_idx:06d}"
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.9
    thickness = 2
    padding_x = 14
    padding_y = 10
    (text_width, text_height), baseline = cv2.getTextSize(label, font, font_scale, thickness)
    frame_height, frame_width = frame.shape[:2]
    x2 = frame_width - 18
    y2 = frame_height - 18
    x1 = max(0, x2 - text_width - padding_x * 2)
    y1 = max(0, y2 - text_height - baseline - padding_y * 2)
    cv2.rectangle(frame, (x1, y1), (x2, y2), (20, 20, 20), -1)
    cv2.rectangle(frame, (x1, y1), (x2, y2), (245, 245, 245), 1)
    cv2.putText(
        frame,
        label,
        (x1 + padding_x, y2 - padding_y - baseline),
        font,
        font_scale,
        (245, 245, 245),
        thickness,
        cv2.LINE_AA,
    )


def stabilize_match(
    match_dir: Path,
    video_path: Path,
    pitch: Any,
    tracks: list[dict[str, Any]],
    video_metadata: dict[str, Any],
    *,
    camera_motion: Any | None = None,
    ball_tracks_doc: dict[str, Any] | None = None,
    ball_candidates_doc: dict[str, Any] | None = None,
    write_debug_overlay: bool = True,
    render_stable_overlay: bool = True,
    defer_stable_overlay_render: bool = False,
    enable_identity_diagnostics: bool = True,
    player_label_overrides: dict[str, str] | None = None,
    progress: Callable[[str, float, str, dict[str, Any] | None], None] | None = None,
) -> dict[str, Any]:
    meta_path = match_dir / "match.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    teams = meta.get("teams") if isinstance(meta.get("teams"), list) else []
    tracklet_parameters = {
        "max_internal_gap_sec": 0.7,
        "split_speed_mps": 16.0,
        "min_duration_sec": 0.2,
        "min_positions": 4,
    }
    if progress:
        progress("stabilization", 91.0, "Splitting raw tracks into tracklets.", {"current": len(tracks), "unit": "tracks"})
    tracklets, rejected = split_tracks_into_tracklets(tracks, **tracklet_parameters)
    if progress:
        progress(
            "stabilization",
            91.4,
            f"Sampling visual appearance for {len(tracklets)} tracklets.",
            {"current": len(tracklets), "unit": "tracklets"},
        )
    sample_tracklet_appearance(video_path, tracklets)
    tracklets = split_tracklets_by_appearance_changes(tracklets)
    if progress:
        progress(
            "stabilization",
            91.8,
            f"Clustering teams for {len(tracklets)} cleaned tracklets.",
            {"current": len(tracklets), "unit": "tracklets"},
    )
    team_clusters = cluster_tracklet_teams(tracklets, teams)
    goalkeeper_role_summary = apply_goalkeeper_role_adjustments(tracklets, pitch_length_m=float(pitch.length_m))
    tracklets_doc = build_tracklets_document(
        tracklets,
        rejected,
        raw_tracks_count=len(tracks),
        parameters=tracklet_parameters,
    )
    tracking_quality_report = build_tracking_quality_report(
        tracklets,
        rejected,
        raw_tracks_count=len(tracks),
        parameters=tracklet_parameters,
    )
    if progress:
        progress(
            "stabilization",
            92.2,
            "Resolving global player identities.",
            {"current": len(tracklets), "unit": "tracklets"},
        )
    match_phase_config_path = match_dir / "match_phase_config.json"
    match_phase_config = (
        json.loads(match_phase_config_path.read_text(encoding="utf-8"))
        if match_phase_config_path.exists()
        else None
    )
    global_identity = resolve_conservative_identity(
        tracklets,
        raw_tracks_count=len(tracks),
        rejected_tracklets_count=len(rejected),
        pitch_width_m=float(pitch.width_m),
        pitch_length_m=float(pitch.length_m),
        fps=float(video_metadata.get("fps") or 25.0),
        pitch_polygon=pitch.polygon_np,
        match_phase_config=match_phase_config,
        progress=progress,
    )
    if progress:
        progress(
            "stabilization",
            93.0,
            "Building stable player stats and reports.",
            {"current": int((global_identity.get("summary") or {}).get("stable_players") or 0), "unit": "players"},
        )
    stable_doc = build_stable_players_from_global_identity(global_identity)
    _apply_player_label_overrides(stable_doc, player_label_overrides)
    ball_tracks_for_refine = _rebuild_ball_tracks_from_candidates(
        ball_tracks_doc,
        ball_candidates_doc,
        fps=float(video_metadata.get("fps") or 25.0),
    )
    refined_ball_tracks_doc = refine_ball_tracks_against_players(
        ball_tracks_for_refine,
        stable_doc,
        fps=float(video_metadata.get("fps") or 25.0),
    )
    if refined_ball_tracks_doc is not None and ball_tracks_doc is not None:
        (match_dir / "ball_tracks.json").write_text(json.dumps(refined_ball_tracks_doc, indent=2), encoding="utf-8")
    frame_detection_counts = build_frame_detection_counts_from_global_identity(
        global_identity,
        fps=float(video_metadata.get("fps") or 25.0),
    )
    frame_detection_counts = apply_stable_overlay_visual_counts(
        frame_detection_counts,
        stable_doc,
        pitch.polygon_np,
        fps=float(video_metadata.get("fps") or 25.0),
        camera_motion=camera_motion,
    )
    stable_doc["frame_detection_summary"] = frame_detection_counts["summary"]
    stable_doc["frame_detection_counts"] = frame_detection_counts
    movement_stats = build_movement_stats_document(stable_doc)
    stable_doc["movement_stats_summary"] = movement_stats["summary"]
    player_stats = build_player_stats_document(stable_doc)
    stable_doc["player_stats_summary"] = player_stats["summary"]
    existing_team_config_path = match_dir / "team_config.json"
    existing_team_config = (
        json.loads(existing_team_config_path.read_text(encoding="utf-8"))
        if existing_team_config_path.exists()
        else None
    )
    team_config = build_team_config_document(meta, team_clusters, stable_doc, existing_team_config)
    team_stats = build_team_stats_document(player_stats, team_config)
    stable_doc["team_stats_summary"] = team_stats["summary"]
    player_heatmaps = build_player_heatmaps_document(stable_doc, match_dir)
    stable_doc["player_heatmaps_summary"] = player_heatmaps["summary"]
    change_artifacts = write_change_candidate_artifacts(match_dir, stable_doc)
    stable_overlay_artifacts: dict[str, str] = {}
    should_render_stable_overlay = bool(render_stable_overlay and not defer_stable_overlay_render)
    if should_render_stable_overlay and progress:
        progress(
            "stable_overlay_render",
            94.0,
            "Rendering stable overlay preview.",
            {"artifact": "stable_overlay_preview.mp4"},
        )
    if should_render_stable_overlay:
        write_stable_overlay(
            video_path,
            match_dir,
            stable_doc,
            pitch.polygon_np,
            fps=float(video_metadata.get("fps") or 25.0),
            frame_size=(int(video_metadata.get("width") or 0), int(video_metadata.get("height") or 0)),
            camera_motion=camera_motion,
            ball_tracks_doc=refined_ball_tracks_doc,
            pitch_homography=pitch.homography(),
        )
        stable_overlay_artifacts["stable_overlay_preview"] = "stable_overlay_preview.mp4"
    elif not render_stable_overlay and progress:
        progress(
            "stable_overlay_render",
            94.0,
            "Skipping stable overlay preview render.",
            {"artifact": "stable_overlay_preview.mp4", "skipped": True},
        )
    debug_overlay_artifacts: dict[str, str] = {}
    if write_debug_overlay:
        if progress:
            progress(
                "stable_overlay_render",
                95.0,
                "Rendering debug identity overlay.",
                {"artifact": "debug_identity_overlay.mp4"},
            )
        write_stable_overlay(
            video_path,
            match_dir,
            stable_doc,
            pitch.polygon_np,
            fps=float(video_metadata.get("fps") or 25.0),
            frame_size=(int(video_metadata.get("width") or 0), int(video_metadata.get("height") or 0)),
            output_name="debug_identity_overlay.mp4",
            include_untrusted=True,
            camera_motion=camera_motion,
            ball_tracks_doc=refined_ball_tracks_doc,
            pitch_homography=pitch.homography(),
        )
        debug_overlay_artifacts["debug_identity_overlay"] = "debug_identity_overlay.mp4"
    if progress:
        progress("stabilization", 93.0, "Writing stable player and identity reports.", None)
    public_stable_doc = _strip_overlay_positions(stable_doc)
    global_identity_parameters = global_identity.get("parameters") if isinstance(global_identity.get("parameters"), dict) else {}
    parameters = {
        "max_internal_gap_sec": 0.7,
        "split_speed_mps": 16.0,
        "max_link_gap_sec": 3.0,
        "max_link_speed_mps": 9.5,
        "max_link_distance_m": 12.0,
        "team_method": "torso_color_kmeans",
        "goalkeeper_role_adjustment": goalkeeper_role_summary,
        "max_interpolation_gap_frames": MAX_INTERPOLATION_GAP_FRAMES,
        "max_interpolation_gap_sec": MAX_INTERPOLATION_GAP_SEC,
        "max_interpolation_speed_mps": MAX_INTERPOLATION_SPEED_MPS,
        "identity_resolver": "conservative_identity_v2",
        "identity_semantics": "stint_first",
        "global_max_assignment_speed_mps": global_identity["parameters"]["max_assignment_speed_mps"],
        "global_max_prediction_sec": global_identity["parameters"]["max_prediction_sec"],
        "global_substitution_gap_sec": global_identity["parameters"]["substitution_gap_sec"],
        "global_identity_parameters": global_identity_parameters,
        "camera_motion_compensation": bool(getattr(camera_motion, "enabled", False)) if camera_motion is not None else False,
        "camera_motion_reference_frame": getattr(camera_motion, "reference_frame", None) if camera_motion is not None else None,
        "render_stable_overlay": bool(render_stable_overlay),
        "stable_overlay_render_deferred": bool(render_stable_overlay and defer_stable_overlay_render),
    }
    report = build_stabilization_report(
        stable_doc=public_stable_doc,
        rejected_tracklets=rejected,
        team_clusters=team_clusters,
        parameters=parameters,
    )
    global_identity_report = build_global_identity_report(
        global_identity,
        frame_detection_counts,
        parameters=parameters,
    )
    analysis_quality_report = build_analysis_quality_report(
        frame_detection_counts=frame_detection_counts,
        stable_players=stable_doc,
        global_identity_report=global_identity_report,
        tracking_quality_report=tracking_quality_report,
        movement_stats=movement_stats,
        player_stats=player_stats,
        team_stats=team_stats,
    )

    identity_diagnostics, identity_diagnostics_warning = _build_identity_diagnostics_safely(
        match_dir,
        tracklets,
        rejected,
        global_identity,
        fps=float(video_metadata.get("fps") or 25.0),
        enabled=enable_identity_diagnostics,
    )

    (match_dir / "stable_players.json").write_text(json.dumps(public_stable_doc, indent=2), encoding="utf-8")
    (match_dir / "global_identity.json").write_text(json.dumps(global_identity, indent=2), encoding="utf-8")
    (match_dir / "global_identity_report.json").write_text(json.dumps(global_identity_report, indent=2), encoding="utf-8")
    (match_dir / "analysis_quality_report.json").write_text(json.dumps(analysis_quality_report, indent=2), encoding="utf-8")
    (match_dir / "team_clusters.json").write_text(json.dumps(team_clusters, indent=2), encoding="utf-8")
    (match_dir / "frame_detection_counts.json").write_text(json.dumps(frame_detection_counts, indent=2), encoding="utf-8")
    (match_dir / "movement_stats.json").write_text(json.dumps(movement_stats, indent=2), encoding="utf-8")
    (match_dir / "player_stats.json").write_text(json.dumps(player_stats, indent=2), encoding="utf-8")
    (match_dir / "player_heatmaps.json").write_text(json.dumps(player_heatmaps, indent=2), encoding="utf-8")
    (match_dir / "team_config.json").write_text(json.dumps(team_config, indent=2), encoding="utf-8")
    (match_dir / "team_stats.json").write_text(json.dumps(team_stats, indent=2), encoding="utf-8")
    (match_dir / "tracklets.json").write_text(json.dumps(tracklets_doc, indent=2), encoding="utf-8")
    (match_dir / "tracking_quality_report.json").write_text(json.dumps(tracking_quality_report, indent=2), encoding="utf-8")
    (match_dir / "stabilization_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    identity_diagnostics_artifacts: dict[str, str] = {}
    for artifact_name, document in identity_diagnostics.items():
        filename = f"{artifact_name}.json"
        (match_dir / filename).write_text(json.dumps(document, indent=2), encoding="utf-8")
        identity_diagnostics_artifacts[artifact_name] = filename
    return {
        "stable_players": public_stable_doc,
        "stable_players_overlay_doc": stable_doc,
        "global_identity": global_identity,
        "global_identity_report": global_identity_report,
        "analysis_quality_report": analysis_quality_report,
        "team_clusters": team_clusters,
        "frame_detection_counts": frame_detection_counts,
        "movement_stats": movement_stats,
        "player_stats": player_stats,
        "player_heatmaps": player_heatmaps,
        "team_config": team_config,
        "team_stats": team_stats,
        "change_candidates": change_artifacts["change_candidates"],
        "change_review_report": change_artifacts["change_review_report"],
        "tracklets": tracklets_doc,
        "tracking_quality_report": tracking_quality_report,
        "stabilization_report": report,
        "identity_diagnostics": identity_diagnostics,
        "identity_diagnostics_warning": identity_diagnostics_warning,
        "refined_ball_tracks": refined_ball_tracks_doc,
        "artifacts": {
            "stable_players": "stable_players.json",
            "global_identity": "global_identity.json",
            "global_identity_report": "global_identity_report.json",
            "analysis_quality_report": "analysis_quality_report.json",
            "stabilization_report": "stabilization_report.json",
            **stable_overlay_artifacts,
            **debug_overlay_artifacts,
            "team_clusters": "team_clusters.json",
            "frame_detection_counts": "frame_detection_counts.json",
            "movement_stats": "movement_stats.json",
            "player_stats": "player_stats.json",
            "player_heatmaps": "player_heatmaps.json",
            "team_config": "team_config.json",
            "team_stats": "team_stats.json",
            **change_artifacts["artifacts"],
            "tracklets": "tracklets.json",
            "tracking_quality_report": "tracking_quality_report.json",
            **identity_diagnostics_artifacts,
        },
    }


def _build_identity_diagnostics_safely(
    match_dir: Path,
    tracklets: list[dict[str, Any]],
    rejected_tracklets: list[dict[str, Any]],
    global_identity: dict[str, Any],
    *,
    fps: float,
    enabled: bool,
) -> tuple[dict[str, dict[str, Any]], str | None]:
    if not enabled:
        return {}, None
    try:
        manual_assignments_path = match_dir / "player_identity_assignments.json"
        manual_assignments_doc = (
            json.loads(manual_assignments_path.read_text(encoding="utf-8"))
            if manual_assignments_path.exists()
            else None
        )
        documents = build_identity_diagnostics(
            tracklets,
            rejected_tracklets,
            global_identity,
            fps=fps,
            manual_assignments_doc=manual_assignments_doc,
        )
    except Exception as exc:
        return {}, f"Shadow identity diagnostics failed without affecting identity outputs: {exc}"
    try:
        documents["identity_stitching_candidates"] = build_shadow_stitching_candidates(
            tracklets,
            documents["identity_tracklet_quality"],
            documents["identity_occlusion_events"],
            global_identity,
            fps=fps,
        )
    except Exception as exc:
        return documents, f"Shadow identity stitching candidates failed without affecting identity outputs: {exc}"
    try:
        documents["identity_occlusion_assignments"] = build_shadow_occlusion_assignments(
            tracklets,
            documents["identity_tracklet_quality"],
            documents["identity_occlusion_events"],
            global_identity,
            fps=fps,
        )
    except Exception as exc:
        return documents, f"Shadow joint occlusion assignment failed without affecting identity outputs: {exc}"
    try:
        documents.update(
            build_shadow_offline_identity(
                tracklets,
                documents["identity_tracklet_quality"],
                documents["identity_stitching_candidates"],
                documents["identity_occlusion_assignments"],
                global_identity,
                fps=fps,
                fragmentation_doc=documents.get("identity_fragmentation_report"),
            )
        )
    except Exception as exc:
        return documents, f"Shadow offline identity resolver failed without affecting identity outputs: {exc}"
    return documents, None


def load_stable_review(match_path: Path) -> dict[str, Any]:
    stable_path = match_path / "stable_players.json"
    if not stable_path.exists():
        raise FileNotFoundError("stable_players.json not found. Run analysis first.")
    stable_doc = json.loads(stable_path.read_text(encoding="utf-8"))
    report_path = match_path / "stabilization_report.json"
    global_report_path = match_path / "global_identity_report.json"
    clusters_path = match_path / "team_clusters.json"
    movement_stats_path = match_path / "movement_stats.json"
    player_stats_path = match_path / "player_stats.json"
    player_heatmaps_path = match_path / "player_heatmaps.json"
    team_config_path = match_path / "team_config.json"
    team_stats_path = match_path / "team_stats.json"
    return {
        "stable_players": stable_doc,
        "stabilization_report": json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else None,
        "global_identity_report": json.loads(global_report_path.read_text(encoding="utf-8")) if global_report_path.exists() else None,
        "team_clusters": json.loads(clusters_path.read_text(encoding="utf-8")) if clusters_path.exists() else None,
        "movement_stats": json.loads(movement_stats_path.read_text(encoding="utf-8")) if movement_stats_path.exists() else None,
        "player_stats": json.loads(player_stats_path.read_text(encoding="utf-8")) if player_stats_path.exists() else None,
        "player_heatmaps": json.loads(player_heatmaps_path.read_text(encoding="utf-8")) if player_heatmaps_path.exists() else None,
        "team_config": json.loads(team_config_path.read_text(encoding="utf-8")) if team_config_path.exists() else None,
        "team_stats": json.loads(team_stats_path.read_text(encoding="utf-8")) if team_stats_path.exists() else None,
    }


def save_stable_review(match_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    review = load_stable_review(match_path)
    stable_doc = review["stable_players"]
    players = stable_doc.get("players") if isinstance(stable_doc.get("players"), list) else []
    if payload.get("swap_teams"):
        for player in players:
            if player.get("team_label") == "A":
                player["team_label"] = "B"
            elif player.get("team_label") == "B":
                player["team_label"] = "A"
    updates = payload.get("updates") if isinstance(payload.get("updates"), list) else []
    for update in updates:
        if not isinstance(update, dict):
            continue
        target = str(update.get("stable_subject_id") or update.get("stable_player_id") or "")
        for player in players:
            if target not in {str(player.get("stable_subject_id")), str(player.get("stable_player_id"))}:
                continue
            if update.get("team_label") in {"A", "B", "U"}:
                player["team_label"] = update["team_label"]
            if "team_id" in update:
                player["team_id"] = update.get("team_id")
            if "team_name" in update:
                player["team_name"] = update.get("team_name") or "Unknown"
            if update.get("status") in {"active", "ignore", "referee", "false_positive", "unknown"}:
                player["status"] = update["status"]
    renumber_stable_players(players)
    stable_doc["updated_at"] = now_iso()
    stable_doc["summary"]["team_counts"] = _team_counts(players)
    stable_doc["summary"]["stable_players"] = len(players)
    meta_path = match_path / "match.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    clusters_path = match_path / "team_clusters.json"
    team_clusters = json.loads(clusters_path.read_text(encoding="utf-8")) if clusters_path.exists() else {}
    team_config_path = match_path / "team_config.json"
    existing_team_config = json.loads(team_config_path.read_text(encoding="utf-8")) if team_config_path.exists() else None
    movement_stats = build_movement_stats_document(stable_doc)
    player_stats = build_player_stats_document(stable_doc)
    team_config = build_team_config_document(meta, team_clusters, stable_doc, existing_team_config)
    team_stats = build_team_stats_document(player_stats, team_config)
    stable_doc["movement_stats_summary"] = movement_stats["summary"]
    stable_doc["player_stats_summary"] = player_stats["summary"]
    stable_doc["team_stats_summary"] = team_stats["summary"]
    (match_path / "stable_players.json").write_text(json.dumps(stable_doc, indent=2), encoding="utf-8")
    (match_path / "movement_stats.json").write_text(json.dumps(movement_stats, indent=2), encoding="utf-8")
    (match_path / "player_stats.json").write_text(json.dumps(player_stats, indent=2), encoding="utf-8")
    (match_path / "team_config.json").write_text(json.dumps(team_config, indent=2), encoding="utf-8")
    (match_path / "team_stats.json").write_text(json.dumps(team_stats, indent=2), encoding="utf-8")
    return load_stable_review(match_path)


def load_team_config_review(match_path: Path) -> dict[str, Any]:
    meta_path = match_path / "match.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    stable_path = match_path / "stable_players.json"
    if not stable_path.exists():
        raise FileNotFoundError("stable_players.json not found. Run analysis first.")
    stable_doc = json.loads(stable_path.read_text(encoding="utf-8"))
    clusters_path = match_path / "team_clusters.json"
    team_clusters = json.loads(clusters_path.read_text(encoding="utf-8")) if clusters_path.exists() else {}
    team_config_path = match_path / "team_config.json"
    team_config = (
        json.loads(team_config_path.read_text(encoding="utf-8"))
        if team_config_path.exists()
        else build_team_config_document(meta, team_clusters, stable_doc)
    )
    player_stats_path = match_path / "player_stats.json"
    player_stats = (
        json.loads(player_stats_path.read_text(encoding="utf-8"))
        if player_stats_path.exists()
        else build_player_stats_document(stable_doc)
    )
    team_stats_path = match_path / "team_stats.json"
    team_stats = (
        json.loads(team_stats_path.read_text(encoding="utf-8"))
        if team_stats_path.exists()
        else build_team_stats_document(player_stats, team_config)
    )
    return {
        "team_config": team_config,
        "team_stats": team_stats,
        "player_stats": player_stats,
        "team_clusters": team_clusters,
    }


def save_team_config_review(match_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    meta_path = match_path / "match.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    stable_path = match_path / "stable_players.json"
    if not stable_path.exists():
        raise FileNotFoundError("stable_players.json not found. Run analysis first.")
    stable_doc = json.loads(stable_path.read_text(encoding="utf-8"))
    players = stable_doc.get("players") if isinstance(stable_doc.get("players"), list) else []
    clusters_path = match_path / "team_clusters.json"
    team_clusters = json.loads(clusters_path.read_text(encoding="utf-8")) if clusters_path.exists() else {}
    team_config_path = match_path / "team_config.json"
    existing_team_config = (
        json.loads(team_config_path.read_text(encoding="utf-8"))
        if team_config_path.exists()
        else build_team_config_document(meta, team_clusters, stable_doc)
    )
    teams = existing_team_config.get("teams") if isinstance(existing_team_config.get("teams"), list) else []
    by_label = {
        str(item.get("team_label")): dict(item)
        for item in teams
        if isinstance(item, dict) and item.get("team_label") in {"A", "B"}
    }
    for label in ["A", "B"]:
        by_label.setdefault(label, {"team_label": label, "team_name": f"Team {label}", "locked": False})

    updates = payload.get("teams") if isinstance(payload.get("teams"), list) else []
    for update in updates:
        if not isinstance(update, dict) or update.get("team_label") not in {"A", "B"}:
            continue
        row = by_label[str(update["team_label"])]
        if "team_id" in update:
            row["team_id"] = update.get("team_id") or None
        if "team_name" in update:
            row["team_name"] = update.get("team_name") or f"Team {row['team_label']}"
        if "display_color" in update:
            row["display_color"] = update.get("display_color") or None
        if "detected_color_hex" in update:
            row["detected_color_hex"] = update.get("detected_color_hex") or row.get("detected_color_hex")
        if "locked" in update:
            row["locked"] = bool(update.get("locked"))
            row["assignment_source"] = "manual_lock" if row["locked"] else "review_unlocked"
        if "notes" in update:
            row["notes"] = str(update.get("notes") or "")
        if isinstance(update.get("goalkeeper_exceptions"), list):
            row["goalkeeper_exceptions"] = update["goalkeeper_exceptions"]

    existing_team_config["teams"] = [by_label["A"], by_label["B"]]
    existing_team_config["updated_at"] = now_iso()
    existing_team_config["locked"] = any(bool(item.get("locked")) for item in existing_team_config["teams"])

    for player in players:
        label = str(player.get("team_label") or "U")
        config = by_label.get(label)
        if not config:
            continue
        player["team_id"] = config.get("team_id")
        player["team_name"] = config.get("team_name") or f"Team {label}"

    movement_stats = build_movement_stats_document(stable_doc)
    player_stats = build_player_stats_document(stable_doc)
    team_config = build_team_config_document(meta, team_clusters, stable_doc, existing_team_config)
    team_stats = build_team_stats_document(player_stats, team_config)
    stable_doc["updated_at"] = now_iso()
    stable_doc["movement_stats_summary"] = movement_stats["summary"]
    stable_doc["player_stats_summary"] = player_stats["summary"]
    stable_doc["team_stats_summary"] = team_stats["summary"]

    stable_path.write_text(json.dumps(stable_doc, indent=2), encoding="utf-8")
    (match_path / "movement_stats.json").write_text(json.dumps(movement_stats, indent=2), encoding="utf-8")
    (match_path / "player_stats.json").write_text(json.dumps(player_stats, indent=2), encoding="utf-8")
    team_config_path.write_text(json.dumps(team_config, indent=2), encoding="utf-8")
    (match_path / "team_stats.json").write_text(json.dumps(team_stats, indent=2), encoding="utf-8")
    return load_team_config_review(match_path)
