from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, UploadFile

from app.main import create_match, parse_metadata_form
from app.services.match_roster import match_roster_readiness


def _player(name: str, player_id: str | None = None) -> dict[str, object]:
    return {
        "id": player_id,
        "name": name,
        "role": "player",
        "is_guest": False,
    }


def _team(
    name: str,
    team_id: str | None,
    players: list[dict[str, object]],
) -> dict[str, object]:
    return {"id": team_id, "name": name, "players": players}


class MatchRosterTests(unittest.TestCase):
    def test_missing_team_a(self) -> None:
        status = match_roster_readiness([])
        self.assertFalse(status["ready"])
        self.assertEqual(status["code"], "missing_team_a")

    def test_missing_team_b(self) -> None:
        status = match_roster_readiness(
            [_team("Corgi", "team-a", [_player("Pawel", "pawel")])]
        )
        self.assertFalse(status["ready"])
        self.assertEqual(status["code"], "missing_team_b")

    def test_same_team_cannot_fill_both_sides(self) -> None:
        team = _team("Corgi", "team-a", [_player("Pawel", "pawel")])
        status = match_roster_readiness([team, dict(team)])
        self.assertFalse(status["ready"])
        self.assertEqual(status["code"], "duplicate_teams")

    def test_team_a_roster_cannot_be_empty(self) -> None:
        status = match_roster_readiness(
            [
                _team("Corgi", "team-a", []),
                _team("Verisk", "team-b", [_player("Roman", "roman")]),
            ]
        )
        self.assertFalse(status["ready"])
        self.assertEqual(status["code"], "empty_team_a_roster")

    def test_team_b_roster_cannot_be_empty(self) -> None:
        status = match_roster_readiness(
            [
                _team("Corgi", "team-a", [_player("Pawel", "pawel")]),
                _team("Verisk", "team-b", []),
            ]
        )
        self.assertFalse(status["ready"])
        self.assertEqual(status["code"], "empty_team_b_roster")

    def test_valid_team_a_and_team_b_rosters_are_ready(self) -> None:
        status = match_roster_readiness(
            [
                _team("Corgi", "team-a", [_player("Pawel", "pawel")]),
                _team("Verisk", "team-b", [_player("Roman", "roman")]),
            ]
        )
        self.assertTrue(status["ready"])
        self.assertIsNone(status["code"])

    def test_metadata_parser_generates_team_and_player_ids(self) -> None:
        teams = [
            _team("Corgi", None, [_player("Pawel")]),
            _team("Verisk", None, [_player("Roman")]),
        ]
        metadata = parse_metadata_form(
            title="Corgi - Verisk",
            match_date=None,
            season=None,
            venue=None,
            format="7v7",
            teams_json=json.dumps(teams),
        )

        team_a, team_b = metadata["teams"]
        self.assertEqual(team_a["id"], "team-1-corgi")
        self.assertEqual(team_b["id"], "team-2-verisk")
        self.assertEqual(
            team_a["players"][0]["id"],
            "team-1-corgi-player-1-pawel",
        )
        self.assertEqual(
            team_b["players"][0]["id"],
            "team-2-verisk-player-1-roman",
        )

    def test_invalid_metadata_does_not_create_match_or_persist_video(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            matches_dir = Path(tmp) / "matches"
            upload = UploadFile(
                filename="match.mp4",
                file=io.BytesIO(b"video bytes must not be copied"),
            )
            invalid_teams = [
                _team("Corgi", "team-a", [_player("Pawel", "pawel")])
            ]

            with patch("app.main.MATCHES_DIR", matches_dir), self.assertRaises(
                HTTPException
            ) as raised:
                create_match(video=upload, teams_json=json.dumps(invalid_teams))

            self.assertEqual(raised.exception.status_code, 400)
            self.assertFalse(matches_dir.exists())
            self.assertEqual(upload.file.tell(), 0)


if __name__ == "__main__":
    unittest.main()
