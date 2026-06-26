from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import team_registry


class TeamRegistryTests(unittest.TestCase):
    def test_create_update_delete_team_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            teams_path = Path(tmp) / "teams.json"
            with patch.object(team_registry, "TEAMS_PATH", teams_path):
                created = team_registry.create_team(
                    {
                        "name": "White Team",
                        "color": "#ffffff",
                        "players": [
                            {"name": "Pawel", "number": "7", "role": "player"},
                            {"name": "Guest", "role": "guest", "is_guest": True},
                        ],
                    }
                )

                self.assertEqual(created["id"], "team-white-team")
                self.assertEqual(created["players"][0]["id"], "team-white-team-player-1-pawel")
                self.assertEqual(len(team_registry.list_teams()), 1)

                updated = team_registry.update_team(
                    created["id"],
                    {
                        "name": "White Team Renamed",
                        "color": "#eeeeee",
                        "players": [{"name": "Pawel", "number": "10"}],
                    },
                )

                self.assertEqual(updated["id"], created["id"])
                self.assertEqual(updated["name"], "White Team Renamed")
                self.assertEqual(updated["players"][0]["number"], "10")

                deleted = team_registry.delete_team(created["id"])
                self.assertEqual(deleted["status"], "deleted")
                self.assertEqual(team_registry.list_teams(), [])


if __name__ == "__main__":
    unittest.main()
