from __future__ import annotations

import unittest

from app.services.ball_possession import build_possession_timeline
from app.services.stabilization import _open_passing_lanes


class OverlayFutureFeatureTests(unittest.TestCase):
    def test_possession_timeline_uses_minimum_ten_points_for_short_clip(self) -> None:
        frames = []
        for frame in range(300):
            team = "A" if frame < 150 else "B"
            frames.append(
                {
                    "frame": frame,
                    "time_sec": frame / 30.0,
                    "status": "controlled",
                    "team_label": team,
                }
            )

        timeline = build_possession_timeline({"frames": frames, "summary": {"frame_interval_sec": 1 / 30}})

        self.assertEqual(len(timeline), 10)
        self.assertGreater(timeline[0]["team_controlled_frames"]["A"], 0)
        self.assertGreater(timeline[-1]["team_controlled_frames"]["B"], 0)

    def test_open_passing_lanes_skips_receiver_blocked_by_opponent_lower_bbox(self) -> None:
        possession = {
            "status": "controlled",
            "confidence": 0.9,
            "stable_player_id": "A01",
            "team_label": "A",
        }
        rows = [
            row("A01", "A", [90, 80, 110, 140]),
            row("A02", "A", [290, 80, 310, 140]),
            row("B01", "B", [190, 80, 210, 140]),
        ]

        lanes = _open_passing_lanes(possession, rows)

        self.assertEqual(lanes, [])

    def test_open_passing_lanes_returns_clear_same_team_receiver(self) -> None:
        possession = {
            "status": "controlled",
            "confidence": 0.9,
            "stable_player_id": "A01",
            "team_label": "A",
        }
        rows = [
            row("A01", "A", [90, 80, 110, 140]),
            row("A02", "A", [290, 80, 310, 140]),
            row("B01", "B", [190, 170, 210, 230]),
        ]

        lanes = _open_passing_lanes(possession, rows)

        self.assertEqual([lane["to_stable_player_id"] for lane in lanes], ["A02"])


def row(stable_player_id: str, team_label: str, bbox_xyxy: list[int]) -> dict:
    return {
        "stable_player_id": stable_player_id,
        "team_label": team_label,
        "source": "detected",
        "bbox_xyxy": bbox_xyxy,
    }


if __name__ == "__main__":
    unittest.main()
