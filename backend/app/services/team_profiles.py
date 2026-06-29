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


def _int(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _load_json_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must contain an object")
    return data


def _nested_number(row: dict[str, Any], group: str, key: str) -> float:
    return _number(_record(row.get(group)).get(key))


def _quality_rank(value: str | None) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(str(value or "unknown"), 3)


def _worst_quality(values: list[str]) -> str:
    if not values:
        return "unknown"
    return max(values, key=_quality_rank)


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


def _registry_team(team_id: str, registry_teams: list[dict[str, Any]]) -> dict[str, Any] | None:
    for team in registry_teams:
        if isinstance(team, dict) and str(team.get("id") or "") == team_id:
            return team
    return None


def _match_has_team(meta: dict[str, Any], team_id: str) -> bool:
    return any(
        isinstance(team, dict) and str(team.get("id") or "") == team_id
        for team in _list(meta.get("teams"))
    )


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


def _empty_player_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "player_id": row.get("player_id"),
        "player_name": row.get("player_name"),
        "player_number": row.get("player_number"),
        "player_role": row.get("player_role"),
        "team_id": row.get("team_id"),
        "team_name": row.get("team_name"),
        "team_label": row.get("team_label"),
        "matches": 0,
        "appearances": [],
        "source_match_ids": [],
        "stable_player_ids": [],
        "stable_subject_ids": [],
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
            "avg_speed_kmh": 0.0,
            "peak_sustained_speed_kmh": 0.0,
            "top_speed_kmh": 0.0,
            "quality": "unknown",
        },
        "intensity": {
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
        },
        "review_warnings": [],
        "distance_quality": "unknown",
        "speed_quality": "unknown",
        "tracking_only": True,
    }


def _appearance(match_path: Path, meta: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    return {
        "match_id": _match_identity(match_path, meta),
        "match_title": meta.get("title") or _match_identity(match_path, meta),
        "match_date": meta.get("match_date"),
        "season": meta.get("season"),
        "venue": meta.get("venue"),
        "status": meta.get("status"),
        "stable_player_ids": _list(row.get("stable_player_ids")),
        "stable_subject_ids": _list(row.get("stable_subject_ids")),
        "time": _record(row.get("time")),
        "distance": _record(row.get("distance")),
        "speed": _record(row.get("speed")),
        "intensity": _record(row.get("intensity")),
        "review_warnings": _list(row.get("review_warnings")),
    }


def _merge_player_row(target: dict[str, Any], match_path: Path, meta: dict[str, Any], row: dict[str, Any]) -> None:
    match_id = _match_identity(match_path, meta)
    target["matches"] += 1
    target["appearances"].append(_appearance(match_path, meta, row))
    target["source_match_ids"].append(match_id)
    target["stable_player_ids"].extend(_list(row.get("stable_player_ids")))
    target["stable_subject_ids"].extend(_list(row.get("stable_subject_ids")))

    for key in ["playing_time_sec", "detected_time_sec", "missing_time_sec", "ambiguous_time_sec"]:
        target["time"][key] += _nested_number(row, "time", key)
    for key in ["observed_distance_m", "estimated_short_gap_distance_m", "total_distance_m"]:
        target["distance"][key] += _nested_number(row, "distance", key)
    for key in ["peak_sustained_speed_kmh", "top_speed_kmh"]:
        target["speed"][key] = max(_number(target["speed"].get(key)), _nested_number(row, "speed", key))

    intensity = _record(row.get("intensity"))
    for key in ["high_intensity_time_sec", "high_intensity_distance_m", "sprint_time_sec", "sprint_distance_m"]:
        target["intensity"][key] += _number(intensity.get(key))
    for key in ["sprint_count", "sprint_candidate_count", "rejected_sprint_candidate_count"]:
        target["intensity"][key] += _int(intensity.get(key))
    for key in [
        "longest_sprint_distance_m",
        "max_sprint_speed_kmh",
        "best_sprint_candidate_speed_kmh",
        "best_sprint_candidate_duration_sec",
    ]:
        target["intensity"][key] = max(_number(target["intensity"].get(key)), _number(intensity.get(key)))
    target["intensity"]["best_rejected_sprint_candidate"] = _best_sprint_candidate_from_rows(
        [
            _record(target["intensity"].get("best_rejected_sprint_candidate")),
            _record(intensity.get("best_rejected_sprint_candidate")),
        ]
    )
    target["review_warnings"].extend(str(item) for item in _list(row.get("review_warnings")))


def _finalize_player_row(row: dict[str, Any]) -> dict[str, Any]:
    playing_time = _number(row["time"].get("playing_time_sec"))
    total_distance = _number(row["distance"].get("total_distance_m"))
    estimated_distance = _number(row["distance"].get("estimated_short_gap_distance_m"))
    row["distance"]["estimated_distance_ratio"] = round(estimated_distance / total_distance, 4) if total_distance > 0 else 0.0
    row["speed"]["avg_speed_kmh"] = round((total_distance / playing_time) * 3.6, 2) if playing_time > 0 else 0.0
    row["distance_quality"] = _worst_quality(
        [
            str(_record(appearance.get("distance")).get("quality") or "unknown")
            for appearance in row["appearances"]
        ]
    )
    row["speed_quality"] = _worst_quality(
        [
            str(_record(appearance.get("speed")).get("quality") or "unknown")
            for appearance in row["appearances"]
        ]
    )
    row["distance"]["quality"] = row["distance_quality"]
    row["speed"]["quality"] = row["speed_quality"]
    row["source_match_ids"] = sorted({str(item) for item in row["source_match_ids"]})
    row["stable_player_ids"] = sorted({str(item) for item in row["stable_player_ids"]})
    row["stable_subject_ids"] = sorted({str(item) for item in row["stable_subject_ids"]})
    row["review_warnings"] = sorted({str(item) for item in row["review_warnings"]})
    row["appearances"] = sorted(
        row["appearances"],
        key=lambda item: (
            str(item.get("match_date") or ""),
            str(item.get("match_title") or ""),
            str(item.get("match_id") or ""),
        ),
        reverse=True,
    )
    for group in ["time", "distance", "speed", "intensity"]:
        for key, value in list(row[group].items()):
            if isinstance(value, float):
                row[group][key] = round(value, 2 if key.endswith("_kmh") or key.endswith("_m") or key.endswith("_sec") else 4)
    return row


def _match_summary(match_path: Path, meta: dict[str, Any], rows: list[dict[str, Any]], *, reason: str | None = None) -> dict[str, Any]:
    return {
        "match_id": _match_identity(match_path, meta),
        "match_title": meta.get("title") or _match_identity(match_path, meta),
        "match_date": meta.get("match_date"),
        "season": meta.get("season"),
        "venue": meta.get("venue"),
        "status": meta.get("status"),
        "players": len(rows),
        "has_resolved_stats": reason is None,
        "missing_reason": reason,
        "playing_time_sec": round(sum(_nested_number(row, "time", "playing_time_sec") for row in rows), 2),
        "total_distance_m": round(sum(_nested_number(row, "distance", "total_distance_m") for row in rows), 2),
        "peak_sustained_speed_kmh": round(max([_nested_number(row, "speed", "peak_sustained_speed_kmh") for row in rows] or [0.0]), 2),
        "high_intensity_distance_m": round(sum(_nested_number(row, "intensity", "high_intensity_distance_m") for row in rows), 2),
        "sprint_count": sum(_int(_record(row.get("intensity")).get("sprint_count")) for row in rows),
        "sprint_candidate_count": sum(_int(_record(row.get("intensity")).get("sprint_candidate_count")) for row in rows),
        "review_warnings": sum(len(_list(row.get("review_warnings"))) for row in rows),
    }


def build_team_profile_stats(
    matches_dir: Path,
    team_id: str,
    *,
    season: str | None = None,
    registry_teams: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not team_id:
        raise ValueError("team_id is required")

    registry = list_teams() if registry_teams is None else registry_teams
    registry_team = _registry_team(team_id, registry)
    player_rows: dict[str, dict[str, Any]] = {}
    match_rows: list[dict[str, Any]] = []
    missing_matches: list[dict[str, Any]] = []
    available_seasons: set[str] = set()
    scanned_matches = 0
    matches_with_team = 0

    if matches_dir.exists():
        for match_path in sorted((path for path in matches_dir.iterdir() if path.is_dir()), key=lambda path: path.name):
            meta_path = match_path / "match.json"
            if not meta_path.exists():
                continue
            meta = _load_json_object(meta_path)
            if meta.get("season"):
                available_seasons.add(str(meta["season"]))
            if season and str(meta.get("season") or "") != season:
                continue
            scanned_matches += 1
            resolved = _read_or_build_resolved_stats(match_path)
            resolved_rows = _list(resolved.get("players")) if resolved else []
            team_rows = [
                row
                for row in resolved_rows
                if isinstance(row, dict) and str(row.get("team_id") or "") == team_id and row.get("player_id")
            ]
            has_team_in_meta = _match_has_team(meta, team_id)
            if has_team_in_meta or team_rows:
                matches_with_team += 1
            if not resolved:
                if has_team_in_meta:
                    missing = _match_summary(match_path, meta, [], reason="missing_resolved_player_stats")
                    missing_matches.append(missing)
                    match_rows.append(missing)
                continue
            if not team_rows:
                if has_team_in_meta:
                    match_rows.append(_match_summary(match_path, meta, [], reason="no_assigned_players_for_team"))
                continue
            match_rows.append(_match_summary(match_path, meta, team_rows))
            for row in team_rows:
                player_id = str(row.get("player_id") or "")
                target = player_rows.setdefault(player_id, _empty_player_row(row))
                _merge_player_row(target, match_path, meta, row)

    players = [_finalize_player_row(row) for row in player_rows.values()]
    players.sort(key=lambda item: str(item.get("player_name") or item.get("player_id") or ""))
    used_matches = [row for row in match_rows if row.get("players")]
    total_distance = sum(_nested_number(row, "distance", "total_distance_m") for row in players)
    playing_time = sum(_nested_number(row, "time", "playing_time_sec") for row in players)
    estimated_distance = sum(_nested_number(row, "distance", "estimated_short_gap_distance_m") for row in players)
    summary = {
        "team_id": team_id,
        "team_name": (registry_team or {}).get("name") or (players[0].get("team_name") if players else None),
        "season": season,
        "scanned_matches": scanned_matches,
        "matches_with_team": matches_with_team,
        "matches_with_stats": len(used_matches),
        "matches_missing_resolved_stats": len(missing_matches),
        "matches_without_assigned_players": sum(1 for row in match_rows if row.get("missing_reason") == "no_assigned_players_for_team"),
        "players": len(players),
        "roster_players": len(_list((registry_team or {}).get("players"))),
        "playing_time_sec": round(playing_time, 2),
        "detected_time_sec": round(sum(_nested_number(row, "time", "detected_time_sec") for row in players), 2),
        "missing_time_sec": round(sum(_nested_number(row, "time", "missing_time_sec") for row in players), 2),
        "ambiguous_time_sec": round(sum(_nested_number(row, "time", "ambiguous_time_sec") for row in players), 2),
        "total_distance_m": round(total_distance, 2),
        "observed_distance_m": round(sum(_nested_number(row, "distance", "observed_distance_m") for row in players), 2),
        "estimated_short_gap_distance_m": round(estimated_distance, 2),
        "estimated_distance_ratio": round(estimated_distance / total_distance, 4) if total_distance > 0 else 0.0,
        "avg_speed_kmh": round((total_distance / playing_time) * 3.6, 2) if playing_time > 0 else 0.0,
        "peak_sustained_speed_kmh": round(max([_nested_number(row, "speed", "peak_sustained_speed_kmh") for row in players] or [0.0]), 2),
        "top_speed_kmh": round(max([_nested_number(row, "speed", "top_speed_kmh") for row in players] or [0.0]), 2),
        "high_intensity_time_sec": round(sum(_nested_number(row, "intensity", "high_intensity_time_sec") for row in players), 2),
        "high_intensity_distance_m": round(sum(_nested_number(row, "intensity", "high_intensity_distance_m") for row in players), 2),
        "sprint_count": sum(_int(_record(row.get("intensity")).get("sprint_count")) for row in players),
        "sprint_distance_m": round(sum(_nested_number(row, "intensity", "sprint_distance_m") for row in players), 2),
        "max_sprint_speed_kmh": round(max([_nested_number(row, "intensity", "max_sprint_speed_kmh") for row in players] or [0.0]), 2),
        "sprint_candidate_count": sum(_int(_record(row.get("intensity")).get("sprint_candidate_count")) for row in players),
        "rejected_sprint_candidate_count": sum(_int(_record(row.get("intensity")).get("rejected_sprint_candidate_count")) for row in players),
        "best_sprint_candidate_speed_kmh": round(max([_nested_number(row, "intensity", "best_sprint_candidate_speed_kmh") for row in players] or [0.0]), 2),
        "players_with_warnings": sum(1 for row in players if row.get("review_warnings")),
        "distance_quality": _worst_quality([str(row.get("distance_quality") or "unknown") for row in players]),
        "speed_quality": _worst_quality([str(row.get("speed_quality") or "unknown") for row in players]),
        "anonymous_slots_aggregated": 0,
    }

    if registry_team is None and not players and not match_rows:
        raise KeyError(team_id)

    return {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "scope": "team_tracking_only_no_ball",
        "identity_semantics": "real_player_id_from_roster_assignments",
        "team": {
            "team_id": team_id,
            "team_name": summary.get("team_name") or team_id,
            "color": (registry_team or {}).get("color"),
            "known_from_registry": registry_team is not None,
            "roster_players": _list((registry_team or {}).get("players")),
        },
        "season": season,
        "available_seasons": sorted(available_seasons, reverse=True),
        "summary": summary,
        "players": players,
        "matches": sorted(
            match_rows,
            key=lambda item: (
                str(item.get("match_date") or ""),
                str(item.get("match_title") or ""),
                str(item.get("match_id") or ""),
            ),
            reverse=True,
        ),
        "missing_matches": missing_matches,
        "notes": [
            "Only explicitly assigned player_id rows are aggregated.",
            "Anonymous stable slots such as A03/B05 remain match-only context and are not aggregated here.",
            "Tracking-only team profile; ball events are not included.",
        ],
    }
