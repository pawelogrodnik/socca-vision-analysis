from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ASSIGNMENT_STATUSES = {
    "unassigned",
    "assigned",
    "unknown",
    "ignore",
    "referee",
    "false_positive",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_stable_doc(path: Path) -> dict[str, Any]:
    stable_path = path / "stable_players.json"
    if not stable_path.exists():
        raise FileNotFoundError("stable_players.json not found. Run analysis first.")
    data = json.loads(stable_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("stable_players.json must contain an object")
    return data


def _load_existing_doc(path: Path) -> dict[str, Any] | None:
    assignment_path = path / "player_identity_assignments.json"
    if not assignment_path.exists():
        return None
    data = json.loads(assignment_path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def _roster_players(meta: dict[str, Any]) -> dict[str, dict[str, Any]]:
    players: dict[str, dict[str, Any]] = {}
    for team_index, team in enumerate(meta.get("teams") or []):
        if not isinstance(team, dict):
            continue
        team_id = str(team.get("id") or f"team-{team_index + 1}")
        team_name = str(team.get("name") or f"Team {team_index + 1}")
        team_label = "A" if team_index == 0 else "B" if team_index == 1 else "U"
        for player in team.get("players") or []:
            if not isinstance(player, dict) or not player.get("id"):
                continue
            player_id = str(player["id"])
            players[player_id] = {
                "player_id": player_id,
                "player_name": str(player.get("name") or player_id),
                "player_number": player.get("number"),
                "player_role": player.get("role") or "player",
                "is_guest": bool(player.get("is_guest")),
                "team_id": team_id,
                "team_name": team_name,
                "team_label": team_label,
            }
    return players


def _roster_summary(meta: dict[str, Any]) -> dict[str, Any]:
    teams = [team for team in meta.get("teams") or [] if isinstance(team, dict)]
    return {
        "teams": len(teams),
        "players": sum(len(team.get("players") or []) for team in teams),
        "players_by_team": {
            str(team.get("id") or team.get("name") or f"team-{index + 1}"): len(team.get("players") or [])
            for index, team in enumerate(teams)
        },
    }


def _stable_players(stable_doc: dict[str, Any]) -> list[dict[str, Any]]:
    players = stable_doc.get("players")
    return players if isinstance(players, list) else []


def _stable_subject_id(player: dict[str, Any]) -> str:
    return str(player.get("stable_subject_id") or player.get("stable_player_id") or player.get("slot_id") or "")


def _slot_defaults(player: dict[str, Any]) -> dict[str, Any]:
    stable_subject_id = _stable_subject_id(player)
    stints = [stint for stint in player.get("stints") or [] if isinstance(stint, dict)]
    player_status = str(player.get("status") or "active")
    default_status = player_status if player_status in {"ignore", "referee", "false_positive", "unknown"} else "unassigned"
    return {
        "stable_subject_id": stable_subject_id,
        "stable_player_id": player.get("stable_player_id") or stable_subject_id,
        "slot_id": player.get("slot_id"),
        "stint_id": None,
        "stint_ids": [stint.get("stint_id") for stint in stints if stint.get("stint_id")],
        "assignment_scope": "stable_slot",
        "status": default_status,
        "team_label": player.get("team_label") or "U",
        "team_id": player.get("team_id"),
        "team_name": player.get("team_name"),
        "player_id": None,
        "player_name": None,
        "player_number": None,
        "player_role": None,
        "notes": "",
        "review_warnings": [],
    }


def _stable_lookup(stable_doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _stable_subject_id(player): player
        for player in _stable_players(stable_doc)
        if _stable_subject_id(player)
    }


def _assignment_key(item: dict[str, Any]) -> tuple[str, str]:
    return str(item.get("stable_subject_id") or ""), str(item.get("stint_id") or "")


def _normalize_assignment(
    item: dict[str, Any],
    *,
    stable_by_subject: dict[str, dict[str, Any]],
    roster_by_player: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    stable_subject_id = str(item.get("stable_subject_id") or "")
    stable_player = stable_by_subject.get(stable_subject_id)
    if not stable_player:
        return None

    normalized = _slot_defaults(stable_player)
    stint_id = item.get("stint_id") or None
    if stint_id is not None:
        valid_stints = {str(stint.get("stint_id")) for stint in stable_player.get("stints") or [] if isinstance(stint, dict)}
        if str(stint_id) not in valid_stints:
            raise ValueError(f"Unknown stint_id={stint_id!r} for stable_subject_id={stable_subject_id!r}")
        normalized["stint_id"] = str(stint_id)
        normalized["stint_ids"] = [str(stint_id)]
        normalized["assignment_scope"] = "stint"

    status = str(item.get("status") or normalized["status"] or "unassigned")
    if status not in ASSIGNMENT_STATUSES:
        status = "unassigned"
    normalized["status"] = status
    normalized["notes"] = str(item.get("notes") or "")

    player_id = item.get("player_id") or None
    if status == "assigned":
        if not player_id:
            raise ValueError(f"Assigned stable_subject_id={stable_subject_id!r} is missing player_id")
        roster_player = roster_by_player.get(str(player_id))
        if not roster_player:
            raise ValueError(f"Unknown player_id={player_id!r}")
        normalized.update(roster_player)
        warnings = []
        stable_team_id = stable_player.get("team_id")
        stable_team_label = stable_player.get("team_label")
        if stable_team_id and roster_player.get("team_id") and stable_team_id != roster_player["team_id"]:
            warnings.append("team_id_mismatch")
        if stable_team_label in {"A", "B"} and roster_player.get("team_label") != stable_team_label:
            warnings.append("team_label_mismatch")
        normalized["review_warnings"] = warnings
    else:
        normalized["player_id"] = None
        normalized["player_name"] = None
        normalized["player_number"] = None
        normalized["player_role"] = None
        normalized["is_guest"] = False
        normalized["review_warnings"] = []

    return normalized


def _expanded_stint_assignments(assignments: list[dict[str, Any]], stable_by_subject: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    expanded: list[dict[str, Any]] = []
    for assignment in assignments:
        stable_player = stable_by_subject.get(str(assignment.get("stable_subject_id") or ""))
        if not stable_player:
            continue
        stints = [stint for stint in stable_player.get("stints") or [] if isinstance(stint, dict)]
        if assignment.get("stint_id"):
            stints = [stint for stint in stints if str(stint.get("stint_id")) == str(assignment["stint_id"])]
        if not stints:
            stints = [{"stint_id": None}]
        for stint in stints:
            expanded.append(
                {
                    "stable_subject_id": assignment.get("stable_subject_id"),
                    "stable_player_id": assignment.get("stable_player_id"),
                    "slot_id": assignment.get("slot_id"),
                    "stint_id": stint.get("stint_id"),
                    "status": assignment.get("status"),
                    "team_label": assignment.get("team_label"),
                    "team_id": assignment.get("team_id"),
                    "team_name": assignment.get("team_name"),
                    "player_id": assignment.get("player_id"),
                    "player_name": assignment.get("player_name"),
                    "player_number": assignment.get("player_number"),
                    "player_role": assignment.get("player_role"),
                    "start_time_sec": stint.get("start_time_sec"),
                    "end_time_sec": stint.get("end_time_sec"),
                    "duration_sec": stint.get("duration_sec"),
                }
            )
    return expanded


def _summary(meta: dict[str, Any], assignments: list[dict[str, Any]], expanded: list[dict[str, Any]]) -> dict[str, Any]:
    assigned = [item for item in assignments if item.get("status") == "assigned" and item.get("player_id")]
    ignored = [item for item in assignments if item.get("status") in {"ignore", "referee", "false_positive"}]
    unassigned = [item for item in assignments if item.get("status") in {"unassigned", "unknown", None, ""}]
    assigned_stints = [item for item in expanded if item.get("status") == "assigned" and item.get("player_id")]
    assigned_by_team: dict[str, int] = {}
    players_by_team: dict[str, set[str]] = {}
    for item in assigned:
        team_id = str(item.get("team_id") or "unknown-team")
        assigned_by_team[team_id] = assigned_by_team.get(team_id, 0) + 1
        if item.get("player_id"):
            players_by_team.setdefault(team_id, set()).add(str(item["player_id"]))
    return {
        "stable_slots": len(assignments),
        "assignments_total": len(assignments),
        "assigned_slots": len(assigned),
        "assigned_stints": len(assigned_stints),
        "unassigned_slots": len(unassigned),
        "ignored_slots": len(ignored),
        "unique_players_total": len({str(item.get("player_id")) for item in assigned if item.get("player_id")}),
        "assigned_slots_by_team": assigned_by_team,
        "unique_players_by_team": {team_id: len(players) for team_id, players in players_by_team.items()},
        "roster": _roster_summary(meta),
        "conflicts_total": sum(1 for item in assignments if item.get("review_warnings")),
    }


def build_player_identity_review(path: Path, meta: dict[str, Any]) -> dict[str, Any]:
    stable_doc = _load_stable_doc(path)
    stable_by_subject = _stable_lookup(stable_doc)
    roster_by_player = _roster_players(meta)
    existing = _load_existing_doc(path)
    assignment_items = existing.get("assignments") if isinstance(existing, dict) and isinstance(existing.get("assignments"), list) else []
    doc = _build_doc(
        meta,
        stable_doc,
        stable_by_subject,
        roster_by_player,
        assignment_items,
        updated_at=str(existing.get("updated_at")) if existing and existing.get("updated_at") else now_iso(),
    )
    return {
        "player_identity_assignments": doc,
        "roster": {
            "teams": meta.get("teams") or [],
            "summary": _roster_summary(meta),
        },
    }


def _build_doc(
    meta: dict[str, Any],
    stable_doc: dict[str, Any],
    stable_by_subject: dict[str, dict[str, Any]],
    roster_by_player: dict[str, dict[str, Any]],
    assignment_items: list[dict[str, Any]],
    *,
    updated_at: str,
) -> dict[str, Any]:
    merged = {
        _assignment_key(_slot_defaults(player)): _slot_defaults(player)
        for player in _stable_players(stable_doc)
        if _stable_subject_id(player)
    }
    for item in assignment_items:
        if not isinstance(item, dict):
            continue
        normalized = _normalize_assignment(
            item,
            stable_by_subject=stable_by_subject,
            roster_by_player=roster_by_player,
        )
        if normalized:
            merged[_assignment_key(normalized)] = normalized

    assignments = sorted(merged.values(), key=lambda item: (str(item.get("team_label") or "U"), str(item.get("stable_player_id") or "")))
    expanded = _expanded_stint_assignments(assignments, stable_by_subject)
    return {
        "schema_version": "0.1.0",
        "updated_at": updated_at,
        "source": "stable_slot_roster_review",
        "identity_semantics": stable_doc.get("identity_semantics") or "stint_first",
        "assignment_scope": "stable_slot_or_stint",
        "assignments": assignments,
        "expanded_stint_assignments": expanded,
        "summary": _summary(meta, assignments, expanded),
    }


def save_player_identity_assignments(path: Path, meta: dict[str, Any], assignments: list[dict[str, Any]]) -> dict[str, Any]:
    stable_doc = _load_stable_doc(path)
    stable_by_subject = _stable_lookup(stable_doc)
    roster_by_player = _roster_players(meta)
    existing = _load_existing_doc(path)
    existing_items = existing.get("assignments") if isinstance(existing, dict) and isinstance(existing.get("assignments"), list) else []
    merged_items = list(existing_items)
    for item in assignments:
        if not isinstance(item, dict):
            continue
        normalized = _normalize_assignment(
            item,
            stable_by_subject=stable_by_subject,
            roster_by_player=roster_by_player,
        )
        if not normalized:
            continue
        key = _assignment_key(normalized)
        merged_items = [existing_item for existing_item in merged_items if _assignment_key(existing_item) != key]
        merged_items.append(normalized)

    doc = _build_doc(
        meta,
        stable_doc,
        stable_by_subject,
        roster_by_player,
        merged_items,
        updated_at=now_iso(),
    )
    (path / "player_identity_assignments.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return {
        "player_identity_assignments": doc,
        "roster": {
            "teams": meta.get("teams") or [],
            "summary": _roster_summary(meta),
        },
    }
