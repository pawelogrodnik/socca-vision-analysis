from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from app.services.identity_jersey_number_common import (
    canonical_digest,
    normalize_jersey_number,
    stable_key,
    team_label,
)


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "identity_jersey_number_roster_shadow"
ALGORITHM_VERSION = "1.0.0"


def build_identity_jersey_number_roster_shadow(
    match_doc: dict[str, Any],
    *,
    reference_doc: dict[str, Any] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build a trusted, conflict-aware jersey registry without mutating match metadata."""
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    reference = reference_doc or {}
    reference_players = {
        str(row.get("player_id")): row
        for row in reference.get("players") or []
        if isinstance(row, dict) and row.get("player_id")
    }
    confirmed_absent = {
        str(value) for value in reference.get("players_without_confirmed_number") or []
    }
    rows: list[dict[str, Any]] = []
    for label, team in _teams(match_doc):
        for player in team.get("players") or []:
            if not isinstance(player, dict):
                continue
            player_id = str(player.get("id") or player.get("player_id") or "")
            if not player_id:
                continue
            roster_number = normalize_jersey_number(
                player.get("number") if player.get("number") is not None else player.get("jersey_number")
            )
            reference_row = reference_players.get(player_id) or {}
            reference_number = normalize_jersey_number(reference_row.get("jersey_number"))
            conflicts: list[str] = []
            if roster_number and reference_number and roster_number != reference_number:
                conflicts.append("number_source_disagreement")
            number = reference_number or roster_number
            if conflicts:
                status = "conflict"
            elif number is not None:
                status = "confirmed"
            elif player_id in confirmed_absent:
                status = "confirmed_absent"
            else:
                status = "unknown"
            source = (
                str(reference.get("source") or "manual_reference")
                if reference_number is not None or player_id in confirmed_absent
                else "match_config" if roster_number is not None else "none"
            )
            rows.append(
                {
                    "player_id": player_id,
                    "player_name": player.get("name") or reference_row.get("player_name"),
                    "team_id": str(team.get("id") or team.get("team_id") or ""),
                    "team_name": team.get("name"),
                    "team_label": label,
                    "jersey_number": number,
                    "jersey_number_source": source,
                    "jersey_number_trusted": status in {"confirmed", "confirmed_absent"},
                    "roster_number_status": status,
                    "conflicts": conflicts,
                }
            )

    by_team_number: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["jersey_number"] is not None and row["roster_number_status"] != "conflict":
            by_team_number[(row["team_label"], row["jersey_number"])].append(row)
    duplicate_keys = {key for key, values in by_team_number.items() if len(values) > 1}
    for row in rows:
        key = (row["team_label"], row["jersey_number"])
        if row["jersey_number"] is not None and key in duplicate_keys:
            row["roster_number_status"] = "conflict"
            row["jersey_number_trusted"] = False
            row["conflicts"] = sorted(set(row["conflicts"] + ["duplicate_number_within_team"]))
        row["registry_key"] = stable_key(
            "jersey-roster",
            {"team_label": row["team_label"], "player_id": row["player_id"]},
        )

    unique_lookup = {
        f"{label}:{number}": {
            "team_label": label,
            "jersey_number": number,
            "player_id": values[0]["player_id"],
            "player_name": values[0].get("player_name"),
        }
        for (label, number), values in sorted(by_team_number.items())
        if len(values) == 1 and values[0]["jersey_number_trusted"]
    }
    statuses = Counter(row["roster_number_status"] for row in rows)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_read_only",
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION},
        "source": {
            "match_digest": canonical_digest(match_doc),
            "reference_digest": canonical_digest(reference),
        },
        "safety": {
            "mutates_match_roster": False,
            "mutates_candidate_identity": False,
            "mutates_production_identity": False,
            "automatic_assignments": 0,
        },
        "summary": {
            "players": len(rows),
            "confirmed_numbers": statuses["confirmed"],
            "confirmed_absent": statuses["confirmed_absent"],
            "unknown": statuses["unknown"],
            "conflicts": statuses["conflict"],
            "unique_trusted_numbers": len(unique_lookup),
        },
        "players": sorted(rows, key=lambda row: (row["team_label"], str(row.get("player_name") or ""))),
        "unique_number_lookup": unique_lookup,
        "gates": {
            "numbers_unique_within_team": not duplicate_keys,
            "conflicts_disable_anchor": all(
                not row["jersey_number_trusted"] for row in rows if row["roster_number_status"] == "conflict"
            ),
            "same_number_across_teams_allowed": True,
        },
    }


def _teams(match_doc: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    candidates = match_doc.get("teams")
    if not isinstance(candidates, list):
        metadata = match_doc.get("metadata") if isinstance(match_doc.get("metadata"), dict) else {}
        candidates = metadata.get("teams")
    if not isinstance(candidates, list):
        candidates = []
    result: list[tuple[str, dict[str, Any]]] = []
    for index, team in enumerate(candidates):
        if not isinstance(team, dict):
            continue
        explicit = team_label(team.get("team_label") or team.get("label"))
        result.append((explicit if explicit != "U" else ("A" if index == 0 else "B" if index == 1 else "U"), team))
    return result
