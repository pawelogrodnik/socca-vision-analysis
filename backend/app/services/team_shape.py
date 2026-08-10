from __future__ import annotations

import math
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

from app.services.artifact_lineage import canonical_json_sha256, generated_from_entry
from app.services.match_phase_config import (
    ATTACK_DIRECTIONS,
    configured_active_periods,
    direction_for_team_at_time,
    match_phase_directions_are_trusted,
)
from app.services.team_assignment import is_trusted_tracklet_team_assignment


ALGORITHM_VERSION = "team_shape_spatial_v1_1"
MIN_TEAM_POSITIONS = 5
MAX_TEAM_POSITIONS = 7
TIMELINE_BIN_SEC = 60.0
MIN_TIMELINE_COVERAGE = 0.5
DENSITY_COLUMNS = 6
DENSITY_ROWS = 10
SOURCE_ARTIFACTS = (
    "tracklets.json",
    "pitch_config.json",
    "match_phase_config.json",
    "team_config.json",
    "match.json",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_team_oriented_coordinates(
    pitch_m: list[float] | tuple[float, float],
    attack_direction: str,
    pitch_width_m: float,
    pitch_length_m: float,
) -> dict[str, float]:
    x_m, y_m = float(pitch_m[0]), float(pitch_m[1])
    direction = str(attack_direction or "unknown")
    if direction == "towards_y_max":
        lateral_m, progress_m = x_m, y_m
        lateral_axis_m, attack_axis_m = pitch_width_m, pitch_length_m
    elif direction == "towards_y_min":
        lateral_m, progress_m = pitch_width_m - x_m, pitch_length_m - y_m
        lateral_axis_m, attack_axis_m = pitch_width_m, pitch_length_m
    elif direction == "towards_x_max":
        lateral_m, progress_m = pitch_length_m - y_m, x_m
        lateral_axis_m, attack_axis_m = pitch_length_m, pitch_width_m
    elif direction == "towards_x_min":
        lateral_m, progress_m = y_m, pitch_width_m - x_m
        lateral_axis_m, attack_axis_m = pitch_length_m, pitch_width_m
    else:
        raise ValueError(f"Unsupported attack direction: {direction}")
    return {
        "lateral_m": lateral_m,
        "progress_m": progress_m,
        "lateral_percent": lateral_m / lateral_axis_m * 100.0,
        "progress_percent": progress_m / attack_axis_m * 100.0,
        "lateral_axis_m": lateral_axis_m,
        "attack_axis_m": attack_axis_m,
    }


def calculate_frame_shape(
    positions_m: list[list[float] | tuple[float, float]],
    attack_direction: str,
    pitch_width_m: float,
    pitch_length_m: float,
) -> dict[str, Any] | None:
    if not MIN_TEAM_POSITIONS <= len(positions_m) <= MAX_TEAM_POSITIONS:
        return None
    oriented = [
        to_team_oriented_coordinates(point, attack_direction, pitch_width_m, pitch_length_m)
        for point in positions_m
    ]
    lateral = [point["lateral_m"] for point in oriented]
    progress = [point["progress_m"] for point in oriented]
    centroid_lateral = sum(lateral) / len(lateral)
    centroid_progress = sum(progress) / len(progress)
    compactness = sum(
        math.hypot(point["lateral_m"] - centroid_lateral, point["progress_m"] - centroid_progress)
        for point in oriented
    ) / len(oriented)
    attack_axis_m = oriented[0]["attack_axis_m"]
    result = {
        "players": len(oriented),
        "width_m": max(lateral) - min(lateral),
        "depth_m": max(progress) - min(progress),
        "centroid_lateral_m": centroid_lateral,
        "centroid_progress_m": centroid_progress,
        "compactness_m": compactness,
        "block_height_percent": centroid_progress / attack_axis_m * 100.0,
        "oriented_positions": oriented,
    }
    if not (
        0.0 <= result["width_m"] <= oriented[0]["lateral_axis_m"]
        and 0.0 <= result["depth_m"] <= attack_axis_m
        and result["compactness_m"] >= 0.0
        and 0.0 <= result["block_height_percent"] <= 100.0
    ):
        return None
    return result


def observations_from_tracklets(tracklets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for tracklet in tracklets:
        team_label = str(tracklet.get("team_label") or "U").upper()
        trusted = is_trusted_tracklet_team_assignment(tracklet)
        for position in tracklet.get("positions") or tracklet.get("positions_m") or []:
            observations.append(
                {
                    "frame": position.get("frame"),
                    "time_sec": position.get("time_sec"),
                    "team_label": team_label,
                    "pitch_m": position.get("smoothed_pitch_m") or position.get("pitch_m"),
                    "play_area_status": position.get("play_area_status") or "inside_play",
                    "source": position.get("source") or "detected",
                    "trusted": trusted,
                    "team_confidence": tracklet.get("team_confidence"),
                    "team_assignment_reason": tracklet.get("team_assignment_reason"),
                    "team_cluster_id": tracklet.get("team_cluster_id"),
                    "team_id": tracklet.get("team_id"),
                }
            )
    return observations


def rebuild_team_shape_artifact(match_path: Path) -> dict[str, Any] | None:
    required = {filename: _load_json(match_path / filename) for filename in SOURCE_ARTIFACTS}
    if any(document is None for document in required.values()):
        return None
    tracklets_doc = required["tracklets.json"] or {}
    pitch_config = required["pitch_config.json"] or {}
    match_phase_config = required["match_phase_config.json"] or {}
    team_config = required["team_config.json"] or {}
    match = required["match.json"] or {}
    video = match.get("video") if isinstance(match.get("video"), dict) else {}
    document = build_team_shape_document(
        player_observations=observations_from_tracklets(tracklets_doc.get("tracklets") or []),
        pitch_width_m=float(pitch_config.get("width_m")),
        pitch_length_m=float(pitch_config.get("length_m")),
        match_phase_config=match_phase_config,
        team_config=team_config,
        video_duration_sec=float(video.get("duration_sec") or 0.0),
    )
    document["generated_from"] = [
        generated_from_entry(filename, source)
        for filename, source in required.items()
        if source is not None
    ]
    (match_path / "team_shape.json").write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")
    return document


def ensure_team_shape_artifact_fresh(match_path: Path) -> dict[str, Any] | None:
    current = _load_json(match_path / "team_shape.json")
    if _freshness_status(match_path, current) == "fresh":
        return current
    try:
        return rebuild_team_shape_artifact(match_path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def build_team_shape_document(
    *,
    player_observations: list[dict[str, Any]],
    pitch_width_m: float,
    pitch_length_m: float,
    match_phase_config: dict[str, Any] | None,
    team_config: dict[str, Any] | None = None,
    video_duration_sec: float | None = None,
    expected_sample_interval_sec: float | None = None,
) -> dict[str, Any]:
    _validate_pitch(pitch_width_m, pitch_length_m)
    eligible = [row for row in player_observations if _eligible(row, pitch_width_m, pitch_length_m)]
    sample_interval_sec = expected_sample_interval_sec or _infer_sample_interval(eligible)
    duration_sec = max(
        float(video_duration_sec or 0.0),
        max([float(row.get("time_sec") or 0.0) for row in eligible] or [0.0]) + sample_interval_sec,
    )
    active_periods = configured_active_periods(match_phase_config, duration_sec=duration_sec)
    active_period_duration_sec = sum(end - start for start, end in active_periods)
    expected_active_samples = math.ceil(active_period_duration_sec / sample_interval_sec) if active_period_duration_sec > 0 else 0
    direction_trusted = match_phase_directions_are_trusted(match_phase_config)
    grouped: dict[tuple[str, int, float], list[dict[str, Any]]] = defaultdict(list)
    for row in eligible:
        grouped[(str(row["team_label"]), int(row.get("frame") or 0), float(row.get("time_sec") or 0.0))].append(row)

    details_by_team = _team_details(team_config)
    teams = []
    for team_label in ("A", "B"):
        frame_shapes = []
        candidate_frames = 0
        direction_frames = 0
        over_cap_frames = 0
        invalid_geometry_frames = 0
        for (label, frame, time_sec), rows in sorted(grouped.items(), key=lambda item: (item[0][2], item[0][1])):
            if label != team_label:
                continue
            phase = direction_for_team_at_time(match_phase_config, team_label, time_sec)
            if phase["period_id"] is None:
                continue
            candidate_frames += 1
            direction = phase["attack_direction"]
            if direction not in ATTACK_DIRECTIONS - {"unknown"}:
                continue
            direction_frames += 1
            if len(rows) > MAX_TEAM_POSITIONS:
                over_cap_frames += 1
                continue
            shape = calculate_frame_shape([row["pitch_m"] for row in rows], direction, pitch_width_m, pitch_length_m)
            if shape is None:
                if MIN_TEAM_POSITIONS <= len(rows) <= MAX_TEAM_POSITIONS:
                    invalid_geometry_frames += 1
                continue
            frame_shapes.append({**shape, "frame": frame, "time_sec": time_sec})

        coverage = len(frame_shapes) / expected_active_samples if expected_active_samples > 0 else 0.0
        direction_coverage = direction_frames / max(1, candidate_frames)
        over_cap_ratio = over_cap_frames / max(1, candidate_frames)
        median_players = float(median([row["players"] for row in frame_shapes])) if frame_shapes else 0.0
        spatial_readiness = _readiness(coverage, median_players, over_cap_ratio, direction_coverage)
        readiness = _direction_gated_readiness(spatial_readiness, direction_trusted)
        details = details_by_team.get(team_label, {})
        teams.append(
            {
                "team_label": team_label,
                "team_id": details.get("team_id"),
                "team_name": details.get("team_name") or f"Team {team_label}",
                "readiness": readiness,
                "summary": _summary(frame_shapes),
                "average_shape": _density(frame_shapes),
                "timeline": _timeline(frame_shapes, duration_sec, sample_interval_sec, active_periods),
                "diagnostics": {
                    "active_period_duration_sec": round(active_period_duration_sec, 3),
                    "expected_active_samples": expected_active_samples,
                    "eligible_frames": len(frame_shapes),
                    "candidate_frames": candidate_frames,
                    "over_cap_frames": over_cap_frames,
                    "invalid_geometry_frames": invalid_geometry_frames,
                    "temporal_coverage": round(coverage, 4),
                    "direction_coverage": round(direction_coverage, 4),
                    "attack_direction_trusted": direction_trusted,
                    "over_cap_frame_ratio": round(over_cap_ratio, 4),
                    "median_usable_players": median_players,
                },
            }
        )

    available = len(teams) == 2 and all(team["readiness"] == "ready" for team in teams)
    return {
        "schema_version": "team-shape-v1",
        "algorithm_version": ALGORITHM_VERSION,
        "generated_at": now_iso(),
        "source": "trusted_detected_tracklet_positions",
        "scope": "all_in_play",
        "available": available,
        "readiness": "ready" if available else _document_readiness(teams),
        "pitch_dimensions_m": {"width_m": pitch_width_m, "length_m": pitch_length_m},
        "parameters": {
            "minimum_team_positions": MIN_TEAM_POSITIONS,
            "maximum_team_positions": MAX_TEAM_POSITIONS,
            "timeline_bin_sec": TIMELINE_BIN_SEC,
            "minimum_timeline_coverage": MIN_TIMELINE_COVERAGE,
            "expected_sample_interval_sec": round(sample_interval_sec, 6),
            "active_period_duration_sec": round(active_period_duration_sec, 3),
            "expected_active_samples": expected_active_samples,
            "density_columns": DENSITY_COLUMNS,
            "density_rows": DENSITY_ROWS,
        },
        "teams": teams,
        "takeaways": build_team_shape_takeaways(teams) if available else [],
    }


def _eligible(row: dict[str, Any], pitch_width_m: float, pitch_length_m: float) -> bool:
    if str(row.get("team_label") or "").upper() not in {"A", "B"}:
        return False
    if row.get("trusted", True) is False or str(row.get("source") or "detected") != "detected":
        return False
    if str(row.get("play_area_status") or "inside_play") != "inside_play":
        return False
    point = row.get("pitch_m")
    if not isinstance(point, (list, tuple)) or len(point) < 2:
        return False
    try:
        x_m, y_m = float(point[0]), float(point[1])
    except (TypeError, ValueError):
        return False
    return math.isfinite(x_m) and math.isfinite(y_m) and 0.0 <= x_m <= pitch_width_m and 0.0 <= y_m <= pitch_length_m


def _summary(rows: list[dict[str, Any]]) -> dict[str, float] | None:
    if not rows:
        return None
    return {
        "average_width_m": round(sum(row["width_m"] for row in rows) / len(rows), 2),
        "average_depth_m": round(sum(row["depth_m"] for row in rows) / len(rows), 2),
        "average_compactness_m": round(sum(row["compactness_m"] for row in rows) / len(rows), 2),
        "average_block_height_percent": round(sum(row["block_height_percent"] for row in rows) / len(rows), 2),
    }


def _density(frame_shapes: list[dict[str, Any]]) -> dict[str, Any]:
    values: dict[tuple[int, int], float] = defaultdict(float)
    for shape in frame_shapes:
        player_weight = 1.0 / len(frame_shapes) / shape["players"]
        for point in shape["oriented_positions"]:
            column = min(DENSITY_COLUMNS - 1, int(point["lateral_percent"] / 100.0 * DENSITY_COLUMNS))
            row = min(DENSITY_ROWS - 1, int(point["progress_percent"] / 100.0 * DENSITY_ROWS))
            values[(column, row)] += player_weight
    return {
        "grid": {"columns": DENSITY_COLUMNS, "rows": DENSITY_ROWS},
        "cells": [
            {"column": column, "row": row, "value": round(value, 6)}
            for (column, row), value in sorted(values.items())
            if value > 0.0
        ],
    }


def _timeline(
    rows: list[dict[str, Any]],
    duration_sec: float,
    sample_interval_sec: float,
    active_periods: list[tuple[float, float]],
) -> list[dict[str, Any]]:
    bin_count = max(1, math.ceil(duration_sec / TIMELINE_BIN_SEC))
    by_bin: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_bin[min(bin_count - 1, int(row["time_sec"] // TIMELINE_BIN_SEC))].append(row)
    timeline = []
    for index in range(bin_count):
        start = index * TIMELINE_BIN_SEC
        end = min(duration_sec, start + TIMELINE_BIN_SEC)
        active_duration = sum(max(0.0, min(end, period_end) - max(start, period_start)) for period_start, period_end in active_periods)
        expected = math.ceil(active_duration / sample_interval_sec) if active_duration > 0 else 0
        values = by_bin.get(index, [])
        summary = _summary(values) if expected > 0 and len(values) / expected >= MIN_TIMELINE_COVERAGE else None
        timeline.append(
            {
                "minute": index + 1,
                "label": f"{int(start // 60):02d}:{int(start % 60):02d}",
                "active_period_duration_sec": round(active_duration, 3),
                "width_m": summary["average_width_m"] if summary else None,
                "depth_m": summary["average_depth_m"] if summary else None,
                "compactness_m": summary["average_compactness_m"] if summary else None,
                "block_height_percent": summary["average_block_height_percent"] if summary else None,
            }
        )
    return timeline


def _infer_sample_interval(rows: list[dict[str, Any]]) -> float:
    times = sorted({float(row.get("time_sec") or 0.0) for row in rows})
    differences = [following - previous for previous, following in zip(times, times[1:]) if following > previous]
    return max(0.001, float(median(differences))) if differences else 1.0


def _readiness(coverage: float, median_players: float, over_cap_ratio: float, direction_coverage: float) -> str:
    if coverage >= 0.8 and median_players >= 6.0 and over_cap_ratio <= 0.02 and direction_coverage >= 0.95:
        return "ready"
    if coverage >= 0.6 and median_players >= 5.0 and over_cap_ratio <= 0.05 and direction_coverage >= 0.8:
        return "experimental"
    return "not_available"


def _direction_gated_readiness(spatial_readiness: str, direction_trusted: bool) -> str:
    if direction_trusted or spatial_readiness == "not_available":
        return spatial_readiness
    return "experimental"


def _document_readiness(teams: list[dict[str, Any]]) -> str:
    statuses = {str(team.get("readiness")) for team in teams}
    return "experimental" if statuses and statuses <= {"ready", "experimental"} else "not_available"


def _team_details(team_config: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    rows = team_config.get("teams") if isinstance(team_config, dict) else []
    return {str(row.get("team_label")): row for row in rows or [] if isinstance(row, dict)}


def build_team_shape_takeaways(teams: list[dict[str, Any]]) -> list[str]:
    by_label = {team["team_label"]: team for team in teams}
    if not by_label.get("A", {}).get("summary") or not by_label.get("B", {}).get("summary"):
        return []
    team_a, team_b = by_label["A"], by_label["B"]
    a, b = team_a["summary"], team_b["summary"]
    name_a, name_b = team_a["team_name"], team_b["team_name"]
    comparisons = [
        ("average_width_m", 2.0, "szerzej"),
        ("average_depth_m", 2.0, "dłuższe ustawienie"),
        ("average_compactness_m", 1.0, "mniej zwarte ustawienie"),
        ("average_block_height_percent", 5.0, "wyżej"),
    ]
    takeaways = []
    for key, threshold, phrase in comparisons:
        difference = float(a[key]) - float(b[key])
        if abs(difference) < threshold:
            continue
        higher, lower = (name_a, name_b) if difference > 0 else (name_b, name_a)
        amount = abs(difference)
        if key == "average_block_height_percent":
            takeaways.append(f"{higher} ustawiał się średnio o {amount:.0f} punktów procentowych {phrase} niż {lower}.")
        elif key == "average_compactness_m":
            takeaways.append(f"{higher} utrzymywał średnio o {amount:.1f} m {phrase} niż {lower}.")
        else:
            takeaways.append(f"{higher} grał średnio o {amount:.1f} m {phrase} niż {lower}.")
        if len(takeaways) == 3:
            break
    return takeaways


def _validate_pitch(width_m: float, length_m: float) -> None:
    if not math.isfinite(width_m) or not math.isfinite(length_m) or width_m <= 0.0 or length_m <= 0.0:
        raise ValueError("Pitch dimensions must be finite positive values")


def _freshness_status(match_path: Path, document: dict[str, Any] | None) -> str:
    if not document or not isinstance(document.get("generated_from"), list):
        return "missing_inputs"
    if document.get("algorithm_version") != ALGORITHM_VERSION:
        return "stale"
    entries = document["generated_from"]
    artifacts = {
        str(entry.get("artifact"))
        for entry in entries
        if isinstance(entry, dict) and entry.get("artifact")
    }
    if artifacts != set(SOURCE_ARTIFACTS):
        return "stale"
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("artifact") or not entry.get("sha256"):
            return "legacy_unknown"
        source = _load_json(match_path / str(entry["artifact"]))
        if source is None:
            return "missing_inputs"
        if canonical_json_sha256(source) != str(entry["sha256"]):
            return "stale"
    return "fresh"


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None