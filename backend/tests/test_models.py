from __future__ import annotations

import unittest

from app.models import PitchConfigPayload


class ModelTests(unittest.TestCase):
    def test_pitch_config_payload_normalizes_dimensions_contract(self) -> None:
        payload = PitchConfigPayload(
            image_points=[[0, 0], [100, 0], [100, 100], [0, 100]],
            pitch_dimensions_m={"width_m": 30.0, "length_m": 47.4},
            calibration_frame_time_sec=2.5,
        )

        self.assertEqual(payload.width_m, 30.0)
        self.assertEqual(payload.length_m, 47.4)
        self.assertEqual(payload.pitch_dimensions_m, {"width_m": 30.0, "length_m": 47.4})
        self.assertEqual(payload.calibration_frame_time_sec, 2.5)

    def test_pitch_config_payload_updates_legacy_default_size(self) -> None:
        payload = PitchConfigPayload(
            image_points=[[0, 0], [100, 0], [100, 100], [0, 100]],
            width_m=26.0,
            length_m=56.0,
        )

        self.assertEqual(payload.pitch_dimensions_m, {"width_m": 30.0, "length_m": 47.4})


if __name__ == "__main__":
    unittest.main()
