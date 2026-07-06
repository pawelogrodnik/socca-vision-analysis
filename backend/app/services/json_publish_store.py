from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import PUBLISHED_DIR


PUBLISHED_MATCHES_DIR = PUBLISHED_DIR / "matches"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_publish_store() -> None:
    PUBLISHED_MATCHES_DIR.mkdir(parents=True, exist_ok=True)


def _published_id_from_package(package: dict[str, Any]) -> str:
    match = package.get("match") or {}
    source_match_id = str(match.get("id") or "unknown")
    return f"published-{source_match_id}"


def _published_match_dir(match_id: str) -> Path:
    return PUBLISHED_MATCHES_DIR / match_id


def _load_json_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return data


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)


def _match_teams(package: dict[str, Any]) -> list[dict[str, Any]]:
    match = package.get("match") if isinstance(package.get("match"), dict) else {}
    teams = match.get("teams") if isinstance(match.get("teams"), list) else []
    return [team for team in teams if isinstance(team, dict)]


def _match_players(package: dict[str, Any]) -> list[dict[str, Any]]:
    players: list[dict[str, Any]] = []
    for team_index, team in enumerate(_match_teams(package)):
        team_id = str(team.get("id") or f"team-{team_index + 1}")
        team_players = team.get("players") if isinstance(team.get("players"), list) else []
        for player_index, player in enumerate(team_players):
            if not isinstance(player, dict):
                continue
            player_id = str(player.get("id") or f"{team_id}-player-{player_index + 1}")
            players.append(
                {
                    "id": player_id,
                    "match_id": _published_id_from_package(package),
                    "team_id": team_id,
                    "name": str(player.get("name") or player_id),
                    "number": player.get("number"),
                    "role": player.get("role"),
                    "is_guest": bool(player.get("is_guest")),
                }
            )
    return players


def _stable_players(package: dict[str, Any]) -> list[dict[str, Any]]:
    stable_doc = package.get("stable_players") if isinstance(package.get("stable_players"), dict) else {}
    players = stable_doc.get("players") if isinstance(stable_doc.get("players"), list) else []
    normalized = []
    published_id = _published_id_from_package(package)
    for player in players:
        if not isinstance(player, dict):
            continue
        stable_player_id = str(player.get("stable_player_id") or player.get("stable_subject_id") or "")
        if not stable_player_id:
            continue
        normalized.append(
            {
                "id": stable_player_id,
                "match_id": published_id,
                "stable_subject_id": player.get("stable_subject_id"),
                "team_id": player.get("team_id"),
                "team_label": str(player.get("team_label") or "U"),
                "team_name": player.get("team_name"),
                "duration_sec": float(player.get("duration_sec") or 0),
                "confidence": str(player.get("confidence") or "low"),
                "confidence_score": player.get("confidence_score"),
                "tracklet_ids": player.get("tracklet_ids") or [],
            }
        )
    return normalized


def _summary_from_package(
    package: dict[str, Any],
    *,
    published_id: str,
    created_at: str,
    updated_at: str,
) -> dict[str, Any]:
    match = package.get("match")
    if not isinstance(match, dict):
        raise ValueError("Package must contain a match object.")

    source_match_id = str(match.get("id") or "")
    if not source_match_id:
        raise ValueError("Package match.id is required.")

    analysis = package.get("analysis_report") if isinstance(package.get("analysis_report"), dict) else {}
    warnings = analysis.get("warnings") if isinstance(analysis, dict) else []
    warnings_count = len(warnings) if isinstance(warnings, list) else 0
    teams = _match_teams(package)
    player_count = sum(
        len(team.get("players") or [])
        for team in teams
        if isinstance(team.get("players"), list)
    )
    return {
        "id": published_id,
        "source_match_id": source_match_id,
        "title": str(match.get("title") or "Untitled match"),
        "match_date": match.get("match_date"),
        "season": match.get("season"),
        "venue": match.get("venue"),
        "format": match.get("format"),
        "status": "published",
        "schema_version": str(package.get("schema_version") or "unknown"),
        "team_count": int(package.get("team_count") or len(teams)),
        "player_count": int(package.get("player_count") or player_count),
        "tracks_count": analysis.get("tracks_count") if isinstance(analysis, dict) else None,
        "frames_processed": analysis.get("frames_processed") if isinstance(analysis, dict) else None,
        "detections_kept": analysis.get("detections_kept") if isinstance(analysis, dict) else None,
        "warnings_count": warnings_count,
        "created_at": created_at,
        "updated_at": updated_at,
        "storage": "json",
    }


def import_match_package(package: dict[str, Any], *, replace: bool = False) -> dict[str, Any]:
    init_publish_store()
    published_id = _published_id_from_package(package)
    target_dir = _published_match_dir(published_id)
    summary_path = target_dir / "summary.json"

    if target_dir.exists() and not replace:
        raise FileExistsError(f"Published match {published_id} already exists. Re-import with replace=true to overwrite it.")
    existing_summary = _load_json_object(summary_path) if summary_path.exists() and replace else {}
    if target_dir.exists() and replace:
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    generated = now_iso()
    created_at = str(existing_summary.get("created_at") or generated)
    summary = _summary_from_package(
        package,
        published_id=published_id,
        created_at=created_at,
        updated_at=generated,
    )
    _atomic_write_json(target_dir / "package.json", package)
    _atomic_write_json(summary_path, summary)
    return get_published_match(published_id)


def list_published_matches() -> list[dict[str, Any]]:
    init_publish_store()
    rows = []
    for summary_path in PUBLISHED_MATCHES_DIR.glob("*/summary.json"):
        try:
            rows.append(_load_json_object(summary_path))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return sorted(
        rows,
        key=lambda row: (
            str(row.get("match_date") or row.get("created_at") or ""),
            str(row.get("created_at") or ""),
        ),
        reverse=True,
    )


def get_published_match(match_id: str) -> dict[str, Any]:
    init_publish_store()
    target_dir = _published_match_dir(match_id)
    summary_path = target_dir / "summary.json"
    package_path = target_dir / "package.json"
    if not summary_path.exists() or not package_path.exists():
        raise KeyError(match_id)
    summary = _load_json_object(summary_path)
    package = _load_json_object(package_path)
    teams = [
        {
            "id": str(team.get("id") or f"team-{index + 1}"),
            "match_id": match_id,
            "name": str(team.get("name") or team.get("id") or f"Team {index + 1}"),
            "color": team.get("color"),
            "players_json": team.get("players") if isinstance(team.get("players"), list) else [],
        }
        for index, team in enumerate(_match_teams(package))
    ]
    return {
        **summary,
        "package": package,
        "teams": teams,
        "players": _match_players(package),
        "stable_players": _stable_players(package),
    }


def delete_published_match(match_id: str) -> dict[str, Any]:
    init_publish_store()
    target_dir = _published_match_dir(match_id)
    summary_path = target_dir / "summary.json"
    if not summary_path.exists():
        raise KeyError(match_id)
    summary = _load_json_object(summary_path)
    shutil.rmtree(target_dir)
    return summary


def publish_store_health() -> dict[str, Any]:
    init_publish_store()
    return {
        "path": str(PUBLISHED_MATCHES_DIR),
        "published_matches": len(list_published_matches()),
        "storage": "json",
    }
