from __future__ import annotations

import unittest

from app.services.identity_minimap import map_pitch_point


class IdentityMinimapTests(unittest.TestCase):
    def test_left_pitch_point_stays_on_left_of_minimap(self) -> None:
        left = map_pitch_point([1, 30], 100, 50, 200, 300, 40, 60)
        right = map_pitch_point([39, 30], 100, 50, 200, 300, 40, 60)
        self.assertLess(left[0], right[0])
