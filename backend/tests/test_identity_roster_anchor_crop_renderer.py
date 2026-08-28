from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from app.services.identity_roster_anchor_crop_renderer import (
    render_identity_roster_anchor_crops,
)


class _FakeCapture:
    def __init__(self, frame: np.ndarray) -> None:
        self._frame = frame
        self._read = False
        self.seek_requests: list[tuple[int, int]] = []

    def isOpened(self) -> bool:
        return True

    def read(self) -> tuple[bool, np.ndarray | None]:
        if self._read:
            return False, None
        self._read = True
        return True, self._frame.copy()

    def set(self, property_id: int, value: int) -> bool:
        self.seek_requests.append((property_id, value))
        return True

    def release(self) -> None:
        return None


class IdentityRosterAnchorCropRendererTests(unittest.TestCase):
    def test_target_bbox_is_drawn_inside_padded_review_crop(self) -> None:
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        captured: list[np.ndarray] = []
        capture = _FakeCapture(frame)
        artifact = {
            "cards": [
                {
                    "anchor_crops": [
                        {
                            "frame": 17_174,
                            "bbox_xyxy": [40, 30, 60, 80],
                            "artifact": "anchor_crops/target.jpg",
                        }
                    ]
                }
            ]
        }

        with TemporaryDirectory() as directory, patch(
            "app.services.identity_roster_anchor_crop_renderer.cv2.VideoCapture",
            return_value=capture,
        ), patch(
            "app.services.identity_roster_anchor_crop_renderer.cv2.imwrite",
            side_effect=lambda _path, image: captured.append(image.copy()) or True,
        ):
            rendered = render_identity_roster_anchor_crops(
                Path(directory) / "source.mp4",
                Path(directory),
                artifact,
            )

        self.assertEqual(rendered, {"anchor_crops/target.jpg"})
        self.assertEqual(capture.seek_requests, [(cv2.CAP_PROP_POS_FRAMES, 17_174)])
        self.assertEqual(len(captured), 1)
        image = captured[0]
        # bbox [40,30,60,80] has 30%/20% padding, so the target's upper-left
        # edge is at x=6/y=10 in the crop. Yellow is BGR (0,255,255).
        self.assertTrue(np.any(np.all(image == np.array([0, 255, 255]), axis=2)))


if __name__ == "__main__":
    unittest.main()
