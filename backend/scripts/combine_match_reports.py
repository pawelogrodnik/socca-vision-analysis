#!/usr/bin/env python3
from __future__ import annotations

"""
Offline combiner for already analysed/reviewed football-video fragments.

It does NOT rerun YOLO, tracking, Reviewed Identity, possession/pass detection,
or mutate source matches. It only reads existing artifacts and creates a new
combined report directory.

Run from backend/:

  .venv-mps/bin/python scripts/combine_match_reports.py \
    9c7485e4 6d8fc20c 5e62625e \
    --title "Corgi - Verisk | full analysed match" \
    --output combined-reports/corgi-verisk-full

Default logical clock: fragments are contiguous in the order supplied.
Default report windows: 5 minutes.
"""

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

SCHEMA_VERSION = "offline-combined-match-report-v1"
HEATMAP_GRID_WIDTH = 48
HEATMAP_GRID_LENGTH = 96


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, required: bool = False) -> dict[str, Any] | None:
    if not path.exists():
        if required:
            raise FileNotFoundError(path)
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def num(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    return float(value) if isinstance(value, (int, float)) else default


def integer(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    return int(value) if isinstance(value, (int, float)) else default


def clock(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    return f"{total // 60}:{total % 60:02d}"


def safe_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-")
    return value or "artifact"


def lowest_quality(values: list[str]) -> str:
    rank = {"not_available": 0, "low": 1, "medium": 2, "high": 3}
    values = [v for v in values if v in rank]
    return min(values, key=lambda v: rank[v]) if values else "not_available"


def source_duration(match: dict[str, Any], stats: dict[str, Any]) -> float:
    timing = stats.get("video_timing") if isinstance(stats.get("video_timing"), dict) else {}
    if num(timing.get("duration_sec")) > 0:
        return num(timing["duration_sec"])
    video = match.get("video") if isinstance(match.get("video"), dict) else {}
    if num(video.get("duration_sec")) > 0:
        return num(video["duration_sec"])
    raise ValueError(f"Cannot determine duration for {match.get('id')}")


def source_fps(match: dict[str, Any], stats: dict[str, Any]) -> float:
    timing = stats.get("video_timing") if isinstance(stats.get("video_timing"), dict) else {}
    if num(timing.get("fps")) > 0:
        return num(timing["fps"])
    video = match.get("video") if isinstance(match.get("video"), dict) else {}
    return num(video.get("fps"), 30.0) or 30.0


def team_maps(match: dict[str, Any], team_config: dict[str, Any]):
    teams_by_id: dict[str, dict[str, Any]] = {}
    roster: dict[str, dict[str, Any]] = {}
    for team in match.get("teams") or []:
        if not isinstance(team, dict):
            continue
        team_id = str(team.get("id") or "").strip()
        if not team_id:
            raise ValueError("match.teams[] must expose stable team IDs")
        teams_by_id[team_id] = team
        for player in team.get("players") or []:
            if not isinstance(player, dict):
                continue
            player_id = str(player.get("id") or "").strip()
            if not player_id:
                continue
            old = roster.get(player_id)
            if old and old["team_id"] != team_id:
                raise ValueError(f"Player {player_id} appears in more than one team")
            roster[player_id] = {
                "player_id": player_id,
                "team_id": team_id,
                "player_name": player.get("name") or player.get("player_name") or player_id,
                "player_number": player.get("number") or player.get("shirt_number") or player.get("roster_number"),
                "player_role": player.get("role"),
            }

    label_to_team_id: dict[str, str] = {}
    for row in team_config.get("teams") or []:
        if not isinstance(row, dict):
            continue
        label = str(row.get("team_label") or "").upper()
        team_id = str(row.get("team_id") or "").strip()
        if label in {"A", "B"} and team_id:
            if team_id not in teams_by_id:
                raise ValueError(f"team_config {label} -> unknown team_id {team_id}")
            label_to_team_id[label] = team_id
    if set(label_to_team_id) != {"A", "B"}:
        raise ValueError("team_config must map A and B to stable team IDs")
    return label_to_team_id, teams_by_id, roster


def load_source(matches_root: Path, match_id: str) -> dict[str, Any]:
    path = matches_root / match_id
    match = read_json(path / "match.json", True) or {}
    team_config = read_json(path / "team_config.json", True) or {}
    stats = read_json(path / "reviewed_player_stats.json", True) or {}
    label_map, teams, roster = team_maps(match, team_config)
    return {
        "id": match_id,
        "path": path,
        "match": match,
        "team_config": team_config,
        "reviewed_stats": stats,
        "reviewed_heatmaps": read_json(path / "reviewed_player_heatmaps.json"),
        "pitch_config": read_json(path / "pitch_config.json"),
        "tracklets": read_json(path / "tracklets.json"),
        "match_phase_config": read_json(path / "match_phase_config.json"),
        "possession_candidates": read_json(path / "possession_candidates.json"),
        "pass_candidates": read_json(path / "pass_candidates.json"),
        "attacking_momentum": read_json(path / "attacking_momentum.json"),
        "team_stats": read_json(path / "team_stats.json"),
        "label_to_team_id": label_map,
        "teams_by_id": teams,
        "roster": roster,
        "duration_sec": source_duration(match, stats),
        "fps": source_fps(match, stats),
    }


def validate_sources(sources: list[dict[str, Any]]):
    first_ids = list(sources[0]["teams_by_id"].keys())
    expected = set(first_ids)
    team_meta: dict[str, dict[str, Any]] = {}
    roster: dict[str, dict[str, Any]] = {}
    for source in sources:
        actual = set(source["teams_by_id"].keys())
        if actual != expected:
            raise ValueError(f"Stable team IDs differ in {source['id']}: {sorted(actual)} != {sorted(expected)}")
        for team_id, team in source["teams_by_id"].items():
            team_meta.setdefault(team_id, {
                "team_id": team_id,
                "team_name": team.get("name") or team_id,
                "display_color": team.get("color"),
            })
        for player_id, row in source["roster"].items():
            old = roster.get(player_id)
            if old and old["team_id"] != row["team_id"]:
                raise ValueError(f"Player {player_id} changes stable team across fragments")
            roster.setdefault(player_id, dict(row))
    return first_ids, team_meta, roster


def pitch_dimensions(sources: list[dict[str, Any]]) -> tuple[float, float]:
    dims = []
    for source in sources:
        heatmaps = source.get("reviewed_heatmaps") or {}
        pitch = heatmaps.get("pitch_dimensions_m") if isinstance(heatmaps, dict) else {}
        if not isinstance(pitch, dict) or not pitch:
            pitch = source.get("pitch_config") or {}
        width, length = num(pitch.get("width_m")), num(pitch.get("length_m"))
        if width > 0 and length > 0:
            dims.append((width, length, source["id"]))
    if not dims:
        raise ValueError("No source exposes pitch dimensions")
    width, length, _ = dims[0]
    for w, l, match_id in dims[1:]:
        if abs(w - width) > 1e-6 or abs(l - length) > 1e-6:
            raise ValueError(f"Pitch dimensions differ in {match_id}: {w}x{l} vs {width}x{length}")
    return width, length


def aggregate_players(sources, roster, team_meta):
    acc: dict[str, dict[str, Any]] = {}
    for source in sources:
        for row in source["reviewed_stats"].get("players") or []:
            if not isinstance(row, dict):
                continue
            player_id = str(row.get("player_id") or "").strip()
            if not player_id:
                continue
            roster_row = roster.get(player_id)
            local_label = str(row.get("team_label") or "").upper()
            team_id = roster_row["team_id"] if roster_row else source["label_to_team_id"].get(local_label)
            if not team_id:
                raise ValueError(f"Cannot map player {player_id} to stable team")
            speed = row.get("speed") if isinstance(row.get("speed"), dict) else {}
            intensity = row.get("intensity") if isinstance(row.get("intensity"), dict) else {}
            target = acc.setdefault(player_id, {
                "player_id": player_id,
                "player_name": (roster_row or {}).get("player_name") or row.get("player_name") or player_id,
                "player_number": (roster_row or {}).get("player_number") or row.get("roster_number"),
                "player_role": (roster_row or {}).get("player_role"),
                "team_id": team_id,
                "team_name": team_meta[team_id]["team_name"],
                "source_match_ids": [],
                "total_distance_m": 0.0,
                "observed_distance_m": 0.0,
                "estimated_short_gap_distance_m": 0.0,
                "movement_time_sec": 0.0,
                "detected_time_sec": 0.0,
                "confirmed_detected_observations": 0,
                "heatmap_samples": 0,
                "observed_movement_segments": 0,
                "estimated_gap_movement_segments": 0,
                "accepted_movement_segments": 0,
                "high_intensity_time_sec": 0.0,
                "high_intensity_distance_m": 0.0,
                "high_intensity_segments": 0,
                "sprint_count": 0,
                "sprint_time_sec": 0.0,
                "sprint_distance_m": 0.0,
                "max_sprint_speed_kmh": 0.0,
                "_peak": 0.0,
                "_raw_peak": 0.0,
                "_qualities": [],
            })
            if target["team_id"] != team_id:
                raise ValueError(f"Player {player_id} changes team across fragments")
            target["source_match_ids"].append(source["id"])
            for key in ("total_distance_m", "observed_distance_m", "estimated_short_gap_distance_m", "movement_time_sec", "detected_time_sec"):
                target[key] += num(row.get(key))
            for key in ("confirmed_detected_observations", "heatmap_samples", "observed_movement_segments", "estimated_gap_movement_segments", "accepted_movement_segments"):
                target[key] += integer(row.get(key))
            target["_peak"] = max(target["_peak"], num(speed.get("peak_sustained_speed_kmh") or speed.get("top_speed_kmh")))
            target["_raw_peak"] = max(target["_raw_peak"], num(speed.get("raw_segment_top_speed_kmh")))
            target["_qualities"].append(str(speed.get("speed_quality") or "not_available"))
            for key in ("high_intensity_time_sec", "high_intensity_distance_m", "sprint_time_sec", "sprint_distance_m"):
                target[key] += num(intensity.get(key))
            for key in ("high_intensity_segments", "sprint_count"):
                target[key] += integer(intensity.get(key))
            target["max_sprint_speed_kmh"] = max(target["max_sprint_speed_kmh"], num(intensity.get("max_sprint_speed_kmh")))

    out = []
    for row in acc.values():
        distance = row["total_distance_m"]
        movement_time = row["movement_time_sec"]
        detected_time = row["detected_time_sec"]
        observed = row["observed_distance_m"]
        high = row["high_intensity_distance_m"]
        sprint = row["sprint_distance_m"]
        row.update({
            "total_distance_m": round(distance, 2),
            "observed_distance_m": round(observed, 2),
            "estimated_short_gap_distance_m": round(row["estimated_short_gap_distance_m"], 2),
            "movement_time_sec": round(movement_time, 3),
            "detected_time_sec": round(detected_time, 3),
            "avg_speed_kmh": round(distance / movement_time * 3.6, 2) if movement_time > 0 else 0.0,
            "observed_avg_speed_kmh": round(observed / detected_time * 3.6, 2) if detected_time > 0 else 0.0,
            "peak_speed_kmh": round(row.pop("_peak"), 2),
            "raw_segment_top_speed_kmh": round(row.pop("_raw_peak"), 2),
            "speed_quality": lowest_quality(row.pop("_qualities")),
            "high_intensity_time_sec": round(row["high_intensity_time_sec"], 3),
            "high_intensity_distance_m": round(high, 2),
            "high_intensity_distance_ratio": round(high / distance, 4) if distance > 0 else 0.0,
            "sprint_time_sec": round(row["sprint_time_sec"], 3),
            "sprint_distance_m": round(sprint, 2),
            "sprint_distance_ratio": round(sprint / distance, 4) if distance > 0 else 0.0,
            "max_sprint_speed_kmh": round(row["max_sprint_speed_kmh"], 2),
            "source_match_ids": list(dict.fromkeys(row["source_match_ids"])),
        })
        out.append(row)
    return sorted(out, key=lambda r: (r["team_name"], r["player_name"]))


def aggregate_team_movement(sources, team_ids, team_meta):
    out = {team_id: {
        "team_id": team_id,
        "team_name": team_meta[team_id]["team_name"],
        "total_distance_m": 0.0,
        "observed_distance_m": 0.0,
        "estimated_short_gap_distance_m": 0.0,
        "accepted_movement_segments": 0,
        "safe_observation_count": 0,
        "high_intensity_distance_m": 0.0,
        "sprint_count": 0,
        "peak_speed_kmh": 0.0,
    } for team_id in team_ids}
    for source in sources:
        label_map = source["label_to_team_id"]
        for row in source["reviewed_stats"].get("teams") or []:
            if not isinstance(row, dict):
                continue
            team_id = label_map.get(str(row.get("team_label") or "").upper())
            if not team_id:
                continue
            target = out[team_id]
            for key in ("total_distance_m", "observed_distance_m", "estimated_short_gap_distance_m", "high_intensity_distance_m"):
                target[key] += num(row.get(key))
            target["accepted_movement_segments"] += integer(row.get("accepted_movement_segments"))
            target["safe_observation_count"] += integer(row.get("safe_observation_count"))
        doc = source.get("team_stats") or {}
        for row in doc.get("teams") or []:
            if not isinstance(row, dict):
                continue
            team_id = label_map.get(str(row.get("team_label") or "").upper())
            if not team_id:
                continue
            out[team_id]["sprint_count"] += integer(row.get("sprint_count"))
            out[team_id]["peak_speed_kmh"] = max(out[team_id]["peak_speed_kmh"], num(row.get("peak_sustained_speed_kmh") or row.get("top_speed_kmh") or row.get("peak_speed_kmh")))
    for row in out.values():
        for key in ("total_distance_m", "observed_distance_m", "estimated_short_gap_distance_m", "high_intensity_distance_m", "peak_speed_kmh"):
            row[key] = round(row[key], 2)
    return out

def possession_samples(source: dict[str, Any]) -> list[dict[str, Any]]:
    doc = source.get("possession_candidates")
    if not isinstance(doc, dict):
        return []
    summary = doc.get("summary") if isinstance(doc.get("summary"), dict) else {}
    interval = num(summary.get("frame_interval_sec"))
    if interval <= 0:
        stride = integer((doc.get("parameters") or {}).get("frame_stride"), 1)
        interval = stride / max(source["fps"], 0.001)
    out = []
    for row in doc.get("frames") or []:
        if not isinstance(row, dict):
            continue
        local_time = num(row.get("time_sec")) if row.get("time_sec") is not None else integer(row.get("frame")) / max(source["fps"], 0.001)
        label = str(row.get("team_label") or "").upper()
        out.append({
            "time_sec": source["offset_sec"] + local_time,
            "weight_sec": interval,
            "status": str(row.get("status") or "unknown"),
            "team_id": source["label_to_team_id"].get(label),
        })
    return out


def possession_summary(samples, team_ids):
    status = Counter(row["status"] for row in samples)
    team_samples = {team_id: 0 for team_id in team_ids}
    team_sec = {team_id: 0.0 for team_id in team_ids}
    total_sec = controlled_sec = free_sec = unknown_sec = contested_sec = 0.0
    for row in samples:
        weight = num(row.get("weight_sec"))
        total_sec += weight
        if row["status"] == "controlled":
            controlled_sec += weight
            team_id = row.get("team_id")
            if team_id in team_sec:
                team_samples[team_id] += 1
                team_sec[team_id] += weight
        elif row["status"] == "free":
            free_sec += weight
        elif row["status"] == "contested":
            contested_sec += weight
        else:
            unknown_sec += weight
    known = sum(team_sec.values())
    return {
        "samples": len(samples),
        "status_samples": dict(sorted(status.items())),
        "controlled_samples_by_team_id": team_samples,
        "controlled_seconds_by_team_id": {k: round(v, 3) for k, v in team_sec.items()},
        "possession_share_percent_by_team_id": {k: round(v / known * 100.0, 1) if known > 0 else None for k, v in team_sec.items()},
        "sampled_duration_sec": round(total_sec, 3),
        "controlled_duration_sec": round(controlled_sec, 3),
        "free_duration_sec": round(free_sec, 3),
        "unknown_duration_sec": round(unknown_sec, 3),
        "contested_duration_sec": round(contested_sec, 3),
        "controlled_coverage": round(controlled_sec / total_sec, 4) if total_sec > 0 else 0.0,
    }


def is_pass_attempt(item: dict[str, Any]) -> bool:
    outcome = str(item.get("outcome") or "")
    if outcome in {"completed_pass", "failed_pass"}:
        return True
    if outcome == "excluded_non_pass":
        return False
    return str(item.get("pass_type") or "") in {"same_team_pass", "turnover_or_interception"}


def pass_samples(source: dict[str, Any]) -> list[dict[str, Any]]:
    doc = source.get("pass_candidates")
    if not isinstance(doc, dict):
        return []
    out = []
    for item in doc.get("candidates") or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("count_for_team_label") or item.get("from_team_label") or "").upper()
        team_id = source["label_to_team_id"].get(label)
        if not team_id:
            continue
        local_time = num(item.get("start_time_sec") if item.get("start_time_sec") is not None else item.get("end_time_sec"))
        out.append({
            "time_sec": source["offset_sec"] + local_time,
            "team_id": team_id,
            "attempt": is_pass_attempt(item),
            "completed": item.get("completed") is True or item.get("outcome") == "completed_pass",
            "failed": item.get("failed") is True or item.get("outcome") == "failed_pass",
            "from_restart": item.get("from_restart") is True,
            "is_progressive": item.get("is_progressive") is True,
            "accepted": item.get("final_stat_eligible") is True or item.get("review_status") == "accepted",
        })
    return out


def pass_summary(rows, team_ids):
    out = {}
    for team_id in team_ids:
        team = [r for r in rows if r["team_id"] == team_id]
        attempts = [r for r in team if r["attempt"]]
        completed = [r for r in attempts if r["completed"]]
        failed = [r for r in attempts if r["failed"]]
        out[team_id] = {
            "pass_candidates": len(team),
            "pass_attempts": len(attempts),
            "completed_passes": len(completed),
            "failed_passes": len(failed),
            "completion_rate": round(len(completed) / len(attempts) * 100.0, 1) if attempts else 0.0,
            "restart_passes": sum(1 for r in attempts if r["from_restart"]),
            "progressive_pass_candidates": sum(1 for r in attempts if r["is_progressive"]),
            "accepted_passes": sum(1 for r in team if r["accepted"]),
        }
    return out


def rebase_momentum(sources):
    out = []
    for source in sources:
        doc = source.get("attacking_momentum")
        if not isinstance(doc, dict):
            continue
        for item in doc.get("points") or []:
            if not isinstance(item, dict):
                continue
            values = {}
            for label, key in (("A", "team_a_value"), ("B", "team_b_value")):
                if isinstance(item.get(key), (int, float)):
                    values[source["label_to_team_id"][label]] = float(item[key])
            dom = str(item.get("dominant_team_label") or "").upper()
            out.append({
                "source_match_id": source["id"],
                "start_time_sec": round(source["offset_sec"] + num(item.get("start_time_sec")), 3),
                "end_time_sec": round(source["offset_sec"] + num(item.get("end_time_sec")), 3),
                "time_sec": round(source["offset_sec"] + num(item.get("time_sec")), 3),
                "team_values_by_team_id": values,
                "dominant_team_id": source["label_to_team_id"].get(dom),
                "confidence": item.get("confidence"),
                "intensity": item.get("intensity"),
            })
    return sorted(out, key=lambda r: (r["start_time_sec"], r["end_time_sec"]))


def momentum_window(points, start, end, team_ids):
    weighted = {team_id: [] for team_id in team_ids}
    count = 0
    for point in points:
        overlap = max(0.0, min(end, num(point.get("end_time_sec"))) - max(start, num(point.get("start_time_sec"))))
        if overlap <= 0:
            continue
        count += 1
        for team_id, value in point.get("team_values_by_team_id", {}).items():
            if team_id in weighted:
                weighted[team_id].append((float(value), overlap))
    if count == 0:
        return None
    averages = {}
    for team_id, values in weighted.items():
        weight = sum(w for _, w in values)
        averages[team_id] = round(sum(v * w for v, w in values) / weight, 3) if weight > 0 else None
    available = {k: v for k, v in averages.items() if v is not None}
    return {
        "points": count,
        "average_team_value_by_team_id": averages,
        "dominant_team_id": max(available, key=available.get) if available else None,
    }


def build_team_shape_samples(sources, pitch_width, pitch_length):
    warnings = []
    shapes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    try:
        from app.services.match_phase_config import ATTACK_DIRECTIONS, direction_for_team_at_time
        from app.services.team_shape import MAX_TEAM_POSITIONS, MIN_TEAM_POSITIONS, calculate_frame_shape, observations_from_tracklets
    except Exception as exc:
        return {}, [f"Team Shape import failed: {exc}"]
    allowed = set(ATTACK_DIRECTIONS) - {"unknown"}
    for source in sources:
        tracklets = source.get("tracklets")
        phases = source.get("match_phase_config")
        if not isinstance(tracklets, dict) or not isinstance(phases, dict):
            warnings.append(f"{source['id']}: missing tracklets.json or match_phase_config.json; Team Shape skipped")
            continue
        grouped = defaultdict(list)
        for row in observations_from_tracklets(tracklets.get("tracklets") or []):
            label = str(row.get("team_label") or "").upper()
            point = row.get("pitch_m")
            if label not in {"A", "B"} or row.get("trusted") is False or str(row.get("source") or "detected") != "detected" or str(row.get("play_area_status") or "inside_play") != "inside_play":
                continue
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            try:
                x, y = float(point[0]), float(point[1])
            except (TypeError, ValueError):
                continue
            if not (math.isfinite(x) and math.isfinite(y) and 0 <= x <= pitch_width and 0 <= y <= pitch_length):
                continue
            grouped[(label, integer(row.get("frame")), num(row.get("time_sec")))].append(row)
        for (label, frame, local_time), rows in sorted(grouped.items(), key=lambda item: (item[0][2], item[0][1])):
            direction_info = direction_for_team_at_time(phases, label, local_time)
            direction = str(direction_info.get("attack_direction") or "unknown")
            if direction_info.get("period_id") is None or direction not in allowed or not (MIN_TEAM_POSITIONS <= len(rows) <= MAX_TEAM_POSITIONS):
                continue
            shape = calculate_frame_shape([r["pitch_m"] for r in rows], direction, pitch_width, pitch_length)
            if shape is None:
                continue
            team_id = source["label_to_team_id"][label]
            shapes[team_id].append({**shape, "source_match_id": source["id"], "frame": frame, "time_sec": source["offset_sec"] + local_time})
    for rows in shapes.values():
        rows.sort(key=lambda r: r["time_sec"])
    return dict(shapes), warnings


def shape_summary(rows):
    if not rows:
        return None
    return {
        "samples": len(rows),
        "average_width_m": round(sum(num(r.get("width_m")) for r in rows) / len(rows), 2),
        "average_depth_m": round(sum(num(r.get("depth_m")) for r in rows) / len(rows), 2),
        "average_compactness_m": round(sum(num(r.get("compactness_m")) for r in rows) / len(rows), 2),
        "average_block_height_percent": round(sum(num(r.get("block_height_percent")) for r in rows) / len(rows), 2),
    }


def shape_density(rows):
    columns, grid_rows = 6, 10
    values = defaultdict(float)
    if rows:
        for shape in rows:
            positions = [p for p in shape.get("oriented_positions") or [] if isinstance(p, dict)]
            if not positions:
                continue
            weight = 1.0 / len(rows) / len(positions)
            for point in positions:
                col = min(columns - 1, max(0, int(num(point.get("lateral_percent")) / 100.0 * columns)))
                row = min(grid_rows - 1, max(0, int(num(point.get("progress_percent")) / 100.0 * grid_rows)))
                values[(col, row)] += weight
    return {
        "grid": {"columns": columns, "rows": grid_rows},
        "cells": [{"column": c, "row": r, "value": round(v, 6)} for (c, r), v in sorted(values.items()) if v > 0],
        "samples": len(rows),
    }


def _draw_pitch(image, draw):
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


def _render_binned_heatmap_png(
    output_path,
    bins,
    *,
    grid_width,
    grid_length,
    width_px=360,
    length_px=720,
):
    """
    Render from the same coarse spatial histogram stored in combined_report.json.

    Do NOT normalize exact raw pixel coordinates before smoothing. Exact-pixel
    normalization lets a few repeated/quantized coordinates dominate the image
    and can make tens of thousands of valid samples appear as only a few red
    rectangles.
    """
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter, ImageOps

    heat = np.zeros((grid_length, grid_width), dtype=np.float32)
    for (x, y), value in bins.items():
        if 0 <= x < grid_width and 0 <= y < grid_length:
            heat[y, x] = float(value)

    if heat.max() <= 0:
        colored = Image.new("RGB", (width_px, length_px), "#1a4630")
        _draw_pitch(colored, ImageDraw.Draw(colored))
        colored.save(output_path)
        return

    # Compress the dynamic range before interpolation so one stationary/quantized
    # hotspot cannot erase the player's lower-density movement footprint.
    heat = np.power(heat, 0.55)

    nonzero = heat[heat > 0]
    scale = float(np.percentile(nonzero, 99.5)) if nonzero.size else float(heat.max())
    scale = max(scale, 1e-6)
    normalized = np.clip(heat / scale, 0.0, 1.0)

    coarse = Image.fromarray((normalized * 255).astype(np.uint8), mode="L")
    resampling = getattr(Image, "Resampling", Image).BICUBIC
    smooth = coarse.resize((width_px, length_px), resampling)
    smooth = smooth.filter(ImageFilter.GaussianBlur(radius=7))

    # Re-normalize after interpolation/blur.
    arr = np.asarray(smooth, dtype=np.float32)
    positive = arr[arr > 0]
    if positive.size:
        post_scale = max(float(np.percentile(positive, 99.5)), 1.0)
        arr = np.clip(arr / post_scale, 0.0, 1.0)
    heat_image = Image.fromarray((arr * 255).astype(np.uint8), mode="L")

    colored = ImageOps.colorize(
        heat_image,
        black="#163d2b",
        mid="#facc15",
        white="#ef4444",
    )
    _draw_pitch(colored, ImageDraw.Draw(colored))
    colored.save(output_path)


def aggregate_heatmaps(sources, roster, team_meta, pitch_width, pitch_length, output_dir, render_png):
    combined = {}
    warnings = []
    for source in sources:
        doc = source.get("reviewed_heatmaps")
        if not isinstance(doc, dict):
            warnings.append(f"{source['id']}: reviewed_player_heatmaps.json missing")
            continue
        for row in doc.get("heatmaps") or []:
            if not isinstance(row, dict):
                continue
            player_id = str(row.get("player_id") or "").strip()
            if not player_id:
                continue
            roster_row = roster.get(player_id)
            team_id = (roster_row or {}).get("team_id") or source["label_to_team_id"].get(str(row.get("team_label") or "").upper())
            target = combined.setdefault(player_id, {
                "player_id": player_id,
                "player_name": (roster_row or {}).get("player_name") or player_id,
                "team_id": team_id,
                "team_name": team_meta.get(team_id, {}).get("team_name") if team_id else None,
                "positions": [],
                "source_match_ids": [],
            })
            target["source_match_ids"].append(source["id"])
            target["positions"].extend([
                [float(p[0]), float(p[1])]
                for p in row.get("positions_m") or []
                if isinstance(p, (list, tuple))
                and len(p) >= 2
                and isinstance(p[0], (int, float))
                and isinstance(p[1], (int, float))
            ])

    heatmap_dir = output_dir / "heatmaps"
    out = []
    for player_id, row in combined.items():
        positions = row.pop("positions")
        bins = defaultdict(int)
        for x, y in positions:
            bx = min(
                HEATMAP_GRID_WIDTH - 1,
                max(0, int(x / max(pitch_width, 0.001) * HEATMAP_GRID_WIDTH)),
            )
            by = min(
                HEATMAP_GRID_LENGTH - 1,
                max(0, int(y / max(pitch_length, 0.001) * HEATMAP_GRID_LENGTH)),
            )
            bins[(bx, by)] += 1

        avg = (
            [
                round(sum(p[0] for p in positions) / len(positions), 3),
                round(sum(p[1] for p in positions) / len(positions), 3),
            ]
            if positions
            else None
        )

        png = None
        if render_png and positions:
            try:
                heatmap_dir.mkdir(parents=True, exist_ok=True)
                filename = f"player_{safe_name(player_id)}.png"
                _render_binned_heatmap_png(
                    heatmap_dir / filename,
                    bins,
                    grid_width=HEATMAP_GRID_WIDTH,
                    grid_length=HEATMAP_GRID_LENGTH,
                    width_px=360,
                    length_px=720,
                )
                png = str(Path("heatmaps") / filename)
            except Exception as exc:
                warnings.append(f"{player_id}: heatmap PNG render failed: {exc}")

        out.append({
            **row,
            "source_match_ids": list(dict.fromkeys(row["source_match_ids"])),
            "samples": len(positions),
            "average_position_m": avg,
            "grid": {
                "width": HEATMAP_GRID_WIDTH,
                "length": HEATMAP_GRID_LENGTH,
                "max_value": max(bins.values(), default=0),
                "cells": [
                    {"x": x, "y": y, "value": v}
                    for (x, y), v in sorted(
                        bins.items(), key=lambda item: (item[0][1], item[0][0])
                    )
                ],
            },
            "png_path": png,
        })
    return (
        sorted(out, key=lambda r: (str(r.get("team_name")), str(r.get("player_name")))),
        warnings,
    )


def merge_identity_coverage(sources):
    keys = ("confirmed_observations", "reliable_player_observations_total", "unresolved_observations", "conflicted_observations", "ignored_observations")
    sums = {key: 0 for key in keys}
    units = set()
    for source in sources:
        coverage = source["reviewed_stats"].get("identity_coverage")
        if not isinstance(coverage, dict):
            continue
        if coverage.get("coverage_unit"):
            units.add(str(coverage["coverage_unit"]))
        for key in keys:
            sums[key] += integer(coverage.get(key))
    reliable = sums["reliable_player_observations_total"]
    return {
        "coverage_unit": next(iter(units)) if len(units) == 1 else sorted(units),
        **sums,
        "confirmed_ratio": round(sums["confirmed_observations"] / reliable, 4) if reliable > 0 else None,
    }


def build_windows(total_duration, window_sec, possession, passes, shape_samples, momentum, team_ids):
    out = []
    count = max(1, math.ceil(total_duration / window_sec))
    for idx in range(count):
        start = idx * window_sec
        end = min(total_duration, (idx + 1) * window_sec)
        pos = [r for r in possession if start <= r["time_sec"] < end]
        pas = [r for r in passes if start <= r["time_sec"] < end]
        out.append({
            "index": idx,
            "label": f"{clock(start)}–{clock(end)}",
            "start_time_sec": round(start, 3),
            "end_time_sec": round(end, 3),
            "duration_sec": round(end - start, 3),
            "possession": possession_summary(pos, team_ids) if pos else None,
            "passes": pass_summary(pas, team_ids) if pas else None,
            "team_shape": {team_id: shape_summary([r for r in shape_samples.get(team_id, []) if start <= r["time_sec"] < end]) for team_id in team_ids},
            "attacking_momentum": momentum_window(momentum, start, end, team_ids),
        })
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Combine reviewed match fragments into one offline report")
    parser.add_argument("match_ids", nargs="+", help="Ordered source match IDs")
    parser.add_argument("--matches-root", default="storage/matches")
    parser.add_argument("--output", default="combined-reports/combined-match")
    parser.add_argument("--title", default="Combined analysed match")
    parser.add_argument("--window-minutes", type=float, default=5.0)
    parser.add_argument("--no-render-heatmaps", action="store_true")
    args = parser.parse_args()
    if args.window_minutes <= 0:
        parser.error("--window-minutes must be > 0")

    matches_root = Path(args.matches_root)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    sources = [load_source(matches_root, match_id) for match_id in args.match_ids]
    team_ids, team_meta, roster = validate_sources(sources)
    pitch_width, pitch_length = pitch_dimensions(sources)

    offset = 0.0
    for source in sources:
        source["offset_sec"] = offset
        source["logical_end_sec"] = offset + source["duration_sec"]
        offset += source["duration_sec"]
    total_duration = offset

    first_map = sources[0]["label_to_team_id"]
    canonical_label = {first_map["A"]: "A", first_map["B"]: "B"}

    possession = sorted([row for source in sources for row in possession_samples(source)], key=lambda r: r["time_sec"])
    passes = sorted([row for source in sources for row in pass_samples(source)], key=lambda r: r["time_sec"])
    momentum = rebase_momentum(sources)
    shape_samples, shape_warnings = build_team_shape_samples(sources, pitch_width, pitch_length)
    heatmaps, heatmap_warnings = aggregate_heatmaps(sources, roster, team_meta, pitch_width, pitch_length, output_dir, not args.no_render_heatmaps)
    players = aggregate_players(sources, roster, team_meta)
    team_movement = aggregate_team_movement(sources, team_ids, team_meta)
    overall_possession = possession_summary(possession, team_ids) if possession else None
    overall_passes = pass_summary(passes, team_ids) if passes else None

    teams = []
    for team_id in team_ids:
        row = dict(team_movement[team_id])
        row["team_label"] = canonical_label.get(team_id)
        if overall_possession:
            row["possession_share_percent"] = overall_possession["possession_share_percent_by_team_id"].get(team_id)
        if overall_passes:
            row["passes"] = overall_passes.get(team_id)
        teams.append(row)

    team_shape = {
        "schema_version": "combined-team-shape-v1",
        "source": "trusted_detected_tracklet_positions_rebased",
        "pitch_dimensions_m": {"width_m": pitch_width, "length_m": pitch_length},
        "teams": [{
            "team_id": team_id,
            "team_label": canonical_label.get(team_id),
            "team_name": team_meta[team_id]["team_name"],
            "summary": shape_summary(shape_samples.get(team_id, [])),
            "average_shape": shape_density(shape_samples.get(team_id, [])),
        } for team_id in team_ids],
        "warnings": shape_warnings,
    }

    windows = build_windows(total_duration, args.window_minutes * 60.0, possession, passes, shape_samples, momentum, team_ids)
    sources_out = [{
        "match_id": source["id"],
        "title": source["match"].get("title"),
        "offset_sec": round(source["offset_sec"], 3),
        "offset_clock": clock(source["offset_sec"]),
        "duration_sec": round(source["duration_sec"], 3),
        "duration_clock": clock(source["duration_sec"]),
        "logical_end_sec": round(source["logical_end_sec"], 3),
        "logical_end_clock": clock(source["logical_end_sec"]),
        "fps": source["fps"],
        "local_team_to_stable_team": source["label_to_team_id"],
    } for source in sources]

    warnings = [
        "Logical clock is contiguous analysed-video time; no real-world gaps are inferred.",
        "Heatmaps are merged in raw pitch coordinates; this run assumes source pitch orientation is compatible.",
        "Team Shape is recomputed from existing trusted tracklets + match_phase_config; no detection/tracking is rerun.",
        "5-minute possession windows are rebuilt from possession_candidates frame samples.",
        "Pass windows use existing pass candidate timestamps.",
        "This is an offline report; it is not registered as a published match.",
        *shape_warnings,
        *heatmap_warnings,
    ]

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "report_type": "offline_combined_match_report",
        "title": args.title,
        "source_match_ids": args.match_ids,
        "timing": {
            "mode": "contiguous_analyzed_video",
            "analyzed_duration_sec": round(total_duration, 3),
            "analyzed_duration_clock": clock(total_duration),
            "window_minutes": args.window_minutes,
        },
        "pitch_dimensions_m": {"width_m": pitch_width, "length_m": pitch_length},
        "sources": sources_out,
        "teams": teams,
        "players": players,
        "identity_coverage": merge_identity_coverage(sources),
        "ball": {
            "possession": overall_possession,
            "passes": overall_passes,
            "attacking_momentum_points": momentum,
        },
        "team_shape": team_shape,
        "player_heatmaps": heatmaps,
        "windows": windows,
        "warnings": warnings,
    }

    write_json(output_dir / "combined_report.json", report)
    write_json(output_dir / "combined_team_shape.json", team_shape)
    write_json(output_dir / "combined_player_heatmaps.json", {
        "schema_version": "combined-player-heatmaps-v1",
        "pitch_dimensions_m": {"width_m": pitch_width, "length_m": pitch_length},
        "heatmaps": heatmaps,
        "warnings": heatmap_warnings,
    })
    write_json(output_dir / "sources.json", {"schema_version": "combined-match-sources-v1", "generated_at": now_iso(), "sources": sources_out})

    print("\n" + "=" * 92)
    print(args.title)
    print("=" * 92)
    for source in sources_out:
        print(f"{source['match_id']}  offset={source['offset_clock']:>6}  duration={source['duration_clock']:>6}  end={source['logical_end_clock']:>6}  {source.get('title') or ''}")
    print("-" * 92)
    print(f"Combined duration: {total_duration / 60.0:.2f} min ({clock(total_duration)})")
    print(f"Windows ({args.window_minutes:g} min): {len(windows)}")
    print(f"Players: {len(players)}")
    print(f"Merged player heatmaps: {len(heatmaps)}")
    print("Team Shape samples: " + ", ".join(f"{team_meta[t]['team_name']}={len(shape_samples.get(t, []))}" for t in team_ids))
    print("\nOutput:")
    for name in ("combined_report.json", "combined_team_shape.json", "combined_player_heatmaps.json", "sources.json"):
        print(f"  {output_dir / name}")
    if (output_dir / "heatmaps").exists():
        print(f"  {output_dir / 'heatmaps'}")
    if warnings:
        print("\nWarnings / assumptions:")
        for warning in warnings:
            print(f"  - {warning}")
    print()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
