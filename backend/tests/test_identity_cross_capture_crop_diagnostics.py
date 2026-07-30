from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np

from app.services.identity_cross_capture_crop_diagnostics import (
    build_cross_capture_crop_diagnostics,
)


class CrossCaptureCropDiagnosticsTests(unittest.TestCase):
    def test_builds_read_only_summary_and_montages(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            h1 = root / "h1"
            h2 = root / "h2"
            h1.mkdir()
            h2.mkdir()
            _write_crop(h1 / "a.jpg", (0, 0, 200))
            _write_crop(h2 / "b.jpg", (200, 0, 0))
            result = build_cross_capture_crop_diagnostics(
                reference_gallery={"players": [{
                    "player_name": "A",
                    "capture_domains": [{"crops": [_crop("a.jpg")] }],
                }]},
                target_anchor_crops={"cards": [{
                    "candidate_subject_id": "subject-b",
                    "anchor_crops": [_crop("b.jpg")],
                }]},
                reference_root=h1,
                target_root=h2,
                output_directory=root / "out",
            )

            self.assertEqual(result["h1"]["crop_count"], 1)
            self.assertEqual(result["h2"]["missing_artifacts"], 0)
            self.assertTrue(
                Path(result["montages"]["h1_reference_crops"]).exists()
            )
            self.assertTrue(result["safety"]["read_only"])


def _crop(artifact: str) -> dict[str, object]:
    return {
        "artifact": artifact,
        "selection_score": 1.0,
        "bbox_xyxy": [1, 2, 31, 62],
    }


def _write_crop(path: Path, color: tuple[int, int, int]) -> None:
    cv2.imwrite(str(path), np.full((60, 30, 3), color, dtype=np.uint8))


if __name__ == "__main__":
    unittest.main()
