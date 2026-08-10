from __future__ import annotations

from typing import Any


def match_roster_readiness(
    match_document_or_teams: dict[str, Any] | list[dict[str, Any]],
    *,
    require_player_ids: bool = True,
) -> dict[str, Any]:
    teams = (
        match_document_or_teams.get("teams") or []
        if isinstance(match_document_or_teams, dict)
        else match_document_or_teams
    )
    team_a = teams[0] if len(teams) > 0 and isinstance(teams[0], dict) else None
    team_b = teams[1] if len(teams) > 1 and isinstance(teams[1], dict) else None
    if team_a is None:
        return _not_ready("missing_team_a", "Wybierz Team A z rosterem zawodników.")
    if team_b is None:
        return _not_ready("missing_team_b", "Wybierz Team B z rosterem zawodników.")
    if _same_team(team_a, team_b):
        return _not_ready("duplicate_teams", "Team A i Team B muszą być różnymi drużynami.")
    if not _valid_players(team_a, require_player_ids=require_player_ids):
        return _not_ready("empty_team_a_roster", "Team A musi mieć co najmniej jednego zawodnika w rosterze.")
    if not _valid_players(team_b, require_player_ids=require_player_ids):
        return _not_ready("empty_team_b_roster", "Team B musi mieć co najmniej jednego zawodnika w rosterze.")
    return {"ready": True, "code": None, "detail": None}


def require_match_roster(
    match_document_or_teams: dict[str, Any] | list[dict[str, Any]],
    *,
    require_player_ids: bool = True,
) -> None:
    status = match_roster_readiness(
        match_document_or_teams,
        require_player_ids=require_player_ids,
    )
    if not status["ready"]:
        raise ValueError(str(status["detail"]))


def _same_team(team_a: dict[str, Any], team_b: dict[str, Any]) -> bool:
    team_a_id = str(team_a.get("id") or "").strip()
    team_b_id = str(team_b.get("id") or "").strip()
    if team_a_id and team_b_id:
        return team_a_id == team_b_id
    return str(team_a.get("name") or "").strip().casefold() == str(
        team_b.get("name") or ""
    ).strip().casefold()


def _valid_players(team: dict[str, Any], *, require_player_ids: bool) -> bool:
    return any(
        isinstance(player, dict)
        and bool(str(player.get("name") or "").strip())
        and (not require_player_ids or bool(str(player.get("id") or "").strip()))
        for player in team.get("players") or []
    )


def _not_ready(code: str, detail: str) -> dict[str, Any]:
    return {"ready": False, "code": code, "detail": detail}