from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.resolved_player_stats import build_resolved_player_stats_from_files
from app.services.team_registry import list_teams


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _load_json_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must contain an object")
    return data


def _nested_number(row: dict[str, Any], group: str, key: str) -> float:
    value = row.get(group)
    if not isinstance(value, dict):
        return 0.0
    return _number(value.get(key))


def _quality_rank(value: str | None) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(str(value or "unknown"), 3)


def _worst_quality(values: list[str]) -> str:
    if not values:
        return "unknown"
    return max(values, key=_quality_rank)


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


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
        "start_frame": int(_number(best.get("start_frame"))),
        "end_frame": int(_number(best.get("end_frame"))),
        "start_time_sec": round(_number(best.get("start_time_sec")), 3),
        "end_time_sec": round(_number(best.get("end_time_sec")), 3),
        "duration_sec": round(_number(best.get("duration_sec")), 3),
        "distance_m": round(_number(best.get("distance_m")), 2),
        "max_speed_kmh": round(_number(best.get("max_speed_kmh")), 2),
        "reason": str(best.get("reason") or "none"),
    }


def _registry_player(player_id: str, registry_teams: list[dict[str, Any]]) -> dict[str, Any] | None:
    for team in registry_teams:
        if not isinstance(team, dict):
            continue
        for player in _list(team.get("players")):
            if isinstance(player, dict) and str(player.get("id") or "") == player_id:
                return {
                    "player_id": player_id,
                    "player_name": player.get("name"),
                    "player_number": player.get("number"),
                    "player_role": player.get("role"),
                    "is_guest": bool(player.get("is_guest")),
                    "team_id": team.get("id"),
                    "team_name": team.get("name"),
                }
    return None


def _read_or_build_resolved_stats(match_path: Path) -> dict[str, Any] | None:
    resolved_path = match_path / "resolved_player_stats.json"
    if resolved_path.exists():
        return _load_json_object(resolved_path)
    if (match_path / "player_identity_assignments.json").exists() and (match_path / "player_stats.json").exists():
        try:
            return build_resolved_player_stats_from_files(match_path, persist=True)
        except (FileNotFoundError, ValueError):
            return None
    return None


def _match_identity(match_path: Path, meta: dict[str, Any]) -> str:
    return str(meta.get("id") or match_path.name)


def _appearance(match_path: Path, meta: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    distance = _record(row.get("distance"))
    speed = _record(row.get("speed"))
    intensity = _record(row.get("intensity"))
    return {
        "match_id": _match_identity(match_path, meta),
        "match_title": meta.get("title") or _match_identity(match_path, meta),
        "match_date": meta.get("match_date"),
        "season": meta.get("season"),
        "venue": meta.get("venue"),
        "format": meta.get("format"),
        "match_status": meta.get("status"),
        "team_label": row.get("team_label"),
        "team_id": row.get("team_id"),
        "team_name": row.get("team_name"),
        "player_name": row.get("player_name"),
        "player_number": row.get("player_number"),
        "player_role": row.get("player_role"),
        "stable_player_ids": _list(row.get("stable_player_ids")),
        "stable_subject_ids": _list(row.get("stable_subject_ids")),
        "source_stable_slots": _list(row.get("source_stable_slots")),
        "time": _record(row.get("time")),
        "distance": distance,
        "speed": speed,
        "intensity": intensity,
        "frames": _record(row.get("frames")),
        "segments": _record(row.get("segments")),
        "review_warnings": _list(row.get("review_warnings")),
        "distance_quality": distance.get("quality") or "unknown",
        "speed_quality": speed.get("quality") or "unknown",
        "tracking_only": True,
    }


def _sort_key(appearance: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(appearance.get("match_date") or ""),
        str(appearance.get("match_title") or ""),
        str(appearance.get("match_id") or ""),
    )


def _player_from_sources(player_id: str, appearances: list[dict[str, Any]], registry_player: dict[str, Any] | None) -> dict[str, Any]:
    first = appearances[0] if appearances else {}
    return {
        "player_id": player_id,
        "player_name": (registry_player or {}).get("player_name") or first.get("player_name"),
        "player_number": (registry_player or {}).get("player_number") or first.get("player_number"),
        "player_role": (registry_player or {}).get("player_role") or first.get("player_role"),
        "is_guest": bool((registry_player or {}).get("is_guest", False)),
        "team_id": (registry_player or {}).get("team_id") or first.get("team_id"),
        "team_name": (registry_player or {}).get("team_name") or first.get("team_name"),
        "known_from_registry": registry_player is not None,
    }


def _teams_from_appearances(registry_player: dict[str, Any] | None, appearances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    teams: dict[str, dict[str, Any]] = {}
    if registry_player and registry_player.get("team_id"):
        teams[str(registry_player["team_id"])] = {
            "team_id": registry_player.get("team_id"),
            "team_name": registry_player.get("team_name"),
            "source": "registry",
        }
    for item in appearances:
        team_id = str(item.get("team_id") or item.get("team_label") or "unknown-team")
        teams.setdefault(
            team_id,
            {
                "team_id": item.get("team_id"),
                "team_name": item.get("team_name"),
                "team_label": item.get("team_label"),
                "source": "match_assignment",
            },
        )
    return sorted(teams.values(), key=lambda item: str(item.get("team_name") or item.get("team_id") or ""))


def _summary(appearances: list[dict[str, Any]], scanned_matches: int) -> dict[str, Any]:
    playing_time = sum(_nested_number(row, "time", "playing_time_sec") for row in appearances)
    total_distance = sum(_nested_number(row, "distance", "total_distance_m") for row in appearances)
    estimated_distance = sum(_nested_number(row, "distance", "estimated_short_gap_distance_m") for row in appearances)
    observed_distance = sum(_nested_number(row, "distance", "observed_distance_m") for row in appearances)
    detected_time = sum(_nested_number(row, "time", "detected_time_sec") for row in appearances)
    peak_speed = max([_nested_number(row, "speed", "peak_sustained_speed_kmh") for row in appearances] or [0.0])
    top_speed = max([_nested_number(row, "speed", "top_speed_kmh") for row in appearances] or [0.0])
    max_sprint_speed = max([_nested_number(row, "intensity", "max_sprint_speed_kmh") for row in appearances] or [0.0])
    best_rejected = _best_sprint_candidate_from_rows(
        [
            _record(_record(row.get("intensity")).get("best_rejected_sprint_candidate"))
            for row in appearances
        ]
    )
    return {
        "matches": len(appearances),
        "appearances": len(appearances),
        "scanned_matches": scanned_matches,
        "playing_time_sec": round(playing_time, 2),
        "detected_time_sec": round(detected_time, 2),
        "missing_time_sec": round(sum(_nested_number(row, "time", "missing_time_sec") for row in appearances), 2),
        "ambiguous_time_sec": round(sum(_nested_number(row, "time", "ambiguous_time_sec") for row in appearances), 2),
        "total_distance_m": round(total_distance, 2),
        "observed_distance_m": round(observed_distance, 2),
        "estimated_short_gap_distance_m": round(estimated_distance, 2),
        "estimated_distance_ratio": round(estimated_distance / total_distance, 4) if total_distance > 0 else 0.0,
        "avg_speed_mps": round(total_distance / playing_time, 3) if playing_time > 0 else 0.0,
        "avg_speed_kmh": round((total_distance / playing_time) * 3.6, 2) if playing_time > 0 else 0.0,
        "peak_sustained_speed_kmh": round(peak_speed, 2),
        "top_speed_kmh": round(top_speed, 2),
        "high_intensity_time_sec": round(sum(_nested_number(row, "intensity", "high_intensity_time_sec") for row in appearances), 2),
        "high_intensity_distance_m": round(sum(_nested_number(row, "intensity", "high_intensity_distance_m") for row in appearances), 2),
        "sprint_count": sum(int(_nested_number(row, "intensity", "sprint_count")) for row in appearances),
        "sprint_time_sec": round(sum(_nested_number(row, "intensity", "sprint_time_sec") for row in appearances), 2),
        "sprint_distance_m": round(sum(_nested_number(row, "intensity", "sprint_distance_m") for row in appearances), 2),
        "longest_sprint_distance_m": round(max([_nested_number(row, "intensity", "longest_sprint_distance_m") for row in appearances] or [0.0]), 2),
        "max_sprint_speed_kmh": round(max_sprint_speed, 2),
        "sprint_candidate_count": sum(int(_nested_number(row, "intensity", "sprint_candidate_count")) for row in appearances),
        "rejected_sprint_candidate_count": sum(int(_nested_number(row, "intensity", "rejected_sprint_candidate_count")) for row in appearances),
        "best_sprint_candidate_speed_kmh": round(max([_nested_number(row, "intensity", "best_sprint_candidate_speed_kmh") for row in appearances] or [0.0]), 2),
        "best_sprint_candidate_duration_sec": round(max([_nested_number(row, "intensity", "best_sprint_candidate_duration_sec") for row in appearances] or [0.0]), 3),
        "best_rejected_sprint_candidate": best_rejected,
        "matches_with_warnings": sum(1 for row in appearances if row.get("review_warnings")),
        "stable_slots": sum(len(_list(row.get("stable_player_ids"))) for row in appearances),
        "distance_quality": _worst_quality([str(row.get("distance_quality") or "unknown") for row in appearances]),
        "speed_quality": _worst_quality([str(row.get("speed_quality") or "unknown") for row in appearances]),
        "anonymous_slots_aggregated": 0,
    }


def build_player_profile_stats(
    matches_dir: Path,
    player_id: str,
    *,
    registry_teams: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not player_id:
        raise ValueError("player_id is required")

    registry = list_teams() if registry_teams is None else registry_teams
    registry_player = _registry_player(player_id, registry)
    appearances: list[dict[str, Any]] = []
    scanned_matches = 0

    if matches_dir.exists():
        for match_path in sorted((path for path in matches_dir.iterdir() if path.is_dir()), key=lambda path: path.name):
            meta_path = match_path / "match.json"
            if not meta_path.exists():
                continue
            scanned_matches += 1
            meta = _load_json_object(meta_path)
            resolved = _read_or_build_resolved_stats(match_path)
            if not resolved:
                continue
            for row in _list(resolved.get("players")):
                if not isinstance(row, dict) or str(row.get("player_id") or "") != player_id:
                    continue
                appearances.append(_appearance(match_path, meta, row))

    appearances.sort(key=_sort_key, reverse=True)
    if not appearances and registry_player is None:
        raise KeyError(player_id)

    return {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "scope": "player_profile_tracking_only_no_ball",
        "identity_semantics": "real_player_id_from_roster_assignments",
        "player": _player_from_sources(player_id, appearances, registry_player),
        "teams": _teams_from_appearances(registry_player, appearances),
        "summary": _summary(appearances, scanned_matches),
        "appearances": appearances,
        "notes": [
            "Only explicitly assigned player_id appearances are aggregated.",
            "Anonymous stable slots such as A03/B05 are match-only context and are not aggregated here.",
            "Tracking-only profile; ball events are not included.",
        ],
    }
