from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.team_profiles import build_team_profile_stats


def player_row(
    player_id: str,
    *,
    team_id: str,
    team_name: str,
    distance: float,
    playing_time: float,
    peak_speed: float,
    sprint_count: int,
) -> dict:
    return {
        "player_id": player_id,
        "player_name": "Pawel" if player_id == "p-a-1" else "Other",
        "player_number": "7" if player_id == "p-a-1" else None,
        "player_role": "player",
        "team_id": team_id,
        "team_name": team_name,
        "team_label": "A" if team_id == "team-a" else "B",
        "stable_player_ids": ["A01" if team_id == "team-a" else "B01"],
        "stable_subject_ids": ["slot-a01" if team_id == "team-a" else "slot-b01"],
        "time": {
            "playing_time_sec": playing_time,
            "detected_time_sec": playing_time - 1.0,
            "missing_time_sec": 1.0,
            "ambiguous_time_sec": 0.0,
        },
        "distance": {
            "observed_distance_m": distance - 4.0,
            "estimated_short_gap_distance_m": 4.0,
            "total_distance_m": distance,
            "quality": "medium",
        },
        "speed": {
            "avg_speed_kmh": 0.0,
            "peak_sustained_speed_kmh": peak_speed,
            "top_speed_kmh": peak_speed,
            "quality": "medium",
        },
        "intensity": {
            "high_intensity_time_sec": 1.0,
            "high_intensity_distance_m": 5.0,
            "sprint_count": sprint_count,
            "sprint_time_sec": 0.6 * sprint_count,
            "sprint_distance_m": 3.0 * sprint_count,
            "longest_sprint_distance_m": 3.0 if sprint_count else 0.0,
            "max_sprint_speed_kmh": peak_speed if sprint_count else 0.0,
            "sprint_candidate_count": sprint_count + 1,
            "rejected_sprint_candidate_count": 1,
            "best_sprint_candidate_speed_kmh": peak_speed,
            "best_sprint_candidate_duration_sec": 0.6,
            "best_rejected_sprint_candidate": {
                "max_speed_kmh": peak_speed + 1.0,
                "duration_sec": 0.2,
                "distance_m": 1.2,
                "reason": "too_short",
            },
        },
        "review_warnings": [],
    }


def write_match(
    matches_dir: Path,
    match_id: str,
    *,
    season: str,
    teams: list[dict],
    players: list[dict] | None,
) -> None:
    match_path = matches_dir / match_id
    match_path.mkdir(parents=True)
    (match_path / "match.json").write_text(
        json.dumps(
            {
                "id": match_id,
                "title": match_id,
                "match_date": "2026-06-01",
                "season": season,
                "venue": "Orlik",
                "status": "reviewed",
                "teams": teams,
            }
        ),
        encoding="utf-8",
    )
    if players is not None:
        (match_path / "resolved_player_stats.json").write_text(
            json.dumps(
                {
                    "schema_version": "0.1.0",
                    "scope": "resolved_player_tracking_only_no_ball",
                    "players": players,
                }
            ),
            encoding="utf-8",
        )


class TeamProfileTests(unittest.TestCase):
    def test_team_dashboard_aggregates_only_selected_real_players(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            matches_dir = Path(tmp)
            teams = [
                {"id": "team-a", "name": "White", "players": [{"id": "p-a-1", "name": "Pawel"}]},
                {"id": "team-b", "name": "Orange", "players": [{"id": "p-b-1", "name": "Opponent"}]},
            ]
            write_match(
                matches_dir,
                "match-1",
                season="2026",
                teams=teams,
                players=[
                    player_row("p-a-1", team_id="team-a", team_name="White", distance=100.0, playing_time=50.0, peak_speed=20.0, sprint_count=1),
                    player_row("p-b-1", team_id="team-b", team_name="Orange", distance=80.0, playing_time=40.0, peak_speed=18.0, sprint_count=2),
                ],
            )
            write_match(
                matches_dir,
                "match-2",
                season="2026",
                teams=teams[:1],
                players=[
                    player_row("p-a-1", team_id="team-a", team_name="White", distance=120.0, playing_time=60.0, peak_speed=22.0, sprint_count=0),
                    player_row("anonymous-b", team_id="team-b", team_name="Orange", distance=90.0, playing_time=45.0, peak_speed=19.0, sprint_count=0),
                ],
            )
            write_match(matches_dir, "match-3", season="2026", teams=teams[:1], players=None)
            write_match(
                matches_dir,
                "old-match",
                season="2025",
                teams=teams[:1],
                players=[
                    player_row("p-a-1", team_id="team-a", team_name="White", distance=999.0, playing_time=400.0, peak_speed=30.0, sprint_count=5),
                ],
            )

            doc = build_team_profile_stats(
                matches_dir,
                "team-a",
                season="2026",
                registry_teams=teams,
            )

            self.assertEqual(doc["scope"], "team_tracking_only_no_ball")
            self.assertEqual(doc["summary"]["matches_with_stats"], 2)
            self.assertEqual(doc["summary"]["matches_missing_resolved_stats"], 1)
            self.assertEqual(doc["summary"]["players"], 1)
            self.assertEqual(doc["summary"]["total_distance_m"], 220.0)
            self.assertEqual(doc["summary"]["playing_time_sec"], 110.0)
            self.assertEqual(doc["summary"]["sprint_count"], 1)
            self.assertEqual(doc["summary"]["sprint_candidate_count"], 3)
            self.assertEqual(doc["summary"]["anonymous_slots_aggregated"], 0)
            self.assertEqual(doc["players"][0]["player_id"], "p-a-1")
            self.assertEqual(doc["players"][0]["matches"], 2)
            self.assertEqual(doc["players"][0]["distance"]["total_distance_m"], 220.0)
            self.assertEqual([item["match_id"] for item in doc["missing_matches"]], ["match-3"])

    def test_unknown_team_without_stats_raises_key_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(KeyError):
                build_team_profile_stats(Path(tmp), "missing-team", registry_teams=[])


if __name__ == "__main__":
    unittest.main()
