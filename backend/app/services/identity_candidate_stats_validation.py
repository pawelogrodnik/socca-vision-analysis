from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from math import hypot
from typing import Any

from app.services.global_identity import MAX_STATS_SPEED_MPS
from app.services.identity_promotion_safety import canonical_document_digest


SCHEMA_VERSION = "0.2.0"
ALGORITHM_NAME = "identity_candidate_stats_validation"
ALGORITHM_VERSION = "0.2.0"
LARGE_JUMP_SPEED_MPS = 12.0
MOVEMENT_OUTLIER_SPEED_MPS = MAX_STATS_SPEED_MPS
MAX_JUMP_GAP_SEC = 2.0
SUBSTITUTION_GAP_SEC = 10.0
LARGE_TIME_DELTA_SEC = 30.0
LARGE_DISTANCE_DELTA_M = 100.0


def build_identity_candidate_stats_validation(
    *,
    candidate_timeline: dict[str, Any],
    candidate_stats: dict[str, Any],
    candidate_diff: dict[str, Any],
    candidate_manifest: dict[str, Any],
    match_doc: dict[str, Any],
    candidate_heatmaps: dict[str, Any] | None = None,
    production_heatmaps: dict[str, Any] | None = None,
    possession_doc: dict[str, Any] | None = None,
    passes_doc: dict[str, Any] | None = None,
    events_doc: dict[str, Any] | None = None,
    generated_at: str | None = None,
) -> dict[str, dict[str, Any]]:
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    fps = _fps(match_doc)
    duration_sec = _duration(match_doc, candidate_timeline, fps)
    stats_by_player = _by_player(candidate_stats)
    diff_by_player = _by_player(candidate_diff)
    heatmaps_by_player = _heatmaps_by_player(candidate_heatmaps or {})
    production_heatmaps_by_player = _heatmaps_by_player(production_heatmaps or {})

    players = []
    all_jumps: list[dict[str, Any]] = []
    total_parallel = 0
    for player in candidate_timeline.get("players") or []:
        if not isinstance(player, dict) or not player.get("player_id"):
            continue
        player_id = str(player["player_id"])
        validation = _validate_player_timeline(
            player,
            fps=fps,
            duration_sec=duration_sec,
            stats=stats_by_player.get(player_id, {}),
            diff=diff_by_player.get(player_id, {}),
            heatmap=heatmaps_by_player.get(player_id),
            production_heatmap=production_heatmaps_by_player.get(player_id),
        )
        players.append(validation)
        all_jumps.extend(validation["large_spatial_jumps"])
        total_parallel += int(validation["parallel_observations"])

    manifest_safety = candidate_manifest.get("safety") or {}
    hard_conflicts = int(manifest_safety.get("hard_conflicts") or 0)
    stats_affecting_jumps = sum(bool(row.get("affects_stats")) for row in all_jumps)
    missing_production_baselines = sum(
        row["production_comparison"]["status"] == "production_baseline_unavailable"
        for row in players
    )
    warnings = _warnings(candidate_manifest, players, stats_affecting_jumps, total_parallel)
    validation_status = (
        "blocked"
        if hard_conflicts or total_parallel or stats_affecting_jumps
        else "ready_with_review"
        if candidate_manifest.get("status") == "partial_candidate"
        else "ready"
    )
    source = {
        "candidate_timeline_digest": canonical_document_digest(candidate_timeline),
        "candidate_stats_digest": canonical_document_digest(candidate_stats),
        "candidate_diff_digest": canonical_document_digest(candidate_diff),
        "candidate_manifest_digest": canonical_document_digest(candidate_manifest),
        "candidate_heatmaps_digest": canonical_document_digest(candidate_heatmaps or {}),
        "production_heatmaps_digest": canonical_document_digest(production_heatmaps or {}),
        "possession_digest": canonical_document_digest(possession_doc or {}),
        "passes_digest": canonical_document_digest(passes_doc or {}),
        "events_digest": canonical_document_digest(events_doc or {}),
    }
    shared = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION},
        "source": source,
    }
    validation_doc = {
        **shared,
        "status": validation_status,
        "thresholds": {
            "large_jump_speed_mps": LARGE_JUMP_SPEED_MPS,
            "movement_outlier_speed_mps": MOVEMENT_OUTLIER_SPEED_MPS,
            "max_jump_gap_sec": MAX_JUMP_GAP_SEC,
            "substitution_gap_sec": SUBSTITUTION_GAP_SEC,
            "large_time_delta_sec": LARGE_TIME_DELTA_SEC,
            "large_distance_delta_m": LARGE_DISTANCE_DELTA_M,
        },
        "match": {"fps": fps, "duration_sec": duration_sec},
        "players": sorted(players, key=lambda row: (str(row.get("player_name")), str(row["player_id"]))),
        "summary": {
            "players": len(players),
            "parallel_observations": total_parallel,
            "large_spatial_jumps": len(all_jumps),
            "stats_affecting_impossible_jumps": stats_affecting_jumps,
            "large_stat_deltas": sum(len(row["explainable_deltas"]) for row in players),
            "production_baseline_unavailable_players": missing_production_baselines,
            "hard_conflicts": hard_conflicts,
        },
        "warnings": warnings,
    }
    readiness_doc = {
        **shared,
        "status": validation_status,
        "features": _feature_readiness(
            validation_status=validation_status,
            candidate_manifest=candidate_manifest,
            candidate_stats=candidate_stats,
            candidate_heatmaps=candidate_heatmaps or {},
            possession_doc=possession_doc or {},
            passes_doc=passes_doc or {},
            events_doc=events_doc or {},
            stats_affecting_jumps=stats_affecting_jumps,
        ),
        "warnings": warnings,
    }
    return {
        "identity_candidate_stats_validation.json": validation_doc,
        "identity_feature_readiness_candidate.json": readiness_doc,
    }


def _validate_player_timeline(
    player: dict[str, Any],
    *,
    fps: float,
    duration_sec: float,
    stats: dict[str, Any],
    diff: dict[str, Any],
    heatmap: dict[str, Any] | None,
    production_heatmap: dict[str, Any] | None,
) -> dict[str, Any]:
    observations = sorted(
        [row for row in player.get("observations") or [] if isinstance(row, dict) and row.get("frame") is not None],
        key=lambda row: (int(row["frame"]), str(row.get("candidate_subject_id") or "")),
    )
    by_frame = Counter(int(row["frame"]) for row in observations)
    unique = _deduplicate_frames(observations)
    frames = [int(row["frame"]) for row in unique]
    intervals = _frame_intervals(frames, fps)
    gaps = _gaps(intervals, fps)
    status_counts = Counter(str(row.get("status") or row.get("source") or "unknown") for row in unique)
    jumps = _large_jumps(unique, fps, str(player["player_id"]))
    known_frames = len(frames)
    span_frames = frames[-1] - frames[0] + 1 if frames else 0
    predicted_or_occluded = status_counts["predicted"] + status_counts["occluded"]
    production_comparison = _production_comparison(diff)
    explainable_deltas = _explainable_deltas(diff, unique, production_comparison)
    player_name = str(stats.get("player_name") or player.get("player_name") or player["player_id"])
    return {
        "player_id": str(player["player_id"]),
        "player_name": player_name,
        "first_observation": _observation_boundary(unique[0]) if unique else None,
        "last_observation": _observation_boundary(unique[-1]) if unique else None,
        "playing_intervals": intervals,
        "possible_substitution_boundaries": _substitution_boundaries(intervals, duration_sec),
        "known_on_pitch": {
            "frames": known_frames,
            "seconds": round(known_frames / fps, 3),
            "span_seconds": round(span_frames / fps, 3),
            "unknown_within_span_seconds": round(max(0, span_frames - known_frames) / fps, 3),
            "coverage_ratio_within_span": round(known_frames / span_frames, 6) if span_frames else None,
            "denominator_status": "unknown_on_pitch_interval",
        },
        "fragment_count": int(stats.get("fragment_count") or len(_fragment_keys(unique))),
        "candidate_subject_ids": sorted(_subject_ids(unique)),
        "tracklet_ids": sorted(_tracklet_ids(unique)),
        "longest_gap": max(gaps, key=lambda row: row["duration_sec"], default=None),
        "gaps": gaps,
        "parallel_observations": sum(count - 1 for count in by_frame.values() if count > 1),
        "status_counts": dict(sorted(status_counts.items())),
        "predicted_occluded_share": round(predicted_or_occluded / known_frames, 6) if known_frames else 0.0,
        "large_spatial_jumps": jumps,
        "stats": stats,
        "production_comparison": production_comparison,
        "heatmap_comparison": _heatmap_comparison(heatmap, production_heatmap),
        "explainable_deltas": explainable_deltas,
        "quality_flags": _player_quality_flags(jumps, by_frame, explainable_deltas),
    }


def _large_jumps(rows: list[dict[str, Any]], fps: float, player_id: str) -> list[dict[str, Any]]:
    detected = [row for row in rows if str(row.get("status") or row.get("source")) == "detected" and _point(row)]
    result: list[dict[str, Any]] = []
    for left, right in zip(detected, detected[1:]):
        frame_gap = int(right["frame"]) - int(left["frame"])
        if frame_gap <= 0 or frame_gap / fps > MAX_JUMP_GAP_SEC:
            continue
        distance = hypot(float(right["pitch_m"][0]) - float(left["pitch_m"][0]), float(right["pitch_m"][1]) - float(left["pitch_m"][1]))
        speed = distance / (frame_gap / fps)
        if speed <= LARGE_JUMP_SPEED_MPS:
            continue
        same_movement_fragment = (
            str(left.get("candidate_subject_id") or "") == str(right.get("candidate_subject_id") or "")
            and str(left.get("tracklet_id") or "") == str(right.get("tracklet_id") or "")
            and frame_gap == 1
        )
        excluded_by_speed_filter = speed > MOVEMENT_OUTLIER_SPEED_MPS
        result.append({
            "player_id": player_id,
            "start_frame": int(left["frame"]),
            "end_frame": int(right["frame"]),
            "distance_m": round(distance, 3),
            "required_speed_mps": round(speed, 3),
            "same_movement_fragment": same_movement_fragment,
            "excluded_by_distance_calculator": excluded_by_speed_filter,
            "affects_stats": same_movement_fragment and not excluded_by_speed_filter,
            "source_review_card_keys": sorted({
                str(value)
                for value in (left.get("review_card_key"), right.get("review_card_key"))
                if value
            }),
            "candidate_subject_ids": sorted({
                str(value)
                for value in (left.get("candidate_subject_id"), right.get("candidate_subject_id"))
                if value
            }),
        })
    return result


def _production_comparison(diff: dict[str, Any]) -> dict[str, Any]:
    production_frames = int(diff.get("production_detected_frames") or 0)
    if "production_detected_frames" in diff and production_frames <= 0:
        return {
            "status": "production_baseline_unavailable",
            "production_detected_frames": 0,
            "reason_codes": ["player_absent_from_production_stats"],
        }
    return {
        "status": "available",
        "production_detected_frames": production_frames,
        "reason_codes": [],
    }


def _explainable_deltas(
    diff: dict[str, Any],
    observations: list[dict[str, Any]],
    production_comparison: dict[str, Any],
) -> list[dict[str, Any]]:
    if production_comparison["status"] != "available":
        return []
    time_delta = float(diff.get("playing_time_delta_sec") or 0.0)
    distance_delta = float(diff.get("distance_delta_m") or 0.0)
    if abs(time_delta) < LARGE_TIME_DELTA_SEC and abs(distance_delta) < LARGE_DISTANCE_DELTA_M:
        return []
    frames = [int(row["frame"]) for row in observations]
    return [{
        "playing_time_delta_sec": round(time_delta, 3),
        "distance_delta_m": round(distance_delta, 2),
        "candidate_subject_ids": sorted(_subject_ids(observations)),
        "source_review_card_keys": sorted({str(row.get("review_card_key")) for row in observations if row.get("review_card_key")}),
        "frame_range": [min(frames), max(frames)] if frames else None,
        "coverage_denominator_status": diff.get("coverage_denominator_status"),
        "requires_manual_explanation": True,
    }]


def _feature_readiness(
    *,
    validation_status: str,
    candidate_manifest: dict[str, Any],
    candidate_stats: dict[str, Any],
    candidate_heatmaps: dict[str, Any],
    possession_doc: dict[str, Any],
    passes_doc: dict[str, Any],
    events_doc: dict[str, Any],
    stats_affecting_jumps: int,
) -> dict[str, dict[str, Any]]:
    partial = candidate_manifest.get("status") == "partial_candidate"
    identity = "blocked" if validation_status == "blocked" else "ready_with_review" if partial else "ready"
    has_stats = bool(candidate_stats.get("players"))
    has_heatmaps = bool(candidate_heatmaps.get("heatmaps"))
    movement_reasons = ["movement_quality_requires_validation"]
    if partial:
        movement_reasons.insert(0, "partial_identity_coverage")
    return {
        "player_identity": _feature(identity, ["partial_identity_coverage"] if partial else []),
        "playing_time": _feature(identity if has_stats else "not_available", [] if has_stats else ["missing_candidate_stats"]),
        "heatmap": _feature(identity if has_heatmaps else "not_available", [] if has_heatmaps else ["missing_candidate_heatmaps"]),
        "distance": _feature(
            "blocked" if stats_affecting_jumps or identity == "blocked" else "experimental" if has_stats else "not_available",
            movement_reasons if has_stats else ["missing_candidate_stats"],
        ),
        "player_possession": _optional_feature(possession_doc, identity),
        "player_passes": _optional_feature(passes_doc, identity),
        "player_turnovers": _optional_feature(events_doc, identity),
        "player_events": _optional_feature(events_doc, identity),
    }


def _optional_feature(document: dict[str, Any], identity: str) -> dict[str, Any]:
    if not document:
        return _feature("not_available", ["optional_input_missing"])
    if identity == "blocked":
        return _feature("blocked", ["identity_blocked"])
    return _feature("experimental", ["identity_candidate_not_applied_to_event_artifact"])


def _feature(status: str, reasons: list[str] | None = None) -> dict[str, Any]:
    return {"status": status, "reason_codes": reasons or []}


def _warnings(
    manifest: dict[str, Any],
    players: list[dict[str, Any]],
    affecting_jumps: int,
    parallel: int,
) -> list[dict[str, Any]]:
    warnings = []
    if manifest.get("status") == "partial_candidate":
        warnings.append({"code": "partial_identity_coverage", "message": "Unresolved fragments are excluded from candidate stats."})
    if parallel:
        warnings.append({"code": "parallel_player_observations", "count": parallel})
    if affecting_jumps:
        warnings.append({"code": "impossible_spatial_jumps_affect_stats", "count": affecting_jumps})
    large_deltas = sum(len(row["explainable_deltas"]) for row in players)
    if large_deltas:
        warnings.append({"code": "large_stat_deltas_require_explanation", "count": large_deltas})
    missing_baselines = sum(
        row["production_comparison"]["status"] == "production_baseline_unavailable"
        for row in players
    )
    if missing_baselines:
        warnings.append({
            "code": "production_stats_baseline_unavailable",
            "count": missing_baselines,
            "message": "Candidate stats were validated structurally, but no per-player production baseline exists for comparison.",
        })
    return warnings


def _frame_intervals(frames: list[int], fps: float) -> list[dict[str, Any]]:
    if not frames:
        return []
    intervals: list[list[int]] = [[frames[0], frames[0]]]
    for frame in frames[1:]:
        if frame <= intervals[-1][1] + 1:
            intervals[-1][1] = frame
        else:
            intervals.append([frame, frame])
    return [
        {
            "start_frame": start,
            "end_frame": end,
            "start_sec": round(start / fps, 3),
            "end_sec": round(end / fps, 3),
            "observed_seconds": round((end - start + 1) / fps, 3),
        }
        for start, end in intervals
    ]


def _gaps(intervals: list[dict[str, Any]], fps: float) -> list[dict[str, Any]]:
    return [
        {
            "start_frame": int(left["end_frame"]) + 1,
            "end_frame": int(right["start_frame"]) - 1,
            "duration_sec": round((int(right["start_frame"]) - int(left["end_frame"]) - 1) / fps, 3),
        }
        for left, right in zip(intervals, intervals[1:])
    ]


def _substitution_boundaries(intervals: list[dict[str, Any]], duration_sec: float) -> list[dict[str, Any]]:
    if not intervals:
        return []
    result = [
        {"kind": "candidate_first_seen", "frame": intervals[0]["start_frame"], "time_sec": intervals[0]["start_sec"]},
        {"kind": "candidate_last_seen", "frame": intervals[-1]["end_frame"], "time_sec": intervals[-1]["end_sec"]},
    ]
    for left, right in zip(intervals, intervals[1:]):
        gap = float(right["start_sec"]) - float(left["end_sec"])
        if gap >= SUBSTITUTION_GAP_SEC:
            result.append({
                "kind": "possible_exit_reentry",
                "exit_frame": left["end_frame"],
                "reentry_frame": right["start_frame"],
                "gap_sec": round(gap, 3),
            })
    if float(intervals[-1]["end_sec"]) > duration_sec:
        result.append({"kind": "timeline_exceeds_video_duration"})
    return result


def _heatmap_comparison(candidate: dict[str, Any] | None, production: dict[str, Any] | None) -> dict[str, Any]:
    if candidate is None:
        return {"status": "candidate_not_available"}
    if production is None:
        return {"status": "production_not_available", "candidate_samples": int(candidate.get("samples") or 0)}
    left = _heatmap_centroid(candidate)
    right = _heatmap_centroid(production)
    return {
        "status": "available",
        "candidate_samples": int(candidate.get("samples") or 0),
        "production_samples": int(production.get("samples") or production.get("sample_count") or 0),
        "candidate_centroid_grid": left,
        "production_centroid_grid": right,
        "centroid_distance_grid": round(hypot(left[0] - right[0], left[1] - right[1]), 3) if left and right else None,
    }


def _heatmap_centroid(heatmap: dict[str, Any]) -> list[float] | None:
    bins = [row for row in heatmap.get("bins") or [] if isinstance(row, dict) and float(row.get("count") or 0) > 0]
    total = sum(float(row["count"]) for row in bins)
    if total <= 0:
        return None
    return [
        round(sum(float(row["x"]) * float(row["count"]) for row in bins) / total, 3),
        round(sum(float(row["y"]) * float(row["count"]) for row in bins) / total, 3),
    ]


def _player_quality_flags(
    jumps: list[dict[str, Any]],
    by_frame: Counter[int],
    deltas: list[dict[str, Any]],
) -> list[str]:
    flags = []
    if jumps:
        flags.append("large_spatial_jump")
    if any(row.get("affects_stats") for row in jumps):
        flags.append("spatial_jump_affects_stats")
    if any(count > 1 for count in by_frame.values()):
        flags.append("parallel_observation")
    if deltas:
        flags.append("large_stat_delta_requires_explanation")
    return flags


def _observation_boundary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "frame": int(row["frame"]),
        "time_sec": round(float(row.get("time_sec") or 0.0), 3),
        "status": str(row.get("status") or row.get("source") or "unknown"),
        "candidate_subject_id": row.get("candidate_subject_id"),
        "tracklet_id": row.get("tracklet_id"),
        "review_card_key": row.get("review_card_key"),
    }


def _deduplicate_frames(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        frame = int(row["frame"])
        current = result.get(frame)
        if current is None or _row_rank(row) > _row_rank(current):
            result[frame] = row
    return [result[frame] for frame in sorted(result)]


def _row_rank(row: dict[str, Any]) -> tuple[int, int, float]:
    status = str(row.get("status") or row.get("source") or "unknown")
    return (
        1 if status == "detected" else 0,
        1 if row.get("play_area_status") == "inside_play" else 0,
        float(row.get("confidence") or 0.0),
    )


def _point(row: dict[str, Any]) -> bool:
    value = row.get("pitch_m")
    return isinstance(value, (list, tuple)) and len(value) >= 2


def _fragment_keys(rows: list[dict[str, Any]]) -> set[tuple[str, str]]:
    return {(str(row.get("candidate_subject_id") or ""), str(row.get("tracklet_id") or "")) for row in rows}


def _subject_ids(rows: list[dict[str, Any]]) -> set[str]:
    return {str(row["candidate_subject_id"]) for row in rows if row.get("candidate_subject_id")}


def _tracklet_ids(rows: list[dict[str, Any]]) -> set[str]:
    return {str(row["tracklet_id"]) for row in rows if row.get("tracklet_id")}


def _by_player(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row["player_id"]): row
        for row in document.get("players") or []
        if isinstance(row, dict) and row.get("player_id")
    }


def _heatmaps_by_player(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = document.get("heatmaps") or document.get("players") or []
    return {
        str(row["player_id"]): row
        for row in rows
        if isinstance(row, dict) and row.get("player_id")
    }


def _fps(match_doc: dict[str, Any]) -> float:
    video = match_doc.get("video") if isinstance(match_doc.get("video"), dict) else {}
    return max(0.001, float(video.get("fps") or match_doc.get("fps") or 30.0))


def _duration(match_doc: dict[str, Any], timeline: dict[str, Any], fps: float) -> float:
    video = match_doc.get("video") if isinstance(match_doc.get("video"), dict) else {}
    explicit = float(video.get("duration_sec") or match_doc.get("duration_sec") or 0.0)
    if explicit > 0:
        return explicit
    end_frame = max((int(row.get("end_frame") or 0) for row in timeline.get("players") or [] if isinstance(row, dict)), default=0)
    return round((end_frame + 1) / fps, 3)
