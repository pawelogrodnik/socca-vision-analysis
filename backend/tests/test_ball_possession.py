from __future__ import annotations

import unittest

from app.services.ball_possession import (
    build_contact_candidates_document,
    build_possession_candidates_document,
    build_possession_segments_document,
)


def ball(frame: int, x: float, y: float, source: str = "detected") -> dict:
    return {
        "frame": frame,
        "time_sec": round(frame / 30, 3),
        "position_m": [x, y] if source != "unknown" else None,
        "position_px": [x * 10, y * 10] if source != "unknown" else None,
        "source": source,
        "confidence": 0.7 if source == "detected" else 0.25,
    }


def stable_player(player_id: str, team: str, positions: list[tuple[int, float, float]]) -> dict:
    return {
        "stable_player_id": player_id,
        "stable_subject_id": f"slot-{player_id}",
        "slot_id": player_id,
        "team_label": team,
        "team_id": f"team-{team}",
        "team_name": f"Team {team}",
        "trajectory_m": [
            {
                "frame": frame,
                "time_sec": round(frame / 30, 3),
                "pitch_m": [x, y],
                "source": "detected",
                "status": "detected",
            }
            for frame, x, y in positions
        ],
    }


def stable_player_with_sources(player_id: str, team: str, rows: list[tuple[int, float, float, str]]) -> dict:
    return {
        "stable_player_id": player_id,
        "stable_subject_id": f"slot-{player_id}",
        "slot_id": player_id,
        "team_label": team,
        "team_id": f"team-{team}",
        "team_name": f"Team {team}",
        "trajectory_m": [
            {
                "frame": frame,
                "time_sec": round(frame / 30, 3),
                "pitch_m": [x, y],
                "source": source,
                "status": source,
            }
            for frame, x, y, source in rows
        ],
    }


class BallPossessionTests(unittest.TestCase):
    def test_possession_marks_controlled_when_one_player_is_close(self) -> None:
        doc = build_possession_candidates_document(
            {"positions": [ball(10, 5.0, 5.0)]},
            {"players": [stable_player("A01", "A", [(10, 5.5, 5.0)])]},
            fps=30,
        )

        frame = doc["frames"][0]
        self.assertEqual(frame["status"], "controlled")
        self.assertEqual(frame["stable_player_id"], "A01")
        self.assertEqual(frame["team_label"], "A")
        self.assertLess(frame["nearest_distance_m"], 1.0)

    def test_possession_uses_short_gap_interpolated_player_position_with_lower_confidence(self) -> None:
        detected = build_possession_candidates_document(
            {"positions": [ball(2, 5.0, 5.0)]},
            {"players": [stable_player("A01", "A", [(2, 5.4, 5.0)])]},
            fps=30,
        )
        interpolated = build_possession_candidates_document(
            {"positions": [ball(2, 5.0, 5.0)]},
            {
                "players": [
                    stable_player_with_sources(
                        "A01",
                        "A",
                        [
                            (0, 5.2, 5.0, "detected"),
                            (1, 5.3, 5.0, "missing"),
                            (2, 5.4, 5.0, "missing"),
                            (3, 5.5, 5.0, "missing"),
                            (4, 5.6, 5.0, "detected"),
                        ],
                    )
                ]
            },
            fps=30,
        )

        detected_frame = detected["frames"][0]
        interpolated_frame = interpolated["frames"][0]
        self.assertEqual(interpolated_frame["status"], "controlled")
        self.assertEqual(interpolated_frame["nearest_player_source"], "short_gap_interpolated")
        self.assertLess(interpolated_frame["confidence"], detected_frame["confidence"])
        self.assertEqual(interpolated["summary"]["interpolated_player_position_frames"], 1)

    def test_possession_marks_contested_when_two_players_are_close(self) -> None:
        doc = build_possession_candidates_document(
            {"positions": [ball(10, 5.0, 5.0)]},
            {
                "players": [
                    stable_player("A01", "A", [(10, 5.6, 5.0)]),
                    stable_player("B01", "B", [(10, 4.5, 5.0)]),
                ]
            },
            fps=30,
        )

        frame = doc["frames"][0]
        self.assertEqual(frame["status"], "contested")
        self.assertEqual(len(frame["nearest_players"]), 2)
        self.assertIsNone(frame.get("stable_player_id"))

    def test_possession_stays_unknown_without_trusted_player_positions(self) -> None:
        doc = build_possession_candidates_document(
            {"positions": [ball(10, 5.0, 5.0)]},
            {"players": []},
            fps=30,
        )

        frame = doc["frames"][0]
        self.assertEqual(frame["status"], "unknown")
        self.assertEqual(frame["reason"], "no_trusted_detected_player_positions")

    def test_contact_candidates_are_built_from_controlled_segments(self) -> None:
        candidates = build_possession_candidates_document(
            {"positions": [ball(10, 5.0, 5.0), ball(11, 5.1, 5.0)]},
            {"players": [stable_player("A01", "A", [(10, 5.5, 5.0), (11, 5.6, 5.0)])]},
            fps=30,
        )
        segments = build_possession_segments_document(candidates, fps=30)
        contacts = build_contact_candidates_document(candidates, segments)

        self.assertEqual(contacts["summary"]["contact_candidates"], 1)
        self.assertEqual(contacts["candidates"][0]["stable_player_id"], "A01")
        self.assertEqual(contacts["candidates"][0]["detected_ball_frames"], 2)


if __name__ == "__main__":
    unittest.main()
