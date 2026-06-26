from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import DATABASE_PATH


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS published_matches (
                id TEXT PRIMARY KEY,
                source_match_id TEXT NOT NULL,
                title TEXT NOT NULL,
                match_date TEXT,
                season TEXT,
                venue TEXT,
                format TEXT,
                status TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                team_count INTEGER NOT NULL DEFAULT 0,
                player_count INTEGER NOT NULL DEFAULT 0,
                tracks_count INTEGER,
                frames_processed INTEGER,
                detections_kept INTEGER,
                warnings_count INTEGER NOT NULL DEFAULT 0,
                package_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS published_teams (
                id TEXT NOT NULL,
                match_id TEXT NOT NULL,
                name TEXT NOT NULL,
                color TEXT,
                players_json TEXT NOT NULL,
                PRIMARY KEY (match_id, id),
                FOREIGN KEY (match_id) REFERENCES published_matches(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS published_players (
                id TEXT NOT NULL,
                match_id TEXT NOT NULL,
                team_id TEXT NOT NULL,
                name TEXT NOT NULL,
                number TEXT,
                role TEXT,
                is_guest INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (match_id, id),
                FOREIGN KEY (match_id, team_id) REFERENCES published_teams(match_id, id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS published_stable_players (
                id TEXT NOT NULL,
                match_id TEXT NOT NULL,
                stable_subject_id TEXT,
                team_id TEXT,
                team_label TEXT NOT NULL,
                team_name TEXT,
                duration_sec REAL NOT NULL DEFAULT 0,
                confidence TEXT NOT NULL,
                confidence_score REAL,
                tracklet_ids_json TEXT NOT NULL,
                PRIMARY KEY (match_id, id),
                FOREIGN KEY (match_id) REFERENCES published_matches(id) ON DELETE CASCADE
            );
            """
        )


def _row_to_match(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "source_match_id": row["source_match_id"],
        "title": row["title"],
        "match_date": row["match_date"],
        "season": row["season"],
        "venue": row["venue"],
        "format": row["format"],
        "status": row["status"],
        "schema_version": row["schema_version"],
        "team_count": row["team_count"],
        "player_count": row["player_count"],
        "tracks_count": row["tracks_count"],
        "frames_processed": row["frames_processed"],
        "detections_kept": row["detections_kept"],
        "warnings_count": row["warnings_count"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _published_id_from_package(package: dict[str, Any]) -> str:
    match = package.get("match") or {}
    source_match_id = str(match.get("id") or "unknown")
    # Match IDs are currently local UUID fragments. Prefix keeps the production namespace explicit.
    return f"published-{source_match_id}"


def import_match_package(package: dict[str, Any], *, replace: bool = False) -> dict[str, Any]:
    init_db()
    match = package.get("match")
    if not isinstance(match, dict):
        raise ValueError("Package must contain a match object.")

    source_match_id = str(match.get("id") or "")
    if not source_match_id:
        raise ValueError("Package match.id is required.")

    published_id = _published_id_from_package(package)
    analysis = package.get("analysis_report") if isinstance(package.get("analysis_report"), dict) else {}
    warnings = analysis.get("warnings") if isinstance(analysis, dict) else []
    warnings_count = len(warnings) if isinstance(warnings, list) else 0
    teams = match.get("teams") if isinstance(match.get("teams"), list) else []
    generated = now_iso()

    with connect() as conn:
        existing = conn.execute("SELECT id FROM published_matches WHERE id = ?", (published_id,)).fetchone()
        if existing and not replace:
            raise FileExistsError(f"Published match {published_id} already exists. Re-import with replace=true to overwrite it.")
        if existing and replace:
            conn.execute("DELETE FROM published_matches WHERE id = ?", (published_id,))

        conn.execute(
            """
            INSERT INTO published_matches (
                id, source_match_id, title, match_date, season, venue, format, status,
                schema_version, team_count, player_count, tracks_count, frames_processed,
                detections_kept, warnings_count, package_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                published_id,
                source_match_id,
                str(match.get("title") or "Untitled match"),
                match.get("match_date"),
                match.get("season"),
                match.get("venue"),
                match.get("format"),
                "published",
                str(package.get("schema_version") or "unknown"),
                int(package.get("team_count") or len(teams)),
                int(package.get("player_count") or sum(len(team.get("players") or []) for team in teams if isinstance(team, dict))),
                analysis.get("tracks_count") if isinstance(analysis, dict) else None,
                analysis.get("frames_processed") if isinstance(analysis, dict) else None,
                analysis.get("detections_kept") if isinstance(analysis, dict) else None,
                warnings_count,
                json.dumps(package, ensure_ascii=False, sort_keys=True),
                generated,
                generated,
            ),
        )

        for team_index, team in enumerate(teams):
            if not isinstance(team, dict):
                continue
            team_id = str(team.get("id") or f"team-{team_index + 1}")
            players = team.get("players") if isinstance(team.get("players"), list) else []
            conn.execute(
                """
                INSERT INTO published_teams (id, match_id, name, color, players_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    team_id,
                    published_id,
                    str(team.get("name") or team_id),
                    team.get("color"),
                    json.dumps(players, ensure_ascii=False, sort_keys=True),
                ),
            )
            for player_index, player in enumerate(players):
                if not isinstance(player, dict):
                    continue
                player_id = str(player.get("id") or f"{team_id}-player-{player_index + 1}")
                conn.execute(
                    """
                    INSERT INTO published_players (id, match_id, team_id, name, number, role, is_guest)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        player_id,
                        published_id,
                        team_id,
                        str(player.get("name") or player_id),
                        player.get("number"),
                        player.get("role"),
                        1 if player.get("is_guest") else 0,
                    ),
                )

        stable_doc = package.get("stable_players") if isinstance(package.get("stable_players"), dict) else {}
        stable_players = stable_doc.get("players") if isinstance(stable_doc.get("players"), list) else []
        for player in stable_players:
            if not isinstance(player, dict):
                continue
            stable_player_id = str(player.get("stable_player_id") or player.get("stable_subject_id") or "")
            if not stable_player_id:
                continue
            conn.execute(
                """
                INSERT INTO published_stable_players (
                    id, match_id, stable_subject_id, team_id, team_label, team_name,
                    duration_sec, confidence, confidence_score, tracklet_ids_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stable_player_id,
                    published_id,
                    player.get("stable_subject_id"),
                    player.get("team_id"),
                    str(player.get("team_label") or "U"),
                    player.get("team_name"),
                    float(player.get("duration_sec") or 0),
                    str(player.get("confidence") or "low"),
                    player.get("confidence_score"),
                    json.dumps(player.get("tracklet_ids") or [], ensure_ascii=False, sort_keys=True),
                ),
            )

    return get_published_match(published_id)


def list_published_matches() -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM published_matches
            ORDER BY COALESCE(match_date, created_at) DESC, created_at DESC
            """
        ).fetchall()
    return [_row_to_match(row) for row in rows]


def get_published_match(match_id: str) -> dict[str, Any]:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM published_matches WHERE id = ?", (match_id,)).fetchone()
        if row is None:
            raise KeyError(match_id)
        package = json.loads(row["package_json"])
        teams = conn.execute("SELECT * FROM published_teams WHERE match_id = ? ORDER BY name", (match_id,)).fetchall()
        players = conn.execute("SELECT * FROM published_players WHERE match_id = ? ORDER BY team_id, name", (match_id,)).fetchall()
        stable_players = conn.execute("SELECT * FROM published_stable_players WHERE match_id = ? ORDER BY team_label, id", (match_id,)).fetchall()
    return {
        **_row_to_match(row),
        "package": package,
        "teams": [dict(team) | {"players_json": json.loads(team["players_json"])} for team in teams],
        "players": [dict(player) | {"is_guest": bool(player["is_guest"])} for player in players],
        "stable_players": [dict(player) | {"tracklet_ids": json.loads(player["tracklet_ids_json"])} for player in stable_players],
    }


def delete_published_match(match_id: str) -> dict[str, Any]:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM published_matches WHERE id = ?", (match_id,)).fetchone()
        if row is None:
            raise KeyError(match_id)
        conn.execute("DELETE FROM published_matches WHERE id = ?", (match_id,))
    return _row_to_match(row)


def database_health() -> dict[str, Any]:
    init_db()
    with connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS count FROM published_matches").fetchone()["count"]
    return {"path": str(DATABASE_PATH), "published_matches": int(count)}
