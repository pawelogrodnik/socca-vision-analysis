from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np

from app.services.identity_jersey_number_panel_readiness import (
    build_identity_jersey_number_panel_readiness,
    extract_number_panel,
    normalized_bbox_to_pixels,
    raw_pixel_digest,
    validate_normalized_bbox,
)


def _review(*, montage_reviewed: bool = False, bbox: object = (0.1, 0.2, 0.8, 0.9), glyph: float | None = 12) -> dict:
    annotation = {
        "number_panel_source_artifact": "crop.png",
        "coordinate_space_version": "crop-normalized-v1",
        "number_panel_bbox_normalized": bbox,
        "glyph_height_px": glyph,
        "annotation_source": "operator",
        "view": "frontal",
    }
    return {
        "montage_reviewed": montage_reviewed,
        "cards": [{
            "candidate_subject_id": "subject-1",
            "visual_evidence": {"anchor_crops": [{
                "anchor_crop_id": "crop-1", "artifact": "crop.png", "tracklet_id": "track-1",
                "visibility_episode_id": "episode-1", "frame": 10,
                "number_panel_annotation": annotation,
                "jersey_number_annotation": {
                    "number": "10", "state": "number_confirmed", "annotation_source": "operator",
                },
            }]},
        }],
    }


class IdentityJerseyNumberPanelReadinessTests(unittest.TestCase):
    def test_floor_ceil_clamp_and_raw_digest_are_deterministic(self) -> None:
        image = np.arange(30 * 20 * 3, dtype=np.uint8).reshape((20, 30, 3))
        self.assertEqual(normalized_bbox_to_pixels([0.11, 0.11, 0.51, 0.51], width=30, height=20), (3, 2, 16, 11))
        first, pixels = extract_number_panel(image, [0.11, 0.11, 0.51, 0.51])
        second, _ = extract_number_panel(image, [0.11, 0.11, 0.51, 0.51])
        self.assertEqual(pixels, (3, 2, 16, 11))
        self.assertEqual(raw_pixel_digest(first), raw_pixel_digest(second))
        self.assertEqual(first.shape, (9, 13, 3))

    def test_bbox_validation_and_input_immutability(self) -> None:
        with self.assertRaises(ValueError):
            validate_normalized_bbox([0.5, 0.2, 0.4, 0.9])
        with self.assertRaises(ValueError):
            validate_normalized_bbox([0.0, 0.0, float("nan"), 1.0])
        source = _review()
        original = deepcopy(source)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cv2.imwrite(str(root / "crop.png"), np.full((20, 30, 3), 100, dtype=np.uint8))
            build_identity_jersey_number_panel_readiness(source, artifact_root=root, generated_at="fixed")
        self.assertEqual(source, original)

    def test_readiness_hard_blockers_and_deterministic_outputs(self) -> None:
        source = _review()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cv2.imwrite(str(root / "crop.png"), np.full((20, 30, 3), 100, dtype=np.uint8))
            first = build_identity_jersey_number_panel_readiness(source, artifact_root=root, generated_at="fixed")
            second = build_identity_jersey_number_panel_readiness(source, artifact_root=root, generated_at="fixed")
        report = first["identity_jersey_number_panel_readiness"]
        self.assertEqual(first, second)
        self.assertEqual(report["status"], "blocked")
        self.assertIn("no_manually_reviewed_montage", report["hard_blockers"])
        self.assertEqual(report["summary"]["readable"], 1)

    def test_missing_bbox_and_assistant_label_block(self) -> None:
        source = _review(montage_reviewed=True, bbox=None)
        crop = source["cards"][0]["visual_evidence"]["anchor_crops"][0]
        crop["number_panel_annotation"] = {"annotation_source": "operator", "glyph_height_px": 12}
        crop["jersey_number_annotation"]["annotation_source"] = "assistant_visual_audit_high_confidence"
        with tempfile.TemporaryDirectory() as directory:
            report = build_identity_jersey_number_panel_readiness(
                source, artifact_root=Path(directory), generated_at="fixed"
            )["identity_jersey_number_panel_readiness"]
        self.assertIn("missing_bbox", report["hard_blockers"])
        self.assertIn("assistant_only_label_ground_truth", report["hard_blockers"])


if __name__ == "__main__":
    unittest.main()
