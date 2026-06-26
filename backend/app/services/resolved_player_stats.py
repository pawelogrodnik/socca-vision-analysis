from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _int(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


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


def _quality_rank(quality: str | None) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(str(quality or "unknown"), 3)


def _worst_quality(values: list[str]) -> str:
    if not values:
        return "unknown"
    return max(values, key=_quality_rank)


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

    for group in ["time", "distance", "speed"]:
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
                "total_distance_m": 0.0,
                "observed_distance_m": 0.0,
                "estimated_short_gap_distance_m": 0.0,
                "peak_sustained_speed_kmh": 0.0,
                "top_speed_kmh": 0.0,
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
        row["total_distance_m"] += _number(player["distance"].get("total_distance_m"))
        row["observed_distance_m"] += _number(player["distance"].get("observed_distance_m"))
        row["estimated_short_gap_distance_m"] += _number(player["distance"].get("estimated_short_gap_distance_m"))
        row["peak_sustained_speed_kmh"] = max(_number(row["peak_sustained_speed_kmh"]), _number(player["speed"].get("peak_sustained_speed_kmh")))
        row["top_speed_kmh"] = max(_number(row["top_speed_kmh"]), _number(player["speed"].get("top_speed_kmh")))
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
) -> dict[str, Any]:
    slot_stats_by_subject = _player_stats_by_subject(player_stats)
    assignments = identity_assignments.get("assignments") if isinstance(identity_assignments.get("assignments"), list) else []
    rows_by_player: dict[str, dict[str, Any]] = {}
    distance_qualities: dict[str, list[str]] = {}
    speed_qualities: dict[str, list[str]] = {}
    skipped_assignments = []

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
        player_id = str(assignment["player_id"])
        row = rows_by_player.setdefault(player_id, _empty_player_row(assignment))
        _merge_slot_stats(row, assignment, slot_stats)
        distance = slot_stats.get("distance") if isinstance(slot_stats.get("distance"), dict) else {}
        speed = slot_stats.get("speed") if isinstance(slot_stats.get("speed"), dict) else {}
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
    doc = build_resolved_player_stats_document(
        player_stats=player_stats,
        identity_assignments=identity_assignments,
    )
    if persist:
        (path / "resolved_player_stats.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return doc
