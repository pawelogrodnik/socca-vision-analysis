from __future__ import annotations

import unittest

import numpy as np

from app.services.identity_reviewed_video import draw_ball_evidence_on_main_frame


class ReviewedBallDiagnosticTests(unittest.TestCase):
    def test_detected_ball_draws_only_persisted_bbox(self) -> None:
        frame = np.zeros((100, 120, 3), dtype=np.uint8)

        state = draw_ball_evidence_on_main_frame(
            frame,
            {
                "source": "detected",
                "position_px": [40.0, 50.0],
                "bbox_xyxy": [30.0, 40.0, 50.0, 60.0],
                "confidence": 0.73,
            },
        )

        self.assertEqual(state, "detected_bbox")
        self.assertTrue(frame[40, 30].any())

    def test_interpolated_ball_uses_marker_not_bbox(self) -> None:
        frame = np.zeros((100, 120, 3), dtype=np.uint8)

        state = draw_ball_evidence_on_main_frame(
            frame,
            {"source": "interpolated", "position_px": [40.0, 50.0], "bbox_xyxy": None, "confidence": 0.5},
        )

        self.assertEqual(state, "interpolated_marker")
        self.assertTrue(frame[50, 33].any())
        self.assertFalse(frame[5, 5].any())

    def test_unknown_ball_leaves_frame_unchanged(self) -> None:
        frame = np.zeros((100, 120, 3), dtype=np.uint8)
        before = frame.copy()

        state = draw_ball_evidence_on_main_frame(frame, {"source": "unknown", "position_px": None, "bbox_xyxy": None})

        self.assertEqual(state, "unknown")
        self.assertTrue(np.array_equal(frame, before))


if __name__ == "__main__":
    unittest.main()
