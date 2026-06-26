from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.player_profiles import build_player_profile_stats


def resolved_player(player_id: str, *, distance: float, playing_time: float, peak_speed: float, match_slot: str) -> dict:
    return {
        "player_id": player_id,
        "player_name": "Pawel" if player_id == "p-a-1" else "Opponent",
        "player_number": "7" if player_id == "p-a-1" else None,
        "player_role": "player",
        "team_label": "A" if player_id == "p-a-1" else "B",
        "team_id": "team-a" if player_id == "p-a-1" else "team-b",
        "team_name": "White" if player_id == "p-a-1" else "Orange",
        "stable_player_ids": [match_slot],
        "stable_subject_ids": [f"slot-{match_slot.lower()}"],
        "source_stable_slots": [{"stable_player_id": match_slot, "stable_subject_id": f"slot-{match_slot.lower()}"}],
        "time": {
            "playing_time_sec": playing_time,
            "detected_time_sec": playing_time - 2.0,
            "missing_time_sec": 2.0,
            "ambiguous_time_sec": 0.0,
        },
        "distance": {
            "observed_distance_m": distance - 5.0,
            "estimated_short_gap_distance_m": 5.0,
            "total_distance_m": distance,
            "quality": "medium",
        },
        "speed": {
            "avg_speed_kmh": 0.0,
            "peak_sustained_speed_kmh": peak_speed,
            "top_speed_kmh": peak_speed,
            "quality": "medium",
        },
        "frames": {"active_frames": 100, "detected_frames": 90, "missing_frames": 10},
        "segments": {"observed_segments": 3},
        "review_warnings": [],
    }


def write_match(matches_dir: Path, match_id: str, title: str, match_date: str, players: list[dict]) -> None:
    match_path = matches_dir / match_id
    match_path.mkdir(parents=True)
    (match_path / "match.json").write_text(
        json.dumps(
            {
                "id": match_id,
                "title": title,
                "match_date": match_date,
                "season": "2026",
                "venue": "Orlik",
                "status": "reviewed",
            }
        ),
        encoding="utf-8",
    )
    (match_path / "resolved_player_stats.json").write_text(
        json.dumps(
            {
                "schema_version": "0.1.0",
                "scope": "resolved_player_tracking_only_no_ball",
                "summary": {"players": len(players)},
                "players": players,
            }
        ),
        encoding="utf-8",
    )


class PlayerProfileTests(unittest.TestCase):
    def test_profile_aggregates_only_explicit_player_assignments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            matches_dir = Path(tmp)
            write_match(
                matches_dir,
                "match-1",
                "Old match",
                "2026-06-01",
                [
                    resolved_player("p-a-1", distance=100.0, playing_time=50.0, peak_speed=18.0, match_slot="A01"),
                    resolved_player("anonymous-b", distance=80.0, playing_time=40.0, peak_speed=15.0, match_slot="B03"),
                ],
            )
            match2_player = resolved_player("p-a-1", distance=150.0, playing_time=60.0, peak_speed=20.0, match_slot="A04")
            match2_player["review_warnings"] = ["team_id_mismatch"]
            write_match(matches_dir, "match-2", "New match", "2026-06-20", [match2_player])

            profile = build_player_profile_stats(
                matches_dir,
                "p-a-1",
                registry_teams=[
                    {
                        "id": "team-a",
                        "name": "White",
                        "players": [{"id": "p-a-1", "name": "Pawel", "number": "7", "role": "player"}],
                    }
                ],
            )

            self.assertEqual(profile["player"]["player_name"], "Pawel")
            self.assertEqual(profile["summary"]["matches"], 2)
            self.assertEqual(profile["summary"]["total_distance_m"], 250.0)
            self.assertEqual(profile["summary"]["playing_time_sec"], 110.0)
            self.assertEqual(profile["summary"]["peak_sustained_speed_kmh"], 20.0)
            self.assertEqual(profile["summary"]["matches_with_warnings"], 1)
            self.assertEqual(profile["summary"]["anonymous_slots_aggregated"], 0)
            self.assertEqual([item["match_id"] for item in profile["appearances"]], ["match-2", "match-1"])

    def test_profile_exists_for_registry_player_without_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile = build_player_profile_stats(
                Path(tmp),
                "p-a-2",
                registry_teams=[
                    {
                        "id": "team-a",
                        "name": "White",
                        "players": [{"id": "p-a-2", "name": "Kuba", "number": "11", "role": "player"}],
                    }
                ],
            )

            self.assertEqual(profile["player"]["player_name"], "Kuba")
            self.assertEqual(profile["summary"]["matches"], 0)
            self.assertEqual(profile["appearances"], [])

    def test_unknown_player_without_appearances_raises_key_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(KeyError):
                build_player_profile_stats(Path(tmp), "missing-player", registry_teams=[])


if __name__ == "__main__":
    unittest.main()
