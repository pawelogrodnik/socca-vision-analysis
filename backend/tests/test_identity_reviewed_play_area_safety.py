from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from app.services.identity_minimap import draw_reviewed_minimap
from app.services.identity_reviewed_effective_observation import (
    visible_reviewed_overlay,
    visible_reviewed_player,
)
from app.services.identity_reviewed_snapshot_observations import observation_coverage
from app.services.identity_reviewed_video import _positions_by_frame


class ReviewedPlayAreaSafetyTests(unittest.TestCase):
    def test_renderer_only_exposes_inside_play_product_labels(self) -> None:
        rows = [
            _position(1, "confirmed", "inside_play"),
            _position(2, "unresolved", "inside_play"),
            _position(3, "confirmed", "boundary_transient"),
            _position(4, "unresolved", "boundary_transient"),
            _position(5, "confirmed", "outside_play"),
            _position(6, "unresolved", "outside_play"),
            _position(7, "referee", "inside_play"),
            _position(8, "false_detection", "inside_play"),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tracklets.json").write_text(
                json.dumps({"tracklets": [{"tracklet_id": "t1", "positions_m": rows}]}),
                encoding="utf-8",
            )
            rendered = _positions_by_frame(
                root,
                {
                    "tracklet_assignments": [
                        {
                            "tracklet_id": "t1",
                            "identity_status": "unresolved",
                            "display_label": "A?",
                        }
                    ],
                    "observation_overrides": [
                        {
                            "tracklet_id": "t1",
                            "frame": row["frame"],
                            "identity_status": row["identity_status"],
                        }
                        for row in rows
                    ],
                    "observation_demotions": [],
                },
            )

        self.assertEqual(sorted(rendered), list(range(1, 9)))
        self.assertEqual(
            [frame for frame, frame_rows in rendered.items() if frame_rows],
            [1, 2, 7],
        )
        self.assertTrue(visible_reviewed_overlay(rows[0]))
        self.assertFalse(visible_reviewed_overlay(rows[2]))
        self.assertFalse(visible_reviewed_overlay(rows[4]))
        self.assertTrue(visible_reviewed_overlay(rows[6]))
        self.assertFalse(visible_reviewed_player(rows[6]))

    def test_minimap_rejects_clamped_outside_and_boundary_rows_defensively(self) -> None:
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        rows = [
            {
                "pitch_m_raw": [-5.0, 20.0],
                "pitch_m": [0.0, 20.0],
                "pitch_m_clamped": True,
                "play_area_status": "outside_play",
                "team_label": "A",
            },
            {
                "pitch_m_raw": [-0.5, 20.0],
                "pitch_m": [0.0, 20.0],
                "pitch_m_clamped": True,
                "play_area_status": "boundary_transient",
                "team_label": "A",
            },
            {
                "pitch_m_raw": [5.0, 20.0],
                "pitch_m": [5.0, 20.0],
                "pitch_m_clamped": False,
                "play_area_status": "inside_play",
                "team_label": "A",
            },
        ]

        result = draw_reviewed_minimap(
            frame,
            rows,
            pitch_width=30.0,
            pitch_length=47.4,
        )

        self.assertEqual(result["players_rendered"], 1)

    def test_coverage_counts_only_inside_play_and_reports_exclusions(self) -> None:
        positions = [
            *[_position(frame, "confirmed", "inside_play") for frame in range(10)],
            *[_position(frame, "unresolved", "inside_play") for frame in range(10, 15)],
            *[_position(frame, "unresolved", "outside_play") for frame in range(15, 35)],
            *[_position(frame, "unresolved", "boundary_transient") for frame in range(35, 45)],
        ]
        coverage = observation_coverage(
            {"t1": {"positions_m": positions}},
            [{"tracklet_id": "t1", "identity_status": "unresolved"}],
            [
                {
                    "tracklet_id": "t1",
                    "frame": frame,
                    "identity_status": "confirmed",
                }
                for frame in range(10)
            ]
            + [
                {
                    "tracklet_id": "t1",
                    "frame": 15,
                    "identity_status": "confirmed",
                }
            ],
            [],
            segment_overrides=[
                {
                    "tracklet_id": "t1",
                    "frame": 35,
                    "identity_status": "confirmed",
                }
            ],
        )

        self.assertEqual(coverage["detected_observations_total"], 15)
        self.assertEqual(coverage["reliable_player_observations_total"], 15)
        self.assertEqual(coverage["confirmed_detected_observations"], 10)
        self.assertEqual(coverage["unresolved_detected_observations"], 5)
        self.assertEqual(coverage["exact_named_observations"], 10)
        self.assertEqual(coverage["segment_named_observations"], 0)
        self.assertAlmostEqual(coverage["confirmed_detected_observation_ratio"], 10 / 15, places=4)
        self.assertAlmostEqual(coverage["unresolved_detected_observation_ratio"], 5 / 15, places=4)
        self.assertEqual(
            coverage["play_area_diagnostics"],
            {
                "detected_observations_all_play_areas": 45,
                "inside_play_observations": 15,
                "boundary_transient_observations": 10,
                "outside_play_observations": 20,
            },
        )


def _position(frame: int, identity_status: str, play_area_status: str) -> dict:
    return {
        "frame": frame,
        "time_sec": frame / 25.0,
        "status": "detected",
        "source": "detected",
        "bbox_xyxy": [10, 10, 20, 30],
        "pitch_m": [5.0, 20.0],
        "identity_status": identity_status,
        "play_area_status": play_area_status,
    }


if __name__ == "__main__":
    unittest.main()
