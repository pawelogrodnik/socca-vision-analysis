from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_analysis_quality_report(
    *,
    frame_detection_counts: dict[str, Any] | None = None,
    stable_players: dict[str, Any] | None = None,
    global_identity_report: dict[str, Any] | None = None,
    tracking_quality_report: dict[str, Any] | None = None,
    movement_stats: dict[str, Any] | None = None,
    player_stats: dict[str, Any] | None = None,
    team_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    frames_doc = frame_detection_counts or {}
    stable_doc = stable_players or {}
    global_report = global_identity_report or {}
    tracking_report = tracking_quality_report or {}
    movement_doc = movement_stats or {}
    player_doc = player_stats or {}
    team_doc = team_stats or {}

    frame_summary = _as_record(frames_doc.get("summary"))
    frames = [_as_record(frame) for frame in frames_doc.get("frames", []) if isinstance(frame, dict)]
    target_players = int(_number(frames_doc, "target_players", 14) or 14)
    frame_count = len(frames) or int(_number(frame_summary, "frames", 0) or 0)

    frame_metrics = _frame_metrics(frames, frame_summary, target_players)
    tracking = _tracking_quality(frame_metrics, target_players)
    identity = _identity_quality(global_report, stable_doc, frame_metrics)
    stats = _stats_quality(movement_doc, player_doc, stable_doc)
    team = _team_quality(team_doc, stable_doc, tracking_report)
    components = {
        "tracking": tracking,
        "identity_stability": identity,
        "stats": stats,
        "team_assignment": team,
    }
    overall_score = round(
        tracking["score"] * 0.35
        + identity["score"] * 0.3
        + stats["score"] * 0.2
        + team["score"] * 0.15,
        1,
    )
    warnings = _dedupe(
        [
            *tracking["warnings"],
            *identity["warnings"],
            *stats["warnings"],
            *team["warnings"],
        ]
    )
    recommendation = _recommendation(overall_score, warnings)
    return {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "status": "completed",
        "quality": _quality_label(overall_score),
        "score": overall_score,
        "recommendation": recommendation,
        "summary": {
            "frames": frame_count,
            "target_players": target_players,
            **frame_metrics,
            "stable_players": _number(_as_record(stable_doc.get("summary")), "stable_players"),
            "stable_player_candidates": _number(_as_record(stable_doc.get("summary")), "stable_player_candidates"),
            "team_counts": _as_record(_as_record(stable_doc.get("summary")).get("team_counts")),
        },
        "components": components,
        "warnings": warnings,
        "frame_ranges": {
            "low_visible": _frame_ranges(
                int(frame.get("frame") or 0)
                for frame in frames
                if int(frame.get("visible_stable_boxes") or 0) < max(1, int(target_players * 0.7))
            ),
            "ambiguous": _frame_ranges(
                int(frame.get("frame") or 0)
                for frame in frames
                if int(frame.get("slot_ambiguous") or 0) > 0
            ),
            "missing": _frame_ranges(
                int(frame.get("frame") or 0)
                for frame in frames
                if int(frame.get("slot_missing") or 0) > 0
            ),
            "visual_hold": _frame_ranges(
                int(frame.get("frame") or 0)
                for frame in frames
                if int(frame.get("visual_interpolated_boxes") or 0) > 0
            ),
        },
        "top_problem_frames": _top_problem_frames(frames, target_players),
    }


def _frame_metrics(frames: list[dict[str, Any]], summary: dict[str, Any], target_players: int) -> dict[str, Any]:
    frame_count = len(frames) or int(_number(summary, "frames", 0) or 0)
    visible_values = [int(frame.get("visible_stable_boxes") or frame.get("stable_total") or 0) for frame in frames]
    trusted_values = [int(frame.get("trusted_detected") or frame.get("stable_detected") or 0) for frame in frames]
    raw_values = [int(frame.get("raw_detections") or 0) for frame in frames]
    missing_values = [int(frame.get("slot_missing") or frame.get("missing_slots") or 0) for frame in frames]
    ambiguous_values = [int(frame.get("slot_ambiguous") or frame.get("ambiguous_slots") or 0) for frame in frames]
    hold_values = [int(frame.get("visual_interpolated_boxes") or 0) for frame in frames]
    predicted_values = [int(frame.get("predicted_visible_boxes") or 0) for frame in frames]
    low_threshold = max(1, int(target_players * 0.7))
    low_visible_frames = sum(1 for value in visible_values if value < low_threshold)
    return {
        "raw_avg": round(_average(raw_values, _number(summary, "raw_avg") or 0.0), 3),
        "visible_avg": round(_average(visible_values, _number(summary, "stable_avg") or 0.0), 3),
        "visible_min": min(visible_values) if visible_values else int(_number(summary, "stable_min") or 0),
        "visible_max": max(visible_values) if visible_values else int(_number(summary, "stable_max") or 0),
        "trusted_detected_avg": round(_average(trusted_values, 0.0), 3),
        "low_visible_frames": low_visible_frames,
        "low_visible_rate": round(low_visible_frames / max(1, frame_count), 4),
        "missing_frame_count": sum(1 for value in missing_values if value > 0),
        "missing_frame_rate": round(sum(1 for value in missing_values if value > 0) / max(1, frame_count), 4),
        "ambiguous_frame_count": sum(1 for value in ambiguous_values if value > 0),
        "ambiguous_frame_rate": round(sum(1 for value in ambiguous_values if value > 0) / max(1, frame_count), 4),
        "visual_interpolated_boxes": sum(hold_values) or int(_number(summary, "visual_interpolated_boxes") or 0),
        "visual_interpolated_frames": sum(1 for value in hold_values if value > 0)
        or int(_number(summary, "visual_interpolated_frames") or 0),
        "predicted_visible_boxes": sum(predicted_values) or int(_number(summary, "predicted_visible_boxes") or 0),
        "ghost_bbox_count": int(_number(summary, "ghost_bbox_count") or 0),
    }


def _tracking_quality(metrics: dict[str, Any], target_players: int) -> dict[str, Any]:
    visible_ratio = _bounded(float(metrics["visible_avg"]) / max(1, target_players))
    low_rate = float(metrics["low_visible_rate"])
    ghost_count = int(metrics["ghost_bbox_count"])
    predicted_boxes = int(metrics["predicted_visible_boxes"])
    hold_boxes = int(metrics["visual_interpolated_boxes"])
    score = 100.0
    score -= (1.0 - visible_ratio) * 45.0
    score -= min(30.0, low_rate * 35.0)
    score -= min(25.0, ghost_count * 4.0 + predicted_boxes * 0.5)
    if hold_boxes > 0:
        score -= min(8.0, hold_boxes / max(1.0, float(metrics.get("visible_max") or target_players)) * 0.2)
    warnings: list[str] = []
    if visible_ratio < 0.7:
        warnings.append("Low average visible stable boxes; tracking may miss too many players.")
    if low_rate > 0.35:
        warnings.append("Many frames have low visible player count.")
    if ghost_count > 0 or predicted_boxes > 0:
        warnings.append("Default overlay contains predicted/ghost boxes; review conservative identity settings.")
    if hold_boxes > 0:
        warnings.append("Overlay uses short visual holds to smooth frame_stride gaps; verify with stable preview.")
    return _component("tracking", score, warnings, metrics)


def _identity_quality(global_report: dict[str, Any], stable_doc: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    summary = _as_record(global_report.get("summary"))
    stable_summary = _as_record(stable_doc.get("summary"))
    blocked = int(_number(summary, "blocked_identity_switches") or _number(stable_summary, "blocked_identity_switches") or 0)
    blocked_team = int(_number(summary, "blocked_team_switches") or _number(stable_summary, "blocked_team_switches") or 0)
    rejected = int(_number(summary, "rejected_candidates") or _number(stable_summary, "rejected_candidates") or 0)
    low_confidence = int(_number(stable_summary, "low_confidence_players") or 0)
    ambiguous_rate = float(metrics["ambiguous_frame_rate"])
    missing_rate = float(metrics["missing_frame_rate"])
    score = 100.0
    score -= min(25.0, ambiguous_rate * 50.0)
    score -= min(20.0, missing_rate * 25.0)
    score -= min(20.0, (blocked + blocked_team) * 3.0)
    score -= min(15.0, rejected * 0.5)
    score -= min(15.0, low_confidence * 4.0)
    warnings: list[str] = []
    if ambiguous_rate > 0.1:
        warnings.append("Identity has ambiguous frame ranges; player stats should be treated conservatively.")
    if missing_rate > 0.25:
        warnings.append("Many active slots are missing in frame history.")
    if blocked or blocked_team:
        warnings.append("Resolver blocked identity/team switches; review debug overlay around those frames.")
    if low_confidence:
        warnings.append("Some stable players have low confidence.")
    return _component(
        "identity_stability",
        score,
        warnings,
        {
            "blocked_identity_switches": blocked,
            "blocked_team_switches": blocked_team,
            "rejected_candidates": rejected,
            "low_confidence_players": low_confidence,
            "ambiguous_frame_rate": ambiguous_rate,
            "missing_frame_rate": missing_rate,
        },
    )


def _stats_quality(movement_doc: dict[str, Any], player_doc: dict[str, Any], stable_doc: dict[str, Any]) -> dict[str, Any]:
    movement_summary = _as_record(movement_doc.get("summary"))
    player_summary = _as_record(player_doc.get("summary"))
    stable_summary = _as_record(stable_doc.get("movement_stats_summary"))
    summary = {**stable_summary, **movement_summary, **player_summary}
    low_quality = int(_number(summary, "players_low_quality") or _number(summary, "low_quality_players") or 0)
    medium_quality = int(_number(summary, "players_medium_quality") or _number(summary, "medium_quality_players") or 0)
    estimated_ratio = float(_number(summary, "estimated_distance_ratio") or 0.0)
    estimated_distance = float(_number(summary, "estimated_gap_distance_m") or 0.0)
    rejected_sprint_candidates = int(_number(summary, "rejected_sprint_candidate_count") or 0)
    score = 100.0
    score -= min(25.0, low_quality * 8.0)
    score -= min(12.0, medium_quality * 2.0)
    score -= min(20.0, estimated_ratio * 40.0)
    score -= min(8.0, rejected_sprint_candidates * 0.5)
    warnings: list[str] = []
    if low_quality:
        warnings.append("Some player movement stats are low quality.")
    if estimated_ratio > 0.25 or estimated_distance > 0:
        warnings.append("Distance includes estimated short-gap segments; compare observed vs total distance.")
    if rejected_sprint_candidates:
        warnings.append("Sprint diagnostics contain rejected candidates; thresholds/outliers should be reviewed.")
    return _component(
        "stats",
        score,
        warnings,
        {
            "players_low_quality": low_quality,
            "players_medium_quality": medium_quality,
            "estimated_distance_ratio": round(estimated_ratio, 4),
            "estimated_gap_distance_m": round(estimated_distance, 2),
            "rejected_sprint_candidate_count": rejected_sprint_candidates,
        },
    )


def _team_quality(team_doc: dict[str, Any], stable_doc: dict[str, Any], tracking_report: dict[str, Any]) -> dict[str, Any]:
    stable_summary = _as_record(stable_doc.get("summary"))
    team_counts = _as_record(stable_summary.get("team_counts"))
    unknown_count = int(team_counts.get("U") or team_counts.get("unknown") or 0)
    team_a = int(team_counts.get("A") or 0)
    team_b = int(team_counts.get("B") or 0)
    tracking_summary = _as_record(tracking_report.get("summary"))
    over_cap_frames = int(_number(tracking_summary, "frames_with_team_over_cap") or 0)
    team_rows = [row for row in team_doc.get("teams", []) if isinstance(row, dict)]
    unlocked = sum(1 for row in team_rows if not bool(row.get("locked")))
    score = 100.0
    score -= min(25.0, unknown_count * 6.0)
    score -= min(15.0, abs(team_a - team_b) * 3.0)
    score -= min(20.0, over_cap_frames * 2.0)
    score -= min(10.0, unlocked * 4.0)
    warnings: list[str] = []
    if unknown_count:
        warnings.append("Some stable players are assigned to unknown team.")
    if abs(team_a - team_b) > 2:
        warnings.append("Team A/B stable player counts are imbalanced.")
    if over_cap_frames:
        warnings.append("Tracking quality reported team over-cap frames.")
    if unlocked:
        warnings.append("Team config is not fully locked/reviewed.")
    return _component(
        "team_assignment",
        score,
        warnings,
        {
            "team_a_players": team_a,
            "team_b_players": team_b,
            "unknown_players": unknown_count,
            "frames_with_team_over_cap": over_cap_frames,
            "unlocked_team_configs": unlocked,
        },
    )


def _component(name: str, score: float, warnings: list[str], metrics: dict[str, Any]) -> dict[str, Any]:
    safe_score = round(_bounded(score / 100.0) * 100.0, 1)
    return {
        "name": name,
        "quality": _quality_label(safe_score),
        "score": safe_score,
        "warnings": warnings,
        "metrics": metrics,
    }


def _recommendation(score: float, warnings: list[str]) -> str:
    if score >= 85 and not warnings:
        return "Analysis looks reliable for tracking-only player stats."
    if score >= 70:
        return "Analysis is usable, but review highlighted diagnostics before trusting detailed stats."
    if score >= 50:
        return "Analysis is partially usable; review overlay and problem frames before publishing stats."
    return "Analysis quality is low; rerun with better video/settings or review calibration/tracking before using stats."


def _quality_label(score: float) -> str:
    if score >= 85:
        return "high"
    if score >= 60:
        return "medium"
    return "low"


def _top_problem_frames(frames: list[dict[str, Any]], target_players: int, limit: int = 30) -> list[dict[str, Any]]:
    ranked = []
    for frame in frames:
        visible = int(frame.get("visible_stable_boxes") or 0)
        missing = int(frame.get("slot_missing") or 0)
        ambiguous = int(frame.get("slot_ambiguous") or 0)
        predicted = int(frame.get("predicted_visible_boxes") or 0)
        score = max(0, target_players - visible) + missing * 2 + ambiguous * 3 + predicted * 2
        if score <= 0:
            continue
        ranked.append(
            {
                "frame": frame.get("frame"),
                "time_sec": frame.get("time_sec"),
                "raw_detections": frame.get("raw_detections"),
                "visible_stable_boxes": visible,
                "trusted_detected": frame.get("trusted_detected"),
                "visual_interpolated_boxes": frame.get("visual_interpolated_boxes"),
                "slot_missing": missing,
                "slot_ambiguous": ambiguous,
                "severity_score": score,
            }
        )
    return sorted(ranked, key=lambda item: int(item["severity_score"]), reverse=True)[:limit]


def _frame_ranges(frames: Any) -> list[dict[str, int]]:
    sorted_frames = sorted({int(frame) for frame in frames})
    if not sorted_frames:
        return []
    ranges: list[dict[str, int]] = []
    start = sorted_frames[0]
    previous = sorted_frames[0]
    for frame in sorted_frames[1:]:
        if frame == previous + 1:
            previous = frame
            continue
        ranges.append({"start_frame": start, "end_frame": previous, "frames": previous - start + 1})
        start = frame
        previous = frame
    ranges.append({"start_frame": start, "end_frame": previous, "frames": previous - start + 1})
    return ranges


def _average(values: list[int], fallback: float) -> float:
    return sum(values) / len(values) if values else fallback


def _number(record: dict[str, Any], key: str, fallback: float | None = None) -> float | None:
    value = record.get(key)
    if isinstance(value, bool):
        return fallback
    if isinstance(value, (int, float)):
        return float(value)
    return fallback


def _as_record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _bounded(value: float) -> float:
    return max(0.0, min(1.0, value))


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
