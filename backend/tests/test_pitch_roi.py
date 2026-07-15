from __future__ import annotations

import unittest
import importlib.util

import numpy as np


CV2_AVAILABLE = importlib.util.find_spec("cv2") is not None


@unittest.skipUnless(CV2_AVAILABLE, "cv2 is required for pitch ROI tests")
class PitchRoiTests(unittest.TestCase):
    def test_accepts_detection_inside_pitch_margin(self) -> None:
        from app.services.analysis import _accept_detection_by_pitch_roi

        polygon = np.array([[0, 0], [100, 0], [100, 100], [0, 100]], dtype=np.float32)

        accepted, accepted_by_margin, distance = _accept_detection_by_pitch_roi((110, 50), polygon, margin_px=60)

        self.assertTrue(accepted)
        self.assertTrue(accepted_by_margin)
        self.assertLess(distance, 0)

    def test_rejects_detection_outside_pitch_margin(self) -> None:
        from app.services.analysis import _accept_detection_by_pitch_roi

        polygon = np.array([[0, 0], [100, 0], [100, 100], [0, 100]], dtype=np.float32)

        accepted, accepted_by_margin, distance = _accept_detection_by_pitch_roi((200, 50), polygon, margin_px=60)

        self.assertFalse(accepted)
        self.assertFalse(accepted_by_margin)
        self.assertLess(distance, -60)

    def test_clamps_pitch_positions_to_true_pitch_dimensions(self) -> None:
        from app.services.analysis import _tracks_with_pitch_positions
        from app.services.pitch import PitchConfig

        pitch = PitchConfig(
            image_points=[[0, 0], [100, 0], [100, 100], [0, 100]],
            width_m=30.0,
            length_m=47.4,
        )
        raw_tracks = [
            {
                "track_id": 1,
                "positions": [
                    {
                        "frame": 1,
                        "time_sec": 0.04,
                        "bbox_xyxy": [0, 0, 1, 1],
                        "footpoint": [35.0, 55.0],
                    }
                ],
            }
        ]

        tracks = _tracks_with_pitch_positions(raw_tracks, np.eye(3), pitch=pitch)

        position = tracks[0]["positions"][0]
        self.assertEqual(position["pitch_m"], [30.0, 47.4])
        self.assertTrue(position["pitch_m_clamped"])
        self.assertEqual(position["play_area_status"], "outside_play")
        self.assertGreater(position["pitch_boundary_distance_m"], 0.0)

    def test_classifies_near_line_position_as_boundary_transient(self) -> None:
        from app.services.play_area import classify_pitch_position

        result = classify_pitch_position(
            [0.2, 20.0],
            pitch_width_m=30.0,
            pitch_length_m=47.4,
        )

        self.assertEqual(result["play_area_status"], "boundary_transient")
        self.assertFalse(result["pitch_m_clamped"])
        self.assertEqual(result["pitch_boundary_distance_m"], 0.0)

    def test_classifies_clear_interior_position_as_inside_play(self) -> None:
        from app.services.play_area import classify_pitch_position

        result = classify_pitch_position(
            [5.0, 20.0],
            pitch_width_m=30.0,
            pitch_length_m=47.4,
        )

        self.assertEqual(result["play_area_status"], "inside_play")
        self.assertFalse(result["pitch_m_clamped"])


if __name__ == "__main__":
    unittest.main()
