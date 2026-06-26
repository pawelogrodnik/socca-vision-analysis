from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import MATCHES_DIR


TEAMS_PATH = MATCHES_DIR.parent / "teams.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return normalized or "item"


def list_teams() -> list[dict[str, Any]]:
    if not TEAMS_PATH.exists():
        return []
    data = json.loads(TEAMS_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return []
    teams = data.get("teams")
    if not isinstance(teams, list):
        return []
    return sorted([team for team in teams if isinstance(team, dict)], key=lambda team: str(team.get("name") or ""))


def get_team(team_id: str) -> dict[str, Any]:
    for team in list_teams():
        if team.get("id") == team_id:
            return team
    raise KeyError(team_id)


def create_team(payload: dict[str, Any]) -> dict[str, Any]:
    teams = list_teams()
    team = _normalize_team(payload, existing_ids={str(item.get("id")) for item in teams})
    teams.append(team)
    _write_teams(teams)
    return team


def update_team(team_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    teams = list_teams()
    updated: dict[str, Any] | None = None
    existing_ids = {str(item.get("id")) for item in teams if item.get("id") != team_id}
    next_teams = []
    for team in teams:
        if team.get("id") != team_id:
            next_teams.append(team)
            continue
        merged = {**team, **payload, "id": team_id, "created_at": team.get("created_at") or now_iso()}
        updated = _normalize_team(merged, existing_ids=existing_ids)
        next_teams.append(updated)
    if updated is None:
        raise KeyError(team_id)
    _write_teams(next_teams)
    return updated


def delete_team(team_id: str) -> dict[str, Any]:
    teams = list_teams()
    next_teams = [team for team in teams if team.get("id") != team_id]
    if len(next_teams) == len(teams):
        raise KeyError(team_id)
    _write_teams(next_teams)
    return {"status": "deleted", "team_id": team_id}


def _write_teams(teams: list[dict[str, Any]]) -> None:
    TEAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    TEAMS_PATH.write_text(
        json.dumps(
            {
                "schema_version": "0.1.0",
                "updated_at": now_iso(),
                "teams": teams,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _normalize_team(payload: dict[str, Any], *, existing_ids: set[str]) -> dict[str, Any]:
    name = str(payload.get("name") or "Team").strip() or "Team"
    team_id = str(payload.get("id") or "").strip()
    if not team_id:
        base = f"team-{slugify(name)}"
        team_id = base
        if team_id in existing_ids:
            team_id = f"{base}-{uuid.uuid4().hex[:6]}"
    if team_id in existing_ids:
        raise ValueError(f"Team id already exists: {team_id}")
    now = now_iso()
    team = {
        "id": team_id,
        "name": name,
        "color": payload.get("color") or "#64748b",
        "players": _normalize_players(team_id, payload.get("players") if isinstance(payload.get("players"), list) else []),
        "created_at": payload.get("created_at") or now,
        "updated_at": now,
    }
    return team


def _normalize_players(team_id: str, players: list[Any]) -> list[dict[str, Any]]:
    normalized = []
    used_ids: set[str] = set()
    for index, raw_player in enumerate(players):
        if not isinstance(raw_player, dict):
            continue
        name = str(raw_player.get("name") or "").strip()
        if not name:
            continue
        player_id = str(raw_player.get("id") or "").strip()
        if not player_id:
            player_id = f"{team_id}-player-{index + 1}-{slugify(name)}"
        if player_id in used_ids:
            player_id = f"{player_id}-{uuid.uuid4().hex[:4]}"
        used_ids.add(player_id)
        normalized.append(
            {
                "id": player_id,
                "name": name,
                "number": raw_player.get("number") or None,
                "role": raw_player.get("role") or "player",
                "is_guest": bool(raw_player.get("is_guest")),
            }
        )
    return normalized
