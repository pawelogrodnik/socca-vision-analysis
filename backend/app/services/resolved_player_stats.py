from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.global_identity import calculate_movement_stats
from app.services.resolved_player_timeline import (
    build_resolved_player_timeline_from_files,
    calculate_timeline_presence,
    trusted_stats_rows,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _int(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _best_sprint_candidate_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = [
        row
        for row in rows
        if isinstance(row, dict) and _number(row.get("max_speed_kmh")) > 0.0
    ]
    if not candidates:
        return {}
    best = max(
        candidates,
        key=lambda row: (
            _number(row.get("max_speed_kmh")),
            _number(row.get("duration_sec")),
            _number(row.get("distance_m")),
        ),
    )
    return {
        "start_frame": _int(best.get("start_frame")),
        "end_frame": _int(best.get("end_frame")),
        "start_time_sec": round(_number(best.get("start_time_sec")), 3),
        "end_time_sec": round(_number(best.get("end_time_sec")), 3),
        "duration_sec": round(_number(best.get("duration_sec")), 3),
        "distance_m": round(_number(best.get("distance_m")), 2),
        "max_speed_kmh": round(_number(best.get("max_speed_kmh")), 2),
        "reason": str(best.get("reason") or "none"),
    }


def _load_json(path: Path, filename: str) -> dict[str, Any]:
    file_path = path / filename
    if not file_path.exists():
        raise FileNotFoundError(f"{filename} not found")
    data = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{filename} must contain an object")
    return data


def _player_stats_by_subject(player_stats: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = player_stats.get("players") if isinstance(player_stats.get("players"), list) else []
    return {
        str(row.get("stable_subject_id") or ""): row
        for row in rows
        if isinstance(row, dict) and row.get("stable_subject_id")
    }


def _stable_players_by_subject(stable_players: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(stable_players, dict):
        return {}
    rows = stable_players.get("players") if isinstance(stable_players.get("players"), list) else []
    return {
        str(row.get("stable_subject_id") or ""): row
        for row in rows
        if isinstance(row, dict) and row.get("stable_subject_id")
    }


def _quality_rank(quality: str | None) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(str(quality or "unknown"), 3)


def _worst_quality(values: list[str]) -> str:
    if not values:
        return "unknown"
    return max(values, key=_quality_rank)


def _stint_frame_count(stint: dict[str, Any]) -> int:
    counted = sum(
        _int(stint.get(key))
        for key in ["detected_frames", "missing_frames", "ambiguous_frames", "predicted_frames"]
    )
    if counted > 0:
        return counted
    start = stint.get("start_frame")
    end = stint.get("end_frame")
    if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end >= start:
        return int(end - start + 1)
    return 0


def _slot_frame_count(slot_stats: dict[str, Any], stable_player: dict[str, Any] | None) -> int:
    frames = slot_stats.get("frames") if isinstance(slot_stats.get("frames"), dict) else {}
    active = _int(frames.get("active_frames"))
    if active > 0:
        return active
    movement = stable_player.get("movement_stats") if isinstance(stable_player, dict) else {}
    active = _int(_record(movement).get("active_frames"))
    if active > 0:
        return active
    if isinstance(stable_player, dict):
        return sum(_stint_frame_count(stint) for stint in stable_player.get("stints") or [] if isinstance(stint, dict))
    return 0


def _stint_for_assignment(stable_player: dict[str, Any] | None, assignment: dict[str, Any]) -> dict[str, Any] | None:
    if not assignment.get("stint_id"):
        return None
    if isinstance(stable_player, dict):
        stint_id = str(assignment.get("stint_id"))
        stint = next(
            (
                item
                for item in stable_player.get("stints") or []
                if isinstance(item, dict) and str(item.get("stint_id") or "") == stint_id
            ),
            None,
        )
        if stint is not None:
            return stint
    if isinstance(assignment.get("start_time_sec"), (int, float)) and isinstance(
        assignment.get("end_time_sec"), (int, float)
    ):
        return assignment
    return None


def _scale_value(value: Any, ratio: float, *, integer: bool = False) -> int | float:
    scaled = _number(value) * ratio
    return int(round(scaled)) if integer else scaled


def _scale_stats_for_unique_coverage(stats: dict[str, Any], ratio: float) -> dict[str, Any]:
    if ratio >= 0.999:
        return stats
    ratio = max(0.0, min(1.0, ratio))
    scaled = copy.deepcopy(stats)
    for group, integer in [
        ("time", False),
        ("distance", False),
        ("frames", True),
        ("segments", True),
    ]:
        source = stats.get(group) if isinstance(stats.get(group), dict) else {}
        scaled[group] = {
            key: _scale_value(value, ratio, integer=integer)
            if isinstance(value, (int, float))
            else value
            for key, value in _record(source).items()
        }

    source_intensity = stats.get("intensity") if isinstance(stats.get("intensity"), dict) else {}
    scaled_intensity = copy.deepcopy(_record(source_intensity))
    for key in [
        "high_intensity_time_sec",
        "high_intensity_distance_m",
        "sprint_time_sec",
        "sprint_distance_m",
    ]:
        scaled_intensity[key] = _scale_value(source_intensity.get(key), ratio)
    for key in [
        "high_intensity_segments",
        "sprint_count",
        "trusted_speed_segments",
        "sprint_candidate_count",
        "rejected_sprint_candidate_count",
    ]:
        scaled_intensity[key] = _scale_value(source_intensity.get(key), ratio, integer=True)
    scaled["intensity"] = scaled_intensity
    scaled["stats_note"] = f"{stats.get('stats_note') or 'stats'}; clipped to unique non-overlapping player time"
    return scaled


def _clip_slot_stats_to_stint(
    slot_stats: dict[str, Any],
    stable_player: dict[str, Any] | None,
    assignment: dict[str, Any],
) -> dict[str, Any]:
    stint = _stint_for_assignment(stable_player, assignment)
    if not stint:
        return slot_stats

    denominator = _slot_frame_count(slot_stats, stable_player)
    stint_frames = _stint_frame_count(stint)
    if denominator <= 0 or stint_frames <= 0:
        return slot_stats
    ratio = max(0.0, min(1.0, stint_frames / denominator))

    clipped = copy.deepcopy(slot_stats)
    clipped["tracklet_ids"] = list(stint.get("tracklet_ids") or [])
    clipped["raw_track_ids"] = list(stint.get("raw_track_ids") or [])
    clipped["stats_note"] = "stint-level estimate clipped from stable slot stats by stint frame coverage"

    source_time = slot_stats.get("time") if isinstance(slot_stats.get("time"), dict) else {}
    source_frames = slot_stats.get("frames") if isinstance(slot_stats.get("frames"), dict) else {}
    seconds_per_active_frame = (
        _number(source_time.get("playing_time_sec")) / denominator
        if denominator > 0 and _number(source_time.get("playing_time_sec")) > 0
        else 0.0
    )

    detected_frames = _int(stint.get("detected_frames"))
    missing_frames = _int(stint.get("missing_frames"))
    ambiguous_frames = _int(stint.get("ambiguous_frames"))
    predicted_frames = _int(stint.get("predicted_frames"))
    active_frames = detected_frames + missing_frames + ambiguous_frames + predicted_frames
    if active_frames <= 0:
        active_frames = stint_frames

    clipped["frames"] = {
        **_record(source_frames),
        "active_frames": active_frames,
        "detected_frames": detected_frames,
        "missing_frames": missing_frames,
        "ambiguous_frames": ambiguous_frames,
        "predicted_frames": predicted_frames,
        "samples_used": int(round(_int(source_frames.get("samples_used")) * ratio)),
    }

    if seconds_per_active_frame > 0:
        clipped["time"] = {
            **_record(source_time),
            "playing_time_sec": active_frames * seconds_per_active_frame,
            "detected_time_sec": detected_frames * seconds_per_active_frame,
            "missing_time_sec": missing_frames * seconds_per_active_frame,
            "ambiguous_time_sec": ambiguous_frames * seconds_per_active_frame,
        }
    else:
        clipped["time"] = {
            **_record(source_time),
            "playing_time_sec": _scale_value(source_time.get("playing_time_sec"), ratio),
            "detected_time_sec": _scale_value(source_time.get("detected_time_sec"), ratio),
            "missing_time_sec": _scale_value(source_time.get("missing_time_sec"), ratio),
            "ambiguous_time_sec": _scale_value(source_time.get("ambiguous_time_sec"), ratio),
        }

    source_distance = slot_stats.get("distance") if isinstance(slot_stats.get("distance"), dict) else {}
    clipped["distance"] = {
        **_record(source_distance),
        "observed_distance_m": _scale_value(source_distance.get("observed_distance_m"), ratio),
        "estimated_short_gap_distance_m": _scale_value(source_distance.get("estimated_short_gap_distance_m"), ratio),
        "total_distance_m": _scale_value(source_distance.get("total_distance_m"), ratio),
    }

    source_segments = slot_stats.get("segments") if isinstance(slot_stats.get("segments"), dict) else {}
    clipped["segments"] = {
        key: _scale_value(value, ratio, integer=True)
        for key, value in _record(source_segments).items()
    }

    source_intensity = slot_stats.get("intensity") if isinstance(slot_stats.get("intensity"), dict) else {}
    clipped_intensity = copy.deepcopy(_record(source_intensity))
    for key in [
        "high_intensity_time_sec",
        "high_intensity_distance_m",
        "sprint_time_sec",
        "sprint_distance_m",
    ]:
        clipped_intensity[key] = _scale_value(source_intensity.get(key), ratio)
    for key in [
        "high_intensity_segments",
        "sprint_count",
        "trusted_speed_segments",
        "sprint_candidate_count",
        "rejected_sprint_candidate_count",
    ]:
        clipped_intensity[key] = _scale_value(source_intensity.get(key), ratio, integer=True)
    clipped["intensity"] = clipped_intensity
    return clipped


def _assignment_interval(
    assignment: dict[str, Any],
    stable_by_subject: dict[str, dict[str, Any]],
) -> tuple[float, float] | None:
    stint = _stint_for_assignment(
        stable_by_subject.get(str(assignment.get("stable_subject_id") or "")),
        assignment,
    )
    if not stint:
        return None
    start = stint.get("start_time_sec")
    end = stint.get("end_time_sec")
    if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end > start:
        return float(start), float(end)
    duration = _number(stint.get("duration_sec"))
    if isinstance(start, (int, float)) and duration > 0:
        return float(start), float(start) + duration
    return None


def _merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not intervals:
        return []
    ordered = sorted((start, end) for start, end in intervals if end > start)
    merged: list[tuple[float, float]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _unique_interval_length(
    start: float,
    end: float,
    covered: list[tuple[float, float]],
) -> float:
    if end <= start:
        return 0.0
    overlap = 0.0
    for covered_start, covered_end in covered:
        overlap += max(0.0, min(end, covered_end) - max(start, covered_start))
    return max(0.0, (end - start) - overlap)


def _assignment_runtime_key(assignment: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(assignment.get("player_id") or ""),
        str(assignment.get("stable_subject_id") or ""),
        str(assignment.get("stint_id") or ""),
    )


def _assignment_unique_ratios(
    assignments: list[dict[str, Any]],
    stable_by_subject: dict[str, dict[str, Any]],
) -> dict[tuple[str, str, str], float]:
    by_player: dict[str, list[tuple[float, float, dict[str, Any]]]] = {}
    ratios: dict[tuple[str, str, str], float] = {}
    for assignment in assignments:
        if assignment.get("status") != "assigned" or not assignment.get("player_id") or not assignment.get("stint_id"):
            continue
        interval = _assignment_interval(assignment, stable_by_subject)
        if not interval:
            continue
        by_player.setdefault(str(assignment["player_id"]), []).append((interval[0], interval[1], assignment))

    for rows in by_player.values():
        covered: list[tuple[float, float]] = []
        for start, end, assignment in sorted(rows, key=lambda item: (item[0], item[1])):
            duration = max(0.0, end - start)
            if duration <= 0:
                ratios[_assignment_runtime_key(assignment)] = 1.0
                continue
            unique = _unique_interval_length(start, end, covered)
            ratios[_assignment_runtime_key(assignment)] = max(0.0, min(1.0, unique / duration))
            covered = _merge_intervals([*covered, (start, end)])
    return ratios


def _empty_player_row(assignment: dict[str, Any]) -> dict[str, Any]:
    return {
        "player_id": assignment.get("player_id"),
        "player_name": assignment.get("player_name"),
        "player_number": assignment.get("player_number"),
        "player_role": assignment.get("player_role"),
        "team_label": assignment.get("team_label"),
        "team_id": assignment.get("team_id"),
        "team_name": assignment.get("team_name"),
        "source_stable_slots": [],
        "stable_player_ids": [],
        "stable_subject_ids": [],
        "tracklet_ids": [],
        "raw_track_ids": [],
        "time": {
            "playing_time_sec": 0.0,
            "detected_time_sec": 0.0,
            "missing_time_sec": 0.0,
            "ambiguous_time_sec": 0.0,
            "inferred_playing_time_sec": 0.0,
        },
        "distance": {
            "observed_distance_m": 0.0,
            "estimated_short_gap_distance_m": 0.0,
            "total_distance_m": 0.0,
            "estimated_distance_ratio": 0.0,
            "quality": "unknown",
        },
        "speed": {
            "avg_speed_mps": 0.0,
            "avg_speed_kmh": 0.0,
            "observed_avg_speed_mps": 0.0,
            "peak_sustained_speed_mps": 0.0,
            "peak_sustained_speed_kmh": 0.0,
            "top_speed_mps": 0.0,
            "top_speed_kmh": 0.0,
            "raw_segment_top_speed_mps": 0.0,
            "raw_segment_top_speed_kmh": 0.0,
            "quality": "unknown",
        },
        "intensity": {
            "high_intensity_threshold_kmh": 0.0,
            "sprint_threshold_kmh": 0.0,
            "min_sprint_duration_sec": 0.0,
            "high_intensity_time_sec": 0.0,
            "high_intensity_distance_m": 0.0,
            "high_intensity_segments": 0,
            "high_intensity_distance_ratio": 0.0,
            "sprint_count": 0,
            "sprint_time_sec": 0.0,
            "sprint_distance_m": 0.0,
            "sprint_distance_ratio": 0.0,
            "longest_sprint_time_sec": 0.0,
            "longest_sprint_distance_m": 0.0,
            "max_sprint_speed_kmh": 0.0,
            "trusted_speed_segments": 0,
            "sprint_candidate_count": 0,
            "rejected_sprint_candidate_count": 0,
            "best_sprint_candidate_speed_kmh": 0.0,
            "best_sprint_candidate_duration_sec": 0.0,
            "best_sprint_candidate_distance_m": 0.0,
            "best_sprint_candidate_reason": "none",
            "best_rejected_sprint_candidate": {},
            "rejected_sprint_candidates": [],
        },
        "frames": {
            "active_frames": 0,
            "detected_frames": 0,
            "missing_frames": 0,
            "ambiguous_frames": 0,
            "predicted_frames": 0,
            "samples_used": 0,
        },
        "segments": {
            "observed_segments": 0,
            "estimated_gap_segments": 0,
            "skipped_outlier_segments": 0,
            "skipped_speed_outlier_segments": 0,
            "skipped_long_gap_segments": 0,
            "sustained_speed_windows": 0,
        },
        "review_warnings": [],
        "tracking_only": True,
        "stats_note": "resolved from stable slot tracking-only stats; ball events are not included",
    }


def _merge_slot_stats(row: dict[str, Any], assignment: dict[str, Any], slot_stats: dict[str, Any]) -> None:
    row["source_stable_slots"].append(
        {
            "stable_subject_id": assignment.get("stable_subject_id"),
            "stable_player_id": assignment.get("stable_player_id"),
            "slot_id": assignment.get("slot_id"),
            "stint_id": assignment.get("stint_id"),
            "stint_ids": assignment.get("stint_ids") or [],
            "assignment_scope": assignment.get("assignment_scope") or "stable_slot",
            "start_frame": assignment.get("start_frame"),
            "end_frame": assignment.get("end_frame"),
            "unique_time_ratio": assignment.get("unique_time_ratio", 1.0),
            "review_warnings": assignment.get("review_warnings") or [],
        }
    )
    if assignment.get("stable_player_id"):
        row["stable_player_ids"].append(assignment["stable_player_id"])
    if assignment.get("stable_subject_id"):
        row["stable_subject_ids"].append(assignment["stable_subject_id"])
    row["tracklet_ids"].extend(slot_stats.get("tracklet_ids") or [])
    row["raw_track_ids"].extend(slot_stats.get("raw_track_ids") or [])

    for group, keys in {
        "time": ["playing_time_sec", "detected_time_sec", "missing_time_sec", "ambiguous_time_sec"],
        "distance": ["observed_distance_m", "estimated_short_gap_distance_m", "total_distance_m"],
        "frames": ["active_frames", "detected_frames", "missing_frames", "ambiguous_frames", "predicted_frames", "samples_used"],
        "segments": [
            "observed_segments",
            "estimated_gap_segments",
            "skipped_outlier_segments",
            "skipped_speed_outlier_segments",
            "skipped_long_gap_segments",
            "sustained_speed_windows",
        ],
    }.items():
        source_group = slot_stats.get(group) if isinstance(slot_stats.get(group), dict) else {}
        for key in keys:
            if group in {"frames", "segments"}:
                row[group][key] += _int(source_group.get(key))
            else:
                row[group][key] += _number(source_group.get(key))

    source_speed = slot_stats.get("speed") if isinstance(slot_stats.get("speed"), dict) else {}
    for key in ["peak_sustained_speed_mps", "peak_sustained_speed_kmh", "top_speed_mps", "top_speed_kmh", "raw_segment_top_speed_mps", "raw_segment_top_speed_kmh"]:
        row["speed"][key] = max(_number(row["speed"].get(key)), _number(source_speed.get(key)))

    source_intensity = slot_stats.get("intensity") if isinstance(slot_stats.get("intensity"), dict) else {}
    for key in [
        "high_intensity_threshold_kmh",
        "sprint_threshold_kmh",
        "min_sprint_duration_sec",
    ]:
        row["intensity"][key] = max(_number(row["intensity"].get(key)), _number(source_intensity.get(key)))
    for key in [
        "high_intensity_time_sec",
        "high_intensity_distance_m",
        "sprint_time_sec",
        "sprint_distance_m",
    ]:
        row["intensity"][key] += _number(source_intensity.get(key))
    for key in ["high_intensity_segments", "sprint_count", "trusted_speed_segments"]:
        row["intensity"][key] += _int(source_intensity.get(key))
    for key in ["longest_sprint_time_sec", "longest_sprint_distance_m", "max_sprint_speed_kmh"]:
        row["intensity"][key] = max(_number(row["intensity"].get(key)), _number(source_intensity.get(key)))
    for key in ["sprint_candidate_count", "rejected_sprint_candidate_count"]:
        row["intensity"][key] += _int(source_intensity.get(key))
    previous_best_speed = _number(row["intensity"].get("best_sprint_candidate_speed_kmh"))
    source_best_speed = _number(source_intensity.get("best_sprint_candidate_speed_kmh"))
    if source_best_speed > previous_best_speed:
        row["intensity"]["best_sprint_candidate_speed_kmh"] = source_best_speed
        row["intensity"]["best_sprint_candidate_duration_sec"] = _number(source_intensity.get("best_sprint_candidate_duration_sec"))
        row["intensity"]["best_sprint_candidate_distance_m"] = _number(source_intensity.get("best_sprint_candidate_distance_m"))
        row["intensity"]["best_sprint_candidate_reason"] = str(source_intensity.get("best_sprint_candidate_reason") or "none")
    row["intensity"]["best_rejected_sprint_candidate"] = _best_sprint_candidate_from_rows(
        [
            _record(row["intensity"].get("best_rejected_sprint_candidate")),
            _record(source_intensity.get("best_rejected_sprint_candidate")),
        ]
    )
    rejected = source_intensity.get("rejected_sprint_candidates") if isinstance(source_intensity.get("rejected_sprint_candidates"), list) else []
    row["intensity"]["rejected_sprint_candidates"].extend(rejected[:5])
    row["review_warnings"].extend(assignment.get("review_warnings") or [])


def _finalize_player_row(row: dict[str, Any], distance_qualities: list[str], speed_qualities: list[str]) -> dict[str, Any]:
    total_distance = _number(row["distance"]["total_distance_m"])
    playing_time = _number(row["time"]["playing_time_sec"])
    observed_distance = _number(row["distance"]["observed_distance_m"])
    detected_time = _number(row["time"]["detected_time_sec"])
    estimated_distance = _number(row["distance"]["estimated_short_gap_distance_m"])

    row["distance"]["estimated_distance_ratio"] = round(estimated_distance / total_distance, 4) if total_distance > 0 else 0.0
    row["distance"]["quality"] = _worst_quality(distance_qualities)
    row["speed"]["avg_speed_mps"] = round(total_distance / playing_time, 3) if playing_time > 0 else 0.0
    row["speed"]["avg_speed_kmh"] = round(row["speed"]["avg_speed_mps"] * 3.6, 2)
    row["speed"]["observed_avg_speed_mps"] = round(observed_distance / detected_time, 3) if detected_time > 0 else 0.0
    row["speed"]["quality"] = _worst_quality(speed_qualities)
    row["intensity"]["high_intensity_distance_ratio"] = round(_number(row["intensity"].get("high_intensity_distance_m")) / total_distance, 4) if total_distance > 0 else 0.0
    row["intensity"]["sprint_distance_ratio"] = round(_number(row["intensity"].get("sprint_distance_m")) / total_distance, 4) if total_distance > 0 else 0.0
    row["intensity"]["rejected_sprint_candidates"] = sorted(
        [
            item
            for item in row["intensity"].get("rejected_sprint_candidates", [])
            if isinstance(item, dict)
        ],
        key=lambda item: (
            _number(item.get("max_speed_kmh")),
            _number(item.get("duration_sec")),
            _number(item.get("distance_m")),
        ),
        reverse=True,
    )[:5]

    for group in ["time", "distance", "speed", "intensity"]:
        for key, value in list(row[group].items()):
            if isinstance(value, float):
                row[group][key] = round(value, 2 if key.endswith("_kmh") or key.endswith("_m") or key.endswith("_sec") else 4)

    row["stable_player_ids"] = sorted({str(item) for item in row["stable_player_ids"]})
    row["stable_subject_ids"] = sorted({str(item) for item in row["stable_subject_ids"]})
    row["tracklet_ids"] = sorted({str(item) for item in row["tracklet_ids"]})
    row["raw_track_ids"] = sorted({int(item) for item in row["raw_track_ids"] if isinstance(item, int)})
    row["review_warnings"] = sorted({str(item) for item in row["review_warnings"]})
    return row


def _team_rows(players: list[dict[str, Any]]) -> list[dict[str, Any]]:
    teams: dict[str, dict[str, Any]] = {}
    for player in players:
        team_id = str(player.get("team_id") or player.get("team_label") or "unknown-team")
        row = teams.setdefault(
            team_id,
            {
                "team_id": player.get("team_id"),
                "team_name": player.get("team_name"),
                "team_label": player.get("team_label"),
                "players": 0,
                "playing_time_sec": 0.0,
                "detected_time_sec": 0.0,
                "missing_time_sec": 0.0,
                "ambiguous_time_sec": 0.0,
                "inferred_playing_time_sec": 0.0,
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
        row["players"] += 1
        row["playing_time_sec"] += _number(player["time"].get("playing_time_sec"))
        row["detected_time_sec"] += _number(player["time"].get("detected_time_sec"))
        row["missing_time_sec"] += _number(player["time"].get("missing_time_sec"))
        row["ambiguous_time_sec"] += _number(player["time"].get("ambiguous_time_sec"))
        row["inferred_playing_time_sec"] += _number(player["time"].get("inferred_playing_time_sec"))
        row["total_distance_m"] += _number(player["distance"].get("total_distance_m"))
        row["observed_distance_m"] += _number(player["distance"].get("observed_distance_m"))
        row["estimated_short_gap_distance_m"] += _number(player["distance"].get("estimated_short_gap_distance_m"))
        row["peak_sustained_speed_kmh"] = max(_number(row["peak_sustained_speed_kmh"]), _number(player["speed"].get("peak_sustained_speed_kmh")))
        row["top_speed_kmh"] = max(_number(row["top_speed_kmh"]), _number(player["speed"].get("top_speed_kmh")))
        intensity = player.get("intensity") if isinstance(player.get("intensity"), dict) else {}
        row["high_intensity_time_sec"] += _number(intensity.get("high_intensity_time_sec"))
        row["high_intensity_distance_m"] += _number(intensity.get("high_intensity_distance_m"))
        row["sprint_count"] += _int(intensity.get("sprint_count"))
        row["sprint_time_sec"] += _number(intensity.get("sprint_time_sec"))
        row["sprint_distance_m"] += _number(intensity.get("sprint_distance_m"))
        row["sprint_candidate_count"] += _int(intensity.get("sprint_candidate_count"))
        row["rejected_sprint_candidate_count"] += _int(intensity.get("rejected_sprint_candidate_count"))
        row["best_sprint_candidate_speed_kmh"] = max(_number(row["best_sprint_candidate_speed_kmh"]), _number(intensity.get("best_sprint_candidate_speed_kmh")))
        row["best_sprint_candidate_duration_sec"] = max(_number(row["best_sprint_candidate_duration_sec"]), _number(intensity.get("best_sprint_candidate_duration_sec")))
        row["best_rejected_sprint_candidate"] = _best_sprint_candidate_from_rows(
            [_record(row.get("best_rejected_sprint_candidate")), _record(intensity.get("best_rejected_sprint_candidate"))]
        )
        row["longest_sprint_distance_m"] = max(_number(row["longest_sprint_distance_m"]), _number(intensity.get("longest_sprint_distance_m")))
        row["max_sprint_speed_kmh"] = max(_number(row["max_sprint_speed_kmh"]), _number(intensity.get("max_sprint_speed_kmh")))
        quality_key = f"players_{player['distance'].get('quality')}_quality"
        if quality_key in row:
            row[quality_key] += 1

    for row in teams.values():
        for key, value in list(row.items()):
            if isinstance(value, float):
                row[key] = round(value, 2)
    return sorted(teams.values(), key=lambda item: str(item.get("team_label") or ""))


def build_resolved_player_stats_document(
    *,
    player_stats: dict[str, Any],
    identity_assignments: dict[str, Any],
    stable_players: dict[str, Any] | None = None,
) -> dict[str, Any]:
    slot_stats_by_subject = _player_stats_by_subject(player_stats)
    stable_by_subject = _stable_players_by_subject(stable_players)
    assignments = identity_assignments.get("assignments") if isinstance(identity_assignments.get("assignments"), list) else []
    unique_ratios = _assignment_unique_ratios(assignments, stable_by_subject)
    rows_by_player: dict[str, dict[str, Any]] = {}
    distance_qualities: dict[str, list[str]] = {}
    speed_qualities: dict[str, list[str]] = {}
    skipped_assignments = []
    clipped_overlap_assignments = 0

    for assignment in assignments:
        if not isinstance(assignment, dict):
            continue
        if assignment.get("status") != "assigned" or not assignment.get("player_id"):
            continue
        stable_subject_id = str(assignment.get("stable_subject_id") or "")
        slot_stats = slot_stats_by_subject.get(stable_subject_id)
        if not slot_stats:
            skipped_assignments.append({"stable_subject_id": stable_subject_id, "reason": "missing_player_stats"})
            continue
        effective_stats = _clip_slot_stats_to_stint(
            slot_stats,
            stable_by_subject.get(stable_subject_id),
            assignment,
        )
        assignment_for_merge = assignment
        unique_ratio = unique_ratios.get(_assignment_runtime_key(assignment), 1.0)
        if unique_ratio < 0.999:
            clipped_overlap_assignments += 1
            effective_stats = _scale_stats_for_unique_coverage(effective_stats, unique_ratio)
            warnings = list(assignment.get("review_warnings") or [])
            if "overlapping_stint_clipped" not in warnings:
                warnings.append("overlapping_stint_clipped")
            assignment_for_merge = {
                **assignment,
                "review_warnings": warnings,
                "unique_time_ratio": round(unique_ratio, 4),
            }
        player_id = str(assignment["player_id"])
        row = rows_by_player.setdefault(player_id, _empty_player_row(assignment))
        _merge_slot_stats(row, assignment_for_merge, effective_stats)
        distance = effective_stats.get("distance") if isinstance(effective_stats.get("distance"), dict) else {}
        speed = effective_stats.get("speed") if isinstance(effective_stats.get("speed"), dict) else {}
        distance_qualities.setdefault(player_id, []).append(str(distance.get("quality") or "unknown"))
        speed_qualities.setdefault(player_id, []).append(str(speed.get("quality") or "unknown"))

    players = [
        _finalize_player_row(row, distance_qualities.get(player_id, []), speed_qualities.get(player_id, []))
        for player_id, row in rows_by_player.items()
    ]
    teams = _team_rows(players)
    summary = {
        "players": len(players),
        "assigned_slots": sum(len(player.get("source_stable_slots") or []) for player in players),
        "assigned_stints": int((identity_assignments.get("summary") or {}).get("assigned_stints") or 0),
        "unresolved_slots": int((identity_assignments.get("summary") or {}).get("unassigned_slots") or 0),
        "skipped_assignments": len(skipped_assignments),
        "overlapping_stint_assignments_clipped": clipped_overlap_assignments,
        "players_with_warnings": sum(1 for player in players if player.get("review_warnings")),
        "total_distance_m": round(sum(_number(player["distance"].get("total_distance_m")) for player in players), 2),
        "observed_distance_m": round(sum(_number(player["distance"].get("observed_distance_m")) for player in players), 2),
        "estimated_short_gap_distance_m": round(sum(_number(player["distance"].get("estimated_short_gap_distance_m")) for player in players), 2),
        "playing_time_sec": round(sum(_number(player["time"].get("playing_time_sec")) for player in players), 2),
        "detected_time_sec": round(sum(_number(player["time"].get("detected_time_sec")) for player in players), 2),
        "missing_time_sec": round(sum(_number(player["time"].get("missing_time_sec")) for player in players), 2),
        "ambiguous_time_sec": round(sum(_number(player["time"].get("ambiguous_time_sec")) for player in players), 2),
        "peak_sustained_speed_kmh": round(max([_number(player["speed"].get("peak_sustained_speed_kmh")) for player in players] or [0.0]), 2),
        "top_speed_kmh": round(max([_number(player["speed"].get("top_speed_kmh")) for player in players] or [0.0]), 2),
        "high_intensity_time_sec": round(sum(_number(_record(player.get("intensity")).get("high_intensity_time_sec")) for player in players), 2),
        "high_intensity_distance_m": round(sum(_number(_record(player.get("intensity")).get("high_intensity_distance_m")) for player in players), 2),
        "sprint_count": sum(_int(_record(player.get("intensity")).get("sprint_count")) for player in players),
        "sprint_time_sec": round(sum(_number(_record(player.get("intensity")).get("sprint_time_sec")) for player in players), 2),
        "sprint_distance_m": round(sum(_number(_record(player.get("intensity")).get("sprint_distance_m")) for player in players), 2),
        "longest_sprint_distance_m": round(max([_number(_record(player.get("intensity")).get("longest_sprint_distance_m")) for player in players] or [0.0]), 2),
        "max_sprint_speed_kmh": round(max([_number(_record(player.get("intensity")).get("max_sprint_speed_kmh")) for player in players] or [0.0]), 2),
        "sprint_candidate_count": sum(_int(_record(player.get("intensity")).get("sprint_candidate_count")) for player in players),
        "rejected_sprint_candidate_count": sum(_int(_record(player.get("intensity")).get("rejected_sprint_candidate_count")) for player in players),
        "best_sprint_candidate_speed_kmh": round(max([_number(_record(player.get("intensity")).get("best_sprint_candidate_speed_kmh")) for player in players] or [0.0]), 2),
        "best_sprint_candidate_duration_sec": round(max([_number(_record(player.get("intensity")).get("best_sprint_candidate_duration_sec")) for player in players] or [0.0]), 3),
        "best_rejected_sprint_candidate": _best_sprint_candidate_from_rows(
            [_record(_record(player.get("intensity")).get("best_rejected_sprint_candidate")) for player in players]
        ),
    }
    return {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "source": "player_identity_assignments",
        "stats_source": "player_stats",
        "identity_semantics": identity_assignments.get("identity_semantics") or player_stats.get("identity_semantics") or "stint_first",
        "scope": "resolved_player_tracking_only_no_ball",
        "units": player_stats.get("units") or {"distance": "meters", "speed": "mps_and_kmh", "time": "seconds"},
        "summary": summary,
        "teams": teams,
        "players": sorted(players, key=lambda item: str(item.get("player_name") or item.get("player_id") or "")),
        "skipped_assignments": skipped_assignments,
    }


def build_resolved_player_stats_from_files(path: Path, *, persist: bool = False) -> dict[str, Any]:
    player_stats = _load_json(path, "player_stats.json")
    identity_assignments = _load_json(path, "player_identity_assignments.json")
    if (path / "identity_review_gallery.json").exists():
        stable_players = _load_json(path, "identity_review_gallery.json")
    else:
        stable_players = _load_json(path, "stable_players.json") if (path / "stable_players.json").exists() else None
    legacy_doc = build_resolved_player_stats_document(
        player_stats=player_stats,
        identity_assignments=identity_assignments,
        stable_players=stable_players,
    )
    doc = legacy_doc
    quality_report = None
    if (path / "global_identity.json").exists():
        timeline = build_resolved_player_timeline_from_files(path)
        match = _load_json(path, "match.json") if (path / "match.json").exists() else {}
        doc, quality_report = _build_exact_resolved_player_stats_document(
            timeline=timeline,
            identity_assignments=identity_assignments,
            player_stats=player_stats,
            match=match,
            legacy_doc=legacy_doc,
        )
    if persist:
        (path / "resolved_player_stats.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
        if quality_report is not None:
            (path / "resolved_stats_quality_report.json").write_text(
                json.dumps(quality_report, indent=2),
                encoding="utf-8",
            )
    return doc


def _movement_slot_stats(fragment: dict[str, Any], fps: float) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = trusted_stats_rows(fragment.get("rows") or [])
    movement = calculate_movement_stats(rows, fps)
    tracklet_ids = sorted(
        {
            str(row.get("tracklet_id"))
            for row in rows
            if row.get("tracklet_id") is not None
        }
    )
    raw_track_ids = sorted(
        {
            int(row.get("raw_track_id"))
            for row in rows
            if isinstance(row.get("raw_track_id"), int)
        }
    )
    slot_stats = {
        "tracklet_ids": tracklet_ids,
        "raw_track_ids": raw_track_ids,
        "time": {
            "playing_time_sec": movement.get("playing_time_sec", 0.0),
            "detected_time_sec": movement.get("detected_time_sec", 0.0),
            "missing_time_sec": movement.get("missing_time_sec", 0.0),
            "ambiguous_time_sec": movement.get("ambiguous_time_sec", 0.0),
        },
        "distance": {
            "observed_distance_m": movement.get("observed_distance_m", 0.0),
            "estimated_short_gap_distance_m": movement.get("estimated_gap_distance_m", 0.0),
            "total_distance_m": movement.get("total_distance_m", 0.0),
            "estimated_distance_ratio": movement.get("estimated_distance_ratio", 0.0),
            "quality": movement.get("distance_quality", "unknown"),
        },
        "speed": {
            "avg_speed_mps": movement.get("avg_speed_mps", 0.0),
            "avg_speed_kmh": movement.get("avg_speed_kmh", 0.0),
            "observed_avg_speed_mps": movement.get("observed_avg_speed_mps", 0.0),
            "peak_sustained_speed_mps": movement.get("peak_sustained_speed_mps", 0.0),
            "peak_sustained_speed_kmh": movement.get("peak_sustained_speed_kmh", 0.0),
            "top_speed_mps": movement.get("top_speed_mps", 0.0),
            "top_speed_kmh": movement.get("top_speed_kmh", 0.0),
            "raw_segment_top_speed_mps": movement.get("raw_segment_top_speed_mps", 0.0),
            "raw_segment_top_speed_kmh": movement.get("raw_segment_top_speed_kmh", 0.0),
            "quality": movement.get("speed_quality", "unknown"),
        },
        "intensity": movement.get("intensity") if isinstance(movement.get("intensity"), dict) else {},
        "frames": {
            "active_frames": movement.get("active_frames", 0),
            "detected_frames": movement.get("detected_frames", 0),
            "missing_frames": movement.get("missing_frames", 0),
            "ambiguous_frames": movement.get("ambiguous_frames", 0),
            "predicted_frames": movement.get("predicted_frames", 0),
            "samples_used": movement.get("samples_used", 0),
        },
        "segments": {
            "observed_segments": movement.get("observed_segments", 0),
            "estimated_gap_segments": movement.get("estimated_gap_segments", 0),
            "skipped_outlier_segments": movement.get("skipped_outlier_segments", 0),
            "skipped_speed_outlier_segments": movement.get("skipped_speed_outlier_segments", 0),
            "skipped_long_gap_segments": movement.get("skipped_long_gap_segments", 0),
            "sustained_speed_windows": movement.get("sustained_speed_windows", 0),
        },
        "stats_note": movement.get("stats_note"),
    }
    return slot_stats, movement


def _video_duration(match: dict[str, Any], timeline: dict[str, Any]) -> float:
    video = match.get("video") if isinstance(match.get("video"), dict) else {}
    duration = _number(video.get("duration_sec"))
    if duration > 0:
        return duration
    fps = max(_number(timeline.get("fps")), 0.001)
    frames = [
        int(row.get("frame") or 0)
        for player in (timeline.get("players") or {}).values()
        for row in player.get("rows") or []
    ]
    return (max(frames) + 1) / fps if frames else 0.0


def _detected_position_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    points = [
        row.get("pitch_m")
        for row in rows
        if row.get("source") == "detected"
        and row.get("play_area_status", "inside_play") == "inside_play"
        and isinstance(row.get("pitch_m"), (list, tuple))
        and len(row.get("pitch_m")) >= 2
    ]
    if not points:
        return {"samples": 0}
    xs = sorted(float(point[0]) for point in points)
    ys = sorted(float(point[1]) for point in points)

    def percentile(values: list[float], ratio: float) -> float:
        return values[min(len(values) - 1, int(round((len(values) - 1) * ratio)))]

    return {
        "samples": len(points),
        "median_x_m": round(percentile(xs, 0.5), 3),
        "median_y_m": round(percentile(ys, 0.5), 3),
        "y_p10_m": round(percentile(ys, 0.1), 3),
        "y_p90_m": round(percentile(ys, 0.9), 3),
    }


def _legacy_comparison(exact_players: list[dict[str, Any]], legacy_doc: dict[str, Any]) -> list[dict[str, Any]]:
    legacy_by_player = {
        str(player.get("player_id")): player
        for player in legacy_doc.get("players") or []
        if isinstance(player, dict) and player.get("player_id")
    }
    rows = []
    for player in exact_players:
        player_id = str(player.get("player_id") or "")
        legacy = legacy_by_player.get(player_id)
        if not legacy:
            continue
        exact_time = _number(_record(player.get("time")).get("playing_time_sec"))
        legacy_time = _number(_record(legacy.get("time")).get("playing_time_sec"))
        exact_distance = _number(_record(player.get("distance")).get("total_distance_m"))
        legacy_distance = _number(_record(legacy.get("distance")).get("total_distance_m"))
        rows.append(
            {
                "player_id": player_id,
                "player_name": player.get("player_name"),
                "playing_time_delta_sec": round(exact_time - legacy_time, 3),
                "distance_delta_m": round(exact_distance - legacy_distance, 2),
            }
        )
    return rows


def _build_exact_resolved_player_stats_document(
    *,
    timeline: dict[str, Any],
    identity_assignments: dict[str, Any],
    player_stats: dict[str, Any],
    match: dict[str, Any],
    legacy_doc: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    fps = max(_number(timeline.get("fps")), 0.001)
    duration = _video_duration(match, timeline)
    rows_by_player: dict[str, dict[str, Any]] = {}
    distance_qualities: dict[str, list[str]] = {}
    speed_qualities: dict[str, list[str]] = {}
    player_quality: list[dict[str, Any]] = []
    assigned_fragments = 0
    presence = calculate_timeline_presence(timeline, duration_sec=duration)

    for player_id, timeline_player in (timeline.get("players") or {}).items():
        metadata = timeline_player.get("metadata") if isinstance(timeline_player.get("metadata"), dict) else {}
        row = rows_by_player.setdefault(str(player_id), _empty_player_row(metadata))
        quality_flags = list(timeline_player.get("quality_flags") or [])
        for fragment in timeline_player.get("fragments") or []:
            if fragment.get("excluded_reason"):
                continue
            fragment_rows = fragment.get("rows") or []
            if not fragment_rows:
                continue
            assignment = dict(fragment.get("assignment") or metadata)
            slot_stats, movement = _movement_slot_stats(fragment, fps)
            _merge_slot_stats(row, assignment, slot_stats)
            distance_qualities.setdefault(str(player_id), []).append(str(movement.get("distance_quality") or "unknown"))
            speed_qualities.setdefault(str(player_id), []).append(str(movement.get("speed_quality") or "unknown"))
            assigned_fragments += 1

        player_presence = (presence.get("players") or {}).get(str(player_id)) or {}
        presence_frames = _int(player_presence.get("presence_frames"))
        inferred_presence_frames = _int(player_presence.get("inferred_presence_frames"))
        ambiguous_presence_frames = _int(player_presence.get("ambiguous_presence_frames"))
        inferred_non_ambiguous_frames = max(0, inferred_presence_frames - ambiguous_presence_frames)
        if presence_frames > 0:
            row["time"]["playing_time_sec"] = presence_frames / fps
            row["time"]["ambiguous_time_sec"] = ambiguous_presence_frames / fps
            row["time"]["missing_time_sec"] = inferred_non_ambiguous_frames / fps
            row["time"]["inferred_playing_time_sec"] = inferred_presence_frames / fps
            row["frames"]["active_frames"] = presence_frames
            row["frames"]["ambiguous_frames"] = ambiguous_presence_frames
            row["frames"]["missing_frames"] = inferred_non_ambiguous_frames

        finalized = _finalize_player_row(
            row,
            distance_qualities.get(str(player_id), []),
            speed_qualities.get(str(player_id), []),
        )
        detected_rows = [
            item
            for item in timeline_player.get("rows") or []
            if item.get("source") == "detected" and item.get("play_area_status", "inside_play") == "inside_play"
        ]
        excluded_frames = sum(
            int(item.get("excluded_frames") or 0)
            for item in (timeline.get("quality") or {}).get("goalkeeper_anomalous_fragments_excluded") or []
            if str(item.get("player_id")) == str(player_id)
        )
        finalized["calculation_method"] = "exact_identity_coverage"
        finalized["playing_time_method"] = str(presence.get("method") or "exact_detected_only")
        finalized["unique_detected_frames"] = len({int(item.get("frame") or 0) for item in detected_rows})
        finalized["trusted_playing_seconds"] = finalized["time"]["playing_time_sec"]
        finalized["inferred_playing_seconds"] = round(inferred_presence_frames / fps, 3)
        finalized["presence_frames"] = presence_frames
        finalized["presence_evidence"] = player_presence.get("evidence_counts") or {}
        finalized["coverage_ratio"] = round(
            min(1.0, _number(finalized["time"].get("playing_time_sec")) / duration),
            4,
        ) if duration > 0 else 0.0
        finalized["excluded_frames"] = excluded_frames
        finalized["quality_flags"] = sorted({*quality_flags, *finalized.get("review_warnings", [])})
        rows_by_player[str(player_id)] = finalized
        player_quality.append(
            {
                "player_id": player_id,
                "player_name": finalized.get("player_name"),
                "assignments": len(timeline_player.get("fragments") or []),
                "unique_detected_frames": finalized["unique_detected_frames"],
                "trusted_playing_seconds": finalized["trusted_playing_seconds"],
                "inferred_playing_seconds": finalized["inferred_playing_seconds"],
                "presence_frames": finalized["presence_frames"],
                "presence_evidence": finalized["presence_evidence"],
                "coverage_ratio": finalized["coverage_ratio"],
                "excluded_frames": excluded_frames,
                "position": _detected_position_summary(timeline_player.get("rows") or []),
                "quality_flags": finalized["quality_flags"],
            }
        )

    players = sorted(rows_by_player.values(), key=lambda item: str(item.get("player_name") or item.get("player_id") or ""))
    teams = _team_rows(players)
    expected_players = 7
    for team in teams:
        theoretical = duration * expected_players
        team["video_duration_sec"] = round(duration, 3)
        team["theoretical_player_time_sec"] = round(theoretical, 3)
        team["coverage_ratio"] = round(min(1.0, _number(team.get("playing_time_sec")) / theoretical), 4) if theoretical > 0 else 0.0

    quality = timeline.get("quality") if isinstance(timeline.get("quality"), dict) else {}
    summary = {
        "players": len(players),
        "assigned_slots": sum(len(player.get("source_stable_slots") or []) for player in players),
        "assigned_stints": assigned_fragments,
        "unresolved_slots": int((identity_assignments.get("summary") or {}).get("unassigned_slots") or 0),
        "skipped_assignments": int(quality.get("assignments_unresolved") or 0),
        "overlapping_stint_assignments_clipped": int(quality.get("duplicate_frames_removed") or 0),
        "players_with_warnings": sum(1 for player in players if player.get("quality_flags")),
        "total_distance_m": round(sum(_number(player["distance"].get("total_distance_m")) for player in players), 2),
        "observed_distance_m": round(sum(_number(player["distance"].get("observed_distance_m")) for player in players), 2),
        "estimated_short_gap_distance_m": round(sum(_number(player["distance"].get("estimated_short_gap_distance_m")) for player in players), 2),
        "playing_time_sec": round(sum(_number(player["time"].get("playing_time_sec")) for player in players), 2),
        "detected_time_sec": round(sum(_number(player["time"].get("detected_time_sec")) for player in players), 2),
        "missing_time_sec": round(sum(_number(player["time"].get("missing_time_sec")) for player in players), 2),
        "ambiguous_time_sec": round(sum(_number(player["time"].get("ambiguous_time_sec")) for player in players), 2),
        "inferred_playing_time_sec": round(sum(_number(player["time"].get("inferred_playing_time_sec")) for player in players), 2),
        "unique_detected_frames": sum(_int(player.get("unique_detected_frames")) for player in players),
        "video_duration_sec": round(duration, 3),
        "calculation_method": "exact_identity_coverage",
        "peak_sustained_speed_kmh": round(max([_number(player["speed"].get("peak_sustained_speed_kmh")) for player in players] or [0.0]), 2),
        "top_speed_kmh": round(max([_number(player["speed"].get("top_speed_kmh")) for player in players] or [0.0]), 2),
        "high_intensity_time_sec": round(sum(_number(_record(player.get("intensity")).get("high_intensity_time_sec")) for player in players), 2),
        "high_intensity_distance_m": round(sum(_number(_record(player.get("intensity")).get("high_intensity_distance_m")) for player in players), 2),
        "sprint_count": sum(_int(_record(player.get("intensity")).get("sprint_count")) for player in players),
        "sprint_time_sec": round(sum(_number(_record(player.get("intensity")).get("sprint_time_sec")) for player in players), 2),
        "sprint_distance_m": round(sum(_number(_record(player.get("intensity")).get("sprint_distance_m")) for player in players), 2),
        "sprint_candidate_count": sum(_int(_record(player.get("intensity")).get("sprint_candidate_count")) for player in players),
        "rejected_sprint_candidate_count": sum(_int(_record(player.get("intensity")).get("rejected_sprint_candidate_count")) for player in players),
    }
    doc = {
        "schema_version": "0.2.0",
        "generated_at": now_iso(),
        "source": "player_identity_assignments",
        "stats_source": "global_identity_exact_frames",
        "calculation_method": "exact_identity_coverage",
        "identity_assignments_updated_at": identity_assignments.get("updated_at"),
        "is_stale": False,
        "identity_semantics": identity_assignments.get("identity_semantics") or player_stats.get("identity_semantics") or "stint_first",
        "scope": "resolved_player_tracking_only_no_ball",
        "units": player_stats.get("units") or {"distance": "meters", "speed": "mps_and_kmh", "time": "seconds"},
        "summary": summary,
        "teams": teams,
        "players": players,
        "skipped_assignments": quality.get("unresolved_assignments") or [],
        "quality_report": "resolved_stats_quality_report.json",
    }
    quality_report = {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "calculation_method": "exact_identity_coverage",
        "video_duration_sec": round(duration, 3),
        "timeline": quality,
        "presence": presence.get("quality") or {},
        "players": player_quality,
        "teams": teams,
        "legacy_estimate_comparison": _legacy_comparison(players, legacy_doc),
    }
    return doc, quality_report
