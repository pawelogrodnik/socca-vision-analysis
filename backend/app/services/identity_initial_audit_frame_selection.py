from __future__ import annotations

from collections import defaultdict
import math
import random
from typing import Any

from app.services.identity_jersey_number_common import canonical_digest


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "identity_initial_audit_frame_selection"
ALGORITHM_VERSION = "0.1.0"

DEFAULT_PARAMETERS: dict[str, Any] = {
    "target_frame_count": 8,
    "maximum_frame_count": 10,
    "candidate_stride_frames": 15,
    "minimum_visible_players": 7,
    "minimum_frame_gap_sec": 4.0,
    "minimum_selection_score": 0.44,
    "switch_risk_window_sec": 0.75,
    "edge_margin_ratio": 0.015,
    "overlap_iou_threshold": 0.10,
    "weights": {
        "visible_players": 0.20,
        "team_balance": 0.08,
        "bbox_size": 0.15,
        "low_overlap": 0.15,
        "low_edge_cutting": 0.10,
        "tracklet_continuity": 0.12,
        "low_blur": 0.10,
        "low_switch_suspicion": 0.06,
        "camera_reliability": 0.04,
    },
}


def collect_candidate_frame_numbers(
    global_identity: dict[str, Any],
    *,
    stride_frames: int = 15,
) -> list[int]:
    stride = max(1, int(stride_frames))
    frames = sorted(
        {
            int(row["frame"])
            for row in global_identity.get("frames") or []
            if isinstance(row, dict) and isinstance(row.get("frame"), (int, float))
        }
    )
    if not frames:
        frames = sorted(
            {
                int(position["frame"])
                for slot in global_identity.get("slots") or []
                for position in slot.get("overlay_positions") or []
                if isinstance(position, dict)
                and isinstance(position.get("frame"), (int, float))
            }
        )
    return [frame for frame in frames if frame % stride == 0]


def build_initial_identity_audit_frame_selection(
    global_identity: dict[str, Any],
    tracklets_document: dict[str, Any],
    analysis_report: dict[str, Any],
    *,
    camera_motion_report: dict[str, Any] | None = None,
    frame_visual_metrics: dict[int, dict[str, Any]] | None = None,
    seeded_subject_ids: set[str] | None = None,
    parameters: dict[str, Any] | None = None,
    generated_at: str | None = None,
    artifact_directory: str = "frames",
    minimum_frame: int | None = None,
    maximum_frame: int | None = None,
) -> dict[str, Any]:
    config = _merged_parameters(parameters)
    video = analysis_report.get("video") or {}
    fps = max(1.0, float(video.get("fps") or 30.0))
    width = max(1, int(video.get("width") or 1))
    height = max(1, int(video.get("height") or 1))
    duration_sec = max(
        0.0,
        float(video.get("duration_sec") or 0.0),
        float(video.get("frame_count") or 0) / fps,
    )
    observations = _observations_by_frame(global_identity)
    tracklet_ranges = _tracklet_ranges(tracklets_document)
    switch_frames = _switch_risk_frames(global_identity)
    camera_samples = _camera_samples(camera_motion_report or {})
    visual_metrics = frame_visual_metrics or {}
    blur_scores = _normalized_blur_scores(visual_metrics)
    candidate_frames = collect_candidate_frame_numbers(
        global_identity,
        stride_frames=int(config["candidate_stride_frames"]),
    )
    if minimum_frame is not None:
        candidate_frames = [
            frame for frame in candidate_frames if frame >= int(minimum_frame)
        ]
    if maximum_frame is not None:
        candidate_frames = [
            frame for frame in candidate_frames if frame <= int(maximum_frame)
        ]
    candidates: list[dict[str, Any]] = []
    for frame in candidate_frames:
        frame_observations = observations.get(frame) or []
        if len(frame_observations) < int(config["minimum_visible_players"]):
            continue
        components = _frame_score_components(
            frame=frame,
            observations=frame_observations,
            tracklet_ranges=tracklet_ranges,
            switch_frames=switch_frames,
            camera_samples=camera_samples,
            blur_score=blur_scores.get(frame, 0.70),
            fps=fps,
            width=width,
            height=height,
            config=config,
        )
        intrinsic_score = _weighted_score(components, config["weights"])
        candidates.append(
            {
                "frame": frame,
                "time_sec": round(frame / fps, 3),
                "intrinsic_score": round(intrinsic_score, 6),
                "score_components": components,
                "visible_detections": frame_observations,
                "capture_domain": _capture_domain(frame, camera_samples),
            }
        )

    selected = _greedy_select(
        candidates,
        duration_sec=duration_sec,
        seeded_subject_ids=seeded_subject_ids or set(),
        config=config,
    )
    baseline = _deterministic_random_baseline(
        candidates,
        selected_count=len(selected),
        minimum_frame_gap_sec=float(config["minimum_frame_gap_sec"]),
        fps=fps,
        seed_material={
            "candidate_frames": [row["frame"] for row in candidates],
            "parameters": config,
        },
    )
    selected_mean = _mean(row["intrinsic_score"] for row in selected)
    baseline_mean = _mean(row["intrinsic_score"] for row in baseline)
    selected_rows = [
        {
            **row,
            "selection_rank": index,
            "selection_reasons": _selection_reasons(row),
            "full_frame_artifact": (
                f"{artifact_directory}/frame-{int(row['frame']):06d}.jpg"
            ),
            "thumbnail_artifact": (
                f"{artifact_directory}/frame-{int(row['frame']):06d}-thumb.jpg"
            ),
        }
        for index, row in enumerate(selected, start=1)
    ]
    selection_digest = canonical_digest(
        [
            {
                "frame": row["frame"],
                "score": row["selection_score"],
                "visible_subject_ids": sorted(
                    detection["stable_subject_id"]
                    for detection in row["visible_detections"]
                ),
            }
            for row in selected_rows
        ]
    )
    unique_subjects = {
        detection["stable_subject_id"]
        for row in selected_rows
        for detection in row["visible_detections"]
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "mode": "initial_identity_audit_frame_selection_shadow",
        "algorithm": {
            "name": ALGORITHM_NAME,
            "version": ALGORITHM_VERSION,
            "parameters": config,
        },
        "source": {
            "analysis_run_id": analysis_report.get("run_id"),
            "global_identity_digest": canonical_digest(global_identity),
            "tracklets_digest": canonical_digest(tracklets_document),
            "analysis_report_digest": canonical_digest(analysis_report),
            "camera_motion_digest": (
                canonical_digest(camera_motion_report)
                if camera_motion_report
                else None
            ),
            "frame_range": {
                "minimum_frame": minimum_frame,
                "maximum_frame": maximum_frame,
            },
        },
        "video": {
            "fps": fps,
            "frame_count": int(video.get("frame_count") or 0),
            "duration_sec": round(duration_sec, 3),
            "width": width,
            "height": height,
        },
        "summary": {
            "candidate_frames": len(candidates),
            "selected_frames": len(selected_rows),
            "maximum_frame_count": int(config["maximum_frame_count"]),
            "unique_subjects_visible": len(unique_subjects),
            "selected_mean_intrinsic_score": round(selected_mean, 6),
            "random_baseline_mean_intrinsic_score": round(baseline_mean, 6),
            "easier_than_random_baseline": selected_mean >= baseline_mean,
            "near_duplicate_pairs": _near_duplicate_pairs(
                selected_rows,
                minimum_gap_sec=float(config["minimum_frame_gap_sec"]),
            ),
        },
        "selected_frames": selected_rows,
        "baseline_comparison": {
            "method": "deterministic_seeded_random_with_spacing",
            "frames": [row["frame"] for row in baseline],
            "mean_intrinsic_score": round(baseline_mean, 6),
            "selected_advantage": round(selected_mean - baseline_mean, 6),
        },
        "selection_digest": selection_digest,
        "safety": {
            "read_only": True,
            "production_identity_untouched": True,
            "candidate_identity_untouched": True,
            "yolo_not_required": True,
            "raw_coordinates_required_from_operator": False,
        },
    }


def _merged_parameters(overrides: dict[str, Any] | None) -> dict[str, Any]:
    merged = {
        **DEFAULT_PARAMETERS,
        "weights": dict(DEFAULT_PARAMETERS["weights"]),
    }
    for key, value in (overrides or {}).items():
        if key == "weights":
            merged["weights"].update(value or {})
        else:
            merged[key] = value
    merged["maximum_frame_count"] = min(
        10,
        max(1, int(merged["maximum_frame_count"])),
    )
    merged["target_frame_count"] = min(
        int(merged["maximum_frame_count"]),
        max(1, int(merged["target_frame_count"])),
    )
    return merged


def _observations_by_frame(
    global_identity: dict[str, Any],
) -> dict[int, list[dict[str, Any]]]:
    observations: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for slot in global_identity.get("slots") or []:
        subject_id = str(
            slot.get("stable_subject_id")
            or slot.get("stable_player_id")
            or slot.get("slot_id")
            or ""
        )
        if not subject_id:
            continue
        for position in slot.get("overlay_positions") or []:
            if position.get("status") != "detected":
                continue
            if position.get("visual_trusted") is False:
                continue
            if str(position.get("play_area_status") or "inside") not in {
                "inside",
                "inside_play",
                "inside_pitch",
            }:
                continue
            bbox = _valid_bbox(position.get("bbox_xyxy"))
            if bbox is None:
                continue
            frame = int(position.get("frame") or 0)
            observations[frame].append(
                {
                    "stable_subject_id": subject_id,
                    "stable_player_id": slot.get("stable_player_id"),
                    "slot_id": slot.get("slot_id"),
                    "team_label": str(slot.get("team_label") or "U"),
                    "role": slot.get("role"),
                    "tracklet_id": position.get("tracklet_id"),
                    "raw_track_id": position.get("raw_track_id"),
                    "bbox_xyxy": bbox,
                    "confidence": round(float(position.get("confidence") or 0.0), 6),
                    "source": position.get("source"),
                    "stint_id": position.get("stint_id"),
                }
            )
    for rows in observations.values():
        rows.sort(key=lambda row: (row["team_label"], row["stable_subject_id"]))
    return dict(observations)


def _tracklet_ranges(
    tracklets_document: dict[str, Any],
) -> dict[str, tuple[int, int]]:
    ranges: dict[str, tuple[int, int]] = {}
    for tracklet in tracklets_document.get("tracklets") or []:
        tracklet_id = str(tracklet.get("tracklet_id") or "")
        frames = [
            int(position["frame"])
            for position in tracklet.get("positions_m") or []
            if isinstance(position, dict)
            and isinstance(position.get("frame"), (int, float))
        ]
        if tracklet_id and frames:
            ranges[tracklet_id] = (min(frames), max(frames))
    return ranges


def _switch_risk_frames(global_identity: dict[str, Any]) -> list[int]:
    frames: set[int] = set()
    event_fields = (
        "blocked_identity_switches",
        "suspicious_assignments",
        "rejected_candidates",
        "identity_events",
        "risky_links",
    )
    for slot in global_identity.get("slots") or []:
        for field in event_fields:
            _collect_event_frames(slot.get(field), frames)
    return sorted(frames)


def _collect_event_frames(value: Any, frames: set[int]) -> None:
    if isinstance(value, list):
        for item in value:
            _collect_event_frames(item, frames)
        return
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        if key in {"frame", "start_frame", "end_frame", "source_frame", "target_frame"}:
            if isinstance(item, (int, float)):
                frames.add(int(item))
        elif isinstance(item, (dict, list)):
            _collect_event_frames(item, frames)


def _camera_samples(camera_motion_report: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        [
            sample
            for sample in camera_motion_report.get("samples") or []
            if isinstance(sample, dict)
            and isinstance(sample.get("frame"), (int, float))
        ],
        key=lambda sample: int(sample["frame"]),
    )


def _normalized_blur_scores(
    metrics: dict[int, dict[str, Any]],
) -> dict[int, float]:
    values = sorted(
        float(row["blur_variance"])
        for row in metrics.values()
        if isinstance(row.get("blur_variance"), (int, float))
        and math.isfinite(float(row["blur_variance"]))
    )
    if not values:
        return {}
    low = _percentile(values, 0.10)
    high = max(low + 1e-6, _percentile(values, 0.90))
    return {
        int(frame): round(
            _clamp((float(row.get("blur_variance") or low) - low) / (high - low)),
            6,
        )
        for frame, row in metrics.items()
    }


def _frame_score_components(
    *,
    frame: int,
    observations: list[dict[str, Any]],
    tracklet_ranges: dict[str, tuple[int, int]],
    switch_frames: list[int],
    camera_samples: list[dict[str, Any]],
    blur_score: float,
    fps: float,
    width: int,
    height: int,
    config: dict[str, Any],
) -> dict[str, float]:
    team_counts = {
        team: sum(row["team_label"] == team for row in observations)
        for team in ("A", "B")
    }
    sizes = [
        _clamp(((bbox[3] - bbox[1]) / height) / 0.12)
        for bbox in (row["bbox_xyxy"] for row in observations)
    ]
    edge_margin_x = width * float(config["edge_margin_ratio"])
    edge_margin_y = height * float(config["edge_margin_ratio"])
    edge_safe = [
        float(
            bbox[0] > edge_margin_x
            and bbox[1] > edge_margin_y
            and bbox[2] < width - edge_margin_x
            and bbox[3] < height - edge_margin_y
        )
        for bbox in (row["bbox_xyxy"] for row in observations)
    ]
    overlaps = 0
    pairs = 0
    for index, left in enumerate(observations):
        for right in observations[index + 1 :]:
            pairs += 1
            if _bbox_iou(left["bbox_xyxy"], right["bbox_xyxy"]) >= float(
                config["overlap_iou_threshold"]
            ):
                overlaps += 1
    continuity = []
    for row in observations:
        tracklet_range = tracklet_ranges.get(str(row.get("tracklet_id") or ""))
        if tracklet_range is None:
            continuity.append(0.5)
            continue
        start_frame, end_frame = tracklet_range
        edge_distance = min(frame - start_frame, end_frame - frame)
        continuity.append(_clamp(edge_distance / max(1.0, fps * 0.5)))
    risk_window = max(1, round(float(config["switch_risk_window_sec"]) * fps))
    switch_safe = float(
        not any(abs(frame - switch_frame) <= risk_window for switch_frame in switch_frames)
    )
    camera = _nearest_camera_sample(frame, camera_samples)
    camera_reliability = 0.70
    if camera is not None:
        status = str(camera.get("status") or "")
        inlier_ratio = float(camera.get("inlier_ratio") or 0.0)
        status_score = 1.0 if status in {"ok", "interpolated"} else 0.35
        camera_reliability = _clamp(0.55 * status_score + 0.45 * inlier_ratio)
    return {
        "visible_players": round(_clamp(len(observations) / 14.0), 6),
        "team_balance": round(
            _clamp(min(team_counts.values()) / max(1.0, max(team_counts.values()))),
            6,
        ),
        "bbox_size": round(_mean(sizes), 6),
        "low_overlap": round(1.0 - _clamp(overlaps / max(1.0, len(observations) / 2)), 6),
        "low_edge_cutting": round(_mean(edge_safe), 6),
        "tracklet_continuity": round(_mean(continuity), 6),
        "low_blur": round(_clamp(blur_score), 6),
        "low_switch_suspicion": round(switch_safe, 6),
        "camera_reliability": round(camera_reliability, 6),
    }


def _greedy_select(
    candidates: list[dict[str, Any]],
    *,
    duration_sec: float,
    seeded_subject_ids: set[str],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    remaining = list(candidates)
    selected: list[dict[str, Any]] = []
    covered = set(seeded_subject_ids)
    target = int(config["target_frame_count"])
    minimum_gap = float(config["minimum_frame_gap_sec"])
    while remaining and len(selected) < target:
        scored: list[tuple[float, int, dict[str, Any]]] = []
        for candidate in remaining:
            if selected and any(
                abs(candidate["time_sec"] - row["time_sec"]) < minimum_gap
                for row in selected
            ):
                continue
            subject_ids = {
                row["stable_subject_id"]
                for row in candidate["visible_detections"]
            }
            new_subjects = subject_ids - covered
            coverage_novelty = _clamp(len(new_subjects) / max(1.0, len(subject_ids)))
            time_diversity = (
                1.0
                if not selected
                else _clamp(
                    min(
                        abs(candidate["time_sec"] - row["time_sec"])
                        for row in selected
                    )
                    / max(minimum_gap * 3.0, duration_sec / max(1, target))
                )
            )
            domain_novelty = float(
                not any(
                    candidate["capture_domain"] == row["capture_domain"]
                    for row in selected
                )
            )
            selection_score = (
                0.58 * float(candidate["intrinsic_score"])
                + 0.24 * coverage_novelty
                + 0.12 * time_diversity
                + 0.06 * domain_novelty
            )
            enriched = {
                **candidate,
                "selection_score": round(selection_score, 6),
                "selection_components": {
                    "intrinsic_quality": round(float(candidate["intrinsic_score"]), 6),
                    "new_subject_coverage": round(coverage_novelty, 6),
                    "time_diversity": round(time_diversity, 6),
                    "capture_domain_diversity": round(domain_novelty, 6),
                },
                "new_subject_ids": sorted(new_subjects),
            }
            scored.append((selection_score, -int(candidate["frame"]), enriched))
        if not scored:
            break
        _, _, best = max(scored, key=lambda item: (item[0], item[1]))
        if selected and best["selection_score"] < float(config["minimum_selection_score"]):
            break
        selected.append(best)
        covered.update(
            row["stable_subject_id"]
            for row in best["visible_detections"]
        )
        remaining = [
            row for row in remaining if row["frame"] != best["frame"]
        ]
    return selected


def _deterministic_random_baseline(
    candidates: list[dict[str, Any]],
    *,
    selected_count: int,
    minimum_frame_gap_sec: float,
    fps: float,
    seed_material: Any,
) -> list[dict[str, Any]]:
    if selected_count <= 0:
        return []
    rows = list(candidates)
    seed = int(canonical_digest(seed_material)[:16], 16)
    random.Random(seed).shuffle(rows)
    selected: list[dict[str, Any]] = []
    minimum_gap_frames = round(minimum_frame_gap_sec * fps)
    for row in rows:
        if any(abs(row["frame"] - item["frame"]) < minimum_gap_frames for item in selected):
            continue
        selected.append(row)
        if len(selected) >= selected_count:
            break
    return selected


def _capture_domain(
    frame: int,
    camera_samples: list[dict[str, Any]],
) -> str:
    sample = _nearest_camera_sample(frame, camera_samples)
    if sample is None:
        return "camera:unknown"
    dx_bucket = round(float(sample.get("dx_px") or 0.0) / 40.0)
    dy_bucket = round(float(sample.get("dy_px") or 0.0) / 25.0)
    scale_bucket = round((float(sample.get("scale") or 1.0) - 1.0) / 0.02)
    return (
        f"camera:{sample.get('estimator') or 'unknown'}:"
        f"{dx_bucket}:{dy_bucket}:{scale_bucket}"
    )


def _nearest_camera_sample(
    frame: int,
    camera_samples: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not camera_samples:
        return None
    return min(camera_samples, key=lambda sample: abs(int(sample["frame"]) - frame))


def _selection_reasons(row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    components = row["score_components"]
    if components["visible_players"] >= 0.80:
        reasons.append("many_visible_players")
    if components["bbox_size"] >= 0.65:
        reasons.append("readable_player_scale")
    if components["low_overlap"] >= 0.85:
        reasons.append("low_bbox_overlap")
    if components["tracklet_continuity"] >= 0.75:
        reasons.append("strong_tracklet_continuity")
    if components["low_blur"] >= 0.65:
        reasons.append("low_motion_blur")
    if row["selection_components"]["new_subject_coverage"] > 0:
        reasons.append("new_subject_coverage")
    if row["selection_components"]["capture_domain_diversity"] > 0:
        reasons.append("capture_domain_diversity")
    return reasons or ["best_available_frame"]


def _weighted_score(
    components: dict[str, float],
    weights: dict[str, float],
) -> float:
    total_weight = sum(max(0.0, float(value)) for value in weights.values())
    if total_weight <= 0:
        return 0.0
    return sum(
        components.get(name, 0.0) * max(0.0, float(weight))
        for name, weight in weights.items()
    ) / total_weight


def _near_duplicate_pairs(
    selected: list[dict[str, Any]],
    *,
    minimum_gap_sec: float,
) -> int:
    return sum(
        abs(left["time_sec"] - right["time_sec"]) < minimum_gap_sec
        for index, left in enumerate(selected)
        for right in selected[index + 1 :]
    )


def _valid_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    bbox = [float(item) for item in value]
    if not all(math.isfinite(item) for item in bbox):
        return None
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return None
    return [round(item, 3) for item in bbox]


def _bbox_iou(left: list[float], right: list[float]) -> float:
    intersection_width = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    intersection_height = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    intersection = intersection_width * intersection_height
    if intersection <= 0:
        return 0.0
    left_area = (left[2] - left[0]) * (left[3] - left[1])
    right_area = (right[2] - right[0]) * (right[3] - right[1])
    return intersection / max(1e-9, left_area + right_area - intersection)


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    position = _clamp(quantile) * (len(values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    fraction = position - lower
    return values[lower] * (1.0 - fraction) + values[upper] * fraction


def _mean(values: Any) -> float:
    rows = list(values)
    return sum(rows) / len(rows) if rows else 0.0


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
