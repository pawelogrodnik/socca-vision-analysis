from __future__ import annotations

import unittest

import numpy as np

from app.services.analysis import _classify_detections_for_tracking
from app.services.pitch import PitchConfig
from app.services.tracker import CentroidTracker


class AnalysisTrackBirthIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pitch = PitchConfig(
            image_points=[[0, 0], [30, 0], [30, 47.4], [0, 47.4]],
            width_m=30.0,
            length_m=47.4,
        )
        self.tracker = CentroidTracker(
            max_distance_px=10.0,
            max_missing=2,
            play_area_aware=True,
            allow_outside_continuation=False,
        )

    @staticmethod
    def _raw_detection(x: float, y: float) -> dict:
        return {
            "bbox_xyxy": [x - 1.0, y - 4.0, x + 1.0, y],
            "footpoint": [x, y],
            "tracking_footpoint": [x, y],
            "confidence": 0.9,
        }

    def _update(self, raw: list[dict], frame: int) -> None:
        classified = _classify_detections_for_tracking(raw, np.eye(3), self.pitch)
        self.tracker.update(classified, frame, frame / 25)

    def test_legitimate_player_and_off_pitch_person_create_one_track(self) -> None:
        for frame in range(1, 6):
            self._update(
                [
                    self._raw_detection(10 + frame, 20),
                    self._raw_detection(-3, 20),
                ],
                frame,
            )

        tracks = self.tracker.all_tracks()
        self.assertEqual(len(tracks), 1)
        self.assertEqual(len(tracks[0].positions), 5)
        self.assertTrue(all(row["play_area_status"] == "inside_play" for row in tracks[0].positions))
        self.assertEqual(self.tracker.telemetry()["track_birth_rejected_outside"], 5)

    def test_canonical_inside_boundary_inside_sequence_keeps_track(self) -> None:
        x_positions = [2.0, 1.0, 0.2, 0.1, 1.0, 2.0]

        for frame, x in enumerate(x_positions, start=1):
            self._update([self._raw_detection(x, 20)], frame)

        tracks = self.tracker.all_tracks()
        self.assertEqual(len(tracks), 1)
        self.assertEqual(
            [row["play_area_status"] for row in tracks[0].positions],
            [
                "inside_play",
                "inside_play",
                "boundary_transient",
                "boundary_transient",
                "inside_play",
                "inside_play",
            ],
        )
        self.assertEqual(self.tracker.telemetry()["track_birth_inside"], 1)


if __name__ == "__main__":
    unittest.main()
