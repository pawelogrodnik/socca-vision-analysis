from __future__ import annotations

import unittest

from app.services.player_detection_quality_audit import (
    _observations_by_frame,
    _render_html,
    select_player_detection_qa_frames,
)


class PlayerDetectionQualityAuditTests(unittest.TestCase):
    def test_qa_observations_come_from_clean_tracklets(self) -> None:
        observations = _observations_by_frame(
            {
                "tracklets": [
                    {
                        "tracklet_id": "tracklet-1",
                        "team_label": "A",
                        "mean_confidence": 0.8,
                        "positions_m": [
                            {
                                "frame": 10,
                                "bbox_xyxy": [10, 20, 30, 70],
                                "confidence": 0.9,
                                "play_area_status": "inside_play",
                            },
                            {
                                "frame": 11,
                                "bbox_xyxy": [12, 20, 32, 70],
                                "play_area_status": "outside_play",
                            },
                        ],
                    }
                ]
            }
        )

        self.assertEqual(
            observations,
            {
                10: [
                    {
                        "stable_subject_id": None,
                        "tracklet_id": "tracklet-1",
                        "team_label": "A",
                        "bbox_xyxy": [10.0, 20.0, 30.0, 70.0],
                        "confidence": 0.9,
                    }
                ]
            },
        )

    def test_overlay_keeps_image_aspect_ratio_when_crop_panel_opens(self) -> None:
        html = _render_html(
            {
                "ui": {},
                "video": {"width": 1920, "height": 1080},
                "items": [],
            }
        )

        self.assertIn("align-items:start", html)
        self.assertIn(
            "#overlay{position:absolute;top:0;left:0;display:block;"
            "width:100%;height:auto",
            html,
        )
        self.assertIn("pointermove", html)
        self.assertIn("Dodano bbox Team", html)
        self.assertIn("dorysowane:", html)
        self.assertIn("Komentarz do tej klatki", html)
        self.assertIn("frame_comments:", html)

    def test_prioritizes_known_false_and_sparse_frames_with_spacing(self) -> None:
        observations = {
            0: [_observation("a", "A", 0.9)],
            20: [_observation("a", "A", 0.9)],
            100: [_observation("a", "A", 0.05)],
            200: [_observation("a", "A", 0.9) for _ in range(4)],
        }

        selected = select_player_detection_qa_frames(
            observations,
            fps=10.0,
            known_false_frames={100},
            maximum_frames=2,
            minimum_gap_seconds=5.0,
        )

        selected_by_frame = {row["frame"]: row for row in selected}
        self.assertIn(100, selected_by_frame)
        self.assertTrue(selected_by_frame[100]["known_false"])
        self.assertEqual(
            selected_by_frame[100]["filter_summary"]["low_confidence"],
            1,
        )
        self.assertTrue(
            all(
                abs(left["frame"] - right["frame"]) >= 50
                for index, left in enumerate(selected)
                for right in selected[index + 1 :]
            )
        )


def _observation(
    subject_id: str,
    team_label: str,
    confidence: float,
) -> dict[str, object]:
    return {
        "stable_subject_id": subject_id,
        "team_label": team_label,
        "bbox_xyxy": [10.0, 10.0, 30.0, 60.0],
        "confidence": confidence,
    }


if __name__ == "__main__":
    unittest.main()
