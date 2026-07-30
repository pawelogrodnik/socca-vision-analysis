from __future__ import annotations

import copy
import unittest

from app.services.identity_unresolved_overlay import (
    build_unrepresented_tracklet_observations,
    select_unresolved_overlay_rows,
)


class IdentityUnresolvedOverlayTests(unittest.TestCase):
    def test_builds_only_clean_observations_missing_from_visual_layers(self) -> None:
        tracklets = [
            {
                "tracklet_id": "100:1",
                "source_tracker_id": 100,
                "team_label": "A",
                "team_confidence": 0.92,
                "positions_m": [
                    _position(1, [10, 10, 20, 40]),
                    _position(2, [20, 10, 30, 40]),
                    _position(3, [30, 10, 40, 40]),
                    {
                        **_position(4, [40, 10, 50, 40]),
                        "play_area_status": "outside_play",
                    },
                ],
            }
        ]
        identity = {
            "slots": [
                {
                    "overlay_positions": [
                        {
                            "frame": 1,
                            "tracklet_id": "100:1",
                            "bbox_xyxy": [10, 10, 20, 40],
                        }
                    ]
                }
            ],
            "unmatched_observations": [
                {
                    "frame": 2,
                    "tracklet_id": "100:1",
                    "bbox_xyxy": [20, 10, 30, 40],
                }
            ],
        }
        tracklets_before = copy.deepcopy(tracklets)
        identity_before = copy.deepcopy(identity)

        rows = build_unrepresented_tracklet_observations(tracklets, identity)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["frame"], 3)
        self.assertEqual(rows[0]["tracklet_id"], "100:1")
        self.assertEqual(rows[0]["team_label"], "A")
        self.assertEqual(rows[0]["source"], "unrepresented_tracklet")
        self.assertFalse(rows[0]["visual_trusted"])
        self.assertEqual(tracklets, tracklets_before)
        self.assertEqual(identity, identity_before)

    def test_selection_keeps_unresolved_rows_after_team_reaches_seven(self) -> None:
        existing_rows = [
            {
                "source": "detected",
                "team_label": "A",
                "bbox_xyxy": [index * 20, 0, index * 20 + 10, 30],
            }
            for index in range(7)
        ]
        unresolved = [
            {
                "source": "unrepresented_tracklet",
                "team_label": "A",
                "bbox_xyxy": [200, 0, 210, 30],
                "confidence": 0.8,
            }
        ]

        selected = select_unresolved_overlay_rows(existing_rows, unresolved)

        self.assertEqual(selected, unresolved)

    def test_selection_suppresses_duplicate_geometry(self) -> None:
        existing_rows = [
            {
                "source": "detected",
                "bbox_xyxy": [10, 10, 30, 50],
            }
        ]
        unresolved = [
            {
                "source": "unmatched_raw",
                "bbox_xyxy": [11, 11, 29, 49],
                "confidence": 0.9,
            },
            {
                "source": "unmatched_raw",
                "bbox_xyxy": [50, 10, 70, 50],
                "confidence": 0.8,
            },
        ]

        selected = select_unresolved_overlay_rows(existing_rows, unresolved)

        self.assertEqual([row["bbox_xyxy"] for row in selected], [[50, 10, 70, 50]])


def _position(frame: int, bbox: list[int]) -> dict[str, object]:
    return {
        "frame": frame,
        "time_sec": frame / 30,
        "bbox_xyxy": bbox,
        "pitch_m": [1.0, 1.0],
        "play_area_status": "inside_play",
        "confidence": 0.8,
    }


if __name__ == "__main__":
    unittest.main()
